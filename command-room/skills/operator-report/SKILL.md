---
name: operator-report
description: "Generates a CEO-facing 'Operating Lift' report — what would have slipped, what got captured, what got delivered without being asked, and a conservative time-absorbed estimate at the bottom. NOT a usage/billing dashboard — a value-of-having-this report shaped like a board update for the CEO's own operations. Triggers: 'operator report', 'operating lift', 'what have you done for me', 'what did you save me', 'show me the value', 'monthly recap', 'what did you do this month', 'show me the impact', 'time saved report', 'my operating report'. Auto-generates on the 1st of every month to `_hq/operator-reports/[YYYY-MM].docx` (per CONTRACT Rule 27, no .md deliverables). The May 15 CEO-group pitch slot uses this format — the qualitative claims (commitments that would have slipped, decisions logged, etc.) lead; the hours estimate is the credibility anchor at the bottom, not the headline."
---

# operator-report

The CEO-facing value report. Surfaces what Command Room actually did for them over a window — written like a board update for their own operating layer, not a usage dashboard.

## What this is NOT

This is not `usage-report`. That skill reports developer-facing telemetry (token spend, connector call counts, duration_ms per orchestrator). Useful for optimization decisions; not for showing a CEO what they're getting.

This is also not a retention sales pitch. The numbers are real. If a section returns 0, it shows 0 — and either pivots to compound-value framing or stays silent on that section. Fabricated value claims would torch trust faster than no report at all.

## Why hours-saved isn't the headline

CEOs at $5B+ companies don't think in hours. They think in:
- **Things that would have slipped** — commitments dropped, relationships cooled, decisions re-debated
- **Things they no longer have to remember** — people, context, project history
- **Output delivered without being asked** — morning briefings, prep briefs, drafts, audits

Hours-saved sits at the bottom as the conservative credibility anchor. The lead is qualitative because the qualitative is what they're actually buying.

## Behavior

### Step 1 — Determine the time window

Defaults: last 30 days (calendar month if invoked on the 1st via scheduled-task).

User can override:
- `operator report this month` → start of current calendar month → now
- `operator report last month` → start of previous calendar month → end of previous calendar month
- `operator report last 7 days` → 7 days back
- `operator report last 90 days` → 90 days back
- `operator report quarter` → start of current calendar quarter → now
- `operator report year` → start of current calendar year → now

### Step 2 — Read the substrate

Read from:
1. `_hq/data/events.jsonl` — full event stream within the window
2. `_hq/data/entities.json` — current counts (people, orgs, projects)
3. `_hq/BRAND_VOICE.md` — sample-size metadata for voice claim
4. `_hq/DECISION_LOG.md` — decision count
5. Scheduled-task fire records (also in events.jsonl as `pack_run` events)

Compute the 5 report sections. **No section is fabricated.** If a count is 0, the report acknowledges it or skips the section per the rules below.

### Step 3 — Compute the 6 sections (v3.13.0+ — added synthesis lead at the top per #1 feedback)

**Section 0: SYNTHESIS LEAD (v3.13.0+ — REQUIRED).** Per M's 2026-05-20 feedback #1, the monthly operator report was previously only operational metrics ("hours saved this month"). That misses the bigger value: WHAT THE MONTH MEANT. The synthesis lead answers in one paragraph: what was the dominant theme of this period, what was the anchor moment that spun off most movement, what shifted in your business between start-of-period and end-of-period. Use the same pattern as Friday Wrap's lead paragraph (which M called out as the best output we'd seen) — one anchor moment + the theme.

Method:
1. Pull the top 5 events by activity-cluster (events that connect to the most other events via shared person_ids / project_ids / org_ids).
2. Identify the anchor moment — the single event whose downstream connections most exceed other events. Often a stage talk, a board meeting, a launch, a major decision logged.
3. Compose one paragraph (~3-5 sentences) that names the anchor moment, the theme it crystallized, and 1-2 of the most consequential downstream events.
4. Render at the TOP of the report (Section 0, before the operational counts).

Example (the kind of output #1 was asking for):

> *"Heavy month on go-to-market and the Acme Co partnership. The May 14 CEO-group stage talk was the anchor — it spun off ~$45K in early pipeline, two demo cycles already booked, and a new vertical wedge that's now an active thread. Acme Co moved from handshake to paperwork. Operating businesses stayed in steady-state; older consulting clients continued to fade."*

If the period has no clear anchor moment (truly mid-cycle period, no events with significant cluster connections), surface that honestly: *"This period was steady-state — no single anchor, just baseline execution across [active orgs]."*

The synthesis lead is the difference between "here are some numbers" and "here's what this month meant." Don't skip it.

**Section 1: What would have slipped**

Pull from events.jsonl within the window:
- Commitments captured outside an existing tracker context (i.e., extracted from a meeting or email, not user-created). Count `type: commitment` events with `source_skill ∈ {meeting-notes, inbox-triage, follow-up-ritual, scan-for-commitments}`.
- Cold relationships flagged. Count `type: pattern_break_detected` events with `source_skill ∈ {dormant-customer-scan, insight-generator, pulse}` (the canonical dormancy-flag event type per events.schema.json — pre-v3.13.6 this spec called for `dormant_flag` which isn't in the enum and would have silently returned 0).
- Decisions logged from real interactions. Count `type: decision` events with `source_skill: meeting-notes` or `decision-log`.
- Aging follow-ups surfaced. Count open `type: commitment` events whose `data.due` is past the window-end date — i.e., commitments that aged into overdue status during the window. (Pre-v3.13.6 this spec called for `type: aging_followup` which isn't in the enum.)

If a category returns 0, omit the bullet entirely (don't say "0 commitments captured" — that reads worse than not mentioning it).

**Section 2: What you no longer have to hold**

Snapshot counts from `entities.json`:
- People tracked with relationship context (count `entities.people` where `last_interaction` is within the last 90 days — i.e., relationship is alive, not just a name).
- Active projects with session history (count `entities.projects` where `session_count` ≥ 1 OR there's at least one event with `primary_thread_id` matching).
- Emails read / triaged (count `interaction` events with `source: gmail` or `source_skill: inbox-triage` in window — the canonical event type per events.schema.json; pre-v3.13.6 this spec called for `email_triaged` which isn't in the enum).
- Meetings processed (count `meeting_processed` events in window).

If `entities.json` is missing any of these fields, fall back to event-stream counts within the window.

**Section 3: What got delivered without you asking**

Pull scheduled-task fire records. `pack_run` events carry `data.task_id` (NOT `orchestrator` — that field name was the pre-v3.13.6 spec, never wired). Task ids per `enable-command-room-schedules/SKILL.md`:
- Morning briefings generated — `pack_run` with `data.task_id: morning-brief`
- Pre-meeting prep briefs — `pack_run` with `data.task_id: upcoming-meetings` AND yielded at least one brief
- Email drafts produced in user's voice (count `email_drafted` events)
- cleanups run — `pack_run` with `data.task_id: cleanup` (cleanup runs as a scheduled task every Sunday, v3.17.0+). Every cleanup run also appends a `cleanup_run` event, so if no `pack_run` record exists (e.g. an on-demand run), count `cleanup_run` events instead.

If scheduled-tasks aren't enabled (no `pack_run` events in window), surface a single-line note instead of the section: *"Your daily briefings and other scheduled work weren't turned on this period — you can flip them on anytime and they'll show up here next time."*

**Section 4: A conservative time estimate**

This is the anchor at the bottom — not the headline. Use a transparent rubric so the number is defensible:

| Activity | Time absorbed per unit (conservative) |
|---|---|
| Commitment captured (would have been re-asked / forgotten) | 8 minutes |
| Meeting processed into structured brief | 12 minutes |
| Morning briefing delivered | 15 minutes |
| Pre-meeting prep brief | 20 minutes |
| Email triaged into Reply Now / Decision / FYI / Discard | 2 minutes |
| Email drafted in voice (assuming user reviews + sends or edits lightly) | 8 minutes |
| Decision logged (avoids re-debate next time topic surfaces) | 10 minutes |
| Aging follow-up surfaced | 5 minutes |
| cleanup | 30 minutes |
| Cold-relationship flag (one nudge prevents quarter of stale silence) | 25 minutes |

Sum across all events in window. Divide by the window-day-count for a per-day figure. Convert to weekly equivalent.

Show:
- Total hours absorbed in window
- Weekly equivalent
- Day-of-the-week equivalent ("equivalent to a 1-day-per-week chief of staff" if weekly ≥ 8h; "equivalent to a half-day-per-week chief of staff" if ≥ 4h; otherwise just show hours).

**Be explicit that this is conservative.** Land the section with: *"Conservative — assumes you would have done each of these tasks yourself at average speed. Real lift is usually higher because half of these would have just dropped."*

**Section 5: Things that get more valuable over time**

- Communication profile sample size from `BRAND_VOICE.md` metadata
- Decision-log entry count (total, not just in-window — this is the compound counter)
- People-layer entry count (total)
- Session-note total count

Frame these as the moat-that-grows. They get bigger every month. Land the section with one line about what that means: *"These don't reset. The longer you use Command Room, the more expensive these are to recreate from scratch."*

### Step 4 — Render

Render the report verbatim in chat (for on-demand invocations) AND write to `_hq/operator-reports/YYYY-MM.docx` (for scheduled monthly run; per CONTRACT Rule 27, no .md deliverables — route through `shared/scripts/brief_writer.py`).

**Render template:**

```
**Your operating lift — [Window readable, e.g. "May 2026" or "Last 30 days"]**

[Section 0 — synthesis lead. ONE paragraph (~3-5 sentences) per Step 3 above. Names the anchor moment, the theme it crystallized, and 1-2 of the most consequential downstream events. Render at the top BEFORE any operational counts. Don't skip this — it's the difference between "here are some numbers" and "here's what this month meant." If the period was steady-state with no clear anchor, surface that honestly in one line.]

What would have slipped
  • [N] commitments captured that weren't in any other system
  • [N] relationships I flagged as going quiet (last contact past their usual cadence)
  • [N] decisions logged — including [reference 1-2 specific high-stakes decisions from DECISION_LOG.md, by name not generic]
  • [N] aging follow-ups surfaced

What you no longer have to hold
  • [N] people tracked with relationship context
  • [N] active projects with full session history
  • [N] emails read, [N] surfaced as needing your attention
  • [N] meetings processed into structured briefs

What got delivered without you asking
  • [N] morning briefings
  • [N] pre-meeting prep briefs
  • [N] email drafts in your voice
  • [N] cleanups

A conservative time estimate
  ~[N] hours of operational overhead absorbed
  (~[N] hours/week — [day-of-the-week equivalent line])

  This is conservative — it assumes you would have done each of these yourself at average speed. The real lift is usually higher because half of these would have just dropped.

Things that get more valuable the longer you use Command Room
  Communication profile: [N] sent emails sampled — every draft renders in your voice
  Decision log: [N] entries — you don't re-debate decisions you already made
  People layer: [N] interactions logged — relationship context never resets

Next [period]'s projection
  Based on current cadence: [N+] commitments captured, [N+] briefings, ~[N] hours absorbed.
```

The "Next period's projection" line is mandatory — it's what makes the report feel like a forward-looking ops update, not a backward-looking receipt.

**Deliverable link (per CONTRACT Rule 3 — v3.13.6+):** after the report renders in chat AND the `.docx` is written, surface the H2 deliverable link at the BOTTOM of the chat turn (not interspersed). Use the helper:

```python
import sys
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import doc_headline_link
from brief_path import get_brief_artifact_url
print(doc_headline_link("Operator report — <Window>", get_brief_artifact_url(output_path)))
```

Renders as `## → **[Operator report — May 2026](computer://...)**` — same format every .docx-emitting skill uses.

**Tone (per CONTRACT Rule 4 — v3.13.6+):** the report is user-facing. No FAIL/CRITICAL/ABORT framing, no `events.jsonl` / `entities.json` paths in user-visible prose, no `Phase N` / `Step Nc` labels, no internal mechanism names. The synthesis lead + body should read like a thoughtful operator's recap, not a system log. Operational counts are numbers; the synthesis is prose. Don't try to make every number sound like a story — let the numbers be numbers, and let the synthesis lead carry the meaning.

### Step 5 — Log

Append an `operator_report_generated` event to `events.jsonl`:
```json
{"type":"operator_report_generated","ts":"<ISO-now>","data":{"window":"<start>..<end>","hours_estimate":<N>,"output_path":"<path-or-chat>"}}
```

## Sourcing rules

1. **Never fabricate.** If `events.jsonl` doesn't have decision events, the "decisions logged" bullet shows 0 OR is omitted entirely. Don't make up "23 decisions" because it looks better.
2. **Conservative rubric.** The time-per-unit table errs on the low side. Better to under-claim a strong report than over-claim a thin one.
3. **Specific references over generic counts.** Section 1's "decisions logged" line names 1-2 specific decisions by topic ("including the May 3 decision on the Northstar deal") rather than a bare number. Same for cold relationships — name the relationship if a single one dominates.
4. **No section is mandatory.** If "What got delivered without you asking" returns 0 (scheduled tasks not enabled), surface the one-line note and skip the section. Better to be honest about a thin section than to render an empty one.

## Scheduling

This skill is invokable on-demand AND scheduled. Scheduled monthly run:
- **Fires:** 1st of every month at 7am in the workspace's `schedule_timezone`
- **Window:** previous calendar month
- **Output:** writes to `_hq/operator-reports/YYYY-MM.docx` + surfaces a one-line link in chat next time the user opens Command Room

The scheduled fire is wired separately in `enable-command-room-schedules` references — not configured here.

## What it doesn't do

- Does not generate marketing copy, sales decks, or external reports. CEO-facing only.
- Does not include sensitive content from meetings or emails — only counts and named entities the CEO already knows about. (E.g., decisions are referenced by topic/date, not by full content.)
- Does not replace `usage-report` — that skill reports token/connector spend for optimization decisions. Different surface, different audience.
- Does not bill, charge, or otherwise touch payment systems. Pure read-only report.
- Does not export to PDF or external formats. The report is delivered as a `.docx` via `brief_writer.py` per CONTRACT Rule 27 — the user can open it directly, paste into another tool, or share it.
- Does not fire if `_hq/data/events.jsonl` is empty (no activity to report on). Surface: *"There's nothing for me to report on yet. Give Command Room about a week of use and ask again."*
