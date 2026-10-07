#!/usr/bin/env python3
"""THE per-commitment movement derivation — one rule, every surface (v4.6.0 MC2).

WHY THIS EXISTS
---------------

R1b (v4.5.2) exposed that the brief's "stuck" number was a lie: the code
computed overdue-by-due-date while the printed caption promised "no movement
21d or blocked on a person" — a metric the system never computed. Undated
items could NEVER be stuck by the code's definition. R1b's honesty fix
relabeled the number "overdue"; THIS module computes the real metric, the
same way thread_activity.py (v4.5.2 C3) became THE thread-staleness
derivation after F-54's fossil-field failure.

THE DERIVATION (settled here, once)
-----------------------------------

An open commitment's **last movement** is the newest state-change event
touching its id:

    commitment_updated / commitment_reclassified / commitment_reopened
    (lifecycle state changes) · outreach_sent (an outbound chase went out)
    · the CUSTOMER's own hand on the row (CUSTOMER_TOUCH_EVENT_TYPES — a
    snooze, an un-snooze, a tapped verb, putting it on the list) · the
    OTHER SIDE's own move on it (COUNTERPARTY_ACTIVITY_EVENT_TYPES — their
    reply, their part delivered). Our OWN unsent draft (`draft_created`)
    is NOT movement — M's ruling R-3, 2026-09-07 (CLEANUP1): the product
    noticing a row has gone quiet is not somebody touching it.

with the commitment's own capture ts as the floor — a commitment with no
movement events "last moved" when it was captured. Events reference
commitments through the same id chains the loader honors (data.commitment_id
/ data.target_id / top-level commitment_id, plus the F3 seq aliases
data.commitment_seq / data.source_event_seq / data.target_seq and every
legacy seq spelling) — append-only history stays readable forever.

Classification of an OPEN commitment (closed items are never classified —
callers pass load_open_commitments' projected set):

    blocked — the NEWEST movement is an outbound chase (`outreach_sent`)
              to a named person, with nothing after it. If the chase had
              been answered, the answer's state change (update / close /
              re-chase) would be newer — so newest-movement-is-a-chase IS
              the unanswered-outbound state. The person comes from the
              chase event's counterparty fields, falling back to the
              commitment's own; a chase with NO resolvable person does not
              block (you can't be blocked on nobody — it still counts as
              movement).
    stuck   — no movement for STUCK_DAYS (21) or more, OR blocked.
              blocked ⊆ stuck: a chased-and-unanswered item is stuck the
              moment the chase goes out, regardless of age — waiting on a
              named person is the state, not the duration.

THE F-54 CROSS-SURFACE-SPLIT RULE applies: every surface that renders a
stuck/blocked number gets it from `count_commitments(..., movement=...)`'s
headline export (which calls classify_commitments here), and every surface
that renders stuck/blocked ROWS calls classify_commitments directly with the
same movement map. No surface re-derives movement its own way — that is how
F-54's 21d-vs-37d split happened on threads.

Consumers (v4.6.0): morning brief (compute_brief_state via
compute_and_log_brief_state), the daily Commitments chat + commitment-triage
(count_commitments over the loader's open set; triage's 30d+ "still on your
plate?" section keys stale_tasks on the same movement map).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cru_match import (  # noqa: E402
    _commitment_field,
    _commitment_id,
    load_events_defensively,
)
from event_time import event_time  # noqa: E402

try:
    from event_types import LEGACY_SEQ_ID_RE as _LEGACY_SEQ_ID_RE
except Exception:  # pragma: no cover
    import re as _re
    _LEGACY_SEQ_ID_RE = _re.compile(r"^(?:commitment_seq_|event_|seq_)?0*(\d+)$")

# The one stuck threshold (days without movement). The commitments line's
# inline caption ("no movement in 21+ days, or blocked on a named person")
# quotes THIS constant's behavior — change them together.
STUCK_DAYS = 21

# THE CUSTOMER'S OWN HAND on a row (EXIT1 FIX ROUND 1, F-4). Saying "later",
# taking it back off later, tapping a verb on it in a chat, or putting it on
# the list to talk about is the customer TOUCHING the item. It is not the
# system filing a note (that is `_is_bookkeeping_update` below, and it stays
# excluded) — it is a person looking at the row and acting on it.
#
# WHY THIS IS HERE AND NOT IN ONE PREDICATE: the age-out receipt says
# "nobody has touched" these items. On M's own book four of the rows route 3
# would rest carry a snooze HE made 33 days ago, so for four of them that
# sentence was false. A receipt that says something untrue is a defect
# wherever the sentence is written, so the fix belongs in the ONE derivation
# every staleness surface passes, not in the surface that happened to print
# it. Narrowing "stuck" is the safe direction: nothing is hidden that was
# not hidden before — a touched row simply stops being called untouched.
CUSTOMER_TOUCH_EVENT_TYPES = frozenset({
    "chat_dismissal",          # "later" / snooze on any chat surface
    "chat_dismissal_cleared",  # ...and taking it back off later
    "chat_action",             # a verb tapped on the row in a chat
    "dont_forget_snooze",      # the same gesture on the don't-forget surface
    "commitment_to_discuss",   # put on the list to talk about
})

# THE OTHER SIDE'S OWN MOVE on a row (EXIT1 FIX ROUND 1, F-4; CLEANUP1's
# reviewer found the same hole from the other end). The baseline counted only
# OUR side — our chases, our edits, our re-dates — so a row the counterparty
# had answered read as untouched. These are the inbound facts the ledger
# actually records against a commitment id.
#
# STILL A LIMIT, STATED: a meeting with them and a calendar acceptance carry
# no commitment id, so they cannot be joined here without a second, person-
# shaped derivation — and a second derivation is how two surfaces start
# disagreeing about the same row. Route 1's calendar leg already CLOSES the
# rows a meeting with the other side proves, which is the stronger act.
COUNTERPARTY_ACTIVITY_EVENT_TYPES = frozenset({
    "email_received",              # their reply, recorded against the row
    "commitment_partial_received",  # they delivered their part of it
})

# What counts as movement. Lifecycle state changes + chase activity + the
# customer's own hand + the other side's own move.
# `commitment_resolved` is deliberately absent — a closed item leaves the
# open set entirely; classification only ever sees open commitments.
#
# M's ruling R-3 (2026-09-07, CLEANUP1 fix round 1) — OUR OWN UNSENT DRAFT IS
# NOT SOMEBODY TOUCHING THE ROW, so `draft_created` left this set.
#
# A draft is the product noticing a row has gone quiet and staging a nudge
# nobody has sent. Counting it as movement made the very act of noticing the
# silence reset the clock that measures the silence — the same inversion the
# watch mark and the park hint already have named exclusions for
# (`_BOOKKEEPING_MARKER_KEYS` / `_BOOKKEEPING_PRESENT_KEYS` below, and their
# reasoning is this reasoning). A row whose only movement in two months is a
# draft that never went out is exactly the dead backlog the staleness rules
# exist to find. Nothing is lost when the draft IS sent: the send writes its
# own `outreach_sent`, which is still movement and still the chase signal.
#
# Ruled once, for every staleness surface — the plate's stuck caption, the
# age-out, the review expiry, EXIT1's silence rule and CLEANUP1's one-shot —
# because a rule about what "quiet" means that differs by surface is two
# books. Restoring the old behaviour is one line: put the name back in the
# set below.
UNSENT_DRAFT_EVENT_TYPE = "draft_created"
MOVEMENT_EVENT_TYPES = frozenset({
    "commitment_updated",
    "commitment_reclassified",
    "commitment_reopened",
    "outreach_sent",
}) | CUSTOMER_TOUCH_EVENT_TYPES | COUNTERPARTY_ACTIVITY_EVENT_TYPES

# The blocked signal: an outbound actually went out. A staged draft
# (`draft_created`) is neither a chase nor movement (R-3 above) — nothing
# reached the person and nobody touched the row.
CHASE_EVENT_TYPES = frozenset({"outreach_sent"})


# WATCHGATE R-5 — the keys that make a `commitment_updated` a REAL change to
# the item: a new date, new wording, a re-owner, an adjudication. A watch
# mark carries none of them.
SUBSTANTIVE_UPDATE_KEYS = (
    "new_due", "due", "due_date", "new_title", "new_summary",
    "change_summary", "owner_confirmed", "review_flags_cleared",
    "review_flags_set", "new_owner_id", "new_counterparty_id",
)

# The pre-OVERDUE1 spelling, kept because this module's own call sites and its
# suites use it. ONE tuple, two names — a second literal list is how the two
# halves of "what counts as a real change" drift apart. The public name exists
# because `cru_match`'s read-side fold needs the SAME answer: an item the user
# actually re-dated or re-worded has been ANSWERED, so its overdue ask clears
# (SPEC OVERDUE1 DD-2), and a fold judging that from its own copy of the list
# would start disagreeing with the movement filter the first time either moved.
_SUBSTANTIVE_UPDATE_KEYS = SUBSTANTIVE_UPDATE_KEYS


# The bookkeeping markers: a `commitment_updated` whose ONLY payload is one of
# these is the system filing a note about an item, never the item moving.
#
# `watch_set` / `watch_cleared` — WATCHGATE R-5, the original pair.
# `asked_set` / `asked_cleared` — SPEC OVERDUE1 DD-1. The slipped block asking
#   an item's owner "done, new date, or drop?" is a QUESTION about an item
#   nobody has touched for days. If asking counted as movement, the very act of
#   noticing an item had gone quiet would reset the 21-day quiet clock, and the
#   item would read as freshly active to every staleness surface the morning
#   after the evening asked about it — the same inversion the watch mark had,
#   for the same reason.
# `status_hint` / `unparked` — POLICY1-B DD-6 (fix F-4). The park hint is
#   the product filing "this rests" on a row nobody touched; the un-park is
#   the product noticing movement that ALREADY happened. Neither is the
#   promise moving. Without this the quiet-lane park reset the very clock
#   that parked it and the plate read "no movement 47 days — updated today".
#   `status_hint` is a PRESENT-KEY test (the un-park writes null).
_BOOKKEEPING_MARKER_KEYS = ("watch_set", "watch_cleared",
                            "asked_set", "asked_cleared", "unparked")
_BOOKKEEPING_PRESENT_KEYS = ("status_hint",)


def _is_customer_unpark(ev: dict, d: dict) -> bool:
    """EXIT1 FIX ROUND 3 (review S-1) — True when a PERSON took an item off
    the resting list, rather than the product noticing movement.

    DD-6 above is right about the product's own hand: the rail parks a row
    nobody touched and un-parks it when something moved, and neither is the
    promise moving, so neither may reset the clock that measures the quiet.
    It was wrong about the CUSTOMER's hand. When somebody says `undo` on a
    rest, the un-park IS the touch — a person looked at the item and put it
    back on their working list. Counting that as bookkeeping left the row's
    last movement six weeks in the past, so the next morning's run rested it
    again, and two weeks after that let it go: an undo the machine re-did
    every day, which is not a reversal at all.

    Told apart by the ACTOR, on the shape every automatic rail already
    writes: an automatic un-park signs itself (`unparked_by` is the rail's
    own name, the same string as the event's `source_skill`, exactly the
    test `change_feed._machine_resolved` uses on a close), and the literal
    "system" is the other machine spelling. Anything else is a person id.
    """
    if not d.get("unparked"):
        return False                       # a PARK is the product's note
    by = str(d.get("unparked_by") or "").strip()
    if not by or by.lower() == "system":
        return False
    return by != str(ev.get("source_skill") or "").strip()


def _is_bookkeeping_update(ev: dict) -> bool:
    """True for a `commitment_updated` that only files a MARKER on an item —
    parking or un-parking a watch, or raising or clearing an overdue ask.

    Movement means the promise moved. Filing one of these is the system making
    a note about an item nobody has touched — and `commitment_updated` sits in
    MOVEMENT_EVENT_TYPES, so without this the act of noticing that an item has
    gone quiet would reset the 21-day clock that measures how long it has been
    quiet. A marked item would read as freshly active to every staleness
    surface, which is the precise opposite of what the mark means.

    Deliberately narrow: an update that ALSO carries a real change (a shifted
    date, a wording fix, an adjudication) is movement and stays movement, even
    if a marker rides along. Only the pure bookkeeping write is excluded.
    """
    if (ev.get("type") or ev.get("event")) != "commitment_updated":
        return False
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if _is_customer_unpark(ev, d):    # EXIT1 FIX ROUND 3 — their own hand
        return False
    if not (any(d.get(k) for k in _BOOKKEEPING_MARKER_KEYS)
            or any(k in d for k in _BOOKKEEPING_PRESENT_KEYS)):  # F-4: a park is a note, not movement
        return False
    return not any(d.get(k) not in (None, "", False)
                   for k in _SUBSTANTIVE_UPDATE_KEYS)


class CommitmentMovement(NamedTuple):
    ts: datetime            # UTC-aware (naive stamps taken as UTC — F-15 mix)
    event_type: str         # "commitment" when the floor (capture) is newest
    seq: Optional[int]      # movement event's seq, for traceability
    chase_to: Optional[str]  # the chase event's own named counterparty, if any


def _aware_utc(ts: datetime) -> datetime:
    """Order-safe UTC key across naive/aware stamps (same rule as
    thread_activity._ts_key — naive is taken as UTC)."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _parse_event_ts(ev: dict) -> Optional[datetime]:
    raw = event_time(ev)
    if not raw:
        return None
    try:
        return _aware_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def event_commitment_refs(ev: dict) -> tuple[list[str], list[int]]:
    """(ids, seqs) a movement event references — the same chains the loader
    and close_commitment honor: data.commitment_id / data.target_id /
    top-level commitment_id for ids; data.commitment_seq /
    data.source_event_seq / data.target_seq for the F3 seq aliases."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    ids: list[str] = []
    seen: set[str] = set()
    for v in (d.get("commitment_id"), d.get("target_id"), ev.get("commitment_id")):
        if isinstance(v, str) and v and v not in seen:
            ids.append(v)
            seen.add(v)
    seqs: list[int] = []
    for field in ("commitment_seq", "source_event_seq", "target_seq"):
        v = d.get(field)
        if isinstance(v, bool):
            continue
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and v not in seqs:
            seqs.append(v)
    return ids, seqs


def _chase_counterparty(ev: dict) -> Optional[str]:
    """The named person an outbound chase went to, from the chase event
    itself (Stage E receipt fields), else None."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for field in ("counterparty_name", "counterparty_id", "to", "recipient"):
        v = d.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# Per-fire memoization, same stat-signature pattern as
# cru_match._OPEN_COMMITMENTS_CACHE (v4.5.2 R1): any append changes a file's
# size → miss → fresh scan. Keyed on (path, movement set, since_ts).
_MOVEMENT_CACHE: dict[tuple, tuple] = {}


def derive_commitment_movement(
    events_jsonl_path: str | Path,
    *,
    movement_types: Optional[Iterable[str]] = None,
    since_ts: Optional[str] = None,
) -> dict[str, CommitmentMovement]:
    """{canonical commitment id: newest CommitmentMovement} derived from
    events at read time. THE movement baseline for stuck/blocked — every
    surface passes THIS map (via count_commitments' `movement=` or
    classify_commitments directly); none re-derives its own.

    The commitment's capture ts seeds the map (event_type "commitment") so
    every parseable commitment has a floor; a commitment whose capture ts is
    unparseable AND has no dated movement event is absent from the map —
    classify_commitments skips it (it can't be placed on a timeline, so
    calling it stuck would be a guess).

    `since_ts` passes through to load_events_defensively's shard pruning —
    the SAME safety contract applies (see that docstring): only pass it when
    the window provably covers the whole commitment history.
    """
    types = (frozenset(movement_types) if movement_types is not None
             else MOVEMENT_EVENT_TYPES)
    path = Path(events_jsonl_path)
    if not path.exists():
        return {}

    from cru_match import _events_files_sig
    cache_key = (str(path.resolve()), types, since_ts)
    sig = _events_files_sig(path)
    cached = _MOVEMENT_CACHE.get(cache_key)
    if cached is not None and cached[0] == sig:
        return dict(cached[1])

    events, _skipped = load_events_defensively(path, since_ts=since_ts)

    # Pass 1 — commitment index (seq → canonical id) + capture-ts floor +
    # the SUB1 child→parent chain (data.parent_id / data.parent_seq) + the
    # C4 merge re-point map (a non-split supersession transfers the closed
    # parent's children to the SURVIVOR read-side, D3b — bubbles must land
    # on the survivor's id, because every consumer of this map keys its
    # lookups by the PROJECTED id: classify_commitments over the loader's
    # top-level set and stale_tasks via the re-pointed data.parent_id).
    by_id: set[str] = set()
    seq_to_id: dict[int, str] = {}
    last: dict[str, CommitmentMovement] = {}
    child_parent: dict[str, str] = {}
    child_parent_seq: dict[str, int] = {}
    superseded_onto: dict[str, str] = {}
    for ev in events:
        et1 = ev.get("type") or ev.get("event")
        if et1 == "commitment_superseded":
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            # Same fold as the loader's merged_onto: split closers
            # (data.split_into present) are NOT merges and stay skipped.
            if not d.get("split_into"):
                old = (d.get("commitment_id") or d.get("thread_id")
                       or d.get("id") or d.get("target_id"))
                survivor = d.get("superseded_by") or d.get("survivor_id")
                if old and survivor:
                    superseded_onto[str(old)] = str(survivor)
            continue
        if et1 != "commitment":
            continue
        cid = _commitment_id(ev)
        by_id.add(cid)
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            seq_to_id[seq] = cid
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        pid = d.get("parent_id")
        if isinstance(pid, str) and pid:
            child_parent[cid] = pid
        else:
            pseq = d.get("parent_seq")
            if isinstance(pseq, int) and not isinstance(pseq, bool):
                child_parent_seq[cid] = pseq
        ts = _parse_event_ts(ev)
        if ts is not None:
            prior = last.get(cid)
            if prior is None or ts > prior.ts:
                last[cid] = CommitmentMovement(
                    ts=ts, event_type="commitment", seq=seq if isinstance(seq, int) else None,
                    chase_to=None)

    for ccid, pseq in child_parent_seq.items():
        if ccid not in child_parent and pseq in seq_to_id:
            child_parent[ccid] = seq_to_id[pseq]

    if superseded_onto:
        # Re-point each child's bubble target through the merge chain to its
        # live end (cycle-safe) — mirrors cru_match._eff_parent and the
        # writer's _resolve_survivor, so all three surfaces agree on WHICH
        # id owns a transferred child's activity.
        def _eff_parent(pid: str) -> str:
            seen: set = set()
            while pid in superseded_onto and pid not in seen:
                seen.add(pid)
                pid = superseded_onto[pid]
            return pid

        child_parent = {c: _eff_parent(p) for c, p in child_parent.items()}

    def _bubble(target_cid: str, record: CommitmentMovement) -> None:
        """SUB1 D5 — child activity IS parent movement: a parent whose steps
        are being checked off is never 'stuck' at 21 days (false-alarm
        class). The child keeps its own movement row too."""
        pcid = child_parent.get(target_cid)
        if pcid is None or pcid not in by_id:
            return
        prior = last.get(pcid)
        if prior is None or record.ts > prior.ts:
            last[pcid] = record

    # Bubble the child CAPTURE floor: adding sub-items is itself movement on
    # the parent (the user just planned the work). At this point last[child]
    # is exactly the capture record from pass 1.
    for ccid in child_parent:
        cm = last.get(ccid)
        if cm is not None:
            _bubble(ccid, cm)

    def _resolve(raw: str) -> Optional[str]:
        if raw in by_id:
            return raw
        m = _LEGACY_SEQ_ID_RE.match(raw)
        if m:
            return seq_to_id.get(int(m.group(1)))
        return None

    # Pass 2 — movement events, newest-ts-wins per commitment. SUB1: a
    # CHILD's movement — and its closure (checking a step off is progress on
    # the deliverable) — bubbles to the parent id. Closures are bubble-ONLY:
    # the child's own row is untouched (a closed child leaves the open set;
    # `commitment_resolved` stays out of MOVEMENT_EVENT_TYPES for the item
    # itself).
    _CHILD_CLOSURE_TYPES = frozenset(
        {"commitment_resolved", "thread_resolved", "commitment_superseded"})
    for ev in events:
        et = ev.get("type") or ev.get("event") or ""
        is_movement = et in types and not _is_bookkeeping_update(ev)
        is_closure = et in _CHILD_CLOSURE_TYPES
        if not (is_movement or is_closure):
            continue
        ts = _parse_event_ts(ev)
        if ts is None:
            continue
        ids, seqs = event_commitment_refs(ev)
        targets: set[str] = set()
        for raw in ids:
            r = _resolve(raw)
            if r:
                targets.add(r)
        for s in seqs:
            r = seq_to_id.get(s)
            if r:
                targets.add(r)
        if not targets:
            continue
        seq = ev.get("seq")
        record = CommitmentMovement(
            ts=ts,
            event_type=et,
            seq=seq if isinstance(seq, int) and not isinstance(seq, bool) else None,
            chase_to=_chase_counterparty(ev) if et in CHASE_EVENT_TYPES else None,
        )
        for cid in targets:
            if is_movement:
                prior = last.get(cid)
                if prior is None or record.ts > prior.ts:
                    last[cid] = record
            _bubble(cid, record)

    _MOVEMENT_CACHE[cache_key] = (sig, last)
    return dict(last)


def _commitment_counterparty(ev: dict) -> Optional[str]:
    """The commitment's own named counterparty (blocked-on fallback when the
    chase event carries no name of its own). MC1: the PRIMARY (first)
    counterparty — a blocked-on line names one person; the fan-out surfaces
    the rest."""
    from commitment_parties import (
        primary_counterparty_name as _p_name,
        primary_counterparty_id as _p_id,
    )
    return _p_name(ev) or _p_id(ev)


def classify_commitments(
    open_commitments: list[dict],
    movement: dict[str, CommitmentMovement],
    now_iso: str,
    *,
    stuck_days: int = STUCK_DAYS,
) -> dict:
    """Classify the OPEN set against a movement map (pure — no I/O).

    Returns {"stuck": [row...], "blocked": [row...]} where blocked ⊆ stuck.
    Row: {commitment_id, title, reason ("no_movement" | "blocked"),
          days_since_movement, last_movement_ts, last_movement_type,
          blocked_on (named person, blocked rows only)}.

    Rules (module docstring is the spec):
      blocked — newest movement is an outreach_sent to a named person.
      stuck   — days_since_movement >= stuck_days, OR blocked.
    A commitment absent from the movement map (unparseable capture ts, no
    dated movement) is skipped — never guessed stuck.
    """
    try:
        now = _aware_utc(datetime.fromisoformat(str(now_iso).replace("Z", "+00:00")))
    except ValueError:
        return {"stuck": [], "blocked": []}

    stuck: list[dict] = []
    blocked: list[dict] = []
    for ev in open_commitments:
        cid = _commitment_id(ev)
        m = movement.get(cid)
        if m is None:
            continue
        days = (now - m.ts).days
        blocked_on = None
        if m.event_type in CHASE_EVENT_TYPES:
            blocked_on = m.chase_to or _commitment_counterparty(ev)
        row = {
            "commitment_id": cid,
            "title": _commitment_field(ev, "title") or "",
            "days_since_movement": days,
            "last_movement_ts": m.ts.isoformat(),
            "last_movement_type": m.event_type,
        }
        if blocked_on:
            row["reason"] = "blocked"
            row["blocked_on"] = blocked_on
            blocked.append(row)
            stuck.append(row)
        elif days >= stuck_days:
            row["reason"] = "no_movement"
            stuck.append(row)
    return {"stuck": stuck, "blocked": blocked}


__all__ = [
    "derive_commitment_movement",
    "classify_commitments",
    "event_commitment_refs",
    "CommitmentMovement",
    "MOVEMENT_EVENT_TYPES",
    "CUSTOMER_TOUCH_EVENT_TYPES",
    "COUNTERPARTY_ACTIVITY_EVENT_TYPES",
    "UNSENT_DRAFT_EVENT_TYPE",
    "CHASE_EVENT_TYPES",
    "SUBSTANTIVE_UPDATE_KEYS",
    "STUCK_DAYS",
]
