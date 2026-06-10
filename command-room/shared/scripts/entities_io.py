#!/usr/bin/env python3
"""Canonical wrapper-aware access to entities.json collections.

Single source of truth for the nested-vs-flat shape split that fragmented
newly-created orgs/people into a shadow store the readers never saw
(deep-audit 2026-05-29, finding #2 — the writer split-brain).

entities.json stores its collections in one of two shapes:

    flat   : {"people": [...], "orgs": [...], "threads": [...]}
    nested : {"entities": {"people": [...], "orgs": [...], "threads": [...]}}

The readers (entity_resolve, build_workspace_map_input, integrity_check)
already unwrap to the nested `entities` mapping when present. The WRITERS did
not — they `data.setdefault("orgs", [])` against the flat top level, so on a
canonical nested workspace every new record landed in a brand-new flat key
that no reader, resolver, projector, or briefing ever looked at, while the id
generator (max+1 over the empty flat list) collided with the real records.

Every reader AND writer of an entities.json collection MUST go through these
helpers so producer and consumer agree on where the data lives.
"""

from __future__ import annotations


def unwrap_entities(data: dict) -> dict:
    """Return the dict that actually holds the entity collections — the inner
    `entities` mapping when present (canonical nested shape), else `data`
    itself (legacy flat shape)."""
    inner = data.get("entities")
    return inner if isinstance(inner, dict) else data


def entities_collection(data: dict, name: str) -> list:
    """Return the LIVE collection list for `name` ('people' / 'orgs' /
    'threads' / 'projects'), honoring the canonical nested wrapper so reads
    see the real records and writes land where the readers look.

    Creates the list in place when absent. Because the returned list is the
    same object stored inside `data`, mutating it (append / in-place filter)
    and then writing `data` back persists correctly under either shape.
    """
    container = unwrap_entities(data)
    coll = container.get(name)
    if not isinstance(coll, list):
        coll = []
        container[name] = coll
    return coll
