#!/usr/bin/env python3
"""The Prep page's own edits: the agenda, the Goal, and the call guide's adds
and removes (cr1#1788, Hub v6.12 slice 5, backend).

The prep itself is made by the full prep (call-prep, Hub Morning's prep part)
and reaches the page on `feed/today` (`prep_parts`, and the invite's own text
as `invite_text`). What the owner changes on the Prep page is saved as
`prepedit` presses and written here at the next Update
(`cockpit_helpers.apply_run`), so an edit survives the next prep rebuild: the
page lays `feed/prepx` (this store, walled) over the rebuilt prep, and the
next brief build reads it (`edits_for`) the way it reads carried points.

One small store, written only here, under its own lock:

  `_hq/data/hub-prep-edits.json`
    `meetings`  one row per meeting occurrence with an edit, keyed by the
                calendar key the page's rows carry (`key`, 32 hex, cr1#1758)
                AND the occurrence's start (`start`, as `feed/today` writes
                it): a recurring meeting's occurrences share one key, so a
                key alone would lay one week's agenda on every week:
                `agenda`  `{text, at, choice_id}` or null: the owner's own
                          agenda / purpose, which replaces the invite's;
                `goal`    `{text, at, choice_id}` or null: the one outcome to
                          walk out with, which replaces the prep's `walk`;
                `adds`    `[{id, kind, text, at, choice_id}]`: a topic or a
                          question the owner added to the call guide;
                `removes` `[{item_id, at, choice_id}]`: a guide item the owner
                          took out, named by its id (`guide_id`).
    `history`   one row per press that changed a meeting, its before and
                after, so its Undo restores exactly that (rulings 43 and 78).
    `applied`   the ids of the presses that wrote here.

The rules, each pinned by HUBPREPEDIT1:
  * Edits are the owner's and survive any rebuild: nothing here reads the
    prep, and a rebuild never writes here.
  * Words that name someone off limits or on the personal side are refused.
  * Nothing crosses between runs through a temporary folder: the store is in
    the book's folder, read again by every reader (the temporary-folder rule).
  * The store does not only grow: an occurrence that started more than
    `EXPIRE_DAYS` ago is dropped at the next write, with its history.
  * A meeting that moved keeps its edits (fix check, MUST 2): when the
    calendar read shows its key at exactly one start and exactly one stored
    occurrence of that key sits at a start INSIDE the read's window that the
    read no longer has, the edit is laid on the new start (`place`, on the
    Today row) and the next press there moves it in the store (`moved_from`).
    A past occurrence is never a move (second check, MUST 1: a fortnightly
    meeting's last one, or a cancelled one, is outside or behind the read).
    When it is not clear, the edit stays at its old start and the row says so
    (`prepx_unplaced`); a moved edit whose words name someone hidden is held
    and said (`prepx_held`); every other stored edit from the start of today
    to the read's end that the read does not show at its time (cancelled,
    moved past the read, made again, slipped after it started, an all-day
    one moved) is counted and said (`gone`), never moved.
  * An edit for a meeting more than `EXPIRE_DAYS` past is refused, never
    saved and then dropped.
  * A store that cannot be read is NEVER replaced (decision 141w): every
    press is refused with a plain line (it may be mid-sync, it is left as it
    is, edits are off until it reads again), nothing is written, and the
    health check says the file cannot be read. Its repair is its own change
    (decision 141y, cr1#1847).
  * Nothing is cut without a line: `feed/prepx` carries `lines` for what it
    left out or could not read.
  * `feed/prepx` lists only the occurrences in its window (from
    `FEED_PAST_DAYS` back to `FEED_AHEAD_DAYS` ahead) and stays under
    `PREPX_DOC_BUDGET`, well under the page's document limit: past it the
    occurrences furthest ahead are left off first, counted in `n_cut` (they
    are kept, and are listed as their day comes nearer).

What the Prep page reads (its builder's contract; the page is not on this
branch, so nothing below is drawn yet):
  `feed/prepx`  `meetings`: `{"<jk>|<start>": {agenda, goal, adds, removed}}`,
                the row's own `jk` and `start` on `feed/today`; `ok`; and
                `lines`, plain sentences the page shows as they are when it
                shows less (store or wall unread, `n_cut`, `n_held`).
  `feed/today`  on a row: `invite_text` (the next `INVITE_ROWS_CAP` rows only);
                `prep_parts.ask_closed` (questions of `ask` flagged yes-or-no,
                drawn beside them); `prepx_moved` `{from, edit}` (the edit
                made for the meeting's old time: lay it on, and send
                `moved_from` with the next press); `prepx_unplaced` (edits
                kept at another time of this meeting); `prepx_held` (a
                moved edit not shown: its words name someone hidden). On the
                document: `prepx_line` (say it when any row is unplaced or
                held, or edits sit on a meeting no longer on the calendar) and
                `size_lines` (say them when invite texts were cut or the
                document is over its budget).
  Every press: `meeting_key` (the row's `jk`), `start` (the row's `start`),
                and `moved_from` when the row carried `prepx_moved`.
  Week rows carry no `prepx_*`: the Prep page opens from Today's rows only.

stdlib only. Never sends. Synthetic sample data only in tests.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
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

EDITS_REL = Path("_hq") / "data" / "hub-prep-edits.json"
STORE_VERSION = 1
VERBS = ("agenda", "goal", "add", "remove", "undo")
GUIDE_KINDS = ("topic", "question")
TEXT_CAP = 1200
ITEM_CAP = 300
ADDS_CAP = 30
REMOVES_CAP = 60
APPLIED_CAP = 2000
HISTORY_CAP = 1000
MEETINGS_CAP = 400
#: An occurrence's edits are dropped this many days after it started.
EXPIRE_DAYS = 14
#: feed/prepx's window: from this many days back ...
FEED_PAST_DAYS = 1
#: ... to this many days ahead (the Week board's seven days and one more).
FEED_AHEAD_DAYS = 8
#: feed/prepx's own size budget, in characters of its JSON: well under
#: `cockpit_helpers.REFRESH_DOC_LIMIT` (215,000), past which a document is
#: not published at all and the page keeps a stale copy.
PREPX_DOC_BUDGET = 120_000
KEY_RE = re.compile(r"^[0-9a-f]{32}$")
ITEM_ID_RE = re.compile(r"^g_[0-9a-f]{12}$")

SAVED_LINE = "Saved. It stays when the prep is made again."
CLEARED_LINE = "Cleared. The prep's own words show again."
ALREADY_LINE = "That is already saved; nothing was written."
ADDED_LINE = "Added to the call guide."
REMOVED_LINE = "Taken off the call guide."
NO_KEY_LINE = "That press names no meeting; nothing was written."
NO_START_LINE = "That press does not say which time of the meeting it is for; nothing was written."
TOO_MANY_REMOVES_LINE = "The call guide already has as many items taken out as it can take; nothing was written."
NO_ITEM_LINE = "That press names no item of the call guide; nothing was written."
BAD_KIND_LINE = "The call guide takes a topic or a question; nothing was written."
EMPTY_LINE = "That item has no words; nothing was written."
NAMED_LINE = "Those words name someone who cannot be named here; nothing was written."
#: cr1#1788 review: an errored press is marked applied (`apply_run`), so these lines
#: ask for the press again rather than promise a retry.
WALL_UNREAD_LINE = "Who may be named could not be checked just now, so nothing was written. Press Go, then make the change again."
STORE_UNREADABLE_LINE = ("Your prep edits file could not be read, so nothing was written. It may be mid-sync, so it "
                         "is left as it is, and edits on the Prep page are off until it reads again.")
LOCK_BUSY_LINE = "Another change to your prep edits was being saved just then, so nothing was written. Press Go, then make the change again."
PAST_LINE = "That meeting is more than two weeks past, so its prep is no longer kept; nothing was written."
#: feed/prepx's lines (fix check, required item a): what the page says when it shows less.
PREPX_UNREAD_LINE = ("Your prep edits could not be read, so none are shown. It may be mid-sync, so the file is left "
                     "as it is, and edits on the Prep page are off until it reads again.")
PREPX_WALL_LINE = "Your prep edits are not shown just now: who may be named could not be checked. The next update tries again."
PREPX_CUT_LINE = "Prep edits for {n} later meetings are not shown yet; they are kept and show as the day comes nearer."
PREPX_HELD_LINE = "Prep edits for {n} meetings are not shown: their words name someone who cannot be named here."
UNKNOWN_VERB_LINE = "The Prep page does not take that press ({verb}); nothing was written."
REPLAYED_LINE = "That press was already applied; nothing was written."
UNDONE_LINE = "Put back as it was."
UNDO_NOTHING_LINE = "That press has nothing left to take back (it was changed since); nothing was written."
UNDO_NO_PRESS_LINE = "That Undo names no press; nothing was written."
TOO_MANY_LINE = "The call guide already has as many added items as it can take; nothing was written."


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


def norm_key(value: Any) -> str:
    k = str(value or "").strip().lower()
    return k if KEY_RE.match(k) else ""


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def norm_start(value: Any) -> str:
    """An occurrence's start as the store keeps it: a timed start in UTC as
    `feed/today` writes it (`2026-03-04T17:00:00Z`), an all-day one as its
    date. "" for none, or for a time with no zone (which instant it names
    cannot be told). Pure."""
    v = str(value or "").strip()
    if _DATE_RE.match(v):
        return v
    if not v or len(v) > 40:
        return ""
    try:
        dt = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return _iso(dt) if dt.tzinfo else ""


def occurrence(key: Any, start: Any) -> str:
    """`<key>|<start>`, the one name of an occurrence on `feed/prepx`, or ""
    when either part is missing. Pure."""
    k, st = norm_key(key), norm_start(start)
    return k + "|" + st if k and st else ""


def _start_dt(start: Any) -> Optional[_dt.datetime]:
    st = norm_start(start)
    if not st:
        return None
    if _DATE_RE.match(st):
        return _dt.datetime.fromisoformat(st).replace(tzinfo=_dt.timezone.utc)
    return _dt.datetime.fromisoformat(st.replace("Z", "+00:00"))


def clean(text: Any, cap: int) -> str:
    """Words as kept: whitespace folded, capped."""
    return " ".join(str(text or "").split())[:cap]


def guide_id(kind: Any, text: Any) -> str:
    """The id of a call-guide item, the same for the same words on any run:
    `g_` and 12 hex of the item's kind and its folded words. The prep's own
    topics and questions and the owner's adds are named by it, so a remove
    survives a rebuild that keeps the item. Pure."""
    raw = str(kind or "").strip().lower() + "|" + clean(text, ITEM_CAP).casefold()
    return "g_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- the store

def edits_path(workspace_root) -> Path:
    return Path(str(workspace_root)) / EDITS_REL


def _empty() -> Dict[str, Any]:
    return {"version": STORE_VERSION, "meetings": [], "history": [], "applied": []}


def _load_for_write(workspace_root) -> Dict[str, Any]:
    path = edits_path(workspace_root)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"prep edits unreadable: {type(exc).__name__}") from exc
    return _checked(data)


def _checked(data: Any) -> Dict[str, Any]:
    """The store's own check, for the live file and a kept copy alike
    (cr1#1847): a ValueError when it is not a store."""
    if not isinstance(data, dict):
        raise ValueError("prep edits unreadable: not an object")
    data.setdefault("version", STORE_VERSION)
    for k in ("meetings", "history", "applied"):
        if k not in data:
            data[k] = []
        elif not isinstance(data[k], list):
            raise ValueError(f"prep edits unreadable: {k} is not a list")
    for m in data["meetings"]:
        if not isinstance(m, dict) or not isinstance(m.get("adds", []), list) \
                or not isinstance(m.get("removes", []), list):
            raise ValueError("prep edits unreadable: a meeting is not an edit")
    return data


def load_store(workspace_root) -> Tuple[Dict[str, Any], bool]:
    try:
        return _load_for_write(workspace_root), False
    except ValueError:
        return _empty(), True


def find(store: Dict[str, Any], key: Any, start: Any) -> Optional[Dict[str, Any]]:
    """The edit of one occurrence: the same key AND the same start. A row
    with no start (or another start) is another occurrence's. Pure."""
    occ = occurrence(key, start)
    if not occ:
        return None
    for m in store.get("meetings") or []:
        if isinstance(m, dict) and occurrence(m.get("key"), m.get("start")) == occ:
            return m
    return None


def edits_for(workspace_root, key: Any, start: Any) -> Optional[Dict[str, Any]]:
    """READ. One occurrence's edits as the page and the next brief build
    read them: `{agenda, goal, adds: [{id, kind, text}], removed: [item ids]}`
    (agenda and goal text or ""), `{}` for none, `{"held": True}` when its
    words now name someone hidden, None when the store or who is hidden
    cannot be read (a reader then lays nothing over the prep and says so)."""
    store, unread = load_store(workspace_root)
    if unread:
        return None
    rec = find(store, key, start)
    if not rec:
        return {}
    v = view(rec)
    named = _named(workspace_root, _words(v))
    if named is None:
        return None
    return {"held": True} if named else v


def _words(v: Dict[str, Any]) -> List[str]:
    return [v["agenda"], v["goal"]] + [a["text"] for a in v["adds"]]


def _inside(m: Dict[str, Any], lo: _dt.datetime, hi: _dt.datetime) -> bool:
    st = _start_dt(m.get("start"))
    return st is not None and lo <= st < hi


def _orphans(store: Dict[str, Any], k: str, seen: set, window: Tuple[_dt.datetime, _dt.datetime]) -> List[Dict[str, Any]]:
    """The stored occurrences of key `k` at a start the read does not have,
    inside `window`. Pure."""
    lo, hi = window
    return [m for m in store.get("meetings") or [] if isinstance(m, dict) and norm_key(m.get("key")) == k
            and norm_start(m.get("start")) and norm_start(m.get("start")) not in seen and _inside(m, lo, hi)]


def place(store: Dict[str, Any], key: Any, start: Any, read_starts: Any,
          window: Tuple[_dt.datetime, _dt.datetime]) -> Tuple[Optional[Dict[str, Any]], str, int]:
    """Where one calendar row's edits are (fix check, MUST 2): `(rec,
    moved_from, unplaced)`. An orphan is a stored occurrence of this key at a
    start the read no longer has, INSIDE the read's `window` (`(lo, hi)`:
    `lo` now, `hi` where the read itself ends, the workspace's midnight after
    its last day): a past occurrence, or one beyond the read, is never a move
    (second check, MUST 1). The row's own occurrence first,
    with any orphan beside it counted as unplaced (the owner typed at the
    new time). Else, when the read shows this key at exactly one start (this
    row's) and there is exactly one orphan, that one (the meeting moved),
    with its old start. Else `(None, "", n)`, `n` the orphans. Pure."""
    k = norm_key(key)
    if not k:
        return None, "", 0
    seen = {norm_start(x) for x in read_starts or ()} - {""}
    orphans = _orphans(store, k, seen, window)
    rec = find(store, key, start)
    if rec:
        return rec, "", len(orphans)
    if len(seen) == 1 and norm_start(start) in seen and len(orphans) == 1:
        return orphans[0], norm_start(orphans[0].get("start")), 0
    return None, "", len(orphans)


def today_places(workspace_root, rows: List[Dict[str, Any]], read: Dict[str, Any],
                 window: Tuple[_dt.datetime, _dt.datetime],
                 say_from: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """READ. For the Today feed: each row's moved edit, laid on its new
    start, and the edits the read cannot place. `read` is `{key: [starts]}`
    over the whole calendar read, `window` its `(lo, hi)` from now to where
    the read ends. Answers `{"rows": {index: {"from", "edit"} or
    {"unplaced": n} or {"held": True}}, "gone": n}`. `gone` counts every
    other stored occurrence from `say_from` (the start of the workspace's
    day; default `lo`) to `hi` that the read does not show at its time and
    no row has said: a meeting cancelled, moved past the read or made again
    under a new id, one that slipped after it started, an all-day one moved
    from today. Those are never moves, only said. `{}` when the store or who
    is hidden cannot be read (feed/prepx says so)."""
    import followup_draft as fd

    store, unread = load_store(workspace_root)
    if unread or not store.get("meetings"):
        return {}
    hw = fd.hidden_words(Path(str(workspace_root)))
    if hw is None:
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    said: set = set()
    for i, r in enumerate(rows):
        k = norm_key(r.get("jk"))
        if not k or not norm_start(r.get("start")):
            continue
        seen = {norm_start(x) for x in read.get(k) or ()} - {""}
        rec, moved_from, unplaced = place(store, k, r.get("start"), read.get(k) or (), window)
        if rec is not None and moved_from:
            v = view(rec)
            out[i] = {"held": True} if fd.names_hidden(_words(v), hw) else {"from": moved_from, "edit": v}
            said.add(occurrence(k, moved_from))
        elif unplaced:
            out[i] = {"unplaced": unplaced}
            said |= {occurrence(m.get("key"), m.get("start")) for m in _orphans(store, k, seen, window)}
    lo, hi = window
    shown = {occurrence(k, st) for k, starts in read.items() for st in starts or ()}
    gone = sum(1 for m in store.get("meetings") or [] if isinstance(m, dict)
               and occurrence(m.get("key"), m.get("start")) and _inside(m, say_from or lo, hi)
               and occurrence(m.get("key"), m.get("start")) not in shown | said)
    return {"rows": out, "gone": gone}


def view(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The edit as read. Pure."""
    a = rec.get("agenda") if isinstance(rec.get("agenda"), dict) else None
    g = rec.get("goal") if isinstance(rec.get("goal"), dict) else None
    return {"agenda": clean((a or {}).get("text"), TEXT_CAP), "goal": clean((g or {}).get("text"), TEXT_CAP),
            "adds": [{"id": str(x.get("id")), "kind": x.get("kind"), "text": clean(x.get("text"), ITEM_CAP)}
                     for x in rec.get("adds") or [] if isinstance(x, dict) and ITEM_ID_RE.match(str(x.get("id") or ""))
                     and x.get("kind") in GUIDE_KINDS],
            "removed": sorted({str(r.get("item_id")) for r in rec.get("removes") or []
                               if isinstance(r, dict) and ITEM_ID_RE.match(str(r.get("item_id") or ""))})}


def _expire(store: Dict[str, Any], now: _dt.datetime) -> bool:
    """Drop every occurrence that started more than `EXPIRE_DAYS` before
    `now` (and one with no readable start, which no press can name), with
    its history rows; then, past `MEETINGS_CAP`, the earliest-starting.
    True when anything was dropped."""
    cut = now - _dt.timedelta(days=EXPIRE_DAYS)
    live = []
    for m in store["meetings"]:
        st = _start_dt(m.get("start")) if isinstance(m, dict) else None
        if st is not None and st >= cut and norm_key(m.get("key")):
            live.append(m)
    if len(live) > MEETINGS_CAP:
        live = sorted(live, key=lambda m: _start_dt(m.get("start")))[-MEETINGS_CAP:]
    if len(live) == len(store["meetings"]):
        return False
    keep = {occurrence(m.get("key"), m.get("start")) for m in live}

    def occ_of(h):
        rec = h.get("after") if isinstance(h.get("after"), dict) else h.get("before")
        return occurrence(rec.get("key"), rec.get("start")) if isinstance(rec, dict) else ""

    store["meetings"] = [m for m in store["meetings"] if any(m is x for x in live)]
    store["history"] = [h for h in store["history"] if isinstance(h, dict) and occ_of(h) in keep]
    return True


def _write(workspace_root, mutate, choice_id: Optional[str] = None, now_iso: Optional[str] = None) -> Dict[str, Any]:
    path = edits_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock = acquire_write_lock(path, holder="hub-prep-edit")
    except TimeoutError:
        return {"outcome": "error", "line": LOCK_BUSY_LINE}
    try:
        try:
            store = _load_for_write(workspace_root)
        except ValueError:
            # decision 141w: never replaced, and nothing written beside it
            return {"outcome": "error", "line": STORE_UNREADABLE_LINE}
        if choice_id and choice_id in store["applied"]:
            return {"outcome": "already", "line": REPLAYED_LINE}
        result, changed = mutate(store)
        expired = _expire(store, _now(now_iso))
        if changed or expired:
            if choice_id:
                store["applied"] = (store["applied"] + [choice_id])[-APPLIED_CAP:]
            store["history"] = store["history"][-HISTORY_CAP:]
            atomic_write_json(path, store)
            _keep_written_copy(workspace_root, path)
        return result
    finally:
        release_write_lock(lock)


def _change(store, key, start, choice_id, now, edit, moved_from: str = ""):
    rec = find(store, key, start)
    if rec is None and moved_from and norm_start(moved_from) != start:
        # fix check, MUST 2: the press is on the meeting's new time and names
        # the time its edit was made for: the edit moves with it
        old = find(store, key, moved_from)
        if old is not None:
            before_move = copy.deepcopy(old)
            old["start"] = start
            store["history"].append({"choice_id": "", "key": key, "before": before_move,
                                     "after": copy.deepcopy(old), "moved": True})
            rec = old
    before = copy.deepcopy(rec) if rec else None
    fresh = rec is None
    if fresh:
        rec = {"key": key, "start": start, "agenda": None, "goal": None, "adds": [], "removes": [], "at": now}
    result, changed = edit(rec)
    if not changed:
        return result, False
    rec["at"] = now
    if fresh:
        store["meetings"].append(rec)
    store["history"].append({"choice_id": choice_id or "", "key": key, "before": before, "after": copy.deepcopy(rec)})
    return result, True


def _named(workspace_root, texts: List[str]) -> Optional[bool]:
    """Whether the words name someone hidden (`followup_draft.names_hidden`
    over the wall's every way of writing them); None when unread."""
    import followup_draft as fd

    hw = fd.hidden_words(Path(str(workspace_root)))
    if hw is None:
        return None
    return fd.names_hidden(texts, hw)


# ---------------------------------------------------------------- the presses

def set_text(workspace_root, *, field: str, key: Any, start: Any, text: Any, choice_id: Optional[str] = None,
             moved_from: Any = "",
             now_iso: Optional[str] = None) -> Dict[str, Any]:
    """WRITE. The owner's agenda or Goal (`field`): words replace the prep's
    own; empty words clear the edit, so the prep's own show again."""
    k, st = norm_key(key), norm_start(start)
    if not k:
        return {"outcome": "refused", "line": NO_KEY_LINE}
    if not st:
        return {"outcome": "refused", "line": NO_START_LINE}
    if (_start_dt(st) or _now(now_iso)) < _now(now_iso) - _dt.timedelta(days=EXPIRE_DAYS):
        return {"outcome": "refused", "line": PAST_LINE}
    words = clean(text, TEXT_CAP)
    if words:
        named = _named(workspace_root, [words])
        if named is None:
            return {"outcome": "error", "line": WALL_UNREAD_LINE}
        if named:
            return {"outcome": "refused", "line": NAMED_LINE}
    now = _iso(_now(now_iso))

    def edit(rec):
        old = rec.get(field) if isinstance(rec.get(field), dict) else None
        if (old or {}).get("text", "") == words or (not old and not words):
            return {"outcome": "already", "line": ALREADY_LINE}, False
        rec[field] = {"text": words, "at": now, "choice_id": choice_id or ""} if words else None
        return {"outcome": "applied", "line": SAVED_LINE if words else CLEARED_LINE}, True

    return _write(workspace_root, lambda s: _change(s, k, st, choice_id, now, edit, norm_start(moved_from)),
                  choice_id, now_iso)


def add_item(workspace_root, *, key: Any, start: Any, kind: Any, text: Any, choice_id: Optional[str] = None,
             moved_from: Any = "",
             now_iso: Optional[str] = None) -> Dict[str, Any]:
    """WRITE. A topic or a question the owner adds to the call guide."""
    k, st = norm_key(key), norm_start(start)
    if not k:
        return {"outcome": "refused", "line": NO_KEY_LINE}
    if not st:
        return {"outcome": "refused", "line": NO_START_LINE}
    if (_start_dt(st) or _now(now_iso)) < _now(now_iso) - _dt.timedelta(days=EXPIRE_DAYS):
        return {"outcome": "refused", "line": PAST_LINE}
    kd = str(kind or "").strip().lower()
    if kd not in GUIDE_KINDS:
        return {"outcome": "refused", "line": BAD_KIND_LINE}
    words = clean(text, ITEM_CAP)
    if not words:
        return {"outcome": "refused", "line": EMPTY_LINE}
    named = _named(workspace_root, [words])
    if named is None:
        return {"outcome": "error", "line": WALL_UNREAD_LINE}
    if named:
        return {"outcome": "refused", "line": NAMED_LINE}
    gid = guide_id(kd, words)
    now = _iso(_now(now_iso))

    def edit(rec):
        if any(isinstance(x, dict) and x.get("id") == gid for x in rec["adds"]):
            back = [r for r in rec["removes"] if isinstance(r, dict) and r.get("item_id") == gid]
            if not back:
                return {"outcome": "already", "line": ALREADY_LINE}, False
        if len(rec["adds"]) >= ADDS_CAP:
            return {"outcome": "refused", "line": TOO_MANY_LINE}, False
        rec["removes"] = [r for r in rec["removes"] if not (isinstance(r, dict) and r.get("item_id") == gid)]
        if not any(isinstance(x, dict) and x.get("id") == gid for x in rec["adds"]):
            rec["adds"].append({"id": gid, "kind": kd, "text": words, "at": now, "choice_id": choice_id or ""})
        return {"outcome": "applied", "line": ADDED_LINE, "item_id": gid}, True

    return _write(workspace_root, lambda s: _change(s, k, st, choice_id, now, edit, norm_start(moved_from)),
                  choice_id, now_iso)


def remove_item(workspace_root, *, key: Any, start: Any, item_id: Any, choice_id: Optional[str] = None,
             moved_from: Any = "",
                now_iso: Optional[str] = None) -> Dict[str, Any]:
    """WRITE. Take a call-guide item out, the prep's own or an add, by its id."""
    k, st = norm_key(key), norm_start(start)
    if not k:
        return {"outcome": "refused", "line": NO_KEY_LINE}
    if not st:
        return {"outcome": "refused", "line": NO_START_LINE}
    if (_start_dt(st) or _now(now_iso)) < _now(now_iso) - _dt.timedelta(days=EXPIRE_DAYS):
        return {"outcome": "refused", "line": PAST_LINE}
    iid = str(item_id or "").strip()
    if not ITEM_ID_RE.match(iid):
        return {"outcome": "refused", "line": NO_ITEM_LINE}
    now = _iso(_now(now_iso))

    def edit(rec):
        if any(isinstance(r, dict) and r.get("item_id") == iid for r in rec["removes"]):
            return {"outcome": "already", "line": ALREADY_LINE}, False
        if len(rec["removes"]) >= REMOVES_CAP:
            return {"outcome": "refused", "line": TOO_MANY_REMOVES_LINE}, False
        rec["removes"].append({"item_id": iid, "at": now, "choice_id": choice_id or ""})
        return {"outcome": "applied", "line": REMOVED_LINE}, True

    return _write(workspace_root, lambda s: _change(s, k, st, choice_id, now, edit, norm_start(moved_from)),
                  choice_id, now_iso)


def undo(workspace_root, back_of: Any, *, choice_id: Optional[str] = None,
         now_iso: Optional[str] = None) -> Dict[str, Any]:
    """WRITE. Take back exactly the press `back_of`, only while nothing
    changed that meeting since (rulings 43 and 78)."""
    bid = str(back_of or "").strip()
    if not bid:
        return {"outcome": "refused", "line": UNDO_NO_PRESS_LINE}

    def mutate(store):
        rows = [h for h in store["history"] if isinstance(h, dict) and str(h.get("choice_id") or "") == bid]
        if not rows:
            return {"outcome": "refused", "line": UNDO_NOTHING_LINE}, False
        h = rows[-1]
        after = h.get("after") if isinstance(h.get("after"), dict) else {}
        rec = find(store, after.get("key"), after.get("start"))
        strip = lambda d: {x: y for x, y in (d or {}).items() if x != "at"}
        if rec is None or strip(rec) != strip(after):
            return {"outcome": "refused", "line": UNDO_NOTHING_LINE}, False
        idx = store["meetings"].index(rec)
        before = h.get("before")
        if isinstance(before, dict):
            store["meetings"][idx] = copy.deepcopy(before)
        else:
            store["meetings"].pop(idx)
        store["history"].append({"choice_id": choice_id or "", "key": after.get("key") or "", "before": copy.deepcopy(rec),
                                 "after": copy.deepcopy(before) if isinstance(before, dict) else None, "undid": bid})
        return {"outcome": "applied", "line": UNDONE_LINE}, True

    return _write(workspace_root, mutate, choice_id, now_iso)


def apply_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None, *,
                  now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{ok, results}` for the Prep page's presses (`plan_apply`'s `prepedit`
    list), in the order they were pressed, every Undo last."""
    rows = sorted((c for c in choices or [] if isinstance(c, dict)), key=lambda c: str(c.get("created_at") or ""))
    rows = [c for c in rows if c.get("action") != "undo"] + [c for c in rows if c.get("action") == "undo"]
    results = []
    for c in rows:
        verb = str(c.get("action") or "").strip().lower()
        cid = str(c.get("id") or "").strip() or None
        try:
            if verb in ("agenda", "goal"):
                res = set_text(workspace_root, field=verb, key=c.get("meeting_key"), start=c.get("start"),
                               text=c.get("text"), choice_id=cid, moved_from=c.get("moved_from"), now_iso=now_iso)
            elif verb == "add":
                res = add_item(workspace_root, key=c.get("meeting_key"), start=c.get("start"), moved_from=c.get("moved_from"), kind=c.get("guide_kind"), text=c.get("text"),
                               choice_id=cid, now_iso=now_iso)
            elif verb == "remove":
                res = remove_item(workspace_root, key=c.get("meeting_key"), start=c.get("start"), moved_from=c.get("moved_from"), item_id=c.get("item_id"),
                                  choice_id=cid, now_iso=now_iso)
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


def feed(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. `feed/prepx`: the edits of each occurrence in the window (from
    `FEED_PAST_DAYS` back to `FEED_AHEAD_DAYS` ahead), keyed `<key>|<start>`
    (`occurrence`: the row's `jk` and its `start` on `feed/today`),
    `{generated_at, ok, meetings: {occurrence: edit view}, n_held, n_cut}`.
    An edit whose words now name someone hidden is left out whole (counted
    in `n_held`). Past `PREPX_DOC_BUDGET` the occurrences furthest ahead are
    left out first, counted in `n_cut` (kept in the store; listed as their
    day comes nearer). `ok: false` (nothing listed) when the store or the
    wall cannot be read."""
    import followup_draft as fd

    now = _now(now_iso)
    out: Dict[str, Any] = {"generated_at": _iso(now), "ok": False, "meetings": {}, "n_held": 0, "n_cut": 0, "lines": []}
    store, unread = load_store(workspace_root)
    if unread:
        # cr1#1847: once an episode is confirmed, the banner carries the repair's phrase
        offer = repair_health_line(workspace_root)
        out["lines"] = [offer[2:] if offer.startswith("⚠ ") else offer] if offer else [PREPX_UNREAD_LINE]
        return out
    hw = fd.hidden_words(Path(str(workspace_root)))
    if hw is None:
        out["lines"] = [PREPX_WALL_LINE]
        return out
    lo, hi = now - _dt.timedelta(days=FEED_PAST_DAYS), now + _dt.timedelta(days=FEED_AHEAD_DAYS)
    held = 0
    rows: List[Tuple[_dt.datetime, str, Dict[str, Any]]] = []
    for rec in store.get("meetings") or []:
        if not isinstance(rec, dict):
            continue
        occ = occurrence(rec.get("key"), rec.get("start"))
        st = _start_dt(rec.get("start"))
        if not occ or st is None or not (lo <= st <= hi):
            continue
        v = view(rec)
        if fd.names_hidden(_words(v), hw):
            held += 1
            continue
        rows.append((st, occ, v))
    rows.sort(key=lambda r: r[0])
    out.update(ok=True, n_held=held)
    out["meetings"] = {occ: v for _, occ, v in rows}
    while out["meetings"] and len(json.dumps(out, separators=(",", ":"))) > PREPX_DOC_BUDGET:
        rows.pop()
        out["meetings"] = {occ: v for _, occ, v in rows}
        out["n_cut"] += 1
    if out["n_cut"]:
        out["lines"].append(PREPX_CUT_LINE.format(n=out["n_cut"]))
    if held:
        out["lines"].append(PREPX_HELD_LINE.format(n=held))
    return out


def health_line(workspace_root) -> str:
    """READ. The one alarm line for the health check (fix check, required
    item a): "" when the store is missing or reads; else what is wrong and
    what happens next. Never raises."""
    try:
        if not edits_path(workspace_root).exists():
            return ""
        _load_for_write(workspace_root)
        return ""
    except Exception:  # noqa: BLE001 - unreadable, as the writers would find it
        # cr1#1847: once an episode is confirmed, the alarm says how to repair it
        return repair_health_line(workspace_root) or "⚠ " + PREPX_UNREAD_LINE


# ---------------------------------------------------------------- the repair (cr1#1847, decision 147t)
#
# The owner-confirmed repair of an edits file that cannot be read, through the
# shared `file_repair` pattern (this file is its first caller; the off-limits
# list moves onto it as a follow-up once cr1#1831 has passed UAT). Nothing is
# moved automatically: a file that is only mid-sync reads again before an
# episode is confirmed (`file_repair.EPISODE_MIN_FIRES` fires at least
# `EPISODE_MIN_GAP_MINUTES` apart, one fingerprint). Maintenance notes what it
# sees (`watch`); the health check, the Prep page's banner and the phrase
# "repair my prep edits" carry the offer (`repair_offer`), and only the
# owner's yes on that offer, with its token handed back, repairs (`repair`).

REPAIR_GROUP = EDITS_REL.name
REPAIR_LABEL = "prep-edits"
REPAIR_OFFER_LINE = ("Your prep edits file could not be read since {since}. The last readable copy is from "
                     "{when}, with edits on {n} meetings. Say yes to put it back; the file as it is now is kept "
                     "in the archive.")
REPAIR_OFFER_CLEAN_LINE = ("Your prep edits file could not be read since {since}, and there is no earlier "
                           "readable copy. Say yes to start with no prep edits; the file as it is now is kept in "
                           "the archive.")
REPAIR_CONFLICT_LINE = " A sync conflict copy is also there beside it, kept as it is."
REPAIR_DONE_LINE = ("Your prep edits are back as they were on {when}. The file as it was is kept at {archived}. "
                    "Changes made on the Prep page while it could not be read were not saved; make them again.")
REPAIR_DONE_CLEAN_LINE = ("Your prep edits start again with none. The file as it was is kept at {archived}. "
                          "Changes made on the Prep page while it could not be read were not saved; make them again.")
REPAIR_NOT_NEEDED_LINE = "Your prep edits file reads normally, so there is nothing to repair."
REPAIR_NONE_LINE = ("Your prep edits file has not been unreadable long enough to repair: it may be mid-sync. "
                    "It is checked again on each Maintenance run.")
REPAIR_ASK_FIRST_LINE = "Nothing was changed: I need to show you what a repair does first. {offer}"
REPAIR_CHANGED_LINE = ("Nothing was changed: the file or its last readable copy is not what you were shown. "
                       "{offer}")
REPAIR_HEALTH_LINE = ("\u26a0 Your prep edits file could not be read since {since}, so edits on the Prep page are "
                      "off. Say \"repair my prep edits\" to see how to put it back.")
REPAIR_FAILED_LINE = ("Your prep edits file could not be replaced just now, so it was left as it was. A copy of it "
                      "as found is kept at {archived}. Say \"repair my prep edits\" again to try once more.")
REPAIR_ERROR_LINE = ("The repair could not be finished just now. Say \"repair my prep edits\" again to see where "
                     "it stands.")
REPAIR_MANIFEST = ("# Prep edits file set aside\n\n"
                   "The Prep page's edits file (`_hq/data/hub-prep-edits.json`) could not be read, and the owner "
                   "said yes to repairing it on {when}. It was copied here as it was found, then {what}. "
                   "Nothing here was deleted.\n\n"
                   "- `{rel}`: the file as it was found\n{conflicts}")


def _store_reads(store: Any) -> bool:
    try:
        _checked(json.loads(json.dumps(store)))
        return True
    except Exception:  # noqa: BLE001
        return False


def _file_reads(path: Path) -> bool:
    try:
        _checked(json.loads(path.read_text(encoding="utf-8")))
        return True
    except Exception:  # noqa: BLE001
        return False


def _keep_written_copy(workspace_root, path: Path) -> None:
    """The store as just written, read back from disk, into today's slot
    (`file_repair.keep_copy`); nothing when it does not read back clean."""
    try:
        import file_repair

        written = _checked(json.loads(path.read_text(encoding="utf-8")))
        file_repair.keep_copy(Path(str(workspace_root)), REPAIR_GROUP, written)
    except Exception:  # noqa: BLE001 - a copy never stops the owner's write
        pass


def _when_words(workspace_root, value: Any) -> str:
    try:
        from tz import to_local
        d = to_local(value, workspace_path=workspace_root)
    except Exception:  # noqa: BLE001
        d = None
    if d is None:
        return "an earlier day"
    hour = d.hour % 12 or 12
    return f"{d:%A}, {d:%B} {d.day} at {hour}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def watch(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Maintenance, every fire: one look at the edits file
    (`file_repair.note_sighting`). Writes the sighting note only, never the
    store. Never raises."""
    import file_repair

    return file_repair.note_sighting(edits_path(workspace_root), _file_reads, now_iso)


def _offer_line(workspace_root, o: Dict[str, Any]) -> str:
    since = _when_words(workspace_root, o.get("since"))
    if o.get("copy_taken_at"):
        n = len([m for m in (o.get("copy_store") or {}).get("meetings") or [] if isinstance(m, dict)])
        line = REPAIR_OFFER_LINE.format(since=since, when=_when_words(workspace_root, o["copy_taken_at"]), n=n)
    else:
        line = REPAIR_OFFER_CLEAN_LINE.format(since=since)
    return line + (REPAIR_CONFLICT_LINE if o.get("conflicts") else "")


def repair_offer(workspace_root) -> Dict[str, Any]:
    """READ. The offer for "repair my prep edits": `{outcome, line, token}`.
    `not_needed` when the file reads or is gone, `none` while no episode is
    confirmed, `offer` with the date and size of what a yes puts back. Names
    no edit."""
    import file_repair

    path = edits_path(workspace_root)
    if not path.exists() or _file_reads(path):
        return {"outcome": "not_needed", "line": REPAIR_NOT_NEEDED_LINE}
    o = file_repair.offer(Path(str(workspace_root)), path, REPAIR_GROUP, _store_reads)
    if o["outcome"] != "offer":
        return {"outcome": "none", "line": REPAIR_NONE_LINE}
    return {"outcome": "offer", "line": _offer_line(workspace_root, o), "token": o["token"],
            "copy_taken_at": o.get("copy_taken_at")}


def repair(workspace_root, *, token: Optional[str] = None, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. The owner's yes to the offer shown, with its `token`. Under
    the store's write lock: the damaged file into a dated archive folder with
    a MANIFEST, then the last readable copy (or an empty store) over it,
    atomically. Outcomes: `not_needed`, `none`, `ask_first`, `changed` (each
    writes nothing), `repaired`, `error`."""
    import file_repair

    root = Path(str(workspace_root))
    path = edits_path(root)
    now = _now(now_iso)
    try:
        lock = acquire_write_lock(path, holder="hub-prep-edit")
    except TimeoutError:
        return {"outcome": "error", "line": LOCK_BUSY_LINE}
    try:
        def manifest(copy_at, conflicts, state):
            # review at 01cad81e1 (should 2): the MANIFEST says what is true at each step
            new = f"the last readable copy, from {copy_at}," if copy_at else "an empty edits file"
            what = {"pending": f"{new} is to be put in its place",
                    "done": f"{new} was put in its place",
                    "failed": f"{new} was to be put in its place, but the replacement was refused, so the "
                              f"live file was left as it was"}[state]
            extra = "".join(f"- `{c}`: a sync conflict copy beside it, left where it is and not read\n"
                            for c in conflicts)
            return REPAIR_MANIFEST.format(when=_iso(now), what=what, rel="{rel}", conflicts=extra)

        try:
            from tz import to_local
            day = f"{to_local(now, workspace_path=root):%Y-%m-%d}"
        except Exception:  # noqa: BLE001
            day = f"{now:%Y-%m-%d}"
        got = file_repair.confirm(root, path, REPAIR_GROUP, _store_reads, _file_reads, token, empty=_empty,
                                  rel=EDITS_REL, label=REPAIR_LABEL, day=day, manifest=manifest)
    except Exception as exc:  # noqa: BLE001 - review at 01cad81e1 (should 2): an error has its own line
        return {"outcome": "error", "line": REPAIR_ERROR_LINE, "error": type(exc).__name__}
    finally:
        release_write_lock(lock)
    out = got["outcome"]
    if out == "not_needed":
        return {"outcome": out, "line": REPAIR_NOT_NEEDED_LINE}
    if out == "none":
        return {"outcome": out, "line": REPAIR_NONE_LINE}
    if out == "failed":
        return {"outcome": out, "line": REPAIR_FAILED_LINE.format(archived=got["archived_to"]),
                "archived_to": got["archived_to"]}
    if out in ("ask_first", "changed"):
        o = got["offer"]
        if o.get("outcome") != "offer":  # the bytes moved on: no episode to offer now
            return {"outcome": out, "line": REPAIR_CHANGED_LINE.format(offer=REPAIR_NONE_LINE)}
        line = (REPAIR_ASK_FIRST_LINE if out == "ask_first" else REPAIR_CHANGED_LINE).format(
            offer=_offer_line(root, o))
        return {"outcome": out, "line": line, "token": o["token"], "copy_taken_at": o.get("copy_taken_at")}
    done = (REPAIR_DONE_LINE.format(when=_when_words(root, got["copy_taken_at"]), archived=got["archived_to"])
            if got["copy_taken_at"] else REPAIR_DONE_CLEAN_LINE.format(archived=got["archived_to"]))
    return {"outcome": "repaired", "line": done, "archived_to": got["archived_to"],
            "copy_taken_at": got["copy_taken_at"]}


def repair_health_line(workspace_root) -> str:
    """READ. The offer's alarm once an episode is confirmed, "" before that.
    Names no edit. Never raises."""
    try:
        import file_repair

        s = file_repair.episode(edits_path(workspace_root))
        return REPAIR_HEALTH_LINE.format(since=_when_words(workspace_root, s.get("first_seen"))) if s else ""
    except Exception:  # noqa: BLE001
        return ""


__all__ = ["EDITS_REL", "VERBS", "GUIDE_KINDS", "guide_id", "edits_path", "load_store", "find", "edits_for",
           "norm_start", "occurrence", "place", "today_places", "health_line", "EXPIRE_DAYS", "FEED_PAST_DAYS", "FEED_AHEAD_DAYS", "PREPX_DOC_BUDGET",
           "view", "set_text", "add_item", "remove_item", "undo", "apply_choices", "feed", "norm_key",
           "watch", "repair_offer", "repair", "repair_health_line"]
