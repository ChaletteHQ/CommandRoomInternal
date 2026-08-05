# Orchestrator prompt — Friday Wrap

This file is the EXACT prompt the bootloader cats and executes for `taskId: friday-wrap`. Fires 4:00 PM Friday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES`. NEW in v3.11.0 — first weekly-rhythm scheduled task. First-install default (one of 4 tasks registered on a fresh workspace).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Friday Wrap is a **markdown chat post**, not a widget — it's a recap, not an action surface. Renderer-validator gates do NOT apply here (no item-block parser, no button-action enforcement). The leak scanner DOES still apply (no entity-ID leaks, no email address leaks, no internal phase labels).

**Brief save path:** Friday Wrap produces a `.docx` artifact via the `weekly-recap` skill's existing Phase 5.B save path — `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx`. NEVER write to `_hq/staging/<today>/` (forbidden by the leak scanner — that path is reserved for scheduled-task email drafts).

**⛔ DELIVERABLE RENDER GATE (DOCFENCE4).** That `.docx` MUST come out of `weekly-recap`'s Phase 5.B `brief_writer` path — delegating the recap does not delegate this gate, because the file lands here:

- **NEVER hand-roll the recap** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or leaking recap (the v3.20.0 failure mode). This task fires on a schedule with nobody in the room, so a bypass here is not one bad document, it is a standing weekly one nobody is watching — and the recap sweeps seven days of every connected source, which is the widest surface any single artifact in this system carries.
- **NEVER create, render, copy, upload, or update the recap — or any part, derivative, or restatement of it ("the week in numbers", "a summary", "the highlights") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/meetings/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "so the team can read it Monday", "as a copy alongside the canonical file" — **nor a direct instruction**: "put this week's recap in a Google Doc so I can send it round" is a request this gate refuses, not an override. Hand back the `.docx` link and let the user forward the file itself.

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface link blocks per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" pattern adapted for a non-widget post.

**Project routing:** thread / project resolution per `references/PROJECT_MAPPING_RULES.md`.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `weekly-recap` skill. The skill at `skills/weekly-recap/SKILL.md` is the source of truth for the recap's window definition, connector pull strategy, section ordering, format constraints, and `.docx` save path. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) execute the weekly-recap skill's Phases 1-6 verbatim against the by-project default mode, (c) ensure the resulting recap posts to chat once + saves the `.docx`, (d) log a `pack_run` event, (e) STOP.

---

## ⛔ STOP CONTRACT (v2.14.14+ — adapted for markdown post + .docx) — READ BEFORE YOU DO ANYTHING

**The markdown recap IS the chat turn. After it posts (plus the Briefs section linking the .docx), YOU STOP.** No exceptions, no edge cases. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered chat output to disk** outside the canonical `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx` path. Not to `_hq/scheduled_outputs/`, not to `_hq/staging/`, not anywhere else.

2. **No narrating what's in the recap.** The user can see it. Don't follow with "Total events scanned: X" / "Files saved to..." / "Here's a summary of what I just posted."

3. **No post-recap summary block.** The chat turn ends after the recap + Briefs section. (EXCEPTION: the weekly-recap skill's one-time First-Run Personalization footer — see Phase 3 — is part of the defined recap tail; it is NOT a summary block and is allowed on the first fire only, gated by `is_configured`.)

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

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)`. **v3.11.1+ contract:** `workspace_path` is REQUIRED on every call. Catch `TZResolutionError` and render the digest header with a "⚠️ Couldn't resolve workspace TZ — times shown as UTC" note rather than aborting.
- Read `<WORKSPACE>/_hq/data/entities.json`. Capture the primary user (`person` record where `is_primary_user: true`) — first name + email + timezone.
- Read `<WORKSPACE>/_hq/data/aliases.json` for canonicalization during connector scans.
- Read `<WORKSPACE>/CLAUDE.md` if it exists (hot cache for people, projects, terms — supplies most quick references without per-file reads).
- Discover available connectors: Mail (Gmail or Outlook MCP — NEVER Zapier for read), Calendar (native Google or Outlook), Slack/Teams, Drive/OneDrive/SharePoint, every meeting-transcript source MCP wired (Granola / Fireflies / Otter / Read.ai / Zoom AI Companion / Microsoft Teams summaries). Per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE: Zapier is send-only; reads use native MCP.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'friday-wrap', fired_via='<scheduled|manual>', env_date='<session date>')))
"
```

**Every python subprocess in this fire carries `CR_WORKSPACE` (CLOCK1).** Prefix them: `CR_WORKSPACE=<WORKSPACE> python3 -c "..."`. Each `python3 -c` is its own process started from the plugin root, so a helper left to guess which workspace it is in finds nothing, cannot cross-check the clock, and stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (Phase 4 — Post the recap): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Execute the weekly-recap skill (by-project mode, 7-day window)

Read `skills/weekly-recap/SKILL.md`. Execute its Phases 1-6 verbatim against the current workspace + connectors, with these orchestrator-imposed defaults:

**Visual pass note (SPEC OUT2 §3):** the weekly-recap skill's visual pass (render-then-critique of the saved .docx per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass") is PART of the skill's phases and runs here too. Its page-preview PNGs go to a session temp dir only — that render does NOT violate this orchestrator's "no writing rendered chat output to disk" rule (the previews are ephemeral critique input, not output; nothing lands in `_hq/` beyond the canonical .docx and the `visual_gate` audit event). In this sandbox the ladder usually returns `None` — log the skipped event and move on; never install a renderer from a scheduled task.

- **Window (SPEC CATCHUP1 F-2):** since the last successful Friday Wrap, floored at the 7-day default and ceilinged at 30 days — the skill's "Window definition" section owns the computation. Call `catchup.catchup_window(<workspace_root>, "friday-wrap", floor_hours=168, cap_days=30, fired_via=lateness["receipt_fired_via"], scheduled_only=True)` and hand its **`start_aware` / `end_aware`** to the skill's Phase 1 instead of `[now - 7d, now]`. A normal weekly fire gets exactly the 7 days it always had; a fire after a missed Friday covers both weeks, because a fixed 7-day window means the skipped week is never recapped and the next fire does not reach back over it. When it returns `extended: true`, the recap headline names the real span (*"the last 12 days"*, never *"this week"*). `fired_via` comes from Phase 2.9 and is never guessed — a human typing "weekly recap" gets the plain 7 days.
- **Window clock (SPEC CATCHUP1 F-1):** the connector queries get `start_aware` / `end_aware` — the offset-carrying instants — never the naive `start` / `end`. The skill's Phase 1 is documented in workspace TZ; `catchup_window`'s math is machine-local because the scheduler is. Handing a bare naive value across that seam is LATETZ's failure class and is invisible on any machine where the two clocks agree. The skill's "Window definition" section owns the full rule; keep the naive pair for the receipt's `window_start` / `window_end`, which are read back by the next fire's machine-local math.
- **Grouping mode:** by-project (the skill's default — customer's mental model is project-shaped).
- **Connector caps:** use the skill's defaults (250 received + 250 sent emails, 200 Slack messages, 100 Drive files, 50 transcripts, all calendar events that occurred). **These were tuned for 7 days and are unchanged; the window above can be 30.** So the skill's **COVERAGE HONESTY GATE** (its Phase 2, SPEC CATCHUP1 F-2) is in force on every `extended: true` fire: a capped read that comes back at its cap means this fire SAMPLED the span rather than covering it — the headline says so, and the receipt below carries `window_incomplete_before` so the next fire reaches back instead of starting after this one.
- **scan-for-commitments side effect:** run per the skill's Phase 3. Commitments captured from the week's meetings deepen the recap's commitment counts.
- **Output surfaces:** inline chat (markdown recap) + saved `.docx` at `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx` per the skill's Phase 5.
- **Surface-preference filter (Phase 6 Loop 2):** when the recap surfaces per-person/per-project callouts the CEO could act on, drop any the CEO has taught the system to stop surfacing — `from surface_preferences import load_surface_preferences, is_suppressed`; keep a callout only if `not is_suppressed(prefs, "friday-wrap", item_class=<class>, entity_id=<person/project id>)`. Missing store → no-op; the recap's counts and substrate are untouched.
- **First-Run Personalization (SPEC FRP1).** Apply the weekly-recap skill's "First-Run Personalization" section. Read the recap's knobs via `get_config(WORKSPACE, "weekly-recap", DEFAULTS)`. On the FIRST fire only (`not is_configured(WORKSPACE, "weekly-recap")`): `save_skill_config(WORKSPACE, "weekly-recap", DEFAULTS)` before rendering, then append the one-time **footer** form of the first-run block after the recap (this orchestrator is a markdown post, NOT a widget — the footer, not fr-items, is the correct transport here, and MUST-NOT rule 5 does not apply). The footer renders exactly once ever, gated by `is_configured`.

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

Append the fire receipt via the canonical helper (`shared/scripts/receipts.py`, v4.5.2 R1 — **NEVER hand-roll the receipt JSON**; hand-rolled shapes are the F-10b/F-49 drift class). This receipt is REQUIRED on every fire — friday-wrap ran receiptless all of dogfood week (F-39/F-43):

```python
from receipts import log_receipt
log_receipt(
    WORKSPACE_ROOT, "friday-wrap",
    fired_via=lateness["receipt_fired_via"],  # from Phase 2.9 — manual | scheduled | catchup; never guess it
    duration_ms=elapsed_ms,
    late_tier=lateness["tier"] if lateness["tier"] in ("note", "degrade") else None,
    extra_data={
        "recap_path": "_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx",
        # The window this fire ACTUALLY covered — Phase 3's catchup_window
        # result verbatim, never a re-derived [now-7d, now]. This receipt is
        # what the NEXT fire's window starts from. The NAIVE pair: this is
        # machine-local receipt math, not a connector query (F-1).
        "window_start": "<the catchup_window `start`>",
        "window_end": "<the catchup_window `end`>",
        "window_extended": <the catchup_window `extended` flag>,
        # SPEC CATCHUP1 F-2 — the coverage gate's output. OMIT THE KEY when
        # no capped read truncated; that omission is the positive assertion
        # "everything before this point is handled" and is the only thing
        # that collapses the next window back to 7 days.
        "window_incomplete_before": <receipt_window_marker(window, incomplete=<any capped read came back at its cap>)>,
        "events_captured": N, "commitments_found": N,
        "telemetry": {...},
    },
)
```

**The marker is computed, never hand-written** (`from catchup import receipt_window_marker`). It returns the ISO string or `None`, and `None` means drop the key from `extra_data` entirely. The spelling is `catchup.WINDOW_INCOMPLETE_FIELD` and nothing else — an improvised synonym is invisible to the reader that consumes it and silently re-opens the orphaning bug (F-50 P2c). If this fire captured nothing at all and the window it was handed already carried a marker, it is still `incomplete=True`: the marker carries forward rather than being cleared by a fire that never looked.

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
- Does NOT fire on weekends if the cron is configured Friday-only (default `0 13 * * 5` — Phase 3/R4; pre-Phase-3 installs registered at `0 16 * * 5` keep their time). Manual trigger of `weekly recap` on any other day still works via the skill's on-demand path.
- Does NOT replace `cleanup` (workspace health check) or `morning-brief` (daily digest). Different surfaces, different cadences.
