#!/usr/bin/env python3
"""
Regression tests for shared/scripts/event_time.py (Phase 1 Foundation / R7).

Fixtures mirror the three timestamp spellings found in the live substrate at
the 2026-07-01 audit (`ts` ×3533, `timestamp` ×156, `date` ×17) — the drift
that made a subagent read commitment capture as "decaying −70%" when it was
healthy. Priority contract: ts → timestamp → date, read-side only, history
never rewritten.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from event_time import event_dt, event_time, parse_ts  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail="") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label} {detail}")


def test_field_priority():
    print("test_field_priority")
    # Real live-substrate shapes: canonical, legacy `timestamp`, legacy `date`.
    canonical = {"seq": 3101, "ts": "2026-06-24T09:15:00+00:00",
                 "type": "commitment", "source_skill": "meeting-notes",
                 "data": {"title": "send deck"}}
    legacy_timestamp = {"seq": 412, "timestamp": "2026-04-18T22:10:03Z",
                        "type": "interaction", "source_skill": "inbox-triage",
                        "data": {}}
    legacy_date = {"seq": 88, "date": "2026-02-03", "type": "meeting",
                   "source_skill": "workspace-ingest", "data": {}}

    check("ts read", event_time(canonical) == "2026-06-24T09:15:00+00:00")
    check("timestamp fallback", event_time(legacy_timestamp) == "2026-04-18T22:10:03Z")
    check("date fallback", event_time(legacy_date) == "2026-02-03")

    # Priority: canonical field wins when spellings coexist.
    mixed = {"ts": "2026-06-01T00:00:00Z", "timestamp": "2026-01-01T00:00:00Z",
             "date": "2025-12-01"}
    check("ts beats timestamp beats date", event_time(mixed) == "2026-06-01T00:00:00Z")
    check("blank ts falls through",
          event_time({"ts": "  ", "timestamp": "2026-01-01T00:00:00Z"})
          == "2026-01-01T00:00:00Z")
    check("non-string ts falls through",
          event_time({"ts": 1751376000, "timestamp": "2026-01-01T00:00:00Z"})
          == "2026-01-01T00:00:00Z")
    check("no field -> empty string", event_time({"type": "note"}) == "")
    check("non-dict -> empty string", event_time(None) == "")


def test_parse_ts():
    print("test_parse_ts")
    utc = dt.timezone.utc
    check("Z suffix", parse_ts("2026-06-24T09:15:00Z")
          == dt.datetime(2026, 6, 24, 9, 15, tzinfo=utc))
    check("explicit offset", parse_ts("2026-06-24T02:15:00-07:00")
          == dt.datetime(2026, 6, 24, 9, 15, tzinfo=utc))
    check("naive assumes UTC", parse_ts("2026-06-24T09:15:00")
          == dt.datetime(2026, 6, 24, 9, 15, tzinfo=utc))
    check("bare date", parse_ts("2026-02-03")
          == dt.datetime(2026, 2, 3, tzinfo=utc))
    check("fractional seconds", parse_ts("2026-06-24T09:15:00.123456+00:00")
          is not None)
    check("garbage -> None", parse_ts("not a date") is None)
    check("empty -> None", parse_ts("") is None)
    check("None -> None", parse_ts(None) is None)

    # Mixed Z / +00:00 offsets compare correctly once parsed — the exact trap
    # the raw-string comparison in cru_match's calendar check guards against.
    a = parse_ts("2026-06-24T09:15:00Z")
    b = parse_ts("2026-06-24T10:15:00+00:00")
    check("cross-offset comparison", a is not None and b is not None and a < b)


def test_event_dt():
    print("test_event_dt")
    check("event_dt composes", event_dt({"timestamp": "2026-04-18T22:10:03Z"})
          == dt.datetime(2026, 4, 18, 22, 10, 3, tzinfo=dt.timezone.utc))
    check("event_dt None on dateless", event_dt({"type": "note"}) is None)


def test_readers_consume_all_spellings():
    print("test_readers_consume_all_spellings")
    # The migration's acceptance shape: a mixed-spelling history sorts and
    # filters correctly through the helper (no spelling is dropped).
    events = [
        {"seq": 1, "date": "2026-02-03", "type": "meeting"},
        {"seq": 2, "timestamp": "2026-04-18T22:10:03Z", "type": "interaction"},
        {"seq": 3, "ts": "2026-06-24T09:15:00Z", "type": "commitment"},
    ]
    dts = [event_dt(e) for e in events]
    check("no event dropped", all(d is not None for d in dts))
    check("chronological order holds", dts == sorted(dts))
    since = dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc)
    kept = [e["seq"] for e in events if event_dt(e) and event_dt(e) >= since]
    check("window filter sees legacy spellings", kept == [2, 3], repr(kept))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== event_time read-side normalization ===")
    test_field_priority()
    test_parse_ts()
    test_event_dt()
    test_readers_consume_all_spellings()
    print()
    if FAIL:
        print(f"FAIL — {FAIL} of {PASS + FAIL} checks failed")
        return 1
    print(f"OK — all {PASS} event_time checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
