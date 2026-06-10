#!/usr/bin/env python3
"""Wiring guard for the v3.18.4 artifact liveness detection (Bug #88).

ROOT CAUSE this guards against: command-room-update-bridge Phase 1 built its
`installed_artifact_set` purely from `artifact_installed` events, then computed
`missing_defaults = CURRENT_DEFAULTS - installed_artifact_set`. So a stale event
marker (artifact removed, or logged-but-not-persisted) made the bridge report an
artifact "already installed" when it wasn't live — the v3.18.1 Quick Commands
false positive.

The fix reconciles the event-derived set against the LIVE sidebar via
`mcp__cowork__list_artifacts` (presence-by-id is enforceable even though byte
content is not). An artifact counts as installed only if its id is in the live
list; a stale marker is dropped into missing_defaults and (idempotently)
reinstalled. This guard asserts that reconciliation survives in the skill.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
BRIDGE = os.path.join(PLUGIN_ROOT, "skills", "command-room-update-bridge", "SKILL.md")

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
    print("=== v3.18.4 artifact liveness detection (Bug #88) ===\n")

    body = open(BRIDGE, encoding="utf-8").read() if os.path.isfile(BRIDGE) else ""
    check("update-bridge SKILL.md present", bool(body), f"missing {BRIDGE}")

    # Isolate Phase 1 step 3 (Installed artifact set) through the next phase.
    s = body.find("3. **Installed artifact set")
    e = body.find("\n## Phase", s) if s != -1 else -1
    sec = body[s:e] if (s != -1 and e != -1) else body

    check(
        "tagged as the Bug #88 liveness fix",
        "Bug #88" in sec,
        "Phase 1 step 3 must carry the Bug #88 liveness-reverification block",
    )
    check(
        "reconciles against the live sidebar via list_artifacts",
        "mcp__cowork__list_artifacts" in sec,
        "must verify presence against mcp__cowork__list_artifacts, not the event log alone",
    )
    check(
        "drops stale markers into missing_defaults for idempotent reinstall",
        "missing_defaults" in sec and ("stale marker" in sec or "stale" in sec),
        "an artifact_installed event absent from the live list must be treated as missing",
    )
    check(
        "explicitly forbids 'already installed' from the event log alone",
        "from the event log alone" in sec or "event marker alone" in sec,
        "must forbid reporting already-installed purely from artifact_installed events",
    )
    check(
        "has a graceful fallback when list_artifacts is unavailable",
        "list_artifacts" in sec and ("unavailable" in sec or "couldn't confirm" in sec),
        "must degrade gracefully (and say so) when the live list can't be read",
    )

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
