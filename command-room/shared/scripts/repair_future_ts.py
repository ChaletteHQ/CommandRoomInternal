#!/usr/bin/env python3
"""CLOCKTS1 — restore the real time of rows a poisoned clock floor re-stamped.

WHAT WENT WRONG, AND WHY A BACKUP CANNOT FIX IT
-----------------------------------------------
A caller-supplied `ts` ahead of the machine clock became the ledger maximum;
the append gate read that maximum, treated the lead as proof the clock was
behind, and stamped the NEXT append with the stored future instant instead of
the machine reading. That row carried the same maximum, so it re-poisoned the
next one. Self-sustaining until real time passed the seed.

Nothing rewrote existing rows. Each one was written WRONG AT CREATION, so no
clean copy of it ever existed and no backup, snapshot or Drive version history
can restore it. Whatever this tool cannot reconstruct from the trail the writes
themselves left behind is genuinely gone, and the report says so per row rather
than quietly guessing.

THE TWO TRAILS
--------------
EXACT — `machine_ts`. The append gate annotated every floor-corrected row with
`ts_source: substrate_floor` and `machine_ts`, the reading the machine actually
gave. That is the true time, verbatim, and restoring it is not an estimate.

INTERPOLATED — `seq` order between exact anchors. Rows stamped through the
~20 `trusted_now()` writer helpers carry no annotation (this is the reported
897 / 345 split). What they do carry is `seq`, which is allocated strictly
monotonically inside the writer lock, so a row between two exactly-known
anchors was written between their two times. That places it to within the gap,
and the gap is reported alongside so nobody mistakes a wide one for precision.

UNRECOVERABLE — a poisoned row with no exact anchor on one side (typically the
head or tail of a contaminated run). Reported, never guessed at, left alone.

WHY INTERPOLATION IS OPT-IN AND EXACT IS NOT
--------------------------------------------
An unannotated forward-dated row is one of two things, and NOTHING IN THE
SUBSTRATE TELLS THEM APART: a site-B victim (a writer helper stamped it from a
poisoned floor and left no trail), or the SEED itself — a caller who supplied
that `ts` deliberately, because the meeting really is on that date. The
annotated rows carry `machine_ts` and are unambiguous; these do not.

So the default rewrites ONLY the exact ones. Interpolation is offered, counted
and reported, but applied only under `--interpolate`, because overwriting
someone's stated time on their behalf is the same class of silent overwrite
this tool exists to undo — and getting it wrong on a seed destroys the only
record of what they meant. Post-CLOCKTS1 a seed is inert where it sits: it can
no longer move any other row's stamp, which is what made it dangerous.

CONCURRENCY
-----------
`apply` holds `writer_lock.events_writer_lock` — the SAME OS byte-range lock
every gated append takes — across the WHOLE read-modify-write, and RE-DERIVES
the plan inside it. A rewrite is a truncating write: an append landing between
the read and the write is silently DESTROYED, and line indices computed outside
the lock can be invalidated by that same append. The caller-supplied plan is
advisory only — it is what the CLI already printed, never what gets written.
Same pattern as `backfill_substrate.apply` and `backfill_meeting_binding`.

Every write routes through `atomic_write.atomic_write_text`, so the ledger is
replaced by rename and never observed half-written.

DRY RUN IS THE DEFAULT. `--apply` is required to write, and the operator runs
it deliberately, per workspace, having read the report.

Usage:
    python repair_future_ts.py <workspace_root>                 # report only
    python repair_future_ts.py <workspace_root> --apply         # exact only
    python repair_future_ts.py <ws> --apply --interpolate       # + placed rows
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _events_path(workspace_root) -> Path:
    ws = Path(workspace_root)
    if ws.name == "events.jsonl":
        return ws
    try:
        from data_root import resolve as _resolve_data_root

        return _resolve_data_root(ws) / "events.jsonl"
    except Exception:
        return ws / "_hq" / "data" / "events.jsonl"


def _parse(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        from event_time import parse_ts

        parsed = parse_ts(value)
    except Exception:
        return None
    if parsed is None:
        return None
    return parsed.astimezone(_dt.timezone.utc)


def _row_ts(ev: dict):
    """The row's effective timestamp on the live `ts` -> `timestamp` -> `date`
    priority order — the SAME order `trusted_now.scan_jsonl_text` uses to build
    the maximum. A repair that read a different field than the poisoning read
    would fix rows that were never broken and miss the ones that were."""
    for field in ("ts", "timestamp", "date"):
        value = ev.get(field)
        if isinstance(value, str) and value.strip():
            return field, _parse(value)
    return None, None


def build_plan(events_path) -> dict:
    """The repair plan for one ledger. Pure, read-only, never raises.

    Returns `{ok, reason, n_rows, exact, interpolated, unrecoverable, edits,
    seqhw_updated}` where `edits` is `[{line, seq, field, old, new, method,
    gap_seconds}]` in file order.
    """
    p = Path(events_path)
    out = {
        "ok": False,
        "reason": None,
        "events_path": str(p),
        "n_rows": 0,
        "exact": 0,
        "interpolated": 0,
        "unrecoverable": 0,
        "edits": [],
        "seqhw_updated": None,
    }
    if not p.exists():
        out["reason"] = f"events.jsonl not found at {p}"
        return out
    try:
        from trusted_now import forward_dated_lead, read_seqhw_updated
    except Exception as exc:  # pragma: no cover - import guard
        out["reason"] = f"trusted_now unavailable: {exc}"
        return out
    witness = read_seqhw_updated(p)
    out["seqhw_updated"] = witness.isoformat() if witness else None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        out["reason"] = f"could not read {p}: {exc}"
        return out

    rows = []  # (line_index, parsed_dict_or_None, field, ts, machine_ts, seq)
    for idx, line in enumerate(text.split("\n")):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        out["n_rows"] += 1
        field, ts = _row_ts(ev)
        seq = ev.get("seq")
        seq = int(seq) if (isinstance(seq, (int, float))
                           and not isinstance(seq, bool)
                           and seq < 10 ** 10) else None
        machine_ts = _parse(ev.get("machine_ts"))
        # PER-ROW, with the row's own `machine_ts` preferred over the moving
        # witness (fix round, F-1). Judging every row against the single live
        # `.seqhw.updated` made the tool report "nothing to repair" on the very
        # dose the spec names as its first run, because fourteen days of later
        # activity had carried the witness past the poisoned rows.
        bad, _lead, reference = forward_dated_lead(ts, machine_ts, witness)
        rows.append({
            "line": idx,
            "field": field,
            "ts": ts,
            "machine_ts": machine_ts,
            "seq": seq,
            "poisoned": bad,
            "reference": reference,
        })

    # Seq order is the WRITE order — the only order that makes interpolation
    # sound. File order usually agrees, but a rotation or a merge can leave the
    # newest stamp anywhere in the file, which is the same reason the
    # corroboration read takes a max rather than a tail.
    ordered = sorted([r for r in rows if r["seq"] is not None],
                     key=lambda r: r["seq"])

    # An ANCHOR is a row whose real write time is known: an exact restore
    # (machine_ts present) or a row that was never poisoned in the first place.
    def _anchor_time(r):
        if r["machine_ts"] is not None:
            return r["machine_ts"]
        if not r["poisoned"] and r["ts"] is not None:
            return r["ts"]
        return None

    anchors = [(i, _anchor_time(r)) for i, r in enumerate(ordered)]

    for i, r in enumerate(ordered):
        if not r["poisoned"] or r["field"] is None:
            continue
        if r["machine_ts"] is not None:
            out["exact"] += 1
            out["edits"].append({
                "line": r["line"], "seq": r["seq"], "field": r["field"],
                "old": r["ts"].isoformat(), "new": r["machine_ts"].isoformat(),
                "method": "machine_ts", "gap_seconds": 0.0,
            })
            continue
        before = next((t for j, t in reversed(anchors[:i]) if t is not None),
                      None)
        after = next((t for j, t in anchors[i + 1:] if t is not None), None)
        if before is None or after is None or after < before:
            out["unrecoverable"] += 1
            continue
        gap = (after - before).total_seconds()
        out["interpolated"] += 1
        out["edits"].append({
            "line": r["line"], "seq": r["seq"], "field": r["field"],
            "old": r["ts"].isoformat(),
            "new": (before + (after - before) / 2).isoformat(),
            "method": "seq_interpolation", "gap_seconds": gap,
        })

    out["edits"].sort(key=lambda e: e["line"])
    out["ok"] = True
    return out


def apply(events_path, plan=None, *, interpolate: bool = False,
          timeout_s: float = 30.0) -> dict:
    """Rewrite the ledger under the events writer lock. The plan is RE-DERIVED
    inside the lock (module docstring, CONCURRENCY); any `plan` passed in is
    advisory only — it is what the CLI already printed, never what gets
    written. Raises TimeoutError rather than writing unlocked.

    `interpolate=False` (the default) rewrites ONLY rows with a `machine_ts`
    trail — see WHY INTERPOLATION IS OPT-IN in the module docstring."""
    p = Path(events_path)
    if not p.exists():
        # Acquiring the lock would CREATE `.writer.lock` beside a path that
        # holds no ledger, fabricating a substrate tree at a mistyped path
        # (the backfill_meeting_binding phantom-tree lesson).
        return {"applied": False, "reason": f"events.jsonl not found at {p}"}
    try:
        from writer_lock import events_writer_lock
    except ImportError:  # pragma: no cover - packaged import
        from .writer_lock import events_writer_lock  # type: ignore
    with events_writer_lock(p, holder="repair_future_ts", timeout_s=timeout_s):
        return _apply_locked(p, interpolate)


def _apply_locked(events_path: Path, interpolate: bool = False) -> dict:
    """The critical section of `apply`. Callers MUST already hold
    `writer_lock.events_writer_lock` — this truncates and rewrites
    events.jsonl, so running it unlocked silently destroys any append that
    lands between its read and its write."""
    from atomic_write import atomic_write_text

    plan = build_plan(events_path)
    if not plan["ok"]:
        return {"applied": False, "reason": plan["reason"], "plan": plan}
    chosen = [e for e in plan["edits"]
              if interpolate or e["method"] == "machine_ts"]
    if not chosen:
        return {"applied": False, "reason": "nothing to repair", "plan": plan}

    text = events_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    by_line = {e["line"]: e for e in chosen}
    n = 0
    for idx, edit in by_line.items():
        if idx >= len(lines):
            continue
        try:
            ev = json.loads(lines[idx])
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        # The repaired row keeps a record of what it was and how it was placed.
        # A silent correction of a silent corruption is still something nobody
        # can audit afterwards.
        ev["ts_repaired_from"] = edit["old"]
        ev["ts_repair_method"] = edit["method"]
        ev[edit["field"]] = edit["new"]
        if ev.get("ts_source") == "substrate_floor":
            del ev["ts_source"]
        lines[idx] = json.dumps(ev, ensure_ascii=False)
        n += 1

    atomic_write_text(events_path, "\n".join(lines), encoding="utf-8")
    return {"applied": True, "n_repaired": n, "plan": plan}


def format_report(plan: dict) -> str:
    """The per-workspace report: what is recoverable, what is not, and how
    precisely — in the operator's terms, not the ledger's."""
    if not plan.get("ok"):
        return f"  cannot classify: {plan.get('reason')}"
    total = plan["exact"] + plan["interpolated"] + plan["unrecoverable"]
    if total == 0:
        return (f"  {plan['n_rows']} row(s) scanned — no forward-dated stamps. "
                f"Nothing to repair.")
    out = [
        f"  {plan['n_rows']} row(s) scanned against the .seqhw witness "
        f"({plan['seqhw_updated']})",
        f"  {total} forward-dated stamp(s) found:",
        f"    {plan['exact']} restorable EXACTLY (machine_ts trail present) "
        f"— repaired by default",
        f"    {plan['interpolated']} placeable by seq order between known "
        f"anchors — NOT written unless you pass --interpolate, because a row "
        f"with no trail may be the deliberate seed rather than a victim",
        f"    {plan['unrecoverable']} NOT recoverable (no anchor on one side) "
        f"— left untouched",
    ]
    gaps = [e["gap_seconds"] for e in plan["edits"]
            if e["method"] == "seq_interpolation"]
    if gaps:
        out.append(f"    widest interpolation gap: {max(gaps) / 60.0:.1f} "
                   f"minute(s)")
    # THE RESIDUE, STATED. A row written through the ~20 `trusted_now()` writer
    # helpers carries no `machine_ts`, so the only thing that can flag it is the
    # `.seqhw` witness — and the witness moves forward with every append. Once
    # real time carries it past such a row, NO predicate over this substrate can
    # see that the row was ever wrong. That is irreducible, and it belongs in
    # the report rather than in silence: the counts above are what is visible
    # TODAY, not a guarantee that nothing else was touched.
    no_trail = plan["interpolated"] + plan["unrecoverable"]
    if no_trail:
        out.append(
            f"    NOTE: {no_trail} of these carry no per-row record of their "
            f"real write time. Rows like that are only detectable while the "
            f"log's last-write marker is still newer than they are — run this "
            f"sooner rather than later, and treat the counts as a floor.")
    return "\n".join(out)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    do_apply = "--apply" in argv
    do_interp = "--interpolate" in argv
    argv = [a for a in argv if a not in ("--apply", "--interpolate")]
    if not argv:
        print(__doc__)
        return 2
    path = _events_path(argv[0])
    plan = build_plan(path)
    print(f"CLOCKTS1 timestamp repair — {path}")
    print(format_report(plan))
    if not do_apply:
        print("\n  DRY RUN (default). Re-run with --apply to write.")
        return 0
    result = apply(path, plan, interpolate=do_interp)
    if result.get("applied"):
        print(f"\n  APPLIED — {result['n_repaired']} row(s) repaired.")
        return 0
    print(f"\n  NOT APPLIED — {result.get('reason')}")
    return 1


__all__ = ["build_plan", "apply", "format_report", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
