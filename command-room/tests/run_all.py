#!/usr/bin/env python3
"""
Command Room — full test battery runner (auto-discovering, tiered).

WHY THIS EXISTS
---------------
The plugin ships ~40+ test files under tests/. The old `run-command-room-tests`
skill hardcoded a list of FIVE of them and silently went stale at v2.14.x — new
test files (cru_match, backfill, event-contract, widget-transport, …) were never
run by the "run plugin tests" path. A hardcoded list is a staleness bug waiting
to happen.

This runner DISCOVERS every `run_*.py` / `test_*.py` in tests/ at call time, so a
new test file is picked up the moment it lands — no list to maintain. It groups
them into tiers, runs cheapest-and-most-structural first (fail fast), and prints
one consolidated pass/fail table.

TIERS (run in this order; --tier <name> runs just one)
  guard    structural / write-time invariants — privacy, jargon, retired skills,
           leak scan, entity-resolve enforcement, and the event-contract guard.
           These encode "this class of bug must never ship" and are the cheapest
           signal, so they run first.
  unit     pure-python unit suites — renderer, helpers, cru_match, backfill,
           writers, detectors, schedule config, etc. The bulk of the battery.
  runtime  code-path simulation against synthetic data (runtime_exercise_*,
           spec-example render). Catches the bug class that static unit tests
           structurally cannot (per feedback_static_analysis_vs_runtime_exercise).

Each test file is run as its own subprocess; a non-zero exit = fail (house
convention, see any run_*_test.py).

DEPENDENCIES: `pip install -r requirements.txt` (repo root) before running.
The battery is stdlib-only EXCEPT for pyyaml, tzdata and jsonschema. That claim
used to read "stdlib only, no external deps", which was untrue and cost real
debugging time: two suites responded to a missing dependency by printing SKIP
and exiting 0, so the battery reported green while those checks silently did not
run. Both now fail loudly. If you add a suite with a third-party import, declare
it in requirements.txt and make the absence loud.

USAGE
  python tests/run_all.py                 # full battery, all tiers
  python tests/run_all.py --tier guard    # just the structural guards
  python tests/run_all.py --tier unit
  python tests/run_all.py --tier runtime
  python tests/run_all.py --quiet         # summary only, no per-test lines

Exit code is non-zero if ANY test fails — safe to wire into a ship gate.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# Files in tests/ that are NOT themselves runnable test suites.
NOT_A_SUITE = {
    "run_all.py",
    # SPEC A8 — developer-loop aggregator; the six output exercises it wraps already
    # run individually in the runtime tier, so this would double-run them.
    "run_output_regression.py",
}

# Tier classification by filename. A file matches a tier if its name contains
# any of the tier's substrings. Order of TIERS defines run order. Anything that
# matches no guard/runtime pattern falls through to "unit".
GUARD_MARKERS = (
    "run_no_",                       # privacy / hardcoded-drive / md / retired / jargon
    "run_guard_",                    # G1-G10 ship-gate registry (tests/GUARDS.md)
    "run_pl_banned_words_test",      # G5 — landed PR 3, wired into the gate here
    "run_bootloader_size_gate_test", # G8 — landed Phase 3, wired into the gate here
    "run_event_contract_test",
    "run_customer_facing_voice_test",
    "run_personification_test",
    "run_docx_leak_scanner_test",
    "run_entity_resolve_enforcement_test",
    "run_ingest_substrate_sync_test",
    "run_source_of_truth_test",
)
RUNTIME_MARKERS = (
    "runtime_exercise",
    "run_spec_example_render_test",
)


def _tier_for(name: str) -> str:
    if any(m in name for m in GUARD_MARKERS):
        return "guard"
    if any(m in name for m in RUNTIME_MARKERS):
        return "runtime"
    return "unit"


def discover() -> dict[str, list[Path]]:
    """Return {tier: [paths]} for every test suite under tests/."""
    tiers: dict[str, list[Path]] = {"guard": [], "unit": [], "runtime": []}
    for path in sorted(TESTS_DIR.glob("*.py")):
        name = path.name
        if name in NOT_A_SUITE:
            continue
        if not (name.startswith("run_") or name.startswith("test_")):
            continue
        tiers[_tier_for(name)].append(path)
    return tiers


def _child_env() -> dict[str, str]:
    """Env for each suite subprocess, with UTF-8 pinned.

    Suites print ✓ and → as they report. Those glyphs have no cp1252 mapping, so
    on Windows — where cp1252 is the locale encoding and stdout here is a pipe,
    not a console — a suite dies with UnicodeEncodeError *while printing that a
    check passed*. Non-zero exit, so the runner scores it FAIL: the assertion was
    green and the announcement killed it. It presents as ~30 suites red at once
    with no real defect behind any of them.

    DEVELOPMENT.md and the CI job both set PYTHONUTF8/PYTHONIOENCODING, but a
    developer who types the documented `python tests/run_all.py` gets the red wall
    anyway. Same lesson as the dependency note above: a failure mode this
    misleading should be impossible to trigger, not documented. Setting it here
    makes the env vars belt-and-braces rather than load-bearing.

    Direct single-suite runs (`python tests/run_foo_test.py`) still need the env
    vars — this only covers suites spawned through the runner.
    """
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _last_meaningful_line(text: str) -> str:
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return line.strip()
    return ""


def run_suite(path: Path) -> tuple[bool, str, float]:
    """Run one test file as a subprocess. Return (passed, summary_line, seconds)."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(TESTS_DIR.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (>300s)", time.monotonic() - start
    elapsed = time.monotonic() - start
    passed = proc.returncode == 0
    summary = _last_meaningful_line(proc.stdout) or _last_meaningful_line(proc.stderr)
    if not passed and not summary:
        summary = f"exit {proc.returncode}"
    # On failure, keep a few lines of stderr/stdout for the report.
    if not passed:
        tail = _last_meaningful_line(proc.stderr) or summary
        summary = f"{summary}  |  {tail}" if tail and tail != summary else summary
    return passed, summary, elapsed


def main() -> int:
    # The runner echoes each suite's summary line, and those lines carry the same
    # ✓/→ glyphs. Pinning UTF-8 on the children alone would just move the
    # UnicodeEncodeError up here — the runner would die reporting a pass.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    args = sys.argv[1:]
    quiet = "--quiet" in args
    tier_filter = None
    if "--tier" in args:
        i = args.index("--tier")
        if i + 1 < len(args):
            tier_filter = args[i + 1]

    tiers = discover()
    tier_order = ["guard", "unit", "runtime"]
    if tier_filter:
        if tier_filter not in tier_order:
            print(f"unknown tier '{tier_filter}' — choose from: {', '.join(tier_order)}")
            return 2
        tier_order = [tier_filter]

    total_pass = total_fail = 0
    failures: list[tuple[str, str]] = []
    t0 = time.monotonic()

    for tier in tier_order:
        suites = tiers[tier]
        if not suites:
            continue
        print(f"\n=== TIER: {tier} ({len(suites)} suites) ===")
        for path in suites:
            passed, summary, elapsed = run_suite(path)
            if passed:
                total_pass += 1
                if not quiet:
                    print(f"  PASS  {path.name:<48} {elapsed:5.1f}s  {summary}")
            else:
                total_fail += 1
                failures.append((path.name, summary))
                print(f"  FAIL  {path.name:<48} {elapsed:5.1f}s  {summary}")
        # Fail fast at the guard tier — a structural-invariant break means the
        # build would ship a known bug class; no point burning the rest.
        if tier == "guard" and total_fail and not tier_filter:
            print("\nGuard tier failed — stopping before unit/runtime tiers.")
            break

    wall = time.monotonic() - t0
    print(f"\n{'='*64}")
    print(f"TOTAL: {total_pass} passed, {total_fail} failed  ({wall:.1f}s)")
    if failures:
        print("\nFailures:")
        for name, summary in failures:
            print(f"  - {name}: {summary}")
        print("\nBattery RED — do not ship.")
        return 1
    print("Battery GREEN — all suites pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
