---
name: dormant-customer-scan
description: "Surface the customers who have gone quiet relative to their own historical cadence — before the CEO finds out from a revenue report. Fires on: 'which customers have gone dark', 'who went dark', 'dormant customer scan', 'who hasn't replied in a while', 'quiet customers', plus 'tune dormant-customer-scan'. Computes per-relationship cadence baselines in code, ranks by cooling severity with evidence, and offers one-tap re-engagement drafts; honors learned suppressions and per-person cadence overrides. Does NOT fire on 'who should I reach out to this week' (relationship-moves — the ranked action pack that CONSUMES this detection), 'warm threads to revive' (thread-resurrection — thread-level), or 'draft an email to [name]' (email-writer). Detection math and fences: Routing section in the body."
---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

If the scan is invoked with a name-bearing trigger ("dormant scan for [name's customers]"), you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` first to resolve the named scope. For the cadence-break computation, call `shared/scripts/cru_match.py::load_open_commitments` if you need open-commitment cross-reference — do NOT hand-roll an events.jsonl scan. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.1)

- **Use dormant-customer-scan for:** proactive detection of cadence breaks. Output is a ranked list of people/orgs the CEO should reach out to.
- **Reads from:** `_hq/data/events.jsonl` (canonical Tier 1 source per `references/SOURCE_OF_TRUTH.md` — compute cadence directly from interaction/meeting events per person via `event_references_person` from `cru_match.py`, NOT from the `_hq/views/RELATIONSHIPS.md` projection, which is regenerated lazily by insight-generator and may be stale) + `_hq/data/entities.json` for people/org records + Gmail/Slack for recency confirmation.
- **Does NOT draft outbound messages itself** — it surfaces the list; the top-3 `draft re-engagement` widget taps (EXEC1) dispatch through apply-choices to `email-writer` / `follow-up-ritual`, which do the drafting (lazy — nothing exists in Gmail until a further click there). If the CEO says "scan for dormant customers and draft re-engagement emails," this skill produces the list, then hands off the same way.

## Writer Contract

This skill reads Gmail and Slack during scans to confirm recency. Every new inbound from a flagged dormant thread emits an `interaction` event tagged as a re-activation signal per `shared/PASSIVE_CAPTURE.md`. No raw message text is persisted — summary + source_ref only.

---

# Dormant Customer Scan

**For:** CEOs with a portfolio of recurring customers who know some have gone quiet but can't keep it in their head. Productizes the "aha" moment Bo hit when Intel surfaced Acme Co.

## What It Does

Scan CRM, email history, and meeting notes for customers whose cadence has broken relative to their own historical pattern. Produce a ranked list with enough context that the CEO can pick up the phone or send a note today.

Each dormant customer gets: last-touch date, cadence baseline, gap vs baseline, historical revenue, last interaction summary, suggested re-engagement angle. One markdown file. Scannable in under 2 minutes.

Runs on demand. Also schedulable weekly as a recurring surface — the hook line for the Beta Tier retainer is "every week Claude tells you who's gone dark."

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). All
three decisions are **show-then-tune (STT)** — the scan runs first, then one-tap changes are
offered. Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "threshold": "2x_30d",      # 2x_30d (2x cadence AND 30-day floor) | 1.5x | 3x
    "revenue_weighting": True,  # weight the ranking by historical revenue
    "watch_list": True,         # keep a persistent watch-list across scans
}
cfg = get_config(workspace_root, "dormant-customer-scan", DEFAULTS)
```

`threshold` sets the cadence-break sensitivity. `revenue_weighting` weights the ranked list by
historical revenue when True. `watch_list` keeps a persistent across-scan watch-list when True.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "who went dark", "dormant customer scan" | run the scan with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "dormant-customer-scan", DEFAULTS)` BEFORE rendering, then append the first-run footer. |
| **Show settings** | "show dormant-customer-scan settings" | render current config in plain English; no scan. |
| **Tune** | "tune dormant-customer-scan" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-run scan. |
| **Reset** | "reset dormant-customer-scan to defaults" | `wipe_skill_config(workspace_root, "dormant-customer-scan")` → next fire is a first-fire again. |

**The first-run block (footer of the chat surface — the widget carries the top-3 ask block per EXEC1 below; this settings footer is plain chat text under it):**

> *First time scanning for dormancy. I set 3 defaults: **I flag a customer once they've been
> quiet twice as long as their usual gap (and at least 30 days)** · **ranked by revenue** ·
> **I keep a watch-list between scans**. Say "tune the dormant scan" to change any, or just
> tell me ("be more sensitive" / "don't weight by revenue").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "be more sensitive" / "flag them sooner" | `threshold = 1.5x` |
| "be less sensitive" / "only the really cold ones" | `threshold = 3x` |
| "don't weight by revenue" | `revenue_weighting = False` |
| "rank by revenue again" | `revenue_weighting = True` |
| "turn off the watch-list" | `watch_list = False` |
| "keep a watch-list" | `watch_list = True` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-run scan + confirm in one line.

## How to Use

```
"Who went dark?"
"Dormant customer scan"
"Scan for dormant customers"
"Who haven't I heard from?"
"Quiet customers"
"Customer dormancy check"
"Customers who stopped responding"
```

Optional scope modifiers the user might pass:
- "last 60 days" / "last quarter" — override the default dormancy window
- "top 20 customers" — limit to revenue tier
- "excluding [customer]" — if there's a reason one is intentionally quiet

## How It Works

### MUST-language preamble (v3.13.7+ — live-check enforcement gate)

Before surfacing ANY dormancy flag — whether to the .docx report or the chat summary — you MUST overlay live Gmail + Calendar signals on the substrate cadence math via the shared canonical helper.

> **No customer may be flagged dormant from substrate-only data. You MUST call `shared/scripts/live_contact_check.py::live_contact_check()` for every flagged candidate and respect its merged `last_contact_iso`. If the live check shows a recent touch the substrate missed, the customer is NOT dormant — drop the flag.**

**REL1 — emit the normalized dormancy signal (AFTER the live-check gate).** For every candidate that passes the live check and stays dormant, also call `shared/scripts/dormancy.py::emit_dormancy_signal(workspace_root, entity_id=<org/person id>, entity_type='org' or 'person', gap_days=<current gap>, baseline_days=<12-month median gap>, source_skill='dormant-customer-scan')`. The ranked .docx report is unchanged — this is an ADDITIVE shared signal so relationship-moves reads one normalized dormancy story per relationship.

This closes Bug #28 from Session-22 testing: Northstar Partners/Lyra was flagged as 44-days-dormant from substrate, while a Calendar event 31 days ago existed but had never been written to events.jsonl. Real CEO-trust miss ("you told me to chase someone I literally met with"). The helper unifies dormant-customer-scan with Pulse (Bug #5) — same call site, same merge math.

If the live-check helper isn't available (sandbox / connector failure), you still must NOT silent-fall-through to substrate-only flags. Surface the gap honestly in the report ("I couldn't check live email/calendar just now, so these flags are from saved history only — double-check before reaching out").

1. **Locate the customer list.**
   - Priority: CRM connector (HubSpot/Salesforce if installed) > `_hq/CUSTOMERS.md` > **entities.json fallback (v3.13.6+)** > email thread analysis.
   - **entities.json fallback:** if no CRM and no `_hq/CUSTOMERS.md`, fall back to `_hq/data/entities.json` orgs where `relationship_type ∈ {client, prospect, portfolio_company}`. Treat each as a customer for the dormancy scan. The user's onboarded org graph IS the customer list when no explicit one exists. Pre-v3.13.6 this fallback was missing — workspaces with fully-modeled org records still got the "I don't have a customer list" prompt.
   - If still no customer list (no CRM, no `_hq/CUSTOMERS.md`, and entities.json has zero customer-typed orgs), ask: "I don't have a customer list to work from yet. Point me at one — your CRM, a customer list file, or a spreadsheet — and I'll scan from there."
2. **Build the cadence baseline** for each customer.
   - For each customer: find all historical touchpoints (emails sent/received, meetings held, Slack threads, deliverables shipped)
   - Compute their typical inter-touch gap (median days between interactions over the last 12 months)
   - A customer with a 14-day baseline who hasn't been touched in 45 days is more dormant than one with a 90-day baseline at day 100
3. **Live-check overlay (v3.13.7+ — required before any flag).** For every candidate that the cadence math would flag, run `shared/scripts/live_contact_check.py::live_contact_check()`:

   ```bash
   SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
   PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
   cd "$PLUGIN_ROOT"
   python3 -c "
   import sys
   sys.path.insert(0, 'shared/scripts')
   from live_contact_check import discover_live_check_tools, live_contact_check
   # Once per fire — resolve Gmail + Calendar tool IDs
   lookup = discover_live_check_tools(available_tools)
   # Per candidate — after invoking those MCP tools to fetch latest Gmail + Calendar touchpoints
   result = live_contact_check(
       workspace_root='\$WORKSPACE',
       person_id=primary_contact_person_id,   # or representative person on the customer org
       external_signals={
           'gmail_last_iso':    gmail_iso_or_none,
           'gmail_detail':      {'subject': '...', 'thread_url': '...'},
           'calendar_last_iso': calendar_iso_or_none,
           'calendar_detail':   {'title': '...', 'event_url': '...'},
       },
       window_days=baseline_days * 2,
   )
   # If result['last_contact_iso'] is more recent than your substrate-only date,
   # DROP THE FLAG. The customer is not dormant.
   "
   ```

   - For org-level candidates with multiple known contacts, run the helper for each primary contact and take the freshest result. Substrate is the org's last_interaction; live signals are per-person on the org. Most-recent across the org wins.
   - Record per-candidate which sources_failed; surface in the report so the user sees partial-signal vs full-signal flags.

4. **Flag dormancy.**
   - Default threshold: current gap > 2x baseline AND current gap > 30 days
   - If user passed an override ("last 60 days"), use that instead
   - **The gap is computed from the live-check-overlay date (Step 3), NOT from substrate alone.**
5. **Enrich each flagged customer.**
   - Last-touch date + last-touch summary (from Granola or email subject line)
   - Historical revenue (from CRM or `_hq/CUSTOMERS.md` if annotated)
   - Last deliverable or proposal sent
   - Suggested re-engagement angle — pull from: known recent news (web search their company), last unresolved thread ("you owed them a pricing answer in February"), anniversary triggers, or a generic check-in if nothing specific
6. **Rank.**
   - Sort by (historical revenue × dormancy score), descending
   - Top 20 in the primary list, rest in an appendix
7. **Write the report.**
   - Save to `_hq/dormant/DORMANT_SCAN_[YYYY-MM-DD].docx` (per CONTRACT Rule 27 — no .md deliverables). Route through `shared/scripts/brief_writer.py` for layout consistency.
   - Return file link + 1-line headline ("12 customers have gone quiet. Top 3 by revenue: Acme Co ($240K, last touch 89 days ago), Northstar Partners ($180K, 67 days), Acme Logistics ($120K, 52 days).")
8. **Write the scan receipt (v4.5.2 R1 — REQUIRED, every run).** The dogfood found this scan surfacing 4 dormant accounts and leaving zero substrate trace (FINDINGS F-57) — the next scan couldn't dedup its own nags and value receipts couldn't count the work. One line via the canonical helper: `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "dormant-scan", fired_via="manual", surfaced=n_flagged, extra_data={"flagged_entity_ids": [...], "live_check_dropped": n_dropped})` — `"scheduled"` for fired_via when configured as a recurring scan. The `flagged_entity_ids` list is what the next scan reads to avoid re-nagging.

## Executive Output Standard (EXEC1, v3.20.0+)

Inherits `shared/EXECUTIVE_OUTPUT_STANDARD.md`. Pass `make_brief(brief_kind="dormant_scan", ...)` an `exec_header`:
- **verdict** = the headline total at stake: *"$540K of historical revenue has gone quiet across 12 customers."* (only the dollar part that quantify can derive — never an estimate; if no revenue is annotated, verdict states the count + cadence, no fabricated dollar).
- **changed / decide / needs** = what shifted since the last scan · the one customer worth calling today · the one-tap re-engagement to approve.
- **Subsumes** the old prose `## Summary` line ("Looked across [N]…") — that line is REPLACED by the exec header, not added on top (net length must not increase).

**Header tile band (SPEC OUT1 §4):** the FIRST section of the .docx (immediately under the exec header) is a stat-tile band — pass it as a section with a `tiles` list: **$ at stake** (sum of the annotated/quantify-derived revenue across the ranked customers) · **dormant** (count of ranked customers) · **trending quieter** (count whose gap is widening but not yet over threshold). Values come from the SAME computation that builds the ranked list — never a second pass, never a prose re-count. Drop-empty (F-60): if no revenue is annotated anywhere, DROP the "$ at stake" tile (never a $0 or estimated frame); the dormant-count tile always has data. A tile whose datum is genuinely unknown is omitted; a real zero renders.

**Ranked-report layout (SPEC OUT2 §4 — this scan is one of the four ranked-report surfaces; contract in `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The ranked report").** This skill ALREADY carries the contract's pieces — align, don't duplicate: the tile band above IS the contract's tile summary band (item 1); each ranked customer entry maps to the scored row (item 2) as rank (list position) · name (customer + org) · quantify tag (historical revenue + gap vs baseline, substrate-derived only) · why-now (the suggested re-engagement angle citing real evidence) · action (draft re-engagement). Nothing new to render — this paragraph exists so the four ranked-report skills read as one system.

**Top-3 one-tap (element 4 ASK block / one-ask-surface):** the top-3 dormant customers each get a one-tap `draft re-engagement` action (already in CANONICAL_ACTIONS; draft-never-send preserved per the Writer Contract). On the widget surface the widget IS the ask block — no prose twin.

**Quantify (element 3):** per-item dollar tags come from `quantify.money_time_tag` (or the annotated `_hq/CUSTOMERS.md` revenue), never an estimate.

**Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("dormant_scan", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions (the ranked-report contract above stays authoritative; the exemplar anchors layout within it). Workspace exemplar (`_hq/exemplars/dormant_scan/`) beats the shipped seed; `None` = compose on the layout above, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header or any gate, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the scan. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered scan ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="dormant_scan", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

Checklist (binary): header concrete-or-nothing · quantify tag only when non-None · top-3 asks one-tap, reader-actionable, one-surface.

**Visual pass (SPEC OUT2 §3, after the .docx save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 6-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "Live lookup unavailable — flags below are substrate-only."
- Good: "I couldn't check live email/calendar just now, so these flags are from saved history only — double-check before reaching out."

## Output

**Deliverable link (CONTRACT Rule 3 — H2 heading link, LAST in the turn):** surface the .docx via `chat_output_renderer.doc_headline_link(label, brief_path.get_brief_artifact_url(absolute_path))` as the final line of the chat response — after the widget/summary and Sources, never interspliced mid-body, never a plain-text path, never a hand-built `computer://` URL. Structure

```
# Customers worth reaching out to — [YYYY-MM-DD]

[Exec header (EXEC1) replaces the former "## Summary" line:]
**[$X of historical revenue has gone quiet across N customers.]**
CHANGED   [movement since last scan]
DECIDE    [the one to call today, or "Nothing — list is current."]
NEEDED    [approve the top re-engagement draft below, or "Nothing from you."]

## Top 20 to reach out to

### 1. Acme Co — $240K historical — last touch 89 days ago (their usual: every 21 days)
Last touch: Feb 1 — "Pricing discussion — awaiting your response"
Last deliverable: Q4 renewal proposal (sent Jan 15, no reply)
Suggested angle: You owe them a pricing answer. News: they just announced a funding round Mar 12.
Contact: Aria Sample, aria@example.com, CEO

### 2. [next customer]
...

## Others worth a look
[Compact table of the rest: Name | Revenue | Days since last touch | Their usual cadence]

## Trending quieter — worth watching
[Customers near the threshold — 1.5x their usual cadence — useful early warning]
```

## How M / The CEO Uses It

- **Daily operation:** Run weekly (scheduled). Open the file Monday morning. Work the top 3-5 before lunch.
- **Demo beat:** Live in front of the CEO group — "who went dark" → list appears → "this one's worth $240K and they're 89 days late on a pricing reply" → room reacts. Universal pain, immediate value.
- **Retainer ritual:** Monthly call touchpoint — "let's walk your dormant list." Turns a retainer into a revenue conversation.

## Triggers

- "who went dark"
- "dormant customer scan"
- "scan for dormant customers"
- "customers who stopped responding"
- "who haven't I heard from"
- "quiet customers"
- "customer dormancy check"
- "customer re-engagement scan"

## Gotchas

- **Baseline matters more than absolute days.** A 45-day gap is nothing for an annual contract customer. Always compute per-customer baseline.
- **If a customer is new (<90 days of history), skip baseline and flag only if zero touches in 30+ days.**
- **Don't flag customers the user has explicitly paused.** Honor any "excluding [customer]" in the prompt or a `_hq/CUSTOMERS.md` "paused: true" annotation.
- **Suggested re-engagement angles must be specific** when evidence exists. "You owe them a pricing answer" beats "reach out to check in."
- **If no CRM connector and no `_hq/CUSTOMERS.md`, offer to scaffold a customer list** from email/meeting patterns as a one-time onboarding, rather than silently failing.
- **Privacy:** no content from customer emails in the report. Only metadata (dates, subjects, counts) + whatever the user has already annotated in their own files.
- **Don't email them automatically.** Ever. Surface, rank, explain — human decides.

## Scheduling

After first successful run, offer: "Want me to run this every Monday morning and drop it in your briefing?" — use the `schedule` skill to register a weekly task.

## Reliability

When configured as a recurring scan, this skill implements `shared/RELIABILITY.md`. Key rules: skip-not-fail when no CRM connector AND no `_hq/CUSTOMERS.md` exist (log to `_hq/logs/scheduled-task-skips.log`, exit clean — never invent a customer list), OOO defers the scan, 60s aggregate scan budget across Gmail / CRM / Granola / Slack with graceful degradation, last-known-good cache at `_hq/caches/dormant-customer-scan-last-good.json` when a connector fails, dedup via `source_ref` hash makes re-running idempotent. If the .docx render fails, deliver the ranked summary INLINE in the chat turn (top 5, same content) and say the report file couldn't be written — never fall back to a `.md` file (CONTRACT Rule 27) and never write outside `_hq/dormant/`.

## What It Doesn't Do

- Doesn't draft re-engagement emails in its own path — the widget's `draft re-engagement` tap hands off to `email-writer` / `follow-up-ritual` (use `one-pager-composer` for a talking-points brief instead of an email)
- Doesn't update CRM records
- Doesn't auto-contact customers
- Doesn't analyze email content — only metadata

## Connected Tools

- **CRM connector** (HubSpot / Salesforce / Pipedrive when available)
- **Gmail** — touchpoint history
- **Granola MCP** — meeting history
- **Slack** — thread activity
- **Web search** — news / funding / anniversary triggers for re-engagement angles
- **_hq/CUSTOMERS.md** — local customer list + annotations
- **schedule skill** — weekly recurring run

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Surface the customers who've gone quiet relative to their own historical cadence — before the CEO finds out from a revenue report. Scans CRM, email, and meeting notes for cadence breaks, produces a ranked list with last-touch date, gap vs baseline, historical revenue, and a suggested re-engagement angle for each. Use when the CEO says 'who went dark', 'gone dark', 'customers have gone dark', 'which customers have gone dark', 'dormant customer scan', 'dormant customer', 'who haven't I heard from', 'who hasn't replied', 'hasn't replied in a while', 'quiet customers', 'customer dormancy check', 'customers who stopped responding'. Runs on-demand or as a scheduled Monday task. Also handles first-run personalization settings — use when the CEO says 'tune the dormant scan', 'tune dormant scan', 'tune dormant-customer-scan', 'show dormant scan settings', 'show dormant-customer-scan settings', 'reset dormant scan to defaults', 'reset dormant-customer-scan to defaults'. DOES NOT fire on 'follow up with [customer]' (email-writer — plain outbound draft; 'follow up on that call' is follow-up-ritual) or 'show me my customer list' (that's a tracker/RELATIONSHIPS.md read, not a scan). DOES NOT fire on 'who should I reach out to' / 'relationship moves' / 'weekly outreach' (that's relationship-moves — the ranked, pre-drafted action pack; this skill is the raw detection report it consumes).
