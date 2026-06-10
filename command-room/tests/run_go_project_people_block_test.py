#!/usr/bin/env python3
"""Wiring guard for the v3.18.2 `go [project]` People live-block surfacing (Bug #86).

ROOT CAUSE this guards against: `go [project]` runs render_thread_live_state.py,
which writes the People block (including the actionable "Proposed — confirm to
add" line) into PROJECT_BRAIN — but the v3.18.1 first-response shape never
surfaced that block to the CEO. The confirm-people workflow dead-ended:
proposed people were written to the brain but the CEO never saw them, so they
could never be confirmed.

The fix adds a People block to the required first-response shape AND a mandatory
read-back-and-surface instruction in the Live State refresh section. This guard
asserts both survive.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
WM = os.path.join(PLUGIN_ROOT, "skills", "workspace-manager", "SKILL.md")

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
    print("=== v3.18.2 go-project People live-block surfacing (Bug #86) ===\n")

    body = open(WM, encoding="utf-8").read() if os.path.isfile(WM) else ""
    check("workspace-manager SKILL.md present", bool(body), f"missing {WM}")

    # Isolate the "go [name]" section (from its heading to the next ### heading).
    start = body.find('### "go [name]"')
    end = body.find('### "new project', start) if start != -1 else -1
    section = body[start:end] if (start != -1 and end != -1) else ""
    check("'go [name]' section located", bool(section), "could not isolate the go-project section")

    print("\n[1] the first-response shape includes a People block")
    check(
        "People block is in the required response shape",
        "People:" in section,
        "the REQUIRED first-response shape must include a People: block",
    )
    check(
        "the response shape carries the 'Proposed — confirm to add' line",
        "Proposed — confirm to add" in section,
        "the confirm-gate handle must appear in the response shape",
    )

    print("\n[2] the Live State refresh has a mandatory read-back-and-surface gate")
    check(
        "Bug #86 surfacing gate is documented",
        "Bug #86" in section,
        "the Live State refresh section must carry the Bug #86 surfacing mandate",
    )
    check(
        "instructs reading back the LIVE-STATE:people region from the brain",
        "<!-- LIVE-STATE:people -->" in section and ("READ BACK" in section or "read back" in section.lower()),
        "must read back the rendered <!-- LIVE-STATE:people --> region rather than re-derive it",
    )
    check(
        "the Proposed line is marked mandatory when present",
        "MUST appear" in section or "REQUIRED line" in section,
        "the 'Proposed — confirm to add' line must be marked mandatory whenever the renderer produced one",
    )

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
