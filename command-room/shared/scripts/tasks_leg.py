#!/usr/bin/env python3
"""
The tasks leg (cr1#668, lane 6 of cr1#609): a declared `tasks` connector
(a task tool: boards of items with an assignee, a creator and a due date)
opens and closes commitments on every Maintenance run, as the owner ruled
on 2026-11-03.

TWO JOBS, ONE MODULE. Captures run before closes (cr1#663, guard G83), so
the leg is two registry rows: `tasks-capture` beside the other capture legs
and `tasks-close` beside the other close legs. Both read the one maintenance
window (`catchup.maintenance_window`: since the job's own last receipt; 30
days on a first run) and both call `connector_reads.for_job` BEFORE the
source, so a dial the owner turned off is a skip on the receipt and never a
read (cr1#667, guard G84).

THE CAPTURE HALF (`capture_tasks_and_receipt`), the ruled capture rule
(`connector_adapters/tasks.capture_class`):

  * an item assigned to the owner, with a due date, becomes the owner's own
    row (a promise: `kind` promise with its creator as counterparty, or
    `task` when nobody else is on it);
  * an item the owner created or assigned to someone else, with a due date,
    becomes a waiting-on (owned by the assignee, the owner as counterparty);
  * an item with no due date, or with no relation to the owner, is people
    and project signal only, never a commitment;
  * a board on the exclude list (or off a non-empty include list) is not
    read at all, and under a non-empty include list neither is an item with
    no board name or id (cr1#759).

Each capture runs THE shared capture block (`capture_gate.
gate_commitment_data`), the restatement dedup against the open set
(`matches_open_commitment`) and the relevance gate (`classify_capture`),
the same gate and receipt shape as the mail and meeting captures. No new
commitment kind.

THE CLOSE HALF (`close_tasks_and_receipt`), the ruled close mode: a done
item on a board the owner declared as project tracking (`close_mode:
close` on its connector) closes the matched commitment outright, with the
board item as the source pointer; a done item on any other included board
proposes the close for the owner to confirm. The match is first the item
itself (a row this leg captured from the same item, by pointer identity),
then the EXISTING matchers (`reconcile_sent` for an item the owner did,
`reconcile_inbound` for one somebody else did) at their own thresholds.
No new matching engine, no threshold moved.

PEOPLE AND PROJECTS. Every item the leg reads on an included board is also
people and project signal, read under the `update_people` and
`update_projects` dials: the receipt names the people and the boards it
saw, and says why when a dial turned a kind away. What it saw goes to the
identity and lifecycle paths (`hand_off_seen`, cr1#738) with the item as the
source pointer. The daily people and project jobs (cr1#665) run after this
leg in the fire, so they judge it there the same fire, or in their next window
when it landed after they ran. Their window rule is unchanged: activity dated
before a daily window is the Sunday pass's, however late it was read.

Pure-ish: no network I/O. The fire's connector step reads the items and
hands dicts in; everything after that happens here.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connector_adapters import tasks as _tasks  # noqa: E402

KIND = "tasks"
JOB_CAPTURE = "tasks-capture"
JOB_CLOSE = "tasks-close"
RESOLVED_BY = "tasks_reconcile"

#: What ONE capture fire may write; the rest is counted and left for the
#: next fire (the inbound capture's cap, adopted rather than re-invented).
DEFAULT_CAPTURE_CAP = 12

#: Evidence word on a close or a proposal made by the item itself.
_DONE_EVIDENCE = "marked done on the board"


class TasksLegError(RuntimeError):
    """The leg could not run in a way that would leave an honest record."""


def _s(v) -> str:
    return str(v if v is not None else "").strip()


def _effective_fired_via(explicit):
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _records(workspace_root, purpose: str, env=None) -> List[Dict[str, Any]]:
    """The declared tasks connectors and, for `purpose`, their skips: the
    reader every leg calls before it reads a source (cr1#667)."""
    from connector_reads import for_job
    try:
        return for_job(workspace_root, KIND, purpose=purpose, env=env)
    except Exception:  # noqa: BLE001 - a reader never raises
        return []


def _window(workspace_root, job_id: str, now=None) -> dict:
    from catchup import maintenance_window
    local_now = None
    if isinstance(now, _dt.datetime):
        local_now = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    return maintenance_window(workspace_root, job_id, now=local_now)


def _user(workspace_root) -> Optional[str]:
    try:
        from primary_user import resolve_primary_user
        return resolve_primary_user(workspace_root)
    except Exception:  # noqa: BLE001
        return None


def _signal_dials(workspace_root, env=None) -> Dict[str, Dict[str, Any]]:
    """category -> {people: skip or None, projects: skip or None}."""
    out: Dict[str, Dict[str, Any]] = {}
    for purpose in ("people", "projects"):
        for rec in _records(workspace_root, purpose, env):
            out.setdefault(rec["category"], {})[purpose] = rec.get("skip_reason")
    return out


# ---------------------------------------------------------------------------
# The plans (READ ONLY; on the read door's allow-list)
# ---------------------------------------------------------------------------

def _plan(workspace_root, job_id: str, purpose: str, intents: tuple, now=None) -> dict:
    window = _window(workspace_root, job_id, now)
    user = _user(workspace_root)
    signal = _signal_dials(workspace_root)
    connectors = []
    for rec in _records(workspace_root, purpose):
        cat = rec.get("category")
        connectors.append({
            "category": cat,
            "provider": rec.get("provider"),
            "label": rec.get("label"),
            "close_mode": (rec.get("dials") or {}).get("close_mode"),
            "include": list(rec.get("include") or []),
            "exclude": list(rec.get("exclude") or []),
            "skip_reason": rec.get("skip_reason"),
            "skip_kind": rec.get("skip_kind"),
            "people_skip_reason": (signal.get(cat) or {}).get("people"),
            "projects_skip_reason": (signal.get(cat) or {}).get("projects"),
            "intents": [i for i in _tasks.plan_intents(
                after=window.get("since_aware"),
                include=rec.get("include"), exclude=rec.get("exclude"))
                if i["intent"] in intents],
        })
    readable = [c for c in connectors
                if not c["skip_reason"]
                or not (c["people_skip_reason"] and c["projects_skip_reason"])]
    from catchup import MAINTENANCE_WINDOW_FIELD
    return {
        "job_id": job_id,
        "kind": KIND,
        "connectors": connectors,
        "connector_queries": [c["label"] or c["category"] for c in readable
                              if c.get("skip_kind") != "reachability"],
        "after": window.get("since_aware"),
        MAINTENANCE_WINDOW_FIELD: window,
        "user_person_id": user,
        "item_fields": list(_tasks.TASK_ITEM_FIELDS),
        "fire_start": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "ready": bool(user) or not connectors,
        "blocked_reason": (None if (user or not connectors) else
                           "the workspace's own person is not recorded"),
    }


def plan_tasks_capture(workspace_root, now=None) -> dict:
    """The tasks-capture job PLANNED beside the data - READ ONLY. The
    declared tasks connectors with their open-dial skips, lists and the two
    capture intents (`assigned_to_me`, `created_by_me`) bounded by the
    window. Writes nothing."""
    return _plan(workspace_root, JOB_CAPTURE, "open",
                 (_tasks.INTENT_ASSIGNED_TO_ME, _tasks.INTENT_CREATED_BY_ME), now)


def plan_tasks_close(workspace_root, now=None) -> dict:
    """The tasks-close job PLANNED beside the data - READ ONLY. The declared
    tasks connectors with their close-dial skips, `close_mode`, lists and
    the `done_since` intent bounded by the window. Writes nothing."""
    return _plan(workspace_root, JOB_CLOSE, "close",
                 (_tasks.INTENT_DONE_SINCE,), now)


# ---------------------------------------------------------------------------
# Shared reading: which connector an item came from, lists, signal
# ---------------------------------------------------------------------------

def _record_for(item_raw: dict, records: List[dict]) -> Optional[dict]:
    """The declared connector a staged item came from: its `category` (or
    `connector`) when it names one, else the only one declared."""
    want = _s((item_raw or {}).get("category") or (item_raw or {}).get("connector"))
    if want:
        for r in records:
            if r.get("category") == want:
                return r
        return None
    return records[0] if len(records) == 1 else None


def signal_skips(dials: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """`{category: {people|projects: why}}` for the connectors a dial turned
    away from that kind: what the receipt says when nothing was written."""
    out: Dict[str, Dict[str, str]] = {}
    for cat, kinds in (dials or {}).items():
        said = {k: v for k, v in (kinds or {}).items() if v}
        if said:
            out[cat] = said
    return out


def hand_off_seen(workspace_root, people, projects, *, source_skill: str) -> dict:
    """Give what a connector job saw to the two paths that own it (cr1#738):
    people to the identity queue, projects to the lifecycle path. The
    receipt fields come back; this job writes no person or project itself.

    A fault here is contained and named on the receipt. This runs ahead of
    the job's own commitment work, and a bad row in the book must never cost
    the owner a capture, a close or the receipt."""
    out: Dict[str, Any] = {}
    for label, module, writer, rows in (
            ("people", "identity_reconcile", "propose_seen_people", people),
            ("projects", "lifecycle_pass", "note_seen_projects", projects)):
        try:
            step = getattr(__import__(module), writer)
            out.update(step(workspace_root, rows, source_skill=source_skill))
        except Exception as exc:  # noqa: BLE001 - see the docstring
            out.setdefault("handoff_errors", []).append(
                {"kind": label, "error": f"{type(exc).__name__}: {exc}"})
    return out


def _read_items(workspace_root, items, records, *, owner_names=(),
                source_skill: str = JOB_CAPTURE):
    """`(kept, counts, signal)`: the items on boards the job may read, each
    with its connector record, the exclusion counts, and the people and
    project signal the readable items carry under their dials, which it
    hands to the book (`hand_off_seen`) with each item as the pointer."""
    from connector_reads import allowed
    signal_dials = _signal_dials(workspace_root)
    own = {_s(n).lower() for n in owner_names or () if _s(n)}
    kept: List[tuple] = []
    counts = {"n_scanned": 0, "n_excluded": 0, "n_unattributed": 0,
              "n_not_items": 0}
    people: set = set()
    projects: set = set()
    seen_people: List[dict] = []
    seen_projects: List[dict] = []
    n_no_pointer = 0
    for raw in items or []:
        counts["n_scanned"] += 1
        rec = _record_for(raw if isinstance(raw, dict) else {}, records)
        if rec is None:
            counts["n_unattributed"] += 1
            continue
        item = _tasks.normalize_task_item(raw, provider=rec.get("provider"))
        if item is None:
            counts["n_not_items"] += 1
            continue
        if not allowed(rec, *_tasks.board_names(item), must_match=True):
            counts["n_excluded"] += 1
            continue
        dials = signal_dials.get(rec.get("category")) or {}
        try:
            ref = _tasks.build_task_source_ref(item)
            pointer = {"ref": ref, "kind": KIND,
                       "source_ref": _tasks.source_ref_string(ref)}
        except _tasks.TaskPointerError:
            pointer = None
        wanted = False
        if "people" in dials and not dials.get("people"):
            names, ids = list(item["assignee_names"]), list(item["assignee_ids"])
            # A tool id belongs to a name only when the two lists line up.
            pairs = (list(zip(names, ids)) if len(names) == len(ids)
                     else [(nm, "") for nm in names])
            for nm, user_id in pairs + [(item["creator_name"], item["creator_id"])]:
                if _s(nm) and _s(nm).lower() not in own:
                    people.add(_s(nm))
                    wanted = True
                    if pointer:
                        seen_people.append(dict(pointer, name=_s(nm),
                                                source_user_id=_s(user_id)))
        if "projects" in dials and not dials.get("projects"):
            if item["board_name"] or item["board_id"]:
                projects.add(item["board_name"] or item["board_id"])
                wanted = True
                if pointer:
                    seen_projects.append(dict(
                        pointer, name=item["board_name"],
                        ts=_s(item.get("updated_at") or item.get("done_at")
                              or item.get("created_at"))))
        if wanted and pointer is None:
            n_no_pointer += 1
        kept.append((item, rec))
    signal = {"people_seen": sorted(people), "projects_seen": sorted(projects),
              "n_people_seen": len(people), "n_projects_seen": len(projects),
              "n_signal_no_pointer": n_no_pointer}
    said = signal_skips(signal_dials)
    if said:
        signal["signal_skips"] = said
    signal.update(hand_off_seen(workspace_root, seen_people, seen_projects,
                                source_skill=source_skill))
    return kept, counts, signal


def _read_outcome(workspace_root, job_id: str, items, records, connectors_read,
                  fetch_blocked) -> tuple:
    """`connector_reads.read_outcome` over this leg's items: `(blocked,
    unread, unconfirmed)`. An item the leg can place on a connector proves
    that read."""
    from connector_reads import read_outcome
    seen = set()
    for raw in items or []:
        rec = _record_for(raw, records) if isinstance(raw, dict) else None
        if rec is not None:
            seen.add(rec.get("category"))
    return read_outcome(workspace_root, job_id, records, seen=seen,
                        connectors_read=connectors_read,
                        fetch_blocked=fetch_blocked)


def read_fields(unread, unconfirmed) -> Dict[str, Any]:
    """What a leg's receipt carries about its gaps (cr1#743), nothing when it
    has none: everything it did not read, and the part nobody vouched for."""
    out: Dict[str, Any] = {}
    if unread:
        out["connectors_unreachable"] = list(unread)
    if unconfirmed:
        out["connectors_unconfirmed"] = list(unconfirmed)
    return out


def note_unread(workspace_root, receipt: dict, unread, unconfirmed) -> None:
    """A run that read part of what it was meant to says which part it did
    not (cr1#743): `degraded`, the chat leg's word for a real read over
    narrower cover, which the scorer counts as a run."""
    if not unread:
        return
    from connector_reads import unread_line
    receipt["status"] = "degraded"
    receipt.update(read_fields(unread, unconfirmed))
    line = unread_line(workspace_root, set(unread) - set(unconfirmed), unconfirmed)
    receipt["summary"] = " ".join(x for x in (receipt.get("summary"), line) if x)


def note_waiting(receipt: dict) -> None:
    """A read that left questions waiting says so (cr1#773): the counts are
    everything still held for a later day, so the line is true of the ledger."""
    people = receipt.get("n_people_waiting") or 0
    projects = receipt.get("n_projects_waiting") or 0
    if not people and not projects:
        return
    from brain_proposals import DAILY_CONNECTOR_QUESTION_CAP as cap
    said = [f"{n} new {one if n == 1 else many}" for n, one, many in (
        (people, "person", "people"), (projects, "project", "projects")) if n]
    verb = "is" if people + projects == 1 else "are"
    line = (f"{' and '.join(said)} {verb} waiting to be asked about, "
            f"no more than {cap} a day.")
    receipt["summary"] = " ".join(x for x in (receipt.get("summary"), line) if x)


def _skip_receipt(workspace_root, job_id: str, reason: str, *, window, fired_via,
                  signal=None, extra=None) -> dict:
    from catchup import MAINTENANCE_WINDOW_FIELD
    receipt = {"ran": False, "status": "skipped", "leg": job_id,
               "skip_reason": reason, MAINTENANCE_WINDOW_FIELD: window,
               "summary": ""}
    receipt.update(signal or {})
    receipt.update(extra or {})
    note_waiting(receipt)
    _log_receipt(workspace_root, job_id, receipt, fired_via=fired_via)
    return receipt


def _log_receipt(workspace_root, job_id: str, receipt: dict, *, fired_via) -> None:
    """ONE `pack_run` under the job's own id, for every terminal state. A
    receipt failure is stamped on the returned dict and never raised: no
    receipt keeps the job due, which self-heals on the next fire."""
    from receipts import log_receipt
    keep = {k: v for k, v in receipt.items()
            if k not in ("events", "summary_rows") and v is not None}
    try:
        log_receipt(workspace_root, job_id, receipt_type="pack_run",
                    status=receipt.get("status") or "complete",
                    fired_via=fired_via,
                    surfaced=int(receipt.get("n_captured") or receipt.get("n_closed") or 0),
                    extra_data=keep)
    except Exception as exc:  # noqa: BLE001
        print(f"tasks_leg: receipt FAILED to land ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        receipt["receipt_failed"] = True
        receipt["receipt_error"] = f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# The capture half
# ---------------------------------------------------------------------------

def _person_for_name(workspace_root, name: str, user_person_id: str) -> str:
    try:
        from capture_gate import _person_id_for_name
        return _person_id_for_name(workspace_root, name,
                                   user_person_id=user_person_id) or ""
    except Exception:  # noqa: BLE001
        return ""


def _already_captured(workspace_root, ref_string: str) -> bool:
    """True when a row from this item (any title) is already on the book,
    open, held or closed: a re-fire over the same board writes nothing."""
    from connector_adapters.provenance import dedup_key_of
    from events_io import iter_events_org_scoped
    want = dedup_key_of(ref_string)
    if not want:
        return False
    for ev in iter_events_org_scoped(workspace_root):
        if ev.get("type") not in ("commitment", "commitment_observed",
                                  "commitment_resolved", "thread_resolved"):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if dedup_key_of(data.get("source_ref")) == want:
            return True
    return False


def build_task_commitment_event(item: dict, cls: str, *, user_person_id: str,
                                workspace_root=None, source_skill: str = JOB_CAPTURE,
                                owner_ids=(), owner_names=()) -> dict:
    """One captured item -> one `commitment` event dict, through THE shared
    capture block. `cls` is `promise` or `waiting_on`. Raises
    `capture_gate.CaptureGateError` (or `TaskPointerError`) on a row the
    gate refuses."""
    from capture_gate import gate_commitment_data
    from event_types import derive_primary_thread_id

    ref = _tasks.build_task_source_ref(item)
    title = _s(item.get("title"))
    data: Dict[str, Any] = {
        "title": title,
        "due": _s(item.get("due")),
        "channel": KIND,
        "origin": "connector",
        "capture_class": cls,
    }
    data.update(_tasks.pointer_fields(ref))
    person_ids = [user_person_id]
    if cls == _tasks.CLASS_PROMISE:
        data["owner_id"] = user_person_id
        creator_pid = _s(item.get("creator_person_id"))
        creator_name = _s(item.get("creator_name"))
        mine_too = _tasks.owner_is_creator(item, owner_person_id=user_person_id,
                                           owner_ids=owner_ids, owner_names=owner_names)
        if not creator_pid and creator_name and not mine_too:
            creator_pid = _person_for_name(workspace_root, creator_name, user_person_id)
        if creator_pid and creator_pid != user_person_id:
            data["counterparty_id"] = creator_pid
            person_ids.append(creator_pid)
            data["kind"] = "promise"
        elif creator_name and not mine_too:
            data["counterparty_name"] = creator_name
            data["kind"] = "promise"
        else:
            data["kind"] = "task"
    else:
        pids = [p for p in item.get("assignee_person_ids") or [] if p != user_person_id]
        names = list(item.get("assignee_names") or [])
        owner_pid = pids[0] if pids else ""
        if not owner_pid and names:
            owner_pid = _person_for_name(workspace_root, names[0], user_person_id)
        data["owner_id"] = owner_pid
        if not owner_pid:
            data["owner_external"] = names[0] if names else (
                (item.get("assignee_ids") or [""])[0])
        else:
            person_ids.append(owner_pid)
        data["counterparty_id"] = user_person_id
        data["kind"] = "promise"
    gate_commitment_data(data, subject=f"task item {data['source_ref']}",
                         workspace_root=workspace_root)
    data["status"] = "open"
    try:
        if _dt.date.fromisoformat(data["due"][:10]) < _dt.datetime.now(_dt.timezone.utc).date():
            data["status"] = "overdue"
    except (ValueError, TypeError):
        pass
    event: Dict[str, Any] = {"type": "commitment", "source_skill": source_skill,
                             "person_ids": person_ids, "data": data}
    thread = derive_primary_thread_id({"data": data}) or ""
    if thread:
        event["primary_thread_id"] = thread
    when = _s(item.get("created_at") or item.get("updated_at"))
    if when:
        event["ts"] = when
    return event


def capture_tasks_and_receipt(workspace_root, items, *, user_person_id,
                              owner_ids=(), owner_names=(),
                              maintenance_window=None, fired_via=None,
                              fetch_blocked=None, connectors_read=None,
                              cap: Optional[int] = None,
                              source_skill: str = JOB_CAPTURE, now=None) -> dict:
    """Run the capture half end to end over the items the fire read and
    return the receipt it wrote (`pack_run` under `tasks-capture`).

    `items` - the staged items, `TASK_ITEM_FIELDS` each, plus `category`
    when the seat declares more than one tasks connector. `owner_ids` and
    `owner_names` are the owner's own ids and names on the task tool; an
    item's `assignee_person_ids` / `creator_person_id`, when the connector
    step resolved them, are compared with `user_person_id` too.

    `connectors_read` (cr1#743) is the categories whose read answered, an
    empty answer included; `fetch_blocked` is why no read answered. An empty
    `items` alone proves no read (`connector_reads.read_outcome`).

    Terminal states: no tasks connector declared, or every one turned away
    by its dial (skipped, receipted, nothing captured; the people and
    project signal is still read where its own dials allow); blocked (no
    read happened, or none was proved); degraded (one connector read, another
    named as not read); complete."""
    from receipts import require_writer_identity
    require_writer_identity(workspace_root=workspace_root)
    fired_via = _effective_fired_via(fired_via)
    window = maintenance_window or _window(workspace_root, JOB_CAPTURE, now)
    from catchup import MAINTENANCE_WINDOW_FIELD

    records = _records(workspace_root, "open")
    if not records:
        return _skip_receipt(workspace_root, JOB_CAPTURE,
                             "no tasks connector is declared for this workspace",
                             window=window, fired_via=fired_via)
    if not user_person_id:
        raise TasksLegError(
            "tasks-capture ABORTED: the primary user is unresolved, so no item "
            "can be told apart as the owner's or someone else's. Nothing was "
            "read or written (the Bug #102 posture).")
    blocked, unread, unsure = _read_outcome(workspace_root, JOB_CAPTURE, items, records,
                                            connectors_read, fetch_blocked)
    if blocked:
        said = "confirmed as read" if unsure else "read"
        receipt = {"ran": False, "status": "blocked", "leg": JOB_CAPTURE,
                   "blocked_reason": blocked, MAINTENANCE_WINDOW_FIELD: window,
                   "connectors_unreachable": unread,
                   "summary": f"Tasks were not {said}: {blocked}. Nothing was captured."}
        receipt.update(read_fields(unread, unsure))
        _log_receipt(workspace_root, JOB_CAPTURE, receipt, fired_via=fired_via)
        return receipt

    kept, counts, signal = _read_items(workspace_root, items, records,
                                       owner_names=owner_names,
                                       source_skill=source_skill)
    open_records = {r["category"] for r in records if not r.get("skip_reason")}
    skips = {r["category"]: r["skip_reason"] for r in records if r.get("skip_reason")}
    if not open_records:
        return _skip_receipt(
            workspace_root, JOB_CAPTURE, "; ".join(sorted(set(skips.values()))),
            window=window, fired_via=fired_via, signal=signal,
            extra=dict(counts, connector_skips=skips, **read_fields(unread, unsure)))

    from capture_gate import (CaptureGateError, classify_capture,
                              intake_kwargs, matches_open_commitment,
                              observed_from_commitment_event,
                              resolve_capture_mode, workspace_capture_context)
    from cru_match import load_open_commitments
    from event_gate import append_event

    cap = max(1, int(cap or DEFAULT_CAPTURE_CAP))
    opens = load_open_commitments(str(_events_path(workspace_root)),
                                  workspace_root=workspace_root)
    ctx = workspace_capture_context(workspace_root)
    user_names = list(ctx.get("user_names") or []) + list(owner_names or [])
    tallies = {"n_promise": 0, "n_waiting_on": 0, "n_no_due": 0,
               "n_no_relation": 0, "n_done_not_captured": 0,
               "n_dialed_off": 0, "n_skipped_existing": 0, "n_merged": 0,
               "n_observed": 0, "n_captured": 0, "n_capped": 0, "n_errors": 0}
    batch: List[dict] = []
    accepted: List[dict] = []
    captured, errors = [], []
    for item, rec in kept:
        if rec.get("category") not in open_records:
            tallies["n_dialed_off"] += 1
            continue
        cls = _tasks.capture_class(item, owner_person_id=user_person_id,
                                   owner_ids=owner_ids, owner_names=owner_names)
        if cls == _tasks.CLASS_NO_DUE:
            tallies["n_no_due"] += 1
            continue
        if cls == _tasks.CLASS_NO_RELATION:
            tallies["n_no_relation"] += 1
            continue
        if item.get("done"):
            # Done already: there is nothing open to track. The close half
            # reads done items; a capture of one would only close again.
            tallies["n_done_not_captured"] += 1
            continue
        try:
            ref = _tasks.build_task_source_ref(item)
            if _already_captured(workspace_root, _tasks.source_ref_string(ref)):
                tallies["n_skipped_existing"] += 1
                continue
            ev = build_task_commitment_event(
                item, cls, user_person_id=user_person_id,
                workspace_root=workspace_root, source_skill=source_skill,
                owner_ids=owner_ids, owner_names=owner_names)
        except (CaptureGateError, _tasks.TaskPointerError, ValueError) as exc:
            tallies["n_errors"] += 1
            errors.append({"item_id": item.get("item_id"), "error": str(exc)})
            continue
        match = matches_open_commitment(
            ev["data"], list(opens) + accepted,
            person_ids=ev.get("person_ids") or (),
            exclude_party_ids={user_person_id}, exclude_party_names=user_names)
        if match is not None:
            tallies["n_merged"] += 1
            continue
        if len(batch) >= cap:
            tallies["n_capped"] += 1
            continue
        tier = classify_capture(
            ev["data"], mode=resolve_capture_mode(workspace_root),
            user_id=user_person_id, user_names=user_names,
            team_ids=ctx.get("team_ids") or (), known_ids=ctx.get("known_ids") or (),
            **intake_kwargs(ctx, primary_thread_id=ev.get("primary_thread_id"),
                            person_ids=ev.get("person_ids")))
        if tier.get("tier") == "observed":
            batch.append(observed_from_commitment_event(ev, reason=tier.get("reason") or ""))
            tallies["n_observed"] += 1
            continue
        batch.append(ev)
        accepted.append(ev)
        tallies["n_captured"] += 1
        tallies["n_promise" if cls == _tasks.CLASS_PROMISE else "n_waiting_on"] += 1
        captured.append({"item_id": item.get("item_id"), "class": cls,
                         "source_ref": ev["data"]["source_ref"]})
    if batch:
        append_event(_events_path(workspace_root), batch, holder=source_skill)
    receipt = {"ran": True, "status": "complete", "leg": JOB_CAPTURE,
               MAINTENANCE_WINDOW_FIELD: window, "captured": captured,
               "errors": errors, "connector_skips": skips, "cap": cap}
    receipt.update(counts)
    receipt.update(tallies)
    receipt.update(signal)
    receipt["summary"] = _capture_summary(receipt)
    note_waiting(receipt)
    note_unread(workspace_root, receipt, unread, unsure)
    _log_receipt(workspace_root, JOB_CAPTURE, receipt, fired_via=fired_via)
    return receipt


def _capture_summary(r: dict) -> str:
    if not r.get("n_scanned"):
        return "No new task items since the last check."
    bits = []
    if r.get("n_captured"):
        bits.append(f"{r['n_captured']} tracked from your task boards "
                    f"({r.get('n_promise', 0)} yours, {r.get('n_waiting_on', 0)} "
                    "waiting on others)")
    else:
        bits.append("nothing new tracked from your task boards")
    if r.get("n_no_due"):
        bits.append(f"{r['n_no_due']} with no due date read for people and projects only")
    if r.get("n_capped"):
        bits.append(f"{r['n_capped']} held for the next pass")
    return "; ".join(bits) + "."


# ---------------------------------------------------------------------------
# The close half
# ---------------------------------------------------------------------------

def _matcher_message(item: dict) -> dict:
    """A done item in the shape the existing matchers take. The native id is
    the item id; the matcher adds the provider prefix itself."""
    return {
        "message_id": _s(item.get("item_id")),
        "ts": _s(item.get("done_at") or item.get("updated_at")),
        "thread_id": None,
        "has_attachment": False,
        "recipient_person_ids": [p for p in list(item.get("assignee_person_ids") or [])
                                 + [_s(item.get("creator_person_id"))] if p],
        "recipient_names": [n for n in list(item.get("assignee_names") or [])
                            + [_s(item.get("creator_name"))] if n],
        # A task title is its whole text: carried once as the body so the
        # title band scores it once (the chat leg's reason, the same one).
        "subject": None,
        "body": _s(item.get("title")),
    }


def party_names(workspace_root, commitment: Optional[dict], *, user_person_id,
                extra=()) -> List[str]:
    """Every name the shared-word fence sets aside for one commitment: the
    owner's, the commitment's parties' (on the row and on file) and the
    `extra` names the evidence itself carries. A name is never content (the
    chat leg's cr1#468 rule: a shared first name is not a shared topic)."""
    from commitment_parties import counterparty_ids, counterparty_names
    names: List[str] = [n for n in extra if _s(n)]
    try:
        import json
        from entities_io import entities_collection
        raw = json.loads((Path(workspace_root) / "_hq" / "data" / "entities.json")
                         .read_text(encoding="utf-8"))
        people = {}
        for p in entities_collection(raw, "people"):
            labels = [p.get("name"), p.get("canonical_name")] + list(p.get("aliases") or [])
            people[_s(p.get("id"))] = [_s(x) for x in labels if _s(x)]
    except Exception:  # noqa: BLE001 - a name lookup never raises into the leg
        people = {}
    pids = [_s(user_person_id)]
    if isinstance(commitment, dict):
        data = commitment.get("data") if isinstance(commitment.get("data"), dict) else {}
        try:
            names += counterparty_names(data, workspace_root=workspace_root)
            pids += counterparty_ids(data) + [_s(data.get("owner_id"))]
        except Exception:  # noqa: BLE001
            pass
        pids += [_s(x) for x in commitment.get("person_ids") or []]
    for pid in pids:
        names += people.get(pid, [])
    return names


def _score_by_matcher(opens, done: List[tuple], *, user_person_id, owner_ids,
                      owner_names, exclude_captured_since, workspace_root):
    """`{native item id: [(row, recommendation)]}` from the EXISTING
    matchers: an item the owner did goes to SENTMATCH (the owner's own act,
    `from_mail=False` as chat), one somebody else did to REPLYCLOSE. Rows
    that share no content word with the commitment are dropped (the chat
    leg's cr1#468 rule: a task title is as short as a chat line)."""
    from cru_match import _commitment_id, shared_content_words
    from reconcile_inbound_commitments import reconcile_inbound
    from reconcile_sent_commitments import reconcile_sent

    by_cid = {str(_commitment_id(ev)): ev for ev in opens or [] if isinstance(ev, dict)}

    by_provider: Dict[str, Dict[str, List[dict]]] = {}
    for item, rec in done:
        mine = _tasks.owner_is_assignee(item, owner_person_id=user_person_id,
                                        owner_ids=owner_ids, owner_names=owner_names)
        lane = "out" if mine else "in"
        by_provider.setdefault(item.get("provider") or "", {}).setdefault(lane, []).append(item)
    out: Dict[str, List[tuple]] = {}
    for provider, lanes in by_provider.items():
        for lane, its in lanes.items():
            msgs = [_matcher_message(i) for i in its]
            if lane == "out":
                res = reconcile_sent(opens, msgs, user_person_id=user_person_id,
                                     provider=provider or None,
                                     exclude_captured_since=exclude_captured_since,
                                     workspace_root=workspace_root, from_mail=False)
            else:
                res = reconcile_inbound(opens, msgs, user_person_id=user_person_id,
                                        provider=provider or None,
                                        exclude_captured_since=exclude_captured_since,
                                        workspace_root=workspace_root)
            text = {m["message_id"]: m for m in msgs}
            for rec_name, rows in (("auto_resolve", res.get("auto_close") or []),
                                   ("pending_review", res.get("pending") or [])):
                for row in rows:
                    mid = _s(row.get("message_id"))
                    msg = text.get(mid) or {}
                    names = party_names(
                        workspace_root, by_cid.get(str(row.get("commitment_id") or "")),
                        user_person_id=user_person_id,
                        extra=list(owner_names or []) + list(msg.get("recipient_names") or []))
                    if not shared_content_words(msg.get("body"), row.get("title"),
                                                names=names):
                        continue
                    out.setdefault(mid, []).append((row, rec_name))
    return out


def close_tasks_and_receipt(workspace_root, items, *, user_person_id,
                            owner_ids=(), owner_names=(),
                            maintenance_window=None, fired_via=None,
                            fetch_blocked=None, connectors_read=None,
                            exclude_captured_since=None,
                            source_skill: str = JOB_CLOSE, now=None) -> dict:
    """Run the close half end to end over the done items the fire read and
    return the receipt it wrote (`pack_run` under `tasks-close`).

    Per done item on a board the job may read: the rows captured from the
    same item (pointer identity) first, then the existing matchers. A
    connector with `close_mode: close` (a board the owner declared as
    project tracking) closes the matched row outright, the item as its
    source pointer; any other connector writes a close proposal for the
    owner to confirm, the item as its source pointer. A matcher row the
    matcher itself only proposes stays a proposal everywhere.

    `connectors_read` and `fetch_blocked` are the capture half's (cr1#743)."""
    from receipts import require_writer_identity
    require_writer_identity(workspace_root=workspace_root)
    fired_via = _effective_fired_via(fired_via)
    window = maintenance_window or _window(workspace_root, JOB_CLOSE, now)
    from catchup import MAINTENANCE_WINDOW_FIELD

    records = _records(workspace_root, "close")
    if not records:
        return _skip_receipt(workspace_root, JOB_CLOSE,
                             "no tasks connector is declared for this workspace",
                             window=window, fired_via=fired_via)
    if not user_person_id:
        raise TasksLegError(
            "tasks-close ABORTED: the primary user is unresolved. Nothing was "
            "read or written (the Bug #102 posture).")
    blocked, unread, unsure = _read_outcome(workspace_root, JOB_CLOSE, items, records,
                                            connectors_read, fetch_blocked)
    if blocked:
        said = "confirmed as read" if unsure else "read"
        receipt = {"ran": False, "status": "blocked", "leg": JOB_CLOSE,
                   "blocked_reason": blocked, MAINTENANCE_WINDOW_FIELD: window,
                   "connectors_unreachable": unread,
                   "summary": f"Tasks were not {said}: {blocked}. Nothing was closed."}
        receipt.update(read_fields(unread, unsure))
        _log_receipt(workspace_root, JOB_CLOSE, receipt, fired_via=fired_via)
        return receipt

    kept, counts, signal = _read_items(workspace_root, items, records,
                                       owner_names=owner_names,
                                       source_skill=source_skill)
    skips = {r["category"]: r["skip_reason"] for r in records if r.get("skip_reason")}
    live = {r["category"] for r in records if not r.get("skip_reason")}
    if not live:
        return _skip_receipt(
            workspace_root, JOB_CLOSE, "; ".join(sorted(set(skips.values()))),
            window=window, fired_via=fired_via, signal=signal,
            extra=dict(counts, connector_skips=skips, **read_fields(unread, unsure)))

    from commitment_state import close_commitments
    from connector_adapters.provenance import dedup_key_of
    from cru_match import (_commitment_id, build_pending_review_event,
                           load_open_commitments, load_open_review_proposals)
    from event_gate import append_event

    events_path = _events_path(workspace_root)
    opens = load_open_commitments(str(events_path), workspace_root=workspace_root)
    by_key: Dict[str, List[dict]] = {}
    for ev in opens:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        k = dedup_key_of(data.get("source_ref"))
        if k:
            by_key.setdefault(k, []).append(ev)

    done: List[tuple] = []
    tallies = {"n_done": 0, "n_not_done": 0, "n_dialed_off": 0,
               "n_refused_no_pointer": 0}
    for item, rec in kept:
        if rec.get("category") not in live:
            tallies["n_dialed_off"] += 1
            continue
        if not item.get("done"):
            tallies["n_not_done"] += 1
            continue
        tallies["n_done"] += 1
        done.append((item, rec))

    # (commitment id) -> (row-like dict, ref, close_mode, recommendation)
    decisions: Dict[str, tuple] = {}
    matcher_items = []
    for item, rec in done:
        try:
            ref = _tasks.build_task_source_ref(item)
        except _tasks.TaskPointerError:
            tallies["n_refused_no_pointer"] += 1
            continue
        mode = (rec.get("dials") or {}).get("close_mode")
        hits = by_key.get(dedup_key_of(_tasks.source_ref_string(ref)) or "") or []
        for ev in hits:
            cid = str(_commitment_id(ev))
            data = ev.get("data") or {}
            decisions[cid] = ({"commitment_id": cid, "title": data.get("title") or "",
                               "primary_thread_id": ev.get("primary_thread_id") or "",
                               "score": 1.0, "ts": item.get("done_at") or None,
                               "evidence": f"{_DONE_EVIDENCE}: {item.get('title')}"},
                              ref, mode, "auto_resolve", "item")
        if not hits:
            matcher_items.append((item, rec))
    if matcher_items:
        rest = [ev for ev in opens if str(_commitment_id(ev)) not in decisions]
        scored = _score_by_matcher(
            rest, matcher_items, user_person_id=user_person_id,
            owner_ids=owner_ids, owner_names=owner_names,
            exclude_captured_since=exclude_captured_since,
            workspace_root=workspace_root)
        by_mid = {_s(i.get("item_id")): (i, r) for i, r in matcher_items}
        for mid, rows in scored.items():
            item, rec = by_mid.get(mid, (None, None))
            if item is None:
                continue
            ref = _tasks.build_task_source_ref(item)
            mode = (rec.get("dials") or {}).get("close_mode")
            for row, recommendation in rows:
                cid = str(row.get("commitment_id") or "")
                if not cid:
                    continue
                prev = decisions.get(cid)
                if prev is not None and (prev[3] == "auto_resolve"
                                         or recommendation != "auto_resolve"):
                    continue
                decisions[cid] = (dict(row), ref, mode, recommendation, "matcher")

    to_close, to_propose = [], []
    for cid, (row, ref, mode, recommendation, basis) in decisions.items():
        if mode == "close" and recommendation == "auto_resolve":
            to_close.append((cid, row, ref, basis))
        else:
            to_propose.append((cid, row, ref, basis))

    closed: List[dict] = []
    refused: List[dict] = []
    if to_close:
        results = close_commitments(workspace_root, [{
            "commitment_id": cid,
            "resolved_by": RESOLVED_BY,
            "evidence": row.get("evidence") or _DONE_EVIDENCE,
            "primary_thread_id": row.get("primary_thread_id") or "",
            "source_ref": _tasks.source_ref_string(ref),
            "extra_data": dict(_tasks.pointer_fields(ref), close_basis=basis,
                               close_mode="close"),
        } for cid, row, ref, basis in to_close], source_skill=source_skill)
        for r in results:
            if r.get("status") == "closed":
                closed.append({"commitment_id": r.get("commitment_id"),
                               "source_ref": (r.get("event") or {}).get("data", {}).get("source_ref")})
            else:
                refused.append({"commitment_id": r.get("commitment_id"),
                                "why": r.get("error") or r.get("status")})

    proposed: List[dict] = []
    if to_propose:
        already = {(p.get("data") or {}).get("commitment_id")
                   for p in load_open_review_proposals(str(events_path))}
        rows_out = []
        for cid, row, ref, basis in to_propose:
            if cid in already:
                continue
            ev = build_pending_review_event(
                commitment_id=cid,
                primary_thread_id=row.get("primary_thread_id") or "",
                source_skill=source_skill,
                proposed_resolution="auto_resolve",
                score=row.get("score") or 0,
                evidence=row.get("evidence") or _DONE_EVIDENCE,
                next_seq=None,
                title=row.get("title") or "",
                evidence_ts=row.get("ts") or None,
                source_ref=_tasks.source_ref_string(ref),
            )
            ev["data"]["task_source_ref"] = dict(ref)
            ev["data"]["close_basis"] = basis
            rows_out.append(ev)
            proposed.append({"commitment_id": cid,
                             "source_ref": _tasks.source_ref_string(ref)})
        if rows_out:
            append_event(events_path, rows_out, holder=source_skill)

    receipt = {"ran": True, "status": "complete", "leg": JOB_CLOSE,
               MAINTENANCE_WINDOW_FIELD: window, "closed": closed,
               "proposed": proposed, "refused": refused,
               "connector_skips": skips,
               "n_closed": len(closed), "n_proposed": len(proposed),
               "n_refused": len(refused)}
    receipt.update(counts)
    receipt.update(tallies)
    receipt.update(signal)
    receipt["summary"] = _close_summary(receipt)
    note_waiting(receipt)
    note_unread(workspace_root, receipt, unread, unsure)
    _log_receipt(workspace_root, JOB_CLOSE, receipt, fired_via=fired_via)
    return receipt


def _close_summary(r: dict) -> str:
    if not r.get("n_done"):
        return "No task items marked done since the last check."
    bits = []
    if r.get("n_closed"):
        bits.append(f"closed {r['n_closed']} done on a tracking board")
    if r.get("n_proposed"):
        bits.append(f"{r['n_proposed']} to confirm")
    if not bits:
        return (f"Checked {r['n_done']} done task item"
                f"{'s' if r['n_done'] != 1 else ''}; nothing matched an open commitment.")
    return "Reconciled your task boards: " + ", ".join(bits) + "."


__all__ = [
    "KIND", "JOB_CAPTURE", "JOB_CLOSE", "RESOLVED_BY", "DEFAULT_CAPTURE_CAP",
    "TasksLegError", "plan_tasks_capture", "plan_tasks_close",
    "build_task_commitment_event", "capture_tasks_and_receipt",
    "close_tasks_and_receipt",
]
