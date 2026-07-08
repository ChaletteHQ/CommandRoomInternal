#!/usr/bin/env python3
"""Tests for integrity_check.py + workspace_root.py (substrate consistency).

Builds a synthetic workspace with planted defects and asserts the checker
catches each class, plus the root-resolution / product-folder-rejection logic.
stdlib only; non-zero exit = fail (house convention).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from workspace_root import (  # noqa: E402
    find_workspace_root,
    is_under_product_subfolder,
    assert_safe_write_path,
)
import integrity_check  # noqa: E402


def _build_workspace(tmp: Path) -> Path:
    """A workspace with one clean project + several planted defects."""
    data = tmp / "_hq" / "data"
    data.mkdir(parents=True)

    entities = {
        "version": 5,
        "last_updated": "2026-05-29",
        "last_writer": "test",
        # flat top-level shape (live convention) + 'threads' key
        "orgs": [
            {"id": "org_acme", "canonical_name": "Acme"},
            {"id": "org_loop_a", "canonical_name": "A", "parent_org_id": "org_loop_b"},
            {"id": "org_loop_b", "canonical_name": "B", "parent_org_id": "org_loop_a"},
        ],
        "people": [
            {"id": "person_001", "canonical_name": "Jo", "first_seen": "2026-01-01",
             "primary_org_id": "org_ghost"},  # C6: dangling org
        ],
        "threads": [
            {"id": "project_clean", "folder_name": "Clean Co", "status": "active",
             "first_seen": "2026-01-01", "affiliation_id": "org_acme"},
            {"id": "project_badaff", "folder_name": "Bad Aff", "status": "active",
             "first_seen": "2026-01-01", "affiliation_id": "org_missing"},  # C2
            {"id": "project_personal", "folder_name": "Personal Stuff", "status": "active",
             "first_seen": "2026-01-01", "affiliation_id": "personal"},  # OK, not a defect
            {"id": "project_moved", "folder_name": "Gone From Disk", "status": "active",
             "first_seen": "2026-01-01"},  # C9: folder missing
        ],
        "engagements": [],
    }
    (data / "entities.json").write_text(json.dumps(entities), encoding="utf-8")

    events = [
        {"seq": 1, "type": "note", "data": {"project_id": "project_clean"}},
        {"seq": 2, "type": "note", "data": {"primary_thread_id": "project_ghost"}},  # C7 dangling
        {"seq": 3, "type": "note", "data": {"project_id": "org_acme"}},  # C7 org-in-thread-slot
        {"seq": 9, "type": "x"},
        {"seq": 9, "type": "y"},  # C12 duplicate seq
    ]
    (data / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    (data / "aliases.json").write_text(
        json.dumps({"mappings": [
            {"raw": "good", "canonical_id": "org_acme"},
            {"raw": "bad", "canonical_id": "person_gone"},  # C8 dead alias
        ]}), encoding="utf-8"
    )

    # Disk folders.
    clean = tmp / "Clean Co"
    clean.mkdir()
    (clean / "PROJECT_CONTEXT.md").write_text("ctx", encoding="utf-8")
    (clean / "PROJECT_BRAIN.md").write_text("brain", encoding="utf-8")  # complete
    (clean / "SESSION_NOTES_Clean Co.md").write_text("notes", encoding="utf-8")  # complete

    badaff = tmp / "Bad Aff"
    badaff.mkdir()
    (badaff / "SESSION_NOTES.md").write_text("notes", encoding="utf-8")  # C11 missing brain

    orphan = tmp / "Mystery Folder"  # C10 orphan (no thread record)
    orphan.mkdir()
    (orphan / "PROJECT_CONTEXT.md").write_text("ctx", encoding="utf-8")  # also C11

    (tmp / "Personal Stuff").mkdir()
    (tmp / "_archive").mkdir()  # ignored
    return tmp


def _checks(tmp: Path):
    return {(f.check, f.subject): f for f in integrity_check.run_checks(tmp)}


def test_finds_planted_defects() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = _build_workspace(Path(d))
        by = _checks(tmp)
        keys = set(by.keys())
        # C2 — unresolved affiliation
        assert ("C2.thread_affiliation", "project_badaff") in keys
        # C4 — org parent cycle (both loop orgs flagged)
        assert any(c == "C4.org_cycle" for c, _ in keys)
        # C6 — person -> ghost org
        assert ("C6.person_org", "person_001") in keys
        # C7 — dangling event thread + org-in-thread-slot
        assert ("C7.dangling_event_thread", "project_ghost") in keys
        assert ("C7.dangling_event_thread", "org_acme") in keys
        assert "org id" in by[("C7.dangling_event_thread", "org_acme")].message
        # C8 — dead alias
        assert ("C8.dead_alias", "person_gone") in keys
        # C9 — moved/missing folder
        assert ("C9.thread_folder_missing", "project_moved") in keys
        # C10 — orphan folder
        assert ("C10.orphan_folder", "Mystery Folder") in keys
        # C11 — missing brains (Bad Aff + Mystery Folder), NOT Clean Co
        assert ("C11.missing_brain", "Bad Aff") in keys
        assert ("C11.missing_brain", "Clean Co") not in keys
        # C11b — missing session notes: Mystery Folder (context only), NOT Bad Aff
        # (has SESSION_NOTES.md) and NOT Clean Co (has SESSION_NOTES_Clean Co.md)
        assert ("C11b.missing_session_notes", "Mystery Folder") in keys
        assert ("C11b.missing_session_notes", "Bad Aff") not in keys
        assert ("C11b.missing_session_notes", "Clean Co") not in keys
        # C12 — duplicate seq
        assert ("C12.duplicate_seq", "9") in keys
        print("PASS test_finds_planted_defects")


def test_personal_affiliation_not_flagged() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = _build_workspace(Path(d))
        by = _checks(tmp)
        assert ("C2.thread_affiliation", "project_personal") not in by
        print("PASS test_personal_affiliation_not_flagged")


def test_clean_project_no_findings() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = _build_workspace(Path(d))
        by = _checks(tmp)
        # The clean project should not appear in any folder/brain/affiliation check.
        for (check, subj) in by:
            assert subj != "project_clean", f"clean project wrongly flagged by {check}"
            assert subj != "Clean Co", f"clean folder wrongly flagged by {check}"
        print("PASS test_clean_project_no_findings")


def test_nested_shape_normalizes() -> None:
    """Schema shape: collections nested under 'entities' with 'projects' key."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        data = tmp / "_hq" / "data"
        data.mkdir(parents=True)
        nested = {
            "version": 1, "last_updated": "x", "last_writer": "t",
            "entities": {
                "orgs": [{"id": "org_x", "canonical_name": "X"}],
                "people": [],
                "projects": [{"id": "project_y", "folder_name": "Y", "status": "active",
                              "first_seen": "2026-01-01", "affiliation_id": "org_missing"}],
            },
        }
        (data / "entities.json").write_text(json.dumps(nested), encoding="utf-8")
        (data / "events.jsonl").write_text("", encoding="utf-8")
        ent = integrity_check.load_entities(tmp)
        assert len(ent["orgs"]) == 1 and len(ent["threads"]) == 1
        by = _checks(tmp)
        assert ("C2.thread_affiliation", "project_y") in by
        print("PASS test_nested_shape_normalizes")


def test_workspace_root_resolution() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d).resolve()
        (tmp / "_hq" / "data").mkdir(parents=True)
        (tmp / "_hq" / "data" / "entities.json").write_text("{}", encoding="utf-8")
        deep = tmp / "Some Project" / "ref"
        deep.mkdir(parents=True)
        assert find_workspace_root(deep) == tmp
        assert find_workspace_root(tmp) == tmp
        print("PASS test_workspace_root_resolution")


def test_no_root_raises() -> None:
    with tempfile.TemporaryDirectory() as d:
        try:
            find_workspace_root(Path(d))
        except FileNotFoundError:
            print("PASS test_no_root_raises")
            return
        raise AssertionError("expected FileNotFoundError when no _hq/data/entities.json above")


def test_product_subfolder_rejected() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve()
        under = root / "Command Room" / "EOS Path"
        assert is_under_product_subfolder(under, root) is True
        assert is_under_product_subfolder(root / "EOS Path", root) is False
        try:
            assert_safe_write_path(under, root)
        except ValueError:
            print("PASS test_product_subfolder_rejected")
            return
        raise AssertionError("expected ValueError writing under Command Room/")


def main() -> int:
    test_finds_planted_defects()
    test_personal_affiliation_not_flagged()
    test_clean_project_no_findings()
    test_nested_shape_normalizes()
    test_workspace_root_resolution()
    test_no_root_raises()
    test_product_subfolder_rejected()
    print("\nALL integrity_check tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
