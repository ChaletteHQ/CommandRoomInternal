#!/usr/bin/env python3
"""
Late-fire detection + graceful degradation (Phase 3 Reliability, R4 — 2026-07;
lateness ledger + run-mode gate v4.5.2 R2).

Cowork fires a missed scheduled task at next app launch, hours or days
after its cron slot — a "Friday wrap + Monday preview" delivered Sunday is
stale, and pre-R4 nothing in the orchestrator knew it was late. Every chat
orchestrator calls `check_lateness()` at the top of its run (the shared
helper the spec prefers over per-orchestrator math) and branches on the
tier it returns.

WHO MAY BE LATE (v4.5.2 R2 — FINDINGS F-47 P1a / F-51). The v4.5.1 dogfood
produced THREE fabricated late_fire receipts in one afternoon because the
lateness check ran on ANY task activation and consulted no served-slot
ledger: a manual re-run scored 356 min against a slot the 8:48 AM scheduled
fire had already served; a manual fire scored 1306 min against YESTERDAY'S
slot, served by the overnight catch-up; a cron change scored 317 min against
a slot the change itself created. The contract now:

  - Lateness is evaluated ONLY on scheduled-context fires (`fired_via` in
    {scheduled, catchup}). A `manual` fire (typed trigger, Run Now, re-run)
    is interactive and never late — tier `manual`, no event, no banner.
  - THE FALLBACK DIRECTION IS `manual`, NOT `scheduled` (DOGFIX1, 2026-07-27
    — the F-47 class recurring live at v5.2.1). `fired_via` reaches this
    helper as a STRING an executing model substituted into a prose
    placeholder, so the value can be anything: an unsubstituted
    `'<scheduled|manual>'`, a freeform `'Run Now'` (the aliases cover
    `run-now`/`run_now`, not the spaced spelling), `''`, `'typed trigger'`.
    Pre-DOGFIX1 every one of those was truthy-and-not-`manual`, so it fell
    through to the scheduled branch and fabricated lateness — the exact
    direction RECEIPT_CONTRACT.md forbids ("**When uncertain, it is
    manual**"). Now only the two literal scheduled-context values run the
    math; EVERYTHING else is tier `manual`, `suppressed:
    "unrecognized_run_mode"`, with the raw string returned as
    `fired_via_raw` so the fallback is visible rather than swallowed.
    Asymmetry, restated: a mis-labeled manual costs one missing lateness
    note; a mis-labeled scheduled REFUSES A SURFACE A HUMAN ASKED FOR.
  - The slot being served must be UNSERVED: the task's newest substrate
    receipt (via the R1 receipt reader — all legacy shapes) is the
    served-slot marker. A slot with a receipt after it is SERVED; there is
    never a second late_fire for it.
  - A slot older than the task's most recent `schedule_config_changed`
    event was minted retroactively by the change (the F-51 phantom) — never
    scored. Schedule changes do not create missed slots.
  - Scheduler `lastRunAt` stamps are NEVER consulted — the 2026-07-08
    cleanup autopsy proved they land without execution (F-39: 9 tasks
    stamped, one receipt). Receipts are the only served/not-served truth.

TIER CONTRACT (thresholds tunable in LATENESS_TIERS — one shared constant):

  manual   — fired_via=manual, OR any value that does not normalize into
             SCHEDULED_CONTEXT: interactive fire, lateness not applicable.
             No banner, no degradation, no event.
  exempt   — silent task classes (SILENT_TASKS registry membership, never a
             hardcoded name list): late is fine, they always run in full.
             No banner, no degradation, no event.
  none     — < 3h late, or the slot was already served / minted by a
             schedule change (`suppressed` says which). Run normally,
             no mention.
  note     — 3h–24h late. Run normally; the output OPENS with the one
             plain-English `banner` line.
  degrade  — > 24h late. Do NOT render the full stale surface. The run
             still performs every substrate write the task owes (events,
             view updates) SILENTLY — invisible-write-loses-to-visible-
             deliverable is the Bug #98 class, so orchestrators keep those
             writes explicit — then posts only the one-line
             `degrade_notice`. The next morning brief reads events.jsonl,
             so no extra carry logic is needed beyond the writes landing.

The returned `receipt_fired_via` is what the fire's closing `log_receipt`
call passes as `fired_via`: `manual` on manual fires, `catchup` when a
scheduled-context fire is serving a missed slot (note/degrade), else
`scheduled` — standardizing the pattern F-51's receipt improvised.

NARRATIVE RULE (F-47/F-50): the banner states the facts — what was
scheduled, that this run is late. It never asserts a CAUSE ("the computer
was likely asleep"): the helper cannot know why, and the dogfood logged
four fabricated sleep narratives in one day on fires that weren't late at
all. Orchestrators must not add a cause either.

TIME RULES: all lateness math is MACHINE-local (cron evaluates on the
machine clock — confirmed live 2026-07-01, machine=Mountain vs workspace=
Pacific), AND SO IS EVERY RENDERED TIME (LATETZ, 2026-07-28). One clock,
end to end: `expected_fires` returns machine-local naive slots, `_now_local`
is machine-local naive, `served_slot_markers` normalizes to machine-local
naive via `_to_local_naive`, and `_human_time` renders that value as-is.

The banner used to be converted into the workspace TZ at render time on the
theory that "workspace TZ is presentation-only" meant "present in workspace
TZ". It does not. The governing rule (references/HOW_COMMAND_ROOM_WORKS.md)
is that CONVERSION HAPPENS ONCE, AT REGISTRATION/CHANGE TIME: change-schedule
turns the user's requested wall time into machine-local via
`workspace_time_to_machine()` and stores that in the cron. Converting again
at render re-expressed the slot in a clock it was never authored in — a no-op
only where machine tz == workspace tz. That masked it on M's PC and made CI
red for 8 consecutive pushes ("8:45 AM" rendering as "1:45 AM" on a UTC
runner; an Asia/Tokyo workspace named the wrong DAY entirely).

Corollary worth knowing before touching this: `tz.to_local()` is for upstream
CONNECTOR timestamps (Granola / Calendar / Gmail) and assumes a naive input is
UTC. A cron slot is neither a connector timestamp nor UTC. Never route one
through it.

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
from receipts import (  # noqa: E402
    TASK_PREDECESSORS,
    _iter_events,  # the one defensive event reader — shared, not re-rolled
    normalize_fired_via,
    normalize_task_id,
    receipt_task_id,
)
from schedule_config import (  # noqa: E402
    SUPERSEDED_BY,
    CronParseError,
    is_silent_task,
    load_schedule_config,
    task_display_name,
)

# MAINT1: the five pre-MAINT1 silent taskIds keep the silent-class exemption
# forever — a not-yet-migrated install still fires them, and their work is
# still silent write-side maintenance (late is fine, they run in full).
_SUPERSEDED_SILENT_IDS = frozenset(
    t for ids in SUPERSEDED_BY.values() for t in ids
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

# The ONLY two run modes lateness math may run on — the docstring's
# "scheduled-context fires" set, now enforced in code rather than asserted in
# prose (DOGFIX1). Anything outside it is manual by fail-safe, including the
# unsubstituted prose placeholder. Deliberately NOT derived from
# receipts.FIRED_VIA: that set contains `manual`, and this one is the
# complement of it.
SCHEDULED_CONTEXT = frozenset({"scheduled", "catchup"})


def _now_local() -> _dt.datetime:
    return _dt.datetime.now()


def _human_time(dt_naive_local: _dt.datetime) -> str:
    """Presentation-only rendering of a MACHINE-local naive datetime — the
    clock cron evaluates in, and the clock every datetime in this module is
    already expressed in (`_to_local_naive`). Rendered AS-IS. Never raises.

    NO WORKSPACE-TZ CONVERSION HAPPENS HERE, DELIBERATELY (LATETZ, 2026-07-28).
    This function used to do `dt.astimezone()` (attach the machine zone) and
    then `tz.to_local(...)` (re-express in the workspace zone). That is a
    second conversion of a value that was already converted once, and it
    violates the governing rule in `references/HOW_COMMAND_ROOM_WORKS.md`:
    **conversion happens ONCE, at registration/change time.** `change-schedule`
    converts the user's requested wall time into machine-local via
    `schedule_config.workspace_time_to_machine()` and stores THAT in the cron;
    by the time a slot reaches this renderer the conversion is already done.
    Doing it again re-expresses the slot in a clock it was never authored in.

    It was a no-op only where machine tz == workspace tz — which is M's PC and
    nowhere else. On the UTC CI runner with a `America/Los_Angeles` workspace
    the 8:45 AM slot rendered as "1:45 AM", and main ran red for 8 consecutive
    pushes on exactly that assertion. It is not only the hour that moves: a
    workspace in Asia/Tokyo rendered the same Friday slot as "12:45 AM
    Saturday", naming the wrong DAY in a customer-visible line.

    `tz.to_local()` is the right helper for what it was built for — upstream
    CONNECTOR timestamps (Granola / Calendar / Gmail), which arrive in UTC or
    with a foreign offset and must be pulled into the user's zone. Its own
    contract assumes a naive input is UTC. A cron slot is not a connector
    timestamp, and it is not UTC. Do not route one through it.
    """
    # Defensive: the module's contract is naive machine-local, but an aware
    # value from a caller is normalized rather than rendered in a foreign zone.
    if dt_naive_local.tzinfo is not None:
        dt_naive_local = dt_naive_local.astimezone().replace(tzinfo=None)
    day = dt_naive_local.strftime("%A")
    hour = dt_naive_local.strftime("%I:%M %p").lstrip("0")
    if dt_naive_local.minute == 0:
        hour = dt_naive_local.strftime("%I %p").lstrip("0")
    return f"{hour} {day}"


def _to_local_naive(dt: Optional[_dt.datetime]) -> Optional[_dt.datetime]:
    """Aware → naive machine-local (the clock cron evaluates in)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def served_slot_markers(workspace_root, task_id) -> dict:
    """One pass over the substrate for the two facts the lateness ledger
    needs — machine-local naive datetimes (None = never):

      last_receipt         — newest receipt for this task, ANY legacy shape
                             (the R1 reader's matcher). This is the
                             served-slot marker: receipts are the only
                             served/not-served truth (F-39 — scheduler
                             lastRunAt stamps land without execution).
      last_schedule_change — newest `schedule_config_changed` event naming
                             this task (change-schedule writes
                             `data.changes: [{task_id, cron, enabled}]`).
                             A slot older than this was minted retroactively
                             by the change (F-51) — never a missed fire.
    """
    canonical = normalize_task_id(task_id)
    # CTS1 — a split successor's ledger also reads its RETIRED predecessor's
    # receipts and schedule changes (receipts.TASK_PREDECESSORS): the morning
    # after the commitments split, the 8:30 slot was served by a receipt
    # written under the old id — without this the first post-split fire
    # fabricates lateness for a slot that ran.
    accepted = {canonical} | set(TASK_PREDECESSORS.get(canonical, ()))
    last_receipt: Optional[_dt.datetime] = None
    last_change: Optional[_dt.datetime] = None
    for ev in _iter_events(workspace_root):
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "schedule_config_changed":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            changes = data.get("changes")
            named = []
            if isinstance(changes, list):
                named = [normalize_task_id(c.get("task_id") or c.get("taskId"))
                         for c in changes if isinstance(c, dict)]
            else:  # defensive: flat single-task shapes
                named = [normalize_task_id(data.get("task_id") or data.get("taskId"))]
            if accepted & set(named):
                dt = event_dt(ev)
                if dt is not None and (last_change is None or dt > last_change):
                    last_change = dt
            continue
        if receipt_task_id(ev) in accepted:
            dt = event_dt(ev)
            if dt is not None and (last_receipt is None or dt > last_receipt):
                last_receipt = dt
    return {
        "last_receipt": _to_local_naive(last_receipt),
        "last_schedule_change": _to_local_naive(last_change),
    }


def check_lateness(
    workspace_root,
    task_id: str,
    *,
    fired_via: str = "manual",
    now: Optional[_dt.datetime] = None,
    emit: bool = True,
) -> dict:
    """Compute this fire's lateness tier. Call at the top of every chat
    orchestrator run, BEFORE any surface is rendered.

    Args:
      fired_via: the run mode the orchestrator detected
        (`shared/RECEIPT_CONTRACT.md` § Run-mode detection): `scheduled`
        for scheduler-initiated fires (including app-launch catch-up
        deliveries of a missed slot), `manual` for anything a human caused
        (typed trigger, Run Now, re-run). Manual fires are never late —
        no tier math, no event, no banner (F-47 P1a).

        ONLY the two SCHEDULED_CONTEXT values run the math. Every other
        input — including an omitted argument, which is why the default is
        `manual` and not `scheduled` — is treated as manual (DOGFIX1). A
        caller that means `scheduled` must say so in that exact word; there
        is no spelling of "I don't know" that degrades a surface.

    Returns:
      {task, tier: manual|exempt|none|note|degrade|unknown,
       fired_via (normalized, fail-safed input), fired_via_raw (what the
       caller actually passed — evidence when the fallback fired),
       receipt_fired_via (what the fire's closing log_receipt call passes:
       manual|scheduled|catchup), suppressed (None | "manual_fire" |
       "unrecognized_run_mode" | "slot_already_served" |
       "slot_created_by_schedule_change"), lateness_minutes,
       scheduled_for (ISO, machine-local), banner (note tier),
       degrade_notice (degrade tier), event_logged (bool)}

    `unknown` (unparseable cron / no schedule entry) behaves like `none` —
    the fire runs normally; lateness detection is best-effort and must
    never block the primary output.
    """
    now = now or _now_local()
    normalized = normalize_fired_via(fired_via)
    # Fail-safe by asymmetry, in code (DOGFIX1). `normalize_fired_via` returns
    # unknown strings UNCHANGED (only absent/blank comes back None), so a
    # `via != "manual"` test read every garbage value as scheduled. Test the
    # positive set instead: recognized-scheduled, or manual.
    recognized = normalized in SCHEDULED_CONTEXT
    via = normalized if recognized else "manual"
    out = {
        "task": task_id,
        "tier": "none",
        "fired_via": via,
        "fired_via_raw": fired_via,
        "receipt_fired_via": "manual" if via == "manual" else "scheduled",
        "suppressed": None,
        "lateness_minutes": 0,
        "scheduled_for": None,
        "banner": None,
        "degrade_notice": None,
        "event_logged": False,
    }
    if via == "manual":
        # Interactive fire — lateness is not a concept that applies (F-47
        # P1a: a manual Run-now self-classified as a 356-min-late scheduled
        # fire). No slot math, no event, no narrative.
        out["tier"] = "manual"
        # Distinguish a caller that SAID manual from one whose value did not
        # resolve. Same tier and same behaviour — every orchestrator's
        # existing `manual` branch handles both with no prose change — but a
        # reviewer, a test, and the receipt can all see which happened.
        out["suppressed"] = (
            "manual_fire" if normalized == "manual" else "unrecognized_run_mode"
        )
        return out
    if is_silent_task(task_id) or task_id in _SUPERSEDED_SILENT_IDS:
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

    # This fire LOOKS late — consult the lateness ledger before believing
    # it (v4.5.2 R2). Best-effort like everything here: if the substrate is
    # unreadable the markers come back None and the tier math proceeds.
    try:
        markers = served_slot_markers(workspace_root, task_id)
    except Exception:
        markers = {"last_receipt": None, "last_schedule_change": None}
    last_receipt = markers["last_receipt"]
    last_change = markers["last_schedule_change"]
    if last_receipt is not None and last_receipt >= scheduled:
        # The slot was served — a receipt exists after it. This fire is a
        # re-run / second delivery, not a late first serve (F-47 triggers
        # 1 and 2). Tier none, no event, run normally.
        out["suppressed"] = "slot_already_served"
        out["lateness_minutes"] = 0
        return out
    if last_change is not None and last_change > scheduled:
        # The slot predates the task's latest schedule change — it only
        # exists because the change re-anchored the cron (F-51's phantom
        # 9:30 slot, minted at 2:46 PM). Never a missed fire.
        out["suppressed"] = "slot_created_by_schedule_change"
        out["lateness_minutes"] = 0
        return out

    # Serving a genuinely missed slot — the receipt says catchup.
    out["receipt_fired_via"] = "catchup"
    when = _human_time(scheduled)
    if lateness < LATENESS_TIERS["degrade"]:
        out["tier"] = "note"
        # Facts only — never assert a cause (F-47/F-50: four fabricated
        # "computer was likely asleep" narratives in one dogfood day).
        out["banner"] = (
            f"This is your {display}, running late — it was scheduled for "
            f"{when}."
        )
    else:
        now_day = _human_time(now).split(" ", 2)[-1]
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
                        "fired_via": out["receipt_fired_via"],
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
    "SCHEDULED_CONTEXT",
    "CHRONIC_WINDOW_WEEKS",
    "CHRONIC_MIN_LATE_WEEKS",
    "check_lateness",
    "detect_chronic_lateness",
    "served_slot_markers",
]
