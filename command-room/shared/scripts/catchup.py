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
import os
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

# THE ONE MAINTENANCE WINDOW BOUND (cr1#663, the cr1#609 rulings of
# 2026-11-03). Every maintenance job reads since its own last receipt with
# no day cap below this figure; a job with no receipt reads this many days;
# a gap longer than this is treated as a first run and reads this many days,
# the receipt saying so. It is the same figure as `DEFAULT_CAP_DAYS` on
# purpose and named so a reader can tell the two uses apart: the cap bounds
# a widened span, this is the first-run floor every job shares.
MAINTENANCE_WINDOW_DAYS = DEFAULT_CAP_DAYS

# How far past that bound a CARRY-OVER MARKER is still honoured. The bound
# caps a gap: a seat that was away. A marker on a recent receipt is not a gap,
# it is work in progress: the job read those items and is working through
# them, so items held on a first run's oldest day would otherwise drop off the
# rolling floor between one fire and the next. The grace is finite because a
# hold can also be a row that will never be readable; past it the marker is
# dropped, as a gap's window is, and the job moves on, and the fire says so
# once (`hold_dropped_span`): what the hold still owed is not read again.
# Seven days is one weekly cycle of fires, far more than a cap of a dozen items
# a fire needs to drain.
HOLD_GRACE_DAYS = 7

# The ONE spelling of the window a job receipt names, reader and writer
# side (the `WINDOW_INCOMPLETE_FIELD` lesson). A job's own receipt carries
# the helper's dict under `MAINTENANCE_WINDOW_FIELD`; the run-level
# `maintenance_run` receipt carries one dict per job under
# `MAINTENANCE_WINDOWS_FIELD`, keyed by job id. Not `window`: the session
# sweep's receipt already carries a label string under that key, and an
# existing field never changes shape.
MAINTENANCE_WINDOW_FIELD = "maintenance_window"
MAINTENANCE_WINDOWS_FIELD = "maintenance_windows"

# The two reasons a maintenance window can give. `cursor`: since the job's
# own last receipt (or its own cursor). `first_run_window`: the 30-day
# window, because the job has no receipt at all OR its last receipt is
# older than the bound (`gap_capped` on the dict keeps the two apart).
WINDOW_REASON_CURSOR = "cursor"
WINDOW_REASON_FIRST_RUN = "first_run_window"

# Ceiling on how many missed periods one fire will produce deliverables for.
DEFAULT_PERIOD_CAP = 12

# THE STATUSES THAT MOVE A WINDOW (cr1#740). A receipt says what its run did;
# only a run that read its whole window may be the basis of the next one.
# `complete` is that run; `degraded` is a real read over narrower cover (the
# chat leg's word, cr1#743), its gap named on the receipt. A receipt with no
# status at all is an older shape written only on completion, and counts.
# Every other word (`skipped`, `blocked`, `failed`, anything this module
# does not know) is a run that read nothing, or not all of it: the window
# stays where the last window-moving receipt left it. Before this rule the
# newest receipt of ANY status moved the window, so a blocked read's span
# was never read again, and a seat that skipped for weeks (no connector
# declared yet) got a 0-day window on its first real run instead of the
# ruled 30 days. The same vocabulary as the dispatcher's
# `COMPLETE_RECEIPT_STATUSES` (cr1#760), spelled here too because the
# dispatcher imports this module.
WINDOW_MOVING_STATUSES = frozenset({"complete", "degraded"})


#: The one exception to the status rule: a leg whose commitment dials are off
#: still reads its connector for people and project signal (the tasks legs).
#: Its receipt says `skipped` (nothing captured or closed, the job stays owed)
#: yet the read happened, whole, so it says so here and it moves the window.
#: Without it such a seat re-reads the same span on every fire for good.
WINDOW_READ_FIELD = "window_read"

# THE PAGED WINDOW (cr1#948, cr1#941). A first run, or a long gap, hands a
# job a 30-day window, and a background fire cannot read 30 days of a busy
# connector: the inbound-mail run stopped after 2 pages of about 600 threads
# and re-opened the same 30 days on every fire, and the meeting capture would
# not start at all. So a job reads its window ONE PAGE per fire, oldest page
# first (the meeting capture pages newest first, cr1#978, below), and the
# receipt's carry-over marker (the same
# `WINDOW_INCOMPLETE_FIELD`) moves to where the page ended. The next fire
# opens there, until the window reaches the present. The 30-day bound stands
# and nothing in it is dropped. `PAGE_FIELD` is the page's dict on the
# receipt, the one spelling reader and writer side.
PAGE_FIELD = "window_page"
# The first page's span, and the bounds every later page is held inside. A
# page that read to its end doubles the next one (a quiet seat reaches the
# present in a few fires); a page whose read stopped short halves it (a busy
# one still moves). A page after a short one keeps its span, so a busy seat
# does not swing between a page too big and one that fits.
PAGE_FIRST_DAYS = 3.0
PAGE_MIN_DAYS = 0.25
PAGE_MAX_DAYS = float(DEFAULT_CAP_DAYS)

# NEWEST FIRST (cr1#978). Oldest-first paging made a meeting held today wait
# behind every older page of a 30-day backlog, a few days per fire, so the
# promises made in it reached the book days late. A job that pages newest
# first (`paged_window(..., newest_first=True)`; the meeting capture) reads
# the NEWEST page first, ending now, and holds the older days as a backlog
# behind it. Later passes take turns: the newest meetings since the last
# newest read (the HEAD), then one page of the backlog, oldest first, until
# it is drained. Each pass still reads ONE contiguous span, so a job adopts
# it with nothing new to read. The page dict says which part it is and
# where the two parts stand, the one spelling reader and writer side:
#   order          PAGE_ORDER_NEWEST_FIRST
#   part           PAGE_PART_HEAD or PAGE_PART_BACKLOG
#   head_until     where the newest read reached (an instant); everything
#                  from `backlog_until` to here is handled
#   backlog_until  the backlog's upper end (an instant), or None when no
#                  backlog is owed; the backlog runs from the window's own
#                  start (the receipt's carry-over marker) up to it
# The marker keeps its one meaning, the oldest point still owed, so every
# reader of `WINDOW_INCOMPLETE_FIELD` reads it unchanged.
PAGE_ORDER_NEWEST_FIRST = "newest_first"
PAGE_PART_HEAD = "head"
PAGE_PART_BACKLOG = "backlog"


def read_reached_end(flag):
    """The `read_complete` a writer was handed, as True, False or None (cr1#742).
    None is a caller that predates the flag and counts as a whole read: a
    scheduled prompt is baked on the seat, so failing closed would hold those
    seats' windows for good. A word from a model's JSON is read as the word it
    is ("false" is False), never by truthiness."""
    if isinstance(flag, str):
        word = flag.strip().lower()
        if word in ("false", "no", "0"):
            return False
        if word in ("true", "yes", "1"):
            return True
        return None
    return None if flag is None else bool(flag)


def receipt_moves_window(status, data=None) -> bool:
    """Does a receipt with this `status` move its job's window? (cr1#740)
    None, or an empty string, is an older shape that was only ever written
    on completion, so it does. `data` is the receipt's own data, for the
    `WINDOW_READ_FIELD` exception."""
    if isinstance(data, dict) and data.get(WINDOW_READ_FIELD) is True:
        return True
    if status is None:
        return True
    if not isinstance(status, str):
        return False
    word = status.strip().lower()
    return (not word) or word in WINDOW_MOVING_STATUSES


def _now_local() -> _dt.datetime:
    """Naive MACHINE-local now — the clock cron actually evaluates in.

    CLOCK1: the INSTANT is corroborated against the workspace ledger; the
    ZONE is untouched. A stale clock used to make the catch-up window claim
    a whole day had not happened yet. Falls back to the raw machine clock if
    the helper is unavailable.
    """
    try:
        from trusted_now import trusted_now_local_naive

        return trusted_now_local_naive()
    except Exception:
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
    # cr1#741: an instant, not this machine's wall clock. A seat's fires can
    # run on machines in two zones; a marker written as naive local text is
    # read as a different instant on the other one, and the next fetch can
    # start after the very items it holds. Every reader goes through
    # `_parse_iso_local`, which takes an offset.
    return to_connector_iso(candidate)


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
             "incomplete_before": datetime|None,
             "newest_receipt_dt": datetime|None,
             "held_by_status": str|None} in machine-local naive time.

    `dt` is the marker when the newest WINDOW-MOVING receipt carries one,
    else that receipt's own timestamp, else None (never completed). The
    newest window-moving receipt is the one consulted: a later fire that
    completed its window is exactly the event that clears an earlier fire's
    marker.

    cr1#740: a receipt whose status does not move the window
    (`receipt_moves_window`: skipped, blocked, failed) is read past. A
    blocked read's span is read again on the next run, and a job whose only
    receipts are skips (no connector declared yet) is a first run. THIS
    FILTER IS THE FENCE: remove it and a blocked run moves the window past
    the mail, items and sessions it never read, with a receipt on the
    record saying so. `newest_receipt_dt` is the newest receipt of any
    status, for a reader that wants to know the job fired; `held_by_status`
    is that receipt's status when it did not move the window, else None.

    Raises nothing of its own, but lets a substrate read failure propagate to
    `catchup_window`, which owns the best-effort fallback.
    """
    canonical = normalize_task_id(task_id)
    accepted = [canonical] + list(TASK_PREDECESSORS.get(canonical, ()))
    newest = None
    newest_dt = None
    any_dt = None
    any_status = None
    for r in iter_receipts(workspace_root, task_ids=accepted):
        dt = _to_local_naive(r.get("dt"))
        if dt is None:
            continue
        if any_dt is None or dt >= any_dt:
            any_dt, any_status = dt, r.get("status")
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        if not receipt_moves_window(r.get("status"), raw.get("data")):
            continue  # cr1#740: a run that read nothing holds the window
        if newest_dt is None or dt >= newest_dt:
            newest, newest_dt = r, dt
    held = (any_status if (any_dt is not None
                           and (newest_dt is None or any_dt > newest_dt))
            else None)
    if newest is None:
        return {"dt": None, "receipt_dt": None, "incomplete_before": None,
                "receipt_seq": None, "page": None,
                "newest_receipt_dt": any_dt, "held_by_status": held}
    raw = newest.get("raw") if isinstance(newest.get("raw"), dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    marker = _incomplete_marker(data)
    seq = raw.get("seq")
    if marker is not None:
        # A capped run stepped back over its own window, so the book position
        # it opens at steps back with it: rows the cap left past that run's
        # start reached the book before its receipt and must stay in reach.
        ran = data.get(MAINTENANCE_WINDOW_FIELD)
        seq = ran.get("since_seq") if isinstance(ran, dict) else None
    return {
        "dt": marker if marker is not None else newest_dt,
        "receipt_dt": newest_dt,
        "incomplete_before": marker,
        # cr1#761: where that receipt sits in the book, so a reader can tell
        # what reached the book after it, whatever the record's own time.
        "receipt_seq": seq if isinstance(seq, int) and not isinstance(seq, bool) else None,
        # cr1#948: the page that receipt read, when its job pages its window
        # (`paged_window`), so the next page is sized from how that one went.
        "page": (data.get(PAGE_FIELD)
                 if isinstance(data.get(PAGE_FIELD), dict) else None),
        "newest_receipt_dt": any_dt,
        "held_by_status": held,
    }


def _window_dict(job_id, basis, *, now: _dt.datetime,
                 last_receipt: Optional[_dt.datetime] = None,
                 incomplete_before: Optional[_dt.datetime] = None,
                 error: Optional[str] = None,
                 since_seq: Optional[int] = None) -> dict:
    """The one piece of window math every maintenance job shares.

    `basis` is where the job's last successful read reached (its newest
    receipt, the batch-cap marker on it, or the leg's own cursor), machine-
    local naive, or None for a job that has never read. The rule, in order:

      no basis                      -> the last MAINTENANCE_WINDOW_DAYS,
                                       reason `first_run_window`
      basis older than that bound   -> the same window, the same reason,
                                       `gap_capped` True (the gap is treated
                                       as a first run; cr1#609 build default)
      a carry-over marker on a      -> since the marker, reason `cursor`,
      recent receipt, up to             not capped: the job is still working
      HOLD_GRACE_DAYS past the bound    through items it has read
      a marker past that grace, on  -> the bound's window, `gap_capped` True
      a recent receipt                  and `hold_dropped` True: the hold is
                                        given up, and the fire says so once
      otherwise                     -> since the basis, reason `cursor`,
                                       no floor and no cap

    THIS IS THE FENCE. Remove the capped branch and a machine off for a
    quarter re-reads a quarter of connector history on one fire;
    `run_maintenance_window_test.py` section [6] removes it from a copy of
    this file and watches section [2] go red.
    """
    floor_start = now - _dt.timedelta(days=MAINTENANCE_WINDOW_DAYS)
    hold_floor = floor_start - _dt.timedelta(days=HOLD_GRACE_DAYS)
    out = {
        "job_id": normalize_task_id(job_id),
        "since": floor_start.isoformat(),
        "until": now.isoformat(),
        # The same two instants, offset-carrying: what a connector query
        # takes (see "THE CLOCK SEAM AT THE CONNECTOR BOUNDARY" above).
        "since_aware": to_connector_iso(floor_start),
        "until_aware": to_connector_iso(now),
        "reason": WINDOW_REASON_FIRST_RUN,
        "days": float(MAINTENANCE_WINDOW_DAYS),
        "gap_capped": False,
        "last_receipt": last_receipt.isoformat() if last_receipt else None,
        "window_incomplete_before": (incomplete_before.isoformat()
                                     if incomplete_before else None),
        "cap_days": float(MAINTENANCE_WINDOW_DAYS),
        "error": error,
        # cr1#761: the book position of the receipt this window opens at. A
        # record that reached the book after it is in the window however it
        # is dated (a meeting held before the last run but captured after).
        # None for a job with no receipt or a leg that keeps its own cursor.
        "since_seq": since_seq,
    }
    if basis is None:
        return out
    if (basis == incomplete_before and basis >= hold_floor
            and last_receipt is not None and last_receipt >= floor_start):
        return _from_basis(out, basis, now)  # a hold, not a gap
    # The grace ran out on a seat that kept running: no gap, but the span the
    # marker still held is given up here.
    if (basis < floor_start and basis == incomplete_before
            and last_receipt is not None and last_receipt >= floor_start):
        out["hold_dropped"] = True  # present only then: a plain window is unchanged
    if basis < floor_start:
        out["gap_capped"] = True
        return out
    return _from_basis(out, basis, now)


def _from_basis(out: dict, basis: _dt.datetime, now: _dt.datetime) -> dict:
    out["since"] = basis.isoformat()
    out["since_aware"] = to_connector_iso(basis)
    out["reason"] = WINDOW_REASON_CURSOR
    out["days"] = round((now - basis).total_seconds() / 86400.0, 6)
    return out


def maintenance_window(workspace_root, job_id: str, *,
                       now: Optional[_dt.datetime] = None) -> dict:
    """The window ONE maintenance job reads on this fire (cr1#663): since
    its own last receipt, no day cap; the last 30 days when it has no
    receipt; a gap longer than 30 days treated as a first run (30 days) and
    said so. One rule for every job, computed from the job's newest receipt
    through the same reader `catchup_window` uses (`last_successful_point`,
    so a batch-cap marker is honoured here too).

    Returns a json-dumps-able dict:
      since / until               machine-local naive ISO strings
      since_aware / until_aware   the same instants carrying the machine's
                                  UTC offset, for connector queries
      reason                      `cursor` | `first_run_window`
      days                        float span length
      gap_capped                  True when a receipt exists but is older
                                  than the bound (the two first-run cases
                                  stay distinguishable)
      last_receipt                the newest receipt's time, or None
      window_incomplete_before    the batch-cap marker, when the newest
                                  receipt carries one
      since_seq                   the newest receipt's place in the book
                                  (cr1#761): a record appended after it is
                                  in the window whatever its own time
      cap_days / job_id / error   echoed, so a logged window is
                                  self-describing

    Never raises: an unreadable book falls back to the first-run window
    with `error` set, because a fire that dies computing its window
    produces nothing at all. Call it BEFORE the job runs (a plan, or a
    receipt writer about to land this run's receipt); once this run's own
    receipt is on the book the newest receipt is this run's.
    """
    now = _parse_iso_local(now) or _now_local()
    try:
        point = last_successful_point(workspace_root, job_id)
    except Exception as exc:  # noqa: BLE001 - best-effort by contract
        return _window_dict(job_id, None, now=now, error=repr(exc)[:200])
    out = _window_dict(job_id, point["dt"], now=now,
                       last_receipt=point["receipt_dt"],
                       incomplete_before=point["incomplete_before"],
                       since_seq=point.get("receipt_seq"))
    # cr1#740: a newer receipt that did not move the window (a skip, a
    # blocked read) is named, so a plan can say why the window reaches back
    # past the job's newest receipt.
    out["held_by_status"] = point.get("held_by_status")
    return out


def gap_unread_span(window) -> Optional[dict]:
    """The days a long gap cost (cr1#746), or None for any other window:
    `{"from", "to", "days"}`, ISO dates, machine-local.

    A gap is the job's newest receipt older than the bound. A window held
    back by a carry-over marker on a recent receipt is not one, however old
    the marker: the seat ran, and nothing was left unread by being away. The
    span runs from where the last read reached up to the day before the
    window opens, and always names at least one day: a gap of thirty days
    and a few hours lost those hours."""
    win = window if isinstance(window, dict) else {}
    if not win.get("gap_capped"):
        return None
    last = _parse_iso_local(win.get("last_receipt"))
    since = _parse_iso_local(win.get("since"))
    if last is None or since is None or last >= since:
        return None
    marker = _parse_iso_local(win.get("window_incomplete_before"))
    first = min(last, marker or last).date()
    end = max(first, since.date() - _dt.timedelta(days=1))
    return {"from": first.isoformat(), "to": end.isoformat(),
            "days": (end - first).days + 1}


def gap_capped_line(window) -> str:
    """The one sentence the owner reads when a gap longer than the bound was
    cut to the bound (cr1#746): which days were not read. Empty for any
    other window. `maintenance_dispatcher` says it once per fire on the run
    receipt; no job's own summary carries it, because nothing reads those."""
    span = gap_unread_span(window)
    if span is None:
        return ""
    n = span["days"]
    bound = _trim(MAINTENANCE_WINDOW_DAYS)
    dates = (span["from"] if n == 1 else f"{span['from']} to {span['to']}")
    return (f"After a long gap only the newest {bound} days were read: {n} "
            f"day{'s' if n != 1 else ''} ({dates}) "
            f"{'were' if n != 1 else 'was'} not.")


def hold_dropped_span(window) -> Optional[dict]:
    """The days a carry-over marker gave up when it passed the grace, or None
    for any other window (cr1#746, cr1#741): `{"from", "to", "days"}`, ISO
    dates, machine-local, from the marker to the day before the window now
    opens. Not a gap: this seat kept running, so it never reads as one. A
    held window inside the grace is None, however old its marker."""
    win = window if isinstance(window, dict) else {}
    if not win.get("hold_dropped"):
        return None
    marker = _parse_iso_local(win.get("window_incomplete_before"))
    since = _parse_iso_local(win.get("since"))
    if marker is None or since is None or marker >= since:
        return None
    first = marker.date()
    end = max(first, since.date() - _dt.timedelta(days=1))
    return {"from": first.isoformat(), "to": end.isoformat(),
            "days": (end - first).days + 1}


def hold_dropped_line(window, job_name: str = "") -> str:
    """The one sentence the owner reads when a held window was given up: which
    job, which days. Empty for any other window."""
    span = hold_dropped_span(window)
    if span is None:
        return ""
    n = span["days"]
    who = str(job_name or (window or {}).get("job_id") or "A job")
    dates = (span["from"] if n == 1 else f"{span['from']} to {span['to']}")
    return (f"{who} could not finish what it held back: {n} "
            f"day{'s' if n != 1 else ''} ({dates}) "
            f"{'were' if n != 1 else 'was'} dropped and will not be read again.")


def window_from_cursor(job_id: str, cursor, *,
                       now: Optional[_dt.datetime] = None) -> dict:
    """`maintenance_window` for a leg that keeps its OWN cursor (the sent
    and chat reconcile legs): the cursor is the basis instead of the
    receipt, under the same bound and the same reasons. `cursor` is an ISO
    string or datetime, aware or naive; None, or anything unparseable, is a
    first run. Reads nothing."""
    now = _parse_iso_local(now) or _now_local()
    basis = _parse_iso_local(cursor)
    return _window_dict(job_id, basis, now=now, last_receipt=basis)


def _page_days(value, fallback: float = PAGE_FIRST_DAYS) -> float:
    return min(PAGE_MAX_DAYS, max(PAGE_MIN_DAYS, _coerce_number(value, fallback)))


def paged_window(workspace_root, job_id: str, *, page_days=None,
                 now: Optional[_dt.datetime] = None,
                 newest_first: bool = False) -> dict:
    """`maintenance_window`, plus the ONE page of it this fire reads
    (cr1#948, cr1#941). The shared answer to a 30-day first run (or a long
    gap) that one background fire cannot read.

    Returns the `maintenance_window` dict unchanged, with one key added,
    `PAGE_FIELD` ("window_page"):
      since / until              machine-local naive ISO: the page. `since`
                                 is the window's own start; `until` is
                                 `since` plus the page span, or the
                                 window's end when that comes first
      since_aware / until_aware  the same instants with the machine's UTC
                                 offset: what the connector query takes
      days                       the page span actually read, in days
      span_days                  the span this page was sized at
      last                       True when the page reaches the window's
                                 end (the present)
      after_short                True when the previous page's read stopped
                                 short (its span is kept, not doubled)

    The page span: `page_days` when the caller names one, else sized from
    the job's previous page on its newest window-moving receipt
    (`next_days`), else `PAGE_FIRST_DAYS`; always inside `PAGE_MIN_DAYS` to
    `PAGE_MAX_DAYS`. A window shorter than the span is one page, `last`
    True, read exactly as before: a steady-state fire is not narrowed.

    The fire reads ONLY `[since, until]` of the page, every page of the
    connector inside it, then hands the window to `page_receipt_fields`,
    whose answer goes on the job's receipt. That is the whole contract; a
    job adopts paging by calling these two and changing nothing else. The
    receipt must be one whose status moves the window
    (`receipt_moves_window`: complete, degraded, or none), or its marker is
    never read. Reads only; never raises (a failed read of the previous
    page sizes this one at `PAGE_FIRST_DAYS`).

    `newest_first` (cr1#978): the page is chosen newest first instead
    (`_newest_first_page`, see `PAGE_ORDER_NEWEST_FIRST`), and it carries
    `order`, `part`, `head_until` and `backlog_until` besides the keys
    above. A window shorter than the span is still one page, read whole."""
    now_dt = _parse_iso_local(now) or _now_local()
    out = maintenance_window(workspace_root, job_id, now=now_dt)
    prev = None
    try:
        prev = last_successful_point(workspace_root, job_id).get("page")
    except Exception:  # noqa: BLE001 - a page is sized best-effort
        prev = None
    if page_days is not None:
        span = _page_days(page_days)
    elif isinstance(prev, dict):
        span = _page_days(prev.get("next_days"))
    else:
        span = PAGE_FIRST_DAYS
    since = _parse_iso_local(out.get("since")) or now_dt
    end = _parse_iso_local(out.get("until")) or now_dt
    if newest_first:
        out[PAGE_FIELD] = _newest_first_page(since, end, span, prev)
        return out
    until = min(end, since + _dt.timedelta(days=span))
    out[PAGE_FIELD] = _page_dict(since, until, end, span, prev)
    return out


def _page_dict(since: _dt.datetime, until: _dt.datetime, end: _dt.datetime,
               span: float, prev) -> dict:
    """The page keys every paged job reads (`paged_window`'s docstring)."""
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "since_aware": to_connector_iso(since),
        "until_aware": to_connector_iso(until),
        "days": round(max(0.0, (until - since).total_seconds()) / 86400.0, 6),
        "span_days": span,
        "last": until >= end,
        "after_short": bool(isinstance(prev, dict)
                            and prev.get("read") == "short"),
    }


def _newest_first_parts(window_since: _dt.datetime, end: _dt.datetime,
                        prev) -> Optional[tuple]:
    """`(head_until, backlog_until)` from the previous newest-first page,
    when it still describes this window, else None (the window is read as
    if fresh: newest page first). It describes this window only when the
    backlog it held is still owed from the window's start (`window_since`
    before `backlog_until`, so the carry-over marker it wrote is the one
    this window opened at) and its head sits between the backlog's end and
    now. A page from the oldest-first pager, a gap that reset the window,
    or anything unreadable is None: the safe direction, which re-reads, and
    never skips."""
    if not isinstance(prev, dict) or prev.get("order") != PAGE_ORDER_NEWEST_FIRST:
        return None
    head = _parse_iso_local(prev.get("head_until"))
    backlog = _parse_iso_local(prev.get("backlog_until"))
    if head is None or backlog is None:
        return None
    if not (window_since < backlog <= head <= end):
        return None
    return head, backlog


def _newest_first_page(since: _dt.datetime, end: _dt.datetime, span: float,
                       prev) -> dict:
    """The ONE page a newest-first pass reads (cr1#978).

    `since` is the window's start: the oldest point still owed (the
    carry-over marker, or the 30-day bound). Three cases:
      the window fits one page          -> the whole window, `last` True,
                                           no backlog: steady state, as
                                           before;
      the previous pass read the head,  -> the BACKLOG's oldest page,
        and a backlog is still owed        `since` to `since` plus the span
                                           or the backlog's end;
      otherwise (a first run, a gap, a  -> the HEAD: from where the last
        backlog pass, the old pager)       head read reached to now, or the
                                           newest page when that is longer
                                           than a page. Everything older is
                                           the backlog, held by the marker.
    A head longer than a page folds what lay between the old backlog and
    the old head into the backlog: read again later, never skipped.

    Review of cr1#978: the backlog's turn comes only after a head read that
    was WHOLE. A head the batch cap or the time fence stopped, or whose
    read stopped short, did not move, and the next pass reads the head
    again, so today's meetings are drained before any older day is read."""
    span_td = _dt.timedelta(days=span)
    parts = _newest_first_parts(since, end, prev)
    if (parts is not None and prev.get("part") == PAGE_PART_HEAD
            and prev.get("read") == "whole"):
        head_until, backlog_until = parts
        until = min(backlog_until, since + span_td)
        page = _page_dict(since, until, end, span, prev)
        page.update(order=PAGE_ORDER_NEWEST_FIRST, part=PAGE_PART_BACKLOG,
                    last=False, head_until=to_connector_iso(head_until),
                    backlog_until=to_connector_iso(backlog_until))
        return page
    head_since, backlog_until = since, None
    if parts is not None:
        head_since, backlog_until = parts[0], parts[1]
    if end - head_since > span_td:
        head_since = max(since, end - span_td)
        backlog_until = head_since
    if backlog_until is not None and backlog_until <= since:
        backlog_until = None
    page = _page_dict(head_since, end, end, span, prev)
    page.update(order=PAGE_ORDER_NEWEST_FIRST, part=PAGE_PART_HEAD,
                last=backlog_until is None and head_since <= since,
                head_until=to_connector_iso(end),
                backlog_until=(to_connector_iso(backlog_until)
                               if backlog_until is not None else None))
    return page


def page_receipt_fields(window, *, read_complete=None,
                        oldest_unhandled=None) -> dict:
    """What a paged job's receipt carries about its page (cr1#948, cr1#941):
    the writer half of `paged_window`. Merge the answer into the receipt's
    data as it stands.

    Args:
      window: the dict `paged_window` returned for THIS fire (a dict with no
        page is read as one page that is the whole window).
      read_complete: the connector read's own word (`read_reached_end`):
        False when the read stopped before the end of the page (more pages
        remained unread). None or True is a read that reached the end.
      oldest_unhandled: where an unhandled tail of the page begins, when the
        job can name one (a batch cap or a time fence over items sorted
        oldest first: the meeting capture can). It is IGNORED when
        `read_complete` is False: a short read on a newest-first connector
        left the OLDER part unlisted, so only the page's start is honest
        there, and that is the marker (review F1).

    Returns:
      `WINDOW_INCOMPLETE_FIELD`  present when the window is not finished:
                                 the page's start when the read stopped
                                 short, else the oldest unhandled point when
                                 one was named, else the page's end (the
                                 page was read and later pages remain).
                                 Absent once the read reached the present.
      `PAGE_FIELD`               the page as read, with `read` ("whole",
                                 "short" or "capped") and `next_days`, the
                                 next page's span
      read_partial / partial_line  present only when the window is not
                                 finished: True, and one plain sentence that
                                 says the read was partial and continues next
                                 run. A partial read is never a connector
                                 that could not be reached.
    """
    win = window if isinstance(window, dict) else {}
    page = win.get(PAGE_FIELD)
    if not isinstance(page, dict):
        page = {"since": win.get("since"), "until": win.get("until"),
                "span_days": win.get("days"), "last": True,
                "after_short": False}
    if page.get("order") == PAGE_ORDER_NEWEST_FIRST:
        return _newest_first_receipt_fields(win, page, read_complete,
                                            oldest_unhandled)
    span = _page_days(page.get("span_days"))
    short = read_reached_end(read_complete) is False
    capped = (not short) and _parse_iso_local(oldest_unhandled) is not None
    bounds = {"start": page.get("since"), "end": page.get("until")}
    if short:
        # Review F1: a read that stopped short holds the page's START,
        # whatever `oldest_unhandled` says. A newest-first connector (the
        # meetings search too) lists the newest items first, so an item the
        # job processed or capped after a short listing says nothing about
        # the older items that were never listed. Resuming past the page's
        # start would drop them for good.
        marker = receipt_window_marker(bounds, incomplete=True,
                                       oldest_unhandled=page.get("since"))
        # Review F3: halve what was actually read, not the sized span: after
        # a quiet stretch the span can be 30 days while the page is hours.
        read_days = _coerce_number(page.get("days"), span)
        next_days = _page_days(min(span, read_days) / 2.0)
        read = "short"
    elif capped:
        marker = receipt_window_marker(bounds, incomplete=True,
                                       oldest_unhandled=oldest_unhandled)
        read, next_days = "capped", span
    else:
        marker = (None if page.get("last") else
                  receipt_window_marker(bounds, incomplete=True,
                                        oldest_unhandled=page.get("until")))
        read = "whole"
        next_days = span if page.get("after_short") else _page_days(span * 2)
    rec = {k: page.get(k) for k in ("since", "until", "days", "span_days",
                                    "last")}
    rec.update(read=read, next_days=next_days)
    out = {PAGE_FIELD: rec}
    if marker is None and (short or capped or not page.get("last")):
        # No parseable page: nothing honest to resume from; the window's own
        # start is re-read next fire (the receipt still says partial).
        out["read_partial"] = True
    if marker is not None:
        out[WINDOW_INCOMPLETE_FIELD] = marker
        out["read_partial"] = True
    if out.get("read_partial"):
        out["partial_line"] = page_partial_line(rec)
    return out


#: The order a pass takes the items of its page in (cr1#978 review): the
#: values of `page_item_order`, and of the `meeting_order` key the meeting
#: capture's window carries for the phases to read.
ITEM_ORDER_OLDEST_FIRST = "oldest_first"
ITEM_ORDER_NEWEST_FIRST = "newest_first"


def page_item_order(page) -> str:
    """The order a pass takes its page's items in: newest first on a
    newest-first HEAD page, so today's meetings come before the cap or the
    fence can stop the pass; oldest first everywhere else (a backlog page,
    any oldest-first page, no page), so the cap marker stays the oldest item
    not handled, as the batch cap has always written it."""
    if (isinstance(page, dict) and page.get("order") == PAGE_ORDER_NEWEST_FIRST
            and page.get("part") == PAGE_PART_HEAD):
        return ITEM_ORDER_NEWEST_FIRST
    return ITEM_ORDER_OLDEST_FIRST


def _newest_first_receipt_fields(win: dict, page: dict, read_complete,
                                 oldest_unhandled) -> dict:
    """`page_receipt_fields` for a newest-first page (cr1#978): how far the
    pass got moves only the part it read, and the marker stays the oldest
    point still owed.

      read short (`read_complete` false) -> that part does not move: a
        short, newest-first listing never saw its older items (review F1),
        and the next page is half what was read;
      capped (a marker handed: the batch cap or the time fence stopped the
        pass) -> a BACKLOG page, whose items are taken oldest first, moves
        to the first item not handled and no further; the HEAD, whose items
        are taken NEWEST first (`page_item_order`), does not move at all
        (review of cr1#978): what the cap left is older than what it
        handled, and the next pass reads the head again, so the marker's
        position is never trusted to split it;
      read whole -> that part moves to the page's end.

    The head moves `head_until`; a backlog page moves the backlog's start,
    which IS the marker. Then: a backlog still owed -> the marker at its
    start; none owed and the head short of the present -> the marker at the
    head (a later head read resumes there); neither -> no marker, the
    window reached the present."""
    span = _page_days(page.get("span_days"))
    short = read_reached_end(read_complete) is False
    p_since = _parse_iso_local(page.get("since"))
    p_until = _parse_iso_local(page.get("until"))
    end = _parse_iso_local(win.get("until")) or p_until
    capped_at = None if short else _parse_iso_local(oldest_unhandled)
    if capped_at is not None and p_since is not None and p_until is not None:
        capped_at = min(max(capped_at, p_since), p_until)
    if short:
        reached, read = p_since, "short"
        read_days = _coerce_number(page.get("days"), span)
        next_days = _page_days(min(span, read_days) / 2.0)
    elif capped_at is not None:
        reached = (capped_at if page.get("part") == PAGE_PART_BACKLOG
                   else p_since)
        read, next_days = "capped", span
    else:
        reached, read = p_until, "whole"
        # Only a full page read whole says the next one may be bigger: the
        # head is often a few hours, and doubling on it would hand the
        # backlog a page far past what any pass has shown it can read.
        full = _coerce_number(page.get("days"), 0.0) >= span * 0.999
        next_days = (_page_days(span * 2)
                     if full and not page.get("after_short") else span)
    backlog_until = _parse_iso_local(page.get("backlog_until"))
    if page.get("part") == PAGE_PART_BACKLOG:
        head_until = _parse_iso_local(page.get("head_until"))
        owed_from = reached or _parse_iso_local(win.get("since"))
    else:
        head_until = reached
        owed_from = _parse_iso_local(win.get("since")) or p_since
    if (backlog_until is not None and owed_from is not None
            and owed_from >= backlog_until):
        backlog_until = None  # the backlog is drained
    if backlog_until is not None:
        oldest = owed_from
    elif head_until is not None and end is not None and head_until < end:
        oldest = head_until
    else:
        oldest = None
    marker = None
    if oldest is not None:
        marker = receipt_window_marker(
            {"start": win.get("since") or page.get("since"),
             "end": win.get("until") or page.get("until")},
            incomplete=True, oldest_unhandled=oldest.isoformat())
    rec = {k: page.get(k) for k in ("since", "until", "days", "span_days")}
    rec.update(
        last=marker is None, read=read, next_days=next_days,
        order=PAGE_ORDER_NEWEST_FIRST, part=page.get("part"),
        head_until=(to_connector_iso(head_until)
                    if head_until is not None else None),
        backlog_until=(to_connector_iso(backlog_until)
                       if backlog_until is not None else None))
    out = {PAGE_FIELD: rec}
    if marker is not None or oldest is not None:
        out["read_partial"] = True
        out["partial_line"] = page_partial_line(rec)
    if marker is not None:
        out[WINDOW_INCOMPLETE_FIELD] = marker
    return out


def page_partial_line(page) -> str:
    """The one plain sentence a partial paged read says on its receipt: how
    far it read, and that the next run carries on from there. Empty for a
    read that reached the present."""
    rec = page if isinstance(page, dict) else {}
    if rec.get("read") == "whole" and rec.get("last"):
        return ""
    if rec.get("order") == PAGE_ORDER_NEWEST_FIRST:
        return _newest_first_line(rec)
    since = _parse_iso_local(rec.get("since"))
    until = _parse_iso_local(rec.get("until"))
    if since is None or until is None:
        return ("Read part of the window; the next run carries on from "
                "where this one stopped.")
    span = f"{since.date().isoformat()} to {until.date().isoformat()}"
    if rec.get("read") == "short":
        return (f"Read part of {span} before stopping; the next run reads "
                f"that span again in a smaller piece.")
    if rec.get("read") == "capped":
        return (f"Read {span} but handled only part of it; the next run "
                f"carries on from the first item not handled.")
    return (f"Caught up through {until.date().isoformat()} ({span} this "
            f"run); the next run carries on from there until it reaches today.")


def _newest_first_line(rec: dict) -> str:
    """`page_partial_line` for a newest-first page (cr1#978): what this pass
    read, and what the later runs still read. Plain words, dates only."""
    since = _parse_iso_local(rec.get("since"))
    until = _parse_iso_local(rec.get("until"))
    if since is None or until is None:
        return ("Read part of the window; a later run carries on from where "
                "this one stopped.")
    span = f"{since.date().isoformat()} to {until.date().isoformat()}"
    if rec.get("read") == "short":
        return (f"Read part of {span} before stopping; a later run reads it "
                f"again in a smaller piece.")
    if rec.get("read") == "capped":
        if rec.get("part") != PAGE_PART_BACKLOG:
            return (f"Read {span} newest first but handled only part of it; "
                    f"the next run reads it again before any older day.")
        return (f"Read {span} but handled only part of it; a later run "
                f"carries on from the first item not handled.")
    backlog = _parse_iso_local(rec.get("backlog_until"))
    if rec.get("part") == PAGE_PART_BACKLOG:
        if backlog is None:
            return (f"Caught up on the older items through "
                    f"{until.date().isoformat()}; the next run reads the "
                    f"newest ones.")
        return (f"Read older items, {span}; later runs carry on with the rest "
                f"up to {backlog.date().isoformat()} and keep reading the "
                f"newest ones in between.")
    if backlog is not None:
        return (f"Read the newest items first, {span}; the older ones up to "
                f"{backlog.date().isoformat()} are read a few days at a time "
                f"on later runs.")
    return (f"Read {span}; the next run carries on from there until it "
            f"reaches today.")


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
    parser.add_argument("--triggered-by", default=None,
                        help="the surface that asked for this run")
    parser.add_argument("--fired-via", default=None,
                        help="scheduled | catchup | manual (this fire's run mode)")
    parser.add_argument("--scheduled-only", action="store_true",
                        help="apply catch-up on scheduled-context fires only")
    args = parser.parse_args(argv)
    # FIX3 F3-6: export what this run was asked by, so every composer
    # below reads it from one place instead of being threaded through
    # a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)

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
    "MAINTENANCE_WINDOW_DAYS",
    "MAINTENANCE_WINDOW_FIELD",
    "MAINTENANCE_WINDOWS_FIELD",
    "WINDOW_REASON_CURSOR",
    "WINDOW_REASON_FIRST_RUN",
    "WINDOW_MOVING_STATUSES",
    "WINDOW_READ_FIELD",
    "PAGE_FIELD",
    "PAGE_FIRST_DAYS",
    "PAGE_MIN_DAYS",
    "PAGE_MAX_DAYS",
    "PAGE_ORDER_NEWEST_FIRST",
    "PAGE_PART_HEAD",
    "PAGE_PART_BACKLOG",
    "ITEM_ORDER_OLDEST_FIRST",
    "ITEM_ORDER_NEWEST_FIRST",
    "page_item_order",
    "paged_window",
    "page_receipt_fields",
    "page_partial_line",
    "read_reached_end",
    "receipt_moves_window",
    "catchup_window",
    "gap_capped_line",
    "gap_unread_span",
    "hold_dropped_line",
    "hold_dropped_span",
    "maintenance_window",
    "window_from_cursor",
    "missed_periods",
    "last_successful_point",
    "receipt_window_marker",
    "to_connector_iso",
]


if __name__ == "__main__":
    sys.exit(main())
