#!/usr/bin/env python3
"""THE org-activity derivation — recency from events, never the stored field
(SPEC HIST1 D6; mirrors thread_activity.derive_thread_activity).

WHY THIS EXISTS
---------------

`org.last_interaction` is an unmaintained FOSSIL — the same class as
`thread.last_activity` (F-54/F-61): no code path bumps it, so it froze at
whatever the last ingest wrote. Before this module there was NO org
equivalent of the thread derivation, so any org-recency claim either read
the fossil or hand-rolled its own scan.

THE DEPRECATION RULE (settled here, once)
-----------------------------------------

`org.last_interaction` is DEPRECATED as a recency source as of SPEC HIST1.
Staleness/recency claims about an org MUST derive from events at read time
via this module. The stored field stays write-tolerated and may be consulted
ONLY when an org has zero events (fresh-ingest workspaces legitimately carry
a record stamp before any event exists) — it may NEVER override or blend
with derived activity. Same rule as thread_activity, verbatim.

An event counts toward an org when it carries the org DIRECTLY (top-level
`org_ids[]`, or `data.org_id` / `data.org_ids[]` — the shapes the
org-history event families write) or INDIRECTLY via a thread it touches
whose `affiliation_id` (legacy `org_id`) is the org. Confidence floor and
timestamp handling mirror thread_activity (`classification_confidence >=
0.40` when present; event_time's ts → timestamp → date resolution).

Consumers (HIST1 Part 1): render_org_history (derived last-touch), the
workspace-manager `go [org] rollup` surface via that renderer.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from thread_activity import (  # noqa: E402
    CONFIDENCE_FLOOR,
    ALL_TYPES,
    _event_dt,
    _iter_events,
    event_thread_ids,
)


class OrgActivity(NamedTuple):
    seq: Optional[int]
    event_type: str
    ts: datetime


def event_org_ids(ev: dict, thread_org: Optional[dict] = None) -> list[str]:
    """Every org id an event touches — direct fields first, then via the
    event's threads when a {thread_id: org_id} map is supplied.

    Direct: top-level `org_ids[]` (events.schema.json), `data.org_id`,
    `data.org_ids[]` (the HIST1 fact/lineage payloads). Indirect: any thread
    the event touches (thread_activity.event_thread_ids — canonical +
    legacy spellings) whose affiliation resolves to an org.
    """
    ids: list[str] = []
    seen: set[str] = set()

    def _add(value) -> None:
        if isinstance(value, str) and value and value != "personal" and value not in seen:
            ids.append(value)
            seen.add(value)

    top = ev.get("org_ids")
    if isinstance(top, list):
        for oid in top:
            _add(oid)
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    _add(data.get("org_id"))
    inner = data.get("org_ids")
    if isinstance(inner, list):
        for oid in inner:
            _add(oid)

    if thread_org:
        for tid in event_thread_ids(ev):
            _add(thread_org.get(tid))
    return ids


def thread_org_map(entities: dict) -> dict[str, str]:
    """{thread_id: org_id} from the entities registry — `affiliation_id`
    first, legacy `org_id` accepted (reader back-compat over rewrite).
    Wrapper-aware callers pass the collections dict (flat or the inner
    `entities` object); both `threads` and `projects` keys are read
    (real-data fixture shape: live workspaces carry either)."""
    out: dict[str, str] = {}
    if not isinstance(entities, dict):
        return out
    view = entities.get("entities") if isinstance(entities.get("entities"), dict) else entities
    for coll in ("threads", "projects"):
        for t in view.get(coll) or []:
            if not isinstance(t, dict) or not t.get("id"):
                continue
            oid = t.get("affiliation_id") or t.get("org_id")
            if isinstance(oid, str) and oid and oid != "personal":
                out[t["id"]] = oid
    return out


def derive_from_events(
    events: Iterable[dict],
    thread_org: Optional[dict] = None,
    activity_types: Optional[Iterable[str]] = None,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> dict[str, OrgActivity]:
    """The fold over an in-memory event iterable — same rules as
    derive_org_activity, for callers that already hold the events
    (render_org_history loads them once for the timeline too).

    activity_types: None → every event type counts (renderer "last touched"
    semantics — an org fact or a people move legitimately bumps an org's
    recency). Pass a real type set from any surface that quotes a
    day-count, and pass the SAME set from every such surface (the F-54
    contract). The thread_activity.ALL_TYPES sentinel is accepted as an
    explicit spelling of None.
    """
    if activity_types is ALL_TYPES or activity_types is None:
        types = None
    else:
        types = frozenset(activity_types)
    last: dict[str, OrgActivity] = {}

    for ev in events:
        if not isinstance(ev, dict):
            continue
        if types is not None and ev.get("type") not in types:
            continue
        conf = ev.get("classification_confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool) and conf < confidence_floor:
            continue
        org_ids = event_org_ids(ev, thread_org)
        if not org_ids:
            continue
        ts = _event_dt(ev)
        if ts is None:
            continue
        seq = ev.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            seq = None
        record = OrgActivity(seq=seq, event_type=ev.get("type") or "", ts=ts)
        for oid in org_ids:
            prior = last.get(oid)
            if prior is None or record.ts > prior.ts:
                last[oid] = record

    return last


def derive_org_activity(
    workspace_root: str | Path,
    entities: Optional[dict] = None,
    activity_types: Optional[Iterable[str]] = None,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> dict[str, OrgActivity]:
    """{org_id: most-recent OrgActivity} derived from events at read time.
    THE org staleness baseline — never the stored last_interaction field
    (fossil; zero-event floor only, decided by the CALLER exactly as
    thread_activity consumers do for thread.last_activity).

    Args:
        workspace_root: folder containing `_hq/data/events.jsonl`.
        entities: optional pre-loaded entities registry (flat or wrapped);
            loaded from disk when omitted. Supplies the thread→org map so
            events reaching an org only via an affiliated thread count.
        activity_types: which event types count. None → all (renderer
            semantics). Day-count surfaces must share one real set (F-54).
    """
    ws = Path(workspace_root)
    if entities is None:
        import json
        p = ws / "_hq" / "data" / "entities.json"
        try:
            entities = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entities = {}
    return derive_from_events(
        _iter_events(ws),
        thread_org=thread_org_map(entities),
        activity_types=activity_types,
        confidence_floor=confidence_floor,
    )


__all__ = [
    "derive_org_activity",
    "derive_from_events",
    "event_org_ids",
    "thread_org_map",
    "OrgActivity",
    "CONFIDENCE_FLOOR",
]
