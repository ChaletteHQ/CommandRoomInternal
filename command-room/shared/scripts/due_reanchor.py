#!/usr/bin/env python3
"""
due_reanchor.py — THE re-anchoring for anything that names a day (SPEC TOMFILT1).

WHY THIS EXISTS
===============
Two related defects, one root cause: a surface computed "what day is this"
ONCE, at write time or at the moment a proposal was drafted, and rendered
that answer forever after without ever checking it against the clock again.

  1. **The day-intent proposer.** The 2026-08-19 close proposed "Move
     tonight's dinner reservation to 5pm" as a plan item for the FOLLOWING
     day — the proposer read the item's title and due date without asking
     whether either one was already anchored to the day being closed.
  2. **Deadline prose.** "before Thursday's call" or "overdue since Aug 6"
     renders identically whether the item came due yesterday or five weeks
     ago — the phrase was composed once and never re-read against today.

ONE module answers both, because they are the same question asked twice:
"does this item belong to a day that has already happened?" Part A
(`is_anchored_to_day` / `is_same_day_text`) answers it for the PROPOSER —
should this item be offered as tomorrow's plan. Part B (`render_due_phrase` /
`render_due_clause`) answers it for RENDERING — how should this item's due
date read, from today, right now.

THREE CALLERS, ONE FUNCTION (SPEC TOMFILT1 acceptance — golden-pinned on the
queue row, the slipped line, and the tomorrow block). A caller that formats
its own due text is the exact defect this module exists to close: two
derivations of "how stale is this" is how one surface says "due Aug 6" and
another says "overdue since Aug 6" about the identical row.

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def parse_date(value) -> Optional[_dt.date]:
    """`YYYY-MM-DD...` (or a `date`/`datetime`) -> a `date`, or None.

    Deliberately forgiving of a trailing time/offset (`due` and `now_iso`
    both arrive as either a bare date or a full timestamp across this
    codebase) and deliberately silent on anything else — a malformed date is
    an absent date, never a crash mid-render.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def due_age_days(due, anchor) -> Optional[int]:
    """Whole days `anchor` sits PAST `due`, or None when not parseable or
    not yet due. `anchor` is "today" for the caller's purposes — the
    workspace-local date, never a raw UTC slice (TOMFILT1 reads `for_date`
    or `now_iso`, both already resolved workspace-local by their callers)."""
    d = parse_date(due)
    a = parse_date(anchor)
    if d is None or a is None:
        return None
    delta = (a - d).days
    return delta if delta > 0 else None


def render_due_phrase(due, anchor) -> str:
    """THE due-phrase renderer (SPEC TOMFILT1 §2). Re-anchored to `anchor`
    ("today", from the caller's own clock) on every call — never stored,
    never cached.

    "undated" / "due today" / "due Aug 6" / "due Aug 6 — 19 days ago".

    Past-due ALWAYS carries its age. A bare "overdue since Aug 6" or a raw
    ISO date is exactly the shape that goes stale the moment it sits on
    screen for a week; a phrase that states its own age cannot.

    An ABSENT due (`None`/empty) renders "undated"; a PRESENT but
    unparseable one (never expected on a real row, but a render must not
    raise over one) renders the raw value rather than claiming there is no
    date at all — those are different claims.
    """
    if not due:
        return "undated"
    d = parse_date(due)
    if d is None:
        return f"due {due}"
    a = parse_date(anchor)
    label = f"{d.strftime('%b')} {d.day}"
    if a is None:
        # No anchor to compare against — the date alone, never a guess at
        # its age.
        return f"due {label}"
    if d == a:
        return "due today"
    if d < a:
        age = (a - d).days
        age_txt = "1 day ago" if age == 1 else f"{age} days ago"
        return f"due {label} — {age_txt}"
    return f"due {label}"


def render_moved_phrase(when_iso, workspace_path=None) -> str:
    """THE "moved to …" phrase (CUT-C item 5, ATTENDED_TEST_v5.28.0 B2.5):
    weekday + date, in the WORKSPACE timezone — "Sunday, Sep 13". The Later…
    ack used to compose its own weekday from prose and said "Friday, Sep 13"
    for a Sunday; this is the one deterministic composer, and
    `commitment_state.apply_later` returns it as `moved_phrase`.

    Input shapes, and the day each names:
      - a bare `YYYY-MM-DD` (what `parse_later_when` and `apply_later`'s
        `new_due` carry): that calendar day, no timezone involved — a value
        that never carried a time must not be shifted;
      - a full timestamp: the day it falls on in the workspace timezone
        (`tz.to_local`), so an evening push west of UTC names the evening's
        own day, never the next UTC day — the HYGIENE9 R4 class;
      - a timestamp when the workspace tz cannot resolve (no path, tz
        unconfigured): the calendar day IN THE OFFSET THE INPUT CARRIED — the
        same rule `commitment_state._later_when` uses for `new_due` — never a
        UTC re-slice.
    Empty / unparseable → "" (an ack composes around it; never a crash).
    """
    raw = str(when_iso or "").strip()
    if not raw:
        return ""
    d = None
    if len(raw) == 10 and raw.count("-") == 2:
        d = parse_date(raw)
    else:
        try:
            dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None:
            if workspace_path:
                try:
                    from tz import to_local
                    local = to_local(dt, workspace_path=workspace_path)
                    d = local.date() if local is not None else None
                except Exception:
                    d = None
            if d is None:
                # the day AS GIVEN, in its own offset (never re-sliced to UTC)
                d = dt.date()
    if d is None:
        return ""
    return f"{d.strftime('%A')}, {d.strftime('%b')} {d.day}"


def render_due_clause(due, anchor) -> str:
    """The bare clause for a "was due {X}" template — no leading "due".

    "today" / "Aug 6" / "Aug 6 — 19 days ago" / "an undated date". Same
    re-anchoring as `render_due_phrase`, phrased to slot into prose that
    already supplies its own verb (`eod_synthesis.SLIP_DATE`: "{title} was
    due {due} and has not moved.").
    """
    phrase = render_due_phrase(due, anchor)
    if phrase == "undated":
        return "an undated date"
    if phrase == "due today":
        return "today"
    return phrase[len("due "):]


# ---------------------------------------------------------------------------
# Anchoring — is this item ABOUT a day that has already happened?
# ---------------------------------------------------------------------------


def is_anchored_to_day(due, day) -> bool:
    """True when `due` parses AND falls on or before `day` — SPEC TOMFILT1
    §1's due-date predicate ("a due date <= today"). An unparseable or
    absent due is never anchored by this predicate alone; text carries that
    signal instead (`is_same_day_text`)."""
    d = parse_date(due)
    anchor = parse_date(day)
    if d is None or anchor is None:
        return False
    return d <= anchor


# Same-day deictic words — the CEO speaking about THIS day, never a future
# one. Deliberately narrow: "tomorrow", "next week" and weekday names are
# FUTURE references and must never trip this predicate (that is the false
# positive TOMFILT1's fixture "the same item un-anchored -> eligible" guards
# against once the deictic word is removed).
_SAME_DAY_DEICTIC_RE = re.compile(
    r"""(?ix)
      \btonight\b
    | \btoday\b
    | \bthis\s+(?:evening|afternoon|morning)\b
    | \bearlier\s+today\b
    | \bby\s+(?:eod|cob|end\s+of\s+day)\b
    """
)

# A bare clock time ("5pm", "5:00 PM") carries no day of its own — absent
# any future-day qualifier alongside it, the only day it can mean is the one
# the speaker is IN. This is SPEC TOMFILT1 §1's "a same-day time" predicate.
_BARE_TIME_RE = re.compile(r"(?i)\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b")

# A future-day qualifier anywhere in the text vetoes the bare-time reading —
# "5pm Thursday" or "the 5pm call next Tuesday" names a day, and it is not
# today's.
_FUTURE_DAY_WORDS_RE = re.compile(
    r"""(?ix)
      \btomorrow\b
    | \bnext\s+\w+
    | \b(?:mon|tues?|wed|thur?s?|fri|sat|sun)(?:day)?\b
    | \b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b
    | \b\d{4}-\d{2}-\d{2}\b
    """
)


def is_same_day_text(text: Optional[str]) -> bool:
    """True when `text` reads as anchored to THIS day — a same-day deictic
    ("tonight", "today", "this evening") or a bare clock time with no future
    day named alongside it ("5pm", not "5pm Thursday").

    Deliberately reads the sentence, not a structured field: the fixture
    this predicate exists for ("move tonight's dinner reservation to 5pm")
    has no due date at all — the anchor is in the words or nowhere."""
    if not text:
        return False
    if _SAME_DAY_DEICTIC_RE.search(text):
        return True
    if _BARE_TIME_RE.search(text) and not _FUTURE_DAY_WORDS_RE.search(text):
        return True
    return False


__all__ = [
    "due_age_days",
    "is_anchored_to_day",
    "is_same_day_text",
    "parse_date",
    "render_due_clause",
    "render_due_phrase",
    "render_moved_phrase",
]
