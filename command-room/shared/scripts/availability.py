#!/usr/bin/env python3
"""
Deterministic free-slot math (SPEC BAL1 D5) — the load-bearing primitive no
other module had.

The 2026-07-18 person-side audit confirmed the plugin has NO free-slot /
open-block function anywhere: `calendar-writer` does availability in LLM
prose, and the whole point of the Balance surface is a TRUSTWORTHY "this
evening is genuinely free" claim. This module is that claim, in code:

  open_evenings(busy_intervals, now=..., ...)  -> the evenings in the horizon
      with a free block >= min_block_hours between evening_start and
      evening_end, after subtracting EVERY busy interval passed in (personal +
      family + business together — the caller merges the sources; a work-
      travel block removes the evening exactly like a family dinner does).

  has_conflict(slot, busy_intervals)           -> deterministic overlap check
      used to VALIDATE a proposed slot before anything is drafted or held
      (replaces prose-only double-booking judgment).

Pure stdlib, no substrate I/O, no LLM arithmetic (the Bug #99 class). All
timestamps are WORKSPACE-LOCAL WALL CLOCK — the caller localizes connector
times via tz.to_local() BEFORE passing them in (the house rule). tz.to_local
returns an AWARE local datetime, so an aware input is accepted by dropping
tzinfo and KEEPING the wall clock (never shifted to UTC — a UTC shift would
move a 6–9 PM local dinner out of the evening window and falsely report the
evening open; second-eyes finding, 2026-07-19). A raw un-localized UTC input
therefore keeps its UTC wall clock — mixing clocks is the caller's bug, not
this module's job to repair.
"""
from __future__ import annotations

import datetime as _dt
from typing import Iterable, List, Optional, Sequence, Tuple, Union

IntervalLike = Union[dict, Sequence]

DEFAULT_EVENING_START = "18:00"
DEFAULT_EVENING_END = "22:00"
DEFAULT_MIN_BLOCK_HOURS = 2.0


class AvailabilityError(ValueError):
    """A malformed input this module cannot interpret deterministically."""


def _parse_dt(value, field: str = "datetime") -> _dt.datetime:
    """Parse a datetime from a datetime/date or ISO string. tz-aware inputs
    keep their WALL CLOCK (tzinfo dropped, never shifted to UTC) — the
    documented contract above: callers pass workspace-local times, and
    tz.to_local returns aware local datetimes."""
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, _dt.date):
        dt = _dt.datetime(value.year, value.month, value.day)
    else:
        s = str(value or "").strip().replace("Z", "+00:00")
        try:
            dt = _dt.datetime.fromisoformat(s)
        except ValueError:
            raise AvailabilityError(f"{field} must be a datetime/ISO string, got {value!r}")
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _parse_hhmm(value, field: str) -> _dt.time:
    if isinstance(value, _dt.time):
        return value
    s = str(value or "").strip()
    try:
        h, m = s.split(":", 1)
        return _dt.time(int(h), int(m))
    except (ValueError, AttributeError):
        raise AvailabilityError(f"{field} must be 'HH:MM', got {value!r}")


def _interval(iv: IntervalLike) -> Optional[Tuple[_dt.datetime, _dt.datetime]]:
    """One busy interval -> (start, end) or None when malformed/empty.
    Accepts {'start': ..., 'end': ...} or a (start, end) pair. Malformed
    entries are DROPPED, not raised — connector output is defensive-read
    territory; the caller sees fewer busy blocks, never a crash."""
    try:
        if isinstance(iv, dict):
            start, end = iv.get("start"), iv.get("end")
        else:
            start, end = iv[0], iv[1]
        s, e = _parse_dt(start, "interval start"), _parse_dt(end, "interval end")
    except (AvailabilityError, IndexError, TypeError, KeyError):
        return None
    if e <= s:
        return None
    return (s, e)


def normalize_busy(busy_intervals: Iterable[IntervalLike]) -> List[Tuple[_dt.datetime, _dt.datetime]]:
    """Parse, sort, and merge overlapping/adjacent busy intervals."""
    parsed = sorted(
        iv for iv in (_interval(x) for x in (busy_intervals or [])) if iv
    )
    merged: List[Tuple[_dt.datetime, _dt.datetime]] = []
    for s, e in parsed:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def open_evenings(
    busy_intervals: Iterable[IntervalLike],
    *,
    now,
    horizon_days: int = 14,
    evening_start: str = DEFAULT_EVENING_START,
    evening_end: str = DEFAULT_EVENING_END,
    min_block_hours: float = DEFAULT_MIN_BLOCK_HOURS,
) -> List[dict]:
    """The open evenings in the next `horizon_days` days (today included when
    its evening hasn't passed), after subtracting every busy interval.

    Returns one row per open evening — the LONGEST free block that evening
    (earliest wins a tie), only when it is >= min_block_hours:

        {"date": "YYYY-MM-DD", "start": ISO, "end": ISO, "hours": float}

    `now` is REQUIRED (an explicit anchor — fixtures stay date-relative, the
    G14 date-guard rule). An evening already underway is clipped to `now`.
    """
    anchor = _parse_dt(now, "now")
    start_t = _parse_hhmm(evening_start, "evening_start")
    end_t = _parse_hhmm(evening_end, "evening_end")
    if end_t <= start_t:
        raise AvailabilityError(
            f"evening_end {evening_end!r} must be after evening_start {evening_start!r}"
        )
    try:
        horizon = int(horizon_days)
    except (TypeError, ValueError):
        raise AvailabilityError(f"horizon_days must be an int, got {horizon_days!r}")
    if horizon < 1:
        return []
    min_block = _dt.timedelta(hours=float(min_block_hours))
    busy = normalize_busy(busy_intervals)

    out: List[dict] = []
    for offset in range(horizon):
        day = anchor.date() + _dt.timedelta(days=offset)
        win_start = _dt.datetime.combine(day, start_t)
        win_end = _dt.datetime.combine(day, end_t)
        if offset == 0 and anchor > win_start:
            win_start = anchor  # tonight, already partly gone
        if win_start >= win_end:
            continue
        # Subtract merged busy intervals from [win_start, win_end).
        free: List[Tuple[_dt.datetime, _dt.datetime]] = []
        cursor = win_start
        for bs, be in busy:
            if be <= win_start or bs >= win_end:
                continue
            if bs > cursor:
                free.append((cursor, min(bs, win_end)))
            cursor = max(cursor, be)
            if cursor >= win_end:
                break
        if cursor < win_end:
            free.append((cursor, win_end))
        qualifying = [(s, e) for s, e in free if (e - s) >= min_block]
        if not qualifying:
            continue
        # max() keeps the FIRST maximal element on ties; qualifying is
        # chronological, so the earliest of equal-length blocks wins.
        best = max(qualifying, key=lambda p: p[1] - p[0])
        out.append({
            "date": day.isoformat(),
            "start": best[0].isoformat(),
            "end": best[1].isoformat(),
            "hours": round((best[1] - best[0]).total_seconds() / 3600.0, 2),
        })
    return out


def has_conflict(slot: IntervalLike, busy_intervals: Iterable[IntervalLike]) -> bool:
    """True when `slot` OVERLAPS any busy interval (strict overlap — an
    interval that merely touches an endpoint is not a conflict). A malformed
    slot conflicts by definition: never validate a slot we can't parse."""
    parsed = _interval(slot)
    if parsed is None:
        return True
    s, e = parsed
    for bs, be in normalize_busy(busy_intervals):
        if s < be and bs < e:
            return True
    return False


__all__ = [
    "AvailabilityError",
    "DEFAULT_EVENING_START",
    "DEFAULT_EVENING_END",
    "DEFAULT_MIN_BLOCK_HOURS",
    "normalize_busy",
    "open_evenings",
    "has_conflict",
]


if __name__ == "__main__":  # smoke
    import json
    now = _dt.datetime(2026, 1, 5, 9, 0)
    busy = [{"start": "2026-01-05T18:00", "end": "2026-01-05T21:00"}]
    print(json.dumps(open_evenings(busy, now=now, horizon_days=3), indent=2))
    print(has_conflict(("2026-01-05T19:00", "2026-01-05T20:00"), busy))
