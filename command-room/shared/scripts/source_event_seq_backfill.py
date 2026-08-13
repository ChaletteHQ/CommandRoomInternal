#!/usr/bin/env python3
"""
v3.13.8.1 — Bug #71 — legacy wrapper source_event_seq backfill migration.

THE BUG THIS FIXES
==================

v3.13.8 §2.9 (Bug #51) introduced cascade-close — when a user marks a
`commitment_to_discuss` wrapper as Resolved via show-my-list + Apply, the
canonical cascade-close path also closes the underlying commitment by
following the wrapper's `data.source_event_seq` to the source commitment.

But this only works for wrappers WRITTEN with that field. Wrappers created
before v3.13.8 (specifically before §2.9 landed) carry no `data` block at all
— they're legacy captures from a time when show-my-list wrappers weren't
linked back to their source commitment. v3.13.8's cascade-close cannot reach
those wrappers because the link doesn't exist.

A9 verification surface (2026-05-24): M closed two legacy wrappers (Aspen +
Granola). Both fired clean wrapper-close events. Neither cascaded. The Aspen
wrapper was a scope_decision wrapper (no underlying work item — false alarm).
The Granola wrapper had an open investigation commitment that should have
cascade-closed; it didn't, because the wrapper had no `data.source_event_seq`.

WHAT THIS MIGRATION DOES
========================

One-time idempotent backfill: scan events.jsonl for `commitment_to_discuss`
events lacking `data.source_event_seq`. For each, try to identify the
underlying source commitment via:

  1. **Same primary_thread_id** (if wrapper has one) — limits search to
     commitments sharing the project/meeting context.
  2. **Time proximity** — commitment events created BEFORE the wrapper, in
     the same source meeting or recent window.
  3. **Text similarity** — wrapper's summary/title text vs commitment text.
     Uses a simple word-overlap Jaccard score (no external dependencies).

If a high-confidence match is found (text Jaccard ≥ 0.5 + same thread or
same source meeting), we rewrite the wrapper with `data.source_event_seq`
set to the matched commitment's seq. Otherwise the wrapper stays as-is but
gets a `data.source_event_seq_match: "needs_review"` marker so future
migrations or UI prompts can surface it.

IDEMPOTENCY
===========

After the migration completes, a `wrapper_source_seq_backfill` event is
appended with `recovery_version: v3.13.8.1` and counts. Subsequent runs
check for that event and short-circuit if present.

USAGE
=====

From `command-room-update-bridge` migration phase:

    from source_event_seq_backfill import run_backfill_if_needed
    summary = run_backfill_if_needed(workspace_root)

CLI:

    python source_event_seq_backfill.py <workspace_root>

SAFETY
======

The migration is conservative — better to leave a wrapper unlinked than
to link it to the wrong commitment and trigger a wrong cascade-close. The
Jaccard threshold (0.5) plus thread-anchoring keeps false-positive links
rare.

Holds a multi_write_context lock for the full migration so we don't race
other writers during the rewrite + event-append sequence.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from atomic_write import (  # noqa: E402
    atomic_append_jsonl,
    atomic_write_text,
    multi_write_context,
)
from cru_match import load_events_defensively  # noqa: E402
from event_time import event_time  # noqa: E402
from next_seq import next_seq  # noqa: E402
from writer_lock import events_writer_lock  # noqa: E402


RECOVERY_VERSION = "v3.13.8.1"

# Conservative Jaccard threshold for text-similarity match. Tuned to avoid
# false positives — better to leave a wrapper unlinked than to link wrongly.
JACCARD_THRESHOLD = 0.5

# Time window to search for source commitments BEFORE the wrapper's timestamp.
# Backfill assumes commitments are usually captured within 7 days of when they
# get parked into the discuss list.
SEARCH_WINDOW_DAYS = 7

# Commitment event types we'll search as source candidates. Order does not
# matter — they all carry commitment shape.
#
# NOTE (v3.14.5): "commitment" is the CANONICAL type the real writers emit
# (scan-for-commitments, meeting-notes, inbox-triage — see COMMITMENT_SCHEMA.md
# and cru_match.load_open_commitments which filters on `type == "commitment"`).
# The three `commitment_*` variants below are legacy/alternate names that no
# current writer produces. Before this line was added, the candidate pool was
# empty in every real workspace, so the backfill silently no-op'd in production
# while its unit tests (which fed the variant names) stayed green. Keep all four
# so both the canonical writer and any legacy substrate are covered.
COMMITMENT_TYPES = (
    "commitment",
    "commitment_logged",
    "commitment_pending_review",
    "commitment_captured",
)

# Stopwords to strip before Jaccard scoring. Keeps the comparison focused
# on content-bearing words.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "for", "of", "to", "in", "on", "at",
    "by", "with", "from", "as", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "should", "could", "may", "might", "can", "this", "that", "these", "those",
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "what", "which",
    "who", "when", "where", "why", "how",
}


def _events_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "events.jsonl"


def _already_ran(workspace_root: Path) -> bool:
    """Check for a prior wrapper_source_seq_backfill event with matching
    RECOVERY_VERSION. Used for idempotency."""
    events, _skipped = load_events_defensively(_events_path(workspace_root))
    for ev in events:
        if ev.get("type") == "wrapper_source_seq_backfill":
            data = ev.get("data") or {}
            if data.get("recovery_version") == RECOVERY_VERSION:
                return True
    return False


def _tokens(text: str) -> set[str]:
    """Lowercase, alphanumeric-only tokens with stopwords stripped."""
    if not text:
        return set()
    # Split on non-word, lowercase, drop empties + short tokens + stopwords
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity coefficient. 0.0 if either is empty."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return intersection / union


def _wrapper_text(ev: dict) -> str:
    """Extract the text we'll use for similarity scoring from a wrapper event.

    Wrapper events vary in shape — try data.summary, data.title, data.text,
    then fall back to a concatenation of any string values in data.
    """
    data = ev.get("data") or {}
    for key in ("summary", "title", "text", "commitment_text", "description"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v
    # Fallback: concat any string values
    return " ".join(
        v for v in data.values() if isinstance(v, str)
    )


def _commitment_text(ev: dict) -> str:
    """Extract scoring text from a candidate source commitment event."""
    data = ev.get("data") or {}
    for key in ("text", "commitment_text", "title", "summary", "description"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return " ".join(
        v for v in data.values() if isinstance(v, str)
    )


def _parse_ts(ts: str | None) -> datetime.datetime | None:
    """Parse ISO-8601 timestamp permissively. Returns None on failure."""
    if not ts or not isinstance(ts, str):
        return None
    # Common shapes: 2026-05-25T01:39:23Z / 2026-05-25T01:39:23+00:00
    try:
        cleaned = ts.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(cleaned)
    except (ValueError, AttributeError):
        return None


def _is_legacy_wrapper(ev: dict) -> bool:
    """A wrapper qualifies for backfill if:
       - type is commitment_to_discuss
       - lacks data.source_event_seq (or has it set to null)
       - NOT already marked as needs_review (so we don't reprocess on every run)
    """
    if ev.get("type") != "commitment_to_discuss":
        return False
    data = ev.get("data") or {}
    if data.get("source_event_seq") is not None:
        return False
    if data.get("source_event_seq_match") == "needs_review":
        # Already attempted; leave it for the UI prompt path
        return False
    return True


def _find_best_match(
    wrapper: dict,
    candidates: list[dict],
) -> tuple[dict | None, float]:
    """Return (best_candidate, score) or (None, 0.0) if nothing meets threshold.

    Scoring combines text similarity + thread anchoring. A perfect thread
    match without text overlap won't cascade-close (too risky); a strong
    text match in the same thread is the canonical signal.
    """
    wrapper_text = _wrapper_text(wrapper)
    wrapper_tokens = _tokens(wrapper_text)
    wrapper_thread = wrapper.get("primary_thread_id") or (
        wrapper.get("data", {}).get("source_thread_id")
        if isinstance(wrapper.get("data"), dict) else None
    )
    wrapper_ts = _parse_ts(event_time(wrapper))

    best_score = 0.0
    best_candidate: dict | None = None

    for cand in candidates:
        if cand.get("type") not in COMMITMENT_TYPES:
            continue

        # Time filter — candidate must be BEFORE the wrapper (commitment
        # captured before it landed in the discuss list)
        cand_ts = _parse_ts(event_time(cand))
        if wrapper_ts and cand_ts and cand_ts > wrapper_ts:
            continue
        # Optional window filter
        if wrapper_ts and cand_ts:
            delta = wrapper_ts - cand_ts
            if delta.days > SEARCH_WINDOW_DAYS:
                continue

        # Thread anchoring — boost score if threads match
        cand_thread = cand.get("primary_thread_id")
        thread_match = (
            wrapper_thread is not None
            and cand_thread is not None
            and wrapper_thread == cand_thread
        )

        # Text similarity
        cand_tokens = _tokens(_commitment_text(cand))
        jaccard = _jaccard(wrapper_tokens, cand_tokens)

        # Combined score: text similarity is the dominant signal; thread
        # match adds a small bonus to break ties between similar candidates.
        score = jaccard + (0.1 if thread_match else 0.0)

        if score > best_score:
            best_score = score
            best_candidate = cand

    if best_score >= JACCARD_THRESHOLD:
        return best_candidate, best_score
    return None, best_score


def run_backfill_if_needed(workspace_root: str | Path) -> dict:
    """Run the source_event_seq backfill on events.jsonl. Returns a summary:

        {
          "ran": bool,
          "skipped_reason": Optional[str],
          "wrappers_examined": int,
          "wrappers_linked": int,        # high-confidence link applied
          "wrappers_marked_needs_review": int,
          "recovery_version": str,
        }

    Idempotent. Conservative — only high-confidence matches get the link.
    """
    workspace_root = Path(workspace_root)
    events_path = _events_path(workspace_root)

    if not events_path.exists():
        return {
            "ran": False,
            "skipped_reason": "no_events_file",
            "wrappers_examined": 0,
            "wrappers_linked": 0,
            "wrappers_marked_needs_review": 0,
            "recovery_version": RECOVERY_VERSION,
        }

    if _already_ran(workspace_root):
        return {
            "ran": False,
            "skipped_reason": "already_run",
            "wrappers_examined": 0,
            "wrappers_linked": 0,
            "wrappers_marked_needs_review": 0,
            "recovery_version": RECOVERY_VERSION,
        }

    # Bug #80 (2026-05-31): the rewrite below reconstructs events.jsonl from the
    # defensively-loaded events ONLY, so any malformed lines would be silently
    # dropped — no quarantine, no corruption_recovery event, no customer message,
    # bypassing every recovery contract. Route them through the canonical recovery
    # path FIRST (recurring=True so it always heals CURRENT malformed lines, not
    # gated by recovery_version) so they are preserved in quarantine + audited,
    # then operate on the healed file. Best-effort: recovery must never block the
    # backfill, and it is a cheap no-op on an already-clean log.
    try:
        from recover_corruption import run_recovery_if_needed
        run_recovery_if_needed(
            workspace_root, source_skill="source-event-seq-backfill", recurring=True
        )
    except Exception:
        pass

    events, _skipped = load_events_defensively(events_path)
    legacy_wrappers = [(i, ev) for i, ev in enumerate(events) if _is_legacy_wrapper(ev)]

    if not legacy_wrappers:
        # Nothing to do, but still write a no-op marker so we don't re-scan
        # every update. Defer that write until inside the lock below.
        return _write_backfill_event_and_return(
            workspace_root=workspace_root,
            events=events,
            events_path=events_path,
            linked=0,
            marked=0,
            examined=0,
            skipped_reason=None,
            ran=False,
            no_op=True,
        )

    linked = 0
    marked = 0

    # BUG-8330 fix round (FX-2): `multi_write_context` holds
    # `_hq/.system/atomic.lock` — a DIFFERENT lock from the one every gated
    # append takes (`_hq/data/.writer.lock`, via `writer_lock`). It therefore
    # excludes other multi_write callers but NOT `atomic_append_jsonl`, and the
    # truncating rewrite below silently destroys any append that lands in the
    # window. Nest the events writer lock inside it (outer atomic.lock, inner
    # .writer.lock — the same order the in-block `atomic_append_jsonl` already
    # establishes, so no lock-order inversion) and re-read in there.
    with multi_write_context(workspace_root, holder="source_event_seq_backfill"), \
            events_writer_lock(events_path, holder="source_event_seq_backfill"):
        # Re-read INSIDE the lock so the rewrite below operates on the same
        # snapshot the lock protects. The pre-lock read above is only used for
        # the cheap "is there anything to do" decision; rewriting the whole file
        # from a stale snapshot would silently drop any event appended between
        # that read and this lock (deep-audit 2026-05-29, finding #8).
        events, _skipped = load_events_defensively(events_path)
        legacy_wrappers = [(i, ev) for i, ev in enumerate(events) if _is_legacy_wrapper(ev)]
        candidates = [ev for ev in events if ev.get("type") in COMMITMENT_TYPES]
        examined = len(legacy_wrappers)

        for idx, wrapper in legacy_wrappers:
            best, score = _find_best_match(wrapper, candidates)
            data = dict(wrapper.get("data") or {})

            if best is not None:
                data["source_event_seq"] = best.get("seq")
                data["source_event_seq_match"] = "high_confidence"
                data["source_event_seq_score"] = round(score, 3)
                data["source_event_seq_backfilled_by"] = RECOVERY_VERSION
                linked += 1
            else:
                data["source_event_seq_match"] = "needs_review"
                data["source_event_seq_backfill_attempted"] = RECOVERY_VERSION
                if score > 0:
                    data["source_event_seq_best_score"] = round(score, 3)
                marked += 1

            events[idx] = dict(wrapper)
            events[idx]["data"] = data

        # Rewrite events.jsonl with backfilled wrappers
        new_content = "".join(
            json.dumps(ev, ensure_ascii=False) + "\n" for ev in events
        )
        atomic_write_text(events_path, new_content)

        # Append the backfill summary event. No hand-stamped seq
        # (BUG-8330 item 7) — appender allocates in-lock.
        backfill_event = {
            "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "type": "wrapper_source_seq_backfill",
            "source_skill": "update-bridge",
            "data": {
                "wrappers_examined": examined,
                "wrappers_linked": linked,
                "wrappers_marked_needs_review": marked,
                "recovery_version": RECOVERY_VERSION,
            },
        }
        atomic_append_jsonl(events_path, [backfill_event])

    return {
        "ran": True,
        "skipped_reason": None,
        "wrappers_examined": examined,
        "wrappers_linked": linked,
        "wrappers_marked_needs_review": marked,
        "recovery_version": RECOVERY_VERSION,
    }


def _write_backfill_event_and_return(
    workspace_root: Path,
    events: list[dict],
    events_path: Path,
    linked: int,
    marked: int,
    examined: int,
    skipped_reason: str | None,
    ran: bool,
    no_op: bool,
) -> dict:
    """Helper: write the wrapper_source_seq_backfill marker event for a no-op
    fire so future runs see we've already scanned this workspace."""
    with multi_write_context(workspace_root, holder="source_event_seq_backfill"):
        # No hand-stamped seq (BUG-8330 item 7) — appender allocates in-lock.
        backfill_event = {
            "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "type": "wrapper_source_seq_backfill",
            "source_skill": "update-bridge",
            "data": {
                "wrappers_examined": examined,
                "wrappers_linked": linked,
                "wrappers_marked_needs_review": marked,
                "recovery_version": RECOVERY_VERSION,
                "no_op": no_op,
            },
        }
        atomic_append_jsonl(events_path, [backfill_event])
    return {
        "ran": ran,
        "skipped_reason": skipped_reason if not no_op else "no_legacy_wrappers",
        "wrappers_examined": examined,
        "wrappers_linked": linked,
        "wrappers_marked_needs_review": marked,
        "recovery_version": RECOVERY_VERSION,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: source_event_seq_backfill.py <workspace_root>", file=sys.stderr)
        return 2
    summary = run_backfill_if_needed(argv[1])
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
