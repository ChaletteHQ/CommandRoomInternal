#!/usr/bin/env python3
"""SPEC REL1 Part 2 — relationship_moves ranking + dedupe/emit tests."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import relationship_moves as rm  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _ws(events):
    ws = Path(tempfile.mkdtemp(prefix="rm_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else ""), encoding="utf-8")
    return ws


def _sig(pid, score, days_ago=1):
    return {"seq": 1, "ts": _iso(days_ago), "type": "dormancy_signal",
            "data": {"entity_id": pid, "entity_type": "person", "score": score}}


def test_scoring_exact():
    sigs = [{"data": {"entity_id": "p1", "entity_type": "person", "score": 3.0}}]
    r = rm.score_candidates(sigs, {"p1": 6}, {"p1": 5})
    check("compound score == 0.655", r[0]["score"] == 0.655)

    r2 = rm.score_candidates([], {}, {"p1": 25})
    check("overdue cap: 25-day -> 0.2 total", r2[0]["score"] == 0.2 and r2[0]["components"]["commitment_overdue"] == 1.0)

    r3 = rm.score_candidates([{"data": {"entity_id": "p1", "entity_type": "person", "score": 2.0}}], {}, {})
    check("missing components contribute 0", r3[0]["components"]["thread_context"] == 0 and r3[0]["components"]["commitment_overdue"] == 0)


def test_thread_person_collapse():
    sigs = [{"data": {"entity_id": "p1", "entity_type": "person", "score": 2.0}}]
    r = rm.score_candidates(sigs, {"p1": 8}, {})
    check("person w/ dormancy + thread -> ONE candidate", len(r) == 1 and r[0]["person_id"] == "p1")
    check("collapsed candidate carries thread component", r[0]["components"]["thread_context"] > 0)


def test_dedupe_and_emit():
    ws = _ws([
        _sig("p1", 4.0), _sig("p2", 4.0),
        {"seq": 2, "ts": _iso(4), "type": "email_sent", "data": {"person_ids": ["p1"]}},  # emailed 4d ago -> exclude p1
    ])
    top = rm.compute_relationship_moves(ws, top_n=3)
    pids = [c["person_id"] for c in top]
    check("email_sent 4d ago excludes p1", "p1" not in pids and "p2" in pids)
    rms = [json.loads(l) for l in (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines()
           if l.strip() and json.loads(l).get("type") == "relationship_move_suggested"]
    check("exactly one suggested event per returned candidate", len(rms) == len(top))
    check("emitted components byte-equal to returned", rms[0]["data"]["components"] == top[0]["components"])

    ws2 = _ws([_sig("p3", 4.0), {"seq": 2, "ts": _iso(8), "type": "email_sent", "data": {"person_ids": ["p3"]}}])
    check("email_sent 8d ago does NOT exclude", "p3" in [c["person_id"] for c in rm.compute_relationship_moves(ws2, emit=False)])

    ws3 = _ws([_sig("p4", 4.0), {"seq": 2, "ts": _iso(5), "type": "relationship_move_suggested", "data": {"person_id": "p4"}}])
    check("prior suggested 5d ago excludes", "p4" not in [c["person_id"] for c in rm.compute_relationship_moves(ws3, emit=False)])

    ws4 = _ws([_sig("p5", 4.0), {"seq": 2, "ts": _iso(1), "type": "dont_forget_snooze", "data": {"person_id": "p5"}}])
    check("active snooze excludes", "p5" not in [c["person_id"] for c in rm.compute_relationship_moves(ws4, emit=False)])


def test_snooze_bounded():
    # Regression (BAL1 second-eyes follow-up, 2026-07-19): dont_forget_snooze
    # exclusion was unbounded — one Pulse snooze muted a person forever.
    ws = _ws([_sig("p6", 4.0), {"seq": 2, "ts": _iso(30), "type": "dont_forget_snooze", "data": {"person_id": "p6"}}])
    check("stale snooze (30d, no snooze_until) no longer excludes", "p6" in [c["person_id"] for c in rm.compute_relationship_moves(ws, emit=False)])

    ws2 = _ws([_sig("p7", 4.0), {"seq": 2, "ts": _iso(30), "type": "dont_forget_snooze",
               "data": {"person_id": "p7", "snooze_until": _iso(-30)}}])
    check("future snooze_until still excludes", "p7" not in [c["person_id"] for c in rm.compute_relationship_moves(ws2, emit=False)])

    ws3 = _ws([_sig("p8", 4.0), {"seq": 2, "ts": _iso(2), "type": "dont_forget_snooze",
               "data": {"person_id": "p8", "snooze_until": _iso(1)}}])
    check("expired snooze_until does not exclude (even inside window)", "p8" in [c["person_id"] for c in rm.compute_relationship_moves(ws3, emit=False)])


def test_no_pad():
    ws = _ws([_sig("p1", 4.0), _sig("p2", 2.0)])
    top = rm.compute_relationship_moves(ws, top_n=3, emit=False)
    check("2 candidates -> returns 2 (never pads to 3)", len(top) == 2)

    ws0 = _ws([])
    top0 = rm.compute_relationship_moves(ws0, top_n=3)
    rms0 = [l for l in (ws0 / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    check("0 candidates -> empty, no event emitted", top0 == [] and rms0 == [])


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_scoring_exact()
    test_thread_person_collapse()
    test_dedupe_and_emit()
    test_snooze_bounded()
    test_no_pad()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL relationship_moves tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
