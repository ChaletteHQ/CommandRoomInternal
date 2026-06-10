#!/usr/bin/env python3
"""
Wiring guard for command-room-update-bridge Phase 4.4 corruption heal.

WHY THIS EXISTS
The Phase 4.4 heal calls recover_corruption.run_recovery_if_needed. The
helper's DEFAULT mode (recurring=False) short-circuits permanently after the
first recovery at RECOVERY_VERSION — and most existing workspaces already ran
that recovery, so a default call is a silent permanent no-op that never heals
new corruption. The phase MUST pass recurring=True so it heals whatever
malformed lines exist at update time (no-op when clean).

This was shipped wrong once (v3.18.0, caught 2026-05-31 during real-workspace
validation: the workspace was already recovered, so the one-time call would
never fire). This guard makes the regression impossible to ship again: if the
bridge calls run_recovery_if_needed at all, it must pass recurring=True.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
BRIDGE = PLUGIN_ROOT / "skills" / "command-room-update-bridge" / "SKILL.md"


def main() -> int:
    if not BRIDGE.exists():
        print(f"FAIL — update-bridge SKILL.md not found at {BRIDGE}")
        return 1

    text = BRIDGE.read_text(encoding="utf-8", errors="replace")

    # Find every run_recovery_if_needed( ... ) call, tolerant of newlines.
    calls = re.findall(r"run_recovery_if_needed\s*\((.*?)\)", text, flags=re.DOTALL)

    if not calls:
        print("FAIL — update-bridge no longer calls run_recovery_if_needed at all.")
        print("       Phase 4.4 (on-update corruption heal) appears to have been dropped.")
        return 1

    # Accept `recurring=True` and `recurring = True` (whitespace-tolerant).
    bad = [c for c in calls if not re.search(r"recurring\s*=\s*True", c)]

    if bad:
        print(f"FAIL — {len(bad)} run_recovery_if_needed call(s) in update-bridge "
              "do NOT pass recurring=True:")
        for c in bad:
            print(f"  call args: {c.strip()[:160]}")
        print()
        print("The default one-time mode short-circuits permanently after the first")
        print("RECOVERY_VERSION recovery — a silent no-op on already-recovered workspaces.")
        print("Phase 4.4 must pass recurring=True to heal current corruption on update.")
        return 1

    print(f"OK — all {len(calls)} run_recovery_if_needed call(s) in update-bridge "
          "pass recurring=True (Phase 4.4 heals current corruption on update).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
