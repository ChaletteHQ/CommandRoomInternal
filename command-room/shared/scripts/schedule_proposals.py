#!/usr/bin/env python3
"""
Later-add task proposals from client mix + substrate readiness
(Phase 3, corrected R3 — 2026-07).

The registration paths for later-add tasks have always existed
(change-schedule `add` / update-bridge); the gap was DISCOVERY — nothing
ever proposed the add, so a fully-qualified workspace (12 prospects, 8
clients, months of daily dormancy signal at the 2026-07-01 audit) sat
un-nudged forever. Open sub-question from the audit, answered at build
time: command-room-update-bridge never claimed this responsibility (its
add loop covers the SILENT_TASKS registry + first-install chats only), so
this is a NEW nudge, not a fix to an existing one.

DESIGN RULES:

- **Propose, never auto-register.** Registration requires Cowork's
  first-fire approval UX; silent registration attempts are a known
  reliability trap. The proposal line routes the CEO to the EXISTING add
  path ("say 'add staff meeting'"); nothing here registers anything.
- **Thresholds in ONE table** (`PROPOSAL_THRESHOLDS`) — tunable here and
  nowhere else.
- **LB1 R4 (M ruling 2026-07-14): the standalone relationship-moves chat is
  no longer proposed to new installs — the Staff Meeting is offered instead
  (it absorbs the moves as its "This week's moves" section, reusing the same
  machinery). EXISTING relationship-moves registrations are untouched by
  THIS module — it never proposed removals and still doesn't. Their
  supersede shipped in LB2 as the `rm_supersede_v1` update-bridge migration
  (propose-and-confirm, adjudication-gated, never silent —
  `schedule_config.rm_supersede_plan` is the pure planner; the bridge
  executes on the user's yes).**
- **Never both:** the Staff Meeting's moves section consumes dormancy
  detection (as relationship-moves did), so dormant-customer-scan is offered
  only as the LIGHTER alternative when the staff meeting doesn't land (not
  qualified, or proposed before and still not added).
- **No nagging:** each surfaced proposal logs a `schedule_add_proposed`
  event (registered type); the same proposal is suppressed for
  REPROPOSE_SUPPRESSION_WEEKS afterward.
- **ONE class is applied rather than proposed (SPEC TASKRET1, M's ruling
  2026-08-17).** `plan_readiness_retirements` plans the auto-disable of the
  READINESS-retired surfaces (`schedule_config.retirement_class` ==
  `readiness`); the update bridge executes it and NARRATES it in the update
  ack. This is the deliberate exception to "propose, never silent", and it is
  scoped by CLASS, not by task name: eliminations and renames still go through
  `propose_task_retirements` and still disable nothing on their own. The
  rationale — register-then-nag teaches the operator to ignore a surface
  permanently, so asking about these three would reproduce the failure being
  removed — lives in the class block above `schedule_config.RETIRED_TASKS`.

Surface: cleanup's Monday note (weekly, already fires — un-silent-killable
like the watchdog). The returned `line` strings are customer-ready.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_time import event_dt  # noqa: E402

# ---------------------------------------------------------------------------
# THE threshold table — the one place R3's numbers live.
# ---------------------------------------------------------------------------
PROPOSAL_THRESHOLDS = {
    # LB1 R4 — staff-meeting inherits relationship-moves' proposal slot (its
    # "This week's moves" section needs the same substrate the standalone
    # chat did), plus a queue-readiness path: a workspace whose brain has a
    # standing confirm queue earns the weekly review even before the client
    # mix matures.
    "staff-meeting": {
        # org mix: prospects + clients tracked in entities.json
        "min_prospect_plus_client_orgs": 8,
        # substrate readiness: at least this many distinct DAYS carrying
        # dormancy_signal events (aligned with the moves section's own
        # dormancy baselines — it needs accumulated cadence history).
        "min_dormancy_signal_days": 14,
        # OR-path: open Living Brain proposals waiting on the user
        "min_open_brain_proposals": 3,
    },
    "dormant-customer-scan": {
        # the lighter alternative when the staff meeting doesn't land
        "min_client_orgs": 5,
    },
    # `pipeline-digest` HAD a row here (PIPE1 Part 2 — gated on >=1 open
    # tracked deal). TASKRET1 removed it: the digest is READINESS-retired
    # (M's ruling 2026-08-17), and this table was its only automated offer
    # path, so the row's absence is what actually stops the offer. A test
    # pins the key ABSENT rather than pinning the table's contents, so
    # re-adding it is a deliberate act with a red suite in the way instead
    # of a quiet merge artifact. The re-offer condition is the registry's
    # `reoffer_when`, not this file.
}

REPROPOSE_SUPPRESSION_WEEKS = 6

# The update-bridge migration id that owns the readiness retirements
# (SPEC TASKRET1). Named for the CHANGE, not the release, because every other
# migration id in the bridge is (`staff_meeting_cadence_mwf_v1`,
# `rm_supersede_v1`, `claude_md_email_rule_v1`) — and because the adjudication
# gate keys on this string forever, so a release number baked into it would
# read as false precision the first time the migration is amended. Declared
# here rather than in the bridge's prose so the id the gate suppresses on and
# the id the event carries can never be two different strings.
READINESS_RETIREMENT_MIGRATION_ID = "readiness_retirement_v1"


def _now_local() -> _dt.datetime:
    """Naive MACHINE-local now.

    CLOCK1: the INSTANT is corroborated against the workspace ledger; the
    ZONE is untouched. Falls back to the raw machine clock if the helper is
    unavailable.
    """
    try:
        from trusted_now import trusted_now_local_naive

        return trusted_now_local_naive()
    except Exception:
        return _dt.datetime.now()


def _entities(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    inner = data.get("entities") if isinstance(data.get("entities"), dict) else None
    return inner or data


def _iter_events(workspace_root):
    try:
        import events_io

        yield from events_io.iter_events(workspace_root)
        return
    except Exception:
        pass
    p = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
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


def _org_mix(workspace_root) -> dict:
    orgs = _entities(workspace_root).get("orgs") or []
    counts = {"prospect": 0, "client": 0}
    for o in orgs:
        rel = o.get("relationship_type")
        if rel in counts and o.get("status") != "archived":
            counts[rel] += 1
    return counts


def propose_later_add_tasks(
    workspace_root,
    registered_ids,
    *,
    now: Optional[_dt.datetime] = None,
) -> list[dict]:
    """The R3 readiness check. Returns 0 or 1 proposal dicts
    ({task, line, reason}) — never more than one candidate in one round
    (staff-meeting > dormant-customer-scan; the 6-week suppression window
    rotates the round between qualifiers).

    Callers surface `line` verbatim, then log ONE `schedule_add_proposed`
    event per surfaced proposal via `log_proposal()`. This function only
    READS (entities.json, events.jsonl); it never writes, never registers.
    """
    now = now or _now_local()
    registered = set(registered_ids or ())
    mix = _org_mix(workspace_root)

    # one pass over events: dormancy-signal days + recent proposals
    dormancy_days = set()
    last_proposed: dict[str, _dt.datetime] = {}
    for ev in _iter_events(workspace_root):
        etype = ev.get("type")
        if etype == "dormancy_signal":
            dt = event_dt(ev)
            if dt:
                dormancy_days.add(dt.date())
        elif etype == "schedule_add_proposed":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            tid = data.get("taskId")
            dt = event_dt(ev)
            if tid and dt:
                dt = dt.astimezone().replace(tzinfo=None)
                if tid not in last_proposed or dt > last_proposed[tid]:
                    last_proposed[tid] = dt

    def suppressed(tid: str) -> bool:
        prior = last_proposed.get(tid)
        return bool(prior and (now - prior) < _dt.timedelta(weeks=REPROPOSE_SUPPRESSION_WEEKS))

    sm_thresholds = PROPOSAL_THRESHOLDS["staff-meeting"]
    mix_qualified = (
        mix["prospect"] + mix["client"] >= sm_thresholds["min_prospect_plus_client_orgs"]
        and len(dormancy_days) >= sm_thresholds["min_dormancy_signal_days"]
    )
    # Queue-readiness OR-path: a standing confirm queue earns the weekly
    # review on its own. Tolerant read — proposal machinery never blocks
    # the Monday note.
    n_open_proposals = 0
    try:
        import brain_proposals

        n_open_proposals = len(brain_proposals.load_open_proposals(workspace_root))
    except Exception:
        pass
    queue_qualified = n_open_proposals >= sm_thresholds["min_open_brain_proposals"]
    # A registered relationship-moves already covers the moves value (R4:
    # existing registrations untouched) — on those workspaces only the
    # queue path proposes the staff meeting.
    if "relationship-moves" in registered:
        sm_qualified = queue_qualified
    else:
        sm_qualified = mix_qualified or queue_qualified
    # A recent relationship-moves offer suppresses the staff meeting too —
    # it carries the same moves value under a new name, and re-offering it
    # inside the window is the exact weekly nag R3's suppression exists to
    # prevent (R4: prior RM offers count as offered-before).
    sm_suppressed = suppressed("staff-meeting") or suppressed("relationship-moves")
    if "staff-meeting" not in registered and sm_qualified and not sm_suppressed:
        if queue_qualified:
            reason = f"{n_open_proposals} proposals waiting on you"
            line = (
                f"Your brain has {n_open_proposals} suggestions waiting on a decision — "
                f"a weekly Staff Meeting reviews everything in one sitting: what I did on my "
                f"own, what's waiting on you, and this week's relationship moves. Say "
                f"'add staff meeting' and it runs Monday mornings."
            )
        else:
            reason = (f"{mix['prospect']} prospects + {mix['client']} clients, "
                      f"{len(dormancy_days)} days of dormancy signal")
            line = (
                f"You're tracking {mix['prospect']} prospects and {mix['client']} clients — "
                f"a weekly Staff Meeting keeps them warm and reviews everything waiting on "
                f"you in one sitting. Say 'add staff meeting' and it runs Monday mornings."
            )
        return [{"task": "staff-meeting", "reason": reason, "line": line}]

    # The PIPE1 pipeline-digest candidate USED to slot here, between the
    # staff meeting and the dormant-scan alternative. TASKRET1 removed it
    # whole (M's ruling 2026-08-17 — readiness retirement): the digest is out
    # of DEFAULT_SCHEDULES, so proposing an add for it would route the
    # customer at a task no registration path will create. The round order is
    # back to staff-meeting > dormant-customer-scan, exactly as it was before
    # PIPE1 Part 2, and the `deal_state` read that gated it is gone with it —
    # nothing here needs to know about deals any more.

    # Lighter alternative — only when the staff meeting did NOT land this
    # round (unqualified, already registered, or previously proposed and
    # still not added). Never both. Prior relationship-moves offers count
    # as "offered before" (the CEO already passed on the moves value).
    dcs_qualified = mix["client"] >= PROPOSAL_THRESHOLDS["dormant-customer-scan"]["min_client_orgs"]
    sm_previously_offered = ("staff-meeting" in last_proposed
                             or "relationship-moves" in last_proposed)
    if (
        dcs_qualified
        and not suppressed("dormant-customer-scan")
        # a registered moves surface consumes dormancy detection — never
        # offer the scan on top of one
        and "relationship-moves" not in registered
        and "staff-meeting" not in registered
        # only when the staff meeting did NOT land this round: unqualified,
        # or offered before and still not added (the CEO passed on it)
        and (not sm_qualified or sm_previously_offered)
    ):
        return [{
            "task": "dormant-customer-scan",
            "reason": f"{mix['client']} clients",
            "line": (
                f"You're tracking {mix['client']} clients — a lighter weekly check can flag "
                f"any that go quiet before they drift. Say 'dormant customer scan' to run it, "
                f"or 'tune the dormant scan' to schedule it weekly."
            ),
        }]
    return []


def log_proposal(workspace_root, task_id: str, *,
                 line: str = "", reason: str = "") -> bool:
    """Log ONE schedule_add_proposed event for a surfaced proposal — the
    suppression record, and since LIFECYCLE1 §7a the ONLY thing this writes.

    Suppression authority is unchanged and singular: propose_later_add_tasks
    consults THIS module's `schedule_add_proposed` log (6-week window)
    before any proposal surfaces. Returns False instead of raising —
    telemetry never blocks the surface.

    THE BP-RAIL WRITE IS GONE (LIFECYCLE1 §7a). LB2 §3a had migrated this
    writer onto `brain_proposals.propose(kind="schedule_add")` so the offer
    would sit on the one rail instead of scrolling away in a Monday note.
    STAFFCUT §3.6 then retired the schedule_add row — measured: 0 of 4
    proposals ever produced a registration, because registration only ever
    happens through the change-schedule / update-bridge add path, which the
    row never invoked and apply-choices explicitly refuses to invoke — but it
    retired the LEGACY ADAPTER, and this writer kept minting `bp_` rows on the
    other rail. One of them rendered on M's live staff meeting the next
    morning, verbs and all. A kind that is retired at the projector and still
    written is landfill with a TTL, so the write stops here.

    Rows already open on a live workspace are unaffected: the projector stops
    rendering them (`brain_proposals.RETIRED_KINDS`) and `expire_stale` still
    ages them out on their own TTL. Nothing is tombstoned early and nothing
    strands."""
    try:
        from event_gate import append_event

        append_event(
            Path(workspace_root) / "_hq" / "data" / "events.jsonl",
            {
                "type": "schedule_add_proposed",
                "source_skill": "cleanup",
                "data": {"taskId": task_id},
            },
            holder="schedule_proposals",
        )
    except Exception:
        return False
    return True


def propose_task_retirements(workspace_root, registered_ids=None,
                             now: Optional[_dt.datetime] = None,
                             task_records=None) -> list:
    """The RETIREMENT direction (SPEC LIFECYCLE1 §4) — the mirror of
    `propose_later_add_tasks`.

    Returns one dict per still-registered retired task
    ({task, line, reason}), or [] — which is the answer on the overwhelming
    majority of workspaces, because a task that was never registered has
    nothing to retire. That case is the FIRST thing this must get right:
    Pulse was a later-add, not a first-install chat, so most workspaces never
    had it, and a retirement flow that says anything at all to them is noise
    about a feature they never saw.

    PROPOSE, NEVER SILENT. The same posture as every schedule change: the line
    routes the customer to the EXISTING pause path (`pause <name>`) and this
    function registers, disables and writes nothing. Silently switching off a
    task the customer can see in their Scheduled list is the add-without-asking
    violation with the sign flipped.

    Suppression is symmetric with the add direction: one
    `schedule_retire_proposed` record per offer (written by
    `log_retire_proposal`), honored for RETIRE_SUPPRESSION_WEEKS. An offer the
    customer ignored is not an offer to repeat next week.

    ALREADY-TAKEN OFFERS ARE NOT RE-OFFERED (EOD2 / REVIEW F-2). Neither
    accept path REMOVES a task — there is no delete API, so `pause` and the
    rename switch both DISABLE, and registration's `registered_taskIds`
    preserves whatever existed. So "still in the registered set" is NOT
    evidence the customer hasn't acted, and a candidate filter that reads it
    that way re-offers forever, every six weeks. Two signals close it:

      * the RENAMED case — the SUCCESSOR is registered, so the switch has
        demonstrably happened; re-offering promises to register something
        already registered.
      * either case — the predecessor is DISABLED, which is what taking the
        offer does to it. Needs `task_records` (the scheduler readback), so
        callers that have it should pass it; without it this signal is
        simply unavailable and the successor check still carries the
        renamed case.

    The disabled check deliberately covers ELIMINATED retirements too: the
    machinery is LIFECYCLE1's and `pulse` has always had the same shape —
    "say `pause pulse`" offered to someone who already paused it is the same
    wrong sentence. EOD2 only escalated the blast radius from the few
    workspaces that ever had `pulse` to the whole fleet.

    THE READINESS CLASS IS NOT PROPOSED (SPEC TASKRET1). A retired row whose
    `retirement_class` is `readiness` never becomes a candidate here — its
    removal is APPLIED by the update bridge's readiness migration
    (`plan_readiness_retirements` below) and narrated in the update ack, on
    M's ruling that a per-task proposal for these three would rebuild the
    register-then-nag pattern the retirement exists to end. Surfacing both
    would be strictly worse than either: the customer would be asked to pause
    a chat the same update already switched off.

    The divergence is read from the CLASS, never from a name list, and a test
    proves it by flipping a fixture row's class and watching the other path
    activate. Eliminations and renames are untouched — they still propose,
    still suppress for six weeks, still disable nothing.

    READS ONLY (events.jsonl). Callers surface `line` verbatim, then call
    `log_retire_proposal()` per surfaced proposal.
    """
    from schedule_config import (RETIRE_SUPPRESSION_WEEKS, RETIRED_TASKS,
                                 is_readiness_retirement, retirement_line,
                                 retirement_reason)

    now = now or _now_local()
    registered = set(registered_ids or ())
    disabled = set()
    for rec in task_records or []:
        if not isinstance(rec, dict):
            continue
        tid = rec.get("taskId") or rec.get("task_id") or rec.get("id")
        if tid and rec.get("enabled") is False:
            disabled.add(tid)
    candidates = []
    for tid in RETIRED_TASKS:
        if tid not in registered:
            continue
        if is_readiness_retirement(tid):
            continue                      # applied, not proposed (TASKRET1)
        if tid in disabled:
            continue                      # already switched off — do not re-ask
        successor = RETIRED_TASKS[tid].get("renamed_to")
        if successor and successor in registered:
            continue                      # already switched — do not re-ask
        candidates.append(tid)
    if not candidates:
        return []

    last_proposed: dict = {}
    for ev in _iter_events(workspace_root):
        if ev.get("type") != "schedule_retire_proposed":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        tid = data.get("taskId")
        dt = event_dt(ev)
        if tid and dt:
            dt = dt.astimezone().replace(tzinfo=None)
            if tid not in last_proposed or dt > last_proposed[tid]:
                last_proposed[tid] = dt

    out = []
    for tid in candidates:
        prior = last_proposed.get(tid)
        if prior and (now - prior) < _dt.timedelta(weeks=RETIRE_SUPPRESSION_WEEKS):
            continue
        # `retirement_reason`, not the raw registry field: a RENAME row's
        # reason carries a `{time}` placeholder the registry resolves from
        # the successor's own cron (EOD2).
        out.append({"task": tid,
                    "reason": retirement_reason(tid),
                    "line": retirement_line(tid)})
    return out


def plan_readiness_retirements(registered_ids=None, task_records=None) -> list:
    """The READINESS direction (SPEC TASKRET1) — the third sibling of
    `propose_later_add_tasks` (add) and `propose_task_retirements` (remove by
    offer). This one plans a removal the update bridge APPLIES.

    Returns one dict per readiness-retired task that is still LIVE on this
    machine — `{task, line, reason, reoffer_when}` — or `[]`, which is the
    answer on the overwhelming majority of workspaces, because a task that
    was never registered has nothing to switch off. That case is the first
    thing this has to get right: all three of these were later-adds, so most
    installs never had any of them, and a migration that says anything at all
    to those workspaces is noise about a chat they never saw.

    PURE. It reads no workspace file, writes nothing, and calls no MCP —
    NOTHING in this module can reach a live scheduler, and a battery guard
    checks that as call syntax (which is why the scheduler tool is named in
    prose here and never written as a call). The bridge executes the plan:
    the scheduler's update tool with `enabled: false` per entry,
    `log_readiness_retirement()` per entry, and ONE
    `schedule_config.readiness_retirement_summary()` line in the update ack.
    Splitting plan from execution is what makes the class testable without a
    scheduler — the same split `rm_supersede_plan` uses.

    IDEMPOTENT ON TWO FENCES, deliberately.

      1. `task_records` (the raw `list_scheduled_tasks` readback) carries
         `enabled`, and an entry already disabled is dropped here. Pass them
         whenever the readback is in hand — on the bridge path it always is.
         Without them this signal is simply unavailable, exactly as it is for
         `propose_task_retirements`.
      2. The bridge's migration-adjudication gate, keyed on the migration id,
         which stops the whole block re-running after it has applied once.

    Either fence alone is enough; both exist because disabling is not the
    expensive half — re-writing a `schedule_config_changed` event on every
    update forever is, and fence 1 is what a workspace whose adjudication
    record was lost still has.

    NEVER pass a non-readiness retirement through here. Eliminations and
    renames are PROPOSED (`propose_task_retirements`), and auto-disabling one
    would be the add-without-asking violation with the sign flipped. The
    filter is `is_readiness_retirement`, read from the registry.
    """
    from schedule_config import (is_readiness_retirement, readiness_retired_task_ids,
                                 reoffer_condition, retirement_line,
                                 retirement_reason)

    registered = set(registered_ids or ())
    disabled = set()
    for rec in task_records or []:
        if not isinstance(rec, dict):
            continue
        tid = rec.get("taskId") or rec.get("task_id") or rec.get("id")
        if tid and rec.get("enabled") is False:
            disabled.add(tid)
    out = []
    for tid in sorted(readiness_retired_task_ids()):
        if tid not in registered or tid in disabled:
            continue
        if not is_readiness_retirement(tid):   # belt and braces; the set is derived
            continue
        out.append({
            "task": tid,
            # `retirement_reason`, never the raw registry field — it is the
            # one resolver for registry placeholders, and handing a raw one
            # to a customer-facing caller is the failure it exists to
            # prevent. Same reason `propose_task_retirements` uses it.
            "reason": retirement_reason(tid),
            "reoffer_when": reoffer_condition(tid),
            "line": retirement_line(tid),
        })
    return out


def log_readiness_retirement(workspace_root, task_id: str) -> bool:
    """Record ONE readiness retirement as a `schedule_config_changed` event —
    the SCHED1 discipline: a live schedule mutated by Command Room leaves a
    substrate record naming the task and the new state, per instance.

    Written by the update bridge AFTER the scheduler's update tool actually
    succeeded with `enabled: false` for `task_id`, never before — an event
    claiming a task is off while it still fires is worse than no event at all.

    Two readers depend on the shape rather than on this function: `late_fire`
    refuses to score any slot older than a task's newest
    `schedule_config_changed` (F-51), which is precisely right here — the
    slots this task will now never fire were minted by this change and must
    never surface as lateness. Returns False instead of raising: telemetry
    never blocks the migration that carried it.

    ROUTED THROUGH THE ONE WRITER (main-merge reconciliation, 2026-08-17).
    This function hand-rolled the event when it was written, because
    `schedule_config.log_schedule_config_change` did not exist on this branch
    yet — SCHED1 landed it on main the same day, and G33 caught the collision
    the moment the two met. The delegation is the point, not a tidy-up: the
    hand-rolled row omitted `cron`, skipped the id's spelling normalization,
    and typed the event name as a literal, so the readiness migration was the
    one pause path on the fleet whose record could drift away from every other
    pause path's. `reason` and `migration_id` ride as annotations; the row
    shape is the writer's.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        return False
    try:
        from schedule_config import log_schedule_config_change

        return log_schedule_config_change(
            workspace_root,
            [{"task_id": task_id.strip(), "cron": None, "enabled": False}],
            source_skill="command-room-update-bridge",
            extra_data={
                "reason": "readiness_retirement",
                "migration_id": READINESS_RETIREMENT_MIGRATION_ID,
            },
        ) is not None
    except Exception:
        return False


def log_retire_proposal(workspace_root, task_id: str) -> bool:
    """Log ONE `schedule_retire_proposed` suppression record. Additive event
    type — nothing is removed from the vocabulary and no existing reader
    changes. Returns False instead of raising: telemetry never blocks the
    surface that carried the offer."""
    try:
        from event_gate import append_event

        append_event(
            Path(workspace_root) / "_hq" / "data" / "events.jsonl",
            {
                "type": "schedule_retire_proposed",
                "source_skill": "cleanup",
                "data": {"taskId": task_id},
            },
            holder="schedule_proposals",
        )
    except Exception:
        return False
    return True


__all__ = [
    "PROPOSAL_THRESHOLDS",
    "REPROPOSE_SUPPRESSION_WEEKS",
    "READINESS_RETIREMENT_MIGRATION_ID",
    "propose_later_add_tasks",
    "log_proposal",
    "propose_task_retirements",
    "log_retire_proposal",
    "plan_readiness_retirements",
    "log_readiness_retirement",
]
