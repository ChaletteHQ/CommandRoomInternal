---
name: dormant-customer-scan
description: "Surface the customers who've gone quiet relative to their own historical cadence — before the CEO finds out from a revenue report. Scans CRM, email, and meeting notes for cadence breaks, produces a ranked list with last-touch date, gap vs baseline, historical revenue, and a suggested re-engagement angle for each. Use when the CEO says 'who went dark', 'gone dark', 'customers have gone dark', 'which customers have gone dark', 'dormant customer scan', 'dormant customer', 'who haven't I heard from', 'who hasn't replied', 'hasn't replied in a while', 'quiet customers', 'customer dormancy check', 'customers who stopped responding'. Runs on-demand or as a scheduled Sunday task. DOES NOT fire on 'follow up with [customer]' (that's follow-up-ritual or inbox-triage draft) or 'show me my customer list' (that's a tracker/RELATIONSHIPS.md read, not a scan)."
---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

If the scan is invoked with a name-bearing trigger ("dormant scan for [name's customers]"), you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` first to resolve the named scope. For the cadence-break computation, call `shared/scripts/cru_match.py::load_open_commitments` if you need open-commitment cross-reference — do NOT hand-roll an events.jsonl scan. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.1)

- **Use dormant-customer-scan for:** proactive detection of cadence breaks. Output is a ranked list of people/orgs the CEO should reach out to.
- **Reads from:** `_hq/data/events.jsonl` (canonical Tier 1 source per `references/SOURCE_OF_TRUTH.md` — compute cadence directly from interaction/meeting events per person via `event_references_person` from `cru_match.py`, NOT from the `_hq/views/RELATIONSHIPS.md` projection, which is regenerated lazily by insight-generator and may be stale) + `_hq/data/entities.json` for people/org records + Gmail/Slack for recency confirmation.
- **Does NOT draft outbound messages** — surfaces the list. If the CEO says "scan for dormant customers and draft re-engagement emails," this skill produces the list, then follow-up-ritual or a composer drafts.

## Writer Contract

This skill reads Gmail and Slack during scans to confirm recency. Every new inbound from a flagged dormant thread emits an `interaction` event tagged as a re-activation signal per `shared/PASSIVE_CAPTURE.md`. No raw message text is persisted — summary + source_ref only.

---

# Dormant Customer Scan

**For:** CEOs with a portfolio of recurring customers who know some have gone quiet but can't keep it in their head. Productizes the "aha" moment Bo hit when Intel surfaced Acme Co.

## What It Does

Scan CRM, email history, and meeting notes for customers whose cadence has broken relative to their own historical pattern. Produce a ranked list with enough context that the CEO can pick up the phone or send a note today.

Each dormant customer gets: last-touch date, cadence baseline, gap vs baseline, historical revenue, last interaction summary, suggested re-engagement angle. One markdown file. Scannable in under 2 minutes.

Runs on demand. Also schedulable weekly as a recurring surface — the hook line for the Beta Tier retainer is "every week Claude tells you who's gone dark."

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

This closes Bug #28 from Session-22 testing: Northstar Partners/Lyra was flagged as 44-days-dormant from substrate, while a Calendar event 31 days ago existed but had never been written to events.jsonl. Real CEO-trust miss ("you told me to chase someone I literally met with"). The helper unifies dormant-customer-scan with Pulse (Bug #5) — same call site, same merge math.

If the live-check helper isn't available (sandbox / connector failure), you still must NOT silent-fall-through to substrate-only flags. Surface the gap honestly in the report ("Live Gmail/Calendar lookup unavailable — flags below are substrate-only; verify any flagged customer before reaching out").

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

## Output Structure

```
# Customers worth reaching out to — [YYYY-MM-DD]

## Summary
Looked across [N] customers. [M] have gone quieter than their usual cadence. Top revenue worth a check-in: $[X].

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

When configured as a recurring scan, this skill implements `shared/RELIABILITY.md`. Key rules: skip-not-fail when no CRM connector AND no `_hq/CUSTOMERS.md` exist (log to `_hq/logs/scheduled-task-skips.log`, exit clean — never invent a customer list), OOO defers the scan, 60s aggregate scan budget across Gmail / CRM / Granola / Slack with graceful degradation, last-known-good cache at `_hq/caches/dormant-customer-scan-last-good.json` when a connector fails, dedup via `source_ref` hash makes re-running idempotent. Output always lands at `_hq/insights/DORMANT_CUSTOMERS_[date].md` as the fallback delivery channel.

## What It Doesn't Do

- Doesn't draft re-engagement emails (that's the user's job, or use `one-pager-composer` for a talking-points brief)
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
