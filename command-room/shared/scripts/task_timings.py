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
[, failed_calls][, failed][, by_job]} | {not_run: true} | {writes_receipt: true}},
measured_by, plugin_version[, gaps, largest_gaps, after_last_s[, gaps_dropped]]}`.
`plugin_version` (cr1#1808) is the version in the `plugin.json` shipped beside
the code that ran (the staged runtime's copy on a merged seat, the installed
plugin's on a local one), or `unknown`; never a guess. `gaps` is, for each
step of the run in order, `{name, before_s}`: the seconds from the previous
step's end to this one's start (the first step's is 0), helper names and
numbers only. `largest_gaps` names the `LARGEST_GAPS_NAMED` widest of them
(and the wait after the last step, as `after_last`, when it is among them),
so a reader sees where the time between steps went without the step file;
`after_last_s` is the time from the last step's end to the receipt. With
calls in a row the gaps and `after_last_s` add up to `between_steps_s`. With
two calls at once (parallel calls, or a shared helper another run made in the
same seconds) `between_steps_s` subtracts the overlap twice and reads short,
while the gaps measure the time no call was running: the gaps are then the
truer number. `total_s` runs from the run's first step to the receipt;
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
#: cr1#1764: the longest gap between a lead step and the next step of its
#: run (the opener, or the next lead step). A lead step further back is
#: another chat's, and the run's clock starts after it.
LEAD_GAP_S = 5 * 60
#: Who measured: named on every receipt's `timings`.
MEASURED_BY = "workspace_access"

#: The one argument recorded with a step, for the steps whose run it names.
KEY_ARGS = {
    "cockpit_helpers:status_doc": "leg",
    "cockpit_helpers:log_run_receipt": "task_id",
    "maintenance_dispatcher:run_job": "job_id",
    # cr1#1843: which input a stage landed (a word from the fire's closed
    # set, never content), so a refused stage's row says which source it was
    "eod_helpers:stage_fire_input": "kind",
}
#: cr1#1679: a Hub light's state ("running", "done", "error") is kept on its
#: row, so only a light that STARTS a run can start a new window.
STATE_ARGS = {"cockpit_helpers:status_doc": "state"}
#: cr1#1843: the stage of a fire's staged inputs, whose row names its fire
#: and kind as `<fire>:<kind>`, both from the closed sets below (review of
#: fa78b9be0: an unchecked argument never reaches `by_job`). The suite proves
#: they equal `eod_helpers.STAGE_DIRS` and `eod_helpers.FIRE_KINDS`.
STAGE_STEP = "eod_helpers:stage_fire_input"
STAGE_FIRES = frozenset({"end-of-day", "maintenance", "morning-brief", "run-maintenance"})
STAGE_KINDS = frozenset({"sent_messages", "chat_messages", "inbound_messages", "calendar_events",
                         "meeting_records", "decision_transcripts", "shadow_payloads",
                         # cr1#1838: the capture pass stages the calendar reads (train D)
                         "capture_calendar", "capture_calendar_personal"})


def stage_key(args: Any) -> Optional[str]:
    """The stage row's key, `<fire>:<kind>`, or None when either is not one
    of the closed words (the stage's own default fire is the End of Day)."""
    if not isinstance(args, dict):
        return None
    fire = str(args.get("fire") or "end-of-day")
    kind = str(args.get("kind") or "")
    return f"{fire}:{kind}" if fire in STAGE_FIRES and kind in STAGE_KINDS else None
#: Steps whose key is a job, not a run: every key counts.
_ANY_KEY = frozenset({"maintenance_dispatcher:run_job", "eod_helpers:stage_fire_input"})

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
    # cr1#1758: a run the page narrowed asks which "Prep this one" presses
    # stand (`prep_one_pick`) before the leg's plan.
    "cockpit-prep": _hub("prep", [("plan", ("cockpit_helpers:prep_one_pick",
                                            "morning_brief_helpers:prep_leg_plan",
                                            "morning_brief_helpers:prep_leg_result")),
                                  # Review round 3 of #1613: call-prep, run end
                                  # to end; every door step it names.
                                  ("call_prep", ("brief_writer:make_brief_from_json", "morning_brief_helpers:call_prep_config",
                                               "morning_brief_helpers:prep_opener", "morning_brief_helpers:prep_constraints",
                                               "morning_brief_helpers:thread_payloads", "morning_brief_helpers:coaching_handoff",
                                               "morning_brief_helpers:call_prep_context",  # cr1#1808: the nine reads in one
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
    # cr1#1722: the Update is the save and the rebuild as one run. It opens
    # with `update_claim` (its light is claimed on the page before that, in
    # code, not through the door) and closes with `update_finish`, which
    # writes its one receipt. The detail steps are the connector parts only.
    "cockpit-update": {
        # review round 1 of #1772: it leads with `load_config`. cr1#1837: the
        # off-limits list is built inside `update_build` now, not its own call
        "opener": (_CH + "update_claim", None), "lead": (_CH + "load_config",),
        "writers": (_CH + "update_finish",),
        "closers": [(_CH + "update_finish", None)],
        "phases": [
            # no `status_doc` here: the Update's light is claimed by `update-start`, in code
            ("setup", (_CH + "load_config",)),
            ("claim", (_CH + "update_claim",)),
            ("save", (_CH + "update_save", _CH + "apply_repin")),
            ("wall", (_WALL,)),
            ("detail_plan", (_CH + "plan_apply",)),
            ("mail_tools", ("inbox_helpers:connector_discovery_step", "inbox_helpers:mail_seams")),
            ("mail_checks", (_CH + "reply_reaches_everyone", _CH + "newer_message_arrived",
                             _CH + "sent_tokens_update")),
            ("hub", (_CH + "miss_search_plan", _CH + "apply_hub_choices")),
            ("build", (_CH + "update_build",)),
            ("parts", (_CH + "refresh_part",)),
            ("finish", (_CH + "update_finish",)),
            # The rest of `cockpit-apply-detail.md`: the Apply task's own calls
            # and its feed steps, which the Update never runs (it does that
            # file's step 1 and step 3 only). They are on the map so a run that
            # calls one by mistake is timed and seen; a right run says not_run.
            ("apply_only", (_CH + "apply_start", _CH + "apply_run", _CH + "apply_finish",
                            _CH + "plan_claim", _CH + "plan_page_records",
                            _CH + "apply_commitment_choices", _CH + "apply_review_choices",
                            _CH + "apply_week_choices", _CH + "apply_settings_choices",
                            _CH + "apply_mail_choices", _CH + "apply_people_choices",
                            _CH + "record_page_sends", _CH + "record_calendar_changes",
                            _CH + "feed_projects", _CH + "feed_board", _CH + "feed_people",
                            _CH + "feed_mine", _CH + "feed_review", _CH + "feed_settings",
                            _CH + "feed_mail_rules")),
        ]},
    "cockpit-refresh": _hub("refresh", [
        ("refresh", (_CH + "refresh_all", _CH + "refresh_part")),
        ("settle", (_CH + "settle_statuses", _CH + "plan_prune_choices")),
        ("feeds", (_CH + "feed_plate", _CH + "feed_projects", _CH + "feed_board", _CH + "feed_mine",
                   _CH + "feed_mail_rules", _CH + "feed_people", _CH + "feed_brief", _CH + "feed_review",
                   "friday_wrap_helpers:week_sections", _CH + "feed_week", _CH + "feed_settings",
                   _CH + "feed_stillyours", _CH + "feed_followups", _CH + "feed_pfollow",
                   _CH + "maintenance_status", _CH + "feed_urgent", _CH + "feed_focus", _CH + "feed_allopen", _CH + "feed_prepx")),
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
                           "morning_brief_helpers:call_prep_context",  # cr1#1808: the nine reads in one
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
                         "meeting_capture:route_meeting_captures", "followup_draft:record_draft",
                         "inbound_mail_leg:plan_inbound_mail",
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


#: cr1#1808 (D'): a failed call's code comes from a FIXED list, never from
#: the answer's shape (review of 5113e418e, should-fix 1). The door's own
#: refusal reasons (workspace_access's `R_*` words and the literal reasons its
#: refusals name; the suite proves every one is here), plus `answered_no`, the
#: door's word for a helper that answered ok false with no reason of its own.
DOOR_CODES = frozenset({
    "no_workspace_on_this_host", "folder_not_attached", "refused_path",
    "refused_runtime_cache", "refused_lock_file", "refused_append_only",
    "refused_protected", "not_registered_jsonl", "not_in_manifest",
    "not_allowed_helper", "not_allowed_writer", "writer_identity_required",
    "stale_read", "timeout", "missing_parent", "not_found", "invalid_json",
    "binary_unsupported", "helper_failed", "bad_args", "write_unverified",
    "writer_owned", "substrate_regressed", "refused_computer_link",
    "ledger_outside_data", "append_refused", "lock_timeout",
    "manifest_mismatch", "manifest_unreadable", "pointer_write_failed",
    "read_failed", "write_failed", "answered_no",
    # cr1#1843: a staged input's four refusals (`eod_helpers.
    # STAGE_REFUSAL_REASONS`) and the word its writer answers after one
    # (`eod_helpers.STAGE_REFUSED`); the suite proves both lists are here.
    "too_large", "no_content", "no_count", "count_mismatch", "not_landed", "stage_refused",
})
#: ...and the language's own exception type names (`KeyError`, `TimeoutError`),
#: a closed set fixed by Python, never by what a helper answered.
EXCEPTION_CODES = frozenset(n for n, v in vars(__import__("builtins")).items()
                            if isinstance(v, type) and issubclass(v, BaseException))
#: The word kept for a failure whose code is on neither list.
OTHER_CODE = "other"
#: How many failed calls one phase names on the receipt.
FAILED_NAMED_MAX = 10


def failure_code(value: Any) -> str:
    """A failed call's code as the receipt keeps it: a word from the fixed
    lists above (a door reason such as `helper_failed`, or an exception's type
    name, read from the text before a first colon), `other` for anything else
    that came back, or "" when nothing came back. Never a message, an argument,
    content or a helper's own word, however short."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    v = raw.split(":", 1)[0].strip()
    return v if (v in DOOR_CODES or v in EXCEPTION_CODES) else OTHER_CODE


def append_step(workspace_root, name: str, *, started: float, elapsed_ms: int, ok: bool,
                args: Any = None, code: str = "") -> None:
    """The DOOR's one write: one timing row for one call it made. `started`
    is the clock at the call's start (epoch seconds), `elapsed_ms` the
    monotonic duration. Never raises: a timing that cannot be kept is lost,
    and the call it timed is unaffected."""
    try:
        row: Dict[str, Any] = {"name": str(name)[:120], "at": _iso(float(started)),
                               "ms": max(0, int(elapsed_ms)), "ok": bool(ok)}
        # cr1#1808 (D'): a failed call says why, as one code token only
        if not ok and failure_code(code):
            row["code"] = failure_code(code)   # a listed word, or `other`
        key_arg = KEY_ARGS.get(str(name))
        if str(name) == STAGE_STEP:
            if stage_key(args):
                row["key"] = stage_key(args)   # cr1#1843: closed words only
        elif key_arg and isinstance(args, dict) and args.get(key_arg) is not None:
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


#: cr1#1808: how many of a run's gaps are named on the receipt's summary.
LARGEST_GAPS_NAMED = 3
#: ...and how many per-step gaps one receipt carries (a longer run says how
#: many were left off, `gaps_dropped`, so the receipt stays small).
GAPS_MAX = 120
#: The word for a version that cannot be read.
VERSION_UNKNOWN = "unknown"
#: The name the wait after the last step goes by among the largest gaps.
AFTER_LAST = "after_last"


def executed_version(plugin_root=None) -> str:
    """cr1#1808: the plugin version the run EXECUTED: the `plugin.json`
    shipped beside this module (the runtime ships it with the scripts, so on a
    merged seat this is the staged runtime's copy, which is what ran), read
    now, at the receipt. `unknown` when it cannot be read; never a guess, and
    never the version a chat handed in."""
    try:
        base = Path(plugin_root) if plugin_root else Path(__file__).resolve().parent.parent.parent
        payload = json.loads((base / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        version = payload.get("version") if isinstance(payload, dict) else None
        if isinstance(version, str) and version.strip():
            return version.strip()[:40]
    except Exception:  # noqa: BLE001 - no file, no version
        pass
    return VERSION_UNKNOWN


def _gaps(mine: List[Dict[str, Any]], now_s: float) -> Dict[str, Any]:
    """cr1#1808: the gap before each step of the run, and the widest named.
    Names and numbers only: a row's name, never its key, arguments or code."""
    out: List[Dict[str, Any]] = []
    prev_end: Optional[float] = None
    for r in sorted(mine, key=lambda row: _epoch(row.get("at")) or 0.0):
        start = _epoch(r.get("at"))
        if start is None:
            continue
        before = 0.0 if prev_end is None else max(0.0, start - prev_end)
        out.append({"name": str(r.get("name"))[:120], "before_s": round(before, 2)})
        prev_end = _end(r) if prev_end is None else max(prev_end, _end(r))
    after = round(max(0.0, now_s - prev_end), 2) if prev_end is not None else 0.0
    ranked = sorted([[g["before_s"], i, g["name"]] for i, g in enumerate(out)] + [[after, len(out), AFTER_LAST]],
                    key=lambda t: (-t[0], t[1]))
    largest = [{"name": n, "before_s": b, "step": i + 1} if n != AFTER_LAST else {"name": n, "before_s": b}
               for b, i, n in ranked[:LARGEST_GAPS_NAMED] if b > 0]
    field: Dict[str, Any] = {"gaps": out[:GAPS_MAX], "largest_gaps": largest, "after_last_s": after}
    if len(out) > GAPS_MAX:
        field["gaps_dropped"] = len(out) - GAPS_MAX
    return field


def _not_run(spec: Dict[str, Any], why: str) -> Dict[str, Any]:
    return {"measured": False, "why": why, "total_s": None, "between_steps_s": None,
            "phases": {phase: {"not_run": True} for phase, _ in spec["phases"]},
            "measured_by": MEASURED_BY, "plugin_version": executed_version()}


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
        # The run's lead: from the last first lead step (`load_config`) on,
        # every lead step before the opener (review round 1 of #1772: the
        # Update's wall step); a task with one lead step keeps its last one.
        lead_order = list(spec.get("lead") or ())
        prior = [r for r in window[:start] if r.get("name") in set(lead_order)]
        first_lead = max((i for i, r in enumerate(prior) if r.get("name") == lead_order[0]),
                         default=None) if lead_order else None
        lead = prior[first_lead:] if first_lead is not None else prior[-1:]
        # cr1#1764: a lead step is this run's only when it leads straight into
        # it: no gap longer than LEAD_GAP_S from its end to the next lead step
        # or the opener. A set-up chat's `load_config` a quarter of an hour
        # earlier started a run's clock there and doubled its total.
        nxt = _epoch(window[start].get("at")) or now_s
        chained: List[Dict[str, Any]] = []
        for r in reversed(lead):
            if nxt - _end(r) > LEAD_GAP_S:
                break
            chained.insert(0, r)
            nxt = _epoch(r.get("at")) or nxt
        lead = chained
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
                # cr1#1808 (D'): which helper failed and its code, names and
                # codes only, so the next receipt says where the retries go
                entry["failed"] = [{"name": str(r.get("name")), "code": failure_code(r.get("code")) or "unknown"}
                                   for r in hits if r.get("ok") is False][:FAILED_NAMED_MAX]
            if set(pnames) & others:
                entry["shared"] = True
            phases[phase] = entry
        total = max(0.0, now_s - t0)
        if total > STEP_CAP_S * max(1, len(mine)) + RUN_CEILING_S:
            out = _not_run(spec, "window_too_long")
            out["phases"] = phases
            return out
        out = {"measured": True, "total_s": round(total, 2),
               "between_steps_s": round(max(0.0, total - attributed / 1000.0), 2),
               "phases": phases, "measured_by": MEASURED_BY, "plugin_version": executed_version()}
        out.update(_gaps(mine, now_s))
        return out
    except Exception:  # noqa: BLE001 - a receipt is never refused for its timings
        return _not_run(spec, "unreadable")
