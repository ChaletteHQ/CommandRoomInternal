#!/usr/bin/env python3
"""Dry-run/apply parity for attribute_person_to_org (Bug #100).

The org-attribution backfill's preview lied: its dry-run counted anyone with a
work-domain email as "WOULD attribute", but apply only attaches when that domain
matches an EXISTING org's domains — so the dry-run promised 11 and apply wrote 0.
The fix gives attribute_person_to_org a `dry_run=True` mode that runs the SAME
matcher without writing. This test locks the parity: whatever dry-run predicts
(attach to org X / no attach) is exactly what apply does, and dry-run writes
nothing.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from org_writer import attribute_person_to_org  # noqa: E402

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


def _ws():
    tmp = tempfile.mkdtemp(prefix="cr-attrib-")
    data = os.path.join(tmp, "_hq", "data")
    os.makedirs(data)
    entities = {
        "version": 1,
        "workspace": {},
        "orgs": [
            {"id": "org_acme", "canonical_name": "Acme Co", "domains": ["acme.com"],
             "relationship_type": "client"},
        ],
        "people": [
            # has a work-domain email that MATCHES an existing org
            {"id": "person_001", "canonical_name": "Match Person",
             "first_seen": "2026-05-01"},
            # has a work-domain email that matches NO org (the #100 case)
            {"id": "person_002", "canonical_name": "Nomatch Person",
             "first_seen": "2026-05-01"},
        ],
        "threads": [], "engagements": [],
    }
    with open(os.path.join(data, "entities.json"), "w", encoding="utf-8") as f:
        json.dump(entities, f)
    with open(os.path.join(data, "events.jsonl"), "w", encoding="utf-8") as f:
        f.write("")
    return tmp


def _entities(tmp):
    return json.load(open(os.path.join(tmp, "_hq", "data", "entities.json"), encoding="utf-8"))


def _primary_org(tmp, pid):
    for p in _entities(tmp).get("people", []):
        if p.get("id") == pid:
            return p.get("primary_org_id")
    return "<missing>"


def main():
    print("=== attribute_person_to_org dry-run/apply parity (Bug #100) ===\n")
    tmp = _ws()

    print("[1] dry-run predicts the real match (and the real NON-match)")
    dry_match, r1 = attribute_person_to_org(tmp, "person_001",
                                            work_domains=["acme.com"], dry_run=True)
    check("matching domain -> dry-run returns the org", dry_match and dry_match.get("id") == "org_acme", r1)
    dry_nomatch, r2 = attribute_person_to_org(tmp, "person_002",
                                              work_domains=["noorg.com"], dry_run=True)
    check("non-matching domain -> dry-run returns None (the #100 fix — no false promise)",
          dry_nomatch is None, r2)

    print("\n[2] dry-run writes NOTHING")
    before = json.dumps(_entities(tmp), sort_keys=True)
    attribute_person_to_org(tmp, "person_001", work_domains=["acme.com"], dry_run=True)
    attribute_person_to_org(tmp, "person_002", work_domains=["noorg.com"], dry_run=True)
    after = json.dumps(_entities(tmp), sort_keys=True)
    check("entities.json unchanged after dry-runs", before == after)
    check("person_001 still unattached after dry-run", _primary_org(tmp, "person_001") in (None, "<missing>"))

    print("\n[3] apply matches the dry-run prediction exactly")
    app_match, _ = attribute_person_to_org(tmp, "person_001", work_domains=["acme.com"])
    app_nomatch, _ = attribute_person_to_org(tmp, "person_002", work_domains=["noorg.com"])
    check("apply attaches the matching person (parity with dry-run)",
          app_match and app_match.get("id") == "org_acme")
    check("apply attaches person_001 in substrate", _primary_org(tmp, "person_001") == "org_acme")
    check("apply does NOT attach the non-matching person (parity with dry-run)",
          app_nomatch is None)
    check("non-matching person stays unattached in substrate",
          _primary_org(tmp, "person_002") in (None, "<missing>"))

    print("\n[4] the regression: a work-domain email is NOT enough — only a real org match counts")
    # This is exactly what fooled the old preview: work-domain present, but no org.
    pred, _ = attribute_person_to_org(tmp, "person_002",
                                      work_domains=["epicor.com", "companycam.com"], dry_run=True)
    check("multiple work-domains with no matching org -> still None",
          pred is None, "the old dry-run counted these as WOULD-attribute")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
