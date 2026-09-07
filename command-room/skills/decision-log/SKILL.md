---
name: decision-log
surfaces: both
description: "Turn every decision the CEO makes — in meetings, Slack threads, or ad-hoc thinking — into a searchable log with who decided, when, why, and what changed. Fires on: 'we decided [X]' / 'we decided to go with [X]' / 'let's go with option [X]' (logging), 'log decision', 'what did we decide' / 'what did we decide about [topic]' (retrieval), 'decision history', 'why did we choose [X]', 'show me the decision log', plus 'tune decision-log'. Revisit mode (formerly decision-revisit): 'revisit the [topic] decision', 'what decisions should I revisit', 'decisions to revisit', 'decision audit', 'is the [topic] decision still right'. Writes decision events and regenerates the decision-log view; tags extraction misses when a manual log follows a processed meeting. Does NOT fire on 'decision memo on [topic]' (decision-memo-composer — multi-option tradeoff) or 'what should I decide' (advisory, not logging)."
---

## Skill Boundary (v2.1)

- **Use decision-log for:** capturing decisions that were MADE — the historical record. Also for searching the record.
- **Use `decision-memo-composer` for:** the structured multi-option tradeoff BEFORE a decision is made ("decision memo on", "choose between", A-vs-B analysis) — v3.8.0+ split.
- **Revisit mode** (`## Mode: revisit`, formerly `decision-revisit` — folded in SKILLMERGE1 2026-09-03): backward-looking re-examination of a PAST decision ("revisit the hiring decision", "what decisions should I revisit").
- **Use `memo-writer` for:** single-decision narrative capture as a shareable document (decision doc), plus scope docs / strategy memos.
- **Invoked automatically by:** meeting-notes (extracts decisions from transcripts), follow-up-ritual (captures decisions from meetings). This skill also runs standalone when the CEO wants to log an ad-hoc decision.

## Writer Contract

- **Writes:** `decision` events to `_hq/data/events.jsonl` (append-only) with v2.2 shape (`primary_thread_id` + optional `related_thread_ids[]` + `org_ids[]`). **v3.13.0+ MANDATORY: stamp `data.project_id` on every new decision event** by inferring from the active project context (the most-recently-loaded project per session, or the project matching `primary_thread_id`). Without `project_id`, the decision can't be surfaced when the CEO opens that specific project — pre-v3.13.0, ~95% of decisions in M's substrate carried no project_id, breaking project-scoped recall.
- **Regenerates:** `_hq/views/DECISION_LOG.md` view after every write via `shared/scripts/render_decision_log.py` (v3.13.0+ — script created in this release). Also regenerated when a `decision_resolved`, `decision_superseded`, `decision_reaffirmed`, or `decision_revisit_scheduled` event is appended — those overlays update the status badge ([SUPERSEDED] / [RESOLVED] / [REAFFIRMED] / [SNOOZED]) on the referenced decision in the view. Pre-v3.13.0 the SKILL prose claimed auto-regeneration but no script existed; the view fell ~57 decisions stale.

**Event append recipe (MANDATORY — SPEC GATE1 / A1).** The `decision` event MUST be written through the locked writer `atomic_append_jsonl`, NOT a hand-rolled `next_seq`+`open('a')` append or a raw `>>`. The helper reserves the seq and writes inside the cross-process writer lock (`_hq/data/.writer.lock`), so a concurrent append can't lose your decision or duplicate a seq. Omit `seq`/`ts` — the helper auto-stamps both atomically. This recipe is the A1 lock contract; see `shared/WORKSPACE_API.md` → Append Protocol §3.

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ '{print NF, $0}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||')
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
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ '{print NF, $0}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||')
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

Closed decisions stay in the log — they're marked with a status badge ([RESOLVED] for a ruling that was carried out, [SUPERSEDED] for one a later ruling replaced) and moved to their own section, but never deleted. The renderer's five buckets are `active` / `reaffirmed` / `snoozed` / `resolved` / `superseded`; the two closed ones are `render_decision_log.CLOSED_DECISION_STATUSES` — read that set rather than spelling the statuses out, so a later bucket cannot slip past a filter. Per `shared/CONTRACT.md` Rule 24, the CRU layer is silent: closure events never appear in chat. The DECISION_LOG view is where users discover them.

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
**Status:** Active / Reaffirmed / Snoozed / Resolved / Superseded by [newer decision + date]  
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

## Mode: revisit — "what decisions should I revisit" (formerly decision-revisit)

The retrieval-and-re-examination mode of the same log. Logging asks *what did we decide*; this mode asks *is it still right*. It was its own skill (`decision-revisit`) until SKILLMERGE1 (2026-09-03); every phrase it answered routes here, its four widget verbs (`revisit` / `still valid` / `replace` / `snooze 30d`) and their apply-choices dispatch entry (`decision-revisit`, the persisted widget src) are unchanged, and the events it writes keep `source_skill: decision-revisit`. Fires on: 'revisit the [topic] decision', 'what decisions should I revisit', 'decisions to revisit', 'decision audit', 'is the [topic] decision still right', 'which decisions are stale', 'review my decisions'.

### Mode boundary

- **Use the revisit mode for:** proactive surfacing of decisions due for review. Reads the decision log, scores revisit-worthiness, surfaces top candidates with per-item action set.
- **Use the logging path (above) for:** capturing a NEW decision, and for retrieval of a specific known decision ("what did we decide about pricing").
- **Use `decision-memo-composer` for:** writing a NEW decision memo (forward-looking tradeoff analysis).

### Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `decision_reaffirmed` when the user confirms a decision is still valid. Carries `{decision_event_seq, reaffirmation_reason, reviewed_at, snooze_until}`.
- `_hq/data/events.jsonl` — event type `decision_superseded` when the user supersedes a decision (chained invocation to `decision-log` to write the NEW decision; `supersedes_seq` field links the two). This event type already exists in the schema (added in v3.4.5).
- `_hq/data/events.jsonl` — event type `decision_revisit_scheduled` when the user snoozes a decision. Carries `{decision_event_seq, snooze_until_ts}`. (Snooze surfaces in the next scan as eligible for re-surface after `snooze_until_ts`.)

**Append through the locked writer (SPEC GATE1 / A1):** the `decision_reaffirmed` and `decision_revisit_scheduled` events MUST be written via `atomic_append_jsonl(events_path, [event], holder="decision-revisit")` (the holder / `source_skill` value stays `decision-revisit` — persisted vocabulary is canonical, see `source_skill_compat.FOLDED_SKILL_ALIASES`) (omit `seq`/`ts` — auto-stamped inside the lock), NOT a hand-rolled append. The `decision_superseded` path chains to `decision-log`, which writes its `decision` event through the same locked recipe. See `shared/WORKSPACE_API.md` → Append Protocol §3.

**Reads from:**
- `_hq/data/events.jsonl` — every `type == "decision"` event (the input universe).
- `_hq/data/events.jsonl` — every event with `data.references_decision_seq == <decision_seq>` OR matching topic / project-id / affected-entity heuristics (the "contradictory signal" pass).
- `_hq/data/entities.json` — for the named-condition check. If a decision's `data.rationale` mentioned "we don't have ops capacity" and entities.json now shows a person with `role: "Head of Ops"` added after the decision date, that's a signal worth surfacing.
- `_hq/views/DECISION_LOG.md` is NOT the source — that's a regenerated view. Read directly from events.jsonl per shared/CONTRACT.md Rule 24 (substrate is the source).

**Conflict boundary:** sole writer of `decision_reaffirmed` and `decision_revisit_scheduled` events. `decision_superseded` writes go through `decision-log` (chained invocation, not direct write).

---

### What the revisit mode does

Decisions are append-only forever; pre-v3.8.0 the substrate had no way to surface "this decision was made 94 days ago, and three events since suggest the rationale no longer holds." This mode closes that loop.

### What It Does

Scans every decision event in events.jsonl. For each `status == "active"` decision, computes a revisit-worthiness score from:

1. **Time elapsed** — decisions older than 60 days enter the candidate pool; >120 days weight higher.
2. **Contradictory signal** — count of subsequent events that reference the decision's topic, affected entities, or project but represent state changes inconsistent with the decision (e.g., a decision "stay agency-only" followed by a `person_added` event with `role: "Head of Ops"`).
3. **Named-condition shift** — if the decision's `data.rationale` contains phrases like "we don't have X" / "until Y exists" / "while Z is the bottleneck", check whether those conditions still hold via entities.json + recent events. Each shift adds to the score.
4. **Stakeholder change** — if a decision's stakeholders (from `person_ids`) have changed roles or left the workspace, score up.

Rank decisions by score. Surface top 5 in a widget with per-decision context ("why revisit?") and the action set.

### How to Use

```
"what decisions should I revisit"
"decision audit"
"decisions to revisit"
"old decisions worth re-examining"
"which decisions are stale"
```

Runs on-demand. Can also be scheduled through `change-schedule` (operator-led setup).

### How It Works

#### Phase 1 — Load candidate decisions

Load the candidate set through the canonical open-decision fold — `from decision_match import load_open_decisions; open_decisions = load_open_decisions(workspace_root / "_hq" / "data" / "events.jsonl")` — NEVER a freelance `status == "active"` filter over raw events. The loader is the one place that honors every accepted supersede shape (SUPERSEQ1: `decision_superseded` by id, by any data-scope seq spelling, or by the top-level `supersedes_seq` field, PLUS the restamp shape — a newer `decision` event carrying `supersedes_seq`); a hand-rolled status filter re-surfaces rulings the ledger already replaced. Then drop from the candidate set anything reaffirmed in the last 30 days or currently snoozed (`decision_reaffirmed` / `decision_revisit_scheduled` overlays referencing the decision by id or seq).

**Named-decision branch ("revisit the hiring decision" / "revisit the pricing decision"):** when the trigger names a specific decision, resolve it against the candidate set (topic/entity fuzzy match; if two candidates tie, ask which). Skip Phases 2–3 entirely — no scoring, no ranking — and render Phase 4's widget with just that ONE decision card (same "why revisit?" snippet, same four actions). Only the bare scan-all triggers ("what decisions should I revisit", "decision audit") run the full rank.

#### Phase 2 — Score each candidate

For each candidate:
- `time_score` = clamp(`days_since_decision / 60`, 0, 3)
- `contradictory_signal_score` = count of events in [decision_ts, now) that reference the decision's topic/entities/project but represent state changes inconsistent with the rationale
- `named_condition_score` = +1 per phrase in rationale that maps to a named condition that has demonstrably shifted (entities.json now contains an entity that contradicts it, OR events.jsonl contains evidence of the condition no longer holding)
- `stakeholder_score` = +0.5 per stakeholder who has changed role / left workspace since decision date
- `total = time + contradictory + named_condition + stakeholder`

**Named-condition extraction (ADV1 — how to actually compute `named_condition_score`):** scan the decision's rationale text for the patterns **"until X", "while X", "as long as X", "we don't have X", "once X", "if X changes"**. Each match is a named condition. **"Demonstrably shifted"** means entities.json or events.jsonl now contradicts it — the role got hired (a person with that title now exists), the budget constraint lifted (a funding/revenue event landed), the dependency moved (that project's status changed). **Quote the original condition AND the contradicting evidence in the widget — never score a shift you can't cite.** A condition you can't point to a contradiction for scores 0, not a guess.

**Coefficients are tunable constants, not arbitrary** (so a future session doesn't treat them as magic): `time_score` clamps at **60 days** (the floor where a decision is old enough to re-pressure-test without nagging) up to a max of 3; `stakeholder_score` is **0.5/stakeholder** (a personnel change is a real but secondary signal — half the weight of a directly-contradicted named condition); `named_condition` and `contradictory_signal` are **1.0 each** (the strongest signals — the world directly disagreeing with the rationale). Adjust these if real usage shows the ranking is too eager or too sleepy.

#### Phase 3 — Rank + surface

Take top 5 by total score. Build a "why revisit?" snippet for each — naming the specific events / entity changes / time gap that drove the score. Render as a widget.

#### Phase 4 — Render widget

Per-item action set (all four are CANONICAL_ACTIONS members — the Phase 4 deliberation-set extension; dispatch lives in apply-choices' `decision-revisit` source entry):
- `revisit` (displays "Revisit now") — opens `decision-memo-composer` pre-filled with the original decision's framing + the contradictory signal pass as starting context.
- `still valid` (displays "Still valid") — appends `decision_reaffirmed` event. Decision exits the candidate pool for 30 days.
- `replace` (displays "Replace it") — chains to `decision-log` to capture the new decision; this skill writes `decision_superseded` linking to the original (the event type keeps its name — only the button label is friendly).
- `snooze 30d` (displays "Snooze (30 days)") — appends `decision_revisit_scheduled` with `snooze_until_ts = now + 30d`. Decision is re-eligible after that.

Widget follows `shared/CHAT_ACTION_WIDGET.md` contract. Validators per `shared/CONTRACT.md` Rules 1-4. **Render it READ-ONLY (SPEC_WIDGETRO1 §2-1, CUT-C item 9 — ATTENDED_TEST_v5.28.0 B5.3):** the one call is `widget_transport.render_and_persist(data_view=<the view>, wrapper="fragment", persist_dir=<WORKSPACE>/_hq/.system/widgets, page=1, read_only=True)` — the rows carry exactly the four verbs above and a pick dispatches on its own; the page carries NO batch footer (no Apply all, no Reset, no `Snooze rest (1 day)` — the footer's 1-day mute contradicted the row's own 30-day snooze and is not a verb of this surface). The transport's validator reds a revisit page that carries one. Relay `transport["html"]` byte-exact.

### Output Structure (widget)

```
5 decisions worth a second look

1. Acme Co as agency vs in-house     [decided 2026-02-14, about 3 months ago]
   Original reasoning: "we don't have ops capacity"
   What's changed: you hired Mira Sample on April 8 as Head of Ops, and
   recent activity points to internal capacity now beating the agency.

   [Revisit now]  [Still valid]  [Replace it]  [Snooze (30 days)]

2. Quarterly pricing reviews          [decided 2026-01-03, about 4 months ago]
   Original reasoning: "monthly is too noisy"
   What's changed: two customers cancelled recently citing "pricing
   surprise" in their exit notes.

   [Revisit now]  [Still valid]  [Replace it]  [Snooze (30 days)]

[Show 3 more]
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary (event types like `decision_superseded` stay internal; the buttons say "Replace it" / "Still valid").
- Bad: "[Supersede] — writes decision_superseded referencing seq 412"
- Good: "[Replace it] — I'll log the new decision and link it to the old one"

### DOES NOT

- Mutate prior decision events. Per references/DATA_CONTRACT.md: events are append-only. Revisit produces NEW events (`decision_reaffirmed`, `decision_superseded`, `decision_revisit_scheduled`); the original `decision` event is never rewritten.
- Auto-resolve. Every action is user-initiated via the widget.
- Surface decisions still inside their snooze window (`snooze_until_ts > now`).
- Read DECISION_LOG.md as truth — it's a regenerated view. Source is events.jsonl.

## Narration leak scan (CUT-C item 8 — MANDATORY on every composed line)

Widget bodies are scanned inside `widget_transport.render_and_persist`; the PROSE this skill composes around them is not, unless this step runs. Before posting any sentence you composed — an ack, a header, a summary, a pointer, a "why" line — run `validate_chat_output(<the text>)` from `chat_output_renderer.py` (`shared/scripts/`). It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_` / `bp_` / `pcand:` wire id), an event or field name, a file name or path, or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(<WORKSPACE>))` is the one substitution). NEVER catch the error and post anyway. Text relayed byte-exact from a driver or the transport is already scanned and is not re-composed.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Turn every decision the CEO makes — in meetings, Slack threads, or ad-hoc thinking — into a searchable log with who decided, when, why, and what changed. Use when the CEO says 'log decision', 'we decided', 'we decided to', 'we're going with', 'let's go with', 'we'll go with', 'what did we decide', 'what did we decide about', 'what did we decide about pricing', 'decision history', 'show me the decision log', 'show decision log', 'show the decision log', 'why did we choose', 'why did we pick'. Writes `decision` events to events.jsonl and regenerates _hq/views/DECISION_LOG.md. Also handles first-run personalization settings — use when the CEO says 'tune my decision log', 'tune decision-log', 'show decision-log settings', 'reset decision-log to defaults'. DOES NOT fire on 'decision memo on', 'decision memo for', 'tradeoff analysis', 'choose between' — those go to decision-memo-composer (v3.8.0+ — structured multi-option tradeoff). Revisit mode (formerly decision-revisit, folded in SKILLMERGE1 2026-09-03) — surface decisions worth re-examining based on elapsed time, contradictory new signal, and whether the original rationale's named conditions still hold: 'what decisions should I revisit', 'decision audit', 'decisions to revisit', 'decision revisit', 'decision review', 'old decisions worth revisiting', 'which decisions are stale', 'review my decisions', 'decisions worth re-examining', 'revisit the hiring decision', 'revisit the pricing decision', 'revisit the' (any single-decision revisit-by-name), 'is the [topic] decision still right'. DOES NOT fire on 'what should I decide' (that's advisory, not logging).
