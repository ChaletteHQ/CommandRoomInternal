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
  `supersedes` link.

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

HIGH_CONFIDENCE_THRESHOLD = MATCH_SCORE_AUTO_RESOLVE   # 0.55
PENDING_REVIEW_THRESHOLD = MATCH_SCORE_PENDING_REVIEW  # 0.30


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
    return [w for w in text.split() if len(w) >= 2 and w not in STOPWORDS]


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
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in COMPLETION_PHRASES)


def detect_schedule_shift_signal(text: Optional[str]) -> bool:
    """True if `text` contains a phrase suggesting the deadline moved
    (`pushed to`, `delayed`, etc.) — used to write `commitment_updated`
    instead of `commitment_resolved`.
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in SCHEDULE_SHIFT_PHRASES)


def detect_new_ask_signal(text: Optional[str]) -> bool:
    """True if `text` contains a phrase suggesting a NEW request layered on
    top (`can you also`, `additionally please`) — used to spawn a new
    commitment with `supersedes` link rather than resolve the existing one.
    """
    if not text:
        return False
    lo = text.lower()
    return any(phrase in lo for phrase in NEW_ASK_PHRASES)


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
    return any(phrase in lo for phrase in SCHEDULING_PHRASES)


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
    """Parse an ISO-8601 timestamp to an aware datetime for instant
    comparison, tolerating both `...Z` and `...+00:00` offsets (the auto-stamp
    emits +00:00 while the build_* helpers emit Z). Returns None when the value
    is missing or unparseable (deep-audit 2026-05-29, finding #17)."""
    if not value or not isinstance(value, str):
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
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
    if not path.exists():
        return [], []

    events: list[dict] = []
    skipped: list[dict] = []

    with open(path, "r", encoding="utf-8") as f:
        for i, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError as e:
                skipped.append(
                    {
                        "line": i,
                        "reason": f"JSONDecodeError: {e.msg}",
                        "value": line[:80],
                    }
                )
                continue
            if not isinstance(ev, dict):
                skipped.append(
                    {
                        "line": i,
                        "reason": "non-dict",
                        "value": repr(ev)[:80],
                    }
                )
                continue
            events.append(ev)
    return events, skipped


def load_open_commitments(events_jsonl_path: str | Path) -> list[dict]:
    """Read events.jsonl and return all open commitments (status open or
    overdue) that have NOT been closed by a subsequent `commitment_resolved`
    or `thread_resolved` event.

    Mirror of build_workspace_map_input.py `_aggregate_commitments`. Returns
    full event dicts (caller pulls what it needs from `data`).

    v3.13.8+ — delegates to `load_events_defensively()` which surfaces
    skipped-line counts to callers via the helper return rather than
    silently swallowing malformed lines (Sub-bug #14b real-world fix).
    The skipped list is currently consumed via logging only here; new
    callers should call `load_events_defensively()` directly when they
    need to render a recovery prompt to the user.
    """
    path = Path(events_jsonl_path)
    if not path.exists():
        return []

    events, skipped = load_events_defensively(path)
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

    open_evs: list[dict] = []
    resolved_ids: set[str] = set()

    for ev in events:
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
                resolved_ids.add(cid)
        elif et == "commitment":
            status = _commitment_field(ev, "status") or "open"
            if status in ("open", "overdue"):
                open_evs.append(ev)

    return [c for c in open_evs if _commitment_id(c) not in resolved_ids]


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
) -> list[dict]:
    """Path 1 — score an outbound send against open commitments.

    Filters open_commitments to those where:
      - `data.owner_id` == sender_person_id (the user owes this)
      - the recipient is involved — EITHER a resolved recipient person_id is in
        the commitment's `person_ids`, OR a recipient name token appears in the
        commitment title (the recall fallback, Bug #103).

    Then scores subject+body against each candidate's title.

    `recipient_names` (Bug #103 — real-data recall fix): display names and/or
    email local-parts of the recipients (e.g. "Sam" plus the local-part "sam"
    taken from an address like sam@<domain>; or "Jordan Lee" plus "jlee"). Commitment extraction frequently fails to LINK the
    counterparty into `person_ids` (some commitments were stored with only the
    user) or the counterparty person has no email on file, so the resolved-
    person_id gate alone misses real completions. Titles almost always name the
    recipient ("Send Sam a recap", "Send Jordan Lee a product summary"), so a
    recipient-name token appearing in the title opens the candidacy gate. The
    score_match threshold still decides the recommendation, which keeps false
    positives down — a name in the title only makes the commitment a candidate;
    the subject/body must still overlap it.

    Returns a list of `{commitment_id, score, recommendation, title}` dicts,
    sorted by score descending. recommendation is one of:
      - "auto_resolve" (score >= HIGH_CONFIDENCE_THRESHOLD)
      - "pending_review" (PENDING_REVIEW_THRESHOLD <= score < HIGH)
      - "no_action" (filtered out — caller can ignore these but they're
        included for diagnostic logging)
    """
    if not sender_person_id:
        return []
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

    for ev in open_commitments:
        if _commitment_field(ev, "owner_id") != sender_person_id:
            continue
        _d = ev.get("data") or {}
        person_ids = set(ev.get("person_ids") or []) | set(_d.get("person_ids") or [])
        title = _commitment_field(ev, "title") or ""
        # Recipient is involved if a resolved id is in person_ids OR a recipient
        # name token appears in the title (the #103 recall fallback).
        recipient_in_pids = bool(recipient_set & person_ids)
        recipient_in_title = bool(recipient_name_tokens & set(_tokenize(title)))
        if not (recipient_in_pids or recipient_in_title):
            continue
        score = score_match(query, title)
        if score >= HIGH_CONFIDENCE_THRESHOLD:
            rec = "auto_resolve"
        elif score >= PENDING_REVIEW_THRESHOLD:
            rec = "pending_review"
        else:
            rec = "no_action"
        if rec == "auto_resolve" and _is_pending_review(ev):
            rec = "pending_review"
        results.append({
            "commitment_id": _commitment_id(ev),
            "score": score,
            "recommendation": rec,
            "title": title,
            "owner_id": sender_person_id,
            "primary_thread_id": ev.get("primary_thread_id") or "",
        })

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
) -> list[dict]:
    """Path 3 — score a meeting transcript against open commitments where any
    meeting attendee is the owner.

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

    has_completion = detect_completion_signal(transcript_text)
    has_schedule_shift = detect_schedule_shift_signal(transcript_text)
    has_new_ask = detect_new_ask_signal(transcript_text)

    results: list[dict] = []
    for ev in open_commitments:
        owner_id = _commitment_field(ev, "owner_id") or ""
        if owner_id not in attendee_set:
            continue
        title = _commitment_field(ev, "title") or ""
        score = score_match(transcript_text, title)

        if score < PENDING_REVIEW_THRESHOLD:
            recommendation = "no_action"
        elif score >= HIGH_CONFIDENCE_THRESHOLD:
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
# Path 4 — match an inbound email to open commitments
# -----------------------------------------------------------------------------


def match_inbound_to_commitments(
    *,
    open_commitments: list[dict],
    sender_person_id: str,
    subject: Optional[str],
    body: Optional[str],
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

    Returns list of `{commitment_id, score, recommendation, title, owner_id,
    primary_thread_id, has_completion_signal, has_schedule_shift_signal,
    has_new_ask_signal}` dicts sorted by score descending. `owner_id` is the
    sender (the counter-party) — the caller uses it as `resolved_by` when
    writing the `commitment_resolved` event.
    """
    if not sender_person_id:
        return []

    query = (subject or "") + " " + (body or "")
    if not query.strip():
        return []

    has_completion = detect_completion_signal(query)
    has_schedule_shift = detect_schedule_shift_signal(query)
    has_new_ask = detect_new_ask_signal(query)

    results: list[dict] = []
    for ev in open_commitments:
        owner_id = _commitment_field(ev, "owner_id") or ""
        if owner_id != sender_person_id:
            continue
        title = _commitment_field(ev, "title") or ""
        score = score_match(query, title)

        if score < PENDING_REVIEW_THRESHOLD:
            recommendation = "no_action"
        elif score >= HIGH_CONFIDENCE_THRESHOLD:
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

        if recommendation == "auto_resolve" and _is_pending_review(ev):
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
    ids.discard(user_person_id)
    return ids


def match_calendar_to_commitments(
    *,
    open_commitments: list[dict],
    user_person_id: str,
    calendar_events: Iterable[dict],
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
    for ev in open_commitments:
        if _commitment_field(ev, "owner_id") != user_person_id:
            continue
        counterparties = _commitment_counterparties(ev, user_person_id)
        if not counterparties:
            continue
        title = _commitment_field(ev, "title") or ""
        has_sched = detect_scheduling_intent(title)
        commit_ts = ev.get("ts") or ""

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
                }

        if best is None:
            continue

        if has_sched:
            recommendation = "auto_resolve"
            # Report a high score for auto-resolves so they sort first; nudge
            # higher when the counter-party has accepted.
            score = 0.9 if best["accepted"] else 0.8
        elif best["overlap"] >= HIGH_CONFIDENCE_THRESHOLD:
            recommendation = "pending_review"
            score = best["overlap"]
        else:
            recommendation = "no_action"
            score = best["overlap"]

        if recommendation == "auto_resolve" and _is_pending_review(ev):
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
    """Build a `commitment_resolved` event dict per shared/COMMITMENT_SCHEMA.md.
    Caller is responsible for atomic_append_jsonl-ing it.
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
) -> dict:
    """Build a `commitment_review_proposed` event — the next Pulse fire
    surfaces these as one-click `confirm / skip` items. Used for MEDIUM-
    confidence matches where auto-resolve is too aggressive.
    """
    return {
        "seq": next_seq,
        "ts": _now_iso(),
        "type": "commitment_review_proposed",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": {
            "commitment_id": commitment_id,
            "proposed_resolution": proposed_resolution,
            "match_score": round(score, 3),
            "evidence": evidence[:200] if evidence else "",
        },
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

    cutoff_iso = (
        datetime.datetime.utcnow() - datetime.timedelta(days=window_days)
    ).isoformat() + "Z"

    review_proposed: list[dict] = []
    closed_commitment_ids: set[str] = set()
    review_closed_for_commitment: set[str] = set()

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
            if et == "commitment_review_proposed":
                if (ev.get("ts") or "") >= cutoff_iso:
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

    out.sort(key=lambda e: e.get("ts") or "", reverse=True)
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
    "load_open_review_proposals",
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
