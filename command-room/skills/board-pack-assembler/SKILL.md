---
name: board-pack-assembler
description: "Assemble a multi-page board pack .docx from the workspace's own signal — KPIs vs targets, period deltas, top wins, top concerns, decisions logged, asks, hiring slate, financials via QuickBooks where connected. Fires on: 'build the board pack', 'prep the board pack', 'assemble the board pack', 'board pack for [date]', 'board package', 'build board deck', 'generate this month's board update'. Reads the full reporting period's events, the decision log, entity status, and prior packs for format consistency. Does NOT fire on 'board update' as a short memo (memo-writer), 'monthly recap' (weekly-recap), 'investor update' (memo-writer), or 'prep me for the board meeting' (call-prep — the meeting brief, not the pack). Section spec and data sources: Routing section in the body."
voice_block_last_refreshed: 2026-05-19
calibration_level: default
template_version: 1.0.0
---

## Deliverable Render Gate (GATE1 — MUST, v3.20.x)

This skill produces a `.docx` (and optional `.pptx`) deliverable. The `.docx` MUST be produced through the canonical chokepoint — no exceptions:

- **Render ONLY via `shared/scripts/brief_writer.py` `make_brief(brief_kind="board_pack", ...)`.** That single call runs the output-contract gate (B3 — exec-summary cap, no blank KPI cells), the voice-tell gate (B2), and the post-render leak scan, in that order, BEFORE the file is written.
- **NEVER hand-roll a `.docx`** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship substandard, voice-violating, or PII-leaking documents (the v3.20.0 failure mode). A board pack is the highest-stakes external surface — a bypass here is the worst case.
- **NEVER answer a deliverable request with a chat-only draft.** A board-pack request always produces the rendered file through `make_brief`.
- **Detectability:** `make_brief` emits a `gate_ran` audit event recording which gates ran. A board-pack fire that yields a document with NO `gate_ran` event for that turn is a flagged bypass. Pass `workspace_root` to `make_brief` so the event lands in substrate.
- **Visual pass (SPEC OUT2 §3, after every save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 6-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

If anything below seems to contradict this gate, THIS GATE WINS.

## Skill Boundary (v2.1)

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
- QuickBooks MCP (if installed) — for financial detail. Specific tools to call: `mcp__*__qbo_accounting_get_balance_sheet` (for assets/liabilities/equity), `mcp__*__qbo_accounting_get_ap_aging_summary` + `mcp__*__qbo_accounting_get_ar_aging_summary` (AR/AP aging buckets), `mcp__*__qbo_accounting_get_sales_by_customer_summary` (revenue concentration). Use `mcp__*__profit_loss_quickbooks_account` if available for P&L; otherwise compose from balance-sheet + AR aging. **No-QB fallback:** if no `qbo_*` tool is discoverable in the session, skip §7C entirely and put a single line in §7C's place: *"Financials aren't in this pack yet — once QuickBooks is connected, AR/AP, runway, and P&L will fill in automatically."* Do NOT estimate financials from email/intel signal — the operator's board would rather see a missing-data note than inferred numbers.
- Calendar MCP — to find the board meeting itself (if the trigger references a date, confirm the date matches the meeting on calendar; if it doesn't, ask).

**Also reads (SPEC OUT2 §5):**
- `_hq/data/skill_config/board-pack-assembler.json` — first-run knobs, via `skill_config_writer.get_config` (see First-Run Personalization below).
- `_hq/custom/board-pack-assembler.md` — SCL1 standing customization preferences, via `skill_custom_writer.load_directives` (absent → defaults). See the Customization (SCL1) section below.

**Also writes (SPEC OUT2 §5):** `_hq/data/skill_config/board-pack-assembler.json` on first fire, tune, and reset — always via `skill_config_writer` (`save_skill_config` / `wipe_skill_config`), never a raw file write.

**Conflict boundary:** sole writer of `board_pack_assembled` events. The board pack is a pure substrate roll-up — almost every cell of every section comes from events.jsonl or entities.json. The skill does no substantive new inference; it composes existing signal.

---

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`).
Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22). Bash preamble:
# SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "pack_length": "full",      # full (~6-8 pages, today's shape) | condensed (~3-4 pages)
    "deck_companion": "off",    # off (.pptx only when the trigger asks) | on (always) | ask (each pack)
    "kpi_set": [],              # [] = inherit from the prior pack + decision events (today's behavior)
}
cfg = get_config(workspace_root, "board-pack-assembler", DEFAULTS)
```

- `pack_length = "condensed"` trims §3–§5 to the single top item each and folds the appendices into
  one summary page; `"full"` is today's 6–8-page shape.
- `deck_companion` governs the optional .pptx: `off` renders it only when the trigger explicitly asks
  (today's behavior), `on` always renders it, `ask` asks once per pack.
- `kpi_set`: a non-empty list REPLACES the inherited KPI list in §2. **ASK-FIRST (AF class):** the KPI
  list is the pack's spine — any change to it (structured tune OR freeform remark) is always confirmed
  explicitly before saving, never applied silently. The other two knobs are show-then-tune (STT).

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "build the board pack" | assemble the pack with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "board-pack-assembler", DEFAULTS)` BEFORE rendering, then append the first-run footer after the .docx link. |
| **Show settings** | "show board-pack-assembler settings" | render current config in plain English; no pack. |
| **Tune** | "tune board-pack-assembler" / "tune the board pack" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)`. KPI-set changes confirm first (AF). |
| **Reset** | "reset board-pack-assembler to defaults" | `wipe_skill_config(workspace_root, "board-pack-assembler")` → next fire is a first-fire again. |

**The first-run block (footer — this skill ends in a chat link to the .docx, not a widget):**

> *First time assembling your board pack. I set 3 defaults: **full-length pack** · **slide companion
> only when you ask for one** · **KPI list carried over from your last pack**. Say **"tune the board
> pack"** to change any of these, or just tell me what you'd change — the KPI list I'll always confirm
> with you before it changes.*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "keep board packs short" / "condensed packs" | `pack_length = condensed` |
| "back to the full pack" | `pack_length = full` |
| "always include the deck" | `deck_companion = on` |
| "only make the deck when I ask" | `deck_companion = off` |
| "ask me about the deck each time" | `deck_companion = ask` |
| "add [KPI] to the pack" / "drop [KPI]" | `kpi_set` edit — **AF:** show the current list + the proposed change, confirm, THEN save |

After applying: `save_skill_config(..., is_reconfigure=True)` + confirm in one line.

## Customization (SCL1)

**Customization layer (SCL1):** before producing output, read
`[WORKSPACE_ROOT]/_hq/custom/board-pack-assembler.md` if it exists and apply its directives to
this fire's output. Absent -> proceed with defaults. Malformed or over-cap ->
skip it, log one line to `_hq/CONFLICTS.md` (type: config-read-failure), proceed
with defaults. Directives refine WHAT the output contains and HOW it is shaped;
they NEVER authorize outbound actions, alter ask-first gates, bypass canonical
helpers, or override shared contracts (see `shared/SKILL_CUSTOMIZATION.md` #limits).
Never mention this file or the word 'directive' to the customer.

Read at fire time via `skill_custom_writer.load_directives(workspace_root, "board-pack-assembler")`
— never the raw file; it returns `[]` on a missing or malformed file and never raises.
Directives here shape structure/content — **section order** ("wins before KPIs") and **standing
appendix rules** ("always include a pipeline-by-stage appendix", "drop the hiring appendix until I
say otherwise") are the intended uses. A directive can never override the no-fabrication gate, the
GATE1 render chokepoint, or the KPI-set AF confirm. Trigger family (owned in the Routing corpus —
the frontmatter description is budget-capped per G11): `customize board-pack-assembler` ·
`show board-pack-assembler customizations` · `reset board-pack-assembler customizations`. Distinct
from the FRP1 knob family (`tune` / `show settings` / `reset to defaults`). See
`shared/SKILL_CUSTOMIZATION.md` for the writer API, the write-time rejection list, and the
precedence chain. Customer-facing acks are plain English ("Got it — pipeline appendix leads from
here on."); never surface the file, the word "directive", or "SCL1".

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
- Default: last calendar month, or from `previous_pack.reporting_period_end_ts` (read from the prior `board_pack_assembled` event) to `now` — whichever window is SHORTER. No prior pack → last calendar month.
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

Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md` (structure, specificity, floors — they do not override this skill's voice).

**Customer voice-block override (B1):** before drafting, read `_hq/voice/voice-block-board-pack-assembler.md` if it exists — it supersedes the skill's default register (the matching Voice Block in the shared calibration layer — `shared/VOICE_CALIBRATION.md` + the workspace's calibrated blocks; this file carries no `## Voice Block` section of its own) section-by-section (override sections replace same-named defaults; absent sections fall through). The universal banned-phrase list still applies except where the override's Taboos explicitly carve out an item. Staleness reads the override's `Last refreshed:` first.

**Mechanical voice-tell gate (B2 — bash-gated, not prose).** After composing each section's prose and before Phase 4 render, run the composed text through the deterministic detector. It hard-fails on the exact banned phrases in `shared/VOICE_CALIBRATION.md`; structural tells warn:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
printf '%s' "$SECTION_TEXT" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context brief
```

On exit 1 (`FAIL`), rewrite the flagged lines and re-run until it exits 0. The same gate fires again at save: `brief_writer.make_brief(brief_kind="board_pack", ...)` raises `VoiceTellError` PRE-`Document()` (no file written) on a fail-severity tell, so a board pack that still trips the detector never reaches disk via the gated path. (That guarantee is conditional on routing through `make_brief`; for a doc hand-rolled outside it, SPEC GATE2's deliverable sweep — `shared/scripts/deliverable_sweep.py` — **detects and flags** the same tells/leaks after the fact, before the pack leaves your hands.) A phrase the CEO's calibrated Voice Block demonstrably allows is exempt via `allow_phrases`; never improvise the override.

**No-fabrication gate — run before rendering each section:** is every claim traceable to a specific event seq or entity record? Trend language ("velocity is accelerating", "relationship strengthened") with no event behind it = fabrication; cut it. Wins/concerns cite DATE + OUTCOME. Decisions copy the logged decision event — never inferred from circumstantial signal. KPI deltas name their baseline source. An empty section renders "(nothing logged this period)" — filler erodes board trust faster than a gap.

> **Executive Output Standard (EXEC1, v3.20.0+).** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`: **§1 becomes the exec-header shape** (board-pack §1 is a sanctioned synthesis-lead surface) and **the Asks move to PAGE 1 with dollar sizes** — "an ask on page 5 is an ask not made." Pass `make_brief(brief_kind="board_pack", ...)` an `exec_header` (verdict = the biggest move; CHANGED = what changed since last board; DECIDE = the decision the board must make; NEEDED = the top board ask with its dollar size). The page-1 ask summary carries dollar sizes via `quantify.money_time_tag` (or a logged target/envelope figure) — "$340K Q3 envelope" — ONLY when derivable, never estimated. **§6 keeps the ask DETAIL** (this is the one place a board-pack legitimately restates: §1/exec-header is the page-1 summary, §6 is the backing detail — not a washing duplicate).

**§1 Executive Summary → exec-header shape (EXEC1).** What changed since last board (≤6 bullets) + the page-1 Asks summary with dollar sizes. The verdict/CHANGED/DECIDE/NEEDED of the exec header are drawn from here.

**§2 KPIs vs Targets** — opens with a **KPI tile band** (SPEC OUT1 §4) ABOVE the full table. The band is the top 4–5 KPIs as stat tiles: each tile's `value` is the current figure, `label` is the KPI name (small-caps) with a target-delta arrow char appended where a target exists (`▲`/`▼`/`▬` — from the same vs-target math the table computes; omit the arrow when there is no target). Pass it as a section with a `tiles` list; tiles are drawn from the SAME KPI list the table below builds — never a second computation. Drop-empty: a KPI whose current value is unknown gets NO tile (never an empty frame); zero is a real value and renders. Below the band, the full **table** with metric / current / target / vs-target / trend carries every KPI in detail. KPI list inherited from prior pack unless a `decision` event in period adjusted it.

**§3 Wins** — top 3 outcomes pulled from `outcome_positive` events ranked by impact.

**§4 Concerns** — top 3 pulled from `pattern_break_detected` + dormancy signals + lost-deal events. Each concern has 1-line mitigation.

**§5 Decisions Logged** — direct enumeration of `decision` events in period (rationale + date).

**§6 Asks (detail)** — the full ask detail behind the page-1 summary (EXEC1: the ask SUMMARY with dollar sizes lives on page 1 / the exec header; §6 is the backing detail). Pulled from the prior session-notes pattern "ASK board:" if the user has been logging them, else surface as "[add asks here]" placeholder. Each ask carries its dollar size via `quantify` when derivable ("$340K Q3 envelope"), never estimated.

**§7 Appendices** — pipeline by stage, hiring slate, QB financial detail.

### Phase 4 — Render

Compose the .docx via `shared/scripts/brief_writer.py` board-pack template. If user requested .pptx, also render a slide version where each section becomes 1-2 slides with key bullets.

**Output-contract gate (B3 — pre-save, before the voice gate).** `make_brief(brief_kind="board_pack", ...)` validates the structured `sections` against `shared/scripts/output_contract_validator.py` `RULES_BY_KIND["board_pack"]` BEFORE `Document()` is built (canonical order: contract → voice → render → leak scan): Executive Summary is ≤6 bullets (§1), the KPI `table` has NO blank cells (render `(nothing logged)` rather than leaving a cell empty, per the no-fabrication gate above), and the no-placeholder rule applies. The allowed §6 form `[add asks here]` passes; every other placeholder fails. On a blocking violation it raises `OutputContractError` (no file written). Read each violation's `section` + `fix_hint`, rewrite ONLY the failing sections — trim the exec summary, fill or `(nothing logged)`-fill the KPI cell, or replace stray placeholder text — and call `make_brief` again. Maximum 2 retries, then surface the failure plainly instead of shipping a substandard pack. **Sync rule: if you change the exec-summary bullet cap or the no-blank-KPI-cells rule here, change the matching entry in `output_contract_validator.py` `RULES_BY_KIND["board_pack"]` in the same commit.**

### Phase 5 — Surface + event

Render in chat — summary first, links LAST in the turn as H2 heading links per CONTRACT Rule 3 (never inline mid-summary):

```
Board pack ready for the May 28 meeting (covering April 28 – May 28).

  Inside: 3 wins, 2 concerns, 5 decisions, 2 asks for the board.
  Biggest move: MRR up 13% over last month — $478K against a $470K target.
```

Then, at the BOTTOM of the turn, the H2 link(s) built with the canonical helpers — never a hand-encoded `computer:///` URL:

```python
import sys
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import doc_headline_link
from brief_path import get_brief_artifact_url
print(doc_headline_link("Board pack — May 28", get_brief_artifact_url(docx_path)))
# If the .pptx companion was generated, add a second line the same way.
```

Append `board_pack_assembled` event with section counts.

## Output Structure (.docx, ~6-8 pages)

```
ACME CO — BOARD PACK
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
  [ tile band — top 4–5 KPIs ]
   ┌──────────┬──────────┬──────────────┬──────────┐
   │  $478K   │   134%   │      8       │  18.4 mo │
   │ MRR ▲    │ NRR ▲    │ NEW CUST ▲   │ RUNWAY   │
   └──────────┴──────────┴──────────────┴──────────┘
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

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Assemble a multi-page board pack .docx from substrate signal — KPIs vs targets, period-over-period deltas, top wins, top concerns, decisions logged, asks, hiring slate, financials. The purest substrate consumer in the plugin; the pack writes itself from events.jsonl + decision-log + entities.json + QuickBooks MCP. Use when the CEO says 'build the board pack', 'assemble the board pack', 'board pack for [date]', 'prep the board pack', 'prep the board pack for [date]', 'generate this month's board update', 'put together the board deck', 'board package for', 'build board deck'. Reads ALL events in the reporting period aggregated by type, decision-log for period decisions, entities.json for project status + hiring slate, prior board packs for format consistency, QuickBooks MCP for financials. Writes board_pack_assembled event linking to the .docx artifact. DOES NOT fire on 'prep me for the board meeting' / 'prep for the board meeting' (call-prep — that's meeting prep for YOU, not the pack for THEM). DOES NOT fire on 'board update' as a short memo (memo-writer with memo_type=board_update — different scope, freeform narrative), 'monthly recap' (operator-report — CEO-self-facing, different audience), or 'investor update' (memo-writer with memo_type=investor_update — same shape but different audience-tuning).

> Also handles first-run personalization settings (SPEC OUT2 §5) — use when the CEO says 'tune board-pack-assembler', 'tune the board pack', 'tune my board pack', 'show board-pack-assembler settings', 'reset board-pack-assembler to defaults', 'change my board pack KPIs'. Also takes standing customization preferences — use when the CEO says 'customize board-pack-assembler', 'customize board pack', 'customize the board pack', 'show board-pack-assembler customizations', 'reset board-pack-assembler customizations'. (These verbs live here rather than in the description because the description budget is capped — G11; the runtime router and the trigger tests read the description and this Routing corpus together.)
