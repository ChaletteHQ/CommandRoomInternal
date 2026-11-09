#!/usr/bin/env python3
"""cr1#928 - the three skill-class maintenance jobs, run through the door.

THE DEFECT. On a merged seat (the folder on the owner's computer, the chat in
the cloud) the registered Maintenance prompt's merged branch left every
`skill`-class job due "for a fire on a seat that holds the files". A merged-only
seat has no such fire, so Cleanup, Weekly Insights and the monthly report never
ran there: their last receipts were from late September and Oct 1.

THE FIX. Each of the three is a `door` job now (`maintenance_dispatcher.
DOOR_JOBS`), with the same three parts every other door job has: a PLAN helper
beside the data (a read, on `workspace_access.RUN_HELPER_ALLOWLIST`), a step in
the session, and a WRITER through the write door (on
`workspace_access.RUN_WRITER_ALLOWLIST`) that lands the job's OWN receipt, so
the job's due-ness moves exactly as it does when its skill runs on a seat that
holds the files.

What each one does through the door - narrower than its skill, by design, and
said on its receipt (`scope: "door"`, plus the passes it did not run):

  * cleanup - the passes of skills/cleanup/SKILL.md that are already code, run
    beside the data: corruption recovery (3b), the dual project key repair
    (3.5a-bis), the integrity inspector (3a, detect only), every active
    thread's Live State (3.5b), the People, decision log, tracker and
    routing-corrections views (3.5c-3.5d4), and the two staleness flags
    (3.5e, the aliases flag). It lands `cleanup_run`. The Monday note, the
    judgment sweeps written as prose, the watchdog sweep and the event-log
    rotation are NOT run on this shape and are named on the receipt.
  * weekly-insights - the five analytical projections of
    skills/insight-generator/SKILL.md (timeline, relationship cadence,
    commitment aging, dormant projects, themes), computed beside the data and
    written to their views, plus the insights document: the sentences the
    session composed from the plan's projections (there is no connector), or,
    with none, the projections' own top lines. It lands the `pack_run`. The
    interactive review passes (8 onward) and the Pass 7 connector probe are
    NOT run here.
  * monthly-report - for each month the dispatcher owes, the value receipt
    (`value_receipt.compute_value_receipt`, which lands its own
    `value_receipt_generated`) and its document, and the operating report:
    the synthesis paragraph the session composed from the plan's digest, the
    value receipt's numbers, the closes coverage line and the flow block. It
    lands one `operator_report_generated` per month.

Every writer names the writer identity FIRST (`receipts.require_writer_
identity`), so on a merged seat with no forwarded identity nothing is written.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path
from statistics import median
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

JOB_CLEANUP = "cleanup"
JOB_INSIGHTS = "weekly-insights"
JOB_MONTHLY = "monthly-report"

#: Stamped on every receipt these writers land: the door's narrower run, so a
#: reader can tell it from the full skill run on a seat that holds the files.
DOOR_SCOPE = "door"

#: The cleanup passes that are code, in the order the writer runs them.
CLEANUP_DOOR_PASSES = (
    "recover_corruption",   # 3b
    "dual_project_key",     # 3.5a-bis
    "integrity_check",      # 3a (detect only)
    "live_state",           # 3.5b
    "people_view",          # 3.5c
    "decision_log",         # 3.5d
    "master_tracker",       # 3.5d2
    "router_misses",        # 3.5d4
    "view_staleness",       # 3.5e
    "aliases_staleness",    # 3.5c's D7 flag
)

#: The cleanup passes that need a session reading the workspace, or a person:
#: not run through the door, and named on the receipt.
CLEANUP_NOT_ON_DOOR = (
    "monday_note", "silent_scan", "auto_fix_sweep", "thread_kind_heal",
    "thread_subject_detection", "watchdog_sweep", "deliverable_sweep",
    "event_log_rotation", "session_notes_rollover",
)

#: The insight-generator passes not run through the door.
INSIGHTS_NOT_ON_DOOR = ("connector_probe", "classification_review",
                        "project_proposals", "org_proposals",
                        "learning_reviews")

#: The five analytical views (references/VIEW_GENERATION.md), by projection.
INSIGHT_VIEWS = {
    "timeline": "TIMELINE.md",
    "relationships": "RELATIONSHIPS.md",
    "commitment_aging": "COMMITMENT_AGING.md",
    "dormant": "DORMANT.md",
    "themes": "THEMES.md",
}

#: The skill's own floor: no synthesis on a workspace with less history.
MIN_HISTORY_DAYS = 14
TIMELINE_DAYS = 30
DORMANT_DAYS = 30
THEME_DAYS = 14
STALE_MIN_DAYS = 14
LIST_CAP = 10
#: The most composed sentences an insights document carries, and their length.
MAX_ITEMS = 5
MAX_ITEM_CHARS = 400
MAX_LEAD_CHARS = 1200

MONTHLY_CRON = "0 0 1 * *"


# ---------------------------------------------------------------------------
# shared reads
# ---------------------------------------------------------------------------

def _ws(workspace_root) -> Path:
    return Path(str(workspace_root))


def _ledger_ready(ws: Path) -> bool:
    return (ws / "_hq" / "data" / "events.jsonl").is_file()


def _now_local(now=None) -> _dt.datetime:
    """Machine-local naive, the clock the dispatcher reads."""
    if now is None:
        return _dt.datetime.now()
    if isinstance(now, str):
        now = _dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
    if now.tzinfo is not None:
        now = now.astimezone().replace(tzinfo=None)
    return now


def _event_local(ev: dict) -> Optional[_dt.datetime]:
    raw = ev.get("ts") if isinstance(ev, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        when = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return when


def _events(ws: Path) -> list:
    """The org-scoped events (PGUARD1): personal-lane rows never reach a
    document."""
    try:
        from events_io import load_events_org_scoped

        rows, _skipped = load_events_org_scoped(ws)
        return [r for r in rows if isinstance(r, dict)]
    except Exception:  # noqa: BLE001 - an unreadable book reads as empty
        return []


def _entities(ws: Path) -> dict:
    try:
        from entities_io import read_entities_json

        doc = read_entities_json(ws / "_hq" / "data" / "entities.json")
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(doc, dict):
        return {}
    inner = doc.get("entities")
    return inner if isinstance(inner, dict) else doc


def _collection(ent: dict, name: str) -> list:
    try:
        from entities_io import entities_collection

        rows = entities_collection(ent, name)
    except Exception:  # noqa: BLE001
        rows = ent.get(name) or []
    return [r for r in rows or [] if isinstance(r, dict)]


def _name(row: dict) -> str:
    return str(row.get("canonical_name") or row.get("name") or row.get("id") or "")


def _window(ws: Path, job_id: str, now: _dt.datetime) -> Optional[dict]:
    try:
        from catchup import maintenance_window

        return maintenance_window(ws, job_id, now=now)
    except Exception:  # noqa: BLE001 - the window never costs a run
        return None


def _run_mode(fired_via, triggered_by) -> tuple:
    from receipts import effective_fired_via

    trig = (triggered_by or os.environ.get("CR_TRIGGERED_BY") or "").strip()
    return effective_fired_via(fired_via), (trig or None)


def _rel(ws: Path, path: Path) -> str:
    try:
        return path.relative_to(ws).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

def plan_cleanup(workspace_root, now=None) -> dict:
    """The cleanup job PLANNED beside the data - READ ONLY (cr1#928; on
    `workspace_access.RUN_HELPER_ALLOWLIST`). Names the passes the writer runs
    and the ones it does not; there is nothing to read in the session.
    Writes nothing."""
    ws = _ws(workspace_root)
    from catchup import MAINTENANCE_WINDOW_FIELD

    return {"job_id": JOB_CLEANUP, "ready": _ledger_ready(ws),
            "passes": list(CLEANUP_DOOR_PASSES),
            "not_run": list(CLEANUP_NOT_ON_DOOR),
            MAINTENANCE_WINDOW_FIELD: _window(ws, JOB_CLEANUP, _now_local(now))}


def _live_state_pass(ws: Path) -> dict:
    import render_thread_live_state as rtl

    refreshed, unlinked, missing, errors = 0, 0, 0, 0
    for thread in _collection(_entities(ws), "threads"):
        if thread.get("status") in ("archived", "resolved") or not thread.get("id"):
            continue
        try:
            res = rtl.render_live_state(ws, thread["id"]) or {}
        except Exception:  # noqa: BLE001 - per thread, counted, never swallowed whole
            errors += 1
            continue
        status = res.get("status")
        if status == "no_brain_path":
            unlinked += 1
        elif status == "no_project_folder":
            missing += 1
        elif res.get("rendered"):
            refreshed += 1
    return {"refreshed": refreshed, "not_linked": unlinked,
            "folder_missing": missing, "errors": errors}


def cleanup_and_receipt(workspace_root, *, maintenance_window=None,
                        fired_via: Optional[str] = None,
                        triggered_by: Optional[str] = None,
                        now=None) -> dict:
    """Run cleanup's code passes beside the data and land ONE `cleanup_run`
    (cr1#928; on `workspace_access.RUN_WRITER_ALLOWLIST`).

    Each pass runs on its own: one that raises is named under
    `passes_failed` and the rest still run. The receipt is `complete` when
    every pass ran and `degraded` (which still counts the run) when some did
    not. When NO pass ran, nothing lands: any receipt of this job's types
    serves its slot whatever its status, so a `blocked` one would hide the
    failure until next week (cr1#928 review F1); the fire scores it failed
    and it stays owed. `dualkey1_repair` carries its three counts on every
    run, zero-written, as the skill's Phase 3e asks."""
    from receipts import log_receipt, require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    ws = _ws(workspace_root)
    via, trig = _run_mode(fired_via, triggered_by)
    actions: list = []
    flagged: list = []
    ran: list = []
    failed: dict = {}
    detail: dict = {}
    dualkey = {"n_merged": 0, "n_quarantined": 0, "n_deleted_keys": 0}

    def run(name, fn):
        try:
            out = fn()
        except Exception as exc:  # noqa: BLE001 - one pass never costs the rest
            failed[name] = type(exc).__name__
            return None
        ran.append(name)
        return out

    rec = run("recover_corruption", lambda: __import__("recover_corruption")
              .run_recovery_if_needed(ws, source_skill="cleanup", recurring=True))
    if isinstance(rec, dict) and rec.get("ran") and rec.get("quarantined_line_count"):
        actions.append(f"set aside {int(rec['quarantined_line_count'])} unreadable "
                       "activity-log lines")

    dk = run("dual_project_key", lambda: __import__("thread_writer")
             .repair_dual_project_key(ws, source_skill="cleanup"))
    if isinstance(dk, dict):
        for key in dualkey:
            try:
                dualkey[key] = int(dk.get(key) or 0)
            except (TypeError, ValueError):
                pass
        if dualkey["n_merged"] or dualkey["n_quarantined"]:
            actions.append("merged a duplicate project list")

    def _integrity():
        import integrity_check as ic

        counts: dict = {}
        for finding in ic.run_checks(ws):
            sev = str(getattr(finding, "severity", "") or "INFO")
            counts[sev] = counts.get(sev, 0) + 1
        return counts

    found = run("integrity_check", _integrity)
    if isinstance(found, dict):
        detail["integrity_findings"] = found
        if found.get("ERROR"):
            flagged.append(f"{found['ERROR']} record problems need a look")

    live = run("live_state", lambda: _live_state_pass(ws))
    if isinstance(live, dict):
        detail["live_state"] = live
        if live.get("refreshed"):
            actions.append(f"refreshed {live['refreshed']} project status pages")
        if live.get("errors"):
            flagged.append(f"{live['errors']} project status pages could not refresh")

    def _view(name, fn, label):
        out = run(name, fn)
        if isinstance(out, dict) and out.get("changed"):
            actions.append(f"rebuilt the {label}")

    import cleanup_actions as ca

    run("people_view", lambda: __import__("render_people_view").regenerate(ws))
    _view("decision_log", lambda: ca.regenerate_decision_log_if_changed(ws),
          "decision log")
    _view("master_tracker", lambda: ca.regenerate_master_tracker_if_changed(ws),
          "project tracker")
    _view("router_misses", lambda: ca.regenerate_router_misses_if_changed(ws),
          "routing corrections")
    stale = run("view_staleness", lambda: ca.check_analytical_view_staleness(ws))
    if stale:
        flagged.append("the weekly insight views are older than the record")
    aliases = run("aliases_staleness", lambda: ca.check_aliases_staleness(ws))
    if aliases:
        flagged.append("the name shortcuts list looks out of date")

    if not ran:
        return {"job_id": JOB_CLEANUP, "ok": False, "status": "not_written",
                "passes_run": [], "passes_failed": failed,
                "actions_taken": [], "items_flagged_for_user": []}
    status = "degraded" if failed else "complete"
    extra = {"actions_taken": actions, "items_flagged_for_user": flagged,
             "dualkey1_repair": dualkey, "scope": DOOR_SCOPE,
             "passes_run": ran, "passes_failed": failed,
             "passes_not_run": list(CLEANUP_NOT_ON_DOOR)}
    extra.update(detail)
    window = maintenance_window
    if not isinstance(window, dict):
        window = _window(ws, JOB_CLEANUP, _now_local(now))
    if isinstance(window, dict):
        from catchup import MAINTENANCE_WINDOW_FIELD

        extra[MAINTENANCE_WINDOW_FIELD] = window
    receipt = log_receipt(ws, JOB_CLEANUP, receipt_type="cleanup_run",
                          status=status, fired_via=via, triggered_by=trig,
                          extra_data=extra)
    return {"job_id": JOB_CLEANUP, "ok": True, "status": status,
            "passes_run": ran, "passes_failed": failed,
            "actions_taken": actions, "items_flagged_for_user": flagged,
            "receipt_type": (receipt or {}).get("type", "cleanup_run")}


# ---------------------------------------------------------------------------
# weekly insights
# ---------------------------------------------------------------------------

def _thread_refs(ev: dict) -> set:
    refs = set()
    primary = ev.get("primary_thread_id")
    if isinstance(primary, str) and primary:
        refs.add(primary)
    for key in ("related_thread_ids", "thread_ids"):
        for tid in ev.get(key) or []:
            if isinstance(tid, str) and tid:
                refs.add(tid)
    return refs


def _due_date(row: dict) -> Optional[_dt.date]:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    raw = data.get("due") or row.get("due")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return _dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _timeline_label(event_type) -> str:
    """A plain word for an event type: the timeline view is read by a
    person, so it never shows a raw type token."""
    word = str(event_type or "")
    if word.startswith("meeting"):
        return "meetings"
    if word in ("interaction", "email_drafted", "email_outcome"):
        return "conversations"
    if "commitment" in word:
        return "commitments"
    if "decision" in word:
        return "decisions"
    return "other updates"


def insight_projections(workspace_root, now=None) -> dict:
    """The five projections of the insight generator's Inputs table, computed
    from the org-scoped events and the entities file. Reads only."""
    ws = _ws(workspace_root)
    now = _now_local(now)
    today = now.date()
    events = _events(ws)
    ent = _entities(ws)
    people = {p.get("id"): p for p in _collection(ent, "people") if p.get("id")}
    threads = {t.get("id"): t for t in _collection(ent, "threads") if t.get("id")}
    dated = [(e, _event_local(e)) for e in events]
    dated = [(e, w) for e, w in dated if w is not None]
    first = min((w for _e, w in dated), default=None)
    history_days = (today - first.date()).days if first else 0

    # timeline: the last 30 days, per day
    since = today - _dt.timedelta(days=TIMELINE_DAYS)
    per_day: dict = {}
    for ev, when in dated:
        if when.date() > since:
            key = when.date().isoformat()
            per_day.setdefault(key, {})
            kind = _timeline_label(ev.get("type"))
            per_day[key][kind] = per_day[key].get(kind, 0) + 1
    timeline =[{"date": d, "counts": dict(sorted(c.items()))}
                for d, c in sorted(per_day.items(), reverse=True)]

    # relationship cadence: people seen at least three times, now quiet for
    # longer than twice their usual gap
    try:
        from primary_user import resolve_primary_user

        owner = resolve_primary_user(ws)
    except Exception:  # noqa: BLE001
        owner = None
    touches: dict = {}
    for ev, when in dated:
        if ev.get("type") not in ("interaction", "meeting"):
            continue
        for pid in ev.get("person_ids") or []:
            if pid in people and pid != owner:
                touches.setdefault(pid, []).append(when.date())
    relationships = []
    for pid, days in touches.items():
        days = sorted(set(days))
        if len(days) < 3:
            continue
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        usual = max(1, int(median(gaps)))
        quiet = (today - days[-1]).days
        if quiet >= STALE_MIN_DAYS and quiet > 2 * usual:
            relationships.append({"person_id": pid, "name": _name(people[pid]),
                                  "days_quiet": quiet, "usual_gap_days": usual,
                                  "last": days[-1].isoformat()})
    relationships.sort(key=lambda r: (-r["days_quiet"], r["name"]))

    # commitment aging: the confirmed half only (INTAKE)
    aging, unconfirmed = [], 0
    try:
        from cru_match import load_open_commitments, split_pending_review

        open_rows = load_open_commitments(ws / "_hq" / "data" / "events.jsonl",
                                          events=events, workspace_root=ws)
        confirmed, review = split_pending_review(open_rows)
        unconfirmed = len(review)
    except Exception:  # noqa: BLE001
        confirmed = []
    for row in confirmed:
        due = _due_date(row)
        if due is None or due >= today:
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        owner = people.get(data.get("owner_id")) or {}
        aging.append({"title": str(data.get("title") or data.get("summary") or ""),
                      "owner": _name(owner) if owner else "",
                      "due": due.isoformat(), "days_late": (today - due).days})
    aging.sort(key=lambda r: (-r["days_late"], r["title"]))

    # dormant threads and themes
    last_by_thread: dict = {}
    recent_by_thread: dict = {}
    theme_since = today - _dt.timedelta(days=THEME_DAYS)
    for ev, when in dated:
        for tid in _thread_refs(ev):
            if tid not in threads:
                continue
            if tid not in last_by_thread or when > last_by_thread[tid]:
                last_by_thread[tid] = when
            if when.date() > theme_since:
                recent_by_thread[tid] = recent_by_thread.get(tid, 0) + 1
    dormant, themes = [], []
    for tid, thread in threads.items():
        if thread.get("status") in ("archived", "resolved"):
            continue
        last = last_by_thread.get(tid)
        if thread.get("kind") == "theme":
            if recent_by_thread.get(tid):
                themes.append({"thread_id": tid, "name": _name(thread),
                               "mentions": recent_by_thread[tid]})
            continue
        if last is not None and (today - last.date()).days > DORMANT_DAYS:
            dormant.append({"thread_id": tid, "name": _name(thread),
                            "days_quiet": (today - last.date()).days,
                            "last": last.date().isoformat()})
    dormant.sort(key=lambda r: (-r["days_quiet"], r["name"]))
    themes.sort(key=lambda r: (-r["mentions"], r["name"]))
    return {"as_of": today.isoformat(), "history_days": history_days,
            "timeline": timeline, "relationships": relationships[:LIST_CAP],
            "commitment_aging": aging[:LIST_CAP],
            "commitment_aging_unconfirmed": unconfirmed,
            "dormant": dormant[:LIST_CAP], "themes": themes[:LIST_CAP]}


def plan_weekly_insights(workspace_root, now=None) -> dict:
    """The weekly-insights job PLANNED beside the data - READ ONLY (cr1#928;
    on `workspace_access.RUN_HELPER_ALLOWLIST`). The projections the session
    composes its sentences from, and whether there is enough history to.
    Writes nothing."""
    ws = _ws(workspace_root)
    now = _now_local(now)
    from catchup import MAINTENANCE_WINDOW_FIELD

    ready = _ledger_ready(ws)
    proj = insight_projections(ws, now=now) if ready else {}
    return {"job_id": JOB_INSIGHTS, "ready": ready,
            "enough_history": bool(proj) and proj.get("history_days", 0)
            >= MIN_HISTORY_DAYS,
            "history_days": proj.get("history_days", 0),
            "projections": {k: proj.get(k) for k in
                            ("relationships", "commitment_aging",
                             "commitment_aging_unconfirmed", "dormant", "themes")},
            "max_items": MAX_ITEMS,
            MAINTENANCE_WINDOW_FIELD: _window(ws, JOB_INSIGHTS, now)}


def _view_text(kind: str, proj: dict) -> str:
    head = f"# {INSIGHT_VIEWS[kind][:-3].replace('_', ' ').title()}\n\n"
    head += (f"_Generated {proj['as_of']} by the weekly insights job. A snapshot: "
             "the next run regenerates it from the record._\n\n")
    lines: list = []
    if kind == "timeline":
        for row in proj["timeline"]:
            parts = ", ".join(f"{n} {k}" for k, n in row["counts"].items())
            lines.append(f"- {row['date']}: {parts}")
    elif kind == "relationships":
        for r in proj["relationships"]:
            lines.append(f"- {r['name']}: quiet {r['days_quiet']} days "
                         f"(usually every {r['usual_gap_days']}), last {r['last']}")
    elif kind == "commitment_aging":
        for r in proj["commitment_aging"]:
            who = f" ({r['owner']})" if r["owner"] else ""
            lines.append(f"- {r['title']}{who}: due {r['due']}, "
                         f"{r['days_late']} days past")
        if proj.get("commitment_aging_unconfirmed"):
            lines.append("")
            lines.append(f"{proj['commitment_aging_unconfirmed']} unconfirmed - not "
                         "counted as open commitments above; say `needs your call`")
    elif kind == "dormant":
        for r in proj["dormant"]:
            lines.append(f"- {r['name']}: no movement for {r['days_quiet']} days "
                         f"(last {r['last']})")
    elif kind == "themes":
        for r in proj["themes"]:
            lines.append(f"- {r['name']}: {r['mentions']} mentions in the last "
                         f"{THEME_DAYS} days")
    return head + ("\n".join(lines) if lines else "Nothing to show this week.") + "\n"


def _computed_lines(proj: dict) -> list:
    out = []
    for r in proj["relationships"][:3]:
        out.append(f"You have not talked to {r['name']} in {r['days_quiet']} days, "
                   f"longer than your usual {r['usual_gap_days']}.")
    for r in proj["commitment_aging"][:3]:
        out.append(f"\"{r['title']}\" was due {r['due']} and is still open, "
                   f"{r['days_late']} days on.")
    for r in proj["dormant"][:3]:
        out.append(f"{r['name']} has had no movement in {r['days_quiet']} days.")
    return out


def _clean_items(items) -> list:
    out = []
    for item in items or []:
        text = item.get("text") if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            out.append(text.strip()[:MAX_ITEM_CHARS])
        if len(out) >= MAX_ITEMS:
            break
    return out


def insights_and_receipt(workspace_root, items=None, *, maintenance_window=None,
                         fired_via: Optional[str] = None,
                         triggered_by: Optional[str] = None,
                         now=None) -> dict:
    """Write the five views and the insights document beside the data and land
    ONE weekly-insights `pack_run` (cr1#928; on
    `workspace_access.RUN_WRITER_ALLOWLIST`).

    The projections are recomputed HERE from the record, never taken from
    the session: only `items` (the composed sentences, at most `MAX_ITEMS`)
    come from it, and with none the document carries the projections' own
    top lines. Under `MIN_HISTORY_DAYS` of history nothing is written but the
    receipt, with `skipped` saying why - the skill's own skip-not-fail
    posture."""
    from receipts import log_receipt, require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    ws = _ws(workspace_root)
    now = _now_local(now)
    via, trig = _run_mode(fired_via, triggered_by)
    extra: dict = {"scope": DOOR_SCOPE,
                   "passes_not_run": list(INSIGHTS_NOT_ON_DOOR),
                   "candidates_queued": 0}
    window = maintenance_window
    if not isinstance(window, dict):
        window = _window(ws, JOB_INSIGHTS, now)
    if isinstance(window, dict):
        from catchup import MAINTENANCE_WINDOW_FIELD

        extra[MAINTENANCE_WINDOW_FIELD] = window
    proj = insight_projections(ws, now=now)
    if proj["history_days"] < MIN_HISTORY_DAYS:
        extra["skipped"] = (f"not enough history yet ({proj['history_days']} of "
                            f"{MIN_HISTORY_DAYS} days)")
        log_receipt(ws, JOB_INSIGHTS, fired_via=via, triggered_by=trig,
                    extra_data=extra)
        return {"job_id": JOB_INSIGHTS, "ok": True, "status": "complete",
                "skipped": extra["skipped"], "views_written": []}

    written, failed = [], {}
    composed = _clean_items(items)
    computed = _computed_lines(proj)
    doc_path = ws / "_hq" / "insights" / f"{proj['as_of']}_insights.docx"
    doc_rel, refused = None, None
    try:
        doc_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 - the render below says so
        failed["insights_folder"] = type(exc).__name__
    if composed:
        # The session's sentences pass the document's own voice and leak
        # gates or they are not used: on a refusal the document is rendered
        # once more from the computed lines alone.
        try:
            doc_rel = _render_insights(ws, doc_path, proj,
                                       _insight_sections(composed, computed))
        except Exception as exc:  # noqa: BLE001 - said on the receipt
            refused = type(exc).__name__
    if doc_rel is None:
        try:
            doc_rel = _render_insights(ws, doc_path, proj,
                                       _insight_sections([], computed))
        except Exception as exc:  # noqa: BLE001
            failed["insights_document"] = type(exc).__name__
    if doc_rel is None:
        # cr1#928 review F2: any receipt of this job's types serves its slot,
        # whatever its status, and so does a fresh analytical view (the
        # watchdog reads their mtimes for this job). So the document renders
        # FIRST and a run whose document could not be written lands nothing:
        # no view, no receipt; the job stays owed and the fire scores it
        # failed.
        return {"job_id": JOB_INSIGHTS, "ok": False, "status": "not_written",
                "views_written": [], "output_path": None, "failed": failed}
    from atomic_write import atomic_write_text

    for kind, file_name in INSIGHT_VIEWS.items():
        try:
            atomic_write_text(ws / "_hq" / "views" / file_name, _view_text(kind, proj))
            written.append(file_name)
        except Exception as exc:  # noqa: BLE001
            failed[file_name] = type(exc).__name__
    used = composed if refused is None else []
    passes = ["timeline", "relationship_cadence", "commitment_aging", "dormant",
              "themes"] + (["composed"] if used else [])
    status = "complete" if not failed else "degraded"
    extra.update({"views_written": written, "passes_run": passes,
                  "n_items": len(used) or len(computed),
                  "composed_in_session": bool(used),
                  "composed_refused": refused,
                  "output_path": doc_rel, "failed": failed})
    log_receipt(ws, JOB_INSIGHTS, status=status, fired_via=via, triggered_by=trig,
                extra_data=extra)
    return {"job_id": JOB_INSIGHTS, "ok": True, "status": status,
            "views_written": written, "output_path": doc_rel, "failed": failed,
            "composed_refused": refused}


def _insight_sections(composed: list, computed: list) -> list:
    sections = []
    if composed:
        sections.append({"heading": "What's worth a closer look", "bullets": composed})
        if computed:
            sections.append({"heading": "Smaller observations", "bullets": computed})
    elif computed:
        sections.append({"heading": "What's worth a closer look", "bullets": computed})
    if not sections:
        sections.append({"heading": "What's on track",
                         "body": "Nothing in this week's record stands out: no "
                                 "relationship has gone unusually quiet, no "
                                 "confirmed commitment is past its date and every "
                                 "active project has moved this month."})
    return sections


def _render_insights(ws: Path, doc_path: Path, proj: dict, sections: list) -> str:
    from brief_writer import make_brief

    make_brief(str(doc_path), brief_kind="insights",
               title=f"Weekly insights, {proj['as_of']}",
               subtitle="What the week's record shows",
               sections=sections, workspace_root=str(ws))
    return _rel(ws, doc_path)


# ---------------------------------------------------------------------------
# monthly report
# ---------------------------------------------------------------------------

def _month_bounds(period: _dt.datetime) -> tuple:
    """The month a period (a 1st-of-month slot) closes: [start, end)."""
    end = _dt.datetime(period.year, period.month, 1)
    prev = end - _dt.timedelta(days=1)
    return _dt.datetime(prev.year, prev.month, 1), end


def _owed_periods(ws: Path, now: _dt.datetime) -> list:
    """The month slots the dispatcher owes, oldest first - the same rule as
    its due dict's `periods` (CATCHUP1), read here so the plan needs no
    argument the fire would have to copy across."""
    try:
        import maintenance_dispatcher as md
        from catchup import DEFAULT_PERIOD_CAP, missed_periods

        last = md.last_receipts(ws, [JOB_MONTHLY]).get(JOB_MONTHLY)
        periods = missed_periods(MONTHLY_CRON, last, now=now, cap=DEFAULT_PERIOD_CAP)
        if not periods:
            slots = md.expected_fires_multi(MONTHLY_CRON, now=now, count=1)
            periods = list(slots[:1])
    except Exception:  # noqa: BLE001
        periods = [_dt.datetime(now.year, now.month, 1)]
    out = []
    for p in periods:
        if isinstance(p, str):
            p = _dt.datetime.fromisoformat(p)
        if p.tzinfo is not None:
            p = p.astimezone().replace(tzinfo=None)
        out.append(_dt.datetime(p.year, p.month, 1))
    return sorted(set(out))


def _month_digest(events: list, threads: dict, start, end) -> dict:
    counts: dict = {}
    by_thread: dict = {}
    decisions = []
    for ev in events:
        when = _event_local(ev)
        if when is None or not (start <= when < end):
            continue
        kind = str(ev.get("type") or "event")
        counts[kind] = counts.get(kind, 0) + 1
        for tid in _thread_refs(ev):
            if tid in threads:
                by_thread[tid] = by_thread.get(tid, 0) + 1
        if kind == "decision":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            title = data.get("title") or data.get("summary") or data.get("decision")
            if isinstance(title, str) and title.strip():
                decisions.append({"date": when.date().isoformat(),
                                  "title": title.strip()[:200]})
    busiest = sorted(by_thread.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {"n_events": sum(counts.values()),
            "counts": dict(sorted(counts.items())),
            "busiest_projects": [{"name": _name(threads[t]), "events": n}
                                 for t, n in busiest],
            "decisions": decisions[:5]}


def plan_monthly_report(workspace_root, now=None) -> dict:
    """The monthly-report job PLANNED beside the data - READ ONLY (cr1#928; on
    `workspace_access.RUN_HELPER_ALLOWLIST`). One entry per month the
    dispatcher owes, oldest first, each with the digest the session writes
    the synthesis paragraph from. Writes nothing."""
    ws = _ws(workspace_root)
    now = _now_local(now)
    from catchup import MAINTENANCE_WINDOW_FIELD

    ready = _ledger_ready(ws)
    months = []
    if ready:
        events = _events(ws)
        threads = {t.get("id"): t for t in _collection(_entities(ws), "threads")
                   if t.get("id")}
        for period in _owed_periods(ws, now):
            start, end = _month_bounds(period)
            months.append({"period": period.isoformat(),
                           "label": start.strftime("%B %Y"),
                           "start": start.isoformat(), "end": end.isoformat(),
                           "digest": _month_digest(events, threads, start, end)})
    return {"job_id": JOB_MONTHLY, "ready": ready, "months": months,
            MAINTENANCE_WINDOW_FIELD: _window(ws, JOB_MONTHLY, now)}


def _operator_sections(ws: Path, lead: str, receipt: dict, start, end,
                       now: _dt.datetime) -> list:
    sections = []
    if lead:
        sections.append({"heading": "What the month meant", "body": lead})
    for section in receipt.get("sections") or []:
        if isinstance(section, dict):
            sections.append(section)
    try:
        from closure_index import pointer_coverage, pointer_coverage_line

        line = pointer_coverage_line(pointer_coverage(_events(ws),
                                                      since=start.isoformat()))
        if line:
            sections.append({"heading": "Traceable closes", "body": line})
    except Exception:  # noqa: BLE001 - an omitted line, never a guessed one
        pass
    try:
        from flow_measure import operator_block

        flow = operator_block(ws, since_iso=start.isoformat(),
                              now_iso=end.isoformat()) or {}
        if flow.get("text"):
            sections.append({"heading": "The flow", "body": flow["text"]})
    except Exception:  # noqa: BLE001
        pass
    return sections


def monthly_report_and_receipt(workspace_root, months=None, *,
                               maintenance_window=None,
                               fired_via: Optional[str] = None,
                               triggered_by: Optional[str] = None,
                               now=None) -> dict:
    """For each owed month, oldest first: the operating report document, then
    the value receipt (its own `value_receipt_generated`) and its document,
    then ONE `operator_report_generated` (cr1#928; on
    `workspace_access.RUN_WRITER_ALLOWLIST`).

    The months are the plan's own, recomputed here; from the session only
    each month's `synthesis_lead` (one paragraph) is taken, matched by
    `period`. Every number is computed beside the data.

    ORDER IS THE GUARANTEE (cr1#928 review F1). Any row of this job's types
    serves the month's slot whatever its status - the value receipt's own row
    included - so a month whose report failed after either row landed would
    never be owed again. So the operating report renders FIRST, from the
    value receipt's numbers previewed without a row
    (`value_receipt.preview_value_receipt`). A paragraph the document's own
    voice or leak gate refuses is dropped and the report renders once more
    from computed lines alone. Only when that also fails is the month not
    written: NOTHING lands for it, the run stops there (a later month's row
    would serve the earlier slot too), and the month and every one after it
    stay owed for the next fire."""
    from receipts import log_receipt, require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    ws = _ws(workspace_root)
    now = _now_local(now)
    via, trig = _run_mode(fired_via, triggered_by)
    leads = {}
    for entry in months or []:
        if isinstance(entry, dict) and isinstance(entry.get("synthesis_lead"), str):
            leads[str(entry.get("period") or "")[:7]] =                 entry["synthesis_lead"].strip()[:MAX_LEAD_CHARS]
    window = maintenance_window
    if not isinstance(window, dict):
        window = _window(ws, JOB_MONTHLY, now)
    from value_receipt import compute_value_receipt, preview_value_receipt

    out_dir = ws / "_hq" / "operator-reports"
    reports = []
    for period in _owed_periods(ws, now):
        start, end = _month_bounds(period)
        label = start.strftime("%B %Y")
        stem = start.strftime("%Y-%m")
        vr_path = out_dir / f"Value_Receipt_{stem}.docx"
        op_path = out_dir / f"{stem}.docx"
        failed: dict = {}
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            numbers = preview_value_receipt(ws, start.isoformat(), end.isoformat(),
                                            rollup="month") or {}
        except Exception as exc:  # noqa: BLE001
            failed["value_receipt"] = type(exc).__name__
            numbers = {}
        lead = leads.get(period.isoformat()[:7], "")
        op_rel, refused = None, None
        if lead:
            try:
                op_rel = _render_operator(ws, op_path, label, start, end,
                                          _operator_sections(ws, lead, numbers,
                                                             start, end, now))
            except Exception as exc:  # noqa: BLE001 - the gate's refusal
                refused = type(exc).__name__
        if op_rel is None:
            try:
                op_rel = _render_operator(ws, op_path, label, start, end,
                                          _operator_sections(ws, "", numbers,
                                                             start, end, now))
            except Exception as exc:  # noqa: BLE001
                failed["operator_report_document"] = type(exc).__name__
        if op_rel is None:
            reports.append({"period": period.isoformat(), "status": "not_written",
                            "output_path": None, "value_receipt_path": None,
                            "failed": failed})
            break
        vr_rel = None
        receipt: dict = {}
        try:
            receipt = compute_value_receipt(ws, start.isoformat(), end.isoformat(),
                                            rollup="month",
                                            output_path=str(vr_path)) or {}
        except Exception as exc:  # noqa: BLE001
            failed["value_receipt"] = type(exc).__name__
        if receipt.get("sections"):
            try:
                from brief_writer import make_brief

                make_brief(str(vr_path), brief_kind="value_receipt",
                           title=f"Value Receipt — {label}",
                           subtitle="Your operating layer, in numbers",
                           sections=receipt["sections"],
                           footer_text="Figures computed from your own workspace "
                                       "activity log.")
                vr_rel = _rel(ws, vr_path)
            except Exception as exc:  # noqa: BLE001
                failed["value_receipt_document"] = type(exc).__name__
        status = "degraded" if failed else "complete"
        used_lead = bool(lead) and refused is None
        extra = {"window": f"{start.isoformat()}..{end.isoformat()}",
                 "period": period.isoformat(),
                 "hours_estimate": (receipt or numbers).get("hours_estimate"),
                 "output_path": op_rel, "value_receipt_path": vr_rel,
                 "synthesis_lead": used_lead, "synthesis_lead_refused": refused,
                 "scope": DOOR_SCOPE, "failed": failed}
        if isinstance(window, dict):
            from catchup import MAINTENANCE_WINDOW_FIELD

            extra[MAINTENANCE_WINDOW_FIELD] = window
        log_receipt(ws, JOB_MONTHLY, receipt_type="operator_report_generated",
                    status=status, fired_via=via, triggered_by=trig,
                    extra_data=extra)
        reports.append({"period": period.isoformat(), "status": status,
                        "output_path": op_rel, "value_receipt_path": vr_rel,
                        "failed": failed, "synthesis_lead_refused": refused})
    ok = bool(reports) and all(r["status"] != "not_written" for r in reports)
    return {"job_id": JOB_MONTHLY, "ok": ok, "reports": reports}


def _render_operator(ws: Path, op_path: Path, label: str, start, end,
                     sections: list) -> str:
    from brief_writer import make_brief

    if not sections:
        sections = [{"heading": "This month",
                     "body": "This period was steady-state, with no recorded "
                             "activity to report."}]
    make_brief(str(op_path), brief_kind="operator_report",
               title=f"Operating report — {label}",
               subtitle=f"{start.date().isoformat()} to "
                        f"{(end - _dt.timedelta(days=1)).date().isoformat()}",
               sections=sections, workspace_root=str(ws))
    return _rel(ws, op_path)
