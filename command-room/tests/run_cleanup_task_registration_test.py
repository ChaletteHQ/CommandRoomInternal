#!/usr/bin/env python3
"""Wiring guard for the v3.18.2 cleanup-task registration gate (Bug #82).

ROOT CAUSE this guards against: `cleanup` is the v3.17.0 headline Sunday task,
but it is NOT in ORCHESTRATOR_MAP (it's not a chat-orchestrator) and registers
via the separate prose Step 1.D. Both scheduling skills' "are all tasks
registered?" idempotency checks enumerate only the 7 chat taskIds, so they are
structurally blind to cleanup: an existing customer (clients ~v3.14.4) who
re-runs `set up command room schedules` (or `update my command room`) is told
"all current" and routes to the management early-exit — Step 1.D is never
reached and cleanup never registers.

The fix is a hard, UNCONDITIONAL assertion in BOTH skills:
  - enable-command-room-schedules: a pre-Phase-6 gate (Phase 5.9) that checks
    `cleanup` is registered before any "already configured" early-exit.
  - command-room-update-bridge: a cleanup generic-add path mirroring the
    friday-wrap precedent, driven by the v3_18_2_cleanup_missing detector.

This guard makes the regression impossible to ship again: if either skill drops
its cleanup assertion, this test fails.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
ENABLE = os.path.join(PLUGIN_ROOT, "skills", "enable-command-room-schedules", "SKILL.md")
BRIDGE = os.path.join(PLUGIN_ROOT, "skills", "command-room-update-bridge", "SKILL.md")
DETECTOR = os.path.join(PLUGIN_ROOT, "shared", "scripts", "release_detectors", "v3_18_2_cleanup_missing.py")

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def main():
    print("=== v3.18.2 cleanup-task registration gate (Bug #82) ===\n")

    enable = open(ENABLE, encoding="utf-8").read() if os.path.isfile(ENABLE) else ""
    bridge = open(BRIDGE, encoding="utf-8").read() if os.path.isfile(BRIDGE) else ""

    # --- enable-command-room-schedules: pre-Phase-6 unconditional gate ---
    print("[1] enable-command-room-schedules asserts cleanup before the Phase 6 early-exit")
    check("SKILL.md present", bool(enable), f"missing {ENABLE}")
    # The gate must live BEFORE Phase 6 in file order.
    gate_idx = enable.find("Cleanup-registration assertion")
    phase6_idx = enable.find("## Phase 6")
    check(
        "a 'Cleanup-registration assertion' gate exists",
        gate_idx != -1,
        "no cleanup-registration assertion section found in enable-command-room-schedules",
    )
    check(
        "the gate is positioned before Phase 6 (early-exit can't skip it)",
        gate_idx != -1 and phase6_idx != -1 and gate_idx < phase6_idx,
        f"gate_idx={gate_idx} phase6_idx={phase6_idx} — gate must precede Phase 6",
    )
    check(
        "the gate is UNCONDITIONAL (runs on the re-run / already-configured path)",
        "UNCONDITIONAL" in enable or "unconditional" in enable,
        "gate must state it runs on every invocation, including the re-run early-exit",
    )
    check(
        "the gate checks the literal 'cleanup' taskId and falls back to Step 1.D",
        '"cleanup"' in enable and "Step 1.D" in enable and "list_scheduled_tasks" in enable,
        "gate must list_scheduled_tasks, check taskId 'cleanup', and run Step 1.D when absent",
    )
    print()

    # --- command-room-update-bridge: cleanup generic-add path ---
    print("[2] command-room-update-bridge has a cleanup generic-add path")
    check("SKILL.md present", bool(bridge), f"missing {BRIDGE}")
    check(
        "bridge references the v3_18_2_cleanup_missing detector",
        "v3_18_2_cleanup_missing" in bridge and "is_cleanup_missing" in bridge,
        "Phase 4.7 must drive the cleanup add via the release detector (mirror of friday-wrap)",
    )
    check(
        "bridge registers cleanup with the canonical Sunday cron",
        "0 18 * * 0" in bridge and "cleanup" in bridge,
        "cleanup add path must use cron 0 18 * * 0",
    )
    check(
        "bridge add path is silent (no question) per Rule 28",
        "Bug #82" in bridge and "Rule 28" in bridge,
        "the cleanup add path must be the silent-add shape (CONTRACT Rule 28), tagged Bug #82",
    )
    print()

    # --- detector module exists and exposes the entrypoint ---
    print("[3] v3_18_2_cleanup_missing detector module exists")
    det = open(DETECTOR, encoding="utf-8").read() if os.path.isfile(DETECTOR) else ""
    check("detector file present", bool(det), f"missing {DETECTOR}")
    check(
        "detector exposes is_cleanup_missing",
        "def is_cleanup_missing" in det,
        "the detector must define is_cleanup_missing(events_jsonl_path)",
    )
    print()

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
