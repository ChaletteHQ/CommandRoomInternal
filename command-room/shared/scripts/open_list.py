#!/usr/bin/env python3
"""The open list: every open promise in one file, rebuilt from the log by
Maintenance, and the one count every surface reads (cr1#1012, train 2 of the
memory and Hub programme; closes cr1#976 when the one-count check passes).

WHY THIS EXISTS
---------------
Before this module four readers decided differently which rows were open and
which were late, so one page said "150 overdue", "167 late" and, in the
brief's own state row, `overdue: 167`. The 150 was the Hub's row cap, not a
count; the Hub's late count kept pending rows and dropped snoozed and
observed-only ones; the brief's count did the opposite; the plate read only
one spelling of the due date. Ruling A1 of 2026-11-10: the now layer is a
folder of ready answers rebuilt every run, and the log stays the record.

THE FILE
--------
`_hq/data/now/open_list/open_list.json`: one row per open top-level promise
(a step of a promise is counted on its parent, never on its own), with what
every reader filters on: direction, whether the owner has confirmed it,
whether it is observed only, the owner and the other people on it, its
thread, its due date (either spelling, `cru_match._commitment_field`), the
day it was opened, when a live snooze on it ends, how many open steps it has
and its source (event id and sequence). Nothing that depends on the clock is
stored: overdue, age and snoozed are worked out when the list is read, so a
list built this morning is still right tonight.

THE ONE COUNT
-------------
`HEADLINE_RULE` and `in_headline_overdue` are the whole definition of the
overdue number, in one place, so an owner ruling changes one function:
open, top-level, confirmed, not observed only, not snoozed, due before the
workspace's own today, whichever way it is owed. `headline_counts` gives that
number with its split by direction and the rows waiting to be confirmed,
which are counted on their own and never as late. `check_one_count` compares
what a surface is about to show with it and names every surface that
disagrees; `assert_one_count` raises.

READ AND WRITE
--------------
The log is read through `cru_match.load_open_commitments` (the owner's own
view: the open list is an owner surface) and the live snoozes through
`mute_ledger.live_mutes`. The only file written is the open list, whole,
through `atomic_write`, and only by `rebuild` (the Maintenance run). A
missing list, or one the log or the entities file has moved past, is never
rewritten by a reader: it is counted from the log in memory and says so
(`counted_from_log`). Run receipts do not make the list stale.

Command line, from the plugin root:
    python shared/scripts/open_list.py <workspace> rebuild [--apply]
    python shared/scripts/open_list.py <workspace> counts [--now ISO] [--json]
    python shared/scripts/open_list.py <workspace> list [--person ID]
        [--thread ID] [--direction D] [--overdue] [--sort due|age|opened]
    python shared/scripts/open_list.py <workspace> person NAME [--now ISO]
    python shared/scripts/open_list.py <workspace> thread ID [--now ISO]
    python shared/scripts/open_list.py <workspace> card NAME [--now ISO]
    python shared/scripts/open_list.py <workspace> thread-card ID [--now ISO]
    python shared/scripts/open_list.py <workspace> work-stream ID [--now ISO]
    python shared/scripts/open_list.py <workspace> priorities [--now ISO]
    python shared/scripts/open_list.py <workspace> company ORG_ID
    python shared/scripts/open_list.py <workspace> next-step NAME [--now ISO]
    python shared/scripts/open_list.py <workspace> cards-check [--now ISO]
    python shared/scripts/open_list.py <workspace> loose-ends [--kind K]
        [--now ISO]

THE INDEXES (part 2)
--------------------
The rebuild also writes `by_person.json` and `by_thread.json` beside the list
(`open_list_index`): for each person, what they owe the owner, what the owner
owes them, and the rows that name them without being linked to them; for
each thread, its rows by direction. `person` and `thread` above read them.

THE CARDS (train 2 part 3, train 3 part 1)
------------------------------------------
The rebuild also writes the person cards (`person_cards`) and the thread,
work-stream and priority cards (`thread_cards`, cr1#1013). `card`,
`thread-card`, `work-stream`, `priorities`, `company`, `next-step` and
`cards-check` above read them.

THE LOOSE ENDS (part 4)
-----------------------
The rebuild also writes `loose_ends.json` beside the list (`loose_ends`):
rows on no thread, rows waiting for a yes, lines heard but never tracked
(their own event type, read apart from the open set) and promises owed to
the owner that are ageing, each marked with its kind and its source.
`loose-ends` above reads it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_json  # noqa: E402

OPEN_LIST_DIR_REL = "_hq/data/now/open_list"
OPEN_LIST_FILE = "open_list.json"
#: 2 (part 2): each row also carries `direction_source` and
#: `unresolved_names`, which the indexes read.
#: 3 (cr1#1013): a row's source also keeps its pointer and the step that
#: wrote it, so a list written before that is rebuilt rather than read.
OPEN_LIST_VERSION = 3
#: The Maintenance job that rebuilds the list.
JOB_ID = "open-list"

DIRECTIONS = ("you_owe", "owed_to_you", "unowned")
#: Rows that never change the list: run receipts, as `receipts` registers
#: them.
try:
    from receipts import ALL_RECEIPT_TYPES as IGNORED_TYPES  # noqa: E402
except Exception:  # pragma: no cover: a reader never fails on an import
    IGNORED_TYPES = frozenset({"pack_run"})
#: The brief's own audit row reads the list and never changes it, so a brief
#: does not make the list stale either.
IGNORED_TYPES = frozenset(IGNORED_TYPES) | {"brief_state"}

#: The overdue number, in words. Kept beside `in_headline_overdue`, the one
#: function that applies it.
HEADLINE_RULE = ("open, confirmed, not observed only, not snoozed, not "
                 "waiting on an event, due "
                 "before today in the workspace's timezone, whichever way "
                 "it is owed; a step of a promise counts on its parent")
SORTS = ("due", "age", "opened")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _dir(ws: Path) -> Path:
    return Path(ws) / OPEN_LIST_DIR_REL


def _events_path(ws: Path) -> Path:
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def _entities_sha(ws: Path) -> str:
    try:
        return hashlib.sha256(
            (Path(ws) / "_hq" / "data" / "entities.json").read_bytes()
        ).hexdigest()
    except OSError:
        return ""


def _log_lines(ws: Path) -> list:
    """Every non-blank line of the log, as text, in order. Counting is by
    position, so a row with no sequence number is seen too."""
    try:
        text = _events_path(ws).read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.strip()]


def _lines_sha(lines: list) -> str:
    h = hashlib.sha256()
    for ln in lines:
        h.update(ln.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _line_type(line: str) -> Optional[str]:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row.get("type") if isinstance(row, dict) else None


def _parse_dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _bare_date(now_iso) -> Optional[date]:
    if isinstance(now_iso, str) and len(now_iso.strip()) == 10:
        return _as_date(now_iso)
    return None


def _now(now_iso=None) -> datetime:
    """The instant a timestamp names, or the real one."""
    if _bare_date(now_iso) is None:
        dt = _parse_dt(now_iso)
        if dt is not None:
            return dt
    return datetime.now(timezone.utc)


def _workspace_tz(ws: Path):
    """The workspace's timezone, resolved once (`tz.load_workspace_tz`, read
    through the module so a render's one-resolve pin sees it); UTC when it
    cannot be resolved, as `_now_calendar_day` falls back."""
    try:
        import tz as _tzmod
        return _tzmod.load_workspace_tz(ws)
    except Exception:  # noqa: BLE001
        return timezone.utc


def _local_today(ws: Path, now: datetime, tzinfo=None) -> date:
    """The workspace's own calendar day at `now`, the anchor the plate, the
    brief and the Hub already share. A caller that already holds the
    workspace's timezone (`tzinfo`) costs no second resolve."""
    if tzinfo is not None:
        return now.astimezone(tzinfo).date()
    from commitment_state import _now_calendar_day
    day = _now_calendar_day(now.isoformat(), workspace_path=str(ws))
    return day if isinstance(day, date) else now.date()


def _clock(ws: Path, now_iso=None, tzinfo=None) -> tuple[datetime, date]:
    """(the instant snoozes are judged at, the workspace's today). A bare
    date is a caller that holds only the day (the scheduled brief passes
    its local day): when it is the workspace's today it means now, the same
    instant the Hub uses; any other day means that day's start. A timestamp
    is that instant, read on the workspace's day."""
    day = _bare_date(now_iso)
    if day is None:
        now = _now(now_iso)
        return now, _local_today(ws, now, tzinfo)
    real = datetime.now(timezone.utc)
    if day == _local_today(ws, real, tzinfo):
        return real, day
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc), day


def _owner_id(ws: Path) -> str:
    try:
        from primary_user import resolve_primary_user
        return str(resolve_primary_user(ws) or "").strip()
    except Exception:  # noqa: BLE001: no owner reads every owned row as owed to you
        return ""


def _direction(ev: dict, owner: str) -> tuple[str, bool, str]:
    """(direction, confirmed, source): which way the promise is owed, whether
    the owner has confirmed it, and where the direction came from. The one
    place a row's direction is decided here.

    It is read from the field the event gate stores on the promise (#1034,
    `owed_direction.stored_direction`; source `stored`). When that value no
    longer fits the row (an owner moved by a path that stored nothing, a
    re-provisioned seat) or was never stored, it is worked out from the owner
    exactly as `commitment_state.bucket_of` does (source `worked_out`): no
    resolvable owner is `unowned` (source `none`), an owner that is not the
    workspace's user, or any owner when the user cannot be resolved, is
    `owed_to_you`. A row the extractor flagged for review keeps its
    direction and is `confirmed=False`."""
    from cru_match import _is_pending_review
    from cru_match import _commitment_field
    from owed_direction import OWED_TO_YOU, YOU_OWE, stored_direction
    confirmed = not _is_pending_review(ev)
    stored = stored_direction(ev, owner)
    if stored:
        return stored, confirmed, "stored"
    # The owner read raw, exactly as `bucket_of` reads it, so the worked-out
    # answer never differs from the counts that still use it.
    who = _commitment_field(ev, "owner_id")
    if not who:
        return "unowned", confirmed, "none"
    mine = bool(owner) and who == owner
    return (YOU_OWE if mine else OWED_TO_YOU), confirmed, "worked_out"


# ---------------------------------------------------------------------------
# Building the list
# ---------------------------------------------------------------------------

def _snoozes(ws: Path, now: datetime) -> dict:
    """{commitment id: when its live snooze ends}, the latest end per row:
    `mute_ledger.item_snoozes`, the one reading every surface shares, so a
    mute that names the item by its seq hides it here too (cr1#1292)."""
    from events_io import load_events_owner_scoped
    from mute_ledger import item_snoozes
    events = load_events_owner_scoped(str(ws))[0]
    return item_snoozes(events, now.isoformat())


def _waits(ws: Path, now: datetime, events=None) -> dict:
    """{commitment id: the event it waits on}: `commitment_wait.item_waits`,
    the one reading the plate and the Later writer share (cr1#1291).
    `events` is the owner's ledger when the caller already holds it."""
    from commitment_wait import item_waits
    if events is None:
        from events_io import load_events_owner_scoped
        events = load_events_owner_scoped(str(ws))[0]
    return {cid: w["wait_on"] for cid, w in item_waits(events, now.isoformat()).items()}


def _unresolved_names(d: dict) -> dict:
    """{field: [name, ...]} for the free-text names a promise carries that
    were never resolved to a person (`counterparty_names`,
    `received_from_names`); empty fields are left out."""
    out: dict = {}
    for field in ("counterparty_names", "received_from_names"):
        vals = d.get(field)
        names = [v.strip() for v in (vals if isinstance(vals, list) else [])
                 if isinstance(v, str) and v.strip()]
        if names:
            out[field] = names
    return out


#: cr1#1289: the mark on a `reclassification` the Hub's "Assign to" writes
#: (`cockpit_helpers.apply_commitment_choices`, verb `assign`). The list
#: honours only these when it places a promise on a thread, so no older
#: correction moves a row it never moved before.
HOME_ORIGIN = "hub_assign"


def archived_thread_ids(entities) -> frozenset:
    """cr1#1341: the ids of the threads the book holds as archived. Pure;
    an unreadable book answers none, so nothing falls back on a bad read."""
    from entities_io import entities_collection
    if not isinstance(entities, dict):
        return frozenset()
    try:
        recs = entities_collection(entities, "threads")
    except Exception:  # noqa: BLE001
        return frozenset()
    return frozenset(str(t.get("id")) for t in recs
                     if isinstance(t, dict) and t.get("id")
                     and str(t.get("status") or "").strip().lower() == "archived")


def homes_given(events, archived=frozenset()) -> dict:
    """{promise id: thread id} from the Hub's own "Assign to" presses
    (`HOME_ORIGIN` reclassifications), the newest per promise. Pure.

    cr1#1341: a home on a thread in `archived` (`archived_thread_ids`)
    answers "", so the promise falls back to no home and is listed under
    No home yet again, never lost with its stream. The Hub's Undo after
    Apply writes a home with no thread, which answers "" the same way."""
    out: dict = {}
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("type") != "reclassification":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = str(d.get("commitment_id") or "")
        if d.get("origin") != HOME_ORIGIN or not cid:
            continue
        seq = ev.get("seq") if isinstance(ev.get("seq"), int) else -1
        if seq >= out.get(cid, ("", -2))[1]:
            out[cid] = (str(ev.get("primary_thread_id") or ""), seq)
    return {cid: ("" if tid in archived else tid) for cid, (tid, _s) in out.items()}


def _archived_threads(ws: Path) -> frozenset:
    from entities_io import read_entities_json
    try:
        return archived_thread_ids(read_entities_json(Path(ws) / "_hq" / "data" / "entities.json"))
    except Exception:  # noqa: BLE001: an unreadable book drops no home
        return frozenset()


def build_rows(ws: Path, now: Optional[datetime] = None,
               tzinfo=None) -> list:
    """Every open top-level promise as a list row, oldest due first. Writes
    nothing."""
    from cru_match import (_commitment_field, _commitment_id,
                           load_open_commitments, partition_subitems)
    from commitment_parties import counterparty_ids
    from plate_view import TIER_OBSERVED, row_title

    ws = Path(ws)
    now = now or _now()
    ep = _events_path(ws)
    opens = load_open_commitments(ep) if ep.exists() else []
    top, steps = partition_subitems(opens)
    n_steps: dict = {}
    for s in steps:
        pid = str((s.get("data") or {}).get("parent_id") or "")
        n_steps[pid] = n_steps.get(pid, 0) + 1
    owner = _owner_id(ws)
    snoozed = _snoozes(ws, now)
    from events_io import load_events_owner_scoped
    _owner_events = load_events_owner_scoped(str(ws))[0]
    homes = homes_given(_owner_events, _archived_threads(ws))
    waiting = _waits(ws, now, _owner_events)
    rows = []
    for ev in top:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = str(_commitment_id(ev))
        direction, confirmed, direction_source = _direction(ev, owner)
        due = _as_date(_commitment_field(ev, "due"))
        # The day it was opened, on the workspace's own calendar: the
        # timezone is resolved once for the whole list, never per row.
        if tzinfo is None:
            tzinfo = _workspace_tz(ws)
        at = _parse_dt(ev.get("ts"))
        opened = at.astimezone(tzinfo).date() if at else None
        thread = ev.get("primary_thread_id") or d.get("primary_thread_id") \
            or d.get("thread_id") or ""
        # A home the owner gave it on the Hub (cr1#1289) wins.
        thread = homes.get(cid) or thread
        rows.append({
            "id": cid,
            "title": row_title(d.get("title"), d.get("summary"))[:240],
            "direction": direction,
            "direction_source": direction_source,
            "confirmed": confirmed,
            "observed": d.get("tier") == TIER_OBSERVED,
            "owner_id": str(_commitment_field(ev, "owner_id") or ""),
            "counterparty_ids": counterparty_ids(ev),
            # Names on the row no person id was found for: the indexes match
            # them, with the title, to list rows that name someone unlinked.
            "unresolved_names": _unresolved_names(d),
            "thread_id": str(thread),
            "due": due.isoformat() if due else "",
            "opened": opened.isoformat() if isinstance(opened, date) else "",
            "snooze_until": snoozed.get(cid, ""),
            # cr1#1291: the event it waits on; never counted late meanwhile.
            "waiting_on": waiting.get(cid, ""),
            "steps": n_steps.get(cid, 0),
            # The pointer and the step that wrote it ride along, so a page
            # can name the source in a word without reading the log again
            # (cr1#1013 part 3 follow-up). Absent when the row has none.
            "source": {"event_id": str(ev.get("id") or ""),
                       "seq": ev.get("seq"),
                       **({"source_ref": str(d.get("source_ref") or ev.get("source_ref"))}
                          if (d.get("source_ref") or ev.get("source_ref")) else {}),
                       **({"skill": str(ev.get("source_skill"))}
                          if ev.get("source_skill") else {})},
        })
    rows.sort(key=lambda r: (r["due"] or "9999-99-99", r["opened"], r["id"]))
    return rows


def _compute(ws: Path, now: Optional[datetime] = None, tzinfo=None) -> dict:
    ws = Path(ws)
    now = now or _now()
    lines = _log_lines(ws)
    return {"version": OPEN_LIST_VERSION,
            "built_at": now.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "log_lines": len(lines), "log_sha256": _lines_sha(lines),
            "entities_sha256": _entities_sha(ws),
            "rule": HEADLINE_RULE,
            "rows": build_rows(ws, now, tzinfo)}


def rebuild(ws, now_iso=None) -> dict:
    """Rebuild the list whole and write it: the one writer of the file."""
    ws = Path(ws)
    now = _clock(ws, now_iso)[0]
    doc = _compute(ws, now)
    atomic_write_json(_dir(ws) / OPEN_LIST_FILE, doc)
    # The per-person and per-thread indexes (part 2), from the same rows and
    # the same stamps, so a reader can tell when either has gone stale.
    # A failed index write never fails the list: a reader finds the old
    # index stale and works the answer out from the list instead.
    try:
        from open_list_index import write_indexes
        idx = write_indexes(ws, doc)
    except Exception as exc:  # noqa: BLE001
        idx = {"indexes_error": f"{type(exc).__name__}: {exc}"}
    # One card per person (part 3a), from the same build, the same way: a
    # failed write never fails the list; a reader finds the old cards stale.
    try:
        from person_cards import write_cards
        idx.update(write_cards(ws, doc))
    except Exception as exc:  # noqa: BLE001
        idx["cards_error"] = f"{type(exc).__name__}: {exc}"
    # Thread, work-stream and priority cards (cr1#1013, train 3 part 1),
    # the same way, with the fill-rate check's fields below their rate.
    try:
        from thread_cards import write_cards as write_thread_cards
        idx.update(write_thread_cards(ws, doc))
    except Exception as exc:  # noqa: BLE001
        idx["thread_cards_error"] = f"{type(exc).__name__}: {exc}"
    # The loose-ends list (part 4), on the same clock, the same way.
    try:
        from loose_ends import write_loose_ends
        idx.update(write_loose_ends(ws, doc, now))
    except Exception as exc:  # noqa: BLE001
        idx["loose_ends_error"] = f"{type(exc).__name__}: {exc}"
    return {"rows": len(doc["rows"]), "log_lines": doc["log_lines"], **idx}


def list_is_current(ws, doc) -> bool:
    """True when nothing that could change the list has landed since it was
    built: the same entities file, the same log lines (none rewritten in
    place), and every line after them a run receipt."""
    if not (isinstance(doc, dict) and isinstance(doc.get("rows"), list)
            and doc.get("version") == OPEN_LIST_VERSION):
        return False
    return stamps_current(ws, doc)


def stamps_current(ws, doc) -> bool:
    """`list_is_current`'s test of a file's build stamps (`log_lines`,
    `log_sha256`, `entities_sha256`) alone, shared with the indexes."""
    ws = Path(ws)
    if not isinstance(doc, dict):
        return False
    try:
        built = int(doc.get("log_lines"))
    except (TypeError, ValueError):
        return False
    if built < 0 or doc.get("entities_sha256") != _entities_sha(ws):
        return False
    lines = _log_lines(ws)
    if len(lines) < built or doc.get("log_sha256") != _lines_sha(lines[:built]):
        return False
    return all(_line_type(ln) in IGNORED_TYPES for ln in lines[built:])


def read_list(ws, now: Optional[datetime] = None,
              tzinfo=None) -> tuple[dict, bool]:
    """(the list, counted_from_log). A current file is read as is; a missing
    or stale one is counted from the log in memory, and nothing is
    written."""
    ws = Path(ws)
    try:
        doc = json.loads((_dir(ws) / OPEN_LIST_FILE).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if list_is_current(ws, doc):
        return doc, False
    return _compute(ws, now or _now(), tzinfo), True


# ---------------------------------------------------------------------------
# The one count
# ---------------------------------------------------------------------------

def is_snoozed(row: dict, now: datetime) -> bool:
    end = _parse_dt(row.get("snooze_until"))
    return end is not None and end > now


def is_late(row: dict, today: date) -> bool:
    """Due strictly before `today`; an undated row is never late."""
    due = _as_date(row.get("due"))
    return due is not None and due < today


def in_headline_overdue(row: dict, today: date, now: datetime) -> bool:
    """THE overdue rule (`HEADLINE_RULE`). Every surface's overdue number is
    the count of rows this returns True for."""
    return (bool(row.get("confirmed")) and not row.get("observed")
            and not is_snoozed(row, now) and not row.get("waiting_on")
            and is_late(row, today))


def verdict(ws, now_iso=None, *, tzinfo=None) -> dict:
    """The overdue verdict in one read of the list: `counts` (the numbers
    every surface shows: `overdue` by `HEADLINE_RULE` with its split by
    direction, `open` (confirmed, shown rows), `to_confirm` (rows waiting
    for the owner, never counted as late), and where they came from) and
    `counted_ids` (the rows that number counts). A caller that already holds
    the workspace's timezone passes it, so a render resolves it once."""
    ws = Path(ws)
    tzinfo = tzinfo or _workspace_tz(ws)  # one resolve for the whole call
    now, today = _clock(ws, now_iso, tzinfo)
    doc, from_log = read_list(ws, now, tzinfo)
    late = [r for r in doc["rows"] if in_headline_overdue(r, today, now)]
    shown = [r for r in doc["rows"]
             if not r.get("observed") and not is_snoozed(r, now)]
    by_dir = {d: 0 for d in DIRECTIONS}
    for r in late:
        by_dir[r.get("direction") or "unowned"] = \
            by_dir.get(r.get("direction") or "unowned", 0) + 1
    counts = {"overdue": len(late), "overdue_by_direction": by_dir,
              "open": sum(1 for r in shown if r.get("confirmed")),
              "to_confirm": sum(1 for r in shown if not r.get("confirmed")),
              "today": today.isoformat(), "rule": HEADLINE_RULE,
              "counted_from_log": from_log}
    return {"counts": counts, "counted_ids": {str(r["id"]) for r in late}}


def headline_counts(ws, now_iso=None, *, tzinfo=None) -> dict:
    """`verdict`'s numbers."""
    return verdict(ws, now_iso, tzinfo=tzinfo)["counts"]


def stamp_overdue(counts: dict, ws, now_iso=None, *, hc=None) -> dict:
    """Put the open list's overdue number into a `count_commitments`-shaped
    dict, in every key that carries it (`overdue`, its deprecated alias
    `stuck`, and `headline.overdue`), with the split by direction and where
    it came from. The brief's state row and the counting API call this, so
    they show the Hub's number. A caller that already read the verdict
    passes its numbers (`hc`). Returns `counts`, changed in place."""
    hc = hc if hc is not None else headline_counts(ws, now_iso)
    counts["overdue"] = hc["overdue"]
    if "stuck" in counts:
        counts["stuck"] = hc["overdue"]
    if isinstance(counts.get("headline"), dict) and \
            "overdue" in counts["headline"]:
        counts["headline"]["overdue"] = hc["overdue"]
    counts["overdue_by_direction"] = hc["overdue_by_direction"]
    counts["overdue_source"] = "open_list"
    return counts


def stamp_overdue_or_book(counts: dict, ws, now_iso=None) -> dict:
    """`stamp_overdue`, or, when the list cannot be read, the book's own
    number left in place and `overdue_source: "book"` saying so: never a
    guessed number and never a raise into a brief."""
    try:
        return stamp_overdue(counts, ws, now_iso)
    except Exception:  # noqa: BLE001
        counts["overdue_source"] = "book"
        return counts


def counted_ids(ws, now_iso=None, *, tzinfo=None) -> set:
    """The ids the overdue number counts, now: every surface that marks a
    single row as overdue reads this, never a date test of its own."""
    return verdict(ws, now_iso, tzinfo=tzinfo)["counted_ids"]


def stamp_state_overdue(state: dict, ws, now_iso=None) -> dict:
    """A brief state (`commitment_state.compute_brief_state`'s answer) with
    the open list's verdict: its counts' overdue number, and each attention
    row's `overdue` flag. The brief, its composed row and the end of day
    call this. When the list cannot be read the counts say `book` and the
    rows keep the book's own date test: never a guessed number."""
    stamp_overdue_or_book(state.get("counts") or {}, ws, now_iso)
    if (state.get("counts") or {}).get("overdue_source") != "open_list":
        return state
    try:
        ids = counted_ids(ws, now_iso)
    except Exception:  # noqa: BLE001
        return state
    for row in state.get("needs_attention") or []:
        if isinstance(row, dict) and "overdue" in row:
            row["overdue"] = str(row.get("commitment_id")) in ids
    return state


class OneCountMismatch(AssertionError):
    """A surface was about to show an overdue number the open list does
    not."""


def check_one_count(ws, surfaces: dict, now_iso=None) -> list:
    """Every surface whose overdue number differs from the open list's, as
    `{surface, shows, open_list}`; [] when they all agree."""
    want = headline_counts(ws, now_iso)["overdue"]
    return [{"surface": name, "shows": got, "open_list": want}
            for name, got in sorted(surfaces.items()) if got != want]


def assert_one_count(ws, surfaces: dict, now_iso=None) -> None:
    bad = check_one_count(ws, surfaces, now_iso)
    if bad:
        raise OneCountMismatch(
            "overdue counts disagree with the open list: " + "; ".join(
                f"{b['surface']} shows {b['shows']}, the open list "
                f"{b['open_list']}" for b in bad))


# ---------------------------------------------------------------------------
# Filtering and sorting
# ---------------------------------------------------------------------------

def query(ws, *, person: Optional[str] = None, thread: Optional[str] = None,
          direction: Optional[str] = None, overdue: Optional[bool] = None,
          due_from=None, due_to=None, min_age: Optional[int] = None,
          include_unconfirmed: bool = False, include_observed: bool = False,
          include_snoozed: bool = False, sort: str = "due",
          now_iso=None, tzinfo=None) -> dict:
    """The open list filtered and sorted by date, person, thread, age and
    direction. Each row gains `overdue` (`in_headline_overdue`, the ruling,
    so a row waiting to be confirmed is never overdue), `late` (the date
    test alone), `age_days` and `snoozed`, worked out now. `overdue=True`
    keeps the rows the headline number counts. `person` matches the owner
    or any other person on the row by id. Writes nothing."""
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {SORTS}, not {sort!r}")
    if direction is not None and direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}")
    ws = Path(ws)
    tzinfo = tzinfo or _workspace_tz(ws)  # one resolve for the whole call
    now, today = _clock(ws, now_iso, tzinfo)
    lo, hi = _as_date(due_from), _as_date(due_to)
    doc, from_log = read_list(ws, now, tzinfo)
    out = []
    for r in doc["rows"]:
        snoozed = is_snoozed(r, now)
        if (not include_unconfirmed and not r.get("confirmed")) or \
                (not include_observed and r.get("observed")) or \
                (not include_snoozed and snoozed):
            continue
        if person and person != r.get("owner_id") and \
                person not in (r.get("counterparty_ids") or []):
            continue
        if thread and thread != r.get("thread_id"):
            continue
        if direction and direction != r.get("direction"):
            continue
        due = _as_date(r.get("due"))
        if (lo or hi) and due is None:
            continue
        if (lo and due < lo) or (hi and due > hi):
            continue
        opened = _as_date(r.get("opened"))
        age = (today - opened).days if opened else None
        if min_age is not None and (age is None or age < min_age):
            continue
        counted = in_headline_overdue(r, today, now)
        if overdue is not None and counted != overdue:
            continue
        out.append({**r, "overdue": counted, "late": is_late(r, today),
                    "age_days": age, "snoozed": snoozed})
    if sort == "due":
        out.sort(key=lambda r: (r["due"] or "9999-99-99", r["id"]))
    elif sort == "age":
        out.sort(key=lambda r: (-(r["age_days"] if r["age_days"] is not None
                                  else -1), r["id"]))
    else:
        out.sort(key=lambda r: (r["opened"] or "9999-99-99", r["id"]))
    return {"rows": out, "count": len(out), "today": today.isoformat(),
            "counted_from_log": from_log}


# ---------------------------------------------------------------------------
# The Maintenance job
# ---------------------------------------------------------------------------

def run_open_list_job(ws, *, apply: bool = False, fired_via=None) -> dict:
    """The `open-list` Maintenance job. `apply=False` is the dry run: the
    rows are built and nothing is written. An applying run writes the list
    and one `pack_run` receipt under the job's own id, which never makes the
    list stale. Never raises for a refusal: the return says why."""
    import time as _time
    from receipts import FIRED_VIA, effective_fired_via, normalize_fired_via

    def _refusal(code: str, reason: str) -> dict:
        return {"ran": False, "applied": False, "refused": code,
                "reason": reason}

    via = normalize_fired_via(effective_fired_via(fired_via))
    if via not in FIRED_VIA:
        return _refusal("unknown_fired_via",
                        f"{fired_via!r} is not a fire provenance; nothing "
                        f"was written.")
    ws = Path(ws)
    if not (ws / "_hq" / "data").is_dir():
        return _refusal("not_a_workspace",
                        f"no _hq/data under {ws}; nothing was written.")
    t0 = _time.perf_counter()
    if not apply:
        return {"ran": True, "applied": False, "refused": None,
                "rows": len(build_rows(ws))}
    out = rebuild(ws)
    duration = int((_time.perf_counter() - t0) * 1000)
    from receipts import log_receipt
    log_receipt(ws, JOB_ID, receipt_type="pack_run", fired_via=via,
                surfaced=0, duration_ms=duration,
                extra_data={"rows": out["rows"]})
    return {"ran": True, "applied": True, "refused": None,
            "duration_ms": duration, **out}


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("workspace")
    sub = p.add_subparsers(dest="cmd", required=True)
    rb = sub.add_parser("rebuild")
    rb.add_argument("--apply", action="store_true")
    rb.add_argument("--fired-via", default=None)
    rb.add_argument("--triggered-by", default=None,
                    help="the surface that ran the job, carried into the "
                         "receipt")
    ct = sub.add_parser("counts")
    ct.add_argument("--now", default=None)
    ct.add_argument("--json", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--person")
    ls.add_argument("--thread")
    ls.add_argument("--direction", choices=DIRECTIONS)
    ls.add_argument("--overdue", action="store_true")
    ls.add_argument("--sort", choices=SORTS, default="due")
    ls.add_argument("--now", default=None)
    pe = sub.add_parser("person")
    pe.add_argument("name")
    pe.add_argument("--now", default=None)
    th = sub.add_parser("thread")
    th.add_argument("thread_id")
    th.add_argument("--now", default=None)
    cd = sub.add_parser("card")
    cd.add_argument("name")
    cd.add_argument("--now", default=None)
    for name, arg in (("thread-card", "thread_id"), ("work-stream", "thread_id"),
                      ("next-step", "name")):
        sp = sub.add_parser(name)
        sp.add_argument(arg)
        sp.add_argument("--now", default=None)
    for name in ("priorities", "cards-check"):
        sub.add_parser(name).add_argument("--now", default=None)
    sub.add_parser("company").add_argument("org_id")
    le = sub.add_parser("loose-ends")
    le.add_argument("--kind", default=None)
    le.add_argument("--now", default=None)
    a = p.parse_args(list(argv) if argv is not None else None)
    ws = Path(a.workspace)
    if a.cmd == "rebuild":
        # The dispatcher appends `--fired-via X --triggered-by Y`; the
        # receipt reads the trigger from the environment, as ledgers.py does.
        if a.triggered_by:
            import os
            os.environ["CR_TRIGGERED_BY"] = str(a.triggered_by)
        out = run_open_list_job(ws, apply=a.apply, fired_via=a.fired_via)
        print(json.dumps(out))  # one line: the dispatcher reads the last one
        return 0 if out.get("ran") else 1
    if a.cmd == "counts":
        out = headline_counts(ws, a.now)
        if a.json:
            print(json.dumps(out, indent=2))
        else:
            d = out["overdue_by_direction"]
            print(f"{out['overdue']} overdue ({d['you_owe']} you owe, "
                  f"{d['owed_to_you']} owed to you, {d['unowned']} with no "
                  f"owner); {out['open']} open; {out['to_confirm']} to "
                  f"confirm. Overdue means: {out['rule']}.")
        return 0
    if a.cmd == "card":
        import person_cards
        print(json.dumps(person_cards.card_view(ws, a.name, a.now), indent=2))
        return 0
    if a.cmd in ("thread-card", "work-stream", "priorities", "company",
                 "next-step", "cards-check"):
        import thread_cards as tcards
        if a.cmd == "thread-card":
            out = tcards.thread_card_view(ws, a.thread_id, a.now)
        elif a.cmd == "work-stream":
            out = tcards.work_stream_view(ws, a.thread_id, a.now)
        elif a.cmd == "priorities":
            out = tcards.priorities_view(ws, a.now)
        elif a.cmd == "company":
            out = tcards.company_filter(ws, a.org_id)
        elif a.cmd == "next-step":
            out = tcards.next_step_for_person(ws, a.name, a.now)
        else:
            out = tcards.fill_check(ws, a.now)
        print(json.dumps(out, indent=2))
        return 0
    if a.cmd == "loose-ends":
        import loose_ends
        try:
            out = loose_ends.loose_ends_view(ws, a.now, kind=a.kind)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(out, indent=2))
        return 0
    if a.cmd in ("person", "thread"):
        import open_list_index as oli
        if a.cmd == "person":
            out = oli.person_view(ws, a.name, a.now)
        else:
            out = oli.thread_view(ws, a.thread_id, a.now)
        print(json.dumps(out, indent=2))
        return 0
    out = query(ws, person=a.person, thread=a.thread, direction=a.direction,
                overdue=True if a.overdue else None, sort=a.sort,
                now_iso=a.now)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
