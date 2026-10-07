---
name: enable-command-room-schedules
surfaces: cowork
slack_fallback: "Scheduled chats are set up from a chat with your Command Room folder attached — say `set up command room schedules` there; your Slack briefs are scheduled server-side and fire on their own."
description: "Sets up the Command Room schedule: its daily chats and silent background tasks. Fires on: 'set up command room schedules', 'register my scheduled chats', 'set up my daily chats', and silently from the update bridge. Registration is the writer of record for the schedule config. Registers the daily action chats, the weekly surfaces, and one maintenance task carrying the SILENT_TASKS jobs, each loading its steps fresh from the installed plugin at fire time. Proposes optional client-mix tasks (relationship-moves, dormant-customer-scan) — propose, never auto-register. Does NOT fire on 'change my schedule' / 'list my schedules' / 'configure my schedules' / 'pause [chat]' (change-schedule), or 'show my schedule' (the calendar, no skill)."
---

<!-- SCHEDULER DISCOVERY >>> -->
```text
SCHEDULER DISCOVERY (SCHEDDISCOVER1) - rendered by schedule_backend.discovery_step(); never edit by hand.
A scheduler tool you cannot see yet may only be waiting to be loaded. Load first, look second, then decide.
1. LOAD FIRST, every time. Claude Code and the Claude app: run the host's tool search once per query,
   exactly as written, asking for up to 10 results each:
       +claude-code-remote create trigger list triggers
       +scheduled-tasks create scheduled task list tasks
   The older desktop app, which has no tool search: read its full connector tool listing, end to end.
2. LOOK SECOND. Hand ONLY the names that loaded, as plain strings, to this skill's availability guard
   and to schedule_backend.select_backend below. A scheduler name still waiting to be loaded is not a
   scheduler: it goes only into step 3's TOOLS, as {"name": <id>, "deferred": true}.
3. THE OPENING LINE. Render this one call with `workspace_access.py plan run_helper --json '<it>'` and
   paste what it prints (the older app, or Code on this computer: `workspace_access.py run_helper` here):
     {"name":"schedule_backend:opening_lines","args":{"tools":<TOOLS>,"env_mode":"<CR_ENV>","availability":<AVAIL>,"folder_attached":<FOLDER>,"reloaded":<RELOADED>,"error_text":<ERROR>}}
   TOOLS = the loaded names and any waiting one; AVAIL = the guard's answer, as it came back;
   FOLDER = is a workspace folder attached to this chat (true/false); RELOADED = false; ERROR = null,
   or the scheduler's own error text when one of its calls failed (then call again). Say each line of
   `result` verbatim, once, as the first thing this skill says about schedules, before any listing or
   registration; it replaces the no-scheduler sentence. An empty result adds nothing: the guard's own
   refusal stands alone, and a step that says nothing about schedules still says nothing. Add nothing
   of your own about where the chats run or why.
   Refused because this chat has no workspace folder: say "This chat has no workspace folder attached, so it cannot see your scheduled chats; open a chat with your Command Room folder attached." instead.
4. If that line said the tools are loading: refresh the host's connector tools once, run step 1
   again, then steps 2 and 3 with RELOADED = true. The answer after that reload is final.
```
<!-- <<< SCHEDULER DISCOVERY -->

## Verify-only mode (preview without firing) — rewritten for the bootloader era (Phase 3 / P0.3)

Triggers: `verify command room prompts` / `check my command room version` / `which version are my tasks on`

When fired with one of these phrases, this skill runs in **read-only verification mode** — it inspects the registered prompts WITHOUT updating anything. Output: per-task status. Use this BEFORE firing scheduled tasks if you want to confirm the prompts are current.

**What changed in Phase 3 (P0.3):** the old flow checked markers where they no longer live and classified every HEALTHY install as "unknown / very old" (see references/HISTORY.md § Phase 3 / P0.3). Verification now checks each layer where that layer actually lives (bootloaders intentionally don't carry the OUTPUT CONTRACT marker; the contract lives in the on-disk orchestrator files the bootloader reads at fire time):

1. **Read the installed plugin version** from `$PLUGIN_ROOT/.claude-plugin/plugin.json` (resolve `$PLUGIN_ROOT` via the canonical CONTRACT.md Rule 22 preamble: the Access preamble (CONTRACT Rule 22 v6 — `shared/WORKSPACE_ACCESS.md`)).
2. Execute the plan `backend.plan_list()` returns and `normalize` the result. The Command Room set is every registered taskId that appears in `ORCHESTRATOR_MAP` (the chats) or the `SILENT_TASKS` registry (the background tasks) — bare taskIds, up to the full `DEFAULT_SCHEDULES` set. Count the set from the registry at read time; never state a number from memory. Do NOT filter on a `cr-` prefix (retired v2.14.27; matches zero tasks on a current install).
3. **Chat tasks — verify the registered prompt is a canonical bootloader:** check the same `REQUIRED_MARKERS` Phase 3.5 uses (`# Scheduled task bootloader`, `Resolve the plugin path`, `Read the orchestrator and execute it verbatim`, `Anti-improvisation contract`) plus correct `<TASK_ID>`/orchestrator-filename substitution and no leading frontmatter. A prompt missing the markers is a stub or a pre-bootloader pin → "refresh needed".
4. **Chat tasks — read the registration version stamp:** parse `plugin-version:` from the registered prompt (stamped at registration, Phase 3/W4). Stamp == installed version → current. Stamp older → "registered under vX — refresh will land on the next `set up command room schedules` run". No stamp → "pre-stamp registration (older than Phase 3)" — informational, not a failure, because fire behavior always comes from the freshly-resolved plugin.
5. **Contract layer — read from the FILES, not the prompts:** for each taskId in `ORCHESTRATOR_MAP`, read the on-disk `references/orchestrator-<name>.md` and confirm it carries the `OUTPUT CONTRACT` marker in its first 1500 chars (same assertion Step 1.A applies at registration). That is where the contract lives in the bootloader era; a registered prompt was never the right place to look for it.
6. **Silent tasks:** verify the registered prompt matches the current composed prompt from `compose_silent_task_prompt()` — match → current; differ → "refresh needed"; missing → "not registered — say `set up command room schedules`".

**THE CURRENCY VERDICT COMES FROM ONE FUNCTION, FOR EVERY TASK (SCHEDVIEW1 5.4, MANDATORY).** Do not compose or compare prompts by hand here, and do not decide currency from the version stamp: `schedule_refresh.plan_prompt_refresh(task_id, registered_prompt=..., workspace_root=..., plugin_version=...)` answers for a chat task and a silent task alike, composing each the way registration composes it and normalising the diagnostic stamp out of both sides. Its `action` is the row's verdict — `current`, `rewrite` (say "refresh needed"), `refuse` (see the cross-folder rule below) or `unknown` (an id this plugin cannot compose at all: report it as unknown, never as current and never as stale).

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
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from schedule_refresh import prompt_body_drift
print(json.dumps(prompt_body_drift(<the NORMALIZED task records>, plugin_version='<installed version>', workspace_basename='<this workspace's folder basename>', workspace_root='<this workspace's absolute path>')))
"
```

`prompt_body_drift` is the same compare over the whole readback at once, and it is what the "all current" line in step 7 must rest on. **Pass `workspace_basename`** — the bare folder name this session is mounted on, the same one registration composes with. A registered prompt that predates prompt-baking, or that is worded the way an older template worded it, names no workspace of its own; given the basename it is composed and judged on its content like any other, and without it the function reports it as drift rather than passing over it in silence. Either way it is never counted as current, which is the whole of what the line in step 7 claims. **Pass `workspace_root` too** — this session's own absolute workspace path. Scheduled tasks are machine-level, so a readback can contain a task registered to a different folder; given the root, such a record is reported as drift rather than judged on another workspace's content, which is the same answer `plan_prompt_refresh` gives it (`refuse`). It is never rewritten from here — see the cross-folder rule below — but the "all current" line must not be sayable over a prompt this session is not allowed to judge. **It now covers the SILENT tasks** — it used to skip every id with no chat orchestrator, and `maintenance`, the task that runs the entire background fire, is exactly such an id. That is why on 2026-09-16 this skill told the operator every prompt was current, in the same minute that his registered Maintenance prompt still typed a command deleted from the product two releases earlier.

**A refresh writes its receipt where the refresh happened — or it does not happen (SCHEDVIEW1 5.4, M's ruling).** Scheduled tasks are machine-level: one registry serves every workspace folder on the box, so this session can see, and rewrite, a task that belongs to a different folder. If `plan_prompt_refresh` returns `refuse`, say its `line` verbatim and change nothing — the task's own registered prompt names another workspace, and rewriting it from here would leave the change with no receipt in the workspace that actually changed (which is what happened on 2026-09-15: the refresh landed from a copy, and the live seat's ledger has no record of it to this day). Never "helpfully" refresh another folder's task and never write the receipt into this one instead.

6b. **RETIRED tasks — report them as retired, never as stale or missing (SPEC BRIEFMERGE §E / LIFECYCLE1 / EOD2).** Membership is `schedule_config.RETIRED_TASKS`, read at run time — never a name typed here. Three branches, and none is a failure:

   - **ELIMINATED and still registered on this machine** (`upcoming-meetings` on any workspace set up before the merge; `pulse` since LIFECYCLE1) → the row reads `retired`, carries `schedule_config.retirement_line(task_id)` so the customer can see where the work went, and is EXCLUDED from the current/stale tally. Verify mode never proposes refreshing it and never disables it — the retirement offer belongs to the update bridge and the customer's own `pause` (propose, never silent).
   - **RENAMED and still registered** (`past-meetings` since EOD2 — its row carries `renamed_to`; test with `schedule_config.is_renamed_task(task_id)`) → this one is a LIVE chat wearing an old name, so grade it like any live chat: check its bootloader, COUNT it in the tally, and say `refresh needed` when it is stale. Refreshing it is correct and load-bearing — it is what keeps the evening close current on a machine that has not taken the rename. Carry `retirement_line(task_id)` beside the row so the new name is visible, and never disable it.
   - **Not registered** → say nothing at all. A retired task's absence is normal, permanently. That includes `end-of-day` reading as absent on a machine whose `past-meetings` is registered — that machine HAS its evening chat, under the predecessor id. `schedule_config.is_task_served("end-of-day", registered_ids)` is the check to use; a bare `"end-of-day" in registered_ids` would report a missing chat that fires tonight.

   ```
   Command Room scheduled-task verification:

   Plugin: v[X] installed.

   ✓ morning-brief           bootloader current (registered under v[X])
   ✓ inbox                   bootloader current (registered under v[X])
   ✗ past-meetings           bootloader stale — refresh needed
                             (your End of Day chat, under its old name)
   ✓ maintenance             background prompt current
   ⊘ upcoming-meetings       retired — its meeting prep now runs inside your Morning Brief

   Orchestrator files on disk: all carry the current contract.

   [N] of [M] current. Say `set up command room schedules` to refresh the rest.
   ```

   (Illustrative shape only — render every row, both counts, and the plugin version from what you just read. Never a literal from this example.)

7. If EVERYTHING is current, confirm in one line: `All [N] tasks current under plugin v[X] — run any task with confidence.` If ANY are stale or missing, the report ends with the explicit instruction to run the refresh trigger. A retired row never blocks that "all current" line.

   **That line is only sayable when `prompt_body_drift` came back EMPTY over the whole readback** — chat tasks and silent tasks together. It is a claim about every registered prompt, so it has to be read from the compare that looks at every registered prompt; a per-row impression, a line count, or a version stamp is not that compare. If `drift` is non-empty, name the tasks it returns and end with the refresh instruction, whatever the stamps say.

**Do NOT update any prompt during verify-only mode.** This is observability, not mutation.

Verification mode is the diagnostic version of the install ritual — explicit visibility into which prompts are current BEFORE fire-and-find-out. (For fired-recency — "did they actually RUN" — that's the watchdog: say `system health`.)

---

# enable-command-room-schedules (M1, 2026-05-23)

The schedule-setup skill. Configures **7 topic-specific persistent chats** + a one-time **historical backfill** sweep, registered through the schedule backend seam. Each chat = 1 stable taskId = 1 chat that keeps its own history, accumulating turns over time.

**On a fresh-install workspace only the `FIRST_INSTALL_TASK_IDS` subset fires automatically** — `morning-brief`, `end-of-day`, `inbox`, `friday-wrap` (plus the silent `maintenance` task, registered via Step 1.D). The rest (`waiting-on`, `my-plate`) get added later via operator-driven follow-up sessions when accumulated workspace signal makes them useful. Read the set from `schedule_config.FIRST_INSTALL_TASK_IDS` rather than this sentence — it is the registry that decides. (`pulse` was in the later-add group until LIFECYCLE1 retired it, and `upcoming-meetings` was first-install until BRIEFMERGE retired it — see the RETIRED rows in `ORCHESTRATOR_MAP`.) (CTS1: `waiting-on` + `my-plate` are the split successors of the retired `commitments` chat — an existing customer with `commitments` registered gets both via the Phase 1 migration table, never a fresh-install auto-add.) (EOD2: `end-of-day` is `past-meetings` RENAMED — it inherits the slot rather than adding one, so the fresh-install chat count is still 4. An existing workspace's `past-meetings` is never auto-swapped; see the Phase 3 rename fence.)

## Phase 0.5 — Substantive explainer (first-time schedule setup)

**The guard comes first, here too (SPEC_NIGHTM1_LANES §7, COPY1).** Phase 0 below opens with `schedule_config.scheduler_availability(...)` and stops the whole skill when the answer is no. This phase runs BEFORE Phase 0, so it asks the same question first and stops on the same answer: an explainer about chats that cannot be registered is a promise, and the refusal sentence is the only thing this skill may say on that seat. Run the guard, and on `available: False` say its `line` and STOP — no explainer, no pre-check listing, no registration.

When this skill fires the **first time the customer sets up their schedules** on a seat where schedules CAN be set up, surface the substantive vanilla-vs-Command-Room explainer below BEFORE Phase 0 workspace-discovery runs. Because Phase 0.C's full `FIRST_INSTALL` detection hasn't run yet at this point, use this **lightweight pre-check** to decide: read the registered set through the seam (`schedule_backend.plan_list`, executed and normalised — the same records every other phase reads) and check whether ANY Command Room taskId (any key of `ORCHESTRATOR_MAP` or the `SILENT_TASKS` registry, or any legacy `cr-*` id) is registered. None registered → treat as first-time and show the explainer. Any registered → skip it (Phase 0.C later makes the authoritative first-install call for registration purposes). This is the education beat that gives the customer the "why" behind the 5 they're about to authorize.

> **Decoupled from onboarding (Command Room build, 2026-06).** Onboarding no longer opens a parallel "Chat 2" to fire this skill — scheduled-task generation was stripped from onboarding. The customer now reaches this skill by running `set up command room schedules` in a fresh chat whenever they're ready (onboarding's Phase 6 points them here). So this explainer fires on that first opt-in, regardless of whether onboarding is complete — it is no longer gated on an onboarding parent context.

Skip this phase if the skill fires from `command-room-update-bridge` post-install (silent registration; no customer in the chat), from a re-run (`FIRST_INSTALL = False` per Phase 0.C detection — customer already knows), or from any explicit calibration trigger (`change my schedule`, etc.).

Read `workspace.brain_name` from the customer's entities.json if available — substitute for `[BrainName]` below. If not yet set (entities.json not seeded yet because onboarding is still mid-flight), default to "Penelope."

**Surface this verbatim (one chat message):**

> *"Scheduled tasks are how [BrainName] reaches out to you — she starts the conversation instead of you having to remember to ask. Each one is a chat that runs on its own schedule and produces output you read like any other conversation.*
>
> *You can set up scheduled tasks in the Claude app without Command Room. The reason they're significantly more useful with Command Room is everything [BrainName] reads when she runs one.*
>
> ***A vanilla scheduled task starts cold.*** *It asks the AI to do something with no memory of who you are. Each run starts from zero — you'd have to re-explain your business, your people, your priorities every time.*
>
> ***A Command Room scheduled task starts with full context loaded.*** *[BrainName] walks into every run knowing: who you are, the companies and people in your workspace, your writing patterns (learned from your sent emails), every decision you've logged, every commitment captured from your calls and emails, and the current state of each of your projects.*
>
> ***Practical difference.*** *A vanilla morning brief gives you a generic 'here's your calendar' rundown. Your Command Room morning brief gives you 'you have [Person] at 2pm — he hasn't sent you anything in 28 days, you owe him the Q2 review since Wednesday, here's the opening line that lands hardest given your last 3 conversations.' Same prompt, completely different output, because [BrainName] is reading from everything she already knows about your business.*
>
> ***The compounding effect.*** *Every meeting you process, every decision you log, every follow-up you send adds to what she knows. The longer you use Command Room, the more context exists, the sharper every scheduled task gets.*
>
> ***Future possibilities.*** *I'm setting up your scheduled tasks now — these cover the daily ritual. Later you can add more yourself or with [Operator]. Examples: a Monday morning prep specifically for your weekly [recurring 1:1] / first-of-the-month investor update draft / pre-call brief that runs 30 min before any meeting with [important person]. One more you can switch on any time through your schedule settings: a **monthly KPI scorecard** — a one-page how-are-we-tracking-against-targets read that lands on the 1st for the prior month, off until you ask for it. [BrainName] can also propose tasks based on patterns she notices.*

**Opt-in monthly scorecard job (SPEC OUT7 — PROPOSE, never auto-register).** The `monthly-scorecard` job lives in `maintenance_dispatcher.OPTIONAL_JOBS` and is OFF by default — this skill NEVER registers it as part of the first-install set (it is not in `FIRST_INSTALL_TASK_IDS` and rides inside the already-authorized `maintenance` task, so turning it on registers nothing and asks the customer for nothing). Offer it once, in plain English, as above; on an explicit yes, turn it on the same way change-schedule does — call `schedule_config.set_maintenance_job_enabled`, the ONE writer for a per-job switch, then confirm in one line. Never open `entities.json` and set the key by hand here: the function is what makes the write locked, bumps the file's own `version` / `last_writer`, logs the `schedule_config_changed` audit row through `log_schedule_config_change`, and writes nothing at all when the stored value already says yes. Two writers for one act is how those four properties drift apart (DOORS1 fix round 2, seam 5 — this file was the last place under `skills/` still naming the raw key, and its "the same way change-schedule does" clause had gone false: change-schedule calls the function). The runnable call is the block directly below the explainer — do not paste it into the customer message.

Absent that explicit confirmation it stays inert (the dispatcher never surfaces it as due). It renders the prior month's KPI scorecard via `shared/scripts/scorecard.py` at the first maintenance fire on/after the 1st and self-limits to monthly via its own `pack_run` receipt.
>
> ***Registering your chats now:***
>
> *• Morning Brief (7 AM weekdays — it preps today's meetings first, then writes)*
> *• Inbox (7:15 AM weekdays)*
> *• End of Day (5 PM weekdays)*
> *• Friday Wrap (1 PM Fridays)*
>
> *These times are the defaults — say `change my schedule` to move any of them.*
>
> *...registering...*
> *Registered. They'll run on their own on the cadence above, and they run without stopping to ask you for approvals. Want to see one right now? Say its name — `morning briefing` — and you'll get real output immediately, exactly what it produces on schedule."*

**The opt-in scorecard switch (run ONLY on an explicit yes).** One call to the one writer, and stop. Self-contained on purpose: shell variables set in one fenced block do not survive into the next, so a block that inherits `PLUGIN_ROOT` or `WORKSPACE` runs in the wrong directory against an empty path (DOORS1 fix round 2, review finding R-F1 — the same defect, found in change-schedule's own copy of this block).

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
cd "$PLUGIN_ROOT" && python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from schedule_config import set_maintenance_job_enabled
res = set_maintenance_job_enabled('$WORKSPACE', 'monthly-scorecard', enabled=True,
                                  source_skill='enable-command-room-schedules')
print(res)
"
```

**Customer-facing task-name vs registered taskId mapping.** The customer reads the DISPLAY names above — the same names their own scheduled chats carry. The actual registered taskIds are: `morning-brief` ("Morning Brief - Command Room") / `end-of-day` ("End of Day - Command Room") / `inbox` ("Inbox - Command Room") / `friday-wrap` ("Friday Wrap - Command Room"). Never surface a taskId or internal skill ID (`inbox-triage`, `weekly-recap`) in customer copy; the canonical taskIds stay back-compat-stable in the registration layer only. (The bullet list is illustrative — render the names and times from `load_schedule_config()` over the set this run actually registers, per the anti-drift note below.)

**Render the explainer fire-times FROM `load_schedule_config()` — do not trust the hardcoded copy.** The bullet list above shows the current `DEFAULT_SCHEDULES` values (`shared/scripts/schedule_config.py`) for reference, but before speaking them, call `load_schedule_config(entities_json_path)` and read each task's `label` (e.g. `"7 AM weekdays"`) so the times you state ALWAYS match what Phase 2 actually registers — including any per-workspace `schedule_config` overrides the operator has already set. Names come from `task_display_name()` for the same reason. This is the anti-drift contract: the explainer copy and the registered cron can never disagree because both come from the same source. (Pre-FIX1 hand-maintained copy drifted — see references/HISTORY.md § Pre-FIX1.) **EOD2 is the reason this is a contract and not a nicety:** the sample bullets said "Past Meetings (5 PM weekdays)" for many releases, and a hand-maintained bullet list is exactly what would have kept saying it after the registry stopped agreeing. A pin over these bullets vs the registry lives in `tests/run_eod2_registration_test.py`.

**OPERATOR (verbal, if present when the explainer lands):** *"Take a minute on that. The 'full context loaded' point is the most important thing here — it's why this stack is different from any of the AI tools you've tried. Anything jump out?"*

~60–90 sec of optional operator-customer discussion. When the customer is setting schedules up on their own (the common case post-onboarding), no operator cue is needed — they read the explainer, the 5 register, and saying any chat's name shows its output immediately.

After this phase, proceed to Phase 0 (workspace discovery).

---

## v2.10.2 changes from v2.9.x / v2.10.1

Task renames, the commitments merge, the historical-backfill chunk tasks, and the chat-output-rules move are recorded in references/HISTORY.md § v2.10.2. The operative legacy→current taskId mapping lives in the Phase 1 migration table below.

## Verified contract (investigated 2026-04-28; the two app-specific lines re-read for the merged app 2026-09-19)

- API: no tool is named here. Registration goes through `backend.plan_create(...)` and the plan it returns (see "How every scheduler call is made now"). The seam owns the argument names on both backends.
- Cron: 5-field, always the customer's **LOCAL** time. Step+range syntax supported. The seam converts to whatever the backend registers in and stores the offset it used, so you always hand it the local expression.
- One-shot: pass `run_once_at="<ISO>"` to `plan_create` instead of a cron. Used for historical backfill chunks. The seam spells it for whichever backend is live.
- Prompt: arbitrary chat string, NOT a skill trigger. Each fire = fresh Claude session.
- Jitter: 60-400 second deterministic dispatch jitter.
- Persistence: filesystem + events.jsonl only. No in-memory state across runs.
- 1 taskId = 1 persistent chat thread, wherever the live backend lists its scheduled chats. Each fire appends a turn to that thread.
- **First fire (2026-09-19, re-read):** on the older desktop app every new taskId blocked once on a manual tool-permission grant, authorized from the task's own row; later fires ran on their own. On the merged app there is no such step — the chat asks for the workspace folder itself (the bootloader's Step 0) and runs with approvals off. This skill performs no authorization ritual on either app; Phase 5 teaches the one folder setting instead.

## Phase 0 — Workspace discovery + customer confirmation (v2.14.26+)

### Step 0.0 — Can this seat have schedules at all? (SPEC_NIGHTM1_LANES §7, COPY1 — FIRST, before anything else in this skill)

**This is the first thing this skill does on any run.** Before the workspace hunt, before any listing, before a single registration call:

```python
import sys
sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
from schedule_config import scheduler_availability
avail = scheduler_availability(<this session's tool names>, workspace_root=WORKSPACE,
                               abs_path=DEVICE)
```

`DEVICE` is the entry in `get_device_info`'s `connectedFolders` whose last path segment is the workspace's folder name (the same value the preamble exports as `CR_DEVICE_WORKSPACE`); on an older sandbox seat or a Code session on the customer's own computer it is the workspace's own path. On the merged app's scheduler the guard also asks whether THIS chat can name the account it would register the chats for (`schedule_config.registration_pair`, derived from this process's own account, never read from the synced identity file). A chat that cannot is refused with `reason: no_account` and its own sentence — it says where scheduled chats are set up and what still works by name, and never invites the phrase back.

`available: True` → carry on with Step 0.A below; the rest of this skill is unchanged and the guard has cost one function call.

`available: False` → do exactly three things, in this order, and then STOP:

1. Say `avail["line"]` — the ONE paragraph, verbatim, as the whole reply. It already carries the words that produce each chat on demand; do not add a second explanation, do not offer to install anything, and do not propose a workaround.
2. Record it once: `workspace_access.py plan run_writer --json '{"name": "schedule_config:write_schedule_skipped", "args": {"workspace_root": "<WS>", "reason": "<avail reason>"}}'` — render it and paste what it prints, like every other `plan` form here (on an older seat or a local Code session the printed line runs in this shell). One row per workspace-local day, so a seat that asks five times this week leaves five readable facts, not five hundred.
3. Stop. **No plan is executed, no task is registered, no `schedule_created` is written, no `workspace_config` is touched, and no install ritual is surfaced.** A registration into a scheduler nothing reaches is eight silent dead fires a day (gap analysis §0.9) — the refusal is the feature.

The reason this lives at the top of Phase 0 rather than inside Phase 1's registration loop: the customer asked one question ("set my schedules up"), and a skill that discovers a workspace, composes seven prompts and THEN says it cannot register has already spent the customer's attention on a no.

**Why this phase exists:** on the older desktop app there is no way to declare which folder a chat belongs to — the diagnostic 2026-05-06 confirmed the parameter is silently dropped — so folder binding is implicit at fire time. On a merged seat the folder IS declarable and the chat asks for it itself at fire time, which is what `<WORKSPACE_ABSOLUTE_PATH>` is for. v2.14.26 works around this by baking the customer-confirmed workspace folder's basename into each task's bootloader at registration time.

This phase runs FIRST, before any task registration. It produces the `WORKSPACE_BASENAME` value used in Phase 1's bootloader composition.

### Step 0.0R: the runtime beside the folder is this plugin's (merged seat only; BRIDGE3, F-T2-9)

Runs after Step 0.0 answered `available: True` and before Step 0.A, and only when `CR_ENV` is `merged_cloud`; every other seat skips it without a word. A runtime that lags the plugin refuses the helpers this skill needs, so it is brought current here or the skill stops here.

1. Resolve as the Access preamble says (`workspace_access.py discover`, its block run where the folder is) and keep the JSON it printed as `D`. Then compare it with this plugin, in this chat's own shell, one call:
   `python3 "${CLAUDE_PLUGIN_ROOT:?}/shared/scripts/workspace_access.py" discover --compare '<D>'`
   It answers `D` plus `plugin_manifest_sha`, `runtime_lags` and `lags_line`. When the call prints no JSON object (a folder path whose quote breaks the line, or any other failure), treat it as `runtime_lags: true`.
2. `runtime_lags: false` → carry on with Step 0.A.
3. `runtime_lags: true` → restage once, with the update bridge's own install step (its Phase 4.6 Step 2), exactly:
   `python3 "${CLAUDE_PLUGIN_ROOT:?}/shared/scripts/workspace_access.py" install --out <scratch dir> --ws "<WS from discover>"`
   then commit the zip where its plan says and run what `workspace_access.py verify` prints, as that step does. Then resolve and compare again (step 1).
4. Still `runtime_lags: true` → say `lags_line` (it is `workspace_access.RUNTIME_LAGS_LINE`), verbatim, as the whole reply, and STOP: nothing is listed, planned or registered, and nothing is written.
5. **A refused helper is a STOP, on every step of this skill (R-WALK-5, restated here).** A door answer `ok: false` with `reason: not_allowed_helper` or `not_allowed_writer` ends the run: say the envelope's `line` when it carries one, else `lags_line`, as the whole reply. Never run the refused helper another way (a one-line python body, an import in this chat's shell, the plugin's own copy of the module): the door refused it because the runtime beside the folder does not carry it, and a planner run by hand is how a second computer was told its chats were set up with no declaration read (walk row 3).

**Step 0.A — Discover candidate workspaces.**

```bash
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
echo "SESSION_DIR=$SESSION_DIR"
find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type f -name "events.jsonl" -path "*/_hq/data/*" -print 2>/dev/null | while read f; do
  WS=$(dirname "$(dirname "$(dirname "$f")")")
  BASENAME=$(basename "$WS")
  MTIME=$(stat -c "%y" "$f" | cut -d'.' -f1)
  EVENTS_COUNT=$(wc -l < "$f")
  echo "CANDIDATE basename=$BASENAME path=$WS last_event=$MTIME events_count=$EVENTS_COUNT"
done
```

Each `CANDIDATE` line is a workspace the customer could bind their tasks to. The basename is what gets baked into each bootloader; the full path + last-event timestamp + events count help the customer decide which is which (the more recently-active workspace is almost always the right one).

**Step 0.B — Customer confirmation flow.**

Three branches based on candidate count:

- **0 candidates:** no workspace folder is attached to this chat right now (or none has the canonical `_hq/data/events.jsonl` layout). Surface plain English and abort registration:

  > *"I can't find a Command Room workspace folder from this chat. Open a new chat with your Command Room folder attached and say `set up command room schedules` again."*

  Do NOT register tasks. The bootloaders would have nothing to bind to.

- **1 candidate:** show + confirm:

  > *"I'll set your scheduled chats up on this workspace: `<workspace folder name>` (at `<full_path>`, last activity <MTIME>). Look right? Say `yes`, or paste a different folder path if this isn't the one."*

  Wait for the reply — there is no timeout in a chat turn. If the reply is anything other than a different path or an objection, treat it as confirmation and proceed. Save `WORKSPACE_BASENAME = <BASENAME>` for Phase 1.B substitution (the "workspace folder name" shown to the customer IS the basename — show it as a plain folder name, never labeled "basename", and never show raw event counts or `_hq/...` paths).

- **2+ candidates:** show numbered list, ask customer to pick:

  > *"I see <N> Command Room workspaces from this chat. Which one should your scheduled chats use?*
  >
  > *1) `<workspace folder name 1>` — `<full_path1>` (last activity <MTIME1>)*
  > *2) `<workspace folder name 2>` — `<full_path2>` (last activity <MTIME2>)*
  > *...*
  >
  > *Reply with `1`, `2`, etc. — or paste a different folder path."*

  Store the customer's choice as `WORKSPACE_BASENAME` for Phase 1.B substitution.

**Step 0.C — Detect first install vs re-run + persist the choice.**

**First-install detection (onboarding-v2 / 2026-05-17+):** read `<chosen_workspace>/_hq/workspace_config.json`. The workspace is treated as **first-install** if any of:

- File doesn't exist.
- File exists but `registered_taskIds` is missing, null, or `[]`.

If first-install, set the local variable `FIRST_INSTALL = True` and use `FIRST_INSTALL_TASK_IDS` (from `shared/scripts/schedule_config.py` — read the frozenset, never a literal typed here; as of EOD2 2026-08-16 it is the chats `morning-brief` / `end-of-day` / `inbox` / `friday-wrap` plus the silent `maintenance` — `upcoming-meetings` having retired into the morning-brief fire, and `past-meetings` having been renamed `end-of-day`) as the registration set. The remaining default tasks are SKIPPED on first install — they get added later through operator-led follow-up sessions once accumulated workspace signal makes them useful.

If NOT first-install (`registered_taskIds` is populated), set `FIRST_INSTALL = False`. The skill enters Phase 6 management flow (`add` / `change` / `remove` / `reset`) — do NOT silently delete or disable tasks the customer already has. Existing customers with all 5, 6, or 7 registered keep what they have.

After detection, write or update `<chosen_workspace>/_hq/workspace_config.json`:

```json
{
  "workspace_root": "<absolute path>",
  "workspace_basename": "<BASENAME>",
  "registered_at": "<ISO timestamp>",
  "first_install": true,
  "registered_taskIds": ["morning-brief", "end-of-day", "inbox", "friday-wrap", "maintenance"]
}
```

For re-runs, `registered_taskIds` reflects the actual set the skill ended up registering at the end of Phase 3 (preserves whatever existed + adds anything new). The `first_install` flag is set true ONLY on the first registration; subsequent re-runs leave it true for audit history but Phase 3 reads `len(registered_taskIds_before_this_run)` to decide first-install vs not.

Future re-runs read this file first; if present, pre-select that workspace and only ask for confirmation if a different workspace is now connected. Idempotent.

**Step 0.D — Switching workspaces (lifecycle command).**

The customer-facing flow for switching to a new workspace is just: re-run `set up command room schedules`. The skill detects the new candidate set, asks the customer to confirm or pick a different workspace, re-bakes the basename into each bootloader, and re-registers through `backend.plan_update`. No separate "rebind" command needed — the regular setup command IS the rebind command. Surface this guidance in the install summary at the end.


### How every scheduler call is made now (BOOT3, 2026-09-19 — read this before Phase 1)

Command Room now talks to **two** schedulers: the older desktop app's, and the merged Claude app's. They have different tool names, different argument names, and different ideas about what a cron expression means. So this skill stops naming a tool at all. Every call is made in two beats:

1. **Ask the seam for a plan.** `shared/scripts/schedule_backend.py` picks the backend from the tools you can actually see — never from an environment variable — and returns either a `CallPlan` (`{tool, args, task_id, after, note}`) or a `Refusal` (`{line, reason_code}`).

   ```python
   import schedule_backend as sb
   backend, found = sb.select_backend(<the tool list you can see>)
   plan = backend.plan_create(task_id, prompt=<composed bootloader>, cron_local=<local cron>,
                              tz_name=<workspace timezone>, notify=<flag>,
                              workspace_basename=<basename>, folders=[<workspace absolute path>])
   ```

2. **Execute it.** If the seam handed back a `Refusal`, say its `line` — one sentence, exactly as written — and STOP: no call, no receipt, no config write. If it handed back a `CallPlan`, call `plan.tool(**plan.args)`, then make every call in `plan.after` in order. The `after` list is where the receipt lives, so a receipt cannot be forgotten by anyone following the plan.

   **The first `after` entry on a create is `schedule_backend.record_trigger_map`, and it is not optional.** It lands where the folder is, through the write door. **Both `after` entries of a create land in ONE door call (SCHEDFAST1, #101):** render `workspace_access.py plan run_writer --json '<payload>'` with the payload `schedule_backend.registration_payload(plan.after, trigger_id=<the id the call returned>)` composes — the writer `schedule_backend:record_registration`, which writes the row exactly as `schedule_backend:record_trigger_row` does (the trigger id filled in, no `folders`: the door fences paths, so the row's folder is this seat's forwarded `CR_DEVICE_WORKSPACE`) and then appends the second entry, `event:schedule_created`, through the same gate (the entry's data with the returned trigger id, no hand `id`/`timestamp`) — and paste what it prints. One call, both receipts; never a separate `plan append_jsonl` for the event on a create. (The older desktop app's create plans no row entry — its `after` is the event alone — and there the event goes through `plan append_jsonl` exactly as before; `registration_payload` refuses an `after` with no row.) A cron change has only the row entry and renders `trigger_row_payload(plan.after[0][1], ...)` for `record_trigger_row` as before (`trigger_row_payload` is what `registration_payload` builds on).

   That row is how every later read knows which chat a registered trigger is — the merged app's listing has no taskId of its own, only a name a customer can edit. Without it, `backend.normalize` cannot name the chat, the live time reads back in world time instead of the customer's, and the nightly daylight-saving job has nothing to walk. A cron change carries the same call with `merge=True`, which moves the two values that changed and keeps the folder and trigger id the registration wrote.

The verbs, and what each one is for:

| Verb | What it plans |
|---|---|
| `backend.plan_list()` | read what is registered |
| `backend.plan_create(task_id, ...)` | register a chat that is not registered yet |
| `backend.plan_update(task_id, ...)` | change a registered chat's prompt, cron or on/off state |
| `backend.plan_delete(task_id)` | remove one of Command Room's own one-shots or probes — refuses anything a customer can see |
| `backend.plan_fire(task_id, ...)` | run a registered chat once, now |
| `backend.normalize(raw, workspace_root=…)` | turn whatever the listing returned into the task records every reader here already expects, using the stored trigger map |
| `backend.verify(records, workspace_root=…, plugin_version=…)` | compare what is registered against what this workspace expects, including what a prompt refresh would do |

**`normalize` before you read, and always pass `workspace_root`.** The merged app returns triggers, not tasks: different field names, a UTC cron, and sometimes no prompt text at all. `normalize` converts them into the `taskId` / `enabled` / `lastRunAt` / `cronExpression` / `prompt` shape every step below reads, with the cron back in the customer's own time. `workspace_root` is how it reaches the stored `triggers` map, which is the only thing that can say which chat a trigger is when its name has been edited, and the only thing that knows the offset the cron was registered on. Without it a listing comes back thinner than it needs to be. A record whose `prompt` comes back `None` means "the listing did not carry the text" — that is unknown, never stale, and nothing in this skill may treat it as a reason to rewrite a prompt.

**The workspace's absolute path.** On a merged seat the registration needs the workspace folder's absolute path on the customer's computer — it is what the chat asks for at fire time and what the bootloader's `<WORKSPACE_ABSOLUTE_PATH>` becomes. Read it from `get_device_info`'s `connectedFolders`, or from the trigger record's `derived_state.folders[0]` when re-registering. Never type one from memory.

**Registration may be switched off.** `sb.registration_gate()` reads a shipped config. While it says blocked, `plan_create` and a cron `plan_update` come back as a `Refusal` and this skill says that one sentence and stops — reading, verifying and re-running an existing chat all still work.


## Phase 1 — Detect current schedule state + migrate legacy

Execute `backend.plan_list()` and `normalize` the result. Build set of `{taskId, cron, prompt}`.

**Vantage guard — before any registration (v4.5.2 R3 — F-40, widened by TRUTH1 2026-09-20):** if the list comes back with zero Command Room tasks, run `task_watchdog.detect_registry_vantage(ws, records, backend=<the selected backend's id, or None>, tools=<this session's tool names>)` (plugin `shared/scripts/`) before treating this as a fresh install. `records` is the literal `None` when `schedule_config.scheduler_availability` said there is no scheduler in this session — never `[]`. If it returns a finding, the workspace's own records say schedules were already set up, and an empty registry here means this session cannot see the scheduler that serves them. Registering now would create a duplicate task set in the wrong place (the F-38 double-fire class; in a cloud session the registrations land in a throwaway VM).

**Say `finding['line']` verbatim, and nothing of your own about why.** The line is already composed for the case; adding a paraphrase is how this step came to tell a merged seat to open a chat its app does not have. `finding['check']` says which case it is, and that is what decides whether this is a refusal or a migration prompt:

| `finding['check']` | what to do after saying the line |
|---|---|
| `registry_vantage` | BLOCKING. This seat has a machine-local scheduler and this chat is not looking at it — a second computer, or a remote session. Add one sentence of your own only to offer the deliberate second setup: if they say they ARE setting up a SECOND computer, continue; otherwise stop. Per-machine second setups are a real, supported case — never proceed silently. |
| `registry_vantage_cloud` | NOT a refusal — a migration prompt (gap analysis §5.3). The chats live in the older desktop app and this account cannot see them; the line already names the phrase that moves them when the gate is open. Continue into registration on the customer's word, and do not warn about duplicates: the old registry is not this one. |
| `scheduler_unreachable` | STOP. There is no scheduler in this session at all, so there is nothing to register into and nothing to duplicate. The line already says what still works when they ask. |

The finding also carries a `machine` field: it is evidence for this step, never text — no state of the line prints it (F16).

**Legacy task migration (v2.9-v2.10.1 → v2.10.2):**

For each legacy taskId found in the user's existing schedule, DISABLE it by executing the plan `backend.plan_update(task_id, enabled=False)` returns, and surface in the install summary as "migrated to [new name]":

**Every pause, disable or enable writes a config record (SPEC SCHED1 §0-4).** The moment an enable/disable plan lands (`backend.plan_update(task_id, enabled=...)`), call `schedule_config.log_schedule_config_change(<WORKSPACE>, [{'task_id': '<id>', 'cron': None, 'enabled': False}], source_skill='<this skill>')` — one call, the same single writer `change-schedule` uses, and never a hand-rolled event (the helper owns the shape). This is not bookkeeping: the lateness ledger READS `schedule_config_changed` to know that a slot older than the change was minted by the change and must never be scored (the F-51 phantom), so a pause nobody recorded leaves the ledger believing this task's newest config change is whatever came before it. The 2026-08-17 fold-in wrote three `schedule_created` events and no record at all for the two chats it paused.

| Legacy taskId | Action | New taskId |
|---|---|---|
| `cr-meetings-today` | **disable only** (BRIEFMERGE — `upcoming-meetings`, its successor, is retired; there is nothing to register) | — |
| `cr-inbox-pulse` | disable + register | `inbox` |
| `cr-commitment-nudge` | disable + register | `commitments` (merged) |
| `cr-commitment-chase` | disable + register | `commitments` (merged) |
| `cr-cracks-watch` | **disable only** (LIFECYCLE1 — `pulse`, its successor, is retired; there is nothing to register) | — |
| `cr-meetings-processed` | disable + register | `past-meetings` |
| `cr-refresh-workspace-map` | **disable** (v2.14.25 — task removed from active set; since Night M3 (RETIRE1) the sidebar dashboards are retired on every seat and its orchestrator file is deleted; surface "Removed the daily Workspace Map refresh — dashboards live in chat now.") | (none — task is gone) |
| `cr-upcoming-meetings` | **disable only** (v2.14.27 taskId rename, and since BRIEFMERGE the successor `upcoming-meetings` is retired too — do NOT disable-and-re-register; register nothing in its place) | — |
| `upcoming-meetings` | **retired, do NOT auto-disable** (BRIEFMERGE, RULED 2026-08-08 — its prep generation is now the morning brief's first leg. Membership is `schedule_config.RETIRED_TASKS`. A still-registered task is the customer's to switch off: the update bridge offers `schedule_config.retirement_line("upcoming-meetings")` once, and `change-schedule` treats the lingering registration as removable drift. Registration NEVER adds it back, on any path, including an explicit `add upcoming meetings`.) | — (folded into `morning-brief`) |
| `cr-inbox` | **disable** (v2.14.27 — taskId rename) | `inbox` |
| `cr-commitments` | **disable** (v2.14.27 — taskId rename) | `commitments` |
| `cr-dont-forget` | **disable** (v2.14.27 — taskId rename to align with display name "Pulse"; events.jsonl history at source_skill='cr-dont-forget' preserved as append-only history) | `pulse` |
| `cr-past-meetings` | **disable** (v2.14.27 — taskId rename) | `past-meetings` |
| `past-meetings` | **renamed, do NOT auto-disable and do NOT auto-replace** (EOD2, RULED 2026-08-16 — the 5 PM chat became the day's CLOSE in EOD1 and the id kept saying "meetings". Membership is `schedule_config.RETIRED_TASKS` with `renamed_to: "end-of-day"`. **Unlike every other row in this table there is nothing to migrate:** both ids map to the SAME orchestrator file, so a still-registered `past-meetings` keeps firing the real End of Day pack forever, at the same hour, with no degradation and no stub. Its prompt IS refreshed on a re-run — that is what keeps it current. `schedule_config.registration_target_set()` removes `end-of-day` from this run's target set while `past-meetings` is registered, so the successor is NEVER silently added beside it; the switch is offered once by the update bridge (`retirement_line("past-meetings")`) and taken by the customer's own `add end of day`, which registers the new id AND disables the old one in the same step. Registration never adds `past-meetings` back on any path.) | `end-of-day` (by PROPOSAL only) |
| `cr-folder-bind-test` | **disable** (v2.14.27 — Cowork diagnostic test task left over from 2026-05-06 Q10/Q11 round; safe to disable, never intended to fire) | (none — diagnostic artifact) |
| `cr-folder-bind-test-2` | **disable** (v2.14.27 — Cowork diagnostic test task left over from 2026-05-06 Q10/Q11 round; safe to disable, never intended to fire) | (none — diagnostic artifact) |
| `commitments` | **disable + register** (CTS1 §10.3, RULED 2026-07-16 — the daily Commitments chat split into two surfaces on fresh taskIds, the v2.14.27 pattern: Cowork derives the sidebar title FROM the taskId, so re-scoping would have left the sidebar saying "Commitments" forever. Register BOTH successors: `waiting-on` inherits the 8:30 slot AND any custom cron override the customer had on `commitments` — MOVE the override (write it under `waiting-on` in `workspace.schedule_config` and REMOVE the `commitments` key: `commitments` is no longer in DEFAULT_SCHEDULES, so a leftover key trips the watchdog's orphan-override scan forever); `my-plate` takes the 8:45 default. Surface the three §10.3 costs in the install summary: the old entry is switched off rather than removed and stays visible wherever schedules are listed (no delete API), old chat history stays in the old thread, and each new chat has to reach the workspace once before it can do real work.) | `waiting-on` + `my-plate` |
| `cleanup` | disable + register (MAINT1 — now a job inside the maintenance task; driven by `SUPERSEDED_BY` in `schedule_config.py`, data not prose) | `maintenance` |
| `reconcile-sent` | disable + register (MAINT1 — now the FIRST job at the 6:45 slot, still before the morning brief. A custom cron override on this taskId migrates onto the `maintenance` task cron — it was the one old silent task whose cadence maps 1:1) | `maintenance` |
| `monthly-report` | disable + register (MAINT1 — now a job, due at the first fire on/after the 1st) | `maintenance` |
| `weekly-insights` | disable + register (MAINT1 — now a job at the Sunday slot, ordered after cleanup) | `maintenance` |
| `session-sweep` | disable + register (MAINT1 — now a job, served once daily at the first fire) | `maintenance` |

**MAINT1 migration notes (idempotent, never deletes):** the five rows above are driven by the `SUPERSEDED_BY` map in `shared/scripts/schedule_config.py` — disable each superseded taskId found registered+enabled by executing `backend.plan_update(task_id, enabled=False)`, register `maintenance` once via Step 1.D. Re-runs converge on the same end state (disabling an already-disabled task is a no-op; a registered `maintenance` with a matching prompt is skipped). Custom cron overrides on the OTHER four old taskIds (`workspace.schedule_config`) cannot map onto a single task cron — leave the override in place (parity ignores superseded ids) and tell the customer in plain English which old time can't carry over (e.g. *"Your cleanup used to run at a custom time — the background upkeep now runs as one task; say `change my schedule` if you want to move it."*). Surface ONE plain-English migration line in the install summary: *"Your background upkeep now runs as one entry instead of five. The old background entries are switched off."*

**Every pause, disable or enable writes a config record (SPEC SCHED1 §0-4).** The moment an enable/disable plan lands (`backend.plan_update(task_id, enabled=...)`), call `schedule_config.log_schedule_config_change(<WORKSPACE>, [{'task_id': '<id>', 'cron': None, 'enabled': False}], source_skill='<this skill>')` — one call, the same single writer `change-schedule` uses, and never a hand-rolled event (the helper owns the shape). This is not bookkeeping: the lateness ledger READS `schedule_config_changed` to know that a slot older than the change was minted by the change and must never be scored (the F-51 phantom), so a pause nobody recorded leaves the ledger believing this task's newest config change is whatever came before it. The 2026-08-17 fold-in wrote three `schedule_created` events and no record at all for the two chats it paused.

(No delete API exists in the scheduled-tasks MCP; disable is the safe operation. Disabled tasks remain in the user's Scheduled section as historical reference but won't fire. v2.14.27 customers running `set up command room schedules` will see ~13 disabled tasks accumulate in their sidebar — surface this in the install summary so it's not a surprise. Filesystem surgery is the only way to make the sidebar truly clean: quit Cowork, edit `scheduled-tasks.json` to remove disabled entries, optionally delete the corresponding `Documents/Claude/Scheduled/<taskId>/` folders, restart.)

**Existing-taskId handling (v2.14.21+ — self-refresh with explicit verification):**

The canonical taskId → orchestrator-file mapping. **Use this dict literally — do NOT improvise filenames or display names from your own knowledge of the task list.** This dict MIRRORS `references/orchestrator-map.json`, which is the single machine-readable source of truth — the Step 1.A registration snippet below reads that same JSON, so the two can never drift. If you ever edit one, edit the JSON:

```python
ORCHESTRATOR_MAP = {
    "morning-brief":     "orchestrator-morning-brief.md",  # Wraps the morning-briefing skill. Registered on first install.
    "upcoming-meetings": "orchestrator-upcoming-meetings.md",  # RETIRED (BRIEFMERGE) — NEVER register, NEVER offer, NEVER count as missing. The row stays ONLY so a workspace that registered it before the retirement still resolves its bootloader; the file it points at is a retirement stub that explains itself and stops. `schedule_config.RETIRED_TASKS` is the membership test — never a name you remember. Historical events (source_skill='cr-upcoming-meetings' / 'upcoming-meetings') stay valid append-only history, and `prep_brief` receipts written under it keep the morning brief's no-prep detector honest forever.
    "inbox":             "orchestrator-inbox.md",
    "waiting-on":        "orchestrator-commitments.md",  # CTS1 Surface 1 — the re-scoped daily (things people owe the user + the confirm tail). Filename kept for events.jsonl source_skill back-compat (events keep source_skill='commitments' — same pattern as pulse below). NOT first-install; successor of the retired `commitments` taskId (Phase 1 migration table).
    "my-plate":          "orchestrator-my-plate.md",     # CTS1 Surface 2 — the owner-me act-list (Promised + Personal groups, one chat). NOT first-install; registers alongside waiting-on in the commitments migration.
    "pulse":             "orchestrator-dont-forget.md",   # RETIRED (LIFECYCLE1) — NEVER register, NEVER offer, NEVER count as missing. The row stays ONLY so a workspace that registered it before the retirement still resolves its bootloader; the file it points at is a retirement stub that explains itself and stops. `schedule_config.RETIRED_TASKS` is the membership test — never a name you remember. Historical events (source_skill='cr-dont-forget' / 'pulse') stay valid append-only history.
    "past-meetings":     "orchestrator-past-meetings.md",  # RENAMED to `end-of-day` (EOD2) — NOT eliminated. Never register it fresh, never offer it, never count it as missing. The row stays so a pre-rename registration resolves its bootloader, and the file it names is the LIVE End of Day orchestrator (not a stub): a workspace that ignores the offer keeps a fully working evening chat forever. `schedule_config.RETIRED_TASKS` is the membership test; `renamed_to` is what distinguishes this from pulse/upcoming-meetings. Historical events (source_skill='cr-past-meetings' / 'past-meetings') stay valid append-only history, and `end_of_day.TASK_ID` still WRITES this id so the day-close receipt series is continuous across the rename.
    "end-of-day":        "orchestrator-past-meetings.md",  # EOD2 — the successor id, deliberately pointed at the SAME file. The filename is baked into each registered prompt at registration time, so renaming the file would break every live `past-meetings` bootloader at its next fire, and a second file would fork one pack into two. One file, two ids, one pack. First-install; on an EXISTING workspace it is fenced out of the target set while `past-meetings` is still registered (see Phase 3).
    "friday-wrap":       "orchestrator-friday-wrap.md",   # NEW v3.11.0. Wraps the weekly-recap skill. Registered on first install. First weekly-rhythm task.
    "relationship-moves": "orchestrator-relationship-moves.md",  # REL1 — weekly Sunday outreach pack. NOT first-install (needs accumulated substrate).
    "commitment-triage": "orchestrator-commitment-triage.md",  # RETIRED — READINESS class (TASKRET1, M's ruling 2026-08-17). NEVER register, NEVER offer, NEVER count as missing. The row stays ONLY so a workspace that registered it before the retirement still resolves its bootloader until the update reaches it; the file it points at is a retirement stub that explains itself and stops. `schedule_config.RETIRED_TASKS` is the membership test, `retirement_class` the class test — never a name you remember. The ON-DEMAND skill (`triage my commitments`) is untouched and fully live.
    "staff-meeting":     "orchestrator-staff-meeting.md",  # LB1 R3 — weekly Monday Living Brain review. NOT first-install; propose-only later-add (never silently registered).
    "balance":           "orchestrator-balance.md",  # RETIRED — READINESS class (TASKRET1). Same rules as commitment-triage above: never register, never offer, never count as missing; the file is a retirement stub. BAL1 had already gated it on a declared personal calendar most workspaces never had, which is precisely the shape this class retires. The ON-DEMAND surface (`balance check`) is untouched and fully live.
    "pipeline-digest":   "orchestrator-pipeline-digest.md",  # RETIRED — READINESS class (TASKRET1). Same rules; the file is a retirement stub. It was the only one of the three with an automated offer path, and that path (`schedule_proposals.PROPOSAL_THRESHOLDS`) is deleted rather than gated. The ON-DEMAND pipeline report (`skills/pipeline-tracker/`) is untouched and fully live.
}
# Every row is a user-facing chat, and SIX of them are retired rows kept so a pre-retirement registration still resolves its bootloader. THREE CLASSES, and `schedule_config.retirement_class(task_id)` is the only correct way to tell them apart:
#   * ELIMINATED — `pulse` (LIFECYCLE1), `upcoming-meetings` (BRIEFMERGE). Files are retirement stubs. The removal is PROPOSED; the customer's `pause` is what switches it off.
#   * RENAMED — `past-meetings` (EOD2). Its file is the LIVE End of Day orchestrator, shared with the successor row — never a stub. The switch is PROPOSED; ignoring it costs nothing.
#   * READINESS — `commitment-triage`, `balance`, `pipeline-digest` (TASKRET1). Files are retirement stubs. This class is the ONE the update bridge APPLIES: it disables a live registration itself and says so in the update ack, because M ruled that asking per-task here would rebuild the register-then-nag pattern the retirement exists to end. The rationale is in the class block above `RETIRED_TASKS` in `shared/scripts/schedule_config.py`; nothing in it weakens the other two classes.
# Every retirement stub must carry the literal `OUTPUT CONTRACT` inside its first 2000 bytes — the bootloader's Step 2 grep window. A stub without it aborts the fire with a false "plugin may be partially installed or corrupted, please reinstall" message (BUG-9517); the `pulse` and `upcoming-meetings` stubs are the two shipped instances, and SPEC_RETIREGATE1 owns the general gate.
# The live set is `DEFAULT_SCHEDULES` minus `RETIRED_TASKS` — derive it, never count these lines. v2.14.27+ uses bare taskIds (no `cr-` prefix) so Cowork's sidebar title formatting renders cleanly: `inbox` → "Inbox", `waiting-on` → "Waiting on", etc. Pre-v2.14.27 used `cr-*` prefix which displayed as "Cr inbox" / "Cr commitments" — the cr- prefix looked like a typo in the title. cr-refresh-workspace-map was REMOVED in v2.14.25. friday-wrap ADDED in v3.11.0 — first weekly-rhythm scheduled task. CTS1: `commitments` RETIRED (disable per the Phase 1 migration table) and split into `waiting-on` + `my-plate`.
#
# First-install gating: on a FRESH workspace (workspace_config.json missing or empty registered_taskIds), only the subset in `shared/scripts/schedule_config.py FIRST_INSTALL_TASK_IDS` registers — read the frozenset at run time. The remaining later-add entries above (waiting-on, my-plate) stay in the map for re-runs / management flows but are NOT auto-registered day 1, and the retired rows (pulse, upcoming-meetings, commitment-triage, balance, pipeline-digest) are never registered by any path. See Phase 3 first-install branching. (The silent background work — the `maintenance` task, MAINT1 — is ALSO in FIRST_INSTALL_TASK_IDS and registers on first install, but via **Step 1.D below** as one loop over the `SILENT_TASKS` registry in `shared/scripts/schedule_config.py`, because it is not a chat-orchestrator and is intentionally absent from this ORCHESTRATOR_MAP.)
```

**Critical mismatch warnings:**

- **(v2.14.20 regression)** If you find a registered task with `taskId == "cr-pulse"` or `cr-dont-forget`, that's pre-v2.14.27 state — disable per the legacy migration table and register `pulse` per the new map.
- **(v2.14.27 rename)** If you find any of `cr-upcoming-meetings`, `cr-inbox`, `cr-commitments`, `cr-dont-forget`, or `cr-past-meetings` registered, those are v2.14.21-v2.14.26 taskIds — disable per the legacy migration table and register the bare-name equivalents (`inbox`, `past-meetings`, etc.) fresh. **Two exceptions, both retirements:** `cr-dont-forget` and `cr-upcoming-meetings` are disabled and NOTHING is registered in their place — their successors (`pulse`, `upcoming-meetings`) are in `schedule_config.RETIRED_TASKS`.
- **(stub regression)** If any registered task's prompt body is shorter than 1000 chars or doesn't contain `"OUTPUT CONTRACT (v2.13.0+ — MANDATORY)"` in its first 1500 characters AND doesn't contain `"# Scheduled task bootloader"` in its first 200 chars — that indicates a stub was registered instead of the canonical bootloader. Re-register fresh.

**Per-task processing (v2.14.24+ — bootloader pattern; run for every taskId in `ORCHESTRATOR_MAP`):**

**v2.14.24 architecture change.** Prior to v2.14.24, registration pinned the FULL canonical orchestrator body into Cowork's scheduled-tasks DB — fixing the v2.14.20 stub-improvisation bug but producing the stale-prompt drift bug (see references/HISTORY.md § v2.14.24).

v2.14.24+ pins a tiny ~50-line **bootloader** instead. The bootloader resolves `$PLUGIN_ROOT` at fire time, reads the canonical `orchestrator-<name>.md` from the currently-installed plugin via `bash cat`, and executes it verbatim. Plugin upgrades propagate automatically. Drift is structurally impossible.

The canonical bootloader template lives at `skills/enable-command-room-schedules/references/scheduled-task-bootloader.md`. Read its top-of-file commentary for the full design rationale — the live evidence (cr-bootloader-test fire, plugin UUID stability check, frontmatter doubling test) that drove each design choice.

**Step 1.A — Verify orchestrator files exist and carry the contract marker (still mandatory at registration time, even though the body itself is no longer pinned).**

The bootloader assumes the orchestrator files exist on disk and contain the OUTPUT CONTRACT marker. Registration verifies this BEFORE composing the bootloader, so a partial / corrupt plugin install can't quietly register bootloaders that will fail at every fire:

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
cd "$PLUGIN_ROOT" && python3 -c "
import json
from pathlib import Path
ref_dir = Path('skills/enable-command-room-schedules/references')
# Single source of truth — same JSON the prose ORCHESTRATOR_MAP above mirrors.
# Strip the _comment key; everything else is taskId -> orchestrator filename.
ORCHESTRATOR_MAP = {k: v for k, v in json.loads((ref_dir / 'orchestrator-map.json').read_text(encoding='utf-8')).items() if not k.startswith('_')}
CONTRACT_MARKER = 'OUTPUT CONTRACT (v2.13.0+ — MANDATORY)'
out = {}
for task_id, fname in ORCHESTRATOR_MAP.items():
    fpath = ref_dir / fname
    assert fpath.exists(), f'{fname} missing — plugin install may be incomplete; ABORT registration'
    body = fpath.read_text(encoding='utf-8')
    # All 7 tasks are chat-emitting; all must carry the v2.13.0 OUTPUT CONTRACT preamble.
    assert CONTRACT_MARKER in body[:1500], f'{fname} is missing the v2.13.0 OUTPUT CONTRACT preamble in its first 1500 chars — file is stale or corrupt; ABORT registration'
    assert len(body) >= 1500, f'{fname} body is only {len(body)} chars — too short to be a real orchestrator; ABORT'
    out[task_id] = len(body)
print('orchestrator-files-verified:', json.dumps(out))
"
```

If any assertion fails, ABORT the whole skill with a plain-English error: *"One of the Command Room files isn't readable right now. Reinstalling Command Room usually fixes this — ask whoever set it up for you, or reinstall it the same way it was first installed, then say `set up command room schedules` again."* Never register a partial set.

**Step 1.B — Compose every chat's prompt for THIS seat, with the one composer (SCHEDREG1, coordinator decision D-1; ruling R-RW3-3).**

Nothing in this skill substitutes a placeholder by hand any more, and no map of orchestrator files is typed here. The prompt each chat is registered with comes from ONE composer — `schedule_refresh.compose_bootloader_body` for a chat (all seven placeholders filled, the writer pair baked; `references/orchestrator-map.json` is its map, so `staff-meeting` and every other row resolve) and `schedule_config.compose_silent_task_prompt` for a silent task (Step 1.D) — and the whole registration is planned by ONE call that uses them: `schedule_backend.plan_registration`. The drift compare, the refresh and the dev renderer compose through the same composer, so a chat registered here reads as current everywhere and a second run changes nothing.

The seat's inputs, each read from THIS seat — never from another computer's render, never typed from memory:

- `DEVICE` — from Step 0.0: the connected folder whose name is the workspace's (the path on the customer's computer; the chat asks for exactly this folder at fire time).
- `TRIGGER_MAP` — the stored trigger rows, read where the folder is: `workspace_access.py plan run_helper --json '{"name": "schedule_backend:read_trigger_map", "args": {"workspace_root": "<WS>"}}'`, the `result` of the envelope.
- `RECORDS` — Phase 1's normalized listing (`backend.normalize(raw, trigger_map=TRIGGER_MAP)`).
- `DECLARATION` — the workspace's declared scheduled writer, read where the folder is: `workspace_access.py plan run_helper --json '{"name": "schedule_config:scheduled_writer", "args": {"workspace_root": "<WS>"}}'`, the `result` of the envelope (`null` when nothing is declared). It is what lets a refusal name the computer that holds the chats.
- `TASK_IDS` — Phase 3's registration set plus the `SILENT_TASKS` ids; `CRONS` — the customer's cron per task from Phase 2's `load_schedule_config()`.
- The writer pair is NOT an input: `plan_registration` derives it with `writer_identity.bakeable_pair(DEVICE)` from this process's own account. A seat that cannot derive one never gets past Step 0.0, and on the merged app's scheduler no chat is ever registered without one.

```python
import sys
sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
import schedule_backend as sb
planned = sb.plan_registration(<this session's tool names>, workspace_root=WS,
                               abs_path=DEVICE, task_ids=TASK_IDS, registered=RECORDS,
                               trigger_map=TRIGGER_MAP, plugin_version=<plugin.json version>,
                               tz_name=<workspace timezone>, crons=CRONS,
                               declaration=DECLARATION)
```

What comes back, and what to do with each part (Phase 3 executes it):

- `planned["ok"]` False → say `planned["line"]` once, as the whole reply, and stop — the same refusal Step 0.0 would have said.
- `planned["reason"]` is `bound_elsewhere` (BRIDGE2, R-WALK-3) → another computer holds this workspace's scheduled chats. Every registered chat is matched to a computer by where its folder lives ON that computer (a digest of the path, `schedule_config.device_digest`), never by the folder's name, so two computers of one account with the same folder name are told apart. Say `planned["line"]` once as the whole reply (it names the computer that holds them) and stop: nothing is registered, nothing is declared (Step 1.B2 does not run), and another computer's chats are never updated or rewritten from here. `planned["foreign"]` lists the chats that live elsewhere; it is for this step's reasoning, never for the reply.
- `planned["creates"]` — one `CallPlan` per chat not registered yet: the merged app's `create_trigger` with `requires_local_device: true`, `folders: [DEVICE]`, `permission_mode: auto`, notifications (push for a chat, off for a silent task), and the UTC cron converted from the chat's local cron. Execute each, then its `after` list (below).
- `planned["updates"]` — one `CallPlan` per registered chat whose body differs from the composed one after the version stamp is normalised out; it carries the trigger id from the listing or the stored map, and the prompt only.
- `planned["current"]` — chats whose registered body already equals the composed one. NO call, no receipt. This is what makes a second `set up command room schedules` plan zero calls.
- `planned["refused"]` — a chat set up for a different workspace folder, or a schedule the app cannot take: say each `line` once and change nothing for that chat.

The composed bootloader is what gets passed as the `prompt` argument — never the orchestrator body, which the bootloader reads fresh at every fire. A `<WRITER_` or any other placeholder never reaches a call: the composer refuses to return a body that still carries one.

**Step 1.B2 — Declare this computer the workspace's scheduled writer (SAFETY0 — RETIRE1, Night M3). Before the FIRST `plan_create` of this run, once.** A workspace has exactly one computer that runs its scheduled chats. Registering here is that claim, so this step records it — through the write door, never by opening `entities.json`:

`workspace_access.py plan run_writer --json '{"name": "schedule_config:declare_scheduled_writer", "args": {"workspace_root": "<WS>", "declared_by": "enable-command-room-schedules", "device_name": "<the name get_device_info reports>"}, "args_file": "<your scratch>/cr_declare_registered.json"}'`

(render it and paste what it prints, exactly like every other `plan` form in this skill — the variables in front of `python3` are what carry this computer's writer id to the host that holds the data.)

**`registered` is the listing Step 1.B read, and it travels by `args_file` (BIND3, D-T2-1).** Write `{"registered": RECORDS}` (the same normalised listing Step 1.B handed `plan_registration`) as one JSON object into THIS SESSION'S OWN scratch on the host that runs the door (never under the workspace), and name that file as `args_file`: every record carries its whole bootloader, which no pasted `--json` argument survives. Each record keeps its plain keys (`taskId`, `enabled`, `prompt`, `trigger_id`, `name`), whole, and never its `folders` or `path` (the door refuses a path argument; a registered chat's own body names its folder). Never trim a prompt to make it fit: a record the writer cannot place makes it refuse. **A record that carries a `path` and no `prompt` (the scheduled-tasks listing: it returns a `path` to the task's SKILL.md, never the prompt text): Read the file at that `path` and set the record's `prompt` to its text, then drop `path` from the record.** Do this for every such record before writing `args_file`. The writer places a chat on a computer by the folder its own body names, and the door refuses a path argument, so a record left with only its `path` cannot be placed and the writer refuses to move the declaration. The writer moves a declaration off another computer only when that listing shows no chat there, which is exactly the case once the customer deleted them, as the refusal's invite asked. Without a listing it moves nothing.

- `result.changed: true` → say `result.line` ONCE in the install summary, verbatim, and nothing else about it.
- `result.reason: "bound_elsewhere"` → another computer still holds this workspace's scheduled chats, and nothing was declared. Step 1.B did not refuse (this step only runs when it did not), so this is the one place the customer hears it: say `result.line` and then `result.invite`, once each, verbatim, as the whole reply about schedules, and STOP: execute none of Step 1.B's `creates` or `updates` (a chat registered here would stop itself at every fire, because this computer is not the declared writer).
- `result.changed: false` with no `reason` → this computer was already the declared writer; say nothing.
- `result.error: "no_writer_id"` → this seat cannot name itself (every older desktop seat and every local session), so it declares nothing — which is right; say nothing and continue.
- `ok: false`, or any other error → say nothing and continue. Declaring registers nothing, and a failed declaration is never a reason to stop a registration.

The writer is idempotent (the same id writes no file and no row), writes `workspace.scheduled_writer` plus one `scheduled_writer_declared` row, and never takes an id from anywhere but this seat. What it buys: once a writer is declared, a scheduled chat registered from ANOTHER computer stops itself at its Step 1.5 with one sentence and writes nothing, and `health check` names how many chats another computer is still writing. **Honest limit:** that stop reaches the other computer only after its own Command Room updates; until then its chats have to be deleted there by hand.

**Step 1.C — For each `taskId`, compare the composed bootloader against the prompt currently registered** (from the normalized listing; a record whose `prompt` is `None` is UNKNOWN and is skipped here, never rewritten). **BRIDGESIL1 (2026-08-27) — compare via `shared/scripts/schedule_refresh.py::prompts_equivalent(composed, registered)`, never a raw hash/string compare.** It normalizes the diagnostic plugin-version stamp out of both sides first (`task_watchdog.normalize_prompt_stamp`) — the fix for BUG_2026-08-16 and BUG_2026-08-19: the stamp made every version bump look like a content diff, so every plugin upgrade rewrote every registered prompt for zero behavioral gain (proof on file: `git diff` across a real release showed the pinned bootloader template byte-identical, yet seven prompts were rewritten on the stamp alone). Three outcomes:

`plan_registration` (Step 1.B) already made this comparison for every chat, with the SAME composed body and the same rules; the outcomes below are what its `creates` / `current` / `refused` / `updates` lists mean, in the order it checks them. On the merged app's scheduler an update always carries the trigger id — from the listing record, else from `TRIGGER_MAP` (`schedule_backend.read_trigger_map`) — because a trigger update names a trigger, not a task.

- **No registered taskId** → execute the plan `backend.plan_create(task_id, prompt=bootloader_body, ...)` returns. Pass the FULL composed bootloader string as the `prompt` argument.
- **`prompts_equivalent(composed, registered)` is True** → skip. Zero writes, zero prompts, zero receipt churn — this covers BOTH an exact match and a stamp-only diff (Ruling §0.2: "stamp-only diffs write NOTHING"). Idempotent.
- **the task belongs to a DIFFERENT workspace folder** (`schedule_refresh.refresh_workspace_guard(...)["ok"]` is False, or `plan_prompt_refresh` returns `refuse`) → **change nothing and say its `line` verbatim** (SCHEDVIEW1 5.4). This is checked BEFORE the two outcomes below, because a prompt that is not this workspace's is not this session's to judge stale. See the cross-folder rule in the verification section above for why refusing beats refreshing.
- **`prompts_equivalent(composed, registered)` is False (a genuine content diff survives normalization)** → execute the plan `backend.plan_update(task_id, prompt=bootloader_body)` returns. Pass the prompt only, so the registered cron, name, on/off state and notification setting are preserved. Never a confirmation first — bootloader text has no customer-facing customization surface, so a real diff here is always core-authored (Ruling §0.1). Then write the ONE receipt Ruling §0.4 requires: `schedule_refresh.log_schedule_refreshed(WORKSPACE_ROOT, task_id, "prompt", schedule_refresh.prompt_fingerprint(registered), schedule_refresh.prompt_fingerprint(composed), source_skill="enable-command-room-schedules")` — a short content fingerprint, never the full multi-KB body (events.jsonl is additive-forever; the template already lives on disk). This receipt is never announced (Ruling §0.3's morning-brief line is cron/label-only, per `SEMANTIC_FIELDS` — a prompt refresh is plumbing, not something a customer asked about).

**Surface in the install summary:** `Migrated N tasks to bootloader pattern (plugin upgrades will now auto-propagate)` for v2.14.24-from-prior migrations, or `All 7 bootloaders already current` if nothing changed (post-v3.11.0; pre-v3.11.0 was 6).

**Step 1.C2 — the SAME run, silently re-anchor an uncustomized CRON to core's current shipped default (BRIDGESIL1, "the work" item 1).** This is the one exception to "registration never re-anchors a cron" below — read that paragraph's carve-out before touching this step. **Cron only, not label:** `label` has no independent live counterpart on the Cowork task — the registered `description` is the fixed `"{display} - Command Room"` string (Per-task registration template above), never the config's `label` text, and `load_schedule_config`'s own merged `label` is *derived* (`override.get("label") or cron_to_english(cron)`) whenever no override exists. An uncustomized label is therefore already current the instant its cron is: there is nothing separate to write, and nothing separate to classify. (`schedule_refresh.plan_schedule_refresh` accepts any field name generically — `SEMANTIC_FIELDS` includes `"label"` so the morning-brief announce ledger stays field-agnostic for a future surface that DOES store a label live — but this skill only ever calls it with `field="cron"`.)

For each `taskId` already registered:

```python
import sys
sys.path.insert(0, 'shared/scripts')
from schedule_config import DEFAULT_SCHEDULES, SHIPPED_CRON_HISTORY
from schedule_refresh import plan_schedule_refresh, apply_schedule_refresh

entities_path = f'{WORKSPACE_ROOT}/_hq/data/entities.json'

# has_override reads the RAW store, never the merged load_schedule_config()
# view — the merged view already folds the current default in when nothing
# was overridden, which is exactly the "current core" comparison Ruling
# §0.3 forbids using as the customization test.
import json
try:
    raw_overrides = (json.load(open(entities_path, encoding='utf-8'))
                      .get('workspace', {}).get('schedule_config') or {})
except Exception:
    raw_overrides = {}

pending_cron_updates = {}
for task_id, live_task in registered_tasks.items():   # from the normalized listing
    default = DEFAULT_SCHEDULES.get(task_id)
    if not default:
        continue  # not a chat-orchestrator default (silent tasks handled in Step 1.D)
    has_override = 'cron' in (raw_overrides.get(task_id) or {})
    plan = plan_schedule_refresh(
        task_id, 'cron',
        live_value=live_task.get('cronExpression'),
        new_default=default['cron'],
        has_override=has_override,
        # The shipped-default TABLE (spec item 3): silent_apply requires the
        # live cron to be one core itself shipped. A live cron in neither the
        # table nor the override store is an out-of-band customization
        # (Cowork-UI edit, declined migration, hand-fix) -> preserve.
        shipped_defaults=SHIPPED_CRON_HISTORY.get(task_id, (default['cron'],)),
    )
    if plan['decision'] == 'silent_apply':
        result = apply_schedule_refresh(WORKSPACE_ROOT, plan,
                                        source_skill='enable-command-room-schedules')
        if result['applied']:
            pending_cron_updates[task_id] = plan['new']
    # noop / preserve → no write, no call, no receipt (Ruling §0.1 / §0.2)
```

Then, for each `taskId` in `pending_cron_updates`, execute the plan `backend.plan_update(task_id, cron_local=pending_cron_updates[task_id], tz_name=<workspace timezone>)` returns — the ONLY place in this skill that ever passes `cronExpression` to a refresh (not a create) call, and only for a task `plan_schedule_refresh` classified `silent_apply`. **Never call this for a task whose live cron already equals the default (`noop`) or whose cron carries a customer override (`preserve`)** — the classify pass above is the gate, not a suggestion. This never asks first (Ruling §0.1: "drop the confirm prompt from this path") and never surfaces a per-change line in THIS skill's own install summary — the customer-visible narration is the morning brief's `schedule_refresh_announce_lines` (Ruling §0.3), rendered once, after the fact, not here and not twice.

**Why this matters (v2.14.24 architecture).** The bootloader closes the drift bug structurally — fires read from disk fresh every time, so a plugin upgrade is enough to update fire behavior (lineage: v2.14.20 → v2.14.21 → v2.14.24 in references/HISTORY.md). **Customers no longer have to re-run `set up command room schedules` after every plugin upgrade.** The hard read happens at fire time, not at registration time.

**Step 1.D — Register the silent maintenance tasks (SPEC-2.3 registry loop — the registry currently holds ONE task, `maintenance`, and any future silent task registers through the same loop).**

The silent background tasks are NOT chat-orchestrators (no widget, no `orchestrator-*.md`) — they are skill-invoking prompts registered separately from the chats. As of Phase 3 (2026-07) they are **data-driven from the `SILENT_TASKS` registry in `shared/scripts/schedule_config.py`** — one loop registers all of them, and a silent task added to that registry in a future release registers here with zero edits to this file. (Pre-registry, each task had its own prose block, and each block was a place to forget one — Bug #82 was exactly that miss; see references/HISTORY.md § Bug #82 silent-task registration miss.)

As of MAINT1 (2026-07) the registry holds exactly one task: `maintenance` (`45 6,12,16,17 * * *` daily — BOOT3, 2026-09-19, retired the :30/:45 cross product because the merged Claude app will not accept two fires under an hour apart; the 16:30 pre-close slot is now served at 16:45, still before the 17:00 close. Seats that have not updated keep firing the older eight-fire string, which is why both older values stay in `SHIPPED_CRON_HISTORY`). It carries the silent JOBS — reconcile-sent (first at 6:45, BEFORE the 7:00 morning brief, Bug #98-v3's load-bearing ordering), session-sweep, cleanup, weekly-insights, deal-signals (LB1 — Sunday, after insights), identity-reconcile (PID1 — Sunday, after deal-signals), monthly-report — dispatched per fire by `shared/scripts/maintenance_dispatcher.py` (`due_jobs()` decides in code from receipts; the prompt never judges due-ness). One taskId is also one permission story: a future silent job lands inside the entry the customer already allowed instead of opening a new fleet-wide gap per release (`task_watchdog`'s `never_authorized` class).

**Supersede step (MAINT1, D5 — data-driven):** after registering each registry task, read `SUPERSEDED_BY[task_id]` from `schedule_config.py`; every listed taskId still registered+enabled is disabled by executing the plan `backend.plan_update(task_id, enabled=False)` returns. Idempotent, and it never deletes: the seam refuses to plan a delete for anything a customer can see, whichever backend is live. This is the same disable-don't-delete pattern as the Phase 1 legacy migration table; the map is data so the bridge's Phase 4.7 loop applies the identical migration with zero prose duplication.

**Every pause, disable or enable writes a config record (SPEC SCHED1 §0-4).** The moment an enable/disable plan lands (`backend.plan_update(task_id, enabled=...)`), call `schedule_config.log_schedule_config_change(<WORKSPACE>, [{'task_id': '<id>', 'cron': None, 'enabled': False}], source_skill='<this skill>')` — one call, the same single writer `change-schedule` uses, and never a hand-rolled event (the helper owns the shape). This is not bookkeeping: the lateness ledger READS `schedule_config_changed` to know that a slot older than the change was minted by the change and must never be scored (the F-51 phantom), so a pause nobody recorded leaves the ledger believing this task's newest config change is whatever came before it. The 2026-08-17 fold-in wrote three `schedule_created` events and no record at all for the two chats it paused.

Compose every silent task's registration parameters in one pass. **The writer pair is baked in here too (MF-26).** The maintenance prompt's merged branch carries THIS seat's pair in front of `python3` on every line, so its fire lands its receipt under the customer's account even when no earlier folder export survived in its shell. The pair comes from `schedule_config.silent_prompt_pair(<the workspace's absolute path>)` — the same path, and the same `writer_identity.bakeable_pair` derivation, the chats' pair is baked from above — and the currency compare (`schedule_refresh.plan_prompt_refresh` / `prompt_body_drift`) recomposes with the same call, so a baked prompt reads as current and one baked under another account reads as refresh needed. No pair on this seat → the prompt is today's text.

```bash
cd "$PLUGIN_ROOT" && WORKSPACE_BASENAME="$WORKSPACE_BASENAME" WORKSPACE_PATH="<the workspace's absolute path>" python3 -c "
import sys, os, json
sys.path.insert(0, 'shared/scripts')
from schedule_config import SILENT_TASKS, DEFAULT_SCHEDULES, compose_silent_task_prompt, silent_prompt_pair
basename = os.environ['WORKSPACE_BASENAME'].strip()
wid, wder = silent_prompt_pair(os.environ['WORKSPACE_PATH']) or (None, None)  # MF-26: this seat's own pair, never the synced file
out = {}
for task_id, spec in SILENT_TASKS.items():
    out[task_id] = {
        'prompt': compose_silent_task_prompt(task_id, basename, writer_id=wid, writer_derivation=wder, workspace_path=os.environ['WORKSPACE_PATH']),  # raises on bad basename / half pair; the path puts the ONE folder request in the body (R-WALK-4)
        'cron': DEFAULT_SCHEDULES[task_id]['cron'],               # cron derives from DEFAULT_SCHEDULES — never duplicated in the registry
        'description': spec['description'],
        'notifyOnCompletion': spec['notify'],
        'reason': spec['reason'],                                  # customer-facing one-liner for the Phase 5 ritual
    }
print(json.dumps({tid: {'chars': len(v['prompt']), 'cron': v['cron']} for tid, v in out.items()}))
"
```

**One plan, not two (SCHEDREG1).** Step 1.B's `plan_registration` is handed the silent ids with the chats, and composes them with this same call and this same pair — so their creates and updates are already in its lists. Execute those, and make no second call here; the block above is the same composition, shown so a reader can see the pair go in.

Then, for each `task_id` in the composed set: register by executing the plan `backend.plan_create(task_id, prompt=<composed prompt>, cron_local=<composed cron — from load_schedule_config() when the workspace has an override, else the composed default>, tz_name=<workspace timezone>, notify=<composed flag>, workspace_basename=<basename>, folders=[<workspace absolute path>])` returns. **Idempotent:** if the task already exists with a matching prompt, skip; if it differs, execute `backend.plan_update(task_id, prompt=...)` preserving cron/enabled (custom-cron preservation applies to silent tasks exactly as it does to the chats — never re-anchor a registered task's cron from here). Surface one install-summary line per task actually registered this run, e.g. `Registered the weekly cleanup (silent Sunday maintenance + brain self-heal)`.

The `maintenance` task IS in `FIRST_INSTALL_TASK_IDS`, so it registers on fresh installs AND re-runs. Because the prompt asks the dispatcher what's due and then fires the due skills (all read from the installed plugin at fire time), plugin upgrades propagate automatically — no re-registration needed when a silent skill's logic changes, and a NEW silent job added to `maintenance_dispatcher.MAINTENANCE_JOBS` ships with zero registration changes at all.

### Known scheduler bug awareness — updating a registered chat, and #40835

[anthropic/claude-code#40835](https://github.com/anthropics/claude-code/issues/40835) (open as of 2026-05-06): creating or modifying a scheduled task may disable MCP connectors in OTHER existing scheduled tasks. The issue body says "creation/modification" so an update plan is plausibly affected too. There is no per-task connector-status field in any listing to verify after the fact.

**Mitigation:** after any registration / update batch, surface this exact line in the install summary so the customer knows to manually re-prime each task's connector cache:

> *"One quick thing: setting up new scheduled tasks can temporarily switch off access to your email, calendar, and other tools in your other scheduled chats. Open each scheduled chat once and confirm any permission prompts. After that, they'll run on schedule with full access."*

If an operator is present for the first-time setup (e.g. immediately after the onboarding call), you can defer this re-prime line to the operator-delivered hand-off rather than surfacing it inline. When the customer is setting schedules up on their own, surface it normally.

If much-older v2.8.x tasks present (`cr-refresh-*`, `cr-daily-morning-pack`, `cr-workflow-commitment-chase-drafts`, `cr-workflow-weekly-audit`), DISABLE those too. Same for any `cr-pulse` or `cr-dont-forget` registrations encountered — both are pre-v2.14.27 state; canonical taskId is bare `pulse` per the migration table above.

## Phase 2 — Load schedule config (v2.14.10+)

Read the per-workspace schedule configuration via the `schedule_config` helper. The helper merges defaults with any overrides stored in entities.json `workspace.schedule_config`:

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
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from schedule_config import load_schedule_config
WORKSPACE = '<absolute path to user workspace>'
config = load_schedule_config(f'{WORKSPACE}/_hq/data/entities.json')
print(json.dumps(config))
"
```

Returned shape: `{taskId: {cron, label, enabled}, ...}` for every default task. Use these config values when registering each schedule in Phase 3 — DO NOT hardcode cron expressions or descriptions in this skill anymore. The hardcoded examples in Phase 3 below are FALLBACKS; the actual values come from the config helper.

**Why config-driven:** different CEOs work different rhythms. A service-business owner may want Inbox at 8 AM (after standups); a fund manager may want it at 6 AM (early Pacific). The helper lets each workspace customize without forking the skill. See `change-schedule` skill (v2.14.10+) for the user-facing customization flow.

**First install behavior:** entities.json typically has no `workspace.schedule_config` field on first install — the helper returns built-in defaults silently. After install, the operator or the customer says `change my schedule` to tune.

**Disabled tasks:** if `config[taskId].enabled` is `false`, skip the registration entirely (or execute `backend.plan_update(task_id, enabled=False)` if currently registered). A disabled task is kept as a record of what used to run, and it does not fire.

**Every pause, disable or enable writes a config record (SPEC SCHED1 §0-4).** The moment an enable/disable plan lands (`backend.plan_update(task_id, enabled=...)`), call `schedule_config.log_schedule_config_change(<WORKSPACE>, [{'task_id': '<id>', 'cron': None, 'enabled': False}], source_skill='<this skill>')` — one call, the same single writer `change-schedule` uses, and never a hand-rolled event (the helper owns the shape). This is not bookkeeping: the lateness ledger READS `schedule_config_changed` to know that a slot older than the change was minted by the change and must never be scored (the F-51 phantom), so a pause nobody recorded leaves the ledger believing this task's newest config change is whatever came before it. The 2026-08-17 fold-in wrote three `schedule_created` events and no record at all for the two chats it paused.

**Defaults (built-in fallbacks if config absent):**
- **Time zone:** detected from entities.json primary user (`person.time_zone` field if set), else system time zone
- **Per-chat times:** read them from `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` — that dict is the only list of times, and a second copy in this file is a second thing to forget (the pre-FIX1 drift class). Retired ids (`pulse`, `upcoming-meetings`) are not in it and have no time.

## Phase 3 — Register or refresh the 7 orchestrators (6 daily widgets + 1 weekly recap)

**Later-add fence (MAINT1 / D7 — BINDING, before anything registers):** the registration set is NEVER expanded by trigger phrasing; "set up ALL command room scheduled tasks" registers the same first-install set, and every task in `later_add_task_ids()` is PROPOSED (one line each, register only on explicit per-task yes). **Read the set from `later_add_task_ids()` at run time — never from a list typed into this sentence.** The later-add chats are deliberately not first-install because they need accumulated workspace signal to fire well; an "all"-shaped request is enthusiasm, not consent to register tasks that will fire badly on day 1 (the observed 2026-07 field failure: a fresh client said "set up all command room scheduled tasks" and got every later-add chat registered day 1, against the substrate gating).

**A RETIRED task is never in that set and is never proposed by this or any other path.** `later_add_task_ids()` derives from `DEFAULT_SCHEDULES`, and retirement takes the row out, so this is structural rather than a rule to remember. TASKRET1 (M's ruling 2026-08-17) is what made the distinction load-bearing: `commitment-triage`, `balance` and `pipeline-digest` were later-adds the week before, each carrying its own extra proposal gate — Balance on a declared `workspace.personal_calendars`, the digest on ≥1 open tracked deal. Those gates are gone with the offers, because a gate that was almost never satisfied is the diagnosis, not the fix: a surface most workspaces could never turn on usefully should not have been offered weekly in the first place. Nothing here proposes them, and `add balance` / `add pipeline digest` / `add commitment triage` are refused warmly with `schedule_config.retirement_line(task_id)` (see the Phase 6 rules below). The on-demand skills of all three names are untouched and fully live.

**First-install gate (M1 / 2026-05-23+):** before iterating ORCHESTRATOR_MAP, decide which subset of taskIds gets registered:

```python
from schedule_config import FIRST_INSTALL_TASK_IDS, registration_target_set

if FIRST_INSTALL:
    # Fresh workspace per Phase 0.C detection. Register ONLY the 5 M1 first-install tasks.
    # The remaining later-adds (waiting-on / my-plate) arrive via operator-driven
    # follow-up sessions — the two CTS1 commitment surfaces land once the customer
    # has been logging meetings for a couple of weeks (same posture the retired
    # `commitments` chat had).
    tasks_to_register = {
        tid: fname
        for tid, fname in ORCHESTRATOR_MAP.items()
        if tid in FIRST_INSTALL_TASK_IDS
    }
else:
    # Existing workspace. Re-run / refresh — preserve whatever the customer already has
    # registered (do NOT auto-disable waiting-on/my-plate if they're already
    # running; a still-registered `commitments` migrates per the Phase 1 table).
    # A RETIRED task (schedule_config.RETIRED_TASKS) is likewise never auto-disabled
    # here — retirement is PROPOSED by the update bridge and executed by the
    # customer's own `pause`, never by this skill (LIFECYCLE1 §4). It is also never
    # ADDED: a retired id can only be in the target set because it is already
    # registered, and then it is there to have its PROMPT refreshed, nothing more.
    # AND make sure the M1 first-install set lands so pre-M1 customers get inbox added
    # on their next re-run. The union behavior is intentional: we add new defaults but
    # never silently remove what the customer has.
    #
    # THE RENAME FENCE IS IN THE HELPER, NOT IN THIS PROSE (EOD2). Call
    # `registration_target_set()` — do NOT hand-roll `existing | FIRST_INSTALL_TASK_IDS`
    # here. The helper subtracts any first-install task whose RENAMED PREDECESSOR is
    # still registered on this machine, which is the only thing standing between the
    # fleet and a second 5 PM chat appearing beside every customer's Past Meetings
    # entry on their next registration run. A rename is PROPOSED (update bridge →
    # `schedule_config.retirement_line("past-meetings")`) and taken by the customer's
    # own `add end of day`; it is never applied by a refresh.
    existing_registered = set(load_registered_taskIds())  # from workspace_config.json
    target_set = registration_target_set(existing_registered)
    tasks_to_register = {
        tid: fname for tid, fname in ORCHESTRATOR_MAP.items() if tid in target_set
    }
```

The migration semantics:
- Fresh install → exactly the `FIRST_INSTALL_TASK_IDS` set (the chats `morning-brief` / `end-of-day` / `inbox` / `friday-wrap`, plus the silent `maintenance` via Step 1.D).
- Existing pre-M1 customer who re-runs the skill → gets their existing tasks refreshed PLUS `inbox` added (because it's now in the M1 first-install set). They never lose tasks they had.
- Existing customer with `upcoming-meetings` registered → the task is left ALONE here (never auto-disabled) and never refreshed into the live set; its prompt IS refreshed if it is still registered, because that is what makes the next fire explain itself instead of replaying the retired chat. The offer to switch it off comes from the update bridge, and `change-schedule` accepts `pause upcoming meetings` (BRIEFMERGE §E).
- **Existing customer with `past-meetings` registered → NOTHING changes on this run (EOD2).** `registration_target_set()` has already removed `end-of-day` from the target set, so no second evening chat is created; `past-meetings` stays registered, stays enabled, and has its PROMPT refreshed like any live task — which is all it needs, because its bootloader names the same orchestrator file `end-of-day` does and it therefore keeps firing the current End of Day pack at 5 PM. Do not disable it, do not "migrate" it, and do not mention a rename here at all: the ONE offer is the update bridge's, and it is suppressed for six weeks after it lands.
- Customer says `add waiting on` / `add my plate` in Phase 6 management flow → those taskIds get registered individually (`add commitments` maps to registering BOTH `waiting-on` and `my-plate` — the split successors).
- **Customer says `add end of day` → this is the RENAME SWITCH, and it is the only path that applies it (EOD2).** Register `end-of-day` through the normal Phase 6 add (bootloader composed from `ORCHESTRATOR_MAP["end-of-day"]`, cron from `load_schedule_config()`, `schedule_created` event per FS-07) AND, in the same step, disable a still-registered `past-meetings` by executing `backend.plan_update("past-meetings", enabled=False)`. Both halves or neither: registering the new id while leaving the old one enabled gives the customer two 5 PM chats, and disabling the old one without registering the new gives them none. Confirm in one line — *"✓ Your evening chat is now End of Day, same 5 PM. The old Past Meetings entry is switched off."* **The override half is ONE CALL and you may not hand-roll it: `schedule_config.apply_rename_retirement(<WORKSPACE>, "past-meetings", source_skill="enable-command-room-schedules")` (SPEC SCHEDINH1).** It carries any custom cron/label the customer had on `past-meetings` onto `end-of-day`, writes the successor's own `enabled: true` so no reader has to infer it, stamps the predecessor `{enabled: false, superseded_by: "end-of-day"}` as the retirement record, and logs the config change for BOTH rows through the one writer — atomically, in the same file write. It raises rather than guessing if the id is not a rename, so a wrong call is loud instead of a store nobody can read back. **This call IS the config record for both rows, so do NOT also hand-roll a `log_schedule_config_change` for the `past-meetings` disable** — the "every pause writes a config record" rule below is already satisfied for this path, and following both instructions writes the same change twice. `change-schedule` says the same thing in the same words; these two bullets govern one store write and must never drift apart again. This replaces the older "write the new key and REMOVE the old key" instruction: the predecessor's key now STAYS, because it is the record of why that id went quiet, and `check_registration_drift` already exempts a renamed predecessor from the orphan-override scan. Do not delete it, and do not write either key by hand — the live workspace that produced SCHEDINH1 had a predecessor disable sitting in its store with no successor key at all, which made a nightly-firing chat read as paused on every surface that asked. Costs to surface, same three as CTS1 §10.3: the old entry is switched off rather than removed and stays visible wherever schedules are listed, its chat history stays in the old thread, and the new chat has to reach the workspace once before it can do real work.

**Every pause, disable or enable writes a config record (SPEC SCHED1 §0-4).** The moment an enable/disable plan lands (`backend.plan_update(task_id, enabled=...)`), call `schedule_config.log_schedule_config_change(<WORKSPACE>, [{'task_id': '<id>', 'cron': None, 'enabled': False}], source_skill='<this skill>')` — one call, the same single writer `change-schedule` uses, and never a hand-rolled event (the helper owns the shape). This is not bookkeeping: the lateness ledger READS `schedule_config_changed` to know that a slot older than the change was minted by the change and must never be scored (the F-51 phantom), so a pause nobody recorded leaves the ledger believing this task's newest config change is whatever came before it. The 2026-08-17 fold-in wrote three `schedule_created` events and no record at all for the two chats it paused.
- Customer says `add pulse` or `add upcoming meetings` → **refuse**, with `schedule_config.retirement_line(task_id)` verbatim. An ELIMINATED task is never registered by any path, including an explicit ask (LIFECYCLE1 / BRIEFMERGE). Customer says `add past meetings` → also refuse, also with `retirement_line("past-meetings")` verbatim — but note the line reads differently on purpose: it is a rename, so it names End of Day rather than offering a `pause`.
- **Customer says `add commitment triage`, `add balance` or `add pipeline digest` → refuse, same way, same helper (TASKRET1).** These are READINESS retirements, and the registry has already worded the line for that class: it says the chat is off the schedule, names what is missing, points at the on-demand surface that still does the work, and states what brings the chat back. Surface it verbatim and register nothing. **Do not soften the refusal into "I can add it anyway if you want"** — the whole ruling is that these surfaces do not fire on a schedule until their substrate is ready, and one hand-registered exception is a workspace that will be firing an empty chat weekly with no record of why. **Do not offer a `pause` either** — the update bridge's readiness migration has already disabled any live registration, so a pause is a tap that no longer exists to take. `add end of day` stays the ONE exception where an `add` phrase for a retired id does something, because that one is a rename with a live successor.

Each taskId in `tasks_to_register` (plus the silent set) was planned by Step 1.B's ONE call, `schedule_backend.plan_registration`; execute its `creates` and `updates` in order, each followed by its `after` list, and make no call for its `current` list. A second run over a workspace this skill already set up plans zero calls. The three paths it chose between:

- **Not yet registered** → execute the plan `backend.plan_create(task_id, prompt=body, ...)` returns, with `body` from Phase 1's read step. Full registration.
- **Already registered with a stale prompt (`schedule_refresh.prompts_equivalent` returns False — a genuine content diff survives the plugin-version-stamp normalization)** → execute the plan `backend.plan_update(task_id, prompt=body)` returns. Refresh in place. Cron, description, enabled, notify all preserved (subject to the Step 1.C2 cron-only carve-out immediately below).
- **Already registered with a current or stamp-only-diff prompt (`prompts_equivalent` returns True)** → skip. No-op idempotent (BRIDGESIL1 Ruling §0.2).

**Custom-cron preservation (MANDATORY on every re-run) — with ONE BRIDGESIL1 exception.** When a task is already registered, do NOT pass `cron_local` to this `backend.plan_update(task_id, prompt=body)` prompt-refresh — pass `prompt` only, so the registered cron is preserved verbatim here. An operator (or the user) may have moved a task via `change-schedule`; that override is stored in entities.json `workspace.schedule_config` AND already reflected in the live task. Blindly re-applying `DEFAULT_SCHEDULES` cron on every refresh would silently stomp it back to the shipped default — the exact "my 6 AM brief jumped back to 7 AM after an update" complaint. `cron_local` is set on the create path (a task not yet registered) from `load_schedule_config()` (which merges the operator's entities.json overrides, never raw `DEFAULT_SCHEDULES`) — and, as of BRIDGESIL1, on ONE other path: **Step 1.C2 below**, which re-anchors a task's cron ONLY when `schedule_refresh.plan_schedule_refresh` classifies it `silent_apply` — meaning the live cron still equals the OLD shipped default (no override was ever recorded for it), so the change is core-authored and the customer never touched it. A cron carrying an override, or a live cron that already diverges from every shipped default, is always `preserve`: it is never touched by anything but `change-schedule`. If you need to re-anchor a CUSTOMIZED cron, that stays `change-schedule`'s job, not registration's.

**Pass the full `body` string from Phase 1 as the `prompt` parameter. NEVER paraphrase, summarize, or extract a "mission section" instead.** The orchestrator IS the work — there's no separate runner code; the Claude session executes everything from the prompt. Loss of the v2.13.0 OUTPUT CONTRACT preamble (which lives in the first ~50 lines of every chat-emitting orchestrator) means the fire bypasses every validator + the renderer + the STOP CONTRACT enforcement chain. That's the v2.14.20 regression this v2.14.21 spec exists to prevent.

**Per-task registration template (v2.14.10+ config-driven):**

For each taskId below, pull `cron` + `label` from the Phase 2 config map. Build the `description` parameter by combining the display name + the config's `label` field. Example:

```python
config = load_schedule_config(...)  # from Phase 2
task_id = "inbox"
spec = config[task_id]               # {"cron": "15 7 * * 1-5", "label": "7:15 AM weekdays", "enabled": True}
display = task_display_name(task_id) # "Inbox"
description = f"{display} - Command Room"  # v2.14.25+ canonical format
cron_expression = spec["cron"]
```

If `spec["enabled"]` is `False`, skip registration for this task entirely (or update its enabled flag if already registered).

### Schedule 0 — Morning Brief (onboarding-v2 / 2026-05-17+, NEW)

- `taskId: "morning-brief"` (NEW — first-install default. Registered fresh on every new workspace.)
- `description`: **`"Morning Brief - Command Room"`** (v2.14.25+ canonical display name format)
- `cronExpression`: from config (default `"0 7 * * 1-5"`, 7 AM weekdays). v3.12.0 shifted the `inbox` default to 7:15 AM so there's no slot collision out of the box.
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template (see Phase 1.B); orchestrator body lives at `references/orchestrator-morning-brief.md` and is read fresh by the bootloader at fire time. The orchestrator wraps the existing `morning-briefing` skill — keeps the scheduled-fire output and the on-demand `morning briefing` / `brief me` / `what do I need to know today` output convergent (one source of truth for the morning-briefing format).

### Schedule 1 — Upcoming Meetings — RETIRED (SPEC BRIEFMERGE, 2026-08-08)

**Do not register this task. There is no registration spec here any more, and that absence is the ruling.** `upcoming-meetings` is in `schedule_config.RETIRED_TASKS` and out of `DEFAULT_SCHEDULES`; the `ORCHESTRATOR_MAP` row survives only so a pre-retirement registration still resolves its bootloader to `references/orchestrator-upcoming-meetings.md`, which is now a retirement stub. Refreshing that prompt on a still-registered task is CORRECT — it is what makes the next fire explain itself instead of replaying the old chat.

Where the work went: prep generation is the morning-brief fire's FIRST leg (`shared/scripts/prep_leg.py`, wired in `references/orchestrator-morning-brief.md` Phase 3.5), so today's meetings are prepped before the brief composes its meeting section. A meeting booked after the brief fires is covered on demand by `call-prep` ("prep me for my 2pm") — that skill and its triggers are untouched. The historical registration facts follow so an auditor can recognise an existing registration. They are a description of what IS, never an instruction to create one:

- `taskId: "upcoming-meetings"` (v2.14.27+ — bare taskId so Cowork's sidebar title renders cleanly as "Upcoming meetings"; prior cr-upcoming-meetings → migration disabled)
- `description`: **`"Upcoming Meetings - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (historical default `"30 6 * * 1-5"`)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template (see Phase 1.B); orchestrator body lives at `references/orchestrator-upcoming-meetings.md`.

### Schedule 2 — Inbox

- `taskId: "inbox"` (v2.14.27+ — bare taskId; prior cr-inbox → migration disabled)
- `description`: **`"Inbox - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (default `"15 7 * * 1-5"`, 7:15 AM — v3.12.0 shifted off the 7:00 morning-brief slot)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-inbox.md`.

### Schedule 3 — Waiting On + My Plate (CTS1 — the split successors of "Commitments")

- `taskId: "waiting-on"` (CTS1 §10.3, fresh taskId per the v2.14.27 pattern; prior `commitments` → migration disabled per the Phase 1 table)
- `description`: **`"Waiting On - Command Room"`** (v2.14.25+ canonical display name format)
- `cronExpression`: from config (default `"30 8 * * 1-5"` — inherits the old commitments slot; a customer's custom cron override on `commitments` carries over to this task at migration)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-commitments.md` (filename kept for events.jsonl source_skill back-compat — events keep `source_skill='commitments'`, same pattern as pulse).

- `taskId: "my-plate"` (CTS1 §10.3 — the second surface; registers alongside waiting-on)
- `description`: **`"My Plate - Command Room"`**
- `cronExpression`: from config (default `"45 8 * * 1-5"` — 15 minutes after waiting-on so it reads the just-reconciled substrate)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-my-plate.md`.

### Schedule 4 — Pulse — RETIRED (SPEC LIFECYCLE1, 2026-08-02)

**Do not register this task. There is no registration spec here any more, and that absence is the ruling.** `pulse` is in `schedule_config.RETIRED_TASKS` and out of `DEFAULT_SCHEDULES`; the `ORCHESTRATOR_MAP` row survives only so a pre-retirement registration still resolves its bootloader to `references/orchestrator-dont-forget.md`, which is now a retirement stub. Refreshing that prompt on a still-registered task is CORRECT — it is what makes the next fire explain itself instead of replaying the old chat. The historical registration facts follow so an auditor can recognise an existing registration. They are a description of what IS, never an instruction to create one:

- `taskId: "pulse"` (v2.14.27+ — bare taskId aligned with display name; prior cr-dont-forget → migration disabled. Orchestrator filename stays as `orchestrator-dont-forget.md` for events.jsonl source_skill back-compat — historical events with source_skill='cr-dont-forget' remain valid as append-only history; new events post-v2.14.27 use source_skill='pulse'.)
- `description`: **`"Pulse - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (default `"0 9 * * 1-5"`)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-dont-forget.md`.

### Schedule 5 — End of Day (EOD2 rename of Past Meetings)

- `taskId: "end-of-day"` (EOD2 — the successor id. Register THIS on a fresh install.)
- `description`: **`"End of Day - Command Room"`** (v2.14.25+ canonical display name)
- `cronExpression`: from config (default `"0 17 * * 1-5"` — Decision 1's hour, inherited unchanged from `past-meetings`)
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body at `references/orchestrator-past-meetings.md`.

**The filename above is not a mistake and must not be "fixed."** The registered prompt bakes `<ORCHESTRATOR_FILENAME>` in at registration time, so every live `past-meetings` bootloader on the fleet already names `orchestrator-past-meetings.md`. Renaming the file would break every one of them at their next fire; giving `end-of-day` its own file would fork one pack into two and let the two ids drift. Both ids map to that one file — that is the mechanism by which "both ids serve the same pack" is true structurally rather than by discipline. (Same pattern as `waiting-on` → `orchestrator-commitments.md` and `pulse` → `orchestrator-dont-forget.md`.)

The predecessor, for auditors recognising an existing registration — a description of what IS, never an instruction to create one:

- `taskId: "past-meetings"` (v2.14.27+ — bare taskId; prior cr-past-meetings → migration disabled). **RENAMED, not eliminated (EOD2).** Still registered on every workspace set up before the rename, still enabled, still firing the current End of Day pack at 5 PM through the very same orchestrator file, and it will keep doing so for as long as the customer leaves it alone. Never register it fresh; never auto-disable it. `description`: `"Past Meetings - Command Room"` — leave it as it is, since Cowork derives the sidebar title from the taskId anyway and a description edit would only make the row confusing.

### Schedule 6 — Friday Wrap (v3.11.0+, NEW)

- `taskId: "friday-wrap"` (NEW — first-install default. Registered fresh on every new workspace post-v3.11.0.)
- `description`: **`"Friday Wrap - Command Room"`** (v2.14.25+ canonical display name format)
- `cronExpression`: from config (default `"0 13 * * 5"`, 1 PM Fridays — Phase 3/R4 moved the default off the routinely-slept-through 4 PM slot for NEW installs; existing registrations keep their live cron per the custom-cron preservation rule). First weekly-rhythm scheduled task; all prior tasks are daily.
- `notifyOnCompletion: true`
- `prompt`: bootloader composed from template; orchestrator body lives at `references/orchestrator-friday-wrap.md` and is read fresh by the bootloader at fire time. The orchestrator wraps the existing `weekly-recap` skill — keeps the scheduled-fire output and the on-demand `weekly recap` / `recap last week` output convergent (one source of truth for the recap format and `.docx` save path).

### v2.14.25+ — Schedule 7 (Workspace Map refresh) DROPPED

The v2.14.11+ daily auto-refresh scheduled task `cr-refresh-workspace-map` is REMOVED from the active task set as of v2.14.25. Per M's call: the daily auto-rebuild of the Workspace Map sidebar artifact wasn't worth the operational complexity (one more task to register, one more cron to fire, one more #40835 risk surface).

**What remains:** nothing of the sidebar. Since Night M3 (RETIRE1, ruling R-M3-4) the pinned dashboards are retired on every seat, the refresh orchestrator file is deleted, and every Workspace Map phrase answers the one sentence (`level_up_lines.DASHBOARDS_IN_CHAT`) — `list active projects` is the Workspace Map now, in chat.

**Migration for existing customers (anyone with the task already registered):** Phase 1's legacy-taskId migration list (above) now includes `cr-refresh-workspace-map`. On next `set up command room schedules` run, the task is DISABLED by executing `backend.plan_update(task_id, enabled=False)` and surfaced in the install summary as: *"Removed the daily Workspace Map refresh — dashboards live in chat now."*

**Every pause, disable or enable writes a config record (SPEC SCHED1 §0-4).** The moment an enable/disable plan lands (`backend.plan_update(task_id, enabled=...)`), call `schedule_config.log_schedule_config_change(<WORKSPACE>, [{'task_id': '<id>', 'cron': None, 'enabled': False}], source_skill='<this skill>')` — one call, the same single writer `change-schedule` uses, and never a hand-rolled event (the helper owns the shape). This is not bookkeeping: the lateness ledger READS `schedule_config_changed` to know that a slot older than the change was minted by the change and must never be scored (the F-51 phantom), so a pause nobody recorded leaves the ledger believing this task's newest config change is whatever came before it. The 2026-08-17 fold-in wrote three `schedule_created` events and no record at all for the two chats it paused.

For each created schedule, log a `schedule_created` event (OMIT `seq`/`ts` — the append gate auto-stamps both inside the writer lock, `ts` in UTC; a hand-typed "now" was the F-15 naive-local-clock bug class, v4.5.2 R4.)
```jsonl
{"type":"schedule_created","data":{"taskId":"<id>","cron":"<expr>","label":"<description>","trigger_id":"<the id the registration call returned>","cron_utc":"<expr>","backend":"cloud"}}
```
**The last three fields are additive and come from the plan, not from you.** `backend.plan_create` puts them on its `event:schedule_created` entry in `plan.after` — `trigger_id` (fill it from what the registration call returned), `cron_utc` (the world-time cron actually registered; `null` on the older desktop app, which schedules in the machine's own time) and `backend`. Take them from `dict(plan.after[<the event:schedule_created entry>][1])` rather than composing them by hand — which is exactly what `registration_payload` does, so on a create this event rides the ONE `record_registration` door call beside the trigger row (SCHEDFAST1) and is never a second call. Every existing reader of this event keeps working: nothing was renamed or removed. On the older app `cron_utc` is `null` — that scheduler evaluates cron in the machine's own time, so there is no world-time value to record — and `backend` reads `legacy`.

**MANDATORY on EVERY registration path (FS-07).** This write fires whenever a task is newly registered — the Phase 3 first-install loop AND the Phase 6 `add` flow (`add staff meeting`, `add commitments`, …). A task that lands in the scheduler with no `schedule_created` event has no substrate record of its registration — that is the FS-07 gap (the live `add staff meeting` created the task + updated `workspace_config.json` but wrote nothing). One `schedule_created` per taskId actually created this run, never for a task that was already registered (idempotent skip).

## Phase 3.5 — Post-registration verification (v2.14.24+ — bootloader pattern)

After ALL the create and update plans of Phase 3 have been executed, **read back what's now registered ONCE (one `backend.plan_list()` + `normalize`) and verify every task you registered or refreshed this run against it.** One listing for the whole run, not one per chat (SCHEDFAST1, #101 — on the merged seat every listing is a device round trip, and a read-back after each of six creates was six of them for one answer); the checks below are per task and unchanged. This is the hard gate that catches v2.14.20-style improvisation regressions, adapted for the bootloader pattern. Keep this listing: Phase 5.9 reads the same one.

What's verified:
1. Each registered taskId's prompt CONTAINS the canonical bootloader markers (`# Scheduled task bootloader`, `Resolve the plugin path`, `Read the orchestrator and execute it verbatim`).
2. Each registered taskId's prompt CONTAINS the correct task name (`<TASK_ID>` was substituted with the actual taskId, not left as a literal placeholder).
3. Each registered taskId's prompt CONTAINS the correct orchestrator filename (`<ORCHESTRATOR_FILENAME>` was substituted with the file from `ORCHESTRATOR_MAP`).
4. Each registered taskId's prompt does NOT start with `---` frontmatter (Cowork prepends its own; user-supplied frontmatter creates a doubling bug).
5. Each registered taskId's prompt length is within **0.9×–1.5× of the composed bootloader for that task** (`len(bootloaders[task_id])` from Phase 1.B — computed at RUN TIME from the real template, never a hardcoded range). Under 0.9× means a stub got registered; over 1.5× means the full orchestrator body (or other bloat) got registered. (Phase 3 / P0.2: hardcoded ranges drifted and failed every healthy install — see references/HISTORY.md § Phase 3 / P0.2. Computing bounds from the template kills the drift class; the composition test in tests/run_bootloader_size_gate_test.py keeps gate and template from ever drifting again.)

```python
# Pseudocode — translate to actual MCP calls in your invocation
registered = backend.normalize(<the result of executing backend.plan_list()>,
                               workspace_root='$WORKSPACE')
registered_by_id = {t["taskId"]: t for t in registered}

REQUIRED_MARKERS = [
    "# Scheduled task bootloader",
    "Resolve the plugin path",
    "Read the orchestrator and execute it verbatim",
    "Anti-improvisation contract",
]

failures = []
# Iterate over what we INTENDED to register this run (Phase 3's tasks_to_register subset),
# not the full ORCHESTRATOR_MAP. On first-install runs the deferred tasks
# (waiting-on/my-plate and the other later-adds) are intentionally NOT
# registered — they're not failures. Neither is a RETIRED task, ever.
for task_id, fname in tasks_to_register.items():
    if task_id not in registered_by_id:
        failures.append(f"{task_id}: not registered")
        continue
    actual_prompt = registered_by_id[task_id]["prompt"]
    # Frontmatter-doubling check
    if actual_prompt.lstrip().startswith("---"):
        failures.append(f"{task_id}: registered prompt starts with frontmatter (Cowork doubling bug); re-register without leading ---")
        continue
    # Marker checks
    for marker in REQUIRED_MARKERS:
        if marker not in actual_prompt:
            failures.append(f"{task_id}: registered prompt missing required bootloader marker {marker!r}")
            break
    else:
        # All markers present — verify task-specific substitutions
        if f"`{task_id}`" not in actual_prompt:
            failures.append(f"{task_id}: registered prompt is missing the task name {task_id!r} in expected location (substitution may have failed)")
            continue
        if fname not in actual_prompt:
            failures.append(f"{task_id}: registered prompt is missing the orchestrator filename {fname!r} (substitution may have failed)")
            continue
        # v2.14.26+ — workspace basename substitution check
        # The bootloader's Step 1 must contain the customer-confirmed basename. If it still
        # has the literal "<WORKSPACE_BASENAME>" placeholder, Phase 0 / Phase 1.B failed and
        # the bootloader will fall back to discovery on every fire (works but suboptimal).
        if "<WORKSPACE_BASENAME>" in actual_prompt:
            failures.append(f"{task_id}: registered prompt has unsubstituted <WORKSPACE_BASENAME> placeholder — Phase 0 customer confirmation may not have run, or Phase 1.B substitution failed")
            continue
        # Verify the basename string we intended to bake is actually present in the path-resolution context.
        expected_workspace_path = f'$SESSION_DIR/mnt/{workspace_basename}'  # workspace_basename from Phase 0
        if expected_workspace_path not in actual_prompt:
            failures.append(f"{task_id}: registered prompt is missing the expected workspace path {expected_workspace_path!r} (basename substitution may have written to wrong location)")
            continue
        # Size sanity check (Phase 3 / P0.2) — bounds computed from THIS RUN's
        # composed bootloader, never a hardcoded range (hardcoded ranges drift
        # the moment the template changes — the pre-Phase-3 gate failed every
        # healthy install for exactly that reason).
        expected = len(bootloaders[task_id])   # from Phase 1.B composition
        if len(actual_prompt) > expected * 1.5:
            failures.append(f"{task_id}: registered prompt is {len(actual_prompt)} chars vs ~{expected} composed — too large for a bootloader. Did the full orchestrator body get registered by mistake?")
            continue
        if len(actual_prompt) < expected * 0.9:
            failures.append(f"{task_id}: registered prompt is {len(actual_prompt)} chars vs ~{expected} composed — too small. Stub-improvisation regression?")
            continue
```

**Surface failures in plain English.** If any task fails verification:

> *"Couldn't finish setting up: [list]. Say `set up command room schedules` again to retry. If it keeps failing, reinstall Command Room the same way it was first installed — or ask whoever set it up for you."*

**Display name verification (v2.14.25+):** also verify each registered task's `description` field matches the canonical "X - Command Room" format. Specifically:
- `morning-brief` → `"Morning Brief - Command Room"`
- `upcoming-meetings` → `"Upcoming Meetings - Command Room"` (RETIRED, BRIEFMERGE — the description is for renders of a task already registered; never create one)
- `inbox` → `"Inbox - Command Room"`
- `waiting-on` → `"Waiting On - Command Room"` (CTS1; the retired `commitments` task keeps whatever description it had — it's disabled, never renamed)
- `my-plate` → `"My Plate - Command Room"` (CTS1)
- `pulse` → `"Pulse - Command Room"` (RETIRED — the description is for renders of a task already registered; never create one)
- `end-of-day` → `"End of Day - Command Room"` (EOD2)
- `past-meetings` → `"Past Meetings - Command Room"` (RENAMED, EOD2 — the description is for a task already registered; never create one, and never rewrite an existing one to the new name: the sidebar title comes from the taskId, so a renamed description on an unrenamed id reads as two different chats)
- `friday-wrap` → `"Friday Wrap - Command Room"` (NEW v3.11.0)

If it doesn't match, execute the plan `backend.plan_update(task_id, ...)` returns to fix it in place. **Read the `name` field, not `description`** — the merged app's triggers have no description, and `normalize` puts the trigger's name there; the canonical form is `schedule_backend.trigger_name(task_id, workspace_basename)`, which is the same `X - Command Room` format with the workspace's folder name appended so one account with two workspaces has two distinguishable chats. Display-name drift is a regression class on its own.

**Why this gate exists, adapted for v2.14.24.** The agent-improvisation risk doesn't go away when the pinned content becomes a bootloader — a stub bootloader could still be improvised if the registration step is loose (history of the stub class in references/HISTORY.md § v2.14.20). So Phase 3.5 verifies the registered prompt has the canonical markers + correct substitutions + frontmatter-clean + reasonable size; v2.14.25 adds display-name verification. Catches the same class of bug, scoped to the new pattern.

## Phase 4 — Onboarding integration: register the historical-backfill chunks

This phase fires ONLY when invoked by `command-room-onboarding` (the onboarding skill explicitly passes `--with-backfill` or sets a flag). When fired by direct user trigger, this phase is skipped — backfill is an onboarding concern.

The historical backfill walks the user's last 12 months of email / calendar / files / meetings at **metadata-only** level (no bodies, transcripts, or file content). It runs as a series of one-shot scheduled tasks, chunked to keep each fire's context budget under ~30K tokens.

### Step 1: detect user volume tier

Read the Phase 1 connector counts captured by onboarding (last-30d email count + calendar density + Granola transcript count). Map to a tier:

| Volume signal | Tier | Chunk strategy |
|---|---|---|
| <5,000 emails/year-projected, <500 calendar events | **light** | 3-month chunks × 4 fires, 1 hour apart |
| 5,000-30,000/year-projected | **medium** | 1-month chunks × 12 fires, 1 hour apart |
| >30,000/year-projected | **heavy** | 2-week chunks × 26 fires, 30 min apart |

If onboarding can't pass volume signals (e.g. running this skill standalone), default to **medium**.

### Step 2: register the chunks

For each chunk N (1-based), compute `fireAt = now + N × interval`. Register one-shot scheduled task:

- `taskId: "cr-historical-backfill-N"`
- `description: "Historical backfill chunk N of M — pulls metadata for [chunk window]"`
- `recurrence: "once"`
- `fireAt: "<ISO>"`
- `notifyOnCompletion: false` (don't spam — this runs in the background)
- `prompt`: see `references/orchestrator-historical-backfill.md` *(NOTE: this file should be created in v2.10.2 if not present yet — defer to a future patch if absent at install time and surface a plain-English note: "I couldn't set up the historical catch-up just now — your history will fill in gradually through your daily chats instead.")*

The prompt receives the chunk window (start/end dates) as part of the orchestrator's input. Each chunk's session:
- Fetches metadata for the window from every connector
- Writes events.jsonl in batched appends (no body content, just metadata)
- Creates provisional person + project records for clusters
- Updates the resume marker (`_hq/data/.backfill_cursor`)
- Exits cleanly

### Step 3: log the schedule

For each chunk (OMIT `seq`/`ts` — the append gate auto-stamps both inside the writer lock, `ts` in UTC; a hand-typed "now" was the F-15 naive-local-clock bug class, v4.5.2 R4.)
```jsonl
{"type":"backfill_chunk_scheduled","data":{"chunk_n":N,"of":M,"fireAt":"<ISO>","window_start":"<date>","window_end":"<date>","tier":"<light|medium|heavy>"}}
```

### Step 4: surface to user as part of onboarding close-out

The onboarding skill (NOT this skill) handles the user-facing close-out. This skill just registers the chunks silently and returns the count.

## Phase 5 — Surface install ritual + confirmation (W2 — authorize EVERYTHING, render from the registration set)

The summary block branches on `FIRST_INSTALL` (set in Phase 0.C).

**The chat list is `schedule_backend.registration_summary_lines(<the ids this run registered>, crons=CRONS)`** — each chat's display name with its LOCAL time in words, from `DEFAULT_SCHEDULES` or the customer's override; say those lines as they come back (SCHEDREG1 SHOULD 10).

**Render the ritual list from what THIS RUN actually registered — never from hardcoded copy.** The grant count and the task list come from the union of Phase 3's `tasks_to_register` + the Step 1.D silent-task set, with display names via `task_display_name()` and fire labels via `load_schedule_config()`. Hardcoded counts drift the moment the default set changes — the silent-task authorization ghost class is what this ritual exists to kill (see references/HISTORY.md § W2).

**The silent tasks are IN the summary.** Every newly registered taskId — including the silent ones — is named with the one-line reason it exists (from `SILENT_TASKS[task_id]["reason"]`). A background task nobody was told about is a background task nobody can ask after when it goes quiet, and this summary is the one moment the customer sees the whole set at once.

**There is no click-through ritual any more (SPEC_NIGHTM1_LANES §7, COPY1; gap analysis §3).** The old shape — one grant per task, pressed by hand in a list — belonged to a desktop app that registered schedules on the machine. Schedules are now account-level, a run reaches this computer through the Claude app, and the whole of the customer's setup is **one setting, once per computer**: the workspace folder in Trusted folders. So this phase teaches that setting, says out loud that the chats run without stopping for approvals, and names nothing the customer has to press per task. Render the Trusted-folders paragraph from `schedule_config.TRUSTED_FOLDERS_PARAGRAPH` — verbatim, one place, so the onboarding pointer, the update bridge and this summary cannot drift into three different instructions for one setting.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "Repaired v2.14.20–v2.14.26 registration: disabled legacy pulse-orchestrator task and registered the canonical bare-name `pulse`."
- GOOD: "Fixed an older setup issue — your Pulse chat has been re-registered fresh."

### If `FIRST_INSTALL = True` (M1 default)

Shape (counts + names + times rendered from the actual registration set — the tasks below are illustrative, not a list to copy):

```
Command Room schedules registered:

Your daily and weekly chats:
✓ Morning Brief        (7 AM weekdays — preps today's meetings, then runs before your workday)
✓ Inbox                (7:15 AM weekdays — clears your inbox before the day starts)
✓ End of Day           (5 PM weekdays — closes out the day and processes the day's calls)
✓ Friday Wrap          (1 PM Fridays — wraps your week into a recap)

Working quietly in the background (no chat output unless something needs you):
✓ Maintenance          — handles all the background upkeep in one place: closing
                         commitments you finished by email, catching unlogged
                         decisions from your chats, the weekly tidy and insight
                         refresh, and the monthly report

These chats run without stopping to ask you for approvals — that is
set on each one when it registers, so nothing waits on a click while
you're away.

ONE SETTING, ONCE ON THIS COMPUTER:

[TRUSTED_FOLDERS_PARAGRAPH — rendered verbatim from
schedule_config.TRUSTED_FOLDERS_PARAGRAPH]

ONE MORE THING — this computer has to be reachable:

The chats run in the cloud and reach this computer through the Claude
app, so the app has to be open here when one is due. "Only on this
computer" in Settings → Preferences → Tasks stops a chat when the app
closes. If a chat ever looks like it "stopped working," a closed app on
this computer is the most common reason — not an error.

Your Waiting On and My Plate chats will be added in a follow-up session once
you've been logging meetings for a couple of weeks. They work best
once there's some history for them to draw on.

To manage anytime: `list my schedules`, `pause [task name]`, or
`change my schedule` to move any of the times.
```

### If `FIRST_INSTALL = False` (existing workspace — refresh / add flow)

**Substrate write on add (FS-07 — MANDATORY):** for every task this run actually registered (the `add <task>` flow's new taskId), log the `schedule_created` event exactly as the Phase 3 loop does (shape + OMIT-seq/ts rule above). The add flow registers the chat AND writes the substrate record — skipping the event (the live `add staff meeting` gap) leaves the task with no registration trace for the watchdog / receipts. Idempotent: no event for a task that was already registered.

Same rendering rule: list what exists + what THIS RUN added (names + times from `load_schedule_config()`; live cron wins for already-registered tasks). Keep the migration notes below when they apply:

```
Command Room schedules registered:

[per-task lines rendered from the config — display name + label, silent tasks marked "(background)"]

[If Morning Brief was just added by this re-run, surface:]
Morning Brief is new. If its time collides with another chat's slot,
say `change my schedule` and I'll move one of them.

[If Friday Wrap was just added by this re-run, surface:]
Friday Wrap is new — it runs Fridays and wraps your week into a recap
you can read here or forward.

[If any SILENT_TASKS entry was just added by this re-run (Step 1.D / Phase 5.9), surface each with its reason:]
Also added in the background: [Display Name] — [reason]. It runs on
its own; you will not hear from it unless something needs you.

[If migrating from v2.9-v2.10.1, add:]
Migrated from prior version:
  • Inbox Pulse → Inbox
  • Commitments You Owe + Commitments Owed To You → Commitments (merged)
  • Meetings Processed → Past Meetings
The old entries are switched off — your scheduled chats now live under the new names.

[If `upcoming-meetings` is still registered on this machine, surface the
retirement OFFER once — never a silent disable (BRIEFMERGE §E). Render it from
`schedule_config.retirement_line("upcoming-meetings")`, verbatim.]

[EOD2 — say NOTHING here about `past-meetings`. It is renamed, not broken: it
is registered, enabled, and firing the current End of Day pack, so an install
summary has no news to report about it. The rename offer belongs to the update
bridge, ONCE, with a six-week suppression window — repeating it in every
registration re-run is the weekly nag the suppression exists to prevent.]

[If migrating from a v2.14.20 broken state where cr-pulse or cr-dont-forget was registered, add:]
Fixed an older setup issue — your Pulse chat has been re-registered fresh.

WHAT THIS RUN NEEDS FROM YOU (only if you have not done it on this
computer):

[TRUSTED_FOLDERS_PARAGRAPH — rendered verbatim from
schedule_config.TRUSTED_FOLDERS_PARAGRAPH. Say it only when this
workspace has no evidence of a successful scheduled run yet; a customer
whose chats are already reaching the workspace has already done it.]

To manage anytime: `list my schedules`, `pause [task name]`, `change my schedule`.
```

## Phase 5.9 — Silent-task registration assertion (v3.18.2+, Bug #82 — UNCONDITIONAL, runs before any Phase 6 early-exit; Phase 3 / SPEC-2.3: one loop over the SILENT_TASKS registry)

**This check is mandatory on EVERY invocation, including the re-run / "already configured" path.** Before surfacing the Phase 6 management prompt (or taking any "all current — nothing to do" early-exit), you MUST verify every task in the `SILENT_TASKS` registry (`shared/scripts/schedule_config.py` — currently one task, `maintenance`) is registered, AND that every taskId its `SUPERSEDED_BY` entry lists is disabled (the MAINT1 migration is part of this gate: an existing install re-running setup gets the five old silent tasks switched off and `maintenance` registered here, even on the early-exit path).

**Why this is its own gate.** The idempotency checks above iterate `ORCHESTRATOR_MAP` (the 7 chats). The silent tasks are intentionally NOT in that map — they are not chat-orchestrators and register separately via **Step 1.D**. So the "are all chats registered?" check is structurally blind to them: on an existing workspace (all 7 chats present), the skill reports "all current" and routes straight to Phase 6, and **Step 1.D is never reached** — exactly the v3.18.1 failure (Bug #82 — see references/HISTORY.md § Bug #82 silent-task registration miss). The silent tasks ARE in `FIRST_INSTALL_TASK_IDS`, so Phase 3's `target_set = existing | FIRST_INSTALL_TASK_IDS` already contains them — this gate makes the assertion explicit and unconditional so no branch can skip it. Looping over the registry (instead of one hand-written bullet per task, the pre-Phase-3 shape) means a future silent task cannot be forgotten here: it's covered the moment it lands in `SILENT_TASKS`.

**Do this:**

1. Reuse Phase 3.5's listing when this run already read one after its last scheduler write (SCHEDFAST1 — nothing registered since, so a second listing would only repeat it); otherwise execute the plan `backend.plan_list()` returns and `normalize` the result.
2. For EVERY `task_id` in `SILENT_TASKS`: if no task with that taskId is present → **run Step 1.D now for that task** (idempotent — if it somehow exists with a stale prompt, Step 1.D updates in place), including Step 1.D's supersede step (disable every still-enabled taskId in `SUPERSEDED_BY[task_id]`). This is the generalization of the Friday-Wrap generic-add path in `command-room-update-bridge` Phase 4.7.
3. Surface one install-summary line per task this gate actually registered (from the registry's `description`/`reason` — don't announce a no-op).
4. Only after the loop completes may you continue to Phase 6.

```python
# Pseudocode — translate to actual MCP calls.
from schedule_config import SILENT_TASKS
registered_ids = {t['taskId'] for t in backend.normalize(<the listing plan's result>,
                                                          workspace_root='$WORKSPACE')}
for task_id in SILENT_TASKS:
    if task_id not in registered_ids:
        run_step_1D(task_id)   # register from the registry — NEVER skipped by the Phase 6 early-exit
        summary_lines.append(f"Registered {SILENT_TASKS[task_id]['description']}")
```

**Authorization follow-through (W2, Phase 3 reliability).** Registration is not the finish line — a task that never reaches the customer's workspace never does anything, forever, with no symptom. What stands between registration and a working chat depends on which app the seat is on, and Phase 5 above says it in the customer's words; this is the mechanical half:

- **On the merged Claude app** there is no per-task click-through. A chat reaches the workspace by asking for the folder itself on its first fire: silently when the folder is in the desktop app's Trusted folders, otherwise with one card on that computer which the customer allows once and never sees again for that chat. The silent tasks are named in the Phase 5 summary for the same reason the visible chats are — a background task nobody was told about is one nobody can ask after — not because anything has to be pressed per task.
- **On the older desktop app** the one-time per-task permission is still real and still the customer's to give, exactly as it always was. Phase 5's summary on those seats is unchanged.
- **Either way, the backstop is the same.** The scheduled-task watchdog (`shared/scripts/task_watchdog.py`, surfaced by the morning brief and cleanup's Monday note) checks fired-recency receipts per task. If a registered task has no substrate receipt within 3 weekdays of registration, it reports `never_authorized` and the next visible surface names that chat and says in one plain sentence what is still waiting. Nothing in this skill needs to poll — the watchdog runs inside surfaces that already fire.

## Phase 6 — Re-run / management

Re-firing this skill detects existing schedules. Surfaces:

> *"Command Room schedules already configured. [N] scheduled chats running. Want to add, change, remove, or reset? (add / change / remove / reset / nothing)"*

(`[N]` is the count of currently-enabled registered taskIds — typically 5 on a fresh M1 install, up to 7 once the Waiting On and My Plate chats have been added.)

- `add` — only useful if a future version adds new chats
- `change` — list existing, ask which + new cron. THIS is the calibration entry path (v2.9.2+ doesn't ask cadence questions at first install; explicit `change` request opens that conversation).
- `remove` — list existing, ask which to disable (no delete API)
- `reset` — disable everything + re-register all v2.10.2 (fresh state, defaults)
- `nothing` — exit silently

**On a seat where schedules cannot be set up, Phase 6 is never reached** — Step 0.0's guard stopped this skill at its first line and said the one paragraph. That paragraph already names the words that produce each chat on demand; `schedule_config.PHRASE_CARD` is the same six phrases as a block when a surface wants them listed rather than said in a sentence. It is the ONE place those words live: never retype them here, in onboarding, or in the update bridge.

### Explicit calibration intent

If the user fires this skill with `customize my command room schedules` / `change my schedule cadence`, route directly into Phase 6 `change` flow without the "already configured" preamble.

## Reference files

The chat-emitting orchestrator prompts live in `references/` (workspace-map refresh was retired in v2.14.25):

- `orchestrator-morning-brief.md` (taskId `morning-brief`; display "Morning Brief"; wraps the `morning-briefing` skill, and since BRIEFMERGE runs meeting prep as its first leg)
- `orchestrator-upcoming-meetings.md` (taskId `upcoming-meetings` — **RETIRED, BRIEFMERGE**; the file is a retirement stub kept so a pre-retirement registration's bootloader still resolves. Never register it.)
- `orchestrator-inbox.md` (taskId `inbox`)
- `orchestrator-commitments.md` (taskId `waiting-on`; display "Waiting On" — CTS1: filename kept for backward compat with events.jsonl `source_skill='commitments'` history, same pattern as pulse below)
- `orchestrator-my-plate.md` (taskId `my-plate`; display "My Plate" — CTS1 Surface 2)
- `orchestrator-dont-forget.md` (taskId `pulse` — **RETIRED, LIFECYCLE1**; the file is a retirement stub kept so a pre-retirement registration's bootloader still resolves. Never register it.)
- `orchestrator-past-meetings.md` (taskIds `end-of-day` AND `past-meetings`; display "End of Day" — EOD2: ONE file serves both ids. The filename keeps the old name for the same reason `orchestrator-commitments.md` and `orchestrator-dont-forget.md` do: it is baked into every already-registered bootloader, and events written from it keep `source_skill='past-meetings'`.)
- `orchestrator-friday-wrap.md` (taskId `friday-wrap`; display "Friday Wrap"; NEW v3.11.0 — wraps the `weekly-recap` skill; first weekly-rhythm scheduled task)

The old refresh orchestrator (`cr-refresh-workspace-map`) is DELETED (Night M3, RETIRE1): it was in no `ORCHESTRATOR_MAP`, never registered since v2.14.25, and the sidebar it refreshed is retired on every seat.

**There is no `orchestrator-pulse.md` file**, and since LIFECYCLE1 there is no Pulse chat either. If you find a registered task with `taskId: "cr-pulse"`, `cr-dont-forget`, or the bare `pulse`, do NOT re-register any of them — the class is retired. Disable a legacy `cr-*` variant per the Phase 1 migration table and leave the bare `pulse` alone for the customer's own `pause` (LIFECYCLE1 §4).

**Since BRIEFMERGE the same is true of Upcoming Meetings.** `cr-meetings-today` / `cr-upcoming-meetings` are disabled per the Phase 1 table with nothing registered in their place, and a bare `upcoming-meetings` registration is left alone for the customer's own `pause`. Its prep generation now runs as the morning-brief fire's first leg; the on-demand `call-prep` triggers are untouched and remain the way to prep a meeting booked after the brief.

Tombstones (back-compat pointers; don't reference directly in new schedules):
- `orchestrator-meetings-today.md` *(does not exist — file was renamed)*
- `orchestrator-inbox-pulse.md` *(does not exist — file was renamed)*
- `orchestrator-commitment-nudge.md` (tombstone pointing at orchestrator-commitments.md)
- `orchestrator-commitment-chase.md` (tombstone pointing at orchestrator-commitments.md)
- `orchestrator-cracks-watch.md` *(does not exist — file was renamed)*
- `orchestrator-meetings-processed.md` *(does not exist — file was renamed)*

Plus shared specs:
- `SHARED_CHAT_OUTPUT_PROTOCOL.md` (in this `references/` folder) — universal chat-output rules (the 10 rules that apply across every orchestrator)
- `shared/EMAIL_DRAFT_PROTOCOL.md` (in the plugin's `shared/` folder, moved out of this skill's `references/` in v3.13.3 to reflect its universal scope — referenced by email-writer / intro-broker / follow-up-ritual / inbox-triage / dormant-customer-scan's threads lens (formerly thread-resurrection) as well as the scheduled orchestrators) — email-draft mechanics (lazy creation, Gmail/Outlook MCP defensive handling)
- `PROJECT_MAPPING_RULES.md` (in this `references/` folder) — deterministic 4-rule project resolution + plain-English unrouted heuristic

(Note: `STAGING_CONVENTION.md` and `PROVENANCE_FRONT_MATTER.md` were retired in v3.12.0. Deliverables route through `_hq/meetings/` via `brief_writer.py` per `MD_DELIVERABLE_POLICY.md`; `_hq/staging/` is now a forbidden path per the leak scanner.)

## Every sentence this chat posts goes through one door (SPEC FIXTRAIN v5.31.0 6.1 — MANDATORY)

**The whole reply goes through one door (SPEC FIXTRAIN v5.31.0 6.1, R-25 — MANDATORY).** A composer gates the sentence it built; it cannot gate the sentences typed after it. Eleven of the thirteen leaks on the v5.31.0 record were exactly that shape — a clean composed answer, then an ungated paragraph naming files, functions, event names and writer ids. `set up command room schedules` on 09-15 answered correctly and then explained itself, naming a command flag, a function, a module and five raw task ids (recorded leak instance 12). Every one of those is machinery: the reader wants to know which of their chats are on, at what time, and what changed — in the words they would use for their own calendar. So compose everything you intend to post, hand it to `post` ONCE, and print what it returns as your entire reply.

```python
import sys
sys.path.insert(0, "shared/scripts")
from surface_composers import post
# THIS SECTION HAS NO COMPOSER - it writes its own sentences, so it
# declares NOTHING. No `relayed`: there is no composer return to relay,
# and the door refuses a relay it cannot vouch for line by line. No
# `customer_rows` either. The whole reply is this product's own words and
# is scanned in full, which is the point of the door.
print(post(whole_reply, surface="schedules", workspace=workspace_root))
```

`relayed` is the composer's own return, and the door checks it twice: for PRESENCE, IN ITS OWN ORDER (paraphrasing it instead of relaying it is a refusal, not a style — and so is shuffling its lines or repeating one of them: a relay is the composer's return, not its ingredients) and for ORIGIN — every line of it must be a line a composer returned in THIS same run, and the check is in full: one unvouched line refuses the whole post. There is no share of a turn a caller may claim as already-checked. `customer_rows` is how a reply says it was composed around the CEO's own words: you name the ROWS (by seq, or by row id) and **the door reads their customer-typed fields off the book itself**. There is no argument for the words — you cannot tell this door what they typed, only which of their rows to go and read, and a call with no `workspace` declares nothing at all. **Be exact about what a declaration does**: it blanks the declared fragment out of the copy the INTERNAL-NAME classes read — `_hq/` paths, data-file and module names, script names, build codes, the vocabulary roster and the record counter — which is most of this gate, so it is not something to hand yourself. Record ids and the absolute-path scan read the whole text whatever was declared. Everything undeclared is this product's own words and is scanned in full. `post` raises rather than returning, and nothing is caught. **If it refuses, post the composer's return on its own** — it is already gated, and the paragraph that could not pass is the paragraph that should not have been written; **if even that refuses, post `surface_composers.refused_line(<surface>)` and nothing else** — one honest sentence that it could not put the answer together, with the phrase offered again. **There is no sentence after it.**

**This section composes no sentence in code, so it declares nothing.** There is no `relayed` to pass — a relay the door cannot vouch for is refused, and a section with no composer has no vouched text — and no `customer_rows`. Every word of the reply is this product's own and is scanned in full.

This covers the install ritual, the refresh flow, the re-run summary and the verify-only preview — every phase in this file that posts to chat, not one of them.

## Forbidden behaviors

- **Don't create schedules without confirmation** in interactive mode.
- **Don't duplicate schedules** — Phase 1 idempotency check is non-negotiable.
- **Don't auto-fire on creation** — registering a chat is not running it. Whatever the live backend asks for on a chat's first fire is the customer's to answer, in their own time.
- **Don't write to `_hq/staging/[date]/`** — that path was retired in v3.12.0 and is now an active leak-pattern scan target. Deliverables go through `brief_path.get_brief_path()` to `_hq/meetings/` or the typed deliverable subfolders.
- **Don't bypass the orchestrator reference files.** Each scheduled task's prompt must be the EXACT text from its reference file — tested behavior depends on the prompt being byte-stable.
- **Don't fire historical-backfill chunks outside onboarding.** Phase 4 is gated to onboarding-invoked runs only.

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Sets up Command Room's scheduled chats — daily + weekly action chats that produce drafts and surface decisions for review. On a fresh-install workspace, registers the `FIRST_INSTALL_TASK_IDS` set (`morning-brief`, `end-of-day`, `inbox`, `friday-wrap`, plus the silent `maintenance`) — the chats that establish the customer's daily and weekly rhythm. The later-add defaults get added via operator-driven follow-up sessions once enough workspace signal exists for them to fire well; every id in `schedule_config.RETIRED_TASKS` (`pulse`, `upcoming-meetings`, `past-meetings`, and the readiness retirements `commitment-triage`, `balance`, `pipeline-digest`) is never registered by any path, and `end-of-day` is fenced out of an existing workspace's target set while its renamed predecessor `past-meetings` is still registered there. On re-runs against an already-configured workspace, the existing Phase 6 (`change` / `add` / `remove` / `reset`) management flow handles task adjustments. Each chat = 1 scheduled task = 1 persistent thread wherever the live backend lists it. **Phase 0.5 opens with a substantive vanilla-vs-Command-Room explainer** before any registration happens — customers learn why scheduled tasks loaded with their substrate beat vanilla scheduled tasks before they authorize them. Triggers: 'set up command room schedules', 'enable schedules', 'register my scheduled chats', 'verify command room prompts', 'check my command room version', 'which version are my tasks on'. The registration set is NEVER expanded by trigger phrasing — "set up ALL command room scheduled tasks" registers the same first-install set, and later-add tasks are only ever proposed, never auto-registered (MAINT1 / D7 fence). DOES NOT fire on 'configure my schedules' / 'change my schedule' / 'customize my schedules' (change-schedule — cadence customization of already-registered chats; this skill registers them). Also called silently by `command-room-update-bridge` post-install + by `command-room-onboarding` for the historical-backfill registration (onboarding does NOT pass `--with-backfill` on the M1 first-install flow). Idempotent: re-runs surface the current set instead of duplicating.
