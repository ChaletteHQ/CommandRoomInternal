#!/usr/bin/env python3
"""Test for integrity_check C16 — Live State block staleness (brain-substrate
fix). A rendered people block older than the newest thread-tagged event means
the render trigger didn't fire; C16 makes that fail loudly. Read-only check.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import integrity_check  # noqa: E402

results = {"pass": 0, "fail": 0}


def check(name, cond):
    if cond:
        results["pass"] += 1; print(f"PASS {name}")
    else:
        results["fail"] += 1; print(f"FAIL {name}")


def _ws(block_seq: int, newest_event_seq: int) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="cr-c16-test-"))
    d = ws / "_hq" / "data"; d.mkdir(parents=True)
    (d / "entities.json").write_text(json.dumps({
        "people": [], "orgs": [],
        "threads": [{"id": "project_t2", "canonical_name": "Plugin", "status": "active",
                     "folder_name": "Plugin", "first_seen": "2026-01-01"}],
    }), encoding="utf-8")
    (d / "events.jsonl").write_text(
        json.dumps({"seq": newest_event_seq, "ts": "2026-05-10",
                    "type": "meeting", "primary_thread_id": "project_t2",
                    "person_ids": ["person_1"]}) + "\n", encoding="utf-8")
    (d / "aliases.json").write_text(json.dumps({"mappings": []}), encoding="utf-8")
    folder = ws / "Plugin"; folder.mkdir()
    (folder / "PROJECT_BRAIN.md").write_text(
        f"# Plugin\n\n## 1. People\n<!-- LIVE-STATE:people source_seq={block_seq} -->\nx\n<!-- /LIVE-STATE:people -->\n",
        encoding="utf-8")
    (folder / "PROJECT_CONTEXT.md").write_text("ctx", encoding="utf-8")
    return ws


def test_stale_block_flagged():
    ws = _ws(block_seq=5, newest_event_seq=10)
    findings = integrity_check.run_checks(ws)
    check("C16 fires when block is stale", any(f.check == "C16.live_state_stale" for f in findings))


def test_fresh_block_not_flagged():
    ws = _ws(block_seq=10, newest_event_seq=10)
    findings = integrity_check.run_checks(ws)
    check("C16 silent when block is current", not any(f.check == "C16.live_state_stale" for f in findings))


def main():
    for t in (test_stale_block_flagged, test_fresh_block_not_flagged):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            results["fail"] += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"=== {results['pass']} passed, {results['fail']} failed ===")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
