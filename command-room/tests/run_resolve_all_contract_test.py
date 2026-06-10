#!/usr/bin/env python3
"""Regression guard for the entity-resolve contract (deep-audit 2026-05-29).

Closes two verified findings the green battery was blind to:

  1. CRITICAL — the canonical ENTITY_RESOLVE_PROTOCOL doc + 7 name-bearing
     SKILL.md snippets taught `resolve_all(query, workspace_root)`, i.e. the
     search string passed into the FIRST positional slot — but the real
     signature is `resolve_all(workspace_root, query)`. Any skill copying the
     backwards order resolves the wrong argument. Static guard below fails if
     the backwards positional order reappears in any doc/skill surface.

  2. HIGH — `resolve_to_linked_project` re-loaded entities.json RAW (no
     `_unwrap_entities`), so on a canonical NESTED-shape workspace
     (`{entities: {threads: [...]}}`) it found zero linked projects and
     `go [person]` / `go [org]` silently returned no project context. Runtime
     test below builds a nested-shape workspace and asserts the walk succeeds.

stdlib only, no external deps.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from entity_resolve import resolve_to_linked_project  # noqa: E402

# Search string passed as the first positional arg — the bug. Matches
# `resolve_all(query,` but NOT the keyword form `resolve_all(query=...)`.
BACKWARDS = re.compile(r"resolve_all\(\s*query\s*,")


def test_no_backwards_signature_in_surfaces() -> None:
    """No doc or SKILL.md may teach resolve_all(query, workspace_root)."""
    surfaces = list((ROOT / "skills").glob("*/SKILL.md"))
    surfaces.append(ROOT / "shared" / "ENTITY_RESOLVE_PROTOCOL.md")
    offenders = []
    for path in surfaces:
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if BACKWARDS.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{i}")
    assert not offenders, (
        "backwards resolve_all(query, workspace_root) order found in: "
        + ", ".join(offenders)
    )
    print("PASS test_no_backwards_signature_in_surfaces")


def _nested_workspace() -> Path:
    """A canonical NESTED-shape workspace: collections under `entities`."""
    ws = Path(tempfile.mkdtemp())
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    (data / "entities.json").write_text(json.dumps({
        "entities": {
            "people": [{
                "id": "person_001",
                "canonical_name": "Avery Nested",
                "primary_org_id": "org_001",
            }],
            "orgs": [{"id": "org_001", "canonical_name": "Nestco"}],
            "threads": [{
                "id": "project_001",
                "canonical_name": "Nestco — Pilot",
                "status": "active",
                "key_contact_id": "person_001",
                "affiliation_id": "org_001",
                "last_activity": "2026-05-01",
            }],
        }
    }), encoding="utf-8")
    return ws


def test_go_person_resolves_project_on_nested_workspace() -> None:
    ws = _nested_workspace()
    result = resolve_to_linked_project(ws, "Avery Nested")
    assert result is not None, (
        "resolve_to_linked_project returned None on a nested-shape workspace "
        "— the _unwrap_entities walk regressed (go [person] loads no context)"
    )
    assert result.entity_type == "project"
    assert result.record.get("id") == "project_001"
    print("PASS test_go_person_resolves_project_on_nested_workspace")


def test_go_org_resolves_project_on_nested_workspace() -> None:
    ws = _nested_workspace()
    result = resolve_to_linked_project(ws, "Nestco")
    assert result is not None, "org walk regressed on nested-shape workspace"
    assert result.entity_type == "project"
    assert result.record.get("id") == "project_001"
    print("PASS test_go_org_resolves_project_on_nested_workspace")


def main() -> int:
    tests = [
        test_no_backwards_signature_in_surfaces,
        test_go_person_resolves_project_on_nested_workspace,
        test_go_org_resolves_project_on_nested_workspace,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    total = len(tests)
    if failed:
        print(f"=== {total - failed} passed, {failed} failed ===")
        return 1
    print(f"OK — all {total} resolve_all contract tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
