#!/usr/bin/env python3
"""
Tests for shared/scripts/decision_match.py — decision-CRU layer (v3.4.5+).

Covers:
  - Completion / reversal signal detectors
  - load_open_decisions (events.jsonl read, resolved/superseded filter)
  - match_transcript_to_decisions (all recommendation paths)
  - Event-builder helpers (decision_resolved, decision_superseded)
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from decision_match import (  # noqa: E402
    DECISION_HIGH_CONFIDENCE_THRESHOLD,
    build_decision_resolved_event,
    build_decision_superseded_event,
    detect_completion_signal,
    detect_reversal_signal,
    load_open_decisions,
    match_transcript_to_decisions,
)


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK {label}")
    else:
        print(f"  FAIL {label}{(' --- ' + detail) if detail else ''}")
        raise AssertionError(label)


# -------- signal detectors --------


def test_completion_signal_detection():
    print("test_completion_signal_detection")
    _check("'went with' matches", detect_completion_signal("we went with NetSuite in the end"))
    _check("'signed with' matches", detect_completion_signal("Signed with the new vendor yesterday"))
    _check("'committed to' matches", detect_completion_signal("We've committed to the Q3 timeline"))
    _check("no completion language -> false",
           not detect_completion_signal("we talked about pricing for an hour"))
    _check("empty text -> false", not detect_completion_signal(""))
    _check("None text -> false", not detect_completion_signal(None))


def test_reversal_signal_detection():
    print("test_reversal_signal_detection")
    _check("'changed our mind' matches",
           detect_reversal_signal("Actually we changed our mind on the vendor pick"))
    _check("'scratch that' matches",
           detect_reversal_signal("Scratch that, we're going a different direction"))
    _check("'switching to' matches",
           detect_reversal_signal("Switching to Quickbooks instead"))
    _check("'decided against' matches",
           detect_reversal_signal("We decided against the rebrand for now"))
    _check("no reversal language -> false",
           not detect_reversal_signal("we agreed on the plan"))


# -------- load_open_decisions --------


def _write_jsonl(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_load_open_decisions_filters_resolved():
    print("test_load_open_decisions_filters_resolved")
    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "events.jsonl"
        _write_jsonl(events_path, [
            {"seq": 1, "ts": "2026-01-01T00:00:00Z", "type": "decision",
             "source_skill": "decision-log", "primary_thread_id": "project_001",
             "data": {"title": "Pivot to product-led GTM", "id": "decision_1"}},
            {"seq": 2, "ts": "2026-02-01T00:00:00Z", "type": "decision",
             "source_skill": "decision-log", "primary_thread_id": "project_001",
             "data": {"title": "Switch ERP to NetSuite", "id": "decision_2"}},
            {"seq": 3, "ts": "2026-02-15T00:00:00Z", "type": "decision_resolved",
             "source_skill": "cr-past-meetings", "primary_thread_id": "project_001",
             "data": {"decision_id": "decision_2", "evidence": "signed contract"}},
        ])
        opens = load_open_decisions(events_path)
        _check("two decisions, one resolved -> one open", len(opens) == 1,
               f"got {len(opens)}")
        _check("the unresolved one survives",
               opens[0].get("data", {}).get("id") == "decision_1")


def test_load_open_decisions_filters_superseded():
    print("test_load_open_decisions_filters_superseded")
    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "events.jsonl"
        _write_jsonl(events_path, [
            {"seq": 1, "ts": "2026-01-01T00:00:00Z", "type": "decision",
             "source_skill": "decision-log", "primary_thread_id": "project_001",
             "data": {"title": "Use Stripe for billing", "id": "decision_1"}},
            {"seq": 2, "ts": "2026-03-01T00:00:00Z", "type": "decision_superseded",
             "source_skill": "cr-past-meetings", "primary_thread_id": "project_001",
             "data": {"decision_id": "decision_1", "evidence": "switched to Paddle"}},
        ])
        opens = load_open_decisions(events_path)
        _check("superseded decision filtered out", len(opens) == 0,
               f"got {len(opens)}")


def test_load_open_decisions_handles_missing_file():
    print("test_load_open_decisions_handles_missing_file")
    opens = load_open_decisions("/no/such/file.jsonl")
    _check("missing file -> empty list", opens == [])


def test_load_open_decisions_uses_seq_fallback_id():
    print("test_load_open_decisions_uses_seq_fallback_id")
    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "events.jsonl"
        _write_jsonl(events_path, [
            # Decision without explicit data.id — closure should still match
            # via the fallback `decision_seq_<seq>` synthesized id.
            {"seq": 7, "ts": "2026-01-01T00:00:00Z", "type": "decision",
             "source_skill": "decision-log", "primary_thread_id": "project_001",
             "data": {"title": "Hire CTO externally"}},
            {"seq": 8, "ts": "2026-04-01T00:00:00Z", "type": "decision_resolved",
             "source_skill": "cr-past-meetings", "primary_thread_id": "project_001",
             "data": {"decision_id": "decision_seq_7", "evidence": "hired Jane"}},
        ])
        opens = load_open_decisions(events_path)
        _check("seq-fallback id matches closure", len(opens) == 0)


# -------- match_transcript_to_decisions --------


def _decision(title: str, decision_id: str = "decision_001",
              thread_id: str = "project_001",
              person_ids: list[str] | None = None) -> dict:
    ev: dict = {
        "seq": 1, "ts": "2026-01-01T00:00:00Z", "type": "decision",
        "source_skill": "decision-log", "primary_thread_id": thread_id,
        "data": {"title": title, "id": decision_id},
    }
    if person_ids is not None:
        ev["person_ids"] = person_ids
    return ev


def test_match_resolves_on_completion_language():
    print("test_match_resolves_on_completion_language")
    opens = [_decision("Switch ERP vendor to NetSuite")]
    transcript = "Quick update — we signed with NetSuite yesterday on the ERP vendor switch. Implementation kicks off Monday."
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=[],
        transcript_text=transcript,
    )
    _check("returns one result", len(results) == 1)
    _check("recommendation is decision_resolved",
           results[0]["recommendation"] == "decision_resolved",
           f"got {results[0]['recommendation']} score={results[0]['score']}")


def test_match_supersedes_on_reversal_language():
    print("test_match_supersedes_on_reversal_language")
    opens = [_decision("Use Stripe for billing")]
    transcript = "Actually we changed our mind on Stripe — switching to Paddle for billing because of the merchant-of-record handling."
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=[],
        transcript_text=transcript,
    )
    _check("returns one result", len(results) == 1)
    _check("recommendation is decision_superseded",
           results[0]["recommendation"] == "decision_superseded",
           f"got {results[0]['recommendation']} score={results[0]['score']}")


def test_match_no_action_on_topic_mention_only():
    print("test_match_no_action_on_topic_mention_only")
    opens = [_decision("Switch ERP vendor to NetSuite")]
    transcript = "We talked about the ERP vendor question and the NetSuite proposal for about 20 minutes but nothing was decided."
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=[],
        transcript_text=transcript,
    )
    # Topic comes up but no completion + no reversal -> no_action
    _check("topic-only mention does not auto-act",
           results[0]["recommendation"] == "no_action",
           f"got {results[0]['recommendation']} score={results[0]['score']}")


def test_match_no_action_below_threshold():
    print("test_match_no_action_below_threshold")
    opens = [_decision("Switch ERP vendor to NetSuite")]
    # Transcript is about something else entirely
    transcript = "We went with Bowie's pricing proposal for the new SaaS tier."
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=[],
        transcript_text=transcript,
    )
    _check("low-score match -> no_action",
           results[0]["recommendation"] == "no_action")


def test_match_no_action_when_both_signals():
    print("test_match_no_action_when_both_signals")
    # Ambiguous — meeting mentions both executing and switching. Be safe.
    opens = [_decision("Switch ERP vendor to NetSuite")]
    transcript = ("We signed with NetSuite yesterday on the ERP vendor switch, "
                  "but honestly we're already reconsidering — might end up switching to Sage instead.")
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=[],
        transcript_text=transcript,
    )
    _check("ambiguous signals -> no_action",
           results[0]["recommendation"] == "no_action",
           f"got {results[0]['recommendation']} score={results[0]['score']}")


def test_match_respects_attendee_overlap():
    print("test_match_respects_attendee_overlap")
    # Decision involved person_005; meeting attendees are unrelated.
    opens = [_decision("Hire CTO externally",
                       person_ids=["person_005"])]
    transcript = "We went with the external CTO candidate from the recruiter."
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=["person_088"],
        transcript_text=transcript,
    )
    _check("decision tracked specific people, no attendee overlap -> filtered",
           len(results) == 0)


def test_match_workspace_wide_decision_always_eligible():
    print("test_match_workspace_wide_decision_always_eligible")
    # No person_ids on the decision -> workspace-wide, always eligible
    # regardless of attendees.
    opens = [_decision("Switch ERP vendor to NetSuite")]
    transcript = "We signed with NetSuite on the ERP switch."
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=["person_088"],
        transcript_text=transcript,
    )
    _check("workspace-wide decision matches regardless of attendees",
           len(results) == 1 and results[0]["recommendation"] == "decision_resolved")


# -------- event builders --------


def test_build_resolved_event_shape():
    print("test_build_resolved_event_shape")
    ev = build_decision_resolved_event(
        decision_id="decision_001",
        primary_thread_id="project_001",
        source_skill="cr-past-meetings",
        evidence="Signed contract on 2026-04-15",
        next_seq=42,
    )
    _check("seq set", ev["seq"] == 42)
    _check("type is decision_resolved", ev["type"] == "decision_resolved")
    _check("data.decision_id set", ev["data"]["decision_id"] == "decision_001")
    _check("evidence preserved", ev["data"]["evidence"].startswith("Signed contract"))
    _check("ts present", "ts" in ev)


def test_build_superseded_event_shape():
    print("test_build_superseded_event_shape")
    ev = build_decision_superseded_event(
        decision_id="decision_001",
        primary_thread_id="project_001",
        source_skill="cr-past-meetings",
        evidence="Switched to Paddle on 2026-05-01",
        next_seq=43,
        superseded_by_decision_seq=88,
    )
    _check("type is decision_superseded", ev["type"] == "decision_superseded")
    _check("superseded_by_decision_seq set",
           ev["data"]["superseded_by_decision_seq"] == 88)


def test_evidence_truncation():
    print("test_evidence_truncation")
    long_evidence = "x" * 500
    ev = build_decision_resolved_event(
        decision_id="d1", primary_thread_id="p1",
        source_skill="test", evidence=long_evidence, next_seq=1,
    )
    _check("evidence truncated to 200 chars",
           len(ev["data"]["evidence"]) == 200)


# -------- runner --------

if __name__ == "__main__":
    tests = [
        test_completion_signal_detection,
        test_reversal_signal_detection,
        test_load_open_decisions_filters_resolved,
        test_load_open_decisions_filters_superseded,
        test_load_open_decisions_handles_missing_file,
        test_load_open_decisions_uses_seq_fallback_id,
        test_match_resolves_on_completion_language,
        test_match_supersedes_on_reversal_language,
        test_match_no_action_on_topic_mention_only,
        test_match_no_action_below_threshold,
        test_match_no_action_when_both_signals,
        test_match_respects_attendee_overlap,
        test_match_workspace_wide_decision_always_eligible,
        test_build_resolved_event_shape,
        test_build_superseded_event_shape,
        test_evidence_truncation,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError:
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
