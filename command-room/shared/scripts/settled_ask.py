#!/usr/bin/env python3
"""
Settled asks on the inbound-mail lane (INCAP2 DD-2, cr1#193).

WHY THIS EXISTS
---------------
An email ask ("could you share the invoice breakdown?") that a LATER meeting
with the same person already covered came back as a fresh reply-owed item to
review, with nothing saying the call had happened. Dedup on this lane checks
the message's source reference and the open set; neither sees a meeting. So
the operator re-reviewed work already handled in conversation.

THE RULE (SPEC_INCAP2 DD-2)
---------------------------
Before a reply-owed row is written, the recall pass reads the `meeting`
records dated AFTER the email and no later than this fire. When one has the
same counterparty and an overlapping topic, the row gets a stated reason that
names that meeting (its title and its day). That is all it does:

  * the row is still WRITTEN, never dropped and never merged;
  * it stays at the tier the relevance gate gave it. A reply-owed row is
    already held for review on this lane (`REPLY_OWED_REVIEW_REASON`), so the
    reason is APPENDED to the one there, never swallowing it; on the observed
    tier it travels as `held_review_reason`, the way every reason does;
  * the relevance gate (`capture_gate.classify_capture`) runs first and is
    untouched;
  * a waiting-on row (someone's promise to the user) is never touched: a
    meeting on a promise's topic is not evidence the promise was kept, and a
    real promise moved to review is the costlier mistake;
  * binding the ask to the meeting record is out of scope (the issue's known
    unknown): what counts as "handled" is still an open product definition,
    so the operator decides, with the meeting named in front of them.

THE MATCH
---------
  * TIME: the meeting's `ts` is strictly after the email's own time and not
    after the fire. No email time, or no meeting time, means no match.
  * PARTY: a person id shared between the row and the meeting
    (`person_ids`), or the row's free-text counterparty matching one of the
    meeting's `attendees_external` on the FULL name (every name word, two at
    least), never on a first name alone. The workspace owner is taken out of
    both sides, since the owner is a party to everything.
  * TOPIC: at least TWO shared topic words between the row's TITLE and the
    meeting's TITLE (never its summary, which covers far more than the
    meeting was about), using INCAP2's own topic words
    (`progress_narration._topic_tokens`): action verbs, generic descriptors,
    time words and every party's name words are taken out first, so "share
    the notes" never matches a meeting on the person's name or on "notes".
  * PERSONAL: a meeting `personal_leak.is_personal` calls personal is never
    a match, so a personal meeting's title never lands in a business row.

The bar is narrow on purpose: a wrong "may already have settled it" can
lead the owner to dismiss a real ask. A miss only means today's capture.

Callers: `inbound_capture.capture_inbound_items` and
`inbox_helpers.plan_inbound_capture`, for reply-owed rows only.

stdlib only. One read: `load_meetings` walks the ledger through
`events_io.load_events_owner_scoped`, the owner-tier reader; any failure
gives no meetings and so today's capture.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from progress_narration import (  # noqa: E402
    _name_words,
    _topic_tokens,
    party_name_words,
)

#: The stated reason's lead clause; the meeting's title and day follow it.
SETTLED_REVIEW_REASON = (
    "a later meeting with this person may already have settled it"
)

#: A meeting record in one of these states did not happen (yet), so it
#: cannot have settled anything.
#: Shared topic words a meeting title needs with the ask's title.
MIN_SHARED_TOPIC_WORDS = 2

#: Name words a free-text name needs before it can match at all: a first
#: name alone names too many people.
_MIN_FULL_NAME_WORDS = 2

_NOT_HELD_STATUSES = frozenset({
    "cancelled", "canceled", "scheduled", "planned", "no_show", "no-show",
    "declined",
})


def _parse_ts(value) -> Optional[_dt.datetime]:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if len(s) == 10:
            out = _dt.datetime.fromisoformat(s)
        else:
            out = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=_dt.timezone.utc)
    return out


def load_meetings(workspace_root) -> List[dict]:
    """Every `meeting` record in the workspace's ledger. Any read failure
    gives [] and so today's capture: an ask with no meeting named is what
    the lane did before, a fire that fails on it is not."""
    if not workspace_root:
        return []
    try:
        from events_io import load_events_owner_scoped

        # The owner-tier reader: this is a capture lane on the owner's own
        # ledger, so it sees every meeting, and the raw read stays inside
        # the allowlisted shard reader (the personal-firewall guard, D4c).
        events, _skipped = load_events_owner_scoped(workspace_root)
        meetings = [ev for ev in events
                    if isinstance(ev, dict) and ev.get("type") == "meeting"]
    except Exception:  # noqa: BLE001
        return []
    # A meeting with a personally tied person and no business binding is a
    # personal meeting (`personal_leak`'s join); it is left out here, and
    # the explicit markers are checked again at match time.
    try:
        import personal_leak

        pids = personal_leak.personal_tie_ids(workspace_root)
        threads = personal_leak.business_thread_ids(workspace_root)
        return [ev for ev in meetings
                if not personal_leak.is_personal(
                    ev, personal_ids=pids, business_threads=threads)]
    except Exception:  # noqa: BLE001 - no personal read, no meetings
        return []


def _full_name(value) -> tuple:
    """A name as its ordered words, lowercased."""
    return tuple(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _full_names(values, excl=frozenset()) -> set:
    """The full names among `values` that can match: two name words at
    least, and not one of the owner's own names."""
    out = set()
    for v in values or ():
        name = _full_name(v)
        if len(name) >= _MIN_FULL_NAME_WORDS and name not in excl:
            out.add(name)
    return out


def _is_personal_meeting(ev) -> bool:
    try:
        import personal_leak

        return bool(personal_leak.is_personal(ev))
    except Exception:  # noqa: BLE001 - unknown reads as personal: skip it
        return True


def later_meeting_match(
    data: dict,
    meetings: Iterable[dict],
    *,
    asked_at,
    person_ids=(),
    exclude_party_ids=frozenset(),
    exclude_party_names=(),
    party_names=(),
    now=None,
) -> Optional[dict]:
    """The first meeting record held after `asked_at` and no later than `now`
    that shares a counterparty and two title topic words with the row
    `data`, else None. Pure; see the module docstring for the tests."""
    data = data or {}
    asked = _parse_ts(asked_at)
    if asked is None:
        return None
    fire = _parse_ts(now) if now else _dt.datetime.now(_dt.timezone.utc)
    if fire is None:
        return None

    excl_ids = {str(i) for i in (exclude_party_ids or ()) if i}
    excl_names = {_full_name(n) for n in (exclude_party_names or ())}

    row_ids = {str(p) for p in (person_ids or ()) if p}
    for k in ("owner_id", "counterparty_id"):
        if data.get(k):
            row_ids.add(str(data[k]))
    row_ids -= excl_ids
    row_names = _full_names(
        [data.get("owner_external"), data.get("counterparty_name")],
        excl_names)
    if not row_ids and not row_names:
        return None

    drop = (_name_words(party_names) | _name_words(exclude_party_names)
            | _name_words([data.get("owner_external"),
                           data.get("counterparty_name")]))
    row_topic = _topic_tokens(data.get("title"), drop)
    if len(row_topic) < MIN_SHARED_TOPIC_WORDS:
        return None

    for ev in meetings or ():
        if not isinstance(ev, dict) or ev.get("type") != "meeting":
            continue
        if _is_personal_meeting(ev):
            continue
        md = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if str(md.get("status") or "").strip().lower() in _NOT_HELD_STATUSES:
            continue
        held = _parse_ts(ev.get("ts"))
        if held is None or not (asked < held <= fire):
            continue
        m_ids = {str(p) for p in (ev.get("person_ids") or ()) if p} - excl_ids
        m_names = _full_names(md.get("attendees_external") or (), excl_names)
        if not ((row_ids & m_ids) or (row_names & m_names)):
            continue
        m_topic = _topic_tokens(
            md.get("title"),
            drop | _name_words(md.get("attendees_external") or ()))
        if len(row_topic & m_topic) >= MIN_SHARED_TOPIC_WORDS:
            return ev
    return None


def settled_reason(meeting: dict) -> str:
    """The stated reason for one matched meeting: the lead clause, then the
    meeting's title and day. A `;` in a title would split the stored reason
    into two clauses, so it is written as a comma."""
    md = (meeting or {}).get("data") or {}
    title = re.sub(r"\s+", " ", str(md.get("title") or "")).strip()
    title = title.replace(";", ",")
    held = _parse_ts((meeting or {}).get("ts"))
    day = held.date().isoformat() if held else ""
    named = f'"{title}"' if title else "a meeting"
    return f"{SETTLED_REVIEW_REASON}: {named}" + (f" on {day}" if day else "")


def note_if_settled(
    event: dict,
    meetings: Iterable[dict],
    *,
    asked_at,
    user_person_id,
    user_names=(),
    workspace_root=None,
    now=None,
) -> Optional[dict]:
    """Add the settled-by-a-meeting reason to a built reply-owed `event` when
    a later meeting covered it. Returns the meeting it matched (and stamps
    `pending_review` plus the appended reason on `event["data"]`), else None
    and `event` is left exactly as it was. The row is never dropped here and
    its tier is the caller's, unchanged. Never raises: a malformed meeting
    record means no reason, never a failed fire."""
    try:
        return _note_if_settled(event, meetings, asked_at=asked_at,
                                user_person_id=user_person_id,
                                user_names=user_names,
                                workspace_root=workspace_root, now=now)
    except Exception:  # noqa: BLE001 - fail quiet: today's capture
        return None


def _note_if_settled(event, meetings, *, asked_at, user_person_id,
                     user_names, workspace_root, now):
    data = (event or {}).get("data")
    if not isinstance(data, dict):
        return None
    meetings = list(meetings or ())
    if not meetings:
        return None
    ids = set(event.get("person_ids") or ())
    for k in ("owner_id", "counterparty_id"):
        if data.get(k):
            ids.add(data[k])
    for ev in meetings:
        if isinstance(ev, dict):
            ids.update(p for p in (ev.get("person_ids") or ()) if p)
    match = later_meeting_match(
        data, meetings,
        asked_at=asked_at,
        person_ids=event.get("person_ids") or (),
        exclude_party_ids={user_person_id} if user_person_id else frozenset(),
        exclude_party_names=user_names or (),
        party_names=sorted(party_name_words(workspace_root, ids)),
        now=now,
    )
    if match is None:
        return None
    reason = settled_reason(match)
    data["pending_review"] = True
    prior = str(data.get("review_reason") or "").strip()
    data["review_reason"] = f"{prior}; {reason}" if prior else reason
    return match


__all__ = [
    "SETTLED_REVIEW_REASON",
    "MIN_SHARED_TOPIC_WORDS",
    "load_meetings",
    "later_meeting_match",
    "settled_reason",
    "note_if_settled",
]
