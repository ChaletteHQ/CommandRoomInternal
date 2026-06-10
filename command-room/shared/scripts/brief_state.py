#!/usr/bin/env python3
"""
brief_state.py — the deterministic commitment-state computer (v3.14.8+).

WHY THIS EXISTS
===============
Command Room's commitment surfacing ("you owe 7 · they owe 0 · 2 stuck",
"reply to X to lock the time") was historically computed by LLM-interpreted
prose spread across morning-briefing Step 3b/3c/3c-bis and the commitments
orchestrator. The logic is pure data math — is this commitment open? overdue?
did the user already reply (by email OR calendar)? has the thread gone quiet? —
but living as prose, it drifted: the model re-derived it every fire and
occasionally got it wrong. The v3.14.7 live bug (a scheduling thread answered by
booking a calendar invite still surfaced as "reply to X to lock time") was one
instance of a recurring class.

This module is the single place that computes that state, deterministically and
test-covered. The skills STILL fetch the raw inputs (Gmail latest-sender,
Calendar events, events.jsonl) — connector I/O stays with the harness — but the
DECISIONS ("does this drop? is it overdue? whose turn is it?") move here, out of
prose. morning-briefing and the commitments orchestrator call
`compute_brief_state()` and render its output instead of re-deriving the rules.

No I/O. Pure functions over data the caller supplies. Same inputs → same output.

THE DROP RULES (why a "you owe" item does NOT surface under Needs Attention)
===========================================================================
Applied in priority order; first match wins and is recorded in `dropped` for
auditability:

1. calendar_action — a calendar event with the counter-party fulfills a
   scheduling commitment (delegates to cru_match Path 5). Closes the v3.14.7
   bug at the surfacing layer regardless of whether the daily resolver has run.
2. email_reply — the linked thread's latest message is FROM the user; they
   already replied, so the ball is not on them (morning-briefing Step 3c).
3. recent_activity — the linked thread had ANY activity in the last 7 days; the
   work is probably done but not formally closed, so surfacing it as overdue is
   noise (morning-briefing Step 3b 7-day stopgap).

Header counts (you owe / they owe / stuck) count ALL open commitments and are
NOT affected by the drops — the header preserves true workspace state; only the
surfaced Needs-Attention list is filtered. This matches the pre-existing
contract in morning-briefing Step 3b.
"""
from __future__ import annotations

import datetime
from typing import Any, Iterable, Optional

# Reuse the canonical shape-aware readers + the calendar matcher so there is ONE
# definition of "open commitment", "owner", "due", and "calendar fulfills this".
from cru_match import (
    _commitment_field,
    _commitment_id,
    match_calendar_to_commitments,
)

RECENT_ACTIVITY_WINDOW_DAYS = 7


def _parse_date(value: Optional[str]) -> Optional[datetime.date]:
    """Best-effort parse of a due-date / timestamp string to a date. Accepts
    full ISO timestamps ("2026-05-29T08:00:00Z") and bare dates ("2026-05-29").
    Returns None if unparseable — callers treat None as "no known due date"
    (never overdue), the conservative choice.
    """
    if not value or not isinstance(value, str):
        return None
    head = value.strip()[:10]
    try:
        return datetime.date.fromisoformat(head)
    except ValueError:
        return None


def is_overdue(due_value: Optional[str], now_iso: str) -> bool:
    """True iff `due_value` parses to a date strictly before today's date.
    Unparseable / missing due → False (an undated commitment is not overdue).
    """
    due = _parse_date(due_value)
    if due is None:
        return False
    today = _parse_date(now_iso)
    if today is None:
        return False
    return due < today


def _within_recent_window(activity_iso: Optional[str], now_iso: str,
                          *, days: int = RECENT_ACTIVITY_WINDOW_DAYS) -> bool:
    """True iff `activity_iso` is within `days` of `now_iso` (inclusive)."""
    act = _parse_date(activity_iso)
    now = _parse_date(now_iso)
    if act is None or now is None:
        return False
    return (now - act).days <= days and act <= now


# Default: a sent-reconcile cursor older than this many days is "stale".
RECONCILE_STALE_DAYS = 1


def reconcile_is_stale(sent_reconcile_cursor: Optional[str], now_iso: str,
                       *, days: int = RECONCILE_STALE_DAYS) -> bool:
    """True iff sent-mail reconciliation is behind: the cursor is absent, or it
    is more than `days` days older than now. When True, the brief MUST soften any
    commitment the user owes — they may have already completed it by a sent email
    that hasn't been reconciled yet — instead of telling them to redo it. This is
    the deterministic floor for Bug #98: even if a given run skips the actual
    reconciliation fetch, the brief still won't send the CEO to redo done work."""
    cur = _parse_date(sent_reconcile_cursor)
    now = _parse_date(now_iso)
    if cur is None or now is None:
        return True
    return (now - cur).days > days


def compute_brief_state(
    *,
    open_commitments: list[dict],
    user_person_id: str,
    now_iso: str,
    threads: Optional[dict] = None,
    calendar_events: Optional[Iterable[dict]] = None,
    thread_activity: Optional[dict] = None,
    sent_reconcile_cursor: Optional[str] = None,
) -> dict:
    """Compute the deterministic commitment state for a brief / commitments fire.

    Inputs (all caller-supplied; this function does NO connector I/O):
      open_commitments: list of open commitment event dicts, exactly as returned
        by `cru_match.load_open_commitments`.
      user_person_id: the primary user's person_id.
      now_iso: current time as an ISO string (caller passes it — keeps the
        function pure and testable; never call datetime.now() in here).
      threads: optional dict keyed by thread_id →
        {"latest_sender_is_user": bool, "latest_msg_ts": iso}. The caller resolves
        each linked thread's latest message once (Gmail get_thread) and records
        whether the user was the latest sender. Threads not present → no
        email_reply drop applied for that commitment.
      calendar_events: optional iterable of Path-5-shaped calendar event dicts
        ({attendee_person_ids, summary, created_ts, accepted_by, calendar_event_id}).
        Passed straight to `match_calendar_to_commitments`.
      thread_activity: optional dict keyed by thread_id → latest-activity ISO
        string (max ts of any event on that thread). Drives the 7-day stopgap.

    Returns:
      {
        "counts": {"you_owe": int, "they_owe": int, "stuck": int},
        "needs_attention": [  # you-owe items that survived ALL drops
            {"commitment_id", "title", "owner_id", "thread_id", "due",
             "overdue": bool}
        ],
        "dropped": [ {"commitment_id", "reason"} ],  # reason in
            # {"calendar_action", "email_reply", "recent_activity"}
      }

    Surfacing scope: `needs_attention` contains only commitments the USER owes —
    the "ball is on you" / "reply to X" class where the recurring bug lived.
    Counter-party-owed items ("they owe") are counted in the header but resolved
    by the inbound/transcript paths, not surfaced-then-dropped here.
    """
    threads = threads or {}
    thread_activity = thread_activity or {}
    # Deterministic Bug #98 floor: is sent-mail reconciliation behind?
    reconcile_stale = reconcile_is_stale(sent_reconcile_cursor, now_iso)

    you_owe = 0
    they_owe = 0
    unowned = 0
    stuck = 0
    you_owe_commitments: list[dict] = []

    for ev in open_commitments:
        owner = _commitment_field(ev, "owner_id")
        if is_overdue(_commitment_field(ev, "due"), now_iso):
            stuck += 1
        if owner == user_person_id:
            you_owe += 1
            you_owe_commitments.append(ev)
        elif owner:
            they_owe += 1
        else:
            # Commitment with no resolvable owner_id (extraction gap). Counted
            # in neither directional bucket, but it IS an open commitment — it
            # must still appear in the canonical total or the brief header
            # (you_owe + they_owe) silently undercounts vs the coach's
            # len(load_open_commitments). That 2-item gap was the v3.18.4 A85
            # parity FAIL (Bug #85-followup). `total` below is the single
            # canonical count both surfaces report.
            unowned += 1

    # Calendar-action drops: one batch call over all you-owe commitments.
    calendar_resolved_ids: set[str] = set()
    if calendar_events:
        cal_results = match_calendar_to_commitments(
            open_commitments=you_owe_commitments,
            user_person_id=user_person_id,
            calendar_events=calendar_events,
        )
        calendar_resolved_ids = {
            r["commitment_id"] for r in cal_results
            if r["recommendation"] == "auto_resolve"
        }

    needs_attention: list[dict] = []
    dropped: list[dict] = []

    for ev in you_owe_commitments:
        cid = _commitment_id(ev)
        thread_id = ev.get("primary_thread_id") or ""

        # Priority order: first matching drop wins.
        if cid in calendar_resolved_ids:
            dropped.append({"commitment_id": cid, "reason": "calendar_action"})
            continue
        t = threads.get(thread_id)
        if t and t.get("latest_sender_is_user"):
            dropped.append({"commitment_id": cid, "reason": "email_reply"})
            continue
        if _within_recent_window(thread_activity.get(thread_id), now_iso):
            dropped.append({"commitment_id": cid, "reason": "recent_activity"})
            continue

        due = _commitment_field(ev, "due")
        needs_attention.append({
            "commitment_id": cid,
            "title": _commitment_field(ev, "title") or "",
            "owner_id": user_person_id,
            "thread_id": thread_id,
            "due": due,
            "overdue": is_overdue(due, now_iso),
            # When True the brief MUST soften this item (you may have already sent
            # the email that closes it) rather than telling the CEO to redo it.
            "reconcile_stale": reconcile_stale,
        })

    return {
        "counts": {
            "you_owe": you_owe,
            "they_owe": they_owe,
            "unowned": unowned,
            "stuck": stuck,
            # Canonical open-commitment total == len(open_commitments). Both the
            # brief header and the coach MUST report THIS number (Bug #85 + the
            # A85 followup). you_owe + they_owe alone drops ownerless items.
            "total": you_owe + they_owe + unowned,
        },
        "needs_attention": needs_attention,
        "dropped": dropped,
        # True iff sent-mail reconciliation is behind (cursor stale/absent). The
        # brief reads this to soften you-owe items instead of telling the CEO to
        # redo work they may have already completed by an unreconciled send.
        "reconcile_stale": reconcile_stale,
    }


def compute_and_log_brief_state(workspace_root, *, source_skill="morning-briefing", **kwargs):
    """Compute the brief state AND emit a `brief_state` audit event carrying the
    CODE's real numbers (Bug #99).

    Why this exists: the brief was caught hand-rolling its counts instead of
    calling `compute_brief_state` (it matched the function's output by luck, then
    bypassed the drop rules it would get subtly wrong over time). You can't force
    a pure, cheap function call with a narration gate — but you CAN make the
    bypass DETECTABLE, the same way the `sent_reconcile` audit event made a skipped
    reconcile detectable (Bug #98-v3). The brief renders from THIS wrapper's
    return value; the wrapper emits a `brief_state` event whose counts come from
    `compute_brief_state` itself (not from anything the model typed). A brief with
    no `brief_state` event for its fire bypassed the computer — checkable in the
    verify loop, no honesty self-report required.

    `kwargs` are passed straight through to `compute_brief_state`.
    """
    state = compute_brief_state(**kwargs)
    try:
        from pathlib import Path as _Path
        from next_seq import next_seq as _next_seq
        from atomic_write import atomic_append_jsonl as _append
        from cru_match import _now_iso as _ts
        events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        _append(events_path, [{
            "seq": _next_seq(str(events_path)),
            "ts": _ts(),
            "type": "brief_state",
            "source_skill": source_skill,
            "data": {
                "counts": state["counts"],
                "n_needs_attention": len(state["needs_attention"]),
                "reconcile_stale": state["reconcile_stale"],
            },
        }])
    except Exception:
        # Never let the audit write block the brief — the state is what matters.
        pass
    return state


def latest_brief_state_event(workspace_root) -> dict | None:
    """Return the most recent `brief_state` event's data dict (Bug #99 check), or
    None if the brief never logged one — i.e. it bypassed compute_brief_state."""
    import json
    from pathlib import Path as _Path
    p = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return None
    latest = None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") == "brief_state":
            latest = e
    return (latest or {}).get("data") if latest else None


__all__ = [
    "RECENT_ACTIVITY_WINDOW_DAYS",
    "RECONCILE_STALE_DAYS",
    "is_overdue",
    "reconcile_is_stale",
    "compute_brief_state",
    "compute_and_log_brief_state",
    "latest_brief_state_event",
]
