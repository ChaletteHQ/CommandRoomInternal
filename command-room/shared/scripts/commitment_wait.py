#!/usr/bin/env python3
"""An item that waits on an event instead of a date (cr1#1291).

The owner's ruling of 2026-11-16 (ranking brief change 2, simple form): an
item can be saved as "no date, waiting on: [event]". It never shows overdue
and never leads the top line. It comes back when the owner clears the wait,
or when, 14 days on, the product asks whether it still waits (the rhythm
undated priorities use: `thread_cards.QUIET_DAYS`). Detecting the event on
its own is out of scope.

HOW IT IS HELD
==============
A wait is a park (`commitment_state.park_commitment`'s event shape, one
`commitment_updated` with `status_hint: parked`) that also carries
`wait_on` (the event, in the owner's words) and `wait_since`. The same
event clears the item's date (`new_due: null` + `due_cleared: true`, with
`prior_due` kept), so the item is undated while it waits and cannot fall
overdue. Because it is a park, the loader folds it with no new reader, the
plate puts it under Parked with its reason line, and the top line, which
reads only the plate's Do it and Confirm rows, never offers it. Clearing
the wait is an un-park (`status_hint: null`), which puts the item back in
its own lane.

THE ONE READING
===============
`item_waits` is the answer to "is this item waiting, on what, since when,
and is it time to ask" for every surface (the plate, the open list, the
Later writer). It reads the same event the loader's park fold reads, by the
same rule: the newest `commitment_updated` on the item that carries the
`status_hint` key. A wait is live when that event is a park carrying
`wait_on`; any later park, un-park or Later replaces it.

Writers go through `event_gate.append_event` inside the events writer lock.
stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re as _re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: Days a wait holds before the product asks whether it still waits.
WAIT_ASK_DAYS = 14
WAIT_TEXT_CAP = 120
#: The words a typed answer starts with to set a wait, and to end one.
_WAIT_RE = _re.compile(r"^\s*(?:still\s+)?wait(?:ing)?\s+(?:on|for)\s*:?\s*(.*?)\s*\.?\s*$",
                       _re.IGNORECASE)
_CLEAR_RE = _re.compile(r"^\s*(?:not\s+waiting|no\s+longer\s+waiting|stop\s+waiting|"
                        r"done\s+waiting|clear\s+(?:the\s+)?wait)\b.*$", _re.IGNORECASE)

WAIT_REASON = "waiting on {event}"
WAIT_ASK = "Still waiting on {event}?"
WAIT_SET_ACK = "{title}: no date, waiting on {event}. In {days} days you will be asked if it still waits."
WAIT_SET_ACK_BARE = "No date, waiting on {event}. In {days} days you will be asked if it still waits."
WAIT_SAME_ACK = "Already waiting on {event}. Nothing changed."
WAIT_CLEARED_ACK = "{title}: no longer waiting. It is back on your list."
WAIT_CLEARED_ACK_BARE = "No longer waiting. It is back on your list."
WAIT_NONE_ACK = "That one was not waiting on anything. Nothing changed."
WAIT_NEEDS_EVENT_ACK = "Say what it waits on, for example: waiting on the contract signing."
WAIT_CLOSED_ACK = "That one is already closed. Nothing changed."
#: Statuses, from the vocabulary the apply audit already classifies.
STATUS_SET = "updated"
STATUS_SAME = "unchanged"
STATUS_CLEARED = "cleared"
STATUS_NOT_OPEN = "not_open"
STATUS_NEEDS_EVENT = "missing_when"
LANDED = frozenset({STATUS_SET, STATUS_CLEARED, STATUS_SAME})


def _parse_dt(value) -> Optional[_dt.datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def _target(d: dict, ev: dict) -> str:
    """The item an update names, by the loader's own chain."""
    return str(d.get("commitment_id") or d.get("target_id") or ev.get("commitment_id") or "")


def item_waits(events: Iterable[dict], now_iso: str) -> dict:
    """{commitment id: {wait_on, since, ask_at, asking}} for every item whose
    newest park-hint update is a wait (cr1#1291). Pure.

    `asking` is True once `WAIT_ASK_DAYS` have passed since the wait was set
    (or last confirmed): the item then carries the question "Still waiting
    on ...?" on every surface that shows it. Open or closed is the caller's
    business; a closed item is on no surface to begin with."""
    now = _parse_dt(now_iso)
    newest: dict = {}
    for ev in events:
        if not isinstance(ev, dict) or (ev.get("type") or ev.get("event")) != "commitment_updated":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        tgt = _target(d, ev)
        if tgt and "status_hint" in d:
            newest[tgt] = (d, ev)
    out: dict = {}
    for cid, (d, ev) in newest.items():
        text = " ".join(str(d.get("wait_on") or "").split())
        if d.get("status_hint") != "parked" or not text:
            continue
        since = _parse_dt(d.get("wait_since")) or _parse_dt(ev.get("ts"))
        ask_at = since + _dt.timedelta(days=WAIT_ASK_DAYS) if since else None
        out[cid] = {"wait_on": text,
                    "since": since.isoformat() if since else "",
                    "ask_at": ask_at.isoformat() if ask_at else "",
                    "asking": bool(now and ask_at and now >= ask_at)}
    return out


def wait_reason(wait: dict) -> str:
    """The row's reason line: the question once it is time to ask, else
    what it waits on."""
    event = wait.get("wait_on") or ""
    return WAIT_ASK.format(event=event) if wait.get("asking") else WAIT_REASON.format(event=event)


def parse_wait_text(text) -> Optional[tuple]:
    """("wait", event) for "waiting on <event>" / "wait for <event>",
    ("wait", "") when the event is missing, ("clear", "") for "not waiting"
    / "no longer waiting", None for anything else (a date, or other words)."""
    s = " ".join(str(text or "").split())
    if not s:
        return None
    if _CLEAR_RE.match(s):
        return ("clear", "")
    m = _WAIT_RE.match(s)
    if m:
        return ("wait", m.group(1).strip()[:WAIT_TEXT_CAP])
    return None


def _now_iso(workspace_root) -> str:
    try:
        from trusted_now import trusted_now_utc
        now = trusted_now_utc(workspace_root)
    except Exception:  # noqa: BLE001 - the machine clock is the floor
        now = _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _title(target: dict) -> str:
    from cru_match import _commitment_field
    return " ".join(str(_commitment_field(target, "title")
                        or (target.get("data") or {}).get("summary") or "").split())[:80]


def _load(events_path) -> list:
    """The owner's own ledger, through the owner-scoped reader every owner
    surface uses (`events_path` is `<workspace>/_hq/data/events.jsonl`)."""
    from events_io import load_events_owner_scoped
    return load_events_owner_scoped(str(Path(events_path).parents[2]))[0]


def end_wait_event(events_path, target: dict, cid: str, *, actor_id: str,
                   source_skill: str, reason: str, now_iso: str) -> Optional[dict]:
    """The un-park that ends a live wait on `cid`, or None when it does not
    wait. For a writer already inside the events lock (the Later writer:
    a date given to a waiting item replaces the wait)."""
    if cid not in item_waits(_load(events_path), now_iso):
        return None
    data = {"commitment_id": cid, "status_hint": None, "unparked": True,
            "wait_on": None, "wait_cleared": True,
            "reason": str(reason or "the wait is over")[:200], "unparked_by": actor_id}
    if isinstance(target.get("seq"), int):
        data["commitment_seq"] = target["seq"]
    return {"type": "commitment_updated", "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "", "data": data}


def wait_on(workspace_root, commitment_id, *, event: str, actor_id: str,
            source_skill: str, now_iso: Optional[str] = None) -> dict:
    """Hold an open item with no date, waiting on `event` (cr1#1291).

    One `commitment_updated`: a park with `wait_on` and `wait_since`, and,
    when the item had a date, the clear of that date (`prior_due` kept, so
    the date can be put back). Saying the same wait again before the ask is
    due writes nothing (`unchanged`); after it, the wait is confirmed and
    the 14 days start again. Refuses a closed item (`not_open`) and a blank
    event (`missing_when`). Raises `CommitmentIdError` on an unknown id."""
    import commitment_state as cs
    from atomic_write import events_write_section
    from event_gate import append_event

    text = " ".join(str(event or "").split())[:WAIT_TEXT_CAP]
    if not text:
        return {"status": STATUS_NEEDS_EVENT, "commitment_id": str(commitment_id),
                "ack": WAIT_NEEDS_EVENT_ACK}
    now_iso = now_iso or _now_iso(workspace_root)
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_write_section(events_path, holder=f"wait_on:{source_skill}"):
        index = cs._scan_commitment_index(events_path)
        cid = cs.normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if cs._currently_closed(index, cid, target.get("seq")):
            return {"status": STATUS_NOT_OPEN, "commitment_id": cid, "ack": WAIT_CLOSED_ACK}
        live = item_waits(_load(events_path), now_iso).get(cid)
        if live and not live["asking"] and live["wait_on"].casefold() == text.casefold():
            return {"status": STATUS_SAME, "commitment_id": cid, "wait_on": text,
                    "ack": WAIT_SAME_ACK.format(event=text)}
        prior_due = cs.effective_due(events_path, cid)
        reason = WAIT_REASON.format(event=text)
        data = {"commitment_id": cid, "status_hint": cs.STATUS_HINT_PARKED,
                "park_reason": reason, "reason": reason, "parked_by": actor_id,
                "wait_on": text, "wait_since": now_iso,
                cs.REASON_ORIGIN_KEY: cs.park_reason_origin(actor_id, source_skill)}
        if prior_due:
            data.update({"new_due": None, cs.DUE_CLEARED_KEY: True, "prior_due": prior_due})
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        ev = {"type": "commitment_updated", "source_skill": source_skill,
              "primary_thread_id": target.get("primary_thread_id") or "", "data": data}
        append_event(events_path, [ev], holder=source_skill)
    title = _title(target)
    ack = (WAIT_SET_ACK.format(title=title, event=text, days=WAIT_ASK_DAYS) if title
           else WAIT_SET_ACK_BARE.format(event=text, days=WAIT_ASK_DAYS))
    return {"status": STATUS_SET, "commitment_id": cid, "wait_on": text,
            "prior_due": prior_due, "event": ev, "ack": ack}


def clear_wait(workspace_root, commitment_id, *, actor_id: str, source_skill: str,
               reason: str = "the wait is over", now_iso: Optional[str] = None) -> dict:
    """End the wait: the item is back in its own lane, still undated
    (cr1#1291). `unchanged` when it was not waiting; `not_open` when closed."""
    import commitment_state as cs
    from atomic_write import events_write_section
    from event_gate import append_event

    now_iso = now_iso or _now_iso(workspace_root)
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_write_section(events_path, holder=f"clear_wait:{source_skill}"):
        index = cs._scan_commitment_index(events_path)
        cid = cs.normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if cs._currently_closed(index, cid, target.get("seq")):
            return {"status": STATUS_NOT_OPEN, "commitment_id": cid, "ack": WAIT_CLOSED_ACK}
        ev = end_wait_event(events_path, target, cid, actor_id=actor_id,
                            source_skill=source_skill, reason=reason, now_iso=now_iso)
        if ev is None:
            return {"status": STATUS_SAME, "commitment_id": cid, "ack": WAIT_NONE_ACK}
        append_event(events_path, [ev], holder=source_skill)
    title = _title(target)
    return {"status": STATUS_CLEARED, "commitment_id": cid, "event": ev,
            "ack": WAIT_CLEARED_ACK.format(title=title) if title else WAIT_CLEARED_ACK_BARE}


def apply_wait_text(workspace_root, commitment_id, typed, *, actor_id: str,
                    source_skill: str, now_iso: Optional[str] = None) -> Optional[dict]:
    """The answer typed where a date goes (the Hub's Snooze box, the End of
    Day's push), when it is a wait and not a date: "waiting on <event>" sets
    or confirms the wait, "not waiting" clears it. None for anything else,
    so the caller keeps its own answer for a date it cannot read."""
    parsed = parse_wait_text(typed)
    if parsed is None:
        return None
    kind, event = parsed
    if kind == "clear":
        return clear_wait(workspace_root, commitment_id, actor_id=actor_id,
                          source_skill=source_skill, now_iso=now_iso)
    return wait_on(workspace_root, commitment_id, event=event, actor_id=actor_id,
                   source_skill=source_skill, now_iso=now_iso)
