#!/usr/bin/env python3
"""Orphan-note re-route (SPEC MLK1 D2, M ruling 2026-07-21).

Pre-MLK1, a widget note typed with no action selected ("orphan note") was
captured as a `commitment_to_discuss` list item. The list is retired — the
live workspace showed those captures were context-free fragments,
undecidable later (UX review finding 11). The re-route: when the note's
source item resolves to a person or thread, hold the note THERE (a `note`
event on that person/thread); when nothing resolves, DECLINE honestly and
write nothing — a fragment saved nowhere beats a junk drawer.

Dispatch wiring: the widget carrier still emits the legacy wire id
`add to my list` with a `context` value (one shape for old and new
widgets); apply-choices routes every context-bearing tuple of that shape
through `reroute_orphan_note` below. A context-LESS `add to my list`
tuple is a stale-widget explicit click and keeps its original fossil
`commitment_to_discuss` write (never re-routed — MLK1 D1: an
already-rendered button must keep its meaning).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from entities_io import entities_collection, unwrap_entities  # noqa: E402
from event_gate import append_event  # noqa: E402

# The honest decline, verbatim (SPEC MLK1 D2). Surfaced in the apply ack;
# nothing is written for the item.
DECLINE_LINE = ("I need a person or project to pin that to — say more "
                "and I'll hold it properly")

# data fields a source event may carry a person linkage in, in trust order.
_PERSON_ID_FIELDS = ("person_id", "counterparty_id", "owner_id")
_PERSON_NAME_FIELDS = ("person", "person_name", "name")
_THREAD_ID_FIELDS = ("thread_id", "project_id")


def _load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_events(events_path: Path) -> list:
    events = []
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue  # defensive: malformed lines never block a read
                if isinstance(ev, dict):
                    events.append(ev)
    except OSError:
        pass
    return events


def _resolve_target(source_ev: Optional[dict], entities: dict) -> tuple:
    """(kind, entity_id) the note pins to, or (None, None).

    Resolution order — first hit against a REAL entity record wins:
      1. a person id the source event's data carries;
      2. a stored person NAME matching a canonical_name exactly
         (case-insensitive — deterministic, never fuzzy);
      3. the event envelope's primary_thread_id, then data thread fields.
    """
    if not isinstance(source_ev, dict):
        return None, None
    data = source_ev.get("data") if isinstance(source_ev.get("data"), dict) else {}

    people = entities_collection(entities, "people")
    person_ids = {p.get("id") for p in people if isinstance(p, dict)}
    by_name = {
        str(p.get("canonical_name") or "").strip().lower(): p.get("id")
        for p in people
        if isinstance(p, dict) and p.get("canonical_name") and p.get("id")
    }
    for field in _PERSON_ID_FIELDS:
        pid = data.get(field)
        if pid and pid in person_ids:
            return "person", pid
    for field in _PERSON_NAME_FIELDS:
        name = str(data.get(field) or "").strip().lower()
        if name and name in by_name:
            return "person", by_name[name]

    threads = entities_collection(entities, "threads")
    thread_ids = {t.get("id") for t in threads if isinstance(t, dict)}
    candidates = [source_ev.get("primary_thread_id")]
    candidates += [data.get(f) for f in _THREAD_ID_FIELDS]
    for tid in candidates:
        if tid and tid in thread_ids:
            return "thread", tid
    return None, None


def reroute_orphan_note(
    workspace_root,
    context_text: str,
    source_event_seq: Optional[int] = None,
    *,
    source_skill: str = "apply-choices",
) -> dict:
    """Route an orphan note to its resolved person/thread, or decline.

    Returns {"outcome": "noted", "target_kind": ..., "target_id": ...} after
    appending ONE `note` event (via the gated writer), or
    {"outcome": "declined", "line": DECLINE_LINE} with NOTHING written.
    Never writes a `commitment_to_discuss` — the list is retired (MLK1).
    """
    ws = Path(workspace_root)
    text = str(context_text or "").strip()
    if not text:
        return {"outcome": "declined", "line": DECLINE_LINE}

    entities_raw = _load_json(ws / "_hq" / "data" / "entities.json")
    entities = unwrap_entities(entities_raw) if isinstance(entities_raw, dict) else {}

    source_ev = None
    if source_event_seq is not None:
        events = _load_events(ws / "_hq" / "data" / "events.jsonl")
        for ev in events:
            if ev.get("seq") == source_event_seq:
                source_ev = ev
                break

    kind, target_id = _resolve_target(source_ev, entities)
    if kind is None:
        return {"outcome": "declined", "line": DECLINE_LINE}

    note_ev = {
        "type": "note",
        "source_skill": source_skill,
        "data": {
            "summary": text,
            "via": "orphan_note_capture",
        },
    }
    if source_event_seq is not None:
        note_ev["data"]["source_event_seq"] = source_event_seq
    if kind == "person":
        note_ev["data"]["person_id"] = target_id
    else:
        note_ev["primary_thread_id"] = target_id
    append_event(ws / "_hq" / "data" / "events.jsonl", note_ev,
                 holder="orphan_note")
    return {"outcome": "noted", "target_kind": kind, "target_id": target_id}


__all__ = ["DECLINE_LINE", "reroute_orphan_note"]
