#!/usr/bin/env python3
"""
objective_state.py — THE objective writer / single closure path
(SPEC OBJ1, DRAFT 2026-07).

WHY THIS EXISTS
===============
A standing objective is a CEO-level priority too big to mark done in a day:
it spans weeks, carries multiple steps, and is tracked through ONE of three
bindings the CEO picks at creation (meeting / self / activity). The failure
mode this module exists to prevent is the same one deal_state was built
against (Bug #99 class): status smuggled into prose or hand-stamped fields
that nothing maintains. Doctrine, mirrored from deal_state / commitment_state:

  - ONE closure path: complete_objective() / archive_objective() are the
    only ways an objective reaches a terminal outcome. Idempotent
    (`already_closed` is a NO-OP, never a duplicate event), loud on bad
    input, and they flip the thread status in the same call
    (completed -> resolved, archived -> archived).
  - NO stored status: directional status (on_track / at_risk / off_track /
    blocked) lives ONLY in objective_review / objective_report events and is
    derived by objective_math.py, branching on the binding type. A status
    field on the entity is rejected at the thread_writer floor.
  - NO hand-rolled writes: entity mutations route through
    thread_writer.create_thread / update_thread (schema validation, atomic
    locked write) and events route through event_gate.append_event (the
    Phase 1 gate) — there is no direct open(...) write anywhere here.
  - Status honesty follows the binding: record_review() is the meeting-path
    harvest (called from the meeting-notes extraction step — never a
    parallel transcript scanner) and refuses objectives not meeting-bound;
    record_report() is the owner's own word and is valid on ANY open
    objective (a spontaneous "we're at risk on X" is always trusted), though
    the weekly touch only ASKS for self-bound ones.
  - Topic over party: an activity binding's entity_ids and the optional
    anchor_thread_id must reference existing THREADS (a project or deal) —
    never a bare person. Party overlap is not a join.

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

import thread_archive  # noqa: E402
import thread_writer  # noqa: E402
from thread_writer import (  # noqa: E402
    ALLOWED_OBJECTIVE_BINDING_FIELDS,
    ALLOWED_OBJECTIVE_FIELDS,
    OBJECTIVE_BINDING_TYPES,
    OBJECTIVE_OUTCOMES,
    OBJECTIVE_SERIES_MATCH,
)
from entities_io import entities_collection  # noqa: E402


class ObjectiveStateError(ValueError):
    """An objective write was refused. Fail loud — silent fallthrough is how
    status stores rot into fiction."""


# Directional status vocabulary — the ONLY values a stated review or an
# owner report may carry. Everything else derives as "moving"/"quiet"
# (objective_math), never directional.
OBJECTIVE_STATUSES = ("on_track", "at_risk", "off_track", "blocked")

# Fields update_objective may touch. Binding changes go through
# rebind_objective (a deliberate re-choice with its own trail);
# outcome/closed_at go through complete/archive — no other path.
UPDATABLE_OBJECTIVE_FIELDS = {"statement", "horizon", "milestones",
                              "target_note"}

DEFAULT_CADENCE_DAYS = 7  # the weekly objectives touch


def _today() -> str:
    return datetime.date.today().isoformat()


def _events_path(ws: Path) -> Path:
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def _load_entities(ws: Path) -> dict:
    p = Path(ws) / "_hq" / "data" / "entities.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _threads(data: dict) -> list:
    threads = entities_collection(data, "threads")
    projects = entities_collection(data, "projects")
    if projects and not threads:
        return projects
    return threads


def _find_thread(data: dict, thread_id: str) -> Optional[dict]:
    return next((t for t in _threads(data) if t.get("id") == thread_id), None)


def _append(ws: Path, event, source_skill: str) -> None:
    """Gated append. Takes one event or a list — a list lands in ONE append, so
    two events written for the same transition cannot half-land."""
    from event_gate import append_event
    events = [event] if isinstance(event, dict) else list(event)
    append_event(_events_path(ws), events, holder=source_skill)


def _require_objective_thread(thread: Optional[dict], thread_id: str) -> dict:
    if thread is None:
        raise ObjectiveStateError(f"thread not found: {thread_id!r}")
    if thread.get("kind") != "objective":
        raise ObjectiveStateError(
            f"thread {thread_id!r} is kind={thread.get('kind')!r}, not an "
            "objective thread — objective_state only writes kind='objective' "
            "threads")
    obj = thread.get("objective")
    if not isinstance(obj, dict):
        raise ObjectiveStateError(
            f"thread {thread_id!r} carries no objective object — a malformed "
            "objective thread; recreate it via create_objective")
    return thread


def _require_open(obj: dict, thread_id: str) -> None:
    if obj.get("outcome") in OBJECTIVE_OUTCOMES:
        raise ObjectiveStateError(
            f"objective {thread_id!r} is already closed "
            f"({obj['outcome']}) — terminal objectives are not editable; "
            "create a new objective for a new push")


def normalize_series_key(title: str) -> str:
    """The series fingerprint's title half: lowercased, punctuation dropped,
    whitespace collapsed. There is no calendar series id in the substrate —
    normalized title (+ usual people) is the established recurrence
    convention; keep this the ONE normalizer both binding-time and
    harvest-time use so the two sides can never drift."""
    s = re.sub(r"[^a-z0-9\s]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _validate_binding(ws: Path, data: dict, binding: dict) -> dict:
    """Semantic checks above the thread_writer schema floor: referenced
    entities must exist, and defaults are filled (cadence, series_match).
    Returns a normalized copy — never mutates the caller's dict."""
    if not isinstance(binding, dict):
        raise ObjectiveStateError(
            "binding must be an object with a 'type' — the CEO's three-way "
            f"tracking choice, one of {list(OBJECTIVE_BINDING_TYPES)}")
    b = dict(binding)
    b_type = b.get("type")
    if b_type not in OBJECTIVE_BINDING_TYPES:
        raise ObjectiveStateError(
            f"binding.type must be one of {list(OBJECTIVE_BINDING_TYPES)}, "
            f"got: {b_type!r}")
    extras = set(b) - ALLOWED_OBJECTIVE_BINDING_FIELDS
    if extras:
        raise ObjectiveStateError(
            f"binding has unknown fields: {sorted(extras)} — allowed: "
            f"{sorted(ALLOWED_OBJECTIVE_BINDING_FIELDS)}")
    if b_type == "meeting":
        key = b.get("series_key")
        if not isinstance(key, str) or not key.strip():
            raise ObjectiveStateError(
                "a meeting binding requires series_key — propose the "
                "recurring meeting from the meeting history and confirm with "
                "the user; never bind blind")
        b["series_key"] = normalize_series_key(key)
        if not b["series_key"]:
            raise ObjectiveStateError(
                f"series_key {key!r} normalizes to nothing — not a usable "
                "meeting title")
        if b.get("series_match") is None:
            b["series_match"] = "title_and_people"
        if b["series_match"] not in OBJECTIVE_SERIES_MATCH:
            raise ObjectiveStateError(
                f"series_match must be one of {list(OBJECTIVE_SERIES_MATCH)}, "
                f"got: {b['series_match']!r}")
        people = b.get("series_people")
        if b["series_match"] == "title_and_people" and not people:
            raise ObjectiveStateError(
                "series_match='title_and_people' requires series_people (the "
                "usual attendees' person_ids). For a distinctively named "
                "meeting, use series_match='title_only' instead.")
    elif b_type == "self":
        if b.get("cadence_days") is None:
            b["cadence_days"] = DEFAULT_CADENCE_DAYS
    elif b_type == "activity":
        ids = b.get("entity_ids")
        if not isinstance(ids, list) or not ids:
            raise ObjectiveStateError(
                "an activity binding requires entity_ids — the linked "
                "thread/deal ids whose own events drive status. Topic over "
                "party: bind to a thread or deal, never a bare person.")
        for tid in ids:
            t = _find_thread(data, tid)
            if t is None:
                raise ObjectiveStateError(
                    f"entity_ids references {tid!r}, which is not an "
                    "existing thread — activity bindings link threads or "
                    "deal threads only (people/orgs are party context, not "
                    "a binding target)")
    return b


def _check_anchor(data: dict, anchor_thread_id: Optional[str]) -> None:
    if anchor_thread_id is None:
        return
    t = _find_thread(data, anchor_thread_id)
    if t is None:
        raise ObjectiveStateError(
            f"anchor_thread_id={anchor_thread_id!r} does not reference an "
            "existing thread")
    if t.get("kind") == "objective":
        raise ObjectiveStateError(
            f"anchor_thread_id={anchor_thread_id!r} is itself an objective "
            "thread — anchor to the underlying project/deal thread instead")


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def create_objective(
    workspace_root,
    *,
    statement: str,
    binding: dict,
    name: Optional[str] = None,
    owner_person_id: Optional[str] = None,
    horizon: Optional[str] = None,
    anchor_thread_id: Optional[str] = None,
    milestones: Optional[list] = None,
    org_id: Optional[str] = None,
    source_skill: str = "objectives",
) -> dict:
    """Open a standing objective: ALWAYS a new kind='objective' thread
    carrying the nested objective object, plus an `objective_created` event.
    An objective about existing work links it via anchor_thread_id — the
    existing thread's kind is never mutated (cold-start proposals included),
    so confirming a proposal is reversible by plain archive.

    `binding` is the CEO's confirmed three-way choice with the system's
    proposed target inside it (see _validate_binding). `name` defaults to
    the statement (threads dedup by name).
    """
    ws = Path(workspace_root)
    if not isinstance(statement, str) or not statement.strip():
        raise ObjectiveStateError(
            "statement must be the objective in the CEO's own words — a "
            "non-empty string")

    data = _load_entities(ws)
    norm_binding = _validate_binding(ws, data, binding)
    _check_anchor(data, anchor_thread_id)

    obj: dict[str, Any] = {
        "statement": statement.strip(),
        "binding": norm_binding,
        "opened_at": _today(),
    }
    if horizon is not None:
        obj["horizon"] = horizon
    if anchor_thread_id is not None:
        obj["anchor_thread_id"] = anchor_thread_id
    if milestones:
        obj["milestones"] = [
            {"title": m["title"], "done": bool(m.get("done", False))}
            if isinstance(m, dict) else {"title": str(m), "done": False}
            for m in milestones
        ]

    # Objectives are the operator's own goals — deliberately unaffiliated by
    # default (they link work via anchor_thread_id, not an org). The sentinel
    # is passed EXPLICITLY per ENTITY1 §4a so this reads as a choice, not an
    # omission; callers with an org-scoped objective pass org_id.
    thread = thread_writer.create_thread(
        ws,
        canonical_name=(name or statement).strip(),
        kind="objective",
        org_id=org_id or thread_writer.UNAFFILIATED_ORG_ID,
        owner_person_id=owner_person_id,
        objective=obj,
        source_skill=source_skill,
    )

    ev_data: dict[str, Any] = {
        "thread_id": thread["id"],
        "statement": obj["statement"],
        "binding_type": norm_binding["type"],
    }
    if owner_person_id:
        ev_data["owner_id"] = owner_person_id
    if horizon:
        ev_data["horizon"] = horizon
    if anchor_thread_id:
        ev_data["anchor_thread_id"] = anchor_thread_id
    _append(ws, {
        "type": "objective_created",
        "source_skill": source_skill,
        "primary_thread_id": thread["id"],
        "related_thread_ids": [anchor_thread_id] if anchor_thread_id else [],
        "data": ev_data,
    }, source_skill)
    return thread


def update_objective(
    workspace_root,
    thread_id: str,
    *,
    owner_person_id: Optional[str] = None,
    source_skill: str = "objectives",
    **fields,
) -> dict:
    """Update non-terminal, non-binding objective fields (statement /
    horizon / milestones / target_note) and/or the owner. Binding changes
    are rebind_objective; terminal outcomes are complete/archive — this
    function refuses both."""
    ws = Path(workspace_root)
    bad = set(fields) - UPDATABLE_OBJECTIVE_FIELDS
    if bad:
        raise ObjectiveStateError(
            f"update_objective cannot touch {sorted(bad)} — binding changes "
            "go through rebind_objective, outcomes through "
            "complete_objective/archive_objective. Updatable: "
            f"{sorted(UPDATABLE_OBJECTIVE_FIELDS)} (+ owner_person_id)")
    if not fields and owner_person_id is None:
        raise ObjectiveStateError(
            "update_objective needs at least one field to change")

    data = _load_entities(ws)
    thread = _require_objective_thread(_find_thread(data, thread_id), thread_id)
    obj = thread["objective"]
    _require_open(obj, thread_id)

    new_obj = dict(obj)
    changed: dict[str, Any] = {}
    target_note = fields.pop("target_note", None)
    if target_note is not None:
        binding = dict(new_obj.get("binding") or {})
        if binding.get("type") != "activity":
            raise ObjectiveStateError(
                "target_note only applies to an activity binding")
        if binding.get("target_note") != target_note:
            binding["target_note"] = str(target_note)
            new_obj["binding"] = binding
            changed["target_note"] = str(target_note)
    for k, v in fields.items():
        if new_obj.get(k) != v:
            new_obj[k] = v
            changed[k] = v

    thread_updates: dict[str, Any] = {}
    if changed:
        thread_updates["objective"] = new_obj
    if owner_person_id is not None and thread.get("owner_person_id") != owner_person_id:
        thread_updates["owner_person_id"] = owner_person_id
        changed["owner_id"] = owner_person_id
    if not thread_updates:
        return {"status": "unchanged", "thread_id": thread_id}

    thread_writer.update_thread(ws, thread_id, source_skill=source_skill,
                                **thread_updates)
    ev_data: dict[str, Any] = {"thread_id": thread_id}
    for k in ("statement", "horizon", "owner_id", "target_note"):
        if k in changed:
            ev_data[k] = changed[k]
    if "milestones" in changed:
        ev_data["milestones_count"] = len(changed["milestones"] or [])
    _append(ws, {
        "type": "objective_updated",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": ev_data,
    }, source_skill)
    return {"status": "updated", "thread_id": thread_id, "changed": changed}


def rebind_objective(
    workspace_root,
    thread_id: str,
    binding: dict,
    *,
    source_skill: str = "objectives",
) -> dict:
    """Replace the tracking binding — a deliberate re-choice (the CEO flips
    the toggle, or rebinds a renamed review meeting). Full binding
    validation runs again; the change leaves an objective_updated trail
    carrying the new binding_type."""
    ws = Path(workspace_root)
    data = _load_entities(ws)
    thread = _require_objective_thread(_find_thread(data, thread_id), thread_id)
    obj = thread["objective"]
    _require_open(obj, thread_id)

    norm_binding = _validate_binding(ws, data, binding)
    if norm_binding == obj.get("binding"):
        return {"status": "unchanged", "thread_id": thread_id}

    new_obj = dict(obj)
    new_obj["binding"] = norm_binding
    thread_writer.update_thread(ws, thread_id, objective=new_obj,
                                source_skill=source_skill)
    _append(ws, {
        "type": "objective_updated",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": {"thread_id": thread_id,
                 "binding_type": norm_binding["type"]},
    }, source_skill)
    return {"status": "rebound", "thread_id": thread_id,
            "binding_type": norm_binding["type"]}


def record_review(
    workspace_root,
    thread_id: str,
    *,
    status: str,
    source_ref: str,
    context: Optional[str] = None,
    meeting_title: Optional[str] = None,
    source_skill: str = "meeting-notes",
) -> dict:
    """The meeting-path harvest: the objective was actually discussed in its
    bound review meeting, and someone STATED a status. Called from the
    meeting-notes extraction step (the one transcript pipeline — never a
    parallel scanner).

      - Refuses objectives not meeting-bound (a discussion elsewhere is
        context/movement via the classification envelope, not a directional
        status — status honesty follows the binding).
      - Idempotent per meeting: a second call with the same source_ref
        returns {"status": "already_reviewed"} and writes NOTHING (the
        belt beneath meeting-notes' own already-processed guard).
      - A bound-series meeting where the objective did NOT clear the
        discussed bar calls nothing — the absence is what drift math counts.
    """
    ws = Path(workspace_root)
    if status not in OBJECTIVE_STATUSES:
        raise ObjectiveStateError(
            f"status must be one of {list(OBJECTIVE_STATUSES)}, got: "
            f"{status!r} — the stated status only, never an inferred one")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ObjectiveStateError(
            "source_ref is required — the processed meeting this review came "
            "from (provenance is not optional)")

    data = _load_entities(ws)
    thread = _require_objective_thread(_find_thread(data, thread_id), thread_id)
    obj = thread["objective"]
    _require_open(obj, thread_id)
    binding = obj.get("binding") or {}
    if binding.get("type") != "meeting":
        raise ObjectiveStateError(
            f"objective {thread_id!r} is {binding.get('type')!r}-bound, not "
            "meeting-bound — a stated status from a non-forum meeting is "
            "context, not a review; record_report is the owner's-word path")

    events, _skipped = load_objective_events(ws)
    for e in events:
        if (e.get("type") == "objective_review"
                and (e.get("data") or {}).get("source_ref") == source_ref
                and _event_thread_id(e) == thread_id):
            return {"status": "already_reviewed", "thread_id": thread_id,
                    "source_ref": source_ref}

    ev_data: dict[str, Any] = {
        "thread_id": thread_id,
        "status": status,
        "source_ref": source_ref,
    }
    if context:
        ev_data["context"] = str(context)[:200]
    if meeting_title:
        ev_data["meeting_title"] = str(meeting_title)[:120]
    _append(ws, {
        "type": "objective_review",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": ev_data,
    }, source_skill)
    return {"status": "reviewed", "thread_id": thread_id,
            "stated_status": status}


def record_report(
    workspace_root,
    thread_id: str,
    *,
    status: str,
    note: Optional[str] = None,
    reported_by: Optional[str] = None,
    source_skill: str = "objectives",
) -> dict:
    """The owner's own 20-second word. Valid on ANY open objective — a
    spontaneous "we're at risk on X" is always trusted regardless of
    binding (it is the owner's word); the weekly touch only ASKS for
    self-bound objectives. Never called by anything automated."""
    ws = Path(workspace_root)
    if status not in OBJECTIVE_STATUSES:
        raise ObjectiveStateError(
            f"status must be one of {list(OBJECTIVE_STATUSES)}, got: "
            f"{status!r}")
    data = _load_entities(ws)
    thread = _require_objective_thread(_find_thread(data, thread_id), thread_id)
    _require_open(thread["objective"], thread_id)

    ev_data: dict[str, Any] = {"thread_id": thread_id, "status": status}
    if note:
        ev_data["note"] = str(note)[:200]
    if reported_by:
        ev_data["reported_by"] = reported_by
    _append(ws, {
        "type": "objective_report",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": ev_data,
    }, source_skill)
    return {"status": "reported", "thread_id": thread_id,
            "reported_status": status}


def _close(
    workspace_root,
    thread_id: str,
    outcome: str,
    *,
    outcome_note: Optional[str],
    source_skill: str,
) -> dict:
    ws = Path(workspace_root)
    data = _load_entities(ws)
    thread = _require_objective_thread(_find_thread(data, thread_id), thread_id)
    obj = thread["objective"]

    # Idempotency: terminal is terminal. No second event, no status churn.
    if obj.get("outcome") in OBJECTIVE_OUTCOMES:
        return {"status": "already_closed", "thread_id": thread_id,
                "outcome": obj["outcome"]}

    new_obj = dict(obj)
    new_obj["outcome"] = outcome
    new_obj["closed_at"] = _today()
    if outcome_note:
        new_obj["outcome_note"] = str(outcome_note)[:300]

    thread_status = "resolved" if outcome == "completed" else "archived"
    # SPEC RIDERS1 item 2 — the ARCHFIX gap, one object over. See the twin
    # comment in `deal_state._close`: this leg landed `status: "archived"` on an
    # archived objective's thread with no `archived_at` (MASTER_TRACKER's sort
    # key) and no timeline event. `archive_thread` is not the path — the closed
    # objective object and the status are ONE atomic record write here — so the
    # stamps ride that write and the event comes from the shared builder.
    from_status = thread.get("status")
    fields: dict[str, Any] = {"objective": new_obj, "status": thread_status}
    archive_reason = None
    if thread_status == thread_archive.ARCHIVED_STATUS:
        # The lifecycle transition itself, never the user's `outcome_note` —
        # that is free text and `archive_reason` lands in a markdown table cell
        # on a rendered view.
        archive_reason = thread_archive.normalize_reason("objective archived")
        fields["archived_at"] = thread_archive.archive_stamp()
        if archive_reason is not None:
            fields["archive_reason"] = archive_reason
    thread_writer.update_thread(
        ws, thread_id, source_skill=source_skill, **fields)

    ev_data: dict[str, Any] = {
        "thread_id": thread_id,
        "statement": obj.get("statement"),
    }
    if outcome_note:
        ev_data["outcome_note"] = str(outcome_note)[:300]
    event_type = ("objective_completed" if outcome == "completed"
                  else "objective_archived")
    event = {
        "type": event_type,
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": ev_data,
    }
    # Record first, event second (the ARCHFIX order) — and both events in ONE
    # gated append, so an archived objective cannot end up with its outcome on
    # the timeline and its archive missing from it.
    to_append = [event]
    if thread_status == thread_archive.ARCHIVED_STATUS:
        to_append.append(thread_archive.build_status_change_event(
            thread_id, from_status=from_status, reason=archive_reason,
            source_skill=source_skill))
    _append(ws, to_append, source_skill)
    return {"status": "closed", "thread_id": thread_id, "outcome": outcome,
            "event": event}


def complete_objective(workspace_root, thread_id: str, *,
                       outcome_note: Optional[str] = None,
                       source_skill: str = "objectives") -> dict:
    """THE completion path — 'we did it'. Idempotent; flips the thread to
    'resolved' in the same call."""
    return _close(workspace_root, thread_id, "completed",
                  outcome_note=outcome_note, source_skill=source_skill)


def archive_objective(workspace_root, thread_id: str, *,
                      outcome_note: Optional[str] = None,
                      source_skill: str = "objectives") -> dict:
    """THE archive path — 'no longer an objective', including the
    self-report graceful death (repeatedly ignored asks escalate to 'is
    this still an objective?' and land here only on the CEO's yes).
    Idempotent; flips the thread to 'archived' in the same call."""
    return _close(workspace_root, thread_id, "archived",
                  outcome_note=outcome_note, source_skill=source_skill)


# ---------------------------------------------------------------------------
# Readers (defensive)
# ---------------------------------------------------------------------------

def _event_thread_id(e: dict) -> Optional[str]:
    return e.get("primary_thread_id") or (e.get("data") or {}).get("thread_id")


def list_open_objectives(workspace_root) -> list[dict]:
    """Every open objective thread, defensively read. One row per
    kind='objective' thread that is not archived/resolved and has no
    terminal outcome:

      {thread_id, name, owner_person_id, status, objective (dict|None),
       malformed (bool)}

    `malformed=True` = a kind='objective' thread with NO objective object
    (hand-made or corrupted). Readers surface these honestly and offer
    recreation — never crash, never silently drop.
    """
    ws = Path(workspace_root)
    try:
        data = _load_entities(ws)
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for t in _threads(data):
        if not isinstance(t, dict) or t.get("kind") != "objective":
            continue
        if t.get("status") in ("archived", "resolved"):
            continue
        obj = t.get("objective") if isinstance(t.get("objective"), dict) else None
        if obj and obj.get("outcome") in OBJECTIVE_OUTCOMES:
            continue
        out.append({
            "thread_id": t.get("id"),
            "name": t.get("canonical_name") or t.get("display_name") or t.get("id"),
            "owner_person_id": t.get("owner_person_id"),
            "status": t.get("status"),
            "objective": obj,
            "malformed": obj is None,
        })
    return out


def list_closed_objectives(workspace_root) -> list[dict]:
    """Closed objectives (terminal outcome on the objective object). Same
    row shape as list_open_objectives; the objective carries
    outcome/closed_at — what weekly-recap and the value receipt read."""
    ws = Path(workspace_root)
    try:
        data = _load_entities(ws)
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for t in _threads(data):
        if not isinstance(t, dict) or t.get("kind") != "objective":
            continue
        obj = t.get("objective") if isinstance(t.get("objective"), dict) else None
        if not obj or obj.get("outcome") not in OBJECTIVE_OUTCOMES:
            continue
        out.append({
            "thread_id": t.get("id"),
            "name": t.get("canonical_name") or t.get("display_name") or t.get("id"),
            "owner_person_id": t.get("owner_person_id"),
            "status": t.get("status"),
            "objective": obj,
            "malformed": False,
        })
    return out


OBJECTIVE_EVENT_TYPES = {
    "objective_created", "objective_updated", "objective_review",
    "objective_report", "objective_completed", "objective_archived",
}


def load_objective_events(workspace_root) -> tuple[list[dict], list[dict]]:
    """All objective_* events, via the canonical defensive reader. Returns
    (events, skipped) — the caller MUST surface a non-empty skipped list
    (banner rule), never swallow it."""
    from cru_match import load_events_defensively
    p = _events_path(Path(workspace_root))
    if not p.exists():
        return [], []
    events, skipped = load_events_defensively(p)
    return [e for e in events if e.get("type") in OBJECTIVE_EVENT_TYPES], skipped


__all__ = [
    "ObjectiveStateError",
    "OBJECTIVE_STATUSES",
    "OBJECTIVE_BINDING_TYPES",
    "OBJECTIVE_SERIES_MATCH",
    "OBJECTIVE_OUTCOMES",
    "OBJECTIVE_EVENT_TYPES",
    "UPDATABLE_OBJECTIVE_FIELDS",
    "DEFAULT_CADENCE_DAYS",
    "normalize_series_key",
    "create_objective",
    "update_objective",
    "rebind_objective",
    "record_review",
    "record_report",
    "complete_objective",
    "archive_objective",
    "list_open_objectives",
    "list_closed_objectives",
    "load_objective_events",
]
