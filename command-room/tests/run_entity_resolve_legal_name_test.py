#!/usr/bin/env python3
"""
res1 regression test — entity_resolve indexes org `legal_name` (HYG2 nit).

THE GAP: F-05 (v4.8.1) admitted `legal_name` into the org schema +
ALLOWED_ORG_FIELDS and taught find_existing_org to match it — but
entity_resolve._iter_match_surfaces never yielded it. Result: "pull up
[legal name]" missed orgs the workspace knows by their registered name
(the resolver only saw canonical_name + aliases).

THE FIX (res1): `legal_name` is now an org match surface in the resolver
tiers (exact → fuzzy → phonetic), so a legal-name query resolves like any
other name surface.

Fixture mirrors the real drifted record's SHAPE (per the real-data fixture
gotcha) with placeholder names only, nested canonical entities shape.

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

from entity_resolve import resolve, resolve_all  # noqa: E402


def _ws(orgs: list[dict]) -> Path:
    """Canonical NESTED-shape workspace (collections under `entities`)."""
    ws = Path(tempfile.mkdtemp(prefix="cr_res1_legal_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "entities.json").write_text(json.dumps({
        "entities": {"people": [], "projects": [], "orgs": orgs},
    }), encoding="utf-8")
    return ws


def _acme() -> dict:
    # Same shape class as the real F-05 record: legal_name differs from
    # canonical_name and appears in no alias list.
    return {
        "id": "org_090",
        "canonical_name": "Acme Labs",
        "legal_name": "Acme Laboratories Incorporated",
        "aliases": ["Acme Group"],
        "status": "active",
    }


def test_exact_legal_name_resolves():
    print("test_exact_legal_name_resolves")
    ws = _ws([_acme()])
    r = resolve(ws, "Acme Laboratories Incorporated")
    assert r is not None, "exact legal_name query must resolve (HYG2 nit)"
    assert r.entity_type == "org" and r.entity_id == "org_090", r
    assert r.confidence == 1.0, r
    assert "legal name" in r.reason, (
        "reason should say the match came via the legal name (plain-English "
        "label, not snake_case — it's user-surfaceable), got: " + r.reason
    )


def test_fuzzy_legal_name_typo_resolves():
    print("test_fuzzy_legal_name_typo_resolves")
    ws = _ws([_acme()])
    r = resolve(ws, "Acme Laboratories Incorporatd")  # typo
    assert r is not None, "near-miss legal_name must fuzzy-resolve"
    assert r.entity_id == "org_090", r
    assert r.matched_via == "fuzzy", r


def test_canonical_name_still_wins_dedup():
    print("test_canonical_name_still_wins_dedup")
    # Same entity matchable via multiple surfaces must appear once.
    ws = _ws([_acme()])
    results = resolve_all(ws, "Acme Labs")
    ids = [r.entity_id for r in results]
    assert ids.count("org_090") == 1, results


def test_nonstring_legal_name_tolerated():
    print("test_nonstring_legal_name_tolerated")
    # Real-data defensive-reader convention: malformed field must not crash
    # the resolver or block other surfaces from matching.
    broken = _acme()
    for bad in (None, 123, ["Acme Laboratories Incorporated"]):
        broken["legal_name"] = bad
        ws = _ws([broken])
        r = resolve(ws, "Acme Labs")
        assert r is not None and r.entity_id == "org_090", (bad, r)


def test_no_legal_name_field_unchanged():
    print("test_no_legal_name_field_unchanged")
    org = _acme()
    del org["legal_name"]
    ws = _ws([org])
    # Without the field there is no exact/fuzzy legal-name surface. (A weak
    # 0.75 phonetic hit via full-string Soundex against "Acme Labs" is
    # pre-existing resolver behavior, so assert above that tier.)
    r = resolve(ws, "Acme Laboratories Incorporated", min_confidence=0.8)
    assert r is None, r
    r = resolve(ws, "Acme Labs")
    assert r is not None and r.entity_id == "org_090"


TESTS = [
    test_exact_legal_name_resolves,
    test_fuzzy_legal_name_typo_resolves,
    test_canonical_name_still_wins_dedup,
    test_nonstring_legal_name_tolerated,
    test_no_legal_name_field_unchanged,
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
