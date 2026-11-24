#!/usr/bin/env python3
"""Per-step timings for the scheduled Command Room tasks (cr1#1602).

WHY. A scheduled task is a model run that calls helpers one at a time through
the door (`workspace_access run_helper` / `run_writer`). Until this, only the
Hub refresh had ever been timed (#1530), and a typed run once wrote timings
the model chose (#1276). So the timings here are measured by CODE: the door
times every call it makes (monotonic for the duration, the clock for the
start) and appends ONE row to a runtime cache file, and the receipt's writer
reads those rows and puts the run's phases on the receipt. The model passes
no timing anywhere.

THE STEP LOG is `_hq/.cache/task-steps.jsonl` (coordinator ruling on #1602,
fork A1): a cache beside the runtime cache the door already keeps, written by
the door only (never by a helper, so every helper stays write-free), pruned
here, and never read as a record. The door's own read and write verbs refuse
it (`workspace_access.fence_path`), so no row can be written there by hand. A
row is `{name, at, ms, ok}` plus `key` for the few steps whose one argument
says whose run it is (`KEY_ARGS`).

WHO WRITES THE FIELD. `receipts.log_receipt` (the Hub tasks' `pack_run`
through `cockpit_helpers.log_run_receipt`, and Maintenance's
`maintenance_run`) and `inbox_helpers._compose_fire_receipt` (the Hub inbox
run's receipt, inside `run_inbox_surface`). The Morning Brief's and the
merged Maintenance run's receipts are COMPUTED by code
(`morning_brief_helpers.plan_combined_receipt` /
`plan_maintenance_receipt` run `log_receipt` with its append held) and
CARRIED by the model, which lands the rows they return unchanged through one
`append_jsonl`; the model never composes them.

ATTRIBUTION (fork B1). Each timed task has a phase map (`TASKS`): which
helpers make each phase, the step that opens a run, the steps that close one
(its receipt), the step during which the receipt is written (when that is a
step of the run), and the steps that may lead before the opener. A run's
window starts after the LATER of the previous run's closing step and the
task's last receipt of any type in the book (a `surface_failed`, a skip, a
root_blocked receipt all end a run), and a gap longer than `RUN_CEILING_S`
between two steps starts it again (a run that died without its receipt does
not swallow the next one). The run starts at its first opener in that
window; the rows from there whose names are in the task's map are its steps.
Two tasks can overlap (an hourly Apply during a refresh), and a helper both
of them call (the page's people feed, the chat wall) cannot be told apart:
such a phase carries `shared: true` when another timed task's run overlaps
this one (it opened inside this window, or opened before it and had not
closed).

THE RECEIPT FIELD is `timings` (additive; nothing renamed or dropped):
`{measured, total_s, between_steps_s, phases: {phase: {s, calls[, shared]
[, failed_calls][, by_job]} | {not_run: true} | {writes_receipt: true}},
measured_by}`. `total_s` runs from the run's first step to the receipt;
`between_steps_s` is the time not inside a timed door call: the model's own
turns and waits between calls, and the door's `read`, `write` and
`append_jsonl`, which are not timed (only `run_helper` and `run_writer`
are). A phase with no step in the window is
`not_run`, never zero; the step that is writing the receipt is
`writes_receipt` (its own time cannot be on the receipt it writes). A run
whose steps the door never saw (a legacy shell that runs the scripts itself)
says `measured: false` and every phase `not_run`; so does a window longer
than `STEP_CAP_S` per step plus `RUN_CEILING_S` (`why: window_too_long`).
Steps after the receipt (the brief's send, the inbox feed) are not on it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: The step log, relative to the workspace root (a cache, never a record).
STEPS_REL = ("_hq", ".cache", "task-steps.jsonl")
#: The same, as the door's fence compares it.
STEPS_REL_POSIX = "/".join(STEPS_REL)
#: Pruning: a log over this size keeps only rows newer than KEEP_HOURS, at
#: most KEEP_ROWS of them.
MAX_BYTES = 512 * 1024
KEEP_HOURS = 48
KEEP_ROWS = 2000
#: The door's budget for one call (the Access preamble's "150 s budget"),
#: and the longest a run may spend between its steps: the Hub's longest
#: running timeout (the inbox leg's 30 minutes). A window over STEP_CAP_S per
#: step plus RUN_CEILING_S is not one run, and a gap of RUN_CEILING_S between
#: two steps starts a new one.
STEP_CAP_S = 150
RUN_CEILING_S = 30 * 60
#: Who measured: named on every receipt's `timings`.
MEASURED_BY = "workspace_access"

#: The one argument recorded with a step, for the steps whose run it names.
KEY_ARGS = {
    "cockpit_helpers:status_doc": "leg",
    "cockpit_helpers:log_run_receipt": "task_id",
    "maintenance_dispatcher:run_job": "job_id",
}
#: cr1#1679: a Hub light's state ("running", "done", "error") is kept on its
#: row, so only a light that STARTS a run can start a new window.
STATE_ARGS = {"cockpit_helpers:status_doc": "state"}
#: Steps whose key is a job, not a run: every key counts.
_ANY_KEY = frozenset({"maintenance_dispatcher:run_job"})

_CH = "cockpit_helpers:"
_WALL = "connector_reads:chat_wall"
_HUB_SETUP = (_CH + "load_config", _CH + "status_doc")


def _hub(leg: str, phases: List[tuple], **extra) -> Dict[str, Any]:
    """A Hub task: opened by its own light (`status_doc` with its leg),
    led by `load_config`, closed by its `log_run_receipt`."""
    spec = {"opener": (_CH + "status_doc", leg), "lead": (_CH + "load_config",),
            "closers": [(_CH + "log_run_receipt", "cockpit-" + leg)],
            "phases": [("setup", _HUB_SETUP)] + phases}
    spec.update(extra)
    return spec


#: The timed tasks: their phases in run order, from each orchestrator's steps.
#: `after_receipt` names the orchestrator's steps that run after the receipt
#: is written, so the reverse check (every step of the orchestrator is in the
#: map or here) has nothing unaccounted for.
TASKS: Dict[str, Dict[str, Any]] = {
    # cr1#1603: Prep's one call (`prep_all`, the leg and the Today feed) and
    # its parts (`refresh_part`) run before the shared last step's receipt.
    # Review round 2 of #1613: the leg's plan and call-prep's brief writes,
    # which Prep runs before its one call.
    "cockpit-prep": _hub("prep", [("plan", ("morning_brief_helpers:prep_leg_plan",
                                            "morning_brief_helpers:prep_leg_result")),
                                  # Review round 3 of #1613: call-prep, run end
                                  # to end; every door step it names.
                                  ("call_prep", ("brief_writer:make_brief_from_json", "morning_brief_helpers:call_prep_config",
                                               "morning_brief_helpers:prep_opener", "morning_brief_helpers:prep_constraints",
                                               "morning_brief_helpers:thread_payloads", "morning_brief_helpers:coaching_handoff",
                                               "prep_pipeline:resolve_prep_brief_path", "receipts:log_prep_receipt",
                                               "commitments_helpers:meeting_today_rows", "craft_recall:cite_rules",
                                               "craft_recall:craft_for_task", "correction_turn:handle_correction_turn",
                                               "exit_doors:plan_fact_closes", "deliverables:export_claude_doc",
                                               "skill_config_writer:reset_skill_config", "skill_config_writer:save_skill_config",
                                               "connector_reads:chat_wall", "context_loader:load_context",
                                               # cr1#1632 slice 1: call-prep reads what the last
                                               # meeting page with these people left.
                                               "hub_meeting:carried_points",
                                               # cr1#1632 slice 2: call-prep reads what is attached to the meeting.
                                               "meeting_attach:for_brief")),
                                  ("today", (_CH + "feed_today", _CH + "prep_all")),
                                  ("parts", (_CH + "refresh_part",))]),
    # The inbox run's ONE receipt is written inside `run_inbox_surface` (the
    # shared last step's receipt call is skipped), or inside the one call that
    # runs it (`inbox_all`, cr1#1603): that step closes the run.
    "cockpit-inbox": _hub("inbox", [
        ("wall", (_WALL,)),
        ("discovery", ("inbox_helpers:connector_discovery_step", "inbox_helpers:mail_seams")),
        ("constraints", ("draft_constraints:load_draft_constraints",)),
        ("context", ("context_loader:load_context",)),
        ("surface", ("inbox_helpers:run_inbox_surface", _CH + "inbox_all")),
    ], writers=("inbox_helpers:run_inbox_surface", _CH + "inbox_all"),
        closers=[("inbox_helpers:run_inbox_surface", None), (_CH + "inbox_all", None),
                 (_CH + "log_run_receipt", "cockpit-inbox")],
        # cr1#1603: the one call (`inbox_all`) runs the surface inside it; its
        # parts are pulled after the receipt it wrote.
        after_receipt=(_CH + "feed_inbox", "inbox_helpers:check_draft_dates", _CH + "refresh_part")),
    # cr1#1608: Apply is two calls, `apply_start` (claims, or writes the
    # receipt and stops when nothing waits) and `apply_run` (writes every
    # choice, and the receipt when it is done); `apply_finish` writes it on
    # any other end; the per-choice steps are the detail path's. The receipt
    # is written inside those calls, so the book's receipt ends the run.
    "cockpit-apply": _hub("apply", [
        ("wall", (_WALL,)),
        ("start", (_CH + "apply_start",)),
        ("run", (_CH + "apply_run", _CH + "apply_repin")),
        ("claim", (_CH + "plan_claim", _CH + "plan_apply")),
        ("commitments", (_CH + "apply_commitment_choices",)),
        ("mail_tools", ("inbox_helpers:connector_discovery_step", "inbox_helpers:mail_seams")),
        ("mail_checks", (_CH + "reply_reaches_everyone", _CH + "newer_message_arrived",
                         _CH + "sent_tokens_update")),
        ("hub", (_CH + "miss_search_plan", _CH + "apply_hub_choices")),
        ("feeds", (_CH + "feed_projects", _CH + "feed_board", _CH + "feed_people",
                   _CH + "feed_mine", _CH + "feed_review", _CH + "feed_settings",
                   _CH + "feed_mail_rules")),
        ("choices", (_CH + "apply_review_choices", _CH + "apply_week_choices",
                     _CH + "apply_settings_choices", _CH + "apply_mail_choices",
                     _CH + "apply_people_choices")),
        ("records", (_CH + "plan_page_records", _CH + "record_page_sends",
                     _CH + "record_calendar_changes")),
        ("finish", (_CH + "apply_finish",)),
    ], opener=(_CH + "apply_start", None),
        writers=(_CH + "apply_start", _CH + "apply_run", _CH + "apply_finish"),
        closers=[(_CH + "apply_finish", None), (_CH + "log_run_receipt", "cockpit-apply")]),
    "cockpit-refresh": _hub("refresh", [
        ("refresh", (_CH + "refresh_all", _CH + "refresh_part")),
        ("settle", (_CH + "settle_statuses", _CH + "plan_prune_choices")),
        ("feeds", (_CH + "feed_plate", _CH + "feed_projects", _CH + "feed_board", _CH + "feed_mine",
                   _CH + "feed_mail_rules", _CH + "feed_people", _CH + "feed_brief", _CH + "feed_review",
                   "friday_wrap_helpers:week_sections", _CH + "feed_week", _CH + "feed_settings",
                   _CH + "feed_stillyours", _CH + "maintenance_status")),
    ]),
    "morning-brief": {
        "opener": ("morning_brief_helpers:lateness", None), "lead": (),
        "closers": [("morning_brief_helpers:plan_combined_receipt", None)],
        "phases": [
            ("lateness", ("morning_brief_helpers:lateness", "morning_brief_helpers:record_fire_stopped")),
            ("config", ("morning_brief_helpers:brief_config", "skill_config_writer:save_skill_config")),
            ("wall", (_WALL,)),
            ("context", ("context_loader:load_context", "morning_brief_helpers:chat_context",
                         "inbox_helpers:connector_discovery_step", "inbox_helpers:mail_seams")),
            ("prep", ("morning_brief_helpers:prep_config", "morning_brief_helpers:prep_leg_plan",
                      "morning_brief_helpers:prep_leg_result")),
            ("brief", ("morning_brief_helpers:run_morning_brief_pack",)),
            # Review round 3 of #1613: morning-briefing Steps 1-4, run verbatim,
            # and call-prep for each meeting.
            ("brief_steps", ("morning_brief_helpers:brief_state", "morning_brief_helpers:morning_pack",
                             "morning_brief_helpers:open_commitments", "morning_brief_helpers:plate_lines",
                             "morning_brief_helpers:reminders", "morning_brief_helpers:confirm_lines",
                             "morning_brief_helpers:money_lines", "morning_brief_helpers:thread_payloads",
                             "commitment_wait:wait_from_chat", "commitments_helpers:meeting_today_rows")),
            ("call_prep", ("brief_writer:make_brief_from_json", "morning_brief_helpers:call_prep_config",
                           "morning_brief_helpers:prep_opener", "morning_brief_helpers:prep_constraints",
                           "morning_brief_helpers:coaching_handoff",
                           "prep_pipeline:resolve_prep_brief_path", "receipts:log_prep_receipt",
                           "craft_recall:cite_rules",
                           "craft_recall:craft_for_task", "correction_turn:handle_correction_turn",
                           "exit_doors:plan_fact_closes", "deliverables:export_claude_doc",
                           "skill_config_writer:reset_skill_config",
                           # cr1#1632 slice 1, as in cockpit-prep.
                           "hub_meeting:carried_points",
                           # cr1#1632 slice 2: call-prep reads what is attached to the meeting.
                           "meeting_attach:for_brief")),
            ("meeting_lines", ("morning_brief_helpers:prep_meeting_lines",)),
        ],
        "after_receipt": ("morning_message:plan_send", "morning_brief_helpers:post_text",
                          _CH + "hub_pointer", "morning_brief_helpers:plan_lane_asked",
                          "morning_message:hub_address", "morning_message:record")},
    "maintenance": {
        "opener": ("maintenance_dispatcher:catch_up_plan", None), "lead": (),
        "closers": [("morning_brief_helpers:plan_maintenance_receipt", None)],
        "phases": [
            ("plan", ("maintenance_dispatcher:catch_up_plan", "maintenance_dispatcher:mark_fire_start",
                      "maintenance_dispatcher:chats_state")),
            ("jobs", ("maintenance_dispatcher:run_job",)),
            ("scoring", ("maintenance_dispatcher:score_door_job",)),
            # Review round 2 of #1613: every door step the dispatcher names,
            # grouped by what it does.
            ("schedule", ("maintenance_dispatcher:plan_schedule_realign",
                          "schedule_backend:record_trigger_map",
                          "schedule_config:log_schedule_config_change",
                          "schedule_refresh:log_schedule_refreshed")),
            ("capture", ("eod_helpers:catchup_window", "eod_helpers:stage_fire_input",
                         "eod_helpers:dedup_meetings", "eod_helpers:decision_rows",
                         "eod_helpers:inbound_mail_staged", "eod_helpers:reconcile_chat_staged",
                         "eod_helpers:reconcile_sent_staged", "eod_helpers:run_shadow_lane",
                         "eod_helpers:clear_fire_staging", "eod_incremental:log_capture_pass_receipt",
                         "meeting_capture:route_meeting_captures", "inbound_mail_leg:plan_inbound_mail",
                         "chat_reconcile:plan_chat_scan", "reconcile_sent_commitments:plan_sent_window")),
            # Review round 3 of #1613: past-meetings Phases 3 to 4.8, run by the
            # capture step.
            ("past_meetings", ("eod_helpers:unprocessed_backlog", "eod_helpers:capture_fence",
                               "eod_helpers:run_cru_pass", "exit_doors:apply_own_word_closes",
                               "eod_helpers:prep_feedback", "eod_helpers:brief_path",
                               "brief_path:ensure_brief_directory", "brief_writer:make_brief_from_json",
                               "context_loader:load_context", "people_writer:door_auto_add_person",
                               "people_writer:door_update_person")),
            ("skill_jobs", ("skill_jobs_door:plan_cleanup", "skill_jobs_door:cleanup_and_receipt",
                            "skill_jobs_door:plan_weekly_insights", "skill_jobs_door:insights_and_receipt",
                            "skill_jobs_door:plan_monthly_report",
                            "skill_jobs_door:monthly_report_and_receipt",
                            "session_sweep:plan_sweep", "session_sweep:sweep_and_receipt",
                            "tasks_leg:plan_tasks_capture", "tasks_leg:capture_tasks_and_receipt",
                            "tasks_leg:plan_tasks_close", "tasks_leg:close_tasks_and_receipt",
                            "connector_proposals:plan_connector_proposals",
                            "connector_proposals:propose_from_connectors_and_receipt",
                            "cleanup_actions:sweep_lock_litter")),
        ]},
}

#: The receipt types that carry `timings` (the run's own receipt per task).
TIMED_RECEIPT_TYPES = frozenset({"pack_run", "maintenance_run"})


def steps_path(workspace_root) -> Path:
    return Path(str(workspace_root)).joinpath(*STEPS_REL)


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, _dt.datetime):
        return value.timestamp()   # a naive one is the machine's local clock
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def append_step(workspace_root, name: str, *, started: float, elapsed_ms: int, ok: bool,
                args: Any = None) -> None:
    """The DOOR's one write: one timing row for one call it made. `started`
    is the clock at the call's start (epoch seconds), `elapsed_ms` the
    monotonic duration. Never raises: a timing that cannot be kept is lost,
    and the call it timed is unaffected."""
    try:
        row: Dict[str, Any] = {"name": str(name)[:120], "at": _iso(float(started)),
                               "ms": max(0, int(elapsed_ms)), "ok": bool(ok)}
        key_arg = KEY_ARGS.get(str(name))
        if key_arg and isinstance(args, dict) and args.get(key_arg) is not None:
            row["key"] = str(args.get(key_arg))[:80]
        state_arg = STATE_ARGS.get(str(name))
        if state_arg and isinstance(args, dict) and args.get(state_arg) is not None:
            row["state"] = str(args.get(state_arg))[:20]
        path = steps_path(workspace_root)
        if not path.parent.parent.is_dir():   # no `_hq`: not a workspace, keep nothing
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        _prune(path)
    except Exception:  # noqa: BLE001 - a timing never breaks the call it timed
        return


def _prune(path: Path) -> None:
    """Keep the log small: past MAX_BYTES, only the rows of the last
    KEEP_HOURS, at most KEEP_ROWS of them (a cache, so a rewrite is fine)."""
    try:
        if path.stat().st_size <= MAX_BYTES:
            return
        cutoff = time.time() - KEEP_HOURS * 3600
        rows = [r for r in _read(path) if (_epoch(r.get("at")) or 0) >= cutoff][-KEEP_ROWS:]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                       encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError:
        return


def _read(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("name"), str) and _epoch(r.get("at")) is not None:
                    out.append(r)
    except OSError:
        return []
    return out


def read_steps(workspace_root) -> List[Dict[str, Any]]:
    """READ. The step log's rows, oldest first."""
    return _read(steps_path(workspace_root))


def _is(row: Dict[str, Any], step: tuple) -> bool:
    name, key = step
    return row.get("name") == name and (key is None or row.get("key") == key)


def _closes(spec: Dict[str, Any], row: Dict[str, Any]) -> bool:
    """cr1#1679: a closer that failed (it kept a row: a helper failure or a
    timeout) wrote no receipt, so it closes nothing; a retry closes the run."""
    return row.get("ok") is not False and any(_is(row, c) for c in spec.get("closers") or [])


def _starts(spec: Dict[str, Any], row: Dict[str, Any]) -> bool:
    """cr1#1679: the task's opener that STARTS a run: a light with no state
    on its row (an older log) or a running one, never a done or an error
    light written late in the same run."""
    return _is(row, spec["opener"]) and row.get("state") in (None, "running")


def _names(spec: Dict[str, Any]) -> set:
    return {n for _phase, names in spec["phases"] for n in names} | set(spec.get("lead") or ())


def _mine(spec: Dict[str, Any], row: Dict[str, Any]) -> bool:
    """Whether a row in the window is one of this task's steps: its name is in
    the map, and a step that names its run names this one."""
    name = row.get("name")
    if name not in _names(spec):
        return False
    if name not in KEY_ARGS or name in _ANY_KEY or row.get("key") is None:
        return True
    return row.get("key") in {spec["opener"][1]} | {c[1] for c in spec.get("closers") or []}


def _not_run(spec: Dict[str, Any], why: str) -> Dict[str, Any]:
    return {"measured": False, "why": why, "total_s": None, "between_steps_s": None,
            "phases": {phase: {"not_run": True} for phase, _ in spec["phases"]},
            "measured_by": MEASURED_BY}


def _last_receipt(workspace_root, task_id: str, now_s: float) -> Optional[float]:
    """The time of the task's newest receipt of any type in the book, before
    now; None when there is none or the book cannot be read."""
    if workspace_root is None:
        return None
    try:
        import receipts as rc

        best = None
        for r in rc.iter_receipts(workspace_root, task_ids=[task_id]):
            at = _epoch(r.get("dt"))
            if at is not None and at <= now_s and (best is None or at > best):
                best = at
        return best
    except Exception:  # noqa: BLE001 - no book, no cut
        return None


def _end(row: Dict[str, Any]) -> float:
    return (_epoch(row.get("at")) or 0.0) + int(row.get("ms") or 0) / 1000.0


def _shared_names(spec: Dict[str, Any]) -> set:
    """Every step another timed task names: on its map, as its opener or a
    closer, or after its receipt (review round 3 of #1613)."""
    out: set = set()
    for other in TASKS.values():
        if other is spec:
            continue
        out |= _names(other) | set(other.get("after_receipt") or ()) | {other["opener"][0]}
        out |= {c[0] for c in other.get("closers") or []}
    return out


def _keyed_own(row: Dict[str, Any]) -> bool:
    """A shared step that names its run (a Hub light's `leg`, a receipt's
    `task_id`): `_mine` has already matched its key to this task, so it is
    this task's own row."""
    name = row.get("name")
    return name in KEY_ARGS and name not in _ANY_KEY and row.get("key") is not None


def _window(steps: List[Dict[str, Any]], spec: Dict[str, Any], cut: Optional[float]) -> List[Dict[str, Any]]:
    """The rows after the previous run: after its last closing step and the
    task's last receipt, and after any gap longer than RUN_CEILING_S between
    two of the task's OWN steps.

    Review round 2 of #1613: the gap is judged on this task's own mapped
    rows (`_mine`, its shared lead steps left out), never on every row.
    Review round 3: a step another task also names (`_shared_names`: a feed
    Apply and Refresh both pull, a part, the chat wall, the mail tools) is
    not this task's own unless its key names this run. On
    the real day other tasks' rows sit between a run that died and the next
    one (a Hub refresh every 25 minutes), so a gap over all rows never fired
    and the next run swallowed the dead one, its time and the other task's
    lead step."""
    last_close = max((i for i, r in enumerate(steps) if _closes(spec, r)), default=-1)
    rows = steps[last_close + 1:]
    if cut is not None:
        rows = [r for r in rows if (_epoch(r.get("at")) or 0) > cut]
    lead = set(spec.get("lead") or ())
    shared = _shared_names(spec)
    own = [i for i, r in enumerate(rows) if _mine(spec, r) and r.get("name") not in lead
           and (r.get("name") not in shared or _keyed_own(r))]
    # Review round 4 of #1613 (N1): a gap starts a new run only when the row
    # after it is the task's opener. Prep's own rows are its light, `prep_all`
    # and `feed_today` (its plan, call-prep and parts steps are shared), so
    # four call-prep runs put more than 30 minutes between two of them inside
    # ONE run; cutting there lost the opener (`no_opening_step`), or measured
    # the fallback leg from its closing light.
    for k in range(len(own) - 1, 0, -1):
        if (_epoch(rows[own[k]].get("at")) or 0) - _end(rows[own[k - 1]]) > RUN_CEILING_S \
                and _starts(spec, rows[own[k]]):
            return rows[own[k - 1] + 1:]
    return rows


def _overlapping(task_id: str, steps: List[Dict[str, Any]], start_at: float) -> set:
    """The step names of every other timed task whose run overlaps this one:
    it opened at or after this run's start, or opened before it and had not
    closed by then."""
    names: set = set()
    for tid, other in TASKS.items():
        if tid == task_id:
            continue
        opened, opened_at = None, 0.0
        for r in steps:
            at = _epoch(r.get("at")) or 0
            if _is(r, other["opener"]):
                if at >= start_at:
                    opened = "inside"
                    break
                opened, opened_at = "before", at
            elif opened == "before" and _closes(other, r) and at < start_at:
                opened = None
        # A run is never open longer than RUN_CEILING_S (a light set after
        # the receipt does not keep it open for good).
        if opened == "inside" or (opened == "before" and start_at - opened_at <= RUN_CEILING_S):
            names |= _names(other)
    return names


#: Review round 4 of #1613 (N4): the step a writer child is running, named
#: by the write door in the child's environment (`workspace_access.run_writer`),
#: so the phase that holds it is the one writing the receipt.
RUNNING_STEP_ENV = "CR_TIMED_STEP"


def receipt_now(workspace_root, task_id: str, now: Any = None) -> float:
    """Review round 4 of #1613 (N2): the instant a receipt writer hands the
    reader as the run's end. A caller's instant (`now_iso`) is kept only
    between the end of the run's last row and the real clock: a typed time
    20 minutes ahead no longer sets the total. Never raises."""
    real = time.time()
    got = _epoch(now) if now is not None else None
    out = real if got is None else min(got, real)
    try:
        spec = TASKS.get(str(task_id))
        if spec is not None:
            lead = set(spec.get("lead") or ())
            ends = [_end(r) for r in read_steps(workspace_root)
                    if _mine(spec, r) or r.get("name") in lead or _is(r, spec["opener"])]
            last = max((e for e in ends if e <= real), default=None)
            if last is not None and out < last:
                out = last
    except Exception:  # noqa: BLE001 - the caller's instant, bounded above
        pass
    return out


def timings_for(workspace_root, task_id: str, *, now: Any = None,
                rows: Optional[Iterable[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """READ. The `timings` for the run of `task_id` that is writing its
    receipt now, from the step log; None for a task that is not timed.
    `now` is the receipt's own instant (epoch seconds, an ISO string or a
    datetime). Never raises."""
    spec = TASKS.get(str(task_id))
    if spec is None:
        return None
    try:
        steps = list(rows) if rows is not None else read_steps(workspace_root)
        now_s = _epoch(now) if now is not None else None
        now_s = time.time() if now_s is None else now_s
        window = _window(steps, spec, _last_receipt(workspace_root, str(task_id), now_s))
        start = next((i for i, r in enumerate(window) if _is(r, spec["opener"])), None)
        if start is None and spec.get("writers") and spec["opener"][0] in spec["writers"]:
            # The opener is the very call writing the receipt (Apply's
            # `apply_start` when nothing waits): its own row lands after, so
            # the run starts at its lead step.
            lead_names = set(spec.get("lead") or ())
            start = max((i for i, r in enumerate(window) if r.get("name") in lead_names), default=None)
        if start is None:
            return _not_run(spec, "no_opening_step")
        lead_names = set(spec.get("lead") or ())
        lead = [r for r in window[:start] if r.get("name") in lead_names][-1:]
        mine = lead + [r for r in window[start:] if _mine(spec, r)]
        t0 = _epoch((lead or [window[start]])[0]["at"]) or now_s
        others = _overlapping(str(task_id), steps, t0)
        phases: Dict[str, Any] = {}
        attributed = 0
        writers = set(spec.get("writers") or ())
        # Review round 3 of #1613: the step writing the receipt is the FIRST
        # writer phase with no row, in run order; a later writer phase (Apply's
        # `run` and `finish` when `apply_start` wrote it) never ran.
        receipt_marked = False
        # Review round 4 (N4): the writer child names its own step; the phase
        # holding it writes the receipt, whatever ran or failed before it. The
        # first-writer rule stays the fallback.
        running = os.environ.get(RUNNING_STEP_ENV) or ""
        if running in writers:
            for phase, pnames in spec["phases"]:
                if running in pnames and not [r for r in mine if r.get("name") in pnames]:
                    phases[phase] = {"writes_receipt": True}
                    receipt_marked = True
        for phase, pnames in spec["phases"]:
            if phase in phases:
                continue
            hits = [r for r in mine if r.get("name") in pnames]
            if not hits:
                if writers & set(pnames) and not receipt_marked:
                    phases[phase] = {"writes_receipt": True}
                    receipt_marked = True
                else:
                    phases[phase] = {"not_run": True}
                continue
            ms = sum(int(r.get("ms") or 0) for r in hits)
            attributed += ms
            entry: Dict[str, Any] = {"s": round(ms / 1000.0, 2), "calls": len(hits)}
            keyed = [r for r in hits if r.get("name") in _ANY_KEY and r.get("key")]
            if keyed:
                by: Dict[str, float] = {}
                for r in keyed:
                    by[r["key"]] = round(by.get(r["key"], 0.0) + int(r.get("ms") or 0) / 1000.0, 2)
                entry["by_job"] = by
            if any(r.get("ok") is False for r in hits):
                entry["failed_calls"] = sum(1 for r in hits if r.get("ok") is False)
            if set(pnames) & others:
                entry["shared"] = True
            phases[phase] = entry
        total = max(0.0, now_s - t0)
        if total > STEP_CAP_S * max(1, len(mine)) + RUN_CEILING_S:
            out = _not_run(spec, "window_too_long")
            out["phases"] = phases
            return out
        return {"measured": True, "total_s": round(total, 2),
                "between_steps_s": round(max(0.0, total - attributed / 1000.0), 2),
                "phases": phases, "measured_by": MEASURED_BY}
    except Exception:  # noqa: BLE001 - a receipt is never refused for its timings
        return _not_run(spec, "unreadable")
