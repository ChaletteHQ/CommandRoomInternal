#!/usr/bin/env python3
"""
MEASURE1 — the number, or none of it counts (SPEC_FLOW1 Lane D, 2026-09-07).

WHY THIS EXISTS
---------------
Night 10 rebuilds the intake door (INTAKE1), the exit door (EXIT1) and the
plate (ONEPLATE1) on one claim: the plate shrinks. Nothing on M's book
records whether it did. Every number quoted in the FLOW1 spec was produced by
a one-off replay somebody ran by hand in a chat, and a one-off cannot show a
trend, cannot show a REGRESSION, and is gone the moment the session ends.

So the product measures itself, every day, on its own book:

    per workspace per day   rows in · rows out BY ROUTE · open · pages
    per week                touches = questions shown + taps requested + fires

and the measure lands in three places and no others:

  * the LEDGER, as one `flow_measure` event per closed day (the series);
  * the WRAP, as ONE line ("This week: 38 came in, 61 went out, 256 on your
    plate.") — the whole customer surface this lane reaches;
  * the OPERATOR REPORT, as the window's totals for this workspace.

No new surface. No new question. No new tap. The design rule's three
questions, in one line: it reaches the weekly wrap; it makes it LONGER by one
line, which is the one length increase M asked for by name; it adds no touch.

ONE FUNCTION, THREE WINDOWS (the ONEPLATE1 discipline, applied to flow)
----------------------------------------------------------------------
`window_counts` is the only place a flow number is computed. The daily job
calls it over a day, the wrap calls it over the week, the operator report
calls it over its own window. Three surfaces cannot disagree about the same
measurement because there is one measurement. The same reasoning that put
every plate count behind `plate_view.plate_numbers` puts every flow count
behind this function — and the two never overlap: THIS module never counts
the open plate itself. `open` and `pages` come from `plate_view` and nowhere
else (see `plate_leg`), because a second tally of the open book is exactly
the four-totals-for-one-book defect ONEPLATE1 exists to end.

THE ROUTES, AND WHICH OF THEM EXIST TODAY
-----------------------------------------
A row leaves the plate one of five ways. Each route is read by ITS OWN
MARKER on the ledger — never inferred from a surface, never from prose.

THE DOOR'S OWN MARKER IS ASKED FIRST (fix round 1, reviewer F-2). EXIT1
stamps `data.exit_route` ∈ {`own_word`, `fact`, `silence`} on every close its
three doors make, which is a purpose-built statement of WHICH DOOR — exactly
the question this classifier asks — so `route_of` reads it before anything
else, the way `change_feed.py` already does. The reason/actor tables below
stay as the second ask, for the shipped rails that predate it and for the
history already on the book. Read at EXIT1 `cdb046d8` (branch `exit1`,
2026-09-07); that branch was never edited by this lane.

  tap       the customer's own gesture, and THE RESIDUAL: a close a person
            made that no automatic door below claims.
            `data.user_confirmed: true` on the closure family; after ATTRIB2
            merges, `event_types.is_customer_act` answers it (the stamp
            `data.actor_kind: "person"` first, then the confirmed flag, then
            the source, then the actor's spelling). This module ASKS
            `event_types` for that reader when it is there and falls back to
            the flag when it is not — one seam, named, never a second copy of
            ATTRIB2's ladder.
  fact      a system fact closed it: the sent-mail rail
            (`resolved_by: "sent_reconcile"`), the calendar closer
            (`confirmed_by: "calendar"`), the transcript closer
            (`confirmed_by: "transcript"` — OFF since v5.29.0, so this leg
            reads history and should measure ~0 going forward).
  own_word  EXIT1 route 2: M's own completion statement closed the row.
            EXIT1 (`cdb046d8`) writes `data.exit_route: "own_word"` plus
            `confirmed_by: "own_word"` and `resolved_by`/`source_skill`
            `"exit-own-word"`; all three are read here. THE WRITER IS NOT IN
            THIS TREE until EXIT1 merges, so on this tree the route reads 0
            and `seams()` says so — liveness asks whether `exit_doors` is
            importable, so the row self-corrects on the merged tree with no
            code change.
  silence   EXIT1 route 3: no movement from either side, so the row let go.
            EXIT1 writes `data.exit_route: "silence"` and
            `resolved_by`/`source_skill` `"exit-silence"` on its 60-day
            let-go. Its v5.29.0 ANCESTOR is also running —
            `commitment_backlog_sweep.AGE_OUT_REASON` ("aged_out"), the
            weekly amnesty on 30 quiet days. This module counts BOTH
            spellings under `silence` rather than reporting 0 for a route
            that demonstrably runs, and the BUILD record says so in those
            words. One table, one column — EXIT1's arrival opens no sixth.
  lapse     nobody answered inside the review window and the guess expired
            (`event_types.NON_DISMISSAL_RESOLUTION_REASONS`). This is the
            470-a-month drain the analysis called the real outflow.

A close matching no marker lands in `other`, which is REPORTED, never
folded into a route. `out` is the sum of the five routes plus `other`, so
the total can never be quietly smaller than the truth. A route counted twice
would be worse than a route counted zero, so `route_of` returns exactly one
answer per event and its precedence is pinned by the suite.

IDEMPOTENCE — WHY RE-RUNS WRITE NOTHING (FOLD1-A)
-------------------------------------------------
FOLD1-A makes the maintenance run fire 3-4 times a day. A daily measure that
recomputed "today so far" would write a different row at every fire and the
series would carry four contradicting rows per day.

So the job measures COMPLETE DAYS ONLY — the workspace-local days that have
already ended — and a day that already carries a row is never measured
again. Re-runs on the same day therefore write NOTHING (not a superseding
row: there is nothing to supersede, because the input to a closed day's
count does not change on the clock). A day whose events arrive late is
BOUNDED by that rule on purpose: the row is the day's record as the product
saw it when the day ended, and rewriting history to make an old row prettier
is the one thing the ledger's append-only rule (LEDGERFENCE1, CONTRACT Rule
31) exists to prevent. Missed days self-heal: the job walks every unmeasured
complete day since its newest row, oldest first, capped at `BACKFILL_DAYS`.

WRITES GO THROUGH THE WRITERS (LEDGERFENCE1, CONTRACT Rule 31)
--------------------------------------------------------------
`flow_measure` events are appended through `event_gate.append_event` and
nothing else; the receipt goes through `receipts.log_receipt`. This module
never opens events.jsonl for writing, and reads it only through `events_io`.

NOTHING PLUMBING REACHES A CUSTOMER SURFACE
-------------------------------------------
`wrap_line` composes the one sentence the wrap prints, verbatim; the surface
never re-derives it and never adds a number the helper did not print (the
`plate_view.wrap_cut` posture). It carries no id, no seq, no route name, no
job id, no path. When a number is genuinely unknown the sentence LEAVES IT
OUT rather than inventing it — a plate whose page count `plate_view` cannot
answer prints no page count.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import event_types as _event_types  # noqa: E402


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break."""
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001 - a resolver that raises is worse
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: The ledger row this lane writes. Registered in
#: `shared/data-schemas/events.schema.json` with its row in
#: `shared/EVENT_TYPES.md` (writer: this module; consumers: `wrap_line`,
#: `operator_block`, `measured_days`).
MEASURE_EVENT_TYPE = "flow_measure"

#: The maintenance job id (a JOB inside the already-authorized `maintenance`
#: task — never a scheduled task of its own, so it costs no client a Run Now).
JOB_ID = "daily-measure"

SOURCE_SKILL = "maintenance"

#: THE routes out, in report order. `other` is deliberately not one of them.
ROUTES: tuple = ("fact", "own_word", "silence", "tap", "lapse")
ROUTE_OTHER = "other"

#: EXIT1's own route stamp (`exit_doors.py` @ `cdb046d8`, read 2026-09-07,
#: never edited by this lane). Its three doors write this key on every close
#: they make, and `change_feed.py` already reads it. It is a statement of
#: WHICH DOOR — the exact question `route_of` asks — so it is asked FIRST.
EXIT_ROUTE_KEY = "exit_route"

#: How far back the job will backfill unmeasured complete days in one fire.
#: A fresh install measuring a year of history in one pass would spend the
#: fire on arithmetic nobody asked for; thirty days covers any laptop that
#: was closed and self-heals the rest over subsequent fires.
BACKFILL_DAYS = 30

#: The window the wrap and the value receipt share (QUIET1's week).
WEEK_DAYS = 7
#: PLATENUM1 4.4 — the window phrase's month names, spelled here rather
#: than through a platform-specific strftime directive.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#: Route markers, by route, read off the event's own `data`.
#:
#: `reasons` are values of `event_types.RESOLUTION_REASON_KEY`;
#: `confirmed_by` are values of the evidence-door field. A route with an
#: EMPTY marker set is a route whose marker does not exist yet in this tree
#: — it counts 0 and `seams()` names it. Widening this table is how a new
#: route marker joins; nothing else changes.
ROUTE_REASONS: dict = {
    # POLICY1-A / POLICY1-B / the sent rail. `sent_reconcile` is matched on
    # the ACTOR field, not a reason, because that is how the rail writes it.
    # `exit-proof` is EXIT1's FACT_SOURCE_SKILL (`exit_doors.py` @ cdb046d8),
    # written as `resolved_by` AND `source_skill` on every proof close.
    "fact": {
        "reasons": frozenset({_event_types.AUTO_TRANSCRIPT_CLOSE_REASON}),
        "confirmed_by": frozenset(_event_types.AUTO_CLOSE_CONFIRMED_BY_VALUES),
        "resolved_by": frozenset({"sent_reconcile", "exit-proof"}),
    },
    # EXIT1 route 2 — `OWN_WORD_CONFIRMED_BY` / `OWN_WORD_SOURCE_SKILL`
    # (@ cdb046d8). The WRITER is not in this tree, so `live_routes()` still
    # reports the route dark here; the markers are read the moment it lands.
    "own_word": {"reasons": frozenset(),
                 "confirmed_by": frozenset({"own_word"}),
                 "resolved_by": frozenset({"exit-own-word"})},
    # EXIT1 route 3 (`SILENCE_SOURCE_SKILL` @ cdb046d8) AND its shipped
    # ancestor, the age-out amnesty, which IS running today.
    "silence": {"reasons": frozenset({"aged_out"}), "confirmed_by": frozenset(),
                "resolved_by": frozenset({"exit-silence"})},
    # `tap` is not a reason at all — it is WHO acted (see `_is_tap`).
    "tap": {"reasons": frozenset(), "confirmed_by": frozenset(),
            "resolved_by": frozenset()},
    "lapse": {"reasons": frozenset(_event_types.NON_DISMISSAL_RESOLUTION_REASONS),
              "confirmed_by": frozenset(), "resolved_by": frozenset()},
}

def _exit_doors_present() -> bool:
    """True when EXIT1's doors are in this tree. A marker with no WRITER is
    not an observation: `own_word`'s markers are in the table above (so the
    route is read correctly the instant EXIT1 merges) but nothing in this
    tree can write one, and a route nothing can write must be reported dark.
    Asking for the module is how that self-corrects without a code change."""
    import importlib.util
    try:
        return importlib.util.find_spec("exit_doors") is not None
    except Exception:  # noqa: BLE001 — an import system that raises is a no
        return False


#: The routes whose marker this tree can actually observe. A route absent
#: here is reported as an OBSERVED ZERO, never as "nothing happened".
def live_routes() -> tuple:
    live = []
    for r in ROUTES:
        if r == "own_word":
            # Markers present, writer absent until EXIT1 merges.
            if _exit_doors_present():
                live.append(r)
            continue
        if r == "tap":
            # `_is_tap` always has a reader (see its docstring): ATTRIB2's
            # ladder when it is there, and the customer-chat source set —
            # which every closure on any book carries — when it is not.
            live.append(r)
            continue
        if any(ROUTE_REASONS[r][k] for k in ("reasons", "confirmed_by",
                                             "resolved_by")):
            live.append(r)
    return tuple(live)


def seams() -> dict:
    """Which routes read a real marker in THIS tree, and which read zero
    because their marker has not been built yet. Reported in the operator
    block and in the BUILD record so a zero is never mistaken for a fact."""
    live = set(live_routes())
    return {
        "live": tuple(r for r in ROUTES if r in live),
        "no_marker_yet": tuple(r for r in ROUTES if r not in live),
        "tap_reader": _tap_reader_name(),
        "silence_marker": "commitment_backlog_sweep.AGE_OUT_REASON",
        "exit_route_key": EXIT_ROUTE_KEY,
    }


# ---------------------------------------------------------------------------
# Small helpers (the house shapes — same as quiet.py's)
# ---------------------------------------------------------------------------

def _parse(ts) -> Optional[datetime]:
    from event_time import parse_ts
    return parse_ts(ts)


def _ts(ev) -> str:
    from event_time import event_time
    return event_time(ev)


def _data(ev) -> dict:
    d = ev.get("data") if isinstance(ev, dict) else None
    return d if isinstance(d, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_events(workspace_root) -> list:
    """Owner-tier read through `events_io` (the allowlisted shard reader) —
    never a raw read of events.jsonl from this module."""
    import events_io
    events, _skipped = events_io.load_events_owner_scoped(workspace_root)
    return events


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _reason(d: dict) -> str:
    return str(d.get(_event_types.RESOLUTION_REASON_KEY) or "").strip().lower()


#: The background `source_skill` values, as a STAND-IN for ATTRIB2's own
#: `event_types.MACHINE_SOURCE_SKILLS` while that lane is unmerged. Copied
#: WITH ITS CITATION from `wt-attrib2` @ `dc795933` (fix round 1, 2026-09-07)
#: — the suite pins that this set stays a SUBSET of ATTRIB2's the moment
#: ATTRIB2's constant is importable, so a drift reddens instead of silently
#: crediting a rail's close to the customer. `_is_machine_source` below asks
#: ATTRIB2's reader first and only falls back to this.
_FALLBACK_MACHINE_SOURCES: frozenset = frozenset({
    "reconcile-sent", "reconcile-chat", "reconcile-inbound",
    "maintenance", "session-sweep", "cleanup",
    "review-expiry", "age-out", "identity-reconcile",
    "deal-signals", "dormant-customer-scan", "dormant-scan",
    "calendar-close", "binding-gauge", "lifecycle", "automation-scanner",
    "commitment-backlog-sweep:review-amnesty",
    "commitment-backlog-sweep:review-expiry",
    "meeting-capture",
    "meeting-notes", "past-meetings", "cr-past-meetings",
})


#: The fields a closure can NAME AN ACTOR IN, as a stand-in for ATTRIB2's own
#: `event_types.ACT_ACTOR_FIELDS` while that lane is unmerged (copied with its
#: citation from `wt-attrib2` @ `dc795933`, fix round 2). `names_no_actor`
#: asks ATTRIB2's reader first and only falls back to this.
_FALLBACK_ACTOR_FIELDS: tuple = ("reopened_by", "resolved_by", "undone_by",
                                 "reversed_by", "disowned_by", "restored_by",
                                 "parked_by", "unparked_by")

#: The other two ways a closure says who acted: the actor-kind stamp ATTRIB2
#: writes on every act from now on, and the shipped confirm flag.
_ACTOR_STAMP_FIELDS: tuple = ("actor_kind", "user_confirmed", "confirmed_by")


def names_no_actor(ev) -> bool:
    """True when a closure names NOBODY AND NOTHING — no surface it was
    written from, no actor field, no actor stamp, not even the shipped
    confirm flag.

    FIX ROUND 2, REVIEWER R-1. Two lanes ruled the nameless close opposite
    ways and both rules are right in their own question. ATTRIB2 asks WHO
    GETS THE CREDIT on a surface and rules "absent is not the machine" — a
    legacy row on a person's surface naming no actor only ever came from the
    confirm card, so the customer keeps it. MEASURE1 asks WHICH DOOR THE ROW
    LEFT BY, and a row that names nothing left by no door. Neither lane has
    to give up its own answer: `route_of` asks THIS question first, so the
    nameless close is `other` before the actor question is ever put, and
    ATTRIB2's ladder still decides every close that DOES name something.

    Numbers on M's book the day this was written: 3 nameless closures in
    1,447 ever, 0 in the 30-day window. This is a rule, not a number."""
    if not isinstance(ev, dict):
        return False
    if str(ev.get("source_skill") or "").strip():
        return False
    d = _data(ev)
    fields = getattr(_event_types, "ACT_ACTOR_FIELDS", None) \
        or _FALLBACK_ACTOR_FIELDS
    for key in tuple(fields) + _ACTOR_STAMP_FIELDS:
        v = d.get(key)
        if v is True or (isinstance(v, str) and v.strip()):
            return False
    return True


#: EXIT1's three doors, by the `source_skill` they write (`exit_doors.py` @
#: `cdb046d8`: `OWN_WORD_SOURCE_SKILL` / `FACT_SOURCE_SKILL` /
#: `SILENCE_SOURCE_SKILL`). A DOOR IS NEVER A TAP, whichever reader answers:
#: these are automatic exits by construction. Stated here because ATTRIB2's
#: background list was written before EXIT1 existed and does not yet name
#: them — so without this, an EXIT1 close that lost its stamp would fall to
#: the residual and be credited to the customer. Named for ATTRIB2's owner in
#: the BUILD record rather than patched into ATTRIB2 from this lane.
_EXIT_DOOR_SOURCES: frozenset = frozenset({"exit-own-word", "exit-proof",
                                           "exit-silence"})


def _tap_reader_name() -> str:
    if callable(getattr(_event_types, "is_customer_act", None)):
        return "event_types.is_customer_act"
    if callable(getattr(_event_types, "is_machine_source", None)):
        return "event_types.is_machine_source + data.user_confirmed"
    return "customer-chat source_skill + data.user_confirmed"


def _is_machine_source(source_skill) -> bool:
    """True when the close was written by the product running on its own.

    ATTRIB2's own reader answers when it is there. The fallback repeats its
    rule and only its rule: a `skill:leg` spelling matches on its SKILL HALF
    only when that skill is itself on the list, so a listed LEG never drags
    its bare skill in (`commitment-backlog-sweep:review-amnesty` is the
    machine's; the bare `commitment-backlog-sweep` chat surface is not)."""
    reader = getattr(_event_types, "is_machine_source", None)
    if callable(reader):
        try:
            return bool(reader(source_skill))
        except Exception:  # noqa: BLE001 — a reader that raises is not a verdict
            pass
    s = str(source_skill or "").strip()
    if not s:
        return False
    return (s in _FALLBACK_MACHINE_SOURCES
            or s.split(":", 1)[0] in _FALLBACK_MACHINE_SOURCES)


def _is_tap(ev) -> bool:
    """THE customer-gesture reader, and the residual of `route_of`.

    ATTRIB2's full ladder answers when it is there — one seam, never a second
    copy of it. When it is not, this asks the two doors that ARE on every
    v5.29.0 book:

      1. the SURFACE the close was written from. A close written from a chat
         surface a person acts on — the plate's triage, needs-your-call,
         apply-choices, the morning brief, the workspace catch-all — is that
         person's act. This is ATTRIB2's own first rung, read as the
         complement of its background-source list.
      2. `data.user_confirmed: true`, the shipped flag.

    FIX ROUND 1, REVIEWER F-3. The first cut asked door 2 only, and that flag
    is set on 0 of the 1,447 closures on M's book — so `tap: 0` was published
    as a measured fact while ~54 hand closes sat in `other` and were then
    printed as "by hand" (F-1). A reader that cannot see a thing that plainly
    happened is not a reader.

    A NAMELESS close claims nothing. An empty `source_skill` is not evidence
    that a person acted, so it falls through to `other` rather than being
    credited to the customer — the same discipline as `other` itself.

    FIX ROUND 2, REVIEWER R-1: that rule no longer lives HERE, because here
    it only holds while ATTRIB2's ladder is absent — its `is_customer_act`
    rules a nameless row the customer's, on purpose, and this function
    delegates to it entirely rather than keeping a second copy of the ladder.
    `route_of` asks `names_no_actor` BEFORE it asks this function, so the
    rule holds on either tree and neither lane loses its own answer.
    """
    src = str(ev.get("source_skill") or "").strip() if isinstance(ev, dict) else ""
    if src in _EXIT_DOOR_SOURCES:
        return False
    reader = getattr(_event_types, "is_customer_act", None)
    if callable(reader):
        try:
            return bool(reader(ev))
        except Exception:  # noqa: BLE001 — a reader that raises is not a verdict
            pass
    if _data(ev).get("user_confirmed") is True:
        return True
    return bool(src) and not _is_machine_source(src)


# ---------------------------------------------------------------------------
# The classifier — exactly one route per closure
# ---------------------------------------------------------------------------

def route_of(ev) -> str:
    """The ONE route a closure left by, or "" when the event is not a
    closure at all.

    PRECEDENCE IS PINNED (run_measure1_test section [1]):
    exit_route -> fact -> own_word -> silence -> lapse -> nameless -> tap
    -> other.

    EXIT1's `data.exit_route` LEADS (fix round 1, reviewer F-2). It is the
    door's own statement of which door, written by the door itself, and it is
    already what `change_feed.py` reads. Without it every close EXIT1's three
    doors make would land in `other` and be printed as a hand close — the
    lane named for counting the doors counting none of the new ones.

    THE DOOR'S OWN MARKER LEADS, and `tap` is the residual. Every marker in
    `ROUTE_REASONS` is written by an automatic rail and by nothing else — a
    customer never types `aged_out`, never sets `confirmed_by: calendar`,
    never signs a close `sent_reconcile`. So a close carrying one of those
    left by that door, whoever the ledger names as its actor; `tap` means a
    person closed it and no automatic door claims it.

    THE REPLAY FOUND THIS THE OTHER WAY ROUND (2026-09-07). The first cut
    asked "who acted" first, on the reading of M's ruling that a person's
    act is the person's. Run against the book with ATTRIB2's
    `is_customer_act` in place, all seven of the month's silence closes
    moved into `tap`: the amnesty leg writes under
    `commitment-backlog-sweep:amnesty`, which ATTRIB2 classifies as the
    CUSTOMER's on purpose (the bare sweep is a chat surface). Attribution
    and route are two different questions — M's ruling settles who gets
    credited on a surface, not which door a row left by — and asking them in
    the wrong order silently emptied a whole route. The marker answers the
    door; ATTRIB2 answers the credit.

    THE NAMELESS CLOSE IS ASKED BEFORE THE ACTOR (fix round 2, reviewer
    R-1). `names_no_actor` runs ahead of `_is_tap` so a closure that names
    nothing at all is `other` on EITHER tree — with ATTRIB2 merged (its
    ladder rules such a row the customer's) and without it. Each lane keeps
    its own rule in its own question; see `names_no_actor`.
    """
    if not isinstance(ev, dict) or ev.get("type") != "commitment_resolved":
        return ""
    d = _data(ev)
    stamped = str(d.get(EXIT_ROUTE_KEY) or "").strip().lower()
    if stamped in ROUTES:
        return stamped
    reason = _reason(d)
    confirmed = str(d.get("confirmed_by") or "").strip().lower()
    resolved = str(d.get("resolved_by") or "").strip().lower()
    for route in ("fact", "own_word", "silence", "lapse"):
        spec = ROUTE_REASONS[route]
        if reason and reason in spec["reasons"]:
            return route
        if confirmed and confirmed in spec["confirmed_by"]:
            return route
        if resolved and resolved in spec["resolved_by"]:
            return route
    if names_no_actor(ev):
        return ROUTE_OTHER
    if _is_tap(ev):
        return "tap"
    return ROUTE_OTHER


#: The event types that put a row ON the plate. A REOPEN is one of them
#: (fix round 1, reviewer F-4): it is the row arriving back.
ROW_IN_TYPES: tuple = ("commitment", "commitment_observed",
                       "commitment_reopened")
REOPEN_TYPE = "commitment_reopened"


def _is_row_in(ev) -> bool:
    """A row ARRIVING on the plate. Both intake tiers count as inflow — the
    held tier is still something the product had to take in, and INTAKE1's
    whole claim is that it moves rows from one tier to the other rather than
    making them vanish. The split rides the row (`in_open` / `in_held` /
    `in_reopened`) so the claim is checkable.

    A REOPEN COUNTS IN, IT IS NOT NETTED OFF `out` (fix round 1, reviewer
    F-4). The first cut counted neither, so a row that closed, came back and
    closed again read as TWO exits and NO entries — 102 of the 755 exits on
    M's thirty days, with net drain overstated by about that much. Two ways
    to fix it, and "in" is the right one for what this row means: `in` and
    `out` here are PLATE MOVEMENTS over a window, and the identity a reader
    needs to reconcile is: the open plate at the end of a window is the open
    plate at its start, plus `in`, minus `out`. Netting a reopen
    against `out` would subtract it from a CLOSE THAT HAPPENED IN AN EARLIER
    WINDOW — a row already written and, under Rule 31, never rewritten — so
    a period's outflow would depend on a later period's events. Counting the
    arrival where it arrived keeps every window self-contained and every
    number the ledger's own."""
    return isinstance(ev, dict) and ev.get("type") in ROW_IN_TYPES


def _is_held(ev) -> bool:
    return (ev.get("type") == "commitment_observed"
            or _data(ev).get("pending_review") is True)


# ---------------------------------------------------------------------------
# THE measurement — one function, three windows
# ---------------------------------------------------------------------------

def window_counts(events: Iterable[dict], start: datetime,
                  end: datetime) -> dict:
    """Rows in and rows out by route over `[start, end)`.

    The ONLY place a flow number is computed. Two identities hold on every
    window, and the suite pins both: `out` is the sum of every route plus
    `other`, so the total is never quietly smaller than the ledger; and `in`
    is `in_open + in_held + in_reopened`, so a row that came back is counted
    where it arrived instead of vanishing. Returns plain ints; no surface
    adds to them.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    rows_in = 0
    in_held = 0
    in_reopened = 0
    out_by_route = {r: 0 for r in ROUTES}
    other = 0
    for ev in events or []:
        dt = _parse(_ts(ev))
        if dt is None or dt < start or dt >= end:
            continue
        if _is_row_in(ev):
            rows_in += 1
            if ev.get("type") == REOPEN_TYPE:
                in_reopened += 1
            elif _is_held(ev):
                in_held += 1
            continue
        route = route_of(ev)
        if not route:
            continue
        if route == ROUTE_OTHER:
            other += 1
        else:
            out_by_route[route] += 1
    return {
        "in": rows_in,
        "in_open": rows_in - in_held - in_reopened,
        "in_held": in_held,
        "in_reopened": in_reopened,
        "out": sum(out_by_route.values()) + other,
        "out_by_route": out_by_route,
        "out_other": other,
    }


def window_touches(events: Iterable[dict], start: datetime,
                   end: datetime) -> dict:
    """The touches asked of the customer over `[start, end)` — the design
    rule's own measure ("touches asked of the client = questions shown +
    taps requested + chats fired").

    questions_shown   `quiet.BUDGET_EVENT_TYPE`'s own `n_asked`. Quiet
                      counts what it actually put in front of the reader;
                      re-deriving it here would give the wrap and the value
                      receipt two answers to one question.
    taps_requested    a confirm-tier `brain_proposal` — the row a surface
                      hands the customer to decide. Auto-tier proposals are
                      NOT taps: they apply themselves and narrate, which is
                      the whole point of the tier. `commitment_review_proposed`
                      is not a tap either — it is evidence ON a row, never a
                      question (M's ruling 2026-09-03).
    fires             `pack_run` — every chat and job that fired at the
                      customer. FOLD1-A's whole claim is that this number
                      falls from eight a day.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    try:
        import quiet as _quiet
        budget_type = _quiet.BUDGET_EVENT_TYPE
    except Exception:  # noqa: BLE001 — the vocabulary, not the module, is what we need
        budget_type = "question_budget_spent"
    questions = 0
    taps = 0
    fires = 0
    for ev in events or []:
        dt = _parse(_ts(ev))
        if dt is None or dt < start or dt >= end:
            continue
        t = ev.get("type")
        d = _data(ev)
        if t == budget_type:
            questions += int(d.get("n_asked") or 0)
        elif t == "brain_proposal":
            if str(d.get("tier") or "confirm").strip().lower() == "confirm":
                taps += 1
        elif t == "pack_run":
            fires += 1
    return {"questions_shown": questions, "taps_requested": taps,
            "fires": fires, "touches": questions + taps + fires}


# ---------------------------------------------------------------------------
# The plate leg — plate_view answers, or nobody does
# ---------------------------------------------------------------------------

def plate_leg(workspace_root, *, now_iso: Optional[str] = None) -> dict:
    """`open` and `pages` — from `plate_view`'s ONE projection and from
    nowhere else (SPEC_FLOW1 Lane C item 1).

    Asked in this order, and the answer records WHICH function answered so a
    reader of the series can tell an ONEPLATE1 number from a pre-ONEPLATE1
    one:

      1. `plate_view.surface_numbers`  (ONEPLATE1 — open + widget_pages)
      2. `plate_view.plate_numbers(build_plate(...))`   (same numbers, one
         build this module already has)
      (PLATENUM1 fix round 1, REVIEW F-4 — there used to be a third leg,
      `build_plate(...)["counts"]["total"]`, described here as "merely the
      older spelling". It is not: `counts["total"]` leaves out the
      `pending_review` rows the plate renders, so the leg silently handed
      back a DIFFERENT population than the two above it — 333 where the
      projection says 335. A fallback that answers a different question is
      worse than no answer, so it is gone: no projection, no number.)

    THIS MODULE NEVER COMPUTES EITHER NUMBER. When no step yields a page
    count — which is the case on the v5.29.0 base, where the forty-row cap
    is a text rule and the widget has no page number to state — `pages` is
    None and every surface simply says less. An invented page count would be
    the fifth total for one book, which is the defect ONEPLATE1 closed.
    """
    out = {"open": None, "pages": None, "source": "", "error": ""}
    try:
        import plate_view
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    fn = getattr(plate_view, "surface_numbers", None)
    if callable(fn):
        try:
            nums = fn(workspace_root, now_iso=now_iso)
            if nums.get("ok") is False:
                out["error"] = str(nums.get("error") or "")
                out["source"] = "plate_view.surface_numbers"
                return out
            out["open"] = nums.get("open")
            out["pages"] = nums.get("widget_pages")
            out["source"] = "plate_view.surface_numbers"
            return out
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"

    try:
        view = plate_view.build_plate(workspace_root, now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    if not view.get("ok"):
        out["error"] = str(view.get("error") or "")
        out["source"] = "plate_view.build_plate"
        return out

    pn = getattr(plate_view, "plate_numbers", None)
    if callable(pn):
        try:
            nums = pn(view)
            out["open"] = nums.get("open")
            out["pages"] = nums.get("widget_pages")
            out["source"] = "plate_view.plate_numbers"
            return out
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"

    # PLATENUM1 fix round 1 (REVIEW F-4) — no third leg. `plate_numbers` is
    # THE projection; a tree without it states no open count at all rather
    # than a second population wearing the same label.
    out["source"] = "plate_view.build_plate (no projection)"
    if not out["error"]:
        out["error"] = "plate_view.plate_numbers is unavailable"
    return out


# ---------------------------------------------------------------------------
# Days — the workspace's own day, never UTC (G14 / TZFLAKE1)
# ---------------------------------------------------------------------------

def _workspace_tz(workspace_root):
    """The workspace's zone. `tz.load_workspace_tz` raises rather than
    silently falling back to UTC (TZFLAKE1), and a measure keyed to the
    wrong midnight would move every row in the series by a few hours — so a
    refusal is the right answer, surfaced by the caller."""
    import tz as _tz
    return _tz.load_workspace_tz(workspace_path=workspace_root)


def day_bounds(day: str, tzinfo) -> tuple:
    """UTC-aware `[start, end)` for the workspace-local calendar day
    `YYYY-MM-DD`."""
    y, m, d = (int(p) for p in day.split("-"))
    start = datetime(y, m, d, tzinfo=tzinfo)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_day(dt: datetime, tzinfo) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).strftime("%Y-%m-%d")


def _day_add(day: str, n: int) -> str:
    y, m, d = (int(p) for p in day.split("-"))
    return (datetime(y, m, d) + timedelta(days=n)).strftime("%Y-%m-%d")


def measured_days(events: Iterable[dict]) -> set:
    """The days already carrying a `flow_measure` row. The job's memory —
    read from the ledger, so it survives every machine, and so a row is
    never written twice for one day (the FOLD1-A re-run rule)."""
    out = set()
    for ev in events or []:
        if isinstance(ev, dict) and ev.get("type") == MEASURE_EVENT_TYPE:
            day = str(_data(ev).get("measure_date") or "").strip()
            if day:
                out.add(day)
    return out


# ---------------------------------------------------------------------------
# The daily row
# ---------------------------------------------------------------------------

#: A day whose plate was never read carries NO plate numbers, and says so in
#: words (fix round 1, reviewer F-5).
NO_PLATE_READING: dict = {"open": None, "pages": None, "source": ""}
PLATE_NOT_MEASURED = "not measured"


def measure_day(workspace_root, day: str, *, events: Optional[list] = None,
                tzinfo=None, plate: Optional[dict] = None,
                backfilled: bool = False,
                plate_read_at: Optional[str] = None) -> dict:
    """One day's row: in / out by route / open / pages.

    `plate` is the point-in-time plate leg (`plate_leg`). It is passed IN
    rather than recomputed per day because the plate is a snapshot of NOW,
    not of a past midnight — a past day's open count cannot be reconstructed
    from a projection of the current book, and pretending otherwise would be
    the worst kind of made-up number.

    A BACKFILLED DAY THEREFORE CARRIES NO PLATE NUMBER AT ALL (fix round 1,
    reviewer F-5). The first cut computed one reading and stamped it on every
    day of the catch-up walk, so a first fire wrote thirty rows all carrying
    today's plate — a flat line on the exact column the "the plate shrinks"
    claim rests on, and nothing in the row said it was borrowed. `open` and
    `pages` are `None` on those rows, `plate_note` says "not measured" in
    words, and `plate_backfilled` is true. The one row whose day the reading
    genuinely belongs to carries it, stamped with the reading's REAL time
    (`plate_as_of`), not the constant `"measure_run"` that never said which
    run. A reader charting `open` now drops the nulls instead of graphing a
    number that was never taken.
    """
    evs = events if events is not None else _load_events(workspace_root)
    tzinfo = tzinfo or _workspace_tz(workspace_root)
    start, end = day_bounds(day, tzinfo)
    counts = window_counts(evs, start, end)
    if backfilled:
        plate = dict(NO_PLATE_READING)
        plate_read_at = None
    else:
        plate = plate if plate is not None else plate_leg(workspace_root)
        plate_read_at = plate_read_at or _now_iso()
    row = {
        "measure_date": day,
        "in": counts["in"],
        "in_open": counts["in_open"],
        "in_held": counts["in_held"],
        "in_reopened": counts["in_reopened"],
        "out": counts["out"],
        "out_by_route": dict(counts["out_by_route"]),
        "out_other": counts["out_other"],
        "open": plate.get("open"),
        "pages": plate.get("pages"),
        "plate_source": plate.get("source") or "",
        "plate_as_of": plate_read_at,
        "plate_backfilled": bool(backfilled),
        "plate_note": "" if not backfilled else PLATE_NOT_MEASURED,
        "routes_without_marker": list(seams()["no_marker_yet"]),
    }
    return row


def days_to_measure(workspace_root, *, events: Optional[list] = None,
                    now_iso: Optional[str] = None, tzinfo=None,
                    max_days: int = BACKFILL_DAYS) -> list:
    """Every COMPLETE workspace-local day that has no `flow_measure` row
    yet, oldest first, capped at `max_days`. Today is never in the list —
    it is not over, and measuring it would give FOLD1-A's three or four
    fires three or four different answers for one date."""
    evs = events if events is not None else _load_events(workspace_root)
    tzinfo = tzinfo or _workspace_tz(workspace_root)
    now = _parse(now_iso) or datetime.now(timezone.utc)
    today = local_day(now, tzinfo)
    done = measured_days(evs)
    out = []
    for back in range(max_days, 0, -1):
        day = _day_add(today, -back)
        if day not in done:
            out.append(day)
    return out


def run_measure_job(workspace_root, *, apply: bool = False,
                    now_iso: Optional[str] = None,
                    fired_via=None,
                    max_days: int = BACKFILL_DAYS) -> dict:
    """MEASURE1's maintenance job — the daily write.

    `apply=False` is the dry run (compute + report, no event, no receipt),
    the same posture as identity-reconcile, lifecycle, review-expiry and the
    binding gauge: the registered prompt passes `--apply`, and the flag
    mattering is the point (a dry run that receipted would go permanently
    un-due).

    Re-runs inside one day write NOTHING (see the module docstring): every
    complete day already carries its row, so `days_to_measure` comes back
    empty and the job exits having written neither event nor receipt. That
    is FOLD1-A-safe by construction rather than by a flag someone has to
    remember to set.

    Returns {ran, applied, days_written, rows, refused, reason, summary}.
    """
    # FIX3 F3-6: a literal default IS an explicit value by the time the
    # resolver sees it (the FIX2 M-3 lesson), so this signature says
    # nothing and the seat answers. A legacy or local seat still reads
    # `scheduled`, byte for byte; a merged seat with nothing forwarded
    # reads `manual`, which is what a typed brief actually is.
    fired_via = _effective_fired_via(fired_via)
    import time as _time

    def _refusal(refused: str, reason: str) -> dict:
        return {"ran": False, "applied": False, "days_written": [],
                "rows": [], "refused": refused, "reason": reason,
                "duration_ms": None, "summary": ""}

    try:
        from receipts import FIRED_VIA, normalize_fired_via
    except ImportError:  # pragma: no cover — direct-path fallback
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from receipts import FIRED_VIA, normalize_fired_via
    via = normalize_fired_via(fired_via)
    if via not in FIRED_VIA:
        return _refusal("unknown_fired_via",
                        f"{fired_via!r} is not a fire provenance I can "
                        f"record, so nothing was measured and nothing was "
                        f"written.")

    root = Path(workspace_root)
    if not (root / "_hq" / "data").is_dir():
        return _refusal("not_a_workspace",
                        f"no _hq/data under {root} — this is not a "
                        f"workspace root, so nothing was measured and "
                        f"nothing was written.")
    try:
        tzinfo = _workspace_tz(root)
    except Exception as exc:  # noqa: BLE001 — TZResolutionError and friends
        return _refusal("no_workspace_timezone",
                        f"this workspace has no resolvable time zone "
                        f"({type(exc).__name__}), and a day measured off the "
                        f"wrong midnight is worse than no measure — nothing "
                        f"was written.")

    t0 = _time.perf_counter()
    evs = _load_events(root)
    days = days_to_measure(root, events=evs, now_iso=now_iso, tzinfo=tzinfo,
                           max_days=max_days)
    if not days:
        return {"ran": True, "applied": bool(apply), "days_written": [],
                "rows": [], "refused": None, "reason": None,
                "duration_ms": int((_time.perf_counter() - t0) * 1000),
                "summary": "every complete day already measured"}

    # THE PLATE IS READ ONCE, AND ONLY THE DAY IT BELONGS TO KEEPS IT
    # (fix round 1, reviewer F-5). The reading is a snapshot of now, so it is
    # the record for the day that just ended and for no other. Every other
    # day of the catch-up walk is written with no plate number and says so.
    now_dt = _parse(now_iso) or datetime.now(timezone.utc)
    yesterday = _day_add(local_day(now_dt, tzinfo), -1)
    plate = plate_leg(root, now_iso=now_iso)
    read_at = now_iso or _now_iso()
    rows = [measure_day(root, day, events=evs, tzinfo=tzinfo,
                        plate=plate, plate_read_at=read_at,
                        backfilled=(day != yesterday))
            for day in days]
    out = {"ran": True, "applied": bool(apply), "days_written": [],
           "rows": rows, "refused": None, "reason": None,
           "duration_ms": int((_time.perf_counter() - t0) * 1000),
           "summary": (f"{len(rows)} day(s) measured "
                       f"({rows[0]['measure_date']}..{rows[-1]['measure_date']})")}
    if not apply:
        return out

    written = write_measure_rows(root, rows, now_iso=now_iso)
    out["days_written"] = written
    out["duration_ms"] = int((_time.perf_counter() - t0) * 1000)
    _log_measure_receipt(root, out, fired_via=via)
    return out


def write_measure_rows(workspace_root, rows: list,
                       now_iso: Optional[str] = None) -> list:
    """Append one `flow_measure` event per row THROUGH THE WRITER
    (`event_gate.append_event` — LEDGERFENCE1 / CONTRACT Rule 31). This
    module never opens events.jsonl itself, and never rewrites a row."""
    if not rows:
        return []
    from event_gate import append_event
    events = [{"type": MEASURE_EVENT_TYPE, "source_skill": SOURCE_SKILL,
               "data": dict(row)} for row in rows]
    path = _events_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    append_event(path, events, holder=f"job:{JOB_ID}")
    return [r["measure_date"] for r in rows]


def _log_measure_receipt(workspace_root, out, *, fired_via) -> None:
    """ONE receipt per WRITING run. A run that wrote nothing leaves no
    trace, for the binding gauge's reason: the job simply stays due and
    exits again in milliseconds at the next fire."""
    try:
        from receipts import log_receipt
        log_receipt(
            workspace_root, JOB_ID,
            receipt_type="pack_run",
            fired_via=fired_via,
            surfaced=0,  # a measure is never surfaced by the job itself
            duration_ms=out.get("duration_ms"),
            extra_data={"days_written": list(out.get("days_written") or []),
                        "n_days": len(out.get("days_written") or [])},
        )
    except Exception as exc:  # noqa: BLE001 — loud, never fatal
        print(f"[daily-measure] receipt FAILED: {type(exc).__name__}: {exc}",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# The surfaces
# ---------------------------------------------------------------------------

def window_phrase(since: datetime, now: datetime) -> str:
    """THE WINDOW, IN WORDS (PLATENUM1 4.4).

    A scheduled wrap widens its window to reach back over a missed Friday
    (CATCHUP1 F-2), so the sentence's window is not always a week — and on
    09-11 it was thirty days while the heading still said "this week". The
    reader was left to work it out and the model filled the gap with the
    phrase "over a gap", which names a mechanism and answers nothing.

    One phrase, derived from the window the caller actually measured.
    """
    days = max(1, (now - since).days)
    if days == 1:
        return "Yesterday"
    if days <= WEEK_DAYS:
        return "This week"
    if days <= 31:
        return f"Over the last {days} days"
    return f"Since {_MONTHS[since.month - 1]} {since.day}"


def wrap_line(workspace_root, *, since_iso: Optional[str] = None,
              now_iso: Optional[str] = None,
              events: Optional[list] = None) -> dict:
    """THE one line the weekly wrap prints, composed here and rendered
    VERBATIM (the `plate_view.wrap_cut` posture): the wrap never re-derives
    a flow number and never adds one this helper did not print.

    "This week: 38 came in, 61 went out, 256 on your plate."

    Drop-empty (`text == ""`) when the week saw no movement AND the plate
    has no number to state — a wrap should never carry a line that says
    nothing happened. A number this tree cannot observe is LEFT OUT of the
    sentence rather than printed as zero.

    PLATENUM1 4.4 — THIS IS THE RECAP'S ONE IN/OUT PAIR, and it STATES ITS
    OWN WINDOW. The 09-11 wrap carried two pairs over two windows — "195
    opened, 661 closed" from the plate's delta and "823 came in, 776 went
    out" from here — and narrated the window as "over a gap" because the
    scheduled fire had widened it to thirty days and nothing told the
    reader so in words (B2.8). `plate_view._delta_text` now prints no
    counts in its wrap head; this sentence opens with the window, spelled
    out, every time.
    """
    now = _parse(now_iso) or datetime.now(timezone.utc)
    since = _parse(since_iso) if since_iso else None
    if since is None:
        since = now - timedelta(days=WEEK_DAYS)
    evs = events if events is not None else _load_events(workspace_root)
    counts = window_counts(evs, since, now)
    plate = plate_leg(workspace_root, now_iso=now_iso)

    bits = []
    if counts["in"]:
        bits.append(f"{counts['in']} came in")
    if counts["out"]:
        bits.append(f"{counts['out']} went out")
    open_n = plate.get("open")
    if isinstance(open_n, int):
        bits.append(f"{open_n} on your plate")
    window = window_phrase(since, now)
    text = (f"{window}: " + ", ".join(bits) + ".") if bits else ""
    return {"text": text, "in": counts["in"], "out": counts["out"],
            "open": open_n, "pages": plate.get("pages"),
            "window": window, "window_days": max(1, (now - since).days)}


def operator_block(workspace_root, *, since_iso: str,
                   now_iso: Optional[str] = None,
                   events: Optional[list] = None) -> dict:
    """The flow block for the operator report: the window's totals for THIS
    workspace, plus the week's touches.

    `text` is rendered verbatim. Drop-empty like every other block on that
    report — a workspace with no flow in the window prints nothing rather
    than a row of zeros.

    ONE WORKSPACE, NOT THE FLEET. This is the honest limit of what a
    workspace's own ledger can answer: the operator runs this report per
    seat, and the fleet total is the sum of the seats' blocks. Nothing here
    reads another workspace, and nothing here pretends to.
    """
    now = _parse(now_iso) or datetime.now(timezone.utc)
    since = _parse(since_iso) or (now - timedelta(days=30))
    evs = events if events is not None else _load_events(workspace_root)
    counts = window_counts(evs, since, now)
    touches = window_touches(evs, since, now)
    plate = plate_leg(workspace_root, now_iso=now_iso)
    week = window_touches(evs, now - timedelta(days=WEEK_DAYS), now)

    if not counts["in"] and not counts["out"] and not touches["touches"]:
        return {"text": "", "counts": counts, "touches": touches,
                "plate": plate, "week_touches": week}

    lines = ["Flow this period"]
    lines.append(f"  • {counts['in']} came in, {counts['out']} went out")
    route_bits = [f"{n} {_route_words(r)}"
                  for r, n in counts["out_by_route"].items()
                  if n and _route_words(r)]
    if counts["out_other"]:
        route_bits.append(f"{counts['out_other']} {OTHER_WORDS}")
    if route_bits:
        lines.append("  • Out: " + ", ".join(route_bits))
    if isinstance(plate.get("open"), int):
        pages = plate.get("pages")
        lines.append(f"  • {plate['open']} still open"
                     + (f" across {pages} pages" if isinstance(pages, int) else ""))
    lines.append(f"  • {week['touches']} touches asked of you in the last "
                 f"seven days ({week['questions_shown']} questions, "
                 f"{week['taps_requested']} decisions offered, "
                 f"{week['fires']} scheduled arrivals)")
    return {"text": "\n".join(lines), "counts": counts, "touches": touches,
            "plate": plate, "week_touches": week}


#: Plain words for each route on a customer surface — never the route key.
_ROUTE_WORDS = {
    "fact": "finished by something we could see",
    "own_word": "finished because you said so",
    "silence": "let go after months of quiet",
    "tap": "closed by you",
    "lapse": "expired unanswered",
}


#: `other` IS NOT A DOOR AND NEVER GETS A DOOR'S NAME (fix round 1, reviewer
#: F-1). The first cut printed it as "N by hand" — in the same sentence as
#: "N closed by you" — and on M's book that asserted 199 hand closes he did
#: not make (197 of them the past-meetings transcript closer, which has been
#: OFF since v5.29.0 precisely because he does not trust it). The bucket's
#: whole meaning is that no door was recorded, so its words claim no actor
#: and no door. The suite pins that: it fails on "by hand" and on any other
#: phrase here that names who acted.
OTHER_WORDS = "closed another way — the door was not recorded"

#: The actor claims `OTHER_WORDS` must never make. Pinned in the suite.
OTHER_WORDS_BANNED: tuple = ("by hand", "by you", "you closed", "closed by",
                             "by us", "we closed", "manually")


def _route_words(route: str) -> str:
    """Plain words for a route. A route with no words is OMITTED rather than
    printed as its raw key (fix round 1, reviewer F-7): widening `ROUTES`
    without widening `_ROUTE_WORDS` must never put a plumbing word on the
    operator report. The suite pins the two sets equal, so the omission is a
    backstop and not the plan."""
    return _ROUTE_WORDS.get(route, "")


def main(argv: Optional[list] = None) -> int:
    import argparse
    import json as _json
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — non-console stream
            pass
    ap = argparse.ArgumentParser(description="MEASURE1 — the daily flow measure")
    ap.add_argument("workspace_root")
    ap.add_argument("--apply", action="store_true",
                    help="write the rows and the receipt (default: dry run)")
    ap.add_argument("--now", default=None, help="test seam: ISO 'now'")
    ap.add_argument("--fired-via", default=None,
                    help="the seat decides when nothing is said (receipts.effective_fired_via); a literal default here IS an explicit value by the time the resolver sees it")
    ap.add_argument("--triggered-by", default=None,
                    help="the surface that asked for this run")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    # FIX3 F3-6: export what this run was asked by, so every composer
    # below reads it from one place instead of being threaded through
    # a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    out = run_measure_job(args.workspace_root, apply=args.apply,
                          now_iso=args.now, fired_via=args.fired_via)
    print(_json.dumps(out, indent=2, default=str))
    return 0 if not out.get("refused") else 1


__all__ = [
    "MEASURE_EVENT_TYPE", "JOB_ID", "ROUTES", "ROUTE_OTHER", "ROUTE_REASONS",
    "EXIT_ROUTE_KEY", "ROW_IN_TYPES", "REOPEN_TYPE",
    "NO_PLATE_READING", "PLATE_NOT_MEASURED",
    "OTHER_WORDS", "OTHER_WORDS_BANNED",
    "BACKFILL_DAYS", "WEEK_DAYS",
    "live_routes", "seams", "route_of",
    "window_counts", "window_touches", "plate_leg",
    "day_bounds", "local_day", "measured_days", "measure_day",
    "days_to_measure", "run_measure_job", "write_measure_rows",
    "wrap_line", "operator_block", "window_phrase",
]


if __name__ == "__main__":
    raise SystemExit(main())
