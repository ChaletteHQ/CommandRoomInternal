# The workspace access layer

**What it is.** One module — `shared/scripts/workspace_access.py` — that every skill goes through to touch the customer's workspace, and the only thing SKILL.md prose is allowed to invoke for a read, a helper run or a write.

**Why it exists.** In the merged Claude app the helpers and the data are on different machines. `shared/scripts/*.py` is synced into a cloud container; the workspace is mounted only inside a sandbox VM on the customer's own computer, reached one shell call at a time. Neither can open the other's files. Every instruction in the plugin that said "`cd` to the plugin root and run Python against the workspace" assumed one filesystem, and there is no longer one.

**The shape.** A version-stamped, content-hashed copy of the runtime is installed into the workspace at `_hq/.cache/cr-runtime/<version>/`, and every substrate verb runs there, in place, against the live files. The append path keeps the real writer lock, the real seq allocation and the real read-back — byte for byte what a local run does today. The container's only jobs are to render commands, stage the runtime and read the skill files. See `PLUGIN_BOUNDARY.md` invariant 4 for the one-directory exception that permits the install.

---

## Access preamble (CONTRACT Rule 22 v6)

This is the block every skill file carries, byte for byte. It is propagated by
`scripts/dev/propagate_access_preamble.py`, which is why it is written here once
and nowhere else: sixty-odd files used to copy the v5 three-line preamble in five
spellings, and editing them by hand is how replicas drift.

It is shell, not prose, so it can sit inside the fenced block it introduces: the
five rules are comments the model reads, and the four lines under them are the
RESOLVE step a legacy or local seat actually runs. That reconciliation is the
whole point of v6 over v5 — a merged seat resolves with `discover` through the
device shell and never touches these four lines, while a Cowork or Claude Code
seat resolves in-shell exactly as it does today, byte for byte. Neither seat
gets a second contract.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
```

**On an empty plugin root.** In a scheduled fire `CLAUDE_PLUGIN_ROOT` comes through empty. Resolve the plugin by content instead — the synced registry glob `/root/.claude/plugins/synced/*/cr/shared/scripts/chat_output_renderer.py` — before falling back to the environment variable. A preamble that fails loud on an empty variable fails every scheduled run.

**The sentinels are the contract's edge.** `scripts/dev/census_python_blocks.py` and guard G69 both read the text between `>>> CR ACCESS PREAMBLE v6` and `<<< CR ACCESS PREAMBLE v6`, and a file whose copy differs by one byte is reported. A carrier is a file that holds at least one whole block; the bootloader is not one, because a scheduled fire resolves its own root branch by branch before it commits to one (BOOT3).

---

## The verbs

Six host-face verbs. One call is one verb and one JSON envelope line on stdout — never two verbs in one shell call, so a dropped folder grant can be answered by re-requesting and pasting the SAME command string again.

| Verb | Call | Answers with |
|---|---|---|
| `read` | `read(rel, *, binary=False)` | `{ok, rel, text\|b64, size, mtime_ns, sha256}` |
| `run_helper` | `run_helper(name, args, *, timeout_s=150, args_file=None)`; an `args_file` is read, walked and bounded exactly as on `run_writer` (CAP1) | `{ok, result, stderr_tail, elapsed_ms}` |
| `run_writer` | `run_writer(name, args, *, args_file=None, timeout_s=150)`; the inline `args` beat the file's, every argument walks against `ARG_WALK_CAP`, and an `args_file` over `ARGS_FILE_MAX_BYTES` (opened once, measured on the open handle, the read bounded at the ceiling plus one byte, sized over the End of Day's staging ceiling) is refused `bad_args` with `detail: args_file_too_large`; a file that is not a regular file is refused `args_file: not a file`, and one nested past the parser's depth `args_file: RecursionError` (CAP1, D-T2B-7) | `{ok, result, rel?, stderr_tail, elapsed_ms}` |
| `write` | `write(rel, data, expected_mtime, *, expected_rev=None, create_parents=None)` — on the CLI the payload key is `data` | `{ok, mtime_ns_before, mtime_ns_after, rev, bytes_written, verified, payload_key}` |
| `append_jsonl` | `append_jsonl(rel, rows, *, holder)` | `{ok, n, stamped}` |
| `remove` | `remove(rel)` | `{ok, existed, removed, moved_to, left_in_place}` |

**Every verb is path-fenced.** The target must resolve inside the workspace root (`refused_path`), must not sit under `_hq/.cache/cr-runtime/` (`refused_runtime_cache` — code is never data), and must not be `_hq/data/.writer.lock` (`refused_lock_file` — its bytes must never change).

**`run_helper`** takes `module:function` and passes three gates before anything runs: the module is listed in the installed runtime's `manifest.json`, the whole `module:function` pair is in `workspace_access.RUN_HELPER_ALLOWLIST` (read and compute helpers ONLY — no writer, gated or not), and every path-shaped argument is fenced exactly as a `read` or `write` target would be. The manifest alone is not a fence: `atomic_write` is in it by construction, which made the verb a write-anywhere primitive. Nor is a gated writer safe here: the helper door applies the path fences and nothing else, so an allowed `event_gate:append_event` let a caller append a line onto `entities.json` — the anchor file — and create unregistered `.jsonl` files, both of which `append_jsonl` refuses by the registered-file rule. `write` and `append_jsonl` are the write doors; `run_helper` writes nothing. A helper outside the registry is refused `not_allowed_helper`; an argument pointing outside the workspace or into the runtime cache is refused `refused_path` / `refused_runtime_cache`, before the helper is started. The helper runs with the workspace root as its working directory, the staged runtime first on the import path, and the environment block below. The budget is the LAYER's, not the shell's: at 150 seconds the verb answers `reason: timeout` with a full envelope rather than letting a 180-second shell cap truncate the reply into silence.

**`run_writer`** is the WRITE door (IDENT1, ruling R-M3-2), beside the read door and never merged into it. A document, a receipt, a close or a re-pin runs here, and passes five gates in order before anything is started: there is a workspace on this host and it is attached (`container` refuses `no_workspace_on_this_host`); the whole `module:function` pair is on `workspace_access.RUN_WRITER_ALLOWLIST` (`not_allowed_writer` — a read helper's name is refused here exactly as a writer's name is refused by `run_helper`); the module is in the installed runtime (`not_in_manifest`); every path-shaped argument fences as a `write` target would; and the WRITER IS NAMED — `receipts.require_writer_identity` is asked of the environment the writer will run with, and a merged seat's VM with no forwarded writer id, or a scheduled fire that is not the workspace's declared scheduled writer, refuses `writer_identity_required` with the one sentence in `line`, having run nothing. Arguments are bound by NAME. A payload too large or too quote-laden for a pasted command line (a brief's sections) travels as `args_file`: a JSON object written in the chat's OWN scratch, read by the door as the arguments and never written into the workspace. The envelope names a landed file by `rel`, never by the mount path. The receipt writers ask the same question themselves, so a writer imported into a shell on a merged seat refuses the same way instead of stamping a session token — the 2026-09-22 prep receipt is the reason (`data.machine: claude-…`, a session id in append-only history).

The list, and why each name is on it (guard G74 reads this table; a name with no row here is RED, and no name may be on both lists):

| Writer | Why it is a writer, and who calls it |
|---|---|
| `brief_writer:make_brief_from_json` | Builds and saves a brief `.docx` at a path the prep resolved; the document IS a write, and a `.docx` cannot cross the text-only `write` verb. Called by call-prep (I-2). |
| `receipts:log_prep_receipt` | Appends the one `prep_brief` receipt, stamped with the customer's writer id. Called by call-prep after the document lands. |
| `exit_doors:apply_fact_closes` | Closes commitments whose facts say they are done, and runs the calendar leg once; appends closure rows and receipts. Called by the scheduled exit-doors job and by `pull up <name>`; never by a prep (D-T2B-1, MIGRATE3-MB). |
| `calendar_close:run_calendar_close_job` | The calendar close leg on its own, for a surface that runs it without the other fact closes. |
| `connector_config:set_declared_backend` | Re-declares which connector serves a category (a whole-file `entities.json` write through the locked writer). Called by the interactive re-pin, never from a fire. |
| `release_actions.connector_display_name_repin_v1:connector_display_name_repin_v1` | The update bridge's one-time re-pin of a connector declaration to the name this seat's registry uses; writes the declaration and its ledger rows. Called by the bridge on a merged seat. |
| `fire_delivery:set_artifact_publish` | Turns the workspace's "publish my scheduled pages" setting on or off (a whole-file `entities.json` write through the locked writer) and appends one `fire_delivery_changed` row. Called by workspace-manager on the customer's own words, never from a fire. |
| `fire_delivery:record_artifact_url` | Stores the page address the interactive publish got for one scheduled chat, so a fire can REPUBLISH it in place (a fire may never publish a new page). Called by workspace-manager right after that publish. |
| `schedule_config:declare_scheduled_writer` | Declares this workspace's ONE scheduled writer (SAFETY0, RETIRE1's R0): a whole-file `entities.json` write through the locked writer and one `scheduled_writer_declared` row, idempotent. Called by the registration skill and the update bridge's Phase 4.7 on the registering seat — the seat whose own writer id becomes the declared one. |
| `commitments_helpers:apply_cru_writes` | Performs the closes and per-person receipts a Waiting On CRU pass PLANNED (`plan_sent_cru` / `plan_calendar_cru`), in order, through the single closure path (`close_commitment` / `mark_partial_received`), recording a refused id and running the next. Called by orchestrator-commitments Phases 2.5 and 2.7 (ORCH2C). |
| `commitments_helpers:run_waiting_on_surface` | The Waiting On one-command driver (`surface_drivers.run_surface("waiting-on", …)`): mints the rows' display numbers, freezes the page-set, lands the page and writes the fire's one receipt in the same call (FB-7). Called by orchestrator-commitments Phase 9 (ORCH2C). |
| `reconcile_inbound_commitments:reconcile_inbound_and_receipt` | The inbound reply pass end to end: matches, closes through the closure path, queues the confirms and lands its own audit row. Called by orchestrator-commitments Phase 2.6 (ORCH2C). |
| `commitment_dedup:apply_auto_merges` | Applies the auto-merges capture stamped (propose, supersede, resolve — the FB-20 lifecycle, reversible). Called by orchestrator-commitments Phase 2.8 (ORCH2C). |
| `skill_config_writer:save_skill_config` | Saves a skill's first-run knobs and emits its configured event. Called by orchestrator-commitments on the Waiting On chat's first fire (ORCH2C). |
| `deliverables:land` | Lands a text or document deliverable at a workspace-relative path through the layer's own fences, and answers the sentence that names where it went. |
| `schedule_backend:record_trigger_row` | Writes one `triggers` row (trigger id, local and UTC cron, zone, offset) into `_hq/workspace_config.json` after a scheduled chat is registered or its cron moves, so every later read can say which chat a trigger is; the row's folder is this seat's forwarded folder, never an argument (the door fences paths). Called by enable-command-room-schedules after every create (SCHEDREG1); since SCHEDFAST1 the create path rides `record_registration` instead, and this writer stays for cron changes. |
| `schedule_backend:record_registration` | One door call per created chat: writes the `triggers` row exactly as `record_trigger_row` does, then appends the chat's `schedule_created` receipt through the same gate `append_jsonl` uses. Halves the device round trips a registration costs on the merged seat. Called by enable-command-room-schedules after every create, rendered from `registration_payload` (SCHEDFAST1, #101). |
| `schedule_config:write_schedule_skipped` | Appends the one `schedule_skipped` row a seat writes per workspace-local day when it cannot register (no scheduler, a shut gate, or no account to name its writer). Called by enable-command-room-schedules Step 0.0 (SCHEDREG1). |
| `release_actions.claude_md_docs_rule_v1:claude_md_docs_rule_v1` | The update bridge's one-time append of the document-routing rule to the workspace CLAUDE.md (one line under `## Session Rules`, the section created at the end of the file when missing) and its one `workspace_migration_applied` row; idempotent by marker and by adjudication. Called by the bridge's Phase 4.5 (DOCS1 D-1). |
| `deliverables:export_claude_doc` | Lands a Claude Doc the composer produced despite the rule as a NEW file under the owning kind's folder through `land` (the same fences), refreshing the same file in place for the same doc, and composes the `deliverable_landed` receipt row the caller appends through `append_jsonl`. Called by the six document-owning skills and by workspace-manager on `export that doc` (DOCS1 D-2). |
| `skill_config_writer:save_skill_config` | Saves a skill's first-run (or tuned) settings: writes the settings file AND its `skill_first_run_configured` / `skill_reconfigured` event. Called by the morning brief on its first fire (ORCH2). |
| `maintenance_dispatcher:run_job` | Runs ONE script-class maintenance job by its registry leg, forwarding the run mode the door sets; the job writes its own rows and receipt through its own writers. ONE entry for the whole job registry. Called by the maintenance fire's merged branch (ORCH2). |
| `substrate_health:substrate_alarm_lines` | Composes the loud substrate alarm lines for the health check, and on its way SWEEPS resolved alert files (a file removal), which is why it is not a read. Called by the health check's self-report (ORCH2). |
| `brief_path:ensure_brief_directory` | Creates `_hq/meetings/` when it is missing (a directory write). Called by the End of Day before a meeting brief is saved (ORCH2). |
| `eod_helpers:apply_cru_pass` | The End of Day's commitment pass over one transcript, applied: closes, updates and proposals through their own writers, then the walk record — the old block's two writes in its order. Called by the End of Day per meeting (ORCH2). |
| `eod_helpers:run_shadow_lane` | The non-attendee shadow lane over the discovered meetings; the code it routes through can create a person record. Called by the End of Day's Phase 4.8 (ORCH2). |
| `exit_doors:apply_own_word_closes` | Closes commitments the CEO said, in their own words on a call, are done — one batch, one `undo`. Called by the End of Day per transcript (ORCH2). |
| `meeting_capture:route_meeting_captures` | THE meeting-capture admission gate: routes each extracted commitment and, given the attendee list, creates the person a capture names; answers the rows for the caller's `append_jsonl`. Called by the End of Day per meeting (ORCH2). |
| `plate_helpers:run_my_plate_surface` | What's on my plate one-command driver — `surface_drivers.run_surface("my-plate", …)`: the view mints the plate's display numbers under the writer lock, freezes the page-set, persists the audit page and, on page 1 with `fired_via`, lands the ONE `pack_run` receipt in the same call (FB-7). Called by the My Plate chat, Phase 9 (ORCH2). |
| `morning_brief_helpers:morning_pack` | The morning brief's pack, built beside the data (`surface_drivers.build_morning_brief_pack`, manual): building the plate mints its display numbers under the ledger lock, so it is a writer. Its ledger rows and its audit copy do NOT land here — they come back for one `plan append_jsonl` and one `plan write`. Called by morning-briefing Step 3h (BRIEFDOOR1). |
| `morning_brief_helpers:plan_lane_asked` | The morning brief's after-the-post mark (`end_of_day.mark_lane_asked`), run beside the data with its appends held: `mark_asked`'s idempotency read takes the ledger lock, so it is a writer. The `commitment_updated` rows come back for one `plan append_jsonl`. Called by orchestrator-morning-brief Phase 6.1 (BRIEFDOOR1 SHOULD 6). |
| `morning_brief_helpers:plate_lines` | The plate's brief cut on the direct path (`plate_view.build_plate` + `render_plate(view, "brief", False, …)`); the build mints display numbers, so it is a writer. Called by morning-briefing Step 3i when the pack is not in hand (BRIEFDOOR1). |
| `morning_brief_helpers:run_morning_brief_pack` | The scheduled Morning Brief's pack (MIGRATE3-MB, F-T2-15): the SAME builder the `surface_drivers.py morning-brief` CLI wraps (`build_morning_brief_pack`, with the fire's run mode), run beside the data. It logs the `brief_state` row, keeps the pack's audit copy and mints the plate's display numbers under the ledger lock, so it is a writer; a build that raises writes the `surface_failed` receipt and answers the one sentence. Called by orchestrator-morning-brief Phase 3.9. |
| `morning_brief_helpers:record_fire_stopped` | The scheduled Morning Brief stopped mid-chain because a door answer was `ok: false` (a refusal, or the door's timeout, which kills the child before the pack writer's own failure branch runs): writes the `surface_failed` receipt through `surface_drivers.log_surface_failed` and answers the one sentence to post. Called by orchestrator-morning-brief's stop rule (MIGRATE3-MB fix round 1). |
| `staff_meeting_helpers:run_staff_meeting_surface` | The Staff Meeting one-command driver — `surface_drivers.run_surface("staff-meeting", …)`: on page 1 it runs the watch-expiry pass, freezes the page-set, persists the audit page, marks the rehomed overdue question asked and, with `fired_via`, lands the ONE `pack_run` receipt in the same call (FB-7). Called by the Staff Meeting chat, Phase 5 (ORCH2 fix pass 1). |
| `inbox_helpers:run_inbox_surface` | The Inbox chat's one-command driver after the fetch (INBOXDRIVE1, ruling R-RW3-1 (a)): the priority cut, the capture and reconcile rows, the page landed through `write` and verified, the grouped text, the delivery plan, the `connector_read` row and — last — the fire's ONE `pack_run` receipt, in one call beside the data; it answers the text the fire delivers. Called by the Inbox chat after its mail fetch. |
| `maintenance_dispatcher:mark_fire_start` | Lands a scheduled maintenance fire's start marker (FIREGAP2), which `catch_up_plan` answers `fire_marker.held` for because a read door writes nothing; one sidecar file under `_hq/.system/`. Called by the maintenance fire's merged branch after M1 answers `verdict: run` (MAINTJOBS1). |
| `reconcile_sent_commitments:reconcile_and_receipt` | The reconcile-sent job end to end from the Sent rows the fire fetched: closes through the closure path, the sent-promise capture and contact passes, the cursor advance and its own `sent_reconcile` audit row; it names its writer first. Called by the maintenance fire's merged branch, M2 (MAINTJOBS1). |
| `chat_reconcile:reconcile_chat_and_receipt` | The reconcile-chat leg end to end from the chat rows the fire read: closes and close-proposals through the shared rails, the chat cursor and its own `chat_reconcile` audit row (a skip receipt when no chat backend is declared); it names its writer first. Called by the maintenance fire's merged branch, M2 (MAINTJOBS1). |
| `eod_incremental:log_capture_pass_receipt` | Appends the incremental capture pass's ONE `pack_run` under task id `meeting-capture` (the window fields and counts the next pass resumes from); never under `past-meetings`. Called by the maintenance fire's merged branch after the capture phases ran through the door, M2 (MAINTJOBS1). |
| `session_sweep:sweep_and_receipt` | The nightly session sweep from the items the fire extracted: dedups and writes them through the capture core, composes the session narratives, and appends its own `session_sweep_run` receipt; it names its writer first. Called by the maintenance fire's merged branch, M2 (MAINTJOBS1). |
| `cleanup_actions:sweep_lock_litter` | The daily lock-litter sweep on the merged shape: archives `.lock.stale.*` sentinels older than an hour by ONE `os.replace` each (the mount refuses a delete) and prunes month-old read-alarm sidecars; a file it cannot move is named in its answer, never retried; it names its writer first. Called by the maintenance fire's merged branch, M2b (MAINTJOBS1). |
| `schedule_refresh:log_schedule_refreshed` | Appends one `schedule_refreshed` row for a field a re-projection moved (the clocks changed, the customer did not). Called by the maintenance fire's merged branch after its scheduler call succeeded, schedule-realign in M2 (MAINTJOBS1). |
| `schedule_backend:record_trigger_map` | Re-stamps one stored trigger row in `_hq/workspace_config.json` (merge) so tomorrow's run does not see the same drift. Called by the maintenance fire's merged branch after its scheduler call succeeded, schedule-realign in M2 (MAINTJOBS1). |
| `path_repair:fire_time_guard` | The update bridge's Step 0 root guard: validates this workspace's registration record against where it runs and, when it can, REPAIRS it (a fingerprint backfill or a re-point, both whole-file writes to the workspace config) - which is why it is a writer and never on the read list (HYGIENE3, D-3). Called by the update bridge before anything else reads the workspace. |
| `recover_corruption:run_recovery_if_needed` | The update bridge's Phase 4.4 self-heal: quarantines unparseable ledger lines, rewrites the ledger without them and appends one `corruption_recovery` row. Called by the update bridge with `recurring=True` (HYGIENE3). |
| `thread_writer:repair_dual_project_key` | The update bridge's Phase 4.4b repair: folds a vestigial `projects` key into `threads` and deletes it (a whole-file `entities.json` write through the locked writer, only when the key exists). Called by the update bridge (HYGIENE3). |
| `eod_helpers:run_end_of_day_pack` | The End of Day pack, built beside the data by the SAME builder the old `surface_drivers.py end-of-day` command wrapped (`build_end_of_day_pack`), its four inputs by name: it writes the pack's audit copy, and on a failure the `surface_failed` receipt and the one sentence, never a trace. Called by orchestrator-past-meetings Phase C (MIGRATE3-EOD, F-T2-15). |
| `eod_helpers:answer_eod_questions` | The evening's numbered answers landed through the queue's own writers (`eod_question_budget.apply_eod_answers`: one batch, one `eod_question_answered` receipt, one `undo`), with the one ack sentence. Called by end-of-day's answer handler (MIGRATE3-EOD). |
| `eod_helpers:score_coach_answer` | The self-scored coaching answer (`end_of_day.resolve_coach_score`): one `coaching_answer` row, or a refusal with nothing written, with the one ack sentence. Called by end-of-day's answer handler (MIGRATE3-EOD). |
| `eod_helpers:stage_fire_input` | Lands ONE input the End of Day fetched (the meeting records, the decision transcripts, the shadow lane's meetings) as a list under `_hq/.system/eod-fire/`, one file per kind overwritten each fire, from bytes handed under `content_base64`, and answers its `rel` for the read that needs it. Called by orchestrator-past-meetings Phases 3.5, 4.6.b and 4.8 (MIGRATE3-EOD fix round 2). |
| `eod_helpers:run_cru_pass` | The End of Day's commitment pass over one transcript in one call beside the data: the walk (walk ledger, then the matcher over the open set) and the write (`apply_cru_pass`), so the matcher's results never cross the door. Called by orchestrator-past-meetings Phase 4.6 (MIGRATE3-EOD fix round 2). |
| `eod_helpers:record_fire_stopped` | When a door step of the End of Day fire is refused mid-chain: writes the End of Day `surface_failed` receipt and answers the one sentence to post, so a partial day is never silent. Called by orchestrator-past-meetings (MIGRATE3-EOD fix round 2). |
| `eod_helpers:reconcile_sent_staged` | The End of Day's mail leg over the Sent batch the fire staged raw: reads it beside the data, keeps each message's named fields in code, and runs `reconcile_sent_commitments.reconcile_and_receipt` once over the whole batch (one audit row, one cursor move, a first run's 30 days included). Called by orchestrator-past-meetings Phase A (EODHARD3). |
| `eod_helpers:reconcile_chat_staged` | The End of Day's chat leg over the chat batch the fire staged raw: reads it beside the data, keeps each message's named fields in code, and runs `chat_reconcile.reconcile_chat_and_receipt` once. Called by orchestrator-past-meetings Phase A (EODHARD3). |
| `eod_helpers:clear_fire_staging` | Removes the End of Day fire's staged files under `_hq/.system/eod-fire/` (the raw inputs, the meeting records, the transcripts, the pack copy) and nothing else, emptying a file the mount will not delete. Called by orchestrator-past-meetings at the start of Phase A and after the Phase 5 receipt (EODHARD3). |
| `eod_helpers:close_from_eod` | A tapped `mark done`, `resolved`, `drop` or `push to [date]` on the End of Day's row list: resolves the number through `end_of_day.resolve_choice` (a refusal answered with nothing written), then closes through `commitment_state.close_commitment` or moves through `commitment_state.apply_later`, and answers the one ack sentence. Called by apply-choices' `end-of-day` source (EODTAP2, MIGRATE3-EOD N-8). |
| `friday_wrap_helpers:record_offer` | Closes the wrap's earned door after the post: `quiet.wrap_record_offer` writes the once-ever offer row (a whole-file coaching settings write), and with no key it writes nothing. Called by the Friday Wrap orchestrator's Phase 5 tail and weekly-recap Phase 6 (MIGRATE3-FW). |
| `friday_wrap_helpers:plate_cut` | The Friday wrap's plate cut and its `.docx` section (`plate_view.wrap_cut`): building the plate mints its display numbers under the ledger lock, so it is a writer. Called by weekly-recap Phase 4 section 3 (MIGRATE3-FW). |
| `friday_wrap_helpers:measure_line` | The wrap's one measure line (`flow_measure.wrap_line`): its plate count comes off the plate's own projection, which mints display numbers. Called by weekly-recap Phase 4 section 8b-ter (MIGRATE3-FW). |
| `friday_wrap_helpers:coaching_blocks` | The week against the word (`quiet.wrap_coaching_blocks`): next week's three come off the plate, which mints display numbers, and a fence refusal writes its one `surface_failed` receipt. Called by weekly-recap Phase 4 section 8d (MIGRATE3-FW). |
| `friday_wrap_helpers:post_turn` | The wrap's whole turn through `surface_composers.post`: it re-composes the plate cut in its own process so the relay's origin check holds (nothing stamps text it was handed), and a refusal writes its one `surface_failed` receipt. Called by weekly-recap Phase 5.C (MIGRATE3-FW). |
| `friday_wrap_helpers:land_recap` | The weekly recap document, built by `brief_writer.make_brief_from_json` from a payload the skill landed with `write` at a workspace-relative path and this writer reads beside the data, because a heavy week's payload is never handed through the door's argument fence (the by-path shape, D-T2B-7; the cap is `ARG_WALK_CAP`). Called by weekly-recap Phase 5.B (MIGRATE3-FW). |
| `people_writer:door_create_person` | Creates one person record (a whole-file `entities.json` write through the locked writer) and its `person_created` row, after the writer's own dedup; a duplicate, an ambiguous name, a one-word name or an out-of-scope account is answered `ok: false` with nothing written, never raised (D-W2-5). Called by people-crm's Writer call shape when the dedup read found no match (PEOPLEWRITE2). |
| `people_writer:door_update_person` | Changes fields on one person on file (a whole-file `entities.json` write) and appends `person_updated` plus any role or company lineage row; an id not on file is answered, never raised. Called by people-crm on a dedup match, a personal tie and a stated role change (PEOPLEWRITE2). |
| `people_writer:door_record_person_fact` | Appends one `person_fact_observed` row for a fact the CEO stated about a person on file; the record itself is never touched. Called by people-crm's `remember [fact] about [name]` (PEOPLEWRITE2). |
| `people_writer:door_auto_add_person` | The rich-context auto-add: the same-name gate first (`needs_confirm` writes nothing), then one person record and its `person_created` row, an email kept only with the source it was observed in. Called by people-crm's auto-add path (PEOPLEWRITE2). |
| `schedule_config:log_schedule_config_change` | Appends one `schedule_config_changed` row (`changes: [{task_id, cron, enabled}]`, reason `reenabled_after_missed_run`) - the row the lateness ledger reads, so a slot the app's switch-off missed is never scored. Called by the maintenance fire's merged branch after it switched its own chat back on, M1b (T3 FIRE3). |

**`write`** takes its payload under ONE key on the command line, `data` — the key every rendered form passes (`text` is still read as a legacy alias; the base64 key decodes and then meets the text-only fence). A call that names no payload key, names two, or hands an empty payload without `allow_empty: true` is refused `bad_args` with the key named, and nothing is written: the 2026-09-22 walk's empty inbox page was a form passing `data` to a verb that read only `text`, answered `ok:true` for 0 bytes. The envelope's `bytes_written` counts the payload read from that key and `payload_key` names it. It refuses `events.jsonl` and its year shards outright (`refused_append_only`) — an append-only ledger is never whole-file written. `entities.json` and `aliases.json` go through the canonical locked JSON writer, and the mtime compare runs INSIDE that lock, on the same host that is about to write, so it is a real compare-and-swap rather than a narrowed window; `expected_rev` is the stronger guard there, because the substrate's own version signals are `version`, `.rev` and `.seqhw` — never mtime. Everything else goes through the canonical text writer, and a path outside `_hq/` will not fabricate a missing parent directory: a missing project folder means the record is wrong, and a `mkdir` there makes a bad record look valid.

**`remove`** is the delete door, and its envelope is VERIFIED rather than assumed. It is fenced exactly like `write`, and on top of that fence it refuses the substrate outright (`refused_protected`): `events.jsonl` and its year shards, `entities.json`, `aliases.json`, `processed-meetings.json`, the writer lock, `.seqhw`, `.source_refs.idx`, and anything under the runtime cache. Everything else is tried as an unlink; a mount that refuses the delete gets DEL1's refuse-then-move idiom and the envelope says `moved_to: <name>.stale.<epoch>.<pid>`; a mount that refuses the rename too says `left_in_place: true` and stays `ok: true`, because the honest answer is the answer. `removed: true` is written only after the path is confirmed gone. In `container` mode there is no workspace on this host, so the answer is `no_workspace_on_this_host` — never a delete against whatever the container has mounted.

**`append_jsonl`** takes no mtime guard at all, by design: an append-only ledger's mtime changes on every legitimate append, so an mtime compare would refuse every contended write. The guards are the writer lock, the `.seqhw` high-water sidecar and the FS-04 refusal — all of which run unchanged because the runtime is beside the data. `rel` must be a registered JSONL file; anything else is `not_registered_jsonl`.

## The envelope

Every host-face call prints exactly one JSON line carrying `ok`, `verb`, `ws`, `mode`, `runtime_version`, `host_python`, `elapsed_ms` and `folder_attached`, plus the verb's own fields. `ok:false` always carries a `reason`, and the reasons that can be acted on carry a `next`:

| `reason` | Means | `next` |
|---|---|---|
| `no_workspace_on_this_host` | this process is in the container | `device_bash` |
| `folder_not_attached` | the workspace is not mounted right now | `device_request_folder_access` |
| `refused_path` / `refused_runtime_cache` / `refused_lock_file` | the path fence | — |
| `refused_append_only` | whole-file write to the ledger | `append_jsonl` |
| `refused_protected` | `remove` on the substrate itself, a lock, a sidecar or the runtime cache | — |
| `not_registered_jsonl` / `not_in_manifest` / `bad_args` | the allow-lists | — |
| `not_allowed_helper` / `not_allowed_writer` | the name is not on that door's list (a writer through the read door, or a helper through the write door) | — |
| `writer_identity_required` | a writer on a merged seat with no forwarded writer id, or a scheduled fire that is not the declared writer; `line` is the one sentence, and it is the whole answer | — |
| `bad_args` on `plan` | a forwarded value carries a quote, a backslash, a newline or a shell expansion, so no command is rendered | — |
| `stale_read` | the compare-and-swap lost; re-read and retry | — |
| `timeout` | the helper outran the layer's budget | — |
| `missing_parent` | the project folder does not exist | — |

`ok:false` is a stop. It is never a reason to hand-run the operation another way — the improvised path is the class this layer exists to end.

## Where the data is, relative to this process

`workspace_access` names four host modes. They answer a different question from `env_detect`'s modes, which name where the PROCESS runs; neither module renames the other's vocabulary.

| `env_detect.detect()` | `host_mode` | What the verbs do |
|---|---|---|
| `legacy_cowork` | `cowork_legacy` | run in this shell — today's behaviour, byte for byte |
| `claude_code_local` | `local` | run in this shell against the local workspace |
| `merged_cloud`, running from the installed runtime inside an anchored workspace | `vm` | run in place on the mount |
| `merged_cloud`, anywhere else | `container` | **refuse** |
| `unknown` | `container` | **refuse** |

In `container` mode every data verb returns `no_workspace_on_this_host` and says to run it through the device shell. It never stages a copy of the customer's data and never guesses a workspace — a silent fallback would put customer data in a place the boundary promises it never goes.

## The environment every helper process gets

The write door's child gets the same block minus `CR_HELPER_DOOR` (it is not a read). `plan` forwards `CR_WRITER_ID`, `CR_WRITER_DERIVATION`, `CR_FIRED_VIA`, `CR_DEVICE_WORKSPACE` and `CR_TRIGGERED_BY` in front of the rendered command, and the door copies them onto the child.

`CR_WORKSPACE` (the root), `TZ` (the workspace's own timezone, because the VM's clock is UTC and several helpers read a naive local clock), `PYTHONDONTWRITEBYTECODE=1` (no `__pycache__` inside a synced folder), `CR_RUNTIME_VERSION`, `CR_HOST_MODE` and `CR_ENV`. `CR_DATA_ROOT` is explicitly removed and never set — relocating the substrate is a separate, M-gated migration.

## Installing the runtime

Once per plugin version, from the update bridge:

1. `workspace_access.py expect` — the version and manifest digest this plugin ships.
2. `workspace_access.py discover` — the version and digest installed in the workspace.
3. They match: nothing to do.
4. They differ: `workspace_access.py install --out <dir> --ws <WS>` builds ONE zip (`shared/scripts/**`, `shared/data-schemas/**`, the `references/**` files those scripts open, `plugin.json`, and `manifest.json` with a sha256 per file) and prints the commit plan. `skills/` is NEVER shipped — instructions stay where the model reads them.
5. Commit the zip to `<WS>/_hq/.cache/cr-runtime/`, then run what `workspace_access.py verify` prints: it unzips into `<version>/`, recomputes every sha in the manifest, and writes the `current.json` pointer ONLY on a full match. A pointer file, never a symlink — Drive and FUSE do not carry symlinks reliably.
6. A mismatch refuses to write the pointer, which means the runtime is not installed, which means the verbs stop. A modified runtime is a foreign write into plugin code, and running it anyway would be worse than not running.

Old versions are kept, not deleted: `cleanup_actions.runtime_cache_report` lists what is installed and which versions are past current + previous, and reports it in the Monday note. Nothing in `cleanup` enters the cache — a swept runtime is a runtime whose manifest no longer verifies.

## Writer identity

Receipts and lock diagnostics name the writer with one id derived from the account and the workspace folder name, persisted at `_hq/.system/writer_identity.json`. The account id itself is never written into the workspace. Seats with no account in their environment — every legacy and local install — keep exactly the identity they carry today.

The account lives in the CONTAINER and the helper child runs on the device host, so the id is DERIVED here and the derived pair travels on the rendered command: `plan` prints `CR_WRITER_ID` and `CR_WRITER_DERIVATION` in front of `python3`, and the far end stores and compares them exactly as it would a locally derived pair. The account id is not in that string and never is. A pair that does not have the right shape is ignored, not repaired — the seat keeps today's identity rather than writing a guess into append-only history. Without this the identity is simply absent beside the data, which is what put a per-session token in `data.machine` on every receipt of the 2026-09-20 walk.
