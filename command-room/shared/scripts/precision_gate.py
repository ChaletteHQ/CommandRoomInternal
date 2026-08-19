#!/usr/bin/env python3
"""The capture-precision gate — data, not prose.

WHAT M RULED (2026-08-16). The 5 PM sort removes the client-side review queue.
That queue is the only net under the roughly one-in-three meeting-derived
captures that were never promises, so the routing flip ships ONLY when:

    >=90% of meeting-derived items routed to the open book are verified
    promises, measured on a >=100-item labelled sample, holding for two
    consecutive dogfood weeks.

`scripts/precision_report.py` produces one JSON record per weekly measurement
into `_hq/audit-reports/`. This module reads the newest of them back and
answers whether the bar is currently met. EOD1 calls it to refuse the flip
while the gate is red.

IT RENDERS NOTHING. Every value it returns is a number, a boolean, an ISO date,
an ISO week, or an UPPER_SNAKE code. A gate that hands back a sentence invites
its caller to print the sentence, and then the wording becomes load-bearing and
the next person to improve it breaks a fence (the caller-side-fence lesson from
DONE1). The caller owns the words; this owns the verdict.

NEVER MOVE THE BAR TO PASS THE BAR. The three constants below are M's ruling,
not tuning knobs. The standing ruling from the sent-close investigation applies
in full: thresholds serve truth, not the number somebody wants.

Read-only. Never raises on a malformed or absent report — an unreadable week is
a week that does not count, which is a FAIL, and a gate that crashes is a gate
that gets removed.

Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

# --- M's ruling, 2026-08-16. Three numbers, one source. ---------------------
# Verified promises as a share of the labelled denominator.
PRECISION_BAR = 0.90
# The labelled denominator itself. A 90% on 13 rows is not this measurement —
# the FLOOR3 replay's own 15.4% rested on a denominator of 13 and had to carry
# a paragraph of caveat because the tool could not carry the number.
MIN_LABELED_SAMPLE = 100
# Consecutive dogfood weeks the bar must hold for.
CONSECUTIVE_WEEKS_REQUIRED = 2

# The report contract. `precision_report.py` writes `<STEM><YYYY-MM-DD>.json`
# into `<workspace>/_hq/audit-reports/` and stamps `kind` so a same-named file
# from some other tool cannot be mistaken for a week.
RECORD_KIND = "capture_precision_report"
REPORT_STEM = "CAPTURE_PRECISION_"
REPORT_DIR_PARTS = ("_hq", "audit-reports")

# Why the gate is red. Stable codes, ordered most-structural first.
BLOCKER_NO_REPORTS = "NO_REPORTS"
BLOCKER_TOO_FEW_WEEKS = "TOO_FEW_WEEKS"
BLOCKER_WEEKS_NOT_CONSECUTIVE = "WEEKS_NOT_CONSECUTIVE"
BLOCKER_SAMPLE_TOO_SMALL = "SAMPLE_TOO_SMALL"
BLOCKER_BELOW_BAR = "BELOW_BAR"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"


def report_dir(workspace_root) -> Path:
    return Path(workspace_root).joinpath(*REPORT_DIR_PARTS)


def _parse_date(text):
    try:
        return _dt.date.fromisoformat(str(text or "")[:10])
    except (TypeError, ValueError):
        return None


def _week_monday(day: _dt.date) -> _dt.date:
    """The Monday of `day`'s ISO week — the week's identity as a date, so
    adjacency is subtraction rather than year-boundary arithmetic (an ISO year
    can hold 52 or 53 weeks, and week 1 of one year and week 52 of the previous
    are adjacent)."""
    return day - _dt.timedelta(days=day.weekday())


def _iso_week(day: _dt.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year:04d}-W{week:02d}"


def load_reports(workspace_root) -> list:
    """Every readable weekly record, newest first.

    A file that is absent, unparseable, or not stamped with `RECORD_KIND` is
    skipped silently — it is not a week. The count of what WAS read rides the
    gate's answer, so a directory full of junk reads as "no weeks", never as a
    pass."""
    out = []
    directory = report_dir(workspace_root)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob(f"{REPORT_STEM}*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("kind") != RECORD_KIND:
            continue
        day = _parse_date(record.get("date"))
        if day is None:
            continue
        out.append((day, record))
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [record for _, record in out]


def _week_row(record: dict) -> dict:
    """One week's result, re-derived from the record's own measurements rather
    than trusting its stored verdict.

    A report carries `week_pass`, and reading that back would make the gate a
    mirror of whatever wrote the file — including a file written by an older
    build with a different bar. The bar lives HERE; the report supplies the
    measurement."""
    day = _parse_date(record.get("date"))
    precision = record.get("precision")
    try:
        precision = float(precision) if precision is not None else None
    except (TypeError, ValueError):
        precision = None
    try:
        denominator = int(record.get("denominator") or 0)
    except (TypeError, ValueError):
        denominator = 0
    meets_precision = precision is not None and precision >= PRECISION_BAR
    meets_sample = denominator >= MIN_LABELED_SAMPLE
    return {
        "date": day.isoformat() if day else "",
        "iso_week": _iso_week(day) if day else "",
        "precision": precision,
        "denominator": denominator,
        "meets_precision_bar": meets_precision,
        "meets_sample_floor": meets_sample,
        "week_pass": bool(meets_precision and meets_sample),
    }


def precision_gate_status(workspace_root) -> dict:
    """Is the capture-precision gate green?

    Returns:
      {"status": PASS|FAIL, "pass": bool,
       "bar": float, "min_sample": int, "weeks_required": int,
       "weeks_found": int, "reports_found": int,
       "weeks": [week row, ... newest first],
       "blockers": [CODE, ...]}

    One week per ISO week: the newest report in a week is that week's result.
    Two reports in the same week are one dogfood week, not two — the bar is
    worded over weeks, and a caller who ran the harness twice on Tuesday has
    not held anything for a second week."""
    reports = load_reports(workspace_root)
    seen_weeks = set()
    weeks = []
    for record in reports:
        row = _week_row(record)
        key = row["iso_week"]
        if not key or key in seen_weeks:
            continue
        seen_weeks.add(key)
        weeks.append(row)

    considered = weeks[:CONSECUTIVE_WEEKS_REQUIRED]
    blockers = []

    if not reports:
        blockers.append(BLOCKER_NO_REPORTS)
    elif len(considered) < CONSECUTIVE_WEEKS_REQUIRED:
        blockers.append(BLOCKER_TOO_FEW_WEEKS)

    if len(considered) == CONSECUTIVE_WEEKS_REQUIRED:
        mondays = [_week_monday(_dt.date.fromisoformat(w["date"]))
                   for w in considered]
        gaps = {(mondays[i] - mondays[i + 1]).days
                for i in range(len(mondays) - 1)}
        if gaps != {7}:
            blockers.append(BLOCKER_WEEKS_NOT_CONSECUTIVE)

    if any(not w["meets_sample_floor"] for w in considered):
        blockers.append(BLOCKER_SAMPLE_TOO_SMALL)
    if any(not w["meets_precision_bar"] for w in considered):
        blockers.append(BLOCKER_BELOW_BAR)

    passed = not blockers
    return {
        "status": STATUS_PASS if passed else STATUS_FAIL,
        "pass": passed,
        "bar": PRECISION_BAR,
        "min_sample": MIN_LABELED_SAMPLE,
        "weeks_required": CONSECUTIVE_WEEKS_REQUIRED,
        "reports_found": len(reports),
        "weeks_found": len(weeks),
        "weeks": considered,
        "blockers": blockers,
    }


__all__ = [
    "PRECISION_BAR",
    "MIN_LABELED_SAMPLE",
    "CONSECUTIVE_WEEKS_REQUIRED",
    "RECORD_KIND",
    "REPORT_STEM",
    "REPORT_DIR_PARTS",
    "BLOCKER_NO_REPORTS",
    "BLOCKER_TOO_FEW_WEEKS",
    "BLOCKER_WEEKS_NOT_CONSECUTIVE",
    "BLOCKER_SAMPLE_TOO_SMALL",
    "BLOCKER_BELOW_BAR",
    "STATUS_PASS",
    "STATUS_FAIL",
    "report_dir",
    "load_reports",
    "precision_gate_status",
]
