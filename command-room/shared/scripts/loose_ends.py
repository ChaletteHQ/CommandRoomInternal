#!/usr/bin/env python3
"""The loose-ends list beside the open list (cr1#1012, train 2 part 4).

WHAT IT HOLDS
-------------
The promises nobody has finished putting away, each marked with the kind of
loose end it is (a row can be more than one):

  unfiled      an open promise, or a heard line, on no thread;
  unconfirmed  an open promise still waiting for the owner's yes;
  observed     a line heard but never tracked: a live `commitment_observed`
               event (`capture_gate.observed_from_commitment_event` writes
               them; `capture_gate.live_observed` is the one read of the
               tier: not past its window, never promoted). These never enter
               the open set, so this list reads them on their own. An open
               row the list marks `observed` is one too;
  owed_ageing  a confirmed promise owed to the owner, opened at least
               `OWED_AGEING_DAYS` ago and not yet due (no date, or a date
               already past).

A row the owner has snoozed or held (the mute ledger, `live_mutes`, open
promises and observed lines alike) is left out while it lasts: the owner
has already looked at it.

Rulings, owner, 2026-11-10:
  A4  a field shows only when a named job fills it. A kind with no rows is
      absent from `kinds`, every kind names its `job`, and a row carries
      only the fields its source filled.
  C3  the list is for display; recall goes to the source. Every row carries
      `source` (its log row `seq`, and the `source_ref` an observed line was
      heard in), so a reader that needs the words goes to the record.

WHERE IT LIVES
--------------
`_hq/data/now/open_list/loose_ends.json`, written whole by
`open_list.rebuild` (the Maintenance run) beside the open list and its
indexes, with the list's build stamps, the day it was built and the ageing
threshold it used. Ageing and an observed line's window depend on the day,
so a file built on another day is stale. A reader that finds the file stale
works the list out in memory, says so (`counted_from_log`), and never
writes; snoozes are judged when the list is read.

OWNER ONLY. Observed lines are read from the whole log, personal ones
included, and the file says so (`audience: "owner"`). An org-facing reader
must not read it, or must filter it through `personal_leak` first.

Read-only except `write_loose_ends`. stdlib only.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_json  # noqa: E402

LOOSE_ENDS_FILE = "loose_ends.json"
LOOSE_ENDS_VERSION = 1
#: A promise owed to the owner is "ageing" once it has been open this many
#: days with no date still ahead of it. A default the owner has not ruled on.
OWED_AGEING_DAYS = 14

KIND_UNFILED = "unfiled"
KIND_UNCONFIRMED = "unconfirmed"
KIND_OBSERVED = "observed"
KIND_OWED_AGEING = "owed_ageing"
#: The kinds in the order a row lists them and a reader shows them.
KINDS = (KIND_UNFILED, KIND_UNCONFIRMED, KIND_OBSERVED, KIND_OWED_AGEING)
#: What each kind is called, and the job that fills it (A4).
KIND_INFO = {
    KIND_UNFILED: {"label": "not on a thread",
                   "job": "the open list: each promise's thread"},
    KIND_UNCONFIRMED: {"label": "waiting for your yes",
                       "job": "the open list: promises the capture flagged "
                              "for review"},
    KIND_OBSERVED: {"label": "heard, never tracked",
                    "job": "the set-aside tier: lines the capture kept "
                           "without opening them"},
    KIND_OWED_AGEING: {"label": "owed to you and ageing",
                       "job": f"the open list: promises owed to you, open "
                              f"{OWED_AGEING_DAYS} days or more with no "
                              f"date still ahead"},
}
FROM_OPEN_LIST = "open_list"
FROM_OBSERVED = "observed_tier"


def _dir(ws) -> Path:
    import open_list
    return Path(ws) / open_list.OPEN_LIST_DIR_REL


def _as_date(value) -> Optional[date]:
    import open_list
    return open_list._as_date(value)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _drop_empty(row: dict) -> dict:
    """A4: a row carries only the fields its source filled."""
    return {k: v for k, v in row.items() if v not in ("", None, [], {})}


def _open_row_kinds(r: dict, today: date) -> list:
    kinds = []
    if not str(r.get("thread_id") or "").strip():
        kinds.append(KIND_UNFILED)
    if not r.get("confirmed"):
        kinds.append(KIND_UNCONFIRMED)
    if r.get("observed"):
        kinds.append(KIND_OBSERVED)
    if is_owed_ageing(r, today):
        kinds.append(KIND_OWED_AGEING)
    return kinds


def is_owed_ageing(r: dict, today: date) -> bool:
    """Confirmed, not observed only, owed to the owner, opened at least
    `OWED_AGEING_DAYS` before `today`, and no date still ahead (undated, or
    due before today)."""
    if r.get("direction") != "owed_to_you" or not r.get("confirmed") \
            or r.get("observed"):
        return False
    opened = _as_date(r.get("opened"))
    if opened is None or (today - opened).days < OWED_AGEING_DAYS:
        return False
    due = _as_date(r.get("due"))
    return due is None or due < today


def _observed_row(ev: dict, tzinfo, snoozes: dict) -> dict:
    """A live observed event as a loose-end row."""
    import open_list
    from commitment_parties import counterparty_ids
    from plate_view import row_title
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    held = open_list._parse_dt(d.get("held_at")) or \
        open_list._parse_dt(ev.get("ts"))
    due = _as_date(d.get("due") or d.get("due_date"))
    thread = ev.get("primary_thread_id") or d.get("primary_thread_id") \
        or d.get("thread_id") or ""
    oid = str(d.get("id") or "") or f"seq:{ev.get('seq')}"
    return _drop_empty({
        "id": oid,
        "title": row_title(d.get("title"), d.get("summary"))[:240],
        "kinds": [k for k in KINDS if k == KIND_OBSERVED or
                  (k == KIND_UNFILED and not str(thread).strip())],
        "from": FROM_OBSERVED,
        "owner_id": str(d.get("owner_id") or ""),
        "counterparty_ids": counterparty_ids(ev),
        "thread_id": str(thread),
        "due": due.isoformat() if due else "",
        "opened": held.astimezone(tzinfo).date().isoformat() if held else "",
        "snooze_until": snoozes.get(oid, ""),
        "reason": str(d.get("held_review_reason") or
                      d.get("observed_reason") or ""),
        "source": _drop_empty({"seq": ev.get("seq"),
                               "source_ref": str(d.get("source_ref") or "")}),
    })


def build_loose_ends(rows: list, observed: list, today: date,
                     tzinfo, snoozes: Optional[dict] = None) -> list:
    """Every loose end, oldest opened first. `rows` are the open list's rows,
    `observed` the live observed events, `snoozes` {observed id: when its
    live mute ends}. Pure: writes nothing."""
    out = []
    for r in rows:
        kinds = _open_row_kinds(r, today)
        if not kinds:
            continue
        out.append(_drop_empty({
            "id": str(r.get("id")),
            "title": r.get("title") or "",
            "kinds": [k for k in KINDS if k in kinds],
            "from": FROM_OPEN_LIST,
            "direction": r.get("direction") or "",
            "owner_id": r.get("owner_id") or "",
            "counterparty_ids": list(r.get("counterparty_ids") or []),
            "thread_id": r.get("thread_id") or "",
            "due": r.get("due") or "",
            "opened": r.get("opened") or "",
            "snooze_until": r.get("snooze_until") or "",
            "source": _drop_empty(dict(r.get("source") or {})),
        }))
    for ev in observed:
        out.append(_observed_row(ev, tzinfo, snoozes or {}))
    out.sort(key=lambda r: (r.get("opened") or "9999-99-99", r["id"]))
    return out


def _live_observed(ws, now: datetime) -> list:
    from capture_gate import live_observed
    return live_observed(str(ws), now=now)


def _compute(ws, list_doc: dict, now: datetime, today: date,
             tzinfo) -> list:
    import open_list
    return build_loose_ends(list_doc.get("rows") or [],
                            _live_observed(ws, now), today, tzinfo,
                            open_list._snoozes(Path(ws), now))


def _stamps(doc: dict, today: date) -> dict:
    return {"version": LOOSE_ENDS_VERSION, "built_at": doc.get("built_at"),
            "built_day": today.isoformat(),
            "owed_ageing_days": OWED_AGEING_DAYS,
            "log_lines": doc.get("log_lines"),
            "log_sha256": doc.get("log_sha256"),
            "entities_sha256": doc.get("entities_sha256"),
            "open_list_version": doc.get("version"),
            "audience": "owner"}


def write_loose_ends(ws, doc: dict, now: Optional[datetime] = None) -> dict:
    """Write the loose-ends list for an open list document just built, on
    the same clock. Called only by `open_list.rebuild`."""
    import open_list
    ws = Path(ws)
    tzinfo = open_list._workspace_tz(ws)
    now = now or open_list._parse_dt(doc.get("built_at")) or open_list._now()
    today = open_list._local_today(ws, now, tzinfo)
    rows = _compute(ws, doc, now, today, tzinfo)
    atomic_write_json(_dir(ws) / LOOSE_ENDS_FILE,
                      {**_stamps(doc, today), "rows": rows})
    return {"loose_ends": len(rows)}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _file_current(ws, f, list_doc: dict, today: date) -> bool:
    import open_list
    return (isinstance(f, dict) and isinstance(f.get("rows"), list)
            and f.get("version") == LOOSE_ENDS_VERSION
            and f.get("open_list_version") == open_list.OPEN_LIST_VERSION
            and f.get("built_day") == today.isoformat()
            and f.get("owed_ageing_days") == OWED_AGEING_DAYS
            and f.get("log_sha256") == list_doc.get("log_sha256")
            and f.get("log_lines") == list_doc.get("log_lines")
            and open_list.stamps_current(ws, f))


def loose_ends_view(ws, now_iso=None, *, kind: Optional[str] = None,
                    keep_ids=None) -> dict:
    """The loose-ends list now: `rows` (each with its `kinds` and `source`,
    and `age_days` worked out now), `by_kind` (how many rows of each kind)
    and `kinds` (the label and job of each kind present). A row snoozed now
    is left out. `kind` keeps one kind. Writes nothing."""
    import open_list
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    ws = Path(ws)
    tzinfo = open_list._workspace_tz(ws)
    now, today = open_list._clock(ws, now_iso, tzinfo)
    list_doc, from_log = open_list.read_list(ws, now, tzinfo)
    try:
        f = json.loads((_dir(ws) / LOOSE_ENDS_FILE).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        f = None
    if not from_log and _file_current(ws, f, list_doc, today):
        rows = f["rows"]
    else:
        rows, from_log = _compute(ws, list_doc, now, today, tzinfo), True
    _keep = {str(k) for k in (keep_ids or ())}
    out = []
    for r in rows:
        # cr1#1292: `keep_ids` keeps snoozed rows a caller places by date
        # (the Week board keeps an item a Later moved, on its new day).
        if open_list.is_snoozed(r, now) and str(r.get("id")) not in _keep:
            continue
        if kind is not None and kind not in (r.get("kinds") or []):
            continue
        r = {k: v for k, v in r.items() if k != "snooze_until"}
        opened = _as_date(r.get("opened"))
        if opened is not None:
            r["age_days"] = (today - opened).days
        out.append(r)
    by_kind = {k: sum(1 for r in out if k in (r.get("kinds") or []))
               for k in KINDS}
    by_kind = {k: n for k, n in by_kind.items() if n}
    return {"today": today.isoformat(), "counted_from_log": from_log,
            "audience": "owner", "count": len(out), "by_kind": by_kind,
            "kinds": {k: dict(KIND_INFO[k]) for k in by_kind},
            "rows": out}


__all__ = ["LOOSE_ENDS_FILE", "OWED_AGEING_DAYS", "KINDS", "KIND_INFO",
           "build_loose_ends", "is_owed_ageing", "write_loose_ends",
           "loose_ends_view"]
