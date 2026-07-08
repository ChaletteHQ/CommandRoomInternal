#!/usr/bin/env python3
"""Regression guard for relationship-moves on date-only commitment dues
(v4.0.0 dogfood Bug 3).

`_load_overdue` compares `now_dt` (tz-aware, from _now_iso) against `due`
(from _parse_ts). A date-only due like `2026-05-22` parsed to a tz-NAIVE
datetime → `now_dt <= due` raised "can't compare offset-naive and
offset-aware". Real data has many date-only dues (11 of 59 in the dogfood
workspace). The existing test bypassed this by injecting `commitment_overdue`
directly; this one drives the REAL `_load_overdue` substrate path.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import relationship_moves as rm  # noqa: E402
from cru_match import _parse_ts  # noqa: E402


def _workspace(events: list[dict]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="cr-relmoves-test-"))
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    (data / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    return d


def test_parse_ts_date_only_is_aware() -> None:
    dt = _parse_ts("2026-05-22")
    assert dt is not None and dt.tzinfo is not None, dt
    # aware-vs-aware comparison must not raise
    assert (_parse_ts("2026-06-21T00:00:00Z") <= dt) is False
    print("PASS test_parse_ts_date_only_is_aware")


def test_load_overdue_with_date_only_due_does_not_crash() -> None:
    events = [
        # open commitment with a DATE-ONLY due (the real-data shape that crashed)
        {"type": "commitment", "seq": 1, "ts": "2026-05-01T00:00:00Z",
         "person_ids": ["person_001"],
         "data": {"due": "2026-05-22", "text": "send the deck", "_commitment_id": "c1"}},
    ]
    ws = _workspace(events)
    now_dt = _parse_ts("2026-06-21T00:00:00Z")
    overdue = rm._load_overdue(ws, now_dt)  # must not raise
    assert overdue.get("person_001", 0) > 0, overdue  # ~30 days overdue
    print("PASS test_load_overdue_with_date_only_due_does_not_crash")


def test_compute_relationship_moves_end_to_end() -> None:
    events = [
        {"type": "commitment", "seq": 1, "ts": "2026-05-01T00:00:00Z",
         "person_ids": ["person_001"],
         "data": {"due": "2026-05-22", "text": "x", "_commitment_id": "c1"}},
    ]
    ws = _workspace(events)
    out = rm.compute_relationship_moves(ws, top_n=3, thread_totals={}, now="2026-06-21T00:00:00Z")
    assert isinstance(out, list), out  # returns a list instead of raising
    print("PASS test_compute_relationship_moves_end_to_end")


def main() -> int:
    test_parse_ts_date_only_is_aware()
    test_load_overdue_with_date_only_due_does_not_crash()
    test_compute_relationship_moves_end_to_end()
    print("ALL relationship-moves real-data tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
