#!/usr/bin/env python3
"""SPEC REL1 Part 1 — dormancy_signal normalization tests."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import dormancy as dz  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _ws(events):
    ws = Path(tempfile.mkdtemp(prefix="rel1_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text(
        "\n".join(json.dumps(e) if isinstance(e, dict) else e for e in events) + "\n",
        encoding="utf-8")
    return ws


def test_normalize_score():
    check("baseline ratio 42/21 == 2.0", dz.normalize_score(42, 21) == 2.0)
    check("null baseline 14 -> 1.8", dz.normalize_score(14, None) == 1.8)
    check("null baseline 29 -> 1.8", dz.normalize_score(29, None) == 1.8)
    check("null baseline 30 -> 2.5", dz.normalize_score(30, None) == 2.5)
    check("null baseline 59 -> 2.5", dz.normalize_score(59, None) == 2.5)
    check("null baseline 60 -> 4.0", dz.normalize_score(60, None) == 4.0)
    check("null baseline <14 -> None", dz.normalize_score(13, None) is None)


def test_emit():
    ws = _ws([{"seq": 1, "ts": _iso(1), "type": "interaction", "data": {}}])
    s = dz.emit_dormancy_signal(ws, "person_1", "person", 42, 21, "dormant-customer-scan")
    evs = [json.loads(l) for l in (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    sig = [e for e in evs if e.get("type") == "dormancy_signal"]
    check("emit appends exactly one dormancy_signal", len(sig) == 1)
    check("emit seq monotonic (> existing)", sig[0]["seq"] == max(e["seq"] for e in evs))
    check("emit payload valid", sig[0]["data"]["entity_id"] == "person_1" and sig[0]["data"]["score"] == 2.0)
    dz.emit_dormancy_signal(ws, "person_1", "person", 50, 21, "dormant-customer-scan")
    evs2 = [json.loads(l) for l in (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    sig2 = [e for e in evs2 if e.get("type") == "dormancy_signal"]
    check("second emit appends (point-in-time)", len(sig2) == 2)
    # sub-14 null baseline -> no signal
    check("no signal when score is None", dz.emit_dormancy_signal(ws, "p9", "person", 5, None, "x") is None)


def test_load_dedupe():
    ws = _ws([
        {"seq": 1, "ts": _iso(3), "type": "dormancy_signal", "data": {"entity_id": "p1", "entity_type": "person", "score": 2.0}},
        {"seq": 2, "ts": _iso(2), "type": "dormancy_signal", "data": {"entity_id": "p1", "entity_type": "person", "score": 3.0}},
        {"seq": 3, "ts": _iso(20), "type": "dormancy_signal", "data": {"entity_id": "p2", "entity_type": "person", "score": 4.0}},  # outside 14d window
        {"seq": 4, "ts": _iso(5), "type": "dormancy_signal", "data": {"entity_id": "p3", "entity_type": "person", "score": 4.0}},
        {"seq": 5, "ts": _iso(1), "type": "interaction", "data": {"person_ids": ["p3"]}},  # newer than p3 signal -> drop
        "this is a corrupt line",
    ])
    sigs = dz.load_dormancy_signals(ws, window_days=14)
    by = {s["data"]["entity_id"]: s["data"]["score"] for s in sigs}
    check("max score wins for p1", by.get("p1") == 3.0)
    check("out-of-window p2 dropped", "p2" not in by)
    check("p3 dropped (newer interaction)", "p3" not in by)
    check("malformed line skipped, no crash", True)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_normalize_score()
    test_emit()
    test_load_dedupe()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL dormancy_signal tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
