---
name: change-schedule
description: "Customize when each Command Room scheduled chat fires. Reads current schedule from entities.json, shows in plain English, accepts changes (move time, switch days, pause/resume, disable/enable), atomic-writes the config, re-registers tasks with the new cadence. Triggers: `change my schedule`, `change schedule`, `update my schedule`, `configure schedules`, `customize my schedules`, `set [task] to [time]`, `move [task] to [time]`, `pause [task]`, `resume [task]`, `disable [task]`, `enable [task]`, `show my schedule`, `what's my schedule`, `when do my chats fire`. Use when the user wants daily/weekly/cadence customization per task. DOES NOT fire on `set up command room schedules` (that's the registration skill — change-schedule modifies the config that registration reads)."
---

# change-schedule

Per-workspace schedule customization. Built v2.14.10+ alongside the schedule_config storage layer. Lets the operator (or the user) adjust per-task cron schedules without forking the plugin.

## What this skill does

1. Reads the user's current schedule from `_hq/data/entities.json` (workspace.schedule_config) merged with built-in defaults via `shared/scripts/schedule_config.py`.
2. Shows the current state in plain English (cron-to-english helper).
3. Parses what the user wants to change — natural-language patterns, listed below.
4. Atomic-writes the updated config to entities.json.
5. Invokes `enable-command-room-schedules` silently to re-register tasks with the new values (the registration skill has v2.11.4+ self-refresh logic, so updates propagate immediately).

## Read-only mode (`show my schedule` / `what's my schedule`)

When the trigger is read-only (`show my schedule`, `what's my schedule`, `when do my chats fire`), skip the change flow and render the current schedule with no prompt for changes:

```
Your current Command Room schedule:

  Upcoming Meetings   — 6:30 AM weekdays
  Inbox               — 7 AM weekdays
  Commitments         — 8:30 AM weekdays
  Pulse               — 9 AM weekdays
  Past Meetings       — 5 PM weekdays

Say `change my schedule` to adjust any of these.
```

Stop. No changes, no prompt for input.

## Change mode (`change my schedule` and variants)

### Step 1 — Discover plugin root + load current config

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from schedule_config import load_schedule_config, task_display_name
config = load_schedule_config(f'$WORKSPACE/_hq/data/entities.json')
for tid, spec in config.items():
    name = task_display_name(tid)
    status = '' if spec['enabled'] else ' [paused]'
    print(f'{tid}|{name}|{spec[\"label\"]}|{spec[\"enabled\"]}')
"
```

Capture stdout — each line `taskId|display_name|label|enabled`.

### Step 2 — Show current schedule + ask what to change

```
Your current Command Room schedule:

  Upcoming Meetings   — 6:30 AM weekdays
  Inbox               — 7 AM weekdays
  Commitments         — 8:30 AM weekdays
  Pulse               — 9 AM weekdays   [paused]
  Past Meetings       — 5 PM weekdays

What would you like to change? Examples:
  · `set inbox to 8am`            move one task to a new time
  · `move pulse to noon mondays`  one task, specific day(s)
  · `pause past meetings`         temporarily disable one task
  · `resume pulse`                re-enable a paused task
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

**Day changes:**
- `<task> daily` / `<task> every day` → `* * *` for day fields
- `<task> weekdays` → `1-5`
- `<task> weekends` → `0,6`
- `<task> mondays` / `<task> on mondays` → `1`
- `<task> mon and fri` / `<task> mondays and fridays` → `1,5`

**Combined time + day:**
- `move pulse to 8am mondays` → `0 8 * * 1`
- `inbox at 7am and 3pm weekdays` → `0 7,15 * * 1-5`

**Enable/disable:**
- `pause <task>` / `disable <task>` / `turn off <task>` → `enabled: false`
- `resume <task>` / `enable <task>` / `turn on <task>` → `enabled: true`

**Bulk changes:**
- `everything daily` — all enabled tasks → `* * *` day fields
- `everything weekdays only` — all → `1-5`
- `pause everything` / `disable all` — all → `enabled: false`
- `back to defaults` / `reset` — clear `workspace.schedule_config` entirely

**Task name matching:**

Accept fuzzy matches: `inbox` / `Inbox` / `the inbox` → `cr-inbox`. `pulse` / `dont forget` / `don't forget` → `cr-dont-forget`. `commitments` / `commits` → `cr-commitments`. `upcoming` / `upcoming meetings` / `meetings prep` → `cr-upcoming-meetings`. `past meetings` / `past` / `meetings processed` → `cr-past-meetings`.

If a task name doesn't match any known scheduled task, surface plain English: `"I don't recognize '<name>' as one of your scheduled chats. The 5 are: Upcoming Meetings, Inbox, Commitments, Pulse, Past Meetings. Reply with the chat name + change you want."`

### Step 4 — Validate the new cron expression

For each change, build the new cron expression and validate via `parse_cron()`:

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

Proceed? (y / no / cancel)
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
from atomic_write import atomic_write_json, atomic_append_jsonl
from next_seq import next_seq
from schedule_config import cron_to_english
import datetime

entities_path = f'$WORKSPACE/_hq/data/entities.json'
with open(entities_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
ws = data.setdefault('workspace', {})
sc = ws.setdefault('schedule_config', {})
# Apply changes (stub — actual values come from skill parsing)
changes = <list of (task_id, cron, enabled) tuples from Step 3>
for task_id, cron, enabled in changes:
    spec = sc.setdefault(task_id, {})
    if cron is not None:
        spec['cron'] = cron
        spec['label'] = cron_to_english(cron)
    if enabled is not None:
        spec['enabled'] = enabled
data['version'] = data.get('version', 1) + 1
data['last_writer'] = 'change-schedule'
data['last_updated'] = datetime.datetime.utcnow().isoformat() + 'Z'
atomic_write_json(entities_path, data)

# Log a schedule_config_changed event for audit trail
events_path = f'$WORKSPACE/_hq/data/events.jsonl'
event = {
    'seq': next_seq(events_path),
    'ts': datetime.datetime.utcnow().isoformat() + 'Z',
    'type': 'schedule_config_changed',
    'source_skill': 'change-schedule',
    'data': {'changes': [{'task_id': t, 'cron': c, 'enabled': e} for t, c, e in changes]},
}
# Canonical append — atomic write + Drive-sync safety + view regen.
# NEVER open(events_path, 'a') directly (see WORKSPACE_API.md §3).
atomic_append_jsonl(events_path, [event])
print('CONFIG_WRITTEN')
"
```

### Step 7 — Re-register tasks via enable-command-room-schedules

Invoke `enable-command-room-schedules` silently. It reads the updated config and refreshes any registered tasks via the v2.11.4+ self-refresh path. Disabled tasks get their `enabled` flag updated; new cron values get propagated to Cowork's scheduled-tasks DB.

### Step 8 — Confirm to user + STOP

One-line confirmation per change. No CHANGELOG narration, no internal mechanics.

```
✓ Inbox now fires at 8 AM weekdays.
✓ Pulse resumed.
✓ Past Meetings now fires at 4:30 PM weekdays.

Cowork will use the new schedule starting tomorrow morning.
```

Stop. No widget, no follow-up suggestion, no "want to change anything else?"

## Edge cases

**No workspace set up yet.** Surface plain English: `"You haven't run onboarding yet — let's get the workspace set up first. Say `set up command room` to begin."` Stop.

**Re-registration fails after a successful config write.** The new schedule was saved but the live tasks didn't refresh. Surface: `"Saved your new schedule, but the live tasks didn't refresh just yet. Run `set up command room schedules` to retry."` Don't roll back the write — partial success is recoverable.

**User asks for an invalid cron value (e.g., `set inbox to 25am`).** Surface the parse error in plain English. Don't write. Ask again.

**User wants to add a NEW task that doesn't exist in DEFAULT_SCHEDULES.** Reject — this skill only customizes the canonical 5 (or 6, post-Workspace Map). Adding new taskIds is a plugin-level concern.

**User says `everything daily` while one task is paused.** Apply the cron change but leave the paused task paused. They'd say `resume everything` separately to reactivate.

## Forbidden behaviors

- **Do NOT write directly to entities.json without atomic_write_json.** Drive-sync corruption risk.
- **Do NOT bypass `enable-command-room-schedules` for re-registration.** That skill has the v2.11.4 self-refresh logic; reinventing it here drifts.
- **Do NOT prompt for changes if the user asked for read-only mode.** `show my schedule` is read-only — render and stop.
- **Do NOT introduce new taskIds via this skill.** The canonical 5 are defined in `schedule_config.py` `DEFAULT_SCHEDULES`. New tasks ship via plugin updates, not user customization.
- **Do NOT narrate event-type names or file paths in chat.** Per CONTRACT.md Rule 4 — no `schedule_config_changed event written` or `entities.json updated`. Just plain-English confirmation of what changed.
