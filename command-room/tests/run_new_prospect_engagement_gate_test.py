#!/usr/bin/env python3
"""Wiring guard for the v3.18.2 `new prospect` engagement gate (Bug #83).

ROOT CAUSE this guards against: workspace-manager's `new prospect` path is
documented to call org_writer.create_org + engagement_writer.create_engagement,
but the v3.18.1 runtime freelanced — it wrote an `org_added` event via a
`track-prospect`-style path, never created the engagement edge, never asked the
deal-status question, and stamped a non-schema `stage` field onto the org by
copying an existing prospect's shape. The prose was satisfiable by improvisation.

The fix turns the prose into a HARD gate: an exact runnable block that calls
both typed writers, resolves from_org_id = the primary-focus org, asserts no
`stage` field, and prints a PROSPECT_CREATED proof line. This guard asserts the
gate is present and that the anti-improvisation language survives.
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
    print("=== v3.18.2 new-prospect engagement gate (Bug #83) ===\n")

    body = open(WM, encoding="utf-8").read() if os.path.isfile(WM) else ""
    check("workspace-manager SKILL.md present", bool(body), f"missing {WM}")

    # Isolate the `new prospect` section so we assert on the right block.
    start = body.find('### "new prospect')
    end = body.find('### "new org', start) if start != -1 else -1
    section = body[start:end] if (start != -1 and end != -1) else ""
    check("'new prospect' section located", bool(section), "could not isolate the new-prospect section")

    print("\n[1] both typed writers are invoked in the gate")
    check(
        "calls org_writer.create_org",
        "org_writer" in section and "create_org" in section,
        "the gate must create the prospect org via org_writer.create_org",
    )
    check(
        "calls engagement_writer.create_engagement",
        "engagement_writer" in section and "create_engagement" in section,
        "the gate must create the engagement edge via engagement_writer.create_engagement",
    )

    print("\n[2] the gate is a hard runnable block, not satisfiable by improvisation")
    check(
        "labeled a HARD gate tied to Bug #83",
        "HARD gate" in section and "Bug #83" in section,
        "must call itself a hard gate (v3.18.2 / Bug #83)",
    )
    check(
        "prints the PROSPECT_CREATED proof line",
        "PROSPECT_CREATED" in section,
        "the block must print PROSPECT_CREATED proving both records landed",
    )
    check(
        "resolves from_org_id from the primary-focus org",
        "is_primary_focus" in section and "from_org_id" in section,
        "from_org_id must resolve the workspace's primary-focus org (not be guessed)",
    )

    print("\n[3] anti-improvisation guardrails are explicit")
    check(
        "forbids the track-prospect / org_added-only bypass",
        "track-prospect" in section or "org_added" in section,
        "must explicitly forbid the track-prospect / org_added-only path (the v3.18.1 failure)",
    )
    check(
        "forbids the non-schema `stage` field and asserts its absence",
        "stage" in section and "'stage' not in org" in section,
        "must forbid writing a `stage` field on the org and assert it's absent (Bug #83 evidence)",
    )
    check(
        "forbids copying an existing prospect's shape",
        "copy the shape" in section.lower() or "same shape" in section.lower(),
        "must forbid the 'same shape as [other prospect]' example-copying that caused the bug",
    )

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
