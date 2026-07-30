#!/usr/bin/env python3
"""
Deterministic dash-as-punctuation rewrite pre-pass (SPEC DASHBAN §4).

WHY THIS EXISTS
---------------

FB-16 made dash-as-punctuation a fail-severity finding, and SPEC DASHBAN makes
that finding BLOCK on every brief kind rather than only the five outbound
`FAIL_BLOCKING_KINDS`. Enforcement alone would have been a regression, not a
fix: the v5.4.0 attended dogfood counted 847 em dashes across 69 of 82
documents produced in ONE week, so a hard block with no rewrite path turns most
of that week's output into refused saves. The scheduled surfaces are where that
bites — Past Meetings fires at 5 PM with nobody watching, and a `VoiceTellError`
there produces a failure notice instead of a brief.

So the ban ships with a way to comply. This module rewrites dash-as-punctuation
into the punctuation M's brand voice actually uses (a comma, a colon, or a
sentence break) BEFORE the gate scans, so the gate blocks only the residue the
rewriter could not resolve deterministically. Ship the two together or the
scheduled surfaces degrade (SPEC DASHBAN §4).

SINGLE SOURCE OF TRUTH
----------------------
The rewriter imports `_DASH_PUNCT_RE`, `_SIGNOFF_RE`, `_BULLET_MARKER_RE`,
`_is_quoted_line`, `_is_allowed` and `_normalize_allow` FROM the detector
rather than restating them. A pre-pass whose notion of "a dash" drifts from the
gate's notion fails in the worst possible way — silently rewriting text the
gate would have passed, or leaving text the gate still blocks. One definition,
imported, is the only version of this that stays correct.

THE LADDER (deterministic, in order, per dash occurrence)
---------------------------------------------------------
Head/tail below mean the text before/after the dash WITHIN its own sentence.

  0. PAIRED — exactly two dashes in one sentence, with non-empty text before,
     between and after them: a parenthetical. Both become commas.
       "the client — Acme Co — signed"  ->  "the client, Acme Co, signed"
  1. NUMERIC RANGE — a digit immediately either side.  ->  " to "
       "3–5 days"  ->  "3 to 5 days"
  2. RESIDUE — empty head or empty tail: nothing to join. Left in place.
  3. ENUMERATION — the tail contains a comma: the dash is introducing a list.
     ->  ": "   (a comma here would produce "changed, scope, budget, and …")
       "three things changed — scope, budget, timeline"
         ->  "three things changed: scope, budget, timeline"
  4. INDEPENDENT CLAUSE — the tail opens with a determiner/pronoun AND carries
     a finite verb, and the head has >=2 words.  ->  ". " + capitalized tail.
       "the deal slipped — it will close in Q1"
         ->  "the deal slipped. It will close in Q1"
  5. DEFAULT — a comma.
       "we shipped it fast — faster than planned"
         ->  "we shipped it fast, faster than planned"

Rule 4 is deliberately narrow. Capitalization alone is NOT used as the
independent-clause signal: "we met three people — Sam and Bo" would split
into the fragment "…people. Sam and Bo." Falling through to a comma yields
a grammatical appositive instead. The worst case of the default branch is a
comma splice, which reads as ordinary business prose; the worst case of an
eager split is a sentence fragment, which does not. When the rewriter is
unsure, it picks the branch whose failure mode is survivable.

UNRESOLVABLE RESIDUE (left in place, so the gate sees and blocks it)
--------------------------------------------------------------------
The rewriter never guesses and never deletes. These fall through untouched:
  - three or more dashes in one sentence (the pairing is ambiguous)
  - a dash with an empty head or tail (nothing to join)
  - a dash inside an inline code span or a URL — those are literals, and
    rewriting `foo - bar` inside backticks would corrupt content. Masked out
    before scanning, so their bytes are never touched.
Residue reaches the gate and blocks exactly as SPEC DASHBAN intends: the human
rewrites what a deterministic pass had no business guessing at.

WHAT IT DOES NOT TOUCH
----------------------
  - The standalone "— Sam" sign-off line (`_SIGNOFF_RE`) — deliberate brand
    voice, exempt in the detector and exempt here.
  - Quoted lines (blockquote / double-quote openers) — the counterparty's words.
  - A line covered by the client's `allow_phrases` (Voice Block Taboos).
  - A leading list marker ("- item"), masked so the bullet is not read as a
    spaced hyphen — the same carve-out `_scan_structural` makes.
  - ANYTHING when `ban_dashes` is False for the client. The caller gates the
    whole pre-pass on that flag: a client whose calibrated voice keeps dashes
    gets neither a rewrite nor a block.

SCOPE
-----
`rewrite_sections` rewrites section `body` prose ONLY — exactly the surface
`voice_tell_detector.check_sections` runs its structural scan over. Bullets,
table cells and matrix cells are out of scope for BOTH, so the invariant "the
gate blocks only what the pre-pass could not resolve" holds by construction.
Widening one without the other would break it.
"""

from __future__ import annotations

import re
import sys as _sys
from pathlib import Path as _Path
from typing import List, Optional, Sequence, Tuple

try:
    from voice_tell_detector import (
        _BULLET_MARKER_RE,
        _DASH_PUNCT_RE,
        _SIGNOFF_RE,
        _is_allowed,
        _is_quoted_line,
        _normalize_allow,
    )
except ImportError:  # pragma: no cover — direct-path import fallback
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from voice_tell_detector import (  # type: ignore
        _BULLET_MARKER_RE,
        _DASH_PUNCT_RE,
        _SIGNOFF_RE,
        _is_allowed,
        _is_quoted_line,
        _normalize_allow,
    )


# A sentence terminator plus any trailing closing quote/bracket. Used only to
# bound the head/tail VIEWS the ladder reasons over — the rewriter never splits
# and rejoins text, it splices at the dash's own span, so a mis-detected
# boundary (an abbreviation like "Inc.") can only change which branch fires,
# never lose or reorder a character.
_SENT_END_RE = re.compile(r"[.!?][\"'”’)\]]*(?:\s|$)")

# Openers that make a following clause independent rather than an appositive.
_CLAUSE_OPENERS = frozenset({
    "the", "this", "that", "these", "those", "it", "we", "they", "he", "she",
    "there", "his", "her", "our", "their", "its", "i", "you", "your", "my",
})

# Finite verbs common in brief prose. Deliberately a closed list: an unknown
# verb falls through to the comma branch, which is never a fragment.
_FINITE_VERBS = frozenset({
    "is", "are", "was", "were", "has", "have", "had", "will", "can", "could",
    "should", "would", "must", "does", "did", "do", "goes", "went", "needs",
    "need", "means", "meant", "stays", "stayed", "remains", "remained",
    "becomes", "became", "gets", "got", "sits", "sat", "runs", "ran", "opens",
    "opened", "closes", "closed", "ships", "shipped", "lands", "landed",
    "starts", "started", "ends", "ended", "moves", "moved", "leaves", "left",
    "comes", "came", "takes", "took", "makes", "made", "holds", "held",
})

# Inline code spans and bare URLs are literals, not prose. Masked before the
# scan so their bytes are never rewritten (see UNRESOLVABLE RESIDUE above).
_MASK_RE = re.compile(r"`[^`]*`|https?://\S+|www\.\S+")
_MASK_CHAR = "\x00"

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _mask(line: str) -> str:
    """Return `line` with code spans, URLs and a leading list marker replaced
    by same-length filler. Same length is load-bearing: match offsets taken
    against the masked string index the ORIGINAL string exactly."""
    out = list(line)

    def _blank(start: int, end: int) -> None:
        for i in range(start, end):
            out[i] = _MASK_CHAR

    bullet = _BULLET_MARKER_RE.match(line)
    if bullet:
        _blank(bullet.start(), bullet.end())
    for m in _MASK_RE.finditer(line):
        _blank(m.start(), m.end())
    return "".join(out)


def _sentence_view(masked: str, start: int, end: int) -> Tuple[str, str]:
    """(head, tail) — the sentence text before and after the dash at
    [start:end), bounded by the nearest sentence terminators."""
    h0 = 0
    for m in _SENT_END_RE.finditer(masked, 0, start):
        h0 = m.end()
    m = _SENT_END_RE.search(masked, end)
    t1 = m.start() if m else len(masked)
    return masked[h0:start], masked[end:t1]


def _sentence_key(masked: str, start: int) -> int:
    """Index at which the sentence containing `start` begins — the grouping key
    that puts two dashes in the same sentence into the same decision."""
    h0 = 0
    for m in _SENT_END_RE.finditer(masked, 0, start):
        h0 = m.end()
    return h0


def _words(text: str) -> List[str]:
    return _WORD_RE.findall(text)


def _decide(masked: str, start: int, end: int) -> Optional[str]:
    """The single-dash ladder. Returns the replacement token — one of
    ", " / ": " / ". " / " to " — or None for residue (leave in place)."""
    # 1. Numeric range: a digit immediately either side, no spaces involved.
    if start > 0 and end < len(masked):
        if masked[start - 1].isdigit() and masked[end].isdigit():
            return " to "

    head, tail = _sentence_view(masked, start, end)
    head_s, tail_s = head.strip(), tail.strip()

    # 2. Residue — nothing to join on one side.
    if not head_s or not tail_s:
        return None

    # 3. Enumeration — the dash introduces a comma-separated list.
    if "," in tail_s:
        return ": "

    # 4. Independent clause — narrow, confident signal only.
    tail_words = [w.lower() for w in _words(tail_s)]
    head_words = _words(head_s)
    if (
        len(head_words) >= 2
        and len(tail_words) >= 3
        and tail_words[0] in _CLAUSE_OPENERS
        and any(w in _FINITE_VERBS for w in tail_words[1:])
    ):
        return ". "

    # 5. Default.
    return ", "


def _expand_ws(line: str, start: int, end: int) -> Tuple[int, int]:
    """Widen a dash span to swallow the whitespace either side, so `a — b`,
    `a—b` and `a - b` all normalize to a single replacement token."""
    while start > 0 and line[start - 1].isspace():
        start -= 1
    while end < len(line) and line[end].isspace():
        end += 1
    return start, end


def _capitalize_first(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
        if not ch.isspace():
            return text
    return text


def rewrite_line(line: str) -> Tuple[str, int, int]:
    """Rewrite dash-as-punctuation in one line of body prose.

    Returns (new_line, n_rewritten, n_residue). Callers are responsible for the
    line-level exemptions (sign-off, quoted, allow_phrases) — see
    `rewrite_text`, which applies them."""
    masked = _mask(line)
    matches = list(_DASH_PUNCT_RE.finditer(masked))
    if not matches:
        return line, 0, 0

    # Group by sentence so a parenthetical pair is decided as a pair.
    groups: dict = {}
    for m in matches:
        groups.setdefault(_sentence_key(masked, m.start()), []).append(m)

    # (start, end, replacement, is_split) for every dash we will rewrite.
    plan: List[Tuple[int, int, str, bool]] = []
    residue = 0

    for _key, group in groups.items():
        if len(group) == 2:
            m1, m2 = group
            head, _ = _sentence_view(masked, m1.start(), m1.end())
            mid = masked[m1.end():m2.start()]
            _, tail = _sentence_view(masked, m2.start(), m2.end())
            if head.strip() and mid.strip() and tail.strip():
                # Parenthetical — both sides become commas.
                plan.append((m1.start(), m1.end(), ", ", False))
                plan.append((m2.start(), m2.end(), ", ", False))
                continue
        if len(group) > 2:
            # Ambiguous pairing — the rewriter declines the whole sentence.
            residue += len(group)
            continue
        for m in group:
            rep = _decide(masked, m.start(), m.end())
            if rep is None:
                residue += 1
            else:
                plan.append((m.start(), m.end(), rep, rep == ". "))

    if not plan:
        return line, 0, residue

    plan.sort(key=lambda t: t[0])
    out: List[str] = []
    cursor = 0
    capitalize_next = False
    for start, end, rep, is_split in plan:
        span_start, span_end = _expand_ws(line, start, end)
        if span_start < cursor:  # overlapping expansion — skip defensively
            continue
        chunk = line[cursor:span_start]
        if capitalize_next:
            chunk = _capitalize_first(chunk)
            capitalize_next = False
        out.append(chunk)
        out.append(rep)
        capitalize_next = is_split
        cursor = span_end
    tail_chunk = line[cursor:]
    if capitalize_next:
        tail_chunk = _capitalize_first(tail_chunk)
    out.append(tail_chunk)
    return "".join(out), len(plan), residue


def rewrite_text(
    text: str,
    *,
    allow_phrases: Optional[Sequence[str]] = None,
    skip_quoted: bool = True,
) -> Tuple[str, int, int]:
    """Rewrite dash-as-punctuation across a body string.

    Applies the same line-level exemptions `_scan_structural` applies, so the
    rewriter and the gate agree line for line: the standalone sign-off, quoted
    lines, and any line covered by the client's `allow_phrases`.

    Returns (new_text, n_rewritten, n_residue)."""
    if not text:
        return text, 0, 0
    allow_norm = _normalize_allow(allow_phrases)
    lines = text.split("\n")
    fixed = res = 0
    for i, line in enumerate(lines):
        if skip_quoted and _is_quoted_line(line):
            continue
        if _SIGNOFF_RE.match(line):
            continue
        if allow_norm and _is_allowed(line, allow_norm):
            continue
        new_line, n_fixed, n_res = rewrite_line(line)
        if n_fixed:
            lines[i] = new_line
        fixed += n_fixed
        res += n_res
    return "\n".join(lines), fixed, res


def rewrite_sections(
    sections: Sequence[dict],
    *,
    allow_phrases: Optional[Sequence[str]] = None,
) -> Tuple[int, int]:
    """MUTATES `sections` IN PLACE, rewriting each section's `body` prose.

    In-place is deliberate and is the whole point: both deliverable backends
    (`brief_writer.make_brief`, `premium_html.make_premium_brief`) hand the same
    `sections` list to `brief_gates.run_pre_save_gates` and then RENDER FROM
    THAT SAME OBJECT. Mutating here is what puts the clean prose in the saved
    document; returning a copy would clean only the text the gate scans and
    ship the dashes anyway. `run_pre_save_gates` keeps its signature and its
    return contract, so callers (including the workspace-side PDF rail that
    inherits this gate) need no change.

    `body` only — see SCOPE in the module docstring.

    Returns (n_rewritten, n_residue)."""
    fixed = res = 0
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        body = sec.get("body")
        if not isinstance(body, str) or not body:
            continue
        new_body, n_fixed, n_res = rewrite_text(body, allow_phrases=allow_phrases)
        if n_fixed:
            sec["body"] = new_body
        fixed += n_fixed
        res += n_res
    return fixed, res


__all__ = ["rewrite_line", "rewrite_text", "rewrite_sections"]
