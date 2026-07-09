#!/usr/bin/env python3
"""
Session backfill (Phase 5 Memory layer, R2) — one-time supervised historical
sweep. Exercises the preview + backfill helpers in shared/scripts/session_sweep.py
against a real-shape workspace copy (fixtures/workspace_mini).

Regresses the R2 guarantees:
  - preview_items dedup-checks WITHOUT writing (events.jsonl byte-identical);
  - backfill_and_receipt snapshots events.jsonl to _archive/ BEFORE appending
    (archive-never-delete / snapshot-before-touch), then writes via the same
    dedup+append path as the nightly sweep and lands a session_backfill_run
    receipt carrying the 60-day window;
  - re-running is idempotent (preview after a backfill shows zero new);
  - the snapshot is a faithful copy of the pre-backfill history.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from output_exercise_lib import copy_fixture  # noqa: E402
import session_sweep as ss  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def _events_text(ws: Path) -> str:
    return (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")


def _events(ws: Path) -> list[dict]:
    return [json.loads(l) for l in _events_text(ws).splitlines() if l.strip()]


ITEMS = [
    {"session_id": "h1", "type": "commitment", "summary": "Owe Priya the Q2 numbers",
     "data": {"kind": "promise", "due": "2026-05-02", "owner_id": "person_001",
              "counterparty_id": "person_042"}, "person_ids": ["person_042"]},
    {"session_id": "h1", "type": "decision", "summary": "Sunset the legacy onboarding flow"},
    {"session_id": "h2", "type": "interaction", "summary": "Reviewed the roadmap with Ken",
     "data": {"channel": "session"}},
    {"session_id": "h2", "type": "note", "summary": "Wrote the Q2 board narrative",
     "data": {"recovered_kind": "deliverable"}},
]


def test_preview_writes_nothing():
    print("test_preview_writes_nothing")
    ws = copy_fixture()
    before = _events_text(ws)
    plan = ss.preview_items(ws, ITEMS)
    check("preview counts all four as recoverable", plan["would_recover"] == 4, repr(plan))
    check("preview reports by_type",
          plan["by_type"] == {"commitment": 1, "decision": 1, "interaction": 1, "note": 1},
          repr(plan["by_type"]))
    check("preview returns a sample", len(plan["sample_summaries"]) == 4)
    check("preview wrote NOTHING (events.jsonl byte-identical)", _events_text(ws) == before)


def test_backfill_snapshots_then_writes():
    print("test_backfill_snapshots_then_writes")
    ws = copy_fixture()
    pre = _events(ws)
    pre_text = _events_text(ws)

    r = ss.backfill_and_receipt(ws, ITEMS, days=60, sessions_scanned=2)
    check("4 recovered", r["events_recovered"] == 4, repr(r))

    # Snapshot exists under _archive/ and equals the PRE-backfill history.
    snap = Path(r["snapshot"])
    check("snapshot lives under _archive/", "_archive" in snap.parts, str(snap))
    check("snapshot is the pre-backfill history verbatim",
          snap.read_text(encoding="utf-8") == pre_text)

    post = _events(ws)
    check("history grew by 4 items + 1 receipt", len(post) == len(pre) + 5, f"{len(pre)}->{len(post)}")
    receipt = post[-1]
    check("receipt is session_backfill_run", receipt["type"] == "session_backfill_run", receipt["type"])
    check("receipt records the 60-day window", receipt["data"].get("days") == 60, repr(receipt["data"]))
    check("receipt records events_recovered", receipt["data"].get("events_recovered") == 4)
    check("receipt links the snapshot", receipt["data"].get("snapshot") == str(snap))

    # Recovered items carry the session provenance ref + backfill source_skill.
    added = [e for e in post[len(pre):] if e["type"] in ss.SWEEPABLE_TYPES]
    check("recovered items sourced from session-backfill",
          all(e.get("source_skill") == "session-backfill" for e in added), repr([e.get("source_skill") for e in added]))
    check("recovered commitment got an id + kind + title",
          any(e["type"] == "commitment" and str(e["data"].get("id", "")).startswith("cmt_")
              and e["data"].get("kind") == "promise" and e["data"].get("title") for e in added))

    v = ss.validate_backfill_ran(ws)
    check("validate_backfill_ran confirms the run", v["ran"] and v["ok"] and v["events_recovered"] == 4, repr(v))


def test_idempotent_rerun():
    print("test_idempotent_rerun")
    ws = copy_fixture()
    ss.backfill_and_receipt(ws, ITEMS, days=60, sessions_scanned=2)
    plan2 = ss.preview_items(ws, ITEMS)
    check("preview after a backfill shows zero new (idempotent)", plan2["would_recover"] == 0, repr(plan2))
    check("all four counted as already-logged", plan2["skipped_dedup"] == 4, repr(plan2))
    r2 = ss.backfill_and_receipt(ws, ITEMS, days=60, sessions_scanned=2)
    check("second backfill recovers nothing", r2["events_recovered"] == 4 - 4, repr(r2))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== session backfill (Phase 5 / R2) ===")
    test_preview_writes_nothing()
    test_backfill_snapshots_then_writes()
    test_idempotent_rerun()
    print()
    if FAIL:
        print(f"FAIL — {FAIL} of {PASS + FAIL} checks failed")
        return 1
    print(f"OK — all {PASS} session-backfill checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
