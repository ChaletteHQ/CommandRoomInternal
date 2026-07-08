#!/usr/bin/env python3
"""
Read-side event-timestamp normalization (Phase 1 Foundation / R7 — 2026-07).

The live substrate carries THREE timestamp field spellings on events
(`ts` ×3533, `timestamp` ×156, `date` ×17 at the 2026-07-01 audit). A reader
that parses only one of them silently drops or mis-orders the others — this
exact hole made a subagent report commitment capture "decaying −70%" when
capture was healthy. Writers emit `ts` only (the append gate + auto-stamp
guarantee it); HISTORY IS NEVER REWRITTEN (additive-only forever), so every
reader normalizes at read time through this helper.

  event_time(ev) -> str        best-available raw string: ts → timestamp → date
  event_dt(ev)   -> datetime?  parsed + tz-aware (naive values assume UTC)
  parse_ts(s)    -> datetime?  tolerant ISO parser (Z suffix, offsets,
                               bare dates); None on garbage

Raw-string comparisons/sorts on event_time() output remain valid for ISO
strings, matching the pre-existing `ev.get("ts") or ""` sort idiom. Use
event_dt() when comparing across mixed Z/+00:00 offsets.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

_FIELD_PRIORITY = ("ts", "timestamp", "date")


def event_time(ev) -> str:
    """The event's best-available timestamp string, `""` when none.

    Reads top-level `ts` → `timestamp` → `date` in priority order (the
    canonical field wins even when a legacy spelling coexists). Non-string
    and blank values are skipped.
    """
    if not isinstance(ev, dict):
        return ""
    for field in _FIELD_PRIORITY:
        v = ev.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def parse_ts(value) -> Optional[_dt.datetime]:
    """Tolerant ISO-8601 parse. Accepts `Z` suffix, explicit offsets,
    fractional seconds, and bare dates (`YYYY-MM-DD` → midnight). Naive
    results are assigned UTC so comparisons never raise. Returns None on
    anything unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    dt = None
    try:
        dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = _dt.datetime.fromisoformat(raw[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def event_dt(ev) -> Optional[_dt.datetime]:
    """Parsed, tz-aware event timestamp — `parse_ts(event_time(ev))`."""
    return parse_ts(event_time(ev))


__all__ = ["event_time", "event_dt", "parse_ts"]
