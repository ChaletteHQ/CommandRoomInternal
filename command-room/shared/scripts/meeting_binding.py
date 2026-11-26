#!/usr/bin/env python3
"""meeting_binding.py: which calendar occurrence a written-up meeting is
(cr1#1838).

WHY THIS EXISTS
A meeting's write-up comes from the notes tool and names the meeting by the
notes' own id; the owner's choices for the meeting (the Hub's follow-up
plan) and the Today page name it by the calendar's. Nothing recorded tied
the two, and the "business or personal" stamp that decides whether a
follow-up may be drafted came from the run's own judgment. This module ties
one write-up to ONE calendar occurrence, or to none, in code, from the
calendar reads the capture pass staged (`eod_helpers.stage_fire_input`):
kind `capture_calendar` for the calendar connector's read and kind
`capture_calendar_personal` for the personal calendar's. The two row
builders record what it answers (`meeting_capture.build_meeting_event`,
`build_meeting_processed_event`).

THE RULE (never the run's word)
  * An event read twice (by both reads, or twice by one) is one event: the
    entries are folded by id before anything is counted, and an event either
    read calls personal is personal.
  * The calendar id, when the notes record carries one and an event has it,
    binds that event. An id no event has (a series id against an
    occurrence's) falls back to the next rule.
  * Otherwise: the same normalized title, and the write-up starting inside
    the event's own span (from `EARLY_MINUTES` before its start to its end,
    the end itself excluded, so back-to-back meetings are told apart). When
    both sides list people, at least one in common, the owner's own
    addresses left out of both sides first (the owner is on every meeting).
    When the owner's addresses cannot be read, no person in common can be
    trusted, so a match that needs one binds nothing (`no_owner`, fail
    closed).
  * Exactly one event binds. None is "none" (`no_match`); two or more are
    "none" (`ambiguous`); no calendar read is "none" (`no_calendar`).
  * A start written with no zone is read on the workspace's clock; with no
    workspace clock it cannot be placed, so nothing binds by time
    (`no_zone`).
  * The business or personal stamp is which read the event came from, in
    code: the calendar connector's read is business, the personal
    calendar's is personal. A binding by title and time alone, with no
    person to confirm it, carries the key but never the business mark.

Unbound is the safe direction: a write-up bound to none, or bound with no
business mark, gets no automatic follow-up (`followup_draft` holds it), so
until the match is right the owner sees fewer drafts, never a wrong one.

The binding: `{event_id, key, start, calendar, basis, reason}`. `key` is the
calendar key the Hub's rows carry (`cockpit_helpers.event_key`'s formula,
pinned equal by MEETBIND1), so a plan the owner made on that row is found by
it. stdlib only; reads the staged files; writes nothing.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any, Dict, Iterable, List, Optional

#: What a binding of none says, by why.
REASONS = ("no_calendar", "no_match", "ambiguous", "no_zone", "no_owner")
#: The marks a binding may carry.
CALENDAR_MARKS = ("business", "personal")
#: The two staged reads, and the mark each gives its events (in code).
KIND_CALENDAR = "capture_calendar"
KIND_PERSONAL = "capture_calendar_personal"
READ_MARKS = {KIND_CALENDAR: "business", KIND_PERSONAL: "personal"}
#: How a binding was made.
BASIS_CALENDAR_ID = "calendar_id"
BASIS_TITLE_TIME_PEOPLE = "title_time_people"
BASIS_TITLE_TIME = "title_time"
BASIS_NONE = "none"
#: How long before its event a write-up may start (notes opened early).
EARLY_MINUTES = 10
#: An event's span when its end is missing, does not parse, or is not after
#: its start.
DEFAULT_SPAN_MINUTES = 30
#: The longest span an event is given (an all-day block never swallows a day
#: of write-ups).
MAX_SPAN_MINUTES = 8 * 60
#: The characters of the calendar key (`cockpit_helpers.EVENT_KEY_LEN`).
KEY_LEN = 32
#: The series the read gives an event that is not recurring (rule R2 of #1814, decision 146b).
SERIES_NONE = "none"
#: Eighth check of #1814: words a read may put where it has no series id; read as unknown (no field), never
#: as a series key every such event would share.
SERIES_PLACEHOLDERS = frozenset({"null", "n/a", "na", "false", "unknown", "undefined", "nil", "-"})
#: The fire a capture pass stages in.
FIRE = "maintenance"


def event_key(value: Any) -> str:
    """The calendar key of an event id, as the Hub computes it
    (`cockpit_helpers.event_key`, pinned equal). "" for none. Pure."""
    v = str(value if value is not None else "").strip()
    if not v or len(v) > 512:
        return ""
    return hashlib.sha256(("ev:" + v).encode("utf-8")).hexdigest()[:KEY_LEN]


def none(reason: str) -> Dict[str, Any]:
    """A binding of none, saying why. Pure."""
    return {"event_id": "", "key": "", "start": "", "end": "", "series": "", "calendar": "", "basis": BASIS_NONE,
            "reason": reason if reason in REASONS else "no_match"}


def _read(workspace_root, rel: str, kind: str, fire: str) -> List[Dict[str, Any]]:
    import eod_helpers

    return eod_helpers._read_raw(str(workspace_root), str(rel), kind, fire)


def staged_events(workspace_root, calendar_rel: Any, fire: str = FIRE) -> Optional[List[Dict[str, Any]]]:
    """READ. The calendar reads the capture pass staged, each event cut to
    its kept fields in code and marked by the read it came from (`READ_MARKS`):
    the calendar connector's at `calendar_rel`, then the personal calendar's
    beside it when one was staged. None when the calendar connector's read
    was not staged or cannot be read (every write-up is then bound to none,
    `no_calendar`)."""
    if workspace_root is None or not str(calendar_rel or "").strip():
        return None
    try:
        events = [dict(e, calendar=READ_MARKS[KIND_CALENDAR])
                  for e in _read(workspace_root, calendar_rel, KIND_CALENDAR, fire)]
    except Exception:  # noqa: BLE001 - unread is none, never a guess
        return None
    try:
        import eod_helpers

        personal_rel = f"{eod_helpers._stage_dir(fire)}/{KIND_PERSONAL}.json"
        events += [dict(e, calendar=READ_MARKS[KIND_PERSONAL])
                   for e in _read(workspace_root, personal_rel, KIND_PERSONAL, fire)]
    except Exception:  # noqa: BLE001 - no personal read staged: its events are simply not read
        pass
    return events


def _instant(value: Any, tz: Any):
    """`(moment, zoneless)`: a start as an aware UTC moment (one written with
    no zone read on `tz`, the workspace's clock), or None when it does not
    parse or has no zone and no clock to read it on. Pure."""
    if value in (None, ""):
        return None, False
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None, False
    if d.tzinfo is not None:
        return d.astimezone(_dt.timezone.utc), False
    if tz is None:
        return None, True
    return d.replace(tzinfo=tz).astimezone(_dt.timezone.utc), True


def _own_key(addr: Any) -> str:
    """An address as the own-address test reads it (`inbox_helpers._own_key`):
    lower case, a `+tag` taken off the local part. Pure."""
    local, _, domain = str(addr or "").strip().lower().partition("@")
    return local.split("+", 1)[0] + "@" + domain if domain else local


def _people(values: Any, own: set) -> set:
    from meeting_discovery import _participant_token

    if isinstance(values, (str, dict)):
        values = [values]
    return {t for t in (_own_key(_participant_token(p)) for p in values or []) if t and t not in own}


def _fold(events: Iterable[Any]) -> List[Dict[str, Any]]:
    """The events, one entry per id, in first-read order; an event any read
    calls personal is personal. Pure."""
    out: Dict[str, Dict[str, Any]] = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("id") or "").strip()
        if not eid:
            continue
        if eid not in out:
            out[eid] = dict(ev)
        elif str(ev.get("calendar") or "").strip().lower() == "personal":
            out[eid]["calendar"] = "personal"
    return list(out.values())


def _span(ev: Dict[str, Any], tz: Any):
    """`(start, end, zoneless)` of an event's own span. Pure."""
    start, zl = _instant(ev.get("start"), tz)
    if start is None:
        return None, None, zl
    end, zl2 = _instant(ev.get("end"), tz)
    if end is None or end <= start:
        end = start + _dt.timedelta(minutes=DEFAULT_SPAN_MINUTES)
    return start, min(end, start + _dt.timedelta(minutes=MAX_SPAN_MINUTES)), zl or zl2


def _binding(ev: Dict[str, Any], basis: str) -> Dict[str, Any]:
    mark = str(ev.get("calendar") or "").strip().lower()
    mark = mark if mark in CALENDAR_MARKS else ""
    if basis == BASIS_TITLE_TIME and mark == "business":
        mark = ""  # title and time alone never earn the business mark
    eid = str(ev.get("id")).strip()
    # rule R2 of #1814: the event's end (its window) and its series key. Sixth review (MUST 2), refined by
    # decision 146b: the read says "none" for an event that is not recurring, and the binding records "none";
    # a series id records its key; a read that did not say (the field absent or empty) records no series
    # (unknown), which rule R2 treats as sharing (fail closed), so an unmarked recurring event never passes
    # for a one-off
    sid = str(ev.get("series_id") or "").strip()
    out = {"event_id": eid[:512], "key": event_key(eid), "start": str(ev.get("start") or "")[:40],
           "end": str(ev.get("end") or "")[:40], "calendar": mark, "basis": basis, "reason": ""}
    if sid.lower() == SERIES_NONE:
        out["series"] = SERIES_NONE
    elif sid.lower() in SERIES_PLACEHOLDERS:
        pass   # the read did not say: unknown
    elif sid and event_key(sid):
        out["series"] = event_key(sid)
    return out


def bind(meeting: Dict[str, Any], events: Optional[List[Dict[str, Any]]], *,
         own: Optional[Iterable[str]] = (), tz: Any = None) -> Dict[str, Any]:
    """The one calendar occurrence `meeting` is, or none. `meeting` is the
    write-up's own facts: `{source_ref, title, start, participants,
    calendar_event_id}` (the notes' calendar id, when they carry one).
    `events` carry their read's mark in `calendar` (`staged_events`); `own`
    the owner's own addresses, left out of both sides of the people test
    (None: they could not be read, and nothing binds on a person in common);
    `tz` the workspace's clock, for a start written with no zone. Pure."""
    from meeting_discovery import normalize_title

    if events is None:
        return none("no_calendar")
    folded = _fold(events)
    cid = str(meeting.get("calendar_event_id") or "").strip()
    if cid:
        by_id = [ev for ev in folded if str(ev.get("id")).strip() == cid]
        if by_id:
            return _binding(by_id[0], BASIS_CALENDAR_ID)
    title = normalize_title(meeting.get("title"))
    if not title:
        return none("no_match")
    when, zoneless = _instant(meeting.get("start"), tz)
    if when is None:
        return none("no_zone" if zoneless else "no_match")
    owner_known = own is not None
    mine = {_own_key(a) for a in own or () if str(a or "").strip()}
    people = _people(meeting.get("participants"), mine)
    hits, unplaced, unconfirmed = [], False, False
    for ev in folded:
        if normalize_title(ev.get("title")) != title:
            continue
        start, end, zl = _span(ev, tz)
        if start is None:
            unplaced = unplaced or zl
            continue
        if not (start - _dt.timedelta(minutes=EARLY_MINUTES) <= when < end):
            continue
        theirs = _people(ev.get("attendees"), mine)
        if people and theirs:
            if not owner_known:
                unconfirmed = True  # the owner may be the only one in common
            elif people & theirs:
                hits.append((ev, BASIS_TITLE_TIME_PEOPLE))
            continue
        hits.append((ev, BASIS_TITLE_TIME))
    if unconfirmed:
        return none("no_owner")  # fail closed: an event the people test could not judge
    if not hits:
        return none("no_zone" if unplaced else "no_match")
    if len(hits) > 1:
        return none("ambiguous")
    return _binding(*hits[0])


def _own_addresses(workspace_root) -> Optional[set]:
    """The owner's own addresses, or None when none can be read (the reader
    never raises, so an empty answer is an unknown owner, not an owner with
    no address)."""
    try:
        from contact_capture import own_addresses

        got = {_own_key(a) for a in (own_addresses(str(workspace_root)) or set()) if a}
    except Exception:  # noqa: BLE001 - unknown owner: fail closed in `bind`
        return None
    return got or None


def _workspace_tz(workspace_root):
    """The workspace's clock, or None when it is not set or could not be
    loaded (then a start with no zone is never placed)."""
    try:
        from tz import load_workspace_tz, zone_fell_back

        zone = load_workspace_tz(str(workspace_root))
        return None if zone_fell_back(zone) else zone
    except Exception:  # noqa: BLE001
        return None


def bind_from_stage(workspace_root, meeting: Dict[str, Any], calendar_rel: Any,
                    fire: str = FIRE) -> Dict[str, Any]:
    """READ. `bind` over the calendar reads the capture pass staged, with the
    owner's own addresses and the workspace's clock."""
    events = staged_events(workspace_root, calendar_rel, fire)
    if events is None:
        return none("no_calendar")
    return bind(meeting, events, own=_own_addresses(workspace_root), tz=_workspace_tz(workspace_root))


__all__ = ["REASONS", "CALENDAR_MARKS", "KIND_CALENDAR", "KIND_PERSONAL", "READ_MARKS", "BASIS_CALENDAR_ID",
           "BASIS_TITLE_TIME_PEOPLE", "BASIS_TITLE_TIME", "BASIS_NONE", "EARLY_MINUTES", "DEFAULT_SPAN_MINUTES",
           "MAX_SPAN_MINUTES", "event_key", "none", "staged_events", "bind", "bind_from_stage"]
