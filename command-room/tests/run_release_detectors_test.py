#!/usr/bin/env python3
"""
Tests for shared/scripts/release_detectors/ — per-release detectors used by
the command-room-update-bridge skill (v3.4.5+ release-manifest system).

Each detector module exports one or more functions that read events.jsonl
and return {applies, context} dicts. These tests verify each detector
classifies workspace state correctly so the update-bridge surfaces the right
remediation prompts.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from release_detectors.v3_4_4_dropped_commitments import (  # noqa: E402
    count_dropped_open_commitments,
)
from release_detectors.always import always_applies  # noqa: E402
from release_detectors.v3_18_12_reconcile_sent_missing import is_reconcile_sent_missing  # noqa: E402


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK {label}")
    else:
        print(f"  FAIL {label}{(' --- ' + detail) if detail else ''}")
        raise AssertionError(label)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


# -------- always_applies --------


def test_always_applies_returns_true():
    print("test_always_applies_returns_true")
    out = always_applies("/nonexistent/path/events.jsonl")
    _check("applies is True", out["applies"] is True)
    _check("context is empty dict", out["context"] == {})


# -------- v3_4_4_dropped_commitments --------


def test_v344_detector_zero_when_no_commitments():
    print("test_v344_detector_zero_when_no_commitments")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [{"seq": 1, "type": "meeting", "data": {}}])
        out = count_dropped_open_commitments(p)
        _check("applies is False when no commitments", out["applies"] is False)
        _check("count is 0", out["context"]["count"] == 0)


def test_v344_detector_zero_when_only_canonical():
    print("test_v344_detector_zero_when_only_canonical")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "type": "commitment",
             "data": {"id": "c1", "owner_id": "u", "title": "x",
                      "status": "open"}},
            {"seq": 2, "type": "commitment",
             "data": {"id": "c2", "owner_id": "u", "title": "y",
                      "status": "open"}},
        ])
        out = count_dropped_open_commitments(p)
        _check("applies is False — all canonical", out["applies"] is False,
               f"got {out}")


def test_v344_detector_counts_flat_new():
    print("test_v344_detector_counts_flat_new")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "type": "commitment",
             "owner_id": "u", "title": "flat one",
             "status": "open"},
            {"seq": 2, "type": "commitment",
             "owner_id": "u", "title": "flat two",
             "status": "open"},
        ])
        out = count_dropped_open_commitments(p)
        _check("applies is True", out["applies"] is True)
        _check("counts 2 flat-new", out["context"]["count"] == 2,
               f"got {out}")
        _check("by_shape names flat-new",
               out["context"]["by_shape"].get("flat-new") == 2)


def test_v344_detector_counts_legacy():
    print("test_v344_detector_counts_legacy")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "type": "commitment",
             "owner": "u", "title": "legacy one", "status": "open"},
        ])
        out = count_dropped_open_commitments(p)
        _check("counts 1 legacy",
               out["context"]["by_shape"].get("legacy") == 1)


def test_v344_detector_counts_owner_person_id_variant():
    print("test_v344_detector_counts_owner_person_id_variant")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "type": "commitment",
             "data": {"owner_person_id": "u", "title": "variant one",
                      "state": "open"}},
        ])
        out = count_dropped_open_commitments(p)
        _check("counts 1 owner_person_id-variant",
               out["context"]["by_shape"].get("owner_person_id-variant") == 1)


def test_v344_detector_excludes_pending_review():
    print("test_v344_detector_excludes_pending_review")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "type": "commitment",
             "data": {"owner_name_proposed": "Rakesh",
                      "pending_review": True,
                      "title": "x", "status": "open"}},
        ])
        out = count_dropped_open_commitments(p)
        _check("pending-review excluded from dropped count",
               out["applies"] is False, f"got {out}")


def test_v344_detector_excludes_resolved():
    print("test_v344_detector_excludes_resolved")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "type": "commitment",
             "owner_id": "u", "title": "flat resolved",
             "status": "open", "id": "c1"},
            {"seq": 2, "type": "commitment_resolved",
             "data": {"commitment_id": "c1"}},
        ])
        out = count_dropped_open_commitments(p)
        _check("resolved flat-new excluded",
               out["applies"] is False, f"got {out}")


def test_v344_detector_mixed_shapes():
    print("test_v344_detector_mixed_shapes")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "events.jsonl"
        _write_jsonl(p, [
            {"seq": 1, "type": "commitment",
             "data": {"owner_id": "u", "title": "canon", "status": "open"}},
            {"seq": 2, "type": "commitment",
             "owner_id": "u", "title": "flat", "status": "open"},
            {"seq": 3, "type": "commitment",
             "owner": "u", "title": "legacy", "status": "open"},
            {"seq": 4, "type": "commitment",
             "data": {"owner_person_id": "u", "title": "variant",
                      "state": "open"}},
        ])
        out = count_dropped_open_commitments(p)
        _check("counts all 3 non-canonical", out["context"]["count"] == 3,
               f"got {out}")
        _check("canonical excluded",
               "canonical" not in out["context"]["by_shape"])


def test_v344_detector_handles_missing_file():
    print("test_v344_detector_handles_missing_file")
    out = count_dropped_open_commitments("/nonexistent/path/events.jsonl")
    _check("applies is False when file missing",
           out["applies"] is False, f"got {out}")
    _check("count is 0 when file missing",
           out["context"]["count"] == 0)


def test_reconcile_sent_missing_fresh_workspace():
    print("test_reconcile_sent_missing_fresh_workspace")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "events.jsonl"
        # fresh workspace, no events file -> not applicable (first-install registers it)
        _check("missing events file -> applies False",
               is_reconcile_sent_missing(str(p)) == {"applies": False})
        # has schedules but NOT reconcile-sent -> applies True (the gap)
        _write_jsonl(p, [
            {"type": "schedule_created", "data": {"taskId": "morning-brief"}},
            {"type": "schedule_created", "data": {"taskId": "cleanup"}},
        ])
        out = is_reconcile_sent_missing(str(p))
        _check("scheduled workspace missing reconcile-sent -> applies True",
               out.get("applies") is True, str(out))


def test_reconcile_sent_present_not_flagged():
    print("test_reconcile_sent_present_not_flagged")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "events.jsonl"
        _write_jsonl(p, [
            {"type": "schedule_created", "data": {"taskId": "morning-brief"}},
            {"type": "schedule_created", "data": {"taskId": "reconcile-sent"}},
        ])
        _check("reconcile-sent already registered -> applies False",
               is_reconcile_sent_missing(str(p)).get("applies") is False)


def main():
    tests = [
        test_always_applies_returns_true,
        test_reconcile_sent_missing_fresh_workspace,
        test_reconcile_sent_present_not_flagged,
        test_v344_detector_zero_when_no_commitments,
        test_v344_detector_zero_when_only_canonical,
        test_v344_detector_counts_flat_new,
        test_v344_detector_counts_legacy,
        test_v344_detector_counts_owner_person_id_variant,
        test_v344_detector_excludes_pending_review,
        test_v344_detector_excludes_resolved,
        test_v344_detector_mixed_shapes,
        test_v344_detector_handles_missing_file,
    ]
    for t in tests:
        t()
    print(f"\nOK {len(tests)} release-detector tests passed")


if __name__ == "__main__":
    main()
