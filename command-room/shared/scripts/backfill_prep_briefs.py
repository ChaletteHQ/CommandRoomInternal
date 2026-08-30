#!/usr/bin/env python3
"""
backfill_prep_briefs — SPEC THREADBIND1 §0 ruling 3: the 30-day prep_brief
thread-binding one-shot.

WHY THIS EXISTS. `prep_brief` receipts bind going forward (`receipts.
log_prep_receipt` now calls `thread_resolve.resolve_thread_binding` at write time —
SPEC THREADBIND1 ruling 3). Every receipt written BEFORE that landed still
carries only `meeting_id`, unbound. This module is the one-shot that walks
the RECENT pile and binds what it safely can.

WHAT THIS MODULE IS (EXCHBACK1's shape, applied to a different append-only
record — `exchange_backfill.py` is the direct precedent, read it first)
  load_pile             — PURE READ. `prep_brief` receipts from the last
                           `WINDOW_DAYS` (30) with no resolved thread (their
                           own `data.thread_id` absent AND `thread_resolve`
                           finds no PRIOR resolution either — a receipt a
                           second run already bound is not re-proposed).
  propose_backfill       — READ-ONLY. Walks a batch, resolves each row's
                           evidence (the meeting's attendees, read off the
                           matching `meeting` event's `person_ids`), and
                           reports ONLY the rows THREADBIND1's resolver can
                           actually bind (`action: "apply"`) plus rows it
                           cannot (`unadjudicable` — no `meeting` event left
                           to resolve, or the resolver still refuses:
                           ambiguous org / no evidence). Writes NOTHING.
  apply_backfill          — SUPERVISED WRITE. Re-derives the delta set FRESH
                           (never trusts a stale propose report — the same
                           EXCHBACK1 discipline), then appends ONE
                           `prep_brief_thread_backfilled` marker per
                           CONFIRMED id, in ONE freshly minted `thb_` brain
                           batch (`brain_undo.undo_batch` reverses the whole
                           run via the registered `prep_brief_thread_backfill`
                           reverser). ONE receipt event
                           (`prep_brief_backfill_run`) closes the run.

THE DOCTRINE (SPEC THREADBIND1 §0 ruling 3, mirroring EXCHBACK1 §0)
  1. Supervised, never silent. `propose_backfill` proposes; nothing changes
     state without an explicit confirmed meeting id (or the literal "all")
     reaching `apply_backfill`. `batch_cap` bounds a single walk.
  2. 30-day window, NOT beyond. Outputs read recent memory; re-binding an
     old brief is risk without value (EXCHBACK1's own pile-walk posture,
     ruling 3's own words).
  3. Canonical writer: the ONE write path here is the additive
     `prep_brief_thread_backfilled` marker — never a rewrite of the
     `prep_brief` receipt itself (receipts are append-only).
  4. Honest absence. A brief whose meeting no longer resolves (no `meeting`
     event survives to read attendees from) is reported `unadjudicable`,
     never guessed at.

Stdlib only except the plugin's own shared/scripts imports.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# 30-day window (ruling 3). Not a knob a caller widens — the ruling's own
# reasoning (recent memory only) is the point, not a default.
WINDOW_DAYS = 30

BATCH_PREFIX = "thb_"
BATCH_SALT_BYTES = 4

CHANGE_CLASS = "prep_brief_thread_backfill"
RECEIPT_EVENT_TYPE = "prep_brief_backfill_run"

DEFAULT_BATCH_CAP = 25


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _now(now_iso: Optional[str] = None):
    from event_time import parse_ts

    return (parse_ts(str(now_iso)) if now_iso else None) \
        or _dt.datetime.now(_dt.timezone.utc)


def _now_iso(now_iso: Optional[str] = None) -> str:
    return _now(now_iso).isoformat()


def _mint_batch_id(now_iso: Optional[str] = None) -> str:
    """`thb_<UTC-to-second>-<8 hex>`. One human-confirmed gesture = one
    batch = one `undo` (same mint shape as EXCHBACK1's `exb_`, CLUSTER1's
    `clu_` — own prefix)."""
    import secrets

    stamp = _now(now_iso).astimezone(_dt.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(BATCH_SALT_BYTES)}"


def _batch_stamp(batch_id: str) -> dict:
    """Factored out so the mutation suite can drop the stamp BY NAME
    (`backfill_prep_briefs._batch_stamp = lambda batch_id: {}`) and watch
    the undo round-trip pin go red — same removal-proof shape as
    `exchange_backfill._batch_stamp`."""
    return {"brain_batch_id": batch_id, "brain_change_class": CHANGE_CLASS}


# -----------------------------------------------------------------------------
# The pile
# -----------------------------------------------------------------------------


def load_pile(workspace_root, *, now_iso: Optional[str] = None,
              window_days: int = WINDOW_DAYS) -> list:
    """The RECENT `prep_brief` receipts with no thread bound (SPEC §The
    work, bullet 4). Read-only. Oldest first.

    A receipt whose OWN `data.thread_id` is already set is excluded (bound
    at write time — ruling 3's forward-binding leg already covered it,
    nothing for the backfill to do).

    Org-scoped (D4c/PGUARD1): a prep_brief is business context, never
    personal-lane."""
    from events_io import load_events_org_scoped

    cutoff = _now(now_iso) - _dt.timedelta(days=window_days)
    from event_time import parse_ts

    out = []
    events, _skipped = load_events_org_scoped(workspace_root)
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "prep_brief":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if str(data.get("thread_id") or "").strip():
            continue
        ts = parse_ts(ev.get("ts"))
        if ts is not None and ts < cutoff:
            continue
        out.append(ev)
    return out


def _meeting_id_of(row: dict) -> str:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    return str(data.get("meeting_id") or "").strip()


def _matching_meeting_event(events: list, meeting_id: str) -> Optional[dict]:
    from meeting_capture import meeting_ref_keys

    wanted = meeting_ref_keys(meeting_id)
    if not wanted:
        return None
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "meeting":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if meeting_ref_keys(data.get("source_ref")) & wanted:
            return ev
    return None


# -----------------------------------------------------------------------------
# Propose — read-only.
# -----------------------------------------------------------------------------


def propose_backfill(
    workspace_root,
    *,
    now_iso: Optional[str] = None,
    batch_cap: Optional[int] = DEFAULT_BATCH_CAP,
    offset: int = 0,
) -> dict:
    """READ-ONLY. Walks up to `batch_cap` pile rows starting at `offset`,
    resolves each row's evidence (the matching `meeting` event's attendees),
    and reports the rows THREADBIND1's resolver can bind. Writes NOTHING —
    not `events.jsonl`, not `entities.json` (Acceptance: byte-identical).

    `batch_cap=None` walks the whole pile in one pass (used internally by
    `apply_backfill`, which must never act on a stale sub-window)."""
    from events_io import load_events_org_scoped
    from thread_resolve import BIND_CONFIDENCE_FLOOR, resolve_thread_binding

    pile = load_pile(workspace_root, now_iso=now_iso)
    total_pile = len(pile)
    window = pile[offset:offset + batch_cap] if batch_cap else pile[offset:]
    all_events, _skipped = load_events_org_scoped(workspace_root)

    deltas: list = []
    unadjudicable: list = []
    n_unchanged = 0

    for row in window:
        meeting_id = _meeting_id_of(row)
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        slug = str(data.get("slug") or "")
        if not meeting_id:
            unadjudicable.append({
                "meeting_id": "", "slug": slug,
                "reason": "receipt carries no meeting_id to resolve",
            })
            continue
        meeting_ev = _matching_meeting_event(all_events, meeting_id)
        if meeting_ev is None:
            unadjudicable.append({
                "meeting_id": meeting_id, "slug": slug,
                "reason": "the meeting no longer resolves — no `meeting` "
                         "event survives to read attendees from",
            })
            continue
        attendee_person_ids = list(meeting_ev.get("person_ids") or [])
        result = resolve_thread_binding(
            {"meeting_id": meeting_id,
             "attendee_person_ids": attendee_person_ids},
            workspace_root=workspace_root)
        # A `meeting_binding` (or `explicit`) verdict means the meeting was
        # ALREADY resolved by something other than this one-shot — its own
        # `primary_thread_id`, or a PRIOR backfill run's marker
        # (`thread_resolve._meeting_bound_thread_id` folds both in). That is
        # not new work for THIS backfill to propose; ruling §0-3's own
        # "UNCHANGED verdict is untouched" rule (EXCHBACK1's `_is_delta`)
        # applies here too. Only `org_single_thread` — the fresh evidence
        # this one-shot specifically contributes — is a genuine delta.
        if (result["thread_id"] and result["confidence"] >= BIND_CONFIDENCE_FLOOR
                and result["basis"] not in ("meeting_binding", "explicit")):
            deltas.append({
                "meeting_id": meeting_id, "slug": slug,
                "thread_id": result["thread_id"], "basis": result["basis"],
                "action": "apply",
            })
        elif result["basis"] in ("meeting_binding", "explicit"):
            n_unchanged += 1
        else:
            n_unchanged += 1
            unadjudicable.append({
                "meeting_id": meeting_id, "slug": slug,
                "reason": f"resolver refused ({result['basis']}) — no "
                         "confident evidence to bind",
            })

    walked = len(window)
    remaining = max(0, total_pile - offset - walked)
    return {
        "workspace_root": str(workspace_root),
        "generated_at": _now_iso(now_iso),
        "rows_in_pile": total_pile,
        "rows_walked": walked,
        "offset": offset,
        "batch_cap": batch_cap,
        "remaining_after_this_batch": remaining,
        "deltas": deltas,
        "n_deltas": len(deltas),
        "n_applicable": sum(1 for d in deltas if d["action"] == "apply"),
        "unchanged": n_unchanged,
        "unadjudicable": unadjudicable,
        "n_unadjudicable": len(unadjudicable),
    }


# -----------------------------------------------------------------------------
# Apply — supervised, one additive marker per confirmed row.
# -----------------------------------------------------------------------------


def apply_backfill(
    workspace_root,
    meeting_ids,
    *,
    applied_by: str,
    source_skill: str,
    now_iso: Optional[str] = None,
) -> dict:
    """SUPERVISED WRITE (ruling 3) — the ONE write path is the additive
    `prep_brief_thread_backfilled` marker (never a rewrite of the receipt
    itself — `prep_brief` is append-only).

    `meeting_ids`: the human's CONFIRMATION — a list of the specific
    meeting ids to bind (from a `propose_backfill` report the caller
    already showed a person), or the literal string `"all"` to apply every
    currently applicable delta.

    Re-derives the delta set FRESH (a whole-pile `propose_backfill`, never
    a stale cached report) so a row resolved between propose and apply is
    never double-acted-on. Every applied row's marker is stamped into ONE
    freshly minted `thb_` brain batch, so `brain_undo.undo_batch(batch_ref)`
    reverses the whole run. ONE receipt event (`prep_brief_backfill_run`)
    closes the run.

    Returns {"status", "batch_id", "batch_ref", "n_applied", "n_skipped",
    "results", "receipt", "summary"}."""
    fresh = propose_backfill(workspace_root, now_iso=now_iso, batch_cap=None)
    applicable = {d["meeting_id"]: d for d in fresh["deltas"]
                 if d["action"] == "apply"}

    if meeting_ids == "all":
        targets = list(applicable.keys())
    else:
        targets = [str(m) for m in (meeting_ids or [])]

    from event_gate import append_event

    batch_id = _mint_batch_id(now_iso)
    events_path = _events_path(workspace_root)
    stamp = _batch_stamp(batch_id)
    results: list = []
    n_applied = 0
    n_skipped = 0

    for mid in targets:
        delta = applicable.get(mid)
        if delta is None:
            results.append({
                "meeting_id": mid, "status": "skipped",
                "detail": "not a currently applicable delta — already "
                          "resolved, no longer in the pile, or the "
                          "resolver no longer confirms it",
            })
            n_skipped += 1
            continue
        data = {"meeting_id": mid, "thread_id": delta["thread_id"],
                "thread_basis": delta["basis"], "applied_by": applied_by}
        data.update(stamp)
        append_event(events_path, [{
            "type": "prep_brief_thread_backfilled",
            "source_skill": source_skill,
            "data": data,
        }], holder=source_skill)
        n_applied += 1
        results.append({"meeting_id": mid, "status": "applied",
                        "thread_id": delta["thread_id"]})

    receipt_data = {
        "batch_id": batch_id,
        "rows_in_pile": fresh["rows_in_pile"],
        "rows_walked": fresh["rows_walked"],
        "deltas_found": fresh["n_deltas"],
        "applied": n_applied,
        "skipped": n_skipped,
        "unadjudicable": fresh["n_unadjudicable"],
        "confirmed_by": applied_by,
    }
    append_event(events_path, [{
        "type": RECEIPT_EVENT_TYPE,
        "source_skill": source_skill,
        "data": receipt_data,
    }], holder=source_skill)

    if n_applied:
        status = "applied"
    elif not targets:
        status = "no_op"
    else:
        status = "error"
    noun = "brief" if n_applied == 1 else "briefs"
    summary = (f"Prep-brief thread backfill — {n_applied} {noun} bound. "
              f"Say `undo` to release them.")
    if n_skipped:
        summary += f" {n_skipped} skipped — details per row."

    return {
        "status": status,
        "batch_id": batch_id,
        "batch_ref": {"kind": "brain_batch", "batch_id": batch_id},
        "n_applied": n_applied,
        "n_skipped": n_skipped,
        "results": results,
        "receipt": receipt_data,
        "summary": summary,
    }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def _print_propose_report(report: dict) -> None:
    print("Prep-brief thread backfill — propose (writes nothing)")
    print(f"  pile: {report['rows_in_pile']} rows  walked: {report['rows_walked']}"
         f" (offset {report['offset']}, cap {report['batch_cap']})")
    print(f"  deltas: {report['n_deltas']} ({report['n_applicable']} applicable)"
         f"  unadjudicable: {report['n_unadjudicable']}")
    for d in report["deltas"]:
        print(f"  [{d['action']}] {d['meeting_id']}  -> {d['thread_id']} "
             f"({d['basis']})")
    for u in report["unadjudicable"]:
        print(f"  [unadjudicable] {u['meeting_id'] or '(no meeting_id)'}  "
             f"{u['reason']}")
    if report["remaining_after_this_batch"]:
        nxt = report["offset"] + (report["batch_cap"] or 0)
        print(f"  {report['remaining_after_this_batch']} more rows remain — "
             f"run again with --offset {nxt}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="SPEC THREADBIND1 — supervised 30-day prep_brief "
                    "thread-binding backfill.")
    sub = ap.add_subparsers(dest="command", required=True)

    p_propose = sub.add_parser("propose", help="read-only delta report")
    p_propose.add_argument("workspace")
    p_propose.add_argument("--now", default=None, help="ISO now override (tests)")
    p_propose.add_argument("--batch-cap", type=int, default=DEFAULT_BATCH_CAP)
    p_propose.add_argument("--offset", type=int, default=0)
    p_propose.add_argument("--json", action="store_true")

    p_apply = sub.add_parser("apply", help="supervised apply of confirmed deltas")
    p_apply.add_argument("workspace")
    p_apply.add_argument("--ids", required=True,
                         help="comma-separated confirmed meeting ids, or "
                              "the literal 'all' — explicit, the "
                              "least-supervised path must never be the "
                              "default (EXCHBACK1's own R3 rule)")
    p_apply.add_argument("--applied-by", required=True)
    p_apply.add_argument("--source-skill", default="backfill-prep-briefs")
    p_apply.add_argument("--now", default=None)
    p_apply.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.command == "propose":
        report = propose_backfill(args.workspace, now_iso=args.now,
                                  batch_cap=args.batch_cap, offset=args.offset)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            _print_propose_report(report)
        return 0

    if args.command == "apply":
        ids = "all" if args.ids == "all" else [
            s.strip() for s in args.ids.split(",") if s.strip()
        ]
        result = apply_backfill(args.workspace, ids, applied_by=args.applied_by,
                                source_skill=args.source_skill, now_iso=args.now)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(result["summary"])
        return 0

    return 2


__all__ = [
    "WINDOW_DAYS",
    "BATCH_PREFIX",
    "BATCH_SALT_BYTES",
    "CHANGE_CLASS",
    "RECEIPT_EVENT_TYPE",
    "DEFAULT_BATCH_CAP",
    "load_pile",
    "propose_backfill",
    "apply_backfill",
    "_batch_stamp",
]


if __name__ == "__main__":
    raise SystemExit(main())
