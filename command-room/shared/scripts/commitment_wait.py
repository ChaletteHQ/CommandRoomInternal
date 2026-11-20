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

UNDO (cr1#1322)
==============
Every wait set or ended by the owner carries an undo handle: a
`brain_batch_id` (the Hub's own batch, or one minted here for End of Day and
chat) and a change class, `commitment_wait` or `commitment_wait_end`, which
`brain_undo` reverses through `undo_wait` and `undo_wait_end` below. A
reversal puts back exactly what the item held before: the park or wait it
replaced (copied from the book), the date the wait cleared, or the wait a
clear or a date ended. When the item has changed since, or the book does not
say what came before, it refuses (`STATUS_UNDO_REFUSED`) and writes nothing.

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
#: cr1#1322: the change classes `brain_undo.REVERSERS` reverses.
WAIT_SET_CLASS = "commitment_wait"
WAIT_END_CLASS = "commitment_wait_end"
#: An undo that cannot put the item back exactly: nothing is written.
STATUS_UNDO_REFUSED = "undo_refused"
UNDO_CHANGED = "changed"          # the item moved after the wait act
UNDO_UNRECORDED = "unrecorded"    # the book does not say what came before
UNDO_REASON = "undo: the wait act was taken back"
#: Keys a copied park never carries back: its own undo handle, its date
#: fields (the reversal sets the date itself) and an earlier reversal's marks.
_NOT_COPIED = ("brain_batch_id", "parent_batch_id", "undo_group", "brain_change_class",
               "new_due", "due", "due_date", "due_cleared", "prior_due",
               "reversal_of", "undone_by")


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


def mint_batch_id(now_iso: str) -> str:
    """An undo handle for a wait act no surface batch names (cr1#1322)."""
    import secrets
    stamp = str(now_iso or "").replace("-", "").replace(":", "")[:15]
    return f"wait_{stamp}Z_{secrets.token_hex(4)}"


def end_wait_event(events_path, target: dict, cid: str, *, actor_id: str,
                   source_skill: str, reason: str, now_iso: str,
                   brain_batch_id: Optional[str] = None) -> Optional[dict]:
    """The un-park that ends a live wait on `cid`, or None when it does not
    wait. For a writer already inside the events lock (the Later writer:
    a date given to a waiting item replaces the wait). With
    `brain_batch_id` it carries its undo handle (cr1#1322)."""
    import commitment_state as cs

    live = item_waits(_load(events_path), now_iso).get(cid)
    if live is None:
        return None
    # cr1#1322 reads these to put the wait back on an undo: the wait it
    # ended and the date the item had just before (None: no date).
    data = {"commitment_id": cid, "status_hint": None, "unparked": True,
            "wait_on": None, "wait_cleared": True,
            "prior_wait_on": live["wait_on"], "prior_wait_since": live["since"],
            "prior_due": cs.effective_due(events_path, cid),
            "reason": str(reason or "the wait is over")[:200], "unparked_by": actor_id}
    if brain_batch_id:
        data.update({"brain_batch_id": str(brain_batch_id), "brain_change_class": WAIT_END_CLASS})
    if isinstance(target.get("seq"), int):
        data["commitment_seq"] = target["seq"]
    return {"type": "commitment_updated", "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "", "data": data}


def wait_on(workspace_root, commitment_id, *, event: str, actor_id: str,
            source_skill: str, now_iso: Optional[str] = None,
            brain_batch_id: Optional[str] = None) -> dict:
    """Hold an open item with no date, waiting on `event` (cr1#1291).

    One `commitment_updated`: a park with `wait_on` and `wait_since`, and,
    when the item had a date, the clear of that date (`prior_due` kept, so
    the date can be put back). Saying the same wait again before the ask is
    due writes nothing (`unchanged`); after it, the wait is confirmed and
    the 14 days start again. Refuses a closed item (`not_open`) and a blank
    event (`missing_when`). Raises `CommitmentIdError` on an unknown id.
    The event carries its undo handle (cr1#1322): `brain_batch_id`, or one
    minted here, and `WAIT_SET_CLASS`; the answer names it as `batch_id`."""
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
                cs.REASON_ORIGIN_KEY: cs.park_reason_origin(actor_id, source_skill),
                "brain_batch_id": str(brain_batch_id or mint_batch_id(now_iso)),
                "brain_change_class": WAIT_SET_CLASS}
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
            "prior_due": prior_due, "event": ev, "ack": ack,
            "batch_id": data["brain_batch_id"]}


def clear_wait(workspace_root, commitment_id, *, actor_id: str, source_skill: str,
               reason: str = "the wait is over", now_iso: Optional[str] = None,
               brain_batch_id: Optional[str] = None) -> dict:
    """End the wait: the item is back in its own lane, still undated
    (cr1#1291). `unchanged` when it was not waiting; `not_open` when closed.
    The end carries its undo handle, as `wait_on`'s does (cr1#1322)."""
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
                            source_skill=source_skill, reason=reason, now_iso=now_iso,
                            brain_batch_id=brain_batch_id or mint_batch_id(now_iso))
        if ev is None:
            return {"status": STATUS_SAME, "commitment_id": cid, "ack": WAIT_NONE_ACK}
        append_event(events_path, [ev], holder=source_skill)
    title = _title(target)
    return {"status": STATUS_CLEARED, "commitment_id": cid, "event": ev,
            "batch_id": ev["data"].get("brain_batch_id"),
            "ack": WAIT_CLEARED_ACK.format(title=title) if title else WAIT_CLEARED_ACK_BARE}


def apply_wait_text(workspace_root, commitment_id, typed, *, actor_id: str,
                    source_skill: str, now_iso: Optional[str] = None,
                    brain_batch_id: Optional[str] = None) -> Optional[dict]:
    """The answer typed where a date goes (the Hub's Snooze box, the End of
    Day's push), when it is a wait and not a date: "waiting on <event>" sets
    or confirms the wait, "not waiting" clears it. None for anything else,
    so the caller keeps its own answer for a date it cannot read.
    `brain_batch_id` is the surface's own batch (the Hub's run), when it has
    one; otherwise the writer mints the undo handle."""
    parsed = parse_wait_text(typed)
    if parsed is None:
        return None
    kind, event = parsed
    if kind == "clear":
        return clear_wait(workspace_root, commitment_id, actor_id=actor_id,
                          source_skill=source_skill, now_iso=now_iso,
                          brain_batch_id=brain_batch_id)
    return wait_on(workspace_root, commitment_id, event=event, actor_id=actor_id,
                   source_skill=source_skill, now_iso=now_iso,
                   brain_batch_id=brain_batch_id)


# ---------------------------------------------------------------------------
# Undo (cr1#1322): put back exactly what a wait set or a wait end replaced.
# ---------------------------------------------------------------------------

def waiting_ids(events_path, now_iso: Optional[str] = None) -> set:
    """The ids that wait on an event right now (one read of the book). For a
    writer inside the events lock that must not undo something under a wait:
    an older date or an older park (cr1#1322)."""
    return set(item_waits(_load(events_path), now_iso or _now_iso(None)))


def batch_wait_end(events_path, cid: str, batch_id: Optional[str]) -> tuple:
    """(ended, put_back) for batch `batch_id` on item `cid`: did the batch
    end a wait on it, and has that end's undo put the wait back? For the
    date reverser, which runs after the wait end's own (newest first) and
    must not move the date when the wait could not go back (cr1#1322)."""
    if not batch_id:
        return (False, False)
    updates = _updates(_load(events_path), cid)
    ends = [sq for sq, d in updates
            if d.get("brain_batch_id") == batch_id and d.get("brain_change_class") == WAIT_END_CLASS]
    if not ends:
        return (False, False)
    undone = {d.get("reversal_of") for _sq, d in updates}
    return (True, all(sq in undone for sq in ends))


def _seq(ev) -> Optional[int]:
    from event_seq import event_seq
    return event_seq(ev)


def _updates(events, cid: str) -> list:
    """[(seq, data)] of the item's `commitment_updated` rows, in book order."""
    out = []
    for ev in events:
        if not isinstance(ev, dict) or (ev.get("type") or ev.get("event")) != "commitment_updated":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if _target(d, ev) == cid and _seq(ev) is not None:
            out.append((_seq(ev), d))
    return out


def _holders(updates: list) -> list:
    """[(seq, data)]: the park-hint acts that hold the item, in book order,
    with every undone act and its reversal taken out. An undo puts back what
    the act before it held, so after "set, clear, undo the clear" the set
    holds the item again and can itself be undone (newest first)."""
    chain: list = []
    for sq, d in updates:
        if "status_hint" not in d:
            continue
        undone = d.get("reversal_of")
        if undone is not None and chain and chain[-1][0] == undone:
            chain.pop()
            continue
        chain.append((sq, d))
    return chain


def _undo_refusal(cid, why: str) -> dict:
    return {"status": STATUS_UNDO_REFUSED, "commitment_id": str(cid or ""), "why": why}


def _restored_status(prior: Optional[dict]) -> dict:
    """What the item's park hint was before the act: the earlier park or wait
    copied from the book (its reason, actor and wait as written), or no park
    at all."""
    if prior and prior.get("status_hint"):
        return {k: v for k, v in prior.items() if k not in _NOT_COPIED}
    return {"status_hint": None, "unparked": True, "reason": UNDO_REASON}


def _undo_act(workspace_root, change_seq, cls: str, *, undone_by: str, source_skill: str,
              put_back) -> dict:
    """Shared frame: find the act at `change_seq`, refuse what is not that
    act, answer `already_put_back` / `not_open`, and otherwise append what
    `put_back(events, cid, act, updates)` composes (or its refusal), all
    inside the events lock."""
    import commitment_state as cs
    from atomic_write import events_write_section
    from event_gate import append_event

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_write_section(events_path, holder=f"undo_wait:{source_skill}"):
        events = _load(events_path)
        act_ev = next((ev for ev in events if isinstance(ev, dict) and _seq(ev) == change_seq
                       and (ev.get("type") or ev.get("event")) == "commitment_updated"), None)
        act = (act_ev or {}).get("data") if isinstance((act_ev or {}).get("data"), dict) else {}
        raw = _target(act, act_ev or {})
        if act_ev is None or act.get("brain_change_class") != cls or "status_hint" not in act:
            return _undo_refusal(raw, UNDO_UNRECORDED)
        index = cs._scan_commitment_index(events_path)
        try:
            cid = cs.normalize_commitment_id(raw, index)
        except cs.CommitmentIdError:
            return _undo_refusal(raw, UNDO_UNRECORDED)
        target = index["by_id"][cid]
        updates = _updates(events, cid)
        if any(d.get("reversal_of") == change_seq for _s, d in updates):
            return {"status": "already_put_back", "commitment_id": cid}
        if cs._currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        hints = _holders(updates)
        # Only the act that still holds the item is undone: a later park,
        # wait, un-park or Later is somebody's later word.
        if not hints or hints[-1][0] != change_seq:
            return _undo_refusal(cid, UNDO_CHANGED)
        data = put_back(events, events_path, cid, act, hints, updates)
        if data.get("status") == STATUS_UNDO_REFUSED:
            return data
        data.update({"commitment_id": cid, "reversal_of": change_seq, "undone_by": undone_by})
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        ev = {"type": "commitment_updated", "source_skill": source_skill,
              "primary_thread_id": target.get("primary_thread_id") or "", "data": data}
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "put_back", "commitment_id": cid, "event": ev}


def _newer_hide_over_batch(events: list, cid: str, bid: str, change_seq) -> bool:
    """cr1#1364: True when batch `bid` hid `cid` (its `chat_dismissal`, older
    than the wait end at `change_seq`) and a newer hide holds the item since
    (`mute_ledger.newer_hide_seq`). Pure; read inside the events lock."""
    from mute_ledger import _item_keys, newer_hide_seq

    keys = _item_keys(events, cid)
    own = [_seq(ev) for ev in events if isinstance(ev, dict)
           and (ev.get("type") or ev.get("event")) == "chat_dismissal"
           and isinstance(ev.get("data"), dict) and ev["data"].get("brain_batch_id") == bid
           and str(ev["data"].get("target_id") or "").strip() in keys
           and _seq(ev) is not None and _seq(ev) < change_seq]
    return any(newer_hide_seq(events, sq) is not None for sq in own)


def undo_wait(workspace_root, change_seq, *, undone_by: str, source_skill: str) -> dict:
    """Reverse one wait set (`WAIT_SET_CLASS`, the event at `change_seq`):
    the park hint goes back to what it was before (none, a rest, or an
    earlier wait, copied from the book) and the date the wait cleared comes
    back (`prior_due`). Refused when the item is no longer held by that wait
    or has a date again."""
    import commitment_state as cs

    def put_back(_events, events_path, cid, act, hints, _updates):
        if cs.effective_due(events_path, cid) is not None:
            return _undo_refusal(cid, UNDO_CHANGED)
        data = _restored_status(hints[-2][1] if len(hints) > 1 else None)
        if act.get("prior_due"):
            data["new_due"] = act["prior_due"]
            # cr1#1292: the date comes back with the hide the Later that set
            # it carried, so the undo puts every surface back exactly.
            hide = cs._restored_hide(events_path, cid, act["prior_due"])
            if hide:
                data["hidden_until"] = hide
        return data

    return _undo_act(workspace_root, change_seq, WAIT_SET_CLASS, undone_by=undone_by,
                     source_skill=source_skill, put_back=put_back)


def undo_wait_end(workspace_root, change_seq, *, undone_by: str, source_skill: str) -> dict:
    """Reverse one wait end (`WAIT_END_CLASS`): the wait it ended comes back,
    copied from the book and checked against what the end recorded
    (`prior_wait_on`, `prior_wait_since`), and the date goes back to what
    it was just before the end (`prior_due`). The date may be the one its
    own batch gave the item (a Later on a waiting item), and only while that
    batch's date move is still the newest live move on the item, judged by
    seq (cr1#1361); any other date, or a newer move to the same date, means
    the item changed since, and it is refused before anything is written."""
    import commitment_state as cs

    def put_back(events, events_path, cid, act, hints, updates):
        if any(k not in act for k in ("prior_wait_on", "prior_wait_since", "prior_due")):
            return _undo_refusal(cid, UNDO_UNRECORDED)
        before_events = [ev for ev in events if isinstance(ev, dict)
                         and _seq(ev) is not None and _seq(ev) < change_seq]
        live = item_waits(before_events, _now_iso(None)).get(cid)
        if (not live or live["wait_on"] != act.get("prior_wait_on")
                or live["since"] != act.get("prior_wait_since")):
            return _undo_refusal(cid, UNDO_UNRECORDED)
        bid = act.get("brain_batch_id")
        own = [sq for sq, d in updates
               if bid and sq < change_seq and d.get("brain_batch_id") == bid and d.get("new_due")]
        # cr1#1364: the batch hid the item (a Later on a waiting item owed to
        # the owner) rather than giving it a date. The hide and the wait go
        # back together or not at all, so the batch's own hide is judged here,
        # by seq, before anything is written: a newer hide on the item (any
        # Snooze, Later or hold since) is a newer word, and this undo would
        # put the wait back under it.
        if bid and _newer_hide_over_batch(events, cid, bid, change_seq):
            return _undo_refusal(cid, UNDO_CHANGED)
        current = cs.effective_due(events_path, cid)
        if own:
            # cr1#1361: the batch also gave the item its date, and the date
            # and the wait go back together or not at all. So the date move
            # is judged here, by its seq, before anything is written (the
            # cr1#1344 rule): a newer move to the SAME date is a newer word,
            # and a check on the date's value alone would erase it.
            verdict = cs._due_undo_verdict(events_path, cid, undoing_seq=own[-1],
                                           prior_due=act.get("prior_due"), undoing_batch=None,
                                           current=current)
            back = verdict.get("why") == "already" and current == act.get("prior_due")
            if verdict.get("act_seq") is None and not back:
                return _undo_refusal(cid, UNDO_CHANGED)
        elif current != act.get("prior_due"):
            return _undo_refusal(cid, UNDO_CHANGED)
        if len(hints) < 2:
            return _undo_refusal(cid, UNDO_UNRECORDED)
        data = _restored_status(hints[-2][1])
        if current != act.get("prior_due"):
            if act.get("prior_due"):
                data["new_due"] = act["prior_due"]
                hide = cs._restored_hide(events_path, cid, act["prior_due"])  # cr1#1292
                if hide:
                    data["hidden_until"] = hide
            else:
                data.update({"new_due": None, "due_cleared": True})
        return data

    return _undo_act(workspace_root, change_seq, WAIT_END_CLASS, undone_by=undone_by,
                     source_skill=source_skill, put_back=put_back)


# ---------------------------------------------------------------------------
# The chat verb (cr1#1323): the same writer, reached from a typed row number.
# ---------------------------------------------------------------------------

#: The surfaces whose numbers a typed wait can name: the plate's own numbers
#: (the board, `work my plate`, commitment triage, My Plate; PLATENUM1), or
#: the morning brief's recorded numbered list (BRIEFFIX1 Item C).
CHAT_SURFACES = ("plate", "morning-brief")
CHAT_SOURCE_SKILL = "apply-choices"
STATUS_NOT_A_WAIT = "not_a_wait"
CHAT_NOT_A_WAIT_ACK = ("That is not a wait. Say \"2 is waiting on the contract signing\" "
                       "or \"2 is not waiting\", with the row's number.")
CHAT_NO_ACTOR_ACK = "I could not tell who is asking, so nothing changed."
CHAT_BAD_SURFACE_ACK = "I can only set a wait on a numbered list of your items. Nothing changed."
CHAT_NO_SUCH_ROW_ACK = ("I don't have a row numbered {n}. Say `work my plate` to see the "
                        "current list. Nothing changed.")
CHAT_GONE_ACK = "I could not find that item any more. Nothing changed."
#: cr1#1335: a wait and another change in one sentence ("2 is waiting on the
#: review, drop 3") is refused whole, so neither half lands on a guess.
STATUS_TWO_ACTS = "two_acts"
CHAT_TWO_ACTS_ACK = ("That asks for more than one change at once. Nothing changed. Say the wait "
                     "on its own, like \"{n} is waiting on the contract signing\", and any other "
                     "change after it.")

_NUM = r"(?:(?:item|number|row)\s+)?#?(?P<n>\d{1,6})"
_EVENT = r":?\s*(?P<event>\S.*?)"
_CHAT_SET = (
    _re.compile(r"^(?:still\s+)?wait(?:ing)?\s+(?:on|for)\s+(?P<event>\S.*?)\s+for\s+" + _NUM + r"$",
                _re.IGNORECASE),
    _re.compile(r"^" + _NUM + r"\s+(?:is\s+)?(?:still\s+)?(?:waiting|waits|wait)\s+(?:on|for)\b\s*"
                + _EVENT + r"$", _re.IGNORECASE),
)
_CHAT_CLEAR = (
    _re.compile(r"^" + _NUM + r"\s+(?:is\s+)?(?:not|no\s+longer)\s+waiting"
                r"(?:\s+(?:on|for)\s+anything)?(?:\s+any\s*more)?$", _re.IGNORECASE),
    _re.compile(r"^" + _NUM + r"\s+(?:is\s+)?done\s+waiting$", _re.IGNORECASE),
    _re.compile(r"^(?:clear\s+(?:the\s+)?wait|stop\s+waiting)\s+(?:on|for)\s+" + _NUM + r"$",
                _re.IGNORECASE),
)
#: Words after "for <n>" that make the number a length of time, not a row.
_DURATION_TAIL = _re.compile(r"\bfor\s+\d+\s*(?:days?|weeks?|months?|hours?|d|w)\b", _re.IGNORECASE)

# cr1#1335: what makes a sentence carry a second act. Only ROW-shaped numbers
# count, so an event with a number inside it ("round 2 of the review",
# "invoice #1043", "sign 2 copies") is still one wait.
_ROW_NUM = r"(?:(?:item|number|row)\s+)?#?\d{1,6}"
#: The typed row verbs a sentence could carry beside the wait.
_ROW_VERBS = (r"(?:drop|mark\s+(?:it\s+)?done|resolved?|later|push(?:\s+to)?|snooze|hold|"
              r"not\s+mine|mine|theirs|nudge|chase|follow-?up\s+call|skip|reopen|undo|reassign|"
              r"split|fix\s+wording|make\s+(?:a\s+)?task|promote)")
#: A row verb on a row number, ending its clause: "drop 3", "later 3 to friday".
#: "resolve 2 questions" goes on past the number, so it is part of an event.
_VERB_ON_ROW = _re.compile(r"\b" + _ROW_VERBS + r"\s+" + _ROW_NUM
                           + r"(?=\s*$|\s*[,;.]|\s+(?:and|then|to|too|also)\b)", _re.IGNORECASE)
#: A clause that starts with a row verb, aimed at a row or at "it". A bare
#: verb ending the clause ("then hold") is not enough: it can be the event.
_CLAUSE_VERB = _re.compile(r"^(?:(?:then|also|please|and)\s+)*" + _ROW_VERBS
                           + r"(?:\s+(?:it|that|this|them)\b|\s+" + _ROW_NUM + r"\b)",
                           _re.IGNORECASE)
#: A clause that names a second row: "3 is too", "3 is not waiting".
_CLAUSE_ROW = _re.compile(r"^(?:(?:then|also|and)\s+)*" + _ROW_NUM
                          + r"\s+(?:is|are|too|also|as\s+well|waits?|waiting|not|no\s+longer)\b",
                          _re.IGNORECASE)
#: A bare row number as a whole clause ("and 3"). It names a second row only
#: beside a sentence that is already a whole wait ("wait on X for 2 and 3");
#: inside an event it is part of the event ("chapters 3 and 4").
_CLAUSE_BARE_ROW = _re.compile(r"^(?:(?:then|also|and)\s+)*" + _ROW_NUM + r"$", _re.IGNORECASE)
#: Where one clause ends and the next begins.
_CLAUSE_BREAK = _re.compile(r"\s*[,;]\s*|\s+(?:and|then|also|plus|&)\s+", _re.IGNORECASE)
#: A row number left at the end of a number-first wait ("3 is waiting on the
#: review for 2"): two rows named, and which one waits is a guess. Up to
#: three digits only, so a year or an order number ("taxes for 2019", "the
#: PO for #4411") stays part of the event.
_TRAILING_ROW = _re.compile(r"\bfor\s+(?:(?:item|number|row)\s+)?#?\d{1,3}$", _re.IGNORECASE)


def _parse_one_wait(s: str) -> Optional[dict]:
    """One clause read as a wait or a clear, or None. No second-act check."""
    for rx in _CHAT_CLEAR:
        m = rx.match(s)
        if m:
            return {"kind": "clear", "n": m.group("n"), "event": ""}
    for rx in _CHAT_SET:
        m = rx.match(s)
        if m:
            event = " ".join(m.group("event").split()).strip(" :")[:WAIT_TEXT_CAP]
            if event:
                return {"kind": "wait", "n": m.group("n"), "event": event}
    return None


def _names_an_act(clause: str, *, bare_row: bool = False) -> bool:
    """True when a clause asks for a change of its own: a row verb ("drop 3",
    "mark done it"), another row ("3 is too"), or a wait. `bare_row` also
    counts a lone row number ("and 3")."""
    c = clause.strip(" .")
    if not c:
        return False
    return bool(_VERB_ON_ROW.search(c) or _CLAUSE_VERB.match(c) or _CLAUSE_ROW.match(c)
                or (bare_row and _CLAUSE_BARE_ROW.match(c)) or _parse_one_wait(c) is not None)


def _event_carries_an_act(event: str) -> bool:
    """True when the words read as the event hold a second act (cr1#1335)."""
    if _VERB_ON_ROW.search(event) or _TRAILING_ROW.search(event):
        return True
    return any(_names_an_act(event[m.end():]) for m in _CLAUSE_BREAK.finditer(event))


def parse_chat_wait(said) -> Optional[dict]:
    """What a typed chat sentence asks of a wait, or None (cr1#1323). Pure.

    `{"kind": "wait", "n": "2", "event": "the sample review"}` for
    "wait on the sample review for 2" / "2 is waiting on the sample review";
    `{"kind": "clear", "n": "2", "event": ""}` for "2 is not waiting" /
    "clear the wait on 2". None for anything else: no row number, a
    question, a length of time ("for 2 days"), or another verb ("later 2 to
    friday", "hold 2 until ..."), so the caller keeps its own answer.

    `{"kind": "two_acts", "n": "2", "event": ""}` for a wait said with a
    second act in the same sentence (cr1#1335): another row verb ("2 is
    waiting on the review, drop 3"), or a second row ("3 is waiting on the
    review for 2", "2 is waiting on legal and 3 is too"). The caller refuses
    it whole and writes nothing, so neither half lands on a guess."""
    s = " ".join(str(said or "").split()).strip()
    s = _re.sub(r"[.!]+$", "", s).strip()
    if not s or s.endswith("?") or _DURATION_TAIL.search(s):
        return None
    one = _parse_one_wait(s)
    if one is not None:
        if one["kind"] == "wait" and _event_carries_an_act(one["event"]):
            return {"kind": "two_acts", "n": one["n"], "event": ""}
        return one
    # A wait beside another act, in either order: "2 is not waiting, drop 3",
    # "drop 3 and 2 is waiting on the review", "wait on the review for 2 and 3".
    for m in _CLAUSE_BREAK.finditer(s):
        left, right = s[:m.start()].strip(), s[m.end():].strip()
        for wait_part, other in ((left, right), (right, left)):
            got = _parse_one_wait(wait_part)
            if got is not None and _names_an_act(other, bare_row=True):
                return {"kind": "two_acts", "n": got["n"], "event": ""}
    return None


def _chat_refusal(status: str, ack: str, **extra) -> dict:
    return {"ok": False, "status": status, "ack": ack, **extra}


def wait_from_chat(workspace_root, *, said: str = "", resolved_by: str = "",
                   surface: str = "plate", now_iso: Optional[str] = None) -> dict:
    """A WRITER (on `RUN_WRITER_ALLOWLIST` only): a wait set or cleared in
    chat by the row's number (cr1#1323), through the same `wait_on` /
    `clear_wait` the Hub's Snooze box and End of Day's push reach.

    `said` is the owner's sentence, verbatim ("wait on the sample review
    for 2", "2 is not waiting"); `parse_chat_wait` reads it, and anything
    that is not a wait answers `not_a_wait` with nothing written. The number
    resolves the way every typed verb on that surface does: on the `plate`
    through `plate_view.resolve_display_number` (the number belongs to the
    item, never a position), on the `morning-brief` through
    `brief_receipt.resolve_mark_done` (the brief's recorded list, refused
    when stale). A refusal writes nothing. The answer is the writer's own
    dict plus `ok`, `n` and `ack`, the one sentence to post, which is the
    Hub's own line."""
    actor = str(resolved_by or "").strip()
    if not actor:
        return _chat_refusal("no_actor", CHAT_NO_ACTOR_ACK)
    where = str(surface or "plate").strip().lower()
    if where not in CHAT_SURFACES:
        return _chat_refusal("bad_surface", CHAT_BAD_SURFACE_ACK, surface=surface)
    parsed = parse_chat_wait(said)
    if parsed is None:
        return _chat_refusal(STATUS_NOT_A_WAIT, CHAT_NOT_A_WAIT_ACK)
    n = parsed["n"]
    if parsed["kind"] == "two_acts":
        return _chat_refusal(STATUS_TWO_ACTS, CHAT_TWO_ACTS_ACK.format(n=n), n=n)
    if where == "morning-brief":
        from brief_receipt import resolve_mark_done
        target = resolve_mark_done(str(workspace_root), n)
        if not target.get("ok"):
            return _chat_refusal("no_such_row", str(target.get("refusal") or CHAT_GONE_ACK), n=n)
        cid = str(target["id"])
    else:
        from plate_view import resolve_display_number
        cid = resolve_display_number(str(workspace_root), n)
        if cid is None:
            return _chat_refusal("no_such_row", CHAT_NO_SUCH_ROW_ACK.format(n=n), n=n)
    import commitment_state as cs
    try:
        if parsed["kind"] == "clear":
            out = clear_wait(workspace_root, cid, actor_id=actor, source_skill=CHAT_SOURCE_SKILL,
                             now_iso=now_iso)
        else:
            out = wait_on(workspace_root, cid, event=parsed["event"], actor_id=actor,
                          source_skill=CHAT_SOURCE_SKILL, now_iso=now_iso)
    except cs.CommitmentIdError:
        return _chat_refusal("no_such_row", CHAT_GONE_ACK, n=n, commitment_id=cid)
    out = dict(out)
    out.pop("event", None)
    return {**out, "ok": out.get("status") in LANDED, "n": n, "surface": where}
