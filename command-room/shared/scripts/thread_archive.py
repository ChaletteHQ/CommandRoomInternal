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
    side effect of closing their own object:
      * `deal_state.close_deal(..., "lost")` — a lost deal's thread
        (deal_state.py, `thread_status = "resolved" if outcome == "won" else
        "archived"`). Owner: SPEC PIPE1.
      * `objective_state.archive_objective` — an archived objective's thread
        (objective_state.py `_close`). Owner: SPEC OBJ1.
    Until RIDERS1 they stamped no `archived_at` and appended no timeline event
    at all — measured live: a deal-leg archive wrote the record and NOTHING
    else. MASTER_TRACKER's Recently Archived section sorts on `archived_at`, so
    those threads sorted under the empty string, rendered their date cell as
    `—`, and sank off the top-10 list; the same symptom as the bug this module
    fixes, one object over.
    Both legs now stamp `archived_at` (via `archive_stamp`) and `archive_reason`
    inside their OWN atomic record write, and append the canonical event built
    by `build_status_change_event` — the shared builder, so the three sites
    cannot drift. They deliberately do NOT route through `archive_thread`: each
    leg writes its closed deal/objective object and the status in ONE
    `update_thread` call, and archiving first would open a window where a thread
    is archived with an open deal still on it. What they skip on purpose is the
    VIEW regen — those closers never rendered, and the next scheduled regen
    (end-session Step 2.5 / cleanup Phase 3.5d2) picks the archive up.
    Do not "fix" this by widening this module; a THIRD lifecycle closer takes
    the same two stamps and the same shared builder.
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
  - No decision-making. Nothing in THIS module decides that a thread SHOULD be
    archived — no staleness rule, no decay, no dormancy transition lives here.
    The caller decides and this module writes.

    Most callers are downstream of a human gesture (workspace-manager's
    `archive [project]`, the stalled-projects `archive` tap, the dormancy
    row's `archive` verb). Exactly ONE is not: `lifecycle_pass.py` archives a
    thread that has been DORMANT and quiet past the 180-day rule in the
    lifecycle state machine (SPEC LIFECYCLE1, M's ruling 2026-08-02 — eliminate
    the Pulse chat, fold its real jobs). That rule and its caps live in that
    module, not here, and it routes through `archive_thread` precisely so the
    leg gets the `archived_at` stamp and the canonical event Pulse's prose
    never wrote. (An earlier draft of this docstring read "every call is
    downstream of a human gesture, Pulse-style automatic lifecycle transitions
    are eliminated" — accurate the day ARCHFIX shipped, superseded the day the
    fold landed. A stale doc is a live instruction.)
  - No un-archive DECISION. Which status a thread comes back to is the caller's
    lifecycle contract, and `lifecycle_pass` owns it: it writes the revive
    through `thread_writer.update_thread` + this module's shared
    `build_thread_status_event`, NOT through a side door here.

    But the two stamps `archive_thread` puts ON a record belong to this module,
    and so does taking them off. `clear_archive_stamps` below is the writer for
    that, added in the LIFECYCLE1 fix round because the first cut left them
    behind: a revived thread still carrying `archived_at` is an ACTIVE project
    holding an archive date. That is the same sort-key class as the bug this
    module exists to fix, one step further on — MASTER_TRACKER's Recently
    Archived section sorts on that field, `archive_status` reported an archive
    the thread no longer has, and the next real archive would have been
    indistinguishable from the stale stamp. Whoever sets a stamp owns clearing
    it; a caller hand-writing `archived_at = None` is exactly the hand-write
    this module refuses everywhere else.

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


def archive_stamp() -> str:
    """The `archived_at` value, in ONE shape.

    Public because the two lifecycle closers named in the census above stamp it
    at their own call sites — they cannot route through `archive_thread` without
    splitting one atomic record write in two — and `archived_at` is MASTER
    TRACKER's sort key for Recently Archived. A second spelling of the same
    timestamp is how a sort key quietly stops sorting.
    """
    return _now_iso()


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


def build_thread_status_event(thread_id: str, *, from_status: Optional[str],
                              to_status: str, reason: Optional[str],
                              source_skill: str) -> dict:
    """The canonical `status_change` envelope for ANY thread transition.

    Shape follows the live precedent (the v21 fixture's status_change payload
    and the dont-forget dispatch): the thread id rides the CANONICAL envelope
    field `primary_thread_id` (`project_id` is deprecated per
    events.schema.json) and the transition rides `data`. `seq` and `ts` are
    omitted on purpose — they are auto-stamped inside the writer lock.

    Split out from the writer so a test can assert the shape without writing,
    and so no call site invents its own payload. `build_status_change_event`
    below is the archive-shaped alias every pre-LIFECYCLE1 caller uses; the
    active->dormant and revive legs of `lifecycle_pass` call THIS one, because
    a shared builder that only ever emits one `to_status` is a shared builder
    for one transition and an invitation to hand-roll the rest.
    """
    return {
        "type": "status_change",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": {
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
        },
    }


def build_status_change_event(thread_id: str, *, from_status: Optional[str],
                              reason: Optional[str],
                              source_skill: str) -> dict:
    """The canonical `status_change` envelope for an ARCHIVE — the shared
    builder `deal_state`, `objective_state` and `archive_thread` all use.
    Delegates to `build_thread_status_event` so there is one payload shape."""
    return build_thread_status_event(
        thread_id, from_status=from_status, to_status=ARCHIVED_STATUS,
        reason=reason, source_skill=source_skill)


def clear_archive_stamps(workspace_root, thread_id: str, *,
                         source_skill: str = "workspace-manager") -> dict:
    """Take the archive stamps OFF a record — the counterpart of the pair
    `archive_thread` puts on. Typed write through `thread_writer.update_thread`
    like everything else here; no direct entities.json edit.

    Call this when a thread LEAVES `archived` for any live status. It does not
    decide the new status and does not write a `status_change` — the caller's
    lifecycle contract owns both; this clears the two fields the caller has no
    business hand-writing.

    Honest no-op when there is nothing to clear (`status: "no_stamps"`, zero
    record churn, no `thread_updated` event) — the same posture
    `archive_thread` takes on an already-archived thread, and for the same
    reason: a write that changed nothing must not look like a write.

    Raises ThreadArchiveError on an unknown thread id.
    """
    ws = Path(workspace_root)
    data = _load_entities(ws)
    thread = _find_thread(data, thread_id)
    if thread is None:
        raise ThreadArchiveError(
            f"thread not found: {thread_id!r} — resolve the name through "
            "entity_resolve and pass the matched id.")
    had = {k: thread.get(k) for k in ("archived_at", "archive_reason")
           if thread.get(k)}
    if not had:
        return {"status": "no_stamps", "thread_id": thread_id, "cleared": {}}
    thread_writer.update_thread(ws, thread_id, source_skill=source_skill,
                                archived_at=None, archive_reason=None)
    return {"status": "cleared", "thread_id": thread_id, "cleared": had}


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
    "archive_stamp",
    "archive_status",
    "archive_thread",
    "clear_archive_stamps",
    "build_status_change_event",
    "build_thread_status_event",
    "normalize_reason",
    "stalled_review_reason",
]
