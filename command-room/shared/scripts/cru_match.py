#!/usr/bin/env python3
"""
CRU (Cross-Reference and Update) layer (v2.14.6+).

Auto-resolve open commitments when downstream signal proves the commitment was
fulfilled. Five paths:

- **Path 1 (apply-choices send)** — after `send` fires for an item, check whether
  the recipient is the counter-party on any open commitment owned by the user
  AND the subject/body matches the commitment title. HIGH match → write
  `commitment_resolved`. MEDIUM match → write `pending_review` event for next
  Pulse fire to surface for one-click confirm.

- **Path 2 (bulk Gmail/Outlook scan)** — same logic as Path 1 but run over the
  user's outbound mail since the last fire (default last 7 days). Catches sends
  made directly from native mail clients OUTSIDE Cowork (Gmail on phone, Outlook
  desktop, etc.). Implementation lives in `orchestrator-commitments.md` Phase
  2.5 — the daily Commitments scheduled task runs this pre-render scan via the
  same `match_send_to_commitments` helper as Path 1.

- **Path 3 (past-meetings transcript)** — for each newly-processed transcript,
  score against open commitments where any meeting attendee is the owner.
  HIGH match + completion language → auto-resolve. Schedule-shift language →
  `commitment_updated`. New-ask language → spawn new commitment with
  `supersedes` link. **Circularity-fenced (AUTOAPPLY §6):** callers pass
  `transcript_source_ref` + `exclude_captured_since` so a transcript can
  never score against the commitments it (or its same-fire siblings) just
  created — see `match_transcript_to_commitments`.

- **Path 4 (inbound email)** — for each inbound message, score against open
  commitments where the SENDER is the owner (i.e. the counter-party owes the
  user, and their email proves they delivered). HIGH match + completion
  language ("here's the", "attached the", "as promised") → auto-resolve.
  Schedule-shift language ("I'll get it to you next week") → `commitment_updated`.
  Real-time leg runs in `orchestrator-inbox.md` per inbox fire; daily backstop
  runs in `orchestrator-commitments.md` Phase 2.6 over inbound mail since the
  last fire (catches counter-party deliveries that arrived between inbox fires).
  Both legs call the same `match_inbound_to_commitments` helper.

- **Path 5 (calendar event)** — the scheduling-close path (v3.14.7+). A whole
  class of commitments is fulfilled NOT by sending a message but by an event
  appearing on the calendar: "set up the build call with Bo", "lock
  Monday with Rio", "find time with the integrator". Paths 1/2/4 are all
  message-direction scans — they never look at the calendar, so these closed
  the moment the user created the invite but stayed surfaced as "reply to X to
  lock time" for days. Path 5 scores open commitments OWED BY THE USER that carry
  scheduling intent in their title against calendar events whose attendees
  include the commitment's counter-party. An event existing with that person
  (created at/after the commitment) is the fulfillment — analogous to Path 1
  where the act of sending IS the fulfillment. Counter-party acceptance
  (`responseStatus: accepted`) is bonus confidence. This ALSO subsumes the
  mirror "counter-party accepted my invite" signal that the inbox classifier
  was discarding as calendar-noise. Real-time leg runs in `calendar-writer`
  when CR creates the event; daily backstop runs in `orchestrator-commitments.md`
  Phase 2.7 over calendar events created/updated since the last fire (catches
  invites M made directly in Google/Outlook calendar, outside Cowork). Both
  legs call the same `match_calendar_to_commitments` helper.

All paths conservative: HIGH-confidence auto-resolve only. Borderline goes to
pending_review queue surfaced in next Pulse for one-click confirm. Path 5's
high-precision gate is structural: owed-by-user + scheduling-intent title +
a real calendar event with the named counter-party. A non-scheduling
commitment ("send the deck") never auto-resolves just because a meeting got
booked — the title-intent gate blocks it.

THE MATCHING MODEL
==================

Two scores computed and the higher of the two becomes the match score:

1. Unigram overlap coefficient: |a ∩ b| / min(|a|, |b|). Picked over Jaccard
   because commitment titles (≤ 120 chars) are usually shorter than the
   queries we score them against (email body, transcript chunk). Jaccard
   over-penalizes the long side; overlap rewards "this short title's content
   words all appear in the longer query."

2. Bigram Jaccard. Catches phrasings that share an ordered word pair (e.g.,
   "send the deck" / "send pricing deck"). Stricter than unigrams; rare to
   exceed unigram overlap unless the phrasing is near-identical.

Tokens are stop-word + punctuation + casing filtered. No stemming (kept
intentionally simple — `sending` won't match `send` exactly, but the rest of
the title usually carries the signal). No external deps.

Thresholds (tunable):
  HIGH_CONFIDENCE_THRESHOLD = 0.55  → auto-resolve
  PENDING_REVIEW_THRESHOLD  = 0.30  → flag for one-click confirm in next
                                       Pulse fire
  below 0.30                        → no action

Recipient match (Path 1) and attendee match (Path 3) PRE-FILTER the candidate
commitments before title scoring. So 0.30 over an already-narrowed candidate
set has high signal — it's not "match against any commitment in the workspace,"
it's "match against the commitments where this person is involved."

PER CONTRACT.md RULE 9
======================

Resolution is silent. The chat ack does NOT narrate "auto-resolved 2
commitments." Events are written to events.jsonl; the user sees the result on
the next Commitments fire (the resolved item simply doesn't appear).
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional


# v3.5.0+: canonical source in shared/scripts/confidence.py. Aliased here
# for back-compat — existing callers can keep importing the old names.
from confidence import MATCH_SCORE_AUTO_RESOLVE, MATCH_SCORE_PENDING_REVIEW
import confidence as _confidence

# Read-side timestamp normalization (Phase 1 Foundation) — ts → timestamp →
# date. History is never rewritten; readers normalize.
from event_time import event_time

# Bilingual overlay (Spanish beta). Inert for English installs: when no non-en
# language is active in the workspace, every accessor returns the English
# default unchanged and accent-folding is a no-op. See shared/scripts/lexicon.py
# + references/SPANISH_BUILD_PLAN.md. Guarded so a missing module can never
# change English-native behavior.
try:
    import lexicon as _lex
except Exception:  # pragma: no cover
    _lex = None

HIGH_CONFIDENCE_THRESHOLD = MATCH_SCORE_AUTO_RESOLVE   # 0.55
PENDING_REVIEW_THRESHOLD = MATCH_SCORE_PENDING_REVIEW  # 0.30


def _match_thresholds(workspace_root=None):
    """(auto_resolve, pending_review) match thresholds, honoring a Loop-4
    `_hq/data/confidence-overrides.json` when `workspace_root` is given, else the
    shipped constants. Passing None reproduces pre-Phase-6 behavior exactly, so
    every existing caller (and test) is unaffected."""
    if workspace_root is None:
        return HIGH_CONFIDENCE_THRESHOLD, PENDING_REVIEW_THRESHOLD
    return (_confidence.match_score_auto_resolve(workspace_root),
            _confidence.match_score_pending_review(workspace_root))


# Common English stop-words. Keep this list tight — a commitment title like
# "Send updated pricing deck to Mira" is mostly content words; over-filtering
# kills signal.
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "he", "her", "him", "his", "i", "in", "is", "it", "its", "me",
    "my", "of", "on", "or", "our", "she", "the", "their", "them", "they",
    "this", "to", "was", "we", "were", "will", "with", "you", "your", "yours",
    "but", "if", "so", "do", "did", "done", "than", "then", "that", "these",
    "those", "what", "when", "where", "which", "who", "whom", "why", "how",
    "would", "should", "could", "into", "out", "up", "down", "about", "over",
    "under", "again", "any", "no", "not", "only", "own", "same", "all", "more",
    "most", "such", "very", "just", "now",
})

# Completion-indicator phrases (Path 3). Anchored on common English fulfillment
# language. Case-insensitive substring match — keep tight.
COMPLETION_PHRASES = (
    "sent the", "sent over", "sent it", "sent that", "sent you",
    "delivered the", "delivered it",
    "shared the", "shared it", "shared that",
    "got the", "got it", "received the", "received it",
    "as promised", "as discussed",
    "here's the", "here is the", "here you go",
    "attaching the", "attached the",
    "wrapped up", "knocked out", "finished the",
    "done with", "all set on",
)

# Schedule-shift phrases (Path 3). Indicates the deadline moved, not that the
# work is done.
SCHEDULE_SHIFT_PHRASES = (
    "pushed to", "pushing to", "moving to", "moved to",
    "delayed to", "delayed until", "deferring to", "deferred to",
    "rescheduled to", "rescheduling to",
    "now targeting", "new target",
    "slipping to", "slipped to",
    "won't make", "wont make", "miss the",
)

# New-ask phrases (Path 3). Indicates a new commitment was layered on the
# existing one, NOT a resolution.
NEW_ASK_PHRASES = (
    "can you also", "could you also",
    "additionally please", "additionally can",
    "one more thing", "while you're at it", "while you are at it",
    "and also send", "and also share",
    "actually can you", "actually could you",
)


# Scheduling-intent phrases (Path 5). Detected in a COMMITMENT TITLE to decide
# whether a calendar event with the counter-party should auto-resolve it. The
# bar is "this commitment is about putting time on a calendar with someone",
# NOT "this commitment mentions a meeting in passing". Kept meeting-flavored so
# the structural pre-filter (owed-by-user + attendee-on-a-real-event) does the
# heavy lifting and this just blocks deliverable-style commitments ("send the
# deck") from resolving when an unrelated meeting gets booked.
SCHEDULING_PHRASES = (
    "schedule", "reschedule",
    "set up a call", "set up a meeting", "set up the call", "set up the meeting",
    "set up a time", "set up time", "set a time", "set up a sync", "set up a",
    "book a call", "book a meeting", "book time", "book a time",
    "find time", "find a time", "grab time", "grab 15", "grab 30",
    "lock in", "lock the", "lock monday", "lock a time", "lock time",
    "propose times", "propose a time", "propose some times", "send times",
    "pick a time", "confirm the time", "confirm a time", "nail down a time",
    "put on the calendar", "put time on", "get on a call", "get on the calendar",
    "call with", "meeting with", "sync with", "get time with", "time with",
    "send an invite", "send the invite", "send a calendar invite", "calendar invite",
)

_PUNCT_RE = re.compile(r"[^\w\s]+")
_WS_RE = re.compile(r"\s+")


def _phrases(key: str, default: tuple) -> tuple:
    """Merged phrase tuple for a ``cru_match`` phrase-list, or the English
    default when the bilingual overlay is inactive or absent (production path)."""
    if _lex is None:
        return default
    return _lex.load_lexicon_terms("cru_match", key, default)


def _tokenize(text: Optional[str]) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace, drop stop-words and
    single-char tokens. Returns a LIST (preserves order for bigrams) — caller
    converts to set for unigram match.

    Two-char tokens kept (e.g., `ap`, `q2`, `id` carry signal in commitment
    titles like "Q2 deck" or "AP demo"); only single-char garbage is dropped.
    """
    if not text:
        return []
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if _lex is None:
        return [w for w in text.split() if len(w) >= 2 and w not in STOPWORDS]
    # Bilingual overlay: merged stop-words + accent-folding when a non-English
    # language is active. English-only workspaces get STOPWORDS back verbatim
    # and fold=False, so the comprehension below is identical to production.
    stop = _lex.stopwords(STOPWORDS)
    fold = _lex.accent_fold_enabled()
    if not fold:
        return [w for w in text.split() if len(w) >= 2 and w not in stop]
    out = []
    for w in text.split():
        w = _lex.fold_accents(w)
        if len(w) >= 2 and w not in stop:
            out.append(w)
    return out


def _bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap_coefficient(a: set, b: set) -> float:
    """|a ∩ b| / min(|a|, |b|). Better than Jaccard for matching short titles
    against longer queries — rewards "this title's content words all appear in
    the longer text" instead of penalizing the longer side.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def extract_snippet(query: Optional[str], text: Optional[str],
                    *, length: int = 200) -> str:
    """Extract a topic-aware snippet from a long body of text (e.g. a meeting
    transcript). Used by `transcript-search` (v2.14.8+) so snippets are
    deterministic — the LLM doesn't fabricate quotes that aren't actually in
    the transcript.

    Algorithm:
      1. Tokenize the query into content words (stopword-filtered).
      2. Walk through text looking for windows where the most query words
         appear in proximity.
      3. Extract a ~length-char window centered on that match. Trim at
         sentence boundaries (`.`, `?`, `!`, `\n`) when possible.
      4. Return with leading/trailing ellipses to signal it's a fragment.

    Returns empty string if no query token appears in text.
    """
    if not query or not text:
        return ""
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return ""

    text_lower = text.lower()
    # Find positions of any query token (any non-overlapping occurrence). Pick
    # the position with the densest match in a 200-char window around it.
    positions: list[int] = []
    for tok in q_tokens:
        start = 0
        while True:
            idx = text_lower.find(tok, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + len(tok)

    if not positions:
        return ""

    # Score each position by counting how many distinct query tokens appear
    # within the window centered there.
    half = length // 2
    best_pos = positions[0]
    best_density = 0
    for p in positions:
        win_start = max(0, p - half)
        win_end = min(len(text), p + half)
        window = text_lower[win_start:win_end]
        density = sum(1 for tok in q_tokens if tok in window)
        if density > best_density:
            best_density = density
            best_pos = p

    win_start = max(0, best_pos - half)
    win_end = min(len(text), best_pos + half)

    # Trim to sentence boundaries when possible
    snippet = text[win_start:win_end]
    # Trim leading partial sentence
    for boundary in [". ", "? ", "! ", "\n"]:
        i = snippet.find(boundary)
        if 0 < i < length // 3:
            snippet = snippet[i + len(boundary):]
            break
    # Trim trailing partial sentence
    for boundary in [". ", "? ", "! ", "\n"]:
        i = snippet.rfind(boundary)
        if i > length * 2 // 3:
            snippet = snippet[:i + 1]
            break

    snippet = snippet.strip()
    leading = "..." if win_start > 0 else ""
    trailing = "..." if win_end < len(text) else ""
    return f"{leading}{snippet}{trailing}"


def score_match(query_text: Optional[str], commitment_title: Optional[str]) -> float:
    """Compute match score between a query (subject+body OR transcript chunk)
    and a commitment title. Returns float 0.0 - 1.0. Higher = better match.

    Method: max of (unigram overlap coefficient, bigram Jaccard). Overlap is
    the dominant signal because titles are usually shorter than queries;
    bigrams catch ordered-phrasing matches that unigram-only might miss.
    """
    q_tokens = _tokenize(query_text)
    c_tokens = _tokenize(commitment_title)
    if not q_tokens or not c_tokens:
        return 0.0
    unigram = _overlap_coefficient(set(q_tokens), set(c_tokens))
    bigram = _jaccard(_bigrams(q_tokens), _bigrams(c_tokens))
    return max(unigram, bigram)


def detect_completion_signal(text: Optional[str]) -> bool:
    """True if `text` contains a phrase suggesting the commitment was
    fulfilled (`sent the`, `delivered`, `as promised`, etc.).

    The baked `COMPLETION_PHRASES` are the deterministic floor. Phase 6 Loop 5
    complements them: `_hq/data/extraction-hints.md` accumulates learned
    resolution-language exemplars from documented `resolution_miss` cases, which
    the LLM-driven CRU legs (meeting-notes Step 5e-bis, the inbound CRU pass)
    consult alongside this scorer — a workspace teaches the matcher its own
    "already done" phrasings without a code change here.
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in _phrases("completion_phrases", COMPLETION_PHRASES))


def detect_schedule_shift_signal(text: Optional[str]) -> bool:
    """True if `text` contains a phrase suggesting the deadline moved
    (`pushed to`, `delayed`, etc.) — used to write `commitment_updated`
    instead of `commitment_resolved`.
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in _phrases("schedule_shift_phrases", SCHEDULE_SHIFT_PHRASES))


def detect_new_ask_signal(text: Optional[str]) -> bool:
    """True if `text` contains a phrase suggesting a NEW request layered on
    top (`can you also`, `additionally please`) — used to spawn a new
    commitment with `supersedes` link rather than resolve the existing one.
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in _phrases("new_ask_phrases", NEW_ASK_PHRASES))


def detect_scheduling_intent(text: Optional[str]) -> bool:
    """True if `text` (a commitment TITLE) is about putting time on a calendar
    with someone — `set up a call`, `lock Monday`, `propose times`, `call with`,
    etc. Used by Path 5 (`match_calendar_to_commitments`) as the precision gate:
    only scheduling-flavored commitments auto-resolve when a calendar event with
    the counter-party appears. A deliverable commitment ("send the deck") returns
    False and is never closed by a booked meeting.
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in _phrases("scheduling_phrases", SCHEDULING_PHRASES))


# -----------------------------------------------------------------------------
# Open-commitment loader (Cross-references the canonical aggregator pattern in
# build_workspace_map_input.py `_aggregate_commitments`)
# -----------------------------------------------------------------------------


def _commitment_id(ev: dict) -> str:
    d = ev.get("data") or {}
    return d.get("id") or ev.get("id") or f"commitment_seq_{ev.get('seq', '?')}"


def _is_pending_review(ev: dict) -> bool:
    """A commitment the extractor itself flagged as uncertain
    (COMMITMENT_SCHEMA `data.pending_review`) must never be silently
    auto-resolved — it may be surfaced for review, but never auto-closed
    (deep-audit 2026-05-29, finding #9)."""
    d = ev.get("data") or {}
    return bool(d.get("pending_review") or ev.get("pending_review"))


def _parse_ts(value):
    """Parse an ISO-8601 timestamp to a tz-AWARE (UTC) datetime for instant
    comparison, tolerating both `...Z` and `...+00:00` offsets (the auto-stamp
    emits +00:00 while the build_* helpers emit Z). Returns None when the value
    is missing or unparseable (deep-audit 2026-05-29, finding #17).

    A date-only value (`2026-05-22`) parses to a NAIVE datetime, which then
    raises "can't compare offset-naive and offset-aware" against the aware
    `now` (real commitment dues are often date-only). So we ALWAYS attach UTC
    when the parse came back naive — every result is aware-vs-aware safe."""
    if not value or not isinstance(value, str):
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# Field aliases per commitment shape variant observed in the wild (v3.4.3+).
# Each value is a tuple of names to try in priority order — the helper tries
# each at the canonical (data.<>) location first, then at the flat (top-level
# <>) location.
#
# Why each alias exists:
#   - owner_id:  canonical schema field
#   - owner_person_id:  emitted by cr-past-meetings in some fires (observed in
#     M's events.jsonl 2026-04-30 → 2026-05-09). Same semantic as owner_id.
#   - owner:  pre-v2.7.15 legacy field name (no _id suffix). The schema doc
#     explicitly documents this as the legacy flat shape.
#   - requester_person_id: parallel to owner_person_id, same writer source.
#   - due_date:  observed in cr-past-meetings owner_person_id-variant events.
#   - state:  observed in cr-past-meetings owner_person_id-variant events
#     (instead of `status`).
#   - classification_confidence:  top-level meeting confidence; some writers
#     populate this instead of data.confidence (e.g., cr-meetings-processed,
#     meeting-notes, scan-for-commitments per M's 2026-04 events).
_COMMITMENT_FIELD_ALIASES = {
    "owner_id": ("owner_id", "owner_person_id", "owner"),
    "requester_id": ("requester_id", "requester_person_id"),
    "due": ("due", "due_date"),
    "status": ("status", "state"),
    "title": ("title",),
    "confidence": ("confidence", "classification_confidence"),
}


def _commitment_field(ev: dict, field: str) -> Any:
    """Read a commitment field handling every shape variant observed per
    shared/COMMITMENT_SCHEMA.md.

    Searches `data.<alias>` first, then top-level `<alias>`, for each alias
    in `_COMMITMENT_FIELD_ALIASES[field]` (defaults to `(field,)` if the
    field has no registered aliases). First non-empty value wins. Returns
    None if no value found at any location.

    The schema explicitly permits multiple shapes — canonical for v2.7.15+
    writers, flat for legacy events that pre-date the canonical contract or
    that some writer paths still emit at top level. Consumers MUST handle all
    shapes; reading only `data.<field>` silently drops flat-shape events from
    view (Sam bug report 2026-05-17: cr-commitments was dropping ~2/3 of
    his commitments because the filter only read `data.owner_id`). v3.4.3
    extends coverage from {canonical, flat-new} to also include the legacy
    `owner` field and the `owner_person_id` / `state` / `due_date` variant
    actively produced by cr-past-meetings.
    """
    d = ev.get("data") or {}
    aliases = _COMMITMENT_FIELD_ALIASES.get(field, (field,))
    for alias in aliases:
        v = d.get(alias)
        if v not in (None, ""):
            return v
    for alias in aliases:
        v = ev.get(alias)
        if v not in (None, ""):
            return v
    return None


# String-confidence levels that some writers (cr-past-meetings, legacy events)
# use instead of a 0-1 float. Maps each to a canonical float so consumers can
# apply a single threshold (>= 0.7) across the whole corpus.
_CONFIDENCE_LEVEL_MAP = {
    "high": 0.85,
    "medium": 0.50,
    "med": 0.50,
    "low": 0.30,
}


# Fields where a person_id may appear, by event type / shape variant.
# Source: orchestrator-dont-forget.md Phase 3 (pre-v3.5.0 inline list) +
# the v3.4.4 shape audit + the 2026-05-17 audit that found this list was
# missing shapes 2/3/4. v3.5.0+ consolidates the full list here.
#
# Each entry is a dotted path. Empty path component "" means top-level.
# A leaf path may point at a scalar (e.g., "data.owner_id") or a list
# (e.g., "person_ids" or "data.attendees"); the helper handles both.
_PERSON_ID_FIELDS = (
    # Root-level person references
    "person_ids",                  # newer events, v2.7.15+
    "actor",                       # when actor is a person, not a skill
    # Nested under data
    "data.person_ids",             # older interaction/meeting events, pre-v2.7.15
    "data.attendees",              # meeting events
    "data.owner_id",               # commitment events — canonical shape
    "data.owner_person_id",        # commitment events — owner_person_id-variant shape (cr-past-meetings)
    "data.requester_id",           # commitment events — canonical shape
    "data.requester_person_id",    # commitment events — owner_person_id-variant shape
    # Flat-new shape (top-level owner fields without nesting under data)
    "owner_id",
    "owner_person_id",
    "requester_id",
    "requester_person_id",
    # Legacy pre-v2.7.15 (top-level `owner` without _id suffix)
    "owner",
)


def event_references_person(ev: dict, person_id: str) -> bool:
    """Return True iff this event references `person_id` in any known field
    location, across all shape variants per shared/COMMITMENT_SCHEMA.md and
    the audit-derived `_PERSON_ID_FIELDS` table above.

    Used by Pulse Phase 3 cadence detection (v3.5.0+) so dormancy scoring
    counts interactions across every shape — not just the 6-field inline list
    Pulse used pre-v3.5.0, which silently missed shape-4 events from
    cr-past-meetings and any flat-new / legacy commitment shapes.

    The function is shape-agnostic — pass any event dict; it inspects every
    known field and returns True at the first match. False positives are
    essentially impossible (person_id strings are namespaced like `person_NNN`
    and don't collide with other identifiers).
    """
    if not person_id or not isinstance(ev, dict):
        return False
    for path in _PERSON_ID_FIELDS:
        parts = path.split(".")
        v = ev
        for p in parts:
            if not isinstance(v, dict):
                v = None
                break
            v = v.get(p)
            if v is None:
                break
        if v is None:
            continue
        if isinstance(v, list):
            if person_id in v:
                return True
        elif isinstance(v, str):
            if v == person_id:
                return True
    return False


def _commitment_confidence(ev: dict) -> float:
    """Read a commitment's confidence as a normalized float in [0.0, 1.0].

    Some writers store confidence as a string label (`"HIGH"`, `"medium"`)
    instead of a 0-1 float; the comparison `data.confidence >= 0.7` crashes on
    string values and silently drops the event. This helper coerces both shapes
    via `_CONFIDENCE_LEVEL_MAP`. Missing confidence defaults to 0.0 (filtered
    out by any non-trivial threshold).
    """
    v = _commitment_field(ev, "confidence")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return _CONFIDENCE_LEVEL_MAP.get(v.strip().lower(), 0.0)
    return 0.0


def load_events_defensively(
    events_jsonl_path: str | Path,
    since_ts=None,
) -> tuple[list[dict], list[dict]]:
    """Canonical events.jsonl reader (v3.13.8+ — Sub-bug #14b).

    Reads events.jsonl line-by-line and returns (events, skipped). Handles
    BOTH JSONDecodeError AND non-dict-shaped lines (the latter was the
    actual crash class behind Sub-bug #14b — pre-v3.13.8 readers raised
    `AttributeError: 'str' object has no attribute 'get'` when a line
    parsed as a top-level string).

    Returns:
      events: list of dict rows. Each row is a parsed events.jsonl entry.
      skipped: list of dicts shaped {"line": int, "reason": str, "value": str}
        describing each line that could not be loaded. The CALLER is
        expected to surface this to the user (warning banner, recovery
        prompt, audit log) — DO NOT silently swallow.

    This replaces the pre-v3.13.8 in-line silent filter pattern
    (`try: ev = json.loads(line); except: continue`) which both lost the
    skipped-count AND failed on non-dict cases.

    Use this from every consumer of events.jsonl in shared/scripts/ and
    skills/. The cru_match.load_open_commitments path (below) is migrated
    inline.
    """
    path = Path(events_jsonl_path)
    # SPEC A5 — shard-transparent: when handed an `events.jsonl` path, read its sibling
    # `events-<year>.jsonl` shards too, so full-history consumers see the whole timeline
    # after a rotation. Unsharded workspace -> identical output to the pre-A5 single read.
    # A non-events.jsonl path is read as-is (back-compat).
    #
    # `since_ts` (v4.6.0 MC2 — the cheap perf half): whole year-shards below
    # the floor year are pruned by FILENAME, never opened (events_io.
    # shard_paths' rule). Default None = full history, byte-identical to
    # pre-MC2 behavior. THE SAFETY CONTRACT lives with the caller: a pruned
    # shard's events are simply invisible, so pass since_ts ONLY when
    # everything that matters to the caller provably postdates the floor.
    # The active events.jsonl is never pruned; a non-events.jsonl path
    # ignores since_ts (no shards to prune).
    if path.name == "events.jsonl":
        try:
            from events_io import shard_paths
            files = shard_paths(path, since_ts=since_ts)
        except Exception:
            files = [path] if path.exists() else []
    else:
        files = [path] if path.exists() else []
    if not files:
        return [], []

    events: list[dict] = []
    skipped: list[dict] = []

    for fp in files:
        if not fp.exists():
            continue
        # FS-15 — when the FINAL non-blank line of a file won't parse, that's
        # the partial-write / truncated-sync-cache signature (not historical
        # junk mid-file): record it in the `.readalarm.json` sidecar the
        # brief / system-health surface loudly. The skipped[] contract is
        # unchanged — callers should still surface it — but the sidecar makes
        # the degradation survive callers that don't.
        last_decode_error = None
        with open(fp, "r", encoding="utf-8") as f:
            for i, raw in enumerate(f, 1):
                line = raw.strip()
                if not line:
                    continue
                last_decode_error = None
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError as e:
                    last_decode_error = f"JSONDecodeError: {e.msg}"
                    skipped.append(
                        {
                            "line": i,
                            "shard": fp.name,
                            "reason": f"JSONDecodeError: {e.msg}",
                            "value": line[:80],
                        }
                    )
                    continue
                if not isinstance(ev, dict):
                    skipped.append(
                        {
                            "line": i,
                            "shard": fp.name,
                            "reason": "non-dict",
                            "value": repr(ev)[:80],
                        }
                    )
                    continue
                events.append(ev)
        if last_decode_error:
            try:
                from read_alarm import record_read_alarm
                record_read_alarm(
                    fp,
                    f"final line unparseable (truncation signature): "
                    f"{last_decode_error}",
                    reader="cru_match",
                )
            except Exception:  # pragma: no cover
                # Best-effort: a broken read_alarm module (ImportError, or the
                # SyntaxError a mid-update truncation leaves) must not turn a
                # degraded read into a hard failure.
                pass
    return events, skipped


# v4.5.2 R1 perf — per-fire memoization of the open-set projection.
# The daily chase (orchestrator-commitments) calls load_open_commitments 4+
# times per fire and once more per closure, and every call was a full-history
# scan across all shards. Cache keyed on the stat signature (path, mtime_ns,
# size) of the events file + every shard: any append changes size → miss →
# fresh scan, so a close_commitment write invalidates automatically. History
# is append-only, so an unchanged signature means unchanged content.
# Keyed on (resolved path, since_ts) — MC2's windowed projections cache
# separately from the full one.
_OPEN_COMMITMENTS_CACHE: dict[tuple, tuple] = {}


def _events_files_sig(path: Path) -> tuple:
    """Stat signature over the events file + its sibling shards (the same file
    set load_events_defensively reads). Any append changes st_size."""
    if path.name == "events.jsonl":
        try:
            from events_io import shard_paths

            files = shard_paths(path)
        except Exception:
            files = [path] if path.exists() else []
    else:
        files = [path] if path.exists() else []
    sig = []
    for fp in files:
        try:
            st = fp.stat()
            sig.append((str(fp), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(fp), None, None))
    return tuple(sig)


def load_open_commitments(
    events_jsonl_path: str | Path,
    since_ts=None,
    *,
    events: Optional[list] = None,
    workspace_root=None,
) -> list[dict]:
    """Read events.jsonl and return all open commitments (status open or
    overdue) that have NOT been closed by a subsequent `commitment_resolved`
    or `thread_resolved` event.

    PGUARD2 D2 — `events` injection: when the caller supplies `events`
    (a list of event dicts), the projection is built from THOSE rows and no
    file load happens (`events_jsonl_path` / `since_ts` are ignored for
    loading; the path is still used only as documentation of intent). This is
    the seam the EXTERNAL COMPOSERS use: they pass
    `events_io.load_events_org_scoped(workspace_root)[0]` so personal-lane
    rows (a `tie: "personal"` commitment, reminder-lane rows) and
    masked-account rows never enter a draft, agenda, memo, or follow-up pack.
    OWNER surfaces (show-my-list, the brief, close/chase flows) keep the
    no-arg form — they legitimately project everything. The default path is
    byte-identical to pre-PGUARD2 behavior. Supplied-events calls are NOT
    memoized (no file signature to key on) and apply the same R5 mask pass
    as the loaded path (idempotent on already-org-scoped rows — mask events
    survive org scoping, so the mask set is still computable).

    F-28 post-review (F-2) — `workspace_root`: with it, the MC1
    `all_counterparties_received` stamp below is computed against the ENTITY
    GRAPH, so a commitment whose only outstanding leg is a phantom (the same
    person written once as a resolved id and once as that person's free-text
    name) is stamped complete rather than left un-stamped forever. Without it
    the stamp stays raw-union, byte-identically pre-F-28.

    Why this argument rather than deriving the root from `events_jsonl_path`:
    walking three parents up would fight `data_root.py`'s `CR_DATA_ROOT`
    override, which exists precisely so `_hq/data` can live elsewhere. The
    caller knows its workspace; this function must not guess one.

    Why the chase surface needs it: `orchestrator-commitments.md` Phase 4.5
    renders in two MUTUALLY EXCLUSIVE modes — step 1 fans out one row per
    `outstanding_counterparties(...)`, step 3 renders the single
    "everyone's received — close it?" row when the projection carries
    `all_counterparties_received`. Fixing the fan-out (step 1) without the
    stamp (step 3) empties both: no nudge row AND no close-proposal row, on an
    item still sitting open. Noise became SILENCE, which is worse. Both halves
    read the same workspace now, so the two modes stay complementary.

    v4.5.2 R1 — memoized per file state: repeated calls within one fire reuse
    the projection (the returned LIST is a fresh copy each call; the event
    dicts inside are shared — treat them as read-only, which every consumer
    already does). Any append to events.jsonl or a shard invalidates the
    cache via the stat signature. The full incremental index is 4.7 scope
    (client-scale trigger).

    v4.6.0 MC2 — `since_ts` shard pruning: passed through to
    load_events_defensively, dropping whole year-shards below the floor year
    by FILENAME (never opened). Default None = full history, byte-identical
    to pre-MC2 behavior — and the correct default for this function: the
    open-set projection is inherently full-history (a 2024 capture can still
    be open, and its closer can live in any later shard), so a pruned shard's
    commitments and closers silently vanish from the result. PASS since_ts
    ONLY when the window provably covers the entire commitment history —
    e.g. the caller has checked the workspace's first commitment-family
    event postdates the floor (fresh workspaces), or a test controls the
    fixture. When in doubt, don't: correctness beats the shard skip.

    Mirror of build_workspace_map_input.py `_aggregate_commitments`. Returns
    full event dicts (caller pulls what it needs from `data`).

    v3.13.8+ — delegates to `load_events_defensively()` which surfaces
    skipped-line counts to callers via the helper return rather than
    silently swallowing malformed lines (Sub-bug #14b real-world fix).
    The skipped list is currently consumed via logging only here; new
    callers should call `load_events_defensively()` directly when they
    need to render a recovery prompt to the user.

    Phase 2 Stage C (2026-07, F3 read-side amnesty) — the closure-id chain is
    extended with the seq aliases `data.commitment_seq` and
    `data.source_event_seq` (both map seq → the commitment event at that
    seq), keeping all existing aliases. ~252 of the 289 historic dead-letter
    closures become readable through this chain with no history rewrite.
    `commitment_state.close_commitment`'s idempotency chain mirrors this
    extension exactly (the two always move together).

    Phase 2 Stage A (2026-07) — `commitment_updated` deferrals are folded
    into the returned events: when a later `commitment_updated` event carries
    a new due date (`data.new_due`, the orchestrator `push to [date]` verb's
    field; `data.due` / `data.due_date` accepted as variants), the returned
    commitment's `data.due` is the EFFECTIVE due (latest update wins) and
    `data.due_updated_by_seq` records the update event's seq. Before this
    fold those events were write-only — nothing read them — so a deferred
    commitment rendered overdue forever off its immutable original due. The
    fold is read-side only (in-memory copies); events.jsonl history is never
    rewritten. Consumers keep reading due via `_commitment_field(ev, "due")`
    and are agnostic to whether it was deferred.

    v4.6.0 S4 — two more read-side folds, same append-only doctrine:
      * WORDING: `commitment_updated` events carrying `data.new_title` /
        `data.new_summary` (the `fix wording` verb via
        commitment_state.edit_commitment_wording) fold into the projected
        item's title/summary — each field independently, newest wins; the
        mis-extracted original stays in history. `change_summary` (the CRU
        schedule-shift prose) deliberately never folds into wording.
      * REASSIGNMENT: the latest `commitment_reassigned` folds
        new_owner_id / new_counterparty_id (+ display names when carried)
        into the projected item. Unconfirmed reassignments stamp
        `pending_review` (+ review_reason) so the item counts as unconfirmed
        and never enters chase; a confirmed one (W4b Theirs→[name], or the
        user naming the person) clears the flag.

    v4.6.1 W4b — the confirm-flow adjudication fold, same doctrine:
      * OWNER CONFIRM ("Mine"): a `commitment_updated` carrying
        `data.owner_confirmed: true` (commitment_state.confirm_commitment_owner)
        folds `new_owner_id` (+ name) into the projection and clears
        `pending_review` / `review_reason` — the item leaves the unconfirmed
        bucket and joins its confirmed direction.
      * REVIEW CLEAR ("Keep both"): a `commitment_updated` carrying
        `data.review_flags_cleared: true` (commitment_state.clear_review_flags)
        clears `pending_review`, `review_reason`, AND the C4
        `suspected_duplicate_of` / `suspected_duplicate_score` flags —
        confirmed distinct, both items stay open.
      * REVIEW SET (AUTOAPPLY §4c review fix — the additive mirror of the
        clear above): a `commitment_updated` carrying
        `data.review_flags_set: true` (commitment_state.
        flag_duplicate_for_review) STAMPS `pending_review`, `review_reason`
        and the C4 `suspected_duplicate_of` / `suspected_duplicate_score`.
        An auto-merge stamp the apply half could not honor (survivor closed,
        stamp stale, merge reversed) lands the pair back on the flag tier as
        a visible question instead of vanishing.
      * WATCH (WATCHGATE §2.3 — the parked-and-still-open fold): a
        `commitment_updated` carrying `data.watch_set: true` plus a
        `data.watch` object (watch_gate.park_in_watch) stamps that object onto
        the projection; one carrying `data.watch_cleared: true`
        (watch_gate.clear_watch) removes it. Latest wins. WATCHING is
        deliberately NOT a status value and NOT a filter: a watched item stays
        `status: "open"`, stays in this projection, and stays in every count —
        surfaces may badge it, but nothing may lose it. A reader written
        before this fold existed sees exactly the open commitment it always
        saw, which is the entire back-compat contract.
      Ordering is append-order-aware ACROSS the reassignment fold: a Mine
      confirm followed by a later unconfirmed reassignment re-stamps
      pending_review (latest adjudication wins), and vice versa.
    Split closers (`commitment_superseded` with `data.split_into`) close the
    original through the standard closer chain but are NOT merges — the C4
    survivor-provenance fold is skipped for them; split children carry their
    own `data.source_event_seq` / `data.split_from` provenance at write time.

    SUB1 (2026-07) — the sub-item fold, same append-only doctrine. A child
    commitment (data.parent_id, written only by commitment_state.add_subitems)
    is an ordinary open commitment in this projection; the fold adds stamps
    on the in-memory copies only:
      * CHILD stamps: `parent_id` re-pointed through the C4 merge chain (a
        superseded parent's children belong to the SURVIVOR read-side — no
        history rewrite), `parent_title` (the parent's EFFECTIVE title, for
        "part of: […]" rendering), and `parent_closed: True` for orphans
        (live child of a closed parent — the cascade crash window; they
        render top-level with a "was part of" note and count in the total).
      * PARENT stamps (only when it has children): `subitem_ids` (append
        order), `n_subitems_open`, `n_subitems_done`,
        `all_subitems_resolved: True` when the LAST open child closed (the
        PROPOSE-closure signal — mirror of MC1's
        all_counterparties_received; never a closer), and
        `next_subitem_due` (min open-child EFFECTIVE due, annotation +
        ranking signal ONLY — never folded into the parent's own data.due;
        a deferred parent stays deferred, D-7).
    """
    path = Path(events_jsonl_path)
    injected = events is not None
    if not injected and not path.exists():
        return []

    cache_key = None
    if not injected:
        # Cache key carries since_ts — a windowed projection and the full one
        # are different results and must never serve each other's cache
        # entries. Supplied-events calls (PGUARD2 D2) never touch the cache:
        # there is no file signature to validate a supplied list against.
        # `workspace_root` is IN the key: with a workspace the MC1 stamp is
        # computed against the entity graph, so the same file legitimately
        # yields two different projections and one must never serve the
        # other's cache entry (the same reasoning `since_ts` is here for).
        cache_key = (str(path.resolve()), since_ts,
                     str(workspace_root) if workspace_root is not None else None)
        sig = _events_files_sig(path)
        cached = _OPEN_COMMITMENTS_CACHE.get(cache_key)
        if cached is not None and cached[0] == sig:
            return list(cached[1])

        events, skipped = load_events_defensively(path, since_ts=since_ts)
        if skipped:
            # Surface a warning so consumers see a non-silent skipped count
            # in stderr / log streams. Per the v3.13.8 contract, callers that
            # render to user MUST display skipped to the user explicitly;
            # this log line is the floor, not the ceiling.
            import sys as _sys
            _sys.stderr.write(
                f"[cru_match] load_open_commitments skipped {len(skipped)} "
                f"malformed events.jsonl lines in {path.name}: "
                f"{[s['line'] for s in skipped]}\n"
            )
    else:
        events = [ev for ev in events if isinstance(ev, dict)]
    # R5 reader-honor (connector-agnostic-v1): the commitment projector drops
    # rows whose account identity matches a LIVE account_scope_masked (un-
    # masked by account_scope_restored). Read-side only; rows never move.
    # Defensive: any failure leaves the events unfiltered.
    try:
        from account_scope_gate import filter_masked_events
        events = filter_masked_events(events)
    except Exception:
        pass

    open_evs: list[dict] = []
    # Closure state is ORDER-AWARE since Stage D's `commitment_reopened`
    # (S4 undo): a commitment is closed iff its latest closure comes AFTER its
    # latest reopen in append order. Each dict maps target → last file index.
    # F3 amnesty (Stage C): closure seqs referencing the commitment EVENT's
    # seq — `data.commitment_seq` and `data.source_event_seq` both map
    # seq → the commitment at that seq. ~252 of the 289 historic dead-letter
    # closures carry one of these (the 52 workspace-manager catch-all
    # closures wrote ONLY source_event_seq).
    closed_ids_at: dict[str, int] = {}
    closed_seqs_at: dict[int, int] = {}
    reopened_ids_at: dict[str, int] = {}
    reopened_seqs_at: dict[int, int] = {}
    # commitment id → latest due-shifting update (Stage A fold; see docstring).
    due_updates: dict[str, dict] = {}
    # commitment id → latest wording update per field (v4.6.0 S4 fold: the
    # `fix wording` verb writes commitment_updated with data.new_title /
    # data.new_summary; each field folds independently, newest wins; the
    # original text stays in history — append-only, never rewritten).
    wording_updates: dict[str, dict] = {}
    # commitment id → latest reassignment (v4.6.0 S4: commitment_reassigned
    # routes an item to a new owner/counterparty instead of discarding it;
    # an UNCONFIRMED reassignment stamps pending_review on the projection so
    # the item sits in the unconfirmed bucket and never enters chase — the
    # W4b no-auto-email-on-a-guessed-owner guardrail).
    reassignments: dict[str, dict] = {}
    # commitment id → latest confirm-flow adjudication (v4.6.1 W4b): a
    # commitment_updated carrying owner_confirmed ("Mine") or
    # review_flags_cleared ("Keep both"). Applied in append order against
    # the reassignment fold — the latest adjudication decides pending_review.
    confirmations: dict[str, dict] = {}
    # commitment id → latest flag-tier fallback (AUTOAPPLY §4c review fix): a
    # commitment_updated carrying review_flags_set (commitment_state.
    # flag_duplicate_for_review). The additive MIRROR of review_flags_cleared
    # — an auto-merge stamp that could not be honored at apply time (survivor
    # closed, stamp stale, merge reversed) puts the pair back on the flag tier
    # as a visible question. Ordered with the other adjudications: latest
    # wins, so a user's Keep-both after this clears it, and this after a
    # Keep-both re-asks.
    review_flag_sets: dict[str, dict] = {}
    # commitment id → latest WATCHING mark (WATCHGATE §2.3): a
    # commitment_updated carrying watch_set (watch_gate.park_in_watch) or
    # watch_cleared (watch_gate.clear_watch). ADDITIVE — the item stays
    # `status: "open"` and gains `data.watch`; WATCHING is deliberately NOT a
    # status value, so every reader written before this fold existed keeps
    # seeing the ordinary open commitment it always saw. Latest wins per
    # target, so a park after a clear re-parks and vice versa.
    watch_marks: dict[str, dict] = {}
    # commitment target → latest kind override (Stage D fold: the
    # `commitment_reclassified` marker is ADDITIVE — promote/migrate never
    # delete/recreate; the projector applies the label change read-side).
    kind_overrides_by_id: dict[str, dict] = {}
    kind_overrides_by_seq: dict[int, dict] = {}
    # survivor id → accumulated merge provenance (v4.6.0 C4 fold): each
    # `commitment_superseded` closes its target through the standard closer
    # chain above AND names a survivor (`data.superseded_by`) that absorbed
    # it — the survivor's in-memory copy carries the union
    # (data.merged_source_refs / data.merged_from). Read-side only; history
    # is never rewritten.
    merged_onto: dict[str, dict] = {}
    # commitment target → accumulated per-person receipts (v4.6.0 MC1): each
    # `commitment_partial_received` records ONE counterparty delivering on a
    # multi-counterparty commitment (id or free-text name). The loader unions
    # them onto the projection (`data.received_from` / `received_from_names`)
    # and stamps `data.all_counterparties_received` when the whole roster has
    # delivered — the PROPOSE-closure signal (never a closer; the item stays
    # open until the user closes it). Keyed by id AND seq alias (mirrors the
    # kind-override fold), so a receipt referencing either resolves.
    received_ids_by_id: dict[str, set] = {}
    received_names_by_id: dict[str, set] = {}
    received_ids_by_seq: dict[int, set] = {}
    received_names_by_seq: dict[int, set] = {}
    # SUB1 fold state: every commitment event by canonical id (parents may be
    # closed — orphan children still need the parent's title), the superseded
    # → survivor re-point map (C4 merges transfer children read-side), and
    # the raw child links (child cid, child seq, on-disk parent_id).
    commitment_by_id: dict[str, dict] = {}
    superseded_onto: dict[str, str] = {}
    child_records: list[tuple] = []

    def _as_seq(value):
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    for idx, ev in enumerate(events):
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et in ("commitment_resolved", "thread_resolved", "commitment_superseded"):
            # commitment_superseded (v3.14.5): people-crm/SKILL.md Gate 2 names
            # it as a valid closer ("commitments closed via commitment_resolved /
            # thread_resolved / commitment_superseded"). It was missing from this
            # filter, so a superseded commitment would stay surfaced as open. No
            # writer emits it yet, but honoring it here closes the contract drift
            # before any producer ships.
            # v3.11.4+: accept data.target_id as a defensive backwards-
            # compat closer-id field. Pre-v3.11.4 show-my-list's `resolved`
            # handler wrote thread_resolved with data.target_id (no other
            # writer used target_id), and consumers didn't recognize the
            # field as a closer — so those events silently failed to close
            # their referenced commitments. Per SOURCE_OF_TRUTH.md the
            # canonical id field going forward is data.commitment_id;
            # target_id stays in the accept list only for in-flight events.
            cid = (
                d.get("commitment_id")
                or d.get("thread_id")
                or d.get("id")
                or d.get("target_id")
                or ev.get("commitment_id")
                or ev.get("thread_id")
                or ev.get("id")
            )
            if cid:
                closed_ids_at[str(cid)] = idx
            # F3 amnesty (Stage C): the seq aliases close the commitment at
            # that seq REGARDLESS of whether an id field was also present. A
            # seq pointing at a non-commitment event simply matches nothing.
            for seq_field in ("commitment_seq", "source_event_seq"):
                sv = _as_seq(d.get(seq_field))
                if sv is not None:
                    closed_seqs_at[sv] = idx
            # C4 merge fold: a supersession names the survivor that absorbed
            # the closed item — accumulate its provenance for the patch below.
            # S4 split closers (data.split_into present) are NOT merges: the
            # children carry their own source_event_seq / split_from
            # provenance at write time, so no survivor fold applies.
            if et == "commitment_superseded" and not d.get("split_into"):
                survivor = d.get("superseded_by") or d.get("survivor_id")
                if survivor:
                    entry = merged_onto.setdefault(
                        str(survivor), {"refs": [], "from": []}
                    )
                    for r in d.get("merged_source_refs") or []:
                        if isinstance(r, str) and r and r not in entry["refs"]:
                            entry["refs"].append(r)
                    if cid and str(cid) not in entry["from"]:
                        entry["from"].append(str(cid))
                    # SUB1 D3b: the merged-away item's children re-point to
                    # the survivor read-side (never rewritten on disk).
                    if cid:
                        superseded_onto[str(cid)] = str(survivor)
        elif et == "commitment_reopened":
            # Stage D (S4 undo): reopen the referenced commitment. Same target
            # chain shape as closures; append order decides the final state.
            target = (
                d.get("commitment_id") or d.get("target_id")
                or ev.get("commitment_id")
            )
            if target:
                reopened_ids_at[str(target)] = idx
            sv = _as_seq(d.get("commitment_seq"))
            if sv is not None:
                reopened_seqs_at[sv] = idx
        elif et == "commitment_reclassified":
            # Stage D fold (S5/S6): latest kind override wins; applied to the
            # in-memory copy only — the original event is never rewritten.
            target = d.get("target_id") or d.get("commitment_id")
            new_kind = d.get("new_kind") or d.get("new_type")
            if new_kind:
                if target:
                    kind_overrides_by_id[str(target)] = {
                        "kind": new_kind, "seq": ev.get("seq")}
                sv = _as_seq(d.get("target_seq"))
                if sv is not None:
                    kind_overrides_by_seq[sv] = {
                        "kind": new_kind, "seq": ev.get("seq")}
        elif et == "commitment_updated":
            # Stage A fold: record the latest due-shifting update per target.
            # Id chain mirrors the closer chain's data-first priority; updates
            # that carry no new due (scope/summary-only changes) are ignored —
            # they must not erase an earlier deferral.
            target = (
                d.get("commitment_id")
                or d.get("target_id")
                or ev.get("commitment_id")
            )
            new_due = d.get("new_due") or d.get("due") or d.get("due_date")
            if target and new_due:
                due_updates[str(target)] = {"due": new_due, "seq": ev.get("seq")}
            # S4 wording fold: explicit new_title / new_summary only — the
            # CRU schedule-shift path's change_summary is prose describing
            # WHAT changed, never the new wording, and must not clobber the
            # title. Each field folds independently (newest wins per field);
            # a due-only update never erases an earlier wording fix and vice
            # versa.
            if target and (d.get("new_title") or d.get("new_summary")):
                slot = wording_updates.setdefault(str(target), {})
                if d.get("new_title"):
                    slot["title"] = d["new_title"]
                    slot["title_seq"] = ev.get("seq")
                if d.get("new_summary"):
                    slot["summary"] = d["new_summary"]
                    slot["summary_seq"] = ev.get("seq")
            # W4b adjudication fold: Mine (owner_confirmed) / Keep both
            # (review_flags_cleared). Keyed on the explicit booleans the
            # commitment_state writers stamp — a generic update never
            # accidentally clears a review flag. Latest wins per target.
            if target and (d.get("owner_confirmed") or d.get("review_flags_cleared")):
                confirmations[str(target)] = {
                    "owner_id": d.get("new_owner_id"),
                    "owner_name": d.get("new_owner_name"),
                    "clear_flags": bool(d.get("review_flags_cleared")),
                    "seq": ev.get("seq"),
                    "idx": idx,
                }
            # AUTOAPPLY §4c review fix — the flag-tier fallback (the additive
            # mirror of the Keep-both clear above). Keyed on the explicit
            # boolean flag_duplicate_for_review stamps, so an ordinary update
            # can never accidentally RAISE a review flag either.
            if target and d.get("review_flags_set"):
                review_flag_sets[str(target)] = {
                    "reason": d.get("review_reason"),
                    "duplicate_of": d.get("suspected_duplicate_of"),
                    "score": d.get("suspected_duplicate_score"),
                    "seq": ev.get("seq"),
                    "idx": idx,
                }
            # WATCHGATE §2.3 watch fold. Keyed on the explicit booleans the
            # watch_gate writers stamp, exactly like the two folds above — an
            # ordinary update can never accidentally park or un-park an item.
            # A watch_set with a non-dict payload is ignored rather than
            # stamped: a malformed park must not make an item unreadable.
            if target and d.get("watch_set") and isinstance(d.get("watch"), dict):
                watch_marks[str(target)] = {"watch": d["watch"],
                                            "seq": ev.get("seq"), "idx": idx}
            elif target and d.get("watch_cleared"):
                watch_marks[str(target)] = {"watch": None,
                                            "seq": ev.get("seq"), "idx": idx}
        elif et == "commitment_partial_received":
            # MC1 receipt fold: union the delivering counterparty (id and/or
            # free-text name) onto the target. Accumulate — never overwrite;
            # append-only marks pile up until the roster is complete.
            target = (
                d.get("commitment_id")
                or d.get("target_id")
                or ev.get("commitment_id")
            )
            rid = d.get("received_counterparty_id")
            rnm = d.get("received_counterparty_name")
            sv = _as_seq(d.get("commitment_seq"))
            if target:
                tkey = str(target)
                if isinstance(rid, str) and rid.strip():
                    received_ids_by_id.setdefault(tkey, set()).add(rid.strip())
                if isinstance(rnm, str) and rnm.strip():
                    received_names_by_id.setdefault(tkey, set()).add(rnm.strip())
            if sv is not None:
                if isinstance(rid, str) and rid.strip():
                    received_ids_by_seq.setdefault(sv, set()).add(rid.strip())
                if isinstance(rnm, str) and rnm.strip():
                    received_names_by_seq.setdefault(sv, set()).add(rnm.strip())
        elif et == "commitment_reassigned":
            # S4 reassignment fold: latest event wins wholesale (a second
            # reassignment replaces the first, including its confirmed flag).
            target = (
                d.get("commitment_id")
                or d.get("target_id")
                or ev.get("commitment_id")
            )
            if target and (d.get("new_owner_id") or d.get("new_counterparty_id")):
                reassignments[str(target)] = {
                    "new_owner_id": d.get("new_owner_id"),
                    "new_counterparty_id": d.get("new_counterparty_id"),
                    "new_owner_name": d.get("new_owner_name"),
                    "new_counterparty_name": d.get("new_counterparty_name"),
                    "confirmed": bool(d.get("confirmed")),
                    "seq": ev.get("seq"),
                    "idx": idx,
                }
        elif et == "commitment":
            cid_ev = _commitment_id(ev)
            commitment_by_id[cid_ev] = ev
            status = _commitment_field(ev, "status") or "open"
            if status in ("open", "overdue"):
                open_evs.append(ev)
                pid = d.get("parent_id")
                if isinstance(pid, str) and pid.strip():
                    child_records.append((cid_ev, ev.get("seq"), pid.strip()))

    def _closed_here(cid: str, seq) -> bool:
        """Closed iff the LATEST closure (id chain or F3 seq alias) comes
        after the LATEST reopen (Stage D undo). Never closed → -1."""
        seq_ok = isinstance(seq, int) and not isinstance(seq, bool)
        last_close = max(
            closed_ids_at.get(cid, -1),
            closed_seqs_at.get(seq, -1) if seq_ok else -1,
        )
        last_reopen = max(
            reopened_ids_at.get(cid, -1),
            reopened_seqs_at.get(seq, -1) if seq_ok else -1,
        )
        return last_close > last_reopen

    # SUB1 fold prep — effective parent per child (merge re-point, cycle-safe)
    # and the per-parent child roster in append order.
    def _eff_parent(pid: str) -> str:
        seen: set = set()
        while pid in superseded_onto and pid not in seen:
            seen.add(pid)
            pid = superseded_onto[pid]
        return pid

    child_parent_eff: dict[str, str] = {}
    children_by_parent: dict[str, list] = {}
    for ccid, cseq, rawpid in child_records:
        eff = _eff_parent(rawpid)
        child_parent_eff[ccid] = eff
        children_by_parent.setdefault(eff, []).append((ccid, cseq))

    def _parse_date_head(value):
        if not value or not isinstance(value, str):
            return None
        try:
            import datetime as _dtm
            return _dtm.date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None

    out: list[dict] = []
    for c in open_evs:
        cid = _commitment_id(c)
        seq = c.get("seq") if isinstance(c.get("seq"), int) else None
        if _closed_here(cid, seq):
            continue
        patch: dict = {}
        upd = due_updates.get(cid)
        if upd:
            # In-memory copy with the EFFECTIVE due — data.due is first in
            # the alias chain, so it wins over a variant data.due_date or a
            # flat top-level due. History on disk is untouched.
            patch["due"] = upd["due"]
            patch["due_updated_by_seq"] = upd["seq"]
        wu = wording_updates.get(cid)
        if wu:
            # S4 wording fold: EFFECTIVE title/summary from the latest `fix
            # wording` update per field. The original wording stays in
            # history (append-only) — this is an in-memory copy only.
            if wu.get("title"):
                patch["title"] = wu["title"]
                patch["title_updated_by_seq"] = wu["title_seq"]
            if wu.get("summary"):
                patch["summary"] = wu["summary"]
                patch["summary_updated_by_seq"] = wu["summary_seq"]
        # Adjudication folds, applied in APPEND ORDER (v4.6.0 S4 reassignment
        # + v4.6.1 W4b owner-confirm / review-clear): the latest adjudication
        # decides pending_review; owner/counterparty folds compose, later
        # events overriding earlier ones. data.owner_id is first in the alias
        # chain, so the patch wins over legacy owner / owner_person_id
        # spellings.
        adjudications = []
        ra = reassignments.get(cid)
        if ra:
            adjudications.append(("reassign", ra))
        cf = confirmations.get(cid)
        if cf:
            adjudications.append(("confirm", cf))
        fs = review_flag_sets.get(cid)
        if fs:
            adjudications.append(("flagset", fs))
        adjudications.sort(key=lambda pair: pair[1]["idx"])
        for kind, entry in adjudications:
            if kind == "flagset":
                # AUTOAPPLY §4c review fix: the auto tier could not honor a
                # stamp, so the pair goes back to being a QUESTION. Same
                # shape the capture-time flag tier writes — pending_review +
                # the C4 duplicate fields — reached additively.
                patch["pending_review"] = True
                if entry.get("reason"):
                    patch["review_reason"] = entry["reason"]
                if entry.get("duplicate_of"):
                    patch["suspected_duplicate_of"] = entry["duplicate_of"]
                if entry.get("score") is not None:
                    patch["suspected_duplicate_score"] = entry["score"]
                patch["review_flagged_by_seq"] = entry["seq"]
            elif kind == "reassign":
                # An UNCONFIRMED reassignment stamps pending_review (the item
                # sits in the unconfirmed bucket and never enters chase — no
                # auto-email on a guessed owner); a confirmed one clears it
                # (the reassignment IS the adjudication).
                if entry.get("new_owner_id"):
                    patch["owner_id"] = entry["new_owner_id"]
                    if entry.get("new_owner_name"):
                        patch["owner_name"] = entry["new_owner_name"]
                if entry.get("new_counterparty_id"):
                    patch["counterparty_id"] = entry["new_counterparty_id"]
                    if entry.get("new_counterparty_name"):
                        patch["counterparty_name"] = entry["new_counterparty_name"]
                patch["reassigned_by_seq"] = entry["seq"]
                if entry["confirmed"]:
                    patch["pending_review"] = False
                else:
                    patch["pending_review"] = True
                    patch["review_reason"] = (
                        "reassigned — confirm the new owner before this is chased"
                    )
            else:
                # W4b Mine / Keep both: the explicit user click IS the
                # adjudication — pending_review clears, review_reason drops;
                # Keep both additionally clears the C4 duplicate flags
                # (confirmed distinct — both items stay open).
                if entry.get("owner_id"):
                    patch["owner_id"] = entry["owner_id"]
                    if entry.get("owner_name"):
                        patch["owner_name"] = entry["owner_name"]
                patch["pending_review"] = False
                patch["review_reason"] = None
                patch["owner_confirmed_by_seq"] = entry["seq"]
                if entry.get("clear_flags"):
                    patch["suspected_duplicate_of"] = None
                    patch["suspected_duplicate_score"] = None
                    patch["review_cleared_by_seq"] = entry["seq"]
        # WATCHGATE §2.3 — the watch stamp, additive and last-writer-wins.
        # `data.watch` present == parked; absent == not parked. Nothing else
        # about the row changes, which is the whole back-compat contract.
        wm = watch_marks.get(cid)
        if wm is not None:
            if wm["watch"] is None:
                patch["watch"] = None
            else:
                patch["watch"] = dict(wm["watch"])
                patch["watch_set_by_seq"] = wm["seq"]
        ko = kind_overrides_by_id.get(cid) or (
            kind_overrides_by_seq.get(seq) if seq is not None else None
        )
        if ko:
            # Stage D fold: EFFECTIVE kind from the additive reclassification
            # marker (S6 migration / S5 promote) — label change, never
            # delete/recreate; original event untouched on disk.
            patch["kind"] = ko["kind"]
            patch["kind_overridden_by_seq"] = ko["seq"]
        merged = merged_onto.get(cid)
        if merged:
            # C4 fold: the survivor of a merge carries the union of absorbed
            # provenance — every source_ref except its own primary one, plus
            # the superseded ids. In-memory copy only.
            own_ref = (c.get("data") or {}).get("source_ref")
            refs = [r for r in merged["refs"] if r != own_ref]
            if refs:
                patch["merged_source_refs"] = refs
            if merged["from"]:
                patch["merged_from"] = list(merged["from"])
        # MC1 receipt fold: union the accumulated per-person receipts (id +
        # seq keyings) onto the projection, then derive the propose-closure
        # flag over the EFFECTIVE roster (computed on the merged copy below,
        # so it sees any reassignment fold's counterparty change).
        rec_ids: set = set(received_ids_by_id.get(cid, set()))
        rec_names: set = set(received_names_by_id.get(cid, set()))
        if seq is not None:
            rec_ids |= received_ids_by_seq.get(seq, set())
            rec_names |= received_names_by_seq.get(seq, set())
        if rec_ids:
            patch["received_from"] = sorted(rec_ids)
        if rec_names:
            patch["received_from_names"] = sorted(rec_names)
        # SUB1 child stamps: effective parent (merge re-pointed), the parent's
        # effective title for "part of: […]" rendering, and the orphan flag
        # when the parent is closed/missing (crash-window children render
        # top-level with a "was part of" note — never vanish, D2).
        eff_parent = child_parent_eff.get(cid)
        if eff_parent:
            patch["parent_id"] = eff_parent
            parent_ev = commitment_by_id.get(eff_parent)
            if parent_ev is not None:
                p_wu = wording_updates.get(eff_parent) or {}
                patch["parent_title"] = (
                    p_wu.get("title")
                    or _commitment_field(parent_ev, "title") or ""
                )
                p_status = _commitment_field(parent_ev, "status") or "open"
                p_seq = (parent_ev.get("seq")
                         if isinstance(parent_ev.get("seq"), int) else None)
                if (p_status not in ("open", "overdue")
                        or _closed_here(eff_parent, p_seq)):
                    patch["parent_closed"] = True
            else:
                patch["parent_closed"] = True
        # SUB1 parent stamps: child roster + progress + the PROPOSE-closure
        # signal + the annotation-only next due (D-7: never folded into the
        # parent's own data.due — a deferred parent stays deferred).
        kids = children_by_parent.get(cid)
        if kids:
            open_kids = [
                (kcid, kseq) for kcid, kseq in kids
                if not _closed_here(
                    kcid, kseq if isinstance(kseq, int)
                    and not isinstance(kseq, bool) else None)
            ]
            patch["subitem_ids"] = [kcid for kcid, _s in kids]
            patch["n_subitems_open"] = len(open_kids)
            patch["n_subitems_done"] = len(kids) - len(open_kids)
            if not open_kids:
                # All sub-items done — PROPOSE "close it?"; never auto-close
                # (the parent may carry residual work the children never
                # listed — standing doctrine, mirror of MC1).
                patch["all_subitems_resolved"] = True
            else:
                best_raw = None
                best_date = None
                for kcid, _s in open_kids:
                    k_upd = due_updates.get(kcid)
                    if k_upd:
                        raw_due = k_upd["due"]
                    else:
                        k_ev = commitment_by_id.get(kcid) or {}
                        raw_due = _commitment_field(k_ev, "due")
                    kd = _parse_date_head(raw_due)
                    if kd is not None and (best_date is None or kd < best_date):
                        best_date = kd
                        best_raw = raw_due
                if best_raw is not None:
                    patch["next_subitem_due"] = best_raw
        if patch:
            c = {**c, "data": {**(c.get("data") or {}), **patch}}
        if rec_ids or rec_names:
            # Derived, in-memory only: PROPOSE closure when every counterparty
            # has delivered. Never a closer — the item stays open until the
            # user closes it (PROPOSE, never auto-close).
            from commitment_parties import all_counterparties_received as _all_rcv
            if _all_rcv(c, workspace_root=workspace_root):
                c["data"]["all_counterparties_received"] = True
        out.append(c)
    if cache_key is not None:
        _OPEN_COMMITMENTS_CACHE[cache_key] = (sig, out)
    return list(out)


def split_pending_review(open_commitments: list[dict]) -> tuple[list[dict], list[dict]]:
    """INTAKE — partition a projected open set into (confirmed, needs_review).

    `needs_review` is the UNCONFIRMED-EXTRACTION queue: items the extractor
    itself flagged (`data.pending_review`), which `_is_pending_review`
    already bars from auto-close and chase. They are not open commitments in
    any user-visible number or list — they live in the needs-your-call queue
    until the user confirms or drops one.

    THE seam every user-facing reader uses. `load_open_commitments` itself
    is deliberately NOT filtered: it is the projection primitive, and the
    write paths (close/confirm/dedup/sent-capture) must still see pending
    rows. Order is preserved inside both halves.
    """
    confirmed: list[dict] = []
    needs_review: list[dict] = []
    for ev in open_commitments or []:
        (needs_review if _is_pending_review(ev) else confirmed).append(ev)
    return confirmed, needs_review


def load_needs_review(
    events_jsonl_path: str | Path,
    since_ts=None,
    *,
    events: Optional[list] = None,
    workspace_root=None,
) -> list[dict]:
    """INTAKE — the needs-your-call queue: `load_open_commitments` filtered to
    the UNCONFIRMED extractions. Same arguments, same projection (all the
    adjudication folds already applied, so a confirmed item is correctly
    absent). Returns the projected event dicts, append order preserved."""
    opens = load_open_commitments(
        events_jsonl_path, since_ts, events=events, workspace_root=workspace_root
    )
    return split_pending_review(opens)[1]


# -----------------------------------------------------------------------------
# Kind policy layer (Phase 2 Stage D — code-enforced per the 2026-07-01
# ratification condition: "tasks never enter CRU" is a code-level kind
# filter, not prose)
# -----------------------------------------------------------------------------


def _commitment_kind(ev: dict) -> str:
    """Effective kind of a (projected) commitment event; missing → promise
    (read-side default, forever). Mirrors commitment_state.commitment_kind —
    kept local to avoid a circular import."""
    kind = (ev.get("data") or {}).get("kind")
    return kind if isinstance(kind, str) and kind else "promise"


def partition_subitems(open_commitments: list[dict]) -> tuple[list[dict], list[dict]]:
    """SUB1 D2 — partition an open set into (top_level, sub_items).

    An item is a SUB-ITEM iff its `data.parent_id` (the loader has already
    re-pointed merged-away parents to their survivor) names ANOTHER item in
    the SAME supplied set — a live parent link. An ORPHAN child (live child
    whose parent is closed — reachable only through the cascade crash window,
    D3) has no live link, so it partitions TOP-LEVEL: it is real open work
    and must never vanish from the total. Every counting/chase/brief surface
    partitions through THIS function; none re-derives the rule."""
    ids = {_commitment_id(c) for c in (open_commitments or [])}
    top: list[dict] = []
    subs: list[dict] = []
    for c in open_commitments or []:
        pid = (c.get("data") or {}).get("parent_id")
        if (isinstance(pid, str) and pid and pid != _commitment_id(c)
                and pid in ids):
            subs.append(c)
        else:
            top.append(c)
    return top, subs


def parent_blocks_auto_resolve(ev: dict) -> bool:
    """SUB1 D3 — THE shared programmatic-closer predicate: True when a
    (projected) commitment has open sub-items, so an automatic closure must
    DOWNGRADE to a propose (`pending_review` recommendation) instead of
    auto-resolving. Programmatic closers never cascade — closing a parent
    whose steps are still open needs the user's one-line confirm. Reads the
    loader's `n_subitems_open` stamp (0/absent → False)."""
    n = (ev.get("data") or {}).get("n_subitems_open")
    return isinstance(n, int) and not isinstance(n, bool) and n > 0


def commitment_source_refs(ev: dict) -> set[str]:
    """Every source_ref a (projected) commitment can be attributed to.

    AUTOAPPLY §6 — the circularity fence needs to know "did THIS transcript
    create this item?", and after a C4 merge the answer is spread across two
    fields: the survivor keeps its own `data.source_ref` while the loader
    folds the absorbed item's refs into `data.merged_source_refs`. A fence
    reading only `source_ref` would let a merged-away self-match back
    through. Both shape locations (`data.<>` and flat top-level) are read,
    per shared/COMMITMENT_SCHEMA.md's multi-shape contract.
    """
    out: set[str] = set()
    if not isinstance(ev, dict):
        return out
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for holder in (d, ev):
        ref = holder.get("source_ref")
        if isinstance(ref, str) and ref.strip():
            out.add(ref.strip())
        merged = holder.get("merged_source_refs")
        if isinstance(merged, list):
            for r in merged:
                if isinstance(r, str) and r.strip():
                    out.add(r.strip())
    return out


def commitment_matches_source_ref(ev: dict, own_ref: Optional[str]) -> bool:
    """RECONFENCE — True when `ev` is attributed to `own_ref`.

    Layer 1 of the circularity fence in the form the SEND paths need. The
    transcript path can compare `granola:<id>` refs literally because one
    connector writes them one way; a MESSAGE ref reaches us in several
    spellings for the same artifact (legacy `gmail:<Id>` at any case, the
    structured `data.provenance` object, the `gmail_message_id` field
    channel), so a raw `in` test lets a self-match straight back through.

    Three comparisons, cheapest first:
      1. raw membership in `commitment_source_refs` (covers `data.source_ref`,
         the flat variant, AND a C4 merge survivor's `merged_source_refs`);
      2. the same set compared as CANONICAL dedup keys (R16 — two spellings
         of one artifact must reduce to one key);
      2b. the same set compared through `is_same_artifact` with the provider
         taken from OUR OWN key's prefix — the back-compat tolerance, scoped
         exactly as `commitment_matches_thread_ref` scopes it one function
         below. See SWEEPBACK below for why this comparison was needed.
      3. the event's own canonical key, which additionally reads
         `data.provenance` and the gmail id-field channel that
         `commitment_source_refs` does not.

    SWEEPBACK — WHY 2b EXISTS, AND WHY IT MATTERS MOST TO A MERGE SURVIVOR.
    Comparisons 1 and 2 are exact on the provider label, so they hold only while
    a ref and the message being scored were written under the SAME backend. The
    caller-side BUG-3719 guard covers the cutover case — but it reads
    `canonical_dedup_key(event=c)`, i.e. the commitment's PRIMARY ref only, and a
    C4 merge survivor's absorbed refs live in `merged_source_refs`. So on a
    workspace that spans a backend switch, a survivor could be closed by the very
    message that opened the twin it swallowed: the pre-filter never looked at that
    ref, and layer 1 saw `gmail:<id>` against a `superhuman:<id>` key and called
    them different. Found by building the SWEEPBACK inheritance proof the spec
    demanded rather than assuming it (non-negotiable 5), on the exact population a
    historical sweep meets.

    The tolerance is the SAME shape and the SAME scope as the thread-ref twin: for
    a PREFIXED ref the provider comes from the incoming key's own prefix, so
    `is_same_artifact`'s resolved branch admits that provider's key and the legacy
    `gmail:` anchor and nothing else — rather than `None`, which would match any
    prefix across a candidate set spanning granola, session, meeting, gcal and
    slack. And this consumer is the fail-SAFE one: over-matching here EXCLUDES a
    candidate from a close, so the worst case is a real completion waiting for the
    next fire (and matching is idempotent). That is the opposite of
    `sent_capture.already_captured`, where over-matching suppresses a capture —
    which is why the tolerance is applied here and not there.

    A PREFIX-LESS incoming ref keeps the pre-existing permissive comparison: there
    is no provider to derive, so `own_prov` really is `None` on that one branch and
    the native halves are compared alone. Both live callers build their key with
    `primary_artifact_key`, which always prefixes, so that branch is unreachable
    from the send and inbound rails. Stated because the twin states it, and because
    the paragraph above otherwise reads as a promise the code does not make on
    every input (review F-1).

    (3) is what makes this fence STRICTLY STRONGER than the caller-side
    BUG-3719 self-closure guard in `reconcile_sent_commitments`, and per the
    F-54 no-resurface-derivation rule it lives HERE so every send-scoring
    caller inherits it — not just the one caller that happens to have its own
    guard. `own_ref` falsy → False, so the fence is inert by default.

    ON THE FAIL-OPEN AT THE IMPORT (review F-2): if provenance cannot be
    imported this degrades to comparison (1) ALONE — raw exact-spelling
    membership, which has already run above — plus layer 2. It is not
    unfenced, and it is not layer-2-only. What makes the fail-open SAFE
    rather than merely tolerable is non-obvious and worth naming: the only
    wired caller, `reconcile_sent_commitments`, imports `canonical_dedup_key`
    at MODULE level (line 47), so a broken provenance module fails that import
    closed and loud long before this branch could be reached. `cru_match`
    imports it lazily, which is what creates the latent branch — so a FUTURE
    caller that passes `send_source_ref` without a hard provenance dependency
    would silently get the weaker comparison. Fix the import, not this line.
    """
    ref = (own_ref or "").strip()
    if not ref or not isinstance(ev, dict):
        return False
    refs = commitment_source_refs(ev)
    if ref in refs:
        return True
    try:
        from connector_adapters.provenance import (canonical_dedup_key,
                                                   is_same_artifact)
    except Exception:
        # No provenance module → raw compare above is the whole fence. Fail
        # OPEN rather than crash the matcher: the caller-side BUG-3719 guard
        # and the same-fire layer still stand.
        return False
    own_key = canonical_dedup_key(source_ref=ref)
    if not own_key:
        return False
    # Our own key's two halves — the provider half is what SCOPES the
    # back-compat tolerance to this provider plus the legacy anchor; the native
    # half keeps every remaining segment, so a `slack:<chan>:<ts>` ref never
    # collapses onto a bare id. Identical derivation to the thread-ref twin.
    own_prov = own_key.split(":", 1)[0] if ":" in own_key else None
    native = own_key.split(":", 1)[-1]
    for r in refs:
        cand = canonical_dedup_key(source_ref=r)
        if cand == own_key or is_same_artifact(cand, own_prov, native):
            return True
    return canonical_dedup_key(event=ev) == own_key


# RECONFENCE F-4 — the "fence itself is unusable" sentinel. Comparing every
# candidate against the earliest representable instant excludes ALL of them,
# because layer 2 drops anything captured at-or-after the fire start. That is
# the maximally-fenced reading, and it is the SAFE one: the run closes and
# proposes nothing (no writes, and the pass is idempotent so the next fire with
# a good value does the work), rather than scoring on unfenced.
_FIRE_START_UNUSABLE = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _normalize_fire_start(value, param="exclude_captured_since"):
    """RECONFENCE — normalize a fence's OWN timestamp input.

    Review F-3/F-4. The first cut of this block did two things wrong, and both
    were failures of the fence rather than of the data it fences:

      F-3 — it type-checked with `hasattr(value, "tzinfo")`, which is True for
      a NAIVE datetime. The naive value passed through untouched and then hit
      `captured >= fire_start` against the AWARE datetime `_parse_ts` returns,
      raising TypeError from inside the candidate loop and taking down the
      whole matcher. A naive datetime is a legitimate input shape, so it is
      NORMALIZED here (UTC attached, mirroring `_parse_ts`'s own defensive
      handling) and can no longer take matching down.

      F-4 — a malformed ISO string or an off-type value (an epoch int) left
      `fire_start = None`, which SILENTLY disabled layer 2 entirely. That is
      the AUTOAPPLY F-5 dead-rail shape: a fence present, tested, and inert.
      It matters here because `fire_start` is LLM-produced at the SKILL.md
      call site. Such a value now fails SAFE — the same posture layer 2 already
      documents for an unparseable CANDIDATE ts (exclude, never admit) — and
      says so loudly on stderr. The fence's own parameter must not fail open
      when the data it fences fails closed.

    None → None, the documented inert default, which is NOT an error: it is how
    every pre-RECONFENCE caller asks for no layer 2 at all. An empty string is
    deliberately NOT treated as None here — a blank where a timestamp belongs
    is an unfilled template, which is exactly the silent fence-off F-4 is about.

    SHARED BY BOTH FENCED RAILS as of F-10 (SENTMATCH): `match_send_to_commitments`
    and `match_transcript_to_commitments` both route their layer-2 input through
    here. The two blocks were byte-identical duplicates and the transcript one
    still carried the defective variant; editing this function now moves both
    rails, which is the point.

    EVORDER — also the normalizer for `send_ts` / `inbound_ts` (layer 3), which
    is why the loud message takes the parameter NAME rather than hardcoding it.
    Before that, a malformed `send_ts` printed `exclude_captured_since=<value>`
    and sent whoever grepped for it at the wrong knob entirely.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:                      # F-3
            return value.replace(tzinfo=datetime.timezone.utc)
        return value
    if isinstance(value, str):
        parsed = _parse_ts(value)
        if parsed is not None:
            return parsed
    # F-4: unparseable string, or a type this fence cannot read at all.
    import sys as _sys
    print(
        f"RECONFENCE: {param}={value!r} is not a usable "
        f"timestamp — failing SAFE (excluding every candidate) rather than "
        f"scoring unfenced. Pass an ISO-8601 string or a datetime.",
        file=_sys.stderr,
    )
    return _FIRE_START_UNUSABLE


def cru_eligible(open_commitments: list[dict]) -> list[dict]:
    """Stage D policy: TASK-kind commitments NEVER enter CRU matching. The
    matchers only make sense with a counterparty — a self-owed task can't be
    closed by an outbound email, an inbound delivery, a transcript mention,
    or a calendar invite; scoring them produces false auto-closes and chase
    noise. Every match_* path filters through this ONE function; a surface
    that hands the matchers a raw event list still gets the filter because
    the filter lives here, not at the call sites.

    SUB1 D5 — live SUB-ITEMS never enter CRU either (same mechanism): the
    parent is the commitment of record; the counterparty cares about the
    deliverable, not the user's step list. This also blocks the Path-1
    failure mode where an outbound send about the deliverable auto-closes
    one STEP on title overlap. Orphan children partition top-level and stay
    eligible — they are real open work."""
    top_level, _subs = partition_subitems(open_commitments or [])
    return [c for c in top_level if _commitment_kind(c) != "task"]


# -----------------------------------------------------------------------------
# SENTMATCH — Path 1 closes on evidence of DELIVERY, not on title echo
# -----------------------------------------------------------------------------
#
# `_overlap_coefficient`'s denominator is `min(|a|, |b|)` and an email body is
# always the longer side, so the Path-1 score measures exactly one thing: what
# fraction of the commitment TITLE's own words reappear in the email. Against
# the title "Send Sam Sample the Q3 pricing deck":
#
#   "Hi Sam, here you go. Let me know what you think."  → 0.17, no candidate
#   "Attaching the Q3 pricing deck as promised."        → 0.60, closes
#
# The email that IS the deliverable never closes; the email that merely quotes
# the title does. Measured on the operator's real substrate (the same shape,
# scoring 0.20 there): 73 runs scanned 1,372 sent messages and auto-closed 21
# (1.53%); the median owned title is 8 content words, so five of them must
# literally reappear before anything closes.
#
# THE FIX IS NOT A LOWER THRESHOLD. 0.333 is the measured noise floor and the
# miss class sits at 0.20 and below, where no threshold reaches without
# admitting noise with it. The fix is a SECOND basis for closing that does not
# read the title as evidence at all:
#
#   A. DELIVERY EVIDENCE — an attachment rode along, the recipient is a
#      counterparty of an item this user owes, and the body says it is done.
#      Promotes only the `no_action` band (score < pending), which IS the
#      measured miss class; the 0.30-0.55 band keeps FS-11's ambiguity-aware
#      caller-side promotion and the >= 0.55 title path is byte-unchanged.
#   B. THREAD PRIOR — the send happened inside the conversation the commitment
#      was captured from. That is a connector-provided identity link, so a weak
#      title score on THAT thread is corroboration rather than the evidence.
#      Promotes `no_action` to `pending_review` only — the close itself still
#      goes through FS-11's unambiguous-1:1 rule at the caller, so the existing
#      ambiguity posture is reused rather than re-invented.
#
# Both bases run INSIDE the existing candidate loop, AFTER both RECONFENCE
# layers have already dropped self-referential and same-fire candidates, so
# fence inheritance is structural rather than re-derived (the F-54 rule).

DELIVERY_BASIS = "delivery_evidence"
THREAD_BASIS = "thread_prior"
AMBIGUOUS_DELIVERY_BASIS = "delivery_evidence_ambiguous"


def commitment_thread_refs(ev: dict) -> set[str]:
    """Every THREAD-level ref a (projected) commitment can be attributed to.

    SENTMATCH signal B asks "did this send happen in the conversation this
    commitment came from?". A commitment's `source_ref` names a MESSAGE
    (`gmail:<message_id>`, COMMITMENT_SCHEMA § source_ref) — not a thread — so
    the thread has to come from the id channels that actually carry one:

      - `thread_ref` — the canonical thread key stamped at capture by
        `sent_capture.build_sent_commitment_event` (SENTMATCH). The
        provider-neutral channel; the only one a non-Gmail backend fills.
      - `provenance.thread_native_id` / `gmail_thread_id` — the two spellings
        `connector_adapters.provenance` already owns for a thread id. Read
        here so a commitment written by any future call site that uses the
        shared provenance builder is covered without a second edit.

    Both shape locations (`data.<>` and flat top-level) are read, per
    COMMITMENT_SCHEMA's multi-shape contract. Values are returned RAW; the
    caller canonicalizes, so one spelling of a thread never masquerades as a
    different thread.
    """
    out: set[str] = set()
    if not isinstance(ev, dict):
        return out
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for holder in (d, ev):
        if not isinstance(holder, dict):
            continue
        for key in ("thread_ref", "gmail_thread_id"):
            v = holder.get(key)
            if isinstance(v, str) and v.strip():
                out.add(v.strip())
        prov = holder.get("provenance")
        if isinstance(prov, dict):
            v = prov.get("thread_native_id")
            if isinstance(v, str) and v.strip():
                provider = prov.get("provider")
                out.add(f"{provider}:{v.strip()}" if provider else v.strip())
    return out


def commitment_matches_thread_ref(ev: dict, thread_ref: Optional[str]) -> bool:
    """True when `ev` was captured inside the conversation `thread_ref` names.

    Two channels, both compared as CANONICAL dedup keys (R16 — two spellings
    of one artifact must reduce to one key, and a raw `in` test lets a
    differently-cased thread id read as a different thread):

      1. the commitment's own thread refs (`commitment_thread_refs`);
      2. the commitment's SOURCE refs. This fires when the item was captured
         from the message that OPENED the thread — on Gmail a thread's id is
         the id of its first message, so `gmail:<thread>` and the capture's
         `gmail:<first message>` reduce to the same key. It is a no-op on a
         backend where the two id spaces are disjoint, which is the correct
         behavior there: no claim is made, so nothing matches.

    A thread hit is a PRIOR, never a closure on its own — the caller still
    requires a non-zero title score before the candidate leaves `no_action`.
    `thread_ref` falsy → False, so signal B is inert by default.

    TRAINFIX (MAILSEAM residual 3): the compare is PROVIDER-LABEL TOLERANT.
    Both callers now build their key with `primary_artifact_key(provider, …)`
    rather than a `gmail` literal, so on a Superhuman workspace the key reads
    `superhuman:<thread>` — while `thread_ref` rows stamped before the backend
    was declared (and every row `sent_capture` wrote pre-MAILSEAM) carry the
    legacy `gmail:` anchor. One thread, two labels. A single-key compare would
    silently stop matching the moment a workspace declares a backend, which is
    the same shape as the guard MAILSEAM just fixed one function over.

    THE TOLERANCE IS SCOPED TO THE BACK-COMPAT CASE, AND ONLY THAT (review
    F-1). The provider is taken from the INCOMING key's own prefix, never left
    unresolved: `is_same_artifact`'s resolved branch matches `{provider}:{id}`
    OR the legacy `gmail:` anchor, which is precisely and only the population
    this tolerance exists for. The first cut passed `None`, which matches ANY
    prefix — and the candidate set is `commitment_thread_refs(ev) |
    commitment_source_refs(ev)`, whose source-ref half spans granola, session,
    meeting, gcal, slack and a dozen more. So a `granola:<uuid>` ref would have
    anchored a mail reply carrying the same native id. That matters HERE more
    than on the send rail: Path 4's R1 turns a thread hit into `auto_resolve`
    with no title floor, while Path 1's signal B only ever reaches
    `pending_review` and additionally requires `score > 0.0`. Measured exposure
    on the reference substrate was zero (0 cross-provider thread-id collisions;
    the 4 cross-source native-id collisions were all granola/meeting UUID
    pairs, none involving a mail id) — so this is a tightening, not a repair,
    and it closes the class by construction rather than by id-namespace luck.

    THE RESIDUAL TRADE, NAMED (see `is_same_artifact`'s own per-consumer note):
    within one provider label plus the legacy anchor this is still the
    OVER-matching direction, and unlike the self-ref fence this consumer can
    CLOSE rather than merely decline to. What bounds it now: a false positive
    needs the SAME provider (or the legacy `gmail:` anchor) to mint the
    identical id under two different threads, and the thread hit is one
    conjunct among several — the reply must also come from the item's owner,
    carry completion language or the artifact the item asked for, not be a
    reschedule, and survive the one-reply-one-delivery ambiguity guard.

    A prefix-less incoming ref keeps the pre-existing permissive comparison
    (there is no provider to derive). Both live callers build their key with
    `primary_artifact_key`, which always prefixes, so that branch is
    unreachable from the send and inbound rails.
    """
    ref = (thread_ref or "").strip()
    if not ref or not isinstance(ev, dict):
        return False
    try:
        from connector_adapters.provenance import (canonical_dedup_key,
                                                   is_same_artifact)
    except Exception:
        # No provenance module → the thread prior cannot be established
        # honestly, so it does not fire. Fail CLOSED: signal B only ever ADDS
        # closure power, so its unavailable state must add none.
        return False
    own_key = canonical_dedup_key(source_ref=ref)
    if not own_key:
        return False
    # Our own key's two halves. The PROVIDER half is what scopes the
    # tolerance: handing it to `is_same_artifact` admits that provider's key
    # and the legacy `gmail:` anchor and nothing else. The NATIVE half — a
    # bare key is its own native id; a multi-segment key keeps its remaining
    # segments, so a `slack:<chan>:<ts>` ref never collapses onto a bare id.
    own_prov = own_key.split(":", 1)[0] if ":" in own_key else None
    native = own_key.split(":", 1)[-1]
    for r in commitment_thread_refs(ev) | commitment_source_refs(ev):
        cand = canonical_dedup_key(source_ref=r)
        if cand == own_key or is_same_artifact(cand, own_prov, native):
            return True
    return False


def _delivery_counterparty_hit(d: dict, recipient_set: set,
                               recipient_name_tokens: set,
                               party_ids: set, owner_id: str) -> bool:
    """SENTMATCH signal A's counterparty conjunct — is this send going to the
    person the item is owed TO?

    DELIBERATELY NARROWER than the candidacy gate that admitted the candidate.
    That gate has three routes and one of them is the Bug #103 recall
    fallback: a recipient NAME TOKEN appearing in the commitment TITLE. That
    route is right for candidacy (it only opens the door; the score still
    decides) and wrong here, because signal A closes without reading the
    title — "Send Sam the deck" plus any attachment to any Sam would close it.
    A name in a title is not a statement about who is owed.

    The two routes that ARE statements about who is owed:
      - a RESOLVED recipient who is a party to the item and is not its owner.
        Covers the Stage-E `counterparty_id(s)` receipt (already unioned into
        `party_ids` by the caller) AND every pre-Stage-E commitment, which
        carry the counterparty in `person_ids` and nothing else — excluding
        those would leave the new basis dead on all historic items.
      - a recipient name token matching a free-text `counterparty_name(s)`
        receipt — the extractor naming a counterparty it could not resolve.
        Still an explicit counterparty assertion, unlike the title route.
    """
    from commitment_parties import counterparty_names as _cp_names_local
    if recipient_set & (set(party_ids) - {owner_id}):
        return True
    if recipient_name_tokens:
        for nm in _cp_names_local(d):
            if recipient_name_tokens & set(_tokenize(nm)):
                return True
    return False


# -----------------------------------------------------------------------------
# Path 1 — match an outbound send to open commitments
# -----------------------------------------------------------------------------


def match_send_to_commitments(
    *,
    open_commitments: list[dict],
    sender_person_id: str,
    recipient_person_ids: Iterable[str],
    subject: Optional[str],
    body: Optional[str],
    recipient_names: Optional[Iterable[str]] = None,
    workspace_root=None,
    send_source_ref: Optional[str] = None,
    exclude_captured_since=None,
    has_attachment: bool = False,
    send_thread_ref: Optional[str] = None,
    send_ts=None,
    diagnostics: Optional[dict] = None,
) -> list[dict]:
    """Path 1 — score an outbound send against open commitments.

    THE CIRCULARITY FENCE (RECONFENCE — the AUTOAPPLY §6 fence, mirrored onto
    the send-scoring path; both layers here so every caller inherits them per
    the F-54 no-resurface-derivation rule):

      1. `send_source_ref` — the ref of the message being scored (e.g.
         `gmail:<id>`). Any candidate attributed to that same ref is dropped
         BEFORE scoring, via `commitment_matches_source_ref`, which compares
         raw refs, canonical dedup keys, AND `merged_source_refs` (a C4 merge
         survivor keeps its own `source_ref` while the absorbed item's refs
         move to `merged_source_refs` — so the survivor of a merge that
         swallowed THIS message's capture resolves to a different key and
         sails past a single-key check). Self-evidence is not evidence.

      2. `exclude_captured_since` — an ISO timestamp (or datetime) marking
         the START of this fire. Candidates captured at or after it are
         same-fire siblings: one orchestrator fire captures in an early phase
         and scores sent mail in a later one, so the two share an extraction
         context and are ONE source, not two (AUTOAPPLY §2's independence
         rule). No ref relationship exists between them, so layer 1 is blind
         to this case by construction.

    WHY A CAPTURE-TIME FENCE IS SOUND ON A PATH WITH NO COMPLETION GATE:
    unlike Path 3, sending IS the fulfillment here, so a send genuinely can
    complete a commitment captured minutes earlier. Layer 2 does not touch
    that — it excludes only what THIS FIRE wrote; anything predating the fire
    stays fully matchable. An unparseable capture ts fails SAFE (excluded): a
    candidate whose age we cannot establish is not independent evidence.

    Both default to None = pre-RECONFENCE behavior, byte-identical, so every
    existing caller and test is unaffected. Measured basis: the v5.4.0
    attended dogfood (2026-07-28) saw 3 self-referential proposals on this
    path, worst case a commitment written at 14:38 questioned at 14:40; on
    the regression fixture the unfenced path AUTO-CLOSES both self-matches at
    score 0.8 rather than merely proposing them.

    Filters open_commitments to those where:
      - `data.owner_id` == sender_person_id (the user owes this)
      - the recipient is involved — EITHER a resolved recipient person_id is in
        the commitment's `person_ids`, OR a recipient name token appears in the
        commitment title (the recall fallback, Bug #103).

    Then scores subject+body against each candidate's title.

    `recipient_names` (Bug #103 — real-data recall fix): display names and/or
    email local-parts of the recipients (e.g. "Sam" plus the local-part "sam"
    taken from an address like sam@<domain>; or "Bowie Stone" plus "bstone"). Commitment extraction frequently fails to LINK the
    counterparty into `person_ids` (some commitments were stored with only the
    user) or the counterparty person has no email on file, so the resolved-
    person_id gate alone misses real completions. Titles almost always name the
    recipient ("Send Sam a recap", "Send Bowie Stone a product summary"), so a
    recipient-name token appearing in the title opens the candidacy gate. The
    score_match threshold still decides the recommendation, which keeps false
    positives down — a name in the title only makes the commitment a candidate;
    the subject/body must still overlap it.

    SENTMATCH — THE TWO NON-TITLE CLOSURE BASES (see the block above this
    function for the measurement they come from):

    `has_attachment` (signal A) — the connector's attachment flag for THIS
    message. Absent/False means "no evidence", never "unknown but probably" —
    a missing signal must not manufacture a closure, so the default leaves
    signal A dead. Combined with a counterparty receipt and completion
    language, it promotes a `no_action` candidate straight to auto_resolve:
    the send IS the fulfillment on this path, and the deliverable rode along.

    `send_thread_ref` (signal B) — the ref of the CONVERSATION this message
    sits in (`gmail:<thread_id>`, canonicalized by the caller, exactly like
    `send_source_ref`). A commitment captured inside that thread gets its
    candidacy floor lowered to "any non-zero title overlap" — the thread id is
    the evidence, the score is corroboration. It promotes to pending_review
    only; the close still runs through FS-11's unambiguous-1:1 rule at the
    caller, so a thread carrying several open items produces confirms, not a
    burst of closures. None → inert.

    NEITHER basis widens the candidacy gate above (owner + recipient), and
    NEITHER lowers `_hi` / `_pend`. Both run after both RECONFENCE layers.

    Returns a list of `{commitment_id, score, recommendation, title}` dicts,
    sorted by score descending. recommendation is one of:
      - "auto_resolve" (score >= HIGH_CONFIDENCE_THRESHOLD, or SENTMATCH
        signal A on a sub-pending candidate)
      - "pending_review" (PENDING_REVIEW_THRESHOLD <= score < HIGH, or
        SENTMATCH signal B on a sub-pending candidate)
      - "no_action" (filtered out — caller can ignore these but they're
        included for diagnostic logging)
    Rows also carry `close_basis`: "" for the title path, `DELIVERY_BASIS`,
    `THREAD_BASIS`, or `AMBIGUOUS_DELIVERY_BASIS` (signal A that matched more
    than one item on one send — downgraded to a confirm).
    """
    if not sender_person_id:
        return []
    _hi, _pend = _match_thresholds(workspace_root)
    recipient_set = {r for r in recipient_person_ids if r}
    # Recipient-name tokens (>=3 chars) for the title fallback (Bug #103).
    recipient_name_tokens: set[str] = set()
    for n in (recipient_names or []):
        for tok in _tokenize(n):
            if len(tok) >= 3:
                recipient_name_tokens.add(tok)
    # Need SOME recipient signal — either resolved ids or names — or we can't
    # attribute the send to anyone.
    if not recipient_set and not recipient_name_tokens:
        return []

    query = (subject or "") + " " + (body or "")
    results: list[dict] = []

    # SENTMATCH signal A — the two body-level conjuncts, computed once. A
    # schedule shift is the explicit NEGATIVE: "attaching the old deck, the
    # updated one is pushed to Friday" carries an attachment AND completion
    # language AND is not a delivery. It only blocks the new basis; a title
    # match at >= 0.55 closes exactly as it did before.
    has_completion = detect_completion_signal(query)
    has_schedule_shift = detect_schedule_shift_signal(query)
    delivery_body_ok = bool(has_attachment) and has_completion and not has_schedule_shift

    # RECONFENCE fence prep. `own_ref` empty / `fire_start` None → the fence
    # is inert and every pre-RECONFENCE call behaves byte-identically.
    own_ref = (send_source_ref or "").strip()
    # F-3/F-4: a naive datetime is normalized (never crashes the loop); an
    # unusable value fails SAFE and loud instead of silently fencing nothing.
    fire_start = _normalize_fire_start(exclude_captured_since)
    # EVORDER — the SEND's own time. Layer 2 fences against the start of this
    # FIRE, which is a different question: a commitment captured before the
    # fire but AFTER the message sails past it. `None` → inert.
    evidence_at = _normalize_fire_start(send_ts, "send_ts")

    for ev in cru_eligible(open_commitments):  # Stage D: tasks never enter CRU
        # Layer 1 — a send can never corroborate a conclusion about an item
        # it created. Dropped before scoring, so the row is NEVER CREATED
        # rather than created-then-suppressed.
        if own_ref and commitment_matches_source_ref(ev, own_ref):
            continue
        # Layer 2 — same-fire captures share one extraction context, so they
        # are one source. An unparseable capture ts fails SAFE (excluded).
        if fire_start is not None:
            captured = _parse_ts(event_time(ev))
            if captured is None or captured >= fire_start:
                continue
        if _commitment_field(ev, "owner_id") != sender_person_id:
            continue
        _d = ev.get("data") or {}
        person_ids = set(ev.get("person_ids") or []) | set(_d.get("person_ids") or [])
        # Stage E (F5) extraction receipts: the counterparty the extractor
        # linked at capture feeds the candidacy gate DIRECTLY — the structural
        # fix for the Bug #103 recall class (the title-token fallback below
        # stays for historic events that never got a receipt). MC1: union the
        # FULL counterparty roster (legacy single + counterparty_ids list) so a
        # send to ANY of a multi-counterparty commitment's people is a
        # candidate.
        from commitment_parties import (
            counterparty_ids as _cp_ids,
            counterparty_names as _cp_names,
            has_multiple_counterparties as _multi_cp,
        )
        person_ids.update(_cp_ids(_d))
        title = _commitment_field(ev, "title") or ""
        counterparty_name = " ".join(_cp_names(_d))
        # Recipient is involved if a resolved id is in person_ids (incl. the
        # Stage E counterparty receipt) OR a recipient name token appears in
        # the title (the #103 recall fallback) OR matches the free-text
        # counterparty_name receipt (Stage E — named-but-unresolved people).
        recipient_in_pids = bool(recipient_set & person_ids)
        recipient_in_title = bool(recipient_name_tokens & set(_tokenize(title)))
        recipient_in_cp_name = bool(
            counterparty_name
            and recipient_name_tokens & set(_tokenize(counterparty_name))
        )
        if not (recipient_in_pids or recipient_in_title or recipient_in_cp_name):
            continue
        # Layer 3 (EVORDER) — a send cannot be evidence for a promise that did
        # not exist when it was sent. Path 5 has carried this guard since
        # SENTMATCH (`match_calendar_to_commitments`); Paths 1 and 4 never did,
        # and F-11 measured four false closes on the operator's real substrate
        # from the gap — including one item still open and overdue days after
        # it was "closed". Dropped before scoring, like the other two layers.
        #
        # PLACEMENT: below the owner and recipient gates, unlike layers 1 and 2
        # which sit at the top of the loop. Two reasons, and the review that
        # caught this was right on both. (a) The COUNT has to mean something: up
        # top it fired once per (message x every open commitment newer than that
        # message) regardless of owner or recipient, so a single 24h-old send
        # against the operator's substrate reported ~46 "drops" and a 20-message
        # fire reported high hundreds — a number that cannot serve as the
        # negative-control signal the spec asked it to be. (b) It saves a
        # `fromisoformat` per irrelevant candidate. Fencing power is unchanged:
        # a candidate the owner or recipient gate already dropped never produced
        # a row for layer 3 to prevent.
        #
        # Asymmetric on purpose, and the absent-vs-malformed split matters on
        # BOTH sides — this module's own presence-test doctrine ("`False` is the
        # connector answering, an absent key is the connector never being
        # asked"):
        #   * `send_ts` ABSENT (None) → `evidence_at is None` → INERT. A fence
        #     that cannot judge ordering must not exclude, and a provider that
        #     carries no send times must not lose closure entirely.
        #   * `send_ts` PRESENT but unusable → `_normalize_fire_start` fails SAFE
        #     and LOUD (warns, excludes everything). Someone handed us a value
        #     and it is junk; for a CLOSURE engine "close nothing" is the
        #     conservative direction, and silently switching the guard off is the
        #     exact failure class this release exists to eliminate.
        #   * strict `>`, not `>=`: capture == send is the "captured from this
        #     very message" case, which layer 1 already owns by ref.
        #   * capture time ABSENT → inert for that candidate, NOT excluded. A
        #     commitment with no capture time cannot exhibit this defect (it
        #     needs a capture NEWER than the send), and excluding it would
        #     silently stop closing every ts-less item — a behavior change far
        #     wider than the bug. Measured: 683/683 real commitment events carry
        #     a parseable `ts`, so this branch is belt-and-braces, and 10 shipped
        #     tests assert the ts-less item still closes.
        #   * capture time PRESENT but unparseable → exclude, mirroring layer 2.
        if evidence_at is not None:
            _raw_captured = event_time(ev)
            if _raw_captured:
                captured = _parse_ts(_raw_captured)
                if captured is None or captured > evidence_at:
                    if isinstance(diagnostics, dict):
                        diagnostics["stale_evidence_dropped"] = (
                            diagnostics.get("stale_evidence_dropped", 0) + 1)
                    continue
        score = score_match(query, title)
        if score >= _hi:
            rec = "auto_resolve"
        elif score >= _pend:
            rec = "pending_review"
        else:
            rec = "no_action"
        # SENTMATCH — the two non-title bases, applied ONLY to the `no_action`
        # band. That band is the measured miss class (the deliverable email
        # lands at 0.20 and below); everything at or above `_pend` keeps the
        # behavior it has today, including FS-11's ambiguity-aware promotion
        # at the caller, so neither basis can silently re-grade an existing
        # match.
        basis = ""
        if rec == "no_action":
            if delivery_body_ok and _delivery_counterparty_hit(
                    _d, recipient_set, recipient_name_tokens,
                    person_ids, sender_person_id):
                # A deliverable was delivered. A SCHEDULING commitment ("set
                # up a call with <name>") is not deliverable by attachment —
                # a booked calendar event closes those (Path 5), so they are
                # ineligible here rather than closable by any file at all.
                if not detect_scheduling_intent(title):
                    rec = "auto_resolve"
                    basis = DELIVERY_BASIS
            elif (send_thread_ref and score > 0.0
                    and commitment_matches_thread_ref(ev, send_thread_ref)):
                rec = "pending_review"
                basis = THREAD_BASIS
        if rec == "auto_resolve" and _is_pending_review(ev):
            rec = "pending_review"
        # MC1: a send to ONE counterparty of a MULTI-counterparty commitment
        # fulfills only THAT person's portion — it must NOT whole-close the
        # item (the "send the deck to the board" bug: chasing/closing one
        # board member for all). Downgrade auto_resolve → partial_received and
        # name the matched counterparties so the caller records a per-person
        # receipt instead of a full closure. Single-counterparty items are
        # untouched (guard only fires on multi), so all pre-MC1 behavior and
        # tests hold byte-identically.
        #
        # F-28 — `workspace_root` is what lets this predicate see that ONE
        # person written as an id AND that person's name is one counterparty,
        # not two. Without it, such an item was downgraded to per-leg receipts
        # and the phantom name leg could never receive one, so the item never
        # closed at all. The workspace is what the roster reader needs to
        # resolve the name against the entity graph; `None` (no workspace on
        # hand) keeps the raw union, i.e. pre-F-28 behavior.
        matched_cp_ids: list = []
        matched_cp_names: list = []
        if rec == "auto_resolve" and _multi_cp(_d, workspace_root=workspace_root):
            roster_ids = set(_cp_ids(_d))
            matched_cp_ids = [i for i in _cp_ids(_d) if i in recipient_set]
            matched_cp_names = [
                nm for nm in _cp_names(_d, workspace_root=workspace_root)
                if recipient_name_tokens & set(_tokenize(nm))
            ]
            rec = "partial_received"
        # SUB1 D3: a parent with OPEN sub-items is never auto-closed by a
        # matcher — programmatic closers propose, the user confirms (the
        # cascade needs their one-line confirm; parent_blocks_auto_resolve
        # is THE shared predicate).
        if rec == "auto_resolve" and parent_blocks_auto_resolve(ev):
            rec = "pending_review"
        results.append({
            "commitment_id": _commitment_id(ev),
            "score": score,
            "recommendation": rec,
            "title": title,
            "owner_id": sender_person_id,
            "primary_thread_id": ev.get("primary_thread_id") or "",
            "matched_counterparty_ids": matched_cp_ids,
            "matched_counterparty_names": matched_cp_names,
            "close_basis": basis,
        })

    # SENTMATCH — ONE send, ONE delivery. A single attachment cannot be the
    # evidence for two different deliverables at once, so when signal A puts a
    # second auto-grade row on the same message the delivery-evidence rows
    # step down to a confirm. This is FS-11's ruling applied to the new basis
    # ("only multi-candidate AMBIGUITY stays a confirm proposal"), and it is
    # the guard that makes signal A's thin conjunction safe: the title path is
    # never touched by it, so a >= 0.55 match keeps closing.
    _delivery_rows = [r for r in results if r["close_basis"] == DELIVERY_BASIS]
    if _delivery_rows and sum(
            1 for r in results if r["recommendation"] == "auto_resolve") > 1:
        for r in _delivery_rows:
            if r["recommendation"] == "auto_resolve":
                r["recommendation"] = "pending_review"
                r["close_basis"] = AMBIGUOUS_DELIVERY_BASIS

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# -----------------------------------------------------------------------------
# Path 3 — match a meeting transcript to open commitments
# -----------------------------------------------------------------------------


def match_transcript_to_commitments(
    *,
    open_commitments: list[dict],
    attendee_person_ids: Iterable[str],
    transcript_text: str,
    workspace_root=None,
    transcript_source_ref: Optional[str] = None,
    exclude_captured_since=None,
    transcript_ts=None,
    diagnostics: Optional[dict] = None,
) -> list[dict]:
    """Path 3 — score a meeting transcript against open commitments where any
    meeting attendee is the owner.

    THE CIRCULARITY FENCE (AUTOAPPLY §6 — two layers, both here so every
    caller inherits them per the F-54 no-resurface-derivation rule):

      1. `transcript_source_ref` — the ref of the transcript being scored
         (e.g. `granola:<id>`). Any candidate attributed to that same ref
         (`commitment_source_refs`, which covers `data.source_ref` AND a
         merged survivor's `merged_source_refs`) is dropped BEFORE scoring.
         A commitment extracted from transcript T title-matches T at ~1.0
         and carries no completion language — the meeting that CREATED an
         ask rarely also completes it — so Path 3's own conservative branch
         emitted `pending_review`: a confirm row born of evidence that
         corroborates nothing. Self-evidence is not evidence.

      2. `exclude_captured_since` — an ISO timestamp (or datetime) marking
         the START of this fire. Candidates captured at or after it are
         same-fire SIBLINGS: meeting A's asks scored against meeting B's
         transcript five minutes later, in one batch. They share the
         extraction context, so they are one source, not two (§2's
         independence rule), and the ref check alone cannot see them.

    Both default to None = pre-AUTOAPPLY behavior, byte-identical, so every
    existing caller and test is unaffected. Measured basis: 10 of the 14
    review proposals written in the 2026-07-26 04:25 fire on the reference
    substrate targeted commitments captured 04:20–04:21 in that same fire.

      3. `transcript_ts` (EVORDER's third rail, F-27) — the MEETING's own
         time, i.e. WHEN THE STATEMENTS WERE MADE. Layer 2 fences against the
         start of THIS FIRE, which is a different question entirely: a
         commitment captured before the fire but AFTER the meeting ended sails
         straight through it. Demonstrated by execution before this guard
         existed: a commitment captured 20:00, a transcript from an 18:00
         meeting saying "I sent the revised pricing sheet already, it is
         done", `exclude_captured_since` at 23:00 → `auto_resolve` at 0.75. A
         meeting cannot be evidence that a promise made two hours later was
         already kept. `diagnostics` is the optional dict layer 3 counts its
         refusals into (`stale_evidence_dropped`), which the past-meetings
         orchestrator folds onto its receipt as `n_stale_evidence_skipped` —
         the same key both mail rails already report.

         This is the highest-volume rail of the three: past-meetings wrote 295
         of 683 commitment events on the reference substrate — more than any
         other closer — and it runs daily.

    For each candidate commitment, compute:
      - title-match score against the transcript (Jaccard, see score_match)
      - completion-signal flag (True if transcript contains "sent the" etc.)
      - schedule-shift flag (True if transcript contains "pushed to" etc.)
      - new-ask flag (True if transcript contains "can you also" etc.)

    Recommendation logic (conservative — order matters; first match wins):
      - score >= HIGH AND completion AND NOT schedule_shift AND NOT new_ask
        → "auto_resolve"
      - score >= HIGH AND schedule_shift
        → "commitment_updated" (deadline shifted; commitment stays open)
      - score >= HIGH AND new_ask
        → "supersede" (spawn new commitment, link via supersedes; old stays
          open until separately resolved)
      - PENDING_REVIEW_THRESHOLD <= score < HIGH AND any signal present
        → "pending_review"
      - else "no_action"

    Returns list of dicts sorted by score descending.
    """
    if not transcript_text:
        return []
    attendee_set = {a for a in attendee_person_ids if a}
    if not attendee_set:
        return []
    _hi, _pend = _match_thresholds(workspace_root)

    has_completion = detect_completion_signal(transcript_text)
    has_schedule_shift = detect_schedule_shift_signal(transcript_text)
    has_new_ask = detect_new_ask_signal(transcript_text)

    # §6 fence prep. `own_ref` empty / `fire_start` None → the fence is inert
    # and every pre-AUTOAPPLY call behaves byte-identically.
    own_ref = (transcript_source_ref or "").strip()
    # RECONFENCE F-10 (due once this file was next touched): this rail carried
    # the raw `hasattr(fire_start, "tzinfo")` variant that RECONFENCE's review
    # proved defective on the send rail — F-3, a NAIVE datetime passes the type
    # check untouched and then raises TypeError from inside the candidate loop;
    # F-4, a malformed string or an off-type value leaves fire_start=None and
    # SILENTLY disables layer 2. The two blocks were byte-identical, so the
    # hardening is PORTED here rather than re-derived: one normalizer, one
    # behavior, both rails.
    fire_start = _normalize_fire_start(exclude_captured_since)
    # EVORDER (F-27) — the MEETING's own time; the exact twin of Path 1's
    # `send_ts` and Path 4's `inbound_ts`, and it routes through the SAME
    # normalizer rather than a third copy of the same six lines. `None` →
    # inert; present-but-junk → fails SAFE and LOUD (warns on stderr naming
    # `transcript_ts`, then excludes every candidate).
    evidence_at = _normalize_fire_start(transcript_ts, "transcript_ts")

    results: list[dict] = []
    for ev in cru_eligible(open_commitments):  # Stage D: tasks never enter CRU
        # §6 layer 1 — a transcript can never corroborate a conclusion about
        # an item it created. Dropped before scoring, so the row is NEVER
        # CREATED rather than created-then-suppressed.
        if own_ref and own_ref in commitment_source_refs(ev):
            continue
        # §6 layer 2 — same-fire siblings share one extraction context, so
        # they are one source. An unparseable capture ts fails SAFE (the item
        # is excluded): a candidate whose age we cannot establish must not be
        # treated as independent evidence.
        if fire_start is not None:
            captured = _parse_ts(event_time(ev))
            if captured is None or captured >= fire_start:
                continue
        owner_id = _commitment_field(ev, "owner_id") or ""
        if owner_id not in attendee_set:
            continue
        # Layer 3 (EVORDER, F-27) — a meeting cannot be evidence that a promise
        # captured AFTER the meeting was already kept. Third rail of the same
        # guard Paths 1 and 4 carry and Path 5 has carried since SENTMATCH;
        # this one is the highest-volume closer in the log, and it was the last
        # one running unfenced.
        #
        # PLACEMENT: below the attendee/owner gate, not at the top of the loop
        # with layers 1 and 2. That is EVORDER review B-3's lesson, and it is
        # about the COUNT rather than the fencing: up top the guard fired once
        # per (transcript x every open commitment newer than the meeting)
        # regardless of whether anyone in the room owned the item, which on the
        # reference substrate turned a single day-old message into ~46 reported
        # "drops" and made the number useless as a signal. Fencing power is
        # unchanged — a candidate the owner gate already dropped never produced
        # a row for layer 3 to prevent — and it saves a `fromisoformat` per
        # irrelevant candidate.
        #
        # The asymmetry is deliberate and identical on all three rails:
        #   * `transcript_ts` ABSENT (None) → inert. A fence that cannot judge
        #     ordering must not exclude, and a caller that carries no meeting
        #     times must not lose closure entirely.
        #   * `transcript_ts` PRESENT but unusable → `_normalize_fire_start`
        #     fails SAFE and LOUD (warns, excludes everything). Someone handed
        #     us a value and it is junk; for a closure engine "close nothing" is
        #     the conservative direction, and a fence that silently switches
        #     itself off is the failure class this line of work exists to end.
        #   * strict `>`, not `>=`: capture == meeting is the "captured from
        #     this very transcript" case, which layer 1 already owns by ref.
        #   * capture time ABSENT → inert for that candidate, NOT excluded. An
        #     item with no capture time cannot exhibit this defect (it needs a
        #     capture NEWER than the meeting), and excluding it would silently
        #     stop closing every ts-less item — far wider than the bug.
        #   * capture time PRESENT but unparseable → exclude, mirroring layer 2.
        if evidence_at is not None:
            _raw_captured = event_time(ev)
            if _raw_captured:
                captured = _parse_ts(_raw_captured)
                if captured is None or captured > evidence_at:
                    if isinstance(diagnostics, dict):
                        diagnostics["stale_evidence_dropped"] = (
                            diagnostics.get("stale_evidence_dropped", 0) + 1)
                    continue
        title = _commitment_field(ev, "title") or ""
        score = score_match(transcript_text, title)

        if score < _pend:
            recommendation = "no_action"
        elif score >= _hi:
            if has_schedule_shift and not has_completion:
                recommendation = "commitment_updated"
            elif has_new_ask and not has_completion:
                recommendation = "supersede"
            elif has_completion:
                recommendation = "auto_resolve"
            else:
                # High title match but no fulfillment language. Could be just
                # discussion of the topic. Stay conservative — pending_review.
                recommendation = "pending_review"
        else:
            # MEDIUM range. Only flag if we saw at least one signal.
            if has_completion or has_schedule_shift or has_new_ask:
                recommendation = "pending_review"
            else:
                recommendation = "no_action"

        if recommendation == "auto_resolve" and _is_pending_review(ev):
            recommendation = "pending_review"
        # MC1: never whole-close a multi-counterparty commitment on a single
        # transcript match — a mention that the deliverable was "sent" doesn't
        # prove every counterparty received it. Downgrade to a per-person
        # receipt (the caller can't tell WHICH from a transcript, so it leaves
        # the item open for explicit per-person marking — safe by default).
        # F-28: `workspace_root` lets the roster reader collapse one person
        # written as an id AND that person's name into ONE counterparty, so a
        # single-counterparty item is not downgraded into a state it can never
        # leave. See the Path 1 twin for the full reasoning.
        from commitment_parties import has_multiple_counterparties as _multi_cp
        if recommendation == "auto_resolve" and _multi_cp(
                ev, workspace_root=workspace_root):
            recommendation = "partial_received"
        # SUB1 D3: never auto-close a parent with open sub-items — propose.
        if recommendation == "auto_resolve" and parent_blocks_auto_resolve(ev):
            recommendation = "pending_review"
        results.append({
            "commitment_id": _commitment_id(ev),
            "score": score,
            "recommendation": recommendation,
            "title": title,
            "owner_id": owner_id,
            "primary_thread_id": ev.get("primary_thread_id") or "",
            "has_completion_signal": has_completion,
            "has_schedule_shift_signal": has_schedule_shift,
            "has_new_ask_signal": has_new_ask,
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# -----------------------------------------------------------------------------
# REPLYCLOSE — a counterparty's reply closes a WAITING-ON item
# -----------------------------------------------------------------------------
#
# M ruled 2026-07-29: yes, a client's reply should close a "waiting on X" item.
# Before this, nothing on any mail path could. Path 1 is owner-gated to items
# the USER owes (97 of 351 eligible open items on the reference substrate — 28%
# — are structurally unreachable from sent mail), and Path 4 below could only
# reach a waiting-on item through TITLE ECHO: the counterparty's message had to
# restate the commitment's own words at >= 0.55 before the completion gate even
# applied. The reply that IS the delivery ("here you go", file attached) scores
# 0.20 and closes nothing — SENTMATCH's measured defect, pointed the other way.
#
# THE TWO BASES ADDED HERE, both applied ONLY to the `no_action` band so the
# existing title path is byte-unchanged:
#
#   R1. THREAD-ANCHORED REPLY -> auto_resolve. The inbound message sits in the
#       conversation the commitment was captured from, it comes from the person
#       who OWES the item, and it either says the work is done or carries the
#       artifact the item asked for. Three independent conjuncts, none of them
#       the title.
#   R2. OFF-THREAD REPLY -> pending_review, never more (spec §2.2). The same
#       evidence without the connector-provided thread link is a proposal: a
#       title-similarity close on inbound mail is the title-echo trap with the
#       counterparty holding the pen. A non-zero title overlap is required so a
#       chatty counterparty does not surface every item they have ever owed.
#
# DIRECTION IS LOAD-BEARING (spec §2.3) AND IT IS DERIVED, NEVER STORED.
# `shared/COMMITMENT_SCHEMA.md` § "Direction": *derived from `owner_id` vs the
# primary user; storing it would create a second source of truth*, and
# `surface_split.classify_surface` is THE projector — Waiting On is exactly
# "owner present AND owner != user". So a reply can only close an item whose
# OWNER is the person who sent it, and the user's own message is refused at the
# top of the function rather than filtered per-candidate: on a thread the user
# replied to last, "the latest message" is the user's, and the pre-REPLYCLOSE
# owner gate (`owner_id == sender`) matches the user's OWN open items happily.
# That is the sent path's job, on the sent path's evidence.

REPLY_BASIS = "reply_evidence"
REPLY_PROPOSED_BASIS = "reply_proposed"
AMBIGUOUS_REPLY_BASIS = "reply_evidence_ambiguous"


# -----------------------------------------------------------------------------
# THE EVIDENCE AUTO-BAR (SWEEPBACK, M ruling 2026-07-29)
# -----------------------------------------------------------------------------
#
# The two bases below are the ones where a close stands on something OTHER than
# how much the message's words overlap the commitment's title:
#
#   * DELIVERY_BASIS — the user's own outbound carried the artifact to a person
#     who is a counterparty of the item (Path 1, SENTMATCH signal A);
#   * REPLY_BASIS — the counterparty replied INSIDE the conversation the item was
#     captured from, and either said it was done or attached what was asked for
#     (Path 4, REPLYCLOSE R1).
#
# WHY THIS PREDICATE EXISTS, AND WHY IT LIVES HERE. A historical backlog sweep
# re-reads months of mail. Over that span the TITLE path is not the rare, specific
# signal the v5.6.0 rationale assumed: the dogfood measured a bare reply subject
# ("Re: q4 vendor list") scoring 0.750 against "Q4 vendor list to <name>" all by
# itself, because `_overlap_coefficient`'s denominator is `min(|a|,|b|)` and a
# short subject that echoes the deliverable's name saturates it (F-19). On one
# live rail, in real time, that is acceptable — the window is a day and the user
# is watching. Applied automatically across 180 days of mail it is noise at scale.
#
# So the sweep's AUTO tier is exactly `auto_resolve` on one of these two bases,
# and it is defined ONCE, HERE, beside the matchers that mint the bases — not
# restated in the sweep. Two properties follow that a copy could not give:
#
#   1. the sweep can never be MORE permissive than the rails, because the
#      `recommendation` it reads is the rails' own verdict, produced by the rails'
#      own conjunctions (owner gate, direction stop, both circularity layers,
#      EVORDER layer 3, the MC1 downgrade, the pending-review floor, the
#      one-send-one-delivery and one-reply-one-delivery ambiguity guards);
#   2. if the rails' bar ever moves, the sweep's moves with it in the same commit,
#      because there is one object.
#
# The live rails do NOT call this: on the daily fires FS-11's unambiguous-moderate
# title match is a legitimate auto-close and M has ruled twice that it should be.
# This predicate is the HISTORICAL narrowing, and its one consumer is the sweep.
AUTO_CLOSE_EVIDENCE_BASES = frozenset({DELIVERY_BASIS, REPLY_BASIS})


def closes_on_evidence(row) -> bool:
    """True when a matcher/driver row is an auto-close standing on EVIDENCE.

    `row` is a row from `match_send_to_commitments` / `match_inbound_to_commitments`
    or a proposal from `reconcile_sent` / `reconcile_inbound` (both carry
    `recommendation` and `close_basis` unchanged). Both conditions are required:

      * `recommendation == "auto_resolve"` — the rails' own verdict, after every
        downgrade they apply. A `partial_received`, a `pending_review`, or a
        `commitment_updated` is not an auto-close and never becomes one here.
      * `close_basis` in `AUTO_CLOSE_EVIDENCE_BASES` — delivery evidence or a
        thread-anchored reply. The empty basis (the TITLE path) is excluded, and
        so are both AMBIGUOUS bases, which the rails already stepped down to a
        confirm.

    Anything else is a PROPOSAL. Non-dict input is False — a fence that cannot
    read its input must not admit.
    """
    if not isinstance(row, dict):
        return False
    return (row.get("recommendation") == "auto_resolve"
            and (row.get("close_basis") or "") in AUTO_CLOSE_EVIDENCE_BASES)


# Artifact nouns that make a commitment title an ASK FOR A DOCUMENT, so an
# inbound message carrying an attachment is itself the fulfillment shape (spec
# §2.1) even when the words never reach `COMPLETION_PHRASES`.
#
# WHY THIS EXISTS RATHER THAN A WIDER PHRASE LIST. SENTMATCH's review measured
# the recall ceiling on `COMPLETION_PHRASES` — "Please find attached." is False,
# and it is plausibly the most common delivery sentence in business email. The
# ruling was to DEFER widening that list, because it is shared with Path 3's
# completion gate and reached by Path 4, so one edit changes another rail's
# auto-close behavior — the one-rail mistake this train exists to kill, run in
# reverse. This predicate answers the same recall problem on THIS rail only: it
# reads the COMMITMENT TITLE (a fixed, already-captured string), never the
# message, so no other path's behavior moves by a byte.
DELIVERABLE_ARTIFACT_NOUNS = (
    "deck", "slides", "presentation", "report", "doc", "docs", "document",
    "contract", "agreement", "invoice", "proposal", "spreadsheet", "statement",
    "memo", "summary", "draft", "pdf", "file", "files", "budget", "forecast",
    "quote", "estimate", "scope", "sow", "brief", "roster", "spec",
    "breakdown", "analysis", "worksheet", "deliverable", "attachment",
)


def detect_deliverable_artifact(title: Optional[str]) -> bool:
    """True when a commitment TITLE names a document-shaped artifact.

    Token-level, not substring: a substring test would fire `doc` inside
    `docket` and `spec` inside `specific`. `_tokenize` already lowercases,
    strips punctuation, and drops stop-words, so this reads the same tokens the
    scorer does.
    """
    if not title:
        return False
    nouns = set(_phrases("deliverable_artifact_nouns", DELIVERABLE_ARTIFACT_NOUNS))
    return bool(nouns & set(_tokenize(title)))


def commitment_is_waiting_on(ev: dict, *, user_person_id: Optional[str],
                             sender_person_id: Optional[str]) -> bool:
    """REPLYCLOSE's direction predicate — is `ev` a WAITING-ON item that the
    inbound sender is the OWNER of?

    Re-derived from the underlying commitment fields through the ONE canonical
    projector (`surface_split.classify_surface`, CTS1 §2.4), never from a
    rendered row — the dogfood found surface rows carrying no direction at all.
    That classifier reads `owner_id` through `_commitment_field`'s full alias
    chain (`owner_id` / `owner_person_id` / `owner`, `data.<>` then flat), so a
    legacy-shaped commitment is judged by the same rule as a canonical one.

    Two conditions, both required:
      1. a resolved primary user exists — direction is a COMPARISON, and with
         no user there is nothing to compare against (`classify_surface`
         documents its own None degrade as "every owned item lands waiting_on",
         which is right for a count and catastrophic for a closure);
      2. the item classifies WAITING ON — owner present, owner != user — and
         its owner IS the sender.

    Together those imply `sender != user`, so this predicate deliberately does
    NOT restate that comparison: a check that can never change an answer is not
    a fence, it is decoration that a mutation test cannot distinguish from a
    working one. The direction STOP that IS load-bearing lives at the top of
    `match_inbound_to_commitments`, where it refuses the message outright.

    An UNOWNED item (no resolvable owner) classifies out here, which is the
    honest answer: no counterparty id means no sender can ever be its owner, so
    those items cannot participate in reply-matching at all. An UNCONFIRMED one
    (pending_review) classifies out too — the user has not adjudicated who owns
    it, and delivery evidence must not answer the second question before the
    first.
    """
    if not user_person_id or not sender_person_id:
        return False
    try:
        from surface_split import SURFACE_WAITING_ON, classify_surface
    except Exception:
        # The projector is core-owned and always present; if it somehow is not,
        # fail CLOSED. This predicate only ever ADDS closure power, so its
        # unavailable state must add none.
        return False
    if classify_surface(ev, user_person_id) != SURFACE_WAITING_ON:
        return False
    return (_commitment_field(ev, "owner_id") or "") == sender_person_id


# -----------------------------------------------------------------------------
# Path 4 — match an inbound email to open commitments
# -----------------------------------------------------------------------------


def match_inbound_to_commitments(
    *,
    open_commitments: list[dict],
    sender_person_id: str,
    subject: Optional[str],
    body: Optional[str],
    workspace_root=None,
    user_person_id: Optional[str] = None,
    inbound_source_ref: Optional[str] = None,
    exclude_captured_since=None,
    inbound_thread_ref: Optional[str] = None,
    has_attachment: bool = False,
    inbound_ts=None,
    diagnostics: Optional[dict] = None,
) -> list[dict]:
    """Path 4 — score an inbound email against open commitments where the
    SENDER is the owner.

    Mirror image of Path 1 (`match_send_to_commitments`): on outbound, the USER
    is the owner and we resolve commitments the user owed. On inbound, the
    COUNTER-PARTY who sent the email is the owner — their message is evidence
    they delivered on something they owed the user. So we pre-filter to
    commitments where `data.owner_id == sender_person_id`.

    Completion-gated like Path 3 (transcript): a high title match alone is not
    enough — counter-parties routinely email ABOUT a topic without delivering
    it ("still working on the deck"). We require fulfillment language before
    auto-resolving. This is the key difference from Path 1, where the act of
    SENDING is itself the fulfillment so no completion gate is needed.

    Recommendation logic (conservative — order matters; first match wins):
      - score >= HIGH AND schedule_shift AND NOT completion
        → "commitment_updated" (they pushed their own deadline; stays open)
      - score >= HIGH AND completion AND NOT schedule_shift
        → "auto_resolve" (they delivered)
      - score >= HIGH (no actionable signal, or conflicting signals)
        → "pending_review" (topic matches but no clear fulfillment)
      - PENDING_REVIEW_THRESHOLD <= score < HIGH AND any signal present
        → "pending_review"
      - else "no_action"

    New-ask language is intentionally NOT acted on here. On inbound, "can you
    also send X" is the counter-party asking the USER for something new — that
    creates a commitment the user owes, which is inbox-triage's job, not a
    resolution of the sender's own commitment. We compute the flag for
    diagnostics but never resolve/supersede on it.

    THE CIRCULARITY FENCE (REPLYCLOSE — RECONFENCE's two layers, ported onto
    this rail; both here so every caller inherits them per the F-54
    no-resurface-derivation rule):

      1. `inbound_source_ref` — the ref of the message being scored (e.g.
         `gmail:<id>`). Any candidate attributed to that same ref is dropped
         BEFORE scoring, via `commitment_matches_source_ref` (raw refs,
         canonical dedup keys, `merged_source_refs`, and the structured
         provenance / gmail-id channels). This rail needed it MOST: inbox-triage
         stamps `data.source_ref: gmail:<message_id>` on the commitments it
         extracts from inbound mail, so the counterparty's own promise
         ("I'll send the deck Friday") becomes an open item attributed to that
         message — and a later re-scan of the SAME message title-matches its own
         capture at ~1.0. The reply that CREATED an item is not the reply that
         completed it. Self-evidence is not evidence.

      2. `exclude_captured_since` — an ISO timestamp (or datetime) marking the
         START of this fire. Candidates captured at or after it are same-fire
         siblings: one inbox fire that extracts commitments in an early phase
         and scores inbound mail in a later one shares an extraction context, so
         the two are ONE source, not two (AUTOAPPLY §2's independence rule). No
         ref relationship exists between them, so layer 1 is blind to this case
         by construction. An unparseable capture ts fails SAFE (excluded).

    Both default to None = pre-REPLYCLOSE behavior, byte-identical.

    REPLYCLOSE — THE TWO REPLY BASES (see the block above this function):

    `user_person_id` — the primary user. Passing it turns on the DIRECTION
    check, and the check is a hard stop, not a filter: a message whose sender IS
    the user returns `[]` immediately. Without it the pre-existing owner gate
    (`owner_id == sender`) happily matches the user's OWN open items against the
    user's OWN message, closing a promise the user made as though a counterparty
    had delivered it. None → the check is inert (pre-REPLYCLOSE behavior) and
    both new bases stay dead, because direction cannot be established without a
    user to compare against.

    `inbound_thread_ref` — the ref of the CONVERSATION this message sits in
    (`gmail:<thread_id>`, canonicalized by the caller exactly like
    `inbound_source_ref`). A commitment captured inside that thread, replied to
    by its own owner with completion language or the artifact it asked for,
    closes outright (basis `REPLY_BASIS`). None → R1 is inert.

    `has_attachment` — the connector's attachment flag for THIS message. Absent
    / False means "no evidence", never "unknown but probably". Combined with a
    title that names a document (`detect_deliverable_artifact`), it is the
    FULFILLMENT SHAPE: the answer to a request for a document is the document.

    NEITHER basis widens the candidacy gate (owner == sender), NEITHER lowers
    `_hi` / `_pend`, and both run AFTER both fence layers.

    Returns list of `{commitment_id, score, recommendation, title, owner_id,
    primary_thread_id, has_completion_signal, has_schedule_shift_signal,
    has_new_ask_signal}` dicts sorted by score descending. `owner_id` is the
    sender (the counter-party) — the caller uses it as `resolved_by` when
    writing the `commitment_resolved` event. Rows also carry `close_basis`: ""
    for the unchanged title path, `REPLY_BASIS`, `REPLY_PROPOSED_BASIS`, or
    `AMBIGUOUS_REPLY_BASIS` (one reply that closed more than one item —
    downgraded to a confirm).
    """
    if not sender_person_id:
        return []

    # REPLYCLOSE direction check (spec §2.3) — a hard stop BEFORE any scoring.
    # Only a COUNTERPARTY's reply closes a waiting-on item; the user's own
    # message on that thread must not. Loud, because a caller that feeds this
    # path the user's own mail has a bug worth seeing.
    if user_person_id and sender_person_id == user_person_id:
        import sys as _sys
        print(
            "REPLYCLOSE: match_inbound_to_commitments was handed the primary "
            "user as the SENDER — an inbound reply path cannot score the "
            "user's own message. Scoring nothing. (The outbound equivalent is "
            "match_send_to_commitments.)",
            file=_sys.stderr,
        )
        return []

    query = (subject or "") + " " + (body or "")
    if not query.strip():
        return []

    _hi, _pend = _match_thresholds(workspace_root)
    has_completion = detect_completion_signal(query)
    has_schedule_shift = detect_schedule_shift_signal(query)
    has_new_ask = detect_new_ask_signal(query)

    # REPLYCLOSE — the message-level half of both bases, computed once. A
    # schedule shift is the explicit NEGATIVE: "here you go on the interim
    # numbers, the final set slips to Friday" carries completion language and is
    # not a completion. It only ever blocks the NEW bases; the existing
    # `commitment_updated` branch below still fires on it exactly as before.
    reply_says_done = has_completion and not has_schedule_shift
    reply_carries_artifact = bool(has_attachment) and not has_schedule_shift

    # Fence prep. `own_ref` empty / `fire_start` None → the fence is inert and
    # every pre-REPLYCLOSE call behaves byte-identically. `_normalize_fire_start`
    # is the shared normalizer both other fenced rails already use (RECONFENCE
    # F-3/F-4 + F-10): a naive datetime is normalized rather than crashing the
    # loop, and an unusable value fails SAFE and loud rather than silently
    # fencing nothing.
    own_ref = (inbound_source_ref or "").strip()
    fire_start = _normalize_fire_start(exclude_captured_since)
    # EVORDER — the REPLY's own time; see the Path 1 twin. `None` → inert.
    evidence_at = _normalize_fire_start(inbound_ts, "inbound_ts")

    results: list[dict] = []
    for ev in cru_eligible(open_commitments):  # Stage D: tasks never enter CRU
        # Layer 1 — a reply can never corroborate a conclusion about an item it
        # created. Dropped before scoring, so the row is NEVER CREATED rather
        # than created-then-suppressed.
        if own_ref and commitment_matches_source_ref(ev, own_ref):
            continue
        # Layer 2 — same-fire captures share one extraction context, so they are
        # one source. An unparseable capture ts fails SAFE (excluded).
        if fire_start is not None:
            captured = _parse_ts(event_time(ev))
            if captured is None or captured >= fire_start:
                continue
        owner_id = _commitment_field(ev, "owner_id") or ""
        if owner_id != sender_person_id:
            continue
        # Layer 3 (EVORDER) — a reply cannot be evidence for a promise that did
        # not exist when the reply was sent. Exact twin of the Path 1 guard,
        # same asymmetry: unknown reply time → inert; unknown capture time with
        # a known reply time → exclude; strict `>` so capture == reply still
        # closes. No live instance was observed on this rail, but the hole is
        # structurally identical and leaving one rail unguarded is what let
        # F-11 hide for a week behind Path 5 already having an ordering check.
        # Placed below the owner gate for the same two reasons as the Path 1
        # twin: a meaningful count, and no wasted parse per irrelevant candidate.
        # Absent capture time → inert, not excluded; see the Path 1 twin for the
        # full reasoning and the 683/683 measurement behind it.
        if evidence_at is not None:
            _raw_captured = event_time(ev)
            if _raw_captured:
                captured = _parse_ts(_raw_captured)
                if captured is None or captured > evidence_at:
                    if isinstance(diagnostics, dict):
                        diagnostics["stale_evidence_dropped"] = (
                            diagnostics.get("stale_evidence_dropped", 0) + 1)
                    continue
        title = _commitment_field(ev, "title") or ""
        score = score_match(query, title)

        if score < _pend:
            recommendation = "no_action"
        elif score >= _hi:
            if has_schedule_shift and not has_completion:
                recommendation = "commitment_updated"
            elif has_completion and not has_schedule_shift:
                recommendation = "auto_resolve"
            else:
                # High title match but no clear fulfillment language, or
                # conflicting signals. Stay conservative — pending_review.
                recommendation = "pending_review"
        else:
            # MEDIUM range. Only flag if we saw at least one signal.
            if has_completion or has_schedule_shift:
                recommendation = "pending_review"
            else:
                recommendation = "no_action"

        # REPLYCLOSE — the two reply bases, applied ONLY to the `no_action`
        # band. That band is the measured miss class (the reply that IS the
        # delivery lands at 0.20 and below); everything at or above `_pend`
        # keeps the grade it has today, so neither basis can silently re-grade
        # an existing match or move a threshold.
        basis = ""
        if recommendation == "no_action" and (reply_says_done
                                              or reply_carries_artifact):
            # The fulfillment shape is narrower than "an attachment arrived": an
            # attachment answers an ask for a DOCUMENT. It does not answer
            # "set up a call" — a booked calendar event does (Path 5) — and it
            # does not answer an ask that names no artifact at all.
            evidence_ok = reply_says_done or (
                reply_carries_artifact and detect_deliverable_artifact(title))
            if (evidence_ok
                    and not detect_scheduling_intent(title)
                    and commitment_is_waiting_on(
                        ev, user_person_id=user_person_id,
                        sender_person_id=sender_person_id)):
                if inbound_thread_ref and commitment_matches_thread_ref(
                        ev, inbound_thread_ref):
                    # R1 — the connector says this reply is IN the conversation
                    # the item came from. That link is not a similarity score,
                    # so no title floor applies: requiring one would put the
                    # title back in the evidence chain, which is the whole
                    # defect. The one-reply-one-delivery guard below is what
                    # keeps a busy thread from closing several items at once.
                    recommendation = "auto_resolve"
                    basis = REPLY_BASIS
                elif score > 0.0:
                    # R2 (spec §2.2) — same evidence, no thread link: PROPOSE,
                    # never close. Off-thread, all that ties this message to
                    # this item is title similarity, and closing on that is the
                    # title-echo trap with the counterparty holding the pen.
                    # The non-zero floor keeps a chatty counterparty from
                    # surfacing every item they have ever owed.
                    recommendation = "pending_review"
                    basis = REPLY_PROPOSED_BASIS

        if recommendation == "auto_resolve" and _is_pending_review(ev):
            recommendation = "pending_review"
        # MC1: an inbound delivery from ONE counterparty of a multi-
        # counterparty owed-to-you item closes only that person's leg —
        # downgrade the whole close to a per-person receipt (the sender is the
        # matched counterparty). Single-counterparty items unchanged.
        # F-28: `workspace_root` collapses one person written as an id AND that
        # person's name into ONE counterparty — see the Path 1 twin.
        from commitment_parties import has_multiple_counterparties as _multi_cp
        matched_cp_ids: list = []
        if recommendation == "auto_resolve" and _multi_cp(
                ev, workspace_root=workspace_root):
            from commitment_parties import counterparty_ids as _cp_ids
            if sender_person_id in _cp_ids(ev):
                matched_cp_ids = [sender_person_id]
            recommendation = "partial_received"
        # SUB1 D3: never auto-close a parent with open sub-items — propose.
        if recommendation == "auto_resolve" and parent_blocks_auto_resolve(ev):
            recommendation = "pending_review"
        results.append({
            "commitment_id": _commitment_id(ev),
            "score": score,
            "recommendation": recommendation,
            "title": title,
            "owner_id": owner_id,
            "primary_thread_id": ev.get("primary_thread_id") or "",
            "has_completion_signal": has_completion,
            "has_schedule_shift_signal": has_schedule_shift,
            "has_new_ask_signal": has_new_ask,
            "matched_counterparty_ids": matched_cp_ids,
            "matched_counterparty_names": [],
            "close_basis": basis,
        })

    # REPLYCLOSE — ONE reply, ONE delivery. A single "here you go" cannot be the
    # evidence for two different deliverables at once, so when R1 puts a second
    # auto-grade row on the same message every reply-evidence row steps down to
    # a confirm. This is FS-11's own ruling ("only multi-candidate AMBIGUITY
    # stays a confirm proposal") applied to the new basis, and it is what makes
    # R1 safe without a title floor: a thread carrying three open items owed by
    # the same person produces three questions, not three closures. The title
    # path is never touched by it — a >= 0.55 match with completion language
    # keeps closing exactly as it did.
    _reply_rows = [r for r in results if r["close_basis"] == REPLY_BASIS]
    if _reply_rows and sum(
            1 for r in results if r["recommendation"] == "auto_resolve") > 1:
        for r in _reply_rows:
            if r["recommendation"] == "auto_resolve":
                r["recommendation"] = "pending_review"
                r["close_basis"] = AMBIGUOUS_REPLY_BASIS

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# -----------------------------------------------------------------------------
# Path 5 — match a calendar event to open scheduling commitments
# -----------------------------------------------------------------------------


def _commitment_counterparties(ev: dict, user_person_id: str) -> set[str]:
    """All non-user person_ids attached to a commitment — the people the
    commitment is *with*. Pulls from `person_ids` (root + nested) and the
    `requester_id` alias, drops the owner/user. Used by Path 5 to decide
    whether a calendar event's attendees match a commitment's counter-party.
    """
    ids: set[str] = set()
    for src in (ev.get("person_ids"), (ev.get("data") or {}).get("person_ids")):
        if isinstance(src, list):
            ids.update(x for x in src if x)
    req = _commitment_field(ev, "requester_id")
    if req:
        ids.add(req)
    # MC1: the explicit counterparty roster (legacy single + counterparty_ids
    # list, unioned by the one helper) — so "send the deck to the board" sees
    # every board member, not just the first.
    from commitment_parties import counterparty_ids as _cp_ids
    ids.update(_cp_ids(ev))
    ids.discard(user_person_id)
    return ids


def match_calendar_to_commitments(
    *,
    open_commitments: list[dict],
    user_person_id: str,
    calendar_events: Iterable[dict],
    workspace_root=None,
) -> list[dict]:
    """Path 5 — resolve scheduling commitments OWED BY THE USER when a calendar
    event with the counter-party now exists.

    This is the calendar twin of Path 1 (`match_send_to_commitments`). On Path 1
    the act of *sending* fulfills the commitment; here the existence of a
    calendar *event* with the named person fulfills it. The user is the owner of
    these commitments ("set up the call with X") so we pre-filter to
    `owner_id == user_person_id`.

    `calendar_events` is an iterable of dicts the CALLER builds from Calendar MCP
    (the helper does no I/O). Recognized keys per event:
      - `attendee_person_ids` (list[str], REQUIRED): person_ids on the event,
        resolved from attendee emails via aliases.json/entities.json. The user's
        own id may be present; it's ignored for matching.
      - `summary` (str, optional): event title, scored against the commitment
        title only to break ties / feed pending_review evidence.
      - `created_ts` (str ISO, optional): when the event was created/updated. If
        present AND it clearly predates the commitment, the event can't have
        fulfilled it → skipped. Absent → treated as eligible (lenient).
      - `accepted_by` (list[str], optional): person_ids who have `accepted` the
        invite. Pure bonus confidence; never required.
      - `calendar_event_id` (str, optional): echoed back as evidence.

    Precision is structural, not threshold-based: a commitment auto-resolves only
    when ALL of these hold — (1) the user owns it, (2) its title carries
    scheduling intent (`detect_scheduling_intent`), (3) a calendar event exists
    whose attendees include one of the commitment's counter-parties, (4) the
    event doesn't predate the commitment. This combination is high-precision;
    we do NOT also require title↔summary overlap, because a scheduling
    commitment's title ("set up the build call with Bo") shares almost
    no tokens with an event summary ("Bo Sample / Sam Sample") — the
    *attendee* IS the topic match.

    Recommendation per (commitment, best-matching event):
      - scheduling-intent title + attendee match + not-predating
        → "auto_resolve"
      - NO scheduling intent, but attendee match AND title↔summary overlap
        >= HIGH → "pending_review" (topic lines up but it isn't obviously a
        scheduling commitment — let a human confirm rather than silently close
        a deliverable)
      - else → "no_action"

    Counter-party acceptance promotes nothing on its own — it only raises the
    `counterparty_accepted` flag and the recorded score — because an unaccepted
    invite the user sent is already fulfillment of "set up the call" (the user
    did their part). This is what subsumes the discarded "counter-party accepted
    my invite" inbox signal: acceptance is captured here as confirmation, not in
    the inbox as noise.

    Returns a list of `{commitment_id, score, recommendation, title, owner_id,
    primary_thread_id, evidence, calendar_event_id, has_scheduling_intent,
    counterparty_accepted}` dicts, one per matched commitment (best event per
    commitment), sorted by score descending. `owner_id` is the user — the caller
    uses it as `resolved_by` when writing `commitment_resolved`.
    """
    if not user_person_id:
        return []

    events = [e for e in calendar_events if isinstance(e, dict)]
    if not events:
        return []

    results: list[dict] = []
    for ev in cru_eligible(open_commitments):  # Stage D: tasks never enter CRU
        if _commitment_field(ev, "owner_id") != user_person_id:
            continue
        counterparties = _commitment_counterparties(ev, user_person_id)
        if not counterparties:
            continue
        title = _commitment_field(ev, "title") or ""
        has_sched = detect_scheduling_intent(title)
        commit_ts = event_time(ev)

        best: Optional[dict] = None
        best_score = -1.0
        for cev in events:
            attendees = {a for a in (cev.get("attendee_person_ids") or []) if a}
            attendees.discard(user_person_id)
            if not (attendees & counterparties):
                continue
            created_ts = cev.get("created_ts") or ""
            # If the event clearly predates the commitment it can't have
            # fulfilled it. Compare instants (not raw strings — ts may mix
            # Z / +00:00 offsets); only skip when BOTH parse and are ordered
            # that way, lenient on missing/unparseable data.
            _c_dt, _e_dt = _parse_ts(commit_ts), _parse_ts(created_ts)
            if _c_dt and _e_dt and _e_dt < _c_dt:
                continue
            summary = cev.get("summary") or ""
            accepted = bool(
                {a for a in (cev.get("accepted_by") or []) if a} & counterparties
            )
            overlap = score_match(summary, title)
            # Acceptance + title overlap only break ties between candidate
            # events; eligibility is the attendee match itself.
            cand_score = max(overlap, 0.6 if accepted else 0.0)
            if cand_score > best_score:
                best_score = cand_score
                best = {
                    "summary": summary,
                    "accepted": accepted,
                    "overlap": overlap,
                    "calendar_event_id": cev.get("calendar_event_id") or "",
                    "matched_cp": sorted(attendees & counterparties),
                }

        if best is None:
            continue

        if has_sched:
            recommendation = "auto_resolve"
            # Report a high score for auto-resolves so they sort first; nudge
            # higher when the counter-party has accepted.
            score = 0.9 if best["accepted"] else 0.8
        elif best["overlap"] >= _match_thresholds(workspace_root)[0]:
            recommendation = "pending_review"
            score = best["overlap"]
        else:
            recommendation = "no_action"
            score = best["overlap"]

        if recommendation == "auto_resolve" and _is_pending_review(ev):
            recommendation = "pending_review"

        # MC1: a calendar event with ONE counterparty of a multi-counterparty
        # commitment fulfills only that person's leg — downgrade the whole
        # close to a per-person receipt (matched attendees only). Single-
        # counterparty items unchanged.
        # F-28: `workspace_root` collapses one person written as an id AND that
        # person's name into ONE counterparty — see the Path 1 twin.
        from commitment_parties import has_multiple_counterparties as _multi_cp
        matched_cp_ids: list = []
        if recommendation == "auto_resolve" and _multi_cp(
                ev, workspace_root=workspace_root):
            matched_cp_ids = list(best.get("matched_cp") or [])
            recommendation = "partial_received"
        # SUB1 D3: never auto-close a parent with open sub-items — propose.
        if recommendation == "auto_resolve" and parent_blocks_auto_resolve(ev):
            recommendation = "pending_review"

        evidence = (
            f"Calendar event "
            f"{'(accepted) ' if best['accepted'] else ''}"
            f"with counter-party: {best['summary'] or best['calendar_event_id'] or 'scheduled'}"
        )
        results.append({
            "commitment_id": _commitment_id(ev),
            "score": score,
            "recommendation": recommendation,
            "title": title,
            "owner_id": user_person_id,
            "primary_thread_id": ev.get("primary_thread_id") or "",
            "evidence": evidence,
            "calendar_event_id": best["calendar_event_id"],
            "has_scheduling_intent": has_sched,
            "counterparty_accepted": best["accepted"],
            "matched_counterparty_ids": matched_cp_ids,
            "matched_counterparty_names": [],
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# -----------------------------------------------------------------------------
# Event-builder helpers (do NOT write — caller chooses to write or queue)
# -----------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def build_commitment_resolved_event(
    *,
    commitment_id: str,
    resolved_by: str,
    primary_thread_id: str,
    source_skill: str,
    evidence: str,
    next_seq: int,
) -> dict:
    """LEGACY shape helper (pre-Stage-B). Build a `commitment_resolved` event
    dict per shared/COMMITMENT_SCHEMA.md.

    Phase 2 Stage B (F2): closures are WRITTEN through
    `commitment_state.close_commitment` — the single closure path (legacy-id
    normalization, loud no-match refusal, full-set idempotency, pending_review
    floor, gated append). Do not build-and-append with this helper in new
    code; it remains only for shape reference and pre-Stage-B callers/tests.
    """
    return {
        "seq": next_seq,
        "ts": _now_iso(),
        "type": "commitment_resolved",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": {
            "commitment_id": commitment_id,
            "resolved_by": resolved_by,
            "evidence": evidence[:200] if evidence else "",
        },
    }


def build_commitment_updated_event(
    *,
    commitment_id: str,
    primary_thread_id: str,
    source_skill: str,
    change_summary: str,
    evidence: str,
    next_seq: int,
) -> dict:
    """Build a `commitment_updated` event for schedule-shift cases. The
    underlying commitment stays OPEN; this event records that the deadline
    or scope changed.
    """
    return {
        "seq": next_seq,
        "ts": _now_iso(),
        "type": "commitment_updated",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": {
            "commitment_id": commitment_id,
            "change_summary": change_summary[:200] if change_summary else "",
            "evidence": evidence[:200] if evidence else "",
        },
    }


def build_pending_review_event(
    *,
    commitment_id: str,
    primary_thread_id: str,
    source_skill: str,
    proposed_resolution: str,
    score: float,
    evidence: str,
    next_seq: int,
    title: str = "",
    has_completion_signal: Optional[bool] = None,
    evidence_ts: Optional[str] = None,
) -> dict:
    """Build a `commitment_review_proposed` event — the next Pulse fire
    surfaces these as one-click `confirm / skip` items. Used for MEDIUM-
    confidence matches where auto-resolve is too aggressive.

    `title` (FB-19) is the commitment's own name, carried so the card row can
    say WHAT it is asking about. Without it the row renders as a bare shape
    label ("Housekeeping") — the live 2026-07-16 defect.

    `has_completion_signal` (WATCHGATE R-2) is the matcher's own fulfillment
    flag — the SAME boolean the rails compute and then, until now, threw
    away. The accept surface needs it: without it the only thing standing
    between a bare guess and a bulk-confirm is whether the evidence string
    happens to contain the words "title match", which is a fragile place for
    a safety property to live. Carrying the flag makes the weakness screen
    read the matcher's actual finding instead of inferring it from prose.
    `None` = the caller did not assess (legacy rows, and any caller that has
    no signal to report); it never weakens a row on its own.

    `evidence_ts` (WATCHGATE §2.5) is WHEN the evidence was observed — the
    meeting's own time on the transcript rail, the send time on the mail rail
    — so the accept surface can check at apply time that the evidence does
    not predate the promise. Optional for the same reason.

    Both are OMITTED from `data` when None, so a caller that passes neither
    writes the byte-identical event it wrote before.
    """
    data = {
        "commitment_id": commitment_id,
        "proposed_resolution": proposed_resolution,
        "match_score": round(score, 3),
        "evidence": evidence[:200] if evidence else "",
        "title": title or "",
    }
    if has_completion_signal is not None:
        data["has_completion_signal"] = bool(has_completion_signal)
    if evidence_ts:
        data["evidence_ts"] = str(evidence_ts)
    return {
        "seq": next_seq,
        "ts": _now_iso(),
        "type": "commitment_review_proposed",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": data,
    }


def build_commitment_review_dismissed_event(
    *,
    commitment_id: str,
    primary_thread_id: str,
    source_skill: str,
    next_seq: int,
) -> dict:
    """Build a `commitment_review_dismissed` event (v2.14.7+). Written when
    the user clicks Skip on a CRU review item — the underlying commitment
    stays open, but this specific review-proposed event is closed and won't
    re-surface for 30 days.
    """
    return {
        "seq": next_seq,
        "ts": _now_iso(),
        "type": "commitment_review_dismissed",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": {
            "commitment_id": commitment_id,
        },
    }


# -----------------------------------------------------------------------------
# Open-review-proposal loader (v2.14.7+) — used by Pulse Phase 4g
# -----------------------------------------------------------------------------


def load_open_review_proposals(
    events_jsonl_path: str | Path,
    *,
    window_days: int = 7,
    now_iso: Optional[str] = None,
) -> list[dict]:
    """Read events.jsonl and return all `commitment_review_proposed` events from
    the last `window_days` days that have NOT been closed by a subsequent
    `commitment_resolved` (HIGH path subsequently fired) or
    `commitment_review_dismissed` (user already skipped) event for the same
    commitment_id. Also filters out reviews whose underlying commitment has
    been resolved by any other path (`thread_resolved` etc.).

    Returns full event dicts sorted by ts descending (newest first). Caller
    extracts `data.commitment_id`, `data.match_score`, etc. as needed.
    """
    path = Path(events_jsonl_path)
    if not path.exists():
        return []

    # `now_iso` (WATCHGATE) — the window's anchor, injectable. It defaulted to
    # the wall clock with no override, which made this reader untestable at a
    # fixed clock: a fixture anchored anywhere but "the last seven real days"
    # returned nothing, and a suite written against it read as a clean pass on
    # an empty set. That is the falsely-clean shape the sweep-methodology
    # lesson is about, arriving in a reader. Default is unchanged behavior.
    _anchor = None
    if now_iso:
        _anchor = _parse_ts(now_iso)
    if _anchor is None:
        _anchor = datetime.datetime.now(datetime.timezone.utc)
    cutoff_iso = (
        _anchor.replace(tzinfo=None) - datetime.timedelta(days=window_days)
    ).isoformat() + "Z"

    review_proposed: list[dict] = []
    closed_commitment_ids: set[str] = set()
    review_closed_for_commitment: set[str] = set()

    # EVGUARD — the hand-rolled loop that used to live here caught only
    # JSONDecodeError, so a top-level bare-string line (`"seq"`) parsed fine
    # and the next `ev.get()` raised AttributeError, taking the whole
    # staff-meeting surface down with it (Sub-bug #14b, second half). The
    # canonical loader handles BOTH shapes. `since_ts=None` = full history.
    events, _skipped = load_events_defensively(path, since_ts=None)
    for ev in events:
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et == "commitment_review_proposed":
            if event_time(ev) >= cutoff_iso:
                review_proposed.append(ev)
        elif et in ("commitment_resolved", "thread_resolved", "commitment_superseded"):
            cid = (
                d.get("commitment_id")
                or d.get("thread_id")
                or d.get("id")
                or ev.get("commitment_id")
                or ev.get("id")
            )
            if cid:
                closed_commitment_ids.add(cid)
        elif et == "commitment_review_dismissed":
            cid = d.get("commitment_id") or ev.get("commitment_id")
            if cid:
                review_closed_for_commitment.add(cid)

    out: list[dict] = []
    for ev in review_proposed:
        cid = (ev.get("data") or {}).get("commitment_id")
        if not cid:
            continue
        if cid in closed_commitment_ids:
            # Underlying commitment already resolved by another path — review is moot
            continue
        if cid in review_closed_for_commitment:
            # User already skipped this review
            continue
        out.append(ev)

    out.sort(key=lambda e: event_time(e), reverse=True)
    return out


def open_review_proposal_ids(
    events_jsonl_path: str | Path,
    *,
    window_days: int = 7,
) -> set:
    """The commitment_ids that ALREADY carry an open review proposal — the
    disk-side seed for `filter_duplicate_review_targets` (AUTOAPPLY §6).

    Thin projection over `load_open_review_proposals` so the two can never
    fork on what "open" means (resolved / dismissed / out-of-window all drop
    in one place)."""
    return {
        (ev.get("data") or {}).get("commitment_id")
        for ev in load_open_review_proposals(
            events_jsonl_path, window_days=window_days)
        if (ev.get("data") or {}).get("commitment_id")
    }


def filter_duplicate_review_targets(results: list, *, already_proposed: set) -> list:
    """ONE open review proposal per commitment (AUTOAPPLY §6 fold-in).

    The reference substrate shows the same commitment proposed TWICE inside a
    single fire — scores 1.0 and 0.571 — because two transcripts in one batch
    each matched it and nothing checked. Two rows asking the identical
    question is precisely the "duplicates asking to confirm" complaint.

    `already_proposed` is MUTATED as targets are accepted, so one set threaded
    across a fire's transcripts dedups within the fire; seed it from
    `open_review_proposal_ids()` and the same set also dedups against disk.
    Order is preserved and the highest-scoring row wins per commitment when
    the caller passes score-sorted results (every match_* path does).
    """
    out: list = []
    for r in results or []:
        cid = (r or {}).get("commitment_id")
        if not cid or cid in already_proposed:
            continue
        already_proposed.add(cid)
        out.append(r)
    return out


__all__ = [
    "HIGH_CONFIDENCE_THRESHOLD",
    "PENDING_REVIEW_THRESHOLD",
    "score_match",
    "extract_snippet",
    "detect_completion_signal",
    "detect_schedule_shift_signal",
    "detect_new_ask_signal",
    "detect_scheduling_intent",
    "load_open_commitments",
    "split_pending_review",
    "load_needs_review",
    "load_open_review_proposals",
    "open_review_proposal_ids",
    "filter_duplicate_review_targets",
    "commitment_source_refs",
    "commitment_matches_source_ref",
    "commitment_thread_refs",
    "commitment_matches_thread_ref",
    "DELIVERY_BASIS",
    "THREAD_BASIS",
    "AMBIGUOUS_DELIVERY_BASIS",
    "REPLY_BASIS",
    "REPLY_PROPOSED_BASIS",
    "AMBIGUOUS_REPLY_BASIS",
    "AUTO_CLOSE_EVIDENCE_BASES",
    "closes_on_evidence",
    "detect_deliverable_artifact",
    "commitment_is_waiting_on",
    "partition_subitems",
    "parent_blocks_auto_resolve",
    "cru_eligible",
    "match_send_to_commitments",
    "match_transcript_to_commitments",
    "match_inbound_to_commitments",
    "match_calendar_to_commitments",
    "build_commitment_resolved_event",
    "build_commitment_updated_event",
    "build_pending_review_event",
    "build_commitment_review_dismissed_event",
    "_commitment_field",
    "_commitment_confidence",
    "event_references_person",
]
