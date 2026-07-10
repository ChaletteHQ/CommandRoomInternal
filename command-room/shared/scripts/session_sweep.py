#!/usr/bin/env python3
"""
Session-sweep write core (Phase 5 Memory layer, R1 — 2026-07).

THE MEMORY LEAK THIS CLOSES
---------------------------
Command Room only remembers what a writing skill happened to capture. Anything
the CEO does in an ad-hoc chat that never fires a capture skill is lost — a
commitment made in passing, a decision reasoned out loud, a deliverable produced
by hand. Cowork exposes full transcripts of every session (proven readable from
inside a scheduled-task session, 2026-07-01), so a nightly pass can promote that
episodic layer (L1) into the canonical event log (L2) after the fact.

This module is the deterministic WRITE half of that pass. The SKILL does the
semantic extraction (reads transcripts, decides what is a real commitment /
decision / interaction / deliverable that never became an event); this module
takes those extracted items and does the parts that must be exact:

  1. Dedup — through the EXISTING `.source_refs.idx` sidecar, never a second
     path. Each item carries `data.source_ref = "session:{session_id}"` for
     provenance plus a content hash in `data.dedup_hash`; the CONTENT hash is
     the dedup discriminator (the session-level source_ref is identical across
     every item of one session, so it can never be the dedup key — that would
     collapse a whole session to one event). Re-running a session only appends
     items whose content the index hasn't seen, so the sweep is idempotent and
     an over-wide re-scan is always safe.
  2. Write — through `append_event()` (the F1 gatekeeper), so swept events get
     the same seq/ts stamping, schema-enum validation, and commitment identity
     (`cmt_<ulid>` + required `data.kind`) as any other writer. There is exactly
     one append path.
  3. Receipt — one `session_sweep_run` audit event per run (sessions scanned,
     events recovered, per-type counts). The watchdog reads this receipt to
     confirm the task fired and did its job (Bug #98 doctrine: enforce on the
     substrate artifact, never on narration). `validate_sweep_ran` reads it
     back so the skill can self-validate exactly like reconcile-sent.

The recovered items reuse the EXISTING `commitment` / `decision` / `interaction`
/ `note` families — there are no parallel "swept" event variants (EVENT_TYPES.md).
Deliverables recovered from a chat land as `note` events tagged
`data.recovered_kind = "deliverable"`.

stdlib only. Shared by the nightly sweep (R1) and — via the same `_sweep` core —
the one-time historical backfill (R2), so both dedup and write identically.
"""
from __future__ import annotations

import hashlib
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from capture_gate import (  # noqa: E402
    classify_capture,
    gate_commitment_data,
    observed_from_commitment_event,
    workspace_capture_context,
)
from event_gate import append_event  # noqa: E402
import source_ref_index  # noqa: E402

# The event families the sweep may recover. Deliberately a small, closed set of
# ALREADY-REGISTERED types — the sweep recovers real history, it does not mint a
# new vocabulary. `deliverable` is an INPUT alias the skill may use; it is stored
# as a `note` (see _normalize_item).
SWEEPABLE_TYPES = frozenset({"commitment", "decision", "interaction", "note"})
_DELIVERABLE_ALIAS = "note"

# Receipt event types (registered in EVENT_TYPES.md / events.schema.json).
RECEIPT_SWEEP = "session_sweep_run"
RECEIPT_BACKFILL = "session_backfill_run"

# Top-level (non-`data`) reference fields the skill may set on an item; lifted
# onto the event so the dual-layer reader (event_refs) links threads/people.
_TOPLEVEL_REF_FIELDS = (
    "person_ids",
    "primary_thread_id",
    "related_thread_ids",
    "classification_confidence",
)


def session_source_ref(session_id: str) -> str:
    """Provenance ref for a swept item: `session:{session_id}`. Constant across
    every item of one session (so it is NOT the dedup key — the content hash is).
    """
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required to compose a source_ref")
    return f"session:{sid}"


def content_hash(source_ref: str, etype: str, summary: str) -> str:
    """12-hex content hash — the dedup discriminator for a swept item.

    Mirrors the PASSIVE_CAPTURE convention (`sha256(...)[:12]` into
    `data.dedup_hash`). Basis is source_ref + type + normalized summary so two
    genuinely-distinct items of one session hash apart, while re-processing the
    same item twice hashes identically and dedups.
    """
    basis = f"{source_ref}|{etype}|{(summary or '').strip().lower()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def already_captured(workspace_root, dedup_hash: str) -> bool:
    """True if this content hash is already in `.source_refs.idx`.

    Checks the CONTENT hash only — never the session-level source_ref, which is
    shared across a whole session and would false-positive after the first item.
    The index self-heals (rebuilds from events.jsonl on first check).
    """
    return source_ref_index.check(workspace_root, dedup_hash=dedup_hash)


class SweepItemError(ValueError):
    """A raw extracted item was malformed — fail loud so a bad extraction is
    visible, not silently dropped."""


def _gate_commitment(data: dict, raw: dict, session_id) -> None:
    """Stage-D / S2 / Stage-E capture parity for a recovered commitment
    (v4.5.2 C1, F-31): the sweep runs the SAME capture block as
    scan-for-commitments Step 3 — since v4.6.1 W4c literally the same code,
    `capture_gate.gate_commitment_data` (this wrapper only binds the sweep's
    error class + subject wording). Mutates `data` in place (pending_review
    stamping); raises SweepItemError on anything the extraction must go back
    and do."""
    gate_commitment_data(
        data,
        subject=f"recovered commitment (session {session_id})",
        classification_confidence=raw.get("classification_confidence"),
        error_cls=SweepItemError,
    )


def _normalize_item(raw: dict, source_skill: str) -> tuple[dict, str]:
    """Turn one raw extracted item into a gated-ready event dict + its dedup
    hash. Raises SweepItemError on anything the sweep cannot safely write."""
    if not isinstance(raw, dict):
        raise SweepItemError(f"item must be a dict, got {type(raw).__name__}")

    session_id = raw.get("session_id")
    source_ref = session_source_ref(session_id)  # raises on empty session_id

    etype = (raw.get("type") or "").strip()
    recovered_kind = None
    if etype == "deliverable":
        recovered_kind = "deliverable"
        etype = _DELIVERABLE_ALIAS
    if etype not in SWEEPABLE_TYPES:
        raise SweepItemError(
            f"unsupported sweep type {raw.get('type')!r} — recover one of "
            f"{sorted(SWEEPABLE_TYPES)} (or 'deliverable', stored as note)"
        )

    summary = (raw.get("summary") or "").strip()
    if not summary:
        raise SweepItemError(f"item (session {session_id}) has no summary")

    dedup_hash = content_hash(source_ref, etype, summary)

    data = dict(raw.get("data") or {})
    data["summary"] = summary
    data["source_ref"] = source_ref
    data["dedup_hash"] = dedup_hash
    data["session_id"] = session_id
    data["recovered_by"] = "session-sweep"
    if recovered_kind:
        data.setdefault("recovered_kind", recovered_kind)

    # Commitments must run the full capture block at extraction — kind
    # classification, S2 due-nudge, Stage-E counterparty receipts, and the
    # pending_review safety inversion (v4.5.2 C1 / F-31 parity with
    # scan-for-commitments Step 3). Title defaults from the summary.
    if etype == "commitment":
        data.setdefault("title", summary)
        # MC1: normalize counterparty fields (single byte-identical; multi
        # writes the list + primary scalar so degrade-readers see the first).
        from commitment_parties import build_counterparty_fields
        _cpf = build_counterparty_fields(
            counterparty_id=data.get("counterparty_id"),
            counterparty_name=data.get("counterparty_name"),
            counterparty_ids=data.get("counterparty_ids"),
            counterparty_names=data.get("counterparty_names"),
        )
        for _k in ("counterparty_id", "counterparty_name",
                   "counterparty_ids", "counterparty_names"):
            data.pop(_k, None)
        data.update(_cpf)
        _gate_commitment(data, raw, session_id)

    event = {"type": etype, "source_skill": source_skill, "data": data}
    for field in _TOPLEVEL_REF_FIELDS:
        if raw.get(field) is not None:
            event[field] = raw[field]

    # Stage-E: a resolved counterparty_id is also a person reference — the
    # schema requires it in person_ids so the dual-layer reader links it.
    if etype == "commitment":
        from commitment_parties import counterparty_ids as _cp_ids
        pids = list(event.get("person_ids") or [])
        changed = False
        for _cid in _cp_ids(data):  # MC1: every resolved counterparty
            if _cid not in pids:
                pids.append(_cid)
                changed = True
        if changed:
            event["person_ids"] = pids
    return event, dedup_hash


# Canonical task identity per receipt type (v4.5.2 R1 receipt contract).
_RECEIPT_TASK_IDS = {
    "session_sweep_run": "session-sweep",
    "session_backfill_run": "session-sweep",
}


def _sweep(
    workspace_root,
    items: Iterable[dict],
    *,
    receipt_type: str,
    source_skill: str,
    sessions_scanned: int,
    window_desc: str,
    extra_receipt: Optional[dict] = None,
    fired_via: str = "scheduled",
) -> dict:
    """Dedup + write + receipt. The one write path shared by the nightly sweep
    and the historical backfill. Returns a receipt dict the skill renders."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    # W4c relevance gate: resolve the capture context once per run (who the
    # user is, the team, the customize-layer mode). Fail-open — an
    # unresolvable user forces track-everything inside the helper.
    ctx = workspace_capture_context(workspace_root)

    recovered: list[dict] = []
    by_type: Counter = Counter()
    seen: set[str] = set()
    skipped = 0

    for raw in items:
        event, h = _normalize_item(raw, source_skill)
        # Two dedup layers, both on the CONTENT hash: the durable index (across
        # every prior run) and a within-batch set (two identical items in one
        # extraction). Neither uses the session-level source_ref.
        if h in seen or already_captured(workspace_root, h):
            skipped += 1
            continue
        seen.add(h)
        # W4c: a recovered commitment opens ONLY when the workspace owner is
        # a party (or the mode/caution rail says otherwise); third-party and
        # can't-attribute items land as observed tier — the full record,
        # silent, promotable. Sessions carry no org context, so no override.
        if event["type"] == "commitment":
            verdict = classify_capture(
                event["data"],
                mode=ctx["mode"],
                user_id=ctx["user_id"],
                user_names=ctx["user_names"],
                team_ids=ctx["team_ids"],
                known_ids=ctx["known_ids"],
            )
            if verdict["tier"] == "observed":
                event = observed_from_commitment_event(
                    event, reason=verdict["reason"]
                )
        recovered.append(event)
        by_type[event["type"]] += 1

    if recovered:
        # One gated, locked append for the whole batch — seq/ts stamping and
        # `.source_refs.idx` maintenance happen inside atomic_append_jsonl.
        append_event(events_path, recovered, holder=source_skill)

    receipt_data = {
        # v4.5.2 R1 receipt-contract fields (shared/RECEIPT_CONTRACT.md):
        # canonical task identity + fired_via + machine on every receipt.
        "task_id": _RECEIPT_TASK_IDS.get(receipt_type, source_skill),
        "kind": _RECEIPT_TASK_IDS.get(receipt_type, source_skill),
        "status": "complete",
        "fired_via": fired_via,
        "sessions_scanned": int(sessions_scanned),
        "events_recovered": len(recovered),
        "skipped_dedup": skipped,
        "by_type": dict(by_type),
        "window": window_desc,
    }
    try:
        from receipts import _machine_name

        machine = _machine_name()
        if machine:
            receipt_data["machine"] = machine
    except Exception:
        pass
    if extra_receipt:
        receipt_data.update(extra_receipt)
    append_event(
        events_path,
        {"type": receipt_type, "source_skill": source_skill, "data": receipt_data},
        holder=source_skill,
    )

    return {
        "events_recovered": len(recovered),
        "skipped_dedup": skipped,
        "sessions_scanned": int(sessions_scanned),
        "by_type": dict(by_type),
        "recovered_summaries": [e["data"]["summary"] for e in recovered],
    }


def sweep_and_receipt(
    workspace_root,
    items: Iterable[dict],
    *,
    sessions_scanned: int,
    source_skill: str = "session-sweep",
    window_hours: int = 24,
    window_desc: Optional[str] = None,
    fired_via: str = "scheduled",
) -> dict:
    """Nightly sweep (R1): dedup + write the extracted items, append one
    `session_sweep_run` receipt. `items` is the skill's extraction; every write
    is idempotent, so a re-run over the same window is a clean no-op.

    Pass `window_desc` when the real window is a since-cursor span — the
    default `last-Nh` label on a cursor-scoped run is the F-08 P2c / F-33
    receipt-metadata inaccuracy (record the real window, not a default).
    `fired_via`: "scheduled" on the nightly cron; "manual" on a chat-phrase
    or Run Now fire (v4.5.2 receipt contract)."""
    return _sweep(
        workspace_root,
        items,
        receipt_type=RECEIPT_SWEEP,
        source_skill=source_skill,
        sessions_scanned=sessions_scanned,
        window_desc=window_desc or f"last-{int(window_hours)}h",
        extra_receipt={"window_hours": int(window_hours)},
        fired_via=fired_via,
    )


def _load_events(workspace_root) -> list[dict]:
    try:
        from event_refs import load_events

        return load_events(Path(workspace_root) / "_hq" / "data" / "events.jsonl")
    except Exception:
        return []


def _receipt_ts(ev: dict) -> str:
    try:
        from event_time import event_time

        return event_time(ev) or ""
    except Exception:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        return ev.get("ts") or d.get("ts") or ""


def last_sweep(workspace_root, *, receipt_type: str = RECEIPT_SWEEP) -> Optional[dict]:
    """The newest sweep receipt event (or None). Its timestamp is the cursor the
    skill uses to scope 'sessions active since the last sweep'."""
    newest = None
    newest_ts = ""
    for ev in _load_events(workspace_root):
        if ev.get("type") != receipt_type:
            continue
        ts = _receipt_ts(ev)
        if newest is None or ts >= newest_ts:
            newest, newest_ts = ev, ts
    return newest


def validate_sweep_ran(
    workspace_root,
    *,
    receipt_type: str = RECEIPT_SWEEP,
    since_ts: Optional[str] = None,
) -> dict:
    """Read the receipt back and confirm the sweep genuinely ran (Bug #98
    doctrine — the audit event is the proof, not the narration).

    Returns {ran, ok, last_ts, events_recovered, sessions_scanned}. `ok` is True
    only when a receipt exists AND (no since_ts, or the receipt is at/after it),
    so the skill can prove THIS run landed rather than an old one.
    """
    ev = last_sweep(workspace_root, receipt_type=receipt_type)
    if ev is None:
        return {"ran": False, "ok": False, "last_ts": None,
                "events_recovered": 0, "sessions_scanned": 0}
    ts = _receipt_ts(ev)
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    ok = bool(ts) and (since_ts is None or ts >= since_ts)
    return {
        "ran": True,
        "ok": ok,
        "last_ts": ts or None,
        "events_recovered": int(data.get("events_recovered") or 0),
        "sessions_scanned": int(data.get("sessions_scanned") or 0),
    }


# -----------------------------------------------------------------------------
# Historical backfill (R2) — one-time, supervised, preview-and-confirm.
# -----------------------------------------------------------------------------
# The backfill reuses the exact `_sweep` write path (so dedup + append are
# identical to the nightly pass); it adds a PREVIEW that dedup-checks without
# writing, and a snapshot-before-touch archive step. History stays additive-only
# (§3.1) — the snapshot is an archived safety copy, never a rewrite/undo path.


def preview_items(workspace_root, items: Iterable[dict], *, source_skill: str = "session-backfill") -> dict:
    """Dedup-check the extracted items WITHOUT writing anything — the plan the
    backfill skill shows the CEO before it commits. Same dedup logic as the
    write path (`_normalize_item` + content-hash membership), so the preview
    count equals what a confirm would actually append."""
    by_type: Counter = Counter()
    seen: set[str] = set()
    would: list[str] = []
    skipped = 0
    ctx = workspace_capture_context(workspace_root)
    for raw in items:
        event, h = _normalize_item(raw, source_skill)
        if h in seen or already_captured(workspace_root, h):
            skipped += 1
            continue
        seen.add(h)
        # Same W4c relevance routing as the write path — the preview's
        # by_type must equal what a confirm would actually append.
        if event["type"] == "commitment":
            verdict = classify_capture(
                event["data"],
                mode=ctx["mode"],
                user_id=ctx["user_id"],
                user_names=ctx["user_names"],
                team_ids=ctx["team_ids"],
                known_ids=ctx["known_ids"],
            )
            if verdict["tier"] == "observed":
                event = observed_from_commitment_event(
                    event, reason=verdict["reason"]
                )
        by_type[event["type"]] += 1
        would.append(event["data"]["summary"])
    return {
        "would_recover": len(would),
        "skipped_dedup": skipped,
        "by_type": dict(by_type),
        "sample_summaries": would[:10],
    }


def _snapshot_events(workspace_root, archive_dir=None):
    """Archive a copy of events.jsonl BEFORE the backfill appends (archive-
    never-delete / snapshot-before-touch, §3.1). Returns the snapshot path, or
    None when there is nothing to snapshot. Never overwrites an existing
    snapshot in the same dir (a second confirm in one day keeps the first)."""
    ep = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not ep.exists():
        return None
    if archive_dir is None:
        import datetime as _d

        archive_dir = Path(workspace_root) / "_archive" / f"session-backfill_{_d.date.today().isoformat()}"
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / "events.jsonl"
    if not dest.exists():
        import shutil

        shutil.copy2(ep, dest)
    return dest


def backfill_and_receipt(
    workspace_root,
    items: Iterable[dict],
    *,
    days: int = 60,
    sessions_scanned: int,
    source_skill: str = "session-backfill",
    archive_dir=None,
) -> dict:
    """One-time historical backfill (R2): snapshot events.jsonl, then dedup +
    write the extracted items and land one `session_backfill_run` receipt.

    Call ONLY after the CEO confirmed the `preview_items` plan (this skill is
    ship-don't-run / supervised). Idempotent via the same content-hash dedup —
    a re-run after a prior backfill recovers only genuinely-new items.
    """
    snap = _snapshot_events(workspace_root, archive_dir)
    result = _sweep(
        workspace_root,
        items,
        receipt_type=RECEIPT_BACKFILL,
        source_skill=source_skill,
        sessions_scanned=sessions_scanned,
        window_desc=f"last-{int(days)}d",
        extra_receipt={"days": int(days), "snapshot": str(snap) if snap else None},
        fired_via="manual",  # backfill is always CEO-confirmed / supervised
    )
    result["snapshot"] = str(snap) if snap else None
    return result


def validate_backfill_ran(workspace_root, *, since_ts: Optional[str] = None) -> dict:
    """Read the `session_backfill_run` receipt back (the supervised run's proof).
    Same contract as `validate_sweep_ran`, on the backfill receipt type."""
    return validate_sweep_ran(workspace_root, receipt_type=RECEIPT_BACKFILL, since_ts=since_ts)


def last_backfill(workspace_root) -> Optional[dict]:
    """The newest `session_backfill_run` receipt (or None) — lets the skill warn
    'you already backfilled on <date>' before a second supervised run."""
    return last_sweep(workspace_root, receipt_type=RECEIPT_BACKFILL)


__all__ = [
    "SWEEPABLE_TYPES",
    "RECEIPT_SWEEP",
    "RECEIPT_BACKFILL",
    "SweepItemError",
    "session_source_ref",
    "content_hash",
    "already_captured",
    "sweep_and_receipt",
    "last_sweep",
    "validate_sweep_ran",
    "preview_items",
    "backfill_and_receipt",
    "validate_backfill_ran",
    "last_backfill",
]
