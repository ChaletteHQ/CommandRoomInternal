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
    `precision_gate` because a gate no instruction layer names is a gate that
    is never consulted (the instruction-layer-gap finding, REVIEW_PREC1 N-1).

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

# The block order IS the render contract (SPEC BK2's table, EOD1 §3).
#
# SPEC EODLEDGER1 inserts `coverage` FIRST, immediately after `alarm_lines`.
# The order is the argument: a reader cannot weigh a number until they know
# what aperture produced it, and every block below coverage is a number. It
# sits under the alarms for the same reason the alarms sit above everything —
# a substrate that is not syncing outranks a statement about what was read.
BLOCK_ORDER = ("alarm_lines", "coverage", "score", "wins", "slipped",
               "confirm", "tomorrow", "sign_off")

# Render bounds. A cap is a render bound, never a silence (the :299 doctrine).
MAX_SLIPPED_ROWS = 3
MAX_CONFIRM_ROWS = 5
MAX_WIN_ROWS = 6
MAX_TOMORROW_ROLLOVER = 3


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

# The tomorrow block's two taps.
TOMORROW_VERBS = ("confirm", "edit [change]")

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
    else:
        meetings_line = COVERAGE_MEETINGS_UNKNOWN
    caps[CAP_MEETINGS] = {"capability": CAP_MEETINGS,
                          "read": bool(ap.get("known")),
                          "aperture": ap or None, "line": meetings_line}
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
    for name in ("threads", "orgs", "projects"):
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

def compute_slipped(*, brief_state: dict, morning: dict,
                    todays_meetings: Optional[Iterable[dict]] = None,
                    processed_meeting_ids: Optional[Iterable[str]] = None,
                    now_iso: Optional[str] = None,
                    cap: int = MAX_SLIPPED_ROWS,
                    softened: bool = False,
                    lane_total: Optional[int] = None) -> dict:
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
    """
    state = brief_state or {}
    dropped_ids = {str(d.get("commitment_id"))
                   for d in (state.get("dropped") or [])
                   if isinstance(d, dict) and d.get("commitment_id")}
    planned = {str(i) for i in ((morning or {}).get("needs_attention_ids") or [])}

    candidates = []
    for row in (state.get("needs_attention") or []):
        if not isinstance(row, dict):
            continue
        cid = str(row.get("commitment_id") or "")
        if not cid or cid in dropped_ids:
            continue
        candidates.append({
            "commitment_id": cid,
            "title": str(row.get("title") or "").strip(),
            "due": row.get("due"),
            "overdue": bool(row.get("overdue")),
            "on_this_mornings_plan": cid in planned,
            "gate_source": "brief_state.needs_attention",
            "verbs": list(SLIPPED_VERBS),
        })

    # Rank: overdue first, then what the morning actually asked for, then the
    # rest. Nothing here re-scores an item — the gate already decided which
    # items may be claimed at all; this only decides which three are shown.
    candidates.sort(key=lambda r: (not r["overdue"],
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
    n_total = len(candidates)
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
    }


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
    """
    from day_intent import load_day_intent

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

    lane = [r for r in ((brief_state or {}).get("needs_attention") or [])
            if isinstance(r, dict)]
    rollover = [{"commitment_id": str(r.get("commitment_id") or ""),
                 "title": str(r.get("title") or "").strip(),
                 "due": r.get("due"),
                 "overdue": bool(r.get("overdue"))}
                for r in lane][:cap]

    proposal = None
    if record is None:
        # The auto-draft: top needs-attention ∩ tomorrow's meetings, falling
        # back to the top of the lane. At most three items — the day_intent
        # cap is the point of that record, and a proposal that would be
        # refused at the writer is not a proposal.
        titles = [t for t in (e["title"] for e in events) if t]
        picked = []
        for r in rollover:
            if not r["title"]:
                continue
            hit = any(_shares_words(r["title"], t) for t in titles)
            picked.append({"text": r["title"], "commitment_id": r["commitment_id"],
                           "matched_meeting": hit})
        picked.sort(key=lambda p: (not p["matched_meeting"],))
        items = picked[:3]
        if items:
            rows = []
            for n, i in enumerate(items, start=1):
                row = {"text": i["text"], "rank": n}
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
    """
    urgent = []
    for row in (slipped or {}).get("rows") or []:
        if row.get("overdue"):
            urgent.append(row)
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

def confirm_ids_from_pack(pack: dict) -> list:
    """The one-tap id map, in the order the surface numbers them.

    ORDER IS THE CONTRACT. `apply-choices` resolves `[n]` positionally against
    this list, so it is built from the pack's own rendered rows and in the
    pack's own order: slipped rows first (they carry the push/draft/drop
    verbs), then the confirm rows, then (PERSONLOOP1) the confirm block's
    person-candidate rows. A surface that renumbers without rewriting this
    list is the wrong-close hazard, which is why the list is DERIVED here
    instead of typed by the orchestrator.

    The candidate rows are APPENDED LAST on purpose: every number a pre-
    PERSONLOOP1 receipt handed out keeps pointing at the same row, so the
    change cannot renumber anything that already existed. A candidate entry
    also carries its own `data` (name key, spellings, org) because a
    candidate has no substrate id to look up — the row IS the payload, and
    the alternative is session state.
    """
    ids = []
    for row in ((pack or {}).get("slipped") or {}).get("rows") or []:
        cid = row.get("commitment_id")
        if cid:
            ids.append({"n": len(ids) + 1, "id": str(cid), "block": "slipped"})
    confirm = (pack or {}).get("confirm") or {}
    for row in confirm.get("rows") or []:
        cid = row.get("commitment_id")
        if cid:
            ids.append({"n": len(ids) + 1, "id": str(cid), "block": "confirm"})
    for row in confirm.get("person_rows") or []:
        wire = row.get("n")
        if wire:
            ids.append({"n": len(ids) + 1, "id": str(wire),
                        "block": PERSON_CANDIDATE_BLOCK,
                        "data": dict(row.get("data") or {})})
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
                           extra_data: Optional[dict] = None) -> dict:
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
    """
    from receipts import log_receipt, normalize_fired_via

    pack = pack or {}
    data: dict = {
        "surface": SURFACE,
        # THE FIRE'S ID. Minted here, once, and read back by `choice_map` so
        # every gesture resolved against this receipt points at THIS fire.
        "receipt_id": mint_receipt_id(),
        "for_date": pack.get("for_date"),
        "branch": pack.get("branch"),
        "confirm_ids": confirm_ids_from_pack(pack),
        "blocks_rendered": [b for b in BLOCK_ORDER if pack.get(b) is not None],
    }
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
        for key in ("window_start", "window_end", "window_incomplete_before",
                    "n_meetings", "n_processed", "n_skipped",
                    "n_stale_evidence_skipped", "capture_counts",
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
            "n_shown": len(confirm_block.get("person_rows") or []),
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
    for k, v in (extra_data or {}).items():
        data.setdefault(k, v)

    surfaced = len(data["confirm_ids"])
    return log_receipt(workspace_root, TASK_ID,
                       fired_via=normalize_fired_via(fired_via) or "scheduled",
                       surfaced=surfaced, duration_ms=duration_ms,
                       late_tier=late_tier, extra_data=data)


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
        out["refusal"] = (
            f"There is no item {index} on this End of Day. It listed {total} "
            f"{'item' if total == 1 else 'items'}. Say the number from the "
            f"list, or tell me what you mean by name.")
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


def resolve_intent_confirm(workspace_root, *, now=None) -> dict:
    """The tomorrow block's one-tap confirm, resolved off the SAME receipt.

    Returns `{"ok", "proposal", "refusal", "source_ref"}`. The caller writes it
    through `day_intent.write_from_proposal(...)` — the CEO tapped, so the
    record is now stated, and the proposal's ITEMS travel whole so the written
    intent keeps the commitment ids it was drafted from (SPEC EODFIX1 §2-5).
    Nothing here writes, and a proposal that is not on the newest receipt is
    refused rather than reconstructed: a re-derived proposal is a NEW guess
    wearing the CEO's confirmation.

    `source_ref` is this gesture's pointer — `session:<receipt id>:confirm` —
    returned for the same reason `resolve_choice` returns its own.
    """
    mapping = choice_map(workspace_root, now=now)
    if not mapping["ok"]:
        return {"ok": False, "proposal": None, "refusal": mapping["refusal"],
                "source_ref": None}
    proposal = mapping.get("proposal")
    if not isinstance(proposal, dict) or not proposal.get("items"):
        return {"ok": False, "proposal": None, "source_ref": None,
                "refusal": ("The last End of Day did not put a suggestion on "
                            "the table for tomorrow, so there is nothing to "
                            "confirm. Tell me what tomorrow is about and I "
                            "will write that down.")}
    return {"ok": True, "proposal": proposal, "refusal": None,
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
    "BLOCK_ORDER", "MAX_SLIPPED_ROWS", "MAX_CONFIRM_ROWS",
    "MAX_TOMORROW_ROLLOVER",
    "NO_PLAN_LINE", "NOT_RECORDED", "NOT_RECORDED_LINE", "NO_CLOSE_RECORDED",
    "NO_SCORE_RECORDED",
    "SIGN_OFF_CLEAR", "SOFTEN_LINE", "NO_WINS_LINE",
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
    "NO_OPENING_FIGURE", "NO_OPENING_FIGURE_LINE", "LEDGER_LINE",
    "LEDGER_MOVEMENT", "LEDGER_NO_OPENING_FIGURE", "LEDGER_STATUSES",
    "LEDGER_RESIDUAL_LINE", "OPENING_FIGURE_ORIGIN",
    "opening_book", "opens_since", "compute_ledger",
    "CATCHUP_TIER", "CATCHUP_LABEL", "CATCHUP_COMPRESSED_LINE",
    "CATCHUP_CAPPED_LINE", "compute_catchup_read",
    "NO_TOMORROW_INTENT_LINE",
    "SLIPPED_VERBS", "CONFIRM_VERBS", "TOMORROW_VERBS", "ROUTES",
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
