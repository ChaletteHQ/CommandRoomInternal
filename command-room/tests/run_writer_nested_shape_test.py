#!/usr/bin/env python3
"""Regression guard for the entity-writer split-brain (deep-audit 2026-05-29,
finding #2 — CRITICAL).

The existing writer tests seed entities.json in the FLAT shape
(`{"people": [...]}`), which is exactly the shape the buggy `setdefault`
writers assumed — so they stayed green while real CANONICAL nested-shape
workspaces (`{"entities": {"people": [...]}}`) silently fragmented: every new
org/person landed in a brand-new flat top-level key that no reader, resolver,
projector or briefing ever looked at, and the id generator collided with the
real records.

These tests seed a NESTED-shape workspace and assert that create_org /
create_person write INTO the canonical `entities.{orgs,people}` collection,
do NOT spawn a flat shadow key, and assign a non-colliding next id.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from org_writer import create_org  # noqa: E402
from people_writer import create_person  # noqa: E402


def _nested_workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="cr-writer-nested-test-"))
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    (data / "entities.json").write_text(json.dumps({
        "entities": {
            "people": [{"id": "person_001", "canonical_name": "Seed Person"}],
            "orgs": [{"id": "org_001", "canonical_name": "Seed Co"}],
            "threads": [],
        }
    }), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    (data / "aliases.json").write_text(json.dumps({"mappings": {}}), encoding="utf-8")
    return ws


def _reload(ws: Path) -> dict:
    return json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))


def test_create_org_lands_in_nested_collection() -> None:
    ws = _nested_workspace()
    rec = create_org(ws, canonical_name="Shadowtest Logistics", source_skill="test")
    data = _reload(ws)
    nested = data.get("entities", {}).get("orgs", [])
    assert any(o["id"] == rec["id"] for o in nested), (
        "new org did not land in entities.orgs — writer split-brain regressed"
    )
    assert "orgs" not in data, (
        "writer created a FLAT top-level 'orgs' shadow key on a nested workspace"
    )
    assert rec["id"] == "org_002", f"id collided/miscomputed: got {rec['id']}"
    print("PASS test_create_org_lands_in_nested_collection")


def test_create_person_lands_in_nested_collection() -> None:
    ws = _nested_workspace()
    rec = create_person(ws, canonical_name="Avery Shadowtest", first_seen="2026-05-29")
    data = _reload(ws)
    nested = data.get("entities", {}).get("people", [])
    assert any(p["id"] == rec["id"] for p in nested), (
        "new person did not land in entities.people — writer split-brain regressed"
    )
    assert "people" not in data, (
        "writer created a FLAT top-level 'people' shadow key on a nested workspace"
    )
    assert rec["id"] == "person_002", f"id collided/miscomputed: got {rec['id']}"
    print("PASS test_create_person_lands_in_nested_collection")


def test_flat_shape_still_works() -> None:
    """Legacy flat-shape workspaces must be unaffected."""
    ws = Path(tempfile.mkdtemp(prefix="cr-writer-flat-test-"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "entities.json").write_text(json.dumps({
        "people": [{"id": "person_001", "canonical_name": "Seed Person"}],
        "orgs": [{"id": "org_001", "canonical_name": "Seed Co"}],
    }), encoding="utf-8")
    (data_dir / "events.jsonl").write_text("", encoding="utf-8")
    (data_dir / "aliases.json").write_text(json.dumps({"mappings": {}}), encoding="utf-8")
    rec = create_person(ws, canonical_name="Flat Newperson", first_seen="2026-05-29")
    data = _reload(ws)
    assert any(p["id"] == rec["id"] for p in data.get("people", [])), (
        "flat-shape write regressed"
    )
    assert "entities" not in data, "flat workspace should not gain an entities wrapper"
    print("PASS test_flat_shape_still_works")


def main() -> int:
    tests = [
        test_create_org_lands_in_nested_collection,
        test_create_person_lands_in_nested_collection,
        test_flat_shape_still_works,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"=== {len(tests) - failed} passed, {failed} failed ===")
        return 1
    print(f"OK — all {len(tests)} writer nested-shape tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
