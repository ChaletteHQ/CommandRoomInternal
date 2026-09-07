#!/usr/bin/env python3
"""review_reasons — re-evaluate frozen pending_review clauses (BUG-8330 item 4).

capture_gate stamps `data.pending_review` + `data.review_reason` at capture
and nothing ever re-evaluated them: RRF1 already re-resolves the
"counterparty 'X' has no person record" clause AT RENDER (the row shows
"'X' — contact added ✓") while the GATING input stayed frozen — the row
visually contradicted its own behavior (it still sat in the unconfirmed
bucket, still barred from chase, still asking for an adjudication the
workspace already made).

This module is the ONE verdict both sides use now:

  - `clause_still_holds(ws, clause, cache, row=...)` — the mechanical check,
    per clause class. True/False when checkable; None when unknown.
  - `review_reason_still_holds(ws, reason, cache, row=...)` — the GATING
    rule: a reason is no longer holding ONLY when it is a SOLE clause whose
    check verdicts False. Multi-clause reasons and unknown clause classes
    hold (conservative — an unverifiable doubt keeps its question).

Consumers:
  - cru_match.load_open_commitments — the read-side fold clears
    `pending_review` in projection (zero writes; annotated
    `review_reason_auto_satisfied`) when the reason no longer holds. It is
    the caller that supplies `row` (it holds the capture event).
  - surface_drivers._display_review_reason — the render overlay (same
    resolver, same memo shape).
  - needs_review_queue.confirm_satisfied_reasons — the reason-scoped batch
    verb that formalizes the verdict as ordinary `clear_review_flags`
    events (durable history, one write path). It reads the projection's
    annotation, so it inherits the row-aware verdict for free.

Checkable clause classes (extend HERE, nowhere else):
  - "counterparty 'X' has no person record" — holds iff X still resolves to
    no person record (entity_resolve ladder — the same matcher RRF1 uses).
  - "no resolved owner" — SPEC PERSONLOOP1 §0-5, option (b): the clause is
    ROW-AWARE. capture_gate stamps it bare (`:351`) because the name it is
    about lives on the row, not in the sentence: `data.owner_external`. So
    the check reads the row it is judging and asks the SAME question the
    sibling class asks — does that name resolve to a person NOW? The doubt
    the stamp records is "we do not know who owns this"; a person record
    for that name answers it, exactly as a contact answers the
    counterparty clause.

    WHY ROW-AWARE AND NOT A RICHER STAMP (the §0-5 fork). Option (a) was to
    enrich the stamp at capture ("owner 'X' has no person record") and keep
    a row-aware fallback for the ~34 rows already on disk. That is TWO
    mechanisms for one question — a new-rows path and a legacy path — and
    the legacy path is the one that has to work, because the whole point of
    the spec is draining the pile that already exists. Row-awareness alone
    covers both populations with ONE code path, changes no stamp text (so
    no render, guard or fixture churn), and needs no migration. Measured on
    the substrate the spec was written against: every sole
    `no resolved owner` row carried `owner_external`, so nothing is left
    behind by declining the stamp change.

    A row with no `owner_external` (or no row supplied at all) verdicts
    None — unknown, so the clause HOLDS. That is the conservative
    direction: an owner nobody ever named is a real open question.
"""
from __future__ import annotations

import re
from typing import Optional

# capture_gate's exact stamp shape (RRF1's regex, moved to the shared home).
NO_PERSON_RE = re.compile(r"^counterparty '(.+)' has no person record$")

# capture_gate.py:351 — the bare stamp whose subject lives on the row.
NO_OWNER_CLAUSE = "no resolved owner"

# The row field carrying that subject (COMMITMENT_SCHEMA: "when the owner is
# named but has no entity record yet"). Named here so the clause check and
# the candidate derivation read the same key.
OWNER_EXTERNAL_FIELD = "owner_external"


def _resolves_to_person(ws, name: str, cache: dict) -> bool:
    """Does `name` resolve to a person record NOW? Memoized per call-batch;
    a resolver failure (fresh workspace, mid-sync entities.json) reads as
    'does not resolve' — the conservative direction (the clause holds)."""
    if name in cache:
        return cache[name]
    try:
        from entity_resolve import resolve_all
        cache[name] = any(r.entity_type == "person"
                          for r in resolve_all(ws, name))
    except Exception:
        cache[name] = False
    return cache[name]


def owner_external_of(row) -> str:
    """The `owner_external` name on a (projected) commitment row, or "".

    Defensive by design: the row is whatever the caller happened to hold, so
    a non-dict, a missing `data`, or a non-string field all read as "no name
    on file" rather than raising inside a render pass."""
    if not isinstance(row, dict):
        return ""
    data = row.get("data")
    value = None
    if isinstance(data, dict):
        value = data.get(OWNER_EXTERNAL_FIELD)
    if not isinstance(value, str) or not value.strip():
        value = row.get(OWNER_EXTERNAL_FIELD)
    return value.strip() if isinstance(value, str) else ""


def clause_still_holds(ws, clause: str, cache: dict,
                       row=None) -> Optional[bool]:
    """The per-clause mechanical verdict: True (still holds), False (no
    longer holds), None (not a checkable class, or checkable-in-principle
    but this row carries nothing to check — unknown).

    `row` is the projected commitment the clause was stamped on. It is
    optional so every pre-PERSONLOOP1 caller keeps working unchanged; a
    row-aware class simply verdicts None without it."""
    text = str(clause).strip()
    m = NO_PERSON_RE.match(text)
    if m:
        return not _resolves_to_person(ws, m.group(1), cache)
    if text == NO_OWNER_CLAUSE:
        name = owner_external_of(row)
        if not name:
            return None
        return not _resolves_to_person(ws, name, cache)
    return None


def review_reason_still_holds(ws, reason, cache: Optional[dict] = None,
                              row=None) -> bool:
    """The GATING rule (BUG-8330 item 4): False ONLY for a SOLE clause whose
    mechanical check says it no longer holds. Everything else — multi-clause
    reasons, unknown clause classes, empty reasons — holds (conservative).

    `cache` memoizes name resolution across a batch (the caller's render/fold
    pass); pass the same dict for every item in one pass. `row` is the
    projected commitment, needed by the row-aware clause classes."""
    if cache is None:
        cache = {}
    # ATTRIB1-B F-2 — A ROW THAT IS STILL ASKING IS NOT SATISFIED.
    #
    # The collision this closes: the ladder's ONE question is minted from a
    # counterparty NAME the extractor heard, and capture_gate stamps that
    # same row "counterparty 'X' has no person record". The clause check
    # then resolves X against the entity graph, finds the person, and
    # concludes the doubt is gone — while `counterparty_id` is STILL UNSET,
    # because resolving a name at read time does not write one. The row
    # dropped off the queue, out of door 1 (which reads the projection) and
    # out of A6's lapse-default (same projection), so the only question M
    # allows and the mechanism that makes it safe were both inert.
    #
    # The conservative reading is the true one: `attribution.question` is
    # this build's own statement that the row does not know who "you" is.
    # It outranks any clause-level satisfaction — the question is the
    # doubt, not the sentence describing it.
    if isinstance(row, dict):
        d = row.get("data") if isinstance(row.get("data"), dict) else row
        attr = (d or {}).get("attribution")
        q = attr.get("question") if isinstance(attr, dict) else None
        if isinstance(q, dict) and q.get("options"):
            return True
    clauses = [c.strip() for c in str(reason or "").split(";") if c.strip()]
    if len(clauses) != 1:
        return True
    return clause_still_holds(ws, clauses[0], cache, row=row) is not False


# ---------------------------------------------------------------------------
# HYGIENE9 (c)/(d2) — the render-time composer: stored clause -> WHOLE SENTENCE
# ---------------------------------------------------------------------------
#
# A stored `review_reason` is a GATING INPUT (the clause checks above,
# cru_match, commitment_dedup, confirm_flow, identity_reconcile all read the
# stamp text) and is never rewritten. What the customer READS is composed
# here, at render, once, for every surface that prints a reason: the plate
# and its brief / day-close / wrap cuts (`plate_view.plain_words`), the held
# queue, commitment triage, waiting-on and the Staff Meeting card
# (`surface_drivers._display_review_reason`).
#
# Two defects this replaces (v5.27.0 supervised test, 2026-09-04; PLATE1-N2
# review F-3):
#   * a RAW SCORE reached the plate — "extraction confidence 0.5 below
#     threshold" is the stamp `capture_gate` writes, and the old path passed
#     every clause it did not know verbatim;
#   * a word-by-word substitution table mangled its own input — "substrate
#     seq 12" became "the record an earlier record", "unconfirmed extraction
#     — stuck on an unowned line" became "not confirmed yet extraction —
#     quiet on an no owner line" — and PLATE1 night 2 carried those into the
#     Friday wrap's prose and .docx.
#
# The rule: a KNOWN clause shape maps to one whole sentence, with no number
# in it (a floor is ours to know, not the customer's to read). An unknown
# clause (model-composed prose is the common case on a live book) keeps its
# words, with the internal vocabulary re-said phrase-first so nothing
# ungrammatical is produced — and then passes the hard-leak scrub: a clause
# that STILL carries a score shape or a wire id after that is replaced whole
# by `FALLBACK_SENTENCE`, never printed. So the output of `render_reason` can
# carry neither shape, by construction; the plate's P4 gate and the widget
# validator pin the same two shapes from the other side.

# Exact / parametrised clause shapes -> the sentence the customer reads.
# `\1` back-references keep the quoted name; nothing else from the stamp
# survives.
REASON_SENTENCES = (
    (re.compile(r"^counterparty '(.+)' has no person record$", re.I),
     r"'\1' isn't in your contacts yet"),
    (re.compile(r"^no resolved owner$", re.I), "no owner on record"),
    (re.compile(r"^no identifiable owner$", re.I), "no owner on record"),
    (re.compile(r"^(?:low extraction confidence"
                r"|extraction confidence\s+\S+\s+below threshold)$", re.I),
     "the extractor wasn't sure this was a real commitment"),
    (re.compile(r"^extraction confidence\s+.*?\bis not a number\b.*$", re.I),
     "the extractor's confidence on this one was unreadable, so it's here "
     "for a look"),
    (re.compile(r"^match (?:score|confidence)\s+\S+\s+below floor$", re.I),
     "it only loosely matched the source"),
    (re.compile(r"^no (?:resolved )?counterparty (?:identified )?for a "
                r"promise$", re.I),
     "it's a promise with nobody named on the other side"),
    (re.compile(r"^observed tier$", re.I), "heard, not promised"),
    (re.compile(r"^substrate seq\s*#?\s*\d+$", re.I), "from an earlier record"),
    (re.compile(r"^same-name collision on an auto contact capture$", re.I),
     "two contacts share this name, so it couldn't be filed automatically"),
)

# The fallback table for a clause no shape above knows: PHRASE-level rows
# first (so a multi-word internal term is re-said as one unit), then the
# single words. Every replacement is chosen to keep the sentence readable
# in place — "an unowned line" -> "an ownerless line", not "an no owner
# line". This is `plate_view.PLAIN_WORDS` (same object, one table).
FALLBACK_WORDS = (
    (re.compile(r"^counterparty '(.+)' has no person record$", re.I),
     r"'\1' isn't in your contacts yet"),
    (re.compile(r"\bunconfirmed extraction\b", re.I),
     "a capture not confirmed yet"),
    (re.compile(r"\bsubstrate seq\s*#?\s*\d+\b", re.I), "an earlier record"),
    (re.compile(r"\bseq\s*#?\s*\d+\s+substrate\b", re.I), "an earlier record"),
    (re.compile(r"\bseq\s*#?\s*\d+\b", re.I), "an earlier record"),
    (re.compile(r"\bobserved tier\b", re.I), "heard, not promised"),
    # Fix round F-6 — "score" is re-said as a WORD the reader has a model
    # for, never as "confidence" (that word plus a number is the exact shape
    # the scrub refuses, so the old row collapsed every unknown "match
    # score…" clause to the generic sentence). The number itself, if any,
    # is handled by the composer's decimal rule.
    (re.compile(r"\bmatch score\b", re.I), "how closely it matched"),
    (re.compile(r"\bcounterparty\b", re.I), "the other person"),
    (re.compile(r"\bunconfirmed\b", re.I), "not confirmed yet"),
    (re.compile(r"\bpending_review\b", re.I), "waiting on you"),
    (re.compile(r"\bneeds_review\b", re.I), "waiting on you"),
    (re.compile(r"\bunowned\b", re.I), "ownerless"),
    (re.compile(r"\bstuck\b", re.I), "quiet"),
    (re.compile(r"\bno resolved owner\b", re.I), "no owner on record"),
    (re.compile(r"\bscore\b", re.I), "rating"),
    (re.compile(r"\btier\b", re.I), "level"),
    (re.compile(r"\bsubstrate\b", re.I), "the record"),
    (re.compile(r"\borchestrator\b", re.I), "the scheduled run"),
)

# The two shapes no rendered reason may carry (v5.27.0 test, B2.5 and A2/C):
# a score — "confidence 0.5", "0.55 below threshold", "below floor" — and a
# wire id — `pcand:53504c35d5f8`, `bp_…`, `cmt_…`, `person:…`, or any
# `<prefix>:<hex>` a future surface mints. Shared with the plate gate and the
# widget validator so all three fences are the same two regexes.
SCORE_SHAPE_RE = re.compile(
    r"(?:\bconfidence\s+-?\d|\b\d+(?:\.\d+)?\s+below\s+(?:threshold|floor)\b"
    r"|\bbelow\s+(?:threshold|floor)\b|\b(?:extraction|match)\s+confidence\b)",
    re.I)
WIRE_ID_SHAPE_RE = re.compile(
    r"(?:\b[a-z][a-z0-9_]*:[0-9a-f]{6,}\b"
    r"|\b(?:person|cru|org|project|dont_forget|schedule|pcand):\S+"
    r"|\bbp_[0-9a-f]{6,}\b|\bcmt_[A-Za-z0-9_]+\b"
    r"|\b(?:commitment_seq|seq|event)_\d+\b)")

FALLBACK_SENTENCE = "the extractor flagged this one for a look"
# Fix round F-6 — a DECIMAL in an unknown clause is a score in practice
# ("match score 0.4 below the floor, twice"): the words may be new, the
# number is still not the customer's to read. Composer-only: the plate gate
# and the widget validator keep the two named shapes, because a context
# line legitimately carries other numbers (counts, dates).
_DECIMAL_RE = re.compile(r"\b\d+\.\d+\b")


def carries_hard_leak(text) -> Optional[str]:
    """The name of the shape `text` carries ("score" / "wire id"), or None.
    The one predicate the composer, the plate gate and the widget validator
    share, so a shape added here is fenced on every surface at once."""
    s = str(text or "")
    if SCORE_SHAPE_RE.search(s):
        return "score"
    if WIRE_ID_SHAPE_RE.search(s):
        return "wire id"
    return None


def render_clause(clause) -> str:
    """One stored clause -> the sentence the customer reads. Never a score,
    never a wire id: a clause that would still carry one is replaced whole."""
    text = str(clause or "").strip()
    if not text:
        return ""
    for pat, repl in REASON_SENTENCES:
        if pat.match(text):
            return pat.sub(repl, text)
    out = text
    for pat, repl in FALLBACK_WORDS:
        out = pat.sub(repl, out)
    if carries_hard_leak(out) or _DECIMAL_RE.search(out):
        return FALLBACK_SENTENCE
    return out


def render_reason(reason) -> str:
    """A stored `review_reason` (one or more `; `-joined clauses) -> the
    customer's sentences, joined the same way. Display only: the stored
    string is a gating input and is never written back."""
    clauses = [c.strip() for c in str(reason or "").split(";")]
    rendered = [render_clause(c) for c in clauses if c]
    return "; ".join(r for r in rendered if r)


__all__ = [
    "NO_PERSON_RE",
    "NO_OWNER_CLAUSE",
    "OWNER_EXTERNAL_FIELD",
    "owner_external_of",
    "clause_still_holds",
    "review_reason_still_holds",
    "REASON_SENTENCES",
    "FALLBACK_WORDS",
    "FALLBACK_SENTENCE",
    "SCORE_SHAPE_RE",
    "WIRE_ID_SHAPE_RE",
    "carries_hard_leak",
    "render_clause",
    "render_reason",
]
