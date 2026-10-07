#!/usr/bin/env python3
"""FOLD1A (SPEC_FLOW1 Lane H) — the update itself folds the two daily chats.

WHY THIS EXISTS
---------------
Waiting On and My Plate fire as their own daily chats, and both are now
redundant: My Plate's number already leads the morning brief (PLATE1 night 2
/ ONEPLATE1), and Waiting On's headline gains the same treatment in this
build (`surface_drivers.py`'s fold_lines — "N owed to you"). Eight scheduled
touches a day on twenty-one seats is the thing DESIGN_RULE_THREE_SURFACES
names directly: "we want to show less options to clients — they are
overwhelmed." This action is the ONE-SHOT that switches both chats off on
every seat at update time, so the fold reaches existing customers the same
day it ships rather than waiting for each one to notice and pause it by hand.

WHAT IT DOES, AND WHAT IT WILL NEVER DO
----------------------------------------
Records, in ONE batch, that both chats are folded — never closes, drops or
deletes anything, never touches a single commitment row, and never disables
the on-demand doors (`show waiting`, `what's on my plate` keep working
exactly as they do today; so does the Inbox chat, untouched by this action or
by anything in FOLD1A). It never asks a question.

THIS IS NOT A READINESS RETIREMENT (schedule_config.RETIRED_TASKS)
--------------------------------------------------------------------
Read `schedule_config.FOLDED_FIRES`'s own module comment for the full
reasoning. In one line: a readiness retirement is permanent and refuses
`add` forever, because the SUBSTRATE was not ready. Waiting On and My Plate
are fully working; this is a deliberate simplification the customer can
reverse. So this class carries a real `undo` (a registered `brain_undo`
reverser, `schedule_fold_disable`) and `DEFAULT_SCHEDULES` keeps both rows'
cron/label forever — `add waiting on` / `add my plate` always has real
metadata to register from, on this seat or a fresh one.

WHAT THIS MODULE WRITES, AND WHAT IT DOES NOT
------------------------------------------------
This module writes the SUBSTRATE half only — the workspace's own
`workspace.schedule_config` store (`enabled: false` for both ids) AND its
matching `schedule_config_changed` audit event, both through the one
sanctioned writer for this class (`schedule_config.apply_fold_state`, ONE
event, ONE `brain_batch_id`), plus the ONE `plugin_update_remediation`
receipt. FIX ROUND 1 (REVIEW_FOLD1A F-1): the store half is new — the first
cut wrote the audit event alone, and the per-fire gate below reads the
store, so the gate was permanently off. It does NOT and CANNOT call the
live scheduler: the `auto_apply` action contract (`release_actions/__init__.py`)
hands this function `(events_jsonl_path, workspace_root, detector_context)`
only — no MCP tool is reachable from inside a plain function, the same reason
`schedule_proposals.plan_readiness_retirements` is documented PURE and leaves
the disable call (`schedule_backend.plan_update`) to the bridge's own
SKILL.md prose. The live half of this fold is one short paragraph in
`skills/command-room-update-bridge/SKILL.md` (the `fold1a_fire_fold_v1`
migration, mirroring `readiness_retirement_v1`'s own two-step shape: the
agent calls `schedule_backend.plan_update(enabled=False)` for whichever of the two
ids `detector_context["task_records"]` shows registered+enabled, and ONLY
after each call succeeds does it invoke `apply_plan` below — never the
reverse order, per the codebase's own rule that an event claiming a task is
off while it still fires is worse than no event at all
(`schedule_proposals.log_readiness_retirement`'s docstring)).

Content stops for the WINDOW between this write and the live disable, because
`references/orchestrator-commitments.md` and `references/orchestrator-my-plate.md`
open with a Step 0 gate that reads `schedule_config.fold_is_active` — the
workspace's own store — on every fire, and the orchestrator body is read
fresh at every fire (`enable-command-room-schedules` SKILL.md). The live
disable is what stops the (now empty) notification from firing at all; the
gate is what stops it from saying anything if it fires first.

FIX ROUND 1 (F-1) corrects the claim this paragraph used to make. It said the
stop was immediate "the moment the code ships, not only after the customer
updates". It is not, and it was never going to be: the gate reads state that
only THIS action writes, so the stop begins when the fold is applied, not
when the code lands. That is the right window anyway — the gate is now
reading a receipted, undoable act rather than deciding on its own that a
customer's chat should go quiet.

ONCE, AND ONLY ONCE
--------------------
The action's own `plugin_update_remediation` receipt (`item_id`) is the
one-shot marker — a seat that has taken its fold (or reversed it with
`undo`) never gets a second unsolicited fold. `undo` restores exactly the
substrate half this module wrote; the bridge migration's own "already
disabled" check (mirroring readiness_retirement_v1's `task_records` fence)
keeps a seat that already folded from writing the event twice.

WIRING (pinned; the release cut pastes the stanza from BUILD_FOLD1A)
---------------------------------------------------------------------
  id               v5300_fold_scheduled_fires
  action           auto_apply
  action_module    release_actions.fold_scheduled_fires
  action_function  fold_scheduled_fires
  detector         release_detectors.always.always_applies
No manifest FILE ships on this branch (the cut writes `shared/releases/
v5.30.0.json`); `MANIFEST_ITEM` below is the exact stanza.

Signature per references/RELEASE_MANIFEST.md "Action contract":
    fn(events_jsonl_path, workspace_root, detector_context) -> dict
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

ITEM_ID = "v5300_fold_scheduled_fires"
ACTION_MODULE = "release_actions.fold_scheduled_fires"
ACTION_FUNCTION = "fold_scheduled_fires"

#: WHO acted — same identity discipline as CLEANUP1's `park_silent_backlog`
#: (ATTRIB2's actor resolver reads `source_skill` by identity: `machine`).
SOURCE_SKILL = "command-room-update-bridge"

#: The two ids this fold touches — read from the registry, never retyped.
FOLDED_TASK_IDS = ("waiting-on", "my-plate")

#: `fsf_<UTC to the second>-<8 hex>` — one batch per fold, distinct from
#: CLEANUP1's `clp_` prefix so the two one-shots are never mistaken for the
#: same batch class in a listing.
BATCH_PREFIX = "fsf_"
BATCH_SALT_BYTES = 4

RECEIPT_TYPE = "plugin_update_remediation"
#: CONTRACT Rule 36 (CLEANUP1, `release_actions.park_silent_backlog`) — the
#: bridge writes its OWN `plugin_update_remediation` audit row for every item
#: that surfaces (SKILL.md Step 4.8c), so "which of these is the one-shot
#: marker" has to be answerable by a FIELD and not by the type alone. Step
#: 4.8c skips its own row when the action already wrote one; the role keeps
#: the marker unambiguous on any seat where an older bridge wrote both.
#: FIX ROUND 1, REVIEW_FOLD1A F-6 — the first cut wrote no role at all and
#: matched on `item_id` alone, so a bridge-written audit row (or a run that
#: errored after surfacing) could silence this fold on that seat forever.
RECEIPT_ROLE = "marker"
RECEIPT_OUTCOME_APPLIED = "applied"

#: `brain_undo`'s registry key for this class (see `brain_undo.py`
#: `REVERSERS["schedule_fold_disable"]`).
BRAIN_CHANGE_CLASS = "schedule_fold_disable"

MANIFEST_ITEM = {
    "id": ITEM_ID,
    "detector_module": "release_detectors.always",
    "detector_function": "always_applies",
    "action": "auto_apply",
    "action_module": ACTION_MODULE,
    "action_function": ACTION_FUNCTION,
    "notice_template": "{summary_line}",
    "fallback_prompt_template": (
        "Folding your Waiting On and My Plate chats into the morning brief "
        "is pending; it will retry on the next update."
    ),
}


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _sc_fold_reason() -> str:
    """`schedule_config.FOLD_REASON`, read rather than re-typed (fix round 2,
    R-3): the store's discriminator and this audit event's `reason` are the
    same fact and must be the same string."""
    from schedule_config import FOLD_REASON
    return FOLD_REASON


def _mint_batch_id(now_iso=None) -> str:
    import secrets
    if now_iso:
        from event_time import parse_ts
        when = parse_ts(now_iso) or datetime.now(timezone.utc)
    else:
        when = datetime.now(timezone.utc)
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(BATCH_SALT_BYTES)}"


def already_ran(workspace_root) -> bool:
    """Has this seat already had its one-shot? The SAME discipline as
    CLEANUP1's `park_silent_backlog.already_ran`, now actually spelled that
    way (fix round 1, F-6). The action's own receipt is the marker, read
    through `events_io` like every full-history read, and a row only counts
    when it is:

      * THIS item (`item_id`), and
      * THIS action's row — `receipt_role: "marker"` OR the action module's
        own name in `source`, so a bridge-written audit row for the same
        item is not mistaken for the marker (CONTRACT Rule 36), and
      * an APPLIED one — a receipt from a run that folded nothing must not
        silence the item forever.

    The bar CLEANUP1 also matches on has no analogue here: a fold has no
    threshold to re-arm against, and its two ids are the whole class.

    A read that fails answers False — a missed marker costs a second pass
    that folds nothing new (both ids are already off, and `plan`'s own
    `task_records` check narrows it to zero), while a marker invented out
    of an error would silence the fold on that seat forever."""
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:
        return False
    for ev in events:
        if (ev.get("type") or ev.get("event")) != RECEIPT_TYPE:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get("item_id") != ITEM_ID:
            continue
        if not (d.get("receipt_role") == RECEIPT_ROLE
                or d.get("source") == ACTION_MODULE):
            continue
        if d.get("outcome") != RECEIPT_OUTCOME_APPLIED:
            continue
        return True
    return False


def plan(workspace_root, *, task_records=None) -> dict:
    """Pure read — nothing is written. Statuses: `not_a_workspace`,
    `already_ran`, `nothing_to_fold`, `would_fold`.

    `task_records` (the live readback through the seam —
    `schedule_backend.plan_list`, executed and normalised — same shape
    `schedule_proposals.plan_readiness_retirements` expects) is OPTIONAL:
    without it, this still plans the fold for the SUBSTRATE half (the
    content stop is unconditional at the file level regardless of whether
    the scheduler's own readback is in hand), it just cannot say which of the
    two ids are actually LIVE on this seat — `tasks_to_disable` is then both
    ids, and the bridge's own live-registration check is what narrows it
    before the real `schedule_backend.plan_update` calls."""
    ws = Path(workspace_root)
    base = {"status": "not_a_workspace", "tasks_to_disable": []}
    if not (ws / "_hq" / "data").is_dir():
        return base
    if already_ran(ws):
        return {**base, "status": "already_ran"}
    live = set()
    for rec in task_records or []:
        if not isinstance(rec, dict):
            continue
        tid = rec.get("taskId") or rec.get("task_id") or rec.get("id")
        if tid in FOLDED_TASK_IDS and rec.get("enabled") is not False:
            live.add(tid)
    tasks_to_disable = sorted(live) if task_records is not None else list(FOLDED_TASK_IDS)
    if not tasks_to_disable:
        return {**base, "status": "nothing_to_fold"}
    return {**base, "status": "would_fold", "tasks_to_disable": tasks_to_disable}


def apply_plan(workspace_root, planned: dict, *, batch_id=None,
               now_iso=None) -> dict:
    """Write the substrate half in ONE call, under ONE batch id: the
    workspace's OWN `workspace.schedule_config` store (`enabled: false` for
    every id in `planned["tasks_to_disable"]`) AND the matching
    `schedule_config_changed` audit event, through the SANCTIONED writer
    (`schedule_config.apply_fold_state`, which is itself the one caller of
    `log_schedule_config_change` for this class — never hand-rolled, per
    that writer's own SPEC SCHED1 §0-4 mandate).

    FIX ROUND 1, REVIEW_FOLD1A F-1. This used to call
    `log_schedule_config_change` DIRECTLY — an audit-event writer that never
    touches the store. `schedule_config.fold_is_active` reads the store, so
    the per-fire content-stop gate in both orchestrator files could never
    fire: it read `None` on every seat, including a seat that had just been
    folded. The store write is what makes the gate real, and it is also what
    makes the gate a RECEIPTED act rather than a silent render decision —
    the bridge item writes the state, the receipt names it, `undo` reverses
    it, and the gate simply reads it.

    Returns `{batch_id, tasks_disabled, logged, prev_overrides}`.
    FIX ROUND 3, REVIEW_FOLD1A S-3: `tasks_disabled` is the ids this call
    ACTUALLY folded (those not already `enabled: false` beforehand), never
    `planned["tasks_to_disable"]` verbatim — see the comment at its
    computation below for why the two can differ.

    FIX ROUND 4, REVIEW_FOLD1A T-2: on a seat with no live-scheduler
    readback, `plan()` still hands over BOTH ids unconditionally (it cannot
    tell which one is already off). When EVERY id in `tasks` already reads
    `enabled: False` in the workspace's own store — the customer paused both
    chats herself, or an earlier partial run already folded them — there is
    nothing left to write, and this function now says so WITHOUT calling
    `apply_fold_state`: no store write, no `schedule_config_changed` event.
    Before this fix it called `apply_fold_state` anyway, which re-wrote the
    (unchanged) store and appended a fresh audit event on every single
    `update command room` on that seat, forever — the narration already
    excluded these ids (`actually_folded` below), but the audit trail never
    did. A MIXED set (one id already off, the other still live) is
    unaffected: the live id genuinely needs folding, so it still goes
    through the write below exactly as before."""
    from schedule_config import apply_fold_state, load_schedule_config

    batch_id = batch_id or _mint_batch_id(now_iso)
    tasks = planned.get("tasks_to_disable") or []
    if not tasks:
        return {"batch_id": batch_id, "tasks_disabled": [], "logged": False,
                "prev_overrides": {}}
    try:
        _view = load_schedule_config(
            Path(workspace_root) / "_hq" / "data" / "entities.json")
    except Exception:
        _view = {}
    if all((_view.get(t) or {}).get("enabled") is False for t in tasks):
        return {"batch_id": batch_id, "tasks_disabled": [], "logged": False,
                "prev_overrides": {}}
    written = apply_fold_state(
        workspace_root, tasks, enabled=False, source_skill=SOURCE_SKILL,
        # ONE spelling of the reason, read from the module that also stamps it
        # on the store's own discriminator (fix round 2, R-3).
        extra_data={"reason": _sc_fold_reason(), "item_id": ITEM_ID,
                    "brain_batch_id": batch_id,
                    "brain_change_class": BRAIN_CHANGE_CLASS})
    # FIX ROUND 3 (REVIEW_FOLD1A S-3) — `tasks_disabled` reports what THIS
    # write actually folded, never the plan's request verbatim. `plan()`
    # called with no live scheduler readback (`task_records=None`) hands
    # BOTH ids to this function unconditionally, because it cannot tell
    # which one is already off — and a chat the CUSTOMER had paused
    # themselves, before this act ran, is untouched by it:
    # `schedule_config.apply_fold_state` stamps `FOLDED_BY_KEY` only when
    # the id was not already `enabled: false` (fix round 2, R-3), and its
    # `prev_overrides` return is the pre-write state that decision was made
    # from. An id whose `prev_overrides[id]` already read `enabled: false`
    # was not folded by this call, and must not be named as folded in the
    # summary line or the receipt `apply_plan` composes from this list —
    # the store write and the audit event still cover it (both harmless
    # no-ops on an already-off id), only the NARRATION changes.
    prev = written["prev_overrides"]
    actually_folded = [
        t for t in tasks
        if not (isinstance(prev.get(t), dict)
                and prev[t].get("enabled") is False)]
    return {"batch_id": batch_id, "tasks_disabled": actually_folded,
            "logged": written["logged"],
            "prev_overrides": prev}


def _write_receipt(workspace_root, context: dict, *, manifest_version=None) -> bool:
    """ONE `plugin_update_remediation` — the act's receipt AND the marker
    that makes it a one-shot. Written through the gated appender (CONTRACT
    Rule 31), never by hand."""
    from event_gate import append_event

    # NOTE: this receipt deliberately does NOT carry `brain_change_class`.
    # `brain_undo._changes_for_brain_batch` collects every event stamped
    # `brain_batch_id == <this batch>` that ALSO carries a `brain_change_class`
    # as one of the batch's reversible changes; the one real change is the
    # `schedule_config_changed` event `apply_plan` already wrote (via
    # `log_schedule_config_change`, which DOES carry the class). A receipt
    # that also carried the class would be double-counted as a second,
    # phantom "change" in the same batch — narration only, never a change.
    import schedule_config as _sc

    data = {"item_id": ITEM_ID, "action": "auto_apply",
            "outcome": RECEIPT_OUTCOME_APPLIED, "receipt_role": RECEIPT_ROLE,
            "source": ACTION_MODULE,
            "tasks_disabled": context.get("tasks_disabled"),
            "brain_batch_id": context.get("batch_id"),
            # FIX ROUND 1 (F-7) — built from the SAME count the customer's
            # own summary line reads, never a constant claiming "both chats"
            # on a seat where one of them was already paused by hand.
            "undo": _sc.folded_fire_undo_line(context.get("tasks_disabled"))}
    if manifest_version:
        data["manifest_version"] = manifest_version
    try:
        append_event(_events_path(workspace_root),
                     [{"type": RECEIPT_TYPE, "source_skill": SOURCE_SKILL,
                       "data": data}], holder=SOURCE_SKILL)
        return True
    except Exception as exc:  # noqa: BLE001 — loud, never fatal: the fold landed
        sys.stderr.write(f"[fold_scheduled_fires] receipt failed: {exc}\n")
        return False


def fold_scheduled_fires(events_jsonl_path, workspace_root,
                         detector_context) -> dict:
    """The `auto_apply` action contract (`release_actions/__init__.py`).

    `success=True ran=False` on every non-act (not a workspace, already
    folded, nothing live to fold) so the bridge surfaces NOTHING."""
    ctx: dict = {}
    try:
        dc = detector_context if isinstance(detector_context, dict) else {}
        planned = plan(workspace_root, task_records=dc.get("task_records"))
        ctx["status"] = planned.get("status")
        ctx["tasks_disabled"] = []
        if planned["status"] != "would_fold":
            return {"success": True, "ran": False, "context": ctx,
                    "error": None, "fallback_prompt": None}
        applied = apply_plan(workspace_root, planned, now_iso=dc.get("now_iso"))
        ctx["batch_id"] = applied["batch_id"]
        ctx["tasks_disabled"] = applied["tasks_disabled"]
        if not applied["tasks_disabled"]:
            return {"success": True, "ran": False, "context": ctx,
                    "error": None, "fallback_prompt": None}
        import schedule_config as _sc
        ctx["summary_line"] = _sc.folded_fire_summary(applied["tasks_disabled"])
        ctx["receipt_written"] = _write_receipt(
            workspace_root, ctx, manifest_version=dc.get("manifest_version"))
        return {"success": True, "ran": True, "context": ctx,
                "error": None, "fallback_prompt": None}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "ran": False, "context": ctx,
                "error": f"{type(exc).__name__}: {exc}",
                "fallback_prompt": MANIFEST_ITEM["fallback_prompt_template"]}


def main(argv=None) -> int:
    import argparse
    import json
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--now", default=None, help="ISO instant to plan against")
    ap.add_argument("--apply", action="store_true",
                    help="fold the planned chats (default: plan only)")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    planned = plan(ws)
    print(json.dumps(planned, indent=1))
    if not args.apply:
        print("MODE: DRY-RUN — nothing written. Re-run with --apply to fold.")
        return 0
    out = fold_scheduled_fires(_events_path(ws), ws,
                               {"now_iso": args.now} if args.now else {})
    print(json.dumps(out, indent=1))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
