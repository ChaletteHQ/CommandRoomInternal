#!/usr/bin/env python3
"""
Auto-stamp test for atomic_append_jsonl (v3.13.8.3 — Bug #74 + Bug #75).

Bug #74 root cause: 36% of events in M's events.jsonl (687 / 1910) lacked
`seq`. LLM-driven writers in skill prose frequently omitted the field;
next_seq.py existed but writers didn't always call it. Bug #51 cascade-close
and Bug #71 source_event_seq linking both break for events without seq.

Bug #75 root cause: command-room-coach SKILL.md template lacked a `ts` field,
so the LLM-driven writer produced coach_session events with empty/missing ts.
Bug #68's defensive wrap caught wrong-type writes but not empty-field writes.

The v3.13.8.3 fix is a caller-agnostic layer inside atomic_append_jsonl:
when the destination file is named `events.jsonl`, auto-stamp `seq` and `ts`
on every event missing/empty value, preserving explicit values.

Verifies:
  1. Missing seq → auto-stamped to existing_max_seq + 1 (monotonic).
  2. Missing ts → auto-stamped to current UTC ISO timestamp.
  3. Empty-string ts → treated as missing, auto-stamped.
  4. Whitespace-only ts → treated as missing, auto-stamped.
  5. Explicit seq + ts → preserved verbatim.
  6. Mixed batch (some explicit, some missing) → fills in only the missing.
  7. Non-events.jsonl destination → no auto-stamping (staging_emissions.jsonl
     left untouched).
  8. Nano-epoch seq artifacts (>= 1e10) in existing file → ignored by next_seq
     computation; new events get human-counter seqs not 1.77e18+1.
  9. Empty file → first auto-stamped seq is 1.
 10. Caller's events list / dicts are NOT mutated by the auto-stamp (shallow
     copy contract — important so caller can reuse the dict).
 11. Bug #75 exact reproducer — coach_session event missing ts gets stamped
     with valid ISO timestamp, lands as well-formed JSONL.
"""

from __future__ import annotations

# This rig tests the stamping/write machinery BELOW the event gate with
# synthetic fixture types; the gate (strict on both entries as of Phase 4
# 2026-07-02) is covered by run_event_gate_test.py. Disable it here so the
# fixtures don't need schema registration.
import os
os.environ["CR_EVENT_GATE"] = "0"

import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from atomic_write import atomic_append_jsonl  # noqa: E402


ISO_8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$"
)


def _setup(filename: str = "events.jsonl") -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="cr_append_autostamp_"))
    return tmp / filename


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ---------- Test 1: missing seq → auto-stamped ----------

def test_missing_seq_auto_stamped():
    path = _setup()
    # Pre-seed with seq=5 so existing_max_seq=5
    atomic_append_jsonl(path, [{"seq": 5, "ts": "2026-05-25T00:00:00Z", "type": "seed"}])
    # New event missing seq
    atomic_append_jsonl(path, {"ts": "2026-05-25T00:00:01Z", "type": "noseq"})
    events = _read_events(path)
    assert len(events) == 2
    assert events[1]["type"] == "noseq"
    assert events[1].get("seq") == 6, f"expected auto-stamped seq=6, got {events[1].get('seq')}"
    print("PASS test_missing_seq_auto_stamped")


# ---------- Test 2: missing ts → auto-stamped ISO timestamp ----------

def test_missing_ts_auto_stamped():
    path = _setup()
    atomic_append_jsonl(path, {"seq": 1, "type": "no_ts"})
    events = _read_events(path)
    assert len(events) == 1
    ts = events[0].get("ts")
    assert ts is not None, "ts should have been auto-stamped"
    assert isinstance(ts, str), f"ts should be string, got {type(ts).__name__}"
    assert ISO_8601_RE.match(ts), f"auto-stamped ts not ISO 8601: {ts!r}"
    # Sanity: should be near-now
    parsed = datetime.fromisoformat(ts)
    now = datetime.now(timezone.utc)
    delta_seconds = abs((now - parsed).total_seconds())
    assert delta_seconds < 5, f"auto-stamped ts off by {delta_seconds}s"
    print("PASS test_missing_ts_auto_stamped")


# ---------- Test 3: empty-string ts → treated as missing ----------

def test_empty_string_ts_auto_stamped():
    """Bug #75 exact symptom — Cowork observed an empty-string ts."""
    path = _setup()
    atomic_append_jsonl(path, {"seq": 1, "ts": "", "type": "empty_ts"})
    events = _read_events(path)
    assert len(events) == 1
    ts = events[0].get("ts")
    assert ts != "", "empty ts should have been replaced"
    assert ISO_8601_RE.match(ts), f"replacement ts not ISO 8601: {ts!r}"
    print("PASS test_empty_string_ts_auto_stamped")


# ---------- Test 4: whitespace-only ts → treated as missing ----------

def test_whitespace_ts_auto_stamped():
    path = _setup()
    atomic_append_jsonl(path, {"seq": 1, "ts": "   ", "type": "ws_ts"})
    events = _read_events(path)
    assert len(events) == 1
    ts = events[0].get("ts")
    assert ts.strip() != "", "whitespace ts should have been replaced"
    assert ISO_8601_RE.match(ts), f"replacement ts not ISO 8601: {ts!r}"
    print("PASS test_whitespace_ts_auto_stamped")


# ---------- Test 5: explicit values are preserved verbatim ----------

def test_explicit_values_preserved():
    path = _setup()
    explicit_ts = "2026-01-15T09:30:00+00:00"
    atomic_append_jsonl(
        path,
        {"seq": 42, "ts": explicit_ts, "type": "explicit"},
    )
    events = _read_events(path)
    assert events[0]["seq"] == 42, f"explicit seq overwritten: {events[0]['seq']}"
    assert events[0]["ts"] == explicit_ts, f"explicit ts overwritten: {events[0]['ts']}"
    print("PASS test_explicit_values_preserved")


# ---------- Test 6: mixed batch — partial fill-in ----------

def test_mixed_batch_partial_fill():
    path = _setup()
    atomic_append_jsonl(
        path,
        [
            {"seq": 100, "ts": "2026-01-01T00:00:00Z", "type": "full"},
            {"type": "noseq_nots"},
            {"seq": 200, "type": "ts_only_missing"},
            {"ts": "2026-01-02T00:00:00Z", "type": "seq_only_missing"},
        ],
    )
    events = _read_events(path)
    assert len(events) == 4
    # Row 0 — fully explicit, preserved
    assert events[0]["seq"] == 100
    assert events[0]["ts"] == "2026-01-01T00:00:00Z"
    # Row 1 — both missing → stamped in-order. Counter starts at 1 (empty
    # file → existing_max_seq=0), bumped to 101 after row 0's explicit 100.
    # Stamp seq=101.
    assert events[1]["seq"] == 101, f"expected 101, got {events[1].get('seq')}"
    assert ISO_8601_RE.match(events[1]["ts"]), f"row1 ts not ISO: {events[1]['ts']}"
    # Row 2 — ts missing, seq=200 preserved (bumps counter to 201)
    assert events[2]["seq"] == 200
    assert ISO_8601_RE.match(events[2]["ts"])
    # Row 3 — seq missing → stamped 201 (post-row2's explicit 200).
    # Counter is in-order; no look-ahead. ts preserved.
    assert events[3]["seq"] == 201, f"expected 201, got {events[3].get('seq')}"
    assert events[3]["ts"] == "2026-01-02T00:00:00Z"
    print("PASS test_mixed_batch_partial_fill")


# ---------- Test 7: non-events.jsonl destination — NO auto-stamp ----------

def test_non_events_jsonl_no_autostamp():
    """staging_emissions.jsonl, classifier_feedback.jsonl, etc. should NOT
    be touched. Only events.jsonl is the canonical seq+ts contract surface."""
    path = _setup(filename="staging_emissions.jsonl")
    atomic_append_jsonl(path, {"type": "no_seq_no_ts_no_problem"})
    events = _read_events(path)
    assert len(events) == 1
    assert "seq" not in events[0], "staging_emissions.jsonl should not get seq stamping"
    assert "ts" not in events[0], "staging_emissions.jsonl should not get ts stamping"
    print("PASS test_non_events_jsonl_no_autostamp")


# ---------- Test 8: nano-epoch artifact ignored by max-seq computation ----------

def test_nano_epoch_seq_ignored():
    """Pre-v3.13.8 some writers used int(time.time_ns()) as seq (~1.77e18).
    next_seq.py contract: ignore these (>= 1e10) when computing next."""
    path = _setup()
    # Seed file with mixed seqs: human-counter 7, nano-epoch artifact
    atomic_append_jsonl(
        path,
        [
            {"seq": 7, "ts": "2026-01-01T00:00:00Z", "type": "human"},
            {"seq": 1779999999999999999, "ts": "2026-01-01T00:00:01Z", "type": "nano_epoch"},
        ],
    )
    # New event missing seq — should get 8, not 1.77e18+1
    atomic_append_jsonl(path, {"ts": "2026-01-01T00:00:02Z", "type": "new"})
    events = _read_events(path)
    assert events[-1]["seq"] == 8, (
        f"expected human-counter seq=8 (ignoring nano-epoch artifact), "
        f"got {events[-1]['seq']}"
    )
    print("PASS test_nano_epoch_seq_ignored")


# ---------- Test 9: empty file → first stamped seq is 1 ----------

def test_empty_file_first_seq_is_1():
    path = _setup()
    atomic_append_jsonl(path, {"type": "first"})
    events = _read_events(path)
    assert events[0]["seq"] == 1, f"first seq should be 1, got {events[0].get('seq')}"
    print("PASS test_empty_file_first_seq_is_1")


# ---------- Test 10: caller's dict not mutated ----------

def test_caller_dict_not_mutated():
    """Shallow-copy contract — auto-stamp adds fields to a copy, not the
    caller's original dict. Caller can safely reuse the dict afterwards."""
    path = _setup()
    caller_event = {"type": "reusable"}
    original_keys = set(caller_event.keys())
    atomic_append_jsonl(path, caller_event)
    # Caller's dict must NOT have seq/ts added
    assert set(caller_event.keys()) == original_keys, (
        f"caller dict mutated: {set(caller_event.keys())} vs {original_keys}"
    )
    # But the file's copy DOES have them
    events = _read_events(path)
    assert "seq" in events[0]
    assert "ts" in events[0]
    print("PASS test_caller_dict_not_mutated")


# ---------- Test 11: Bug #75 exact reproducer — coach_session ----------

def test_bug_75_coach_session_reproducer():
    """The exact bug Cowork surfaced during v3.13.8.2 verification:
    coach_session event written by command-room-coach skill with empty ts.
    Pre-v3.13.8.3 behavior left empty string in place. Post-fix: ISO ts."""
    path = _setup()
    coach_event = {
        "type": "coach_session",
        "ts": "",  # Bug #75 — empty string per Cowork's introspection
        "mirror_dimensions_used": ["who", "voice"],
        "insights_shown": ["substrate_5pct"],
        "outputs_offered": ["bailey_check_in"],
        "output_produced": "bailey_check_in",
        "ran_at": "2026-05-25T17:32:30Z",
    }
    atomic_append_jsonl(path, coach_event)
    events = _read_events(path)
    assert len(events) == 1
    assert events[0]["type"] == "coach_session"
    assert events[0]["ts"] != "", "Bug #75 regression — empty ts persisted"
    assert ISO_8601_RE.match(events[0]["ts"]), (
        f"Bug #75 regression — ts not ISO 8601: {events[0]['ts']!r}"
    )
    # seq should also be auto-stamped (Bug #74)
    assert events[0].get("seq") == 1, (
        f"Bug #74 regression — seq not auto-stamped: {events[0].get('seq')}"
    )
    # Domain-specific fields preserved
    assert events[0]["ran_at"] == "2026-05-25T17:32:30Z"
    assert events[0]["output_produced"] == "bailey_check_in"
    print("PASS test_bug_75_coach_session_reproducer")


def main():
    test_missing_seq_auto_stamped()
    test_missing_ts_auto_stamped()
    test_empty_string_ts_auto_stamped()
    test_whitespace_ts_auto_stamped()
    test_explicit_values_preserved()
    test_mixed_batch_partial_fill()
    test_non_events_jsonl_no_autostamp()
    test_nano_epoch_seq_ignored()
    test_empty_file_first_seq_is_1()
    test_caller_dict_not_mutated()
    test_bug_75_coach_session_reproducer()
    print()
    print("OK — all 11 atomic_append_jsonl auto-stamp tests passed.")


if __name__ == "__main__":
    main()
