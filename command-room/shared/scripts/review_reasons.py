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

  - `clause_still_holds(ws, clause, cache)` — the mechanical check, per
    clause class. True/False when checkable; None when unknown.
  - `review_reason_still_holds(ws, reason, cache)` — the GATING rule: a
    reason is no longer holding ONLY when it is a SOLE clause whose check
    verdicts False. Multi-clause reasons and unknown clause classes hold
    (conservative — an unverifiable doubt keeps its question).

Consumers:
  - cru_match.load_open_commitments — the read-side fold clears
    `pending_review` in projection (zero writes; annotated
    `review_reason_auto_satisfied`) when the reason no longer holds.
  - surface_drivers._display_review_reason — the render overlay (same
    resolver, same memo shape).
  - needs_review_queue.confirm_satisfied_reasons — the reason-scoped batch
    verb that formalizes the verdict as ordinary `clear_review_flags`
    events (durable history, one write path).

Checkable clause classes (extend HERE, nowhere else):
  - "counterparty 'X' has no person record" — holds iff X still resolves to
    no person record (entity_resolve ladder — the same matcher RRF1 uses).
"""
from __future__ import annotations

import re
from typing import Optional

# capture_gate's exact stamp shape (RRF1's regex, moved to the shared home).
NO_PERSON_RE = re.compile(r"^counterparty '(.+)' has no person record$")


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


def clause_still_holds(ws, clause: str, cache: dict) -> Optional[bool]:
    """The per-clause mechanical verdict: True (still holds), False (no
    longer holds), None (not a checkable class — unknown)."""
    m = NO_PERSON_RE.match(str(clause).strip())
    if m:
        return not _resolves_to_person(ws, m.group(1), cache)
    return None


def review_reason_still_holds(ws, reason, cache: Optional[dict] = None) -> bool:
    """The GATING rule (BUG-8330 item 4): False ONLY for a SOLE clause whose
    mechanical check says it no longer holds. Everything else — multi-clause
    reasons, unknown clause classes, empty reasons — holds (conservative).

    `cache` memoizes name resolution across a batch (the caller's render/fold
    pass); pass the same dict for every item in one pass."""
    if cache is None:
        cache = {}
    clauses = [c.strip() for c in str(reason or "").split(";") if c.strip()]
    if len(clauses) != 1:
        return True
    return clause_still_holds(ws, clauses[0], cache) is not False


__all__ = [
    "NO_PERSON_RE",
    "clause_still_holds",
    "review_reason_still_holds",
]
