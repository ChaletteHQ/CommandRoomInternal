#!/usr/bin/env python3
"""
Late-fire detection + graceful degradation (Phase 3 Reliability, R4 — 2026-07).

Cowork fires a missed scheduled task at next app launch, hours or days
after its cron slot — a "Friday wrap + Monday preview" delivered Sunday is
stale, and pre-R4 nothing in the orchestrator knew it was late. Every chat
orchestrator calls `check_lateness()` at the top of its run (the shared
helper the spec prefers over per-orchestrator math) and branches on the
tier it returns.

TIER CONTRACT (thresholds tunable in LATENESS_TIERS — one shared constant):

  exempt   — silent task classes (SILENT_TASKS registry membership, never a
             hardcoded name list): late is fine, they always run in full.
             No banner, no degradation, no event.
  none     — < 3h late. Run normally, no mention.
  note     — 3h–24h late. Run normally; the output OPENS with the one
             plain-English `banner` line.
  degrade  — > 24h late. Do NOT render the full stale surface. The run
             still performs every substrate write the task owes (events,
             view updates) SILENTLY — invisible-write-loses-to-visible-
             deliverable is the Bug #98 class, so orchestrators keep those
             writes explicit — then posts only the one-line
             `degrade_notice`. The next morning brief reads events.jsonl,
             so no extra carry logic is needed beyond the writes landing.

TIME RULES: all lateness math is MACHINE-local (cron evaluates on the
machine clock — confirmed live 2026-07-01, machine=Mountain vs workspace=
Pacific). Workspace TZ is presentation-only: human-facing times in the
banner render via tz.py when resolvable, machine-local otherwise.

TELEMETRY: a `late_fire` event (registered in the Phase 1 vocabulary)
is appended through the append_event() gate on note/degrade tiers, so
cleanup / insight-generator can detect chronic patterns
(`detect_chronic_lateness`) and propose a better default time — the
proposal surfaces in the Monday note; the actual move goes through
change-schedule. Telemetry never blocks the run (append failures are
swallowed and reported in the return dict).
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_time import event_dt  # noqa: E402
from schedule_config import (  # noqa: E402
    CronParseError,
    is_silent_task,
    load_schedule_config,
    task_display_name,
)
from task_watchdog import expected_fires  # noqa: E402

# The R4 behavior tiers — ONE shared constant, tunable here and nowhere else.
LATENESS_TIERS = {
    "note": _dt.timedelta(hours=3),
    "degrade": _dt.timedelta(hours=24),
}

# Chronic-lateness proposal threshold: >24h-late fires in >=3 of the last 4
# weeks triggers the better-default-time proposal (cleanup Monday note).
CHRONIC_WINDOW_WEEKS = 4
CHRONIC_MIN_LATE_WEEKS = 3


def _now_local() -> _dt.datetime:
    return _dt.datetime.now()


def _human_time(dt_naive_local: _dt.datetime, workspace_root=None) -> str:
    """Presentation-only rendering: workspace TZ via tz.py when resolvable,
    machine-local wall time otherwise. Never raises."""
    aware = dt_naive_local.astimezone()  # attach the machine's tz
    try:
        from tz import to_local

        local = to_local(aware, workspace_path=workspace_root)
        if local is not None:
            aware = local
    except Exception:
        pass
    day = aware.strftime("%A")
    hour = aware.strftime("%I:%M %p").lstrip("0")
    if aware.minute == 0:
        hour = aware.strftime("%I %p").lstrip("0")
    return f"{hour} {day}"


def check_lateness(
    workspace_root,
    task_id: str,
    *,
    now: Optional[_dt.datetime] = None,
    emit: bool = True,
) -> dict:
    """Compute this fire's lateness tier. Call at the top of every chat
    orchestrator run, BEFORE any surface is rendered.

    Returns:
      {task, tier: exempt|none|note|degrade|unknown, lateness_minutes,
       scheduled_for (ISO, machine-local), banner (note tier),
       degrade_notice (degrade tier), event_logged (bool)}

    `unknown` (unparseable cron / no schedule entry) behaves like `none` —
    the fire runs normally; lateness detection is best-effort and must
    never block the primary output.
    """
    now = now or _now_local()
    out = {
        "task": task_id,
        "tier": "none",
        "lateness_minutes": 0,
        "scheduled_for": None,
        "banner": None,
        "degrade_notice": None,
        "event_logged": False,
    }
    if is_silent_task(task_id):
        out["tier"] = "exempt"
        return out

    try:
        entities = Path(workspace_root) / "_hq" / "data" / "entities.json"
        config = load_schedule_config(entities)
        spec = config.get(task_id)
        if not spec:
            out["tier"] = "unknown"
            return out
        fires = expected_fires(spec["cron"], now=now, count=1)
    except Exception:  # CronParseError, unreadable entities, anything — best-effort
        out["tier"] = "unknown"
        return out
    if not fires:
        out["tier"] = "unknown"
        return out

    scheduled = fires[0]
    lateness = now - scheduled
    out["scheduled_for"] = scheduled.isoformat()
    out["lateness_minutes"] = int(lateness.total_seconds() // 60)

    display = task_display_name(task_id)
    if lateness < LATENESS_TIERS["note"]:
        return out  # tier "none" — run normally, no mention

    when = _human_time(scheduled, workspace_root)
    if lateness < LATENESS_TIERS["degrade"]:
        out["tier"] = "note"
        out["banner"] = (
            f"This is your {display}, running late — it was scheduled for "
            f"{when} and the computer was likely asleep or closed then."
        )
    else:
        now_day = _human_time(now, workspace_root).split(" ", 2)[-1]
        out["tier"] = "degrade"
        out["degrade_notice"] = (
            f"Skipped the full {display} — it was scheduled for {when} and "
            f"it's now {now_day}. I've quietly saved everything it captures; "
            f"the next Morning Brief will fold in what mattered."
        )

    if emit:
        try:
            from event_gate import append_event

            append_event(
                Path(workspace_root) / "_hq" / "data" / "events.jsonl",
                {
                    "type": "late_fire",
                    "source_skill": task_id,
                    "data": {
                        "taskId": task_id,
                        "tier": out["tier"],
                        "lateness_minutes": out["lateness_minutes"],
                        "scheduled_for": out["scheduled_for"],
                    },
                },
                holder="late_fire",
            )
            out["event_logged"] = True
        except Exception:
            # Telemetry never blocks the fire (RELIABILITY.md core principle).
            out["event_logged"] = False
    return out


def detect_chronic_lateness(
    workspace_root,
    *,
    now: Optional[_dt.datetime] = None,
    window_weeks: int = CHRONIC_WINDOW_WEEKS,
    min_late_weeks: int = CHRONIC_MIN_LATE_WEEKS,
) -> list[dict]:
    """The late_fire consumer (cleanup Monday note / insight-generator).

    A task with >24h-late fires in >= min_late_weeks of the last
    window_weeks gets a better-default-time proposal. Proposal only —
    the actual move goes through change-schedule; existing users'
    customized crons are never touched from here.
    """
    now = now or _now_local()
    cutoff = now - _dt.timedelta(weeks=window_weeks)
    weeks_late: dict[str, set] = {}
    try:
        import events_io

        events = events_io.iter_events(workspace_root)
    except Exception:
        events = []
    for ev in events:
        if ev.get("type") != "late_fire":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("tier") != "degrade":
            continue
        dt = event_dt(ev)
        if dt is None:
            continue
        dt = dt.astimezone().replace(tzinfo=None)
        if dt < cutoff:
            continue
        tid = data.get("taskId") or ev.get("source_skill")
        if not tid:
            continue
        weeks_late.setdefault(tid, set()).add(dt.isocalendar()[:2])

    proposals = []
    for tid, weeks in sorted(weeks_late.items()):
        if len(weeks) >= min_late_weeks:
            display = task_display_name(tid)
            proposals.append({
                "task": tid,
                "late_weeks": len(weeks),
                "window_weeks": window_weeks,
                "line": (
                    f"Your {display} has run more than a day late "
                    f"{len(weeks)} of the last {window_weeks} weeks — usually the "
                    f"computer is off at its scheduled time. Want it earlier? "
                    f"Say 'change my schedule' and pick a time you're at the desk."
                ),
            })
    return proposals


__all__ = [
    "LATENESS_TIERS",
    "CHRONIC_WINDOW_WEEKS",
    "CHRONIC_MIN_LATE_WEEKS",
    "check_lateness",
    "detect_chronic_lateness",
]
