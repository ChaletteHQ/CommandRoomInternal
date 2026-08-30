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

    SPEC DUALKEY1 (2026-08-26): `threads` is the canonical collection — every
    fleet workspace's project records already live there (43-record shape,
    `project_*` ids). `projects` is a rejected spelling: requesting it here
    returns the SAME `threads` list object (an alias, not a second list), so
    a caller that still asks for "projects" can never again create a second,
    empty collection that silently shadows the real one (the exact landmine
    `repair_dual_project_key` below cleans up on existing workspaces). This
    replaces the pre-DUALKEY1 behavior where "projects" created its own list.
    """
    container = unwrap_entities(data)
    key = "threads" if name == "projects" else name
    coll = container.get(key)
    if not isinstance(coll, list):
        coll = []
        container[key] = coll
    return coll


def repair_dual_project_key(data: dict) -> dict:
    """One-shot repair for the DUALKEY1 landmine: an entities.json written
    before the `entities_collection` alias above can carry a real, separate
    `projects` key (created by the old creates-on-read behavior, sometimes
    non-empty) alongside the canonical `threads` key. Left alone, every
    `or`-fallback reader in the product resolves `projects` first — the
    instant ANY record lands there, every one of those readers drops the
    real `threads` collection and sees only that one record (A1 silent-data-
    loss family).

    Merges `projects` into `threads`, deduped by id:
      - a `projects` record whose id is NOT already in `threads` is
        appended to `threads` (n_merged);
      - a `projects` record whose id IS already in `threads` is dropped from
        `threads` and the `threads` copy is kept — the duplicate is
        quarantined under `container["_recovery"]["dualkey1_projects_
        quarantine"]` instead of being discarded (n_quarantined);
      - a malformed (non-dict) entry is quarantined the same way rather
        than silently discarded (counted in n_quarantined);
      - the `projects` key itself is always deleted once inspected
        (n_deleted_keys), whether or not it held anything, so a stray empty
        `projects: []` key doesn't linger.

    Mutates `data` in place (same in-place contract as `entities_collection`
    — the caller writes `data` back). Returns a counts dict with all three
    keys ALWAYS present (zero-written, never omitted) so the cleanup receipt
    that stamps these numbers never has to guess at an absent field:

        {"n_merged": int, "n_quarantined": int, "n_deleted_keys": int}

    A workspace with no `projects` key at all (the common case once this
    ships) is a true no-op: all three counts come back 0.
    """
    counts = {"n_merged": 0, "n_quarantined": 0, "n_deleted_keys": 0}
    container = unwrap_entities(data)
    if "projects" not in container:
        return counts

    projects = container.get("projects")
    if isinstance(projects, list) and projects:
        threads = container.get("threads")
        if not isinstance(threads, list):
            threads = []
            container["threads"] = threads
        existing_ids = {t.get("id") for t in threads if isinstance(t, dict)}
        quarantined = []
        for rec in projects:
            if not isinstance(rec, dict):
                # A malformed (non-dict) entry is not a mergeable record, but
                # deleting the key below would silently discard it — quarantine
                # it instead (REVIEW DUALKEY1 F2: no record silently dropped).
                quarantined.append(rec)
                counts["n_quarantined"] += 1
                continue
            rid = rec.get("id")
            if rid is not None and rid in existing_ids:
                quarantined.append(rec)
                counts["n_quarantined"] += 1
            else:
                threads.append(rec)
                if rid is not None:
                    existing_ids.add(rid)
                counts["n_merged"] += 1
        if quarantined:
            recovery = container.get("_recovery")
            if not isinstance(recovery, dict):
                recovery = {}
                container["_recovery"] = recovery
            bucket = recovery.get("dualkey1_projects_quarantine")
            if not isinstance(bucket, list):
                bucket = []
                recovery["dualkey1_projects_quarantine"] = bucket
            bucket.extend(quarantined)

    del container["projects"]
    counts["n_deleted_keys"] += 1
    return counts
