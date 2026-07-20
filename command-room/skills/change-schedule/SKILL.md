---
name: change-schedule
description: "Customize when each Command Room scheduled chat fires — the user-facing schedule mutator. Fires on: 'change my schedule', 'show my schedule', 'when do my chats run', 'show my scheduled chats', 'configure my schedules', 'move [chat] to [time]', 'set [chat] to [time]', 'pause [chat]' / 'resume [chat]', 'disable [chat]' / 'enable [chat]', 'back to defaults'. Renders the registration-aware merged view (defaults + your changes + what's actually registered), converts requested times from your timezone to the machine clock at registration, and pushes cron changes to the live scheduler itself. Does NOT fire on 'set up command room schedules' (enable-command-room-schedules — first registration), 'what's my schedule today' (calendar / morning-briefing), or 'health check' (system-health). Command grammar and config semantics: Routing section in the body."
---

# change-schedule

Per-workspace schedule customization. Built v2.14.10+ alongside the schedule_config storage layer; registration-aware since Phase 3 (2026-07). Lets the operator (or the user) adjust per-task cron schedules without forking the plugin.

## What this skill does

1. Reads the registration-aware schedule view: `load_schedule_view()` merges built-in defaults with the user's overrides from `_hq/data/entities.json` (`workspace.schedule_config` — a SPARSE override store: an entry means "the operator customized this"; missing means "default") and partitions against the registered-task set. **No task renders as scheduled unless it is actually registered** — a default entry alone does not imply registration (the pre-Phase-3 render showed relationship-moves as an enabled Sunday task on workspaces where it was never added: a ghost).
2. Shows the current state in plain English (cron-to-english helper), in two groups: **Registered** and **Available, not added**.
3. Parses what the user wants to change — natural-language patterns, listed below. User-requested times mean the WORKSPACE timezone (what "8am" means to the user); the stored cron is machine-local (R8 — Cowork evaluates cron on the machine clock), converted at write time.
4. Atomic-writes the updated config to entities.json.
5. **Pushes the new cadence to the live task itself** via `mcp__scheduled-tasks__update_scheduled_task` (Step 7). Cron re-anchoring is THIS skill's job; the registration skill deliberately never re-anchors a registered task's cron.

## Read-only mode (`list my schedules` / `show my scheduled chats`)

When the trigger is read-only (`list my schedules`, `show my scheduled chats`, `when do my chats fire`, `when do my chats run`), skip the change flow and render the current registration-aware view with no prompt for changes. Shape (names/times/flags come from Step 1's real output — the tasks below are illustrative, not a canonical list):

```
Your current Command Room schedule:

  Morning Brief       — 7 AM weekdays
  Upcoming Meetings   — 6:30 AM weekdays
  Inbox               — 7:15 AM weekdays
  Past Meetings       — 5 PM weekdays
  Friday Wrap         — 1 PM Fridays

Background maintenance (runs quietly, no chat output):
  Maintenance         — 6:45 AM, 12:45 PM, and 5:45 PM daily
                        (sent-mail reconcile, session sweep, weekly cleanup
                        + insights, monthly report — each runs when due)

Available, not added yet:
  Commitments         — say `add commitments` when you're ready
  Pulse               — say `add pulse`
  Relationship Moves  — say `add relationship moves`
  Staff Meeting       — say `add staff meeting`
  Pipeline Digest     — say `add pipeline digest`

Say `change my schedule` to adjust any of these.
```

Stop. No changes, no prompt for input.

## Change mode (`change my schedule` and variants)

### Step 1 — Discover plugin root + load the registration-aware view

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from schedule_config import load_schedule_view, task_display_name
# Registered set: workspace_config.json registered_taskIds is the maintained
# offline-first record (written by the registration skill's Phase 0.C and kept
# current through its Phase 3 / Phase 6 flows). When you have fresh
# mcp__scheduled-tasks__list_scheduled_tasks output in hand, pass those
# taskIds instead — they're the live truth. Missing/empty -> empty set, and
# everything honestly renders as not-added.
try:
    cfg = json.load(open(f'$WORKSPACE/_hq/workspace_config.json', encoding='utf-8'))
    registered = set(cfg.get('registered_taskIds') or [])
except Exception:
    registered = set()
view = load_schedule_view(f'$WORKSPACE/_hq/data/entities.json', registered)
for tid, spec in view.items():
    name = task_display_name(tid)
    print(f'{tid}|{name}|{spec[\"label\"]}|{spec[\"enabled\"]}|{spec[\"registered\"]}|{spec[\"later_add\"]}|{spec[\"silent\"]}')
"
```

Capture stdout — each line `taskId|display_name|label|enabled|registered|later_add|silent`.

**Vantage guard (v4.5.2 R3 — F-40):** the scheduler registry is MACHINE-LOCAL; a cloud/remote chat (or a different computer) reads it empty even when all tasks are live. Before rendering "nothing is registered" or an all-not-added view from an empty registered set, run `task_watchdog.detect_registry_vantage(ws, records)` with whatever `list_scheduled_tasks` returned. If it returns a finding, say its `line` ("I can't see your scheduler from this chat — … open a local (non-cloud) chat") and STOP — no schedule render, and absolutely no mutations: `update_scheduled_task` / registration from a blind vantage lands in the wrong scheduler and creates duplicates. Only a genuinely fresh workspace (guard returns None with no registration history) renders honestly as not-set-up.

**Live-cron precision:** for registered tasks, when you already have `list_scheduled_tasks` records in this conversation, render each task's LIVE `cronExpression` (via `cron_to_english`) rather than the merged config label — a default that changed in a later plugin version (e.g. friday-wrap 4 PM → 1 PM) does not move an existing registration, and the render must show what will actually fire. Don't make an extra MCP call just for the render; the config label is the designed offline default.

### Step 2 — Show current schedule + ask what to change

Render the same three groups as read-only mode (Registered / Background maintenance / Available-not-added), then:

```
What would you like to change? Examples:
  · `set inbox to 8am`            move one task to a new time
  · `move pulse to noon mondays`  one task, specific day(s)
  · `pause past meetings`         temporarily disable one task
  · `resume pulse`                re-enable a paused task
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
- **The user's time means their workspace timezone.** Before building the cron, convert via `schedule_config.workspace_time_to_machine(hour, minute, workspace_root)` — cron evaluates in MACHINE-local time (R8), and on a machine whose clock differs from the workspace TZ an unconverted "8am" fires an hour or more off. The stored `label` stays the USER's requested time (presentation is workspace-TZ); the stored `cron` carries the converted machine-local time. When the two differ, say so once in the Step 5 diff ("8 AM your time — 7 AM on this computer's clock").

**Day changes:**
- `<task> daily` / `<task> every day` → `* * *` for day fields
- `<task> weekdays` → `1-5`
- `<task> weekends` → `0,6`
- `<task> mondays` / `<task> on mondays` → `1`
- `<task> mon and fri` / `<task> mondays and fridays` → `1,5`

**Combined time + day:**
- `move pulse to 8am mondays` → `0 8 * * 1` (after TZ conversion)
- `inbox at 7am and 3pm weekdays` → `0 7,15 * * 1-5` (after TZ conversion)

**Enable/disable:**
- `pause <task>` / `disable <task>` / `turn off <task>` → `enabled: false`
- `resume <task>` / `enable <task>` / `turn on <task>` → `enabled: true`

**Add an available task (Phase 3 / R1 — routes through the EXISTING add path):**
- `add <task>` on a task rendered under "Available, not added" → invoke `enable-command-room-schedules`'s Phase 6 `add` flow for that taskId (it composes the bootloader, registers with the config-merged cron, and updates `workspace_config.json`). This skill does not build a second registration mechanism — the add path stays owned by the registration skill.

**Bulk changes:**
- `everything daily` — all enabled tasks → `* * *` day fields
- `everything weekdays only` — all → `1-5`
- `pause everything` / `disable all` — all → `enabled: false`
- `back to defaults` / `reset` — clear `workspace.schedule_config` entirely

**Task name matching:**

Accept fuzzy matches and resolve to the **bare canonical taskId** (the key both `schedule_config.DEFAULT_SCHEDULES` and `mcp__scheduled-tasks__update_scheduled_task` use — a legacy `cr-`-prefixed key would write an override that `load_schedule_config` silently ignores):

- `morning brief` / `morning briefing` / `brief` / `the brief` / `daily brief` → `morning-brief`
- `upcoming` / `upcoming meetings` / `meetings prep` / `meeting prep` → `upcoming-meetings`
- `inbox` / `Inbox` / `the inbox` / `inbox triage` → `inbox`
- `waiting on` / `waiting-on` / `commitment chase` / `chase chat` → `waiting-on` (CTS1 Surface 1 — the re-scoped daily)
- `my plate` / `my-plate` / `plate` → `my-plate` (CTS1 Surface 2)
- `commitments` / `commits` → the CTS1 pair: ask which of the two split surfaces they mean (`waiting-on` = things people owe them, `my-plate` = their own list) unless the request obviously covers both (e.g. "pause commitments" pauses both). The retired `commitments` taskId itself is disabled — never re-enable or re-anchor it.
- `pulse` / `dont forget` / `don't forget` → `pulse`
- `past meetings` / `past` / `meetings processed` → `past-meetings`
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
- `monthly scorecard` / `kpi scorecard schedule` / `scorecard` → the `monthly-scorecard` JOB inside `maintenance` (SPEC OUT7 — **OPT-IN, off by default**; turning it on writes `maintenance_jobs.monthly-scorecard = {"enabled": true}`, the propose-and-confirm registration)

**MAINT1 — the maintenance task and its jobs:**

- **Moving `maintenance`'s cron moves ALL its slots.** The six background jobs (sent-mail reconcile, session sweep, weekly cleanup, weekly insights, deal signals, monthly report) all run inside this one task; there is no per-job time to move. Say so in the Step 5 diff when the customer moves it — and warn once if the new time drops the pre-7 AM slot: the sent-mail reconcile runs before the morning brief on purpose, so a maintenance time after the brief means the brief reads yesterday's closures.
- **Pausing an individual job is a JOB-LEVEL override, not a task change.** `pause cleanup` / `pause reconcile sent` etc. writes `workspace.schedule_config.maintenance_jobs.<job_id> = {"enabled": false}` via the same Step 6 atomic write (resume deletes the key or sets `enabled: true`) — the dispatcher (`maintenance_dispatcher.due_jobs`) skips a disabled job and records it in the run's `skipped_disabled`. NEVER pause the `maintenance` task itself to stop one job — that silently stops all five.
- **The `monthly-scorecard` job is OPT-IN, the inverse default (SPEC OUT7).** The six core jobs run unless disabled; `monthly-scorecard` (in `maintenance_dispatcher.OPTIONAL_JOBS`, not `MAINTENANCE_JOBS`) is inert until the customer turns it on. "turn on the monthly scorecard" / "add the monthly scorecard" / "run a KPI scorecard every month" writes `maintenance_jobs.monthly-scorecard = {"enabled": true}` (same Step 6 atomic write) — this IS the propose-and-confirm registration; confirm the change in one line. "pause / turn off the monthly scorecard" sets `{"enabled": false}` (or deletes the key). It never auto-registers — absent that explicit enable, the dispatcher never surfaces it as due. It fires monthly on/after the 1st for the prior month.
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
  Past Meetings  5 PM weekdays  →  4:30 PM weekdays

Proceed? (yes / no / cancel)
```

If user says `y` / `yes` / `go` / `proceed` → Step 6.
If `no` / `cancel` / `wait` → exit cleanly. No changes written.

### Step 6 — Atomic-write entities.json + log event

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from atomic_write import atomic_write_json
from event_gate import append_event
from schedule_config import cron_to_english
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

# Audit event through the canonical gate (seq/ts auto-stamped inside the
# writer lock — never hand-roll a next_seq + open-append; see WORKSPACE_API.md §3).
events_path = f'$WORKSPACE/_hq/data/events.jsonl'
append_event(events_path, {
    'type': 'schedule_config_changed',
    'source_skill': 'change-schedule',
    'data': {'changes': [{'task_id': t, 'cron': c, 'enabled': e} for t, c, e in changes]},
}, holder='change-schedule')
print('CONFIG_WRITTEN')
"
```

### Step 7 — Push the new cadence to the live task YOURSELF (Phase 3 / P0.1)

For each changed task, call `mcp__scheduled-tasks__update_scheduled_task(taskId, cronExpression=<new cron>, enabled=<new enabled flag>)` — **never pass `prompt`** (the registered bootloader is registration's property; touching it here risks stomping a newer bootloader with nothing).

**Cron re-anchoring is THIS skill's job.** The registration skill (`enable-command-room-schedules`) deliberately preserves whatever cron it finds on a registered task — its custom-cron-preservation rule exists so plugin updates never stomp an operator's chosen time. Pre-Phase-3, this step said "invoke enable-command-room-schedules silently... new cron values get propagated" while enable's Phase 3 said "NEVER pass cronExpression to update_scheduled_task... that is change-schedule's job" — a model honoring both wrote entities.json, left the live task on the old time, and then confirmed "✓ Inbox now fires at 8 AM" falsely. The two halves are now explicit: **this skill writes config AND re-anchors the live cron; registration writes prompts AND never touches a registered cron.**

If a changed task turns out not to be registered at all (it was in the "Available, not added" group), there is nothing to re-anchor — the config write alone is correct; the value applies when the task is added.

**A schedule change NEVER fires the task (v4.5.2 R2 — FINDINGS F-51).** Re-anchoring moves the NEXT occurrence; it does not create a missed slot for today. If the new time is already past at the moment of the change, the task simply fires at its next future occurrence — do NOT run the task "to catch up," do NOT invoke its orchestrator, do NOT compute or write any lateness for the moved slot, and do NOT reason about it ("the 9:30 slot was missed" is false — the slot did not exist when 9:30 passed). The live case: a 9:00→9:30 Pulse move at 2:46 PM fabricated a 317-minute late_fire plus a phantom 2:54 PM catch-up run against a slot the change itself created, on a day whose fire had already run at 9:09 under the old cron. Defensively, `late_fire.check_lateness` now refuses to score any slot older than the task's latest `schedule_config_changed` event — but the contract is that this path never invokes lateness at all.

### Step 8 — Confirm to user + STOP

One-line confirmation per change, only after Step 7's `update_scheduled_task` calls actually succeeded. No CHANGELOG narration, no internal mechanics.

```
✓ Inbox now runs at 8 AM weekdays.
✓ Pulse resumed.
✓ Past Meetings now runs at 4:30 PM weekdays.

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

**User wants to add a task that exists in DEFAULT_SCHEDULES but isn't registered.** That's the `add <task>` flow (Step 3) — route through the registration skill's Phase 6 add path. **User wants a task that doesn't exist in DEFAULT_SCHEDULES at all** — reject: this skill customizes any task in `DEFAULT_SCHEDULES` (currently 13, `balance` included) plus the job-level pause/resume inside `maintenance`; brand-new taskIds ship via plugin updates, not user customization.

**User says `everything daily` while one task is paused.** Apply the cron change but leave the paused task paused. They'd say `resume everything` separately to reactivate.

## Forbidden behaviors

- **Do NOT fire, run, or invoke a task from this skill — ever.** A cron move is a config write + re-anchor, nothing else: no catch-up runs, no late_fire events, no lateness receipts (F-51 — Step 7's "schedule change NEVER fires the task" rule).
- **Do NOT write directly to entities.json without atomic_write_json.** Drive-sync corruption risk.
- **Do NOT pass `prompt` to `update_scheduled_task`.** Step 7 re-anchors `cronExpression`/`enabled` ONLY. Prompt content is registration's job (`set up command room schedules`).
- **Do NOT render a task as scheduled unless it is registered.** The merged config alone is not evidence a task exists in Cowork's scheduler — that's the R1 ghost-task bug.
- **Do NOT densify the config.** `workspace.schedule_config` is a sparse override store — an entry means "operator customized this." Never write default values for tasks the user didn't change (it would destroy the "non-default cron = user-set" signal every consumer relies on).
- **Do NOT prompt for changes if the user asked for read-only mode.** `show my schedule` is read-only — render and stop.
- **Do NOT introduce new taskIds via this skill.** Tasks are defined in `schedule_config.py` `DEFAULT_SCHEDULES`; the add flow only registers tasks that already exist there.
- **Do NOT narrate event-type names or file paths in chat.** Per CONTRACT.md Rule 4 — no `schedule_config_changed event written` or `entities.json updated`. Just plain-English confirmation of what changed.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Customize when each Command Room scheduled chat fires. Reads current schedule from entities.json merged with defaults AND the registered-task set (so only tasks that actually exist in Cowork's scheduler render as scheduled — Phase 3/R1), shows it in plain English, accepts changes (move time, switch days, pause/resume, disable/enable), atomic-writes the config, and pushes the new cadence to the live tasks itself via update_scheduled_task (Phase 3/P0.1 — cron re-anchoring is THIS skill's job). Triggers: 'change my schedule', 'change schedule', 'update my schedule', 'configure schedules', 'configure my schedules', 'customize my schedules', 'set [task] to [time]', 'move [task] to [time]', 'pause [task]', 'resume [task]', 'disable [task]', 'enable [task]', 'add staff meeting', 'add relationship moves', 'add commitments', 'add pulse', 'add commitment triage', 'add balance', 'add pipeline digest' (the later-add turn-on phrases — each routes to the registration skill's Phase 6 add, this skill never builds a second registration mechanism), 'list my schedules', 'show my scheduled chats', 'when do my chats fire', 'when do my chats run'. Use when the user wants daily/weekly/cadence customization per task. DOES NOT fire on 'set up command room schedules' (that's the registration skill — change-schedule modifies the config that registration reads), 'what's my schedule' / 'show my schedule' / 'what's on my calendar' (a calendar read — morning-briefing covers today; the calendar itself covers the rest; this skill only manages Command Room's scheduled chats).
