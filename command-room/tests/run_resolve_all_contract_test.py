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


# ---------------------------------------------------------------------------
# WG1-B D-B5 — the opt-in open-proposal tier. Fixtures mirror real substrate
# shapes; dates relative to today (G14).
# ---------------------------------------------------------------------------

def _proposal_workspace() -> Path:
    """On-file person 'Avery Nested' + an OPEN person_proposal for a person
    with no record yet ('Nova Placeholder') + a resolved (tombstoned) one."""
    import datetime as dt
    ws = _nested_workspace()
    now = dt.datetime.now(dt.timezone.utc)

    def _ts(days_ago):
        return (now - dt.timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    events = [
        {"seq": 1, "ts": _ts(6), "type": "person_proposal",
         "source_skill": "meeting-notes",
         "data": {"name": "Nova Placeholder", "pending_review": True,
                  "evidence": "Introduced as the new ops lead on the call.",
                  "source_ref": "granola:00000000-b5"}},
        {"seq": 2, "ts": _ts(5), "type": "person_proposal",
         "source_skill": "meeting-notes",
         "data": {"name": "Rex Placeholder", "pending_review": True,
                  "source_ref": "granola:00000000-b5b"}},
        {"seq": 3, "ts": _ts(4), "type": "person_proposal_resolved",
         "source_skill": "apply-choices",
         "data": {"proposal_seq": 2, "resolution": "not_relevant"}},
    ]
    (ws / "_hq" / "data" / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return ws


def test_open_proposal_tier_default_off_byte_identical() -> None:
    """The default path never surfaces proposals — existing callers are
    byte-identical, hit or miss."""
    from entity_resolve import resolve_all
    ws = _proposal_workspace()
    hit_default = resolve_all(ws, "Avery Nested")
    hit_flag = resolve_all(ws, "Avery Nested", include_open_proposals=True)
    assert [r.entity_id for r in hit_default] == \
           [r.entity_id for r in hit_flag], "entity hit must be identical"
    miss = resolve_all(ws, "Nova Placeholder")
    assert miss == [], f"default flag must NOT surface proposals: {miss}"
    print("PASS test_open_proposal_tier_default_off_byte_identical")


def test_open_proposal_tier_hit_on_total_miss() -> None:
    """Flag on + entities miss + open proposal → the distinct open_proposal
    result carrying the proposal row; tombstoned proposals never surface."""
    from entity_resolve import resolve_all
    ws = _proposal_workspace()
    res = resolve_all(ws, "Nova Placeholder", include_open_proposals=True)
    assert len(res) == 1, f"expected one open_proposal hit: {res}"
    r = res[0]
    assert r.entity_type == "open_proposal", r.entity_type
    assert r.matched_via == "open_proposal", r.matched_via
    assert r.record.get("seq") == 1
    assert "ops lead" in (r.record.get("evidence") or ""), r.record
    assert "pending" in r.reason, r.reason
    gone = resolve_all(ws, "Rex Placeholder", include_open_proposals=True)
    assert gone == [], f"a tombstoned proposal must never surface: {gone}"
    none = resolve_all(ws, "Zed Nobody", include_open_proposals=True)
    assert none == [], f"no proposal, no entity → honest miss: {none}"
    print("PASS test_open_proposal_tier_hit_on_total_miss")


def test_open_proposal_tier_never_shadows_an_entity_hit() -> None:
    """The row-19 Garrick shape: an ON-FILE person resolves normally with
    zero proposal noise, even with the flag on and a same-name proposal
    somehow open."""
    from entity_resolve import resolve_all
    ws = _proposal_workspace()
    ev_path = ws / "_hq" / "data" / "events.jsonl"
    ev_path.write_text(ev_path.read_text(encoding="utf-8") + json.dumps(
        {"seq": 4, "ts": "2026-07-01T00:00:00Z", "type": "person_proposal",
         "source_skill": "meeting-notes",
         "data": {"name": "Avery Nested", "pending_review": True,
                  "source_ref": "mail:b5c"}}) + "\n", encoding="utf-8")
    res = resolve_all(ws, "Avery Nested", include_open_proposals=True)
    assert res and res[0].entity_type == "person", res
    assert all(r.entity_type != "open_proposal" for r in res), \
        "an entity hit must never carry proposal noise"
    print("PASS test_open_proposal_tier_never_shadows_an_entity_hit")


def main() -> int:
    tests = [
        test_no_backwards_signature_in_surfaces,
        test_go_person_resolves_project_on_nested_workspace,
        test_go_org_resolves_project_on_nested_workspace,
        test_open_proposal_tier_default_off_byte_identical,
        test_open_proposal_tier_hit_on_total_miss,
        test_open_proposal_tier_never_shadows_an_entity_hit,
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
