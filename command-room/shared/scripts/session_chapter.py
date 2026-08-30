#!/usr/bin/env python3
"""
session_chapter — the mid-session work-boundary marker (SPEC SESSSTORY1, §0
Ruling 1, 2026-08-27).

THE PROBLEM THIS CLOSES
------------------------
Facts (commitments, decisions, interactions, notes) are already end-session
proof — 95.3% of them write live, and the nightly sweep (session_sweep.py)
catches the rest. The NARRATIVE — the session-notes block, the human-readable
"here's what happened" — writes only inside the "end session" ritual, which a
client will rarely if ever say. SESSSTORY1 closes that gap by letting
session-sweep COMPOSE the narrative overnight instead of requiring it live.

This module is the ONE thing that happens mid-chat: an OPTIONAL, tiny,
append-only marker at a natural work boundary (a deliverable produced, a
decision logged, a topic closed). It is:

  - ONE LINE of text. A chapter marker is a pointer into a moment, not a
    paragraph — embedded newlines are rejected outright (fail loud, per this
    codebase's "silent success is the disease" doctrine). Multi-line content
    belongs in the FACT events (commitment/decision/interaction/note), which
    already capture it; a chapter is a breadcrumb, not a second copy.
  - ADVISORY, NOT MANDATORY (Ruling 1). A session that writes zero chapters is
    not a defect — session-sweep's narrative leg (session_narrative.py) falls
    back to its own transcript summarization at sweep time, exactly as it
    already does for the commitment/decision/interaction/note families.
  - THE ONLY SUBSTRATE TOUCHED FOR THIS LANE DURING A SESSION. No .md write
    happens here or from here — never a mid-chat Drive-synced markdown edit
    (§0 rationale: sync churn + the bundling rule). The composed session-notes
    block itself is written later: overnight by the sweep, or on-the-spot by
    "end session" (workspace-manager, see session_narrative.py).

Written through the ONE gated append path (event_gate.append_event) like
every other event in this codebase — no second writer, no hand-rolled append.
Registered: shared/data-schemas/events.schema.json + shared/EVENT_TYPES.md
"Session chapter lane".
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_gate import append_event  # noqa: E402

EVENT_TYPE = "session_chapter"


class SessionChapterError(ValueError):
    """A chapter marker was malformed — fail loud, never silently trimmed or
    silently dropped (this codebase's standing doctrine: a defect that is
    silently "fixed" at the call site is a defect nobody ever sees again)."""


def chapter_source_ref(session_id: str) -> str:
    """Provenance ref for a chapter marker: `session:{session_id}` — the SAME
    spelling session_sweep.session_source_ref uses, so the narrative leg's
    per-session grouping (session_narrative.py) and any future reader can join
    chapters to a session by one string, not two."""
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required to log a chapter marker")
    return f"session:{sid}"


def log_chapter(
    workspace_root,
    session_id: str,
    text: str,
    *,
    source_skill: str = "workspace-manager",
    primary_thread_id: Optional[str] = None,
    related_thread_ids: Optional[List[str]] = None,
) -> List[dict]:
    """Append ONE `session_chapter` event. Returns the stamped event(s) from
    append_event (seq/ts allocated inside the writer lock).

    Raises SessionChapterError when `text` is empty or carries more than one
    line — a chapter marker degrading into free-form prose is exactly the
    mid-chat narrative write SESSSTORY1 §0 rules out.
    """
    if not isinstance(text, str):
        raise SessionChapterError(
            f"session_chapter text must be a string, got {type(text).__name__}"
        )
    stripped = text.strip()
    if not stripped:
        raise SessionChapterError("session_chapter text is empty")
    if "\n" in stripped or "\r" in stripped:
        raise SessionChapterError(
            "session_chapter text must be ONE line — a chapter marker is a "
            "pointer into a moment, not a paragraph. Embedded newlines are "
            "the raw-transcript-leak shape this lane exists to avoid; log "
            "the full detail through the ordinary commitment/decision/"
            "interaction/note capture instead."
        )

    source_ref = chapter_source_ref(session_id)
    data: dict[str, Any] = {
        "session_id": session_id,
        "source_ref": source_ref,
        "text": stripped,
    }

    event: dict[str, Any] = {
        "type": EVENT_TYPE,
        "source_skill": source_skill,
        "data": data,
    }
    if primary_thread_id:
        event["primary_thread_id"] = primary_thread_id
    if related_thread_ids:
        event["related_thread_ids"] = list(related_thread_ids)

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    return append_event(events_path, event, holder=source_skill)


def load_chapters(workspace_root, session_id: str) -> List[dict]:
    """Every `session_chapter` event for one session, in append (seq) order.
    Read-only convenience for the narrative leg and for tests; not on any
    hot path (session-sweep windows are small — a handful of sessions, a
    handful of chapters each)."""
    try:
        from event_refs import load_events
    except ImportError:
        sys.path.insert(0, str(_HERE))
        from event_refs import load_events

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    out = []
    for ev in load_events(events_path):
        # Literal (not the EVENT_TYPE constant) so run_event_contract_test's
        # code-shaped reader detector sees this as a real read of
        # "session_chapter", matching the write it already sees in
        # log_chapter()'s docstring.
        if ev.get("type") != "session_chapter":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("session_id") == session_id:
            out.append(ev)
    return out


def chapter_lines_for(workspace_root, session_id: str) -> List[str]:
    """The verbatim one-line texts of every chapter marker for `session_id`,
    in append order — exactly the "material it already reads" the narrative
    leg composes from (SESSSTORY1 scope fence)."""
    return [
        (ev.get("data") or {}).get("text", "")
        for ev in load_chapters(workspace_root, session_id)
        if (ev.get("data") or {}).get("text")
    ]


__all__ = [
    "EVENT_TYPE",
    "SessionChapterError",
    "chapter_source_ref",
    "log_chapter",
    "load_chapters",
    "chapter_lines_for",
]
