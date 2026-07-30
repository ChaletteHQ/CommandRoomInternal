#!/usr/bin/env python3
"""Canonical dual-layer extraction of thread + person references from events.

THE PROBLEM THIS SOLVES (deep-audit + brain-substrate-drift audit, 2026-05):
events.jsonl is schema-heterogeneous. Newer events carry references at the
TOP level (`primary_thread_id`, `person_ids`); older `workspace-ingest`
backfill carries them NESTED under `data` (`data.project_id`,
`data.person_ids`). A scan that reads only one layer silently under-reads the
other — which is exactly how a prior linkage table came out ~10x low on
thread-links and how downstream consumers miss real involvement.

Every consumer that needs "which threads/people does this event touch" MUST
import `threads_of` / `persons_of` from here, so producer and consumer never
drift on where the reference lives. Verified to reproduce the corrected C5
linkage table against the live 2,453-event substrate.

stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def threads_of(ev: dict) -> set[str]:
    """Every thread/project id this event references, across both schema
    layers (top-level + nested `data`) and both the singular and list forms,
    including soft `related_thread_ids` cross-refs."""
    out: set[str] = set()
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for k in ("primary_thread_id", "thread_id", "project_id", "primary_project_id"):
        if ev.get(k):
            out.add(ev[k])
        if d.get(k):
            out.add(d[k])
    for k in ("related_thread_ids", "thread_ids", "related_project_ids"):
        for r in (ev.get(k) or []):
            out.add(r)
        for r in (d.get(k) or []):
            out.add(r)
    return {t for t in out if isinstance(t, str) and t}


def persons_of(ev: dict) -> set[str]:
    """Every person id this event references, across both schema layers and
    every observed person-bearing field (owner/requester/attendee/etc.).
    Filtered to ids that look like `person_*` so a stray org/thread id in a
    shared slot can't leak in."""
    out: set[str] = set()
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for r in (ev.get("person_ids") or []):
        out.add(r)
    for r in (d.get("person_ids") or []):
        out.add(r)
    for k in ("person_id", "owner_person_id", "requester_person_id", "inferred_person_id"):
        if d.get(k):
            out.add(d[k])
    for k in ("attendee_person_ids", "counterparty_person_ids", "people"):
        for r in (d.get(k) or []):
            out.add(r)
    for k in ("owner_id", "requester_id", "target_id"):
        v = d.get(k)
        if isinstance(v, str) and v.startswith("person_"):
            out.add(v)
    return {p for p in out if isinstance(p, str) and p.startswith("person_")}


def event_ts(ev: dict) -> str:
    """Best-effort ISO timestamp string for ordering/recency (canonical
    `ts` → `timestamp` → `date` via event_time, else nested). Returns '' when
    absent — callers compare lexically, which is correct for ISO-8601."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    try:
        from event_time import event_time
    except ImportError:  # pragma: no cover
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from event_time import event_time
    return event_time(ev) or d.get("ts") or ev.get("created_ts") or d.get("created_ts") or ""


def event_seq(ev: dict):
    """The event's seq (identity backbone), or None.

    UNDOGUARD: delegates to the canonical `event_seq.event_seq`. This
    function predates it and carried DIFFERENT semantics under the SAME name
    — it dropped a string seq as unreadable and passed floats through
    un-normalized. Two same-named helpers disagreeing about what a seq is
    was exactly the drift that lets a fix look complete while a caller keeps
    the old behavior, so this is now a thin alias rather than a second
    implementation. Kept as a re-export because callers already import it
    from here."""
    try:
        from event_seq import event_seq as _canonical
    except ImportError:
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from event_seq import event_seq as _canonical
    return _canonical(ev)


def load_events(events_jsonl_path: str | Path) -> list[dict]:
    """Defensively load events.jsonl, skipping blank/unparseable/non-dict
    lines. (Mirrors the cru_match defensive loader; kept dependency-free.)

    SPEC A5 — shard-transparent: when handed a path to an `events.jsonl`, this also
    includes any sibling `events-<year>.jsonl` shards so full-history callers see the
    whole timeline after a rotation. On an unsharded workspace the result is identical
    to the pre-A5 single-file read (no siblings exist). A non-`events.jsonl` path is
    read as-is (back-compat for callers pointing at a custom file)."""
    path = Path(events_jsonl_path)
    if path.name == "events.jsonl":
        try:
            from events_io import shard_paths
            paths = shard_paths(path)
        except Exception:
            paths = [path] if path.exists() else []
    else:
        paths = [path] if path.exists() else []
    out: list[dict] = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out


def events_for_threads(events: Iterable[dict], thread_ids: set[str]) -> list[dict]:
    """Subset of events touching any of `thread_ids` (via the dual-layer
    reader)."""
    tset = {t for t in thread_ids if t}
    return [ev for ev in events if threads_of(ev) & tset]
