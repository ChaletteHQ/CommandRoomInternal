#!/usr/bin/env python3
"""The workspace access layer — one module, two faces (SPEC_NIGHTM1 §2, ACCESS1).

WHY THIS EXISTS
---------------
In the merged Claude app the helpers and the data are on different machines.
`shared/scripts/*.py` is synced into a cloud container; the customer's workspace
is FUSE-mounted only inside a sandbox VM on their own PC, reached one shell call
at a time through the device bridge. Nothing bridges the two: the container
cannot open a workspace file, and the VM has no plugin files. Every line of the
old contract ("cd $PLUGIN_ROOT && python3 -c …", with the workspace found under
the same $SESSION_DIR/mnt) assumed one filesystem, and there is no longer one.

THE SHAPE (gap analysis §4, strategy 2, ruled §0.8). A version-stamped,
content-hashed copy of the runtime is installed INTO the workspace at
`_hq/.cache/cr-runtime/<version>/`, and every substrate verb runs in the VM, in
place, against the live files — so the append path keeps the real writer lock,
the real seq allocation and the real read-back, byte for byte. This module is
the only thing SKILL.md prose is allowed to invoke, and it has two faces:

  * HOST face — runs where the data is (the VM under the installed runtime, or
    an ordinary local machine, or a legacy Cowork sandbox). `read`,
    `run_helper`, `write`, `append_jsonl`. One call = one verb = one JSON
    envelope line on stdout.
  * CONTAINER face — runs in the cloud container from the plugin checkout and
    NEVER opens a workspace file. `discover` (the standalone block the model
    pastes into the device shell), `expect`, `install --out`, `verify`,
    `plan <verb>` (the exact command string to paste). In `container` mode
    every data verb REFUSES and says where to run it (§0.25) — a silent
    stage-and-compute fallback is the improvised-path class Rule 22 ended.

MODE VOCABULARY (coordinator decision D-1). `env_detect.MODES` names WHERE THIS
PROCESS RUNS; `host_mode` here names WHERE THE DATA IS relative to this process.
Neither module renames the other's enum; the mapping lives in `MODE_FROM_ENV`
and `detect_host_mode` below and is pinned by `tests/run_access1_test.py`.

WHAT THIS MODULE MUST NEVER DO. Stage customer data into the container; guess a
workspace when it cannot resolve one; write plugin code anywhere but the
content-hashed runtime cache; set `CR_DATA_ROOT`; or unlink anything on a hot
path (`os.unlink` is EPERM on the mount — `build/P1_RESULT_2026-09-19.md`).

3.10-safe by contract: the sandbox VM is Python 3.10.12 and this file is the
first thing that runs there. Stdlib only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants — the vocabulary every other lane builds against
# ---------------------------------------------------------------------------

VM = "vm"
LOCAL = "local"
COWORK_LEGACY = "cowork_legacy"
CONTAINER = "container"
HOST_MODES = (VM, LOCAL, COWORK_LEGACY, CONTAINER)

# D-1: env_detect's vocabulary -> this module's. `merged_cloud` is the one that
# needs a fact rather than a lookup (is THIS file running from the installed
# runtime inside a workspace?), so it is resolved in `detect_host_mode`.
MODE_FROM_ENV = {
    "legacy_cowork": COWORK_LEGACY,
    "claude_code_local": LOCAL,
    "unknown": CONTAINER,
}

# The runtime cache — plugin-owned code inside the customer's workspace, the one
# write a plugin update makes under the root (PLUGIN_BOUNDARY invariant 4, as
# amended by this lane). Never data; never readable through the data verbs.
RUNTIME_CACHE_REL = "_hq/.cache/cr-runtime"
CURRENT_POINTER = "current.json"   # a pointer FILE, never a symlink (FUSE/Drive)
MANIFEST_NAME = "manifest.json"

# The substrate anchor, spelled exactly as `workspace_root.py` spells it.
ANCHOR_REL = "_hq/data/entities.json"
PRUNE_DIRS = ("_archive", "_demo-framework")
MAX_SEARCH_DEPTH = 5

# The writer lock's content must never change (writer_lock.py:32-39).
LOCK_REL = "_hq/data/.writer.lock"

# Registered JSONL files — the append-only set `atomic_write.py:776-777` names.
# `events.jsonl` and its year shards route through the event gate; the rest are
# ordinary registered appends. Anything else is refused by name.
REGISTERED_JSONL = frozenset({
    "events.jsonl",
    "staging_emissions.jsonl",
    "classifier_feedback.jsonl",
    ".backfill_cursor",
})
_EVENTS_SHARD_RE = re.compile(r"^events-\d{4}\.jsonl$")

# The two files whose canonical writer takes the sentinel lock and stamps `.rev`
# (`atomic_write.atomic_write_json_locked`).
REV_FILES = ("entities.json", "aliases.json")

# The layer reports its own timeout at 150 s so a 180 s device_bash cap never
# truncates an envelope silently.
DEFAULT_TIMEOUT_S = 150

# The helpers `run_helper` may call, spelled `module:function`.
#
# The manifest alone is NOT an allow-list: `atomic_write` is in the manifest by
# construction, so a module-level gate let `run_helper` write any file anywhere
# — outside the root, into this cache, and over the ledger — going round the
# three fences `write` and `append_jsonl` enforce (review finding B-2; the
# coordinator ruled it a BUILD bug and tightened the spec, SPEC §2 item 2, to
# require this registry). So the gate is a named FUNCTION list: read/compute
# helpers, plus the gated writers that stamp, dedup and sidecar on the way in.
#
# The rule for adding one: it READS or COMPUTES and writes nothing. No writer
# belongs here at all, gated or not (review finding B-1): `event_gate:
# append_event` was allowed as "the sanctioned writer", and because the helper
# door applies only the path fences, a fire could append a JSON line onto
# `entities.json` or `aliases.json` — leaving the substrate's anchor file
# invalid JSON — and create unregistered `.jsonl` files, all of which the
# layer's own `append_jsonl` door refuses by the `REGISTERED_JSONL` rule.
# `write` and `append_jsonl` ARE the doors; `run_helper` never writes. A raw
# writer (`atomic_write:*`) and a process/filesystem verb (`os`, `shutil`,
# `pathlib`, `subprocess`) never belonged here either.
RUN_HELPER_ALLOWLIST = frozenset({
    # reads and computes
    "data_root:resolve",
    "entity_resolve:resolve",
    "entity_resolve:resolve_all",
    "events_io:count_rows",
    "events_io:load_all",
    "events_io:shard_invariants",
    "events_io:shard_paths",
    # the inbox chain (ORCH1). Every one reads or computes; the rows they
    # compose are appended by the CALLER through `plan append_jsonl`, which is
    # what keeps this door a read door.
    "inbox_helpers:apply_priority_rules",
    "inbox_helpers:billing_door",
    "inbox_helpers:check_draft_dates",
    "inbox_helpers:filter_by_preferences",
    "inbox_helpers:gate_reply",
    "inbox_helpers:header_date",
    "inbox_helpers:lateness",
    "inbox_helpers:mail_seams",
    "inbox_helpers:pack_run_telemetry",
    "inbox_helpers:plan_fire_receipt",
    "inbox_helpers:plan_inbound_capture",
    "inbox_helpers:plan_inbound_reconcile",
    "inbox_helpers:priority_rules",
    "inbox_helpers:render_inbox_page",
    "inbox_helpers:renderer_preflight",
    "inbox_helpers:setup",
    "inbox_helpers:surface_preferences",
    "inbox_helpers:triage_config",
    # HEAL1 (Night M2) — the maintenance catch-up's read half. A merged seat
    # asks these three questions through this door and executes the answer's
    # job legs the same way; the one receipt goes through `append_jsonl`,
    # never through here.
    #
    # `catch_up_plan` is the surface's form of the due-jobs question and is
    # the one that is listed, NOT `due_jobs` itself. The spec's sentence
    # names `due_jobs`; the difference is B-1, and it is deliberate. Both
    # reach `dispatch_plan`, whose root-validation leg REPAIRS — an
    # idempotent fingerprint backfill on a healthy root, a repoint plus a
    # receipt on a dead one — and whose `scheduled` path stamps a fire-start
    # marker. `catch_up_plan` takes neither branch by construction (it plans
    # read-only and always as a manual fire), while `due_jobs` takes both
    # whenever the caller asks for them, and on this list the caller is
    # whatever the model pasted. A helper here reads or computes; a gated
    # writer is exactly what made this verb a write door once already.
    "maintenance_dispatcher:catch_up_plan",
    "maintenance_dispatcher:maintenance_staleness",
    "surface_drivers:maintenance_truth_line",
    # TZ1 (Night M2) — the schedule registry, read where the folder is.
    "schedule_backend:read_trigger_map",
    # FIX3 (F3-2) — the two questions a scheduled fire asks before it does
    # anything. They used to be three `python3 -c` bodies pasted into the
    # bootloader's own prose, which is the class guard G69 exists to stop.
    # Both read and compute: `root_guard` is PATHREPAIR1's verdict with the
    # apply leg removed (a fire never repairs — R-HEAL1-4), and `retirement`
    # asks the registry two questions and renders one sentence.
    # FIX3 (F3-10) — the connector-read row, COMPOSED here and appended
    # by the caller through `append_jsonl`. The writer half
    # (`chat_context:log_connector_read`) is not on this list and must
    # never be: a read door writes nothing.
    "chat_context:connector_read_row",
    "fire_guards:retirement",
    "fire_guards:root_guard",
    # IDENT1 I-12 - the text surface for a run with no widget tool, the
    # delivery plan it follows, and the typed `draft N` reader. All three
    # read or compose; the landing is `write`, the rows are `append_jsonl`.
    "inbox_helpers:render_inbox_text",
    "inbox_helpers:plan_delivery",
    "inbox_helpers:typed_reply_action",
    # IDENT1 I-14 - the connector discovery step, rendered from the
    # declaration (a read of the workspace's own connector labels).
    "inbox_helpers:connector_discovery_step",
    # IDENT1 I-15 - is this chain migrated? Reads the PLUGIN's own census,
    # never the workspace; a stop is one sentence and zero writes.
    "migration_gate:gate",
    # IDENT1 I-2 (R-RW2-3, first half). The prep's ONE path question. It was
    # refused here on 2026-09-22, so the chat imported the writers into a
    # shell to get the answer and wrote the receipt with a session token.
    # A read: it names the file a brief for this occurrence lives in.
    "prep_pipeline:resolve_prep_brief_path",
    # RETIRE1 (Night M3, SAFETY0) — the third fire question: is this fire the
    # workspace's declared scheduled writer? It reads the declaration and the
    # process environment and writes nothing; `foreign: true` makes its
    # `line` the whole turn.
    "fire_guards:scheduled_writer",
    # SCHEDDISCOVER1 (night M3, review M-2) — the scheduler discovery step's
    # ONE rendered call: the opening line a scheduling skill says (where the
    # chats run, or why there is no scheduler). Computes one sentence from its
    # arguments; reads nothing and writes nothing.
    "schedule_backend:opening_lines",
    # ORCH2C (Night M3 lane 6) — the Waiting On chain
    # (`orchestrator-commitments.md`). Every one reads or computes: the CRU
    # passes are PLANNED here (the closes they would make come back as
    # `writes` for `run_writer commitments_helpers:apply_cru_writes`, the
    # review proposals as rows for `append_jsonl`), the receipt is COMPOSED,
    # and a hand-shaped page is rendered and handed back for `write`. The
    # chain's two writers are on the writer list, never here.
    # `chase_candidates` (fix pass 1, REVIEW_ORCH2C HIGH-2) is Phases 3,
    # 3.6, 3.8, 4.5 and 5 - the chase set built where the ledger is.
    "commitments_helpers:chase_candidates",
    "commitments_helpers:cru_window",
    "commitments_helpers:filter_chase_rows",
    "commitments_helpers:fold_gate",
    "commitments_helpers:lateness",
    "commitments_helpers:meeting_today_rows",
    "commitments_helpers:plan_calendar_cru",
    "commitments_helpers:plan_commitments_receipt",
    "commitments_helpers:plan_sent_cru",
    "commitments_helpers:render_waiting_on_page",
    "commitments_helpers:render_waiting_on_text",
    "commitments_helpers:renderer_preflight",
    "commitments_helpers:resolve_people",
    "commitments_helpers:setup",
    "commitments_helpers:validate_inbound_ran",
    # ORCH2 (Night M3) — the morning brief. Every one reads or computes; a
    # row its delegated writer would append (the clock record, a served-slot
    # skip, the chat touches, `brief_state`, the combined `pack_run`) comes
    # back in `pending_rows`/`rows` for the caller's `plan append_jsonl`.
    # Writers (the first-fire settings save) go through `run_writer`.
    "morning_brief_helpers:brief_config",
    "morning_brief_helpers:brief_state",
    "morning_brief_helpers:chat_context",
    "morning_brief_helpers:lateness",
    "morning_brief_helpers:open_commitments",
    "morning_brief_helpers:plan_combined_receipt",
    "morning_brief_helpers:plan_maintenance_receipt",
    "morning_brief_helpers:prep_config",
    "morning_brief_helpers:prep_leg_plan",
    "morning_brief_helpers:prep_leg_result",
    "morning_brief_helpers:prep_meeting_lines",
    "morning_brief_helpers:render_for_fire",
    "morning_brief_helpers:settings_for_fire",
    "morning_brief_helpers:thread_payloads",
    # ORCH2 — what's on my plate (show-my-list's fossil list and mute
    # ledger, the My Plate chat's lateness check). Same rule: reads and
    # computes; the page comes back for `plan write`, rows for
    # `plan append_jsonl`.
    "plate_helpers:discuss_list",
    # ORCH2 fix pass 1 — the My Plate chat's Step 0 fold gate and its
    # degrade-tier receipt compose, both beside the data.
    "plate_helpers:fold_gate",
    "plate_helpers:lateness",
    "plate_helpers:live_mutes",
    "plate_helpers:plan_list_marker",
    "plate_helpers:plan_plate_receipt",
    "plate_helpers:render_list_page",
    # ORCH2 — the staff meeting and the health check (system-health).
    # The substrate alarm lines are NOT here: they sweep resolved alerts
    # as they run, so they go through `run_writer`.
    "staff_meeting_helpers:health_report",
    "staff_meeting_helpers:health_substrate",
    "staff_meeting_helpers:lateness",
    "staff_meeting_helpers:plan_staff_receipt",
    "staff_meeting_helpers:self_report",
    "staff_meeting_helpers:staff_window",
    # ORCH2 fix pass 1 — the staff meeting's unnamed-speaker count line
    "staff_meeting_helpers:unnamed_speakers",
    # ORCH2 — End of Day (orchestrator-past-meetings.md, end-of-day). The
    # module's two WRITERS (`apply_cru_pass`, `run_shadow_lane`) are on the
    # write list and never here (G74).
    "eod_helpers:brief_path",
    "eod_helpers:capture_fence",
    "eod_helpers:catchup_window",
    "eod_helpers:cru_walk",
    "eod_helpers:decision_rows",
    "eod_helpers:dedup_meetings",
    "eod_helpers:lateness",
    "eod_helpers:meeting_write_counts",
    "eod_helpers:plan_eod_receipt",
    "eod_helpers:prep_feedback",
    "eod_helpers:render_eod_page",
    "eod_helpers:render_for_fire",
    "eod_helpers:render_set",
    "eod_helpers:renderer_preflight",
    "eod_helpers:unprocessed_backlog",
    "reconcile_sent_commitments:plan_sent_window",  # MAINTJOBS1
    "chat_reconcile:plan_chat_scan",  # MAINTJOBS1
    "session_sweep:plan_sweep",  # MAINTJOBS1
    "maintenance_dispatcher:plan_schedule_realign",  # MAINTJOBS1
    "schedule_backend:bridge_plan",  # v5.33.0 merge-fix MF-3 (D-8): the bridge refreshes mapped chats only; a read that plans
    "schedule_backend:bridge_schedules_line",  # v5.33.0 merge-fix MF-3/MF-4 (D-5): the bridge's schedules sentence through the one producer
    # KEEP THIS NAME LAST, directly above the closing brace.
    # `run_access1_test`'s B-1 removal proof anchors on the exact two-line
    # string `"workspace_root:find_workspace_root",` + `})` and puts the
    # gated writer back in that slot. An addition appended after this line
    # breaks that anchor and the proof reports "anchor not found" instead of
    # going red for a real reason. New entries go ABOVE.
    "morning_brief_helpers:confirm_lines",  # BRIEFDOOR1 - Step 3g's confirm pointer
    "morning_brief_helpers:money_lines",  # BRIEFDOOR1 - Step 3h's money carve-out
    "morning_brief_helpers:reminders",  # BRIEFDOOR1 - Step 3f's own pins
    "morning_brief_helpers:post_text",  # BRIEFDOOR1 - the chat copy's document links
    "bridge_versions:install_versions",  # HYGIENE3 - the bridge's version triple, workspace half (D-3)
    "schedule_backend:plan_registration",  # BRIDGE2 - registration planned beside the data: the seat digest vs the declaration (R-WALK-3)
    "schedule_config:scheduled_writer",  # BRIDGE2 - the declaration read where the folder is, so a refusal can name the computer
    "migration_adjudication:adjudication_status",  # BRIDGE2 - the bridge's Phase 1 adjudication gate, read beside the data (R-WALK-5)
    "maintenance_dispatcher:chats_state",  # CHATSON1 (#98) - M1b asks with the scheduler's rows for EVERY chat on the roster; switch-on only for the ones that read off
    "maintenance_dispatcher:own_chat_state",  # FIRE3B - M1b asks with the scheduler's rows; switch-on only when this chat reads off (F-T2-13)
    "eod_helpers:resolve_choice",  # MIGRATE3-EOD - the evening answer resolver, read beside the data (F-T2-15)
    "friday_wrap_helpers:lateness",  # MIGRATE3-FW - the wrap's Phase 2.9 verdict, its rows held
    "friday_wrap_helpers:plan_wrap_receipt",  # MIGRATE3-FW - the wrap's one pack_run, composed not written
    "friday_wrap_helpers:wrap_post",  # MIGRATE3-FW - the post fences beside the data, a refusal answered
    "friday_wrap_helpers:recap_config",  # MIGRATE3-FW - weekly-recap's first-run settings read
    "friday_wrap_helpers:catchup_window",  # MIGRATE3-FW - the wrap's since-last-run window
    "friday_wrap_helpers:group_section",  # MIGRATE3-FW - section 8's heading from the reader's settings
    "friday_wrap_helpers:week_sections",  # MIGRATE3-FW - sections 8b, 8b-bis and 8c read beside the data
    "friday_wrap_helpers:window_events",  # MIGRATE3-FW - the window's org-scoped rows (PGUARD1)
    "friday_wrap_helpers:brief_path",  # MIGRATE3-FW - the recap document's path
    "friday_wrap_helpers:closing_lines",  # MIGRATE3-FW - 5.C's footer and heading link, composed not printed
    "friday_wrap_helpers:plan_commitment_captures",  # MIGRATE3-FW - Phase 3's commitments, gated and composed (no scan skill)
    "friday_wrap_helpers:week_facts",  # MIGRATE3-FW - the primary user and the commitment counts, read beside the data
    "friday_wrap_helpers:plan_visual_gate",  # MIGRATE3-FW - 5.B's visual-pass audit row, composed not written
    "morning_brief_helpers:call_prep_config",  # MIGRATE3-MB - call-prep's first-run knobs, read beside the data
    "morning_brief_helpers:prep_constraints",  # MIGRATE3-MB - call-prep's learned constraints (and the draft through them)
    "morning_brief_helpers:coaching_handoff",  # MIGRATE3-MB - call-prep's coaching handoff question
    "morning_brief_helpers:prep_opener",  # MIGRATE3-MB - the landed prep's cloud-aware opener
    "exit_doors:plan_fact_closes",  # MIGRATE3-MB - D-T2B-1: which Owed rows the record shows finished; a read, never a close
    "maintenance_dispatcher:score_door_job",  # FIRE3 - the merged fire scores each script/door job beside the data (MAINTJOBS1 S-4)
    # PEOPLEWRITE2
    "people_writer:door_find_person",  # PEOPLEWRITE2 - people-crm's dedup read before a person write; an ambiguous name answered, never raised (D-W2-5)
    "workspace_root:find_workspace_root",
})

# The writers `run_writer` may call, spelled `module:function` (IDENT1 I-0,
# ruling R-M3-2 / coordinator decision D-2).
#
# `run_helper` is a READ door and stays one: its seven-frame write-scan is
# the pin that keeps it honest. But a document, a receipt, a close and a
# re-pin are WRITES, and until this door existed the layer had no way to run
# one beside the data - so the 2026-09-22 prep chat imported `brief_writer`
# and `receipts` straight into a shell, and the receipt it wrote carried a
# session token instead of the customer's writer id. This is the door those
# calls go through instead: its own list (no name is on both - guard G74),
# refused where there is no workspace (`container`), path-fenced like
# `write`, arguments bound by NAME, and an identity PRECONDITION
# (`receipts.require_writer_identity`) asked before anything runs. Every
# name here has a reason in `shared/WORKSPACE_ACCESS.md`'s writer table.
RUN_WRITER_ALLOWLIST = frozenset({
    # the prep (I-2): the document, its receipt, and the fact closes the
    # prep runs before its Owed table (which include the calendar leg)
    "brief_writer:make_brief_from_json",
    "receipts:log_prep_receipt",
    "exit_doors:apply_fact_closes",
    "calendar_close:run_calendar_close_job",
    # the connector declaration and the update bridge's re-pin (I-7)
    "connector_config:set_declared_backend",
    "release_actions.connector_display_name_repin_v1:connector_display_name_repin_v1",
    # the fire-delivery setting (I-12 (8)): `publish my scheduled pages`
    "fire_delivery:set_artifact_publish",
    "fire_delivery:record_artifact_url",
    # SAFETY0 (RETIRE1's R0; spec §7 seam S-10): declaring THIS workspace's
    # one scheduled writer is a whole-file entities.json write plus one
    # `scheduled_writer_declared` row, made on the registering seat.
    "schedule_config:declare_scheduled_writer",
    # ORCH2C (Night M3 lane 6) - the Waiting On chain
    # (`orchestrator-commitments.md`). The closes its CRU passes plan, the
    # inbound pass end to end, the stamped auto-merges, the first-fire
    # config save, and the one-command driver (its view mints the plate's
    # display numbers, and its render and receipt are one call, FB-7).
    "commitments_helpers:apply_cru_writes",
    "commitments_helpers:run_waiting_on_surface",
    "reconcile_inbound_commitments:reconcile_inbound_and_receipt",
    "commitment_dedup:apply_auto_merges",
    "skill_config_writer:save_skill_config",
    # ORCH2 (Night M3) — the daily surfaces' and the maintenance fire's
    # writers. Each is a write that cannot be expressed as ledger rows: a
    # job that writes through its own writers, an alert sweep. (ORCH2's
    # settings-file writer is `skill_config_writer:save_skill_config`, listed
    # ONCE above under ORCH2C - MF-23; G74 [6] polices duplicates.)
    "maintenance_dispatcher:run_job",
    "substrate_health:substrate_alarm_lines",
    # ORCH2 — End of Day's writes that are not ledger rows
    "brief_path:ensure_brief_directory",
    "eod_helpers:apply_cru_pass",
    "eod_helpers:run_shadow_lane",
    "exit_doors:apply_own_word_closes",
    "meeting_capture:route_meeting_captures",
    # ORCH2 — My Plate's one-command driver: its view mints the plate's
    # display numbers under the writer lock and lands the receipt (FB-7).
    "plate_helpers:run_my_plate_surface",
    # ORCH2 fix pass 1 — the Staff Meeting's one-command driver, the same
    # shape: it runs the watch-expiry pass, freezes the page-set, lands the
    # page and writes the fire's one receipt (FB-7).
    "staff_meeting_helpers:run_staff_meeting_surface",
    "schedule_backend:record_trigger_row",  # SCHEDREG1 - registration's trigger row, landed where the folder is
    "schedule_backend:record_registration",  # SCHEDFAST1 (#101) - the trigger row AND the schedule_created receipt, one door call per created chat
    "schedule_config:write_schedule_skipped",  # SCHEDREG1 - the one refusal row a seat that cannot register writes
    "inbox_helpers:run_inbox_surface",  # INBOXDRIVE1
    "maintenance_dispatcher:mark_fire_start",  # MAINTJOBS1
    "reconcile_sent_commitments:reconcile_and_receipt",  # MAINTJOBS1
    "chat_reconcile:reconcile_chat_and_receipt",  # MAINTJOBS1
    "eod_incremental:log_capture_pass_receipt",  # MAINTJOBS1
    "session_sweep:sweep_and_receipt",  # MAINTJOBS1
    "cleanup_actions:sweep_lock_litter",  # MAINTJOBS1
    "schedule_refresh:log_schedule_refreshed",  # MAINTJOBS1
    "schedule_backend:record_trigger_map",  # MAINTJOBS1
    "release_actions.claude_md_docs_rule_v1:claude_md_docs_rule_v1",  # DOCS lane (DOCS1 D-1): the rule into CLAUDE.md, one applied row
    "deliverables:export_claude_doc",  # DOCS lane (DOCS1 D-2): a Claude Doc landed in the folder + its receipt row
    # KEEP THIS NAME LAST, directly above the closing brace - the same anchor
    # discipline as `RUN_HELPER_ALLOWLIST`: removal proofs and later lanes
    # (ORCH2 adds entries) anchor on the two-line string
    # `"deliverables:land",` + `})`. New entries go ABOVE.
    "morning_brief_helpers:morning_pack",  # BRIEFDOOR1 - the brief's pack (mints plate numbers)
    "morning_brief_helpers:plate_lines",  # BRIEFDOOR1 - the plate's brief cut (mints plate numbers)
    "morning_brief_helpers:plan_lane_asked",  # BRIEFDOOR1 - Phase 6.1's mark (takes the ledger lock)
    "path_repair:fire_time_guard",  # HYGIENE3 - the bridge's Step 0 root guard (it repairs; D-3)
    "recover_corruption:run_recovery_if_needed",  # HYGIENE3 - the bridge's Phase 4.4 self-heal
    "thread_writer:repair_dual_project_key",  # HYGIENE3 - the bridge's Phase 4.4b dual-key repair
    "eod_helpers:run_end_of_day_pack",  # MIGRATE3-EOD - Phase C, the pack builder the old CLI wrapped (F-T2-15)
    "eod_helpers:answer_eod_questions",  # MIGRATE3-EOD - the evening answers through the queue writers
    "eod_helpers:score_coach_answer",  # MIGRATE3-EOD - the self-scored answer, one coaching_answer row
    "eod_helpers:stage_fire_input",  # MIGRATE3-EOD fix round 2 - the day's fetch staged beside the data (R-2, R-6)
    "eod_helpers:run_cru_pass",  # MIGRATE3-EOD fix round 2 - the CRU walk and write in one writer (R-2)
    "eod_helpers:record_fire_stopped",  # MIGRATE3-EOD fix round 2 - a refused step leaves a receipt
    "eod_helpers:reconcile_sent_staged",  # EODHARD3 - the mail leg over the Sent batch staged raw, one call
    "eod_helpers:reconcile_chat_staged",  # EODHARD3 - the chat leg over the chat batch staged raw, one call
    "eod_helpers:clear_fire_staging",  # EODHARD3 - the fire's staged files removed
    "eod_helpers:close_from_eod",  # EODTAP2 - a tapped mark done, drop or push on the End of Day's row list (MIGRATE3-EOD N-8)
    "friday_wrap_helpers:record_offer",  # MIGRATE3-FW - the earned door closed after the post
    "friday_wrap_helpers:plate_cut",  # MIGRATE3-FW - the wrap's plate cut (mints plate numbers)
    "friday_wrap_helpers:measure_line",  # MIGRATE3-FW - the one measure line (its plate count mints)
    "friday_wrap_helpers:coaching_blocks",  # MIGRATE3-FW - the week against the word (next week's three mint)
    "friday_wrap_helpers:post_turn",  # MIGRATE3-FW - the whole-turn post, the cut re-composed in process
    "friday_wrap_helpers:land_recap",  # MIGRATE3-FW - the recap document from a payload landed by write (the by-path shape, D-T2B-7)
    "morning_brief_helpers:run_morning_brief_pack",  # MIGRATE3-MB - Phase 3.9's pack, the CLI's own builder beside the data
    "morning_brief_helpers:record_fire_stopped",  # MIGRATE3-MB - a refused or timed-out step lands the surface_failed receipt (fix round 1 N-3)
    "schedule_config:log_schedule_config_change",  # FIRE3 - the maintenance fire's re-enable receipt, one schedule_config_changed row (D-T3-3)
    # PEOPLEWRITE2
    "people_writer:door_create_person",  # PEOPLEWRITE2 - people-crm's new person, a duplicate or a one-word name answered (D-W2-5)
    "people_writer:door_update_person",  # PEOPLEWRITE2 - people-crm's field change on a person on file
    "people_writer:door_record_person_fact",  # PEOPLEWRITE2 - people-crm's remember-a-fact, one person_fact_observed row
    "people_writer:door_auto_add_person",  # PEOPLEWRITE2 - people-crm's rich-context auto-add, the same-name gate first
    "deliverables:land",
})

# The key the layer stamps on every helper child process (MF-M2-13). Spelled
# here and read as a literal in `events_io`, which must not import this module.
HELPER_DOOR_ENV = "CR_HELPER_DOOR"

#: The ONLY variables `plan` will ever put in front of the command, in this
#: order. A fixed forward set, never "whatever starts with CR_": the rendered
#: string is pasted into a shell on the customer's machine, so what crosses is
#: a reviewed list or it is nothing.
FORWARD_ENV_KEYS = ("CR_WRITER_ID", "CR_WRITER_DERIVATION",
                    "CR_FIRED_VIA", "CR_DEVICE_WORKSPACE", "CR_TRIGGERED_BY")

#: Where the customer opens the workspace on their own computer. Read here and
#: carried on the resolver's context; `deliverables` is what spells a path with
#: it. Named once, so the layer and the deliverables door cannot disagree.
DEVICE_WORKSPACE_ENV = "CR_DEVICE_WORKSPACE"

#: Where `plan` rendered the line (IDENT1 I-11, Probe B defect 1). The merged
#: seat's real fire shape ran `plan` from the STAGED runtime through the device
#: shell, where no account lives, so every line it rendered crossed with no
#: writer identity. The rendered line now says where it was rendered, as the
#: first thing on it: `container` (a plugin root, with an identity to forward)
#: or `device` (the staged runtime). The door reads it back and names the
#: cause when a device-rendered write has no identity.
PLAN_ORIGIN_ENV = "CR_PLAN_ORIGIN"
PLAN_ORIGIN_CONTAINER = "container"
PLAN_ORIGIN_DEVICE = "device"
RENDER_IN_CONTAINER_HINT = "render plan in the container"

# How a helper child hands back a record it held rather than wrote.
SIDECAR_MARKER = "\x00CR_SIDECARS\x00"

# Modules no allow-list entry may ever name, asserted in the suite so a future
# addition cannot quietly re-open B-2.
RUN_HELPER_FORBIDDEN_MODULES = ("atomic_write", "os", "shutil", "pathlib",
                                "subprocess", "shlex", "tempfile")

# A helper argument is treated as a PATH (and therefore fenced like any other
# target) when it is absolute, carries a separator, or ends in one of these.
PATH_LIKE_SUFFIXES = (".json", ".jsonl", ".md", ".txt", ".html", ".csv",
                      ".py", ".yaml", ".yml", ".lock", ".rev", ".seqhw",
                      ".log", ".zip", ".ics", ".docx", ".xlsx", ".pptx")

# Refusal reasons — spelled once so prose, tests and BOOT3 read the same word.
R_NO_WORKSPACE = "no_workspace_on_this_host"
R_NOT_ATTACHED = "folder_not_attached"
R_REFUSED_PATH = "refused_path"
R_REFUSED_RUNTIME = "refused_runtime_cache"
R_REFUSED_LOCK = "refused_lock_file"
R_REFUSED_APPEND_ONLY = "refused_append_only"
R_REFUSED_PROTECTED = "refused_protected"
R_NOT_REGISTERED = "not_registered_jsonl"
R_NOT_IN_MANIFEST = "not_in_manifest"
R_NOT_ALLOWED_HELPER = "not_allowed_helper"
R_NOT_ALLOWED_WRITER = "not_allowed_writer"
R_WRITER_IDENTITY_REQUIRED = "writer_identity_required"
R_STALE_READ = "stale_read"
R_TIMEOUT = "timeout"
R_MISSING_PARENT = "missing_parent"
R_NOT_FOUND = "not_found"
R_INVALID_JSON = "invalid_json"
R_BINARY_UNSUPPORTED = "binary_unsupported"
R_HELPER_FAILED = "helper_failed"
R_BAD_ARGS = "bad_args"
R_WRITE_UNVERIFIED = "write_unverified"


class StaleRead(RuntimeError):
    """The compare-and-swap lost: the file moved under the caller's read.

    Raised INSIDE the sentinel lock by the `write` verb's precheck so the
    compare and the write are one critical section on one host.
    """


# ---------------------------------------------------------------------------
# The discover block — the ONE thing that runs in the VM before the runtime is
# installed. Stdlib only, no plugin imports, byte-identical everywhere it is
# pasted (guard G68 pins that). Keep it at or under 40 lines.
# ---------------------------------------------------------------------------

DISCOVER_BLOCK = r"""python3 - <<'CR_DISCOVER'
import json, os, sys
from pathlib import Path
P = {"_archive", "_demo-framework"}
def find(base, md=5):
    base = Path(base); hits = []
    for dp, dn, fn in os.walk(str(base)):
        d = Path(dp); k = len(d.parts) - len(base.parts)
        dn[:] = [] if k >= md else [x for x in dn if x not in P]
        if (d / "_hq" / "data" / "entities.json").is_file(): hits.append((len(d.parts), dp)); dn[:] = []
    return Path(sorted(hits)[0][1]) if hits else None
ws = find(os.environ.get("CR_WORKSPACE") or (Path.home() / "mnt"))
o = {"ws": None, "basename": None, "runtime_present": False, "runtime_version": None, "manifest_sha": None, "staged_root": None, "brain_file": None, "device_path": os.environ.get("CR_DEVICE_WORKSPACE") or None, "python": "%d.%d.%d" % sys.version_info[:3], "probes": {"replace_over_existing": None, "unlink": None, "flock": None}}
if ws is not None:
    o["ws"] = str(ws); o["basename"] = ws.name
    o["brain_file"] = str(ws / "CLAUDE.md") if (ws / "CLAUDE.md").is_file() else None
    rt = ws / "_hq" / ".cache" / "cr-runtime"
    try:
        c = json.loads((rt / "current.json").read_text(encoding="utf-8")); d = str(c.get("dir") or c.get("version") or "")
        if c.get("version") and d and (rt / d).is_dir(): o["runtime_present"] = True; o["runtime_version"] = c["version"]; o["manifest_sha"] = c.get("manifest_sha"); o["staged_root"] = str(rt / d)
    except Exception: pass
    s = ws / "_hq" / ".system"
    try:
        s.mkdir(parents=True, exist_ok=True); a = s / ".cr-probe"; b = s / ".cr-probe.new"
        a.write_text("1", encoding="utf-8"); b.write_text("2", encoding="utf-8"); os.replace(str(b), str(a))
        o["probes"]["replace_over_existing"] = a.read_text(encoding="utf-8") == "2"
    except Exception: o["probes"]["replace_over_existing"] = False
    try:
        u = s / ".cr-probe.u"; u.write_text("x", encoding="utf-8"); u.unlink(); o["probes"]["unlink"] = True
    except Exception: o["probes"]["unlink"] = False
    try:
        import fcntl; f = open(s / ".cr-probe", "r+"); fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(f.fileno(), fcntl.LOCK_UN); f.close(); o["probes"]["flock"] = True
    except Exception: o["probes"]["flock"] = False
print(json.dumps(o))
CR_DISCOVER"""

# The keys `discover` answers with, in both faces. BOOT3's bootloader v3 reads
# exactly these names — a rename here is a break there.
DISCOVER_KEYS = ("ws", "basename", "runtime_present", "runtime_version",
                 "manifest_sha", "staged_root", "brain_file", "device_path",
                 "python", "probes")
PROBE_KEYS = ("replace_over_existing", "unlink", "flock")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent


def _ensure_scripts_on_path() -> None:
    d = str(_scripts_dir())
    if d not in sys.path:
        sys.path.insert(0, d)


def plugin_root() -> Path:
    """The plugin checkout this file belongs to (`command-room/`)."""
    return _scripts_dir().parent.parent


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_root(d: Path) -> bool:
    return (d / "_hq" / "data" / "entities.json").is_file()


def find_root_up(start: Optional[Path] = None) -> Optional[Path]:
    """The nearest ancestor of `start` that carries the substrate anchor."""
    here = Path(start).resolve() if start is not None else Path.cwd().resolve()
    candidate = here if here.is_dir() else here.parent
    for d in (candidate, *candidate.parents):
        if _is_root(d):
            return d
    return None


def find_root_down(base: Path, max_depth: int = MAX_SEARCH_DEPTH) -> Optional[Path]:
    """The SHALLOWEST anchored root at or under `base`, with `_archive/` and
    `_demo-framework/` pruned — the G43 rule, in Python.

    The decoy class this exists for is real and was found in a customer
    workspace: cleanup's own archive mirrors `_hq/...` paths, and the demo
    framework ships a whole fake substrate. First-in-traversal-order binds one
    of them; shallowest-after-prune binds the real root.
    """
    base = Path(base)
    hits: List[Tuple[int, str]] = []
    for dirpath, dirnames, _files in os.walk(str(base)):
        d = Path(dirpath)
        depth = len(d.parts) - len(base.parts)
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [x for x in dirnames if x not in PRUNE_DIRS]
        if _is_root(d):
            hits.append((len(d.parts), dirpath))
            dirnames[:] = []
    if not hits:
        return None
    hits.sort()
    return Path(hits[0][1])


def _running_from_runtime_cache() -> Optional[Path]:
    """The workspace root when THIS file is the installed runtime, else None.

    `<ws>/_hq/.cache/cr-runtime/<version>/shared/scripts/workspace_access.py`
    → `<ws>`, and only when `<ws>` carries the substrate anchor.
    """
    here = _scripts_dir()
    parts = here.parts
    for i in range(len(parts) - 3):
        if parts[i] == "_hq" and parts[i + 1] == ".cache" and parts[i + 2] == "cr-runtime":
            ws = Path(*parts[:i])
            if _is_root(ws):
                return ws
            return None
    return None


def _env_detect_mode(env: Dict[str, str]) -> Optional[str]:
    """ENV1's `env_detect.detect()`, or None when the module is not on the tree.

    Behind try/except by design (D-5, shared-module-first): this lane's suite is
    green before E0 arrives and unchanged after, and a legacy seat whose runtime
    predates `env_detect.py` never breaks on the import.
    """
    try:
        _ensure_scripts_on_path()
        import env_detect  # type: ignore
    except Exception:
        return None
    try:
        result = env_detect.detect(env=env)  # type: ignore[call-arg]
    except TypeError:
        try:
            result = env_detect.detect()  # type: ignore[misc]
        except Exception:
            return None
    except Exception:
        return None
    # ENV1 answers with an `EnvReport` DATACLASS (`env_detect.EnvReport.mode`).
    # The dict and str branches below are the fallbacks for an older runtime
    # whose `env_detect` predates it. Reading only those two shapes is what made
    # this adapter return None for every mode the moment E0 landed, collapsing
    # the whole D-1 mapping into the no-module fallback (review finding B-1).
    attr_mode = getattr(result, "mode", None)
    if isinstance(attr_mode, str) and attr_mode:
        return attr_mode
    if isinstance(result, dict):
        result = result.get("mode")
    if isinstance(result, str) and result:
        return result
    return None


def detect_host_mode(env: Optional[Dict[str, str]] = None,
                     start: Optional[Path] = None) -> str:
    """Where the data is, relative to this process (D-1).

    With `env_detect` present the mapping is a lookup, except for
    `merged_cloud`, which asks a fact: is this file the installed runtime inside
    an anchored workspace? Yes → `vm`; no → `container`.

    `unknown` is the container posture (D-1, §0.34: writers and fires refuse) —
    but only after the one FACT that outranks a non-reading has been asked. The
    VM runs this file out of the installed runtime inside the anchored
    workspace and sees none of `env_detect`'s signals, so it reads `unknown`;
    answering `container` there would refuse the verbs on the one host that CAN
    serve them.

    Without `env_detect` the fallback is deliberately conservative.
    """
    env = dict(os.environ) if env is None else env
    detected = _env_detect_mode(env)
    staged_ws = _running_from_runtime_cache()
    if detected == "merged_cloud":
        return VM if staged_ws is not None else CONTAINER
    if detected in ("legacy_cowork", "claude_code_local"):
        return MODE_FROM_ENV[detected]
    if staged_ws is not None:
        return VM
    if detected == "unknown":
        return MODE_FROM_ENV["unknown"]
    # Fallback: no env_detect on this tree.
    if str(env.get("CLAUDE_CODE_REMOTE", "")).strip().lower() == "true":
        return CONTAINER
    ws_env = str(env.get("CR_WORKSPACE", "")).strip()
    if ws_env and _is_root(Path(ws_env)):
        return LOCAL
    return LOCAL if find_root_up(start) is not None else CONTAINER


def _device_workspace(env: Dict[str, str]) -> Optional[str]:
    """The workspace folder's path on the CUSTOMER'S OWN COMPUTER, or None.

    The third source `deliverables.device_workspace_root` was written for -
    "for the day the resolver learns to carry it" - closed. The value comes
    from `CR_DEVICE_WORKSPACE`, which the Access preamble exports from
    `get_device_info`'s `connectedFolders` on a merged seat and which the
    bootloader already holds as its substituted workspace path; `plan`
    forwards it across the door. Without it a landed document can only be
    described in words, which is right but poorer, and the walk's prep
    invented a breadcrumb instead.
    """
    value = str(env.get(DEVICE_WORKSPACE_ENV, "") or "").strip()
    return value or None


def _env_mode_name(env: Dict[str, str]) -> Optional[str]:
    """The `env_detect` mode name to export as `CR_ENV` (None when absent)."""
    return _env_detect_mode(env)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def resolve(start: Optional[Path] = None,
            env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """`{basename, vm_path, staged_root, brain_file, mode, data_dir,
    runtime_version, manifest_sha, tz, python}` — gap analysis §4.2.1.

    In `container` mode every path field is None: there is no workspace on this
    host and the layer never guesses one.
    """
    env = dict(os.environ) if env is None else env
    mode = detect_host_mode(env, start)
    out: Dict[str, Any] = {
        "mode": mode,
        "basename": None,
        "vm_path": None,
        "staged_root": None,
        "brain_file": None,
        "data_dir": None,
        "runtime_version": None,
        "manifest_sha": None,
        "tz": None,
        "device_path": _device_workspace(env),
        "python": "%d.%d.%d" % sys.version_info[:3],
    }
    if mode == CONTAINER:
        return out

    ws = _running_from_runtime_cache()
    if ws is None:
        ws_env = str(env.get("CR_WORKSPACE", "")).strip()
        if ws_env:
            candidate = Path(ws_env).expanduser()
            if _is_root(candidate):
                ws = candidate.resolve()
            elif candidate.is_dir():
                ws = find_root_down(candidate)
    if ws is None:
        ws = find_root_up(start)
    if ws is None and start is not None and Path(start).is_dir():
        ws = find_root_down(Path(start))
    if ws is None:
        return out

    ws = Path(ws).resolve()
    out["vm_path"] = str(ws)
    out["basename"] = ws.name
    brain = ws / "CLAUDE.md"
    out["brain_file"] = str(brain) if brain.is_file() else None
    out["data_dir"] = str(_data_dir(ws))
    out["tz"] = _workspace_tz(ws)
    version, manifest_sha, staged = _installed_runtime(ws)
    out["runtime_version"] = version
    out["manifest_sha"] = manifest_sha
    out["staged_root"] = str(staged) if staged is not None else None
    return out


def _data_dir(ws: Path) -> Path:
    """`<ws>/_hq/data` through `data_root.resolve` — with NO override.

    The layer never sets `CR_DATA_ROOT` (`data_root.py:40`); relocating the
    substrate is a separate, M-gated migration.
    """
    try:
        _ensure_scripts_on_path()
        import data_root  # type: ignore
        return Path(data_root.resolve(ws))
    except Exception:
        return ws / "_hq" / "data"


def _workspace_tz(ws: Path) -> Optional[str]:
    """`workspace.user_timezone` as a STRING, or None.

    `tz.load_workspace_tz` returns a tzinfo and RAISES when the zone is unset,
    and a resolver must never raise over a missing preference — so the zone NAME
    is read here, with the same two-block merge `tz.py:154-161` does (the inner
    `entities.workspace` block, then the top-level one, top level winning). Same
    rule, one place it can drift: the pin compares this against `tz.py`'s own
    answer on the fixture.
    """
    try:
        raw = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
        inner = raw.get("entities") if isinstance(raw.get("entities"), dict) else None
        inner_ws = (inner or {}).get("workspace")
        top_ws = raw.get("workspace")
        merged = {
            **(inner_ws if isinstance(inner_ws, dict) else {}),
            **(top_ws if isinstance(top_ws, dict) else {}),
        }
        value = merged.get("user_timezone")
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return None


def runtime_cache_dir(ws: Path) -> Path:
    return Path(ws) / "_hq" / ".cache" / "cr-runtime"


def _installed_runtime(ws: Path) -> Tuple[Optional[str], Optional[str], Optional[Path]]:
    """`(version, manifest_sha, staged_root)` from the `current.json` pointer.

    A pointer FILE, never a symlink: Drive and FUSE do not carry symlinks
    reliably, and a dangling one would silently resolve to nothing.
    """
    cache = runtime_cache_dir(ws)
    try:
        payload = json.loads((cache / CURRENT_POINTER).read_text(encoding="utf-8"))
    except Exception:
        return None, None, None
    if not isinstance(payload, dict):
        return None, None, None  # REVIEW_T2B_BRIDGE3 N-7: a damaged pointer is no runtime
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        return None, None, None
    # IDENT1 I-7: the pointer names the DIRECTORY when an install could not
    # reuse the bare version directory (`<version>-<sha8>`); the version it
    # reports stays the plugin's version. Never the bare dir by assumption.
    folder = payload.get("dir")
    folder = folder if isinstance(folder, str) and folder.strip() else version
    staged = cache / folder
    if not staged.is_dir():
        return None, None, None
    sha = payload.get("manifest_sha")
    return version, sha if isinstance(sha, str) else None, staged


# ---------------------------------------------------------------------------
# Envelopes and the path fence
# ---------------------------------------------------------------------------

def _folder_attached(ctx: Dict[str, Any]) -> bool:
    vm_path = ctx.get("vm_path")
    return bool(vm_path) and Path(vm_path).is_dir()


def _envelope(verb: str, ctx: Dict[str, Any], started: float,
              **fields: Any) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "ok": bool(fields.pop("ok", False)),
        "verb": verb,
        "ws": ctx.get("vm_path"),
        "mode": ctx.get("mode"),
        "runtime_version": ctx.get("runtime_version"),
        "host_python": ctx.get("python"),
        "elapsed_ms": int((time.time() - started) * 1000),
        "folder_attached": _folder_attached(ctx),
    }
    env.update(fields)
    return env


def _refuse(verb: str, ctx: Dict[str, Any], started: float, reason: str,
            **fields: Any) -> Dict[str, Any]:
    return _envelope(verb, ctx, started, ok=False, reason=reason, **fields)


def _preflight(verb: str, ctx: Dict[str, Any], started: float) -> Optional[Dict[str, Any]]:
    """The two refusals every data verb shares, in order.

    `container` first (§0.25 — there is no workspace on this host, and the
    layer never stages), then re-request safety (F22 side finding: the folder
    grant can drop mid-session on a bridge reconnect, so one verb per call means
    the model re-requests and retries the SAME command string).
    """
    if ctx.get("mode") == CONTAINER or not ctx.get("vm_path"):
        return _refuse(verb, ctx, started, R_NO_WORKSPACE, next="device_bash")
    if not _folder_attached(ctx):
        return _refuse(verb, ctx, started, R_NOT_ATTACHED,
                       next="device_request_folder_access")
    return None


def fence_path(vm_path: Path, rel: str) -> Tuple[Optional[Path], Optional[str]]:
    """`(absolute path, None)` when `rel` is a legal target, else `(None, reason)`.

    Three fences, in order: inside the workspace (an absolute `rel` or any `..`
    escape fails `relative_to`), never under the runtime cache (code is not
    data), never the writer lock (its bytes must not change).
    """
    vm_path = Path(vm_path).resolve()
    try:
        target = (vm_path / rel).resolve()
    except (OSError, ValueError):
        return None, R_REFUSED_PATH
    try:
        inside = target.relative_to(vm_path)
    except ValueError:
        return None, R_REFUSED_PATH
    posix = inside.as_posix()
    if posix == RUNTIME_CACHE_REL or posix.startswith(RUNTIME_CACHE_REL + "/"):
        return None, R_REFUSED_RUNTIME
    if posix == LOCK_REL:
        return None, R_REFUSED_LOCK
    return target, None


# ---------------------------------------------------------------------------
# Host-face verbs
# ---------------------------------------------------------------------------

def read(rel: str, *, binary: bool = False, ctx: Optional[Dict[str, Any]] = None,
         env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """`{ok, rel, text|b64, size, mtime_ns, sha256}` for one workspace file."""
    started = time.time()
    ctx = resolve(env=env) if ctx is None else ctx
    refusal = _preflight("read", ctx, started)
    if refusal is not None:
        return refusal
    target, reason = fence_path(Path(ctx["vm_path"]), rel)
    if reason is not None:
        return _refuse("read", ctx, started, reason, rel=rel)
    try:
        raw = target.read_bytes()
        stat = target.stat()
    except FileNotFoundError:
        return _refuse("read", ctx, started, R_NOT_FOUND, rel=rel)
    except OSError as exc:
        return _refuse("read", ctx, started, "read_failed", rel=rel, detail=str(exc))
    out: Dict[str, Any] = {
        "rel": rel,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if binary:
        out["b64"] = base64.b64encode(raw).decode("ascii")
    else:
        try:
            out["text"] = raw.decode("utf-8")
        except UnicodeDecodeError:
            out["b64"] = base64.b64encode(raw).decode("ascii")
    return _envelope("read", ctx, started, ok=True, **out)


def manifest_modules(ctx: Dict[str, Any]) -> frozenset:
    """The module names `run_helper` may import.

    From the installed runtime's `manifest.json` when one is installed — the
    allow-list is the manifest, so a modified or partial runtime cannot widen
    it. On a machine with no runtime installed (a developer box, this lane's
    fixtures, a legacy seat) the same set is derived from the plugin checkout's
    own `shared/scripts/**/*.py`, which is exactly what the manifest would ship.
    """
    staged = ctx.get("staged_root")
    names = set()
    if staged:
        try:
            payload = json.loads((Path(staged) / MANIFEST_NAME).read_text(encoding="utf-8"))
            for rel in (payload.get("files") or {}):
                if rel.startswith("shared/scripts/") and rel.endswith(".py"):
                    names.add(Path(rel).stem)
        except Exception:
            names = set()
    if not names:
        for path in _iter_runtime_scripts(plugin_root()):
            names.add(path.stem)
    names.discard("__init__")
    return frozenset(names)


def _helper_child_env(ctx: Dict[str, Any],
                      env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The env block every helper process gets (gap analysis §4.2.4).

    `TZ` is the workspace zone, not the host's: under strategy 2 the helpers run
    in a UTC VM, and the machine-local naive reads (`late_fire`, `receipts`, the
    lock diagnostics) would otherwise resolve to the wrong day. `CR_DATA_ROOT`
    is REMOVED, never set — relocating the substrate is not this layer's call.
    """
    base = dict(os.environ if env is None else env)
    base.pop("CR_DATA_ROOT", None)
    base["CR_WORKSPACE"] = str(ctx.get("vm_path") or "")
    base["PYTHONDONTWRITEBYTECODE"] = "1"
    # THE DOOR SAYS SO (MF-M2-13). `run_helper` promises it writes nothing at
    # all, and the allow-list is read/compute only — but a listed READ can
    # still reach a best-effort diagnostic writer two frames down
    # (`events_io._record_stale_read` did, and dropped a sidecar beside the
    # customer's ledger). A helper child is told it is a helper child, so a
    # best-effort writer on a read path can hold its record; nothing else
    # changes, and an in-process or in-shell reader never sees this key.
    base[HELPER_DOOR_ENV] = "run_helper"
    if ctx.get("tz"):
        base["TZ"] = str(ctx["tz"])
    if ctx.get("runtime_version"):
        base["CR_RUNTIME_VERSION"] = str(ctx["runtime_version"])
    base["CR_HOST_MODE"] = str(ctx.get("mode") or "")
    env_name = _env_mode_name(dict(os.environ if env is None else env))
    if ctx.get("mode") == VM and env_name in (None, "", "unknown"):
        # In the sandbox VM `env_detect` sees none of the container's signals
        # and answers `unknown` — the value reserved for "a writer refuses".
        # The layer already knows better: a VM only exists on a merged seat,
        # which is the D-1 row `vm -> merged_cloud`. Without this, every helper
        # run beside the data is told the environment is unknown, and the M2
        # writers that read `CR_ENV` would refuse the one host that can serve
        # them (merged-tree review M-1).
        env_name = "merged_cloud"
    if env_name:
        base["CR_ENV"] = env_name
    # THE DOOR DOES NOT STRIP THE FORWARD SET. These four arrive on the
    # command line that started THIS process (`plan` renders them), so they are
    # already in the copied environment — restating them is the pin: a future
    # edit that filters this dict cannot silently drop the writer identity and
    # send the ledger back to session tokens. Only keys actually present are
    # re-set, so nothing is invented on a seat that forwarded nothing.
    source = dict(os.environ if env is None else env)
    for key in FORWARD_ENV_KEYS:
        value = str(source.get(key, "")).strip()
        if value:
            base[key] = value
    return base


_HELPER_RUNNER = r"""
import json, sys
payload = json.loads(sys.stdin.read())
sys.path.insert(0, payload["scripts"])
import importlib
mod = importlib.import_module(payload["module"])
fn = getattr(mod, payload["function"])
def plain(value):
    import dataclasses
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return plain(dataclasses.asdict(value))
        except (TypeError, RecursionError):
            return str(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    return str(value)
def hand_back_held():
    try:
        import read_alarm
        held = read_alarm.held_sidecars()
    except Exception:
        held = []
    if held:
        sys.stderr.write("\x00CR_SIDECARS\x00" + json.dumps(plain(held)) + chr(10))
try:
    result = fn(**payload["args"])
except BaseException as exc:
    hand_back_held()
    reason = getattr(exc, "cr_refusal_reason", None)
    if reason or type(exc).__name__ == "WriterIdentityRequired":
        sys.stdout.write("\x00CR_REFUSAL\x00" + json.dumps({
            "reason": str(reason or "writer_identity_required"),
            "line": str(getattr(exc, "line", "") or ""),
            "cause": str(getattr(exc, "cause", "") or "")}))
        sys.exit(0)
    raise
sys.stdout.write("\x00CR_RESULT\x00" + json.dumps(plain(result)))
hand_back_held()
"""

#: How a child hands back a writer-identity refusal instead of a traceback.
REFUSAL_MARKER = "\x00CR_REFUSAL\x00"


#: Longer than any path a filesystem will take (Windows' widest is 32,767
#: characters; POSIX PATH_MAX is 4,096). A string past this is CONTENT - a
#: document's bytes, a section body - and fencing it as a path answers the
#: wrong question: `Path.resolve` raises on it, so the door refused every
#: export whose base64 carried a `/` (DOCS1 fix round 3, review N-19), and
#: would refuse a long brief section for the same reason.
PATH_MAX_CHARS = 4096

#: The door's argument cap (ruling D-T2B-7, D-W2-1): how many items
#: `fence_helper_args` walks (every dict key and value, every list element,
#: the value itself) before the payload is refused `bad_args`. The walk is
#: what bounds the fence's own time; a payload past it is pathological. The
#: by-path shapes (a staged file read by a `*_rel`) stay the product: this is
#: headroom, not a licence to hand a day's fetch inline. Every pin that names
#: the cap reads this constant.
ARG_WALK_CAP = 20000

#: The byte ceiling on an `args_file` (ruling D-T2B-7, D-W2-1), measured on
#: disk before the file is read: it bounds one read of a session's scratch
#: file into memory, so a runaway payload is refused before it is parsed.
#: SIZED OVER the staging primitive's own ceiling (CAP1, a deviation from
#: D-W2-1's 2 MiB for the reviewer to rule): `eod_helpers.STAGE_MAX_BYTES`
#: is 32 MiB decoded, which rides this file as base64 (about 42.7 MiB), and
#: a worst real day's transcript stage (1.8 MB decoded, REVIEW_T2B_EODHARD3)
#: is already past 2 MiB as base64. A smaller ceiling here would refuse the
#: End of Day's own stage before its writer could answer its one sentence.
ARGS_FILE_MAX_BYTES = 48 * 1024 * 1024

#: How deep the fence walks before a payload is refused `bad_args` (CAP1 fix
#: round 1, REVIEW_W2_CAP1 N-2). The walk itself is iterative and cannot
#: raise, but a structure nested deeper than this is refused before the door
#: serialises it for the child, where a `RecursionError` would escape the
#: door. No real form nests past about a dozen levels.
ARG_DEPTH_CAP = 100

#: The `detail` a refused oversized `args_file` carries (reason `bad_args`).
ARGS_FILE_TOO_LARGE = "args_file_too_large"

#: The `detail` an `args_file` that is not a regular file carries (a pipe, a
#: device, anything whose size on disk says nothing about its read).
ARGS_FILE_NOT_A_FILE = "args_file: not a file"


def _read_args_file(args_file: Any) -> Tuple[Any, Optional[str]]:
    """`(parsed JSON, None)` for a session's `args_file`, else `(None,
    detail)` for a refusal (reason `bad_args`) - the ONE read both doors do.

    CAP1 fix round 1 (REVIEW_W2_CAP1 N-1): opened ONCE; the open handle is
    measured (`os.fstat`), so the size and the read are of the same file; a
    handle that is not a regular file is refused; and the read itself is
    bounded at `ARGS_FILE_MAX_BYTES + 1` bytes, so a file that grew after
    the measure is refused as too large and never read whole. A missing or
    unopenable file answers its `OSError` name, as before. The open does not
    wait on a pipe with no writer (`O_NONBLOCK` where the platform has it).
    """
    import stat as _stat

    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        with os.fdopen(os.open(str(args_file), flags), "rb") as fh:
            status = os.fstat(fh.fileno())
            if not _stat.S_ISREG(status.st_mode):
                return None, ARGS_FILE_NOT_A_FILE
            if status.st_size > ARGS_FILE_MAX_BYTES:
                return None, ARGS_FILE_TOO_LARGE
            raw = fh.read(ARGS_FILE_MAX_BYTES + 1)
        if len(raw) > ARGS_FILE_MAX_BYTES:
            return None, ARGS_FILE_TOO_LARGE
        return json.loads(raw.decode("utf-8")), None
    except (OSError, ValueError, RecursionError) as exc:
        # fix round 1 N-2: a file nested past the parser's own depth is a
        # refusal (`args_file: RecursionError`), never a raise out of the door
        return None, "args_file: " + type(exc).__name__

#: Argument keys whose VALUE is a document's bytes, never a path: the export
#: payload's `content_base64` (DOCS1 D-2). The key itself is still walked;
#: the value is decoded by the writer and meets `land`'s fences as bytes.
CONTENT_KEY_SUFFIXES = ("_base64",)


def _looks_like_path(value: str) -> bool:
    """True when a helper argument is path-shaped and must therefore be fenced.

    Deliberately wide: absolute, carrying a separator, or ending in a known
    substrate extension. A false positive costs a caller one relative path
    inside the workspace; a false negative is B-2 again. A string longer than
    any path can be (`PATH_MAX_CHARS`) is content, not a path.
    """
    text = value.strip()
    if not text:
        return False
    if len(text) > PATH_MAX_CHARS:
        return False
    if "/" in text or "\\" in text:
        return True
    try:
        if Path(text).is_absolute():
            return True
    except (OSError, ValueError):
        return False
    return text.lower().endswith(PATH_LIKE_SUFFIXES)


def fence_helper_args(vm_path: Path, args: Any) -> Optional[Tuple[str, str]]:
    """`(offending value, reason)` for the first path-shaped argument that is
    not a legal target, else None.

    Every string anywhere in the argument structure is walked — a path hidden
    one list or dict deep is still a path. The fence is `fence_path`'s, so a
    helper argument can reach exactly what `read` and `write` can reach.
    """
    stack = [args]
    seen = 0
    # CAP1 fix round 1 (N-2): each stacked item's depth, kept beside the
    # stack so the walk itself reads exactly as before
    depths = [0]
    while stack:
        item = stack.pop()
        depth = depths.pop()
        seen += 1
        if seen > ARG_WALK_CAP:              # a pathological payload is bad_args
            return ("<too many arguments to fence>", R_BAD_ARGS)
        if depth > ARG_DEPTH_CAP:            # nested past any real form
            return ("<arguments nested too deep to fence>", R_BAD_ARGS)
        if isinstance(item, str):
            if not _looks_like_path(item):
                continue
            _, reason = fence_path(vm_path, item)
            if reason is not None:
                return (item, reason)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            # A content key's value is a document's bytes (base64), never a
            # path (DOCS1 fix round 3, review N-19); every other value walks.
            stack.extend(v for k, v in item.items()
                         if not (isinstance(k, str)
                                 and k.lower().endswith(CONTENT_KEY_SUFFIXES)))
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)
        depths.extend([depth + 1] * (len(stack) - len(depths)))
    return None


def run_helper(name: str, args: Optional[Dict[str, Any]] = None, *,
               timeout_s: int = DEFAULT_TIMEOUT_S,
               ctx: Optional[Dict[str, Any]] = None,
               env: Optional[Dict[str, str]] = None,
               args_file: Optional[str] = None) -> Dict[str, Any]:
    """`{ok, result, stderr_tail, elapsed_ms}` — run ONE shared helper in place.

    `name` is `module:function` and passes THREE gates before anything runs:
    the module is in the installed runtime's manifest, the whole `module:
    function` pair is in `RUN_HELPER_ALLOWLIST`, and every path-shaped argument
    fences exactly as a `read` or `write` target would. The manifest alone was
    not enough — `atomic_write` is in it by construction, which made this verb a
    write-anywhere primitive (review B-2) — and neither is a gated writer:
    every entry reads or computes, so this verb writes nothing at all
    (review B-1). Writes go through `write` and `append_jsonl`, which carry the
    `REGISTERED_JSONL` rule this door cannot express. The helper runs in a child process
    with cwd = the workspace root and
    `sys.path[0]` = the installed runtime's scripts dir, under a wall-clock
    budget the LAYER enforces — a hang comes back as `reason: timeout` with an
    envelope, never as a truncated device_bash reply.
    """
    started = time.time()
    ctx = resolve(env=env) if ctx is None else ctx
    refusal = _preflight("run_helper", ctx, started)
    if refusal is not None:
        return refusal
    if not isinstance(name, str) or name.count(":") != 1:
        return _refuse("run_helper", ctx, started, R_BAD_ARGS, name=name,
                       detail="name must be 'module:function'")
    module, function = name.split(":", 1)
    if not module or not function:
        return _refuse("run_helper", ctx, started, R_BAD_ARGS, name=name)
    if module not in manifest_modules(ctx):
        return _refuse("run_helper", ctx, started, R_NOT_IN_MANIFEST, name=name)
    if name not in RUN_HELPER_ALLOWLIST:
        return _refuse("run_helper", ctx, started, R_NOT_ALLOWED_HELPER, name=name)
    # BRIDGE3 (Train 2b §5 MUST 1): a read's payload too large or too
    # quote-laden for a pasted line (the bridge's registered listing) travels
    # as `args_file`, exactly as `run_writer` takes it: a JSON object in THIS
    # SESSION'S OWN scratch, read here, fenced like every other argument.
    if args_file:
        # CAP1 (D-T2B-7, fix round 1 N-1): one open, measured on the handle,
        # a bounded read; a missing file is its OSError name, as before.
        loaded, refused = _read_args_file(args_file)
        if refused is not None:
            return _refuse("run_helper", ctx, started, R_BAD_ARGS, name=name,
                           detail=refused)
        if not isinstance(loaded, dict):
            return _refuse("run_helper", ctx, started, R_BAD_ARGS, name=name,
                           detail="args_file must hold a JSON object")
        args = dict(loaded, **dict(args or {}))
    offender = fence_helper_args(Path(ctx["vm_path"]), dict(args or {}))
    if offender is not None:
        return _refuse("run_helper", ctx, started, offender[1], name=name,
                       arg=offender[0])

    staged = ctx.get("staged_root")
    scripts = str(Path(staged) / "shared" / "scripts") if staged else str(_scripts_dir())
    payload = json.dumps({
        "scripts": scripts,
        "module": module,
        "function": function,
        "args": dict(args or {}),
    })
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _HELPER_RUNNER],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(ctx["vm_path"]),
            env=_helper_child_env(ctx, env),
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return _refuse("run_helper", ctx, started, R_TIMEOUT, name=name,
                       timeout_s=timeout_s)
    # MF-M2-13: a read helper that would have dropped a sidecar beside the
    # customer's file holds the record instead, and it comes back HERE. The
    # layer carries the evidence; the write never happens.
    stderr_out = proc.stderr or ""
    sidecars: List[Dict[str, Any]] = []
    if SIDECAR_MARKER in stderr_out:
        head, _sep, raw = stderr_out.partition(SIDECAR_MARKER)
        line, _nl, rest = raw.partition(chr(10))
        # whatever the helper wrote on its own account — a traceback,
        # a warning — is kept; only the layer's own line comes out.
        stderr_out = head + rest
        try:
            parsed = json.loads(line)
            if isinstance(parsed, list):
                sidecars = parsed
        except ValueError:
            sidecars = []
    stderr_tail = stderr_out[-2000:]
    marker = "\x00CR_RESULT\x00"
    stdout = proc.stdout or ""
    refused = _child_refusal(stdout)
    if refused is not None:
        return _refuse("run_helper", ctx, started,
                       refused.get("reason") or R_WRITER_IDENTITY_REQUIRED,
                       name=name, line=refused.get("line"),
                       cause=refused.get("cause"), sidecars=sidecars)
    if proc.returncode != 0 or marker not in stdout:
        return _refuse("run_helper", ctx, started, R_HELPER_FAILED, name=name,
                       stderr_tail=stderr_tail, returncode=proc.returncode,
                       sidecars=sidecars)
    try:
        result = json.loads(stdout.split(marker, 1)[1])
    except ValueError:
        return _refuse("run_helper", ctx, started, R_HELPER_FAILED, name=name,
                       stderr_tail=stderr_tail, sidecars=sidecars)
    return _envelope("run_helper", ctx, started, ok=True, name=name,
                     result=result, stderr_tail=stderr_tail,
                     sidecars=sidecars, **_origin_fields(env))


def _child_refusal(stdout: str) -> Optional[Dict[str, Any]]:
    """The writer-identity refusal a child handed back, or None."""
    if REFUSAL_MARKER not in (stdout or ""):
        return None
    try:
        parsed = json.loads(stdout.split(REFUSAL_MARKER, 1)[1])
    except ValueError:
        return {"line": "", "cause": ""}
    return parsed if isinstance(parsed, dict) else {"line": "", "cause": ""}


def _module_in_manifest(module: str, ctx: Dict[str, Any]) -> bool:
    """`manifest_modules`, taught one more spelling: a DOTTED module (the
    update bridge's re-pin lives in `release_actions/`) is in the runtime when
    its own file is, never because a module with the same last name is."""
    if "." not in module:
        return module in manifest_modules(ctx)
    rel = "shared/scripts/" + module.replace(".", "/") + ".py"
    staged = ctx.get("staged_root")
    if staged:
        try:
            payload = json.loads((Path(staged) / MANIFEST_NAME).read_text(encoding="utf-8"))
            files = payload.get("files") or {}
            if files:
                return rel in files
        except Exception:
            pass
    return (plugin_root() / rel).is_file()


def _relativize(value: Any, vm_path: Path) -> Any:
    """Every absolute path inside the workspace, spelled workspace-relative.

    A writer answers with where it wrote, and on a merged seat that spelling
    is the sandbox mount (`/sessions/<id>/mnt/...`) - a session id in the
    envelope the model reads back, which is the leak class R-DELIV1-2 closed
    for landed files. The envelope names the file by `rel`; the customer's own
    path is `deliverables`' to spell."""
    if isinstance(value, str):
        text = value.strip()
        if text and ("/" in text or "\\" in text):
            try:
                candidate = Path(text)
                if candidate.is_absolute():
                    return candidate.resolve().relative_to(vm_path).as_posix()
            except (OSError, ValueError):
                return value
        return value
    if isinstance(value, dict):
        return {k: _relativize(v, vm_path) for k, v in value.items()}
    if isinstance(value, list):
        return [_relativize(v, vm_path) for v in value]
    return value


def _writer_child_env(ctx: Dict[str, Any],
                      env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The helper child's environment, minus the one key that says "read
    door": a writer child is not a helper child, so a best-effort writer
    beneath it (the identity cache) is allowed to write."""
    base = _helper_child_env(ctx, env)
    base.pop(HELPER_DOOR_ENV, None)
    return base


def run_writer(name: str, args: Optional[Dict[str, Any]] = None, *,
               args_file: Optional[str] = None,
               timeout_s: int = DEFAULT_TIMEOUT_S,
               ctx: Optional[Dict[str, Any]] = None,
               env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """`{ok, result, rel?, stderr_tail, elapsed_ms}` - run ONE writer in place.

    THE WRITE DOOR (IDENT1 I-0; ruling R-M3-2). `run_helper` stays a read
    door; a document, a receipt, a close or a re-pin runs HERE, and passes
    these gates in order before anything is started:

      1. there is a workspace on this host and it is attached (`_preflight`:
         `container` refuses `no_workspace_on_this_host`);
      2. the name is `module:function` and on `RUN_WRITER_ALLOWLIST`
         (`not_allowed_writer` - a read helper's name is refused here, as a
         writer's name is refused by `run_helper`);
      3. the module is in the installed runtime (`not_in_manifest`);
      4. every path-shaped argument fences exactly as a `write` target would;
      5. the WRITER IS NAMED: `receipts.require_writer_identity`, asked of the
         environment the writer will run with. A merged seat's VM with no
         forwarded writer id, or a scheduled fire that is not the
         workspace's declared writer, refuses `writer_identity_required` with
         the one sentence in `line` - and nothing has run.

    `args` are bound by NAME onto the writer's own parameters. A payload too
    large or too quote-laden to survive a pasted command line (a brief's
    sections) travels as `args_file`: a JSON object in THIS SESSION'S OWN
    scratch, read here as the arguments and never written into the
    workspace. The envelope names a landed file by `rel`, never by the mount
    path.
    """
    started = time.time()
    ctx = resolve(env=env) if ctx is None else ctx
    refusal = _preflight("run_writer", ctx, started)
    if refusal is not None:
        return refusal
    if not isinstance(name, str) or name.count(":") != 1:
        return _refuse("run_writer", ctx, started, R_BAD_ARGS, name=name,
                       detail="name must be 'module:function'")
    module, function = name.split(":", 1)
    if not module or not function:
        return _refuse("run_writer", ctx, started, R_BAD_ARGS, name=name)
    if name not in RUN_WRITER_ALLOWLIST:
        return _refuse("run_writer", ctx, started, R_NOT_ALLOWED_WRITER, name=name)
    if not _module_in_manifest(module, ctx):
        return _refuse("run_writer", ctx, started, R_NOT_IN_MANIFEST, name=name)
    bound: Dict[str, Any] = {}
    if args_file:
        # CAP1 (D-T2B-7, fix round 1 N-1): one open, measured on the handle,
        # a bounded read; a missing file is its OSError name, as before.
        loaded, refused = _read_args_file(args_file)
        if refused is not None:
            return _refuse("run_writer", ctx, started, R_BAD_ARGS, name=name,
                           detail=refused)
        if not isinstance(loaded, dict):
            return _refuse("run_writer", ctx, started, R_BAD_ARGS, name=name,
                           detail="args_file must hold a JSON object")
        bound.update(loaded)
    bound.update(dict(args or {}))
    offender = fence_helper_args(Path(ctx["vm_path"]), bound)
    if offender is not None:
        return _refuse("run_writer", ctx, started, offender[1], name=name,
                       arg=offender[0])

    refusal = _device_origin_refusal("run_writer", ctx, started, env)
    if refusal is not None:
        refusal["name"] = name
        return refusal
    child_env = _writer_child_env(ctx, env)
    # THE WRITER IS NAMED BEFORE ANYTHING RUNS - asked of the environment the
    # writer itself will see, so the door and the writer cannot disagree.
    try:
        _ensure_scripts_on_path()
        import receipts as _receipts  # type: ignore
        _receipts.require_writer_identity(child_env,
                                          workspace_root=str(ctx["vm_path"]))
    except Exception as exc:  # noqa: BLE001 - ours is a refusal; any other is too
        if type(exc).__name__ == "WriterIdentityRequired":
            return _refuse("run_writer", ctx, started, R_WRITER_IDENTITY_REQUIRED,
                           name=name, line=getattr(exc, "line", ""),
                           cause=getattr(exc, "cause", ""))
        return _refuse("run_writer", ctx, started, R_HELPER_FAILED, name=name,
                       detail="identity check: " + type(exc).__name__)

    staged = ctx.get("staged_root")
    scripts = str(Path(staged) / "shared" / "scripts") if staged else str(_scripts_dir())
    payload = json.dumps({"scripts": scripts, "module": module,
                          "function": function, "args": bound})
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _HELPER_RUNNER],
            input=payload, capture_output=True, text=True,
            cwd=str(ctx["vm_path"]), env=child_env, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return _refuse("run_writer", ctx, started, R_TIMEOUT, name=name,
                       timeout_s=timeout_s)
    stdout = proc.stdout or ""
    stderr_out = proc.stderr or ""
    if SIDECAR_MARKER in stderr_out:
        head, _sep, raw = stderr_out.partition(SIDECAR_MARKER)
        _line, _nl, rest = raw.partition(chr(10))
        stderr_out = head + rest
    stderr_tail = stderr_out[-2000:]
    refused = _child_refusal(stdout)
    if refused is not None:
        return _refuse("run_writer", ctx, started,
                       refused.get("reason") or R_WRITER_IDENTITY_REQUIRED,
                       name=name, line=refused.get("line"),
                       cause=refused.get("cause"))
    result_marker = "\x00CR_RESULT\x00"
    if proc.returncode != 0 or result_marker not in stdout:
        return _refuse("run_writer", ctx, started, R_HELPER_FAILED, name=name,
                       stderr_tail=stderr_tail, returncode=proc.returncode)
    try:
        result = json.loads(stdout.split(result_marker, 1)[1])
    except ValueError:
        return _refuse("run_writer", ctx, started, R_HELPER_FAILED, name=name,
                       stderr_tail=stderr_tail)
    vm_path = Path(str(ctx["vm_path"])).resolve()
    shaped = _relativize(result, vm_path)
    extra: Dict[str, Any] = {}
    # A landed document is also SAID (IDENT1 I-2/I-3): the one sentence
    # `deliverables` composes for it - the path on the customer's own
    # computer in a code span when this run knows that spelling, the words
    # form when it does not. The caller prints it verbatim.
    if isinstance(shaped, str) and shaped.strip():
        # A writer that answers with a file (the brief writer answers with
        # where it saved) is named by that file's workspace-relative path,
        # whichever spelling it answered in - including the customer's own
        # computer's spelling, which the brief writer answers with whenever
        # the device path is forwarded (every merged seat; fix pass 1, H-1).
        shaped = _device_relative(shaped, ctx.get("device_path"))
        try:
            landed = (vm_path / shaped).resolve()
            if landed.is_file():
                extra["rel"] = landed.relative_to(vm_path).as_posix()
        except (OSError, ValueError):
            pass
    if extra.get("rel"):
        try:
            _ensure_scripts_on_path()
            import deliverables as _dl  # type: ignore

            root = ctx.get("device_path")
            device = _dl.device_join(root, extra["rel"]) if root else ""
            extra["opener_line"] = _dl.opener_line(device, extra["rel"])
        except Exception:  # noqa: BLE001 - a sentence never blocks a landing
            pass
    extra.update(_origin_fields(env))
    return _envelope("run_writer", ctx, started, ok=True, name=name,
                     result=shaped, stderr_tail=_relativize(stderr_tail, vm_path),
                     **extra)


def _device_relative(answer: str, device_path: Any) -> str:
    """`answer` spelled workspace-relative when it names a file under the
    customer's own computer's copy of the workspace (`device_path`, either
    slash, any case of the drive letter); any other answer unchanged.

    The door cannot `is_file` a device path from the VM, so without this a
    landed brief came back with no `rel` and no sentence, and the prep's
    receipt had no valid path to record (IDENT1 fix pass 1, H-1)."""
    dev = str(device_path or "").replace(chr(92), "/").rstrip("/")
    text = answer.strip().replace(chr(92), "/")
    if dev and text.lower().startswith(dev.lower() + "/"):
        return text[len(dev) + 1:]
    return answer


def _is_append_only(target_name: str) -> bool:
    return target_name == "events.jsonl" or bool(_EVENTS_SHARD_RE.match(target_name))


def write(rel: str, data, expected_mtime: Optional[int] = None, *,
          expected_rev: Optional[int] = None,
          create_parents: Optional[bool] = None,
          holder: str = "workspace_access",
          ctx: Optional[Dict[str, Any]] = None,
          env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Compare-and-swap one workspace file through the canonical writers.

    `events.jsonl` (and its year shards) are REFUSED: an append-only ledger is
    never whole-file written, and `append_jsonl` is the door. `entities.json` /
    `aliases.json` go through `atomic_write_json_locked`, with the mtime compare
    running INSIDE that lock on this host, so it is a real compare-and-swap
    rather than a narrowed window; `expected_rev` is the stronger guard there
    because the substrate's own version signals are `version`, `.rev` and
    `.seqhw` — never mtime. Everything else goes through `atomic_write_text`,
    with FOLDERGUARD's `create_parents=False` outside `_hq/` so a missing
    project folder is a loud refusal, not a fabricated directory.

    THIS VERB IS TEXT ONLY, ON PURPOSE, AND THERE IS NO SECOND WRITE DOOR
    (MF-M2-1, night M2 — decided by reading and by driving, and recorded here
    so the next reader does not re-open it). A payload that is not valid
    UTF-8 is refused `binary_unsupported`, and the CLI's `b64` argument is
    NOT an exception to that: it decodes at the door and the refusal below is
    what the decoded bytes meet. That is a documented one-way door, not a
    hole — `read` hands `b64` back because a reader must be able to see a
    file it cannot render, while a WRITE of bytes is a deliverable landing
    and belongs to the one place that knows what a deliverable is.

    That place is `deliverables.land`, and it is not a way around anything:
    it asks `_preflight` for both of its halves before it decides anything,
    then `fence_path`, `_is_append_only`, `REV_FILES` and
    `_create_parents_default` — every fence this verb applies, asked of this
    module, so a fence tightened here tightens there. Driven on the merged
    tree: a `.docx` lands in `vm` mode byte-identical, the ledger comes back
    `not_a_deliverable`, a `..` escape `refused_path`, a rev-file name
    `not_a_deliverable`, and a dropped grant `no_workspace_on_this_host`.
    Nothing in the instruction layer hands bytes to this CLI, so a
    `write_bytes` verb tonight would add a second write surface with no
    caller. M3 owns the question of whether the bytes door should MOVE here.
    """
    started = time.time()
    ctx = resolve(env=env) if ctx is None else ctx
    refusal = _preflight("write", ctx, started)
    if refusal is not None:
        return refusal
    # BRIEFDOOR1 MUST 5 -- the write door names its writer the way the append
    # door and the writer door do (a VM seat with no identity refuses).
    refusal = _device_origin_refusal("write", ctx, started, env)
    if refusal is not None:
        refusal["rel"] = rel
        return refusal
    target, reason = fence_path(Path(ctx["vm_path"]), rel)
    if reason is not None:
        return _refuse("write", ctx, started, reason, rel=rel)
    if _is_append_only(target.name):
        return _refuse("write", ctx, started, R_REFUSED_APPEND_ONLY, rel=rel,
                       next="append_jsonl")

    if isinstance(data, str):
        payload = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray)):
        payload = bytes(data)
    else:
        return _refuse("write", ctx, started, R_BAD_ARGS, rel=rel,
                       detail="data must be text or bytes")

    before = _mtime_ns(target)
    if target.name in REV_FILES:
        return _write_rev_file(rel, target, payload, expected_mtime, expected_rev,
                               holder, ctx, started, before)

    if expected_mtime is not None and before != int(expected_mtime):
        return _refuse("write", ctx, started, R_STALE_READ, rel=rel,
                       current_mtime_ns=before)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return _refuse("write", ctx, started, R_BINARY_UNSUPPORTED, rel=rel)
    parents = _create_parents_default(rel) if create_parents is None else bool(create_parents)
    try:
        _ensure_scripts_on_path()
        from atomic_write import atomic_write_text  # type: ignore
        atomic_write_text(target, text, create_parents=parents)
    except FileNotFoundError:
        return _refuse("write", ctx, started, R_MISSING_PARENT, rel=rel)
    except OSError as exc:
        return _refuse("write", ctx, started, "write_failed", rel=rel, detail=str(exc))
    # IDENT1 I-6 (the re-walk's 0-byte page). `atomic_write_text` answered, so
    # this verb used to answer ok:true - and on the merged mount a NEW file
    # landed EMPTY (5,091 bytes intended, 0 on the PC). The bytes are read
    # back; a mismatch gets ONE direct write (no rename - the step the mount
    # mishandles) and a second read; still wrong is `write_unverified`, never
    # a claimed success.
    found = _verify_bytes(target, payload)
    if found != len(payload) or not _same_bytes(target, payload):
        try:
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            return _refuse("write", ctx, started, R_WRITE_UNVERIFIED, rel=rel,
                           bytes_expected=len(payload), bytes_found=found,
                           detail=str(exc))
        found = _verify_bytes(target, payload)
        if found != len(payload) or not _same_bytes(target, payload):
            return _refuse("write", ctx, started, R_WRITE_UNVERIFIED, rel=rel,
                           bytes_expected=len(payload), bytes_found=found)
    return _envelope("write", ctx, started, ok=True, rel=rel,
                     mtime_ns_before=before, mtime_ns_after=_mtime_ns(target),
                     rev=None, bytes_written=len(payload), verified=True,
                     **_origin_fields(env))


def _verify_bytes(target: Path, payload: bytes) -> int:
    """How many bytes are actually at `target` now (-1 when unreadable)."""
    try:
        return len(Path(target).read_bytes())
    except OSError:
        return -1


def _same_bytes(target: Path, payload: bytes) -> bool:
    try:
        return hashlib.sha256(Path(target).read_bytes()).hexdigest() == \
            hashlib.sha256(payload).hexdigest()
    except OSError:
        return False


def _create_parents_default(rel: str) -> bool:
    """FOLDERGUARD by location: `_hq/` self-creates, a project folder does not."""
    posix = Path(rel).as_posix()
    return posix == "_hq" or posix.startswith("_hq/")


def _mtime_ns(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _write_rev_file(rel: str, target: Path, payload: bytes,
                    expected_mtime: Optional[int], expected_rev: Optional[int],
                    holder: str, ctx: Dict[str, Any], started: float,
                    before: Optional[int]) -> Dict[str, Any]:
    """entities.json / aliases.json — the compare runs inside the sentinel lock."""
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return _refuse("write", ctx, started, R_INVALID_JSON, rel=rel, detail=str(exc))

    def precheck(path: Path) -> None:
        current = _mtime_ns(path)
        if expected_mtime is not None and current != int(expected_mtime):
            raise StaleRead(str(current))
        if expected_rev is not None and _read_rev(path) != int(expected_rev):
            raise StaleRead(str(current))

    try:
        _ensure_scripts_on_path()
        from atomic_write import atomic_write_json_locked  # type: ignore
        atomic_write_json_locked(target, obj, holder=holder, precheck=precheck)
    except StaleRead:
        return _refuse("write", ctx, started, R_STALE_READ, rel=rel,
                       current_mtime_ns=_mtime_ns(target), rev=_read_rev(target))
    except TimeoutError:
        return _refuse("write", ctx, started, "lock_timeout", rel=rel)
    except OSError as exc:
        return _refuse("write", ctx, started, "write_failed", rel=rel, detail=str(exc))
    # IDENT1 I-6: the locked JSON writer serialises the object itself, so the
    # check is that what landed PARSES back to the same object - an empty or
    # truncated file on the mount is `write_unverified`, never ok:true.
    try:
        landed = json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        landed = None
    if landed != obj:
        return _refuse("write", ctx, started, R_WRITE_UNVERIFIED, rel=rel,
                       bytes_found=_verify_bytes(target, payload))
    return _envelope("write", ctx, started, ok=True, rel=rel,
                     mtime_ns_before=before, mtime_ns_after=_mtime_ns(target),
                     rev=_read_rev(target), verified=True)


def _read_rev(path: Path) -> Optional[int]:
    try:
        raw = json.loads(path.with_name(path.name + ".rev").read_text(encoding="utf-8"))
        value = raw.get("rev")
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    except Exception:
        return None


def append_jsonl(rel: str, rows: Iterable[Dict[str, Any]], *,
                 holder: str = "workspace_access",
                 ctx: Optional[Dict[str, Any]] = None,
                 env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """`{ok, n, stamped}` — the ONLY append door, routed through the gate.

    No mtime guard, by design: an append-only ledger's mtime changes on every
    legitimate append, so an mtime compare-and-swap would refuse every contended
    write. The guards are the writer lock, `.seqhw` and FS-04 — all of which run
    unchanged, in one process, on the live file, because the runtime is beside
    the data rather than a continent away.
    """
    started = time.time()
    ctx = resolve(env=env) if ctx is None else ctx
    refusal = _preflight("append_jsonl", ctx, started)
    if refusal is not None:
        return refusal
    refusal = _device_origin_refusal("append_jsonl", ctx, started, env)
    if refusal is not None:
        return refusal
    target, reason = fence_path(Path(ctx["vm_path"]), rel)
    if reason is not None:
        return _refuse("append_jsonl", ctx, started, reason, rel=rel)
    if not (target.name in REGISTERED_JSONL or _EVENTS_SHARD_RE.match(target.name)):
        return _refuse("append_jsonl", ctx, started, R_NOT_REGISTERED, rel=rel)
    rows = list(rows or [])
    if not all(isinstance(r, dict) for r in rows):
        return _refuse("append_jsonl", ctx, started, R_BAD_ARGS, rel=rel,
                       detail="rows must be a list of objects")
    try:
        _ensure_scripts_on_path()
        if _is_append_only(target.name):
            from event_gate import append_event  # type: ignore
            stamped = append_event(target, rows, holder=holder)
        else:
            from atomic_write import atomic_append_jsonl  # type: ignore
            stamped = atomic_append_jsonl(target, rows, holder=holder)
    except Exception as exc:  # the gate's refusals are the caller's answer
        typed = getattr(exc, "cr_refusal_reason", None)
        if typed:
            # A typed refusal (LeaseLost -> `lease_lost` + LEASE_LOST_LINE) answers by its
            # own reason and pinned line, the way run_helper / run_writer already do
            # (REVIEW_T2_LEASE2 N-2). Nothing was appended when it is said.
            return _refuse("append_jsonl", ctx, started, str(typed), rel=rel,
                           detail=str(getattr(exc, "line", "") or f"{type(exc).__name__}: {exc}"))
        return _refuse("append_jsonl", ctx, started, "append_refused", rel=rel,
                       detail=f"{type(exc).__name__}: {exc}")
    return _envelope("append_jsonl", ctx, started, ok=True, rel=rel,
                     n=len(stamped), stamped=stamped, **_origin_fields(env))


# ---------------------------------------------------------------------------
# Container face — install / expect / verify / plan / discover
# ---------------------------------------------------------------------------

_REFERENCE_RE = re.compile(r"references[/\"'][\s,/]*[\"']?([A-Za-z0-9_.\-]+\.(?:md|html|json|txt))")


#: Files no chat removes, ever — the substrate itself and the machinery that
#: keeps it honest. A `rm` that takes the ledger is not recoverable from a
#: `.stale.<epoch>` rename somebody has to know to look for, so this door
#: refuses outright rather than moving them aside. The runtime cache and the
#: writer lock are refused one fence earlier, by `fence_path`; `remove` reports
#: those under this same reason, because from the caller's side they are the
#: same answer: not this file, not by this door.
PROTECTED_SUFFIXES = (".seqhw", ".source_refs.idx", ".lock")
PROTECTED_NAMES = frozenset({"entities.json", "aliases.json",
                             "events.jsonl", "processed-meetings.json"})


def _is_protected(target: Path) -> bool:
    name = target.name
    if name in PROTECTED_NAMES or _EVENTS_SHARD_RE.match(name):
        return True
    if name.startswith(".writer.lock"):
        return True
    return any(name.endswith(suffix) for suffix in PROTECTED_SUFFIXES)


def remove(rel: str, *, holder: str = "workspace_access",
           ctx: Optional[Dict[str, Any]] = None,
           env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Remove one workspace file, and SAY WHAT ACTUALLY HAPPENED.

    The fifth host-face verb (FIX3 F3-12, ruling R-RW-6). Before it, a chat
    that needed a file gone under `_hq/` had one honest option — leave it —
    and one dishonest one: `rm` in a device shell, which on a merged seat
    reaches a different filesystem than the one holding the workspace and
    reports success either way. The walk left three staging files behind
    exactly that way.

    Fenced like `write`: inside the workspace, never the runtime cache, never
    the writer lock, and a protected set (`PROTECTED_NAMES` + the year shards
    + `.seqhw` / `.source_refs.idx` / lock files) refused outright as
    `refused_protected`. In `container` mode there is no workspace on this
    host at all, so the answer is `no_workspace_on_this_host` — never a
    delete against whatever the container happens to have mounted.

    THE ENVELOPE IS VERIFIED, WHICH IS THE WHOLE POINT. `removed: true` is
    written only after `exists()` says the path is gone. A mount that refuses
    the delete gets DEL1's refuse-then-move idiom (`.stale.<epoch>.<pid>`) and
    the envelope says `moved_to`; a mount that refuses the rename too says
    `left_in_place: true` and stays `ok: true`, because an honest answer IS
    the answer — the caller then tells the CEO the file is still there. This
    verb never raises: it is called mid-operation, and a delete that kills the
    operation after its real work landed is the bug DEL1 exists to remove.
    """
    started = time.time()
    ctx = resolve(env=env) if ctx is None else ctx
    refusal = _preflight("remove", ctx, started)
    if refusal is not None:
        return refusal
    target, reason = fence_path(Path(ctx["vm_path"]), rel)
    if reason == R_REFUSED_PATH:
        return _refuse("remove", ctx, started, R_REFUSED_PATH, rel=rel)
    if reason is not None:
        # The runtime cache and the lock: refused one fence earlier, reported
        # here under the name the caller's own rule uses.
        return _refuse("remove", ctx, started, R_REFUSED_PROTECTED, rel=rel,
                       detail=reason)
    if _is_protected(target):
        return _refuse("remove", ctx, started, R_REFUSED_PROTECTED, rel=rel)

    existed = target.exists()
    moved_to = None
    left_in_place = False
    detail = None
    try:
        os.unlink(str(target))
    except FileNotFoundError:
        detail = "already gone"
    except OSError as first:
        # DEL1's idiom, asked of the module that owns it rather than
        # re-spelled here: try once more, then rename aside, never raise.
        try:
            _ensure_scripts_on_path()
            from delete_grant import remove_or_move_aside  # type: ignore

            out = remove_or_move_aside(target, "chat remove: " + str(rel))
        except Exception as exc:  # noqa: BLE001 — never raises, by contract
            out = {"removed": False, "moved_to": None, "left_in_place": True,
                   "reason": "{0}: {1}".format(type(exc).__name__, exc)}
        moved_to = out.get("moved_to")
        left_in_place = bool(out.get("left_in_place"))
        detail = out.get("reason") or str(first)

    # VERIFIED. Not "the call returned", not "no exception": the path is gone.
    gone = not target.exists()
    removed = gone and not moved_to
    if not gone and not moved_to:
        left_in_place = True
    return _envelope("remove", ctx, started, ok=True, rel=rel,
                     existed=existed, removed=removed,
                     moved_to=str(moved_to) if moved_to else None,
                     left_in_place=bool(left_in_place and not removed),
                     detail=detail)


def _iter_runtime_scripts(root: Path) -> List[Path]:
    out = []
    for path in sorted((root / "shared" / "scripts").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append(path)
    return out


def referenced_reference_files(root: Optional[Path] = None) -> List[str]:
    """Every `references/**` file a shared script NAMES, as workspace-relative
    paths — the static scan the runtime manifest ships from.

    By NAME rather than by proven open: the scan is a deliberate superset, and
    the cost of shipping a named-but-unopened plugin document is a few KB while
    the cost of missing an opened one is a helper that dies in the VM with no
    network to fetch it. `skills/**/references/` never ships — instructions stay
    where the model reads them (PLUGIN_BOUNDARY:17-24).
    """
    root = plugin_root() if root is None else Path(root)
    ref_dir = root / "references"
    if not ref_dir.is_dir():
        return []
    available = {p.name for p in ref_dir.iterdir() if p.is_file()}
    named = set()
    for path in _iter_runtime_scripts(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _REFERENCE_RE.finditer(text):
            if match.group(1) in available:
                named.add(match.group(1))
    return sorted("references/" + name for name in named)


#: A shipped script naming a directory or a file BESIDE its own package —
#: `_HERE.parent / "templates" / "premium_brief.html"`, `_HERE.parent /
#: "exemplars"`, `Path(__file__).resolve().parent.parent / "config" /
#: "env_support.json"`. `.parent` off `shared/scripts/` IS `shared/`, so every
#: quoted segment after it spells a path inside `shared/`.
_SHARED_ASSET_RE = re.compile(
    r"""(?:_HERE|ROOT|Path\(__file__\)\.resolve\(\)\.parent)\.parent\s*"""
    r"""((?:/\s*["'][^"'\n]+["']\s*)+)"""
)
_ASSET_SEGMENT_RE = re.compile(r"""["']([^"'\n]+)["']""")

#: Directories under `shared/` that the manifest already ships whole; naming
#: one again would be harmless but noisy.
_ALREADY_SHIPPED = ("scripts", "data-schemas")


def referenced_shared_assets(root: Optional[Path] = None) -> List[str]:
    """Every non-`.py` file under `shared/` that a SHIPPED script opens.

    DELIV1 review F-3: the manifest shipped `shared/scripts/**` and
    `shared/data-schemas/**` and nothing else under `shared/`, so
    `premium_html.TEMPLATE_PATH` — `shared/templates/premium_brief.html` —
    was absent from every installed runtime, and the `.html` composer died with
    `FileNotFoundError` on a merged seat while its `.docx` twin worked. It is
    not only templates: `exemplars.SEED_ROOT`, `infographic._TEMPLATE_DIR`,
    `lexicon._LEXICON_DIR` and four `shared/config/*.json` readers were in the
    same hole. The VM has no network, so a file that does not ship cannot be
    fetched later — an absent asset is a dead backend, not a slow one.

    DERIVED, never hand-listed: a static scan for the one spelling a shipped
    script uses to reach out of its own package — `.parent / "<segment>" ...`
    off the scripts directory — resolved against the tree. A named FILE ships;
    a named DIRECTORY ships every non-`.py` file under it. Like
    `referenced_reference_files` this is a deliberate superset: the cost of
    shipping a named-but-unopened asset is a few KB, the cost of missing an
    opened one is a backend that cannot run where there is no network.
    """
    root = plugin_root() if root is None else Path(root)
    shared = root / "shared"
    if not shared.is_dir():
        return []
    named: set = set()
    for path in _iter_runtime_scripts(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _SHARED_ASSET_RE.finditer(text):
            segments = _ASSET_SEGMENT_RE.findall(match.group(1))
            if not segments or segments[0] in _ALREADY_SHIPPED:
                continue
            if any("/" in seg or "\\" in seg or seg in ("..", ".")
                   for seg in segments):
                continue
            candidate = shared.joinpath(*segments)
            try:
                inside = candidate.resolve().relative_to(shared.resolve())
            except (OSError, ValueError):
                continue
            if not str(inside):
                continue
            if candidate.is_file():
                if candidate.suffix != ".py":
                    named.add(candidate.relative_to(root).as_posix())
            elif candidate.is_dir():
                for child in sorted(candidate.rglob("*")):
                    if (child.is_file() and child.suffix != ".py"
                            and "__pycache__" not in child.parts):
                        named.add(child.relative_to(root).as_posix())
    return sorted(named)


def runtime_file_set(root: Optional[Path] = None) -> List[str]:
    """Every file the runtime ships, as plugin-relative posix paths.

    `shared/scripts/**`, `shared/data-schemas/**`, `shared/templates/**`, the
    other `shared/**` assets a shipped script opens (`referenced_shared_assets`
    — DELIV1 review F-3), the named `references/**` files, and
    `.claude-plugin/plugin.json`. NOT `skills/` — ever.
    """
    root = plugin_root() if root is None else Path(root)
    rels: List[str] = []
    for path in _iter_runtime_scripts(root):
        rels.append(path.relative_to(root).as_posix())
    for sub in ("data-schemas", "templates"):
        asset_dir = root / "shared" / sub
        if asset_dir.is_dir():
            for path in sorted(asset_dir.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    rels.append(path.relative_to(root).as_posix())
    rels.extend(referenced_shared_assets(root))
    rels.extend(referenced_reference_files(root))
    plugin_json = root / ".claude-plugin" / "plugin.json"
    if plugin_json.is_file():
        rels.append(".claude-plugin/plugin.json")
    return sorted(set(rels))


def plugin_version(root: Optional[Path] = None) -> Optional[str]:
    root = plugin_root() if root is None else Path(root)
    try:
        payload = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    version = payload.get("version")
    return version if isinstance(version, str) else None


def build_manifest(root: Optional[Path] = None) -> Dict[str, Any]:
    """`{plugin_version, source_commit, built_at, files:{rel: sha256}}`."""
    root = plugin_root() if root is None else Path(root)
    files = {}
    for rel in runtime_file_set(root):
        files[rel] = _sha256_file(root / rel)
    return {
        "plugin_version": plugin_version(root),
        "source_commit": _source_commit(root),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files,
    }


def manifest_sha(manifest: Dict[str, Any]) -> str:
    """A stable digest of the manifest's FILE SET, not of the manifest document.

    `built_at` changes on every build; the thing `expect` and `discover` compare
    must not. So the digest is over the sorted `rel:sha256` pairs alone.
    """
    body = "\n".join(f"{rel}:{sha}" for rel, sha in sorted((manifest.get("files") or {}).items()))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _source_commit(root: Path) -> Optional[str]:
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    out = (proc.stdout or "").strip()
    return out if proc.returncode == 0 and out else None


def install(out_dir: Path, root: Optional[Path] = None,
            ws: Optional[str] = None) -> Dict[str, Any]:
    """Build ONE zip holding the runtime plus its manifest; return the plan.

    One zip rather than N files because `device_commit_files` caps at 50 files
    per call and the runtime is 270+; `unzip` is present in the VM.
    """
    root = plugin_root() if root is None else Path(root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(root)
    version = manifest.get("plugin_version") or "unknown"
    zip_name = f"cr-runtime-{version}.zip"
    zip_path = out_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(manifest["files"]):
            zf.write(root / rel, rel)
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True))
    return {
        "zip": str(zip_path),
        "zip_name": zip_name,
        "version": version,
        "manifest_sha": manifest_sha(manifest),
        "file_count": len(manifest["files"]),
        "device_commit_files": [{
            "stagedPath": str(zip_path),
            # `install` runs in the container, which has no workspace — so the
            # target carries the WS the model already holds from `discover`.
            "devicePath": f"{ws or '$WS'}/{RUNTIME_CACHE_REL}/{zip_name}",
        }],
    }


def expect(root: Optional[Path] = None) -> Dict[str, Any]:
    manifest = build_manifest(root)
    return {
        "plugin_version": manifest.get("plugin_version"),
        "manifest_sha": manifest_sha(manifest),
        "file_count": len(manifest.get("files") or {}),
    }


def verify_stage(stage: Path) -> Dict[str, Any]:
    """Recompute every sha in a staged runtime; write `current.json` on a FULL
    match and only then.

    A partial match is a modified runtime, which is a foreign write into plugin
    code — `PLUGIN_BOUNDARY.md:140` ("Customer → Plugin: NEVER") forbids it, and
    a half-verified runtime that runs is worse than one that refuses.
    """
    stage = Path(stage).resolve()
    try:
        manifest = json.loads((stage / MANIFEST_NAME).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": "manifest_unreadable", "detail": str(exc),
                "stage": str(stage)}
    mismatched: List[str] = []
    missing: List[str] = []
    for rel, sha in sorted((manifest.get("files") or {}).items()):
        path = stage / rel
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256_file(path) != sha:
            mismatched.append(rel)
    version = manifest.get("plugin_version") or stage.name
    sha = manifest_sha(manifest)
    if missing or mismatched:
        return {"ok": False, "reason": "manifest_mismatch", "stage": str(stage),
                "version": version, "manifest_sha": sha,
                "missing": missing[:10], "mismatched": mismatched[:10],
                "missing_count": len(missing), "mismatched_count": len(mismatched),
                "current_written": False}
    pointer = stage.parent / CURRENT_POINTER
    record: Dict[str, Any] = {"version": version, "manifest_sha": sha}
    if stage.name != version:
        # IDENT1 I-7: a suffixed stage (`<version>-<sha8>`) is named, so every
        # reader follows the pointer to it; a bare stage writes today's bytes.
        record["dir"] = stage.name
    try:
        pointer.write_text(json.dumps(record) + "\n", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "reason": "pointer_write_failed", "detail": str(exc),
                "stage": str(stage), "version": version, "manifest_sha": sha,
                "current_written": False}
    return {"ok": True, "stage": str(stage), "version": version,
            "manifest_sha": sha, "file_count": len(manifest.get("files") or {}),
            "current_written": True}


def verify_command(ctx: Dict[str, Any], version: Optional[str] = None,
                   zip_name: Optional[str] = None,
                   manifest_sha: Optional[str] = None) -> str:
    """The one-liner the model pastes into the device shell after committing the
    zip. Unzips, then hands verification to the runtime's own code — no `rm`,
    no `unlink`, nothing that a no-delete mount would refuse.

    IT NEVER OVERWRITES (IDENT1 I-7, ruling R-M3-7). On 2026-09-22 `unzip -o`
    into the existing version directory printed "cannot delete old ...
    Operation not permitted" for every file on a mount that cannot unlink,
    and the chat hand-copied 31 files instead. So the unzip goes into a
    FRESH directory: the bare `<version>` when it holds no runtime yet, else
    `<version>-<manifest sha[:8]>`; `-n` never replaces a file (a re-run of
    the same stage skips what is there and `verify` re-checks every sha).
    `verify --stage` writes `current.json` naming the directory it verified,
    and every reader follows the pointer. Where this process can see the
    cache the directory is decided here; where it cannot (the container)
    the line decides in the shell."""
    version = version or plugin_version() or "unknown"
    zip_name = zip_name or f"cr-runtime-{version}.zip"
    if not manifest_sha:
        try:
            manifest_sha = expect().get("manifest_sha")
        except Exception:  # noqa: BLE001 - a missing digest names no suffix
            manifest_sha = None
    suffixed = f"{version}-{str(manifest_sha or 'stage')[:8]}"
    ws = ctx.get("vm_path")
    tail = ('unzip -n -q "{zip}" -d "{d}" && '
            'python3 "{d}/shared/scripts/workspace_access.py" verify --stage "{d}"')
    if ws and Path(str(ws)).is_dir():
        cache_dir = Path(str(ws)) / RUNTIME_CACHE_REL
        d = suffixed if (cache_dir / version / MANIFEST_NAME).exists() else version
        return (f'cd "{ws}/{RUNTIME_CACHE_REL}" && '
                + tail.format(zip=zip_name, d=d))
    ws = ws or '$HOME/mnt/<workspace basename>'
    cache = f'"{ws}/{RUNTIME_CACHE_REL}"'
    return (f'cd {cache} && {{ d="{version}"; [ -e "$d/{MANIFEST_NAME}" ] && '
            f'd="{suffixed}"; ' + tail.format(zip=zip_name, d="$d") + "; }")


#: A forwarded value may be pasted unquoted when it is made of these. Anything
#: else is double-quoted; anything shell-active is refused outright.
_PLAIN_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/+-]+$")
#: Characters that can end the quoting or start a substitution. A workspace
#: path, a derived id, a run mode: none of them legitimately contains one, so a
#: value that does is a mangled paste or an injection and is refused, never
#: escaped. (Escaping is a second contract to get right; refusing is one.)
_UNSAFE_VALUE_CHARS = ('"', "'", "`", "$", "\\", "\n", "\r", "\x00")


class ForwardRefused(ValueError):
    """A forwarded env value could not be rendered safely — `bad_args`."""

    def __init__(self, key: str, detail: str):
        super().__init__(f"{key}: {detail}")
        self.key = key
        self.detail = detail


def _derived_writer_pair(ctx: Dict[str, Any],
                         env: Dict[str, str]) -> Dict[str, str]:
    """`{CR_WRITER_ID, CR_WRITER_DERIVATION}` derived HERE, or `{}`.

    The container is the process that holds `CLAUDE_CODE_ACCOUNT_UUID`; the
    helper child runs on the device host and has never seen it (breakdown §A.1
    row 105), which is why every receipt the gate walk wrote carried a session
    token instead of the writer id. So the derivation happens in this process
    and only its OUTPUT crosses: the `acct-…` id and a digest of the inputs.
    The uuid itself is never rendered into a command, a transcript or a file
    (ruling R-FIX-5).

    The basename is the workspace's, wherever this process can see it: the
    resolved context first, then the device path the preamble exported, then
    `CR_WORKSPACE`. It must match what `writer_id()` derives beside the data,
    and all three name the same folder.
    """
    account = str(env.get("CLAUDE_CODE_ACCOUNT_UUID", "")).strip()
    if not account:
        return {}
    basename = str(ctx.get("basename") or "").strip()
    if not basename:
        for key in ("CR_DEVICE_WORKSPACE", "CR_WORKSPACE"):
            raw = str(env.get(key, "")).strip().rstrip("/\\")
            if raw:
                basename = PurePosixPath(raw.replace("\\", "/")).name
                break
    if not basename:
        return {}
    try:
        _ensure_scripts_on_path()
        import writer_identity  # type: ignore

        if writer_identity.detected_mode(env) in writer_identity.LEGACY_ID_MODES:
            # A seat that keeps today's identities byte for byte forwards
            # nothing — the same fence `writer_id()` applies at the far end.
            return {}
        org = str(env.get("CLAUDE_CODE_ORGANIZATION_UUID", "")).strip() or None
        return {
            "CR_WRITER_ID": writer_identity.derive(account, basename, org),
            "CR_WRITER_DERIVATION": writer_identity.derivation_digest(
                account, basename, org),
        }
    except Exception:  # noqa: BLE001 — identity never blocks a rendered command
        return {}


def _handed_writer_pair(env: Dict[str, str]) -> Dict[str, str]:
    """The shape-checked pair in this environment, or `{}`."""
    try:
        _ensure_scripts_on_path()
        import writer_identity  # type: ignore

        if writer_identity.detected_mode(env) in writer_identity.LEGACY_ID_MODES:
            return {}
        pair = writer_identity.forwarded_pair(env)
    except Exception:  # noqa: BLE001 - identity never blocks a rendered command
        return {}
    if not pair:
        return {}
    return {"CR_WRITER_ID": pair[0], "CR_WRITER_DERIVATION": pair[1]}


def plan_origin(forward: Dict[str, str]) -> Optional[str]:
    """`device` when this file is the staged runtime, `container` when it is a
    plugin root and an identity crosses with the line, else None (a legacy or
    local seat's line is exactly today's)."""
    if _running_from_runtime_cache() is not None:
        return PLAN_ORIGIN_DEVICE
    if forward.get("CR_WRITER_ID"):
        return PLAN_ORIGIN_CONTAINER
    return None


def _origin_fields(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """`{plan_origin, identity_forwarded}` for a door's envelope - only when
    the line was rendered by a `plan` that stamped its origin, so a legacy
    seat's envelope keeps today's shape."""
    source = dict(os.environ if env is None else env)
    origin = str(source.get(PLAN_ORIGIN_ENV, "") or "").strip()
    if not origin:
        return {}
    try:
        _ensure_scripts_on_path()
        import writer_identity  # type: ignore

        forwarded = writer_identity.forwarded_pair(source) is not None
    except Exception:  # noqa: BLE001
        forwarded = False
    return {"plan_origin": origin, "identity_forwarded": forwarded}


def _identity_available(env: Optional[Dict[str, str]] = None) -> bool:
    """Can a writer on this host be NAMED at all? A shape-checked forwarded
    pair (what a container-rendered line carries), or an account uuid this
    process could derive one from. Nothing else counts: a VM seat never
    reads its identity off the synced file (D-1)."""
    source = dict(os.environ if env is None else env)
    if str(source.get("CLAUDE_CODE_ACCOUNT_UUID", "") or "").strip():
        return True
    try:
        _ensure_scripts_on_path()
        import writer_identity  # type: ignore

        return writer_identity.forwarded_pair(source) is not None
    except Exception:  # noqa: BLE001 - an unreadable pair is no pair
        return False


def _device_origin_refusal(verb: str, ctx: Dict[str, Any], started: float,
                           env: Optional[Dict[str, str]] = None
                           ) -> Optional[Dict[str, Any]]:
    """A WRITE on a VM seat with no identity, refused by name - the run log
    then says WHY there is no writer (Probe B defect 1).

    WIDENED by BRIEFDOOR1 MUST 5 (ruling R-RW3-8). It used to refuse only a
    line stamped `CR_PLAN_ORIGIN=device`; a hand-typed line with no origin
    stamp and no pair walked through it and wrote under whatever the child
    could mint. Now a VM seat with NO identity available at all refuses for
    `write`, `append_jsonl` and `run_writer`, whatever the origin stamp says.
    Legacy and local seats are not `vm` mode: unchanged."""
    fields = _origin_fields(env)
    if ctx.get("mode") != VM:
        return None
    if fields.get("plan_origin") != PLAN_ORIGIN_DEVICE or fields.get(
            "identity_forwarded"):
        if _identity_available(env):
            return None
    try:
        _ensure_scripts_on_path()
        import receipts as _receipts  # type: ignore

        line = _receipts.WRITER_IDENTITY_REQUIRED_LINE
    except Exception:  # noqa: BLE001
        line = ""
    return _refuse(verb, ctx, started, R_WRITER_IDENTITY_REQUIRED, line=line,
                   hint=RENDER_IN_CONTAINER_HINT, **fields)


def forward_env(ctx: Optional[Dict[str, Any]] = None,
                env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The variables that cross the door with a rendered command, in order.

    Two derived here (the writer pair) and two passed through when the
    container's environment carries them: `CR_FIRED_VIA`, so the helper child
    can tell a scheduled fire from a typed one without guessing (F2-4), and
    `CR_DEVICE_WORKSPACE`, so a landed file can name its path on the
    customer's own computer instead of a breadcrumb (F2-6).
    """
    ctx = resolve() if ctx is None else ctx
    env = dict(os.environ) if env is None else dict(env)
    out: Dict[str, str] = dict(_derived_writer_pair(ctx, env))
    if not out:
        # IDENT1 I-11 / I-9: no account here to derive from, but the fire was
        # HANDED a pair (the one the registering seat baked into its prompt,
        # exported in this shell). It crosses exactly as a derived one would;
        # a seat that keeps today's identities forwards nothing.
        out.update(_handed_writer_pair(env))
    for key in ("CR_FIRED_VIA", "CR_DEVICE_WORKSPACE", "CR_TRIGGERED_BY"):
        value = str(env.get(key, "")).strip()
        if not value:
            continue
        if key == DEVICE_WORKSPACE_ENV:
            # A WINDOWS PATH IS THE ORDINARY CASE (re-verifier B-1). The
            # preamble and the bootloader both tell the model to export this
            # value exactly as `get_device_info` spells it, and on Windows
            # that is `C:\\Users\\...\\<Workspace>`. The renderer below
            # refuses a backslash outright, so without this line the first
            # `plan` on a Windows merged seat answers `bad_args` and rule 3
            # stops the whole seat. Forward slashes are also what both
            # consumers want: `deliverables.device_join` forward-slashes its
            # base anyway, and `brief_path.get_brief_artifact_url` re-spells
            # the drive letter with backslashes when it builds the opener.
            # The same judgement `_derived_writer_pair` already makes one
            # function away about the basename. Quotes, newlines and shell
            # expansions are still refused.
            value = value.replace(chr(92), "/")
        out[key] = value
    return {k: out[k] for k in FORWARD_ENV_KEYS if k in out}


def render_env_prefix(forward: Dict[str, str]) -> str:
    """`KEY=value KEY="value with a space" ` — or `""` when nothing crosses."""
    parts = []
    for key in FORWARD_ENV_KEYS:
        if key not in forward:
            continue
        value = str(forward[key])
        bad = [c for c in _UNSAFE_VALUE_CHARS if c in value]
        if bad:
            raise ForwardRefused(key, "value carries a quote, a backslash, a "
                                      "newline or a shell expansion")
        parts.append(f"{key}={value}" if _PLAIN_VALUE_RE.match(value)
                     else f'{key}="{value}"')
    return (" ".join(parts) + " ") if parts else ""


def plan(verb: str, args_json: str, ctx: Optional[Dict[str, Any]] = None,
         env: Optional[Dict[str, str]] = None) -> str:
    """The exact command string for one verb — pasted verbatim, never retyped.

    It may now begin with an env prefix (`forward_env`). The preamble's rule 3
    says "paste what it prints, verbatim — including the variables in front of
    `python3`", because those variables ARE the identity and the run mode
    crossing to the host that holds the data.
    """
    ctx = resolve() if ctx is None else ctx
    # MF-17: in the container the ctx carries no staged root (there is no mount
    # to resolve it from); the discovery JSON does, and the bootloader exports it
    # as CR_STAGED_ROOT beside CR_DEVICE_WORKSPACE. `$RT` survives only for the
    # in-shell (legacy / local) render, where Step 1 defined it.
    staged = (ctx.get("staged_root")
              or (env if env is not None else os.environ).get("CR_STAGED_ROOT")
              or "$RT")
    compact = args_json
    try:
        compact = json.dumps(json.loads(args_json), sort_keys=True)
    except Exception:
        pass
    forward = forward_env(ctx, env)
    prefix = render_env_prefix(forward)
    origin = plan_origin(forward)
    if origin:
        prefix = f"{PLAN_ORIGIN_ENV}={origin} " + prefix
    # The WHOLE path is inside one pair of double quotes: a workspace basename
    # with a space in it is the ordinary case, not the exotic one.
    # INBOXDRIVE1 review F-3: the payload rides in single quotes, and a mail
    # body or a draft with an apostrophe ("I'll") closed them early - the
    # pasted line answered `bad_args` or died in the shell. POSIX escape: end
    # the quote, an escaped quote, reopen (`'\''`).
    quoted = compact.replace("'", "'\\''")
    return (f'{prefix}python3 "{staged}/shared/scripts/workspace_access.py" {verb} '
            f"--json '{quoted}'")


#: BRIDGE3 (Train 2b §5 MUST 2, F-T2-9) - the keys a runtime check adds to a
#: `discover` answer. Never part of `DISCOVER_KEYS`: the plain answer (and
#: the block the bootloader carries) keeps its ten keys.
RUNTIME_CHECK_KEYS = ("plugin_manifest_sha", "runtime_lags", "lags_line")

#: BRIDGE3 (Train 2b §5 MUST 2, F-T2-9) - the ONE sentence a seat whose
#: runtime beside the folder lags this plugin says, after one restage failed
#: to bring it current. No sha, no path; the phrase to type is the update's.
RUNTIME_LAGS_LINE = (
    "Command Room in your folder is older than the version your Claude app "
    "has, so nothing was set up. Say `update command room` first, then ask "
    "again."
)


def runtime_lags(answer: Optional[Dict[str, Any]], plugin_sha: Optional[str]) -> bool:
    """True when the runtime a `discover` answer names is absent, or its
    `manifest_sha` is not `plugin_sha` (this plugin's own `expect` value)."""
    answer = answer if isinstance(answer, dict) else {}
    if not answer.get("runtime_present"):
        return True
    if not plugin_sha or not answer.get("manifest_sha"):
        return True  # REVIEW_T2B_BRIDGE3 N-3: nothing to compare is never current
    return answer.get("manifest_sha") != plugin_sha


def runtime_check(answer: Optional[Dict[str, Any]],
                  root: Optional[Path] = None) -> Dict[str, Any]:
    """`answer` plus `RUNTIME_CHECK_KEYS`: this plugin's own `expect`
    manifest sha, whether the runtime `answer` names lags it, and the one
    sentence for a seat that still lags after a restage. `answer` is a
    `discover` answer from either face (the block's JSON or `discover(ctx)`)."""
    out = dict(answer if isinstance(answer, dict) else {})
    try:
        plugin_sha = expect(root).get("manifest_sha")
    except Exception:  # noqa: BLE001 - no digest cannot vouch for any runtime
        plugin_sha = None
    out["plugin_manifest_sha"] = plugin_sha
    out["runtime_lags"] = runtime_lags(out, plugin_sha)
    out["lags_line"] = RUNTIME_LAGS_LINE
    return out


def discover(ctx: Optional[Dict[str, Any]] = None, *,
             check_runtime: bool = False) -> Dict[str, Any]:
    """The same answer `DISCOVER_BLOCK` prints, computed in-process.

    Two faces, one shape: in the container the block is printed for the device
    shell (nothing here can see the workspace); on a host that holds the data
    this returns the JSON directly, which is what makes the contract testable
    without a device bridge.

    BRIDGE3 (Train 2b §5 MUST 2): `check_runtime` adds `RUNTIME_CHECK_KEYS`
    (`runtime_check`); the CLI's `discover --compare '<answer>'` adds them to
    the block's own answer in the process that holds the plugin.
    """
    if check_runtime:
        return runtime_check(discover(ctx))
    ctx = resolve() if ctx is None else ctx
    ws = ctx.get("vm_path")
    out: Dict[str, Any] = {
        "ws": ws,
        "basename": ctx.get("basename"),
        "runtime_present": bool(ctx.get("runtime_version")),
        "runtime_version": ctx.get("runtime_version"),
        "manifest_sha": ctx.get("manifest_sha"),
        "staged_root": ctx.get("staged_root"),
        "brain_file": ctx.get("brain_file"),
        "device_path": ctx.get("device_path"),
        "python": ctx.get("python"),
        "probes": {"replace_over_existing": None, "unlink": None, "flock": None},
    }
    if ws:
        out["probes"] = _run_probes(Path(ws))
    return out


def _run_probes(ws: Path) -> Dict[str, Any]:
    """The three mount facts, run against `_hq/.system/` exactly as the block
    runs them: can `os.replace` land on an existing file, does `unlink` work,
    does `flock` raise. On the merged mount the answers are True / False / True
    (`P1_RESULT_2026-09-19.md`)."""
    probes: Dict[str, Any] = {"replace_over_existing": None, "unlink": None, "flock": None}
    system = ws / "_hq" / ".system"
    try:
        system.mkdir(parents=True, exist_ok=True)
        a = system / ".cr-probe"
        b = system / ".cr-probe.new"
        a.write_text("1", encoding="utf-8")
        b.write_text("2", encoding="utf-8")
        os.replace(str(b), str(a))
        probes["replace_over_existing"] = a.read_text(encoding="utf-8") == "2"
    except Exception:
        probes["replace_over_existing"] = False
    try:
        u = system / ".cr-probe.u"
        u.write_text("x", encoding="utf-8")
        u.unlink()
        probes["unlink"] = True
    except Exception:
        probes["unlink"] = False
    try:
        import fcntl  # noqa: F401 — absent on Windows, which answers the probe
        with open(system / ".cr-probe", "r+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        probes["flock"] = True
    except Exception:
        probes["flock"] = False
    return probes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_DATA_VERBS = ("read", "run_helper", "run_writer", "write", "append_jsonl",
               "remove")


def _flag(argv: List[str], name: str) -> Optional[str]:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _print(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    return 0 if (isinstance(payload, dict) and payload.get("ok")) else 1


# MF-19 (Night M3 merged-tree review F-1, BLOCKING). The CLI `write` verb read
# its payload from `text` while every rendered write form in the instruction
# layer - and this module's own verb table in WORKSPACE_ACCESS.md - passes
# `data`. `args.get("text")` found nothing, the door wrote "" and I-6's
# read-back verified the EMPTY payload it was handed: `ok:true, verified:true,
# bytes_written:0`. That was the 2026-09-22 walk's 0-byte inbox page, read at
# the time as a mount fault. ONE documented key now (`data`), `text` kept as a
# legacy alias, `b64` decoded and then met by the text-only fence below; a
# write that names no payload, or an empty one it did not ask for, is refused
# with the key named - an empty page is never a landed page.
WRITE_PAYLOAD_KEY = "data"
WRITE_PAYLOAD_KEYS = (WRITE_PAYLOAD_KEY, "text", "b64")


def _cli_write_payload(args: Dict[str, Any]) -> Tuple[Any, str, Optional[str]]:
    """`(payload, key read, None)` for a well-formed CLI `write`, else
    `(None, key, detail)` - the caller refuses `bad_args` with that detail.

    An empty payload is refused unless the call says `allow_empty: true`: every
    rendered form promises bytes (a page, a config, a list), so "" there is a
    lost payload, not an intended empty file.
    """
    present = [k for k in WRITE_PAYLOAD_KEYS if k in args]
    if not present:
        return None, WRITE_PAYLOAD_KEY, (
            "write needs `data` (the text to land); the call named none of "
            + " / ".join("`" + k + "`" for k in WRITE_PAYLOAD_KEYS)
            + ", so nothing was written")
    if len(present) > 1:
        return None, present[0], (
            "write takes ONE payload key; the call named "
            + ", ".join("`" + k + "`" for k in present))
    key = present[0]
    value = args[key]
    if not isinstance(value, str):
        return None, key, ("`" + key + "` must be a string, not "
                           + type(value).__name__)
    if key == "b64":
        # MF-M2-1: decoded here, refused below. `write` is text only and
        # `b64` is not a way round that - see its docstring. A caller that
        # wants to land a document calls `deliverables.land`, which applies
        # these same fences by asking this module for them.
        try:
            data: Any = base64.b64decode(value)
        except ValueError as exc:
            return None, key, "`b64` is not base64: " + str(exc)
    else:
        data = value
    if len(data) == 0 and args.get("allow_empty") is not True:
        return None, key, (
            "`" + key + "` is empty, so there is nothing to land; a write that "
            "means an empty file says `allow_empty: true`")
    return data, key, None


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: workspace_access.py "
                         "<discover|expect|install|verify|plan|read|run_helper|"
                         "run_writer|write|append_jsonl> [--json '<args>']\n")
        return 2
    verb = argv[0]
    started = time.time()

    if verb == "discover":
        if "--compare" in argv:
            # BRIDGE3 (Train 2b §5 MUST 2): the block's answer, compared with
            # THIS plugin's own `expect` where the plugin is.
            raw = _flag(argv, "--compare")
            try:
                handed = json.loads(raw) if raw else None
            except ValueError:
                handed = None
            if not isinstance(handed, dict):
                sys.stderr.write("discover --compare needs the JSON object the "
                                 "discover block printed\n")
                return 2
            return _print(dict(runtime_check(handed), ok=True))
        ctx = resolve()
        if "--block" in argv or (ctx["mode"] == CONTAINER and "--json" not in argv):
            sys.stdout.write(DISCOVER_BLOCK + "\n")
            return 0
        return _print(dict(discover(ctx), ok=True))

    if verb == "expect":
        return _print(dict(expect(), ok=True))

    if verb == "install":
        out = _flag(argv, "--out")
        if not out:
            sys.stderr.write("install needs --out <dir>\n")
            return 2
        return _print(dict(install(Path(out), ws=_flag(argv, "--ws")), ok=True))

    if verb == "verify":
        stage = _flag(argv, "--stage")
        if stage:
            return _print(verify_stage(Path(stage)))
        ctx = resolve()
        sys.stdout.write(verify_command(ctx) + "\n")
        return 0

    if verb == "plan":
        if len(argv) < 2:
            sys.stderr.write("plan needs a verb\n")
            return 2
        try:
            rendered = plan(argv[1], _flag(argv, "--json") or "{}")
        except ForwardRefused as exc:
            # A forwarded value that cannot be rendered safely is a STOP, not a
            # command with the value escaped or dropped: the model pastes what
            # this prints, so a half-rendered prefix would write the ledger
            # under the wrong identity. `ok:false` is the contract's stop.
            return _print(_refuse("plan", resolve(), started, R_BAD_ARGS,
                                  detail=str(exc), key=exc.key))
        sys.stdout.write(rendered + "\n")
        return 0

    if verb not in _DATA_VERBS:
        sys.stderr.write(f"unknown verb: {verb}\n")
        return 2

    ctx = resolve()
    try:
        args = json.loads(_flag(argv, "--json") or "{}")
    except ValueError as exc:
        return _print(_refuse(verb, ctx, started, R_BAD_ARGS, detail=str(exc)))
    if not isinstance(args, dict):
        return _print(_refuse(verb, ctx, started, R_BAD_ARGS,
                              detail="--json must be an object"))

    if verb == "read":
        return _print(read(args.get("rel", ""), binary=bool(args.get("binary")), ctx=ctx))
    if verb == "run_helper":
        return _print(run_helper(args.get("name", ""), args.get("args") or {},
                                 timeout_s=int(args.get("timeout_s") or DEFAULT_TIMEOUT_S),
                                 ctx=ctx, args_file=args.get("args_file")))
    if verb == "run_writer":
        return _print(run_writer(args.get("name", ""), args.get("args") or {},
                                 args_file=args.get("args_file"),
                                 timeout_s=int(args.get("timeout_s") or DEFAULT_TIMEOUT_S),
                                 ctx=ctx))
    if verb == "write":
        data, key, detail = _cli_write_payload(args)
        if detail is not None:
            return _print(_refuse(verb, ctx, started, R_BAD_ARGS,
                                  rel=args.get("rel", ""), key=key, detail=detail))
        landed = write(args.get("rel", ""), data, args.get("expected_mtime"),
                       expected_rev=args.get("expected_rev"),
                       create_parents=args.get("create_parents"),
                       holder=args.get("holder") or "workspace_access",
                       ctx=ctx)
        if isinstance(landed, dict):
            landed["payload_key"] = key
        return _print(landed)
    if verb == "remove":
        return _print(remove(args.get("rel", ""),
                             holder=args.get("holder") or "workspace_access",
                             ctx=ctx))
    if "rows" not in args:
        # IDENT1 I-5. The orchestrator's connector-read form once spelled the
        # key `pending_rows`, and this verb read `rows`, found none, and
        # answered ok:true n:0 - a landed-looking append of nothing. A call
        # that names no rows at all is a malformed call, said so.
        return _print(_refuse(verb, ctx, started, R_BAD_ARGS,
                              detail="append_jsonl needs `rows`"))
    return _print(append_jsonl(args.get("rel", ""), args.get("rows") or [],
                               holder=args.get("holder") or "workspace_access",
                               ctx=ctx))


__all__ = [
    "CONTAINER", "COWORK_LEGACY", "LOCAL", "VM", "HOST_MODES", "MODE_FROM_ENV",
    "DISCOVER_BLOCK", "DISCOVER_KEYS", "PROBE_KEYS", "RUNTIME_CACHE_REL",
    "RUNTIME_CHECK_KEYS", "RUNTIME_LAGS_LINE", "runtime_check", "runtime_lags",
    "REGISTERED_JSONL", "REV_FILES", "StaleRead",
    "FORWARD_ENV_KEYS", "ForwardRefused", "forward_env", "render_env_prefix",
    "append_jsonl", "build_manifest", "detect_host_mode", "discover", "expect",
    "fence_path", "find_root_down", "find_root_up", "install", "main",
    "manifest_modules", "manifest_sha", "plan", "plugin_root", "plugin_version",
    "read", "referenced_reference_files", "remove", "resolve", "run_helper",
    "RUN_WRITER_ALLOWLIST", "run_writer",
    "runtime_cache_dir", "runtime_file_set", "verify_command", "verify_stage",
    "write",
]


if __name__ == "__main__":
    raise SystemExit(main())
