#!/usr/bin/env python3
"""Test the v3.18.9 brain-rerender auto_apply pair (Bugs #87/#97) — the update
path that pushes the corrected people lists into client workspaces on update.

Proves end-to-end on a synthetic workspace:
  - detector flags a brain whose Live-State block is at an OLD logic version
    (and does NOT flag a current one, an archived thread, or a blockless brain);
  - the action re-renders the stale brain under the current logic — which also
    applies the #87 fix (the org-less contact disappears from the proposed line)
    and stamps the current logic_v;
  - it preserves durable content byte-for-byte;
  - it's idempotent (second run re-renders nothing; detector then applies=False).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import render_thread_live_state as rtls  # noqa: E402
import render_brain_block  # noqa: E402
from release_detectors.v3_18_9_brains_stale_logic import brains_stale_logic  # noqa: E402
from release_actions.v3_18_9_rerender_brains import rerender_brains  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


SUB = "project_050"
ORG = "org_050"


def _build(tmp):
    data = os.path.join(tmp, "_hq", "data")
    os.makedirs(data)
    entities = {
        "entities": {
            "threads": [
                {"id": SUB, "status": "active", "folder_name": "Proj", "org_id": ORG},
                {"id": "project_cur", "status": "active", "folder_name": "Cur", "org_id": ORG},
                {"id": "project_arch", "status": "archived", "folder_name": "Arch", "org_id": ORG},
            ],
            "orgs": [{"id": ORG}],
            "people": [
                {"id": "person_orgmate", "canonical_name": "Org Mate", "primary_org_id": ORG},
                {"id": "person_vendor", "canonical_name": "Stray Vendor"},  # NO org → #87 drops
            ],
        }
    }
    with open(os.path.join(data, "entities.json"), "w", encoding="utf-8") as f:
        json.dump(entities, f)
    events = [
        {"seq": 1, "ts": "2026-05-01T00:00:00", "type": "interaction",
         "primary_thread_id": SUB, "person_ids": ["person_orgmate"]},
        {"seq": 2, "ts": "2026-05-02T00:00:00", "type": "interaction",
         "primary_thread_id": SUB, "person_ids": ["person_vendor"]},
    ]
    with open(os.path.join(data, "events.jsonl"), "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    # Stale brain: a Live-State block written under the OLD logic (no logic_v),
    # carrying the pre-fix proposed line that still lists the org-less vendor.
    proj_dir = os.path.join(tmp, "Proj")
    os.makedirs(proj_dir)
    durable = "# Proj Brain\n\n## Durable notes\nKEEP THIS EXACTLY.\n\n## 1. People\n"
    stale_block = ("<!-- LIVE-STATE:people source_seq=1 -->\n"
                   "**Status:** active\n\n"
                   "*Proposed — confirm to add (inherited from a pre-split umbrella or low signal):* "
                   "Org Mate, Stray Vendor\n"
                   "<!-- /LIVE-STATE:people -->\n\n## 2. More\ntail\n")
    with open(os.path.join(proj_dir, "PROJECT_BRAIN.md"), "w", encoding="utf-8") as f:
        f.write(durable + stale_block)

    # Current brain: already stamped at the current logic version (no refresh).
    cur_dir = os.path.join(tmp, "Cur")
    os.makedirs(cur_dir)
    cur_block = (f"<!-- LIVE-STATE:people source_seq=1 logic_v={rtls.LIVE_STATE_LOGIC_VERSION} -->\n"
                 "**Status:** active\n<!-- /LIVE-STATE:people -->\n")
    with open(os.path.join(cur_dir, "PROJECT_BRAIN.md"), "w", encoding="utf-8") as f:
        f.write("# Cur\n\n## 1. People\n" + cur_block)
    return tmp


def main():
    print("=== v3.18.9 brain-rerender auto_apply (Bugs #87/#97) ===\n")
    tmp = _build(tempfile.mkdtemp(prefix="cr-rerender-"))
    ev = os.path.join(tmp, "_hq", "data", "events.jsonl")
    brain = os.path.join(tmp, "Proj", "PROJECT_BRAIN.md")

    print("[1] detector flags the stale-logic brain (and only it)")
    det = brains_stale_logic(ev)
    check("applies=True", det["applies"] is True, det)
    check("n_stale == 1 (current + archived NOT counted)", det["context"]["n_stale"] == 1, det)

    print("\n[2] action re-renders the stale brain under current logic")
    res = rerender_brains(ev, tmp, det.get("context", {}))
    check("success", res["success"] is True, res)
    check("ran=True", res["ran"] is True, res)
    check("n_rerendered == 1 (the current brain was a no-op)",
          res["context"]["n_rerendered"] == 1, res)

    after = open(brain, encoding="utf-8").read()
    meta = render_brain_block.read_block_meta(brain, "people")
    print("\n[3] the re-render stamps current logic_v AND applies the #87 fix")
    check("block now stamped logic_v=current",
          meta and meta.get("logic_v") == rtls.LIVE_STATE_LOGIC_VERSION, meta)
    check("org-mate still proposed", "Org Mate" in after, after)
    check("org-less vendor DROPPED by the #87 fix on re-render",
          "Stray Vendor" not in after, after)

    print("\n[4] durable content preserved byte-for-byte")
    check("durable header kept", "## Durable notes" in after and "KEEP THIS EXACTLY." in after)
    check("trailing durable kept", "## 2. More\ntail" in after, after)

    print("\n[5] idempotent — second run is a no-op, detector clears")
    res2 = rerender_brains(ev, tmp, {})
    check("second action ran=False (nothing stale left)", res2["ran"] is False, res2)
    det2 = brains_stale_logic(ev)
    check("detector now applies=False", det2["applies"] is False, det2)

    print("\n[6] graceful on a missing workspace")
    miss = brains_stale_logic("/nonexistent/_hq/data/events.jsonl")
    check("missing workspace -> applies False, no crash", miss["applies"] is False, miss)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
