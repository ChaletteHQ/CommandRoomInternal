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
    """Best-effort ISO timestamp string for ordering/recency (top-level `ts`,
    else nested). Returns '' when absent — callers compare lexically, which is
    correct for ISO-8601."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return ev.get("ts") or d.get("ts") or ev.get("created_ts") or d.get("created_ts") or ""


def event_seq(ev: dict):
    """The event's seq (identity backbone), or None."""
    s = ev.get("seq")
    return s if isinstance(s, (int, float)) and not isinstance(s, bool) else None


def load_events(events_jsonl_path: str | Path) -> list[dict]:
    """Defensively load events.jsonl, skipping blank/unparseable/non-dict
    lines. (Mirrors the cru_match defensive loader; kept dependency-free.)"""
    path = Path(events_jsonl_path)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
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
