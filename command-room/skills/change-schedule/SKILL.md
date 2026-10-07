---
name: change-schedule
surfaces: both
description: "Fires FIRST on: 'show my scheduled chats', 'list my schedules', 'when do my chats run', 'when do my chats fire' — the times your Command Room scheduled chats run, never the calendar. Also the user-facing schedule mutator: 'change my schedule', 'configure my schedules', 'move [chat] to [time]', 'set [chat] to [time]', 'pause [chat]' / 'resume [chat]', 'disable [chat]' / 'enable [chat]', 'back to defaults'. Renders the registration-aware merged view (defaults + your changes + what's actually registered), converts requested times from your timezone to the machine clock at registration, and pushes cron changes to the live scheduler itself. Does NOT fire on 'set up command room schedules' (enable-command-room-schedules — first registration), 'what's my schedule today' / 'show my schedule' / 'show my week' / 'what's on my calendar this week' (calendar — not this skill), or 'health check' (system-health)."
---

# change-schedule

Per-workspace schedule customization. Built v2.14.10+ alongside the schedule_config storage layer; registration-aware since Phase 3 (2026-07). Lets the operator (or the user) adjust per-task cron schedules without forking the plugin.

## What this skill does

1. Reads the registration-aware schedule view: `load_schedule_view()` merges built-in defaults with the user's overrides from `_hq/data/entities.json` (`workspace.schedule_config` — a SPARSE override store: an entry means "the operator customized this"; missing means "default") and partitions against the registered-task set. **No task renders as scheduled unless it is actually registered** — a default entry alone does not imply registration (the pre-Phase-3 render showed relationship-moves as an enabled Sunday task on workspaces where it was never added: a ghost).
2. Shows the current state in plain English (cron-to-english helper), in two groups: **Registered** and **Available, not added**.
3. Parses what the user wants to change — natural-language patterns, listed below. User-requested times mean the WORKSPACE timezone (what "8am" means to the user); the stored cron is that same workspace wall clock, and ONE conversion happens at write time, to whichever clock the seat's scheduler actually evaluates in. **Which clock that is depends on the seat (TZ1, 2026-09-20), and nothing here decides it twice:** on a legacy desktop seat it is the machine clock (R8, confirmed live 2026-07-01 — `schedule_config.workspace_time_to_machine`); on a merged/cloud seat the trigger's cron is UTC and `schedule_backend.plan_create` / `plan_update` project the workspace-local cron onto UTC, so `workspace_time_to_machine(backend_id="cloud")` returns the time UNCHANGED rather than adding a container's offset on top. Lateness and slot math on a cloud seat reads the workspace zone through `shared/scripts/clock_policy.py`, never the host clock.
4. Atomic-writes the updated config to entities.json.
5. **Pushes the new cadence to the live chat itself** through the schedule backend seam (Step 7). Cron re-anchoring for a CUSTOMIZED task is THIS skill's job; the registration skill never re-anchors a customer-customized cron. The one exception (BRIDGESIL1, 2026-08-27) is a task whose cron is NOT customized — no `cron` override recorded here at all AND the live cron is one core itself shipped (`schedule_config.SHIPPED_CRON_HISTORY`; a live cron core never shipped is an out-of-band customization and is always preserved) — and core shipped a new default cron for it: `enable-command-room-schedules` Step 1.C2 silently carries an uncustomized cron forward to core's current default, receipted (`schedule_refreshed`) and announced once on the next morning brief, never through this skill. The moment this skill writes a `cron` override for that task, it is customized again and BRIDGESIL1's silent path stops touching it, permanently, until the customer clears the override (`back to defaults`).

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

## Read-only mode (`list my schedules` / `show my scheduled chats` / `when do my chats run`)

**This surface is the schedule of your Command Room CHATS — when each one fires — and never your calendar.** It answers `show my scheduled chats`, `list my schedules` and `when do my chats run` / `fire`. `show my schedule` is the calendar's: this skill does not fire on it and the harness's own calendar answers it (ruling R-RW-1 as M ruled it on 2026-09-22, reversing the 2026-09-21 default). A week of meetings is likewise the calendar's answer; nothing here renders one (R-FIX3-3: no Command Room calendar-week view exists, in this release or by accident).

**Ask the guard first — a stored cadence is not a running chat (SPEC_NIGHTM1_LANES §7, COPY1; SPEC_MERGEFIX1 F15).** Before rendering anything:

```python
import sys
sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
from schedule_config import scheduler_availability, unavailable_schedule_lines
avail = scheduler_availability(<this session's tool names>, workspace_root=WORKSPACE)
```

On `available: False`, render every registered chat through `unavailable_schedule_lines(<registered ids>)` — one line per chat, each saying it is **not running in this version of the Claude app** — then say `avail["line"]` once, and stop. **No time of day on any row, and no cadence suggestion.** Not "7 AM weekdays" with a parenthetical beside it: that is the exact reply a client read on 2026-09-19 as a live schedule, out of a config store no scheduler has read since the app merged. A time the customer cannot rely on is worse than no time, and `change my schedule` cannot be offered as the remedy when the change itself is refused below.

On `available: False` because THIS chat cannot name the account it would register for (`reason: no_account` — the merged app's scheduler is there, but this chat has no account of its own), the same rule holds: the refusal line stands alone, and no row carries a time.

On `available: True` this mode is unchanged — the whole render below, byte for byte, including the times.

**On the merged app's scheduler the rows are READ BACK from the registry of THIS computer, never from the stored config and never from the app's own task list (SCHEDREG1, spec §1 MUST 6; ROUTE3, walk row 5).** The discovery step above has already opened this surface with its one line. Then, in this order:

1. Execute `backend.plan_list()` once and keep its answer as LISTING. The listing is the INPUT, never the reply: it also holds another computer's switched-off copies and tasks that are not Command Room chats at all.
2. Read the stored trigger rows where the folder is, through the door. Render it with `workspace_access.py plan run_helper --json '…'` and paste what it prints, verbatim; the shape is:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "schedule_backend:read_trigger_map"}'
```

   Keep the envelope's `result` as TRIGGER_MAP; `ok:false` is a stop, never a hand retry.
3. Compose the rows in this shell, from plugin code alone (it opens no file in the customer's folder). `device_path` is THIS computer's folder, the value preamble step 1 exported as `CR_DEVICE_WORKSPACE`, so a copy registered on another computer never decides a row. **If `CR_DEVICE_WORKSPACE` is empty in this shell, set it to DEVICE from preamble step 1 in the same command; never compose the rows on the merged scheduler without it.** The one call is `sb.this_seat_chat_listing`, which wraps `sb.scheduled_chat_rows(...)` and `sb.scheduled_chat_lines(...)` and fails loud: with no device folder it lists nothing and answers one sentence instead (ROUTE3 fix round 1, N-1):

```python
import os, sys
sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
import schedule_backend as sb
answer = sb.this_seat_chat_listing(LISTING, trigger_map=TRIGGER_MAP, tz_name=TZ,
                                   device_path=os.environ.get("CR_DEVICE_WORKSPACE"))
lines = answer["lines"]
```

   (`TZ` is the workspace timezone.) One row per Command Room chat (`task_id`, `name`, local `time`, `enabled`, `registered`), the time recovered in the customer's own zone from the UTC schedule through the stored offset. When `answer["ok"]` is false, `lines` is that one sentence: say it and stop; never list from LISTING by hand instead.

**The reply is `lines`, as they come back, and NOTHING else (the one-door rule, R-25).** A registered chat with its local time (and "(paused)" when it is switched off), a chat that is not registered yet as "not running yet" with no time. Never the app's own task list, never a count of the app's other tasks (another computer's copies, test tasks, anything that is not one of these chats), and never an offer to delete, pause or clean up anything. The registry is the truth here: a chat the stored config lists but the registry does not hold is not running, whatever the config says.

When the trigger is read-only (`list my schedules`, `show my scheduled chats`, `when do my chats fire`, `when do my chats run` -- NOT `show scheduling`, which is the plate's own phrase for its TO SCHEDULE view, ruled by M on 2026-09-13 and pinned in the trigger table), skip the change flow and render the current registration-aware view with no prompt for changes. This view and the shape below are the older desktop app's; on the merged app's scheduler the whole reply is `lines` above. Shape (names/times/flags come from Step 1's real output — the tasks below are illustrative, not a canonical list):

```
Your current Command Room schedule:

  Morning Brief       — 7 AM weekdays
  Inbox               — 7:15 AM weekdays
  End of Day          — 5 PM weekdays
  Friday Wrap         — 1 PM Fridays

Background maintenance (runs quietly, no chat output):
  Maintenance         — 6:45 AM, 12:45 PM, 4:45 PM, and 5:45 PM daily
                        (sent-mail reconcile, chat reconcile,
                        meeting capture, session sweep,
                        weekly cleanup, weekly insights,
                        picking up your edits, deal signals,
                        identity reconcile, project lifecycle,
                        duplicate check,
                        unconfirmed cleanup, old questions answered,
                        booked meeting check,
                        finished and forgotten check,
                        memory coverage check,
                        flow check, daylight saving check,
                        monthly report
                        — each runs when due)

Available, not added yet:
  Commitments         — say `add commitments` when you're ready
  Relationship Moves  — say `add relationship moves`
  Staff Meeting       — say `add staff meeting`

Say `change my schedule` to adjust any of these.
```

**The "Available, not added yet" group is DERIVED from `later_add_task_ids()`, never from the sample above.** The sample is illustrative and it has drifted before — Pipeline Digest sat in it until TASKRET1 retired the task, at which point the line was offering an `add` no registration path would honour. A retired id can never appear in that group in any class (retirement takes the row out of `DEFAULT_SCHEDULES`, which is what the set derives from), and a still-registered retired task renders under "Registered" with its retirement line instead — see the retired-task rules below.

**The maintenance parenthetical is DERIVED, never copied from the sample above.**
Render it from the live registry — `maintenance_dispatcher.MAINTENANCE_JOBS`
carries every background job and its own `description`, and that registry is the
only thing that knows what actually fires. The sample block drifted from it once
already: the chat-closure leg was registered beside the mail one and this text
kept listing mail alone, so a customer read their own schedule and concluded
chat closures were not scheduled — on the exact feature whose receipts exist to
prove that they are. A hand-typed roster in a user-facing render is a promise
about someone else's data that nothing keeps.

Stop. No changes, no prompt for input.

## Change mode (`change my schedule` and variants)

**Step 0 — the guard, before Step 1 and before any plan (SPEC_NIGHTM1_LANES §7, COPY1).** `schedule_config.scheduler_availability(<this session's tool names>, workspace_root=WORKSPACE)` is the first call this mode makes. On `available: False`, say `schedule_config.change_refused_line()` — ONE sentence, the whole reply — and stop: **no view, no diff, no config write, no `schedule_config_changed` event, no plan of any kind.** Not even the local override: a store this seat's scheduler has never read is not a schedule, and writing the customer's new time into it while nothing moves is the false confirmation this lane exists to delete (`Step 8` used to print "✓ Inbox now runs at 8 AM" over exactly that). On `available: True` everything below is unchanged.

### Step 1 — Discover plugin root + load the registration-aware view

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
import sys, json
sys.path.insert(0, 'shared/scripts')
from schedule_config import load_schedule_view, task_display_name
# Registered set: workspace_config.json registered_taskIds is the maintained
# offline-first record (written by the registration skill's Phase 0.C and kept
# current through its Phase 3 / Phase 6 flows). When you have a fresh
# listing in hand — `schedule_backend.select_backend(...)`, execute the plan
# `backend.plan_list()` returns, then `backend.normalize(..., workspace_root=<workspace>)` — pass those
# taskIds instead; they're the live truth. Missing/empty -> empty set, and
# everything honestly renders as not-added.
try:
    cfg = json.load(open(f'$WORKSPACE/_hq/workspace_config.json', encoding='utf-8'))
    registered = set(cfg.get('registered_taskIds') or [])
except Exception:
    registered = set()
view = load_schedule_view(f'$WORKSPACE/_hq/data/entities.json', registered)
for tid, spec in view.items():
    name = task_display_name(tid)
    print(f'{tid}|{name}|{spec[\"label\"]}|{spec[\"enabled\"]}|{spec[\"registered\"]}|{spec[\"later_add\"]}|{spec[\"silent\"]}|{spec[\"served_by\"] or \"\"}')
"
```

Capture stdout — each line `taskId|display_name|label|enabled|registered|later_add|silent|served_by`.

`served_by` (EOD2) is empty on almost every row and almost every workspace. When it is NOT empty, this task is registered under a RENAMED PREDECESSOR's id — the row is live, and the name to render is `task_display_name(served_by)`, because that is the entry the customer can actually see in their Scheduled list. Rendering the successor's name for a row the sidebar calls something else is how a customer concludes a chat is missing and asks for a second one.

**Vantage guard (v4.5.2 R3 — F-40, widened by TRUTH1 2026-09-20):** an empty registered set is not the same fact as an empty schedule. Before rendering "nothing is registered" or an all-not-added view from one, run `task_watchdog.detect_registry_vantage(ws, records, backend=<the selected backend's id, or None>, tools=<this session's tool names>)` with whatever the normalized listing returned (`records` is the literal `None` when `schedule_config.scheduler_availability` said there is no scheduler in this session — never `[]`). If it returns a finding, **say `finding['line']` verbatim and STOP** — no schedule render, and absolutely no mutations: a change pushed from a blind vantage lands in the wrong scheduler and creates duplicates.

Say nothing of your own about WHY. The line is already composed for the case, and `finding['check']` says which case it is — `registry_vantage` (a machine-local scheduler answered with nothing, on a seat that still has one), `registry_vantage_cloud` (the chats were set up in the older desktop app and this account cannot see them), `scheduler_unreachable` (there is no scheduler in this session at all). Only the first of those is about a different computer, so a paraphrase that says "a cloud/remote chat, or a different computer" is wrong two times out of three on a merged seat. The finding also carries a `machine` field: evidence for this step, never text (F16). Only a genuinely fresh workspace (guard returns None with no registration history) renders honestly as not-set-up.

**Live-cron precision:** for registered tasks, when you already have normalized records in this conversation, render each task's LIVE `cronExpression` (via `cron_to_english`) rather than the merged config label. `normalize` always hands back the cron in the customer's own local time, even where the backend registered it in UTC, so this render never needs to know which scheduler is underneath — a default that changed in a later plugin version (e.g. friday-wrap 4 PM → 1 PM) does not move an existing registration, and the render must show what will actually fire. Don't make an extra MCP call just for the render; the config label is the designed offline default.

### Step 2 — Show current schedule + ask what to change

**Only reached on a seat where the guard said yes** (Step 0). This is also the entry point a direct cadence verb lands on — `set <chat> to <time>`, `move <chat> to <time>`, `add <chat>`, `pause <chat>`: each of them runs Step 0 first and is refused there with the one sentence when the answer is no. A refused change writes nothing at all, and the customer is not asked to pick a time for a chat that cannot be moved.

Render the same three groups as read-only mode (Registered / Background maintenance / Available-not-added), then:

```
What would you like to change? Examples:
  · `set inbox to 8am`            move one task to a new time
  · `move inbox to noon mondays`  one task, specific day(s)
  · `pause end of day`            temporarily disable one task
  · `resume end of day`           re-enable a paused task
  · `add relationship moves`      turn on an available task
  · `everything daily`            run weekdays AND weekends for all
  · `back to defaults`            reset everything to shipped defaults
  · `done` / `nothing`            keep as-is

Reply with one or more changes.
```

### Step 3 — Parse the user's reply

Recognize these patterns. Match case-insensitively. The user can stack multiple changes in one reply.

**Time changes:**
- `set <task> to <time>` / `move <task> to <time>` / `<task> at <time>`
- `<time>` accepts: `7am`, `7 am`, `7:30am`, `08:00`, `noon`, `midnight`
- Default day-of-week stays `1-5` (weekdays) unless user specifies
- **The user's time means their workspace timezone, and the SEAM converts it — you do not.** Build the cron from the time the user asked for, in their workspace timezone, and hand THAT to `backend.plan_update(cron_local=…, tz_name=<workspace timezone>)`. The seam does the one conversion the live backend needs: on the merged Claude app it projects the workspace-local cron onto world time, which is what that scheduler stores; on the older desktop app cron evaluates in the machine's own clock (R8) and the seam converts to it. Converting here as well moves the customer's 8 AM twice — once to this computer's clock and once again inside the plan — and lands the chat two offsets from the time they asked for, on a cron no later check can match.
- **The one call, when you need the machine-clock value yourself,** is `schedule_config.workspace_time_to_machine(hour, minute, workspace_root, backend_id=backend.id)`. It returns the time unchanged on the merged app (the seam owns that conversion) and today's machine-local value on the older one. Never call it without `backend_id`. The stored `label` stays the USER's requested time (presentation is workspace-TZ) and the stored `cron` stays the USER's workspace-local time. When the live backend is the older app and the two clocks differ, say so once in the Step 5 diff ("8 AM your time — 7 AM on this computer's clock").

**Day changes:**
- `<task> daily` / `<task> every day` → `* * *` for day fields
- `<task> weekdays` → `1-5`
- `<task> weekends` → `0,6`
- `<task> mondays` / `<task> on mondays` → `1`
- `<task> mon and fri` / `<task> mondays and fridays` → `1,5`

**Combined time + day:**
- `move end of day to 8am mondays` → `0 8 * * 1` (after TZ conversion)
- `inbox at 7am and 3pm weekdays` → `0 7,15 * * 1-5` (after TZ conversion)

**Enable/disable:**
- `pause <task>` / `disable <task>` / `turn off <task>` → `enabled: false`
- `resume <task>` / `enable <task>` / `turn on <task>` → `enabled: true`

**Add an available task (Phase 3 / R1 — routes through the EXISTING add path):**
- `add <task>` on a task rendered under "Available, not added" → invoke `enable-command-room-schedules`'s Phase 6 `add` flow for that taskId (it composes the bootloader, registers with the config-merged cron, and updates `workspace_config.json`). This skill does not build a second registration mechanism — the add path stays owned by the registration skill.

**Retired tasks (SPEC LIFECYCLE1, extended by SPEC BRIEFMERGE) — never offered, never added, always answerable:**

Membership is `schedule_config.RETIRED_TASKS`, never a name you remember. A retired task is not a later-add the customer hasn't got to yet; it is gone.

**A still-registered retired task is REMOVABLE DRIFT (BRIEFMERGE §E).** Schedules are per-machine, so the repo change retires nothing by itself: a machine set up before the merge still has the old entry sitting in its Scheduled list, firing a chat that now only explains itself. When the schedule view is rendered, name that row as drift in one sentence — it is registered here, it is retired, and `pause <name>` clears it — and say nothing at all when no such row exists. Idempotent in both directions: a machine that never had it gets no line ever, and a machine that pauses it gets no second offer.

- **Never render one under "Available, not added yet."** That list is for things worth turning on.
- **`add <retired task>` / `resume <retired task>` → refuse, warmly, once.** Reply with `schedule_config.retirement_line(task_id)` verbatim (it names why it went and where the work is now) and register nothing. Do not offer a workaround, and do not treat a second ask as a new decision.
- **`pause <retired task>` works normally** — a workspace that still has it registered must be able to switch it off, and that tap is the whole retirement path. `enabled: false`, same as any pause.
- **Render a still-registered retired task under "Registered"**, because it IS registered and hiding it would be a lie about the customer's own Scheduled list — with the retirement line under it so the tap is obvious. Nothing here disables it on its own (SPEC LIFECYCLE1 §4: propose, never silent).

**READINESS retirements are the third animal, and the update has already acted (SPEC TASKRET1, M's ruling 2026-08-17).** A retired row whose `schedule_config.retirement_class(task_id)` is `readiness` — `commitment-triage`, `balance`, `pipeline-digest` — came out because the product shipped it before its substrate could support it. The update bridge's readiness migration DISABLES a live registration itself and narrates it, so by the time this skill renders anything the tap has been taken. Three rules follow, and they are all about not asking for that tap twice:

- **Never call it drift, and never tell the customer to `pause` it.** The BRIEFMERGE drift sentence is for the ELIMINATED class, where the registration is still live and the customer's tap is the whole retirement path. Here it is already off, and "say `pause balance`" reads as the product not knowing its own state.
- **`add <readiness-retired task>` → refuse with `schedule_config.retirement_line(task_id)` verbatim, and register nothing.** The line is already worded for this class: it says the chat is off the schedule, why, which on-demand surface still does the work, and what brings it back. Do not add a "but I can add it anyway" — the ruling is that these do not fire on a schedule until their substrate is ready.
- **The on-demand skill is NOT retired and must not be described as one.** `triage my commitments`, `balance check`, and the pipeline report all still work in full. If the customer's real ask is the work rather than the schedule, point at the phrase — that is the honest answer and it is usually the one they wanted.

**FOLDED fires are the fourth animal, and the only one where `add` always says YES (SPEC_FLOW1 Lane H / FOLD1A, M's ruling 2026-09-07).** Membership is `schedule_config.is_folded_fire(task_id)` — `waiting-on` and `my-plate` — never a name you remember, and never `RETIRED_TASKS`. **Membership is the CLASS; whether to render any of this is the STATE (fix round 2, REVIEW_FOLD1A R-3).** `is_folded_fire` only says the id belongs to the class — it is equally true of a chat that is ON and of one the CUSTOMER paused themselves. The render below is gated on `schedule_config.fold_is_active(workspace_root, task_id)`, which is true only when the FOLD is what switched the chat off; `schedule_config.paused_by_customer(workspace_root, task_id)` is true when they did it, and that row renders as an ordinary pause with nothing said about a fold. A customer who switched Waiting On off last month must never be told an update folded it — that is the same not-knowing-its-own-state failure this whole section exists to remove, in the mirror direction. A fold is a simplification the product chose, not a gap it is waiting out: the chat works, its headline rides the morning brief, and the customer may have it back at any time, forever. The update bridge's `fold1a_fire_fold_v1` migration already disabled the live registration, receipted, so the row is REGISTERED AND OFF by the time this skill renders anything. Three rules:

- **When `fold_is_active` is true: render it under "Registered", with `schedule_config.folded_fire_line(task_id)` under the row — never under "Available, not added yet", never as drift, and never as a plain "paused".** A folded chat rendered as an ordinary pause is the product not knowing its own state: the customer did not pause it, an update did, and the line is what says so and names the way back. **When `paused_by_customer` is true instead, none of that applies:** render the ordinary paused row, say nothing about a fold, and let `resume` do what it always does. The two are answerable apart because the fold stamps its own discriminator on the stored override (`schedule_config.FOLDED_BY_KEY`, the SCHEDINH1 `superseded_by` device) and its `schedule_config_changed` event carries the same `reason` — so "did we fold this or did they pause it" is a read, never a guess.
- **`add waiting on` / `add my plate` / `bring back waiting on` / `bring back my plate` → ENABLE, never refuse, and never the Phase 6 registration path.** The task is already registered; what is off is its switch. Route to the ordinary resume: `enabled: true` through the same path `resume <task>` takes, plus the live `backend.plan_update(task_id, enabled=True)` plan, executed exactly as `resume` does. Registering a second time would give the customer two of the same chat. `resume waiting on` / `enable waiting on` are the same act under other words and already worked; these are the words the product's OWN sentence prints, so they have to land here too — a trigger the product tells a customer to say, and that nothing claims, is the offer going nowhere (the same reasoning as `add end of day` below).
- **`pause` on a FOLDED fire (`fold_is_active`) is a no-op worth one sentence, not an error.** It is already off. Say so and offer nothing. `pause` on a chat of this class that is simply ON is an ordinary pause and behaves like every other pause — the class never makes a live chat unpausable.

**RENAMED tasks are a different animal from eliminated ones (SPEC EOD2).** A retired row carrying `renamed_to` (`schedule_config.is_renamed_task(task_id)`; `past-meetings` → `end-of-day` is the first) was NOT eliminated — the chat is alive under a new id, both ids resolve to the same orchestrator, and the old registration keeps firing the current pack at the same hour for as long as the customer leaves it. Four rules follow:

- **Never call it drift and never call it retired in the render.** It is the customer's evening chat, working. Render it under "Registered" with its live time, name it as End of Day's old name in one clause, and attach `schedule_config.retirement_line("past-meetings")` — which is already worded as a switch, not a removal.
- **Never render the successor under "Available, not added yet" while the predecessor is registered.** Step 1's view already prevents this: `load_schedule_view` reports `registered: True` with `served_by: "past-meetings"` for a served successor. Read `served_by` and render ONE row, under the name the customer's Scheduled list actually shows — two rows for one 5 PM chat is how a customer ends up with two 5 PM chats.
- **A custom time set on the OLD id still applies, and the view already shows it (EOD2 / REVIEW F-1).** `load_schedule_config` inherits a renamed predecessor's override onto the successor's row when the successor has none, so a workspace customised to 4 PM renders "4 PM weekdays" — not the shipped 5 PM default — on either id. Never render the default for a row whose predecessor carries an override, and never tell a customer their evening chat runs at 5 when their own config says otherwise. **The customer's TIME travels across a rename; the retirement's own switch-off does not (SPEC SCHEDINH1).** A predecessor override carrying `superseded_by` was written by the rename, not by the customer, so its `enabled: false` is bookkeeping and `load_schedule_config` lets the successor fall through to its default (on). A predecessor paused by the customer — no `superseded_by` — still travels in full, because that pause was a decision. Never render a renamed successor as paused on the strength of an inherited retirement record: that is a nightly-firing chat being described to its owner as switched off.
- **`add end of day` is the switch, and it is two operations.** Route to registration's Phase 6 add: register `end-of-day`, then disable `past-meetings` by executing `backend.plan_update("past-meetings", enabled=False)`. Never do one half — registering the new id while the old one is still enabled gives the customer two 5 PM chats, and disabling the old one without registering the new gives them none. **The override half is ONE CALL and you may not hand-roll it: `schedule_config.apply_rename_retirement(<WORKSPACE>, "past-meetings", source_skill='change-schedule')` (SPEC SCHEDINH1).** It carries any custom cron/label from the old key onto `end-of-day`, writes the successor's own `enabled: true` so no reader has to infer it, stamps the predecessor `{enabled: false, superseded_by: "end-of-day"}`, and logs the config change for BOTH rows through the one writer — atomically, in the same file write. It raises rather than guessing if the id is not a rename, so a wrong call is loud instead of a store nobody can read back. **The predecessor's key STAYS** — this replaces the older "move the override and DELETE the old key" instruction. The key is the record of why that id went quiet, the DD-1 read carve-out is what reads it, and `check_registration_drift` already exempts a renamed predecessor from the orphan-override scan, so leaving it costs nothing. Do not delete it, and do not write either key by hand. **This call IS the config record for both rows (SPEC SCHED1 §0-4)** — do not also hand-roll a `log_schedule_config_change` for the pause half, which would double-write the same change. The 2026-08-17 fold-in is why the record matters at all: three `schedule_created` events written, and no record at all for the two chats it paused.
- **`pause past meetings` on a workspace with no `end-of-day` registered is the one pause worth a sentence before it happens.** It would leave no evening chat at all. Say that, offer `add end of day` instead, and proceed only if the customer still wants the pause — they may genuinely want no 5 PM chat, and that is their call to make knowingly.

**Bulk changes:**
- `everything daily` — all enabled tasks → `* * *` day fields
- `everything weekdays only` — all → `1-5`
- `pause everything` / `disable all` — all → `enabled: false`
- `back to defaults` / `reset` — clear `workspace.schedule_config` entirely

**Task name matching:**

Accept fuzzy matches and resolve to the **bare canonical taskId** (the key both `schedule_config.DEFAULT_SCHEDULES` and the normalized listing use — a legacy `cr-`-prefixed key would write an override that `load_schedule_config` silently ignores):

- `morning brief` / `morning briefing` / `brief` / `the brief` / `daily brief` → `morning-brief`
- `upcoming` / `upcoming meetings` / `meetings prep` / `meeting prep` → `upcoming-meetings` — **RETIRED (BRIEFMERGE, `schedule_config.RETIRED_TASKS`).** Resolve the name so a workspace that still has it registered can `pause upcoming meetings`, and NEVER offer, add or resume it: `add upcoming meetings` / `resume upcoming meetings` get the retirement line from `schedule_config.retirement_line("upcoming-meetings")` verbatim and nothing else. Its prep generation runs inside the Morning Brief now, so a request to move "when my meeting prep runs" is a request to move the Morning Brief — say so, and offer that change instead of re-enabling anything.
- `inbox` / `Inbox` / `the inbox` / `inbox triage` → `inbox`
- `waiting on` / `waiting-on` / `commitment chase` / `chase chat` → `waiting-on` (CTS1 Surface 1 — the re-scoped daily)
- `my plate` / `my-plate` / `plate` → `my-plate` (CTS1 Surface 2)
- `commitments` / `commits` → the CTS1 pair: ask which of the two split surfaces they mean (`waiting-on` = things people owe them, `my-plate` = their own list) unless the request obviously covers both (e.g. "pause commitments" pauses both). The retired `commitments` taskId itself is disabled — never re-enable or re-anchor it.
- `pulse` / `dont forget` / `don't forget` → `pulse` — **RETIRED (LIFECYCLE1, `schedule_config.RETIRED_TASKS`).** Resolve the name so a workspace that still has it registered can `pause pulse`, and NEVER offer, add or resume it: `add pulse` / `resume pulse` get the retirement line from `schedule_config.retirement_line("pulse")` verbatim and nothing else. Retired is not the same as available-not-added — see the retirement rule below.
- `end of day` / `eod` / `day close` / `evening chat` / `close out my day` → `end-of-day` (EOD2 — the 5 PM close)
- `past meetings` / `past` / `meetings processed` → `past-meetings` — **RENAMED to `end-of-day` (EOD2, `schedule_config.RETIRED_TASKS` with `renamed_to`).** Resolve the name forever: a machine set up before the rename still has it registered and firing, and the customer will keep calling it Past Meetings for months. `pause` / `set to <time>` / `resume` all work on it normally — it is a live task. `add past meetings` gets `schedule_config.retirement_line("past-meetings")` verbatim and registers nothing, because the thing to add is End of Day. A request to move "when my meetings get processed" is a request to move whichever of the two ids this machine actually has — resolve it through Step 1's `served_by`, never by guessing.
- `friday wrap` / `friday` / `weekly wrap` / `weekly recap` → `friday-wrap`
- `maintenance` / `background maintenance` / `background tasks` → `maintenance` (the TASK — moving its time moves every slot; see the MAINT1 rules below)
- `cleanup` / `clean up` / `weekly maintenance` → the `cleanup` JOB inside `maintenance` (job-level pause/resume only — see below)
- `reconcile sent` / `reconcile` / `sent reconciliation` → the `reconcile-sent` JOB inside `maintenance`
- `monthly report` / `monthly` / `operator report schedule` / `value receipt schedule` → the `monthly-report` JOB inside `maintenance`
- `weekly insights` / `insights` / `insight views` / `sunday insights` → the `weekly-insights` JOB inside `maintenance`
- `session sweep` / `sweep my sessions schedule` / `nightly sweep` → the `session-sweep` JOB inside `maintenance`
- `relationship moves` / `relationship` / `outreach pack` / `weekly outreach` → `relationship-moves`
- `commitment triage` / `triage my commitments schedule` / `friday triage` → `commitment-triage`
- `staff meeting` / `staff` / `weekly staff meeting` / `monday staff meeting` → `staff-meeting`
- `deal signals` / `deal detector` / `deal scan schedule` → the `deal-signals` JOB inside `maintenance` (job-level pause/resume only)
- `picking up your edits` / `learning` / `learn from my edits` → the `learning` JOB inside `maintenance` (job-level pause/resume only; to stop it changing anything without pausing the job, say `stop learning from my edits`)
- `monthly scorecard` / `kpi scorecard schedule` / `scorecard` → the `monthly-scorecard` JOB inside `maintenance` (SPEC OUT7 — **OPT-IN, off by default**; turning it on calls `schedule_config.set_maintenance_job_enabled` with `enabled=True`, the propose-and-confirm registration)

**MAINT1 — the maintenance task and its jobs:**

- **Moving `maintenance`'s cron moves ALL its slots.** EVERY background job (sent-mail reconcile, chat reconcile, session sweep, weekly cleanup, weekly insights, deal signals, identity reconcile, project lifecycle, monthly report — the live roster is `maintenance_dispatcher.MAINTENANCE_JOBS`, and this list is guard-checked against it) runs inside this one task; there is no per-job time to move. Say so in the Step 5 diff when the customer moves it — and warn once if the new time drops the pre-7 AM slot: the sent-mail reconcile runs before the morning brief on purpose, so a maintenance time after the brief means the brief reads yesterday's closures.
- **Pausing an individual job is a JOB-LEVEL override, not a task change.** `pause cleanup` / `pause reconcile sent` etc. go through `schedule_config.set_maintenance_job_enabled(WORKSPACE, "<job_id>", enabled=False, source_skill="change-schedule")` — **THE writer for this act**, called from Step 6 (resume is the same call with `enabled=True`). Never open `entities.json` and set the key by hand here: that function does the locked atomic write, bumps `version` / `last_writer` / `last_updated`, and logs the `schedule_config_changed` audit row the lateness ledger reads — three things an inline write has to be trusted to remember, and a second writer for one act is how they drift (DOORS1, 2026-09-14: the release one-shot that retires a job is the second caller) — the dispatcher (`maintenance_dispatcher.due_jobs`) skips a disabled job and records it in the run's `skipped_disabled`. NEVER pause the `maintenance` task itself to stop one job — that silently stops every one of them.
- **The `monthly-scorecard` job is OPT-IN, the inverse default (SPEC OUT7).** The core jobs run unless disabled; `monthly-scorecard` (in `maintenance_dispatcher.OPTIONAL_JOBS`, not `MAINTENANCE_JOBS`) is inert until the customer turns it on. "turn on the monthly scorecard" / "add the monthly scorecard" / "run a KPI scorecard every month" calls `schedule_config.set_maintenance_job_enabled` with `enabled=True` (the same job-level writer as every other job) — this IS the propose-and-confirm registration; confirm the change in one line. "pause / turn off the monthly scorecard" is the same call with `enabled=False`. It never auto-registers — absent that explicit enable, the dispatcher never surfaces it as due. It fires monthly on/after the 1st for the prior month.
- **Renders:** show `maintenance` as one background task with its slots; when asked about a specific job, say which task carries it in plain English ("Your weekly cleanup runs inside the background Maintenance task, Sundays at 5:45 PM").

(Workspaces upgraded from pre-v2.14.27 may still have a task registered under a legacy `cr-*` id; `enable-command-room-schedules` migrates those to the bare id on its next run. Resolve to the bare id regardless — that is what the live registration uses.)

If a task name doesn't match any known scheduled task, surface plain English, rendering the names FROM Step 1's view (never a hardcoded list): `"I don't recognize '<name>' as one of your scheduled tasks. They are: [registered display names] — plus the background tasks [silent display names][, and available to add: [not-added display names]]. Reply with the task name + change you want."`

### Step 4 — Validate the new cron expression

For each change, build the new cron expression (post TZ-conversion) and validate via `parse_cron()`:

```python
from schedule_config import parse_cron, CronParseError
try:
    parse_cron(new_cron)
except CronParseError as e:
    surface_error(f"Couldn't parse '{new_cron}': {e}")
```

If validation fails, surface the error in plain English and ask again — don't write the bad value to entities.json.

### Step 5 — Show the user what's about to change + confirm

Before writing, show a diff:

```
Here's what I'll change:

  Inbox          7 AM weekdays  →  8 AM weekdays
  Pulse          [paused]       →  active, 9 AM weekdays
  End of Day     5 PM weekdays  →  4:30 PM weekdays

Proceed? (yes / no / cancel)
```

If user says `y` / `yes` / `go` / `proceed` → Step 6.
If `no` / `cancel` / `wait` → exit cleanly. No changes written.

**The diff is a promise, so re-check before you show it.** Step 0's guard already ran; if anything in this conversation has since learned that the live chat cannot be moved, do not print this diff. Showing a customer "5 PM weekdays → 4:30 PM weekdays" is telling them their evening chat will arrive at 4:30 — and the write that follows can only move a stored preference, never a chat nothing is running. On a refused seat the whole of change mode is the one sentence from Step 0 and nothing else.

### Step 6 — Atomic-write entities.json + log event

**A JOB-level change is not this block.** If the change is "pause / resume one
maintenance job" (or the opt-in scorecard), do NOT open `entities.json` here:
call the one writer for that act and stop. The block below derives
`SESSION_DIR` / `PLUGIN_ROOT` / `WORKSPACE` in its own first three lines, the
same as Step 1 and the task-level block further down, because it is meant to be
run ON ITS OWN — shell variables set in one fenced block do not survive into
the next one, so a block that inherits them runs in the wrong directory against
an empty workspace path (fix round 2, review finding R-F1).

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
res = set_maintenance_job_enabled('$WORKSPACE', '<job_id>', enabled=False,
                                  source_skill='change-schedule')
print(res)
"
```

It writes `entities.json` through the locked atomic writer, bumps
`version` / `last_writer` / `last_updated`, and logs the
`schedule_config_changed` audit row through `log_schedule_config_change` —
and when the stored value already matches it writes nothing at all and
returns `changed: False`, so a resume of a job that was never paused is a
no-op rather than a phantom audit row. The block below is for TASK-level
changes (cron, enable/disable a whole task).

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
import sys, json
sys.path.insert(0, 'shared/scripts')
from atomic_write import atomic_write_json
from schedule_config import cron_to_english, log_schedule_config_change
import datetime

entities_path = f'$WORKSPACE/_hq/data/entities.json'
with open(entities_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
ws = data.setdefault('workspace', {})
sc = ws.setdefault('schedule_config', {})
# Apply changes (stub — actual values come from skill parsing). The config
# stays SPARSE: only tasks the user actually customized get an entry.
changes = <list of (task_id, cron, enabled) tuples from Step 3>
for task_id, cron, enabled in changes:
    spec = sc.setdefault(task_id, {})
    if cron is not None:
        spec['cron'] = cron
        spec['label'] = cron_to_english(cron)  # or the user's workspace-TZ phrasing when the TZ conversion shifted the cron
    if enabled is not None:
        spec['enabled'] = enabled
data['version'] = data.get('version', 1) + 1
data['last_writer'] = 'change-schedule'
data['last_updated'] = datetime.datetime.utcnow().isoformat() + 'Z'
atomic_write_json(entities_path, data)

# Audit event through the ONE writer (SCHED1) — it routes the canonical gate,
# so seq/ts are auto-stamped inside the writer lock; never hand-roll a next_seq
# + open-append, and never hand-roll this event shape either (see
# WORKSPACE_API.md §3). Registration's pause paths call the same helper.
log_schedule_config_change('$WORKSPACE', changes, source_skill='change-schedule')
print('CONFIG_WRITTEN')
"
```

### Step 7 — Push the new cadence to the live task YOURSELF (Phase 3 / P0.1)

For each changed task, ask the seam for a plan and execute it:

```python
import schedule_backend as sb
backend, found = sb.select_backend(<the tool list you can see>)
triggers = sb.read_trigger_map(<workspace root>)          # written at registration
plan = backend.plan_update(task_id, trigger_id=(triggers.get(task_id) or {}).get("trigger_id"),
                           cron_local=<new cron>, enabled=<new enabled flag>,
                           tz_name=<workspace timezone>)
```

`read_trigger_map` is the ONE place the live trigger's id is looked up — it is stored per chat at registration and it is what the merged app's update call is addressed to (on the merged app read it where the folder is: `plan run_helper schedule_backend:read_trigger_map`). A chat with no row there has never been registered on this backend, and the config write alone is correct (see below). On a cron change the plan's `after` list carries `schedule_backend.record_trigger_map` with `merge=True`; run it with the rest of the `after` list — through the write door, as `workspace_access.py plan run_writer --json '<schedule_backend.trigger_row_payload(that entry)>'`, pasted as printed — or the stored row keeps the old offset and the nightly daylight-saving check re-projects the same chat every night.

**Never pass `prompt`** (the registered bootloader is registration's property; touching it here risks stomping a newer bootloader with nothing).

**If the seam hands back a `Refusal` instead of a plan, the change did not happen.** Say the refusal's `line` — one sentence, exactly as written — and write NOTHING: no `schedule_config_changed`, no local override, no confirmation. This is the order that matters. Step 6's config write must happen only AFTER Step 7 has a real plan in hand, because a config write followed by a refused push is the exact false confirmation this step was built to stop: the store says 4 PM, the live chat still fires at 1 PM, and the customer was told it moved.

**Cron re-anchoring is THIS skill's job.** The registration skill (`enable-command-room-schedules`) deliberately preserves whatever cron it finds on a registered task — its custom-cron-preservation rule exists so plugin updates never stomp an operator's chosen time. Pre-Phase-3, this step said "invoke enable-command-room-schedules silently... new cron values get propagated" while enable's Phase 3 said "NEVER re-anchor a registered cron from here... that is change-schedule's job" — a model honoring both wrote entities.json, left the live task on the old time, and then confirmed "✓ Inbox now fires at 8 AM" falsely. The two halves are now explicit: **this skill writes config AND re-anchors the live cron; registration writes prompts AND never touches a registered cron.**

If a changed task turns out not to be registered at all (it was in the "Available, not added" group), there is nothing to re-anchor — the config write alone is correct; the value applies when the task is added.

**A schedule change NEVER fires the task (v4.5.2 R2 — FINDINGS F-51).** Re-anchoring moves the NEXT occurrence; it does not create a missed slot for today. If the new time is already past at the moment of the change, the task simply fires at its next future occurrence — do NOT run the task "to catch up," do NOT invoke its orchestrator, do NOT compute or write any lateness for the moved slot, and do NOT reason about it ("the 9:30 slot was missed" is false — the slot did not exist when 9:30 passed). The live case: a 9:00→9:30 Pulse move at 2:46 PM fabricated a 317-minute late_fire plus a phantom 2:54 PM catch-up run against a slot the change itself created, on a day whose fire had already run at 9:09 under the old cron. Defensively, `late_fire.check_lateness` now refuses to score any slot older than the task's latest `schedule_config_changed` event — but the contract is that this path never invokes lateness at all.

### Step 8 — Confirm to user + STOP

One-line confirmation per change, only after Step 7's update plans were executed and actually succeeded. A refusal is not a change: it gets the refusal's sentence, and nothing else. No CHANGELOG narration, no internal mechanics.

```
✓ Inbox now runs at 8 AM weekdays.
✓ Pulse resumed.
✓ End of Day now runs at 4:30 PM weekdays.

Your new schedule starts tomorrow morning.
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "✓ Inbox now fires at 8 AM weekdays."
- GOOD: "✓ Inbox now runs at 8 AM weekdays."

Stop. No widget, no follow-up suggestion, no "want to change anything else?"

## Edge cases

**No workspace set up yet.** Surface plain English: `"Your Command Room isn't set up yet — let's do that first. Say `set up command room` to begin."` Stop.

**Live-task update fails after a successful config write (Step 7 error).** The new schedule was saved but the live task didn't re-anchor. Surface: `"Saved your new schedule, but the live task didn't pick it up just yet. Say `change my schedule` again in a minute to retry."` Don't roll back the write — partial success is recoverable, and the config value is what any future registration of that task will use.

**User asks for an invalid cron value (e.g., `set inbox to 25am`).** Surface the parse error in plain English. Don't write. Ask again.

**User wants to add a task that exists in DEFAULT_SCHEDULES but isn't registered.** That's the `add <task>` flow (Step 3) — route through the registration skill's Phase 6 add path. **User wants a task that doesn't exist in DEFAULT_SCHEDULES at all** — reject: this skill customizes any task in `DEFAULT_SCHEDULES` (count it from the registry at run time — never from a number typed here) plus the job-level pause/resume inside `maintenance`; brand-new taskIds ship via plugin updates, not user customization. **A RETIRED id is a different rejection with a different sentence** — it is not an unknown task, it is a known one that is gone, so answer it with `schedule_config.retirement_line(task_id)` per the retired-task rules above rather than with this generic reject.

**User says `everything daily` while one task is paused.** Apply the cron change but leave the paused task paused. They'd say `resume everything` separately to reactivate.

## Forbidden behaviors

- **Do NOT fire, run, or invoke a task from this skill — ever.** A cron move is a config write + re-anchor, nothing else: no catch-up runs, no late_fire events, no lateness receipts (F-51 — Step 7's "schedule change NEVER fires the task" rule).
- **Do NOT write directly to entities.json without atomic_write_json.** Drive-sync corruption risk.
- **Do NOT pass `prompt` to an update plan.** Step 7 re-anchors cron and enabled ONLY. Prompt content is registration's job (`set up command room schedules`).
- **Do NOT render a task as scheduled unless it is registered.** The merged config alone is not evidence a task exists in Cowork's scheduler — that's the R1 ghost-task bug.
- **Do NOT densify the config.** `workspace.schedule_config` is a sparse override store — an entry means "operator customized this." Never write default values for tasks the user didn't change (it would destroy the "non-default cron = user-set" signal every consumer relies on).
- **Do NOT prompt for changes if the user asked for read-only mode.** `show my scheduled chats` is read-only — render and stop.
- **Do NOT introduce new taskIds via this skill.** Tasks are defined in `schedule_config.py` `DEFAULT_SCHEDULES`; the add flow only registers tasks that already exist there.
- **Do NOT narrate event-type names or file paths in chat.** Per CONTRACT.md Rule 4 — no `schedule_config_changed event written` or `entities.json updated`. Just plain-English confirmation of what changed.

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

> Customize when each Command Room scheduled chat fires. Reads current schedule from entities.json merged with defaults AND the registered-task set (so only chats that are actually registered render as scheduled — Phase 3/R1), shows it in plain English, accepts changes (move time, switch days, pause/resume, disable/enable), atomic-writes the config, and pushes the new cadence to the live chats itself through the schedule backend seam (Phase 3/P0.1 — cron re-anchoring is THIS skill's job). Triggers: 'change my schedule', 'change schedule', 'update my schedule', 'configure schedules', 'configure my schedules', 'customize my schedules', 'set [task] to [time]', 'move [task] to [time]', 'pause [task]', 'resume [task]', 'disable [task]', 'enable [task]', 'add staff meeting', 'add relationship moves', 'add commitments', 'add pulse', 'add commitment triage', 'add balance', 'add pipeline digest' (the later-add turn-on phrases — each routes to the registration skill's Phase 6 add, this skill never builds a second registration mechanism), 'add waiting on', 'add my plate', 'bring back waiting on', 'bring back my plate' (SPEC_FLOW1 Lane H / FOLD1A — the FOLD switch-back, and the exact phrases the fold line prints to the customer. These do NOT route to Phase 6: a folded fire is already registered and merely switched off, so they take the ordinary resume path — `enabled: true` plus the live enable call — and are never refused), 'add end of day' (SPEC EOD2 — the RENAME SWITCH, and the exact phrase the retirement line hands a customer still on the old id; it routes to the same Phase 6 add, which registers the new taskId AND disables the predecessor in one step. `add past meetings` is deliberately NOT declared: claiming it would give this skill a claim on every past-meetings utterance, the unowned `regenerate past meetings` included, so that refusal rides the generic retired-task rule in the body instead of a trigger of its own. Nothing in this paragraph may quote a bare taskId — the mechanical matcher reads quoted strings here as owned triggers, which is exactly how that hijack got in), 'list my schedules', 'show my scheduled chats', 'when do my chats fire', 'when do my chats run'. Use when the user wants daily/weekly/cadence customization per task. DOES NOT fire on 'set up command room schedules' (that's the registration skill — change-schedule modifies the config that registration reads), 'what's my schedule today' / 'show my schedule' / 'show my week' / 'what's on my calendar this week' (a calendar read — morning-briefing covers today; the calendar itself covers the rest; this skill only manages Command Room's scheduled chats). `show my schedule` was given to this skill on 2026-09-21 (FIX3 F3-5) and ruled the other way on 2026-09-22 (R-RW-1): the calendar keeps it.
