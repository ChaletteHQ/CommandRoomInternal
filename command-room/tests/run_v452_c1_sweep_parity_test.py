#!/usr/bin/env python3
"""
v4.5.2 C1 — sweep capture parity + undated-commitment visibility (F-31, F-44).

Fixture is built from the REAL substrate shapes the Jul 7 2026 sweep wrote to
M's live workspace (events.jsonl seq 3540-3546: 1 decision + 6 commitments,
titles verbatim): 0/7 carried `due` or `no_due` despite explicit dates in the
text ("before tomorrow's call", "Thursday"), Stage-E counterparty fields were
empty despite named people, and 5 items were classified `task` instead of
`promise` (F-31). The next morning those items were INVISIBLE in the brief and
every chase surface on the exact day they mattered — two of them were about
that morning's 9:15 "Michele - Matthew - Erick" call (F-44).

Regresses both halves of the C1 fix:

  CAPTURE (session_sweep._gate_commitment — parity with scan-for-commitments
  Step 3, the reference capture block):
  - S2 due-nudge: a commitment with neither a parseable `due` nor explicit
    `no_due: true` fails loud (the "before tomorrow's call" item can no longer
    land undated); due + no_due together is a loud contradiction.
  - Promise-vs-task: a `task` carrying a counterparty is rejected ("send X to
    [person]" is a promise, not a task — F-31's misclassification).
  - Stage-E: a resolved counterparty_id auto-joins person_ids.
  - Safety inversion: pending_review is STAMPED (absence of the flag is not
    consent) for a counterparty name without a person record, a promise with
    no counterparty / no owner, and sub-threshold extraction confidence.

  SURFACE (commitment_state.match_commitments_to_meetings +
  compute_brief_state's meeting_linked):
  - The RAW F-31 shapes (undated, task-kind, counterparty-less, confidence-
    less — exactly as they sit on disk today) match today's 9:15 by
    name-mention, including the transcript-spelling drift "Michelle" ->
    resolved attendee "Michele Jewett" (single-edit tolerance).
  - meeting_linked survives every condition that hid the items before:
    no due date, kind=task, recent thread activity, missing confidence.
  - Post-fix shapes match by counterparty_id.
  - Short names never fuzzy-match (exact only below 5 chars).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from output_exercise_lib import copy_fixture  # noqa: E402
import session_sweep as ss  # noqa: E402
from commitment_state import (  # noqa: E402
    compute_and_log_brief_state,
    compute_brief_state,
    latest_brief_state_event,
    match_commitments_to_meetings,
)

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def _events(ws: Path) -> list[dict]:
    ep = ws / "_hq" / "data" / "events.jsonl"
    return [json.loads(l) for l in ep.read_text(encoding="utf-8").splitlines() if l.strip()]


SESSION = "local_9c085d17-fb64-4425-84e3-702e820177a2"  # the real Jul 7 session id

# The 9:15 the F-44 items were about, as the surface builds it from the
# calendar: resolved attendee ids + display names (entities spelling).
MEETING_915 = {
    "meeting_id": "granola:eb06c827",
    "title": "Michele - Matthew - Erick",
    "attendee_person_ids": ["person_093", "person_001", "person_017"],
    "attendee_names": ["Michele Jewett", "Erick Burg"],
}

# ---------------------------------------------------------------------------
# The raw F-31 events VERBATIM (summaries/titles as written on Jul 7; kind
# task, no due, no counterparty, no confidence). These exist on disk in the
# live substrate — the surface fix must make THESE visible, unmodified.
# ---------------------------------------------------------------------------
RAW_F31_EVENTS = [
    {"type": "commitment", "source_skill": "session-sweep", "seq": 3541,
     "ts": "2026-07-07T23:10:39.556978+00:00", "primary_thread_id": "project_015",
     "data": {"kind": "task", "id": "cmt_01KWZDJ2WCEZY7CVJK37RCVE25",
              "summary": "Send the positioning briefs + collected feedback to messaging collaborator before tomorrow's call (unblocks Michelle one-pager).",
              "title": "Send the positioning briefs + collected feedback to messaging collaborator before tomorrow's call (unblocks Michelle one-pager).",
              "source_ref": f"session:{SESSION}", "session_id": SESSION,
              "recovered_by": "session-sweep"}},
    {"type": "commitment", "source_skill": "session-sweep", "seq": 3542,
     "ts": "2026-07-07T23:10:39.556978+00:00", "primary_thread_id": "project_015",
     "data": {"kind": "task", "id": "cmt_01KWZDJ2WCMDP7PZMSE6AH7NJZ",
              "summary": "Finalize Don's paperwork: $2K from the 14th, two-meeting guarantee, no payout until after the second meeting.",
              "title": "Finalize Don's paperwork: $2K from the 14th, two-meeting guarantee, no payout until after the second meeting.",
              "source_ref": f"session:{SESSION}", "session_id": SESSION,
              "recovered_by": "session-sweep"}},
    {"type": "commitment", "source_skill": "session-sweep", "seq": 3543,
     "ts": "2026-07-07T23:10:39.556978+00:00", "primary_thread_id": "project_015",
     "data": {"kind": "task", "id": "cmt_01KWZDJ2WC1H286AAEYBCPKENQ",
              "summary": "Soft-sell the video-testimonial idea to Michelle at tomorrow's call, framed as her idea.",
              "title": "Soft-sell the video-testimonial idea to Michelle at tomorrow's call, framed as her idea.",
              "source_ref": f"session:{SESSION}", "session_id": SESSION,
              "recovered_by": "session-sweep"}},
    {"type": "commitment", "source_skill": "session-sweep", "seq": 3544,
     "ts": "2026-07-07T23:10:39.556978+00:00", "primary_thread_id": "project_015",
     "data": {"kind": "task", "id": "cmt_01KWZDJ2WCV7DRNAWTWHGAFQQ6",
              "summary": "Hand Brandon the John assets when he is back, to formalize testimonials via secured demos.",
              "title": "Hand Brandon the John assets when he is back, to formalize testimonials via secured demos.",
              "source_ref": f"session:{SESSION}", "session_id": SESSION,
              "recovered_by": "session-sweep"}},
    {"type": "commitment", "source_skill": "session-sweep", "seq": 3545,
     "ts": "2026-07-07T23:10:39.556978+00:00", "primary_thread_id": "project_015",
     "data": {"kind": "task", "id": "cmt_01KWZDJ2WCES5FHF4MJZY75FYK",
              "summary": "Move Evan to the new version as the first user.",
              "title": "Move Evan to the new version as the first user.",
              "source_ref": f"session:{SESSION}", "session_id": SESSION,
              "recovered_by": "session-sweep"}},
    {"type": "commitment", "source_skill": "session-sweep", "seq": 3546,
     "ts": "2026-07-07T23:10:39.556978+00:00", "primary_thread_id": "project_015",
     "data": {"kind": "scheduling", "id": "cmt_01KWZDJ2WCCQ225JEV83BY5PWS",
              "summary": "Kevin call on Thursday.", "title": "Kevin call on Thursday.",
              "source_ref": f"session:{SESSION}", "session_id": SESSION,
              "recovered_by": "session-sweep"}},
]


# ---------------------------------------------------------------------------
# Capture half — the gate rejects F-31's actual shapes and stamps the flags
# ---------------------------------------------------------------------------

def _item(summary, data, **top):
    it = {"session_id": SESSION, "type": "commitment", "summary": summary, "data": data}
    it.update(top)
    return it


def _rejects(label, item, needle):
    try:
        ss._normalize_item(item, "session-sweep")
        check(label, False, "accepted a shape the gate must reject")
    except ss.SweepItemError as e:
        check(label, needle in str(e), f"wrong message: {e}")


def test_due_nudge_gate():
    print("test_due_nudge_gate — S2: silence is not an option")
    # F-31's item 3541 EXACTLY as the Jul 7 extraction emitted it: the text
    # says "before tomorrow's call" and it still landed with no due.
    _rejects("undated 'before tomorrow's call' item is rejected",
             _item(RAW_F31_EVENTS[0]["data"]["summary"], {"kind": "task"}),
             "due")
    _rejects("undated 'Kevin call on Thursday' (scheduling) is rejected",
             _item("Kevin call on Thursday.", {"kind": "scheduling"}),
             "due")
    _rejects("garbage due string is rejected",
             _item("Send the deck", {"kind": "task", "due": "tomorrow"}),
             "due")
    _rejects("due AND no_due together is a loud contradiction",
             _item("Send the deck", {"kind": "task", "due": "2026-07-08",
                                     "no_due": True}),
             "BOTH")
    # Explicit no_due passes.
    ev, _ = ss._normalize_item(
        _item("Move Evan to the new version as the first user.",
              {"kind": "task", "no_due": True}), "session-sweep")
    check("explicit no_due: true is accepted", ev["data"].get("no_due") is True)
    # A due proposed from the source language passes ("before tomorrow's
    # call" in a Jul 7 session -> 2026-07-08).
    ev, _ = ss._normalize_item(
        _item(RAW_F31_EVENTS[0]["data"]["summary"],
              {"kind": "promise", "due": "2026-07-08",
               "counterparty_id": "person_017", "owner_id": "person_001"}),
        "session-sweep")
    check("due inferred from 'before tomorrow's call' is accepted",
          ev["data"].get("due") == "2026-07-08")


def test_promise_vs_task_rule():
    print("test_promise_vs_task_rule — a counterparty makes it a promise")
    # F-31: "send briefs to collaborator" has a counterparty — it is a
    # promise, not a task. A task carrying one is rejected either way.
    _rejects("task with counterparty_id rejected",
             _item("Send the positioning briefs to the collaborator",
                   {"kind": "task", "due": "2026-07-08",
                    "counterparty_id": "person_017"}),
             "promise")
    _rejects("task with counterparty_name rejected",
             _item("Soft-sell the video-testimonial idea to Michelle",
                   {"kind": "task", "due": "2026-07-08",
                    "counterparty_name": "Michelle"}),
             "promise")
    # Same items as promises pass.
    ev, _ = ss._normalize_item(
        _item("Send the positioning briefs to the collaborator",
              {"kind": "promise", "due": "2026-07-08", "owner_id": "person_001",
               "counterparty_id": "person_017"}), "session-sweep")
    check("promise with resolved counterparty accepted",
          ev["data"]["kind"] == "promise")
    check("counterparty_id auto-joins person_ids (Stage E)",
          "person_017" in (ev.get("person_ids") or []), repr(ev.get("person_ids")))


def test_pending_review_inversion():
    print("test_pending_review_inversion — absence of the flag is not consent")
    # Unresolved counterparty name (the sweep refused to guess between
    # Michelle-vs-Michele ids — correct; the flag must be stamped for it).
    ev, _ = ss._normalize_item(
        _item("Soft-sell the video-testimonial idea to Michelle at tomorrow's call",
              {"kind": "promise", "due": "2026-07-08", "owner_id": "person_001",
               "counterparty_name": "Michelle"}), "session-sweep")
    check("counterparty name without record stamps pending_review",
          ev["data"].get("pending_review") is True, repr(ev["data"]))
    check("review_reason is auto-set", bool(ev["data"].get("review_reason")))

    # Promise with no counterparty info at all: capture succeeds, flagged.
    ev, _ = ss._normalize_item(
        _item("Send the follow-up summary", {"kind": "promise", "no_due": True,
                                             "owner_id": "person_001"}),
        "session-sweep")
    check("counterparty-less promise captured but flagged",
          ev["data"].get("pending_review") is True)

    # Ownerless promise: flagged.
    ev, _ = ss._normalize_item(
        _item("Owe the Q2 numbers", {"kind": "promise", "no_due": True,
                                     "counterparty_id": "person_004"}),
        "session-sweep")
    check("ownerless promise flagged", ev["data"].get("pending_review") is True)

    # Sub-threshold extraction confidence: flagged.
    ev, _ = ss._normalize_item(
        _item("Maybe send the deck over", {"kind": "task", "no_due": True},
              classification_confidence=0.4), "session-sweep")
    check("low-confidence extraction flagged",
          ev["data"].get("pending_review") is True)

    # Fully-resolved, confident item: NOT flagged.
    ev, _ = ss._normalize_item(
        _item("Send Priya the Q2 numbers",
              {"kind": "promise", "due": "2026-07-10", "owner_id": "person_001",
               "counterparty_id": "person_004"},
              classification_confidence=0.9), "session-sweep")
    check("fully-attributed confident item carries no flag",
          "pending_review" not in ev["data"], repr(ev["data"]))
    # An extractor-set True is never unset.
    ev, _ = ss._normalize_item(
        _item("Ambiguous thing", {"kind": "promise", "no_due": True,
                                  "owner_id": "person_001",
                                  "counterparty_id": "person_004",
                                  "pending_review": True}), "session-sweep")
    check("extractor-set pending_review survives", ev["data"]["pending_review"] is True)


def test_f31_fixture_reswept_compliant():
    print("test_f31_fixture_reswept_compliant — the 7 items, captured right")
    ws = copy_fixture()
    before = len(_events(ws))
    # The positioning session re-extracted under the fixed Step 3 (same
    # source language, session date Jul 7 -> "tomorrow" = Jul 8; "Thursday"
    # = Jul 9). Names without fixture records stay free-text counterparty_name.
    items = [
        {"session_id": SESSION, "type": "decision",
         "summary": "CR GTM positioning direction set: drop 'AI chief of staff' language for AI-literate audiences."},
        _item(RAW_F31_EVENTS[0]["data"]["summary"],
              {"kind": "promise", "due": "2026-07-08", "owner_id": "person_001",
               "counterparty_id": "person_017"},
              person_ids=["person_017"], classification_confidence=0.85),
        _item(RAW_F31_EVENTS[1]["data"]["summary"],
              {"kind": "promise", "due": "2026-07-14", "owner_id": "person_001",
               "counterparty_name": "Don"}, classification_confidence=0.8),
        _item(RAW_F31_EVENTS[2]["data"]["summary"],
              {"kind": "promise", "due": "2026-07-08", "owner_id": "person_001",
               "counterparty_name": "Michelle"}, classification_confidence=0.8),
        _item(RAW_F31_EVENTS[3]["data"]["summary"],
              {"kind": "promise", "no_due": True, "owner_id": "person_001",
               "counterparty_name": "Brandon"}, classification_confidence=0.75),
        _item(RAW_F31_EVENTS[4]["data"]["summary"],
              {"kind": "task", "no_due": True, "owner_id": "person_001"},
              classification_confidence=0.8),
        _item(RAW_F31_EVENTS[5]["data"]["summary"],
              {"kind": "scheduling", "due": "2026-07-09", "owner_id": "person_001",
               "counterparty_name": "Kevin"}, classification_confidence=0.9),
    ]
    r = ss.sweep_and_receipt(ws, items, sessions_scanned=4)
    check("all 7 recovered", r["events_recovered"] == 7, repr(r))
    added = [e for e in _events(ws)[before:] if e["type"] == "commitment"]
    check("6 commitments landed", len(added) == 6)
    check("6/6 carry due or explicit no_due (F-31 was 0/7)",
          all(e["data"].get("due") or e["data"].get("no_due") is True for e in added))
    named = [e for e in added if e["data"]["kind"] in ("promise", "scheduling")]
    check("every named item carries a counterparty receipt (Stage E)",
          all(e["data"].get("counterparty_id") or e["data"].get("counterparty_name")
              for e in named), repr([e["data"].get("title") for e in named]))
    check("'send briefs' is a promise, not a task (F-31 classification)",
          added[0]["data"]["kind"] == "promise")
    flagged = [e for e in added if e["data"].get("pending_review") is True]
    check("unresolved-name items are pending_review (Don/Michelle/Brandon/Kevin)",
          len(flagged) == 4, repr([e["data"].get("title") for e in flagged]))


# ---------------------------------------------------------------------------
# Surface half — F-44: the RAW on-disk shapes become visible on meeting day
# ---------------------------------------------------------------------------

def test_raw_f31_events_match_todays_meeting():
    print("test_raw_f31_events_match_todays_meeting — F-44 repro on raw shapes")
    rows = match_commitments_to_meetings(
        RAW_F31_EVENTS, [MEETING_915], user_person_id="person_001")
    ids = {r["commitment_id"] for r in rows}
    check("both 9:15-relevant items surface (briefs + soft-sell)",
          ids == {"cmt_01KWZDJ2WCEZY7CVJK37RCVE25", "cmt_01KWZDJ2WC1H286AAEYBCPKENQ"},
          repr(ids))
    check("matched by name-mention despite empty counterparty fields",
          all(r["match"] == "name_mention" for r in rows))
    check("'Michelle' in the sweep text matched resolved attendee 'Michele Jewett'",
          any(r["matched_name"] == "Michele Jewett" for r in rows))
    check("undated items carry due=None, not a crash or a drop",
          all(r["due"] is None for r in rows))
    check("Don / Brandon / Evan / Kevin items do NOT match this meeting",
          not ({"cmt_01KWZDJ2WCMDP7PZMSE6AH7NJZ", "cmt_01KWZDJ2WCV7DRNAWTWHGAFQQ6",
                "cmt_01KWZDJ2WCES5FHF4MJZY75FYK", "cmt_01KWZDJ2WCCQ225JEV83BY5PWS"}
               & ids))


def test_meeting_linked_survives_every_old_hiding_condition():
    print("test_meeting_linked_survives_every_old_hiding_condition")
    # Recent thread activity (project_015 was active the very same night) —
    # the recent_activity drop hides needs_attention items but MUST NOT
    # touch meeting_linked.
    state = compute_brief_state(
        open_commitments=RAW_F31_EVENTS,
        user_person_id="person_001",
        now_iso="2026-07-08T14:00:00+00:00",
        thread_activity={"project_015": "2026-07-07T23:10:39+00:00"},
        todays_meetings=[MEETING_915],
    )
    linked = {r["commitment_id"] for r in state["meeting_linked"]}
    check("meeting_linked populated despite undated + task-kind + fresh thread activity",
          len(linked) == 2, repr(state["meeting_linked"]))
    check("no meetings supplied -> meeting_linked is []",
          compute_brief_state(open_commitments=RAW_F31_EVENTS,
                              user_person_id="person_001",
                              now_iso="2026-07-08")["meeting_linked"] == [])
    # Post-fix shape: resolved counterparty matches by id, not name.
    fixed = {"type": "commitment", "seq": 4001,
             "data": {"kind": "promise", "id": "cmt_FIXED", "title": "Send the positioning briefs",
                      "due": "2026-07-08", "owner_id": "person_001",
                      "counterparty_id": "person_017"}}
    rows = match_commitments_to_meetings([fixed], [MEETING_915],
                                         user_person_id="person_001")
    check("post-fix shape matches by counterparty id",
          len(rows) == 1 and rows[0]["match"] == "counterparty", repr(rows))
    # pending_review rides along as a flag (render-as-confirm, never hidden).
    flagged = {"type": "commitment", "seq": 4002,
               "data": {"kind": "promise", "id": "cmt_PR", "title": "Soft-sell the idea",
                        "no_due": True, "owner_id": "person_001",
                        "counterparty_name": "Michelle", "pending_review": True}}
    rows = match_commitments_to_meetings([flagged], [MEETING_915],
                                         user_person_id="person_001")
    check("pending_review item surfaces WITH its flag",
          len(rows) == 1 and rows[0]["pending_review"] is True, repr(rows))


def test_name_matching_stays_conservative():
    print("test_name_matching_stays_conservative")
    ev = {"type": "commitment", "seq": 4003,
          "data": {"kind": "task", "id": "cmt_X", "title": "Prep the Eva onboarding doc",
                   "no_due": True}}
    meeting = {"meeting_id": "m2", "title": "Sync",
               "attendee_person_ids": [], "attendee_names": ["Evan Sample"]}
    rows = match_commitments_to_meetings([ev], [meeting], user_person_id="person_001")
    check("short names never fuzzy-match ('Eva' text vs attendee 'Evan')",
          rows == [], repr(rows))
    # The primary user attending their own meeting is not a match signal.
    ev2 = {"type": "commitment", "seq": 4004,
           "data": {"kind": "promise", "id": "cmt_Y", "title": "Send the deck",
                    "no_due": True, "owner_id": "person_001",
                    "counterparty_id": "person_004"}}
    meeting2 = {"meeting_id": "m3", "title": "Solo block",
                "attendee_person_ids": ["person_001"], "attendee_names": ["Sam Sample"]}
    rows = match_commitments_to_meetings([ev2], [meeting2], user_person_id="person_001")
    check("primary user's own attendance never links an item", rows == [], repr(rows))


def test_brief_state_audit_carries_meeting_linked_count():
    print("test_brief_state_audit_carries_meeting_linked_count")
    ws = copy_fixture()
    compute_and_log_brief_state(
        ws,
        open_commitments=RAW_F31_EVENTS,
        user_person_id="person_001",
        now_iso="2026-07-08T14:00:00+00:00",
        todays_meetings=[MEETING_915],
    )
    audit = latest_brief_state_event(ws)
    check("brief_state audit event carries n_meeting_linked=2",
          audit is not None and audit.get("n_meeting_linked") == 2, repr(audit))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== v4.5.2 C1 — sweep parity + undated visibility (F-31 / F-44) ===")
    test_due_nudge_gate()
    test_promise_vs_task_rule()
    test_pending_review_inversion()
    test_f31_fixture_reswept_compliant()
    test_raw_f31_events_match_todays_meeting()
    test_meeting_linked_survives_every_old_hiding_condition()
    test_name_matching_stays_conservative()
    test_brief_state_audit_carries_meeting_linked_count()
    print()
    if FAIL:
        print(f"FAIL — {FAIL} of {PASS + FAIL} checks failed")
        return 1
    print(f"OK — all {PASS} C1 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
