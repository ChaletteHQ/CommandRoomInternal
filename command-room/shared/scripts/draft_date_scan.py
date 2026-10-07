#!/usr/bin/env python3
"""draft_date_scan — DRAFTDATE1: a draft never states a date the row does
not hold.

THE REGRESSION. ATTENDED_TEST_v5.29.0, 2026-09-07: the My Plate fire drafted
"I'll have it finished by Friday" and "this is on your calendar today" for
rows carrying NO due date and NO calendar event, and the Inbox pass drafted
"let's get this paid this week" the same way. Nobody had promised any of
those days. A draft the CEO sends on that basis makes a commitment the book
does not know about — and the next time the product asks "did you do it by
Friday?", Friday is its own invention.

WHERE IT CAME FROM. Not the model freelancing: the My Plate orchestrator's
own instruction said the status draft carries a "new ETA (default this
Friday EOD)". The instruction is removed in the same change as this module.
But drafts are MODEL-COMPOSED on every surface — email-writer, the nudge and
follow-up paths, My Plate, Inbox — so the fence has to be a scan of the
rendered draft, the way `validate_chat_output` is a scan of rendered chat.

THE RULE. Every date, day or deadline phrase in a draft must TRACE to a date
the row actually holds:
  1. the row's due date;
  2. a calendar event on the row (`calendar_dates`);
  3. a date stated in the row's OWN source text — its title, the quote it
     was captured from, its evidence line, the thread subject.
A phrase that resolves to no date at all ("soon", "shortly", "when I can")
is not a date claim and is not scanned. A phrase that resolves to a date the
row does not hold is refused, by phrase, in plain words.

WHAT IT IS NOT. It does not rewrite the draft, and it never invents a date
to substitute — the fix is always to drop the promise or to give the row a
real date first. It is deliberately generous on the resolution side (a
weekday matches its next OR its following occurrence) so the finding is
always "the row holds no such date", never "we disagree about which Friday".

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# The phrases
# ---------------------------------------------------------------------------

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
             "thurs": 3, "fri": 4, "sat": 5, "sun": 6}

_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
           "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
           "november": 11, "december": 12,
           "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
           "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}

_WD = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_MO = "|".join(sorted(_MONTHS, key=len, reverse=True))

# REVIEW_LEAK2 F-4. `_WD` carries the ABBREVIATIONS, and the bare catch-all
# used it, so four ordinary English words were read as day names:
#   "we sat down with the team" · "best deal under the sun" ·
#   "dinner at Mon Ami Gabi" · "they wed in June"
# `assert_draft_dates` then refused the draft, and the skill instruction says
# NEVER catch that error and send anyway — so an ordinary sentence blocked a
# draft with a reason that reads as nonsense to the CEO.
#
# The split: the bare catch-all reads FULL weekday names only. The
# abbreviations still earn their place wherever the sentence gives them a
# date context — after a deadline preposition (`by Fri`, `on Wed`, `this
# Thurs`), which the prepositional pattern already covers, or in front of a
# day number (`Fri 12`, `Wed the 3rd`).
#
# Capitalisation alone was considered as the context test and REJECTED:
# "Mon Ami Gabi" and "Sun Microsystems" are capitalised and would still red.
_WEEKDAY_FULL_NAMES = ("monday", "tuesday", "wednesday", "thursday",
                       "friday", "saturday", "sunday")
_WD_FULL = "|".join(sorted(_WEEKDAY_FULL_NAMES, key=len, reverse=True))
_WD_ABBR_CAP = "|".join(sorted(
    (k.title() for k in _WEEKDAYS if k not in _WEEKDAY_FULL_NAMES),
    key=len, reverse=True))

# Each entry: (compiled pattern, kind). The pattern's whole match is the
# phrase reported back to the composer, so it reads as the words in the draft.
DATE_PHRASE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "iso"),
    (re.compile(rf"\b(?:{_MO})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.I), "monthday"),
    (re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MO})\b", re.I), "daymonth"),
    # A slash date. REVIEW_LEAK2 F-4: a BARE `n/m` is a fraction at least as
    # often as it is a date — "3/4 of the fee is due on signature" was read
    # as March 4th and refused. A year makes it unambiguous; without one the
    # sentence has to give it a date context. The cost, named honestly: a
    # bare "9/11" in a draft is no longer a date claim — one such row exists
    # on the 2026-09-07 book snapshot, and it is a row TITLE, not a draft.
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "slashdate"),
    (re.compile(r"\b(?:by|on|before|due(?:\s+on)?|no\s+later\s+than|"
                r"scheduled\s+for|the\s+week\s+of|starting|from)\s+"
                r"\d{1,2}/\d{1,2}\b", re.I), "slashdate"),
    (re.compile(r"\bthe\s+day\s+after\s+tomorrow\b", re.I), "dayafter"),
    (re.compile(r"\btomorrow\b", re.I), "tomorrow"),
    (re.compile(r"\btoday\b", re.I), "today"),
    (re.compile(r"\btonight\b", re.I), "today"),
    (re.compile(r"\bend\s+of\s+(?:the\s+)?day\b|\bEOD\b", re.I), "today"),
    (re.compile(r"\bin\s+(\d{1,3})\s+(?:business\s+)?days?\b", re.I), "in_days"),
    (re.compile(r"\bin\s+(?:a|one)\s+week\b", re.I), "in_week"),
    (re.compile(r"\bnext\s+week\b", re.I), "next_week"),
    (re.compile(r"\b(?:this|the\s+rest\s+of\s+the)\s+week\b", re.I), "this_week"),
    (re.compile(r"\bend\s+of\s+(?:the\s+)?week\b|\bEOW\b", re.I), "this_week"),
    (re.compile(r"\bby\s+the\s+end\s+of\s+(?:the\s+)?month\b|\bthis\s+month\b", re.I),
     "this_month"),
    (re.compile(rf"\b(?:by|on|before|this|next|come)\s+(?:{_WD})\b", re.I), "weekday"),
    # A CAPITALISED abbreviation in front of a day number is a date claim on
    # its own ("Fri 12", "Wed the 3rd") — the other place the abbreviations
    # still earn their keep. Case-sensitive on purpose: lower-case "we sat
    # 12 people at the table" is a verb and a headcount, not a Saturday.
    (re.compile(rf"\b(?:{_WD_ABBR_CAP})\.?\s+(?:the\s+)?"
                r"\d{1,2}(?:st|nd|rd|th)?\b"), "weekday"),
    # REVIEW_LEAK2 round-2 R-5 — SENTENCE-FINAL is a date context too.
    # Round 1 took the abbreviations out of the bare catch-all, which was
    # right for "we sat down with the team" but went one step too far:
    # "It lands Thurs." and "I'll ship it Fri." are promises in ordinary
    # business English and stopped being claims. A weekday abbreviation
    # at the END of a sentence has nowhere else to point. CAPITALISED
    # only, like the day-number pattern above: "best deal under the sun."
    # stays ordinary English, and a mid-sentence "Mon Ami Gabi" is
    # untouched because it is not sentence-final.
    (re.compile(rf"\b(?:{_WD_ABBR_CAP})(?=\.?\s*(?:[.!?)\"']|$))"), "weekday"),
    # …and the same for a bare slash date. Round 1 required a year or a
    # preposition, so "Kickoff is 9/11." said nothing. Sentence-final is
    # the context it was missing. NAMED COST: a sentence-final fraction
    # ("the split is 3/4.") now reads as a date — mid-sentence fractions,
    # which is how the round-1 regression actually read ("3/4 of the fee
    # is due on signature"), are untouched.
    (re.compile(r"\b\d{1,2}/\d{1,2}(?=\s*(?:[.!?)\"']|$))"), "slashdate"),
    # The bare catch-all reads FULL weekday names only (F-4).
    (re.compile(rf"\b(?:{_WD_FULL})\b", re.I), "weekday"),
]

# The words that turn a phrase into a PROMISE rather than a mention. Kept for
# the caller's own reporting; the scan itself refuses any unheld date phrase,
# because a draft that merely mentions a day the row does not hold is the same
# invention ("this is on your calendar today").
DEADLINE_MARKERS = ("by ", "before ", "deadline", "due ", "no later than",
                    "finished", "delivered", "sent by", "paid")


class DraftDateError(ValueError):
    """A rendered draft states a date, day or deadline the row does not hold.

    The fix is the DRAFT: drop the promise, or give the row a real date
    first. Never widen this scan, and never let the draft invent a date so
    the sentence reads better.
    """


# ---------------------------------------------------------------------------
# What the row holds
# ---------------------------------------------------------------------------

_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_SOURCE_TEXT_KEYS = ("title", "summary", "evidence", "quote", "source_quote",
                     "review_reason", "thread_subject", "subject", "proof",
                     "context_tag", "body")


def _as_date(value) -> Optional[_dt.date]:
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.datetime):
        return value.date()
    m = _ISO_RE.search(str(value or ""))
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def source_text(row) -> str:
    """The row's OWN words — where a date the customer themselves stated is
    allowed to come from."""
    if not isinstance(row, dict):
        return ""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    parts: list[str] = []
    for key in _SOURCE_TEXT_KEYS:
        for src in (row, data):
            v = src.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v)
    return " \n".join(parts)


def held_dates(row) -> set:
    """Every date the row actually holds: its due date, any calendar event on
    it, and any date written into its own source text."""
    if not isinstance(row, dict):
        return set()
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    out: set = set()
    for key in ("due", "due_date", "new_due", "prior_due"):
        for src in (row, data):
            d = _as_date(src.get(key))
            if d:
                out.add(d)
    for key in ("calendar_dates", "calendar_event_dates", "event_dates"):
        for src in (row, data):
            v = src.get(key)
            if isinstance(v, (list, tuple, set)):
                for item in v:
                    d = _as_date(item)
                    if d:
                        out.add(d)
    for m in _ISO_RE.finditer(source_text(row)):
        d = _as_date(m.group(0))
        if d:
            out.add(d)
    return out


# ---------------------------------------------------------------------------
# Resolving a phrase
# ---------------------------------------------------------------------------

def _week_range(day: _dt.date) -> tuple:
    start = day - _dt.timedelta(days=day.weekday())
    return start, start + _dt.timedelta(days=6)


def resolve_phrase(phrase: str, kind: str, today: _dt.date) -> Optional[list]:
    """The date, or the inclusive (first, last) window, a phrase names —
    None when the phrase names no date at all (and so is not a claim).

    Returns a list of candidate (first, last) windows: a weekday resolves to
    BOTH its next and its following occurrence, on purpose (see the module
    docstring — the finding must never be a disagreement about which Friday).
    """
    p = phrase.strip().lower()
    if kind == "today":
        return [(today, today)]
    if kind == "tomorrow":
        d = today + _dt.timedelta(days=1)
        return [(d, d)]
    if kind == "dayafter":
        d = today + _dt.timedelta(days=2)
        return [(d, d)]
    if kind == "in_days":
        m = re.search(r"(\d{1,3})", p)
        if not m:
            return None
        d = today + _dt.timedelta(days=int(m.group(1)))
        return [(d, d)]
    if kind == "in_week":
        d = today + _dt.timedelta(days=7)
        return [(d, d)]
    if kind == "this_week":
        start, end = _week_range(today)
        return [(max(start, today), end)]
    if kind == "next_week":
        start, end = _week_range(today + _dt.timedelta(days=7))
        return [(start, end)]
    if kind == "this_month":
        first = today.replace(day=1)
        nxt = (first + _dt.timedelta(days=32)).replace(day=1)
        return [(today, nxt - _dt.timedelta(days=1))]
    if kind == "iso":
        d = _as_date(p)
        return [(d, d)] if d else None
    if kind in ("monthday", "daymonth"):
        mo = next((v for k, v in _MONTHS.items()
                   if re.search(rf"\b{k}\b", p)), None)
        dm = re.search(r"\b(\d{1,2})\b", p)
        if not mo or not dm:
            return None
        day = int(dm.group(1))
        out = []
        for year in (today.year, today.year + 1):
            try:
                out.append((_dt.date(year, mo, day),) * 2)
            except ValueError:
                continue
        return out or None
    if kind == "slashdate":
        nums = [int(n) for n in re.findall(r"\d+", p)]
        if len(nums) < 2:
            return None
        mo, day = nums[0], nums[1]
        year = today.year
        if len(nums) > 2:
            year = nums[2] if nums[2] > 999 else 2000 + nums[2]
        try:
            d = _dt.date(year, mo, day)
        except ValueError:
            return None
        return [(d, d)]
    if kind == "weekday":
        wd = next((v for k, v in _WEEKDAYS.items()
                   if re.search(rf"\b{k}\b", p)), None)
        if wd is None:
            return None
        ahead = (wd - today.weekday()) % 7
        first = today + _dt.timedelta(days=ahead)
        return [(first, first),
                (first + _dt.timedelta(days=7),) * 2]
    return None


def _window_holds(windows, dates: Iterable) -> bool:
    for first, last in windows:
        for d in dates:
            if first <= d <= last:
                return True
    return False


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

def scan_draft(draft_text, row, *, today) -> list:
    """Every date phrase in `draft_text` the row does not hold.

    Returns `[{phrase, kind, why}]` — empty means clean. `today` is the
    workspace's own day (a date or an ISO string); a caller must pass the
    same day the surface is rendering for, never a UTC re-slice.
    """
    text = str(draft_text or "")
    if not text.strip():
        return []
    day = _as_date(today)
    if day is None:
        raise DraftDateError("the draft scan needs the workspace's own day; "
                             f"got {today!r}")
    dates = held_dates(row)
    own_words = source_text(row).lower()
    findings: list = []
    seen: set = set()
    covered: list = []          # spans already reported by an earlier pattern
    for pat, kind in DATE_PHRASE_PATTERNS:
        for m in pat.finditer(text):
            if any(m.start() < end and start < m.end()
                   for start, end in covered):
                continue
            phrase = m.group(0)
            windows = resolve_phrase(phrase, kind, day)
            if windows is None:
                continue
            covered.append((m.start(), m.end()))
            key = phrase.lower()
            if key in seen:
                continue
            if _window_holds(windows, dates):
                continue
            # The customer's OWN words are allowed to carry the day.
            if key in own_words:
                continue
            seen.add(key)
            findings.append({
                "phrase": phrase,
                "kind": kind,
                "why": ("the row holds no such date — no due date, no "
                        "calendar event, and nothing in its own words says "
                        "it" if not dates else
                        "the row's dates are not this one"),
            })
    return findings


def assert_draft_dates(draft_text, row, *, today, where: str = "a draft") -> None:
    """Raise `DraftDateError` when a draft states a date the row does not
    hold. The MANDATORY post step on every draft-composing surface —
    email-writer, the nudge and follow-up paths, My Plate, Inbox. NEVER catch
    the error and send anyway: fix the draft, or give the row a real date."""
    findings = scan_draft(draft_text, row, today=today)
    if not findings:
        return
    lines = [f"{where} states a date the row does not hold — refusing to send:"]
    for f in findings:
        lines.append(f"  - {f['phrase']!r}: {f['why']}")
    lines.append("Drop the day from the draft, or set a real date on the row "
                 "first. Never invent one so the sentence reads better.")
    raise DraftDateError("\n".join(lines))


__all__ = [
    "DATE_PHRASE_PATTERNS",
    "DraftDateError",
    "assert_draft_dates",
    "held_dates",
    "resolve_phrase",
    "scan_draft",
    "source_text",
]
