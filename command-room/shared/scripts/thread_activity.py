#!/usr/bin/env python3
"""THE thread-activity derivation — one rule, every surface (v4.5.2 C3).

WHY THIS EXISTS
---------------

FINDINGS F-54 (2026-07-08 dogfood): `stalled projects` reported "Acme Co —
43 days quiet" on a day TWO Acme Co meetings were processed and 10
commitments written. Root cause chain:

  1. `entities.json thread.last_activity` is a FOSSIL. The cleanup autopsy
     (F-61, before/after snapshot of a full successful run) proved the field
     is byte-identical after cleanup's projection refresh — NO code path
     maintains it. It froze at whatever the last ingest wrote (~May 27 on
     M's workspace).
  2. stall_detector's event scan read the thread id from `data.project_id` /
     `data.primary_thread_id` — but canonical v2.2 events carry
     `primary_thread_id` at the event's TOP LEVEL (DATA_CONTRACT.md). On
     real substrates the scan matched ZERO events, so the fossil field won
     every comparison. The unit tests stayed green because their fixture
     mirrored the code's wrong assumption instead of the real event shape
     (the realdata-fixture bug class).
  3. `related_thread_ids[]` was never scanned, so cross-referenced activity
     didn't count at all.
  4. Pulse Phase 4a derived its own `last_event_date` with different rules,
     so the two surfaces quoted different day-counts for the same project on
     the same day (F-54: 21d vs 37d).

This module is the consolidation: ONE scan that resolves every thread an
event touches (top-level `primary_thread_id`, top-level `related_thread_ids`,
the deprecated top-level `project_id` mirror, plus the legacy data-level
spellings — parsed forever, reader back-compat over rewrite), honors the
documented `computed_last_activity` confidence rule (VIEW_GENERATION.md:
`classification_confidence >= 0.40` when present), and reads through
events_io so rotated shards stay visible.

Consumers (v4.5.2): stall_detector (stalled-projects), pulse Phase 4
(orchestrator-dont-forget.md). Future candidates: build_workspace_map_input,
build_dcc_input (still displaying the deprecated field — see
ORG_AND_THREAD_MODEL.md deprecation note).

THE DEPRECATION RULE (settled here, once)
-----------------------------------------

`thread.last_activity` is DEPRECATED as of v4.5.2. No writer maintains it.
Staleness/recency claims MUST derive from events at read time via this
module. The stored field may be consulted ONLY when a thread has zero
events (fresh-ingest workspaces legitimately carry a record stamp before
any event exists) — it may NEVER override or blend with derived activity.
A thread with same-day substrate activity must be structurally incapable
of reading as stalled.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The documented computed_last_activity floor (VIEW_GENERATION.md; same
# constant render_master_tracker enforces). Events with a numeric
# classification_confidence below this don't count as thread activity.
# Events WITHOUT the field count — infrastructure writers omit it.
CONFIDENCE_FLOOR = 0.40

# Default "what counts as activity" set — mirrors stall_detector's
# DEFAULT_CONFIG so the on-demand list and pulse Phase 4 agree out of the
# box. Callers with a saved stalled-projects config pass its
# activity_event_types instead (BOTH surfaces must pass the same set).
DEFAULT_ACTIVITY_TYPES = frozenset({"meeting", "commitment", "decision", "interaction"})


class ThreadActivity(NamedTuple):
    seq: Optional[int]
    event_type: str
    ts: datetime


def _iter_events(workspace_root: Path) -> Iterable[dict]:
    """events_io (shard-transparent) with a defensive active-file fallback —
    same pattern as receipts._iter_events."""
    try:
        import events_io

        yield from events_io.iter_events(workspace_root)
        return
    except Exception:
        pass
    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
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
    except OSError:
        return


def event_thread_ids(ev: dict) -> list[str]:
    """Every thread id an event touches, canonical fields first.

    Canonical (DATA_CONTRACT v2.2): top-level `primary_thread_id` +
    top-level `related_thread_ids[]`; top-level `project_id` is the
    deprecated one-cycle mirror. Legacy data-level spellings
    (`data.project_id`, `data.primary_thread_id`) are parsed forever —
    append-only history stays readable.
    """
    ids: list[str] = []
    seen: set[str] = set()

    def _add(value) -> None:
        if isinstance(value, str) and value and value not in seen:
            ids.append(value)
            seen.add(value)

    _add(ev.get("primary_thread_id"))
    related = ev.get("related_thread_ids")
    if isinstance(related, list):
        for rid in related:
            _add(rid)
    _add(ev.get("project_id"))
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    _add(data.get("project_id"))
    _add(data.get("primary_thread_id"))
    return ids


def derive_thread_activity(
    workspace_root: str | Path,
    activity_types: Optional[Iterable[str]] = None,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> dict[str, ThreadActivity]:
    """{thread_id: most-recent ThreadActivity} derived from events at read
    time. THE staleness baseline — never the stored last_activity field.

    Args:
        workspace_root: folder containing `_hq/data/events.jsonl`.
        activity_types: which event types count as activity. None →
            DEFAULT_ACTIVITY_TYPES. Pass the user's saved stalled-projects
            `activity_event_types` — and pass the SAME set from every
            surface that quotes a day-count, or the numbers diverge (F-54's
            21d-vs-37d split).
        confidence_floor: events with numeric classification_confidence
            below this are skipped (absent field = counted).

    Recency is decided by `ts` (the honest signal); `seq` is carried for
    traceability. An event with an unparseable/missing ts is skipped —
    it can't be placed on a timeline.
    """
    types = frozenset(activity_types) if activity_types is not None else DEFAULT_ACTIVITY_TYPES
    last: dict[str, ThreadActivity] = {}

    for ev in _iter_events(Path(workspace_root)):
        if ev.get("type") not in types:
            continue
        conf = ev.get("classification_confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool) and conf < confidence_floor:
            continue
        thread_ids = event_thread_ids(ev)
        if not thread_ids:
            continue
        try:
            ts = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        seq = ev.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            seq = None
        # Store UTC-aware so downstream day-count arithmetic against an
        # aware `now` never mixes naive/aware (the F-15 legacy writer mix).
        record = ThreadActivity(seq=seq, event_type=ev["type"], ts=_ts_key(ts))
        for tid in thread_ids:
            prior = last.get(tid)
            if prior is None or record.ts > prior.ts:
                last[tid] = record

    return last


def _ts_key(ts: datetime) -> datetime:
    """Order-safe comparison key across naive/aware timestamps (the F-15
    legacy writer mix): naive stamps are taken as UTC — the same assumption
    stall_detector has always made for date-only fields."""
    from datetime import timezone as _tz

    if ts.tzinfo is None:
        return ts.replace(tzinfo=_tz.utc)
    return ts.astimezone(_tz.utc)


__all__ = [
    "derive_thread_activity",
    "event_thread_ids",
    "ThreadActivity",
    "DEFAULT_ACTIVITY_TYPES",
    "CONFIDENCE_FLOOR",
]
