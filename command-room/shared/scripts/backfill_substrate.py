#!/usr/bin/env python3
"""One-shot, payload-preserving substrate backfill (v4.0.0 re-verify, Open Q1).

`integrity_check.py` *reports* three classes of structurally-valid drift that no
healer touches (recover_corruption only quarantines unparseable lines; cleanup
declares dup-seqs report-only). This converter proposes the safe corrections.

It fixes TWO deterministic classes and REPORTS the third:

  FIX 1 — org-in-thread-slot (C7). An org id (org_NNN) sitting in
          `primary_thread_id` is a type error: a thread/project slot holding an
          org. Correction: relocate the org into `org_ids` (deduped) and clear
          the polluted thread slot. NOTE: the *correct* thread is NOT inferable
          when the org owns multiple threads, so the slot is cleared, not
          reassigned — the event then shows up under FIX 2's "underivable"
          bucket for a deliberate thread assignment.

  FIX 2 — missing primary_thread_id (C14), DERIVABLE subset only. A thread-bound
          event missing `primary_thread_id` but carrying an explicit
          `thread_id` / `project_id` / `primary_project_id` (envelope or data)
          gets it promoted, through the SAME ladder the capture gate and the
          append gate use — `event_types.derive_primary_thread_id`. The rest
          are reported as underivable — they carry no promotable thread
          reference and need a deliberate assignment (NOT auto-filled here).
          A lone `related_thread_ids` entry is deliberately NOT derivable; see
          `_derive_thread`.

  REPORT ONLY — duplicate seqs (C12). `seq` is an index/reference key
          (source_ref_index, source_event_seq back-references). Reassigning a
          seq orphans those refs, so this tool does NOT rewrite seqs. It lists
          them for a deliberate, ref-aware additive correction. (There is no
          tamper hash-chain in the current schema, despite cleanup/SKILL.md's
          note — the real constraint is the index-key dependency.)

DRY-RUN BY DEFAULT. `--apply` takes the EVENTS WRITER LOCK, re-derives the plan
inside it, snapshots events.jsonl to a timestamped .bak, rewrites atomically
preserving every byte of every untouched line and every payload field except
the corrected one, then appends a `substrate_backfill` marker event and reports
what it did NOT touch. Owner-invoked only; never part of recurring cleanup, and
never run on a live workspace without the operator's explicit call — the plan
is the thing to read first.

Usage:
    python3 backfill_substrate.py <workspace_root>            # dry-run (default)
    python3 backfill_substrate.py <workspace_root> --apply
    python3 backfill_substrate.py <workspace_root> --json     # machine-readable plan
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from atomic_write import atomic_write_text, atomic_append_jsonl
    from event_types import (
        PRIMARY_THREAD_FIELD,
        THREAD_BOUND_TYPES,
        THREAD_REF_DERIVE_FIELDS,
        derive_primary_thread_id,
    )
    from next_seq import next_seq
    from writer_lock import events_writer_lock
except ImportError:  # pragma: no cover - package import
    from .atomic_write import atomic_write_text, atomic_append_jsonl  # type: ignore
    from .event_types import (  # type: ignore
        PRIMARY_THREAD_FIELD,
        THREAD_BOUND_TYPES,
        THREAD_REF_DERIVE_FIELDS,
        derive_primary_thread_id,
    )
    from .next_seq import next_seq  # type: ignore
    from .writer_lock import events_writer_lock  # type: ignore

# Event types that are thread-bound (must carry a primary_thread_id).
#
# THREADSTAMP1. This used to be a LOCAL literal whose comment claimed it
# "mirrors integrity_check C14's scope". It did not — the two sets disagreed on
# twelve of thirteen types, so this tool repaired six types the checker never
# counted and ignored six it did. Both now read `event_types.THREAD_BOUND_TYPES`
# and there is nothing left to drift.
_THREAD_BOUND_TYPES = THREAD_BOUND_TYPES

# Every spelling of "this row points at a thread", destination first.
# `personal_leak._THREAD_REF_FIELDS` is pinned equal to this tuple by
# `run_personal_tie_join_test` — the firewall keeps its own literal copy so it
# stays import-free inside `events_io`, and the pin is what stops a new
# spelling here from silently bypassing the firewall's resolver. Derived from
# the shared constants rather than restated, so the pin can only be broken
# deliberately.
_THREAD_KEYS = (PRIMARY_THREAD_FIELD,) + THREAD_REF_DERIVE_FIELDS


def _data(ev: dict) -> dict:
    d = ev.get("data")
    return d if isinstance(d, dict) else {}


def _is_org(v) -> bool:
    return isinstance(v, str) and v.startswith("org_")


def _derive_thread(ev: dict) -> str | None:
    """A thread/project id this event already carries, if unambiguous.

    THREADSTAMP1 — one ladder, shared with the capture gate and the append
    gate (`event_types.derive_primary_thread_id`). Two derivations of the same
    thing WILL disagree, and a repair tool that fills a slot differently from
    the gate that will fill it tomorrow is a slow-motion inconsistency.

    DELIBERATE NARROWING, and the one deviation from SPEC_THREADSTAMP1 DD-1's
    literal ladder: a lone `related_thread_ids` entry is NO LONGER derived
    from. `personal_leak.business_thread_id` resolves every OTHER spelling in
    this ladder already, which makes the derivation provably neutral for the
    personal firewall; `related_thread_ids` is the one field it does not read,
    so promoting it into `primary_thread_id` would be the single rung capable
    of moving a tie-touching row from withheld to rendered — the leak BUG-8330
    item 12 was filed for and FX-5 re-opened twice. The spec instructs that the
    BUG-8330 analysis constrains this derivation, and this is that constraint
    biting. Rows that only carried a related thread now report as UNDERIVABLE
    (untouched, counted) instead of being auto-filled, which is the direction
    this tool's own FIX 1 note already calls for: a slot that cannot be filled
    safely gets a deliberate thread assignment, not a guess.
    """
    return derive_primary_thread_id(ev)


def build_plan(events_path: Path) -> dict:
    raw_lines: list[str] = []
    parsed: list[tuple[int, dict | None]] = []  # (line_idx, event-or-None)
    with open(events_path, "r", encoding="utf-8") as f:
        for i, raw in enumerate(f):
            raw_lines.append(raw if raw.endswith("\n") else raw + "\n")
            s = raw.strip()
            if not s:
                parsed.append((i, None)); continue
            try:
                ev = json.loads(s)
            except json.JSONDecodeError:
                parsed.append((i, None)); continue
            parsed.append((i, ev if isinstance(ev, dict) else None))

    fix1_orgslot: list[dict] = []   # org in primary_thread_id
    fix2_derivable: list[dict] = []
    fix2_underivable: list[dict] = []
    seqs: dict = {}
    edits: dict[int, dict] = {}     # line_idx -> new event

    for line_idx, ev in parsed:
        if ev is None:
            continue
        s = ev.get("seq")
        if isinstance(s, int) and not isinstance(s, bool):
            seqs.setdefault(s, []).append(line_idx)

        new = None
        # FIX 1 — org id parked in primary_thread_id.
        pt = ev.get("primary_thread_id")
        if _is_org(pt):
            new = json.loads(json.dumps(ev))  # deep copy
            orgs = new.get("org_ids")
            orgs = list(orgs) if isinstance(orgs, list) else []
            if pt not in orgs:
                orgs.append(pt)
            new["org_ids"] = orgs
            new["primary_thread_id"] = None
            new["_backfilled_orgslot"] = True
            fix1_orgslot.append({"seq": ev.get("seq"), "type": ev.get("type"), "moved": pt})

        # FIX 2 — thread-bound, missing primary_thread_id.
        base = new if new is not None else ev
        if ev.get("type") in _THREAD_BOUND_TYPES and not base.get("primary_thread_id"):
            derived = _derive_thread(ev)
            if derived:
                if new is None:
                    new = json.loads(json.dumps(ev))
                new["primary_thread_id"] = derived
                new["_backfilled_thread"] = True
                fix2_derivable.append({"seq": ev.get("seq"), "type": ev.get("type"), "set": derived})
            else:
                fix2_underivable.append({"seq": ev.get("seq"), "type": ev.get("type")})

        if new is not None:
            edits[line_idx] = new

    dup_seqs = {s: idxs for s, idxs in seqs.items() if len(idxs) > 1}

    return {
        "events_path": str(events_path),
        "total_lines": len(raw_lines),
        "fix1_orgslot": fix1_orgslot,
        "fix2_derivable": fix2_derivable,
        "fix2_underivable": fix2_underivable,
        "dup_seqs": {str(k): v for k, v in dup_seqs.items()},
        "_raw_lines": raw_lines,
        "_edits": edits,
    }


def render(plan: dict) -> str:
    o = []
    o.append("Substrate backfill plan (dry-run)")
    o.append(f"  events.jsonl: {plan['events_path']}  ({plan['total_lines']} lines)")
    o.append("")
    o.append(f"  FIX 1  org-in-thread-slot relocations : {len(plan['fix1_orgslot'])}")
    from collections import Counter
    c = Counter(x["moved"] for x in plan["fix1_orgslot"])
    for org, n in c.most_common():
        o.append(f"           {org} -> org_ids, primary_thread_id cleared : {n}")
    o.append(f"  FIX 2  primary_thread_id auto-filled   : {len(plan['fix2_derivable'])}  (derivable)")
    o.append(f"         primary_thread_id UNDERIVABLE   : {len(plan['fix2_underivable'])}  (need manual/heuristic)")
    o.append(f"  REPORT duplicate seqs (NOT rewritten)  : {len(plan['dup_seqs'])} seqs")
    for s, idxs in list(plan["dup_seqs"].items())[:12]:
        o.append(f"           seq {s} on lines {[i + 1 for i in idxs]}")
    o.append("")
    o.append(f"  Lines that would change: {len(plan['_edits'])}  "
             f"(all other {plan['total_lines'] - len(plan['_edits'])} lines byte-preserved)")
    o.append("  Run with --apply to snapshot + rewrite.")
    return "\n".join(o)


def apply(plan: dict, events_path: Path, *, timeout_s: float = 30.0) -> dict:
    """Owner-invoked apply of the plan above (SPEC_THREADSTAMP1 DD-3).

    THE LOCK IS THE POINT (the BUG-8330 FX-2 class, same shape as
    `repair_seq_relocation.apply_remap` and `seq_health.detect_and_mark`). This
    is a whole-file READ-MODIFY-WRITE of events.jsonl. Run without the events
    writer lock, a concurrent gated append lands between the read and the
    write-back and is DESTROYED — and worse than plain loss, because the
    appender allocated its seq from a `.seqhw` this rewrite does not move, so
    the surviving ledger and the sidecar disagree afterwards. The whole
    critical section is under `events_writer_lock`, which is reentrant, so the
    marker append at the end nests cleanly rather than deadlocking.

    THE PLAN IS RE-DERIVED INSIDE THE LOCK. A plan built before the lock was
    taken describes a file that may have grown since — its line indices would
    then edit the WRONG rows. The caller's `plan` is advisory: it is used for
    nothing but the dry-run-parity comparison returned in `plan_matched`.

    A MISSING LEDGER IS REFUSED BEFORE ANY DIRECTORY IS TOUCHED — a mistyped
    root must not have a substrate tree fabricated under it.

    UNTOUCHED COUNTS ARE REPORTED, always (DD-3 acceptance): what was filled,
    what was left alone because nothing was derivable, and how many lines were
    preserved byte-for-byte."""
    events_path = Path(events_path)
    if not events_path.exists():
        return {"applied": False, "refused": "no events.jsonl at "
                f"{events_path} — refusing rather than fabricating a substrate "
                "tree under a mistyped workspace root"}

    with events_writer_lock(events_path, holder="backfill_substrate",
                            timeout_s=timeout_s):
        # Re-derive under the lock. Anything built outside it describes a file
        # that may have moved.
        live = build_plan(events_path)
        raw = list(live["_raw_lines"])
        edits = live["_edits"]
        plan_matched = (
            len(plan.get("_edits") or {}) == len(edits)
            and len(plan.get("fix2_derivable") or []) == len(live["fix2_derivable"])
            and len(plan.get("fix1_orgslot") or []) == len(live["fix1_orgslot"])
        )
        untouched = {
            "thread_id_underivable": len(live["fix2_underivable"]),
            "lines_preserved": live["total_lines"] - len(edits),
            "dup_seqs_reported_not_rewritten": len(live["dup_seqs"]),
        }
        if not edits:
            return {"applied": False, "reason": "nothing to change",
                    "plan_matched": plan_matched, "untouched": untouched,
                    "plan": live}
        # Snapshot first — inside the lock, so the copy is of the same bytes
        # the rewrite is about to replace.
        stamp = int(events_path.stat().st_mtime)
        backup = events_path.with_name(
            events_path.name + f".bak_backfill_{stamp}")
        atomic_write_text(backup, "".join(raw))
        # Rewrite touched lines, preserve the rest verbatim.
        for line_idx, new_ev in edits.items():
            raw[line_idx] = json.dumps(new_ev, ensure_ascii=False) + "\n"
        atomic_write_text(events_path, "".join(raw))
        # No hand-stamped seq (BUG-8330 item 7) — appender allocates in-lock.
        marker = {
            "type": "substrate_backfill",
            "source_skill": "backfill_substrate",
            "data": {
                "orgslot_fixed": len(live["fix1_orgslot"]),
                "thread_id_filled": len(live["fix2_derivable"]),
                "thread_id_underivable": len(live["fix2_underivable"]),
                "dup_seqs_reported": len(live["dup_seqs"]),
                "backup": backup.name,
            },
        }
        atomic_append_jsonl(events_path, [marker])
        return {"applied": True, "backup": str(backup),
                "lines_changed": len(edits), "plan_matched": plan_matched,
                "untouched": untouched, "plan": live}


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if not args:
        print("usage: backfill_substrate.py <workspace_root> [--apply] [--json]", file=sys.stderr)
        return 2
    root = Path(args[0])
    events_path = root / "_hq" / "data" / "events.jsonl"
    if not events_path.exists():
        print(f"events.jsonl not found at {events_path}", file=sys.stderr)
        return 2
    plan = build_plan(events_path)
    if "--json" in flags:
        public = {k: v for k, v in plan.items() if not k.startswith("_")}
        print(json.dumps(public, indent=2))
        return 0
    if "--apply" in flags:
        res = apply(plan, events_path)
        # Report the plan the APPLY actually acted on (re-derived inside the
        # lock), never the pre-lock one — printing the stale plan next to a
        # different outcome is how a supervised tool loses its supervisor.
        print(render(res.get("plan") or plan))
        if res.get("refused"):
            print("\nREFUSED:", res["refused"])
            return 2
        print("\nAPPLIED:" if res.get("applied") else "\nNO-OP:", {
            k: v for k, v in res.items() if k != "plan"})
        if not res.get("plan_matched"):
            print("  NOTE: the ledger changed between the dry-run scan and the "
                  "lock; the plan above is the one that was applied.")
        return 0
    print(render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
