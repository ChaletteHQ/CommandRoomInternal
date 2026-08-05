# Orchestrator prompt — Morning Brief

This file is the EXACT prompt the bootloader cats and executes for `taskId: morning-brief`. Fires 7:00 AM weekdays local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES`. NEW in onboarding-v2 / 2026-05-17. First-install default (one of 3 tasks registered on a fresh workspace).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Morning Brief is a **markdown chat post**, not a widget — it's a digest, not an action surface. Renderer-validator gates do NOT apply here (no item-block parser, no button-action enforcement). The leak scanner DOES still apply (no entity-ID leaks, no email address leaks, no internal phase labels).

**Brief save path:** Morning Brief does NOT produce a `.docx` brief deliverable. It posts the digest inline and (optionally) saves a markdown snapshot to `_hq/briefings/morning-<YYYY-MM-DD>.md` per the morning-briefing skill's Step 5 saved-snapshot path. NEVER write to `_hq/staging/<today>/` (forbidden by the leak scanner — that path is reserved for scheduled-task email drafts).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface link blocks per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" pattern adapted for a non-widget post.

**Project routing:** thread / project resolution per `references/PROJECT_MAPPING_RULES.md`.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `morning-briefing` skill. The skill at `skills/morning-briefing/SKILL.md` is the source of truth for the digest's format, section ordering, urgency rules, and relationship-grouped thread layout. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) execute the morning-briefing skill's connector/context gather (Steps 1-4) verbatim, (c) run the ONE-command brief-pack driver as the LAST gathering step (T3.2 FB-18 — gather first, driver last, place immediately), (d) post the digest once, as PROSE, with every pack block placed and NO widget (FB-20), (e) log a `pack_run` event, (f) STOP.

---

## ⛔ STOP CONTRACT (v2.14.14+ — adapted for markdown post) — READ BEFORE YOU DO ANYTHING

**The markdown digest IS the chat turn. After it posts (plus any optional Links section), YOU STOP.** No exceptions, no edge cases. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered chat output to disk** outside the canonical `_hq/briefings/morning-<date>.md` snapshot path. Not to `_hq/scheduled_outputs/`, not to `_hq/staging/`, not anywhere else.

2. **No narrating what's in the digest.** The user can see it. Don't follow with "Total scan results: X events" / "Files saved to..." / "Here's a summary of what I just posted."

3. **No post-digest summary block.** The chat turn ends after the digest + Links section. (EXCEPTION: the morning-briefing skill's one-time First-Run Personalization footer — see Phase 3 — is part of the defined digest tail, like the scan-for-commitments nudge; it is NOT a summary block and is allowed on the first fire only, gated by `is_configured`.)

4. **No "regenerate with real data" mode.** If the user asks to re-fire, re-execute Phase 1 onward — don't switch to file-write mode.

5. **NO WIDGET. AT ALL. (FB-20 — M's ruling 2026-07-16.)** This surface never calls `mcp__visualize__show_widget` — there is no exception any more. The brief is a prose post, start to finish: the digest is markdown, the money carve-out is markdown sentences, the queue pointer is one markdown line. The old "ONE WIDGET EXCEPTION" (the LB1 "Needs your eyes" card, t3 FB-9, reordered T3.2 FB-18) is **RETIRED** — the driver no longer emits a `transport` block, so there are no bytes to post and nothing to relay. A widget rendered from a morning-brief fire is a contract violation on its own, regardless of how correct its contents are. Adjudication lives at the staff meeting; the pointer line hands off to it.

6. **HARD LINE — logging is not posting (T3.2 FB-18, carried into the prose-only brief by FB-20).** The driver call writing the `brief_state` event, the pack persisting to `_hq/.system/briefs/`, the digest snapshot saved to `_hq/briefings/`, and the Phase 5 fire receipt are all BOOKKEEPING, not delivery. A fire that logged every one of them and posted no digest to the chat did not run — it filed paperwork about a brief that never happened. This rule outlived the widget it was written for: FB-18's specific failure (bytes emitted, never relayed) is now impossible by construction — the driver emits no bytes — but the general law stands over the PROSE blocks. A turn is delivered when the digest is in the chat, not when the events are on disk. "I logged the state and the receipt" is never "the brief ran."

   **The receipt is owed on every completed fire, including the two no-delivery paths** (this is the carve-out that used to be tangled up in the widget relay — with the widget gone it reads clean, and there is no longer any tier on which a receipt can be withheld):
   - a **degrade-tier fire** (Phase 2.9) — the degrade notice is the entire output by design; the receipt still MUST be logged. Withholding it is the Bug #98 class (an invisible write losing to a suppressed deliverable).
   - a fire whose pack came back **entirely empty** — nothing to place is not an error, and the receipt still logs.

**Self-check before posting anything:** if you're about to write text AFTER the digest + Links section, ask: "is this required by spec?" If no → don't post it. **And the inverse check (t3 FB-9 / FB-20): before STOPPING, confirm every non-empty block of Phase 3.9's CR-BRIEF-PACK was placed — alarm lines at top, CHANGED lines cited, money sentences in the body, the queue-pointer line last, watchdog line in the tail. A turn that stops with an unplaced pack block is INVALID, not "done early." And confirm the inverse of the inverse (FB-20): `show_widget` was NOT called this turn. (Degrade-tier fires excepted per Phase 2.9 — the degrade notice is the whole output.)**

---

You are firing the Command Room "Morning Brief" chat. Today is the LOCAL date now. You're producing the morning digest before the user starts their workday.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — whether by cron or manual `re-run` trigger. Multiple fires per day are allowed. A `pack_run` event writes at the end of every fire for audit trail.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and the orchestrator file path. Continue with:

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)`. **v3.11.1+ contract:** `workspace_path` is REQUIRED — pass the resolved `<WORKSPACE>` path on every call (or set `CR_WORKSPACE` in the subprocess env). The prior walk-up resolver was removed because it never resolved inside the plugin clone and silently rendered UTC. If `to_local` raises `TZResolutionError`, surface "⚠️ Couldn't resolve workspace TZ — times shown as UTC" in the digest header and continue rendering with raw UTC; do not let the exception abort the fire.
- Every site in this orchestrator that renders a connector timestamp (Gmail `internalDate`, Calendar event start/end, Slack `ts`) MUST pass `workspace_path=<WORKSPACE>` to `to_local()` / `format_local()`. No exceptions.
- Read `<WORKSPACE>/_hq/data/entities.json`. Capture the primary user (`person` record where `is_primary_user: true`) — first name + email + timezone.
- Read `<WORKSPACE>/_hq/data/aliases.json` for canonicalization during connector scans.
- Read `<WORKSPACE>/CLAUDE.md` if it exists (hot cache for people, projects, terms — supplies most quick references without per-file reads).
- Read `<WORKSPACE>/_hq/MASTER_TRACKER.md` (project list, statuses, next actions, waiting-on).
- Resolve connectors through the seam: calendar + mail via `tool_discovery.discover_for_category(<category>, "<op>", tools, declared=connector_config.declared_backend(<category>))`, falling back to the `discover_*` helpers when no backend is declared (empty map = today's behavior, R4); Slack via `discover_slack_tool`. NEVER Zapier for reads (the seam excludes Zapier legs automatically). On drift (declared backend NOT PRESENT) in a scheduled fire: skip-and-flag per SHARED_CHAT_OUTPUT_PROTOCOL § Connector drift (R13) — never prompt from a silent fire. Per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE: Zapier is send-only; reads use native MCP.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'morning-brief', fired_via='<scheduled|manual>', env_date='<session date>')))
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
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (Phase 4 — the digest post): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Execute the morning-briefing skill (the connector/context gather — runs BEFORE the Phase 3.9 driver)

Read `skills/morning-briefing/SKILL.md`. Execute its Steps 1-4 verbatim against the current workspace + connectors — the skill steps below own the CONNECTOR half (calendar, mail, Slack) and the digest composition prep. The substrate blocks (counts, CHANGED lines, alarms, watchdog, the money sentences, the queue pointer) arrive from Phase 3.9's pack, which runs AFTER these steps as the LAST action before posting (T3.2 FB-18 — gather first, driver last, place immediately):

- **Step 1 — Load core context** (already done in Phase 2 above; do not re-read).
- **Step 2 — Scan connected sources.** Calendar today + tomorrow's first event; Mail unread/important from last 18h, filtered by people in PEOPLE.md + project-related subjects + flagged; Slack/Teams unread DMs and mentions from last 18h, plus project channels. Per the skill's caps: top 10 emails, top 5 Slack items. **Self-reply filter (v3.11.1 — REQUIRED):** apply the skill's Step 2 "Self-reply filter" verbatim. For every email-thread candidate, fetch the thread's latest message and compare `From:` to the primary user's email (from `entities.json`'s `is_primary_user: true` record). If the latest message is FROM the primary user, DROP the thread from Needs Attention and Overnight Inbox — the user already responded. This filter applies to scheduled fires; the default **in-inbox** query alone is insufficient because the mail search surfaces earlier inbound messages in threads the user has since replied to.
- **Step 3 — Check tracker for urgency.** Scan MASTER_TRACKER for overdue commitments, stale waiting-on items (7+ days), today's deadlines, urgent flags. Apply Step 3b commitments aggregation from `events.jsonl` (`type: commitment` not closed by a later `commitment_resolved` / `thread_resolved` event). The header counts come from **Phase 3.9's pack (`brief_state.headline` — the driver, which runs AFTER these gather steps, runs `compute_and_log_brief_state` internally; never call it yourself, never hand-compute the counts — leave the header slot to be filled from the pack in Phase 4)**, matching the skill's Step 3d — render its `counts["headline"]` buckets (you owe / owed to you / unowned / unconfirmed, plus overdue and stuck), the one bucket export (Phase 2 Stage A + v4.5.2 R4 + v4.6.0 MC2); never hand-compute them, never fold unowned/unconfirmed into a direction, and never label the overdue number "stuck" (R1b). `headline["stuck"]` is the REAL movement metric (MC2: no movement 21+ days, or blocked on a named person — `compute_and_log_brief_state` derives it automatically); render it as its own segment, and omit the segment when the key is absent (not computed, never 0). **Step 3a freshness overlay (v3.11.1 — REQUIRED):** apply the skill's Step 3a overlay verbatim — parse the tracker's `<!-- generated-at -->` stamp, and for every thread the digest will surface scan `events.jsonl` for events with `ts > tracker_stamp` and `primary_thread_id == thread.id`. Override `Last touched` / `Next Action` / `Waiting On` from those newer events. The tracker is a snapshot, not a live view; without this overlay a scheduled fire on a workspace whose tracker hasn't been regenerated in 10 days will surface stale "quiet since April 25" copy for threads that had activity today.
- **Step 4 — Build the digest (compose here, post in Phase 4 — after the Phase 3.9 driver).** Apply the relationship-grouped thread layout from the skill's Step 4: every thread's `affiliation_id` resolves to its org; primary-focus orgs render prominently; non-primary roll up under "OTHER ORGS" with `relationship_type` badges. Section headers use `canonical_name`, not hardcoded labels. Omit any section with no content — never pad. Number the Needs Attention items and keep the rendered order: the item→commitment-id mapping writes into the `pack_run` receipt as `needs_attention_ids` (Phase 5) so a later `mark done [n]` resolves. SUGGESTED FIRST MOVE at the end — one sentence. **Surface-preference filter (Phase 6 Loop 2):** before finalizing Needs Attention, drop any item the CEO has taught the system to stop surfacing — `from surface_preferences import load_surface_preferences, is_suppressed`; keep an item only if `not is_suppressed(prefs, "morning-brief", item_class=<class>, entity_id=<person/project id>)`. Missing store → no-op; the substrate is untouched.
- **Step 4b — Background-task watchdog line (v4.6.1 S3 — the light daily pass; receipts-only).** The line is Phase 3.9's pack field `watchdog_line` (the driver makes the ONE `task_watchdog.brief_watchdog_line` call — do NOT call it yourself, before or after). If it is not None, append it to the digest tail (after SUGGESTED FIRST MOVE, before the Links section) verbatim, ONCE — this placement and Phase 4's placement-map item 4 are the SAME action, not two lines — e.g. "2 of your background tasks need attention — say health check for the detail." That sentence is the ENTIRE pass: no per-task detail, no cause narration, no second scan — `health check` (system-health) owns the deep answer. `None` means nothing renders (all healthy, or this chat can't see the scheduler) — never pad an all-clear line into the brief.
- **Step 5 — Deliver: executes in PHASE 4, never here (T3.2 FB-18).** The digest cannot post before Phase 3.9's driver has run — its pack fills the header counts, the alarm lines, the money sentences, and the pointer line. When Phase 4 reaches it: in scheduled mode (this is one), follow the saved-snapshot path — write the rendered digest to `<WORKSPACE>/_hq/briefings/morning-<YYYY-MM-DD>.md` per the skill's "save to file" branch, AND post the digest inline in this chat turn.
- **First-Run Personalization (SPEC FRP1).** Apply the morning-briefing skill's "First-Run Personalization" section. Read the brief's knobs via `get_config(WORKSPACE, "morning-briefing", DEFAULTS)`. On the FIRST fire only (`not is_configured(WORKSPACE, "morning-briefing")`): `save_skill_config(WORKSPACE, "morning-briefing", DEFAULTS)` before rendering, then append the one-time **footer** form of the first-run block after the digest (this orchestrator is a markdown post, NOT a widget, so the footer — not fr-items — is the correct transport, and MUST-NOT rule 5 does not apply). The footer renders exactly once ever, gated by `is_configured`.

Every connector read MUST emit corresponding events to `<WORKSPACE>/_hq/data/events.jsonl` per `shared/PASSIVE_CAPTURE.md`. Use `atomic_append_jsonl` from `shared/scripts/atomic_write.py` for batched appends. Dedup via `source_ref_hash` so re-fires don't double-count overnight email reads.

# Phase 3.9 — The one-command brief pack (t3 FB-9; de-carded FB-20 — the LAST gathering step, prose only)

**Why this phase runs LAST (T3.2 FB-18, kept):** a live 2026-07-16 scheduled fire ran this driver FIRST, then ~10 connector/context steps, and the pack's blocks went unplaced — a driver whose output sits 10 steps behind the post step gets forgotten. Gather first, driver last, place immediately. That ordering stands even though the widget it originally protected is gone (FB-20): the pack's prose blocks are just as skippable as a widget was.

```bash
python3 shared/scripts/surface_drivers.py morning-brief \
    --workspace "<WORKSPACE>" --mode <scheduled|manual per Phase 2.9's run mode>
```

The driver prints exactly ONE line: `CR-BRIEF-PACK: {json}`. That is the whole contract.

**⛔ THE BRIEF RENDERS NO WIDGET (FB-20 — M's ruling 2026-07-16, "the morning brief should just be a morning brief").** This surface is READ-ONLY. It has no card, no rows, no buttons, no `show_widget` call, and no relay obligation — the driver emits no `CR-WIDGET-HTML` block and no `CR-REQUIRED-NEXT-STEP` banner, because there are no bytes to relay. **Do NOT call `mcp__visualize__show_widget` from this orchestrator, ever, for any reason** — not for the pack, not to "helpfully" render the queue, not as a fallback when the prose feels thin. A widget posted from a morning-brief fire is a CONTRACT VIOLATION even if it renders beautifully. Adjudication happens at the **staff meeting** (Mon/Wed/Fri, or any time the user says `staff meeting`) — that is the sole surface where items get confirmed, and the pointer line below is how the brief hands off to it.

**Every non-empty pack field is a MANDATORY placement in this turn** (the placement map lives in Phase 4):

- `alarm_lines` → verbatim at the very TOP of the digest prose, above the synthesis lead (FS-04/05/06/15).
- `changed.lines` → the CHANGED contract line MUST cite them (FS-09 — never "Nothing material" over a non-empty feed).
- `brief_state.headline` → the header counts. The driver already ran `compute_and_log_brief_state` (the Step-3d derivation — `commitment_state.compute_brief_state` under the hood) and logged the `brief_state` audit event — do NOT call it again; render the pack's numbers.
- `brief_state.needs_attention` → the NEEDS ATTENTION rows, ALREADY CAPPED and ALREADY RANKED by the driver (CAPTUREFLOW §D — at most 5, due-then-age). Render them in the order given and do NOT add rows back from anywhere; when `brief_state.needs_attention_more_line` is non-empty, print it verbatim as that section's last line. The lane is bound at the RENDER, never at the derivation: `brief_state.headline` still counts everything, because a cap is a render bound and never a silence.
- `watchdog_line` → digest tail, verbatim (after SUGGESTED FIRST MOVE, before Links).
- `money_lines` → verbatim, one sentence each (FB-20's ONE carve-out — see Phase 4's map item 5).
- `queue_pointer.line` → verbatim, ONE line, the digest's last content line (map item 6).

Run the driver ONCE per fire (idempotent-single-call — a re-run to "refresh" double-logs the brief state, the RV-3 class). In a `degrade`-tier fire the driver still runs (its writes are substrate the task owes) but nothing renders except the degrade notice.

# Phase 4 — Post the digest (+ the pack placements)

**Order (FB-20):** there is no relay step. Output the rendered markdown digest as the chat turn body. Follow the exact format from `skills/morning-briefing/SKILL.md` Step 4 — header line, optional commitments line, calendar section, NEEDS ATTENTION, OVERNIGHT INBOX, per-org thread sections, SUGGESTED FIRST MOVE.

**Pack placement map (t3 FB-9 / FB-20 — every non-empty CR-BRIEF-PACK block lands, no exceptions):**

1. `alarm_lines` — verbatim, pinned at the very top of the digest prose (above even the escalated-reminders block and the synthesis lead).
2. `changed.lines` — folded into the CHANGED contract line (one narration slot; substance first).
3. `brief_state.headline` — the commitments header counts, rendered per the skill's Step 3d rules (omit absent keys, never 0-pad).
3b. `brief_state.needs_attention` + `needs_attention_more_line` — the NEEDS ATTENTION section (CAPTUREFLOW §D 2026-08-01). The driver hands you at most **5** rows, already ranked due-then-age, and the pointer line for the rest. Render exactly those rows in exactly that order; never re-rank them, never top the section up from your own Step-3 scan, and never drop the pointer line to save a line. The lane was unbounded before this (72 rows on a live workspace) and an unreadable lane is an unread one. A 14-day rotation inside the driver pins any item that has been below the fold too long into the visible set, so nothing is suppressed forever — which is exactly why you must not reorder what you were handed. `needs_attention_total` is the honest full count if you need to say one; the header counts in `brief_state.headline` are unfiltered and stay that way.
4. `watchdog_line` — verbatim in the digest tail.
5. `money_lines` — verbatim, one sentence each, in the digest body (place with NEEDS ATTENTION). **The ONE money carve-out (FB-20):** a deal signal is the single class the brief still names outright, because a deal that goes quiet for a day is the one silence with a price tag. These sentences are PROPOSE-ONLY and carry no verbs — each one already routes the user to `staff meeting`, which is where the confirm happens by chat phrase. Never add buttons to them, never invent a "confirm?" affordance, never act on one from this turn. Empty list → nothing renders; **never** pad an all-clear ("no new deals today" — never).
6. `queue_pointer.line` — verbatim, exactly ONE line, as the digest's last content line before SUGGESTED FIRST MOVE. It is the brief's entire handoff to the adjudication surface. The count is the driver's, computed from the same projector the staff meeting renders — **never recount it, never adjust it, never round it, never soften it** ("a few things need your eyes" is a lie about a number you were handed). Empty line (nothing queued) → nothing renders.

Before STOP: re-check the pack against what posted. An unplaced non-empty block = the turn is incomplete — place it, then stop. **A logged receipt never substitutes for an unplaced block (STOP-contract rule 6).**

**Tone:** crisp, direct. Opening order per the skill's Tone section: (1) the personified intro line (`"Morning, {first_name} — {brain_name} here with today's read."` — the ONLY greeting), (2) the digest header, (3) the synthesis lead. No other greeting ("Good morning!" / "Here's what's happening." — never). After that it's a status board, not a conversation.

**Voice match:** if `<WORKSPACE>/_hq/.claude/brand-voice-guidelines.md` exists, match user voice for the SUGGESTED FIRST MOVE line. Otherwise neutral professional.

After the digest, if any briefs or files were referenced (Past Meetings docs, Upcoming Meetings prep docs from yesterday's fires), add a **Links:** section per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" — one bulleted line per linked file, `computer://` artifact URLs. Skip the Links section entirely if nothing connects.

# Phase 5 — Log the fire + close

**Gate before logging (T3.2 FB-18 → FB-20):** the receipt is bookkeeping, not delivery. If Phase 4's digest has not been posted to the chat, do NOT log — post it first, then return here. A receipt over an undelivered digest is a contract violation (STOP-contract rule 6), not a completed fire. (Sole exception, same as rule 6: a degrade-tier fire per Phase 2.9 — nothing renders except the degrade notice, and the receipt still MUST be logged; withholding it is the Bug #98 class.) The old widget half of this gate is retired with FB-20 — there is no widget to relay and no relay to gate on; if you called `show_widget` this turn, that is the violation, not the omission.

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
