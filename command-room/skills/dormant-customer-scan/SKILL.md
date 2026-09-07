---
name: dormant-customer-scan
surfaces: both
description: "Surface the customers who have gone quiet relative to their own historical cadence — before the CEO finds out from a revenue report. Fires on: 'which customers have gone dark', 'who went dark', 'dormant customer scan', 'who hasn't replied in a while', 'quiet customers', plus 'tune dormant-customer-scan'. Threads lens (formerly thread-resurrection): 'warm threads to revive', 'what conversations went quiet', 'threads I dropped', 'thread resurrection'. Computes per-relationship cadence baselines in code, ranks by cooling severity with evidence, and offers one-tap re-engagement drafts; honors learned suppressions and per-person cadence overrides. Does NOT fire on 'who should I reach out to this week' (relationship-moves — the ranked action pack that CONSUMES this detection), 'draft an email to [name]' (email-writer), or 'balance check' / 'my white space' (balance — personal ties are never customers; this scan skips them at its emit gate)."
---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

If the scan is invoked with a name-bearing trigger ("dormant scan for [name's customers]"), you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` first to resolve the named scope. For the cadence-break computation, call `shared/scripts/cru_match.py::load_open_commitments` if you need open-commitment cross-reference — do NOT hand-roll an events.jsonl scan — and keep the confirmed half via `cru_match.split_pending_review(...)` (INTAKE: an unconfirmed extraction must not push a customer up or down the cadence ranking). See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.1)

- **Use dormant-customer-scan for:** proactive detection of cadence breaks — two lenses, one skill. The **people lens** (this scan) ranks people/orgs the CEO should reach out to; the **threads lens** (`## Mode: threads`, formerly `thread-resurrection`) finds specific conversations that died mid-step. A person can be active while a thread died, and vice versa.
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
# PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; then run python FROM $PLUGIN_ROOT:
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

> **MAILTRUST1 (2026-07-29): the live check is still a single read, and dormancy is a NEGATIVE claim.** A full-content per-thread fetch came back one message short on 2026-07-29 — any one read proves presence, never absence. Before a dormancy flag stands, corroborate the mail side with a second, differently-shaped query (a broad all-mail recency sweep — never another sender-scoped search; the sender-scoped shape went stale in the same incident), via `shared/scripts/mail_absence.py::corroborate_absence` where thread-scoped. If the two reads disagree, the customer is not flagged — report that the reads disagreed instead.

**REL1 — emit the normalized dormancy signal (AFTER the live-check gate).** For every candidate that passes the live check and stays dormant, also call `shared/scripts/dormancy.py::emit_dormancy_signal(workspace_root, entity_id=<org/person id>, entity_type='org' or 'person', gap_days=<current gap>, baseline_days=<12-month median gap>, source_skill='dormant-customer-scan')`. The ranked .docx report is unchanged — this is an ADDITIVE shared signal so relationship-moves reads one normalized dormancy story per relationship. **BAL1 D1.1(3) — personal-tie skip at this emit gate:** never emit for (or report on) a PERSON whose record carries `tie: "personal"` — personal ties are the Balance surface's lane, never a customer signal. Absent `tie` = work (back-compat); only the explicit `personal` value skips. **ORGSCHEMA1 — account-owner skip at the same gate:** never emit for (or report on) an org whose `account_owner` is set to a DIFFERENT operator than this workspace's primary user (`org_writer.outreach_eligible(org, user_id)` is the canonical check) — those are someone else's accounts to chase, and ranking them produces confidently wrong outreach in any multi-operator workspace. People attached to such an org skip too. Absent `account_owner` = unclaimed, ranks normally.

This closes Bug #28 from Session-22 testing: Northstar Partners/Lyra was flagged as 44-days-dormant from substrate, while a Calendar event 31 days ago existed but had never been written to events.jsonl. Real CEO-trust miss ("you told me to chase someone I literally met with"). The helper unified dormant-customer-scan with the Pulse chat's own dormancy scan (Bug #5) — same call site, same merge math. LIFECYCLE1 retired that chat, so this skill is now the primary person-dormancy detector; the shared helper and its MUST-language are unchanged and every future detector still routes through them.

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
   PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
   cd "$PLUGIN_ROOT"
   python3 -c "
   import sys
   sys.path.insert(0, 'shared/scripts')
   from live_contact_check import discover_live_check_tools, live_contact_check
   # Once per fire — resolve the mail + Calendar tool IDs. Pass workspace_root:
   # it is how the DECLARED mail backend reaches the seam (MAILSEAM2). Without
   # it, a workspace with two mail connectors live-checks the wrong inbox and
   # every candidate reads as dormant.
   lookup = discover_live_check_tools(available_tools, workspace_root='\$WORKSPACE')
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
   - Save to `_hq/dormant/DORMANT_SCAN_[YYYY-MM-DD].docx` (per CONTRACT Rule 27 — no .md deliverables). Route through `shared/scripts/brief_writer.py` for layout consistency — that route is mandatory, not a preference (DOCFENCE1):
     - **NEVER hand-roll the report** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or PII-leaking scan (the v3.20.0 failure mode) — and this report is a list of named customers with revenue attached, which is exactly what the leak scan exists to catch.
     - **NEVER create, render, copy, upload, or update the report — or any part, derivative, or restatement of it ("the top ten", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/dormant/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so sales can work the list", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the dormant list in a Google Sheet" is a request this gate refuses, not an override. Hand back the canonical file's link.
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

**Visual pass (SPEC OUT2 §3, after the .docx save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

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

## Mode: threads — "what conversations went quiet" (formerly thread-resurrection)

The second lens of the same scan. The people lens above asks *which person has gone quiet vs their cadence*; this lens asks *which specific live conversation died mid-step*. It was its own skill (`thread-resurrection`) until SKILLMERGE1 (2026-09-03); every phrase it answered routes here, the `dormancy_signal` events it emits keep `source_skill: thread-resurrection` (the persisted vocabulary stays canonical — see `source_skill_compat.FOLDED_SKILL_ALIASES`), and its widget, verbs and writer contract are unchanged. Fires on: 'what conversations went quiet', 'warm threads to revive', 'dead threads worth reviving', 'threads I dropped', 'thread resurrection', 'what conversations went silent', 'threads to revive', 'conversations to restart', 'find warm conversations'.

### Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

Before resolving any person / org / project from loose input in your trigger phrase or arguments, you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)`. Only after the resolver returns NO candidates may you fall back to substring grep — and that fallback MUST be flagged to the user, not silently surfaced as a single result. For commitment / event surface, call `shared/scripts/cru_match.py::load_open_commitments` — do NOT hand-roll an `events.jsonl` scan — passing the org-scoped rows: `load_open_commitments(events_path, events=org_events)` with `org_events` from `events_io.load_events_org_scoped` (PGUARD2 D2), then keep the confirmed half via `cru_match.split_pending_review(...)` (INTAKE — see the Reads bullet below). See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract + rationale (the bug class this closes).

### Lens boundary

- **Use the threads lens for:** finding specific high-context THREADS (email or Slack) that died mid-discussion. The unit of analysis is the thread.
- **Use the people lens (the scan above) for:** finding PEOPLE (or customer orgs) that have gone quiet vs their historical cadence. Different unit of analysis, same skill.
- **Use `follow-up-ritual` for:** post-meeting follow-up after a recent meeting. This skill is for surfacing the old threads that never got followed up on.
- **Use `email-writer` directly** for drafting a one-off email to someone (no thread context).

### Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `thread_resurrected` when the user clicks "Draft revival" and the draft is created. Carries `{thread_ref, last_msg_ts, days_silent, revival_draft_event_seq, resurrection_hook}`. The `revival_draft_event_seq` points at the `email_drafted` event the chained `email-writer` invocation produced.
- `_hq/data/entities.json` — `relationship.last_touched_at` bumped on the counterparty record(s) when the revival email is sent.

**Reads from:** All `events.jsonl` reads come from ONE org-scoped load — **read via the org-scoped reader, never a raw load** (PGUARD2 — this skill selects threads and feeds revival drafts; a masked account's thread must never be resurrected): `from events_io import load_events_org_scoped; org_events, skipped = load_events_org_scoped(workspace_root)`, then filter by `type` at the call site. The reader applies the account-scope mask and drops personal-lane rows by design.
- `_hq/data/events.jsonl` — `type == "interaction"` events (the thread activity log, from the org-scoped load) to compute per-thread last-activity and days-silent.
- `_hq/data/events.jsonl` — `type == "meeting"` events (from the org-scoped load) to find meetings whose follow-up emails were never sent (no subsequent `interaction` event with `direction == "outbound"` from the host to attendees in the 7 days after the meeting).
- `_hq/data/entities.json` — for the thread → person → org → project graph. "High-context" means: thread touches an active project OR a high-value person (relationship strength tier 1-2) OR an explicit commitment.
- `_hq/data/events.jsonl` — `type == "commitment"` events with `status == "open"` involving thread participants — via the seam, `load_open_commitments(events_path, events=org_events)` (PGUARD2 D2 — never the no-arg owner form here), confirmed half via `cru_match.split_pending_review(...)` — these get surfaced as "use commitment chase instead" alternative actions. **INTAKE: an unconfirmed extraction can never be that alternative** — the chase path already refuses pending rows in code (`_is_pending_review`), so offering one here suggests an action the system will decline.
- `_hq/data/events.jsonl` — prior `thread_resurrected` events (from the org-scoped load) to avoid re-surfacing recently revived threads.

**Conflict boundary:** sole writer of `thread_resurrected` events. The chained `email-writer` invocation writes the `email_drafted` event.

---

### What the threads lens does

The people lens above finds dormant PEOPLE (relationship cadence breaks). This lens finds dormant THREADS — specific conversations that died mid-step, where reviving the thread is a different action than reviving the relationship.

### What It Does

Scans interaction events to identify threads where (a) the thread has substantive context (multi-turn, project-tagged or commitment-tied), (b) the thread has been silent for 14+ days, and (c) the silence isn't an intentional resolution (no `thread_resolved` event on the thread_ref).

For each candidate thread, surfaces:
- Last message text excerpt + sender
- Attendees + days silent
- Why this thread matters (project, open commitment, high-value person)
- Suggested revival hook tuned to thread context

Ranks by score (commitment-bearing > project-tagged > high-value-person > generic-context). Top 5 in a widget with per-thread action set.

### How to Use

```
"what conversations went silent"
"warm threads to revive"
"thread resurrection"
"threads to revive"
"conversations to restart"
"what's gone quiet that I should restart"
```

Runs on-demand. Schedulable weekly through `change-schedule`.

### How It Works

#### Phase 1 — Load candidate threads

Read `_hq/data/events.jsonl` for `type == "interaction"` events — from the Reads section's org-scoped load (`load_events_org_scoped`), never a raw read; a masked account's threads never enter the candidate pool. Group by thread (Gmail thread-id, Slack thread-ts, or meeting follow-up cluster). For each thread:
- `last_activity_ts` = max(ts of events in thread)
- `days_silent = (now - last_activity_ts).days`
- `direction_of_last_msg` = direction of the latest interaction event
- `attendees` = union of person_ids across events in thread

Filter to `days_silent >= 14` AND no `thread_resolved` event on the thread_ref.

**Live-check gate (MANDATORY — defined here; REL1 below assumes it).** For each surviving thread, call `shared/scripts/live_contact_check.py::live_contact_check()` on the thread's counterparty (same helper and MUST-language as dormant-customer-scan): overlay live Gmail + Calendar signal on the substrate math BEFORE surfacing. If the live check finds a touch newer than `last_activity_ts` (a reply the substrate missed, a meeting on the calendar), the thread is not silent — DROP it and emit no dormancy signal. Substrate-only resurrection pitches for threads that already resumed are the failure mode this gate closes.

**MAILTRUST1 — "went silent" is a negative claim; the live check is still one read.** On 2026-07-29 a full-content thread-fetch was itself one message short, so a clean live check does not prove silence. For any EMAIL thread about to surface as a resurrection candidate, corroborate with a broad recency sweep of a different shape (an N-day recency window scoped across ALL mail, never a sender-scoped search) via `shared/scripts/mail_absence.py::corroborate_absence(thread_fetch, sweep, thread_id=...)`. On `corroborated: False`, do not pitch the thread as silent — either drop it or surface it flagged "my mail reads disagreed on this thread". Slack threads and meeting clusters have no second mail shape; they keep the plain live-check gate.

**REL1 — emit the normalized dormancy signal (absolute tier).** For each thread that passes this filter (and its live-check), call `shared/scripts/dormancy.py::emit_dormancy_signal(workspace_root, entity_id=<thread_ref>, entity_type='thread', gap_days=<days_silent>, baseline_days=None, source_skill='thread-resurrection')` — null baseline maps to the absolute 14/30/60-day tiers. The legacy `days_silent >= 14` filter is unchanged.

#### Phase 2 — Score for high-context

For each candidate:
- `commitment_score` = +3 if any open commitment event references thread attendees AND mentions thread topic
- `project_score` = +2 if thread is tagged to an active project
- `relationship_score` = +(3 - relationship_tier) — tier 1 person = +2, tier 2 = +1, tier 3+ = 0
- `multi_turn_score` = +1 per turn beyond the third (caps at +3)
- `direction_score` = +1 if the last message was FROM the counterparty (you're the one who hasn't replied; you have leverage to revive)

`total = commitment + project + relationship + multi_turn + direction`

Threshold: `total >= 4` to enter the surface set. Top 5 by score.

#### Phase 3 — Build revival hooks

For each surfaced thread, compose a revival hook tuned to thread context. Every hook is SENDABLE text — a message the user could fire as-is, never a strategy note ("nudge them with X" is a note, not a hook). Hooks must also pass the voice-tell gate — "circle back" / "touching base" are banned phrases:
- Open-commitment thread: "Where did we land on [commitment topic]? Any movement?"
- Project-tagged thread: "Where did we land on [topic]?"
- High-value person thread: "Following up on our [date] conversation — [last-message-paraphrase]"
- Multi-turn died-mid-discussion: "Coming back to your [date] note on [topic] — [reframe]"

#### Phase 4 — Render widget (v3.13.2+ — canonical action widget per CONTRACT Rule 5)

Surface results via `render_chat_output_widget`. Each candidate thread renders as one item with the canonical action set: `draft / resolved / skip` (MLK1 retired `add to my list` — no row emits it). No bracket-style display labels — those crash the renderer's canonical-action validator. Conditional routing for commitment-bearing threads is handled INSIDE the `draft` action's apply-choices dispatch (see "Action semantics" below) — not as a separate per-item verb.

**Email-draft protocol compliance (v3.13.0+ universal scope per `shared/EMAIL_DRAFT_PROTOCOL.md`):** thread-resurrection follows the protocol VIA the chained `email-writer` invocation. The Phase 4 widget surfaces thread-selection (not the email body), so it doesn't directly emit email-shaped metadata. When the user clicks `draft` on a thread item, apply-choices chains to email-writer, which emits the actual email widget per the protocol §3a/§3c (lazy creation, native Gmail / Zapier-threaded send). Both this widget and email-writer's downstream widget meet CONTRACT Rule 5 + the protocol's lazy-creation rules.

**Data view shape (multi-item `all_batch_widget`):**

```python
items = []
for i, thread in enumerate(candidate_threads, start=1):
    counterparty_label = thread["counterparty_display_name"]
    title_clause = thread["thread_topic"]  # e.g., "partnership exploration"
    days_silent = thread["days_silent"]

    # Per-thread context lines (rendered as body_lines)
    body_lines = [
        f"Last from {thread['last_sender_short']}: \"{thread['last_excerpt']}\"",
        f"Last from you: {thread['your_last_topic_summary']}",
        f"Suggested opener: {thread['revival_hook']}",
    ]
    if thread.get("open_commitment"):
        body_lines.append(f"{thread['debtor_short']} still owes you: {thread['open_commitment']['title']} ({thread['open_commitment']['days_past']} days past)")

    items.append({
        "n": i,
        "icon": "💬",
        "name": f"{counterparty_label} — {title_clause}",
        # PROVENANCE — this skill WROTE that `name` (a label, an em-dash and a
        # clause it assembled), so it says so. `name` is normally the user's
        # own words and the renderer exempts it from the internal-vocabulary
        # scan on that basis; a driver template sitting in it must keep facing
        # the full scan, or this skill's own prose gets a silent carve-out.
        "composed_fields": ["name"],
        "context_tag": f"quiet for {days_silent} days",
        "body_lines": body_lines,
        "actions": [
            f"{i} draft",
            f"{i} resolved",
            f"{i} skip",
        ],
    })

data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"{len(items)} conversation{'s' if len(items)!=1 else ''} worth reviving",
    "sub_header": "Pick which conversations to revive — I'll draft each one.",
    "sections": [{"title": None, "count": None, "items": items}],
}
```

**Render + post (same pattern as email-writer Phase 4):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from widget_transport import render_and_persist
data_view = json.loads('''<DATA_VIEW_JSON>''')
transport = render_and_persist(data_view=data_view, wrapper='fragment',
                               persist_dir='<WORKSPACE>/_hq/.system/widgets',
                               name_hint='thread-resurrection')
print(transport['html'])
"
# Pass the rendered HTML (transport["html"]) to mcp__visualize__show_widget as widget_code (EW2+T, F-15 —
# shared/CHAT_ACTION_WIDGET.md § Transport). Never hand-compose or post-process the HTML.
```

**Action semantics** (per `apply-choices`):
- `draft` — kicks off the revival-email composition. Chains to `email-writer` with the thread context + revival hook pre-filled. The email-writer widget posts as the post-Apply surface so the user can edit/send/draft inline. Writes `thread_resurrected` event with `revival_draft_event_seq` pointing at the new `email_drafted` event. **For commitment-bearing threads** (where `open_commitment` was set in the data view), apply-choices routes through `cr-commitments`'s chase-email flow instead — same `draft` verb, conditional downstream. The decision rule is in the apply-choices handler, not the user-facing widget: the user just clicks `draft`, the system picks the right flow.
- `resolved` — writes a `thread_resolved` event. The thread is closed; no revival.
- `skip` — 24h dismissal (`chat_dismissal` event). Re-surfaces on the next thread-resurrection run after 24h.

**Why `draft` covers the commitment-chase case** (per CONTRACT Rule 5 — no improvised action verbs): both flows produce an email draft from the user's perspective. The internal routing (revival hook vs. chase prompt) is plumbing, not UX. Two verbs for the same user-facing outcome would force a choice the user doesn't need to make.

### Output Structure (widget — what the rendered surface looks like)

The widget renders as a Cowork action card. Conceptually, what the user sees:

```
3 conversations worth reviving
Pick which conversations to revive — I'll draft each one.

  💬 1.  Bo Sample — partnership exploration
        Quiet for 23 days
        Last from Bo: "Let me think about the structure and revert"
        Last from you: discovery questions about their distribution
        Suggested opener: Bo — where did we land on the structure question? Any movement?
        [Draft]  [Resolved]  [Skip]

  💬 2.  Rio Sample — investor intro thread
        Quiet for 31 days
        Last from her: intro to 2 partners at Northstar Partners
        Last from you: thank-you reply
        Suggested opener: Rio — those Northstar intros are still warm on my side. We just landed [new traction] — worth me pinging them with that?
        [Draft]  [Resolved]  [Skip]

  💬 3.  Sam Sample — Acme Co pilot
        Quiet for 18 days
        Last from Sam: "let me get back to you with the brief"
        Last from you: kickoff scope outline
        Suggested opener: Sam — any movement on the pilot brief?
        Sam still owes you: pilot brief (11 days past)
        [Draft]  [Resolved]  [Skip]
```

The bracket-style button labels above are illustrative — the actual rendered display labels come from the renderer's `_action_display_label` (e.g., `draft` → `Draft`, `skip` → `Snooze (1 day)`).

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary. In this widget the customer-facing noun is "conversations" (email/Slack threads may be called "threads" only when literally naming an email or Slack thread).
- Bad: "Pick which threads to revive — thread_resurrected event will be written."
- Good: "Pick which conversations to revive — I'll draft each one."

### DOES NOT

- Auto-send revival emails. Always drafts to Gmail Drafts; user reviews and clicks Send.
- Re-surface a thread within 30 days of the last `thread_resurrected` event for it.
- Touch threads with `thread_resolved` events (those are explicitly closed).
- Surface threads where the user's last message was unanswered for less than 14 days (that's too early — would harass).

## Narration leak scan (CUT-C item 8 — MANDATORY on every composed line)

Widget bodies are scanned inside `widget_transport.render_and_persist`; the PROSE this skill composes around them is not, unless this step runs. Before posting any sentence you composed — an ack, a header, a summary, a pointer, a "why" line — run `validate_chat_output(<the text>)` from `chat_output_renderer.py` (`shared/scripts/`). It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_` / `bp_` / `pcand:` wire id), an event or field name, a file name or path, or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(<WORKSPACE>))` is the one substitution). NEVER catch the error and post anyway. Text relayed byte-exact from a driver or the transport is already scanned and is not re-composed.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Surface the customers who've gone quiet relative to their own historical cadence — before the CEO finds out from a revenue report. Scans CRM, email, and meeting notes for cadence breaks, produces a ranked list with last-touch date, gap vs baseline, historical revenue, and a suggested re-engagement angle for each. Use when the CEO says 'who went dark', 'gone dark', 'customers have gone dark', 'which customers have gone dark', 'dormant customer scan', 'dormant customer', 'who haven't I heard from', 'who hasn't replied', 'hasn't replied in a while', 'quiet customers', 'customer dormancy check', 'customers who stopped responding'. Runs on-demand or as a scheduled Monday task. Also handles first-run personalization settings — use when the CEO says 'tune the dormant scan', 'tune dormant scan', 'tune dormant-customer-scan', 'show dormant scan settings', 'show dormant-customer-scan settings', 'reset dormant scan to defaults', 'reset dormant-customer-scan to defaults'. DOES NOT fire on 'follow up with [customer]' (email-writer — plain outbound draft; 'follow up on that call' is follow-up-ritual) or 'show me my customer list' (that's a tracker/RELATIONSHIPS.md read, not a scan). DOES NOT fire on 'who should I reach out to' / 'relationship moves' / 'weekly outreach' (that's relationship-moves — the ranked, pre-drafted action pack; this skill is the raw detection report it consumes).
>
> Threads lens (formerly thread-resurrection, folded in SKILLMERGE1 2026-09-03) — surface conversations (email threads, Slack threads, meeting follow-ups) that went silent but had high-value context worth reviving. Use when the CEO says 'what conversations went silent', 'what conversations went quiet', 'warm threads to revive', 'dead threads worth reviving', 'threads I dropped', 'thread resurrection', 'threads to revive', 'conversations to restart', 'what's gone quiet that I should restart', 'find warm conversations'. Reads interaction events (org-scoped), cross-references entities.json and open commitments, writes thread_resurrected events on revival action. DOES NOT fire on 'follow up with [name]' (email-writer — plain outbound draft; meeting-shaped follow-ups are follow-up-ritual) or 'show my open threads' (workspace-manager).
