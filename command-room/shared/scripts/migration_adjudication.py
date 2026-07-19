#!/usr/bin/env python3
"""Durable migration-adjudication lookup for command-room-update-bridge Phase 1
(FB-5, T3 fix bundle — confirmed live 2026-07-16).

WHY THIS EXISTS
---------------
The bridge decides which workspace migrations are "pending" before every update
run. That adjudication check was prose left to the LLM at runtime, and the
per-migration trigger gates for `apply_once` migrations enumerated ONLY two
suppression signals: the marker phrase present in the target file, or a
`workspace_migration_applied` event. A migration the operator SKIPPED logged a
`workspace_migration_skipped` event — which no gate ever consulted — so the
marker stayed absent, no applied event existed, and the bridge re-proposed the
same migration on every subsequent run, forever (the live case:
`draft_posture_queue_on_click_v1`, skipped 2026-07-16, re-proposed on every
bridge fire after).

The durable record was always there in `_hq/data/events.jsonl`; the framework
just never read it. This module is the mechanized reader: fold the adjudication
events, key on the MIGRATION ID (never on marker phrases or log prose), and
report per-id whether re-proposal is suppressed. The bridge SHELLS IN to this
instead of grepping for phrases — so a logged adjudication (applied OR
skipped/declined) suppresses "pending" on every future run, deterministically.

SEMANTICS
---------
Latest adjudication event per migration id wins (events.jsonl is append-only;
file order is authoritative — old lines may predate `seq`).

  * status "applied"  -> suppressed (the apply-once deliberate-deletion rule and
    the partially-applied re-confirm edge case are the CALLER's per-type policy;
    this module just reports the durable record).
  * status "skipped"  -> suppressed, UNLESS the skip reason is one of the
    documented deliberately-re-surface reasons (RE_SURFACE_REASONS below).
    Unknown/free-form skip reasons suppress — live skips carry operator-authored
    reasons ("content_already_current_operator_declined", …), and the durable
    default must be "an adjudication sticks"; re-surfacing is the explicit
    opt-in, never the fallback.

`redo workspace migrations` runs the same lookup with honor_skips=False so a
customer can always opt back in — skips are never physically cleared from the
append-only log.

Reads both live event shapes: `migration_id` / `reason` at the event top level
(older bridge fires) or nested under `data` (current shape). Malformed JSONL
lines are skipped defensively (real substrate carries them — the
passes-unit-tests-crashes-on-real-data class).

CLI
---
  python3 shared/scripts/migration_adjudication.py <workspace_root_or_events.jsonl> [--ignore-skips] [migration_id ...]

Prints a JSON object keyed by migration id:
  {"<id>": {"status": "applied"|"skipped"|"unadjudicated",
            "reason": <str|null>, "suppressed": bool, "ts": <str|null>}}
With explicit ids, every requested id appears (unadjudicated ones included);
with no ids, every adjudicated id found in the log is reported.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ADJUDICATION_TYPES = ("workspace_migration_applied", "workspace_migration_skipped")

# Skip reasons with documented DELIBERATE re-surface semantics (SKILL.md Phase
# 4.5): a deferral or a structural blocker is re-checked on the next run.
# Everything else — user_declined, user_declined_permanently,
# user_declined_after_preview, and any operator-authored free-form reason —
# suppresses re-proposal until `redo workspace migrations`.
RE_SURFACE_REASONS = frozenset({
    "user_deferred",
    "awaiting_manual_apply",
    "structural_mismatch",
    "structural_mismatch_manual_fallback",
})


def _events_path(root_or_file) -> Path:
    p = Path(root_or_file)
    if p.is_dir():
        return p / "_hq" / "data" / "events.jsonl"
    return p


def _field(ev: dict, key: str):
    """An adjudication field lives at the top level (older fires) or under
    `data` (current shape). Top level wins when both are present."""
    if ev.get(key) is not None:
        return ev.get(key)
    data = ev.get("data")
    if isinstance(data, dict):
        return data.get(key)
    return None


def load_adjudications(root_or_file) -> dict:
    """Fold events.jsonl into {migration_id: latest adjudication record}.

    Record shape: {"status": "applied"|"skipped", "reason": str|None,
                   "ts": str|None, "suppressed": bool}
    Missing file -> {} (a workspace with no event log has no adjudications).
    """
    path = _events_path(root_or_file)
    out: dict = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue  # malformed substrate line — never fatal here
            if not isinstance(ev, dict):
                continue
            if ev.get("type") not in ADJUDICATION_TYPES:
                continue
            mig_id = _field(ev, "migration_id")
            if not isinstance(mig_id, str) or not mig_id:
                continue
            status = ("applied" if ev.get("type") == "workspace_migration_applied"
                      else "skipped")
            reason = _field(ev, "reason")
            record = {
                "status": status,
                "reason": reason if isinstance(reason, str) else None,
                "ts": ev.get("ts") or ev.get("timestamp"),
            }
            record["suppressed"] = _suppresses(record)
            out[mig_id] = record  # later line wins — append-only log order
    return out


def _suppresses(record: dict) -> bool:
    if record["status"] == "applied":
        return True
    return record["reason"] not in RE_SURFACE_REASONS


def adjudication_status(root_or_file, migration_ids=None, honor_skips: bool = True) -> dict:
    """Per-id adjudication report for the bridge's Phase 1 detection pass.

    migration_ids=None reports every adjudicated id in the log; an explicit
    list reports every requested id, filling "unadjudicated" for ids with no
    logged adjudication. honor_skips=False (the `redo workspace migrations`
    path) treats skipped ids as NOT suppressed while leaving applied ones
    suppressed.
    """
    folded = load_adjudications(root_or_file)
    ids = list(migration_ids) if migration_ids else sorted(folded)
    report = {}
    for mig_id in ids:
        rec = folded.get(mig_id)
        if rec is None:
            report[mig_id] = {"status": "unadjudicated", "reason": None,
                              "ts": None, "suppressed": False}
            continue
        rec = dict(rec)
        if not honor_skips and rec["status"] == "skipped":
            rec["suppressed"] = False
        report[mig_id] = rec
    return report


def is_suppressed(root_or_file, migration_id: str, honor_skips: bool = True) -> bool:
    """True iff a durable adjudication says this migration must NOT be
    re-proposed as pending."""
    return adjudication_status(root_or_file, [migration_id],
                               honor_skips=honor_skips)[migration_id]["suppressed"]


def _main(argv) -> int:
    args = [a for a in argv[1:] if a != "--ignore-skips"]
    honor_skips = "--ignore-skips" not in argv[1:]
    if not args:
        print("usage: migration_adjudication.py <workspace_root_or_events.jsonl> "
              "[--ignore-skips] [migration_id ...]", file=sys.stderr)
        return 2
    root, ids = args[0], (args[1:] or None)
    print(json.dumps(adjudication_status(root, ids, honor_skips=honor_skips),
                     indent=2))
    return 0


__all__ = ["load_adjudications", "adjudication_status", "is_suppressed",
           "ADJUDICATION_TYPES", "RE_SURFACE_REASONS"]


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
