#!/usr/bin/env python3
"""SPEC EODSYNTH1 — the End of Day says how the day went instead of scoring it.

WHAT THIS MODULE IS
-------------------
The RENDER LAYER of the evening bookend, and nothing below it. Every number it
prints was already computed by `end_of_day` and is already carried by the
`pack_run` receipt; this module decides which of them reach a sentence and what
that sentence is allowed to say. Nothing here fetches, nothing here writes.

M's ruling (2026-08-23): *"I don't think we should score it … synthesize how the
day went … takeaways … what today meant in the bigger picture."* The score is
UN-RENDERED, not unbuilt — `compute_score` and `compute_ledger` still run, still
land on the receipt, and weekly-recap still reads them. What changed is that no
sentence on the evening surface is allowed to contain one.

THE THREE TIERS
---------------
  Tier 1  what happened — a grounded paragraph over today's own rows.
  Tier 2  what it meant — today's rows joined to arcs the substrate DECLARES
          (objectives, the day's stated intent, workstream status, deal stage).
          Never an arc the model infers, and omitted entirely when nothing
          joins rather than padded.
  Tier 3  precedent echoes — at most two, each citing a prior record BY ID,
          labelled as inference. Absent by default.

GROUNDING IS THE BUILD, NOT A FOOTNOTE (SPEC §3)
------------------------------------------------
Synthesis is model-shaped prose and model-shaped prose is exactly where this
product's past bugs live: fabricated speaker attribution on label-free
transcripts, freelance narration of numbers nobody computed. So every sentence
this module emits is a `Sentence` carrying its `refs`, and a sentence whose ref
set is EMPTY is dropped before it can be rendered — by `drop_unreferenced`,
which the composer calls unconditionally. There is no path from this module to
a chat turn that skips it, which is the point: a rule the composer can forget is
not a rule.

Six pins, each proven in `tests/run_eodsynth1_test.py` by REMOVING the fence and
watching the named check go red:

  1. An unreferenced sentence never renders (`drop_unreferenced`).
  2. A quote is admitted only from a LABELLED transcript, and never assembled
     across speakers (`admit_quote`). The workspace's own binding rule: the
     meeting connector fabricates attribution on label-free transcripts.
  3. Tier 2 joins only to arcs present in `declared_arcs`' output — the
     substrate's own declarations. An arc-shaped string with no declaration
     behind it is refused (`join_to_arcs`).
  4. Tier 3 refuses an echo with no precedent id, and rations to two
     (`compute_echoes`).
  5. The numbers in the prose are READ OFF THE LEDGER DICT the receipt carries
     (`ledger_numbers`), so the F-W1 class — "closed 4" on one surface and
     "closed 0" on another, same day — cannot recur: the prose and the receipt
     are the same fields by construction.
  6. A row held out of sight by the held tier or the personal firewall never
     reaches a sentence (`visible_rows`).

And one more that is the whole ruling: `score_tokens_in` finds any rendered
score claim, and `build_synthesis` runs it over everything it is about to hand
back. A score sentence cannot leave this module.

SPEC EODARC1 (2026-08-25) — THE SYNTHESIS READS THE DAY AGAINST THE ARCS
------------------------------------------------------------------------
M's verdict on the first live EODSYNTH1 render: every line was true, grounded,
and about a ROW; not one was about the day. **A recap lists what changed; a
synthesis says what it means for what you are running.** So Tier 2 stopped
being a bare join and became the ARC READ, in this order: which arcs moved
today (grounded in what happened), which consequence-carrying arcs did NOT
move (what is waiting, and on whom), and where the day's weight went — one
sentence relating effort to arcs. Prose only, ZERO new interactions (the
day-close asks nothing since CUT-PLATE — a stated tomorrow renders as fact), and a fence —
`drop_rows_only` — under which a sentence with no arc attached cannot render
in the section; the one exemption is the honest empty-day line, which is the
section saying "no arc moved" rather than a row wearing a heading.

Ruling 3, verbatim intent: deals are read as CONTEXT, never as state. The
pipeline tracker is not in a shape to be load-bearing, so the arc read must
not depend on deal rows existing or being current — an org with recent
activity and a live thread IS an arc whether or not a deal row tracks it, and
no read in this path raises when deal state is absent, stale, or malformed.
No writes to deal state, ever.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# The blocks this module composes. These names are the SECTION NAMES the
# surface renders under, and section names are JOIN KEYS into learned config —
# see `end_of_day.migrate_section_config`, which is why a rename here is never
# a rename here alone.
# ---------------------------------------------------------------------------

BLOCK_DAY_WENT = "day_went"
BLOCK_WHAT_IT_MEANT = "what_it_meant"
BLOCK_WORTH_REMEMBERING = "worth_remembering"
BLOCK_SLIPPED_PROSE = "slipped_prose"
BLOCK_ECHOES = "echoes"

SYNTHESIS_BLOCKS = (BLOCK_DAY_WENT, BLOCK_WHAT_IT_MEANT,
                    BLOCK_WORTH_REMEMBERING, BLOCK_SLIPPED_PROSE,
                    BLOCK_ECHOES)

TIER_HAPPENED = 1
TIER_MEANT = 2
TIER_ECHO = 3

# Tier 3 is rationed. Two is the ceiling M's ruling named; the default is
# ABSENT, and this is a cap on a set that is usually empty rather than a target
# to fill.
MAX_ECHOES = 2

# Worth-remembering is 1-4 lines. Four is a bound on a curated set — the block
# exists because takeaways are not a list of every event.
MAX_WORTH_REMEMBERING = 4

# ---------------------------------------------------------------------------
# The score, un-rendered (R-1)
# ---------------------------------------------------------------------------

# THE PHRASES THAT MAY NOT REACH THE SCREEN. Each is a rendered SCORE claim —
# a grade against this morning's plan — and M's ruling is that none of them
# render, in chat or in the widget, while every one of the fields behind them
# keeps being computed onto the receipt.
#
# These are patterns rather than literals on purpose. "0 of 5 closed" is the
# shape, not the string: a build that renders "3 of 11 closed" has committed
# the same defect with different numbers, and a literal pin would be green for
# it. `LEDGER_DELTA_FLAT`'s exact words are here as a literal because that one
# IS a fixed string in `end_of_day`.
SCORE_PATTERNS = (
    # "0 of 5 closed" / "0 of 5 planned items"
    re.compile(r"\b\d+\s+of\s+\d+\b[^.\n]{0,24}\b(clos|plan|done|complete)",
               re.IGNORECASE),
    # the ledger's movement arithmetic, rendered
    re.compile(r"\bno net change\b", re.IGNORECASE),
    re.compile(r"\bbook at open\b", re.IGNORECASE),
    re.compile(r"\bopen book:\s*\d+", re.IGNORECASE),
    # "5 still open" against a plan
    re.compile(r"\b\d+\s+still\s+open\b", re.IGNORECASE),
    # an explicit grade
    re.compile(r"\bscored?\s+(?:the\s+)?day\b", re.IGNORECASE),
    re.compile(r"\b(?:you\s+)?closed\s+\d+\s+of\s+\d+", re.IGNORECASE),
)


def score_tokens_in(text: Optional[str]) -> List[str]:
    """Every rendered SCORE claim in `text`, as the matched substrings.

    Empty list means the text carries no grade. This is the fence R-1 is
    enforced by, and it takes TEXT rather than a pack so it can be pointed at
    the widget's bytes as easily as at a paragraph — the ruling covers both
    surfaces and a fence that only sees one of them is half a fence.
    """
    if not text:
        return []
    hits: List[str] = []
    for pattern in SCORE_PATTERNS:
        for m in pattern.finditer(str(text)):
            hits.append(m.group(0))
    return hits


class ScoreRenderedError(RuntimeError):
    """A composed surface carried a score claim. Loud, in code, before the post.

    Never caught inside this module. A surface that grades the day is the one
    thing this build exists to remove, and a fence that degrades to a warning
    is a fence the next refactor deletes.
    """


def assert_no_score(text: Optional[str], *, where: str = "synthesis") -> None:
    hits = score_tokens_in(text)
    if hits:
        raise ScoreRenderedError(
            f"{where}: the score is not rendered (SPEC EODSYNTH1 R-1); "
            f"found {hits!r}")


# ---------------------------------------------------------------------------
# Sentences and their refs (§3.1)
# ---------------------------------------------------------------------------

def sentence(text: str, refs: Optional[Iterable[str]] = None, *,
             tier: int = TIER_HAPPENED, kind: str = "") -> dict:
    """One synthesized sentence, carrying the rows it was derived from.

    `refs` is what makes the sentence checkable: the chat shows prose, the
    persisted brief shows the join. A sentence built without them is not a
    weaker sentence, it is an unsupportable one, and `drop_unreferenced` is
    what stops it reaching a reader.
    """
    clean = [str(r).strip() for r in (refs or []) if str(r or "").strip()]
    # Order-preserving dedup: the same row supporting a clause twice is one
    # ref, and a ref list that repeats reads as more evidence than there is.
    seen, ordered = set(), []
    for r in clean:
        if r not in seen:
            seen.add(r)
            ordered.append(r)
    return {"text": str(text or "").strip(), "refs": ordered,
            "tier": int(tier), "kind": str(kind or "")}


def drop_unreferenced(sentences: Iterable[dict]) -> dict:
    """Split sentences into the ones that may render and the ones that may not.

    Returns `{"kept": [...], "dropped": [...], "n_dropped": int}`.

    THE PIN (§3.1). A sentence whose `refs` is empty is dropped BEFORE render,
    always, with no override argument and no caller-supplied allowance. The
    suite plants an unreferenced sentence into every composer's input and
    asserts it is absent from the composed text; removing this call is what
    turns that check red by name.

    An empty `text` is dropped on the same pass, for the neighbouring reason:
    a ref list with no claim on it is a join nobody can read.
    """
    kept, dropped = [], []
    for s in (sentences or []):
        if not isinstance(s, dict):
            continue
        if s.get("refs") and str(s.get("text") or "").strip():
            kept.append(s)
        else:
            dropped.append(s)
    return {"kept": kept, "dropped": dropped, "n_dropped": len(dropped)}


def compose(sentences: Iterable[dict]) -> dict:
    """Referenced sentences → one paragraph plus the join behind it.

    Returns `{"text", "sentences", "refs", "n_dropped"}`. `text` is what the
    chat shows; `sentences` and `refs` are what the persisted brief carries,
    which is where the claim becomes checkable.
    """
    split = drop_unreferenced(sentences)
    kept = split["kept"]
    text = " ".join(s["text"] for s in kept).strip()
    refs: List[str] = []
    for s in kept:
        for r in s["refs"]:
            if r not in refs:
                refs.append(r)
    return {"text": text, "sentences": kept, "refs": refs,
            "n_dropped": split["n_dropped"]}


# ---------------------------------------------------------------------------
# Refs — one spelling, minted here (§3.1)
# ---------------------------------------------------------------------------

def ref_commitment(cid) -> str:
    return f"commitment:{str(cid).strip()}" if str(cid or "").strip() else ""


def ref_event(seq_or_id) -> str:
    return f"event:{str(seq_or_id).strip()}" if str(seq_or_id or "").strip() else ""


def ref_meeting(mid) -> str:
    return f"meeting:{str(mid).strip()}" if str(mid or "").strip() else ""


def ref_decision(did) -> str:
    return f"decision:{str(did).strip()}" if str(did or "").strip() else ""


def ref_objective(oid) -> str:
    return f"objective:{str(oid).strip()}" if str(oid or "").strip() else ""


def ref_thread(tid) -> str:
    return f"thread:{str(tid).strip()}" if str(tid or "").strip() else ""


def ref_org(oid) -> str:
    return f"org:{str(oid).strip()}" if str(oid or "").strip() else ""


def ref_window(for_date) -> str:
    """SPEC EODARC1 — the ref the honest empty-day line carries: the day
    window the arc read looked at. An empty day's claim is about the WINDOW,
    not about a row, and a claim with no ref does not render (§3.1)."""
    return f"window:{str(for_date).strip()}" if str(for_date or "").strip() else ""


def ref_ledger(field: str) -> str:
    """A ref onto the LEDGER FIELD a number came from (§3.5).

    A counted sentence's evidence is the field, not a row: "four promises
    closed" is supported by `ledger.n_closed` and by nothing else, and naming
    the field is what makes the prose and the receipt provably the same number.
    """
    return f"ledger:{str(field).strip()}" if str(field or "").strip() else ""


# ---------------------------------------------------------------------------
# The numbers (§3.5) — read off the ledger the receipt carries
# ---------------------------------------------------------------------------

# The ledger fields the prose is allowed to count from. Spelled once, here, and
# read by key: a composer that recomputed a count from rows would be a SECOND
# source for a number the receipt already carries, which is the F-W1 defect
# ("closed 4" on the Staff Meeting and "0 closed" on the End of Day, same day).
LEDGER_COUNT_FIELDS = ("n_closed", "n_opened", "n_dropped")
LEDGER_BOOK_FIELDS = ("book_at_open", "book_now")


def ledger_numbers(ledger: Optional[dict]) -> dict:
    """The counts the prose may use, read STRICTLY off the ledger dict.

    Returns `{field: int|None}` over `LEDGER_COUNT_FIELDS + LEDGER_BOOK_FIELDS`.
    Nothing is derived, nothing is defaulted to zero — a missing field comes
    back `None` and the sentence that wanted it is simply not composed, which
    is the honest outcome and the one that keeps "I could not count" distinct
    from "the count was zero".

    THE PIN (§3.5): mutate `ledger["n_closed"]` and the composed paragraph
    changes. That is what proves the prose is reading this dict rather than
    recounting the rows beside it.
    """
    src = ledger if isinstance(ledger, dict) else {}
    out = {}
    for field in LEDGER_COUNT_FIELDS + LEDGER_BOOK_FIELDS:
        value = src.get(field)
        out[field] = (value if isinstance(value, int)
                      and not isinstance(value, bool) else None)
    return out


# ---------------------------------------------------------------------------
# Quotes (§3.2)
# ---------------------------------------------------------------------------

class QuoteRefused(ValueError):
    """A quote was asked for that this build will not render."""


def transcript_is_labelled(transcript: Optional[dict]) -> bool:
    """Does this transcript carry real per-speaker labels?

    `{"labelled": True}` is the only thing that says yes, and it has to be
    STATED — an absent flag is a no. The workspace's own binding rule is that
    the meeting connector fabricates speaker attribution on label-free
    transcripts, so an unlabelled transcript is not a transcript with weaker
    attribution, it is one whose attribution is invented.
    """
    return bool(isinstance(transcript, dict) and transcript.get("labelled"))


def admit_quote(text: str, *, transcript: Optional[dict],
                speaker: Optional[str] = None,
                speakers: Optional[Sequence[str]] = None) -> dict:
    """Return the renderable form of a quote, or refuse it (§3.2).

    Returns `{"mode": "quote"|"paraphrase", "text", "speaker"}`.

    Two refusals and one downgrade:

      * MORE THAN ONE SPEAKER → `QuoteRefused`, unconditionally. A quote
        assembled across speakers is a sentence nobody said, and there is no
        transcript quality that makes one safe.
      * an UNLABELLED transcript → downgraded to `paraphrase` with NO speaker.
        The words may still inform a sentence; the attribution may not travel
        with them.
      * a labelled transcript with one speaker → the quote, attributed.
    """
    named = [str(s).strip() for s in (speakers or ([speaker] if speaker else []))
             if str(s or "").strip()]
    if len(set(named)) > 1:
        raise QuoteRefused(
            "a quote may never be assembled across speakers (SPEC EODSYNTH1 "
            f"§3.2); got {sorted(set(named))!r}")
    body = str(text or "").strip()
    if not transcript_is_labelled(transcript):
        return {"mode": "paraphrase", "text": body, "speaker": None}
    return {"mode": "quote", "text": body,
            "speaker": named[0] if named else None}


# ---------------------------------------------------------------------------
# The visibility fence (§3.6)
# ---------------------------------------------------------------------------

def visible_rows(rows: Iterable[dict], *, held_ids: Optional[Iterable[str]] = None,
                 workspace_root=None) -> dict:
    """Drop every row held out of sight, before any of them reaches a sentence.

    Returns `{"rows": [...], "n_withheld": int, "reasons": {...}}`.

    Two fences, both existing, applied HERE because a synthesized paragraph is
    a new door onto rows that were already fenced out of the row-lists:

      * the HELD TIER — `data.held` on the row itself, plus the `held_ids` this
        fire just held. Same double fence `compute_confirm` keeps, and for the
        same reason: "out of sight" is a property of the surfaces, so a
        paragraph that names a held row makes the flip a lie with extra steps.
      * the PERSONAL FIREWALL — `personal_leak.is_personal`. A personal-account
        row may render in the row-lists under the R9 ephemeral rule and writes
        nothing; it has no business inside a narrative sentence about the
        business day, which is a durable artifact saved to the brief.

    A firewall read that raises withholds nothing EXTRA and says so in
    `reasons`: a fence that crashes the fire is a fence someone removes. It
    never fails open on the held check, which is the one that is pure data on
    the row.

    WHAT THE TWO HELD BRACES ACTUALLY REACH (review, 2026-08-23 — stated here
    because a fence that reads wider than it is, is a fence nobody re-checks).
    `held_ids` is the LIVE brace on every driver call: it carries the ids this
    fire held and it is what stops them. The `data.held` / `row.held` brace
    fires only on rows that still CARRY that marker — the capture-routing
    lanes do, and hand-built rows do, but the needs-attention lane is a
    PROJECTION (`commitment_state.compute_brief_state`) whose rows are rebuilt
    from `commitment_id` / `title` / `due` / `overdue` and carry no held key
    and no `data` sub-dict. So a row held on a PREVIOUS fire is not stopped
    here; it is stopped one layer up, because a held capture is a
    `pending_review` row and the you-owe lane excludes those. That upstream
    exclusion is the load-bearing one. Widening this brace to the durable
    record (`held_tier.load_held`) is a design call about whether the held
    store gets a second reader, not a line to add quietly.
    """
    held = {str(i).strip() for i in (held_ids or []) if str(i or "").strip()}
    out, reasons = [], {"held": 0, "personal": 0, "firewall_unreadable": 0}
    masks = personal_ids = None
    firewall_ok = True
    if workspace_root is not None:
        try:
            import personal_leak as _pl
            personal_ids = _pl.personal_tie_ids(workspace_root)
        except Exception:  # noqa: BLE001 — see the docstring
            firewall_ok = False
            reasons["firewall_unreadable"] = 1
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else row
        if data.get("held") or row.get("held"):
            reasons["held"] += 1
            continue
        rid = str(row.get("commitment_id") or data.get("commitment_id")
                  or data.get("id") or "").strip()
        if rid and rid in held:
            reasons["held"] += 1
            continue
        if workspace_root is not None and firewall_ok:
            try:
                import personal_leak as _pl
                if _pl.is_personal(row, masks=masks, personal_ids=personal_ids):
                    reasons["personal"] += 1
                    continue
            except Exception:  # noqa: BLE001
                reasons["firewall_unreadable"] = 1
        out.append(row)
    return {"rows": out,
            "n_withheld": reasons["held"] + reasons["personal"],
            "reasons": reasons}


# ---------------------------------------------------------------------------
# Tier 1 — how the day went
# ---------------------------------------------------------------------------

# Every template below counts from a LEDGER FIELD and says which one in its
# ref. None of them contains a denominator: "four promises closed" is a fact
# about today, "four of nine closed" is a grade, and the difference is the
# whole ruling.
T1_CLOSED = "{n} {noun} closed today."
T1_CLOSED_NAMED = "{n} {noun} closed today, including {names}."
T1_OPENED = "{n} new {noun} landed on the book."
T1_DROPPED = "{n} {noun} you let go."
T1_MEETINGS = "You were in {n} {noun}."
T1_DECISIONS = "{n} {noun} got made and written down."
T1_NOTHING = "Nothing closed today that I can see."


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def compute_day_went(*, ledger: Optional[dict],
                     closures: Optional[Iterable[dict]] = None,
                     meetings: Optional[Iterable[dict]] = None,
                     decisions: Optional[Iterable[dict]] = None,
                     name_cap: int = 3) -> dict:
    """TIER 1 — one grounded paragraph about what moved today.

    EDITORIAL, NOT EXHAUSTIVE. It names at most `name_cap` closes, because the
    point of a takeaway is that it is not the full list; the full list is the
    ledger, which is on the receipt.

    EVERY NUMBER COMES FROM `ledger` (§3.5), through `ledger_numbers`, and
    carries `ledger:<field>` as its ref. Nothing here counts the rows it was
    handed — the rows supply NAMES and their own refs, never a count. That
    asymmetry is the fence: two counts of one thing is how the same day reads
    "closed 4" on one surface and "0 closed" on another.
    """
    nums = ledger_numbers(ledger)
    rows = [c for c in (closures or []) if isinstance(c, dict)]
    named = [c for c in rows
             if str(c.get("title") or "").strip()
             and str(c.get("resolution") or "done").lower() != "dropped"]

    out: List[dict] = []

    n_closed = nums["n_closed"]
    if isinstance(n_closed, int) and n_closed > 0:
        noun = _plural(n_closed, "promise", "promises")
        refs = [ref_ledger("n_closed")]
        picks = named[:name_cap] if name_cap else []
        for c in picks:
            r = ref_commitment(c.get("commitment_id")) or ref_event(c.get("ts"))
            if r:
                refs.append(r)
        if picks:
            names = _english_list([str(c["title"]).strip() for c in picks])
            out.append(sentence(T1_CLOSED_NAMED.format(n=n_closed, noun=noun,
                                                       names=names),
                                refs, tier=TIER_HAPPENED, kind="closed"))
        else:
            out.append(sentence(T1_CLOSED.format(n=n_closed, noun=noun),
                                refs, tier=TIER_HAPPENED, kind="closed"))
    elif isinstance(n_closed, int) and n_closed == 0:
        # The honest empty. It is a claim about today and it is supported by
        # the same field a non-zero count would be, so it carries the same ref.
        out.append(sentence(T1_NOTHING, [ref_ledger("n_closed")],
                            tier=TIER_HAPPENED, kind="closed"))

    n_opened = nums["n_opened"]
    if isinstance(n_opened, int) and n_opened > 0:
        out.append(sentence(
            T1_OPENED.format(n=n_opened,
                             noun=_plural(n_opened, "promise", "promises")),
            [ref_ledger("n_opened")], tier=TIER_HAPPENED, kind="opened"))

    n_dropped = nums["n_dropped"]
    if isinstance(n_dropped, int) and n_dropped > 0:
        out.append(sentence(
            T1_DROPPED.format(n=n_dropped,
                              noun=_plural(n_dropped, "thing", "things")),
            [ref_ledger("n_dropped")], tier=TIER_HAPPENED, kind="dropped"))

    mrows = [m for m in (meetings or []) if isinstance(m, dict)]
    if mrows:
        refs = [r for r in (ref_meeting(m.get("meeting_id") or m.get("id"))
                            for m in mrows) if r]
        if refs:
            out.append(sentence(
                T1_MEETINGS.format(n=len(mrows),
                                   noun=_plural(len(mrows), "meeting",
                                                "meetings")),
                refs, tier=TIER_HAPPENED, kind="meetings"))

    drows = [d for d in (decisions or []) if isinstance(d, dict)]
    if drows:
        refs = [r for r in (ref_decision(d.get("decision_id") or d.get("id"))
                            for d in drows) if r]
        if refs:
            out.append(sentence(
                T1_DECISIONS.format(n=len(drows),
                                    noun=_plural(len(drows), "decision",
                                                 "decisions")),
                refs, tier=TIER_HAPPENED, kind="decisions"))

    result = compose(out)
    assert_no_score(result["text"], where="day_went")
    return result


def _english_list(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


# ---------------------------------------------------------------------------
# Tier 2 — what it meant, on DECLARED arcs only (§3.3)
# ---------------------------------------------------------------------------

# The event types that DECLARE an arc. A declaration is a row the user or a
# writer put on the substrate saying "this is a thing we are working toward" —
# never a theme a reader could infer from a cluster of activity. The list is
# the fence: an arc kind not in here cannot be joined to, whatever it looks
# like.
ARC_OBJECTIVE = "objective"
ARC_DAY_INTENT = "day_intent"
ARC_WORKSTREAM = "workstream"
ARC_DEAL = "deal"
# SPEC EODARC1 — two more declared kinds, and no inferred one:
#   org         an org RELATIONSHIP with a live thread (partnership, client,
#               vendor — anything the entity register tracks that is not the
#               workspace itself). Read from the register, never from the deal
#               tracker: ruling 3 is that an org with a live thread IS an arc
#               whether or not a deal row tracks it.
#   commitment  a consequence-carrying OPEN commitment (a due date, a named
#               counterparty, a stated blocker) that no other arc tracks. The
#               ruling's class (c): the thing you are running can be one
#               promise, and a promise sliding for a week is an arc even when
#               no thread was ever minted for it.
ARC_ORG = "org"
ARC_COMMITMENT = "commitment"
ARC_KINDS = (ARC_OBJECTIVE, ARC_DAY_INTENT, ARC_WORKSTREAM, ARC_DEAL,
             ARC_ORG, ARC_COMMITMENT)

# ---------------------------------------------------------------------------
# SPEC EODARC1 — the arc read's templates. Three questions, in §"shape of
# done" order: which arcs moved, which consequence-carrying arcs did not,
# where the day's weight went. Every one of them renders WITH an arc attached
# or not at all (`drop_rows_only`); none of them contains a denominator
# against a plan, so none of them can grow into the grade R-1 removed.
# ---------------------------------------------------------------------------

T_ARC_MOVED_FIRST = "The day went into {arc} — {what}."
T_ARC_MOVED_ALSO = "{arc} moved too — {what}."

# Unmoved-with-consequence, arc-labelled spellings. "Did not move" is a claim
# about TODAY's record joined against the arc, which is exactly what the join
# computed; the second clause is the row's own stated consequence, never an
# inferred one.
T_ARC_UNMOVED_DATE = "{arc} did not move: {title} is {days} {noun} past due."
T_ARC_UNMOVED_DUE = "{arc} did not move: {title} was due {due}."
T_ARC_UNMOVED_MEETING = ("{arc} did not move: {title} has not gone out, "
                         "and {meeting} needs it.")
T_ARC_UNMOVED_PERSON = ("{arc} did not move: {person} is waiting "
                        "on {title}.")
T_ARC_UNMOVED_BLOCKER = "{arc} did not move: {title} is blocked on {blocker}."

# The commitment-arc spellings — the arc's label IS the title, so the long
# forms above would say the name twice.
T_CMT_UNMOVED_DATE = "{title} did not move — {days} {noun} past due."
T_CMT_UNMOVED_DUE = "{title} did not move — it was due {due}."
T_CMT_UNMOVED_MEETING = "{title} did not move, and {meeting} needs it."
T_CMT_UNMOVED_PERSON = "{title} did not move, and {person} is waiting on it."
T_CMT_UNMOVED_BLOCKER = "{title} did not move — blocked on {blocker}."

# The weight sentence. Counted claims over TODAY's own rows (never against a
# plan): the meeting shape when the day had meetings, the record shape
# otherwise. Both spell the count with words between the numerals on purpose —
# "2 of the day's 3 meetings" is a fact about where the day went; "2 of 3
# closed" would be the grade, and `SCORE_PATTERNS` catches that shape.
T_ARC_WEIGHT_MEETINGS = "{k} of the day's {n} meetings were with {arc}."
T_ARC_WEIGHT_ROWS = ("The day's weight went to {arc}: {k} of the {n} things "
                     "on today's record touched it.")

# The honest empty. It is the SECTION's claim — no arc moved and nothing
# consequence-carrying is visibly waiting — grounded on the day window it read.
T_ARC_EMPTY_DAY = "Nothing on today's record moved a standing arc."

# The sentence kind the empty-day line travels under. `drop_rows_only` exempts
# exactly this kind, because the honest empty is the one sentence in the
# section that is ABOUT the absence of an arc rather than about a row.
KIND_EMPTY_DAY = "empty_day"


def declared_arc(kind: str, arc_id: str, label: str, *,
                 thread_id: Optional[str] = None,
                 thread_ids: Optional[Sequence[str]] = None) -> dict:
    """One arc the substrate DECLARES. Built here so every consumer agrees on
    the shape, and so an arc without an id cannot be constructed at all — an
    unidentified arc is exactly the inferred theme §3.3 refuses.

    SPEC EODARC1 — `thread_ids`: an ORG arc is reachable through every live
    thread the register affiliates to it, so its linkage is a set rather than
    the single `thread_id` the older kinds carry. Both spellings join; neither
    is required."""
    if kind not in ARC_KINDS:
        raise ValueError(f"not a declared arc kind: {kind!r}")
    if not str(arc_id or "").strip():
        raise ValueError("a declared arc carries an id; an arc with no id is "
                         "an inference (SPEC EODSYNTH1 §3.3)")
    return {"kind": kind, "arc_id": str(arc_id).strip(),
            "label": str(label or "").strip(),
            "thread_id": str(thread_id).strip() if thread_id else None,
            "thread_ids": [str(t).strip() for t in (thread_ids or [])
                           if str(t or "").strip()]}


def arc_ref(arc: dict) -> str:
    kind = (arc or {}).get("kind")
    if kind == ARC_OBJECTIVE:
        return ref_objective(arc.get("arc_id"))
    if kind == ARC_WORKSTREAM:
        return ref_thread(arc.get("arc_id"))
    if kind == ARC_ORG:
        return ref_org(arc.get("arc_id"))
    if kind == ARC_COMMITMENT:
        # A commitment arc's ref IS the commitment ref — one spelling per row
        # (§3.1), so the arc and the evidence resolve to the same record.
        return ref_commitment(arc.get("arc_id"))
    return f"{kind}:{arc.get('arc_id')}"


def _row_links(row: dict) -> set:
    """Every id a row explicitly links to — its OWN fields, nothing fuzzy.

    One spelling of the link-set, read by `join_to_arcs` and by
    `mint_commitment_arcs` (SPEC EODARC1), so "does this row touch that arc"
    is one question with one answer wherever it is asked. SPEC EODARC1 widens
    it with the commitment and org fields; `data.id` is deliberately NOT here
    — only an explicit `commitment_id` links to a commitment arc, because a
    generic id field would join a decision's own id to an unrelated arc that
    happened to share a spelling.
    """
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    linked = {str(row.get("thread_id") or ""),
              str(row.get("primary_thread_id") or ""),
              str(data.get("thread_id") or ""),
              str(data.get("primary_thread_id") or ""),
              str(row.get("objective_id") or ""),
              str(data.get("objective_id") or ""),
              str(row.get("deal_id") or ""),
              str(data.get("deal_id") or ""),
              str(row.get("commitment_id") or ""),
              str(data.get("commitment_id") or ""),
              str(row.get("org_id") or ""),
              str(data.get("org_id") or "")}
    for field in (row.get("org_ids"), data.get("org_ids")):
        if isinstance(field, (list, tuple)):
            linked.update(str(v or "") for v in field)
    linked.discard("")
    return linked


def join_to_arcs(rows: Iterable[dict], arcs: Iterable[dict]) -> List[dict]:
    """Join today's rows to DECLARED arcs. Never infers one (§3.3).

    A row joins to an arc when it shares the arc's `thread_id` (or, SPEC
    EODARC1, any of an org arc's `thread_ids`) or names the arc's `arc_id` in
    one of its own link fields. There is deliberately NO fuzzy, no keyword, no
    title-similarity path: every similarity heuristic in this codebase is a
    PROPOSER (`_shares_words` orders a draft the CEO confirms), and this is a
    claim about meaning that renders as fact.

    Returns `[{"arc": <arc>, "rows": [...]}, ...]`, arcs with no rows omitted.
    """
    declared = [a for a in (arcs or [])
                if isinstance(a, dict) and a.get("kind") in ARC_KINDS
                and str(a.get("arc_id") or "").strip()]
    out = []
    for arc in declared:
        hits = []
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            linked = _row_links(row)
            if (arc["arc_id"] in linked
                    or (arc.get("thread_id") and arc["thread_id"] in linked)
                    or any(t in linked
                           for t in (arc.get("thread_ids") or []))):
                hits.append(row)
        if hits:
            out.append({"arc": arc, "rows": hits})
    return out


def arc_stated_consequence(row: dict) -> Optional[dict]:
    """The consequence an OPEN row states, for the arc read (SPEC EODARC1).

    Delegates to `stated_consequence` — one reading of the shared classes,
    never two — with two arc-read differences, both deliberate:

      * an OVERDUE DUE DATE outranks the other classes here. The slipped
        prose's priority (meeting > person > date) fits a triage row; the
        arc read's sharpest honest claim about an unmoved arc is how long
        its stated date has been sliding, which is the reference shape M's
        own day should have produced ("four days past due").
      * the STATED-BLOCKER class ruling 2c names is added, last. "States" is
        as literal here as it is there: the blocker has to be a field on the
        row (`blocker` / `blocked_on`), never a condition a reader would
        infer.
    """
    if not isinstance(row, dict):
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    due = row.get("due") or data.get("due")
    if due and row.get("overdue"):
        return {"kind": CONSEQUENCE_DATE, "value": str(due).strip(),
                "ref": ref_commitment(row.get("commitment_id"))}
    cons = stated_consequence(row)
    if cons:
        return cons
    blocker = (row.get("blocker") or data.get("blocker")
               or row.get("blocked_on") or data.get("blocked_on"))
    if blocker:
        return {"kind": CONSEQUENCE_BLOCKER, "value": str(blocker).strip(),
                "ref": ref_commitment(row.get("commitment_id"))}
    return None


def mint_commitment_arcs(open_rows: Optional[Iterable[dict]],
                         declared: Optional[Iterable[dict]]) -> List[dict]:
    """Ruling 2's class (c): a consequence-carrying OPEN commitment that no
    declared arc tracks is an arc of its own (SPEC EODARC1).

    Three refusals, each structural: no stated consequence → not an arc (an
    open row with nothing waiting on it is a row, and rows are the morning's
    business); no id or no title → not constructible (`declared_arc` refuses);
    already linked to a declared arc → not minted, because the org or thread
    that tracks it is the arc and a second arc over the same row would narrate
    one promise twice.
    """
    declared_list = [a for a in (declared or []) if isinstance(a, dict)]
    out: List[dict] = []
    for row in (open_rows or []):
        if not isinstance(row, dict):
            continue
        if not arc_stated_consequence(row):
            continue
        cid = str(row.get("commitment_id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not cid or not title:
            continue
        links = _row_links(row)
        tracked = any(
            a.get("arc_id") in links
            or (a.get("thread_id") and a["thread_id"] in links)
            or any(t in links for t in (a.get("thread_ids") or []))
            for a in declared_list)
        if tracked:
            continue
        try:
            out.append(declared_arc(ARC_COMMITMENT, cid, title))
        except ValueError:
            continue
    return out


def drop_rows_only(sentences: Iterable[dict], arc_refs) -> dict:
    """THE RECAP/SYNTHESIS FENCE (SPEC EODARC1). A sentence with no arc
    attached does not belong in the section.

    Returns `{"kept": [...], "dropped": [...], "n_dropped": int}`. A sentence
    survives only when its ref set names at least one of the arcs this render
    was handed — the fence is a MEMBERSHIP test against the actual arc refs,
    never a prefix heuristic, so a row ref dressed in an arc-shaped spelling
    still drops. The ONE exemption is the `KIND_EMPTY_DAY` sentence, which is
    the section honestly saying no arc moved; it must still carry a ref
    (`drop_unreferenced` sees it too) — the exemption is from the arc
    requirement, never from grounding.

    This is what "refuses to enumerate rows as synthesis" means in code: a
    composer that starts listing row state produces sentences whose refs are
    all row refs, and every one of them dies here before `compose`. The suite
    pins it by feeding a rows-only day and by removing the membership test.
    """
    allowed = {str(r) for r in (arc_refs or ()) if str(r or "").strip()}
    kept, dropped = [], []
    for s in (sentences or []):
        if not isinstance(s, dict):
            continue
        if s.get("kind") == KIND_EMPTY_DAY:
            kept.append(s)
            continue
        if any(r in allowed for r in (s.get("refs") or [])):
            kept.append(s)
        else:
            dropped.append(s)
    return {"kept": kept, "dropped": dropped, "n_dropped": len(dropped)}


def _days_past_due(due, for_date) -> Optional[int]:
    """Whole days between a row's own `due` and the fire's own day — grounded
    arithmetic over two recorded fields, or None when either does not parse.
    Never raises: a malformed date is a row that narrates under the plainer
    was-due template, not a fire that dies composing prose."""
    try:
        import datetime as _dt2
        d = _dt2.date.fromisoformat(str(due).strip()[:10])
        f = _dt2.date.fromisoformat(str(for_date).strip()[:10])
        n = (f - d).days
        return n if n > 0 else None
    except Exception:  # noqa: BLE001 — see the docstring
        return None


def _unmoved_sentence(arc: dict, row: dict, cons: dict,
                      for_date=None) -> Optional[dict]:
    """One unmoved-with-consequence sentence: the arc, the row that is
    waiting, and the consequence the row itself states."""
    title = str(row.get("title") or "").strip()
    if not title:
        return None
    label = str(arc.get("label") or arc.get("arc_id") or "").strip()
    is_cmt = arc.get("kind") == ARC_COMMITMENT
    kind = cons.get("kind")
    if kind == CONSEQUENCE_DATE:
        days = _days_past_due(cons.get("value"), for_date)
        if days:
            noun = _plural(days, "day", "days")
            text = (T_CMT_UNMOVED_DATE.format(title=title, days=days,
                                              noun=noun) if is_cmt
                    else T_ARC_UNMOVED_DATE.format(arc=label, title=title,
                                                   days=days, noun=noun))
        else:
            due = str(cons.get("value") or "").strip()
            text = (T_CMT_UNMOVED_DUE.format(title=title, due=due) if is_cmt
                    else T_ARC_UNMOVED_DUE.format(arc=label, title=title,
                                                  due=due))
    elif kind == CONSEQUENCE_MEETING:
        text = (T_CMT_UNMOVED_MEETING.format(title=title,
                                             meeting=cons["value"]) if is_cmt
                else T_ARC_UNMOVED_MEETING.format(arc=label, title=title,
                                                  meeting=cons["value"]))
    elif kind == CONSEQUENCE_PERSON:
        text = (T_CMT_UNMOVED_PERSON.format(title=title,
                                            person=cons["value"]) if is_cmt
                else T_ARC_UNMOVED_PERSON.format(arc=label, title=title,
                                                 person=cons["value"]))
    elif kind == CONSEQUENCE_BLOCKER:
        text = (T_CMT_UNMOVED_BLOCKER.format(title=title,
                                             blocker=cons["value"]) if is_cmt
                else T_ARC_UNMOVED_BLOCKER.format(arc=label, title=title,
                                                  blocker=cons["value"]))
    else:
        return None
    refs = [r for r in (arc_ref(arc), ref_commitment(row.get("commitment_id")),
                        cons.get("ref")) if r]
    return sentence(text, refs, tier=TIER_MEANT, kind="unmoved")


def compute_what_it_meant(*, rows: Optional[Iterable[dict]] = None,
                          arcs: Optional[Iterable[dict]] = None,
                          cap: int = 2,
                          open_rows: Optional[Iterable[dict]] = None,
                          meetings: Optional[Iterable[dict]] = None,
                          for_date=None,
                          unmoved_cap: int = 2,
                          render_unmoved: bool = True) -> dict:
    """TIER 2 — THE ARC READ (SPEC EODARC1). The day against the arcs the
    substrate declares, answered in the spec's order: which arcs MOVED today
    (grounded in what happened), which consequence-carrying arcs did NOT
    (what is waiting, and on whom), and where the day's WEIGHT went.

    CUT-PLATE (2026-09-06) — `render_unmoved=False` KEEPS the unmoved read
    as data and takes it OFF the screen. The v5.28.0 attended test (B2.2)
    saw the day-close say "X did not move — 41 days past due" beside the
    coach's "has now survived 7 closes"; M's ruling is that the evening
    reads the day in the PLATE's shape (opened / closed / slipped) and
    shows less. The unmoved sentences are still composed, still fenced,
    still grounded, and returned under `unmoved` (a list of sentences) with
    `n_unmoved` — the coach's stillness detector and the receipt read them
    there — but they are not joined into `text` / `sentences` / `refs`, so
    nothing prints them. The driver passes False; a direct caller keeps the
    pre-CUT-PLATE default (True), byte-identical.

    A recap lists what changed; a synthesis says what it means for what you
    are running. The fence between the two is `drop_rows_only`, run over
    every sentence this composer produces: a sentence with no arc attached
    cannot render in this section, so a rows-only day cannot be enumerated
    here — it renders the honest empty-day line instead (when the caller
    stated `for_date`) or nothing at all.

    BACKWARD SHAPE: called with only `rows` and `arcs` — the pre-EODARC1
    signature — a day that joins to nothing still renders NOTHING, never a
    theme (§1.3 pin). The unmoved read runs only when the caller hands the
    OPEN BOOK (`open_rows`); the honest empty renders only when the caller
    states the day (`for_date`), because an empty-day claim with no day
    behind it is a guess.

    DEALS ARE CONTEXT, NEVER STATE (ruling 3): nothing here reads the deal
    tracker. A deal arc that arrives in `arcs` joins like any other; a deal
    arc that does not exist subtracts nothing, because org and workstream
    arcs are declared from the entity register independently.
    """
    day_rows = [r for r in (rows or []) if isinstance(r, dict)]
    declared = [a for a in (arcs or []) if isinstance(a, dict)]
    opens = [r for r in (open_rows or []) if isinstance(r, dict)]
    mrows = [m for m in (meetings or []) if isinstance(m, dict)]

    # Ruling 2c — consequence-carrying commitments no declared arc tracks
    # are arcs of their own.
    all_arcs = declared + mint_commitment_arcs(opens, declared)
    arc_ref_set = {arc_ref(a) for a in all_arcs if arc_ref(a)}

    out: List[dict] = []

    # 1 — WHICH ARCS MOVED TODAY. THE ARC JOIN: strict, declared, no fuzz.
    joins = join_to_arcs(day_rows, all_arcs)
    moved_keys = {(j["arc"]["kind"], j["arc"]["arc_id"]) for j in joins}
    n_named = 0
    for j in joins:
        if cap and n_named >= cap:
            break
        arc, hits = j["arc"], j["rows"]
        titles = [str(r.get("title") or "").strip() for r in hits]
        titles = [t for t in titles if t]
        if not titles:
            # A join with nothing nameable behind it is a count, not a
            # meaning. Dropping it here is cheaper than a sentence that says
            # "three things moved on X" and cannot say which.
            continue
        what = _english_list(titles[:3])
        refs = [arc_ref(arc)]
        for r in hits:
            ref = (ref_commitment(r.get("commitment_id"))
                   or ref_meeting(r.get("meeting_id"))
                   or ref_decision(r.get("decision_id")))
            if ref:
                refs.append(ref)
        label = arc.get("label") or arc.get("arc_id")
        template = T_ARC_MOVED_FIRST if n_named == 0 else T_ARC_MOVED_ALSO
        out.append(sentence(template.format(arc=label, what=what), refs,
                            tier=TIER_MEANT, kind=arc["kind"]))
        n_named += 1

    # 2 — WHICH CONSEQUENCE-CARRYING ARCS DID NOT MOVE. Joined off the OPEN
    # book with the same strict join; "did not move" means the arc took no
    # hit from today's rows, which is exactly what `moved_keys` measured. An
    # arc that moved never renders here, capped or not.
    n_unmoved = 0
    unmoved_sentences: List[dict] = []
    if opens:
        candidates = []
        for j in join_to_arcs(opens, all_arcs):
            arc = j["arc"]
            if (arc["kind"], arc["arc_id"]) in moved_keys:
                continue
            best = None
            for row in j["rows"]:
                cons = arc_stated_consequence(row)
                if not cons:
                    continue
                days = (_days_past_due(cons.get("value"), for_date)
                        if cons.get("kind") == CONSEQUENCE_DATE else None)
                rank = ((0, -days) if days
                        else (1, 0) if cons.get("kind") == CONSEQUENCE_MEETING
                        else (2, 0) if cons.get("kind") == CONSEQUENCE_PERSON
                        else (3, 0))
                if best is None or rank < best[0]:
                    best = (rank, row, cons)
            if best is None:
                continue
            candidates.append((best[0], len(candidates), arc, best[1],
                               best[2]))
        candidates.sort(key=lambda c: (c[0], c[1]))
        n_unmoved = len(candidates)
        for _rank, _tie, arc, row, cons in candidates[:unmoved_cap]:
            s = _unmoved_sentence(arc, row, cons, for_date=for_date)
            if s:
                unmoved_sentences.append(s)
                if render_unmoved:
                    out.append(s)

    # 3 — WHERE THE DAY'S WEIGHT WENT. One sentence, counted off today's own
    # rows (never against a plan). The meeting shape when the day had two or
    # more meetings and an arc took two or more of them; else the record
    # shape when an arc took at least half of a three-plus-row day. A day
    # too thin to support the claim gets no sentence — a weight read over
    # one row is a restatement, not a synthesis.
    weight = None
    if len(mrows) >= 2:
        mjoins = join_to_arcs(mrows, all_arcs)
        if mjoins:
            top = max(mjoins, key=lambda j: len(j["rows"]))
            k = len(top["rows"])
            if k >= 2:
                refs = [arc_ref(top["arc"])]
                refs += [r for r in
                         (ref_meeting(m.get("meeting_id") or m.get("id"))
                          for m in top["rows"]) if r]
                label = top["arc"].get("label") or top["arc"]["arc_id"]
                weight = sentence(
                    T_ARC_WEIGHT_MEETINGS.format(k=k, n=len(mrows),
                                                 arc=label),
                    refs, tier=TIER_MEANT, kind="weight")
    if weight is None and joins and len(day_rows) >= 3:
        top = max(joins, key=lambda j: len(j["rows"]))
        k = len(top["rows"])
        if k >= 2 and k * 2 >= len(day_rows):
            refs = [arc_ref(top["arc"])]
            for r in top["rows"]:
                ref = (ref_commitment(r.get("commitment_id"))
                       or ref_meeting(r.get("meeting_id"))
                       or ref_decision(r.get("decision_id")))
                if ref and ref not in refs:
                    refs.append(ref)
            label = top["arc"].get("label") or top["arc"]["arc_id"]
            weight = sentence(
                T_ARC_WEIGHT_ROWS.format(arc=label, k=k, n=len(day_rows)),
                refs, tier=TIER_MEANT, kind="weight")
    if weight is not None:
        out.append(weight)

    # 4 — THE HONEST EMPTY. Only when the caller stated the day, and only
    # when the whole read produced nothing: no arc moved, nothing
    # consequence-carrying is visibly waiting. Saying so is a claim about
    # the day window, and it carries that window as its ref.
    empty_day = False
    if for_date and not out:
        out.append(sentence(T_ARC_EMPTY_DAY, [ref_window(for_date)],
                            tier=TIER_MEANT, kind=KIND_EMPTY_DAY))
        empty_day = True

    # THE FENCE, then the grounding pass `compose` always runs. Order
    # matters only for the counters: a rows-only sentence is an arc defect,
    # not a missing-ref defect, and the receipt should say which.
    fenced = drop_rows_only(out, arc_ref_set)
    result = compose(fenced["kept"])
    result["renders"] = bool(result["text"])
    result["n_arcs"] = len(joins)
    result["n_moved"] = len(moved_keys)
    result["n_unmoved"] = n_unmoved
    # CUT-PLATE — the unmoved read as DATA, whatever `render_unmoved` said:
    # the same fenced, grounded sentences, kept beside the text so the coach
    # and the receipt read one record. When `render_unmoved` is True these
    # are also inside `sentences`; when False they live only here.
    result["unmoved"] = drop_rows_only(unmoved_sentences, arc_ref_set)["kept"]
    result["unmoved_rendered"] = bool(render_unmoved)
    result["n_rows_only_dropped"] = fenced["n_dropped"]
    result["empty_day"] = empty_day
    assert_no_score(result["text"], where="what_it_meant")
    return result


# ---------------------------------------------------------------------------
# Worth remembering (§1.4)
# ---------------------------------------------------------------------------

WR_DECISION = "Decided: {text}"
WR_NOTE = "Noted: {text}"


def compute_worth_remembering(*, decisions: Optional[Iterable[dict]] = None,
                              notes: Optional[Iterable[dict]] = None,
                              cap: int = MAX_WORTH_REMEMBERING) -> dict:
    """Takeaways logged today — decision events and explicit `note` rows.

    1-4 lines, each traceable to the row it came from. Nothing is summarized
    across rows and nothing is composed: the line IS the row's own text, so
    there is no sentence here for a model to write and nothing for it to
    round off.
    """
    lines: List[dict] = []
    for d in (decisions or []):
        if not isinstance(d, dict):
            continue
        data = d.get("data") if isinstance(d.get("data"), dict) else d
        text = str(data.get("decision") or data.get("title")
                   or data.get("summary") or "").strip()
        ref = ref_decision(data.get("decision_id") or data.get("id")
                           or d.get("id"))
        lines.append(sentence(WR_DECISION.format(text=text), [ref] if ref else [],
                              tier=TIER_HAPPENED, kind="decision"))
    for n in (notes or []):
        if not isinstance(n, dict):
            continue
        data = n.get("data") if isinstance(n.get("data"), dict) else n
        text = str(data.get("note") or data.get("text")
                   or data.get("summary") or "").strip()
        ref = ref_event(data.get("id") or n.get("seq") or n.get("ts"))
        lines.append(sentence(WR_NOTE.format(text=text), [ref] if ref else [],
                              tier=TIER_HAPPENED, kind="note"))
    split = drop_unreferenced(lines)
    kept = split["kept"][:cap] if cap else split["kept"]
    refs: List[str] = []
    for s in kept:
        for r in s["refs"]:
            if r not in refs:
                refs.append(r)
    text = "\n".join(s["text"] for s in kept)
    assert_no_score(text, where="worth_remembering")
    return {"lines": [s["text"] for s in kept], "sentences": kept,
            "refs": refs, "n_dropped": split["n_dropped"],
            "n_total": len(split["kept"]), "renders": bool(kept),
            "text": text}


# ---------------------------------------------------------------------------
# Slipped, with consequences (§1.5)
# ---------------------------------------------------------------------------

SLIP_MEETING = "{title} did not go out, and {meeting} needs it."
SLIP_PERSON = "{title} is still open, and {person} is waiting on it."
SLIP_DATE = "{title} was due {due} and has not moved."

# The consequence kinds a slip may be narrated under. A slip with none of
# them is SILENT here and appears in the morning — that is the whole selection
# rule, and "137 slipped" is not a sentence this surface can produce.
CONSEQUENCE_MEETING = "gates_meeting"
CONSEQUENCE_PERSON = "person_waiting"
CONSEQUENCE_DATE = "stated_date"
CONSEQUENCE_KINDS = (CONSEQUENCE_MEETING, CONSEQUENCE_PERSON,
                     CONSEQUENCE_DATE)

# The consequence classes the ARC read may narrate (SPEC EODARC1, ruling 2c:
# due dates, named counterparties, stated blockers — plus the meeting-gate
# this block already knew). `stated_consequence` stays exactly what the
# slipped prose renders (shape of done: that line is UNCHANGED); the blocker
# class exists only on the arc read, through `arc_stated_consequence`.
CONSEQUENCE_BLOCKER = "stated_blocker"
ARC_CONSEQUENCE_KINDS = (CONSEQUENCE_MEETING, CONSEQUENCE_PERSON,
                         CONSEQUENCE_DATE, CONSEQUENCE_BLOCKER)


def stated_consequence(row: dict) -> Optional[dict]:
    """The DOWNSTREAM EFFECT this slipped row states, or None.

    "States" is literal: the effect has to be a field on the row — the meeting
    it gates, the person waiting, the date it was owed by. A consequence the
    reader would have to infer is not a consequence this surface may narrate,
    and a row with none is not a lesser row: it is a row whose ask belongs to
    the morning (R-3), where the operator is in triage mode.
    """
    if not isinstance(row, dict):
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    gates = (row.get("gates_meeting") or data.get("gates_meeting")
             or row.get("blocks_meeting") or data.get("blocks_meeting"))
    if gates:
        return {"kind": CONSEQUENCE_MEETING, "value": str(gates).strip(),
                "ref": ref_meeting(row.get("gates_meeting_id")
                                   or data.get("gates_meeting_id") or gates)}
    waiting = (row.get("waiting_person") or data.get("waiting_person")
               or row.get("counterparty_name") or data.get("counterparty_name"))
    if waiting:
        return {"kind": CONSEQUENCE_PERSON, "value": str(waiting).strip(),
                "ref": ref_commitment(row.get("commitment_id"))}
    due = row.get("due") or data.get("due")
    if due and row.get("overdue"):
        return {"kind": CONSEQUENCE_DATE, "value": str(due).strip(),
                "ref": ref_commitment(row.get("commitment_id"))}
    return None


def compute_slipped_prose(rows: Optional[Iterable[dict]] = None, *,
                          cap: int = 3, now_iso: Optional[str] = None) -> dict:
    """Prose, not a list, and ONLY the slips whose consequence is stated.

    Returns `{"text", "sentences", "refs", "n_with_consequence", "n_silent",
    "renders"}`. `n_silent` is the honest count of slips this block did not
    narrate — they are not gone, they are in the morning — and it is a number
    the receipt carries rather than a header the surface prints.

    SPEC TOMFILT1 §2 — a `CONSEQUENCE_DATE` slip's due clause is re-anchored
    to `now_iso` on every render (`due_reanchor.render_due_clause`), so "was
    due Aug 6" carries its age ("was due Aug 6 — 19 days ago") instead of a
    bare date that goes stale the moment it sits on screen. `now_iso=None`
    (no caller passes a clock) keeps the bare raw value — the pre-TOMFILT1
    shape — rather than guess an age with nothing to measure it against.
    """
    out: List[dict] = []
    n_silent = 0
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        cons = stated_consequence(row) if title else None
        if not cons:
            n_silent += 1
            continue
        refs = [r for r in (ref_commitment(row.get("commitment_id")),
                            cons.get("ref")) if r]
        if cons["kind"] == CONSEQUENCE_MEETING:
            text = SLIP_MEETING.format(title=title, meeting=cons["value"])
        elif cons["kind"] == CONSEQUENCE_PERSON:
            text = SLIP_PERSON.format(title=title, person=cons["value"])
        else:
            due_clause = cons["value"]
            if now_iso:
                from due_reanchor import render_due_clause
                due_clause = render_due_clause(cons["value"], now_iso)
            text = SLIP_DATE.format(title=title, due=due_clause)
        out.append(sentence(text, refs, tier=TIER_HAPPENED,
                            kind=cons["kind"]))
    kept = out[:cap] if cap else out
    n_silent += max(0, len(out) - len(kept))
    result = compose(kept)
    result["n_with_consequence"] = len(out)
    result["n_silent"] = n_silent
    result["renders"] = bool(result["text"])
    assert_no_score(result["text"], where="slipped_prose")
    return result


# ---------------------------------------------------------------------------
# Tier 3 — precedent echoes (§3.4)
# ---------------------------------------------------------------------------

# The form M ruled: "this resembles X, which went Y". The label is part of the
# string so an echo cannot render as a fact by a renderer forgetting a flag.
ECHO_TEMPLATE = ("Reading across the record: this resembles {precedent}, "
                 "which {outcome}.")
ECHO_LABEL = "Reading across the record:"

# Banned outright. Each is a shape of manufactured profundity — a claim with
# no row behind it that reads as insight. "Momentum is building" is M's own
# example; the rest are its siblings, and the list is checked as a SUBSTRING
# sweep so a rephrasing that keeps the phrase is still caught.
BANNED_ECHO_PHRASES = (
    "momentum is building",
    "momentum building",
    "things are picking up",
    "you are on a roll",
    "the trend is clear",
    "everything is coming together",
    "this is a turning point",
)


class EchoRefused(ValueError):
    """An echo was offered that cites no precedent, or that says nothing."""


def make_echo(*, precedent_id: str, precedent_label: str,
              outcome: str) -> dict:
    """One labelled precedent echo. REFUSES without a precedent id (§3.4).

    The id is not decoration and it is not optional: an echo is an inference
    over history, and the only thing separating it from invented profundity is
    that a reader can go and look at the row it names. `compute_echoes` calls
    this, so there is no path to an echo that skips the refusal.
    """
    pid = str(precedent_id or "").strip()
    if not pid:
        raise EchoRefused(
            "an echo must cite its precedent by id (SPEC EODSYNTH1 §3.4); "
            "an uncited echo is the manufactured profundity this tier bans")
    label = str(precedent_label or "").strip() or pid
    said = str(outcome or "").strip()
    if not said:
        raise EchoRefused("an echo must say how the precedent went")
    text = ECHO_TEMPLATE.format(precedent=label, outcome=said)
    low = text.lower()
    for banned in BANNED_ECHO_PHRASES:
        if banned in low:
            raise EchoRefused(f"banned echo phrasing: {banned!r}")
    return sentence(text, [f"precedent:{pid}"], tier=TIER_ECHO,
                    kind="echo")


def compute_echoes(candidates: Optional[Iterable[dict]] = None, *,
                   cap: int = MAX_ECHOES) -> dict:
    """TIER 3 — at most `cap` echoes, each citing a precedent by id.

    ABSENT BY DEFAULT. `candidates` is normally empty and the block does not
    render; a candidate without `precedent_id` is DROPPED rather than raising,
    because a caller assembling candidates from the substrate will meet rows
    that do not qualify and the correct answer for those is silence.

    Each candidate: `{"precedent_id", "precedent_label", "outcome"}`.
    """
    out, n_refused = [], 0
    for c in (candidates or []):
        if not isinstance(c, dict):
            continue
        try:
            out.append(make_echo(precedent_id=c.get("precedent_id"),
                                 precedent_label=c.get("precedent_label"),
                                 outcome=c.get("outcome")))
        except EchoRefused:
            n_refused += 1
    kept = out[:cap] if cap else out
    result = compose(kept)
    result["renders"] = bool(result["text"])
    result["n_refused"] = n_refused
    result["n_candidates"] = len(list(candidates or []))
    result["labelled"] = True
    assert_no_score(result["text"], where="echoes")
    return result


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------

def build_synthesis(*, ledger: Optional[dict],
                    closures: Optional[Iterable[dict]] = None,
                    meetings: Optional[Iterable[dict]] = None,
                    decisions: Optional[Iterable[dict]] = None,
                    notes: Optional[Iterable[dict]] = None,
                    arcs: Optional[Iterable[dict]] = None,
                    arc_rows: Optional[Iterable[dict]] = None,
                    open_rows: Optional[Iterable[dict]] = None,
                    for_date=None,
                    slipped_rows: Optional[Iterable[dict]] = None,
                    echo_candidates: Optional[Iterable[dict]] = None,
                    held_ids: Optional[Iterable[str]] = None,
                    workspace_root=None,
                    now_iso: Optional[str] = None,
                    render_unmoved: bool = True) -> dict:
    """Every synthesis block, composed once, fenced once.

    `render_unmoved` (CUT-PLATE) threads through to `compute_what_it_meant`:
    the day-close driver passes False so the "did not move" sentences stay
    on the block as data and off the screen; every other caller keeps the
    default.

    The visibility fence (§3.6) is applied HERE, to every row set that can
    reach a sentence, rather than inside each composer — one call site is one
    thing to remove in a red-proof, and a per-composer fence is five places for
    the next build to add a sixth composer beside.

    The score fence (R-1) runs over the ASSEMBLED text as well as inside each
    composer. Both are deliberate: the composers catch a template that grew a
    grade, and this catches an assembly that put two ungraded halves together
    into one.
    """
    fenced_closures = visible_rows(closures or [], held_ids=held_ids,
                                   workspace_root=workspace_root)
    fenced_arc_rows = visible_rows(arc_rows or [], held_ids=held_ids,
                                   workspace_root=workspace_root)
    # SPEC EODARC1 — the OPEN BOOK enters the arc read (the unmoved-with-
    # consequence paragraph), so it passes the same §3.6 fence as every other
    # row set that can reach a sentence: a held or personal open commitment
    # must not be narrated as "waiting" any more than it may be listed.
    fenced_open_rows = visible_rows(open_rows or [], held_ids=held_ids,
                                    workspace_root=workspace_root)
    fenced_slipped = visible_rows(slipped_rows or [], held_ids=held_ids,
                                  workspace_root=workspace_root)
    fenced_decisions = visible_rows(decisions or [], held_ids=held_ids,
                                    workspace_root=workspace_root)
    fenced_notes = visible_rows(notes or [], held_ids=held_ids,
                                workspace_root=workspace_root)

    day_went = compute_day_went(ledger=ledger,
                                closures=fenced_closures["rows"],
                                meetings=meetings,
                                decisions=fenced_decisions["rows"])
    what_it_meant = compute_what_it_meant(rows=fenced_arc_rows["rows"],
                                          arcs=arcs,
                                          open_rows=fenced_open_rows["rows"],
                                          meetings=meetings,
                                          for_date=for_date,
                                          render_unmoved=render_unmoved)
    worth = compute_worth_remembering(decisions=fenced_decisions["rows"],
                                      notes=fenced_notes["rows"])
    slipped_prose = compute_slipped_prose(fenced_slipped["rows"],
                                          now_iso=now_iso)
    echoes = compute_echoes(echo_candidates)

    n_withheld = (fenced_closures["n_withheld"] + fenced_arc_rows["n_withheld"]
                  + fenced_open_rows["n_withheld"]
                  + fenced_slipped["n_withheld"]
                  + fenced_decisions["n_withheld"]
                  + fenced_notes["n_withheld"])

    out = {
        BLOCK_DAY_WENT: day_went,
        BLOCK_WHAT_IT_MEANT: what_it_meant,
        BLOCK_WORTH_REMEMBERING: worth,
        BLOCK_SLIPPED_PROSE: slipped_prose,
        BLOCK_ECHOES: echoes,
        "n_withheld": n_withheld,
        "n_unreferenced_dropped": (day_went["n_dropped"]
                                   + what_it_meant["n_dropped"]
                                   + worth["n_dropped"]
                                   + slipped_prose["n_dropped"]
                                   + echoes["n_dropped"]),
    }
    assert_no_score(synthesis_text(out), where="build_synthesis")
    return out


def synthesis_text(synthesis: Optional[dict]) -> str:
    """Every rendered character of the synthesis, in render order.

    This is what the score fence is pointed at, and what a suite scans when it
    asks "did anything reach the screen". Blocks that do not render contribute
    nothing rather than an empty line.
    """
    src = synthesis or {}
    parts = []
    for block in SYNTHESIS_BLOCKS:
        body = src.get(block)
        if isinstance(body, dict) and str(body.get("text") or "").strip():
            parts.append(str(body["text"]).strip())
    return "\n\n".join(parts)


def synthesis_refs(synthesis: Optional[dict]) -> List[str]:
    """The whole join behind the prose, deduped, in render order. This is what
    the persisted brief carries so a sentence can be checked against the row it
    came from."""
    src = synthesis or {}
    out: List[str] = []
    for block in SYNTHESIS_BLOCKS:
        body = src.get(block)
        if not isinstance(body, dict):
            continue
        for r in body.get("refs") or []:
            if r not in out:
                out.append(r)
    return out


__all__ = [
    "BLOCK_DAY_WENT", "BLOCK_WHAT_IT_MEANT", "BLOCK_WORTH_REMEMBERING",
    "BLOCK_SLIPPED_PROSE", "BLOCK_ECHOES", "SYNTHESIS_BLOCKS",
    "TIER_HAPPENED", "TIER_MEANT", "TIER_ECHO", "MAX_ECHOES",
    "MAX_WORTH_REMEMBERING",
    "SCORE_PATTERNS", "score_tokens_in", "assert_no_score",
    "ScoreRenderedError",
    "sentence", "drop_unreferenced", "compose",
    "ref_commitment", "ref_event", "ref_meeting", "ref_decision",
    "ref_objective", "ref_thread", "ref_ledger",
    "LEDGER_COUNT_FIELDS", "LEDGER_BOOK_FIELDS", "ledger_numbers",
    "QuoteRefused", "transcript_is_labelled", "admit_quote",
    "visible_rows",
    "compute_day_went",
    "ARC_KINDS", "ARC_OBJECTIVE", "ARC_DAY_INTENT", "ARC_WORKSTREAM",
    "ARC_DEAL", "ARC_ORG", "ARC_COMMITMENT", "declared_arc", "arc_ref",
    "join_to_arcs", "ref_org", "ref_window",
    "KIND_EMPTY_DAY", "CONSEQUENCE_BLOCKER", "ARC_CONSEQUENCE_KINDS",
    "arc_stated_consequence", "mint_commitment_arcs", "drop_rows_only",
    "compute_what_it_meant",
    "compute_worth_remembering",
    "CONSEQUENCE_KINDS", "stated_consequence", "compute_slipped_prose",
    "BANNED_ECHO_PHRASES", "EchoRefused", "make_echo", "compute_echoes",
    "build_synthesis", "synthesis_text", "synthesis_refs",
]
