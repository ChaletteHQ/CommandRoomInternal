#!/usr/bin/env python3
"""
thread_archive.py — THE archive path for a project/thread (SPEC ARCHFIX).

WHY THIS EXISTS
===============
"archive [project]" used to be three prose steps that edited MASTER_TRACKER.md:
move the row to the archive stage, add a Recently Archived line with a date and
a reason, ask about loose ends. Not one of them touched `entities.json`.

But MASTER_TRACKER.md is a GENERATED VIEW. `render_master_tracker.py` rebuilds
it deterministically from `entities.json` + `events.jsonl`, and its Recently
Archived section renders solely from thread records whose `status` is
`"archived"`, sorted by `archived_at`, showing `archive_reason`. So a
tracker-markdown archive was overwritten by the very next render — end-session
Step 2.5 and cleanup Phase 3.5d2 both run the renderer — and the project came
back with its old status. Measured on a real-shape fixture: after the second
render the view was BYTE-IDENTICAL to the pre-archive render. The archive did
not degrade; it vanished.

Same class as the frozen-tracker bug (v4.2.0) seen from the other side: there,
the view stopped tracking the substrate; here, a write went only to the view.

WHAT THIS MODULE GUARANTEES
===========================
  - ONE archive path FOR ARCHIVING A PROJECT AS A PROJECT. Two prose call
    sites use it — workspace-manager's "archive [project]" and apply-choices'
    stalled-projects `archive` action — and neither may hand-copy the write
    pair. A prose-only mandate is presumed skipped (the Bug #98 class); a
    python entry point is what makes the fence mutation-testable instead of
    aspirational.

    It is NOT the only code that can set `status: "archived"` on a thread, and
    claiming otherwise would be the same kind of false safety claim this
    build's A3 deleted. Two lifecycle CLOSERS also land that status as a
    side effect of closing their own object, and neither stamps `archived_at`
    or `archive_reason`:
      * `deal_state.close_deal(..., "lost")` — a lost deal's thread
        (deal_state.py, `thread_status = "resolved" if outcome == "won" else
        "archived"`). Owner: SPEC PIPE1.
      * `objective_state.archive_objective` — an archived objective's thread
        (objective_state.py `_close`). Owner: SPEC OBJ1.
    Because they stamp no `archived_at`, MASTER_TRACKER's Recently Archived
    section sorts them under the empty string and renders their date cell as
    `—`, so they sink below every properly stamped archive and fall off the
    top-10 list. That is the same symptom as the bug this module fixes, one
    object over. Out of ARCHFIX scope by its §0 ruling; do not "fix" it by
    widening this module — route those closers through it, or give them their
    own stamps, under their own spec.
  - NO hand-rolled writes. The record mutation goes through
    `thread_writer.update_thread` (ALLOWED-field check, schema validation,
    atomic locked write, `thread_updated` event) and the timeline event goes
    through `event_gate.append_event` (the one gated append path). There is no
    direct `open(...)` or entities.json edit anywhere here, and the markdown
    view is never written by hand — the renderer regenerates it.
  - Idempotent. Archiving an already-archived thread is an honest no-op:
    `status: "already_archived"`, zero events, zero record churn. A second
    `status_change` would be a second archive that never happened.
  - Record first, event second. If the append fails, the archive still landed
    where every reader looks; the reverse order would leave a `status_change`
    asserting an archive the substrate does not have. Mirrors
    `objective_state._close`.
  - The view is refreshed in the same call. A1 step 5 requires the tracker to
    reflect the archive immediately, and A2 dispatches the same path, so the
    regen belongs here rather than in two prose steps that can drift. It is
    DEFENSIVE: a renderer failure is reported in the return value and never
    unwinds or masks a substrate write that already succeeded.

WHAT IS DELIBERATELY NOT HERE
=============================
  - No entity resolution. Callers pass a thread id they resolved through
    `entity_resolve` first (the standard gate for a name-bearing trigger).
    Guessing a thread from a name inside a writer is how the wrong project
    gets archived.
  - No auto-anything. Nothing in this module decides that a thread SHOULD be
    archived — no staleness rule, no decay, no dormancy transition. Every call
    is downstream of a human gesture. (M's ruling 2026-08-01: Pulse-style
    automatic lifecycle transitions are eliminated.)
  - No un-archive. Reviving a thread is a separate lifecycle contract with its
    own questions (which status does it come back to?) and belongs to whoever
    specs it, not to a side door in the archive writer.

stdlib only.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import thread_writer  # noqa: E402
from entities_io import entities_collection  # noqa: E402

ARCHIVED_STATUS = "archived"

# A reason lands in a markdown table cell in MASTER_TRACKER's Recently Archived
# section, so an embedded newline or pipe would break the row it renders into.
# Whitespace is collapsed and the length is capped (same 300-char convention as
# objective_state's outcome_note) — capped, never silently dropped.
REASON_MAX_CHARS = 300


class ThreadArchiveError(Exception):
    """Raised when the archive cannot proceed (unknown thread id, unreadable
    substrate). Loud by design — a silent failure here reads exactly like the
    bug this module fixes."""


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _entities_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "entities.json"


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _load_entities(ws: Path) -> dict:
    try:
        return json.loads(_entities_path(ws).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThreadArchiveError(
            f"cannot read {_entities_path(ws)}: {exc}") from exc


def _threads(data: dict) -> list:
    """Live thread collection. Real data stores under `threads`; the legacy
    schema also names it `projects`. Same wrapper-aware read as thread_writer
    and objective_state — a nested-vs-flat workspace must resolve identically
    or the writer edits a record no reader sees."""
    threads = entities_collection(data, "threads")
    projects = entities_collection(data, "projects")
    if projects and not threads:
        return projects
    return threads


def _find_thread(data: dict, thread_id: str) -> Optional[dict]:
    return next((t for t in _threads(data) if t.get("id") == thread_id), None)


def normalize_reason(reason: Any) -> Optional[str]:
    """Collapse a caller's reason to one safe table-cell line, or None.

    Public because the ack copy at both call sites should print exactly what
    got stored, not the caller's raw string.
    """
    if reason is None:
        return None
    text = re.sub(r"\s+", " ", str(reason)).strip()
    if not text:
        return None
    return text[:REASON_MAX_CHARS]


def stalled_review_reason(days_since_activity: Any) -> str:
    """The default archive reason for a stalled-projects `archive` click.

    Lives here, not in the dispatcher's prose, so the reason stored on the
    record is one string with one test rather than a sentence two skills
    paraphrase differently. A missing/unusable day-count degrades to the
    provenance alone — the click still records WHERE the archive came from,
    which is the part a later reader needs.
    """
    try:
        days = int(days_since_activity)
    except (TypeError, ValueError):
        days = -1
    if days < 0:
        return "archived from stalled-projects review"
    unit = "day" if days == 1 else "days"
    return f"archived from stalled-projects review — quiet {days} {unit}"


def archive_status(workspace_root, thread_id: str) -> dict:
    """Read-only: what the archive path would find. Never writes.

    Returns `{"found": bool, "status": <current status or None>,
    "archived": bool, "archived_at": …, "archive_reason": …,
    "display_name": …}`. Used by a caller that wants to ack an already-archived
    thread without attempting a write.
    """
    ws = Path(workspace_root)
    thread = _find_thread(_load_entities(ws), thread_id)
    if thread is None:
        return {"found": False, "status": None, "archived": False,
                "archived_at": None, "archive_reason": None,
                "display_name": None}
    return {
        "found": True,
        "status": thread.get("status"),
        "archived": thread.get("status") == ARCHIVED_STATUS,
        "archived_at": thread.get("archived_at"),
        "archive_reason": thread.get("archive_reason"),
        "display_name": (thread.get("canonical_name")
                         or thread.get("display_name")),
    }


def build_status_change_event(thread_id: str, *, from_status: Optional[str],
                              reason: Optional[str],
                              source_skill: str) -> dict:
    """The canonical `status_change` envelope for an archive.

    Shape follows the live precedent (the v21 fixture's status_change payload
    and the dont-forget dispatch): the thread id rides the CANONICAL envelope
    field `primary_thread_id` (`project_id` is deprecated per
    events.schema.json) and the transition rides `data`. `seq` and `ts` are
    omitted on purpose — they are auto-stamped inside the writer lock.

    Split out from the writer so a test can assert the shape without writing,
    and so the two call sites cannot each invent their own payload.
    """
    return {
        "type": "status_change",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": {
            "from_status": from_status,
            "to_status": ARCHIVED_STATUS,
            "reason": reason,
        },
    }


def archive_thread(workspace_root, thread_id: str, *,
                   reason: Any = None,
                   source_skill: str = "workspace-manager",
                   archived_at: Optional[str] = None,
                   regenerate_view: bool = True) -> dict:
    """Archive a thread as a typed substrate write. THE archive path.

    Writes, in order:
      1. `thread_writer.update_thread(..., status="archived", archived_at=…,
         archive_reason=…)` — validated, atomic, emits `thread_updated`.
      2. ONE `status_change` event via `event_gate.append_event`.
      3. `render_master_tracker.regenerate` so the view reflects the archive
         immediately. The Recently Archived row now comes FROM the substrate
         and survives every future render.

    Idempotent: an already-archived thread returns `status:
    "already_archived"` and writes nothing at all — no event, no record touch,
    no regen. "Nothing was written" is the assertion a test can pin; a
    view refresh smuggled into the no-op path would blur it, and a stale view
    is cleanup's job.

    `archived_at` exists for deterministic fixtures. It is an explicit caller
    argument, never wired to a `--now` CLI flag: this is a WRITE path, and the
    WATCHGATE N-1 lesson is that a simulated clock may read but must never
    drive one.

    Raises ThreadArchiveError on an unknown thread id — the caller resolved a
    name to get here, so a miss means the resolution was wrong and the honest
    move is to say so, not to archive something adjacent.
    """
    ws = Path(workspace_root)
    data = _load_entities(ws)
    thread = _find_thread(data, thread_id)
    if thread is None:
        raise ThreadArchiveError(
            f"thread not found: {thread_id!r} — resolve the name through "
            "entity_resolve and pass the matched id, or tell the user nothing "
            "matched. Never archive an adjacent thread.")

    from_status = thread.get("status")
    display_name = thread.get("canonical_name") or thread.get("display_name")

    if from_status == ARCHIVED_STATUS:
        return {
            "status": "already_archived",
            "thread_id": thread_id,
            "display_name": display_name,
            "from_status": from_status,
            "archived_at": thread.get("archived_at"),
            "archive_reason": thread.get("archive_reason"),
            "event": None,
            "view": None,
            "view_error": None,
        }

    clean_reason = normalize_reason(reason)
    stamp = archived_at or _now_iso()

    fields: dict[str, Any] = {"status": ARCHIVED_STATUS, "archived_at": stamp}
    if clean_reason is not None:
        # Omitted rather than written as null when absent: the renderer's
        # `archive_reason or "—"` reads both the same, and an absent key keeps
        # a record that carries no reason honestly empty.
        fields["archive_reason"] = clean_reason

    thread_writer.update_thread(ws, thread_id, source_skill=source_skill,
                                **fields)

    event = build_status_change_event(
        thread_id, from_status=from_status, reason=clean_reason,
        source_skill=source_skill)
    from event_gate import append_event  # local: keeps import order flat
    append_event(_events_path(ws), [event], holder=source_skill)

    view: Optional[dict] = None
    view_error: Optional[str] = None
    if regenerate_view:
        try:
            import render_master_tracker
            view = render_master_tracker.regenerate(ws)
        except Exception as exc:  # noqa: BLE001 — see the docstring
            # The substrate write already landed and is the truth. A renderer
            # failure must not unwind it or be raised over it; the next
            # regen (end-session Step 2.5 / cleanup Phase 3.5d2) recovers the
            # view. Report it so the caller can surface it honestly.
            view_error = f"{type(exc).__name__}: {exc}"

    return {
        "status": "archived",
        "thread_id": thread_id,
        "display_name": display_name,
        "from_status": from_status,
        "to_status": ARCHIVED_STATUS,
        "archived_at": stamp,
        "archive_reason": clean_reason,
        "event": event,
        "view": view,
        "view_error": view_error,
    }


__all__ = [
    "ARCHIVED_STATUS",
    "REASON_MAX_CHARS",
    "ThreadArchiveError",
    "archive_status",
    "archive_thread",
    "build_status_change_event",
    "normalize_reason",
    "stalled_review_reason",
]
