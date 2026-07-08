#!/usr/bin/env python3
"""Regression guard for events.jsonl seq monotonicity (deep-audit 2026-05-29,
finding #7).

atomic_append_jsonl auto-stamps MISSING seqs and bumps the counter past
explicit seqs that are AHEAD — but pre-fix, an explicit seq that was STALE
(a value a caller peeked before a concurrent append overtook it) hit neither
branch and was written verbatim, producing a DUPLICATE seq that corrupts
supersedes_seq / source_event_seq / _commitment_id back-references. These
tests assert stale explicit seqs are reassigned, while fresh/missing seqs
behave as before.

stdlib only.
"""

from __future__ import annotations

# This rig tests the writer-lock/seq machinery BELOW the event gate with
# synthetic fixture types; the gate (strict on both entries as of Phase 4
# 2026-07-02) is covered by run_event_gate_test.py.
import os
os.environ["CR_EVENT_GATE"] = "0"

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from atomic_write import atomic_append_jsonl  # noqa: E402


def _events_file(seqs: list[int]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="cr-seq-mono-test-"))
    p = d / "events.jsonl"
    p.write_text(
        "".join(json.dumps({"type": "seed", "seq": s}) + "\n" for s in seqs),
        encoding="utf-8",
    )
    return p


def _read(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_stale_explicit_seq_reassigned() -> None:
    p = _events_file([1, 2, 3])
    atomic_append_jsonl(p, [{"type": "x", "seq": 2}])  # stale — collides with existing 2
    evs = _read(p)
    seqs = [e["seq"] for e in evs]
    assert len(seqs) == len(set(seqs)), f"duplicate seq written: {seqs}"
    assert evs[-1]["seq"] == 4, f"stale seq not reassigned to 4: got {evs[-1]['seq']}"
    print("PASS test_stale_explicit_seq_reassigned")


def test_fresh_explicit_seq_preserved() -> None:
    p = _events_file([1, 2, 3])
    atomic_append_jsonl(p, [{"type": "x", "seq": 10}])  # ahead of max — valid
    evs = _read(p)
    assert evs[-1]["seq"] == 10, f"fresh explicit seq should be preserved: got {evs[-1]['seq']}"
    print("PASS test_fresh_explicit_seq_preserved")


def test_missing_seq_autostamped() -> None:
    p = _events_file([1, 2, 3])
    atomic_append_jsonl(p, [{"type": "x"}])
    evs = _read(p)
    assert evs[-1]["seq"] == 4, f"missing seq should auto-stamp to 4: got {evs[-1]['seq']}"
    print("PASS test_missing_seq_autostamped")


def main() -> int:
    tests = [
        test_stale_explicit_seq_reassigned,
        test_fresh_explicit_seq_preserved,
        test_missing_seq_autostamped,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"=== {len(tests) - failed} passed, {failed} failed ===")
        return 1
    print(f"OK — all {len(tests)} seq-monotonicity tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
