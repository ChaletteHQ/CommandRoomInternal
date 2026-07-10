#!/usr/bin/env python3
"""SPEC A8 — follow-up-ritual output regression exercise (runtime tier).

Simulates the post-meeting ritual against a fixed synthetic transcript (3 attendees,
3 action items, 1 decision): render the follow-up pack via make_brief(brief_kind=
"followup_pack"), surface each attendee's still-open commitments from the fixture, and
append the followup_pack_drafted event with its documented shape. Asserts structure +
event side-effect + golden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import output_exercise_lib as lib  # noqa: E402

NOW = "2026-06-01T12:00:00+00:00"
DATE = "2026-06-01"

# Fixed synthetic transcript structure (the LLM-extracted layer is a stand-in).
ATTENDEES = ["person_001", "person_002", "person_005"]  # Sam, Rio, Lee
ACTION_ITEMS = ["Sam to send the pricing sheet to Northstar",
                "Rio to finish the sourcing spec",
                "Lee to wire the bid-leveling endpoint"]
DECISIONS = ["Lock the pricing tiers before the Northstar renewal"]
MEETING_EVENT_SEQ = 18  # the "Pricing review" meeting in the fixture


def main() -> int:
    from brief_writer import make_brief
    from cru_match import load_open_commitments, event_references_person
    from next_seq import next_seq
    from atomic_write import atomic_append_jsonl

    ok, fail, section, finish = lib.make_recorder()
    ws = lib.copy_fixture()
    events_path = ws / "_hq" / "data" / "events.jsonl"

    section("derivation — still-open-from-before per attendee")
    opens = load_open_commitments(str(events_path))
    still_open = {}
    for pid in ATTENDEES:
        still_open[pid] = [c.get("data", {}).get("title")
                           for c in opens if event_references_person(c, pid)]
    # Sam (person_001) owns 'Send Northstar the pricing sheet' + 'Ship the pricing page copy'.
    if any("pricing" in (t or "").lower() for t in still_open["person_001"]):
        ok("Sam's still-open commitments surfaced", str(still_open["person_001"]))
    else:
        fail("Sam's still-open commitments", str(still_open["person_001"]))

    section("render (real make_brief)")
    out = ws / "_hq" / "meetings" / f"Followup_Pack_{DATE}.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    sections = [
        {"heading": "What you committed to in this room",
         "bullets": ["Send the pricing sheet to Northstar.",
                     "Ship the pricing page copy this week."]},
        {"heading": "Action items", "bullets": ACTION_ITEMS},
        {"heading": "Decided", "bullets": DECISIONS},
        {"heading": "Still open from before",
         "bullets": [f"{name}: {', '.join(t for t in titles if t)}"
                     for name, titles in [("Sam", still_open["person_001"]),
                                          ("Rio", still_open["person_002"])]
                     if any(titles)] or ["Nothing carried over."]},
    ]
    try:
        make_brief(str(out), brief_kind="followup_pack",
                   title="Follow-up pack — Pricing review",
                   subtitle=f"{DATE} · 3 attendees",
                   exec_header={"verdict": "Three follow-ups drafted; pricing decision logged."},  # OUT2 §4 flip
                   sections=sections, contract="report")
        ok("make_brief rendered the follow-up pack")
    except Exception as e:
        fail("make_brief rendered the follow-up pack", f"{type(e).__name__}: {e}")
        return finish("follow_up_ritual_exercise")
    ok("pack .docx exists > 5KB") if out.exists() and out.stat().st_size > 5000 else fail("pack .docx > 5KB")

    section("extracted structure + golden")
    text = lib.extract_docx_text(out)
    for h in ("FOLLOW-UP PACK", "Action items", "Decided", "Still open from before"):
        ok(f"contains: {h}") if h in text else fail(f"contains: {h}")
    ph = lib.assert_no_placeholders(text)
    ok("no placeholders") if not ph else fail("no placeholders", str(ph))
    matched, diff = lib.compare_golden("follow_up_ritual", text)
    ok("extracted text matches golden") if matched else fail("golden match", diff[:600])

    section("substrate side-effect (followup_pack_drafted)")
    seq = next_seq(str(events_path))
    ev = {"seq": seq, "ts": NOW, "type": "followup_pack_drafted",
          "source_skill": "follow-up-ritual", "primary_thread_id": "project_001",
          "data": {"meeting_event_seq": MEETING_EVENT_SEQ, "attendee_count": len(ATTENDEES),
                   "draft_email_count": len(ATTENDEES), "decisions_logged": len(DECISIONS),
                   "commitments_extracted": len(ACTION_ITEMS),
                   "pack_artifact_path": f"_hq/meetings/Followup_Pack_{DATE}.docx"}}
    atomic_append_jsonl(events_path, [ev])
    appended = json.loads(events_path.read_text(encoding="utf-8").splitlines()[-1])
    v = lib.validate_event(appended)
    ok("followup_pack_drafted validates") if not v else fail("event valid", str(v))
    d = appended["data"]
    if d["meeting_event_seq"] == 18 and d["attendee_count"] == 3 and d["commitments_extracted"] == 3:
        ok("event carries meeting_event_seq=18, 3 attendees, 3 commitments")
    else:
        fail("event payload", json.dumps(d))

    return finish("follow_up_ritual_exercise")


if __name__ == "__main__":
    raise SystemExit(main())
