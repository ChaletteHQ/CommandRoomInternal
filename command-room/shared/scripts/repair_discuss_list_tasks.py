#!/usr/bin/env python3
"""repair_discuss_list_tasks.py: put back what the first discuss-later move got
wrong, on a seat that already ran it (cr1#973).

The first version of `release_actions.discuss_list_retired_v1` made a My Plate
task of every open item with no person or project. Empty items, bare names,
notes-to-self and items naming a day already gone became open tasks, each read
as the owner's own word. The migration is fixed for seats that have not run it;
this script repairs the seats that have.

WHAT IT DOES
------------
It finds the tasks that migration wrote (its own marker: a `commitment` with
`data.source == "discuss_list_retirement"` and `data.source_event_seq` naming
the discuss row), still open, and reads each one's discuss row with today's
rules (`discuss_list_retired_v1.read_item`):
  * not a task (no words, a label, a passed date): the task is closed through
    THE closure path (`commitment_state.close_commitment`, `superseded`, owner
    confirmed) and a note carrying the item's words is written where the fixed
    migration would have put it, pointing at the same discuss row;
  * a task whose words name a day still ahead and that carries no date: the
    date is set through `due_set.set_due`.
Everything else is left alone.

SAFETY
------
- PREVIEW BY DEFAULT: with no flag it lists what it would change and writes
  nothing.
- `--apply`: owner-invoked, by hand only. Never on a schedule, never from the
  Hub. Copies events.jsonl into `_archive/` first (archive, never delete), then
  writes, then logs ONE `workspace_migration_applied` row with
  `migration_id: "discuss_list_retired_v1_repair"` naming every change.
- Idempotent: a second run finds the tasks closed and dated, and writes nothing.
- `--undo`: reopens every task the last repair closed and takes back every date
  it set. The notes stay, as history.

USAGE
-----
    python3 shared/scripts/repair_discuss_list_tasks.py <workspace_root>           # preview
    python3 shared/scripts/repair_discuss_list_tasks.py <workspace_root> --apply   # snapshot + write
    python3 shared/scripts/repair_discuss_list_tasks.py <workspace_root> --undo    # put it back

stdlib + shared/scripts only.
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from plate_helpers import DISCUSS_MOVED_VIA  # noqa: E402
from release_actions import discuss_list_retired_v1 as mig  # noqa: E402

REPAIR_ID = "discuss_list_retired_v1_repair"
CLOSE_EVIDENCE = "Not a task: kept as a note when the discuss-later move was repaired."
DATE_REASON = "the date the discuss-later item named"

STATUS_NOTHING = "nothing_to_repair"
STATUS_PENDING = "pending"
STATUS_REPAIRED = "repaired"
STATUS_UNDONE = "undone"
STATUS_NOTHING_TO_UNDO = "nothing_to_undo"

_READ_WORDS = {
    mig.READ_NO_WORDS: "no words",
    mig.READ_LABEL: "a name or a note, not a task",
    mig.READ_DATE_PASSED: "its date has passed",
}


def _events_path(workspace_root) -> Path:
    return Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _owner(workspace_root) -> str:
    try:
        from primary_user import resolve_primary_user

        return str(resolve_primary_user(workspace_root) or "").strip()
    except Exception:  # noqa: BLE001 - a seat with no owner names nobody
        return ""


def _open_moved_tasks(workspace_root) -> List[Dict[str, Any]]:
    """The tasks the migration wrote that are still open, by its own marker."""
    from cru_match import load_open_commitments

    out = []
    for ev in load_open_commitments(_events_path(workspace_root)):
        d = ev.get("data") or {}
        if (ev.get("source_skill") == mig.SOURCE_SKILL
                and d.get("source") == DISCUSS_MOVED_VIA
                and d.get("source_event_seq") is not None):
            out.append(ev)
    return out


def _owner_touched(events: List[Dict[str, Any]], task: Dict[str, Any]) -> bool:
    """Has anyone but a background pass touched this task since it was
    written (a date, a title, a priority, a snooze)? The repair leaves such a
    task alone: the owner has worked on it (review)."""
    from event_types import is_machine_source

    d = task.get("data") or {}
    keys = {str(d.get("id") or ""), str(task.get("seq") or "")} - {""}
    from event_seq import event_seq

    seq = event_seq(task, context="repair_discuss_list_tasks")
    if seq is None:
        return False  # a task with no readable seq has no "since" to read
    for ev in events:
        later = event_seq(ev, context="repair_discuss_list_tasks")
        if later is None or later <= seq or ev is task:
            continue
        src = ev.get("source_skill")
        if src == mig.SOURCE_SKILL or is_machine_source(src):
            continue
        ed = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        named = {str(ed.get(k)) for k in ("commitment_id", "commitment_seq", "target_id",
                                          "item_id") if ed.get(k) is not None}
        if named & keys:
            return True
    return False


def plan(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """What a repair would do. A pure read: nothing is written.

    Each item is read as of the day its task was written (the day the
    migration ran), not today: a weekday that was ahead then made a dated
    task under the fixed rules even if that day has passed since (review).

    Returns `{status, to_note: [...], to_date: [...], left: [...], lines:
    [...]}`. Each `to_note` row is `{commitment_id, title, discuss_seq,
    read_as, target_kind, target_id}`; each `to_date` row `{commitment_id,
    title, discuss_seq, due}`; each `left` row `{commitment_id, title, why}`
    is a task the repair will not touch. `lines` are what the owner is shown."""
    from commitment_state import effective_due

    out: Dict[str, Any] = {"status": STATUS_NOTHING, "to_note": [], "to_date": [], "left": [],
                           "lines": []}
    events = mig._events(workspace_root)
    by_seq = {ev.get("seq"): ev for ev in events if ev.get("type") == "commitment_to_discuss"}
    entities = mig._entities(workspace_root)
    today = mig._day_of((now_iso or "").strip() or _now_iso(), workspace_root)
    owner = _owner(workspace_root)
    path = _events_path(workspace_root)
    for task in _open_moved_tasks(workspace_root):
        d = task.get("data") or {}
        discuss = by_seq.get(d.get("source_event_seq"))
        if discuss is None:
            continue  # the item it moved is not on file: nothing to read it by
        cid = str(d.get("id") or "")
        if not cid:
            continue
        title = str(d.get("title") or "")
        then = mig._day_of(task.get("ts"), workspace_root) or today
        read = mig.read_item(discuss, entities, today=then, workspace_root=workspace_root)
        wrong = read["read_as"] != mig.READ_TASK
        dated = bool(read["due"]) and not wrong
        if not wrong and not dated:
            continue
        if _owner_touched(events, task):
            out["left"].append({"commitment_id": cid, "title": title, "why": "changed"})
            out["lines"].append(f'"{title}": left as it is, because you have changed it since.')
            continue
        if wrong:
            kind, target = read["name_target"]
            if not kind and not owner:
                continue  # nobody to hold the note: leave the task as it is
            out["to_note"].append({"commitment_id": cid, "title": title,
                                   "discuss_seq": discuss.get("seq"),
                                   "read_as": read["read_as"],
                                   "target_kind": kind or "person",
                                   "target_id": target or owner})
            out["lines"].append(f'"{title}" ({_READ_WORDS[read["read_as"]]}): '
                                "close the task, keep the words as a note.")
            continue
        try:
            has_due = bool(effective_due(path, cid))
        except Exception:  # noqa: BLE001 - an unreadable date is no date
            has_due = False
        if has_due:
            continue
        if today is not None and read["due"] < today.isoformat():
            # The fixed rules would have dated it, but that day is gone and a
            # date cannot be set in the past: left for the owner.
            out["left"].append({"commitment_id": cid, "title": title, "why": "date_gone"})
            out["lines"].append(f'"{title}": left as it is; the day its words named '
                                f'({read["due"]}) has passed.')
            continue
        out["to_date"].append({"commitment_id": cid, "title": title,
                               "discuss_seq": discuss.get("seq"), "due": read["due"]})
        out["lines"].append(f'"{title}": due {read["due"]}, the day its words name.')
    if out["to_note"] or out["to_date"]:
        out["status"] = STATUS_PENDING
    return out


def snapshot(workspace_root) -> Optional[Path]:
    """Copy events.jsonl into `_archive/` before any write. None, and nothing
    created, when there is no events.jsonl at that root."""
    src = _events_path(workspace_root)
    if not src.exists():
        return None
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest_dir = Path(str(workspace_root)) / "_archive" / f"events-jsonl_discuss-repair-snapshot_{stamp}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "events.jsonl"
    shutil.copy2(src, dest)
    return dest


def apply(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot, then make every planned change, then one record row.
    Returns `{status, snapshot, noted, dated, errors}` (`noted` and `dated`
    are commitment ids).

    Each wrong task's note is written BEFORE the task is closed, and the
    record row is written whatever happens after the snapshot, so a run that
    stops partway leaves nothing `--undo` cannot find (review). A note
    already written by an earlier repair for the same task is not written
    twice."""
    from commitment_state import close_commitment
    from due_set import set_due
    from event_gate import append_event

    planned = plan(workspace_root, now_iso=now_iso)
    result: Dict[str, Any] = {"status": planned["status"], "snapshot": None,
                              "noted": [], "dated": [], "errors": []}
    if planned["status"] != STATUS_PENDING:
        return result
    snap = snapshot(workspace_root)
    if snap is None:
        result["status"] = "refused"
        result["errors"].append("no events.jsonl in that folder")
        return result
    result["snapshot"] = str(snap.relative_to(Path(str(workspace_root)))).replace("\\", "/")
    owner = _owner(workspace_root) or mig.SOURCE_SKILL
    path = _events_path(workspace_root)
    noted_before = {str((ev.get("data") or {}).get("moved_from_commitment"))
                    for ev in mig._events(workspace_root) if ev.get("type") == "note"
                    and (ev.get("data") or {}).get("moved_from_commitment")}
    # The record names a date it set by the date row's seq only: a row that
    # names the item itself reads as a later touch, and `undo_set_due` would
    # then refuse to take the date back.
    due_seqs: List[int] = []
    try:
        for row in planned["to_note"]:
            cid = row["commitment_id"]
            try:
                if cid not in noted_before:
                    text = (str(row["title"] or "").strip()
                            or mig.UNTITLED_TEXT.format(seq=row["discuss_seq"]))
                    note: Dict[str, Any] = {
                        "type": "note", "source_skill": mig.SOURCE_SKILL,
                        "data": {"summary": text, "via": DISCUSS_MOVED_VIA,
                                 "source_event_seq": row["discuss_seq"],
                                 "read_as": row["read_as"], "moved_from_commitment": cid,
                                 "evidence": mig.MOVED_NOTE_LINE},
                    }
                    if row["target_kind"] == "thread":
                        note["primary_thread_id"] = row["target_id"]
                    else:
                        note["data"]["person_id"] = row["target_id"]
                    append_event(path, [note], holder=mig.SOURCE_SKILL)
                close_commitment(workspace_root, cid, resolved_by=owner,
                                 evidence=CLOSE_EVIDENCE, source_skill=mig.SOURCE_SKILL,
                                 resolution="superseded", user_confirmed=True)
            except Exception as exc:  # noqa: BLE001 - one bad row never stops the rest
                result["errors"].append(f"{cid}: {exc}")
                continue
            result["noted"].append(cid)
        for row in planned["to_date"]:
            try:
                got = set_due(workspace_root, row["commitment_id"], when_iso=row["due"],
                              actor_id=owner, source_skill=mig.SOURCE_SKILL,
                              reason=DATE_REASON, now_iso=now_iso)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f'{row["commitment_id"]}: {exc}')
                continue
            if got.get("status") == "set":
                result["dated"].append(row["commitment_id"])
                due_seqs.append(got.get("seq"))
            else:
                result["errors"].append(f'{row["commitment_id"]}: {got.get("ack") or got.get("status")}')
    finally:
        record = {"type": mig.APPLIED_EVENT, "source_skill": mig.SOURCE_SKILL,
                  "data": {"migration_id": REPAIR_ID, "target_file": "_hq/data/events.jsonl",
                           "actor": owner, "snapshot": result["snapshot"],
                           "closed_to_notes": result["noted"], "due_rows": due_seqs,
                           "errors": len(result["errors"])}}
        append_event(path, [record], holder=mig.SOURCE_SKILL)
    result["status"] = STATUS_REPAIRED
    return result


def undo(workspace_root) -> Dict[str, Any]:
    """Reopen every task the last repair closed and take back every date it
    set. Returns `{status, reopened, undated, errors}`.

    The undo's own record row is written only when every step landed; with
    an error the repair's row stays the last one, so running `--undo` again
    retries (a task already reopened and a date already taken back count as
    done)."""
    from commitment_state import reopen_commitment
    from cru_match import load_open_commitments
    from due_set import undo_set_due
    from event_gate import append_event

    result: Dict[str, Any] = {"status": STATUS_NOTHING_TO_UNDO, "reopened": [],
                              "undated": [], "errors": []}
    events = mig._events(workspace_root)
    rows = [ev for ev in events if ev.get("type") == mig.APPLIED_EVENT
            and (ev.get("data") or {}).get("migration_id") in (REPAIR_ID, REPAIR_ID + "_undo")]
    if not rows or (rows[-1].get("data") or {}).get("migration_id") != REPAIR_ID:
        return result
    last = rows[-1]["data"]
    owner = _owner(workspace_root) or mig.SOURCE_SKILL
    for due_seq in last.get("due_rows") or []:
        try:
            got = undo_set_due(workspace_root, due_seq, undone_by=owner,
                               source_skill=mig.SOURCE_SKILL)
            status = str(got.get("status"))
            if status not in ("undo_refused", "None"):
                result["undated"].append(str(got.get("commitment_id") or due_seq))
            else:
                result["errors"].append(f"date row {due_seq}: {got.get('why') or status}")
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"date row {due_seq}: {exc}")
    open_ids = {str((c.get("data") or {}).get("id"))
                for c in load_open_commitments(_events_path(workspace_root))}
    for cid in last.get("closed_to_notes") or []:
        if cid in open_ids:
            result["reopened"].append(cid)  # reopened by an earlier try
            continue
        try:
            reopen_commitment(workspace_root, cid, reopened_by=owner,
                              reason="Undo of the discuss-later repair.",
                              source_skill=mig.SOURCE_SKILL, user_confirmed=True)
            result["reopened"].append(cid)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{cid}: {exc}")
    if result["errors"]:
        result["status"] = "partial"
        return result
    append_event(_events_path(workspace_root), [{
        "type": mig.APPLIED_EVENT, "source_skill": mig.SOURCE_SKILL,
        "data": {"migration_id": REPAIR_ID + "_undo", "target_file": "_hq/data/events.jsonl",
                 "actor": owner, "reopened": result["reopened"],
                 "undated": result["undated"]}}],
        holder=mig.SOURCE_SKILL)
    result["status"] = STATUS_UNDONE
    return result


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    flags = {a for a in args if a.startswith("--")}
    roots = [a for a in args if not a.startswith("--")]
    if len(roots) != 1 or flags - {"--apply", "--undo"} or flags == {"--apply", "--undo"}:
        print(__doc__.split("USAGE")[1].strip(), file=sys.stderr)
        return 2
    root = roots[0]
    if not _events_path(root).exists():
        print(f"No events.jsonl under {root!r}; nothing done.", file=sys.stderr)
        return 2
    if "--undo" in flags:
        print(json.dumps(undo(root), indent=2))
        return 0
    if "--apply" in flags:
        print(json.dumps(apply(root), indent=2))
        return 0
    planned = plan(root)
    if planned["status"] == STATUS_NOTHING and not planned["lines"]:
        print("Nothing to repair: no open task from the discuss-later move reads as anything but a task.")
        return 0
    print(f"{len(planned['lines'])} change(s) the repair would make (nothing written):")
    for line in planned["lines"]:
        print("  - " + line)
    print("Run again with --apply to make them (events.jsonl is copied to _archive/ first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
