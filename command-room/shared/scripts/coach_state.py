#!/usr/bin/env python3
"""
coach_state.py — deterministic state engine for the Business Coach Pack
(SPEC COACH1 §4.7).

Every count, window, tally, roster figure, and flag the coach-* skills surface
comes from THIS module — one tested function per derived number, nothing
re-derived in prose. Same discipline as `brief_state.py` (now
`commitment_state.py`) and the EOS module's `eos_state.py`: the prep pack, the
session record, the billing tally, the renewal watch, and the practice review
all read the SAME functions, so they can never disagree with each other.

THE DEFECT THIS MODULE IS SHAPED AROUND
---------------------------------------
Two cohorts with overlapping member sets got crossed in prep and needed manual
correction (SPEC COACH1 §4.2 — a real live bug, treated here as the acceptance
test). The root cause is an API shape, not a lookup miss: person → cohort is
ONE-TO-MANY, and any function that answers it with a single cohort must guess.
So:

  * `cohorts_for_person` returns a LIST, always. There is deliberately no
    singular `cohort_for_person` to reach for.
  * Every per-member read is scoped BY COHORT — `member_arc_slice` requires a
    thread id, so one member's items in cohort A can never bleed into cohort B.
  * A meeting resolves to a cohort by the COHORT's own evidence (roster overlap
    against the attendee set, plus the thread's name), never by picking one of
    a person's cohorts. When the evidence does not separate two cohorts the
    result says `ambiguous` and names both, rather than choosing.

Data contract:
  - Coaching engagements are threads with `kind: "coaching"`.
  - Cohorts are threads with `kind: "cohort"` carrying the `cohort` sub-object
    (roster, cadence, term, billing) — entities.schema.json $defs.project.cohort.
  - Terms live on `$defs.engagement.term_end` / `.renewal_date` (org-to-org
    engagements) and on `cohort.term_end` / `.renewal_date`.
  - Everything else is append-only events (the coach lane in
    shared/EVENT_TYPES.md). Sessions, arc items, billable units, patterns and
    invoices are events; none of them is ever stamped onto an entity.
  - Workspace config is `workspace.coach` — ABSENT on a non-coach workspace,
    and absence is the off switch.

All reads go through `cru_match.load_events_defensively` (malformed-line
tolerant, shard-transparent) and a non-mutating collection reader (nested- and
flat-shape tolerant). This module NEVER writes.

PRIVACY — WHICH SURFACE IS CALLING (PGUARD1/PGUARD2, SPEC COACH1 §12)
---------------------------------------------------------------------
The default read here is RAW, and that is correct for the coach's OWN surfaces
(prep, capture, the practice read). The coach is the workspace owner; the
session template deliberately splits work from personal, and per §12 that
SPLIT is a knob while the FIREWALL is not.

**A member-facing composer must not take the default.** Anything that produces
an artifact leaving the workspace — `coach-member-pack` above all (§9), and any
invoice or recap that reaches a member — passes org-scoped events into the
`events=` parameter every function here accepts:

    from events_io import load_events_org_scoped
    scoped, _ = load_events_org_scoped(workspace_root)
    slice_ = member_arc_slice(ws, person_id, thread_id, events=scoped)

That is the same PGUARD2 D2 injection seam the other external composers use;
it drops personal-lane rows and masked-account rows before they can reach a
member's document. `shared/scripts/coach_state.py` is on the D4c raw-read
allowlist in `tests/run_personal_firewall_test.py` for the owner half only —
the reason string there names this contract.

The §12 CROSS-CLIENT firewall is a separate rule and is not this module's job:
material surfaces as material, and one client's situation never appears in
another's session. Nothing here reads across engagements except the
practice-level rollups (`renewal_windows_open`, `underwater_engagements`),
which are the coach's own book and never member-facing.

stdlib only.
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

from cru_match import load_events_defensively, load_open_commitments  # noqa: E402
from entities_io import unwrap_entities  # noqa: E402
from event_seq import event_seq  # noqa: E402

try:  # SYNC1 B1 — route the _hq/data seam through the resolver (dormant today:
    # with no override it returns <root>/_hq/data, byte-identical to the constant).
    from data_root import resolve as _resolve_data_root
except ImportError:  # pragma: no cover — defensive; the default path still works
    _resolve_data_root = None


# --- vocabulary ------------------------------------------------------------

COACHING_KIND = "coaching"
COHORT_KIND = "cohort"

SESSION_EVENT_TYPES = frozenset({"session_captured", "cohort_session_captured"})

COACH_EVENT_TYPES = frozenset({
    "coaching_engagement_started", "engagement_baseline_set",
    "session_captured", "session_prep_generated", "arc_pattern_flagged",
    "cohort_session_captured", "cohort_member_added", "cohort_member_departed",
    "material_surfaced", "billable_session_logged",
    "invoice_drafted", "invoice_sent",
    "renewal_window_opened", "referral_moment_flagged",
    "member_artifact_delivered",
})

# Engagement kinds whose term dates a coaching practice actually watches. A
# `vendor` or `portfolio` edge has a term too, but it is not the coach's book.
RENEWAL_ENGAGEMENT_KINDS = frozenset({"client", "advisor", "partner_other"})

DEFAULT_RENEWAL_LEAD_DAYS = 60      # workspace.coach.renewal_lead_days default
PROMISED_TWICE_MIN = 2              # restatements before an item is "promised twice"
UNDERWATER_MIN_SESSIONS = 2         # below this, an economics read is noise

# Attendee-overlap floor for resolving a meeting to a cohort. Below this the
# resolver refuses rather than guessing — the §4.2 defect was a confident wrong
# answer, so an honest "unresolved" is the better failure.
#
# The SHARE floor is the load-bearing one: without it, a 1:1 between two people
# who both happen to sit in the same twelve-seat group would read as that
# group's session (2/12 = 0.17). Requiring half the live seats keeps a cohort
# match meaning "the group met". A light month that falls under the floor
# degrades to a normal call-prep brief, which is a wrong-cohort answer avoided,
# not a feature lost.
COHORT_MATCH_MIN_OVERLAP = 2
COHORT_MATCH_MIN_SHARE = 0.5


# --- plumbing --------------------------------------------------------------

def _data_dir(workspace_root) -> Path:
    if _resolve_data_root is not None:
        return _resolve_data_root(workspace_root)
    return Path(workspace_root) / "_hq" / "data"


def _events_path(workspace_root) -> Path:
    return _data_dir(workspace_root) / "events.jsonl"


def _entities_path(workspace_root) -> Path:
    return _data_dir(workspace_root) / "entities.json"


def load_entities(workspace_root) -> dict:
    """Parsed entities.json, or {} when missing/corrupt (callers degrade)."""
    try:
        data = json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_coach_events(workspace_root) -> tuple[list[dict], list[dict]]:
    """(all events, skipped lines) — full history, defensive, shard-aware."""
    return load_events_defensively(_events_path(workspace_root))


def _events(workspace_root, events: Optional[list[dict]]) -> list[dict]:
    if events is not None:
        return events
    loaded, _ = load_coach_events(workspace_root)
    return loaded


def _parse_date(value) -> Optional[_dt.date]:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _event_date(ev: dict) -> Optional[_dt.date]:
    """Event date honoring the read-side timestamp contract (ts → timestamp →
    date). Mirrors event_time.py's resolution order without taking the import,
    so this module stays loadable from a bare shared/scripts path."""
    for key in ("ts", "timestamp", "date"):
        d = _parse_date(ev.get(key))
        if d is not None:
            return d
    return None


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).strip()


def _tokens(text) -> set[str]:
    return {t for t in _norm(text).split() if len(t) > 2}


def _name_tokens(text) -> set[str]:
    """Tokens for cohort-NAME matching — short ones kept on purpose.

    Coaching groups are routinely named by a short code that is the entire
    difference between them ("Group B", "Cohort 3", a two-digit class number).
    The general `_tokens` floor of >2 characters throws exactly that
    discriminator away, which would leave two near-identically-named cohorts
    permanently tied.
    """
    return {t for t in _norm(text).split() if t}


def _collection(data: dict, name: str) -> list:
    """Read a collection WITHOUT creating it.

    Deliberately not `entities_io.entities_collection`: that helper creates the
    list in place so a WRITER's append lands where readers look. This module
    never writes, and a caller who passes its own `entities` dict in and later
    persists it must not get spurious empty `threads` / `engagements` keys back
    from a read. Same nested-wrapper tolerance, no mutation.
    """
    container = unwrap_entities(data if isinstance(data, dict) else {})
    coll = container.get(name)
    return coll if isinstance(coll, list) else []


def _threads(workspace_root, entities: Optional[dict] = None) -> list[dict]:
    """Live thread collection under either the nested or flat shape. Mirrors
    thread_writer._threads: real data stores under `threads`, the legacy schema
    names it `projects`; prefer whichever already has rows."""
    data = entities if entities is not None else load_entities(workspace_root)
    threads = _collection(data, "threads")
    projects = _collection(data, "projects")
    if projects and not threads:
        return projects
    return threads


def _thread_name(thread: dict) -> str:
    return str(thread.get("canonical_name") or thread.get("display_name")
               or thread.get("folder_name") or thread.get("id") or "")


def _is_open(thread: dict) -> bool:
    return str(thread.get("status") or "active").lower() not in {
        "archived", "resolved", "closed"
    }


# --- config ----------------------------------------------------------------

def get_coach_config(workspace_root, entities: Optional[dict] = None) -> dict:
    """`workspace.coach`, or {} when the pack isn't enabled here.

    {} is the off switch AND the honest answer on every non-coach workspace —
    callers must treat it as "this is not a coaching practice", never as
    "defaults apply".
    """
    data = entities if entities is not None else load_entities(workspace_root)
    ws = data.get("workspace")
    coach = (ws or {}).get("coach") if isinstance(ws, dict) else None
    return coach if isinstance(coach, dict) else {}


def coach_enabled(workspace_root, entities: Optional[dict] = None) -> bool:
    return bool(get_coach_config(workspace_root, entities).get("enabled"))


def renewal_lead_days(workspace_root, entities: Optional[dict] = None) -> int:
    raw = get_coach_config(workspace_root, entities).get("renewal_lead_days")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return DEFAULT_RENEWAL_LEAD_DAYS
    return raw


# --- threads ---------------------------------------------------------------

def coaching_threads(workspace_root, entities: Optional[dict] = None,
                     include_closed: bool = False) -> list[dict]:
    """Every kind='coaching' thread (1:1 engagements)."""
    out = [t for t in _threads(workspace_root, entities)
           if isinstance(t, dict) and t.get("kind") == COACHING_KIND]
    return out if include_closed else [t for t in out if _is_open(t)]


def cohort_threads(workspace_root, entities: Optional[dict] = None,
                   include_closed: bool = False) -> list[dict]:
    """Every kind='cohort' thread (group engagements)."""
    out = [t for t in _threads(workspace_root, entities)
           if isinstance(t, dict) and t.get("kind") == COHORT_KIND]
    return out if include_closed else [t for t in out if _is_open(t)]


def _thread_by_id(workspace_root, thread_id: str,
                  entities: Optional[dict] = None) -> Optional[dict]:
    for t in _threads(workspace_root, entities):
        if isinstance(t, dict) and t.get("id") == thread_id:
            return t
    return None


# --- roster ----------------------------------------------------------------

def cohort_roster(workspace_root, cohort_thread_id: str,
                  entities: Optional[dict] = None,
                  as_of: Optional[_dt.date] = None,
                  include_departed: bool = False) -> dict:
    """The one roster reader. Keyed on the COHORT, never on a person — that
    direction is one-to-many and picking from it is the §4.2 crossing defect.

    Returns {
      "cohort_thread_id", "name", "cadence",
      "seats_contracted": int|None,   what the coach SOLD (cohort.seat_count)
      "seats_filled": int,            active + paused seats right now
      "seat_gap": int|None,           contracted - filled, or None when unstated
      "members": [{person_id, status, joined_at, departed_at, also_1to1,
                   billing_target, tenure_days}],
      "member_ids": [person_id, ...]  active + paused only
      "term_end", "renewal_date", "billing",
      "missing": bool                 the thread id resolved to nothing
    }

    seats_contracted and seats_filled are reported SIDE BY SIDE and never
    reconciled into each other: a departed seat that is still sold is a real
    state, and collapsing the two would hide it.
    """
    as_of = as_of or _dt.date.today()
    thread = _thread_by_id(workspace_root, cohort_thread_id, entities)
    if thread is None:
        return {"cohort_thread_id": cohort_thread_id, "name": None,
                "cadence": None, "seats_contracted": None, "seats_filled": 0,
                "seat_gap": None, "members": [], "member_ids": [],
                "term_end": None, "renewal_date": None, "billing": None,
                "missing": True}

    coh = thread.get("cohort")
    coh = coh if isinstance(coh, dict) else {}
    rows = []
    for m in (coh.get("members") or []):
        if not isinstance(m, dict) or not m.get("person_id"):
            continue
        status = str(m.get("status") or "active")
        if status == "departed" and not include_departed:
            continue
        joined = _parse_date(m.get("joined_at"))
        rows.append({
            "person_id": m["person_id"],
            "status": status,
            "joined_at": m.get("joined_at"),
            "departed_at": m.get("departed_at"),
            "also_1to1": bool(m.get("also_1to1")),
            "billing_target": m.get("billing_target"),
            "tenure_days": (as_of - joined).days if joined else None,
        })

    filled = sum(1 for r in rows if r["status"] in {"active", "paused"})
    contracted = coh.get("seat_count")
    if isinstance(contracted, bool) or not isinstance(contracted, int):
        contracted = None
    return {
        "cohort_thread_id": cohort_thread_id,
        "name": _thread_name(thread),
        "cadence": coh.get("cadence"),
        "seats_contracted": contracted,
        "seats_filled": filled,
        "seat_gap": (contracted - filled) if contracted is not None else None,
        "members": rows,
        "member_ids": [r["person_id"] for r in rows
                       if r["status"] in {"active", "paused"}],
        "term_end": coh.get("term_end"),
        "renewal_date": coh.get("renewal_date"),
        "billing": coh.get("billing"),
        "missing": False,
    }


def cohorts_for_person(workspace_root, person_id: str,
                       entities: Optional[dict] = None,
                       as_of: Optional[_dt.date] = None,
                       include_departed: bool = False) -> list[dict]:
    """EVERY cohort this person sits in — a LIST, always, even when it has one
    entry. There is deliberately no singular counterpart: a person genuinely
    can sit in two cohorts, and a function that returns one of them has to
    guess. Guessing here is exactly the §4.2 defect.

    Each entry is the person's row from that cohort's roster plus the cohort's
    id and name, so a caller that has to disambiguate has the evidence to do it
    (or to ask) without a second read.
    """
    as_of = as_of or _dt.date.today()
    out = []
    for thread in cohort_threads(workspace_root, entities, include_closed=True):
        roster = cohort_roster(workspace_root, thread.get("id"),
                               entities=entities, as_of=as_of,
                               include_departed=include_departed)
        for row in roster["members"]:
            if row["person_id"] == person_id:
                out.append({
                    "cohort_thread_id": roster["cohort_thread_id"],
                    "name": roster["name"],
                    "cadence": roster["cadence"],
                    "status": thread.get("status"),
                    "member": row,
                })
                break
    out.sort(key=lambda c: (c["name"] or "", c["cohort_thread_id"] or ""))
    return out


def coaching_threads_for_person(workspace_root, person_id: str,
                                entities: Optional[dict] = None) -> list[dict]:
    """Every kind='coaching' thread this person is the client on. A list for
    the same reason as cohorts_for_person — a coach can run more than one
    engagement with the same person (a second company, a second mandate)."""
    out = []
    for t in coaching_threads(workspace_root, entities, include_closed=True):
        if t.get("owner_person_id") == person_id or person_id in (
                t.get("stakeholder_person_ids") or []):
            out.append(t)
    return out


def member_is_also_1to1(workspace_root, person_id: str,
                        entities: Optional[dict] = None) -> bool:
    """Does this cohort member ALSO have a 1:1 coaching engagement?

    Derived from the live threads, not read off `also_1to1` — a stored
    duplicate of a derivable fact drifts (the `last_activity` lesson). The
    roster flag is only the fallback for a workspace where the coaching thread
    hasn't been created yet.
    """
    if any(_is_open(t) for t in coaching_threads_for_person(
            workspace_root, person_id, entities)):
        return True
    for c in cohorts_for_person(workspace_root, person_id, entities=entities):
        if c["member"].get("also_1to1"):
            return True
    return False


# --- meeting → cohort / coaching resolution (the §4.6 handoff answer) -------

def cohort_for_meeting(workspace_root, attendee_person_ids: Iterable[str] = (),
                       title: str = "",
                       entities: Optional[dict] = None,
                       as_of: Optional[_dt.date] = None) -> dict:
    """Which cohort is this meeting? Resolved from the COHORT's own evidence.

    Scoring, in order:
      1. roster overlap — how many of the meeting's attendees hold a live seat,
         and what share of that cohort's filled seats they are;
      2. DISTINCTIVE name overlap, used ONLY to separate two cohorts that tie
         on roster overlap. Tokens shared by more than one cohort's name are
         discarded before scoring: with groups named alike, "group" carries no
         information and the code that follows it carries all of it. Roster
         evidence is never overridden by a name — the name only breaks a tie.

    Returns {"cohort_thread_id", "name", "confidence", "candidates": [...],
             "ambiguous": bool, "reason": str}. `cohort_thread_id` is None when
    nothing clears the floor OR when two candidates tie — an honest unresolved
    beats a confident wrong answer, which is what the §4.2 defect actually was.
    """
    as_of = as_of or _dt.date.today()
    attendees = {a for a in attendee_person_ids if a}
    title_tokens = _name_tokens(title)

    rosters = []
    for thread in cohort_threads(workspace_root, entities):
        roster = cohort_roster(workspace_root, thread.get("id"),
                               entities=entities, as_of=as_of)
        if roster["member_ids"]:
            rosters.append(roster)

    # Tokens appearing in more than one cohort name carry no signal.
    token_counts: dict[str, int] = {}
    for roster in rosters:
        for tok in _name_tokens(roster["name"]):
            token_counts[tok] = token_counts.get(tok, 0) + 1

    scored = []
    for roster in rosters:
        seats = set(roster["member_ids"])
        overlap = len(attendees & seats)
        distinctive = {t for t in _name_tokens(roster["name"])
                       if token_counts.get(t, 0) == 1}
        scored.append({
            "cohort_thread_id": roster["cohort_thread_id"],
            "name": roster["name"],
            "overlap": overlap,
            "seats": len(seats),
            "share": round(overlap / len(seats), 3),
            "name_hits": len(title_tokens & distinctive),
        })

    eligible = [
        c for c in scored
        if c["overlap"] >= min(COHORT_MATCH_MIN_OVERLAP, c["seats"])
        and c["share"] >= COHORT_MATCH_MIN_SHARE
    ]
    if not eligible:
        return {"cohort_thread_id": None, "name": None, "confidence": 0.0,
                "candidates": scored, "ambiguous": False,
                "reason": "no cohort's live roster covers this attendee set"}

    eligible.sort(key=lambda c: (c["overlap"], c["share"], c["name_hits"]),
                  reverse=True)
    best = eligible[0]
    rivals = [
        c for c in eligible[1:]
        if (c["overlap"], c["share"], c["name_hits"])
        == (best["overlap"], best["share"], best["name_hits"])
    ]
    if rivals:
        return {"cohort_thread_id": None, "name": None, "confidence": 0.0,
                "candidates": [best] + rivals, "ambiguous": True,
                "reason": ("two cohorts match this attendee set equally well — "
                           "ask which one rather than guessing")}
    return {"cohort_thread_id": best["cohort_thread_id"], "name": best["name"],
            "confidence": round(best["share"], 3), "candidates": eligible,
            "ambiguous": False,
            "reason": (f"{best['overlap']} of {best['seats']} live seats are on "
                       f"this meeting")}


def coaching_handoff_for_meeting(workspace_root,
                                 attendee_person_ids: Iterable[str] = (),
                                 title: str = "",
                                 entities: Optional[dict] = None,
                                 as_of: Optional[_dt.date] = None) -> dict:
    """SPEC COACH1 §4.6 — the one question `call-prep` asks before it builds.

    Returns {"defer": bool, "kind": "coaching"|"cohort"|None, "thread_id",
             "name", "reason"}. `defer: True` means the meeting's counterpart
    resolved to a coaching engagement or a cohort, and call-prep should hand
    off to the pack's prep skill and stop.

    A declared handoff, NOT a trigger fight: call-prep keeps its whole trigger
    family, and this is a deterministic substrate question answered in code —
    not a routing judgment made in prose.

    Refuses to defer when the pack is not enabled, and refuses on an ambiguous
    cohort match (call-prep proceeds normally and the coach can name the
    cohort) — deferring into an ambiguous prep is how the two cohorts got
    crossed in the first place.
    """
    if not coach_enabled(workspace_root, entities):
        return {"defer": False, "kind": None, "thread_id": None, "name": None,
                "reason": "coach pack not enabled on this workspace"}

    attendees = [a for a in attendee_person_ids if a]

    match = cohort_for_meeting(workspace_root, attendees, title,
                               entities=entities, as_of=as_of)
    if match["cohort_thread_id"]:
        return {"defer": True, "kind": COHORT_KIND,
                "thread_id": match["cohort_thread_id"], "name": match["name"],
                "reason": match["reason"]}
    if match["ambiguous"]:
        return {"defer": False, "kind": None, "thread_id": None, "name": None,
                "reason": match["reason"]}

    # 1:1 — the meeting's counterpart is a coaching client. Deduped BY THREAD,
    # not by attendee: a client who brings their co-founder puts two people on
    # one coaching thread, and counting them twice would refuse a handoff that
    # is completely unambiguous.
    hits: dict[str, dict] = {}
    for pid in attendees:
        for t in coaching_threads_for_person(workspace_root, pid, entities):
            if _is_open(t):
                hits[t.get("id")] = t
    if len(hits) == 1:
        thread = next(iter(hits.values()))
        return {"defer": True, "kind": COACHING_KIND, "thread_id": thread.get("id"),
                "name": _thread_name(thread),
                "reason": "the meeting's counterpart is a coaching client"}
    if len(hits) > 1:
        return {"defer": False, "kind": None, "thread_id": None, "name": None,
                "reason": ("more than one coaching engagement is on this "
                           "meeting — ask which one rather than guessing")}
    return {"defer": False, "kind": None, "thread_id": None, "name": None,
            "reason": "no coaching engagement or cohort on this meeting"}


# --- sessions --------------------------------------------------------------

def sessions_since(workspace_root, thread_id: str,
                   since: Optional[_dt.date] = None,
                   as_of: Optional[_dt.date] = None,
                   events: Optional[list[dict]] = None) -> list[dict]:
    """Captured sessions on one engagement, oldest → newest.

    `since` is inclusive and optional (None = the whole arc). Scoped to ONE
    thread: a cohort's sessions never appear on another cohort's list, which
    is the same scoping rule that keeps two overlapping rosters apart.
    """
    as_of = as_of or _dt.date.today()
    rows = []
    for ev in _events(workspace_root, events):
        if ev.get("type") not in SESSION_EVENT_TYPES:
            continue
        if not _touches_thread(ev, thread_id):
            continue
        d = _event_date(ev)
        if d is None or d > as_of or (since is not None and d < since):
            continue
        data = ev.get("data") or {}
        rows.append({
            # UNDOGUARD sibling rail: normalized at projection, so the sort
            # tiebreak below can never compare str to int.
            "seq": event_seq(ev),
            "date": d.isoformat(),
            "type": ev.get("type"),
            "thread_id": thread_id,
            "minutes": data.get("duration_minutes"),
            "summary": str(data.get("summary") or data.get("title") or "")[:200],
            "person_ids": list(ev.get("person_ids") or []),
        })
    # `or 0` was safe-looking and wrong: it only reached the seq element on a
    # same-DATE tie, so a string seq crashed the sort intermittently. Zero is
    # a fine TIEBREAK default (unlike a window bound, where it silently
    # excluded every seq-less row — see event_seq's module docstring).
    rows.sort(key=lambda r: (r["date"], r["seq"] if r["seq"] is not None else 0))
    return rows


def _touches_thread(ev: dict, thread_id: str) -> bool:
    if not thread_id:
        return False
    if ev.get("primary_thread_id") == thread_id:
        return True
    if thread_id in (ev.get("related_thread_ids") or []):
        return True
    # Legacy flat shape — readers fall back to project_id per the schema note.
    return ev.get("project_id") == thread_id


def last_session(workspace_root, thread_id: str,
                 as_of: Optional[_dt.date] = None,
                 events: Optional[list[dict]] = None) -> Optional[dict]:
    rows = sessions_since(workspace_root, thread_id, as_of=as_of, events=events)
    return rows[-1] if rows else None


# --- the arc ---------------------------------------------------------------

def load_open_arc_items(workspace_root, thread_id: str,
                        as_of: Optional[_dt.date] = None,
                        events: Optional[list[dict]] = None) -> list[dict]:
    """The engagement's open commitments — the arc the coach reads back.

    Goes through the canonical open-commitments projector
    (`cru_match.load_open_commitments`), never a hand-rolled scan: closure
    state, reopens, reassignments and supersession all already live there, and
    a second scan would drift from them the first time one of those lands.

    Ordered oldest first — an item that has been open longest is the one the
    follow-up section leads with.

    An injected `events` list is forwarded to the projector's own `events=`
    seam rather than being ignored: a caller that injects must get ONE
    consistent view, not a mix of injected sessions and on-disk commitments.
    """
    as_of = as_of or _dt.date.today()
    out = []
    try:
        if events is not None:
            open_items = load_open_commitments(_events_path(workspace_root),
                                               events=events)
        else:
            open_items = load_open_commitments(_events_path(workspace_root))
    except Exception:
        return out
    for c in open_items:
        if not _touches_thread(c, thread_id):
            continue
        d = _event_date(c)
        data = c.get("data") or {}
        out.append({
            "seq": event_seq(c),  # UNDOGUARD sibling rail — see the sort below
            "id": data.get("id"),
            "title": str(data.get("title") or data.get("text")
                         or data.get("summary") or "(unlabeled)")[:200],
            "kind": data.get("kind"),
            "opened": d.isoformat() if d else None,
            "age_days": (as_of - d).days if d else None,
            "due": data.get("due") or data.get("due_date"),
            "person_ids": list(c.get("person_ids") or []),
        })
    # UNDOGUARD sibling rail — same shape as the session sort: the seq element
    # is only reached when two commitments were opened the same day.
    out.sort(key=lambda i: (i["opened"] is None, i["opened"] or "",
                            i["seq"] if i["seq"] is not None else 0))
    return out


def commitments_promised_twice(workspace_root, thread_id: str,
                               as_of: Optional[_dt.date] = None,
                               events: Optional[list[dict]] = None,
                               min_restatements: int = PROMISED_TWICE_MIN) -> list[dict]:
    """Open items the client has committed to in `min_restatements` or more
    SEPARATE sessions without closing them.

    This is the arc read §5.2 exists for: the client didn't mention it again,
    so the transcript can't produce it, but the coach still needs it in front
    of them. Matching is on normalized title within one thread — deliberately
    conservative: two genuinely different items that happen to normalize alike
    is a far cheaper error than a missed repeat, and the caller shows the
    evidence dates so the coach can see the grouping.

    Distinct SESSION DATES are what count, not raw restatement events: two
    captures of the same session must never look like two promises.
    """
    as_of = as_of or _dt.date.today()
    evs = _events(workspace_root, events)

    # Every commitment-family statement on this thread, grouped by normalized
    # title, collecting the distinct dates it was stated on.
    stated: dict[str, dict] = {}
    for ev in evs:
        etype = ev.get("type")
        if etype not in {"commitment", "commitment_updated", "commitment_observed"}:
            continue
        if not _touches_thread(ev, thread_id):
            continue
        d = _event_date(ev)
        if d is None or d > as_of:
            continue
        data = ev.get("data") or {}
        title = str(data.get("title") or data.get("text")
                    or data.get("summary") or "").strip()
        key = _norm(title)
        if not key:
            continue
        row = stated.setdefault(key, {"title": title, "dates": set(),
                                      "seqs": [], "ids": set()})
        row["dates"].add(d)
        row["seqs"].append(ev.get("seq"))
        if data.get("id"):
            row["ids"].add(data["id"])

    open_keys = {_norm(i["title"]): i
                 for i in load_open_arc_items(workspace_root, thread_id,
                                              as_of=as_of,
                                              events=evs if events is not None
                                              else None)}

    out = []
    for key, row in stated.items():
        if key not in open_keys:
            continue                      # closed items are not still promised
        if len(row["dates"]) < min_restatements:
            continue
        dates = sorted(row["dates"])
        out.append({
            "title": row["title"],
            "times_promised": len(dates),
            "first_promised": dates[0].isoformat(),
            "last_promised": dates[-1].isoformat(),
            "days_carried": (dates[-1] - dates[0]).days,
            "evidence_seqs": [s for s in row["seqs"] if s is not None],
            "commitment_ids": sorted(row["ids"]),
        })
    out.sort(key=lambda r: (-r["times_promised"], -r["days_carried"]))
    return out


def member_arc_slice(workspace_root, person_id: str, thread_id: str,
                     window_days: int = 180,
                     as_of: Optional[_dt.date] = None,
                     events: Optional[list[dict]] = None) -> dict:
    """One member's arc INSIDE one engagement.

    `thread_id` is REQUIRED and is the whole point: scoping by (person, thread)
    rather than by person alone is what stops a member who sits in two cohorts
    from having the two arcs merged. That merge is the §4.2 defect.

    Returns {"person_id", "thread_id", "window_days", "sessions_attended",
             "open_items", "promised_twice", "last_session", "touched_events"}.
    """
    as_of = as_of or _dt.date.today()
    floor = as_of - _dt.timedelta(days=window_days)
    evs = _events(workspace_root, events)

    sessions = [s for s in sessions_since(workspace_root, thread_id, since=floor,
                                          as_of=as_of, events=evs)
                if not s["person_ids"] or person_id in s["person_ids"]]

    # Forward the CALLER's events argument (not the resolved list) so the
    # default path keeps the projector's memoization and the injected path
    # stays one consistent view.
    open_items = [i for i in load_open_arc_items(workspace_root, thread_id,
                                                 as_of=as_of, events=events)
                  if person_id in i["person_ids"]]

    twice = [p for p in commitments_promised_twice(workspace_root, thread_id,
                                                   as_of=as_of, events=events)
             if any(p["title"] == i["title"] for i in open_items)]

    touched = []
    for ev in evs:
        if ev.get("type") not in COACH_EVENT_TYPES:
            continue
        if not _touches_thread(ev, thread_id):
            continue
        if person_id not in (ev.get("person_ids") or []):
            continue
        d = _event_date(ev)
        if d is None or d < floor or d > as_of:
            continue
        data = ev.get("data") or {}
        # One key per line: the event-contract guard reads a line carrying a
        # "type" literal as type-comparison context and scores every other
        # quoted token on it for near-miss typos ("date" lands 2 edits from
        # "note"). Keeping them apart avoids blinding that guard with an
        # allowlist entry for what is only a formatting artifact.
        touched.append({
            "type": ev.get("type"),
            "date": d.isoformat(),
            "label": str(data.get("summary") or data.get("title")
                         or ev.get("type"))[:140],
        })
    touched.sort(key=lambda t: t["date"])

    return {
        "person_id": person_id,
        "thread_id": thread_id,
        "window_days": window_days,
        "sessions_attended": len(sessions),
        "open_items": open_items,
        "promised_twice": twice,
        "last_session": sessions[-1] if sessions else None,
        "touched_events": touched,
    }


# --- billing ---------------------------------------------------------------

def _seat_payer(roster_row: dict, cohort_billing) -> Optional[dict]:
    """A seat's payer: its own billing_target, else the cohort's payer."""
    target = roster_row.get("billing_target")
    if isinstance(target, dict) and target.get("id"):
        return {"kind": target.get("kind"), "id": target.get("id")}
    if isinstance(cohort_billing, dict):
        payer = cohort_billing.get("payer")
        if isinstance(payer, dict) and payer.get("id"):
            return {"kind": payer.get("kind"), "id": payer.get("id")}
    return None


def billable_units_for_period(workspace_root, period_start, period_end,
                              entities: Optional[dict] = None,
                              events: Optional[list[dict]] = None) -> dict:
    """CONFIRMED billable units in a period, rolled up by payer.

    Reads `billable_session_logged` ONLY. A captured session is evidence that a
    unit probably exists; it is not a unit. The confirmation pass (§8.2)
    surfaces candidates and the coach confirms — never a silent write, so never
    a silent invoice line.

    Returns {"period_start", "period_end", "lines": [...], "by_payer": {...},
             "unpayered": [...], "total_units", "total_amount"}.
    `by_payer` is keyed "person:<id>" / "org:<id>" and is the §8.1 roll-up: one
    entry per payer, itemized per person and per cohort seat. Lines whose payer
    could not be resolved land in `unpayered` rather than being folded into
    someone else's invoice.
    """
    start, end = _parse_date(period_start), _parse_date(period_end)
    if start is None or end is None:
        raise ValueError("billable_units_for_period needs two ISO dates "
                         f"(got {period_start!r}, {period_end!r})")
    if start > end:
        raise ValueError(f"period_start {start} is after period_end {end}")

    ents = entities if entities is not None else load_entities(workspace_root)

    # Seat-level payer overrides, per (cohort thread, person).
    seat_payers: dict[tuple[str, str], Optional[dict]] = {}
    for thread in cohort_threads(workspace_root, ents, include_closed=True):
        roster = cohort_roster(workspace_root, thread.get("id"), entities=ents,
                               as_of=end, include_departed=True)
        for row in roster["members"]:
            seat_payers[(roster["cohort_thread_id"], row["person_id"])] = \
                _seat_payer(row, roster["billing"])

    lines, unpayered = [], []
    for ev in _events(workspace_root, events):
        if ev.get("type") != "billable_session_logged":
            continue
        d = _event_date(ev)
        if d is None or d < start or d > end:
            continue
        data = ev.get("data") or {}
        thread_id = ev.get("primary_thread_id") or data.get("thread_id")
        person_id = data.get("person_id") or next(
            iter(ev.get("person_ids") or []), None)

        units = data.get("units")
        if isinstance(units, bool) or not isinstance(units, (int, float)):
            units = 1
        rate = data.get("rate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            rate = None

        payer = data.get("payer") if isinstance(data.get("payer"), dict) else None
        if payer is None:
            payer = seat_payers.get((thread_id, person_id))

        line = {
            "seq": ev.get("seq"),
            "date": d.isoformat(),
            "thread_id": thread_id,
            "person_id": person_id,
            "units": units,
            "unit": data.get("unit"),
            "rate": rate,
            "amount": (units * rate) if rate is not None else None,
            "currency": data.get("currency") or "USD",
            "payer": payer,
            "label": str(data.get("summary") or data.get("title") or "")[:160],
        }
        if payer is None:
            unpayered.append(line)
        else:
            lines.append(line)

    by_payer: dict[str, dict] = {}
    for line in lines:
        key = f"{line['payer']['kind']}:{line['payer']['id']}"
        bucket = by_payer.setdefault(key, {
            "payer": line["payer"], "lines": [], "units": 0, "amount": 0,
            "amount_complete": True, "currency": line["currency"],
        })
        bucket["lines"].append(line)
        bucket["units"] += line["units"]
        if line["amount"] is None:
            # One rate-less line makes the payer total a partial number. Say so
            # rather than quietly under-billing (quantify.py discipline).
            bucket["amount_complete"] = False
        else:
            bucket["amount"] += line["amount"]
    for bucket in by_payer.values():
        if not bucket["amount_complete"]:
            bucket["amount"] = None

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "lines": lines,
        "by_payer": by_payer,
        "unpayered": unpayered,
        "total_units": sum(line["units"] for line in lines),
        # 0 when there is nothing to bill (a definite answer); None only when
        # SOME line has no rate — an unknown total must never render as a
        # smaller-but-confident number.
        "total_amount": (
            sum(line["amount"] for line in lines)
            if all(line["amount"] is not None for line in lines)
            else None
        ),
    }


# --- renewal ---------------------------------------------------------------

def renewal_windows_open(workspace_root, as_of: Optional[_dt.date] = None,
                         entities: Optional[dict] = None,
                         lead_days: Optional[int] = None) -> list[dict]:
    """Engagements whose renewal conversation is due, off REAL term dates.

    Two sources, because a coaching practice has two shapes of book:
      * `entities.engagements[]` — `term_end` / `renewal_date` on the org-to-org
        edge (SPEC COACH1 §4.3), filtered to the kinds a practice watches;
      * `cohort` threads — `cohort.term_end` / `cohort.renewal_date`.

    An engagement with no `term_end` AND no `renewal_date` is skipped entirely
    and never inferred from silence — renewal watching being OFF is said plainly
    at setup rather than faked with a dormancy heuristic.

    Sorted soonest-first. Past-due windows are INCLUDED with a negative
    `days_until` — a term that already lapsed is more urgent, not less.
    """
    as_of = as_of or _dt.date.today()
    ents = entities if entities is not None else load_entities(workspace_root)
    lead = lead_days if lead_days is not None else renewal_lead_days(
        workspace_root, ents)

    out = []

    def consider(source, ref_id, name, term_end, renewal_date, extra=None):
        term = _parse_date(term_end)
        due = _parse_date(renewal_date)
        if due is None and term is None:
            return
        if due is None:
            due = term - _dt.timedelta(days=lead)
        if due > as_of:
            return
        row = {
            "source": source,
            "id": ref_id,
            "name": name,
            "term_end": term.isoformat() if term else None,
            "renewal_due": due.isoformat(),
            "days_until_term": (term - as_of).days if term else None,
            "days_overdue": (as_of - due).days,
            "explicit_renewal_date": renewal_date is not None,
        }
        row.update(extra or {})
        out.append(row)

    for eng in _collection(ents, "engagements"):
        if not isinstance(eng, dict):
            continue
        if eng.get("is_active") is False or eng.get("ended_at"):
            continue
        if eng.get("kind") not in RENEWAL_ENGAGEMENT_KINDS:
            continue
        consider("engagement", eng.get("id"),
                 eng.get("label") or eng.get("to_org_id"),
                 eng.get("term_end"), eng.get("renewal_date"),
                 {"to_org_id": eng.get("to_org_id")})

    for thread in cohort_threads(workspace_root, ents):
        coh = thread.get("cohort")
        coh = coh if isinstance(coh, dict) else {}
        roster = cohort_roster(workspace_root, thread.get("id"), entities=ents,
                               as_of=as_of)
        consider("cohort", thread.get("id"), _thread_name(thread),
                 coh.get("term_end"), coh.get("renewal_date"),
                 {"seats_filled": roster["seats_filled"],
                  "seats_contracted": roster["seats_contracted"]})

    out.sort(key=lambda r: (r["renewal_due"], r["name"] or ""))
    return out


# --- practice economics ----------------------------------------------------

def underwater_engagements(workspace_root, period_start, period_end,
                           entities: Optional[dict] = None,
                           events: Optional[list[dict]] = None) -> list[dict]:
    """Engagements delivering materially more sessions than they bill for.

    Compares CAPTURED sessions on a thread against CONFIRMED billable units on
    the same thread in the same period. Both numbers come from the functions
    above, so the practice review can never disagree with the billing tally.

    An engagement with fewer than UNDERWATER_MIN_SESSIONS captured sessions is
    skipped: two data points is not an economics read, and flagging one is how
    a report loses the coach's trust. An engagement with NO billing data at all
    is reported with `billing_known: False` rather than as underwater — no
    rate is not the same as a bad rate.
    """
    start, end = _parse_date(period_start), _parse_date(period_end)
    if start is None or end is None:
        raise ValueError("underwater_engagements needs two ISO dates "
                         f"(got {period_start!r}, {period_end!r})")
    ents = entities if entities is not None else load_entities(workspace_root)
    evs = _events(workspace_root, events)
    tally = billable_units_for_period(workspace_root, start, end,
                                      entities=ents, events=evs)

    billed_by_thread: dict[str, dict] = {}
    for line in tally["lines"] + tally["unpayered"]:
        b = billed_by_thread.setdefault(line["thread_id"],
                                        {"units": 0, "amount": 0,
                                         "amount_complete": True})
        b["units"] += line["units"]
        if line["amount"] is None:
            b["amount_complete"] = False
        else:
            b["amount"] += line["amount"]

    out = []
    for thread in (coaching_threads(workspace_root, ents)
                   + cohort_threads(workspace_root, ents)):
        tid = thread.get("id")
        delivered = len(sessions_since(workspace_root, tid, since=start,
                                       as_of=end, events=evs))
        if delivered < UNDERWATER_MIN_SESSIONS:
            continue
        billed = billed_by_thread.get(tid)
        row = {
            "thread_id": tid,
            "name": _thread_name(thread),
            "kind": thread.get("kind"),
            "sessions_delivered": delivered,
            "units_billed": billed["units"] if billed else 0,
            "amount_billed": (billed["amount"] if billed
                              and billed["amount_complete"] else None),
            "billing_known": billed is not None,
            "unbilled_sessions": delivered - (billed["units"] if billed else 0),
        }
        row["underwater"] = bool(billed) and row["unbilled_sessions"] > 0
        row["amount_per_session"] = (
            round(row["amount_billed"] / delivered, 2)
            if row["amount_billed"] is not None and delivered else None
        )
        out.append(row)

    out.sort(key=lambda r: (-r["unbilled_sessions"], r["name"] or ""))
    return out


__all__ = [
    "COACHING_KIND", "COHORT_KIND", "COACH_EVENT_TYPES", "SESSION_EVENT_TYPES",
    "DEFAULT_RENEWAL_LEAD_DAYS",
    "load_entities", "load_coach_events",
    "get_coach_config", "coach_enabled", "renewal_lead_days",
    "coaching_threads", "cohort_threads",
    "cohort_roster", "cohorts_for_person", "coaching_threads_for_person",
    "member_is_also_1to1",
    "cohort_for_meeting", "coaching_handoff_for_meeting",
    "sessions_since", "last_session",
    "load_open_arc_items", "commitments_promised_twice", "member_arc_slice",
    "billable_units_for_period", "renewal_windows_open",
    "underwater_engagements",
]


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps({
        "coach_enabled": coach_enabled(root),
        "coaching_threads": len(coaching_threads(root)),
        "cohorts": len(cohort_threads(root)),
        "renewal_windows_open": len(renewal_windows_open(root)),
    }, indent=2))
