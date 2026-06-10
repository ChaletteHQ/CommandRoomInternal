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

3. **No post-digest summary block.** The chat turn ends after the digest + Links section.

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

# Phase 3 — Execute the morning-briefing skill

Read `skills/morning-briefing/SKILL.md`. Execute its Steps 1-5 verbatim against the current workspace + connectors:

- **Step 1 — Load core context** (already done in Phase 2 above; do not re-read).
- **Step 2 — Scan connected sources.** Calendar today + tomorrow's first event; Mail unread/important from last 18h, filtered by people in PEOPLE.md + project-related subjects + flagged; Slack/Teams unread DMs and mentions from last 18h, plus project channels. Per the skill's caps: top 10 emails, top 5 Slack items. **Self-reply filter (v3.11.1 — REQUIRED):** apply the skill's Step 2 "Self-reply filter" verbatim. For every email-thread candidate, fetch the thread's latest message and compare `From:` to the primary user's email (from `entities.json`'s `is_primary_user: true` record). If the latest message is FROM the primary user, DROP the thread from Needs Attention and Overnight Inbox — the user already responded. This filter applies to scheduled fires; the default `in:inbox` query alone is insufficient because Gmail surfaces earlier inbound messages in threads the user has since replied to.
- **Step 3 — Check tracker for urgency.** Scan MASTER_TRACKER for overdue commitments, stale waiting-on items (7+ days), today's deadlines, urgent flags. Apply Step 3b commitments aggregation from `events.jsonl` (`type: commitment` not closed by a later `commitment_resolved` / `thread_resolved` event). Compute three counts: you owe / they owe / stuck. **Step 3a freshness overlay (v3.11.1 — REQUIRED):** apply the skill's Step 3a overlay verbatim — parse the tracker's `<!-- generated-at -->` stamp, and for every thread the digest will surface scan `events.jsonl` for events with `ts > tracker_stamp` and `primary_thread_id == thread.id`. Override `Last touched` / `Next Action` / `Waiting On` from those newer events. The tracker is a snapshot, not a live view; without this overlay a scheduled fire on a workspace whose tracker hasn't been regenerated in 10 days will surface stale "quiet since April 25" copy for threads that had activity today.
- **Step 4 — Build the digest.** Apply the relationship-grouped thread layout from the skill's Step 4: every thread's `affiliation_id` resolves to its org; primary-focus orgs render prominently; non-primary roll up under "OTHER ORGS" with `relationship_type` badges. Section headers use `canonical_name`, not hardcoded labels. Five sections maximum, omit any with no content. SUGGESTED FIRST MOVE at the end — one sentence.
- **Step 5 — Deliver.** In scheduled mode (this is one), follow the saved-snapshot path: write the rendered digest to `<WORKSPACE>/_hq/briefings/morning-<YYYY-MM-DD>.md` per the skill's "save to file" branch, AND post the digest inline in this chat turn.

Every connector read MUST emit corresponding events to `<WORKSPACE>/_hq/data/events.jsonl` per `shared/PASSIVE_CAPTURE.md`. Use `atomic_append_jsonl` from `shared/scripts/atomic_write.py` for batched appends. Dedup via `source_ref_hash` so re-fires don't double-count overnight email reads.

# Phase 4 — Post the digest

Output the rendered markdown digest as the chat turn body. Follow the exact format from `skills/morning-briefing/SKILL.md` Step 4 — header line, optional commitments line, calendar section, NEEDS ATTENTION, OVERNIGHT INBOX, per-org thread sections, SUGGESTED FIRST MOVE.

**Tone:** crisp, direct, no preamble. Per the skill: "this is a status board, not a conversation. No 'Good morning!' or 'Here's what's happening.' Just the data."

**Voice match:** if `<WORKSPACE>/_hq/.claude/brand-voice-guidelines.md` exists, match user voice for the SUGGESTED FIRST MOVE line. Otherwise neutral professional.

After the digest, if any briefs or files were referenced (Past Meetings docs, Upcoming Meetings prep docs from yesterday's fires), add a **Links:** section per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" — one bulleted line per linked file, `computer://` artifact URLs. Skip the Links section entirely if nothing connects.

# Phase 5 — Log the fire + close

Build the telemetry block via `shared/scripts/telemetry.py` `build_pack_run_telemetry()` — same pattern as the other 5 orchestrators. Track connector calls + prompt/response sizes + duration. Merge into `pack_run.data` as `telemetry: {...}`. Silent — never narrated to chat. (v3.5.0+ — morning-brief was the only orchestrator missing this; `usage report` aggregation was incomplete for morning-brief fires until then.)

Append one `pack_run` event to `events.jsonl` via `atomic_append_jsonl`:

```json
{"type": "pack_run", "source_skill": "morning-brief", "primary_thread_id": null, "related_thread_ids": [], "classification_confidence": null, "data": {"task_id": "morning-brief", "fired_at": "<ISO>", "digest_path": "_hq/briefings/morning-<YYYY-MM-DD>.md", "sections_rendered": [<list>], "events_captured": <N>, "outcome": "complete", "telemetry": {...}}}
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
