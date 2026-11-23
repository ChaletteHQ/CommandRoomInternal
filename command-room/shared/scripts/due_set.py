#!/usr/bin/env python3
"""due_set.py: set a commitment's due date, and only that, with an undo
(cr1#1383, owner ruling 2026-11-19, option B).

WHY THIS EXISTS
Nothing in the product set a due date on its own. Later
(`commitment_state.apply_later`) moves the owner's own item's date AND hides
it until that day (owner ruling 2026-11-17, cr1#1292); on an item owed to
the owner it moves no date, it only hides it. `restore_due` sets a date only
as the undo of one move. So a Hub or chat edit that asked for a new date had
no writer. The owner ruled for a new one: it only sets the date, it never
hides the item, it works on items owed to the owner too, and its undo
refuses if anything newer touched the item.

THE WRITE. One `commitment_updated` through the event gate, inside the
events writer lock, the shape every due change has (`new_due`, `prior_due`,
read by `cru_match.load_open_commitments`), with change class
`commitment_due_set` and its own `brain_batch_id`, so a bare `undo` lists
it. It carries no hide of its own. The hide a Later put on the item is read
off the item's newest due change (`mute_ledger.item_snoozes`), so a new date
change with no `hidden_until` would bring a hidden item back early: a hide
still running is copied onto this row unchanged, so the item stays exactly
as visible as it was.

Refusals write nothing: a closed item (`not_open`), an item waiting on an
event (`waiting`: a waiting item holds no date), a day already gone
(`in_the_past`), the date it already has (`unchanged`), an unreadable date
(ValueError before the lock). A live hide carried over is never lengthened:
a nearer date shortens it to the new day.

`check_due` (cr1#1383 slice 4, PR 2) answers what `set_due` would do,
writing nothing: the Hub's Edit runs it before it writes the wording of a
press that also carries a date, so a press is refused whole, never half
applied. `set_due` itself runs the same `_verdict` inside its lock.

NO ROW, NO "SET" (corrects PR #1467). When the event gate lands no row,
`set_due` answers `not_recorded`, naming the item, and never `set` with an
event that is not on the book.

THE UNDO. `undo_set_due(seq)`, which the registered reverser for
`commitment_due_set` calls (`brain_undo`). It refuses whole when ANY later
row names the item (a wording fix, a wait, a snooze, another date, a close),
except an undo marker or a Hub press row, which change nothing on it. Else
the date goes back through `commitment_state.restore_due` by this change's
seq, which puts back exactly the date this change replaced, or no date.

The chat's door is `due_from_chat`, on the write door, by the row's number.
stdlib only. Nothing here sends anything.
"""
from __future__ import annotations

import datetime
import json
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

CHANGE_CLASS = "commitment_due_set"
BATCH_PREFIX = "dset_"
STATUS_SET = "set"
STATUS_UNCHANGED = "unchanged"
STATUS_NOT_OPEN = "not_open"
STATUS_PAST = "in_the_past"
STATUS_UNDO_REFUSED = "undo_refused"
STATUS_WAITING = "waiting"
#: cr1#1383 (corrects PR #1467): no row landed, so nothing was set.
STATUS_NOT_RECORDED = "not_recorded"
#: `check_due`'s answer when `set_due` would write.
STATUS_OK = "ok"
#: Rows that name an item without changing it: an undo marker, a Hub press.
NOT_A_TOUCH = frozenset({"brain_change_undone", "hub_edit",
                         # cr1#1632 slice 3: the wrap-up row names what it placed
                         "hub_meeting_wrapped"})
CHAT_SOURCE_SKILL = "apply-choices"
CHAT_SURFACES = ("plate", "morning-brief")

SET_ACK = "{title} is now due {day}."
UNCHANGED_ACK = "{title} is already due {day}; nothing was written."
NOT_OPEN_ACK = "That item is closed, so its date was not changed."
WAITING_ACK = ("{title} is waiting on something, so its date was not changed. "
               "Say it is not waiting first, then give it the date.")
PAST_ACK = "{day} has already passed, so nothing was written."
NO_ACTOR_ACK = "Nothing was written: the change needs to know who asked for it."
NO_SUCH_ROW_ACK = "No item carries the number {n}; nothing was written."
GONE_ACK = "That item is no longer on your list; nothing was written."
BAD_DATE_ACK = "That is not a date I can set; nothing was written."
BAD_SURFACE_ACK = "Nothing was written: that list has no numbers to go by."
NOT_RECORDED_ACK = "{title}: the new date could not be recorded, so nothing changed. Try again."


def _events_path(workspace_root) -> Path:
    return Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"


def _load(path: Path) -> list:
    """The owner's own ledger through the owner-scoped reader every owner
    surface uses, which is the merged view across computers' logs."""
    from events_io import load_events_owner_scoped
    if not path.exists():
        return []
    return [e for e in load_events_owner_scoped(str(path.parents[2]))[0] if isinstance(e, dict)]


def _seq(ev: dict):
    from event_seq import event_seq
    return event_seq(ev)


def _item_keys(events: list, cid: str) -> set:
    """Every spelling that names the item: its id and its seq spellings, as
    the mute ledger reads a hide's target (`mute_ledger._item_keys`)."""
    try:
        from mute_ledger import _item_keys as keys_of
        return set(keys_of(events, cid)) | {cid}
    except Exception:  # noqa: BLE001 - the id itself is the floor
        return {cid}


def _names(ev: dict, keys: set) -> bool:
    """Does the row name the item under any of its spellings? A string value
    anywhere in the row counts, so a doubt counts as a touch (a refusal)."""
    text = json.dumps(ev, ensure_ascii=False, default=str)
    return any(json.dumps(k, ensure_ascii=False) in text for k in keys if k)


def _live_hide(events: list, cid: str, now_iso: str) -> Optional[str]:
    """The `hidden_until` on the item's newest due change, while it runs."""
    newest = None
    for ev in events:
        if (ev.get("type") or ev.get("event")) != "commitment_updated":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        target = d.get("commitment_id") or d.get("target_id") or ev.get("commitment_id")
        if not target or str(target) != cid:
            continue
        if d.get("new_due") or d.get("due") or d.get("due_date") or d.get("due_cleared") is True:
            newest = d.get("hidden_until")
    if not newest:
        return None
    try:
        end = datetime.datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
        now = datetime.datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return str(newest) if end > now else None


def _when(stamp: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def _title(target: dict) -> str:
    import commitment_state as cs
    t = cs._commitment_field(target, "title") or (target.get("data") or {}).get("summary") or ""
    return " ".join(str(t).split())[:80] or "That item"


def _day_words(day: str, workspace_root) -> str:
    try:
        from due_reanchor import render_moved_phrase
        return render_moved_phrase(day, workspace_path=str(workspace_root)) or day
    except Exception:  # noqa: BLE001 - the date itself is the floor
        return day


def _past(workspace_root, commitment_id, calendar_day: str, now: str) -> Optional[Dict[str, Any]]:
    """The `in_the_past` answer when the day is gone, else None."""
    import commitment_state as cs
    today = cs._now_calendar_day(now, str(workspace_root))
    if today is not None and calendar_day < today.isoformat():
        return {"status": STATUS_PAST, "commitment_id": str(commitment_id), "due": calendar_day,
                "ack": PAST_ACK.format(day=_day_words(calendar_day, workspace_root))}
    return None


def _verdict(workspace_root, path: Path, commitment_id, calendar_day: str, now: str) -> Dict[str, Any]:
    """What a date write on the item would meet, read only: `{status: ok,
    cid, target, title, prior}`, or `{status, answer}` with the refusal
    `set_due` answers. Raises `commitment_state.CommitmentIdError` on no
    such item. Called inside `set_due`'s lock, and by `check_due` outside it."""
    import commitment_state as cs
    index = cs._scan_commitment_index(path)
    cid = cs.normalize_commitment_id(commitment_id, index)
    target = index["by_id"][cid]
    title = _title(target)
    if cs._currently_closed(index, cid, target.get("seq")):
        return {"status": STATUS_NOT_OPEN,
                "answer": {"status": STATUS_NOT_OPEN, "commitment_id": cid, "ack": NOT_OPEN_ACK}}
    from commitment_wait import waiting_ids
    if cid in waiting_ids(path, now):
        # A waiting item holds no date (`wait_on` clears it); setting one
        # beside the wait would be a date no list shows. Nothing written.
        return {"status": STATUS_WAITING, "answer": {"status": STATUS_WAITING, "commitment_id": cid,
                                                     "ack": WAITING_ACK.format(title=title)}}
    prior = cs.effective_due(path, cid)
    if prior and str(prior)[:10] == calendar_day:
        return {"status": STATUS_UNCHANGED, "answer": {
            "status": STATUS_UNCHANGED, "commitment_id": cid, "due": calendar_day,
            "ack": UNCHANGED_ACK.format(title=title, day=_day_words(calendar_day, workspace_root))}}
    return {"status": STATUS_OK, "cid": cid, "target": target, "title": title, "prior": prior}


def check_due(workspace_root, commitment_id, *, when_iso: str,
              now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. What `set_due` would answer for this date, writing nothing:
    `{status: "ok", commitment_id, due, prior_due, title}` when it would set
    the date, else the very refusal `set_due` answers (`in_the_past`,
    `not_open`, `waiting`, `unchanged`). Raises as `set_due` does on a date
    it cannot read or no such item. Read without the lock, so `set_due`
    judges again when it writes."""
    import commitment_state as cs
    calendar_day, _stamp = cs._later_when(str(when_iso))
    now = now_iso or cs._now_iso()
    past = _past(workspace_root, commitment_id, calendar_day, now)
    if past:
        return past
    v = _verdict(workspace_root, _events_path(workspace_root), commitment_id, calendar_day, now)
    if v["status"] != STATUS_OK:
        return v["answer"]
    return {"status": STATUS_OK, "commitment_id": v["cid"], "due": calendar_day, "prior_due": v["prior"],
            "title": v["title"]}


def set_due(workspace_root, commitment_id, *, when_iso: str, actor_id: str,
            source_skill: str, reason: str = "due date set",
            brain_batch_id: Optional[str] = None,
            now_iso: Optional[str] = None) -> Dict[str, Any]:
    """WRITE. Set `commitment_id`'s due date to the calendar day of
    `when_iso` (already resolved, as `apply_later` takes it), and nothing
    else. Answers `{status, commitment_id, due, prior_due, batch_id, ack,
    event}`; see the module docstring for the refusals, each of which
    writes nothing. Raises ValueError on a date it cannot read, before the
    lock, and `commitment_state.CommitmentIdError` on no such item."""
    import commitment_state as cs
    from atomic_write import events_write_section as events_writer_lock

    calendar_day, _stamp = cs._later_when(str(when_iso))
    now = now_iso or cs._now_iso()
    past = _past(workspace_root, commitment_id, calendar_day, now)
    if past:
        return past
    path = _events_path(workspace_root)
    with events_writer_lock(path, holder=f"set_due:{source_skill}"):
        v = _verdict(workspace_root, path, commitment_id, calendar_day, now)
        if v["status"] != STATUS_OK:
            return v["answer"]
        cid, target, title, prior = v["cid"], v["target"], v["title"], v["prior"]
        batch = str(brain_batch_id or (BATCH_PREFIX + datetime.datetime.now(datetime.timezone.utc)
                                       .strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(3)))
        data: Dict[str, Any] = {
            "commitment_id": cid,
            "new_due": calendar_day,
            "prior_due": prior,
            "set_by": actor_id,
            "reason": (reason or "")[:200],
            "brain_change_class": CHANGE_CLASS,
            "brain_batch_id": batch,
        }
        hide = _live_hide(_load(path), cid, now)
        if hide:
            # Carried, never added or lengthened: a hide ends on its own due
            # day, so a nearer date shortens it to the new day.
            day_end = _stamp
            data["hidden_until"] = hide if _when(hide) <= _when(day_end) else day_end
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        ev = {"type": "commitment_updated", "source_skill": source_skill,
              "primary_thread_id": target.get("primary_thread_id") or "", "data": data}
        from event_gate import append_event
        landed = append_event(path, [ev], holder=source_skill)
    row = landed[0] if isinstance(landed, list) and landed and isinstance(landed[0], dict) else None
    if row is None or _seq(row) is None:
        # Corrects PR #1467: a row the gate did not land is not a date set.
        return {"status": STATUS_NOT_RECORDED, "commitment_id": cid,
                "ack": NOT_RECORDED_ACK.format(title=title)}
    return {"status": STATUS_SET, "commitment_id": cid, "due": calendar_day, "prior_due": prior,
            "batch_id": batch, "seq": _seq(row), "event": row,
            "ack": SET_ACK.format(title=title, day=_day_words(calendar_day, workspace_root))}


def _plan_before(events: List[Dict[str, Any]], cid: str, seq, prior_due) -> Optional[Dict[str, Any]]:
    """Review of #1606 (coordinator decision): the Week plan block of the
    move this date replaced, so its Undo brings the block back. The newest
    date move on the item before `seq`; its block when it planned the very
    day being put back, else None."""
    prev = None
    for e in events:
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if ((_seq(e) or 0) < seq and e.get("type") == "commitment_updated"
                and str(d.get("commitment_id") or "") == cid and ("new_due" in d or d.get("due_cleared"))):
            prev = d
    if not prev or "plan_at" not in prev or str(prev.get("new_due") or "")[:10] != str(prior_due or "")[:10]:
        return None
    return {"plan_at": prev.get("plan_at"), "plan_minutes": prev.get("plan_minutes"),
            "plan_batch_id": str(prev.get("brain_batch_id") or prev.get("plan_batch_id") or "")}


def undo_set_due(workspace_root, seq, *, undone_by: str, source_skill: str) -> Dict[str, Any]:
    """WRITE. Take back the date set by the `commitment_due_set` row at
    `seq`. Refused, nothing written, when any later row touched the item
    (`why: changed`), when the row is not one of this writer's
    (`unrecorded`), or when it was taken back before (`already_undone`)."""
    import commitment_state as cs
    from atomic_write import events_write_section as events_writer_lock

    path = _events_path(workspace_root)
    # The verdict and the write under one hold of the events lock (it is
    # re-entrant, and `restore_due` takes it again), so nothing can land on
    # the item between the check and the put-back.
    with events_writer_lock(path, holder=f"undo_set_due:{source_skill}"):
        events = _load(path)
        act = next((e for e in events if _seq(e) == seq), None)
        d = (act or {}).get("data") if isinstance((act or {}).get("data"), dict) else {}
        cid = str(d.get("commitment_id") or "")
        if not act or d.get("brain_change_class") != CHANGE_CLASS or not cid:
            return {"status": STATUS_UNDO_REFUSED, "commitment_id": cid, "why": "unrecorded"}
        keys = _item_keys(events, cid)
        later = [e for e in events if (_seq(e) or 0) > seq and _names(e, keys)]
        if any((e.get("data") or {}).get("reversal_of") == seq for e in later
               if isinstance(e.get("data"), dict)):
            return {"status": "already_undone", "commitment_id": cid}
        if any((e.get("type") or e.get("event")) not in NOT_A_TOUCH for e in later):
            return {"status": STATUS_UNDO_REFUSED, "commitment_id": cid, "why": "changed"}
        out = cs.restore_due(str(workspace_root), cid, prior_due=cs.DUE_FROM_MOVE,
                             actor_id=undone_by, source_skill=source_skill,
                             reason="due date put back", undoing_seq=seq,
                             plan=_plan_before(events, cid, seq, d.get("prior_due")))
    if out.get("status") == "undo_refused" and out.get("why") == "newer_date":
        out = dict(out, why="changed")
    out.pop("event", None)
    return out


def due_from_chat(workspace_root, *, n: str = "", when_iso: str = "", resolved_by: str = "",
                  surface: str = "plate", now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (on `RUN_WRITER_ALLOWLIST` only): a due date set in chat by
    the row's number. `when_iso` is the day the owner named, resolved by
    the chat (this writer parses no words). The number resolves as every
    typed verb on that surface does (the plate's own numbers, or the
    morning brief's recorded list). A refusal writes nothing. The answer is
    the writer's own plus `ok`, `n` and `ack`, the one sentence to post."""
    actor = str(resolved_by or "").strip()
    if not actor:
        return {"ok": False, "status": "no_actor", "ack": NO_ACTOR_ACK}
    where = str(surface or "plate").strip().lower()
    if where not in CHAT_SURFACES:
        return {"ok": False, "status": "bad_surface", "ack": BAD_SURFACE_ACK}
    num = str(n or "").strip()
    if where == "morning-brief":
        from brief_receipt import resolve_mark_done
        target = resolve_mark_done(str(workspace_root), num)
        if not target.get("ok"):
            return {"ok": False, "status": "no_such_row", "n": num,
                    "ack": str(target.get("refusal") or GONE_ACK)}
        cid = str(target["id"])
    else:
        from plate_view import resolve_display_number
        cid = resolve_display_number(str(workspace_root), num) if num else None
        if cid is None:
            return {"ok": False, "status": "no_such_row", "n": num, "ack": NO_SUCH_ROW_ACK.format(n=num or "?")}
    import commitment_state as cs
    try:
        out = set_due(workspace_root, cid, when_iso=when_iso, actor_id=actor,
                      source_skill=CHAT_SOURCE_SKILL, now_iso=now_iso)
    except cs.CommitmentIdError:  # a ValueError too, so caught first
        return {"ok": False, "status": "no_such_row", "n": num, "ack": GONE_ACK}
    except ValueError:
        return {"ok": False, "status": "bad_date", "n": num, "ack": BAD_DATE_ACK}
    out = dict(out)
    out.pop("event", None)
    return {**out, "ok": out.get("status") == STATUS_SET, "n": num, "surface": where}
