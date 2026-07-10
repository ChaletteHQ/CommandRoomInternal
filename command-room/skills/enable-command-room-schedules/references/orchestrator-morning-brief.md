# Orchestrator prompt — Morning Brief

This file is the EXACT prompt the bootloader cats and executes for `taskId: morning-brief`. Fires 7:00 AM weekdays local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES`. NEW in onboarding-v2 / 2026-05-17. First-install default (one of 3 tasks registered on a fresh workspace).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Morning Brief is a **markdown chat post**, not a widget — it's a digest, not an action surface. Renderer-validator gates do NOT apply here (no item-block parser, no button-action enforcement). The leak scanner DOES still apply (no entity-ID leaks, no email address leaks, no internal phase labels).

**Brief save path:** Morning Brief does NOT produce a `.docx` brief deliverable. It posts the digest inline and (optionally) saves a markdown snapshot to `_hq/briefings/morning-<YYYY-MM-DD>.md` per the morning-briefing skill's Step 5 saved-snapshot path. NEVER write to `_hq/staging/<today>/` (forbidden by the leak scanner — that path is reserved for scheduled-task email drafts).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface link blocks per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" pattern adapted for a non-widget post.

**Project routing:** thread / project resolution per `references/PROJECT_MAPPING_RULES.md`.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `morning-briefing` skill. The skill at `skills/morning-briefing/SKILL.md` is the source of truth for the digest's format, section ordering, urgency rules, and relationship-grouped thread layout. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) execute the morning-briefing skill's Steps 1-5 verbatim, (c) ensure the resulting digest posts to chat once, (d) log a `pack_run` event, (e) STOP.

---

## ⛔ STOP CONTRACT (v2.14.14+ — adapted for markdown post) — READ BEFORE YOU DO ANYTHING

**The markdown digest IS the chat turn. After it posts (plus any optional Links section), YOU STOP.** No exceptions, no edge cases. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered chat output to disk** outside the canonical `_hq/briefings/morning-<date>.md` snapshot path. Not to `_hq/scheduled_outputs/`, not to `_hq/staging/`, not anywhere else.

2. **No narrating what's in the digest.** The user can see it. Don't follow with "Total scan results: X events" / "Files saved to..." / "Here's a summary of what I just posted."

3. **No post-digest summary block.** The chat turn ends after the digest + Links section. (EXCEPTION: the morning-briefing skill's one-time First-Run Personalization footer — see Phase 3 — is part of the defined digest tail, like the scan-for-commitments nudge; it is NOT a summary block and is allowed on the first fire only, gated by `is_configured`.)

4. **No "regenerate with real data" mode.** If the user asks to re-fire, re-execute Phase 1 onward — don't switch to file-write mode.

5. **No widget fallback.** Morning Brief is not a widget surface. Don't try to render via `mcp__visualize__show_widget` — that's for action surfaces (inbox / commitments / pulse / past-meetings / upcoming-meetings). Morning Brief is a digest. Post markdown directly.

**Self-check before posting anything:** if you're about to write text AFTER the digest + Links section, ask: "is this required by spec?" If no → don't post it.

---

You are firing the Command Room "Morning Brief" chat. Today is the LOCAL date now. You're producing the morning digest before the user starts their workday.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — whether by cron or manual `re-run` trigger. Multiple fires per day are allowed. A `pack_run` event writes at the end of every fire for audit trail.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and the orchestrator file path. Continue with:

- Compute today's date in local time (YYYY-MM-DD) via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)`. **v3.11.1+ contract:** `workspace_path` is REQUIRED — pass the resolved `<WORKSPACE>` path on every call (or set `CR_WORKSPACE` in the subprocess env). The prior walk-up resolver was removed because it never resolved inside the plugin clone and silently rendered UTC. If `to_local` raises `TZResolutionError`, surface "⚠️ Couldn't resolve workspace TZ — times shown as UTC" in the digest header and continue rendering with raw UTC; do not let the exception abort the fire.
- Every site in this orchestrator that renders a connector timestamp (Gmail `internalDate`, Calendar event start/end, Slack `ts`) MUST pass `workspace_path=<WORKSPACE>` to `to_local()` / `format_local()`. No exceptions.
- Read `<WORKSPACE>/_hq/data/entities.json`. Capture the primary user (`person` record where `is_primary_user: true`) — first name + email + timezone.
- Read `<WORKSPACE>/_hq/data/aliases.json` for canonicalization during connector scans.
- Read `<WORKSPACE>/CLAUDE.md` if it exists (hot cache for people, projects, terms — supplies most quick references without per-file reads).
- Read `<WORKSPACE>/_hq/MASTER_TRACKER.md` (project list, statuses, next actions, waiting-on).
- Discover available connectors: Calendar (native Google or Outlook MCP), Mail (Gmail or Outlook MCP — NEVER Zapier for read), Slack/Teams. Per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE: Zapier is send-only; reads use native MCP.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'morning-brief', fired_via='<scheduled|manual>')))
"
```

Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (Phase 4 — Post the digest): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Execute the morning-briefing skill

Read `skills/morning-briefing/SKILL.md`. Execute its Steps 1-5 verbatim against the current workspace + connectors:

- **Step 1 — Load core context** (already done in Phase 2 above; do not re-read).
- **Step 2 — Scan connected sources.** Calendar today + tomorrow's first event; Mail unread/important from last 18h, filtered by people in PEOPLE.md + project-related subjects + flagged; Slack/Teams unread DMs and mentions from last 18h, plus project channels. Per the skill's caps: top 10 emails, top 5 Slack items. **Self-reply filter (v3.11.1 — REQUIRED):** apply the skill's Step 2 "Self-reply filter" verbatim. For every email-thread candidate, fetch the thread's latest message and compare `From:` to the primary user's email (from `entities.json`'s `is_primary_user: true` record). If the latest message is FROM the primary user, DROP the thread from Needs Attention and Overnight Inbox — the user already responded. This filter applies to scheduled fires; the default `in:inbox` query alone is insufficient because Gmail surfaces earlier inbound messages in threads the user has since replied to.
- **Step 3 — Check tracker for urgency.** Scan MASTER_TRACKER for overdue commitments, stale waiting-on items (7+ days), today's deadlines, urgent flags. Apply Step 3b commitments aggregation from `events.jsonl` (`type: commitment` not closed by a later `commitment_resolved` / `thread_resolved` event). The header counts come from `commitment_state.compute_brief_state(...)` per the skill's Step 3d — render its `counts["headline"]` buckets (you owe / owed to you / unowned / unconfirmed, plus overdue and stuck), the one bucket export (Phase 2 Stage A + v4.5.2 R4 + v4.6.0 MC2); never hand-compute them, never fold unowned/unconfirmed into a direction, and never label the overdue number "stuck" (R1b). `headline["stuck"]` is the REAL movement metric (MC2: no movement 21+ days, or blocked on a named person — `compute_and_log_brief_state` derives it automatically); render it as its own segment, and omit the segment when the key is absent (not computed, never 0). **Step 3a freshness overlay (v3.11.1 — REQUIRED):** apply the skill's Step 3a overlay verbatim — parse the tracker's `<!-- generated-at -->` stamp, and for every thread the digest will surface scan `events.jsonl` for events with `ts > tracker_stamp` and `primary_thread_id == thread.id`. Override `Last touched` / `Next Action` / `Waiting On` from those newer events. The tracker is a snapshot, not a live view; without this overlay a scheduled fire on a workspace whose tracker hasn't been regenerated in 10 days will surface stale "quiet since April 25" copy for threads that had activity today.
- **Step 4 — Build the digest.** Apply the relationship-grouped thread layout from the skill's Step 4: every thread's `affiliation_id` resolves to its org; primary-focus orgs render prominently; non-primary roll up under "OTHER ORGS" with `relationship_type` badges. Section headers use `canonical_name`, not hardcoded labels. Omit any section with no content — never pad. Number the Needs Attention items and keep the rendered order: the item→commitment-id mapping writes into the `pack_run` receipt as `needs_attention_ids` (Phase 5) so a later `mark done [n]` resolves. SUGGESTED FIRST MOVE at the end — one sentence. **Surface-preference filter (Phase 6 Loop 2):** before finalizing Needs Attention, drop any item the CEO has taught the system to stop surfacing — `from surface_preferences import load_surface_preferences, is_suppressed`; keep an item only if `not is_suppressed(prefs, "morning-brief", item_class=<class>, entity_id=<person/project id>)`. Missing store → no-op; the substrate is untouched.
- **Step 4b — Background-task watchdog line (v4.6.1 S3 — the light daily pass; ONE call, receipts-only).** `from task_watchdog import brief_watchdog_line; line = brief_watchdog_line(WORKSPACE)`. If `line` is not None, append it to the digest tail (after SUGGESTED FIRST MOVE, before the Links section) verbatim — e.g. "2 of your background tasks need attention — say health check for the detail." That sentence is the ENTIRE pass: no per-task detail, no cause narration, no second scan — `health check` (system-health) owns the deep answer. `None` means nothing renders (all healthy, or this chat can't see the scheduler) — never pad an all-clear line into the brief.
- **Step 5 — Deliver.** In scheduled mode (this is one), follow the saved-snapshot path: write the rendered digest to `<WORKSPACE>/_hq/briefings/morning-<YYYY-MM-DD>.md` per the skill's "save to file" branch, AND post the digest inline in this chat turn.
- **First-Run Personalization (SPEC FRP1).** Apply the morning-briefing skill's "First-Run Personalization" section. Read the brief's knobs via `get_config(WORKSPACE, "morning-briefing", DEFAULTS)`. On the FIRST fire only (`not is_configured(WORKSPACE, "morning-briefing")`): `save_skill_config(WORKSPACE, "morning-briefing", DEFAULTS)` before rendering, then append the one-time **footer** form of the first-run block after the digest (this orchestrator is a markdown post, NOT a widget, so the footer — not fr-items — is the correct transport, and MUST-NOT rule 5 does not apply). The footer renders exactly once ever, gated by `is_configured`.

Every connector read MUST emit corresponding events to `<WORKSPACE>/_hq/data/events.jsonl` per `shared/PASSIVE_CAPTURE.md`. Use `atomic_append_jsonl` from `shared/scripts/atomic_write.py` for batched appends. Dedup via `source_ref_hash` so re-fires don't double-count overnight email reads.

# Phase 4 — Post the digest

Output the rendered markdown digest as the chat turn body. Follow the exact format from `skills/morning-briefing/SKILL.md` Step 4 — header line, optional commitments line, calendar section, NEEDS ATTENTION, OVERNIGHT INBOX, per-org thread sections, SUGGESTED FIRST MOVE.

**Tone:** crisp, direct. Opening order per the skill's Tone section: (1) the personified intro line (`"Morning, {first_name} — {brain_name} here with today's read."` — the ONLY greeting), (2) the digest header, (3) the synthesis lead. No other greeting ("Good morning!" / "Here's what's happening." — never). After that it's a status board, not a conversation.

**Voice match:** if `<WORKSPACE>/_hq/.claude/brand-voice-guidelines.md` exists, match user voice for the SUGGESTED FIRST MOVE line. Otherwise neutral professional.

After the digest, if any briefs or files were referenced (Past Meetings docs, Upcoming Meetings prep docs from yesterday's fires), add a **Links:** section per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" — one bulleted line per linked file, `computer://` artifact URLs. Skip the Links section entirely if nothing connects.

# Phase 5 — Log the fire + close

Build the telemetry block via `shared/scripts/telemetry.py` `build_pack_run_telemetry()` — same pattern as the other 5 orchestrators. Track connector calls + prompt/response sizes + duration. Merge into `pack_run.data` as `telemetry: {...}`. Silent — never narrated to chat. (v3.5.0+ — morning-brief was the only orchestrator missing this; `usage report` aggregation was incomplete for morning-brief fires until then.)

Append the fire receipt via the canonical helper (`shared/scripts/receipts.py`, v4.5.2 R1 — **NEVER hand-roll the receipt JSON**; hand-rolled shapes are the F-10b/F-49 drift class):

```python
from receipts import log_receipt
log_receipt(
    WORKSPACE_ROOT, "morning-brief",
    fired_via=lateness["receipt_fired_via"],  # from Phase 2.9 — manual | scheduled | catchup; never guess it
    duration_ms=elapsed_ms,
    late_tier=lateness["tier"] if lateness["tier"] in ("note", "degrade") else None,
    extra_data={
        "digest_path": "_hq/briefings/morning-<YYYY-MM-DD>.md",
        "sections_rendered": [...],
        # the commitment data.id for each numbered Needs Attention item, render
        # order — apply-choices resolves `mark done [n]` against this list;
        # empty list if the section didn't render
        "needs_attention_ids": [...],
        "events_captured": N,
        "telemetry": {...},
    },
)
```

If the workspace has **zero commitment events** but ≥3 meeting events on file, the morning-briefing skill's Step 3b nudge ("💡 Commitments tab is empty even though you've had N meetings — say 'scan for commitments' to backfill") was already included in the digest tail. Do not duplicate it as a separate chat turn.

**7-day activity stopgap (v3.11.1 — REQUIRED).** Apply the morning-briefing skill's Step 3b 7-day filter verbatim: for every commitment that would surface in Needs Attention as overdue/stuck, look up the linked thread's max `ts` in events.jsonl across ALL event types. If the thread has any activity in the last 7 days, drop the commitment from the Needs Attention surface (the work is likely done — events.jsonl just doesn't have the resolution event yet). The three header counts continue to reflect raw workspace state; only the actionable surfaced list is filtered.

**STOP.** The chat turn is over. Do not narrate what just posted. Do not summarize sections. Do not preview tomorrow's fire.

---

## Why this orchestrator wraps the skill instead of reimplementing

The on-demand triggers `morning briefing` / `brief me` / `what do I need to know today` / `start my day` already fire the `morning-briefing` skill in Cowork. The scheduled task and the on-demand triggers MUST produce identical content — same sections, same urgency rules, same relationship-grouped layout. Reimplementing the logic in this orchestrator would create two divergent code paths for the same output.

The single source of truth lives at `skills/morning-briefing/SKILL.md`. This orchestrator is the thinnest possible wrapper: resolve paths, delegate to the skill, render the output as chat, log, stop. Plugin upgrades that change the morning-briefing format propagate automatically — this orchestrator inherits whatever the skill produces.

## What this orchestrator does NOT do

- Does NOT triage individual emails (that's `inbox` scheduled task — different orchestrator).
- Does NOT process meeting transcripts (that's `past-meetings`).
- Does NOT generate per-meeting prep briefs (that's `upcoming-meetings`).
- Does NOT modify entities.json, MASTER_TRACKER, or any workspace state — morning-briefing is read-only.
- Does NOT fabricate data when a connector times out — per the skill's Reliability section, output "⚠️ Couldn't reach [Gmail/Calendar/Slack] — check connection" and continue without that source's data.
- Does NOT fire on weekends if the cron is configured weekday-only (default). Manual trigger of `morning briefing` on a weekend still works via the skill's on-demand path.
