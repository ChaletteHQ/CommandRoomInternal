#!/usr/bin/env python3
"""
migrate_commitment_kinds.py — one-time ADDITIVE kind partition of the
existing open commitment set (Phase 2 Stage D, S6).

⚠️  DRY-RUN BY DEFAULT (same contract as backfill_substrate.py and
repair_commitment_closures.py). DO NOT run --apply against a live workspace
unsupervised — the live run happens at dogfood time, and its before/after
counts feed the Phase 8 migration announcement.

WHAT IT DOES
============
The live audit found 69% of truly-open items are self-owed with no
counterparty — tasks, not promises. They can never be auto-closed by the CRU
matchers, they rot on the chase surfaces, and they bury real promises. This
script partitions the CURRENT OPEN set:

  - `owner_id == primary user` AND no counterparty signal (no requester_* via
    the alias chain, no counterparty_id, no owner_external, and person_ids
    carries nobody but the user) AND effective kind is currently `promise`
    → append `commitment_reclassified {target_id, target_seq, new_kind: "task"}`
      (ADDITIVE marker; the projector applies it read-side — the original
      event is never rewritten, per §3.1).
  - Everything else keeps its kind. Items with `requester_*` present stay
    promises — the requester IS the counterparty (direction/counterparty_id
    derivation from requester_* is Stage E's receipt work, not this script).
  - `pending_review` items are SKIPPED (never silently reclassified) and
    listed for M's confirm.
  - The 249 in-place-closed rows already got amnesty tombstones from F3's
    repair script — they are not open, so this script never sees them
    (no double-counting, per S6).

Idempotent: re-runs find effective kind already `task` and plan nothing.

USAGE
=====
    python3 shared/scripts/migrate_commitment_kinds.py <workspace_root>            # dry-run
    python3 shared/scripts/migrate_commitment_kinds.py <workspace_root> --apply    # write markers
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cru_match import (  # noqa: E402
    _commitment_field,
    _commitment_id,
    _is_pending_review,
    load_open_commitments,
)
from commitment_state import commitment_kind  # noqa: E402
from primary_user import resolve_primary_user  # noqa: E402

MIGRATION_SOURCE_SKILL = "kind-migration-2026-07"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _has_counterparty_signal(ev: dict, user_id: str) -> bool:
    """True when anything ties a second party to this commitment."""
    if _commitment_field(ev, "requester_id"):
        return True
    d = ev.get("data") or {}
    if d.get("counterparty_id") or d.get("counterparty_name") or d.get("owner_external"):
        return True
    others = {
        p for p in (ev.get("person_ids") or []) + (d.get("person_ids") or [])
        if p and p != user_id
    }
    return bool(others)


def analyze(workspace_root) -> dict:
    """Pure analysis — reads the workspace, writes nothing."""
    opens = load_open_commitments(_events_path(workspace_root))
    user_id = resolve_primary_user(workspace_root)
    to_task: list[dict] = []
    needs_confirm: list[dict] = []
    kept = 0
    for ev in opens:
        owner = _commitment_field(ev, "owner_id")
        if (
            user_id
            and owner == user_id
            and commitment_kind(ev) == "promise"
            and not _has_counterparty_signal(ev, user_id)
        ):
            entry = {
                "target_id": _commitment_id(ev),
                "target_seq": ev.get("seq"),
                "title": _commitment_field(ev, "title") or "",
            }
            if _is_pending_review(ev):
                needs_confirm.append(entry)
            else:
                to_task.append(entry)
        else:
            kept += 1
    return {
        "to_task": to_task,
        "needs_confirm": needs_confirm,
        "kept_as_commitment": kept,
        "n_open": len(opens),
        "user_id": user_id,
    }


def apply_markers(workspace_root, plan: dict) -> dict:
    """Append one additive commitment_reclassified marker per planned item
    through the gate. Never rewrites anything."""
    from event_gate import append_event
    events_path = _events_path(workspace_root)
    written = 0
    for r in plan["to_task"]:
        data = {
            "target_id": r["target_id"],
            "new_kind": "task",
            "reason": "S6 kind partition — self-owed, no counterparty signal",
        }
        if isinstance(r.get("target_seq"), int):
            data["target_seq"] = r["target_seq"]
        append_event(events_path, [{
            "type": "commitment_reclassified",
            "source_skill": MIGRATION_SOURCE_SKILL,
            "data": data,
        }], holder=MIGRATION_SOURCE_SKILL)
        written += 1
    return {"markers_written": written}


def render_report(plan: dict, *, applied: dict | None = None) -> str:
    lines = ["=== commitment kind partition (S6, kind-migration-2026-07) ==="]
    lines.append(f"open commitments scanned: {plan['n_open']}")
    lines.append(f"kept as promise/other:    {plan['kept_as_commitment']}")
    lines.append(f"planned task markers:     {len(plan['to_task'])}")
    for r in plan["to_task"]:
        lines.append(f"  [task] seq {r['target_seq']} — {r['title'][:60]!r}")
    if plan["needs_confirm"]:
        lines.append(f"pending_review — CONFIRM WITH M, never silently reclassified: {len(plan['needs_confirm'])}")
        for r in plan["needs_confirm"]:
            lines.append(f"  [confirm] seq {r['target_seq']} — {r['title'][:60]!r}")
    if applied is None:
        lines.append("MODE: DRY-RUN — nothing written. Re-run with --apply (supervised) to write.")
    else:
        lines.append(f"MODE: APPLIED — {applied['markers_written']} additive markers written.")
    return "\n".join(lines)


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    workspace_root = argv[1]
    do_apply = "--apply" in argv[2:]
    if not _events_path(workspace_root).exists():
        print(f"no events.jsonl under {workspace_root!r}", file=sys.stderr)
        return 2
    plan = analyze(workspace_root)
    applied = apply_markers(workspace_root, plan) if do_apply else None
    print(render_report(plan, applied=applied))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
