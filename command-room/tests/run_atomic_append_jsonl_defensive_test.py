#!/usr/bin/env python3
"""
Defensive-wrap test for atomic_append_jsonl (v3.13.8.1 — Bug #68 fix).

Bug #68 root cause: callers passing a single dict instead of a list caused
`for e in events: json.dumps(e)+"\n"` to iterate the dict's keys, writing
each key as a malformed line. Recovery (Sub-bug #14b §2.7) cleaned the mess
but the writer kept re-corrupting events.jsonl every few hours.

The v3.13.8.1 fix is a defensive wrap inside atomic_append_jsonl itself —
single-dict input gets wrapped to [dict] transparently; any other shape raises
TypeError loudly rather than silently producing garbage.

Verifies:
  1. Canonical list[dict] input still works (regression check).
  2. Single-dict input is transparently wrapped to [dict] — produces exactly
     one well-formed JSON line.
  3. Non-list non-dict input (e.g., str, None, int) raises TypeError.
  4. List with a non-dict entry raises TypeError identifying the bad index.
  5. The exact Bug #68 reproducer (passing a commitment-shaped bare dict)
     now produces a single well-formed JSONL line, not 7 keys-only lines.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from atomic_write import atomic_append_jsonl  # noqa: E402


def _setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="cr_append_defensive_"))
    return tmp / "events.jsonl"


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _assert_all_valid_json(lines: list[str]) -> list[dict]:
    """Every line must parse as a JSON object (the Bug #68 contract)."""
    out = []
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise AssertionError(f"Line {i} is not valid JSON: {line!r} ({e})")
        assert isinstance(obj, dict), f"Line {i} is not a JSON object: {obj!r}"
        out.append(obj)
    return out


# ---------- Test 1: canonical list[dict] input still works ----------

def test_list_of_dicts_canonical():
    path = _setup()
    events = [
        {"seq": 1, "type": "alpha", "data": {"x": 1}},
        {"seq": 2, "type": "beta", "data": {"y": 2}},
    ]
    atomic_append_jsonl(path, events)
    lines = _read_lines(path)
    assert len(lines) == 2, f"expected 2 lines, got {len(lines)}: {lines}"
    parsed = _assert_all_valid_json(lines)
    assert parsed[0]["type"] == "alpha"
    assert parsed[1]["type"] == "beta"
    print("PASS test_list_of_dicts_canonical")


# ---------- Test 2: single-dict input is wrapped transparently ----------

def test_single_dict_input_wrapped():
    path = _setup()
    # Bug #68 trigger pattern — caller passes a bare dict
    event = {"seq": 42, "type": "lonely", "data": {"foo": "bar"}}
    atomic_append_jsonl(path, event)
    lines = _read_lines(path)
    assert len(lines) == 1, (
        f"expected 1 line (defensive wrap), got {len(lines)}: {lines}"
    )
    parsed = _assert_all_valid_json(lines)
    assert parsed[0]["type"] == "lonely"
    assert parsed[0]["data"] == {"foo": "bar"}
    print("PASS test_single_dict_input_wrapped")


# ---------- Test 3: non-list non-dict input raises TypeError ----------

def test_bad_type_str_raises():
    path = _setup()
    try:
        atomic_append_jsonl(path, "not valid")  # type: ignore[arg-type]
    except TypeError as e:
        assert "list[dict] or dict" in str(e), f"unexpected message: {e}"
        print("PASS test_bad_type_str_raises")
        return
    raise AssertionError("expected TypeError for str input, got nothing")


def test_bad_type_none_raises():
    path = _setup()
    try:
        atomic_append_jsonl(path, None)  # type: ignore[arg-type]
    except TypeError as e:
        assert "list[dict] or dict" in str(e), f"unexpected message: {e}"
        print("PASS test_bad_type_none_raises")
        return
    raise AssertionError("expected TypeError for None input, got nothing")


def test_bad_type_int_raises():
    path = _setup()
    try:
        atomic_append_jsonl(path, 42)  # type: ignore[arg-type]
    except TypeError as e:
        assert "list[dict] or dict" in str(e), f"unexpected message: {e}"
        print("PASS test_bad_type_int_raises")
        return
    raise AssertionError("expected TypeError for int input, got nothing")


# ---------- Test 4: list with non-dict entry raises TypeError ----------

def test_list_with_non_dict_entry_raises():
    path = _setup()
    events = [
        {"seq": 1, "type": "good"},
        "this is not a dict",  # bad entry at index 1
        {"seq": 3, "type": "good"},
    ]
    try:
        atomic_append_jsonl(path, events)  # type: ignore[arg-type]
    except TypeError as e:
        assert "entry 1" in str(e), f"expected entry index in message, got: {e}"
        print("PASS test_list_with_non_dict_entry_raises")
        return
    raise AssertionError("expected TypeError for non-dict list entry, got nothing")


# ---------- Test 5: Bug #68 exact reproducer ----------

def test_bug_68_commitment_writer_reproducer():
    """
    The exact bug pattern Cowork diagnosed during v3.13.8 verification:
    a commitment writer (scan-for-commitments / meeting-notes / past-meetings)
    called atomic_append_jsonl with a bare commitment-shaped dict. The
    pre-v3.13.8.1 behavior iterated dict keys → wrote each key as a malformed
    line (7 lines for a commitment-shaped event). Post-fix: 1 well-formed line.
    """
    path = _setup()
    commitment_event = {
        "seq": 1779999999999999999,
        "ts": "2026-05-25T03:14:15Z",
        "type": "commitment_resolved",
        "source_skill": "scan-for-commitments",
        "primary_thread_id": "project_015",
        "related_thread_ids": ["project_001"],
        "classification_confidence": 0.92,
        "data": {
            "commitment_id": 1779999999999999990,
            "resolved_via": "test-fixture",
        },
    }
    atomic_append_jsonl(path, commitment_event)

    lines = _read_lines(path)
    assert len(lines) == 1, (
        f"Bug #68 reproducer: expected 1 well-formed line, got {len(lines)} "
        f"(if you see 7 keys-only lines, the defensive wrap is missing): {lines}"
    )
    parsed = _assert_all_valid_json(lines)
    assert parsed[0]["type"] == "commitment_resolved"
    assert parsed[0]["data"]["commitment_id"] == 1779999999999999990
    # Critical Bug #68 check: line must NOT contain bare key strings
    assert lines[0] != '"ts"', "Bug #68 regression — keys-only line detected"
    assert lines[0] != '"type"', "Bug #68 regression — keys-only line detected"
    print("PASS test_bug_68_commitment_writer_reproducer")


def main():
    test_list_of_dicts_canonical()
    test_single_dict_input_wrapped()
    test_bad_type_str_raises()
    test_bad_type_none_raises()
    test_bad_type_int_raises()
    test_list_with_non_dict_entry_raises()
    test_bug_68_commitment_writer_reproducer()
    print()
    print("OK — all 7 atomic_append_jsonl defensive-wrap tests passed.")


if __name__ == "__main__":
    main()
