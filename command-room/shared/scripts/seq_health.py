#!/usr/bin/env python3
"""seq_health — recurring duplicate-seq detector (BUG-8330 item 7c).

The appender has allocated seq inside the writer lock since SPEC A1, so NEW
collisions should not occur — but ~140 historic duplicates predate it (the
pre-atomic-write window, CHANGELOG v3.13.8.x), and a dup seq is live ambiguity:
an F3 seq-alias closure (`source_event_seq`) resolves to EVERY commitment at
that seq, and the `commitment_seq_<n>` id fallback collides the same way.

This detector runs recurring (cleanup's weekly pass; system-health reads the
report on demand) instead of the one-shot report `backfill_substrate.py` was:

  - `scan(events_path)` — pure read: every seq held by 2+ events.
  - `detect_and_mark(workspace_root, apply=True)` — appends ONE additive
    `seq_repaired` marker per NEWLY-found duplicated seq. The marker is the
    detector's own memory: already-marked seqs are not re-reported, so the
    Monday note only ever surfaces NEW collisions (which, post-A1, indicate
    a real writer bug worth eyes). History is never rewritten; the events
    holding the duplicate seq stay exactly as written.

Marker shape:
  {"type": "seq_repaired", "source_skill": "<caller>",
   "data": {"duplicate_seq": N, "n_occurrences": k,
            "event_types": [...], "detected_by": "seq_health"}}

Readers: this module (dedup of its own reports), cleanup's Monday note and
system-health's report line (both render the counts).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

EPOCH_THRESHOLD = 10**10  # next_seq.py contract — nano-epoch artifacts excluded


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def scan(events_path) -> Dict[int, List[dict]]:
    """seq → list of events holding it, for every seq held by 2+ events.
    Pure read; malformed lines are skipped (loader contract)."""
    from cru_match import load_events_defensively
    events, _skipped = load_events_defensively(Path(events_path))
    by_seq: Dict[int, List[dict]] = {}
    for ev in events:
        seq = ev.get("seq")
        if (isinstance(seq, int) and not isinstance(seq, bool)
                and seq < EPOCH_THRESHOLD):
            by_seq.setdefault(seq, []).append(ev)
    return {s: evs for s, evs in by_seq.items() if len(evs) > 1}


def detect_and_mark(workspace_root, *, apply: bool = False,
                    source_skill: str = "seq_health") -> dict:
    """Scan for duplicate seqs; report NEW ones (not yet covered by a
    `seq_repaired` marker). With apply=True, append one additive marker per
    new duplicate so the next run treats it as known.

    Returns {"n_duplicate_seqs", "n_new", "new": [...], "marked": bool}.

    CONCURRENCY (BUG-8330 fix round, FX-2): the mark path is a
    read-decide-append, so under `apply=True` the WHOLE of it runs inside
    `writer_lock.events_writer_lock` and the report is re-derived in there.
    Unlocked, a `seq_repaired` marker appended by a concurrent run between
    this scan and this append is invisible to the dedup check and the same
    duplicate gets marked twice. (Unlike the seq-relocation repair this is an
    APPEND, never a truncating rewrite, so the failure is a duplicate marker,
    not a destroyed event.) A read-only run (`apply=False`) takes no lock.

    PHANTOM PATHS: a missing events.jsonl is refused before the lock is taken
    — acquiring it would create `<root>/_hq/data/.writer.lock` and fabricate a
    substrate tree under a mistyped root.
    """
    events_path = _events_path(workspace_root)
    if not events_path.exists():
        return {"n_duplicate_seqs": 0, "n_new": 0, "new": [], "marked": False,
                "refused": f"no events.jsonl under {str(workspace_root)!r}"}
    if not apply:
        return _detect(workspace_root)
    from writer_lock import events_writer_lock
    with events_writer_lock(events_path, holder=source_skill):
        report = _detect(workspace_root)  # RE-DERIVED inside the lock
        if report["new"]:
            from atomic_write import atomic_append_jsonl
            atomic_append_jsonl(events_path, [{
                "type": "seq_repaired",
                "source_skill": source_skill,
                "data": dict(entry, detected_by="seq_health"),
            } for entry in report["new"]], holder=source_skill)
            report["marked"] = True
        return report


def _detect(workspace_root) -> dict:
    """Pure read half of `detect_and_mark` — the duplicate scan and the
    already-marked dedup, with nothing written. Callers on the mark path hold
    `events_writer_lock` around this."""
    from cru_match import load_events_defensively
    events_path = _events_path(workspace_root)
    dups = scan(events_path)

    already_marked = set()
    events, _skipped = load_events_defensively(events_path)
    for ev in events:
        if ev.get("type") == "seq_repaired":
            v = (ev.get("data") or {}).get("duplicate_seq")
            if isinstance(v, int):
                already_marked.add(v)

    new = []
    for seq in sorted(dups):
        if seq in already_marked:
            continue
        evs = dups[seq]
        new.append({
            "duplicate_seq": seq,
            "n_occurrences": len(evs),
            "event_types": sorted({str(e.get("type") or "?") for e in evs}),
        })

    return {
        "n_duplicate_seqs": len(dups),
        "n_new": len(new),
        "new": new,
        "marked": False,
    }


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: seq_health.py <workspace_root> [--mark]", file=sys.stderr)
        return 2
    import json
    report = detect_and_mark(argv[1], apply="--mark" in argv[2:])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
