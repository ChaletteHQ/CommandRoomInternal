---
name: usage-report
surfaces: both
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
cd "$PLUGIN_ROOT" && WORKSPACE="$WORKSPACE" python3 -c "..."
```

Inside the `python3 -c` body (cwd is `$PLUGIN_ROOT`, so `shared/scripts` imports resolve — but the events file lives in the WORKSPACE, not under the plugin root; resolve it from the exported `WORKSPACE` env var, never as a relative path):

```python
import sys, os, json, datetime
sys.path.insert(0, 'shared/scripts')
from receipts import count_runs, iter_receipts
from telemetry import aggregate_pack_run_telemetry
from closure_index import pointer_coverage, pointer_coverage_line
from events_io import load_events_org_scoped

ws = os.environ['WORKSPACE']
since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)  # the parsed window

# 1. Run counts — EVERY scheduled task, zero-filled (a task with no receipts
#    shows "ran 0x"; it never silently vanishes from the table).
runs = count_runs(ws, since=since)

# 2. Cost columns — telemetry aggregation over the window's pack_run receipts
#    (silent tasks have no telemetry; their rows show run counts only).
window_receipts = [r['raw'] for r in iter_receipts(ws, since=since)]
agg = aggregate_pack_run_telemetry(window_receipts)

# 3. Pointer coverage (PROV1) — how many of the window's closes point back to
#    the thing that closed them. Read through the shared closer fold, never a
#    hand-rolled scan; the LINE is rendered by the helper so this report and
#    the operator report say the same sentence.
events, _skipped = load_events_org_scoped(ws)
coverage = pointer_coverage(events, since=since.isoformat().replace('+00:00', 'Z'))

print(json.dumps({'runs': runs, 'agg': agg, 'coverage': coverage,
                  'coverage_line': pointer_coverage_line(coverage)}, default=str))
```

If the workspace has no receipts at all: surface plain English `(Nothing to report yet — let a few of your scheduled chats run first, then check back.)` Don't guess.

**Never claim "run counts are solid" beyond what the reader returned** — the counts are the reader's output verbatim; the report renders them, it does not re-derive or adjust them.

**A fire is counted under the SURFACE it served, not the task id it wears (SPEC SURFCOUNT1) — and both columns of a row agree because both readers apply the same rule.** Some fires deliberately run under a legacy task id: the day-close registers as Past Meetings and stamps `data.surface: "end-of-day"`, because re-pointing the registration would have moved every customer's evening chat. `count_runs` buckets those fires under End of Day, and `aggregate_pack_run_telemetry` puts their word/lookup/second columns on the same row. Before this, a workspace whose day-close fired four times that week read "End of Day ran 0x" the morning after it fired, with the spend parked on Past Meetings — and the honest reaction to that is to distrust either the schedule or this report. **Render one row per key the readers return, named through `schedule_config.task_display_name`** — never a row list typed here, and never fold two keys into one row to "tidy up" a workspace mid-rename: a machine that has fired under both ids genuinely has two histories, and saying so is the honest answer.

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
  End of Day        ran 5x   avg 18.3k words  avg 35 lookups   avg 24 seconds   ← uses the most
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

Traceable closes: 23 of 25 (92%) point back to the email, meeting, or message that closed them. 6 of those point to the surface and moment you closed them, with no message or meeting behind it. 2 closed with nothing to point back to.
```

Numbers shown rounded for readability. Plain English.

**The traceable-closes line is `pointer_coverage_line(coverage)` VERBATIM** — one line, rendered by the helper, never re-worded and never re-derived from the raw counts here. Two surfaces phrasing the same measurement differently is how a customer ends up with two answers to one question. Omit the line only when the helper returns an empty string.

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
