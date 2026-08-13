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

A SKIPPED RECEIPT IS VOIDED BY THE CONFIG CHANGE THAT REMOVES ITS REASON
------------------------------------------------------------------------
(SPEC BRIEFFIX1 Item D, 2026-08-09)

The uniform rule above treats every receipt alike: a receipt after the slot
means the slot was served. That is right for a COMPLETED run and wrong for a
SKIPPED one, because a skip is not work — it is a recorded answer to a
question the workspace was asked at that moment ("is a chat backend
declared?"), and a config change can make that answer obsolete before the next
slot arrives.

Lived on 2026-08-09: the chat leg skipped at 22:05 with "no chat backend is
declared", the user declared one at 22:15, and the leg stayed inert until
Monday 06:45 — a whole weekend of chat closures nobody was watching for —
because Friday's skip receipt was serving Friday's slot. Nothing was broken;
the rule simply could not see that the reason had evaporated.

So a job may declare `voided_by`: event shapes whose appearance AFTER a
skipped receipt voids it for dueness. COMPLETED receipts serve their slot
unconditionally — a config change never re-runs finished work. It lives on the
registry ROW rather than in the due function so the next config-skipped leg
inherits the behaviour by declaring one line, instead of by someone
remembering this paragraph.

PARTITIONED JOBS PROCESS EVERY MISSED PERIOD (CATCHUP1, 2026-07-28)
-------------------------------------------------------------------
Due-ness above answers "should this job run"; for most jobs that is the whole
question, because their work is a current-state pass or a cursor-driven span
that self-heals whenever it next runs. A `partitioned` job is different: each
PERIOD is its own deliverable under its own label. Miss the 1st of August and
the September fire produces August's report — and July's is lost forever,
because "the previous full calendar month" is measured from `now` and the
job's due rule (`expected_fires(count=1)`) structurally cannot see that two
periods went unserved. So a partitioned job's due dict additionally carries
`periods` — every unserved nominal slot since its last receipt, oldest first
(`catchup.missed_periods`) — and the registered prompt produces ONE
deliverable per entry. Non-partitioned jobs are untouched: no `periods` key,
same dict they have always returned.
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
    # CHATSCAN1 — the chat closure leg, ordered IMMEDIATELY BESIDE the mail
    # one and on the identical nominal cron. M's ruling 2026-08-06: closing
    # and review from chat are wired into the maintenance cadence exactly like
    # mail, first-class and registered, never an on-demand extra. Same slot as
    # `reconcile-sent` and ordered after it so both legs land before the 7:00
    # brief reads the substrate. It rides the already-authorized `maintenance`
    # taskId, so it adds ZERO scheduled tasks on any machine. A workspace with
    # no declared chat backend still runs it: the leg skips silently and
    # writes its skip receipt, which is what keeps "no chat backend" readable
    # apart from "swept and found nothing". Entry point:
    # chat_reconcile.reconcile_chat_and_receipt.
    "reconcile-chat": {
        "skill": "reconcile-sent (chat leg — shared/scripts/chat_reconcile.py)",
        "nominal_cron": "45 6,12,17 * * 1-5",
        "description": "close commitments discharged in the declared chat backend",
        # BRIEFFIX1 Item D — this leg's ONE skip reason is "no chat backend is
        # declared", and declaring one is exactly the event below. A skip
        # receipt written before that declaration is answering a question the
        # workspace has since answered differently, so it stops serving its
        # slot and the leg runs at the next fire instead of at the next slot.
        "voided_by": (
            {"type": "connector_backend_changed", "match": {"category": "chat"}},
        ),
        "voided_reason": ("its last run skipped because no chat backend was "
                          "declared, and one has been declared since"),
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
    # PID1 D7 — the identity reconciler: same Sunday slot, ordered AFTER
    # deal-signals (settled substrate first; Monday's Staff Meeting opens
    # with a fresh, clustered identity queue). Entry point:
    # identity_reconcile.run_identity_reconcile(workspace_root, apply=True,
    # caps=STEADY_CAPS) — auto-adds ride the R1 rail (narrated + batch-
    # undoable), links/merges are propose-only, caps spill narrated.
    "identity-reconcile": {
        "skill": "identity reconciler (shared/scripts/identity_reconcile.py "
                 "--apply — dry-run without the flag)",
        "nominal_cron": "0 17 * * 0",
        "description": "reconcile person identities: auto-add corroborated "
                       "people, link/merge-propose the rest for review",
    },
    # LIFECYCLE1 — the project lifecycle pass, same Sunday slot, ordered LAST
    # of the Sunday group: it reads the settled week AND it reads the expiry
    # tombstones `cleanup`'s `brain_proposals.expire_stale` sweep writes
    # earlier in this same fire (an ask that expired unanswered is the
    # precondition for the active->dormant flip, so running before cleanup
    # would delay every flip by a week). Entry point:
    # lifecycle_pass.run_lifecycle_pass(workspace_root, apply=True) — dormancy
    # asks ride the LB2 confirm rail (on-demand rows, never a scheduled
    # surface), and the dormant->archived leg goes through
    # thread_archive.archive_thread, THE archive chokepoint.
    "lifecycle": {
        "skill": "project lifecycle pass (shared/scripts/lifecycle_pass.py "
                 "--apply — dry-run without the flag)",
        "nominal_cron": "0 17 * * 0",
        "description": "ask about projects gone quiet; retire and revive the "
                       "ones the lifecycle rules already decided",
    },
    # Nominal midnight on the 1st -> due at the first fire on/after the 1st.
    # PARTITIONED (CATCHUP1 F-3): one report per missed month, each labelled
    # with its own month. A machine closed across a 1st loses that month
    # entirely without this — the next fire produces the NEWEST prior month
    # and the skipped one is never written.
    "monthly-report": {
        "skill": "operator-report + value-receipt",
        "nominal_cron": "0 0 1 * *",
        "description": "monthly operating report + value receipt for the prior month",
        "partitioned": True,
    },
}

# OPT-IN jobs (SPEC OUT7). A separate registry from MAINTENANCE_JOBS so the
# silent-job execution ORDER above stays the load-bearing contract it is (the
# order pin never sees these). An optional job is NEVER due unless the
# workspace explicitly opted it in — `schedule_config.maintenance_jobs.<id> =
# {"enabled": true}`, written only after a propose-and-confirm through
# enable-command-room-schedules. This is the "never auto-registered" posture in
# code: the job rides inside the already-authorized `maintenance` task (zero
# client Run-Now), but it stays inert until the user turns it on, and its own
# pack_run receipt self-limits it to its nominal cadence thereafter. Due
# optional jobs run AFTER the core jobs.
OPTIONAL_JOBS: dict[str, dict] = {
    # The monthly KPI scorecard (SPEC OUT7 §3c route 2). Renders the prior
    # month's scorecard via scorecard.py through the board-pack render path;
    # writes a pack_run receipt tagged monthly-scorecard so the uniform due
    # rule self-limits it to once a month. Opt-in ONLY.
    "monthly-scorecard": {
        "skill": "scorecard (shared/scripts/scorecard.py) via the board-pack render path",
        "nominal_cron": "0 0 1 * *",
        "description": "monthly KPI scorecard for the prior month (opt-in)",
        "opt_in": True,
        # Same class as monthly-report (CATCHUP1 F-4): a scorecard IS its
        # month. One per missed period, never one standing in for several.
        "partitioned": True,
    },
}

MAINTENANCE_TASK_ID = "maintenance"
RECEIPT_EVENT_TYPE = "maintenance_run"

# CHATSCAN1 V1b — THE CLOSURE ROSTER.
#
# The set of legs a maintenance fire is expected to close commitments through.
# Declared HERE, separately from MAINTENANCE_JOBS, and that separation is the
# whole mechanism: if a leg were only ever "expected" because it appeared in
# the job registry, then deleting it from the registry would delete the
# expectation too, and a fire that reconciled mail and not chat would report
# a clean, complete run. The roster is the independent statement of what
# SHOULD be there, so a missing leg reads as a GAP instead of a smaller plan.
#
# A gap is not an error — a workspace can legitimately disable a leg — but it
# is never invisible: it lands on the receipt as `roster_gap`, and
# `validate_maintenance_ran` refuses to call such a run complete.
RECONCILE_LEGS: tuple = ("reconcile-sent", "reconcile-chat")


def roster_gap(jobs=None) -> list:
    """Which closure legs the registry is MISSING, in roster order.

    `jobs` defaults to the live `MAINTENANCE_JOBS` keys. A leg the workspace
    explicitly disabled is NOT a gap — it is a registered leg that was turned
    off, which `skipped_disabled` already records honestly. A gap means the
    leg is not in the roster's registry at all: nothing will run it, nothing
    will report it, and without this check nothing would say so."""
    registered = set(jobs if jobs is not None else MAINTENANCE_JOBS.keys())
    return [leg for leg in RECONCILE_LEGS if leg not in registered]


def _all_job_ids() -> list[str]:
    """Every job id whose receipts drive due-ness — core plus optional."""
    return list(MAINTENANCE_JOBS) + list(OPTIONAL_JOBS)


def _optin_enabled(overrides: dict, job_id: str) -> bool:
    """An opt-in job is due-eligible ONLY when the workspace turned it on
    (`{"enabled": true}` in schedule_config.maintenance_jobs). Absent /
    malformed / anything but an explicit True = not opted in (never auto)."""
    ov = overrides.get(job_id)
    return isinstance(ov, dict) and ov.get("enabled") is True


def _job_overrides(workspace_root) -> dict:
    """Job-level overrides from entities.json
    `workspace.schedule_config.maintenance_jobs.<job_id>` — change-schedule can
    pause ONE job (`{"enabled": false}`) without touching the task. Defensive:
    missing/corrupt config means no overrides."""
    # SPEC SYNC1 B1 — route the entities.json read through the (dormant)
    # resolver; byte-identical to `_hq/data/entities.json` with no override.
    try:
        from data_root import resolve as _resolve_data_root
        path = _resolve_data_root(workspace_root) / "entities.json"
    except Exception:
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


SKIPPED_STATUS = "skipped"


def _newest_receipt(workspace_root, job_id) -> tuple:
    """`(status, machine-local naive dt)` for this job's newest RECEIPT, or
    `(None, None)`.

    Deliberately returns the receipt's own instant rather than reusing the
    dueness `last` (review F11). `task_watchdog.last_receipts` takes the newer
    of {receipt, analytical-view mtime} for jobs that declare `views`, so
    `last` is not always a receipt timestamp — and comparing a voiding EVENT
    against a view's file mtime while reading the status from a receipt would
    be two clocks in one decision. No job declares both today; the divergence
    is one registry row away, and it would be invisible when it arrived.

    Best-effort like everything else here: an unreadable substrate must never
    crash a fire, and "unknown" falls through to the unchanged uniform rule.
    """
    try:
        from receipts import iter_receipts

        rows = iter_receipts(workspace_root, task_ids=[job_id])
    except Exception:  # noqa: BLE001
        return None, None
    newest, newest_dt = None, None
    for r in rows or []:
        dt = r.get("dt")
        if dt is None:
            continue
        if newest_dt is None or dt > newest_dt:
            newest, newest_dt = r, dt
    if newest is None:
        return None, None
    status = newest.get("status")
    return (status if isinstance(status, str) else None,
            _to_local_naive(newest_dt))


def _voiding_event_after(workspace_root, specs, after: _dt.datetime) -> Optional[str]:
    """The newest event matching any of `specs` that is NEWER than `after`
    (machine-local naive), as an ISO string — or None.

    `specs` are the registry's own `voided_by` rows: `{"type": ...,
    "match": {<data key>: <value>}}`. Matching on the DATA payload rather than
    on the type alone is what keeps a mail-backend change from re-arming the
    chat leg — same event type, different category, unrelated fact.
    """
    try:
        import events_io
        from event_time import event_dt

        events = events_io.iter_events(workspace_root)
    except Exception:  # noqa: BLE001
        return None
    wanted = []
    for spec in specs or ():
        if not isinstance(spec, dict) or not spec.get("type"):
            continue
        wanted.append((spec["type"], dict(spec.get("match") or {})))
    if not wanted:
        return None
    newest = None
    try:
        for ev in events:
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if not any(etype == t and all(data.get(k) == v for k, v in m.items())
                       for t, m in wanted):
                continue
            dt = event_dt(ev)
            if dt is None:
                continue
            local = _to_local_naive(dt)
            if local is None or local <= after:
                continue
            if newest is None or local > newest:
                newest = local
    except Exception:  # noqa: BLE001
        return None
    return newest.isoformat() if newest else None


def _skip_rearm(workspace_root, job_id, spec, last: _dt.datetime) -> Optional[str]:
    """Is this job's served slot VOIDED because the config changed under a
    SKIPPED receipt? Returns the voiding event's ISO instant, or None.

    Two conditions, both required and both deliberate:
      * the newest receipt says `skipped` — a completed run serves its slot
        unconditionally, because a config change is not a reason to redo
        finished work;
      * a declared voiding event is NEWER than that receipt — the reason the
        run skipped is gone.

    "Newer than that receipt" means newer than the RECEIPT's own instant, not
    than the dueness `last` this function is handed: for a job declaring
    `views`, `last` can be an analytical-view mtime, and one decision must not
    straddle two clocks (review F11). `last` stays the parameter because it is
    the correct fallback when the receipt read fails.

    Only ever consulted for a job that declares `voided_by`, so the common
    path pays nothing.
    """
    specs = spec.get("voided_by")
    if not specs:
        return None
    status, receipt_dt = _newest_receipt(workspace_root, job_id)
    if status != SKIPPED_STATUS:
        return None
    return _voiding_event_after(workspace_root, specs, receipt_dt or last)


def dispatch_plan(workspace_root, now: Optional[_dt.datetime] = None) -> dict:
    """The full fire plan: which jobs are due (ordered), which were skipped by
    a job-level disable. Machine-local naive `now` (the clock cron evaluates
    in); defaults to the real clock.

    Returns {"now": iso, "due": [job dicts], "skipped_disabled": [job ids]}.
    Each due dict: {job_id, skill, description, reason, last_receipt (iso|None),
    slot (iso)}.

    A job whose newest receipt is `skipped` and whose registry row declares a
    `voided_by` event that has since fired is due ANYWAY (BRIEFFIX1 Item D) —
    its due dict carries `skip_voided_at` and a reason naming the change.
    Completed receipts are unaffected.

    A job registered `partitioned` (CATCHUP1) carries two more keys:
    `periods` — every unserved nominal slot since its last receipt, ISO,
    OLDEST FIRST, one deliverable owed per entry — and `periods_capped`,
    True when more periods were missed than `catchup.DEFAULT_PERIOD_CAP` and
    the oldest were dropped. The list always contains at least the slot the
    job is due for, so the prompt can iterate `periods` unconditionally for
    a partitioned job.
    """
    now = now or _now_local()
    if now.tzinfo is not None:
        now = _to_local_naive(now)
    overrides = _job_overrides(workspace_root)
    lasts = last_receipts(workspace_root, _all_job_ids())

    def _due_dict(job_id: str, spec: dict) -> Optional[dict]:
        try:
            slots = expected_fires(spec["nominal_cron"], now=now, count=1)
        except CronParseError:
            return None  # unparseable registry cron — never crash a fire
        if not slots:
            return None
        slot = slots[0]
        last = lasts.get(job_id)
        voided_at = None
        if last is not None and last >= slot:
            voided_at = _skip_rearm(workspace_root, job_id, spec, last)
            if voided_at is None:
                return None  # already served this slot
        if voided_at is not None:
            reason = (f"skipped at {last.isoformat()} — "
                      f"{spec.get('voided_reason') or 'the reason it skipped no longer applies'} "
                      f"(recorded {voided_at})")
        else:
            reason = ("no run recorded yet" if last is None else
                      f"last ran {last.isoformat()}, its {slot.isoformat()} slot has passed")
        d = {
            "job_id": job_id,
            "skill": spec["skill"],
            "description": spec["description"],
            "reason": reason,
            "last_receipt": last.isoformat() if last else None,
            "slot": slot.isoformat(),
        }
        if voided_at is not None:
            # Named on the due dict so the fire's own receipt can say WHY this
            # job ran outside its slot — a job appearing off-cadence with no
            # recorded cause is the next reader's mystery.
            d["skip_voided_at"] = voided_at
        if spec.get("partitioned"):
            # Each period is its own deliverable — enumerate every one that
            # went unserved, oldest first. Best-effort like everything the
            # dispatcher does: if the enumeration fails, fall back to the one
            # slot the due rule already computed, so a partitioned job never
            # loses its normal fire to a catch-up failure.
            try:
                from catchup import DEFAULT_PERIOD_CAP, missed_periods

                # Ask for one MORE than the cap so `periods_capped` is exact:
                # a gap of exactly cap periods dropped nothing, and flagging
                # it would have the prompt report a shortfall on a clean
                # sweep. Only a cap+1-th period proves something fell off.
                periods = missed_periods(spec["nominal_cron"], last, now=now,
                                         cap=DEFAULT_PERIOD_CAP + 1)
                capped = len(periods) > DEFAULT_PERIOD_CAP
                if capped:
                    periods = periods[-DEFAULT_PERIOD_CAP:]
            except Exception:  # noqa: BLE001 — never crash a fire
                periods, capped = [], False
            if not periods:
                periods = [slot]
                capped = False
            d["periods"] = [p.isoformat() for p in periods]
            d["periods_capped"] = capped
        return d

    due: list[dict] = []
    skipped_disabled: list[str] = []
    # Core silent jobs — always considered (opt-OUT via {"enabled": false}).
    for job_id, spec in MAINTENANCE_JOBS.items():
        override = overrides.get(job_id)
        if isinstance(override, dict) and override.get("enabled") is False:
            skipped_disabled.append(job_id)
            continue
        d = _due_dict(job_id, spec)
        if d is not None:
            due.append(d)
    # Opt-IN jobs — considered ONLY when the workspace turned them on; they run
    # after the core jobs and never appear in the order-is-the-contract pin.
    for job_id, spec in OPTIONAL_JOBS.items():
        if not _optin_enabled(overrides, job_id):
            continue  # never auto: no confirmation => not registered => not due
        d = _due_dict(job_id, spec)
        if d is not None:
            due.append(d)
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
            # CHATSCAN1 V1b — computed at WRITE time from the live registry,
            # never handed in by the caller. A caller-supplied value would be
            # a claim; this is a measurement, and it is the difference between
            # a fire that is genuinely complete and one that is missing a
            # closure leg nobody noticed had gone.
            "roster_gap": roster_gap(),
            "reconcile_legs": list(RECONCILE_LEGS),
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
    gap = data.get("roster_gap") or []
    out = {
        "ok": True,
        "reason": None,
        "dt": newest_dt.isoformat() if newest_dt else None,
        "fired_at_slot": data.get("fired_at_slot"),
        "jobs_due": data.get("jobs_due") or [],
        "jobs_completed": data.get("jobs_completed") or [],
        "jobs_failed": data.get("jobs_failed") or [],
        "skipped_disabled": data.get("skipped_disabled") or [],
        # CHATSCAN1 V1b — a closure leg missing from the roster is reported
        # every time this receipt is read back, not only at the fire that
        # wrote it.
        "roster_gap": list(gap),
        "reconcile_legs": data.get("reconcile_legs") or list(RECONCILE_LEGS),
    }
    if gap:
        # NOT ok. A run that closed commitments through some of its declared
        # channels and silently not the others is the "visibly incomplete vs
        # silently partial" distinction this whole check exists to draw — and
        # a validator that answered True here would be the thing making it
        # silent.
        out["ok"] = False
        out["reason"] = (
            "this maintenance run reconciled only part of what it is supposed "
            "to: no leg is registered for " + ", ".join(gap))
    return out


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
    "OPTIONAL_JOBS",
    "MAINTENANCE_TASK_ID",
    "RECEIPT_EVENT_TYPE",
    "RECONCILE_LEGS",
    "roster_gap",
    "dispatch_plan",
    "due_jobs",
    "maintenance_receipt",
    "validate_maintenance_ran",
]


if __name__ == "__main__":
    sys.exit(main())
