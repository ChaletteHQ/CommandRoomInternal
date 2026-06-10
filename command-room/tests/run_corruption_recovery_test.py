#!/usr/bin/env python3
"""Tests for Sub-bug #14b 2-layer substrate-hygiene defense:
  - Layer 1: recover_corruption.run_recovery_if_needed (this file)
  - Layer 2: cru_match.load_events_defensively (this file)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from cru_match import load_events_defensively, load_open_commitments  # noqa: E402
from recover_corruption import (  # noqa: E402
    RECOVERY_VERSION,
    run_recovery_if_needed,
)


def _setup_workspace(lines: list[str]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="cr_recovery_test_"))
    data_dir = tmp / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    events_path = data_dir / "events.jsonl"
    with open(events_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    return tmp


def test_load_events_defensively_handles_non_dict() -> None:
    """The actual Sub-bug #14b crash class was non-dict, not JSONDecodeError."""
    ws = _setup_workspace([
        json.dumps({"seq": 1, "type": "commitment", "data": {"status": "open", "title": "do x", "commitment_id": "c1"}}),
        '"a string at top level"',  # non-dict — would crash pre-v3.13.8
        json.dumps([1, 2, 3]),  # list at top level — also non-dict
        json.dumps({"seq": 2, "type": "commitment", "data": {"status": "open", "title": "do y", "commitment_id": "c2"}}),
    ])
    events, skipped = load_events_defensively(ws / "_hq" / "data" / "events.jsonl")
    assert len(events) == 2, events
    assert len(skipped) == 2, skipped
    assert all(s["reason"] == "non-dict" for s in skipped), skipped
    print("PASS test_load_events_defensively_handles_non_dict")


def test_load_events_defensively_handles_json_decode_error() -> None:
    ws = _setup_workspace([
        json.dumps({"seq": 1, "type": "x"}),
        "not valid json {",
        '{"unfinished":',
        json.dumps({"seq": 2, "type": "x"}),
    ])
    events, skipped = load_events_defensively(ws / "_hq" / "data" / "events.jsonl")
    assert len(events) == 2, events
    assert len(skipped) == 2, skipped
    assert all("JSONDecodeError" in s["reason"] for s in skipped), skipped
    print("PASS test_load_events_defensively_handles_json_decode_error")


def test_load_open_commitments_survives_malformed_lines() -> None:
    """The end-to-end Sub-bug #14b path: load_open_commitments must not crash."""
    ws = _setup_workspace([
        json.dumps({
            "seq": 1, "type": "commitment",
            "data": {"status": "open", "title": "task1", "commitment_id": "c1"}
        }),
        "garbage line",
        '"top-level-string"',
        json.dumps({
            "seq": 2, "type": "commitment",
            "data": {"status": "open", "title": "task2", "commitment_id": "c2"}
        }),
    ])
    open_evs = load_open_commitments(ws / "_hq" / "data" / "events.jsonl")
    assert len(open_evs) == 2, open_evs
    print("PASS test_load_open_commitments_survives_malformed_lines")


def test_recovery_quarantines_malformed_lines() -> None:
    ws = _setup_workspace([
        json.dumps({"seq": 1, "type": "x"}),
        json.dumps({"seq": 2, "type": "x"}),
        json.dumps({"seq": 3, "type": "x"}),
        "{malformed",
        '"top-string"',
        json.dumps({"seq": 6, "type": "x"}),
        json.dumps({"seq": 7, "type": "x"}),
        json.dumps({"seq": 8, "type": "x"}),
    ])
    summary = run_recovery_if_needed(ws)
    assert summary["ran"] is True, summary
    assert summary["quarantined_line_count"] >= 2, summary
    assert summary["recovery_version"] == RECOVERY_VERSION
    # Quarantine file should exist
    qfile = Path(summary["quarantine_file"])
    assert qfile.exists(), qfile
    # events.jsonl should still load fine (no more malformed lines)
    events, skipped = load_events_defensively(ws / "_hq" / "data" / "events.jsonl")
    assert len(skipped) == 0, skipped
    # corruption_recovery event present
    recovery_events = [e for e in events if e.get("type") == "corruption_recovery"]
    assert len(recovery_events) == 1, recovery_events
    print("PASS test_recovery_quarantines_malformed_lines")


def test_recovery_idempotent() -> None:
    ws = _setup_workspace([
        json.dumps({"seq": 1, "type": "x"}),
        "{malformed",
        json.dumps({"seq": 3, "type": "x"}),
    ])
    first = run_recovery_if_needed(ws)
    assert first["ran"] is True, first
    second = run_recovery_if_needed(ws)
    assert second["ran"] is False, second
    assert second["skipped_reason"] == "already_run", second
    print("PASS test_recovery_idempotent")


def test_recovery_noop_when_no_corruption() -> None:
    ws = _setup_workspace([
        json.dumps({"seq": 1, "type": "x"}),
        json.dumps({"seq": 2, "type": "x"}),
    ])
    summary = run_recovery_if_needed(ws)
    assert summary["ran"] is False, summary
    assert summary["skipped_reason"] == "no_corruption_found", summary
    print("PASS test_recovery_noop_when_no_corruption")


def test_recovery_preserves_neighbors_of_malformed_lines() -> None:
    """v3.13.8 ship-time regression — caught by the slow-pass runtime exercise.

    An earlier draft of recover_corruption used `window=3` around each
    malformed line to capture "multi-line corruption neighbors." For
    line-oriented JSONL that was wrong: well-formed JSON dicts that happened
    to sit near a malformed line got quarantined too, destroying real data.

    This test pins the fix — only malformed lines themselves are quarantined.
    """
    ws = _setup_workspace([
        json.dumps({"seq": i, "type": "x"}) for i in range(1, 11)
    ] + [
        '"bad-1"', "{malformed-2",
        json.dumps({"seq": 11, "type": "x"}),
        json.dumps({"seq": 12, "type": "x"}),
    ])
    summary = run_recovery_if_needed(ws)
    assert summary["ran"] is True
    # ONLY the 2 malformed lines should be quarantined — not the 8 well-formed
    # commitments at positions 8, 9, 10, 12, 13 that would have been swept up
    # under window=3 (range 8-15 around malformed lines 11, 12 in the file).
    assert summary["quarantined_line_count"] == 2, summary

    # Verify surviving file still has every well-formed event
    from cru_match import load_events_defensively
    events, skipped = load_events_defensively(ws / "_hq" / "data" / "events.jsonl")
    seqs = {e.get("seq") for e in events if e.get("type") == "x"}
    assert seqs == set(range(1, 13)), f"missing seqs after recovery: {sorted(seqs)}"
    print("PASS test_recovery_preserves_neighbors_of_malformed_lines")


def main() -> int:
    test_load_events_defensively_handles_non_dict()
    test_load_events_defensively_handles_json_decode_error()
    test_load_open_commitments_survives_malformed_lines()
    test_recovery_quarantines_malformed_lines()
    test_recovery_idempotent()
    test_recovery_noop_when_no_corruption()
    test_recovery_preserves_neighbors_of_malformed_lines()
    print("\nALL Sub-bug #14b tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
