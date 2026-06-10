#!/usr/bin/env python3
"""Unit test for the v3.18.2 cleanup_missing detector (Bug #82).

The `cleanup` Sunday self-maintenance task is registered separately from the 7
chat orchestrators (via enable-command-room-schedules Step 1.D) and is absent
from ORCHESTRATOR_MAP, so the chat-task completeness checks in both
enable-command-room-schedules and command-room-update-bridge are structurally
blind to it. Every pre-v3.17.0 upgrader silently never got it.

This detector is the update-bridge silent-add gate. It must:
  - return applies=True  when the workspace is established (has schedule_created
    events) but has NO cleanup schedule_created event;
  - return applies=False when cleanup IS registered;
  - return applies=False on a fresh workspace (no events / no schedule_created)
    — the first-install path registers cleanup, no gap to surface.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts", "release_detectors"))

import v3_18_2_cleanup_missing as det  # noqa: E402

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


def _write_events(lines):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for ev in lines:
            f.write(json.dumps(ev) + "\n")
    return path


def main():
    print("=== v3.18.2 cleanup_missing detector ===\n")

    SCHED = lambda tid: {"type": "schedule_created", "ts": "2026-05-31T00:00:00", "data": {"taskId": tid}}

    # 1. Established workspace, chats registered, NO cleanup → applies=True
    p = _write_events([SCHED("morning-brief"), SCHED("inbox"), SCHED("friday-wrap")])
    res = det.is_cleanup_missing(p)
    check("established workspace without cleanup → applies=True", res.get("applies") is True, res)
    os.unlink(p)

    # 2. Cleanup IS registered → applies=False
    p = _write_events([SCHED("morning-brief"), SCHED("cleanup")])
    res = det.is_cleanup_missing(p)
    check("cleanup already registered → applies=False", res.get("applies") is False, res)
    os.unlink(p)

    # 3. No schedule_created events at all → applies=False (fresh-ish; first-install owns it)
    p = _write_events([{"type": "interaction", "ts": "2026-05-31T00:00:00", "data": {}}])
    res = det.is_cleanup_missing(p)
    check("no schedule events → applies=False", res.get("applies") is False, res)
    os.unlink(p)

    # 4. Nonexistent events file → applies=False (fresh workspace)
    res = det.is_cleanup_missing(os.path.join(tempfile.gettempdir(), "does-not-exist-cr.jsonl"))
    check("missing events file → applies=False", res.get("applies") is False, res)

    # 5. Cleanup persists in append-only log even if disabled later → still applies=False
    #    (deliberate removal is protected — the schedule_created event remains)
    p = _write_events([SCHED("cleanup"), {"type": "schedule_disabled", "ts": "x", "data": {"taskId": "cleanup"}}])
    res = det.is_cleanup_missing(p)
    check("cleanup once-registered then disabled → applies=False (no force re-add)", res.get("applies") is False, res)
    os.unlink(p)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
