#!/usr/bin/env python3
"""
migrate_cts1_backfill.py — the CTS1 §8 backfill sweep (2026-07).

⚠️  DRY-RUN BY DEFAULT (same contract as migrate_commitment_kinds.py and
backfill_substrate.py). DO NOT run --apply against a live workspace
unsupervised — dry-run first, report the expected counts, THEN apply with M
(or the operator) watching. `--apply` snapshots events.jsonl to a timestamped
.bak before writing anything.

MINIMAL-WRITES PRINCIPLE (RULED 2026-07-16)
===========================================
The CTS1 surfaces derive from owner + EFFECTIVE kind read-side
(shared/scripts/surface_split.py), so most open rows need NO write at all. A
marker is written ONLY where the effective kind must actually CHANGE. This
script NEVER annotates rows, never rewrites history, never writes a
`direction` field — additive `commitment_reclassified` markers only, through
the gate.

WHAT EACH §8 CLASS GETS (live basis 2026-07-16: 124 open = 29 Waiting On /
16 Promised / 69 Personal-by-old-rule, 49 of which are orphaned promises /
3 unowned / 7 unconfirmed):

1. `kind: task` rows → My Plate · Personal by projection. NO write for clean
   rows. A task CARRYING a counterparty signal (zero live at verification —
   defensive) is flagged for the `mine/theirs/drop` review, never silently
   flipped.
2. EXPLICIT `kind: promise`, owner me, no counterparty — the orphaned
   promises: they STAY Promised ("counterparty unresolved" is a
   projection-side tag). NO write, EVER — the drip + Friday-batch fixup is
   the convergence path (Bug #103: most are REAL promises with failed
   counterparty linking). Auto-demote is explicitly rejected.
3. `kind: promise` / scheduling / agenda with owner ≠ me → Waiting On by
   projection. NO write.
4. LEGACY NO-KIND rows (read as `promise` forever): NO write when they are
   genuinely promises (the default read is already correct). A
   `commitment_reclassified → task` marker ONLY for CLEAR self-directed
   rows: owner me, no counterparty signal, AND the title carries §5
   task-language ("I need to / I should / set up / clean up / review my
   ..."). Genuinely ambiguous rows (owner me, no counterparty, no task
   language) are ROUTED TO THE §8.2(b) TRIAGE BATCH — this script writes NO
   pending_review flags at all (the flood cap, taken to its floor: 0). They
   render Promised with the "counterparty unresolved" tag until the batch or
   the drip adjudicates them, which is the ruled treatment for exactly this
   shape of row.

Idempotent: re-runs see the effective kind already `task` and plan nothing.
Report ALWAYS prints expected counts BEFORE anything writes (dry-run is the
report; --apply prints the same plan and then writes it).

USAGE
=====
    python3 shared/scripts/migrate_cts1_backfill.py <workspace_root>            # dry-run
    python3 shared/scripts/migrate_cts1_backfill.py <workspace_root> --apply    # snapshot + write markers
"""
from __future__ import annotations

import re
import shutil
import sys
import time
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
from primary_user import resolve_primary_user  # noqa: E402
from surface_split import has_counterparty_signal  # noqa: E402

MIGRATION_SOURCE_SKILL = "cts1-backfill-2026-07"

# §5.2 task-language: self-directed phrasing that marks a legacy no-kind row
# as a clear personal to-do. Conservative by design — a miss just leaves the
# row Promised for the batch fixup; a false hit demotes a real promise.
_TASK_LANGUAGE = re.compile(
    r"^(i\s+(need|want|have|ought)\s+to\b|i\s+should\b|"
    r"(set\s*up|clean\s*up|organize|refresh|review|update|fix|research|"
    r"read|write\s+up|look\s+into|figure\s+out)\s+(my|the)\b)",
    re.IGNORECASE,
)


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _raw_kind(ev: dict):
    """The ON-DISK data.kind (None when absent) — distinct from effective
    kind: §8 treats explicit `promise` and legacy no-kind differently."""
    kind = (ev.get("data") or {}).get("kind")
    return kind if isinstance(kind, str) and kind else None


def _effective_kind(ev: dict) -> str:
    """Effective kind of the PROJECTED row (the loader already folded
    commitment_reclassified overrides into data.kind)."""
    return _raw_kind(ev) or "promise"


def analyze(workspace_root) -> dict:
    """Pure analysis — reads the workspace, writes nothing. Every open
    top-level row lands in exactly one plan bucket."""
    opens = load_open_commitments(_events_path(workspace_root))
    user_id = resolve_primary_user(workspace_root)

    to_task: list[dict] = []          # legacy no-kind + task-language → marker
    batch_review: list[dict] = []     # ambiguous → §8.2(b) triage batch, NO write
    orphan_promises: list[dict] = []  # explicit promise, no counterparty → stay (report only)
    task_with_counterparty: list[dict] = []  # defensive flag → mine/theirs/drop review
    no_write = 0

    for ev in opens:
        entry = {
            "target_id": _commitment_id(ev),
            "target_seq": ev.get("seq"),
            "title": (_commitment_field(ev, "title") or "")[:80],
        }
        if _is_pending_review(ev):
            no_write += 1  # unconfirmed — the W4b confirm flow owns these
            continue
        owner = _commitment_field(ev, "owner_id")
        eff = _effective_kind(ev)
        raw = _raw_kind(ev)
        owner_is_me = bool(user_id and owner == user_id)
        has_cp = has_counterparty_signal(ev, user_id)

        if eff == "task":
            if has_cp:
                task_with_counterparty.append(entry)
            else:
                no_write += 1
            continue
        if not owner_is_me:
            no_write += 1  # Waiting On / unowned — projection handles it
            continue
        if has_cp:
            no_write += 1  # a linked promise — Promised, correct as-is
            continue
        # Owner me, promise-shaped, no counterparty:
        if raw is not None:
            if raw == "promise":
                # Explicit promise — the §8.2 orphans.
                orphan_promises.append(entry)
            else:
                # Counterparty-less scheduling/agenda classify PERSONAL
                # (surface_split §2.3) — nothing is unlinked; no write, and
                # NOT an orphan (the report must match the classifier).
                no_write += 1
            continue
        # Legacy no-kind:
        title = _commitment_field(ev, "title") or ""
        if _TASK_LANGUAGE.search(title.strip()):
            to_task.append(entry)
        else:
            batch_review.append(entry)

    return {
        "to_task": to_task,
        "batch_review": batch_review,
        "orphan_promises": orphan_promises,
        "task_with_counterparty": task_with_counterparty,
        "no_write": no_write,
        "n_open": len(opens),
        "user_id": user_id,
    }


def apply_markers(workspace_root, plan: dict) -> dict:
    """Snapshot events.jsonl, then append one additive
    commitment_reclassified marker per to_task row through the gate. The
    ONLY write class this script has — everything else in the plan is
    report-only by ruling."""
    events_path = _events_path(workspace_root)
    backup = events_path.with_name(
        events_path.name + f".bak_cts1_{int(time.time())}"
    )
    shutil.copy2(events_path, backup)
    from event_gate import append_event
    written = 0
    for r in plan["to_task"]:
        data = {
            "target_id": r["target_id"],
            "new_kind": "task",
            "reason": "CTS1 §8.4 — legacy no-kind, self-directed task language",
        }
        if isinstance(r.get("target_seq"), int):
            data["target_seq"] = r["target_seq"]
        append_event(events_path, [{
            "type": "commitment_reclassified",
            "source_skill": MIGRATION_SOURCE_SKILL,
            "data": data,
        }], holder=MIGRATION_SOURCE_SKILL)
        written += 1
    return {"markers_written": written, "backup": str(backup)}


def render_report(plan: dict, *, applied: dict | None = None) -> str:
    lines = ["=== CTS1 §8 backfill sweep (cts1-backfill-2026-07) ==="]
    lines.append(f"open commitments scanned:          {plan['n_open']}")
    lines.append(f"no write needed (projection-only): {plan['no_write'] + len(plan['orphan_promises'])}")
    lines.append(f"planned task markers (§8.4):       {len(plan['to_task'])}")
    for r in plan["to_task"]:
        lines.append(f"  [task] seq {r['target_seq']} — {r['title']!r}")
    lines.append(
        f"orphaned promises — STAY Promised, tagged read-side (§8.2, NO write): "
        f"{len(plan['orphan_promises'])}"
    )
    lines.append(
        f"ambiguous legacy rows → Friday-triage batch (§8.2(b), NO write, "
        f"expected pending_review volume: 0): {len(plan['batch_review'])}"
    )
    for r in plan["batch_review"]:
        lines.append(f"  [batch] seq {r['target_seq']} — {r['title']!r}")
    if plan["task_with_counterparty"]:
        lines.append(
            f"tasks CARRYING a counterparty — flag for mine/theirs/drop, never "
            f"silently flipped: {len(plan['task_with_counterparty'])}"
        )
        for r in plan["task_with_counterparty"]:
            lines.append(f"  [flag] seq {r['target_seq']} — {r['title']!r}")
    if applied is None:
        lines.append("MODE: DRY-RUN — nothing written. These ARE the expected "
                     "counts; re-run with --apply (supervised) to write.")
    else:
        lines.append(
            f"MODE: APPLIED — {applied['markers_written']} additive markers "
            f"written; snapshot at {applied['backup']}."
        )
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
