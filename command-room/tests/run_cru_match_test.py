#!/usr/bin/env python3
"""
Tests for shared/scripts/cru_match.py — the CRU layer (v2.14.6+).

Covers:
  - Tokenizer + stopword filter
  - score_match (unigram + bigram Jaccard, max-of)
  - Completion / schedule-shift / new-ask signal detectors
  - load_open_commitments (events.jsonl read, resolved-id filter)
  - match_send_to_commitments (Path 1)
  - match_transcript_to_commitments (Path 3 — all 4 recommendation paths)
  - Event-builder helpers (commitment_resolved, commitment_updated, review)
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cru_match import (  # noqa: E402
    HIGH_CONFIDENCE_THRESHOLD,
    PENDING_REVIEW_THRESHOLD,
    build_commitment_resolved_event,
    build_commitment_review_dismissed_event,
    build_commitment_updated_event,
    build_pending_review_event,
    detect_completion_signal,
    detect_new_ask_signal,
    detect_schedule_shift_signal,
    detect_scheduling_intent,
    event_references_person,
    extract_snippet,
    load_open_commitments,
    load_open_review_proposals,
    match_calendar_to_commitments,
    match_inbound_to_commitments,
    match_send_to_commitments,
    match_transcript_to_commitments,
    score_match,
)


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK {label}")
    else:
        print(f"  FAIL {label}{(' --- ' + detail) if detail else ''}")
        raise AssertionError(label)


# -------- score_match --------


def test_identical_titles_score_one():
    print("test_identical_titles_score_one")
    s = score_match("send the pricing deck", "send the pricing deck")
    _check("identical = 1.0", s == 1.0, f"got {s}")


def test_disjoint_score_zero():
    print("test_disjoint_score_zero")
    s = score_match("buy groceries milk eggs", "submit Q3 financial report")
    _check("disjoint = 0.0", s == 0.0, f"got {s}")


def test_partial_overlap_scores_above_threshold():
    print("test_partial_overlap_scores_above_threshold")
    # commitment: "Send updated pricing deck to Mira"
    # email subject + body: "Q2 pricing deck — see attached" — shares "pricing", "deck"
    s = score_match("Q2 pricing deck see attached", "Send updated pricing deck to Mira")
    _check("partial overlap >= PENDING threshold", s >= PENDING_REVIEW_THRESHOLD, f"got {s}")


def test_strong_match_above_high_threshold():
    print("test_strong_match_above_high_threshold")
    # commitment: "Send pricing deck to Mira"
    # email body: "sending pricing deck Mira promised"
    s = score_match("sending pricing deck Mira promised", "Send pricing deck to Mira")
    _check("strong match >= HIGH threshold", s >= HIGH_CONFIDENCE_THRESHOLD, f"got {s}")


def test_empty_inputs_safe():
    print("test_empty_inputs_safe")
    _check("None query = 0.0", score_match(None, "send deck") == 0.0)
    _check("empty title = 0.0", score_match("send deck", "") == 0.0)
    _check("both None = 0.0", score_match(None, None) == 0.0)


def test_stopword_filtering():
    print("test_stopword_filtering")
    # "send the deck to Mira" should match "Send a deck to Mira" strongly
    # despite article differences.
    s = score_match("send the deck to Mira", "Send a deck to Mira")
    _check("stopwords ignored", s >= 0.7, f"got {s}")


# -------- signal detectors --------


def test_completion_signal_positive():
    print("test_completion_signal_positive")
    _check("sent the X", detect_completion_signal("Sent the deck this morning"))
    _check("delivered", detect_completion_signal("Delivered the report yesterday"))
    _check("as promised", detect_completion_signal("As promised, here's the doc"))
    _check("got it", detect_completion_signal("Got it — thanks!"))


def test_completion_signal_negative():
    print("test_completion_signal_negative")
    _check("future tense excluded", not detect_completion_signal("I'll send the deck next week"))
    _check("question excluded", not detect_completion_signal("Did you receive anything?"))


def test_schedule_shift_signal_positive():
    print("test_schedule_shift_signal_positive")
    _check("pushed to", detect_schedule_shift_signal("Pushed to next Friday"))
    _check("rescheduled", detect_schedule_shift_signal("Rescheduled to Q3"))
    _check("delayed until", detect_schedule_shift_signal("Delayed until June"))
    _check("won't make", detect_schedule_shift_signal("won't make Friday — slipping"))


def test_schedule_shift_signal_negative():
    print("test_schedule_shift_signal_negative")
    _check("plain delivery word excluded",
           not detect_schedule_shift_signal("Sent the deck and we're done"))


def test_new_ask_signal_positive():
    print("test_new_ask_signal_positive")
    _check("can you also", detect_new_ask_signal("Can you also send the contract?"))
    _check("additionally please", detect_new_ask_signal("Additionally please share the budget"))
    _check("one more thing", detect_new_ask_signal("One more thing — need the spec too"))


def test_new_ask_signal_negative():
    print("test_new_ask_signal_negative")
    _check("plain ask excluded", not detect_new_ask_signal("Send the deck please"))


# -------- load_open_commitments --------


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_load_open_commitments_filters_resolved():
    print("test_load_open_commitments_filters_resolved")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "type": "commitment", "primary_thread_id": "project_001",
             "person_ids": ["person_001", "person_002"],
             "data": {"id": "c1", "owner_id": "person_001",
                      "title": "Send deck to Mira", "status": "open"}},
            {"seq": 2, "type": "commitment", "primary_thread_id": "project_001",
             "person_ids": ["person_001"],
             "data": {"id": "c2", "owner_id": "person_001",
                      "title": "Review proposal", "status": "open"}},
            # c1 gets resolved
            {"seq": 3, "type": "commitment_resolved",
             "data": {"commitment_id": "c1"}},
            # commitment with no explicit id — synthesized as commitment_seq_4
            {"seq": 4, "type": "commitment", "primary_thread_id": "project_002",
             "person_ids": ["person_001"],
             "data": {"owner_id": "person_001",
                      "title": "Draft brief", "status": "open"}},
        ])
        opens = load_open_commitments(p)
        ids = {(o.get("data") or {}).get("id")
               or f"commitment_seq_{o.get('seq')}" for o in opens}
        _check("c1 filtered (resolved)", "c1" not in ids, f"got {ids}")
        _check("c2 still open", "c2" in ids, f"got {ids}")
        _check("synthesized id present", "commitment_seq_4" in ids, f"got {ids}")


def test_load_open_commitments_handles_missing_file():
    print("test_load_open_commitments_handles_missing_file")
    out = load_open_commitments("/nonexistent/path/events.jsonl")
    _check("empty list when file missing", out == [])


def test_load_open_commitments_handles_bad_lines():
    print("test_load_open_commitments_handles_bad_lines")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        p.write_text(
            'not json at all\n'
            '{"seq": 1, "type": "commitment", "primary_thread_id": "p1", '
            '"person_ids": ["u"], "data": {"owner_id": "u", "title": "x", "status": "open"}}\n'
            '\n',
            encoding="utf-8")
        opens = load_open_commitments(p)
        _check("skipped malformed line, kept valid", len(opens) == 1)


# -------- match_send_to_commitments (Path 1) --------


def _open_commitment(*, owner: str, others: list[str], title: str,
                     primary: str = "project_001", id_: str = None) -> dict:
    data = {"owner_id": owner, "title": title, "status": "open"}
    if id_:
        data["id"] = id_
    return {
        "seq": 100,
        "type": "commitment",
        "primary_thread_id": primary,
        "person_ids": [owner] + others,
        "data": data,
    }


def test_send_match_high_confidence():
    print("test_send_match_high_confidence")
    opens = [
        _open_commitment(owner="user_m", others=["person_maya"],
                         title="Send pricing deck to Mira", id_="c1"),
        _open_commitment(owner="user_m", others=["person_daniel"],
                         title="Schedule Q3 review with Sam", id_="c2"),
    ]
    results = match_send_to_commitments(
        open_commitments=opens,
        sender_person_id="user_m",
        recipient_person_ids=["person_maya"],
        subject="Pricing deck — final",
        body="Mira, sending the pricing deck as promised. Let me know.",
    )
    _check("only Mira commitment scored (Sam filtered out by recipient)",
           len(results) == 1)
    top = results[0]
    _check("matched c1", top["commitment_id"] == "c1")
    _check("recommendation is auto_resolve",
           top["recommendation"] == "auto_resolve",
           f"score={top['score']}")


def test_send_match_no_recipient_no_results():
    print("test_send_match_no_recipient_no_results")
    opens = [_open_commitment(owner="user_m", others=["person_maya"],
                              title="Send deck", id_="c1")]
    results = match_send_to_commitments(
        open_commitments=opens,
        sender_person_id="user_m",
        recipient_person_ids=[],
        subject="anything", body="anything",
    )
    _check("no recipient = no results", results == [])


def test_send_match_filters_other_owner():
    print("test_send_match_filters_other_owner")
    opens = [
        _open_commitment(owner="person_maya", others=["user_m"],
                         title="Mira owes M the deck", id_="c1"),
    ]
    results = match_send_to_commitments(
        open_commitments=opens,
        sender_person_id="user_m",
        recipient_person_ids=["person_maya"],
        subject="Deck", body="Sending the deck",
    )
    _check("commitment owned by Mira not matched (sender is M)", results == [])


def test_send_match_pending_review_band():
    print("test_send_match_pending_review_band")
    opens = [_open_commitment(owner="user_m", others=["person_maya"],
                              title="Send the very specific Q4 strategy memo",
                              id_="c1")]
    # Subject loosely related — should land in pending_review band
    results = match_send_to_commitments(
        open_commitments=opens,
        sender_person_id="user_m",
        recipient_person_ids=["person_maya"],
        subject="Strategy quick note",
        body="thoughts on direction",
    )
    _check("got result", len(results) == 1)
    rec = results[0]["recommendation"]
    _check("recommendation is pending_review or no_action",
           rec in ("pending_review", "no_action"),
           f"got {rec} score={results[0]['score']}")


# -------- match_transcript_to_commitments (Path 3) --------


def test_transcript_auto_resolve_with_completion():
    print("test_transcript_auto_resolve_with_completion")
    opens = [_open_commitment(owner="user_m", others=["person_maya"],
                              title="Send pricing deck to Mira", id_="c1")]
    transcript = (
        "Mira: did you get the pricing deck? "
        "M: yes I sent the pricing deck on Monday. "
        "Mira: great, got it, thanks."
    )
    results = match_transcript_to_commitments(
        open_commitments=opens,
        attendee_person_ids=["user_m", "person_maya"],
        transcript_text=transcript,
    )
    _check("got result", len(results) == 1)
    _check("auto_resolve", results[0]["recommendation"] == "auto_resolve",
           f"got {results[0]}")


def test_transcript_schedule_shift_writes_updated():
    print("test_transcript_schedule_shift_writes_updated")
    opens = [_open_commitment(owner="user_m", others=["person_maya"],
                              title="Send pricing deck to Mira", id_="c1")]
    transcript = (
        "Mira: still waiting on the pricing deck. "
        "M: pushed to next Friday — running behind."
    )
    results = match_transcript_to_commitments(
        open_commitments=opens,
        attendee_person_ids=["user_m", "person_maya"],
        transcript_text=transcript,
    )
    _check("got result", len(results) == 1)
    _check("commitment_updated (not resolved)",
           results[0]["recommendation"] == "commitment_updated",
           f"got {results[0]}")


def test_transcript_new_ask_supersedes():
    print("test_transcript_new_ask_supersedes")
    opens = [_open_commitment(owner="user_m", others=["person_maya"],
                              title="Send pricing deck to Mira", id_="c1")]
    transcript = (
        "Mira: about the pricing deck — also can you also send the term sheet?"
    )
    results = match_transcript_to_commitments(
        open_commitments=opens,
        attendee_person_ids=["user_m", "person_maya"],
        transcript_text=transcript,
    )
    _check("got result", len(results) == 1)
    _check("supersede recommendation",
           results[0]["recommendation"] == "supersede",
           f"got {results[0]}")


def test_transcript_high_match_no_signal_pending_review():
    print("test_transcript_high_match_no_signal_pending_review")
    opens = [_open_commitment(owner="user_m", others=["person_maya"],
                              title="Send pricing deck to Mira", id_="c1")]
    # Discusses the deck with high title overlap but no completion / shift / new-ask signal
    transcript = "We talked about the pricing deck for Mira. It came up briefly."
    results = match_transcript_to_commitments(
        open_commitments=opens,
        attendee_person_ids=["user_m", "person_maya"],
        transcript_text=transcript,
    )
    _check("got result", len(results) == 1)
    _check("pending_review (no signal language)",
           results[0]["recommendation"] == "pending_review",
           f"got {results[0]}")


def test_transcript_filters_owner_not_attendee():
    print("test_transcript_filters_owner_not_attendee")
    opens = [_open_commitment(owner="person_dan", others=["user_m"],
                              title="Dan owes M the contract", id_="c1")]
    transcript = "Sent the contract to M yesterday — done."
    results = match_transcript_to_commitments(
        open_commitments=opens,
        attendee_person_ids=["user_m"],   # Dan NOT in attendees
        transcript_text=transcript,
    )
    _check("filtered out (owner not attending)", results == [])


# -------- dual-shape commitment events (Sam bug report 2026-05-17) --------
#
# Per shared/COMMITMENT_SCHEMA.md, consumers MUST handle both canonical
# (data.<field>) and flat (top-level <field>) shapes. The cr-commitments
# orchestrator and cru_match Path 1 / Path 3 were dropping flat-shape events
# silently — Sam was operating on ~1/3 of his actual commitment surface
# until he caught a missed live-deal commitment. Fixed in v3.4.2 via the
# `_commitment_field` helper below; these tests pin the contract.


def _flat_commitment(*, owner: str, others: list[str], title: str,
                     primary: str = "project_001", id_: str = None,
                     due: str = "") -> dict:
    """Build a flat-shape commitment event — fields at top level, `data` holds
    only ancillary info (evidence, due copy). Mirrors the shape Sam
    reported in his 2026-05-17 bug report.
    """
    ev = {
        "seq": 200,
        "type": "commitment",
        "primary_thread_id": primary,
        "person_ids": [owner] + others,
        "owner_id": owner,
        "title": title,
        "status": "open",
        "confidence": 0.85,
        "data": {"evidence": "from transcript"},
    }
    if id_:
        ev["id"] = id_
    if due:
        ev["due"] = due
        ev["data"]["due"] = due
    return ev


def test_commitment_field_helper_canonical():
    print("test_commitment_field_helper_canonical")
    from cru_match import _commitment_field
    ev = {"data": {"owner_id": "u1", "title": "Send deck"}}
    _check("reads data.owner_id", _commitment_field(ev, "owner_id") == "u1")
    _check("reads data.title", _commitment_field(ev, "title") == "Send deck")
    _check("missing field is None", _commitment_field(ev, "missing") is None)


def test_commitment_field_helper_flat():
    print("test_commitment_field_helper_flat")
    from cru_match import _commitment_field
    ev = {"owner_id": "u1", "title": "Send deck", "data": {"evidence": "x"}}
    _check("falls back to top-level owner_id",
           _commitment_field(ev, "owner_id") == "u1")
    _check("falls back to top-level title",
           _commitment_field(ev, "title") == "Send deck")


def test_commitment_field_helper_canonical_wins_when_both_present():
    print("test_commitment_field_helper_canonical_wins_when_both_present")
    from cru_match import _commitment_field
    ev = {"owner_id": "flat", "data": {"owner_id": "canonical"}}
    _check("data.<field> wins over top-level when both truthy",
           _commitment_field(ev, "owner_id") == "canonical")


def test_commitment_field_helper_empty_data_falls_back():
    print("test_commitment_field_helper_empty_data_falls_back")
    from cru_match import _commitment_field
    ev = {"owner_id": "flat", "data": {"owner_id": ""}}
    _check("empty data.<field> falls through to top-level",
           _commitment_field(ev, "owner_id") == "flat")


def test_commitment_field_helper_owner_person_id_variant():
    """cr-past-meetings owner_person_id-variant shape (M's workspace audit
    2026-05-17): data.owner_person_id stands in for data.owner_id."""
    print("test_commitment_field_helper_owner_person_id_variant")
    from cru_match import _commitment_field
    ev = {"data": {"owner_person_id": "person_X", "title": "Send deck"}}
    _check("owner_person_id maps to owner_id read",
           _commitment_field(ev, "owner_id") == "person_X")
    ev = {"data": {"requester_person_id": "person_Y"}}
    _check("requester_person_id maps to requester_id read",
           _commitment_field(ev, "requester_id") == "person_Y")


def test_commitment_field_helper_legacy_owner():
    """Pre-v2.7.15 legacy shape: top-level `owner` (no _id suffix)."""
    print("test_commitment_field_helper_legacy_owner")
    from cru_match import _commitment_field
    ev = {"owner": "person_legacy", "title": "Old commitment"}
    _check("legacy top-level owner maps to owner_id read",
           _commitment_field(ev, "owner_id") == "person_legacy")


def test_commitment_field_helper_due_date_alias():
    """owner_person_id-variant uses `due_date` not `due`."""
    print("test_commitment_field_helper_due_date_alias")
    from cru_match import _commitment_field
    ev = {"data": {"owner_person_id": "u", "due_date": "2026-05-04"}}
    _check("due_date maps to due read",
           _commitment_field(ev, "due") == "2026-05-04")


def test_commitment_field_helper_state_alias():
    """owner_person_id-variant uses `state` not `status`."""
    print("test_commitment_field_helper_state_alias")
    from cru_match import _commitment_field
    ev = {"data": {"owner_person_id": "u", "state": "open"}}
    _check("state maps to status read",
           _commitment_field(ev, "status") == "open")


def test_commitment_field_helper_classification_confidence_alias():
    """Some writers populate top-level classification_confidence; treat as
    confidence fallback for the filter threshold."""
    print("test_commitment_field_helper_classification_confidence_alias")
    from cru_match import _commitment_field
    ev = {"classification_confidence": 0.85, "data": {"owner_id": "u"}}
    _check("classification_confidence maps to confidence read",
           _commitment_field(ev, "confidence") == 0.85)


def test_commitment_field_priority_canonical_over_alias():
    """When both data.owner_id and data.owner_person_id are present (unlikely
    but defensive), prefer the canonical owner_id."""
    print("test_commitment_field_priority_canonical_over_alias")
    from cru_match import _commitment_field
    ev = {"data": {"owner_id": "canonical", "owner_person_id": "variant"}}
    _check("canonical wins over alias when both present",
           _commitment_field(ev, "owner_id") == "canonical")


def test_commitment_confidence_float_passthrough():
    print("test_commitment_confidence_float_passthrough")
    from cru_match import _commitment_confidence
    _check("float confidence preserved",
           _commitment_confidence({"data": {"confidence": 0.92}}) == 0.92)
    _check("int confidence coerced to float",
           _commitment_confidence({"data": {"confidence": 1}}) == 1.0)


def test_commitment_confidence_string_levels():
    """cr-past-meetings (canonical sample in M's events): data.confidence:
    'HIGH'. Legacy events: confidence: 'high'. Both must coerce to a number
    so the >= 0.7 filter doesn't crash."""
    print("test_commitment_confidence_string_levels")
    from cru_match import _commitment_confidence
    _check("HIGH coerces above threshold",
           _commitment_confidence({"data": {"confidence": "HIGH"}}) >= 0.7,
           f"got {_commitment_confidence({'data': {'confidence': 'HIGH'}})}")
    _check("high (lowercase) coerces above threshold",
           _commitment_confidence({"confidence": "high"}) >= 0.7)
    _check("medium coerces below threshold",
           _commitment_confidence({"data": {"confidence": "medium"}}) < 0.7)
    _check("low coerces below threshold",
           _commitment_confidence({"data": {"confidence": "low"}}) < 0.7)


def test_commitment_confidence_missing_defaults_zero():
    print("test_commitment_confidence_missing_defaults_zero")
    from cru_match import _commitment_confidence
    _check("missing confidence defaults to 0.0",
           _commitment_confidence({"data": {"owner_id": "u"}}) == 0.0)
    _check("unknown string label defaults to 0.0",
           _commitment_confidence({"data": {"confidence": "ehhh"}}) == 0.0)


def test_send_match_finds_owner_person_id_variant():
    """The owner_person_id-variant from cr-past-meetings — same fix scope as
    the v3.4.2 flat-shape fix but for the variant M's workspace audit found."""
    print("test_send_match_finds_owner_person_id_variant")
    ev = {
        "seq": 300, "type": "commitment", "primary_thread_id": "project_001",
        "person_ids": ["user_m", "person_maya"],
        "data": {
            "owner_person_id": "user_m",
            "requester_person_id": "person_maya",
            "title": "Send pricing deck to Mira",
            "due_date": "2026-05-04",
            "state": "open",
            "confidence": "HIGH",
        },
    }
    results = match_send_to_commitments(
        open_commitments=[ev],
        sender_person_id="user_m",
        recipient_person_ids=["person_maya"],
        subject="Pricing deck — final",
        body="Mira, sending the pricing deck as promised.",
    )
    _check("owner_person_id-variant matched", len(results) == 1,
           f"got {results}")


# -------- event_references_person (v3.5.0+ — Pulse Phase 3 helper) --------


def test_event_references_person_root_person_ids():
    print("test_event_references_person_root_person_ids")
    ev = {"type": "meeting", "person_ids": ["person_A", "person_B"]}
    _check("finds A", event_references_person(ev, "person_A"))
    _check("finds B", event_references_person(ev, "person_B"))
    _check("misses C", not event_references_person(ev, "person_C"))


def test_event_references_person_data_person_ids():
    print("test_event_references_person_data_person_ids")
    ev = {"type": "interaction", "data": {"person_ids": ["person_A"]}}
    _check("finds via data.person_ids", event_references_person(ev, "person_A"))


def test_event_references_person_actor():
    print("test_event_references_person_actor")
    ev = {"type": "interaction", "actor": "person_X"}
    _check("finds via actor", event_references_person(ev, "person_X"))


def test_event_references_person_data_attendees():
    print("test_event_references_person_data_attendees")
    ev = {"type": "meeting", "data": {"attendees": ["person_A", "person_B"]}}
    _check("finds via data.attendees", event_references_person(ev, "person_B"))


def test_event_references_person_data_owner_id_canonical():
    print("test_event_references_person_data_owner_id_canonical")
    ev = {"type": "commitment", "data": {"owner_id": "person_owner"}}
    _check("finds canonical owner_id",
           event_references_person(ev, "person_owner"))


def test_event_references_person_data_owner_person_id_variant():
    """Shape 4 (cr-past-meetings owner_person_id-variant) — pre-v3.5.0 Pulse
    inline list missed this; v3.5.0+ helper handles it."""
    print("test_event_references_person_data_owner_person_id_variant")
    ev = {"type": "commitment",
          "data": {"owner_person_id": "person_owner",
                   "requester_person_id": "person_req"}}
    _check("finds data.owner_person_id",
           event_references_person(ev, "person_owner"))
    _check("finds data.requester_person_id",
           event_references_person(ev, "person_req"))


def test_event_references_person_flat_new_owner_id():
    """Shape 2 (Sam's flat-new) — top-level owner_id without data nesting.
    Pre-v3.5.0 Pulse missed this too."""
    print("test_event_references_person_flat_new_owner_id")
    ev = {"type": "commitment", "owner_id": "person_owner",
          "requester_id": "person_req"}
    _check("finds top-level owner_id",
           event_references_person(ev, "person_owner"))
    _check("finds top-level requester_id",
           event_references_person(ev, "person_req"))


def test_event_references_person_legacy_owner():
    """Shape 3 (pre-v2.7.15 legacy) — top-level `owner` without _id suffix."""
    print("test_event_references_person_legacy_owner")
    ev = {"type": "commitment", "owner": "person_legacy"}
    _check("finds legacy top-level owner",
           event_references_person(ev, "person_legacy"))


def test_event_references_person_missing_returns_false():
    print("test_event_references_person_missing_returns_false")
    _check("empty event", not event_references_person({}, "person_X"))
    _check("None event", not event_references_person(None, "person_X"))
    _check("empty person_id", not event_references_person({"person_ids": ["a"]}, ""))
    _check("person not in any field",
           not event_references_person(
               {"type": "meeting", "person_ids": ["person_A"]},
               "person_Z"))


def test_event_references_person_graceful_on_malformed_data():
    """data could be missing, None, or non-dict — must not crash."""
    print("test_event_references_person_graceful_on_malformed_data")
    _check("data=None",
           not event_references_person({"type": "x", "data": None}, "person_A"))
    _check("data is a string somehow",
           not event_references_person({"type": "x", "data": "garbage"}, "person_A"))
    # Note: a malformed `person_ids` that's a string instead of a list will
    # be string-equality-matched against person_id (helper falls back to the
    # str branch). That's permissive but defensible — if the person's id is
    # literally there, count it. Don't test against that edge case here.


# -------- existing test --------


def test_transcript_match_finds_legacy_owner():
    """Legacy pre-v2.7.15 events with top-level `owner` (no _id) must also
    match in the transcript path."""
    print("test_transcript_match_finds_legacy_owner")
    ev = {
        "seq": 301, "type": "commitment", "primary_thread_id": "project_001",
        "person_ids": ["user_m", "person_maya"],
        "owner": "user_m",
        "title": "Send pricing deck to Mira",
        "status": "open",
        "confidence": "high",
    }
    transcript = (
        "Mira: did you get the pricing deck? "
        "M: yes I sent the pricing deck on Monday. "
        "Mira: got it, thanks."
    )
    results = match_transcript_to_commitments(
        open_commitments=[ev],
        attendee_person_ids=["user_m", "person_maya"],
        transcript_text=transcript,
    )
    _check("legacy-owner commitment matched", len(results) == 1,
           f"got {results}")


def test_load_open_commitments_includes_flat_shape():
    print("test_load_open_commitments_includes_flat_shape")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            _flat_commitment(owner="user_m", others=["person_maya"],
                             title="Flat-shape commitment", id_="cflat"),
            _open_commitment(owner="user_m", others=["person_maya"],
                             title="Canonical commitment", id_="ccanon"),
        ])
        opens = load_open_commitments(p)
        ids = {(o.get("data") or {}).get("id") or o.get("id")
               or f"commitment_seq_{o.get('seq')}" for o in opens}
        _check("flat-shape included", "cflat" in ids, f"got {ids}")
        _check("canonical included", "ccanon" in ids, f"got {ids}")


def test_send_match_finds_flat_shape_commitment():
    print("test_send_match_finds_flat_shape_commitment")
    # The exact bug Sam reported: a flat-shape commitment owned by the
    # user that an outbound send should match. Pre-fix, this returned [].
    opens = [_flat_commitment(owner="user_m", others=["person_maya"],
                              title="Send pricing deck to Mira", id_="cflat")]
    results = match_send_to_commitments(
        open_commitments=opens,
        sender_person_id="user_m",
        recipient_person_ids=["person_maya"],
        subject="Pricing deck — final",
        body="Mira, sending the pricing deck as promised.",
    )
    _check("flat-shape commitment is matched", len(results) == 1,
           f"got {results}")
    _check("matched cflat", results[0]["commitment_id"] == "cflat")


def test_transcript_match_finds_flat_shape_commitment():
    print("test_transcript_match_finds_flat_shape_commitment")
    opens = [_flat_commitment(owner="user_m", others=["person_maya"],
                              title="Send pricing deck to Mira", id_="cflat")]
    transcript = (
        "Mira: did you get the pricing deck? "
        "M: yes I sent the pricing deck on Monday. "
        "Mira: great, got it, thanks."
    )
    results = match_transcript_to_commitments(
        open_commitments=opens,
        attendee_person_ids=["user_m", "person_maya"],
        transcript_text=transcript,
    )
    _check("flat-shape commitment is matched", len(results) == 1,
           f"got {results}")
    _check("auto_resolve recommended",
           results[0]["recommendation"] == "auto_resolve",
           f"got {results[0]}")


# -------- match_inbound_to_commitments (Path 4 — v3.14.5+) --------
#
# Inbound mirror of Path 1: the SENDER is the owner (the counter-party who
# owed the user), and their email is evidence they delivered. Completion-GATED
# like Path 3 — a high title match alone never auto-resolves.


def test_inbound_auto_resolve_with_completion():
    print("test_inbound_auto_resolve_with_completion")
    # Maya owed M the pricing deck; she emails it in with completion language.
    opens = [_open_commitment(owner="person_maya", others=["user_m"],
                              title="Send pricing deck to M", id_="c1")]
    results = match_inbound_to_commitments(
        open_commitments=opens,
        sender_person_id="person_maya",
        subject="Pricing deck — as promised",
        body="Hi M, here's the pricing deck I promised. Attached the final.",
    )
    _check("got result", len(results) == 1, f"got {results}")
    _check("auto_resolve", results[0]["recommendation"] == "auto_resolve",
           f"got {results[0]}")
    _check("resolved_by is the sender (counter-party)",
           results[0]["owner_id"] == "person_maya", f"got {results[0]}")


def test_inbound_high_match_no_completion_pending_review():
    print("test_inbound_high_match_no_completion_pending_review")
    # Maya emails ABOUT the deck but hasn't delivered — no completion language.
    # High title match alone must NOT auto-resolve (key difference vs Path 1).
    opens = [_open_commitment(owner="person_maya", others=["user_m"],
                              title="Send pricing deck to M", id_="c1")]
    results = match_inbound_to_commitments(
        open_commitments=opens,
        sender_person_id="person_maya",
        subject="Pricing deck",
        body="Still pulling the pricing deck together — quick question on scope.",
    )
    _check("got result", len(results) == 1, f"got {results}")
    _check("NOT auto_resolve without completion language",
           results[0]["recommendation"] != "auto_resolve",
           f"got {results[0]}")


def test_inbound_schedule_shift_writes_updated():
    print("test_inbound_schedule_shift_writes_updated")
    opens = [_open_commitment(owner="person_maya", others=["user_m"],
                              title="Send pricing deck to M", id_="c1")]
    results = match_inbound_to_commitments(
        open_commitments=opens,
        sender_person_id="person_maya",
        subject="Pricing deck — running behind",
        body="The pricing deck is pushed to next Friday, sorry for the delay.",
    )
    _check("got result", len(results) == 1, f"got {results}")
    _check("commitment_updated (not resolved)",
           results[0]["recommendation"] == "commitment_updated",
           f"got {results[0]}")


def test_inbound_filters_by_sender_owner():
    print("test_inbound_filters_by_sender_owner")
    # The commitment is owned by the USER (M owes Maya). An inbound email FROM
    # Maya must NOT match it — Path 4 only resolves what the SENDER owed.
    opens = [_open_commitment(owner="user_m", others=["person_maya"],
                              title="Send pricing deck to Mira", id_="c1")]
    results = match_inbound_to_commitments(
        open_commitments=opens,
        sender_person_id="person_maya",
        subject="Pricing deck",
        body="Sent the pricing deck — here you go.",
    )
    _check("user-owed commitment not matched on inbound from counter-party",
           results == [], f"got {results}")


def test_inbound_new_ask_not_acted_on():
    print("test_inbound_new_ask_not_acted_on")
    # Counter-party asks the user for something new. That's inbox-triage's job
    # (spawn a user-owed commitment), NOT a resolution of the sender's own
    # commitment. Must never auto_resolve / commitment_updated off new-ask.
    opens = [_open_commitment(owner="person_maya", others=["user_m"],
                              title="Send pricing deck to M", id_="c1")]
    results = match_inbound_to_commitments(
        open_commitments=opens,
        sender_person_id="person_maya",
        subject="Pricing deck",
        body="About the pricing deck — can you also send me the term sheet?",
    )
    _check("got result", len(results) == 1, f"got {results}")
    _check("new-ask never auto_resolves",
           results[0]["recommendation"] != "auto_resolve", f"got {results[0]}")
    _check("new-ask never commitment_updated",
           results[0]["recommendation"] != "commitment_updated",
           f"got {results[0]}")
    _check("new-ask flag surfaced for diagnostics",
           results[0]["has_new_ask_signal"] is True, f"got {results[0]}")


def test_inbound_empty_sender_no_results():
    print("test_inbound_empty_sender_no_results")
    opens = [_open_commitment(owner="person_maya", others=["user_m"],
                              title="Send deck", id_="c1")]
    _check("empty sender = no results",
           match_inbound_to_commitments(
               open_commitments=opens, sender_person_id="",
               subject="x", body="here's the deck") == [])
    _check("empty subject+body = no results",
           match_inbound_to_commitments(
               open_commitments=opens, sender_person_id="person_maya",
               subject="", body="") == [])


def test_inbound_finds_flat_shape_commitment():
    print("test_inbound_finds_flat_shape_commitment")
    # Same dual-shape contract as Path 1/3 — flat-shape owned by counter-party.
    opens = [_flat_commitment(owner="person_maya", others=["user_m"],
                              title="Send pricing deck to M", id_="cflat")]
    results = match_inbound_to_commitments(
        open_commitments=opens,
        sender_person_id="person_maya",
        subject="Pricing deck — as promised",
        body="Here's the pricing deck, attached the final as promised.",
    )
    _check("flat-shape commitment matched on inbound", len(results) == 1,
           f"got {results}")
    _check("matched cflat", results[0]["commitment_id"] == "cflat")
    _check("auto_resolve", results[0]["recommendation"] == "auto_resolve",
           f"got {results[0]}")


# -------- Path 5: match_calendar_to_commitments --------


def test_scheduling_intent_detector():
    print("test_scheduling_intent_detector")
    _check("'set up the call with' is scheduling",
           detect_scheduling_intent("Set up the build call with Bo"))
    _check("'lock Monday' is scheduling",
           detect_scheduling_intent("Lock Monday 8AM with Rio"))
    _check("'propose times' is scheduling",
           detect_scheduling_intent("Propose times to the EOS integrator"))
    _check("'find time with' is scheduling",
           detect_scheduling_intent("Find time with Quinn next week"))
    _check("'send the pricing deck' is NOT scheduling",
           not detect_scheduling_intent("Send the pricing deck to Mira"))
    _check("'review the contract' is NOT scheduling",
           not detect_scheduling_intent("Review the MSA redlines"))
    _check("empty is False", not detect_scheduling_intent(""))


def test_calendar_auto_resolve_scheduling_commitment():
    """The scheduling-close regression: M owes 'set up the call with Bo', he creates
    the calendar invite, and the commitment must auto-resolve — even though no
    outbound EMAIL was sent (Paths 1/2/4 would all miss it)."""
    print("test_calendar_auto_resolve_scheduling_commitment")
    opens = [
        _open_commitment(owner="user_m", others=["person_bo"],
                         title="Set up the build call with Bo",
                         id_="c_bo"),
        # A deliverable commitment with the SAME person — must NOT resolve.
        _open_commitment(owner="user_m", others=["person_bo"],
                         title="Send Bo the data-integrity one-pager",
                         id_="c_deck"),
    ]
    results = match_calendar_to_commitments(
        open_commitments=opens,
        user_person_id="user_m",
        calendar_events=[{
            "attendee_person_ids": ["user_m", "person_bo"],
            "summary": "Bo Sample / Sam Sample (Acme Co)",
            "created_ts": "2026-05-29T15:29:44Z",
            "accepted_by": ["person_bo"],
            "calendar_event_id": "evt_abc",
        }],
    )
    by_id = {r["commitment_id"]: r for r in results}
    _check("scheduling commitment auto-resolves",
           by_id.get("c_bo", {}).get("recommendation") == "auto_resolve",
           f"got {by_id.get('c_bo')}")
    _check("acceptance recorded", by_id["c_bo"]["counterparty_accepted"])
    _check("deliverable commitment NOT auto-resolved",
           by_id.get("c_deck", {}).get("recommendation") != "auto_resolve",
           f"got {by_id.get('c_deck')}")


def test_calendar_filters_attendee_mismatch():
    print("test_calendar_filters_attendee_mismatch")
    opens = [_open_commitment(owner="user_m", others=["person_bo"],
                              title="Set up a call with Bo", id_="c1")]
    results = match_calendar_to_commitments(
        open_commitments=opens,
        user_person_id="user_m",
        calendar_events=[{
            "attendee_person_ids": ["user_m", "person_someone_else"],
            "summary": "Unrelated sync",
            "calendar_event_id": "evt_x",
        }],
    )
    _check("no match when counter-party not on the event", results == [],
           f"got {results}")


def test_calendar_skips_event_predating_commitment():
    print("test_calendar_skips_event_predating_commitment")
    commit = _open_commitment(owner="user_m", others=["person_bo"],
                              title="Set up a call with Bo", id_="c1")
    commit["ts"] = "2026-05-29T00:00:00Z"
    results = match_calendar_to_commitments(
        open_commitments=[commit],
        user_person_id="user_m",
        calendar_events=[{
            "attendee_person_ids": ["person_bo"],
            "summary": "Old standing meeting",
            "created_ts": "2026-04-01T00:00:00Z",  # predates the commitment
            "calendar_event_id": "evt_old",
        }],
    )
    _check("event predating the commitment is skipped", results == [],
           f"got {results}")


def test_calendar_pending_review_topic_match_no_intent():
    """No scheduling intent in the title, but the event summary strongly matches
    the title and the counter-party is on the event → pending_review, never a
    silent auto-resolve."""
    print("test_calendar_pending_review_topic_match_no_intent")
    opens = [_open_commitment(
        owner="user_m", others=["person_bo"],
        title="data integrity confidentiality build review", id_="c1")]
    results = match_calendar_to_commitments(
        open_commitments=opens,
        user_person_id="user_m",
        calendar_events=[{
            "attendee_person_ids": ["person_bo"],
            "summary": "data integrity confidentiality build review",
            "calendar_event_id": "evt_y",
        }],
    )
    _check("one result", len(results) == 1, f"got {results}")
    _check("recommendation is pending_review",
           results[0]["recommendation"] == "pending_review",
           f"got {results[0]}")


def test_calendar_filters_other_owner():
    print("test_calendar_filters_other_owner")
    # Commitment owned by the counter-party (they owe the user) — Path 5 only
    # resolves commitments the USER owes, so this must not match.
    opens = [_open_commitment(owner="person_bo", others=["user_m"],
                              title="Set up a call with Sam", id_="c1")]
    results = match_calendar_to_commitments(
        open_commitments=opens,
        user_person_id="user_m",
        calendar_events=[{
            "attendee_person_ids": ["user_m", "person_bo"],
            "summary": "Bo / Sam",
            "calendar_event_id": "evt_z",
        }],
    )
    _check("commitment owned by counter-party not matched", results == [],
           f"got {results}")


def test_calendar_empty_events_no_results():
    print("test_calendar_empty_events_no_results")
    opens = [_open_commitment(owner="user_m", others=["person_bo"],
                              title="Set up a call with Bo", id_="c1")]
    _check("no calendar events → no results",
           match_calendar_to_commitments(
               open_commitments=opens, user_person_id="user_m",
               calendar_events=[]) == [])
    _check("no user id → no results",
           match_calendar_to_commitments(
               open_commitments=opens, user_person_id="",
               calendar_events=[{"attendee_person_ids": ["person_bo"]}]) == [])


# -------- event builders --------


def test_build_commitment_resolved_event():
    print("test_build_commitment_resolved_event")
    ev = build_commitment_resolved_event(
        commitment_id="c1",
        resolved_by="user_m",
        primary_thread_id="project_001",
        source_skill="apply-choices",
        evidence="Sent via Cowork at 10:42 AM",
        next_seq=199,
    )
    _check("seq", ev["seq"] == 199)
    _check("type", ev["type"] == "commitment_resolved")
    _check("data.commitment_id", ev["data"]["commitment_id"] == "c1")
    _check("data.resolved_by", ev["data"]["resolved_by"] == "user_m")
    _check("ts is ISO Z", ev["ts"].endswith("Z"))


def test_build_commitment_updated_event_truncates_evidence():
    print("test_build_commitment_updated_event_truncates_evidence")
    long_evidence = "x" * 500
    ev = build_commitment_updated_event(
        commitment_id="c1",
        primary_thread_id="project_001",
        source_skill="cr-past-meetings",
        change_summary="Pushed to next Friday",
        evidence=long_evidence,
        next_seq=200,
    )
    _check("evidence truncated to 200", len(ev["data"]["evidence"]) == 200)
    _check("change_summary preserved", ev["data"]["change_summary"] == "Pushed to next Friday")


def test_build_pending_review_event():
    print("test_build_pending_review_event")
    ev = build_pending_review_event(
        commitment_id="c1",
        primary_thread_id="project_001",
        source_skill="apply-choices",
        proposed_resolution="auto_resolve",
        score=0.42,
        evidence="Subject: pricing deck",
        next_seq=201,
    )
    _check("type", ev["type"] == "commitment_review_proposed")
    _check("score rounded to 3 places", ev["data"]["match_score"] == 0.42)


def test_build_commitment_review_dismissed_event():
    print("test_build_commitment_review_dismissed_event")
    ev = build_commitment_review_dismissed_event(
        commitment_id="c1",
        primary_thread_id="project_001",
        source_skill="cr-dont-forget",
        next_seq=202,
    )
    _check("type", ev["type"] == "commitment_review_dismissed")
    _check("commitment_id", ev["data"]["commitment_id"] == "c1")
    _check("ts is ISO Z", ev["ts"].endswith("Z"))


# -------- load_open_review_proposals (v2.14.7+) --------


def test_load_review_proposals_filters_resolved_and_dismissed():
    print("test_load_review_proposals_filters_resolved_and_dismissed")
    import datetime
    today_iso = datetime.datetime.utcnow().isoformat() + "Z"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            # Three review proposals, all recent
            {"seq": 10, "ts": today_iso,
             "type": "commitment_review_proposed",
             "primary_thread_id": "project_001",
             "data": {"commitment_id": "c1", "match_score": 0.42}},
            {"seq": 11, "ts": today_iso,
             "type": "commitment_review_proposed",
             "primary_thread_id": "project_001",
             "data": {"commitment_id": "c2", "match_score": 0.48}},
            {"seq": 12, "ts": today_iso,
             "type": "commitment_review_proposed",
             "primary_thread_id": "project_001",
             "data": {"commitment_id": "c3", "match_score": 0.39}},
            # c1 underlying commitment got resolved by another path → review moot
            {"seq": 13, "ts": today_iso,
             "type": "commitment_resolved",
             "data": {"commitment_id": "c1"}},
            # c2 review explicitly dismissed by user
            {"seq": 14, "ts": today_iso,
             "type": "commitment_review_dismissed",
             "data": {"commitment_id": "c2"}},
        ])
        opens = load_open_review_proposals(p)
        cids = {(e.get("data") or {}).get("commitment_id") for e in opens}
        _check("c1 filtered (commitment resolved)", "c1" not in cids,
               f"got {cids}")
        _check("c2 filtered (review dismissed)", "c2" not in cids,
               f"got {cids}")
        _check("c3 still open", "c3" in cids, f"got {cids}")


def test_load_review_proposals_window_filter():
    print("test_load_review_proposals_window_filter")
    import datetime
    now = datetime.datetime.utcnow()
    recent_iso = now.isoformat() + "Z"
    old_iso = (now - datetime.timedelta(days=30)).isoformat() + "Z"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "ts": old_iso,
             "type": "commitment_review_proposed",
             "primary_thread_id": "project_001",
             "data": {"commitment_id": "old1", "match_score": 0.40}},
            {"seq": 2, "ts": recent_iso,
             "type": "commitment_review_proposed",
             "primary_thread_id": "project_001",
             "data": {"commitment_id": "new1", "match_score": 0.40}},
        ])
        out = load_open_review_proposals(p, window_days=7)
        cids = {(e.get("data") or {}).get("commitment_id") for e in out}
        _check("recent kept", "new1" in cids)
        _check("old (30d) excluded", "old1" not in cids)


def test_load_review_proposals_handles_missing_file():
    print("test_load_review_proposals_handles_missing_file")
    out = load_open_review_proposals("/nonexistent/path/events.jsonl")
    _check("empty list when file missing", out == [])


# -------- extract_snippet (v2.14.8+) --------


def test_extract_snippet_finds_topic():
    print("test_extract_snippet_finds_topic")
    text = (
        "We talked about pricing strategy for a while. "
        "Then Bo brought up MegaSupply and how their procurement team "
        "had a similar problem with the data shape. "
        "John said the MegaSupply contract almost closed but went to the competitor. "
        "We tabled the rest until next quarter."
    )
    snippet = extract_snippet("MegaSupply procurement", text)
    _check("snippet contains topic word",
           "MegaSupply" in snippet,
           f"got {snippet!r}")
    _check("snippet is reasonable length", 50 <= len(snippet) <= 300,
           f"got len {len(snippet)}")


def test_extract_snippet_empty_when_no_match():
    print("test_extract_snippet_empty_when_no_match")
    text = "We talked about pricing strategy for a while."
    snippet = extract_snippet("MegaSupply procurement", text)
    _check("empty when topic not in text", snippet == "")


def test_extract_snippet_handles_empty_inputs():
    print("test_extract_snippet_handles_empty_inputs")
    _check("empty query", extract_snippet("", "some text") == "")
    _check("empty text", extract_snippet("topic", "") == "")
    _check("None query", extract_snippet(None, "text") == "")
    _check("None text", extract_snippet("topic", None) == "")


def test_extract_snippet_picks_densest_match():
    print("test_extract_snippet_picks_densest_match")
    # First mention is sparse, second is denser. Should pick the second.
    text = (
        "MegaSupply was mentioned briefly at the start. "
        "Then we moved on to other topics for a long stretch. "
        "Later: MegaSupply procurement is the model — that's why John keeps "
        "bringing them up. The MegaSupply situation is still our reference case."
    )
    snippet = extract_snippet("MegaSupply procurement", text)
    _check("dense region picked",
           "procurement" in snippet,
           f"got {snippet!r}")


def main():
    tests = [
        test_identical_titles_score_one,
        test_disjoint_score_zero,
        test_partial_overlap_scores_above_threshold,
        test_strong_match_above_high_threshold,
        test_empty_inputs_safe,
        test_stopword_filtering,
        test_completion_signal_positive,
        test_completion_signal_negative,
        test_schedule_shift_signal_positive,
        test_schedule_shift_signal_negative,
        test_new_ask_signal_positive,
        test_new_ask_signal_negative,
        test_load_open_commitments_filters_resolved,
        test_load_open_commitments_handles_missing_file,
        test_load_open_commitments_handles_bad_lines,
        test_send_match_high_confidence,
        test_send_match_no_recipient_no_results,
        test_send_match_filters_other_owner,
        test_send_match_pending_review_band,
        test_transcript_auto_resolve_with_completion,
        test_transcript_schedule_shift_writes_updated,
        test_transcript_new_ask_supersedes,
        test_transcript_high_match_no_signal_pending_review,
        test_transcript_filters_owner_not_attendee,
        test_commitment_field_helper_canonical,
        test_commitment_field_helper_flat,
        test_commitment_field_helper_canonical_wins_when_both_present,
        test_commitment_field_helper_empty_data_falls_back,
        test_commitment_field_helper_owner_person_id_variant,
        test_commitment_field_helper_legacy_owner,
        test_commitment_field_helper_due_date_alias,
        test_commitment_field_helper_state_alias,
        test_commitment_field_helper_classification_confidence_alias,
        test_commitment_field_priority_canonical_over_alias,
        test_commitment_confidence_float_passthrough,
        test_commitment_confidence_string_levels,
        test_commitment_confidence_missing_defaults_zero,
        test_load_open_commitments_includes_flat_shape,
        test_send_match_finds_flat_shape_commitment,
        test_send_match_finds_owner_person_id_variant,
        test_event_references_person_root_person_ids,
        test_event_references_person_data_person_ids,
        test_event_references_person_actor,
        test_event_references_person_data_attendees,
        test_event_references_person_data_owner_id_canonical,
        test_event_references_person_data_owner_person_id_variant,
        test_event_references_person_flat_new_owner_id,
        test_event_references_person_legacy_owner,
        test_event_references_person_missing_returns_false,
        test_event_references_person_graceful_on_malformed_data,
        test_transcript_match_finds_flat_shape_commitment,
        test_transcript_match_finds_legacy_owner,
        test_inbound_auto_resolve_with_completion,
        test_inbound_high_match_no_completion_pending_review,
        test_inbound_schedule_shift_writes_updated,
        test_inbound_filters_by_sender_owner,
        test_inbound_new_ask_not_acted_on,
        test_inbound_empty_sender_no_results,
        test_inbound_finds_flat_shape_commitment,
        test_scheduling_intent_detector,
        test_calendar_auto_resolve_scheduling_commitment,
        test_calendar_filters_attendee_mismatch,
        test_calendar_skips_event_predating_commitment,
        test_calendar_pending_review_topic_match_no_intent,
        test_calendar_filters_other_owner,
        test_calendar_empty_events_no_results,
        test_build_commitment_resolved_event,
        test_build_commitment_updated_event_truncates_evidence,
        test_build_pending_review_event,
        test_build_commitment_review_dismissed_event,
        test_load_review_proposals_filters_resolved_and_dismissed,
        test_load_review_proposals_window_filter,
        test_load_review_proposals_handles_missing_file,
        test_extract_snippet_finds_topic,
        test_extract_snippet_empty_when_no_match,
        test_extract_snippet_handles_empty_inputs,
        test_extract_snippet_picks_densest_match,
    ]
    for t in tests:
        t()
    print(f"\nOK {len(tests)} cru_match tests passed")


if __name__ == "__main__":
    main()
