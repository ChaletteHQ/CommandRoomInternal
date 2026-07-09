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
verdict = tw.health_verdict(ws, task_records=records)
installed = json.loads(open('.claude-plugin/plugin.json', encoding='utf-8').read()).get('version', '')
drift = tw.check_prompt_versions(records, installed)
print(json.dumps({
    'vantage': verdict['vantage'],
    'summary_line': verdict['summary_line'],
    'lines': verdict['lines'],
    'info_lines': verdict['info_lines'],
    'reports': verdict['reports'],
    'stale_prompts': [f for f in drift if f.get('stale')],
}))
"
```

`health_verdict` (v4.5.2 R3) is the ONE verdict source: it runs the F-40
vantage guard, partitions every task into exactly one bucket (problem /
caught-up late / waiting on first run / on schedule), and computes
`summary_line` from that partition in code. Never recompute, restate, or
"round up" its counts in prose — the counts ARE the truth rules.

## Step 3 — Render the answer

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "(3) a connector needs to be reconnected"
- GOOD: "(3) access to one of your tools — email, calendar, Slack — needs to be re-approved"

**Vantage blocked (`vantage` non-null — F-40):** render `summary_line` and STOP. This chat cannot see the machine-local scheduler (cloud/remote session, or a different computer); the line already says so and points to a local chat. NEVER report tasks as unregistered, NEVER name 'set up command room schedules' from here — running it in this chat would register everything into the wrong place and create duplicates.

**Everything healthy (empty `lines` + empty `info_lines`, no stale prompts):** render `summary_line` verbatim and stop — it is the "Everything's running. All [N]…" one-liner, with the count computed from tasks that actually have on-schedule receipts.

**Anything else:** render `summary_line`, then every `info_lines` entry, then every `lines` entry — all verbatim, one per line (they're already plain English with dates and the fix named — never re-narrate with taskIds, cron strings, or event names).

**Truth rules (R3 — binding on this render, F-43/F-40/F-10):**
- A task only "ran on its normal schedule" if it has a run receipt on time — `summary_line` already encodes this; never widen its claim.
- A task named in ANY line (catch-up, first-run, problem) is already excluded from the on-schedule count — never re-add it or summarize the report as "everything's fine except…" with different numbers.
- Off-schedule catch-ups are reported with their dates (the info line carries them) — a task that caught up late is not "on schedule."
- Never state a CAUSE for a missing or late run. What is known is what the lines say; when a gap is unexplained, say what's known and stop.

When any `late` problem line renders, you may add the generic self-serve list below — it is framed as common possibilities, NOT a diagnosis of this task (grounded in the 2026-06 support calls; the watchdog cannot know which applies, and says so):

> *"I can't tell from here which applies, but the most common reasons a task stops running: (1) the computer was asleep or the lid closed when it was due — it runs at the next chance; (2) your Claude usage limit was reached — it resets on its own, check the usage meter; (3) access to one of your tools — email, calendar, Slack — needs to be re-approved: open the chat once and approve any prompt; (4) after a plugin update, Cowork sometimes needs a full quit-and-reopen."*

If `stale_prompts` is non-empty, add ONE line total: *"Your scheduled chats were set up under an older version — say 'update command room' once and they'll refresh themselves."*

Stop after the render. No widget, no doc, no follow-up question.

## What this unlocks later (W5 — gated, nothing here registers them)

The Tue/Thu `waiting-on chase` shipped Phase 4 (2026-07-02) as the Commitments orchestrator's Phase 3.8 — its gates (the kinds split + counterparty receipts) merged, and it rides the existing commitments task, so nothing new registered. Two opt-in surfaces remain gated until this watchdog has been live long enough to verify fires: the day-1/week-1 lifecycle one-shots (cut in v4.1.0 because nothing could verify they fired) and an optional "system health" sidebar card. Each ships as its own later addition through the normal add paths — this skill only diagnoses.

## Gotchas

- **Machine-local time is the scheduler's clock.** Cron evaluates in the machine's timezone, not the workspace timezone — the watchdog's math is machine-local by design. Don't "correct" a fire time against the workspace TZ; `tz.py` localization is presentation-only.
- **A later-add task rendering as "not registered" is not a failure** — relationship-moves / commitments / pulse are deliberately not first-install. The watchdog stays quiet about them; change-schedule owns that render.
- **Don't fabricate a diagnosis.** If the watchdog returns a finding you can't explain, surface the finding and the named fix — never speculate about causes beyond the generic self-serve list (which is possibilities, not a diagnosis). "The computer was likely asleep" as an asserted cause is the exact fabricated-narrative class the 2026-07 dogfood catalogued (F-10/F-43/F-47).
- **An empty scheduler list is a vantage question before it is a finding.** `health_verdict` checks the substrate's registration history first (F-40); trust its `vantage` verdict. A cloud/remote chat reading an empty machine-local registry and reporting "nothing is registered" is the false total-outage failure this skill exists to never repeat.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> On-demand scheduled-task health check — the reliability watchdog's interactive surface (Phase 3 / W1, 2026-07). Reads each scheduled task's substrate receipts + the live scheduler records, compares against expected cadence in machine-local time, and answers in plain English: what's running, what stopped, what was never authorized, and the one action that fixes each. Triggers: 'is everything running', 'system health', 'health check', 'are my tasks running', 'did my tasks run', 'why didn't my [task] run'. ('health check' used to redirect to cleanup — it moved here in Phase 3; cleanup still runs the same watchdog weekly as its deep pass.) Read-only: this skill never registers, re-registers, or edits anything — it diagnoses and names the fix. DOES NOT fire on 'weekly cleanup' / 'tidy up' / 'maintenance' (cleanup), 'set up command room schedules' (registration), 'change my schedule' / 'show my schedule' (change-schedule), or 'usage report' (usage-report — cost/volume telemetry, not health).
