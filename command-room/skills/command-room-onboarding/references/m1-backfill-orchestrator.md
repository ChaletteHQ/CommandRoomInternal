# cr-m1-backfill — Tier C+E deep-read async orchestrator

OUTPUT CONTRACT (v2.13.0+ — MANDATORY): every Command Room scheduled-task fire begins by loading the renderer + leak scanner per `shared/CONTRACT.md`. This orchestrator emits one structured chat post (no widget — see Phase 5) and writes events to `_hq/data/events.jsonl`. All other contract rules apply: no real customer names (Rule 26), no path leaks (Rule 25), no internal mechanism narration (Rule 4 voice half).

**Status:** NEW (M1 redesign, 2026-05-23). Registered by `command-room-onboarding` Phase 1b as a one-shot `recurrence: "once"` scheduled task. Customer clicks Run Now to authorize MCP permissions. Fires once, ~5–7 minutes, then auto-disables.

**Model tier:** Haiku. Reason: high-volume pattern matching at scale. Tier C+E is mechanical extraction over assembled content — Sonnet/Opus overkill for the matching work. The synthesis pass that consumes this output runs on Opus in the customer's home chat (`command-room-coach`).

**Wraps:** no existing skill — this is greenfield M1-specific extraction. The customer-facing recap rendered in Phase 5 is what Chat 5 displays when the customer comes back to it in Phase 2b of the M1 timeline.

---

## What this task does

Reads the customer's last 7 days at full content depth (Tier C from the M1 backfill architecture), extracts structured events from that content (Tier E), backdates them into `events.jsonl` with original timestamps, and emits a customer-readable last-week summary to the chat.

This is the ONE-SHOT deep read that happens behind the scenes during M1. The customer doesn't sit and watch it. They authorize via Run Now in Phase 1b, return to their home chat (Chat 4), and when the operator tells them the deep read is done, they type `show me what's next` in Chat 4 (which loads this orchestrator's output via cached context) and read the structured recap here in parallel.

After this fires once, the task auto-disables. It is NOT a recurring scheduled task.

---

## Hard caps and protections

| Cap | Value | Rationale |
|---|---|---|
| **Max input tokens** | 80,000 | Tier C ceiling per M1 spec. If 7-day window content exceeds, sample down (see Phase 2.D). |
| **Max chat output tokens** | 4,000 | Recap fits one Cowork chat post; longer surfaces are out-of-character for the M1 "structured recap worth reading in parallel" beat. |
| **Window** | Last 7 days from `fireAt` | Hard-coded. Customer-driven deeper history goes through `backfill [N] months on [project]`, not this task. |
| **Per-transcript cap** | 8,000 tokens per transcript | Long transcripts get summary-extracted, never full-read. |
| **Wall-clock budget** | ~5–7 minutes | Customer is doing other reading in parallel; longer breaks the M1 choreography. |

If any cap blows, sample down and surface a one-line note in the recap (`Sampled 12 of 14 meetings — full set covered, deepest 12 extracted`). Never silently truncate without surfacing.

---

## Phase 0 — Setup

Resolve the plugin path and workspace per `shared/CONTRACT.md` Rule 22:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
```

Read the renderer + leak-scanner pre-flight; if either import fails, ABORT with a plain-English error (this is a one-shot, so failing here means the customer never sees the recap — surface clearly).

Load:
- `$WORKSPACE/_hq/data/entities.json` — for person/org/project IDs (canonicalization)
- `$WORKSPACE/_hq/data/aliases.json` — for raw-to-canonical mapping
- The `workspace.brain_name` field from entities.json — for chat copy ("Penelope read your last 7 days…"; default "Penelope" if unset)
- The customer's last-event timestamp in events.jsonl — defines the END of the 7-day window (now), so the window is `[now - 7d, now]` in workspace timezone

Append a `m1_backfill_started` event to events.jsonl (OMIT `seq`/`ts` — the append gate auto-stamps both inside the writer lock, `ts` in UTC; a hand-typed "now" was the F-15 naive-local-clock bug class, v4.5.2 R4.)

```jsonl
{"type":"m1_backfill_started","data":{"window_start":"<ISO>","window_end":"<ISO>"},"source_skill":"m1-backfill"}
```

---

## Phase 1 — Tier C: deep read

For each connector in scope, pull full-content material for the 7-day window. Run connectors in series, not parallel — parallel inside one chat slows the chat per CONTRACT Rule for distributed work.

### 1.A — Email (Gmail / Outlook)

- All inbound emails received in window, full body
- All outbound emails sent in window, full body
- Use `discover_mail_search_tool()` then `discover_mail_thread_fetch_tool()` for thread expansion

Cap: 60K input tokens across all email content. If exceeded, sample by (a) inbound from top-15 most-frequent senders, (b) all outbound, (c) sample remainder.

### 1.B — Meetings (transcripts)

- All meeting transcripts in window via `discover_transcript_tool()` (Granola / Fireflies / Otter / Read.ai / Zoom / Teams)
- Per-transcript cap 8K tokens — if longer, fetch summary instead of full text

### 1.C — Documents (Drive / OneDrive)

- Top 5–10 documents modified in window, full body
- Selection by recent-modification + matches-existing-project-keywords (cheap heuristic)
- Cap: 8K tokens across all docs

### 1.D — Slack / Teams DMs

- DMs to/from the user in window, full content
- Group channels: messages the user sent only (other-people's messages stay metadata-only per privacy rule)
- Cap: 6K tokens

### 1.E — Cap check

Sum input tokens used so far. If approaching 80K, stop adding sources. Surface in recap: `Sampled the deepest content from your week. Full-fidelity coverage starts after M1 as Penelope sees more.`

---

## Phase 2 — Tier E: extraction

Walk the assembled content from Phase 1. Extract structured events.

### 2.A — Commitments

Scan for first-person and second-person commitment language:

| Pattern | Direction | Confidence |
|---|---|---|
| "I'll [verb] by [date]", "I'll get back to you", "I'll send", "let me follow up" | from user → other | high |
| "I'll have it [by/on] [date]" | from user → other | high |
| "Can you [verb] by [date]?", "Could you send me [thing]?" | from other → user (request) | medium |
| "I'll [verb] [thing] for you" | from other → user | high |
| "by [date]", "before [date]" anchored to an action verb | direction from context | medium |

For each detection:
- Resolve speaker → canonical person_id via aliases.json + entities.json
- Resolve target (the other party) → canonical person_id
- Backdate the event with the original source timestamp (email send time, meeting time, doc modified time)
- Append a `commitment` event to events.jsonl

### 2.B — Decisions

Scan for decision language:

| Pattern | Confidence |
|---|---|
| "we decided", "we agreed", "going with [X]", "final call" | high |
| "the call is", "decided to [verb]" | high |
| "let's go with [option]" | medium |

For each: extract the decision text, attribute to the conversation participants, link to the most-relevant `primary_thread_id`, append a `decision` event with original timestamp.

### 2.C — Follow-ups

For each commitment from 2.A: scan for matching follow-through (sent email referencing the commitment, calendar event on the promised date, document modification). If matched, write `commitment_resolved`. If unmatched AND older than 5 days from window end, mark as overdue in the Phase 5 recap.

### 2.D — Sampling note

If Phase 1 sampled down due to the 80K cap, note it in extraction: extraction operates only on what was sampled. The recap surfaces this honestly.

---

## Phase 3 — Aggregate metrics

Compute for the recap (skip any that can't be computed — never fabricate):

- `meetings_in_window` — count of distinct meetings
- `distinct_people_in_meetings` — unique people attended with
- `emails_full_read` — count of full bodies parsed
- `documents_modified` — count
- `commitments_user_to_others` — outbound commitments
- `commitments_others_to_user` — inbound commitments
- `decisions_logged`
- `follow_ups_sent` / `follow_ups_pending`
- `most_active_projects` — top 5 by event count, with counts
- `most_engaged_people` — top 5 by interaction count (inbound + outbound)
- `silence_anomalies` — high-stakes people (those with prior 60d cadence < 7d) with zero touches in window
- `decisions_named` — top 3 decisions with date + counterparty
- `open_commitments_older_than_5_days` — list with target person + age

---

## Phase 4 — Write events

Atomic-append all events from Phase 2 to `_hq/data/events.jsonl` per `shared/WORKSPACE_API.md`. Batch all appends — don't fire one at a time.

Each event carries `source_skill: "m1-backfill"` (bare since v2.14.27; the registered taskId stays `cr-m1-backfill`, and any pre-rename history at `source_skill: "cr-m1-backfill"` remains valid append-only) and original-source timestamp (not `fireAt`).

After append, regenerate views (`MASTER_TRACKER.md`, `DECISION_LOG.md`, `PEOPLE.md`) per `references/VIEW_GENERATION.md`.

---

## Phase 5 — Emit recap to chat

This is the one chat post the customer reads when they come back to Chat 5 in Phase 2b of the M1 timeline.

**Voice rules (CONTRACT Rule 4 voice half):**
- Friendly assistant register — match the customer's `BRAND_VOICE.md` if available
- No internal mechanism names (no "Phase 2", no "Tier C", no "extraction pass")
- No file/path mentions (no `events.jsonl`, no `_hq/`)
- Specific names + dates + counts
- Skip any section that has nothing real to surface — empty sections > stubbed sections

**Render shape:**

```
Last 7 days, recapped from the deep read.

**Activity volume:**
• <N> meetings processed (across <M> distinct people)
• <N> emails read (full body — top inbound + sent)
• <N> documents you modified

**Captured this week:**
• <N> commitments — <X> from you to others, <Y> from others to you
• <N> decisions logged
• <N> follow-ups sent / not-sent flagged

**Most-active workstreams (by event count):**
• <Project A> — <N> events
• <Project B> — <N> events
• <Project C> — <N> events
• Everything else: < 5 events each

**Most-engaged people this week:**
• <Person A>: <X> inbound, <Y> outbound — <note if silence anomaly>
• <Person B>: <N> touches
• <Person C>: <N> touches

**Decisions logged this week:**
• <Decision text> (<date> with <counterparty>)
• <Decision text> (<date> with <counterparty>)

**Open commitments older than 5 days:**
• To <Person>: <commitment text> (overdue since <date>)
• To <Person>: <commitment text> (<N> days)

That's the raw recap. <BrainName> is now using all of this in your home chat — go there and ask her `show me what's next` to see what she notices.
```

**`<BrainName>` substitution:** read `entities.json` `workspace.brain_name`. Default "Penelope" if unset.

**Footer (last line):**
> *This task fired once and is now off. <BrainName>'s daily scheduled tasks pick up from here.*

---

## Phase 6 — Auto-disable

After successful chat post, call `mcp__scheduled-tasks__update_scheduled_task(taskId="cr-m1-backfill", enabled=false)`. This is a one-shot — re-firing wastes tokens and re-extracts the same week.

Append final event (same seq/ts omission rule):

```jsonl
{"type":"m1_backfill_complete","data":{"events_written":<N>,"input_tokens_used":<N>,"sampled":<bool>},"source_skill":"m1-backfill"}
```

---

## Failure modes

| Failure | Handling |
|---|---|
| Renderer / leak scanner import fails at Phase 0 | ABORT, surface plain-English error. Customer's recap will not appear. Operator catches this manually. |
| 80K cap blown by Phase 1.E | Sample down. Surface in recap. Continue. |
| No connectors return content in window | Skip Phase 2 entirely. Emit a short recap: *"<BrainName> didn't find content in your tools for the last 7 days — maybe you were off, maybe the connectors weren't on yet. Daily scheduled tasks will fill the gap from here."* |
| events.jsonl atomic-write fails | Retry once with conflict-resolution per WORKSPACE_API.md. If still fails, skip Phase 4, still emit the recap (memory-only — customer sees the summary, the substrate just won't have backdated events). |
| Auto-disable fails | Surface in build/operator logs but not in customer chat. The task will sit idle (one-shot recurrence won't re-fire on its own). |

---

## What this task is NOT

- Not a recurring scheduled task. One fire, then off.
- Not a 12-month backfill. Window is 7 days. Customers extend per-project via `backfill [N] months on [project]` on demand.
- Not a synthesis surface. The structured recap is data; the Insights + Mirror v2 synthesis happens in Chat 4 (`command-room-coach`) on Opus.
- Not a wow surface — it's a substrate-builder + structured recap. The wow lands in Chat 4 when the user types `show me what's next`.
- Not a tour. No "here's what I did" narration — just the recap.
