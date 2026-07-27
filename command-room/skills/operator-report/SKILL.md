---
name: operator-report
description: "Generates the CEO-facing 'Operating Lift' report — what would have slipped, what got captured, what got delivered unasked, and a conservative time-absorbed estimate, every number computed in code. Fires on: 'operator report', 'show me the value', 'portfolio velocity', 'monthly operating report', plus 'tune operator-report' and 'customize operator-report'. Output: chat summary plus forwardable .docx with trend context. Does NOT fire on 'weekly recap' / 'what happened this month' (weekly-recap — events digest, not lift accounting), 'value receipt' (value-receipt — the forwardable ROI receipt), or 'usage report' (usage-report — cost/volume telemetry). Full trigger list and section spec: Routing section in the body."
---

# operator-report

The CEO-facing value report. Surfaces what Command Room actually did for them over a window — written like a board update for their own operating layer, not a usage dashboard.

## What this is NOT

This is not `usage-report`. That skill reports developer-facing telemetry (token spend, connector call counts, duration_ms per orchestrator). Useful for optimization decisions; not for showing a CEO what they're getting.

This is also not a retention sales pitch. The numbers are real. If a section returns 0, it shows 0 — and either pivots to compound-value framing or stays silent on that section. Fabricated value claims would torch trust faster than no report at all.

## Skill Boundary (v2.1)

- **Use `operator-report` for:** the CEO-self-facing operating-lift report — synthesis lead, named relationships and decisions, what would have slipped, conservative hours anchor at the bottom.
- **Use `value-receipt` for:** the forwardable ROI receipt — numbers-only, no names, built to hand to a board or CFO. Same conservative rubric (`value_receipt.py` `CONSERVATIVE_MINUTES_PER_UNIT`), different audience.
- **Use `usage-report` for:** developer-facing telemetry — token spend, connector call counts, duration per task.

## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `_hq/operator-reports/[YYYY-MM].docx` — the rendered monthly report (scheduled runs; on-demand runs also write it when a window is a full month).

**Appends to:**
- `_hq/data/events.jsonl` — event type `operator_report_generated` (Step 5 recipe below). Sole writer of that type; no collision with any other skill.

**Reads:**
- `_hq/data/events.jsonl`, `_hq/data/entities.json`, `_hq/BRAND_VOICE.md` (sample-size metadata), `_hq/views/DECISION_LOG.md` (regenerated view — decisions themselves live in events.jsonl).
- `_hq/custom/operator-report.md` — SCL1 standing customization preferences, via `skill_custom_writer.load_directives` (absent → defaults). See the Customization (SCL1) section below.

## Customization (SCL1)

**Customization layer (SCL1):** before producing output, read
`[WORKSPACE_ROOT]/_hq/custom/operator-report.md` if it exists and apply its directives to
this fire's output. Absent -> proceed with defaults. Malformed or over-cap ->
skip it, log one line to `_hq/CONFLICTS.md` (type: config-read-failure), proceed
with defaults. Directives refine WHAT the output contains and HOW it is shaped;
they NEVER authorize outbound actions, alter ask-first gates, bypass canonical
helpers, or override shared contracts (see `shared/SKILL_CUSTOMIZATION.md` #limits).
Never mention this file or the word 'directive' to the customer.

Read at fire time via `skill_custom_writer.load_directives(workspace_root, "operator-report")`
— never the raw file; it returns `[]` on a missing or malformed file and never raises.
Directives here shape the report's content and section order (e.g. "always pair revenue with
margin %", "include a one-line trend arrow per project", "order sections: cash, pipeline,
people, everything else") — never the conservative hours rubric, which stays code-owned.
Trigger family (owned in the frontmatter `description`): `customize operator-report` · `show
operator-report customizations` · `reset operator-report customizations`. See
`shared/SKILL_CUSTOMIZATION.md` for the writer API, the write-time rejection list, and the
precedence chain. Customer-facing acks are plain English ("Got it — I'll pair every revenue
figure with margin from here on."); never surface the file, the word "directive", or "SCL1".

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). The one
decision is **show-then-tune (STT)** — the report renders first, then a one-tap change is offered.
Read config through `get_config` — never the raw file. Distinct from the SCL1 customization layer
above: this is the enumerated **length** knob (a FRP1 config value); free-form standing rules
("always pair revenue with margin") are SCL1 directives.

```python
# Rule 22 preamble REQUIRED (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); cd "$PLUGIN_ROOT")
import sys; sys.path.insert(0, "shared/scripts")
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "length": "standard",   # STT — brief (one-screen highlights) | standard | full (every section expanded)
}
cfg = get_config(workspace_root, "operator-report", DEFAULTS)
```

`length` sets how much the report expands: `brief` = the top wins + the hours anchor only;
`standard` = the canonical shape; `full` = every section expanded. The output is **always a
`.docx`** regardless of length (CONTRACT Rule 27 — this kills the md-vs-docx ambiguity the
settings handoff flagged). Config keys are validated at write against
`shared/data-schemas/skill_config.schema.json` (settings-layer C4) — an unknown key rejects loudly.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "operator report", scheduled 1st-of-month fire | produce the report with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "operator-report", DEFAULTS)` BEFORE rendering, then append the first-run footer after the .docx link. |
| **Show settings** | "show operator-report settings" | render current config in plain English; no report. Listed beside "show operator-report customizations" (SCL1) in the footer. |
| **Tune** | "tune operator-report" | freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-produce. |
| **Reset** | "reset operator-report to defaults" | `wipe_skill_config(workspace_root, "operator-report")` → next fire is a first-fire again. |

**The first-run block (footer):**

> *First time building your operating report. I set it to **standard length**. Say "tune
> operator-report" to make it shorter or fuller, or just tell me ("keep it brief" / "give me the
> full version").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "keep it brief" / "just the highlights" | `length = brief` |
| "give me the full version" / "expand everything" | `length = full` |
| "standard length" / "the normal report" | `length = standard` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-produce + confirm in one line.

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
1. `_hq/data/events.jsonl` — full event stream within the window. **Read via the org-scoped reader, never a raw load** (PGUARD1): `from events_io import load_events_org_scoped; events, skipped = load_events_org_scoped(workspace_root)` — it applies the account-scope mask and drops personal-lane rows by design, so a reclassified personal account or a personal reminder can never leak into the report's counts.
2. `_hq/data/entities.json` — current counts (people, orgs, projects)
3. `_hq/BRAND_VOICE.md` — sample-size metadata for voice claim
4. `_hq/views/DECISION_LOG.md` — decision count (regenerated view; the canonical decisions are `decision` events in events.jsonl)
5. Scheduled-task fire records (also in events.jsonl as `pack_run` events)

Compute the 6 report sections (0-5). **No section is fabricated.** If a count is 0, the report acknowledges it or skips the section per the rules below.

### Step 3 — Compute the 6 sections (v3.13.0+ — added synthesis lead at the top per #1 feedback)

**Section 0: SYNTHESIS LEAD (v3.13.0+ — REQUIRED).** Per M's 2026-05-20 feedback #1, the monthly operator report was previously only operational metrics ("hours saved this month"). That misses the bigger value: WHAT THE MONTH MEANT. The synthesis lead answers in one paragraph: what was the dominant theme of this period, what was the anchor moment that spun off most movement, what shifted in your business between start-of-period and end-of-period. Use the same pattern as Friday Wrap's lead paragraph (which M called out as the best output we'd seen) — one anchor moment + the theme.

Method:
1. Pull the top 5 events by activity-cluster (events that connect to the most other events via shared person_ids / project_ids / org_ids).
2. Identify the anchor moment — the single event whose downstream connections most exceed other events. Often a stage talk, a board meeting, a launch, a major decision logged.
3. Compose one paragraph (~3-5 sentences) that names the anchor moment, the theme it crystallized, and 1-2 of the most consequential downstream events.
4. Render at the TOP of the report (Section 0, before the operational counts).

Example (the kind of output #1 was asking for):

> *"Heavy month on go-to-market and the Acme Co partnership. The May 14 CEO-group stage talk was the anchor — it spun off ~$45K in early pipeline, two demo cycles already booked, and a new vertical wedge that's now an active project. Acme Co moved from handshake to paperwork. Operating businesses stayed in steady-state; older consulting clients continued to fade."*

If the period has no clear anchor moment (truly mid-cycle period, no events with significant cluster connections), surface that honestly: *"This period was steady-state — no single anchor, just baseline execution across [active orgs]."*

The synthesis lead is the difference between "here are some numbers" and "here's what this month meant." Don't skip it.

**Section 1: What would have slipped**

Pull from the Step-2 org-scoped event list within the window (same `load_events_org_scoped` read — never re-read raw):
- Commitments captured outside an existing tracker context (i.e., extracted from a meeting or email, not user-created). Count `type: commitment` events with `source_skill ∈ {meeting-notes, inbox-triage, follow-up-ritual, scan-for-commitments}`.
- Cold relationships flagged. Count `type: pattern_break_detected` events with `source_skill ∈ {dormant-customer-scan, insight-generator, pulse}` (the canonical dormancy-flag event type per events.schema.json — pre-v3.13.6 this spec called for `dormant_flag` which isn't in the enum and would have silently returned 0). **Back-compat (client migration):** normalize each event's `source_skill` through `source_skill_compat.normalize_source_skill` before the set check so workspaces whose Pulse history predates the v2.14.27 rename (`source_skill='cr-dont-forget'`) still count — it normalizes to `pulse`. events.jsonl is append-only; never rewrite it.
- Decisions logged from real interactions. Count `type: decision` events with `source_skill: meeting-notes` or `decision-log`.
- Aging follow-ups surfaced. Count open `type: commitment` events whose `data.due` is past the window-end date — i.e., commitments that aged into overdue status during the window. (Pre-v3.13.6 this spec called for `type: aging_followup` which isn't in the enum.)

If a category returns 0, omit the bullet entirely (don't say "0 commitments captured" — that reads worse than not mentioning it).

**Quantify discipline (EXEC1 element 3 — Section 1 only; Section 0 is untouched, it's the prototype).** Each "what would have slipped" item that traces to a valued relationship gets the dollar via `shared/scripts/quantify.py::money_time_tag(commitment_or_thread, entities)` — "the pricing reply you owed Acme — a $240K relationship." Append the tag ONLY when `money_time_tag` returns non-None (the helper traces commitment → thread → org → revenue/deal field and returns None when that field is absent). NEVER hand-type a dollar figure and NEVER estimate — a workspace without the field simply shows the item with no tag.

**Section 2: What you no longer have to hold**

Snapshot counts from `entities.json`:
- People tracked with relationship context (count `entities.people` where `last_interaction` is within the last 90 days — i.e., relationship is alive, not just a name).
- Active projects with session history (count `entities.projects` where `session_count` ≥ 1 OR there's at least one event with `primary_thread_id` matching).
- Emails read / triaged (count `interaction` events with `source: gmail` or `source_skill: inbox-triage` in window — the canonical event type per events.schema.json; pre-v3.13.6 this spec called for `email_triaged` which isn't in the enum).
- Meetings processed (count `meeting_processed` events in window).

If `entities.json` is missing any of these fields, fall back to event-stream counts within the window.

**Section 3: What got delivered without you asking**

Pull scheduled-task fire records. `pack_run` events carry `data.task_id` (NOT `orchestrator` — that field name was the pre-v3.13.6 spec, never wired). **Back-compat (client migration):** a `pack_run` fire is identified by `data.task_id` OR `data.kind` OR `source_skill`; match by normalizing each through `source_skill_compat.normalize_source_skill` (so legacy `source_skill='cr-commitments'` history → `commitments`, `'cr-dont-forget'` → `pulse`). This mirrors the canonical `_is_for` matcher in `SHARED_CHAT_OUTPUT_PROTOCOL.md`. Never rewrite events.jsonl. Task ids per `enable-command-room-schedules/SKILL.md`:
- Morning briefings generated — `pack_run` with `data.task_id: morning-brief`
- Pre-meeting prep briefs — `pack_run` with `data.task_id: upcoming-meetings` AND yielded at least one brief
- Email drafts produced in user's voice (count `email_drafted` events)
- cleanups run — `pack_run` with `data.task_id: cleanup` (cleanup runs as a scheduled task every Sunday, v3.17.0+). Every cleanup run also appends a `cleanup_run` event, so if no `pack_run` record exists (e.g. an on-demand run), count `cleanup_run` events instead.

If scheduled-tasks aren't enabled (no `pack_run` events in window), surface a single-line note instead of the section: *"Your daily briefings and other scheduled work weren't turned on this period — you can flip them on anytime and they'll show up here next time."*

**Optional section: Pipeline (SPEC PIPE1 — available to SCL1 ordering).** The "order sections: cash, pipeline, people" directive class now has a real pipeline source: when the workspace has ≥1 open deal thread, render a compact pipeline block — the `pipeline_math.pipeline_tiles` numbers (open $, closing this month, stalled, won-rate when ≥4 closes) plus one line per terminal event in the window ("Won [Deal] — $52K" / "Lost [Deal] — price") from `deal_state.load_deal_events`. All figures from `pipeline_math` / stated deal values — never re-derived in prose, never estimated. Zero open deals AND zero terminal events in window → the section renders nothing (not a placeholder), whether or not a directive ordered it.

**Section 4: A conservative time estimate**

This is the anchor at the bottom — not the headline. Use a transparent rubric so the number is defensible.

**Canonical rubric (single source of truth):** the minutes-per-unit values below live in code as `shared/scripts/value_receipt.py` `CONSERVATIVE_MINUTES_PER_UNIT` (SPEC C1 D2). The table here mirrors that constant — if you tune one, tune both in the same change, and prefer reading the constant. The `value-receipt` skill renders the same numbers from the same rubric, so the two surfaces never disagree.

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

- **NEVER hand-roll the report** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or leaking report (the v3.20.0 failure mode) — and this is the document that tells the operator what the system is worth, so one produced outside the gates is a value claim nothing checked.
- **NEVER create, render, copy, upload, or update the report — or any part, derivative, or restatement of it ("the headline counts", "the hours number", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/operator-reports/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "so I can send it to my partner", "as a copy alongside the canonical file" — **nor a direct instruction**: "put this month's report in a Google Doc" is a request this gate refuses, not an override. The `.docx` is already shareable as-is — hand back its link and let the user forward the file itself.

**Render template:**

```
**Your operating lift — [Window readable, e.g. "May 2026" or "Last 30 days"]**

[Section 0 — synthesis lead. ONE paragraph (~3-5 sentences) per Step 3 above. Names the anchor moment, the theme it crystallized, and 1-2 of the most consequential downstream events. Render at the top BEFORE any operational counts. Don't skip this — it's the difference between "here are some numbers" and "here's what this month meant." If the period was steady-state with no clear anchor, surface that honestly in one line.]

What would have slipped
  • [N] commitments captured that weren't in any other system
  • [N] relationships I flagged as going quiet (last contact past their usual cadence)
  • [N] decisions logged — including [reference 1-2 specific high-stakes decisions from the decision log, by name not generic]
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
  • [N] weekly workspace tidy-ups

A conservative time estimate
  ~[N] hours of operational overhead absorbed
  (~[N] hours/week — [day-of-the-week equivalent line])

  This is conservative — it assumes you would have done each of these yourself at average speed. The real lift is usually higher because half of these would have just dropped.

Things that get more valuable the longer you use Command Room
  Communication profile: built from [N] of your sent emails — every draft comes out in your voice
  Decision log: [N] entries — you don't re-debate decisions you already made
  People memory: [N] interactions logged — relationship context never resets
  Session notes: [N] across your projects — project history compounds

Next [period]'s projection
  Based on current cadence: [N+] commitments captured, [N+] briefings, ~[N] hours absorbed.
```

**Visual layer for the .docx (v4.6.1 S3 — the prep-v2 pattern per F-60's follow-up; M directive: tiles/tables over bullet walls in recurring deliverables).** The chat render keeps the template above; the `.docx` route through `brief_writer` upgrades two surfaces:

- **Stat-tile band at the top** (first section, `tiles` key): the report's headline counts — commitments captured · decisions logged · meetings processed · briefings delivered · ~hours absorbed. 1-5 tiles, every value one of the Step-3 counts (substrate-derived), never estimated beyond what Step 3 already computes. **A tile with a zero/unknown value is DROPPED, never rendered empty** — brief_writer's renderer refuses empty tiles; when nothing is countable, skip the band.
- **"Things that get more valuable" renders as a two-column table** (`table` key: asset | current size), replacing the four hanging-indent lines. No rows with unknown counts — drop the row, and drop the table if no rows survive.
- **Trend line under the tile band (SPEC OUT3).** When the workspace has at least 2 full calendar months of history, compute the trailing window (the report month plus up to 2 prior full months) through the SAME rubric the counts already use — `value_receipt.compute_metrics(events, <window_start>, <window_end>)` — and attach `charts: [value_receipt.build_trend_chart(computed["per_month"])]` to the tile-band section. The helper returns the month-over-month hours line with every point verbatim from `per_month` (one computation, one owner), or `None` below 2 months / all-zero — omit the key then. Renders best-effort via `charts.try_chart_png` inside `make_brief`: no rasterizer on this machine = the section renders exactly as before. Selection rules: `shared/CHART_SELECTION.md`.

Everything else stays as the template renders it. No decorative charts ever — the trend line above is substrate-derived through the rubric, which is the only kind of chart this report carries. No fabricated numbers.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "[N] cleanups · People layer: [N] interactions · every draft renders in your voice"
- Good: "[N] weekly workspace tidy-ups · People memory: [N] interactions · every draft comes out in your voice"

The "Next period's projection" line is mandatory — it's what makes the report feel like a forward-looking ops update, not a backward-looking receipt.

**Deliverable link (per CONTRACT Rule 3 — v3.13.6+):** after the report renders in chat AND the `.docx` is written, surface the H2 deliverable link at the BOTTOM of the chat turn (not interspersed). Use the helper:

```python
import sys
# Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1))
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import doc_headline_link
from brief_path import get_brief_artifact_url
print(doc_headline_link("Operator report — <Window>", get_brief_artifact_url(output_path)))
```

Renders as `## → **[Operator report — May 2026](computer://...)**` — same format every .docx-emitting skill uses.

**Tone (per CONTRACT Rule 4 — v3.13.6+):** the report is user-facing. No FAIL/CRITICAL/ABORT framing, no `events.jsonl` / `entities.json` paths in user-visible prose, no `Phase N` / `Step Nc` labels, no internal mechanism names. The synthesis lead + body should read like a thoughtful operator's recap, not a system log. Operational counts are numbers; the synthesis is prose. Don't try to make every number sound like a story — let the numbers be numbers, and let the synthesis lead carry the meaning.

### Step 5 — Log

Append the `operator_report_generated` receipt via the canonical helper (`shared/scripts/receipts.py`, v4.5.2 R1) — never a hand-rolled JSON append. This receipt is the monthly-report job's RUN COUNTER (the maintenance task's monthly leg, MAINT1) (the value receipts the same fire emits are freshness signals only — one fire writes several of them), so exactly one per run:

```python
import sys
sys.path.insert(0, "shared/scripts")
from receipts import log_receipt
log_receipt(
    workspace_root, "monthly-report",
    receipt_type="operator_report_generated",
    fired_via="scheduled",   # "manual" on an on-demand `operator report` chat fire
    extra_data={"window": "<start>..<end>", "hours_estimate": "<N>", "output_path": "<path-or-chat>"},
)
```

## Sourcing rules

1. **Never fabricate.** If `events.jsonl` doesn't have decision events, the "decisions logged" bullet shows 0 OR is omitted entirely. Don't make up "23 decisions" because it looks better.
2. **Conservative rubric.** The time-per-unit table errs on the low side. Better to under-claim a strong report than over-claim a thin one.
3. **Specific references over generic counts.** Section 1's "decisions logged" line names 1-2 specific decisions by topic ("including the May 3 decision on the Northstar deal") rather than a bare number. Same for cold relationships — name the relationship if a single one dominates.
4. **No section is mandatory.** If "What got delivered without you asking" returns 0 (scheduled tasks not enabled), surface the one-line note and skip the section. Better to be honest about a thin section than to render an empty one.

## Scheduling

This skill is invokable on-demand AND scheduled. Scheduled monthly run:
- **Fires:** as the monthly-report job inside the `maintenance` task (MAINT1) — due at the first fire on/after the 1st of each month (~6:45 AM), self-healing to the next fire if the computer was off
- **Window:** previous calendar month
- **Output:** writes to `_hq/operator-reports/YYYY-MM.docx` + surfaces a one-line link in chat next time the user opens Command Room

The scheduled fire is real (SPEC C1, task topology updated in MAINT1): the monthly-report JOB inside the `maintenance` task (nominal cadence `0 0 1 * *` in `maintenance_dispatcher.MAINTENANCE_JOBS`) runs this report AND the `value-receipt` for the previous month; the task registers via `enable-command-room-schedules` Step 1.D (the `SILENT_TASKS` registry loop, Phase 3 / SPEC-2.3). (Before C1, this section claimed a monthly fire that was never actually wired into `DEFAULT_SCHEDULES` — folding both reports into the one monthly task is where that claim became real, and it avoids paying the overlapping substrate read twice.) The on-demand trigger always works regardless of scheduled-task reliability.

## What it doesn't do

- Does not generate marketing copy, sales decks, or external reports. CEO-facing only.
- Does not include sensitive content from meetings or emails — only counts and named entities the CEO already knows about. (E.g., decisions are referenced by topic/date, not by full content.)
- Does not replace `usage-report` — that skill reports token/connector spend for optimization decisions. Different surface, different audience.
- Does not bill, charge, or otherwise touch payment systems. Pure read-only report.
- Does not export to PDF or external formats. The report is delivered as a `.docx` via `brief_writer.py` per CONTRACT Rule 27 — the user can open it directly, paste into another tool, or share it.
- Does not fire if `_hq/data/events.jsonl` is empty (no activity to report on). Surface: *"There's nothing for me to report on yet. Give Command Room about a week of use and ask again."*

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Generates a CEO-facing 'Operating Lift' report — what would have slipped, what got captured, what got delivered without being asked, and a conservative time-absorbed estimate at the bottom. NOT a usage/billing dashboard — a value-of-having-this report shaped like a board update for the CEO's own operations. Triggers: 'operator report', 'operating lift', 'what have you done for me', 'what did you save me', 'show me the value', 'what did you do this month', 'show me the impact', 'time saved report', 'my operating report', 'portfolio velocity', 'which projects are gaining momentum' (the 60-day project-momentum scorecard — coach deliverable-catalog 2.3, rendered from events.jsonl by this report). Auto-generates on the 1st of every month to `_hq/operator-reports/[YYYY-MM].docx` (per CONTRACT Rule 27, no .md deliverables). Also takes standing customization preferences — use when the CEO says 'customize operator-report', 'show operator-report customizations', 'reset operator-report customizations'. Also handles first-run settings — use when the CEO says 'tune operator-report', 'show operator-report settings', 'reset operator-report to defaults'. DOES NOT fire on 'monthly recap' / 'what happened this month' (weekly-recap's month window — a month-in-review of YOUR business, not a report on what I did). DOES NOT fire on 'value receipt' / 'roi receipt' / 'show me the receipt' (that's value-receipt — the forwardable numbers-only receipt built for a board or CFO; operator-report is the CEO-self-facing narrative with a synthesis lead and named relationships) or 'usage report' / 'token usage' (usage-report — developer-facing spend telemetry).
