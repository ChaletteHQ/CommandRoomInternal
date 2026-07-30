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
- **Machine-local time everywhere — INCLUDING WHAT IS RENDERED.** Cowork
  cron/fireAt evaluate in MACHINE-local time, not workspace TZ (confirmed
  live 2026-07-01: machine=Mountain, workspace=Pacific). All lateness math
  here uses the machine clock, and so does every string `_human_time`
  emits. "Workspace TZ is presentation-only" used to be read as "present in
  workspace TZ" and it is NOT (LATETZ, 2026-07-28): conversion happens ONCE,
  at registration/change time, so a value arriving here is already converted
  and a second hop moves it into a clock it was never authored in. `tz.py`
  is for upstream CONNECTOR timestamps, which is a different problem.
- **One plain-English sentence per problem, only when something is wrong**
  (Rule 28 posture). `plain_english_lines()` is the single formatter every
  surface uses. Lines state FACTS + the one action — never a fabricated
  cause (v4.5.2 R3, superseding W3's sleep-first wording: the dogfood
  logged four invented "computer was likely asleep" narratives in one day,
  F-10/F-43/F-47; the generic common-causes education lives in
  system-health's self-serve list, framed as possibilities).
- **The watchdog itself must be un-silent-killable:** it runs inside
  surfaces that already fire (morning-brief step, cleanup Monday note) plus
  the on-demand `system health` trigger (skills/system-health) — never as
  yet another silent task.

TRUTH RULES (v4.5.2 R3 — F-43 P1a/P1b/P2c, F-40, the F-10 lie catalog):

- **Never assert fire history without a receipt.** A registered task with
  zero receipts is `never_fired` ("hasn't had its first run yet — next fire
  is [time]"), never part of "ran on their normal schedule" (F-43 P1a
  invented run history for two tasks registered that morning).
- **Late serves are read, not ignored.** `late_signals()` reads the
  `late_fire` events + `fired_via`/`late_tier` receipt fields R2 writes; a
  task whose newest fire was a catch-up carries `caught_up: True` and is
  reported AS a dated catch-up ("caught up Wednesday 12:20 AM"), never as
  "normal schedule" (F-43 P1b/P2c).
- **Internal consistency is code, not prose.** `health_verdict()` partitions
  every task into exactly ONE bucket (problem / caught-up / first-run-
  pending / on-schedule) and computes the summary counts from the
  partition — a task in any warning can never simultaneously count inside
  "everything's running" (F-43's self-contradiction).
- **Vantage before verdict (F-40).** An empty scheduler registry with a
  substrate full of registration history + run receipts means THIS CHAT
  CANNOT SEE THE SCHEDULER (cloud/remote session, or another machine) —
  `detect_registry_vantage()` returns that finding and the verdict becomes
  "I can't see your scheduler from this chat", never the false total-outage
  "nothing is registered" (whose named fix would double-register
  everything).

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

# Analytical views owned by insight-generator — weekly-insights' pre-v4.5.2
# fires wrote no audit event, so the view-file mtimes remain a FALLBACK
# freshness signal forever (append-only history: old installs never gain
# receipts retroactively). v4.5.2+ fires also write a pack_run receipt.
_INSIGHT_VIEWS = (
    "TIMELINE.md",
    "RELATIONSHIPS.md",
    "COMMITMENT_AGING.md",
    "DORMANT.md",
    "THEMES.md",
)

# Receipt spec per task — DERIVED from the receipt contract
# (`shared/scripts/receipts.py`, v4.5.2 R1), the single source of truth for
# receipt types + legacy-spelling normalization. This dict keeps the
# watchdog's historical shape for its consumers; the matcher itself is
# `receipts.receipt_task_id` (all legacy spellings — cr-* prefixes,
# underscore kinds, kind-only payloads — parse forever).
# `views` marks the file-mtime fallback receipt (weekly-insights).
from receipts import (  # noqa: E402
    RECEIPT_TYPES as _RECEIPT_TYPES,
    get_late_tier as _get_late_tier,
    last_receipt_times as _last_receipt_times,
    normalize_fired_via as _normalize_fired_via,
    normalize_task_id as _normalize_task_id,
    receipt_task_id as _receipt_task_id,
)

RECEIPT_SPECS: dict[str, dict] = {
    tid: {"types": set(spec["types"]), "match": None}
    for tid, spec in _RECEIPT_TYPES.items()
}
RECEIPT_SPECS["weekly-insights"]["views"] = _INSIGHT_VIEWS

# MAINT1: the five pre-MAINT1 silent taskIds live on in RECEIPT_SPECS as JOB
# ids — check_tasks no longer reports them as tasks (they left
# DEFAULT_SCHEDULES), but check_maintenance_jobs reads their receipts per-job
# against the nominal crons in maintenance_dispatcher.MAINTENANCE_JOBS.

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
    """Back-compat shim — the matcher is the receipt contract's
    (`receipts.receipt_task_id`), which parses every legacy shape forever."""
    if ev.get("type") not in spec["types"]:
        return False
    tid = _receipt_task_id(ev)
    return tid is not None and spec is RECEIPT_SPECS.get(tid, spec)


def last_receipts(workspace_root, task_ids=None) -> dict[str, Optional[_dt.datetime]]:
    """Newest substrate receipt per task, machine-local naive datetimes.

    Delegates to the shared receipt reader (`receipts.last_receipt_times`,
    v4.5.2 R1 — one pass, shard-transparent, all legacy shapes). weekly-
    insights ALSO checks the analytical-view mtime fallback: pre-v4.5.2
    fires left no audit event, so the newest of {receipt, view mtime} wins.
    """
    task_ids = list(task_ids) if task_ids is not None else list(RECEIPT_SPECS)
    event_tasks = [t for t in task_ids if RECEIPT_SPECS.get(t, {}).get("types")]
    out: dict[str, Optional[_dt.datetime]] = {tid: None for tid in task_ids}
    if event_tasks:
        try:
            found = _last_receipt_times(workspace_root, event_tasks)
        except Exception:
            found = {}
        for tid, dt in found.items():
            if tid in out and dt is not None:
                out[tid] = _to_local_naive(dt)
    for tid in task_ids:
        views = RECEIPT_SPECS.get(tid, {}).get("views")
        if views:
            newest = out.get(tid)
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


def next_fire(cron: str, now: Optional[_dt.datetime] = None) -> Optional[_dt.datetime]:
    """The next scheduled fire datetime strictly after `now` — the forward
    mirror of expected_fires. Machine-local math, same bounded day walk.

    R3 consumer: the `never_fired` render ("hasn't had its first run yet —
    next fire is [time]") must name a real upcoming time, never invent a
    past one."""
    now = now or _now_local()
    minute_set, hour_set, dom_set, month_set, dow_set = parse_cron(cron)
    day = now.date()
    for _ in range(0, 800):  # bound: > 2 years covers any sane cadence
        cron_dow = (day.weekday() + 1) % 7  # python Mon=0 → cron Sun=0
        if day.month in month_set and day.day in dom_set and cron_dow in dow_set:
            for h in sorted(hour_set):
                for m in sorted(minute_set):
                    candidate = _dt.datetime.combine(day, _dt.time(h, m))
                    if candidate > now:
                        return candidate
        day += _dt.timedelta(days=1)
    return None


def _human_time(
    dt_naive: _dt.datetime,
    now: Optional[_dt.datetime] = None,
) -> str:
    """Presentation-only: 'Wednesday 12:20 AM' when within ~6 days of now
    (past or future), 'Jul 2, 12:20 AM' beyond that — a bare weekday would
    be ambiguous across weeks. Renders the MACHINE-local naive value as-is —
    the watchdog's clock (`late_signals`), and the clock cron evaluates in.
    Never raises.

    NO WORKSPACE-TZ CONVERSION HAPPENS HERE, DELIBERATELY (LATETZ,
    2026-07-28). This is the sibling rail of `late_fire._human_time`, and it
    carried the identical defect: it attached the machine zone with
    `.astimezone()` and then re-expressed the value in the workspace zone via
    `tz.to_local()`. Every value reaching it is ALREADY machine-local naive
    (`_to_local_naive` at the `records()` boundary, then round-tripped through
    `.isoformat()`), so that second hop moved a slot into a clock it was never
    authored in — a no-op only where machine tz == workspace tz. See
    `late_fire._human_time` for the full reasoning and the governing rule
    ("conversion happens once, at registration/change time").

    Two further bugs died with it, both from the old version keeping the
    branch decision and the rendered string on DIFFERENT clocks:
      - `abs(ref - dt_naive)` compared a naive `ref` against `dt_naive`, which
        the old line 306 left AWARE whenever a caller passed an aware value —
        a TypeError in a function documented to never raise. Not reachable
        from today's call sites (they all serialize naive values), but it was
        one aware caller away.
      - the ~6-day window was decided on the machine clock while the weekday
        was rendered on the workspace clock, so near a date boundary the two
        could disagree — "Wednesday" on a row the window had judged as far.
    """
    now = now or _now_local()
    # ONE clock for both the branch decision and the rendered string.
    if dt_naive.tzinfo is not None:
        dt_naive = dt_naive.astimezone().replace(tzinfo=None)
    ref = now if now.tzinfo is None else now.astimezone().replace(tzinfo=None)
    clock = dt_naive.strftime("%I:%M %p").lstrip("0")
    if abs(ref - dt_naive) < _dt.timedelta(days=6):
        return f"{dt_naive.strftime('%A')} {clock}"
    return f"{dt_naive.strftime('%b')} {dt_naive.day}, {clock}"


def late_signals(workspace_root, task_ids=None) -> dict[str, dict]:
    """One defensive substrate pass for the late-serve evidence the truth
    rules read (v4.5.2 R3 — F-43 P1b/P2c). Per task:

      receipt_dt / fired_via / late_tier — the task's NEWEST receipt with
        the run mode + lateness tier it carried (all legacy spellings
        normalized by the R1 contract).
      late_fire — the task's newest `late_fire` event (written by
        late_fire.check_lateness on note/degrade tiers):
        {dt, tier, lateness_minutes, scheduled_for}.

    Machine-local naive datetimes throughout (the watchdog's clock).
    """
    wanted = None
    if task_ids is not None:
        wanted = {_normalize_task_id(t) for t in task_ids}
    out: dict[str, dict] = {}

    def _slot(tid: str) -> dict:
        return out.setdefault(tid, {
            "receipt_dt": None, "fired_via": None, "late_tier": None,
            "late_fire": None,
        })

    for ev in _iter_events(workspace_root):
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "late_fire":
            tid = _normalize_task_id(
                data.get("taskId") or data.get("task_id") or ev.get("source_skill")
            )
            if not isinstance(tid, str) or (wanted is not None and tid not in wanted):
                continue
            dt = _to_local_naive(event_dt(ev))
            if dt is None:
                continue
            s = _slot(tid)
            prev = s["late_fire"]
            if prev is None or dt > prev["dt"]:
                lm = data.get("lateness_minutes")
                tier = data.get("tier")
                sched = data.get("scheduled_for")
                s["late_fire"] = {
                    "dt": dt,
                    "tier": tier if isinstance(tier, str) else None,
                    "lateness_minutes": lm if isinstance(lm, int) else None,
                    "scheduled_for": sched if isinstance(sched, str) else None,
                }
            continue
        tid = _receipt_task_id(ev)
        if tid is None or (wanted is not None and tid not in wanted):
            continue
        dt = _to_local_naive(event_dt(ev))
        if dt is None:
            continue
        s = _slot(tid)
        if s["receipt_dt"] is None or dt > s["receipt_dt"]:
            s["receipt_dt"] = dt
            s["fired_via"] = _normalize_fired_via(data.get("fired_via"))
            s["late_tier"] = _get_late_tier(data)
    return out


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
    except (OSError, json.JSONDecodeError) as e:
        # FS-15 — a workspace_config.json that EXISTS but won't read makes
        # every registered task look unregistered (the probable mechanism of
        # the June workspace_config truncation). Keep the {} fallback (the
        # watchdog must not crash the brief) but record the degradation so
        # the brief / system-health surface it loudly. Alarm recording is
        # best-effort: a broken read_alarm module must not turn this
        # degraded read into a hard failure.
        try:
            from read_alarm import record_read_alarm
            record_read_alarm(p, e, reader="task_watchdog")
        except Exception:
            pass
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
       expected (ISO|None), next_fire (ISO|None), last_run_at (ISO|None),
       receipt_gap (bool), registered (bool), last_fired_via (str|None),
       caught_up (bool), catchup (dict|None)}

    `caught_up` (R3 — F-43 P1b/P2c): the task's NEWEST fire served its slot
    late — a `catchup` fired_via / note-or-degrade `late_tier` on the newest
    receipt, or a `late_fire` event written alongside it. Cadence-wise the
    task is not broken (it fired), but it did NOT run "on its normal
    schedule" and every render must say so, with dates (`catchup` carries
    fired_at / scheduled_for / tier / lateness_minutes).
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
    try:
        signals = late_signals(workspace_root, enabled.keys())
    except Exception:
        signals = {}
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
        # Receipts are the ONLY served/not-served truth (v4.5.2 R2). The
        # pre-R2 code took max(receipt, lastRunAt) here, which let a
        # stamped-but-never-executed "run" read as on-schedule forever —
        # the 2026-07-08 cleanup autopsy proved lastRunAt lands without
        # execution (F-39: 9 tasks stamped at app launch, ONE receipt).
        # lastRunAt stays a SECONDARY signal via receipt_gap below.
        effective = last_fired

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

        # Late-serve detection (R3 — F-43 P1b/P2c): does the NEWEST fire
        # evidence say this slot was served late? Signals only count when
        # they belong to the newest fire — an old late_fire with a normal
        # receipt after it is history, not a current finding. `last_fired`
        # can come from the view-mtime fallback (weekly-insights), so the
        # receipt-borne signals are gated on the receipt actually BEING the
        # newest fire (within dispatch grace).
        sig = signals.get(tid) or {}
        lf = sig.get("late_fire")
        last_fired_via = None
        caught_up = False
        catchup_info = None
        if last_fired is not None:
            receipt_is_newest = (
                sig.get("receipt_dt") is not None
                and abs(sig["receipt_dt"] - last_fired) <= _FIRE_GRACE
            )
            if receipt_is_newest:
                last_fired_via = sig.get("fired_via")
            lf_is_newest = bool(
                lf and lf.get("dt") is not None
                and abs(lf["dt"] - last_fired) <= _FIRE_GRACE
            )
            if status == "ok" and (
                (receipt_is_newest and sig.get("fired_via") == "catchup")
                or (receipt_is_newest and sig.get("late_tier") in ("note", "degrade"))
                or lf_is_newest
            ):
                caught_up = True
                catchup_info = {
                    "fired_at": last_fired.isoformat(),
                    "scheduled_for": (lf or {}).get("scheduled_for"),
                    "tier": (lf or {}).get("tier")
                            or (sig.get("late_tier") if receipt_is_newest else None),
                    "lateness_minutes": (lf or {}).get("lateness_minutes"),
                }

        try:
            upcoming = next_fire(spec["cron"], now=now)
        except CronParseError:
            upcoming = None

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
            "next_fire": upcoming.isoformat() if upcoming else None,
            "receipt_gap": receipt_gap,
            "last_fired_via": last_fired_via,
            "caught_up": caught_up,
            "catchup": catchup_info,
        })
    return reports


def check_maintenance_jobs(workspace_root, *, now=None) -> list[dict]:
    """MAINT1 (D8) — per-JOB receipt-gap check for the jobs inside the
    `maintenance` task. Same posture as check_tasks: receipts are the only
    served/not-served truth, and one missed nominal slot is tolerance (a
    single fire can be cut short); missing BOTH of the two most recent
    nominal slots is `stale`.

    Statuses per job:
      ok    — receipt within tolerance of the job's own nominal cadence.
      never — no receipt ever (meaningful only once the maintenance task has
              been firing across the job's slots — health_verdict gates on
              that before flagging).
      stale — receipted before, then stopped: missed the two most recent
              nominal slots.

    Returns [{job, display_name, status, last_receipt (ISO|None),
    expected (ISO|None), second_expected (ISO|None)}] in registry order.
    """
    # Lazy import — maintenance_dispatcher imports this module's cron math at
    # load; importing it back at module level would be a cycle.
    from maintenance_dispatcher import MAINTENANCE_JOBS

    now = now or _now_local()
    receipts = last_receipts(workspace_root, list(MAINTENANCE_JOBS))
    findings = []
    for job_id, spec in MAINTENANCE_JOBS.items():
        try:
            recent = expected_fires(spec["nominal_cron"], now=now, count=2)
        except CronParseError:
            continue
        last = receipts.get(job_id)
        if last is None:
            status = "never"
        elif len(recent) >= 2 and last < recent[1] - _FIRE_GRACE:
            status = "stale"
        else:
            status = "ok"
        findings.append({
            "job": job_id,
            "display_name": task_display_name(job_id),
            "status": status,
            "last_receipt": last.isoformat() if last else None,
            "expected": recent[0].isoformat() if recent else None,
            "second_expected": recent[1].isoformat() if len(recent) >= 2 else None,
        })
    return findings


def _maintenance_job_problems(workspace_root, reports, *, now=None):
    """The job-level findings health_verdict folds in (MAINT1 D8). Job detail
    is only meaningful when the maintenance TASK itself is firing — a broken
    task already gets its own task-level line, and doubling it per job would
    be noise. So this returns [] unless the maintenance task report exists,
    is receipted, and is not itself a problem.

    A `never` job is flagged only when the task has been firing since before
    the job's second-most-recent nominal slot (the task had >= 2 chances to
    serve it and never did) — a fresh install's first week stays quiet.

    Returns (findings, lines): the stale-job findings + one plain-English
    sentence each (facts + the one action, never a cause — R3).
    """
    maint = next((r for r in reports if r["task"] == "maintenance"), None)
    if maint is None or not maint.get("last_fired"):
        return [], []
    if maint["status"] not in ("ok",) or maint.get("receipt_gap"):
        return [], []
    now = now or _now_local()
    try:
        findings = check_maintenance_jobs(workspace_root, now=now)
    except Exception:
        return [], []
    # Oldest maintenance_run receipt = how long the task has been firing.
    oldest_fire = None
    try:
        from receipts import iter_receipts as _iter_receipts

        for r in _iter_receipts(workspace_root, task_ids=["maintenance"]):
            dt_local = _to_local_naive(r["dt"]) if r["dt"] is not None else None
            if dt_local is not None and (oldest_fire is None or dt_local < oldest_fire):
                oldest_fire = dt_local
    except Exception:
        oldest_fire = None

    problems, lines = [], []
    for f in findings:
        flag = False
        if f["status"] == "stale":
            flag = True
        elif f["status"] == "never" and f["second_expected"] and oldest_fire is not None:
            try:
                flag = oldest_fire < _dt.datetime.fromisoformat(f["second_expected"]) - _FIRE_GRACE
            except ValueError:
                flag = False
        if not flag:
            continue
        name = f["display_name"]
        since = (f["last_receipt"] or "")[:10]
        since_phrase = f" since {since}" if since else ""
        lines.append(
            f"Your Maintenance task is running, but its {name} pass hasn't "
            f"recorded any work{since_phrase} — open the Maintenance task in "
            f"the Scheduled section and press Run Now once, and check the "
            f"result looks right."
        )
        problems.append(f)
    return problems, lines


# ---------------------------------------------------------------------------
# Hard-failure surfacing (HYG1 Item 4 — the dead-letter scheduled_task_failure)
# ---------------------------------------------------------------------------
#
# Orchestrators WRITE `scheduled_task_failure` on hard failures (dont-forget /
# upcoming-meetings / historical-backfill error contracts) but nothing ever
# READ the type — a task that fired and crashed mid-run looked healthy as
# long as its receipt landed, and the failure event was a dead letter. This
# reader closes the loop: recent failures surface in the health verdict as
# fact-only problem lines. R3's cause-fabrication ban applies verbatim —
# quote the event's own diagnostic, never speculate about why.

FAILURE_WINDOW_DAYS = 7

# The event's own diagnostic string, first non-empty of these data keys.
_FAILURE_DETAIL_KEYS = ("error", "reason", "message", "detail", "note", "summary")


def check_task_failures(workspace_root, *, now=None, reports=None,
                        exclude_tasks=None):
    """`scheduled_task_failure` events from the last FAILURE_WINDOW_DAYS,
    grouped by task (ids normalized via receipts.normalize_task_id over
    data.task_id → data.kind → source_skill), newest failure per task.

    Gating (mirrors R3's newest-fire rule): a failure OLDER than the task's
    newest successful receipt is history, not a finding — the task has
    demonstrably run clean since. A task with no receipt at all keeps its
    failure (there is nothing newer to vouch for it).

    MAINT1 attribution: dispatcher-owned silent jobs attribute to the
    failing sub-task when the event names one (its id is a MAINTENANCE_JOBS
    key), else to `maintenance`.

    `exclude_tasks`: task ids already in the verdict's problems bucket —
    their task-level line already exists; doubling it with the failure
    detail would be noise (same doctrine as the maintenance job findings).

    Returns (findings, lines): findings are
      {"task", "display_name", "ts" (ISO), "detail"} newest-first;
    lines are one fact-only sentence each — what failed, when (localized),
    the event's own diagnostic — plus the one action.
    """
    now = now or _now_local()
    exclude = set(exclude_tasks or ())
    floor = now - _dt.timedelta(days=FAILURE_WINDOW_DAYS)

    try:
        from maintenance_dispatcher import MAINTENANCE_JOBS
        _dispatcher_jobs = set(MAINTENANCE_JOBS)
    except Exception:
        _dispatcher_jobs = set()

    newest_by_task: dict[str, dict] = {}
    for ev in _iter_events(workspace_root):
        if ev.get("type") != "scheduled_task_failure":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        raw = d.get("task_id") or d.get("kind") or ev.get("source_skill") or ""
        tid = _normalize_task_id(raw) if isinstance(raw, str) else ""
        if not tid:
            continue
        # MAINT1 attribution: a dispatcher-owned sub-task keeps its own id
        # (it IS the named failing job); an unnameable dispatcher failure
        # arrives already stamped `maintenance` by the dispatcher itself.
        ts_raw = ev.get("ts") or d.get("ts") or ""
        try:
            when = _dt.datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue  # an undatable failure can't be windowed honestly
        when = _to_local_naive(when)
        if when is None or when < floor:
            continue
        detail = next(
            (str(d[k]).strip() for k in _FAILURE_DETAIL_KEYS
             if isinstance(d.get(k), str) and d.get(k).strip()),
            "no detail recorded",
        )
        prev = newest_by_task.get(tid)
        if prev is None or when > prev["_when"]:
            newest_by_task[tid] = {
                "task": tid,
                "display_name": task_display_name(tid),
                "ts": when.isoformat(),
                "detail": detail[:200],
                "_when": when,
            }

    if not newest_by_task:
        return [], []

    # Newest-successful-receipt gate: one shared read for every affected id.
    try:
        receipts_by_task = last_receipts(workspace_root, list(newest_by_task))
    except Exception:
        receipts_by_task = {}

    display_by_task = {}
    for r in reports or []:
        display_by_task[r.get("task")] = r.get("display_name")

    findings, lines = [], []
    for tid, f in sorted(newest_by_task.items(),
                         key=lambda kv: kv[1]["_when"], reverse=True):
        if tid in exclude:
            continue
        newest_receipt = receipts_by_task.get(tid)
        if newest_receipt is not None and newest_receipt > f["_when"]:
            continue  # ran clean since — history, not a finding
        name = display_by_task.get(tid) or f["display_name"]
        when_h = _human_time(f["_when"], now=now)
        lines.append(
            f"{name} hit an error mid-run at {when_h} — its own log says: "
            f"\"{f['detail']}\". Its next scheduled run will show whether it "
            "recovered, or run it now to check."
        )
        findings.append({k: v for k, v in f.items() if not k.startswith("_")})
    return findings, lines


# How fresh a run receipt must be for the vantage line to vouch that the
# tasks "look alive" — a week covers the sparsest default cadence (weekly).
_VANTAGE_FRESH = _dt.timedelta(days=7)


def detect_registry_vantage(workspace_root, task_records, *, now=None) -> Optional[dict]:
    """The F-40 guard: an EMPTY machine-local scheduler registry in a chat
    whose substrate carries registration history (`schedule_created` events
    or a recorded registered set) means this chat CANNOT SEE the scheduler —
    a cloud/remote session, or a different computer than the one the tasks
    run on. The honest verdict is "I can't see your scheduler from this
    chat", NEVER "nothing is registered" (F-40's false total-outage report,
    whose named fix would have double-registered all 12 tasks).

    Returns None when the registry is visibly non-empty (any record — the
    scheduler is in view, normal checks apply) or when the substrate shows
    no registration history either (a genuinely fresh install — "not set up
    yet" is then the honest verdict). Otherwise a finding dict:
      {check, schedule_created_seen, registered_recorded, newest_receipt,
       receipts_fresh, machine, line}
    """
    records = [r for r in (task_records or [])
               if isinstance(r, dict) and r.get("taskId")]
    if records:
        return None
    now = now or _now_local()
    ws_config = read_workspace_config(workspace_root)
    raw = ws_config.get("registered_taskIds")
    recorded = [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []

    seen_schedule_created = False
    newest_receipt: Optional[_dt.datetime] = None
    machine: Optional[str] = None
    for ev in _iter_events(workspace_root):
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "schedule_created":
            seen_schedule_created = True
            continue
        tid = _receipt_task_id(ev)
        if tid is None:
            continue
        dt = _to_local_naive(event_dt(ev))
        if dt is not None and (newest_receipt is None or dt > newest_receipt):
            newest_receipt = dt
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            m = data.get("machine")
            machine = m.strip() if isinstance(m, str) and m.strip() else None

    if not seen_schedule_created and not recorded:
        return None

    fresh = newest_receipt is not None and (now - newest_receipt) <= _VANTAGE_FRESH
    opener = (
        "I can't see your scheduler from this chat — that usually means this "
        "is a cloud or remote session, or a different computer than the one "
        "your scheduled tasks run on."
    )
    closer = (
        "For a full check, open a local (non-cloud) chat on the computer "
        "where Command Room is set up."
    )
    if fresh:
        when = _human_time(newest_receipt, now=now)
        alive = f"Your tasks look alive: the most recent one ran {when}"
        if machine:
            alive += f" (on {machine})"
        line = f"{opener} {alive}. {closer}"
    elif newest_receipt is not None:
        when = _human_time(newest_receipt, now=now)
        line = (
            f"{opener} I can't tell from here whether your tasks are still "
            f"running — the most recent recorded run was {when}. {closer}"
        )
    else:
        line = (
            f"{opener} Your tasks were set up before, but there's no run "
            f"record I can read from here. {closer}"
        )
    return {
        "check": "registry_vantage",
        "schedule_created_seen": seen_schedule_created,
        "registered_recorded": len(recorded),
        "newest_receipt": newest_receipt.isoformat() if newest_receipt else None,
        "receipts_fresh": fresh,
        "machine": machine,
        "line": line,
    }


def _caught_up_line(r: dict, now=None) -> str:
    """Dated catch-up render (F-43 P2c's fix): name WHEN it caught up and
    which slot it served — facts only, no cause."""
    name = r["display_name"]
    fired = _human_time(_dt.datetime.fromisoformat(r["last_fired"]), now=now)
    sched_iso = (r.get("catchup") or {}).get("scheduled_for")
    if sched_iso:
        try:
            sched = _human_time(_dt.datetime.fromisoformat(sched_iso), now=now)
            return (
                f"Your {name} caught up {fired} — its {sched} run didn't "
                f"happen on time. The work is done, just later than scheduled."
            )
        except ValueError:
            pass
    return (
        f"Your {name}'s most recent run was a late catch-up ({fired}), not an "
        f"on-schedule fire. The work is done, just later than scheduled."
    )


def _first_run_line(r: dict, now=None) -> str:
    """never_fired render (F-43 P1a's fix): a task with zero receipts has NO
    fire history to speak of — say so, and name the real next fire time."""
    name = r["display_name"]
    if r.get("next_fire"):
        try:
            nxt = _human_time(_dt.datetime.fromisoformat(r["next_fire"]), now=now)
            return (
                f"Your {name} task hasn't had its first run yet — its next "
                f"scheduled run is {nxt}."
            )
        except ValueError:
            pass
    return f"Your {name} task hasn't had its first run yet."


def health_verdict(workspace_root, *, task_records=None, now=None) -> dict:
    """R3's one entry point for every health surface (system-health, cleanup's
    weekly pass, any future daily pass). Vantage guard first, then the
    partition: every counted task lands in exactly ONE bucket, and the
    summary counts come from the partition — so a task named in any warning
    can never simultaneously count inside "everything's running" (F-43's
    self-contradiction, made structurally impossible).

    Buckets (priority order — first match wins):
      problems          — late / never_authorized / missing first-install /
                          receipt_gap: something needs the user.
      caught_up         — newest fire served its slot late (dated info line).
      first_run_pending — registered, zero receipts, inside the grace window.
      on_schedule       — receipted within cadence, served on time.
    Later-add tasks that simply aren't registered are excluded from the
    partition entirely (change-schedule owns that render).

    Returns:
      {vantage (None|finding), reports, on_schedule, caught_up,
       first_run_pending, problems (task-id lists), summary_line,
       lines (problem sentences incl. binding), info_lines (dated catch-up +
       first-run sentences)}
    """
    now = now or _now_local()
    vantage = detect_registry_vantage(workspace_root, task_records, now=now)
    if vantage is not None:
        return {
            "vantage": vantage,
            "reports": [],
            "on_schedule": [], "caught_up": [],
            "first_run_pending": [], "problems": [],
            "maintenance_jobs": [],
            "task_failures": [],
            "summary_line": vantage["line"],
            "lines": [],
            "info_lines": [],
        }

    reports = check_tasks(workspace_root, now=now, task_records=task_records)
    binding = check_workspace_binding(workspace_root)
    on_schedule, caught_up, first_run, problems = [], [], [], []
    for r in reports:
        if r["status"] == "not_registered" and not r["first_install"]:
            continue  # later-add: expected, uncounted; change-schedule renders it
        if r["status"] in ("late", "never_authorized", "not_registered") or r["receipt_gap"]:
            problems.append(r)
        elif r["caught_up"]:
            caught_up.append(r)
        elif r["status"] == "never_fired":
            first_run.append(r)
        else:
            on_schedule.append(r)

    lines = plain_english_lines(reports, binding=binding)
    info_lines = [_caught_up_line(r, now=now) for r in caught_up]
    info_lines += [_first_run_line(r, now=now) for r in first_run]

    # MAINT1 (D8): per-JOB receipt gaps inside a healthy maintenance task —
    # the task fired, a job chronically wrote nothing. Job findings ride the
    # problem lines/counts but never move the task out of its bucket (the
    # task DID run on schedule; the job inside it is what needs eyes).
    job_problems, job_lines = _maintenance_job_problems(
        workspace_root, reports, now=now
    )
    lines += job_lines

    # HYG1 Item 4: recent hard failures (scheduled_task_failure) ride the
    # problem lines/counts like the job findings — a failure never moves a
    # task out of its partition bucket (its receipt may genuinely be on
    # schedule; the mid-run crash is what needs eyes). Tasks already in the
    # problems bucket are excluded — their task-level line exists.
    failure_findings, failure_lines = check_task_failures(
        workspace_root, now=now, reports=reports,
        exclude_tasks={r["task"] for r in problems},
    )
    lines += failure_lines

    total = len(on_schedule) + len(caught_up) + len(first_run) + len(problems)
    fresh_unregistered = (
        total > 0
        and len(problems) == total
        and all(r["status"] == "not_registered" for r in problems)
    )
    if total == 0 or fresh_unregistered:
        # Nothing registered and no run history claimed for anything — a
        # genuinely fresh install. One honest line beats N identical
        # "missing from the schedule" flags; the per-task lines collapse
        # (the binding finding, if any, stays).
        summary = (
            "Your scheduled chats and background tasks aren't set up yet — "
            "say 'set up command room schedules' to get started."
        )
        if fresh_unregistered:
            lines = plain_english_lines([], binding=binding)
    elif not lines and not caught_up and not first_run:
        newest = max(
            (r for r in on_schedule if r["last_fired"]),
            key=lambda r: r["last_fired"],
            default=None,
        )
        recency = ""
        if newest:
            when = _human_time(
                _dt.datetime.fromisoformat(newest["last_fired"]), now=now
            )
            recency = f", most recently {newest['display_name']} at {when}"
        summary = (
            f"Everything's running. All {total} of your scheduled chats and "
            f"background tasks ran on their normal schedule{recency}."
        )
    else:
        parts = [
            f"{len(on_schedule)} of {total} scheduled chats and background "
            f"tasks ran on their normal schedule"
        ]
        if caught_up:
            parts.append(
                f"{len(caught_up)} caught up late" if len(caught_up) > 1
                else "1 caught up late"
            )
        if first_run:
            parts.append(
                f"{len(first_run)} are waiting on their first run" if len(first_run) > 1
                else "1 is waiting on its first run"
            )
        n_attention = len(problems) + len(job_problems) + len(failure_findings)
        if n_attention:
            parts.append(
                f"{n_attention} need attention" if n_attention > 1
                else "1 needs attention"
            )
        summary = "; ".join(parts) + "."

    return {
        "vantage": None,
        "reports": reports,
        "on_schedule": [r["task"] for r in on_schedule],
        "caught_up": [r["task"] for r in caught_up],
        "first_run_pending": [r["task"] for r in first_run],
        # Job-level findings count as problems (brief_watchdog_line's count,
        # the "N need attention" math) under a namespaced id so consumers can
        # tell a task from a job inside the maintenance task.
        "problems": [r["task"] for r in problems]
                    + [f"maintenance:{f['job']}" for f in job_problems]
                    + [f"failure:{f['task']}" for f in failure_findings],
        "maintenance_jobs": job_problems,
        "task_failures": failure_findings,
        "summary_line": summary,
        "lines": lines,
        "info_lines": info_lines,
    }


def brief_watchdog_line(workspace_root, *, verdict=None, now=None):
    """The morning brief's LIGHT daily watchdog pass (v4.6.1 S3 — the R3
    discovery: system-health's docstring promised this line while the
    morning-brief orchestrator called nothing, so the brief inherited no
    watchdog at all).

    ONE line, receipts-only, derived SOLELY from health_verdict's
    partition — no per-task detail, no cause guessing, no second scan:

      problems > 0   → "N of your background tasks need attention — say
                        health check for the detail."
      problems == 0  → None (never pad the brief with an all-clear; the
                        brief's job is today's work, not green checkmarks)
      cloud vantage  → None (this chat can't see the scheduler; staying
                        quiet beats a false alarm — system-health owns
                        the vantage explanation when asked directly)

    Pass a precomputed `verdict` to avoid a second receipt scan when the
    caller already ran health_verdict this fire.
    """
    if verdict is None:
        verdict = health_verdict(workspace_root, now=now)
    if verdict.get("vantage") is not None:
        return None
    n = len(verdict.get("problems") or [])
    if n == 0:
        return None
    if n == 1:
        return ("1 of your background tasks needs attention — "
                "say health check for the detail.")
    return (f"{n} of your background tasks need attention — "
            "say health check for the detail.")


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

    # MAINT1: superseded taskIds (the five old silent tasks) are disabled by
    # migration, not removed — an override left behind for one of them is
    # expected history, never drift. Same for the `maintenance_jobs` sub-dict
    # (change-schedule's job-level pause store), which shares the
    # schedule_config namespace but is not a taskId.
    from schedule_config import SUPERSEDED_BY

    superseded = {t for ids in SUPERSEDED_BY.values() for t in ids}
    orphans = []
    try:
        data = json.loads(entities.read_text(encoding="utf-8"))
        overrides = ((data.get("workspace") or {}).get("schedule_config") or {})
        for tid in overrides:
            if tid == "maintenance_jobs" or tid in superseded:
                continue
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

    Facts + the one action, never a cause the watchdog can't know (R3 —
    the pre-R3 `late` line asserted "the computer was asleep", the exact
    fabricated-narrative class F-10/F-43/F-47 catalogued; when a gap is
    unexplained, say what is known and stop). Scheduler stamps are quoted
    AS the schedule's claim ("the schedule shows..."), never as fact — F-39
    proved they land without execution. No jargon, no taskIds, no event
    names — display names + the exact next action.
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
        stamp_phrase = ""
        if r.get("last_run_at"):
            try:
                stamp_phrase = " " + _human_time(_dt.datetime.fromisoformat(r["last_run_at"]))
            except ValueError:
                stamp_phrase = ""
        if r["status"] == "never_authorized":
            if r["receipt_gap"]:
                # The scheduler claims runs but the substrate has nothing —
                # never call that "waiting on permission" (F-39 class:
                # lastRunAt stamps land without execution).
                lines.append(
                    f"The schedule shows runs for your {name} task"
                    f"{f' (latest{stamp_phrase})' if stamp_phrase else ''}, but it has never "
                    f"recorded any work — open it and press Run Now once, and check the result "
                    f"looks right."
                )
            else:
                lines.append(
                    f"Your {name} task has never run — it's likely still waiting on its one-time "
                    f"permission. Open it in the Scheduled section and press Run Now once."
                )
        elif r["status"] == "late":
            since = (r["last_fired"] or "")[:10]
            since_phrase = f" since {since}" if since else ""
            if r["receipt_gap"]:
                lines.append(
                    f"The schedule shows your {name} task ran{stamp_phrase}, but it hasn't "
                    f"recorded any work{since_phrase} — open it and press Run Now once, and "
                    f"check the result looks right."
                )
            else:
                # What is KNOWN: no record since <date>. The cause is not
                # knowable from here — never assert one (R3).
                lines.append(
                    f"Your {name} task hasn't run{since_phrase} — I can't tell from here why it "
                    f"stopped. Open it in the Scheduled section and press Run Now to catch up."
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
    "brief_watchdog_line",
    "check_maintenance_jobs",
    "check_tasks",
    "check_schedule_parity",
    "check_workspace_binding",
    "check_prompt_versions",
    "detect_registry_vantage",
    "expected_fires",
    "health_verdict",
    "last_receipts",
    "late_signals",
    "next_fire",
    "plain_english_lines",
    "read_workspace_config",
]
