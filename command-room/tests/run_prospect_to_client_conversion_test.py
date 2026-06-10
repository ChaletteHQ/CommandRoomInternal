#!/usr/bin/env python3
"""Wiring guard for the prospect -> client conversion handler (Bug #91).

ROOT CAUSE this guards against: the `new prospect` flow OFFERED "say `[Name] is
now a client` and I'll convert the prospect to a real project" — but the
conversion was never implemented (no handler, not even a trigger). It was a
dead promise: a prospect that actually closed stayed stuck at
relationship_type=prospect with stale notes and no engagement edge.

The fix adds a real conversion handler in workspace-manager that mutates the
EXISTING org through the typed writers (org_writer.update_org to flip
relationship_type, engagement_writer to create/update the edge), reuses the
org_id (no duplicate), and writes no `stage` field. This guard asserts the
handler + its triggers survive, and that the offer is no longer a dead-end.
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
    print("=== prospect -> client conversion handler (Bug #91) ===\n")

    body = open(WM, encoding="utf-8").read() if os.path.isfile(WM) else ""
    check("workspace-manager SKILL.md present", bool(body), f"missing {WM}")

    # Isolate the conversion handler section.
    start = body.find('### "[Name] is now a client"')
    end = body.find('### "new org', start) if start != -1 else -1
    section = body[start:end] if (start != -1 and end != -1) else ""

    print("[1] the conversion handler exists (no longer a dead promise)")
    check("handler section present + tagged Bug #91", bool(section) and "Bug #91" in section,
          "the '[Name] is now a client' conversion handler must exist")

    print("\n[2] the trigger is wired in the frontmatter (skill actually fires on it)")
    fm = body[:body.find("---", 5)] if body.startswith("---") else body[:4000]
    check("'is now a client' is a registered trigger",
          "is now a client" in fm,
          "the conversion phrase must be in the frontmatter triggers, not just offered in prose")

    print("\n[3] conversion goes through the typed writers (HARD gate)")
    check("flips relationship_type via org_writer.update_org",
          "org_writer.update_org" in section and "relationship_type='client'" in section,
          "must flip relationship_type to client through the typed writer")
    check("creates/updates the engagement edge via engagement_writer",
          "engagement_writer" in section and ("create_engagement" in section or "update_engagement" in section),
          "must create or update the engagement edge")
    check("prints the CONVERTED proof line",
          "CONVERTED" in section,
          "the block must print a CONVERTED proof line (org_updated + engagement event)")

    print("\n[4] anti-improvisation guardrails")
    check("forbids a stage field + asserts its absence",
          "'stage' not in org" in section,
          "must assert no stage field is written on the org")
    check("warns that `new client` is the WRONG tool (would duplicate)",
          "new client" in section and ("WRONG tool" in section or "duplicate" in section.lower()),
          "must steer away from new-client-create (which duplicates) toward mutating the existing org")
    check("reuses the existing org_id for the project scaffold (no second org)",
          "reuse" in section.lower() and "org_id" in section,
          "the optional project scaffold must reuse org_id, not create a duplicate org")

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
