#!/usr/bin/env python3
"""Unit test for the prospect-conversion detect-and-nudge (Bug #92).

Asserts the detector flags prospects that look converted (and ONLY those):
  - prospect with an active client engagement → HIGH
  - prospect with an active affiliated project → HIGH
  - prospect with recent signing language in events → MEDIUM
  - prospect with no signal → not flagged
  - a non-prospect (already client) → never flagged
And that it NEVER mutates anything (detect-and-suggest, not auto-flip).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import prospect_conversion_detector as pcd  # noqa: E402

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


def _ws(entities, events):
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "_hq", "data")
    os.makedirs(data)
    with open(os.path.join(data, "entities.json"), "w", encoding="utf-8") as f:
        json.dump({"entities": entities}, f)
    with open(os.path.join(data, "events.jsonl"), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return tmp


def main():
    print("=== prospect-conversion detect-and-nudge (Bug #92) ===\n")

    entities = {
        "orgs": [
            {"id": "org_001", "canonical_name": "MyCo", "relationship_type": "operating", "is_primary_focus": True},
            {"id": "org_A", "canonical_name": "Alpha", "relationship_type": "prospect"},   # active client engagement
            {"id": "org_B", "canonical_name": "Bravo", "relationship_type": "prospect"},   # active project
            {"id": "org_C", "canonical_name": "Charlie", "relationship_type": "prospect"}, # signing language
            {"id": "org_D", "canonical_name": "Delta", "relationship_type": "prospect"},   # nothing
            {"id": "org_E", "canonical_name": "Echo", "relationship_type": "client"},      # already client
        ],
        "engagements": [
            {"id": "engagement_001", "from_org_id": "org_001", "to_org_id": "org_A", "kind": "client", "is_active": True},
        ],
        "threads": [
            {"id": "project_b", "display_name": "Bravo Build", "org": "org_B", "status": "active"},
        ],
        "people": [],
    }
    events = [
        {"seq": 1, "type": "meeting", "ts": "2026-05-30T00:00:00", "org_ids": ["org_C"],
         "data": {"summary": "Great call — they signed the engagement agreement today."}},
        # Delta only has pursuit-phase language → must NOT flag.
        {"seq": 2, "type": "interaction", "ts": "2026-05-30T00:00:00", "org_ids": ["org_D"],
         "data": {"summary": "Sent the proposal, still interviewing other vendors."}},
    ]

    tmp = _ws(entities, events)
    before = open(os.path.join(tmp, "_hq", "data", "entities.json"), encoding="utf-8").read()
    cands = pcd.detect_prospect_conversion_candidates(tmp)
    after = open(os.path.join(tmp, "_hq", "data", "entities.json"), encoding="utf-8").read()
    by_id = {c["org_id"]: c for c in cands}

    print("[1] structural HIGH-confidence signals")
    check("Alpha flagged HIGH (active client engagement)",
          by_id.get("org_A", {}).get("confidence") == "high", by_id.get("org_A"))
    check("Bravo flagged HIGH (active project)",
          by_id.get("org_B", {}).get("confidence") == "high", by_id.get("org_B"))

    print("\n[2] textual MEDIUM-confidence signal")
    check("Charlie flagged MEDIUM (signing language)",
          by_id.get("org_C", {}).get("confidence") == "medium", by_id.get("org_C"))

    print("\n[3] no false positives")
    check("Delta NOT flagged (pursuit-phase language only)", "org_D" not in by_id, by_id.get("org_D"))
    check("Echo NOT flagged (already a client, not a prospect)", "org_E" not in by_id)

    print("\n[4] suggests the Bug #91 conversion command, does NOT auto-flip")
    check("suggested_command is the conversion phrase",
          by_id.get("org_A", {}).get("suggested_command") == "Alpha is now a client",
          by_id.get("org_A"))
    check("detector did NOT mutate entities.json (suggest, never flip)",
          before == after, "entities.json changed — the detector must be read-only")

    print("\n[6] render_line — verbatim nudge line per candidate (Bug #92b)")
    # The surface must render a detector-owned line, not synthesize its own
    # (that synthesis was the discretion point where the brief dropped a true
    # candidate). Every qualifying candidate carries render_line; format stable.
    for oid in ("org_A", "org_B", "org_C"):
        c = by_id.get(oid, {})
        line = c.get("render_line", "")
        check(f"{oid} has a render_line",
              bool(line), c)
        check(f"{oid} render_line is verbatim-renderable (🔄 + name + reason + command)",
              line.startswith("🔄 ")
              and c.get("name", "\0") in line
              and c.get("reason", "\0") in line
              and f"`{c.get('name')} is now a client`" in line,
              line)
    # Every candidate the detector returns must carry a render_line — the surface
    # renders the FULL set verbatim, never a self-selected subset.
    check("every candidate carries a render_line (no subset rendering)",
          all(c.get("render_line") for c in cands), [c.get("name") for c in cands if not c.get("render_line")])

    print("\n[5] empty workspace is a safe no-op")
    tmp2 = _ws({"orgs": [{"id": "org_001", "canonical_name": "Solo", "relationship_type": "operating"}],
                "engagements": [], "threads": [], "people": []}, [])
    check("no prospects → no candidates", pcd.detect_prospect_conversion_candidates(tmp2) == [])

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
