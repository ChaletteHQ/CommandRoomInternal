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
  path ("say 'add relationship moves'"); nothing here registers anything.
- **Thresholds in ONE table** (`PROPOSAL_THRESHOLDS`) — tunable here and
  nowhere else.
- **Never both:** relationship-moves consumes dormancy detection, so
  dormant-customer-scan is offered only as the LIGHTER alternative when
  relationship-moves doesn't land (not qualified, or proposed before and
  still not added).
- **No nagging:** each surfaced proposal logs a `schedule_add_proposed`
  event (registered type); the same proposal is suppressed for
  REPROPOSE_SUPPRESSION_WEEKS afterward.

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
    "relationship-moves": {
        # org mix: prospects + clients tracked in entities.json
        "min_prospect_plus_client_orgs": 8,
        # substrate readiness: at least this many distinct DAYS carrying
        # dormancy_signal events (aligned with relationship-moves' own
        # dormancy baselines — it needs accumulated cadence history).
        "min_dormancy_signal_days": 14,
    },
    "dormant-customer-scan": {
        # the lighter alternative when relationship-moves doesn't land
        "min_client_orgs": 5,
    },
}

REPROPOSE_SUPPRESSION_WEEKS = 6


def _now_local() -> _dt.datetime:
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
    ({task, line, reason}) — never both candidates in one round.

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

    rm_thresholds = PROPOSAL_THRESHOLDS["relationship-moves"]
    rm_qualified = (
        mix["prospect"] + mix["client"] >= rm_thresholds["min_prospect_plus_client_orgs"]
        and len(dormancy_days) >= rm_thresholds["min_dormancy_signal_days"]
    )
    if "relationship-moves" not in registered and rm_qualified and not suppressed("relationship-moves"):
        return [{
            "task": "relationship-moves",
            "reason": f"{mix['prospect']} prospects + {mix['client']} clients, "
                      f"{len(dormancy_days)} days of dormancy signal",
            "line": (
                f"You're tracking {mix['prospect']} prospects and {mix['client']} clients — "
                f"a weekly outreach pack keeps them warm. Say 'add relationship moves' and it "
                f"runs Sunday evenings, ready Monday morning."
            ),
        }]

    # Lighter alternative — only when relationship-moves did NOT land this
    # round (unqualified, already registered, or previously proposed and
    # still not added). Never both.
    dcs_qualified = mix["client"] >= PROPOSAL_THRESHOLDS["dormant-customer-scan"]["min_client_orgs"]
    rm_previously_offered = "relationship-moves" in last_proposed
    if (
        dcs_qualified
        and not suppressed("dormant-customer-scan")
        # a registered relationship-moves consumes dormancy detection — never
        # offer the scan on top of it
        and "relationship-moves" not in registered
        # only when relationship-moves did NOT land this round: unqualified,
        # or offered before and still not added (the CEO passed on it)
        and (not rm_qualified or rm_previously_offered)
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


def log_proposal(workspace_root, task_id: str) -> bool:
    """Log ONE schedule_add_proposed event for a surfaced proposal (the
    suppression record). Returns False instead of raising — telemetry
    never blocks the surface."""
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
        return True
    except Exception:
        return False


__all__ = [
    "PROPOSAL_THRESHOLDS",
    "REPROPOSE_SUPPRESSION_WEEKS",
    "propose_later_add_tasks",
    "log_proposal",
]
