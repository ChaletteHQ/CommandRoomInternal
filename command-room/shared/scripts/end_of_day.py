#!/usr/bin/env python3
"""
end_of_day — the evening bookend's read half (SPEC EOD1).

WHY THIS EXISTS
===============
The 5 PM chat used to be a transcript-processing job that handed the CEO a
pile of meeting rows to adjudicate. SPEC EOD1 turns it into the day's CLOSE:
what the day discharged, what it did not, what tomorrow is about. One chat,
not two; the day-close should make the open book SMALLER most days; and what
End of Day reads it writes to memory with a resolvable pointer, so the daily
close IS the daily memory commit.

This module owns the READ. It computes the seven blocks, resolves the receipt
map one-tap actions run against, and holds the WORDS the surface is allowed to
say. It performs no connector I/O: the orchestrator fetches, this assembles.

THE ONE SENTENCE THAT MATTERS
-----------------------------
**"Not recorded", never "not done".** The absence of a close event is not
evidence that the work did not happen. It is evidence that nothing wrote it
down — which is a statement about this product, not about the CEO's day. A
surface that says "not done" over a missing record is a surface the CEO
argues with, and a surface the CEO argues with stops being read. Every code
path in here that reaches for the second phrasing has to reach through a
constant that does not contain it.

The same law, applied one level up: a MISSING morning receipt is not a zero
score. It is `NO_PLAN_LINE` — "No plan on record this morning." A guessed
score is worse than no score, because a guess is indistinguishable from a
measurement once it is on screen (the F-29 receipts doctrine).

And once more at the week level (the Monday roll-up): a day whose fire never
ran renders `NO_CLOSE_RECORDED`, never a zero. Zero is a measurement.

WHAT THIS MODULE DOES NOT DO
----------------------------
  - It does not FETCH. Calendar, mail and chat arrive as arguments; a leg with
    no connector is skipped and receipted by the caller (per-capability asks).
  - It does not WRITE closes. Every close the fire performs goes through
    `commitment_state.close_commitment` WITH its `source_ref` (PROV1); this
    module only reads what those writers left.
  - It does not log a `brief_state` audit event. `compute_brief_state` is
    called PURE here, deliberately: `brief_receipt.orphan_brief_finding` reads
    the NEWEST `brief_state` of any origin and expects a morning-brief
    `pack_run` after it, so an evening fire logging one would make every
    evening look like a morning brief that lost its receipt, and would
    stale-refuse the next morning's `mark done [n]`. One audit event per
    morning fire, and the evening borrows the derivation without the write.
  - It does not decide the capture flip. That is `held_tier.py`, and it is
    DARK: see that module and this fire's skill prose, which cites
    `operator_capability` because a fence no instruction layer names is a fence
    that is never consulted (the instruction-layer-gap finding, REVIEW_PREC1
    N-1). The flip is an operator grant shipped in the payload, never a
    measurement this workspace takes of itself (HELDOP1).

Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

# The surface name (pack + receipt vocabulary).
SURFACE = "end-of-day"

# The skill folder whose config carries this fire's knobs.
SKILL_NAME = "end-of-day"

# THE taskId this fire serves. EOD1 upgrades the EXISTING 5 PM weekday chat
# rather than minting a task: the registered prompt on every live machine
# names `past-meetings` and loads its steps fresh from the installed plugin at
# fire time, so upgrading the orchestrator it already reads is the only change
# that reaches a machine without a re-registration.
#
# EOD2 minted the `end-of-day` REGISTRY id (DEFAULT_SCHEDULES /
# FIRST_INSTALL_TASK_IDS / DISPLAY_NAMES / ORCHESTRATOR_MAP) and proposes the
# rename — and deliberately left THIS constant alone. The registry id and the
# RECEIPT id are two different things, and only the first needed to move:
#
#   * Both ids map to the same orchestrator file, so both fire this same pack.
#   * The rename is propose-only and per-machine, so at any moment some
#     workspaces fire `past-meetings` and some fire `end-of-day`. Writing the
#     receipt under whichever id happened to fire would split one day-close
#     history into two half-series either side of the day a given customer
#     took the offer — and the week roll-up, the score, `catchup_window` and
#     `late_fire` all read that series.
#   * So the series stays `past-meetings`, forever, and the successor reads
#     through it: `receipts.TASK_PREDECESSORS["end-of-day"] = ("past-meetings",)`.
#     Continuous in both directions, no migration, nothing to backfill.
TASK_ID = "past-meetings"

# The morning fire this evening scores against.
MORNING_TASK_ID = "morning-brief"

RECEIPT_EVENT = "pack_run"

# SPEC EODSPEED1 §4 — the stated budget: a close on a day whose captures are
# current lands within FIVE MINUTES of the slot, at full checking depth. The
# budget is met by moving work earlier (the incremental capture pass), never
# by trimming a check — nothing anywhere reads this constant to skip work.
# The receipt stamps `close_budget` (see log_end_of_day_receipt) so the
# EODPHASE1 phase records plus this one verdict are the before/after
# instrument on M's own fires.
CLOSE_BUDGET_MS = 5 * 60 * 1000


def close_budget_read(duration_ms) -> Optional[dict]:
    """The budget verdict the receipt carries: `{"budget_ms", "duration_ms",
    "within_budget"}`, or None when the fire did not report a duration —
    absent, never a fabricated pass (the MC2 rule: "not measured" and
    "within budget" are different claims)."""
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) \
            or duration_ms < 0:
        return None
    return {"budget_ms": CLOSE_BUDGET_MS,
            "duration_ms": duration_ms,
            "within_budget": duration_ms <= CLOSE_BUDGET_MS}


# ---------------------------------------------------------------------------
# SPEC CAPFENCE1 — the capture leg's own stopping rule (2026-08-27)
# ---------------------------------------------------------------------------
#
# §0 Ruling 1, RE-ASSERTED: CLOSE_BUDGET_MS above does not move and gains no
# control-flow reader here or anywhere else in this module. It stays the
# standing 5-minute promise the fire keeps failing honestly until a real
# close passes it — widening it to fit a failing fire is fixing the
# thermometer, and nothing below does that. `CAPTURE_FENCE_MS` is a SEPARATE,
# NEW constant with a narrower job: a stopping rule for the close's own
# capture leg (Phase D) only. Nothing reads CLOSE_BUDGET_MS to decide whether
# to run this check, skip the substance floor, or trim a gate — see
# `run_capfence1_test.py`'s source-scan pin, which greps this file for every
# occurrence of the name and fails if a new one appears outside
# `close_budget_read`.
#
# Ramp M-ruled: 15 minutes now, walked down by a LATER ruling once CAPSLOT1's
# own receipts show the fence rarely binding — never silently, and never by
# this constant moving on its own.
CAPTURE_FENCE_MS = 15 * 60 * 1000  # 15 minutes


def capture_fence_elapsed_ms(capture_leg_start, now=None) -> Optional[int]:
    """Milliseconds since the capture leg's own start — LEDGER-ELAPSED, the
    SAME `capture_leg_start` EODLEG1 already records at the top of Phase D,
    never a second timestamp invented for this check alone.

    `now` is the instant to measure against; omitted, this reads the actual
    current UTC time. A caller replaying a fixture passes a fixed ISO string
    instead of monkeypatching the clock.

    Returns None — never a fabricated elapsed — when either timestamp is
    unparseable, or when the computed delta is negative (a clock that moved
    backwards mid-fire is not a real elapsed reading; the 5 PM fire often
    runs on a machine that just woke up, the same condition CLOCK1 and
    `PhaseLedger`'s own `monotonic` note both guard against one level down).
    `capture_fence_should_defer` treats a None elapsed as "the fence has not
    bound" — an instrumentation read that cannot be trusted must not stop a
    fire's capture leg (§0 Ruling 4's own posture, `PhaseLedger.record_leg`'s
    posture one level up: instrumentation never costs the fire its work).
    """
    start = _parse_iso(capture_leg_start)
    end = (_parse_iso(now) if now is not None
           else _dt.datetime.now(_dt.timezone.utc))
    if start is None or end is None:
        return None
    delta_ms = (end - start).total_seconds() * 1000.0
    if delta_ms < 0:
        return None
    return int(round(delta_ms))


def capture_fence_should_defer(elapsed_ms, n_captured_full_depth) -> bool:
    """Whether Phase D's between-meetings check should stop and defer every
    meeting still left in this fire's oldest-first set (§0 Rulings 2 and 3).

    `elapsed_ms` is `capture_fence_elapsed_ms`'s own reading. `n_captured_
    full_depth` is how many meetings THIS fire has already finished Phase 4
    steps 1-9 for — full checking depth, §0 Ruling 4 — before this check
    runs.

    THE SUBSTANCE FLOOR (Ruling 3), and it is not a knob: this function
    takes no floor argument, so a future call site cannot widen it by
    passing a bigger number. Below one captured meeting the fence is
    completely inert regardless of `elapsed_ms` — an empty evening on a day
    that had meetings is worse than a long one, so the fire always finishes
    at least its FIRST meeting even if the fence was already crossed before
    that meeting started (a slow transcript fetch, a cold connector). Once
    one meeting has cleared full depth, the fence is live: the very next
    check after `elapsed_ms` reaches `CAPTURE_FENCE_MS` defers everything
    still queued.
    """
    if not isinstance(n_captured_full_depth, int) \
            or isinstance(n_captured_full_depth, bool) \
            or n_captured_full_depth < 1:
        return False
    if not isinstance(elapsed_ms, (int, float)) \
            or isinstance(elapsed_ms, bool):
        return False
    if elapsed_ms != elapsed_ms:  # NaN != NaN
        return False
    return elapsed_ms >= CAPTURE_FENCE_MS


def capture_fence_window_marker(window, oldest_deferred_start) -> Optional[str]:
    """The resume marker for a FENCE-triggered defer — the SAME
    `window_incomplete_before` value the batch cap already writes
    (`catchup.receipt_window_marker`), named for this spec's own call site
    so a fence-triggered defer never grows a second dialect of the batch
    cap's own honesty gate (§0 Ruling 2: "the EXISTING receipt_window_marker
    /window_incomplete_before machinery").

    `oldest_deferred_start` is the earliest start time among the meetings
    THIS check is about to defer — the same "oldest still-unprocessed"
    reading the batch cap already computes, just handed in from the fence's
    own stopping point rather than the cap's. If Phase 3's batch cap ALSO
    left meetings unhandled beyond its 5-meeting count, this still resolves
    to the correct resume point: `receipt_window_marker` clamps to the
    oldest unhandled start regardless of WHICH mechanism made it unhandled,
    so the two triggers never produce two different resume points for the
    same window.

    Call this ONLY when there is something to defer (`capture_fence_
    should_defer` returned True and at least one meeting remains in the
    oldest-first set) — it always passes `incomplete=True` and never returns
    None for a real remaining set.
    """
    from catchup import receipt_window_marker
    return receipt_window_marker(window, incomplete=True,
                                 oldest_unhandled=oldest_deferred_start)


# ---------------------------------------------------------------------------
# Per-phase instrumentation (SPEC EODPHASE1)
# ---------------------------------------------------------------------------
#
# The fire recorded ONE `duration_ms` for the whole thing. Live durations
# climbed roughly 8 -> 19 minutes across two days and nothing on the receipt
# said which phase paid, so any optimisation would have been guesswork. This
# instruments FIRST; the fix is a later spec, written off real receipts.
#
# The phase NAMES are a vocabulary, not labels — the suite pins the set, and
# anything reading these receipts joins on them. Renaming one silently orphans
# every prior fire's number for that phase, which is the whole class of bug
# that makes a measurement quietly read as zero. Add a name here, in order,
# rather than spelling one at a call site.
PHASE_ALARMS = "alarms"
PHASE_BRIEF_STATE = "brief_state"
PHASE_MORNING_READ = "morning_read"
PHASE_CLOSURES = "closures"
PHASE_LEDGER = "ledger"
PHASE_SCORE = "score"
PHASE_WINS = "wins"
PHASE_SLIPPED = "slipped"
PHASE_CONFIRM = "confirm"
PHASE_TOMORROW = "tomorrow"
PHASE_SIGN_OFF = "sign_off"
PHASE_COVERAGE = "coverage"
PHASE_CATCHUP = "catchup"
PHASE_WEEK_ROLLUP = "week_rollup"
# SPEC EODSYNTH1 — the synthesis composition. A phase of its own because it is
# the one leg whose cost scales with how much the day DID rather than with how
# much the workspace holds, and because it is the leg a reader will suspect
# first the day the evening gets slow.
PHASE_SYNTHESIS = "synthesis"
PHASE_RENDER = "render"

# The phases `build_end_of_day_pack` itself runs, in execution order.
# `week_rollup` is Monday-only, so it is in the vocabulary and absent from
# most fires' receipts — absent, never zero (the MC2 rule): "this phase did
# not run today" and "this phase took no time" are different claims.
PACK_PHASES = (
    PHASE_ALARMS, PHASE_BRIEF_STATE, PHASE_MORNING_READ, PHASE_CLOSURES,
    PHASE_LEDGER, PHASE_SCORE, PHASE_WINS, PHASE_SLIPPED, PHASE_CONFIRM,
    PHASE_TOMORROW, PHASE_SIGN_OFF, PHASE_COVERAGE, PHASE_CATCHUP,
    PHASE_WEEK_ROLLUP, PHASE_SYNTHESIS, PHASE_RENDER,
)

# Legs the ORCHESTRATOR runs around the pack build — the capture leg and the
# title-match close leg both happen outside this driver, and a driver cannot
# time work it does not perform. They are named here so an orchestrator that
# wants to contribute times has one vocabulary to use rather than inventing a
# second, and so a reader knows the pack's phases do NOT sum to the fire.
PHASE_CAPTURE = "capture_leg"
PHASE_CLOSE = "close_leg"
PHASE_POST = "post"
LEG_PHASES = (PHASE_CAPTURE, PHASE_CLOSE, PHASE_POST)

ALL_PHASES = PACK_PHASES + LEG_PHASES


# ---------------------------------------------------------------------------
# SPEC EODLEG1 — the receipt-shape guard (battery, guard tier)
# ---------------------------------------------------------------------------
#
# EODPHASE1 made PHASE_CAPTURE nameable; it did not make it MANDATORY. The
# live measurement (`Penelopes Brain/_hq/audit-reports/EODSPEED1_live_test_
# 2026-08-26/REPORT.md`, receipt `eod_20260827T003743Z-48e3f23f`) found
# `phase_order` covering the pack build alone — 0.45% of a 1,855,666 ms fire
# that captured four meetings — on the first `close_budget` verdict ever
# stamped. §0 rulings 1 and 3 make the ledger MANDATORY in the orchestrator
# CONTRACT, but DEVELOPMENT.md's own architecture invariant is the reason
# this function exists at all: "Prose contracts don't hold; code chokepoints
# do... a prose-only mandate is presumed skipped (Bug #98 class)." An
# orchestrator `.md` is read and executed by a model, not compiled, so the
# battery cannot run it — this predicate is the code half: a receipt shaped
# like the regression is a defect the guard tier can name BY ITSELF, so the
# next orchestrator edit that quietly drops the ledger threading is caught
# here rather than inferred from file mtimes a second time.
def receipt_missing_capture_phase(data: dict) -> bool:
    """True when a `past-meetings`/`end-of-day` `pack_run` receipt's own
    `data` claims work the phase vocabulary does not corroborate: it
    processed at least one meeting (`data["n_processed"]`, the EODSPEED1
    `capture_leg` whitelist key `log_end_of_day_receipt` flattens onto the
    receipt) and `phase_order` — EODPHASE1's own execution-order record —
    does not contain `PHASE_CAPTURE`.

    A fire that captured nothing (`n_processed` 0, absent, or not a plain
    int) is NEVER flagged: an untimed leg that did no work is not the
    defect this guards against, and flagging it would make the guard noisy
    on the ordinary case where a day's meetings were all captured
    incrementally and this fire's own `n_processed` is 0 by design
    (EODSPEED1). Only a receipt that says it did the work and cannot show
    when is the shape this refuses.
    """
    if not isinstance(data, dict):
        return False
    n_processed = data.get("n_processed")
    if not isinstance(n_processed, int) or isinstance(n_processed, bool) \
            or n_processed <= 0:
        return False
    order = data.get("phase_order")
    if not isinstance(order, list):
        return True
    return PHASE_CAPTURE not in order


def scan_capture_phase_violations(workspace_root) -> list:
    """Every `past-meetings`/`end-of-day` `pack_run` receipt on this
    workspace that fails `receipt_missing_capture_phase`, oldest first —
    the guard's "red BY NAME" half.

    Read-only and best-effort: a workspace this cannot read returns `[]`,
    never a raise. Returns
    `[{"ts": iso|None, "task_id": str|None, "n_processed": int}, ...]`.
    """
    out: list = []
    try:
        from receipts import iter_receipts
        rows = iter_receipts(workspace_root, task_ids=[TASK_ID, "end-of-day"])
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        if r.get("type") != RECEIPT_EVENT:
            continue
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        if not receipt_missing_capture_phase(data):
            continue
        dt = r.get("dt")
        out.append({"ts": dt.isoformat() if dt else None,
                    "task_id": r.get("task_id"),
                    "n_processed": data.get("n_processed")})
    return out


class PhaseLedger:
    """Wall time and row counts per phase of one fire.

    Usage — the whole surface is the context manager:

        ledger = PhaseLedger()
        with ledger.phase(PHASE_WINS) as p:
            wins = compute_wins(...)
            p.count(out=len(wins["rows"]))

    Three properties, each of which is why this is a class and not a dict of
    `time.monotonic()` calls at the call sites:

    **A raising phase is still recorded.** The timer stops in a `finally` and
    the phase is stamped `failed`, then the exception propagates untouched. A
    19-minute fire that dies in its slowest phase is exactly the fire whose
    diagnostics matter most, and the pre-instrumentation failure mode — a
    crash erasing the numbers that would explain it — is the one this must not
    reproduce. Nothing here swallows anything: a caller that wants the partial
    ledger holds it BEFORE the call and reads it after the raise, which is why
    `build_end_of_day_pack` takes one rather than only returning one.

    **`monotonic`, never the wall clock.** A clock adjustment mid-fire (NTP, a
    DST-adjacent laptop waking up — this fire runs at 5 PM on a machine that
    has often just woken) would otherwise produce a negative or wildly long
    phase and put a fabricated number on a diagnostic receipt.

    **Counts are ABSENT rather than zero when nobody supplied them.** A phase
    that never called `count()` records no counts at all; "we did not measure"
    and "we measured nothing" are different claims and only one of them should
    make a reader go looking.

    Re-entering the same phase name ACCUMULATES time and keeps the first
    `in` / last `out` — a phase run in two chunks is one phase, and the
    alternative (last write wins) would silently discard half of a slow one.
    """

    __slots__ = ("_ms", "_counts", "_failed", "_order")

    def __init__(self):
        self._ms: dict[str, float] = {}
        self._counts: dict[str, dict] = {}
        self._failed: list[str] = []
        self._order: list[str] = []

    class _Phase:
        __slots__ = ("_ledger", "_name")

        def __init__(self, ledger, name):
            self._ledger = ledger
            self._name = name

        def count(self, *, n_in=None, out=None) -> None:
            """Rows entering / leaving this phase. Either may be omitted; a
            key nobody set stays absent."""
            slot = self._ledger._counts.setdefault(self._name, {})
            if isinstance(n_in, int) and not isinstance(n_in, bool):
                slot.setdefault("in", n_in)
            if isinstance(out, int) and not isinstance(out, bool):
                slot["out"] = out

    def phase(self, name: str):
        return _PhaseTimer(self, name)

    def mark_failed(self, name: str) -> None:
        if name not in self._failed:
            self._failed.append(name)

    def add_ms(self, name: str, ms: float) -> None:
        if name not in self._ms:
            self._order.append(name)
            self._ms[name] = 0.0
        self._ms[name] += ms

    def record_leg(self, name: str, ms, *, n_in=None, out=None,
                   failed: bool = False) -> None:
        """Record a leg's wall time from an EXTERNALLY measured duration
        (SPEC EODLEG1) — for a leg that ran in a DIFFERENT process than this
        ledger. `with ledger.phase(...)` only works inside one continuous
        Python process; the orchestrator's close/capture/post legs each run
        in their own `python3 -c` invocation (this fire's own module
        docstring rule: "each is its own process"), so nothing here can hold
        a live monotonic timer open across the boundary — the caller times
        the leg as a wall-clock UTC-ISO delta instead and hands the already-
        computed milliseconds to this method.

        `ms` must be a non-negative real number; anything else (missing,
        unparseable, negative — the shape a failed timestamp computation
        leaves behind) is a silent no-op rather than a corrupt or fabricated
        entry, which is what lets a caller try/except the whole timing block
        and still call this unconditionally (BRIEFFIX1 Item C: instrumentation
        never costs the fire its receipt).
        """
        try:
            ms = float(ms)
        except (TypeError, ValueError):
            return
        if ms < 0 or ms != ms:  # NaN != NaN
            return
        self.add_ms(name, ms)
        if failed:
            self.mark_failed(name)
        if n_in is not None or out is not None:
            self._Phase(self, name).count(n_in=n_in, out=out)

    def merge_snapshot(self, snapshot: Optional[dict]) -> None:
        """Absorb a PRIOR `.snapshot()` dict (SPEC EODLEG1) — e.g. the pack
        build's own `phase_timings`, computed inside a SEPARATE subprocess
        (the Phase C driver) and handed back as JSON rather than as a live
        object this ledger could have held onto. Without this, an explicit
        `phase_ledger` passed to `log_end_of_day_receipt` WINS over the
        pack's own snapshot outright (that function's own docstring: "the
        explicit ledger WINS") — so an orchestrator that built its own
        ledger for the close/capture/post legs and forgot to fold the pack's
        own phase record into it would silently DISCARD the fourteen
        pack-build phases the driver already measured, the exact regression
        this method exists to prevent.

        Order-preserving: the snapshot's own `phase_order` is walked in
        order, so its phases are recorded ahead of anything this ledger
        times afterward. A malformed or empty snapshot is a no-op — the same
        posture every other read in this class takes: an instrumentation
        read that cannot be trusted is skipped, never fatal to the fire it
        is trying to describe.
        """
        if not isinstance(snapshot, dict):
            return
        durations = snapshot.get("phase_durations_ms")
        order = snapshot.get("phase_order")
        if not isinstance(durations, dict) or not isinstance(order, list):
            return
        counts = (snapshot.get("phase_counts")
                 if isinstance(snapshot.get("phase_counts"), dict) else {})
        failed = (snapshot.get("phases_failed")
                 if isinstance(snapshot.get("phases_failed"), list) else [])
        for name in order:
            name = str(name)
            if name not in durations:
                continue
            row_counts = counts.get(name) if isinstance(counts.get(name), dict) else {}
            self.record_leg(name, durations[name],
                            n_in=row_counts.get("in"), out=row_counts.get("out"),
                            failed=name in failed)

    def snapshot(self) -> dict:
        """The additive receipt payload, or `{}` when nothing was timed.

        `{"phase_durations_ms": {...}, "phase_counts": {...},
          "phase_order": [...], "phases_failed": [...]}` — the last two keys
        present only when they carry something. `phase_order` is execution
        order, which a dict's key order would also give but only by accident
        of the JSON round trip; naming it means a reader never has to bet on
        that.
        """
        if not self._ms:
            return {}
        out: dict = {
            "phase_durations_ms": {k: int(round(self._ms[k]))
                                   for k in self._order},
            "phase_order": list(self._order),
        }
        counts = {k: v for k, v in self._counts.items() if v}
        if counts:
            out["phase_counts"] = counts
        if self._failed:
            out["phases_failed"] = list(self._failed)
        return out


class _PhaseTimer:
    """`PhaseLedger.phase()`'s context manager. Separate class so the ledger
    stays picklable/inspectable and so re-entry is obviously supported."""

    __slots__ = ("_ledger", "_name", "_t0", "_handle")

    def __init__(self, ledger: PhaseLedger, name: str):
        self._ledger = ledger
        self._name = name
        self._t0 = None
        self._handle = PhaseLedger._Phase(ledger, name)

    def __enter__(self):
        import time as _time
        self._t0 = _time.monotonic()
        return self._handle

    def __exit__(self, exc_type, exc, tb):
        import time as _time
        if self._t0 is not None:
            self._ledger.add_ms(self._name,
                                (_time.monotonic() - self._t0) * 1000.0)
        if exc_type is not None:
            self._ledger.mark_failed(self._name)
        return False  # never swallow — a degraded fire must still degrade

# The block order IS the render contract (SPEC BK2's table, EOD1 §3).
#
# SPEC EODLEDGER1 inserts `coverage` FIRST, immediately after `alarm_lines`.
# The order is the argument: a reader cannot weigh a number until they know
# what aperture produced it, and every block below coverage is a number. It
# sits under the alarms for the same reason the alarms sit above everything —
# a substrate that is not syncing outranks a statement about what was read.
BLOCK_ORDER = ("alarm_lines", "coverage", "score", "wins", "slipped",
               "confirm", "tomorrow", "sign_off")

# ---------------------------------------------------------------------------
# SPEC EODSYNTH1 — WHAT RENDERS, AND WHAT IS ONLY COMPUTED
# ---------------------------------------------------------------------------
#
# M's ruling (2026-08-23): the evening SYNTHESIZES the day instead of scoring
# it, and asks exactly one thing — tomorrow. So the render order below is no
# longer `BLOCK_ORDER`, and the difference between the two lists is the ruling
# written down:
#
#   `RENDER_ORDER`      what reaches the screen, in this order.
#   `COMPUTED_ONLY`     what still runs, still lands on the `pack_run` receipt,
#                       and renders NOWHERE — chat or widget.
#
# `BLOCK_ORDER` STAYS, and it is not dead. It is the RECEIPT's vocabulary:
# `blocks_rendered` has named these eight since EOD1 and weekly-recap, the
# watchdog and the trend surfaces join on it. Renaming or truncating it would
# rewrite the meaning of every receipt already on disk. UN-RENDER, DON'T
# UNBUILD is the whole of R-1, and this pair of tuples is where that lives:
# `score` is still computed, still carries `n_planned`/`n_closed`, and still
# reaches the receipt through `log_end_of_day_receipt` — it simply has no
# sentence on the surface any more.
#
# Why `wins`, `slipped` and `confirm` are here too, and this is NOT scope
# creep. Each of them was a RENDERED ROW-LIST and each is now an INPUT:
# `wins` and the ledger feed the how-the-day-went paragraph, `slipped` feeds
# the with-consequences prose (and its ask moves to the morning, R-3), and
# `confirm` renders on the morning surfaces where the operator is in triage
# mode. Leaving them in the render order would have been the pile M's ruling
# removes, wearing a new heading.
RENDER_ORDER = ("alarm_lines", "coverage", "day_went", "what_it_meant",
                "worth_remembering", "slipped_prose", "echoes", "tomorrow",
                "sign_off")

# Computed, receipted, NEVER rendered. A block in this tuple that acquires a
# sentence on the surface is the defect EODSYNTH1 exists to remove.
COMPUTED_ONLY = ("score", "wins", "slipped", "confirm")

# The synthesis blocks, in their render order. Spelled from the module that
# composes them so the two can never drift into two orders.
SYNTHESIS_BLOCKS = ("day_went", "what_it_meant", "worth_remembering",
                    "slipped_prose", "echoes")

# Render bounds. A cap is a render bound, never a silence (the :299 doctrine).
MAX_SLIPPED_ROWS = 3
MAX_CONFIRM_ROWS = 5
MAX_WIN_ROWS = 6
MAX_TOMORROW_ROLLOVER = 3

# ---------------------------------------------------------------------------
# THE FATIGUE RULE (SPEC OVERDUE1) — ask once, then let the row rest
# ---------------------------------------------------------------------------
#
# M's ruling R-3, on three items due Aug 6-8 that rendered in the same order in
# this block every night for two weeks: "I would do it for 3-4 days." Past that
# the repetition stops being a reminder and starts being wallpaper, and a
# surface nobody reads has no way back.
#
# So: an item this far past its due date gets ONE direct question with the
# block's existing verbs, and from the next fire it is suppressed from THIS
# BLOCK until the question is answered. It is suppressed nowhere else — it
# stays on the open book, on `my plate`, and in the brief's needs-attention
# lane. Only the nightly nagging stops.
#
# NOT the same problem as `commitment_state.cap_needs_attention`'s rotation
# rule, which exists so that no item can be suppressed FOREVER. That rule is
# about items the cap never reaches; this one is about an item the cap reaches
# every single night. They point in opposite directions, they share no
# function, and neither is allowed to be re-expressed in terms of the other.
OVERDUE_ASK_AFTER_DAYS = 3

# The per-workspace override, on this fire's own FRP1 config (the knob's
# default lives with every other End of Day knob in `held_tier.CONFIG_DEFAULTS`
# so `get_config` has one full default set). M's ruling says 3 or 4; both are
# inside it, and nothing here bounds the value beyond "a positive whole number"
# — a workspace that sets 10 has decided it wants ten days of reminders.
OVERDUE_ASK_CONFIG_KEY = "overdue_ask_after_days"


# ---------------------------------------------------------------------------
# SPEC EODSYNTH1 — SECTION NAMES ARE JOIN KEYS (the renamed-section gotcha)
# ---------------------------------------------------------------------------
#
# A section name in this product is not a label, it is a KEY: the FRP1 config
# stores per-section decisions under it, and a rename that does not carry the
# stored value across does not fail — it DEFAULTS, silently, and the workspace
# quietly loses a decision its owner made. That is the recorded gotcha
# (`cr-renamed-section-orphans-learned-config`), and this build renames a
# section, so the migration is written out rather than hoped for.
#
# ONE rename and ONE retirement, both explicit:
#
#   `slipped_section`  ->  `slipped_prose_section`
#       The slipped ROW-LIST became the slipped-with-consequences PROSE. The
#       operator's on/off decision is about "does the evening tell me what
#       slipped", which is the same question about a different rendering, so
#       the stored value travels. An owner who turned it OFF still has it off.
#
#   `tone: "scoreboard"`  ->  `tone: "journal"`
#       "Scoreboard" named a surface that no longer exists — R-1 un-renders
#       the score. Leaving the value in place would point a live preference at
#       a retired rendering, which is the orphan in the other direction. The
#       old value is PRESERVED under `tone_before_eodsynth1` rather than
#       overwritten, because a migration that destroys the thing it migrated
#       cannot be reviewed after the fact.
#
# Idempotent by construction: a config already carrying the new key is left
# alone, so re-running this over an already-migrated workspace is a no-op and
# a later hand-edit of the new key is never clobbered by the old one.
SECTION_KEY_MIGRATIONS = (("slipped_section", "slipped_prose_section"),)
TONE_RETIRED_VALUE = "scoreboard"
TONE_SUCCESSOR_VALUE = "journal"
TONE_PRESERVED_KEY = "tone_before_eodsynth1"


def migrate_section_config(config: Optional[dict]) -> dict:
    """Carry the FRP1 section decisions across EODSYNTH1's renames. PURE.

    Returns `{"config": <new dict>, "migrated": [(old, new, value), ...],
    "changed": bool}`. Never mutates the input — a migration that edits the
    caller's dict is one that has already half-run when it raises.

    Takes and returns a CONFIG rather than a workspace so the rule is testable
    without a disk, and so the one writer (`migrate_section_config_on_disk`)
    is the only thing that can persist it.
    """
    src = dict(config or {})
    migrated = []
    for old, new in SECTION_KEY_MIGRATIONS:
        if new in src:
            continue  # already migrated, or set by hand: leave it alone
        if old in src:
            src[new] = src[old]
            migrated.append((old, new, src[old]))
    tone = src.get("tone")
    if tone == TONE_RETIRED_VALUE:
        src.setdefault(TONE_PRESERVED_KEY, tone)
        src["tone"] = TONE_SUCCESSOR_VALUE
        migrated.append(("tone", "tone", TONE_SUCCESSOR_VALUE))
    return {"config": src, "migrated": migrated, "changed": bool(migrated)}


def migrate_section_config_on_disk(workspace_root, *,
                                   origin: str = "eodsynth1") -> dict:
    """Persist `migrate_section_config` for this workspace, once.

    Writes NOTHING when nothing moved — a no-op migration that still writes is
    a `skill_reconfigured` event per fire, which is noise in the one log a
    reader consults to find out what changed a setting.
    """
    # THE RAW STORED CONFIG, not the defaulted one, and the difference is
    # load-bearing. `get_config` merges `held_tier.CONFIG_DEFAULTS` over what
    # is on disk, so the moment the new key acquires a default the migration
    # would see it as already present and skip forever — the rename would then
    # silently default on every workspace, which is precisely the gotcha this
    # function exists to close. Reading raw means "present" means "this
    # workspace decided it", which is the only reading the skip is safe under.
    # (`slipped_prose_section` is deliberately NOT in `CONFIG_DEFAULTS` for the
    # same reason: absent means never decided, and `slipped_prose_enabled`
    # answers that as ON.)
    try:
        from skill_config_writer import load_skill_config, save_skill_config
        record = load_skill_config(workspace_root, SKILL_NAME)
        # `load_skill_config` hands back the whole envelope
        # (`schema_version` / `configured_at` / `skill_name` / `config`); the
        # knobs are the inner dict, and `save_skill_config` takes that inner
        # dict back. Passing the envelope in would be rejected as unknown keys.
        cfg = (record or {}).get("config") if isinstance(record, dict) else None
    except Exception:  # noqa: BLE001 — a config that cannot be read is a
        # config this migration leaves exactly as it found it.
        return {"changed": False, "migrated": [], "error": "unreadable"}
    if not isinstance(cfg, dict):
        # Never configured, so there is no decision to carry across. Writing a
        # config here would manufacture a first-run the owner never had.
        return {"changed": False, "migrated": []}
    result = migrate_section_config(cfg)
    if not result["changed"]:
        return {"changed": False, "migrated": []}
    try:
        save_skill_config(workspace_root, SKILL_NAME, result["config"],
                          is_reconfigure=True, origin=origin)
    except Exception:  # noqa: BLE001
        return {"changed": False, "migrated": result["migrated"],
                "error": "unwritable"}
    return {"changed": True, "migrated": result["migrated"]}


def slipped_prose_enabled(config: Optional[dict]) -> bool:
    """Is the slipped-with-consequences section on for this workspace?

    Reads the MIGRATED key first and falls back to the pre-rename one, so a
    workspace whose migration has not run yet still honours the decision its
    owner made. The fallback is the belt to the migration's braces and it is
    deliberately not removable: a read that only knows the new key is exactly
    the silent default-to-on this whole section exists to prevent.
    """
    cfg = config if isinstance(config, dict) else {}
    for key in ("slipped_prose_section", "slipped_section"):
        if key in cfg:
            return str(cfg[key]).strip().lower() != "off"
    return True


# ---------------------------------------------------------------------------
# THE WORDS. Pinned, because the wording IS the contract here.
# ---------------------------------------------------------------------------

# No morning fire on record for today. NOT a zero score.
NO_PLAN_LINE = "No plan on record this morning."

# The state word for an item with no close on file. The forbidden neighbour is
# "not done" and it must never be reachable from this module.
NOT_RECORDED = "Not recorded"

NOT_RECORDED_LINE = (
    "Not recorded means nothing wrote a close for it today. It is not a claim "
    "that the work did not happen."
)

# The Monday roll-up's word for a weekday whose evening fire never ran.
NO_CLOSE_RECORDED = "no close was recorded"

# The Monday roll-up's word for the THIRD shape, and the one the first Monday
# after this ships will actually produce (review N-1). A `past-meetings`
# `pack_run` from BEFORE this build is a real fire — the day happened and the
# chat ran — but it predates the score, so it carries no counts. That is a
# different claim from both of its neighbours and it needs its own sentence:
#
#   recorded: False              -> NO_CLOSE_RECORDED   (the chat did not run)
#   recorded: True, counted: F   -> NO_SCORE_RECORDED   (it ran, uncounted)
#   recorded: True, counted: T   -> the numbers
#
# Without this the row reaches the renderer as a null count with no line, which
# is the fabricated-zero shape arriving through the other door: the code is
# honest (`None`, never `0`) and the surface then has nothing to say, which is
# exactly the moment a number gets invented.
NO_SCORE_RECORDED = "the day ran before this count existed"

# Zero urgent. Verbatim, computed, never composed.
SIGN_OFF_CLEAR = "Nothing else needs you before tomorrow's brief."

# The soften floor (reconcile_stale). ONE line, said plainly.
SOFTEN_LINE = (
    "Today's sent mail and chat did not finish syncing before this ran, so "
    "the score and the slipped list may be missing closes."
)

# Zero wins. One honest line, never padding.
NO_WINS_LINE = "Nothing closed today that I can see."

# SPEC OVERDUE1 — the fork. ONE question, the block's existing three answers,
# and the number of days said out loud, because "still on you" said for the
# fourteenth time carries no information and "8 days overdue" does. It is a
# QUESTION and never an instruction: R-3 says ask, never act, so no wording
# here may imply the system will decide if the CEO does not.
OVERDUE_ASK_LABEL = "{title} — {n} days overdue. Done, new date, or drop?"
OVERDUE_ASK_LABEL_ONE_DAY = "{title} — 1 day overdue. Done, new date, or drop?"

# The trailing line, said ONLY when something is actually resting (a line
# reporting zero is noise, and this surface has a rule about that). It names
# where the items went, because a row that vanishes from a block without a
# forwarding address reads as a row the system lost.
OVERDUE_RESTING_LINE = (
    "{n} overdue items are resting until you answer them — say `my plate` to "
    "see them.")
OVERDUE_RESTING_LINE_ONE = (
    "One overdue item is resting until you answer it — say `my plate` to see "
    "it.")


def overdue_ask_label(title: str, days_over: int) -> str:
    """The fork label for one asked row. Singular and plural are two
    constants rather than a formatted noun, for the same reason `more_line`'s
    "one"/"more" is: "1 days overdue" on a customer's screen is the kind of
    seam that makes the whole surface read as machine output."""
    title = str(title or "").strip()
    template = (OVERDUE_ASK_LABEL_ONE_DAY if days_over == 1
                else OVERDUE_ASK_LABEL)
    return template.format(title=title, n=days_over)


def resting_line(n_resting: int) -> str:
    """The trailing resting sentence, or `""` when nothing is resting — the
    same empty-string contract `more_line` returns and every render path
    already tests for."""
    try:
        n = int(n_resting)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n == 1:
        return OVERDUE_RESTING_LINE_ONE
    return OVERDUE_RESTING_LINE.format(n=n)


# WHICH WINDOW THE EVENING ACTUALLY READ (SPEC WINSFLOOR1). The evening's
# lower bound is normally the morning fire's own instant. When no morning
# receipt exists for the day there is no anchor to open the window at, and the
# shipped code let that mean NO lower bound at all — so the block that exists
# to say what moved today read every win-type event ever written and reported
# 2,328 of them as having "moved today" on a day when nothing had.
#
# The floor is workspace-LOCAL midnight of the fire's own day. A missing anchor
# means "today, as this workspace reckons it" and never "all of history".
#
# The floored case is LABELLED, never silently substituted: a surface that
# swapped one window for another would be honest in its numbers and mute about
# its aperture, which is the same defect one level up.
WINDOW_MORNING_ANCHOR = "morning_anchor"
WINDOW_DAY_FLOOR = "day_floor"

# The overflow pointer. A CAP IS A RENDER BOUND AND NEVER A SILENCE — the same
# rule and the same mechanism as the morning brief's needs-attention lane
# (`commitment_state.cap_needs_attention`, whose `more_line` its prose prints
# verbatim). This block did not need one while it was blind: it saw 18 things
# and rendered 6 of them, so the cap almost never bound. Resolving the titles
# is what made saturation the norm — on the substrate the review measured, 23
# of 28 days now exceed the cap, and one live day put 37 wins through a 6-row
# aperture. Reporting 6 of 37 with nothing saying so is the same false-zero
# class this whole build exists to close, one order of magnitude smaller.
#
# There is no "show me the rest" trigger to point at, so this line points at
# nothing and says so honestly: the count, and the rule the aperture used.
#
# TWO SPELLINGS, ONE PER WINDOW (SPEC WINSFLOOR1). The line names the window it
# read and no other. On the anchor path "moved today" is TRUE — the window
# opened at this morning's brief. On the floored path it is a claim the read
# cannot support in that wording, so the floored spelling says the window it
# actually used, plainly. Both live here so there is one place to read them and
# one place to change them; `WINS_MORE_LINES` is the only lookup.
#
# AND EVERY CAPPED BLOCK NOW STATES ITS DENOMINATOR (SPEC EODLEDGER1 part 1).
# `{n_shown} of {n_total}` replaces "at most {cap}", and the same shape spells
# `slipped` and `confirm` too. The cap was never the reader's question: `wins`
# said "at most 6" while it was hiding 31, `slipped` bound 3 of 41 silently and
# `confirm` 5 of 67, and a cap without a denominator is not a summary — it is a
# claim about size. `cap` is what the code was told; `n_total` is what the day
# actually held, and only the second is a fact about the reader's day.
MORE_LINE_SHAPE = ("…and {n_more} more {tail} — this block shows {n_shown} of "
                   "{n_total}, {rank} first.")


def _more_template(tail: str, rank: str) -> str:
    """One block's cap sentence, MINTED from `MORE_LINE_SHAPE`.

    Derived rather than typed out four times: "there is one wording rule" is a
    property of the code this way, and a fifth block cannot acquire its own
    dialect by being written somewhere else in the file. Only the two words
    that are genuinely per-block — what the hidden rows ARE and what won the
    visible slots — are supplied here.
    """
    return MORE_LINE_SHAPE.replace("{tail}", tail).replace("{rank}", rank)


# The per-block words. `{tail}` names what the hidden rows are, because "and 38
# more" over a list of promises and "and 62 more" over a review queue are
# different sentences to be on the hook for; `{rank}` names what won the
# visible slots, so the reader knows what they are NOT seeing.
WINS_TAIL_ANCHOR = "moved today"
WINS_TAIL_DAY_FLOOR = "moved since midnight"
WINS_RANK = "named rows"
SLIPPED_TAIL = "on the you-owe list"
SLIPPED_RANK = "overdue"
CONFIRM_TAIL = "waiting to be confirmed"
CONFIRM_RANK = "higher-stakes"

WINS_MORE_LINE_ANCHOR = _more_template(WINS_TAIL_ANCHOR, WINS_RANK)
WINS_MORE_LINE_DAY_FLOOR = _more_template(WINS_TAIL_DAY_FLOOR, WINS_RANK)
WINS_MORE_LINES = {
    WINDOW_MORNING_ANCHOR: WINS_MORE_LINE_ANCHOR,
    WINDOW_DAY_FLOOR: WINS_MORE_LINE_DAY_FLOOR,
}
SLIPPED_MORE_LINE = _more_template(SLIPPED_TAIL, SLIPPED_RANK)
CONFIRM_MORE_LINE = _more_template(CONFIRM_TAIL, CONFIRM_RANK)

# THE registry. One place to read every cap sentence this surface can say, so
# "there is one wording rule" is checkable rather than asserted — a fifth
# template added anywhere else is a template this tuple does not know about,
# and the suite reads THIS tuple when it pins that every one of them carries a
# denominator.
MORE_LINE_TEMPLATES = (WINS_MORE_LINE_ANCHOR, WINS_MORE_LINE_DAY_FLOOR,
                       SLIPPED_MORE_LINE, CONFIRM_MORE_LINE)


def more_line(template: str, *, n_shown: int, n_total: int) -> str:
    """THE cap sentence, for every capped block on this surface (EODLEDGER1).

    ONE helper and one shape, so a block cannot acquire its own dialect of
    "there is more than this". `wins` had this treatment and the other two did
    not, which is how 3-of-41 and 5-of-67 read on screen as 3 and 5.

    Returns `""` when the cap did not bind — a pointer at nothing is noise, and
    an empty string is what every render path already tests for.

    `n_total` is the HONEST total, not the number of rows the caller could see:
    a lane that was already bounded upstream must pass the upstream total or
    the denominator it prints is the cap wearing a total's clothes.
    """
    try:
        n_shown = max(0, int(n_shown))
        n_total = max(0, int(n_total))
    except (TypeError, ValueError):
        return ""
    n_more = n_total - n_shown
    if n_more <= 0:
        return ""
    return template.format(n_more=n_more, n_shown=n_shown, n_total=n_total)


# ---------------------------------------------------------------------------
# THE COVERAGE STRIP (SPEC EODLEDGER1 part 1) — what this fire actually READ
# ---------------------------------------------------------------------------
#
# The fire asks for email, calendar and chat per capability and receipts every
# gap under `connector_gaps` — and said nothing about any of it in chat, by
# design. Measured on the live workspace on 2026-08-19: the chat cursor had sat
# at 2026-08-14 for five days while the evening surface reported the day's
# closes with no qualification at all, and a calendar outage rendered
# byte-identically to a genuinely empty tomorrow. The reader could not tell
# "nothing happened" from "I could not look".
#
# So the aperture gets a block, and it is the FIRST one under the alarms.
# Composed in code, rendered verbatim, never hand-written: the whole value of
# the strip is that it states what the RUN did rather than what the surface
# would like to have done, and a line a model writes is a line about the
# latter.
#
# NEVER SUPPRESSED AND NEVER SOFTENED — the same posture as `alarm_lines`, for
# the same reason. A degraded read is precisely when the reader most needs to
# know what the aperture was; a coverage strip that goes quiet on a bad night
# is a coverage strip that only ever says "everything was fine".

CAP_MAIL = "mail"
CAP_CHAT = "chat"
CAP_CALENDAR = "calendar"
CAP_MEETINGS = "meetings"

# The order the strip renders in, and the whole set of capabilities this fire
# reaches for. CONN1/CONN2 add Drive and DocuSign by adding rows here.
COVERAGE_CAPABILITIES = (CAP_MAIL, CAP_CHAT, CAP_CALENDAR, CAP_MEETINGS)

COVERAGE_LABELS = {
    CAP_MAIL: "Mail",
    CAP_CHAT: "Chat",
    CAP_CALENDAR: "Calendar",
    CAP_MEETINGS: "Meetings",
}

# A cursor whose instant falls on an EARLIER workspace-local day than the fire's
# own is stale, and the line names the span. One day, not three: the cursor is
# supposed to advance every evening, so "yesterday" is already a missed night.
CURSOR_STALE_DAYS = 1

# THE WORDS. Every sentence the strip can say lives here, so there is one place
# to read them and one place to change them.
COVERAGE_READ_THROUGH = "{label}: read through {when}."
COVERAGE_STALE = (
    "{label}: read through {when} — {n_days} behind, so anything closed "
    "there since then is not in tonight's numbers.")
COVERAGE_NEVER_ADVANCED = (
    "{label}: nothing on record for how far this has been read, so tonight's "
    "numbers cannot claim it was.")
# A leg that ran on the PARTIAL path is a real run and not a complete one. The
# chat leg's own receipt already carries `degraded` plus the note; without this
# clause a per-conversation sweep renders identically to a full one, which is
# the same overclaim as a stale cursor rendering as current, one degree milder.
COVERAGE_DEGRADED = " Read on the partial path: {note}"
COVERAGE_NOT_READ = "{label}: not read — {reason}."
COVERAGE_NOT_READ_NO_REASON = (
    "{label}: not read, and nothing on the record says why.")
COVERAGE_CALENDAR_READ = "Calendar: read."
COVERAGE_CALENDAR_ABSENT = (
    "Calendar: not read, so tomorrow's list below is what is on file, not "
    "what is on the calendar.")
# The same claim WITH the reason the fire receipted. Two spellings rather than
# one with an optional clause, because the reasonless case is a real one (a
# fire that omitted `--calendar-json` and receipted no gap) and it must not
# render a dangling dash.
COVERAGE_CALENDAR_ABSENT_REASON = (
    "Calendar: not read — {reason}. Tomorrow's list below is what is on file, "
    "not what is on the calendar.")
COVERAGE_MEETINGS = "Meetings: reading back to {since} ({span}); {counts}."
COVERAGE_MEETINGS_UNKNOWN = (
    "Meetings: the capture window could not be read, so this fire cannot say "
    "how far back it looked.")
# THE TWO COUNTS DEGRADE INDEPENDENTLY OF THE WINDOW, AND OF EACH OTHER
# (EODLEDGER1 review fix-round 1). `capture_aperture` sets `known` the moment
# `catchup_window` returns, and the two counts are read AFTER that — the meeting
# scan needs a parseable `start_aware` and the backlog sweep is its own import
# that can raise. Either can come back `None` over a perfectly known window, and
# rendering `None` as `0` prints a number this fire does not have: "0 still
# waiting to be processed" is a claim, and an unread count is not zero. That is
# the guessed-baseline class this same spec forbids one field down, and it is
# what the block's own docstring already promised ("a different claim from
# 'there is nothing there'"). So each half says which it is.
COVERAGE_MEETINGS_N = "{n_meetings} on record in that span"
COVERAGE_MEETINGS_N_UNREAD = "how many are on record in that span could not be read"
COVERAGE_MEETINGS_BACKLOG = "{n_backlog} still waiting to be processed"
COVERAGE_MEETINGS_BACKLOG_UNREAD = (
    "how many are still waiting to be processed could not be read")

# The data-quality note. A COUNT, not a section (EODLEDGER1 §6): 802 closes on
# the live workspace cite no artifact anyone can open, which is a real signal
# with no surface today and not worth a block of its own.
COVERAGE_UNSOURCED = (
    "{n} of the closes in this window cite no artifact anyone can open.")


# ---------------------------------------------------------------------------
# THE LEDGER (SPEC EODLEDGER1 part 2) — what the day did to the open book
# ---------------------------------------------------------------------------
#
# The `score` block scores this morning's plan. The BOOK is the other question
# and the bigger one: 218 open, 137 owed by the CEO, 41 overdue — seven numbers
# computed on every fire and rendered on none of them.
#
# THE OPENING FIGURE IS READ, NEVER INFERRED. It comes off the morning fire's
# own `brief_state` — the audit event that fire wrote, carrying the counts
# `compute_brief_state` produced at that instant. With no morning fire there is
# no opening figure, the block says exactly that, and NO arithmetic happens at
# all. This is `NO_PLAN_LINE`'s doctrine one field down: never a guessed
# baseline, and specifically never TODAY's count standing in for THIS MORNING's,
# which would render a delta of zero on the one day the delta is unknowable.
# The ledger's two states, declared in ONE home — the same posture
# `FIRST_MOVE_STATUSES` keeps, and for the same reason: prose that names a
# value the code actually writes is documentation, and the prose-contract
# scanner can only tell that apart from drift if there is a home to point at.
# There is no third state: either the opening figure was read, or it was not.
LEDGER_MOVEMENT = "movement"
LEDGER_NO_OPENING_FIGURE = "no_opening_figure"
LEDGER_STATUSES = (LEDGER_MOVEMENT, LEDGER_NO_OPENING_FIGURE)

NO_OPENING_FIGURE = "no opening figure on record"
NO_OPENING_FIGURE_LINE = (
    "There is no opening figure on record for today, so the day's movement "
    "cannot be stated. What closed is counted below.")

LEDGER_LINE = ("Open book: {book_at_open} this morning, {opened} opened, "
               "{closed} closed, {dropped} dropped — {book_now} now "
               "({delta}).")
LEDGER_DELTA_DOWN = "down {n}"
LEDGER_DELTA_UP = "up {n}"
LEDGER_DELTA_FLAT = "no net change"

# The parts will not always account for the whole, and saying so is cheaper
# than a number that does not add up. An item confirmed out of the review queue
# enters the book without being "opened"; a merge retires two rows into one.
# The residual is NAMED rather than absorbed into one of the four movements,
# because absorbing it is how a ledger starts lying quietly.
LEDGER_RESIDUAL_LINE = (
    "The movements above account for {accounted} of the change; {residual} "
    "came from somewhere this fire cannot see (a confirm out of the review "
    "queue, a merge, an edit).")


# ---------------------------------------------------------------------------
# THE CATCH-UP READ (SPEC EODLEDGER1 part 3 — M's ruling on D3)
# ---------------------------------------------------------------------------
#
# Over 24 hours late the fire enters the degrade tier: it performs every
# substrate write and then posts only `late_fire`'s `degrade_notice`. The score
# anchors on TODAY's morning receipt, so a skipped Tuesday is never scored —
# not that evening, not ever. The catch-up machinery works perfectly and its
# output has never been shown to anyone.
#
# M'S RULING: LABEL THE SPAN, DO NOT WITHHOLD IT. The degrade tier's instinct
# is right — a stale surface must never be presented as fresh — and the remedy
# is a label, not a silence.
#
# WHY `degrade_notice` IS NOT WHAT GETS POSTED HERE. That string says "Skipped
# the full {display}", which is a true sentence on every OTHER scheduled
# surface and a false one on this one from the moment this fire renders. It is
# a SHARED constant: every orchestrator's degrade branch posts it as its whole
# output, and rewording it would silently change what those surfaces say. So
# `late_fire` is untouched — thresholds, return shape and notice text all
# byte-identical — and this fire composes its own label from the SAME return's
# facts (`lateness_minutes`, `scheduled_for`) plus the catch-up window. The
# notice is retained on the record (the fire carries it onto its receipt) and
# is not the thing the reader sees.
#
# ONE READ, NOT ONE PER DAY. A multi-day catch-up is compressed into a single
# surface covering the span. The per-day detail already exists in the Monday
# roll-up, and a stack of stale surfaces is the pile M's ruling removed.
CATCHUP_LABEL = (
    "This is your End of Day for {span}, arriving {late}. It was scheduled "
    "for {scheduled}.")
CATCHUP_SPAN_ONE_DAY = "{day}"
CATCHUP_SPAN_MULTI = "{first} through {last}"
CATCHUP_COMPRESSED_LINE = (
    "{n_days} days are accounted for in this one read, not one surface each.")
CATCHUP_CAPPED_LINE = (
    "The gap was longer than the {cap}-day ceiling, so this read starts at "
    "the ceiling and does not cover everything before it.")

# No day intent, and none proposable.
NO_TOMORROW_INTENT_LINE = "Nothing on file yet for tomorrow."

# The verbs a slipped row offers. Canonical wire ids, one display word each
# (verb_taxonomy owns the labels; these are the wire ids apply-choices routes).
SLIPPED_VERBS = ("push to [date]", "draft", "drop")

# The verb set the confirm block offers.
CONFIRM_VERBS = ("confirm", "drop")

# PERSONLOOP1 §0-3 — the confirm block's second, capped half: the recurring
# names the queue is jammed behind. Its verbs come from the ONE list all
# three surfaces read (`person_candidates.CANDIDATE_ROW_ACTIONS`), imported
# lazily at the row-build site so this module stays import-light.
PERSON_CANDIDATE_BLOCK = "person_candidate"

# The tomorrow block's two taps. SPEC TOMPICK1 does not add a verb here — a
# bare digit ("1"/"2"/"3") is a POSITION, resolved by `resolve_intent_confirm`
# off the SAME candidates `confirm` already reads, not a new tap type. Adding
# digits to this tuple would also break the byte-identical pin TOMFILT1's
# suite already carries (`eod.TOMORROW_VERBS == ("confirm", "edit [change]")`).
TOMORROW_VERBS = ("confirm", "edit [change]")

# SPEC TOMPICK1 §0.1 — each ranked candidate carries its one-line why, so the
# CEO is picking between reasons, not just text. Two shapes only: it lines up
# with something already on tomorrow's calendar, or it is simply the next
# open item on tonight's list. `{title}` is the matched meeting's own title —
# never paraphrased, so the why can be checked against the calendar row it
# names.
TOMORROW_CANDIDATE_WHY_MATCHED = "lines up with tomorrow's \"{title}\""
TOMORROW_CANDIDATE_WHY_OPEN = "still open on tonight's list"

# Refusals, plain English, when a tap cannot be resolved.
NO_MAP_REFUSAL = (
    "I do not have a numbered list from an End of Day to match that against "
    "yet. Run the End of Day once, or tell me what to close by name."
)

STALE_MAP_REFUSAL = (
    "I cannot act on that number. The most recent End of Day did not record "
    "its numbered list, so the numbers you are looking at and the numbers I "
    "have do not line up, and acting by number now could hit the wrong item. "
    "Say 'end of day' once and the numbers will match, or tell me what you "
    "mean by name."
)

ORPHAN_LINE = (
    "Your End of Day posted without recording that it ran, so its numbers "
    "cannot be used for one-tap actions right now. Say 'end of day' once and "
    "the numbers will line up again."
)

# SPEC TOMPICK1 §0.2 — a positional pick outside the offered candidates.
# `{rank}` is what the CEO typed; `{n}`/`{plural}`/`{span}` describe what was
# actually on the receipt, so the refusal names the real range rather than a
# generic "invalid choice".
NO_SUCH_CANDIDATE_REFUSAL = (
    "There's no option {rank} in tonight's tomorrow proposal — I offered "
    "{n} candidate{plural}. Say 1{span}, or tell me what tomorrow is about "
    "and I will write that down."
)

# Kept as a module constant so a wording change is one edit and the pin above
# it stays honest.
DEVREAD_SLOT_ID = "weekly_development_read"


# ---------------------------------------------------------------------------
# Small shared plumbing
# ---------------------------------------------------------------------------

def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value):
    from event_time import parse_ts
    try:
        return _aware(parse_ts(value))
    except Exception:  # noqa: BLE001 — a read never breaks a fire
        return None


def _load_events(workspace_root) -> list:
    """Every well-formed event, defensively. A half-written line is SKIPPED,
    never fatal: an evening surface that crashes because one row is truncated
    is worse than one that says less."""
    from event_refs import load_events
    out = []
    for ev in load_events(_events_path(workspace_root)):
        if isinstance(ev, dict):
            out.append(ev)
    return out


def workspace_today(workspace_root, *, now=None) -> _dt.date:
    """The workspace's OWN calendar date. Delegates to `day_intent`, which
    delegates to `tz.py` — ONE date resolver for the bookends, because two
    would disagree exactly at the hour this fire runs (9 PM Pacific is already
    tomorrow in UTC)."""
    from day_intent import workspace_today as _today
    return _today(workspace_root, now=now)


def _day_floor(workspace_root, *, now_iso=None) -> _dt.datetime:
    """Workspace-LOCAL midnight of the fire's own day, as an aware datetime.

    Through `workspace_today` → `day_intent` → `tz.py`, which is the ONE date
    resolver the bookends share. Never the machine clock and never UTC: this
    fire runs in the evening, and at 9 PM Pacific the UTC calendar has already
    rolled over, so a UTC floor would open the window on tomorrow and hide the
    entire day it is supposed to be closing.

    Raises rather than guessing when the workspace timezone cannot be resolved
    — the same posture `day_intent.workspace_today` already takes, and the same
    reason: a floor under the wrong date reads as a perfectly plausible day.
    The evening driver already calls `workspace_today` before it reaches either
    helper, so this raises nothing that was not already going to raise.
    """
    from tz import load_workspace_tz
    day = workspace_today(workspace_root, now=now_iso)
    zone = load_workspace_tz(workspace_path=workspace_root)
    return _dt.datetime.combine(day, _dt.time(0, 0), tzinfo=zone)


def _window(workspace_root, since_ts, *, now_iso=None) -> tuple:
    """`(since, window_source)` — the lower bound the evening reads from, and
    the name of the window it is (SPEC WINSFLOOR1).

    THE LOWER BOUND IS NEVER `None`. That is the whole fix. `since_ts` arrives
    from `morning_fire(...)["ts"]`, which is correctly `None` on a day whose
    morning brief never fired; the shipped readers turned that into an absent
    lower bound and scanned the workspace's entire history.

    An unparseable `since_ts` floors too, and for the same reason: "I could not
    read the anchor" is not a licence to read everything.

    This lives in the helpers, not at the call site. Both readers are called
    from more than one place, and a caller-side fence makes prose load-bearing
    — 4 of the 8 findings on the 2026-08-17 walk were that class.
    """
    since = _parse_iso(since_ts) if since_ts else None
    if since is not None:
        return since, WINDOW_MORNING_ANCHOR
    return _day_floor(workspace_root, now_iso=now_iso), WINDOW_DAY_FLOOR


def day_branch(day: _dt.date) -> str:
    """Which branch of §3 this fire renders: `monday` (the prior-week roll-up
    plus the development-read slot), `friday` (a plain day-close, NO hand-off
    line — Conflict D, M's ruling: the Friday chat competes with weekly-recap
    and loses), or `plain`."""
    if day.weekday() == 0:
        return "monday"
    if day.weekday() == 4:
        return "friday"
    return "plain"


# ---------------------------------------------------------------------------
# The morning fire this evening is scored against
# ---------------------------------------------------------------------------

def morning_fire(workspace_root, for_date, *, now_iso=None) -> dict:
    """The morning-brief `pack_run` for `for_date`, normalized.

    Returns `{"found": bool, "ts": iso|None, "needs_attention_ids": [...],
    "digest_path": str|None, "fired_via": str|None, "anchor": str|None,
    "n_fires_today": int, "latest_ts": iso|None}`.

    `found` False is the `NO_PLAN_LINE` case and the ONLY correct answer to a
    day with no morning receipt. It is never turned into a zero: the score
    block exists to compare against a plan, and there is no plan to compare
    against.

    THE ANCHOR IS THE DAY'S FIRST SCHEDULED FIRE, NOT ITS LATEST (SPEC EODFIX1
    §0-3). This function's `ts` is what the evening passes as `since_ts` for
    the wins window and the closure join, so "which morning receipt" IS "how
    much of the day the evening can see". Taking the latest meant a second
    morning fire — a duplicate at 1:21 PM, a manual re-run, a catch-up — moved
    the window's opening to the afternoon and hid every close before it. On
    2026-08-17 that collapsed a nine-hour window to two hours and the flagship
    surface reported nothing closed on a day with two closes on file.

    Deliberately independent of any scheduler fix: even with duplicate fires
    gone, a mid-day manual re-run must not shrink the evening's window.

    `scheduled` wins because it is the fire that IS the morning; when the day
    carries none (a workspace that only ever briefs by hand), the earliest fire
    of any kind anchors instead — `anchor` says which rule applied, and
    `latest_ts` / `n_fires_today` keep the rest of the day's fires visible
    rather than silently discarded.
    """
    from receipts import iter_receipts

    target = str(for_date)
    todays = []
    try:
        rows = iter_receipts(workspace_root, task_ids=[MORNING_TASK_ID])
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        if r.get("type") != RECEIPT_EVENT:
            continue
        dt = _aware(r.get("dt"))
        if dt is None:
            continue
        # Local calendar day, not UTC: a 7 AM Pacific brief is the same UTC
        # day as its 5 PM close, but a workspace east of UTC is not.
        try:
            from tz import to_local
            local = to_local(dt, workspace_path=workspace_root)
            day = local.date().isoformat() if local else dt.date().isoformat()
        except Exception:  # noqa: BLE001
            day = dt.date().isoformat()
        if day != target:
            continue
        todays.append((dt, r))
    if not todays:
        return {"found": False, "ts": None, "needs_attention_ids": [],
                "digest_path": None, "fired_via": None, "anchor": None,
                "n_fires_today": 0, "latest_ts": None}

    todays.sort(key=lambda pair: pair[0])
    scheduled = [pair for pair in todays
                 if str(pair[1].get("fired_via") or "") == "scheduled"]
    if scheduled:
        best_dt, best = scheduled[0]
        anchor = "first_scheduled"
    else:
        best_dt, best = todays[0]
        anchor = "first_any"

    raw = best.get("raw") if isinstance(best.get("raw"), dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    ids = data.get("needs_attention_ids")
    return {
        "found": True,
        "ts": best_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "needs_attention_ids": [str(i) for i in ids] if isinstance(ids, list) else [],
        "digest_path": data.get("digest_path") or None,
        "fired_via": best.get("fired_via"),
        "anchor": anchor,
        "n_fires_today": len(todays),
        "latest_ts": todays[-1][0].strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def read_morning_digest(workspace_root, morning: dict) -> dict:
    """Read the morning digest BACK off disk (`pack_run.digest_path` →
    `_hq/briefings/morning-YYYY-MM-DD.md`).

    The receipt says what the brief NUMBERED; the digest says what it ASKED.
    The score block wants both — the ids to join closures against, and the
    brief's own first move to say whether the day went where the morning said
    it would. Reading the file back is the whole plumbing; nothing new is
    persisted for it.

    Returns `{"path", "exists", "asks": [...], "first_move": str|None}`.
    A path that does not resolve is `exists: False` with empty asks — never a
    raise, and never a claim that the brief asked for nothing.
    """
    out = {"path": None, "exists": False, "asks": [], "first_move": None}
    raw_path = (morning or {}).get("digest_path")
    if not raw_path:
        return out
    try:
        from workspace_paths import normalize_persisted_path
        resolved = normalize_persisted_path(raw_path, workspace_root)
    except Exception:  # noqa: BLE001 — legacy absolute rows and odd shapes
        resolved = raw_path
    path = Path(resolved)
    if not path.is_absolute():
        path = Path(workspace_root) / path
    out["path"] = str(raw_path)
    try:
        if not path.is_file():
            return out
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    out["exists"] = True
    lines = [l.strip() for l in text.splitlines()]
    for i, line in enumerate(lines):
        low = line.lower().replace("*", "").replace("#", "").strip()
        if low.startswith("suggested first move"):
            tail = line.split(":", 1)[1].strip() if ":" in line else ""
            if not tail:
                for nxt in lines[i + 1:]:
                    if nxt:
                        tail = nxt
                        break
            out["first_move"] = tail.lstrip("-* ").strip() or None
            break
    # The numbered asks the brief printed, in its own order.
    for line in lines:
        m = re.match(r"^(?:\*\*)?(\d{1,2})[.)](?:\*\*)?\s+(.*\S)\s*$", line)
        if m:
            out["asks"].append({"n": int(m.group(1)),
                                "text": m.group(2).strip()})
    return out


# ---------------------------------------------------------------------------
# Closures — the day's discharge, with pointers
# ---------------------------------------------------------------------------

_CLOSE_TYPES = ("commitment_resolved", "thread_resolved")


class ClosureWindow(list):
    """The closure rows, carrying the NAME of the window they were read from.

    A list, because every caller of `closures_since` iterates it — the score
    join, `unsourced_closes`, the first-move check and two suites. A dict
    return would have been the plainer shape for one extra fact and a breaking
    change for all of them, so the fact rides on the rows instead of replacing
    them.

    `window_source` is an ATTRIBUTE, not an element: it survives iteration,
    `len`, indexing, equality against a plain list and `json.dumps` (which
    serializes this as the array it is). It does NOT survive slicing or
    `list(...)` — those produce a plain list. Read it off the object this
    function returned, and read it STRICTLY (`rows.window_source`, never a
    defaulted `getattr`): a window that cannot say which window it is should
    fail loudly, not report the wrong one quietly.
    """

    __slots__ = ("window_source", "since")

    def __init__(self, rows, *, window_source, since):
        super().__init__(rows)
        self.window_source = window_source
        self.since = since


def closures_since(workspace_root, since_ts, *, now_iso=None) -> "ClosureWindow":
    """Every commitment closure written since `since_ts`, newest last.

    SPEC WINSFLOOR1 — `since_ts` NO LONGER MEANS "since the beginning of time"
    when it is absent. A day with no morning receipt floors to workspace-local
    midnight (`_window`), and the return names the window it read:
    `window_source` is `morning_anchor` or `day_floor`. Measured on the live
    workspace on 2026-08-19, the unfloored read returned 942 closures on a day
    that had closed none of them.

    Each row: `{"commitment_id", "title", "ts", "resolved_by", "resolution",
    "source_ref", "ref_grain", "provenance_missing", "source_skill"}`.

    `source_ref` is read, never invented. A close written without one carries
    `provenance_missing` and this reader passes that through untouched: the
    fire's own closes are required to carry a pointer (PROV1), and a row that
    does not is a row a reviewer should be able to SEE, not one this reader
    quietly launders into looking sourced.

    SPEC PROVMINT1 — `ref_grain` rides along for exactly that reason. The
    writers now MINT a surface receipt when nothing reaches them, so
    `provenance_missing` no longer marks the honest-degradation case and a
    reader that projected only `source_ref` would show every close as sourced.
    The grain is the discriminator; it is projected, never derived, and its
    absence means caller-passed (which every pre-PROVMINT1 row is).
    """
    since, window_source = _window(workspace_root, since_ts, now_iso=now_iso)
    until = _parse_iso(now_iso) if now_iso else None
    events = _load_events(workspace_root)
    # SPEC EODFIX1 — the same id→title join the wins block uses. Without it
    # `title` is empty for every close whose writer predates the snapshot,
    # which is every close already on disk: the score's closed rows render
    # nameless and the first_move check has nothing to compare against.
    idx = _win_join_index(events)
    names = _entity_names(workspace_root)
    out = []
    for ev in events:
        if ev.get("type") not in _CLOSE_TYPES:
            continue
        ts = _parse_iso(ev.get("ts"))
        # THE LOWER BOUND IS ALWAYS REAL (SPEC WINSFLOOR1). `_window` never
        # hands back None, so this test is never the no-op it used to be on a
        # day with no morning receipt.
        if ts is None or ts < since:
            continue
        if until is not None and ts is not None and ts > until:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = data.get("commitment_id") or data.get("id")
        title, title_source, _ref = _resolve_win_title(ev, data, idx, names)
        if title_source == TITLE_GENERIC:
            # The typed line is a sentence about the EVENT, not a name for the
            # thing. It renders in the wins block, where that is the whole
            # claim; a closure row's `title` is a name, so an unnamed close
            # stays unnamed here rather than borrowing a sentence.
            title = ""
        out.append({
            "commitment_id": str(cid) if cid else None,
            "title": title,
            "title_source": title_source if title else None,
            # SPEC EODARC1 — the thread the closing event itself named, so a
            # close can JOIN the arc that tracks it. Read off the event, never
            # inferred; absent stays absent.
            "thread_id": (ev.get("primary_thread_id") or data.get("thread_id")
                          or data.get("primary_thread_id")),
            "ts": ev.get("ts"),
            "resolved_by": data.get("resolved_by"),
            "resolution": data.get("resolution") or "done",
            "source_ref": data.get("source_ref"),
            "ref_grain": data.get("ref_grain"),
            "provenance_missing": bool(data.get("provenance_missing")),
            "source_skill": ev.get("source_skill"),
            "evidence": str(data.get("evidence") or "").strip(),
        })
    return ClosureWindow(out, window_source=window_source,
                         since=since.isoformat())


# ---------------------------------------------------------------------------
# SPEC EODSYNTH1 — the synthesis INPUTS. Reads only; nothing here composes.
# ---------------------------------------------------------------------------
#
# These four readers exist HERE rather than in `eod_synthesis` on purpose:
# that module composes prose and touches no disk, so every claim it makes can
# be reproduced from its arguments alone. Splitting the I/O out is what makes
# the grounding pins testable without a workspace — plant a row, compose, read
# the sentence.

_DECISION_TYPES = ("decision",)
_NOTE_TYPES = ("note",)


def todays_decisions(workspace_root, since_ts, *, now_iso=None) -> list:
    """Decision events inside this fire's own window. Same window resolution
    (`_window`) every other block on this surface uses, so a day with no
    morning brief floors to workspace-local midnight rather than reading the
    whole history (the WINSFLOOR1 failure, one block over)."""
    since, _src = _window(workspace_root, since_ts, now_iso=now_iso)
    until = _parse_iso(now_iso) if now_iso else None
    out = []
    for ev in _load_events(workspace_root):
        if ev.get("type") not in _DECISION_TYPES:
            continue
        ts = _parse_iso(ev.get("ts"))
        if ts is None or ts < since:
            continue
        if until is not None and ts > until:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        out.append({"decision_id": data.get("decision_id") or data.get("id"),
                    "title": str(data.get("decision") or data.get("title")
                                 or "").strip(),
                    "decision": str(data.get("decision") or "").strip(),
                    "thread_id": data.get("thread_id")
                    or data.get("primary_thread_id"),
                    "ts": ev.get("ts"), "data": data})
    return out


def todays_notes(workspace_root, since_ts, *, now_iso=None) -> list:
    """Explicit `note` rows inside the same window. `note` is the ONE type
    read here: a takeaway the user wrote down is a takeaway; a takeaway
    inferred from a cluster of activity is the manufactured profundity §3.4
    bans, one tier down.

    SESSSTORY1's compose-dedup MARKER also rides the `note` family
    (`data.recovered_kind: "session_narrative"`, one per composed session,
    both origins — see session_narrative.mark_composed / EVENT_TYPES.md
    "Session chapter lane"). That row is bookkeeping, not a takeaway: its
    only text is a fixed-shape `summary` ("Session notes composed (...)"),
    and the swept ones land nightly — unfiltered they would print one
    "Noted: Session notes composed ..." line per session into Worth
    Remembering EVERY day and compete with real takeaways for its cap
    (REVIEW SESSSTORY1 F1). Excluded here, at the reader this docstring's
    own doctrine defines — the writer stays exactly as EVENT_TYPES.md
    documents it."""
    since, _src = _window(workspace_root, since_ts, now_iso=now_iso)
    until = _parse_iso(now_iso) if now_iso else None
    out = []
    for ev in _load_events(workspace_root):
        if ev.get("type") not in _NOTE_TYPES:
            continue
        _d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if _d.get("recovered_kind") == "session_narrative":
            # session_narrative._MARKER_KIND — the dedup marker, never a takeaway.
            continue
        ts = _parse_iso(ev.get("ts"))
        if ts is None or ts < since:
            continue
        if until is not None and ts > until:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        out.append({"id": data.get("id") or ev.get("seq") or ev.get("ts"),
                    "note": str(data.get("note") or data.get("text")
                                or "").strip(),
                    "thread_id": data.get("thread_id"),
                    "ts": ev.get("ts"), "data": data})
    return out


def declared_arcs(workspace_root, *, for_date: str, now_iso=None) -> list:
    """The arcs this substrate DECLARES — never one a model infers (§3.3).

    Five sources and no sixth, each of them a row somebody put on the record
    saying "this is a thing we are working toward":

      objectives  open `objective_created` rows (completed / archived ones are
                  no longer arcs; a finished objective is history, and today
                  cannot land on it)
      day intent  TODAY's stated intent (`load_day_intent`, default
                  `include_proposed=False` — a proposal is a guess and may
                  never stand as an arc)
      org         (SPEC EODARC1) an org RELATIONSHIP with a live thread —
                  every non-self org the entity register carries that has at
                  least one active/scoping thread affiliated to it. Read from
                  the register through `entities_io`, NEVER from the deal
                  tracker: ruling 3 is that deals are context, never state,
                  so an org with a live thread IS an arc whether or not a
                  deal row tracks it, and nothing in this read raises when
                  deal state is absent, stale, or malformed.
      workstream  active threads in `entities.json` that no org arc already
                  carries — the product tracks and internal lanes. A thread
                  an org arc claims is that arc's linkage, not a second arc:
                  one relationship, one sentence.
      deal        deals carrying a recorded stage — kept, and deliberately
                  ADDITIVE ONLY: a deal row can add an arc, its absence can
                  never subtract one (ruling 3 again, from the other side).

    The sixth class ruling 2 names — consequence-carrying commitments — is
    minted from the OPEN BOOK inside the composer
    (`eod_synthesis.mint_commitment_arcs`), because it is a function of rows
    the driver already holds, not a disk read of its own.

    Everything comes back through `eod_synthesis.declared_arc`, which REFUSES
    an arc with no id — so an arc-shaped string can never enter the join.
    """
    import eod_synthesis as syn

    arcs, seen = [], set()

    def _add(kind, arc_id, label, thread_id=None, thread_ids=None):
        key = (kind, str(arc_id))
        if not str(arc_id or "").strip() or key in seen:
            return
        try:
            arcs.append(syn.declared_arc(kind, arc_id, label,
                                         thread_id=thread_id,
                                         thread_ids=thread_ids))
            seen.add(key)
        except ValueError:
            pass

    retired = set()
    objectives = {}
    for ev in _load_events(workspace_root):
        etype = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if etype == "objective_created":
            oid = data.get("objective_id") or data.get("id")
            if oid:
                objectives[str(oid)] = (str(data.get("title")
                                            or data.get("objective") or "").strip(),
                                        data.get("thread_id"))
        elif etype in ("objective_completed", "objective_archived"):
            oid = data.get("objective_id") or data.get("id")
            if oid:
                retired.add(str(oid))
        elif etype in ("deal_created", "deal_updated", "deal_stage_changed"):
            did = data.get("deal_id") or data.get("id")
            if did and data.get("stage"):
                _add(syn.ARC_DEAL, did,
                     str(data.get("title") or data.get("name")
                         or data.get("stage") or "").strip(),
                     thread_id=data.get("thread_id"))
    for oid, (label, thread_id) in objectives.items():
        if oid not in retired:
            _add(syn.ARC_OBJECTIVE, oid, label, thread_id=thread_id)

    try:
        from day_intent import load_day_intent
        record = load_day_intent(workspace_root, for_date)
    except Exception:  # noqa: BLE001 — a read never breaks the fire
        record = None
    if isinstance(record, dict) and record.get("stated"):
        goal = str(record.get("goal") or record.get("intent") or "").strip()
        if not goal:
            items = [i for i in (record.get("items") or [])
                     if isinstance(i, dict)]
            goal = str(items[0].get("text") or "").strip() if items else ""
        _add(syn.ARC_DAY_INTENT, f"day:{for_date}", goal or for_date)

    try:
        path = Path(workspace_root) / "_hq" / "data" / "entities.json"
        ents = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        ents = {}

    def _coll(name):
        # The canonical wrapper-aware read (`entities_io`) with the pre-EODARC1
        # tolerant fallback behind it: a register this fire cannot read is a
        # register with no arcs, never a fire that dies declaring them.
        rows = None
        if isinstance(ents, dict):
            try:
                from entities_io import entities_collection
                rows = entities_collection(ents, name)
            except Exception:  # noqa: BLE001
                rows = ents.get(name)
        if isinstance(rows, dict):
            rows = list(rows.values())
        return [r for r in (rows or []) if isinstance(r, dict)]

    # SPEC DUALKEY1: `entities_collection(ents, "projects")` is now an alias
    # for the canonical `threads` list, so this used to double-count every
    # live thread once the alias landed (the pre-alias two-key loop existed
    # because "threads"/"projects" could each hold a disjoint slice; post-
    # alias they are the SAME list object). Single collection, single pass.
    live_threads = []
    for row in _coll("projects"):
        if str(row.get("status") or "").strip().lower() not in (
                "active", "scoping"):
            continue
        tid = row.get("id") or row.get("thread_id")
        if str(tid or "").strip():
            live_threads.append(row)

    # SPEC EODARC1 — ORG ARCS FIRST, so a row that reaches both the org and
    # its thread lands on the relationship. `affiliation_id` is the canonical
    # spelling, `org_id` the legacy one (`org_activity.thread_org_map` reads
    # both; so does this).
    org_threads: dict = {}
    for row in live_threads:
        tid = str(row.get("id") or row.get("thread_id") or "").strip()
        oid = str(row.get("affiliation_id") or row.get("org_id") or "").strip()
        if tid and oid:
            org_threads.setdefault(oid, []).append(tid)
    claimed: set = set()
    for org in _coll("orgs"):
        oid = str(org.get("id") or "").strip()
        if not oid:
            continue
        if str(org.get("relationship_type") or "").strip().lower() == "self":
            # The workspace itself is not a relationship; its threads are the
            # product tracks and stay workstream arcs below.
            continue
        tids = org_threads.get(oid) or []
        if not tids:
            continue
        _add(syn.ARC_ORG, oid,
             str(org.get("canonical_name") or org.get("name") or "").strip()
             or oid,
             thread_ids=tids)
        if (syn.ARC_ORG, oid) in seen:
            claimed.update(tids)

    for row in live_threads:
        tid = str(row.get("id") or row.get("thread_id") or "").strip()
        if tid in claimed:
            continue
        _add(syn.ARC_WORKSTREAM, tid,
             str(row.get("canonical_name") or row.get("name") or "").strip(),
             thread_id=tid)
    return arcs


def _people_names(workspace_root) -> dict:
    """`{person_id: display name}` through the canonical wrapper-aware read.

    SPEC EODARC1 — the arc read narrates who is WAITING on an unmoved
    commitment, and a person renders by name or not at all: a raw
    `person_...` id in prose is a leak of the substrate's plumbing into a
    sentence. Same defensive posture as `_entity_names`, one collection over.
    """
    out = {}
    try:
        from entities_io import entities_collection
        raw = json.loads((Path(workspace_root) / "_hq" / "data"
                          / "entities.json").read_text(encoding="utf-8"))
        rows = entities_collection(raw, "people")
    except Exception:  # noqa: BLE001 — a read never breaks a fire
        return out
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        name = str(row.get("canonical_name") or row.get("name") or "").strip()
        if pid and name:
            out[pid] = name
    return out


def open_commitment_rows(opens: Iterable[dict], *, workspace_root=None,
                         now_iso=None) -> list:
    """The OPEN BOOK, projected for the arc read (SPEC EODARC1). PURE over
    its arguments except for two register reads (names, timezone), both
    defensive.

    Takes the `load_open_commitments` projection the driver already holds and
    returns consequence-checkable rows:

        {"commitment_id", "title", "due", "overdue", "thread_id",
         "waiting_person", "counterparty_name", "blocker", "gates_meeting",
         "gates_meeting_id", "ts", "data"}

    Three rules, each load-bearing:

      * `overdue` is COMPUTED here, against the fire's own workspace-local
        day — never trusted from a stored flag, because the open book's rows
        were written on their own days and a staleness claim is a claim about
        NOW.
      * `counterparty_name` resolves through the entity register
        (`_people_names`); an id that does not resolve stays None and the
        person-waiting consequence simply does not fire for it. A sentence
        naming `person_0042` is worse than no sentence.
      * NOTHING here reads deal state (ruling 3). The projection is of
        commitments; a deal tracker in any condition — absent, stale,
        malformed — changes no field of it.

    `data` rides along whole so the §3.6 visibility fence
    (`eod_synthesis.visible_rows`) can still see `data.held` on the projected
    row: a held capture must be as invisible to the arc read as to every
    other surface.
    """
    today = None
    if workspace_root is not None:
        try:
            today = workspace_today(workspace_root, now=now_iso)
        except Exception:  # noqa: BLE001
            today = None
    if today is None and now_iso:
        parsed = _parse_iso(now_iso)
        today = parsed.date() if parsed is not None else None
    people = _people_names(workspace_root) if workspace_root is not None else {}
    out = []
    for ev in (opens or []):
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = data.get("id") or data.get("commitment_id")
        due = str(data.get("due") or data.get("due_date") or "").strip() or None
        overdue = False
        if due and today is not None:
            try:
                overdue = _dt.date.fromisoformat(due[:10]) < today
            except Exception:  # noqa: BLE001 — an unparseable date is no date
                overdue = False
        counterparty = (str(data.get("counterparty_name") or "").strip()
                        or people.get(str(data.get("counterparty_id") or ""))
                        or None)
        out.append({
            "commitment_id": str(cid) if cid else None,
            "title": str(data.get("title") or "").strip(),
            "due": due,
            "overdue": overdue,
            "thread_id": (ev.get("primary_thread_id") or data.get("thread_id")
                          or data.get("primary_thread_id")),
            "waiting_person": (str(data.get("waiting_person") or "").strip()
                               or None),
            "counterparty_name": counterparty,
            "blocker": (str(data.get("blocker") or data.get("blocked_on")
                            or "").strip() or None),
            "gates_meeting": (data.get("gates_meeting")
                              or data.get("blocks_meeting")),
            "gates_meeting_id": data.get("gates_meeting_id"),
            "ts": ev.get("ts"),
            "data": data,
        })
    return out


def unsourced_closes(closures: Iterable[dict]) -> list:
    """The closes in `closures` that cite no ARTIFACT — no email, meeting or
    message anyone can open.

    EOD1 acceptance: every close THIS FIRE writes carries a `source_ref`. That
    is asserted against this function rather than against a grep, because the
    pointer is a property of the written event and the only honest test is to
    read the events back.

    SPEC PROVMINT1 WIDENED THE TEST, and had to. The three close writers now
    mint `session:<surface>:<now>` when nothing reaches them, so "has no
    `source_ref`" stopped describing anything — every close would read as
    sourced and this function would return `[]` forever, which is the
    falsely-clean-zero shape: a check that cannot fail is not a check. A minted
    receipt is real provenance of the ACT and a poor substitute for the
    artifact this fire is supposed to be able to name, so it belongs on this
    list. The three cases, and they are one question asked three ways: the
    honest marker, no pointer at all, and a pointer the writer minted for want
    of a real one.
    """
    try:
        from connector_adapters.provenance import REF_GRAIN_SURFACE_MINTED
    except Exception:  # pragma: no cover — direct-path fallback
        REF_GRAIN_SURFACE_MINTED = "surface_minted"
    return [c for c in (closures or [])
            if c.get("provenance_missing") or not c.get("source_ref")
            or c.get("ref_grain") == REF_GRAIN_SURFACE_MINTED]


# ---------------------------------------------------------------------------
# Block: coverage — the aperture, stated (SPEC EODLEDGER1 part 1)
# ---------------------------------------------------------------------------

def _local_dt(workspace_root, value):
    """An instant in the WORKSPACE's own clock, or None. Never raises: a
    coverage strip that crashes on an odd cursor is a coverage strip that
    stops existing on exactly the nights it matters."""
    if not value:
        return None
    try:
        from tz import to_local
        return to_local(value, workspace_path=workspace_root)
    except Exception:  # noqa: BLE001
        return _parse_iso(value)


def _when_phrase(local, *, today) -> str:
    """How a cursor instant reads to a person. Same day → the time; an earlier
    day → the day, named. Never a bare ISO string: the whole point of this line
    is that a reader can tell at a glance whether the read is current."""
    if local is None:
        return ""
    clock = local.strftime("%I:%M %p").lstrip("0")
    if today is not None and local.date() == today:
        return f"{clock} today"
    return local.strftime("%A, %B ") + str(local.day)


def _days_phrase(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _leg_coverage(capability: str, leg, *, workspace_root, today,
                  gap_reason=None) -> dict:
    """ONE cursor-backed capability's coverage row.

    `leg` is the close phase's own receipt for that leg — the same dict
    `soften_floor` reads — so this states what the RUN did rather than what the
    workspace is configured to do. Four terminal shapes and they must not look
    alike: read and current, read but BEHIND (the line names the span), asked
    for and skipped (the plain-English reason, from the receipt or from
    `connector_gaps`), and never asked at all.
    """
    label = COVERAGE_LABELS.get(capability, capability.title())
    out = {"capability": capability, "read": False, "cursor": None,
           "stale": False, "degraded": False, "days_behind": None,
           "reason": None, "line": ""}

    if not isinstance(leg, dict) or not leg:
        out["reason"] = gap_reason or None
        out["line"] = (COVERAGE_NOT_READ.format(label=label,
                                                reason=out["reason"])
                       if out["reason"]
                       else COVERAGE_NOT_READ_NO_REASON.format(label=label))
        return out

    # THE FOUR SPELLINGS OF "IT DID NOT RUN", and all four are real. The chat
    # leg reports `status: "skipped"` (no backend declared) or
    # `status: "blocked"` (a backend that could not be read). The mail leg's
    # blocked receipt carries NO `status` at all — it is `ran: False` plus
    # `blocked: True` — so a reader that tested only `status` would take a
    # blocked mail run's unmoved cursor and print "read through <last week>",
    # which is the exact silence this block exists to remove.
    status = str(leg.get("status") or "").lower()
    ran = leg.get("ran")
    if status in ("skipped", "blocked") or leg.get("blocked") is True \
            or ran is False:
        # The reason, in the spellings the two legs actually use. `reason` is
        # last so a caller-supplied shape still works, and `connector_gaps` is
        # the floor beneath all of them.
        for key in ("skip_reason", "blocked_reason", "fetch_blocked",
                    "reason"):
            candidate = str(leg.get(key) or "").strip()
            if candidate:
                out["reason"] = candidate
                break
        out["reason"] = out["reason"] or gap_reason or None
        out["line"] = (COVERAGE_NOT_READ.format(label=label,
                                                reason=out["reason"])
                       if out["reason"]
                       else COVERAGE_NOT_READ_NO_REASON.format(label=label))
        return out

    # THE CURSOR READ. This one line is the whole claim the strip makes about
    # this capability, and it is read off the leg's OWN receipt — never from
    # the workspace's configuration, never from a default, and never from the
    # fire's own clock. `cursor_after` is where the leg finished; `cursor_before`
    # is where it started when it could not advance.
    raw_cursor = leg.get("cursor_after") or leg.get("cursor_before")
    out["read"] = True
    out["cursor"] = raw_cursor or None
    if not raw_cursor:
        # It ran and left no mark of how far it reached. That is not "current".
        out["line"] = COVERAGE_NEVER_ADVANCED.format(label=label)
        return out

    local = _local_dt(workspace_root, raw_cursor)
    if local is None:
        out["line"] = COVERAGE_NEVER_ADVANCED.format(label=label)
        return out

    when = _when_phrase(local, today=today)
    behind = (today - local.date()).days if today is not None else 0
    out["days_behind"] = max(0, behind)
    if behind >= CURSOR_STALE_DAYS:
        out["stale"] = True
        out["line"] = COVERAGE_STALE.format(label=label, when=when,
                                            n_days=_days_phrase(behind))
    else:
        out["line"] = COVERAGE_READ_THROUGH.format(label=label, when=when)
    # A run on the PARTIAL path is a real run and not a complete one, and the
    # leg's own receipt already says which it was. Without this clause a
    # per-conversation sweep renders identically to a full one.
    note = str(leg.get("coverage_note") or "").strip()
    if leg.get("degraded") and note:
        out["degraded"] = True
        out["line"] += COVERAGE_DEGRADED.format(note=note)
    return out


def capture_aperture(workspace_root, *, now_iso=None) -> dict:
    """How far back the capture leg is reaching, and what is still owed.

    Read ENTIRELY from the workspace's own ledger — `catchup.catchup_window`
    over this fire's receipt series, plus `meeting_discovery.unprocessed_backlog`
    — so it can be stated in Phase C, BEFORE the capture leg runs in Phase D.
    That ordering is not negotiable (capture-last is the circularity fence), and
    it is why this reports the APERTURE rather than the outcome: what the fire
    is about to look at, not what it found.

    Best-effort in every direction. A read that fails comes back `known: False`
    and the strip says the window could not be read, which is a different claim
    from "there is nothing there".
    """
    out = {"known": False, "start": None, "end": None, "start_aware": None,
           "days": None, "extended": False, "capped": False,
           "n_meetings": None, "n_backlog": None, "n_no_transcript": None,
           "error": None}
    try:
        from catchup import catchup_window
        # `now=now_iso` is not optional: without it this reads the machine
        # clock, and a fixture (or a corroborated CLOCK1 instant) would be
        # described by a window nobody asked for.
        window = catchup_window(workspace_root, TASK_ID, floor_hours=24,
                                cap_days=30, now=now_iso)
    except Exception as exc:  # noqa: BLE001 — a read never breaks a fire
        out["error"] = repr(exc)[:200]
        return out
    out.update({"known": True, "start": window.get("start"),
                "end": window.get("end"),
                # THE OFFSET-CARRYING instant is what gets rendered and what
                # gets compared. `start` is machine-local NAIVE, and handing a
                # naive value to `to_local` (which assumes naive means UTC)
                # names the wrong day on any box that is not on UTC.
                "start_aware": window.get("start_aware"),
                "days": window.get("days"),
                "extended": bool(window.get("extended")),
                "capped": bool(window.get("capped"))})

    since = _parse_iso(window.get("start_aware"))
    if since is not None:
        n = 0
        for ev in _load_events(workspace_root):
            if ev.get("type") != "meeting":
                continue
            ts = _parse_iso(ev.get("ts"))
            if ts is not None and ts >= since:
                n += 1
        out["n_meetings"] = n

    try:
        from meeting_discovery import unprocessed_backlog
        sweep = unprocessed_backlog(workspace_root, now=now_iso or None)
        out["n_backlog"] = sweep.get("n_candidates")
        out["n_no_transcript"] = sweep.get("n_no_transcript")
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)[:200]
    return out


def compute_coverage(workspace_root, *, close_result=None,
                     calendar_available: bool = False,
                     connector_gaps=None, now_iso=None,
                     n_unsourced: int = 0, capture=None) -> dict:
    """THE COVERAGE STRIP. What this fire actually read, per capability.

    Composed here and rendered VERBATIM. Placed first under `alarm_lines`,
    never suppressed and never softened — the same posture the alarms keep, and
    for the same reason: a degraded read is exactly when the reader needs to
    know what the aperture was.

    `calendar_available` MUST be the `tomorrow` block's own value, not a second
    derivation. "The calendar was not read" and "tomorrow is empty" are the two
    statements that rendered identically before this spec, and the fence that
    keeps them consistent is that they come from ONE boolean rather than from
    two readers who might disagree.

    `connector_gaps` is the fire's existing receipt field, unchanged: the gap
    stops being audit-only and becomes the sentence the reader sees, in the
    same plain English the leg already recorded.

    Returns `{"lines": [...], "capabilities": {...}, "note": str|None,
    "n_unsourced": int}`. `lines` is the render contract; the structured half
    is there so a test can pin a claim rather than a substring.
    """
    result = close_result or {}
    gaps = [g for g in (connector_gaps or [])]

    def _gap_reason(capability):
        """The plain-English reason the fire already receipted for this leg."""
        for g in gaps:
            if isinstance(g, dict):
                if str(g.get("capability") or g.get("leg") or "").lower() \
                        == capability:
                    return str(g.get("reason") or "").strip() or None
            elif isinstance(g, str) and g.lower().startswith(capability):
                tail = g.split(":", 1)[1].strip() if ":" in g else ""
                return tail or None
        return None

    try:
        today = workspace_today(workspace_root, now=now_iso)
    except Exception:  # noqa: BLE001
        today = None

    caps = {}
    lines = []
    for capability in (CAP_MAIL, CAP_CHAT):
        row = _leg_coverage(capability, result.get(capability),
                            workspace_root=workspace_root, today=today,
                            gap_reason=_gap_reason(capability))
        caps[capability] = row
        lines.append(row["line"])

    cal_reason = None if calendar_available else _gap_reason(CAP_CALENDAR)
    if calendar_available:
        cal_line = COVERAGE_CALENDAR_READ
    elif cal_reason:
        cal_line = COVERAGE_CALENDAR_ABSENT_REASON.format(reason=cal_reason)
    else:
        cal_line = COVERAGE_CALENDAR_ABSENT
    cal = {"capability": CAP_CALENDAR, "read": bool(calendar_available),
           "reason": cal_reason, "line": cal_line}
    caps[CAP_CALENDAR] = cal
    lines.append(cal["line"])

    ap = capture if isinstance(capture, dict) else {}
    if ap.get("known"):
        since_local = _local_dt(workspace_root, ap.get("start_aware")
                                or ap.get("start"))
        since = (_when_phrase(since_local, today=today) if since_local
                 else str(ap.get("start") or ""))
        days = ap.get("days")
        span = _days_phrase(int(round(days))) if isinstance(days, (int, float)) \
            else "the nominal window"
        # An UNREAD count is not zero (review fix-round 1). Each half names
        # itself, so a backlog sweep that raised over a window that resolved
        # fine cannot print "0 still waiting to be processed".
        n_meetings, n_backlog = ap.get("n_meetings"), ap.get("n_backlog")
        counts = ", ".join((
            COVERAGE_MEETINGS_N.format(n_meetings=n_meetings)
            if isinstance(n_meetings, int) and not isinstance(n_meetings, bool)
            else COVERAGE_MEETINGS_N_UNREAD,
            COVERAGE_MEETINGS_BACKLOG.format(n_backlog=n_backlog)
            if isinstance(n_backlog, int) and not isinstance(n_backlog, bool)
            else COVERAGE_MEETINGS_BACKLOG_UNREAD,
        ))
        meetings_line = COVERAGE_MEETINGS.format(since=since, span=span,
                                                 counts=counts)
        backlog_clause = (
            COVERAGE_MEETINGS_BACKLOG.format(n_backlog=n_backlog)
            if isinstance(n_backlog, int) and not isinstance(n_backlog, bool)
            else COVERAGE_MEETINGS_BACKLOG_UNREAD)
    else:
        meetings_line = COVERAGE_MEETINGS_UNKNOWN
        since = span = None
        backlog_clause = None
    # `since` / `span` / `backlog_clause` are STASHED for one caller:
    # `reconcile_meetings_line` (SPEC MEETCOUNT1) rebuilds this row's sentence
    # from the render set after the capture leg has run, and it must keep the
    # window phrasing and the backlog half EXACTLY as this fire computed them
    # — recomputing either there would be a second producer for a sentence
    # whose whole point is having one.
    caps[CAP_MEETINGS] = {"capability": CAP_MEETINGS,
                          "read": bool(ap.get("known")),
                          "aperture": ap or None, "line": meetings_line,
                          "since": since, "span": span,
                          "backlog_clause": backlog_clause}
    lines.append(meetings_line)

    try:
        n_unsourced = max(0, int(n_unsourced))
    except (TypeError, ValueError):
        n_unsourced = 0
    note = COVERAGE_UNSOURCED.format(n=n_unsourced) if n_unsourced else None
    if note:
        lines.append(note)

    return {"lines": [l for l in lines if l], "capabilities": caps,
            "note": note, "n_unsourced": n_unsourced}


# ---------------------------------------------------------------------------
# SPEC MEETCOUNT1 — the coverage line reconciles with its own render
# ---------------------------------------------------------------------------
#
# THE DEFECT. The meetings line's count came off `capture_aperture` — a
# Phase-C read of the ledger's own `meeting` events — while the briefs the
# same screen renders come off the capture leg's discovery, a Phase-D
# outcome. Two producers, one sentence apart: the live fire said "1 on
# record in that span" above two rendered briefs, on a day whose backend
# held three meetings. A count the same screen disproves is worse than no
# count.
#
# THE RULE. The stated count and the rendered briefs derive from ONE
# producer — `meeting_render_set`, one row per meeting the capture leg found
# in the window — consumed by both, so they cannot diverge. And anything
# that reduces the displayed number (an already-processed exclusion, a
# duplicate fold, a deliberate skip, a failed brief save) is said IN THE
# SAME SENTENCE. Silent reduction is the bug, whatever the mechanism: a row
# whose status this fire cannot name is still a NAMED reduction ("not
# briefed for a reason this fire did not state"), never a quiet subtraction.
#
# WHAT THIS IS NOT. Meeting dedup itself is MEETDUP1's build, not this one.
# This spec owns only the reconciliation of the stated count to the rendered
# set — when an upstream fold happens, the fold is NAMED here, and nothing
# here decides what to fold.

# The statuses a render-set row may carry. `briefed` is the rendered lane;
# every other status is a REDUCTION and gets a clause in the sentence. A
# status outside this vocabulary folds to `unstated` — visible, never silent.
MEETING_BRIEFED = "briefed"
# SPEC EODSPEED1 — a meeting the incremental capture pass (the silent
# `meeting-capture` maintenance job) already processed, whose brief is on
# disk. It COUNTS AS BRIEFED — the brief renders, the ref rides
# `briefed_refs` — because the pass is silent by fence and the close is the
# one narrator: folding these into `already_processed` would make the day's
# briefs reach nobody. The counts clause still NAMES them ("captured earlier
# by the background pass"), because a number that moved producer mid-day is a
# number the sentence must explain.
MEETING_BRIEFED_PRIOR = "briefed_prior"
MEETING_ALREADY_PROCESSED = "already_processed"
MEETING_DUPLICATE_FOLDED = "duplicate_folded"
MEETING_SKIPPED = "skipped"
MEETING_BRIEF_FAILED = "brief_failed"
MEETING_UNSTATED = "unstated"
MEETING_RENDER_STATUSES = (
    MEETING_BRIEFED, MEETING_BRIEFED_PRIOR, MEETING_ALREADY_PROCESSED,
    MEETING_DUPLICATE_FOLDED, MEETING_SKIPPED, MEETING_BRIEF_FAILED,
    MEETING_UNSTATED)

# EODSPEED1 — the briefed-prior clause, singular and plural. NOT a member of
# MEETING_REDUCTION_LABELS: these rows are IN `n_briefed`, so putting them in
# the reductions dict would break the render-set invariant. They get their
# own clause inside the same parenthetical instead.
MEETING_BRIEFED_PRIOR_LABELS = (
    "{n} captured earlier by the background pass",
    "{n} captured earlier by the background pass")

# THE WORDS for each reduction, singular and plural, rendered inside one
# parenthetical: "(1 duplicate capture folded, 1 already processed)". Order
# here is render order.
MEETING_REDUCTION_LABELS = {
    MEETING_DUPLICATE_FOLDED: ("{n} duplicate capture folded",
                               "{n} duplicate captures folded"),
    MEETING_ALREADY_PROCESSED: ("{n} already processed",
                                "{n} already processed"),
    MEETING_SKIPPED: ("{n} deliberately skipped",
                      "{n} deliberately skipped"),
    MEETING_BRIEF_FAILED: ("{n} brief could not be saved",
                           "{n} briefs could not be saved"),
    MEETING_UNSTATED: ("{n} not briefed for a reason this fire did not state",
                       "{n} not briefed for a reason this fire did not state"),
}

# The reconciled counts clause. The braces are filled by `meeting_render_set`
# and the whole clause replaces `COVERAGE_MEETINGS_N` in the sentence — the
# two claims ("on record" and "briefed") always travel together, because the
# gap between them is exactly what the reductions parenthetical explains.
COVERAGE_MEETINGS_RECONCILED = "{n_on_record} on record, {n_briefed} briefed"
# The reconciled sentence when the capture window itself could not be read:
# the window claim stays honest (unknown) while the counts still reconcile
# with the render, because the render happened whether or not the cursor
# could be read.
COVERAGE_MEETINGS_RECONCILED_NO_WINDOW = (
    "Meetings: the capture window could not be read, so this fire cannot say "
    "how far back it looked; of what it found, {counts}.")


def meeting_render_set(rows) -> dict:
    """THE ONE PRODUCER (SPEC MEETCOUNT1). One row per meeting the capture
    leg found in the window; the coverage line's counts AND the Meeting
    briefs section both consume this return, so they cannot diverge.

    `rows`: iterable of `{"source_ref": str, "status": str}` — one per
    meeting the discovery leg returned for the span, status per
    `MEETING_RENDER_STATUSES`. A missing or unrecognized status folds to
    `unstated`, which renders as its own named reduction: a row this fire
    cannot explain still moves the stated arithmetic in the open.

    Returns `{"known": True, "n_on_record", "n_briefed", "reductions"
    (ordered {status: n}, only non-zero, never `briefed`), "briefed_refs"
    (input order), "counts_clause" (the sentence fragment both the line and
    a test can pin)}`. Invariant, by construction and asserted anyway:
    n_on_record == n_briefed + sum(reductions.values()).
    """
    n_briefed = 0
    n_briefed_prior = 0
    briefed_refs = []
    tallies = {s: 0 for s in MEETING_RENDER_STATUSES}
    n_on_record = 0
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        n_on_record += 1
        status = str(row.get("status") or "").strip().lower()
        if status not in MEETING_RENDER_STATUSES:
            status = MEETING_UNSTATED
        if status in (MEETING_BRIEFED, MEETING_BRIEFED_PRIOR):
            # EODSPEED1 — briefed-prior rows ARE briefed: the brief renders
            # and the ref rides briefed_refs. They are tallied separately so
            # the sentence can name where they came from.
            n_briefed += 1
            if status == MEETING_BRIEFED_PRIOR:
                n_briefed_prior += 1
            ref = str(row.get("source_ref") or "").strip()
            briefed_refs.append(ref)
        else:
            tallies[status] += 1
    reductions = {s: tallies[s] for s in MEETING_REDUCTION_LABELS
                  if tallies[s]}
    assert n_on_record == n_briefed + sum(reductions.values())
    clause = COVERAGE_MEETINGS_RECONCILED.format(
        n_on_record=n_on_record, n_briefed=n_briefed)
    parts = []
    if n_briefed_prior:
        one, many = MEETING_BRIEFED_PRIOR_LABELS
        parts.append((one if n_briefed_prior == 1 else many).format(
            n=n_briefed_prior))
    for status, n in reductions.items():
        one, many = MEETING_REDUCTION_LABELS[status]
        parts.append((one if n == 1 else many).format(n=n))
    if parts:
        clause += " (" + ", ".join(parts) + ")"
    return {"known": True, "n_on_record": n_on_record,
            "n_briefed": n_briefed, "n_briefed_prior": n_briefed_prior,
            "reductions": reductions,
            "briefed_refs": briefed_refs, "counts_clause": clause}


def reconcile_meetings_line(coverage, render_set) -> dict:
    """Rebuild the coverage strip's meetings sentence from the render set —
    called by the fire AFTER the capture leg has run and BEFORE the receipt
    is logged, so the strip the reader sees and the strip the receipt
    carries both state the numbers the render can back.

    The Phase-C aperture line was a statement about what the fire was ABOUT
    to look at; this replaces its count with what the fire found and
    rendered, from `meeting_render_set` — the same producer the Meeting
    briefs section draws its rows from. The window phrasing and the backlog
    half are kept EXACTLY as `compute_coverage` computed them (they are
    stashed on the meetings row for this call), because this function's one
    job is to change which producer the COUNT reads, not to re-derive the
    window.

    Returns a NEW coverage dict; the input is not mutated. A malformed
    `coverage` or `render_set` returns the input unchanged — a reconcile
    that cannot run must not eat the strip.
    """
    if not isinstance(coverage, dict) or not isinstance(render_set, dict) \
            or not render_set.get("known"):
        return coverage
    caps = coverage.get("capabilities")
    if not isinstance(caps, dict) or CAP_MEETINGS not in caps:
        return coverage
    row = dict(caps[CAP_MEETINGS] or {})
    old_line = row.get("line")
    # THE ONE PRODUCER: the counts clause comes off the render set, verbatim.
    counts = render_set["counts_clause"]
    backlog_clause = row.get("backlog_clause")
    if backlog_clause:
        counts = ", ".join((counts, backlog_clause))
    if row.get("since") is not None and row.get("span") is not None:
        new_line = COVERAGE_MEETINGS.format(since=row["since"],
                                            span=row["span"], counts=counts)
    else:
        new_line = COVERAGE_MEETINGS_RECONCILED_NO_WINDOW.format(counts=counts)
    row["line"] = new_line
    row["render_set"] = render_set
    out = dict(coverage)
    out["capabilities"] = dict(caps)
    out["capabilities"][CAP_MEETINGS] = row
    out["lines"] = [new_line if l == old_line else l
                    for l in (coverage.get("lines") or [])]
    return out


# ---------------------------------------------------------------------------
# SPEC COVERQUIET1 — coverage speaks only when it has something to say
# ---------------------------------------------------------------------------
#
# THE FENCE THIS MUST NOT BREAK. The strip exists because of MEETCOUNT1 /
# CATCHUP1 F-1: never subtract a meeting from a stated count without its
# clause; silent reduction is the bug. EODLEDGER1's own posture — "NEVER
# SUPPRESSED AND NEVER SOFTENED" — was right for every day the strip has
# something to say and wrong for the days it does not: "5 meetings on
# record, 4 processed, all current" on an ordinary day is the extra garbage
# M's live-walk intake named (2026-08-25/26). Quiet is only honest when
# there is NOTHING TO DISCLOSE — the gate below is what tells the two apart,
# and it is deliberately narrower than "the day looked ordinary": a day can
# be ordinary in every other respect and still owe a disclosure.
#
# THE FIVE-ITEM LIST (§0 ruling 1), read verbatim off the pack and nothing
# else:
#   1. a REDUCTION clause   — `meeting_render_set`'s own non-briefed tallies
#                              on the RECONCILED meetings row (duplicate
#                              fold, already-processed exclusion, deliberate
#                              skip, a brief that could not be saved, or an
#                              unstated drop).
#   2. a DEFERRAL            — `window_incomplete_before` is set. Read
#                              generically off the pack: whatever wrote it
#                              (today's past-meetings batch-cap marker, or
#                              CAPFENCE1's fence once that spec lands) uses
#                              the SAME field, `catchup.WINDOW_INCOMPLETE_
#                              FIELD`, so this reads the field, never a
#                              producer.
#   3. a TASKALARM1 dark-surface line — `dark_surface_lines` non-empty.
#   4. a CONNECTOR GAP       — `connector_gaps` non-empty: a leg this fire
#                              asked for and could not read.
#   5. a CATCH-UP / DEGRADE note — SPEC EODLEDGER1 part 3's labelled span,
#                              `catchup["renders"]` True.
#
# ANY ONE of the five is enough — this is an OR, not a weighting. And the
# HONESTY FLOOR (§Acceptance's own name for it): a reduction, however small
# or ordinary its cause, MUST return True on its own. That is the load-
# bearing pin of this whole build — a reduction with the strip suppressed IS
# the silent-reduction bug, MEETCOUNT1/CATCHUP1 F-1 restated one gate up.
#
# `pack["window_incomplete_before"]` is NOT written by `build_end_of_day_pack`
# — Phase C runs before the capture leg, and the marker is only knowable
# after it. The orchestrator folds it onto the pack in Phase 5, the same
# instant it already reads the value to build `capture_leg` for the receipt,
# and BEFORE this predicate (or the render decision) is consulted. See
# orchestrator-past-meetings.md Phase 5.

COVERAGE_DISCLOSURE_REDUCTION_LEAD = "Meetings: {clause}."
COVERAGE_DISCLOSURE_DEFERRAL_LEAD_ONE = "1 meeting deferred to tonight's pass."
COVERAGE_DISCLOSURE_DEFERRAL_LEAD = "{n} meetings deferred to tonight's pass."
COVERAGE_DISCLOSURE_DEFERRAL_LEAD_UNKNOWN = (
    "Some of today's meetings are deferred to tonight's pass.")
COVERAGE_DISCLOSURE_CONNECTOR_GAP_LEAD = (
    "A connector this fire needed was not read.")
# SPEC CAPFENCE1 — the close's own narration for a TIME-fence-triggered
# defer (§0 item 3), distinct from the generic batch-cap lead above: it
# names WHERE the rest went (tonight's background pass — CAPSLOT1's
# incremental capture, running again before tomorrow's close) and WHEN the
# reader sees them (MORNCAP1's morning narration), which the generic lead
# names neither.
COVERAGE_DISCLOSURE_FENCE_LEAD_ONE = (
    "1 meeting deferred to tonight's background pass — "
    "tomorrow's brief will carry it.")
COVERAGE_DISCLOSURE_FENCE_LEAD = (
    "{n} meetings deferred to tonight's background pass — "
    "tomorrow's brief will carry them.")


def coverage_has_disclosure(pack: dict) -> bool:
    """SPEC COVERQUIET1 — THE gate, and the ONLY place render intent for
    `coverage` is decided. True iff the pack carries at least one of the
    five §0.1 disclosures above; False on a genuinely clean day. Both the
    orchestrator's Phase 6 render bullet and `log_end_of_day_receipt`'s
    `blocks_rendered` / `blocks_computed_only` split call THIS function —
    nothing re-derives the answer from a shortcut (a stale cursor alone, an
    unsourced-close count alone, a non-empty `coverage["lines"]` alone are
    all deliberately NOT on the list: they are ordinary detail, not a
    disclosure, and folding them in would make an unremarkable day noisy
    again by a different door).

    PURE and best-effort: a malformed or partial pack reads as "nothing
    disclosed in what is readable" rather than raising. A gate that cannot
    evaluate itself must not crash the fire over it — the same posture every
    other read in this module takes, and specifically NOT the direction a
    silent-reduction bug could hide in, because the one signal this function
    is not allowed to miss (the reduction tally) is read off a plain dict
    walk with no external I/O to fail.
    """
    if not isinstance(pack, dict):
        return False

    # 1. REDUCTION — the meetings capability's own reconciled render_set.
    # THE HONESTY FLOOR: this branch alone must return True on any non-zero
    # reduction, full stop (§Acceptance's mutation-by-removal pin targets
    # exactly this branch).
    coverage = pack.get("coverage")
    if isinstance(coverage, dict):
        caps = coverage.get("capabilities")
        if isinstance(caps, dict):
            meetings = caps.get(CAP_MEETINGS)
            if isinstance(meetings, dict):
                render_set = meetings.get("render_set")
                if isinstance(render_set, dict):
                    reductions = render_set.get("reductions")
                    if isinstance(reductions, dict) and any(
                            isinstance(n, int) and not isinstance(n, bool)
                            and n > 0
                            for n in reductions.values()):
                        return True

    # 2. DEFERRAL — window_incomplete_before, however it was produced.
    if pack.get("window_incomplete_before"):
        return True

    # 3. TASKALARM1 — a dead-surface line this fire is carrying.
    if pack.get("dark_surface_lines"):
        return True

    # 4. CONNECTOR GAP — a leg this fire asked for and could not read.
    if pack.get("connector_gaps"):
        return True

    # 5. CATCH-UP / DEGRADE note — SPEC EODLEDGER1 part 3's labelled span.
    catchup = pack.get("catchup")
    if isinstance(catchup, dict) and (catchup.get("renders")
                                       or catchup.get("lines")):
        return True

    return False


def coverage_disclosure_lead(pack: dict) -> Optional[str]:
    """THE disclosure-first sentence (§0 ruling 3) — "1 meeting deferred to
    tonight's pass", never "5 meetings on record, 4 processed, 1 deferred":
    the count survives only inside the ONE clause that needs it. Checked in
    the SAME priority order `coverage_has_disclosure` checks, and the first
    category actually present supplies the lead — a day can carry more than
    one disclosure and the strip still leads with exactly one sentence.

    Every branch either composes from a fixed template plus a bare count (no
    names, nothing this module has not already leak-scanned once) or reuses
    a string `compute_coverage` / `dark_surface_lines` / `compute_catchup_read`
    already composed and the orchestrator already scanned — this function
    never introduces new free text.

    Returns `None` when nothing is disclosed. The caller's own
    `coverage_has_disclosure` is still the render decision; this only
    composes the sentence for a day that already cleared it.
    """
    if not isinstance(pack, dict):
        return None

    coverage = pack.get("coverage")
    caps = coverage.get("capabilities") if isinstance(coverage, dict) else None
    meetings = caps.get(CAP_MEETINGS) if isinstance(caps, dict) else None

    # 1. REDUCTION.
    render_set = meetings.get("render_set") if isinstance(meetings, dict) \
        else None
    reductions = render_set.get("reductions") if isinstance(render_set, dict) \
        else None
    if isinstance(reductions, dict):
        parts = []
        for reduction_status, reduction_n in reductions.items():
            if not isinstance(reduction_n, int) \
                    or isinstance(reduction_n, bool) or reduction_n <= 0:
                continue
            labels = MEETING_REDUCTION_LABELS.get(reduction_status)
            if not labels:
                continue
            one, many = labels
            parts.append((one if reduction_n == 1 else many)
                         .format(n=reduction_n))
        if parts:
            return COVERAGE_DISCLOSURE_REDUCTION_LEAD.format(
                clause=", ".join(parts))

    # 2. DEFERRAL.
    if pack.get("window_incomplete_before"):
        # SPEC CAPFENCE1 — a TIME-fence-triggered defer carries its own
        # count (`n_time_fence_deferred`, folded onto the pack in Phase 5
        # exactly like `window_incomplete_before` itself) and its own lead.
        # Checked FIRST: a fence-triggered defer is always also a
        # `window_incomplete_before` defer (Ruling 2 — same machinery), so
        # without this branch the generic batch-cap wording below would
        # silently absorb it and the MORNCAP1 pointer would never render.
        n_fence = pack.get("n_time_fence_deferred")
        if isinstance(n_fence, int) and not isinstance(n_fence, bool) \
                and n_fence > 0:
            return (COVERAGE_DISCLOSURE_FENCE_LEAD_ONE if n_fence == 1
                    else COVERAGE_DISCLOSURE_FENCE_LEAD.format(n=n_fence))
        n_backlog = None
        aperture = meetings.get("aperture") if isinstance(meetings, dict) \
            else None
        if isinstance(aperture, dict):
            n_backlog = aperture.get("n_backlog")
        if isinstance(n_backlog, int) and not isinstance(n_backlog, bool) \
                and n_backlog > 0:
            return (COVERAGE_DISCLOSURE_DEFERRAL_LEAD_ONE if n_backlog == 1
                    else COVERAGE_DISCLOSURE_DEFERRAL_LEAD.format(
                        n=n_backlog))
        return COVERAGE_DISCLOSURE_DEFERRAL_LEAD_UNKNOWN

    # 3. TASKALARM1 — reuse the dark line verbatim; it is already scanned.
    dark = pack.get("dark_surface_lines")
    if dark:
        return dark[0]

    # 4. CONNECTOR GAP — reuse the strip's own NOT-READ sentence for the
    # first gapped capability, so the lead names the same reason the detail
    # repeats rather than composing a second wording for one fact.
    if pack.get("connector_gaps"):
        if isinstance(caps, dict):
            for cap_name in COVERAGE_CAPABILITIES:
                row = caps.get(cap_name)
                if isinstance(row, dict) and row.get("read") is False \
                        and row.get("line"):
                    return row["line"]
        return COVERAGE_DISCLOSURE_CONNECTOR_GAP_LEAD

    # 5. CATCH-UP / DEGRADE — reuse the label; it is `catchup["lines"][0]`,
    # already scanned as part of `catchup.lines`.
    catchup = pack.get("catchup")
    if isinstance(catchup, dict) and catchup.get("renders") \
            and catchup.get("label"):
        return catchup["label"]

    return None


def coverage_render_lines(pack: dict) -> list:
    """The coverage strip AS RENDERED (SPEC COVERQUIET1) — `[]` on a quiet
    day, else the disclosure-first lead followed by every line
    `compute_coverage` / `reconcile_meetings_line` already composed,
    unchanged.

    THE GATE IS ALL-OR-NOTHING BY DESIGN. EODLEDGER1's rule — the strip is
    one unit, never suppressed and never softened ONCE it is showing — is
    still in force; this build adds only the day-level on/off switch on top
    of it. It does not turn per-capability suppression on: a day with one
    reduction still shows the mail/chat/calendar detail alongside it, exactly
    as before.
    """
    if not coverage_has_disclosure(pack):
        return []
    coverage = pack.get("coverage") if isinstance(pack, dict) else None
    lines = list(coverage.get("lines") or []) if isinstance(coverage, dict) \
        else []
    lead = coverage_disclosure_lead(pack)
    if lead:
        # A lead borrowed verbatim from an existing line (a dark surface, a
        # blocked capability) must not repeat itself as the strip's second
        # line.
        lines = [lead] + [l for l in lines if l != lead]
    return lines


# ---------------------------------------------------------------------------
# Block: score
# ---------------------------------------------------------------------------

# The three things this surface may say about the morning's suggested first
# move, and there is no fourth. `stale` is a claim about the RECORD (something
# closed today that this line names), never about the CEO.
FIRST_MOVE_OPEN = "open"
FIRST_MOVE_STALE = "stale"
FIRST_MOVE_UNVERIFIABLE = "unverifiable"
FIRST_MOVE_STATUSES = (FIRST_MOVE_OPEN, FIRST_MOVE_STALE,
                       FIRST_MOVE_UNVERIFIABLE)


def check_first_move(text, closures: Iterable[dict], *,
                     close_legs: Optional[Iterable[dict]] = None) -> Optional[dict]:
    """The morning's "Suggested first move", ANNOTATED (SPEC EODFIX1 §0-4).

    `compute_score` used to copy this line out of the morning digest verbatim
    with zero checks, so an instruction the CEO discharged at 10 AM was handed
    back at 9 PM as though it were still the next thing to do — twice, on
    2026-08-17, byte-for-byte identical in both packs.

    Returns `{text, status, checked_against, matched}`, or None when the brief
    printed no first move (nothing is ever invented here).

      `stale`         a closure recorded TODAY names this line. Context about
                      this morning's plan, never tonight's instruction.
      `open`          the day's closures were readable and none of them is
                      this. The line stands.
      `unverifiable`  there was nothing to check against — no closure on file
                      AND no close leg that advanced. Absence of a match is
                      not evidence, and saying "open" here would be a guess
                      wearing a measurement's clothes (the F-29 receipts
                      doctrine, applied one field down).

    The match is `_shares_words` — deliberately crude, exactly as the tomorrow
    proposal's ordering is. It decides a LABEL on a line the CEO reads, never a
    write, and a false `stale` is a milder failure than a confident `open` on
    work that is already done.
    """
    line = str(text or "").strip()
    if not line:
        return None
    rows = [c for c in (closures or []) if isinstance(c, dict)]
    named = [c for c in rows if str(c.get("title") or "").strip()]
    advanced = [l.get("leg") for l in (close_legs or [])
                if isinstance(l, dict) and l.get("advanced")]

    matched = None
    for c in named:
        if _shares_words(line, str(c.get("title"))):
            matched = {"commitment_id": c.get("commitment_id"),
                       "title": c.get("title"), "ts": c.get("ts"),
                       "source_ref": c.get("source_ref")}
            break

    if matched is not None:
        status = FIRST_MOVE_STALE
    elif named or advanced:
        status = FIRST_MOVE_OPEN
    else:
        status = FIRST_MOVE_UNVERIFIABLE

    return {
        "text": line,
        "status": status,
        "checked_against": {"closures": len(rows), "named_closures": len(named),
                            "close_legs_advanced": sorted(advanced)},
        "matched": matched,
    }


# How far either side of the morning fire's own instant this reader will look
# for the `brief_state` that fire wrote. The driver logs it moments before the
# receipt, so the two are seconds apart in practice; two hours is slack for a
# slow fire and is still far too narrow to reach yesterday's or this evening's.
OPENING_FIGURE_TOLERANCE_MINUTES = 120

# The one origin an opening figure may be read from. The evening fire writes NO
# `brief_state` of its own (deliberately — see the module docstring), and the
# on-demand surfaces write theirs under their own skill names, so keying on
# this is what keeps "the book at open" meaning the MORNING's book.
OPENING_FIGURE_ORIGIN = "morning-briefing"


def opening_book(workspace_root, morning: dict, *, now_iso=None) -> dict:
    """The open book AS THIS MORNING'S FIRE MEASURED IT. Read, never inferred.

    Returns `{"found", "total", "ts", "headline"}`. `found` False is the only
    correct answer when the morning fire did not run or left no `brief_state`
    behind, and it is NEVER converted into a number: today's count standing in
    for this morning's would render a delta of zero on precisely the day the
    delta is unknowable, which is the `no_plan` failure one field down.

    WHY `brief_state` AND NOT THE `pack_run` RECEIPT. The receipt carries the
    numbered ids and the digest path; the counts live on the `brief_state`
    audit event the same fire wrote seconds earlier, straight out of
    `compute_brief_state` rather than out of anything a model typed (Bug #99's
    whole point). This reader anchors on the receipt's instant and reads the
    counts off the event — the receipt is still what says a morning fire
    happened at all.
    """
    out = {"found": False, "total": None, "ts": None, "headline": None}
    if not (morning or {}).get("found"):
        return out
    anchor = _parse_iso(morning.get("ts"))
    if anchor is None:
        return out
    tolerance = _dt.timedelta(minutes=OPENING_FIGURE_TOLERANCE_MINUTES)

    best, best_gap = None, None
    for ev in _load_events(workspace_root):
        if ev.get("type") != "brief_state":
            continue
        if str(ev.get("source_skill") or "") != OPENING_FIGURE_ORIGIN:
            continue
        ts = _parse_iso(ev.get("ts"))
        if ts is None:
            continue
        gap = abs(ts - anchor)
        if gap > tolerance:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = ev, gap
    if best is None:
        return out
    data = best.get("data") if isinstance(best.get("data"), dict) else {}
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    headline = counts.get("headline") if isinstance(counts.get("headline"),
                                                    dict) else {}
    total = counts.get("total")
    if not isinstance(total, int) or isinstance(total, bool):
        # A half-written event is not an opening figure. Degrade to "no opening
        # figure on record" rather than to a partial number.
        return out
    out.update({"found": True, "total": total, "ts": best.get("ts"),
                "headline": headline})
    return out


def opens_since(workspace_root, since_ts, *, now_iso=None) -> dict:
    """What ENTERED the open book in this fire's window.

    `{"n": int, "window_source": str, "since": iso}` — the same `_window`
    resolution `closures_since` uses, deliberately, so the ledger's two sides
    are measured over one span. A ledger whose opens and closes came from
    different windows would balance by accident or not at all.

    Counts only what the book counts: `pending_review` extractions are the
    needs-your-call queue rather than the open book, and a sub-item is a step
    of a promise rather than another one (`commitment_state`'s own partition).
    """
    since, window_source = _window(workspace_root, since_ts, now_iso=now_iso)
    until = _parse_iso(now_iso) if now_iso else None
    n = 0
    for ev in _load_events(workspace_root):
        if ev.get("type") != "commitment":
            continue
        ts = _parse_iso(ev.get("ts"))
        if ts is None or ts < since:
            continue
        if until is not None and ts > until:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("pending_review"):
            continue
        if data.get("parent_id"):
            continue
        n += 1
    return {"n": n, "window_source": window_source, "since": since.isoformat()}


def compute_ledger(*, opening: dict, n_opened: int,
                   closures: Iterable[dict], brief_state: dict) -> dict:
    """THE LEDGER (SPEC EODLEDGER1 part 2). What the day did to the open book.

    Book at open → `+opened` → `−closed` → `−dropped` → book now, with the
    delta named and its sign meaningful. Every input already exists on this
    fire: the opening figure off the morning's own `brief_state`, the two close
    kinds out of `closures_since`'s `resolution`, and the closing figure out of
    the `brief_state` this driver already computes and discards.

    NO OPENING FIGURE → NO ARITHMETIC. Not a zero, not a delta of zero, not a
    "book now" presented as though it were movement: the block says *"no
    opening figure on record"*, renders the closes it can count, and the delta
    key is `None`. The suite pins the ABSENCE, not merely the presence of the
    line — a fabricated baseline is indistinguishable from a measured one once
    it is on screen.

    THE RESIDUAL IS NAMED, NEVER ABSORBED. The four movements will not always
    account for the whole change (a confirm out of the review queue enters the
    book without being opened; a merge retires two rows into one), and a ledger
    that silently folded the difference into "closed" would be a ledger that
    lies quietly. When the parts do not add up, the block says by how much and
    stops there.
    """
    rows = [c for c in (closures or []) if isinstance(c, dict)]
    n_dropped = sum(1 for c in rows
                    if str(c.get("resolution") or "").lower() == "dropped")
    n_closed = len(rows) - n_dropped
    try:
        n_opened = max(0, int(n_opened))
    except (TypeError, ValueError):
        n_opened = 0

    headline = ((brief_state or {}).get("headline")
                if isinstance((brief_state or {}).get("headline"), dict)
                else {})
    book_now = headline.get("total")
    if not isinstance(book_now, int) or isinstance(book_now, bool):
        book_now = None

    out = {
        "status": LEDGER_NO_OPENING_FIGURE,
        "book_at_open": None,
        "n_opened": n_opened,
        "n_closed": n_closed,
        "n_dropped": n_dropped,
        "book_now": book_now,
        "delta": None,
        "movement_line": None,
        "residual": None,
        "residual_line": None,
        "reconciles": None,
        "headline": headline,
        "line": NO_OPENING_FIGURE_LINE,
    }
    book_at_open = (opening or {}).get("total")
    if not (opening or {}).get("found") \
            or not isinstance(book_at_open, int) \
            or isinstance(book_at_open, bool) \
            or book_now is None:
        return out

    delta = book_now - book_at_open
    if delta < 0:
        delta_phrase = LEDGER_DELTA_DOWN.format(n=abs(delta))
    elif delta > 0:
        delta_phrase = LEDGER_DELTA_UP.format(n=delta)
    else:
        delta_phrase = LEDGER_DELTA_FLAT
    accounted = n_opened - n_closed - n_dropped
    residual = delta - accounted

    out.update({
        "status": LEDGER_MOVEMENT,
        "book_at_open": book_at_open,
        "delta": delta,
        "movement_line": LEDGER_LINE.format(
            book_at_open=book_at_open, opened=n_opened, closed=n_closed,
            dropped=n_dropped, book_now=book_now, delta=delta_phrase),
        "residual": residual,
        "reconciles": residual == 0,
        "opening_ts": (opening or {}).get("ts"),
    })
    out["line"] = out["movement_line"]
    if residual:
        out["residual_line"] = LEDGER_RESIDUAL_LINE.format(
            accounted=accounted, residual=residual)
    return out


def compute_score(*, morning: dict, digest: dict, open_ids: Iterable[str],
                  closures: Iterable[dict], softened: bool = False,
                  close_legs: Optional[Iterable[dict]] = None,
                  ledger: Optional[dict] = None) -> dict:
    """The report card. A diff, not a grade.

    Joins the morning fire's `needs_attention_ids` against today's closures and
    today's still-open set. Three states per row and no fourth:

      `closed`        a closure event landed for it today (carrying its
                      pointer, which the row keeps so the claim is checkable);
      `open`          it is still on the open book — carried, not judged;
      `not_recorded`  it is on neither list. **The word is "Not recorded".**
                      There is no code path from here to "not done": that
                      phrase is not in this module, and the pin that proves it
                      reads the module's own source.

    No morning receipt → `{"status": "no_plan", "line": NO_PLAN_LINE}` and no
    rows at all. A score computed against nothing is a guess wearing a number.
    """
    if not (morning or {}).get("found"):
        # THE LEDGER RIDES BOTH BRANCHES (EODLEDGER1). A day with no morning
        # plan still moved the book, and the ledger's own no-opening-figure
        # answer is exactly the right thing to say about it. Suppressing the
        # ledger here would have made "no plan" mean "no day".
        return {"status": "no_plan", "line": NO_PLAN_LINE, "rows": [],
                "n_planned": 0, "n_closed": 0, "n_open": 0,
                "n_not_recorded": 0, "softened": bool(softened),
                "ledger": ledger,
                "first_move": None, "digest_read": bool((digest or {}).get("exists"))}

    open_set = {str(i) for i in (open_ids or [])}
    closed_by_id = {}
    for c in closures or []:
        cid = c.get("commitment_id")
        if cid:
            closed_by_id[str(cid)] = c

    rows = []
    for cid in (morning.get("needs_attention_ids") or []):
        key = str(cid)
        close = closed_by_id.get(key)
        if close is not None:
            rows.append({"commitment_id": key, "state": "closed",
                         "title": close.get("title") or "",
                         "source_ref": close.get("source_ref"),
                         "closed_ts": close.get("ts")})
        elif key in open_set:
            rows.append({"commitment_id": key, "state": "open",
                         "title": "", "source_ref": None, "closed_ts": None})
        else:
            rows.append({"commitment_id": key, "state": "not_recorded",
                         "label": NOT_RECORDED,
                         "title": "", "source_ref": None, "closed_ts": None})

    n_closed = sum(1 for r in rows if r["state"] == "closed")
    n_open = sum(1 for r in rows if r["state"] == "open")
    n_missing = sum(1 for r in rows if r["state"] == "not_recorded")
    total = len(rows)

    if total == 0:
        line = "This morning's brief asked for nothing, so there is nothing to score."
    else:
        line = (f"{n_closed} of {total} closed"
                + (f", {n_open} still open" if n_open else "")
                + (f", {n_missing} not recorded" if n_missing else "")
                + ".")
    notes = []
    if n_missing:
        notes.append(NOT_RECORDED_LINE)
    if softened:
        notes.append(SOFTEN_LINE)
    return {
        "status": "scored",
        "line": line,
        "rows": rows,
        "n_planned": total,
        "n_closed": n_closed,
        "n_open": n_open,
        "n_not_recorded": n_missing,
        "notes": notes,
        "softened": bool(softened),
        # THE BOOK'S MOVEMENT (EODLEDGER1 part 2). The score reads the plan;
        # this reads the book. Both are the report card and only one of them
        # existed.
        "ledger": ledger,
        "first_move": check_first_move((digest or {}).get("first_move"),
                                       closures, close_legs=close_legs),
        "digest_read": bool((digest or {}).get("exists")),
    }


# ---------------------------------------------------------------------------
# Block: wins
# ---------------------------------------------------------------------------

_WIN_SPECS = (
    ("commitment_resolved", "closed"),
    ("thread_resolved", "closed"),
    ("decision", "decided"),
    ("decision_resolved", "decided"),
    ("deal_stage_changed", "moved"),
    ("deal_won", "moved"),
    ("meeting_processed", "processed"),
    ("person_proposal_resolved", "cleared"),
    ("brain_proposal_resolved", "cleared"),
)

# THE TYPED FALLBACK (SPEC EODFIX1 §0-2). When neither a snapshot title nor a
# join produces a name, the row still renders — as the plainest true sentence
# about what happened, with no name in it. The alternative that shipped was
# DROPPING the row, which is how a block whose whole job is "what moved today"
# reported zero on a day with two closes on disk.
#
# These are sentences, not labels: they are rendered where a title would be, so
# they have to read as English next to one. And they never guess: "a decision
# was resolved" is the whole claim, because the record is the whole evidence.
_WIN_GENERIC_LINES = {
    "commitment_resolved": "a commitment was closed",
    "thread_resolved": "a thread was resolved",
    "decision": "a decision was logged",
    "decision_resolved": "a decision was resolved",
    "deal_stage_changed": "a deal moved stage",
    "deal_won": "a deal was won",
    "meeting_processed": "a meeting was processed",
    "person_proposal_resolved": "a person proposal was cleared",
    "brain_proposal_resolved": "a proposal was cleared",
}

# Where a rendered win title came from. On the row, because "the surface named
# it" and "the surface fell back to a sentence" are different claims and a
# reviewer (and the removal-proof pins) must be able to tell them apart.
TITLE_SNAPSHOT = "snapshot"     # the writer stamped it on the event
TITLE_JOINED = "joined"         # resolved by id against the record it names
TITLE_GENERIC = "generic"       # the typed line — nothing was named

# The id spellings a `commitment_resolved` may use to name its commitment (the
# closer alias-group `event-payloads.schema.json` declares, minus the seq
# aliases, which are looked up in the seq index instead).
_CLOSER_ID_KEYS = ("commitment_id", "id", "target_id", "thread_id")
_CLOSER_SEQ_KEYS = ("commitment_seq", "source_event_seq")


def _snapshot_title(data: dict) -> str:
    """The title the WRITER stamped, if any. Unchanged from the original guard
    — it is now the first of three answers rather than the only one."""
    return str(data.get("title") or data.get("summary")
               or data.get("decision") or "").strip()


def _win_join_index(events: Iterable[dict]) -> dict:
    """ONE pass over the events already loaded → every lookup table the win
    resolvers need. Batch by construction: no resolver re-reads the log, and
    none of them loads a record per event (a six-row block that re-scanned the
    substrate six times would be a fix with a performance bug inside it).

    One pass per CALLER, not per fire — `compute_wins` and `closures_since`
    each build their own (review N-8). Both are sub-tenth-of-a-second on a
    ~10k-event log; sharing one index across them would couple two readers to
    save nothing measurable.
    """
    idx = {
        "commitment_by_id": {},     # commitment id  -> title
        "commitment_by_seq": {},    # commitment seq -> title
        "decision_by_id": {},       # decision id    -> title
        "person_proposal_by_seq": {},
        "person_proposal_by_fp": {},
        "brain_proposal_by_id": {},
    }
    try:
        from confirm_flow import PERSON_NAME_KEYS, PROPOSAL_TYPES
    except Exception:  # noqa: BLE001 — a read never breaks a fire
        PERSON_NAME_KEYS = ("name", "inferred_name", "proposed_name",
                            "display_name")
        PROPOSAL_TYPES = ("person_proposal", "person_update_proposal")

    for ev in events:
        etype = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if etype == "commitment":
            title = str(data.get("title") or "").strip()
            if not title:
                continue
            cid = data.get("id") or ev.get("id")
            if cid:
                idx["commitment_by_id"].setdefault(str(cid), title)
            seq = ev.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                idx["commitment_by_seq"].setdefault(seq, title)
        elif etype == "decision":
            title = _snapshot_title(data)
            if not title:
                continue
            # The decision-id derivation `decision_match` / `render_decision_log`
            # both use, including the `decision_seq_<n>` fallback — a closer
            # written by the matcher names the decision that way and nothing
            # else joins to it.
            did = (data.get("id") or ev.get("id")
                   or f"decision_seq_{ev.get('seq', '?')}")
            idx["decision_by_id"].setdefault(str(did), title)
        elif etype in PROPOSAL_TYPES:
            name = ""
            for key in PERSON_NAME_KEYS:
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    name = val.strip()
                    break
            if not name:
                continue
            seq = ev.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                idx["person_proposal_by_seq"].setdefault(seq, name)
            else:
                # PID1 D8 — a seq-less proposal is adjudicated by fingerprint,
                # so that is the only key its tombstone can join on.
                try:
                    from confirm_flow import compute_proposal_fingerprint
                    fp = compute_proposal_fingerprint(
                        etype, name, ev.get("ts") or ev.get("timestamp") or "")
                except Exception:  # noqa: BLE001
                    fp = None
                if fp:
                    idx["person_proposal_by_fp"].setdefault(str(fp), name)
        elif etype == "brain_proposal":
            pid = data.get("proposal_id")
            if not pid:
                continue
            line = str(data.get("render_line") or data.get("evidence")
                       or "").strip()
            if line:
                idx["brain_proposal_by_id"].setdefault(str(pid), line)
    return idx


def _entity_names(workspace_root) -> dict:
    """`{id: display name}` for threads and orgs, read at most once per CALL.

    Per call, not per fire: `compute_wins` and `closures_since` each read it
    once, so a fire that runs both reads entities.json twice. Measured on a
    ~10k-event log that is well under a tenth of a second, and the honest
    docstring is worth more than the shared cache (review N-8).

    Deals and resolved threads carry an id and nothing else — `deal_won` is
    `{thread_id, org_id, value}` by contract — so their name lives in
    entities.json or nowhere. Read through the canonical wrapper-aware helper
    (`entities_io`), never by reaching into a shape.
    """
    out = {}
    try:
        from entities_io import entities_collection
        raw = json.loads((Path(workspace_root) / "_hq" / "data"
                          / "entities.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a read never breaks a fire
        return out
    # SPEC DUALKEY1: "projects" dropped from this tuple — entities_collection
    # now aliases it to the same `threads` list, so keeping both names here
    # would process every thread twice for no new coverage.
    for name in ("threads", "orgs"):
        try:
            rows = entities_collection(raw, name)
        except Exception:  # noqa: BLE001
            continue
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            rid = row.get("id")
            label = (row.get("name") or row.get("title")
                     or row.get("canonical_name") or row.get("display_name"))
            if rid and isinstance(label, str) and label.strip():
                out.setdefault(str(rid), label.strip())
    return out


def _resolve_win_title(ev: dict, data: dict, idx: dict, names: dict) -> tuple:
    """`(title, title_source, ref_id)` for one win event.

    Three answers, in this order and no fourth:

      1. the SNAPSHOT the writer stamped;
      2. the id→title JOIN against the record the event names;
      3. the typed generic line.

    Never a fabrication. A join that misses falls to the sentence; it does not
    reach for a nearby string that happens to be present, and it does not
    compose a title out of an id.
    """
    etype = ev.get("type")
    snapshot = _snapshot_title(data)
    ref_id = None

    if etype == "commitment_resolved":
        for key in _CLOSER_ID_KEYS:
            val = data.get(key)
            if val:
                ref_id = str(val)
                break
        if snapshot:
            return snapshot, TITLE_SNAPSHOT, ref_id
        for key in _CLOSER_ID_KEYS:
            val = data.get(key)
            hit = idx["commitment_by_id"].get(str(val)) if val else None
            if hit:
                return hit, TITLE_JOINED, str(val)
        for key in _CLOSER_SEQ_KEYS:
            val = data.get(key)
            if isinstance(val, str) and val.strip().isdigit():
                val = int(val.strip())
            hit = (idx["commitment_by_seq"].get(val)
                   if isinstance(val, int) and not isinstance(val, bool)
                   else None)
            if hit:
                return hit, TITLE_JOINED, str(val)

    elif etype == "thread_resolved":
        # `commitment_id` is in the tuple for the same reason it is in
        # `_CLOSER_ID_KEYS`: the live rows carry it (35 of 50 on the substrate
        # the review sampled, and none of the other three spellings). Omitting
        # it here was an asymmetry, not a rule.
        tid = (data.get("id") or data.get("thread_id")
               or data.get("target_id") or data.get("commitment_id"))
        ref_id = str(tid) if tid else None
        if snapshot:
            return snapshot, TITLE_SNAPSHOT, ref_id
        if tid:
            hit = names.get(str(tid)) or idx["commitment_by_id"].get(str(tid))
            if hit:
                return hit, TITLE_JOINED, str(tid)

    elif etype == "decision_resolved":
        did = data.get("decision_id") or data.get("id")
        ref_id = str(did) if did else None
        if snapshot:
            return snapshot, TITLE_SNAPSHOT, ref_id
        if did:
            hit = idx["decision_by_id"].get(str(did))
            if hit:
                return hit, TITLE_JOINED, str(did)

    elif etype in ("deal_won", "deal_stage_changed"):
        tid = data.get("thread_id")
        ref_id = str(tid) if tid else None
        if snapshot:
            return snapshot, TITLE_SNAPSHOT, ref_id
        for key in ("thread_id", "org_id"):
            val = data.get(key)
            hit = names.get(str(val)) if val else None
            if hit:
                return hit, TITLE_JOINED, str(val)

    elif etype == "person_proposal_resolved":
        seq = data.get("proposal_seq")
        if isinstance(seq, str) and seq.strip().isdigit():
            seq = int(seq.strip())
        fp = data.get("proposal_fingerprint")
        ref_id = str(seq if seq is not None else (fp or "")) or None
        if snapshot:
            return snapshot, TITLE_SNAPSHOT, ref_id
        if isinstance(seq, int) and not isinstance(seq, bool):
            hit = idx["person_proposal_by_seq"].get(seq)
            if hit:
                return hit, TITLE_JOINED, str(seq)
        if isinstance(fp, str) and fp.strip():
            hit = idx["person_proposal_by_fp"].get(fp.strip())
            if hit:
                return hit, TITLE_JOINED, fp.strip()

    elif etype == "brain_proposal_resolved":
        pid = data.get("proposal_id")
        ref_id = str(pid) if pid else None
        if snapshot:
            return snapshot, TITLE_SNAPSHOT, ref_id
        if pid:
            hit = idx["brain_proposal_by_id"].get(str(pid))
            if hit:
                return hit, TITLE_JOINED, str(pid)

    elif snapshot:
        return snapshot, TITLE_SNAPSHOT, ref_id

    return _WIN_GENERIC_LINES.get(etype, ""), TITLE_GENERIC, ref_id


def compute_wins(workspace_root, since_ts, *, now_iso=None,
                 cap: int = MAX_WIN_ROWS) -> dict:
    """Names, not statistics. What actually moved since the morning fire.

    Zero wins renders ONE honest line (`NO_WINS_LINE`) and never a padded
    all-clear: "a quiet day" is a judgement, and this surface does not make
    them.

    THE TITLE IS RESOLVED, NOT DEMANDED (SPEC EODFIX1 §2-1). The shipped guard
    read `data.title | data.summary | data.decision` and `continue`d when all
    three were absent — which is the shape of five of the nine declared win
    types, including the canonical commitment close. So the block that exists
    to say what moved was structurally blind to the movement this product
    writes most of: on the 2026-08-17 walk it rendered "Nothing closed today
    that I can see" with two closes on disk inside the window.

    Every row now carries `title_source` — `snapshot`, `joined` or `generic` —
    because "I found its name" and "I could not, and here is what happened
    anyway" are different claims, and a row that cannot distinguish them is a
    row that will eventually invent the difference.

    AND THE CAP SPEAKS (review N-1). Seeing more is what made the 6-row bound
    start biting, so the block returns `more_line` whenever it bound — the
    same posture the morning brief's capped lane has always had. `n_total` is
    the honest count regardless of the aperture; the cap binds the RENDER.

    AND THE WINDOW HAS A FLOOR (SPEC WINSFLOOR1). A missing `since_ts` used to
    mean no lower bound at all, so on any day the morning brief did not fire
    this block read every win-type event the workspace had ever written and
    printed the count as what moved today — 2,334 rows on the live workspace on
    2026-08-19, on a day that had moved none of them. It now floors to
    workspace-local midnight and returns `window_source` naming the window it
    read, which is also what spells `more_line` (`WINS_MORE_LINES`).
    """
    since, window_source = _window(workspace_root, since_ts, now_iso=now_iso)
    until = _parse_iso(now_iso) if now_iso else None
    kinds = dict(_WIN_SPECS)
    events = _load_events(workspace_root)
    idx = _win_join_index(events)
    names = None
    rows = []
    for ev in events:
        kind = kinds.get(ev.get("type"))
        if kind is None:
            continue
        ts = _parse_iso(ev.get("ts"))
        # THE LOWER BOUND IS ALWAYS REAL (SPEC WINSFLOOR1). This was the line
        # that went silent when no morning brief fired: `since` was None, the
        # guard fell through, and the block rendered the whole history of the
        # workspace as what moved today.
        if ts is None or ts < since:
            continue
        if until is not None and ts is not None and ts > until:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if names is None:
            # Read entities.json at most once per fire, and only when a win
            # actually reached the resolver.
            names = _entity_names(workspace_root)
        title, source, ref_id = _resolve_win_title(ev, data, idx, names)
        if not title:
            # No snapshot, no join, and no typed line for this type. Dropping
            # is right here and ONLY here: there is nothing true to render.
            continue
        rows.append({"kind": kind, "title": title, "ts": ev.get("ts"),
                     "source_ref": data.get("source_ref"),
                     "type": ev.get("type"),
                     "title_source": source,
                     "ref_id": ref_id})
    rows.sort(key=lambda r: (r.get("ts") or ""))
    n_total = len(rows)
    if cap and n_total > cap:
        # THE APERTURE IS A DELIBERATE RANKING (review N-1), not a slice.
        # "The last six" was harmless while nothing was ever hidden; it is a
        # choice now that the cap binds most days. Named rows win the slots:
        # a `generic` row's whole content is "a thread was resolved", which is
        # what `n_more` and `n_generic` already say in numbers, so spending a
        # scarce slot on one buys the reader nothing. Recency breaks the tie
        # inside each group, and the SHOWN set still renders in chronological
        # order — the block reads as a day, not as a leaderboard.
        order = sorted(range(n_total),
                       key=lambda i: (rows[i]["title_source"] == TITLE_GENERIC,
                                      -i))
        keep = set(order[:cap])
        shown = [r for i, r in enumerate(rows) if i in keep]
    else:
        shown = rows
    n_more = max(0, n_total - len(shown))
    return {
        "rows": shown,
        "n_total": n_total,
        "n_more": n_more,
        # WHICH WINDOW THESE ROWS CAME FROM. The pack carries it, and the
        # overflow line is spelled from it — a block that swapped its aperture
        # without saying so would be honest in its numbers and mute about what
        # they count.
        "window_source": window_source,
        # A cap is a render bound and never a silence — and since EODLEDGER1
        # it states the DENOMINATOR, through the one shared helper every capped
        # block on this surface now uses.
        "more_line": more_line(WINS_MORE_LINES[window_source],
                               n_shown=len(shown), n_total=n_total),
        "n_generic": sum(1 for r in rows if r["title_source"] == TITLE_GENERIC),
        "line": NO_WINS_LINE if n_total == 0 else None,
    }


# ---------------------------------------------------------------------------
# Block: slipped
# ---------------------------------------------------------------------------

def overdue_ask_after_days(workspace_root) -> int:
    """This workspace's fatigue threshold, in whole days (SPEC OVERDUE1 D1).

    Reads `overdue_ask_after_days` off the End of Day fire's own FRP1 config,
    defaulting to `OVERDUE_ASK_AFTER_DAYS`. An unreadable or nonsensical value
    falls back to the default rather than guessing: a threshold of 0 would ask
    about everything the day it came due, and a negative one would ask about
    work that is not late yet, and neither is a reading anyone intended.
    """
    try:
        from held_tier import CONFIG_SKILL, config_defaults
        from skill_config_writer import get_config
        defaults = config_defaults()
        defaults.setdefault(OVERDUE_ASK_CONFIG_KEY, OVERDUE_ASK_AFTER_DAYS)
        value = get_config(workspace_root, CONFIG_SKILL,
                           defaults).get(OVERDUE_ASK_CONFIG_KEY)
    except Exception:  # noqa: BLE001 — a config read never costs a fire
        return OVERDUE_ASK_AFTER_DAYS
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return OVERDUE_ASK_AFTER_DAYS
    return value


ASK_STATE_ASK = "ask"
ASK_STATE_REST = "rest"
ASK_STATE_NONE = "none"


def overdue_ask_state(row: dict, *, now_iso: Optional[str],
                      ask_after_days: int = OVERDUE_ASK_AFTER_DAYS) -> dict:
    """ONE row's fatigue verdict (SPEC OVERDUE1), as a pure function.

    Returns `{"state": "ask"|"rest"|"none", "days_over": int|None,
    "ask_line": str|None}`.

    EXTRACTED, NOT REWRITTEN (SPEC EODSYNTH1 R-3). The rule is unchanged down
    to the comparison: a mark suppresses only while its `due_at_ask` still
    equals the row's CURRENT due, so re-dating an item is an answer and the
    clock re-arms from the new date, with no second write and nothing to
    schedule. What moved is WHO ASKS. R-3 takes the confirm/drop asks out of
    the evening and gives them to the morning surfaces, and the marker is
    "written by whichever surface asks" — so the verdict had to stop living
    inside `compute_slipped`, which is an evening-only function. Two callers,
    one implementation: the morning lane (`apply_overdue_ask`) and the
    evening's own selection, which now asks nothing and only rests.

    `now_iso` GATES THE ASK AND NOT THE REST, exactly as before: with no clock
    there is no "days overdue" so nothing new is asked, but a row that already
    carries a live mark still rests — the mark is a fact about the row (the
    CEO was asked and has not answered), not a fact about the clock.
    """
    from commitment_state import overdue_days as _overdue_days

    if not isinstance(row, dict):
        return {"state": ASK_STATE_NONE, "days_over": None, "ask_line": None}
    try:
        threshold = int(ask_after_days)
    except (TypeError, ValueError):
        threshold = OVERDUE_ASK_AFTER_DAYS
    if threshold < 1:
        threshold = OVERDUE_ASK_AFTER_DAYS

    due = row.get("due")
    mark = row.get("asked") if isinstance(row.get("asked"), dict) else None
    if mark is not None and str(mark.get("due_at_ask") or "").strip() == str(
            due or "").strip():
        return {"state": ASK_STATE_REST, "days_over": None, "ask_line": None}
    days_over = _overdue_days(due, now_iso) if now_iso else None
    if isinstance(days_over, int) and days_over >= threshold:
        title = str(row.get("title") or "").strip()
        return {"state": ASK_STATE_ASK, "days_over": days_over,
                "ask_line": overdue_ask_label(title, days_over)}
    return {"state": ASK_STATE_NONE, "days_over": days_over, "ask_line": None}


def apply_overdue_ask(rows: Optional[Iterable[dict]] = None, *,
                      now_iso: Optional[str] = None,
                      ask_after_days: int = OVERDUE_ASK_AFTER_DAYS,
                      ask: bool = True) -> dict:
    """The fatigue rule over a needs-attention LANE (SPEC EODSYNTH1 R-3).

    Returns `{"rows", "asked_ids", "resting_ids", "n_resting", "resting_line",
    "ask_after_days"}`. The returned rows are the lane MINUS the resting ones,
    with `ask_line` / `ask_now` / `days_over` stamped on the rows being asked
    about tonight — the same three keys the evening block has carried since
    OVERDUE1, so a renderer that already knew them needs no new vocabulary.

    THIS IS THE MORNING'S ENTRY POINT. R-3 moved the "Done, new date, or
    drop?" question off the evening and onto the morning brief's
    needs-attention lane, and the ask-once marker is unchanged: whichever
    surface ASKS writes it, through `commitment_state.mark_asked`, and the
    rest-until-answered fold holds exactly as before.

    `ask=False` is the EVENING's call. It rests what is resting — a row the
    CEO has already been asked about must not come back as narrative prose the
    next night either — and asks nothing, which is what makes the evening's
    ask count zero.
    """
    out, asked, resting = [], [], []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        verdict = overdue_ask_state(row, now_iso=now_iso,
                                    ask_after_days=ask_after_days)
        cid = str(row.get("commitment_id") or "")
        if verdict["state"] == ASK_STATE_REST:
            if cid:
                resting.append(cid)
            continue
        new = dict(row)
        if ask and verdict["state"] == ASK_STATE_ASK:
            new["ask_now"] = True
            new["days_over"] = verdict["days_over"]
            new["ask_line"] = verdict["ask_line"]
            # THE FORK'S ANSWERS TRAVEL WITH THE QUESTION, unchanged from the
            # evening block that used to ask it. The question is "Done, new
            # date, or drop?" and these are those three answers; a row that
            # carried the question without them would be asking something the
            # reader has no listed way to answer.
            #
            # NAMED HONESTLY (see the BUILD record): the morning brief
            # one-taps `mark done [n]` today and routes a new date or a drop
            # through `my plate`, which is one extra hop for two of the three
            # answers. That hop is a cost of the move, not a defect in this
            # list — the verbs are what the row OFFERS, and closing the hop is
            # an apply-choices route on the morning surface, which is a build.
            new.setdefault("verbs", list(SLIPPED_VERBS))
            if cid:
                asked.append(cid)
        out.append(new)
    # The asked rows lead: a question the reader never sees is not a question.
    out.sort(key=lambda r: (not r.get("ask_now"),))
    return {"rows": out, "asked_ids": sorted(asked),
            "resting_ids": sorted(resting), "n_resting": len(resting),
            "resting_line": resting_line(len(resting)),
            "ask_after_days": ask_after_days}


def compute_slipped(*, brief_state: dict, morning: dict,
                    todays_meetings: Optional[Iterable[dict]] = None,
                    processed_meeting_ids: Optional[Iterable[str]] = None,
                    now_iso: Optional[str] = None,
                    cap: int = MAX_SLIPPED_ROWS,
                    softened: bool = False,
                    lane_total: Optional[int] = None,
                    ask: bool = True,
                    ask_after_days: int = OVERDUE_ASK_AFTER_DAYS) -> dict:
    """The ball-is-on-you rows, and ONLY those.

    A "slipped" claim is a ball-is-on-you claim, so it rides the SAME gates the
    morning brief's Step 3c / 3c-bis put in front of every other one (Bug #93
    class). The gated source is `compute_brief_state`'s `needs_attention` — the
    you-owe set that survived every drop — and this function reads nothing
    else. Every row carries `gate_source` plus the id, so the provenance of the
    claim is on the row rather than in a paragraph.

    Structurally, not by discipline: `dropped_ids` is built from the same
    state's `dropped` list and every candidate is checked against it. An item
    the gate dropped this morning cannot be promoted back into a slipped claim
    by this evening — the exact move Bug #93 was.

    Max `cap` rows, each carrying its verbs — and since EODLEDGER1 the cap
    STATES ITS DENOMINATOR, through the same `more_line` helper `wins` uses.

    `lane_total` IS THE HONEST DENOMINATOR AND IT HAS TO BE PASSED. The rows
    this function receives have ALREADY been bounded once, upstream, by
    `commitment_state.cap_needs_attention` — the driver hands over `lane["shown"]`
    (at most five) and keeps `lane["n_total"]` beside it. So `len(candidates)`
    is a second cap wearing a total's clothes: on the workspace this spec was
    measured against it would print "3 of 5" over a lane holding 41. When
    `lane_total` is absent the local count stands, which is right for a direct
    caller handing over the whole lane and wrong for the driver — so the driver
    passes it, and the suite pins that it does.

    THE FATIGUE RULE (SPEC OVERDUE1). Two additions and nothing else moves:

      * A row `ask_after_days` or more past its EFFECTIVE due that carries no
        live `asked` mark is pinned to the TOP of the block with the fork
        label, and `ask_now: True` tells the orchestrator to write the mark
        after the pack is posted (`mark_slipped_asked`).
      * A row carrying a live mark for its CURRENT due is suppressed from the
        block — the same shape and the same place as the Bug #93 `dropped_ids`
        check — counted in `n_resting`, and named by one trailing line.

    "Live" is the whole idea and it is one comparison: the mark records the
    due date it asked about, so an item the user re-dated no longer matches
    its own mark, the mark stops suppressing anything, and the clock re-arms
    from the new date. No second event, no expiry sweep, nothing to schedule.

    Suppression is from THIS BLOCK ONLY. A resting row is still on the open
    book, still on `my plate`, still in the brief's needs-attention lane, and
    still inside `n_total` — the denominator counts it, because it really is
    on the you-owe list and a total that quietly shrank would be the
    dishonesty the cap doctrine exists to prevent.

    `now_iso` GATES THE ASK AND NOT THE REST, deliberately. With no clock
    there is no "days overdue", so nothing new is ever asked and every row
    renders exactly as it did before this build. A row that ALREADY carries a
    mark still rests, because the mark is a fact about the row — the CEO was
    asked and has not answered — and not a fact about the clock; un-resting it
    because a caller forgot to pass a time would re-nag on the one input the
    reader never chose. So "no clock" means "asks nothing", never "does
    nothing". (An earlier draft of this docstring claimed a clock-less caller
    got the pre-OVERDUE1 block byte for byte; that was false for any caller
    whose rows carry marks, and the suite's pin was written over mark-less
    rows and so proved nothing about it — REVIEW OVERDUE1 F-2.)

    ONE INHERITED DEPENDENCY, named because it is invisible from here
    (REVIEW OVERDUE1 F-6). Every row this function can ever ask about is
    YOU-OWE: `compute_brief_state` builds `needs_attention` from owner-is-user
    items only. That is what makes `push to [date]` take
    `commitment_state.apply_later`'s DEFER leg — a `commitment_updated` with
    `new_due`, which clears the mark through the fold — rather than the SNOOZE
    leg, which writes a `chat_dismissal` this fold never sees. If that other
    module ever admits a they-owe row into the lane, a push on an asked row
    would stop clearing its mark and the row would rest permanently and
    silently. The fatigue rule has no defence of its own against that; the
    lane's you-owe rule IS the defence.
    """
    state = brief_state or {}
    dropped_ids = {str(d.get("commitment_id"))
                   for d in (state.get("dropped") or [])
                   if isinstance(d, dict) and d.get("commitment_id")}
    planned = {str(i) for i in ((morning or {}).get("needs_attention_ids") or [])}

    try:
        threshold = int(ask_after_days)
    except (TypeError, ValueError):
        threshold = OVERDUE_ASK_AFTER_DAYS
    if threshold < 1:
        threshold = OVERDUE_ASK_AFTER_DAYS

    candidates = []
    resting_ids = []
    for row in (state.get("needs_attention") or []):
        if not isinstance(row, dict):
            continue
        cid = str(row.get("commitment_id") or "")
        if not cid or cid in dropped_ids:
            continue
        # SPEC EODSYNTH1 R-3 — ONE implementation of the fatigue verdict,
        # shared with the morning lane (`apply_overdue_ask`). The rule is
        # unchanged; what changed is that `ask` is now a parameter, because
        # the evening no longer asks and the morning does.
        verdict = overdue_ask_state(row, now_iso=now_iso,
                                    ask_after_days=threshold)
        if verdict["state"] == ASK_STATE_REST:
            resting_ids.append(cid)
            continue
        candidate = {
            "commitment_id": cid,
            "title": str(row.get("title") or "").strip(),
            "due": row.get("due"),
            "overdue": bool(row.get("overdue")),
            "on_this_mornings_plan": cid in planned,
            "gate_source": "brief_state.needs_attention",
            "verbs": list(SLIPPED_VERBS),
        }
        # SPEC EODSYNTH1 — the ask itself is CONDITIONAL now. With `ask=False`
        # (the evening's own call) nothing is stamped and `asked_ids` comes
        # back empty, so `mark_slipped_asked` has nothing to write and the
        # evening's ask count is zero. Resting is NOT conditional: a row the
        # CEO has already been asked about must not return as narrative prose
        # the next night either.
        if ask and verdict["state"] == ASK_STATE_ASK:
            candidate["ask_now"] = True
            candidate["days_over"] = verdict["days_over"]
            candidate["ask_line"] = verdict["ask_line"]
        candidates.append(candidate)

    # Rank: the one being ASKED about first, then overdue, then what the
    # morning actually asked for, then the rest. Nothing here re-scores an
    # item — the gate already decided which items may be claimed at all; this
    # only decides which three are shown. The new term LEADS because a
    # question the reader never sees is not a question.
    candidates.sort(key=lambda r: (not r.get("ask_now"),
                                   not r["overdue"],
                                   not r["on_this_mornings_plan"],
                                   r["title"].lower()))

    unprepped = []
    processed = {str(m) for m in (processed_meeting_ids or [])}
    for m in (todays_meetings or []):
        mid = str((m or {}).get("meeting_id") or "")
        if mid and mid not in processed:
            unprepped.append({"meeting_id": mid,
                              "title": str((m or {}).get("title") or "").strip()})

    shown = candidates[:cap] if cap else candidates
    # The upstream total wins when it is both present and larger — larger is
    # the only direction an upstream cap can move it, and taking the max means
    # a caller that passes a stale or wrong-shaped value can never make the
    # denominator SMALLER than what this function can see with its own eyes.
    # The resting rows are ADDED BACK here and nowhere else. They are genuinely
    # still on the you-owe list — unlike a `dropped_ids` row, which the morning
    # gate refused as a claim at all — so a denominator that quietly shed them
    # the night the block went quiet would be the cap doctrine's own dishonesty
    # wearing the fatigue rule's clothes. (On the driver's path `lane_total`
    # already counts them; this is what makes a direct caller agree.)
    n_total = len(candidates) + len(resting_ids)
    if isinstance(lane_total, int) and not isinstance(lane_total, bool):
        n_total = max(n_total, lane_total)
    return {
        "rows": shown,
        "n_total": n_total,
        "n_more": max(0, n_total - len(shown)),
        "more_line": more_line(SLIPPED_MORE_LINE, n_shown=len(shown),
                               n_total=n_total),
        "dropped_ids": sorted(dropped_ids),
        "meetings_without_notes": unprepped,
        "softened": bool(softened),
        "soften_line": SOFTEN_LINE if softened else None,
        # SPEC OVERDUE1 — what the fatigue rule did tonight. `n_resting` is
        # the count the receipt records and the trailing line reports;
        # `resting_ids` is beside it so a reader can name them rather than
        # take the number on faith, exactly as `dropped_ids` does one line up.
        # `ask_after_days` states the threshold this fire actually used, which
        # is the only way a workspace that changed the knob can read its own
        # numbers back later.
        "n_resting": len(resting_ids),
        "resting_ids": sorted(resting_ids),
        "resting_line": resting_line(len(resting_ids)),
        # Derived from `shown`, NEVER from `candidates`: if more rows qualify
        # than the cap can hold, the ones below the fold were not asked about,
        # and marking them would rest a row the reader never saw a question
        # about. That is the disappearance this whole rule is built to avoid.
        "asked_ids": sorted(r["commitment_id"] for r in shown
                            if r.get("ask_now")),
        "ask_after_days": threshold,
    }


def mark_slipped_asked(workspace_root, pack: dict, *,
                       source_skill: str = SURFACE,
                       now_iso: Optional[str] = None) -> dict:
    """Write the ask marks for the rows this fire actually ASKED about
    (SPEC OVERDUE1 DD-3). Called by the orchestrator AFTER the post.

    ONE call takes the pack whole, exactly as `log_end_of_day_receipt` does,
    and for the same reason: the alternative is prose telling a fire to loop
    over rows and call a writer per row, and a prose contract is presumed
    skipped (the Bug #98 class). Everything about WHICH rows get marked is
    decided in `compute_slipped` and read off `slipped["asked_ids"]` here —
    this function chooses nothing.

    AFTER the post, deliberately. The mark's whole meaning is "the CEO has
    been asked", so writing it before the question reaches the screen would
    rest a row nobody was ever asked about. That is the opposite ordering from
    the receipt, which must precede the post because it is what the numbers on
    screen resolve against — two bookkeeping writes, two different failure
    modes, two different places in the turn.

    Returns {"n_marked", "n_already", "n_closed", "n_failed", "results": […]}
    — never raises. A mark that cannot be written costs one repeated row
    tomorrow night; an exception here would cost the fire its ending.
    `n_closed` is its own number rather than a failure: an item the CEO closed
    between the pack and the post is the rule working, not breaking.
    """
    slipped = pack.get("slipped") if isinstance(pack, dict) else None
    slipped = slipped if isinstance(slipped, dict) else {}
    return _write_asks(workspace_root,
                       ids=slipped.get("asked_ids") or [],
                       rows=slipped.get("rows") or [],
                       source_skill=source_skill, surface=SURFACE,
                       now_iso=now_iso)


def mark_lane_asked(workspace_root, brief_state: dict, *,
                    source_skill: str = MORNING_TASK_ID,
                    now_iso: Optional[str] = None) -> dict:
    """The MORNING's half of the same write (SPEC EODSYNTH1 R-3).

    R-3 moved the "Done, new date, or drop?" ask to the morning, and the
    marker's rule is "written by whichever surface asks". So this is the same
    writer with the same body, reading the morning pack's `brief_state`
    instead of the evening's `slipped` block, and stamping `surface` with the
    morning's own id — because "which surface asked" is a fact the fold and
    any later audit need, and a morning ask recorded as an evening one is a
    record that cannot be read back.

    Called AFTER the morning post, for the same reason its evening twin is:
    the mark means the CEO has been asked, and writing it before the question
    reaches the screen rests a row nobody saw.
    """
    state = brief_state if isinstance(brief_state, dict) else {}
    return _write_asks(workspace_root,
                       ids=state.get("asked_ids") or [],
                       rows=state.get("needs_attention") or [],
                       source_skill=source_skill, surface=MORNING_TASK_ID,
                       now_iso=now_iso)


def _write_asks(workspace_root, *, ids, rows, source_skill: str,
                surface: str, now_iso: Optional[str]) -> dict:
    """ONE implementation of the ask write, shared by both bookends.

    Extracted by SPEC EODSYNTH1 rather than copied: two writers over one
    marker is two things to keep in step, and the whole safety property of
    the fatigue rule is that the mark records WHICH deadline was asked about.
    A second implementation is a second chance to record a different one.
    """
    ids = [str(i) for i in (ids or []) if str(i).strip()]
    out = {"n_marked": 0, "n_already": 0, "n_closed": 0, "n_failed": 0,
           "results": []}
    if not ids:
        return out
    due_by_id = {str(r.get("commitment_id")): r.get("due")
                 for r in (rows or [])
                 if isinstance(r, dict)}
    try:
        from commitment_state import asked_commitment_marks, mark_asked
    except Exception as exc:  # noqa: BLE001
        out["n_failed"] = len(ids)
        out["results"] = [{"commitment_id": i, "status": "error",
                           "error": str(exc)} for i in ids]
        return out
    # Project the live marks ONCE for the whole batch rather than per row —
    # the `known_watched` bargain `park_in_watch` offers, for the same reason.
    known = asked_commitment_marks(workspace_root)
    for cid in ids:
        try:
            res = mark_asked(workspace_root, cid,
                             due_at_ask=due_by_id.get(cid),
                             source_skill=source_skill,
                             surface=surface, now_iso=now_iso,
                             known_asked=known)
        except Exception as exc:  # noqa: BLE001
            out["n_failed"] += 1
            out["results"].append({"commitment_id": cid, "status": "error",
                                   "error": str(exc)})
            continue
        status = res.get("status")
        if status == "asked":
            out["n_marked"] += 1
        elif status == "already_asked":
            out["n_already"] += 1
        elif status == "not_open":
            out["n_closed"] += 1
        else:
            out["n_failed"] += 1
        out["results"].append({"commitment_id": cid, "status": status})
    return out


# ---------------------------------------------------------------------------
# Block: confirm
# ---------------------------------------------------------------------------

def compute_confirm(open_commitments, *, now_iso: str,
                    dismissed_ids: Optional[Iterable[str]] = None,
                    held_ids: Optional[Iterable[str]] = None,
                    cap: int = MAX_CONFIRM_ROWS) -> dict:
    """`confirm_flow.select_confirm_items`, relocated to this fire.

    BK5 owns tiering and decay later; EOD1 only MOVES the selection here, caps
    it at five, and ranks stakes-then-age.

    Held captures are fenced out TWICE, and both are load-bearing:

      1. **Off the ROW ITSELF.** A held capture stays an open, pending-review
         commitment on disk forever, so a later fire's confirm selection would
         pick it up on its own merits. The `data.held` stamp is read here and
         the row is dropped before the selector ever sees it. This is the fence
         that matters, because it holds on every fire after the one that held
         the row.
      2. **Off `held_ids`**, for the rows THIS fire just held — belt to the
         first fire's braces.

    A capture the flip HELD is out of sight by definition, so it must never
    appear in the surface whose entire job is to ask about things. If a held
    row could reach this block the flip would be a lie with extra steps.
    """
    from confirm_flow import select_confirm_items

    held = {str(i) for i in (held_ids or [])}
    candidates, n_stamped = [], 0
    for ev in (open_commitments or []):
        data = ev.get("data") if isinstance(ev, dict) and isinstance(
            ev.get("data"), dict) else {}
        if data.get("held"):
            n_stamped += 1
            continue
        candidates.append(ev)
    rows = select_confirm_items(candidates, now_iso,
                                dismissed_ids=dismissed_ids)
    rows = [r for r in rows if str(r.get("commitment_id")) not in held]

    def _stake(row) -> int:
        classes = row.get("classes") or row.get("unconfirmed_classes") or []
        return 1 if "pending_review" in classes else 0

    rows.sort(key=lambda r: (-_stake(r), str(r.get("captured_ts") or "")))
    shown = rows[:cap] if cap else rows
    for r in shown:
        r["verbs"] = list(CONFIRM_VERBS)
    return {"rows": shown, "n_total": len(rows),
            "n_more": max(0, len(rows) - len(shown)),
            # EODLEDGER1 — the cap states its denominator. This block bound 5
            # of 67 in silence on the workspace the spec was measured against.
            "more_line": more_line(CONFIRM_MORE_LINE, n_shown=len(shown),
                                   n_total=len(rows)),
            "n_held_excluded": n_stamped + len(held)}


def compute_person_candidates(workspace_root, *, now_iso: Optional[str] = None,
                              cap: Optional[int] = None) -> dict:
    """PERSONLOOP1 §0-3 — the confirm block's person-candidate rows.

    The recurring names the confirm block keeps asking about WITHOUT ever
    offering the answer: every one of them blocks captures that cannot drain
    until a person record exists. Derived live
    (`person_candidates.derive_candidates`), capped, counts on the receipt.

    Returns `{"rows", "n_total", "telemetry"}` — `rows` in the widget row
    shape all three surfaces share, so the End of Day renders the same
    question as the queue and the staff meeting, and answers it through the
    same writer. Any failure degrades to NO rows: the day-close must not die
    because a proposal could not be computed."""
    try:
        from person_candidates import (PROPOSAL_CAP, candidate_rows,
                                       derive_candidates, telemetry)

        cands = derive_candidates(workspace_root, now_iso=now_iso)
        rows = candidate_rows(
            cands, cap=PROPOSAL_CAP if cap is None else cap)
        return {"rows": rows, "n_total": len(cands),
                "telemetry": telemetry(cands)}
    except Exception as exc:  # pragma: no cover — the fire must survive
        sys.stderr.write(f"[end_of_day] person candidates skipped: {exc}\n")
        return {"rows": [], "n_total": 0,
                "telemetry": {"n_candidates": 0, "n_rows_blocked": 0,
                              "n_top_rows_blocked": 0}}


# ---------------------------------------------------------------------------
# Block: tomorrow
# ---------------------------------------------------------------------------

def compute_tomorrow(workspace_root, *, for_date: str,
                     calendar_events: Optional[Iterable[dict]] = None,
                     brief_state: Optional[dict] = None,
                     calendar_available: bool = True,
                     cap: int = MAX_TOMORROW_ROLLOVER) -> dict:
    """Tomorrow: the wide calendar look (now → +3d), the rollover items, and
    the day-intent AUTO-DRAFT.

    THE PROPOSAL IS NOT A FACT. `load_day_intent` is called with its default
    (`include_proposed=False`), so a `proposed` row on disk can never come back
    as the CEO's word; the draft this function computes is returned under
    `proposal` and carries `stated: False`. It is written ONLY on tap-confirm,
    by the caller, through `day_intent.write_day_intent(..., origin="wrap")`.
    Nothing here writes anything.

    `calendar_available=False` is the per-capability skip: the block still
    renders its intent half, and the caller receipts the missing leg. A skipped
    leg is a stated absence, never an empty section that reads as "nothing
    tomorrow".

    SPEC TOMFILT1 §1 — TOMORROW NEVER PROPOSES THE PAST. `for_date` is
    tomorrow, so the day being CLOSED is `for_date` minus one — resolved
    from `for_date` itself (already workspace-local by the time it reaches
    here) rather than a fresh clock read, so this stays a pure function of
    its own arguments. An item ANCHORED to that day — a due date on or
    before it, or same-day text ("tonight", a bare time with no future day
    named) — is excluded from the AUTO-DRAFT. The informational `rollover`
    list is untouched: it is "what's on your plate", not "what tomorrow is
    about", and the fence belongs to the proposer alone.
    """
    from day_intent import load_day_intent
    from due_reanchor import is_anchored_to_day, is_same_day_text, \
        parse_date, render_due_phrase

    record = None
    try:
        record = load_day_intent(workspace_root, for_date)
    except Exception:  # noqa: BLE001 — a read never breaks the fire
        record = None

    events = []
    for ev in (calendar_events or []):
        if not isinstance(ev, dict):
            continue
        events.append({
            "meeting_id": ev.get("meeting_id") or ev.get("id"),
            "title": str(ev.get("title") or ev.get("summary") or "").strip(),
            "start": ev.get("start"),
            "time_label": ev.get("time_label"),
            "prep_exists": bool(ev.get("prep_exists")),
        })
    events.sort(key=lambda e: str(e.get("start") or ""))

    # The day being closed. `for_date` parses cleanly by construction (every
    # caller resolves it through `day_intent.resolve_for_date`); a defensive
    # None here just means the anchor fence sits out rather than the whole
    # block raising over a caller's malformed date.
    _for_date_d = parse_date(for_date)
    today = (_for_date_d - _dt.timedelta(days=1)) if _for_date_d else None

    lane = [r for r in ((brief_state or {}).get("needs_attention") or [])
            if isinstance(r, dict)]
    rollover = [{"commitment_id": str(r.get("commitment_id") or ""),
                 "title": str(r.get("title") or "").strip(),
                 "due": r.get("due"),
                 "overdue": bool(r.get("overdue")),
                 # SPEC TOMFILT1 §2 — re-anchored to TODAY on every render,
                 # never a stale weekday name. `today` may be None only on a
                 # malformed `for_date`; the phrase then names the date alone.
                 "due_phrase": render_due_phrase(r.get("due"), today)}
                for r in lane][:cap]

    proposal = None
    if record is None:
        # SPEC TOMPICK1 §0.1 — UP TO THREE RANKED CANDIDATES, never padded.
        # The auto-draft: top needs-attention ∩ tomorrow's meetings, falling
        # back to the top of the lane. At most three items — the day_intent
        # cap is the point of that record, and a proposal that would be
        # refused at the writer is not a proposal. Fewer than three eligible
        # rows means fewer than three candidates; nothing here fills the gap.
        #
        # THE POOL IS THE FULL LANE, NOT THE DISPLAY-CAPPED `rollover`.
        # SPEC TOMFILT1's anchor fence runs FIRST, over every needs-attention
        # row; running it after `rollover`'s own `cap` slice would let three
        # anchored (excluded) rows fill the cap and starve a real candidate
        # sitting fourth — the fence would be correct and the draft would
        # come up empty anyway on any evening whose top rows are all
        # carryover from today.
        titles = [t for t in (e["title"] for e in events) if t]
        picked = []
        for r in lane:
            title = str(r.get("title") or "").strip()
            if not title:
                continue
            if today is not None and is_anchored_to_day(r.get("due"), today):
                continue
            if is_same_day_text(title):
                continue
            # The FIRST calendar title this candidate shares a content word
            # with — kept as text (not just a bool) so the why line can name
            # what tomorrow's brief will show, not just say "it matched".
            matched_title = next(
                (t for t in titles if _shares_words(title, t)), None)
            picked.append({"text": title,
                           "commitment_id": str(r.get("commitment_id") or ""),
                           "matched_title": matched_title})
        picked.sort(key=lambda p: (p["matched_title"] is None,))
        items = picked[:3]
        if items:
            rows = []
            for n, i in enumerate(items, start=1):
                why = (TOMORROW_CANDIDATE_WHY_MATCHED.format(
                           title=i["matched_title"])
                       if i["matched_title"] else TOMORROW_CANDIDATE_WHY_OPEN)
                row = {"text": i["text"], "rank": n, "why": why}
                # An EMPTY id is an absent id, and writing the key with an
                # empty value is how a downstream reader learns to treat "" as
                # a real commitment id (`day_intent.normalize_items` refuses
                # one outright).
                if i.get("commitment_id"):
                    row["commitment_id"] = i["commitment_id"]
                rows.append(row)
            proposal = {
                "for_date": for_date,
                "origin": "proposed",
                "stated": False,
                "items": rows,
                "verbs": list(TOMORROW_VERBS),
            }

    return {
        "for_date": for_date,
        "intent": record,
        "intent_stated": bool(record and record.get("stated")),
        "proposal": proposal,
        "first_event": events[0] if events else None,
        "events": events,
        "rollover": rollover,
        "calendar_available": bool(calendar_available),
        "line": (None if (record or proposal) else NO_TOMORROW_INTENT_LINE),
    }


def _shares_words(a: str, b: str) -> bool:
    """Do two titles share a content word? Deliberately crude — this decides
    the ORDER of a proposal the CEO confirms or rewrites with one tap, never
    a write."""
    stop = {"the", "a", "an", "and", "or", "for", "with", "to", "of", "on",
            "call", "meeting", "sync", "check", "in", "at"}
    wa = {w for w in re.findall(r"[a-z0-9]+", (a or "").lower())
          if len(w) > 2 and w not in stop}
    wb = {w for w in re.findall(r"[a-z0-9]+", (b or "").lower())
          if len(w) > 2 and w not in stop}
    return bool(wa & wb)


# ---------------------------------------------------------------------------
# Block: sign_off
# ---------------------------------------------------------------------------

def compute_sign_off(*, slipped: dict, tomorrow: dict, now_iso: str,
                     brief_state: Optional[dict] = None) -> dict:
    """Computed, never composed.

    Urgent = anything in the GATED needs-attention set that is due before
    tomorrow's brief and survived every drop. Zero urgent → `SIGN_OFF_CLEAR`,
    verbatim. Otherwise ONE line naming the exception — assembled from the
    row's own title, so there is nothing for a model to write here.

    SPEC OVERDUE1 — A RESTING ROW IS STILL OVERDUE (REVIEW F-3). The block
    stops REPEATING a row after it has asked once; it does not stop the item
    being late, and the sign-off is the fire's last honest sentence about what
    is outstanding. The `slipped["resting_ids"]` rows are therefore counted
    here, joined back to their titles through the SAME `brief_state` lane the
    block itself was built from — the rows never left it (D3). Shown rows lead
    the list, so the item the CEO can see is the one the sentence names, and
    the resting ones make the COUNT true. Without this the fire says "one
    thing is still overdue" on a night when three are, and the two it did not
    say are precisely the ones it has stopped showing. Byte-identical on any
    night with nothing resting.
    """
    urgent = []
    for row in (slipped or {}).get("rows") or []:
        if row.get("overdue"):
            urgent.append(row)
    resting_ids = {str(i) for i in (slipped or {}).get("resting_ids") or []
                   if str(i).strip()}
    if resting_ids:
        for row in ((brief_state or {}).get("needs_attention") or []):
            if not isinstance(row, dict) or not row.get("overdue"):
                continue
            rid = str(row.get("commitment_id") or "")
            if rid in resting_ids:
                urgent.append({"commitment_id": rid,
                               "title": str(row.get("title") or "").strip(),
                               "resting": True})
    if not urgent:
        for row in ((brief_state or {}).get("needs_attention") or []):
            if isinstance(row, dict) and row.get("overdue"):
                urgent.append({"commitment_id": str(row.get("commitment_id") or ""),
                               "title": str(row.get("title") or "").strip()})
                break
    if not urgent:
        return {"urgent": [], "line": SIGN_OFF_CLEAR, "computed": True}
    head = urgent[0]
    title = head.get("title") or "one item"
    extra = len(urgent) - 1
    line = (f"One thing is still overdue before tomorrow's brief: {title}."
            if extra <= 0 else
            f"{len(urgent)} items are still overdue before tomorrow's brief, "
            f"starting with {title}.")
    return {"urgent": urgent, "line": line, "computed": True}


# ---------------------------------------------------------------------------
# Monday branch — the prior-week roll-up + the development-read SLOT
# ---------------------------------------------------------------------------

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


def week_rollup(workspace_root, *, for_date, now_iso=None) -> dict:
    """Monday only: last week's day-scores, read off THIS FIRE'S OWN receipts.

    THREE SHAPES, THREE SENTENCES, AND NEVER A ZERO BETWEEN THEM. Every row
    carries `recorded` (did the evening chat run) and `counted` (did that fire
    record a score), and each combination is a different claim:

      `recorded: False` -> `NO_CLOSE_RECORDED`. The chat did not run. Zero
          would say the day closed nothing, which is a claim about the CEO;
          this is a claim about the machine.
      `recorded: True, counted: False` -> `NO_SCORE_RECORDED`. The chat DID
          run and predates the score — every `past-meetings` `pack_run` written
          before EOD1 is this shape, and since this build reaches live machines
          through that same taskId, it is what the first Monday after ship
          produces for the whole prior week (review N-1). The day happened; the
          count did not exist yet.
      `recorded: True, counted: True` -> the numbers.

    The row carries its own `line` in the first two cases so the renderer is
    handed a SENTENCE rather than a null. A null count with no line is the
    fabricated-zero shape by another route: the code stays honest and the
    surface is left with nothing to say, which is the moment a number gets
    invented.
    """
    from receipts import iter_receipts

    if isinstance(for_date, str):
        day = _dt.date.fromisoformat(for_date)
    else:
        day = for_date
    prior_monday = day - _dt.timedelta(days=7)

    by_day = {}
    try:
        rows = iter_receipts(workspace_root, task_ids=[TASK_ID])
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        if r.get("type") != RECEIPT_EVENT:
            continue
        dt = _aware(r.get("dt"))
        if dt is None:
            continue
        try:
            from tz import to_local
            local = to_local(dt, workspace_path=workspace_root)
            key = (local.date() if local else dt.date())
        except Exception:  # noqa: BLE001
            key = dt.date()
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        prev = by_day.get(key)
        if prev is None or dt >= prev["dt"]:
            by_day[key] = {"dt": dt, "data": data}

    days = []
    n_recorded = 0
    for offset in range(5):
        d = prior_monday + _dt.timedelta(days=offset)
        row = by_day.get(d)
        if row is None:
            days.append({"date": d.isoformat(), "weekday": _WEEKDAY_NAMES[offset],
                         "recorded": False, "counted": False,
                         "line": NO_CLOSE_RECORDED,
                         "n_closed": None, "n_planned": None})
            continue
        data = row["data"]
        score = data.get("score") if isinstance(data.get("score"), dict) else {}
        n_closed = score.get("n_closed")
        n_planned = score.get("n_planned")
        n_recorded += 1
        # THE THIRD SHAPE (review N-1). A fire that ran but recorded no score —
        # every pre-EOD1 `past-meetings` receipt — is COUNTED: False, and it
        # gets its own sentence rather than a null the renderer has to invent
        # around. `counted` is keyed on the counts actually being integers, not
        # on the presence of a `score` key, so a half-written score degrades
        # into this shape instead of into a partial number.
        counted = isinstance(n_closed, int) and isinstance(n_planned, int)
        # THE READER for the receipt's `close_leg` (G29). A day whose close
        # phase could not advance its cursor produced a score that understates
        # the closes, and a week read that averages it in silently is the
        # softened-day problem restated at a week's scale. The roll-up carries
        # the flag so the number is read for what it is.
        close_leg = data.get("close_leg") if isinstance(data.get("close_leg"), dict) else {}
        softened = bool(close_leg.get("softened") or score.get("softened"))
        days.append({
            "date": d.isoformat(),
            "weekday": _WEEKDAY_NAMES[offset],
            "recorded": True,
            "counted": counted,
            "n_closed": n_closed if counted else None,
            "n_planned": n_planned if counted else None,
            "softened": softened,
            "line": None if counted else NO_SCORE_RECORDED,
        })
    n_softened = sum(1 for d in days if d.get("softened"))
    n_uncounted = sum(1 for d in days if d.get("recorded") and not d.get("counted"))
    return {"week_of": prior_monday.isoformat(), "days": days,
            "n_days_recorded": n_recorded,
            "n_days_counted": sum(1 for d in days if d.get("counted")),
            # The legacy shape's own number, so a week that is ENTIRELY
            # pre-EOD1 is legible as that rather than as a bad week.
            "n_days_uncounted": n_uncounted,
            "n_days_softened": n_softened,
            "n_days_missing": 5 - n_recorded}


def development_read_slot(workspace_root) -> dict:
    """The weekly development read SLOT (Monday). It renders NOTHING until
    DEVREAD1 lands.

    Zero findings → nothing. Not a placeholder, not "coming soon", not a
    heading with an empty body: an empty section teaches the reader to skim
    the surface, and this one is the surface's most valuable real estate on
    the one day it exists. The slot's whole contract right now is that its
    `renders` is False and its `lines` are empty.
    """
    return {"slot": DEVREAD_SLOT_ID, "renders": False, "lines": [],
            "owner_spec": "DEVREAD1"}


# ---------------------------------------------------------------------------
# The soften floor
# ---------------------------------------------------------------------------

def soften_floor(close_result: Optional[dict]) -> dict:
    """Did the in-fire reconcile advance the cursor?

    `close_result` is the caller's own record of the close phase:
    `{"mail": <reconcile_and_receipt receipt>, "chat": <chat receipt>}`. When
    NEITHER leg advanced its cursor, the score and slipped blocks soften and
    the surface says so in one line — because a score computed over a stale
    mail cursor understates the closes and overstates the slips, and the CEO
    is the one who would be blamed for the difference.

    A leg that was SKIPPED for want of a connector does not soften on its own:
    a workspace with no chat backend is not a workspace whose chat is behind.
    Softening is for a leg that should have run and did not advance.
    """
    result = close_result or {}
    legs = []
    for name in ("mail", "chat"):
        leg = result.get(name)
        if not isinstance(leg, dict):
            continue
        status = str(leg.get("status") or "").lower()
        if status == "skipped":
            continue
        legs.append({"leg": name,
                     "advanced": bool(leg.get("cursor_advanced")),
                     "status": status or "complete"})
    if not legs:
        return {"softened": False, "line": None, "legs": []}
    advanced = any(l["advanced"] for l in legs)
    return {"softened": not advanced,
            "line": None if advanced else SOFTEN_LINE,
            "legs": legs}


# ---------------------------------------------------------------------------
# The catch-up read (SPEC EODLEDGER1 part 3 — M's ruling on D3)
# ---------------------------------------------------------------------------

# Below this the fire is not doing catch-up work in any sense a reader cares
# about, and the label would be noise on a surface that is otherwise fine.
CATCHUP_TIER = "degrade"


def _late_phrase(minutes) -> str:
    """"30 hours late" / "3 days late", from the lateness helper's OWN number.

    Never recomputed from a wall clock here: `check_lateness` already did that
    math against the slot the cron evaluates, in the clock the cron evaluates
    in, and a second derivation is a second answer waiting to disagree.
    """
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return "late"
    if minutes < 0:
        return "late"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hours late" if hours != 1 else "1 hour late"
    days = hours // 24
    return f"{days} days late" if days != 1 else "1 day late"


def _slot_phrase(scheduled_for) -> str:
    """The missed slot, rendered AS IT WAS AUTHORED.

    `scheduled_for` is machine-local naive — the clock the cron evaluates in —
    and it is rendered without conversion, which is the LATETZ rule: a cron
    slot is not a connector timestamp, the workspace-TZ conversion already
    happened at registration, and re-expressing it here names the wrong hour
    (and sometimes the wrong DAY) on any box whose zone differs from the
    workspace's.
    """
    try:
        dt = _dt.datetime.fromisoformat(str(scheduled_for))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if dt.minute == 0:
        clock = dt.strftime("%I %p").lstrip("0")
    return f"{clock} {dt.strftime('%A')}"


def compute_catchup_read(*, lateness: dict, window=None, workspace_root=None,
                         now_iso=None) -> dict:
    """The LABEL a late day-close arrives under, and whether it renders at all.

    M's ruling on D3: a late fire delivers a labelled read; it does not
    withhold one. Over 24 hours late the fire used to perform every substrate
    write and then post `degrade_notice` alone, so a skipped Tuesday was never
    scored — not that evening, not ever, and the only surviving trace was a
    Monday roll-up row reading "no close was recorded".

    Returns `{"renders", "tier", "label", "lines", "days", "n_days",
    "hours_late", "scheduled_for", "degrade_notice", "capped"}`.

      `renders` True   → the surface posts, with `label` at the top.
      `renders` False  → nothing changes; this is every non-degrade tier and,
                         critically, every `skip_render` directive.

    **`skip_render` NEVER RENDERS THROUGH HERE.** A slot already delivered
    posts its `ack` and stops — that is a duplicate, not a late first serve,
    and conflating the two is what delivered three duplicate full surfaces in
    one afternoon. The two paths are separated in code and pinned apart in the
    suite; `directive` is read BEFORE the tier, exactly as the fire's own prose
    requires.

    **`late_fire` IS NOT MODIFIED.** `LATENESS_TIERS` keeps its 3h/24h
    thresholds and `check_lateness` keeps its return contract, so every other
    scheduled surface keeps today's suppress-on-degrade behaviour. Only this
    orchestrator's degrade branch changed. `degrade_notice` is carried through
    on the return so the fire can put it on the RECORD, and is deliberately not
    the line the reader sees: it says "Skipped the full …", which is true of
    every other surface and false of this one from the moment it renders.

    The span is SOURCED, never hand-computed: how late comes from
    `lateness_minutes`, the missed slot from `scheduled_for`, and which days
    are accounted for from `catchup.catchup_window` over this fire's own
    receipt series. One read covers the whole span — never one surface per
    missed day.
    """
    late = lateness or {}
    out = {"renders": False, "tier": late.get("tier"), "label": None,
           "lines": [], "days": [], "n_days": 0,
           "hours_late": None, "scheduled_for": late.get("scheduled_for"),
           "degrade_notice": late.get("degrade_notice"), "capped": False}
    if late.get("directive"):
        # The served-slot skip. Not a late first serve; not this function's
        # business. Returning early rather than falling through is the fence.
        return out
    if late.get("tier") != CATCHUP_TIER:
        return out

    if window is None and workspace_root is not None:
        try:
            from catchup import catchup_window
            # `now=now_iso` for the same reason `capture_aperture` passes it:
            # the span is a statement about the fire's own instant, and the
            # machine clock is not that instant on a catch-up fire.
            window = catchup_window(workspace_root, TASK_ID, floor_hours=24,
                                    cap_days=30, now=now_iso)
        except Exception:  # noqa: BLE001 — catch-up never blocks a fire
            window = None
    window = window if isinstance(window, dict) else {}
    out["capped"] = bool(window.get("capped"))

    first = _local_dt(workspace_root, window.get("start_aware")
                      or window.get("start"))
    last = _local_dt(workspace_root, window.get("end_aware")
                     or window.get("end"))
    if last is None and now_iso:
        last = _local_dt(workspace_root, now_iso)
    days = []
    if first is not None and last is not None and first.date() <= last.date():
        step = first.date()
        # The 30-day ceiling is `catchup_window`'s, and it has already been
        # applied to `start`; this loop only expands what came back.
        while step <= last.date() and len(days) < 31:
            days.append(step.isoformat())
            step = step + _dt.timedelta(days=1)
    out["days"] = days
    out["n_days"] = len(days)

    if len(days) > 1:
        span = CATCHUP_SPAN_MULTI.format(
            first=_dt.date.fromisoformat(days[0]).strftime("%A, %B ")
            + str(_dt.date.fromisoformat(days[0]).day),
            last=_dt.date.fromisoformat(days[-1]).strftime("%A, %B ")
            + str(_dt.date.fromisoformat(days[-1]).day))
    elif days:
        span = CATCHUP_SPAN_ONE_DAY.format(
            day=_dt.date.fromisoformat(days[0]).strftime("%A, %B ")
            + str(_dt.date.fromisoformat(days[0]).day))
    else:
        span = "the missed slot"

    minutes = late.get("lateness_minutes")
    try:
        out["hours_late"] = int(minutes) // 60
    except (TypeError, ValueError):
        out["hours_late"] = None
    out["renders"] = True
    out["label"] = CATCHUP_LABEL.format(
        span=span, late=_late_phrase(minutes),
        scheduled=_slot_phrase(late.get("scheduled_for")))
    lines = [out["label"]]
    if len(days) > 1:
        lines.append(CATCHUP_COMPRESSED_LINE.format(n_days=len(days)))
    if out["capped"]:
        lines.append(CATCHUP_CAPPED_LINE.format(
            cap=int(window.get("cap_days") or 30)))
    out["lines"] = lines
    return out


# ---------------------------------------------------------------------------
# The receipt — written BEFORE the post, and it is what taps resolve against
# ---------------------------------------------------------------------------

# The blocks whose rows the EVENING numbers, in numbering order.
#
# SPEC EODSYNTH1 R-2/R-3 — THIS TUPLE IS NOW EMPTY, AND THAT IS THE BUILD.
# The evening's one interaction is the tomorrow block, and it does not resolve
# by number: `resolve_intent_confirm` reads the PROPOSAL off the same receipt,
# so a confirm needs no numbered row and never did. Everything that used to be
# numbered here — the slipped rows and their push/draft/drop verbs, the confirm
# rows, the person candidates — renders on the MORNING surfaces now, where the
# operator is in triage mode and where those surfaces keep their own maps.
#
# NUMBERING A ROW THAT DOES NOT RENDER IS THE DEFECT, not a spare capability.
# `log_end_of_day_receipt` records `confirm_ids` as a claim about what was on
# screen and `surfaced` counts it; a map entry for a row the surface never drew
# makes every tap past it resolve against something invisible. That is the
# PERSONLOOP1 N-1 finding, and this is the same rule applied in the direction
# the ruling moved: the rows left, so their numbers left with them.
#
# The invariant the suite pins: every block named here is also in
# `RENDER_ORDER`. A future build that brings a numbered row back to the evening
# has to put its block in both lists, in the same commit.
NUMBERED_BLOCKS: tuple = ()


def confirm_ids_from_pack(pack: dict) -> list:
    """The one-tap id map, in the order the surface numbers them.

    ORDER IS THE CONTRACT. `apply-choices` resolves `[n]` positionally against
    this list, so it is built from the pack's own RENDERED rows and in the
    pack's own order. A surface that renumbers without rewriting this list is
    the wrong-close hazard, which is why the list is DERIVED here instead of
    typed by the orchestrator.

    SPEC EODSYNTH1 — the evening renders no numbered rows, so this returns an
    EMPTY list and the receipt records `surfaced: 0`. That is not a regression
    and it is not a silence: zero rows were offered for a tap, which is exactly
    R-2 ("everything else is read-only") measured. The tomorrow confirm resolves
    through `resolve_intent_confirm` off `day_intent_proposal` on this same
    receipt and is unaffected — it never used this map.

    The walk over `NUMBERED_BLOCKS` is kept rather than replaced with a bare
    `return []` so that restoring a numbered row to this surface is a one-line
    change in ONE place, next to the invariant that says the block must render
    first.
    """
    ids = []
    for block in NUMBERED_BLOCKS:
        rows = ((pack or {}).get(block) or {}).get("rows") or []
        for row in rows:
            cid = row.get("commitment_id") or row.get("n")
            if cid:
                entry = {"n": len(ids) + 1, "id": str(cid), "block": block}
                payload = row.get("data")
                if isinstance(payload, dict):
                    entry["data"] = dict(payload)
                ids.append(entry)
    return ids


# The fire's own id. `<prefix><UTC to the second>-<8 hex>`, the `swb_` /
# `di_` mint shape (`commitment_backlog_sweep._mint_batch_id`): sortable,
# readable aloud, and unique per FIRE rather than per second.
#
# WHY IT HAD TO EXIST (SPEC EODFIX1 §1-5). Every gesture on this surface is
# supposed to stamp `session:<this fire's receipt id>` — but `pack_run`
# receipts carried no id field, so the placeholder in the fire's prose named
# something that did not exist and the model filled it with a per-DAY
# constant. Three separate gestures on one evening carried one identical
# pointer, which resolves to nothing while still counting as "has a pointer"
# in `closure_index.pointer_coverage` — a constant that inflates the very
# metric PROV1 exists to produce.
RECEIPT_ID_PREFIX = "eod_"
RECEIPT_ID_SALT_BYTES = 4


def mint_receipt_id(now=None) -> str:
    """`eod_<UTC to the second>-<8 hex>` — this fire's id."""
    import secrets

    stamp = now if isinstance(now, _dt.datetime) else \
        _dt.datetime.now(_dt.timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    return (f"{RECEIPT_ID_PREFIX}"
            f"{stamp.astimezone(_dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            f"-{secrets.token_hex(RECEIPT_ID_SALT_BYTES)}")


def gesture_ref(receipt_id: str, gesture: str) -> str:
    """`session:<receipt id>:<gesture>` — the pointer ONE act on this surface
    stamps. Built here so the two resolvers cannot spell it two ways, and
    RETURNED to callers rather than described to them (the caller-side-fence
    class: a pointer a caller composes from prose is a pointer a caller can
    flatten)."""
    return f"session:{receipt_id}:{gesture}"


def log_end_of_day_receipt(workspace_root, pack: dict, *,
                           fired_via: str = "scheduled",
                           duration_ms: Optional[int] = None,
                           late_tier: Optional[str] = None,
                           capture_leg: Optional[dict] = None,
                           phase_ledger: Optional["PhaseLedger"] = None,
                           extra_data: Optional[dict] = None,
                           now=None) -> dict:
    """THE receipt. ONE per fire, written BEFORE the post (BRIEFFIX1 Item C).

    Carries the id map (`confirm_ids`) and the day-intent PROPOSAL, so a tap
    resolves positionally against what was actually on screen. Both orders of
    receipt-and-post lose something when a fire dies in the middle; they do not
    lose the SAME thing. Receipt-then-post leaves a receipt with no post, which
    this product has always accepted. Post-then-receipt leaves a numbered
    surface the record cannot explain, and every one-tap action on it lands on
    whatever used to be at that position.

    The capture leg's own bookkeeping rides this same receipt — `window_*`,
    the meeting counts — which is why the capture leg runs BEFORE this call
    and the POST comes after it. Capture is still the fire's final work leg;
    the receipt is bookkeeping and the post is delivery.

    SPEC EODPHASE1 — the per-phase wall time and row counts ride here too,
    ADDITIVELY: `data.phase_durations_ms`, `data.phase_counts`,
    `data.phase_order`, and `data.phases_failed` when a phase raised. Same
    posture as every other receipt extension — a reader that does not know
    these keys behaves identically, and a fire from before this spec simply
    carries none of them. The numbers come from a `PhaseLedger`: `phase_ledger`
    when the caller holds one (the orchestrator, which can also time its own
    capture and close legs into it), else the snapshot the driver left on
    `pack["phase_timings"]`. The explicit ledger WINS, because a caller that
    kept its own is by definition the one that saw more of the fire.

    The phase durations do NOT sum to `duration_ms` and are not meant to: the
    pack build is one leg of the fire, and the capture leg, the close leg and
    the post sit outside it unless the orchestrator timed them into the same
    ledger. `phase_order` says which legs are represented.

    SPEC FLAKEFIX2 — `now` is handed straight to `receipts.log_receipt`, which
    measures the receipt's SLOT PROVENANCE against it. Omitted (every shipped
    call site), the provenance reads the real clock exactly as before. A
    fixture that compares two receipt payloads pins it; see that function's
    own note on why a minute boundary otherwise moves `slot_delta_minutes`.
    """
    from receipts import log_receipt, normalize_fired_via

    pack = pack or {}
    # SPEC COVERQUIET1 — `coverage` is the one `RENDER_ORDER` member whose
    # presence on the pack does NOT settle whether it reached the screen.
    # Every other block's rule stays `pack.get(b) is not None`; coverage's
    # rule is that PLUS `coverage_has_disclosure(pack)` — a quiet day still
    # computes the full reconciled strip (ruling 2: the receipt keeps the
    # record either way) but does not RENDER it, so it must not count as
    # rendered below. `coverage_has_disclosure` is the ONE decision-maker
    # (nothing here re-derives it); the orchestrator's Phase 6 posting
    # bullet calls the SAME function over the SAME pack, so the two answers
    # cannot drift apart.
    #
    # `RENDER_ORDER` and `COMPUTED_ONLY` themselves stay UNTOUCHED and
    # disjoint (the EODSYNTH1 pin) — this is the RECEIPT'S OWN two lists
    # doing the per-fire moving `coverage` needs, not the module-level
    # tuples: "coverage moves between the two lists per the disclosure
    # test" (§0 ruling 2) is implemented here, once.
    _coverage_disclosed = coverage_has_disclosure(pack)
    blocks_rendered = [b for b in RENDER_ORDER if pack.get(b) is not None
                       and (b != "coverage" or _coverage_disclosed)]
    blocks_computed_only = [b for b in COMPUTED_ONLY if pack.get(b) is not None]
    if not _coverage_disclosed and pack.get("coverage") is not None:
        # Leads the list the same way `coverage` leads `BLOCK_ORDER` — first,
        # not appended, so a reader scanning either list meets it in the same
        # relative place.
        blocks_computed_only = ["coverage"] + blocks_computed_only
    data: dict = {
        "surface": SURFACE,
        # THE FIRE'S ID. Minted here, once, and read back by `choice_map` so
        # every gesture resolved against this receipt points at THIS fire.
        "receipt_id": mint_receipt_id(),
        "for_date": pack.get("for_date"),
        "branch": pack.get("branch"),
        "confirm_ids": confirm_ids_from_pack(pack),
        # SPEC EODSYNTH1 — `blocks_rendered` now means what it says. It walks
        # `RENDER_ORDER`, so it names what reached the screen; the blocks that
        # are computed and un-rendered (R-1) are recorded BESIDE it under their
        # own key rather than smuggled into this one. Two claims, two keys: a
        # reader joining on `blocks_rendered` across the EODSYNTH1 boundary
        # gets a truthful answer on both sides of it, which is more than a
        # widened single list could have given them.
        "blocks_rendered": blocks_rendered,
        "blocks_computed_only": blocks_computed_only,
    }
    # The synthesis JOIN — every source ref behind the evening's prose. The
    # chat shows sentences; this is the record that makes each one checkable
    # (§3.1). Counts and refs only, never the prose itself: the paragraph is
    # already persisted with the pack, and a receipt is not a second copy of
    # the surface.
    synth = pack.get("synthesis") if isinstance(pack.get("synthesis"), dict) else None
    if synth is not None:
        try:
            import eod_synthesis as _syn
            data["synthesis"] = {
                "refs": _syn.synthesis_refs(synth),
                "blocks": [b for b in _syn.SYNTHESIS_BLOCKS
                           if str(((synth.get(b) or {}) if isinstance(
                               synth.get(b), dict) else {}).get("text")
                               or "").strip()],
                "n_unreferenced_dropped": synth.get("n_unreferenced_dropped"),
                "n_withheld": synth.get("n_withheld"),
                "n_echoes": len((synth.get("echoes") or {}).get("sentences")
                                or []),
            }
        except Exception:  # noqa: BLE001 — the join is diagnostics; a receipt
            # that cannot describe its own prose still has to write, because
            # the receipt is what the tomorrow confirm resolves against.
            pass
    # SPEC EODCOACH2 — deliberately NOT added here. `pack["coach"]` already
    # carries its own full `refs` (per sentence, on `patterns`/`delta`/`push`
    # and pooled at the top level) and `push_state` — the record this
    # writer's own "nine keys ... and nothing else" pin (EODLEDGER1) closes
    # over. The pack IS the grounding record for this block (EODARC1's
    # contract, extended): the audit copy on disk is what
    # `eod_coach.read_prior_packs` reads back, and the receipt does not need
    # a second copy of it to satisfy that.
    score = pack.get("score") if isinstance(pack.get("score"), dict) else None
    if score is not None:
        data["score"] = {"status": score.get("status"),
                         "n_planned": score.get("n_planned"),
                         "n_closed": score.get("n_closed"),
                         "n_open": score.get("n_open"),
                         "n_not_recorded": score.get("n_not_recorded"),
                         "softened": bool(score.get("softened"))}
    tomorrow = pack.get("tomorrow") if isinstance(pack.get("tomorrow"), dict) else None
    if tomorrow is not None and tomorrow.get("proposal"):
        # The PROPOSAL, not an intent. It rides the receipt so a tap-confirm
        # can be resolved later in the turn without recomputing it — and it is
        # marked `stated: False` all the way down, so nothing downstream can
        # mistake the draft for the CEO's own word.
        data["day_intent_proposal"] = tomorrow["proposal"]
    if isinstance(capture_leg, dict) and capture_leg:
        # SPEC EODSPEED1 adds three keys to this whitelist. The two ledger
        # counts are the 237-row class's own measurement — walks the CRU
        # ledger let this fire skip, and the stale refusals those recorded
        # walks already made — zero-written by the orchestrator, never
        # omitted. `n_review_proposals_suppressed` is TITLEMINT1's mandated
        # receipt key: the orchestrator has passed it since that spec and
        # this whitelist silently dropped it, so the cap ran with no count on
        # any receipt — the exact silence the key exists to prevent.
        # SPEC CAPFENCE1 adds `n_time_fence_deferred` — the count of
        # meetings the 15-minute capture fence, not the batch cap, left
        # unhandled this fire. It rides the SAME omission convention
        # `window_incomplete_before` already keeps: present with the real
        # count when the fence actually deferred something THIS fire,
        # absent when it did not — never a phantom zero for a mechanism
        # that never engaged, which is what keeps a fence-never-bound
        # receipt byte-identical to the pre-CAPFENCE1 shape (§Acceptance's
        # no-op pin).
        for key in ("window_start", "window_end", "window_incomplete_before",
                    "n_meetings", "n_processed", "n_skipped",
                    "n_stale_evidence_skipped",
                    "n_review_proposals_suppressed",
                    "n_cru_walks_ledger_skipped",
                    "n_stale_evidence_ledger_honored",
                    "n_time_fence_deferred",
                    "capture_counts",
                    "held_routing", "n_held"):
            if key in capture_leg and capture_leg[key] is not None:
                data[key] = capture_leg[key]
    # PERSONLOOP1 §3-4 — the person-loop's own arithmetic, riding the SAME
    # receipt shape `capture_counts` rides and keeping the same discipline:
    # counts only, never a name. Without it the V1-style re-measure has
    # nothing to read and "did the bucket shrink?" is unanswerable.
    confirm_block = (pack.get("confirm")
                     if isinstance(pack.get("confirm"), dict) else {})
    pc = confirm_block.get("person_telemetry")
    if isinstance(pc, dict) and pc:
        data["person_candidate_counts"] = {
            "n_candidates": pc.get("n_candidates"),
            "n_rows_blocked": pc.get("n_rows_blocked"),
            "n_top_rows_blocked": pc.get("n_top_rows_blocked"),
            # SPEC EODSYNTH1 — `n_shown` ASSERTS THAT ROWS REACHED THE SCREEN,
            # and on the evening none do any more: the confirm block is
            # computed-only (R-3, the queues moved to the morning). Reporting
            # `len(person_rows)` here would receipt a render that never
            # happened, in the optimistic direction — the exact PERSONLOOP1
            # N-1 defect, arriving through the other door. `n_selected` keeps
            # the pre-build signal readable so "did the waiting pile shrink?"
            # stays answerable across the boundary.
            "n_shown": (len(confirm_block.get("person_rows") or [])
                        if "confirm" not in COMPUTED_ONLY else 0),
            "n_selected": len(confirm_block.get("person_rows") or []),
        }
    # SPEC OVERDUE1 — the fatigue rule's own arithmetic, on the SAME receipt
    # and to the same discipline `person_candidate_counts` keeps: counts and
    # the threshold only, never a title. Without it "how many rows are resting
    # tonight, and did asking actually drain anything?" is unanswerable, which
    # is the measurement this rule will be judged on.
    slipped_block = (pack.get("slipped")
                     if isinstance(pack.get("slipped"), dict) else {})
    if isinstance(slipped_block.get("n_resting"), int):
        data["slipped_fatigue"] = {
            "n_asked": len(slipped_block.get("asked_ids") or []),
            "n_resting": slipped_block["n_resting"],
            "ask_after_days": slipped_block.get("ask_after_days"),
        }
    close = pack.get("close") if isinstance(pack.get("close"), dict) else None
    if close is not None:
        data["close_leg"] = {
            "legs": (pack.get("soften") or {}).get("legs") or [],
            "softened": bool((pack.get("soften") or {}).get("softened")),
        }
    gaps = pack.get("connector_gaps")
    if gaps:
        data["connector_gaps"] = list(gaps)

    # EODPHASE1 — the per-phase diagnostics. Best-effort by design: a fire that
    # cannot describe its own timing must still write its receipt, because the
    # receipt is what the one-tap actions resolve against and instrumentation
    # is never allowed to cost a fire its numbering (BRIEFFIX1 Item C).
    try:
        timings = None
        if phase_ledger is not None:
            timings = phase_ledger.snapshot()
        if not timings:
            candidate = pack.get("phase_timings")
            timings = candidate if isinstance(candidate, dict) else None
        for key in ("phase_durations_ms", "phase_counts", "phase_order",
                    "phases_failed"):
            value = (timings or {}).get(key)
            if value:
                data[key] = value
    except Exception:  # noqa: BLE001
        pass

    # SPEC EODSPEED1 — the budget verdict, from the fire's own reported
    # duration. Absent when no duration was reported: "not measured" is not
    # "within budget". Additive; a reader that does not know the key is
    # unaffected.
    budget = close_budget_read(duration_ms)
    if budget is not None:
        data["close_budget"] = budget

    for k, v in (extra_data or {}).items():
        data.setdefault(k, v)

    surfaced = len(data["confirm_ids"])
    return log_receipt(workspace_root, TASK_ID,
                       fired_via=normalize_fired_via(fired_via) or "scheduled",
                       surfaced=surfaced, duration_ms=duration_ms,
                       late_tier=late_tier, extra_data=data, now=now)


# ---------------------------------------------------------------------------
# Resolving taps against the receipt
# ---------------------------------------------------------------------------

def _eod_receipts(workspace_root) -> list:
    try:
        from receipts import iter_receipts
        rows = iter_receipts(workspace_root, task_ids=[TASK_ID])
    except Exception:  # noqa: BLE001
        return []
    return [r for r in (rows or []) if r.get("type") == RECEIPT_EVENT]


def choice_map(workspace_root, *, now=None) -> dict:
    """The numbered list a tap may resolve against, or an explicit refusal.

    `{"ok", "rows", "refusal", "receipt", "proposal"}`. `ok` is False in
    exactly two situations and the refusals differ because the next move
    differs: no End of Day has ever recorded a numbering (`NO_MAP_REFUSAL`),
    or the newest receipt carries none while a newer fire exists
    (`STALE_MAP_REFUSAL`).

    The refusal is the FENCE, and it is here rather than at each call site for
    the reason `brief_receipt.resolve_mark_done` gives: a stale-map check that
    lives at three call sites is a stale-map check that is missing from one.
    """
    receipts = _eod_receipts(workspace_root)
    base = {"ok": False, "rows": [], "refusal": None, "receipt": None,
            "receipt_id": None, "proposal": None}
    if not receipts:
        base["refusal"] = NO_MAP_REFUSAL
        return base

    newest = None
    newest_with_map = None
    for r in receipts:
        dt = _aware(r.get("dt"))
        if dt is None:
            continue
        if newest is None or dt >= _aware(newest.get("dt")):
            newest = r
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        if isinstance(data.get("confirm_ids"), list):
            if newest_with_map is None or dt >= _aware(newest_with_map.get("dt")):
                newest_with_map = r

    if newest_with_map is None:
        base["refusal"] = NO_MAP_REFUSAL
        return base
    if newest is not None and _aware(newest.get("dt")) > _aware(newest_with_map.get("dt")):
        base["receipt"] = _aware(newest_with_map["dt"]).isoformat()
        base["refusal"] = STALE_MAP_REFUSAL
        return base

    raw = newest_with_map.get("raw") or {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    rows = [r for r in (data.get("confirm_ids") or []) if isinstance(r, dict)]
    dt = _aware(newest_with_map["dt"])
    base["ok"] = True
    base["rows"] = rows
    base["receipt"] = dt.isoformat()
    # A PRE-EODFIX1 receipt carries no id. Its own instant is the only thing
    # that identifies that fire, and it is a true one — so the fallback is
    # `end-of-day:<the receipt's timestamp>` rather than a null pointer, which
    # would silently degrade every tap on an older receipt to
    # `provenance_missing`. Mirrors `needs_review_queue`'s
    # `session:<surface>:<now>` mint.
    base["receipt_id"] = (data.get("receipt_id")
                          or f"{SURFACE}:{dt.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    base["proposal"] = data.get("day_intent_proposal")
    return base


# The verbs a tap may carry, and the block each is legal in. A verb offered on
# a block that never rendered it is a refusal, not a best-effort guess.
ROUTES = {
    "mark done": ("slipped", "confirm"),
    "resolved": ("slipped", "confirm"),
    "push to [date]": ("slipped",),
    "draft": ("slipped",),
    "drop": ("slipped", "confirm"),
    "confirm": ("confirm",),
    # PERSONLOOP1 — the candidate row's three answers, legal ONLY on the
    # candidate block. A `drop` on a candidate would read as "close it", and
    # there is nothing to close: the answer that means "stop asking" is
    # `not a person`, and it says so.
    "add person": (PERSON_CANDIDATE_BLOCK,),
    "same as [existing]": (PERSON_CANDIDATE_BLOCK,),
    "not a person": (PERSON_CANDIDATE_BLOCK,),
}


def resolve_choice(workspace_root, n, *, action: str, now=None) -> dict:
    """`<verb> [n]` → the commitment id to act on, or a refusal.

    Returns `{"ok", "id", "block", "action", "refusal", "n", "source_ref"}`.
    Out-of-range is refused with its own sentence rather than clamped: guessing
    which row the CEO meant is the wrong-close hazard by another route.

    `source_ref` is the pointer the caller stamps on whatever this tap writes:
    `session:<this fire's receipt id>:row<N>`. It is RETURNED rather than
    described in prose (SPEC EODFIX1 §2-6), because a pointer the caller
    composes is a pointer the caller can flatten — which is exactly what
    happened: three gestures on one evening carried one per-day constant.
    """
    out = {"ok": False, "id": None, "block": None, "action": action, "n": n,
           "refusal": None, "source_ref": None, "data": None}
    verb = str(action or "").strip().lower()
    mapping = choice_map(workspace_root, now=now)
    if not mapping["ok"]:
        out["refusal"] = mapping["refusal"]
        return out
    if verb not in ROUTES:
        out["refusal"] = (
            f"I do not have a handler for '{action}' on the End of Day. The "
            f"actions this surface offers are: "
            f"{', '.join(sorted(ROUTES))}.")
        return out
    try:
        index = int(n)
    except (TypeError, ValueError):
        out["refusal"] = ("I could not read that item number. Say the number "
                          "from the list, for example `mark done 2`.")
        return out
    rows = mapping["rows"]
    if index < 1 or index > len(rows):
        total = len(rows)
        # CLOSEID1 DD-4 — this used to end "or tell me what you mean by name."
        # The safety property of this whole surface is POSITIONAL resolution
        # against the receipt this fire rendered; inviting a name after refusing
        # a number hands the target back to a string match, which is how the
        # 2026-08-22 fire closed the wrong promise. The number is the answer.
        out["refusal"] = (
            f"There is no item {index} on this End of Day. It listed {total} "
            f"{'item' if total == 1 else 'items'}. Say the number from the "
            f"list.")
        return out
    row = rows[index - 1]
    block = row.get("block")
    if block not in ROUTES[verb]:
        out["refusal"] = (
            f"Item {index} is not one I can '{verb}' from here. That action "
            f"belongs to a different part of the list.")
        return out
    out["ok"] = True
    out["id"] = str(row.get("id") or "")
    out["block"] = block
    # PERSONLOOP1 — a candidate row's payload travels with the map entry (it
    # has no substrate id to resolve), so the handler needs no session state.
    # None on every other block, which is what every pre-PERSONLOOP1 receipt
    # produces.
    payload = row.get("data")
    out["data"] = dict(payload) if isinstance(payload, dict) else None
    out["source_ref"] = gesture_ref(mapping["receipt_id"], f"row{index}")
    return out


def resolve_intent_confirm(workspace_root, *, pick: Optional[int] = None,
                           now=None) -> dict:
    """The tomorrow block's confirm, resolved POSITIONALLY off the SAME
    receipt `choice_map` already reads (SPEC TOMPICK1 §0.2).

    `pick` is the 1-based RANK the CEO named — "2" picks the second ranked
    candidate. Omitted (a bare "confirm"), it takes rank 1 — today's default
    door, unchanged (§0.2's "bare confirm takes rank 1"). Resolution is
    against the candidate's OWN `rank` field, never list position, so a
    proposal whose rows ever arrived out of rank order still resolves to the
    candidate the CEO actually saw at that number.

    Returns `{"ok", "proposal", "refusal", "source_ref"}`. The `proposal` on
    success carries exactly ONE item — the chosen candidate, re-ranked to 1 —
    so `day_intent.write_from_proposal` (UNCHANGED, SPEC TOMPICK1 §0.3) writes
    ONE intent exactly as it always has; the widening lives entirely here and
    in `compute_tomorrow`'s candidate list, never in the writer. The written
    intent keeps the commitment id it was drafted from (SPEC EODFIX1 §2-5).
    Nothing here writes, and a proposal that is not on the newest receipt is
    refused rather than reconstructed: a re-derived proposal is a NEW guess
    wearing the CEO's confirmation.

    A pre-TOMPICK1 receipt (a single-item proposal, rank 1 only) still
    resolves: the default `pick=None` -> rank 1 finds that one item exactly
    as it always did — the backward door SPEC TOMPICK1's acceptance requires.

    `source_ref` is this gesture's pointer — `session:<receipt id>:confirm` —
    returned for the same reason `resolve_choice` returns its own.
    """
    mapping = choice_map(workspace_root, now=now)
    if not mapping["ok"]:
        return {"ok": False, "proposal": None, "refusal": mapping["refusal"],
                "source_ref": None}
    proposal = mapping.get("proposal")
    items = proposal.get("items") if isinstance(proposal, dict) else None
    if not isinstance(proposal, dict) or not items:
        return {"ok": False, "proposal": None, "source_ref": None,
                "refusal": ("The last End of Day did not put a suggestion on "
                            "the table for tomorrow, so there is nothing to "
                            "confirm. Tell me what tomorrow is about and I "
                            "will write that down.")}
    try:
        rank = 1 if pick is None else int(pick)
    except (TypeError, ValueError):
        rank = None
    by_rank = {int(i["rank"]): i for i in items
              if isinstance(i, dict) and isinstance(i.get("rank"), int)}
    chosen = by_rank.get(rank) if rank is not None else None
    if chosen is None:
        n = len(items)
        return {"ok": False, "proposal": None, "source_ref": None,
                "refusal": NO_SUCH_CANDIDATE_REFUSAL.format(
                    rank=(pick if pick is not None else 1), n=n,
                    plural=("" if n == 1 else "s"),
                    span=("" if n <= 1 else f" through {n}"))}
    single = dict(chosen)
    single["rank"] = 1
    narrowed = dict(proposal)
    narrowed["items"] = [single]
    return {"ok": True, "proposal": narrowed, "refusal": None,
            "source_ref": gesture_ref(mapping["receipt_id"], "confirm")}


def orphan_end_of_day_finding(workspace_root, *, now=None,
                              window_minutes: int = 20) -> Optional[dict]:
    """A fire that posted without recording its numbering.

    Mirrors `brief_receipt.orphan_brief_finding` for this surface: the newest
    `pack_run` for this task carrying NO `confirm_ids` while the surface it
    described offered numbered actions is a named red line on the health read,
    never a silence. Read-only, never raises.
    """
    now = _aware(now) or _dt.datetime.now(_dt.timezone.utc)
    receipts = _eod_receipts(workspace_root)
    if not receipts:
        return None
    newest = None
    for r in receipts:
        dt = _aware(r.get("dt"))
        if dt is None:
            continue
        if newest is None or dt >= _aware(newest.get("dt")):
            newest = r
    if newest is None:
        return None
    dt = _aware(newest.get("dt"))
    if now - dt < _dt.timedelta(minutes=window_minutes):
        return None
    raw = newest.get("raw") if isinstance(newest.get("raw"), dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    if isinstance(data.get("confirm_ids"), list):
        return None
    if data.get("surface") != SURFACE:
        # A pre-EOD1 past-meetings receipt. It never claimed a numbering, so
        # it cannot have lost one — reporting it would train the reader to
        # ignore this finding.
        return None
    return {"check": "end-of-day-posted-without-numbering",
            "line": ORPHAN_LINE,
            "receipt": dt.isoformat()}


__all__ = [
    "SURFACE", "SKILL_NAME", "TASK_ID", "MORNING_TASK_ID", "RECEIPT_EVENT",
    # SPEC EODPHASE1 — the phase vocabulary and its ledger.
    "PHASE_ALARMS", "PHASE_BRIEF_STATE", "PHASE_MORNING_READ",
    "PHASE_CLOSURES", "PHASE_LEDGER", "PHASE_SCORE", "PHASE_WINS",
    "PHASE_SLIPPED", "PHASE_CONFIRM", "PHASE_TOMORROW", "PHASE_SIGN_OFF",
    "PHASE_COVERAGE", "PHASE_CATCHUP", "PHASE_WEEK_ROLLUP", "PHASE_RENDER",
    "PHASE_CAPTURE", "PHASE_CLOSE", "PHASE_POST",
    "PACK_PHASES", "LEG_PHASES", "ALL_PHASES", "PhaseLedger",
    # SPEC EODLEG1 — the receipt-shape guard.
    "receipt_missing_capture_phase", "scan_capture_phase_violations",
    "BLOCK_ORDER", "MAX_SLIPPED_ROWS", "MAX_CONFIRM_ROWS",
    "MAX_TOMORROW_ROLLOVER",
    # SPEC EODSYNTH1
    "RENDER_ORDER", "COMPUTED_ONLY", "SYNTHESIS_BLOCKS", "NUMBERED_BLOCKS",
    "SECTION_KEY_MIGRATIONS", "TONE_RETIRED_VALUE", "TONE_SUCCESSOR_VALUE",
    "TONE_PRESERVED_KEY", "migrate_section_config",
    "migrate_section_config_on_disk", "slipped_prose_enabled",
    "ASK_STATE_ASK", "ASK_STATE_REST", "ASK_STATE_NONE",
    "overdue_ask_state", "apply_overdue_ask",
    "PHASE_SYNTHESIS", "todays_decisions", "todays_notes", "declared_arcs",
    "mark_lane_asked",
    "NO_PLAN_LINE", "NOT_RECORDED", "NOT_RECORDED_LINE", "NO_CLOSE_RECORDED",
    "NO_SCORE_RECORDED",
    "SIGN_OFF_CLEAR", "SOFTEN_LINE", "NO_WINS_LINE",
    # SPEC OVERDUE1 — the fatigue rule: ask once, then let the row rest.
    "OVERDUE_ASK_AFTER_DAYS", "OVERDUE_ASK_CONFIG_KEY",
    "OVERDUE_ASK_LABEL", "OVERDUE_ASK_LABEL_ONE_DAY",
    "OVERDUE_RESTING_LINE", "OVERDUE_RESTING_LINE_ONE",
    "overdue_ask_label", "resting_line", "overdue_ask_after_days",
    "mark_slipped_asked",
    "WINDOW_MORNING_ANCHOR", "WINDOW_DAY_FLOOR",
    "MORE_LINE_SHAPE", "MORE_LINE_TEMPLATES", "more_line",
    "WINS_MORE_LINE_ANCHOR", "WINS_MORE_LINE_DAY_FLOOR", "WINS_MORE_LINES",
    "SLIPPED_MORE_LINE", "CONFIRM_MORE_LINE",
    # SPEC EODLEDGER1 — coverage, the ledger, the catch-up read.
    "CAP_MAIL", "CAP_CHAT", "CAP_CALENDAR", "CAP_MEETINGS",
    "COVERAGE_CAPABILITIES", "COVERAGE_LABELS", "CURSOR_STALE_DAYS",
    "COVERAGE_READ_THROUGH", "COVERAGE_STALE", "COVERAGE_NOT_READ",
    "COVERAGE_NOT_READ_NO_REASON", "COVERAGE_NEVER_ADVANCED",
    "COVERAGE_DEGRADED",
    "COVERAGE_CALENDAR_READ", "COVERAGE_CALENDAR_ABSENT",
    "COVERAGE_CALENDAR_ABSENT_REASON",
    "COVERAGE_MEETINGS", "COVERAGE_MEETINGS_UNKNOWN",
    "COVERAGE_MEETINGS_N", "COVERAGE_MEETINGS_N_UNREAD",
    "COVERAGE_MEETINGS_BACKLOG", "COVERAGE_MEETINGS_BACKLOG_UNREAD",
    "COVERAGE_UNSOURCED",
    "compute_coverage", "capture_aperture",
    # SPEC MEETCOUNT1 — the ONE producer for the meetings count + the briefs.
    "MEETING_BRIEFED", "MEETING_ALREADY_PROCESSED",
    "MEETING_DUPLICATE_FOLDED", "MEETING_SKIPPED", "MEETING_BRIEF_FAILED",
    "MEETING_UNSTATED", "MEETING_RENDER_STATUSES",
    # SPEC EODSPEED1 — the incremental-capture vocabulary + the budget.
    "MEETING_BRIEFED_PRIOR", "MEETING_BRIEFED_PRIOR_LABELS",
    "CLOSE_BUDGET_MS", "close_budget_read",
    # SPEC CAPFENCE1 — the capture leg's own stopping rule.
    "CAPTURE_FENCE_MS", "capture_fence_elapsed_ms",
    "capture_fence_should_defer", "capture_fence_window_marker",
    "COVERAGE_DISCLOSURE_FENCE_LEAD_ONE", "COVERAGE_DISCLOSURE_FENCE_LEAD",
    "MEETING_REDUCTION_LABELS", "COVERAGE_MEETINGS_RECONCILED",
    "COVERAGE_MEETINGS_RECONCILED_NO_WINDOW",
    "meeting_render_set", "reconcile_meetings_line",
    # SPEC COVERQUIET1 — coverage renders only when it has something to say.
    "COVERAGE_DISCLOSURE_REDUCTION_LEAD", "COVERAGE_DISCLOSURE_DEFERRAL_LEAD_ONE",
    "COVERAGE_DISCLOSURE_DEFERRAL_LEAD", "COVERAGE_DISCLOSURE_DEFERRAL_LEAD_UNKNOWN",
    "COVERAGE_DISCLOSURE_CONNECTOR_GAP_LEAD",
    "coverage_has_disclosure", "coverage_disclosure_lead",
    "coverage_render_lines",
    "NO_OPENING_FIGURE", "NO_OPENING_FIGURE_LINE", "LEDGER_LINE",
    "LEDGER_MOVEMENT", "LEDGER_NO_OPENING_FIGURE", "LEDGER_STATUSES",
    "LEDGER_RESIDUAL_LINE", "OPENING_FIGURE_ORIGIN",
    "opening_book", "opens_since", "compute_ledger",
    "CATCHUP_TIER", "CATCHUP_LABEL", "CATCHUP_COMPRESSED_LINE",
    "CATCHUP_CAPPED_LINE", "compute_catchup_read",
    "NO_TOMORROW_INTENT_LINE",
    "SLIPPED_VERBS", "CONFIRM_VERBS", "TOMORROW_VERBS", "ROUTES",
    # SPEC TOMPICK1 — up to three ranked tomorrow candidates, picked
    # positionally.
    "TOMORROW_CANDIDATE_WHY_MATCHED", "TOMORROW_CANDIDATE_WHY_OPEN",
    "NO_SUCH_CANDIDATE_REFUSAL",
    "NO_MAP_REFUSAL", "STALE_MAP_REFUSAL", "ORPHAN_LINE",
    "workspace_today", "day_branch",
    "morning_fire", "read_morning_digest", "closures_since", "ClosureWindow",
    "unsourced_closes", "check_first_move", "FIRST_MOVE_STATUSES",
    "FIRST_MOVE_OPEN", "FIRST_MOVE_STALE", "FIRST_MOVE_UNVERIFIABLE",
    "compute_score", "compute_wins", "compute_slipped", "compute_confirm",
    "compute_tomorrow", "compute_sign_off",
    "week_rollup", "development_read_slot", "soften_floor",
    "compute_person_candidates", "PERSON_CANDIDATE_BLOCK",
    "confirm_ids_from_pack", "log_end_of_day_receipt",
    "mint_receipt_id", "gesture_ref",
    "choice_map", "resolve_choice", "resolve_intent_confirm",
    "orphan_end_of_day_finding",
]
