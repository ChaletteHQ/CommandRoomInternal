#!/usr/bin/env python3
"""Tests for thread_writer.py (deep-audit #6 — the missing typed thread writer).

Verifies the spine entity finally gets the same write discipline orgs/people
have: lands in the wrapper-aware collection (nested + flat), non-racy id gen,
dedup, schema validation, legacy string-stage coercion, roster_overrides
allowed, and a canonical thread_created event.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import thread_writer as tw  # noqa: E402

results = {"pass": 0, "fail": 0}


def check(name, cond):
    if cond:
        results["pass"] += 1; print(f"PASS {name}")
    else:
        results["fail"] += 1; print(f"FAIL {name}")


def _ws(entities):
    ws = Path(tempfile.mkdtemp(prefix="cr-threadwriter-test-"))
    d = ws / "_hq" / "data"; d.mkdir(parents=True)
    (d / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    (d / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def _reload(ws):
    return json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))


def test_create_nested():
    ws = _ws({"entities": {"threads": [], "people": [], "orgs": []}})
    rec = tw.create_thread(ws, canonical_name="New Initiative", source_skill="test")
    data = _reload(ws)
    nested = data["entities"]["threads"]
    check("lands in entities.threads (nested)", any(t["id"] == rec["id"] for t in nested))
    check("no flat shadow", "threads" not in data)
    check("id is project_001", rec["id"] == "project_001")
    check("folder_name slugified", rec["folder_name"] == "new_initiative")
    evs = [json.loads(l) for l in (ws / "_hq" / "data" / "events.jsonl").read_text().splitlines() if l.strip()]
    check("thread_created event emitted", any(e.get("type") == "thread_created" for e in evs))


def test_create_flat_and_id_increment():
    ws = _ws({"threads": [{"id": "project_005", "canonical_name": "Existing", "status": "active", "folder_name": "e", "first_seen": "2026-01-01"}]})
    rec = tw.create_thread(ws, canonical_name="Another", source_skill="test")
    data = _reload(ws)
    check("flat create lands in threads", any(t["id"] == rec["id"] for t in data["threads"]))
    check("id increments past existing (project_006)", rec["id"] == "project_006")


def test_dedup_raises():
    ws = _ws({"threads": [{"id": "project_001", "canonical_name": "Dup Co", "status": "active", "folder_name": "dup", "first_seen": "2026-01-01"}]})
    try:
        tw.create_thread(ws, canonical_name="Dup Co", source_skill="test")
        check("dedup raises", False)
    except ValueError:
        check("dedup raises", True)


def test_bad_status_raises():
    ws = _ws({"threads": []})
    try:
        tw.create_thread(ws, canonical_name="X", status="bogus", source_skill="test")
        check("bad status raises", False)
    except ValueError:
        check("bad status raises", True)


def test_real_world_status_accepted():
    ws = _ws({"threads": []})
    rec = tw.create_thread(ws, canonical_name="Scoped", status="scoping", source_skill="test")
    check("real-world 'scoping' status accepted", rec["status"] == "scoping")


def test_string_stage_coerced():
    ws = _ws({"threads": [{"id": "project_001", "canonical_name": "S", "status": "active", "folder_name": "s", "first_seen": "2026-01-01"}]})
    rec = tw.update_thread(ws, "project_001", stage="active", source_skill="test")  # legacy string stage
    check("legacy string stage coerced to None", rec.get("stage") is None)


def test_roster_overrides_allowed():
    ws = _ws({"threads": [{"id": "project_001", "canonical_name": "R", "status": "active", "folder_name": "r", "first_seen": "2026-01-01"}]})
    rec = tw.update_thread(ws, "project_001", roster_overrides={"pin": ["person_9"], "suppress": []}, source_skill="test")
    check("roster_overrides persists", rec.get("roster_overrides", {}).get("pin") == ["person_9"])


def test_forbidden_field_rejected():
    try:
        tw._validate_thread({"id": "project_001", "status": "active", "name": "should be canonical_name"})
        check("forbidden 'name' field rejected", False)
    except ValueError:
        check("forbidden 'name' field rejected", True)


def main():
    for t in [test_create_nested, test_create_flat_and_id_increment, test_dedup_raises,
              test_bad_status_raises, test_real_world_status_accepted, test_string_stage_coerced,
              test_roster_overrides_allowed, test_forbidden_field_rejected]:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            results["fail"] += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"=== {results['pass']} passed, {results['fail']} failed ===")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
