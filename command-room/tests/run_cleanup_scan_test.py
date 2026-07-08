#!/usr/bin/env python3
"""Tests for the SPEC CLEAN1 cleanup upgrades (v3.19.x).

Builds a synthetic workspace planted with the forensic failure classes that
survived five real cleanup runs, and asserts a single simulated weekly pass
SURFACES or FIXES each one with zero false-negatives:

  - orphan folder (flagged, never deleted)
  - a project folder with a brain but no session notes  -> C11b.missing_session_notes
  - a project folder with notes but no brain            -> C11.missing_brain
  - a stale DECISION_LOG                                 -> regenerated (changed-only)
  - a >1h-old `.lock.stale.` sentinel                    -> archived to _archive/stale-locks/ (and a 10-min one kept in place)
  - a stale analytical view                              -> flagged, owner = insight-generator

Then re-runs the whole pass and asserts ZERO writes (idempotence, acceptance #7).

CLIENT SAFETY is asserted directly: orphans are flagged not removed, existing
session notes are never overwritten, and the substrate (entities.json /
events.jsonl) is never rewritten.

stdlib only; non-zero exit = fail (house convention).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import integrity_check  # noqa: E402
import cleanup_actions as ca  # noqa: E402

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


# Fixed reference time so lock-age + view-staleness assertions are deterministic.
NOW = 1_750_000_000.0
DAY = 86400.0


def _set_mtime(p: Path, epoch: float) -> None:
    os.utime(p, (epoch, epoch))


def _build_workspace(tmp: Path) -> Path:
    data = tmp / "_hq" / "data"
    data.mkdir(parents=True)
    (tmp / "_hq" / "views").mkdir(parents=True)
    (tmp / "_hq" / ".system").mkdir(parents=True)

    entities = {
        "version": 5, "last_updated": "2026-06-14", "last_writer": "test",
        "orgs": [{"id": "org_acme", "canonical_name": "Acme"}],
        "people": [{"id": "person_001", "canonical_name": "Jo", "first_seen": "2026-01-01"}],
        "threads": [
            {"id": "project_brain", "folder_name": "Brain Only", "status": "active",
             "first_seen": "2026-01-01", "affiliation_id": "org_acme"},
            {"id": "project_notes", "folder_name": "Notes Only", "status": "active",
             "first_seen": "2026-01-01", "affiliation_id": "org_acme"},
        ],
        "engagements": [],
    }
    (data / "entities.json").write_text(json.dumps(entities), encoding="utf-8")

    events = [
        {"seq": 1, "type": "note", "data": {"project_id": "project_brain"}},
        {"seq": 2, "type": "decision",
         "data": {"title": "Go with vendor B", "decided_by": "person_001",
                  "decided_at": "2026-06-10", "rationale": "Best price/perf."}},
    ]
    (data / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (data / "aliases.json").write_text(
        json.dumps({"mappings": [{"raw": "acme", "canonical_id": "org_acme"}]}),
        encoding="utf-8")

    # --- disk folders -------------------------------------------------------
    # Brain Only: has a brain, NO session notes -> C11b.missing_session_notes
    bo = tmp / "Brain Only"
    bo.mkdir()
    (bo / "PROJECT_BRAIN.md").write_text("brain", encoding="utf-8")

    # Notes Only: has notes, NO brain -> C11.missing_brain (and never overwritten)
    no = tmp / "Notes Only"
    no.mkdir()
    (no / "SESSION_NOTES_Notes Only.md").write_text("REAL HAND NOTES", encoding="utf-8")

    # Orphan Co: a folder with no thread record -> C10.orphan_folder
    orphan = tmp / "Orphan Co"
    orphan.mkdir()
    (orphan / "PROJECT_CONTEXT.md").write_text("ctx", encoding="utf-8")

    # --- stale DECISION_LOG -------------------------------------------------
    (tmp / "_hq" / "views" / "DECISION_LOG.md").write_text(
        "# Decision Log\n\nstale — last regenerated weeks ago\n", encoding="utf-8")

    # --- stale analytical view (TIMELINE) -----------------------------------
    timeline = tmp / "_hq" / "views" / "TIMELINE.md"
    timeline.write_text("# Timeline\n\nold snapshot\n", encoding="utf-8")

    # --- stale ALIASES view (D7) --------------------------------------------
    aliases_view = tmp / "_hq" / "views" / "ALIASES.md"
    aliases_view.write_text("# Aliases\n\nold\n", encoding="utf-8")

    # --- lock sentinels -----------------------------------------------------
    old_lock = data / "entities.json.lock.stale.1700000000.111"
    old_lock.write_text("pid=111", encoding="utf-8")
    fresh_lock = data / "events.jsonl.lock.stale.1700000500.222"
    fresh_lock.write_text("pid=222", encoding="utf-8")
    sys_lock = (tmp / "_hq" / ".system" / "schedule.json.lock.stale.1700000001.333")
    sys_lock.write_text("pid=333", encoding="utf-8")

    # --- force mtimes deterministically -------------------------------------
    for f in (data / "entities.json", data / "events.jsonl", data / "aliases.json"):
        _set_mtime(f, NOW)
    _set_mtime(timeline, NOW - 40 * DAY)       # stale: older than substrate
    _set_mtime(aliases_view, NOW - 40 * DAY)   # stale vs aliases.json
    _set_mtime(old_lock, NOW - 2 * 3600)       # 2h old -> sweep
    _set_mtime(sys_lock, NOW - 3 * 3600)       # 3h old -> sweep
    _set_mtime(fresh_lock, NOW - 600)          # 10 min old -> keep
    return tmp


def _snapshot(root: Path) -> dict:
    """Map of relpath -> (size, mtime_ns) for every file under root. Used to
    prove a re-run made zero writes (idempotence)."""
    snap = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


def _run_pass(root: Path) -> dict:
    """Simulate one weekly cleanup pass over the CLEAN1 surfaces. Returns a
    findings/actions dict the assertions inspect."""
    scan = integrity_check.scan_project_structure(root)
    by = {(f.check, f.subject) for f in scan}

    # Backfill every folder flagged missing session notes (D3).
    backfilled = []
    for f in scan:
        if f.check == "C11b.missing_session_notes":
            created = ca.backfill_session_notes(root, f.subject, today="2026-06-14")
            if created:
                backfilled.append(created)

    locks = ca.sweep_stale_locks(root, now=NOW)
    decision = ca.regenerate_decision_log_if_changed(root)
    stale_views = ca.check_analytical_view_staleness(root, now=NOW)
    insight = ca.insight_generator_staleness(root, now=NOW)
    aliases = ca.check_aliases_staleness(root)
    return {
        "scan_keys": by, "backfilled": backfilled, "locks": locks,
        "decision": decision, "stale_views": stale_views, "insight": insight,
        "aliases": aliases,
    }


def main() -> int:
    print("=== SPEC CLEAN1 cleanup scan + remediation ===\n")
    with tempfile.TemporaryDirectory() as d:
        root = _build_workspace(Path(d))

        r = _run_pass(root)

        # --- D1: scanner finds the structural classes ----------------------
        check("orphan folder flagged",
              ("C10.orphan_folder", "Orphan Co") in r["scan_keys"], r["scan_keys"])
        check("missing brain flagged (Notes Only)",
              ("C11.missing_brain", "Notes Only") in r["scan_keys"], r["scan_keys"])
        check("missing session notes flagged (Brain Only)",
              ("C11b.missing_session_notes", "Brain Only") in r["scan_keys"], r["scan_keys"])

        # --- D3: backfill scaffold created, with provenance ----------------
        notes_path = root / "Brain Only" / "SESSION_NOTES_Brain Only.md"
        check("session-notes scaffold created", notes_path.is_file())
        if notes_path.is_file():
            body = notes_path.read_text(encoding="utf-8")
            check("scaffold carries backfill provenance line",
                  "Backfilled by cleanup on 2026-06-14" in body, body[:200])
            check("scaffold has Current Status section", "## Current Status" in body)

        # CLIENT SAFETY: existing notes are NEVER overwritten.
        no_path = root / "Notes Only" / "SESSION_NOTES_Notes Only.md"
        res = ca.backfill_session_notes(root, "Notes Only", today="2026-06-14")
        check("backfill refuses to overwrite existing notes", res is None, res)
        check("existing notes content untouched",
              no_path.read_text(encoding="utf-8") == "REAL HAND NOTES")

        # --- D6: lock sweep -------------------------------------------------
        check(">1h stale lock (data) swept",
              "_hq/data/entities.json.lock.stale.1700000000.111" in r["locks"], r["locks"])
        check(">1h stale lock (.system) swept",
              "_hq/.system/schedule.json.lock.stale.1700000001.333" in r["locks"], r["locks"])
        check("fresh (10-min) lock preserved",
              (root / "_hq" / "data" / "events.jsonl.lock.stale.1700000500.222").is_file())
        # archive-only policy: the aged sentinel is MOVED, never deleted — it now
        # lives under _archive/stale-locks/, mirroring its original path.
        check(">1h stale lock archived (not deleted)",
              (root / "_archive" / "stale-locks" / "_hq" / "data"
               / "entities.json.lock.stale.1700000000.111").is_file())

        # --- D4: DECISION_LOG regenerated -----------------------------------
        check("decision log regenerated (changed)", r["decision"].get("changed") is True, r["decision"])
        dl = (root / "_hq" / "views" / "DECISION_LOG.md").read_text(encoding="utf-8")
        check("decision log reflects substrate decision", "Go with vendor B" in dl)
        check("decision log resolves person id to name", "person_001" not in dl and "Jo" in dl)

        # --- D5: analytical-view staleness + insight nudge ------------------
        timeline_flag = next((v for v in r["stale_views"] if v["view"] == "TIMELINE.md"), None)
        check("stale analytical view flagged", timeline_flag is not None, r["stale_views"])
        check("stale view names insight-generator as owner",
              timeline_flag and timeline_flag["owner"] == "insight-generator", timeline_flag)
        check("insight-generator-not-firing nudge fires (>14d / never)",
              r["insight"].get("stale") is True, r["insight"])

        # --- D7: ALIASES safety net (flag, owner people-crm) ----------------
        check("stale ALIASES view flagged", r["aliases"] is not None, r["aliases"])
        check("ALIASES flag names people-crm as owner",
              r["aliases"] and r["aliases"]["owner"] == "people-crm", r["aliases"])

        # CLIENT SAFETY: substrate never rewritten by the pass.
        ent_after = json.loads((root / "_hq" / "data" / "entities.json").read_text("utf-8"))
        check("entities.json never rewritten (orphan not removed, no thread added)",
              len(ent_after["threads"]) == 2)
        check("orphan folder still on disk (flagged, not deleted)",
              (root / "Orphan Co").is_dir())

        # --- Acceptance #7: idempotence — a re-run makes ZERO writes --------
        snap_before = _snapshot(root)
        r2 = _run_pass(root)
        snap_after = _snapshot(root)
        check("re-run is a no-op write (idempotent)", snap_before == snap_after,
              {"added": sorted(set(snap_after) - set(snap_before)),
               "removed": sorted(set(snap_before) - set(snap_after)),
               "changed": sorted(k for k in snap_before
                                 if k in snap_after and snap_before[k] != snap_after[k])})
        check("re-run backfills nothing", r2["backfilled"] == [], r2["backfilled"])
        check("re-run sweeps no locks", r2["locks"] == [], r2["locks"])
        check("re-run decision log unchanged", r2["decision"].get("changed") is False, r2["decision"])

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
