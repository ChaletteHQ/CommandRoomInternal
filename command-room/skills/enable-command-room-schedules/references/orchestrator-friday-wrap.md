# Orchestrator prompt — Friday Wrap

This file is the EXACT prompt the bootloader cats and executes for `taskId: friday-wrap`. Fires 4:00 PM Friday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES`. NEW in v3.11.0 — first weekly-rhythm scheduled task. First-install default (one of 4 tasks registered on a fresh workspace).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Friday Wrap is a **markdown chat post**, not a widget — it's a recap, not an action surface. Renderer-validator gates do NOT apply here (no item-block parser, no button-action enforcement). The leak scanner DOES still apply (no entity-ID leaks, no email address leaks, no internal phase labels).

**Brief save path:** Friday Wrap produces a `.docx` artifact via the `weekly-recap` skill's existing Phase 5.B save path — `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx`. NEVER write to `_hq/staging/<today>/` (forbidden by the leak scanner — that path is reserved for scheduled-task email drafts).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface link blocks per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" pattern adapted for a non-widget post.

**Project routing:** thread / project resolution per `references/PROJECT_MAPPING_RULES.md`.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `weekly-recap` skill. The skill at `skills/weekly-recap/SKILL.md` is the source of truth for the recap's window definition, connector pull strategy, section ordering, format constraints, and `.docx` save path. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) execute the weekly-recap skill's Phases 1-6 verbatim against the by-project default mode, (c) ensure the resulting recap posts to chat once + saves the `.docx`, (d) log a `pack_run` event, (e) STOP.

---

## ⛔ STOP CONTRACT (v2.14.14+ — adapted for markdown post + .docx) — READ BEFORE YOU DO ANYTHING

**The markdown recap IS the chat turn. After it posts (plus the Briefs section linking the .docx), YOU STOP.** No exceptions, no edge cases. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered chat output to disk** outside the canonical `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx` path. Not to `_hq/scheduled_outputs/`, not to `_hq/staging/`, not anywhere else.

2. **No narrating what's in the recap.** The user can see it. Don't follow with "Total events scanned: X" / "Files saved to..." / "Here's a summary of what I just posted."

3. **No post-recap summary block.** The chat turn ends after the recap + Briefs section.

4. **No "regenerate with real data" mode.** If the user asks to re-fire, re-execute Phase 1 onward — don't switch to file-write mode.

5. **No widget fallback.** Friday Wrap is not a widget surface. Don't try to render via `mcp__visualize__show_widget` — that's for action surfaces (inbox / commitments / pulse / past-meetings / upcoming-meetings). Friday Wrap is a recap. Post markdown directly.

**Self-check before posting anything:** if you're about to write text AFTER the recap + Briefs section, ask: "is this required by spec?" If no → don't post it.

---

You are firing the Command Room "Friday Wrap" chat. Today is Friday in workspace LOCAL time. You're producing the week's recap before the user closes out the workweek.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — whether by cron or manual `re-run` trigger. Multiple fires per week are allowed. A `pack_run` event writes at the end of every fire for audit trail.

The weekly-recap skill's own idempotency (events.jsonl dedup via `source_ref_hash`, `.docx` overwrite on same-date filename) makes re-fires safe.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and the orchestrator file path. Continue with:

- Compute today's date in local time (YYYY-MM-DD) via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)`. **v3.11.1+ contract:** `workspace_path` is REQUIRED on every call. Catch `TZResolutionError` and render the digest header with a "⚠️ Couldn't resolve workspace TZ — times shown as UTC" note rather than aborting.
- Read `<WORKSPACE>/_hq/data/entities.json`. Capture the primary user (`person` record where `is_primary_user: true`) — first name + email + timezone.
- Read `<WORKSPACE>/_hq/data/aliases.json` for canonicalization during connector scans.
- Read `<WORKSPACE>/CLAUDE.md` if it exists (hot cache for people, projects, terms — supplies most quick references without per-file reads).
- Discover available connectors: Mail (Gmail or Outlook MCP — NEVER Zapier for read), Calendar (native Google or Outlook), Slack/Teams, Drive/OneDrive/SharePoint, every meeting-transcript source MCP wired (Granola / Fireflies / Otter / Read.ai / Zoom AI Companion / Microsoft Teams summaries). Per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE: Zapier is send-only; reads use native MCP.

# Phase 3 — Execute the weekly-recap skill (by-project mode, 7-day window)

Read `skills/weekly-recap/SKILL.md`. Execute its Phases 1-6 verbatim against the current workspace + connectors, with these orchestrator-imposed defaults:

- **Window:** last 7 days `[now - 7d, now]` in workspace timezone. Same as the skill's default.
- **Grouping mode:** by-project (the skill's default — customer's mental model is project-shaped).
- **Connector caps:** use the skill's defaults (250 received + 250 sent emails, 200 Slack messages, 100 Drive files, 50 transcripts, all calendar events that occurred).
- **scan-for-commitments side effect:** run per the skill's Phase 3. Commitments captured from the week's meetings deepen the recap's commitment counts.
- **Output surfaces:** inline chat (markdown recap) + saved `.docx` at `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx` per the skill's Phase 5.

Every connector read MUST emit corresponding events to `<WORKSPACE>/_hq/data/events.jsonl` per `shared/PASSIVE_CAPTURE.md` and the weekly-recap skill's batched `atomic_append_jsonl` pattern. Dedup via `source_ref_hash` so re-fires don't double-count.

# Phase 4 — Post the recap

Output the rendered markdown recap as the chat turn body. Follow the exact format from `skills/weekly-recap/SKILL.md` Phase 4 — Headline, Top Decisions, Commitments Captured (You Owe / They Owe), Notable Meetings, Email Threads of Note, New People Surfaced, Anomalies, By-Project Breakdown, What Now. Omit sections with no real content (the skill's "no placeholders" rule).

**Tone:** crisp, direct, no preamble. The recap stands on its own — don't introduce it with "Here's your week" or "Wrapping up the week."

**Voice match:** if `<WORKSPACE>/_hq/.claude/brand-voice-guidelines.md` exists, match user voice for the "What Now" section. Otherwise neutral professional.

After the recap, add a **Briefs:** section per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" — one bulleted line pointing at the saved `.docx`:

```markdown
**Briefs:**

- [Weekly Recap — <Mon DD> to <Mon DD>](computer:///<encoded path>) — saved as `.docx` in `_hq/meetings/`
```

If `_hq/meetings/` save failed for any reason (brief_writer error, path-resolution failure), surface a one-line footnote in plain English (*"⚠️ Couldn't save the .docx — the recap above is the working copy."*) and skip the Briefs section entirely.

# Phase 5 — Log the fire + close

Build the telemetry block via `shared/scripts/telemetry.py` `build_pack_run_telemetry()` — same pattern as the other orchestrators. Track connector calls + prompt/response sizes + duration. Merge into `pack_run.data` as `telemetry: {...}`. Silent — never narrated to chat.

Append one `pack_run` event to `events.jsonl` via `atomic_append_jsonl`:

```json
{"type": "pack_run", "source_skill": "friday-wrap", "primary_thread_id": null, "related_thread_ids": [], "classification_confidence": null, "data": {"task_id": "friday-wrap", "fired_at": "<ISO>", "recap_path": "_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx", "window_start": "<ISO>", "window_end": "<ISO>", "events_captured": <N>, "commitments_found": <N>, "outcome": "complete", "telemetry": {...}}}
```

Note: the weekly-recap skill ALSO appends its own `weekly_recap_run` event per its Phase 6. Both events coexist — the `weekly_recap_run` records the skill's invocation; the `pack_run` records the scheduled-task fire that invoked it. Same pattern as morning-brief / morning-briefing.

**STOP.** The chat turn is over. Do not narrate what just posted. Do not summarize sections. Do not preview next week's fire.

---

## Why this orchestrator wraps the skill instead of reimplementing

The on-demand triggers `weekly recap` / `weekly summary` / `what happened last week` / `recap last week` already fire the `weekly-recap` skill in Cowork. The scheduled task and the on-demand triggers MUST produce identical content — same window, same sections, same .docx save path. Reimplementing the logic in this orchestrator would create two divergent code paths for the same output.

The single source of truth lives at `skills/weekly-recap/SKILL.md`. This orchestrator is the thinnest possible wrapper: resolve paths, delegate to the skill, render the output as chat, save the .docx, log, stop. Plugin upgrades that change the weekly-recap format propagate automatically — this orchestrator inherits whatever the skill produces.

## What this orchestrator does NOT do

- Does NOT process individual meeting transcripts (that's `past-meetings` — daily, single-meeting scope).
- Does NOT triage email (that's `inbox` scheduled task).
- Does NOT generate per-meeting prep briefs (that's `upcoming-meetings`).
- Does NOT modify entities.json or aliases.json — weekly-recap only appends events. New people surfaced are queued for `people-crm` on the next turn via `pending_review: true` event annotations.
- Does NOT fabricate data when a connector times out — per the weekly-recap skill's Phase 2 caps + footnote rule, output a footnote and continue without that source's data.
- Does NOT fire on weekends if the cron is configured Friday-only (default `0 16 * * 5`). Manual trigger of `weekly recap` on any other day still works via the skill's on-demand path.
- Does NOT replace `cleanup` (workspace health check) or `morning-brief` (daily digest). Different surfaces, different cadences.
