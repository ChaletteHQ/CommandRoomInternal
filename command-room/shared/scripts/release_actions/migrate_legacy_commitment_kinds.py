#!/usr/bin/env python3
"""POLICY1-A D12(a) — legacy `kind: None` commitment rows get a kind.

WHAT IT DOES
------------
Commitments written before the Phase 1 gate carry no `data.kind` (402 on the
operator's ledger; 2 of them open today). Every reader defaults them to
`promise` (`commitment_state.KIND_DEFAULT`), which is right for most and
wrong for the self-owed tasks and the "set up a call" rows among them —
and a history reader (the value receipt, the replay, the plate's counts by
kind) has no way to tell. This action appends ONE additive
`commitment_reclassified` marker per legacy row, carrying the kind the
capture-side rules would have given it:

  scheduling  the title carries scheduling intent
              (`cru_match.detect_scheduling_intent` — the same predicate the
              capture gate and the calendar closer read)
  task        self-owed with no counterparty signal — owner is the primary
              user and nothing ties a second party to it (the S6 partition
              rule `migrate_commitment_kinds._has_counterparty_signal`)
  promise     everything else (the default every reader already applied)

The original event is NEVER rewritten (DEVELOPMENT.md:56); the projector
applies the marker read-side exactly as it applies a triage `make task`.
A row that already carries a kind, or already has a marker, is skipped —
idempotent by construction. Closed rows are included: this is a HISTORY
migration (D12(a): "for history readers; PLATE1's open-row need is already
met"), so counts over the ledger stop defaulting.

`pending_review` rows are NOT skipped here (unlike the S6 open-set
partition): a kind label does not confirm anything, and the marker is what
lets the review surfaces show the right shape.

MANIFEST TRANSPORT
------------------
`action: auto_apply` — additive, reversible (the markers carry one
`brain_batch_id`; a future reverser can retract them), no data loss. The
manifest item JSON lives in the BUILD record (no manifest file ships on the
branch — the release cut adds it). Detector: `release_detectors.always`.

Receipt: one `commitment_reclassified` per row + the returned context
{n_scanned, n_marked, n_scheduling, n_task, n_promise, n_skipped,
batch_id}. Nothing is written when nothing qualifies (ran=False).
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))  # shared/scripts on path

MIGRATION_SOURCE_SKILL = "kind-migration-2026-09"
BATCH_PREFIX = "kmg_"
MARKER_REASON = "POLICY1-A D12(a) legacy kind: None — classified by the capture rules"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def classify_legacy_kind(ev: dict, user_id) -> str:
    """The capture-side rules, applied to a legacy row. Pure."""
    from cru_match import _commitment_field, detect_scheduling_intent
    from migrate_commitment_kinds import _has_counterparty_signal
    title = _commitment_field(ev, "title") or ""
    if detect_scheduling_intent(title):
        return "scheduling"
    owner = _commitment_field(ev, "owner_id")
    if user_id and owner == user_id and not _has_counterparty_signal(ev, user_id):
        return "task"
    return "promise"


def plan(workspace_root) -> dict:
    """Pure analysis — reads through events_io, writes nothing."""
    from events_io import load_events_owner_scoped
    from cru_match import _commitment_id
    from primary_user import resolve_primary_user

    user_id = resolve_primary_user(workspace_root)
    # The OWNER-TIER door (the D4c firewall): the migration labels the
    # operator's own history, so it reads everything — through the
    # allowlisted shard reader, never a new raw-read site.
    events, _skipped = load_events_owner_scoped(workspace_root)
    already_marked: set = set()
    already_marked_seqs: set = set()
    rows: list = []
    for ev in events:
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et == "commitment_reclassified":
            t = d.get("target_id") or d.get("commitment_id")
            if t:
                already_marked.add(str(t))
            if isinstance(d.get("target_seq"), int):
                already_marked_seqs.add(d["target_seq"])
        elif et == "commitment":
            kind = d.get("kind")
            if isinstance(kind, str) and kind.strip():
                continue
            rows.append(ev)
    out = {"user_id": user_id, "n_scanned": len(rows), "to_mark": [], "n_skipped": 0,
           "n_scheduling": 0, "n_task": 0, "n_promise": 0}
    for ev in rows:
        cid = _commitment_id(ev)
        seq = ev.get("seq")
        if cid in already_marked or (isinstance(seq, int) and seq in already_marked_seqs):
            out["n_skipped"] += 1
            continue
        kind = classify_legacy_kind(ev, user_id)
        out[f"n_{kind}"] += 1
        out["to_mark"].append({"target_id": cid, "target_seq": seq if isinstance(seq, int) else None,
                               "new_kind": kind})
    return out


def apply_plan(workspace_root, planned: dict, *, batch_id=None) -> dict:
    from event_gate import append_event
    from datetime import datetime, timezone
    import secrets
    batch_id = batch_id or (BATCH_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                            + "-" + secrets.token_hex(4))
    rows = []
    for r in planned.get("to_mark") or []:
        data = {"target_id": r["target_id"], "new_kind": r["new_kind"],
                "reason": MARKER_REASON, "brain_batch_id": batch_id,
                "legacy_kind_none": True}
        if isinstance(r.get("target_seq"), int):
            data["target_seq"] = r["target_seq"]
        rows.append({"type": "commitment_reclassified",
                     "source_skill": MIGRATION_SOURCE_SKILL, "data": data})
    if rows:
        append_event(_events_path(workspace_root), rows, holder=MIGRATION_SOURCE_SKILL)
    return {"markers_written": len(rows), "batch_id": batch_id}


def migrate_legacy_commitment_kinds(events_jsonl_path, workspace_root, detector_context) -> dict:
    """The `auto_apply` action contract (release_actions/__init__.py)."""
    try:
        planned = plan(workspace_root)
        ctx = {k: planned[k] for k in ("n_scanned", "n_skipped", "n_scheduling",
                                       "n_task", "n_promise")}
        ctx["n_marked"] = len(planned["to_mark"])
        if not planned["to_mark"]:
            return {"success": True, "ran": False, "context": ctx, "error": None,
                    "fallback_prompt": None}
        applied = apply_plan(workspace_root, planned)
        ctx["batch_id"] = applied["batch_id"]
        ctx["n_marked"] = applied["markers_written"]
        return {"success": True, "ran": True, "context": ctx, "error": None,
                "fallback_prompt": None}
    except Exception as exc:
        return {"success": False, "ran": False, "context": {},
                "error": f"{type(exc).__name__}: {exc}",
                "fallback_prompt": ("A small history fix (labelling old commitments "
                                    "by kind) is pending; it will retry on the next update.")}


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    ws = argv[1]
    planned = plan(ws)
    print(f"legacy kind:None rows: {planned['n_scanned']} — plan: "
          f"scheduling {planned['n_scheduling']} · task {planned['n_task']} · "
          f"promise {planned['n_promise']} · already marked {planned['n_skipped']}")
    if "--apply" in argv[2:]:
        print(apply_plan(ws, planned))
    else:
        print("MODE: DRY-RUN — nothing written. Re-run with --apply to write markers.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
