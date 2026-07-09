#!/usr/bin/env python3
"""
One-time closure-integrity audit — REPORT ONLY (v4.5.2 R1c).

WHY
---
The 2026-07-09 pre-build commitment-domain audit found, in the live history:
  - 83 duplicate resolve-on-top-of-resolve tombstones (benign for the loader
    — closure state is idempotent read-side — but they pollute resolution
    counts and value metrics);
  - exactly 1 true orphan closure (an empty `{}` resolution that references
    nothing);
  - 4 duplicate event seqs flagged by cleanup (1655 / 1703 / 1957 / 3397 —
    the F-38 seq race materialized historically).

This tool makes those findings inspectable on demand: it walks every event
shard read-only, replays closure state in append order, and prints
  1. ORPHAN closures — commitment_resolved events whose reference resolves
     to NO commitment under the full id amnesty (canonical ids, legacy seq
     spellings, seq-alias fields);
  2. DUPLICATE tombstones — closures whose target was ALREADY closed at that
     point in history (and not reopened since);
  3. thread_resolved / commitment_superseded events that match no commitment
     (informational — thread_resolved legitimately closes threads too);
  4. DUPLICATE seqs — seq values appearing on more than one event.

It changes NOTHING. There is no apply/repair flag by design — events.jsonl
is append-only history; any remediation is a separately-approved, supervised
operation. (The write-side fixes shipped alongside this tool: close paths
check state inside the writer lock, and the event gate validates explicit
commitment ids — so these classes stop growing.)

USAGE
    python audit_closure_integrity.py <workspace_root> [--limit N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from commitment_state import (  # noqa: E402
    _closer_target_id,
    _closer_target_seqs,
)
from cru_match import _commitment_id  # noqa: E402
from event_types import LEGACY_SEQ_ID_RE  # noqa: E402


CLOSER_TYPES = ("commitment_resolved", "thread_resolved", "commitment_superseded")


def _iter_all_events(workspace_root):
    """All events across shards + active file, append order, read-only."""
    import json
    try:
        from events_io import shard_paths
        paths = shard_paths(workspace_root)
    except Exception:
        paths = [Path(workspace_root) / "_hq" / "data" / "events.jsonl"]
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict):
                    yield ev


def _resolve_target(ev, by_id, by_seq):
    """Canonical id a closer event references, or None when nothing matches.
    Mirrors normalize_commitment_id's amnesty WITHOUT raising: exact id →
    legacy seq spelling → seq-alias fields."""
    raw = _closer_target_id(ev)
    if raw:
        raw_s = str(raw).strip()
        if raw_s in by_id:
            return raw_s
        m = LEGACY_SEQ_ID_RE.match(raw_s)
        if m:
            target = by_seq.get(int(m.group(1)))
            if target is not None:
                return _commitment_id(target)
    for s in _closer_target_seqs(ev):
        target = by_seq.get(s)
        if target is not None:
            return _commitment_id(target)
    return None


def _label(ev):
    return (f"seq={ev.get('seq')} ts={ev.get('ts')} "
            f"source={ev.get('source_skill')} data.keys="
            f"{sorted((ev.get('data') or {}).keys())}")


def run_audit(workspace_root, *, limit: int = 50) -> dict:
    """Replay closure state and print the report. Returns the findings dict
    (tests use it). NEVER writes anything."""
    events = list(_iter_all_events(workspace_root))

    # Final commitment index (ids + seqs) — the resolution universe.
    by_id: dict = {}
    by_seq: dict = {}
    for ev in events:
        if (ev.get("type") or ev.get("event")) == "commitment":
            by_id[_commitment_id(ev)] = ev
            if isinstance(ev.get("seq"), int) and not isinstance(ev.get("seq"), bool):
                by_seq[ev["seq"]] = ev

    orphans = []          # commitment_resolved resolving to nothing
    duplicates = []       # closures whose target was already closed
    informational = []    # thread/superseded closers matching no commitment
    closed: set = set()   # canonical ids currently closed, replayed in order
    seq_seen: dict = {}   # seq -> [event types]

    for ev in events:
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            seq_seen.setdefault(seq, []).append(ev.get("type"))
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et in CLOSER_TYPES:
            cid = _resolve_target(ev, by_id, by_seq)
            if cid is None:
                if et == "commitment_resolved":
                    orphans.append(ev)
                else:
                    informational.append(ev)
                continue
            if cid in closed:
                duplicates.append((cid, ev))
            else:
                closed.add(cid)
        elif et == "commitment_reopened":
            target = d.get("commitment_id") or d.get("target_id") or ev.get("commitment_id")
            if target and str(target) in closed:
                closed.discard(str(target))
            v = d.get("commitment_seq")
            if isinstance(v, str) and v.strip().isdigit():
                v = int(v.strip())
            if isinstance(v, int) and not isinstance(v, bool):
                t = by_seq.get(v)
                if t is not None:
                    closed.discard(_commitment_id(t))

    dup_seqs = {s: types for s, types in seq_seen.items() if len(types) > 1}

    print("Closure-integrity audit — REPORT ONLY (nothing written)")
    print(f"events scanned: {len(events)} · commitments: {len(by_id)} · "
          f"closer events: {sum(1 for e in events if (e.get('type') or '') in CLOSER_TYPES)}")
    print()
    print(f"1. ORPHAN closures (commitment_resolved matching nothing): {len(orphans)}")
    for ev in orphans[:limit]:
        print(f"   {_label(ev)}")
    print(f"2. DUPLICATE tombstones (target already closed): {len(duplicates)}")
    for cid, ev in duplicates[:limit]:
        print(f"   target={cid} closer: {_label(ev)}")
    if len(duplicates) > limit:
        print(f"   ... and {len(duplicates) - limit} more")
    print(f"3. thread/superseded closers matching no commitment "
          f"(informational): {len(informational)}")
    print(f"4. DUPLICATE seqs: {len(dup_seqs)}")
    for s in sorted(dup_seqs)[:limit]:
        print(f"   seq {s}: {dup_seqs[s]}")
    print()
    print("REPORT ONLY — no file was modified; this tool has no repair mode. "
          "Loader state is unaffected by these findings (closure is "
          "idempotent read-side); they pollute counts, not truth.")
    return {"orphans": orphans, "duplicates": duplicates,
            "informational": informational, "dup_seqs": dup_seqs}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Closure-integrity audit (report-only)")
    ap.add_argument("workspace_root")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    run_audit(args.workspace_root, limit=args.limit)
