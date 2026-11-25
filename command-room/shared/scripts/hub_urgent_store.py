#!/usr/bin/env python3
"""The Hub's Urgent store (cr1#1786, ruling 127f): what the inbox read saw that the Urgent ranking needs, kept walled between inbox reads.

Mail is fetched only in the inbox part; the hourly Update fetches none
(ruling 130e). So the inbox part saves, from each item it fetched, the few
facts `hub_urgent.signals` reads, and every Update re-ranks from them: time
passes (a deadline that has passed drops out), and an item the owner has
answered since leaves. The page lays its own live inbox lines over this base
(ruling 136c); the page never writes this store (128q).

What is kept, per item (the fewest the signals need, never a message):
`thread_id`, `message_id`, `ts`, `kind` (`email_reply` or
`calendar_invite`), the sender (`sender_email`, `sender_name`,
`sender_person_id`), `subject` (what the row says), `url`, and what the text
said instead of the text: `asks` (a question or request), `deadline_on` (the
stated deadline's day) and `copy` (the owner on Cc only); an invite's
`response` and `event_start`; `needs` when the run judged one; `has_draft`
when the inbox wrote a reply. No body, no recipients' addresses, no other
field.

What is never kept: noise (the inbox read's `noise` kind or a noise class),
a suppressed sibling, the owner's own newest message, a run verdict that it
needs nothing, and anything from or naming a person behind the wall. When
the wall cannot be read, nothing is kept (the store is emptied).

How long: each inbox read replaces the store whole (an item the inbox no
longer lists is gone), and an item older than `MAX_AGE_HOURS` is never
ranked. An item is answered when the book holds an `email_sent` on its
thread after it arrived.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

STORE_REL = Path("_hq") / "data" / "hub-urgent-items.json"
#: The most items kept from one inbox read.
MAX_ITEMS = 60
#: An item older than this is not ranked (and drops out at the next save).
MAX_AGE_HOURS = 72
SUBJECT_CAP = 200
KEPT_FIELDS = ("thread_id", "message_id", "ts", "kind", "sender_email", "sender_name", "sender_person_id",
               "subject", "url", "asks", "deadline_on", "copy", "response", "event_start", "needs", "has_draft")


def store_path(workspace_root) -> Path:
    return Path(str(workspace_root)) / STORE_REL


def _low(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def walled(item: Dict[str, Any], wall: Optional[Dict[str, Any]]) -> bool:
    """True when the item is from, or names, someone behind the wall. `wall`
    is `{addrs, names}` (lower case), as `cockpit_helpers._meeting_wall`
    answers it; None (unreadable) walls everything."""
    if wall is None:
        return True
    addr = _low(item.get("sender_email") or item.get("sender"))
    if addr and addr in (wall.get("addrs") or ()):
        return True
    words = " " + _name_words(" ".join(str(item.get(k) or "") for k in ("sender_name", "subject"))) + " "
    return any(n and (" " + _name_words(n) + " ") in words for n in (wall.get("names") or ()))


def _name_words(text: Any) -> str:
    """Text as the name match reads it (review round 1 of #1819): lower case,
    a possessive and any mark around a word taken off, so "Sample's" and
    "Sample," still name Sample; an apostrophe inside a name stays."""
    t = re.sub(r"['\u2019]s\b", " ", _low(text))
    t = re.sub(r"[^\w\s'\u2019-]+", " ", t)
    return " ".join(t.split())


def project(item: Any, *, own: Iterable[str] = (), tzinfo: _dt.tzinfo = _dt.timezone.utc,
            now: Optional[_dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """One fetched item as the store keeps it, or None when it is never kept
    (noise, the owner's own, nothing needed). Pure."""
    import hub_urgent as hu

    if not isinstance(item, dict) or hu.is_noise(item, own):
        return None
    kind = "calendar_invite" if str(item.get("kind") or "") == "calendar_invite" else "email_reply"
    text = " ".join(str(item.get(k) or "") for k in ("subject", "body"))
    ts = hu._parse_ts(item.get("ts") or item.get("message_ts"))
    today = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(tzinfo).date()
    sent_on = ts.astimezone(tzinfo).date() if ts else today
    due = hu.deadline_date(text, sent_on)
    mine = {hu._addr(a) for a in own or ()}
    to, cc = hu._addrs(item.get("to")), hu._addrs(item.get("cc"))
    out = {
        "thread_id": str(item.get("thread_id") or "") or None,
        "message_id": str(item.get("message_id") or "") or None,
        "ts": hu._stamp(ts) if ts else None,
        "kind": kind,
        "sender_email": hu._addr(item.get("sender_email") or item.get("sender")) or None,
        "sender_name": str(item.get("sender_name") or "")[:120] or None,
        "sender_person_id": str(item.get("sender_person_id") or "") or None,
        "subject": str(item.get("subject") or "")[:SUBJECT_CAP],
        "url": str(item.get("url") or "") or None,
        "asks": hu.asks(text),  # feed-shape audit (U6): the one rule
        "deadline_on": due.isoformat() if due else None,
        "copy": bool(mine & set(cc)) and not (mine & set(to)),
        "has_draft": bool(item.get("draft")),
    }
    if kind == "calendar_invite":
        out["response"] = str(item.get("response") or item.get("rsvp") or "")[:40]
        start = hu._parse_ts(item.get("event_start"))
        out["event_start"] = hu._stamp(start) if start else None
    if isinstance(item.get("needs"), bool):
        out["needs"] = item["needs"]
    if not (out["thread_id"] or out["message_id"]):
        return None
    return {k: v for k, v in out.items() if k in KEPT_FIELDS}


def build(items: Iterable[Any], *, wall: Optional[Dict[str, Any]], own: Iterable[str] = (),
          tzinfo: _dt.tzinfo = _dt.timezone.utc, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The whole store from one inbox read's items. Pure. An unreadable wall
    (`wall` None) keeps nothing."""
    import hub_urgent as hu

    now = hu._parse_ts(now_iso) if now_iso else _dt.datetime.now(_dt.timezone.utc)
    kept: List[Dict[str, Any]] = []
    held = wall is None
    for it in items or []:
        if held:
            break
        if not isinstance(it, dict) or walled(it, wall):
            continue
        row = project(it, own=own, tzinfo=tzinfo, now=now)
        if row is not None:
            kept.append(row)
    kept.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return {"version": 1, "saved_at": hu._stamp(now), "held": held, "items": kept[:MAX_ITEMS]}


def save(workspace_root, doc: Dict[str, Any]) -> bool:
    """A WRITER, called by the inbox part only (`cockpit_helpers.inbox_all`):
    replaces the store whole, locked and atomic. False when it could not be
    written; the inbox run goes on (Urgent keeps its last base)."""
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock

    path = store_path(workspace_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = acquire_write_lock(path, holder="hub-urgent-store")
        try:
            atomic_write_json(path, doc)
        finally:
            release_write_lock(lock)
    except Exception:  # noqa: BLE001 - never stops the inbox run
        return False
    return True


def load(workspace_root) -> Dict[str, Any]:
    """READ. The store, or an empty one; never raises."""
    try:
        doc = json.loads(store_path(workspace_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "saved_at": None, "held": False, "items": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        return {"version": 1, "saved_at": None, "held": False, "items": []}
    return doc


def answered_threads(events: Iterable[Any]) -> Dict[str, str]:
    """The threads the owner wrote on, each with its newest `email_sent`
    time. Pure."""
    out: Dict[str, str] = {}
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("type") != "email_sent" or not isinstance(ev.get("data"), dict):
            continue
        d = ev["data"]
        ts = str(ev.get("ts") or d.get("sent_at") or "")
        for key in ("thread_id", "gmail_thread_id", "thread_native_id"):
            tid = str(d.get(key) or "")
            if tid and ts > out.get(tid, ""):
                out[tid] = ts
    return out


def current(doc: Dict[str, Any], *, answered: Optional[Dict[str, str]] = None,
            now_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """The store's items that may still rank now: younger than
    `MAX_AGE_HOURS` and not answered since they arrived. Pure."""
    import hub_urgent as hu

    now = hu._parse_ts(now_iso) if now_iso else _dt.datetime.now(_dt.timezone.utc)
    answered = answered or {}
    out = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        ts = hu._parse_ts(it.get("ts"))
        if ts is None or now - ts > _dt.timedelta(hours=MAX_AGE_HOURS):
            continue
        sent = hu._parse_ts(answered.get(str(it.get("thread_id") or "")))
        if sent is not None and sent >= ts:
            continue
        out.append(dict(it))
    return out
