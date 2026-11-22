#!/usr/bin/env python3
"""Clean titles: one display rule for every title Command Room shows
(cr1#1591, the Hub declutter, plan section 6).

The owner's words (2026-11-21): "want titles and headers of items to be
clean". A mail subject arrives as "Re: Fwd: [EXT] Sample pricing question",
a meeting summary carries an internal id ("Sample sync 2 on project_001"), a
commitment is captured as a quote with its full stop. This module is the
Python twin of the Hub page's `showTitle(t, opts)`; both run the one case
table in `tests/data/title_display_cases.json` (TITLES1,
`tests/run_hub_title_display_test.py`), so the two cannot drift.

DISPLAY ONLY. The stored title, every edit box and everything sent back to
the book keep the words as written; a feed keeps the raw `title`. Use this
only where Python composes a sentence that shows a title (the morning
message's day sentence and its list).

Which steps run depends on the kind (`KINDS`, review round 1): mail and
calendar titles come from other people's systems and lose reply markers and
shouting; the owner's own words (to-dos, reminders, commitments, priorities,
thread and work-stream names) keep theirs; a generated sub-line keeps its
case. The steps, in order:

  1. Spaces collapse to one (every kind); a title wrapped in one pair of
     quotes loses them (not a sub-line).
  2. Internal ids go (every kind), with a dangling "on", "for", "re" (and the
     like) before them, leaving a space, never glued words or doubled
     punctuation: only shapes `surface_leak_patterns.carries_surface_id`
     already calls an id (project_001, person_002, cmt_..., bp_...).
  3. Mail and meetings: leading reply and forward markers go, repeated: Re:,
     Fwd:, Fw:, AW:, SV: (any case) followed by a space or the end ("re:Invent"
     stays), and the tags [EXT], [External], (External), EXTERNAL:.
  4. Mail: a name already on the row goes from the front ("<name>: ",
     "<name> - ") and a trailing "(from <name>)" (the sender, `drop`).
  5. A sub-line: a generated age after a dash reads with a comma ("due Nov 17,
     3 days ago").
  6. Trailing . , ; : go (every kind); ?, !, closing brackets, an ellipsis and
     a dotted abbreviation ("a.m.") stay.
  7. Mail and meetings: a title in capitals (two or more words of three or
     more letters, 80 percent of its letters upper) is written in sentence
     case, keeping words with a digit or "&" (Q4, AT&T), the acronyms in
     `ACRONYMS` and their plurals (KPIs), the pronoun I, and the words of a
     name on the row as that name writes them.
  8. The first letter is upper (as the page's `cap()`), unless the first word
     is written like "iPhone" or "re:Invent"; a sub-line keeps its case.
  9. Nothing left: the kind's empty words ("No subject", "Meeting", "" for a
     sub-line, "Untitled"); `empty` overrides.

Length is never cut here: rows show one line with an ellipsis by CSS.

Pure: reads nothing, writes nothing, imports nothing from the plugin.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

#: Every space JavaScript's `\s` knows, so the page and this twin collapse the
#: same characters (Python's own `\s` differs at the edges).
_SPACE_RE = re.compile("[ \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+")
_QUOTES = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019", "\u00ab": "\u00bb"}
#: A marker needs a space (or the end) after its colon, so "re:Invent" stays.
_PREFIX_RE = re.compile(
    r"(?:(?:re|fwd|fw|aw|sv)\s*:(?=\s|$)\s*|\[\s*(?:ext|external)\s*\]\s*"
    r"|\(\s*(?:ext|external)\s*\)\s*|external\s*:(?=\s|$)\s*)",
    re.IGNORECASE | re.ASCII)
#: The id shapes step 2 takes out: a subset of what
#: `surface_leak_patterns.carries_surface_id` refuses (TITLES1 [3] checks it).
ID_SRC = (r"(?:cmt|seq|person|project|org|deal)_\d+|(?:thread|event|matter|engagement)_\d{3,}"
          r"|cmt_[0-9a-z]{6,}|bp_[0-9a-f]{6,}|commitment_seq_\d+")
_ID_RE = re.compile(
    r"(?:\s+(?:on|for|re|in|about|with|from|to|at|of)\b\s*:?)?\s*[(\[]?\b(?:" + ID_SRC + r")\b[)\]]?",
    re.IGNORECASE | re.ASCII)
_ID_LEAD_RE = re.compile("^[ :;,\u00b7|\u2014\u2013-]+")
_ID_TAIL_RE = re.compile("[ ;,\u00b7|\u2014\u2013-]+\\Z")
_DROP_SEP_RE = re.compile("\\s*(?::|[\u2014\u2013|]|-(?=\\s))\\s*", re.ASCII)
_DROP_FROM_RE = re.compile(r"\s*\(\s*from\s+([^()]+?)\s*\)\Z", re.IGNORECASE | re.ASCII)
_AGE_RE = re.compile("\\s+[\u2014\u2013-]\\s+(\\d+ days? ago)\\b", re.ASCII)
_TAIL_SOFT_RE = re.compile(r"[ ,;:]+\Z")
_TAIL_RE = re.compile(r"[ .,;:]+\Z")
_KEEP_DOTS_RE = re.compile("(?:\\.\\.\\.|\u2026)\\Z|(?:^| )(?:[A-Za-z]\\.){2,}\\Z", re.ASCII)
_CAPS_WORD_RE = re.compile("([^A-Za-z]*)([A-Za-z]+)(['\u2019][A-Za-z]+)?([^A-Za-z]*)", re.ASCII)
_PRONOUN_RE = re.compile("i(?:['\u2019]|\\Z)")
_CAMEL_RE = re.compile(r"[a-z]\S*[A-Z]", re.ASCII)
_NAME_WORD_RE = re.compile(r"[A-Za-z]+", re.ASCII)

#: Short words that stay in capitals when a shouted title is written in
#: sentence case (and their plurals: KPIS reads KPIs). Words with a digit or
#: "&" (Q4, AT&T) stay without being listed. The page's `TITLE_ACRONYMS`
#: holds the same words (TITLES1 [3]).
ACRONYMS = frozenset((
    "AI API AP AR ASAP CEO CFO CIO CMO COO CPA CR CRM CSV CTO DM EO EOD EOM EOS EOW ERP ESOP ETA EU "
    "FAQ FYI GDPR GM HQ HR HVAC IBM ID IPO IRS KPI LLC LLP LOI MBA MOU MSA MSP NDA OK OKR PDF PO PR PTO "
    "QA QBR RFI RFP RFQ ROI SEO SLA SMS SOC SOP SOW SVP TBD UAE UK URL USA USC VAT VC VIP VP").split())

#: Which steps each kind takes, and its words when nothing is left (review
#: round 1). The page's `TITLE_KINDS` is the same table (TITLES1 [3]). Every
#: kind loses extra spaces, internal ids and a trailing full stop.
KINDS: Dict[str, Dict[str, Any]] = {
    "mail": {"quotes": 1, "markers": 1, "drop": 1, "shout": 1, "age": 0, "capital": 1, "empty": "No subject"},
    "meeting": {"quotes": 1, "markers": 1, "drop": 0, "shout": 1, "age": 0, "capital": 1, "empty": "Meeting"},
    "item": {"quotes": 1, "markers": 0, "drop": 0, "shout": 0, "age": 0, "capital": 1, "empty": "Untitled"},
    "line": {"quotes": 0, "markers": 0, "drop": 0, "shout": 0, "age": 1, "capital": 0, "empty": ""},
}


def _unquote(s: str) -> str:
    close = _QUOTES.get(s[:1])
    if close and len(s) > 1 and s[-1] == close:
        inner = s[1:-1]
        if s[0] not in inner and close not in inner:
            return inner.strip(" ")
    return s


def _letters(s: str, upper: bool = False) -> int:
    return sum(1 for c in s if ("A" <= c <= "Z") or (not upper and "a" <= c <= "z"))


def _caps_word(w: str, names: Dict[str, str]) -> str:
    if re.search(r"[0-9&]", w):
        return w
    m = _CAPS_WORD_RE.fullmatch(w)
    if m:
        core, tail = m.group(2), (m.group(3) or "").lower()
        if core.lower() in names:
            return m.group(1) + names[core.lower()] + tail + m.group(4)
        if core in ACRONYMS:
            return m.group(1) + core + tail + m.group(4)
        if len(core) > 3 and core.endswith("S") and core[:-1] in ACRONYMS:
            return m.group(1) + core[:-1] + "s" + tail + m.group(4)
    low = w.lower()
    return "I" + low[1:] if _PRONOUN_RE.match(low) else low


def _sentence_case(s: str, names: Dict[str, str]) -> str:
    words = s.split(" ")
    letters = _letters(s)
    long_words = sum(1 for w in words if _letters(w) >= 3)
    if long_words < 2 or not letters or _letters(s, upper=True) * 5 < letters * 4:
        return s
    return " ".join(_caps_word(w, names) for w in words)


def _tidy_after_id(s: str) -> str:
    """Where an id came out: one space, no space before punctuation, no
    doubled punctuation, a space after a comma, no separator at either end."""
    s = re.sub(r" {2,}", " ", s).strip(" ")
    s = re.sub(r" +([,;:.!?)\]])", r"\1", s)
    s = re.sub(r"([,;:])(?: *[,;:])+", r"\1", s)
    s = re.sub(r"([,;])(?=[A-Za-z])", r"\1 ", s)
    s = re.sub(r"([(\[]) +", r"\1", s)
    return _ID_TAIL_RE.sub("", _ID_LEAD_RE.sub("", s))


def display_title(text: Any, kind: str = "item", drop: Iterable[Any] = (),
                  empty: Optional[str] = None) -> str:
    """The title as a row shows it. `kind` is "mail", "meeting", "line" (a
    generated sub-line) or anything else for the owner's own item (`KINDS`);
    `drop` holds names already on the row (used for mail); `empty` replaces
    the kind's words for nothing left. The page's `showTitle`."""
    k = KINDS.get(kind) if isinstance(kind, str) else None
    k = k or KINDS["item"]
    s = _SPACE_RE.sub(" ", "" if text is None else str(text)).strip(" ")
    names = ([n for n in (_SPACE_RE.sub(" ", str(x or "")).strip(" ") for x in (drop or ())) if n]
             if k["drop"] else [])
    hit = []

    def _gone(m):
        hit.append(1)
        return " "

    s = _ID_RE.sub(_gone, s)
    if hit:
        s = _tidy_after_id(s)
    while True:
        prev = s
        if k["quotes"]:
            s = _unquote(s)
        if k["markers"]:
            pre = _PREFIX_RE.match(s)
            if pre:
                s = s[pre.end():]
        for n in names:
            if s[:len(n)].lower() == n.lower():
                sep = _DROP_SEP_RE.match(s[len(n):])
                rest = s[len(n) + sep.end():] if sep else ""
                if sep and rest:
                    s = rest
            m = _DROP_FROM_RE.search(s)
            if m and m.start() > 0 and _SPACE_RE.sub(" ", m.group(1)).lower() == n.lower():
                s = s[:m.start()]
        if s == prev:
            break
    if k["age"]:
        s = _AGE_RE.sub(r", \1", s)
    s = _TAIL_SOFT_RE.sub("", s)
    if not _KEEP_DOTS_RE.search(s):
        s = _TAIL_RE.sub("", s)
    if k["shout"]:
        known: Dict[str, str] = {}
        for n in (drop or ()) if k["drop"] else ():
            for w in _SPACE_RE.split(str(n or "")):
                if _NAME_WORD_RE.fullmatch(w):
                    known[w.lower()] = w
        s = _sentence_case(s, known)
    if s and k["capital"] and not _CAMEL_RE.match(s):
        s = s[0].upper() + s[1:]
    if not s:
        s = str(empty) if empty is not None else k["empty"]
    return s


__all__ = ["ACRONYMS", "ID_SRC", "KINDS", "display_title"]
