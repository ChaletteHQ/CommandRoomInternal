#!/usr/bin/env python3
"""FOLD1A (SPEC_FLOW1 Lane H) — End of Day's ≤2 questions, ONE shared budget.

WHY THIS EXISTS
----------------
M's ruling: "End of Day's ≤ 2 questions draw from the Staff Meeting's weekly
five (TTL1... is building the expiry engine over the same queue) — not on
top of them." Two facts made this a plumbing problem rather than a number to
invent:

  1. The Staff Meeting's "FROM YOUR MEETINGS" section (`needs_review_queue.
     staff_meeting_group_section`) reads the SAME queue this module reads
     (`needs_review_queue.build_queue_view`) — it is already "the same
     queue" TTL1 is building its expiry engine over.
  2. `quiet.py` (QUIET1, D4) already runs ONE shared weekly question budget
     (`QUESTION_BUDGET` — 5/week under the shipped `light` preset, which is
     every new client's starting posture) that `ASKER_OVERDUE` /
     `ASKER_MEETING_CARD` / `ASKER_AGE_OUT` already draw from through the
     ONE door, `quiet.submit_questions`. "The Staff Meeting's weekly five"
     IS this budget.

So the plumbing this lane owns is: register a FOURTH asker
(`quiet.ASKER_EOD_CONFIRM`) and submit End of Day's candidates through the
SAME door — never a second, parallel cap invented here. `submit_questions`'s
`used` count is DISTINCT (asker, id) pairs asked in the rolling week, across
EVERY asker, so an EOD-asked row spends the identical budget an
overdue-morning-ask or a meeting-card ask would.

THE HONEST CAVEAT (FOLD1B is still deferred)
--------------------------------------------------------------------------
FOLD1B (the Staff Meeting's own redesign — "one-tap, pre-picked, no header
count") is EXPLICITLY DEFERRED past this build, and it has not yet been
wired to submit its OWN rows through `quiet.submit_questions` either — that
wiring is FOLD1B's job, not this lane's (`Do NOT touch the Staff Meeting's
shape`). Until FOLD1B lands, this module's asker is the ONLY one of the four
actually drawing on the queue at end-of-day cadence. Wiring THIS asker
through the shared door is still correct and forward-compatible: the day
FOLD1B submits the Staff Meeting's own rows through `quiet.submit_questions`
too, End of Day's asks are ALREADY inside the same pool, "not on top of
them" for real, with no further change needed here.

FIX ROUND 2 (REVIEW_FOLD1A R-1) closed the half of that seam this lane could
close on its own: the Staff Meeting's page ceiling now reads what is LEFT of
the week's budget rather than its limit (`quiet.staff_meeting_question_ceiling`
— TTL1's `question_ttl.staff_meeting_question_ceiling` yields to it at the
merge), so the evening's two shrink the Staff Meeting's page. The half that
remains is FOLD1B's and is stated plainly here rather than claimed closed: the
Staff Meeting reads that ceiling as a page bound and never calls
`quiet.submit_questions`, so it does not DEBIT the budget. The arithmetic is
enforced on one side only until FOLD1B wires its rows through this same door.

THE FENCE THIS WIDENS, AND EXACTLY HOW FAR (fix round 1)
--------------------------------------------------------------------
`end_of_day.compose_screen` ships a mechanically enforced no-question
contract for the day-close (`ScreenShapeError`; `screen_shape_violations`
reds on any line ending in `?`), cut by CUT-PLATE on 2026-09-06 — the SAME
day as the design rule this module cites. The first cut of this lane found
the collision, built the plumbing computed-only, and left the call.

RULED (R-N10-3, from M's design rule of 2026-09-06 — "I don't mind a couple
of those questions appearing on end of day" — on the reviewer's reasoning in
REVIEW_FOLD1A §8): the fence is WIDENED BY CITATION, narrowly. G50's own
text describes it as "M's hold: NO decisions block and no question in the
day-close", and a hold is a provisional pin awaiting a decision; the thing
it pins is the DECISIONS BLOCK — the four-verb confirm menu — which is a
different object from "a couple of important questions, answer pre-picked".
So:

  * `end_of_day.COMPUTED_ONLY` still contains `confirm`. The decisions block
    stays forbidden, and this is a DISTINCT key (`eod_questions`), never a
    re-render of that one.
  * `screen_shape_violations` gains an allowance keyed to the DECLARED
    `eod_questions` block — never a blanket "lines ending in `?` are fine".
    A question line the block did not contribute still reds. A THIRD
    question still reds.
  * At most 2 rows, drawn from the Staff Meeting's existing weekly five
    (this module's whole point), each with the likely answer pre-picked,
    each answerable in one word, and never a question the Staff Meeting was
    not already going to ask.

The morning brief and the Friday wrap are untouched and still never ask
(DESIGN_RULE §1) — pinned in this lane's suite.

WHAT THIS MODULE DOES, AND DOES NOT, ANSWER
----------------------------------------------
`eod_confirm_candidates` returns up to `MAX_EOD_QUESTIONS` rows from
`needs_review_queue.build_queue_view`, oldest first (the same "oldest call
first" rotation rule the Staff Meeting's own section already uses), submitted
through the shared budget so a row the budget has already spent this week
does not double-count. It returns only what the budget APPROVES — 0, 1, or 2
rows, never more, and never a phantom row the customer cannot actually act
on. The row's own verbs (`confirm` / `already done` / `drop` / `not mine`)
are UNCHANGED — this module names candidates, it does not invent a new verb
set, and answering ANY of them routes through the EXACT SAME
`needs_review_queue.confirm_items` / `done_items` / `drop_items` /
`not_mine_items` functions the on-demand `needs your call` queue and the
Staff Meeting's own section already use. A row answered at End of Day is
REMOVED from the shared queue by that call alone — no separate "mark asked
so the Staff Meeting skips it" bookkeeping was needed or built, because
answering a queue row through its one writer is what makes it stop being an
open row anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: M's ruling: "End of Day's ≤ 2 questions". A per-EVENING cap, independent
#: of the shared weekly budget — the evening never asks more than this even
#: on a week the budget has room for five.
MAX_EOD_QUESTIONS = 2


def _queue_ledger_map(workspace_root, *, now_iso: str | None = None) -> dict:
    """`{commitment_id: probe}` — the LEDGER EVENT the lifetime predicate is
    asked about, for every needs-review row (REVIEW_NIGHT11C M-1).

    ONE PREDICATE, ONE OBJECT. FOLD1-B built `_lifetime_ask` so the meeting
    fold, the on-demand queue page and the expiry engine all hand
    `within_lifetime` the same thing: the ledger event, which carries
    `owner_id` / `counterparty_id` / `org_id` / `person_ids`. The evening
    built its own probe instead — a copy of the queue VIEW row, which
    carries `title`, `due`, `evidence` and nothing the importance rule
    reads. So DESIGN_RULE §4's escape ("whatever a fold hides, it never
    hides an overdue item or one due this week", and never a client's)
    could not fire here: a client-owned row past two days was held
    everywhere and hidden in the evening.

    A row that CARRIES A CARD QUESTION gets the fold's own object, clock and
    default (`needs_review_queue._lifetime_ask`). Every other needs-review
    row gets its ledger event, and the class is supplied the way it always
    was — the row's own if it resolves, the capture card's if not. The
    difference is whose row the importance rule reads, not which lifetime it
    measures.

    Degrades to `{}`: no map, and `_offerable` falls back to the view row,
    which is exactly the behaviour before this existed.
    """
    out: dict = {}
    try:
        import needs_review_queue as nrq
        from cru_match import _commitment_id, load_needs_review
    except Exception:  # pragma: no cover — no queue module, no map
        return out
    try:
        events = load_needs_review(str(nrq._events_path(Path(workspace_root))),
                                   workspace_root=str(workspace_root))
    except Exception:  # pragma: no cover — degrade to the view row
        return out
    try:
        cards = nrq._card_question_ages(workspace_root, now_iso=now_iso,
                                        rows=events)
    except Exception:  # pragma: no cover
        cards = {}
    for ev in events or []:
        try:
            cid = _commitment_id(ev)
        except Exception:  # pragma: no cover
            continue
        if not cid:
            continue
        entry = cards.get(cid)
        if entry is not None:
            age, has_default, ledger_ev = entry
            out[cid] = nrq._lifetime_ask(ledger_ev, age, has_default)
        else:
            out[cid] = dict(ev)
    return out


def _offerable(row: dict, workspace_root, *, now_iso: str | None = None,
               ledger: dict | None = None) -> bool:
    """May the evening still OFFER this queue row? (11c item 1.)

    Delegates to `question_ttl.within_lifetime` — FOLD1-B's predicate, which
    is the read side of the engine that will otherwise answer this question
    for the customer. Nothing here re-derives an age or a lifetime.

    ONE FIELD IS SUPPLIED, and it is a statement about what a queue row IS
    rather than a guess: a needs-review row is a CAPTURE-CARD question — an
    unsure extraction asking "did I hear this right?" — so it carries that
    class when it does not name one of its own. `age_days` is the queue's own
    field and `within_lifetime` believes it rather than re-deriving a second
    clock.

    `has_default` IS DELIBERATELY NOT ASSERTED. A queue row does not record a
    card's own likely answer (the evening pre-picks `yes` as a RENDER choice,
    which is not the same claim), and the safe direction when it is unknown is
    the branch that asks the IMPORTANCE RULE: a row that is overdue, due this
    week, or carries a client or money stays offerable however old it is —
    DESIGN_RULE §4, "whatever a fold hides, it never hides an overdue item or
    one due this week" — and only a stale capture nothing is waiting on is
    dropped. Asserting a default would skip that question entirely.

    FAILS OPEN, like the predicate it calls: no engine, an unreadable row or
    a raised read all leave the row offerable. A question nobody can date is
    a question the customer still gets to answer.
    """
    if not isinstance(row, dict):
        return True
    try:
        import question_ttl
    except Exception:  # pragma: no cover — no engine, no gate
        return True
    # M-1 — THE OBJECT COMES FROM THE SHARED PRODUCER WHEN THERE IS ONE.
    # `ledger` is `_queue_ledger_map`'s answer: the ledger event for this
    # row, already composed by `needs_review_queue._lifetime_ask` when the
    # row carries a card question. A row the map does not know (a synthetic
    # probe, an unreadable ledger) falls back to the view row, which is what
    # this function always did.
    _known = (ledger or {}).get(str(row.get("commitment_id") or ""))
    if isinstance(_known, dict):
        probe = dict(_known)
        if probe.get("age_days") is None and row.get("age_days") is not None:
            probe["age_days"] = row.get("age_days")
    else:
        probe = dict(row)
    # FIX ROUND 1 (REVIEW_EOD2 L-5). The class is supplied only when the row
    # has no class of its OWN: `question_class_of` prefers an explicit
    # `question_class` and otherwise falls through to `SHAPE_CLASSES`, so a
    # bare `setdefault` here pre-empted that fall-through and forced a capture
    # card's two days onto a row whose own shape says identity (14), money (7)
    # or hygiene (14). No queue row carries either key today; this keeps it
    # correct on the day one does.
    #
    # FIX ROUND 2 (REVIEW_EOD2 F-2). The condition asks whether the row's
    # class RESOLVES, not whether a key is PRESENT. Round 1 keyed on the
    # presence of `shape`, so a row naming a shape the engine does not know
    # (`question_class_of` -> None) was handed to `within_lifetime` with no
    # class at all and passed straight through its "nobody's to hide"
    # fall-through — escaping the gate entirely and reverting to the exact
    # every-night nag item 1 exists to stop. Asking the engine's own resolver
    # keeps L-5's fall-through intact (a KNOWN shape still keeps its own life)
    # and closes the unknown-vocabulary half in both spellings, shape and
    # class alike.
    if question_ttl.question_class_of(probe) is None:
        probe["question_class"] = question_ttl.CLASS_CAPTURE_CARD
    # M-1 — AND THE ROW THE IMPORTANCE RULE READS IS THE ONE THE PROBE CAME
    # FROM. `within_lifetime` asks the rule about `probe["row"]` when there
    # is one, so pointing it back at the queue VIEW row here would have
    # undone the whole fix: the ledger event would have carried the ids and
    # the rule would still have been handed the row that has none.
    if not isinstance(_known, dict):
        probe.setdefault("row", row)
    try:
        return bool(question_ttl.within_lifetime(probe, workspace_root,
                                                 now_iso=now_iso))
    except Exception:  # pragma: no cover — degrade to OFFERING the row
        return True


def eod_confirm_candidates(workspace_root, *, now_iso: str | None = None,
                           cap: int = MAX_EOD_QUESTIONS,
                           apply: bool = True) -> dict:
    """Up to `cap` needs-review rows End of Day may ask about tonight,
    budget-approved. NOT A PURE READ: on `apply=True` (the default, and what
    the render path passes) the shared door writes ONE
    `question_budget_spent` receipt for what it approved, exactly as every
    other asker's submission does. Pass `apply=False` to compute the rows
    without spending anything — what a caller that is only measuring wants.

    FIX ROUND 1, REVIEW_FOLD1A F-2. The first cut offered `cap * 3` rows —
    six — to `quiet.submit_questions`, which records EVERY approved
    candidate as asked and only then let this function slice the result to
    `cap`. On a `light` seat (limit 5, the shipped default posture for every
    client) one evening therefore rendered 2 questions and spent all 5,
    leaving `remaining: 0` for the rest of the week. That inverts the ruling
    it implements — M: End of Day's ≤ 2 questions draw from the Staff
    Meeting's weekly five, NOT on top of them. It now ranks first, by the
    shared door's own `rank_questions` (consequence first, DESIGN_RULE §4),
    and submits EXACTLY the ≤ `cap` rows it will render, so a light seat
    ends the evening with 3 of its 5 still in hand.

    FIX ROUND 2, REVIEW_FOLD1A R-1 — what this docstring used to claim, and
    what is true. It said "and the Staff Meeting still has them
    (`question_ttl.staff_meeting_question_ceiling`, TTL1 — the ceiling reads
    the same budget)". The ceiling read that budget's LIMIT, so it did not
    shrink when the evening spent, and the Staff Meeting never calls
    `quiet.submit_questions`, so it does not debit either: a `light` seat could
    be shown 2 + 5 = 7 questions in one week. The ceiling now reads the
    REMAINDER (`quiet.staff_meeting_question_ceiling`, on this branch; TTL1's
    copy of that function yields to it at the merge), so the evening's two DO
    shrink the Staff Meeting's page. THE OTHER HALF IS STILL OWED, AND IT IS
    FOLD1B's: the Staff Meeting submits nothing through the shared door, so
    the arithmetic is enforced on one side only. Worst case on a `light` seat
    is now 5 + 1 = 6 — the residual is TTL1's floor of one row, kept so the
    page is never empty.

    Returns `{"rows", "n_queue_total", "budget"}`:

      * `rows` — 0..cap rows, EACH carrying the queue's own fields
        (`commitment_id`, `title`, `age_days`, `review_reason`, `due`,
        `evidence`, `weak_reason`) PLUS nothing new — the evening renders
        them with the queue's own verb set, never a bespoke one.
      * `n_queue_total` — the queue's honest total (for a "+N more, ask any
        time — `needs your call`" pointer if the evening wants one; never
        itself a question).
      * `budget` — the `quiet.question_budget` snapshot BEFORE this
        submission, for a caller that wants to say why fewer than `cap`
        rows came back (budget exhausted vs. queue empty vs. everything
        already asked this week).

    Never raises on a workspace with no queue module reachable or an
    unreadable substrate — degrades to `{"rows": [], "n_queue_total": 0,
    "budget": None}`, the safe direction for a surface that must still
    close the day.
    """
    out = {"rows": [], "n_queue_total": 0, "budget": None,
           "n_past_lifetime": 0}
    try:
        from needs_review_queue import build_queue_view
        view = build_queue_view(workspace_root, now_iso=now_iso,
                                group_by="counterparty", scope="all")
    except Exception:
        return out
    rows = []
    for group in view.get("groups") or []:
        has_counterparty = (group.get("name") or "") not in ("", "(no counterparty)")
        for item in group.get("items") or []:
            row = dict(item)
            row["has_counterparty"] = has_counterparty
            rows.append(row)
    out["n_queue_total"] = view.get("total") or len(rows)
    if not rows:
        return out
    # SPEC SURFACES2_11c Lane 3 item 1 — NO QUESTION OLDER THAN ITS OWN
    # LIFETIME, dropped BEFORE the sort (FOLD1-B supplies the predicate).
    #
    # The sort below is "oldest first", which on a queue holding a
    # seventy-five-day capture means the evening offers the oldest thing on
    # the book every night — a question the expiry engine is about to answer
    # for the customer anyway. The engine's own read gate answers "may this
    # still be OFFERED", so the evening asks it rather than inventing a
    # second age rule. Ordering is untouched: consequence still wins at
    # `quiet.submit_questions`.
    n_before = len(rows)
    # ONE READ OF THE LEDGER FOR THE WHOLE SCREEN (M-1), so every row is
    # judged on the same object the fold and the queue page judge it on.
    _ledger = _queue_ledger_map(workspace_root, now_iso=now_iso)
    rows = [r for r in rows
            if _offerable(r, workspace_root, now_iso=now_iso,
                          ledger=_ledger)]
    out["n_past_lifetime"] = n_before - len(rows)
    if not rows:
        return out
    # Oldest first, to decide which candidates are OFFERED to the shared
    # budget when the queue holds more than `cap` — the same rotation rule
    # the Staff Meeting's own section uses ("oldest-first IS the rotation
    # rule — the front of the queue is what shows, so no call can be
    # suppressed forever"). This is NOT the final selection order:
    # `quiet.submit_questions` re-ranks whatever it is offered by
    # CONSEQUENCE first (`quiet.rank_questions` — a row with both a
    # counterparty and a date outranks one with only a counterparty), which
    # is DESIGN_RULE §4's "importance first, not recency" and correctly
    # overrides this pre-sort. `age_days` is the queue's own field;
    # missing/odd values sort last, never crash the ask.
    rows.sort(key=lambda r: -(r.get("age_days") or 0))
    try:
        import quiet
        cands = [{"commitment_id": r["commitment_id"],
                  "has_counterparty": r["has_counterparty"],
                  "has_date": bool(r.get("due")),
                  "due": r.get("due")}
                 for r in rows if r.get("commitment_id")]
        # RANK FIRST, THEN SUBMIT EXACTLY WHAT WILL RENDER (fix round 1,
        # F-2). `rank_questions` is the shared door's OWN ranker and is
        # applied again inside `submit_questions` — running it here first is
        # idempotent, and it is the only way to know which ≤ cap rows the
        # door would choose BEFORE handing it a list it will mark as asked.
        # Offering more than the evening can show is what spent the whole
        # week's allowance to ask two questions.
        chosen = quiet.rank_questions(cands)[: max(int(cap), 0)]
        out["budget"] = quiet.question_budget(workspace_root, now_iso=now_iso)
        if not chosen:
            return out
        sub = quiet.submit_questions(workspace_root, quiet.ASKER_EOD_CONFIRM,
                                     chosen, now_iso=now_iso, apply=apply)
        approved_ids = [r["commitment_id"] for r in sub.get("render") or []]
    except Exception:
        approved_ids = []
    by_id = {r["commitment_id"]: r for r in rows if r.get("commitment_id")}
    out["rows"] = [by_id[cid] for cid in approved_ids if cid in by_id][:cap]
    return out


# ---------------------------------------------------------------------------
# THE RENDER — End of Day's ≤2 pre-picked questions (fix round 1)
# ---------------------------------------------------------------------------
#
# M's design rule of 2026-09-06, in his own words: "Staff meeting can be the
# surface for questions and confirmations... I don't mind a couple of those
# questions appearing on end of day." The reviewer's reading, adopted as the
# ruling (R-N10-3, REVIEW_FOLD1A §8): CUT-PLATE's `ScreenShapeError` is a
# HOLD on the DECISIONS BLOCK — the four-verb confirm menu — and stays exactly
# as it is for that and for any NEW question class. The fence is widened BY
# CITATION and narrowly: at most 2 rows, drawn from the Staff Meeting's
# existing weekly five, each with the likely answer pre-picked, each
# answerable in one word through the queue's existing writers, and never a
# question the Staff Meeting was not already going to ask.
#
# What that rules OUT, deliberately: numbers (`end_of_day.NUMBERED_BLOCKS` is
# `()` and a `[n]` tap is refused in plain English on this screen — a numbered
# row here would resolve against a map nobody built), a widget (`compose_screen`
# returns `widget: None` and the day-close posts text), and a three-way menu
# (DESIGN_RULE §2: "one tap each, the likely answer pre-picked, no three-way
# menus"). The row's own four verbs still exist and are still answered through
# `needs_review_queue.confirm_items` / `done_items` / `drop_items` /
# `not_mine_items` — the evening simply names the two that fit a one-word
# answer, and the queue carries the rest on demand.
#
# The lines are composed HERE, not in the orchestrator prose, for the same
# reason `folded_fire_line` is: the fence in `end_of_day.screen_shape_violations`
# allows EXACTLY the question lines the declared block contributed, so a line
# the model wrote itself still reds.

#: The header, by count. Never "0 before you close" — the block is drop-empty.
#: The PICK is stated here, once, rather than repeated on every row: the
#: likely answer is `yes` (the queue's own `confirm` verb — an unsure
#: extraction is asking "did I hear this right?"), and saying one word takes
#: it. Two doors only, never three (DESIGN_RULE §2: "no three-way menus").
_HEADERS = {
    1: "One before you close — I have picked `yes`; one tap takes it.",
    2: "Two before you close — I have picked `yes` for both; one tap takes each.",
}

#: The footer, by count: how to take the pick, how to refuse it BY NUMBER,
#: where the rest live, and that either answer is reversible.
#:
#: FIX ROUND 2 (REVIEW_FOLD1A R-2). These used to read "Say `yes` to take
#: both, or name the one that is wrong" — and `yes` was a phrase no skill
#: claimed, there was no widget to tap, and nothing was numbered, so the only
#: door that actually worked was `needs your call`. Every phrase below is now
#: claimed: the tap is the widget's one action per row
#: (`build_eod_questions_view`), and the typed forms are NUMBERED against the
#: block's own numbering, resolved by `end_of_day.resolve_choice` against the
#: map this fire's receipt records. Bare `yes` is never printed, because a
#: bare `yes` is a phrase the whole product would have to claim.
_FOOTERS = {
    1: ("Tap the tick to take it, or say `yes 1` / `no 1` by number — "
        "`needs your call` has the rest, and `undo` reverses either."),
    2: ("Tap either tick, or answer by number — `yes 1`, `no 2` — "
        "`needs your call` has the rest, and `undo` reverses either."),
}


#: SPEC SURFACES2_11c Lane 3 item 3 — the header and footer when the block
#: carries a self-scored coaching question. The confirm spellings above claim
#: a pre-picked `yes`, which is true of a confirm and false of a score, so a
#: block holding one of these says what is actually on it.
_HEADER_WITH_COACH = {
    1: "One before you close.",
    2: "Two before you close.",
}
_FOOTER_WITH_COACH = (
    "Answer by number — a confirm takes `yes 1` or `no 1`, and the scored one "
    "takes `score 1 7` for a seven. Saying nothing leaves both as they are.")

#: The marker a coaching row carries. `question_line` reads it, the receipt map
#: records it, and `end_of_day.resolve_coach_score` refuses any row without it
#: — a score answered onto a confirm row would close a promise.
COACH_ROW_KIND = "coach_score"

#: The question itself. ONE number, the seat's own words for the behaviour, and
#: no scale the reader has to remember: the range is in the sentence.
COACH_QUESTION = "How did {behaviour} go today, 1–10?"


def question_line(row: dict) -> str:
    """ONE row, rendered. It ENDS in the question mark ON PURPOSE: that is
    exactly the shape `end_of_day.screen_shape_violations` scans for, so
    these lines are governed by the fence and permitted only because the
    declared block hands them over as its allowance. A row phrased to dodge
    the scanner — the mark mid-line, or a leading `- ` (the scanner's own
    plate-row carve-out) — would slip past the fence entirely, and a fence
    that cannot see the one class it was widened for is not a fence.

    A COACHING row carries its own sentence (11c item 3) because it is not a
    confirm and must not be dressed as one: it is rendered through this same
    function, into the same declared block, so the fence governs it exactly
    as it governs the confirms and nothing new had to be allowed anywhere.
    """
    row = row or {}
    if row.get("kind") == COACH_ROW_KIND:
        behaviour = str(row.get("behaviour") or "").strip()
        return COACH_QUESTION.format(behaviour=behaviour or "it")
    title = str(row.get("title") or "").strip() or "a capture"
    return f"{title} — did I get that right?"


def render_eod_questions(rows) -> dict:
    """The declared `eod_questions` block: `{"lines", "questions", "n"}`, or
    the empty shape when there is nothing to ask.

    `questions` is the EXACT list of question-shaped lines this block
    contributes — `end_of_day.compose_screen` hands it straight to
    `screen_shape_violations` as the allowance, so the fence stays real for
    every other line on the screen. A seat with no rows renders NOTHING: no
    header, no footer, no "nothing to confirm" line."""
    rows = [r for r in (rows or []) if isinstance(r, dict)][:MAX_EOD_QUESTIONS]
    if not rows:
        return {"lines": [], "questions": [], "n": 0, "rows": []}
    questions = [question_line(r) for r in rows]
    has_coach = any(r.get("kind") == COACH_ROW_KIND for r in rows)
    headers = _HEADER_WITH_COACH if has_coach else _HEADERS
    header = headers.get(len(rows)) or headers[2]
    footer = (_FOOTER_WITH_COACH if has_coach
              else (_FOOTERS.get(len(rows)) or _FOOTERS[2]))
    return {"lines": [header] + questions + [footer],
            "questions": questions, "n": len(questions),
            # FIX ROUND 2 (R-2) — THE MAP. `n` is the number printed beside
            # the row and the number a typed answer names; `id` is the queue
            # row it resolves to. `end_of_day.log_end_of_day_receipt` records
            # this list on the fire's receipt and `end_of_day.choice_map`
            # reads it back, so an answer resolves POSITIONALLY against what
            # THIS fire rendered — the same safety property every other tap
            # on this surface has (CLOSEID1), never a string match.
            "rows": [{"n": i, "id": str(r.get("commitment_id") or ""),
                      "title": str(r.get("title") or ""),
                      # 11c item 3 — WHICH KIND OF ANSWER THIS ROW TAKES.
                      # Recorded on the map so the resolver can refuse a score
                      # onto a confirm row and a `yes` onto a scored one; the
                      # confirm rows carry the same spelling they always did
                      # (absent), so every receipt already on disk reads the
                      # same way it did yesterday.
                      **({"kind": COACH_ROW_KIND,
                          "behaviour": str(r.get("behaviour") or "")}
                         if r.get("kind") == COACH_ROW_KIND else {})}
                     for i, r in enumerate(rows, start=1)]}


# ---------------------------------------------------------------------------
# SPEC SURFACES2_11c Lane 3 item 3 — THE SELF-SCORED QUESTION
# ---------------------------------------------------------------------------
#
# `coaching_doors.surface_deltas` promises a seat that opens the door "up to
# two self-scored questions a day, out of the five the Staff Meeting already
# budgets. Never an extra one." Three things make that literally true here:
#
#   * the cap is the EVENING's existing two (`MAX_EOD_QUESTIONS`), and the
#     CONFIRMS TAKE IT FIRST (ruling R-9). A confirm is an act waiting on the
#     reader; a coaching question is not, so the coach asks with what is left
#     and asks nothing on an evening with two confirms due.
#   * the submission goes through `quiet.submit_questions` on its own asker,
#     so it spends the same weekly five every other asker spends. A spent week
#     means no coaching question, exactly as it means no confirm.
#   * an OBSERVED seat has no slot at all, whatever the budget says. The
#     shape is the switch (§7), so `turn off coaching` ends this with no
#     second key to unset.

#: Slots by shape. Observed seats are absent by design rather than zero-valued
#: elsewhere: a shape this map does not know is observed.
COACH_CAP_BY_SHAPE = {"observed": 0, "named": 1, "coached": 2}

#: The event one answer writes, and the batch it carries.
COACH_ANSWER_EVENT = "coaching_answer"
COACH_BATCH_PREFIX = "eodc_"
COACH_SCORE_MIN = 1
COACH_SCORE_MAX = 10

#: RULING R-5, default taken: the answer is recorded, and it has NO undo
#: tonight. `brain_undo.REVERSERS` belongs to no lane on this train, so rather
#: than promise a reversal nothing performs, the receipt says plainly that the
#: answer is a note and the next evening's answer stands in front of it.
COACH_ANSWER_NO_UNDO = ("It is a note to yourself, not a change to anything, "
                        "so there is nothing to undo.")


def coach_question_slots(*, shape: str, n_confirms: int) -> int:
    """How many self-scored questions this evening may ask (ruling R-9).

    The shape's cap, bounded by what the evening's two have left after the
    confirms. Never negative, never more than the evening's own cap however
    many shapes are added later.
    """
    cap = COACH_CAP_BY_SHAPE.get(str(shape or ""), 0)
    try:
        taken = max(0, int(n_confirms))
    except (TypeError, ValueError):  # pragma: no cover — a bad count is zero
        taken = 0
    return max(0, min(cap, MAX_EOD_QUESTIONS - taken))


def coach_answered_on(workspace_root, for_date: str) -> bool:
    """True when this seat already scored the behaviour for `for_date`.

    A re-fire of the same evening — a manual `end of day` after the scheduled
    one — must not ask a question the reader has already answered, and it must
    not spend a second question out of the week to do it. Read through
    `events_io` like every other full-history read on this surface.
    """
    day = str(for_date or "").strip()[:10]
    if not day:
        return False
    try:
        import events_io
        rows = events_io.load_all(workspace_root)
    except Exception:  # noqa: BLE001 — an unreadable book asks nothing extra
        return False
    for ev in rows or []:
        if not isinstance(ev, dict) or ev.get("type") != COACH_ANSWER_EVENT:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if str(data.get("for_date") or "")[:10] == day:
            return True
    return False


def eod_coach_candidates(workspace_root, *, for_date: str, n_confirms: int,
                         now_iso: str | None = None, apply: bool = True,
                         shape: str | None = None) -> dict:
    """The evening's self-scored question rows — 0, or 1 — budget-approved.

    Returns `{"rows", "slots", "shape", "behaviour", "budget"}`. The ROW COUNT
    is bounded by `slots` AND by how many behaviours the seat actually
    declared, which today is one: the walk asks for "the one thing you want to
    be better at this quarter" and stores one sentence. So a coached seat with
    both slots free is offered its one question and the second slot goes
    unspent rather than being filled with a question nobody asked for — a
    question is a defect to be justified (DESIGN_RULE §5), and there is no
    second behaviour to justify one.

    Never raises: an evening that cannot read the coaching store asks nothing,
    which is the same evening every observed seat has.
    """
    out = {"rows": [], "slots": 0, "shape": "observed", "behaviour": "",
           "budget": None}
    try:
        import eod_synthesis as _syn
        import coaching_doors as _doors
        shape = shape if shape in COACH_CAP_BY_SHAPE else \
            _doors.coaching_shape(workspace_root)
    except Exception:  # noqa: BLE001
        return out
    out["shape"] = shape = shape if shape in COACH_CAP_BY_SHAPE else "observed"
    out["slots"] = slots = coach_question_slots(shape=shape,
                                                n_confirms=n_confirms)
    if slots <= 0:
        return out
    try:
        arc = _syn.behaviour_arc(workspace_root)
    except Exception:  # noqa: BLE001
        arc = None
    if not arc:
        return out
    out["behaviour"] = behaviour = str(arc.get("label") or "")
    if coach_answered_on(workspace_root, for_date):
        return out
    row = {"kind": COACH_ROW_KIND, "behaviour": behaviour,
           "commitment_id": _syn.ref_behaviour(arc.get("arc_id")),
           "for_date": str(for_date or "")}
    try:
        import quiet
        out["budget"] = quiet.question_budget(workspace_root, now_iso=now_iso)
        sub = quiet.submit_questions(
            workspace_root, quiet.ASKER_EOD_COACH,
            [{"commitment_id": row["commitment_id"],
              # A behaviour has no counterparty and no date: it ranks last
              # among candidates, which is right — a confirm about somebody
              # else's promise outranks a note to yourself.
              "has_counterparty": False, "has_date": False}],
            now_iso=now_iso, apply=apply)
        approved = [r for r in (sub.get("render") or [])]
    except Exception:  # noqa: BLE001 — no budget door, no question
        approved = []
    if approved:
        out["rows"] = [row][:slots]
    return out


def _mint_coach_batch_id(now_iso=None) -> str:
    """`eodc_<UTC to the second>-<8 hex>` — one answer, one batch id, minted in
    the same shape every other batch id on this rail uses."""
    return _mint_batch_id(now_iso).replace(BATCH_PREFIX, COACH_BATCH_PREFIX, 1)


def apply_coach_answer(workspace_root, *, behaviour: str, score,
                       for_date: str, answered_by: str,
                       source_ref: str | None = None, now_iso=None,
                       batch_id: str | None = None) -> dict:
    """Record ONE self-scored answer as a `coaching_answer` event.

    `{"ok", "score", "behaviour", "batch_id", "receipt", "refusal", "undo"}`.
    The score is a whole number from one to ten and nothing else: a refusal
    names the range rather than clamping, because a clamped score is a number
    the seat did not give.

    RULING R-5, DEFAULT TAKEN — NO UNDO, AND THE RECEIPT SAYS SO. The event
    carries no `brain_change_class`, so `brain_undo` does not collect it into
    a batch it cannot reverse, and the acknowledgement says in plain words
    that there is nothing to undo. `brain_undo.py` is frozen on this train;
    the two lines that would register a reverser are named in this lane's
    record as a seam rather than written across an ownership line.
    """
    out = {"ok": False, "score": None, "behaviour": str(behaviour or ""),
           "batch_id": None, "receipt": False, "refusal": None,
           "undo": COACH_ANSWER_NO_UNDO}
    try:
        value = int(str(score).strip())
    except (TypeError, ValueError):
        out["refusal"] = (f"That is not a number I can read. Answer with a "
                          f"whole number from {COACH_SCORE_MIN} to "
                          f"{COACH_SCORE_MAX}.")
        return out
    if value < COACH_SCORE_MIN or value > COACH_SCORE_MAX:
        out["refusal"] = (f"The scale is {COACH_SCORE_MIN} to "
                          f"{COACH_SCORE_MAX}. Say a number inside it.")
        return out
    out["score"] = value
    out["batch_id"] = batch_id = batch_id or _mint_coach_batch_id(now_iso)
    data = {"behaviour": out["behaviour"], "score": value,
            "for_date": str(for_date or "")[:10],
            "brain_batch_id": batch_id, "answered_by": answered_by,
            "undo": COACH_ANSWER_NO_UNDO}
    if source_ref:
        data["source_ref"] = source_ref
    try:
        from event_gate import append_event
        append_event(Path(workspace_root) / "_hq" / "data" / "events.jsonl",
                     [{"type": COACH_ANSWER_EVENT,
                       "source_skill": SOURCE_SKILL, "data": data}],
                     holder=SOURCE_SKILL)
        out["receipt"] = True
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 — loud, never fatal
        sys.stderr.write("[eod_question_budget] coaching answer not "
                         f"recorded: {exc}\n")
        out["refusal"] = ("I could not write that down just now — say it "
                          "again and I will try once more.")
    return out


def coach_answer_ack(result: dict) -> str:
    """The one sentence the fire posts back after a score. No judgement, no
    comparison with yesterday, no encouragement — the number is the reader's
    own reading and this says only that it landed."""
    r = result or {}
    if r.get("refusal"):
        return str(r["refusal"])
    if not r.get("ok"):
        return "Nothing was written down."
    return (f"Noted — {r.get('behaviour') or 'it'}, {r.get('score')} out of "
            f"{COACH_SCORE_MAX} for today. {COACH_ANSWER_NO_UNDO}")


# ---------------------------------------------------------------------------
# THE ANSWER — the widget's one tap, the numbered typed answers, the writer
# ---------------------------------------------------------------------------
#
# FIX ROUND 2, REVIEW_FOLD1A R-2. Round 1 rendered the two questions and
# routed nothing: `yes` was a declared trigger of no skill, there was no
# widget, `end_of_day.NUMBERED_BLOCKS` was `()` so no `[n]` tap resolved, and
# `needs_review_queue.confirm_items` — the writer that records a `yes` — was
# named nowhere in the day-close's own instructions. The budget was spent at
# render, correctly, so a `light` seat paid 2 of its 5 weekly questions for
# two questions it could not answer where they were asked.
#
# The answer path is built from parts that already ship, never re-implemented:
#
#   THE ROW SHAPE is the Staff Meeting's own
#   (`needs_review_queue.staff_meeting_group_section`): `name`, the
#   `context_tag` from `needs_review_queue._row_context_tag`, `data.id`
#   verbatim, `actions`. The ONE difference is the verb list: the Staff
#   Meeting offers all four `QUEUE_ROW_ACTIONS`; the evening offers exactly
#   ONE — the pre-picked answer — because DESIGN_RULE §2 is "one tap each,
#   the likely answer pre-picked, no three-way menus". Everything else the
#   row can be answered as is still answerable, on the queue, on demand.
#
#   THE TRANSPORT is `widget_transport.render_and_persist` (the transport runs
#   every gate), the same call `attribution_doors.render_card_questions` and
#   `held_review.render_review_page` make.
#
#   THE WRITERS are the queue's own — `needs_review_queue.confirm_items` for a
#   `yes`, `drop_items` for a `no` — so a row answered at the day-close is the
#   same write, through the same bulk-accept fence, as the same row answered
#   on the queue or in the Staff Meeting. `skip` writes NOTHING and says so.
#
#   THE UNDO is the registered one: the confirm stamps `commitment_confirm`
#   and the drop `commitment_close`, both under ONE batch id, so a bare `undo`
#   reverses the whole evening's answer through reversers that already exist.
#   Nothing here appends a hand-built event.

#: The widget's ONE row action — the pre-picked answer, as one tap. The
#: canonical `confirm` verb (`chat_output_renderer.CANONICAL_ACTIONS`), which
#: is the same verb this row carries on the queue and in the Staff Meeting.
EOD_QUESTION_ACTION = "confirm"

#: The typed answers, by number, and what each one means. `yes` is the tap's
#: twin; `no` drops; `skip` leaves the row alone and writes nothing (the
#: below-the-cut default every asker already has — an unanswered question
#: takes its default rather than nagging).
ANSWER_YES = "yes"
ANSWER_NO = "no"
ANSWER_SKIP = "skip"
EOD_ANSWERS = (ANSWER_YES, ANSWER_NO, ANSWER_SKIP)

#: The block key, spelled once. `end_of_day` imports it rather than declaring
#: a second copy — the same rule the cap already follows.
QUESTION_BLOCK = "eod_questions"

#: The undo batch's prefix and the receipt this answer writes.
BATCH_PREFIX = "eodq_"
BATCH_SALT_BYTES = 4
ANSWER_RECEIPT_TYPE = "eod_question_answered"
SOURCE_SKILL = "end-of-day"
#: The change classes the two writers stamp — both already registered in
#: `brain_undo.REVERSERS`, which is why this surface needs no reverser of its
#: own (add beside, never displace).
CONFIRM_CHANGE_CLASS = "commitment_confirm"
DROP_CHANGE_CLASS = "commitment_close"


def _mint_batch_id(now_iso=None) -> str:
    """`eodq_<UTC to the second>-<8 hex>` — this answer's batch, minted once
    per gesture in the mint shape every other batch id on this rail uses."""
    import secrets
    from datetime import datetime, timezone
    when = None
    if now_iso:
        try:
            from event_time import parse_ts
            when = parse_ts(now_iso)
        except Exception:
            when = None
    when = when or datetime.now(timezone.utc)
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(BATCH_SALT_BYTES)}"


def build_eod_questions_view(rows, *, header=None) -> dict:
    """The `widget_transport.render_and_persist` data view for the evening's
    <= 2 questions — the Staff Meeting's OWN row shape, with ONE action.

    `n` is the POSITION (1, 2), matching the block's printed numbering and
    the map `render_eod_questions` returns, because this surface resolves a
    tap positionally against its own receipt (`end_of_day.resolve_choice`);
    the queue row's id travels on `data.id` verbatim so dispatch needs no
    session state. Empty `rows` -> an empty sections list: a caller renders
    NOTHING rather than a card that asks nothing."""
    items: list = []
    try:
        from needs_review_queue import _row_context_tag
    except Exception:  # pragma: no cover — a tag is chrome, not the answer
        _row_context_tag = None
    for i, row in enumerate([r for r in (rows or []) if isinstance(r, dict)]
                            [:MAX_EOD_QUESTIONS], start=1):
        cid = str(row.get("commitment_id") or "")
        tag = ""
        if _row_context_tag is not None:
            try:
                tag = _row_context_tag(row)
            except Exception:  # pragma: no cover
                tag = ""
        items.append({
            "n": i,
            "icon": "",
            # PLAIN STRINGS. The title is the customer's own words and
            # marking them is the RENDERER's job through its own chokepoint.
            "name": question_line(row),
            "context_tag": tag,
            "data": {"id": cid},
            # STATELESS DISPATCH: the tuple carries which block it came from,
            # so `apply-choices` routes it without reading session state and
            # without having to guess from an empty `confirm_ids`.
            "context": QUESTION_BLOCK,
            "actions": [EOD_QUESTION_ACTION],
        })
    return {
        "widget_mode": "all_batch_widget",
        "source_skill": SOURCE_SKILL,
        "header": (header if header is not None
                   else ((_HEADERS.get(len(items)) or _HEADERS[2])
                         if items else "")),
        "sections": ([{"title": None, "count": None, "items": items}]
                     if items else []),
        "save_confirmation": None,
    }


def render_question_widget(workspace_root, rows, *, persist_dir=None,
                           name_hint: str = "end-of-day-questions"):
    """The <= 2 questions as ONE validated widget page, through the canonical
    transport. Returns the transport dict, or None when there is nothing to
    ask (drop-empty, all the way up — an empty frame is never data).

    Never raises: the day must still close. A transport that cannot render
    leaves the evening with its text block and the `needs your call` door,
    which is exactly what the block's own footer already names."""
    view = build_eod_questions_view(rows)
    if not view["sections"]:
        return None
    try:
        from widget_transport import render_and_persist
        pd = persist_dir or (Path(workspace_root) / "_hq" / ".system"
                             / "widgets")
        return render_and_persist(data_view=view, wrapper="fragment",
                                  persist_dir=str(pd), name_hint=name_hint)
    except Exception as exc:  # noqa: BLE001 — loud, never fatal
        sys.stderr.write("[eod_question_budget] question widget skipped: "
                         f"{exc}\n")
        return None


def apply_eod_answers(workspace_root, answers, *, answered_by: str,
                      source_skill: str = "apply-choices",
                      individually_named=(), source_ref=None,
                      now_iso=None, batch_id=None) -> dict:
    """THE one door the evening's answers land through. `answers` is
    `[{"id": <commitment id>, "answer": "yes" | "no" | "skip"}, ...]`.

    ONE batch id over the whole evening's answer, so a bare `undo` reverses
    all of it in one gesture; ONE `eod_question_answered` receipt naming what
    was written. The writes themselves are the queue's own
    (`needs_review_queue.confirm_items` / `drop_items`) — this function adds
    no policy, invents no verb and appends no hand-built event; what it adds
    is the batch, the receipt and the plain-language counts.

    `individually_named` is passed to `confirm_items` as `confirm_weak_ids`
    and it is the CALLER's claim, never widened here: the ids the customer
    actually tapped or numbered. A row answered one tap at a time IS named
    individually; a gesture that names nothing passes an empty set and a weak
    row is HELD and reported, exactly as it is on the queue.

    `skip` writes nothing at all. It is not a verb the customer has to reach
    for — an unanswered question already takes its default — it exists so the
    answer "leave it" has somewhere honest to land instead of becoming a drop.

    Returns `{"batch_id", "n_confirmed", "n_dropped", "n_skipped", "n_held",
    "n_failed", "results", "receipt"}`. Never raises on a queue that refuses a
    row: a refusal is REPORTED per row, the same contract the queue keeps."""
    from needs_review_queue import confirm_items, drop_items

    out = {"batch_id": None, "n_confirmed": 0, "n_dropped": 0, "n_skipped": 0,
           "n_held": 0, "n_failed": 0, "results": [], "receipt": False}
    rows = [a for a in (answers or []) if isinstance(a, dict) and a.get("id")]
    if not rows:
        return out
    yes_ids = [str(a["id"]) for a in rows if a.get("answer") == ANSWER_YES]
    no_ids = [str(a["id"]) for a in rows if a.get("answer") == ANSWER_NO]
    skip_ids = [str(a["id"]) for a in rows if a.get("answer") == ANSWER_SKIP]
    batch_id = batch_id or _mint_batch_id(now_iso)
    out["batch_id"] = batch_id
    for a in rows:
        if a.get("answer") not in EOD_ANSWERS:
            out["results"].append(
                {"commitment_id": str(a["id"]), "status": "unknown_answer",
                 "detail": "the evening takes " + ", ".join(EOD_ANSWERS)})
            out["n_failed"] += 1
    named = {str(x) for x in (individually_named or ())}
    if yes_ids:
        res = confirm_items(workspace_root, yes_ids, source_skill=source_skill,
                            confirm_weak_ids=tuple(i for i in yes_ids
                                                   if i in named),
                            brain_batch_id=batch_id,
                            brain_change_class=CONFIRM_CHANGE_CLASS)
        out["results"].extend(res.get("results") or [])
        out["n_confirmed"] += int(res.get("n_confirmed") or 0)
        out["n_held"] += int(res.get("n_held") or 0)
        out["n_failed"] += int(res.get("n_failed") or 0)
    if no_ids:
        res = drop_items(workspace_root, no_ids, resolved_by=answered_by,
                         source_skill=source_skill, source_ref=source_ref,
                         brain_batch_id=batch_id,
                         brain_change_class=DROP_CHANGE_CLASS)
        out["results"].extend(res.get("results") or [])
        out["n_dropped"] += int(res.get("n_dropped") or 0)
        out["n_failed"] += int(res.get("n_failed") or 0)
    for cid in skip_ids:
        out["results"].append({"commitment_id": cid, "status": "skipped"})
        out["n_skipped"] += 1
    out["receipt"] = _write_answer_receipt(workspace_root, out,
                                           answered_by=answered_by,
                                           source_ref=source_ref)
    return out


def _write_answer_receipt(workspace_root, result: dict, *, answered_by,
                          source_ref=None) -> bool:
    """ONE `eod_question_answered` row, through the gated appender (CONTRACT
    Rule 31), never by hand.

    It deliberately carries NO `brain_change_class`:
    `brain_undo._changes_for_brain_batch` collects every event stamped with
    this batch id that ALSO carries a class as one of the batch's reversible
    changes, and the real changes are the ones the queue's own writers already
    wrote. A receipt carrying the class would be counted as a phantom extra
    change in the same batch — narration only, never a change. (The same trap
    `fold_scheduled_fires._write_receipt` documents, and the same fix.)"""
    from event_gate import append_event

    if not (result.get("n_confirmed") or result.get("n_dropped")
            or result.get("n_skipped")):
        return False
    data = {"brain_batch_id": result.get("batch_id"),
            "n_confirmed": result.get("n_confirmed"),
            "n_dropped": result.get("n_dropped"),
            "n_skipped": result.get("n_skipped"),
            "n_held": result.get("n_held"),
            "answered_by": answered_by,
            "undo": "say `undo` to put these back the way they were"}
    if source_ref:
        data["source_ref"] = source_ref
    try:
        append_event(Path(workspace_root) / "_hq" / "data" / "events.jsonl",
                     [{"type": ANSWER_RECEIPT_TYPE,
                       "source_skill": SOURCE_SKILL, "data": data}],
                     holder=SOURCE_SKILL)
        return True
    except Exception as exc:  # noqa: BLE001 — loud, never fatal: it landed
        sys.stderr.write("[eod_question_budget] answer receipt failed: "
                         f"{exc}\n")
        return False


def answer_ack(result: dict) -> str:
    """The one plain sentence the fire posts back. Counts and words only —
    never an id, never an event type, never a batch id."""
    r = result or {}
    bits = []
    if r.get("n_confirmed"):
        n = r["n_confirmed"]
        bits.append(f"kept {n} on your plate" if n > 1
                    else "kept it on your plate")
    if r.get("n_dropped"):
        n = r["n_dropped"]
        bits.append(f"let {n} go" if n > 1 else "let it go")
    if r.get("n_skipped"):
        n = r["n_skipped"]
        bits.append(f"left {n} for later" if n > 1 else "left it for later")
    if r.get("n_held"):
        n = r["n_held"]
        bits.append(f"held {n} back — there was not enough behind "
                    f"{'them' if n > 1 else 'it'} to be sure")
    if not bits:
        return "Nothing changed."
    return " and ".join(bits).capitalize() + " — say `undo` to reverse that."


__all__ = ["MAX_EOD_QUESTIONS", "eod_confirm_candidates", "_offerable",
           # SPEC SURFACES2_11c Lane 3 item 3 — the self-scored question.
           "COACH_ROW_KIND", "COACH_QUESTION", "COACH_CAP_BY_SHAPE",
           "COACH_ANSWER_EVENT", "COACH_SCORE_MIN", "COACH_SCORE_MAX",
           "COACH_ANSWER_NO_UNDO", "coach_question_slots",
           "coach_answered_on", "eod_coach_candidates", "apply_coach_answer",
           "coach_answer_ack",
           "question_line", "render_eod_questions",
           "QUESTION_BLOCK", "EOD_QUESTION_ACTION", "EOD_ANSWERS",
           "ANSWER_YES", "ANSWER_NO", "ANSWER_SKIP",
           "ANSWER_RECEIPT_TYPE", "CONFIRM_CHANGE_CLASS", "DROP_CHANGE_CLASS",
           "build_eod_questions_view", "render_question_widget",
           "apply_eod_answers", "answer_ack"]
