#!/usr/bin/env python3
"""
Unit tests for the v3.2-candidate people_writer.py — canonical writer for
person records in entities.json.

These cover the bug surfaced 2026-05-08: cr-past-meetings → apply-choices →
people-crm hand-rolled JSON shapes for new person records, producing the
malformed person_063 (Rio Sample) and person_064 (Dustin Sample) entries.
The helper validates field names, dedups against existing canonical records,
and atomic-writes — three things the prose Writer Contract did not enforce.

Run via: python tests/run_people_writer_test.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
SCHEMA = HERE.parent / "shared" / "data-schemas" / "entities.schema.json"
sys.path.insert(0, str(SCRIPTS))

from people_writer import (  # noqa: E402
    ALLOWED_PERSON_FIELDS,
    FORBIDDEN_PERSON_FIELDS,
    DuplicatePersonError,
    MultipleCandidatesError,
    create_person,
    find_existing_person,
    merge_person_into,
    repair_person,
    update_person,
)


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}{(' — ' + detail) if detail else ''}")
        raise AssertionError(label)


# ---------- fixture ----------

def _make_workspace() -> Path:
    """Build a throwaway workspace with a seeded entities.json + empty
    events.jsonl. Returns the workspace root."""
    ws = Path(tempfile.mkdtemp(prefix="cr-people-writer-test-"))
    (ws / "_hq" / "data").mkdir(parents=True)
    seed = {
        "version": 1,
        "last_updated": "2026-05-08T00:00:00",
        "last_writer": "test-fixture",
        "people": [
            {
                "id": "person_001",
                "canonical_name": "Matthew Sample",
                "first_seen": "2026-04-01",
                "email": "matthew@chaletteholdings.com",
                "aliases": ["Matt", "M"],
                "primary_org_id": "org_001",
            },
            {
                "id": "person_004",
                "canonical_name": "Dustin Sample",
                "first_seen": "2026-04-09",
                "email": "dustin@example.com",
                "primary_org_id": "org_010",
                "role": "Owner / CEO",
                "last_interaction": "2026-04-28",
                "aliases": ["Drew"],
            },
        ],
    }
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps(seed, indent=2), encoding="utf-8"
    )
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def _read_people(ws: Path) -> list[dict]:
    return json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))["people"]


def _read_events(ws: Path) -> list[dict]:
    text = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------- schema-allowlist sync ----------

def test_allowlist_matches_schema_defs_person():
    """ALLOWED_PERSON_FIELDS is the in-source mirror of the schema's
    $defs.person.properties. Drift between the two is the bug class this
    whole script exists to prevent — assert they agree."""
    print("test_allowlist_matches_schema_defs_person")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema_fields = set(schema["$defs"]["person"]["properties"].keys())
    _check(
        "ALLOWED_PERSON_FIELDS == schema $defs.person.properties keys",
        ALLOWED_PERSON_FIELDS == schema_fields,
        f"diff: in-source-only={ALLOWED_PERSON_FIELDS - schema_fields}, "
        f"schema-only={schema_fields - ALLOWED_PERSON_FIELDS}",
    )


def test_forbidden_keys_cover_observed_wild_drift():
    """The forbidden-keys map must list every wrong-shaped key the agent
    actually emitted in the wild (person_063 + person_064 shapes), so the
    error message can recommend the right field by name.

    v3.13.0 schema evolution: `emails` (plural array) MOVED FROM forbidden
    INTO canonical (see ALLOWED_PERSON_FIELDS). So it's no longer in the
    observed-wild-drift list — it's now the right answer."""
    print("test_forbidden_keys_cover_observed_wild_drift")
    observed_in_wild = {
        # person_063 Rio Sample shape
        "display_name", "current_org_id", "first_seen_at",
        "first_seen_source", "confidence",
        # person_064 Dustin Sample shape — `emails` removed in v3.13.0
        # because it became canonical (was wild drift pre-v3.13.0).
        "normalized_name", "role_at_primary_org",
        "inferred_from", "last_seen",
    }
    missing = observed_in_wild - set(FORBIDDEN_PERSON_FIELDS)
    _check("every observed wrong key has a remediation hint", not missing,
           f"unmapped: {sorted(missing)}")


# ---------- create_person ----------

def test_create_person_canonical_shape():
    print("test_create_person_canonical_shape")
    ws = _make_workspace()
    try:
        record = create_person(
            ws,
            canonical_name="Rio Sample",
            primary_org_id="org_005",
            role="Project Manager",
            aliases=["Rio N"],
            notes="Cat Co PM, mentioned by Sam.",
            first_seen="2026-04-30",
        )
        _check("returns id matching person_NNN", record["id"].startswith("person_"))
        _check("canonical_name set", record["canonical_name"] == "Rio Sample")
        _check("primary_org_id set (not current_org_id)", record["primary_org_id"] == "org_005")
        _check("first_seen set (not first_seen_at)", record["first_seen"] == "2026-04-30")
        _check("aliases preserved", record["aliases"] == ["Rio N"])
        _check("no display_name in record", "display_name" not in record)
        _check("no current_org_id in record", "current_org_id" not in record)
        _check("no first_seen_at in record", "first_seen_at" not in record)
        _check("no confidence in record", "confidence" not in record)

        people = _read_people(ws)
        _check("appended to entities.json", any(p["id"] == record["id"] for p in people))

        events = _read_events(ws)
        _check("logged person_created event", any(e["type"] == "person_created" for e in events))
    finally:
        shutil.rmtree(ws)


def test_create_person_assigns_next_id():
    print("test_create_person_assigns_next_id")
    ws = _make_workspace()
    try:
        # Seed has person_001 + person_004; next free is person_005.
        record = create_person(ws, canonical_name="Test Person", first_seen="2026-05-08")
        _check("next id is person_005 (max+1)", record["id"] == "person_005",
               f"got {record['id']}")
    finally:
        shutil.rmtree(ws)


def test_create_person_rejects_unknown_keys_via_internal():
    """create_person's signature enforces canonical names — but if a caller
    sneaks in extras via repair/update, the validator must reject. Test
    indirectly by trying update_person with bad keys."""
    print("test_create_person_rejects_unknown_keys_via_internal")
    ws = _make_workspace()
    try:
        try:
            update_person(ws, "person_001", display_name="Whatever")
        except ValueError as e:
            msg = str(e)
            _check("error names the wrong key", "display_name" in msg)
            _check("error suggests canonical_name", "canonical_name" in msg)
            return
        finally:
            pass
        raise AssertionError("expected ValueError")
    finally:
        shutil.rmtree(ws)


# ---------- dedup ----------

def test_create_person_dedups_by_email():
    print("test_create_person_dedups_by_email")
    ws = _make_workspace()
    try:
        try:
            create_person(
                ws,
                canonical_name="Drew W",
                email="dustin@example.com",  # already on person_004
            )
        except DuplicatePersonError as e:
            _check("raises DuplicatePersonError", True)
            _check("points at existing id", e.person_id == "person_004")
            return
        raise AssertionError("expected DuplicatePersonError")
    finally:
        shutil.rmtree(ws)


def test_create_person_dedups_by_email_case_insensitive():
    print("test_create_person_dedups_by_email_case_insensitive")
    ws = _make_workspace()
    try:
        try:
            create_person(
                ws,
                canonical_name="Dustin S",
                email="DUSTIN@EXAMPLE.COM",  # case differs
            )
        except DuplicatePersonError as e:
            _check("case-insensitive match", e.person_id == "person_004")
            return
        raise AssertionError("expected DuplicatePersonError")
    finally:
        shutil.rmtree(ws)


def test_create_person_dedups_by_canonical_name():
    print("test_create_person_dedups_by_canonical_name")
    ws = _make_workspace()
    try:
        try:
            create_person(ws, canonical_name="Dustin Sample")  # no email
        except DuplicatePersonError as e:
            _check("matches by canonical_name when email absent",
                   e.person_id == "person_004")
            return
        raise AssertionError("expected DuplicatePersonError")
    finally:
        shutil.rmtree(ws)


def test_create_person_dedups_by_alias():
    print("test_create_person_dedups_by_alias")
    # v3.13.7+ Bug #19 fix: alias-only matches now raise MultipleCandidatesError
    # (forcing apply-choices Step 3a to surface a disambiguation widget) rather
    # than auto-routing to the existing record via DuplicatePersonError. Even a
    # single candidate at the alias tier requires user confirmation — "Bo"
    # hitting Bo Sample's alias might be the same person OR a different
    # person with the same first name; the writer can't tell.
    ws = _make_workspace()
    try:
        try:
            # "Drew" is already an alias on person_004
            create_person(ws, canonical_name="Some Other Name", aliases=["Drew"])
        except MultipleCandidatesError as e:
            _check("alias hit surfaces for disambiguation",
                   len(e.candidates) == 1 and e.candidates[0]["id"] == "person_004")
            return
        raise AssertionError("expected MultipleCandidatesError on alias match")
    finally:
        shutil.rmtree(ws)


def test_create_person_skip_dedup_actually_skips():
    print("test_create_person_skip_dedup_actually_skips")
    ws = _make_workspace()
    try:
        record = create_person(
            ws,
            canonical_name="Dustin Sample",  # would normally dedup
            email="dustin@example.com",
            skip_dedup=True,
        )
        _check("skip_dedup=True bypasses the check", record["id"].startswith("person_"))
    finally:
        shutil.rmtree(ws)


# ---------- find_existing_person ----------

def test_find_existing_by_email():
    print("test_find_existing_by_email")
    ws = _make_workspace()
    try:
        result = find_existing_person(ws, email="dustin@example.com")
        _check("returns person_004", result and result["id"] == "person_004")
    finally:
        shutil.rmtree(ws)


def test_find_existing_returns_none_on_miss():
    print("test_find_existing_returns_none_on_miss")
    ws = _make_workspace()
    try:
        result = find_existing_person(ws, name="Nobody Here", email="x@y.com")
        _check("returns None on no match", result is None)
    finally:
        shutil.rmtree(ws)


# ---------- update_person ----------

def test_update_person_sets_field():
    print("test_update_person_sets_field")
    ws = _make_workspace()
    try:
        result = update_person(ws, "person_004", last_interaction="2026-05-08")
        _check("last_interaction updated", result["last_interaction"] == "2026-05-08")
        events = _read_events(ws)
        _check("logged person_updated event",
               any(e["type"] == "person_updated" for e in events))
    finally:
        shutil.rmtree(ws)


def test_update_person_rejects_forbidden_key_with_hint():
    print("test_update_person_rejects_forbidden_key_with_hint")
    ws = _make_workspace()
    try:
        try:
            update_person(ws, "person_004", normalized_name="Anything")
        except ValueError as e:
            msg = str(e)
            _check("error names the wrong key", "normalized_name" in msg)
            _check("error suggests removal (canonical_name is source of truth)",
                   "canonical_name" in msg or "remove" in msg)
            return
        raise AssertionError("expected ValueError")
    finally:
        shutil.rmtree(ws)


# ---------- merge_person_into ----------

def test_merge_dedup_into_canonical():
    """Repro of the Dustin Sample duplicate scenario: a malformed person_064
    must merge into person_004 carrying any net-new info, then disappear."""
    print("test_merge_dedup_into_canonical")
    ws = _make_workspace()
    # Seed in a duplicate Dustin Sample shaped like the real person_064.
    data = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    data["people"].append({
        "id": "person_064",
        "canonical_name": "Dustin Sample",  # duplicate
        "first_seen": "2026-04-26",
        "email": "dustin@example.com",
        "primary_org_id": "org_010",
        "last_interaction": "2026-04-30",
        "aliases": ["Reed"],
    })
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(data, indent=2),
                                                       encoding="utf-8")
    try:
        result = merge_person_into(ws, keep_id="person_004", duplicate_id="person_064")

        _check("kept canonical id", result["id"] == "person_004")
        _check("kept canonical_name", result["canonical_name"] == "Dustin Sample")
        _check("kept first_seen (older)", result["first_seen"] == "2026-04-09")
        _check("last_interaction took max",
               result["last_interaction"] == "2026-04-30")  # 2026-04-28 vs 2026-04-30
        _check("aliases unioned (Drew + Reed)",
               set(result["aliases"]) == {"Drew", "Reed"})

        people = _read_people(ws)
        _check("duplicate removed from people array",
               not any(p["id"] == "person_064" for p in people))
        _check("keeper still present",
               any(p["id"] == "person_004" for p in people))

        events = _read_events(ws)
        _check("logged person_merged event",
               any(e["type"] == "person_merged" for e in events))
    finally:
        shutil.rmtree(ws)


def test_merge_normalizes_legacy_keys_on_duplicate():
    """The actual person_064 in the wild had `last_seen: "2026-04-30"` (a
    forbidden legacy key). Without normalization, the strip-non-schema step
    drops `last_seen` before the merge runs → keeper's older `last_interaction`
    wins → newer signal lost. Normalization renames the legacy key first so
    its value is carried onto the keeper."""
    print("test_merge_normalizes_legacy_keys_on_duplicate")
    ws = _make_workspace()
    data = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    data["people"].append({
        "id": "person_064",
        "display_name": "Dustin Sample",       # → canonical_name
        "current_org_id": "org_010",            # → primary_org_id
        "first_seen_at": "2026-04-26T07:19:08Z",  # → first_seen "2026-04-26"
        "last_seen": "2026-04-30",              # → last_interaction (newer)
        "emails": ["dustin@example.com"],       # v3.13.0+: canonical (preserved, not dropped)
        "role_at_primary_org": "VP",            # → role (keeper has "Owner / CEO" so this won't override)
    })
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(data, indent=2),
                                                       encoding="utf-8")
    try:
        result = merge_person_into(ws, keep_id="person_004", duplicate_id="person_064")
        _check("last_seen normalized to last_interaction with newer date",
               result["last_interaction"] == "2026-04-30",
               f"got {result.get('last_interaction')}")
        _check("legacy display_name not on result",
               "display_name" not in result)
        _check("legacy current_org_id not on result",
               "current_org_id" not in result)
        # v3.13.0+: `emails` is canonical, not legacy. Don't assert it's absent.
        _check("legacy last_seen not on result", "last_seen" not in result)
        _check("legacy first_seen_at not on result", "first_seen_at" not in result)
        _check("legacy role_at_primary_org not on result",
               "role_at_primary_org" not in result)
        _check("keeper's role wins over legacy role_at_primary_org",
               result["role"] == "Owner / CEO")
    finally:
        shutil.rmtree(ws)


def test_merge_takes_first_seen_from_dup_when_keeper_lacks_it():
    """Many legacy records pre-date the schema's first_seen requirement.
    On merge into a legacy keeper without first_seen, the duplicate's value
    must carry over so the validator passes."""
    print("test_merge_takes_first_seen_from_dup_when_keeper_lacks_it")
    ws = _make_workspace()
    data = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    # Strip first_seen from the keeper to simulate a legacy record.
    for p in data["people"]:
        if p["id"] == "person_004":
            p.pop("first_seen", None)
    data["people"].append({
        "id": "person_064",
        "canonical_name": "Dustin Sample",
        "first_seen": "2026-04-26",
    })
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(data, indent=2),
                                                       encoding="utf-8")
    try:
        result = merge_person_into(ws, keep_id="person_004", duplicate_id="person_064")
        _check("first_seen carried from duplicate to keeper",
               result["first_seen"] == "2026-04-26")
    finally:
        shutil.rmtree(ws)


def test_merge_strips_non_schema_keys_from_duplicate():
    """If the duplicate carried wild-drift keys (display_name etc.), the
    merge must NOT carry them onto the keeper."""
    print("test_merge_strips_non_schema_keys_from_duplicate")
    ws = _make_workspace()
    data = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    data["people"].append({
        "id": "person_064",
        "canonical_name": "Dustin Sample",
        "first_seen": "2026-04-26",
        "display_name": "Dustin Sample",        # WRONG — must not propagate
        "normalized_name": "Dustin Sample",     # WRONG — must not propagate
        "role_at_primary_org": "VP",             # WRONG — must not propagate
    })
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(data, indent=2),
                                                       encoding="utf-8")
    try:
        result = merge_person_into(ws, keep_id="person_004", duplicate_id="person_064")
        _check("no display_name on keeper", "display_name" not in result)
        _check("no normalized_name on keeper", "normalized_name" not in result)
        _check("no role_at_primary_org on keeper", "role_at_primary_org" not in result)
    finally:
        shutil.rmtree(ws)


# ---------- repair_person ----------

def test_repair_person_renames_drops_validates():
    """Repro of the Rio Sample repair: rename display_name → canonical_name,
    rename current_org_id → primary_org_id, drop first_seen_at /
    first_seen_source / confidence, set first_seen as ISO date."""
    print("test_repair_person_renames_drops_validates")
    ws = _make_workspace()
    data = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    data["people"].append({
        "id": "person_063",
        "display_name": "Rio Sample",
        "current_org_id": "org_005",
        "first_seen_at": "2026-04-30T07:19:08Z",
        "first_seen_source": "meeting:c586db49",
        "confidence": 0.9,
        "role": "Project Manager",
        "aliases": ["Rio Sample"],
    })
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(data, indent=2),
                                                       encoding="utf-8")
    try:
        result = repair_person(
            ws, "person_063",
            field_renames={
                "display_name": "canonical_name",
                "current_org_id": "primary_org_id",
            },
            drop_fields=["first_seen_at", "first_seen_source", "confidence"],
            set_fields={"first_seen": "2026-04-30"},
        )
        _check("canonical_name set", result["canonical_name"] == "Rio Sample")
        _check("primary_org_id set", result["primary_org_id"] == "org_005")
        _check("first_seen is ISO date", result["first_seen"] == "2026-04-30")
        _check("first_seen_at dropped", "first_seen_at" not in result)
        _check("first_seen_source dropped", "first_seen_source" not in result)
        _check("confidence dropped", "confidence" not in result)
        _check("display_name gone", "display_name" not in result)
        _check("current_org_id gone", "current_org_id" not in result)
    finally:
        shutil.rmtree(ws)


def test_repair_validates_after_repair():
    """If the repair instructions don't actually fix the record, the
    validator must still raise rather than silently writing garbage."""
    print("test_repair_validates_after_repair")
    ws = _make_workspace()
    data = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    data["people"].append({
        "id": "person_063",
        "display_name": "Rio Sample",
    })
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(data, indent=2),
                                                       encoding="utf-8")
    try:
        try:
            # Drop display_name without supplying canonical_name → required
            # field missing → must raise.
            repair_person(ws, "person_063", drop_fields=["display_name"])
        except ValueError as e:
            _check("validator catches missing required field", "canonical_name" in str(e))
            return
        raise AssertionError("expected ValueError on missing required field")
    finally:
        shutil.rmtree(ws)


# ---------- atomic-write integration ----------

def test_writes_increment_version_field():
    print("test_writes_increment_version_field")
    ws = _make_workspace()
    try:
        before = json.loads((ws / "_hq" / "data" / "entities.json")
                            .read_text(encoding="utf-8"))["version"]
        create_person(ws, canonical_name="Version Bump Test",
                      first_seen="2026-05-08")
        after = json.loads((ws / "_hq" / "data" / "entities.json")
                           .read_text(encoding="utf-8"))["version"]
        _check("version field incremented", after == before + 1)
    finally:
        shutil.rmtree(ws)


def test_last_writer_field_updates():
    print("test_last_writer_field_updates")
    ws = _make_workspace()
    try:
        create_person(ws, canonical_name="Writer Test",
                      first_seen="2026-05-08",
                      source_skill="my-test-skill")
        data = json.loads((ws / "_hq" / "data" / "entities.json")
                          .read_text(encoding="utf-8"))
        _check("last_writer is the source_skill", data["last_writer"] == "my-test-skill")
    finally:
        shutil.rmtree(ws)


# ---------- runner ----------

ALL_TESTS = [
    test_allowlist_matches_schema_defs_person,
    test_forbidden_keys_cover_observed_wild_drift,
    test_create_person_canonical_shape,
    test_create_person_assigns_next_id,
    test_create_person_rejects_unknown_keys_via_internal,
    test_create_person_dedups_by_email,
    test_create_person_dedups_by_email_case_insensitive,
    test_create_person_dedups_by_canonical_name,
    test_create_person_dedups_by_alias,
    test_create_person_skip_dedup_actually_skips,
    test_find_existing_by_email,
    test_find_existing_returns_none_on_miss,
    test_update_person_sets_field,
    test_update_person_rejects_forbidden_key_with_hint,
    test_merge_dedup_into_canonical,
    test_merge_normalizes_legacy_keys_on_duplicate,
    test_merge_takes_first_seen_from_dup_when_keeper_lacks_it,
    test_merge_strips_non_schema_keys_from_duplicate,
    test_repair_person_renames_drops_validates,
    test_repair_validates_after_repair,
    test_writes_increment_version_field,
    test_last_writer_field_updates,
]


def main() -> int:
    failed = 0
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as e:
            print(f"  FAIL: {fn.__name__}: {e}")
            failed += 1
    print()
    if failed:
        print(f"{failed} / {len(ALL_TESTS)} test(s) FAILED")
        return 1
    print(f"all {len(ALL_TESTS)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
