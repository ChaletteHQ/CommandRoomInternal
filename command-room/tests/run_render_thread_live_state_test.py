#!/usr/bin/env python3
"""Tests for render_thread_live_state.py (brain-substrate-drift build).

Verifies the orchestrator: confirmed/proposed split, status line, the
self-bootstrapping create-after-heading, the dirty-check (skip when not
newer / re-render when newer), and the human-counter seq filter that ignores
legacy nano-epoch seqs.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import render_thread_live_state as rtls  # noqa: E402

results = {"pass": 0, "fail": 0}


def check(name, cond):
    if cond:
        results["pass"] += 1; print(f"PASS {name}")
    else:
        results["fail"] += 1; print(f"FAIL {name}")


def _ws(events):
    ws = Path(tempfile.mkdtemp(prefix="cr-rtls-test-"))
    d = ws / "_hq" / "data"; d.mkdir(parents=True)
    people = [{"id": f"person_{i}", "canonical_name": f"P{i}"} for i in range(1, 6)]
    threads = [{"id": "project_t2", "canonical_name": "Plugin", "status": "active",
                "folder_name": "Plugin"}]
    (d / "entities.json").write_text(json.dumps({"people": people, "threads": threads}), encoding="utf-8")
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    (ws / "Plugin").mkdir()
    (ws / "Plugin" / "PROJECT_BRAIN.md").write_text(
        "# Plugin Brain\n\n## 1. People\n\n## 2. Durable\nkeep me\n", encoding="utf-8")
    return ws


def test_format_split_and_status():
    ws = _ws([
        {"seq": 1, "ts": "2026-05-01", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
        {"seq": 2, "ts": "2026-05-02", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
        {"seq": 3, "ts": "2026-05-03", "primary_thread_id": "project_t2", "person_ids": ["person_2"]},  # 1 direct = low/proposed
    ])
    body, seq = rtls.format_live_state(ws, "project_t2")
    check("status line present", "**Status:** active" in body)
    check("confirmed in table", "| P1 |" in body)
    check("proposed line for low-signal", "Proposed" in body and "P2" in body)
    check("source_seq is human counter", seq == 3)


def test_human_counter_ignores_nano_epoch():
    ws = _ws([
        {"seq": 5, "ts": "2026-05-01", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
        {"seq": 1779989321439952674, "ts": "2026-05-02", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
    ])
    _, seq = rtls.format_live_state(ws, "project_t2")
    check("nano-epoch seq ignored (seq==5 not 1.7e18)", seq == 5)


def test_render_creates_then_dirty_check():
    ws = _ws([
        {"seq": 1, "ts": "2026-05-01", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
        {"seq": 2, "ts": "2026-05-02", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
    ])
    bp = ws / "Plugin" / "PROJECT_BRAIN.md"
    r1 = rtls.render_live_state(ws, "project_t2", brain_path=bp)
    check("first render creates block", r1["status"] == "created" and r1["rendered"])
    text = bp.read_text(encoding="utf-8")
    check("durable preserved after create", "## 2. Durable\nkeep me" in text)
    # No new events → dirty-check should skip
    r2 = rtls.render_live_state(ws, "project_t2", brain_path=bp)
    check("dirty-check skips when not newer", r2["rendered"] is False)
    # Add a newer event → should re-render
    with open(ws / "_hq" / "data" / "events.jsonl", "a", encoding="utf-8") as f:
        f.write("\n" + json.dumps({"seq": 7, "ts": "2026-05-09", "primary_thread_id": "project_t2", "person_ids": ["person_3", "person_4", "person_5"]}))
        # person_3 needs 2 direct for "high"; one event gives low. add a second
        f.write("\n" + json.dumps({"seq": 8, "ts": "2026-05-10", "primary_thread_id": "project_t2", "person_ids": ["person_3"]}))
    r3 = rtls.render_live_state(ws, "project_t2", brain_path=bp)
    check("re-renders when newer event exists", r3["rendered"] and r3["status"] == "written")


def main():
    test_format_split_and_status()
    test_human_counter_ignores_nano_epoch()
    test_render_creates_then_dirty_check()
    print(f"=== {results['pass']} passed, {results['fail']} failed ===")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
