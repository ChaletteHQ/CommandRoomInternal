---
name: system-health
description: "On-demand scheduled-task health check — the reliability watchdog's interactive surface. Fires on: 'health check', 'is everything running', 'system health', 'are my tasks running', 'did my tasks run', 'why didn't my [task] run', 'run a health check on the workspace'. Reads each task's run receipts plus the live scheduler records, compares against expected cadence in machine-local time, and answers in plain English: what's running, what stopped, what was never authorized, what fired but wrote nothing — each with the one action that fixes it. Read-only: diagnoses and names the fix, never registers or edits. Does NOT fire on 'weekly cleanup' / 'maintenance' (cleanup — runs the same watchdog weekly as its deep pass), 'set up command room schedules' (registration), 'change my schedule' (change-schedule), or 'usage report' (usage-report). Check list and verdict format: Routing section in the body."
---

# system-health

The on-demand face of the scheduled-task watchdog (`shared/scripts/task_watchdog.py`). The CEO asks "is everything running?" and gets a straight answer grounded in artifacts — substrate receipts and scheduler records — never in what a past fire narrated (the Bug #98 lesson, generalized). The same watchdog rides the morning brief (light daily pass) and cleanup's Monday note (weekly deep pass); this skill exists so the customer never has to wait for either.

## Skill Boundary (v2.1)

- **Owns:** the on-demand health diagnosis of Command Room's scheduled tasks + workspace binding.
- **Does not own:** fixing anything. Registration/repair is `set up command room schedules`; cadence changes are `change-schedule`; weekly maintenance is `cleanup`; usage/cost telemetry is `usage-report`.

## Writer Contract

Read-only. This skill writes NOTHING — no events, no config, no files. (The watchdog module it calls is likewise read-only.)

## Step 1 — Gather the two evidence sources

1. `mcp__scheduled-tasks__list_scheduled_tasks` — the live scheduler records (taskId, lastRunAt, enabled, prompt).
2. The workspace substrate — resolve `[WORKSPACE_ROOT]` via the canonical CONTRACT.md Rule 22 discovery preamble.

## Step 2 — Run the watchdog

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import task_watchdog as tw
ws = '<workspace_root>'
records = <the list_scheduled_tasks result, as a Python list of task dicts>
reports = tw.check_tasks(ws, task_records=records)
binding = tw.check_workspace_binding(ws)
installed = json.loads(open('.claude-plugin/plugin.json', encoding='utf-8').read()).get('version', '')
drift = tw.check_prompt_versions(records, installed)
print(json.dumps({
    'lines': tw.plain_english_lines(reports, binding=binding),
    'reports': reports,
    'stale_prompts': [f for f in drift if f.get('stale')],
}))
"
```

## Step 3 — Render the answer

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "(3) a connector needs to be reconnected"
- GOOD: "(3) access to one of your tools — email, calendar, Slack — needs to be re-approved"

**Everything healthy (empty `lines`, no stale prompts):** one line and stop —

> *"Everything's running. All [N] of your scheduled chats and background tasks ran on their normal schedule, most recently [most recent display name] at [local time]."*

**Something's wrong:** the healthy one-liner is replaced by the finding lines, verbatim, one per problem (they're already plain English with the fix named — never re-narrate with taskIds, cron strings, or event names). Then, when the status is `late`, add the stall-diagnosis line so the CEO can self-serve the cause (merged from SPEC-3.2 — grounded in the 2026-06 support calls: a lid-closed stall read as "Claude broke", a session lost to an org usage pool):

> *"Most common reasons a task stops running, in order: (1) the computer was asleep or the lid closed when it was due — it runs at the next chance; (2) your Claude usage limit was reached — it resets on its own, check the usage meter; (3) access to one of your tools — email, calendar, Slack — needs to be re-approved: open the chat once and approve any prompt; (4) after a plugin update, Cowork sometimes needs a full quit-and-reopen."*

If `stale_prompts` is non-empty, add ONE line total: *"Your scheduled chats were set up under an older version — say 'update command room' once and they'll refresh themselves."*

Stop after the render. No widget, no doc, no follow-up question.

## What this unlocks later (W5 — gated, nothing here registers them)

The Tue/Thu `waiting-on chase` shipped Phase 4 (2026-07-02) as the Commitments orchestrator's Phase 3.8 — its gates (the kinds split + counterparty receipts) merged, and it rides the existing commitments task, so nothing new registered. Two opt-in surfaces remain gated until this watchdog has been live long enough to verify fires: the day-1/week-1 lifecycle one-shots (cut in v4.1.0 because nothing could verify they fired) and an optional "system health" sidebar card. Each ships as its own later addition through the normal add paths — this skill only diagnoses.

## Gotchas

- **Machine-local time is the scheduler's clock.** Cron evaluates in the machine's timezone, not the workspace timezone — the watchdog's math is machine-local by design. Don't "correct" a fire time against the workspace TZ; `tz.py` localization is presentation-only.
- **A later-add task rendering as "not registered" is not a failure** — relationship-moves / commitments / pulse are deliberately not first-install. The watchdog stays quiet about them; change-schedule owns that render.
- **Don't fabricate a diagnosis.** If the watchdog returns a finding you can't explain, surface the finding and the named fix — never speculate about causes beyond the stall-diagnosis list.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> On-demand scheduled-task health check — the reliability watchdog's interactive surface (Phase 3 / W1, 2026-07). Reads each scheduled task's substrate receipts + the live scheduler records, compares against expected cadence in machine-local time, and answers in plain English: what's running, what stopped, what was never authorized, and the one action that fixes each. Triggers: 'is everything running', 'system health', 'health check', 'are my tasks running', 'did my tasks run', 'why didn't my [task] run'. ('health check' used to redirect to cleanup — it moved here in Phase 3; cleanup still runs the same watchdog weekly as its deep pass.) Read-only: this skill never registers, re-registers, or edits anything — it diagnoses and names the fix. DOES NOT fire on 'weekly cleanup' / 'tidy up' / 'maintenance' (cleanup), 'set up command room schedules' (registration), 'change my schedule' / 'show my schedule' (change-schedule), or 'usage report' (usage-report — cost/volume telemetry, not health).
