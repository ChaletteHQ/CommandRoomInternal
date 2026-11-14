#!/usr/bin/env python3
"""The per-person and per-thread indexes beside the open list (cr1#1012,
train 2 part 2).

WHAT IT ANSWERS
---------------
"What does X owe me" and "what do I owe X", both ways, and what is open on a
thread. Two files sit beside `open_list.json` in `_hq/data/now/open_list/`:

- `by_person.json`: for each person other than the workspace's owner,
  `owes_you` (rows they own, owed to the owner), `you_owe` (rows the owner
  owns with them on the other side), `other` (rows they are linked to some
  other way: a promise between two other people, or one with no owner) and
  `unlinked` (rows that name them but are not linked to them, each with the
  field the name was found in, the words matched and the source event).
- `by_thread.json`: for each thread, its row ids by direction.

Direction is the list row's own, which reads the stored field (#1034). Both
files are written whole by `open_list.rebuild` (the Maintenance run) and by
nothing else. They carry the list's build stamps plus the aliases file's
hash; a reader that finds either stale works the answer out from the list in
memory and says so (`counted_from_log`), and never writes.

WHO IS MEANT
------------
`resolve_person` reads names from the entities file and from the alias reader
(`alias_index.alias_rows`, #954, every shape aliases.json has been written
in). A name, an alias, a first name or an id, in any case. When it fits more
than one person, the one with the more recent contact is picked (the later
of the entities file's `last_interaction` and the newest open row they are
linked to); when the two most recent are within `CLOSE_DAYS` of each other,
the answer is a question naming both, and no rows are listed.

MATCHING A NAME IN A ROW
------------------------
A name matches in any case and only as a whole word, so "Dustinesque" is no
one. A single word must also be written with a capital in the row, so a
lowercase word in a sentence is no one, whatever case the alias file uses. A name that fits two people is given to
neither. The owner's own names are not indexed.

Read-only except `write_indexes`. stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
import sys  # noqa: E402
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_json  # noqa: E402

BY_PERSON_FILE = "by_person.json"
BY_THREAD_FILE = "by_thread.json"
INDEX_VERSION = 1
#: Two people who share a name are "close" when their last contacts are this
#: many days apart or fewer; then the reader asks instead of picking.
CLOSE_DAYS = 14
#: The fields of a row searched for a person's name, in order.
_TEXT_FIELDS = ("title",)


# ---------------------------------------------------------------------------
# Reading the people
# ---------------------------------------------------------------------------

def _data_dir(ws) -> Path:
    return Path(ws) / "_hq" / "data"


def _aliases_sha(ws) -> str:
    try:
        return hashlib.sha256((_data_dir(ws) / "aliases.json").read_bytes()
                              ).hexdigest()
    except OSError:
        return ""


def _people(ws) -> dict:
    """{person id: {"name": shown name, "names": [every name on file],
    "last": last_interaction as a date or None}}, from the entities file and
    the alias reader. Never raises."""
    out: dict = {}
    try:
        from entities_io import entities_collection
        raw = json.loads((_data_dir(ws) / "entities.json").read_text(
            encoding="utf-8"))
        for p in entities_collection(raw, "people"):
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            names = [p.get("canonical_name"), p.get("name")] + \
                list(p.get("aliases") or [])
            names = [n.strip() for n in names if isinstance(n, str) and n.strip()]
            out[pid] = {"name": names[0] if names else pid,
                        "names": list(dict.fromkeys(names)),
                        "last": _as_date(p.get("last_interaction"))}
    except Exception:  # noqa: BLE001: no entities file means no people
        return {}
    try:
        from alias_index import alias_rows
        doc = json.loads((_data_dir(ws) / "aliases.json").read_text(
            encoding="utf-8"))
        for row in alias_rows(doc):
            pid = row.get("canonical_id")
            if pid in out and row["raw"] not in out[pid]["names"]:
                out[pid]["names"].append(row["raw"])
    except Exception:  # noqa: BLE001: no aliases file adds no names
        pass
    return out


def _as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _owner(ws) -> str:
    import open_list
    return open_list._owner_id(Path(ws))


# ---------------------------------------------------------------------------
# Matching a name in a row
# ---------------------------------------------------------------------------

def _name_forms(people: dict, owner: str) -> dict:
    """{name as written: person id} for every name that fits exactly one
    person other than the owner (a name two people share, or one the owner
    shares, is dropped). Compared case-insensitively here; `_pattern` makes a
    single word case-sensitive."""
    holders: dict = {}
    for pid, p in people.items():
        for n in p["names"]:
            if len(n) < 2:
                continue
            holders.setdefault(n.casefold(), {}).setdefault(pid, n)
    out: dict = {}
    for key, by_pid in holders.items():
        if len(by_pid) != 1:
            continue
        pid, written = next(iter(by_pid.items()))
        if pid == owner:
            continue
        out[written] = pid
    return out


def _pattern(name: str):
    body = r"\s+".join(re.escape(w) for w in name.split())
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE)


def _found(pat, text: str):
    """The first match of a name in a text, or None. A one-word name counts
    only where the text writes it with a capital."""
    for m in pat.finditer(text):
        words = m.group(0).split()
        if len(words) > 1 or words[0][:1].isupper():
            return m
    return None


def _mentions(row: dict, forms: list) -> list:
    """[(person id, field, words matched)] for every name found in the row's
    title or its unresolved names, one per person (the first field wins)."""
    seen: dict = {}
    texts = [(f, row.get(f)) for f in _TEXT_FIELDS]
    for field, names in (row.get("unresolved_names") or {}).items():
        texts += [(field, n) for n in names]
    for field, text in texts:
        if not isinstance(text, str) or not text:
            continue
        for written, pid, pat in forms:
            if pid in seen:
                continue
            m = _found(pat, text)
            if m:
                seen[pid] = (pid, field, m.group(0))
    return list(seen.values())


# ---------------------------------------------------------------------------
# Building the indexes
# ---------------------------------------------------------------------------

def build_indexes(rows: list, people: dict, owner: str) -> tuple[dict, dict]:
    """(people entries, thread entries) from the open list's rows. Pure."""
    forms = sorted(((w, pid, _pattern(w)) for w, pid in
                    _name_forms(people, owner).items()),
                   key=lambda t: (-len(t[0]), t[0]))
    by_person: dict = {}
    by_thread: dict = {}

    def entry(pid: str) -> dict:
        return by_person.setdefault(pid, {
            "name": (people.get(pid) or {}).get("name", pid),
            "owes_you": [], "you_owe": [], "other": [], "unlinked": []})

    for r in rows:
        rid = str(r.get("id"))
        direction = r.get("direction") or "unowned"
        holder = str(r.get("owner_id") or "")
        others = [str(c) for c in (r.get("counterparty_ids") or []) if c]
        linked = {holder, *others} - {""}
        for pid in sorted(linked - {owner} if owner else linked):
            if direction == "owed_to_you" and pid == holder:
                entry(pid)["owes_you"].append(rid)
            elif direction == "you_owe" and pid in others:
                entry(pid)["you_owe"].append(rid)
            else:
                entry(pid)["other"].append(rid)
        for pid, field, words in _mentions(r, forms):
            if pid in linked:
                continue
            entry(pid)["unlinked"].append({
                "id": rid, "field": field, "matched": words,
                "source": dict(r.get("source") or {})})
        tid = str(r.get("thread_id") or "")
        if tid:
            t = by_thread.setdefault(tid, {"you_owe": [], "owed_to_you": [],
                                            "unowned": []})
            t.setdefault(direction, []).append(rid)
    return by_person, by_thread


def _stamps(doc: dict, ws) -> dict:
    return {"version": INDEX_VERSION, "built_at": doc.get("built_at"),
            "log_lines": doc.get("log_lines"),
            "log_sha256": doc.get("log_sha256"),
            "entities_sha256": doc.get("entities_sha256"),
            "aliases_sha256": _aliases_sha(ws),
            "open_list_version": doc.get("version")}


def write_indexes(ws, doc: dict) -> dict:
    """Write both indexes for an open list document just built. Called only
    by `open_list.rebuild`."""
    ws = Path(ws)
    import open_list
    by_person, by_thread = build_indexes(doc.get("rows") or [], _people(ws),
                                         _owner(ws))
    stamps = _stamps(doc, ws)
    d = ws / open_list.OPEN_LIST_DIR_REL
    atomic_write_json(d / BY_PERSON_FILE, {**stamps, "people": by_person})
    atomic_write_json(d / BY_THREAD_FILE, {**stamps, "threads": by_thread})
    return {"people": len(by_person), "threads": len(by_thread)}


def _index_current(ws, idx, list_doc: dict, key: str) -> bool:
    import open_list
    return (isinstance(idx, dict) and isinstance(idx.get(key), dict)
            and idx.get("version") == INDEX_VERSION
            and idx.get("open_list_version") == open_list.OPEN_LIST_VERSION
            and idx.get("log_sha256") == list_doc.get("log_sha256")
            and idx.get("log_lines") == list_doc.get("log_lines")
            and idx.get("aliases_sha256") == _aliases_sha(ws)
            and open_list.stamps_current(ws, idx))


def _read(ws, key: str, fname: str, now=None, tzinfo=None):
    """(entries, the list document, counted_from_log)."""
    import open_list
    ws = Path(ws)
    list_doc, from_log = open_list.read_list(ws, now, tzinfo)
    try:
        idx = json.loads((ws / open_list.OPEN_LIST_DIR_REL / fname).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        idx = None
    if not from_log and _index_current(ws, idx, list_doc, key):
        return idx[key], list_doc, False
    by_person, by_thread = build_indexes(list_doc.get("rows") or [],
                                         _people(ws), _owner(ws))
    return (by_person if key == "people" else by_thread), list_doc, True


# ---------------------------------------------------------------------------
# Who is meant
# ---------------------------------------------------------------------------

def _recency(people: dict, rows: list) -> dict:
    """{person id: the latest of their last_interaction and the opening day
    of the newest open row they are linked to}."""
    out = {pid: p["last"] for pid, p in people.items()}
    for r in rows:
        opened = _as_date(r.get("opened"))
        if opened is None:
            continue
        for pid in {str(r.get("owner_id") or ""),
                    *[str(c) for c in r.get("counterparty_ids") or []]}:
            if pid in out and (out[pid] is None or opened > out[pid]):
                out[pid] = opened
    return out


def resolve_person(ws, who: str, rows: Optional[list] = None) -> dict:
    """Who a name means: `{"status": "found", "id", "name"}`,
    `{"status": "ask", "candidates": [{id, name, last_contact}, ...]}` or
    `{"status": "not_found"}`. A name that fits several people picks the one
    with the more recent contact unless the two most recent are within
    `CLOSE_DAYS`."""
    import open_list
    ws = Path(ws)
    text = (who or "").strip()
    people = _people(ws)
    if not text:
        return {"status": "not_found", "query": who}
    if text in people:
        return {"status": "found", "id": text, "name": people[text]["name"]}
    key = " ".join(text.split()).casefold()
    one_word = len(key.split()) == 1

    def fits(n: str) -> bool:
        words = n.split()
        if " ".join(words).casefold() == key:
            return True
        # A first name fits every full name it starts, whichever name or
        # alias carries it, so two people who share one are both seen.
        return one_word and len(words) > 1 and words[0].casefold() == key

    hits = [pid for pid, p in people.items() if any(fits(n) for n in p["names"])]
    # The owner is never the person asked about: "what does X owe me" is about
    # someone else, so a name the owner shares means the other person.
    owner = _owner(ws)
    if owner in hits and text != owner:
        hits.remove(owner)
        if not hits:
            return {"status": "not_found", "query": who, "is_owner": True}
    if not hits:
        return {"status": "not_found", "query": who}
    if len(hits) == 1:
        return {"status": "found", "id": hits[0], "name": people[hits[0]]["name"]}
    if rows is None:
        rows = (open_list.read_list(ws)[0].get("rows") or [])
    rec = _recency(people, rows)
    ranked = sorted(hits, key=lambda pid: (rec.get(pid) or date.min, pid),
                    reverse=True)
    cands = [{"id": pid, "name": people[pid]["name"],
              "last_contact": rec[pid].isoformat() if rec.get(pid) else ""}
             for pid in ranked]
    top, second = rec.get(ranked[0]), rec.get(ranked[1])
    if top is not None and (second is None or
                            (top - second).days > CLOSE_DAYS):
        return {"status": "found", "id": ranked[0],
                "name": people[ranked[0]]["name"], "also": cands[1:]}
    return {"status": "ask", "query": who, "candidates": cands}


# ---------------------------------------------------------------------------
# The views
# ---------------------------------------------------------------------------

def _shape(rows_by_id: dict, ids: list, today, now) -> list:
    import open_list as ol
    out = []
    for rid in ids:
        r = rows_by_id.get(str(rid))
        if r is None:
            continue
        out.append({**r, "overdue": ol.in_headline_overdue(r, today, now),
                    "late": ol.is_late(r, today),
                    "snoozed": ol.is_snoozed(r, now)})
    out.sort(key=lambda r: (r.get("due") or "9999-99-99", r["id"]))
    return out


def _visible(r: dict) -> bool:
    return not r.get("observed") and not r.get("snoozed")


def person_view(ws, who: str, now_iso=None) -> dict:
    """What a person owes the owner and what the owner owes them, both ways.
    Rows follow the open list's default view: confirmed, not observed only,
    not snoozed, each with `overdue` by the one rule. Rows waiting for a yes
    are listed apart (`to_confirm`), and so are the rows that name the
    person without being linked to them (`unlinked`, each with its source).
    Writes nothing."""
    import open_list as ol
    ws = Path(ws)
    tzinfo = ol._workspace_tz(ws)
    now, today = ol._clock(ws, now_iso, tzinfo)
    people, list_doc, from_log = _read(ws, "people", BY_PERSON_FILE, now, tzinfo)
    rows = list_doc.get("rows") or []
    found = resolve_person(ws, who, rows)
    base = {"today": today.isoformat(), "counted_from_log": from_log,
            "owes_you": [], "you_owe": [], "other": [], "to_confirm": [],
            "unlinked": []}
    if found.get("status") != "found":
        return {**base, **found}
    e = people.get(found["id"]) or {}
    by_id = {str(r.get("id")): r for r in rows}
    out = {**base, "status": "found",
           "person": {"id": found["id"], "name": found["name"]}}
    if found.get("also"):
        out["also"] = found["also"]
    for key in ("owes_you", "you_owe", "other"):
        shaped = [r for r in _shape(by_id, e.get(key) or [], today, now)
                  if _visible(r)]
        out[key] = [r for r in shaped if r.get("confirmed")]
        out["to_confirm"] += [r for r in shaped if not r.get("confirmed")]
    unlinked = []
    for u in e.get("unlinked") or []:
        shaped = _shape(by_id, [u.get("id")], today, now)
        if shaped and _visible(shaped[0]):
            unlinked.append({**shaped[0], "field": u.get("field"),
                             "matched": u.get("matched"),
                             "source": u.get("source")})
    out["unlinked"] = unlinked
    return out


def thread_view(ws, thread_id: str, now_iso=None) -> dict:
    """What is open on one thread, by direction, in the open list's default
    view (rows waiting for a yes apart). Writes nothing."""
    import open_list as ol
    ws = Path(ws)
    tzinfo = ol._workspace_tz(ws)
    now, today = ol._clock(ws, now_iso, tzinfo)
    threads, list_doc, from_log = _read(ws, "threads", BY_THREAD_FILE, now,
                                        tzinfo)
    by_id = {str(r.get("id")): r for r in list_doc.get("rows") or []}
    e = threads.get(str(thread_id or "")) or {}
    out = {"thread_id": str(thread_id or ""), "today": today.isoformat(),
           "counted_from_log": from_log, "to_confirm": []}
    for key in ("you_owe", "owed_to_you", "unowned"):
        shaped = [r for r in _shape(by_id, e.get(key) or [], today, now)
                  if _visible(r)]
        out[key] = [r for r in shaped if r.get("confirmed")]
        out["to_confirm"] += [r for r in shaped if not r.get("confirmed")]
    return out


__all__ = ["BY_PERSON_FILE", "BY_THREAD_FILE", "CLOSE_DAYS", "build_indexes",
           "write_indexes", "resolve_person", "person_view", "thread_view"]
