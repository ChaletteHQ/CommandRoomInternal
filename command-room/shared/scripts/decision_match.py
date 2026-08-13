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
the proposals on the staff meeting.)

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
try:
    from text_clip import clip  # noqa: E402
except ImportError:  # pragma: no cover — direct-path fallback
    import sys as _sys_tc
    from pathlib import Path as _Path_tc
    _sys_tc.path.insert(0, str(_Path_tc(__file__).resolve().parent))
    from text_clip import clip  # noqa: E402

import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

# Reuse the scoring + tokenizer infrastructure from cru_match — same engine,
# different domain.
from cru_match import load_events_defensively, score_match

# WALKFIX1 Item A — the same-fire fence reads a fence timestamp exactly the way
# the commitment rail does; two copies of that normalization is how two rails
# drift apart.
from cru_match import _normalize_fire_start, _parse_ts  # noqa: E402
from event_time import event_time  # noqa: E402

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


DECISION_HIGH_CONFIDENCE_THRESHOLD = DECISION_MATCH_AUTO_RESOLVE


# =============================================================================
# WALKFIX1 FR-2 — supersedes are RECOMMEND-ONLY.  ⚠ M-STRIKEABLE ⚠
# =============================================================================
#
# THE EVIDENCE. On 2026-08-10 one past-meetings fire processed TWO transcripts
# (both from the same client) and wrote NINETEEN `decision_superseded` events.
# Six targeted decisions the same fire had just extracted — the Item A fence
# kills those. The other thirteen targeted OLD decisions, and four of four
# sampled from the live ledger were plainly wrong: a client onboarding call was
# recorded as reversing an unrelated internal meeting time, an office-lease
# decision, another person's login preference, and another client's video
# platform.
#
# WHY SCORING CANNOT BE TRUSTED HERE, in three lines of mechanism:
#   1. `score_match` is `max(unigram overlap coefficient, bigram Jaccard)`, and
#      the overlap coefficient divides by `min(|A|,|B|)`. With a 45-minute
#      transcript as A and a short title as B, the divisor is the TITLE's token
#      set — any title whose content words all appear anywhere in the
#      transcript scores 1.0.
#   2. `detect_reversal_signal` is a WHOLE-TRANSCRIPT boolean over 20 phrases,
#      one of which is "instead of".
#   3. The two are ANDed with NO locality requirement — the reversal language
#      need not be anywhere near the matched title.
# There is no second gate. The attendee filter is not one: every affected
# decision carried the operator's own person id, and he is on every call.
#
# WHY POSTURE AND NOT A THRESHOLD FIX. A supersede is a WRITE to the canonical
# decision ledger, and a superseded decision drops out of the active view — so
# a wrong one silently removes a real, unreversed decision from the customer's
# "current decisions". Fixing the matcher properly (locality + a capped
# divisor) is a redesign with its own risk and its own train. Refusing to
# auto-write on a signal this weak costs one review click and cannot be wrong
# in the dangerous direction. Recommend-only is the cheap half of the right
# answer, taken now.
#
# WHAT IS UNCHANGED: the scoring, the thresholds, the phrase lists, the
# attendee filter, and `decision_resolved` (completion language, a different
# signal, not implicated in this finding). Only the supersede WRITE moves.
#
# ⚠ M'S TWO PATHS. They are DIFFERENT, and conflating them is what broke the
# first version of this list (confirm review C-2: following it produced an
# ImportError, because item 1 deletes constants that a test imports).
#
# PATH A — REVERSE THE POSTURE, keep the machinery. THREE edits, and the
#   count is verified by execution rather than reasoned about (the first two
#   drafts of this block said "one" and then "two"; both were wrong, and both
#   were caught by actually performing the path):
#
#    (i)  RECOMMEND_ONLY_SUPERSEDES = False  — the runtime change. On its own
#         this is enough to make supersedes auto-write again immediately.
#   (ii)  `tests/run_walkfix1_recommend_only_test.py` — delete the single
#         check "the SHIPPED default is recommend-only". It exists so a
#         refactor cannot flip this flag SILENTLY; M reversing it on purpose
#         is the supported act, and this check is the one thing that has to
#         acknowledge the decision.
#  (iii)  `tests/run_walkfix1_mutation_test.py` — delete mutations `W19` and
#         `W19b` and their docstring rows (`W20`/`W22` are FR-3's; leave
#         them). Both prove things about a posture
#         that is now off: with the flag False, deleting the seam is a no-op,
#         so the mutation cannot redden anything and the harness fails.
#
#   Nothing else moves. Every other assertion follows the flag rather than the
#   posture — `run_decision_match_test.py`, the fence suite's `RECOMMEND`
#   alias, and section [1] of the recommend-only suite all read
#   `RECOMMEND_ONLY_SUPERSEDES` and assert whichever posture is configured.
#   This is the path if the posture is wrong but the proposal vocabulary is
#   worth keeping.
#
# PATH B — REMOVE IT ENTIRELY. Ten locations, and the battery is RED until
#   all ten are done (verified by execution, not asserted):
#
#   1. `shared/scripts/decision_match.py` — this block, the flag, the two
#      RECOMMEND_* constants below, the seam in `match_transcript_to_decisions`
#      (marked "THE RECOMMEND-ONLY SEAM"),
#      `build_decision_supersede_proposal_event`, and the three `__all__`
#      entries naming them;
#   2. `shared/data-schemas/events.schema.json` — the
#      `decision_supersede_proposed` enum member;
#   3. `shared/EVENT_TYPES.md` — the Decision-review lane entry;
#   4. `shared/scripts/render_decision_log.py` — the `proposals_map` in
#      `_categorize_decisions`, its `[SUPERSEDE PROPOSED]` rendering in
#      `_format_decision_line`, and the docstring paragraph naming it;
#   5. `skills/enable-command-room-schedules/references/orchestrator-past-meetings.md`
#      — the Phase 4.6.b recommend-only paragraph, the proposal branch in its
#      bash block (restore `build_decision_superseded_event`), and the
#      `supersede_proposed=` counter in the diagnostic print;
#   6. `tests/run_walkfix1_recommend_only_test.py` — the whole suite.
#      (FR-3's pins live in `run_walkfix1_decision_repair_test.py` and
#      STAY — FR-3 is not M-strikeable. They were in this suite once,
#      and striking FR-2 disarmed FR-3 and left two mutations pointing
#      at a deleted file. Found by performing the strike.);
#   7. `tests/run_walkfix1_decision_fence_test.py` — the MODULE-level
#      `RECOMMEND = (...)` conditional reverts to the plain
#      `"decision_superseded"` literal. One binding; every section in that
#      suite reads that one name (it was function-local once, with a second
#      section referencing the constant directly, and striking only the named
#      site left a NameError — found by performing the strike);
#   8. `tests/run_walkfix1_mutation_test.py` — mutations `W19`, `W19b` and
#      `W21` plus their docstring rows. `W20` and `W22` are FR-3's and
#      stay, pointing at the repair suite;
#   9. `CHANGELOG.md` — the "A meeting no longer closes decisions it only
#      mentioned" section of the WALKFIX1 Unreleased block;
#  10. `tests/run_decision_match_test.py` — **the one the first list missed.**
#      It imports the three RECOMMEND_* symbols item 1 deletes, so leaving it
#      alone is an ImportError, not a stale expectation. Drop them from the
#      import and assert the `"decision_superseded"` literal again.
#
RECOMMEND_ONLY_SUPERSEDES = True

# The recommendation the scorer produces, and the one the fire is allowed to
# act on. Spelled as constants so the seam is one identifier, not a literal
# scattered across a matcher, an orchestrator and three suites.
RECOMMEND_SUPERSEDED = "decision_superseded"
RECOMMEND_SUPERSEDE_PROPOSED = "decision_supersede_proposed"


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

    # EVGUARD — the hand-rolled loop that used to live here caught only
    # JSONDecodeError, so a top-level bare-string line parsed fine and the next
    # `ev.get()` raised AttributeError out of the loader (Sub-bug #14b, second
    # half). The canonical reader handles both malformed shapes and is
    # shard-transparent; since_ts=None = full history.
    events, _skipped = load_events_defensively(path, since_ts=None)
    for ev in events:
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
    workspace_root=None,
    exclude_captured_since=None,
) -> list[dict]:
    """Path 3 — score a meeting transcript against open decisions.

    Filters open_decisions to those touching at least one attendee (via
    `person_ids` on the original decision event), then scores transcript
    against each candidate's title.

    THE SAME-FIRE CIRCULARITY FENCE (WALKFIX1 Item A)
    -------------------------------------------------
    `exclude_captured_since` is the fence Phase 4.6 has carried since
    RECONFENCE and Phase 4.6.b did not. Pass the fire's own start (window
    start / run start, ISO or datetime) and any decision captured at or after
    it is dropped as a candidate BEFORE scoring — never created-then-
    suppressed, so it cannot become a supersede TARGET at all.

    Why the fence has to exist here specifically: this pass reads the SAME
    transcripts Phase 4 just extracted decisions from. A call that decides
    something and then discusses switching approach carries both the decision
    and reversal-shaped language in one text, so without the fence the fire
    scores its own seconds-old writes against the words they came out of and
    supersedes them. Field-reported 2026-08-10: one fire wrote 8 decisions and
    superseded 6 of them, evidence "Past meeting transcript (reversal
    language)". Nothing was lost — lineage is append-only — but the log read
    as though the call reversed itself, and any "current decisions" view for
    that thread drops the call's live outcomes.

    `None` (the default) leaves the fence INERT and every pre-WALKFIX1 caller
    byte-identical. A malformed value fails SAFE and loud (never silently
    inert) — `cru_match._normalize_fire_start` is the shared normalizer, so
    both rails read one implementation of what a fence timestamp is. A
    candidate whose own capture time is unparseable is EXCLUDED, the same
    posture layer 2 documents for commitments: on this rail an admitted
    unknown is a supersede of real history.

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
    # WALKFIX1 Item A — the fence's own input, normalized through the SAME
    # helper Phase 4.6 uses. `None` -> inert; unparseable -> fails safe + loud.
    fire_start = _normalize_fire_start(exclude_captured_since)

    results: list[dict] = []
    for ev in open_decisions:
        # Layer 2 — a transcript cannot corroborate a conclusion about a
        # decision extracted from that same transcript in this same fire.
        # Dropped before scoring, so the row is NEVER CREATED.
        if fire_start is not None:
            captured = _parse_ts(event_time(ev))
            if captured is None or captured >= fire_start:
                continue
        # If we know the attendees and the decision tracked specific people,
        # require at least one attendee overlap. Decisions with empty
        # person_ids are workspace-wide; let them through.
        #
        # BUG-8244 fail-closed: an EMPTY attendee_set used to skip this
        # filter entirely — every person-scoped decision workspace-wide got
        # scored against the transcript, and a coincidental title match plus
        # a generic completion phrase could auto-write decision_resolved.
        # This was the one reader that turned a missing meeting binding into
        # wrong PERSISTED state. Now an unverifiable overlap (no attendees
        # resolved, decision person-scoped) may still be scored, but its
        # auto recommendations are downgraded to "no_action" and flagged
        # `attendee_unverified` — surface in review, never auto-act.
        person_ids = set(ev.get("person_ids") or [])
        attendee_unverified = bool(person_ids) and not attendee_set
        if attendee_set and person_ids and not (attendee_set & person_ids):
            continue

        title = _decision_field(ev, "title") or ""
        if not title:
            continue
        score = score_match(transcript_text, title)

        # BUG-8330 item 6 — resolve through the calibration accessor so a
        # workspace override moves this threshold; baked default unchanged.
        try:
            from confidence import decision_match_auto_resolve as _dmar
            _threshold = _dmar(workspace_root)
        except Exception:
            _threshold = DECISION_HIGH_CONFIDENCE_THRESHOLD
        if score < _threshold:
            recommendation = "no_action"
        elif has_reversal and not has_completion:
            recommendation = RECOMMEND_SUPERSEDED
        elif has_completion and not has_reversal:
            recommendation = "decision_resolved"
        else:
            # Both signals (ambiguous) OR neither signal (topic came up but
            # nothing closed). Stay conservative.
            recommendation = "no_action"
        if attendee_unverified and recommendation != "no_action":
            recommendation = "no_action"

        # ================= THE RECOMMEND-ONLY SEAM (WALKFIX1 FR-2) ==========
        # M-STRIKEABLE. See RECOMMEND_ONLY_SUPERSEDES for the argument and the
        # complete strike set. Scoring above is UNTOUCHED; this changes only
        # what the fire is allowed to WRITE off the back of it.
        if RECOMMEND_ONLY_SUPERSEDES and recommendation == RECOMMEND_SUPERSEDED:
            recommendation = RECOMMEND_SUPERSEDE_PROPOSED
        # ===================================================================

        results.append({
            "decision_id": _decision_id(ev),
            "score": score,
            "recommendation": recommendation,
            "title": title,
            "primary_thread_id": ev.get("primary_thread_id") or "",
            "has_completion_signal": has_completion,
            "has_reversal_signal": has_reversal,
            "attendee_unverified": attendee_unverified,
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
            "evidence": clip(evidence) if evidence else "",
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
        "evidence": clip(evidence) if evidence else "",
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


def build_decision_supersede_proposal_event(
    *,
    decision_id: str,
    primary_thread_id: str,
    source_skill: str,
    evidence: str,
    next_seq: int,
    score: Optional[float] = None,
    title: str = "",
) -> dict:
    """Build a `decision_supersede_proposed` event — WALKFIX1 FR-2.

    A PROPOSAL, not a closure. It changes no decision's status: the decision
    stays exactly where its owner left it, and the decision log renders the
    proposal beside it so a human can adjudicate where the decision lives.
    That is the whole difference from `build_decision_superseded_event`, and
    it is the difference between "a meeting mentioned this" and "a meeting
    reversed this" — a distinction the matcher cannot currently draw and
    therefore must not act on alone.

    Carries the SCORE and the matched TITLE deliberately. Whoever adjudicates
    this is being asked to overrule a machine, and they can only do that if
    they can see what the machine matched and how confidently.
    """
    data = {
        "decision_id": decision_id,
        "proposed_action": RECOMMEND_SUPERSEDED,
        "evidence": clip(evidence) if evidence else "",
        "status": "proposed",
    }
    if score is not None:
        data["score"] = round(float(score), 4)
    if title:
        data["title"] = title[:200]
    return {
        "seq": next_seq,
        "ts": _now_iso(),
        "type": RECOMMEND_SUPERSEDE_PROPOSED,
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": data,
    }


__all__ = [
    "DECISION_HIGH_CONFIDENCE_THRESHOLD",
    "DECISION_COMPLETION_PHRASES",
    "DECISION_REVERSAL_PHRASES",
    "RECOMMEND_ONLY_SUPERSEDES",
    "RECOMMEND_SUPERSEDED",
    "RECOMMEND_SUPERSEDE_PROPOSED",
    "detect_completion_signal",
    "detect_reversal_signal",
    "load_open_decisions",
    "match_transcript_to_decisions",
    "build_decision_resolved_event",
    "build_decision_superseded_event",
    "build_decision_supersede_proposal_event",
    "_decision_field",
    "_decision_id",
]
