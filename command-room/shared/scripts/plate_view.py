#!/usr/bin/env python3
"""
plate_view — ONE shape for every commitment surface (SPEC PLATE1, 2026-09-03).

WHY THIS EXISTS
---------------
Seven surfaces grouped the same open rows three different ways (triage by
age, needs-your-call by call, the brief by owner), spelled the verbs
differently on each, and rendered the unowned and the guesses as if they were
work. The operator book read "you owe 0 / owed to you 231" because the
primary-user pointer was unset and every surface degraded quietly. This
module is the one grouping and the one renderer:

    build_plate(workspace_root, *, user_person_id, now_iso, preset) -> dict
        blocks -> projects ("No project" last) -> horizons -> rows.
        Reads through the canonical loaders and the events_io seam; pure
        apart from the reads. REFUSES (returns {"error": <one plain line>})
        when the primary user cannot be resolved (D8) — no surface renders
        lanes without it.

    render_plate(view, surface, verbs) -> {"text", "data_view", ...}
        owns EVERY word: block titles, reason lines, the evidence chip, the
        verb strip, the collapsed-count line. Surfaces pass only `surface`
        and `verbs`. A jargon gate runs on the renderer's own words
        (`PLATE_BANNED_TOKENS`, folding NUMBERS1's plain-words list): the
        words "unconfirmed", "stuck", "unowned" never render.

THE GROUPING (D1, in this order — CONFIRM beats everything)
-----------------------------------------------------------
    CONFIRM   a row carrying a system question (`data.question`), an
              extractor's guess (`pending_review`), or no owner at all
              (the implied question is "whose is this?"). Overdue questions
              render FIRST inside the block with the overdue badge (G3).
    PARKED    a user-written question (`not mine` — P3: the user already
              answered; the item waits for someone to claim it), a
              `status_hint: parked` (P7), or an undated personal task with
              no movement for PARKED_STALE_DAYS. Renders OPEN with its reason
              line — never hidden (P2 / OVERDUE1 D3).
    SCHEDULE  `kind: scheduling`, not yet on the calendar (a booked one is
              closed by the calendar path, so every OPEN one is unscheduled).
    DO IT     you owe it.
    CHASE     owed to you, quiet longer than the party's cadence or
              CHASE_QUIET_DAYS — whichever is shorter.
    WAIT      owed to you, inside that window.

Level 2 is the project (`primary_thread_id` -> the thread's name; "No
project" last). Level 3 is the horizon from the effective due in the
workspace timezone (tz.py): Overdue / This week / Later / No date.

One line per real-world item (CLUSTER1 unchanged): `render_clusters` folds
the survivor's siblings into a count on its line. Sub-items ride their parent
as a progress chip (SUB1) and never render as rows. Observed-tier rows never
render (D5).

COUNTS (D9 / F-56, extended to the block totals)
------------------------------------------------
Header numbers come from `count_commitments(...)["headline"]` — the same
call, the same input. The block totals partition the confirmed top-level set
exactly: DO IT + CHASE + WAIT + SCHEDULE + PARKED + CONFIRM(no owner) ==
headline.total, and CONFIRM(guesses) == headline.unconfirmed. Pinned by
`tests/run_plate1_test.py`.

WHAT THIS MODULE DOES NOT DO
----------------------------
It never writes. The four verbs (`done` / `not mine` / `later` / `drop`)
dispatch through apply-choices to the existing writers (DD-4). It never
decides what to auto-close (POLICY1), never budgets questions (QUIET1), never
attributes (ATTRIB1) — it renders whatever is bound.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Vocabulary — spelled once
# ---------------------------------------------------------------------------

BLOCK_DO_IT = "do_it"
BLOCK_CHASE = "chase"
BLOCK_WAIT = "wait"
BLOCK_SCHEDULE = "schedule"
BLOCK_CONFIRM = "confirm"
BLOCK_PARKED = "parked"

# Render order. CONFIRM leads: a question is the one thing the reader can
# answer in a tap, and an overdue question is the most urgent line on the
# page (G3). Then the work, then what waits, then what rests.
BLOCK_ORDER = (BLOCK_CONFIRM, BLOCK_DO_IT, BLOCK_CHASE, BLOCK_WAIT,
               BLOCK_SCHEDULE, BLOCK_PARKED)

BLOCK_TITLES = {
    BLOCK_DO_IT: "DO IT",
    BLOCK_CHASE: "CHASE",
    BLOCK_WAIT: "WAIT",
    BLOCK_SCHEDULE: "SCHEDULE",
    BLOCK_CONFIRM: "CONFIRM",
    BLOCK_PARKED: "PARKED",
}

# What each block wants, in one clause — the title's subtitle.
BLOCK_WANTS = {
    BLOCK_DO_IT: "you owe these",
    BLOCK_CHASE: "owed to you and gone quiet — nudge them",
    BLOCK_WAIT: "owed to you, still inside their window",
    BLOCK_SCHEDULE: "agreed, not on the calendar yet",
    BLOCK_CONFIRM: "one tap each — is this real, and whose is it?",
    BLOCK_PARKED: "resting on the book — nothing is asked of you",
}

# D2 — DO IT and CHASE open by default; the rest fold to a count line.
# P2 — PARKED renders OPEN (with its reason) and is never folded.
OPEN_BY_DEFAULT = frozenset({BLOCK_DO_IT, BLOCK_CHASE, BLOCK_CONFIRM,
                             BLOCK_PARKED})

HORIZON_OVERDUE = "overdue"
HORIZON_THIS_WEEK = "this_week"
HORIZON_LATER = "later"
HORIZON_NO_DATE = "no_date"
HORIZON_ORDER = (HORIZON_OVERDUE, HORIZON_THIS_WEEK, HORIZON_LATER,
                 HORIZON_NO_DATE)
HORIZON_TITLES = {
    HORIZON_OVERDUE: "Overdue",
    HORIZON_THIS_WEEK: "This week",
    HORIZON_LATER: "Later",
    HORIZON_NO_DATE: "No date",
}
# CUT-D (M ruling 2026-09-06, REVIEW_QUIET1 R3 — "importance first, not
# recency"): a DO IT / CHASE row on one of THESE horizons is never parked by
# the plate cap (QUIET1 D5). The cap parks the oldest captures among the
# rest (Later + No date). Pinned by membership in `run_quiet1_test` [7];
# proven by removal in G49 F14.
CAP_KEEP_HORIZONS = frozenset({HORIZON_OVERDUE, HORIZON_THIS_WEEK})


def rests_under_parked(horizon: str) -> bool:
    """WRAPSTAFF1 4.1 (M's ruling R-23, 2026-09-17) — MAY a row on this
    horizon render under PARKED at all?

    The plate cap already honoured the importance rule (`_cap_cut` excludes
    `CAP_KEEP_HORIZONS`), but the two OTHER paths into `BLOCK_PARKED` did
    not: a row rested months ago by the silence door — back when that door
    was described as six weeks — whose date has since passed rendered under
    Parked forever. The 09-15 wrap listed ten of them, every one overdue
    (ATTENDED_TEST_v5.31.0 B2.4). M's ruling: "overdue rows never rest under
    Parked — the board ruling wins over the silence door."

    This is a RENDER rule and nothing else. The stored park hint is not
    rewritten, no event is appended and the row's history is intact; the row
    simply falls through to the block its bucket dictates and carries its
    stored reason with it, so the reader still sees WHY it was rested.

    The silence door itself is unchanged (`exit_doors.plan_silence` already
    refuses dated and overdue rows on both legs); this closes the rows that
    were rested before it did.
    """
    return horizon not in CAP_KEEP_HORIZONS

NO_PROJECT_LABEL = "No project"
NO_PROJECT_KEY = ""

# ONEPLATE1 — the shapes a "project name" may never be. A thread record
# auto-created from a mail id carries the id AS its name, so a name check
# alone is not enough (ATTENDED_TEST_v5.29.0 B1.3: a Superhuman message id
# rendered as a plate group header, twice).
_ID_SHAPED = (
    re.compile(r"^[0-9a-f]{8,}$", re.I),                 # bare hex id
    re.compile(r"\b[a-z][a-z0-9_]*:[0-9a-f]{6,}\b", re.I),  # superhuman:<hex>
    re.compile(r"^(?:person|project|org|thread|cmt)_[0-9a-z]+$", re.I),
)


def _looks_like_id(name: str) -> bool:
    s = (name or "").strip()
    return bool(s) and any(p.search(s) for p in _ID_SHAPED)


def _leak2_carries_surface_id(name: str) -> bool:
    """LEAK2's half of the same question, when LEAK2 is on the tree.

    `surface_leak_patterns` does not exist on this lane's base, so the
    import is soft: absent, this returns False and `_looks_like_id` answers
    alone; present (after the trial merge), the two are asked as a UNION.
    Nothing here reaches for a module the branch does not have.

    IT IS NOT A CONFLICT-FREE MERGE (REVIEW_ONEPLATE1 N-6, corrected). The
    first cut of this docstring claimed "nothing at merge has to be
    hand-resolved", and the reviewer's trial merge disproved it:
    `plate_view.py` conflicts with LEAK2 in ONE hunk, on `project_group`,
    and that hunk is resolved by hand — keep the body two docstrings below,
    which is the union and loses nothing on either side. What the soft
    import buys is that the PREDICATE needs no resolution and this branch
    runs standalone; the merge itself still wants a pair of eyes.
    """
    try:
        from surface_leak_patterns import carries_surface_id
    except Exception:
        return False
    try:
        return bool(carries_surface_id(name))
    except Exception:  # pragma: no cover — a name check never raises a render
        return False


def id_shaped_name(name: str) -> bool:
    """Is this "name" an id rather than a name a reader would recognise?

    THE UNION of this lane's shapes and LEAK2's, and it is a union because
    NEITHER IS A SUPERSET OF THE OTHER (REVIEW_ONEPLATE1 F-2, measured):

      * `a1b2c3d4e5f6`, `deadbeef` — bare hex under sixteen characters, the
        actual B1.3 shape — this lane's `_looks_like_id` catches, LEAK2's
        `carries_surface_id` does not (its floor is sixteen);
      * `thread_00a`, `cmt_x1`, `org_7` — entity ids with a short or
        non-numeric tail — likewise;
      * `superhuman:9f8e…`, `project_040`, a bare UUID, a sixteen-plus hex
        run — both catch, and LEAK2 catches the UUID this lane does not.

    So the earlier note in `project_group` ("take LEAK2's body — it is a
    superset") was wrong twice over: it drops the bare-hex coverage, and it
    AttributeErrors this lane's suite, which monkeypatches `_looks_like_id`
    by name. Asking both is the resolution, and asking both HERE means the
    trial merge has one function to keep rather than a body to choose.
    """
    return _looks_like_id(name) or _leak2_carries_surface_id(name)


def project_group(project_key, threads: dict) -> tuple:
    """(group key, group header) for a row — SPEC_FLOW1 Lane C item 3:
    **a group header never carries an id.**

    The regression is ATTENDED_TEST_v5.29.0 B1.3: a row whose
    `primary_thread_id` was a Superhuman MESSAGE id rather than a thread on
    file rendered a bare hex string as a plate group header. The old
    expression fell back to the KEY when the name index missed it — and the
    key is the id. The plate's own jargon gate cannot catch it either: a
    group header is user-authored text (a project's name IS the user's word),
    so the gate blanks it before scanning.

    A key with no name on file — or a "name" that is itself an id shape —
    is not a project a reader can recognise. It groups with every other
    unbound row under "No project", which the ordering already puts last.

    SEAM (REVIEW_ONEPLATE1 F-2, corrected): LEAK2 builds this same function
    under this same name, with the same signature and the same two-tuple
    contract; its body asks `surface_leak_patterns.carries_surface_id`.
    That predicate is NOT a superset of this one — it misses bare hex under
    sixteen characters, which is the shape M actually saw (B1.3), and it
    misses `thread_00a` / `cmt_x1` / `org_7`. So this asks BOTH, through
    `id_shaped_name`, with LEAK2's half behind a soft import that answers
    False while its module is absent. AT THE TRIAL MERGE KEEP THIS BODY:
    it is the union, it needs nothing LEAK2 removes, and it loses no
    coverage on either side. (The earlier note here said "take LEAK2's
    body"; that resolution was measured to drop the bare-hex coverage and
    to AttributeError this lane's suite, which patches `_looks_like_id` by
    name.)
    """
    key = str(project_key or "")
    name = str((threads or {}).get(key) or "").strip()
    if not name or id_shaped_name(name):
        return NO_PROJECT_KEY, NO_PROJECT_LABEL
    return key, name


SURFACES = ("brief", "plate", "eod", "wrap", "board")

# The four verbs, as the wire ids `CANONICAL_ACTIONS` already carries
# (verb_taxonomy: `resolved` displays Done, `push to [date]` displays
# Later…, `drop`, `not mine`). D4: exactly these on every verb-bearing
# surface, plus ONE block one-tap where the block has a next move
# (BLOCK_ONE_TAPS below).
VERB_DONE = "resolved"
VERB_LATER = "push to [date]"
VERB_DROP = "drop"
VERB_NOT_MINE = "not mine"
PLATE_VERBS = (VERB_DONE, VERB_LATER, VERB_DROP, VERB_NOT_MINE)
# A question row has no date to move: Done confirms AND closes in one tap
# (P3 / G3), Drop lets it go, Not mine parks it for someone else.
CONFIRM_VERBS = (VERB_DONE, VERB_DROP, VERB_NOT_MINE)
CONFIRM_REDUCED_REASON = ("Fewer options — this one still needs your say-so; "
                          "Done confirms it and closes it in one tap.")

# D4 — THE BLOCK ONE-TAPS (night 2). Two blocks want one more thing than the
# four verbs, and both moves already have a canonical wire id with a shipped
# handler, so no new verb is minted (F-59: one id per behaviour, everywhere):
#
#   CHASE     `nudge`          — drafts the chase email on click (draft
#                                posture, nothing sends). The Waiting On
#                                delegated row's ruled primary verb (WG1-A
#                                D-A4); the plate is a second surface for it.
#   SCHEDULE  `follow-up call` — drafts the invite request for a meeting
#                                that was agreed and never booked.
#
# CONFIRM's one-tap is the attribution pick-list, which ATTRIB1 owns (its
# door 1 renders the ≤3 pre-selected choices); the plate offers its three
# verbs until that lands. No other block gets one: DO IT / WAIT / PARKED
# want a decision, not a draft.
ONE_TAP_CHASE = "nudge"
ONE_TAP_SCHEDULE = "follow-up call"
BLOCK_ONE_TAPS = {
    BLOCK_CHASE: ONE_TAP_CHASE,
    BLOCK_SCHEDULE: ONE_TAP_SCHEDULE,
}
# What the strip says about them, in the renderer's own words (the labels
# themselves come from `verb_taxonomy` at the widget, never restated here).
ONE_TAP_LINES = {
    BLOCK_CHASE: "CHASE rows add one tap: Nudge drafts the chase — nothing sends.",
    BLOCK_SCHEDULE: "SCHEDULE rows add one tap: Follow-up call drafts the invite.",
}
# CUT-C item 7 (ATTENDED_TEST_v5.28.0 B2.6) — the TEXT path names the
# per-row verb set ON THE BLOCK that carries a one-tap, right under its
# heading, so a reader of the text plate (and of a page delivered as text
# when it is over the widget byte budget) sees every verb the row takes,
# including the block's own. The widget path already carried the verb
# (`_block_sections`); the text path stated the four verbs once, globally,
# and nine of ten SCHEDULE rows on the test's page 19 reached the customer
# with "Done" alone. One line per open block, never per row — the goldens
# move by exactly that line (plate1_plate: the open CHASE block).
BLOCK_VERB_LINES = {
    BLOCK_CHASE: ("Each row here takes: Done · Later… · Drop · Not mine · "
                  "Nudge (say `nudge N`)."),
    BLOCK_SCHEDULE: ("Each row here takes: Done · Later… · Drop · Not mine · "
                     "Follow-up call (say `follow-up call N`)."),
}

# CHASE window: quiet longer than this OR longer than the party's learned
# cadence (chase_policy), whichever is shorter (D1).
CHASE_QUIET_DAYS = 5
# PARKED: an undated personal task with no movement this long rests.
PARKED_STALE_DAYS = 30

# The brief's cut (D7): DO IT + CHASE, top rows by horizon, one attention
# number + one pointer (NUMBERS1 R-1 — never three block totals there).
BRIEF_CAP = 5
BRIEF_POINTER = "say `what's on my plate` for the rest"

#: NUMBER1 3.1 (R-26, M 2026-09-17) — THE BRIEF LEADS WITH THE PLATE'S OWN
#: OPEN NUMBER, and the attention count is a BREAKDOWN of it.
#:
#: The brief led with `attention` (DO IT + CHASE) from LB1 until now. On
#: 2026-09-16 that read "53 on your plate today" over a board of 314 and an
#: internal total of 342 (ATTENDED_TEST_v5.31.0 A4, B2.5): three integers
#: for one book, and the one the reader saw first was the smallest and the
#: only one no other surface repeated. M's ruling is that the number the
#: BOARD states is the number the morning states, and that the figure the
#: brief used to lead with keeps its meaning underneath it.
#:
#: The breakdown sits on line two in the board's own shape
#: (`BOARD_BREAKDOWN_TEMPLATE`) so the two surfaces read alike, and it
#: passes `surface_drivers.assert_single_open_count` by construction: the
#: only OPEN-class figure it states is the headline's own.
BRIEF_BREAKDOWN_TEMPLATE = "{open} open · {attention} want you today"
BRIEF_BREAKDOWN_NONE = "{open} open · nothing wants you today"
BRIEF_EMPTY_LINE = "Nothing is waiting on you today."

# Where the collapsed blocks point (DD-2). The phrases are in-chat
# follow-ups on the plate, never router triggers (ROUTEMISS1 rows pin that).
SHOW_MORE_PHRASES = {
    BLOCK_WAIT: "show waiting",
    BLOCK_SCHEDULE: "show scheduling",
}

STATUS_HINT_PARKED = "parked"
TIER_OBSERVED = "observed"

# Night 2 — the brief's rows carry the BLOCK'S verb (what the row wants),
# never the four dispatch verbs (FB-20: the brief is read-only prose).
BRIEF_ROW_VERBS = {
    BLOCK_DO_IT: "Do",
    BLOCK_CHASE: "Chase",
}
# Night 2 — the delta cuts (D7 `eod` / `wrap`): what the window did to the
# plate, in the plate's own shape. The window is the caller's (the day-close
# hands its morning anchor; the wrap hands the week's start).
DELTA_OPENED = "opened"
DELTA_CLOSED = "closed"
DELTA_SLIPPED = "slipped"
DELTA_CAP = 8            # rows per delta section — a cap states its denominator
DELTA_POINTER = "say `what's on my plate` for the whole plate"
WINDOW_WORDS = {"eod": "today", "wrap": "this week"}

# ---------------------------------------------------------------------------
# P4 — the jargon gate on the renderer's own words
# ---------------------------------------------------------------------------

# NUMBERS1 DD-3's plain-words list, folded in (the words a customer has no
# model for), plus the identifier shapes no row may carry (D3: never an id,
# score, seq, or tier word). Applied to the RENDERER'S text only: user-
# authored titles and roster names are blanked before the scan — a title
# that happens to say "stuck" is the user's word, not ours.
CUSTOMER_BANNED_TOKENS = ("unconfirmed", "pending_review", "stuck", "unowned",
                          "needs_review")
PLATE_BANNED_PATTERNS = tuple(
    [(rf"\b{re.escape(tok)}\b", f"plain-words token {tok!r}")
     for tok in CUSTOMER_BANNED_TOKENS]
    + [
        (r"\bseq\s*#?\s*\d+\b", "event-seq pointer"),
        # HYGIENE9 (d2) — the two shapes the v5.27.0 test found on customer
        # surfaces: a score ("extraction confidence 0.5 below threshold") and
        # a wire id (`pcand:53504c35d5f8`). Same regexes as the composer's
        # scrub and the widget validator (`review_reasons`).
        (r"(?:\bconfidence\s+-?\d|\b\d+(?:\.\d+)?\s+below\s+(?:threshold|floor)\b"
         r"|\bbelow\s+(?:threshold|floor)\b|\b(?:extraction|match)\s+confidence\b)",
         "raw score"),
        (r"\b[a-z][a-z0-9_]*:[0-9a-f]{6,}\b", "wire id"),
        (r"\bpcand:\S+", "wire id"),
        (r"\bcmt_[a-z0-9_]+\b", "commitment id"),
        (r"\bcommitment_seq_\d+\b", "legacy commitment id"),
        (r"\b(?:person|project|org|thread)_\d+\b", "entity id"),
        (r"\btier\b", "tier word"),
        (r"\bscore\b", "match score"),
        (r"\bsubstrate\b", "internal architecture term"),
        (r"\borchestrator\b", "internal architecture term"),
        (r"\bjsonl?\b", "wire-format term"),
        (r"\bcounterparty\b", "internal vocabulary"),
    ])


# Stored writer text that reaches a row (a capture's review_reason, a parked
# hint's reason) is re-said in plain words BEFORE the gate: the stored clause
# is a gating input other modules read and is never rewritten — only the
# rendered string changes (the RRF1 posture).
#
# HYGIENE9 (c)/(d2): the composer moved to `review_reasons` so the plate, the
# held queue, triage, waiting-on and the Staff Meeting card all say ONE
# sentence per stored clause (v5.27.0 test B2.5: a raw "extraction
# confidence 0.5 below threshold" reached the plate; PLATE1-N2 F-3: the old
# word-by-word table mangled "substrate seq 12" into "the record an earlier
# record" and PLATE1 night 2 carried it into the wrap). `PLAIN_WORDS` is the
# shared FALLBACK table (same object) — a clause no whole-sentence shape
# knows is re-said phrase-first and then scrubbed of any score or wire-id
# shape, so `plain_words` can never return either (pinned in
# run_hygiene9_test and by the hard-leak fence in `_row_line`).
from review_reasons import (FALLBACK_WORDS as PLAIN_WORDS,  # noqa: E402
                            carries_hard_leak, render_reason)
# LEAK2 — the group header's post-condition (`project_group` below).


def plain_words(text) -> str:
    """Re-say a stored writer clause in customer words (display only) — one
    whole sentence per `; `-joined clause, never a score, never a wire id."""
    return render_reason(text)


# ---------------------------------------------------------------------------
# THE ASK FENCE (M's rulings 1 + 2, 2026-09-03) — mechanical, not prose
# ---------------------------------------------------------------------------
#
# A proposal is EVIDENCE riding the row. A guess a later transcript shows was
# done closes itself, with its quote, receipted and undoable; the one narrow
# band that still writes a chip acts or retracts ITSELF at the policy window.
# Nothing waits on the reader to answer a proposal, so no surface may tell
# them one does — and ruling 2 forbids inventing any new question class at
# all. That was written into four docstrings and one contract paragraph and
# enforced by NOTHING (REVIEW_PLATE1_N2 F-2): the reviewer planted the exact
# sentence "3 proposals are pending, awaiting your answer" into the morning
# brief's template and four suites stayed green.
#
# These are PHRASE shapes, never bare words: "pending" alone is legitimate
# English on a dozen rows ("chase the pending invoice"), so the fence bites
# the claim, not the vocabulary. It runs on the renderer's OWN words with the
# user spans blanked, exactly like the jargon gate beside it.
# The state words a claim uses, and the gap it may span. A period that is
# part of a NUMBER is not a sentence end (re-verify F-2b: `[^.]` alone let
# "Two proposals at 0.75 confidence are outstanding." through, and a
# proposal sentence carrying a confidence number is precisely the shape this
# fence exists to catch). `(?!-)` keeps the hyphenated technical compounds
# out — "pending-first stake, the newest proposal" is a genuine sentence in
# this repo's own prose, and the mirrored pattern trips on it without the
# guard.
_ASK_STATE = (r"(?:pending|outstanding|unanswered|unresolved|not\s+answered"
              r"|still\s+open|awaiting|await\w*|waiting)")
_ASK_GAP = r"(?:[^.]|\.(?=\d))"

# EVERY gap between tokens is `\s+`, never a literal space (re-verify F-2a):
# `render_plate` joins its lines with newlines, so a claim that wraps —
# "awaiting your\nanswer" — is reachable in rendered output, and a pattern
# with a literal space walks straight past it. The probe is ALSO flattened
# before matching (see `scan_no_pending_question`), which is the belt to this
# brace: either alone would have closed the gap, and both together mean a
# future pattern written with a plain space still cannot leak.
ASK_SHAPE_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"awaiting\s+(?:your|a|an|his|her|their)\s+"
    r"(?:answer|reply|response|say|call|confirmation|decision)",
    r"waiting\s+(?:on|for)\s+(?:you|your)(?:\s+to)?\s+"
    r"(?:answer|reply|respond|response|say|confirm|decide|decision)",
    r"unanswered\s+(?:proposal|suggestion|chip)",
    # A claim that a proposal is outstanding, IN EITHER WORD ORDER — the
    # proposal first ("proposals are still outstanding") or the state first
    # ("you have not answered these proposals"). Two mirrored patterns
    # rather than one, because the alternation between them is where four
    # phrasings hid (re-verify F-2b).
    rf"proposals?\b{_ASK_GAP}{{0,40}}?\b{_ASK_STATE}(?!-)\b",
    rf"\b{_ASK_STATE}(?!-)\b{_ASK_GAP}{{0,40}}?\bproposals?\b",
    r"needs?\s+(?:your\s+)?(?:answer|decision|call)\s+(?:on|to)\s+"
    r"(?:this|that|the)\s+(?:proposal|suggestion|chip)",
    r"(?:confirm|answer|approve|reject)\s+(?:this|that|the|a)\s+proposal",
))

# A sentence that FORBIDS the shape is not an instance of it. These markers
# are how a template scan tells "never say a proposal is pending" (a fence)
# from "3 proposals are pending" (the defect the fence exists to stop).
# Exported so the suite and any future lane share ONE list.
#
# THEY NAME THE ACT OF SAYING, not negation in general (re-verify F-2c). The
# first cut listed bare "never" / "do not" / "don't", which made the marker a
# whole-sentence kill switch: "Don't forget: 3 proposals are pending,
# awaiting your answer." was exempt because of its opening two words. A fence
# talks about what a surface may SAY; a claim does not.
ASK_FENCE_PROSE_MARKERS = (
    "never say", "never says", "never tell", "never render", "never write",
    "no sentence", "must not say", "may not say", "may say", "cannot say",
    "do not say", "don't say", "forbidden", "banned", "instead of saying",
    "rather than saying", "never a question", "not a question",
    "never an ask", "reword",
)


class PlateAskError(ValueError):
    """The renderer composed a sentence telling the reader that a proposal is
    waiting on them. M's rulings of 2026-09-03: a guess a transcript shows was
    done closes itself, the middle band's chip acts or retracts itself, and no
    lane may add a question class. The fix is the sentence, never the fence."""


def scan_no_pending_question(text: str) -> None:
    """Raise `PlateAskError` if the renderer's own words claim a proposal is
    pending, awaiting an answer, or waiting on the reader."""
    # FLATTEN FIRST (re-verify F-2a). The renderer joins its lines with
    # newlines, so a claim can wrap mid-phrase; collapsing every whitespace
    # run to one space means the shapes match the sentence a reader sees
    # rather than the layout the composer happened to produce.
    probe = " ".join(_blank_user_spans(text or "").split())
    for pat in ASK_SHAPE_PATTERNS:
        m = pat.search(probe)
        if m:
            raise PlateAskError(
                "a plate surface says a proposal is waiting on the reader: "
                f"{m.group(0)!r}. A proposal is evidence riding the row — it "
                "closes itself or retracts itself (M's rulings 2026-09-03); "
                "no surface may ask about one. Reword the sentence.")


class PlateJargonError(ValueError):
    """The renderer's own words carried a banned token. Fix the words in
    `render_plate`; never widen the gate."""


_USER_OPEN = "\x02"
_USER_CLOSE = "\x03"


def _user(text) -> str:
    """Mark a user-authored span (title, roster name) so the jargon scan
    blanks it. The markers never reach the output — `_finish` strips them."""
    return f"{_USER_OPEN}{text}{_USER_CLOSE}"


def _blank_user_spans(text: str) -> str:
    return re.sub(f"{_USER_OPEN}.*?{_USER_CLOSE}", "", text, flags=re.S)


def _strip_user_marks(text: str) -> str:
    return text.replace(_USER_OPEN, "").replace(_USER_CLOSE, "")


def user_authored_spans(marked_text: str) -> list:
    """Every span of `marked_text` the renderer marked as the CUSTOMER'S OWN
    WORDS — a title, a counterparty's name, a chip, a stored reason, a
    project label — in the order they appear, with the marks removed.

    WHY THIS IS EXPORTED (REVIEW_ONEPLATE1 F-1). The chat gate already stops
    reading the customer's own words for the internal-vocabulary classes
    (`chat_output_renderer.blank_user_text` + `USER_TEXT_BLANKED_LABELS`);
    the DOCUMENT gate had no notion of provenance at all, so one banned
    marketing word inside a commitment the customer's own counterparty
    wrote refused the whole weekly `.docx` — the document was written to
    disk and then refused (ATTENDED_TEST_v5.29.0 B2.3, second half).

    Provenance cannot be recovered from a rendered document, so it travels
    WITH the section: `render_plate` hands these spans back, `wrap_cut`
    carries them, `wrap_docx_section` puts them on the section, and
    `brief_writer.make_brief` passes them to `docx_leak_scanner`, which
    blanks them for the marketing family ONLY. Every other family — ids,
    substrate paths, connector ids, process narration — still scans the
    document in full, and no word list is widened.

    These are the SAME marks `scan_plate_words` blanks, so the two gates
    cannot disagree about which words are the customer's.
    """
    if not marked_text:
        return []
    return [m.strip() for m in
            re.findall(f"{_USER_OPEN}(.*?){_USER_CLOSE}", marked_text, re.S)
            if m.strip()]


def scan_plate_words(text: str) -> None:
    """Raise `PlateJargonError` if the renderer-authored part of `text`
    carries a banned token. User spans (marked) are blanked first."""
    visible = _blank_user_spans(text)
    scan_no_pending_question(text)
    for pattern, label in PLATE_BANNED_PATTERNS:
        m = re.search(pattern, visible, flags=re.IGNORECASE)
        if m:
            raise PlateJargonError(
                f"plate render carries {label} ({m.group(0)!r}) — fix the "
                f"renderer's words in plate_view.render_plate")


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------

def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(value) -> Optional[_dt.date]:
    if not value or not isinstance(value, str):
        return None
    try:
        return _dt.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _parse_iso(value) -> Optional[_dt.datetime]:
    """A tz-aware datetime from an ISO string (Z or offset), else None."""
    if not value or not isinstance(value, str):
        return None
    try:
        d = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


# F-16 (REVIEW_DATE1 round 3) — a sentinel, never `None`, so `_local_today`
# can tell "no instant was handed in, resolve it" apart from "the caller
# already tried and the workspace has no resolvable timezone" (`None` is a
# legitimate, already-resolved answer). Passing `None` explicitly must NOT
# trigger a second resolve attempt.
_INSTANT_UNSET = object()

# Seam round (DATE1 x ONEPLATE1 merge) — the PUBLIC counterpart of
# `_INSTANT_UNSET` for `build_plate` / `surface_numbers`: "I already
# resolved this workspace's timezone for this `now_iso` and it is
# genuinely unresolvable — do not try again."  `None` deliberately keeps
# its ordinary Python meaning here ("not provided, resolve it"), so a
# caller that forwards an Optional it never filled can NEVER suppress the
# resolve and silently re-introduce the DATE1 defect (`today` falling back
# to the raw UTC date slice).  Only this explicit marker suppresses it.
INSTANT_UNRESOLVABLE = object()


def _local_instant(ws: Path, now_iso: str) -> Optional[_dt.datetime]:
    """The workspace-local INSTANT for `now_iso`, or `None` for an
    unresolvable timezone/clock. Resolves the workspace timezone AT MOST
    ONCE. `_local_today` and `build_plate` both derive from this single
    call rather than each resolving on their own (F-16, REVIEW_DATE1
    round 3 — `build_plate` was paying two resolves per render, one via
    `_local_today` and a second for the counter's own INSTANT need).

    Deliberately NO bare-date short-circuit here (unlike `_local_today`):
    this mirrors `build_plate`'s pre-F-16 `to_local(now_iso, ...)` call
    exactly, byte-for-byte in behaviour, for the one reader (the counter's
    INSTANT) that never had a bare-date guard even before this round. The
    guard belongs to `_local_today` alone — putting it here too would
    silently protect `_local_today`'s own guard from ever being exercised,
    which is exactly the shape that would hide a regression there (see the
    F-15 removal proof)."""
    from tz import TZResolutionError, to_local
    try:
        return to_local(now_iso, workspace_path=ws)
    except (TZResolutionError, ValueError, TypeError):
        return None


def _local_today(ws: Path, now_iso: str, *, instant=_INSTANT_UNSET) -> _dt.date:
    """Today in the workspace timezone (tz.py — no silent UTC). A workspace
    with no timezone resolves to the ISO date of `now_iso`, which is the
    same answer for every date-only due (the common case) and the honest
    fallback for a fixture without one.

    `instant` (F-16) — a caller that already resolved `_local_instant`
    (e.g. `build_plate`, which needs the same instant for its own counter)
    passes it here so this never resolves the timezone a second time.
    Leave it unset to resolve here, exactly once. Passing `instant=None`
    explicitly means "already tried, unresolvable" and also costs no
    second resolve."""
    # F-15 (REVIEW_DATE1 round 2) — a bare `YYYY-MM-DD` `now_iso` never
    # carried a time and must not be TZ-shifted backwards: `to_local` reads
    # a date-only string as MIDNIGHT UTC, so on a Pacific workspace
    # `_local_today(ws, "2026-09-07")` returned 2026-09-06. `tz.localize_date`
    # already guards exactly this case; this is the same guard, so a caller
    # that follows `commitment-triage/SKILL.md`'s
    # `build_plate("<WORKSPACE>", now_iso="<now ISO>")` with a bare date
    # gets today, not yesterday. The guard sits before any resolve, so a
    # bare date costs zero timezone resolutions either way.
    if isinstance(now_iso, str) and len(now_iso) == 10 and now_iso.count("-") == 2:
        try:
            return _dt.date.fromisoformat(now_iso)
        except ValueError:
            pass
    if instant is _INSTANT_UNSET:
        instant = _local_instant(ws, now_iso)
    if instant is not None:
        return instant.date()
    d = _parse_date(now_iso)
    return d or _dt.date.today()


def _entities(ws: Path) -> dict:
    import json
    try:
        raw = json.loads((ws / "_hq" / "data" / "entities.json").read_text(
            encoding="utf-8"))
    except Exception:
        return {}
    inner = raw.get("entities")
    return inner if isinstance(inner, dict) else raw


def client_person_ids(ent: dict) -> set:
    """ONEPLATE1 — the people who work at an org the workspace calls a
    client (`orgs[].relationship_type == "client"`). One of the two
    importance signals the widget cap ranks by; read once per build."""
    client_orgs = {o.get("id") for o in (ent.get("orgs") or [])
                   if isinstance(o, dict)
                   and str(o.get("relationship_type") or "").strip().lower()
                   == "client"}
    return {p.get("id") for p in (ent.get("people") or [])
            if isinstance(p, dict) and p.get("org_id") in client_orgs
            and p.get("id")}


def says_money(text) -> bool:
    """ONEPLATE1 — does this row's own words carry an amount? The regex is
    `deal_signal_detector._MONEY_RE`, imported rather than restated: two
    notions of "an amount" that drift make the ranking silently wrong."""
    try:
        from deal_signal_detector import _MONEY_RE
    except Exception:  # pragma: no cover — the ranking degrades, never fails
        return False
    return bool(_MONEY_RE.search(str(text or "")))


def _names_by_id(ent: dict, key: str) -> dict:
    out: dict = {}
    for rec in ent.get(key) or []:
        if isinstance(rec, dict) and rec.get("id"):
            out[rec["id"]] = (rec.get("canonical_name") or rec.get("name")
                              or rec.get("display_name") or "")
    return out


def horizon_of(due, today: _dt.date) -> str:
    """Overdue / This week / Later / No date from the EFFECTIVE due (the
    loader already folded deferrals) against the workspace's today. "This
    week" runs through Sunday of the current ISO week."""
    d = _parse_date(due)
    if d is None:
        return HORIZON_NO_DATE
    if d < today:
        return HORIZON_OVERDUE
    end_of_week = today + _dt.timedelta(days=6 - today.weekday())
    if d <= end_of_week:
        return HORIZON_THIS_WEEK
    return HORIZON_LATER


# ---------------------------------------------------------------------------
# ONEPLATE1 (SPEC_FLOW1 Lane C) — the cap's words, the ranking, and THE
# numbers. All three are pure functions of the model above.
# ---------------------------------------------------------------------------

# PLATENUM1 4.3 — `cap_block_line` and `cap_row_reason` are RETIRED.
#
# They wrote the mechanism onto the surface. On M's book on 09-11 the
# Friday wrap carried 176 Parked rows and most of them read "under the cap:
# no date" — one hundred and seventy-six copies of an internal rule, and
# two rows due the following day reading "under the cap: due Sep 14", which
# was simply false (an overdue or due-this-week row is never cap-parked).
# The reader learned nothing about any row from any of it.
#
# The rule now lives where a rule belongs — in the count line the block
# prints once ("N items rest under Parked …") — and a row says only what is
# TRUE OF THAT ROW: the night-10 real reasons (`exit_doors.SILENCE_PARK_REASON`
# "no movement 45 days", a stored `park_reason`, "waiting on <name>"). A row
# the cap moved and nothing else is true of RENDERS NO REASON AT ALL.


#: PLATENUM1 4.3 — the wrap lists at most this many Parked rows (M's
#: ruling 1, 2026-09-13, default in force). The rest are one count line.
#: 176 rows relayed byte-exact is not a weekly review, it is a data dump,
#: and the reader skipped all of it (ATTENDED_TEST_v5.30.0 Part E wrap).
WRAP_PARKED_CAP = 10

#: PLATENUM1 fix round 1 (REVIEW F-1a) — WHOSE WORDS THE WRAP MAY DELETE.
#:
#: The wrap is prose and never asks, so it drops the PRODUCT's own question
#: reasons. It must never drop the CUSTOMER's. The first cut of the rule was
#: `reason.rstrip().endswith("?")`, which deleted any interrogative reason —
#: including "you said this isn't yours — whose is it?", which is the
#: reader's OWN answer through the `not mine` door, re-said back to him. The
#: weekly document silently lost it (`run_cutplate_test` [3], two checks).
#:
#: The rule LEAK3/CAPTUREONCE1 follow: a reason the customer TYPED is user
#: text and survives verbatim; a reason the PRODUCT wrote is machine text
#: and the wrap may retire it. A row says which via `reason_origin`
#: (`REASON_ORIGIN_CUSTOMER` when it came through a typed verb). Where no
#: origin is stamped, only these exact machine strings are dropped — never a
#: shape, never a trailing "?".
try:  # F-8 (merged-tree review): one value, LEAK3's
    from chat_output_renderer import REASON_ORIGIN_CUSTOMER
except Exception:  # pragma: no cover - renderer absent only in stubs
    REASON_ORIGIN_CUSTOMER = "customer"
REASON_ORIGIN_PRODUCT = "product"


def _customer_typed(row) -> bool:
    """LEAK3's predicate, imported at call time so the plate and the chat
    renderer agree about a STAMPED reason (MF-3). They differ, by design, on
    an UNSTAMPED one: the chat gate treats a missing stamp as machine text,
    while the plate's row line keeps the legacy arm — a reason written
    before the stamp existed is still the customer's words (MF-11c-7b).
    """
    try:
        from chat_output_renderer import customer_typed
    except Exception:  # pragma: no cover - renderer absent only in stubs
        return False
    return bool(customer_typed(row, "reason"))

#: The product's own question reasons, verbatim (`_assign`'s CONFIRM and
#: unowned branches). Every one of these is composed by this module; none is
#: a word the reader wrote.
PRODUCT_QUESTION_REASONS = (
    "whose is this?",
    "is this real?",
    "no owner on record — whose is this?",
)


def parked_cap_line(n_parked, cap_limit) -> str:
    """The cap's rule, said once on the block, WITH ITS OWN COUNT."""
    n = int(n_parked or 0)
    return (f"{n} {'row' if n == 1 else 'rows'} rest here because the plate "
            f"holds {cap_limit} at a time — overdue and this-week items are "
            "never among them.")


def _age_in_days(ts, today: _dt.date):
    d = _parse_date(str(ts or "")[:10])
    if d is None:
        return None
    return max(0, (today - d).days)


#: THE FALLBACK page shape, and the number of pages M ruled a plate may
#: cost a reader ("34 pages makes the surface completely irrelevant",
#: 2026-09-07).
#:
#: REVIEW_ONEPLATE1 F-7: this used to be stated as "the widget pages at
#: nine rows", which is not a constant the transport has. The transport
#: asks for `chat_output_renderer.DEFAULT_PAGE_SIZE` (15) and then SHRINKS
#: it per view against a byte budget, down to a floor of 3 — nine is what
#: that shrink happens to return for the plate's two shapes on M's book,
#: not a number anything declares. So `widget_pages` is now computed from
#: the MEASURED fit (`measured_page_rows`) whenever a rendered view is at
#: hand, and this constant is only the fallback for a caller that has no
#: view to measure. `run_oneplate_test` [2] pins it inside the transport's
#: own bounds, so it reds if either of those moves.
WIDGET_PAGE_ROWS = 9
WIDGET_MAX_PAGES = 5
#: The door to everything the widget held back.
SHOW_PARKED_PHRASE = "show parked"

#: WHY a row is behind that door. Two reasons, and the door says both
#: (REVIEW_ONEPLATE1 F-4: sixty of the two hundred and thirty-nine rows the
#: widget held back on M's book carried no reason at all and no marker, and
#: the door offering them was called `show parked` although they were not
#: parked).
HELD_RESTING = "resting"          # in PARKED — nothing is asked of it
HELD_FURTHER = "further"          # live, but below the cap's line
HELD_NOUNS = {HELD_RESTING: "resting",
              HELD_FURTHER: "further down the list"}


def stamp_held_back(view: dict) -> dict:
    """Mark every row the widget's page holds back, and give the ones the
    CAP held back their own one-line why (REVIEW_ONEPLATE1 F-4).

    A row in PARKED already carries a reason — the cap's, the hint's, the
    stale rule's. A live row the cap pushed below the line carried nothing:
    it left the page silently, and the only door offered for it said
    "parked", which it was not. A budget cut that hides a row owes the
    reader the same sentence a parked row gets.

    Additive: `held_back` (one of `HELD_RESTING` / `HELD_FURTHER`) and, for
    the further-down rows, `held_reason`. Rows that render carry neither.
    Stamped at build so every render reads one answer.
    """
    rows = view.get("rows") or []
    hidden = widget_hidden_ids(view)
    for r in rows:
        r.pop("held_back", None)
        r.pop("held_reason", None)
        if r["id"] not in hidden:
            continue
        if r.get("block") == BLOCK_PARKED:
            r["held_back"] = HELD_RESTING
        else:
            r["held_back"] = HELD_FURTHER
            # PLATENUM1 4.3 — the row's OWN reason if it has one, and
            # nothing if it does not. The retired `cap_row_reason` wrote the
            # mechanism here ("under the cap: no date, 31 days old"), which
            # told the reader about the cap and nothing about the row.
            if r.get("reason"):
                r["held_reason"] = r["reason"]
    return view


def plate_item_count(rows) -> int:
    """How many OPEN ITEMS a set of plate rows stands for — `1 + folded_n`
    each (NUMBER1 3.2).

    THE FOLD IS THE OFF-BY-ONE. `build_plate` counts a cluster-fold
    survivor as `1 + folded_n` into `block_totals` — the statement
    `block_totals[r["block"]] += 1 + r["folded_n"]`, named here rather
    than numbered because a line number is wrong one commit later
    (REVIEW_NUMBER1 F-5: the build record's `:1946-1947` matched no
    revision) — so
    `plate_numbers["open"]` is an ITEM count and the identity
    `open - unconfirmed == open_confirmed` holds against
    `commitment_state.count_commitments`, which counts every folded twin.
    Every figure the BOARD stated was a ROW count (`board_rows`,
    `held_back_counts`, `board_quick_read`'s remainder), so on 2026-09-16
    the header said 314 over footers summing to 313, and on the 09-17
    scratch 318 over 22 + 88 + 207 = 317 — exactly the one folded row
    on that book (ATTENDED_TEST_v5.31.0 A1, B2.5).

    Fixed HERE rather than by dropping `folded_n` from the projection:
    the projection is the book the reader is carrying, the fold is a
    render device, and a header that under-reported the book by its folds
    would break the identity `run_platenum1_test` pins instead of fixing
    the arithmetic on the page. The survivor still counts ONCE as a row,
    says "+N more like it" on its own context line, and lands in exactly
    one footer.
    """
    return sum(1 + int(r.get("folded_n") or 0) for r in (rows or ()))


def held_back_counts(view: dict) -> dict:
    """`{resting, further, total}` — exactly what is behind `show parked`."""
    rows = view.get("rows") or []
    # NUMBER1 3.2 — ITEMS, not rows: this clause is read beside a header
    # that states items.
    n_rest = plate_item_count(
        [r for r in rows if r.get("held_back") == HELD_RESTING])
    n_more = plate_item_count(
        [r for r in rows if r.get("held_back") == HELD_FURTHER])
    return {"resting": n_rest, "further": n_more, "total": n_rest + n_more}


def show_parked_clause(view: dict) -> str:
    """The door line, naming WHAT IS BEHIND IT (REVIEW_ONEPLATE1 F-4).

    Not "239 more": a reader told "more" cannot tell a resting row from a
    row the budget cut, and the two want different things of them.
    """
    n = held_back_counts(view)
    if not n["total"]:
        return ""
    if n["resting"] and n["further"]:
        what = (f"{n['resting']} {HELD_NOUNS[HELD_RESTING]} and "
                f"{n['further']} {HELD_NOUNS[HELD_FURTHER]}")
    elif n["resting"]:
        what = f"{n['resting']} {HELD_NOUNS[HELD_RESTING]}"
    else:
        what = f"{n['further']} {HELD_NOUNS[HELD_FURTHER]}"
    return f"{what} — say `{SHOW_PARKED_PHRASE}`"


def row_title(*candidates) -> str:
    """THE one preparation a title gets before it reaches a rendered row
    (review F-3, 2026-09-17).

    The board applies exactly this and nothing else: the first non-empty
    candidate, stripped, or the untitled placeholder. No clip, no case
    change, no re-wording — a customer's own words reach the reader as the
    customer wrote them, and `_user()` marks them as theirs at the line.

    It is a named function so the WRAP's "Decided for you" act lines can
    print a title exactly as the board prints it. Before this, the wrap's
    named acts went through `change_feed`'s own read and the board's went
    through the expression below, and two cleaners on one title is how the
    same row comes to read two ways on two surfaces. One cleaner, reused.
    """
    from commitment_state import UNTITLED_PLACEHOLDER

    for c in candidates:
        s = str(c).strip() if c is not None else ""
        if s:
            return s
    return UNTITLED_PLACEHOLDER


def importance_key(row: dict) -> tuple:
    """Rank ONE row for the widget cap (SPEC_FLOW1 Lane C item 2), most
    important first: overdue, then due this week, then a client on the
    other end, then money in its words, then age.

    AGE runs NEWEST FIRST, deliberately: this is the direction the shipped
    text cap already ranks by ("the cap parks the OLDEST captures among the
    rest", QUIET1 D5 / CUT-D), because an old undated capture is the one
    nobody has touched. Two caps that ranked age in opposite directions
    would hide different rows on two surfaces of one plate.
    """
    hz = row.get("horizon")
    return (0 if hz == HORIZON_OVERDUE else 1 if hz == HORIZON_THIS_WEEK else 2,
            0 if row.get("client") else 1,
            0 if row.get("money") else 1,
            _neg_ts(str(row.get("ts") or "")),
            str(row.get("id") or ""))


def _neg_ts(ts: str) -> str:
    """Invert an ISO ts so `sorted(asc)` yields newest-first — the same
    digit-complement `surface_drivers._neg_ts` uses (one idiom, two
    modules; a second definition that drifts reorders one surface only)."""
    return (ts or "").translate(str.maketrans("0123456789", "9876543210"))


def widget_hidden_ids(view: dict, *, limit=None) -> set:
    """The row ids the WIDGET holds back (SPEC_FLOW1 Lane C item 2).

    v5.29.0 shipped the forty-row cap as a TEXT rule only: `build_plate`
    moved the overflow into PARKED, and the widget then rendered PARKED like
    every other block — 317 rows at nine a page, 34 to 36 pages
    (ATTENDED_TEST_v5.29.0 B2.4; M: "34 pages makes the surface completely
    irrelevant"). So the widget gains the same cap the text plate has:

      * every resting row (PARKED) is behind the door, not on a page —
        WHATEVER ITS HORIZON. Nothing is asked of a resting row
        (BLOCK_WANTS), so an overdue one that a person parked BY HAND is
        behind the door too, and its reason is one `show parked` away.
        That is the rule as built (REVIEW_ONEPLATE1 F-10 asked for it to
        be said out loud rather than left to a book where no such row
        happens to exist);
      * of the rest — the LIVE rows — every OVERDUE or DUE-THIS-WEEK row
        renders, always. THE CAP MAY NEVER HIDE ONE (importance first,
        DESIGN_RULE §4). This is the half the fence protects;
      * the remainder fills the plate up to the preset's cap, ranked by
        `importance_key`.

    The TEXT plate is untouched: it still prints every block, PARKED
    included, with every reason (P2). This is the widget's door only.
    Returns an empty set under a preset with no cap (`engaged`).
    """
    cap = limit if limit is not None else (view.get("cap") or {}).get("limit")
    rows = view.get("rows") or []
    if not cap:
        return set()
    hidden = {r["id"] for r in rows if r.get("block") == BLOCK_PARKED}
    live = [r for r in rows if r.get("block") != BLOCK_PARKED]
    keep = [r for r in live if r.get("horizon") in CAP_KEEP_HORIZONS]
    rest = sorted((r for r in live if r.get("horizon") not in CAP_KEEP_HORIZONS),
                  key=importance_key)
    room = max(0, cap - len(keep))
    hidden |= {r["id"] for r in rest[room:]}
    return hidden


#: PLATENUM1 (M's ruling 3, 2026-09-13) — WHERE THE NUMBERS LIVE.
#: One file, one map: the id of a row and the number that row carries for
#: as long as it exists. `next` is carried with the map so a number that
#: belonged to a row which has since closed can never come back on a
#: different row.
PLATE_NUMBERS_REL = ("_hq", ".system", "plate_numbers.json")
PLATE_NUMBERS_VERSION = 1


def plate_numbers_path(workspace_root) -> Path:
    """The one file the display numbers live in."""
    return Path(workspace_root).joinpath(*PLATE_NUMBERS_REL)


def load_number_map(workspace_root) -> dict:
    """`{"numbers": {id: n}, "next": n}` as it stands on disk.

    Absent or unreadable → an empty map starting at 1. A number is a
    convenience, never a fact: a read that fails degrades to minting fresh
    numbers rather than refusing the plate.
    """
    import json
    empty = {"version": PLATE_NUMBERS_VERSION, "next": 1, "numbers": {}}
    try:
        raw = json.loads(plate_numbers_path(workspace_root).read_text("utf-8"))
    except Exception:  # noqa: BLE001 — a missing map is the first run
        return empty
    if not isinstance(raw, dict):
        return empty
    nums = raw.get("numbers")
    if not isinstance(nums, dict):
        return empty
    clean = {}
    for k, v in nums.items():
        try:
            clean[str(k)] = int(v)
        except Exception:  # noqa: BLE001 — one bad row never voids the map
            continue
    try:
        nxt = int(raw.get("next") or 0)
    except Exception:  # noqa: BLE001
        nxt = 0
    # MONOTONIC, defensively: `next` is at least one past the highest number
    # the file actually holds, whatever the file claims.
    nxt = max(nxt, (max(clean.values()) + 1) if clean else 1, 1)
    return {"version": PLATE_NUMBERS_VERSION, "next": nxt, "numbers": clean}


def _save_number_map(workspace_root, state: dict) -> bool:
    """Write the map atomically. Returns whether it landed; a failure is
    never fatal to a render (the numbers just get minted again next time)."""
    try:
        from atomic_write import atomic_write_json
        atomic_write_json(plate_numbers_path(workspace_root),
                          {"version": PLATE_NUMBERS_VERSION,
                           "next": int(state["next"]),
                           "numbers": dict(state["numbers"])})
        return True
    except Exception:  # noqa: BLE001 — a number never fails a read
        return False


#: PLATENUM1 fix round 1 (REVIEW F-3) — how long a render waits for the
#: number map's lock before it gives up and mints nothing. Shorter than the
#: writer lock's own 30 s default ON PURPOSE: an event append may fairly
#: make a reader wait, but a RENDER must never hang on one. A render that
#: cannot take the lock shows the numbers already on disk and stamps none of
#: its own - a number is a convenience, never a fact.
NUMBER_MAP_LOCK_TIMEOUT_S = 10.0


def _mint_under_lock(workspace_root, want: list) -> dict:
    """read -> mint -> write, as ONE critical section. Callers hold the
    workspace's writer lock around this."""
    state = load_number_map(workspace_root)
    nums = state["numbers"]
    nxt = state["next"]
    fresh = []
    for key in want:
        if key in nums:
            continue
        nums[key] = nxt
        nxt += 1
        fresh.append(key)
    if not fresh:
        return nums
    state["next"] = nxt
    if _save_number_map(workspace_root, state):
        return nums
    # The write did not land. A number nothing remembers is a number that
    # will be minted again for a DIFFERENT row next time, so these rows
    # render unnumbered rather than carrying one this workspace never
    # recorded.
    for key in fresh:
        nums.pop(key, None)
    return nums


def mint_display_numbers(workspace_root, ids) -> dict:
    """The `{id: n}` map covering `ids`, minting a number for every id that
    has never had one (PLATENUM1 4.2).

      * MONOTONIC — a new number is always higher than every number this
        workspace has ever minted, including the numbers of rows that have
        since closed;
      * NEVER REUSED — a closed row's entry stays in the map forever, so its
        number cannot land on a different item;
      * NEVER RENUMBERED — an id already in the map keeps the number it has,
        whatever order it arrives in today.

    Ids are minted in the order they arrive, which is the text plate's
    order, so a freshly-seeded workspace still reads 1, 2, 3 down the page.

    PLATENUM1 fix round 1 (REVIEW F-3) — MINTING IS A CRITICAL SECTION.
    `atomic_write_json` makes each WRITE atomic; it does not make
    read-modify-write atomic, and `build_plate` writes on every render that
    sees a new id. Unlocked, four renders minting 30 ids each left 30 of 120
    in the file (the last writer clobbered the rest AND rolled `next` back),
    and two renders that both read before either wrote handed one printed
    number to two different items — M's ruling 3 ("never reused") broken
    through the second door. So the whole load->mint->save now runs inside
    the workspace's sanctioned writer lock, and the map is RE-READ inside it.

    A render that cannot take the lock in `NUMBER_MAP_LOCK_TIMEOUT_S` mints
    nothing and returns the numbers already on disk; those rows render
    without a number (`assign_display_numbers`) and get one on a later
    render. That is the reviewer's ruling-1 default: a render never hangs,
    and never invents a number.
    """
    want = [str(cid) for cid in ids]
    on_disk = load_number_map(workspace_root)["numbers"]
    # Nothing new to mint — the overwhelmingly common render. No lock, no
    # write, no contention with the event writers.
    if all(key in on_disk for key in want):
        return on_disk
    try:
        from atomic_write import events_write_section as events_writer_lock  # LEASE3: flock, then lease
    except ImportError:  # pragma: no cover — packaging accident
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from atomic_write import events_write_section as events_writer_lock  # LEASE3: flock, then lease
    try:
        with events_writer_lock(workspace_root, holder="plate_display_numbers",
                                timeout_s=NUMBER_MAP_LOCK_TIMEOUT_S):
            return _mint_under_lock(workspace_root, want)
    except Exception:  # noqa: BLE001 — a contended lock never fails a render
        return on_disk


def resolve_display_number(workspace_root, n):
    """THE MAPPING LEAVES THE MODEL'S HANDS (PLATENUM1 4.2).

    `n` — what the reader typed ("83", "#83", 83) — back to the commitment
    id that number belongs to, or None when no row in this workspace has
    ever carried it. Every typed verb resolves through here, so "drop 83"
    means the same row whatever page the model happens to be holding and
    whatever has been closed since the page was drawn.

    The defect this closes (ATTENDED_TEST_v5.30.0 B2.5): row 83 was one
    item on the morning board and a different item at 15:03, because the
    number was a POSITION in the last render and the model resolved it by
    reading that render back.
    """
    key = str(n or "").strip().lstrip(BOARD_NUMBER_PREFIX).strip()
    if not key.isdigit():
        return None
    want = int(key)
    for cid, num in load_number_map(workspace_root)["numbers"].items():
        if num == want:
            return cid
    return None


def display_number_of(workspace_root, commitment_id):
    """The number THIS item carries, or None if it has never been on a
    plate surface. The inverse of `resolve_display_number`."""
    return load_number_map(workspace_root)["numbers"].get(str(commitment_id))


def assign_display_numbers(rows: list, workspace_root=None) -> list:
    """Stamp THE row number on every row — the number that BELONGS TO THE
    ITEM, read off the persisted map (PLATENUM1 4.2, M's ruling 3).

    `rows` reaches here already sorted by `_row_key` — block, then project,
    then horizon, then due, then title — which is exactly the order
    `_block_text` prints and the order `_block_sections` used to COUNT in.
    Counting positions was safe only while one render existed. The board
    regroups by project across blocks and orders by importance, so its
    third row is not the page's third row; two renders counting their own
    positions would hand the same reader two numbers for one row, and a
    typed `drop 12` would then close whichever row the last render happened
    to put third-from-top.

    v5.30.0 fixed half of that — one number per BUILD, read off the row by
    every render. The other half broke on the attended test: a build is not
    an item. Close row 5 and rebuild, and every row below it moves up one,
    so `later 83 to friday` typed off the morning's board landed on a
    different item in the afternoon (B2.5). So the number now comes off
    disk, is minted on a row's FIRST appearance on any plate surface, and
    is never reused and never renumbered.

    `workspace_root=None` keeps the old positional stamp — the fallback for
    a caller rendering a view that has no workspace behind it. `build_plate`
    always passes one.

    Returns `rows` (stamped in place) so a caller can chain.
    """
    if workspace_root is None:
        for i, r in enumerate(rows, 1):
            r["display_n"] = i
        return rows
    nums = mint_display_numbers(workspace_root, [r["id"] for r in rows])
    for r in rows:
        # PLATENUM1 fix round 1 (REVIEW F-3) — NO POSITIONAL FALLBACK when a
        # workspace is behind the render. A row the map does not carry (the
        # mint could not take the lock, or the write did not land) renders
        # WITHOUT a number rather than with its place in this list, which is
        # exactly the number that could collide with a real one minted for a
        # different item. `board_display_n` leaves an unstamped row alone.
        r["display_n"] = nums.get(str(r["id"]))
    return rows


#: The keys of THE projection — every number any surface may state about the
#: plate comes from here and from nowhere else (SPEC_FLOW1 Lane C item 1).
NUMBER_KEYS = ("open", "open_confirmed", "you_owe", "owed_to_you",
               "unowned", "unconfirmed", "overdue", "attention")


def measured_page_rows(data_view: dict) -> int:
    """How many rows the transport will actually put on ONE page of THIS
    view — measured through `widget_transport.page_row_budget`, never
    assumed (REVIEW_ONEPLATE1 F-7). Falls back to `WIDGET_PAGE_ROWS` when
    the transport cannot be reached at all."""
    try:
        from widget_transport import page_row_budget
        n = int(page_row_budget(data_view))
        return n if n > 0 else WIDGET_PAGE_ROWS
    except Exception:  # pragma: no cover — a number never fails a render
        return WIDGET_PAGE_ROWS


def plate_numbers(view: dict, *, page_rows: Optional[int] = None) -> dict:
    """THE numbers, from ONE `build_plate` view (SPEC_FLOW1 Lane C item 1).

    ATTENDED_TEST_v5.29.0 Part A found four totals for one book on one day —
    46 on the brief, 297 on the plate, 158 on My Plate, 24 in the brief's
    own list — because four surfaces each counted their own way over their
    own input (one folded duplicates first, one dropped the observed tier,
    one counted after a confidence filter). Not one of them was wrong on its
    own terms, which is exactly the problem: a reader cannot tell four
    definitions from four books.

    So every surface asks THIS, and states only what it hands back.
    `blocks` is the one set of block counts; `attention` is DO IT + CHASE
    (what the brief leads with); `widget_rows` / `widget_pages` are what the
    plate costs a reader after the cap.

    ROWS AND ITEMS ARE TWO POPULATIONS, AND THIS DICT CARRIES BOTH — named,
    after REVIEW_NUMBER1 F-4 found them sharing a prefix and no label:

      `widget_held`        — ROWS the widget held back (`len(hidden)`); the
                             reader's page cost, which is what a cap spends.
      `widget_held_items`  — OPEN ITEMS those rows stand for
                             (`plate_item_count`): a cluster-fold survivor
                             is one row and `1 + folded_n` items.
      `widget_held_split`  — the same ITEM count, split `{resting, further,
                             total}`; `total == widget_held_items` always,
                             and `widget_held_items >= widget_held`, equal
                             only when nothing behind the door is folded.

    ITEMS are what every HEADER on this plate states (`open` is the block
    sum, and `build_plate` counts a fold survivor as `1 + folded_n` into
    `block_totals` so `open - unconfirmed == open_confirmed` holds against
    `commitment_state.count_commitments`). So the footer under an item
    header counts items too — that is NUMBER1 3.2 — and the two keys that
    still count rows say `rows` or say so here.
    """
    counts = view.get("counts") or {}
    totals = view.get("block_totals") or {}
    hidden = widget_hidden_ids(view)
    shown = max(0, len(view.get("rows") or []) - len(hidden))
    # REVIEW_ONEPLATE1 F-7 — the measured page size when a caller has a
    # rendered view to measure; the documented fallback otherwise.
    rows_per_page = int(page_rows or WIDGET_PAGE_ROWS)
    # PLATENUM1 fix round 1 (REVIEW F-4) — `open` IS THE SUM OF THE BLOCKS.
    #
    # It used to be `counts["total"]`, and `counts["total"]` deliberately
    # leaves out the `pending_review` extractions the plate nevertheless
    # RENDERS, in CONFIRM (`commitment_state.count_commitments`, the INTAKE
    # 2026-07-31 carve-out: "pending_review items are QUEUE MEMBERS, not open
    # commitments"). So the board printed "333 open" over blocks that added
    # up to 335 on M's book on 09-14, and 13 over 16 on the plate1 fixture —
    # at base and at tip, in one header line, with nothing saying which rows
    # the two numbers disagreed about. A reader who counts the page is right
    # and the header is wrong.
    #
    # The header now states the population it is standing over: every row
    # this plate renders. The OTHER population keeps its own name and its
    # own key rather than hiding inside this one:
    #
    #   open            — rows on this plate; the block footers sum to it
    #   unconfirmed     — of those, the extractions nobody has agreed to yet
    #   open_confirmed  — `count_commitments`' total: open minus unconfirmed,
    #                     the book the reader is actually carrying
    #
    # `open - unconfirmed == open_confirmed` is an identity the suite pins,
    # so the carve-out can never go quiet again.
    block_sum = sum(int(totals.get(b, 0) or 0) for b in BLOCK_ORDER)
    return {
        "open": block_sum,
        "open_confirmed": counts.get("total", 0),
        "you_owe": counts.get("you_owe", 0),
        "owed_to_you": counts.get("owed_to_you", 0),
        "unowned": counts.get("unowned", 0),
        "unconfirmed": counts.get("unconfirmed", 0),
        "overdue": counts.get("overdue", 0),
        "attention": (totals.get(BLOCK_DO_IT, 0) + totals.get(BLOCK_CHASE, 0)),
        "blocks": {b: totals.get(b, 0) for b in BLOCK_ORDER},
        "cap": dict(view.get("cap") or {}),
        "widget_rows": shown,
        "widget_page_rows": rows_per_page,
        "widget_pages": (shown + rows_per_page - 1) // rows_per_page,
        # REVIEW F-4 — ROWS here…
        "widget_held": len(hidden),
        # …and ITEMS in both of these, labelled rather than inferred.
        "widget_held_items": held_back_counts(view)["total"],
        "widget_held_split": held_back_counts(view),
    }


def surface_numbers(workspace_root, *, now_iso: Optional[str] = None,
                    user_person_id: Optional[str] = None,
                    local_instant: Optional[_dt.datetime] = None) -> dict:
    """One build, THE numbers (SPEC_FLOW1 Lane C item 1). A surface that
    needs numbers and not rows calls this and nothing else. A refused plate
    (no primary user) comes back as `{"ok": False, "error": <one line>}` —
    the caller degrades, it never invents a number.

    `local_instant` (seam round, DATE1 x ONEPLATE1 merge finding) — passed
    straight through to `build_plate`'s own `local_instant`, for a caller
    that already hoisted its own resolution."""
    view = build_plate(workspace_root, user_person_id=user_person_id,
                       now_iso=now_iso, local_instant=local_instant)
    if not view.get("ok"):
        return {"ok": False, "error": view.get("error") or ""}
    out = plate_numbers(view)
    out["ok"] = True
    return out


# ---------------------------------------------------------------------------
# P7 — data.proposal and status_hint, folded minimally here
# ---------------------------------------------------------------------------

def fold_proposals_and_hints(events: list, *, now_iso: str) -> tuple[dict, dict]:
    """ONE pass over the event stream (the events_io seam) for the two
    fields the resolution policy owns, defined minimally here (P7):

      proposals   {commitment_id: {score, evidence, evidence_ts, nags,
                  has_completion_signal}} from the NEWEST live
                  `commitment_review_proposed` per commitment. LIVE means
                  (REVIEW_MERGED_v5280 F-8 — this describes the body, which
                  is POLICY1-A's `newest_open_proposals`): not superseded by
                  a later re-score of the same (item, source); not closed by
                  a `commitment_review_dismissed` NAMING its seq (a legacy,
                  seq-less dismissal still closes every proposal on that
                  commitment); and its target still open. A proposal with no
                  readable `seq` is skipped by this fold (the gate stamps
                  seq, so only a hand-built fixture can hit that).

                  FIVE KEYS, NOT FOUR (REVIEW_PLATE1_N2 F-1). The day-close
                  ranks its confirm rows on the completion signal FIRST and
                  the score second; emitting four keys left that tier dead
                  on the only path production takes (the driver folds this
                  map and hands it straight to `compute_confirm`), so the
                  rank silently degraded to score-then-age everywhere while
                  a row-planted fixture said otherwise. `has_completion_signal`
                  is CARRIED from the event exactly as the producer wrote
                  it — never derived here, never coerced. It has THREE
                  states: `True` (looked, found completion language),
                  `False` (looked, found none) and `None` (nobody assessed
                  it — legacy rows, and the sent rail, which computes no
                  completion finding at all). They are three different
                  facts; flattening `None` to `False` would make "not
                  assessed" read as "assessed and clean", which is the one
                  thing that would disagree with POLICY1-A at merge without
                  either suite going red.
                  The name and the shape are POLICY1's own (its
                  `commitment_policy.newest_open_proposals` carries the same
                  five); this fold is the fallback that must not drop one.
      hints       {commitment_id: {"status_hint": str, "ts": str}} from the
                  newest `commitment_updated` carrying `status_hint`.

    A PROPOSAL IS EVIDENCE ON THE ROW, NEVER A QUESTION (M's ruling
    2026-09-03). A guess a later transcript shows was done now CLOSES as
    done automatically, with its quote, receipted and undoable — it never
    reaches a surface as an ask. Only the narrow middle band still writes a
    proposal at all, and that chip ACTS OR RETRACTS ITSELF at the policy's
    four-day window; nothing waits on the reader to answer it. So this fold
    supplies the row's evidence chip and the day-close's ranking, and no
    renderer over it may say a proposal is pending, awaiting a reply, or
    nagging. (`nags` is the policy's own field name — P7's — and counts how
    many OPEN proposals stack on the row; a superseded re-score chain counts
    ONCE, so a re-score does not raise it. It is a count of the system's
    standing writes, not of times the reader was asked. Nothing renders it.)

    POLICY1's re-issue writes these shapes onto the projected row; this fold
    is the fallback for a stream that carries them only as events."""
    from commitment_policy import newest_open_proposals
    proposals: dict = {}
    for cid, row in newest_open_proposals(events or []).items():
        proposals[cid] = {
            "score": row.get("score"),
            "evidence": row.get("evidence") or "",
            "evidence_ts": row.get("evidence_ts") or "",
            "nags": int(row.get("nags") or 1),
            # REVIEW_PLATE1_N2 F-1 — carried, not derived, and NOT coerced:
            # True / False / None are three different facts (see the
            # docstring). Without this key the confirm surface's
            # completion tier is unreachable in production.
            "has_completion_signal": row.get("has_completion_signal"),
        }
    hints: dict = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") != "commitment_updated":
            continue
        d = ev.get("data") or {}
        if not d.get("status_hint"):
            continue
        cid = d.get("commitment_id") or d.get("target_id") or ev.get("commitment_id")
        if not cid:
            continue
        hints[str(cid)] = {"status_hint": str(d.get("status_hint")),
                           "ts": ev.get("ts") or "",
                           "reason": d.get("reason") or "",
                           # MF-11c-7 (CARD1 seam): the hint keeps the
                           # writer's provenance stamp so the plate can tell
                           # the customer's typed reason from its own words.
                           "reason_origin": d.get("reason_origin") or ""}
    return proposals, hints


# ---------------------------------------------------------------------------
# build_plate — the one grouping
# ---------------------------------------------------------------------------

def _cid(ev: dict) -> str:
    d = ev.get("data") or {}
    return d.get("id") or f"commitment_seq_{ev.get('seq')}"


def _plain_movement(event_type: str) -> str:
    return {
        "commitment": "captured",
        "commitment_updated": "updated",
        "commitment_reclassified": "reclassified",
        "commitment_reopened": "reopened",
        "outreach_sent": "you nudged them",
        "draft_created": "a draft was staged",
    }.get(event_type or "", "moved")


def _short_date(value) -> str:
    d = _parse_date(value)
    if d is None:
        return ""
    return f"{d.strftime('%b')} {d.day}"


def build_plate(workspace_root, *, user_person_id: Optional[str] = None,
                now_iso: Optional[str] = None,
                preset: str = "default",
                since_iso: Optional[str] = None,
                include_movement: bool = False,
                local_instant: Optional[_dt.datetime] = None) -> dict:
    """The plate model: blocks -> projects -> horizons -> rows (DD-1).

    `user_person_id=None` resolves via `primary_user.resolve_primary_user`;
    an unresolvable user REFUSES: `{"error": <one plain line>, "blocks": {}}`
    (D8). `preset` — QUIET1 D5: `"default"` resolves the workspace's
    EFFECTIVE preset (`quiet.effective_preset` — the stored posture, stepped
    down after fourteen silent days) and applies its plate cap: under
    `light` DO IT + CHASE together render at most 40 rows, under `quiet`
    25, under `engaged` everything; rows beyond the cap move to PARKED with
    the reason on the row, and `view["cap"]` says how many. Which rows
    (CUT-D, M ruling 2026-09-06 — importance first): a row that is OVERDUE
    or DUE THIS WEEK (`CAP_KEEP_HORIZONS`) is never parked; the cap parks the
    OLDEST captures among the rest (Later + No date). The cap is a CAP on
    what renders — bucketing, ordering and grouping are untouched, and a
    parked-by-cap row keeps everything else it had. An explicit preset name
    is used as given (tests, previews).

    `since_iso` (night 2, D7 `eod` / `wrap`) adds `view["delta"]` — what the
    window [since, now] did to the plate: `opened` (rows captured in the
    window, in their blocks), `closed` (closures written in the window, with
    the title and whether it was done or dropped), `slipped` (rows still
    open whose due date fell inside the window — they went past their date
    and nobody closed them). The model itself is IDENTICAL with or without
    the window: the delta is a read over the same rows and the same event
    stream, never a second grouping.

    `include_movement=True` also hands back the movement fold this build
    already did (`{commitment_id: Movement}` — the last state change per
    commitment), so a caller that needs it does not re-read and re-fold the
    whole stream (REVIEW_PLATE1_N2 F-4; the sidebar dashboard needs it to
    keep its headline equal to `commitment_counts`, whose `stuck` /
    `blocked` keys exist only when the counter is handed a map). OPT-IN, and
    never on by default: the model is JSON-round-trippable by contract and
    those objects carry datetimes, so a view that always held them could not
    be deep-copied or snapshotted.

    `local_instant` (F-16, REVIEW_DATE1 round 3) — the workspace-local
    INSTANT for `now_iso`, already resolved (`plate_view._local_instant` /
    `tz.to_local`), for a caller that hoisted its own timezone resolution
    before calling here (a merge-seam caller routing several surfaces
    through `build_plate` in one render). Default `None` resolves it once,
    right here — the ordinary path, unchanged for every existing caller.
    Passing it through keeps the WHOLE render's resolve count at one
    instead of `build_plate` resolving again on top of the caller's own
    hoist.

    Returns::

        {"ok": True, "now_iso", "today", "user_person_id",
         "counts": <count_commitments headline>,
         "block_totals": {block: n},
         "blocks": {block: {"total": n, "projects": [
             {"key", "label", "total", "horizons": [
                 {"key", "label", "rows": [row, ...]}]}]}},
         "rows": [row, ...]   # flat, render order
         "n_folded": int}

    Row: {id, title, block, horizon, project_key, project_label, due,
          due_phrase, owner_id, owner_name, counterparty, kind, chip,
          reason, badges, question, question_by, pending, folded_n,
          folded_ids, subitems, movement_days}
    """
    from commitment_activity import classify_commitments, derive_commitment_movement
    from commitment_cluster import render_clusters
    from commitment_state import (
        BUCKET_OWED_TO_YOU, BUCKET_UNCONFIRMED, BUCKET_UNOWNED,
        BUCKET_YOU_OWE, PRIMARY_USER_REFUSAL_LINE, QUESTION_BY_USER_VERB,
        bucket_of, commitment_kind, count_commitments,
    )
    from commitment_parties import (counterparty_ids, primary_counterparty_name)
    from cru_match import (_commitment_field, load_open_commitments,
                           partition_subitems, split_pending_review)
    from due_reanchor import render_due_phrase
    from primary_user import resolve_primary_user
    import events_io

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    if user_person_id is None:
        try:
            user_person_id = resolve_primary_user(ws)
        except Exception:
            user_person_id = None
    if not user_person_id:
        return {"ok": False, "error": PRIMARY_USER_REFUSAL_LINE, "blocks": {},
                "rows": [], "block_totals": {}, "counts": {}}

    events_path = _events_path(ws)
    # INDEX1 D5 — the one full-history read for this surface goes through
    # the events_io seam (shards + active file); the loader projects over it.
    all_events = events_io.load_all(ws)
    opens = load_open_commitments(events_path, events=all_events,
                                  workspace_root=str(ws))
    # D5 — the observed tier is never a row on any plate surface, and the
    # header counts are `count_commitments` over the SAME input the rows come
    # from (F-56): drop the tier before counting, not after.
    opens = [ev for ev in opens
             if (ev.get("data") or {}).get("tier") != TIER_OBSERVED]
    movement = derive_commitment_movement(events_path)
    # F-12 (REVIEW_DATE1 round 2) — `count_commitments` uses `now_iso` for TWO
    # different kinds of arithmetic: `is_overdue`/`overdue_days` slice it to a
    # DAY (a bare `today.isoformat()` is right for that), but the `movement`
    # map's elapsed-time rule (`classify_commitments` -> `datetime.fromisoformat`)
    # wants the INSTANT. Handing it the bare date reads as midnight UTC and
    # moves the movement clock backwards by the workspace's offset plus the
    # time of day, silently under-counting `stuck`/`blocked`.
    # `_local_instant` (not `localize_date`, which returns a bare date by
    # contract) gives the workspace-local instant so both `today` and the
    # counter get the right shape. A workspace with no resolvable timezone
    # (or an unparseable clock) falls back to the raw `now_iso` — the same
    # quiet-fallback posture ruling R1 already took for the header and
    # every due-phrase, never a raise mid-render.
    # F-16 (REVIEW_DATE1 round 3) — ONE resolve for the whole render: a
    # caller that already hoisted this (`local_instant`) is trusted as-is;
    # otherwise resolve here, once, and hand the SAME instant to `_local_today`
    # so it never resolves a second time on top of this one.
    _lnow = (None if local_instant is INSTANT_UNRESOLVABLE
             else local_instant if local_instant is not None
             else _local_instant(ws, now_iso))
    today = _local_today(ws, now_iso, instant=_lnow)
    counts = count_commitments(opens, user_person_id=user_person_id,
                               now_iso=(_lnow.isoformat() if _lnow else now_iso),
                               movement=movement)
    headline = counts["headline"]
    confirmed_all, _pending = split_pending_review(opens)
    top_level, sub_level = partition_subitems(opens)
    subs_by_parent: dict = {}
    for ev in sub_level:
        subs_by_parent.setdefault((ev.get("data") or {}).get("parent_id"), []).append(ev)
    confirmed_top = [ev for ev in top_level
                     if bucket_of(ev, user_person_id) != BUCKET_UNCONFIRMED]
    cls = classify_commitments(confirmed_top, movement, now_iso)
    blocked_ids = {r["commitment_id"] for r in cls["blocked"]}
    stuck_ids = {r["commitment_id"] for r in cls["stuck"]}
    proposals, hints = fold_proposals_and_hints(all_events, now_iso=now_iso)

    now_dt = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=_dt.timezone.utc)
    ent = _entities(ws)
    people = _names_by_id(ent, "people")
    threads = _names_by_id(ent, "threads")
    # ONEPLATE1 — the two importance signals the widget cap ranks by, read
    # once here rather than per row.
    client_ids = client_person_ids(ent)

    # The party's cadence (chase_policy) — the learned window when there is
    # one, DEFAULT otherwise; the block rule takes the shorter of it and
    # CHASE_QUIET_DAYS.
    try:
        from chase_policy import get_chase_window, load_chase_policy
        policy = load_chase_policy(ws)
    except Exception:
        policy, get_chase_window = {}, None

    def _cadence_days(ev) -> int:
        if get_chase_window is None:
            return CHASE_QUIET_DAYS
        rtype = ""
        for pid in counterparty_ids(ev) or [_commitment_field(ev, "owner_id")]:
            rec = next((p for p in (ent.get("people") or [])
                        if isinstance(p, dict) and p.get("id") == pid), None)
            if rec:
                rtype = rec.get("relationship_type") or ""
                break
        try:
            days, _esc = get_chase_window(policy, rtype, CHASE_QUIET_DAYS)
        except Exception:
            days = CHASE_QUIET_DAYS
        return min(int(days or CHASE_QUIET_DAYS), CHASE_QUIET_DAYS)

    def _movement_days(cid: str) -> Optional[int]:
        m = movement.get(cid)
        if m is None:
            return None
        return max(0, (now_dt - m.ts).days)

    def _person_name(pid) -> str:
        if not pid:
            return ""
        return people.get(pid) or ""

    def _counterparty_line(ev, bucket) -> str:
        """The NAME on the row (D3): who you owe it to, or who owes it."""
        if bucket == BUCKET_OWED_TO_YOU:
            return (_person_name(_commitment_field(ev, "owner_id"))
                    or _commitment_field(ev, "owner_name") or "")
        ids = counterparty_ids(ev)
        if ids:
            nm = _person_name(ids[0])
            if nm:
                return nm
        return primary_counterparty_name(ev, workspace_root=str(ws)) or ""

    def _chip(ev, cid) -> str:
        # P7 — POLICY1 writes `data.proposal` onto the projected row; this
        # fold READS it when present and falls back to the stream fold when
        # absent (main today), so both branches pass alone.
        p = (ev.get("data") or {}).get("proposal")
        if not isinstance(p, dict) or not p.get("evidence"):
            p = proposals.get(cid)
        if p and p.get("evidence"):
            when = _short_date(p.get("evidence_ts"))
            core = p["evidence"]
            return f"{core} ({when})" if when else core
        m = movement.get(cid)
        if m is None:
            return ""
        return f"{_plain_movement(m.event_type)} {_short_date(m.ts.isoformat())}".strip()

    def _assign(ev, bucket) -> tuple[str, str, list, bool]:
        """(block, reason, badges, reason_is_stored) for one top-level row —
        the D1 rules, in order. CONFIRM beats everything."""
        d = ev.get("data") or {}
        cid = _cid(ev)
        badges: list = []
        due = d.get("due")
        overdue = horizon_of(due, today) == HORIZON_OVERDUE
        question = d.get("question")
        question_by = d.get("question_by")
        # REVIEW_PLATE1_N1 F-2 — a reason that ORIGINATES in stored writer
        # text (a capture's review_reason, a parked hint's reason) is re-said
        # in plain words AND marked as writer text on the row, so the P4
        # gate never refuses a whole render over one old clause. The
        # renderer's own reasons stay unmarked and fully gated.
        stored = False
        if question and question_by == QUESTION_BY_USER_VERB:
            when = _short_date(d.get("question_ts") or "")
            asked = "you said this isn't yours"
            if when:
                asked += f" on {when}"
            return BLOCK_PARKED, f"{asked} — whose is it?", badges, False
        if bucket == BUCKET_UNCONFIRMED or question or bucket == BUCKET_UNOWNED:
            if overdue:
                badges.append("overdue")
            if bucket == BUCKET_UNCONFIRMED:
                reason = "is this real?"
                rr = d.get("review_reason")
                if rr:
                    reason = plain_words(rr)
                    stored = True
            elif question:
                reason = "whose is this?" if question == "whose_is_this" else "needs your call"
            else:
                reason = "no owner on record — whose is this?"
            return BLOCK_CONFIRM, reason, badges, stored
        hint = hints.get(cid)
        # P7 — a `status_hint` folded onto the projected row by the loader
        # (POLICY1 DD-6, `data.status_hint` + `data.park_reason`) wins over
        # the stream fold; absent on main today, so the fold below stands.
        if d.get("status_hint"):
            # POLICY1-B DD-6 — the loader now folds the hint with its ts
            # (`status_hint_ts`), so the date renders exactly as the
            # stream fold's did.
            hint = {"status_hint": str(d.get("status_hint")),
                    "ts": d.get("status_hint_ts") or "",
                    "reason": d.get("park_reason") or d.get("reason") or ""}
        # WRAPSTAFF1 4.1 (R-23) — the row's horizon, read ONCE for both
        # parked branches below. `overdue` above is the same test narrowed
        # to one horizon; this is the pair `rests_under_parked` reads.
        row_horizon = horizon_of(due, today)
        # A rested row that is OVERDUE or DUE THIS WEEK does not render under
        # PARKED: it falls through to the block its bucket dictates and
        # CARRIES ITS STORED REASON, so the reader still sees why it was
        # rested. Nothing is rewritten — see `rests_under_parked`.
        carried, carried_stored = "", False
        if hint and hint.get("status_hint") == STATUS_HINT_PARKED:
            when = _short_date(hint.get("ts"))
            why = plain_words(hint.get("reason") or "parked")
            rested = f"{why} ({when})" if when else why
            if rests_under_parked(row_horizon):
                return BLOCK_PARKED, rested, badges, True
            carried, carried_stored = rested, True
        kind = commitment_kind(ev)
        if kind == "scheduling":
            return BLOCK_SCHEDULE, carried, badges, carried_stored
        if bucket == BUCKET_YOU_OWE:
            mdays = _movement_days(cid)
            # The no-movement branch already requires an UNDATED row, so its
            # horizon is always `HORIZON_NO_DATE` and the test can never
            # refuse it today. It is stated anyway: the day a dated row
            # reaches this branch, the ruling must still hold here.
            if (kind == "task" and _parse_date(due) is None
                    and mdays is not None and mdays >= PARKED_STALE_DAYS
                    and rests_under_parked(row_horizon)):
                return BLOCK_PARKED, f"no movement {mdays} days", badges, False
            return BLOCK_DO_IT, carried, badges, carried_stored
        # owed to you. A nudge you already sent (`blocked` in the movement
        # classification) reset the quiet clock: WAIT inside the window,
        # CHASE again past it — the same clock, no special case.
        mdays = _movement_days(cid)
        window = _cadence_days(ev)
        if mdays is not None and mdays > window:
            return (BLOCK_CHASE, carried or f"quiet {mdays} days", badges,
                    carried_stored)
        return BLOCK_WAIT, carried, badges, carried_stored

    rows: list = []
    for ev in top_level:
        d = ev.get("data") or {}
        cid = _cid(ev)
        bucket = bucket_of(ev, user_person_id)
        block, reason, badges, reason_stored = _assign(ev, bucket)
        due = d.get("due")
        kids = subs_by_parent.get(cid) or []
        n_open_k = d.get("n_subitems_open")
        n_done_k = d.get("n_subitems_done") or 0
        subitems = None
        if kids or isinstance(n_open_k, int):
            subitems = {"open": len(kids) if kids else (n_open_k or 0),
                        "done": n_done_k}
        project_key, project_label = project_group(
            ev.get("primary_thread_id") or NO_PROJECT_KEY, threads)
        rows.append({
            "id": cid,
            "title": row_title(d.get("title"), d.get("summary")),
            "block": block,
            "horizon": horizon_of(due, today),
            "project_key": project_key,
            "project_label": project_label,
            "due": due,
            # HYGIENE9 (e2) — THE PHRASE AND THE BUCKET SHARE ONE "TODAY".
            # `horizon_of` above buckets the row against the workspace-local
            # `today` (tz.py); this phrase used to take its anchor from the
            # ISO date of `now_iso`, which is UTC — so after 17:00 Pacific a
            # row due tomorrow read "due today", a row due today read "1 day
            # ago", and every overdue row aged a day early, while the
            # morning brief on the same machine still said the right date
            # (v5.27.0 supervised test, the C-run finding). This is the ONE
            # place the plate derives the phrase — the brief, day-close,
            # board and wrap cuts all read `due_phrase` off this row — so
            # anchoring it to `today` fixes every plate render at once.
            "due_phrase": render_due_phrase(due, today.isoformat()) if due else "",
            "owner_id": _commitment_field(ev, "owner_id") or "",
            "owner_name": _person_name(_commitment_field(ev, "owner_id")),
            "counterparty": _counterparty_line(ev, bucket),
            # ONEPLATE1 — the widget cap's two importance signals, stamped
            # on the row so the ranking is a pure function of the model.
            "client": bool(set(counterparty_ids(ev) or []) & client_ids),
            "money": says_money(d.get("title") or d.get("summary") or ""),
            "kind": commitment_kind(ev),
            "chip": _chip(ev, cid),
            "reason": reason,
            "reason_stored": reason_stored,
            # PLATENUM1 fix round 1 (REVIEW F-1a) — WHOSE WORDS THIS IS.
            # The `not mine` door stores the reader's own typed verb
            # (`question_by == QUESTION_BY_USER_VERB`) and `_assign` says it
            # back to him ("you said this isn't yours — whose is it?"). That
            # is customer text: no surface may retire it. Everything else in
            # this reason column the product composed.
            # MF-2 (trial merge 2026-09-14): `reason_origin` means the
            # customer's OWN TYPED WORDS - the contract LEAK3's
            # `customer_typed()` enforces (a `customer` stamp exempts the field
            # from the internal-vocabulary scan). Every reason in this column
            # is composed by this module, so it is `product` here, always; the
            # `not mine` answer survives on the wrap because of the ACT
            # (`question_by`), not because of a provenance stamp on words the
            # customer never typed. A writer that stores the customer's typed
            # reason stamps `customer` itself.
            # MF-11c-7 (CARD1 seam, MF-5's plate half): the stamp the WRITER
            # put on the reason travels. MF-11c-7b (reader, FAIL): a writer
            # that said NOTHING stays "" — never `product` — because the
            # row line's legacy arm reads that absence as "written before
            # the stamp existed, still the customer's words"; a `product`
            # fallback here made that arm unreachable and would have refused
            # the whole wrap on 9 of the book's 809 historical reasons.
            # `REASON_ORIGIN_PRODUCT` is stamped only by the writers that
            # COMPOSE a reason (the silence park), never invented here.
            "reason_origin": ((hints.get(cid) or {}).get("reason_origin") or ""),
            "badges": badges,
            "question": d.get("question"),
            "question_by": d.get("question_by"),
            "pending": bucket == BUCKET_UNCONFIRMED,
            "bucket": bucket,
            "folded_n": 0,
            "folded_ids": [],
            "subitems": subitems,
            "movement_days": _movement_days(cid),
            "quiet": cid in stuck_ids,
            "nudged": cid in blocked_ids,
            "ts": ev.get("ts") or "",
        })

    # CLUSTER1 — one line per real-world item. Clusters are computed over the
    # rows that render (confirmed top-level); the survivor carries the count.
    by_id = {r["id"]: r for r in rows}
    cluster_input = [ev for ev in top_level
                     if _cid(ev) in by_id and not by_id[_cid(ev)]["pending"]]
    clusters = render_clusters(cluster_input, workspace_root=str(ws), now_iso=now_iso)
    n_folded = 0
    for c in clusters or []:
        survivor = by_id.get(c["survivor_id"])
        if survivor is None:
            continue
        folded = [fid for fid in c["folded_ids"] if fid in by_id
                  and by_id[fid]["block"] == survivor["block"]]
        if not folded:
            continue
        survivor["folded_n"] = len(folded)
        survivor["folded_ids"] = folded
        n_folded += len(folded)
        for fid in folded:
            by_id[fid]["_folded_into"] = c["survivor_id"]
    rows = [r for r in rows if not r.get("_folded_into")]

    # QUIET1 D5 — THE one point the plate cap touches (SPEC_QUIET1 D5): the
    # oldest DO IT + CHASE rows beyond the preset's cap park with the reason
    # on the row. Applied AFTER the cluster fold (a survivor counts once
    # with its fold) and BEFORE the sort, so the ruled order below is what
    # renders the survivors; nothing else about a row changes.
    import quiet as _quiet
    if preset == "default":
        preset = _quiet.effective_preset(ws)
    cap_limit = _quiet.plate_cap(preset) if preset in _quiet.PRESETS else None
    n_capped = 0
    n_capped_new = 0
    if cap_limit is not None:
        live = [r for r in rows if r["block"] in (BLOCK_DO_IT, BLOCK_CHASE)]
        if len(live) > cap_limit:
            # CUT-D (M ruling 2026-09-06, REVIEW_QUIET1 R3): importance
            # first, not recency. A row that is OVERDUE or DUE THIS WEEK
            # (`CAP_KEEP_HORIZONS` — the plate's own horizons, against the
            # workspace-local `today`) is NEVER parked by the cap. The cap
            # parks exactly as many rows as the plate is over its limit,
            # taken from the REST (Later + No date) oldest capture first —
            # or every remaining row when the important ones alone exceed
            # the cap. `_cap_cut` is the one rule; the wrap's old/new read
            # below re-runs it over the older rows.
            def _cap_cut(pool: list) -> list:
                rest = sorted((r for r in pool if r["horizon"] not in CAP_KEEP_HORIZONS),
                              key=lambda r: (r.get("ts") or "", r["id"]))
                return rest[:max(0, len(pool) - cap_limit)]
            # REVIEW_QUIET1 F-3 — "listed once in the wrap": a cap-parked
            # row is a VIEW rule with no stored hint, so the wrap tells old
            # from new by re-running the same cut over the rows that existed
            # at the window's start (`since_iso`): a row that was ALREADY
            # beyond the cap then is not news and is not re-listed; a row
            # that crossed the cap inside the window is. Approximate on
            # purpose (closures inside the window are ignored, and the
            # horizons are today's) and only a rendering hint —
            # `parked_by_cap` itself is unchanged.
            was_parked: set = set()
            if since_iso:
                older = [r for r in live if (r.get("ts") or "") <= since_iso]
                was_parked = {r["id"] for r in _cap_cut(older)}
            for r in _cap_cut(live):
                r["block"] = BLOCK_PARKED
                # ONEPLATE1 (ATTENDED_TEST_v5.29.0 B2.3: ~130 of 164 parked
                # rows carried ONE sentence as their "reason", so the column
                # told the reader nothing about the row). The RULE is said
                # once — under the PARKED heading on the plate, and in the
                # wrap's roll-up line — and each row says why IT is the one
                # that went there. REVIEW_CUTD F-1's requirement (the
                # sentence names the rule that is actually applied) is met
                # by the block line, not by 130 copies of it.
                # PLATENUM1 4.3 — the row KEEPS whatever is true of it (a
                # stored park reason, "no movement 45 days", a review
                # reason) and gains nothing. `cap_row_reason` used to
                # overwrite it with the mechanism; a row with no real
                # reason now renders none.
                r["parked_by_cap"] = True
                r["parked_by_cap_new"] = r["id"] not in was_parked
                n_capped += 1
                if r["parked_by_cap_new"]:
                    n_capped_new += 1

    # Order: block -> project (No project last, else by label) -> horizon ->
    # (overdue questions first inside CONFIRM) -> due -> title.
    def _row_key(r):
        return (BLOCK_ORDER.index(r["block"]),
                1 if r["project_key"] == NO_PROJECT_KEY else 0,
                r["project_label"].lower(),
                HORIZON_ORDER.index(r["horizon"]),
                0 if "overdue" in r["badges"] else 1,
                r["due"] or "9999",
                r["title"].lower())
    rows.sort(key=_row_key)
    # R-N10-1 — THE ONE NUMBERING, stamped here and read everywhere. The
    # board reorders rows by importance and regroups them by project, so a
    # number counted off a rendered position would differ between the board
    # and the page — and a reader who typed `later 12` would move a
    # different row on each. The number belongs to the ROW, in the text
    # plate's order, and every render reads it off the row. PLATENUM1 — and
    # the row reads it off the persisted map, so the number survives a close
    # and a rebuild (M's ruling 3, 2026-09-13).
    assign_display_numbers(rows, workspace_root=workspace_root)

    blocks: dict = {}
    block_totals: dict = {b: 0 for b in BLOCK_ORDER}
    for r in rows:
        blk = blocks.setdefault(r["block"], {"total": 0, "projects": []})
        blk["total"] += 1 + r["folded_n"]
        block_totals[r["block"]] += 1 + r["folded_n"]
        proj = next((p for p in blk["projects"] if p["key"] == r["project_key"]), None)
        if proj is None:
            proj = {"key": r["project_key"], "label": r["project_label"],
                    "total": 0, "horizons": []}
            blk["projects"].append(proj)
        proj["total"] += 1 + r["folded_n"]
        hz = next((h for h in proj["horizons"] if h["key"] == r["horizon"]), None)
        if hz is None:
            hz = {"key": r["horizon"], "label": HORIZON_TITLES[r["horizon"]],
                  "rows": []}
            proj["horizons"].append(hz)
        hz["rows"].append(r)
    for b in BLOCK_ORDER:
        blocks.setdefault(b, {"total": 0, "projects": []})

    plate = {
        "ok": True,
        "now_iso": now_iso,
        "today": today.isoformat(),
        "user_person_id": user_person_id,
        "preset": preset,
        "cap": {"limit": cap_limit, "parked": n_capped, "parked_new": n_capped_new},
        "counts": dict(headline),
        "block_totals": block_totals,
        "blocks": blocks,
        "rows": rows,
        "n_folded": n_folded,
    }
    # REVIEW_ONEPLATE1 F-4 — every row the widget's page holds back carries
    # its marker and, where the CAP is what held it, its own why. Stamped
    # once, here, so the page, the door and the text all read one answer.
    stamp_held_back(plate)
    if include_movement:
        # The fold this build already did, handed back on request (F-4).
        # Model-only: never rendered, never persisted, never a widget key —
        # and absent unless asked for, because it is not JSON-safe.
        plate["movement"] = movement
    if since_iso:
        # (The local is `plate`, not `view`: SWEEPRENDER Slot 9's scanner
        # reads `view["k"] = …` in any module that mentions `widget_mode` as
        # a write to a WIDGET data view, and this module holds both shapes.)
        plate["delta"] = _delta(rows, all_events, since_iso=since_iso,
                                now_dt=now_dt, today=today, ws=ws)
    return plate


def _delta(rows: list, all_events: list, *, since_iso: str,
           now_dt: _dt.datetime, today: _dt.date, ws: Path) -> dict:
    """What the window did to the plate (night 2, D7 `eod` / `wrap`).

    opened   plate rows whose capture `ts` is inside the window, in render
             order (so they carry their block, project and horizon).
    closed   `commitment_resolved` written inside the window — id, title
             (joined from the row's own capture, never invented), done or
             dropped, when. Sub-items and unconfirmed guesses count here
             exactly as the ledger counts them: a closure is a closure.
    slipped  rows STILL OPEN whose effective due date fell inside the
             window AND HAS PASSED: the date is strictly before today and
             nobody closed the item. A row already overdue before the
             window is not a slip of this window — it is on the plate's
             Overdue horizon. A row due TODAY has not slipped — it has not
             passed yet — even if it is still open when the day closes.

             DATE1 (2026-09-07, M's ruling 9 — ATTENDED_TEST_v5.29.0 /
             NIGHT10_THREE_SURFACES_TRAIN "RULINGS ADDED"): *"'Slipped' =
             the date has passed; a row due today is not slipped at the
             day-close."* This CORRECTS CUT-PLATE's 2026-09-06 choice,
             recorded below for the history: CUT-PLATE read a same-day
             window (the morning anchor or the day floor) and reasoned that
             with `due < today` no row could ever slip on a scheduled 5 PM
             fire (PLATE1-N2's own record §5.5 noted the resulting 0-by-
             construction count), so it widened the test to
             `since_date <= due <= today` — a row due TODAY that is still
             open counted as slipped. M's ruling 9 says that is the wrong
             word for that row: due-today is not yet overdue, so it is not
             "slipped" by any reading a customer would recognize, and the
             lane that produced 0-by-construction on a same-day window is
             an honest count under this definition, not a defect — a
             same-day EOD window CANNOT contain a row whose date has
             already passed within that same day. The test is
             `since_date <= due < today`. Readers of `n_slipped`: the eod
             and wrap headers (`_delta_text`), the persisted pack, and the
             CUT-PLATE / DATE1 suites; the PLATE1 goldens carry no row due
             on their fixture's `today`, so they are byte-identical.
    """
    since = _parse_iso(since_iso)
    if since is None:
        return {"since": since_iso, "opened": [], "closed": [], "slipped": [],
                "n_opened": 0, "n_closed": 0, "n_slipped": 0,
                "window_unreadable": True}
    try:
        from tz import to_local
        since_date = to_local(since_iso, workspace_path=str(ws)).date()
    except Exception:
        since_date = since.date()
    opened, slipped = [], []
    for r in rows:
        ts = _parse_iso(r.get("ts"))
        if ts is not None and since <= ts <= now_dt:
            opened.append(r)
        due = _parse_date(r.get("due"))
        if due is not None and since_date <= due < today:
            slipped.append(r)
    titles: dict = {}
    closed: list = []
    # POLICY1-B (c) — a close a later undo reversed is open at fire time and
    # is not counted here (the same fold End of Day's closed-today uses).
    try:
        from closure_index import reversed_closer_positions as _reversed
        reversed_at = _reversed(list(all_events or []), until=now_dt)
    except Exception:  # pragma: no cover
        reversed_at = set()
    for pos, ev in enumerate(all_events or []):
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") or {}
        et = ev.get("type")
        if et == "commitment" and d.get("id"):
            titles[str(d["id"])] = d.get("title") or d.get("summary") or ""
        elif et == "commitment_resolved":
            if pos in reversed_at:  # (c): reopened since — not a close of this window
                continue
            ts = _parse_iso(ev.get("ts"))
            if ts is None or ts < since or ts > now_dt:
                continue
            cid = str(d.get("commitment_id") or d.get("target_id") or "")
            res = str(d.get("resolution") or "done").lower()
            closed.append({"id": cid,
                           "title": titles.get(cid) or d.get("title") or "",
                           "resolution": "dropped" if res == "dropped" else "done",
                           "ts": ev.get("ts") or ""})
    return {"since": since_iso, "opened": opened, "closed": closed,
            "slipped": slipped, "n_opened": len(opened),
            "n_closed": len(closed), "n_slipped": len(slipped)}


# ---------------------------------------------------------------------------
# render_plate — the one renderer, owns all words
# ---------------------------------------------------------------------------

def _row_line(r: dict, *, verbs: bool) -> str:
    who = f" · {_user(r['counterparty'])}" if r.get("counterparty") else ""
    due = f" · {r['due_phrase']}" if r.get("due_phrase") else ""
    badge = " · OVERDUE" if "overdue" in (r.get("badges") or []) else ""
    chip = f" — {_user(r['chip'])}" if r.get("chip") else ""
    folded = f" · +{r['folded_n']} more like it" if r.get("folded_n") else ""
    sub = ""
    if r.get("subitems"):
        s = r["subitems"]
        sub = f" · steps {s['done']}/{s['done'] + s['open']}"
    reason = ""
    if r.get("reason"):
        # HYGIENE9 (d2) — THE HARD-LEAK FENCE ON THE REASON. A stored reason
        # is marked as writer text so the P4 token gate never refuses a whole
        # render over one old clause (F-2) — which is exactly how a raw score
        # walked onto the plate at v5.27.0. Two shapes are never legitimate
        # in a reason, stored or ours: a score and a wire id. `plain_words`
        # scrubs them by construction; this is the fence that reds if that
        # scrub is removed. It is a renderer bug, not a customer-visible
        # degrade — so it raises, by name.
        leak = carries_hard_leak(r["reason"])
        if leak:
            raise PlateJargonError(
                f"plate row reason carries a {leak} ({r['reason']!r}) — "
                f"reasons are composed by review_reasons.render_reason and "
                f"can never carry one")
        # MF-3 (trial merge 2026-09-14, re-derived after the merged-tree
        # review F-1): a STORED reason (the customer's park / review words,
        # `_assign`'s two stored branches) stays marked as user text so the
        # plate's own token gate never refuses a whole render over one old
        # clause (HYGIENE9 d2) - the legacy signal until the writers stamp
        # `reason_origin: customer` (deferred MF-5, night 11c). A customer-
        # stamped reason is user text by LEAK3's predicate. A reason this
        # module COMPOSED is neither and is scanned in full.
        # MF-11c-7 — the SAFE form (CARD1 review): a customer-stamped reason
        # is user text; a reason with NO stamp is a legacy row written before
        # the stamp existed and keeps the legacy arm (809 historical rows
        # render unchanged, 0 new refusals); a `product` stamp is this
        # module's own words and is scanned in full.
        why = (_user(r["reason"]) if (_customer_typed(r) or not r.get("reason_origin"))
               else r["reason"])
        reason = f" — {why}"
    return f"- {_user(r['title'])}{who}{due}{badge}{folded}{sub}{reason}{chip}"


def wrap_parked_rows(rows: list, cap: int = None) -> tuple:
    """The Parked rows the WRAP lists, and how many it does not
    (PLATENUM1 4.3, M's ruling 1 — cap 10).

    Ranked by `importance_key`: overdue first, then due this week, then a
    client on the other end, then money in its words, then newest. R-N10-1
    holds above the cap — an overdue or due-this-week row can never be
    among the ones left out, because the ranking puts every one of them
    ahead of every row that is not.

    Returns `(rows_to_list, n_not_listed)`.
    """
    limit = WRAP_PARKED_CAP if cap is None else int(cap)
    ranked = sorted(rows, key=importance_key)
    if limit < 0 or len(ranked) <= limit:
        return ranked, 0
    return ranked[:limit], len(ranked) - limit


def wrap_reason_retired(r: dict) -> bool:
    """True when the WRAP may drop this row's reason (PLATENUM1 fix round 1,
    REVIEW F-1a).

    The wrap is prose and NEVER ASKS (M's rulings 2026-09-07, "brief/wrap
    asks out"), so the PRODUCT's own question reasons render as no reason at
    all in a document the reader cannot answer back. But a reason the
    customer TYPED is his own words coming back to him and always survives —
    `reason_origin` says which, and where a row carries no origin only the
    exact `PRODUCT_QUESTION_REASONS` strings are retired. A reason is never
    judged by its shape: "…— whose is it?" written by the reader through the
    `not mine` door stays on the page."""
    reason = str(r.get("reason") or "").strip()
    if not reason:
        return False
    if r.get("reason_origin") == REASON_ORIGIN_CUSTOMER:
        return False
    from commitment_state import QUESTION_BY_USER_VERB as _USER_VERB
    if r.get("question") and r.get("question_by") == _USER_VERB:
        # MF-2: the reader's own `not mine` answer, said back to him - the
        # ACT is his even though the sentence is composed. Never retired.
        return False
    return reason in PRODUCT_QUESTION_REASONS


def _wrap_row_line(r: dict) -> str:
    """A Parked row as the WRAP prints it — the plate's own line, minus a
    reason only the product wrote (`wrap_reason_retired`). The plate still
    asks it, where the row has a verb."""
    if wrap_reason_retired(r):
        r = dict(r)
        r["reason"] = ""
    return _row_line(r, verbs=False)


def _block_text(view: dict, block: str, *, verbs: bool, open_blocks) -> list:
    blk = view["blocks"].get(block) or {"total": 0, "projects": []}
    if blk["total"] == 0:
        return []
    title = f"{BLOCK_TITLES[block]} ({blk['total']}) — {BLOCK_WANTS[block]}"
    if block not in open_blocks:
        phrase = SHOW_MORE_PHRASES.get(block)
        n = blk["total"]
        noun = {"wait": "waiting", "schedule": "to schedule"}.get(block, "more")
        line = f"{n} {noun}"
        if phrase:
            line += f" — say `{phrase}`"
        return [f"## {title}", line, ""]
    out = [f"## {title}"]
    # ONEPLATE1 — the cap's rule, said ONCE, on the block that holds the
    # rows it moved. PLATENUM1 4.3: it now says HOW MANY rows it is about,
    # so it is a fact of this plate rather than the boilerplate that used to
    # be copied onto all 176 of them.
    cap = view.get("cap") or {}
    if block == BLOCK_PARKED and cap.get("parked") and cap.get("limit"):
        out.append(parked_cap_line(cap["parked"], cap["limit"]))
    # CUT-C item 7 — the block's full verb set, one line, only when verbs
    # render and the block carries a one-tap (see BLOCK_VERB_LINES).
    if verbs and block in BLOCK_VERB_LINES:
        out.append(BLOCK_VERB_LINES[block])
    for proj in blk["projects"]:
        out.append(f"### {_user(proj['label'])} ({proj['total']})")
        for hz in proj["horizons"]:
            out.append(f"{hz['label']}:")
            for r in hz["rows"]:
                out.append(_row_line(r, verbs=verbs))
        out.append("")
    return out


def _block_sections(view: dict, block: str, *, verbs: bool, display,
                    skip_ids=None, only_ids=None) -> list:
    """Widget sections for one block: one section per project, rows keyed by
    the commitment's `data.id` VERBATIM (identity contract) with the
    horizon on the context line.

    `skip_ids` (ONEPLATE1) — the rows the widget cap holds back
    (`widget_hidden_ids`). They are not pages and not numbers here; they are
    behind `show parked`. The TEXT plate never passes it.

    `only_ids` (REVIEW_ONEPLATE1 F-3) — the mirror: render EXACTLY these
    rows and nothing else. That is what the `show parked` door is: the same
    plate, the same build, the same row numbers, restricted to the set the
    page held back. (The prose used to tell the reader to rebuild the plate
    at `engaged` instead, which is a DIFFERENT plate with different blocks,
    different counts and no cap — measured on M's book as 317 rows over 36
    pages, the exact surface the ruling called irrelevant.)"""
    blk = view["blocks"].get(block) or {"total": 0, "projects": []}
    skip = set(skip_ids or ())
    only = None if only_ids is None else set(only_ids)
    sections: list = []
    for proj in blk["projects"]:
        items: list = []
        for hz in proj["horizons"]:
            for r in hz["rows"]:
                if r["id"] in skip:
                    continue
                if only is not None and r["id"] not in only:
                    continue
                parts = [hz["label"]]
                if r.get("due_phrase"):
                    parts.append(r["due_phrase"])
                if "overdue" in (r.get("badges") or []):
                    parts.append("OVERDUE")
                if r.get("counterparty"):
                    parts.append(r["counterparty"])
                if r.get("folded_n"):
                    parts.append(f"+{r['folded_n']} more like it")
                if r.get("subitems"):
                    s = r["subitems"]
                    parts.append(f"steps {s['done']}/{s['done'] + s['open']}")
                if r.get("reason"):
                    parts.append(r["reason"])
                elif r.get("held_reason"):
                    # REVIEW_ONEPLATE1 F-4 — a row the budget cut says why
                    # it was cut, in the same words a parked row uses.
                    parts.append(r["held_reason"])
                if r.get("chip"):
                    parts.append(r["chip"])
                item = {
                    "n": r["id"],
                    # R-N10-1 — the row's OWN number (`assign_display_numbers`),
                    # not this loop's position. The running counter stays as
                    # the fallback for a view built before the stamp existed.
                    "display_n": r.get("display_n") or display["n"],
                    "name": r["title"],
                    "context_tag": " · ".join(parts),
                    "actions": ([] if not verbs else
                                list(CONFIRM_VERBS if block == BLOCK_CONFIRM
                                     else PLATE_VERBS)
                                + ([BLOCK_ONE_TAPS[block]]
                                   if verbs and block in BLOCK_ONE_TAPS
                                   else [])),
                    "block": block,
                    "horizon": r["horizon"],
                    # F-1: the dispatcher's branch — a guess confirms+closes
                    # through done_items; every other row closes directly.
                    "pending": bool(r.get("pending")),
                }
                display["n"] += 1
                if r.get("folded_ids"):
                    item["folded_ids"] = list(r["folded_ids"])
                if verbs and block == BLOCK_CONFIRM:
                    item["reduced_verbs_reason"] = CONFIRM_REDUCED_REASON
                items.append(item)
        if items:
            sections.append({
                "title": f"{BLOCK_TITLES[block]} · {proj['label']}",
                "count": len(items),
                "lane": block,
                "items": items,
            })
    return sections


SCOPE_ALL = "all"
SCOPE_WOULD_HOLD = "would_hold"


def render_plate(view: dict, surface: str = "plate", verbs: bool = True,
                 *, source_skill: str = "commitment-triage",
                 scope: str = SCOPE_ALL,
                 exclude_ids=None, ask_lines: Optional[dict] = None) -> dict:
    """Render a `build_plate` view for ONE surface. Owns every word (DD-2).

    surface   "brief" — DO IT + CHASE, top BRIEF_CAP rows by horizon, ONE
                        attention number + one pointer (NUMBERS1 R-1; no
                        block totals here, P4).
              "plate" — the full model, block totals in the header (D9),
                        paged by block on the widget.
              "eod" / "wrap" — THE DELTA CUT (night 2): what the window
                        did to the plate, from `view["delta"]` (build with
                        `since_iso`): opened / closed / slipped in the
                        plate's shape, capped per section with the cap
                        stating its denominator, then ONE pointer. `wrap`
                        adds the PARKED review — every resting row, open,
                        with its reason (P2: never hidden). Neither carries
                        verbs (the wrap is prose; the evening asks nothing —
                        EODSYNTH1 R-3). A view built without a window
                        renders the one-line "no window" form.
              "board" — the full model, read-only (never verbs).
    verbs     False renders no verb strip and no reduced-verb line. P5:
              MANDATORY False for a would_hold scope (display-only) and
              for `board`.
    exclude_ids   brief only — rows the brief's own gates dropped this fire
              (Bug #93 class: an item the CEO already handled by mail or on
              the calendar must not be told to them as "do it"). The
              attention NUMBER still counts them — it is the plate's number
              and reads the same on every surface (F-56); only the printed
              rows skip them.
    ask_lines     brief only — `{id: ask line}` from the fatigue rule
              (OVERDUE1 / EODSYNTH1 R-3): a row that carries one prints it
              as its label, verbatim, instead of the plain row line.

    Returns {"text": <markdown>, "data_view": <widget data view or None>,
             "attention": n, "block_totals": {...}}; the brief adds
             `"rows"` ([{id, block, verb, line, ask_line}] in print order)
             and `"pointer"`; the delta cuts add `"delta"` (the counts).
    Raises PlateJargonError if the renderer's own words carry a banned
    token. A refused view (`view["error"]`) renders as ONE plain line and
    no lanes.
    """
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of {SURFACES}; got {surface!r}")
    if scope == SCOPE_WOULD_HOLD and verbs:
        # P5 — HELDREVIEW1 DD-4 / CLUSTER1: the "what would be hidden" view
        # is display-only. A reading chair that could resolve a row makes
        # reading destructive; refuse loudly rather than render a verb.
        raise ValueError(
            "render_plate: scope='would_hold' is display-only — pass "
            "verbs=False (HELDREVIEW1 DD-4)")
    if surface == "board":
        verbs = False
    if not view.get("ok"):
        line = view.get("error") or "Nothing to show."
        return {"text": line, "data_view": None, "attention": 0,
                "block_totals": {}, "refused": True}

    # PLATENUM1 4.1 — ONE PROJECTION. Every count-class integer this render
    # states comes from `plate_numbers`, and from nowhere else. The attended
    # test read 60 / 58 / 46 on the brief and End of Day against 334 / 289
    # on the plate for one book on one day (B2.5), because the brief's
    # headline added two block totals here, the text header read
    # `counts["total"]` raw, the wrap's head read the delta, and the board
    # read the projection — four surfaces, four arithmetics, one book.
    nums = plate_numbers(view)
    totals = nums["blocks"]
    attention = nums["attention"]
    lines: list = []
    data_view = None

    brief_rows: list = []
    pointer = ""
    #: The brief's line-two breakdown, carried out as a key (REVIEW F-6).
    brief_breakdown = ""
    if surface == "brief":
        n_more = 0
        skip = {str(i) for i in (exclude_ids or [])}
        asks = ask_lines or {}
        # NUMBER1 3.1 (R-26) — the PROJECTION leads; `attention` breaks it
        # down on line two. Nothing here re-derives a count: both integers
        # come off `plate_numbers` above.
        open_n = nums["open"]
        if open_n:
            lines.append(f"{open_n} on your plate today")
            # FIX ROUND 1 (REVIEW F-6) -- THE COMPOSER HANDS THE BREAKDOWN
            # OUT AS A KEY. `surface_drivers._brief_plate_block` used to
            # recover this line by re-parsing the composed text by index
            # ("line two, unless it starts with `- `"), which is a second
            # reader of a string this function already has in a variable:
            # one row line that ever fails to start with "- " and the pack
            # prints a row where the breakdown belongs.
            brief_breakdown = (
                BRIEF_BREAKDOWN_TEMPLATE if attention
                else BRIEF_BREAKDOWN_NONE).format(open=open_n,
                                                  attention=attention)
            lines.append(brief_breakdown)
        else:
            lines.append(BRIEF_EMPTY_LINE)
        shown = 0
        for block in (BLOCK_DO_IT, BLOCK_CHASE):
            blk = view["blocks"].get(block) or {"projects": []}
            for proj in blk["projects"]:
                for hz in proj["horizons"]:
                    for r in hz["rows"]:
                        if r["id"] in skip:
                            continue
                        if shown < BRIEF_CAP:
                            verb = BRIEF_ROW_VERBS[block]
                            ask = asks.get(r["id"]) or ""
                            body = (_user(ask) if ask
                                    else _row_line(r, verbs=False)[2:])
                            line = f"- {verb}: {body}"
                            lines.append(line)
                            brief_rows.append({
                                "id": r["id"], "block": block, "verb": verb,
                                "line": _strip_user_marks(line),
                                "ask_line": ask or None,
                                "title": r["title"],
                                "counterparty": r.get("counterparty") or "",
                                "due": r.get("due") or "",
                                "pending": bool(r.get("pending")),
                            })
                            shown += 1
                        else:
                            n_more += 1 + r["folded_n"]
        if n_more:
            pointer = f"…and {n_more} more — {BRIEF_POINTER}."
            lines.append(pointer)
        text = "\n".join(lines)
    elif surface in ("eod", "wrap"):
        text = _delta_text(view, surface)
    else:
        header = f"What's on your plate — {nums['open']} open"
        if surface in ("plate", "board"):
            header += (f" · DO IT {totals.get(BLOCK_DO_IT, 0)} · CHASE "
                       f"{totals.get(BLOCK_CHASE, 0)} · WAIT "
                       f"{totals.get(BLOCK_WAIT, 0)}")
        lines.append(f"# {header}")
        lines.append("")
        open_blocks = set(OPEN_BY_DEFAULT)
        if surface in ("board", "wrap", "eod"):
            open_blocks = set(BLOCK_ORDER)
        for block in BLOCK_ORDER:
            lines.extend(_block_text(view, block, verbs=verbs,
                                     open_blocks=open_blocks))
        if verbs and view["rows"]:
            lines.append("Verbs on every row: Done · Later… · Drop · Not mine.")
            for block in BLOCK_ORDER:
                if block in BLOCK_ONE_TAPS and totals.get(block, 0):
                    lines.append(ONE_TAP_LINES[block])
        text = "\n".join(lines).rstrip() + "\n"

        sections: list = []
        display = {"n": 1}
        # ONEPLATE1 item 2 — THE WIDGET HONOURS THE CAP. The text above is
        # unchanged (every block, every row, every reason — P2); the widget
        # renders the capped set and points at the door for the rest.
        # `board` is the artifact surface and keeps everything (add beside,
        # never displace).
        hidden = widget_hidden_ids(view) if surface == "plate" else set()
        for block in BLOCK_ORDER:
            sections.extend(_block_sections(view, block, verbs=verbs,
                                            display=display,
                                            skip_ids=hidden))
        counters = []
        if surface in ("plate", "board"):
            counters = [
                {"label": "Do it", "value": totals.get(BLOCK_DO_IT, 0)},
                {"label": "Chase", "value": totals.get(BLOCK_CHASE, 0)},
                {"label": "Wait", "value": totals.get(BLOCK_WAIT, 0)},
            ]
        data_view = {
            "source_skill": source_skill,
            "surface": "commitments",
            "header": header,
            "sections": sections,
            "block_totals": dict(totals),
            "plate_counts": dict(view.get("counts") or {}),
        }
        if counters:
            data_view["counters"] = counters
        # NUMBER1 3.3 — THE PAGE SET, MEASURED. `on_page` used to be
        # "rows of this block that the cap did not hide", which is the
        # whole widget across ALL its pages: on 2026-09-16 that printed
        # "6 of 18 waiting on this page" over a page of nine CONFIRM and
        # DO IT rows and no WAIT row at all (B2.5). The transport
        # paginates `sections` in this order, so the page is the first
        # `measured_page_rows` items of that flattened list — the same
        # measurement `plate_numbers` already uses for `widget_pages`,
        # never an assumed nine.
        page_ids = set()
        if data_view is not None:
            budget = measured_page_rows(data_view)
            flat = [str(it.get("n")) for sec in (data_view.get("sections") or [])
                    for it in (sec.get("items") or [])]
            page_ids = set(flat[:max(0, int(budget))])
        folded_lines = []
        for block in BLOCK_ORDER:
            if block in open_blocks:
                continue
            n = totals.get(block, 0)
            if not n:
                continue
            phrase = SHOW_MORE_PHRASES.get(block)
            noun = {"wait": "waiting",
                    "schedule": "to schedule"}.get(block, "more")
            # REVIEW_ONEPLATE1 F-6 — THE HEADER AND THE RENDERED SET AGREE.
            # This clause used to state the block TOTAL and point at the
            # block's phrase, on a widget that was already showing some of
            # those rows: the header said WAIT 17, five were on the page,
            # twelve were behind a different phrase, and the line said "17
            # waiting — say `show waiting`". The total is still the plate's
            # number (one projection); the clause now says how many of it
            # the reader is looking at.
            # NUMBER1 3.3 — counted against the RENDERED page set, in
            # items (the fold badge on the row discloses its twin), so the
            # clause names a population the reader is looking at.
            on_page = plate_item_count(
                [r for r in (view.get("rows") or [])
                 if r.get("block") == block and r["id"] not in hidden
                 and str(r["id"]) in page_ids])
            if on_page:
                # PLATENUM1 4.1 — ONE POPULATION, NAMED. "10 waiting — 2 on
                # this page" reads as two facts about two sets and the
                # reader has to guess whether the 2 are inside the 10
                # (B2.5, on a page of nine CONFIRM rows). One sentence, one
                # population, the relationship spelled out.
                folded_lines.append(
                    f"{on_page} of {n} {noun} on this page")
            else:
                folded_lines.append(f"{n} {noun}"
                                    + (f" — say `{phrase}`" if phrase else ""))
        if hidden:
            # ONEPLATE1 item 2 / REVIEW_ONEPLATE1 F-4 — THE DOOR, NAMING
            # WHAT IS BEHIND IT. Nothing the page holds back is lost: it is
            # one phrase away, and the phrase now says which of it is
            # resting and which the budget cut.
            folded_lines.append(show_parked_clause(view))
        if folded_lines:
            data_view["quick_read"] = "; ".join(folded_lines) + "."

    # P4 — the gate, on the renderer's words (user spans blanked).
    scan_plate_words(text)
    # …and the ask fence (M's rulings 2026-09-03, REVIEW_PLATE1_N2 F-2): no
    # cut may say a proposal is waiting on the reader.
    #
    # THIS IS THE SECOND PASS OVER THE SAME STRING, deliberately and
    # honestly labelled (re-verify F-2d corrected an earlier claim here):
    # the fence's load-bearing call site is INSIDE `scan_plate_words`, which
    # every path already reaches — this text, and the widget's composed
    # strings through `_scan_data_view`. So removing either site alone
    # changes no verdict. It stays because it is the call a reader of
    # `render_plate` can see, and because a future refactor that stops
    # routing rendered text through `scan_plate_words` would otherwise take
    # the fence with it silently.
    scan_no_pending_question(text)
    if data_view is not None:
        _scan_data_view(data_view)
    out = {"text": _strip_user_marks(text), "data_view": data_view,
           "attention": attention, "block_totals": dict(totals),
           # WRAPSAVE1 / REVIEW_ONEPLATE1 F-1 — provenance, carried out with
           # the text so the document gate can stop reading the customer's
           # own words the way the chat gate already does.
           "user_spans": user_authored_spans(text)}
    if surface == "brief":
        out["rows"] = brief_rows
        out["pointer"] = pointer
        # REVIEW F-6 — the breakdown the composer wrote, not a re-parse of
        # the text it wrote it into. "" on the empty plate, which keeps its
        # own one-line form.
        out["breakdown"] = brief_breakdown
    elif surface in ("eod", "wrap"):
        d = view.get("delta") or {}
        out["delta"] = {k: d.get(k, 0) for k in ("n_opened", "n_closed",
                                                 "n_slipped")}
        out["delta"]["since"] = d.get("since")
    return out


def _delta_text(view: dict, surface: str) -> str:
    """The delta cut (D7 `eod` / `wrap`) — the plate's words for what the
    window did. Every row line is the plate's own `_row_line`, prefixed with
    its block so a reader who knows the plate reads the same shape here."""
    word = WINDOW_WORDS[surface]
    d = view.get("delta")
    nums = plate_numbers(view)
    attention = nums["attention"]
    lines: list = []
    if not d or d.get("window_unreadable"):
        lines.append(f"No window to read {word}'s plate against — "
                     f"{DELTA_POINTER}.")
        return "\n".join(lines) + "\n"
    n_o, n_c, n_s = d["n_opened"], d["n_closed"], d["n_slipped"]
    if surface == "wrap":
        # PLATENUM1 4.4 — ONE in/out pair on the recap. The 09-11 wrap
        # carried two: this head said "195 opened, 661 closed" over the
        # plate's window while `flow_measure.wrap_line` said "823 came in,
        # 776 went out" over the ledger's, and the reader was handed two
        # contradictory accounts of one week (B2.8). The ledger's pair is
        # the one that survives — it counts the book, it states its window
        # in words, and its `open` comes from this same projection. This
        # head keeps the window and drops the counts; the sections below
        # still name what opened, closed and slipped, each with its rows.
        head = f"# Your plate {word}"
    else:
        head = (f"# Your plate {word} — {n_o} opened · {n_c} closed · "
                f"{n_s} slipped")
    lines.append(head)
    lines.append("")
    if not (n_o or n_c or n_s):
        lines.append(f"Nothing moved on your plate {word}.")
    def _section(title, rows, fmt):
        if not rows:
            return
        lines.append(f"## {title} ({len(rows)})")
        for r in rows[:DELTA_CAP]:
            lines.append(fmt(r))
        if len(rows) > DELTA_CAP:
            lines.append(f"…and {len(rows) - DELTA_CAP} more of the {len(rows)}")
        lines.append("")
    _section(f"Opened {word}", d["opened"],
             lambda r: f"- {BLOCK_TITLES[r['block']]} · " + _row_line(r, verbs=False)[2:])
    _section(f"Closed {word}", d["closed"],
             lambda c: f"- {_user(c['title'] or 'an item')} — "
                       + ("dropped" if c["resolution"] == "dropped" else "done"))
    _section(f"Slipped {word} — the date passed, still open", d["slipped"],
             lambda r: f"- {BLOCK_TITLES[r['block']]} · " + _row_line(r, verbs=False)[2:])
    if surface == "wrap":
        blk = view["blocks"].get(BLOCK_PARKED) or {"total": 0, "projects": []}
        # REVIEW_QUIET1 F-3 — a cap-parked row is listed ONCE: the first
        # wrap after it crossed the cap. Rows the cap had already parked at
        # the window's start are not re-listed; since WRAPSTAFF1 4.2 they are
        # carried by the block total and the pointer, not by a count of
        # their own.
        def _listed(r):
            return not (r.get("parked_by_cap") and not r.get("parked_by_cap_new", True))
        # WRAPSTAFF1 4.2 — ONE PARKED INTEGER ON THE PAGE.
        #
        # The 09-15 wrap carried four (ATTENDED_TEST_v5.31.0 B2.4: 119 / 89 /
        # 93 beside the plate's 207), because the heading counted the rows
        # this wrap MAY list, the pointer counted the remainder of THAT, and
        # a second line counted the rows the cap had already parked before
        # the window opened. Three populations, three integers, no reader can
        # add them up.
        #
        # The heading now states the block's own total — the same number
        # `plate_numbers` hands every other surface — and the pointer states
        # the remainder of exactly that number, so `shown + remainder` is an
        # identity the suite pins. The F-3 "listed once" rule is unchanged
        # and still governs WHICH rows may be listed; it no longer prints a
        # count of its own.
        #
        # PLATENUM1 4.3's retired cap boilerplate ("…fill the plate up to
        # {cap} at a time") is gone with it. The rule the reader needs is
        # the one that decides whether a row is here at all, and since R-23
        # that rule is "overdue and this-week items stay up".
        n_parked_total = int(nums["blocks"].get(BLOCK_PARKED, 0) or 0)
        if n_parked_total:
            # PLATENUM1 4.3 — THE CAP. `listed` is every row this wrap may
            # list; the reader sees the `WRAP_PARKED_CAP` most important of
            # them (`importance_key`) and one pointer for the rest.
            listed = [r for proj in blk["projects"] for hz in proj["horizons"]
                      for r in hz["rows"] if _listed(r)]
            shown, _n_over = wrap_parked_rows(listed)
            shown_ids = {r["id"] for r in shown}
            if shown:
                lines.append(f"## PARKED ({n_parked_total}) — each one rests "
                             "with its reason; overdue and this-week items "
                             "stay up on the board")
                for proj in blk["projects"]:
                    rows_p = [r for hz in proj["horizons"] for r in hz["rows"]
                              if r["id"] in shown_ids]
                    if not rows_p:
                        continue
                    lines.append(f"### {_user(proj['label'])} ({len(rows_p)})")
                    for r in rows_p:
                        lines.append(_wrap_row_line(r))
                remainder = n_parked_total - len(shown)
                if remainder > 0:
                    lines.append(f"…and {remainder} more resting — say "
                                 f"`{SHOW_PARKED_PHRASE}` for the rest.")
            else:
                # Nothing new to list this week — one line, one integer, and
                # the door to the rest.
                lines.append(
                    f"{n_parked_total} "
                    f"{'item rests' if n_parked_total == 1 else 'items rest'} "
                    "under Parked — overdue and this-week items stay up on "
                    f"the board. Say `{SHOW_PARKED_PHRASE}` to see them.")
            lines.append("")
    # NUMBER1 3.1 (R-26) — the day-close and the wrap state the SAME
    # integer the brief and the board state. This one line sits inside
    # WRAPSTAFF1's region of this file and is routed to NUMBER1 by the
    # night-11d ownership table; nothing else in `_delta_text` is ours.
    lines.append(f"{nums['open']} on your plate now — {DELTA_POINTER}.")
    return "\n".join(lines).rstrip() + "\n"


def _scan_data_view(dv: dict) -> None:
    """The widget's renderer-authored strings pass the same gate. Row names
    are user text (blanked); context lines carry names and chips, which are
    user/writer text — so the scan covers the section titles, the header,
    the quick-read, the counters and the reduced-verb line: every string
    this module composed."""
    # WRAP2 4.2 item 8 — `sub_header` is renderer-authored text on the board
    # (the CAP-F7 breakdown line), so it goes through the same gate as the
    # header above it. A composed line the scan cannot see is the leak class
    # this scan exists for.
    parts = [dv.get("header") or "", dv.get("sub_header") or "",
             dv.get("quick_read") or ""]
    for c in dv.get("counters") or []:
        parts.append(str(c.get("label")))
    for s in dv.get("sections") or []:
        parts.append(str(s.get("title") or ""))
        for it in s.get("items") or []:
            parts.append(str(it.get("reduced_verbs_reason") or ""))
            # HYGIENE9 (d2) — the WHOLE context tag (names, due phrase, chips
            # and the reason together) passes the hard-leak scan: a score or
            # a wire id is never legitimate in any of those parts.
            leak = carries_hard_leak(it.get("context_tag") or "")
            if leak:
                raise PlateJargonError(
                    f"plate widget row carries a {leak} in its context line "
                    f"({str(it.get('context_tag'))!r})")
            for part in str(it.get("context_tag") or "").split(" · "):
                # Horizon labels, badges, reasons and the folded line are
                # ours; names, due phrases and chips are not composed here.
                if part in HORIZON_TITLES.values() \
                        or part in BLOCK_TITLES.values() \
                        or part == "OVERDUE" \
                        or part.startswith("+") or part.endswith("whose is this?") \
                        or part.startswith("no movement") or part.startswith("quiet ") \
                        or part.startswith("no owner") or part == "is this real?":
                    parts.append(part)
    scan_plate_words("\n".join(parts))


# ---------------------------------------------------------------------------
# The widget path — ONE call for the surface driver
# ---------------------------------------------------------------------------

# REVIEW_CUTC_2026-09-06 F-3 — the sources whose over-budget page gets the text
# form below. `page_text_fallback` runs the PLATE's banned-word scan
# (`scan_plate_words`), and only the plate's own header and rows are written
# to pass it: the needs-your-call header ("N unconfirmed extractions") carries
# a banned word by design (night-10 vocabulary item), so composing the form
# for every paginated surface turned an over-budget queue page into a
# PlateJargonError where v5.28.0 returned it flagged `over_budget`. The
# transport composes the text form for these sources and none other.
TEXT_FALLBACK_SOURCES = frozenset({"commitment-triage"})


def page_text_fallback(page_view: dict) -> str:
    """CUT-C item 7 — THE text form of ONE FITTED PAGE, for a page the widget
    byte budget refuses (`pagination.over_budget`). Numbered by the SAME
    `display_n` the persisted page carries, so `follow-up call 265` typed in
    chat dispatches by number against the persisted page exactly as a click
    would; every row names every verb it takes, by DISPLAY label
    (`verb_taxonomy.DISPLAY_LABELS`), including the block one-tap. Wire ids
    never render. The transport composes it (`widget_transport.
    render_and_persist` returns it as `text` when over budget); the skill
    relays it byte-exact and never hand-assembles a page from pieces."""
    from verb_taxonomy import DISPLAY_LABELS

    lines: list = []
    header = page_view.get("header") or ""
    if header:
        lines.append(f"# {_strip_user_marks(str(header))}")
    pg = page_view.get("pagination") or {}
    if pg.get("total_pages"):
        lines.append(f"Page {pg.get('page')} of {pg.get('total_pages')} — "
                     f"delivered as text (this page is too large for the "
                     f"widget); answer by number.")
    for section in page_view.get("sections") or []:
        title = section.get("title")
        if title:
            lines.append("")
            cnt = section.get("count")
            lines.append(f"## {title}" + (f" ({cnt})" if cnt is not None else ""))
        for item in section.get("items") or []:
            n = item.get("display_n")
            if n in (None, ""):
                n = "•"
            name = str(item.get("name") or item.get("subject") or "(untitled)")
            tag = item.get("context_tag") or ""
            line = f"{n}. {name}"
            if tag:
                line += f" — {tag}"
            lines.append(line)
            verbs = [DISPLAY_LABELS.get(a, a) for a in (item.get("actions") or [])]
            if verbs:
                lines.append("   " + " · ".join(verbs)
                             + (f" — say `<verb> {n}`" if n != "•" else ""))
    text = "\n".join(lines).rstrip() + "\n"
    scan_plate_words(text)
    return text


PARKED_HEADING = "Behind your plate"


def render_parked(view: dict, *, verbs: bool = True,
                  source_skill: str = "commitment-triage") -> dict:
    """`show parked` — EXACTLY the rows the plate's page held back, and
    nothing else (REVIEW_ONEPLATE1 F-3).

    THE DOOR IS THE SAME PLATE. Same build, same blocks, same row numbers,
    restricted to `widget_hidden_ids`. What it replaces is prose that told
    the reader to pass `preset="engaged"` to `build_plate`: that rebuilds a
    DIFFERENT plate — different block membership, different counts, no cap
    at all — and lands the reader on the 317-row, 36-page surface M ruled
    irrelevant. Measured, not argued (REVIEW_ONEPLATE1 §12 F-3).

    Every row here says why it is here: a resting row carries its own
    reason, and a row the budget cut carries `held_reason` in the same
    words. The header says which is which.
    """
    if not view.get("ok"):
        line = view.get("error") or "Nothing to show."
        return {"source_skill": source_skill, "surface": "commitments",
                "widget_mode": "all_clear_summary", "header": line,
                "refused": True}
    held = widget_hidden_ids(view)
    n = held_back_counts(view)
    what = []
    if n["resting"]:
        what.append(f"{n['resting']} {HELD_NOUNS[HELD_RESTING]}")
    if n["further"]:
        what.append(f"{n['further']} {HELD_NOUNS[HELD_FURTHER]}")
    header = f"{PARKED_HEADING} — " + (" · ".join(what) if what
                                       else "nothing is held back")
    # REVIEW F-4 — THE HEADER SAYS WHICH POPULATION IT COUNTS. These two
    # numbers are ITEM counts (NUMBER1 3.2 moved `held_back_counts` to
    # items so the board's footer would sum to its item header), and this
    # page renders ROWS: a cluster-fold survivor is one row standing for
    # itself and its twin. When the two differ, the header says so in the
    # reader's own words instead of leaving them to count the page and find
    # it short. Silent on every book with no fold behind the door, which is
    # most of them.
    n_rows = len(held)
    extra = n["total"] - n_rows
    if what and extra > 0:
        header += (f" · {extra} of these {'is' if extra == 1 else 'are'} "
                   f"folded into a row here")
    sections: list = []
    display = {"n": 1}
    for block in BLOCK_ORDER:
        sections.extend(_block_sections(view, block, verbs=verbs,
                                        display=display, only_ids=held))
    dv = {
        "source_skill": source_skill,
        "surface": "commitments",
        "header": header,
        "sections": sections,
        "block_totals": dict(view.get("block_totals") or {}),
        "plate_counts": dict(view.get("counts") or {}),
        "held": dict(n),
    }
    if held:
        dv["quick_read"] = (f"Everything here is off your page. Say "
                            f"`{WORK_MY_PLATE_PHRASE}` for the page itself.")
    _scan_data_view(dv)
    return dv


def parked_data_view(workspace_root, *, now_iso: Optional[str] = None,
                     user_person_id: Optional[str] = None,
                     source_skill: str = "commitment-triage") -> dict:
    """`build_plate` -> `render_parked` -> the widget data view. The `show
    parked` door, end to end, on ONE build of the same plate."""
    view = build_plate(workspace_root, user_person_id=user_person_id,
                       now_iso=now_iso)
    return render_parked(view, source_skill=source_skill)


def plate_data_view(workspace_root, *, now_iso: Optional[str] = None,
                    user_person_id: Optional[str] = None,
                    source_skill: str = "commitment-triage") -> dict:
    """`build_plate` -> `render_plate("plate", verbs=True)` -> the widget data
    view `widget_transport.render_and_persist` takes. A refused plate (no
    primary user) comes back as the one-line empty-state view, no rows, no
    verbs — never lanes."""
    view = build_plate(workspace_root, user_person_id=user_person_id,
                       now_iso=now_iso)
    rendered = render_plate(view, "plate", True, source_skill=source_skill)
    if rendered.get("refused") or rendered["data_view"] is None:
        # The empty-state mode's own vocabulary (header only): no rows, no
        # verbs, no lanes — the line IS the surface.
        return {
            "source_skill": source_skill,
            "surface": "commitments",
            "widget_mode": "all_clear_summary",
            "header": rendered["text"],
        }
    dv = rendered["data_view"]
    dv["plate_text"] = rendered["text"]
    return dv


# ---------------------------------------------------------------------------
# R-N10-1 — THE BOARD IS THE DEFAULT PLATE RENDER
#
# M, 2026-09-07 ~22:15 PT: "the default plate render is the board, grouped by
# project (or by person where the client's brief is organized that way), one
# line per row, one tap for Done or no buttons, as many rows as the widget
# holds; typed verbs by number for everything else. The nine-row page with
# full buttons stays behind 'work my plate'. Forty is the text cap only."
#
# What was there before: one page of nine fat rows — each row a card with
# four or five verb buttons and a date field, ~3KB rendered — and 34 to 36
# such pages on a real book. The reader's cost was pages, and the pages were
# expensive because the ROW was expensive. The board inverts that: a row is
# one line and at most one tap, so the same byte budget holds several times
# as many rows, and the whole plate is one look instead of a page-turning
# exercise. Everything a row could ask for beyond Done is typed by number —
# and the number is the row's own (`assign_display_numbers`), so it is the
# same number the working page and the text form show.
# ---------------------------------------------------------------------------

BOARD_GROUP_PROJECT = "project"
BOARD_GROUP_PERSON = "person"
BOARD_GROUPINGS = (BOARD_GROUP_PROJECT, BOARD_GROUP_PERSON)

#: The group a row falls in when the board is organised by person and the row
#: names nobody. The "No project" idiom, one noun over.
NO_PERSON_LABEL = "No one named"

#: The door to the nine-row page with the full button set (it did not go
#: away — it stopped being the default).
WORK_MY_PLATE_PHRASE = "work my plate"

#: What the board falls back to when the byte fit cannot be run at all (a
#: renderer that will not import). Small on purpose: a short board that
#: names the door is a surface; a board that raises is not.
BOARD_ROW_FLOOR = 12

#: The board's own one tap. Every other verb is typed by number.
BOARD_VERBS = (VERB_DONE,)

#: THE NUMBERS READ BADLY, AND THE MECHANISM IS RIGHT (REVIEW_ONEPLATE1
#: ruling 1, 2026-09-08). On M's own book the board's twenty-two rows show
#: 83, 82, 81, 78, 104, 105, 56 … — a first group that counts DOWN, a
#: lowest number of 54, and no 1 through 53 anywhere. That is not a defect
#: in the numbering; it is the numbering being honest. The number is the
#: ROW's, stamped once in the text plate's order, so the same row answers
#: to the same number on every surface. Counting positions instead would
#: renumber a row every time the board regrouped, and a typed `drop 12`
#: would then close whichever row happened to land twelfth.
#:
#: What can be fixed is the READING. A bare "83." at the top of a list
#: looks like a position and invites the question "where are 1 to 82?".
#: A "#" says the number is a label, and one line says where the label
#: comes from. Both are legibility only — no guarantee moves, the wire id
#: stays in `n`, and the number on the row stays the integer every other
#: surface compares against.
BOARD_NUMBER_PREFIX = "#"

#: Said ONCE under the board, never on a row. REVIEW_ONEPLATE1 N-8
#: (2026-09-08, fix round 3): the sentence this replaced pointed the reader
#: at "the text plate" as the place to check a number against — but no text
#: surface prints a row number, in any mode, so the claim was true and
#: uncheckable, and "text plate" is the only place in the whole tree that
#: phrase was ever spoken to a customer. This version drops the pointer to
#: an unprintable surface and keeps only the claim a reader can act on: the
#: number is the item's own, not a position. Deliberately names no verb —
#: the board's own copy already says "one tap on a row (Done) and nothing
#: else", so a line naming `later`/`drop` beside `done` would claim the
#: typed path for a verb the board only ever offers as a tap. Passes
#: `scan_plate_words` (no banned token); measured at 66 bytes against the
#: retired sentence's 87 — it costs nothing extra on M's board (see N-9).
BOARD_NUMBER_NOTE = ("Each number belongs to the item, not to where it "
                     "sits in this list")


def board_display_n(row: dict):
    """The row's number as the BOARD shows it: the same integer every other
    surface shows, wearing a `#` so it reads as a label and not as a place
    in a queue (REVIEW_ONEPLATE1 ruling 1). Unstamped rows are left alone."""
    n = row.get("display_n")
    return f"{BOARD_NUMBER_PREFIX}{n}" if n not in (None, "") else n


def _board_header_text(nums: dict) -> str:
    """The board's header line, off `plate_numbers(view)`. ONE composer —
    `render_board` and `board_fixed_text_bytes` both call this, so the
    byte-budget pin can never drift from what actually renders."""
    return (f"What's on your plate — {nums['open']} open"
            f" · DO IT {nums['blocks'].get(BLOCK_DO_IT, 0)}"
            f" · CHASE {nums['blocks'].get(BLOCK_CHASE, 0)}"
            f" · WAIT {nums['blocks'].get(BLOCK_WAIT, 0)}")


#: WRAP2 4.2 item 8 / CAP-F7 — THE ONE READER OF `open_confirmed`.
#:
#: PLATENUM1 fix round 1 gave `plate_numbers` three names for two
#: populations — `open` (the rows this plate renders), `unconfirmed` (the
#: extractions nobody has agreed to yet) and `open_confirmed` (the book the
#: reader is actually carrying). Two of the three render somewhere. The
#: third had NO reader at all: `NUMBER_KEYS` carried it, `plate_numbers`
#: computed it, and nothing on any surface said it out loud. So the number
#: M had read for months (333 on his own book) simply vanished from every
#: surface on the day the header moved to 335, with nothing anywhere saying
#: why — which is the same defect the projection was built to end, arriving
#: from the other side.
#:
#: It renders ONCE, here, under the board's header line, as a BREAKDOWN of
#: the number that header already states — never as a second headline
#: (amendment B-1 allows exactly this shape and no other: a breakdown that
#: SUMS to the one stated total). The morning brief does not carry it;
#: `surface_drivers.assert_single_open_count` stays green over the brief
#: because nothing about the brief changes.
#:
#: Drop-empty by construction. On a book with nothing unconfirmed the three
#: numbers are "N open · N confirmed · 0 awaiting your word" — a breakdown
#: that breaks nothing down, three numbers where one would do. There is no
#: second population to name, so the line does not render.
BOARD_BREAKDOWN_AWAITING = "awaiting your word"
BOARD_BREAKDOWN_TEMPLATE = ("{open} open · {confirmed} confirmed · "
                            "{unconfirmed} " + BOARD_BREAKDOWN_AWAITING)


def board_breakdown_line(nums: dict) -> str:
    """The board's breakdown line — "335 open · 333 confirmed · 2 awaiting
    your word" — or `""` when there is no second population to name.

    The three integers SUM by construction (`open - unconfirmed ==
    open_confirmed` is the identity `plate_numbers` maintains and
    `run_platenum1_test` §7 pins), and this composer re-derives the middle
    term from the other two rather than printing all three off the map: a
    breakdown whose own arithmetic does not close is worse than no
    breakdown, and deriving it here means the line can never disagree with
    the header above it even if a future caller hands in a hand-built dict.
    """
    try:
        n_open = int(nums.get("open") or 0)
        n_unconfirmed = int(nums.get("unconfirmed") or 0)
    except (TypeError, ValueError):
        return ""
    if n_unconfirmed <= 0:
        return ""
    return BOARD_BREAKDOWN_TEMPLATE.format(
        open=n_open, confirmed=n_open - n_unconfirmed,
        unconfirmed=n_unconfirmed)


#: REVIEW_ONEPLATE1 N-9 (fix round 3): the board's header and its one line
#: underneath (the doors clause plus BOARD_NUMBER_NOTE) cost the same
#: handful of bytes on every render, whatever the row count — and that
#: cost comes straight off the row budget, because it is measured in the
#: same relay. On M's own book this text is already the entire margin
#: between a board that fits and one that would lose its 23rd row: 121
#: bytes of slack out of 40,000, and the round-2 fix (the `#` prefixes plus
#: BOARD_NUMBER_NOTE) is most of why that margin is thin. A future clause
#: added to `board_quick_read` or the header — one more block count, one
#: more sentence — would spend the rest of it with nothing here to notice.
#: This budget is deliberately NOT "whatever it costs today": it is
#: today's fixed cost on the suite's own fixture (230 bytes, after the N-8
#: reword) plus about one short clause's worth of headroom (50 bytes), so a
#: change that stays small still passes and a change that is not small is
#: caught before it ships, not after M loses a row.
#:
#: WRAP2 4.2 item 8 raised it by exactly what the CAP-F7 breakdown line
#: costs and NOT ONE BYTE MORE. Measured on this suite's own fixture: the
#: fixed text was 230 bytes with 50 bytes of headroom; `board_breakdown_line`
#: is 49 bytes on a three-integer book ("165 open · 163 confirmed · 2
#: awaiting your word"), which took the measurement to 279 and the headroom
#: to ONE. A budget with one byte of slack is a budget that reds on the next
#: reword rather than on the next mistake, so the ceiling moves with the
#: line: 280 + 49 = 329, rounded to 330, and the headroom is the same 50 it
#: was. The line is drop-empty, so a book with nothing unconfirmed pays
#: nothing for it.
BOARD_FIXED_TEXT_BUDGET_BYTES = 330


def board_fixed_text_bytes(view: dict) -> int:
    """The byte cost of the board's text that does not scale with which
    rows are shown: `_board_header_text` plus `board_quick_read`'s line as
    it reads once at least one row is on the board (the doors clause and
    BOARD_NUMBER_NOTE — the same text on a 5-row board as on a 25-row one).
    Row lines themselves (title, context tag) are excluded on purpose:
    they are what the byte-fit step-down (N-1) already polices per row.
    This is the OTHER half — the part no row-count changes."""
    nums = plate_numbers(view)
    header = _board_header_text(nums)
    n_rows = plate_item_count(board_rows(view))
    quick = board_quick_read(view, n_shown=min(1, n_rows), n_rows=n_rows)
    # WRAP2 4.2 item 8 — the breakdown line is fixed text too: it costs the
    # same bytes on a 5-row board as on a 25-row one, and those bytes come
    # straight off the row budget. Counting it HERE is the whole point of
    # this function; a fixed line the budget cannot see is a row M loses
    # with nothing to notice.
    breakdown = board_breakdown_line(nums)
    return len((header + breakdown + quick).encode("utf-8"))

#: THE DOORS, as a table rather than as prose (REVIEW_ONEPLATE1 F-3 and the
#: scope round's open item 1: both phrases were registered replies that
#: only the skill's paragraphs knew how to answer, and one of those
#: paragraphs named a mechanism that landed the reader on a different
#: plate). A reply on an open plate -> the surface `surface_drivers` fires
#: for it. One table, read by the driver, by the skill's prose and by the
#: suite.
PLATE_REPLY_SURFACES = {
    WORK_MY_PLATE_PHRASE: "plate-page",
    SHOW_PARKED_PHRASE: "show-parked",
    # SPEC SURFACEFIX1 5.5 (M's ruling 4, 2026-09-13) — the two phrases
    # `SHOW_MORE_PHRASES` has PRINTED under the WAIT and SCHEDULE blocks
    # since PLATE1, which until now no handler claimed. `show waiting` was
    # announced in the manifest as answering any time and every brief
    # pointed at it; typed on its own it said "no open plate in this
    # session" (attended test B2.9). These two lines are the route. Owned by
    # SURFACEFIX1; every other line of this file is PLATENUM1's.
    SHOW_MORE_PHRASES[BLOCK_WAIT]: "waiting-on",
    SHOW_MORE_PHRASES[BLOCK_SCHEDULE]: "schedule",
}

_REPLY_TRIM_RE = re.compile(r"[^a-z ]+")


def reply_surface(text) -> Optional[str]:
    """The surface an in-chat plate reply re-fires, or None when the words
    are not one of the doors. Backticks, punctuation and case are the
    reader's, not the router's."""
    key = _REPLY_TRIM_RE.sub("", str(text or "").lower()).strip()
    key = " ".join(key.split())
    return PLATE_REPLY_SURFACES.get(key)

# A directive only counts as a GROUPING instruction when it says so; "lead
# with anything involving Stone Co" arranges the brief without regrouping it.
_GROUPING_VERB_RE = re.compile(
    r"\b(group|grouped|grouping|organi[sz]e|organi[sz]ed|arrange|arranged|"
    r"section|sectioned|sort|sorted|break\s+out|split)\b", re.I)
_GROUP_BY_PERSON_RE = re.compile(
    r"\bby\s+(person|people|who|name|names|counterpart\w*|contact|contacts|"
    r"owner|owners)\b", re.I)
_GROUP_BY_PROJECT_RE = re.compile(
    r"\bby\s+(project|projects|entity|entities|company|companies|org|orgs|"
    r"client|clients|account|accounts|thread|threads|deal|deals)\b", re.I)


def brief_grouping(workspace_root) -> str:
    """How this workspace's BRIEF is organised — `project` (default) or
    `person`. R-N10-1: the board is grouped by project "or by person where
    the client's brief is organized that way", and the workspace's existing
    brief-organisation setting is what decides.

    That setting is the SCL1 customization layer, not a knob: a workspace
    says how it wants the brief arranged by telling it so, and
    `skill_custom_writer` stores the sentence
    (`_hq/custom/morning-briefing.md`; morning-briefing's own prose names
    "group by entity" as the example of the class). This reads the same
    directives the brief reads, so the two surfaces cannot disagree about
    how this seat is organised — and it reads them the way the brief does,
    through `load_directives`, never the raw file.

    LAST WORD WINS: directives accumulate, and the newest is the one the
    customer meant. Anything unreadable, absent or silent on grouping is
    `project` — the shipped arrangement. Never raises: a board that refuses
    to render because a customization file is malformed would be a worse
    fault than the one it reported.
    """
    try:
        from skill_custom_writer import load_directives
        directives = load_directives(workspace_root, "morning-briefing") or []
    except Exception:  # pragma: no cover — fail to the default, always
        return BOARD_GROUP_PROJECT
    for d in reversed(directives):
        try:
            text = str((d or {}).get("text") or "")
        except Exception:  # pragma: no cover
            continue
        if not _GROUPING_VERB_RE.search(text):
            continue
        if _GROUP_BY_PERSON_RE.search(text):
            return BOARD_GROUP_PERSON
        if _GROUP_BY_PROJECT_RE.search(text):
            return BOARD_GROUP_PROJECT
    return BOARD_GROUP_PROJECT


def board_group_of(row: dict, group_by: str) -> tuple:
    """(group key, group header) for ONE row on the board.

    By project this is the row's own project group — already resolved by
    `project_group` at build, so a header is never an id here either
    (SPEC_FLOW1 Lane C item 3 reaches the board for free). By person it is
    the name on the row: who you owe it to, or who owes it.
    """
    if group_by == BOARD_GROUP_PERSON:
        who = str(row.get("counterparty") or "").strip()
        return (who.lower(), who) if who else ("", NO_PERSON_LABEL)
    key = str(row.get("project_key") or NO_PROJECT_KEY)
    label = str(row.get("project_label") or NO_PROJECT_LABEL)
    return key, label


def board_rows(view: dict) -> list:
    """The rows the board renders: every OPEN row, importance first.

    PARKED is not here. A resting row is asked nothing (BLOCK_WANTS), so a
    line of board given to one is a line taken from a row that wants
    something — and P2 is kept by the two places that carry every resting
    row with its reason: the text plate and the wrap's PARKED review, plus
    the `show parked` door the board names.
    """
    return sorted((r for r in (view.get("rows") or [])
                   if r.get("block") != BLOCK_PARKED),
                  key=importance_key)


def board_groups(view: dict, *, group_by: str = BOARD_GROUP_PROJECT,
                 rows: Optional[list] = None) -> list:
    """`[{key, label, rows}]` — the board's groups, most important first.

    Rows inside a group are in importance order; the GROUPS are ordered by
    the most important row each one holds, so the group carrying today's
    overdue money row leads and the group of somedays sits last. Ordering
    groups by name instead — or parking the unbound group last the way the
    TEXT plate's project order does — would push an overdue row below a fold
    that a quiet project's name happened to win, which is the one thing the
    ruling forbids. The text plate keeps its own order; this is the board's.

    `rows` renders a SUBSET (the budget's keep set) with the same grouping.
    """
    if group_by not in BOARD_GROUPINGS:
        raise ValueError(
            f"group_by must be one of {BOARD_GROUPINGS}; got {group_by!r}")
    groups: dict = {}
    for r in (board_rows(view) if rows is None else rows):
        key, label = board_group_of(r, group_by)
        g = groups.get(key)
        if g is None:
            g = {"key": key, "label": label, "rows": []}
            groups[key] = g
        g["rows"].append(r)
    return sorted(groups.values(),
                  key=lambda g: (importance_key(g["rows"][0]),
                                 g["label"].lower()))


def _board_context(row: dict) -> str:
    """The row's one line, after its title: what it wants, when, and who is
    on the other end. Deliberately short — the board's whole economy is that
    a row costs one line, so the chip, the reason and the step count stay on
    the working page and in the text plate."""
    parts = [BLOCK_TITLES[row["block"]]]
    if "overdue" in (row.get("badges") or []):
        parts.append("OVERDUE")
    if row.get("due_phrase"):
        parts.append(row["due_phrase"])
    elif not row.get("due"):
        parts.append(HORIZON_TITLES[HORIZON_NO_DATE])
    if row.get("counterparty"):
        parts.append(row["counterparty"])
    if row.get("folded_n"):
        parts.append(f"+{row['folded_n']} more like it")
    return " · ".join(parts)


def board_sections(view: dict, *, group_by: str = BOARD_GROUP_PROJECT,
                   verbs: bool = True, rows: Optional[list] = None) -> list:
    """The board's widget sections — one per group, one line per row, ONE
    tap per row (Done) or none.

    `verbs=False` renders the same board with no buttons at all, which is
    the other half of M's ruling ("one tap for Done or no buttons") and what
    a read-only placement of the board takes.
    """
    sections: list = []
    for g in board_groups(view, group_by=group_by, rows=rows):
        items = []
        for r in g["rows"]:
            items.append({
                "n": r["id"],
                # THE row number — never this loop's position (R-N10-1),
                # shown as a label (`#83`) rather than as an ordinal.
                "display_n": board_display_n(r),
                "name": r["title"],
                "context_tag": _board_context(r),
                # ONE tap. Everything else is typed against this number.
                "actions": list(BOARD_VERBS) if verbs else [],
                "block": r["block"],
                "horizon": r["horizon"],
                # F-1 — the dispatcher's branch, unchanged: a guess confirms
                # and closes through done_items, every other row closes
                # directly.
                "pending": bool(r.get("pending")),
            })
        sections.append({
            "title": f"{g['label']} ({len(items)})",
            "count": len(items),
            "lane": g["key"] or NO_PROJECT_KEY,
            "items": items,
        })
    return sections


def board_quick_read(view: dict, *, n_shown: int, n_rows: int) -> str:
    """The line under a board that did not fit: how many more, and the two
    doors to them.

    NUMBER1 3.2 — `n_shown` and `n_rows` are ITEM counts
    (`plate_item_count`), because the header above this line states items:
    `n_shown + (n_rows - n_shown) + held_back_counts["resting"]` equals
    `plate_numbers["open"]` exactly, at every row budget. `work my plate` opens the working page over the whole
    plate; `show parked` opens the resting rows. Nothing is lost and the
    reader is told where it went — the idiom the folded blocks already use
    ("16 waiting — say `show waiting`")."""
    parts = []
    n_more = max(0, n_rows - n_shown)
    if n_more:
        parts.append(f"{n_more} more — say `{WORK_MY_PLATE_PHRASE}`")
    # REVIEW_ONEPLATE1 F-4 — ONE composer for the door, on every surface
    # that names it, so the count behind `show parked` is the same number
    # wherever a reader reads it. It is the working page's held-back set:
    # the resting rows PLUS the rows the page's own cap cut, which are the
    # only rows no page reaches.
    clause = show_parked_clause(view)
    if clause:
        parts.append(clause)
    line = "; ".join(parts) + "." if parts else ""
    # REVIEW_ONEPLATE1 ruling 1 — the one place the numbering explains
    # itself. Said under the board, once, never on a row: a reader looking
    # at 83, 82, 81, 78 needs to know that is a label and not a position,
    # and that the same label works on every other surface.
    if n_shown:
        line = (line + " " if line else "") + BOARD_NUMBER_NOTE + "."
    return line


def render_board(view: dict, *, group_by: str = BOARD_GROUP_PROJECT,
                 verbs: bool = True, row_budget: Optional[int] = None,
                 source_skill: str = "commitment-triage") -> dict:
    """THE DEFAULT PLATE RENDER (R-N10-1) — the board, as a widget data view.

    `row_budget` caps the rows RENDERED, not the rows that exist. THE CUT IS
    MADE ON THE IMPORTANCE-ORDERED ROW LIST, BEFORE GROUPING — never on the
    grouped sections. Cutting the grouped view instead would drop the last
    group's rows first, and the last group is only the least important group
    ON AVERAGE: a single overdue row sitting in a quiet project would fall
    below the fold while six somedays rows above it survived. Cut the rows,
    then group what is left, and the fence is structural: the kept set is a
    prefix of the importance order, so an overdue row is below the fold only
    when every row above it is also overdue.

    When the budget cuts, `quick_read` says how many more and names the
    doors. `None` renders every open row.

    A refused view renders as the one plain line and no rows, exactly as
    `plate_data_view` does — the empty-state mode, never lanes.
    """
    if not view.get("ok"):
        line = view.get("error") or "Nothing to show."
        return {"source_skill": source_skill, "surface": "commitments",
                "widget_mode": "all_clear_summary", "header": line,
                "refused": True}
    nums = plate_numbers(view)
    header = _board_header_text(nums)
    breakdown = board_breakdown_line(nums)
    all_rows = board_rows(view)
    n_rows = len(all_rows)
    keep = (all_rows if row_budget is None
            else all_rows[:max(0, int(row_budget))])
    sections = board_sections(view, group_by=group_by, verbs=verbs, rows=keep)
    n_shown = len(keep)
    # NUMBER1 3.2 — what the FOOTERS state is the population the HEADER
    # states: items, folds included. The row counts stay beside them
    # because the byte budget is spent per rendered row, not per item.
    items_open = plate_item_count(all_rows)
    items_shown = plate_item_count(keep)
    dv = {
        "source_skill": source_skill,
        "surface": "commitments",
        "header": header,
        "sections": sections,
        "block_totals": dict(nums["blocks"]),
        "plate_counts": dict(view.get("counts") or {}),
        "counters": [
            {"label": "Do it", "value": nums["blocks"].get(BLOCK_DO_IT, 0)},
            {"label": "Chase", "value": nums["blocks"].get(BLOCK_CHASE, 0)},
            {"label": "Wait", "value": nums["blocks"].get(BLOCK_WAIT, 0)},
        ],
        # Model-only, never rendered: what the board actually did, so the
        # driver and the suite can state it without re-deriving it.
        "board": {"group_by": group_by, "rows_shown": n_shown,
                  "rows_open": n_rows,
                  "items_shown": items_shown, "items_open": items_open,
                  # The remainder the reader is TOLD about, in items.
                  "n_more": max(0, items_open - items_shown),
                  "n_parked": nums["blocks"].get(BLOCK_PARKED, 0)},
    }
    # WRAP2 4.2 item 8 / CAP-F7 — the breakdown, under line one. `sub_header`
    # is the renderer's own slot for exactly that position
    # (`chat_output_renderer.render_chat_output_widget`: header, then
    # sub_header, then the sections), so this is the existing line-two slot
    # rather than a new key nothing renders. Drop-empty.
    if breakdown:
        dv["sub_header"] = breakdown
    quick = board_quick_read(view, n_shown=items_shown,
                             n_rows=items_open)
    if quick:
        dv["quick_read"] = quick
    _scan_data_view(dv)
    return dv


def board_row_budget(dv: dict) -> int:
    """How many board rows the widget's SIZE budget holds — measured, never
    guessed. The budget is bytes (`widget_transport.WIDGET_PAGE_BYTE_BUDGET`,
    the relay ceiling), and a board row's rendered weight depends on its
    title and its one button, so the only honest answer is to render and
    look. Falls back to `BOARD_ROW_FLOOR` if the renderer cannot be reached
    at all."""
    try:
        from widget_transport import fit_row_budget
        return fit_row_budget(dv, wrapper="fragment")
    except Exception:  # pragma: no cover — a short board beats no board
        return BOARD_ROW_FLOOR


def plate_board_view(workspace_root, *, now_iso: Optional[str] = None,
                     user_person_id: Optional[str] = None,
                     source_skill: str = "commitment-triage",
                     group_by: Optional[str] = None,
                     row_budget: Optional[int] = None) -> dict:
    """`build_plate` -> the BOARD -> the widget data view
    `widget_transport.render_and_persist` takes. THE default plate render
    (R-N10-1). One projection: every number on it comes from
    `plate_numbers` over the same build.

    `group_by` defaults to the workspace's own brief organisation
    (`brief_grouping`); `row_budget` defaults to what the widget's size
    budget measurably holds.
    """
    view = build_plate(workspace_root, user_person_id=user_person_id,
                       now_iso=now_iso)
    if group_by is None:
        group_by = brief_grouping(workspace_root)

    def _board(n=None):
        return render_board(view, group_by=group_by,
                            source_skill=source_skill, row_budget=n)

    full = _board()
    if full.get("refused"):
        return full
    if row_budget is None:
        row_budget = board_row_budget(full)
    if full["board"]["rows_open"] <= row_budget:
        return full
    dv = _board(row_budget)
    # The search above measured a probe cut in SECTION order; the board it
    # produced is cut in IMPORTANCE order and so may carry a different number
    # of group headings. Small, but "small" is not "fits" — check the thing
    # that will actually be relayed and step down until it does. Bounded by
    # the row count, and every step is one render.
    try:
        from widget_transport import fits_budget
        n = row_budget
        while n > 1 and not fits_budget(dv, wrapper="fragment"):
            n -= 1
            dv = _board(n)
    except Exception:  # pragma: no cover — never fail a read on the check
        pass
    return dv


class WrapRelayError(RuntimeError):
    """The recap's posted body dropped or summarised a line of the plate's
    wrap cut — a Parked row the reader never saw."""


def wrap_relay_check(post_text: str, plate_text: str) -> list:
    """CUT-PLATE (2026-09-06) — THE BYTE-EXACT RELAY FENCE for the wrap.

    The v5.28.0 attended test (B2.3) saw the Friday wrap replace the PARKED
    review with one sentence ("171 on the plate now, 38 parked, most
    untouched 36–54 days") and print no Parked row at all — the skill said
    "render VERBATIM" and the model summarised. This is the mechanical
    half: every non-empty line of `plate_text` (the `wrap_cut` render)
    must appear in `post_text` exactly. Returns the MISSING lines, in the
    plate's order; empty list = relayed byte-exact. The recap runs it
    before posting and refuses to post over a non-empty result."""
    have = {l.rstrip() for l in (post_text or "").split("\n")}
    return [l for l in (plate_text or "").split("\n")
            if l.strip() and l.rstrip() not in have]


def parked_lines_in(text: str) -> list:
    """The Parked ROW lines of a wrap text — the lines under `## PARKED`
    that begin a row ("- "), stopping at the next top-level heading."""
    out: list = []
    inside = False
    for line in (text or "").split("\n"):
        s = line.rstrip()
        if s.startswith("## "):
            inside = s.startswith("## PARKED")
            continue
        if inside and s.startswith("- "):
            out.append(s)
    return out


def wrap_parked_overflow(post_text: str, plate_text: str) -> int:
    """How many Parked rows the POST carries beyond the ones the plate cut
    gave it (PLATENUM1 4.3). Zero is the only acceptable answer."""
    return max(0, len(parked_lines_in(post_text))
               - len(parked_lines_in(plate_text)))


def assert_wrap_relayed(post_text: str, plate_text: str) -> None:
    """Both halves of the relay contract.

    CUT-PLATE: every line of the cut appears in the post, byte-exact — the
    model may not summarise a Parked row away.

    PLATENUM1 4.3: and it may not ADD one either. The cut now caps the
    Parked list at `WRAP_PARKED_CAP`, so a post carrying more Parked rows
    than the cut did is a model that went back to the plate and re-expanded
    the list — the 176-row wrap, arriving through the other door. A cap the
    relay fence does not know about is a cap the next render undoes.
    """
    missing = wrap_relay_check(post_text, plate_text)
    if missing:
        raise WrapRelayError(
            f"the wrap's plate cut was not relayed byte-exact — {len(missing)} "
            f"line(s) missing from the post (CUT-PLATE, P2: a Parked row is "
            f"never hidden): {missing[:3]!r}")
    extra = wrap_parked_overflow(post_text, plate_text)
    if extra:
        raise WrapRelayError(
            f"the post lists {extra} Parked row(s) the plate cut did not — "
            f"the wrap lists at most {WRAP_PARKED_CAP} (PLATENUM1 4.3); the "
            f"rest are one `{SHOW_PARKED_PHRASE}` away")


def wrap_cut(workspace_root, *, since_iso: str,
             now_iso: Optional[str] = None,
             user_person_id: Optional[str] = None) -> dict:
    """PLATE1 night 2 — the Friday wrap's ONE call (D7 `wrap`): the week's
    delta (opened / closed / slipped over [since, now]) in the plate's shape
    plus the PARKED review, no verbs (the wrap is prose). Returns
    `{"refused", "line", "text", "delta", "attention"}`; a no-user workspace
    refuses with the one plain line (D8). Never raises into the recap."""
    from commitment_state import PRIMARY_USER_REFUSAL_LINE
    refusal = {"refused": True, "line": PRIMARY_USER_REFUSAL_LINE, "text": "",
               "delta": {"n_opened": 0, "n_closed": 0, "n_slipped": 0,
                         "since": since_iso}, "attention": 0}
    try:
        view = build_plate(workspace_root, user_person_id=user_person_id,
                           now_iso=now_iso, since_iso=since_iso)
        if not view.get("ok"):
            out = dict(refusal)
            out["line"] = view.get("error") or PRIMARY_USER_REFUSAL_LINE
            return out
        rendered = render_plate(view, "wrap", False)
    except Exception as exc:  # noqa: BLE001
        out = dict(refusal)
        out["line"] = "I couldn't read your plate for this week's wrap."
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    # THE CUT IS STAMPED HERE, BY THE COMPOSER THAT MADE IT
    # (RE-VERIFY_LEAK4 P-1, 2026-09-18). `surface_composers.post` vouches a
    # relay in full or refuses, and the Friday wrap relays this cut -- so
    # something has to tell the register that this text came back from a
    # composer. Fix round 2 put that line in `quiet.wrap_post`, which was
    # wrong twice over: `wrap_post` stamps whatever TEXT IT IS HANDED, so one
    # call from anywhere vouched for any paragraph, and it stamped before its
    # own fences ran, so even a refused wrap forged. The stamp belongs to the
    # function that PRODUCED the words, and that is this one: it stamps its
    # own `rendered["text"]` and nothing a caller passed in, so there is no
    # argument here for a caller to spend.
    import surface_composers as _sc
    _sc._register_composed(rendered["text"])
    return {"refused": False, "line": rendered["text"].split("\n")[0],
            "text": rendered["text"], "delta": rendered["delta"],
            "attention": rendered["attention"],
            # REVIEW_ONEPLATE1 F-1 - the customer's own words, so the
            # document gate can tell them from the renderer's.
            "user_spans": list(rendered.get("user_spans") or [])}


WRAP_DOCX_HEADING = "Your plate this week"


def wrap_docx_section(cut: dict, *, heading: str = WRAP_DOCX_HEADING) -> dict:
    """WRAPSAVE1 — the wrap cut as a `brief_writer` section that SAVES.

    ATTENDED_TEST_v5.29.0 B2.3: the Friday wrap's `.docx` did not save. The
    plate cut was handed to `brief_writer` as a section `body`, and the
    plate's own punctuation — the em dash that separates a row's title from
    its counterparty, its due phrase and its reason, on every row — is
    `voice_tell_detector`'s `dash_as_punctuation`, a FAIL-severity finding
    on every brief kind (SPEC DASHBAN). So the gate refused the save, and
    the weekly document M actually reads never appeared.

    Neither half of that is wrong on its own: the dash ban is M's brand
    voice, and the plate's dash is a column separator in a LIST, not
    punctuation in prose. The fix is to hand the cut to the writer as what
    it is. `voice_tell_detector.check_sections` runs its structural scan
    over section `body` paragraphs ONLY — bullets and table cells are
    "legitimately list-shaped" (SPEC B2 §8) — and `dash_rewriter` scopes to
    exactly the same surface, so a list-shaped cut is not rewritten either.
    One line in, one bullet out: the relay stays byte-exact
    (`wrap_relay_check` passes over the joined bullets), every Parked row
    still renders (P2), and nothing about the words changes.

    Refused cut (`cut["refused"]`) → the one plain line, as the single
    bullet. It carries a dash of its own ("Nothing was rendered — update
    Command Room…"), so a refusal handed over as `body` would fail the save
    for the second time in a row, on a workspace that is already degraded.

    PLATENUM1 4.3 — the document carries THE SAME cut, so the `.docx` holds
    at most `WRAP_PARKED_CAP` Parked rows too. The cap is applied once, in
    `_delta_text`; this function's job is only never to re-expand it, and
    the raise below is the fence that says so out loud.
    """
    if cut.get("refused"):
        return {"heading": heading, "bullets": [cut.get("line") or ""]}
    bullets = [ln.rstrip() for ln in (cut.get("text") or "").split("\n")
               if ln.strip()]
    n_parked = len(parked_lines_in(cut.get("text") or ""))
    if n_parked > WRAP_PARKED_CAP:
        raise WrapRelayError(
            f"the weekly document would carry {n_parked} Parked rows — the "
            f"wrap lists at most {WRAP_PARKED_CAP} (PLATENUM1 4.3)")
    # REVIEW_ONEPLATE1 F-1 - PROVENANCE TRAVELS WITH THE SECTION. Without
    # it the document gate refuses the whole weekly `.docx` over one banned
    # marketing word sitting inside a promise the customer's counterparty
    # wrote, which is the second half of ATTENDED_TEST_v5.29.0 B2.3.
    # `brief_writer.make_brief` hands these to `docx_leak_scanner`, which
    # blanks them for the MARKETING family only; every other family still
    # reads the document in full.
    return {"heading": heading, "bullets": bullets,
            "user_spans": list(cut.get("user_spans") or [])}


__all__ = [
    "build_plate", "render_plate", "plate_data_view", "wrap_cut", "horizon_of",
    "CAP_KEEP_HORIZONS",
    "WrapRelayError", "wrap_relay_check", "assert_wrap_relayed",
    "fold_proposals_and_hints", "scan_plate_words", "PlateJargonError",
    "plain_words", "PLAIN_WORDS", "PlateAskError", "ASK_SHAPE_PATTERNS",
    "ASK_FENCE_PROSE_MARKERS", "scan_no_pending_question",
    "BLOCK_ORDER", "BLOCK_TITLES", "HORIZON_ORDER", "PLATE_VERBS",
    "CONFIRM_VERBS", "CUSTOMER_BANNED_TOKENS", "PLATE_BANNED_PATTERNS",
    "CHASE_QUIET_DAYS", "PARKED_STALE_DAYS", "BRIEF_CAP", "SURFACES",
    "OPEN_BY_DEFAULT", "SHOW_MORE_PHRASES", "SCOPE_ALL", "SCOPE_WOULD_HOLD",
    "row_title",
    "BLOCK_VERB_LINES", "page_text_fallback", "TEXT_FALLBACK_SOURCES",
    "BLOCK_DO_IT", "BLOCK_CHASE", "BLOCK_WAIT", "BLOCK_SCHEDULE",
    "BLOCK_CONFIRM", "BLOCK_PARKED",
    "BRIEF_ROW_VERBS", "DELTA_CAP", "DELTA_POINTER", "WINDOW_WORDS",
    "BLOCK_ONE_TAPS", "ONE_TAP_LINES", "ONE_TAP_CHASE", "ONE_TAP_SCHEDULE",
    # ONEPLATE1 (SPEC_FLOW1 Lane C)
    "project_group", "NO_PROJECT_LABEL", "NO_PROJECT_KEY",
    "user_authored_spans", "id_shaped_name",
    "plate_numbers", "surface_numbers", "NUMBER_KEYS",
    "importance_key", "widget_hidden_ids", "parked_cap_line",
    # PLATENUM1 — the wrap's Parked cap, the number that belongs to the item
    "WRAP_PARKED_CAP", "wrap_parked_rows", "parked_lines_in",
    "wrap_parked_overflow", "wrap_reason_retired",
    "REASON_ORIGIN_CUSTOMER", "REASON_ORIGIN_PRODUCT",
    "PRODUCT_QUESTION_REASONS",
    "PLATE_NUMBERS_REL", "PLATE_NUMBERS_VERSION", "plate_numbers_path",
    "load_number_map", "mint_display_numbers", "resolve_display_number",
    "display_number_of", "NUMBER_MAP_LOCK_TIMEOUT_S",
    "WIDGET_PAGE_ROWS", "WIDGET_MAX_PAGES", "SHOW_PARKED_PHRASE",
    "client_person_ids", "says_money", "wrap_docx_section",
    # R-N10-1 — the board is the default plate render
    "assign_display_numbers", "brief_grouping", "board_group_of",
    "stamp_held_back", "held_back_counts", "show_parked_clause",
    "plate_item_count",
    "HELD_RESTING", "HELD_FURTHER", "HELD_NOUNS",
    "render_parked", "parked_data_view", "PARKED_HEADING",
    "PLATE_REPLY_SURFACES", "reply_surface", "measured_page_rows",
    "board_rows", "board_groups", "board_sections", "board_quick_read",
    "render_board", "plate_board_view", "board_row_budget",
    "BOARD_GROUP_PROJECT", "BOARD_GROUP_PERSON", "BOARD_GROUPINGS",
    "BOARD_VERBS", "BOARD_ROW_FLOOR", "NO_PERSON_LABEL",
    "BOARD_NUMBER_PREFIX", "BOARD_NUMBER_NOTE", "board_display_n",
    "BOARD_FIXED_TEXT_BUDGET_BYTES", "board_fixed_text_bytes",
    "BOARD_BREAKDOWN_TEMPLATE", "BOARD_BREAKDOWN_AWAITING",
    "board_breakdown_line",
    "WORK_MY_PLATE_PHRASE",
]
