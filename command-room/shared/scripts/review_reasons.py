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
    clauses = [c.strip() for c in str(reason or "").split(";") if c.strip()]
    if len(clauses) != 1:
        return True
    return clause_still_holds(ws, clauses[0], cache, row=row) is not False


__all__ = [
    "NO_PERSON_RE",
    "NO_OWNER_CLAUSE",
    "OWNER_EXTERNAL_FIELD",
    "owner_external_of",
    "clause_still_holds",
    "review_reason_still_holds",
]
