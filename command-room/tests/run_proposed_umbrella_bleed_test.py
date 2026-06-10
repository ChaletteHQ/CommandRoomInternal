#!/usr/bin/env python3
"""Regression test for the Proposed-confirm line org-association filter (Bug #87).

The Proposed line surfaces `low` + `inherited` roster candidates for the CEO to
confirm onto a thread. The umbrella-bleed bug: people who aren't really on this
project get proposed and (since the Bug #86 fix renders this line) reach the CEO.

**Why this test exists in its current shape (v3.18.9 #87 re-fix).** The first
cut of this test built its bleed person as `inherited` (events only on the parent
umbrella, n_direct=0). The v3.18.4 fix org-filtered the `inherited` tier, so the
test passed — but it never exercised the shape that actually bled on M's live
workspace: `low`-confidence contacts with a SINGLE direct event on the thread
(n_direct=1, e.g. a vendor/demo contact who showed up in one meeting) and NO org
affiliation. The old code returned `True` unconditionally for `low`, so those
sailed through. The fixture below is rebuilt from that real `derive_roster`
shape, covering BOTH tiers (`low` and `inherited`) crossed with org-associated /
org-less, so a fix that only handles one tier can no longer false-pass.

Confidence model (thread_roster.derive_roster): n_direct>=2 -> high (confirmed),
n_direct==1 -> low, n_direct==0 + umbrella event -> inherited.

The fix: a `low`/`inherited` candidate is proposed only if affiliated with THIS
thread's org. Org-associated candidates with light signal are kept; org-less
demo/vendor noise is dropped. A thread with no org_id can't discriminate, so it
keeps everything (lineage-safe direction).
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


SUB = "project_016"        # the sub-thread we render
UMBRELLA = "project_001"   # archived parent umbrella
SUB_ORG = "org_010"        # the sub-thread's own org
UMBRELLA_ORG = "org_001"   # the umbrella's org (a different org)


def _build_workspace(tmp):
    hq = os.path.join(tmp, "_hq", "data")
    os.makedirs(hq, exist_ok=True)
    entities = {
        "entities": {
            "threads": [
                {"id": UMBRELLA, "status": "archived", "folder_name": "Umbrella"},
                {"id": SUB, "status": "active", "folder_name": "SubThread",
                 "org_id": SUB_ORG, "parent_thread_id": UMBRELLA},
            ],
            "orgs": [{"id": SUB_ORG}, {"id": UMBRELLA_ORG}],
            "people": [
                # low + org-associated: 1 direct event on SUB, affiliated with the
                # sub-thread's org. The REAL keep case (Adan/Marvin analog).
                {"id": "person_low_org", "canonical_name": "Low OrgMate",
                 "primary_org_id": SUB_ORG},
                # low + NO org: 1 direct event on SUB, no org affiliation at all.
                # THE REAL BUG (Mehreen/Scott analog — vendor/demo contact). The
                # old code waved this straight through; the fix must drop it.
                {"id": "person_low_noorg", "canonical_name": "Low Vendor"},
                # inherited + org-associated: umbrella-only events, sub-thread org.
                # A genuine umbrella member of the right org → keep.
                {"id": "person_inh_org", "canonical_name": "Inherited OrgMate",
                 "primary_org_id": SUB_ORG},
                # inherited + wrong org: umbrella-only events, umbrella's org. The
                # v3.18.4-covered bleed — must STAY dropped (regression guard).
                {"id": "person_inh_noorg", "canonical_name": "Inherited Bleed",
                 "primary_org_id": UMBRELLA_ORG},
                # high: 2+ direct events on SUB → confirmed, not in the Proposed line.
                {"id": "person_high", "canonical_name": "Confirmed Member",
                 "primary_org_id": SUB_ORG},
            ],
        }
    }
    with open(os.path.join(hq, "entities.json"), "w", encoding="utf-8") as f:
        json.dump(entities, f)

    events = [
        # low people: exactly ONE direct event each on the sub-thread.
        {"seq": 1, "ts": "2026-05-03T00:00:00", "type": "interaction",
         "primary_thread_id": SUB, "person_ids": ["person_low_org"]},
        {"seq": 2, "ts": "2026-05-04T00:00:00", "type": "interaction",
         "primary_thread_id": SUB, "person_ids": ["person_low_noorg"]},
        # inherited people: events only on the umbrella (n_direct=0).
        {"seq": 3, "ts": "2026-05-01T00:00:00", "type": "interaction",
         "primary_thread_id": UMBRELLA, "person_ids": ["person_inh_org"]},
        {"seq": 4, "ts": "2026-05-02T00:00:00", "type": "interaction",
         "primary_thread_id": UMBRELLA, "person_ids": ["person_inh_noorg"]},
        # high person: two direct events on the sub-thread.
        {"seq": 5, "ts": "2026-05-05T00:00:00", "type": "interaction",
         "primary_thread_id": SUB, "person_ids": ["person_high"]},
        {"seq": 6, "ts": "2026-05-06T00:00:00", "type": "interaction",
         "primary_thread_id": SUB, "person_ids": ["person_high"]},
    ]
    with open(os.path.join(hq, "events.jsonl"), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return tmp


def _proposed_line(body: str) -> str:
    """The single 'Proposed — confirm to add …' line, or '' if absent."""
    return next((ln for ln in body.splitlines() if "Proposed — confirm to add" in ln), "")


def main():
    print("=== Proposed-set org-association filter (Bug #87 re-fix) ===\n")
    tmp = tempfile.mkdtemp()
    _build_workspace(tmp)

    body, _seq = rtls.format_live_state(tmp, SUB)
    proposed = _proposed_line(body)

    print("[1] low + org-associated is KEPT (light signal but a real org-mate)")
    check("Low OrgMate in Proposed line", "Low OrgMate" in proposed, proposed)

    print("\n[2] low + NO org is DROPPED — THE REAL BUG (vendor/demo bleed)")
    check("Low Vendor NOT in Proposed line", "Low Vendor" not in proposed, proposed)

    print("\n[3] inherited + org-associated is KEPT (genuine umbrella member)")
    check("Inherited OrgMate in Proposed line", "Inherited OrgMate" in proposed, proposed)

    print("\n[4] inherited + wrong org stays DROPPED (v3.18.4 regression guard)")
    check("Inherited Bleed NOT in Proposed line", "Inherited Bleed" not in proposed, proposed)

    print("\n[5] high-confidence member is confirmed, NOT in the Proposed line")
    check("Confirmed Member NOT in Proposed line", "Confirmed Member" not in proposed, proposed)
    check("Confirmed Member is in the rendered block (confirmed table)",
          "Confirmed Member" in body, body)

    print("\n[6] the Proposed line still renders (confirm-gate intact)")
    check("Proposed line present with the two survivors",
          bool(proposed) and "Low OrgMate" in proposed and "Inherited OrgMate" in proposed,
          proposed)

    print("\n[7] degenerate fallback — NO org_id on the thread keeps every candidate")
    # Without a thread org we can't discriminate; both org-less people reappear.
    hq = os.path.join(tmp, "_hq", "data")
    ent = json.load(open(os.path.join(hq, "entities.json"), encoding="utf-8"))
    for t in ent["entities"]["threads"]:
        if t["id"] == SUB:
            t.pop("org_id", None)
    with open(os.path.join(hq, "entities.json"), "w", encoding="utf-8") as f:
        json.dump(ent, f)
    body2, _ = rtls.format_live_state(tmp, SUB)
    proposed2 = _proposed_line(body2)
    check("without a thread org, low Vendor is NOT silently dropped",
          "Low Vendor" in proposed2, proposed2)
    check("without a thread org, inherited Bleed is NOT silently dropped",
          "Inherited Bleed" in proposed2, proposed2)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
