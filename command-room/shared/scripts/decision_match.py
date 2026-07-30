#!/usr/bin/env python3
"""
Decision-CRU layer (v3.4.5+).

Sister module to `cru_match.py`. Auto-resolves open decisions when a meeting
transcript proves the decision was acted on, reversed, or superseded by a
newer decision. Decisions, unlike commitments, do not have an obvious "send
an email and we're done" closure path — they get re-decided, executed, or
abandoned, almost always in conversation. So this module ships with Path 3
(past-meetings transcript) only; Path 1 (apply-choices send) is left unwired
for now because emails rarely state decision outcomes explicitly.

THE MATCHING MODEL
==================

Same scoring engine as `cru_match.py` (max of unigram overlap coefficient
and bigram Jaccard, tokenizer with stopword filter). Decisions are
typically titled with content words ("Pivot to product-led GTM", "Switch
ERP vendor to NetSuite") so the same tokenizer treats them well.

Thresholds are intentionally TIGHTER than commitments — decisions are
higher-stakes; a false-positive auto-resolution loses real history.

  DECISION_HIGH_CONFIDENCE_THRESHOLD = 0.65  -> auto-resolve / supersede
  below 0.65                                 -> no action

(No medium-confidence "pending_review" path yet. If telemetry shows the
0.65 threshold misses too many real closures, we'll add it and surface
the proposals in Pulse.)

RECOMMENDATION TYPES
====================

For each open decision + transcript pair:

  - score >= HIGH AND completion language AND NOT reversal language
        -> "decision_resolved" (the decision was executed)
  - score >= HIGH AND reversal language
        -> "decision_superseded" (a newer decision overrides the older one;
            the writer is responsible for emitting the NEW decision event
            separately — this helper only emits the superseded marker)
  - else
        -> "no_action"

Per CONTRACT.md Rule 24 (CRU layer is silent): every event written here is
silent in chat. The user sees the effect on the next DECISION_LOG view
regen (superseded / resolved decisions filter out of the active list).
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Iterable, Optional

# Reuse the scoring + tokenizer infrastructure from cru_match — same engine,
# different domain.
from cru_match import score_match

# v3.5.0+: canonical source in shared/scripts/confidence.py. Aliased here
# for back-compat — existing callers and __all__ exports keep the old name.
from confidence import DECISION_MATCH_AUTO_RESOLVE

# Bilingual overlay (Spanish beta) — inert for English installs. See
# shared/scripts/lexicon.py + references/SPANISH_BUILD_PLAN.md.
try:
    import lexicon as _lex
except Exception:  # pragma: no cover
    _lex = None


def _phrases(key, default):
    """Merged decision phrase-list, or the English default when the overlay is
    inactive/absent (production path)."""
    if _lex is None:
        return default
    return _lex.load_lexicon_terms("decision_match", key, default)


DECISION_HIGH_CONFIDENCE_THRESHOLD = DECISION_MATCH_AUTO_RESOLVE  # 0.65


# Phrases indicating a decision was acted on / executed. Slightly different
# from commitment-completion phrases — decisions get "selected" / "went with"
# rather than "sent" / "delivered."
DECISION_COMPLETION_PHRASES = (
    "went with",
    "going with",
    "going forward with",
    "moving forward with",
    "moving ahead with",
    "decided on",
    "decided to go with",
    "we chose",
    "we selected",
    "we picked",
    "signed with",
    "signed the",
    "committed to",
    "locked in",
    "kicked off",
    "starting with",
    "rolled out",
    "launched with",
)


# Phrases indicating a decision was reversed / superseded. The transcript
# describes the OLD decision being abandoned in favor of a NEW one.
DECISION_REVERSAL_PHRASES = (
    "changed our mind",
    "changed my mind",
    "actually going with",
    "actually we're going",
    "actually decided",
    "scratch that",
    "scratch the",
    "instead of",
    "switching to",
    "switching from",
    "reconsidered",
    "reversing",
    "abandoning the",
    "pulling back from",
    "walked back",
    "reverted",
    "rethinking",
    "pivoting from",
    "pivoting away",
    "decided against",
)


_DECISION_FIELD_ALIASES = {
    # Mirror the commitment alias-table approach so decisions written in
    # varying shapes by different writers (meeting-notes, decision-log,
    # follow-up-ritual) all read correctly.
    "title": ("title", "decision", "summary"),
    "decided_by": ("decided_by", "made_by", "owner_id"),
    "status": ("status", "state"),
    "rationale": ("rationale", "reason", "why"),
}


def _decision_field(ev: dict, field: str) -> Any:
    """Read a decision-event field handling shape variants. Tries
    `data.<alias>` first across the alias chain, then top-level `<alias>`.
    Returns None if nothing found.
    """
    d = ev.get("data") or {}
    aliases = _DECISION_FIELD_ALIASES.get(field, (field,))
    for alias in aliases:
        v = d.get(alias)
        if v not in (None, ""):
            return v
    for alias in aliases:
        v = ev.get(alias)
        if v not in (None, ""):
            return v
    return None


def _decision_id(ev: dict) -> str:
    """Stable id for an open decision. Mirrors `_commitment_id` shape so
    closing events can point back via `data.decision_id`."""
    d = ev.get("data") or {}
    return d.get("id") or ev.get("id") or f"decision_seq_{ev.get('seq', '?')}"


def detect_completion_signal(text: Optional[str]) -> bool:
    """True if `text` contains a phrase suggesting a decision was executed
    (`went with`, `signed with`, `committed to`, etc.).
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in _phrases("completion_phrases", DECISION_COMPLETION_PHRASES))


def detect_reversal_signal(text: Optional[str]) -> bool:
    """True if `text` contains a phrase suggesting a decision was reversed
    (`changed our mind`, `scratch that`, `switching to`, etc.).
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in _phrases("reversal_phrases", DECISION_REVERSAL_PHRASES))


# -----------------------------------------------------------------------------
# Open-decision loader
# -----------------------------------------------------------------------------


def load_open_decisions(events_jsonl_path: str | Path) -> list[dict]:
    """Read events.jsonl and return all `type: decision` events that have
    NOT been closed by a subsequent `decision_resolved` or
    `decision_superseded` event referencing them.

    Mirrors `cru_match.load_open_commitments`. Returns full event dicts so
    the caller can pull title / decided_by / primary_thread_id as needed.
    """
    path = Path(events_jsonl_path)
    if not path.exists():
        return []

    open_evs: list[dict] = []
    closed_ids: set[str] = set()

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            et = ev.get("type") or ev.get("event") or ""
            d = ev.get("data") or {}
            if et in ("decision_resolved", "decision_superseded"):
                did = (
                    d.get("decision_id")
                    or d.get("id")
                    or ev.get("decision_id")
                    or ev.get("id")
                )
                if did:
                    closed_ids.add(did)
            elif et == "decision":
                status = _decision_field(ev, "status") or "active"
                # Active is the default; legacy decisions sometimes carry
                # "Active" (title-cased) or no status at all. Treat anything
                # other than explicit "superseded" / "resolved" string as open.
                if str(status).lower() not in ("superseded", "resolved"):
                    open_evs.append(ev)

    return [d for d in open_evs if _decision_id(d) not in closed_ids]


# -----------------------------------------------------------------------------
# Path 3 — match a meeting transcript to open decisions
# -----------------------------------------------------------------------------


def match_transcript_to_decisions(
    *,
    open_decisions: list[dict],
    attendee_person_ids: Iterable[str],
    transcript_text: str,
) -> list[dict]:
    """Path 3 — score a meeting transcript against open decisions.

    Filters open_decisions to those touching at least one attendee (via
    `person_ids` on the original decision event), then scores transcript
    against each candidate's title.

    Recommendation logic (conservative, in order):
      - score >= HIGH AND reversal AND NOT completion -> "decision_superseded"
      - score >= HIGH AND completion AND NOT reversal -> "decision_resolved"
      - score >= HIGH AND both signals present       -> "no_action"
            (ambiguous — meeting mentions both executing and switching;
             don't auto-act. Surface in transcript review instead.)
      - score >= HIGH AND neither signal             -> "no_action"
            (title match alone means "the topic came up," not that it
             closed. Same conservative bias as commitments Path 3.)
      - score < HIGH                                  -> "no_action"

    Returns list of dicts sorted by score descending. Caller decides which
    recommendations to write events for.
    """
    if not transcript_text or not open_decisions:
        return []
    attendee_set = {a for a in attendee_person_ids if a}

    has_completion = detect_completion_signal(transcript_text)
    has_reversal = detect_reversal_signal(transcript_text)

    results: list[dict] = []
    for ev in open_decisions:
        # If we know the attendees and the decision tracked specific people,
        # require at least one attendee overlap. Decisions with empty
        # person_ids are workspace-wide; let them through.
        person_ids = set(ev.get("person_ids") or [])
        if attendee_set and person_ids and not (attendee_set & person_ids):
            continue

        title = _decision_field(ev, "title") or ""
        if not title:
            continue
        score = score_match(transcript_text, title)

        if score < DECISION_HIGH_CONFIDENCE_THRESHOLD:
            recommendation = "no_action"
        elif has_reversal and not has_completion:
            recommendation = "decision_superseded"
        elif has_completion and not has_reversal:
            recommendation = "decision_resolved"
        else:
            # Both signals (ambiguous) OR neither signal (topic came up but
            # nothing closed). Stay conservative.
            recommendation = "no_action"

        results.append({
            "decision_id": _decision_id(ev),
            "score": score,
            "recommendation": recommendation,
            "title": title,
            "primary_thread_id": ev.get("primary_thread_id") or "",
            "has_completion_signal": has_completion,
            "has_reversal_signal": has_reversal,
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# -----------------------------------------------------------------------------
# Event builders
# -----------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def build_decision_resolved_event(
    *,
    decision_id: str,
    primary_thread_id: str,
    source_skill: str,
    evidence: str,
    next_seq: int,
) -> dict:
    """Build a `decision_resolved` event — the decision was executed /
    acted on. The original decision event stays in the log; the resolved
    event closes it so the DECISION_LOG view filters it out of the
    "Active" list.

    Caller atomic_append_jsonl-s it.
    """
    return {
        "seq": next_seq,
        "ts": _now_iso(),
        "type": "decision_resolved",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": {
            "decision_id": decision_id,
            "evidence": evidence[:200] if evidence else "",
        },
    }


def build_decision_superseded_event(
    *,
    decision_id: str,
    primary_thread_id: str,
    source_skill: str,
    evidence: str,
    next_seq: int,
    superseded_by_decision_seq: Optional[int] = None,
) -> dict:
    """Build a `decision_superseded` event — a newer decision overrides
    this one. If the new decision is identifiable, pass its seq via
    `superseded_by_decision_seq` so the log can render the cross-link.
    The new decision itself is written separately (typically by
    meeting-notes' decision extractor on the same transcript pass).
    """
    data = {
        "decision_id": decision_id,
        "evidence": evidence[:200] if evidence else "",
    }
    if superseded_by_decision_seq is not None:
        data["superseded_by_decision_seq"] = superseded_by_decision_seq
    return {
        "seq": next_seq,
        "ts": _now_iso(),
        "type": "decision_superseded",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": data,
    }


__all__ = [
    "DECISION_HIGH_CONFIDENCE_THRESHOLD",
    "DECISION_COMPLETION_PHRASES",
    "DECISION_REVERSAL_PHRASES",
    "detect_completion_signal",
    "detect_reversal_signal",
    "load_open_decisions",
    "match_transcript_to_decisions",
    "build_decision_resolved_event",
    "build_decision_superseded_event",
    "_decision_field",
    "_decision_id",
]
