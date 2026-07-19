#!/usr/bin/env python3
"""FB-5 regression (T3 fix bundle) — bridge migration skips are DURABLE.

THE BUG (confirmed live 2026-07-16): the operator skipped the
`draft_posture_queue_on_click_v1` migration; the skip was logged as a
`workspace_migration_skipped` event, but the apply-once trigger gates decided
"already adjudicated?" by marker-phrase matching + enumerating ONLY the
`workspace_migration_applied` event — the skip event was never consulted, so
every subsequent bridge run re-proposed the same migration forever.

THE FIX: `shared/scripts/migration_adjudication.py` — a mechanized adjudication
gate keyed on the MIGRATION ID (never marker phrases). A logged adjudication
(applied OR skipped/declined) suppresses "pending" on all future runs; only the
documented deliberately-re-surface skip reasons (user_deferred,
awaiting_manual_apply, structural_mismatch*) do not suppress.

Fixture event shapes MIRROR the live substrate (per the real-data fixture
gotcha): both the top-level `migration_id` shape and the `data`-nested shape
exist in real workspaces, plus malformed lines the reader must tolerate.
All fixture timestamps are computed relative to today (G14 — never hardcode
today-or-future dates).

Run via: python3 tests/run_migration_adjudication_test.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import migration_adjudication as ma  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")


def _ts(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def write_events(dirpath: Path, lines: list) -> Path:
    """Write a fixture events.jsonl under <dirpath>/_hq/data/ (workspace shape)."""
    data_dir = dirpath / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "events.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln if isinstance(ln, str) else json.dumps(ln))
            fh.write("\n")
    return path


# ------------------------------------------------------ 1. the FB-5 live repro
print("1. FB-5 repro — a logged skip suppresses re-proposal on the next run")
with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    # Live record shape verbatim (top-level migration_id, operator-authored
    # free-form decline reason) — content anonymized.
    write_events(ws, [
        {"type": "workspace_migration_skipped",
         "source_skill": "command-room-update-bridge",
         "migration_id": "draft_posture_queue_on_click_v1",
         "reason": "content_already_current_operator_declined",
         "actor": "command-room-update-bridge",
         "seq": 4312, "ts": _ts(2)},
    ])
    rep = ma.adjudication_status(ws, ["draft_posture_queue_on_click_v1"])
    rec = rep["draft_posture_queue_on_click_v1"]
    check("skipped migration reports status 'skipped'", rec["status"] == "skipped")
    check("skipped-as-declined migration is SUPPRESSED (not pending) on a later run",
          rec["suppressed"] is True)
    check("is_suppressed() agrees",
          ma.is_suppressed(ws, "draft_posture_queue_on_click_v1") is True)
    # `redo workspace migrations` path — the deliberate opt-back-in
    rep_redo = ma.adjudication_status(
        ws, ["draft_posture_queue_on_click_v1"], honor_skips=False)
    check("honor_skips=False (redo flow) un-suppresses the skip",
          rep_redo["draft_posture_queue_on_click_v1"]["suppressed"] is False)

# --------------------------------------------- 2. both live event shapes fold
print("2. Event-shape tolerance — top-level AND data-nested migration_id")
with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    write_events(ws, [
        # data-nested shape (current bridge fires)
        {"type": "workspace_migration_skipped",
         "source_skill": "command-room-update-bridge",
         "data": {"migration_id": "sample_migration_nested_v1",
                  "reason": "user_declined",
                  "note": "Sam Sample said no on the Acme workspace"},
         "seq": 10, "ts": _ts(30)},
        # applied event, data-nested with extra fields (live shape)
        {"type": "workspace_migration_applied",
         "source_skill": "command-room-update-bridge",
         "data": {"migration_id": "sample_migration_applied_v1",
                  "target_file": "CLAUDE.md", "from_version": "4.5.1",
                  "to_version": "4.8.0",
                  "actor": "command-room-update-bridge"},
         "seq": 11, "ts": _ts(29)},
    ])
    rep = ma.adjudication_status(ws, ["sample_migration_nested_v1",
                                      "sample_migration_applied_v1",
                                      "never_adjudicated_v1"])
    check("data-nested skip folds and suppresses",
          rep["sample_migration_nested_v1"]["status"] == "skipped"
          and rep["sample_migration_nested_v1"]["suppressed"] is True)
    check("applied event suppresses",
          rep["sample_migration_applied_v1"]["status"] == "applied"
          and rep["sample_migration_applied_v1"]["suppressed"] is True)
    check("applied stays suppressed even on the redo flow",
          ma.adjudication_status(ws, ["sample_migration_applied_v1"],
                                 honor_skips=False)
          ["sample_migration_applied_v1"]["suppressed"] is True)
    check("an id with no adjudication reports 'unadjudicated', not suppressed",
          rep["never_adjudicated_v1"]["status"] == "unadjudicated"
          and rep["never_adjudicated_v1"]["suppressed"] is False)

# ------------------------------------------- 3. re-surface reasons still work
print("3. Deliberate re-surface skip reasons do NOT suppress")
with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    write_events(ws, [
        {"type": "workspace_migration_skipped",
         "data": {"migration_id": "deferred_migration_v1",
                  "reason": "user_deferred"}, "ts": _ts(10)},
        {"type": "workspace_migration_skipped",
         "data": {"migration_id": "structural_migration_v1",
                  "reason": "structural_mismatch"}, "ts": _ts(10)},
        {"type": "workspace_migration_skipped",
         "data": {"migration_id": "awaiting_migration_v1",
                  "reason": "awaiting_manual_apply"}, "ts": _ts(10)},
    ])
    rep = ma.adjudication_status(ws, ["deferred_migration_v1",
                                      "structural_migration_v1",
                                      "awaiting_migration_v1"])
    check("user_deferred re-surfaces",
          rep["deferred_migration_v1"]["suppressed"] is False)
    check("structural_mismatch re-surfaces",
          rep["structural_migration_v1"]["suppressed"] is False)
    check("awaiting_manual_apply re-surfaces",
          rep["awaiting_migration_v1"]["suppressed"] is False)

# ----------------------------------------------------- 4. latest event wins
print("4. Latest adjudication per id wins (append-only fold)")
with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    write_events(ws, [
        {"type": "workspace_migration_skipped",
         "data": {"migration_id": "flip_migration_v1",
                  "reason": "user_deferred"}, "ts": _ts(20)},
        {"type": "workspace_migration_skipped",
         "data": {"migration_id": "flip_migration_v1",
                  "reason": "user_declined"}, "ts": _ts(5)},
        {"type": "workspace_migration_skipped",
         "data": {"migration_id": "then_applied_v1",
                  "reason": "user_declined"}, "ts": _ts(20)},
        {"type": "workspace_migration_applied",
         "data": {"migration_id": "then_applied_v1"}, "ts": _ts(5)},
    ])
    rep = ma.adjudication_status(ws, ["flip_migration_v1", "then_applied_v1"])
    check("deferred-then-declined resolves to the later decline (suppressed)",
          rep["flip_migration_v1"]["suppressed"] is True
          and rep["flip_migration_v1"]["reason"] == "user_declined")
    check("skipped-then-applied resolves to applied",
          rep["then_applied_v1"]["status"] == "applied")

# ------------------------------------------ 5. defensive reads (real substrate)
print("5. Defensive reader — malformed lines and missing files never crash")
with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    write_events(ws, [
        '{"type": "workspace_migration_skipped", "data": {"migration_id": "torn',  # torn line
        '"keys_only_line"',                                                        # keys-only junk
        "",                                                                        # blank
        '{"type": "workspace_migration_skipped"}',                                 # no migration_id
        {"type": "workspace_migration_skipped",
         "data": {"migration_id": "survivor_migration_v1",
                  "reason": "user_declined"}, "ts": _ts(3)},
    ])
    rep = ma.adjudication_status(ws, ["survivor_migration_v1"])
    check("valid adjudication still folds despite malformed neighbors",
          rep["survivor_migration_v1"]["suppressed"] is True)
with tempfile.TemporaryDirectory() as td:
    rep = ma.adjudication_status(Path(td), ["anything_v1"])
    check("missing events.jsonl → unadjudicated, no crash",
          rep["anything_v1"]["status"] == "unadjudicated")

# --------------------------------------------------------------- 6. CLI smoke
print("6. CLI — the shape the bridge shells into")
with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    write_events(ws, [
        {"type": "workspace_migration_skipped",
         "migration_id": "draft_posture_queue_on_click_v1",
         "reason": "content_already_current_operator_declined", "ts": _ts(1)},
    ])
    proc = subprocess.run(
        [sys.executable,
         str(ROOT / "shared" / "scripts" / "migration_adjudication.py"),
         str(ws), "draft_posture_queue_on_click_v1", "missing_one_v1"],
        capture_output=True, text=True)
    check("CLI exits 0", proc.returncode == 0, proc.stderr.strip()[:200])
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = {}
    check("CLI reports the skip suppressed",
          out.get("draft_posture_queue_on_click_v1", {}).get("suppressed") is True)
    check("CLI reports the unknown id unadjudicated",
          out.get("missing_one_v1", {}).get("status") == "unadjudicated")
    proc2 = subprocess.run(
        [sys.executable,
         str(ROOT / "shared" / "scripts" / "migration_adjudication.py"),
         str(ws), "--ignore-skips", "draft_posture_queue_on_click_v1"],
        capture_output=True, text=True)
    out2 = json.loads(proc2.stdout) if proc2.returncode == 0 else {}
    check("CLI --ignore-skips un-suppresses (redo flow)",
          out2.get("draft_posture_queue_on_click_v1", {}).get("suppressed") is False)

# -------------------------------------- 7. instruction-layer wiring (G13 class)
print("7. Bridge SKILL.md wires the helper (instruction-layer gap guard)")
bridge = (ROOT / "skills" / "command-room-update-bridge" / "SKILL.md").read_text(
    encoding="utf-8")
check("SKILL.md references migration_adjudication.py",
      "migration_adjudication.py" in bridge)
check("detection logic carries the adjudication gate before marker checks",
      "Adjudication gate runs FIRST" in bridge)
check("the buggy applied-only gate wording is gone",
      "AND no `workspace_migration_applied` event with this migration id exists"
      not in bridge
      and "AND events.jsonl has no `workspace_migration_applied` event with this `migration_id`"
      not in bridge)
check("apply-once bullet now names the suppressing skipped event",
      "NO suppressing `workspace_migration_skipped` event" in bridge)
check("redo flow documents --ignore-skips (skips never deleted)",
      "--ignore-skips" in bridge)

print()
print(f"Passed: {PASS}")
print(f"Failed: {FAIL}")
sys.exit(1 if FAIL else 0)
