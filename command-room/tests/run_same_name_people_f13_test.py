#!/usr/bin/env python3
"""
F13 regression test — add-person elicit path's same-name pre-check
(integration-2026-07, Bug #19's visible half).

THE GAP: on "add a new person: [first name]" the live elicit form acknowledged
collision risk prospectively but never NAMED the same-first-name people
already on file. Mechanically, neither existing helper can produce that list:
`find_existing_person`'s tiers are exact-match ("quinn" never hits "Quinn
Sample"), and `entity_resolve.resolve_all` early-returns on the first exact
alias hit. `people_writer.list_same_name_people` (v4.8.1) is the deterministic
token-level scan the form header needs; people-crm's SKILL.md now mandates it
(plus find_existing_person) BEFORE rendering the add form.

Also guards the instruction layer: a helper referenced by zero skill texts is
invisible at runtime (the F-15 / render_and_relay gotcha class), so this test
asserts people-crm's SKILL.md actually references both helper names on its
elicit path.

stdlib-only, temp workspaces, non-zero exit on failure (house convention —
auto-discovered by run_all.py, unit tier).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from people_writer import list_same_name_people  # noqa: E402


def _ws(people: list[dict]) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="cr_f13_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True)
    entities = {
        "version": 1,
        "last_updated": "2026-07-01T00:00:00",
        "last_writer": "test",
        "entities": {"people": people, "projects": [], "orgs": []},
    }
    (data_dir / "entities.json").write_text(
        json.dumps(entities, indent=2), encoding="utf-8"
    )
    return ws


def _person(pid: str, name: str, **extra) -> dict:
    return {"id": pid, "canonical_name": name, "first_seen": "2026-01-01", **extra}


# Placeholder people only (references/PRIVACY_POLICY.md approved set).
PEOPLE = [
    _person("person_001", "Quinn Sample", role="Founder"),
    _person("person_002", "Quinn Stone", role="Ops Lead"),
    _person("person_003", "Rio Sample"),
    _person("person_004", "Bo Sample",
            status="archived", aliases=["Quinn"]),  # archived w/ Quinn alias
    _person("person_005", "Mira Stone"),
]


def test_first_name_query_lists_all_same_name_people():
    print("test_first_name_query_lists_all_same_name_people")
    ws = _ws(PEOPLE)
    hits = list_same_name_people(ws, "Quinn")
    names = [p["canonical_name"] for p in hits]
    assert names == ["Quinn Sample", "Quinn Stone"], names
    # (sorted by canonical_name; no exact full-name match for a bare "Quinn")


def test_exact_full_name_sorts_first():
    print("test_exact_full_name_sorts_first")
    ws = _ws(PEOPLE)
    hits = list_same_name_people(ws, "Quinn Stone")
    names = [p["canonical_name"] for p in hits]
    assert names[0] == "Quinn Stone", names
    assert "Quinn Sample" in names, names  # shared token still surfaces


def test_alias_and_nickname_tokens_match():
    print("test_alias_and_nickname_tokens_match")
    ws = _ws([
        _person("person_010", "Sam Stone", aliases=["Bowie Q"]),
        _person("person_011", "Lyra Sample", nicknames=["Bowie"]),
    ])
    hits = list_same_name_people(ws, "Bowie")
    names = sorted(p["canonical_name"] for p in hits)
    assert names == ["Lyra Sample", "Sam Stone"], names


def test_archived_excluded_by_default():
    print("test_archived_excluded_by_default")
    ws = _ws(PEOPLE)
    default_hits = {p["id"] for p in list_same_name_people(ws, "Quinn")}
    assert "person_004" not in default_hits, "archived must be excluded by default"
    with_archived = {p["id"] for p in
                     list_same_name_people(ws, "Quinn", include_archived=True)}
    assert "person_004" in with_archived


def test_no_match_and_empty_query_return_empty():
    print("test_no_match_and_empty_query_return_empty")
    ws = _ws(PEOPLE)
    assert list_same_name_people(ws, "Zyxx") == []
    assert list_same_name_people(ws, "") == []
    assert list_same_name_people(ws, "   ") == []


def test_max_candidates_cap():
    print("test_max_candidates_cap")
    many = [_person(f"person_{i:03d}", f"Quinn Placeholder{i}") for i in range(20)]
    ws = _ws(many)
    hits = list_same_name_people(ws, "Quinn", max_candidates=8)
    assert len(hits) == 8, len(hits)


def test_flat_workspace_shape_supported():
    print("test_flat_workspace_shape_supported")
    # Live workspaces exist in BOTH shapes: nested entities:{people:[]} and
    # flat top-level people:[] (the split-brain that motivated entities_io).
    ws = Path(tempfile.mkdtemp(prefix="cr_f13_flat_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True)
    flat = {
        "version": 1,
        "last_updated": "2026-07-01T00:00:00",
        "last_writer": "test",
        "people": [
            _person("person_001", "Quinn Sample"),
            _person("person_002", "Quinn Stone"),
        ],
        "projects": [],
        "orgs": [],
    }
    (data_dir / "entities.json").write_text(
        json.dumps(flat, indent=2), encoding="utf-8"
    )
    names = [p["canonical_name"] for p in list_same_name_people(ws, "Quinn")]
    assert names == ["Quinn Sample", "Quinn Stone"], names


def test_skill_text_references_the_helpers():
    print("test_skill_text_references_the_helpers")
    # Instruction-layer guard (F-15 gotcha class): the elicit path is only as
    # real as the skill text that mandates it.
    skill = (ROOT / "skills" / "people-crm" / "SKILL.md").read_text(encoding="utf-8")
    assert "list_same_name_people" in skill, (
        "people-crm SKILL.md must reference list_same_name_people on the "
        "add-person elicit path"
    )
    assert "find_existing_person" in skill
    assert "Never silent-create" in skill or "never silent-create" in skill.lower()


TESTS = [
    test_first_name_query_lists_all_same_name_people,
    test_exact_full_name_sorts_first,
    test_alias_and_nickname_tokens_match,
    test_archived_excluded_by_default,
    test_no_match_and_empty_query_return_empty,
    test_max_candidates_cap,
    test_flat_workspace_shape_supported,
    test_skill_text_references_the_helpers,
]


def main() -> int:
    failures = 0
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  FAIL: {t.__name__}: {e}")
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
