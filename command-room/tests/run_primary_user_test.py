#!/usr/bin/env python3
"""Tests for primary_user.resolve_primary_user (Bug #102).

The resolver must find the CEO's person_id deterministically through a fallback
chain, because real workspaces were missing the is_primary_user flag entirely and
every consumer silently resolved to None.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from primary_user import resolve_primary_user, resolve_primary_user_from_entities  # noqa: E402

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


def ent(workspace=None, people=None):
    return {"workspace": workspace or {}, "people": people or []}


def main():
    print("=== resolve_primary_user (Bug #102) ===\n")

    print("[1] explicit pointer wins")
    e = ent({"user_person_id": "person_007", "user_first_name": "Morgan"},
            [{"id": "person_001", "canonical_name": "Morgan Reyes"},
             {"id": "person_007", "canonical_name": "Someone Else"}])
    check("workspace.user_person_id is authoritative",
          resolve_primary_user_from_entities(e) == "person_007")

    print("\n[2] is_primary_user / is_user flag")
    check("is_primary_user flag",
          resolve_primary_user_from_entities(
              ent({}, [{"id": "p1", "canonical_name": "A"},
                       {"id": "p2", "canonical_name": "B", "is_primary_user": True}])) == "p2")
    check("legacy is_user flag",
          resolve_primary_user_from_entities(
              ent({}, [{"id": "p2", "canonical_name": "B", "is_user": True}])) == "p2")

    print("\n[3] user_first_name fallback (the real-workspace case — no flag set)")
    e = ent({"user_first_name": "Morgan"},
            [{"id": "person_001", "canonical_name": "Morgan Reyes"},
             {"id": "person_002", "canonical_name": "Alex Kim"}])
    check("first-token match on canonical_name resolves the user",
          resolve_primary_user_from_entities(e) == "person_001")
    check("case-insensitive", resolve_primary_user_from_entities(
        ent({"user_first_name": "morgan"},
            [{"id": "person_001", "canonical_name": "Morgan Reyes"}])) == "person_001")

    print("\n[4] None when unresolvable (don't guess a random person)")
    check("no signal -> None",
          resolve_primary_user_from_entities(
              ent({}, [{"id": "p1", "canonical_name": "A"}])) is None)
    check("first-name with no matching person -> None",
          resolve_primary_user_from_entities(
              ent({"user_first_name": "Zoltan"},
                  [{"id": "p1", "canonical_name": "Morgan Reyes"}])) is None)

    print("\n[5] file-backed + shape-defensive")
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "_hq", "data")
    os.makedirs(data)
    # wrapped shape
    with open(os.path.join(data, "entities.json"), "w", encoding="utf-8") as f:
        json.dump({"entities": {"workspace": {"user_first_name": "Morgan"},
                                "people": [{"id": "person_001", "canonical_name": "Morgan Reyes"}]}}, f)
    check("resolves from disk (wrapped shape)", resolve_primary_user(tmp) == "person_001")
    check("missing workspace -> None (no crash)", resolve_primary_user("/nonexistent") is None)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
