#!/usr/bin/env python3
"""Tests for release_actions/migrate_brain_live_state.py.

The migration's whole safety case: it converts the hand People table to the
generated block, NEVER deletes a hand-written person (eventless durable rows
like a framework author are relocated, not dropped), preserves all durable
content, is dry-run by default, and is idempotent.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))
sys.path.insert(0, str(ROOT / "shared" / "scripts" / "release_actions"))

from migrate_brain_live_state import migrate_brain  # noqa: E402

results = {"pass": 0, "fail": 0}


def check(name, cond):
    if cond:
        results["pass"] += 1; print(f"PASS {name}")
    else:
        results["fail"] += 1; print(f"FAIL {name}")


BRAIN = """# Plugin Brain

## Durable judgment (hand-owned)
Never touch this. Special: é → `code`.

## 1. People

| Name | Role | Notes |
|------|------|-------|
| Active Person | builder | has events |
| Geoff Woods | author | Not a contact — his book is the framework |

## 2. Gotchas
keep me exactly
"""


def _ws():
    ws = Path(tempfile.mkdtemp(prefix="cr-migrate-test-"))
    d = ws / "_hq" / "data"; d.mkdir(parents=True)
    people = [{"id": "person_1", "canonical_name": "Active Person"},
              {"id": "person_9", "canonical_name": "Geoff Woods"}]
    threads = [{"id": "project_t2", "canonical_name": "Plugin", "status": "active", "folder_name": "Plugin"}]
    (d / "entities.json").write_text(json.dumps({"people": people, "threads": threads}), encoding="utf-8")
    # Active Person has 2 direct events (high); Geoff has NONE.
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"seq": 1, "ts": "2026-05-01", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
        {"seq": 2, "ts": "2026-05-02", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
    ]), encoding="utf-8")
    bp = ws / "Plugin"; bp.mkdir()
    brain = bp / "PROJECT_BRAIN.md"
    brain.write_text(BRAIN, encoding="utf-8")
    return ws, brain


def test_dry_run_does_not_write():
    ws, brain = _ws()
    before = brain.read_text(encoding="utf-8")
    r = migrate_brain(ws, "project_t2", brain, dry_run=True)
    check("dry-run reports changed", r["changed"] is True)
    check("dry-run wrote nothing", brain.read_text(encoding="utf-8") == before)
    check("Geoff flagged for preservation", "Geoff Woods" in r["unmatched_preserved"])
    check("Active Person NOT in preserved (it's in the generated block)", "Active Person" not in r["unmatched_preserved"])


def test_apply_preserves_everyone_and_durable():
    ws, brain = _ws()
    migrate_brain(ws, "project_t2", brain, dry_run=False)
    text = brain.read_text(encoding="utf-8")
    check("generated block created", "<!-- LIVE-STATE:people" in text)
    check("active person in generated block", "Active Person" in text)
    check("eventless hand person PRESERVED (not deleted)", "Geoff Woods" in text)
    check("preserved under manually-tracked note", "Manually tracked" in text)
    check("durable header preserved", "## Durable judgment (hand-owned)" in text)
    check("durable special chars preserved", "é → `code`" in text)
    check("trailing gotchas preserved", "## 2. Gotchas\nkeep me exactly" in text)


def test_idempotent():
    ws, brain = _ws()
    migrate_brain(ws, "project_t2", brain, dry_run=False)
    r2 = migrate_brain(ws, "project_t2", brain, dry_run=False)
    check("second migrate is noop", r2["changed"] is False and r2["status"] == "noop")


def main():
    test_dry_run_does_not_write()
    test_apply_preserves_everyone_and_durable()
    test_idempotent()
    print(f"=== {results['pass']} passed, {results['fail']} failed ===")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
