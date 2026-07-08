#!/usr/bin/env python3
"""SPEC A8 — output-skill regression aggregator (developer convenience).

Subprocess-runs the fixture validator + all six output exercises and lints the six
eval-prompt JSON files, printing one summary table. EXCLUDED from run_all.py discovery
(listed in NOT_A_SUITE) — the six exercises already run individually in the runtime tier
with per-skill granularity; this is the one-shot developer loop.

Regenerate every golden:  CR_UPDATE_GOLDENS=1 python tests/run_output_regression.py
A golden churned by an intentional brief_writer / contract change? Review the .txt diff
in the SAME commit — owner is whoever shipped the change.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PY = sys.executable

SUITES = [
    "run_fixture_workspace_mini_test.py",
    "run_runtime_exercise_output_memo_writer_test.py",
    "run_runtime_exercise_output_call_prep_test.py",
    "run_runtime_exercise_output_follow_up_ritual_test.py",
    "run_runtime_exercise_output_board_pack_test.py",
    "run_runtime_exercise_output_email_writer_test.py",
    "run_runtime_exercise_output_morning_briefing_test.py",
]

EVAL_FILES = [
    "eval_prompts_memo_writer.json",
    "eval_prompts_call_prep.json",
    "eval_prompts_follow_up_ritual.json",
    "eval_prompts_board_pack.json",
    "eval_prompts_email_writer.json",
    "eval_prompts_morning_briefing.json",
]


def _run_suite(name: str) -> tuple[bool, str]:
    env = dict(os.environ)  # forwards CR_UPDATE_GOLDENS into the exercises
    proc = subprocess.run([PY, str(TESTS / name)], cwd=str(TESTS.parent),
                          capture_output=True, text=True, timeout=300, env=env)
    last = ""
    for line in reversed(proc.stdout.strip().splitlines()):
        if line.strip():
            last = line.strip()
            break
    return proc.returncode == 0, last or f"exit {proc.returncode}"


def _lint_eval(name: str) -> tuple[bool, str]:
    try:
        d = json.loads((TESTS / name).read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"parse error: {e}"
    if not d.get("skill") or not isinstance(d.get("prompts"), list):
        return False, "missing skill / prompts"
    kinds = [p.get("kind") for p in d["prompts"]]
    pos = kinds.count("positive_routing")
    neg = kinds.count("negative_routing")
    beh = kinds.count("behavior")
    if pos >= 1 and neg >= 1 and beh >= 3:
        return True, f"{pos} pos / {neg} neg / {beh} behavior"
    return False, f"needs >=1 pos, >=1 neg, >=3 behavior (got {pos}/{neg}/{beh})"


def main() -> int:
    update = os.environ.get("CR_UPDATE_GOLDENS") == "1"
    print(f"\nOutput-skill regression{'  [UPDATING GOLDENS]' if update else ''}\n" + "=" * 60)
    fails = 0

    print("\nSUITES")
    for name in SUITES:
        ok, summary = _run_suite(name)
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<52} {summary}")
        fails += 0 if ok else 1

    print("\nEVAL-PROMPT LINT")
    for name in EVAL_FILES:
        ok, summary = _lint_eval(name)
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<40} {summary}")
        fails += 0 if ok else 1

    print("\n" + "=" * 60)
    if fails:
        print(f"FAIL — {fails} check(s) failed. If a golden churned on an intentional "
              f"render change, run CR_UPDATE_GOLDENS=1 and review the .txt diff.")
        return 1
    print("ALL output-regression suites + eval lints PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
