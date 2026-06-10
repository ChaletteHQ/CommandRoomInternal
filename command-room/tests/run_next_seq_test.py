#!/usr/bin/env python3
"""Tests for next_seq.py (Bug #41 — canonical seq reservation helper)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from next_seq import next_seq, EPOCH_THRESHOLD  # noqa: E402


def _write_events(lines: list[str]) -> Path:
    fd = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".jsonl", delete=False
    )
    for line in lines:
        fd.write(line + "\n")
    fd.close()
    return Path(fd.name)


def test_empty_file_returns_one() -> None:
    path = _write_events([])
    assert next_seq(path) == 1
    print("PASS test_empty_file_returns_one")


def test_nonexistent_returns_one() -> None:
    path = Path(tempfile.gettempdir()) / "does_not_exist_at_all.jsonl"
    if path.exists():
        path.unlink()
    assert next_seq(path) == 1
    print("PASS test_nonexistent_returns_one")


def test_simple_human_counter() -> None:
    path = _write_events([
        json.dumps({"seq": i, "type": "x"}) for i in range(1, 101)
    ])
    assert next_seq(path) == 101
    print("PASS test_simple_human_counter")


def test_ignores_nano_epoch_artifacts() -> None:
    path = _write_events([
        json.dumps({"seq": 1, "type": "x"}),
        json.dumps({"seq": 2, "type": "x"}),
        json.dumps({"seq": 1779426060120467301, "type": "artifact"}),
        json.dumps({"seq": 3, "type": "x"}),
        json.dumps({"seq": 1779426060120467302, "type": "artifact"}),
    ])
    assert next_seq(path) == 4
    print("PASS test_ignores_nano_epoch_artifacts")


def test_tail_line_no_seq() -> None:
    path = _write_events([
        json.dumps({"seq": 1, "type": "x"}),
        json.dumps({"seq": 2, "type": "x"}),
        json.dumps({"type": "no_seq_event"}),  # last line has no seq
    ])
    assert next_seq(path) == 3
    print("PASS test_tail_line_no_seq")


def test_non_dict_lines_skipped() -> None:
    path = _write_events([
        json.dumps({"seq": 1, "type": "x"}),
        '"a string at top level"',  # non-dict
        json.dumps([1, 2, 3]),  # non-dict
        json.dumps({"seq": 5, "type": "x"}),
    ])
    assert next_seq(path) == 6
    print("PASS test_non_dict_lines_skipped")


def test_malformed_lines_skipped() -> None:
    path = _write_events([
        json.dumps({"seq": 1, "type": "x"}),
        "not valid json {",
        '{"unfinished":',
        json.dumps({"seq": 2, "type": "x"}),
    ])
    assert next_seq(path) == 3
    print("PASS test_malformed_lines_skipped")


def test_blank_lines_skipped() -> None:
    path = _write_events([
        json.dumps({"seq": 1, "type": "x"}),
        "",
        "   ",
        json.dumps({"seq": 7, "type": "x"}),
        "",
    ])
    assert next_seq(path) == 8
    print("PASS test_blank_lines_skipped")


def test_bool_seq_ignored() -> None:
    """Bools are int subclass in Python — make sure we don't accept them as seqs."""
    path = _write_events([
        json.dumps({"seq": True, "type": "boolish"}),
        json.dumps({"seq": 5, "type": "x"}),
    ])
    assert next_seq(path) == 6
    print("PASS test_bool_seq_ignored")


def test_epoch_threshold_boundary() -> None:
    """Seq exactly at EPOCH_THRESHOLD is excluded; one below is included."""
    path = _write_events([
        json.dumps({"seq": EPOCH_THRESHOLD - 1, "type": "human"}),
        json.dumps({"seq": EPOCH_THRESHOLD, "type": "artifact"}),
    ])
    assert next_seq(path) == EPOCH_THRESHOLD
    print("PASS test_epoch_threshold_boundary")


def main() -> int:
    test_empty_file_returns_one()
    test_nonexistent_returns_one()
    test_simple_human_counter()
    test_ignores_nano_epoch_artifacts()
    test_tail_line_no_seq()
    test_non_dict_lines_skipped()
    test_malformed_lines_skipped()
    test_blank_lines_skipped()
    test_bool_seq_ignored()
    test_epoch_threshold_boundary()
    print("\nALL next_seq tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
