#!/usr/bin/env python3
"""Phase 6 Loop 3 — prep-brief grading + Pass 15 section-weight proposals."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import prep_grading as pg  # noqa: E402
from event_gate import gate_events  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws(events):
    ws = Path(tempfile.mkdtemp(prefix="pg_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    lines = [json.dumps({**e, "seq": i}) for i, e in enumerate(events, 1)]
    (ws / "_hq" / "data" / "events.jsonl").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")
    return ws


def test_grade_brief():
    predicted = {
        "Talking Points": ["Push on the NetSuite cutover date",
                           "Discuss the pricing tiers"],
        "Risks / Watch-outs": ["Legal review might slip the timeline"],
    }
    topics = ["we agreed the netsuite cutover date is april 30",
              "brand new topic about hiring nobody predicted"]
    g = pg.grade_brief(predicted, topics)
    check("hit the netsuite talking point", g["sections_hit"]["Talking Points"] == 1)
    check("risks section empty (no legal topic)", "Risks / Watch-outs" in g["sections_empty"])
    check("unpredicted hiring topic surfaced",
          any("hiring" in t for t in g["unpredicted_topics"]))


def test_prep_feedback_event_gate():
    g = pg.grade_brief({"Talking Points": ["x topic here"]}, ["x topic here now"])
    ev = pg.build_prep_feedback_event(meeting_id="granola:abc", meeting_type="internal_1_1",
                                      grade=g, person_ids=["person_003"])
    check("event type prep_feedback", ev["type"] == "prep_feedback")
    check("no seq/ts", "seq" not in ev and "ts" not in ev)
    out = gate_events([ev], strict_enum=True)
    check("prep_feedback passes the gate", out and out[0]["type"] == "prep_feedback")


def test_aggregate_and_propose():
    # 8 internal_1_1 meetings, Risks empty in 7 of them → propose dropping it.
    evs = []
    for i in range(8):
        empty = ["Risks / Watch-outs"] if i < 7 else []
        evs.append({"type": "prep_feedback", "data": {
            "meeting_id": f"m{i}", "meeting_type": "internal_1_1",
            "sections_rendered": {"Talking Points": 4, "Risks / Watch-outs": 1},
            "sections_missed": empty}})
    ws = _ws(evs)
    rows = pg.load_prep_feedback(ws)
    stats = pg.aggregate_section_stats(rows)
    props = pg.propose_section_weights(stats)
    check("proposes dropping Risks for internal_1_1",
          any(p["section"] == "Risks / Watch-outs" and p["meeting_type"] == "internal_1_1"
              and p["weight"] == 0 for p in props))
    check("does not propose dropping Talking Points",
          all(p["section"] != "Talking Points" for p in props))


def test_floor_and_cooldown():
    evs = [{"type": "prep_feedback", "data": {
        "meeting_id": f"m{i}", "meeting_type": "board",
        "sections_rendered": {"Risks / Watch-outs": 1},
        "sections_missed": ["Risks / Watch-outs"]}} for i in range(5)]
    ws = _ws(evs)
    stats = pg.aggregate_section_stats(pg.load_prep_feedback(ws))
    check("below ≥6-meeting floor → nothing", pg.propose_section_weights(stats) == [])
    # cooldown
    evs2 = [{"type": "prep_feedback", "data": {
        "meeting_id": f"m{i}", "meeting_type": "board",
        "sections_rendered": {"Risks / Watch-outs": 1},
        "sections_missed": ["Risks / Watch-outs"]}} for i in range(7)]
    ws2 = _ws(evs2)
    stats2 = pg.aggregate_section_stats(pg.load_prep_feedback(ws2))
    fp = pg.propose_section_weights(stats2)[0]["fingerprint"]
    check("cooldown suppresses",
          pg.propose_section_weights(stats2, cooldown_fingerprints={fp}) == [])
    check("existing weight 0 suppresses re-proposal",
          pg.propose_section_weights(stats2, existing_weights={"board": {"Risks / Watch-outs": 0}}) == [])


def test_config_section_weight():
    cfg = {}
    check("default weight 1.0", pg.section_weight(cfg, "board", "Risks / Watch-outs") == 1.0)
    cfg = pg.set_section_weight(cfg, "internal_1_1", "Risks / Watch-outs", 0)
    check("set weight persists in dict",
          pg.section_weight(cfg, "internal_1_1", "Risks / Watch-outs") == 0)
    check("other meeting-type unaffected",
          pg.section_weight(cfg, "board", "Risks / Watch-outs") == 1.0)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_grade_brief()
    test_prep_feedback_event_gate()
    test_aggregate_and_propose()
    test_floor_and_cooldown()
    test_config_section_weight()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL prep_grading tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
