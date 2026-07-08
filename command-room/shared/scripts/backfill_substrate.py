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
          `data.thread_id` (or a single `related_thread_ids`) gets it promoted.
          The rest are reported as underivable — they carry no thread reference
          and need a heuristic or manual pass (NOT auto-filled here).

  REPORT ONLY — duplicate seqs (C12). `seq` is an index/reference key
          (source_ref_index, source_event_seq back-references). Reassigning a
          seq orphans those refs, so this tool does NOT rewrite seqs. It lists
          them for a deliberate, ref-aware additive correction. (There is no
          tamper hash-chain in the current schema, despite cleanup/SKILL.md's
          note — the real constraint is the index-key dependency.)

DRY-RUN BY DEFAULT. `--apply` snapshots events.jsonl to a timestamped .bak,
rewrites atomically preserving every byte of every untouched line and every
payload field except the corrected one, then appends a `substrate_backfill`
marker event. Owner-invoked only; never part of recurring cleanup.

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
    from next_seq import next_seq
except ImportError:  # pragma: no cover - package import
    from .atomic_write import atomic_write_text, atomic_append_jsonl  # type: ignore
    from .next_seq import next_seq  # type: ignore

# Event types that are thread-bound (must carry a primary_thread_id). Mirrors
# integrity_check C14's scope; kept local so this tool is self-contained.
_THREAD_BOUND_TYPES = {
    "meeting", "decision", "draft_created", "commitment", "commitment_captured",
    "follow_up", "thread_resolved", "memo", "brief", "call_prep",
}
_THREAD_KEYS = ("primary_thread_id", "thread_id", "project_id", "primary_project_id")


def _data(ev: dict) -> dict:
    d = ev.get("data")
    return d if isinstance(d, dict) else {}


def _is_org(v) -> bool:
    return isinstance(v, str) and v.startswith("org_")


def _derive_thread(ev: dict) -> str | None:
    """A thread/project id this event already carries, if unambiguous."""
    for k in ("thread_id", "project_id", "primary_project_id"):
        v = ev.get(k) or _data(ev).get(k)
        if isinstance(v, str) and v and not _is_org(v):
            return v
    rel = ev.get("related_thread_ids") or _data(ev).get("related_thread_ids")
    if isinstance(rel, list) and len(rel) == 1 and isinstance(rel[0], str) and not _is_org(rel[0]):
        return rel[0]
    return None


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


def apply(plan: dict, events_path: Path) -> dict:
    import time
    raw = list(plan["_raw_lines"])
    edits = plan["_edits"]
    if not edits:
        return {"applied": False, "reason": "nothing to change"}
    # Snapshot first.
    stamp = int(Path(events_path).stat().st_mtime)
    backup = events_path.with_name(events_path.name + f".bak_backfill_{stamp}")
    atomic_write_text(backup, "".join(raw))
    # Rewrite touched lines, preserve the rest verbatim.
    for line_idx, new_ev in edits.items():
        raw[line_idx] = json.dumps(new_ev, ensure_ascii=False) + "\n"
    atomic_write_text(events_path, "".join(raw))
    marker = {
        "seq": next_seq(events_path),
        "type": "substrate_backfill",
        "source_skill": "backfill_substrate",
        "data": {
            "orgslot_fixed": len(plan["fix1_orgslot"]),
            "thread_id_filled": len(plan["fix2_derivable"]),
            "thread_id_underivable": len(plan["fix2_underivable"]),
            "dup_seqs_reported": len(plan["dup_seqs"]),
            "backup": backup.name,
        },
    }
    atomic_append_jsonl(events_path, [marker])
    return {"applied": True, "backup": str(backup), "lines_changed": len(edits)}


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
        print(render(plan))
        print("\nAPPLIED:" if res.get("applied") else "\nNO-OP:", res)
        return 0
    print(render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
