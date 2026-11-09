"""Is a meeting ABOUT a scheduling item? (cr1#765)

One job: the two reads the calendar closer's predicate was missing.

WHY. `commitment_policy.calendar_matches` matched a scheduling item to a
meeting by WHO attended and never by what the meeting was about, so "set up
a call with the landlord about the lease renewal" closed because the same
person sat in the weekly team standup. Task captures made it worse: a
task's creator is recorded as the counterparty, so any meeting the creator
attended was "the meeting with the other side".

THE RULES, both pure:

  * `subject_words(title, names)`: what the item is about, beyond the people
    and beyond the act of meeting. The title's topic ("about ...", when it
    has one) or else its content words, minus the parties' names, who the
    title says the meeting is with (the "with ..." span, "call the
    accountant", a role word), the words that only say "meet" or when or for
    how long (`SCHEDULING_WORDS`), and numbers. "set up a call with Quinn"
    has no subject: a meeting with Quinn IS the item, and attendance alone
    still closes it (the pre-cr1#765 behaviour, kept whole). "set up a call
    with the landlord about the lease renewal" has one (lease, renewal), and
    the meeting must name it: "Landlord catch-up" does not (owner ruling,
    2026-11-07: the counterparty's role word is not the subject).
  * `meeting_words(meeting)`: the content words of the meeting's title,
    subject, summary, notes, description and agenda.
  * `meeting_grade(row, meeting, names)`: how sure the meeting is about the
    item (a word and its plural are one word). `SURE`: no subject, the
    meeting's title names the subject, or a note is very explicit about the
    item (`note_is_explicit`). `UNSURE`: one telling word of several in the
    title, or two subject words in the notes. `NOT_ABOUT` otherwise.
    `meeting_is_about` is either of the first two. The meeting's own
    attendees are names too: "a call with Alex and Priya" has no subject at
    a meeting Priya attends.
  * `calendar_parties(row, user_id)`: the row's parties for the calendar
    predicate. A task-captured item the owner owes (`task_source_ref` on the
    row, owner is the user) carries its creator as counterparty, and the
    creator is not the other side of the meeting the item asks for: that
    row has no calendar party, so it is never matched by attendance. Every
    other row keeps today's party set.
  * `owed_task_grade(row, meeting, names)`: the read for that row instead
    (owner ruling, 2026-11-07): `SURE` closes, `UNSURE` proposes, and it is
    the subject that decides, not who created the task. With no subject,
    only the person the title names attending is `SURE`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: Words that only say "meet": never a subject on their own.
SCHEDULING_WORDS = frozenset({
    "call", "calls", "phone", "meeting", "meetings", "meet", "meetup",
    "schedule", "scheduled", "scheduling", "set", "setup", "book", "booked",
    "booking", "chat", "sync", "catch", "catchup", "time", "times", "talk",
    "zoom", "teams", "video", "discuss", "discussion", "get", "together",
    "follow", "followup", "touch", "base", "coffee", "lunch", "dinner",
    "breakfast", "drinks", "visit", "session", "demo", "intro",
    "introduction", "connect", "huddle", "standup", "checkin", "check",
    "appointment", "slot", "calendar", "invite", "find", "lock", "propose",
    "day", "week", "month", "next", "quick", "team", "him", "her", "them",
    "hop", "jump", "grab", "arrange", "organise", "organize", "line",
    "reconnect", "regroup", "debrief", "walk", "through", "walkthrough",
    # another way to say meet, or to arrange it
    "send", "sent", "availability", "reach", "out", "confirm", "confirmed",
    "zoom", "my", "our",
    "kickoff", "kick", "off", "sit", "down", "sitdown", "reschedule",
    "rescheduled", "rebook", "up", "first", "initial",
    # how long
    "minute", "min", "hour", "hr", "half",
    # when
    "today", "tomorrow", "tonight", "morning", "afternoon", "evening", "noon",
    "weekend", "early", "late", "later", "soon", "asap", "this", "coming",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar",
    "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})

#: Words too common to show two topics are the same: "review the lease
#: renewal" and "Weekly review" share only this. Not counted as the shared
#: word unless the item has nothing else.
GENERIC_TOPIC_WORDS = frozenset({
    "review", "plan", "update", "new", "project", "status", "report",
    "planning", "weekly", "monthly", "quarterly", "general", "item", "meeting"})

#: How sure a meeting is about an item.
NOT_ABOUT, UNSURE, SURE = 0, 1, 2

#: Who a meeting may be with, by role: the word says who, never what.
ROLE_WORDS = frozenset({
    "landlord", "tenant", "accountant", "bookkeeper", "lawyer", "attorney",
    "banker", "broker", "advisor", "adviser", "consultant", "contractor",
    "agent", "realtor", "auditor"})

#: A number or a time, never "Q3".
_NUMBER_RE = re.compile(r"\d+(?:st|nd|rd|th|am|pm|min|mins|m|h|hr|hrs)?")

#: The "with ..." span of a title: the people the meeting is with.
_WITH_SPAN_RE = re.compile(
    r"\b(?:with|meet)\s+(.+?)(?=\s+(?:about|re|regarding|on|to|for|next|this|in|at|by)\b|$)",
    re.IGNORECASE)

#: "Call the accountant", "talk to our landlord": the object is who.
_PERSON_OBJECT_RE = re.compile(
    r"\b(?:with|to|call|meet|ring|phone)\s+(?:the|our|my|their|his|her)\s+([\w'-]+)",
    re.IGNORECASE)

#: Where the title says what the meeting is about.
_TOPIC_RE = re.compile(
    r"\b(?:about|regarding|concerning|re|to discuss|to review|to go over)\b(.+)$",
    re.IGNORECASE)

#: The meeting fields that say what it was about.
MEETING_TEXT_FIELDS = ("title", "subject", "summary", "notes", "description",
                       "agenda", "name")

#: A note's opening that says what the meeting was about. Anchored, so "we
#: did not discuss the lease" is not one.
_EXPLICIT_NOTE_RE = re.compile(
    r"^\s*(?:(?:agenda|topic|subject|purpose|re|regarding)\s*[:\-]?\s+"
    r"|(?:(?:this|the)\s+)?(?:meeting|call|session|discussion)\s+(?:(?:was|is)\s+)?"
    r"(?:about|on|regarding|re|for)\s+"
    r"|(?:we\s+)?(?:discussed|covered|reviewed|went through|talked about|met about)\s+)"
    r"(.+)$", re.IGNORECASE)

_NOTE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _tokens(text) -> set:
    try:
        from cru_match import _content_tokens
        return set(_content_tokens(str(text or "")))
    except Exception:  # noqa: BLE001
        return {w for w in str(text or "").lower().split() if len(w) > 1}


def _name_tokens(names: Iterable) -> set:
    out: set = set()
    for n in names or ():
        out |= _tokens(n)
    return out


def row_names(row: dict, people_names: Optional[dict] = None) -> list:
    """Every name the row's parties go by: the names the row carries itself
    and, through `people_names` ({person id: [names]}), the names on file
    for every party id it names."""
    d = (row or {}).get("data") if isinstance((row or {}).get("data"), dict) else {}
    names: list = []
    for key in ("counterparty_name", "owner_external", "owner_name"):
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            names.append(v.strip())
    v = d.get("counterparty_names")
    if isinstance(v, list):
        names += [str(x) for x in v if isinstance(x, str) and x.strip()]
    if people_names:
        ids: set = set()
        for scope in ((row or {}), d):
            pv = scope.get("person_ids") if isinstance(scope, dict) else None
            if isinstance(pv, list):
                ids.update(str(x) for x in pv if x)
        for key in ("counterparty_id", "owner_id"):
            if isinstance(d.get(key), str) and d.get(key).strip():
                ids.add(d[key].strip())
        cv = d.get("counterparty_ids")
        if isinstance(cv, list):
            ids.update(str(x) for x in cv if x)
        for pid in ids:
            names += [str(n) for n in (people_names.get(pid) or []) if n]
    return names


def _fold(words: Iterable) -> set:
    from cru_match import fold_plural
    return {fold_plural(w) for w in words}


def _with_spans(title) -> list:
    return _WITH_SPAN_RE.findall(str(title or ""))


def _with_span_names(title) -> list:
    """The capitalised words of the title's "with ..." span: the people named
    there, on file or not. "the Landlord" is a role, not a name."""
    out: list = []
    for span in _with_spans(title):
        words = span.split()
        for i, word in enumerate(words):
            if word[:1].isupper() and (i == 0 or words[i - 1].lower() != "the"):
                out.append(word)
    return out


def subject_words(title, names: Iterable = ()) -> set:
    """What the item is about: its content words minus its parties' names
    minus the words that only say "meet", when or for how long. Empty means
    the meeting itself is the item."""
    gone = (_fold(_name_tokens(list(names or ()) + _with_span_names(title))
                  | SCHEDULING_WORDS | ROLE_WORDS)
            | _fold(_name_tokens(_with_spans(title)))
            | _fold(_name_tokens(_PERSON_OBJECT_RE.findall(str(title or "")))))

    def what(text) -> set:
        return {w for w in _tokens(text)
                if not _NUMBER_RE.fullmatch(w) and next(iter(_fold([w]))) not in gone}

    topic = _TOPIC_RE.search(str(title or ""))
    return (what(topic.group(1)) if topic else set()) or what(title)


def meeting_words(meeting: dict) -> set:
    """The content words of everything the meeting record says it was about."""
    out: set = set()
    for scope in ((meeting or {}), (meeting or {}).get("data") or {}):
        if not isinstance(scope, dict):
            continue
        for key in MEETING_TEXT_FIELDS:
            v = scope.get(key)
            if isinstance(v, str) and v.strip():
                out |= _tokens(v)
    return out


def _title_of(meeting: dict) -> str:
    return " ".join(str(scope.get(k) or "")
                    for scope in ((meeting or {}), (meeting or {}).get("data") or {})
                    if isinstance(scope, dict) for k in ("title", "subject", "name"))


def meeting_names(meeting: dict, people_names: Optional[dict] = None) -> list:
    """The names of the meeting's attendees: those it carries itself and,
    through `people_names`, those on file for its bound people."""
    d = (meeting or {}).get("data") if isinstance((meeting or {}).get("data"), dict) else {}
    names = [str(n) for n in d.get("attendees_external") or [] if isinstance(n, str)]
    if people_names:
        from event_refs import meeting_person_ids
        for pid in meeting_person_ids(meeting or {}):
            names += [str(n) for n in people_names.get(str(pid)) or [] if n]
    return names


def _note_text(meeting: dict) -> str:
    """Everything the meeting record says beyond its title."""
    return "\n".join(str(scope.get(k)) for scope in ((meeting or {}), (meeting or {}).get("data") or {})
                     if isinstance(scope, dict)
                     for k in ("summary", "notes", "description", "agenda")
                     if isinstance(scope.get(k), str))


def note_is_explicit(meeting: dict, pool: set, wanted: set) -> bool:
    """Does a note say outright that the meeting was about the item? A
    sentence that opens by saying what the meeting was about, and goes on to
    name the subject and nothing else: "Topic: lease". Anything less keeps
    the two-word rule."""
    for sentence in _NOTE_SPLIT_RE.split(_note_text(meeting)):
        hit = _EXPLICIT_NOTE_RE.match(sentence)
        if not hit:
            continue
        said = _fold(_tokens(re.split(r"[,;]", hit.group(1))[0]))
        if said & pool and said <= wanted | _fold(SCHEDULING_WORDS):
            return True
    return False


def _row_subject(row: dict, meeting: dict, people_names, names) -> set:
    d = (row or {}).get("data") if isinstance((row or {}).get("data"), dict) else {}
    all_names = (list(names or ()) + row_names(row, people_names)
                 + meeting_names(meeting, people_names))
    return subject_words(d.get("title") or (row or {}).get("title"), all_names)


def _subject_grade(subject: set, meeting: dict) -> int:
    wanted = _fold(subject)
    telling = wanted - _fold(GENERIC_TOPIC_WORDS)
    pool = telling or wanted
    # The title says what the meeting is: one telling word is enough to match
    # there, and two (or the only one) are sure.
    in_title = telling & _fold(_tokens(_title_of(meeting)))
    if in_title and len(in_title) >= min(2, len(telling)):
        return SURE
    if note_is_explicit(meeting, pool, wanted):
        return SURE
    if in_title:
        return UNSURE
    # Notes wander: they take two ("gym membership renewal" is not the lease).
    if len(pool & _fold(meeting_words(meeting))) >= min(2, len(pool)):
        return UNSURE
    return NOT_ABOUT


def meeting_grade(row: dict, meeting: dict, *, people_names: Optional[dict] = None,
                  names: Iterable = ()) -> int:
    """How sure the meeting is about the item (the module docstring)."""
    subject = _row_subject(row, meeting, people_names, names)
    return _subject_grade(subject, meeting) if subject else SURE


def meeting_is_about(row: dict, meeting: dict, *, people_names: Optional[dict] = None,
                     names: Iterable = ()) -> bool:
    """True when the item has no subject beyond meeting its parties, or the
    meeting names that subject."""
    return meeting_grade(row, meeting, people_names=people_names,
                         names=names) != NOT_ABOUT


def owed_task_grade(row: dict, meeting: dict, *, people_names: Optional[dict] = None,
                    names: Iterable = ()) -> int:
    """`meeting_grade` for a task-captured item the owner owes. Attendance
    says nothing here, so an item with no subject is `SURE` only when everyone
    its title names attended, and `NOT_ABOUT` otherwise."""
    subject = _row_subject(row, meeting, people_names, names)
    if subject:
        return _subject_grade(subject, meeting)
    d = (row or {}).get("data") if isinstance((row or {}).get("data"), dict) else {}
    named = _fold(_name_tokens(_with_span_names(d.get("title") or (row or {}).get("title"))))
    present = _fold(_name_tokens(meeting_names(meeting, people_names)))
    return SURE if named and named <= present else NOT_ABOUT


def is_task_capture(row: dict) -> bool:
    """Was this row captured from a task board item?"""
    d = (row or {}).get("data") if isinstance((row or {}).get("data"), dict) else {}
    if isinstance(d.get("task_source_ref"), dict):
        return True
    return str(d.get("channel") or "").strip().lower() == "tasks"


def is_owed_task(row: dict, user_id="") -> bool:
    """A task-captured row the user owes."""
    d = (row or {}).get("data") if isinstance((row or {}).get("data"), dict) else {}
    owner = str(d.get("owner_id") or "").strip()
    return is_task_capture(row) and bool(owner) and owner == str(user_id or "")


def calendar_parties(row: dict, user_id="", *, party_ids: Optional[set] = None) -> set:
    """The non-user party ids a meeting must include to count as the meeting
    the row asks for. `party_ids` is the caller's own read of the row's party
    ids (the policy's `_row_party_ids`); a task-captured row the user owes
    has none: its creator is who asked, not who the meeting is with."""
    ids = {str(p) for p in (party_ids or set()) if p}
    ids.discard(str(user_id or ""))
    if not ids:
        return set()
    return set() if is_owed_task(row, user_id) else ids


def people_names(workspace_root) -> dict:
    """`{person id: [names]}` on file (the shared reader, `people_names.py`)."""
    from people_names import people_names as _read
    return _read(workspace_root)


__all__ = ["NOT_ABOUT", "UNSURE", "SURE", "SCHEDULING_WORDS", "ROLE_WORDS",
           "MEETING_TEXT_FIELDS", "subject_words", "meeting_words", "meeting_names",
           "note_is_explicit", "meeting_grade", "meeting_is_about", "owed_task_grade",
           "is_task_capture", "is_owed_task", "calendar_parties", "row_names",
           "people_names"]
