#!/usr/bin/env python3
"""Derive who is on a thread from the event substrate — ranked, recency-aware,
lineage-aware, with human overrides. The reader half of the brain-substrate
fix: membership is RECOMPUTED from events every time, never stored as a frozen
`members[]` field (which would re-create the maintenance trap).

Design (brain-substrate-drift audit, 2026-05-30):
  - Uses `event_refs` for dual-layer thread/person extraction.
  - LINEAGE-AWARE: a thread that was spawned from / parented by an archived
    umbrella (e.g. the 3 live Command Room threads all carry
    parent_thread_id = project_001) inherits the umbrella's people as
    `inherited` candidates — lower confidence, surfaced for the confirm-gate —
    so real members tagged only to the pre-split umbrella (the Wetsels) are
    NOT silently dropped. Verified against the live substrate.
  - OVERRIDES ONLY are persisted: `thread.roster_overrides = {pin:[...],
    suppress:[...]}`. Pins force-include (e.g. a durable contact with no
    events); suppresses force-exclude (kill cross-thread bleed the CEO
    rejected). Everything else is recomputed.

stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

import event_refs

# Inherited (pre-split umbrella) events count for less than direct events.
INHERITED_WEIGHT = 0.5
# A person needs at least this much direct signal to be auto-confirmed "high".
HIGH_DIRECT_FLOOR = 2


def _entities(workspace_root: Path) -> dict:
    data = json.loads((workspace_root / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    return data["entities"] if isinstance(data.get("entities"), dict) else data


def _thread_by_id(threads: list[dict], thread_id: str) -> dict | None:
    return next((t for t in threads if t.get("id") == thread_id), None)


def _lineage_ids(thread: dict) -> set[str]:
    """Predecessor thread ids this thread inherits roster from (its umbrella /
    the thread it was split out of)."""
    out = set()
    for k in ("parent_thread_id", "spawned_from_thread_id"):
        v = thread.get(k)
        if isinstance(v, str) and v:
            out.add(v)
    return out


def derive_roster(workspace_root: str | Path, thread_id: str) -> list[dict]:
    """Return a ranked roster for `thread_id`:
        [{person_id, name, n_direct, n_inherited, n_events, last_ts,
          score, source, confidence}]
    sorted by score descending. `source` ∈ {direct, inherited, pinned};
    `confidence` ∈ {high, low, inherited, pinned}.
    """
    workspace_root = Path(workspace_root)
    ent = _entities(workspace_root)
    people = ent.get("people", [])
    threads = ent.get("threads", []) or ent.get("projects", [])
    pmap = {p.get("id"): p.get("canonical_name") for p in people}

    thread = _thread_by_id(threads, thread_id) or {}
    lineage = _lineage_ids(thread)
    overrides = thread.get("roster_overrides") or {}
    pin = {p for p in (overrides.get("pin") or [])}
    suppress = {p for p in (overrides.get("suppress") or [])}

    events = event_refs.load_events(workspace_root / "_hq" / "data" / "events.jsonl")

    agg: dict[str, dict] = {}
    for ev in events:
        tset = event_refs.threads_of(ev)
        direct = thread_id in tset
        inherited = (not direct) and bool(tset & lineage)
        if not (direct or inherited):
            continue
        ts = event_refs.event_ts(ev)
        for pid in event_refs.persons_of(ev):
            if pid in suppress:
                continue
            rec = agg.setdefault(pid, {"n_direct": 0, "n_inherited": 0, "last": ""})
            if direct:
                rec["n_direct"] += 1
            else:
                rec["n_inherited"] += 1
            if ts > rec["last"]:
                rec["last"] = ts

    roster: list[dict] = []
    for pid, rec in agg.items():
        nd, ni = rec["n_direct"], rec["n_inherited"]
        score = nd + ni * INHERITED_WEIGHT
        if nd >= HIGH_DIRECT_FLOOR:
            source, confidence = "direct", "high"
        elif nd > 0:
            source, confidence = "direct", "low"
        else:
            source, confidence = "inherited", "inherited"
        roster.append({
            "person_id": pid,
            "name": pmap.get(pid, pid),
            "n_direct": nd,
            "n_inherited": ni,
            "n_events": nd + ni,
            "last_ts": rec["last"][:10],
            "score": round(score, 2),
            "source": source,
            "confidence": confidence,
        })

    # Pins: force-include even with zero events (durable contacts).
    present = {r["person_id"] for r in roster}
    for pid in pin:
        if pid in present:
            for r in roster:
                if r["person_id"] == pid:
                    r["source"], r["confidence"] = "pinned", "pinned"
        else:
            roster.append({
                "person_id": pid, "name": pmap.get(pid, pid),
                "n_direct": 0, "n_inherited": 0, "n_events": 0, "last_ts": "",
                "score": 9_999, "source": "pinned", "confidence": "pinned",
            })

    roster.sort(key=lambda r: (-r["score"], r["name"]))
    return roster


if __name__ == "__main__":
    import sys
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    tid = sys.argv[2] if len(sys.argv) > 2 else "project_016"
    for r in derive_roster(ws, tid):
        print(f"{r['person_id']:12s} {str(r['name'])[:22]:22s} "
              f"direct={r['n_direct']:3d} inherited={r['n_inherited']:3d} "
              f"last={r['last_ts']} {r['confidence']}")
