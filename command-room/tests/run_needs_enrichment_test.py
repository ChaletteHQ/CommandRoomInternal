#!/usr/bin/env python3
"""Tests for the `needs_enrichment` provisional flag (deep-audit #21).

The design judge's "single most important thing": provisional people created
through the typed writer used to lose their enrichment trigger because
people_writer FORBIDS + strips `pending_review`/`inferred_from`. `needs_enrichment`
is the allowed on-entity flag that fixes it. These tests verify it persists via
create_person, clears via update_person, and that the forbidden flags still
can't sneak onto the record.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import people_writer  # noqa: E402
from people_writer import create_person, update_person  # noqa: E402

results = {"pass": 0, "fail": 0}


def check(name, cond):
    if cond:
        results["pass"] += 1; print(f"PASS {name}")
    else:
        results["fail"] += 1; print(f"FAIL {name}")


def _ws():
    ws = Path(tempfile.mkdtemp(prefix="cr-needsenrich-test-"))
    d = ws / "_hq" / "data"; d.mkdir(parents=True)
    (d / "entities.json").write_text(json.dumps({"people": [], "orgs": [], "threads": []}), encoding="utf-8")
    (d / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def _people(ws):
    return json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))["people"]


def test_allowed():
    check("needs_enrichment in ALLOWED_PERSON_FIELDS", "needs_enrichment" in people_writer.ALLOWED_PERSON_FIELDS)
    check("pending_review still forbidden", "pending_review" in people_writer.FORBIDDEN_PERSON_FIELDS)
    check("inferred_from still forbidden", "inferred_from" in people_writer.FORBIDDEN_PERSON_FIELDS)


def test_create_with_flag():
    ws = _ws()
    rec = create_person(ws, canonical_name="Provisional Person", needs_enrichment=True, source_skill="test")
    check("create persists needs_enrichment", rec.get("needs_enrichment") is True)
    check("flag on stored record", _people(ws)[0].get("needs_enrichment") is True)


def test_create_without_flag_omits_it():
    ws = _ws()
    rec = create_person(ws, canonical_name="Plain Person", source_skill="test")
    check("no needs_enrichment when not requested", "needs_enrichment" not in rec)


def test_update_clears_flag():
    ws = _ws()
    rec = create_person(ws, canonical_name="Prov", needs_enrichment=True, source_skill="test")
    update_person(ws, rec["id"], needs_enrichment=False, source_skill="people-crm")
    stored = _people(ws)[0]
    check("update_person clears flag to False", stored.get("needs_enrichment") is False)


def main():
    for t in [test_allowed, test_create_with_flag, test_create_without_flag_omits_it, test_update_clears_flag]:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            results["fail"] += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"=== {results['pass']} passed, {results['fail']} failed ===")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
