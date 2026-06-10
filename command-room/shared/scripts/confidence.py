#!/usr/bin/env python3
"""
Shared confidence + match-score constants (v3.5.0+).

Before v3.5.0 every orchestrator hardcoded its own thresholds inline:
  - orchestrator-commitments.md Phase 3 filter:       confidence >= 0.7
  - orchestrator-past-meetings.md Phase 4 auto-commit: confidence >= 0.8
  - orchestrator-past-meetings.md Phase 4.5b people-crm: confidence >= 0.85
  - orchestrator-dont-forget.md Phase 5 auto-apply:    confidence >= 0.85
  - decision_match.py:                                 score >= 0.65
  - cru_match.py (Path 1 / Path 3 match scoring):     score >= 0.55 (HIGH), 0.30 (PENDING)

Five different numbers across the codebase, all called "confidence threshold"
or "match threshold" depending on which file you read. Local choices were
reasonable; the cross-orchestrator inconsistency made it hard to reason about
"high-confidence" globally and impossible to tune via a single dial.

v3.5.0 consolidates into two named families. Callers import from here.

TWO FAMILIES — they are semantically different:

EXTRACTION CONFIDENCE — how confident the EXTRACTION pipeline is that a
particular commitment / decision / person-record actually exists in the
source material (transcript, email, etc.). Stored on the event as
`data.confidence` (sometimes `classification_confidence`). Used by
downstream consumers as a quality filter: only surface events the extractor
believed in.

MATCH SCORE — how strongly a NEW event matches an EXISTING event for
cross-reference purposes (CRU layer: did this outbound email match this open
commitment? did this transcript chunk complete this open decision?). Computed
in cru_match.py / decision_match.py via Jaccard + overlap-coefficient.

These are different axes. A high-extraction-confidence commitment can have a
low-match-score to a particular email; that means "we're sure the commitment
exists, we're not sure this email is what fulfilled it." Conflating the two
under one threshold was the v3.5.0 audit finding.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# EXTRACTION CONFIDENCE — used by consumers to filter low-quality extractions
# ---------------------------------------------------------------------------

# Below this, we don't even surface the item in the daily Commitments fire.
# Equivalent to "the extractor isn't sure this is really a commitment."
# Used by: orchestrator-commitments Phase 3 filter.
CONFIDENCE_SURFACE_MIN = 0.7

# Auto-commit threshold for fresh extraction during past-meetings processing.
# Items above this are written committed=true to events.jsonl; items below
# go to pending_review.
# Used by: orchestrator-past-meetings Phase 4 step 4-5.
CONFIDENCE_AUTOCOMMIT = 0.8

# Auto-apply threshold for people-crm record changes detected during real-time
# transcript processing. Higher than autocommit because people-record changes
# write to entities.json and false-positives corrupt the canonical relationship
# layer.
# Used by: orchestrator-past-meetings Phase 4.5b people-crm pass,
#          orchestrator-dont-forget Phase 5 auto-apply.
CONFIDENCE_AUTOAPPLY_PEOPLE = 0.85


# ---------------------------------------------------------------------------
# MATCH SCORES — used by CRU layer to decide auto-resolve vs. pending review
# ---------------------------------------------------------------------------

# Cross-reference match (cru_match.match_send_to_commitments,
# match_transcript_to_commitments). At or above this score, auto-resolve the
# matched commitment silently. Below this but >= PENDING, write a
# commitment_review_proposed event for next Pulse fire to surface for
# one-click confirm.
# Used by: cru_match.py HIGH_CONFIDENCE_THRESHOLD.
MATCH_SCORE_AUTO_RESOLVE = 0.55

# Below auto-resolve, above this: pending review surface. Below this: no action.
# Used by: cru_match.py PENDING_REVIEW_THRESHOLD.
MATCH_SCORE_PENDING_REVIEW = 0.30

# Decision-match auto-resolve threshold. Tighter than commitments because
# decision false-positives lose real history. No pending review path in v3.4.5;
# borderline matches simply don't act.
# Used by: decision_match.py DECISION_HIGH_CONFIDENCE_THRESHOLD.
DECISION_MATCH_AUTO_RESOLVE = 0.65


__all__ = [
    "CONFIDENCE_SURFACE_MIN",
    "CONFIDENCE_AUTOCOMMIT",
    "CONFIDENCE_AUTOAPPLY_PEOPLE",
    "MATCH_SCORE_AUTO_RESOLVE",
    "MATCH_SCORE_PENDING_REVIEW",
    "DECISION_MATCH_AUTO_RESOLVE",
]
