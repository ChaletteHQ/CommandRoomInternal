#!/usr/bin/env python3
"""Phase 6 Loop 5 — extraction-miss detection, clustering, and hint store."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import extraction_hints as eh  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _ws(events):
    ws = Path(tempfile.mkdtemp(prefix="eh_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    lines = [json.dumps({**e, "seq": i}) for i, e in enumerate(events, 1)]
    (ws / "_hq" / "data" / "events.jsonl").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")
    return ws


def test_resolution_miss_classifier():
    check("already done", eh.is_resolution_miss("Actually that's already done, thanks"))
    check("sent it last week", eh.is_resolution_miss("I sent that over last week"))
    check("already handled", eh.is_resolution_miss("already handled on our end"))
    check("normal reply not a miss", eh.is_resolution_miss("Sure, I'll get to it Friday") is False)


def test_find_recent_meeting():
    meeting = {"type": "meeting_processed", "ts": _iso(5),
               "person_ids": ["person_003", "person_004"],
               "data": {"meeting_id": "granola:xyz", "source_ref": "granola:xyz"}}
    # decision logged 3h after the meeting, overlapping attendee → miss.
    dec = {"type": "decision", "ts": _iso(2), "person_ids": ["person_003"],
           "data": {"summary": "go with vendor A"}}
    ref = eh.find_recent_meeting(dec, [meeting])
    check("overlapping decision within 24h → miss", ref is not None and ref["meeting_id"] == "granola:xyz")
    # 40h later → outside window.
    dec_old = {"type": "decision", "ts": _iso(5), "person_ids": ["person_003"],
               "data": {"summary": "x"}}
    meeting_old = {**meeting, "ts": _iso(50)}
    check("outside 24h window → no miss", eh.find_recent_meeting(dec_old, [meeting_old]) is None)
    # no attendee overlap → no miss.
    dec_np = {"type": "decision", "ts": _iso(2), "person_ids": ["person_999"], "data": {}}
    check("no attendee overlap → no miss", eh.find_recent_meeting(dec_np, [meeting]) is None)


def test_load_misses_all_three_sources():
    events = [
        # (a) explicitly tagged extraction_miss
        {"type": "commitment", "ts": _iso(1),
         "data": {"id": "cmt_1", "kind": "promise", "title": "send the recap",
                  "extraction_miss": True, "meeting_type": "external"}},
        # (b) resolution_miss on an outcome
        {"type": "email_outcome", "ts": _iso(2),
         "data": {"recipient": "x@example.com", "outcome": "replied",
                  "resolution_miss": True, "summary": "already sent last week"}},
        # (c) session-sweep recovery overlapping a processed meeting
        {"type": "meeting_processed", "ts": _iso(6),
         "person_ids": ["person_003"], "data": {"meeting_id": "granola:1"}},
        {"type": "commitment", "ts": _iso(5), "person_ids": ["person_003"],
         "data": {"id": "cmt_2", "kind": "promise", "title": "share the numbers",
                  "source_ref": "session:abc"}},
    ]
    ws = _ws(events)
    rows = eh.load_misses(ws)
    kinds = sorted({r["kind"] for r in rows})
    check("collects extraction + resolution", kinds == ["extraction", "resolution"])
    check("session-sweep recovery counted as extraction",
          any(r.get("via") == "session_sweep" for r in rows))
    check("three miss rows total", len(rows) == 3)


def test_cluster_and_propose():
    rows = [{"kind": "extraction", "summary": "send the recap deck", "meeting_type": "external"}
            for _ in range(4)]
    clusters = eh.cluster_misses(rows)
    check("4 similar misses cluster", any(c["count"] == 4 for c in clusters.values()))
    props = eh.propose_hints(clusters)
    check("proposes a hint", len(props) == 1)
    check("below ≥3 floor → no cluster",
          eh.cluster_misses(rows[:2]) == {})


def test_hint_store_append_dedup():
    ws = Path(tempfile.mkdtemp(prefix="ehs_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    check("first append", eh.append_extraction_hint(ws, "Capture 'send the recap' items"))
    check("dup append no-op", eh.append_extraction_hint(ws, "Capture 'send the recap' items") is False)
    hints = eh.load_extraction_hints(ws)
    check("hint readable back", any("recap" in h for h in hints))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_resolution_miss_classifier()
    test_find_recent_meeting()
    test_load_misses_all_three_sources()
    test_cluster_and_propose()
    test_hint_store_append_dedup()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL extraction_hints tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
