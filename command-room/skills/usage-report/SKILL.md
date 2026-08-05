---
name: usage-report
description: "Shows a plain-English breakdown of where your scheduled-task usage goes — which threads cost the most to run, which connectors get called the most, how long each fire takes. Triggers: `usage report`, `command room usage`, `where does the spend go`, `show me task costs`, `token usage`, `how expensive are my scheduled tasks`. Read-only — surfaces the picture, doesn't change anything. DOES NOT fire on 'where am I wasting time' / 'what can be automated' (automation-scanner — time-cost of manual work, not task-run usage). DOES NOT fire on money-spend questions ('what did we spend this month' — QuickBooks / the financial tools)."
---

# usage-report

Plain-English read-only report of where Command Room scheduled-task usage actually concentrates. Use BEFORE deciding what to optimize, per shared/CONTRACT.md Rule 17 (speed over perfection — but measure first).

## What this is

usage-report is **read-only over `_hq/data/events.jsonl`** — it appends nothing. It READS the `data.telemetry` fields that v2.14.0+ orchestrators write to every `pack_run` event. The schema (per `shared/scripts/telemetry.py` `build_pack_run_telemetry`):

- `prompt_tokens_est` — char-count / 4 estimate of the orchestrator prompt at fire time
- `response_tokens_est` — char-count / 4 estimate of the chat response (widget HTML + Briefs/Sources sections)
- `connector_call_count` — total tool calls during the fire
- `connector_calls_by_connector` — breakdown (gmail, calendar, granola, drive, zapier)
- `connector_calls_by_op` — per-operation breakdown (e.g. `mail.search`, `calendar.list`, `transcript.fetch` — provider-neutral operation labels, not raw provider tool names)
- `duration_ms` — total fire duration

Token estimates are rough (chars/4 heuristic). Connector calls are the OTHER big cost driver — each MCP tool call has overhead.

## Behavior

### Step 1 — Determine the time window

Default: last 7 days. User can ask `usage report last 14 days` / `usage report last 30 days` / `usage report this week` / `usage report today` and the skill parses the window.

### Step 2 — Read receipts through the shared reader

**Run counts come from the receipt contract's shared reader (`shared/scripts/receipts.py` `count_runs`) — NEVER from a hand-rolled pack_run scan.** The v4.5.1 dogfood (FINDINGS F-49) proved the hand-rolled path undercounts: it missed legacy id spellings (`cr-commitments`, `past_meetings`) and two whole task families (reconcile-sent's `sent_reconcile` receipts, session-sweep's `session_sweep_run` receipts) because it read only `pack_run` events. The shared reader parses every receipt shape ever written, forever.

Wrap the Python invocation in the canonical CONTRACT.md Rule 22 discovery preamble:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && WORKSPACE="$WORKSPACE" python3 -c "..."
```

Inside the `python3 -c` body (cwd is `$PLUGIN_ROOT`, so `shared/scripts` imports resolve — but the events file lives in the WORKSPACE, not under the plugin root; resolve it from the exported `WORKSPACE` env var, never as a relative path):

```python
import sys, os, json, datetime
sys.path.insert(0, 'shared/scripts')
from receipts import count_runs, iter_receipts
from telemetry import aggregate_pack_run_telemetry

ws = os.environ['WORKSPACE']
since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)  # the parsed window

# 1. Run counts — EVERY scheduled task, zero-filled (a task with no receipts
#    shows "ran 0x"; it never silently vanishes from the table).
runs = count_runs(ws, since=since)

# 2. Cost columns — telemetry aggregation over the window's pack_run receipts
#    (silent tasks have no telemetry; their rows show run counts only).
window_receipts = [r['raw'] for r in iter_receipts(ws, since=since)]
agg = aggregate_pack_run_telemetry(window_receipts)

print(json.dumps({'runs': runs, 'agg': agg}, default=str))
```

If the workspace has no receipts at all: surface plain English `(Nothing to report yet — let a few of your scheduled chats run first, then check back.)` Don't guess.

**Never claim "run counts are solid" beyond what the reader returned** — the counts are the reader's output verbatim; the report renders them, it does not re-derive or adjust them.

### Step 3 — Surface a plain-English breakdown

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary. Customers meet these as **scheduled chats** — never "scheduled threads" or "tasks" in the rendered report.
- BAD: "By scheduled thread:"
- GOOD: "By scheduled chat:"

```
Command Room usage — last 7 days

By scheduled chat:
  Inbox             ran 5x   avg 6.2k words   avg 12 lookups   avg 8 seconds
  Commitments       ran 5x   avg 9.8k words   avg 22 lookups   avg 14 seconds
  Staff Meeting     ran 5x   avg 4.1k words   avg 8 lookups    avg 6 seconds
  Past Meetings     ran 5x   avg 18.3k words  avg 35 lookups   avg 24 seconds   ← uses the most
  Upcoming Meetings ran 5x   avg 11.2k words  avg 18 lookups   avg 12 seconds

Background tasks (silent — no chat output, so no word counts):
  Sent-mail check   ran 6x
  Nightly sweep     ran 4x
  Weekly cleanup    ran 1x

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

- If one scheduled chat is using more than 2x the average → flag it
- If a single kind of lookup dominates → flag it
- If a chat takes a long time but processes little text → the lookups are slow, not the thinking

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
- Orchestrators write telemetry silently into `pack_run` events (see `telemetry.py`'s docstring) — never narrated into scheduled-chat output. This skill is the explicit on-demand reporting path that DOES surface it; different context, no conflict.
