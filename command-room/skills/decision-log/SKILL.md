---
name: decision-log
description: "Turn every decision the CEO makes — in meetings, Slack threads, or ad-hoc thinking — into a searchable log with who decided, when, why, and what changed. Use when the CEO says 'log decision', 'we decided', 'we decided to', 'what did we decide', 'what did we decide about', 'what did we decide about pricing', 'decision history', 'why did we choose', 'why did we pick'. Writes `decision` events to events.jsonl and regenerates _hq/views/DECISION_LOG.md. DOES NOT fire on 'decision memo on', 'decision memo for', 'tradeoff analysis', 'choose between' — those go to decision-memo-composer (v3.8.0+ — structured multi-option tradeoff). DOES NOT fire on 'revisit decision', 'revisit the hiring decision', 'what decisions should I revisit', 'decisions to revisit', 'decision audit' — those go to decision-revisit (v3.8.0+ — backward-looking re-examination of past decisions). DOES NOT fire on 'what should I decide' (that's advisory, not logging)."
---

## Skill Boundary (v2.1)

- **Use decision-log for:** capturing decisions that were MADE — the historical record. Also for searching the record.
- **Use `memo-writer` for:** producing a comparative or strategic memo BEFORE a decision is made (decision docs / scope docs / strategy memos).
- **Invoked automatically by:** meeting-notes (extracts decisions from transcripts), follow-up-ritual (captures decisions from meetings). This skill also runs standalone when the CEO wants to log an ad-hoc decision.

## Writer Contract

- **Writes:** `decision` events to `_hq/data/events.jsonl` (append-only) with v2.2 shape (`primary_thread_id` + optional `related_thread_ids[]` + `org_ids[]`). **v3.13.0+ MANDATORY: stamp `data.project_id` on every new decision event** by inferring from the active project context (the most-recently-loaded project per session, or the project matching `primary_thread_id`). Without `project_id`, the decision can't be surfaced when the CEO opens that specific project — pre-v3.13.0, ~95% of decisions in M's substrate carried no project_id, breaking project-scoped recall.
- **Regenerates:** `_hq/views/DECISION_LOG.md` view after every write via `shared/scripts/render_decision_log.py` (v3.13.0+ — script created in this release). Also regenerated when a `decision_resolved`, `decision_superseded`, `decision_reaffirmed`, or `decision_revisit_scheduled` event is appended — those overlays update the status badge ([SUPERSEDED] / [REAFFIRMED] / [SNOOZED]) on the referenced decision in the view. Pre-v3.13.0 the SKILL prose claimed auto-regeneration but no script existed; the view fell ~57 decisions stale.

**Renderer invocation pattern (after appending any decision-lifecycle event):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
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
**Status:** Active / Superseded by [newer decision ID]  
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
- **MASTER_TRACKER** — Cross-references project status with active decisions
- **BUSINESS_CONTEXT** — Provides context for decision rationale
- **Session Notes** — Pulls decisions from ongoing work sessions

## Gotchas

- **Superseding Old Decisions:** When a new decision overrides an older one, mark the old entry as "Superseded by [newer decision]" rather than deleting it. This preserves decision history and context.
- **Ambiguous Decisions:** If "we decided" but it's unclear who actually made the call, log it as "Team consensus" or "Inferred from [source]"
- **Timing:** Log decisions as close to when they're made as possible. End-of-day batch logging loses context.
- **Rationale Matters:** Just logging "Decision: X" without rationale is useless. Future you (or your successor) will need to know why this decision made sense at the time.
- **Impact Is Key:** A decision without identifying impact is incomplete. What actually changes? Who needs to adjust their work?
- **Interdependencies:** Some decisions depend on others. Call this out in the "Impact" or "Rationale" section.

## What It Doesn't Do

- It doesn't enforce decisions or track compliance
- It doesn't automatically reverse or update decisions (you explicitly do that)
- It doesn't integrate with project management tools directly (but references them)
- It doesn't create decision-making frameworks (use the workshop mode for that)

## Workflow Integration

**After Each Meeting:**
1. meeting-notes skill processes the transcript
2. Decisions are automatically logged to DECISION_LOG.md
3. You can review them with "decisions from the [Project] meeting"

**Weekly Reviews:**
1. Run "recent decisions" to see what was decided this week
2. Check if any decisions need immediate action
3. Update status if any have been superseded

**Quarterly Planning:**
1. Pull "all decisions for [Project]" to see the arc of decisions
2. Identify patterns or conflicting decisions
3. Use history to inform next quarter's priorities

## Next Steps

- Use **MASTER_TRACKER** to connect decisions to project execution
- Use **call-prep** to reference relevant decisions in upcoming meetings
- Use **people-crm** to track decisions that impact relationships
- Use **cleanup** (`--summary` mode) to surface key decisions in weekly/monthly recaps
