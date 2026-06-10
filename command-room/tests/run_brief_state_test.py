#!/usr/bin/env python3
"""
Tests for shared/scripts/brief_state.py — the deterministic commitment-state
computer (v3.14.8+).

Covers:
  - is_overdue (parseable / bare-date / unparseable / future)
  - compute_brief_state header counts (you owe / they owe / stuck)
  - the three Needs-Attention drops (calendar_action, email_reply,
    recent_activity) + priority ordering
  - the v3.14.7 regression replayed end-to-end through the brief computer
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from brief_state import (  # noqa: E402
    RECENT_ACTIVITY_WINDOW_DAYS,
    compute_brief_state,
    compute_and_log_brief_state,
    latest_brief_state_event,
    is_overdue,
    reconcile_is_stale,
)

NOW = "2026-05-29T17:00:00Z"


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK {label}")
    else:
        print(f"  FAIL {label}{(' --- ' + detail) if detail else ''}")
        raise AssertionError(label)


def _commit(*, owner, others, title, id_, due=None, thread="thread_001"):
    data = {"owner_id": owner, "title": title, "status": "open", "id": id_}
    if due:
        data["due"] = due
    return {
        "seq": 1, "type": "commitment", "primary_thread_id": thread,
        "person_ids": [owner] + others, "data": data,
    }


# -------- is_overdue --------


def test_is_overdue_past_date():
    print("test_is_overdue_past_date")
    _check("past date overdue", is_overdue("2026-05-01", NOW))
    _check("past timestamp overdue", is_overdue("2026-05-01T09:00:00Z", NOW))


def test_is_overdue_future_and_today():
    print("test_is_overdue_future_and_today")
    _check("future not overdue", not is_overdue("2026-06-15", NOW))
    _check("today not overdue", not is_overdue("2026-05-29", NOW))


def test_is_overdue_unparseable_or_missing():
    print("test_is_overdue_unparseable_or_missing")
    _check("None not overdue", not is_overdue(None, NOW))
    _check("garbage not overdue", not is_overdue("next Tuesday", NOW))
    _check("empty not overdue", not is_overdue("", NOW))


# -------- header counts --------


def test_counts_you_owe_they_owe_stuck():
    print("test_counts_you_owe_they_owe_stuck")
    opens = [
        _commit(owner="user_m", others=["person_bo"], title="Set up call with Bo",
                id_="c1"),
        _commit(owner="user_m", others=["person_rio"], title="Send Rio the deck",
                id_="c2", due="2026-05-01"),  # overdue
        _commit(owner="person_lyra", others=["user_m"], title="Lyra sends spec",
                id_="c3"),  # they owe
    ]
    state = compute_brief_state(open_commitments=opens, user_person_id="user_m",
                                now_iso=NOW)
    _check("you_owe == 2", state["counts"]["you_owe"] == 2, str(state["counts"]))
    _check("they_owe == 1", state["counts"]["they_owe"] == 1, str(state["counts"]))
    _check("stuck == 1", state["counts"]["stuck"] == 1, str(state["counts"]))


def test_counts_unaffected_by_drops():
    """Header counts reflect TRUE state even when items are dropped from the
    surfaced list — the morning-briefing Step 3b contract."""
    print("test_counts_unaffected_by_drops")
    opens = [_commit(owner="user_m", others=["person_bo"],
                     title="Set up call with Bo", id_="c1")]
    state = compute_brief_state(
        open_commitments=opens, user_person_id="user_m", now_iso=NOW,
        threads={"thread_001": {"latest_sender_is_user": True}},
    )
    _check("you_owe still counts the dropped item", state["counts"]["you_owe"] == 1)
    _check("but it's not in needs_attention", state["needs_attention"] == [])
    _check("recorded as email_reply drop",
           state["dropped"] == [{"commitment_id": "c1", "reason": "email_reply"}],
           str(state["dropped"]))


# -------- drops --------


def test_email_reply_drop():
    print("test_email_reply_drop")
    opens = [_commit(owner="user_m", others=["person_bo"], title="Reply to Bo",
                     id_="c1")]
    state = compute_brief_state(
        open_commitments=opens, user_person_id="user_m", now_iso=NOW,
        threads={"thread_001": {"latest_sender_is_user": True}},
    )
    _check("dropped via email_reply", state["needs_attention"] == [])


def test_no_drop_when_latest_sender_is_counterparty():
    print("test_no_drop_when_latest_sender_is_counterparty")
    opens = [_commit(owner="user_m", others=["person_bo"], title="Reply to Bo",
                     id_="c1")]
    state = compute_brief_state(
        open_commitments=opens, user_person_id="user_m", now_iso=NOW,
        threads={"thread_001": {"latest_sender_is_user": False}},
    )
    _check("surfaces when ball is on user", len(state["needs_attention"]) == 1,
           str(state))


def test_recent_activity_drop():
    print("test_recent_activity_drop")
    opens = [_commit(owner="user_m", others=["person_bo"], title="Reply to Bo",
                     id_="c1", due="2026-05-01")]
    state = compute_brief_state(
        open_commitments=opens, user_person_id="user_m", now_iso=NOW,
        thread_activity={"thread_001": "2026-05-27T00:00:00Z"},  # 2 days ago
    )
    _check("overdue item dropped due to recent activity",
           state["needs_attention"] == [], str(state))
    _check("drop reason recent_activity",
           state["dropped"][0]["reason"] == "recent_activity")


def test_stale_activity_does_not_drop():
    print("test_stale_activity_does_not_drop")
    opens = [_commit(owner="user_m", others=["person_bo"], title="Reply to Bo",
                     id_="c1", due="2026-05-01")]
    state = compute_brief_state(
        open_commitments=opens, user_person_id="user_m", now_iso=NOW,
        thread_activity={"thread_001": "2026-05-10T00:00:00Z"},  # 19 days ago
    )
    _check("stale thread still surfaces", len(state["needs_attention"]) == 1,
           str(state))


def test_calendar_action_drop_takes_priority():
    """The v3.14.7 regression through the brief computer: a scheduling
    commitment with a matching calendar event drops even though the email
    thread's latest sender is still the counter-party."""
    print("test_calendar_action_drop_takes_priority")
    opens = [_commit(owner="user_m", others=["person_bo"],
                     title="Set up the build call with Bo", id_="c1")]
    state = compute_brief_state(
        open_commitments=opens, user_person_id="user_m", now_iso=NOW,
        threads={"thread_001": {"latest_sender_is_user": False}},  # they emailed last
        calendar_events=[{
            "attendee_person_ids": ["user_m", "person_bo"],
            "summary": "Bo Sample / Sam Sample",
            "created_ts": "2026-05-29T15:29:44Z",
            "accepted_by": ["person_bo"],
            "calendar_event_id": "evt_1",
        }],
    )
    _check("calendar-closed scheduling item does NOT surface",
           state["needs_attention"] == [], str(state))
    _check("dropped via calendar_action",
           state["dropped"] == [{"commitment_id": "c1", "reason": "calendar_action"}],
           str(state["dropped"]))


def test_deliverable_not_dropped_by_unrelated_meeting():
    """A non-scheduling commitment with the same person must STILL surface even
    if a meeting got booked — the calendar drop is scheduling-only."""
    print("test_deliverable_not_dropped_by_unrelated_meeting")
    opens = [_commit(owner="user_m", others=["person_bo"],
                     title="Send Bo the pricing one-pager", id_="c1")]
    state = compute_brief_state(
        open_commitments=opens, user_person_id="user_m", now_iso=NOW,
        threads={"thread_001": {"latest_sender_is_user": False}},
        calendar_events=[{
            "attendee_person_ids": ["user_m", "person_bo"],
            "summary": "Bo / Sam sync",
            "calendar_event_id": "evt_1",
        }],
    )
    _check("deliverable still surfaces", len(state["needs_attention"]) == 1,
           str(state))


def test_no_inputs_surfaces_all_you_owe():
    print("test_no_inputs_surfaces_all_you_owe")
    opens = [
        _commit(owner="user_m", others=["person_bo"], title="Reply to Bo", id_="c1"),
        _commit(owner="user_m", others=["person_rio"], title="Reply to Rio", id_="c2",
                thread="thread_002"),
    ]
    state = compute_brief_state(open_commitments=opens, user_person_id="user_m",
                                now_iso=NOW)
    _check("both surface when no drop signals", len(state["needs_attention"]) == 2,
           str(state))
    _check("no drops recorded", state["dropped"] == [])


def test_reconcile_stale_flag():
    """Bug #98-v2 deterministic floor: when the sent-reconcile cursor is absent or
    >1 day old, compute_brief_state flags reconcile_stale (top-level + per item) so
    the brief softens you-owe items instead of telling the CEO to redo work a
    skipped reconcile would have closed."""
    print("test_reconcile_stale_flag")
    # NOW = 2026-05-29.
    _check("None cursor is stale", reconcile_is_stale(None, NOW) is True)
    _check("2-days-old cursor is stale", reconcile_is_stale("2026-05-27T00:00:00Z", NOW) is True)
    _check("today's cursor is fresh", reconcile_is_stale("2026-05-29T06:00:00Z", NOW) is False)
    _check("1-day-old cursor is within threshold (not stale)",
           reconcile_is_stale("2026-05-28T06:00:00Z", NOW) is False)

    opens = [_commit(owner="user_m", others=["person_bo"], title="Send Bo the deck", id_="c1")]
    # Stale cursor -> flag set top-level AND on the surfaced item.
    s_stale = compute_brief_state(open_commitments=opens, user_person_id="user_m",
                                  now_iso=NOW, sent_reconcile_cursor="2026-05-26T00:00:00Z")
    _check("top-level reconcile_stale True", s_stale["reconcile_stale"] is True, str(s_stale))
    _check("surfaced item carries reconcile_stale True",
           s_stale["needs_attention"] and s_stale["needs_attention"][0]["reconcile_stale"] is True,
           str(s_stale["needs_attention"]))
    # Fresh cursor -> not stale; item not flagged.
    s_fresh = compute_brief_state(open_commitments=opens, user_person_id="user_m",
                                  now_iso=NOW, sent_reconcile_cursor="2026-05-29T06:00:00Z")
    _check("top-level reconcile_stale False with fresh cursor",
           s_fresh["reconcile_stale"] is False, str(s_fresh))
    _check("item not flagged with fresh cursor",
           s_fresh["needs_attention"] and s_fresh["needs_attention"][0]["reconcile_stale"] is False,
           str(s_fresh["needs_attention"]))
    # Default (no cursor passed) is conservative: stale -> soften (safe direction).
    s_default = compute_brief_state(open_commitments=opens, user_person_id="user_m", now_iso=NOW)
    _check("absent cursor defaults to stale (safe: soften)",
           s_default["reconcile_stale"] is True, str(s_default))


def test_compute_and_log_brief_state():
    """Bug #99: the logging wrapper computes via compute_brief_state AND emits a
    brief_state audit event carrying the CODE's real numbers, so a brief that
    hand-rolls (and never calls the wrapper) leaves NO brief_state event — making
    the bypass detectable, the same trick that caught #98."""
    import json
    import tempfile
    import os as _os
    print("test_compute_and_log_brief_state")
    tmp = tempfile.mkdtemp()
    data = _os.path.join(tmp, "_hq", "data")
    _os.makedirs(data)
    open(_os.path.join(data, "events.jsonl"), "w").close()

    # Before any compute: no brief_state event -> bypass would be detectable.
    _check("no brief_state event yet -> latest is None (bypass detectable)",
           latest_brief_state_event(tmp) is None)

    opens = [_commit(owner="user_m", others=["person_bo"], title="Send Bo the deck", id_="c1")]
    state = compute_and_log_brief_state(
        tmp, open_commitments=opens, user_person_id="user_m", now_iso=NOW,
        sent_reconcile_cursor="2026-05-29T06:00:00Z")
    _check("wrapper returns the same state shape (counts present)",
           state["counts"]["you_owe"] == 1, str(state["counts"]))

    rows = [json.loads(l) for l in open(_os.path.join(data, "events.jsonl")) if l.strip()]
    bs = [r for r in rows if r.get("type") == "brief_state"]
    _check("exactly one brief_state audit event written", len(bs) == 1, bs)
    d = bs[0].get("data") or {}
    _check("event carries the CODE's real counts.total + reconcile_stale + n_needs_attention",
           d.get("counts", {}).get("total") == state["counts"]["total"]
           and "reconcile_stale" in d and "n_needs_attention" in d, d)
    _check("latest_brief_state_event reads it back",
           latest_brief_state_event(tmp) is not None
           and latest_brief_state_event(tmp)["counts"]["total"] == state["counts"]["total"])

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    tests = [
        test_compute_and_log_brief_state,
        test_is_overdue_past_date,
        test_is_overdue_future_and_today,
        test_is_overdue_unparseable_or_missing,
        test_counts_you_owe_they_owe_stuck,
        test_counts_unaffected_by_drops,
        test_email_reply_drop,
        test_no_drop_when_latest_sender_is_counterparty,
        test_recent_activity_drop,
        test_stale_activity_does_not_drop,
        test_calendar_action_drop_takes_priority,
        test_deliverable_not_dropped_by_unrelated_meeting,
        test_no_inputs_surfaces_all_you_owe,
        test_reconcile_stale_flag,
    ]
    for t in tests:
        t()
    print(f"\nOK {len(tests)} brief_state tests passed")


if __name__ == "__main__":
    main()
