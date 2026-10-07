#!/usr/bin/env python3
"""One-command surface drivers (T2.2 scope 1e — kill the ~30-command prep).

WHY THIS EXISTS
The RV round measured a manual `commitments` fire spending ~30 shell/python
round-trips assembling the data view before the widget rendered (load →
project → count → escalate → sort → annotate → build → fit → persist), with
the double-render nit (RV-3) riding on the re-runs. Every one of those steps
is deterministic — so this module does ALL of them in ONE CLI invocation per
surface and prints exactly what the runtime needs to relay:

    python3 shared/scripts/surface_drivers.py commitments \
        --workspace <WORKSPACE> [--page N] [--page-size 15]
    python3 shared/scripts/surface_drivers.py staff-meeting \
        --workspace <WORKSPACE> [--page N] [--moves-json <file>] \
        [--fired-via scheduled|manual|catchup]
    python3 shared/scripts/surface_drivers.py waiting-on \
        --workspace <WORKSPACE> [--page N] [--chase-json <file>] \
        [--fired-via scheduled|manual|catchup]
    python3 shared/scripts/surface_drivers.py my-plate \
        --workspace <WORKSPACE> [--page N] [--status-json <file>] \
        [--personal-cap N] [--fired-via scheduled|manual|catchup]
    python3 shared/scripts/surface_drivers.py commitments \
        --workspace <WORKSPACE> --format artifact

ARTIFACT MODE (SPEC_BOARD1): `--format artifact` on the commitments surface
serializes the SAME view as the full-set triage board — one self-contained
page, no pagination, its own CR-BOARD / CR-BOARD-HTML-* markers so nobody
relays a board into show_widget or an interactive widget page into the
Artifact tool. It renders and validates; it never writes a receipt and never
freezes a page-set.

STDOUT SHAPE (fixed contract — the skill texts pin it):

    CR-PAGINATION: {"page": 1, "total_pages": 3, ...}
    CR-WIDGET-HTML-BEGIN
    <the persisted page's validated bytes, verbatim>
    CR-WIDGET-HTML-END
    CR-RECEIPT: {"task_id": ..., "status": "written"}   (only with --fired-via)

FIRE RECEIPTS (FB-7): `--fired-via <run mode>` makes the page-1 invocation
ALSO append the surface's canonical per-fire receipt (receipts.log_receipt)
inside this same call — the 2026-07-16 live staff-meeting scheduled fire
rendered its widget but never reached the prose receipt step that came after
the widget post, so the render and the receipt are now one invocation that
no orchestrator path can split. Pages 2+ (`show more`) never receipt.

The runtime relays the bytes BETWEEN the BEGIN/END markers to
`mcp__visualize__show_widget` as `widget_code`, byte-exact, and reads the
pagination line for the position/`show more` narration. Nothing else needs
running: `render_and_persist` (validators + byte-fit + persist + audit file)
already ran inside this call.

IDEMPOTENT-SINGLE-CALL (RV-3 double-render): one driver invocation per page
per fire. The persisted audit file is written once per invocation; re-running
the driver "to refresh" writes a second audit file and is exactly the
double-render defect. If the transport output for the requested page is
already in hand, relay it — never re-run.

Views are built from the CANONICAL projectors only (cru_match /
commitment_state / confirm_flow / brain_proposals) — the driver adds no
judgment, no filtering, no re-derivation. Read-only against the substrate
except for the transport's own persist into `_hq/.system/widgets/`.

stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The commitment-triage row verb sets (SKILL.md Step 3 — verbatim).
_PROMISE_VERBS = ["resolved", "push to [date]", "drop", "not mine",
                  "make task", "never track this", "skip"]
_TASK_VERBS = ["resolved", "push to [date]", "drop", "promote",
               "never track this", "skip"]
# FB-20 — how many money-class items the brief names in prose before it
# leaves the rest to the pointer's count. A render bound, never a silence:
# every capped item is still inside `queue_pointer["count"]`, and money is
# the only class that gets named at all.
MONEY_PROSE_CAP = 3


# ---------------------------------------------------------------------------
# CUT-PLATE (2026-09-06) — THE NUMBER LEADS THE BRIEF.
#
# The v5.28.0 attended test (B2.1 / A4) saw the morning brief open with
# "1 duplicate entry number(s) in your activity log", then a paragraph, then
# CHANGED / DECIDE / NEEDED, and only THEN "170 on your plate today" — with
# four or five other counts competing around it. M's standing rule ("we want
# to show less options to clients — they are overwhelmed") and PLATE1-N2 R-1
# / NUMBERS1 say the opposite: the FIRST line of the brief is the attention
# number, then the top moves, then ONE pointer; every other count either
# folds into that pointer or sits below the fold; the duplicate-entry
# warning and any health line go to the END, never above the number.
#
# This is made MECHANICAL where the driver composes the pack:
#   pack["lead"]          {lines, text} — plate.line FIRST, then the plate
#                         rows, then the one pointer. The orchestrator prints
#                         it verbatim as the first content block.
#   pack["fold_lines"]    the count lines that sit BELOW the fold, in order:
#                         the resting line, the queue pointer.
#   pack["health_lines"]  alarm_lines + watchdog + dark-surface + schedule-
#                         refresh lines — the END of the brief, verbatim.
# ...and `assert_number_leads` is the fence: it scans the composed order
# (lead, fold, health) and RAISES `BriefOrderError` when any count-shaped
# sentence precedes the number line. The same scanner is run over the
# rendered templates by `tests/run_cutplate_test.py`, so the prose the
# model follows cannot drift back either.
# ---------------------------------------------------------------------------

#: The plate's own number line — the ONE line that may lead. `[N]` is the
#: template's placeholder for the number, accepted so the same scanner reads
#: the SKILL.md template and a live render alike.
NUMBER_LINE_RE = re.compile(
    r"^\s*(?:\[N\]|N|\d+)\s+on your plate today\s*$"
    r"|^\s*Nothing is waiting on you today\.\s*$")

#: Count-shaped sentences — a number (or the template's `[N]` / `[X]` / `[Y]`
#: placeholder) counting workspace items. Named so a red says WHICH shape.
_NUM = r"(?:\[[NXY]\]|\d+)"
COUNT_SHAPES = (
    ("things-need-your-eyes", re.compile(
        rf"{_NUM}\s+(?:things?|items?)\s+need", re.I)),
    ("duplicate-entry-warning", re.compile(
        rf"{_NUM}\s+duplicate entry", re.I)),
    ("resting-overdue", re.compile(
        rf"^\W*{_NUM}\s+overdue item", re.I)),
    ("and-N-more", re.compile(
        rf"(?:^|\s)(?:…|\.\.\.)?and\s+{_NUM}\s+more\b", re.I)),
    ("N-new-items", re.compile(
        rf"^\W*{_NUM}\s+new items?\b", re.I)),
    ("N-personal-items", re.compile(
        rf"^\W*{_NUM}\s+personal item", re.I)),
    ("N-background-tasks", re.compile(
        rf"^\W*{_NUM}\s+of your background", re.I)),
    ("N-count-noun", re.compile(
        rf"^\W*{_NUM}\s+(?:relationships?|opened|closed|slipped|open\b|"
        rf"promises?|commitments?|proposals?|suggestions?|guesses|"
        rf"batch(?:es)?|questions?|older items?|entries)", re.I)),
    ("inventory-line", re.compile(
        rf"{_NUM}\s+you owe\s*·", re.I)),
    # FOLD1A fix round 1 (REVIEW_FOLD1A F-4) — the fold's own new count.
    # `assert_number_leads` was still being invoked over `lead_lines +
    # fold_lines + health_lines` with no shape matching "N owed to you", so
    # moving that line ABOVE the plate's number raised nothing: the fence
    # was live for every count except the one this lane added. A future
    # driver that re-orders `fold_lines` above `lead_lines` now reds.
    ("owed-to-you", re.compile(
        rf"^\W*{_NUM}\s+owed to you", re.I)),
)


class BriefOrderError(RuntimeError):
    """A count-shaped sentence sits above the plate's number line. Loud, in
    code, before the pack reaches a chat turn — the same posture as the
    plate's jargon gate and EODSYNTH1's score fence."""


def count_shaped_before_number(text: str, *, number_line: str = "") -> list:
    """The count-shaped lines that precede the plate's number line in
    `text`, as `(shape, line)` pairs. Empty list = the number leads.

    `number_line`, when given, is the plate's OWN lead line and anchors the
    scan even when it is not a number: a workspace with no resolvable owner
    leads with the one plain refusal sentence (D8), and a plate the loader
    could not read leads with its one-line apology — both are the plate's
    line, and nothing may sit above either.

    A text with NO number line at all returns a single
    `("no-number-line", "")` finding: the number cannot lead if it is not
    there. Blank lines and lines without a number are never findings."""
    lines = [l for l in (text or "").split("\n")]
    idx = next((i for i, l in enumerate(lines)
                if NUMBER_LINE_RE.match(l)
                or (number_line and l.strip() == number_line.strip())), None)
    if idx is None:
        return [("no-number-line", "")]
    out = []
    for l in lines[:idx]:
        if not l.strip():
            continue
        for name, rx in COUNT_SHAPES:
            if rx.search(l):
                out.append((name, l))
                break
    return out


def assert_number_leads(text: str, *, where: str = "morning-brief",
                        number_line: str = "") -> None:
    hits = count_shaped_before_number(text, number_line=number_line)
    if hits:
        raise BriefOrderError(
            f"{where}: the plate's number must be the first count in the "
            f"brief (CUT-PLATE, PLATE1-N2 R-1); found {hits!r} above it")


# ---------------------------------------------------------------------------
# SPEC SURFACEFIX1 5.4 / amendment B-1 — ONE OPEN COUNT ON THE BRIEF
# ---------------------------------------------------------------------------
#
# `assert_number_leads` fences the ORDER of counts and says nothing about
# their AGREEMENT. The v5.30.0 attended test (A4, B2.5) read a brief whose
# line one said 60 while the plate behind it held 334: both numbers were
# rendered, both were count-shaped, neither was above the other, and the
# fence was green. A reader cannot act on two answers to one question.
#
# THE RULE. Every integer of the OPEN class on the brief — open / owed /
# parked / waiting — either EQUALS line one, or is part of a breakdown that
# SUMS to line one. Nothing else about the brief's numbers is policed here:
# a duration, a date, a count of meetings, a money figure are all different
# questions and none of them is this fence's business.
#
# The class is deliberately narrow and named, for the same reason
# `COUNT_SHAPES` is: a red has to say WHICH sentence broke the rule.

#: Sentences that answer THE SAME QUESTION AS LINE ONE — "how many are on my
#: plate / open / waiting on me today". These are the figures that must
#: reconcile: A4's "60" beside "58" beside a plate of 334 were three answers
#: to this one question on one morning. `[N]`/`[X]`/`[Y]` are the SKILL.md
#: templates' placeholders, accepted so one scanner reads a template and a
#: live render alike (a placeholder states no number and is never a
#: disagreement).
OPEN_COUNT_SHAPES = (
    ("on-your-plate", re.compile(rf"({_NUM})\s+on your plate", re.I)),
    ("open", re.compile(rf"({_NUM})\s+open\b", re.I)),
    ("you-owe", re.compile(rf"({_NUM})\s+you owe", re.I)),
    ("waiting-on-you", re.compile(rf"({_NUM})\s+waiting on you", re.I)),
)

#: Figures that answer a DIFFERENT question and are allowed to stand beside
#: line one — what OTHERS owe, what is resting, what is parked. They are not
#: breakdowns of the attention number (the plate's WAIT, RESTING and PARKED
#: blocks sit outside it by construction, `plate_view.render_plate`), so
#: demanding that they sum to it would delete honest, ruled lines: "N owed to
#: you — say `show waiting`" is M's own wording, folded in by FOLD1A.
#:
#: What they may NOT do is appear TWICE. The other half of A4 was the brief
#: stating the owed-to-you figure once as a count and again as a pointer at
#: the retired Waiting On chat — one question, two sentences, and on 09-13
#: two different numbers. Each of these may say its figure ONCE.
DISTINCT_QUESTION_SHAPES = (
    ("owed-to-you", re.compile(rf"({_NUM})\s+owed to you", re.I)),
    ("resting", re.compile(rf"({_NUM})\s+(?:overdue items?\s+are\s+)?resting",
                           re.I)),
    ("parked", re.compile(rf"({_NUM})\s+parked\b", re.I)),
)


class BriefCountError(RuntimeError):
    """Two figures of the open class disagree on one brief. Loud, in code,
    before the pack reaches a chat turn."""


def headline_number_in(line) -> Optional[int]:
    """The plate's own leading figure, read off its lead line.

    `None` when the plate led with a refusal or an apology rather than a
    number (D8's no-owner sentence, the unreadable-plate line) — a brief with
    no headline figure has nothing for a second figure to disagree WITH, and
    `assert_single_open_count` skips it rather than inventing a whole.
    """
    m = re.search(r"\b(\d+)\b", str(line or ""))
    return int(m.group(1)) if m else None


def _figures_in(text: str, shapes) -> list:
    """Every figure of `shapes` in `text`, as `(shape, n, line)`. Placeholder
    figures (`[N]`) are skipped — a template states no number."""
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        for name, rx in shapes:
            m = rx.search(line)
            if not m:
                continue
            raw = m.group(1)
            if not raw.isdigit():
                break          # a template placeholder: no claim, no check
            out.append((name, int(raw), line.strip()))
            break
    return out


def open_counts_in(text: str) -> list:
    """Every figure that answers line one's question, as `(shape, n, line)`."""
    return _figures_in(text, OPEN_COUNT_SHAPES)


def distinct_question_counts_in(text: str) -> list:
    """Every figure that answers a DIFFERENT question (owed to you, resting,
    parked), as `(shape, n, line)`."""
    return _figures_in(text, DISTINCT_QUESTION_SHAPES)


def assert_single_open_count(text: str, *, where: str = "morning-brief",
                             headline: int | None = None) -> None:
    """SPEC SURFACEFIX1 5.4 / B-1 — the brief gives ONE answer per question.

    Two rules, and they are different because the questions are:

      1. Every figure that answers LINE ONE'S question ("how many are on my
         plate today") either equals line one or is part of a set that SUMS
         to it. "60 on your plate" beside "58" beside a plate of 334 — A4's
         reading, on one morning — raises.
      2. Every figure that answers a DIFFERENT question (owed to you,
         resting, parked) may be stated AT MOST ONCE. Those figures are not
         breakdowns of line one and are not required to reconcile with it;
         what they may not do is be answered twice, which is how the brief
         carried the owed-to-you count as a number AND as a pointer at a
         retired chat, with two different values on 09-13.

    A brief with no headline number (a refused plate, a workspace with no
    primary user) skips rule 1 — there is nothing to disagree with. Rule 2
    holds either way.
    """
    for name, figures in _group_by_shape(distinct_question_counts_in(text)):
        values = sorted({n for n, _line in figures})
        if len(figures) > 1:
            raise BriefCountError(
                f"{where}: the brief answers {name!r} more than once "
                f"({[l for _n, l in figures]!r})"
                + (f" — and with different figures {values!r}"
                   if len(values) > 1 else "")
                + ". One question, one sentence (B-1).")
    found = open_counts_in(text)
    if headline is None or not found:
        return
    others = [f for f in found if f[1] != headline]
    if not others:
        return
    # A breakdown: the parts sum to the whole. Checked over the figures that
    # are NOT the headline, because the headline itself is the whole.
    if sum(n for _name, n, _line in others) == headline:
        return
    raise BriefCountError(
        f"{where}: the brief states more than one figure for what is on the "
        f"plate today and they do not reconcile with line one ({headline}); "
        f"found {[(n, line) for _name, n, line in others]!r}. One number "
        f"leads and every other figure of that question is a breakdown of "
        f"it (B-1).")


def _group_by_shape(figures) -> list:
    """`[(shape, [(n, line), ...]), ...]`, in first-seen order."""
    order: list = []
    groups: dict = {}
    for name, n, line in figures:
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append((n, line))
    return [(name, groups[name]) for name in order]


# ---------------------------------------------------------------------------
# SPEC SURFACEFIX1 5.1 / R3 — NO REACHABILITY SENTENCE ON A CUSTOMER SURFACE
# ---------------------------------------------------------------------------
#
# M, 2026-09-13 (ruling R3): "Connector-reachability / coverage lines OFF the
# brief and End of Day, same class as the 09-07 health lines; home = health
# check + maintenance report."
#
# The End of Day half is mechanical — the coverage block left `RENDER_ORDER`
# and `compose_screen` (see `end_of_day`). The BRIEF half had no code behind
# it at all: the sentences M read on 09-13 ("I haven't been able to check your
# sent mail in the last day", "your personal and family calendars aren't
# reachable") were composed by the model from instructions in prose, so there
# was nothing to remove and nothing to pin. This is that missing half: the
# shapes, in code, with a checker the brief's own composed text runs through
# and the suites run over the prose that instructs it.
#
# WHAT IS AND IS NOT ONE. A reachability sentence says something about a
# SOURCE — reached, read, checked, current, behind. It is not:
#   * a hedge with no reason ("you may have already handled this") — that is
#     the Bug #98 soften floor and it stays;
#   * a caveat on ONE rendered number (COVERQUIET1's carve-out) — that one
#     lives in `end_of_day.coverage_number_caveat` and is placed adjacent to
#     the figure it qualifies, never as a line of its own;
#   * an item's own text that happens to mention mail.

#: The sources a reachability sentence can be ABOUT. Every shape below is
#: bound to one of these within a short span, because the negation on its own
#: ("do NOT read session notes") is ordinary instruction prose and the fence
#: has to leave it alone.
_SOURCE_WORD = (r"(?:sent\s+)?(?:e-?mails?|mail|inbox|calendars?|chats?|"
                r"slack|connectors?|sources?|granola|drive|transcripts?)")
_NOT = r"n(?:['’]?t|ot)"

#: The named shapes. Each is `(name, regex)` so a red says WHICH sentence.
#: Every one is NEGATIVE and SOURCE-BOUND by construction — "the calendar is
#: reachable" and "do NOT read session notes" are honest text and trip none
#: of them. The roster was derived from the four sentences the attended test
#: actually recorded (B1.5, Part E 09-11, leak 10), not invented.
REACHABILITY_SHAPES = (
    ("could-not-reach", re.compile(
        rf"(?:(?:could|can|do|did|was|were|am|is|are|have|has)\s*{_NOT}|"
        rf"cannot|unable\s+to)\s+(?:been\s+able\s+to\s+)?"
        rf"(?:reach|read|check|see)\b[^.\n]{{0,40}}"
        rf"\b{_SOURCE_WORD}\b", re.I)),
    ("not-reachable", re.compile(
        rf"\b{_SOURCE_WORD}\b[^.\n]{{0,30}}(?:{_NOT}|never)\s+reachable\b|"
        rf"\b{_SOURCE_WORD}\b[^.\n]{{0,20}}\bunreachable\b", re.I)),
    ("have-not-been-able", re.compile(
        rf"h(?:ave|as|a)\s*{_NOT}\s+been\s+able\s+to\s+\w+"
        rf"[^.\n]{{0,40}}\b{_SOURCE_WORD}\b", re.I)),
    ("source-behind", re.compile(
        rf"\b(?:{_SOURCE_WORD}|cursor)\b[^.\n]{{0,40}}\bbehind\b", re.I)),
    ("connector-not-read", re.compile(
        rf"\bconnectors?\b[^.\n]{{0,60}}\b(?:was|were|is|are)\s+not\s+read\b|"
        rf"\bconnectors?\b[^.\n]{{0,40}}\b(?:is|was|are|were)\s+"
        rf"(?:down|unavailable|failing)\b", re.I)),
    ("check-connection", re.compile(r"check\s+(?:your\s+)?connection", re.I)),
)

#: THE ONE SANCTIONED SENTENCE. COVERQUIET1's carve-out (R3's only exception)
#: qualifies ONE rendered number and is composed in code by
#: `end_of_day.coverage_number_caveat`. Its two templates both end in this
#: tail, which is what makes a line a caveat rather than a coverage strip: it
#: says what the gap does to a FIGURE, never what the connector did. Carried
#: as a literal, not read off the constant, so a rename of the template shows
#: up as a red here rather than silently widening the exemption — and
#: `run_health1_test.py` pins the literal against both templates.
COVERAGE_CAVEAT_TAIL = "so these counts may be short."

#: A prose line that RETIRES one of these shapes has to quote it to say what
#: it retires. The lane marker is the one token that licenses a quote, and it
#: only licenses it alongside a word that says the sentence is gone — so a
#: future edit cannot re-instate a render instruction by pasting the marker
#: onto it. Both halves are required.
REACHABILITY_EXEMPT_MARKER = "SURFACEFIX1 5.1"
REACHABILITY_EXEMPT_NEGATIONS = (
    "never", "no longer", "used to", "retired", "off this surface",
    "off the brief", "says nothing", "say nothing", "reds on",
    "has left this surface", "rendered nowhere",
)


#: HEAL1 — THE CATCH-UP'S OWN VOCABULARY, ON NO CUSTOMER SURFACE.
#:
#: NUMBER1 3.9 took "Completed 4 background maintenance jobs on schedule."
#: off the brief by dropping a FEED CATEGORY (`brief_plumbing_categories`).
#: HEAL1 creates a second way for the same sentence to arrive: the surface
#: now RUNS those jobs itself, moments before it composes, and the natural
#: thing for a model holding that fact is to mention it — "caught up on
#: maintenance first", "ran 6 background jobs before this". A category
#: filter cannot see a sentence nobody put in the feed.
#:
#: So the catch-up's vocabulary joins the R3 fence, which is the one gate
#: that reads the brief's FINAL composed text rather than its ingredients.
#: Same `(name, regex)` shape as the reachability roster, same marker
#: exemption for prose that names what it removed, and the same rule: the
#: home for the plumbing's own condition is the health check and the weekly
#: maintenance report, never a surface the reader typed.
CATCHUP_PLUMBING_SHAPES = (
    ("catchup-ran-maintenance", re.compile(
        r"\b(?:ran|run|running|completed|finished|performed|executed|did)\b"
        r"[^.\n]{0,30}\b(?:background\s+)?"
        r"(?:maintenance|upkeep|housekeeping)\b", re.I)),
    ("catchup-maintenance-state", re.compile(
        r"\b(?:maintenance|upkeep|housekeeping)\b[^.\n]{0,30}"
        r"\b(?:caught\s+up|up\s+to\s+date|out\s+of\s+date|behind|overdue|"
        r"stale|had\s+n(?:ot|ever))\b", re.I)),
    ("catchup-caught-up", re.compile(
        r"\b(?:caught|catching)\s+up\b[^.\n]{0,30}"
        r"\b(?:maintenance|upkeep|housekeeping|background\s+jobs?)\b", re.I)),
    ("catchup-job-count", re.compile(
        r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:background\s+|maintenance\s+|overdue\s+)+jobs?\b", re.I)),
    ("catchup-before-this-surface", re.compile(
        r"\bbefore\s+(?:this|the|your)\s+"
        r"(?:brief(?:ing)?|close|day.?close|recap|wrap)\b[^.\n]{0,30}"
        r"\b(?:ran|i\s+ran|caught|catch)\b", re.I)),
)

#: THE PRODUCT'S OWN TYPED PHRASE IS NOT A CLAIM ABOUT THE PLUMBING.
#:
#: `run maintenance` is a command a reader types. It is offered on the health
#: check, in the weekly report, and in the one sentence a read surface says
#: when the upkeep is behind — and it is written, verbatim, all over the
#: instruction layer that tells the fire what to do. Every one of those is the
#: PHRASE, not a sentence saying the machinery ran, and a fence that cannot
#: tell them apart would forbid the product from naming its own remedy.
#:
#: So the phrase is blanked out of a line before the catch-up shapes read it.
#: The reachability family is unaffected: it never matched on this word and
#: scans the raw line exactly as it always has.
CATCHUP_TYPED_PHRASE_RE = re.compile(
    r"`?\b(?:run|running)\s+(?:my\s+|the\s+)?maintenance(?:\s+now)?\b`?",
    re.I)


def blank_typed_phrase(line: str) -> str:
    """`line` with the offered `run maintenance` phrase blanked to spaces.

    Same length, so any offset a caller kept still points at the same place.
    """
    return CATCHUP_TYPED_PHRASE_RE.sub(lambda m: " " * len(m.group(0)),
                                       line or "")


#: The two rosters the surface fence reads, in one place, so a caller that
#: wants "everything a customer surface may not say about the plumbing"
#: cannot get half of it. The reachability family stays first, so a line
#: matching both is still reported under the name the R3 record uses.
SURFACE_FORBIDDEN_SHAPES = REACHABILITY_SHAPES + CATCHUP_PLUMBING_SHAPES


class ReachabilityLineError(RuntimeError):
    """A customer surface states something about a connector. R3 forbids it."""


def catchup_plumbing_lines_in(text: str) -> list:
    """Every catch-up-plumbing line in `text`, as `(shape, line)`.

    The HEAL1 half of the surface fence on its own, for the suites and for
    any caller that wants to say WHICH family a sentence tripped.
    """
    return [hit for hit in reachability_lines_in(text)
            if hit[0].startswith("catchup-")]


def reachability_lines_in(text: str, *, allow_marked: bool = False) -> list:
    """Every reachability OR catch-up-plumbing line in `text`, as
    `(shape, line)`.

    `allow_marked=True` skips lines that both carry the lane marker AND say
    the sentence is retired — the shape a SKILL.md needs to name what it is
    removing. Off by default: a RENDERED brief gets no exemptions at all.
    """
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if line.rstrip().endswith(COVERAGE_CAVEAT_TAIL):
            continue           # COVERQUIET1's one caveat — the exception
        if allow_marked and REACHABILITY_EXEMPT_MARKER in line \
                and any(w in line.lower()
                        for w in REACHABILITY_EXEMPT_NEGATIONS):
            continue
        scrubbed = blank_typed_phrase(line)
        for name, rx in SURFACE_FORBIDDEN_SHAPES:
            # The catch-up family reads the line with the product's own typed
            # phrase blanked; the reachability family reads the raw line.
            target = scrubbed if name.startswith("catchup-") else line
            if rx.search(target):
                out.append((name, line.strip()))
                break
    return out


def assert_no_reachability_line(text: str, *, where: str = "morning-brief",
                                allow_marked: bool = False) -> None:
    """SPEC SURFACEFIX1 5.1 / R3 — raise if `text` says anything about a
    source being unreachable, behind, stale or unchecked, or (HEAL1) anything
    about the maintenance this surface just caught up on."""
    hits = reachability_lines_in(text, allow_marked=allow_marked)
    if hits:
        raise ReachabilityLineError(
            f"{where}: a connector-reachability or plumbing sentence reached "
            f"a customer surface (M's ruling R3, 2026-09-13; HEAL1 behaviour "
            f"2); found {hits!r}. The home "
            f"for these conditions is the health check and the weekly "
            f"maintenance report, never the brief or the day-close.")


class CatchUpPlumbingLineError(RuntimeError):
    """A customer surface states something about the upkeep it just ran."""


def assert_no_catchup_line(text: str, *, where: str) -> None:
    """FIX ROUND 1, REVIEW M-2 — the HEAL1 half of the surface fence, for a
    surface that does not take the whole R3 family.

    The catch-up now runs from three surfaces and only the morning brief
    called `assert_no_reachability_line` on its composed text, so behaviour 2
    was code-enforced on one surface and prose-enforced on the other two. The
    day close cannot take the WHOLE fence — its coverage block exists to say
    what this fire could and could not read, which is the R3 family's own
    shape and is right there by design — so it takes the half this lane
    added: nothing about the maintenance the surface just caught up on.
    """
    hits = catchup_plumbing_lines_in(text)
    if hits:
        raise CatchUpPlumbingLineError(
            f"{where}: a sentence about the catch-up's own plumbing reached a "
            f"customer surface (HEAL1 behaviour 2); found {hits!r}. What the "
            f"catch-up DID to the reader's rows belongs in the CHANGED strip; "
            f"how the machinery is feeling belongs on the health check.")


#: THE CUSTOMER'S-OWN-WORDS MARKS (review F-5). `plate_view` has fenced its
#: own text this way since PLATE1-N2: a span the customer wrote is wrapped in
#: two control characters, and `scan_no_pending_question` BLANKS those spans
#: before it looks for the product's vocabulary. That is the whole mechanism
#: that lets a row whose title the reader typed sit inside a fenced text
#: without the reader's words being judged as the product's.
#:
#: Line two is exactly that class of text and had been getting the crude
#: version of the same treatment — left out of the fenced text altogether,
#: which also took it out of the INTERROGATIVE half, which is not user-text
#: business at all (a stated intent is a statement; a question put to the
#: reader is a question whoever typed it). So it joins the fenced text
#: MARKED: blanked for the vocabulary scan, read in full by the ask scan.
#:
#: The marks are read from `plate_view` so the two cannot drift, with a named
#: fallback for a trimmed tree — the `_waiting_phrase` pattern. They never
#: reach the reader: only the fence's own text carries them.
#:
#: NAMED "reader words", NOT "user text", DELIBERATELY. `chat_output_renderer`
#: owns a DIFFERENT marking mechanism with a reserved vocabulary of its own
#: (`mark_field` / `mark_user_text` / `USER_TEXT_OPEN`), and
#: `run_guard_user_text_provenance_test`'s bypass scan reads every `.py` under
#: `shared/scripts/` for those names — the first spelling of this helper was
#: called `mark_user_text` and tripped that guard, correctly: two marking
#: mechanisms must not share a name. This one is `plate_view`'s in-text fence
#: instrument and never travels to a widget, a board or a document.
READER_SPAN_OPEN_FALLBACK = ""
READER_SPAN_CLOSE_FALLBACK = ""


def reader_span_marks() -> tuple:
    """`(open, close)` — `plate_view`'s own user-span marks, or the fallback."""
    try:
        from plate_view import _USER_CLOSE, _USER_OPEN
        if _USER_OPEN and _USER_CLOSE:
            return str(_USER_OPEN), str(_USER_CLOSE)
    except Exception:  # pragma: no cover — the fallback carries it
        pass
    return READER_SPAN_OPEN_FALLBACK, READER_SPAN_CLOSE_FALLBACK


def as_reader_words(text: str) -> str:
    """Wrap `text` as the CUSTOMER'S OWN WORDS for the fences below."""
    if not text:
        return ""
    o, c = reader_span_marks()
    return f"{o}{text}{c}"


def strip_reader_marks(text: str) -> str:
    """`text` with the marks removed and the words kept — what a scan that
    judges SHAPE rather than vocabulary must read."""
    if not text:
        return ""
    o, c = reader_span_marks()
    return text.replace(o, "").replace(c, "")


class BriefAsksError(RuntimeError):
    """The brief asked a question. M's design rule of 2026-09-06: the brief
    and the wrap never ask; the Staff Meeting is where questions live, and
    End of Day may carry two."""


#: An interrogative sentence, by its OPENER. A question mark alone is not the
#: test: a row title the customer wrote can end in one ("Ask Quinn whether the
#: date still works?") and reddening the whole fire over the reader's own
#: words would be the fence doing harm. The openers below are what a QUESTION
#: PUT TO THE READER starts with, and the recorded defect — "Done, new date,
#: or drop?" riding a plate row, and "did I get that right?" — is caught by
#: the trailing-clause shape rather than by an opener.
_ASK_OPENERS = (
    "did", "do", "does", "is", "are", "was", "were", "can", "could",
    "should", "would", "will", "shall", "have", "has", "who", "what",
    "when", "where", "why", "which", "how", "want", "ready", "anything",
)
_ASK_TAIL_SHAPES = tuple(re.compile(p, re.I) for p in (
    r"\bor\s+drop\?",                     # "Done, new date, or drop?"
    r"\bdid\s+i\s+get\s+(?:that|this|it)\s+right\?",
    r"\bwhose\s+is\s+this\?",
    r"\b(?:yes|no)\s+or\s+\w+\?",
))


def interrogatives_in(text: str) -> list:
    """Every sentence in `text` that asks the reader something."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        for rx in _ASK_TAIL_SHAPES:
            if rx.search(line):
                out.append(line)
                break
        else:
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                s = sentence.strip()
                if not s.endswith("?"):
                    continue
                first = re.sub(r"^\W+", "", s).split(" ", 1)[0].lower()
                if first in _ASK_OPENERS:
                    out.append(line)
                    break
    return out


def assert_brief_never_asks(text: str, *, where: str = "morning-brief") -> None:
    """SPEC SURFACEFIX1 5.4 / amendment B-2 — THE BRIEF NEVER ASKS.

    The 09-13 renders both carried a pinned question riding in from the
    plate's ask rows. The plate's own ask fence (`plate_view.
    scan_no_pending_question`) has policed the PLATE's text since PLATE1-N2;
    this runs the SAME fence over the WHOLE composed brief, which is the half
    that was never covered — a question composed by any other block reached
    the reader untouched.

    TWO HALVES, and only one of them existed.

    The PROPOSAL half is `plate_view.scan_no_pending_question` — "a proposal
    is waiting on you", in its several phrasings. That fence has policed the
    PLATE's own text since PLATE1-N2 and it is called here over the WHOLE
    composed brief, which is the scope that was missing: a claim composed by
    any other block reached the reader untouched. It is called, never copied,
    so the shapes have one home and cannot drift. (Its own patterns live in
    `plate_view.py`, which this lane does not own.)

    The INTERROGATIVE half is B-2's and is new: a question PUT TO THE READER,
    anywhere in the brief. That is what the 09-13 renders carried and what no
    existing fence could see.
    """
    # THE TWO HALVES READ DIFFERENT TEXTS, and that is the point (review
    # F-5). The interrogative half judges SHAPE — is a question being put to
    # the reader — which is true or false whoever typed the sentence, so it
    # reads the marks OUT and the words IN. The proposal-vocabulary half
    # judges the PRODUCT'S WORDS and must not judge the customer's, so it
    # reads the marks IN and `scan_no_pending_question` blanks those spans
    # itself (`plate_view._blank_user_spans`).
    asks = interrogatives_in(strip_reader_marks(text))
    if asks:
        raise BriefAsksError(
            f"{where}: the brief asks the reader a question "
            f"({asks[:3]!r}). M's design rule of 2026-09-06: the brief and "
            f"the wrap never ask — the Staff Meeting is where questions "
            f"live, and End of Day may carry two (B-2).")
    try:
        from plate_view import scan_no_pending_question
    except Exception:  # pragma: no cover — the fence's home must be importable
        return
    scan_no_pending_question(text or "")


# ---------------------------------------------------------------------------
# SPEC_SURFACES2_11c BRIEF2 2.2 item 1 — THE CAP NEVER HIDES A MACHINE BATCH
# ---------------------------------------------------------------------------
#
# The brief asked `change_feed.changes_since(..., max_lines=3)` for its
# CHANGED strip, and `changes_since` ranks by CATEGORY in one fixed order.
# On 2026-09-13 that cap ate the two lines that mattered most on the book: a
# 48-row review-door lapse and a 51-row rest batch, both acts the machine
# took on the reader's OWN rows while they were away, both reversible by one
# word — cut so that three ordinary housekeeping lines could print.
#
# M's design rule of 2026-09-06, item 4: "Importance first, not recency.
# Whatever a cap or a fold hides, it never hides an overdue item or one due
# this week." A door that moved the reader's rows without them is the same
# class: it is the one thing on the strip they may need to reverse, and a
# line they never see is a line they cannot reverse.
#
# So the cap moves off the batch lines and onto the REST. The batch lines
# print in full; the ordinary lines keep the cap they always had. Nothing
# here re-classifies and nothing here re-ranks inside either group:
# `change_feed` stays the one place that decides what a category means and
# what order categories come in (it is FROZEN this night), and this is a
# PARTITION of its output, which is the smallest thing that can fix this.
#
# A REVERSED batch is not this function's business either, and that is
# deliberate: `changes_since` already folds every act an undo reversed out of
# its counts (`closure_index.reversed_act_positions`, the ATTRIB2 / POLICY1-B
# folds) and narrates it once as "Undid N changes you reversed". So a batch
# undone before the render arrives here as the undo line, never as a standing
# batch line, and the brief inherits that fold rather than repeating it. The
# suite pins the inheritance ON THE BRIEF, because an inherited property that
# nothing checks at the surface is a property one refactor away from gone.

#: The brief's cap on ORDINARY feed lines. Unchanged in value from the
#: `max_lines=3` this surface has passed since LB1 — what changed is what it
#: is a cap ON.
BRIEF_CHANGED_ORDINARY_CAP = 3

#: The feed categories that are a JOB'S BATCH ON THE READER'S OWN ROWS — a
#: door the product walked through on their behalf, narrated as a count with
#: an undo phrase. These are what the cap may never hide.
#:
#: The test is not "did the machine do it" (it did all of these) — it is "did
#: it MOVE ONE OF THEIR ROWS". `people_added`, `facts_noted` and the learning
#: job's lines are the machine's work too and they are ordinary here on
#: purpose: nothing left the plate. `closed_from_your_word`, `put_back` and
#: `unrested` are the CUSTOMER'S own acts and are likewise ordinary — a
#: reader does not need protecting from the news that they themselves closed
#: something.
#:
#: TWO ENTRIES ARE HERE FOR A REASON THE SENTENCE ABOVE DOES NOT COVER, AND
#: SAYING SO IS THE FIX (review F-2, F-4; M's ruling, defaults taken).
#:
#:   `changes_undone` — "Undid N changes you reversed." It IS the customer's
#:   own act, so the rule above would make it ordinary; it was, and on a
#:   crowded morning the cap ate it. It is the ONE ordinary line that
#:   narrates the reversal of a line this tuple protects: a reader who undid
#:   a 48-row batch last night and reads a brief that says nothing about it
#:   gets exactly the silence this partition exists to remove, one category
#:   over. The tuple's own test is true of it on the rows — they moved BACK.
#:
#:   `proposals_retracted` / `proposals_expired` — measured against the
#:   ledger, NOT reversible in the way the other batch lines are, and their
#:   sentences say so themselves ("(nothing was changed)"): no row left the
#:   plate, the question did. A retract (`commitment_review_dismissed`,
#:   reason `commitment_policy.RETRACT_REASON`) carries a `brain_batch_id`
#:   and NO `brain_change_class`, so `brain_undo.REVERSERS` has nothing to
#:   dispatch on — there is no undo to offer and the parenthetical is
#:   honest. `brain_proposal_expired` has TWO writers and only one of them
#:   is reversible (`question_ttl.PROPOSAL_EXPIRY_CHANGE_CLASS`, which IS a
#:   registered reverser; `brain_proposals.expire_stale` stamps no class at
#:   all), so its one sentence cannot carry an undo phrase truthfully for
#:   both. They stay protected because a question the product withdrew on
#:   the reader's behalf is still a door it walked through on their behalf,
#:   and it is the line they will never otherwise learn existed — not
#:   because a row moved. The suite pins both halves of that reading off
#:   `brain_undo.REVERSERS` so this comment cannot drift from the code.
#:   Wording the feed's two sentences to match (drop the parenthetical, or
#:   split the expiry line by writer) is a `change_feed.py` change and that
#:   file is frozen this night — it is in this lane's Seams.
#:
#: Every name below is a `change_feed.changes_since` counts key; the suite
#: pins that, so a rename in the feed reds here instead of silently emptying
#: this tuple.
#: NUMBER1 3.8 (night 11d) ADDS `learned`, and the reason is the same one
#: the two paragraphs above give for `proposals_retracted`: the rule "did it
#: move one of their rows" is not the whole test. The learning pass changes
#: how the product WRITES for them, on its own, with one undo -- a door it
#: walked through on their behalf -- and on 2026-09-15 four such changes
#: landed and the brief narrated one, because four ordinary lines met an
#: ordinary cap of three (B6.3). NUMBER1 3.8 collapses the pass to ONE line
#: naming every skill it touched, so the cost of protecting it is one line
#: on a crowded morning and the alternative was three real changes reaching
#: no surface at all.
#: FIX ROUND 1 (REVIEW F-3, coordinator ruling): the collapse is ONE LINE PER
#: WINDOW, not per batch. Per batch, a catch-up window covering several
#: passes cost N uncapped lines here -- four batches of one measured as four
#: protected lines, where at base at most three reached the strip at all. The
#: cost of protecting `learned` is therefore exactly ONE line on a crowded
#: morning, at any number of passes. THE EXPOSURE THAT REMAINS, stated: on a
#: multi-pass window that one line's `undo` reverses the LATEST pass only;
#: the earlier passes are named in the sentence, stay applied, and are
#: reviewed and reversed one at a time behind `what have you learned`.
CHANGED_MACHINE_BATCH_CATEGORIES = (
    "learned",
    "closed_from_sent",
    "closed_from_meetings",
    "closed_from_calendar",
    "closed_from_deal",
    "rested_quiet",
    "let_go_quiet",
    "parked_quiet",
    "unconfirmed_expired",
    "proposals_retracted",
    "proposals_expired",
    "changes_undone",
)


#: NUMBER1 3.9 -- THE PLUMBING NEVER COMPOSES INTO A CUSTOMER SURFACE.
#: `cleanup_runs` ("Ran the weekly cleanup pass.") and `maintenance_jobs`
#: ("Completed 4 background maintenance jobs on schedule.", read on the
#: 2026-09-16 19:16 brief) are the condition of the machinery, not the
#: reader's work. M's 2026-09-07 ruling took the health block off this
#: surface; its extension the same day covers every line of that kind
#: however honestly worded. Read from `change_feed` so the surface that
#: drops them and the maintenance report that claims them name the same
#: set. `run_health1_test` reds by name if either sentence comes back.
def brief_plumbing_categories() -> tuple:
    """The feed categories the brief refuses, from their one home."""
    try:
        from change_feed import PLUMBING_CATEGORIES
        return tuple(PLUMBING_CATEGORIES)
    except Exception:  # pragma: no cover -- never widen what the brief says
        return ("cleanup_runs", "maintenance_jobs")


def split_machine_batch_lines(lines) -> tuple:
    """`(batch, ordinary)` — the feed's lines partitioned by the tuple above,
    each group in the feed's own order. Pure; a non-dict line is ordinary."""
    batch, ordinary = [], []
    for line in (lines or []):
        cat = (line.get("category") if isinstance(line, dict) else None)
        (batch if cat in CHANGED_MACHINE_BATCH_CATEGORIES
         else ordinary).append(line)
    return batch, ordinary


def brief_changed_lines(feed: dict, *,
                        cap: int = BRIEF_CHANGED_ORDINARY_CAP) -> list:
    """The CHANGED strip for the morning brief: every machine-batch line the
    window holds, then the top ordinary lines to `cap`.

    Takes the FULL feed (`changes_since` with no `max_lines`) — a cap applied
    upstream has already thrown away the lines this function exists to keep.
    """
    plumbing = brief_plumbing_categories()
    rows = [l for l in (feed.get("lines") or [])
            if not (isinstance(l, dict) and l.get("category") in plumbing)]
    batch, ordinary = split_machine_batch_lines(rows)
    kept = batch + (ordinary[:cap] if cap is not None else ordinary)
    return [str(l.get("text") or "") for l in kept if l.get("text")]


# ---------------------------------------------------------------------------
# HEAL1 — the on-demand surfaces carry the upkeep (SPEC_MERGEFIX1 §4 HEAL1,
# amended by SPEC_NIGHTM2_LANES §4)
# ---------------------------------------------------------------------------
#
# The decision lives in `maintenance_dispatcher` (which surfaces, which
# family, has the book fallen behind, the min-gap guard, the container
# refusal). These two functions are the surfaces' door onto it: the three
# on-demand drivers call `maintenance_catch_up` before they gather, and the
# two READ surfaces call `maintenance_truth_line` and print nothing else.
#
# The driver never EXECUTES a job. This module has never run a skill and does
# not start here: a job's leg is the orchestrator's (or, on a merged seat,
# the model's `plan run_helper` line), and the driver takes it as `runner`.
# With no runner the call is a plan — the pack carries it and the surface's
# own prose runs it, exactly as `cleanup/SKILL.md` Step 0 does.

#: The one fallback sentence for the READ surfaces, used only on a tree that
#: does not carry TRUTH1 (SPEC_NIGHTM2 §4 amendment c — the line is TRUTH1's
#: and this lane imports it behind an ImportError).
#:
#: FIX ROUND 1, REVIEW B-1 — IT IS TRUTH1'S OWN SENTENCE, BYTE FOR BYTE.
#: It was this lane's own plainer sentence, which meant the two lanes said
#: different words about the same fact depending on whether TRUTH1 happened
#: to be on the tree. The text below is `task_watchdog.truth_line(when=None)`
#: at TRUTH1's `c534e5bd` — the composer's own output with the "the last one
#: ran <when>" clause dropped, which is the form that goes to a reader when
#: there is no run to name. `run_heal1_test` pins it by string equality
#: against TRUTH1's constant on a grafted tree, so the two cannot drift.
MAINTENANCE_TRUTH_FALLBACK = (
    "Your scheduled chats and background maintenance stopped running after "
    "the Claude app update. Everything still works when you ask: say "
    "`morning briefing`, `end of day`, `weekly recap` on Fridays, "
    "`staff meeting`, and `run maintenance` once a day. Command Room will "
    "tell you when schedules are back.")

#: A hole a template left behind — `<when>`, `{days}`. The read surfaces'
#: sentence is composed elsewhere (TRUTH1's `truth_line`), so this module's
#: last act before handing a line to a reader is to check that nothing
#: template-shaped survived the composition. B-1 was exactly this: HEAL1
#: imported TRUTH1's raw CONSTANT instead of its composer and a literal
#: `<when>` went to the customer.
MAINTENANCE_TRUTH_HOLE_RE = re.compile(
    r"<[^<>\n]{1,40}>|\{[^{}\n]{0,40}\}")


def maintenance_truth_has_hole(text) -> bool:
    """True when `text` still carries an unfilled template hole."""
    return bool(MAINTENANCE_TRUTH_HOLE_RE.search(str(text or "")))


#: More than ONE weekday stale is the floor (HEAL1 behaviour 4). A Friday
#: receipt read on a Monday is one weekday old and says nothing; the same
#: receipt read on Tuesday is two and does.
MAINTENANCE_TRUTH_WEEKDAYS = 1

#: The surfaces that carry the sentence and run no job. Read off the
#: dispatcher's own set so the two cannot disagree about which surfaces are
#: read surfaces, with the literal as the fallback for a trimmed tree.
def _read_surface_truth_line() -> frozenset:
    try:
        from maintenance_dispatcher import CATCH_UP_READ_SURFACES
        return frozenset(CATCH_UP_READ_SURFACES) & frozenset(
            {"staff-meeting", "my-plate", "plate", "plate-page"})
    except Exception:  # pragma: no cover — never widen what a surface says
        return frozenset({"staff-meeting", "my-plate", "plate"})


_READ_SURFACE_TRUTH_LINE = _read_surface_truth_line()


def maintenance_truth_line(workspace_root, now_iso: str | None = None) -> str:
    """The one sentence a READ surface says when this book's upkeep has
    fallen more than one weekday behind — `""` when it has not.

    READ/COMPUTE ONLY (it is on the access layer's helper allow-list). It
    runs no job, writes no receipt and returns no count: a read surface is a
    glance at the reader's own rows, and the plumbing gets one sentence
    there or nothing.

    The sentence itself is TRUTH1's, COMPOSED BY TRUTH1 (`truth_line`), never
    re-typed and never assembled from its raw constant here. Until that lane
    lands the import fails and `MAINTENANCE_TRUTH_FALLBACK` is used, which is
    the same words — the seam is named, not guessed at (SPEC_NIGHTM2 §4 (c)).
    """
    import maintenance_dispatcher as md

    state = md.maintenance_staleness(workspace_root, now=now_iso)
    if not state.get("stale"):
        return ""
    weekdays = state.get("weekdays_stale")
    # A BOOK WITH NO UPKEEP ON RECORD AT ALL SAYS NOTHING HERE, deliberately.
    # `weekdays_stale` is None exactly when there has never been a
    # `maintenance_run` receipt, which is every brand-new workspace and every
    # test fixture. "Your upkeep has not run for a few days" is false there,
    # and the sentence that IS true on such a seat — that schedules were never
    # set up — is TRUTH1's, on the health check, with its own phrase. Stated
    # so the narrow gate reads as a decision rather than an oversight; the
    # never-ran branch is in this lane's Seams.
    if weekdays is None or weekdays <= MAINTENANCE_TRUTH_WEEKDAYS:
        return ""
    # FIX ROUND 1, REVIEW B-1 — CALL TRUTH1'S COMPOSER, NEVER ITS CONSTANT.
    # `TRUTH_LINE` is a template with one hole spelled `<when>`, and the whole
    # job of `truth_line()` is to fill that clause or drop it. Importing the
    # constant put a literal `<when>` in front of a reader the moment the two
    # lanes met. `when=None` is the deliberate argument: this module has no
    # humanised time to hand over — `last_receipt` is a machine ISO string —
    # and TRUTH1's composer drops the clause rather than inventing a date,
    # which is R3's posture and the default the reviewer recommended.
    try:
        from task_watchdog import truth_line as _compose   # TRUTH1, Night M2
        text = str(_compose(when=None) or "")
    except ImportError:
        text = MAINTENANCE_TRUTH_FALLBACK
    except Exception:  # noqa: BLE001 — a sentence never fails on an import
        text = MAINTENANCE_TRUTH_FALLBACK
    if not text.strip():
        text = MAINTENANCE_TRUTH_FALLBACK
    if "{" in text:
        # BELT: a composer that hands back a brace template. Fill what this
        # lane can answer rather than printing a brace at a reader.
        try:
            text = text.format(weekdays=weekdays,
                               days=weekdays,
                               last=state.get("last_receipt") or "")
        except (KeyError, IndexError, ValueError):
            text = MAINTENANCE_TRUTH_FALLBACK
    # THE LAST ACT BEFORE A READER SEES IT. Whatever composed the sentence,
    # a hole that survived composition is never shown: the fallback says the
    # same words with no clause to fill (it IS `truth_line(when=None)`), so
    # degrading here costs the reader nothing.
    if maintenance_truth_has_hole(text):
        text = MAINTENANCE_TRUTH_FALLBACK
    return "" if maintenance_truth_has_hole(text) else text


#: FIX ROUND 1, REVIEW H-1 — THE HOST MODE A SURFACE DID NOT NAME.
#:
#: The dispatcher takes a `mode` and refuses write-class jobs in `container`.
#: Nothing passed one: the three surfaces declared `host_mode` and every
#: production call left it None, so the refusal was pinned dead code. The
#: mode is now RESOLVED here, from the access layer's own resolver
#: (`workspace_access.detect_host_mode`), asked about THE FOLDER THIS CALL
#: WAS HANDED rather than about the process's cwd — which is the difference
#: between "is there a workspace anywhere near me" and "is the book I was
#: asked to tidy on this machine".
#:
#: ONE FACT OUTRANKS A CONSERVATIVE ANSWER, exactly as it does inside the
#: layer. `detect_host_mode` answers `container` for `unknown`, which is the
#: right posture for a WRITE VERB and the wrong one for this question: a
#: legacy seat whose signals nobody recognises still has the folder right
#: here, and refusing half its upkeep there would break a working seat to
#: fence a host it is not on. So when the layer says `container` and the
#: book is demonstrably on this host, the answer is "no opinion" — today's
#: behaviour, byte for byte.


def resolved_host_mode(workspace_root=None, env=None) -> str | None:
    """The access layer's host mode for the book this call was handed.

    None when the layer is not on the tree, when it cannot answer, or when
    its conservative `container` answer is contradicted by the folder being
    here. Never raises: a mode nobody could resolve must not cost a reader
    the surface they asked for.
    """
    try:
        import workspace_access as wa
    except Exception:  # noqa: BLE001 — a trimmed tree has no layer
        return None
    try:
        start = Path(workspace_root) if workspace_root else None
        mode = wa.detect_host_mode(env, start)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(mode, str) or not mode:
        return None
    if mode == getattr(wa, "CONTAINER", "container") and workspace_root:
        try:
            # MF-M2-22. The question is "is THIS book on this host", and the
            # answer has to be about this book. `find_root_up` walks
            # ANCESTORS, so a container that happens to sit under an anchored
            # directory answered yes for a root that does not exist at all,
            # and the layer's conservative `container` was overturned by a
            # folder nobody asked about. `_is_root` asks about the directory
            # it was handed and nothing above it.
            if wa._is_root(Path(workspace_root)):
                return None
        except Exception:  # noqa: BLE001
            return None
    return mode


#: The word the morning brief hands `maintenance_catch_up` for its upkeep
#: (SPEC_NIGHTM3_LANES §5 P-2, ruling R-RW-5 as M ruled it on 2026-09-22:
#: upkeep receipts inside a hand-typed brief are `fired_via: manual`,
#: `triggered_by: morning-brief`). It is the dispatcher's canonical task id, so
#: it is ALSO the literal every rendered leg ends in and every job receipt and
#: the one `maintenance_run` carry under `triggered_by`. One home: the driver
#: below reads it, and `morning-briefing/SKILL.md` Step 0's rendered call names
#: this value (`run_phrase1_test` executes that block and compares).
MORNING_BRIEF_SURFACE = "morning-brief"


def maintenance_catch_up(workspace_root, surface, *,
                         now_iso: str | None = None,
                         mode: str | None = None,
                         runner=None) -> dict:
    """The upkeep a typed surface owes before it gathers.

    With `runner=None` (every production driver call today) this PLANS: it
    returns the dispatcher's catch-up plan and writes nothing at all. The
    pack carries it under `catch_up` and the surface's own prose executes it
    — one job at a time, in the plan's order, then ONE receipt — exactly the
    engine `cleanup/SKILL.md` Step 0 already drives.

    With a `runner` the whole thing runs here: the jobs in order, scored
    through the one predicate, then the single `maintenance_run` receipt
    carrying `fired_via: manual` and `triggered_by: <surface>`. That is the
    path the suite drives end to end, and the path a caller that already
    holds an executor (a test, a local operator script) takes.

    Never raises. A catch-up that cannot be planned must not cost the reader
    the surface they asked for, so every failure degrades to "no catch-up"
    with the reason on the return.
    """
    try:
        import maintenance_dispatcher as md

        # H-1: a caller that names a mode is obeyed; a caller that names none
        # gets the layer's answer about this book, not a blank.
        host = mode if mode is not None else resolved_host_mode(workspace_root)
        if runner is None:
            return md.catch_up_plan(workspace_root, surface, now=now_iso,
                                    mode=host)
        return md.run_catch_up(workspace_root, surface, runner=runner,
                               now=now_iso, mode=host)
    except Exception as exc:  # noqa: BLE001 — the surface outranks the upkeep
        return {"surface": surface, "triggered_by": None, "catch_up": False,
                "jobs": [], "held_sunday": [], "refused_container": [],
                "refused_line": "", "refused_reason": "", "refused_next": "",
                "host_mode": None, "reason": "error", "error": str(exc),
                "ran": [], "completed": [], "failed": [], "receipt": None}


# ---------------------------------------------------------------------------
# SPEC_SURFACES2_11c BRIEF2 2.2 items 2 and 3 — LINE TWO, AND THE ONE LINE
# ---------------------------------------------------------------------------
#
# Line two says what today is about, in the reader's own words. `day_intent`
# has stored it since BK1 ("tomorrow is about closing the Stone renewal",
# typed at the day-close) and `shared/EVENT_TYPES.md` has claimed since then
# that the morning surface reads it. Nothing read it. This is the read.
#
# STATED ORIGINS ONLY. `load_day_intent`'s default (`include_proposed=False`)
# is BK1's own fence: a `proposed` row is a guess the product made about the
# reader's day, and a guess rendered as fact on line two of the morning is
# exactly the thing the fence exists to stop. The default is taken here
# EXPLICITLY rather than inherited, so a future reader of this call can see
# which way it goes without opening the other module.
#
# Nothing when nothing is stated. Never "nothing on file" — an empty line
# two costs the reader nothing and a padded one costs them a line every day.
#
# WHY IT IS USER TEXT, AND WHAT THAT COSTS. The items are the customer's own
# sentence. `assert_number_leads` sees the line (it sits inside `lead_lines`,
# below the number, so the order fence reads the real composed order), but
# `assert_single_open_count` does NOT: a stated item may legitimately contain
# a digit — "close the 3 open Stone items" — and that digit answers the
# reader's question, not the plate's. Letting the count fence read it would
# let the customer's own sentence raise on their own brief. The pack marks
# the line as user text carrying a digit when it does, so a reviewer reading
# a receipt never has to guess why a figure on the brief was not reconciled.

#: Line two, in the voice `end_of_day.TOMORROW_STATED_LINE` uses at the other
#: bookend. Same shape, same separator, the other end of the same day.
BRIEF_DAY_INTENT_LINE = "Today is about {what}."

#: The template ends in a full stop, so a stated item that already ends in
#: its own terminal punctuation double-punctuates: "Today is about should we
#: renew Stone?." (review F-5, measured). The reader's own mark WINS — it is
#: their sentence and the shape of it is information — so the template's stop
#: is dropped instead of theirs. Only these three count as terminal; a stated
#: item ending in "..." or a quote keeps the stop it needs.
DAY_INTENT_TERMINALS = (".", "?", "!")

#: What the stated items are joined with — `end_of_day`'s own separator, so
#: the evening's "Tomorrow is about A · B" and the morning's "Today is about
#: A · B" are the same sentence about the same day.
DAY_INTENT_JOIN = " · "

_DIGIT_RE = re.compile(r"\d")


def brief_day_intent_line(workspace_root, *, today: str) -> dict:
    """`{"line", "items", "contains_digit"}` for line two, or an empty line.

    `today` is the workspace-local `YYYY-MM-DD` the brief already resolved
    (`tz.localize_date`) — passed in rather than re-derived, so line two can
    never be about a different day than the rest of the brief.

    NEVER RAISES (2.2 item 5). A store this cannot read degrades to LINE
    ABSENT — one line missing from an otherwise true brief, never a
    `surface_failed` and never a brief built by hand.
    """
    out = {"line": "", "items": [], "contains_digit": False}
    try:
        import day_intent as _day_intent
        record = _day_intent.load_day_intent(workspace_root, today,
                                             include_proposed=False)
    except Exception:  # noqa: BLE001 — line absent, never a failed surface
        return out
    if not record:
        return out
    # The stored item is `{text, rank}` (`day_intent.normalize_items`). The
    # TEXT is the sentence the reader said and the only thing that renders;
    # the rank is their ordering and the list already arrives in it. Read
    # exactly as the day-close reads it (`end_of_day.compose_screen`), so the
    # two bookends cannot disagree about one record.
    items = [str(i.get("text") or "").strip()
             for i in (record.get("items") or []) if isinstance(i, dict)]
    items = [t for t in items if t]
    if not items:
        return out
    out["items"] = items
    what = DAY_INTENT_JOIN.join(items)
    line = BRIEF_DAY_INTENT_LINE.format(what=what)
    # The reader's own terminal mark wins over the template's full stop —
    # otherwise a day that is about "should we renew Stone?" reads "Today is
    # about should we renew Stone?." (review F-5). Their mark is kept, not
    # stripped: a question mark the reader typed is theirs, and the ask fence
    # below reads the composed line either way (see `build_morning_brief_pack`
    # — line two joins the fenced text as USER TEXT).
    if what.endswith(DAY_INTENT_TERMINALS) and line.endswith(what + "."):
        line = line[:-1]           # the template's stop, never the reader's
    out["line"] = line
    out["contains_digit"] = bool(_DIGIT_RE.search(out["line"]))
    return out


#: The one coaching line. A STATEMENT of what the reader themselves said,
#: in the brief's own voice — never a question, never a score, never a
#: comparison, and never a second character's voice.
BRIEF_COACHING_LINE = ("If {behaviour} comes up today, you said you would do "
                       "it rather than defer it.")


def brief_coaching_line(workspace_root) -> str:
    """The one coaching line, or "".

    THREE gates, and the ORDER is the point:

      1. the SHAPE (`coaching_doors.coaching_shape`) — an `observed` seat has
         not opened the coaching door and never gets this line WHATEVER the
         render switch says. The switch is a preference; the shape is
         consent, and a preference may not buy consent;
      2. the render SWITCH (`brief_settings` `coaching_line`) — the seat that
         opened the door still gets to say "not on my brief". COACH2's
         `_apply` flips it on with the chosen door and back off with `turn
         off coaching`; this lane only READS it;
      3. a BEHAVIOUR on the coaching object — the line is about a named
         thing or it is not a line. No behaviour, no sentence; there is
         nothing honest to put in the slot.

    NEVER RAISES (2.2 item 5): every read degrades to line absent.
    """
    try:
        import coaching_doors as _doors
        if _doors.coaching_shape(workspace_root) == _doors.SHAPE_OBSERVED:
            return ""
        from brief_settings import SURFACE_BRIEF, settings_for_fire
        settings, _notes = settings_for_fire(workspace_root, SURFACE_BRIEF)
        if str((settings or {}).get("coaching_line") or "") != "on":
            return ""
        behaviour = str((_doors.relationship(workspace_root) or {}).get(
            "behaviour") or "").strip()
    except Exception:  # noqa: BLE001 — line absent, never a failed surface
        return ""
    if not behaviour:
        return ""
    return BRIEF_COACHING_LINE.format(behaviour=behaviour)


class BriefCoachingLeakError(RuntimeError):
    """A coaching-tier fingerprint reached the morning brief on a fire that
    did not declare itself a coaching surface."""


#: What the brief calls itself to the coaching leak scan on an ordinary fire.
#: It is deliberately a name `coaching_confidential.NON_COACHING_SURFACES_NAMED`
#: already lists: the scan fails CLOSED, so this tag buys nothing and is only
#: here so the declaration is a value a reader can see rather than a `None`.
BRIEF_SURFACE_TAG = "morning-brief"

#: What the brief calls itself on the ONE fire that renders the coaching line.
#: Its home is `coaching_confidential.COACHING_SURFACES`; the suite pins that
#: this spelling is in that set, so deleting it there reds here.
BRIEF_COACHING_SURFACE_TAG = "morning-brief-coaching-line"


def brief_surface_tag(coaching_line: str) -> str:
    """The surface this fire declares to the coaching leak scan.

    THE DECLARATION IS PER FIRE, NOT PER SURFACE, and that is the whole
    point (review F-3, ruling R-10). A brief that renders no coaching line is
    an ordinary non-coaching surface and the fail-closed scan applies to it
    in full. A brief that renders the one stated line — which only happens
    behind `brief_coaching_line`'s three gates, i.e. for a seat that walked
    the chosen door and left the switch on — declares the coaching tag for
    that fire, because that seat asked for that sentence on that surface.

    WHAT THE DECLARATION BUYS, AND WHAT IT DOES NOT (review R1, fix round 2):
    it is read by `brief_coaching_scan_target` and it exempts ONE SENTENCE.
    It is no longer handed to the scan as the surface, because a coaching
    surface returns early and that switched the scan off for the whole page.
    """
    return BRIEF_COACHING_SURFACE_TAG if coaching_line else BRIEF_SURFACE_TAG


def brief_coaching_scan_target(text: str, coaching_line: str) -> tuple:
    """`(text_to_scan, surface)` for the coaching leak scan — the declaration
    covers ONE SENTENCE, never the page.

    REVIEW R1, FIX ROUND 2, AND THE MEASUREMENT BEHIND IT. Fix round 1
    declared the coaching tag for the whole fire and handed that tag to
    `assert_no_coaching_leak`, which returns early on a coaching surface. So
    on exactly the mornings that carry coaching content the scan was off for
    the WHOLE brief: the re-verifier planted a MARKED coaching note in
    another slot of a coached seat's fire and it reached the reader. The
    declaration was granted for one stated sentence and it now covers one
    stated sentence.

    So the brief always scans as the ordinary, non-coaching surface it is,
    and the ONE declared line is BLANKED out of the text first — the same
    move the ask fence makes over the reader's own spans
    (`plate_view._blank_user_spans`): take the declared span out, hold every
    word that is left to the ordinary rule. The line is composed into this
    text twice (`lead_lines` and `_composed`), so every occurrence goes.

    `brief_surface_tag` is still the declaration of record and still
    load-bearing — it is what decides whether there IS a declared line to
    blank. Stub it and the coached seat's own line is scanned and refused.
    """
    if not text:
        return "", BRIEF_SURFACE_TAG
    if (coaching_line
            and brief_surface_tag(coaching_line)
            == BRIEF_COACHING_SURFACE_TAG):
        text = text.replace(coaching_line, "")
    return text, BRIEF_SURFACE_TAG


def assert_no_coaching_leak(text: str, *, surface: str,
                            where: str = "morning-brief") -> None:
    """PROFILE1's fail-closed coaching scan, run over the composed brief.

    WHY THIS EXISTS. PROFILE1 shipped the promise — "whatever you say in a
    coaching conversation stays in its own tier: not in a shared document,
    not on your brief" — and a scan that fails closed to keep it. The scan
    had exactly ONE caller in the tree (`docx_leak_scanner.py`, i.e.
    document artifacts). The chat path ran none, so the brief's coaching
    line passed by the ABSENCE of a gate rather than by a rule, and a future
    writer pasting a MARKED coaching note into any brief slot would have
    reached the reader untouched.

    Called exactly as `docx_leak_scanner` calls it — the same two names, the
    same `if not is_coaching_surface(surface)` direction, the same
    ImportError tolerance — so there is one reading of the tier and not two.
    """
    try:
        from coaching_confidential import (is_coaching_surface,
                                           scan_for_coaching_leak)
    except ImportError:  # pragma: no cover — partial-update tolerance
        sys.stderr.write(
            "[surface_drivers] WARN: coaching_confidential module missing — "
            "the coaching-tier scan did NOT run on the morning brief.\n")
        return
    if is_coaching_surface(surface):
        return
    findings = scan_for_coaching_leak(text or "")
    if findings:
        names = sorted({str(f.get("name")) for f in findings})
        raise BriefCoachingLeakError(
            f"{where}: coaching-tier content reached a brief that did not "
            f"declare itself a coaching surface (found {names!r}). PROFILE1's "
            f"promise: what is said in a coaching conversation is not on the "
            f"brief. The one declared exception is the stated line behind "
            f"`brief_coaching_line`'s three gates.")


def brief_explain_once_line(workspace_root) -> str:
    """The first-week explain-once line, consumed INSIDE the pack (2.2 item
    4) so it lands above the three fences instead of below them.

    It was prose-wired: `morning-briefing/SKILL.md` told the model to call
    `explain_once.consume` after the pack, which put the one sentence the
    product says about itself outside every fence the pack runs. Code at the
    write — the pack consumes it, the pack fences it, the prose narrates it.

    `consume` is idempotent by construction (it writes `explain_once_shown`
    in the same call), so calling it here keeps "once" true and moves nothing
    else. NEVER RAISES: an unreadable ledger is one line absent.
    """
    try:
        from explain_once import consume
        return str(consume(workspace_root, "morning-briefing") or "")
    except Exception:  # noqa: BLE001 — line absent, never a failed surface
        return ""


# ---------------------------------------------------------------------------
# SPEC SURFACEFIX1 5.3 / amendment E-5 — A FIRE THAT CANNOT RENDER SAYS SO
# ---------------------------------------------------------------------------
#
# On 2026-09-13 the day-close driver raised inside `json.dumps` and the fire
# printed the traceback: the module's own filename, the pack's key path, a
# helper's dotted name and two absolute Windows paths, on the customer's
# screen (attended test, leak 10). Then the chat rebuilt the day-close by
# hand from the ledger — the "canonical path fails, freelance substitutes"
# class that has cost this product more than any single bug.
#
# The cause is fixed at the write (the transport `str()` and `default=str` on
# both dumps). This is the FLOOR under that fix, and it is deliberately not
# specific to serialisation: any exception on the way to a surface becomes
# ONE plain sentence the reader can act on, plus a receipt nobody has to read.
#
# WHAT THE SENTENCE MAY NOT CONTAIN: a path, a module, a function, an
# exception class, a seq, a wire id, the word "traceback". The reader is told
# what happened in their own terms and what to say next; everything else is
# on the receipt, where the health check and the operator report read it.

#: Surface keys for the failure path. One per fire that has a retry phrase.
SURFACE_FAILED_EOD = "end-of-day"
SURFACE_FAILED_BRIEF = "morning-brief"
#: The Friday wrap (REVIEW_NIGHT11C H-4, 2026-09-15). It had no failure
#: vocabulary at all: a wrap that died left a receipt-less silence the
#: watchdog reads as a job that never fired.
SURFACE_FAILED_WRAP = "friday-wrap"

#: The sentence, per surface. Plain words, a retry phrase, nothing else.
SURFACE_FAILED_LINES = {
    SURFACE_FAILED_EOD:
        "End of Day could not render tonight — say `end of day` to retry.",
    SURFACE_FAILED_BRIEF:
        "The morning brief could not render — say `brief me` to retry.",
    SURFACE_FAILED_WRAP:
        "The weekly wrap could not render — say `weekly recap` to retry.",
}

#: The receipt's status word, kept for ONE RELEASE beside the type. Every
#: failure receipt already on disk is a `pack_run` carrying this status and
#: both spellings are pinned (`tests/run_eod2_test.py [5]`), so no reader has
#: to change on the day the writer does. EOD2 registered `surface_failed` in
#: `receipts.RECEIPT_TYPES` on the two tasks that have a failure path and on
#: the day-close's counting bucket, so the type is real vocabulary.
#: (MF-11c-2, night 11c trial merge: the writer now spells the type.)
SURFACE_FAILED_STATUS = "surface_failed"

#: Which canonical task each surface's receipt is written under.
_SURFACE_FAILED_TASKS = {
    SURFACE_FAILED_EOD: "past-meetings",
    SURFACE_FAILED_BRIEF: "morning-brief",
    SURFACE_FAILED_WRAP: "friday-wrap",
}


def log_surface_failed(workspace_root, surface: str, exc: BaseException, *,
                       mode: str = "scheduled", now_iso=None) -> Optional[dict]:
    """Record that a fire could not render, and return the receipt.

    The exception's CLASS NAME goes on the receipt (it is the one datum that
    tells a maintainer where to look and it never reaches a screen); the
    message does not — an exception message routinely carries the path, id or
    payload that made it fail, and a receipt is read back by the operator
    report and the health check, both of which render.

    Never raises. A fire that failed must not fail again on the way to saying
    so — that is how a traceback reached the screen in the first place.
    """
    try:
        from receipts import log_receipt
        return log_receipt(
            workspace_root, _SURFACE_FAILED_TASKS.get(surface, "past-meetings"),
            # MF-11c-2 (night 11c trial merge) — the receipt names what
            # happened: a failed surface, not a pack run with a sad status.
            receipt_type="surface_failed",
            status=SURFACE_FAILED_STATUS,
            fired_via=("manual" if mode == "manual" else "scheduled"),
            surfaced=0,
            extra_data={"surface": surface,
                        "failure_class": type(exc).__name__},
            now=now_iso)
    except Exception:  # noqa: BLE001 — the sentence still gets printed
        return None


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _pointer_line(count: int) -> str:
    """FB-20's ONE queue-pointer line — the brief's entire adjudication
    affordance now that the card is gone. Drop-empty at zero: a brief with
    nothing queued says nothing about the queue (never "0 things need your
    eyes", never an all-clear pad)."""
    if count <= 0:
        return ""
    noun = "thing needs" if count == 1 else "things need"
    return f"{count} {noun} your eyes — say `staff meeting`."


# Unconfirmed-block confirm cluster (W4b).
_CONFIRM_VERBS = ["mine", "theirs to [name]", "make task", "drop"]
_DUP_VERBS = ["merge", "keep both", "drop"]
# BUG-8330 item 13 — the triage Unowned lane's verbs (all registered; same
# family as the confirm tail): claim it, route it, or let it go.
_UNOWNED_VERBS = ["mine", "theirs to [name]", "drop"]
# pending_review rows outside the 7d escalation pin (explicit confirm-shaped
# actions only — an explicit click IS confirmation).
_PENDING_VERBS = ["resolved", "drop", "not mine"]
# SUB1 D6 — child rows get the standard per-kind dropdown MINUS the one verb
# that doesn't apply to a child: `never track this` (suppression rules key on
# capture shape — children aren't captures) stays parent-level. Everything
# else — Done, Later…, Drop, Not mine, Turn into a task / Promote, Skip —
# works on
# a child with zero special-casing (children are real commitments).
# (`add to my list` was the other parent-level carve-out until MLK1 retired
# the verb entirely — no row emits it now.)
_CHILD_PROMISE_VERBS = ["resolved", "push to [date]", "drop", "not mine",
                        "make task", "skip"]
_CHILD_TASK_VERBS = ["resolved", "push to [date]", "drop", "promote", "skip"]

_REDUCED_REASON = ("Fewer options — the owner is unconfirmed; clicking Done, "
                   "Drop, or Not mine confirms it.")

# FB-15 — the daily Waiting On chat (CTS1 Surface 1; orchestrator-commitments)
# row verb sets, post-FB-17. Delegated tasks (owner != user, effective kind
# `task`) are CRU-INELIGIBLE, so they get NO PRE-STAGED chase draft. A fully
# resolved, pre-staged chase still rides chase_rows like any other email row.
#
# WG1-A D-A4 (M ruling 2026-07-20, big-test row 13b): `nudge` is the delegated
# row's ruled PRIMARY verb, and it is CONNECTOR-FREE at render — compose-on-
# CLICK, not compose-at-render. The driver emits the bare `nudge` action id;
# apply-choices composes the chase draft (email-writer chain, draft posture)
# only when the row is tapped, so this read-only driver still touches no
# connector and scheduled fires stay connector-free. It leads the set so the
# grammar promotes it as the visible primary button. (Train-merge note
# 2026-07-21: the widget-batch interim used the bare `draft` verb for the same
# compose-on-demand behavior; D-A4's named `nudge` verb supersedes it — same
# dispatch chain, grammar-registered id. `add to my plate` is CTS1FIX D5,
# post-dating the WG1-A build; `add to my list` is retired — MLK1.)
#
# The pending_review / unowned confirm tail asks an OWNERSHIP question, so it
# carries the ownership cluster (orchestrator-commitments §452 + § "Confirm
# section actions" §1027-1033), not the opaque person-record `confirm` verb:
#   - `mine`             — CLAIMS (commitment_state.confirm_commitment_owner:
#                          sets the owner AND clears any pending_review flag)
#   - `theirs to [name]` — ROUTES (reassign_commitment, confirmed=True)
#   - `drop`             — closes an item that is nobody's (§452 dismissal)
#   - `snooze 3d`        — the deferral tail (UXR1 D1)
# `make task` is deliberately omitted: reclassifying a commitment to a task in
# place while its owner is still unknown is incoherent — `add to my plate`
# (make it MY task) is the coherent task path here. Dispatch is keyed per
# row-id, so the ONE shared cluster preserves the genuinely-different behavior
# between the two classes (mine clears the pending_review flag only where one
# is present; on a bare-unowned row it simply stamps the owner).
#
# UXR1 D1 (M ruling 2026-07-21): the confirm tail SLIMMED from five verbs to
# four. Removed from EMISSION only — `not relevant` (the dishonest twin of
# `drop` on this row: hides it 60d while the item stays open + unconfirmed)
# and `add to my plate` (the redundant twin of `mine`: both land it on My
# Plate; `mine` keeps the counterparty and the chase, the correct default for
# a captured promise). Both wire ids stay registered in verb_taxonomy and
# dispatch unchanged — old persisted widgets carrying the 5-verb rows must
# still apply. Under WG1-A's ≤4 rule the slimmed row renders as 4 buttons,
# no dropdown.
_DELEGATED_VERBS = ["nudge", "mark received", "snooze 3d", "add to my plate"]
_REVIEW_VERBS = ["mine", "theirs to [name]", "drop", "snooze 3d"]

# FB-plumbing item 6 — My Plate (CTS1 Surface 2; orchestrator-my-plate) row
# verb sets. The driver renders the CONNECTOR-FREE row classes deterministically
# (the counterparty-unresolved Promised fixup rows + the whole Personal group);
# the email-shaped status drafts for counterparty-RESOLVED Promised rows are
# connector-dependent (email-writer) so the orchestrator composes them and
# passes them verbatim as `status_rows`, exactly as waiting-on's `chase_rows`.
# Counterparty-unresolved Promised rows (Bug #103, the 49 orphaned promises):
# the reassign/make-task fixup rides two existing verbs; NEVER auto-demoted.
_MP_UNRESOLVED_VERBS = ["reassign to [name]", "make task", "push to [date]",
                        "resolved", "drop", "snooze 3d"]
# Personal (owner-me own work) — no drafts; the standard owner-me act verbs.
# (`add to my list` was in the FB-plumbing build of this set; retired by MLK1
# before this driver merged — removed at the train merge, never emitted.)
_MP_PERSONAL_VERBS = ["resolved", "push to [date]", "prep deep work",
                      "promote", "snooze 3d"]
# BUG-8330 item 5 — the RESIDUAL Promised class: a counterparty-RESOLVED
# promise the orchestrator drafted no status email for. These rows were
# ABSENT from My Plate entirely (not capped — absent); they render
# deterministically now with the owner-me act verbs (no reassign — the
# counterparty is known; no draft — that stays connector-side).
_MP_RESIDUAL_VERBS = ["resolved", "push to [date]", "drop", "snooze 3d"]
# Default Personal-group cap (CTS1 §4.2 — `my-plate` skill config
# `personal_cap`, default 7); the tail line points at `show my plate`.
_MP_PERSONAL_CAP = 7
# Default Promised-group cap (BUG-8330 item 5): explicit, with the same
# footer tail — the old behavior was an implicit cap of "however many rows
# the orchestrator happened to compose", with the overflow invisible.
_MP_PROMISED_CAP = 7


class MountStaleError(RuntimeError):
    """A surface driver refused to render because the substrate view is stale.

    SPEC SYNC1 A4, extended to the RENDER path. `preflight_freshness` was wired
    into the maintenance orchestrator only, so a driver rendering from a stale
    sandbox mount had no gate at all. Two observed outcomes, and the silent one
    is worse:

      * loud — a stale projection resurfaces a token that was already fixed on
        disk, and the render dies inside a downstream validator with an error
        naming the wrong thing (a leak, when the substrate is clean);
      * silent — the surface PUBLISHES a stale page: stale rows, stale counts,
        no indication anywhere that the view is behind.

    So the drivers refuse up front and produce NO SURFACE and NO RECORD: no
    page, no receipt, no page-set, no events. That enumeration is the claim,
    and it is what the pins assert — not "writes nothing", which would be
    false. The refusal path DOES touch disk, deliberately and only through the
    sanctioned alarm machinery:

      * `preflight_freshness` writes the `.mount_stale.json` sidecar beside
        events.jsonl (a sidecar, never an events append — an append through a
        stale view is the clobber vector itself) and renders the alert through
        `alarm_artifacts.write_alert`;
      * `substrate_alarm_lines` calls `alarm_artifacts.sweep_alerts`, which
        archives any alert whose condition has already resolved.

    All three are alarm artifacts about the refusal, never workspace state, and
    routing through them is why this class hand-authors no prose of its own.
    `lines` is `substrate_health.substrate_alarm_lines`, the same plain-English
    syncing vocabulary the health check and the morning brief already print.

    One honest edge: `preflight_freshness` retries ×3 with backoff and
    `substrate_alarm_lines` re-probes without retrying, so a staleness that
    clears between the two probes yields an empty `lines`. The fallback below
    handles it by reporting the machine detail; it is not the impossible case
    an earlier draft of this docstring implied.
    """

    def __init__(self, lines, detail=None):
        self.lines = [str(ln) for ln in (lines or [])]
        self.detail = dict(detail or {})
        # Never invent prose. With no alarm line to speak (a degenerate case —
        # a not-ok preflight always leaves at least one), the machine detail is
        # reported verbatim rather than replaced by a sentence nobody wrote.
        super().__init__("\n".join(self.lines) or json.dumps(self.detail))


def refuse_if_mount_stale(workspace_root) -> None:
    """The render-path preflight. Returns on a healthy view; raises
    `MountStaleError` on a stale one, BEFORE the caller reads or renders
    anything.

    Deliberately the FIRST statement of every driver entry point that renders
    substrate — a preflight that runs after the view is built has already paid
    for the stale read it exists to prevent.
    """
    from substrate_health import preflight_freshness, substrate_alarm_lines
    result = preflight_freshness(workspace_root)
    if result.get("ok"):
        return
    raise MountStaleError(substrate_alarm_lines(workspace_root),
                          result.get("detail"))


def _now_iso() -> str:
    return _clock_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _age_days(ts: str, now_iso: str) -> int | None:
    from event_time import parse_ts

    a, b = parse_ts(ts), parse_ts(now_iso)
    if a is None or b is None:
        return None
    return max(0, int((b - a).total_seconds() // 86400))


def _due_phrase(due, now_iso: str, ws) -> str:
    """The queue row's due phrase (SPEC TOMFILT1 §2, golden-pinned) —
    delegates to `due_reanchor.render_due_phrase`, the ONE renderer the
    slipped line and the tomorrow block also call. Re-anchored to `now_iso`
    on every call: past-due always carries its age ("due Aug 6 — 19 days
    ago"), never a bare "overdue since Aug 6" that goes stale the moment it
    sits on screen.

    DATE1 (ATTENDED_TEST_v5.29.0 B2.1 class; REVIEW_HYGIENE9 R4). `anchor`
    is the WORKSPACE's calendar day (`tz.localize_date`), never a raw slice
    of `now_iso`'s own ISO string. `due_reanchor.render_due_phrase` treats
    its `anchor` argument as "today" verbatim — TOMFILT1's own docstring
    says both `render_due_phrase` callers must already resolve it
    workspace-local — and these seven call sites were the ones that never
    did: `render_due_phrase(due, now_iso)` sliced a UTC `now_iso`'s own
    first 10 characters, so at 18:00 Pacific (01:00 UTC the next day) a row
    due TOMORROW read "due today" and a row due TODAY read "due — 1 day
    ago", on the same machine where `plate_view`'s own copy of this phrase
    (fixed at HYGIENE9 the same way) read the right day.

    DATE1 fix round 1 (REVIEW_DATE1 F-4) — `ws` is now REQUIRED, not
    `ws=None`. The additive default silently restored the pre-DATE1
    UTC-slice behaviour for any caller who forgot it, and the structural
    "exactly seven call sites" pin (`run_cutplate_test.py` [10]) could only
    ever count calls that DO pass `ws` — an eighth, unanchored site left
    the count at seven and shipped green. A caller with no real workspace
    (`run_tomfilt1_test`'s golden) now passes `ws=None` EXPLICITLY, which is
    byte-identical behaviour (`tz.localize_date` degrades the same way with
    an explicit `None` as with an absent kwarg) but makes the omission a
    conscious choice at every call site rather than a name nobody typed."""
    from due_reanchor import render_due_phrase
    from tz import localize_date

    anchor = localize_date(now_iso, workspace_path=ws) or now_iso
    return render_due_phrase(due, anchor)


def _people_by_id(ws: Path) -> dict:
    """id -> person record from entities.json.

    Defensive: a missing / corrupt entities.json yields an empty map (the
    caller then falls back to owner_external / a bare 'delegated' tag and the
    `add email then send` recovery verb)."""
    try:
        raw = (ws / "_hq" / "data" / "entities.json").read_text("utf-8")
        people = (json.loads(raw) or {}).get("people") or []
    except Exception:
        return {}
    out: dict = {}
    for p in people:
        pid = p.get("id")
        if pid:
            out[pid] = p
    return out


# RRF1 — the checkable clause class + the resolve verdict live in the shared
# review_reasons module now (BUG-8330 item 4: the same verdict also drives
# the loader's read-side gating and the queue's reason-scoped batch verb —
# render and gating can no longer disagree). This alias keeps the local name.
from review_reasons import NO_PERSON_RE as _RR_NO_PERSON_RE  # noqa: E402


def _display_review_reason(ws: Path, raw, cache: dict) -> str:
    """Render-time overlay for the frozen "counterparty 'X' has no person
    record" review_reason clause (RRF1 + UXC1 plain-language ruling
    2026-07-21). The STORED clause is never shown raw — "counterparty" is
    banned vocabulary (VOICE_CALIBRATION glossary): an unresolved name
    renders as "'X' isn't in your contacts yet"; if X NOW resolves to a
    person record (entity_resolve ladder — the same one brain_proposals
    uses; A6: one home for the matcher) it renders as "'X' — contact added
    ✓" so the row stops telling the CEO to do something they already did.
    Every other clause class is re-said as ONE WHOLE SENTENCE by the shared
    composer (`review_reasons.render_clause`, HYGIENE9 (c)/(d2)) — it used
    to pass through verbatim, which is how "extraction confidence 0.5 below
    threshold" reached the held queue's "why it's here" line at v5.27.0.
    The composer never returns a score or a wire id.

    DISPLAY-ONLY: the STORED review_reason is a gating input (cru_match /
    commitment_dedup / confirm_flow / identity_reconcile read it) and is
    never written back — this rewrites the rendered string only.

    `cache` is a per-driver-call memo (name -> bool): each distinct name
    costs at most one resolve_all call per render pass (resolve_all re-reads
    entities.json internally, so the memo IS the read fence), and reasons
    with no eligible clause never load the resolver at all.
    """
    from review_reasons import render_clause

    raw = str(raw)
    if "has no person record" not in raw:
        return "; ".join(r for r in (render_clause(c) for c in raw.split(";"))
                         if r)
    out = []
    for clause in (c.strip() for c in raw.split(";")):
        m = _RR_NO_PERSON_RE.match(clause)
        if not m:
            rendered = render_clause(clause)
            if rendered:
                out.append(rendered)
            continue
        name = m.group(1)
        # Shared verdict (review_reasons._resolves_to_person semantics via
        # clause_still_holds): the same memo dict, the same resolver, the
        # same failure posture — a resolver failure reads as unresolved and
        # never breaks a surface render.
        from review_reasons import _resolves_to_person
        out.append(f"'{name}' — contact added ✓"
                   if _resolves_to_person(ws, name, cache)
                   else f"'{name}' isn't in your contacts yet")
    return "; ".join(out)


def _owner_display_name(ev: dict, people_by_id: dict) -> str | None:
    """Resolve the delegated-task owner's display name for the row tag.

    A delegated row is owner != M by definition, so it should name who: read a
    display name carried on the event first (legacy `owner_display` /
    `owner_name`), else resolve `owner_id` (multi-shape via `_commitment_field`)
    through entities.json, else fall back to the raw `owner_external` string.
    Returns None when nothing resolves — the caller then renders bare
    'delegated'."""
    from cru_match import _commitment_field

    d = ev.get("data") or {}
    for k in ("owner_display", "owner_name"):
        v = (d.get(k) or ev.get(k) or "")
        if isinstance(v, str) and v.strip():
            return v.strip()
    owner_id = _commitment_field(ev, "owner_id")
    rec = people_by_id.get(owner_id) if owner_id else None
    if rec:
        name = (rec.get("name") or rec.get("canonical_name") or "").strip()
        if name:
            return name
    ext = d.get("owner_external") or ev.get("owner_external") or ""
    if isinstance(ext, str) and ext.strip():
        return ext.strip()
    return None


def _owner_email(ev: dict, people_by_id: dict) -> str | None:
    """The delegated owner's first actionable email, or None.

    `draft` is a send-class verb (chat_output_renderer Gate 6 / Bug #44): a row
    that exposes it MUST carry a valid To: address or the renderer refuses to
    ship it. Resolve `owner_id` -> the person record's canonical `emails` array
    (via `people_writer.get_person_emails`); if `owner_external` is itself an
    email, use that. None -> the caller degrades `draft` to the canonical
    `add email then send` recovery verb (no To: required)."""
    from cru_match import _commitment_field
    from people_writer import get_person_emails

    owner_id = _commitment_field(ev, "owner_id")
    rec = people_by_id.get(owner_id) if owner_id else None
    if rec:
        emails = get_person_emails(rec)
        if emails:
            return emails[0]
    ext = ((ev.get("data") or {}).get("owner_external")
           or ev.get("owner_external") or "")
    if isinstance(ext, str) and "@" in ext and "." in ext.split("@")[-1]:
        return ext.strip()
    return None


def build_commitment_triage_view(workspace_root, *, now_iso: str | None = None) -> dict:
    """The full commitment-triage data view (SKILL.md Steps 1-2, mechanized):
    canonical loader + bucket export + escalation split + age sections +
    per-row context tags + per-kind verb sets. Pure read."""
    from commitment_activity import derive_commitment_movement
    from chat_output_renderer import COMPOSED_FIELDS_KEY
    from commitment_state import (BUCKET_UNCONFIRMED, UNCONFIRMED_LANE,
                                  UNCONFIRMED_SECTION_LABEL,
                                  UNTITLED_PLACEHOLDER, bucket_of,
                                  commitment_kind, count_commitments,
                                  stale_tasks, unconfirmed_slices)
    from confirm_flow import select_unconfirmed_escalation, unconfirmed_classes
    # WALKFIX1 Item E: `_is_pending_review` is deliberately NOT imported here
    # any more. This view partitions by `commitment_state.bucket_of` — THE
    # predicate `count_commitments` counts with — and importing a second way to
    # ask the same question is how the headline and the rendered rows ended up
    # partitioned by two functions that only happened to agree.
    from cru_match import load_open_commitments
    from event_time import event_time
    from primary_user import resolve_primary_user

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    # DATE1 fix round 1 (REVIEW_DATE1 F-2) — resolve the workspace-local
    # calendar day ONCE per render, not once per row. `_due_phrase` used to
    # call `tz.localize_date` (which `json.load`s entities.json on every
    # miss) inside this view's per-row loops — 309 calls on one triage
    # render. `anchor` is a date-only string ("YYYY-MM-DD"); handed back
    # into `_due_phrase` as its `now_iso` it hits `localize_date`'s own
    # date-only passthrough (no second tz load), so behavior is unchanged.
    # Also closes REVIEW_DATE1 F-1's class for `count_commitments`'s own
    # `overdue` headline (commitment_state.py:1318) — dead for display on
    # this view today (see BUILD record) but no longer independently wrong.
    # seam round (DATE1 x ONEPLATE1 merge finding, F-16) — one resolve for
    # both `anchor` and the headline's instant; see `_hoisted_anchor_and_instant`.
    anchor, _lnow_hoist = _hoisted_anchor_and_instant(ws, now_iso)
    events_path = _events_path(ws)
    opens = load_open_commitments(events_path)
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    movement = derive_commitment_movement(events_path)
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=anchor, movement=movement)
    stale_ids = {row.get("commitment_id") or row.get("id")
                 for row in stale_tasks(opens, now_iso, movement=movement)}
    esc = select_unconfirmed_escalation(opens, now_iso)
    esc_ids = {row["commitment_id"] for row in esc["pin"]}
    propose_drop_ids = {row["commitment_id"] for row in esc["propose_drop"]}

    def _cid(ev) -> str:
        d = ev.get("data") or {}
        return d.get("id") or f"commitment_seq_{ev.get('seq')}"

    # SUB1 D6 — the family nests: sub-items render INSIDE their parent's row
    # (the existing sub_items shape), never as their own top-level rows.
    # Pagination is family-atomic structurally: paginate_data_view slices
    # top-level items only, so a family that doesn't fit moves whole to the
    # next page (a 12-child family alone on an oversized page degrades via
    # the transport's over_budget flag rather than splitting). Orphan
    # children (parent closed — the cascade crash window) partition
    # top-level and render as ordinary rows with a "was part of" note.
    from cru_match import partition_subitems
    top_level, sub_level = partition_subitems(opens)
    subs_by_parent: dict = {}
    for ev in sub_level:
        subs_by_parent.setdefault(
            (ev.get("data") or {}).get("parent_id"), []).append(ev)

    display_n = 0
    sections: list[dict] = []
    rr_cache: dict = {}  # RRF1 per-render-pass memo (name -> resolves now)
    # BOARD1 — the projected event behind each rendered row, so a row's
    # bucket and kind are read from THE event through the canonical
    # predicates rather than re-derived from the row's rendered text.
    by_cid: dict = {_cid(ev): ev for ev in opens}

    def _stamp(row: dict, ev) -> dict:
        """Stamp the row with its headline bucket + effective kind (BOARD1).

        The one derivation: `commitment_state.bucket_of` is the SAME predicate
        `count_commitments` counts with, and `commitment_kind` is the same
        effective-kind projection every other surface reads. The board's tabs
        and pinned strips partition on these two keys, so a tab's membership
        cannot drift from the tile above it. Unknown row keys are ignored by
        the widget renderer, so the widget path is byte-identical.
        """
        row["bucket"] = bucket_of(ev, user_id) if ev is not None else BUCKET_UNCONFIRMED
        row["kind"] = commitment_kind(ev) if ev is not None else "promise"
        return row

    # Unconfirmed block FIRST (v4.6.1 W4b escalation — never age-buried).
    # BUG-8330 item 13: rows whose ONLY amber class is `unowned` do NOT pin
    # here — this block was the "pinned block on a bounded page" 145 unowned
    # rows sat in without draining. They drain through the dedicated Unowned
    # lane below (mine / theirs to [name] / drop). pending_review and
    # suspected-duplicate escalations keep their pin.
    pin_shown = pin_escalated = 0
    unconfirmed_section_index = None
    if esc["pin"]:
        rows = []
        for r in esc["pin"]:
            _pin_ev = by_cid.get(r["commitment_id"])
            if (_pin_ev is not None
                    and unconfirmed_classes(_pin_ev) == ["unowned"]):
                continue
            display_n += 1
            dup = bool(r.get("suspected_duplicate_of"))
            lead = ""
            if r["commitment_id"] in propose_drop_ids:
                lead = (f"sat unconfirmed for {r['days_unconfirmed']} days — "
                        f"drop it? · ")
            tag = (f"{lead}captured {r['days_unconfirmed']} days ago — still "
                   f"unconfirmed"
                   + (f" · {_display_review_reason(ws, r['review_reason'], rr_cache)}"
                      if r.get("review_reason") else ""))
            row = _stamp({
                "n": r["commitment_id"], "display_n": display_n,
                # WALKFIX1 Item E — a title-less row renders the shared
                # repair placeholder, not an empty card. The live case was an
                # unowned, undated scheduling row whose card came out blank.
                "name": r.get("title") or UNTITLED_PLACEHOLDER,
                "context_tag": tag,
                "actions": _DUP_VERBS if dup else _CONFIRM_VERBS,
            }, by_cid.get(r["commitment_id"]))
            if not (r.get("title") or "").strip():
                # The placeholder is the RENDERER talking about the record, so
                # it declares its own prose and keeps facing the whole scan.
                row[COMPOSED_FIELDS_KEY] = ["name"]
            rows.append(row)
        # The rows this strip renders, and how many of them the BUCKET
        # predicate calls unconfirmed. The difference is the crossing set (the
        # ownerless escalation — a verified non-bug), and it is counted here
        # so no surface has to guess at it later.
        pin_shown = len(rows)
        pin_escalated = sum(
            1 for row in rows if row.get("bucket") == BUCKET_UNCONFIRMED)
        # BUG-8330 item 13 × WALKFIX1 merge: with unowned-only rows skipped
        # above, the strip can be EMPTY while esc["pin"] was not — guard the
        # append and take the section index inside it, or the index points at
        # whatever section lands in this slot next.
        if rows:
            unconfirmed_section_index = len(sections)
            # `lane` is the STABLE identifier. The title carries a reconciliation
            # sentence and is therefore a label that may change; three shipped
            # suites used to find this block by matching its title string and all
            # three broke the moment it did. Unknown section keys are ignored by
            # both renderers, so this is invisible on the surface.
            sections.append({"title": UNCONFIRMED_SECTION_LABEL,
                             "lane": UNCONFIRMED_LANE,
                             "count": len(rows), "items": rows})

    # Age sections, oldest first; escalation-pinned rows excluded (no
    # double-surfacing). SUB1: top-level items only — children render nested.
    # INTAKE: EVERY unconfirmed-bucket row is excluded here, not just the
    # pinned ones — an unconfirmed extraction is not an open commitment, so it
    # does not belong in an age section. The labelled "Unconfirmed" pin block
    # above is the deliberate exception; the rest are pointed at the
    # needs-your-call queue by the pointer line below.
    #
    # WALKFIX1 Item E — the membership test here is `bucket_of`, THE bucketing
    # predicate `count_commitments` counts with, not a second read of the
    # pending_review flag. The two agree today, which is exactly why the
    # substitution is safe and exactly why it was never noticed that the
    # headline and the rendered rows were partitioned by two different
    # functions. One of them changing was the off-by-one class: a row counted
    # as confirmed by one predicate and skipped as unconfirmed by the other
    # renders NOWHERE while still being in the header's total.
    aged: list[tuple[int, dict]] = []
    unowned_aged: list[tuple[int, dict]] = []
    n_pending_unpinned = 0
    for ev in top_level:
        cid = _cid(ev)
        if bucket_of(ev, user_id) == BUCKET_UNCONFIRMED:
            if cid not in esc_ids:
                n_pending_unpinned += 1
            continue
        age = _age_days(ev.get("ts") or "", now_iso)
        # BUG-8330 item 13 — unowned rows get their OWN lane instead of
        # age-burying or pin-block accumulation. The daily confirm selector
        # only admits rows ≤7 days old and the escalation pin block sat on a
        # bounded page: 145 unowned rows mathematically never drained. Same
        # membership predicate as the Unowned tile (bucket_of), so the lane
        # can never disagree with the counter above it. Suspected duplicates
        # keep their pin (the merge adjudication needs its own verbs);
        # everything else unowned drains here, esc-pinned or not.
        if (bucket_of(ev, user_id) == "unowned"
                and not (ev.get("data") or {}).get("suspected_duplicate_of")):
            unowned_aged.append((age if age is not None else -1, ev))
            continue
        if cid in esc_ids:
            continue
        aged.append((age if age is not None else -1, ev))
    aged.sort(key=lambda t: -t[0])
    unowned_aged.sort(key=lambda t: -t[0])

    def _row(ev, age):
        nonlocal display_n
        display_n += 1
        cid = _cid(ev)
        d = ev.get("data") or {}
        kind = commitment_kind(ev)
        classes = unconfirmed_classes(ev)
        pending = "pending_review" in classes
        parts = []
        if age >= 0:
            parts.append(f"{age} days old" if age != 1 else "1 day old")
        parts.append(_due_phrase(d.get("due"), anchor, ws))
        parts.append("task (yours)" if kind == "task" else kind)
        if cid in stale_ids:
            parts.append("still on your plate?")
        if pending and d.get("review_reason"):
            parts.append(_display_review_reason(ws, d.get("review_reason"),
                                                rr_cache))
        # SUB1 D5/D6 — parent progress chip + orphan note (loader stamps).
        kids = subs_by_parent.get(cid) or []
        n_open_k = d.get("n_subitems_open")
        n_done_k = d.get("n_subitems_done")
        if kids or isinstance(n_open_k, int):
            total_k = (n_open_k or 0) + (n_done_k or 0)
            chip = f"sub-items {n_done_k or 0}/{total_k}"
            nxt = _next_open_child(kids)
            if nxt is not None:
                chip += f" · next: {nxt}"
            parts.append(chip)
        if d.get("parent_closed") and d.get("parent_title"):
            parts.append(f"was part of: {d['parent_title']}")
        row = _stamp({
            "n": cid, "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(parts),
            "actions": (_PENDING_VERBS if pending
                        else (_TASK_VERBS if kind == "task" else _PROMISE_VERBS)),
        }, ev)
        if pending:
            row["reduced_verbs_reason"] = _REDUCED_REASON
        # SUB1 D3 — the PROPOSE-closure line (never auto-close): renders on
        # the parent row when the last open child closed.
        if d.get("all_subitems_resolved"):
            row["annotations"] = ["all sub-items done — close it?"]
        # SUB1 D6 — nested child rows: id = the child's data.id VERBATIM
        # (identity contract, Stage B); per-kind dropdown minus the
        # non-child verbs. Only OPEN children render (done ones are the
        # chip's numerator).
        if kids:
            row["sub_items"] = []
            for k in kids:
                kd = k.get("data") or {}
                k_kind = commitment_kind(k)
                summary = kd.get("title") or "(untitled)"
                if kd.get("due"):
                    summary += f" — {_due_phrase(kd.get('due'), anchor, ws)}"
                row["sub_items"].append({
                    "id": _cid(k),
                    "summary": summary,
                    "actions": (_CHILD_TASK_VERBS if k_kind == "task"
                                else _CHILD_PROMISE_VERBS),
                    # BOARD1 — the CHILD's own effective kind. A child of a
                    # promise can itself be a task, so a reader that needs
                    # the kind must not inherit the parent's.
                    "kind": k_kind,
                })
        return row

    def _next_open_child(kids: list) -> str | None:
        """The step to name in the progress chip: the open child with the
        earliest parseable effective due, else the first in append order."""
        if not kids:
            return None
        best_ev, best_date = None, None
        for k in kids:
            kd = k.get("data") or {}
            try:
                kd_date = _dt.date.fromisoformat(str(kd.get("due"))[:10])
            except (ValueError, TypeError):
                kd_date = None
            if kd_date is not None and (best_date is None or kd_date < best_date):
                best_ev, best_date = k, kd_date
        pick = best_ev or kids[0]
        return (pick.get("data") or {}).get("title") or None

    # BUG-8330 item 13 — the UNOWNED lane, oldest first, ahead of the age
    # sections (these are the rows nothing else ever surfaces). Stored
    # `attribution_candidates` finally render: the capture paths write them
    # and no surface ever read one — proposing them here is the wire half of
    # that census entry (item 16).
    def _unowned_row(ev, age):
        nonlocal display_n
        display_n += 1
        cid = _cid(ev)
        d = ev.get("data") or {}
        parts = []
        if age >= 0:
            parts.append(f"{age} days old" if age != 1 else "1 day old")
        parts.append(_due_phrase(d.get("due"), anchor, ws))
        parts.append("no owner on record — whose is this?")
        cand_names = []
        for cand in (d.get("attribution_candidates") or [])[:3]:
            if isinstance(cand, str) and cand.strip():
                cand_names.append(cand.strip())
            elif isinstance(cand, dict):
                nm = (cand.get("name") or cand.get("display_name")
                      or cand.get("person_name") or "").strip()
                if nm:
                    cand_names.append(nm)
        if cand_names:
            parts.append("maybe: " + " / ".join(cand_names))
        return _stamp({
            "n": cid, "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(parts),
            "actions": list(_UNOWNED_VERBS),
        }, ev)

    unowned_rows = [_unowned_row(ev, age) for age, ev in unowned_aged]
    if unowned_rows:
        sections.append({"title": "Unowned — oldest first",
                         "count": len(unowned_rows), "items": unowned_rows})

    old_rows = [_row(ev, age) for age, ev in aged if age >= 30]
    new_rows = [_row(ev, age) for age, ev in aged if age < 30]
    if old_rows:
        sections.append({"title": "30+ days old", "count": len(old_rows),
                         "items": old_rows})
    if new_rows:
        sections.append({"title": "The rest", "count": len(new_rows),
                         "items": new_rows})

    # ONEPLATE1 / REVIEW_ONEPLATE1 F-5 — THE NUMBERS COME FROM THE PLATE.
    # This surface counted its own way: `count_commitments` over its own
    # input, WITHOUT the plate's observed-tier drop. It feeds the
    # `commitments` chat surface AND the BOARD1 artifact page, both of which
    # state an open count to the reader, so on a book carrying one observed
    # row it and the plate say two different totals for one book on one day
    # (ATTENDED_TEST_v5.29.0 Part A). Its ROWS and its tiles' meanings are
    # unchanged; this is the header and the five counters.
    h = _plate_headline(ws, now_iso=now_iso, fallback=counts["headline"],
                        local_instant=_lnow_hoist)
    counters = [
        {"label": "Open", "value": h["total"]},
        {"label": "You owe", "value": h["you_owe"]},
        {"label": "Owed to you", "value": h["owed_to_you"]},
        {"label": "Unowned", "value": h["unowned"]},
        {"label": "Unconfirmed", "value": h["unconfirmed"]},
    ]
    # SUB1 D6 — tiles unchanged in shape (values are top-level per D2); when
    # sub-items exist the HEADER appends the additive key — never a new tile
    # that implies a fifth bucket.
    header = f"Commitment triage — {h['total']} open, oldest first"
    if h.get("subitems_open"):
        header += f" (+{h['subitems_open']} sub-items)"
    view = {
        "source_skill": "commitment-triage",
        "header": header,
        "counters": counters,
        "sections": sections,
    }

    # WALKFIX1 Item E — THE unconfirmed derivation, computed ONCE, feeding
    # every surface that says an unconfirmed number. The tile keeps the queue
    # total unchanged; the section header and the quick-read read off this.
    slices = unconfirmed_slices(queue_total=h.get("unconfirmed"),
                                shown=pin_shown, escalated=pin_escalated)
    view["unconfirmed_slices"] = slices
    if unconfirmed_section_index is not None:
        sections[unconfirmed_section_index]["title"] = slices["section_title"]

    # INTAKE — the unconfirmed extractions this surface deliberately does NOT
    # render as rows still get one honest line saying where they went. Only
    # the ones outside the labelled pin block: those already have rows.
    # Drop-empty at zero (never "0 unconfirmed" padding). The sentence now
    # names what its number is a slice OF, from the same derivation.
    if n_pending_unpinned:
        pointer = slices.get("quick_read")
        if not pointer:
            noun = ("extraction waiting" if n_pending_unpinned == 1
                    else "extractions waiting")
            pointer = (f"{n_pending_unpinned} unconfirmed {noun} — say "
                       f"`needs your call` to clear them.")
        view["pointer"] = pointer
        # `quick_read` is the key BOTH renderers (markdown + widget) actually
        # print; `pointer` is the machine-readable twin the tests pin.
        view["quick_read"] = pointer

    # WALKFIX1 Item E — the arithmetic, on the record. Everything below comes
    # off the SAME bucketing pass, so the identity is a fact about this view
    # rather than a hope about two independent counts:
    #
    #     rows rendered = headline.total + pinned rows in the unconfirmed
    #                     bucket
    #
    # (an unconfirmed-bucket row renders ONLY when the escalation strip pinned
    # it; a pinned row from any other bucket was already inside headline.total
    # and is merely relocated into the strip). The 225-vs-226 class was a row
    # counted by one predicate and skipped by another, rendering nowhere; a
    # residual here is that class, and it is visible instead of silent.
    # CLUSTER1 — render-level clustering, DEFAULT-ON, over the OPEN sections
    # (the Unowned / age lanes; the unconfirmed pin strip is left alone — its
    # rows are queue members and cluster on the queue surface, and the
    # WALKFIX1 slices arithmetic above is a fact about that strip). One line
    # per real-world item: survivor + "+N folded" + read-only expand + the
    # one `keep as one` tap, ids widget-embedded. Additive and drop-empty:
    # with no clusters the view is byte-identical to before this existed.
    _apply_triage_clusters(view, by_cid, ws, now_iso)

    rendered_rows = sum(len(s.get("items") or []) for s in sections)
    n_cluster_folded = int(view.get("n_folded") or 0)
    view["count_reconciliation"] = {
        "headline_total": h["total"],
        "queue_total": h.get("unconfirmed"),
        "rows_rendered": rendered_rows,
        "pin_shown": pin_shown,
        "pin_escalated": pin_escalated,
        "pin_crossing": slices["crossing"],
        "queue_not_rendered": slices["remainder"],
        # CLUSTER1 — folded rows are still rendered work (they ride their
        # survivor's expand), so the identity adds them back rather than
        # reading a fold as a vanished row.
        # MF-1 (trial merge 2026-09-14): PLATENUM1 repointed h["total"] to the
        # sum of the rendered blocks, so the pinned unconfirmed rows are
        # already inside it; subtracting `pin_escalated` again double-counted
        # (residual read -pin_escalated on the walkfix1 fixture).
        "residual": (rendered_rows + n_cluster_folded - h["total"]),
    }
    if n_cluster_folded:
        view["count_reconciliation"]["n_cluster_folded"] = n_cluster_folded
    return view


def _apply_triage_clusters(view: dict, by_cid: dict, ws, now_iso) -> None:
    """CLUSTER1 — fold the triage view's open sections, in place.

    Clusters are computed at render by `commitment_cluster.render_clusters`
    (the shipped duplicate scorer + roster counterparty conjunct + temporal
    adjacency — precision over recall) over the events behind the rendered
    OPEN rows. The unconfirmed pin strip ("lane" == unconfirmed) is skipped:
    its rows are needs-your-call queue members and cluster there. Rows
    carrying sub-items never fold (hiding a family behind an expand hides
    its children — the same caution `auto_merge_eligible` takes).

    Survivor rows gain: "+N folded" on the context tag, `folded_rows` (the
    read-only expand, numbers kept), `data.id` + `data.folded_ids` (the
    CLOSEID2 "id" door), and the `keep as one` tap. Folded rows leave their
    sections; an emptied section leaves the page. The header's headline
    number becomes the information count with the true open count kept in
    the same sentence (SPEC §0-4). Additive keys only; no clusters -> no
    change, byte for byte. Defensive throughout — a clustering failure
    must never take the triage surface down."""
    from commitment_state import UNCONFIRMED_LANE

    try:
        from commitment_cluster import (CLUSTER_ACTION, folded_line,
                                        render_clusters)

        open_sections = [s for s in (view.get("sections") or [])
                         if s.get("lane") != UNCONFIRMED_LANE]
        row_home: dict = {}
        for sec in open_sections:
            for row in sec.get("items") or []:
                if row.get("sub_items"):
                    continue
                cid = str(row.get("n") or "")
                ev = by_cid.get(cid)
                if not cid or ev is None:
                    continue
                d = ev.get("data") or {}
                n_open_subs = d.get("n_subitems_open")
                if isinstance(n_open_subs, int) and n_open_subs > 0:
                    continue
                row_home[cid] = (sec, row)
        if len(row_home) < 2:
            return
        clusters = render_clusters([by_cid[c] for c in row_home],
                                   workspace_root=str(ws), now_iso=now_iso)
        # A cluster only folds when EVERY member is a foldable rendered row.
        clusters = [c for c in clusters
                    if all(m in row_home for m in c["member_ids"])]
        if not clusters:
            return
        n_folded = 0
        for c in clusters:
            _sec, srow = row_home[c["survivor_id"]]
            folded_meta = []
            for fid in c["folded_ids"]:
                fsec, frow = row_home[fid]
                fsec["items"] = [r for r in fsec.get("items") or []
                                 if r is not frow]
                fsec["count"] = len(fsec["items"])
                folded_meta.append((frow, fsec))
                n_folded += 1
            srow["context_tag"] = (str(srow.get("context_tag") or "")
                                   + f" · +{len(folded_meta)} folded — the "
                                     f"same real-world item")
            srow["folded_rows"] = [
                folded_line(fr.get("display_n"),
                            fr.get("name") or "(untitled)",
                            str(fs.get("title") or ""))
                for fr, fs in folded_meta]
            data = dict(srow.get("data") or {})
            data.setdefault("id", c["survivor_id"])
            data["folded_ids"] = list(c["folded_ids"])
            srow["data"] = data
            srow["actions"] = list(srow.get("actions") or []) \
                + [CLUSTER_ACTION]
        view["sections"] = [s for s in (view.get("sections") or [])
                            if s.get("items")]
        view["n_clusters"] = len(clusters)
        view["n_folded"] = n_folded
        # SPEC §0-4 — the headline count is the information count; the true
        # open count stays in the sentence. The counter tiles keep their
        # reconciled meanings (true counts, one level down).
        header = str(view.get("header") or "")
        h_total = None
        for counter in view.get("counters") or []:
            if counter.get("label") == "Open":
                h_total = counter.get("value")
        if h_total is not None and header.startswith(
                f"Commitment triage — {h_total} open"):
            info = int(h_total) - n_folded
            info_noun = "item" if info == 1 else "items"
            view["header"] = header.replace(
                f"Commitment triage — {h_total} open",
                f"Commitment triage — {info} {info_noun} "
                f"({h_total} open)", 1)
    except Exception as exc:  # pragma: no cover — the surface must render
        import sys as _sys
        _sys.stderr.write(f"[surface_drivers] triage clustering skipped: "
                          f"{exc}\n")


# WATCHGATE §2.3 — the expiry question's verbs. Canonical ids only: "mark
# done" is the Done answer, "hold" is Still open (it quiets the row while the
# item stays open), "drop" lets it go. One tap either way, per §2.3.
_WATCH_ASK_ACTIONS = ["mark done", "hold", "drop"]
_WATCH_ASK_SECTION = "STILL OPEN?"


def build_watch_ask_rows(ask_rows: list, *, now_iso: str) -> list[dict]:
    """The expiry questions as card rows — rendered STRENGTH-AWARE per §2.1,
    because they land on the same surface §2.2 hardens and must not read like
    the confident rows beside them."""
    from watch_gate import strength_line

    out: list[dict] = []
    for i, row in enumerate(ask_rows or [], start=1):
        watch = row.get("watch") or {}
        bits = [strength_line(watch.get("reason") or "",
                              evidence=row.get("evidence") or "")]
        reasons = (row.get("stakes") or {}).get("reasons") or []
        if "overdue" in reasons:
            bits.append("the date has passed")
        out.append({
            "n": row["id"], "display_n": i,
            "name": row.get("title") or "(untitled)",
            "context_tag": " · ".join(b for b in bits if b),
            "actions": list(_WATCH_ASK_ACTIONS),
        })
    return out


def build_staff_meeting_view(workspace_root, *, now_iso: str | None = None,
                             moves_rows: list | None = None,
                             watch_rows: list | None = None) -> dict:
    """The Staff Meeting queue view (orchestrator Phase 3+5, mechanized):
    THE projector + D3 ranking + build_card_view. `moves_rows` (Phase 4's
    email-shaped rows, connector-dependent so built by the orchestrator) are
    appended as the THIS WEEK'S MOVES section when supplied.

    `watch_rows` (WATCHGATE §2.3) are the expiry questions this fire owes —
    already routed, capped and ordered by `watch_gate.run_watch_expiry`, which
    `run_surface` runs before this builds. Supplied rather than derived here
    for the reason every other write stays out of a view builder: this
    function reads, `run_surface` decides when writing is allowed.

    STAFFCUT — two RENDER-side passes run between the projector and the builder,
    in this order and never inside the projector:

      1. `proposal_digests.group_into_digests` folds each evidence class into
         ONE row carrying its members' own dispatch payloads. The audit day's 54
         sent-match rows rested on 16 distinct evidence lines (34 of them on a
         single line) — that is 16 decisions asked 54 times.
      2. `proposal_digests.bound_page` bounds what THIS FIRE renders to about
         two screens, appended sections included — the meeting fold's shipped
         volume-guard pattern applied to the queue lane. It bounds the PAGE-SET,
         never the projector: the queue keeps everything, the ranked front is
         what shows, and answering the front is what advances the rotation.

    The honest arithmetic (rows rendered vs items represented, and the bound's
    remainder) rides the section titles via `section_notes` and the D2 receipt
    via `view["receipt_extra"]`, which `run_surface` pops before rendering."""
    from brain_proposals import (build_card_view, load_open_proposals,
                                 rank_proposals)
    from proposal_digests import (bound_extra_sections, bound_page,
                                  group_into_digests, section_notes)

    # FOLD1-B FIX ROUND 1 (reviewer F-2) — SCOPE NOTE, deliberately loud:
    # the lane's ownership of this file is one kwarg plus the two blocks
    # marked below, and this is the receipt half of F-2. The lifetime gate
    # hides questions IMMEDIATELY while their defaults are applied LATER by
    # a weekly rail that marked itself failed in 7 of 9 runs in the attended
    # window. Nothing on the page or in the ledger said how many rows were
    # held back, so a failing rail would now fail silently. This dict is
    # filled by the gate and the one integer lands on the fire receipt below.
    _screen_stats: dict = {}
    open_items = load_open_proposals(workspace_root, "staff-meeting",
                                     now_iso=now_iso,
                                     screen_stats=_screen_stats)
    queue = rank_proposals(open_items)
    extra: list[dict] = []
    if watch_rows:
        extra.append({"title": _WATCH_ASK_SECTION,
                      "count": len(watch_rows),
                      "items": build_watch_ask_rows(
                          watch_rows, now_iso=now_iso or _now_iso())})
    # CAPTUREFLOW §C — the meeting fold. ONE section of the SAME per-meeting
    # groups the on-demand needs-your-call queue renders, from the SAME
    # builder (`needs_review_queue.staff_meeting_group_section`), answered
    # through the SAME confirm/drop fence. Not a new surface, not a new
    # scheduled task: the staff meeting is already scheduled, so this is a
    # section, not an appointment. The section carries its own volume guard
    # (whole calls, oldest first, capped, with the honest totals and a pointer
    # to the on-demand queue in its title) so it can never dominate the page.
    # Drop-empty like every other section; any failure degrades to no section
    # rather than a dead fire.
    try:
        from needs_review_queue import staff_meeting_group_section
        fold = staff_meeting_group_section(workspace_root, now_iso=now_iso)
        if fold:
            extra.append(fold)
    except Exception as exc:  # pragma: no cover — the fire must survive
        sys.stderr.write(f"[surface_drivers] meeting fold skipped: {exc}\n")
    # INTAKE1 rule 6 — the ONE question the intake door may produce: a held
    # row carrying money or a client, with no second source after a week.
    # It sits BEFORE the meeting fold's siblings in nothing and after the
    # fold deliberately — the fold is the week's captures and this is the
    # handful of them that are important enough to be worth a tap. Capped at
    # two rows by its own builder and counted into the page bound like every
    # other appended section, so it takes from the weekly budget rather than
    # adding to it. Drop-empty; any failure degrades to no section rather
    # than a dead fire, the same posture as the fold above.
    try:
        from needs_review_queue import held_important_section
        held_ask = held_important_section(workspace_root, now_iso=now_iso)
        if held_ask:
            extra.append(held_ask)
    except Exception as exc:  # pragma: no cover — the fire must survive
        sys.stderr.write("[surface_drivers] held asks skipped: "
                         + str(exc) + chr(10))
    # FOLD1-B 1.2 item 4(a) — THE OVERDUE FORK'S HOME.
    #
    # SCOPE NOTE, and it is deliberately loud: FOLD1-B's file ownership gives
    # this lane ONE kwarg in this file (the brief's `ask=` below). The spec
    # then asks the Staff Meeting to GAIN a section, which nothing but this
    # function can append — so this block is six lines outside that scope,
    # in the exact shape of the three sections above it, and it is written
    # up as a scope exception at the top of the lane's record. Deleting it
    # reverts the rehome whole; nothing else depends on it.
    #
    # "{title} — {n} days overdue. Done, new date, or drop?" used to ride the
    # morning brief. It is one pre-picked row here now, drawn from the SAME
    # weekly budget (`quiet.ASKER_OVERDUE`) it drew from there, so this adds
    # no touch. `run_surface` writes the ask-once marker AFTER the post,
    # through `needs_review_queue.mark_overdue_asked` — before the post would
    # rest a row nobody saw.
    try:
        from needs_review_queue import overdue_ask_section
        overdue = overdue_ask_section(workspace_root, now_iso=now_iso)
        if overdue:
            extra.append(overdue)
    except Exception as exc:  # pragma: no cover — the fire must survive
        sys.stderr.write(f"[surface_drivers] overdue asks skipped: {exc}\n")
    # PERSONLOOP1 §0-3 — the person-candidate offer, one capped section from
    # the SAME builder the on-demand queue and the End of Day read
    # (`person_candidates.candidate_section`). It sits after the meeting fold
    # deliberately: the fold is the week's captures, and this is the reason
    # so many of them are stuck. Drop-empty, and any failure degrades to no
    # section rather than a dead fire — the same posture as the fold above.
    try:
        from person_candidates import candidate_section
        offer = candidate_section(workspace_root, now_iso=now_iso)
        if offer:
            extra.append(offer)
    except Exception as exc:  # pragma: no cover — the fire must survive
        sys.stderr.write(f"[surface_drivers] person candidates skipped: "
                         f"{exc}\n")
    if moves_rows:
        extra.append({"title": "THIS WEEK'S MOVES", "items": list(moves_rows)})

    # STAFFCUT §3.2/3.3/3.5 — group, then RE-RANK (a digest inherits its oldest
    # member's age, so it must be re-placed in the ranked order), then bound.
    digested, digest_stats = group_into_digests(queue)
    digested = rank_proposals(digested)
    n_extra_rows = sum(len(sec.get("items") or []) for sec in extra)
    # TTL1 (SPEC_FLOW1 Lane G) — THE CEILING IS THE BUDGET, not the page.
    #
    # The page bound (21 rows) decided what one screen held; nothing decided
    # how many questions a week the person had agreed to answer. That is why
    # the 2026-09-07 fire opened "21 waiting on you" over a queue whose
    # oldest rows had been asked since June. QUIET1 already holds the answer
    # — five a week under `light` — so the Staff Meeting reads it instead of
    # inventing a second number, and never renders more than that in one
    # fire. Extra sections (the meeting fold, INTAKE1's day-7 held-with-
    # client question, this week's moves) come out of the SAME ceiling, so a
    # new asker lands inside the budget rather than on top of it.
    #
    # FIX ROUND 1 (reviewer F-5) — THE CEILING BOUNDS THE PAGE, NOT ONE LANE.
    # Passing it to `bound_page` alone bounded the QUEUE and let the appended
    # sections render whole on top: the meeting fold's own caps (3 calls / 8
    # rows) are both larger than a `light` seat's five, so three calls with
    # two unconfirmed captures each — an ordinary week — put the header back
    # to "6 waiting on you", the exact string this lane exists to fix. The
    # extras are now bounded FIRST, keeping one row for the queue whenever
    # the queue has any, and `bound_page` then fills what is left. Queue plus
    # extras never exceeds the ceiling, so the header can't either.
    try:
        from question_ttl import staff_meeting_question_ceiling
        page_cap = staff_meeting_question_ceiling(workspace_root,
                                                  now_iso=now_iso)
    except Exception as exc:  # pragma: no cover — a ceiling that cannot read
        sys.stderr.write(f"[surface_drivers] question ceiling skipped: {exc}\n")
        page_cap = None      # falls back to `bound_page`'s shipped default
    extra_stats: dict = {}
    if page_cap is not None:
        extra, extra_stats = bound_extra_sections(
            extra, cap=page_cap, queue_rows=len(digested))
        n_extra_rows = sum(len(sec.get("items") or []) for sec in extra)
    shown, bound_stats = bound_page(
        digested, n_extra_rows=n_extra_rows,
        **({} if page_cap is None else {"page_cap": page_cap}))
    # LIFECYCLE1 §7b — the tiles show the HONEST per-shape total, the same
    # convention the section titles already use, computed from the FULL open
    # queue (pre-digest, pre-bound) rather than from the page. Grouping and
    # the page bound go on governing what renders and nothing else.
    shape_totals: dict = {}
    for it in open_items:
        s = (it or {}).get("shape") or "hygiene"
        shape_totals[s] = shape_totals.get(s, 0) + 1
    view = build_card_view(shown, surface="staff-meeting",
                           extra_sections=extra or None,
                           section_notes=section_notes(shown, bound_stats),
                           shape_totals=shape_totals,
                           # M-6 — the rows the ceiling cut from the appended
                           # sections belong in the footer's number too.
                           extra_dropped=int(extra_stats.get("dropped", 0)))
    # D2 — the per-kind + digest arithmetic the fire receipt records. Popped by
    # `run_surface` before the view reaches the renderer, so nothing new travels
    # into the widget contract.
    view["receipt_extra"] = _staff_receipt_extra(open_items, shown,
                                                 digest_stats, bound_stats,
                                                 n_extra_rows,
                                                 extra_stats=extra_stats)
    # FOLD1-B FIX ROUND 1 (reviewer F-2) — ONE INTEGER, and it is the second
    # half of the scope note above. `n_hidden_past_lifetime` is what the
    # lifetime gate held back from THIS fire. It counts the projector's
    # screen (the identity / hygiene / money question classes, whose
    # defaults ride the weekly `question-expiry` and Sunday
    # `identity-reconcile` rails); the meeting fold's capture-card rows are
    # settled by a different rail and are not in this number. Always
    # written, 0 included: a field that appears only when it is non-zero
    # cannot tell "nothing was hidden" from "nobody recorded it".
    view["receipt_extra"]["n_hidden_past_lifetime"] = int(
        _screen_stats.get("n_hidden", 0))
    # M-6 — and its sibling: the rows the page ceiling cut from the APPENDED
    # sections (overdue, held, the meeting fold). It rode the receipt only
    # inside `page_bound`; it sits beside the hidden count now, because the
    # two answer the same question about the same page and a reader
    # comparing the footer against the receipt should find both.
    view["receipt_extra"]["n_extra_held_back"] = int(
        extra_stats.get("dropped", 0))
    return view


def _kind_counts(items) -> dict:
    """kind -> count, in descending count order (stable for reading a receipt
    by eye). An item with no kind is counted as "unknown" rather than dropped —
    a receipt that quietly omits rows is the thing D2 exists to fix."""
    counts: dict[str, int] = {}
    for it in items or []:
        key = str((it or {}).get("kind") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _staff_receipt_extra(open_items, shown, digest_stats, bound_stats,
                         n_extra_rows: int, extra_stats=None) -> dict:
    """STAFFCUT D2 — the staff-meeting receipt's per-kind counts.

    `surface_drivers._log_fire_receipt` wrote only the scalar `surfaced`, so
    load history could not be measured: the 2026-08-02 audit had to reconstruct
    23 fires from an upper-bound model because no receipt had ever recorded WHAT
    was surfaced. This is additive only — the scalar keeps its meaning (rows the
    widget showed), every existing reader is untouched, and the new keys ride
    `log_receipt(extra_data=...)`, which already exists for exactly this.

    Both halves of the digest arithmetic are recorded on purpose: a future audit
    has to be able to tell "the queue shrank" from "the rows were grouped"."""
    return {
        "open_by_kind": _kind_counts(open_items),
        "surfaced_by_kind": _kind_counts(shown),
        "queue_rows_rendered": len(shown),
        "queue_items_represented": sum(
            max(1, int((it or {}).get("digest_count") or 1)) for it in shown),
        "queue_open_total": len(open_items),
        "digest_rows": digest_stats.get("digest_rows", 0),
        "digest_items_grouped": digest_stats.get("grouped_items", 0),
        "digests_by_class": dict(digest_stats.get("by_class") or {}),
        "page_bound": {"cap": bound_stats.get("cap"),
                       "queue_budget": bound_stats.get("budget"),
                       "extra_section_rows": n_extra_rows,
                       "held_back": bound_stats.get("dropped", 0),
                       # TTL1 fix round 1 (F-5) — what the CEILING took off
                       # the appended sections, so a receipt can answer "why
                       # did the fold show four of nine" without guessing.
                       "extra_rows_before": (extra_stats or {}).get(
                           "rows_before", n_extra_rows),
                       "extra_held_back": (extra_stats or {}).get("dropped", 0)},
        # RV-4's arithmetic, recorded: the header count is exactly this.
        "page_rows_rendered": len(shown) + n_extra_rows,
    }


def _hoisted_anchor_and_instant(ws, now_iso: str):
    """ONE resolve for both the row-level date anchor (`tz.localize_date`'s
    own shape — the DATE1 F-2 hoist) and `_plate_headline`'s workspace-local
    INSTANT (the ONEPLATE1 merge-seam finding, F-16) — never two. A bare
    `YYYY-MM-DD` `now_iso` never resolves at all (the same guard
    `plate_view._local_today` uses); a resolvable full timestamp resolves
    ONCE via `plate_view._local_instant` and the anchor date is sliced off
    that SAME instant, so `build_commitment_triage_view` /
    `build_waiting_on_view` / `build_my_plate_view` each pay one resolve
    for the whole render, not two (one for `anchor`, a second inside
    `build_plate` for `_plate_headline`'s numbers)."""
    if isinstance(now_iso, str) and len(now_iso) == 10 and now_iso.count("-") == 2:
        return now_iso, None
    from plate_view import _local_instant
    instant = _local_instant(ws, now_iso)
    if instant is not None:
        return instant.date().isoformat(), instant
    # Genuinely unresolvable (no workspace timezone, or an unparseable
    # clock) — `_local_instant` already made the one resolve attempt and
    # failed; do NOT retry via `tz.localize_date` (it would attempt
    # `to_local` a second time for the same answer). Take `localize_date`'s
    # own documented fallback (the UTC-spelling date slice) directly.
    anchor = now_iso[:10] if isinstance(now_iso, str) and len(now_iso) >= 10 else (now_iso or "")
    from plate_view import INSTANT_UNRESOLVABLE
    return anchor, INSTANT_UNRESOLVABLE


#: SPEC SURFACEFIX1 5.4 — the name the degrade is recorded under, on the
#: pack and in the fire's persisted audit copy. A DIFFERENT NUMBER IS NEVER
#: RENDERED: the caller drops the line instead of printing the fallback.
PLATE_HEADLINE_DEGRADED = "plate_headline_degraded"


def _plate_headline(ws, *, now_iso: str, fallback: dict,
                    folded: int = 0, folded_key: str | None = None,
                    local_instant=None, degraded_out: list | None = None) -> dict:
    """ONE projection's numbers for a surface that renders its own rows
    (SPEC_FLOW1 Lane C item 1): `plate_view.surface_numbers`, in the
    headline shape the chat surfaces already read.

    NEVER raises into a fire. A refused plate (no primary user) or a
    loader error falls back to the caller's own count — a degraded number
    is better than a chat that does not render — and the fallback is the
    number that shipped, so the failure mode is exactly today's behaviour.

    `folded` / `folded_key` — THE SURFACE'S OWN DUPLICATE FOLD, subtracted
    (BUG-8330 FX-3, `run_dup_render_fold_test`). Waiting On and My Plate
    fold rows that point at each other with `duplicate_of` before they
    render, because a fold is IDENTITY — two records of one promise are one
    promise — not visibility. The plate's own cluster fold is a different
    fold and does not see those pointers, so handing the plate's number
    straight to a folded surface put "2 owed to you" over one row. One
    projection is about where the number comes FROM; a header still has to
    reconcile with the rows under it. The subtraction is named, bounded by
    the surface's own receipt count, and never goes below zero.

    `local_instant` (seam round, DATE1 x ONEPLATE1 merge finding, F-16) —
    passed straight through to `surface_numbers` / `build_plate`. The
    caller already resolved this once for its own row-level date anchor
    (`_hoisted_anchor_and_instant`); handing it here keeps the WHOLE
    render's timezone resolve count at one instead of `build_plate`
    resolving a second time on top of the caller's own hoist.
    """
    # SPEC SURFACEFIX1 5.4 — THE FALLBACK IS NO LONGER SILENT. "A degraded
    # number is better than a chat that does not render" was the shipped
    # doctrine and it is the wrong trade on a COUNT: the fallback is a
    # DIFFERENT projection's figure, so a degraded read renders a number that
    # disagrees with the one number the surface promises (A4's "60" beside a
    # plate of 334). The degrade is now recorded by name — `degraded_out`
    # collects it for the pack and for the fire's persisted audit copy — and
    # the caller drops the line rather than printing a second answer.
    def _degrade(reason: str) -> dict:
        if degraded_out is not None:
            degraded_out.append(reason)
        out = dict(fallback)
        out["degraded"] = True
        out["degrade_reason"] = reason
        return out

    try:
        from plate_view import surface_numbers
        n = surface_numbers(ws, now_iso=now_iso, local_instant=local_instant)
        if not n.get("ok"):
            return _degrade(str(n.get("error") or "the plate refused to build"))
        out = dict(fallback)
        for k in ("total", "you_owe", "owed_to_you", "unowned", "unconfirmed",
                  "overdue"):
            src = "open" if k == "total" else k
            if src in n:
                out[k] = n[src]
        if folded:
            for k in ("total", folded_key):
                if k and isinstance(out.get(k), int):
                    out[k] = max(0, out[k] - int(folded))
        return out
    except Exception as exc:  # noqa: BLE001 — the fire must not crash on a number
        return _degrade(type(exc).__name__)


#: SPEC SURFACEFIX1 5.5 — the schedule view's own header, one sentence.
SCHEDULE_VIEW_HEADER = "To schedule — {n} {items}"
SCHEDULE_VIEW_EMPTY = "Nothing to schedule."


def build_schedule_view(workspace_root, *, now_iso: str | None = None) -> dict:
    """The `show scheduling` door (SPEC SURFACEFIX1 5.5).

    `show scheduling` was a LABEL — `plate_view.SHOW_MORE_PHRASES` printed it
    under the plate's SCHEDULE block as the way to see the rest, and no
    handler claimed it, exactly as `show waiting` was a label with no route
    (B2.9). This is its route.

    Deliberately thin, and deliberately NOT in `plate_view`: this lane owns
    two lines of that file and nothing else, so the view is composed here
    from the SAME primitives the Waiting On view uses — the canonical loader
    and `surface_split.effective_kind_of` — rather than a second projection
    or a renderer of its own. It is a reading surface: rows, one line each,
    no verbs, no numbers to tap.
    """
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from surface_split import effective_kind_of

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    anchor, _ = _hoisted_anchor_and_instant(ws, now_iso)
    user_id = resolve_primary_user(ws)
    rows = []
    for ev in load_open_commitments(_events_path(ws), workspace_root=ws):
        if effective_kind_of(ev) != "scheduling":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        title = str(data.get("title") or "").strip()
        if not title:
            continue
        rows.append({"id": str(data.get("id") or ""), "title": title,
                     "due": data.get("due"),
                     "tag": _schedule_row_tag(data, anchor, ws),
                     "line": _schedule_row_line(title, data, anchor, ws)})
    rows.sort(key=lambda r: (r["due"] is None, str(r["due"] or ""),
                             r["title"].lower()))
    n = len(rows)
    header = (SCHEDULE_VIEW_EMPTY if not n else SCHEDULE_VIEW_HEADER.format(
        n=n, items=("item" if n == 1 else "items")))
    # SCHEDVIEW1 5.1 — THE SECTION CARRIES `items`, NOT `rows`.
    #
    # This builder shipped returning its section as
    # `{"title", "count", "rows": [<line strings>]}` while every other
    # plate-bearing view in this module returns `items` with row DICTS
    # (`build_waiting_on_view`'s three sections). `widget_transport`
    # paginates on `section["items"]` and `_stamp_map_numbers` reads
    # `sec.get("items")`, so the transport saw a section with nothing in it:
    # the `show scheduling` card rendered EMPTY over a real book (attended
    # test v5.31.0, B2.9), the chat then hand-printed the rows, and it
    # numbered them BY POSITION — the exact class PLATENUM1 exists to
    # prevent, because a typed verb resolves a number through
    # `plate_view.resolve_display_number` and a positional number lands it
    # on a different item.
    #
    # The row dicts are the renderer's own shape (`n` = the commitment id,
    # `name` = the title, `context_tag` = the date phrase), and
    # `_stamp_map_numbers` below stamps each one with THE number that
    # belongs to the item, off PLATENUM1's persisted map — the same number
    # the board shows for the same row. A row with no commitment id gets no
    # number rather than a positional one (that helper's own rule).
    sections = ([{"title": "To schedule", "count": n,
                  "items": [{"n": r["id"], "name": r["title"],
                             "context_tag": r["tag"]} for r in rows]}]
                if n else [])
    _stamp_map_numbers(ws, sections)
    return {
        "source_skill": "commitment-triage",
        "surface": "schedule",
        "header": header,
        "user_id": user_id,
        "sections": sections,
        "rows": rows,
        "counters": [{"label": "To schedule", "value": n}],
    }


def _schedule_row_tag(data: dict, anchor, ws) -> str:
    """The row's date phrase on its own — the `context_tag` half of
    `_schedule_row_line`, for the section's row dicts (SCHEDVIEW1 5.1).
    Empty when the row carries no date: this surface never invents one."""
    due = data.get("due")
    if not due:
        return ""
    try:
        return _due_phrase(due, anchor, ws)
    except Exception:  # pragma: no cover — a date read never breaks a list
        return ""


def _schedule_row_line(title: str, data: dict, anchor, ws) -> str:
    """One scheduling row, read-only: the title, and its date when it has one.
    Never a verb and never a number — this surface is a list, not a queue."""
    due = data.get("due")
    if not due:
        return title
    try:
        return f"{title} — {_due_phrase(due, anchor, ws)}"
    except Exception:  # pragma: no cover — a date read never breaks a list
        return title



def _stamp_map_numbers(workspace_root, sections: list) -> None:
    """Night 11b trial merge (merged-tree review, DOORS1×SURFACEFIX1 reader
    F-1): a row on a standalone plate-bearing view carries THE number that
    belongs to the item — read off PLATENUM1's persisted map — never its
    position in this render. `apply-choices` and commitment-triage resolve a
    typed number through `plate_view.resolve_display_number`, so a positional
    number here would land a typed verb on a different item (the B2.5 class).
    A row with no commitment id (an orchestrator-supplied draft) renders
    unnumbered rather than with a number that could collide with a real one."""
    try:
        from plate_view import mint_display_numbers
    except Exception:  # pragma: no cover
        return
    rows = [r for sec in (sections or []) for r in (sec.get("items") or [])
            if isinstance(r, dict)]
    ids = [str(r.get("n")) for r in rows if r.get("n")]
    nums = {}
    if ids:
        try:
            nums = mint_display_numbers(workspace_root, ids) or {}
        except Exception:  # pragma: no cover — a refused lock mints nothing
            nums = {}
    for r in rows:
        r["display_n"] = nums.get(str(r.get("n"))) if r.get("n") else None

def build_waiting_on_view(workspace_root, *, now_iso: str | None = None,
                          chase_rows: list | None = None) -> dict:
    """The daily Waiting On chat data view (CTS1 Surface 1 —
    orchestrator-commitments, mechanized): canonical loader + `surface_split`
    five-way partition + the count headline + the deterministic delegated and
    confirm-tail sections. Pure read.

    `chase_rows` (the pre-staged chase-email items for the CRU-eligible
    owed-to-you commitments — connector-dependent, so built by the orchestrator
    exactly like build_staff_meeting_view's `moves_rows`) are appended VERBATIM
    as the leading "Waiting On" section; the driver never composes an email body
    or touches a connector. Everything else — the partition, the header counts,
    the delegated-task rows, and the unowned/unconfirmed confirm tail — is
    deterministic and built here, killing the ~30-command surface the
    orchestrator assembled live (FB-15). Owner-me rows never surface here (they
    are My Plate — the partition routes them away)."""
    from commitment_activity import derive_commitment_movement
    from commitment_state import count_commitments
    from confirm_flow import unconfirmed_classes
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from surface_split import (SURFACE_UNCONFIRMED, SURFACE_UNOWNED,
                               SURFACE_WAITING_ON, effective_kind_of,
                               partition_surfaces)

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    # DATE1 fix round 1 (REVIEW_DATE1 F-2 / F-1) — see the identical comment
    # in build_commitment_triage_view: one workspace-day resolve per render,
    # reused by `_due_phrase` and `count_commitments` below.
    # seam round (DATE1 x ONEPLATE1 merge finding, F-16) — one resolve for
    # both `anchor` and the headline's instant; see `_hoisted_anchor_and_instant`.
    anchor, _lnow_hoist = _hoisted_anchor_and_instant(ws, now_iso)
    events_path = _events_path(ws)
    opens = load_open_commitments(events_path)
    # BUG-8330 item 14 (fix round FX-3) — collapse suspected-duplicate
    # components BEFORE the counts are taken. A duplicate fold is an IDENTITY
    # operation, not a row filter: the folded rows are the SAME commitment, so
    # counting them separately double-counts one real promise. This is not the
    # F-47 exception below — F-47 keeps headline numbers full-set against ROW
    # FILTERS (visibility), and the fold is not one. The review's B3 shape
    # ("2 owed to you" over an empty surface) needed both halves: the cycle
    # guard in `fold_suspected_duplicates`, and the header reconciling here.
    opens, n_dup_folded = _fold_dups(opens)
    # BUG-8330 item 6 — the confidence floor lives HERE (code), not in
    # orchestrator prose. It filters ROWS only: header counts stay computed
    # over the FULL open set (the F-47 rule — headline numbers identical
    # across morning brief / this chat / commitment-triage; row filters
    # never shrink them). The receipt records how many rows the floor
    # removed, so a 70→6 collapse is visible instead of silent.
    surfaced, n_conf_filtered = _apply_confidence_floor(ws, opens)
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    movement = derive_commitment_movement(events_path)
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=anchor, movement=movement)
    part = partition_surfaces(surfaced, user_id)

    def _cid(ev) -> str:
        d = ev.get("data") or {}
        return d.get("id") or f"commitment_seq_{ev.get('seq')}"

    display_n = 0
    sections: list[dict] = []
    rr_cache: dict = {}  # RRF1 per-render-pass memo (name -> resolves now)

    # 1. Chase drafts — orchestrator-supplied (connector-dependent), appended
    #    verbatim: email-shaped rows whose pre-staged nudge lives in the widget.
    if chase_rows:
        rows = []
        for r in chase_rows:
            display_n += 1
            row = dict(r)
            row.setdefault("display_n", display_n)
            rows.append(row)
        sections.append({"title": "Waiting On", "count": len(rows),
                         "items": rows})

    # 2. Delegated tasks (owner != user, effective kind `task`) — CRU-ineligible,
    #    so no pre-staged chase; the delegated set (`nudge` composes the chase
    #    on click — D-A4) per orchestrator-commitments §2.3.
    delegated = [ev for ev in part[SURFACE_WAITING_ON]
                 if effective_kind_of(ev) == "task"]
    if delegated:
        people_by_id = _people_by_id(ws)
        rows = []
        for ev in delegated:
            display_n += 1
            d = ev.get("data") or {}
            age = _age_days(ev.get("ts") or "", now_iso)
            bits = []
            if age is not None and age >= 0:
                bits.append("1 day old" if age == 1 else f"{age} days old")
            bits.append(_due_phrase(d.get("due"), anchor, ws))
            # A delegated row is owner != M by definition — name who we're
            # waiting on (FIX A); fall back to bare "delegated" only if no name
            # resolves.
            owner_name = _owner_display_name(ev, people_by_id)
            bits.append(
                f"delegated to {owner_name} — nudge is manual, "
                "I won't auto-chase this" if owner_name
                else "delegated — nudge is manual, I won't auto-chase this")
            _dfn = _dup_fold_note(d)
            if _dfn:
                bits.append(_dfn)
            # `nudge` (WG1-A D-A4) composes the chase on demand at dispatch
            # (no pre-staged body). Resolve the owner's email so the row
            # carries a real To:; when none is on file, degrade `nudge` to the
            # `add email then send` recovery verb so the surface still renders
            # (never a dead button — the Bug #44 principle). THIS DEGRADE IS
            # THE ONLY GUARD: renderer Gate 6 (_SEND_CLASS_ACTIONS) deliberately
            # does NOT include `nudge`, because the WG1-B D-B4 moves adapter
            # (relationship_moves.moves_rows_from_candidates) legitimately
            # emits To-less nudge rows on scheduled staff-meeting fires
            # (compose-on-click resolves the address at dispatch). Train-merge
            # review F-4 ruling 2026-07-22: keep nudge out of the frozenset;
            # this driver-level degrade is the enforcement for delegated rows.
            email = _owner_email(ev, people_by_id)
            row: dict = {
                "n": _cid(ev), "display_n": display_n,
                "name": d.get("title") or d.get("summary") or "(untitled)",
                "context_tag": " · ".join(bits),
            }
            if email:
                row["metadata"] = [["To", email]]
                row["actions"] = list(_DELEGATED_VERBS)
            else:
                row["actions"] = ["add email then send"] + [
                    v for v in _DELEGATED_VERBS if v != "nudge"]
            rows.append(row)
        sections.append({"title": "Delegated", "count": len(rows),
                         "items": rows})

    # 3. Confirm tail — unowned + pending_review (unconfirmed). The REVIEW
    #    cluster only: NEVER a pre-staged chase on an unconfirmed/unowned item
    #    (no auto-email on a guessed owner — orchestrator-commitments §458/§478).
    confirm_evs = list(part[SURFACE_UNOWNED]) + list(part[SURFACE_UNCONFIRMED])
    if confirm_evs:
        rows = []
        for ev in confirm_evs:
            display_n += 1
            d = ev.get("data") or {}
            pending = "pending_review" in unconfirmed_classes(ev)
            tag = ("captured from a chat — confirm it's yours" if pending
                   else "no owner resolved yet — whose is this?")
            if d.get("review_reason"):
                tag += f" · {_display_review_reason(ws, d['review_reason'], rr_cache)}"
            rows.append({
                "n": _cid(ev), "display_n": display_n,
                "name": d.get("title") or d.get("summary") or "(untitled)",
                "context_tag": tag,
                "actions": list(_REVIEW_VERBS),
            })
        sections.append({"title": "Needs a quick confirm", "count": len(rows),
                         "items": rows})

    # ONEPLATE1 (SPEC_FLOW1 Lane C item 1) — THE NUMBERS COME FROM THE
    # PLATE. This surface used to count its own way: `count_commitments`
    # over its own input, AFTER `_fold_dups` and WITHOUT the plate's
    # observed-tier drop. On a book where those two disagree the same book
    # states two "owed to you" numbers on two surfaces in one morning
    # (ATTENDED_TEST_v5.29.0 Part E: 94 + 8 here against the brief's 119).
    # The rows below are unchanged — this is the header and the counters.
    # `folded`: this surface's own duplicate fold comes off the projection's
    # number, or the header states two for one promise it renders once
    # (BUG-8330 FX-3 — see `_plate_headline`).
    h = _plate_headline(ws, now_iso=now_iso, fallback=counts["headline"],
                        folded=n_dup_folded, folded_key="owed_to_you",
                        local_instant=_lnow_hoist)
    counters = [
        {"label": "Owed to you", "value": h["owed_to_you"]},
        {"label": "Unowned", "value": h["unowned"]},
        {"label": "Unconfirmed", "value": h["unconfirmed"]},
    ]
    header = f"Waiting On — {h['owed_to_you']} owed to you"
    if n_conf_filtered:
        # Item 6's honesty rule: the headline stays the full-set truth, so
        # any gap between it and the rows below must say WHY, on-surface.
        header += f" ({n_conf_filtered} low-confidence not shown)"
    _stamp_map_numbers(ws, sections)
    return {
        "source_skill": "commitments",
        "header": header,
        "counters": counters,
        "sections": sections,
        "receipt_extra": {"n_filtered_by_confidence": n_conf_filtered,
                          "n_duplicates_folded": n_dup_folded},
    }


def build_my_plate_view(workspace_root, *, now_iso: str | None = None,
                        status_rows: list | None = None,
                        personal_cap: int = _MP_PERSONAL_CAP,
                        promised_cap: int = _MP_PROMISED_CAP) -> dict:
    """The daily My Plate chat data view (CTS1 Surface 2 — orchestrator-my-plate,
    mechanized): canonical loader + `surface_split` partition + the count
    headline + the two owner-me groups. Pure read.

    Mirrors `build_waiting_on_view` exactly (FB-plumbing item 6 — kill the inline
    builder scripts): the DETERMINISTIC, connector-free row classes are built
    here — the counterparty-unresolved Promised fixup rows (Bug #103) and the
    whole Personal group — while the connector-DEPENDENT email-shaped status
    drafts for counterparty-resolved Promised rows are composed by the
    orchestrator (email-writer chain) and passed VERBATIM as `status_rows`,
    appended as the LEADING rows of the Promised section (the `chase_rows`
    parallel). The driver never composes an email body or touches a connector.
    Waiting-on rows (owner != user) never surface here — the partition routes
    them to the Waiting On chat; unowned / pending_review rows are that chat's
    confirm tail, never My Plate's (this is a pure act-list).

    `personal_cap` (CTS1 §4.2, the `my-plate` skill config knob, default 7)
    caps the Personal group; the section footer carries the "+N more — say
    'show my plate' for everything" tail when rows are hidden. `show my plate`
    re-renders with the cap lifted (the orchestrator passes a large cap)."""
    from commitment_activity import derive_commitment_movement
    from commitment_state import count_commitments
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from surface_split import (SURFACE_PERSONAL, SURFACE_PROMISED,
                               counterparty_unresolved, partition_surfaces)

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    # DATE1 fix round 1 (REVIEW_DATE1 F-2 / F-1) — see the identical comment
    # in build_commitment_triage_view: one workspace-day resolve per render,
    # reused by `_due_phrase` and `count_commitments` below.
    # seam round (DATE1 x ONEPLATE1 merge finding, F-16) — one resolve for
    # both `anchor` and the headline's instant; see `_hoisted_anchor_and_instant`.
    anchor, _lnow_hoist = _hoisted_anchor_and_instant(ws, now_iso)
    events_path = _events_path(ws)
    opens = load_open_commitments(events_path)
    # BUG-8330 item 14 (fix round FX-3) — same identity-before-counts fold as
    # Waiting On: duplicates collapse first, so the header counts one real
    # promise once.
    opens, n_dup_folded = _fold_dups(opens)
    # BUG-8330 item 6 — same code-side floor as build_waiting_on_view: rows
    # filter, header counts stay full-set (F-47), receipt carries the delta.
    surfaced, n_conf_filtered = _apply_confidence_floor(ws, opens)
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    movement = derive_commitment_movement(events_path)
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=anchor, movement=movement)
    part = partition_surfaces(surfaced, user_id)
    promised = part[SURFACE_PROMISED]
    personal = part[SURFACE_PERSONAL]

    def _cid(ev) -> str:
        d = ev.get("data") or {}
        return d.get("id") or f"commitment_seq_{ev.get('seq')}"

    display_n = 0
    sections: list[dict] = []

    # === Group A — PROMISED (someone's waiting; renders FIRST) =============
    promised_rows: list[dict] = []

    # 1. Orchestrator-supplied status drafts (connector-dependent), appended
    #    verbatim: email-shaped rows whose pre-drafted status lives in the
    #    widget (the counterparty-RESOLVED, external-recipient promises).
    if status_rows:
        for r in status_rows:
            display_n += 1
            row = dict(r)
            row.setdefault("display_n", display_n)
            promised_rows.append(row)

    # 2. Counterparty-UNRESOLVED promises (deterministic, connector-free): a
    #    real promise whose counterparty linking failed. The fixup IS the
    #    action (no recipient to draft to). NEVER auto-demoted to Personal.
    for ev in promised:
        if not counterparty_unresolved(ev, user_id):
            continue
        display_n += 1
        d = ev.get("data") or {}
        age = _age_days(ev.get("ts") or "", now_iso)
        bits = []
        if age is not None and age >= 0:
            bits.append("1 day old" if age == 1 else f"{age} days old")
        bits.append(_due_phrase(d.get("due"), anchor, ws))
        _dfn = _dup_fold_note(d)
        if _dfn:
            bits.append(_dfn)
        bits.append("counterparty unresolved — who was this for?")
        promised_rows.append({
            "n": _cid(ev), "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(bits),
            "actions": list(_MP_UNRESOLVED_VERBS),
        })

    # 3. RESIDUAL promised rows (BUG-8330 item 5, deterministic): a
    #    counterparty-RESOLVED promise the orchestrator composed no status
    #    draft for. Before this class existed such a promise was ABSENT from
    #    the widget — not capped, absent — while the header counted it; the
    #    "+49 more" the user saw was model-composed. Draft-less is a state,
    #    not an exclusion.
    status_ids = {str(r.get("n")) for r in (status_rows or []) if r.get("n")}
    for ev in promised:
        if counterparty_unresolved(ev, user_id):
            continue
        if _cid(ev) in status_ids:
            continue
        display_n += 1
        d = ev.get("data") or {}
        age = _age_days(ev.get("ts") or "", now_iso)
        bits = []
        if age is not None and age >= 0:
            bits.append("1 day old" if age == 1 else f"{age} days old")
        bits.append(_due_phrase(d.get("due"), anchor, ws))
        _dfn = _dup_fold_note(d)
        if _dfn:
            bits.append(_dfn)
        promised_rows.append({
            "n": _cid(ev), "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(bits),
            "actions": list(_MP_RESIDUAL_VERBS),
        })

    # Explicit cap + honest footer (BUG-8330 item 5): the section shows at
    # most `promised_cap` rows and SAYS how many it is holding back — the
    # footer_note now survives both render paths.
    p_cap = promised_cap if promised_cap and promised_cap > 0 \
        else len(promised_rows)
    p_hidden = len(promised_rows) - min(len(promised_rows), p_cap)
    if promised_rows:
        sec = {"title": "↗ PROMISED — someone's waiting",
               "count": len(promised_rows),
               "items": promised_rows[:p_cap]}
        if p_hidden > 0:
            sec["footer_note"] = (f"+{p_hidden} more promised — say "
                                  "'show my plate' for everything")
        sections.append(sec)

    # === Group B — PERSONAL (my own work; capped) =========================
    # Sort: dated first (due soonest), then undated by most-recently-touched
    # (the movement ts, capture ts floor) — newest first. Deterministic.
    def _touch_ts(ev) -> str:
        d = ev.get("data") or {}
        mv = movement.get(_cid(ev)) if isinstance(movement, dict) else None
        if isinstance(mv, dict) and mv.get("ts"):
            return str(mv["ts"])
        return str(ev.get("ts") or "")

    def _personal_key(ev):
        d = ev.get("data") or {}
        due = d.get("due")
        due_s = str(due)[:10] if due else None
        # dated rows first (0), sorted by due asc; undated (1), newest touch first
        return (0, due_s, "") if due_s else (1, "", _neg_ts(_touch_ts(ev)))

    personal_sorted = sorted(personal, key=_personal_key)
    cap = personal_cap if personal_cap and personal_cap > 0 else len(personal_sorted)
    shown = personal_sorted[:cap]
    hidden = len(personal_sorted) - len(shown)

    personal_rows: list[dict] = []
    for ev in shown:
        display_n += 1
        d = ev.get("data") or {}
        age = _age_days(ev.get("ts") or "", now_iso)
        bits = []
        if age is not None and age >= 0:
            bits.append("1 day old" if age == 1 else f"{age} days old")
        bits.append(_due_phrase(d.get("due"), anchor, ws))
        _dfn = _dup_fold_note(d)
        if _dfn:
            bits.append(_dfn)
        personal_rows.append({
            "n": _cid(ev), "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(bits),
            "actions": list(_MP_PERSONAL_VERBS),
        })
    if personal_rows:
        sec = {"title": "PERSONAL — your own list",
               "count": len(personal_rows), "items": personal_rows}
        if hidden > 0:
            sec["footer_note"] = (f"+{hidden} more — say 'show my plate' "
                                  "for everything")
        sections.append(sec)

    # ONEPLATE1 (SPEC_FLOW1 Lane C item 1) — THE NUMBERS COME FROM THE
    # PLATE (see the same note in `build_waiting_on_view`). Part A of the
    # v5.29.0 attended test found four totals for one book on one day; My
    # Plate's 158 was one of them. The group sizes below are this surface's
    # own rows and stay its own.
    h = _plate_headline(ws, now_iso=now_iso, fallback=counts["headline"],
                        folded=n_dup_folded, folded_key="you_owe",
                        local_instant=_lnow_hoist)
    counters = [
        {"label": "On your plate", "value": h["you_owe"]},
        {"label": "Promised", "value": len(promised)},
        {"label": "Personal", "value": len(personal)},
        {"label": "Waiting on others", "value": h["owed_to_you"]},
    ]
    header = (f"My Plate — {h['you_owe']} on your plate "
              f"({len(promised)} promised · {len(personal)} personal)")
    if n_conf_filtered:
        # Same on-surface honesty as Waiting On: the headline is full-set,
        # the groups are row-level — the gap must name itself.
        header += f" · {n_conf_filtered} low-confidence not shown"
    _stamp_map_numbers(ws, sections)
    return {
        "source_skill": "commitments",
        "header": header,
        "counters": counters,
        "sections": sections,
        "receipt_extra": {"n_filtered_by_confidence": n_conf_filtered,
                          "n_duplicates_folded": n_dup_folded},
    }


def _apply_confidence_floor(ws, opens: list) -> tuple[list, int]:
    """BUG-8330 item 6 — the ONE confidence surface filter, in code.

    The floor previously existed only as orchestrator prose (a bare constant
    applied by hand over `_commitment_confidence`, whose missing→0.0 default
    silently dropped every unscored capture). `passes_surface_floor` treats
    missing as unscored (passes) and resolves the floor through
    `confidence.surface_min(ws)` — so the per-workspace calibration override
    finally moves the filter that matters. Returns (kept, n_filtered);
    defensive — any failure keeps the full set (a broken floor must never
    blank a daily surface).

    COMPONENT-AWARE (SPEC DEDUPFLOOR1). `_fold_dups` has already run, so some
    rows here are duplicate-component SURVIVORS standing in for members the
    floor will never see. Survivors are elected by lowest seq, not by
    confidence, so judging one on its own row killed components whose
    high-confidence member would have rendered alone. A row passes if EITHER
    it clears the floor or its component does — see
    `_component_clears_floor`."""
    try:
        from confidence import surface_min
        from cru_match import passes_surface_floor
        floor = surface_min(ws)
        kept = [ev for ev in opens
                if passes_surface_floor(ev, floor=floor)
                or _component_clears_floor(ev, floor)]
        return kept, len(opens) - len(kept)
    except Exception:
        return opens, 0


def _component_clears_floor(ev, floor: float) -> bool:
    """Does this row's duplicate component clear the floor, if it has one?

    Reads the transient `_component_max_confidence` annotation that
    `commitment_dedup.fold_suspected_duplicates` stamps on a fold survivor.
    A row that was never folded has no annotation and answers False — it is
    floored on its own confidence, unchanged. A non-numeric or absent value
    reads as "no component": this is a rescue for a real folded group, never
    a bypass a malformed payload can talk its way into.

    REVIEW DEDUPFLOOR1 R-1 — the annotation ALONE is not enough. `data` is a
    free-form object in `events.schema.json` and the capture gate whitelists
    no keys, so an event on disk can carry any field name at all; a numeric
    `_component_max_confidence` read on its own is a floor bypass that needs
    no fold and shows nothing on the row. So the rescue is gated on the
    fold's OWN back-reference, `duplicate_fold_count` — written on the same
    three lines as the annotation, and the exact field `_dup_fold_note`
    renders as "N records — merge?". A row can therefore only be rescued
    while it is visibly saying it stands for a group. That is the difference
    between a rescue and a bypass: not that the value is well-formed, but
    that the reader can see it happening.

    The comparison is deliberately NOT written `float(m) >= floor`: `float()`
    raises OverflowError on an out-of-float-range integer, and this function
    runs inside `_apply_confidence_floor`'s `except Exception`, which answers
    a raise by returning the WHOLE open set unfiltered. Python compares int
    and float exactly and without converting, so the bare comparison is both
    correct and total. Same lesson as R-2 one layer down: in this pair of
    functions, a raise is not an error — it is the floor switching itself off
    for the entire surface, silently."""
    d = ev.get("data") or {}
    m = d.get("_component_max_confidence")
    if isinstance(m, bool) or not isinstance(m, (int, float)):
        return False
    n = d.get("duplicate_fold_count")
    if isinstance(n, bool) or not isinstance(n, int) or n < 2:
        return False
    return m >= floor


def _fold_dups(surfaced: list) -> tuple[list, int]:
    """BUG-8330 item 14 — the render-time suspected-duplicate fold, applied to
    the OPEN set of the daily surfaces before rows and counts are derived from
    it (fix round FX-3: a fold is identity, not visibility, so the header must
    see the folded set or it double-counts one promise). Never applied to
    triage, whose pin block is the merge-adjudication surface and must show
    both rows. Defensive: any failure keeps the set unfolded."""
    try:
        from commitment_dedup import fold_suspected_duplicates
        return fold_suspected_duplicates(surfaced)
    except Exception:
        return surfaced, 0


def _dup_fold_note(d: dict) -> str | None:
    n = d.get("duplicate_fold_count")
    if isinstance(n, int) and n > 1:
        return f"{n} records — merge?"
    # INGESTDUP1 D2 — the id-twin collapse. Deliberately NOT worded as a merge
    # question: these are appends of ONE record, so there is nothing to
    # adjudicate and nothing for the reader to decide. It is said out loud
    # anyway, because a projection that quietly drops rows is how a count bug
    # becomes invisible (and this field is the collapse's only consumer — an
    # unread stamp is a write-only field).
    t = d.get("id_twin_count")
    if isinstance(t, int) and t > 1:
        return f"written {t} times — counted once"
    return None


def _neg_ts(ts: str) -> str:
    """Sort helper — invert an ISO ts so `sorted(asc)` yields newest-first.
    Deterministic + stdlib-only: complement each digit so a later timestamp
    sorts earlier. Non-digits pass through (they compare stably)."""
    tbl = str.maketrans("0123456789", "9876543210")
    return (ts or "").translate(tbl)


def _last_brief_ts(workspace_root, now_iso: str) -> str:
    """The CHANGED window's opening edge: the newest prior morning-brief
    receipt's timestamp, else the newest `brief_state` event's ts, else
    36 hours back (first-ever brief — one day plus slack so an overnight
    install still gets a real window, never an empty-string scan)."""
    from event_time import parse_ts
    from receipts import iter_receipts

    try:
        receipts = iter_receipts(workspace_root, task_ids=["morning-brief"])
        dts = [r["dt"] for r in receipts if r.get("dt") is not None]
        if dts:
            return max(dts).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    try:
        import json as _json
        newest = None
        p = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if '"brief_state"' not in line:
                    continue
                try:
                    ev = _json.loads(line)
                except Exception:
                    continue
                # EVGUARD sibling-rail (joined by the Slot 9 sweep) — a
                # top-level bare string containing `brief_state` clears the
                # substring pre-filter above and PARSES, so it used to reach
                # `.get()`. The AttributeError was swallowed by this function's
                # outer `except Exception: pass`, and the brief's CHANGED
                # window silently collapsed to the 36-hour floor.
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") == "brief_state" and ev.get("ts"):
                    newest = ev["ts"]
        if newest:
            return newest
    except Exception:
        pass
    base = parse_ts(now_iso)
    return (base - _dt.timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sent_reconcile_cursor(workspace_root) -> str | None:
    """The workspace's `sent_reconcile_cursor`, read through the CANONICAL
    dual-shape accessor (BRIEFSTATE1).

    Why not an inline `entities["workspace"]["sent_reconcile_cursor"]`:
    entities.json exists in two live shapes — flat (`workspace` at the top
    level) and nested (`entities.workspace`) — and the reconcile writer
    (`reconcile_sent_commitments._write_cursor`) maintains whichever shape
    the file already has. On a workspace whose cursor sits in the INNER
    block a top-level read returns None, `reconcile_is_stale(None, ...)`
    returns True, and the brief announces staleness over a cursor that
    advanced hours ago. `connector_config.workspace_block` is the merged
    read of both shapes (inner wins on conflict), so this reads back
    whatever the writer wrote on either shape.

    Precisely how far that agreement goes, since the neighbouring reader is
    NOT the same read: `_write_cursor` writes into the inner container
    whenever the `entities` wrapper exists and into the top level otherwise,
    so this merged reader recovers every shape that writer can PRODUCE.
    `reconcile_sent_commitments._read_cursor` is inner-if-wrapper with no
    merge, so on a shape the writer cannot produce — a wrapper workspace
    carrying an orphan TOP-LEVEL cursor — the two readers disagree: this one
    returns that value, reconcile-sent's own reader returns None. Reaching
    that shape takes a hand edit or a shape migration; the divergence is
    captured for intake rather than papered over here.

    Never raises. An unreadable entities.json degrades to None, which reads
    as stale — deliberately the conservative direction: soften the item
    rather than tell the CEO to redo work they may have finished.
    """
    try:
        from connector_config import workspace_block

        cur = workspace_block(workspace_root).get("sent_reconcile_cursor")
        return cur if isinstance(cur, str) and cur.strip() else None
    except Exception:
        return None


def _brief_thread_activity(workspace_root) -> dict:
    """{thread_id: latest-activity ISO} for the 7-day recent-activity drop,
    from THE canonical derivation (BRIEFSTATE1).

    ONE helper, BOTH bookends: the morning brief pack and the end-of-day pack
    call this and nothing else, because two surfaces quoting one day-count
    must derive it with one set of rules (the F-54 contract).

    SEMANTICALLY equivalent to the derivation the hand-driven path
    documents, by delegation — NOT the same call. `morning-briefing/SKILL.md`
    Step 3d instructs `derive_from_events(events,
    activity_types=BOOKEND_ACTIVITY_TYPES, honor_reclassifications=True)`
    over the events that fire already holds; this calls
    `derive_thread_activity(ws, …)` with the same two arguments, which
    delegates to `derive_from_events` over `_iter_events(ws)` with the same
    default `confidence_floor`. So the rules are identical — every event type
    counts EXCEPT the commitment-lifecycle writers, reclassifications folded
    (RECL1), the 0.40 floor applied — while the event SOURCE differs: the
    full shard-transparent stream here, the fire's already-loaded events
    there. Immaterial inside a 7-day window (shard contents are older than
    that by construction), and named rather than glossed because the equality
    is what makes the two fires quote one day-count. That equality is pinned
    in `tests/run_briefstate1_test.py`, not asserted here: a bespoke max(ts)
    scan or a dropped RECL1 fold would each be the C3 migration undone, and
    prose does not stop that.

    THE TYPE SET (SPEC BOOKENDS1, 2026-08-23). This used to pass `ALL_TYPES`
    — the filter OFF — described as "renderer last-touched semantics, the
    7-day stopgap's original intent". `thread_activity.derive_from_events`
    has always said the opposite for surfaces like this one ("never pass
    ALL_TYPES from a surface that quotes a day-count"), and the consequence
    was measurable: with every type counting, the `commitment_resolved`
    written when ONE item on a thread is closed marks that whole thread
    active for seven days and hides every OTHER open item on it. On real
    substrate, 7 items on one day (GATE0 2026-08-23: morning
    `needs_attention` 42 -> 49).

    `BOOKEND_ACTIVITY_TYPES` is that same last-touched breadth MINUS the
    enumerated commitment-lifecycle writers — not `DEFAULT_ACTIVITY_TYPES`,
    which would additionally stop `thread_updated` / `deal_won` /
    `objective_updated` counting (a separate narrowing nobody ruled on;
    GATE0 measured it at 51). The C3 migration's fear was losing the
    canonical derivation; the derivation, the RECL1 fold and the confidence
    floor are all still here. Do NOT re-widen this to `ALL_TYPES`:
    last-touched IS the right rule for the renderers (MASTER_TRACKER's
    column, the list-active tree), which keep passing it, and the wrong rule
    for a surface that decides whether to stop telling the CEO about
    something they still owe.

    `compute_brief_state` reads these values as ISO strings
    (`_within_recent_window` parses a string); the derivation returns
    tz-aware datetimes, so the render happens here rather than teaching the
    state computer a second input shape.

    Never raises. A failed derivation degrades to `{}` — the drop simply
    does not apply and the item surfaces, which is the safe direction
    (SKILL.md Step 3d: "pass what you have").
    """
    try:
        from thread_activity import (BOOKEND_ACTIVITY_TYPES,
                                     derive_thread_activity)

        rows = derive_thread_activity(workspace_root,
                                      activity_types=BOOKEND_ACTIVITY_TYPES,
                                      honor_reclassifications=True)
        return {tid: act.ts.isoformat() for tid, act in rows.items()
                if getattr(act, "ts", None) is not None}
    except Exception:
        return {}


def _brief_plate_block(ws, *, now_iso: str, user_id, state: dict,
                       ask: dict, view: dict | None = None) -> dict:
    """PLATE1 night 2 — `build_plate` -> `render_plate("brief")` for the
    morning pack, with the brief's gates applied at the cut (see the call
    site). Never raises into the fire: a render the jargon gate refuses or a
    loader error comes back as the refusal shape with `error` set, so the
    pack still builds and the orchestrator prints ONE line instead of lanes."""
    from commitment_state import PRIMARY_USER_REFUSAL_LINE
    from plate_view import build_plate, render_plate

    refusal = {"refused": True, "line": PRIMARY_USER_REFUSAL_LINE,
               "breakdown": "",
               "attention": 0, "rows": [], "pointer": "", "text": "",
               "excluded_ids": [], "block_totals": {}}
    if not user_id:
        return refusal
    dropped = {str(r.get("commitment_id") or "")
               for r in (state.get("dropped") or []) if isinstance(r, dict)}
    asks = {str(r.get("commitment_id") or ""): r["ask_line"]
            for r in (ask.get("rows") or [])
            if isinstance(r, dict) and r.get("ask_line")}
    try:
        # NUMBER1 3.4 — ONE BUILD for the fire. The audit row and this
        # render now state the same integer because they read the same view,
        # not because two builds happened to agree.
        if view is None:
            view = build_plate(ws, user_person_id=user_id, now_iso=now_iso)
        if not view.get("ok"):
            out = dict(refusal)
            out["line"] = view.get("error") or PRIMARY_USER_REFUSAL_LINE
            return out
        rendered = render_plate(view, "brief", False, exclude_ids=dropped,
                                ask_lines=asks)
    except Exception as exc:  # noqa: BLE001 — the fire must not crash
        out = dict(refusal)
        out["line"] = ("I couldn't read your plate this morning — say "
                       "`what's on my plate` and I'll try again.")
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    lines = rendered["text"].split("\n")
    # NUMBER1 3.1 (R-26) -- line two is the breakdown of line one, composed
    # by `plate_view.render_plate` and carried out here so the pack (and the
    # brief's own fences) see it. `lead_lines` below prints it directly
    # under the number, which is the only place it means anything.
    #
    # FIX ROUND 1 (REVIEW F-6) -- READ THE KEY, NOT THE TEXT BY INDEX. This
    # used to recover the line as `lines[1]` when it did not start with
    # "- ", i.e. by re-parsing the string the composer had just built out of
    # a variable it still held. `render_plate("brief")` now returns
    # `breakdown` itself, so one function composes the sentence and one key
    # carries it; a row line that ever failed the "- " test can no longer
    # be printed where the breakdown belongs.
    breakdown = rendered.get("breakdown") or ""
    return {
        "refused": False,
        "line": lines[0],
        "breakdown": breakdown,
        "attention": rendered["attention"],
        "rows": rendered["rows"],
        "pointer": rendered.get("pointer") or "",
        "text": rendered["text"],
        "excluded_ids": sorted(dropped & {r["id"] for r in view["rows"]}),
        "block_totals": rendered["block_totals"],
    }


def build_morning_brief_pack(workspace_root, *, mode: str = "scheduled",
                             now_iso: str | None = None,
                             catch_up_runner=None,
                             host_mode: str | None = None) -> dict:
    """t3 FB-9 — the morning brief's mandatory substrate blocks, assembled,
    validated, and persisted in ONE call (the t2.2 skip-proofing pattern
    that fixed commitments/staff-meeting). A live post-update fire skipped
    the brain-card widget AND the substrate-alarm line while the pre-update
    fire rendered both — instruction-layer MUSTs (FS-09) don't survive an
    orchestrator that stops early; a driver whose single output carries
    every block does.

    Returns (and persists to `_hq/.system/briefs/`) the pack:

      alarm_lines    substrate_health.substrate_alarm_lines — render
                     VERBATIM inside `health_lines`, LAST (FS-04/05/06/15;
                     CUT-PLATE — they were the top until v5.28.0).
                     This block is ALSO this entry point's mount-freshness
                     answer, and the reason it does NOT take the hard refusal
                     `run_board` / `run_surface` take. A stale view here is
                     already LOUD — the same syncing vocabulary, first block,
                     above everything — so the brief degrades honestly instead
                     of vanishing. The refusal exists on the other two because
                     a widget page and a board page carry no alarm surface at
                     all: they would publish stale rows and stale counts
                     silently.
      changed        change_feed.changes_since(<last brief ts>) — the lines
                     the CHANGED contract line MUST cite when non-empty
                     (FS-09; "Nothing material" over a non-empty feed is
                     the bug).
      brief_state    compute_and_log_brief_state's counts headline +
                     needs_attention + reconcile_stale. THE one Step-3d
                     derivation — the driver call writes the `brief_state`
                     audit event, so the orchestrator must NOT call it
                     again (one event per fire). BRIEFSTATE1: the two
                     substrate-derivable inputs ride the call —
                     `sent_reconcile_cursor` (so `reconcile_stale` means
                     something instead of being always-true) and
                     `thread_activity` (so the 7-day drop applies here too).
                     The connector-fed inputs do not; see the call site for
                     the named degradation.
      watchdog_line  task_watchdog.brief_watchdog_line — append verbatim
                     when non-None (S3 light pass). SPEC COVERQUIET1
                     ruling 4 ("morning and evening both") is already this
                     line's OWN posture and needed no change here: `None`
                     on a healthy morning, never a padded all-clear — the
                     same "speaks only when it has something to disclose"
                     rule the evening's `coverage` strip now follows
                     explicitly, applied here since S3.
      dark_surface_lines  TASKALARM1 — task_alarm.dark_surface_lines, the
                     per-task "X has not fired in N days" / "was never set
                     up on this machine" lines, capped 3 worst-first with an
                     "and N more" tail, render-once per (task, dark-window)
                     via task_alarm's own ledger. Empty list → nothing
                     renders (SPEC COVERQUIET1 ruling 4 — already true here,
                     unchanged by this build). Append verbatim, same spot as
                     watchdog_line.
      prior_captures_lines / n_prior_captures_narrated  MORNCAP1 — "captured
                     since your last close": meetings the background/
                     catch-up passes briefed after the last day-close
                     receipt, capped 3 with an "...and N more, filed." tail,
                     render-once per meeting via morning_capture's own
                     ledger (`morning_capture.narrated_since_close`). A
                     deferred-then-recovered meeting (the close's own
                     `window_incomplete_before` era) renders as "caught up
                     overnight: X", never as a fresh capture.
                     `n_prior_captures_narrated` is the TOTAL pending count
                     (capped-render or not) — zero-written onto the receipt,
                     never omitted.
      money_lines    FB-20's ONE carve-out: money-class proposals (deal
                     signals) as one prose sentence each, propose-only —
                     "Command Room thinks [Org] is a live deal — say staff
                     meeting to confirm." Money is the one class that may
                     never go silent, so it is NAMED in the brief; the
                     adjudication still happens at the staff meeting, by
                     chat phrase, with no widget. Capped at
                     MONEY_PROSE_CAP (the pointer's count carries the rest —
                     a cap is a render bound, never a silence).
      queue_pointer  {count, line} — the live count of everything the staff
                     meeting would render, and the ONE pointer line that
                     replaces the card ("N things need your eyes — say
                     staff meeting"). count == what `staff meeting` actually
                     shows (same projector, same surface, same held/mute
                     filters), so the number can never over-promise. Zero →
                     empty line, nothing renders (drop-empty).

    FB-20 (M's ruling 2026-07-16 — "the morning brief should just be a
    morning brief"): this pack emits NO widget and NO confirm card. The
    brief is a READ-ONLY prose surface; the staff meeting is the sole
    adjudication surface (run it more often instead). A `transport` key from
    this driver is a contract violation — the T3.2 relay machinery stays
    intact for every OTHER surface, but the brief has exited the widget
    business entirely. There is nothing to relay, so nothing can be dropped
    on the way to the relay (the FB-18 failure mode is gone by construction,
    not by instruction).

    Every text line in the pack passes the chat-output leak scan before
    return — a leaking canonical line fails HERE, loudly, not in M's chat.
    Connector-dependent digest content (calendar, mail, Slack) remains the
    orchestrator's job; this pack is the substrate half — the half that
    kept getting skipped.
    """
    from chat_output_renderer import validate_chat_output
    from change_feed import changes_since
    from commitment_state import compute_and_log_brief_state
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from substrate_health import substrate_alarm_lines

    if mode not in ("scheduled", "manual"):
        raise ValueError(f"mode must be scheduled|manual; got {mode!r}")
    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()

    # HEAL1 — THE UPKEEP RUNS FIRST, AND THE READER NEVER HEARS ABOUT IT.
    #
    # On demand only (`mode == "manual"`): a scheduled fire on a seat whose
    # scheduler works already has the maintenance task, and one on a seat
    # whose scheduler does not never reaches this line at all. So the catch-up
    # rides exactly the moment a person opened the workspace and typed.
    #
    # BEFORE the gather, deliberately: the whole point is that the brief reads
    # an already-reconciled substrate (Bug #98-v3's reason for the 6:45 anchor,
    # one runtime over). With no runner this PLANS and the orchestrator runs
    # it; `catch_up_runner` is what the suite and a local operator pass.
    #
    # Nothing composed from it reaches the reader — behaviour 2 — and
    # `assert_no_reachability_line` below now reds by name if it does.
    catch_up = None
    if mode == "manual":
        catch_up = maintenance_catch_up(ws, MORNING_BRIEF_SURFACE,
                                        now_iso=now_iso,
                                        mode=host_mode,
                                        runner=catch_up_runner)

    # DATE1 (ATTENDED_TEST_v5.29.0 B2.1) — the header's weekday, ONE
    # composer, workspace-anchored, never prose. `Step 5` prints this
    # verbatim in the "Morning briefing — …" header; it must never derive
    # the day name itself the way the B2.1 regression did.
    from due_reanchor import render_today_header
    date_header = render_today_header(now_iso, workspace_path=ws)

    # DATE1 fix round 1 (REVIEW_DATE1 F-1) — `commitment_state.is_overdue` /
    # `overdue_days` slice `now_iso` the same raw-UTC way `_due_phrase` used
    # to: at 18:00 Pacific (01:00 UTC the next day) a row due exactly today
    # read `overdue: True` and the brief's "Done, new date, or drop?" ask
    # line counted one day too many ("18 days overdue" beside the SAME row's
    # own due-phrase, now correctly "17 days ago" two lines away). Anchored
    # ONCE here, through the identical `tz.localize_date` door, and handed
    # to every call below that reaches `is_overdue` / `overdue_days`
    # (`compute_and_log_brief_state` → `compute_brief_state` →
    # `count_commitments` + the needs_attention row stamp; `apply_overdue_ask`
    # → `overdue_ask_state`).
    from tz import localize_date
    _local_today = localize_date(now_iso, workspace_path=ws) or now_iso

    # `alarm_lines` stays a live read (HEALTH1 does not stop this call: unlike
    # the dark-surface / schedule-refresh lines below, `substrate_alarm_lines`
    # is not a render-once ledger — every caller sees the same finding, so the
    # brief calling it too costs the weekly maintenance report nothing, and
    # it is what keeps `alarm_artifacts.sweep_alerts` (its first step)
    # self-clearing a resolved regression alert daily instead of weekly). The
    # brief just never PRINTS it any more — see `health_lines` below.
    alarm_lines = list(substrate_alarm_lines(ws) or [])

    since_ts = _last_brief_ts(ws, now_iso)
    # QUIET1 D7 — the CHANGED window runs from the last user answer or
    # opened surface once the person has been away two days or more, not
    # from yesterday's brief: coming back is a summary with undo, never a
    # wall. `return_summary` is the "While you were out (N days): …" header
    # plus counts; the three feed lines beneath it are the same lines as
    # always, over the longer window. While the person is around, the
    # window and the lines are byte-identical to before.
    import quiet as _quiet
    since_ts, return_meta = _quiet.brief_window(ws, since_ts, now_iso)
    # SPEC_SURFACES2_11c BRIEF2 2.2 item 1 — the feed is asked for EVERYTHING
    # in the window and the cap is applied HERE, on the ordinary lines only.
    # Passing `max_lines=3` upstream threw away the machine's batch lines
    # before this surface could see them (the 09-13 brief: a 48-row lapse and
    # a 51-row rest batch, both cut, both reversible by one word). See
    # `brief_changed_lines` for what counts as a batch and why.
    feed = changes_since(ws, since_ts, now_iso=now_iso)
    changed_lines = brief_changed_lines(feed)
    return_summary = _quiet.return_summary(ws, now_iso) if return_meta else None
    # QUIET1 D3 — the one line that says the product stopped asking. Written
    # into the ledger as narrated the moment it is handed out, so a re-run
    # or tomorrow's brief cannot say it twice. Empty when there is nothing
    # to say.
    try:
        quiet_line = _quiet.step_down_narration(ws, now_iso=now_iso, mark=True)
    except Exception:
        quiet_line = ""

    opens = load_open_commitments(_events_path(ws))
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    # NUMBER1 3.4 — the plate is built ONCE, here, and its `open` is
    # what the audit row records and what the brief renders. Before this the
    # event carried `count_commitments`' confirmed-only total (342 / 305 on
    # 2026-09-16) while the plate rendered 354, and nothing on the ledger
    # said which of the three the reader had seen.
    brief_view = None
    brief_plate_open = None
    if user_id:
        try:
            from plate_view import build_plate as _build_plate
            from plate_view import plate_numbers as _plate_numbers
            brief_view = _build_plate(ws, user_person_id=user_id,
                                      now_iso=now_iso)
            if brief_view.get("ok"):
                brief_plate_open = _plate_numbers(brief_view)["open"]
            else:
                brief_view = None
        except Exception:  # noqa: BLE001 — the fire must not crash
            brief_view, brief_plate_open = None, None
    state = compute_and_log_brief_state(
        ws, open_commitments=opens, user_person_id=user_id, now_iso=_local_today,
        plate_open=brief_plate_open,
        # BRIEFSTATE1 — the substrate-derivable brief-state inputs, passed.
        # This call used to pass NONE of them, and two things followed
        # deterministically on every scheduled fire: the cursor defaulted to
        # None so `reconcile_stale` was structurally always True (the
        # staleness line rendered daily and every needs-attention row was
        # softened, on workspaces reconciling cleanly four times a day), and
        # the recent-activity drop could not apply at all, so this path and
        # the hand-driven Step-3d path disagreed by construction.
        sent_reconcile_cursor=_sent_reconcile_cursor(ws),
        thread_activity=_brief_thread_activity(ws),
        # DELIBERATE DEGRADATION, named so no one reads it as an oversight:
        # `threads` (Step 3c latest-sender) and `calendar_events` (Step 3c-bis)
        # stay unpassed because both require connector I/O — a Gmail
        # `get_thread` per linked thread and a Calendar `list_events` — and
        # this driver is connector-free by contract (every fetch belongs to
        # the orchestrator). So on a scheduled fire the latest-sender drop and
        # the calendar drop do not apply and those items surface; the manual
        # Step-3d path, which holds the fetches, keeps both. Surfacing an item
        # the CEO already handled by email is the safe direction of that
        # residual gap, and closing it properly means threading cached
        # connector state through the pack — a design item, not this build.
        # `todays_meetings` is likewise the orchestrator's: today's meetings
        # come out of the prep leg's calendar fetch (`prep_leg.run_prep_leg`),
        # which this pack builder neither calls nor receives. Nothing about
        # the pack changes as a result — this surface has never carried a
        # meeting-linked block for it to feed — so it is a drop-input, not
        # the defect.
        # BRIEFFIX1 Item C / F1 — the driver is the ONE place that already
        # knows the run mode, so it is the place that stamps it. Without this
        # the audit event is identical on both paths and the receipt-ordering
        # check cannot tell a hand-run brief from a scheduled one.
        fired_via=mode)
    # CAPTUREFLOW §D — the lane is BOUND here, at the render, never at the
    # derivation: `compute_brief_state` keeps returning the full list and the
    # `brief_state` audit event keeps counting all of it, so the header counts
    # stay unfiltered (the :299 doctrine). What the brief PRINTS is the top
    # N by due-then-age plus one honest pointer line, with the 14-day rotation
    # so nothing below the fold can go permanently invisible.
    from commitment_state import cap_needs_attention
    lane = cap_needs_attention(state.get("needs_attention") or [],
                               now_iso=now_iso)
    # SPEC EODSYNTH1 R-3 — THE OVERDUE ASK ARRIVES IN THE MORNING.
    #
    # The "Done, new date, or drop?" fork used to live in the evening's slipped
    # block. M's ruling moves it here, and the rule itself is UNCHANGED — same
    # threshold, same rest-until-answered fold, same `commitment_state.mark_asked`
    # write. `end_of_day.apply_overdue_ask` is the shared implementation both
    # bookends now call, which is the BOOKENDS1 discipline applied to a second
    # rule: one helper, so the two surfaces cannot ask two different questions
    # about the same row on the same day.
    #
    # It runs AFTER the cap, deliberately. Resting a row that the cap had
    # already pushed below the fold would mark a question the reader never
    # saw — the disappearance OVERDUE1's own `asked_ids` derivation is written
    # to avoid, one surface over. The asked rows then lead the lane, because a
    # question at the bottom of a five-row list is a question that gets
    # scrolled past.
    import end_of_day as _eod
    # QUIET1 D4 — the workspace root hands the morning's asks to the weekly
    # question budget (the `overdue_ask` asker); a row the budget cuts
    # stays on the lane unasked and unmarked.
    #
    # SPEC SURFACEFIX1 5.4 / amendment B-2 — READ THIS BEFORE CHANGING THE
    # `ask=` ARGUMENT BELOW. M ruled the brief's "pinned ask" off on
    # 2026-09-07 and read it on both 09-13 renders anyway (A4). The ask is
    # THIS call: the fatigue rule's "Done, new date, or drop?" riding a plate
    # row as its label, which R-3 moved off the evening and onto the morning.
    #
    # `ask=False` LOOKS like the fix and is not. The rest-until-answered fold
    # keys on the ask-once MARKER this call writes, so a morning that never
    # asks is a morning that never marks, and a row that is never marked
    # never rests: `resting_line` goes empty forever and the fatigue rule
    # dies quietly with it. Measured, not reasoned — `run_overdue1_test`
    # night 4 and `run_eodsynth1_test`'s morning lane both go red on exactly
    # that, and they are right to.
    #
    # So the ask stays here and the question needs a HOME, not a switch: the
    # Staff Meeting, which is where M's design rule of 2026-09-06 puts every
    # question.
    #
    # FOLD1-B 1.2 item 4(b), 2026-09-14 — THE HOME NOW EXISTS, so the switch
    # is finally safe to throw. `needs_review_queue.overdue_ask_section`
    # renders the question on the Staff Meeting, draws it from the SAME
    # weekly budget (`quiet.ASKER_OVERDUE`), and `run_surface` writes the
    # ask-once marker after that post through `mark_overdue_asked`. So the
    # marker still gets written every week the question is asked, and the
    # rest-until-answered fold holds.
    #
    # VERIFIED BEFORE THE FLIP, not assumed (the SURFACEFIX1 note above was
    # half right): `ask=False` never skipped the marker READ. `overdue_ask_
    # state` returns REST off the row's own live mark whatever `ask` says,
    # so `resting_ids` and `resting_line` are byte-identical either way.
    # What `ask=False` alone lost was the WRITE — `asked_ids` came back
    # empty, so `mark_lane_asked` wrote nothing and no row was ever marked.
    # Nothing inside this function needed decoupling; what was missing was a
    # surface that asks. Now one does.
    _ask = _eod.apply_overdue_ask(
        lane["shown"], now_iso=_local_today,
        ask_after_days=_eod.overdue_ask_after_days(ws), ask=False,
        workspace_root=ws)
    brief_state = {
        "headline": (state.get("counts") or {}).get("headline") or {},
        "needs_attention": _ask["rows"],
        # UNCHANGED, and that is the point: a resting row is still on the
        # you-owe list, so the denominator still counts it. A total that
        # quietly shrank the morning a row went quiet would be the cap
        # doctrine's own dishonesty wearing the fatigue rule's clothes.
        "needs_attention_total": lane["n_total"],
        "needs_attention_more": lane["n_more"],
        "needs_attention_more_line": lane["more_line"],
        "reconcile_stale": state.get("reconcile_stale"),
        # SPEC EODSYNTH1 R-3 — what the fatigue rule did this morning. The
        # orchestrator prints `resting_line` when it is non-empty and calls
        # `commitment_state.mark_asked` for `asked_ids` AFTER the post.
        "asked_ids": _ask["asked_ids"],
        "resting_ids": _ask["resting_ids"],
        "n_resting": _ask["n_resting"],
        "resting_line": _ask["resting_line"],
        "ask_after_days": _ask["ask_after_days"],
    }

    # PLATE1 night 2 — THE BRIEF'S CUT OF THE PLATE (D7 `brief`, NUMBERS1
    # R-1). ONE attention number + one pointer, and the top DO IT / CHASE
    # rows with the block's verb. Same model, same renderer, same words as
    # `what's on my plate`; the brief passes only its surface and its gates:
    #   * `exclude_ids` — the rows compute_brief_state DROPPED this fire
    #     (calendar action / email reply / recent activity). A "Do:" line for
    #     an item the CEO already handled is the Bug #93 class; the plate's
    #     rows are not gated, so the gate is applied HERE, at the brief's
    #     cut. The attention NUMBER still counts them (it is the plate's
    #     number and reads the same on every surface).
    #   * `ask_lines` — the fatigue rule's question rides the row it asked
    #     about (OVERDUE1 / EODSYNTH1 R-3), verbatim, as its label.
    # `asked_ids` is then narrowed to the rows the cut actually PRINTS: a
    # question the reader never saw is not a question (OVERDUE1's own rule).
    # An unresolvable primary user refuses with the one plain line and no
    # rows — the packs no longer degrade quietly (D8).
    plate = _brief_plate_block(ws, now_iso=now_iso, user_id=user_id,
                               view=brief_view,
                               state=state, ask=_ask)
    brief_state["asked_ids"] = [i for i in _ask["asked_ids"]
                                if i in {r["id"] for r in plate["rows"]}]

    # HEALTH1 (2026-09-07) — M: reading the substrate alarm on his own brief,
    # "this should not be shown"; ruled in full for all four health/plumbing
    # kinds (substrate alarms, the watchdog line, the dark-surface line, the
    # schedule-refresh line). They no longer render on the morning brief AT
    # ALL — not last, not softened, not anywhere — and the lane does NOT
    # merely stop PRINTING them here: `dark_surface_lines` and
    # `schedule_refresh.announce_lines` each mark their own render-once
    # ledger the instant they are CALLED (not the instant they are shown), so
    # a brief that kept calling them unrendered would silently consume the
    # only copy of the finding before the maintenance run (`cleanup`, the
    # weekly job M's ruling names as the sole place these are reported AND
    # the only surface where a cleanup pass can actually be offered and run)
    # ever got a turn to see it. So the calls themselves move, not just the
    # print statements — `cleanup`'s Monday-note pass is now the only reader
    # of `task_alarm.dark_surface_lines` / `schedule_refresh.announce_lines`.
    # `watchdog` (`task_watchdog.brief_watchdog_line`, the S3 light daily
    # pass) has no such ledger — it is a pure read of `health_verdict` — but
    # it is retired from this surface anyway: never pad an all-clear, and a
    # line nobody prints is not worth a receipts scan every morning.
    # `pack["watchdog_line"]` / `pack["dark_surface_lines"]` /
    # `pack["schedule_refresh_announce_lines"]` stay on the pack (empty) so
    # an existing reader of the persisted JSON never hits a missing key.
    watchdog = None
    dark_surface_lines = []
    schedule_refresh_announce_lines = []

    # MORNCAP1 — "captured since your last close": meetings the background/
    # catch-up passes briefed after the last day-close receipt, narrated
    # here because the pass itself is silent by fence (EODSPEED1) and the
    # close's own narration only covers up to ITS OWN fire — nothing since
    # narrates what happened overnight until this section exists. Same
    # render-once posture as the two ledgers just above; best-effort, a
    # read failure never breaks the morning brief.
    try:
        from morning_capture import narrated_since_close as _narrated_since_close

        _prior_captures = _narrated_since_close(ws, now=now_iso)
        prior_captures_lines = _prior_captures["lines"]
        n_prior_captures_narrated = _prior_captures["n_narrated"]
    except Exception:  # noqa: BLE001
        prior_captures_lines = []
        n_prior_captures_narrated = 0

    # FB-20 — the queue POINTER (not the queue). The brief names no rows and
    # renders no card; it points at the surface that adjudicates.
    #
    # WHAT THE COUNT MEANS, precisely, because STAFFCUT changed it. The count is
    # the OPEN ITEMS on the staff-meeting projection — how many things are
    # waiting. It is no longer the number of ROWS that surface renders: the
    # staff meeting now groups items into evidence-class digests and bounds the
    # page, so on a heavy week it can render ~21 rows against ~100 open items.
    # "The same number by construction" was true before those two passes existed
    # and is not true now.
    #
    # ITEMS is the honest number for a POINTER, and deliberately so. The brief
    # sentence is "N things need your eyes" — a promise about the user's
    # workload, which the queue's own presentation choices must not deflate.
    # Reporting the row count would understate what is waiting, which is the
    # FS-09 dishonesty in the other direction; the staff meeting itself then
    # states its own render arithmetic in its section titles (§3.1), so the two
    # surfaces disagree about nothing — they are answering different questions.
    from brain_proposals import load_open_proposals, money_prose_lines
    queue = load_open_proposals(ws, "staff-meeting", now_iso=now_iso)
    # Auto-tier items are applied-then-narrated, never adjudicated (LB1
    # review F5) — they are not "things that need your eyes". Redundant
    # since LB2 flipped the projector default (include_auto=False) — kept
    # as defense-in-depth; the parity pin tests the projector, not this.
    queue = [i for i in queue if i.get("tier") != "auto"]
    money_lines = money_prose_lines(queue, cap=MONEY_PROSE_CAP)
    queue_pointer = {"count": len(queue), "line": _pointer_line(len(queue))}

    # CUT-PLATE — THE NUMBER LEADS (see the module constants). The lead is
    # the plate's own cut verbatim: the number line, the rows, the ONE
    # pointer. The count lines that used to compete with it sit below the
    # fold. The fence runs over the composed order here, so a driver that
    # ever re-orders these lists reds before the pack is handed out rather
    # than on a customer's screen.
    #
    # HEALTH1 (2026-09-07) — `health_lines` is EMPTY, always, on this
    # surface. M's ruling supersedes CUT-PLATE's "every health line goes to
    # the END": the substrate alarms (the duplicate-entry warning included),
    # the watchdog line, the dark-surface line and the schedule-refresh line
    # do not render on the morning brief AT ALL any more — not last, not
    # softened, nowhere. They are not lost: the maintenance run (`cleanup`'s
    # weekly Monday note) is the one place that now composes and reports
    # them, and the only place a cleanup pass can actually be offered and
    # run. The key stays on the pack, empty, so an existing reader of the
    # persisted JSON (`_hq/.system/briefs/morning-pack-*.json`) never hits a
    # missing key — see the golden citation at `tests/golden/
    # cutplate_brief_order.golden.txt` and `tests/run_health1_test.py`,
    # which reds by name if this list is ever composed from `alarm_lines` /
    # `watchdog` / `dark_surface_lines` / `schedule_refresh_announce_lines`
    # again without the composition also moving to the maintenance report.
    # SPEC_SURFACES2_11c BRIEF2 2.2 items 2 and 3 — LINE TWO, THEN THE ONE
    # LINE, THEN THE ROWS. Both reads are best-effort by construction (item
    # 5): each returns an empty line rather than raising, so a brief with an
    # unreadable day-intent store is a brief missing one line, not a
    # `surface_failed`. `_local_today` is the same workspace-local date every
    # other read in this fire uses — line two cannot be about another day.
    _intent = brief_day_intent_line(ws, today=_local_today)
    day_intent_line = _intent["line"]
    coaching_line = brief_coaching_line(ws)
    # 2.2 item 4 — consumed INSIDE the pack so it lands above the fences.
    explain_once_line = brief_explain_once_line(ws)
    lead_lines = ([plate["line"]]
                  + ([plate["breakdown"]] if plate.get("breakdown") else [])
                  + ([day_intent_line] if day_intent_line else [])
                  + ([coaching_line] if coaching_line else [])
                  + [r["line"] for r in plate["rows"]]
                  + ([plate["pointer"]] if plate.get("pointer") else []))
    # FOLD1A (SPEC_FLOW1 Lane H) — Waiting On's headline folds in HERE, below
    # the fold, beside the resting line and the queue pointer it already
    # joins. This is deliberately a SECOND number, not a replacement of the
    # plate's: CUT-PLATE / NUMBERS1 R-1 says the plate's number is the ONLY
    # one that may LEAD (`assert_number_leads` only refuses a count ABOVE
    # the plate's line); nothing in that rule forbids a second count below
    # it, and this is the exact line M's ruling named ("N owed to you, M
    # chases drafted" / "the plate line the brief already carries" — the
    # second clause is what PLATE1 night 2 / ONEPLATE1 already ship; this is
    # the first). Read straight off `brief_state["headline"]` — already
    # computed by `count_commitments`, never re-derived by hand (Bug #99).
    # Empty when there is nothing owed, same "never pad" rule every other
    # fold line follows.
    #
    # Honesty note (left open, see BUILD_FOLD1A "what this leaves open"):
    # M's own phrasing pairs this with "M chases drafted" — the live count
    # of chase drafts the (now-folded) Waiting On chat used to pre-stage
    # each morning inside its own fire (CRU phases 2.5-2.7). Moving that
    # drafting itself into the brief's own generation (a BRIEFMERGE-style
    # leg) is a build this lane did not do — folding the DRAFTING logic in
    # is a materially bigger change than folding the HEADLINE, and this
    # line says only what is true today: the count, and where to see them.
    # AT MERGE (night 10, REVIEW_FOLD1A F-9 x ONEPLATE1): the count comes
    # from the ONE projection like every other header, with the brief's
    # own count as the fallback. The brief renders no waiting rows of its
    # own, so there is no duplicate fold to subtract here (folded=0); the
    # `show waiting` page subtracts its own fold when it renders the rows.
    #
    # SPEC SURFACEFIX1 5.4 / amendment B-1 — AND THE DEGRADE IS LOUD NOW. If
    # the one projection could not answer, this used to fall back silently to
    # the brief's own figure — a DIFFERENT count, printed beside the plate's,
    # which is how the 09-13 brief said 60 over a plate of 334 (A4, B2.5).
    # The degrade is collected by name and the line is DROPPED: a reader who
    # is not told how many are owed is missing one line; a reader told two
    # different numbers cannot trust either.
    _headline_degraded: list = []
    _owed_h = _plate_headline(ws, now_iso=now_iso,
                              fallback=(brief_state.get("headline") or {}),
                              folded=0, folded_key="owed_to_you",
                              degraded_out=_headline_degraded)
    n_owed_to_you = int(_owed_h.get("owed_to_you") or 0)
    owed_to_you_line = (
        f"{n_owed_to_you} owed to you — say `show waiting` for the list."
        if n_owed_to_you and not _headline_degraded else "")
    fold_lines = [l for l in (owed_to_you_line, brief_state.get("resting_line"),
                              queue_pointer["line"]) if l]
    health_lines: list[str] = []
    _brief_text = "\n".join(lead_lines + fold_lines + health_lines)
    assert_number_leads(_brief_text, number_line=plate["line"])
    # SPEC SURFACEFIX1 5.4 — WHAT THE BRIEF COMPOSES ITSELF. The plate's ROW
    # lines are excluded from the ask fence and from nothing else: they carry
    # the customer's own titles (a row the reader wrote may legitimately end
    # in a question mark) and they carry the one ruled ask this lane could not
    # rehome (see the `apply_overdue_ask` note above). Everything the brief
    # writes in its own voice — the plate's lead line, the fold lines, the
    # pointer, the health slot — is fenced, so the next question to reach this
    # surface has to get past a red suite first.
    #
    # SPEC_SURFACES2_11c BRIEF2 — FOUR LINES JOIN THIS TEXT, AND ONE OF
    # THEM JOINS IT MARKED. The coaching line (2.2 item 3) and the explain-once line (item 4)
    # are the brief's OWN sentences and are fenced like every other one: the
    # explain-once line is the whole reason item 4 exists — prose had it
    # rendered AFTER the pack, which is below every fence here, so a planted
    # interrogative in `EXPLAIN_ONCE_LINES` would have reached the reader
    # untouched.
    #
    # LINE TWO (item 2) — fix round 1, review F-5. It is the CUSTOMER'S own
    # words, the same class as a plate row's title, and the build left it out
    # of this text entirely for that reason. That was one fence too many: it
    # also took line two out of the INTERROGATIVE half, which is not
    # user-text business — a stated intent is a statement, and "done, new
    # date, or drop?" is a question put to the reader whoever typed it. So it
    # joins MARKED (`as_reader_words`): `scan_no_pending_question` blanks the
    # span before it looks for the product's proposal vocabulary, and
    # `interrogatives_in` reads the words with the marks taken out. The marks
    # live only in this text; nothing the reader ever sees carries them.
    #
    # WHAT IS STILL EXCLUDED, AND ONLY HERE: the plate's ROW lines, which
    # carry the customer's own titles.
    #
    # THE ROW'S ASK IS NOT EXCLUDED ANY MORE (REVIEW_NIGHT11C M-13,
    # 2026-09-15). FOLD1-B rehomed the overdue fork to the Staff Meeting and
    # built this pack with `ask=False`, and the only thing pinning that
    # kwarg was a golden file: flipping it back to `True` left
    # `run_brief2_test`, `run_fold1b_test` and `run_surfacefix1_test` green
    # while the composed lead read "— 9 days overdue. Done, new date, or
    # drop?" three times. The fence could not see it because the row lines
    # were out of this text altogether.
    #
    # So the row's `ask_line` — which the PRODUCT composes, and which no row
    # carries at all while `ask` is false — joins the text, with the
    # customer's own title inside it MARKED, exactly as line two is. The row
    # line itself stays out: its title is the customer's and this fence has
    # no business judging it.
    _ask_lines = []
    for _row in (plate.get("rows") or []):
        _ask = str(_row.get("ask_line") or "")
        if not _ask:
            continue
        _title = str(_row.get("title") or "")
        _ask_lines.append(_ask.replace(_title, as_reader_words(_title))
                          if _title and _title in _ask else _ask)
    _composed = "\n".join(
        [plate.get("line") or ""]
        + ([as_reader_words(day_intent_line)] if day_intent_line else [])
        + ([coaching_line] if coaching_line else [])
        + _ask_lines
        + ([plate["pointer"]] if plate.get("pointer") else [])
        + fold_lines + health_lines
        + ([explain_once_line] if explain_once_line else []))
    # SPEC SURFACEFIX1 5.4 — the three fences the brief never had. All three
    # are loud, in code, BEFORE the pack reaches a chat turn:
    #   B-1  one open figure, or figures that sum to it (A4's four competing
    #        counts);
    #   B-2  the brief never asks (the pinned question on both 09-13 renders);
    #   R3   no sentence about a source being unreachable (B1.5).
    # SPEC_SURFACES2_11c BRIEF2 2.2 item 2 — THE COUNT FENCE READS THE
    # PRODUCT'S FIGURES, NOT THE CUSTOMER'S. Line two is the one line on this
    # surface the customer wrote end to end, and a stated item may contain a
    # digit — "close the 3 open Stone items" is a perfectly good answer to
    # "what is today about" and no answer at all to "how many are on my
    # plate". Reconciling it against line one would let the reader's own
    # sentence raise on the reader's own brief. It is excluded here and
    # nowhere else: the order fence above reads the real composed order, the
    # leak scan below reads every word of it, and the pack marks it (see
    # `day_intent.contains_digit`) so a receipt says why it was not counted.
    _counted_lead = list(lead_lines)
    if day_intent_line and day_intent_line in _counted_lead:
        _counted_lead.remove(day_intent_line)   # the one line, once
    _count_text = "\n".join(
        _counted_lead + fold_lines + health_lines
        + ([explain_once_line] if explain_once_line else []))
    assert_single_open_count(_count_text,
                             headline=headline_number_in(plate.get("line")))
    assert_brief_never_asks(_composed)
    assert_no_reachability_line(_brief_text)
    # SPEC_SURFACES2_11c BRIEF2 2.2 item 3 — fix round 1, review F-3 (ruling
    # R-10). THE BRIEF DECLARES, AND THE SCAN RUNS. PROFILE1's coaching tier
    # fails closed: a surface that does not name itself coaching does not get
    # the note. The brief never named itself anything, and nothing on the chat
    # path ran the scan — so the one coaching line passed by the absence of a
    # gate. Now the fire declares (the coaching tag only when it actually
    # renders the line), and the scan runs over everything the pack composed.
    # The marks come off first: they are the ask fence's instrument and no
    # part of the reader's text.
    #
    # FIX ROUND 2, REVIEW R1 — THE DECLARATION EXEMPTS ONE SENTENCE, NOT THE
    # PAGE. Handing the coaching tag to the scan as the SURFACE exempted the
    # whole brief on exactly the mornings that carry coaching content: a
    # marked note planted in any other slot reached the reader. The brief now
    # always scans as the ordinary surface, with the one declared line
    # blanked out of the text first (`brief_coaching_scan_target`).
    _coaching_scan_text, _coaching_scan_surface = brief_coaching_scan_target(
        strip_reader_marks("\n".join(
            [_brief_text, _composed]
            + ([explain_once_line] if explain_once_line else []))),
        coaching_line)
    assert_no_coaching_leak(_coaching_scan_text,
                            surface=_coaching_scan_surface)

    # Leak-scan every text line the pack hands the orchestrator. Loud by
    # design — there is no widget validator behind this one any more.
    #
    # `prior_captures_lines` is deliberately EXCLUDED here, same as
    # `prep_leg.meeting_lines` always has been (BRIEFMERGE §C / BRIEFFIX1
    # Item A): both carry WORKSPACE-RELATIVE `.docx` hrefs, which this
    # validator's own dead-link rule refuses on sight — correctly, because a
    # relative pointer is dead in chat and only becomes postable after
    # Phase 6's `absolutize_doc_links` conversion. The driver runs long
    # before that conversion, so scanning these lines HERE would refuse
    # every real fire that ever narrates a capture. The orchestrator's
    # Phase 6 already re-validates the FULL composed digest after
    # absolutizing every doc link in it, which is where these lines get
    # their real leak scan — not skipped, just scanned at the right layer.
    scannable = "\n".join(
        # DATE1 — the header line is printed verbatim; scanned like the rest.
        ([date_header] if date_header else [])
        + alarm_lines + changed_lines + ([watchdog] if watchdog else [])
        # QUIET1 — the return header and the step-down line are printed
        # verbatim by the orchestrator, so they are scanned like the rest.
        + ([return_summary["header"]] if return_summary else [])
        + ([quiet_line] if quiet_line else [])
        + dark_surface_lines
        + schedule_refresh_announce_lines
        + money_lines
        + ([queue_pointer["line"]] if queue_pointer["line"] else [])
        # CAPTUREFLOW §D — the lane's overflow pointer is a text line the
        # orchestrator prints verbatim, so it is scanned like every other one.
        + ([brief_state["needs_attention_more_line"]]
           if brief_state.get("needs_attention_more_line") else [])
        # PLATE1 night 2 — the renderer's own two lines of the plate cut.
        + [plate["line"]] + ([plate["pointer"]] if plate.get("pointer") else [])
        # SPEC_SURFACES2_11c BRIEF2 — the three lines this lane composes are
        # scanned like every other one. Line two carries the customer's own
        # words, which is exactly why it is scanned: the leak gate blanks a
        # declared user span, it does not refuse the reader for quoting a
        # file name at their own day-close.
        + ([day_intent_line] if day_intent_line else [])
        + ([coaching_line] if coaching_line else [])
        + ([explain_once_line] if explain_once_line else [])
    )
    if scannable.strip():
        validate_chat_output(scannable)

    pack = {
        "surface": "morning-brief",
        "mode": mode,
        "now": now_iso,
        # HEAL1 — the catch-up's own record: the plan (and, when a runner ran
        # it, what ran and the one receipt). None on a scheduled fire. It is
        # a machine's note to a machine and never composes into a sentence.
        "catch_up": catch_up,
        # DATE1 — "Monday, September 7, 2026", workspace-anchored, printed
        # verbatim in the "Morning briefing — …" header (never re-derived).
        "date_header": date_header,
        "alarm_lines": alarm_lines,
        "changed": {"since_ts": since_ts, "lines": changed_lines,
                    # QUIET1 D7 — present only when the person has been
                    # away: {"header", "lines", "counts", "days", "since_ts"}.
                    # The orchestrator prints `header` ABOVE the CHANGED
                    # lines, verbatim, and nothing else from it.
                    "return_summary": return_summary},
        # QUIET1 D3 — "" or the one step-down line; printed verbatim once.
        "quiet_line": quiet_line,
        "brief_state": brief_state,
        # PLATE1 night 2 — the plate's brief cut: `line` (ONE number),
        # `rows` (top DO IT / CHASE with the block verb, gated), `pointer`,
        # `text` (the cut verbatim), `refused` + `line` on a no-user
        # workspace. The orchestrator renders it in the NEEDS ATTENTION
        # slot and records `rows[*].id` as `needs_attention_ids`.
        "plate": plate,
        # CUT-PLATE — the mechanical order. `lead` prints FIRST (verbatim,
        # the number then the rows then the pointer); `fold_lines` print
        # below the fold, in order; `health_lines` print LAST. The fields
        # they are composed from stay on the pack for their existing readers.
        "lead": {"lines": lead_lines, "text": "\n".join(lead_lines)},
        # SPEC_SURFACES2_11c BRIEF2 2.2 item 2 — LINE TWO, and the mark.
        # `line` is already inside `lead` (second, below the number); this
        # key exists so a reader of the persisted pack can tell the
        # customer's sentence from the product's without re-deriving it, and
        # `contains_digit` is the mark: a figure on the brief that the count
        # fence deliberately did not reconcile, because it is user text.
        "day_intent": {"line": day_intent_line, "items": _intent["items"],
                       "user_text": True,
                       "contains_digit": _intent["contains_digit"]},
        # 2.2 item 3 — "" on an observed seat, on a seat with the render
        # switch off, and on a seat whose coaching object names no
        # behaviour. Already inside `lead`, after line two, before the rows;
        # the orchestrator renders the lead verbatim and passes NO
        # `coaching_line` into `brief_settings.render_surface` (that would
        # print it twice).
        "coaching_line": coaching_line,
        # 2.2 item 4 — the first-week line, consumed HERE (inside the fences)
        # rather than by the prose after the pack. "" on every fire after the
        # first and on a workspace onboarding never armed. The orchestrator
        # renders it verbatim as the last line and calls `consume` NEVER.
        "explain_once_line": explain_once_line,
        "fold_lines": fold_lines,
        "health_lines": health_lines,
        "watchdog_line": watchdog,
        "dark_surface_lines": dark_surface_lines,
        "schedule_refresh_announce_lines": schedule_refresh_announce_lines,
        "prior_captures_lines": prior_captures_lines,
        "n_prior_captures_narrated": n_prior_captures_narrated,
        "money_lines": money_lines,
        "queue_pointer": queue_pointer,
        # SPEC SURFACEFIX1 5.4 — THE DEGRADE, BY NAME. Empty on an ordinary
        # morning. Non-empty means the one projection could not answer and
        # the owed-to-you line was DROPPED rather than filled with a second
        # count; the reason rides here so the health check and the operator
        # report can say so, and nobody has to infer it from a missing line.
        PLATE_HEADLINE_DEGRADED: list(_headline_degraded),
    }

    # Persist the pack (audit trail, parallel to the widget audit files).
    try:
        from atomic_write import atomic_write_text
        out_dir = ws / "_hq" / ".system" / "briefs"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_iso[:19].replace(":", "-")
        atomic_write_text(out_dir / f"morning-pack-{stamp}.json",
                          json.dumps(pack, indent=2, ensure_ascii=False))
    except Exception:
        pass  # the pack in hand is what matters; the audit copy is best-effort

    return pack


def _eod_plate_block(ws, *, now_iso: str, since_iso) -> dict:
    """PLATE1 night 2 — `build_plate(since_iso)` -> `render_plate("eod")`.
    Never raises into the fire; a no-user workspace or a render the jargon
    gate refuses comes back as the refusal shape (`refused`, one `line`)."""
    from commitment_state import PRIMARY_USER_REFUSAL_LINE
    from plate_view import build_plate, render_plate

    since = since_iso.isoformat() if hasattr(since_iso, "isoformat") else since_iso
    refusal = {"refused": True, "line": PRIMARY_USER_REFUSAL_LINE, "text": "",
               "delta": {"n_opened": 0, "n_closed": 0, "n_slipped": 0,
                         "since": since},
               "attention": 0, "block_totals": {}}
    try:
        view = build_plate(ws, now_iso=now_iso, since_iso=since)
        if not view.get("ok"):
            out = dict(refusal)
            out["line"] = view.get("error") or PRIMARY_USER_REFUSAL_LINE
            return out
        rendered = render_plate(view, "eod", False)
    except Exception as exc:  # noqa: BLE001
        out = dict(refusal)
        out["line"] = "I couldn't read your plate this evening."
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    return {"refused": False, "line": rendered["text"].split("\n")[0],
            "text": rendered["text"], "delta": rendered["delta"],
            "attention": rendered["attention"],
            "block_totals": rendered["block_totals"],
            "opened_ids": [r["id"] for r in view["delta"]["opened"]],
            "closed_ids": [c["id"] for c in view["delta"]["closed"]],
            "slipped_ids": [r["id"] for r in view["delta"]["slipped"]]}


def build_end_of_day_pack(workspace_root, *, mode: str = "scheduled",
                          now_iso: str | None = None,
                          close_result: dict | None = None,
                          calendar_events=None,
                          calendar_available: bool | None = None,
                          todays_meetings=None,
                          processed_meeting_ids=None,
                          connector_gaps=None,
                          held_ids=None,
                          lateness=None,
                          phase_ledger=None,
                          catch_up_runner=None,
                          host_mode: str | None = None) -> dict:
    """SPEC EOD1 — the End of Day fire's seven blocks, assembled in ONE call.

    The t3 FB-9 pattern the morning brief already runs on: one fetch per fire,
    blocks placed, never re-derived. The orchestrator does the connector half
    (mail, chat, calendar) and hands the results in; this assembles the read.

    Blocks, in render order (SPEC BK2's table is the contract):

      alarm_lines  substrate_health.substrate_alarm_lines — verbatim, pinned
                   top, never suppressed. Same block, same reason, same
                   degrade-honestly posture as the brief: a stale view here is
                   already LOUD, so the surface renders rather than vanishes.
      dark_surface_lines  TASKALARM1 — task_alarm.dark_surface_lines,
                   rendered directly under alarm_lines (same never-
                   suppressed posture, same phase). Render-once per (task,
                   dark-window) — a spell already alarmed on the morning
                   brief today stays quiet here rather than repeating.
      coverage     SPEC EODLEDGER1 — what this fire actually READ, per
                   capability: mail and chat through their own cursors (naming
                   the span when one is behind), calendar present or absent,
                   and the capture leg's window with what is still owed. Same
                   never-suppressed posture as `alarm_lines`, and placed
                   directly under them: a reader cannot weigh a number until
                   they know what aperture produced it, and everything below
                   this block is a number.
      score        the report card. The morning fire's receipt joined against
                   today's closures, plus the saved digest read back off disk.
                   NO morning receipt → "No plan on record this morning",
                   never a guessed score. An item with no close on file is
                   "Not recorded", NEVER "not done".
      wins         events since the morning fire, by NAME. Zero → one honest
                   line, never padding.
      slipped      the ball-is-on-you rows, from the GATED needs-attention set
                   only (Step 3c/3c-bis; Bug #93 class), max 3, verbs on each.
      confirm      confirm_flow.select_confirm_items relocated here, capped 5.
                   Held captures can never enter it.
      tomorrow     the wide calendar look, the rollover, and the day_intent
                   auto-DRAFT. The draft is `origin="proposed"` and is written
                   ONLY on tap-confirm, by the orchestrator, through BK1's
                   writer. Nothing in this call writes one.
      sign_off     computed, never composed. Zero urgent → the verbatim line.

    Monday additionally carries `week_rollup` (the prior week's day-scores,
    where a day with no fire reads "no close was recorded" and NEVER zero) and
    the `development_read` SLOT, which renders NOTHING until DEVREAD1.
    Friday is a plain day-close with NO hand-off line (Conflict D).

    **This driver writes no `brief_state` audit event.** It calls the PURE
    `compute_brief_state`, because `brief_receipt.orphan_brief_finding` reads
    the newest `brief_state` of any origin and expects a morning-brief receipt
    after it: an evening fire logging one would make every evening look like a
    morning brief that lost its receipt AND would stale-refuse the next
    morning's `mark done [n]`. The evening borrows the derivation; the morning
    keeps the write.

    SPEC EODLEDGER1 adds three things and no connector. `coverage` states the
    aperture (above). `score["ledger"]` states what the day did to the OPEN
    BOOK — book at open → opened → closed → dropped → book now — with the
    opening figure READ off the morning fire's own `brief_state` and NO
    arithmetic at all when there is none to read. And `catchup` is the label a
    LATE day-close arrives under: pass the Phase 2.9 `check_lateness` return as
    `lateness=` and, on the degrade tier only, the pack composes the span the
    read covers. Every field any of them renders is already computed here or
    read from this workspace's own ledger; nothing new is fetched.

    It writes no RECEIPT either. `end_of_day.log_end_of_day_receipt` is the one
    writer, called by the orchestrator AFTER the capture leg and BEFORE the
    post — the receipt carries the capture leg's window fields as well as this
    pack's id map, and it has to precede the numbered surface (BRIEFFIX1 Item
    C). See that function for why the order is load-bearing.

    SPEC EODPHASE1 times each phase. The pack carries `phase_timings` (the
    `end_of_day.PhaseLedger` snapshot) and `log_end_of_day_receipt` lifts it
    onto the receipt additively — no schema break, and a reader that does not
    know the field is unaffected.

    `phase_ledger` is the seam for the two things a snapshot on the RETURN
    VALUE structurally cannot give you. Pass an `end_of_day.PhaseLedger` and
    (a) you still hold the partial ledger if a phase RAISES and no pack is
    returned at all — the fire that dies in its slowest phase being exactly
    the one whose numbers matter — and (b) the orchestrator can time its own
    capture and close legs into the SAME ledger, so the receipt reads as one
    fire rather than as a driver with two unaccounted neighbours. Omit it and
    the driver keeps its own; the pack is identical either way.
    """
    import end_of_day as eod
    import eod_synthesis as _eod_syn
    from chat_output_renderer import validate_chat_output
    from commitment_state import cap_needs_attention, compute_brief_state
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from substrate_health import substrate_alarm_lines

    if mode not in ("scheduled", "manual"):
        raise ValueError(f"mode must be scheduled|manual; got {mode!r}")
    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()

    # HEAL1 — the upkeep runs first, on demand only, and silently. See the
    # identical block in `build_morning_brief_pack` for why it sits here and
    # why nothing composed from it reaches the reader. Same weekday family:
    # the Sunday family belongs to `weekly recap` and `run maintenance`
    # (R-M2), and a day-close that opened with a cleanup pass would be the
    # same wrong surface as a Monday brief that did.
    catch_up = None
    if mode == "manual":
        catch_up = maintenance_catch_up(ws, "end-of-day", now_iso=now_iso,
                                        mode=host_mode,
                                        runner=catch_up_runner)

    # DATE1 fix round 1 (REVIEW_DATE1 F-1) — see the identical comment in
    # build_morning_brief_pack. Handed to `compute_brief_state` (the
    # PHASE_BRIEF_STATE call below) and `compute_slipped` (which forwards it
    # into `overdue_ask_state` for the "overdue" sort key), the same two
    # readers of `is_overdue` / `overdue_days` the evening reaches.
    # `compute_confirm`'s own `now_iso` is left alone: it never reaches
    # `is_overdue` / `overdue_days` (its only date consumer,
    # `confirm_flow.select_confirm_items`, needs full timestamp precision
    # via `event_time.parse_ts`, not a bare calendar day) — anchoring it to
    # a date-only string would change nothing this finding is about and
    # risks changing hour-level recency reasoning that IS in this finding's
    # blast radius only by accident of sharing a parameter name.
    from tz import localize_date
    _local_today = localize_date(now_iso, workspace_path=ws) or now_iso
    if calendar_available is None:
        # A caller that handed over no fetch AT ALL had no calendar capability;
        # a caller that handed over an EMPTY fetch had one and tomorrow is
        # clear. The two are different claims and the default must not collapse
        # them — "nothing tomorrow" is a statement, "I could not look" is an
        # absence, and only the second belongs in `connector_gaps`.
        calendar_available = calendar_events is not None

    # The fire's OWN instant decides the day, resolved workspace-LOCAL through
    # tz.py. Never the process clock and never UTC: at 9 PM Pacific the UTC
    # calendar has already rolled over, which is the hour this fire runs.
    today = eod.workspace_today(ws, now=now_iso)
    for_date = today.isoformat()
    tomorrow_date = (today + _dt.timedelta(days=1)).isoformat()
    branch = eod.day_branch(today)

    # EODPHASE1 — the caller's ledger when it passed one (it keeps the partial
    # record if a phase raises), else our own.
    phases = phase_ledger if phase_ledger is not None else eod.PhaseLedger()

    with phases.phase(eod.PHASE_ALARMS) as _p:
        # `alarm_lines` stays a live read (HEALTH1 — see the identical note
        # in `build_morning_brief_pack`): `substrate_alarm_lines` is not a
        # render-once ledger, so every caller sees the same finding and the
        # weekly maintenance report loses nothing by this surface also
        # calling it. It is simply never PLACED on the composed screen any
        # more (`end_of_day.compose_screen` no longer adds it — CUT-PLATE's
        # "health lines LAST" is superseded by M's 2026-09-07 ruling: off
        # this surface entirely).
        alarm_lines = list(substrate_alarm_lines(ws) or [])
        # HEALTH1 (2026-09-07) — `dark_surface_lines` is DIFFERENT from
        # `alarm_lines`: `task_alarm.dark_surface_lines` marks its own
        # render-once ledger the instant it is CALLED, so if this fire kept
        # calling it just to leave it unrendered, it would silently consume
        # the only copy of the finding before the weekly maintenance run
        # (`cleanup`) — the surface M's ruling names as the sole reporter —
        # ever got a turn to see it. So the call itself is retired from this
        # driver, not only its rendering. This ALSO closes the day-close's
        # other route a dark-surface line could reach the customer: an empty
        # `pack["dark_surface_lines"]` can never trip
        # `end_of_day.coverage_has_disclosure`'s dark-surface branch, so the
        # coverage strip cannot borrow it as a lead sentence either (SPEC
        # COVERQUIET1 — untouched, out of HEALTH1's scope; its other four
        # disclosure triggers are unaffected). `pack["dark_surface_lines"]`
        # stays on the pack, empty, for existing readers of the persisted
        # JSON.
        dark_surface_lines = []
        _p.count(out=len(alarm_lines) + len(dark_surface_lines))

    soften = eod.soften_floor(close_result)
    softened = bool(soften.get("softened"))

    with phases.phase(eod.PHASE_BRIEF_STATE) as _p:
        opens = load_open_commitments(_events_path(ws))
        try:
            user_id = resolve_primary_user(ws)
        except Exception:
            user_id = None
        state = compute_brief_state(
            open_commitments=opens, user_person_id=user_id,
            now_iso=_local_today, workspace_root=str(ws),
            # EODSTATE1 — the declared same-class sibling of the morning fix
            # in `build_morning_brief_pack`. This call used to pass NEITHER
            # substrate-derivable input, and two things followed on every
            # evening fire: the cursor defaulted to None so `reconcile_stale`
            # was structurally True — a permanently-True per-item flag on
            # every slipped row, so the moment any surface reads it the
            # evening hedges every row falsely on a workspace reconciling
            # cleanly four times a day — and `thread_activity` was empty, so
            # the 7-day recent-activity drop could not fire at all and
            # `dropped` was empty BY CONSTRUCTION. That second one is what
            # made `compute_slipped`'s "rides the SAME gates / structurally,
            # not by discipline" docstring false on this path: the dropped
            # set it checks candidates against had nothing in it to check
            # against.
            #
            # BOOKENDS1 is why this lands as the SAME call as the morning's:
            # `_brief_thread_activity` is ONE helper passing ONE type set, so
            # the two bookends cannot quote different day-counts (F-54). On
            # the day this was measured the evening applied zero drops and
            # sat 95 items away from the morning; wiring it up with the
            # pre-BOOKENDS1 type set would have closed that gap by importing
            # the sibling-hiding bug into the evening too, which is why the
            # build order is BOOKENDS1 first and this second.
            #
            # DELIBERATE DEGRADATION, same as the morning path and named for
            # the same reason: `threads` (Step 3c latest-sender) and
            # `calendar_events` (Step 3c-bis) stay unpassed because both need
            # connector I/O, and this driver is connector-free by contract.
            # Surfacing an item the CEO already handled by email is the safe
            # direction of that residual gap.
            sent_reconcile_cursor=_sent_reconcile_cursor(ws),
            thread_activity=_brief_thread_activity(ws))
        lane = cap_needs_attention(state.get("needs_attention") or [],
                                   now_iso=now_iso)
        # SPEC EODSYNTH1 §3.6 — THE HELD FENCE, ONE LEVEL UP FROM WHERE IT
        # USED TO BE ENOUGH. `compute_confirm` has always fenced held rows out
        # of the confirm block, because that block asks about things. This
        # spec gives the evening two NEW doors onto the same rows — the
        # synthesized prose, and the tomorrow block's rollover, which is what
        # the one interaction proposes — and a held row reaching either makes
        # the flip a lie with extra steps. So the lane is fenced ONCE, here,
        # before anything downstream reads it. Naming it at the chokepoint
        # rather than in each consumer is what stops the next block that reads
        # this lane from needing to remember.
        _fenced = _eod_syn.visible_rows(lane["shown"], held_ids=held_ids,
                                        workspace_root=ws)
        brief_state = {
            "headline": (state.get("counts") or {}).get("headline") or {},
            "needs_attention": _fenced["rows"],
            "needs_attention_total": lane["n_total"],
            "needs_attention_more": lane["n_more"],
            "dropped": state.get("dropped") or [],
            "n_withheld": _fenced["n_withheld"],
        }
        # `in` is the open book this phase read; `out` is the lane the surface
        # will actually show. The gap between them IS the drop-and-cap work,
        # which is the expensive half.
        _p.count(n_in=len(opens), out=len(lane["shown"]))

    with phases.phase(eod.PHASE_MORNING_READ):
        morning = eod.morning_fire(ws, for_date, now_iso=now_iso)
        digest = eod.read_morning_digest(ws, morning)
        # SPEC SURFACES2_11c Lane 3 item 2 — ONE WINDOW, RESOLVED ONCE, FOR
        # THE WHOLE EVENING (REVIEW_SURFACEFIX1 R-1, the blocking finding).
        #
        # `anchor_ts` is None on a day whose morning brief never fired, and
        # that is the correct answer from `morning_fire`. Every reader below
        # used to floor it for itself (SPEC WINSFLOOR1), which was right until
        # SURFACEFIX1 fix round 1 lowered ONE of those floors to the day: from
        # that moment the machine-acts block read the day while the ledger,
        # the score and the wins block read the morning, and on 2026-09-11 the
        # same screen printed `0 closed` in its header, ten named closes three
        # lines below it, and "Nothing closed today that I can see" between
        # them.
        #
        # So the instant is resolved HERE, once, by `end_of_day.evening_window`
        # (the earlier of the morning anchor and the day floor — the
        # derivation `compute_machine_acts` used to carry inline), and handed
        # to every reader together with the NAME of the window it is. The
        # helpers still floor for themselves when called from anywhere else;
        # what this removes is five readers each deciding privately what
        # "today" means. Timed inside the morning read because that is what it
        # reads — the anchor this phase just resolved, against the workspace's
        # own midnight (EODPHASE1: an un-phased call is work the instrument
        # cannot see).
        anchor_ts = morning.get("ts")
        _evening_since, window_source = eod.evening_window(ws, anchor_ts,
                                                           now_iso=now_iso)
        since_ts = _evening_since.isoformat()
    with phases.phase(eod.PHASE_CLOSURES) as _p:
        closures = eod.closures_since(ws, since_ts, now_iso=now_iso,
                                      window_source=window_source)
        # `ClosureWindow` IS a list (with the window fields hung off it), so
        # its length is the closure count.
        _p.count(out=len(closures))
    open_ids = [str((ev.get("data") or {}).get("id"))
                for ev in opens if isinstance(ev, dict)]

    # THE LEDGER (SPEC EODLEDGER1 part 2). The opening figure is READ off the
    # morning fire's own `brief_state` — never inferred, and specifically never
    # today's count standing in for this morning's. `opens_since` uses the SAME
    # `_window` resolution `closures_since` does, so both sides of the ledger
    # are measured over one span.
    with phases.phase(eod.PHASE_LEDGER) as _p:
        opening = eod.opening_book(ws, morning, now_iso=now_iso)
        opened = eod.opens_since(ws, since_ts, now_iso=now_iso,
                                 window_source=window_source)
        opened_window_source = opened.get("window_source")
        ledger = eod.compute_ledger(opening=opening, n_opened=opened["n"],
                                    closures=closures, brief_state=brief_state)
        _p.count(out=opened["n"])

    with phases.phase(eod.PHASE_SCORE):
        score = eod.compute_score(morning=morning, digest=digest,
                                  open_ids=open_ids, closures=closures,
                                  softened=softened,
                                  # EODFIX1 — the first_move check needs to
                                  # know whether the close phase could see the
                                  # day at all: "nothing closed it" over a
                                  # stale cursor is not a measurement.
                                  close_legs=soften.get("legs"),
                                  ledger=ledger)
    with phases.phase(eod.PHASE_WINS) as _p:
        wins = eod.compute_wins(ws, since_ts, now_iso=now_iso,
                                window_source=window_source)
        # SPEC SURFACEFIX1 5.2 / amendment E-3 — THE DAY'S JOB RECEIPTS,
        # through the ONE reader that classifies them (`change_feed`, via
        # `end_of_day.compute_machine_acts`).
        #
        # AND IT IS THE SAME WINDOW AGAIN (11c item 2). SURFACEFIX1 fix round
        # 2 had to correct this comment because F-1b had split the two: the
        # block floored to the day while `compute_wins` kept the morning
        # anchor. The split is closed — `since_ts` above IS the one instant
        # every reader on this fire was handed, and `window_since` on the
        # return is that same instant, so the plate block below reads what
        # this block read rather than a second window that usually matches.
        # Timed INSIDE the wins phase deliberately: it is the other half of
        # "what moved today", and a second phase name for one read would be a
        # dialect. (`run_eodphase1_test` pins that every ledger read is inside
        # some phase — an un-phased call is work the instrument cannot see, so
        # a slow fire would point at the wrong leg.) Never fatal — the helper
        # swallows its own read failures and an evening with no machine line
        # still closes.
        machine_acts = eod.compute_machine_acts(ws, since_ts, now_iso=now_iso,
                                                window_source=window_source)
        # Read INSIDE the phase (EODPHASE1 pins that a call outside one is
        # work the instrument cannot see) and carried to the receipt below.
        machine_window_source = machine_acts.get("window_source")
        machine_window_since = machine_acts.get("window_since")
        _p.count(out=len(wins.get("rows") or [])
                 + len(machine_acts.get("rows") or []))
    with phases.phase(eod.PHASE_SLIPPED) as _p:
        slipped = eod.compute_slipped(
            brief_state=brief_state, morning=morning,
            todays_meetings=todays_meetings,
            processed_meeting_ids=processed_meeting_ids,
            now_iso=_local_today, softened=softened,
            # THE HONEST DENOMINATOR (EODLEDGER1). The lane handed over above
            # was ALREADY bounded by `cap_needs_attention`; without this the
            # slipped block would print "3 of 5" over a lane holding 41.
            lane_total=brief_state["needs_attention_total"],
            # SPEC OVERDUE1 — the fatigue threshold, read from THIS
            # workspace's End of Day config. The block stays a pure function
            # of what it is handed, so the config read lives out here with
            # every other I/O this driver owns.
            ask_after_days=eod.overdue_ask_after_days(ws),
            # SPEC EODSYNTH1 R-3 — THE EVENING ASKS NOTHING. The confirm/drop
            # queues and the "Done, new date, or drop?" fork move to the
            # MORNING surfaces, where the operator is in triage mode; 5 PM is
            # wind-down. The marker itself is untouched — `apply_overdue_ask`
            # is the same rule and `commitment_state.mark_asked` is the same
            # write — it is simply the morning that performs it now, and
            # `asked_ids` therefore comes back EMPTY here, so
            # `mark_slipped_asked` has nothing to do on this surface.
            ask=False)
        _p.count(n_in=len(brief_state["needs_attention"]),
                 out=len(slipped.get("rows") or []))
    with phases.phase(eod.PHASE_CONFIRM) as _p:
        # PLATE1 night 2 (P7 / EODRANK1 DD-1 superseded) — the confirm
        # block ranks on the evidence the plate renders: the newest
        # proposal riding the row (`data.proposal` when the resolution
        # policy wrote it there; the stream fold otherwise). One read, the
        # events_io seam, tolerant of the field being absent (main today).
        # The proposal is EVIDENCE, not a question: a guess a transcript
        # shows was done closes itself, and the middle band's chip acts or
        # retracts itself at the policy window (M's ruling 2026-09-03).
        try:
            import events_io as _events_io
            from plate_view import fold_proposals_and_hints as _fold
            _all_events = _events_io.load_all(ws)
            _proposals, _ = _fold(_all_events, now_iso=now_iso)
        except Exception:  # noqa: BLE001 — ranking is a bonus, never a crash
            _proposals = {}
        confirm = eod.compute_confirm(opens, now_iso=now_iso,
                                      held_ids=held_ids,
                                      proposals=_proposals)
        # PERSONLOOP1 §0-3 — the confirm block's second half. It rides INSIDE
        # the confirm block rather than as a new pack block, so `BLOCK_ORDER`
        # and every `blocks_rendered` claim are unchanged, and
        # `confirm_ids_from_pack` appends these rows LAST so no pre-existing
        # number moves. This is a CODE chokepoint on purpose: the fire's prose
        # cannot forget a step it does not perform.
        #
        # EODPHASE1 times the two together under one name because they are one
        # block to the reader and one decision to the operator: a phase split
        # nobody can act on differently is a number nobody can use.
        _pcand = eod.compute_person_candidates(ws, now_iso=now_iso)
        confirm["person_rows"] = _pcand["rows"]
        confirm["person_total"] = _pcand["n_total"]
        confirm["person_telemetry"] = _pcand["telemetry"]
        _p.count(out=len(confirm.get("rows") or [])
                 + len(confirm.get("person_rows") or []))
        # PLATE1 night 2 — THE DAY'S DELTA IN THE PLATE SHAPE (D7 `eod`).
        # Computed here, carried on the pack and its persisted copy, and
        # RENDERED NOWHERE on the scheduled fire: EODSYNTH1 (M, 2026-08-23)
        # stands — 5 PM is wind-down, no lists; `RENDER_ORDER` and
        # `COMPUTED_ONLY` are byte-pinned and this build touches neither
        # (the `catchup` / `coach` precedent: a key outside both tuples).
        # The block is the plate's own words for what the window did —
        # opened / closed / slipped over the SAME anchor the ledger reads
        # (`since_ts`, day-floored the same way) — so the manual family and
        # the morning can read one shape. Timed inside this phase rather
        # than growing EODPHASE1's pinned vocabulary. Best-effort: a plate
        # that cannot build comes back as the refusal shape, never a crash.
        #
        # AND THE WINDOW IS THE FIRE'S ONE WINDOW (11c item 2). The header
        # this block prints says "Your plate today", and so does the block
        # four lines below it: on 2026-09-11 the header counted from the
        # morning brief and printed `0 closed` directly above three lines
        # naming ten closes. SURFACEFIX1 fix round 2 patched that by having
        # this block borrow the machine-acts block's own instant; now there
        # is only one instant on the fire and every reader — this one
        # included — is handed it. Taken, never re-derived: re-deriving a
        # window is exactly how these came apart.
        plate = _eod_plate_block(ws, now_iso=now_iso, since_iso=since_ts)
    with phases.phase(eod.PHASE_TOMORROW) as _p:
        tomorrow = eod.compute_tomorrow(ws, for_date=tomorrow_date,
                                        calendar_events=calendar_events,
                                        brief_state=brief_state,
                                        calendar_available=calendar_available)
        _p.count(out=len(tomorrow.get("rollover") or []))
    with phases.phase(eod.PHASE_SIGN_OFF):
        sign_off = eod.compute_sign_off(slipped=slipped, tomorrow=tomorrow,
                                        now_iso=now_iso,
                                        brief_state=brief_state)

    # THE COVERAGE STRIP (SPEC EODLEDGER1 part 1). Composed LAST among the
    # blocks and placed FIRST among them: it needs `unsourced_closes` and the
    # tomorrow block's own answer about the calendar. That last point is the
    # fence, not a convenience — "the calendar was not read" and "tomorrow is
    # empty" rendered identically before this spec, and they cannot disagree
    # now because there is one boolean behind both of them.
    with phases.phase(eod.PHASE_COVERAGE) as _p:
        unsourced = eod.unsourced_closes(closures)
        coverage = eod.compute_coverage(
            ws, close_result=close_result, connector_gaps=connector_gaps,
            calendar_available=tomorrow["calendar_available"],
            now_iso=now_iso, n_unsourced=len(unsourced),
            capture=eod.capture_aperture(ws, now_iso=now_iso))
        _p.count(out=len(unsourced))

    # THE CATCH-UP READ (SPEC EODLEDGER1 part 3, M's ruling on D3). Present on
    # every fire and `renders: False` on all but the degrade tier — one
    # unconditional thing for the fire to read, the same posture `directive`
    # takes. `lateness` is the Phase 2.9 return, unmodified; nothing here
    # recomputes lateness and nothing here touches `late_fire`.
    with phases.phase(eod.PHASE_CATCHUP):
        catchup = eod.compute_catchup_read(lateness=lateness or {},
                                           workspace_root=ws, now_iso=now_iso)

    # THE SYNTHESIS (SPEC EODSYNTH1). The evening says how the day WENT; the
    # score is computed above and rendered nowhere (R-1). Everything below is
    # render layer: no connector, no new fetch, no write. The ledger goes in
    # WHOLE rather than as extracted counts, because §3.5's pin is that the
    # prose reads the same fields the receipt carries — hand the composer a
    # pre-flattened count and the two can drift apart again, which is the
    # F-W1 class ("closed 4" on one surface, "0 closed" on another, same day).
    with phases.phase(eod.PHASE_SYNTHESIS) as _p:
        import eod_synthesis as syn

        decisions = eod.todays_decisions(ws, since_ts, now_iso=now_iso)
        notes = eod.todays_notes(ws, since_ts, now_iso=now_iso)
        arcs = eod.declared_arcs(ws, for_date=for_date, now_iso=now_iso)
        # SPEC SURFACES2_11c Lane 3 item 5 — THE BEHAVIOUR THE SEAT NAMED,
        # handed in as one more declared arc. `coaching_doors.surface_deltas`
        # has promised this sentence to every seat that opens the coaching
        # door ("the pattern line you already have, plus one sentence tying
        # the day to what you are working on") and nothing produced it (11a
        # N-13). It joins, ranks and renders through the arc read's own
        # machinery — no second code path, no second template — and
        # `eod_synthesis.behaviour_arc` returns None on an observed seat, so
        # an ordinary evening is byte-identical to before.
        #
        # FIRST in the list, deliberately. The arc read names at most two arcs
        # and spells the first one "The day went into {arc} — {what}."; on a
        # seat that opened the door, the behaviour is the thing the seat asked
        # to have the day read against, so it leads and a declared arc can be
        # the one the cap drops. On every other seat this list is untouched.
        _behaviour_arc = syn.behaviour_arc(ws)
        if _behaviour_arc:
            arcs = [_behaviour_arc] + list(arcs)
        # Tier 3 is ABSENT BY DEFAULT and this driver supplies no candidates:
        # a precedent echo needs a recorded precedent with an outcome, and
        # nothing in this fire's inputs carries one. The seam is here, wired
        # and empty, so the block that adds echoes adds a producer rather than
        # a render path — and so the "rationed to two, each citing an id"
        # fence is already load-bearing the day the first one arrives.
        # SPEC EODARC1 — the OPEN BOOK enters the arc read: the unmoved-
        # with-consequence paragraph is a claim about what is WAITING, and
        # the open commitments this fire already loaded are that claim's
        # rows. Deal state enters nowhere (ruling 3): nothing below reads
        # the deal tracker, so its absence, staleness, or corruption cannot
        # subtract an arc or raise.
        open_rows = eod.open_commitment_rows(opens, workspace_root=ws,
                                             now_iso=now_iso)
        synthesis = syn.build_synthesis(
            ledger=ledger,
            closures=list(closures),
            meetings=todays_meetings,
            decisions=decisions,
            notes=notes,
            arcs=arcs,
            # The rows Tier 2 may join FROM: today's closes, today's meetings
            # and today's decisions. Slipped rows are deliberately NOT here —
            # a thing that did not happen did not land on an arc.
            arc_rows=list(closures) + list(todays_meetings or []) + decisions,
            open_rows=open_rows,
            for_date=for_date,
            slipped_rows=slipped.get("rows") or [],
            echo_candidates=(),
            held_ids=held_ids,
            # SPEC SURFACEFIX1 5.2 / FIX ROUND 1 (F-3) — the day's machine
            # acts, so the synthesis can say the machine's share of the drops
            # by DOOR and skip any door the machine-acts block already
            # narrated. One act, one sentence.
            machine_acts=machine_acts,
            workspace_root=ws,
            # SPEC TOMFILT1 §2 — re-anchors the slipped line's due clause to
            # THIS fire's own clock, the same `now_iso` every other phase
            # here reads from.
            now_iso=now_iso,
            # CUT-PLATE — the arc read's "did not move" sentences stay on
            # the block as data (`what_it_meant["unmoved"]`) and off the
            # screen: the evening reads the day in the plate's shape.
            render_unmoved=False)
        _p.count(n_in=len(arcs), out=len(syn.synthesis_refs(synthesis)))

        # SPEC EODCOACH2 — the two coaching layers on top of the arc read:
        # pattern memory across closes, and the intent-vs-outcome delta plus
        # one push line. Timed inside THIS phase rather than a new one —
        # `end_of_day.PACK_PHASES` is a pinned 16-name vocabulary
        # (EODPHASE1) and this build does not grow it; the coach's cost
        # scales with the same "how much the day did" driver synthesis
        # already does, so it shares the phase honestly.
        #
        # `today_closures` / `today_open_rows` are a THIRD door onto rows
        # `build_synthesis` already had to fence (EODARC1 §3.6) — the arc
        # read and the tomorrow rollover were the first two. `open_rows`
        # above is the UNFENCED projection; fence it here exactly as
        # `build_synthesis` fences its own copy, rather than trust the
        # caller's copy is still safe to read a second time.
        import eod_coach as _eod_coach

        _fenced_closures_for_coach = syn.visible_rows(
            list(closures), held_ids=held_ids, workspace_root=ws)["rows"]
        _fenced_open_rows_for_coach = syn.visible_rows(
            open_rows, held_ids=held_ids, workspace_root=ws)["rows"]
        # The arc label map the arc read itself would have built internally
        # (declared arcs plus the SAME minted commitment arcs
        # `compute_what_it_meant` mints from this fire's own open book) —
        # reconstructed here, from the same two inputs, so Layer 1's
        # stillness sentences can name an arc the same way the arc read
        # already did rather than inventing a second label source.
        _all_arcs_for_coach = list(arcs) + syn.mint_commitment_arcs(
            _fenced_open_rows_for_coach, arcs)
        _arc_label_by_ref = {
            syn.arc_ref(a): (a.get("label") or a.get("arc_id"))
            for a in _all_arcs_for_coach if syn.arc_ref(a)
        }
        coach = _eod_coach.build_coach(
            workspace_root=ws, for_date=for_date,
            today_what_it_meant=synthesis[syn.BLOCK_WHAT_IT_MEANT],
            today_slipped_prose=synthesis[syn.BLOCK_SLIPPED_PROSE],
            today_closures=_fenced_closures_for_coach,
            today_open_rows=_fenced_open_rows_for_coach,
            arc_label_by_ref=_arc_label_by_ref)

    pack = {
        "surface": eod.SURFACE,
        "task_id": eod.TASK_ID,
        "mode": mode,
        "now": now_iso,
        # HEAL1 — the catch-up's own record; see the morning pack's key.
        "catch_up": catch_up,
        "for_date": for_date,
        "branch": branch,
        "alarm_lines": alarm_lines,
        # TASKALARM1 — rendered "by instruction" like `catchup` (see that
        # key's own comment above): never inside `render_order`/`BLOCK_ORDER`,
        # which are pinned by exact-equality guards elsewhere and this build
        # touches none of them. Placed directly under alarm_lines/coverage in
        # the orchestrator prose — the same never-suppressed spot.
        "dark_surface_lines": dark_surface_lines,
        "coverage": coverage,
        "catchup": catchup,
        "close": close_result or {},
        "soften": soften,
        "score": score,
        "wins": wins,
        # SPEC SURFACEFIX1 5.2 / E-3 — one line per machine batch, by door,
        # with its undo phrase. A SCREEN block (`end_of_day.SCREEN_ORDER`),
        # never a member of `render_order` / `BLOCK_ORDER` / `COMPUTED_ONLY`
        # — those three are byte-pinned receipt vocabulary and this build
        # changes exactly one of them (`coverage`, per R3) and no other.
        "machine_acts": machine_acts,
        "slipped": slipped,
        "confirm": confirm,
        # PLATE1 night 2 — the day's delta in the plate shape (see the
        # confirm phase). NOT in `render_order` / `BLOCK_ORDER` / `COMPUTED_ONLY`
        # (all byte-pinned). CUT-PLATE (2026-09-06): it RENDERS — first on
        # the screen `end_of_day.compose_screen` composes below.
        "plate": plate,
        "tomorrow": tomorrow,
        "sign_off": sign_off,
        # SPEC EODSYNTH1 — the five prose blocks, plus each one flattened to a
        # top-level key so `RENDER_ORDER` can be walked over the pack directly.
        # Two spellings of one object, not two objects: the flattened keys ARE
        # the entries of `synthesis`, so a renderer reading either finds the
        # same text and the same refs.
        "synthesis": synthesis,
        syn.BLOCK_DAY_WENT: synthesis[syn.BLOCK_DAY_WENT],
        syn.BLOCK_WHAT_IT_MEANT: synthesis[syn.BLOCK_WHAT_IT_MEANT],
        syn.BLOCK_WORTH_REMEMBERING: synthesis[syn.BLOCK_WORTH_REMEMBERING],
        syn.BLOCK_SLIPPED_PROSE: synthesis[syn.BLOCK_SLIPPED_PROSE],
        syn.BLOCK_ECHOES: synthesis[syn.BLOCK_ECHOES],
        # SPEC EODCOACH2 — the two coaching layers, prose only, rendered
        # directly under the synthesis and above `tomorrow` (never inside
        # `render_order`/`BLOCK_ORDER`: precisely the `catchup` precedent —
        # a key the fire renders by instruction, not by tuple membership,
        # because those tuples are pinned by exact equality elsewhere and
        # this build touches none of them).
        _eod_coach.BLOCK_COACH: coach,
        "render_order": list(eod.RENDER_ORDER),
        "computed_only": list(eod.COMPUTED_ONLY),
        "brief_state": brief_state,
        "connector_gaps": list(connector_gaps or []),
        "unsourced_closes": unsourced,
        # EODLEDGER1 — what entered the book in the same window the closes were
        # read over. The ledger's other side; carried so a reader can check the
        # arithmetic against the two counts rather than trust the sentence.
        "opened": opened,
        # WHICH WINDOW THE EVENING READ (SPEC WINSFLOOR1). `wins` carries its
        # own copy for the renderer; this is where the closure read — which has
        # no block of its own — says the same thing. Read STRICTLY off the
        # object each helper returned: a defaulted read would report the wrong
        # window rather than fail.
        # WHICH WINDOW THE EVENING READ — ONE INSTANT, REPORTED ONCE (11c
        # item 2). `since` is what every reader on this fire was handed and
        # `source` is what that window IS; `anchor_ts` is the morning receipt
        # the day floor was compared against, kept so a reader can still see
        # whether a brief fired at all. The per-reader fields stay because
        # receipts already on disk carry them and a reader joining on them
        # must keep parsing — they now all say the same thing, which is the
        # point, and `run_eod2_test` pins that they do.
        "window": {
            "anchor_ts": anchor_ts,
            "since": since_ts,
            "source": window_source,
            "wins": wins["window_source"],
            "closures": closures.window_source,
            "closures_since": closures.since,
            "machine_acts": machine_window_source,
            "machine_acts_since": machine_window_since,
            "opened": opened_window_source,
        },
    }
    if branch == "monday":
        # Monday only, so this key is ABSENT from most fires' receipts rather
        # than zero — "did not run today" and "took no time" are different
        # claims and a reader must be able to tell them apart.
        with phases.phase(eod.PHASE_WEEK_ROLLUP):
            pack["week_rollup"] = eod.week_rollup(ws, for_date=for_date,
                                                  now_iso=now_iso)
            pack["development_read"] = eod.development_read_slot(ws)

    with phases.phase(eod.PHASE_RENDER) as _p:
        pack["confirm_ids"] = eod.confirm_ids_from_pack(pack)
        # FOLD1A fix round 1 — the evening's ≤2 pre-picked confirms (R-N10-3,
        # M's design rule 2026-09-06: "I don't mind a couple of those
        # questions appearing on end of day"). Drawn from the Staff Meeting's
        # OWN weekly five through `quiet`'s one shared budget — never a
        # second allowance — so a `light` seat that answers two here has
        # three left, and the Staff Meeting's page ceiling now reads that
        # REMAINDER rather than the week's limit (fix round 2, REVIEW_FOLD1A
        # R-1: `quiet.staff_meeting_question_ceiling`, which TTL1's copy of
        # that function yields to at the merge). The Staff Meeting still does
        # not DEBIT the budget — that half is FOLD1B's — so the arithmetic is
        # enforced on one side only, and the honest worst case is 5 + 1, not
        # 2 + 5. Drop-empty:
        # an evening with nothing to ask renders nothing, which is the
        # ordinary evening. THIS is the render, so `apply=True` — the budget
        # is spent exactly when the question is shown, never on a compute.
        # It never raises: `eod_confirm_candidates` degrades to no rows on an
        # unreadable substrate, and the day must still close.
        try:
            import eod_question_budget as _eodq
            _eodq_rows = _eodq.eod_confirm_candidates(ws, now_iso=now_iso,
                                                      apply=True)["rows"]
            # SPEC SURFACES2_11c Lane 3 item 3 — THE SELF-SCORED QUESTION,
            # INSIDE THE TWO AND BEHIND THE CONFIRMS (ruling R-9). The
            # confirms are computed and spent FIRST, and what the coach may
            # ask is whatever the evening's two have left: an evening with
            # two confirms due asks no coaching question at all, which is the
            # whole of the ruling. An observed seat gets no slot, so this call
            # returns nothing and the block is byte-identical to yesterday's.
            _coach_q = _eodq.eod_coach_candidates(
                ws, for_date=for_date, n_confirms=len(_eodq_rows),
                now_iso=now_iso, apply=True)
            pack["eod_coach_questions"] = {
                "slots": _coach_q["slots"], "shape": _coach_q["shape"],
                "n": len(_coach_q["rows"]),
                "behaviour": _coach_q["behaviour"]}
            pack["eod_questions"] = _eodq.render_eod_questions(
                list(_eodq_rows) + list(_coach_q["rows"]))
            # FIX ROUND 2 (REVIEW_FOLD1A R-2) — THE ANSWER PATH. The block is
            # also posted as a two-row tap card, so the pre-picked answer is
            # ONE TAP (DESIGN_RULE §2) rather than a sentence naming a phrase
            # nothing claimed. Through the canonical transport, which runs
            # every gate; drop-empty and never fatal — an evening whose card
            # cannot render still posts the text block, whose own footer names
            # the numbered typed answers and `needs your call`.
            if pack["eod_questions"].get("rows"):
                _q_transport = _eodq.render_question_widget(ws, _eodq_rows)
                # SPEC SURFACEFIX1 5.3 / amendment E-5 — SERIALISE AT THE
                # WRITE. `widget_transport.render_and_persist` returns a real
                # `Path` under `transport["path"]`, and this pack is handed to
                # `json.dumps` twice: once for the audit copy (swallowed —
                # which is why nobody saw it) and once for the CR-EOD-PACK
                # line the orchestrator reads. On 2026-09-13 the second one
                # raised `TypeError: Object of type PosixPath is not JSON
                # serializable` and the fire printed a traceback instead of a
                # day-close. The board branch has always done exactly this
                # `str()` at ITS write; this is the same fix at the other one.
                # `default=str` on both dumps is the belt below; this is the
                # braces, and it is the one that keeps the pack's own shape
                # honest (a string is what every reader of this field wants).
                if isinstance(_q_transport, dict) and "path" in _q_transport:
                    _q_transport = dict(_q_transport)
                    _q_transport["path"] = str(_q_transport["path"])
                pack["eod_questions"]["transport"] = _q_transport
        except Exception:  # noqa: BLE001 — the day closes either way
            pack["eod_questions"] = {"lines": [], "questions": [], "n": 0,
                                     "rows": []}
            pack["eod_coach_questions"] = {"slots": 0, "shape": "observed",
                                           "n": 0, "behaviour": ""}
        # CUT-PLATE — THE SCREEN, composed in code (`end_of_day.SCREEN_ORDER`):
        # the plate's eod cut leads, the synthesis follows, the coach's delta,
        # a STATED tomorrow as fact (never the proposal), the sign-off, and
        # the health lines last. `compose_screen` raises on a retired
        # sentence or an asking line, so the old shape cannot re-enter the
        # pack quietly. The orchestrator prints `pack["screen"]["text"]`.
        pack["screen"] = eod.compose_screen(pack)

        # SPEC EODSYNTH1 R-1 — THE SCORE DOES NOT REACH THE SCREEN. The
        # composers each check their own output; this checks the ASSEMBLY,
        # over exactly the strings this driver declares renderable, because
        # two ungraded halves can be a grade together. It raises rather than
        # warns: a surface that grades the day is the one thing this build
        # exists to remove, and a fence that degrades to a log line is a fence
        # the next refactor deletes.
        rendered_text = "\n".join(
            list(coverage.get("lines") or [])
            + list(catchup.get("lines") or [])
            + list(pack.get("dark_surface_lines") or [])
            # CUT-PLATE — the composed screen is the ASSEMBLY the reader
            # sees; it joins the score fence like every other prose surface.
            + [pack["screen"]["text"]]
            + [syn.synthesis_text(synthesis)]
            # SPEC EODCOACH2 — the coach's own composed text joins the same
            # ASSEMBLY check the synthesis text does: two ungraded halves can
            # still be a grade together, and this is a THIRD prose surface
            # that fence has to see.
            + [l for l in [coach.get("text"), sign_off.get("line"),
                           tomorrow.get("line")] if l])
        syn.assert_no_score(rendered_text, where="end_of_day_render")

        # Leak-scan every text line the pack hands the orchestrator. Loud by
        # design, in the driver, before any of it reaches a chat turn.
        scannable = "\n".join(
            alarm_lines
            # TASKALARM1 — the render-once dead-surface lines are composed
            # prose the fire posts, scanned exactly like alarm_lines.
            + list(pack.get("dark_surface_lines") or [])
            # EODLEDGER1 — the three new text surfaces are scanned like every
            # other one. The coverage strip carries connector reasons that
            # arrived from the orchestrator, which is exactly the shape a leak
            # travels in.
            + list(coverage.get("lines") or [])
            + list(catchup.get("lines") or [])
            + [l for l in [score.get("line"), wins.get("line"),
                           wins.get("more_line"), slipped.get("more_line"),
                           # SPEC OVERDUE1 — the resting line is composed
                           # prose the fire posts, so it is scanned like every
                           # other line the pack hands the orchestrator.
                           slipped.get("resting_line"),
                           slipped.get("soften_line"), confirm.get("more_line"),
                           (ledger or {}).get("line"),
                           (ledger or {}).get("residual_line"),
                           sign_off.get("line"),
                           tomorrow.get("line")] if l]
            + list(score.get("notes") or [])
            # SPEC EODSYNTH1 — the synthesized prose is the LARGEST text
            # surface this fire produces and the newest, so it is scanned like
            # every other one. A composed sentence naming a person is exactly
            # the shape a leak travels in, and it is the shape the row-lists
            # never had.
            + [syn.synthesis_text(synthesis)]
            # SPEC EODCOACH2 — the coach's composed text (patterns, delta,
            # push) is new prose naming rows and people, scanned exactly like
            # the synthesis it sits beside.
            + [coach.get("text") or ""]
            # CUT-PLATE — the composed screen, scanned as one string: every
            # line the fire may post, including the plate's rows.
            + [pack["screen"]["text"]]
        )
        if scannable.strip():
            validate_chat_output(scannable)
            # FIX ROUND 1, REVIEW M-2 — the day close composes text too, and
            # it now runs the catch-up moments before it does. Same fence as
            # the brief's, on the same composed string the leak scan reads.
            assert_no_catchup_line(scannable, where="end-of-day")
        _p.count(out=len(pack["confirm_ids"]))

    # EODPHASE1 — the timings ride the pack so the receipt writer can lift
    # them without a second call. Placed AFTER the render phase closes, so the
    # `render` number includes the leak scan (which is the part that grows with
    # the pack) and excludes this bookkeeping. The audit copy below therefore
    # carries the timings too, which is free and occasionally the only surviving
    # record when a fire dies after the pack and before the receipt.
    timings = phases.snapshot()
    if timings:
        pack["phase_timings"] = timings

    try:
        from atomic_write import atomic_write_text
        out_dir = ws / "_hq" / ".system" / "briefs"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_iso[:19].replace(":", "-")
        atomic_write_text(out_dir / f"end-of-day-pack-{stamp}.json",
                          json.dumps(pack, indent=2, ensure_ascii=False,
                                     default=str))
    except Exception:
        pass  # the pack in hand is what matters; the audit copy is best-effort

    return pack


# ---------------------------------------------------------------------------
# Fire receipts (FB-7 — the receipt writes INSIDE the driver call)
# ---------------------------------------------------------------------------

# surface -> the canonical receipts.py task its fire receipts belong to.
_SURFACE_TASKS = {"commitments": "commitment-triage",
                  "plate": "commitment-triage",       # PLATE1 — My Plate IS the triage widget
                  "plate-page": "commitment-triage",  # R-N10-1 — the same surface's working page
                  "show-parked": "commitment-triage",  # the door to what the page held back
                  "staff-meeting": "staff-meeting",
                  "waiting-on": "waiting-on",   # FB-15 (CTS1 taskId)
                  # SPEC SURFACEFIX1 5.5 — `show scheduling`'s route.
                  # A reading surface off the plate's own SCHEDULE block,
                  # so its fire receipts belong to the same task.
                  "schedule": "commitment-triage",
                  "my-plate": "my-plate"}       # FB-plumbing item 6 (CTS1 Surface 2)

# RV-3 guard: a NON-MANUAL driver re-run this close to an already-written
# non-manual receipt is the same fire re-rendering (the live 2026-07-16
# staff-meeting double-render), not a second run — one receipt, never two.
# Manual fires never dedup: two back-to-back manual sweeps are two real
# runs (F-08).
_REFIRE_RECEIPT_GUARD = _dt.timedelta(minutes=15)


#: WRAPSTAFF1 4.4 — the Staff Meeting's fallback window, in days. Seven, the
#: number the orchestrator's bash snippet already used; named here so the
#: label and the marker can never come from two different places again.
STAFF_MEETING_FALLBACK_DAYS = 7

#: The TWO sentences this surface may say about its window, and there are
#: only two. Never both, never neither (ATTENDED_TEST_v5.31.0 B2.7: the block
#: said "since the last staff meeting" over counts taken from a seven-day
#: window — 17 / 12 / 42 where the ledger held 6 / 2 / 5 since Monday's fire,
#: and 42 is exactly the seven-day figure).
STAFF_MEETING_WINDOW_LABELS = {
    "prior_receipt": "since your last staff meeting on {date}",
    "fallback": "over the last seven days",
}


def staff_meeting_window(workspace_root, now_iso: str | None = None) -> dict:
    """WRAPSTAFF1 4.4 — the window the Staff Meeting reports, and the words
    it says about it, from ONE read.

    Returns `{"since_ts", "label", "source"}`:

      since_ts  the marker every count under the label is taken from — the
                last `staff-meeting` receipt's own timestamp, or
                `STAFF_MEETING_FALLBACK_DAYS` back when there is none.
      label     one of `STAFF_MEETING_WINDOW_LABELS`, already filled in.
      source    `"prior_receipt"` or `"fallback"` — which of the two it is,
                so a caller never has to infer it from the words.

    The computation used to be a bash snippet inside prose
    (`orchestrator-staff-meeting.md`), and the label beside it was an
    unconditional sentence naming the last meeting. Two writers, one claim:
    the label described one window and the numbers came from the other. In
    code there is one.

    Read-only. Never raises into a fire: an unreadable receipt log falls back
    to the seven-day window and says so in `source`.
    """
    from receipts import iter_receipts

    now = _clock_now(workspace_root)
    if now_iso:
        try:
            from event_time import parse_ts
            parsed = parse_ts(now_iso)
            if parsed is not None:
                now = parsed
        except Exception:  # pragma: no cover — a bad instant never blocks a fire
            pass
    prior = None
    try:
        for r in iter_receipts(workspace_root, task_ids=["staff-meeting"]):
            if r.get("dt") is not None and r["dt"] < now:
                prior = r
    except Exception:  # pragma: no cover — the window must survive
        prior = None
    if prior is not None:
        since = prior["dt"]
        try:
            from change_feed import _act_date
            when = _act_date(workspace_root, since.isoformat())
        except Exception:  # pragma: no cover
            when = since.date().isoformat()
        return {"since_ts": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "label": STAFF_MEETING_WINDOW_LABELS["prior_receipt"]
                .format(date=when),
                "source": "prior_receipt"}
    since = now - _dt.timedelta(days=STAFF_MEETING_FALLBACK_DAYS)
    return {"since_ts": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "label": STAFF_MEETING_WINDOW_LABELS["fallback"],
            "source": "fallback"}


def _view_fingerprint(view: dict) -> str:
    """WRAPSTAFF1 4.6 — the identity of ONE rendered page-set.

    Two receipts for one surface inside the re-fire guard are the same fire
    re-rendering exactly when they stood over the same rows in the same
    order. So the fingerprint is the section titles and the wire ids the view
    carries, and nothing else: not the clock, not the header (which since
    FOLD1-B is a constant), not the verbs or the copy on a row, which a
    re-render may legitimately recompose without the page having changed.

    A digest of the ids rather than the ids themselves, because this goes on
    the permanent record and a receipt is not a place to copy a page-set to.

    AN ID-LESS VIEW IS UN-DEDUPABLE, NEVER EQUAL (review F-4, 2026-09-17).
    A row with no `n` used to contribute the empty string, so the fingerprint
    degenerated to "section titles + row count" and two genuinely different
    page-sets of the same size collided — which would dedup a real second
    fire, the exact failure this guard exists to prevent, inverted. Every
    shipped view carries `n` today, so this is latent; it is closed anyway,
    because the next view is the one that will not. When ANY rendered row
    lacks an id this returns `""`, and `_log_fire_receipt` reads an empty
    fingerprint as "cannot tell these apart" and WRITES the receipt.
    """
    import hashlib

    parts: list = []
    for sec in view.get("sections") or []:
        parts.append(str(sec.get("title") or ""))
        for it in sec.get("items") or []:
            key = (it or {}).get("n")
            if key is None or str(key) == "":
                return ""
            parts.append(str(key))
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _log_fire_receipt(workspace_root, surface: str, view: dict,
                      fired_via: str, extra_data: dict | None = None) -> dict:
    """Append the surface's per-fire pack_run receipt via the canonical
    helper (receipts.log_receipt — NEVER hand-rolled JSON). Runs inside
    run_surface's page-1 invocation so the widget render and the receipt
    can never be separated (FB-7: the scheduled staff-meeting fire posted
    its widget, then the turn ended before the prose receipt step).

    `extra_data` (STAFFCUT D2, optional) rides `log_receipt(extra_data=...)`
    untouched — the per-kind counts and digest arithmetic the staff meeting now
    records. ADDITIVE ONLY: `surfaced` keeps its exact meaning (the rows the
    widget showed), the receipt's contract fields are unchanged, and
    `log_receipt` already refuses to let extra keys overwrite them. Every
    existing receipt reader is defensive about unknown keys, and legacy
    receipts that carry none keep parsing byte-identically."""
    from receipts import iter_receipts, log_receipt, normalize_fired_via

    task_id = _SURFACE_TASKS[surface]
    via = normalize_fired_via(fired_via)
    surfaced = sum(len(sec.get("items") or [])
                   for sec in view.get("sections") or [])
    now = _clock_now(workspace_root)
    # ONE ledger read and ONE digest per fire (review F-5): both branches
    # need the recent receipts, and the manual branch used to compute the
    # fingerprint twice — once to compare, once to stamp.
    recent = iter_receipts(workspace_root, task_ids=[task_id],
                           since=now - _REFIRE_RECEIPT_GUARD)
    fp = _view_fingerprint(view)
    if via != "manual":
        if any(r["fired_via"] != "manual" for r in recent):
            return {"task_id": task_id, "fired_via": via,
                    "surfaced": surfaced, "status": "deduped_refire"}
    else:
        # WRAPSTAFF1 4.6 — ONE RECEIPT PER FIRE, INCLUDING A MANUAL ONE.
        #
        # A manual fire is deliberately never deduped on the clock alone
        # (F-08: two back-to-back manual sweeps are two real runs, and the
        # second is often the point). The 09-15 on-demand Staff Meeting
        # nevertheless wrote two identical receipts, 17609 and 17610
        # (ATTENDED_TEST_v5.31.0 B2.7), because ONE fire re-rendered.
        #
        # So the dedup keys on the VIEW'S IDENTITY rather than on the clock:
        # a second receipt for the same surface over the same page-set inside
        # the guard is that fire rendering twice, and writes nothing. Two
        # manual sweeps over a book that CHANGED have different fingerprints
        # and still write two receipts — F-08 survives, by construction.
        # An empty fingerprint means the page-set carries a row with no id
        # and cannot be told from another (F-4): the dedup is OFF and the
        # receipt is written, which is the safe direction.
        for r in recent if fp else ():
            if r["fired_via"] != "manual":
                continue
            if str((r["raw"].get("data") or {}).get("view_fingerprint") or "") == fp:
                return {"task_id": task_id, "fired_via": via,
                        "surfaced": surfaced, "status": "deduped_rerender"}
    extra_data = dict(extra_data or {})
    if fp:
        extra_data["view_fingerprint"] = fp
    log_receipt(workspace_root, task_id, fired_via=via, surfaced=surfaced,
                extra_data=extra_data or None)
    out = {"task_id": task_id, "fired_via": via, "surfaced": surfaced,
           "status": "written"}
    if extra_data:
        out["extra_data"] = dict(extra_data)
    return out


_SURFACE_NAME_HINTS = {"commitments": "commitment-triage",
                       "plate": "commitment-triage",
                       "plate-page": "commitment-triage",
                       "show-parked": "commitment-triage",
                       "staff-meeting": "staff-meeting",
                       "waiting-on": "waiting-on",
                       "schedule": "commitment-triage",
                       "my-plate": "my-plate"}

#: R-N10-1 — surfaces that render ONCE and name a door for the rest, instead
#: of paging. The board fits itself to the widget's size budget inside its own
#: build, so `run_surface` hands it to the transport unpaged: a position line
#: ("page 1 of 4") on a surface that has no page 2 is a lie the reader has to
#: work out.
_UNPAGED_SURFACES = frozenset({"plate"})


def surface_for_reply(text):
    """THE ROUTE for an in-chat reply on an open plate (REVIEW_ONEPLATE1
    F-3; the scope round's open item 1).

    `work my plate` -> the nine-row working page; `show parked` -> exactly
    what that page held back. Both used to be answered by a paragraph of
    the skill, and one of those paragraphs named a mechanism
    (`preset="engaged"`) that rebuilt a different plate. A phrase that
    opens a surface belongs in a table the driver reads, so the words and
    the surface cannot drift apart.

    Returns the surface name, or None when the reply is not a door.
    """
    from plate_view import reply_surface
    return reply_surface(text)


def run_watch_expiry_pass(workspace_root, *, now_iso=None) -> dict:
    """WATCHGATE MUST-FIX-2 — the scheduled driver for stakes-routed expiry.

    §2.3 makes expiry the TERMINAL behavior of every parked item, §2.4's whole
    table is "unproven after window", and §2.7's cap is defined "per staff-
    meeting fire" — a phrase that names an orchestrator, not a pure function.
    Built but undriven, WATCHING was a one-way door: items parked and were
    never routed, never assumed, never asked about. §0 forbids exactly that
    ("never silently stops watching"), so this runs on the staff-meeting fire
    every workspace already has — no registration, no setup, no connector.

    Defensive by construction: ANY failure returns an empty result and the
    fire proceeds. An expiry pass that could take the staff meeting down with
    it would be a worse bug than the one it fixes.

    WATCHGATE N-5: "defensive" used to mean "invisible". A skipped pass wrote
    one line to STDERR — which nothing on a scheduled fire reads — and returned
    a zeroed shape indistinguishable from a healthy quiet day, so nobody ever
    learned that WATCHING had stopped routing. Per-item failures are contained
    inside `run_watch_expiry` and counted there; both counts ride the fire's
    RECEIPT (`_log_fire_receipt`'s `extra_data`), which is a record someone can
    actually read later. Additive: on a healthy pass neither key is written and
    the receipt is byte-identical to a pre-N-5 one.

    Returns run_watch_expiry's result, or a zeroed shape marked `pass_skipped`.
    """
    empty = {"assumed": [], "ask": [], "carried": [], "not_due": [],
             "results": [], "n_assumed": 0, "n_ask": 0, "n_carried": 0,
             "n_failed": 0, "failures": [], "pass_skipped": False}
    try:
        from primary_user import resolve_primary_user
        from watch_gate import run_watch_expiry

        return run_watch_expiry(
            workspace_root,
            resolved_by=resolve_primary_user(workspace_root) or "",
            source_skill="staff-meeting",
            now_iso=now_iso,
        )
    except Exception as exc:  # pragma: no cover — the fire must survive
        sys.stderr.write(f"[surface_drivers] watch expiry pass skipped: "
                         f"{exc}\n")
        return dict(empty, pass_skipped=True)


def watch_expiry_receipt_extra(pass_result) -> dict:
    """The expiry pass's health, in receipt keys — {} when it was healthy.

    Split out so the ONE place that builds it is testable without a render, and
    so a second caller cannot invent its own spelling of the same fact.
    """
    out: dict = {}
    if not isinstance(pass_result, dict):
        return out
    n_failed = int(pass_result.get("n_failed") or 0)
    if n_failed:
        out["watch_expiry_failed"] = n_failed
    if pass_result.get("pass_skipped"):
        out["watch_expiry_skipped"] = True
    return out


def _build_surface_view(surface: str, ws, *, now_iso, moves_rows,
                        chase_rows, status_rows, personal_cap,
                        promised_cap=_MP_PROMISED_CAP,
                        watch_rows=None) -> dict:
    """Build ONE surface's data view from LIVE substrate. The only place that
    reads; `run_surface` decides WHEN it is allowed to be called."""
    if surface == "commitments":
        return build_commitment_triage_view(ws, now_iso=now_iso)
    if surface == "plate":
        # R-N10-1 (M, 2026-09-07) — THE DEFAULT PLATE RENDER IS THE BOARD:
        # grouped by project (or by person where this workspace's brief is
        # organised that way), one line per row, one tap, as many rows as
        # the widget's size budget holds. The nine-row page with the full
        # button set did not go away — it is `plate-page` below, behind
        # `work my plate`.
        from plate_view import plate_board_view
        return plate_board_view(ws, now_iso=now_iso)
    if surface == "plate-page":
        # PLATE1 — the triage widget renders THE plate (action block ->
        # project -> horizon, four verbs) through the one grouping +
        # renderer. `build_commitment_triage_view` stays only as the
        # BOARD1 artifact's data source until night 2 adopts the board.
        # R-N10-1 moved this off the default and behind `work my plate`;
        # nothing about the page itself changed.
        from plate_view import plate_data_view
        return plate_data_view(ws, now_iso=now_iso)
    if surface == "show-parked":
        # REVIEW_ONEPLATE1 F-3 — `show parked` is a ROUTE, not a paragraph.
        # It renders exactly the rows the working page held back, off the
        # same build of the same plate, with the same row numbers. The
        # prose it replaces told the reader to rebuild the plate at
        # `engaged`, which is a different plate with different counts and
        # no cap at all.
        from plate_view import parked_data_view
        return parked_data_view(ws, now_iso=now_iso)
    if surface == "staff-meeting":
        return build_staff_meeting_view(ws, now_iso=now_iso,
                                        moves_rows=moves_rows,
                                        watch_rows=watch_rows)
    if surface == "waiting-on":
        return build_waiting_on_view(ws, now_iso=now_iso,
                                     chase_rows=chase_rows)
    if surface == "schedule":
        # SPEC SURFACEFIX1 5.5 — `show scheduling`'s route. Gated like every
        # other surface here: it goes out through `run_surface`, so the leak
        # gate and the transport see it.
        return build_schedule_view(ws, now_iso=now_iso)
    if surface == "my-plate":
        return build_my_plate_view(ws, now_iso=now_iso,
                                   status_rows=status_rows,
                                   personal_cap=personal_cap,
                                   promised_cap=promised_cap)
    raise SystemExit(
        f"unknown surface {surface!r} "
        "(supported: commitments, plate, staff-meeting, waiting-on, "
        "schedule, my-plate)")


def run_board(workspace_root, *, now_iso: str | None = None,
              persist_dir=None) -> dict:
    """SPEC_BOARD1 — the commitments surface, serialized as an artifact board.

    This is the ARTIFACT BRANCH of the commitments pipeline, not a second
    pipeline. Read the order and note what is shared:

      0. `refuse_if_mount_stale` — SPEC SYNC1 A4's mount-freshness preflight,
         run BEFORE the read. ok=false → refuse in the existing syncing
         vocabulary, producing no page, no receipt, no page-set and no
         events. (The preflight's own alarm sidecar and alert artifact are
         written on that path by design — see `MountStaleError`.)
      1. `build_commitment_triage_view` — THE view. Identical call, identical
         helpers, identical rows to the widget path (§3: one derivation; the
         artifact mode differs only at the serialization step).
      2. `chat_output_renderer.validate_data_view` — the pre-render gate
         family the widget path runs (canonical actions, data shape, pulse
         richness, send-class emails, the voice-tell backstop). CALLED, not
         re-typed.
      3. `artifact_board.render_board_html` — the serialization. The ONLY
         step that differs from the widget path.
      4. `chat_output_renderer.scan_rendered_html` — the same leak scan, with
         the same style/script/href/data-* preparation the widget path uses.
      5. `artifact_board.validate_board_html` — the board-shaped structural
         contract (the `validate_rendered_widget` analog: that validator
         asserts the widget DOM, which this page is not).
      6. persist to `_hq/.system/widgets/` — the same audit trail the
         persisted widget pages already write to.

    NO PAGINATION (§3 full-set single page): the board is one document, so
    there is no page-set to freeze and nothing is dropped — a single render is
    its own snapshot, which is why PAGESNAP does not apply here. NO RECEIPT:
    a receipt records a FIRE of a scheduled surface; publishing a board is not
    one, and writing one would corrupt the triage fire history the load audits
    read.

    Returns {"html", "path", "file_uri", "board": {...}} — no `pagination`
    key, deliberately, so no caller can mistake this for the widget transport.
    """
    from artifact_board import (generated_at_line, lane_counts,
                                render_board_html, validate_board_html)
    from chat_output_renderer import scan_rendered_html, validate_data_view

    ws = Path(workspace_root)
    # 0. MOUNT-FRESHNESS PREFLIGHT — before the read, not after it. A board
    #    built from a stale mount either dies in a downstream validator naming
    #    the wrong cause or, worse, publishes stale rows and stale counts with
    #    nothing on the page saying so.
    refuse_if_mount_stale(ws)
    now_iso = now_iso or _now_iso()
    view = build_commitment_triage_view(ws, now_iso=now_iso)
    validate_data_view(view)
    html = render_board_html(
        view, generated_at=now_iso,
        stamp_line=generated_at_line(now_iso, ws))
    scan_rendered_html(html)
    counts = lane_counts(view)
    # Conservation: the page must carry exactly the rows THE VIEW handed over.
    #
    # Derived from the view's own sections, NOT from `lane_counts` (review
    # F-2). `lane_counts` calls the same `partition_board` the page is
    # serialized from, so a partitioner that dropped a row lowered the
    # expectation by exactly the rows it dropped and the gate agreed with
    # itself — measured at 8 rows in, 6 rendered, `expect_rows` 6, no raise.
    # That is the self-referential-assertion gotcha in its runtime form: the
    # suite caught the mutation, the gate that ships to real data did not.
    # The view's `sections` are the one count upstream of the partition.
    # (Sub-items ride nested inside their parent row, so a section's `items`
    # is exactly the top-level population `validate_board_html` counts.)
    #
    # An empty board is a legitimate answer; a board one row short is not.
    expect_rows = sum(len(s.get("items") or [])
                      for s in (view.get("sections") or []))
    validate_board_html(html, expect_rows=expect_rows)

    persist_dir = Path(persist_dir) if persist_dir is not None \
        else ws / "_hq" / ".system" / "widgets"
    persist_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_iso[:19].replace(":", "-")
    out_path = persist_dir / f"commitment-board_{stamp}.html"
    from atomic_write import atomic_write_text
    atomic_write_text(out_path, html)
    file_uri = "file:///" + str(out_path.resolve()).replace("\\", "/").lstrip("/")

    return {
        "html": html,
        "path": out_path,
        "file_uri": file_uri,
        "board": {
            "generated_at": now_iso,
            "generated_at_local": generated_at_line(now_iso, ws),
            "lane_counts": counts,
            "headline": {c["label"]: c["value"]
                         for c in (view.get("counters") or [])},
            "rows": sum(counts.values()),
        },
    }


def run_surface(surface: str, workspace_root, *, page: int = 1,
                page_size: int | None = None, now_iso: str | None = None,
                moves_rows: list | None = None,
                chase_rows: list | None = None,
                status_rows: list | None = None,
                personal_cap: int = _MP_PERSONAL_CAP,
                promised_cap: int = _MP_PROMISED_CAP,
                fired_via: str | None = None,
                rerun_of: str | None = None,
                pageset_ttl_minutes: int | None = None,
                extra_receipt_data: dict | None = None) -> dict:
    """Build the view + render_and_persist ONE page. Returns the transport
    dict (html / pagination / path). The CLI wraps this; tests call it
    directly.

    PAGESNAP — pages 2+ slice a SNAPSHOT, never a fresh read.

    This function used to rebuild the view from live substrate on EVERY call
    and hand `page` to a slicer that indexed the result. Page 2 was therefore
    a different query result than page 1, indexed against page 1's ordering:
    a write landing between the renders shifted every row after it, so the
    tail of page 1 reappeared atop page 2 (insert) or the rows that should
    have opened page 2 appeared on NO page at all (delete — silent, and the
    one that loses user-visible work). Observed live 2026-07-28.

    Now: page 1 builds live and FREEZES the view as the fire's page-set
    (`page_snapshot`); pages 2+ load that frozen view and slice the same list.
    Nothing is re-read between pages, so nothing shifts, and the reported
    total holds steady across the fire.

    A page-set older than `pageset_ttl_minutes` (default
    page_snapshot.DEFAULT_TTL_MINUTES), or missing/corrupt, is NOT silently
    re-read — the rebuild is announced on `transport["pagination"]` via
    `refreshed` / `refresh_reason` / `previous_total` so the surface can say
    the list changed under it. Rows applied since the snapshot are suppressed
    from later pages (`suppressed`), so an Apply on page 1 is reflected on
    page 2 without moving anything else.

    `fired_via` (scheduled | manual | catchup — the orchestrator's detected
    run mode, Phase 2.9 `receipt_fired_via`) makes the PAGE-1 invocation
    also write the surface's canonical per-fire receipt inside this same
    call (see _log_fire_receipt; the written/deduped outcome rides back on
    transport["receipt"]). Pages 2+ never receipt; omitting fired_via
    renders only (legacy callers unchanged). The snapshot path does NOT touch
    receipt behavior: the receipt still fires on page 1 only, still counts the
    live-built view it was always counting.

    `rerun_of` (SPEC RERUNFAN1, the ISO instant `late_fire.check_lateness`
    returns on a `rerun` tier) rides onto that receipt's `extra_data` under
    `late_fire.RERUN_OF_FIELD`. It is PASSED, never derived: this module has
    no opinion about run modes, and RUNNOW1 DD-3 deliberately left the field a
    prose contract rather than a code chokepoint. It exists here because three
    orchestrators — waiting-on, my-plate and staff-meeting — write their
    receipt INSIDE this call on every normal fire, and staff-meeting has no
    other receipt call at all (only a degrade branch a `rerun` tier never
    reaches); without it their re-run paragraph would be a reader with no
    producer on the path they actually take (RUNNOW1 F-1). A receipt carrying
    the field is excluded from the
    served-slot marker, which is what lets the next press render (DD-5).
    Ignored when no receipt is written (pages 2+, or no `fired_via`).

    `extra_receipt_data` (S-13, PARALLEL-B lane A, 2026-09-24): ADDITIVE keys
    a driver hands in for the receipt's `extra_data` — the delivery flags
    (`receipt_flags`: widget_posted / text_fallback) a run with no widget
    tool records. When given, the receipt ALSO carries `page_sha256` and
    `page_bytes` of the page AS LANDED (read back from disk after the persist,
    before the receipt), which is what makes "the receipt is written after
    the page" observable; and the built view rides back on
    `transport["view"]` so the driver can compose the text form from the SAME
    rows the widget shows. None (every existing caller) changes nothing:
    receipt, transport and page-set are byte-identical to before."""
    from page_snapshot import (DEFAULT_TTL_MINUTES, applied_ids_since,
                               load_pageset, save_pageset)
    from widget_transport import render_and_persist

    if fired_via is not None:
        from receipts import FIRED_VIA, normalize_fired_via
        if normalize_fired_via(fired_via) not in FIRED_VIA:
            raise ValueError(
                f"fired_via must be one of {sorted(FIRED_VIA)}; "
                f"got {fired_via!r}")

    ws = Path(workspace_root)
    if surface not in _SURFACE_NAME_HINTS:
        raise SystemExit(
            f"unknown surface {surface!r} "
            "(supported: commitments, plate, staff-meeting, waiting-on, "
        "schedule, my-plate)")
    # MOUNT-FRESHNESS PREFLIGHT — the widget path's half of the same gate
    # `run_board` runs. This entry point is write-chained (a page-1 fire with
    # `fired_via` appends the surface's receipt), so a stale view here is the
    # stale-READ-becomes-clobbering-WRITE class, not only a stale render.
    refuse_if_mount_stale(ws)
    name_hint = _SURFACE_NAME_HINTS[surface]
    page = 1 if page is None else int(page)
    ttl = (DEFAULT_TTL_MINUTES if pageset_ttl_minutes is None
           else int(pageset_ttl_minutes))

    view = None
    suppress_ids: set = set()
    snap_note: dict = {}

    if page > 1 and surface not in _UNPAGED_SURFACES:
        view, meta = load_pageset(ws, surface, ttl_minutes=ttl,
                                  now_iso=now_iso)
        if view is not None:
            # Rows the user applied since the snapshot froze. Derived from the
            # substrate's own audit events, so no caller has to remember to
            # register anything.
            suppress_ids = applied_ids_since(ws, meta.get("created_at"))
            snap_note = {"from_snapshot": True,
                         "snapshot_at": meta.get("created_at")}
        else:
            # NOT a silent re-read. Rebuild, start a fresh page-set so the
            # rest of this sequence is stable again, and SAY it refreshed.
            snap_note = {"refreshed": True,
                         "refresh_reason": meta.get("reason")}
            if meta.get("previous_total") is not None:
                snap_note["previous_total"] = meta["previous_total"]

    if view is None:
        watch_rows = None
        watch_pass = None
        if surface == "staff-meeting" and page == 1 and now_iso is None:
            # WATCHGATE MUST-FIX-2. Three conditions, and each one is load-
            # bearing:
            #
            #   PAGE 1 — pages 2+ read a frozen page-set, and running a WRITE
            #   pass to serve a paging request would close items the user is
            #   only scrolling past.
            #
            #   view is None — only when a view is actually being built, not
            #   on a snapshot read.
            #
            #   now_iso is None — A SIMULATED CLOCK MAY READ, IT MAY NEVER
            #   WRITE. This is the only write ever wired into `run_surface`,
            #   which was all reads before it, and it is the only clock in
            #   this call that does not float on wall time. Without this
            #   condition a render at `--now +30d` permanently closes parked
            #   items whose windows are weeks from expiring and records them
            #   as assumed-done — measured, not theorised (re-verify N-1).
            #   `--now` is a shipped CLI flag and this product is full of
            #   catch-up and backfill flows whose whole idiom is a simulated
            #   clock; nothing passes one HERE today, which is exactly why
            #   the guard belongs in code rather than in a comment telling
            #   the next person not to.
            watch_pass = run_watch_expiry_pass(ws)
            watch_rows = watch_pass.get("ask")
        view = _build_surface_view(
            surface, ws, now_iso=now_iso, moves_rows=moves_rows,
            chase_rows=chase_rows, status_rows=status_rows,
            personal_cap=personal_cap, promised_cap=promised_cap,
            watch_rows=watch_rows)
        live_view = view
        # STAFFCUT D2 — the builder's receipt arithmetic travels on the view
        # and is POPPED here, before `save_pageset` freezes it and before the
        # renderer sees it: the widget data contract gains nothing, the frozen
        # page-set stays byte-comparable to a pre-STAFFCUT one, and the receipt
        # still counts the live-built view it was always counting.
        receipt_extra = view.pop("receipt_extra", None) \
            if isinstance(view, dict) else None
        # N-5 — the expiry pass's own health, folded in. Nothing is added on a
        # healthy pass, so the receipt is unchanged when there is nothing to say.
        _watch_extra = watch_expiry_receipt_extra(watch_pass)
        if _watch_extra:
            receipt_extra = dict(receipt_extra or {})
            receipt_extra.update(_watch_extra)
        # Freeze this build as the page-set — on page 1 because that is the
        # fire's anchor, and on a refreshed page N so pages N+1... are stable
        # against the same list rather than drifting again.
        saved = save_pageset(ws, surface, view, now_iso=now_iso)
        if not saved.get("saved"):
            # A page-set we could not write is a page-set page 2 will miss and
            # announce. Never fatal: losing the snapshot must not cost the
            # user their page.
            snap_note["snapshot_unavailable"] = True
    else:
        live_view = None
        receipt_extra = None

    transport = render_and_persist(
        data_view=view,
        wrapper="fragment",
        persist_dir=ws / "_hq" / ".system" / "widgets",
        name_hint=name_hint,
        # R-N10-1 — the board renders once; it already fit itself to the
        # widget's size budget and named the doors to what did not fit.
        page=None if surface in _UNPAGED_SURFACES else page,
        page_size=page_size,
        suppress_ids=suppress_ids or None,
    )
    if snap_note and transport.get("pagination") is not None:
        transport["pagination"].update(snap_note)
    # HEAL1 behaviour 4 — THE READ SURFACES SAY ONE SENTENCE AND RUN NOTHING.
    #
    # `staff meeting` and `what's on my plate` are glances at the reader's own
    # rows. A multi-minute upkeep pass in front of a glance is not a win, so
    # these two never catch up (`maintenance_dispatcher.CATCH_UP_READ_SURFACES`
    # is where that is written down, and `catch_up_plan` refuses them by name).
    # What they DO carry is the one honest sentence, when there is one.
    #
    # It rides the TRANSPORT, not the view: the view is frozen as the page-set
    # and handed to the renderer, and a sentence about the plumbing is neither
    # a row nor part of a page's identity. Absent entirely when there is
    # nothing to say, so no fire gains a key it has no opinion about.
    if surface in _READ_SURFACE_TRUTH_LINE:
        try:
            _truth = maintenance_truth_line(ws, now_iso=now_iso)
        except Exception:  # noqa: BLE001 — a sentence never costs a surface
            _truth = ""
        if _truth:
            transport["maintenance_line"] = _truth
    # FOLD1-B 1.2 item 4(a) — THE ASK-ONCE MARKER, WRITTEN AFTER THE POST.
    #
    # Second half of the same scope exception the staff-meeting view builder
    # carries (see the block there): the rehomed overdue question is asked on
    # this surface now, and the rest-until-answered fold keys on the marker
    # whichever surface asks writes. AFTER `render_and_persist`, never
    # before — the mark means the customer has been asked, and marking a row
    # before the question reaches the screen rests a row nobody saw. Only
    # page 1, because that is the fire that composed the section.
    if surface == "staff-meeting" and page == 1 and isinstance(view, dict):
        try:
            from needs_review_queue import (OVERDUE_SECTION_TITLE,
                                            mark_overdue_asked)
            for _sec in view.get("sections") or []:
                # STARTSWITH, not equality: the page bound appends its own
                # honest "showing N of M" clause to a section title it
                # trimmed, and the marker must follow the section wherever
                # the bound leaves it — and must mark only the rows that
                # survived the trim, which is what the section now carries.
                if str((_sec or {}).get("title") or "").startswith(
                        OVERDUE_SECTION_TITLE):
                    mark_overdue_asked(ws, _sec, now_iso=now_iso)
                    break
        except Exception as exc:  # pragma: no cover — never cost the post
            sys.stderr.write(f"[surface_drivers] overdue marker skipped: "
                             f"{exc}\n")
    if extra_receipt_data is not None:
        # S-13: the driver asked for the text form's facts. The view it was
        # built from, and the page's landed bytes — read back AFTER the
        # persist and BEFORE the receipt, so a receipt that names the page's
        # sha is a receipt written after the page.
        transport["view"] = view
        landed_sha = None
        landed_bytes = None
        try:
            import hashlib as _hashlib
            _landed = Path(str(transport.get("path") or "")).read_bytes()
            landed_sha = _hashlib.sha256(_landed).hexdigest()
            landed_bytes = len(_landed)
        except (OSError, ValueError):
            pass
        receipt_extra = dict(receipt_extra or {})
        receipt_extra.update({k: v for k, v in extra_receipt_data.items()
                              if v is not None})
        receipt_extra["page_sha256"] = landed_sha
        receipt_extra["page_bytes"] = landed_bytes
    if fired_via is not None and page == 1:
        # SPEC RERUNFAN1 — merged HERE rather than inside the page-1 view
        # build so it cannot depend on which path produced the view, and
        # under the field name `late_fire` itself uses: a second spelling of
        # a key one reader keys on is the F-50 P2c drift class, and this key
        # is the one the served-slot exclusion reads.
        if rerun_of:
            from late_fire import RERUN_OF_FIELD
            receipt_extra = dict(receipt_extra or {})
            receipt_extra[RERUN_OF_FIELD] = rerun_of
        transport["receipt"] = _log_fire_receipt(
            ws, surface, live_view if live_view is not None else view,
            fired_via, extra_data=receipt_extra)
    return transport


def main() -> int:
    # Review F-5: Windows pipes default to cp1252 — the output carries
    # non-ASCII (middots, warning glyphs) and would crash the CLI.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("surface",
                    choices=["commitments", "plate", "plate-page",
                             "show-parked", "staff-meeting", "waiting-on",
                             "schedule",
                             "my-plate", "morning-brief", "end-of-day"],
                    help="`plate` is the default plate render — the BOARD, "
                         "one look, one line a row (R-N10-1). `plate-page` "
                         "is the nine-row page with the full button set, "
                         "behind the reply `work my plate`. `show-parked` "
                         "is the door to exactly what that page held back")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--mode", default="scheduled",
                    choices=["scheduled", "manual"],
                    help="morning-brief only: scheduled renders the confirm "
                         "card as a widget page; manual renders markdown "
                         "lines (t3 FB-9)")
    ap.add_argument("--format", dest="fmt", default="widget",
                    choices=["widget", "artifact"],
                    help="commitments only: `widget` (default, unchanged) "
                         "relays one paginated page to show_widget; "
                         "`artifact` serializes the SAME view as the "
                         "full-set, self-contained triage board for the "
                         "Artifact tool (SPEC_BOARD1). Never both in one "
                         "call — the board is a different question, not a "
                         "different page")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--page-size", type=int, default=None,
                    help="requested rows/page ceiling (the byte-fit may lower "
                         "it); default = chat_output_renderer.DEFAULT_PAGE_SIZE")
    ap.add_argument("--pageset-ttl-minutes", type=int, default=None,
                    help="how long this fire's page-set stays authoritative "
                         "before `show more` rebuilds and SAYS it refreshed "
                         "(default page_snapshot.DEFAULT_TTL_MINUTES)")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--page-byte-budget", type=int, default=None,
                    help="tests only: override widget_transport."
                         "WIDGET_PAGE_BYTE_BUDGET so the over-budget text "
                         "form (CR-WIDGET-TEXT-*) can be exercised")
    ap.add_argument("--close-json", default=None,
                    help="end-of-day only: JSON file with the close phase's "
                         "own record ({\"mail\": <receipt>, \"chat\": "
                         "<receipt>}) — the soften floor reads whether either "
                         "leg advanced its cursor")
    ap.add_argument("--calendar-json", default=None,
                    help="end-of-day only: JSON file with the wide "
                         "now->+3d calendar fetch; omit it and the tomorrow "
                         "block renders its intent half and the fire "
                         "receipts the missing leg")
    ap.add_argument("--lateness-json", default=None,
                    help="end-of-day only: JSON file with the Phase 2.9 "
                         "`check_lateness` return, VERBATIM. On the degrade "
                         "tier the pack composes the catch-up label the "
                         "surface opens with (SPEC EODLEDGER1); on every "
                         "other tier the block is present and renders "
                         "nothing. Never edited on the way in — the fire "
                         "passes what the helper returned")
    ap.add_argument("--gaps-json", default=None,
                    help="end-of-day only: JSON file with the fire's "
                         "`connector_gaps` — the plain-English reason each "
                         "absent capability was skipped. The coverage strip "
                         "renders these; without them a skipped leg reads as "
                         "'nothing on the record says why'")
    ap.add_argument("--moves-json", default=None,
                    help="staff-meeting only: JSON file with the Phase-4 "
                         "moves rows (email-shaped item dicts)")
    ap.add_argument("--chase-json", default=None,
                    help="waiting-on only: JSON file with the pre-staged chase "
                         "rows (email-shaped item dicts, connector-dependent)")
    ap.add_argument("--status-json", default=None,
                    help="my-plate only: JSON file with the pre-staged Promised "
                         "status-draft rows (email-shaped item dicts, "
                         "connector-dependent — the chase_rows parallel)")
    ap.add_argument("--personal-cap", type=int, default=_MP_PERSONAL_CAP,
                    help="my-plate only: Personal-group row cap (CTS1 §4.2 "
                         "`personal_cap`, default 7); 'show my plate' passes a "
                         "large value to lift the cap")
    ap.add_argument("--promised-cap", type=int, default=_MP_PROMISED_CAP,
                    help="my-plate only: Promised-group row cap (BUG-8330 "
                         "item 5); the footer names what's held back")
    ap.add_argument("--triggered-by", default=None,
                    help="the surface that asked for this run")
    ap.add_argument("--fired-via", default=None,
                    choices=["scheduled", "manual", "catchup"],
                    help="the fire's run mode (the orchestrator's Phase-2.9 "
                         "receipt_fired_via); when given, the page-1 "
                         "invocation also writes the surface's canonical "
                         "per-fire receipt inside this call (FB-7)")
    ap.add_argument("--rerun-of", default=None,
                    help="SPEC RERUNFAN1: the ISO instant check_lateness "
                         "returns as `rerun_of` on a `rerun` tier. Rides onto "
                         "the page-1 receipt's extra_data and is what lets "
                         "the NEXT press render; OMIT the flag on every "
                         "other tier")
    args = ap.parse_args()
    # FIX3 F3-6: export what this run was asked by, so every composer
    # below reads it from one place instead of being threaded through
    # a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)

    try:
        return _dispatch(args)
    except MountStaleError as stale:
        # Exit nonzero having written nothing. The message is the existing
        # syncing vocabulary (substrate_alarm_lines); the durable alert was
        # already rendered through alarm_artifacts by the preflight itself.
        for line in (stale.lines or [str(stale)]):
            print(line, file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    if args.surface == "morning-brief":
        # t3 FB-9 — ONE call, every mandatory block. The orchestrator places
        # each emitted block; the CR-BRIEF-PACK line is the checklist.
        # FB-20: this surface emits PROSE ONLY — no widget block, no relay
        # banner, nothing to post to show_widget. The brief is read-only by
        # construction. (The banner + CR-WIDGET-HTML markers below this
        # branch still serve commitments / staff-meeting unchanged.)
        # SPEC SURFACEFIX1 5.3 / FIX ROUND 1 (reviewer F-2) — THE BRIEF FAILS
        # IN ONE SENTENCE TOO. This lane put three RAISING fences inside
        # `build_morning_brief_pack` (`assert_single_open_count`,
        # `assert_brief_never_asks`, `assert_no_reachability_line`) and left
        # this branch unwrapped, so a tripped fence printed a full traceback
        # at the reader — file names, function names, the exception class and
        # the offending customer sentence quoted back verbatim. That is leak
        # 10's class, newly opened on the morning by the lane that closes it
        # on the evening. Same three lines as the End of Day branch below,
        # same constants, same receipt.
        try:
            pack = build_morning_brief_pack(args.workspace, mode=args.mode,
                                            now_iso=args.now)
            line = "CR-BRIEF-PACK: " + json.dumps(pack, ensure_ascii=False,
                                                  default=str)
        except Exception as exc:  # noqa: BLE001 — one sentence, never a trace
            log_surface_failed(args.workspace, SURFACE_FAILED_BRIEF, exc,
                               mode=args.mode, now_iso=args.now)
            print(SURFACE_FAILED_LINES[SURFACE_FAILED_BRIEF])
            return 1
        print(line)
        return 0

    if args.surface == "end-of-day":
        # SPEC EOD1 — the evening bookend. ONE line out, the same contract the
        # brief pack keeps: every non-empty block is a mandatory placement.
        close_result = None
        if args.close_json:
            close_result = json.loads(
                Path(args.close_json).read_text(encoding="utf-8"))
        calendar_events, calendar_available = None, False
        if args.calendar_json:
            calendar_events = json.loads(
                Path(args.calendar_json).read_text(encoding="utf-8"))
            calendar_available = True
        lateness = None
        if args.lateness_json:
            lateness = json.loads(
                Path(args.lateness_json).read_text(encoding="utf-8"))
        connector_gaps = None
        if args.gaps_json:
            connector_gaps = json.loads(
                Path(args.gaps_json).read_text(encoding="utf-8"))
        # SPEC SURFACEFIX1 5.3 / amendment E-5 (Part E 09-13, leak 10) — THE
        # FIRE FAILS IN ONE SENTENCE. On 2026-09-13 this call raised
        # `TypeError: Object of type PosixPath is not JSON serializable` and
        # the fire printed the TRACEBACK: `surface_drivers.py`, the pack's own
        # key path, a module name and two Windows paths, all on the customer's
        # screen (leak 10) — and then the chat HAND-BUILT the day-close from
        # the ledger, which is the canonical-path-fails / freelance-substitutes
        # class this build exists to close.
        #
        # Both halves are fixed here. The serialisation is fixed at the write
        # (`str()` on the transport path above, `default=str` on both dumps).
        # This is the floor under that: ANY exception out of the builder or
        # the dump renders ONE plain sentence and writes a `surface_failed`
        # receipt, and nothing else reaches the screen — no path, no module,
        # no class name, no traceback. The orchestrator prose says the rest:
        # a fire that printed this sentence is DONE, and hand-building the
        # pack is forbidden.
        try:
            pack = build_end_of_day_pack(
                args.workspace, mode=args.mode, now_iso=args.now,
                close_result=close_result, calendar_events=calendar_events,
                calendar_available=calendar_available,
                connector_gaps=connector_gaps, lateness=lateness)
            line = "CR-EOD-PACK: " + json.dumps(pack, ensure_ascii=False,
                                                default=str)
        except Exception as exc:  # noqa: BLE001 — one sentence, never a trace
            log_surface_failed(args.workspace, SURFACE_FAILED_EOD, exc,
                               mode=args.mode, now_iso=args.now)
            print(SURFACE_FAILED_LINES[SURFACE_FAILED_EOD])
            return 1
        print(line)
        return 0

    if args.fmt == "artifact":
        # SPEC_BOARD1 — the board. Its own markers on purpose: the widget
        # contract says "relay the bytes between CR-WIDGET-HTML-* to
        # show_widget byte-exact", and these bytes go to the Artifact tool
        # instead. Reusing those markers would invite exactly the wrong relay.
        if args.surface != "commitments":
            raise SystemExit(
                "--format artifact is the commitments surface only "
                f"(got {args.surface!r})")
        result = run_board(args.workspace, now_iso=args.now)
        meta = dict(result["board"])
        meta["path"] = str(result["path"])
        meta["file_uri"] = result["file_uri"]
        print("CR-BOARD: " + json.dumps(meta, ensure_ascii=False))
        print("CR-BOARD-HTML-BEGIN")
        print(result["html"])
        print("CR-BOARD-HTML-END")
        return 0

    moves_rows = None
    if args.moves_json:
        moves_rows = json.loads(Path(args.moves_json).read_text(encoding="utf-8"))
    chase_rows = None
    if args.chase_json:
        chase_rows = json.loads(Path(args.chase_json).read_text(encoding="utf-8"))
    status_rows = None
    if args.status_json:
        status_rows = json.loads(Path(args.status_json).read_text(encoding="utf-8"))

    if args.page_byte_budget is not None:
        import widget_transport as _wt
        _wt.WIDGET_PAGE_BYTE_BUDGET = int(args.page_byte_budget)

    transport = run_surface(
        args.surface, args.workspace, page=args.page,
        page_size=args.page_size, now_iso=args.now, moves_rows=moves_rows,
        chase_rows=chase_rows, status_rows=status_rows,
        personal_cap=args.personal_cap,
        promised_cap=getattr(args, "promised_cap", _MP_PROMISED_CAP),
        fired_via=args.fired_via, rerun_of=args.rerun_of,
        pageset_ttl_minutes=args.pageset_ttl_minutes)

    pagination = transport.get("pagination") or {}
    print("CR-PAGINATION: " + json.dumps(pagination))
    print("CR-WIDGET-HTML-BEGIN")
    print(transport["html"])
    print("CR-WIDGET-HTML-END")
    # CUT-C item 7 / REVIEW_CUTC F-2: on an over-budget page the transport
    # composes the page's sanctioned TEXT form (`transport["text"]`); this CLI
    # is the skill's only sanctioned build path, so it is printed here between
    # its own markers — the runner relays exactly that block, byte-exact.
    text = transport.get("text")
    if text:
        print("CR-WIDGET-TEXT-BEGIN")
        print(text, end="" if text.endswith(chr(10)) else chr(10))
        print("CR-WIDGET-TEXT-END")
    receipt = transport.get("receipt")
    if receipt is not None:
        print("CR-RECEIPT: " + json.dumps(receipt))
    return 0


__all__ = [
    "BriefOrderError",
    "COUNT_SHAPES",
    "NUMBER_LINE_RE",
    "MountStaleError",
    "assert_number_leads",
    # SPEC SURFACEFIX1 5.1 / 5.4 — the brief's own fences.
    "BriefCountError",
    "OPEN_COUNT_SHAPES",
    "DISTINCT_QUESTION_SHAPES",
    "distinct_question_counts_in",
    "open_counts_in",
    "headline_number_in",
    "assert_single_open_count",
    "ReachabilityLineError",
    "REACHABILITY_SHAPES",
    "reachability_lines_in",
    "catchup_plumbing_lines_in",
    "CATCHUP_PLUMBING_SHAPES",
    "CATCHUP_TYPED_PHRASE_RE",
    "blank_typed_phrase",
    "SURFACE_FORBIDDEN_SHAPES",
    "assert_no_reachability_line",
    "assert_no_catchup_line",
    "CatchUpPlumbingLineError",
    "maintenance_catch_up",
    "MORNING_BRIEF_SURFACE",
    "resolved_host_mode",
    "maintenance_truth_line",
    "MAINTENANCE_TRUTH_FALLBACK",
    "MAINTENANCE_TRUTH_HOLE_RE",
    "MAINTENANCE_TRUTH_WEEKDAYS",
    "maintenance_truth_has_hole",
    "assert_brief_never_asks",
    "assert_no_coaching_leak",
    "BriefCoachingLeakError",
    "BRIEF_SURFACE_TAG",
    "BRIEF_COACHING_SURFACE_TAG",
    "brief_surface_tag",
    "brief_coaching_scan_target",
    "READER_SPAN_OPEN_FALLBACK",
    "READER_SPAN_CLOSE_FALLBACK",
    "reader_span_marks",
    "as_reader_words",
    "strip_reader_marks",
    "DAY_INTENT_TERMINALS",
    "BriefAsksError",
    "interrogatives_in",
    "PLATE_HEADLINE_DEGRADED",
    "count_shaped_before_number",
    # SPEC_SURFACES2_11c BRIEF2 — the brief's CHANGED partition, line two,
    # the one coaching line, the explain-once line.
    "BRIEF_CHANGED_ORDINARY_CAP",
    "CHANGED_MACHINE_BATCH_CATEGORIES",
    "split_machine_batch_lines",
    "brief_changed_lines",
    "BRIEF_DAY_INTENT_LINE",
    "DAY_INTENT_JOIN",
    "brief_day_intent_line",
    "BRIEF_COACHING_LINE",
    "brief_coaching_line",
    "brief_explain_once_line",
    "build_commitment_triage_view",
    "build_end_of_day_pack",
    "build_morning_brief_pack",
    "build_my_plate_view",
    "build_staff_meeting_view",
    "build_waiting_on_view",
    "build_schedule_view",
    "refuse_if_mount_stale",
    "run_board",
    "run_surface",
]


if __name__ == "__main__":
    raise SystemExit(main())
