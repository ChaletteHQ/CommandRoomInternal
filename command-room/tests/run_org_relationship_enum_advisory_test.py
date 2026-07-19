#!/usr/bin/env python3
"""
res1 regression test — relationship_type enum check is ADVISORY-only (HYG2 nit).

THE GAP: the F-05 migration preserved relationship_type values verbatim
(migrate-don't-drop), but nothing checked them against the schema enum —
a typo like `relationship_type: "clint"` sailed through the validator
silently.

THE CONTRACT (res1, F-05 lesson): the enum check is flag-only.
  - advisory_org_warnings() returns an FYI string for an off-enum value.
  - _validate_org NEVER raises for it (the live workspace carries a legacy
    `relationship_type: "network"`; a hard error would reopen the F-05
    flag-loop — validator flags forever, repair path has no rule to clear it).
  - Writes (create/update/repair) are NEVER gated by it, and repair stays a
    fixpoint — the advisory never makes a record a repair candidate.

Fixture mirrors the real record's SHAPE with placeholder names only.

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
    RELATIONSHIP_TYPES,
    _normalize_legacy_keys,
    _validate_org,
    advisory_org_warnings,
    update_org,
)


def _network_org() -> dict:
    # The live off-enum shape (org_020-class), placeholder names.
    return {
        "id": "org_090",
        "canonical_name": "Acme Labs",
        "relationship_type": "network",  # off-enum legacy value on the real record
        "status": "active",
    }


def _ws_with(org: dict) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="cr_res1_enum_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "entities.json").write_text(json.dumps({
        "version": 1,
        "last_updated": "2026-07-01T00:00:00",
        "last_writer": "test",
        "entities": {"people": [], "projects": [], "orgs": [org]},
    }, indent=2), encoding="utf-8")
    return ws


def test_off_enum_value_is_advisory_not_error():
    print("test_off_enum_value_is_advisory_not_error")
    record = _network_org()
    warnings = _validate_org(record)  # MUST NOT raise — the F-05 lesson
    assert warnings, "off-enum relationship_type must produce an advisory"
    assert "network" in warnings[0], warnings
    assert advisory_org_warnings(record) == warnings


def test_canonical_values_produce_no_advisory():
    print("test_canonical_values_produce_no_advisory")
    for rt in sorted(RELATIONSHIP_TYPES):
        record = {"id": "org_001", "canonical_name": "Acme Labs",
                  "relationship_type": rt}
        assert _validate_org(record) == [], rt


def test_absent_relationship_type_produces_no_advisory():
    print("test_absent_relationship_type_produces_no_advisory")
    assert _validate_org({"id": "org_001", "canonical_name": "Acme Labs"}) == []


def test_nonstring_value_is_advisory_not_error():
    print("test_nonstring_value_is_advisory_not_error")
    record = {"id": "org_001", "canonical_name": "Acme Labs",
              "relationship_type": ["client"]}
    warnings = _validate_org(record)  # must not raise
    assert warnings and "string" in warnings[0], warnings


def test_off_enum_never_becomes_a_repair_candidate():
    print("test_off_enum_never_becomes_a_repair_candidate")
    # repair-all's candidate check is `cleaned != original` — the advisory
    # must not create phantom repair work (the F-05 loop shape).
    record = _network_org()
    assert _normalize_legacy_keys(record) == record, (
        "advisory value must be left exactly as-is by the repair path"
    )


def test_update_on_off_enum_record_is_not_gated():
    print("test_update_on_off_enum_record_is_not_gated")
    ws = _ws_with(_network_org())
    updated = update_org(ws, "org_090", last_interaction="2026-07-14",
                         source_skill="test")
    assert updated.get("relationship_type") == "network", (
        "off-enum value must survive an unrelated update untouched"
    )


def test_enum_constant_in_lockstep_with_schema():
    print("test_enum_constant_in_lockstep_with_schema")
    schema = json.loads(
        (ROOT / "shared" / "data-schemas" / "entities.schema.json")
        .read_text(encoding="utf-8")
    )
    schema_enum = set(
        schema["$defs"]["org"]["properties"]["relationship_type"]["enum"]
    )
    assert set(org_writer.RELATIONSHIP_TYPES) == schema_enum, (
        f"RELATIONSHIP_TYPES drifted from the schema enum: "
        f"writer-only={set(org_writer.RELATIONSHIP_TYPES) - schema_enum}, "
        f"schema-only={schema_enum - set(org_writer.RELATIONSHIP_TYPES)}"
    )


TESTS = [
    test_off_enum_value_is_advisory_not_error,
    test_canonical_values_produce_no_advisory,
    test_absent_relationship_type_produces_no_advisory,
    test_nonstring_value_is_advisory_not_error,
    test_off_enum_never_becomes_a_repair_candidate,
    test_update_on_off_enum_record_is_not_gated,
    test_enum_constant_in_lockstep_with_schema,
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
