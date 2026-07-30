#!/usr/bin/env python3
"""
Outcome tracking v1 (SPEC B6) — what happens AFTER Command Room produces work.

Two outcomes, both code-computed (never LLM-judged — these feed ROI receipts, so
they must be reproducible; Bug #99 precedent):

  1. Email replies — for every CR-drafted email that was actually sent, detect
     within 7 days whether the thread got a counterparty reply. Emits one
     terminal `email_outcome` event per send (`replied` / `no_reply_7d` /
     `bounced`). Idempotent: exactly one terminal outcome per `sent_event_seq`.

  2. Commitment punctuality — computed READ-SIDE from existing
     `commitment` / `commitment_resolved` pairs. NO new events (derived data
     that's re-derivable from the log stays out of the append-only source).

Architecture mirrors `reconcile_sent_commitments.py`: a pure compute core
(`classify_outcomes`, no I/O, no clock — the clock is passed in), a loader, an
orchestrator that does the writes (`watch_and_receipt`), and read-side stats.

CLIENT SAFETY: metadata only — never reply content or subject. Never raises into
a scheduled fire on a per-send basis; an unresolvable thread ages out of the
21-day window rather than polluting the stats.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

# cru_match is the canonical events reader + ts/commitment-field helpers.
try:
    from cru_match import (
        load_events_defensively,
        _parse_ts,
        _commitment_field,
        _commitment_id,
        _now_iso,
    )
except Exception:  # pragma: no cover - import-path guard for direct CLI use
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import (  # type: ignore
        load_events_defensively,
        _parse_ts,
        _commitment_field,
        _commitment_id,
        _now_iso,
    )

try:
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from event_time import event_time  # type: ignore

_TERMINAL_OUTCOMES = ("replied", "no_reply_7d", "bounced")
_DAEMON_MARKERS = ("mailer-daemon", "postmaster")


# ---------------------------------------------------------------------------
# Loader — the tracked population (idempotence boundary)
# ---------------------------------------------------------------------------

def load_pending_tracked_sends(
    events_jsonl_path: str | Path,
    *,
    window_days: int = 21,
    now_iso: Optional[str] = None,
) -> List[dict]:
    """`email_sent` events from the last `window_days` that don't yet have a
    terminal `email_outcome` referencing their `seq`. Newest-first.

    The dedupe key is the `email_sent` event's own `seq` (== `sent_event_seq`).
    A send older than the window is dropped (its outcome can no longer be
    determined reliably and isn't worth a Gmail fetch)."""
    events, _ = load_events_defensively(events_jsonl_path)

    terminal: set = set()
    for ev in events:
        if ev.get("type") == "email_outcome":
            seq = (ev.get("data") or {}).get("sent_event_seq")
            if seq is not None:
                terminal.add(seq)

    now_dt = _parse_ts(now_iso or _now_iso())
    floor_dt = None
    if now_dt is not None:
        from datetime import timedelta
        floor_dt = now_dt - timedelta(days=window_days)

    pending: List[tuple] = []
    for ev in events:
        if ev.get("type") != "email_sent":
            continue
        seq = ev.get("seq")
        if seq is None or seq in terminal:
            continue
        sent_ts = event_time(ev) or (ev.get("data") or {}).get("sent_ts")
        sent_dt = _parse_ts(sent_ts)
        if floor_dt is not None and sent_dt is not None and sent_dt < floor_dt:
            continue
        d = ev.get("data") or {}
        # Dual-format reads (connector-agnostic-v1): legacy id fields first
        # (every historical row), structured provenance as the fallback for
        # new-format rows (provenance.native_id = message id,
        # provenance.thread_native_id = thread id).
        prov = d.get("provenance") if isinstance(d.get("provenance"), dict) else {}
        pending.append(
            (
                sent_dt,
                {
                    "sent_event_seq": seq,
                    "draft_ref": d.get("draft_event_seq"),
                    "recipient": d.get("recipient"),
                    "gmail_thread_id": d.get("gmail_thread_id") or prov.get("thread_native_id"),
                    "gmail_message_id": d.get("gmail_message_id") or prov.get("native_id"),
                    "sent_ts": sent_ts,
                },
            )
        )
    # Newest first (None timestamps sort last).
    pending.sort(key=lambda t: (t[0] is not None, t[0]), reverse=True)
    return [p for _, p in pending]


# ---------------------------------------------------------------------------
# Pure classifier — no I/O, no clock (now is passed in)
# ---------------------------------------------------------------------------

def _addr_is_user(sender: str, user_addrs: Sequence[str]) -> bool:
    s = (sender or "").lower()
    return any(a and a.lower() in s for a in user_addrs)


def _addr_is_daemon(sender: str) -> bool:
    s = (sender or "").lower()
    return any(m in s for m in _DAEMON_MARKERS)


def classify_outcomes(
    tracked_sends: List[dict],
    thread_states: Dict,
    *,
    user_email_addresses: Sequence[str],
    now_iso: str,
    no_reply_days: int = 7,
) -> dict:
    """Classify each tracked send into a terminal outcome (or leave it pending).

    `thread_states` is keyed by `sent_event_seq`; each value is
    `{"messages": [{"from": str, "ts": iso, "message_id": str}, ...]}`. A send
    with no thread state (unresolvable id) is left pending — it ages out of the
    window rather than polluting the stats (D4).

    Returns `{"outcomes": [<event-payload dicts>], "counts": {...}}`. Only
    TERMINAL outcomes appear in `outcomes`; still-pending sends emit nothing.
    Pure: comparisons use `now_iso`, never a live clock."""
    now_dt = _parse_ts(now_iso)
    out: List[dict] = []
    counts = {"replied": 0, "no_reply_7d": 0, "bounced": 0, "still_pending": 0}

    for send in tracked_sends:
        seq = send.get("sent_event_seq")
        sent_dt = _parse_ts(send.get("sent_ts"))
        state = thread_states.get(seq) if isinstance(thread_states, dict) else None
        msgs = (state or {}).get("messages") or []

        # 1. Bounce — checked BEFORE replied (a bounce beats a later human reply).
        bounce = next((m for m in msgs if _addr_is_daemon(m.get("from", ""))), None)
        if bounce is not None:
            out.append(_payload(send, "bounced", None, bounce.get("message_id")))
            counts["bounced"] += 1
            continue

        # 2. Reply — a message after the sent ts from a non-user, non-daemon sender.
        reply = None
        for m in sorted(msgs, key=lambda x: (_parse_ts(x.get("ts")) is not None, _parse_ts(x.get("ts")))):
            m_dt = _parse_ts(m.get("ts"))
            if sent_dt is not None and m_dt is not None and m_dt <= sent_dt:
                continue
            if _addr_is_user(m.get("from", ""), user_email_addresses):
                continue
            if _addr_is_daemon(m.get("from", "")):
                continue
            reply = (m, m_dt)
            break
        if reply is not None:
            m, m_dt = reply
            latency = None
            if sent_dt is not None and m_dt is not None:
                latency = round((m_dt - sent_dt).total_seconds() / 86400.0, 1)
            out.append(_payload(send, "replied", latency, m.get("message_id")))
            counts["replied"] += 1
            continue

        # 3. no_reply_7d — terminal once the window has elapsed with nothing.
        if now_dt is not None and sent_dt is not None:
            from datetime import timedelta
            if now_dt >= sent_dt + timedelta(days=no_reply_days):
                out.append(_payload(send, "no_reply_7d", None, None))
                counts["no_reply_7d"] += 1
                continue

        counts["still_pending"] += 1

    return {"outcomes": out, "counts": counts}


def _payload(send: dict, outcome: str, latency, reply_message_id) -> dict:
    # MAILSEAM item 5 — DUAL-WRITE the thread id, the same posture
    # `connector_adapters.provenance.build_email_sent_provenance` established
    # for the send event. `gmail_thread_id` is a LEGACY FIELD NAME carrying
    # whatever the declared backend's native thread id is: on a Superhuman or
    # Outlook workspace the name has always lied about the value. It stays,
    # because reader back-compat is forever and history is never rewritten —
    # but `thread_native_id` ships alongside it so a new reader never has to
    # spell a provider to ask for a thread.
    tid = send.get("gmail_thread_id")
    return {
        "sent_event_seq": send.get("sent_event_seq"),
        "draft_ref": send.get("draft_ref"),
        "recipient": send.get("recipient"),
        "gmail_thread_id": tid,
        "thread_native_id": tid,
        "outcome": outcome,
        "latency_days": latency,
        "reply_message_id": reply_message_id,
    }


# ---------------------------------------------------------------------------
# Orchestrator — appends the terminal email_outcome events
# ---------------------------------------------------------------------------

def _resolve_user_emails(workspace_root) -> List[str]:
    """Best-effort set of the primary user's email addresses (for "did THEY
    reply vs did I"). Never raises — returns [] if unresolvable."""
    addrs: List[str] = []
    try:
        import json
        from primary_user import resolve_primary_user
        ent_path = Path(workspace_root) / "_hq" / "data" / "entities.json"
        raw = json.loads(ent_path.read_text(encoding="utf-8"))
        ent = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
        ws = ent.get("workspace") if isinstance(ent.get("workspace"), dict) else {}
        uid = resolve_primary_user(workspace_root)
        for p in ent.get("people") or []:
            if p.get("id") == uid:
                d = p.get("data") or {}
                for key in ("emails", "email_addresses"):
                    v = p.get(key) or d.get(key)
                    if isinstance(v, list):
                        addrs.extend(str(x) for x in v)
                for key in ("email", "primary_email"):
                    v = p.get(key) or d.get(key)
                    if isinstance(v, str):
                        addrs.append(v)
        for key in ("user_email", "primary_email"):
            v = ws.get(key)
            if isinstance(v, str):
                addrs.append(v)
    except Exception:
        return [a for a in addrs if a]
    return [a for a in addrs if a]


def watch_and_receipt(
    workspace_root,
    thread_states: Dict,
    *,
    now_iso: Optional[str] = None,
    no_reply_days: int = 7,
    window_days: int = 21,
    user_email_addresses: Optional[Sequence[str]] = None,
    source_skill: str = "reconcile-sent",
) -> dict:
    """Load pending tracked sends, classify against `thread_states`, append the
    terminal `email_outcome` events. Returns a summary suitable for merging into
    the `sent_reconcile` audit event (`reconcile_and_receipt(outcome_watch_summary=…)`).

    Idempotent: a re-run finds the now-terminal sends already excluded by
    `load_pending_tracked_sends`, so it appends zero new events."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    now = now_iso or _now_iso()
    pending = load_pending_tracked_sends(events_path, window_days=window_days, now_iso=now)
    emails = list(user_email_addresses) if user_email_addresses else _resolve_user_emails(workspace_root)

    res = classify_outcomes(
        pending, thread_states or {},
        user_email_addresses=emails, now_iso=now, no_reply_days=no_reply_days,
    )
    outcomes = res["outcomes"]

    events_written = 0
    if outcomes:
        from next_seq import next_seq
        from atomic_write import atomic_append_jsonl
        seq = next_seq(str(events_path))
        rows = []
        for o in outcomes:
            rows.append({
                "seq": seq,
                "ts": now,
                "type": "email_outcome",
                "source_skill": source_skill,
                "data": o,
            })
            seq += 1
        atomic_append_jsonl(events_path, rows)
        events_written = len(rows)

    counts = res["counts"]
    checked = len(pending)
    summary = (
        f"Checked {checked} sent message{'s' if checked != 1 else ''} for replies — "
        f"{counts['replied']} replied, {counts['no_reply_7d']} went quiet, "
        f"{counts['bounced']} bounced, {counts['still_pending']} still pending."
        if checked else "No sends in the outcome window to check."
    )
    return {
        "ran": True,
        "checked": checked,
        "replied": counts["replied"],
        "no_reply_7d": counts["no_reply_7d"],
        "bounced": counts["bounced"],
        "still_pending": counts["still_pending"],
        "events_written": events_written,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Commitment punctuality — read-side, no new events (D5)
# ---------------------------------------------------------------------------

_CLOSER_TYPES = ("commitment_resolved", "thread_resolved", "commitment_superseded")


def commitment_punctuality(events: List[dict], *, as_of_iso: str) -> dict:
    """Per-commitment punctuality, computed from existing events only.

    Buckets: on_time | late | open_past_due | open_not_due | no_due_date.
    Returns `{"per_commitment": [...], "aggregates": {<bucket>: count}}`.
    Pure — `as_of_iso` is the clock; no `datetime.now()` here."""
    as_of = _parse_ts(as_of_iso)

    # Earliest resolved ts per commitment id (mirrors load_open_commitments' closer chain).
    resolved_ts: Dict[str, object] = {}
    for ev in events:
        if ev.get("type") not in _CLOSER_TYPES:
            continue
        d = ev.get("data") or {}
        cid = (
            d.get("commitment_id") or d.get("thread_id") or d.get("id")
            or d.get("target_id") or ev.get("commitment_id") or ev.get("thread_id")
            or ev.get("id")
        )
        if not cid:
            continue
        rdt = _parse_ts(event_time(ev))
        if cid not in resolved_ts or (rdt is not None and (resolved_ts[cid] is None or rdt < resolved_ts[cid])):
            resolved_ts[cid] = rdt

    per: List[dict] = []
    agg = {"on_time": 0, "late": 0, "open_past_due": 0, "open_not_due": 0, "no_due_date": 0}
    for ev in events:
        if ev.get("type") != "commitment":
            continue
        cid = _commitment_id(ev)
        due_raw = _commitment_field(ev, "due")
        due = _parse_ts(due_raw) if isinstance(due_raw, str) else None
        rdt = resolved_ts.get(cid)

        if due is None:
            bucket = "no_due_date"
        elif cid in resolved_ts:
            bucket = "on_time" if (rdt is not None and rdt <= due) else "late"
        elif as_of is not None and as_of > due:
            bucket = "open_past_due"
        else:
            bucket = "open_not_due"

        agg[bucket] += 1
        per.append({
            "commitment_id": cid,
            "due": due_raw,
            "resolved_ts": (rdt.isoformat() if rdt is not None else None) if cid in resolved_ts else None,
            "bucket": bucket,
        })

    return {"per_commitment": per, "aggregates": agg}


__all__ = [
    "load_pending_tracked_sends",
    "classify_outcomes",
    "watch_and_receipt",
    "commitment_punctuality",
]
