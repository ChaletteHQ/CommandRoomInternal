#!/usr/bin/env python3
"""Regression guard for aggregate_pack_run_telemetry on real telemetry (v4.0.0 dogfood Bug 2).

Real pack_run events carry numeric telemetry keys PRESENT-but-None, so
`dict.get(k, 0)` never returns its default → `None + None` TypeError crashed
usage-report. They also spread duration across drifted field names
(duration_ms / duration_s / duration_sec / duration_seconds) where the `_s`
variants are SECONDS, not ms. Unit fixtures used well-formed ms-only telemetry.

stdlib only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from telemetry import aggregate_pack_run_telemetry, _duration_ms  # noqa: E402


def test_none_numerics_do_not_crash() -> None:
    events = [
        {"type": "pack_run", "data": {"kind": "inbox", "telemetry": {
            "prompt_tokens_est": None, "response_tokens_est": None,
            "connector_call_count": None, "duration_ms": None}}},
    ]
    r = aggregate_pack_run_telemetry(events)  # must not raise
    assert r["totals"]["tokens"] == 0, r
    assert r["totals"]["ms"] == 0, r
    assert r["totals"]["connector_calls"] == 0, r
    print("PASS test_none_numerics_do_not_crash")


def test_duration_field_name_drift_coalesced_with_units() -> None:
    # duration_s/sec/seconds are SECONDS → must convert ×1000 to ms.
    assert _duration_ms({"duration_ms": 3000}) == 3000
    assert _duration_ms({"duration_s": 12}) == 12000
    assert _duration_ms({"duration_sec": 2}) == 2000
    assert _duration_ms({"duration_seconds": 1}) == 1000
    assert _duration_ms({"duration_ms": None, "duration_s": 5}) == 5000  # ms present-but-None → fall through to the seconds field
    assert _duration_ms({}) == 0
    print("PASS test_duration_field_name_drift_coalesced_with_units")


def test_correct_totals_mixed() -> None:
    events = [
        {"type": "pack_run", "data": {"kind": "x", "telemetry": {
            "prompt_tokens_est": None, "response_tokens_est": None, "duration_s": 12}}},
        {"type": "pack_run", "data": {"kind": "x", "telemetry": {
            "prompt_tokens_est": 100, "response_tokens_est": 50, "duration_ms": 3000}}},
    ]
    r = aggregate_pack_run_telemetry(events)
    assert r["totals"]["tokens"] == 150, r
    assert r["totals"]["ms"] == 15000, r  # 12s→12000 + 3000
    assert r["totals"]["fires"] == 2, r
    print("PASS test_correct_totals_mixed")


def main() -> int:
    test_none_numerics_do_not_crash()
    test_duration_field_name_drift_coalesced_with_units()
    test_correct_totals_mixed()
    print("ALL telemetry real-data tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
