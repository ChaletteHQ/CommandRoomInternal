#!/usr/bin/env python3
"""
v4.6.1 S3 regression — the morning brief's light watchdog pass (the R3
discovery: system-health's docstring promised "the same watchdog rides
the morning brief (light daily pass)" while orchestrator-morning-brief
called NOTHING — the brief inherited no watchdog because it invoked no
watchdog).

Guards:
  1. brief_watchdog_line renders SOLELY from health_verdict's partition
     (verdict injection — no second scan, no other inputs).
  2. problems > 0 → the one-line count + "health check" pointer; NO
     per-task detail (no task ids, no display names, no causes).
  3. problems == 0 → None (never pad an all-clear into the brief).
  4. cloud vantage → None (quiet beats a false alarm from a chat that
     can't see the scheduler).
  5. Wired end-to-end on a real (empty) workspace without raising.
  6. Prose guard: the orchestrator carries the Step 4b call.

Run via: python3 tests/run_brief_watchdog_line_test.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from task_watchdog import brief_watchdog_line  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")


def verdict(problems, vantage=None):
    """A health_verdict-shaped dict — the ONLY thing the line may read."""
    return {
        "vantage": vantage,
        "reports": [],
        "on_schedule": ["morning-brief"],
        "caught_up": [],
        "first_run_pending": [],
        "problems": problems,
        "summary_line": "…",
        "lines": [f"Your {t} task stopped firing." for t in problems],
        "info_lines": [],
    }


def main() -> int:
    ws = tempfile.mkdtemp(prefix="s3_watchdog_")

    # ------------------------------------------------------------------
    print("[1] renders from health_verdict only — verdict injection")
    # ------------------------------------------------------------------
    line = brief_watchdog_line(ws, verdict=verdict(["pulse", "session-sweep"]))
    check("two problems → the docstring's promised line",
          line == ("2 of your background tasks need attention — "
                   "say health check for the detail."), repr(line))
    check("no per-task detail leaks into the line",
          "pulse" not in line and "session-sweep" not in line
          and "stopped firing" not in line)
    line1 = brief_watchdog_line(ws, verdict=verdict(["pulse"]))
    check("singular reads naturally",
          line1 == ("1 of your background tasks needs attention — "
                    "say health check for the detail."), repr(line1))

    # ------------------------------------------------------------------
    print("[2] quiet cases — all healthy, and cloud vantage")
    # ------------------------------------------------------------------
    check("zero problems → None (never pad an all-clear)",
          brief_watchdog_line(ws, verdict=verdict([])) is None)
    v = verdict([], vantage={"finding": "registry_not_visible",
                             "line": "I can't see your scheduler from this chat."})
    check("cloud vantage → None (quiet beats a false alarm)",
          brief_watchdog_line(ws, verdict=v) is None)
    v2 = verdict(["pulse"], vantage={"finding": "registry_not_visible",
                                     "line": "…"})
    check("vantage wins even when stale problems ride the dict",
          brief_watchdog_line(ws, verdict=v2) is None)

    # ------------------------------------------------------------------
    print("[3] end-to-end — a real workspace path, one call, no raise")
    # ------------------------------------------------------------------
    try:
        out = brief_watchdog_line(ws)
        check("fresh empty workspace runs the full verdict path",
              out is None or isinstance(out, str), repr(out))
    except Exception as e:  # noqa: BLE001
        check("fresh empty workspace runs the full verdict path", False, repr(e))

    # ------------------------------------------------------------------
    print("[4] prose guard — the orchestrator actually calls it")
    # ------------------------------------------------------------------
    orch = (ROOT / "skills/enable-command-room-schedules/references/"
                   "orchestrator-morning-brief.md").read_text(encoding="utf-8")
    check("orchestrator imports brief_watchdog_line",
          "brief_watchdog_line" in orch)
    check("orchestrator says the None case renders nothing",
          "never pad an all-clear" in orch)
    sh = (ROOT / "skills/system-health/SKILL.md").read_text(encoding="utf-8")
    check("system-health's docstring promise still names the daily pass",
          "light daily pass" in sh)

    # ------------------------------------------------------------------
    print(f"\n=== Summary: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        print("FAIL — brief watchdog line regressed")
        return 1
    print("OK — the light daily pass exists and stays light")
    return 0


if __name__ == "__main__":
    sys.exit(main())
