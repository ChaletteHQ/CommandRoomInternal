#!/usr/bin/env python3
"""The decision list: one row per decision, and the answer to "what did we
decide about X" (cr1#1015, train 6 of the memory and Hub programme).

WHY THIS EXISTS
---------------
The log holds every decision, its closes and its replacements, but no one
place said, for a decision: what it was, who made it, when, on which thread,
what it replaced, and whether it still stands. A reader asked "what did we
decide about hosting?" found the words in an older entry and stated it as
current, even where a later ruling had replaced it.

THE RULINGS IT FOLLOWS (owner, 2026-11-10; recorded on cr1#1009)
  * A1 / A2: the now layer is a folder of ready answers rebuilt from the log,
    beside the live data files: `_hq/data/now/decisions/decisions.json`.
  * A7: no vector search. A topic matches a decision by its own words.
  * C3: the list is for display and lookup; recall goes back to the source
    entry through its id. Every row carries the decision's id (train 1's
    `dec_...`, or `decision_seq_<n>` for an older decision written with no
    id) and its entry number.

ONE READING OF A DECISION'S STATE
---------------------------------
Whether a decision still stands, and what replaced it, is read through the
decision log's own fold (`render_decision_log._categorize_decisions` and
`_decision_status`): the same supersede, resolve, reaffirm and snooze rules
the decision log view shows, never a second copy of them. A decision stands
when that fold says `active`, `reaffirmed` or `snoozed`.

"What it replaced" is the other side of the same link: a decision replaced by
a later one names that later one (the supersede's `new_seq`, or a restamp's
own entry), and the later one lists every decision it replaced.

READ AND WRITE
--------------
The log is read through `events_io.load_events_org_scoped` (the account mask
and the personal-lane drop), the entities file through `entities_io`. The one
file written is the list, whole, through `atomic_write`, and only by
`rebuild`. An answer reads the list; a missing list, or one the log or the
entities file has moved past, is counted from the log in memory and says so
(`counted_from_log`). Nothing an answer does writes.

THE MAINTENANCE JOB
-------------------
`decision-list` rebuilds the list once a day inside Maintenance, beside the
number ledgers (ruling A1), and writes one `pack_run` receipt, which never
makes the list stale (cr1#1073). Between rebuilds an answer still counts from
the log whenever the list is behind, so it is correct either way; the job
only makes it fast.

Command line, from the plugin root:
    python shared/scripts/decision_list.py <workspace> rebuild [--apply]
    python shared/scripts/decision_list.py <workspace> ask "<topic>" [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_json  # noqa: E402
from entities_io import entities_collection  # noqa: E402
from event_time import event_time  # noqa: E402
from events_io import load_events_org_scoped  # noqa: E402

LIST_DIR_REL = "_hq/data/now/decisions"
LIST_FILE = "decisions.json"
LIST_VERSION = 1
#: The Maintenance job that rebuilds the list (cr1#1073).
JOB_ID = "decision-list"

#: The decision log's own state words, by whether the decision still stands.
#: Read from the renderer so a sixth state is added in one place.
try:
    from render_decision_log import (  # noqa: E402
        CLOSED_DECISION_STATUSES, OPEN_DECISION_STATUSES)
except Exception:  # pragma: no cover: a reader never fails on an import
    CLOSED_DECISION_STATUSES = frozenset({"superseded", "resolved"})
    OPEN_DECISION_STATUSES = frozenset({"active", "reaffirmed", "snoozed"})

#: The fields a decision's own words are read from, for a topic match.
TEXT_KEYS = ("title", "summary", "decision", "rationale", "context", "impact",
             "reason", "topic")
WHAT_CHARS = 240
#: How many current rulings an answer names, newest first.
ANSWER_CAP = 10
_MONTHS = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")

STATUS_WORDS = {
    "active": "stands",
    "reaffirmed": "stands, confirmed again",
    "snoozed": "stands, set aside for a later look",
    "resolved": "carried out and closed",
    "superseded": "replaced",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _ws(workspace_root) -> Path:
    return Path(str(workspace_root))


def _list_path(ws: Path) -> Path:
    return ws / LIST_DIR_REL / LIST_FILE


def _entities(ws: Path) -> dict:
    """The entities file, or an empty one when it is missing or unreadable."""
    try:
        from entities_io import read_entities_json
        data = read_entities_json(ws / "_hq" / "data" / "entities.json")
    except (OSError, ValueError):
        return {}
    except Exception:  # noqa: BLE001: a reader never crashes on the register
        return {}
    return data if isinstance(data, dict) else {}


def _names(entities: dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for coll in ("people", "orgs", "threads"):
        try:
            recs = entities_collection(entities, coll)
        except Exception:  # noqa: BLE001
            recs = []
        for r in recs:
            if isinstance(r, dict) and r.get("id"):
                out[str(r["id"])] = str(r.get("canonical_name")
                                        or r.get("display_name") or "")
    return out


def _clip(text: str, n: int = WHAT_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= n else flat[: n - 1].rstrip() + "…"


def _the_date(value, ws: Path) -> str:
    """A decision's date in the workspace's own calendar, YYYY-MM-DD."""
    if not isinstance(value, str) or not value.strip():
        return ""
    v = value.strip()
    if len(v) == 10:
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            return ""
    try:
        from tz import localize_date
        return localize_date(v, workspace_path=str(ws)) or v[:10]
    except Exception:  # noqa: BLE001: a stripped install keeps the UTC date
        return v[:10]


def human_date(iso: str) -> str:
    """`2026-11-04` as `Nov 4, 2026`; anything else as it is."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return str(iso or "")
    return f"{_MONTHS[d.month]} {d.day}, {d.year}"


def _words(text: str) -> List[str]:
    """The search words of a text, split the way the log search splits them
    (A7: one keyword rule across the deep tier)."""
    from log_search import _tokens
    return _tokens(text)


# ---------------------------------------------------------------------------
# Building the list
# ---------------------------------------------------------------------------

#: An id-shaped value: never said to the owner, named from the register or
#: left out (the decision log view's `_resolve_name` rule).
_ID_SHAPE = re.compile(r"^(person|org|project|thread|objective|cmt|dec)[_-][A-Za-z0-9_-]+$")


def _who(ev: dict, names: Dict[str, str]) -> Dict[str, Any]:
    """Who made the call: the writer's own field, the alias chain the
    decision loader reads plus the view's `decided_by_id`. An id is named
    from the register and dropped when the register does not know it; plain
    words are kept as written. The people merely on the row (often a
    meeting's attendees) are never named as the decider."""
    from decision_match import _decision_field
    raw = _decision_field(ev, "decided_by")
    if raw in (None, "", []):
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        raw = d.get("decided_by_id") or ev.get("decided_by_id")
    vals = raw if isinstance(raw, list) else [raw]
    ids: List[str] = []
    words: List[str] = []
    for v in vals:
        if not isinstance(v, str) or not v.strip():
            continue
        v = v.strip()
        if v in names or _ID_SHAPE.match(v):
            ids.append(v)
        else:
            words.append(v)
    named = [names[i] for i in ids if names.get(i)]
    return {"ids": ids, "names": named, "text": ", ".join(named + words)}


def _thread(ev: dict, names: Dict[str, str]) -> Optional[Dict[str, Any]]:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    tid = ev.get("primary_thread_id") or d.get("project_id") or d.get("thread_id")
    if not isinstance(tid, str) or not tid.strip():
        return None
    return {"id": tid, "name": names.get(tid) or None}


def _text_of(ev: dict) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    parts = []
    for k in TEXT_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return "\n".join(parts)


def build_rows(events: list, entities: dict, ws: Path) -> List[dict]:
    """One row per decision, oldest first. Writes nothing."""
    from render_decision_log import (_categorize_decisions, _decision_id,
                                     _decision_status, _decision_title)
    names = _names(entities)
    # A hand-edited or legacy row whose `data` is not an object is read as
    # one with no fields, never a crash.
    events = [ev if isinstance(ev.get("data"), dict) or "data" not in ev
              else {**ev, "data": {}} for ev in events if isinstance(ev, dict)]
    overlays = _categorize_decisions(events)
    decisions = overlays["decisions"]
    rows: List[dict] = []
    by_seq: Dict[Any, dict] = {}
    for ev in decisions:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        status, overlay = _decision_status(ev, overlays)
        logged = _the_date(event_time(ev), ws)
        when = _the_date(d.get("decided_at"), ws) or logged
        row = {
            "id": _decision_id(ev),
            "seq": ev.get("seq"),
            "what": _clip(_decision_title(d)),
            "who": _who(ev, names),
            "when": when,
            "logged": logged,
            "thread": _thread(ev, names),
            "status": status,
            "stands": status in OPEN_DECISION_STATUSES,
            "replaces": [],
            "replaced_by": None,
            "source": {"skill": ev.get("source_skill"),
                       "ref": d.get("source_ref") or ev.get("source_ref")},
            "_text": _text_of(ev),
            "_new_seq": overlay.get("new_seq") if status == "superseded" else None,
        }
        imported = d.get("imported_from")
        if isinstance(imported, dict):
            row["source"]["imported_from"] = {
                k: imported[k] for k in ("file", "line", "ruling_date")
                if imported.get(k) not in (None, "")}
        rows.append(row)
        if _seq_key(ev.get("seq")) is not None:
            by_seq.setdefault(_seq_key(ev.get("seq")), row)
    # The other side of each replacement link.
    for row in rows:
        newer = by_seq.get(_seq_key(row.pop("_new_seq")))
        if newer is None or newer is row:
            continue
        row["replaced_by"] = _ref(newer)
        newer["replaces"].append(_ref(row))
    return rows


def _seq_key(value):
    """An entry number as an int, whether it was written as 7 or "7"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _ref(row: dict) -> dict:
    return {"id": row["id"], "seq": row["seq"], "what": row["what"],
            "when": row["when"]}


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# The stored list
# ---------------------------------------------------------------------------

def _compute(ws: Path) -> tuple:
    """(head, rows with their search text), from the log and the entities
    file. Writes nothing."""
    from ledgers import _entities_sha, _rows_sha
    events, skipped = load_events_org_scoped(ws)
    entities = _entities(ws)
    rows = build_rows(events, entities, ws)
    head = {"version": LIST_VERSION,
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "log_rows": len(events), "log_rows_sha256": _rows_sha(events),
            "entities_sha256": _entities_sha(ws),
            "skipped_lines": len(skipped)}
    return head, rows


def _counts(rows: List[dict]) -> Dict[str, int]:
    out = {"total": len(rows), "standing": sum(1 for r in rows if r["stands"])}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def rebuild(workspace_root) -> dict:
    """Rebuild the list whole and write it: the one writer of the file."""
    ws = _ws(workspace_root)
    head, rows = _compute(ws)
    doc = {**head, "counts": _counts(rows), "rows": rows}
    atomic_write_json(_list_path(ws), doc)
    return {"rows": len(rows), "standing": doc["counts"]["standing"],
            "log_rows": head["log_rows"], "skipped_lines": head["skipped_lines"]}


def list_is_current(workspace_root, doc) -> bool:
    """True when nothing that could change the list has landed since it was
    built: the same entities file, the same log rows (none rewritten in
    place), and only run receipts after them. The same rule the number
    ledgers keep (`ledgers.ledger_is_current`)."""
    from ledgers import IGNORED_TYPES, _entities_sha, _log_rows, _rows_sha
    ws = _ws(workspace_root)
    if not (isinstance(doc, dict) and isinstance(doc.get("rows"), list)
            and doc.get("version") == LIST_VERSION):
        return False
    try:
        built = int(doc.get("log_rows"))
    except (TypeError, ValueError):
        return False
    if built < 0 or doc.get("entities_sha256") != _entities_sha(ws):
        return False
    rows = _log_rows(ws)
    if len(rows) < built or doc.get("log_rows_sha256") != _rows_sha(rows[:built]):
        return False
    return all(isinstance(r, dict) and r.get("type") in IGNORED_TYPES
               for r in rows[built:])


def read_list(workspace_root) -> tuple:
    """(rows, counted_from_log). The stored list when it is current; else the
    list counted from the log in memory. Never writes."""
    ws = _ws(workspace_root)
    try:
        doc = json.loads(_list_path(ws).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if list_is_current(ws, doc):
        return doc["rows"], False
    _head, rows = _compute(ws)
    return rows, True


def decision_list(workspace_root, *, standing_only: bool = False,
                  thread: Optional[str] = None) -> Dict[str, Any]:
    """READ. The whole list, newest first: every decision with its id, what,
    who, when, thread, what it replaced and whether it still stands. Writes
    nothing."""
    rows, from_log = read_list(workspace_root)
    out = [_public(r) for r in rows
           if (not standing_only or r.get("stands"))
           and (not thread or ((r.get("thread") or {}).get("id") == thread))]
    out.sort(key=_newest_key, reverse=True)
    return {"ok": True, "rows": out, "counts": _counts(out),
            "counted_from_log": from_log}


# ---------------------------------------------------------------------------
# "What did we decide about X"
# ---------------------------------------------------------------------------

def _search_text(row: dict) -> str:
    text = row.get("_text")
    if text is None:  # a stored row: its own words are what it says
        text = row.get("what") or ""
    thread = (row.get("thread") or {}).get("name") or ""
    return f"{text}\n{row.get('what') or ''}\n{thread}"


def _newest_key(row: dict):
    seq = _seq_key(row.get("seq"))
    return (row.get("when") or "", -1 if seq is None else seq)


def what_did_we_decide(workspace_root, topic: str, *,
                       thread: Optional[str] = None) -> Dict[str, Any]:
    """READ. The rulings on a topic that stand now, newest first, each naming
    what it replaced; and the matching rulings that no longer stand, each
    naming what replaced it.

    A topic matches a decision by its own words, its title and its thread's
    name (A7). A matching decision that was replaced leads to the ruling that
    replaced it, following the chain to the one that stands, so an ask worded
    the old way still gets the current answer. `lines` are the one-line
    summaries the decision-log skill hands its composer. Writes nothing."""
    terms = _words(topic or "")
    if not terms:
        return {"ok": True, "topic": topic, "current": [], "replaced": [],
                "lines": [], "counted_from_log": False,
                "reason": "no words to look for" if (topic or "").strip() else "no topic"}
    rows, from_log = read_list(workspace_root)
    # Keyed by position: two older decisions can share a name (no entry
    # number, or a repeated id), and each is its own ruling.
    by_id: Dict[str, int] = {}
    by_seq: Dict[int, int] = {}
    for i, r in enumerate(rows):
        by_id.setdefault(r["id"], i)
        if _seq_key(r.get("seq")) is not None:
            by_seq.setdefault(_seq_key(r.get("seq")), i)

    def _at(ref) -> Optional[int]:
        """Where a replacement link points: by its entry number, else its id."""
        if not isinstance(ref, dict):
            return None
        seq = _seq_key(ref.get("seq"))
        if seq is not None and seq in by_seq:
            return by_seq[seq]
        return by_id.get(ref.get("id"))
    need = max(1, math.ceil(len(terms) / 2))
    phrase = " ".join(terms)
    matched: Dict[int, int] = {}
    for i, r in enumerate(rows):
        if thread and (r.get("thread") or {}).get("id") != thread:
            continue
        toks_list = _words(_search_text(r))
        toks = set(toks_list)
        score = sum(1 for t in terms if t in toks)
        if score < need:
            continue
        if len(terms) > 1 and phrase in " ".join(toks_list):
            score += len(terms)
        matched[i] = score
    current: Dict[int, dict] = {}
    replaced: Dict[int, dict] = {}
    for pos, score in matched.items():
        r = rows[pos]
        if r.get("stands"):
            current.setdefault(pos, {**_public(r), "score": score})
            continue
        replaced.setdefault(pos, {**_public(r), "score": score})
        # Follow what replaced it to the ruling that stands now.
        seen = {pos}
        at = _at(r.get("replaced_by"))
        while at is not None and at not in seen:
            seen.add(at)
            later = rows[at]
            if later.get("stands"):
                cur = current.setdefault(at, {**_public(later), "score": 0})
                cur.setdefault("reached_from", []).append(_ref(r))
                break
            replaced.setdefault(at, {**_public(later), "score": 0})
            at = _at(later.get("replaced_by"))
    cur_rows = sorted(current.values(), key=_newest_key, reverse=True)
    old_rows = sorted(replaced.values(), key=_newest_key, reverse=True)
    out = {"ok": True, "topic": topic, "current": cur_rows[:ANSWER_CAP],
           "n_current": len(cur_rows), "replaced": old_rows,
           "counted_from_log": from_log}
    out["lines"] = answer_lines(out)
    return out


def _decision_words(row: dict) -> str:
    bits = [f"decided {human_date(row['when'])}" if row.get("when") else "date not on file"]
    who = (row.get("who") or {}).get("text")
    if who:
        bits.append(f"by {who}")
    tname = (row.get("thread") or {}).get("name")
    if tname:
        bits.append(f"on {tname}")
    return ", ".join(bits)


def answer_lines(ans: Dict[str, Any]) -> List[str]:
    """One plain line per ruling that stands, naming what it replaced; and one
    per matching ruling that was replaced or closed with nothing standing in
    its place. No ids, paths or field names: these are said to the owner."""
    lines: List[str] = []
    for r in ans.get("current") or []:
        line = f"{r['what'].rstrip('.')} ({_decision_words(r)})."
        older = r.get("replaces") or []
        if older:
            line += " It replaced " + "; ".join(
                f"{o['what'].rstrip('.')} ({human_date(o['when'])})" for o in older) + "."
        lines.append(line)
    standing_ids = {r["id"] for r in ans.get("current") or []}
    for r in ans.get("replaced") or []:
        nxt = r.get("replaced_by") or {}
        if nxt.get("id") in standing_ids:
            continue  # already named on the line of the ruling that replaced it
        if r.get("status") == "resolved":
            lines.append(f"{r['what'].rstrip('.')} ({_decision_words(r)}): carried out and closed.")
        elif nxt:
            lines.append(f"{r['what'].rstrip('.')} ({_decision_words(r)}): replaced by "
                         f"{nxt['what'].rstrip('.')} ({human_date(nxt['when'])}).")
        else:
            lines.append(f"{r['what'].rstrip('.')} ({_decision_words(r)}): replaced; "
                         "the later decision is not named on file.")
    return lines


def render(ans: Dict[str, Any]) -> str:
    lines = ans.get("lines") or []
    if not lines:
        return f"No decision on file about {ans.get('topic') or 'that'}."
    return "\n".join(f"- {ln}" for ln in lines)


def run_decision_list_job(ws, *, apply: bool = False, fired_via=None) -> dict:
    """The `decision-list` Maintenance job. `apply=False` is the dry run: the
    rows are counted and nothing is written. An applying run writes the list
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
        _head, rows = _compute(ws)
        return {"ran": True, "applied": False, "refused": None,
                "rows": len(rows)}
    out = rebuild(ws)
    duration = int((_time.perf_counter() - t0) * 1000)
    from receipts import log_receipt
    log_receipt(ws, JOB_ID, receipt_type="pack_run", fired_via=via,
                surfaced=0, duration_ms=duration,
                extra_data={"rows": out["rows"]})
    return {"ran": True, "applied": True, "refused": None,
            "duration_ms": duration, **out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The decision list.")
    ap.add_argument("workspace")
    ap.add_argument("command", choices=("rebuild", "ask", "list"))
    ap.add_argument("topic", nargs="?", default="")
    ap.add_argument("--apply", action="store_true",
                    help="rebuild: write the list (default: a dry run)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fired-via", default=None,
                    help="rebuild: the fire provenance, carried into the receipt")
    ap.add_argument("--triggered-by", default=None,
                    help="rebuild: the surface that ran the job, carried into the receipt")
    a = ap.parse_args(argv)
    ws = Path(a.workspace)
    if a.command == "rebuild":
        # The dispatcher appends `--fired-via X --triggered-by Y`; the
        # receipt reads the trigger from the environment, as ledgers.py does.
        if a.triggered_by:
            import os
            os.environ["CR_TRIGGERED_BY"] = str(a.triggered_by)
        out = run_decision_list_job(ws, apply=a.apply, fired_via=a.fired_via)
        print(json.dumps(out))  # one line: the dispatcher reads the last one
        return 0 if out.get("ran") else 1
    if a.command == "list":
        print(json.dumps(decision_list(ws), indent=2))
        return 0
    ans = what_did_we_decide(ws, a.topic)
    print(json.dumps(ans, indent=2) if a.json else render(ans))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
