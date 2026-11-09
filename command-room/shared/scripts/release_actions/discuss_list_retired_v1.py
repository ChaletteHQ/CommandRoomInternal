#!/usr/bin/env python3
"""discuss_list_retired_v1: empty the retired discuss-later list on update,
once, without asking (cr1#552, the owner's ruling 2026-11-06, option A1).

WHY
---
`show-my-list` was the only surface that rendered an open
`commitment_to_discuss` item in full, and it is gone. The owner ruled what
happens to whatever is still open on a seat: each item becomes a note on its
person or project (the destination the July list retirement named for new
captures), an item with no person or project becomes a task on My Plate, and
the update message says so in one line. Nothing is deleted and nothing is
closed by hand: the ledger keeps every discuss row, and each one is MOVED by
the row written for it.

HOW A MOVE IS RECORDED (and why not through the closure path)
-------------------------------------------------------------
A discuss item was never a `commitment` row, so the commitment index does not
hold it and `commitment_state.close_commitment` refuses its seq. The move is
therefore the destination write itself: the `note` or the `commitment` (a My
Plate task) carries `data.source_event_seq` = the discuss row's seq and the
stamp `plate_helpers.DISCUSS_MOVED_VIA`, and `plate_helpers.open_discuss_items`
treats an item one of those rows points at as moved. A live mute only HID an
item on the old list, so the migration moves muted items too (`include_muted`). One rule, read by the
plan, the apply and the suite. A second run finds nothing open and writes
nothing: idempotent on identity, never on a clock.

WHO WRITES
----------
The bridge, through the WRITE door (`workspace_access.py plan run_writer`),
under the customer's writer id. The My Plate task is owned by the workspace's
primary user (`primary_user.resolve_primary_user`); a seat that cannot name
one keeps those items open, counts them, and the line says so rather than
minting a task nobody owns.

stdlib + shared/scripts only. Action contract: `release_actions/__init__.py`.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from plate_helpers import DISCUSS_MOVED_VIA, open_discuss_items  # noqa: E402

SOURCE_SKILL = "command-room-update-bridge"
MIGRATION_ID = "discuss_list_retired_v1"
APPLIED_EVENT = "workspace_migration_applied"

STATUS_NOTHING_OPEN = "nothing_open"
STATUS_PENDING = "pending"
STATUS_ADJUDICATED = "adjudicated"
STATUS_APPLIED = "applied"
STATUS_PARTIAL = "partial"

#: Stamped on the pointer note written when an item's words were already on My
#: Plate (the write-time capture-once filter refuses a second task with the same
#: title); the note sits on the owner's own record and marks the item moved.
MOVED_TO_EXISTING_TASK = "my_plate_existing"

#: Evidence the moved rows carry, in the customer's words.
MOVED_NOTE_LINE = "Moved from your discuss-later list when it retired."


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _events(workspace_root) -> List[Dict[str, Any]]:
    from events_io import load_events_owner_scoped

    events, _skipped = load_events_owner_scoped(workspace_root)
    return list(events)


def _entities(workspace_root) -> dict:
    from entities_io import unwrap_entities

    path = Path(str(workspace_root)) / "_hq" / "data" / "entities.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return unwrap_entities(raw) if isinstance(raw, dict) else {}


def _adjudicated(workspace_root) -> bool:
    try:
        import migration_adjudication

        return bool(migration_adjudication.is_suppressed(workspace_root, MIGRATION_ID))
    except Exception:  # noqa: BLE001 - an unreadable ledger is not a suppression
        return False


def _summary(ev: Dict[str, Any]) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for key in ("summary", "title", "text"):
        val = str(d.get(key) or "").strip()
        if val:
            return val
    return ""


def plan(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """What a run would do. A pure read: nothing is written.

    Returns `{migration_id, status, items: [{seq, summary, target_kind,
    target_id}], counts: {to_note, to_plate, untitled}}`. `status` is
    `nothing_open` (the common answer), `adjudicated` (a durable skip or
    applied record for this id) or `pending`."""
    out: Dict[str, Any] = {"migration_id": MIGRATION_ID, "items": [],
                           "counts": {"to_note": 0, "to_plate": 0, "untitled": 0}}
    if _adjudicated(workspace_root):
        out["status"] = STATUS_ADJUDICATED
        return out
    from orphan_note import resolve_note_target

    events = _events(workspace_root)
    entities = _entities(workspace_root)
    open_items = open_discuss_items(events, (now_iso or "").strip() or _now_iso(),
                                    include_muted=True)
    for ev in open_items:
        summary = _summary(ev)
        kind, target_id = resolve_note_target(ev, entities)
        row = {"seq": ev.get("seq"), "summary": summary,
               "target_kind": kind, "target_id": target_id}
        out["items"].append(row)
        if not summary:
            out["counts"]["untitled"] += 1
        elif kind:
            out["counts"]["to_note"] += 1
        else:
            out["counts"]["to_plate"] += 1
    out["status"] = STATUS_PENDING if out["items"] else STATUS_NOTHING_OPEN
    return out


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary_line(counts: Dict[str, int]) -> str:
    """The ONE line the update message says; "" when nothing moved."""
    noted = int(counts.get("noted") or 0)
    tasked = int(counts.get("tasked") or 0)
    on_plate = int(counts.get("already_on_plate") or 0)
    left = int(counts.get("left_open") or 0)
    parts: List[str] = []
    if noted:
        parts.append(f"{_plural(noted, 'item', 'items')} became "
                     + ("a note on its person or project" if noted == 1
                        else "notes on their people or projects"))
    if tasked:
        parts.append(f"{_plural(tasked, 'item', 'items')} became "
                     + ("a My Plate task" if tasked == 1 else "My Plate tasks"))
    if on_plate:
        parts.append(f"{_plural(on_plate, 'item was', 'items were')} already on My Plate")
    if not parts and not left:
        return ""
    line = "Your discuss-later list is retired"
    if parts:
        line += ": " + " and ".join(parts)
    line += "."
    if left:
        line += (f" {_plural(left, 'item', 'items')} could not be moved because this "
                 "workspace has no owner on record; say `what's on my plate` once "
                 "it does.")
    return line


def _moved_task_landed(workspace_root, seq) -> bool:
    """Read back: did a My Plate task pointing at `seq` reach the ledger?
    `create_personal_task` answers `created` even when the write-time
    capture-once filter (`atomic_write`, keyed on `(source_ref, title)`)
    refused the row because the plate already holds those words, so the
    only honest answer is the ledger's own."""
    for ev in _events(workspace_root):
        if ev.get("type") != "commitment":
            continue
        d = ev.get("data") or {}
        if d.get("source") == DISCUSS_MOVED_VIA and d.get("source_event_seq") == seq:
            return True
    return False


def discuss_list_retired_v1(events_jsonl_path, workspace_root,
                            detector_context: Optional[Dict[str, Any]] = None
                            ) -> Dict[str, Any]:
    """The action: move every open item once, log once, or do nothing and say why."""
    ctx = dict(detector_context or {})
    try:
        from receipts import require_writer_identity

        require_writer_identity(workspace_root=workspace_root)
    except ImportError:
        pass
    planned = plan(workspace_root)
    context: Dict[str, Any] = {"migration_id": MIGRATION_ID, "status": planned["status"],
                               "counts": {"noted": 0, "tasked": 0, "already_on_plate": 0,
                                          "left_open": 0},
                               "summary_line": ""}
    if planned["status"] != STATUS_PENDING:
        return {"success": True, "ran": False, "context": context, "error": None,
                "fallback_prompt": None}

    from commitment_state import create_personal_task
    from event_gate import append_event
    from primary_user import resolve_primary_user

    events_path = (Path(str(events_jsonl_path)) if events_jsonl_path
                   else Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl")
    owner = str(resolve_primary_user(workspace_root) or "").strip()
    counts = context["counts"]
    moved: List[Dict[str, Any]] = []
    for item in planned["items"]:
        seq, summary = item["seq"], item["summary"]
        # An untitled item names its own entry so two of them never share a
        # title (the capture-once filter would refuse the second).
        text = summary or f"Discuss-later item with no words recorded (entry {seq})"
        if item["target_kind"]:
            note_ev: Dict[str, Any] = {
                "type": "note", "source_skill": SOURCE_SKILL,
                "data": {"summary": text, "via": DISCUSS_MOVED_VIA,
                         "source_event_seq": seq, "evidence": MOVED_NOTE_LINE},
            }
            if item["target_kind"] == "person":
                note_ev["data"]["person_id"] = item["target_id"]
            else:
                note_ev["primary_thread_id"] = item["target_id"]
            append_event(events_path, [note_ev], holder=SOURCE_SKILL)
            counts["noted"] += 1
            moved.append({"seq": seq, "became": "note",
                          "target_kind": item["target_kind"], "target_id": item["target_id"]})
            continue
        if not owner:
            counts["left_open"] += 1
            continue
        create_personal_task(str(workspace_root), title=text[:240], owner_id=owner,
                             source_event_seq=seq, source_skill=SOURCE_SKILL,
                             source=DISCUSS_MOVED_VIA)
        if _moved_task_landed(workspace_root, seq):
            counts["tasked"] += 1
            moved.append({"seq": seq, "became": "task", "owner_id": owner})
            continue
        # The plate already holds these words (the write was refused by the
        # capture-once filter, never a second copy). The item is not dropped:
        # a pointer note on the owner's own record marks it moved and says
        # where the words live.
        note_ev = {
            "type": "note", "source_skill": SOURCE_SKILL,
            "data": {"summary": text, "via": DISCUSS_MOVED_VIA,
                     "source_event_seq": seq, "person_id": owner,
                     "moved_to": MOVED_TO_EXISTING_TASK,
                     "evidence": "Already on My Plate when the discuss-later list retired."},
        }
        append_event(events_path, [note_ev], holder=SOURCE_SKILL)
        counts["already_on_plate"] += 1
        moved.append({"seq": seq, "became": "note", "target_kind": "person",
                      "target_id": owner, "moved_to": MOVED_TO_EXISTING_TASK})

    context["summary_line"] = summary_line(counts)
    if counts["left_open"]:
        # Items a seat with no owner could not move stay open and are said
        # out loud; no applied row, so the next update tries them again once
        # the workspace names its owner.
        context["status"] = STATUS_PARTIAL
        return {"success": True, "ran": bool(moved), "context": context, "error": None,
                "fallback_prompt": None}

    row = {"type": APPLIED_EVENT, "source_skill": SOURCE_SKILL,
           "data": {"migration_id": MIGRATION_ID, "target_file": "_hq/data/events.jsonl",
                    "from_version": ctx.get("from_version"),
                    "to_version": ctx.get("to_version"),
                    "actor": SOURCE_SKILL, "moved": moved,
                    "noted": counts["noted"], "tasked": counts["tasked"],
                    "already_on_plate": counts["already_on_plate"]}}
    stamped = append_event(events_path, [row], holder=SOURCE_SKILL)
    context.update({"status": STATUS_APPLIED,
                    "event_seq": (stamped[0].get("seq") if stamped else None)})
    return {"success": True, "ran": True, "context": context, "error": None,
            "fallback_prompt": None}


__all__ = [
    "APPLIED_EVENT", "MIGRATION_ID", "MOVED_NOTE_LINE", "MOVED_TO_EXISTING_TASK", "SOURCE_SKILL",
    "STATUS_ADJUDICATED", "STATUS_APPLIED", "STATUS_NOTHING_OPEN", "STATUS_PARTIAL",
    "STATUS_PENDING",
    "discuss_list_retired_v1", "plan", "summary_line",
]
