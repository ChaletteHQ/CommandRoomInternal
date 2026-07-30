#!/usr/bin/env python3
"""
Since-last-run processing windows (SPEC CATCHUP1, 2026-07-28).

THE GAP THIS CLOSES
-------------------
MAINT1 already solved DISPATCH: a missed fire self-heals because due-ness is
"last receipt older than the most recent nominal slot", so a laptop closed
through Sunday still runs cleanup on Monday (`maintenance_dispatcher`). What
it did NOT solve is what a late fire PROCESSES once it runs. Several surfaces
compute a window as a fixed offset from `now`:

  past-meetings   last 24 hours of meeting transcripts
  friday-wrap     [now - 7d, now]
  monthly-report  "the previous full calendar month"

Every one of those is measured from the clock, not from the last successful
run — so a closure LONGER than the window silently drops the span. Closed
Monday through Wednesday, Thursday's past-meetings fire sees Wednesday→
Thursday only: Monday's and Tuesday's meetings are never processed at all —
no notes, no commitments, no follow-ups, and nothing says so. The dispatcher
faithfully re-fires a job whose window has already forgotten the work.

`catchup_window` is the fix for the span class; `missed_periods` is the fix
for the partitioned class (a monthly deliverable where "the previous month"
is not a substitute for a month that was skipped — each period is its own
artifact and has to be produced under its own label).

BUILT ON EXISTING MACHINERY ONLY
--------------------------------
- Receipts come from the v4.5.2 R1 reader (`receipts.iter_receipts` — the
  same one pass `receipts.last_receipt_times` wraps; this module needs the
  newest receipt's DATA as well as its timestamp, so it calls the reader one
  level down rather than reading events itself). Shard-transparent, every
  legacy receipt spelling parsed forever, `TASK_PREDECESSORS` bridged so a
  split task's retired-id receipts still count (the CTS1 class).
- Cron math comes from `task_watchdog.expected_fires`. No second cron parser.
- Run-mode vocabulary comes from `late_fire.SCHEDULED_CONTEXT` and
  `receipts.normalize_fired_via` — imported, never copied.

TIME RULES
----------
Machine-local naive datetimes throughout — the clock cron actually evaluates
in (confirmed live 2026-07-01: machine=Mountain, workspace=Pacific). Matches
`late_fire.py` and `maintenance_dispatcher.py`. Workspace TZ is presentation-
only and never enters this math. The dicts returned carry ISO STRINGS so a
prose call site can `json.dumps` the result straight out of a `python3 -c`,
the same shape `check_lateness` and `dispatch_plan` already return.

THE CLOCK SEAM AT THE CONNECTOR BOUNDARY (F-1)
----------------------------------------------
The math above is machine-local because the SCHEDULER is (R8). A connector
query is not: `weekly-recap` resolves its Phase 1 window in WORKSPACE tz and
hands those timestamps to every connector. Handing a bare machine-local naive
value across that boundary is LATETZ's exact failure class — a value already
expressed in one clock re-labelled as another, invisible wherever the two
clocks agree (a UTC CI box, or M's machine where both are Pacific). It is
worse than a silent no-op, because `tz.to_local` documents naive input as
ASSUMED UTC, so a naive machine-local value passed through it moves by the
machine's whole UTC offset.

So every window this module returns carries BOTH forms, and the promotion
happens exactly ONCE, here:

  start / end              machine-local naive — receipt math, span
                           comparisons, anything that stays inside the
                           scheduler's clock.
  start_aware / end_aware  the SAME two instants carrying the machine's UTC
                           offset. THESE are what a connector query gets.

An offset-carrying timestamp is an absolute instant, so it is correct in
every zone and needs no second conversion. It is also safe to hand to
`tz.to_local` for DISPLAY — that helper converts an aware input rather than
re-labelling it — which is how a headline renders the span in the CEO's
timezone without moving it. Convert at the boundary, once, and nowhere else.

BEST-EFFORT, ALWAYS
-------------------
Catch-up widens a window; it must never be the reason a fire produces
nothing. Every substrate read here is wrapped: an unreadable events.jsonl,
a corrupt receipt, an unparseable cron all fall back to the nominal floor
window and say so in `reason`. RELIABILITY.md core principle — the primary
user task always wins.

THE BATCH-CAP TRAP (why `window_incomplete_before` exists)
----------------------------------------------------------
Widening a window without touching the batch cap creates a quieter version
of the same bug. past-meetings processes "up to 5 unprocessed meetings this
fire". A widened window that finds 12 processes 5, writes its receipt, and
the NEXT window starts after that receipt — silently orphaning the other 7,
permanently, with a green receipt on the record.

So a fire that could not finish its window records the oldest meeting it did
NOT process as `window_incomplete_before`, and this module resumes from THAT
value instead of the receipt time. The receipt then means "everything before
this point is handled", which is the only thing a window start may safely be
derived from. The writer owns the honesty: a fire that leaves anything in its
window unprocessed — including a fire that processed nothing and inherited a
marker from the previous fire — must carry the marker forward. That contract
is stated at each call site; here it is only read.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from late_fire import SCHEDULED_CONTEXT  # noqa: E402
from receipts import (  # noqa: E402
    TASK_PREDECESSORS,
    iter_receipts,
    normalize_fired_via,
    normalize_task_id,
)
from task_watchdog import expected_fires  # noqa: E402

# The ONE spelling of the batch-cap marker, reader and writer side. The F-50
# P2c lesson in one constant: an unspecified field name gets improvised
# differently by every fire that writes it (`late_tier` vs `lateness_tier`,
# same skill, same day).
WINDOW_INCOMPLETE_FIELD = "window_incomplete_before"

# Hard ceiling on any widened span. A machine off for a quarter should not
# make one fire re-read a quarter of connector history.
DEFAULT_CAP_DAYS = 30

# Ceiling on how many missed periods one fire will produce deliverables for.
DEFAULT_PERIOD_CAP = 12


def _now_local() -> _dt.datetime:
    """Naive machine-local now — the clock cron actually evaluates in."""
    return _dt.datetime.now()


def _to_local_naive(dt: Optional[_dt.datetime]) -> Optional[_dt.datetime]:
    """Aware → naive machine-local. Naive values pass through untouched."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_iso_local(value) -> Optional[_dt.datetime]:
    """Parse an ISO timestamp from substrate into machine-local naive time.
    Anything unparseable returns None — a corrupt marker degrades to "no
    marker", never to an exception."""
    if isinstance(value, _dt.datetime):
        return _to_local_naive(value)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        return _to_local_naive(_dt.datetime.fromisoformat(raw))
    except ValueError:
        return None


def to_connector_iso(value, *, tz=None) -> Optional[str]:
    """Machine-local naive -> an ISO string carrying the machine's UTC offset.

    THE ONE PROMOTION (F-1). Everything in this module is machine-local naive
    because the scheduler is; a connector query needs an unambiguous INSTANT.
    This is where the two meet, and it happens once:

      - naive in  -> the machine's own zone is attached (`.astimezone()` on a
        naive datetime is the one legal reading of it — Python interprets a
        naive value as local time and attaches the local offset). Nothing
        moves; the same wall-clock moment simply becomes self-describing.
      - AWARE in   -> returned untouched. An instant is already an instant,
        and converting it again is the LATETZ defect.

    `tz` is an explicit machine zone for tests and simulation, so the seam can
    be pinned with machine tz != workspace tz on a box where they agree (and
    on a UTC CI runner, where a naive-as-UTC bug is accidentally correct and
    therefore invisible). Production leaves it None.

    Returns None for None / unparseable input — a window that could not be
    computed must not produce a plausible-looking connector bound.
    """
    dt = value if isinstance(value, _dt.datetime) else _parse_iso_local(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz) if tz is not None else dt.astimezone()
    return dt.isoformat()


def receipt_window_marker(window, *, incomplete: bool,
                          oldest_unhandled=None) -> Optional[str]:
    """The value a fire's receipt must carry in `WINDOW_INCOMPLETE_FIELD` —
    or None, meaning OMIT the key.

    The writer half of the batch-cap contract, as code rather than judgement.
    Both widened surfaces call it, so the field spelling, the clamping and the
    carry-forward rule cannot be re-improvised per call site (the F-50 P2c
    class, which is why `WINDOW_INCOMPLETE_FIELD` exists at all).

    Args:
      window: the dict `catchup_window` returned for THIS fire.
      incomplete: did this fire leave any of `[start, end]` unhandled? Batch
        cap hit, a capped connector read that came back at its cap, a fetch
        that failed mid-run — or a fire that processed nothing at all while
        the window it was handed already carried an inherited marker.
      oldest_unhandled: where the tail begins, when the surface can name it.
        `past-meetings` can: its cap truncates a chronologically sorted list,
        so the oldest still-unprocessed meeting is a real resume point and the
        marker ADVANCES as the backlog drains.
        `weekly-recap` cannot, and passing None is the correct answer there
        rather than a defect: its caps drop the LOWEST-RANKED items, scattered
        across the span, so no contiguous tail exists. The only honest resume
        point is the window's own start — the next fire re-covers the whole
        span, which costs nothing because capture dedups on `source_ref_hash`.

    Clamped into `[start, end]`: a marker before the start would re-open work
    the 30-day ceiling already ruled out of scope (that ceiling is what bounds
    a window that keeps truncating), and a marker after the end would strand
    the very items it is supposed to protect.

    Returns None when `incomplete` is False — a receipt WITHOUT the field is
    the positive assertion "everything before this point is handled", and it
    is the only thing the next window's start may be derived from.
    """
    if not incomplete:
        return None
    win = window if isinstance(window, dict) else {}
    start = _parse_iso_local(win.get("start"))
    end = _parse_iso_local(win.get("end"))
    candidate = _parse_iso_local(oldest_unhandled)
    if candidate is None:
        candidate = start
    if candidate is None:
        # No parseable window at all. There is no honest resume point to
        # write; the caller's own best-effort fallback (the nominal window)
        # already covers the floor. Documented, and pinned in the suite.
        return None
    if start is not None and candidate < start:
        candidate = start
    if end is not None and candidate > end:
        candidate = end
    return candidate.isoformat()


def _coerce_number(value, fallback: float) -> float:
    """A count of hours/days from a caller that may be a prose template.

    Both public entry points here are invoked from registered prompts, where
    every argument arrives as whatever string an executing model substituted
    into a placeholder — DOGFIX1's lesson, generalized. `"24"` is 24;
    `'<floor>'` is not a number and falls back rather than raising, because a
    fire that dies computing its window produces nothing at all, which is
    strictly worse than a fire that covers its nominal span.
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if out != out or out <= 0:  # NaN or nonsense
        return float(fallback)
    return out


def _incomplete_marker(data) -> Optional[_dt.datetime]:
    """The batch-cap marker off a receipt's `data`, machine-local naive.

    THIS READ IS THE FENCE. Remove it and `catchup_window` silently resumes
    from the receipt time instead of the oldest still-unprocessed item — the
    exact orphaning bug the marker exists to prevent, now with a green
    receipt on the record. `run_catchup_window_test.py` case 8 removes this
    line from a copy of the source and asserts case 9 goes red.
    """
    if not isinstance(data, dict):
        return None
    raw = data.get(WINDOW_INCOMPLETE_FIELD)
    return _parse_iso_local(raw)


def last_successful_point(workspace_root, task_id) -> dict:
    """How far a task's processing has actually reached — the only honest
    basis for the next window's start.

    Returns {"dt": datetime|None, "receipt_dt": datetime|None,
             "incomplete_before": datetime|None} in machine-local naive time.

    `dt` is the marker when the newest receipt carries one, else the newest
    receipt's own timestamp, else None (never receipted). The NEWEST receipt
    is the one consulted: a later fire that completed its window is exactly
    the event that clears an earlier fire's marker.

    Raises nothing of its own, but lets a substrate read failure propagate to
    `catchup_window`, which owns the best-effort fallback.
    """
    canonical = normalize_task_id(task_id)
    accepted = [canonical] + list(TASK_PREDECESSORS.get(canonical, ()))
    newest = None
    newest_dt = None
    for r in iter_receipts(workspace_root, task_ids=accepted):
        dt = _to_local_naive(r.get("dt"))
        if dt is None:
            continue
        if newest_dt is None or dt >= newest_dt:
            newest, newest_dt = r, dt
    if newest is None:
        return {"dt": None, "receipt_dt": None, "incomplete_before": None}
    raw = newest.get("raw") if isinstance(newest.get("raw"), dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    marker = _incomplete_marker(data)
    return {
        "dt": marker if marker is not None else newest_dt,
        "receipt_dt": newest_dt,
        "incomplete_before": marker,
    }


def catchup_window(
    workspace_root,
    task_id: str,
    *,
    floor_hours: float,
    cap_days: float = DEFAULT_CAP_DAYS,
    now: Optional[_dt.datetime] = None,
    fired_via: Optional[str] = None,
    scheduled_only: bool = False,
) -> dict:
    """The span a fire must process: everything since its last successful run,
    never narrower than its nominal window, never wider than `cap_days`.

        start = clamp(last_successful_point, now - cap_days, now - floor_hours)

    Args:
      floor_hours: the task's NOMINAL window (24 for past-meetings, 168 for
        the weekly wrap). The span is never narrower than this, so a fire
        that ran an hour ago still covers its normal ground.
      cap_days: hard ceiling. When it clamps, `capped` is True so the caller
        can say the span was truncated rather than imply completeness.
      fired_via / scheduled_only: the F-2 rule as code rather than prose.
        With `scheduled_only=True`, catch-up applies ONLY to a scheduled-
        context fire (`fired_via` in `late_fire.SCHEDULED_CONTEXT`) — a human
        typing "weekly recap" means last week, not five weeks. Fail-safe
        direction is DOGFIX1's: anything that does not normalize into the
        scheduled set (an unsubstituted `'<scheduled|manual>'` placeholder,
        `'Run Now'`, `''`, an omitted argument) is treated as manual and gets
        the plain floor window. Call sites that WANT catch-up on every fire
        (past-meetings — re-processing is idempotent and catching the backlog
        up is the point) simply leave `scheduled_only` False.

    Returns a json-dumps-able dict:
      start / end / last_receipt / window_incomplete_before — ISO strings
        (or None), machine-local naive, matching the shape `check_lateness`
        and `dispatch_plan` already hand back to prose call sites.
      start_aware / end_aware — the SAME two instants carrying the machine's
        UTC offset. Every CONNECTOR query takes these, never the naive pair:
        a connector needs an unambiguous instant, and a machine-local naive
        value crossing into a workspace-TZ phase is LATETZ's failure class
        (F-1). See the module docstring's connector-boundary section.
      days      — float span length.
      extended  — True when the span exceeds `floor_hours`. THE one signal an
                  orchestrator needs to know it is doing catch-up work.
      capped    — True when `cap_days` truncated the span.
      reason    — one plain sentence naming what set the start.
      error     — repr of a swallowed substrate failure, else None.
      task / floor_hours / cap_days — echoed inputs, so a logged window is
                  self-describing.

    Never raises. Unusable arguments (a `now` that isn't a datetime, a
    `floor_hours` that isn't a number — both reachable from a prose call site)
    fall back to sane values, and a substrate read that fails falls back to
    the floor window with `error` set: catch-up must never block a fire's
    primary output.
    """
    now = _parse_iso_local(now) or _now_local()
    floor_hours = _coerce_number(floor_hours, 24)
    cap_days = _coerce_number(cap_days, DEFAULT_CAP_DAYS)
    floor = _dt.timedelta(hours=floor_hours)
    cap = _dt.timedelta(days=cap_days)
    floor_start = now - floor
    cap_start = now - cap

    out = {
        "task": normalize_task_id(task_id),
        "start": floor_start.isoformat(),
        "end": now.isoformat(),
        # The same two instants, offset-carrying. Connector queries take
        # THESE; see "THE CLOCK SEAM AT THE CONNECTOR BOUNDARY" above.
        "start_aware": to_connector_iso(floor_start),
        "end_aware": to_connector_iso(now),
        "days": round(floor.total_seconds() / 86400.0, 6),
        "extended": False,
        "capped": False,
        "last_receipt": None,
        "window_incomplete_before": None,
        "floor_hours": floor_hours,
        "cap_days": cap_days,
        "reason": "",
        "error": None,
    }

    if scheduled_only:
        via = normalize_fired_via(fired_via)
        if via not in SCHEDULED_CONTEXT:
            out["reason"] = (
                "interactive fire — the nominal window applies "
                f"(last {_describe(floor)}); catch-up is for scheduled fires"
            )
            return out

    try:
        point = last_successful_point(workspace_root, task_id)
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        out["reason"] = (
            f"couldn't read the run history — falling back to the nominal "
            f"window (last {_describe(floor)})"
        )
        out["error"] = repr(exc)[:200]
        return out

    basis = point["dt"]
    out["last_receipt"] = (
        point["receipt_dt"].isoformat() if point["receipt_dt"] else None
    )
    out["window_incomplete_before"] = (
        point["incomplete_before"].isoformat() if point["incomplete_before"] else None
    )

    if basis is None:
        # A fresh install does NOT back-process a month of history.
        out["reason"] = (
            f"no run recorded yet — processing the nominal window "
            f"(last {_describe(floor)})"
        )
        return out

    capped = basis < cap_start
    start = min(max(basis, cap_start), floor_start)
    span = now - start

    out["start"] = start.isoformat()
    out["start_aware"] = to_connector_iso(start)
    out["days"] = round(span.total_seconds() / 86400.0, 6)
    out["extended"] = span > floor
    out["capped"] = capped

    if capped:
        out["reason"] = (
            f"last processed {basis.isoformat()} — older than the "
            f"{_trim(cap_days)}-day ceiling, so this fire covers the last "
            f"{_trim(cap_days)} days"
        )
    elif point["incomplete_before"] is not None and out["extended"]:
        out["reason"] = (
            f"the previous fire could not finish its window — resuming from "
            f"{basis.isoformat()}, the oldest item it left unprocessed"
        )
    elif out["extended"]:
        out["reason"] = (
            f"last processed {basis.isoformat()} — processing everything since"
        )
    else:
        out["reason"] = (
            f"last processed {basis.isoformat()} — inside the nominal window, "
            f"so the usual last {_describe(floor)} applies"
        )
    return out


def missed_periods(
    cron: str,
    last_receipt,
    *,
    now: Optional[_dt.datetime] = None,
    cap: int = DEFAULT_PERIOD_CAP,
) -> list:
    """Every nominal slot in (last_receipt, now] that never got served —
    OLDEST FIRST, so the caller iterates the list and produces one deliverable
    per period in chronological order.

    For period-partitioned jobs (the monthly operator report + value receipt,
    the opt-in monthly scorecard) where each period is its OWN artifact and
    "the previous month" is not a substitute for a month that was skipped.
    `expected_fires(count=1)` — what the dispatcher's due rule uses — can see
    that such a job is due, but structurally cannot see that TWO periods were
    missed; it returns the newest slot and the older period is lost forever.

    Args:
      cron: the job's nominal cadence (`0 0 1 * *` for monthly).
      last_receipt: the job's newest receipt — a datetime (naive machine-local
        or aware) or an ISO string, since this is reachable from a prose call
        site. None, or anything unparseable, means never receipted.
      cap: ceiling on returned periods. When more slots were missed than the
        cap, the OLDEST are dropped — a caller that wants to say so compares
        `len(result)` against `cap`.

    Returns machine-local naive datetimes. Empty list when nothing is missed.
    Never raises: an unparseable cron returns [].

    Fresh install (`last_receipt is None`) returns the single most recent slot
    — the same posture as `catchup_window`'s no-receipt branch. A workspace
    installed today does not owe the customer a year of backdated monthly
    reports, and producing them would be inventing history from a substrate
    that has none.
    """
    now = _parse_iso_local(now) or _now_local()
    try:
        cap = max(1, int(cap))
    except (TypeError, ValueError):
        cap = DEFAULT_PERIOD_CAP
    try:
        slots = expected_fires(cron, now=now, count=cap)
    except Exception:  # noqa: BLE001 — CronParseError and anything else
        return []
    if not slots:
        return []

    last = _parse_iso_local(last_receipt)
    if last is None:
        return [slots[0]]
    # expected_fires returns newest-first; keep only unserved slots and hand
    # them back oldest-first.
    return sorted(s for s in slots if s > last)


def _describe(delta: _dt.timedelta) -> str:
    """'24 hours' / '7 days' — for the one plain sentence in `reason`."""
    hours = delta.total_seconds() / 3600.0
    if hours >= 48 and abs(hours % 24) < 1e-9:
        return f"{_trim(hours / 24)} days"
    return f"{_trim(hours)} hours"


def _trim(value: float) -> str:
    """30.0 -> '30', 1.5 -> '1.5'."""
    return f"{value:g}"


def main(argv: Optional[list] = None) -> int:
    """CLI: print a task's catch-up window as JSON.

    python3 catchup.py <workspace_root> <task_id> --floor-hours 24
                       [--cap-days 30] [--now ISO] [--fired-via scheduled]
                       [--scheduled-only]
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Command Room since-last-run processing window")
    parser.add_argument("workspace_root", help="absolute path to the workspace root")
    parser.add_argument("task_id", help="canonical task id (past-meetings, friday-wrap, ...)")
    parser.add_argument("--floor-hours", type=float, required=True,
                        help="the task's nominal window in hours (24, 168, ...)")
    parser.add_argument("--cap-days", type=float, default=DEFAULT_CAP_DAYS)
    parser.add_argument("--now", default=None,
                        help="frozen machine-local ISO datetime (testing/simulation)")
    parser.add_argument("--fired-via", default=None,
                        help="scheduled | catchup | manual (this fire's run mode)")
    parser.add_argument("--scheduled-only", action="store_true",
                        help="apply catch-up on scheduled-context fires only")
    args = parser.parse_args(argv)

    now = None
    if args.now:
        try:
            now = _dt.datetime.fromisoformat(args.now)
        except ValueError:
            print(json.dumps({"error": f"unparseable --now value: {args.now!r}"}))
            return 2
    print(json.dumps(catchup_window(
        args.workspace_root, args.task_id,
        floor_hours=args.floor_hours, cap_days=args.cap_days, now=now,
        fired_via=args.fired_via, scheduled_only=args.scheduled_only,
    ), indent=2))
    return 0


__all__ = [
    "WINDOW_INCOMPLETE_FIELD",
    "DEFAULT_CAP_DAYS",
    "DEFAULT_PERIOD_CAP",
    "catchup_window",
    "missed_periods",
    "last_successful_point",
    "receipt_window_marker",
    "to_connector_iso",
]


if __name__ == "__main__":
    sys.exit(main())
