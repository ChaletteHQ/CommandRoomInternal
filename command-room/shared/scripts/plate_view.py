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

NO_PROJECT_LABEL = "No project"
NO_PROJECT_KEY = ""

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


def _local_today(ws: Path, now_iso: str) -> _dt.date:
    """Today in the workspace timezone (tz.py — no silent UTC). A workspace
    with no timezone resolves to the ISO date of `now_iso`, which is the
    same answer for every date-only due (the common case) and the honest
    fallback for a fixture without one."""
    from tz import TZResolutionError, to_local
    try:
        local = to_local(now_iso, workspace_path=ws)
        return local.date() if local is not None else _dt.date.today()
    except (TZResolutionError, ValueError, TypeError):
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
                           "reason": d.get("reason") or ""}
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
                include_movement: bool = False) -> dict:
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
        UNTITLED_PLACEHOLDER, bucket_of, commitment_kind, count_commitments,
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
    counts = count_commitments(opens, user_person_id=user_person_id,
                               now_iso=now_iso, movement=movement)
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

    today = _local_today(ws, now_iso)
    now_dt = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=_dt.timezone.utc)
    ent = _entities(ws)
    people = _names_by_id(ent, "people")
    threads = _names_by_id(ent, "threads")

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
        if hint and hint.get("status_hint") == STATUS_HINT_PARKED:
            when = _short_date(hint.get("ts"))
            why = plain_words(hint.get("reason") or "parked")
            return BLOCK_PARKED, (f"{why} ({when})" if when else why), badges, True
        kind = commitment_kind(ev)
        if kind == "scheduling":
            return BLOCK_SCHEDULE, "", badges, False
        if bucket == BUCKET_YOU_OWE:
            mdays = _movement_days(cid)
            if (kind == "task" and _parse_date(due) is None
                    and mdays is not None and mdays >= PARKED_STALE_DAYS):
                return BLOCK_PARKED, f"no movement {mdays} days", badges, False
            return BLOCK_DO_IT, "", badges, False
        # owed to you. A nudge you already sent (`blocked` in the movement
        # classification) reset the quiet clock: WAIT inside the window,
        # CHASE again past it — the same clock, no special case.
        mdays = _movement_days(cid)
        window = _cadence_days(ev)
        if mdays is not None and mdays > window:
            return BLOCK_CHASE, f"quiet {mdays} days", badges, False
        return BLOCK_WAIT, "", badges, False

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
        project_key = ev.get("primary_thread_id") or NO_PROJECT_KEY
        rows.append({
            "id": cid,
            "title": d.get("title") or d.get("summary") or UNTITLED_PLACEHOLDER,
            "block": block,
            "horizon": horizon_of(due, today),
            "project_key": project_key,
            "project_label": threads.get(project_key) or (
                NO_PROJECT_LABEL if not project_key else project_key),
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
            "kind": commitment_kind(ev),
            "chip": _chip(ev, cid),
            "reason": reason,
            "reason_stored": reason_stored,
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
                # REVIEW_CUTD F-1 — the sentence names the rule that is
                # actually applied (the kept set is never trimmed, so the
                # plate may show MORE than the cap); pinned verbatim in
                # `run_quiet1_test` [7].
                r["reason"] = (f"held back — overdue and this-week items stay up; "
                               f"the rest fill the plate up to {cap_limit} at a time")
                r["reason_stored"] = False
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
             window: the date passed and nobody closed the item. A row
             already overdue before the window is not a slip of this
             window — it is on the plate's Overdue horizon.

             CUT-PLATE (2026-09-06): the window's LAST day counts. The
             day-close reads a same-day window (the morning anchor or the
             day floor), and with `due < today` no row could ever slip on
             a scheduled 5 PM fire — the count was 0 by construction
             (PLATE1-N2's own record §5.5 noted it). A row due TODAY that is
             still open when the day closes has slipped today, so the test
             is `since_date <= due <= today`. Readers of `n_slipped`: the
             eod and wrap headers (`_delta_text`), the persisted pack, and
             the CUT-PLATE suite; the PLATE1 goldens carry no row due on
             their fixture's `today`, so they are byte-identical.
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
        if due is not None and since_date <= due <= today:
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
        why = _user(r["reason"]) if r.get("reason_stored") else r["reason"]
        reason = f" — {why}"
    return f"- {_user(r['title'])}{who}{due}{badge}{folded}{sub}{reason}{chip}"


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


def _block_sections(view: dict, block: str, *, verbs: bool, display) -> list:
    """Widget sections for one block: one section per project, rows keyed by
    the commitment's `data.id` VERBATIM (identity contract) with the
    horizon on the context line."""
    blk = view["blocks"].get(block) or {"total": 0, "projects": []}
    sections: list = []
    for proj in blk["projects"]:
        items: list = []
        for hz in proj["horizons"]:
            for r in hz["rows"]:
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
                if r.get("chip"):
                    parts.append(r["chip"])
                item = {
                    "n": r["id"],
                    "display_n": display["n"],
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

    totals = view["block_totals"]
    attention = totals.get(BLOCK_DO_IT, 0) + totals.get(BLOCK_CHASE, 0)
    lines: list = []
    data_view = None

    brief_rows: list = []
    pointer = ""
    if surface == "brief":
        n_more = 0
        skip = {str(i) for i in (exclude_ids or [])}
        asks = ask_lines or {}
        lines.append(f"{attention} on your plate today" if attention
                     else "Nothing is waiting on you today.")
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
        total = view["counts"].get("total", 0)
        header = f"What's on your plate — {total} open"
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
        for block in BLOCK_ORDER:
            sections.extend(_block_sections(view, block, verbs=verbs,
                                            display=display))
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
            "plate_counts": dict(view["counts"]),
        }
        if counters:
            data_view["counters"] = counters
        folded_lines = []
        for block in BLOCK_ORDER:
            if block in open_blocks:
                continue
            n = totals.get(block, 0)
            if n:
                phrase = SHOW_MORE_PHRASES.get(block)
                noun = {"wait": "waiting", "schedule": "to schedule"}.get(block, "more")
                folded_lines.append(f"{n} {noun}" + (f" — say `{phrase}`" if phrase else ""))
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
           "attention": attention, "block_totals": dict(totals)}
    if surface == "brief":
        out["rows"] = brief_rows
        out["pointer"] = pointer
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
    totals = view["block_totals"]
    attention = totals.get(BLOCK_DO_IT, 0) + totals.get(BLOCK_CHASE, 0)
    lines: list = []
    if not d or d.get("window_unreadable"):
        lines.append(f"No window to read {word}'s plate against — "
                     f"{DELTA_POINTER}.")
        return "\n".join(lines) + "\n"
    n_o, n_c, n_s = d["n_opened"], d["n_closed"], d["n_slipped"]
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
        # the window's start are summed into one count line instead.
        def _listed(r):
            return not (r.get("parked_by_cap") and not r.get("parked_by_cap_new", True))
        n_resting = sum(1 for proj in blk["projects"] for hz in proj["horizons"]
                        for r in hz["rows"] if not _listed(r))
        n_listed = blk["total"] - n_resting
        if n_listed:
            lines.append(f"## PARKED ({n_listed}) — still on your plate? "
                         "Each one rests with its reason; clear it from the "
                         "plate or leave it resting")
            for proj in blk["projects"]:
                rows_p = [r for hz in proj["horizons"] for r in hz["rows"] if _listed(r)]
                if not rows_p:
                    continue
                lines.append(f"### {_user(proj['label'])} ({len(rows_p)})")
                for r in rows_p:
                    lines.append(_row_line(r, verbs=False))
            lines.append("")
        if n_resting:
            cap = (view.get("cap") or {}).get("limit")
            lines.append(f"{n_resting} {'item rests' if n_resting == 1 else 'items rest'} "
                         "under Parked — overdue and this-week items stay up; the rest "
                         f"fill the plate up to {cap} at a time — listed the week they "
                         "went there, not again.")
            lines.append("")
    lines.append(f"{attention} on your plate now — {DELTA_POINTER}.")
    return "\n".join(lines).rstrip() + "\n"


def _scan_data_view(dv: dict) -> None:
    """The widget's renderer-authored strings pass the same gate. Row names
    are user text (blanked); context lines carry names and chips, which are
    user/writer text — so the scan covers the section titles, the header,
    the quick-read, the counters and the reduced-verb line: every string
    this module composed."""
    parts = [dv.get("header") or "", dv.get("quick_read") or ""]
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
                if part in HORIZON_TITLES.values() or part == "OVERDUE" \
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


def assert_wrap_relayed(post_text: str, plate_text: str) -> None:
    missing = wrap_relay_check(post_text, plate_text)
    if missing:
        raise WrapRelayError(
            f"the wrap's plate cut was not relayed byte-exact — {len(missing)} "
            f"line(s) missing from the post (CUT-PLATE, P2: a Parked row is "
            f"never hidden): {missing[:3]!r}")


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
    return {"refused": False, "line": rendered["text"].split("\n")[0],
            "text": rendered["text"], "delta": rendered["delta"],
            "attention": rendered["attention"]}


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
    "BLOCK_VERB_LINES", "page_text_fallback", "TEXT_FALLBACK_SOURCES",
    "BLOCK_DO_IT", "BLOCK_CHASE", "BLOCK_WAIT", "BLOCK_SCHEDULE",
    "BLOCK_CONFIRM", "BLOCK_PARKED",
    "BRIEF_ROW_VERBS", "DELTA_CAP", "DELTA_POINTER", "WINDOW_WORDS",
    "BLOCK_ONE_TAPS", "ONE_TAP_LINES", "ONE_TAP_CHASE", "ONE_TAP_SCHEDULE",
]
