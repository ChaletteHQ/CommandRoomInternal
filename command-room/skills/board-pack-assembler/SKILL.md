---
name: board-pack-assembler
description: "Assemble a multi-page board pack .docx from substrate signal — KPIs vs targets, period-over-period deltas, top wins, top concerns, decisions logged, asks, hiring slate, financials. The purest substrate consumer in the plugin; the pack writes itself from events.jsonl + decision-log + entities.json + QuickBooks MCP. Use when the CEO says 'build the board pack', 'assemble the board pack', 'board pack for [date]', 'prep for the [date] board meeting', 'generate this month's board update', 'put together the board deck', 'board package for', 'build board deck'. Reads ALL events in the reporting period aggregated by type, decision-log for period decisions, entities.json for project status + hiring slate, prior board packs for format consistency, QuickBooks MCP for financials. Writes board_pack_assembled event linking to the .docx artifact. DOES NOT fire on 'board update' as a short memo (memo-writer with memo_type=board_update — different scope, freeform narrative), 'monthly recap' (operator-report — CEO-self-facing, different audience), or 'investor update' (memo-writer with memo_type=investor_update — same shape but different audience-tuning)."
voice_block_last_refreshed: 2026-05-19
calibration_level: default
template_version: 1.0.0
---

## Skill Boundary

- **Use board-pack-assembler for:** the multi-page board pack with KPIs, deltas, wins/concerns, decisions, asks, appendices. Structured deliverable for the board meeting itself.
- **Use `memo-writer` (memo_type=board_update) for:** a shorter 1-2 page board narrative (freeform structure, between-meeting updates).
- **Use `operator-report` for:** the CEO-self-facing monthly Operating Lift recap. Different audience (you, not the board), different framing (value delivered, not state-of-business).
- **Use `weekly-recap` for:** weekly internal recap. Shorter cadence, smaller audience.

## Personification Contract (v3.13.8.4+)

Before composing the .docx board pack, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The cover-page intro paragraph uses the shape:

```
This pack was assembled by {brain_name} for {first_name}'s
{Board / Meeting Name} on {Date}. Source materials drawn from
{N} project files, {M} commitments, and {K} decisions logged
through {Last activity date}.
```

where `{first_name}` comes from `entities.json` `workspace.user_first_name` and `{brain_name}` defaults to `"Penelope"`. No additional name references inside section bodies — the cover intro carries the personification; the pack content stays formal and data-led.

## Writer Contract (v3.8.0+ — substrate-native, purest consumer)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `_hq/board-packs/BoardPack_[YYYY-MM-DD].docx` — the multi-page board pack. Per CONTRACT Rule 27 (no .md deliverables) the output is `.docx`.
- (Optional) `_hq/board-packs/BoardPack_[YYYY-MM-DD].pptx` — slide companion for in-room presentation if requested.

**Appends to:**
- `_hq/data/events.jsonl` — event type `board_pack_assembled` with `{board_meeting_ts, reporting_period_start_ts, reporting_period_end_ts, artifact_path, pptx_path?, section_counts: {wins, concerns, decisions_logged, asks}}`.
- `_hq/data/entities.json` — board member person records get `last_pack_received_ts` updated (when the pack is shared via email or Drive; if just generated, that's not a touch yet).

**Reads from (heaviest substrate consumer in the plugin):**
- `_hq/data/events.jsonl` — ALL events in the reporting period:
  - `commitment` + `commitment_resolved` to compute closure rate, open count, overdue count
  - `decision` to populate §5 (Decisions Logged This Period) directly
  - `meeting` events for meeting count + key meeting list
  - `outcome_positive` / equivalent signal events for §3 (Wins)
  - `pattern_break_detected` and dormancy signal events for §4 (Concerns)
  - `email_drafted` + `email_sent` for outbound activity counts
  - `intel_logged` for context dropped in the period
- `_hq/data/entities.json` — project status (active/at-risk/paused), hiring slate (people records with `role: "open"`), org tier changes. **This is the canonical source for project state-of-the-world per `references/SOURCE_OF_TRUTH.md`.** Pre-v3.11.4 this skill also read `_hq/views/MASTER_TRACKER.md` for the same roll-up; that's been removed because the tracker is a Tier 2 projection that can lag entities.json + events.jsonl by hours-to-days. Reading both risks rendering inconsistent state in the same pack.
- `_hq/board-packs/BoardPack_*.docx` (prior packs) — for format/voice consistency. The most recent prior pack defines the section ordering, KPI list, target lines that this pack continues.
- `_hq/data/events.jsonl` — `type == "decision"` events about KPI targets (e.g., "Q1 MRR target = $450K" — surfaces in the KPIs vs Targets section).
- QuickBooks MCP (if installed) — for financial detail. Specific tools to call: `mcp__*__qbo_accounting_get_balance_sheet` (for assets/liabilities/equity), `mcp__*__qbo_accounting_get_ap_aging_summary` + `mcp__*__qbo_accounting_get_ar_aging_summary` (AR/AP aging buckets), `mcp__*__qbo_accounting_get_sales_by_customer_summary` (revenue concentration). Use `mcp__*__profit-loss-quickbooks-account` if available for P&L; otherwise compose from balance-sheet + AR aging. **No-QB fallback:** if no `qbo_*` tool is discoverable in the session, skip §7C entirely and put a single line in §7C's place: *"Financials aren't in this pack yet — once QuickBooks is connected, AR/AP, runway, and P&L will fill in automatically."* Do NOT estimate financials from email/intel signal — the operator's board would rather see a missing-data note than inferred numbers.
- Calendar MCP — to find the board meeting itself (if the trigger references a date, confirm the date matches the meeting on calendar; if it doesn't, ask).

**Conflict boundary:** sole writer of `board_pack_assembled` events. The board pack is a pure substrate roll-up — almost every cell of every section comes from events.jsonl or entities.json. The skill does no substantive new inference; it composes existing signal.

---

# board-pack-assembler

The purest substrate consumer in the plugin. Pre-substrate, board pack assembly is 4-6 hours of pulling data from 5 systems (CRM, QB, calendar, Slack archives, project tools) into a Word doc. Command Room's data layer already has every signal needed; this skill composes them.

The output isn't AI-generated narrative dressed up as analysis — it's structured roll-up of what your own substrate already records. The model's job is layout and section composition, not content generation.

## What It Does

For "build the board pack for 2026-05-28":

1. Verify the board meeting on calendar; compute reporting period (default: last calendar month, or since last board pack — whichever is shorter).
2. Pull all event types listed in Writer Contract for the period.
3. Compute KPI deltas vs targets (target line read from prior pack + any `decision` events updating targets).
4. Compose each section from the corresponding substrate signal.
5. Render the .docx via `shared/scripts/brief_writer.py` with the board-pack template.
6. (Optional) Render the .pptx if user requested slide companion.
7. Surface in chat with the link + 1-line summary.
8. Append `board_pack_assembled` event.

## How to Use

```
"build the board pack for 2026-05-28"
"assemble the board pack"           (defaults to next board meeting on calendar)
"board pack for May"                (resolves to most recent / upcoming board meeting in May)
"prep for the May 28 board meeting"
"generate this month's board update"
"put together the board deck"        (interprets "deck" as wanting .pptx companion too)
```

## How It Works

### Phase 1 — Resolve the meeting + period

Parse trigger for board meeting date. If absent, query Calendar MCP for upcoming meetings with title matching `board|investor|directors`. Compute reporting period:
- Default: from `previous_pack.reporting_period_end_ts` (read from prior `board_pack_assembled` event) to `now`
- Fallback: last calendar month
- If user specifies window in trigger, use that

### Phase 2 — Substrate pull (parallel)

In parallel:
- All `commitment` + `commitment_resolved` events in period → closure rate, count, overdue
- All `decision` events in period → §5 source
- All `meeting` events → meeting count + key meetings (those with high-value attendees or project-tagged)
- `outcome_positive` / `customer_won` / similar → §3 source
- `pattern_break_detected` + dormancy signals → §4 source
- `email_drafted` / `email_sent` → outbound activity
- `intel_logged` → context dropped
- entities.json: project tier changes, hiring slate, org tier changes
- QuickBooks (if discoverable via `qbo_*` MCP tool match): balance sheet, AR/AP aging summaries, sales-by-customer summary. Compose runway from balance-sheet cash + last-period burn delta. If no `qbo_*` tool is discoverable, skip the financial pull entirely — DO NOT infer financials from email/intel signal. §7C will render the missing-data note (per Writer Contract).
- Prior board pack — extract KPI list, targets, section order

### Phase 3 — Compose sections

**§1 Executive Summary** — what changed since last board (≤6 bullets).

**§2 KPIs vs Targets** — table with metric / current / target / vs-target / trend. KPI list inherited from prior pack unless a `decision` event in period adjusted it.

**§3 Wins** — top 3 outcomes pulled from `outcome_positive` events ranked by impact.

**§4 Concerns** — top 3 pulled from `pattern_break_detected` + dormancy signals + lost-deal events. Each concern has 1-line mitigation.

**§5 Decisions Logged** — direct enumeration of `decision` events in period (rationale + date).

**§6 Asks** — pulled from the prior session-notes pattern "ASK board:" if the user has been logging them, else surface as "[add asks here]" placeholder.

**§7 Appendices** — pipeline by stage, hiring slate, QB financial detail.

### Phase 4 — Render

Compose the .docx via `shared/scripts/brief_writer.py` board-pack template. If user requested .pptx, also render a slide version where each section becomes 1-2 slides with key bullets.

### Phase 5 — Surface + event

Render in chat:
```
Board pack ready for the May 28 meeting (covering April 28 – May 28).
  Pack: [link]
  Slides: [link]   (if generated)

  Inside: 3 wins, 2 concerns, 5 decisions, 2 asks for the board.
  Biggest move: MRR up 13% over last month — $478K against a $470K target.
```

Append `board_pack_assembled` event with section counts.

## Output Structure (.docx, ~6-8 pages)

```
CHALETTE HOLDINGS — BOARD PACK
Board: 2026-05-28 (Tue, 2 PM ET) | Period: April 28 – May 28

TABLE OF CONTENTS
  1. Executive summary
  2. KPIs vs targets
  3. Wins (3)
  4. Concerns (3) + mitigation
  5. Decisions logged this period (5)
  6. Asks for the board (2)
  7. Appendix — pipeline, hiring slate, financials

PAGE 1 — EXECUTIVE SUMMARY
  What changed since last board:
    • MRR $478K, +13% MoM (target was +10%)             ✓
    • Closed Acme Co — $52K ARR, largest deal to date   ✓
    • Lost Northstar Partners (cited timing)                  — postmortem §4
    • Command Room hit $200K ARR in 60 days             ✓

  Decisions you should know about (detail §5):
    • Hired Rio Sample as Head of Ops (2026-04-08)
    • Paused the consumer pilot (2026-05-04)
    • Locked Command Room pricing tiers (2026-04-29)

  Asks (detail §6):
    • Approval to extend Q3 hiring envelope by 2 FTE
    • Intro to the operator network discussed last meeting

PAGE 2 — KPIs VS TARGETS
  Metric          Apr      Target    vs Tgt    Trend
  MRR             $478K    $470K     +1.7%     ▲
  NRR             134%     125%      +9 pts    ▲
  New customers   8        6         +33%      ▲
  Logo churn      1        ≤1        flat      —
  Burn            -$31K    -$40K     better    ▲
  Runway          18.4 mo  >18       ✓         flat
  Sales cycle     42 days  <60       ✓         ▲

[... continues for 5 more pages ...]

APPENDICES (auto)
  A. Pipeline by stage (from your project tracker)
  B. Hiring slate — open roles, candidates in flight
  C. Financial detail by line item (QuickBooks)
```

## DOES NOT

- Generate narrative not grounded in substrate signal. Every claim in the pack comes from a specific event seq or entity record. If a section has no substrate signal, the section shows "(nothing logged for this section this period)" rather than fabricating.
- Send the pack to board members. Generates the artifact; user reviews and shares manually (or via Gmail / Drive MCP if installed).
- Modify entities.json beyond `last_pack_received_ts` (and only when user explicitly marks the pack as shared).
- Override the KPI list arbitrarily. KPI list is inherited from prior pack unless an explicit `decision` event in period adjusted it.
- Run if no prior board pack AND no explicit KPI list defined. First-use bootstrap: ask the user to define the KPI list (5-question wizard) → write a `decision` event capturing it → then proceed.
