---
name: usage-report
description: "Shows a plain-English breakdown of where your scheduled-task usage goes — which threads cost the most to run, which connectors get called the most, how long each fire takes. Triggers: `usage report`, `command room usage`, `where does the spend go`, `show me task costs`, `token usage`, `how expensive are my scheduled tasks`. Read-only — surfaces the picture, doesn't change anything."
---

# usage-report

Plain-English read-only report of where Command Room scheduled-task usage actually concentrates. Use BEFORE deciding what to optimize, per shared/CONTRACT.md Rule 17 (speed over perfection — but measure first).

## What this is

v2.14.0+ orchestrators write `data.telemetry` fields to every `pack_run` event in `_hq/data/events.jsonl`. The schema (per `shared/scripts/telemetry.py` `build_pack_run_telemetry`):

- `prompt_tokens_est` — char-count / 4 estimate of the orchestrator prompt at fire time
- `response_tokens_est` — char-count / 4 estimate of the chat response (widget HTML + Briefs/Sources sections)
- `connector_call_count` — total tool calls during the fire
- `connector_calls_by_connector` — breakdown (gmail, calendar, granola, drive, zapier)
- `connector_calls_by_op` — per-operation breakdown (gmail.search_threads, calendar.list_events, etc.)
- `duration_ms` — total fire duration

Token estimates are rough (chars/4 heuristic). Connector calls are the OTHER big cost driver — each MCP tool call has overhead.

## Behavior

### Step 1 — Determine the time window

Default: last 7 days. User can ask `usage report last 14 days` / `usage report last 30 days` / `usage report this week` / `usage report today` and the skill parses the window.

### Step 2 — Read pack_run events

Wrap the Python invocation in the canonical CONTRACT.md Rule 22 discovery preamble:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "..."
```

Inside the `python3 -c` body (cwd is `$PLUGIN_ROOT`):

```python
import sys
sys.path.insert(0, 'shared/scripts')
from telemetry import aggregate_pack_run_telemetry
import json

with open('_hq/data/events.jsonl', 'r', encoding='utf-8') as f:
    events = [
        json.loads(line) for line in f
        if line.strip()
    ]

# Filter to pack_run events in the window
window_events = [
    ev for ev in events
    if ev.get('type') == 'pack_run' and in_window(ev.get('ts'), window)
]

agg = aggregate_pack_run_telemetry(window_events)
```

If `events.jsonl` doesn't exist or has no telemetry-bearing pack_run events: surface plain English `(Nothing to report yet — let a few scheduled tasks run first, then check back.)` Don't guess.

### Step 3 — Surface a plain-English breakdown

```
Command Room usage — last 7 days

By scheduled thread:
  Inbox             ran 5x   avg 6.2k words   avg 12 lookups   avg 8 seconds
  Commitments       ran 5x   avg 9.8k words   avg 22 lookups   avg 14 seconds
  Pulse             ran 5x   avg 4.1k words   avg 8 lookups    avg 6 seconds
  Past Meetings     ran 5x   avg 18.3k words  avg 35 lookups   avg 24 seconds   ← uses the most
  Upcoming Meetings ran 5x   avg 11.2k words  avg 18 lookups   avg 12 seconds

Where the lookups go:
  Gmail thread searches:     35
  Gmail full reads:          18
  Calendar:                  12
  Meeting transcripts:       10

Total this week: 25 runs, ~245k words processed, ~205 lookups, about 5 1/2 minutes of run time.
```

Numbers shown rounded for readability. Plain English.

### Step 4 — Surface optimization candidates

Based on the data, suggest 1-3 things worth looking at:

- If one scheduled thread is using more than 2x the average → flag it
- If a single kind of lookup dominates → flag it
- If a thread takes a long time but processes little text → the lookups are slow, not the thinking

Suggestions surface as plain English ("Past Meetings is using about three times what the others use — that's the per-meeting transcript pulls plus writing each brief. A few ways to trim it: ...").

NEVER make changes. Read-only.

## Trigger pattern

`usage report` / `command room usage` / `where does the spend go` / `show me task costs` / `token usage` / `how expensive are my scheduled tasks`

Optional time window suffix: `last 7 days`, `last 14 days`, `last 30 days`, `this week`, `today`. Default = last 7 days.

## What this skill does NOT do

- Does NOT modify events.jsonl
- Does NOT update orchestrator prompts
- Does NOT change scheduled-task cron settings
- Does NOT call any external APIs

Pure read-only observability. Optimization is a separate decision that follows the data this surfaces.

## See also

- `shared/scripts/telemetry.py` — the helper orchestrators use to build telemetry blocks
- `shared/CONTRACT.md` Rule 9 — telemetry writes silently to events, never narrated to chat (that rule is for scheduled-task chat output; this skill is the explicit on-demand reporting path that DOES surface telemetry — different context, no Rule 9 violation)
