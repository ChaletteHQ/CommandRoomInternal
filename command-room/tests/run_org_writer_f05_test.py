#!/usr/bin/env python3
"""
F-05 regression test — the org-record validator flag-loop (integration-2026-07).

THE BUG: an org record carrying `legal_name` (real data, off-schema at the
time) plus the legacy `relationship` key failed `_validate_org`, but the
sanctioned repair path (`_normalize_legacy_keys` → repair_org / repair-all)
had no rule for either field — `cleaned == original`, so repair-all reported
0 repairs while the validator kept flagging the record on EVERY future update,
permanently. The bridge's `org_record_repair_v3_13_0` migration (validator_check
semantics) therefore re-surfaced the same record on every bridge run.

THE FIX (v4.8.1): `legal_name` admitted into the schema + ALLOWED_ORG_FIELDS
(it's real data); legacy `relationship` gets a migrate-don't-drop rule in
`_normalize_legacy_keys` (rename when relationship_type is absent, dedup-drop
when equal, preserve-into-notes when conflicting). The CONTRACT under test:
the validator and the repair path must agree — anything the validator flags,
the repair path must clear.

Fixture mirrors the real drifted record's SHAPE (per the real-data fixture
gotcha) with placeholder names only.

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

import org_writer  # noqa: E402
from org_writer import (  # noqa: E402
    _normalize_legacy_keys,
    _validate_org,
    find_existing_org,
    repair_org,
    update_org,
)


# The real drifted record's shape (org_020-class), placeholder names.
def _drifted_org() -> dict:
    return {
        "id": "org_090",
        "canonical_name": "Acme Labs",
        "parent_org_id": None,
        "relationship_type": "network",   # off-enum legacy value, present on the real record
        "scope": None,
        "is_primary_focus": False,
        "notes": "Placeholder notes carried over from the real record shape.",
        "inferred_from": ["user_input"],
        "aliases": ["Acme Group"],
        "relationship": "client",          # legacy key CONFLICTING with relationship_type
        "status": "active",
        "legal_name": "Acme Lab Inc.",     # real data, off-schema pre-F-05
    }


def _ws_with(org: dict) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="cr_f05_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True)
    entities = {
        "version": 1,
        "last_updated": "2026-07-01T00:00:00",
        "last_writer": "test",
        "entities": {"people": [], "projects": [], "orgs": [org]},
    }
    (data_dir / "entities.json").write_text(
        json.dumps(entities, indent=2), encoding="utf-8"
    )
    return ws


def test_legal_name_validates():
    print("test_legal_name_validates")
    record = {"id": "org_001", "canonical_name": "Acme Labs",
              "legal_name": "Acme Lab Inc."}
    _validate_org(record)  # must not raise


def test_legacy_relationship_renamed_when_type_absent():
    print("test_legacy_relationship_renamed_when_type_absent")
    out = _normalize_legacy_keys(
        {"id": "org_001", "canonical_name": "Acme Labs", "relationship": "client"}
    )
    assert "relationship" not in out, out
    assert out.get("relationship_type") == "client", out


def test_legacy_relationship_equal_dropped_no_notes():
    print("test_legacy_relationship_equal_dropped_no_notes")
    out = _normalize_legacy_keys(
        {"id": "org_001", "canonical_name": "Acme Labs",
         "relationship": "client", "relationship_type": "client"}
    )
    assert "relationship" not in out, out
    assert out.get("relationship_type") == "client", out
    assert "notes" not in out, "equal duplicate must not generate a notes line"


def test_legacy_relationship_conflict_preserved_into_notes():
    print("test_legacy_relationship_conflict_preserved_into_notes")
    out = _normalize_legacy_keys(_drifted_org())
    assert "relationship" not in out, out
    assert out.get("relationship_type") == "network", "existing type must win"
    assert "'client'" in out.get("notes", ""), (
        "conflicting legacy value must be preserved into notes, got: "
        + repr(out.get("notes"))
    )
    assert "Placeholder notes" in out["notes"], "existing notes must be kept"
    _validate_org(out)  # migrated record must validate clean


def test_flag_loop_closed_repair_then_validate_then_idempotent():
    print("test_flag_loop_closed_repair_then_validate_then_idempotent")
    ws = _ws_with(_drifted_org())

    # Pre-fix symptom half 1: the validator flags the drifted record.
    try:
        _validate_org(_drifted_org())
        raise AssertionError("drifted record should fail validation (legacy key)")
    except ValueError:
        pass

    # The sanctioned repair path must now clear every flag ...
    repaired = repair_org(ws, "org_090", source_skill="test")
    _validate_org(repaired)  # no raise = validator and repair tool agree
    assert repaired.get("legal_name") == "Acme Lab Inc.", "legal_name must survive"
    assert "relationship" not in repaired
    assert "'client'" in repaired.get("notes", "")

    # ... and be idempotent: a second normalize pass changes nothing
    # (repair-all's candidate check is `cleaned != original`).
    assert _normalize_legacy_keys(repaired) == repaired, "repair must be a fixpoint"

    # ... and a future update must not re-flag (the loop is closed).
    updated = update_org(ws, "org_090", last_interaction="2026-07-14",
                         source_skill="test")
    assert updated.get("legal_name") == "Acme Lab Inc."

    # org_repaired + org_updated events landed
    events = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    assert '"org_repaired"' in events and '"org_updated"' in events


def test_nonstring_relationship_preserved_not_dropped():
    print("test_nonstring_relationship_preserved_not_dropped")
    # Second-eyes finding 2: a list/dict/number legacy value must never be
    # silently discarded (and never renamed into the enum field).
    out = _normalize_legacy_keys(
        {"id": "org_001", "canonical_name": "Acme Labs",
         "relationship": ["client", "partner"]}
    )
    assert "relationship" not in out, out
    assert "relationship_type" not in out, "non-string must not be renamed"
    assert "client" in out.get("notes", "") and "partner" in out.get("notes", ""), out
    _validate_org(out)


def test_list_shaped_notes_survive_the_migration_line():
    print("test_list_shaped_notes_survive_the_migration_line")
    # Second-eyes finding 3: legacy list-shaped notes keep every element.
    out = _normalize_legacy_keys(
        {"id": "org_001", "canonical_name": "Acme Labs",
         "relationship": "client", "relationship_type": "vendor",
         "notes": ["keep me", "and me"]}
    )
    notes = out.get("notes")
    assert isinstance(notes, list), notes
    assert notes[:2] == ["keep me", "and me"], notes
    assert any("'client'" in str(line) for line in notes[2:]), notes


def test_find_existing_org_matches_legal_name():
    print("test_find_existing_org_matches_legal_name")
    ws = _ws_with(_drifted_org())
    repair_org(ws, "org_090", source_skill="test")
    hit = find_existing_org(ws, name="Acme Lab Inc.")
    assert hit is not None and hit["id"] == "org_090", hit


def test_allowed_fields_mirror_schema():
    print("test_allowed_fields_mirror_schema")
    schema = json.loads(
        (ROOT / "shared" / "data-schemas" / "entities.schema.json")
        .read_text(encoding="utf-8")
    )
    schema_fields = set(schema["$defs"]["org"]["properties"].keys())
    allowed = org_writer.ALLOWED_ORG_FIELDS
    # `brand` is schema-only by design (written by hand in paid engagements,
    # never by the writer). Everything else must stay in lockstep.
    missing_in_writer = schema_fields - allowed - {"brand"}
    missing_in_schema = allowed - schema_fields
    assert not missing_in_writer, f"schema fields absent from ALLOWED_ORG_FIELDS: {missing_in_writer}"
    assert not missing_in_schema, f"ALLOWED_ORG_FIELDS not in schema: {missing_in_schema}"


TESTS = [
    test_legal_name_validates,
    test_legacy_relationship_renamed_when_type_absent,
    test_legacy_relationship_equal_dropped_no_notes,
    test_legacy_relationship_conflict_preserved_into_notes,
    test_nonstring_relationship_preserved_not_dropped,
    test_list_shaped_notes_survive_the_migration_line,
    test_flag_loop_closed_repair_then_validate_then_idempotent,
    test_find_existing_org_matches_legal_name,
    test_allowed_fields_mirror_schema,
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
