#!/usr/bin/env python3
"""
SPEC BAL1 — availability.py acceptance suite (the deterministic free-slot
primitive; §6 test plan).

Pure math, exact expectations (no tolerance — acceptance #3). Every date is
computed RELATIVE to a fixed anchor `now` passed in explicitly (the G14
date-guard rule: nothing here ever goes stale).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared" / "scripts"))

import availability as av  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


# Anchor: a MORNING datetime so day-0's evening is fully ahead of `now`.
# Absolute-date-free: everything below derives from this one value.
NOW = dt.datetime(2026, 1, 5, 9, 0)  # DATE_GUARD_OK — pure-math anchor, never compared to the wall clock
D0 = NOW.date()


def day(offset: int) -> dt.date:
    return D0 + dt.timedelta(days=offset)


def at(offset: int, hhmm: str) -> str:
    h, m = hhmm.split(":")
    return dt.datetime.combine(day(offset), dt.time(int(h), int(m))).isoformat()


# ---------------------------------------------------------------------------
print("\n[1] open_evenings — subtraction, both calendars, thresholds")
# ---------------------------------------------------------------------------

# Day 0: personal dinner 18:00-20:00 -> remaining 20:00-22:00 = 2h (counts).
# Day 1: business travel block covers the whole evening -> evening removed.
# Day 2: fully free -> 18:00-22:00 = 4h.
# Day 3: meeting 19:30-20:30 splits the evening into 1.5h + 1.5h -> neither
#        reaches min_block_hours=2 -> evening removed.
busy = [
    {"start": at(0, "18:00"), "end": at(0, "20:00")},   # personal dinner
    {"start": at(1, "08:00"), "end": at(1, "23:00")},   # business travel
    {"start": at(3, "19:30"), "end": at(3, "20:30")},   # split meeting
]
slots = av.open_evenings(busy, now=NOW, horizon_days=4)
by_date = {s["date"]: s for s in slots}

check("day 0 partial evening survives (2h tail block)",
      day(0).isoformat() in by_date
      and by_date[day(0).isoformat()]["start"] == at(0, "20:00")
      and by_date[day(0).isoformat()]["hours"] == 2.0,
      repr(slots))
check("business-travel evening removed (day 1)", day(1).isoformat() not in by_date)
check("free evening reported in full (day 2, 4h)",
      by_date.get(day(2).isoformat(), {}).get("hours") == 4.0)
check("sub-min_block fragments do not count (day 3 split)",
      day(3).isoformat() not in by_date)
check("exactly the two qualifying evenings returned", len(slots) == 2,
      repr(slots))

# Horizon respected: the same busy set with horizon 1 sees only day 0.
one = av.open_evenings(busy, now=NOW, horizon_days=1)
check("horizon respected (horizon_days=1 -> day 0 only)",
      [s["date"] for s in one] == [day(0).isoformat()], repr(one))

# evening_start boundary honored: busy ending exactly at evening_start leaves
# the whole window free; busy starting exactly at evening_end never intrudes.
edge_busy = [
    {"start": at(0, "16:00"), "end": at(0, "18:00")},
    {"start": at(0, "22:00"), "end": at(0, "23:30")},
]
edge = av.open_evenings(edge_busy, now=NOW, horizon_days=1)
check("evening_start/evening_end boundaries honored (touching != conflict)",
      len(edge) == 1 and edge[0]["start"] == at(0, "18:00")
      and edge[0]["end"] == at(0, "22:00"), repr(edge))

# Tonight already underway: an anchor mid-evening clips the window to now.
midnow = dt.datetime.combine(day(0), dt.time(19, 0))
clipped = av.open_evenings([], now=midnow, horizon_days=1)
check("evening already underway clips to now (19:00-22:00 = 3h)",
      len(clipped) == 1 and clipped[0]["start"] == midnow.isoformat()
      and clipped[0]["hours"] == 3.0, repr(clipped))

# An anchor after evening_end yields no day-0 slot at all.
latenow = dt.datetime.combine(day(0), dt.time(22, 30))
gone = av.open_evenings([], now=latenow, horizon_days=1)
check("evening already over -> no slot tonight", gone == [], repr(gone))

# Longest-block-per-evening: two qualifying blocks -> the longer one wins.
two_block = [{"start": at(0, "20:00"), "end": at(0, "20:30")}]
# 18:00-20:00 (2h) and 20:30-22:00 (1.5h) -> 2h block returned.
best = av.open_evenings(two_block, now=NOW, horizon_days=1,
                        min_block_hours=1.0)
check("longest free block per evening wins",
      best[0]["start"] == at(0, "18:00") and best[0]["hours"] == 2.0,
      repr(best))

# Malformed interval dropped, not raised (defensive-read contract).
junk = av.open_evenings([{"start": "not-a-date", "end": None}, "junk"],
                        now=NOW, horizon_days=1)
check("malformed busy intervals dropped defensively", len(junk) == 1)

# ---------------------------------------------------------------------------
print("\n[2] has_conflict — strict overlap")
# ---------------------------------------------------------------------------

b = [{"start": at(0, "19:00"), "end": at(0, "20:00")}]
check("overlapping slot conflicts",
      av.has_conflict((at(0, "19:30"), at(0, "21:00")), b) is True)
check("containing slot conflicts",
      av.has_conflict((at(0, "18:00"), at(0, "22:00")), b) is True)
check("adjacent-but-not-overlapping does NOT conflict (end == start)",
      av.has_conflict((at(0, "20:00"), at(0, "21:00")), b) is False)
check("disjoint slot does not conflict",
      av.has_conflict((at(1, "19:00"), at(1, "20:00")), b) is False)
check("unparseable slot conflicts by definition (never validate junk)",
      av.has_conflict({"start": "??", "end": "??"}, b) is True)

# ---------------------------------------------------------------------------
print("\n[3] normalize_busy — merge + ordering")
# ---------------------------------------------------------------------------

merged = av.normalize_busy([
    (at(0, "20:00"), at(0, "21:00")),
    (at(0, "18:00"), at(0, "19:00")),
    (at(0, "18:30"), at(0, "20:00")),   # bridges the first two
])
check("overlapping/adjacent intervals merge into one",
      len(merged) == 1
      and merged[0][0].isoformat() == at(0, "18:00")
      and merged[0][1].isoformat() == at(0, "21:00"), repr(merged))
check("inverted interval (end <= start) dropped",
      av.normalize_busy([(at(0, "20:00"), at(0, "19:00"))]) == [])

# ---------------------------------------------------------------------------
print("\n[4] tz-aware inputs keep their WALL CLOCK (BAL1 second-eyes fix)")
# ---------------------------------------------------------------------------
# tz.to_local returns AWARE local datetimes; the old aware->UTC-naive shift
# moved a 6-9 PM local dinner out of the 18:00-22:00 window entirely, so a
# fully-busy evening reported as OPEN. Aware inputs must keep the wall clock.

_off = dt.timezone(dt.timedelta(hours=-7))  # any fixed offset; value must not matter
aware_dinner = [(dt.datetime.combine(day(0), dt.time(18, 0), tzinfo=_off),
                 dt.datetime.combine(day(0), dt.time(21, 0), tzinfo=_off))]
blocked = av.open_evenings(aware_dinner, now=NOW, horizon_days=1)
check("aware local 18:00-21:00 dinner blocks the evening (no UTC shift)",
      blocked == [], repr(blocked))

check("has_conflict sees an aware slot colliding with the same naive time",
      av.has_conflict(
          (dt.datetime.combine(day(0), dt.time(19, 0), tzinfo=_off),
           dt.datetime.combine(day(0), dt.time(20, 0), tzinfo=_off)),
          [{"start": at(0, "19:00"), "end": at(0, "20:00")}]) is True)

check("aware ISO string keeps wall clock too",
      av.open_evenings([{"start": at(0, "18:00") + "-07:00",
                         "end": at(0, "21:30") + "-07:00"}],
                       now=NOW, horizon_days=1) == [])

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}\nPASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
