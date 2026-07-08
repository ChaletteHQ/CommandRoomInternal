#!/usr/bin/env python3
"""Regression guard for the events_io defensive reader's type-guard
(v4.0.0 re-verify, Open Q4).

`_iter_file` always dropped non-dict lines ([1,2,3], "x", 42) but ADMITTED any
dict — including structurally-valid-but-empty rows ({}, {"type": null}) that are
not events. They inflated load_all() counts and handed type-filtering consumers
a row with no type. The guard now requires a non-empty string `type`. Verified
0/3221 live events lack one, so no real data is dropped.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from events_io import load_all  # noqa: E402


def _workspace(lines: list[str]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="cr-typeguard-test-"))
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    (data / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def test_drops_empty_and_typeless_dicts() -> None:
    lines = [
        json.dumps({"type": "meeting", "seq": 1, "data": {}}),  # valid
        "{}",                                                     # empty dict
        json.dumps({"type": None, "seq": 2}),                     # null type
        json.dumps({"type": "", "seq": 3}),                       # empty-string type
        json.dumps({"seq": 4, "data": {}}),                       # no type key
        json.dumps({"type": "decision", "seq": 5}),               # valid
    ]
    events = load_all(_workspace(lines))
    assert len(events) == 2, f"expected 2 real events, got {len(events)}: {events}"
    assert {e["type"] for e in events} == {"meeting", "decision"}, events
    print("PASS test_drops_empty_and_typeless_dicts")


def test_drops_non_dict_rows() -> None:
    lines = [
        json.dumps({"type": "meeting", "seq": 1}),  # valid
        "[1, 2, 3]",                                 # bare array
        '"a string"',                                # bare string
        "42",                                        # bare number
        "true",                                      # bare bool
        "   ",                                        # blank
        "{not json",                                 # unparseable
        json.dumps({"type": "note", "seq": 2}),      # valid
    ]
    events = load_all(_workspace(lines))
    assert len(events) == 2, f"expected 2 real events, got {len(events)}: {events}"
    print("PASS test_drops_non_dict_rows")


def test_keeps_all_valid_events() -> None:
    lines = [json.dumps({"type": "x", "seq": i}) for i in range(50)]
    events = load_all(_workspace(lines))
    assert len(events) == 50, f"expected 50, got {len(events)}"
    print("PASS test_keeps_all_valid_events")


def main() -> int:
    test_drops_empty_and_typeless_dicts()
    test_drops_non_dict_rows()
    test_keeps_all_valid_events()
    print("ALL events_io type-guard tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
