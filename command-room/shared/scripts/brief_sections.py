#!/usr/bin/env python3
"""The morning brief's org sections, named in code (cr1#148).

The brief renders one section per primary-focus org (morning-briefing
SKILL.md Step 4). Those sections are composed by the model at render time,
and until this module nothing on the pack or the receipt named them, so a
fire that dropped them left no trace.

Two jobs, one each:

  expected_org_sections(ws)   the org sections a brief on this workspace is
                              expected to render. The pack carries it as
                              `expected_sections`.
  missing_sections(text, exp) the expected sections a rendered brief does not
                              carry. The fire's receipt records the result as
                              `sections_check`, read from the saved snapshot.

Which orgs are expected (the layout rules of Step 4, as the MASTER_TRACKER
renderer already applies them):
  * a top-level org (no `parent_org_id`) with `is_primary_focus: true`;
  * with at least one live thread in its subtree. A holding's section carries
    its operating children's threads, so a holding is expected under its own
    name, never its child's. An org with nothing live has no section to
    render (Step 4: "skip any section that has nothing to report").
A thread is live unless its status is one of `NOT_LIVE_STATUSES`.

What counts as the section in the rendered text: a line that is the org's
canonical name and nothing else, compared without case, allowing a markdown
heading mark or emphasis around it, a trailing colon, and trailing bracketed
badges or a parenthesised count (`Acme Logistics [holding]`). A line that
merely mentions the org (a thread line, a going-quiet line, a sentence) is
not its section.

Reads only. Stdlib only, 3.10-safe (ships in the runtime manifest).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from entities_io import entities_collection, unwrap_entities

#: The one section kind this module names today.
KIND_ORG = "org"

#: Thread statuses that keep a thread out of its org's section: exactly the
#: MASTER_TRACKER renderer's exclusions (`render_master_tracker`
#: `active_threads_for`), so the brief is never told to drop a section the
#: tracker it reads still renders.
NOT_LIVE_STATUSES = ("archived", "paused", "blocked")

_TRAILING_TAG = re.compile(r"\s*(\[[^\]]*\]|\([^)]*\))\s*$")


def _thread_org_id(thread: Mapping[str, Any]) -> Optional[str]:
    """`org_id` is canonical (ENTITY1); `affiliation_id` is the legacy alias.
    Same read order as the MASTER_TRACKER renderer."""
    return (thread.get("org_id") or thread.get("affiliation_id")
            or thread.get("primary_org_id")
            or ((thread.get("affiliation_ids") or [None])[0]))


def _load_entities(workspace_root) -> Dict[str, Any]:
    from entities_io import read_entities_json
    path = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        data = read_entities_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def expected_org_sections(workspace_root, *,
                          thread_activity: Optional[Mapping[str, str]] = None
                          ) -> List[Dict[str, str]]:
    """`[{"kind": "org", "org_id", "heading"}]`, one per expected org section,
    most recently active first (Step 4's order), then by name.

    `thread_activity` is `{thread_id: latest activity ISO}` when the caller
    already holds it (the pack does); without it the thread's stored
    `last_activity` / `first_seen` order the list. Order never decides what
    is expected. An unreadable entities file expects nothing."""
    data = _load_entities(workspace_root)
    if not data:
        return []
    view = unwrap_entities(data)
    orgs = [o for o in (view.get("orgs") or [])
            if isinstance(o, dict) and o.get("id")]
    threads = [t for t in entities_collection(data, "threads")
               if isinstance(t, dict)]
    activity = dict(thread_activity or {})

    children: Dict[str, List[str]] = {}
    for o in orgs:
        parent = o.get("parent_org_id")
        if parent:
            children.setdefault(parent, []).append(o["id"])

    def subtree(oid: str) -> set:
        seen = {oid}
        frontier = [oid]
        while frontier:
            for cid in children.get(frontier.pop(), []):
                if cid not in seen:
                    seen.add(cid)
                    frontier.append(cid)
        return seen

    def recency(thread: Mapping[str, Any]) -> str:
        return str(activity.get(thread.get("id") or "")
                   or thread.get("last_activity") or thread.get("first_seen")
                   or "")

    out = []
    for o in orgs:
        if not o.get("is_primary_focus") or o.get("parent_org_id"):
            continue
        ids = subtree(o["id"])
        live = [t for t in threads
                if _thread_org_id(t) in ids
                and str(t.get("status") or "").lower() not in NOT_LIVE_STATUSES]
        if not live:
            continue
        out.append((max(recency(t) for t in live),
                    {"kind": KIND_ORG, "org_id": o["id"],
                     "heading": str(o.get("canonical_name") or o["id"])}))
    out.sort(key=lambda pair: pair[1]["heading"].casefold())
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in out]


def _normalise_heading(line: str) -> str:
    """The text of a line once heading marks, emphasis, a trailing colon and
    trailing badges are taken off. Repeated until nothing changes, so
    `## **Acme Co** [client]:` comes down to `Acme Co`."""
    s = line.strip()
    while True:
        before = s
        s = s.lstrip("#").strip()
        s = s.strip("*_").strip()
        s = s.rstrip(":").strip()
        s = _TRAILING_TAG.sub("", s).strip()
        if s == before:
            return s


def missing_sections(text: str, expected: Iterable[Mapping[str, Any]]
                     ) -> List[Dict[str, Any]]:
    """The entries of `expected` whose heading is not a line of its own in
    `text`, in the order given.

    Both sides are normalised the same way, so an org whose own name ends in
    a bracket (`Acme Co (US)`) still finds its line. A blank line is never a
    heading."""
    lines = {_normalise_heading(line).casefold()
             for line in str(text or "").splitlines()} - {""}
    return [dict(entry) for entry in (expected or [])
            if isinstance(entry, Mapping)
            and _heading_key(entry.get("heading")) not in lines]


def _heading_key(heading: Any) -> str:
    """What a heading line must normalise to. A name that normalises to
    nothing is compared as written, and an empty one never matches."""
    raw = str(heading or "").strip()
    return (_normalise_heading(raw) or raw).casefold()


def sections_check(workspace_root, text: str) -> Dict[str, List[Dict[str, Any]]]:
    """`{"expected", "missing"}` for one rendered brief: what the receipt
    records. Computed here from the workspace and the text, never from what
    the composer says it rendered."""
    expected = expected_org_sections(workspace_root)
    return {"expected": expected, "missing": missing_sections(text, expected)}
