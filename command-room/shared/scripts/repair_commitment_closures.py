#!/usr/bin/env python3
"""
repair_commitment_closures.py — one-time ADDITIVE repair of historic
dead-letter commitment closures (Phase 2 Stage C, F3).

⚠️  DO NOT run --apply against a live workspace unsupervised. The live run
happens ONCE, supervised, at dogfood time (Build Guide §4 Phase 8 requires
the migration announcement to surface every auto-closed item with a one-tap
reopen). Ship-time verification is fixture-only (tests/run_commitment_amnesty_test.py).

WHAT IT REPAIRS
===============
The 2026-07-01 lifecycle audit found 408 closure events of which only 43
matched a commitment. The Stage C READ-side amnesty (load_open_commitments'
`commitment_seq` / `source_event_seq` chain extension) recovers ~252 dead
letters with no write at all. This script formalizes what the read side
cannot recover, as APPENDED canonical tombstones — history is never
rewritten (§3.1 additive-only doctrine):

  1. `seq` tier — closures whose only id is a legacy seq SPELLING the read
     chain doesn't parse (e.g. `data.commitment_id: "86"` / `"seq_86"` /
     `"event_086"` / `"commitment_seq_86"`) but that maps to exactly one
     commitment event's seq.
  2. `title` tier — closures with no readable/matchable id whose evidence
     text matches EXACTLY ONE open commitment's title at ratio ≥ 0.8
     (difflib). Ambiguous (≥2 matches at threshold) → unrecoverable, never
     guessed.
  3. `mutation` tier — commitment events whose `data.status` was flipped
     in place to a closed-family value (closed/resolved/superseded/done)
     with NO closure event referencing them (the 249 in-place-mutated rows;
     F4/S6). Readers already treat them as closed — the tombstone makes the
     closure exist in append-only history so the mutation class can be
     retired. Counts do not change.

  pending_review targets are NEVER auto-closed (F2 floor) — they are listed
  separately for M's confirm. Unrecoverables are listed for M's 5-minute
  triage. The 4 known-stale opens from the audit are closed MANUALLY via
  close_commitment at the supervised run, not by this script.

SAFETY
======
- PREVIEW BY DEFAULT: with no flag, prints the full plan and writes nothing.
- `--apply`: snapshots events.jsonl to
  `<workspace>/_archive/events-jsonl_repair-snapshot_<YYYY-MM-DD>/` BEFORE
  any write (archive-never-delete), then appends tombstones through
  `commitment_state.close_commitments` (the single closure path — gated,
  idempotent, pending_review-refusing) with
  `source_skill: "closure-repair-2026-07"`.
- Idempotent: a re-run finds every repaired target already resolved and
  writes nothing.

USAGE
=====
    python3 shared/scripts/repair_commitment_closures.py <workspace_root>            # preview
    python3 shared/scripts/repair_commitment_closures.py <workspace_root> --apply    # snapshot + write
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cru_match import (  # noqa: E402
    _commitment_field,
    _commitment_id,
    _is_pending_review,
    load_events_defensively,
    load_open_commitments,
)
from commitment_state import (  # noqa: E402
    _closer_target_id,
    _closer_target_seqs,
)

TITLE_MATCH_THRESHOLD = 0.8
REPAIR_SOURCE_SKILL = "closure-repair-2026-07"
_CLOSED_STATUSES = ("closed", "resolved", "superseded", "done")
_CLOSER_TYPES = ("commitment_resolved", "thread_resolved", "commitment_superseded")
# Legacy seq spellings the READ chain does not parse (it only reads the
# commitment_seq / source_event_seq fields) — the write-side normalizer's
# vocabulary, reused here for historic id VALUES.
_LEGACY_SEQ_SPELLING_RE = re.compile(r"^(?:commitment_seq_|event_|seq_)?0*(\d+)$")


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _closure_text(ev: dict) -> str:
    """Best free-text description a dead-letter closure carries — used for the
    title-match tier."""
    d = ev.get("data") or {}
    parts = [d.get("evidence"), d.get("title"), d.get("summary"), ev.get("title")]
    return " ".join(str(p) for p in parts if p)


def _title_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def analyze(workspace_root) -> dict:
    """Pure analysis — reads the workspace, writes nothing.

    Returns {
      "ok": int,                       # closures the read chain already resolves
      "repairs": [ {tier, target_id, target_seq, title, closure_seq, reason} ],
      "needs_confirm": [ ... ],        # pending_review targets — M confirms
      "unrecoverable": [ {closure_seq, text, reason} ],
      "n_open_before": int,
    }
    """
    events_path = _events_path(workspace_root)
    events, _skipped = load_events_defensively(events_path)

    by_seq: dict[int, dict] = {}
    by_id: dict[str, dict] = {}
    closers: list[dict] = []
    for ev in events:
        et = ev.get("type") or ev.get("event") or ""
        if et == "commitment":
            by_id[_commitment_id(ev)] = ev
            if isinstance(ev.get("seq"), int):
                by_seq[ev["seq"]] = ev
        elif et in _CLOSER_TYPES:
            closers.append(ev)

    # What the read chain already resolves (ids + F3 seq aliases).
    resolved_ids: set[str] = set()
    resolved_seqs: set[int] = set()
    for ev in closers:
        cid = _closer_target_id(ev)
        if cid:
            resolved_ids.add(str(cid))
        resolved_seqs.update(_closer_target_seqs(ev))

    def _is_open(c: dict) -> bool:
        status = _commitment_field(c, "status") or "open"
        if status not in ("open", "overdue"):
            return False
        if _commitment_id(c) in resolved_ids:
            return False
        return not (isinstance(c.get("seq"), int) and c["seq"] in resolved_seqs)

    opens = [c for c in by_id.values() if _is_open(c)]

    ok = 0
    repairs: list[dict] = []
    needs_confirm: list[dict] = []
    unrecoverable: list[dict] = []
    planned_targets: set[str] = set()

    def _plan(tier, target, closure_seq, reason):
        entry = {
            "tier": tier,
            "target_id": _commitment_id(target),
            "target_seq": target.get("seq"),
            "title": _commitment_field(target, "title") or "",
            "closure_seq": closure_seq,
            "reason": reason,
        }
        if _commitment_id(target) in planned_targets:
            return  # one tombstone per commitment, first evidence wins
        planned_targets.add(_commitment_id(target))
        if _is_pending_review(target):
            needs_confirm.append(entry)
        else:
            repairs.append(entry)

    for ev in closers:
        cid = _closer_target_id(ev)
        seq_refs = _closer_target_seqs(ev)
        closure_seq = ev.get("seq")

        # Already readable → nothing to do.
        readable = (str(cid) in by_id if cid else False) or any(
            s in by_seq for s in seq_refs
        )
        if readable:
            ok += 1
            continue

        # Tier 1 — legacy seq SPELLING in the id value.
        if cid:
            m = _LEGACY_SEQ_SPELLING_RE.match(str(cid).strip())
            if m and int(m.group(1)) in by_seq:
                target = by_seq[int(m.group(1))]
                if _is_open(target):
                    _plan("seq", target, closure_seq,
                          f"closure id {cid!r} parses to seq {int(m.group(1))}")
                else:
                    ok += 1  # already closed some other way — no work
                continue

        # Tier 1b — data.source_commitment_ref straggler (BUG-8330 item 3):
        # an external-origin pointer field NO reader honors and no plugin
        # writer emits. Where its value resolves (exact id or legacy seq
        # spelling), the closure gets an additive canonical tombstone; the
        # field itself stays unhonored — promoting a zero-writer field into
        # the live chain would be a new dead mechanism (item 16's class).
        scr = (ev.get("data") or {}).get("source_commitment_ref")
        if scr not in (None, ""):
            scr_s = str(scr).strip()
            target = by_id.get(scr_s)
            if target is None:
                m = _LEGACY_SEQ_SPELLING_RE.match(scr_s)
                if m:
                    target = by_seq.get(int(m.group(1)))
            if target is not None:
                if _is_open(target):
                    _plan("source_ref", target, closure_seq,
                          f"data.source_commitment_ref {scr_s!r} resolves; "
                          "field is honored by no reader (external origin)")
                else:
                    ok += 1
                continue

        # Tier 2 — ≥0.8 title match against EXACTLY ONE open commitment.
        text = _closure_text(ev)
        if text:
            scored = [
                (c, _title_ratio(text, _commitment_field(c, "title") or ""))
                for c in opens
            ]
            hits = [(c, r) for c, r in scored if r >= TITLE_MATCH_THRESHOLD]
            if len(hits) == 1:
                target, ratio = hits[0]
                _plan("title", target, closure_seq,
                      f"title match {ratio:.2f} against exactly one open commitment")
                continue
            if len(hits) > 1:
                unrecoverable.append({
                    "closure_seq": closure_seq, "text": text[:120],
                    "reason": f"ambiguous — {len(hits)} open commitments match ≥ "
                              f"{TITLE_MATCH_THRESHOLD}; never guessed",
                })
                continue

        unrecoverable.append({
            "closure_seq": closure_seq,
            "text": (text or str(cid or ""))[:120],
            "reason": "no seq mapping and no unique title match",
        })

    # Tier 3 — in-place-mutated rows: closed-family status, no closure event.
    for c in by_id.values():
        status = _commitment_field(c, "status") or "open"
        if status not in _CLOSED_STATUSES:
            continue
        already_tombstoned = (_commitment_id(c) in resolved_ids) or (
            isinstance(c.get("seq"), int) and c["seq"] in resolved_seqs
        )
        if already_tombstoned:
            continue
        _plan("mutation", c, None,
              f"in-place status={status!r} with no closure event — "
              "formalized into append-only history (counts unchanged)")

    return {
        "ok": ok,
        "repairs": repairs,
        "needs_confirm": needs_confirm,
        "unrecoverable": unrecoverable,
        "n_open_before": len(load_open_commitments(events_path)),
    }


def snapshot(workspace_root) -> Path | None:
    """Copy events.jsonl into _archive/ before any write (never delete).

    Returns None and creates NOTHING when there is no events.jsonl at the
    given root (BUG-8330 fix round, FX-2 — phantom-path class). The
    `mkdir(parents=True)` below would otherwise fabricate an `_archive/` tree
    under a mistyped root before the copy failed; `main()` guards the CLI, but
    `apply_repairs`/`snapshot` are importable and were reachable directly.
    """
    import datetime
    import shutil
    src = _events_path(workspace_root)
    if not src.exists():
        return None
    stamp = datetime.date.today().isoformat()
    dest_dir = Path(workspace_root) / "_archive" / f"events-jsonl_repair-snapshot_{stamp}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "events.jsonl"
    shutil.copy2(src, dest)
    return dest


def apply_repairs(workspace_root, plan: dict) -> dict:
    """Snapshot, then append one canonical tombstone per planned repair through
    THE closure path. Returns {snapshot, closed, already, errors}.

    Every write goes through `commitment_state.close_commitments`, which takes
    `writer_lock.events_writer_lock` per item — there is no raw rewrite here,
    so FX-2's read-modify-write class does not apply. What DID apply is the
    phantom-path class: a missing events.jsonl is now refused before anything
    is created.
    """
    from commitment_state import close_commitments

    snap = snapshot(workspace_root)
    if snap is None:
        return {"refused": f"no events.jsonl under {str(workspace_root)!r}",
                "snapshot": None, "closed": 0, "already": 0, "errors": []}
    results = close_commitments(
        workspace_root,
        [{
            "commitment_id": r["target_id"],
            "resolved_by": "closure-repair",
            "evidence": (
                f"repair tier={r['tier']}"
                + (f" from closure seq {r['closure_seq']}" if r.get("closure_seq") else "")
                + f" — {r['reason']}"
            ),
            "primary_thread_id": None,
            "extra_data": {
                "repair_tier": r["tier"],
                **({"repaired_from_seq": r["closure_seq"]} if r.get("closure_seq") else {}),
            },
        } for r in plan["repairs"]],
        source_skill=REPAIR_SOURCE_SKILL,
    )
    return {
        "snapshot": str(snap),
        "closed": sum(1 for x in results if x["status"] == "closed"),
        "already": sum(1 for x in results if x["status"] == "already_resolved"),
        "errors": [x for x in results if x["status"] == "error"],
    }


def render_report(plan: dict, *, applied: dict | None = None) -> str:
    lines = ["=== commitment-closure repair (F3, closure-repair-2026-07) ==="]
    lines.append(f"closures already readable: {plan['ok']}")
    lines.append(f"open commitments before:   {plan['n_open_before']}")
    lines.append(f"planned tombstones:        {len(plan['repairs'])}")
    for r in plan["repairs"]:
        lines.append(f"  [{r['tier']}] seq {r['target_seq']} — {r['title'][:60]!r} ({r['reason']})")
    if plan["needs_confirm"]:
        lines.append(f"pending_review — CONFIRM WITH M, never auto-closed: {len(plan['needs_confirm'])}")
        for r in plan["needs_confirm"]:
            lines.append(f"  [confirm] seq {r['target_seq']} — {r['title'][:60]!r}")
    if plan["unrecoverable"]:
        lines.append(f"unrecoverable — M's triage list: {len(plan['unrecoverable'])}")
        for u in plan["unrecoverable"]:
            lines.append(f"  [??] closure seq {u['closure_seq']} — {u['text']!r} ({u['reason']})")
    if applied is None:
        lines.append("MODE: PREVIEW — nothing written. Re-run with --apply (supervised) to write.")
    elif applied.get("refused"):
        lines.append(f"MODE: REFUSED — {applied['refused']}. Nothing written.")
    else:
        lines.append(f"MODE: APPLIED — snapshot at {applied['snapshot']}")
        lines.append(f"tombstones written: {applied['closed']} (already resolved: {applied['already']})")
        if applied["errors"]:
            lines.append(f"errors: {applied['errors']}")
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
    applied = apply_repairs(workspace_root, plan) if do_apply else None
    print(render_report(plan, applied=applied))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
