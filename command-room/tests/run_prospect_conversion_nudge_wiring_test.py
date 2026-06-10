#!/usr/bin/env python3
"""Wiring guard for the prospect-conversion detect-and-nudge (Bug #92).

The detector (run_prospect_conversion_detector_test covers its logic) only helps
if it's actually wired into a surface AND the surfaces are explicit that they
SUGGEST, never auto-flip. This guard asserts:
  - the detector module exists and exposes the entrypoint;
  - command-room-coach and cleanup both call it;
  - both surfaces are explicit about detect-and-nudge (no silent auto-flip);
  - both route to the Bug #91 conversion command.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
DETECTOR = os.path.join(PLUGIN_ROOT, "shared", "scripts", "prospect_conversion_detector.py")
COACH = os.path.join(PLUGIN_ROOT, "skills", "command-room-coach", "SKILL.md")
CLEANUP = os.path.join(PLUGIN_ROOT, "skills", "cleanup", "SKILL.md")
BRIEF = os.path.join(PLUGIN_ROOT, "skills", "morning-briefing", "SKILL.md")

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
    print("=== prospect-conversion nudge wiring (Bug #92) ===\n")

    det = open(DETECTOR, encoding="utf-8").read() if os.path.isfile(DETECTOR) else ""
    coach = open(COACH, encoding="utf-8").read() if os.path.isfile(COACH) else ""
    cleanup = open(CLEANUP, encoding="utf-8").read() if os.path.isfile(CLEANUP) else ""
    brief = open(BRIEF, encoding="utf-8").read() if os.path.isfile(BRIEF) else ""

    print("[1] detector module")
    check("detector file present", bool(det), f"missing {DETECTOR}")
    check("exposes detect_prospect_conversion_candidates",
          "def detect_prospect_conversion_candidates" in det)

    for label, body in (("coach", coach), ("cleanup", cleanup), ("morning-briefing", brief)):
        print(f"\n[{label}] wired + detect-and-nudge (never auto-flip)")
        check(f"{label} calls the detector",
              "prospect_conversion_detector" in body and "detect_prospect_conversion_candidates" in body,
              f"{label} must call the detector")
        check(f"{label} is tagged Bug #92",
              "Bug #92" in body,
              f"{label} must carry the Bug #92 detect-and-nudge marker")
        check(f"{label} routes to the Bug #91 conversion command",
              "is now a client" in body,
              f"{label} must suggest the `[Name] is now a client` conversion")
        check(f"{label} explicitly does NOT auto-flip relationship_type",
              ("NEVER auto-flip" in body or "never auto-flip" in body.lower()
               or "does NOT change" in body or "only suggest" in body or "only surfaces the suggestion" in body),
              f"{label} must state it suggests, never mutates relationship_type")

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
