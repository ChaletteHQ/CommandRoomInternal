#!/usr/bin/env python3
"""Ledgers for numbers: a sessions ledger and a pipeline ledger, rebuilt whole
from the log into the now folder, and the two answers that read them
(cr1#1018, train 9 of the memory and Hub programme).

WHY THIS EXISTS
---------------
Two counts could not be answered before this module: sessions by client, and
the pipeline by stage. Every reader rebuilt them from the whole log by hand,
so two readers gave two counts. Ruling A1 of 2026-11-10: the now layer is a
folder of ready answers rebuilt every run, and the log stays the record.
Ruling A2: the live data files stay where they are and new folders go beside
them, so the ledgers live in `_hq/data/now/ledgers/`.

THE TWO LEDGERS
---------------
  sessions.json  one row per `meeting` event, deduplicated on its source
                 reference (the newest sequence wins, so a reprocessed meeting
                 counts once): date (local to the workspace), client (org id
                 and name), kind (the meeting type, normalised the way prep
                 grading normalises it; "untyped" when there is none), who
                 (attendee ids and names) and the source (event id, sequence,
                 source reference).
  pipeline.json  one row per open deal thread: stage (the hand-typed stage on
                 the deal, kept for the pipeline by ruling A4), date entered
                 (`stage_entered`), last touch (worked out from activity by
                 `thread_activity`, never the stored field) and the source
                 thread. A deal thread with no deal object is listed with no
                 stage.

Which org a meeting belongs to is worked out in a fixed order and never
guessed: the meeting's own org ids, then its thread's org, then the one org
every attendee with an org shares. The owner's own orgs never count: every
org whose relationship type is `self` (the idiom capture_gate, end_of_day and
binding_gauge key on) or `operating`, and the primary user's own org.
Anything left over has no client and is counted as such. One meeting is one
row however its reference is spelled (`meeting_capture.meeting_ref_keys`).

NO MONEY
--------
Nothing here reads a deal's value, currency or forecast into a ledger or an
answer (no money on the Hub, programme ruling). Rows are built field by field;
the deal object is never copied.

READ AND WRITE
--------------
The log is read through `events_io.load_events_org_scoped` (the account mask
and the personal-lane drop), the entities file through `entities_io`. The
only files written are the two ledgers, whole, through `atomic_write`, and
only by `rebuild` (the Maintenance run, ruling A1). The log and the entities
file are never written. An answer reads its ledger. A missing ledger, or one
the log or the entities file has moved past, is never rewritten by an
answer: the answer is counted from the log in memory and says so
(`counted_from_log`). Receipt rows (`IGNORED_TYPES`) do not make a ledger
stale, so the run's own receipt never does.

Command line, from the plugin root:
    python shared/scripts/ledgers.py <workspace> rebuild [--apply]
    python shared/scripts/ledgers.py <workspace> sessions [--from D --to D] [--json]
    python shared/scripts/ledgers.py <workspace> pipeline [--json]
Both answers take `--today YYYY-MM-DD` (the default is today in the
workspace's timezone).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_json  # noqa: E402
from entities_io import entities_collection, read_entities_json  # noqa: E402
from event_refs import email_person_index, meeting_person_ids  # noqa: E402
from events_io import load_events_org_scoped  # noqa: E402
from meeting_capture import meeting_ref_keys  # noqa: E402
from prep_grading import normalize_meeting_type  # noqa: E402
from thread_activity import derive_from_events  # noqa: E402

LEDGER_DIR_REL = "_hq/data/now/ledgers"
SESSIONS_FILE = "sessions.json"
PIPELINE_FILE = "pipeline.json"
LEDGER_VERSION = 1
#: The daily Maintenance job that rebuilds both ledgers (slice 2).
JOB_ID = "number-ledgers"

#: The owner's own orgs are never a client.
OWN_RELATIONSHIPS = frozenset({"self", "operating"})
#: Rows that never change a ledger: run receipts, as `receipts` registers
#: them. A newer row of one of these types does not make a ledger stale.
try:
    from receipts import ALL_RECEIPT_TYPES as IGNORED_TYPES  # noqa: E402
except Exception:  # pragma: no cover: a reader never fails on an import
    IGNORED_TYPES = frozenset({"pack_run"})
NO_TZ_NOTE = ("No workspace timezone is set, so dates are counted in UTC "
              "and a meeting near midnight can fall on the next day.")
CLIENT_RELATIONSHIP = "client"
UNTYPED = "untyped"
#: What an answer says for an org named on a meeting but not on file: the id
#: never reaches anything the owner reads.
UNKNOWN_ORG = "an organisation not on file"
#: Plain words for the kind of row that last touched a deal.
TOUCH_WORDS = {"meeting": "a meeting", "commitment": "a promise",
               "decision": "a decision", "interaction": "a message"}

#: Pipeline order. Mirrors `thread_writer.DEAL_STAGES` and the display labels
#: in `deal_state.STAGE_DISPLAY`; imported, not copied, below.
try:
    from deal_state import STAGE_DISPLAY  # noqa: E402
    from thread_writer import DEAL_OUTCOMES, DEAL_STAGES  # noqa: E402
except Exception:  # pragma: no cover: a reader never fails on an import
    DEAL_STAGES = ("lead", "qualified", "proposal_sent", "negotiating")
    DEAL_OUTCOMES = ("won", "lost")
    STAGE_DISPLAY = {"lead": "Lead", "qualified": "Qualified",
                     "proposal_sent": "Proposal sent",
                     "negotiating": "Negotiating"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _ledger_dir(ws: Path) -> Path:
    return Path(ws) / LEDGER_DIR_REL


def _workspace_tz(ws: Path):
    """(tzinfo, note). An unset workspace timezone is never silent: the
    note travels into the ledger and every answer. With no entities file
    `tz` would go looking in `CR_WORKSPACE`, another workspace: not here."""
    if not (Path(ws) / "_hq" / "data" / "entities.json").is_file():
        return timezone.utc, NO_TZ_NOTE
    try:
        from tz import load_workspace_tz
        return load_workspace_tz(ws), None
    except Exception:  # noqa: BLE001: TZResolutionError and friends
        return timezone.utc, NO_TZ_NOTE


def _parse_ts(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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


def last_month_window(today: date) -> tuple[date, date]:
    """The calendar month before `today`, first and last day inclusive."""
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    return last_prev.replace(day=1), last_prev


def _today(ws: Path, today=None) -> date:
    d = _as_date(today)
    if d is not None:
        return d
    return datetime.now(_workspace_tz(ws)[0]).date()


def _name(rec: dict) -> str:
    return str(rec.get("canonical_name") or rec.get("display_name")
               or rec.get("id") or "")


def _index(entities: dict) -> dict:
    orgs = {o["id"]: o for o in entities_collection(entities, "orgs")
            if isinstance(o, dict) and o.get("id")}
    people = {p["id"]: p for p in entities_collection(entities, "people")
              if isinstance(p, dict) and p.get("id")}
    threads = {t["id"]: t for t in entities_collection(entities, "threads")
               if isinstance(t, dict) and t.get("id")}
    own = {oid for oid, o in orgs.items()
           if str(o.get("relationship_type") or "").strip().lower()
           in OWN_RELATIONSHIPS}
    try:
        from primary_user import resolve_primary_user_from_entities
        me = resolve_primary_user_from_entities(entities)
    except Exception:  # noqa: BLE001: no primary user, no extra own org
        me = None
    own |= _person_orgs(people.get(me or ""))
    return {"orgs": orgs, "people": people, "threads": threads, "own": own}


def _thread_org(thread: Optional[dict]) -> Optional[str]:
    if not isinstance(thread, dict):
        return None
    return thread.get("affiliation_id") or thread.get("org_id") or None


def _person_orgs(person: Optional[dict]) -> set:
    if not isinstance(person, dict):
        return set()
    out = set()
    for key in ("primary_org_id", "org_id"):
        if person.get(key):
            out.add(person[key])
    if not out:
        for oid in person.get("affiliation_ids") or []:
            if oid:
                out.add(oid)
    return out


def _meeting_org(ev: dict, idx: dict, person_ids: Iterable[str]
                 ) -> tuple[Optional[str], str]:
    """(org_id, how) for one meeting, in the fixed order; (None, why) when
    no single org can be named."""
    own = idx["own"]
    named = [o for o in (ev.get("org_ids") or []) if o and o not in own]
    if len(set(named)) == 1:
        return named[0], "meeting"
    thread = idx["threads"].get(ev.get("primary_thread_id") or "")
    t_org = _thread_org(thread)
    if t_org and t_org not in own:
        return t_org, "thread"
    orgs = set()
    for pid in person_ids:
        orgs |= _person_orgs(idx["people"].get(pid))
    orgs -= own
    if len(orgs) == 1:
        return next(iter(orgs)), "attendees"
    if len(orgs) > 1 or len(set(named)) > 1:
        return None, "more_than_one_org"
    return None, "no_org"


def _ref_key(ref) -> str:
    """One key per meeting however its reference is spelled: the shortest of
    `meeting_capture.meeting_ref_keys` (so `granola:<id>` and `<id>` agree)."""
    keys = meeting_ref_keys(ref)
    return min(keys, key=lambda k: (len(k), k)) if keys else ""


def _kind(ev: dict) -> str:
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    raw = data.get("meeting_type")
    if not str(raw or "").strip():
        return UNTYPED
    return normalize_meeting_type(raw)


# ---------------------------------------------------------------------------
# Building the ledgers (pure over loaded inputs)
# ---------------------------------------------------------------------------

def build_sessions(events: list, entities: dict, tzinfo) -> list[dict]:
    """One row per meeting, deduplicated on its source reference; the newest
    sequence wins. Pure."""
    idx = _index(entities)
    emails = email_person_index(entities)
    newest: dict = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "meeting":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        key = _ref_key(data.get("source_ref")) or str(ev.get("id")
                                                       or ev.get("seq"))
        seq = ev.get("seq") if isinstance(ev.get("seq"), int) else -1
        held = newest.get(key)
        if held is None or seq >= (held.get("seq") if isinstance(
                held.get("seq"), int) else -1):
            newest[key] = ev
    rows = []
    for key, ev in newest.items():
        dt = _parse_ts(ev.get("ts"))
        if dt is None:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        pids = sorted(meeting_person_ids(ev, emails))
        org_id, how = _meeting_org(ev, idx, pids)
        org = idx["orgs"].get(org_id or "") or {}
        rows.append({
            "date": dt.astimezone(tzinfo).date().isoformat(),
            "org_id": org_id,
            "org_name": _name(org) if org_id else None,
            "client": org.get("relationship_type") == CLIENT_RELATIONSHIP,
            "org_from": how,
            "kind": _kind(ev),
            "who": [{"id": pid, "name": _name(idx["people"].get(pid) or
                                              {"id": pid})}
                    for pid in pids],
            "title": str(data.get("title") or ""),
            "thread_id": ev.get("primary_thread_id"),
            "source": {"event_id": ev.get("id"), "seq": ev.get("seq"),
                       "source_ref": str(data.get("source_ref") or key)},
        })
    rows.sort(key=lambda r: (r["date"], r["source"]["seq"]
                             if isinstance(r["source"]["seq"], int) else -1))
    return rows


def build_pipeline(entities: dict, activity: dict) -> list[dict]:
    """One row per open deal thread. Pure. The deal object is read field by
    field; nothing about money is carried."""
    idx = _index(entities)
    rows = []
    for t in idx["threads"].values():
        if t.get("kind") != "deal" or t.get("status") in ("archived",
                                                          "resolved"):
            continue
        deal = t.get("deal") if isinstance(t.get("deal"), dict) else None
        if deal and deal.get("outcome") in DEAL_OUTCOMES:
            continue
        stage = (deal or {}).get("stage")
        if stage not in DEAL_STAGES:
            stage = None
        act = activity.get(t["id"])
        org_id = _thread_org(t)
        org = idx["orgs"].get(org_id or "") or {}
        entered = _as_date((deal or {}).get("stage_entered"))
        rows.append({
            "thread_id": t["id"],
            "name": _name(t),
            "org_id": org_id,
            "org_name": _name(org) if org else None,
            "stage": stage,
            "stage_label": STAGE_DISPLAY.get(stage) if stage else None,
            "date_entered": entered.isoformat() if entered else None,
            "last_touch": act.ts.date().isoformat() if act else None,
            "source": {"thread_id": t["id"],
                       "last_touch_seq": act.seq if act else None,
                       "last_touch_type": act.event_type if act else None},
        })
    rows.sort(key=lambda r: (r["name"], r["thread_id"]))
    return rows


# ---------------------------------------------------------------------------
# Rebuild and read
# ---------------------------------------------------------------------------

def _entities(ws: Path) -> dict:
    """The entities file, or an empty one when it is missing or unreadable
    (a fresh workspace), never a crash."""
    try:
        data = read_entities_json(Path(ws) / "_hq" / "data" / "entities.json")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _entities_sha(ws: Path) -> str:
    try:
        return hashlib.sha256(
            (Path(ws) / "_hq" / "data" / "entities.json").read_bytes()
        ).hexdigest()
    except OSError:
        return ""


def _log_rows(ws: Path) -> list:
    """Every row the ledgers are built from, in order: the org-scoped read,
    the same one the build uses, so the personal lane is never read raw here
    and a personal row cannot make a ledger stale. Used to tell whether a
    ledger is behind; counting is by position, so a row with no sequence
    number is seen too."""
    try:
        return load_events_org_scoped(ws)[0]
    except Exception:  # noqa: BLE001: an unreadable log proves nothing
        return []


def _rows_sha(rows: list) -> str:
    """A fingerprint of log rows, so a row rewritten in place (main's meeting
    binding backfill does this on purpose) is seen, not only rows added."""
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r, sort_keys=True, ensure_ascii=False,
                            default=str).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _compute(ws: Path) -> tuple[dict, list, list]:
    """(head, sessions, pipeline), computed from the log and the entities
    file. Writes nothing."""
    ws = Path(ws)
    ent_sha = _entities_sha(ws)
    events, skipped = load_events_org_scoped(ws)
    # The fingerprint is of exactly the rows the ledgers are built from.
    log_rows, log_sha = len(events), _rows_sha(events)
    entities = _entities(ws)
    tzinfo, tz_note = _workspace_tz(ws)
    sessions = build_sessions(events, entities, tzinfo)
    activity = derive_from_events(events, honor_reclassifications=True)
    pipeline = build_pipeline(entities, activity)
    head = {"version": LEDGER_VERSION,
            "built_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "log_rows": log_rows, "log_rows_sha256": log_sha,
            "entities_sha256": ent_sha,
            "skipped_lines": len(skipped), "timezone_note": tz_note}
    return head, sessions, pipeline


def rebuild(ws, today=None) -> dict:
    """Rebuild both ledgers whole and write them: the one writer of the two
    files. Returns the row counts. `today` is accepted for symmetry with the
    answers; nothing in a ledger depends on it."""
    ws = Path(ws)
    head, sessions, pipeline = _compute(ws)
    d = _ledger_dir(ws)
    atomic_write_json(d / SESSIONS_FILE, {**head, "rows": sessions})
    atomic_write_json(d / PIPELINE_FILE, {**head, "rows": pipeline})
    return {"sessions": len(sessions), "pipeline": len(pipeline),
            "log_rows": head["log_rows"],
            "skipped_lines": head["skipped_lines"]}


def ledger_is_current(ws, doc) -> bool:
    """True when nothing that could change the ledger has landed since it
    was built: the entities file is the same, the log rows it read are the
    same rows (none rewritten in place), and every log row after them is a
    run receipt."""
    ws = Path(ws)
    if not (isinstance(doc, dict) and isinstance(doc.get("rows"), list)
            and doc.get("version") == LEDGER_VERSION):
        return False
    try:
        built_rows = int(doc.get("log_rows"))
    except (TypeError, ValueError):
        return False
    if built_rows < 0 or doc.get("entities_sha256") != _entities_sha(ws):
        return False
    rows = _log_rows(ws)
    if len(rows) < built_rows:
        return False  # the log is shorter than the one it was built from
    if doc.get("log_rows_sha256") != _rows_sha(rows[:built_rows]):
        return False  # a row it read was rewritten in place
    return all(isinstance(r, dict) and r.get("type") in IGNORED_TYPES
               for r in rows[built_rows:])


def _read_or_count(ws: Path, name: str) -> tuple[dict, bool]:
    """(ledger, counted_from_log). A current ledger file is read as is; a
    missing or stale one is counted from the log in memory, and nothing is
    written."""
    path = _ledger_dir(ws) / name
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if ledger_is_current(ws, doc):
        return doc, False
    head, sessions, pipeline = _compute(ws)
    rows = sessions if name == SESSIONS_FILE else pipeline
    return {**head, "rows": rows}, True


def run_ledgers_job(ws, *, apply: bool = False, fired_via=None) -> dict:
    """The `number-ledgers` Maintenance job. `apply=False` is the dry run:
    the counts are computed and nothing is written. An applying run writes
    both ledgers and one `pack_run` receipt under the job's own id; the
    receipt is a registered receipt type, so it never makes the ledger it
    follows look stale. Never raises for a refusal: the return says why."""
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
        _head, sessions, pipeline = _compute(ws)
        return {"ran": True, "applied": False, "refused": None,
                "sessions": len(sessions), "pipeline": len(pipeline)}
    out = rebuild(ws)
    duration = int((_time.perf_counter() - t0) * 1000)
    from receipts import log_receipt
    log_receipt(ws, JOB_ID, receipt_type="pack_run", fired_via=via,
                surfaced=0, duration_ms=duration,
                extra_data={"sessions": out["sessions"],
                            "pipeline": out["pipeline"],
                            "skipped_lines": out["skipped_lines"]})
    return {"ran": True, "applied": True, "refused": None,
            "duration_ms": duration, **out}


# ---------------------------------------------------------------------------
# The answers
# ---------------------------------------------------------------------------

def sessions_by_client(workspace_root, start=None, end=None,
                       today=None) -> dict:
    """Sessions per client for a window (default: last calendar month in the
    workspace's timezone), every row with its source, and the answer in
    plain words under `text`. Writes nothing (it is on the door's read
    list, whose fill names `workspace_root`)."""
    ws = Path(workspace_root)
    doc, from_log = _read_or_count(ws, SESSIONS_FILE)
    s, e = _as_date(start), _as_date(end)
    if s is None or e is None:
        ls, le = last_month_window(_today(ws, today))
        s, e = s or ls, e or le
    rows = [r for r in doc["rows"]
            if s.isoformat() <= str(r.get("date") or "") <= e.isoformat()]
    idx = _index(_entities(ws))

    groups: dict = {}
    no_client = []
    for r in rows:
        if not r.get("org_id"):
            no_client.append(r)
            continue
        groups.setdefault(r["org_id"], []).append(r)

    def _group(org_id: str, grp: list) -> dict:
        by_kind: dict = {}
        for r in grp:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        org = idx["orgs"].get(org_id) or {}
        return {"org_id": org_id,
                "org_name": _name(org) if org else (
                    (grp[0].get("org_name") if grp else None)
                    or UNKNOWN_ORG),
                "count": len(grp), "by_kind": by_kind, "rows": grp}

    client_ids = {oid for oid, o in idx["orgs"].items()
                  if o.get("relationship_type") == CLIENT_RELATIONSHIP
                  and oid not in idx["own"]}
    clients = [_group(oid, groups.get(oid, [])) for oid in client_ids]
    others = [_group(oid, grp) for oid, grp in groups.items()
              if oid not in client_ids]
    order = (lambda g: (-g["count"], str(g["org_name"]).lower()))
    clients.sort(key=order)
    others.sort(key=order)
    ans = {
        "window": {"from": s.isoformat(), "to": e.isoformat()},
        "built_at": doc.get("built_at"),
        "counted_from_log": from_log,
        "timezone_note": doc.get("timezone_note"),
        "clients": clients,
        "other_orgs": others,
        "no_client": len(no_client),
        "no_client_rows": no_client,
        "untyped": sum(1 for r in rows if r.get("kind") == UNTYPED),
        "total": len(rows),
    }
    ans["text"] = render_sessions(ans)
    return ans


def pipeline_by_stage(workspace_root, today=None) -> dict:
    """Open deals per stage, in pipeline order, with date entered and last
    touch, every row with its source, and the answer in plain words under
    `text`. No money. Writes nothing (it is on the door's read list)."""
    ws = Path(workspace_root)
    doc, from_log = _read_or_count(ws, PIPELINE_FILE)
    t = _today(ws, today)
    rows = []
    for r in doc["rows"]:
        r = dict(r)
        lt = _as_date(r.get("last_touch"))
        r["days_since_touch"] = (t - lt).days if lt else None
        rows.append(r)
    stages = [{"stage": st, "label": STAGE_DISPLAY.get(st, st),
               "count": sum(1 for r in rows if r.get("stage") == st),
               "rows": [r for r in rows if r.get("stage") == st]}
              for st in DEAL_STAGES]
    untracked = [r for r in rows if not r.get("stage")]
    ans = {"built_at": doc.get("built_at"),
           "counted_from_log": from_log,
           "timezone_note": doc.get("timezone_note"),
           "today": t.isoformat(),
           "stages": stages, "untracked": untracked,
           "total": sum(s["count"] for s in stages)}
    ans["text"] = render_pipeline(ans)
    return ans


# ---------------------------------------------------------------------------
# Plain words
# ---------------------------------------------------------------------------

def _kind_words(kind: str) -> str:
    return str(kind).replace("_", " ")


def _built_line(ans: dict) -> str:
    if ans.get("counted_from_log"):
        line = ("Counted just now from the log, because the ledger was "
                "missing or older than the log; Maintenance rebuilds it on "
                "its next run.")
    else:
        line = f"From the ledger built at {ans.get('built_at')}."
    if ans.get("timezone_note"):
        line += " " + ans["timezone_note"]
    return line


def render_sessions(ans: dict) -> str:
    w = ans.get("window") or {}
    lines = [f"Sessions by client, {w.get('from')} to {w.get('to')}. "
             + _built_line(ans), ""]
    for g in ans.get("clients", []):
        kinds = ", ".join(f"{_kind_words(k)} {n}" for k, n in
                          sorted(g["by_kind"].items(), key=lambda kv: -kv[1]))
        lines.append(f"- {g['org_name']}: {g['count']}"
                     + (f" ({kinds})" if kinds else ""))
    if ans.get("other_orgs"):
        lines.append("")
        lines.append("Other organisations: " + "; ".join(
            f"{g['org_name']}: {g['count']}" for g in ans["other_orgs"]))
    lines.append("")
    lines.append(f"No client: {ans.get('no_client', 0)}. "
                 f"No meeting type: {ans.get('untyped', 0)}. "
                 f"Total meetings in the window: {ans.get('total', 0)}.")
    lines.append("")
    # Sources in the owner's words: the date, the client and the meeting's
    # title as recorded. The ids stay in the structured rows (`source`).
    lines.append("Sources:")
    groups = list(ans.get("clients", [])) + list(ans.get("other_orgs", []))
    for g in groups:
        for r in g["rows"]:
            lines.append(f"- {r['date']}, {g['org_name']}: "
                         f"{r.get('title') or 'untitled meeting'}")
    for r in ans.get("no_client_rows", []):
        lines.append(f"- {r['date']}, no client: "
                     f"{r.get('title') or 'untitled meeting'}")
    return "\n".join(lines)


def render_pipeline(ans: dict) -> str:
    lines = ["Pipeline by stage, open deals, stage as typed on each deal. "
             + _built_line(ans), ""]
    for s in ans.get("stages", []):
        lines.append(f"- {s['label']}: {s['count']}")
        for r in s["rows"]:
            touch = (f"last touch {r['last_touch']}, "
                     f"{r['days_since_touch']} days ago"
                     if r.get("last_touch") else "no touch on file")
            org = f" ({r['org_name']})" if r.get("org_name") else ""
            lines.append(f"  - {r['name']}{org}: in this stage since "
                         f"{r.get('date_entered') or 'an unknown date'}, "
                         f"{touch}")
    if ans.get("untracked"):
        lines.append("")
        lines.append(f"Deal threads with no stage: {len(ans['untracked'])} ("
                     + "; ".join(r["name"] for r in ans["untracked"]) + ")")
    lines.append("")
    # Sources in the owner's words: the deal record and what last touched
    # it. The ids stay in the structured rows (`source`).
    lines.append("Sources:")
    for s in ans.get("stages", []):
        for r in s["rows"]:
            what = TOUCH_WORDS.get((r.get("source") or {})
                                   .get("last_touch_type"), "activity")
            touch = (f"last touch from {what} on {r['last_touch']}"
                     if r.get("last_touch") else "no activity on file")
            lines.append(f"- {r['name']}: stage as typed on the deal; "
                         f"{touch}")
    for r in ans.get("untracked", []):
        lines.append(f"- {r['name']}: a deal with no stage typed on it")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sessions and pipeline ledgers.")
    ap.add_argument("workspace")
    ap.add_argument("command", choices=("rebuild", "sessions", "pipeline"))
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--today")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="rebuild: write the ledgers and the receipt "
                         "(default: a dry run that writes nothing)")
    ap.add_argument("--fired-via", default=None,
                    help="rebuild: the fire provenance (the seat decides "
                         "when nothing is said)")
    ap.add_argument("--triggered-by", default=None,
                    help="rebuild: the surface that asked for this run")
    a = ap.parse_args(argv)
    ws = Path(a.workspace)
    if a.command == "rebuild":
        if a.triggered_by:
            import os
            os.environ["CR_TRIGGERED_BY"] = str(a.triggered_by)
        out = run_ledgers_job(ws, apply=a.apply, fired_via=a.fired_via)
        print(json.dumps(out))
        return 0 if not out.get("refused") else 1
    if a.command == "sessions":
        ans = sessions_by_client(ws, a.start, a.end, today=a.today)
        print(json.dumps(ans, indent=2) if a.json else render_sessions(ans))
        return 0
    ans = pipeline_by_stage(ws, today=a.today)
    print(json.dumps(ans, indent=2) if a.json else render_pipeline(ans))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
