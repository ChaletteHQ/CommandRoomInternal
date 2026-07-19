#!/usr/bin/env python3
"""
Maintenance dispatcher — the due-jobs engine for the single `maintenance`
scheduled task (MAINT1, 2026-07).

WHY ONE TASK
------------
Every new taskId needs a manual Run Now per client machine (Cowork's one-time
permission gate), so every release that added a silent task created a
fleet-wide silent-failure risk: the task registers on update but never fires,
and nothing visible breaks (`task_watchdog.check_tasks`'s `never_authorized`
status exists only to detect it after the fact). The five silent tasks
(cleanup / reconcile-sent / monthly-report / weekly-insights / session-sweep)
now run as JOBS inside one `maintenance` task (cron `45 6,12,17 * * *`), and
any future silent job lands inside the already-authorized taskId — zero
client action, ever.

DUE-NESS IS CODE, NEVER LLM-JUDGED
----------------------------------
One uniform rule for every job, whatever its cadence: a job is DUE iff its
last receipt (via the v4.5.2 R1 receipt contract) is older than the most
recent nominal-cron slot <= now. No receipt ever = due. The registered
maintenance prompt calls `due_jobs()` and executes what it returns, in order —
it never decides due-ness from the prompt (the Bug #99 hand-rolled-arithmetic
class). "Due since last receipt" also beats fixed crons on reliability: a
laptop closed through Sunday evening means cleanup is still due at Monday
6:45 and runs then — missed work self-heals instead of skipping a cycle.

FAILURE CONTAINMENT = JOBS STAY DUE
-----------------------------------
Each job's success criterion is its OWN existing receipt validator
(validate_reconcile_ran, validate_sweep_ran, cleanup_run, the insights
pack_run, the report events) — the dispatcher never vouches for a job. A job
that fails or gets cut off writes no receipt, so it is still due at the next
fire: self-healing by construction. The dispatcher's own `maintenance_run`
audit event records jobs_due / jobs_completed / jobs_failed so the watchdog
and cleanup's Monday note can surface chronic failures.

ORDER IS THE CONTRACT
---------------------
`MAINTENANCE_JOBS` insertion order is execution order and is load-bearing:
reconcile-sent runs FIRST at the 6:45 slot so the 7:00 morning brief reads an
already-reconciled substrate (Bug #98-v3's original reason for the 6:45
anchor), and weekly-insights runs AFTER cleanup (synthesis wants a settled
substrate). Never parallelize the jobs.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from schedule_config import DEFAULT_SCHEDULES, CronParseError  # noqa: E402
from task_watchdog import (  # noqa: E402
    _now_local,
    _to_local_naive,
    expected_fires,
    last_receipts,
)


# The job registry — insertion order IS execution order (see module docstring).
# `nominal_cron` is each job's own cadence, evaluated by the uniform due rule;
# the task's actual fire slots come from DEFAULT_SCHEDULES["maintenance"].
# `skill` names what the registered prompt executes for the job (customer copy
# never shows these — job identity stays internal vocabulary).
MAINTENANCE_JOBS: dict[str, dict] = {
    # Weekday fires only (dow 1-5 in the nominal cron): due at every weekday
    # slot, exactly the pre-MAINT1 reconcile-sent cadence (SPEC-2.4).
    "reconcile-sent": {
        "skill": "reconcile-sent",
        "nominal_cron": "45 6,12,17 * * 1-5",
        "description": "close commitments completed by mail sent outside the product",
    },
    # Nominal midnight daily -> due once per day, served at the FIRST fire of
    # the day (6:45). Evening chats sweep the next morning, still BEFORE the
    # 7:00 brief, so the brief sees them.
    "session-sweep": {
        "skill": "session-sweep",
        "nominal_cron": "0 0 * * *",
        "description": "promote unlogged commitments and decisions from ad-hoc chats",
    },
    # Nominal Sunday 17:00 -> due at the Sunday 17:45 fire; still due Monday
    # 6:45 if the laptop was closed (the self-heal the old fixed cron lacked).
    "cleanup": {
        "skill": "cleanup",
        "nominal_cron": "0 17 * * 0",
        "description": "weekly workspace tidy + brain self-heal",
    },
    # Same Sunday slot as cleanup, ordered AFTER it: settled substrate first.
    "weekly-insights": {
        "skill": "insight-generator",
        "nominal_cron": "0 17 * * 0",
        "description": "recompute the analytical views from the settled week",
    },
    # LB1 — same Sunday slot, ordered after insights: the deal-signal
    # detector proposes over the settled week's events, so Monday's card
    # (and the Monday 9 AM Staff Meeting, where registered) opens with a
    # fresh queue. Entry point: deal_signal_detector.run_deal_signal_job —
    # detection + brain_proposals.propose(tier="confirm") only; nothing
    # mutates a deal until the user confirms through apply-choices.
    "deal-signals": {
        "skill": "deal-signal detector (shared/scripts/deal_signal_detector.py)",
        "nominal_cron": "0 17 * * 0",
        "description": "propose observed deal stage/value/creation changes for confirmation",
    },
    # Nominal midnight on the 1st -> due at the first fire on/after the 1st.
    "monthly-report": {
        "skill": "operator-report + value-receipt",
        "nominal_cron": "0 0 1 * *",
        "description": "monthly operating report + value receipt for the prior month",
    },
}

MAINTENANCE_TASK_ID = "maintenance"
RECEIPT_EVENT_TYPE = "maintenance_run"


def _job_overrides(workspace_root) -> dict:
    """Job-level overrides from entities.json
    `workspace.schedule_config.maintenance_jobs.<job_id>` — change-schedule can
    pause ONE job (`{"enabled": false}`) without touching the task. Defensive:
    missing/corrupt config means no overrides."""
    path = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    sc = ws.get("schedule_config") if isinstance(ws.get("schedule_config"), dict) else {}
    mj = sc.get("maintenance_jobs")
    return mj if isinstance(mj, dict) else {}


def dispatch_plan(workspace_root, now: Optional[_dt.datetime] = None) -> dict:
    """The full fire plan: which jobs are due (ordered), which were skipped by
    a job-level disable. Machine-local naive `now` (the clock cron evaluates
    in); defaults to the real clock.

    Returns {"now": iso, "due": [job dicts], "skipped_disabled": [job ids]}.
    Each due dict: {job_id, skill, description, reason, last_receipt (iso|None),
    slot (iso)}.
    """
    now = now or _now_local()
    if now.tzinfo is not None:
        now = _to_local_naive(now)
    overrides = _job_overrides(workspace_root)
    lasts = last_receipts(workspace_root, list(MAINTENANCE_JOBS))

    due: list[dict] = []
    skipped_disabled: list[str] = []
    for job_id, spec in MAINTENANCE_JOBS.items():
        override = overrides.get(job_id)
        if isinstance(override, dict) and override.get("enabled") is False:
            skipped_disabled.append(job_id)
            continue
        try:
            slots = expected_fires(spec["nominal_cron"], now=now, count=1)
        except CronParseError:
            continue  # unparseable registry cron — never crash a fire
        if not slots:
            continue
        slot = slots[0]
        last = lasts.get(job_id)
        if last is not None and last >= slot:
            continue  # already served this slot
        if last is None:
            reason = "no run recorded yet"
        else:
            reason = (
                f"last ran {last.isoformat()}, its {slot.isoformat()} slot has passed"
            )
        due.append({
            "job_id": job_id,
            "skill": spec["skill"],
            "description": spec["description"],
            "reason": reason,
            "last_receipt": last.isoformat() if last else None,
            "slot": slot.isoformat(),
        })
    return {
        "now": now.isoformat(),
        "due": due,
        "skipped_disabled": skipped_disabled,
    }


def due_jobs(workspace_root, now: Optional[_dt.datetime] = None) -> list[dict]:
    """The jobs due at this fire, in execution order (see MAINTENANCE_JOBS —
    order is the contract, never parallelize). A fire with nothing due is a
    fast no-op: write the maintenance_receipt with empty lists and exit."""
    return dispatch_plan(workspace_root, now=now)["due"]


def _job_ids(jobs: Optional[Iterable]) -> list[str]:
    """Normalize a jobs argument (ids or due-dicts) to a clean id list."""
    out: list[str] = []
    for j in jobs or ():
        if isinstance(j, dict):
            jid = j.get("job_id")
        else:
            jid = j
        if isinstance(jid, str) and jid.strip():
            out.append(jid.strip())
    return out


def maintenance_receipt(
    workspace_root,
    jobs_due: Optional[Iterable] = None,
    jobs_completed: Optional[Iterable] = None,
    jobs_failed: Optional[Iterable] = None,
    *,
    skipped_disabled: Optional[Iterable] = None,
    fired_via: str = "scheduled",
    now: Optional[_dt.datetime] = None,
) -> dict:
    """Append THE one `maintenance_run` audit event for this fire, via the
    locked append gate (receipts.log_receipt -> event_gate.append_event).

    This is the dispatcher's own receipt — it records what was due and what
    landed, and the watchdog reads it for task freshness. It is NOT a job
    receipt: a job counts as completed only when its own validator confirmed
    its own receipt; never list a job in jobs_completed without that.
    """
    now = now or _now_local()
    if now.tzinfo is not None:
        now = _to_local_naive(now)
    try:
        task_cron = DEFAULT_SCHEDULES[MAINTENANCE_TASK_ID]["cron"]
        slots = expected_fires(task_cron, now=now, count=1)
        fired_at_slot = slots[0].isoformat() if slots else None
    except (KeyError, CronParseError):
        fired_at_slot = None

    from receipts import log_receipt

    return log_receipt(
        workspace_root,
        MAINTENANCE_TASK_ID,
        receipt_type=RECEIPT_EVENT_TYPE,
        fired_via=fired_via,
        extra_data={
            "fired_at_slot": fired_at_slot,
            "jobs_due": _job_ids(jobs_due),
            "jobs_completed": _job_ids(jobs_completed),
            "jobs_failed": _job_ids(jobs_failed),
            "skipped_disabled": _job_ids(skipped_disabled),
        },
    )


def validate_maintenance_ran(
    workspace_root,
    since: Optional[_dt.datetime] = None,
) -> dict:
    """Read the newest `maintenance_run` audit event back and confirm the
    dispatcher actually ran (the Bug #98-v3 posture: enforcement binds to the
    substrate artifact, never a narrated sentence).

    `since` (naive = machine-local) restricts ok to an event newer than that
    instant — pass the pre-fire clock to confirm THIS fire's receipt landed.
    Returns {"ok": bool, "reason": str|None, "dt": iso|None, plus the payload
    lists when an event exists}.
    """
    from receipts import iter_receipts

    if since is not None and since.tzinfo is not None:
        since = _to_local_naive(since)

    newest = None
    newest_dt = None
    for r in iter_receipts(workspace_root, task_ids=[MAINTENANCE_TASK_ID]):
        if r["type"] != RECEIPT_EVENT_TYPE:
            continue
        dt_local = _to_local_naive(r["dt"]) if r["dt"] is not None else None
        if newest is None or (
            dt_local is not None and (newest_dt is None or dt_local > newest_dt)
        ):
            newest, newest_dt = r, dt_local
    if newest is None:
        return {"ok": False, "dt": None,
                "reason": "no maintenance_run audit event — the dispatcher did not run"}
    if since is not None and (newest_dt is None or newest_dt <= since):
        return {"ok": False, "dt": newest_dt.isoformat() if newest_dt else None,
                "reason": "newest maintenance_run audit event predates this fire"}
    data = newest["raw"].get("data") if isinstance(newest["raw"].get("data"), dict) else {}
    return {
        "ok": True,
        "reason": None,
        "dt": newest_dt.isoformat() if newest_dt else None,
        "fired_at_slot": data.get("fired_at_slot"),
        "jobs_due": data.get("jobs_due") or [],
        "jobs_completed": data.get("jobs_completed") or [],
        "jobs_failed": data.get("jobs_failed") or [],
        "skipped_disabled": data.get("skipped_disabled") or [],
    }


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: print the due-jobs plan for a workspace as JSON.

    python3 maintenance_dispatcher.py <workspace_root> [--now ISO]
    """
    import argparse

    parser = argparse.ArgumentParser(description="Command Room maintenance due-jobs plan")
    parser.add_argument("workspace_root", help="absolute path to the workspace root")
    parser.add_argument("--now", default=None,
                        help="frozen machine-local ISO datetime (testing/simulation)")
    args = parser.parse_args(argv)
    now = None
    if args.now:
        try:
            now = _dt.datetime.fromisoformat(args.now)
        except ValueError:
            print(json.dumps({"error": f"unparseable --now value: {args.now!r}"}))
            return 2
    plan = dispatch_plan(args.workspace_root, now=now)
    print(json.dumps(plan, indent=2))
    return 0


__all__ = [
    "MAINTENANCE_JOBS",
    "MAINTENANCE_TASK_ID",
    "RECEIPT_EVENT_TYPE",
    "dispatch_plan",
    "due_jobs",
    "maintenance_receipt",
    "validate_maintenance_ran",
]


if __name__ == "__main__":
    sys.exit(main())
