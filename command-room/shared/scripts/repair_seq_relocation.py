#!/usr/bin/env python3
"""
repair_seq_relocation.py — supervised ONE-SHOT remap of a seq-relocated
ledger (BUG-8330 item 8).

⚠️  DO NOT run --apply unsupervised. This is the ONE sanctioned history
rewrite in the seq family: a ledger whose allocation ceiling was relocated by
a hand-stamped far-future seq (the observed case: one explicit `"seq": 999999`
made both max+1 allocators — and then the `.seqhw` sidecar — allocate above
1,000,000 forever). The write-side gate (`atomic_write.SEQ_GAP_MAX`) prevents
NEW relocations; this script repairs ledgers already relocated.

WHAT IT DOES
============
1. Detects the relocation point: the smallest seq that LEADS the contiguous
   body by more than SEQ_GAP_MAX (events below it are the body; events at or
   above it are the relocated band).
2. PREVIEW (default): prints the plan — band size, old→new ranges, every
   reference field that will be remapped — and writes NOTHING.
3. --apply (supervised): snapshots events.jsonl AND .seqhw to
   `_archive/events-jsonl_seq-remap-snapshot_<date>/`, then rewrites the
   ledger with the band renumbered contiguously after the body (append order
   preserved, ts untouched), remaps every seq-valued REFERENCE that pointed
   into the band (`*_seq` keys and `*_refs` lists — `source_event_seq`,
   `commitment_seq`, `target_seq`, `supersedes_seq`, `commitment_refs`, …),
   rewrites `.seqhw` to the new max, and appends a `substrate_backfill`
   marker recording the remap table size.

Reference remap is KEY-SCOPED (a key named `seq`, ending `_seq`, or a list
under a key ending `_refs`) and TABLE-SCOPED (only values that are exactly a
remapped old seq change), so an unrelated integer can never be rewritten.

CONCURRENCY (BUG-8330 fix round, FX-2)
======================================
The whole read-modify-write is held under `writer_lock.events_writer_lock` —
the SAME OS byte-range lock every gated append takes — and the plan is
RE-DERIVED inside it. This is not belt-and-braces: the rewrite is a
TRUNCATING write, so an append landing between the read and the write is
silently DESTROYED (the write puts the file back as it was before the
append). Worse here than in the general case: the concurrent appender
allocates its seq from the PRE-remap `.seqhw`, and this script then rewrites
`.seqhw` down to the new max — had the row survived, the ledger would carry
a seq a million above its own high-water mark and trip FS-04 forever.
Same defect class and same fix shape as PR #49's `backfill_meeting_binding`.

A caller-supplied `plan` is ADVISORY ONLY — it is what the CLI printed, never
what gets written. Line offsets and the remap table computed outside the lock
can be invalidated by any append that lands before the write.

PHANTOM PATHS: a missing `events.jsonl` is REFUSED before anything is
created. Both the archive `mkdir(parents=True)` and the lock acquisition
would otherwise fabricate a substrate tree under a mistyped root.

USAGE
=====
    python3 shared/scripts/repair_seq_relocation.py <workspace_root>          # preview
    python3 shared/scripts/repair_seq_relocation.py <workspace_root> --apply  # snapshot + rewrite
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import (  # noqa: E402
    SEQ_GAP_MAX,
    _write_seqhw,
    atomic_write_text,
)
from writer_lock import events_writer_lock  # noqa: E402

EPOCH_THRESHOLD = 10**10  # nano-epoch artifacts — never touched by this tool


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _seqhw_path(events_path: Path) -> Path:
    return events_path.with_name(events_path.name + ".seqhw")


def analyze(workspace_root) -> dict:
    """Pure analysis. Returns {relocated: bool, body_max, band: [...],
    remap: {old: new}, n_reference_fixes} — writes nothing."""
    events_path = _events_path(workspace_root)
    raw_lines = events_path.read_text(encoding="utf-8").splitlines()
    rows = []           # (line_idx, parsed dict or None)
    seqs = []
    for i, line in enumerate(raw_lines):
        line_s = line.strip()
        if not line_s:
            rows.append((i, None))
            continue
        try:
            ev = json.loads(line_s)
        except json.JSONDecodeError:
            rows.append((i, None))
            continue
        rows.append((i, ev if isinstance(ev, dict) else None))
        if isinstance(ev, dict):
            s = ev.get("seq")
            if (isinstance(s, int) and not isinstance(s, bool)
                    and s < EPOCH_THRESHOLD):
                seqs.append(s)

    if not seqs:
        return {"relocated": False, "reason": "no human-counter seqs"}

    # Relocation point: sort the distinct seqs; the band starts at the first
    # value that leads its predecessor by more than SEQ_GAP_MAX.
    distinct = sorted(set(seqs))
    band_start = None
    for prev, cur in zip(distinct, distinct[1:]):
        if cur - prev > SEQ_GAP_MAX:
            band_start = cur
            break
    if band_start is None:
        return {"relocated": False, "reason": "seq space is contiguous"}

    body_max = max(s for s in distinct if s < band_start)
    # Band events in APPEND order (file order), renumbered body_max+1, +2, …
    remap: dict[int, int] = {}
    next_new = body_max + 1
    for _i, ev in rows:
        if ev is None:
            continue
        s = ev.get("seq")
        if (isinstance(s, int) and not isinstance(s, bool)
                and band_start <= s < EPOCH_THRESHOLD and s not in remap):
            remap[s] = next_new
            next_new += 1

    n_refs = 0
    for _i, ev in rows:
        if ev is not None:
            n_refs += _count_reference_hits(ev, remap)

    return {
        "relocated": True,
        "body_max": body_max,
        "band_start": band_start,
        "n_band_events": len(remap),
        "remap": remap,
        "n_reference_fixes": n_refs,
        "new_max": next_new - 1,
    }


def _remap_value(key: str, value, remap: dict, counter: list):
    """Remap one value if the key is seq-scoped and the value is in the
    table. `counter` accumulates the number of changes (list of one int)."""
    seq_key = key == "seq" or key.endswith("_seq")
    refs_key = key.endswith("_refs")
    if seq_key and isinstance(value, int) and not isinstance(value, bool) \
            and value in remap:
        counter[0] += 1
        return remap[value]
    if refs_key and isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, int) and not isinstance(v, bool) and v in remap:
                counter[0] += 1
                out.append(remap[v])
            else:
                out.append(v)
        return out
    if isinstance(value, dict):
        return {k: _remap_value(k, v, remap, counter) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap_value(key, v, remap, counter) if isinstance(v, (dict, list))
                else v for v in value]
    return value


def _count_reference_hits(ev: dict, remap: dict) -> int:
    counter = [0]
    for k, v in ev.items():
        if k == "seq":
            continue  # the band event's own seq is the remap, not a reference
        _remap_value(k, v, remap, counter)
    return counter[0]


def apply_remap(workspace_root, plan: dict | None = None) -> dict:
    """Snapshot, rewrite the ledger with the remap applied, rewrite .seqhw,
    append a substrate_backfill marker. Supervised one-shot.

    Held under `writer_lock.events_writer_lock` for the WHOLE read-modify-write,
    and the plan is re-derived inside it (see the module docstring). The
    `plan` argument is advisory only — it is what the CLI already printed,
    never what gets written. Raises TimeoutError rather than writing unlocked
    if another writer holds the lock past the timeout.

    Refuses (returns `{"refused": ...}`, writes nothing, creates nothing) when
    there is no `events.jsonl` at the given root, or when the re-derived plan
    finds no relocation left to repair — the second case is what a concurrent
    or repeated `--apply` looks like from inside the lock.
    """
    events_path = _events_path(workspace_root)
    if not events_path.exists():
        # Refuse BEFORE the archive mkdir and BEFORE the lock: both create
        # directories, and a mistyped root must never grow a substrate tree.
        return {"refused": f"no events.jsonl under {str(workspace_root)!r}",
                "snapshot": None, "n_remapped": 0, "n_reference_fixes": 0,
                "new_max": None}
    with events_writer_lock(events_path, holder="repair_seq_relocation"):
        return _apply_remap_locked(workspace_root)


def _apply_remap_locked(workspace_root) -> dict:
    """The critical section of `apply_remap`. Callers MUST already hold
    `writer_lock.events_writer_lock` — this function truncates and rewrites
    events.jsonl, so running it unlocked silently destroys any append that
    lands between its read and its write."""
    import datetime
    import shutil

    events_path = _events_path(workspace_root)
    plan = analyze(workspace_root)  # RE-DERIVED inside the lock
    if not plan.get("relocated"):
        return {"refused": f"not relocated ({plan.get('reason')})",
                "snapshot": None, "n_remapped": 0, "n_reference_fixes": 0,
                "new_max": None}
    seqhw = _seqhw_path(events_path)
    stamp = datetime.date.today().isoformat()
    dest_dir = (Path(workspace_root) / "_archive"
                / f"events-jsonl_seq-remap-snapshot_{stamp}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(events_path, dest_dir / "events.jsonl")
    if seqhw.exists():
        shutil.copy2(seqhw, dest_dir / seqhw.name)

    remap = plan["remap"]
    out_lines = []
    n_ref_fixes = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        try:
            ev = json.loads(line_s)
        except json.JSONDecodeError:
            out_lines.append(line)  # malformed lines pass through verbatim
            continue
        if not isinstance(ev, dict):
            out_lines.append(line)
            continue
        counter = [0]
        new_ev = {k: (_remap_value(k, v, remap, counter) if k != "seq" else v)
                  for k, v in ev.items()}
        n_ref_fixes += counter[0]
        s = new_ev.get("seq")
        if (isinstance(s, int) and not isinstance(s, bool) and s in remap):
            new_ev["seq"] = remap[s]
        out_lines.append(json.dumps(new_ev, ensure_ascii=False))
    atomic_write_text(events_path, "\n".join(out_lines) + "\n")
    # Canonical sidecar writer — the sidecar is JSON ({"max_seq": …}); a bare
    # int here would be unreadable to _read_seqhw and trip FS-04 forever.
    _write_seqhw(events_path, plan["new_max"])

    from atomic_write import atomic_append_jsonl
    atomic_append_jsonl(events_path, [{
        "type": "substrate_backfill",
        "source_skill": "repair_seq_relocation",
        "data": {
            "kind": "seq-relocation-remap",
            "band_start": plan["band_start"],
            "n_band_events": plan["n_band_events"],
            "n_reference_fixes": n_ref_fixes,
            "new_max": plan["new_max"],
            "snapshot": str(dest_dir),
        },
    }], holder="repair_seq_relocation")

    return {"snapshot": str(dest_dir), "n_remapped": len(remap),
            "n_reference_fixes": n_ref_fixes, "new_max": plan["new_max"]}


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
    if not plan.get("relocated"):
        print(f"No seq relocation detected ({plan.get('reason')}). Nothing to do.")
        return 0
    print("=== seq-relocation remap (BUG-8330 item 8) ===")
    print(f"contiguous body max:   {plan['body_max']}")
    print(f"relocated band starts: {plan['band_start']}")
    print(f"band events to remap:  {plan['n_band_events']} "
          f"→ {plan['body_max'] + 1}..{plan['new_max']}")
    print(f"references to fix:     {plan['n_reference_fixes']}")
    if do_apply:
        applied = apply_remap(workspace_root, plan)
        if applied.get("refused"):
            # The plan is re-derived inside the writer lock; a refusal here
            # means the ledger changed between the preview and the write.
            print(f"MODE: REFUSED — {applied['refused']}. Nothing written.",
                  file=sys.stderr)
            return 2
        print(f"MODE: APPLIED — snapshot at {applied['snapshot']}")
        print(f"remapped {applied['n_remapped']} events, "
              f"fixed {applied['n_reference_fixes']} references, "
              f".seqhw now {applied['new_max']}")
    else:
        print("MODE: PREVIEW — nothing written. Re-run with --apply (supervised).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
