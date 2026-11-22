#!/usr/bin/env python3
"""Thread, work-stream and priority cards (cr1#1013, train 3 part 1).

A short card per thread, per work stream and per priority, each line
pointing to where it came from. Data side only: nothing here renders the
Hub (later parts of train 3 read these cards).

WHAT A THREAD CARD HOLDS
------------------------
For every live thread (not archived, completed, resolved, retired, closed,
merged or deleted; objective threads are priorities, not thread cards):

  name, kind      from the thread's record;
  company         the company on the thread, or on its parent when it has
                  none (`inherited_from`). `company_id` sits on every card
                  as the per-company filter key (ruling B3), "" when none;
  status          the thread's record status;
  stands          where it stands in three lines: the three newest meetings,
                  mail, notes and decisions on the thread, each line with
                  its source (seq, event id, source ref);
  people          the people from activity on the thread, the owner left
                  out, most involved first, each with the rows that put them
                  there. `key_contact` is the first of them (cr1#981: never
                  the owner; ruling A4: the hand-typed key contact no longer
                  shows);
  last_decision   the newest confirmed decision on the thread, with source;
  last_moved      the newest day something moved on the thread, by the
                  day-count rule every bookend uses
                  (`thread_activity.BOOKEND_ACTIVITY_TYPES`, reclassifications
                  folded), with the row that moved it, and the distinct
                  days it moved (newest first, at most `ACTIVE_DAYS_KEPT`).

Read live, never stored (they depend on the clock): the open rows both ways
(`open_list_index`, the same rows the open list counts) and the next step
(`pick_next`: the nearest dated row due today or later; else the latest
row past its date, marked overdue; else the oldest undated row, the
owner's own first).

WORK STREAMS AND PRIORITIES (ruling A3)
---------------------------------------
A work stream is a parent thread: any live thread that some live thread
names as its `parent_thread_id`. Its card is built from the thread cards
of its children and itself and says so (`built_from`, `says`).

A priority is today's objective record (`objective_state`). Ruling B2:
priorities are suggested by the system and then confirmed, or set up by
hand. A confirmed priority is an open objective. A suggested one
(`suggested_priorities`) is a live thread that moved on at least
`SUGGEST_MIN_DAYS` of the last `SUGGEST_WINDOW_DAYS` days and is not
already tied to an open objective; it waits for the owner's confirm, which
is the objectives skill's own `confirm objective`, and is never counted as
a priority until then. A priority card is built from the thread cards of
the threads it is tied to and says so. An undated priority never shows
overdue; one with a date shows overdue only when the date is past. After
`QUIET_DAYS` days without movement the card asks whether it is still a
priority.

A priority card's `last_moved` (cr1#1459) is the newest action on it or on
any thread it is tied to: a thread moving by the day-count rule, or a
commitment there the owner closed as done (`is_customer_act`). Only the
priority card counts that close; thread and work-stream cards keep the
day-count rule alone. It names the day, the kind of row, its source, the
thread it happened on and, for a close, the commitment's title.

Rulings, owner, 2026-11-10:
  A3  a work stream is a parent thread; a priority is today's objective.
  A4  a field shows only when a named job fills it: a section nothing fills
      is absent, never an empty heading, and each section names its `job`.
  B2  priorities are suggested and then confirmed, or set up by hand.
  B3  one folder per seat; the per-business filter is the company on each
      thread.
  C3  cards are for display; recall goes back to the source.

THE FILL-RATE CHECK
-------------------
`fill_check` reports, for every displayed field of the thread card, the
share of live threads it is filled on, beside its set rate (`FILL_RATES`),
and names every field below its rate. The rebuild carries the names of the
fields below their rate in its return, so the Maintenance run reports them.

NEXT STEP PER PERSON (ruling A9, from #110)
-------------------------------------------
`next_step_for_person` picks a person's next step from their open rows both
ways by the same `pick_next` rule, so person cards can read it later. It is
not stored on the person card.

WHERE IT LIVES
--------------
`_hq/data/now/threads/cards.json`, written whole by `open_list.rebuild` (the
Maintenance run) beside the open list and the person cards, with the same
build stamps. A reader that finds the file stale works the cards out from
the log in memory, says so (`counted_from_log`), and never writes. No money
on any card (owner ruling, 2026-11-09). The file is for the owner's own
surfaces (`audience: "owner"`); its events are read through the org-scoped
reader, so personal rows never reach a card.

Read-only except `write_cards`. stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_json  # noqa: E402

CARDS_DIR_REL = "_hq/data/now/threads"
CARDS_FILE = "cards.json"
CARDS_VERSION = 2  # 2: a priority's last_moved names what moved it (cr1#1459)

#: Thread statuses that have no card (the Hub's Projects tab's own list).
DONE_STATUSES = ("archived", "completed", "resolved", "retired", "closed",
                 "merged", "deleted")
#: The row types that say where a thread stands, newest first.
STANDS_TYPES = frozenset({"meeting", "meeting_processed", "interaction",
                          "email_sent", "email_received", "note", "memo",
                          "insight", "decision", "session_chapter"})
STANDS_LINES = 3
#: The text fields a stands line is read from, in order.
_TEXT_FIELDS = ("summary", "title", "subject", "decision", "note", "text")
PEOPLE_SHOWN = 5
#: Distinct days a thread moved, kept on the card for the read-time
#: suggestions (newest first).
ACTIVE_DAYS_KEPT = 30
#: A priority that has not moved for this many days asks whether it is
#: still a priority.
QUIET_DAYS = 14
#: A thread is suggested as a priority when it moved on at least
#: SUGGEST_MIN_DAYS of the last SUGGEST_WINDOW_DAYS days; at most
#: SUGGEST_MAX are suggested.
SUGGEST_WINDOW_DAYS = 14
SUGGEST_MIN_DAYS = 2
SUGGEST_MAX = 3
#: The mute target a skipped suggestion is snoozed under (a standard
#: `chat_dismissal` whose `target_id` is the suggestion's id).
SUGGESTION_PREFIX = "priority_suggestion:"
#: The objective binding a suggestion's confirm carries (one of
#: `objective_state.OBJECTIVE_BINDING_TYPES`; not an event type).
ACTIVITY_BINDING = "activity"
QUIET_ASK = "Is this still a priority?"

#: The jobs that fill each section (A4), named on the card.
JOB_RECORD = "the thread's record"
JOB_COMPANY = "the company on the thread's record"
JOB_STANDS = "the thread's newest meetings, mail, notes and decisions in the log"
JOB_PEOPLE = "the people on the thread's rows in the log"
JOB_DECISION = "decisions captured from meetings, mail and chat"
JOB_MOVED = "the thread's rows in the log (the day-count rule)"
JOB_OPEN = "the open list"
JOB_NEXT = "the open list (nearest dated row first)"
JOB_CHILDREN = "the thread cards of the work stream's threads"
JOB_OBJECTIVE = "the objectives skill (the owner's confirmed objective)"
JOB_SUGGEST = "the thread's movement in the log, waiting for the owner's confirm"

#: The set fill rate of each displayed thread-card field: the share of live
#: threads it must be filled on. A default until the owner sets them.
FILL_RATES = {
    "status": 0.95,
    "last_moved": 0.90,
    "company": 0.80,
    "stands": 0.60,
    "people": 0.60,
    "next_step": 0.50,
    "open": 0.50,
    "last_decision": 0.20,
}


def _dir(ws) -> Path:
    return Path(ws) / CARDS_DIR_REL


def _thread_name(t: dict) -> str:
    return str(t.get("display_name") or t.get("canonical_name")
               or t.get("name") or t.get("id") or "").strip()[:160]


def _one_line(text: Any, cap: int = 200) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:cap]


def _source(ev: dict) -> dict:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    out = {"seq": ev.get("seq"), "event_id": str(ev.get("id") or d.get("id") or "")}
    ref = d.get("source_ref") or ev.get("source_ref")
    if ref:
        out["source_ref"] = str(ref)
    return out


def _live(t: dict) -> bool:
    return (isinstance(t, dict) and bool(t.get("id"))
            and str(t.get("status") or "active").lower() not in DONE_STATUSES)


# ---------------------------------------------------------------------------
# Reading the book
# ---------------------------------------------------------------------------

def _book(ws):
    """(threads, people names, org names, owner id)."""
    from entities_io import entities_collection, read_entities_json
    from people_view import _name, _owner_id
    root = Path(ws)
    try:
        ent = read_entities_json(root / "_hq" / "data" / "entities.json")
    except (FileNotFoundError, ValueError, OSError):
        ent = {}
    if not isinstance(ent, dict):
        ent = {}
    threads = [t for t in (entities_collection(ent, "threads") if ent else [])
               if isinstance(t, dict) and t.get("id")]
    people = [p for p in (entities_collection(ent, "people") if ent else [])
              if isinstance(p, dict) and p.get("id")]
    orgs = [o for o in (entities_collection(ent, "orgs") if ent else [])
            if isinstance(o, dict) and o.get("id")]
    names = {p["id"]: _name(p) for p in people if _name(p)
             and str(p.get("status") or "").lower() not in ("merged", "deleted")}
    org_names = {o["id"]: _name(o) for o in orgs if _name(o)}
    return threads, names, org_names, _owner_id(root, people)


def _events(ws) -> list:
    """The org-scoped rows, reclassifications folded (RECL1), so a row the
    owner moved to another thread counts there."""
    from events_io import load_events_org_scoped
    from thread_activity import apply_reclassifications
    root = Path(ws)
    if not (root / "_hq" / "data" / "events.jsonl").exists():
        return []
    scoped, _skipped = load_events_org_scoped(root)
    return apply_reclassifications([e for e in scoped if isinstance(e, dict)])


def _above_floor(ev: dict) -> bool:
    """The classification floor every thread read honours
    (`thread_activity.CONFIDENCE_FLOOR`)."""
    from thread_activity import CONFIDENCE_FLOOR
    conf = ev.get("classification_confidence")
    return not (isinstance(conf, (int, float)) and not isinstance(conf, bool)
                and conf < CONFIDENCE_FLOOR)


#: cr1#1459: rows a priority writes about itself that are not action on it.
#: Setting a priority up (its `thread_created` and `objective_created` rows;
#: the second also lands on its anchor thread) and rewording it
#: (`objective_updated`) never read as its last action, so a new priority
#: says "No action yet".
PRIORITY_SKIP_TYPES = frozenset({"thread_created", "objective_created", "objective_updated"})


def _owner_closed(ev: dict) -> bool:
    """cr1#1459: a close the owner made, never the system's own (a drain,
    an age-out, a rail): `event_types.is_customer_act`."""
    from event_types import is_customer_act
    return is_customer_act(ev)


def _counts_as_moved(ev: dict) -> bool:
    """The day-count rule: the bookend type set, so the system's own
    bookkeeping about a promise never reads as the thread moving."""
    from thread_activity import BOOKEND_ACTIVITY_TYPES
    return ev.get("type") not in BOOKEND_ACTIVITY_TYPES.excluded


def _local_day(ev: dict, tzinfo) -> str:
    from thread_activity import _event_dt
    at = _event_dt(ev)
    if at is None:
        return ""
    return (at.astimezone(tzinfo) if tzinfo else at).date().isoformat()


def _text_of(ev: dict) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if ev.get("type") == "decision":
        from decision_match import _decision_field
        return _one_line(_decision_field(ev, "title"))
    for k in _TEXT_FIELDS:
        if d.get(k):
            return _one_line(d[k])
    return ""


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _fold(events: list, owner: str, tzinfo) -> dict:
    """{thread id: {"stands": [...], "people": {pid: {...}}, "decision": ev,
    "moved": (day, ev), "pmoved": (day, ev), "closed": (day, ev), "days": set}}
    from one pass over the rows. Read by priority cards only (cr1#1459):
    `pmoved` is `moved` without a priority's own bookkeeping
    (`PRIORITY_SKIP_TYPES`), and `closed` the newest commitment on the
    thread the owner closed as done and did not open again."""
    from closure_index import closer_target_id
    from event_refs import persons_of, threads_of
    from commitment_state import without_retracted_notes
    from hub_edit import undo_seqs
    out: Dict[str, dict] = {}
    # cr1#1383 (review of #1611): a commitment note taken back, from the Hub
    # or from chat (cr1#1463), is not on the thread: its row leaves the
    # stands and the activity.
    events = without_retracted_notes(events)
    # cr1#1383: the writer rows a Hub undo made are not the thread moving
    # (an undo is not a touch).
    undone = undo_seqs(events)
    # cr1#1459: a close the owner took back (an Undo of Done, or a reopen)
    # writes a `commitment_reopened` after it; that close no longer counts.
    reopened: Dict[str, int] = {}
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "commitment_reopened" and isinstance(ev.get("seq"), int):
            cid = closer_target_id(ev)
            if cid:
                reopened[cid] = max(reopened.get(cid, -1), ev["seq"])
    for ev in events:
        if not _above_floor(ev):
            continue
        tids = threads_of(ev)
        if not tids:
            continue
        day = _local_day(ev, tzinfo)
        if not day:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        etype = ev.get("type")
        text = _text_of(ev) if etype in STANDS_TYPES else ""
        is_decision = (etype == "decision" and not d.get("pending_review")
                       and bool(text))
        people = persons_of(ev) - ({owner} if owner else set())
        moved = _counts_as_moved(ev) and ev.get("seq") not in undone
        # A close with no resolution reads as done, as every close reader does.
        closed = (etype == "commitment_resolved" and str(d.get("resolution") or "done") == "done"
                  and ev.get("seq") not in undone and _owner_closed(ev)
                  and not (isinstance(ev.get("seq"), int)
                           and reopened.get(closer_target_id(ev), -1) > ev["seq"]))
        pmoved = moved and etype not in PRIORITY_SKIP_TYPES
        for tid in tids:
            f = out.setdefault(tid, {"stands": [], "people": {},
                                     "decision": None, "moved": None,
                                     "pmoved": None, "closed": None, "days": set()})
            key = (day, ev.get("seq") if isinstance(ev.get("seq"), int) else -1)
            if moved:
                f["days"].add(day)
                if f["moved"] is None or key >= f["moved"][0]:
                    f["moved"] = (key, ev)
            if pmoved and (f.get("pmoved") is None or key >= f["pmoved"][0]):
                f["pmoved"] = (key, ev)
            if closed and (f.get("closed") is None or key >= f["closed"][0]):
                f["closed"] = (key, ev)
            if text:
                f["stands"].append((key, text, ev))
            if is_decision and (f["decision"] is None or key >= f["decision"][0]):
                f["decision"] = (key, ev)
            for pid in people:
                p = f["people"].setdefault(pid, {"n": 0, "last": "", "refs": []})
                p["n"] += 1
                p["last"] = max(p["last"], day)
                p["refs"].append((key, _source(ev)))
    return out


def _thread_card(t: dict, f: Optional[dict], by_id: dict, names: dict,
                 org_names: dict) -> dict:
    tid = str(t["id"])
    card: Dict[str, Any] = {"id": tid, "name": _thread_name(t)}
    if t.get("kind"):
        card["kind"] = str(t["kind"])
    # No stored status reads "active", the Hub's Projects tab's own default.
    status = str(t.get("status") or "active").strip().lower()
    card["status"] = {"value": status, "job": JOB_RECORD}
    # The company (B3): the thread's own, else its parent's.
    from thread_writer import UNAFFILIATED_ORG_ID

    def org_of(rec: dict) -> str:
        oid = str(rec.get("org_id") or rec.get("affiliation_id") or "")
        return "" if oid == UNAFFILIATED_ORG_ID else oid

    oid, inherited = org_of(t), ""
    parent = by_id.get(str(t.get("parent_thread_id") or ""))
    if not oid and parent:
        oid, inherited = org_of(parent), str(parent["id"])
    card["company_id"] = oid
    if oid:
        company = {"org_id": oid, "name": org_names.get(oid, ""), "job": JOB_COMPANY}
        if inherited:
            company["inherited_from"] = inherited
        card["company"] = company
    if parent:
        card["parent"] = {"id": str(parent["id"]), "name": _thread_name(parent),
                          "job": JOB_RECORD}
    f = f or {}
    lines, seen = [], set()
    for key, text, ev in sorted(f.get("stands") or [], key=lambda x: x[0],
                                reverse=True):
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        lines.append({"text": text, "day": key[0], "type": ev.get("type"),
                      "source": _source(ev)})
        if len(lines) == STANDS_LINES:
            break
    if lines:
        card["stands"] = {"lines": lines, "job": JOB_STANDS}
    rows = []
    for pid, p in (f.get("people") or {}).items():
        if pid not in names:
            continue
        refs = [r for _k, r in sorted(p["refs"], key=lambda x: x[0], reverse=True)[:3]]
        rows.append({"person_id": pid, "name": names[pid], "rows": p["n"],
                     "last_day": p["last"], "sources": refs})
    rows.sort(key=lambda r: (-r["rows"], _neg_day(r["last_day"]), r["person_id"]))
    rows = rows[:PEOPLE_SHOWN]
    if rows:
        card["people"] = {"rows": rows, "key_contact": {
            "person_id": rows[0]["person_id"], "name": rows[0]["name"]},
            "job": JOB_PEOPLE}
    if f.get("decision"):
        key, ev = f["decision"]
        card["last_decision"] = {"text": _text_of(ev), "day": key[0],
                                 "source": _source(ev), "job": JOB_DECISION}
    if f.get("moved"):
        key, ev = f["moved"]
        card["last_moved"] = {
            "day": key[0], "type": ev.get("type"), "source": _source(ev),
            "active_days": sorted(f["days"], reverse=True)[:ACTIVE_DAYS_KEPT],
            "job": JOB_MOVED}
    return card


def _neg_day(day: str) -> str:
    """A key that sorts newer days first inside an ascending sort."""
    return "".join(chr(0x7F - ord(c)) if c.isdigit() else c for c in day)


def _priority_card(row: dict, thread_cards: dict, moved_of: dict,
                   thread_names: Optional[dict] = None) -> dict:
    """`moved_of`: {thread id: ((day, seq), event)}, the newest action on each
    thread (movement or the owner's close). `thread_names` names a tied
    thread that has no card (archived or done), so the line never reads as
    if the action happened on the priority itself."""
    from objective_math import _linked_ids
    obj = row.get("objective") if isinstance(row.get("objective"), dict) else {}
    tid = str(row.get("thread_id") or "")
    card: Dict[str, Any] = {
        "id": tid, "state": "confirmed",
        "statement": _one_line(obj.get("statement") or row.get("name") or tid, 240),
        "job": JOB_OBJECTIVE}
    horizon = str(obj.get("horizon") or "").strip()
    due = _as_date(horizon)
    if due:
        card["due"] = due.isoformat()
    elif horizon:
        card["horizon"] = _one_line(horizon, 80)
    if obj.get("opened_at"):
        card["opened"] = str(obj["opened_at"])[:10]
    if row.get("malformed"):
        card["malformed"] = True
    linked = [i for i in (_linked_ids(obj) if obj else []) if i]
    built = [i for i in linked if i in thread_cards]
    card["built_from"] = built
    card["says"] = _says(len(built))
    best = None
    for i in [tid] + linked:
        m = moved_of.get(i)
        if m and (best is None or m[0] > best[1][0]):
            best = (i, m)
    if best:
        i, (key, ev) = best
        mv: Dict[str, Any] = {"day": key[0], "type": ev.get("type"), "source": _source(ev),
                              "thread_id": i, "job": JOB_MOVED}
        name = (thread_cards.get(i) or {}).get("name") or (thread_names or {}).get(i)
        if i != tid and name:
            mv["thread_name"] = name
        if ev.get("type") == "commitment_resolved":
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            title = _one_line(d.get("title"), 160)
            if title:
                mv["title"] = title
        card["last_moved"] = mv
    return card


def _says(n: int) -> str:
    if n == 0:
        return "Built from no thread cards: this priority is tied to no live thread yet."
    return f"Built from {n} thread card{'s' if n != 1 else ''}."


def _as_date(value) -> Optional[date]:
    s = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]) or len(s) > 10:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def build_cards(ws) -> dict:
    """{"threads": {...}, "work_streams": {...}, "priorities": {...}}.
    Writes nothing."""
    import open_list as ol
    from objective_state import list_open_objectives
    root = Path(ws)
    threads, names, org_names, owner = _book(root)
    by_id = {str(t["id"]): t for t in threads}
    folded = _fold(_events(root), owner, ol._workspace_tz(root))
    cards: Dict[str, dict] = {}
    for t in threads:
        if not _live(t) or t.get("kind") == "objective":
            continue
        cards[str(t["id"])] = _thread_card(t, folded.get(str(t["id"])), by_id,
                                           names, org_names)
    streams: Dict[str, dict] = {}
    for tid, c in cards.items():
        pid = (c.get("parent") or {}).get("id")
        if pid and pid in cards:
            streams.setdefault(pid, []).append(tid)
    ws_cards = {}
    for pid, kids in streams.items():
        parent = cards[pid]
        built = [pid] + sorted(kids)
        kid_rows = [{"id": k, "name": cards[k]["name"],
                     "status": (cards[k].get("status") or {}).get("value", ""),
                     "last_moved": (cards[k].get("last_moved") or {}).get("day", "")}
                    for k in sorted(kids)]
        card = {"id": pid, "name": parent["name"],
                "company_id": parent.get("company_id", ""),
                "threads": {"rows": kid_rows, "job": JOB_CHILDREN},
                "built_from": built, "says": _says(len(built))}
        for k in ("company", "status"):
            if parent.get(k):
                card[k] = parent[k]
        days = [(cards[i].get("last_moved") or {}).get("day") for i in built]
        days = [d for d in days if d]
        if days:
            card["last_moved"] = {"day": max(days), "job": JOB_CHILDREN}
        ws_cards[pid] = card
    moved_of = {}
    for tid, f in folded.items():
        got = [x for x in (f.get("pmoved"), f.get("closed")) if x]
        if got:
            moved_of[tid] = max(got, key=lambda x: x[0])
    prios = {}
    for row in list_open_objectives(root):
        if row.get("thread_id"):
            prios[str(row["thread_id"])] = _priority_card(
                row, cards, moved_of, {k: _thread_name(t) for k, t in by_id.items()})
    return {"threads": cards, "work_streams": ws_cards, "priorities": prios}


def _stamps(doc: dict) -> dict:
    return {"version": CARDS_VERSION, "audience": "owner",
            "built_at": doc.get("built_at"),
            "log_lines": doc.get("log_lines"), "log_sha256": doc.get("log_sha256"),
            "entities_sha256": doc.get("entities_sha256")}


def write_cards(ws, doc: dict) -> dict:
    """Write every card for an open list document just built, then run the
    fill-rate check. Called only by `open_list.rebuild`."""
    built = build_cards(ws)
    atomic_write_json(_dir(ws) / CARDS_FILE, {**_stamps(doc), **built})
    out = {"thread_cards": len(built["threads"]),
           "work_stream_cards": len(built["work_streams"]),
           "priority_cards": len(built["priorities"])}
    try:
        out["thread_fill_below"] = fill_check(ws, cards=built)["below"]
    except Exception as exc:  # noqa: BLE001: the check never fails the write
        out["thread_fill_error"] = f"{type(exc).__name__}: {exc}"
    return out


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _current(ws, doc) -> bool:
    import open_list
    return (isinstance(doc, dict) and doc.get("version") == CARDS_VERSION
            and all(isinstance(doc.get(k), dict)
                    for k in ("threads", "work_streams", "priorities"))
            and open_list.stamps_current(ws, doc))


def read_cards(ws) -> tuple:
    """(cards, counted_from_log). A stale or missing file is worked out from
    the log in memory. Never writes."""
    try:
        doc = json.loads((_dir(ws) / CARDS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if _current(ws, doc):
        return {k: doc[k] for k in ("threads", "work_streams", "priorities")}, False
    return build_cards(ws), True


def _open_by_thread(ws, now_iso=None, *, keep_ids=None):
    """({thread id: {you_owe, owed_to_you, unowned, to_confirm}}, today,
    now, counted_from_log): the open list's default view per thread, read
    once. `keep_ids` (cr1#1292, the Week board): snoozed rows to keep."""
    keep = {str(k) for k in (keep_ids or ())}
    import open_list as ol
    import open_list_index as oli
    ws = Path(ws)
    tzinfo = ol._workspace_tz(ws)
    now, today = ol._clock(ws, now_iso, tzinfo)
    threads, list_doc, from_log = oli._read(ws, "threads", oli.BY_THREAD_FILE,
                                            now, tzinfo)
    by_id = {str(r.get("id")): r for r in list_doc.get("rows") or []}
    out = {}
    for tid, e in threads.items():
        v = {"to_confirm": []}
        for key in ("you_owe", "owed_to_you", "unowned"):
            shaped = [r for r in oli._shape(by_id, e.get(key) or [], today, now)
                      if oli._visible(r)
                      or (str(r.get("id")) in keep and not r.get("observed"))]
            v[key] = [r for r in shaped if r.get("confirmed")]
            v["to_confirm"] += [r for r in shaped if not r.get("confirmed")]
        out[str(tid)] = v
    return out, today, now, from_log


def _row_line(r: dict, direction: str, names: Optional[dict] = None) -> dict:
    out = {"id": r.get("id"), "title": r.get("title"), "direction": direction,
           "due": r.get("due") or "", "overdue": bool(r.get("overdue")),
           "source": dict(r.get("source") or {})}
    if names is not None:
        who = (str(r.get("owner_id") or "") if direction == "owed_to_you"
               else next((str(c) for c in r.get("counterparty_ids") or [] if c), ""))
        if who and who in names:
            out["with"] = {"person_id": who, "name": names[who]}
    return out


def pick_next(you_owe: list, owed_to_you: list, today: date) -> Optional[dict]:
    """The next step from open rows both ways (cr1#981: an upcoming row
    before one long past its date). The nearest row due today or later,
    the owner's own first on the same day; else the most recent row past
    its date, marked overdue; else the oldest undated row, the owner's own
    first. None when nothing is open."""
    rows = [(r, "you_owe") for r in you_owe] + [(r, "owed_to_you") for r in owed_to_you]
    rank = {"you_owe": 0, "owed_to_you": 1}
    ahead = [(r, d) for r, d in rows if _as_date(r.get("due")) and _as_date(r["due"]) >= today]
    if ahead:
        r, d = min(ahead, key=lambda x: (x[0]["due"], rank[x[1]], str(x[0].get("id"))))
        return {**_row_line(r, d), "overdue": False}
    past = [(r, d) for r, d in rows if _as_date(r.get("due"))]
    if past:
        r, d = max(past, key=lambda x: (x[0]["due"], -rank[x[1]], str(x[0].get("id"))))
        return {**_row_line(r, d), "overdue": bool(r.get("overdue"))}
    if rows:
        r, d = min(rows, key=lambda x: (rank[x[1]], x[0].get("opened") or "",
                                        str(x[0].get("id"))))
        return {**_row_line(r, d), "overdue": False}
    return None


def _open_section(v: dict, names: dict, full: dict) -> Optional[dict]:
    if not v or not any(v.get(k) for k in ("you_owe", "owed_to_you")):
        return None
    return {"you_owe": [_row_line(r, "you_owe", names) for r in v["you_owe"][:3]],
            "owed_to_you": [_row_line(r, "owed_to_you", names) for r in v["owed_to_you"][:3]],
            "n_you_owe": len(v["you_owe"]), "n_owed_to_you": len(v["owed_to_you"]),
            "n_to_confirm": len(v.get("to_confirm") or []),
            "full_list": full, "job": JOB_OPEN}


def _with_live(card: dict, v: dict, today: date, names: dict) -> dict:
    out = dict(card)
    sec = _open_section(v, names, {"command": "thread", "thread_id": card["id"]})
    if sec:
        out["open"] = sec
    nxt = pick_next((v or {}).get("you_owe") or [], (v or {}).get("owed_to_you") or [], today)
    if nxt:
        out["next_step"] = {**nxt, "job": JOB_NEXT}
    return out


def thread_card_view(ws, thread_id: str, now_iso=None) -> dict:
    """One thread's card with the open rows both ways and the next step,
    read live. Writes nothing."""
    cards, from_log = read_cards(ws)
    card = cards["threads"].get(str(thread_id or ""))
    if card is None:
        return {"status": "not_found", "thread_id": str(thread_id or "")}
    opens, today, _now, list_from_log = _open_by_thread(ws, now_iso)
    _t, names, _o, _owner = _book(ws)
    return {"status": "found", "today": today.isoformat(),
            "counted_from_log": from_log or list_from_log,
            "card": _with_live(card, opens.get(card["id"]), today, names)}


def work_stream_view(ws, thread_id: str, now_iso=None) -> dict:
    """One work stream's card: its threads, their open rows summed and the
    next step across them, read live. Writes nothing."""
    cards, from_log = read_cards(ws)
    card = cards["work_streams"].get(str(thread_id or ""))
    if card is None:
        return {"status": "not_found", "thread_id": str(thread_id or "")}
    opens, today, _now, list_from_log = _open_by_thread(ws, now_iso)
    _t, names, _o, _owner = _book(ws)
    merged = {"you_owe": [], "owed_to_you": [], "to_confirm": []}
    for tid in card["built_from"]:
        for k in merged:
            merged[k] += (opens.get(tid) or {}).get(k) or []
    for k in merged:
        merged[k].sort(key=lambda r: (r.get("due") or "9999-99-99", str(r.get("id"))))
    out = dict(card)
    sec = _open_section(merged, names, {"command": "thread", "thread_ids": card["built_from"]})
    if sec:
        out["open"] = {**sec, "job": JOB_CHILDREN}
    nxt = pick_next(merged["you_owe"], merged["owed_to_you"], today)
    if nxt:
        out["next_step"] = {**nxt, "job": JOB_CHILDREN}
    return {"status": "found", "today": today.isoformat(),
            "counted_from_log": from_log or list_from_log, "card": out}


def _quiet_ask(card: dict, today: date) -> Optional[dict]:
    since = _as_date((card.get("last_moved") or {}).get("day")) or _as_date(card.get("opened"))
    if since is None:
        return None
    quiet = (today - since).days
    if quiet < QUIET_DAYS:
        return None
    return {"ask": QUIET_ASK, "quiet_days": quiet, "since": since.isoformat(),
            "job": JOB_MOVED}


def suggested_priorities(ws, now_iso=None, *, cards: Optional[dict] = None) -> list:
    """The threads the system suggests as priorities (B2), most active
    first, at most `SUGGEST_MAX`. Each waits for the owner's confirm and is
    never a priority until then. Writes nothing."""
    import open_list as ol
    from events_io import load_events_owner_scoped
    from mute_ledger import live_mutes
    from objective_math import _linked_ids
    root = Path(ws)
    if cards is None:
        cards, _from_log = read_cards(root)
    now, today = ol._clock(root, now_iso, ol._workspace_tz(root))
    tied = set()
    for c in (cards.get("priorities") or {}).values():
        tied.update(c.get("built_from") or [])
    from objective_state import list_open_objectives
    for row in list_open_objectives(root):
        obj = row.get("objective") if isinstance(row.get("objective"), dict) else {}
        tied.update(_linked_ids(obj) if obj else [])
    muted = set()
    if (root / "_hq" / "data" / "events.jsonl").exists():
        events = load_events_owner_scoped(str(root))[0]
        muted = {str(m.get("target_id")) for m in live_mutes(events, now.isoformat())}
    floor = today - timedelta(days=SUGGEST_WINDOW_DAYS)
    picks = []
    for tid, c in (cards.get("threads") or {}).items():
        if tid in tied or (SUGGESTION_PREFIX + tid) in muted:
            continue
        days = [d for d in (c.get("last_moved") or {}).get("active_days") or []
                if (_as_date(d) or date.min) > floor and (_as_date(d) or date.max) <= today]
        if len(days) < SUGGEST_MIN_DAYS:
            continue
        picks.append((len(days), max(days), tid, c))
    picks.sort(key=lambda x: (-x[0], _neg_day(x[1]), x[2]))
    out = []
    for n, last, tid, c in picks[:SUGGEST_MAX]:
        out.append({
            "id": SUGGESTION_PREFIX + tid, "state": "suggested",
            "statement": c["name"], "company_id": c.get("company_id", ""),
            "why": f"moved on {n} of the last {SUGGEST_WINDOW_DAYS} days",
            "last_moved": {"day": last, "job": JOB_MOVED},
            "built_from": [tid], "says": _says(1),
            "waits_for": "the owner's confirm",
            "confirm": {"verb": "confirm objective", "anchor_thread_id": tid,
                        "binding": {"type": ACTIVITY_BINDING, "entity_ids": [tid]}},
            "skip": {"verb": "skip", "target_id": SUGGESTION_PREFIX + tid},
            "job": JOB_SUGGEST})
    return out


def priorities_view(ws, now_iso=None) -> dict:
    """The confirmed priorities (each with `overdue` and the still-a-priority
    question, read live) and the suggested ones, apart. Writes nothing."""
    import open_list as ol
    root = Path(ws)
    cards, from_log = read_cards(root)
    _now, today = ol._clock(root, now_iso, ol._workspace_tz(root))
    confirmed = []
    for c in cards["priorities"].values():
        row = dict(c)
        due = _as_date(c.get("due"))
        # An undated priority never shows overdue (B2).
        row["overdue"] = bool(due and due < today)
        ask = _quiet_ask(c, today)
        if ask:
            row["still_a_priority"] = ask
        confirmed.append(row)
    confirmed.sort(key=lambda r: (not r["overdue"], r.get("due") or "9999-99-99",
                                  r.get("statement") or ""))
    return {"today": today.isoformat(), "counted_from_log": from_log,
            "confirmed": confirmed,
            "suggested": suggested_priorities(root, now_iso, cards=cards)}


def company_filter(ws, company_id: str) -> dict:
    """The thread and work-stream cards of one company (B3), by the filter
    key on each card. Writes nothing."""
    cards, from_log = read_cards(ws)
    key = str(company_id or "")
    return {"company_id": key, "counted_from_log": from_log,
            "threads": sorted(t for t, c in cards["threads"].items()
                              if c.get("company_id") == key),
            "work_streams": sorted(t for t, c in cards["work_streams"].items()
                                   if c.get("company_id") == key)}


def next_step_for_person(ws, who: str, now_iso=None) -> dict:
    """A person's next step from their open rows both ways, by `pick_next`
    (ruling A9, from #110). For person cards to read; not stored on them.
    Writes nothing."""
    import open_list_index as oli
    live = oli.person_view(ws, who, now_iso)
    if live.get("status") != "found":
        return {k: v for k, v in live.items()
                if k in ("status", "query", "candidates", "today")}
    today = date.fromisoformat(live["today"])
    nxt = pick_next(live.get("you_owe") or [], live.get("owes_you") or [], today)
    out = {"status": "found", "person": live["person"], "today": live["today"]}
    if nxt:
        out["next_step"] = {**nxt, "job": JOB_NEXT}
    return out


# ---------------------------------------------------------------------------
# The fill-rate check
# ---------------------------------------------------------------------------

def fill_check(ws, now_iso=None, *, cards: Optional[dict] = None) -> dict:
    """For each displayed thread-card field: how many live thread cards it
    is filled on, the share, the set rate and whether it is below it.
    `below` names every field under its rate. Writes nothing."""
    if cards is None:
        cards, _from_log = read_cards(ws)
    threads = cards.get("threads") or {}
    opens, today, _now, _fl = _open_by_thread(ws, now_iso)
    _t, names, _o, _owner = _book(ws)
    filled = {f: 0 for f in FILL_RATES}
    for tid, c in threads.items():
        view = _with_live(c, opens.get(tid), today, names)
        for f in FILL_RATES:
            if view.get(f):
                filled[f] += 1
    n = len(threads)
    fields = {}
    for f, rate in FILL_RATES.items():
        share = round(filled[f] / n, 3) if n else None
        fields[f] = {"filled": filled[f], "of": n, "rate": share,
                     "set_rate": rate,
                     "below": share is not None and share < rate}
    return {"today": today.isoformat(), "threads": n, "fields": fields,
            "below": [f for f, v in fields.items() if v["below"]]}


__all__ = ["CARDS_DIR_REL", "CARDS_FILE", "FILL_RATES", "QUIET_DAYS",
           "build_cards", "write_cards", "read_cards", "pick_next",
           "thread_card_view", "work_stream_view", "priorities_view",
           "suggested_priorities", "company_filter", "next_step_for_person",
           "fill_check"]
