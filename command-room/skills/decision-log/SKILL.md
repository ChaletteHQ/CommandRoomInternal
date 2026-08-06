---
name: decision-log
surfaces: both
description: "Turn every decision the CEO makes — in meetings, Slack threads, or ad-hoc thinking — into a searchable log with who decided, when, why, and what changed. Fires on: 'we decided [X]' / 'we decided to go with [X]' / 'let's go with option [X]' (logging), 'log decision', 'what did we decide' / 'what did we decide about [topic]' (retrieval), 'decision history', 'why did we choose [X]', 'show me the decision log', plus 'tune decision-log'. Writes decision events and regenerates the decision-log view; tags extraction misses when a manual log follows a processed meeting. Does NOT fire on 'decision memo on [topic]' (decision-memo-composer — multi-option tradeoff), 'revisit the [topic] decision' (decision-revisit), or 'what should I decide' (advisory, not logging). Event shapes and retrieval grammar: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use decision-log for:** capturing decisions that were MADE — the historical record. Also for searching the record.
- **Use `decision-memo-composer` for:** the structured multi-option tradeoff BEFORE a decision is made ("decision memo on", "choose between", A-vs-B analysis) — v3.8.0+ split.
- **Use `decision-revisit` for:** backward-looking re-examination of a PAST decision ("revisit the hiring decision", "what decisions should I revisit") — v3.8.0+ split.
- **Use `memo-writer` for:** single-decision narrative capture as a shareable document (decision doc), plus scope docs / strategy memos.
- **Invoked automatically by:** meeting-notes (extracts decisions from transcripts), follow-up-ritual (captures decisions from meetings). This skill also runs standalone when the CEO wants to log an ad-hoc decision.

## Writer Contract

- **Writes:** `decision` events to `_hq/data/events.jsonl` (append-only) with v2.2 shape (`primary_thread_id` + optional `related_thread_ids[]` + `org_ids[]`). **v3.13.0+ MANDATORY: stamp `data.project_id` on every new decision event** by inferring from the active project context (the most-recently-loaded project per session, or the project matching `primary_thread_id`). Without `project_id`, the decision can't be surfaced when the CEO opens that specific project — pre-v3.13.0, ~95% of decisions in M's substrate carried no project_id, breaking project-scoped recall.
- **Regenerates:** `_hq/views/DECISION_LOG.md` view after every write via `shared/scripts/render_decision_log.py` (v3.13.0+ — script created in this release). Also regenerated when a `decision_resolved`, `decision_superseded`, `decision_reaffirmed`, or `decision_revisit_scheduled` event is appended — those overlays update the status badge ([SUPERSEDED] / [REAFFIRMED] / [SNOOZED]) on the referenced decision in the view. Pre-v3.13.0 the SKILL prose claimed auto-regeneration but no script existed; the view fell ~57 decisions stale.

**Event append recipe (MANDATORY — SPEC GATE1 / A1).** The `decision` event MUST be written through the locked writer `atomic_append_jsonl`, NOT a hand-rolled `next_seq`+`open('a')` append or a raw `>>`. The helper reserves the seq and writes inside the cross-process writer lock (`_hq/data/.writer.lock`), so a concurrent append can't lose your decision or duplicate a seq. Omit `seq`/`ts` — the helper auto-stamps both atomically. This recipe is the A1 lock contract; see `shared/WORKSPACE_API.md` → Append Protocol §3.

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT"
python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from atomic_write import atomic_append_jsonl
event = {
    'type': 'decision',
    'source_skill': 'decision-log',
    'primary_thread_id': '<project_NNN or null>',
    'person_ids': ['<canonical decider id>'],
    'data': {
        'project_id': '<project_NNN — MANDATORY per v3.13.0+>',
        'decision': '<what was decided>',
        'rationale': '<why — alternatives considered>',
        'made_by': '<who made the call>',
        'impact': '<what changes>',
    },
}
atomic_append_jsonl('$WORKSPACE/_hq/data/events.jsonl', [event], holder='decision-log')
print('decision event appended via locked writer')
"
```

**Extraction-miss tag (Phase 6 Loop 5).** When the CEO logs a decision by hand shortly after a meeting was processed, that's a signal meeting-notes missed the extraction. Before appending, check for a recent processed meeting sharing an attendee: `from extraction_hints import find_recent_meeting` → if `find_recent_meeting(event, <recent meeting_processed/meeting events>)` returns a ref, add `data.extraction_miss = True` and `data.source_meeting_ref = <ref["meeting_id"]>` to the event. The decision still logs normally; the tag is additive telemetry that insight-generator's Loop 5 pass clusters into extraction hints (never surfaced to the CEO). Best-effort — on any error, append the decision untagged.

After the append, regenerate the view:

**Renderer invocation pattern (after appending any decision-lifecycle event):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT"
python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from render_decision_log import regenerate
result = regenerate('$WORKSPACE')
print(f'regenerated {result[\"total\"]} decisions ({result[\"active\"]} active)')
"
```

Per `shared/CONTRACT.md` Rule 4: the regeneration is silent — the user sees the updated view at `_hq/views/DECISION_LOG.md`, not a "regenerated X decisions" narration in chat.
- **Does not write to:** `entities.json` (decisions are events, not entities), `aliases.json`, `classifier_feedback.jsonl`.
- **Read-only consumer of:** `entities.json` (for person/project/org lookup), `events.jsonl` (for search/retrieval).
- **Conflict boundary:** primary writer for `decision` events. If meeting-notes or follow-up-ritual extract a decision, they emit the event via decision-log's write protocol (not directly) so the regeneration hook fires consistently.

**Closure events (v3.4.5+ — read-only here).** Two new event types close decisions in the log:
- `decision_resolved` — the decision was executed / acted on. Written by the decision-CRU layer (`shared/scripts/decision_match.py`) when a meeting transcript shows completion language matching an open decision (HIGH-confidence auto-write only).
- `decision_superseded` — a newer decision overrides this one. Same auto-write path, triggered by reversal language in a transcript.

Closed decisions stay in the log — they're marked with a status badge (✓ Resolved or ⚠ Superseded) but never deleted. View regeneration must filter active vs closed when rendering. Per `shared/CONTRACT.md` Rule 24, the CRU layer is silent: closure events never appear in chat. The DECISION_LOG view is where users discover them.

---

# Decision Log

**For:** CEOs making 50+ decisions a day who need to remember what was decided, why, and what changed.

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)** — the decision is logged first, then one-tap changes are
offered. Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "auto_log": "auto",                # auto (log decisions from meetings silently) | confirm_first
    "revisit_reminders": "strategic",  # strategic (remind on strategic decisions) | off
}
cfg = get_config(workspace_root, "decision-log", DEFAULTS)
```

`auto_log=auto` (default) logs decisions extracted from meetings/threads without a confirm step;
`confirm_first` surfaces a one-tap confirm before writing the `decision` event. `revisit_reminders`
controls whether `decision_revisit_scheduled` reminders are set on strategic decisions.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "log decision", "we decided…" | log the decision with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "decision-log", DEFAULTS)` BEFORE confirming, then append the first-run footer. |
| **Show settings** | "show decision-log settings" | render current config in plain English; no logging. |
| **Tune** | "tune decision-log" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → confirm. |
| **Reset** | "reset decision-log to defaults" | `wipe_skill_config(workspace_root, "decision-log")` → next fire is a first-fire again. |

**The first-run block (footer — decision-log confirms in chat, no widget):**

> *First time logging a decision for you. I set 2 defaults: **I log decisions from meetings
> automatically** · **I'll remind you to revisit the strategic ones**. Say "tune my decision log"
> to change either, or just tell me ("ask me before logging" / "no revisit reminders").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "ask me before logging decisions" / "confirm first" | `auto_log = confirm_first` |
| "just log decisions automatically" | `auto_log = auto` |
| "no revisit reminders" / "stop reminding me to revisit" | `revisit_reminders = off` |
| "remind me to revisit strategic decisions" | `revisit_reminders = strategic` |

After applying: `save_skill_config(..., is_reconfigure=True)` + confirm in one line.

## What It Does

Maintains a searchable, comprehensive record of every decision made across your entire business. Decisions are captured in real-time and organized by project, date, and status so you can instantly find "what did we decide about X?" without digging through emails or meeting notes.

The log lives at `[WORKSPACE_ROOT]/_hq/views/DECISION_LOG.md` (v3.13.0+ canonical path — consolidated from the pre-v3.13.0 dual paths at `_hq/DECISION_LOG.md` AND `_hq/views/DECISION_LOG.md`). The view is regenerated by `shared/scripts/render_decision_log.py` after every decision-event write. The view is automatically updated when:
- You explicitly log a decision during a session
- The meeting-notes skill processes a meeting and extracts decisions
- You ask the skill to search or retrieve decision history

## How to Use

**Log a Decision (Explicit)**

```
"Log decision: we're going with the [option] because [reason]"
"Decision: pivot to [new direction]"
"Log: approved the [thing] with these constraints [constraints]"
```

**Search & Retrieve**

```
"What did we decide about [topic/project]?"
"Decision history for [Project Name]"
"Recent decisions"
"Who decided [decision]?"
"When did we decide about [X]?"
"What was the rationale for deciding on [option]?"
"Show me all decisions on [project] in [timeframe]"
```

**Decision Tracking**

```
"Is the [decision] still active or superseded?"
"Has anything changed since we decided [X]?"
"Which decisions are currently active?"
"Decisions that need review"
```

## Decision Entry Format

Each decision in the log follows this structure:

```markdown
### [Date] — [Decision Title]
**Project:** [project name]  
**Decision:** [what was decided]  
**Rationale:** [why — context, alternatives considered]  
**Made by:** [who made the call]  
**Impact:** [what changes as a result]  
**Status:** Active / Superseded by [newer decision + date]  
**Tags:** [relevant categories or outcomes]  
```

### Example

```markdown
### 2026-04-08 — Transition to Product-Led Go-To-Market
**Project:** Go-To-Market Strategy  
**Decision:** Shift from enterprise sales to freemium product-led model  
**Rationale:** Market data shows 70% of new users self-serve; enterprise sales cycle is 6+ months. Competitors gaining share. Alternatives: stay with current model (risk), hybrid approach (too complex for now).  
**Made by:** CEO  
**Impact:** Sales team realigns to expansion; Product prioritizes onboarding UX; Marketing shifts to product demos  
**Status:** Active  
**Tags:** GTM, Product Strategy, Go-To-Market
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "Status: Superseded by decision_0042"
- Good: "Status: Superseded by the freemium pricing decision (Apr 12)"

## Key Queries

**What Happened Recently**

```
"Recent decisions" — last 2 weeks of all decisions
"Recent decisions on [project]" — last 2 weeks for a specific project
"Decisions made in [month/date range]"
```

**Project-Level View**

```
"All decisions for [Project Name]"
"Active decisions on [project]"
"Decision timeline for [project]"
"What have we decided about [project scope]?"
```

**Decision Intelligence**

```
"Which decisions are superseded?"
"Are we still following through on [decision]?"
"Decisions that depend on [other decision]"
"What alternatives did we rule out for [decision]?"
```

## Triggers

- "log decision"
- "decision:"
- "we decided"
- "log this decision"
- "what did we decide about"
- "decision history"
- "recent decisions"
- "decision log"
- "are we still doing"
- "did we decide to"

## Connected Tools

- **meeting-notes** — Automatically extracts and logs decisions from processed meetings
- **entities.json + events.jsonl** — the substrate this skill cross-references project status against (MASTER_TRACKER is a regenerated Tier-2 view — never read it as the source of truth or write to it)
- **BUSINESS_CONTEXT** — Provides context for decision rationale
- **Session Notes** — Pulls decisions from ongoing work sessions

## Gotchas

- **Superseding Old Decisions:** When a new decision overrides an older one, append a `decision_superseded` event referencing the old decision rather than deleting anything — the regenerated view then badges the old entry "Superseded by [newer decision]". Never hand-edit the view. This preserves decision history and context.
- **Ambiguous Decisions:** If "we decided" but it's unclear who actually made the call, log it as "Team consensus" or "Inferred from [source]"
- **Timing:** Log decisions as close to when they're made as possible. End-of-day batch logging loses context.
- **Rationale Matters:** Just logging "Decision: X" without rationale is useless. Future you (or your successor) will need to know why this decision made sense at the time.
- **Impact Is Key:** A decision without identifying impact is incomplete. What actually changes? Who needs to adjust their work?
- **Interdependencies:** Some decisions depend on others. Call this out in the "Impact" or "Rationale" section.

## What It Doesn't Do

- It doesn't enforce decisions or track compliance
- It doesn't automatically reverse or update decisions (you explicitly do that)
- It doesn't integrate with project management tools directly (but references them)
- It doesn't create decision-making frameworks — for a structured A-vs-B tradeoff say `decision memo on [topic]` (decision-memo-composer); for failure-mode mapping say `stress test [plan]` (stress-test)

## Workflow Integration

**After Each Meeting:**
1. meeting-notes skill processes the transcript
2. Decisions are automatically logged as `decision` events in events.jsonl (the source of truth); the `_hq/views/DECISION_LOG.md` view regenerates from them
3. You can review them with "decisions from the [Project] meeting"

**Weekly Reviews:**
1. Run "recent decisions" to see what was decided this week
2. Check if any decisions need immediate action
3. If any have been superseded, log the superseding decision — the status badge updates in the regenerated view

**Quarterly Planning:**
1. Pull "all decisions for [Project]" to see the arc of decisions
2. Identify patterns or conflicting decisions
3. Use history to inform next quarter's priorities

## Next Steps

- Connect decisions to project execution through the substrate — `decision` events carry `primary_thread_id`, so project-scoped recall ("all decisions for [Project]") reads events.jsonl directly
- Use **call-prep** to reference relevant decisions in upcoming meetings
- Use **people-crm** to track decisions that impact relationships
- Use **weekly-recap** / **operator-report** to see the period's decisions surfaced in recaps

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Turn every decision the CEO makes — in meetings, Slack threads, or ad-hoc thinking — into a searchable log with who decided, when, why, and what changed. Use when the CEO says 'log decision', 'we decided', 'we decided to', 'we're going with', 'let's go with', 'we'll go with', 'what did we decide', 'what did we decide about', 'what did we decide about pricing', 'decision history', 'show me the decision log', 'show decision log', 'show the decision log', 'why did we choose', 'why did we pick'. Writes `decision` events to events.jsonl and regenerates _hq/views/DECISION_LOG.md. Also handles first-run personalization settings — use when the CEO says 'tune my decision log', 'tune decision-log', 'show decision-log settings', 'reset decision-log to defaults'. DOES NOT fire on 'decision memo on', 'decision memo for', 'tradeoff analysis', 'choose between' — those go to decision-memo-composer (v3.8.0+ — structured multi-option tradeoff). DOES NOT fire on 'revisit decision', 'revisit the hiring decision', 'what decisions should I revisit', 'decisions to revisit', 'decision audit' — those go to decision-revisit (v3.8.0+ — backward-looking re-examination of past decisions). DOES NOT fire on 'what should I decide' (that's advisory, not logging).
