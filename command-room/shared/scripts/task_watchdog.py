#!/usr/bin/env python3
"""
Scheduled-task fired-recency watchdog (Phase 3 Reliability, W1 — 2026-07).

Scheduled tasks fail silently at four joints and no symptom reaches the
user: registration never happened (onboarding registers zero tasks by
design), silent tasks never cleared Cowork's first-fire permission gate,
the laptop slept through the fire window, or a platform fault (folder
rename, VHD cache, connector drop) broke the task path. A customer named
the purchase-driving pain verbatim on a 2026-06 call: "I don't know if
it's lagging or if dispatch forgot." This module is the detector for all
four.

DESIGN RULES (from the owning spec + Cowork decisions):

- **Enforcement binds to substrate artifacts (audit events), never emitted
  text** — the Bug #98 lesson generalized. The watchdog READS receipts
  (`pack_run` / `sent_reconcile` / `cleanup_run` / report events); it does
  not trust narration. Scheduler metadata (`lastRunAt` from
  `list_scheduled_tasks`), when the caller provides it, is used as a
  SECONDARY signal — a fresh `lastRunAt` with a stale receipt is its own
  finding (`receipt_gap`: the task fired but wrote nothing — the
  render-without-write class the R10 transcript self-audit chases).
- **Machine-local time everywhere.** Cowork cron/fireAt evaluate in
  MACHINE-local time, not workspace TZ (confirmed live 2026-07-01:
  machine=Mountain, workspace=Pacific). All lateness math here uses the
  machine clock; workspace TZ is presentation-only (`tz.py`).
- **One plain-English sentence per problem, only when something is wrong**
  (Rule 28 posture). `plain_english_lines()` is the single formatter every
  surface uses; `late` names sleep as the likely cause FIRST (W3 — a customer
  twice misdiagnosed lid-closed stalls as usage limits, 06-30/07-01).
- **The watchdog itself must be un-silent-killable:** it runs inside
  surfaces that already fire (morning-brief step, cleanup Monday note) plus
  the on-demand `system health` trigger (skills/system-health) — never as
  yet another silent task.

STATUSES per task:

  ok               — receipt within tolerance of the expected cadence.
  late             — fired before, then stopped: the last receipt misses
                     the two most recent expected fires (>=2 missed — one
                     missed fire is holiday/one-off tolerance, R5).
  never_fired      — registered, no receipt ever, registration recent
                     (< 3 weekdays): first fire simply hasn't landed yet.
  never_authorized — registered, no receipt ever, and >= 3 weekdays have
                     passed since registration: almost certainly the
                     Cowork first-fire permission gate was never cleared
                     (the silent-task ghost class W2's ritual closes).
  not_registered   — enabled in the merged schedule config but absent from
                     the registered set. Later-add tasks (not first-install)
                     are EXPECTED here and stay quiet — change-schedule R1
                     owns that render; first-install tasks missing from the
                     registered set are real breakage.

W5 (unlocked surfaces — status): the Tue/Thu `waiting-on chase` shipped
Phase 4 (2026-07-02) as orchestrator-commitments Phase 3.8 — its gates
(Stage D kinds split, Stage E counterparty receipts) merged, and it rides
the existing commitments task (nothing new registered). Still gated: the
day-1/week-1 lifecycle one-shots (cut in v4.1.0 for registration
unreliability) — safe to reintroduce as opt-ins once this watchdog can
verify fires. Nothing registers from this module.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_time import event_dt, parse_ts  # noqa: E402
from schedule_config import (  # noqa: E402
    DEFAULT_SCHEDULES,
    FIRST_INSTALL_TASK_IDS,
    SILENT_TASKS,
    CronParseError,
    load_schedule_config,
    parse_cron,
    task_display_name,
)

# Analytical views owned by insight-generator — weekly-insights' only
# durable receipt today is the view files themselves (the skill writes no
# audit event yet), so its receipt is the newest view mtime.
_INSIGHT_VIEWS = (
    "TIMELINE.md",
    "RELATIONSHIPS.md",
    "COMMITMENT_AGING.md",
    "DORMANT.md",
    "THEMES.md",
)

# Receipt spec per task. `types` are the substrate audit event types that
# count as "this task fired"; `match` values are accepted against the
# event's source_skill / data.kind / data.task_id / data.source_skill so
# legacy spellings (cr-* prefixes, kind underscores) keep matching forever.
# `views` marks a file-mtime fallback receipt (weekly-insights).
RECEIPT_SPECS: dict[str, dict] = {
    "morning-brief": {"types": {"pack_run"}, "match": {"morning-brief", "morning_brief", "cr-morning-brief", "morning-briefing"}},
    "upcoming-meetings": {"types": {"pack_run"}, "match": {"upcoming-meetings", "upcoming_meetings", "cr-upcoming-meetings"}},
    "inbox": {"types": {"pack_run"}, "match": {"inbox", "cr-inbox", "cr-inbox-pulse"}},
    "commitments": {"types": {"pack_run"}, "match": {"commitments", "cr-commitments"}},
    "pulse": {"types": {"pack_run", "pulse_run"}, "match": {"pulse", "dont_forget", "dont-forget", "cr-dont-forget", "cr-pulse"}},
    "past-meetings": {"types": {"pack_run"}, "match": {"past-meetings", "past_meetings", "cr-past-meetings"}},
    "friday-wrap": {"types": {"pack_run"}, "match": {"friday-wrap", "friday_wrap", "weekly-recap", "weekly_recap"}},
    "relationship-moves": {"types": {"pack_run"}, "match": {"relationship-moves", "relationship_moves"}},
    "commitment-triage": {"types": {"pack_run"}, "match": {"commitment-triage", "commitment_triage"}},  # Phase 2 Stage D (S4)
    "cleanup": {"types": {"cleanup_run", "audit_run"}, "match": None},
    "reconcile-sent": {"types": {"sent_reconcile"}, "match": None},
    "monthly-report": {"types": {"operator_report_generated", "value_receipt_generated"}, "match": None},
    "weekly-insights": {"types": set(), "match": None, "views": _INSIGHT_VIEWS},
    "session-sweep": {"types": {"session_sweep_run"}, "match": None},  # Phase 5 / R1 — receipt is the nightly sweep audit event
}

# The version stamp the bootloader template carries as of Phase 3 (W4).
# Registered prompts older than the stamp's introduction simply don't have
# one — reported as "unstamped", which is informational, not a failure.
_VERSION_STAMP_RE = re.compile(r"plugin-version:\s*v?([0-9][0-9A-Za-z.\-]*)")

_AUTHORIZATION_GRACE_WEEKDAYS = 3
_FIRE_GRACE = _dt.timedelta(minutes=90)  # dispatch jitter + long fires


def _now_local() -> _dt.datetime:
    """Naive machine-local now — the clock cron actually evaluates in."""
    return _dt.datetime.now()


def _to_local_naive(dt: Optional[_dt.datetime]) -> Optional[_dt.datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _iter_events(workspace_root) -> Iterable[dict]:
    try:
        import events_io

        yield from events_io.iter_events(workspace_root)
        return
    except Exception:
        pass
    # Defensive fallback — active file only, bad lines skipped.
    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict):
                    yield ev
    except OSError:
        return


def _event_matches_task(ev: dict, spec: dict) -> bool:
    if ev.get("type") not in spec["types"]:
        return False
    match = spec.get("match")
    if not match:
        return True
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    candidates = {
        ev.get("source_skill"),
        data.get("source_skill"),
        data.get("kind"),
        data.get("task_id"),
        data.get("taskId"),
    }
    return any(isinstance(c, str) and c in match for c in candidates)


def last_receipts(workspace_root, task_ids=None) -> dict[str, Optional[_dt.datetime]]:
    """Newest substrate receipt per task, machine-local naive datetimes.

    One pass over events.jsonl (shard-transparent when events_io is
    importable). weekly-insights uses the analytical-view mtime fallback.
    """
    task_ids = list(task_ids) if task_ids is not None else list(RECEIPT_SPECS)
    out: dict[str, Optional[_dt.datetime]] = {tid: None for tid in task_ids}
    event_tasks = [t for t in task_ids if RECEIPT_SPECS.get(t, {}).get("types")]
    if event_tasks:
        for ev in _iter_events(workspace_root):
            etype = ev.get("type")
            if not isinstance(etype, str):
                continue
            for tid in event_tasks:
                spec = RECEIPT_SPECS[tid]
                if etype in spec["types"] and _event_matches_task(ev, spec):
                    dt = _to_local_naive(event_dt(ev))
                    if dt and (out[tid] is None or dt > out[tid]):
                        out[tid] = dt
    for tid in task_ids:
        views = RECEIPT_SPECS.get(tid, {}).get("views")
        if views:
            newest = None
            views_dir = Path(workspace_root) / "_hq" / "views"
            for name in views:
                p = views_dir / name
                if p.exists():
                    try:
                        mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime)
                    except OSError:
                        continue
                    if newest is None or mtime > newest:
                        newest = mtime
            out[tid] = newest
    return out


def expected_fires(cron: str, now: Optional[_dt.datetime] = None, count: int = 2) -> list[_dt.datetime]:
    """The most recent `count` scheduled fire datetimes <= now, newest first.

    Machine-local math via schedule_config.parse_cron — no new dependency
    and no coupling to config writes. Walks back day-by-day (bounded), so
    weekly and monthly crons resolve without minute-stepping.
    """
    now = now or _now_local()
    minute_set, hour_set, dom_set, month_set, dow_set = parse_cron(cron)
    fires: list[_dt.datetime] = []
    day = now.date()
    for _ in range(0, 800):  # bound: > 2 years covers any sane cadence
        cron_dow = (day.weekday() + 1) % 7  # python Mon=0 → cron Sun=0
        if day.month in month_set and day.day in dom_set and cron_dow in dow_set:
            for h in sorted(hour_set, reverse=True):
                for m in sorted(minute_set, reverse=True):
                    candidate = _dt.datetime.combine(day, _dt.time(h, m))
                    if candidate <= now:
                        fires.append(candidate)
                        if len(fires) >= count:
                            return fires
        day -= _dt.timedelta(days=1)
    return fires


def _weekdays_since(start: _dt.datetime, now: _dt.datetime) -> int:
    days, d = 0, start.date()
    while d < now.date():
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def read_workspace_config(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "workspace_config.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def check_tasks(
    workspace_root,
    *,
    now: Optional[_dt.datetime] = None,
    registered_ids: Optional[set] = None,
    task_records: Optional[list] = None,
    config: Optional[dict] = None,
) -> list[dict]:
    """The W1 report: one dict per enabled task.

    Args:
      registered_ids: the registered-task set. Defaults to
        workspace_config.json `registered_taskIds` (the offline-first record
        registration maintains); pass the taskIds from
        `list_scheduled_tasks` when the caller has them (they're fresher).
      task_records: optional raw records from `list_scheduled_tasks`
        (dicts with taskId / lastRunAt / enabled / prompt). Used for the
        secondary lastRunAt signal + receipt_gap detection.

    Returns dicts:
      {task, display_name, status, silent, last_fired (ISO|None),
       expected (ISO|None), last_run_at (ISO|None), receipt_gap (bool),
       registered (bool)}
    """
    now = now or _now_local()
    ws_config = read_workspace_config(workspace_root)
    if registered_ids is None:
        raw = ws_config.get("registered_taskIds")
        registered_ids = set(raw) if isinstance(raw, list) else set()
    records_by_id = {}
    for rec in task_records or []:
        if isinstance(rec, dict) and rec.get("taskId"):
            records_by_id[rec["taskId"]] = rec
    if config is None:
        entities = Path(workspace_root) / "_hq" / "data" / "entities.json"
        config = load_schedule_config(entities)

    enabled = {tid: spec for tid, spec in config.items() if spec.get("enabled")}
    receipts = last_receipts(workspace_root, enabled.keys())
    registered_at = parse_ts(ws_config.get("registered_at") or "")
    registered_at = _to_local_naive(registered_at)

    reports = []
    for tid, spec in enabled.items():
        rec = records_by_id.get(tid)
        is_registered = tid in registered_ids or rec is not None
        last_fired = receipts.get(tid)
        last_run_at = _to_local_naive(parse_ts((rec or {}).get("lastRunAt") or ""))
        try:
            recent = expected_fires(spec["cron"], now=now, count=2)
        except CronParseError:
            recent = []
        expected_latest = recent[0] if recent else None
        # Effective last fire = strongest available signal for cadence math;
        # the receipt stays authoritative for "did it actually do its job".
        effective = max(d for d in (last_fired, last_run_at) if d is not None) if (last_fired or last_run_at) else None

        if not is_registered:
            status = "not_registered"
        elif effective is None:
            grace_start = registered_at or (now - _dt.timedelta(days=1))
            if _weekdays_since(grace_start, now) >= _AUTHORIZATION_GRACE_WEEKDAYS:
                status = "never_authorized"
            else:
                status = "never_fired"
        elif len(recent) >= 2 and effective < recent[1] - _FIRE_GRACE:
            # Missed BOTH of the two most recent expected fires (>=2 missed
            # = R5 threshold; one missed fire is holiday tolerance).
            status = "late"
        else:
            status = "ok"

        receipt_gap = bool(
            last_run_at is not None
            and expected_latest is not None
            and (last_fired is None or last_fired < last_run_at - _FIRE_GRACE)
            and RECEIPT_SPECS.get(tid, {}).get("types")
        )

        reports.append({
            "task": tid,
            "display_name": task_display_name(tid),
            "status": status,
            "silent": tid in SILENT_TASKS,
            "first_install": tid in FIRST_INSTALL_TASK_IDS,
            "registered": is_registered,
            "last_fired": last_fired.isoformat() if last_fired else None,
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "expected": expected_latest.isoformat() if expected_latest else None,
            "receipt_gap": receipt_gap,
        })
    return reports


def check_schedule_parity(workspace_root, registered_ids=None) -> dict:
    """R2 schedule-parity check (cleanup's weekly pass). Detect + report —
    NO config writes; `schedule_config` stays a sparse override store.

    Mismatch classes:
      ghost_first_install — enabled first-install task absent from the
        registered set: real breakage, flag in the Monday note.
      ghost_later_add     — enabled later-add task not registered: EXPECTED
        (deliberately not first-install) — say nothing; R3's proposal step
        owns the nudge.
      orphan_overrides    — schedule_config entries for taskIds that exist
        in neither DEFAULT_SCHEDULES nor the registered set (e.g. legacy
        cr-* keys): flag in the Monday note. Removal would be the only
        heal and cleanup never removes — flag-only (R2 reframed; the
        original densify-heal died with old-R1).
    """
    from schedule_config import DEFAULT_SCHEDULES, load_schedule_view

    ws_config = read_workspace_config(workspace_root)
    if registered_ids is None:
        raw = ws_config.get("registered_taskIds")
        registered_ids = set(raw) if isinstance(raw, list) else set()
    registered_ids = set(registered_ids)

    entities = Path(workspace_root) / "_hq" / "data" / "entities.json"
    view = load_schedule_view(entities, registered_ids)
    ghosts_first, ghosts_later = [], []
    for tid, spec in view.items():
        if spec["enabled"] and not spec["registered"]:
            (ghosts_later if spec["later_add"] else ghosts_first).append(tid)

    orphans = []
    try:
        data = json.loads(entities.read_text(encoding="utf-8"))
        overrides = ((data.get("workspace") or {}).get("schedule_config") or {})
        for tid in overrides:
            if tid not in DEFAULT_SCHEDULES and tid not in registered_ids:
                orphans.append(tid)
    except (OSError, json.JSONDecodeError):
        pass

    return {
        "ghost_first_install": sorted(ghosts_first),
        "ghost_later_add": sorted(ghosts_later),
        "orphan_overrides": sorted(orphans),
    }


def check_workspace_binding(workspace_root) -> Optional[dict]:
    """The folder-rename case (JS 07-01): the stored binding no longer
    matches reality. Returns a finding dict, or None when the binding is
    healthy / no binding is recorded yet."""
    ws_config = read_workspace_config(workspace_root)
    stored = (ws_config.get("workspace_basename") or "").strip()
    if not stored:
        return None
    actual = Path(workspace_root).name
    if stored == actual:
        return None
    return {
        "check": "workspace_binding",
        "stored_basename": stored,
        "actual_basename": actual,
        "fix": "re-run `set up command room schedules` to re-bind",
    }


def check_prompt_versions(task_records, installed_version: str) -> list[dict]:
    """W4 stale-prompt drift: compare each registered prompt's stamped
    plugin version against the installed one. Unstamped prompts predate the
    stamp — reported as informational (`stamped: False`)."""
    findings = []
    for rec in task_records or []:
        if not isinstance(rec, dict):
            continue
        tid = rec.get("taskId")
        prompt = rec.get("prompt") or ""
        m = _VERSION_STAMP_RE.search(prompt)
        if not m:
            findings.append({"task": tid, "stamped": False, "stale": False})
        elif installed_version and m.group(1) != installed_version.lstrip("v"):
            findings.append({
                "task": tid, "stamped": True, "stale": True,
                "prompt_version": m.group(1), "installed_version": installed_version,
            })
    return findings


def plain_english_lines(reports, *, binding=None, include_ok: bool = False) -> list[str]:
    """One sentence per problem — the ONLY watchdog voice any surface uses.

    Sleep is named first on `late` (W3): a lid-closed laptop is the most
    common cause and customers read it as "Claude broke". No jargon, no
    taskIds, no event names — display names + the exact next action.
    """
    lines: list[str] = []
    if binding:
        lines.append(
            "Your workspace folder looks like it was renamed (I have it as "
            f"'{binding['stored_basename']}', but the folder is '{binding['actual_basename']}') — "
            "say 'set up command room schedules' once and I'll re-bind everything."
        )
    for r in reports:
        name = r["display_name"]
        if r["status"] == "never_authorized":
            lines.append(
                f"Your {name} task has never run — it's likely still waiting on its one-time "
                f"permission. Open it in the Scheduled section and press Run Now once."
            )
        elif r["status"] == "late":
            since = (r["last_fired"] or r["last_run_at"] or "")[:10]
            since_phrase = f" since {since}" if since else ""
            lines.append(
                f"Your {name} task hasn't run{since_phrase} — usually that means the computer "
                f"was asleep or closed when it was due. Open it and press Run Now to catch up "
                f"(and consider setting your computer to stay awake during work hours)."
            )
        elif r["status"] == "not_registered" and r["first_install"]:
            lines.append(
                f"Your {name} task is missing from the schedule — say "
                f"'set up command room schedules' to restore it."
            )
        elif r["receipt_gap"] and r["status"] == "ok":
            lines.append(
                f"Your {name} task ran but didn't record its work — worth opening it once to "
                f"check the last run looks right."
            )
        elif include_ok and r["status"] == "ok":
            lines.append(f"{name}: running on schedule.")
    return lines


__all__ = [
    "RECEIPT_SPECS",
    "check_tasks",
    "check_schedule_parity",
    "check_workspace_binding",
    "check_prompt_versions",
    "expected_fires",
    "last_receipts",
    "plain_english_lines",
    "read_workspace_config",
]
