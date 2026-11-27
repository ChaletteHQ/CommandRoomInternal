#!/usr/bin/env python3
"""The Hub's Focus: the list the owner picks (cr1#1785, Hub v6.12 slice 2).

Owner ruling 125d (2026-11-24): the owner-picked Focus replaces the
system-ranked list on Today. Focus is everything of the owner's own that is
due today, plus anything the owner put there, in the owner's order.

One small store, its own document, and one read:

  store   `_hq/data/hub-focus.json`, written only here, under its own lock:
            `items`  one row per pick, `{commitment_id, added_at, choice_id}`;
                     a take-out stamps `removed_at` and `removed_by` and keeps
                     the row (history stays additive, as the People stars).
            `outs`   one row per due-today item taken out, `{commitment_id,
                     due, out_at, choice_id}`: it leaves Focus while its due
                     date is still that day; a later add stamps `back_at`.
            `order`  `{ids, at, choice_id, prev}`: the owner's order, as the
                     page last sent it, and the one before it (for its Undo).
            `entered` one row per item that came into Focus on its own,
                     `{commitment_id, due, at}`, and `stamped_on` the last day
                     recorded, both written by the Update's build
                     (`mark_entered`, ruling 130a).
            `applied` the ids of the presses that wrote here, newest last,
                     so a press a stale page sends again is not applied twice.
          Ruling 111, #1768 and #1751's size guard: the plate and the people
          rows take no new field, so Focus lives here and nowhere else.
  feed    `feed(workspace_root)`: the rows Focus shows, in order, built from
          the open list (`open_list.read_list`, the one reading of what is
          open). Read only.
  all     `all_open(workspace_root)`: every open item, lean, for All open's
          search (ruling 128l). Read only.

The rules the store keeps, each pinned by HUBFOCUS1:

  * Focus never writes the book's items. Putting an item in Focus, taking it
    out or moving it never changes its due date, its title or anything else
    on its record; the only file written is the store above (and the Hub's
    one audit row per batch, written by the caller).
  * An item stays until it is done or taken out. Nothing clears overnight:
    nothing here reads the clock to drop a pick, and an item that came in on
    its own by being due today stays after its day, flagged `late`, until it
    is done or taken out (owner ruling 130a). An item already late before
    Focus started on this seat, or captured after its due day, never comes
    in on its own. A done item leaves the feed because it is no longer open,
    and an Undo of the done brings it back.
  * A press is one of `VERBS`, from the page, written at the next Update the
    way every other press is (`cockpit_helpers.apply_run`). Its Undo is the
    `undo` press naming it (`back_of`): it takes back exactly what that press
    wrote and nothing else, so an Undo never leaves Focus other than it was
    (review round 1 of #1797).

Synthetic sample data only in tests; nothing here names anyone.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock  # type: ignore

FOCUS_REL = Path("_hq") / "data" / "hub-focus.json"
STORE_VERSION = 1
VERBS = ("add", "remove", "order", "undo")
#: The press ids kept in the store's `applied` list (the newest).
APPLIED_CAP = 2000
#: An order press names at most this many items; the rest are not kept.
ORDER_CAP = 200
#: The feed carries at most this many rows (and counts the rest).
FEED_CAP = 200
TITLE_CAP = 200
ID_CAP = 80

ADDED_LINE = "Added to Focus: {title}."
ADDED_KEEP_LINE = "{title} is due today and now stays in Focus until it is done or taken out."
ADDED_ALREADY_LINE = "{title} is already in Focus; nothing was written."
REMOVED_LINE = "Taken out of Focus: {title}. Its due date is unchanged."
REMOVED_ALREADY_LINE = "That one is not in Focus; nothing was written."
ORDERED_LINE = "Focus reordered."
ORDERED_ALREADY_LINE = "Focus is already in that order; nothing was written."
ORDER_EMPTY_LINE = "That order names nothing in Focus; nothing was written."
NOT_OPEN_LINE = "That item is not open on your book, so it was not added to Focus; nothing was written."
NO_ITEM_LINE = "That press names no item; nothing was written."
BOOK_UNREADABLE_LINE = "Your open items could not be read just now, so nothing was written. The next update tries again."
STORE_UNREADABLE_LINE = "Your Focus list could not be read, so nothing was written. The next update tries again."
UNKNOWN_VERB_LINE = "Focus does not take that press ({verb}); nothing was written."
REPLAYED_LINE = "That press was already applied; nothing was written."
ORDER_SUPERSEDED_LINE = "A newer order from the page replaced this one; nothing was written for it."
UNDONE_LINE = "Put back as it was."
UNDO_NOTHING_LINE = "That press has nothing left to take back (it was changed since); nothing was written."
UNDO_NO_PRESS_LINE = "That Undo names no press; nothing was written."
#: Audit of #1823 (F3): an unread document is published as it is (the refresh
#: writes what a builder answers), replacing the page's copy, so the line never
#: says the last copy is kept.
FEED_UNREAD_LINE = "Your Focus list could not be read just now. The next update tries again."


def _now(now_iso: Optional[str] = None) -> _dt.datetime:
    if now_iso:
        try:
            dt = _dt.datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            pass
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cid(value: Any) -> str:
    return str(value or "").strip()[:ID_CAP]


# ---------------------------------------------------------------- the store

def focus_path(workspace_root) -> Path:
    return Path(str(workspace_root)) / FOCUS_REL


def _empty() -> Dict[str, Any]:
    return {"version": STORE_VERSION, "stamped_on": "", "entered": [], "items": [], "outs": [],
            "order": {"ids": [], "at": "", "choice_id": "", "prev": []}, "applied": []}


def _load_for_write(workspace_root) -> Dict[str, Any]:
    path = focus_path(workspace_root)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"focus unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ValueError("focus unreadable: not an object")
    data.setdefault("version", STORE_VERSION)
    # A field that is missing is empty; a field that is there in another shape
    # is a store this module did not write, so it is refused and left as it
    # is, never reset (review round 1 of #1797).
    for key in ("items", "outs", "applied", "entered"):
        if key not in data:
            data[key] = []
        elif not isinstance(data[key], list):
            raise ValueError(f"focus unreadable: {key} is not a list")
    if "order" not in data:
        data["order"] = {"ids": [], "at": "", "choice_id": "", "prev": []}
    elif not isinstance(data["order"], dict) or not isinstance(data["order"].get("ids", []), list) \
            or not isinstance(data["order"].get("prev", []), list):
        raise ValueError("focus unreadable: order is not an order")
    stamp = data.get("stamped_on", "")
    if not isinstance(stamp, str) or (stamp and not _DAY_RE.match(stamp)):
        raise ValueError("focus unreadable: stamped_on is not a day")
    data.setdefault("stamped_on", "")
    data["order"].setdefault("ids", [])
    data["order"].setdefault("prev", [])
    return data


def load_store(workspace_root) -> Tuple[Dict[str, Any], bool]:
    """(the store, unread). A missing store is an empty one; an unreadable
    one is an empty one with `unread` True, so a reader can say so."""
    try:
        return _load_for_write(workspace_root), False
    except ValueError:
        return _empty(), True


def picked_ids(store: Dict[str, Any]) -> List[str]:
    """The items picked now, oldest pick first."""
    rows = [r for r in store.get("items") or [] if isinstance(r, dict)
            and _cid(r.get("commitment_id")) and not r.get("removed_at")]
    rows.sort(key=lambda r: str(r.get("added_at") or ""))
    seen: set = set()
    out: List[str] = []
    for r in rows:
        cid = _cid(r["commitment_id"])
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def out_for(store: Dict[str, Any]) -> Dict[str, str]:
    """`{commitment id: due day}` for the due-today items taken out and not
    brought back. Such an item stays out only while its due date is that day."""
    out: Dict[str, str] = {}
    for r in store.get("outs") or []:
        if isinstance(r, dict) and _cid(r.get("commitment_id")) and not r.get("back_at"):
            out[_cid(r["commitment_id"])] = str(r.get("due") or "")
    return out


def _write(workspace_root, mutate, choice_id: Optional[str] = None) -> Dict[str, Any]:
    """The one write: under the store's lock, read, change, write whole. A
    press whose id is already in `applied` is not applied again (a stale page
    sending it twice); a press that changed the store is added to it."""
    path = focus_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_write_lock(path, holder="hub-focus")
    try:
        try:
            store = _load_for_write(workspace_root)
        except ValueError:
            return {"outcome": "error", "line": STORE_UNREADABLE_LINE}
        if choice_id and choice_id in store["applied"]:
            return {"outcome": "already", "line": REPLAYED_LINE}
        result, changed = mutate(store)
        if changed:
            if choice_id:
                store["applied"] = (store["applied"] + [choice_id])[-APPLIED_CAP:]
            atomic_write_json(path, store)
        return result
    finally:
        release_write_lock(lock)


# ---------------------------------------------------------------- the book (read only)

def _book(workspace_root, now: _dt.datetime) -> Tuple[Optional[Dict[str, Dict[str, Any]]], str]:
    """(`{id: open-list row}`, the workspace's today), or (None, today) when
    the open items cannot be read. Read only: the open list is never written
    here (`open_list.read_list` counts from the log when its file is stale)."""
    import open_list

    ws = Path(str(workspace_root))
    tzinfo = open_list._workspace_tz(ws)
    today = open_list._local_today(ws, now, tzinfo).isoformat()
    try:
        doc, _from_log = open_list.read_list(ws, now, tzinfo)
    except Exception:  # noqa: BLE001 - an unread book answers None, never a guess
        return None, today
    rows = {str(r.get("id")): r for r in (doc or {}).get("rows") or [] if isinstance(r, dict) and r.get("id")}
    return rows, today


#: Owner ruling 130a (2026-11-24, on the coordinator's second review of #1797):
#: an unfinished item that came into Focus on its own, by being due today,
#: STAYS after its day, tagged late, until it is done or taken out. True is
#: that ruling. Whether an item came in is RECORDED, never guessed (review of
#: 130a): each Update's build writes down the items in Focus on their own that
#: day (`mark_entered`), so an item whose due date was moved back, or that was
#: snoozed, waiting or someone else's on its day, never comes in late. False
#: would drop a late item at the day's end. `due_joins` is the only reader of
#: this line; HUBFOCUS1 [4g] pins both answers.
DUE_TODAY_CARRIES_OVER = True
#: Days with no Update (a weekend, a day the computer was off) are recorded at
#: the next Update, at most this many back: an item due on such a day was in
#: Focus on the page the owner had then, had it been built.
GAP_DAYS = 7
#: The recorded entries kept (the newest).
ENTERED_CAP = 5000
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def due_joins(due: str, today: str, entered: Any = ()) -> bool:
    """THE one rule for an item past its day (`DUE_TODAY_CARRIES_OVER`): it
    is in Focus on its own only when the store recorded it coming in on its
    own with this same due date (`entered`, the item's recorded due dates)."""
    if not due or due >= today:
        return False
    return DUE_TODAY_CARRIES_OVER and due in entered


def entered_dues(store: Dict[str, Any]) -> Dict[str, set]:
    """`{commitment id: {due dates it came into Focus on its own with}}`."""
    out: Dict[str, set] = {}
    for r in store.get("entered") or []:
        if isinstance(r, dict) and _cid(r.get("commitment_id")) and r.get("due"):
            out.setdefault(_cid(r["commitment_id"]), set()).add(str(r["due"]))
    return out


def _auto(row: Dict[str, Any], today: str, now: _dt.datetime, entered: Optional[Dict[str, set]] = None) -> bool:
    """In Focus without a pick: the owner's own work due today (not snoozed,
    not waiting on someone, not observed only), or an item past its day that
    came in that way and is still open (`due_joins`, ruling 130a). A late item
    that never came in does not (ruling 125d: no system-ranked list)."""
    import open_list

    due = str(row.get("due") or "")
    if due != today:
        return due_joins(due, today, (entered or {}).get(str(row.get("id") or ""), ()))
    return (due == today
            and row.get("direction") == "you_owe"
            and not row.get("observed")
            and not row.get("waiting_on")
            and not open_list.is_snoozed(row, now))


def _in_focus(cid: str, store: Dict[str, Any], book: Dict[str, Dict[str, Any]], today: str,
              now: _dt.datetime) -> bool:
    if cid in picked_ids(store):
        return True
    row = book.get(cid)
    if not row or not _auto(row, today, now, entered_dues(store)):
        return False
    return out_for(store).get(cid) != str(row.get("due") or "")


def _title(row: Optional[Dict[str, Any]], fallback: str = "") -> str:
    t = str((row or {}).get("title") or fallback or "That item").strip()
    return t[:TITLE_CAP]


def mark_entered(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """WRITE. Records the items in Focus on their own today, so each stays
    after its day (ruling 130a): the owner's own work due today, as `_auto`
    reads it now. Days since the last Update with none of its own (at most
    `GAP_DAYS` back) are recorded too, for the items due on them that were on
    the book by their day and are the owner's own and live now. `stamped_on`
    is the last day recorded; a stamp from a clock that ran ahead is not
    trusted (only today is recorded, and the stamp comes back). Called by the Update's
    build (`cockpit_helpers.update_build`), a writer; never by a read. An
    unreadable store or book is left as it is."""
    now = _now(now_iso)
    book, today = _book(workspace_root, now)
    if book is None:
        return {"outcome": "error", "line": BOOK_UNREADABLE_LINE}
    at = _iso(now)
    t = _dt.date.fromisoformat(today)

    def mutate(store):
        last = str(store.get("stamped_on") or "")
        floor = (t - _dt.timedelta(days=GAP_DAYS)).isoformat()
        # the days after the last one recorded, never more than GAP_DAYS back;
        # none on a first run or after a clock that ran ahead (today only)
        lo = today if not last or last > today else max(last, floor)
        have = {(_cid(r.get("commitment_id")), str(r.get("due") or "")) for r in store["entered"] if isinstance(r, dict)}
        new = []
        for cid, row in book.items():
            due = str(row.get("due") or "")
            if not due or due > today or (cid, due) in have:
                continue
            if due == today:
                ok = _auto(row, today, now)
            else:
                # a gap day: after the last day recorded, on the book by then, the owner's own and live now
                ok = (lo < due and str(row.get("opened") or "") <= due
                      and _auto(dict(row, due=today), today, now))
            if ok:
                new.append({"commitment_id": cid, "due": due, "at": at})
        moved = last != today
        if not new and not moved:
            return {"outcome": "already", "entered": 0, "stamped_on": today}, False
        store["entered"] = (store["entered"] + new)[-ENTERED_CAP:]
        store["stamped_on"] = today
        return {"outcome": "applied", "entered": len(new), "stamped_on": today}, True

    return _write(workspace_root, mutate)


# ---------------------------------------------------------------- the presses

def add(workspace_root, commitment_id: Any, *, choice_id: Optional[str] = None,
        title: str = "", now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Put an open item in Focus. Writes the store only; the item's record,
    its due date included, is not touched."""
    cid = _cid(commitment_id)
    if not cid:
        return {"outcome": "refused", "line": NO_ITEM_LINE}
    now = _now(now_iso)
    book, today = _book(workspace_root, now)
    if book is None:
        return {"outcome": "error", "line": BOOK_UNREADABLE_LINE}
    row = book.get(cid)
    if row is None:
        return {"outcome": "refused", "line": NOT_OPEN_LINE}
    name = _title(row, title)
    at = _iso(now)

    def mutate(store):
        back = [r for r in store["outs"] if isinstance(r, dict) and _cid(r.get("commitment_id")) == cid
                and not r.get("back_at")]
        for r in back:
            r["back_at"] = at
            r["back_by"] = choice_id or ""
        if cid in picked_ids(store):
            return ({"outcome": "already", "line": ADDED_ALREADY_LINE.format(title=name)}, bool(back))
        was_auto = _auto(row, today, now, entered_dues(store)) and not back
        ids = [i for i in store["order"].get("ids") or [] if isinstance(i, str)]
        placed = cid not in ids
        store["items"].append({"commitment_id": cid, "added_at": at, "choice_id": choice_id or "",
                               **({"placed": True} if placed else {})})
        if placed:
            store["order"]["ids"] = ids + [cid]
        line = ADDED_KEEP_LINE if was_auto else ADDED_LINE
        return {"outcome": "applied", "line": line.format(title=name)}, True

    return _write(workspace_root, mutate, choice_id)


def remove(workspace_root, commitment_id: Any, *, choice_id: Optional[str] = None,
           title: str = "", now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Take an item out of Focus: its pick is stamped removed and, when it is
    due today, it is set out for that day. Writes the store only."""
    cid = _cid(commitment_id)
    if not cid:
        return {"outcome": "refused", "line": NO_ITEM_LINE}
    now = _now(now_iso)
    book, today = _book(workspace_root, now)
    if book is None:
        return {"outcome": "error", "line": BOOK_UNREADABLE_LINE}
    row = book.get(cid)
    name = _title(row, title)
    at = _iso(now)

    def mutate(store):
        live = [r for r in store["items"] if isinstance(r, dict) and _cid(r.get("commitment_id")) == cid
                and not r.get("removed_at")]
        for r in live:
            r["removed_at"] = at
            r["removed_by"] = choice_id or ""
        auto = bool(row) and _auto(row, today, now, entered_dues(store)) and out_for(store).get(cid) != str(row.get("due") or "")
        if auto:
            store["outs"].append({"commitment_id": cid, "due": str(row.get("due") or ""), "out_at": at,
                                  "choice_id": choice_id or ""})
        if not live and not auto:
            return {"outcome": "already", "line": REMOVED_ALREADY_LINE}, False
        return {"outcome": "applied", "line": REMOVED_LINE.format(title=name)}, True

    return _write(workspace_root, mutate, choice_id)


def order(workspace_root, ids: Any, *, choice_id: Optional[str] = None,
          now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The owner's order, as the page sent it: the ids in Focus now, in that
    order, first seen kept. Ids not in Focus are left out. Writes the store
    only."""
    wanted: List[str] = []
    for raw in (ids if isinstance(ids, list) else [])[:ORDER_CAP]:
        cid = _cid(raw)
        if cid and cid not in wanted:
            wanted.append(cid)
    now = _now(now_iso)
    book, today = _book(workspace_root, now)
    if book is None:
        return {"outcome": "error", "line": BOOK_UNREADABLE_LINE}
    at = _iso(now)

    def mutate(store):
        keep = [c for c in wanted if _in_focus(c, store, book, today, now)]
        if not keep:
            return {"outcome": "refused", "line": ORDER_EMPTY_LINE}, False
        old = [i for i in store["order"].get("ids") or [] if isinstance(i, str)]
        # the ids the press did not name keep their place after the named ones
        rest = [i for i in old if i not in keep]
        new = keep + rest
        if new == old:
            return {"outcome": "already", "line": ORDERED_ALREADY_LINE}, False
        store["order"] = {"ids": new, "at": at, "choice_id": choice_id or "", "prev": old}
        return {"outcome": "applied", "line": ORDERED_LINE}, True

    return _write(workspace_root, mutate, choice_id)


def undo(workspace_root, back_of: Any, *, choice_id: Optional[str] = None,
         now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The Undo of one Focus press (`back_of`, its id): exactly what that
    press wrote is taken back, and nothing else. An add's pick is stamped
    removed and any take-out it cleared stands again; a remove's picks stand
    again and any take-out it made is cleared; an order's list goes back to
    the one before it, while it is still the order now. What a later press
    changed since is left as it is. Writes the store only."""
    bid = str(back_of or "").strip()[:160]
    if not bid:
        return {"outcome": "refused", "line": UNDO_NO_PRESS_LINE}
    at = _iso(_now(now_iso))

    def mutate(store):
        hit = False
        for r in store["items"]:
            if not isinstance(r, dict):
                continue
            if r.get("choice_id") == bid and not r.get("removed_at"):
                r["removed_at"], r["removed_by"] = at, choice_id or ""
                hit = True
                if r.get("placed"):
                    # the add put it last in the order; its Undo takes that place back
                    cid = _cid(r.get("commitment_id"))
                    store["order"]["ids"] = [i for i in store["order"].get("ids") or [] if i != cid]
            elif r.get("removed_at") and r.get("removed_by") == bid:
                r.pop("removed_at", None)
                r.pop("removed_by", None)
                hit = True
        for r in store["outs"]:
            if not isinstance(r, dict):
                continue
            if r.get("back_at") and r.get("back_by") == bid:
                r.pop("back_at", None)
                r.pop("back_by", None)
                hit = True
            elif r.get("choice_id") == bid and not r.get("back_at"):
                r["back_at"], r["back_by"] = at, choice_id or ""
                hit = True
        od = store["order"]
        if od.get("choice_id") == bid:
            store["order"] = {"ids": list(od.get("prev") or []), "at": at, "choice_id": choice_id or "",
                              "prev": list(od.get("ids") or [])}
            hit = True
        if not hit:
            return {"outcome": "already", "line": UNDO_NOTHING_LINE}, False
        return {"outcome": "applied", "line": UNDONE_LINE}, True

    return _write(workspace_root, mutate, choice_id)


def sequence(choices: Optional[List[Dict[str, Any]]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(the presses in the order to apply them, the orders a newer one
    replaced). The page's query has no order and press ids are random, so
    one Update's presses are put in the order they were pressed
    (`created_at`, coordinator's second review of #1797): every add, remove
    and Undo first, in that order; then only the newest order, so an order
    never runs before an add it names and two reorders never land reversed;
    then any Undo of an order, after the order it takes back."""
    rows = sorted((c for c in choices or [] if isinstance(c, dict)),
                  key=lambda c: str(c.get("created_at") or ""))

    def verb(c):
        return str(c.get("action") or "").strip().lower()

    orders = [c for c in rows if verb(c) == "order"]
    order_ids = {str(c.get("id") or "") for c in orders}
    of_order = [c for c in rows if verb(c) == "undo" and str(c.get("back_of") or "") in order_ids]
    first = [c for c in rows if verb(c) != "order" and c not in of_order]
    return first + orders[-1:] + of_order, orders[:-1]


def apply_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None, *,
                  now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{ok, results: [{id, outcome, line}]}` for Focus presses, each
    `{id, action, commitment_id, order, back_of, title}` (`cockpit_helpers.plan_apply`'s
    `focus` list). One bad row never stops the rest."""
    results = []
    ordered, replaced = sequence(choices)
    for c in replaced:
        results.append({"outcome": "already", "line": ORDER_SUPERSEDED_LINE, "id": c.get("id")})
    for c in ordered:
        verb = str(c.get("action") or "").strip().lower()
        cid = str(c.get("id") or "").strip() or None
        try:
            if verb == "add":
                res = add(workspace_root, c.get("commitment_id"), choice_id=cid,
                          title=str(c.get("title") or ""), now_iso=now_iso)
            elif verb == "remove":
                res = remove(workspace_root, c.get("commitment_id"), choice_id=cid,
                             title=str(c.get("title") or ""), now_iso=now_iso)
            elif verb == "order":
                res = order(workspace_root, c.get("order"), choice_id=cid, now_iso=now_iso)
            elif verb == "undo":
                res = undo(workspace_root, c.get("back_of"), choice_id=cid, now_iso=now_iso)
            else:
                res = {"outcome": "refused", "line": UNKNOWN_VERB_LINE.format(verb=verb or "none")}
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the rest
            res = {"outcome": "error", "line": STORE_UNREADABLE_LINE, "error": f"{type(exc).__name__}: {exc}"[:240]}
        res = dict(res)
        res["id"] = c.get("id")
        results.append(res)
    return {"ok": True, "results": results}


# ---------------------------------------------------------------- the feed

#: All open's document holds at most this many rows. Measured (HUBFOCUSPAGE1
#: [7b]): 599 rows whose titles were all at full length (`TITLE_CAP`) came to
#: 219 KB, past the page's 215 KB limit per document; 500 stays under it with
#: every title at full length. Past it the document says `complete: false` and
#: the page says more are open; pages of their own are the follow-up if a real
#: book needs them.
ALL_OPEN_CAP = 500


def other_side(row: Dict[str, Any]) -> List[str]:
    """cr1#1887 (decision 152b): the person ids on the other side of an
    open-list row, in order, for who a Focus or All open row names: the
    holder (`owner_id`) on a row owed to the owner (`owed_to_you`), else the
    row's counterparties (`you_owe`, `unowned`, or an `owed_to_you` row with
    no holder). The open list's directions are `you_owe`, `owed_to_you` and
    `unowned` (`open_list.DIRECTIONS`); there is no `they_owe`. Pure."""
    holder = str(row.get("owner_id") or "").strip()
    if str(row.get("direction") or "") == "owed_to_you" and holder:
        return [holder]
    return [str(p) for p in row.get("counterparty_ids") or [] if str(p)]


def _tied_ids(row: Dict[str, Any]) -> set:
    """cr1#1887: every person an open-list row is tied to, the holder and
    every counterparty, whatever its direction: what the hidden filter
    checks. On the owner's own row the holder is the owner, never hidden."""
    return ({str(row.get("owner_id") or "").strip()}
            | {str(p) for p in row.get("counterparty_ids") or [] if str(p)}) - {""}


def all_open(workspace_root, *, now_iso: Optional[str] = None,
             names: Optional[Dict[str, str]] = None,
             threads: Optional[Dict[str, str]] = None,
             hidden_ids: Optional[set] = None) -> Dict[str, Any]:
    """READ. Every open item, lean, for To do's All open and its search
    (cr1#1785 page part, ruling 128l): `{id, title, due, late, due_today,
    who, who_id, where, mine}` each, ordered late first, then due today,
    then by due date, then undated. The plate's row cap does not apply:
    a search on the page covers every row here. `who_id` is the caller's to
    drop after the wall is applied. `complete` says whether every open item
    fits (`ALL_OPEN_CAP`); `ok: false` when the open items cannot be read.
    `hidden_ids` (audit of #1823, F2): an item tied to any of them is left out
    of the whole book before anything is counted, so `n` is the number of
    open items the page may list and says nothing of the hidden ones."""
    now = _now(now_iso)
    book, today = _book(workspace_root, now)
    base = {"generated_at": _iso(now), "today": today}
    if book is None:
        return dict(base, ok=False, line=FEED_UNREAD_LINE, rows=[], n=0, complete=False)
    names, threads = names or {}, threads or {}

    def rank(r):
        d = str(r.get("due") or "")
        return (0 if d and d < today else 1 if d == today else 2 if d else 3, d, str(r.get("opened") or ""),
                str(r.get("id")))

    hidden = {str(i) for i in hidden_ids or ()}
    # cr1#1887: the holder too, so an item a hidden person owes the owner is left out
    listable = [r for r in book.values() if not (hidden & _tied_ids(r))]
    rows = []
    for r in sorted(listable, key=rank)[:ALL_OPEN_CAP]:
        # cr1#1887: the other side, the holder on a row owed to the owner
        who_id = (other_side(r) or [""])[0]
        due = str(r.get("due") or "")
        rows.append({"id": str(r["id"]), "title": _title(r), "due": due, "late": bool(due) and due < today,
                     "due_today": due == today, "who": names.get(who_id, "") if who_id else "", "who_id": who_id,
                     "where": str(threads.get(str(r.get("thread_id") or "")) or "")[:80],
                     "mine": str(r.get("direction") or "") == "you_owe"})
    return dict(base, ok=True, rows=rows, n=len(listable), complete=len(listable) <= ALL_OPEN_CAP)


def feed(workspace_root, *, now_iso: Optional[str] = None,
         names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """READ. The rows Focus shows, in order: the owner's order first, then
    the other picks oldest first, then the rest that came in on their own
    (due today, or carried over late, ruling 130a). Each row is
    `{id, title, due, due_today, picked, late, direction, confirmed, who, who_id}`;
    the caller scrubs who is named (`who_id` is the caller's to drop). A
    closed item is not a row; its pick stays in the store, so an Undo of the
    close brings it back. `ok: false` with a line when the store or the open
    items cannot be read."""
    now = _now(now_iso)
    store, unread = load_store(workspace_root)
    book, today = _book(workspace_root, now)
    base = {"generated_at": _iso(now), "today": today}
    if unread or book is None:
        return dict(base, ok=False, line=FEED_UNREAD_LINE, rows=[], n=0, n_hidden=0)
    picked = picked_ids(store)
    outs = out_for(store)
    names = names or {}
    in_focus: List[str] = [c for c in picked if c in book]
    entered = entered_dues(store)
    auto = [r for r in book.values() if _auto(r, today, now, entered) and str(r["id"]) not in in_focus
            and outs.get(str(r["id"])) != str(r.get("due") or "")]
    # confirmed first, then the book's own order (oldest due, then opened)
    auto.sort(key=lambda r: (not r.get("confirmed"), str(r.get("opened") or ""), str(r["id"])))
    in_focus += [str(r["id"]) for r in auto]
    rank = {c: i for i, c in enumerate(i for i in store["order"].get("ids") or [] if isinstance(i, str))}
    pick_rank = {c: i for i, c in enumerate(picked)}
    n_named = len(rank)
    ordered = sorted(in_focus, key=lambda c: (rank.get(c, n_named),
                                               0 if c in pick_rank else 1,
                                               pick_rank.get(c, 0)))
    rows = []
    for c in ordered[:FEED_CAP]:
        r = book[c]
        # cr1#1887: the other side, the holder on a row owed to the owner
        who_id = (other_side(r) or [""])[0]
        rows.append({"id": c, "title": _title(r), "due": str(r.get("due") or ""),
                     "due_today": str(r.get("due") or "") == today, "picked": c in pick_rank,
                     # ruling 130a: an item past its day is tagged Late on the page
                     "late": bool(r.get("due")) and str(r.get("due")) < today,
                     "direction": str(r.get("direction") or ""), "confirmed": bool(r.get("confirmed")),
                     "who": names.get(who_id, "") if who_id else "", "who_id": who_id})
    return dict(base, ok=True, rows=rows, n=len(ordered), n_hidden=max(0, len(ordered) - FEED_CAP))
