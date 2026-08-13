# Orchestrator prompt — Morning Brief

This file is the EXACT prompt the bootloader cats and executes for `taskId: morning-brief`. Fires 7:00 AM weekdays local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES`. NEW in onboarding-v2 / 2026-05-17. First-install default (one of 3 tasks registered on a fresh workspace).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Morning Brief is a **markdown chat post**, not a widget — it's a digest, not an action surface. Renderer-validator gates do NOT apply here (no item-block parser, no button-action enforcement). The leak scanner DOES still apply (no entity-ID leaks, no email address leaks, no internal phase labels).

**Brief save path:** Morning Brief does NOT produce a `.docx` brief deliverable. It posts the digest inline and (optionally) saves a markdown snapshot to `_hq/briefings/morning-<YYYY-MM-DD>.md` per the morning-briefing skill's Step 5 saved-snapshot path. NEVER write to `_hq/staging/<today>/` (forbidden by the leak scanner — that path is reserved for scheduled-task email drafts).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface link blocks per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" pattern adapted for a non-widget post.

**Project routing:** thread / project resolution per `references/PROJECT_MAPPING_RULES.md`.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `morning-briefing` skill. The skill at `skills/morning-briefing/SKILL.md` is the source of truth for the digest's format, section ordering, urgency rules, and relationship-grouped thread layout. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) run the PREP LEG first (Phase 2.95 — SPEC BRIEFMERGE §A), (c) execute the morning-briefing skill's connector/context gather (Steps 1-4) verbatim, (d) run the ONE-command brief-pack driver as the LAST gathering step (T3.2 FB-18 — gather first, driver last, place immediately), (e) COMPOSE the digest as PROSE with every pack block placed and NO widget (FB-20) and save the snapshot (Phase 4), (f) log ONE combined `pack_run` receipt covering both legs (Phase 5), (g) POST the digest once (Phase 6), (h) STOP.

**Receipt BEFORE post (SPEC BRIEFFIX1 Item C, M's ruling 2026-08-09).** The receipt is written in Phase 5 and the digest posts in Phase 6, in that order, and the order is the point. Both orders lose something when a fire dies in the middle; they do not lose the same thing. Receipt-then-post leaves a receipt and no post — which the degrade tier already blesses (a write without a surface is a state this product has always accepted) and which the next fire can see. Post-then-receipt leaves a digest on screen that the substrate has no record of: the watchdog reads it as a fire that never happened, and `mark done [n]` resolves against an OLDER brief's numbering, so a one-tap close lands on the wrong item silently. That is the Bug #98 class with a wrong-close on top, and it happened on 2026-08-09. If the post then fails, the fire is still auditable. Do NOT add a "posted!" confirmation event to compensate — the receipt shape is unchanged and every existing reader must keep working.

**Two legs, one fire (SPEC BRIEFMERGE, M's ruling 2026-08-08).** The separate Upcoming Meetings chat is RETIRED; its prep generation is this fire's first leg. Two things follow and neither is optional: prep runs BEFORE the digest composes, so the meeting section links files that already exist; and the prep leg can NEVER kill the brief — every failure degrades to a line and the brief always renders (Phase 2.95). The `.docx` prep briefs this leg writes to `_hq/meetings/` are documented deliverables, separate from the chat-output surface the STOP CONTRACT governs.

---

## ⛔ STOP CONTRACT (v2.14.14+ — adapted for markdown post) — READ BEFORE YOU DO ANYTHING

**The markdown digest IS the chat turn. After it posts (plus any optional Links section), YOU STOP.** No exceptions, no edge cases. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered chat output to disk** outside the canonical `_hq/briefings/morning-<date>.md` snapshot path. Not to `_hq/scheduled_outputs/`, not to `_hq/staging/`, not anywhere else.

2. **No narrating what's in the digest.** The user can see it. Don't follow with "Total scan results: X events" / "Files saved to..." / "Here's a summary of what I just posted."

3. **No post-digest summary block.** The chat turn ends after the digest + Links section. (EXCEPTION: the morning-briefing skill's one-time First-Run Personalization footer — see Phase 3 — is part of the defined digest tail, like the scan-for-commitments nudge; it is NOT a summary block and is allowed on the first fire only, gated by `is_configured`.)

4. **No "regenerate with real data" mode.** If the user asks to re-fire, re-execute Phase 1 onward — don't switch to file-write mode.

5. **NO WIDGET. AT ALL. (FB-20 — M's ruling 2026-07-16.)** This surface never calls `mcp__visualize__show_widget` — there is no exception any more. The brief is a prose post, start to finish: the digest is markdown, the money carve-out is markdown sentences, the queue pointer is one markdown line. The old "ONE WIDGET EXCEPTION" (the LB1 "Needs your eyes" card, t3 FB-9, reordered T3.2 FB-18) is **RETIRED** — the driver no longer emits a `transport` block, so there are no bytes to post and nothing to relay. A widget rendered from a morning-brief fire is a contract violation on its own, regardless of how correct its contents are. Adjudication lives at the staff meeting; the pointer line hands off to it.

6. **HARD LINE — logging is not posting (T3.2 FB-18, carried into the prose-only brief by FB-20).** The driver call writing the `brief_state` event, the pack persisting to `_hq/.system/briefs/`, the digest snapshot saved to `_hq/briefings/`, and the Phase 5 fire receipt are all BOOKKEEPING, not delivery. A fire that logged every one of them and posted no digest to the chat did not run — it filed paperwork about a brief that never happened. This rule outlived the widget it was written for: FB-18's specific failure (bytes emitted, never relayed) is now impossible by construction — the driver emits no bytes — but the general law stands over the PROSE blocks. A turn is delivered when the digest is in the chat, not when the events are on disk. "I logged the state and the receipt" is never "the brief ran." **BRIEFFIX1 Item C sharpens this rather than softening it:** the receipt now goes FIRST, so the sequence a stopped fire leaves behind is receipt-without-post rather than post-without-receipt. That does not license stopping there. Phase 6 is owed on every non-degrade fire, and a turn that ends after Phase 5 is incomplete — the difference is only that it is now an incomplete turn the substrate can SEE.

   **The receipt is owed on every completed fire, including the two no-delivery paths** (this is the carve-out that used to be tangled up in the widget relay — with the widget gone it reads clean, and there is no longer any tier on which a receipt can be withheld):
   - a **degrade-tier fire** (Phase 2.9) — the degrade notice is the entire output by design; the receipt still MUST be logged. Withholding it is the Bug #98 class (an invisible write losing to a suppressed deliverable).
   - a fire whose pack came back **entirely empty** — nothing to place is not an error, and the receipt still logs.

**Self-check before posting anything:** if you're about to write text AFTER the digest + Links section, ask: "is this required by spec?" If no → don't post it. **And the inverse check (t3 FB-9 / FB-20): before STOPPING, confirm the Phase 5 receipt landed and every non-empty block of Phase 3.9's CR-BRIEF-PACK was placed — alarm lines at top, CHANGED lines cited, money sentences in the body, the queue-pointer line last, watchdog line in the tail. A turn that stops with an unplaced pack block is INVALID, not "done early." And confirm the inverse of the inverse (FB-20): `show_widget` was NOT called this turn. (Degrade-tier fires excepted per Phase 2.9 — the degrade notice is the whole output.)**

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
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (Phase 6 — the digest post): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 2.95 — THE PREP LEG (SPEC BRIEFMERGE §A/§B/§C — runs FIRST, before the Phase 3 gather)

**Why this phase is first.** Prep docs must exist before the digest composes its meeting section, or the brief links files that are not there yet. This is the whole ordering ruling: prep generation → brief render. Nothing in Phase 3 or Phase 4 re-discovers today's meetings — the digest reads what THIS leg returned (§A: no second discovery pass).

**Why it can never kill the brief.** The prep leg is wrapped in `shared/scripts/prep_leg.py`, which catches at two levels: one meeting failing degrades to a line in the brief and the loop continues; the discovery step failing degrades the WHOLE leg to one banner and no per-meeting rows. `run_prep_leg` does not raise. If you ever find yourself writing a `try` around this phase to protect the fire, the fence is already there and you are about to add a second one that hides it.

**Degrade-tier fires skip the leg deliberately.** When Phase 2.9 returned `degrade`, do NOT generate prep: nothing renders on that tier, so the customer would never receive the links, and a `prep_brief` receipt written for a brief they never saw would make tomorrow's no-prep detector call the meeting prepped. Record the leg with `prep_leg.skipped_leg()` — the constructor, never a hand-rolled dict — still write the Phase 5 combined receipt with `brief_status="degraded"`, and move on. A skip is a decision, not a failure: the receipt says `skipped` and the watchdog raises nothing over it.

```python
from prep_leg import skipped_leg
leg = skipped_leg()          # reason defaults to prep_leg.SKIP_DEGRADE_TIER
```

**Step A — discover today's meetings (the fire's ONE calendar pass).**

Pull today's events in the workspace timezone using `clock["today"]` from Phase 2.9 — never this computer's clock. This is the same fetch the morning-briefing skill's Step 2 "Calendar" bullet needs, so make it HERE, once, and carry the result into Phase 3 rather than issuing it twice. (Step 3c-bis's wide scheduling-verification window is a different query and still issues its own — see the skill's Step 2 note.)

Filters, carried verbatim from the retired Upcoming Meetings chat so nothing changes about WHICH meetings get prepped:

- **Drop already-passed meetings.** Any event whose end time is before now (workspace TZ) is out. Meetings in progress are IN — the CEO may still walk in mid-meeting.
- **Keep internal AND external business meetings.** Internal-only calls get project-context prep (recent project events, open commitments either way, prior decisions), not external-prep sections.
- **Drop personal calls** — no business-domain attendee at all.
- **Solo blocks** (the CEO is the only attendee) get a project-context brief when the title or routing resolves to an active project; a block that routes nowhere and gives no signal is personal time, skipped.

**Step B — honor the call-prep `auto_fire` knob before prepping anything.**

```bash
CR_WORKSPACE=<WORKSPACE> python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from skill_config_writer import get_config
print(json.dumps(get_config('<workspace_root>', 'call-prep', {'auto_fire': '24h'})))
"
```

- **`24h`** (default) — prep every kept meeting.
- **`morning_of`** — prep only meetings starting TODAY in the workspace timezone.
- **`off`** — generate NO `.docx` on this scheduled fire. Every kept meeting is recorded `skipped` with that reason; the brief's calendar section is unchanged, and the fire still writes every substrate record it owes (a suppressed deliverable must never drop a silent write — Bug #98 class). Say nothing about the knob and never name it.

This gate governs the SCHEDULED leg only. A manual "prep me for my 2pm" always runs `call-prep` regardless.

**Step C — run the leg.**

```python
# (Inside python3, after the Rule 22 preamble + sys.path.insert)
from prep_leg import run_prep_leg

leg = run_prep_leg(discover, generate, workspace_root=WORKSPACE_ROOT)
```

`discover()` returns the Step A meetings as `{"meeting_id": <calendar event id>, "title": ..., "time_label": "2:15", "start": <the instance's own start, ISO from the calendar>}` — `time_label` is what the degrade line says back to the CEO, so it is the same short form the calendar section prints ("2:15", not "14:15" and not a full timestamp). **`start` is REQUIRED for reuse to be possible (BRIEFFIX1 Item B).** A calendar id is stable across a recurring series, so the id alone cannot say which standup a prep was written for; the start is the field that differs. Pass the calendar's own start value — a meeting handed over without one is never reused, it is regenerated, which is safe and merely wasteful.

**A meeting that is already prepped is `reused`, not re-generated and not re-labelled (SPEC BRIEFFIX1 Item B).** `run_prep_leg` asks the substrate — `prep_brief` receipts, F-29, never a folder listing — whether a prep was written TODAY for THIS MEETING INSTANCE (the calendar id AND the instance's own start), and when one was it does NOT call the generator. The instance half is not optional: a recurring meeting's id is identical every week, so an id-only test hands over last week's document, and an age-based window cannot help because for anything daily or weekday-cadenced the window sits inside the recurrence interval. Anything unproven — no start on the receipt, no start on the meeting, a prep from an earlier day — regenerates. The outcome is `reused`, it renders the SAME link line the CEO wants, and the row records the `source_receipt_seq` it leaned on. Do not force a rebuild to "earn" the link: a fresh document that duplicates a fresh document is work for the machine's benefit. Do not report it as `ran` either — `ran` means this fire generated something, and on 2026-08-09 a fire reported `ran` over a prep built six hours earlier, which is how a stale document was handed over as fresh. The word for the true thing exists now; use it. The leg applies this per meeting and hands you the result — there is nothing to decide here beyond not overriding it.

`generate(meeting)` runs **`skills/call-prep/SKILL.md` end to end** for that meeting and returns `{"brief_path": <workspace-relative>}`, or `None` for a deliberate skip. There is no scheduled variant of the generator: the ONE-GENERATOR CONTRACT (v4.5.2 S1) says depth comes only from the call-prep `depth` setting, never from which path fired, and this leg is now the only scheduled caller. Everything that governs a prep brief — the five-block gathering, the deliverable render gate, the owed-table pending split, the name-spelling rule, the visual pass, and the `receipts.log_prep_receipt` call — lives in that skill and is read fresh at fire time. Three arguments differ from the on-demand path: pass `generated_by="morning-brief"`, `fired_via=<the Phase 2.9 receipt_fired_via>`, and `meeting_start=<the meeting's own start>` to `log_prep_receipt`. The last one is what lets tomorrow's fire tell this prep apart from the next instance of the same recurring meeting; omit it and every future fire regenerates instead of reusing.

**Step D — paths are WORKSPACE-RELATIVE (SPEC BRIEFMERGE §C — the attachment-rot fix).**

Every file-pointer this fire persists — `brief_path` on a prep receipt, `digest_path` on the fire receipt — is stored workspace-relative (`_hq/meetings/<file>.docx`), never absolute. An absolute path is valid only on the machine and in the session that wrote it: a fire in a cloud session writes pointers that are dead the moment the session ends, and a fire on one computer writes pointers the other computer cannot open. Convert with `workspace_paths.to_workspace_relative(path, WORKSPACE_ROOT)` and let `workspace_paths.assert_workspace_relative` refuse anything absolute at the write — `run_prep_leg` and `prep_leg.log_combined_receipt` both call it for you, so the only way to persist an absolute path from this fire is to hand-roll a write around them. Don't.

Legacy rows keep their absolute values forever — events.jsonl is append-only history and nothing here rewrites it. `workspace_paths.normalize_persisted_path` resolves them at READ time instead.

**Step E — carry the result forward. Both handoffs are mandatory:**

- Phase 3 Step 2 builds `todays_meetings` from the leg's meetings — no second calendar query.
- Phase 4 renders the meeting-section lines from `prep_leg.meeting_lines(leg, workspace_root=WORKSPACE_ROOT)`. That helper REFUSES to run without the leg's result (`LegNotRunError`) — the ordering fence in code, not in convention.

# Phase 3 — Execute the morning-briefing skill (the connector/context gather — runs BEFORE the Phase 3.9 driver)

Read `skills/morning-briefing/SKILL.md`. Execute its Steps 1-4 verbatim against the current workspace + connectors — the skill steps below own the CONNECTOR half (calendar, mail, Slack) and the digest composition prep. The substrate blocks (counts, CHANGED lines, alarms, watchdog, the money sentences, the queue pointer) arrive from Phase 3.9's pack, which runs AFTER these steps as the LAST action before posting (T3.2 FB-18 — gather first, driver last, place immediately):

- **Step 1 — Load core context** (already done in Phase 2 above; do not re-read).
- **Step 2 — Scan connected sources.** **Today's calendar is ALREADY IN HAND from Phase 2.95's prep leg — reuse it; do not query the calendar again for today (SPEC BRIEFMERGE §A: no second discovery pass).** Add only tomorrow's first event, which the leg does not need. Build `todays_meetings` for Step 3d from the leg's meetings, and take each meeting's prep status from the leg's outcomes rather than re-deriving it. Calendar today + tomorrow's first event; Mail unread/important from last 18h, filtered by people in PEOPLE.md + project-related subjects + flagged; the declared chat backend's unread DMs and mentions from last 18h, plus project channels (resolve the tool with `tool_discovery.discover_chat_tool` — never name a chat product). Per the skill's caps: top 10 emails, top 5 chat items. **Chat context leg (SPEC CHATSCAN1 §C) — a leg INSIDE this same Step 2, never a second sweep:** run `chat_context.run_chat_context(workspace_root, chat_messages, tracked_entities, provider=chat_seam.resolve_chat_provider(workspace_root), scan_plan=chat_seam.plan_scan(provider), budget=ReadBudget())` over the fetch you already have. Its `context_line` is ONE sentence placed inside an existing section and is NEVER a row — the needs-attention lane still shows at most 5, and the brief's row count must be identical with this leg on and off. An undeclared chat backend returns a skipped block: render nothing, say nothing. Append `coverage_note` verbatim to any line that would otherwise read as full chat coverage. **Self-reply filter (v3.11.1 — REQUIRED):** apply the skill's Step 2 "Self-reply filter" verbatim. For every email-thread candidate, fetch the thread's latest message and compare `From:` to the primary user's email (from `entities.json`'s `is_primary_user: true` record). If the latest message is FROM the primary user, DROP the thread from Needs Attention and Overnight Inbox — the user already responded. This filter applies to scheduled fires; the default **in-inbox** query alone is insufficient because the mail search surfaces earlier inbound messages in threads the user has since replied to.
- **Step 3 — Check tracker for urgency.** Scan MASTER_TRACKER for overdue commitments, stale waiting-on items (7+ days), today's deadlines, urgent flags. Apply Step 3b commitments aggregation from `events.jsonl` (`type: commitment` not closed by a later `commitment_resolved` / `thread_resolved` event). The header counts come from **Phase 3.9's pack (`brief_state.headline` — the driver, which runs AFTER these gather steps, runs `compute_and_log_brief_state` internally; never call it yourself, never hand-compute the counts — leave the header slot to be filled from the pack in Phase 4)**, matching the skill's Step 3d — render its `counts["headline"]` buckets (you owe / owed to you / unowned / unconfirmed, plus overdue and stuck), the one bucket export (Phase 2 Stage A + v4.5.2 R4 + v4.6.0 MC2); never hand-compute them, never fold unowned/unconfirmed into a direction, and never label the overdue number "stuck" (R1b). `headline["stuck"]` is the REAL movement metric (MC2: no movement 21+ days, or blocked on a named person — `compute_and_log_brief_state` derives it automatically); render it as its own segment, and omit the segment when the key is absent (not computed, never 0). **Step 3a freshness overlay (v3.11.1 — REQUIRED):** apply the skill's Step 3a overlay verbatim — parse the tracker's `<!-- generated-at -->` stamp, and for every thread the digest will surface scan `events.jsonl` for events with `ts > tracker_stamp` and `primary_thread_id == thread.id`. Override `Last touched` / `Next Action` / `Waiting On` from those newer events. The tracker is a snapshot, not a live view; without this overlay a scheduled fire on a workspace whose tracker hasn't been regenerated in 10 days will surface stale "quiet since April 25" copy for threads that had activity today.
- **Step 4 — Build the digest (compose here, post in Phase 6 — after the Phase 3.9 driver and after the Phase 5 receipt).** Apply the relationship-grouped thread layout from the skill's Step 4: every thread's `affiliation_id` resolves to its org; primary-focus orgs render prominently; non-primary roll up under "OTHER ORGS" with `relationship_type` badges. Section headers use `canonical_name`, not hardcoded labels. Omit any section with no content — never pad. Number the Needs Attention items and keep the rendered order: the item→commitment-id mapping writes into the `pack_run` receipt as `needs_attention_ids` (Phase 5) so a later `mark done [n]` resolves. SUGGESTED FIRST MOVE at the end — one sentence. **Surface-preference filter (Phase 6 Loop 2):** before finalizing Needs Attention, drop any item the CEO has taught the system to stop surfacing — `from surface_preferences import load_surface_preferences, is_suppressed`; keep an item only if `not is_suppressed(prefs, "morning-brief", item_class=<class>, entity_id=<person/project id>)`. Missing store → no-op; the substrate is untouched.
- **Step 4b — Background-task watchdog line (v4.6.1 S3 — the light daily pass; receipts-only).** The line is Phase 3.9's pack field `watchdog_line` (the driver makes the ONE `task_watchdog.brief_watchdog_line` call — do NOT call it yourself, before or after). If it is not None, append it to the digest tail (after SUGGESTED FIRST MOVE, before the Links section) verbatim, ONCE — this placement and Phase 4's placement-map item 4 are the SAME action, not two lines — e.g. "2 of your background tasks need attention — say health check for the detail." That sentence is the ENTIRE pass: no per-task detail, no cause narration, no second scan — `health check` (system-health) owns the deep answer. `None` means nothing renders (all healthy, or this chat can't see the scheduler) — never pad an all-clear line into the brief.
- **Step 5 — Deliver: executes in PHASES 4-6, never here (T3.2 FB-18).** The digest cannot post before Phase 3.9's driver has run — its pack fills the header counts, the alarm lines, the money sentences, and the pointer line. The delivery is then split across three phases and the split is load-bearing (BRIEFFIX1 Item C): Phase 4 composes it and, in scheduled mode (this is one), writes the rendered digest to `<WORKSPACE>/_hq/briefings/morning-<YYYY-MM-DD>.md` per the skill's "save to file" branch; Phase 5 logs the receipt; Phase 6 posts it inline in this chat turn.
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

# Phase 4 — Compose the digest (+ the pack placements) and save the snapshot

**Order (FB-20 + BRIEFFIX1 Item C):** there is no relay step, and there is no post step HERE either — this phase produces the digest text and writes it to disk; Phase 5 receipts it; Phase 6 posts it. Build the rendered markdown digest in the exact format from `skills/morning-briefing/SKILL.md` Step 4 — header line, optional commitments line, calendar section, NEEDS ATTENTION, OVERNIGHT INBOX, per-org thread sections, SUGGESTED FIRST MOVE.

**Pack placement map (t3 FB-9 / FB-20 — every non-empty CR-BRIEF-PACK block lands, no exceptions):**

1. `alarm_lines` — verbatim, pinned at the very top of the digest prose (above even the escalated-reminders block and the synthesis lead).
2. `changed.lines` — folded into the CHANGED contract line (one narration slot; substance first).
3. `brief_state.headline` — the commitments header counts, rendered per the skill's Step 3d rules (omit absent keys, never 0-pad).
3b. `brief_state.needs_attention` + `needs_attention_more_line` — the NEEDS ATTENTION section (CAPTUREFLOW §D 2026-08-01). The driver hands you at most **5** rows, already ranked due-then-age, and the pointer line for the rest. Render exactly those rows in exactly that order; never re-rank them, never top the section up from your own Step-3 scan, and never drop the pointer line to save a line. The lane was unbounded before this (72 rows on a live workspace) and an unreadable lane is an unread one. A 14-day rotation inside the driver pins any item that has been below the fold too long into the visible set, so nothing is suppressed forever — which is exactly why you must not reorder what you were handed. `needs_attention_total` is the honest full count if you need to say one; the header counts in `brief_state.headline` are unfiltered and stay that way.
4. `watchdog_line` — verbatim in the digest tail.
5. `money_lines` — verbatim, one sentence each, in the digest body (place with NEEDS ATTENTION). **The ONE money carve-out (FB-20):** a deal signal is the single class the brief still names outright, because a deal that goes quiet for a day is the one silence with a price tag. These sentences are PROPOSE-ONLY and carry no verbs — each one already routes the user to `staff meeting`, which is where the confirm happens by chat phrase. Never add buttons to them, never invent a "confirm?" affordance, never act on one from this turn. Empty list → nothing renders; **never** pad an all-clear ("no new deals today" — never).
6. `queue_pointer.line` — verbatim, exactly ONE line, as the digest's last content line before SUGGESTED FIRST MOVE. It is the brief's entire handoff to the adjudication surface. The count is the driver's, computed from the same projector the staff meeting renders — **never recount it, never adjust it, never round it, never soften it** ("a few things need your eyes" is a lie about a number you were handed). Empty line (nothing queued) → nothing renders.

7. **Prep-leg lines (SPEC BRIEFMERGE §A/§B)** — `prep_leg.meeting_lines(leg, workspace_root=<WORKSPACE>)`, rendered inside the existing Today's-calendar section. They are LINES, not a section: a whole-leg degrade contributes ONE banner line, a per-meeting failure contributes ONE line naming the meeting and the phrase that regenerates it, a successful prep contributes the workspace-relative link (or the honest `syncing — open from your cloud drive` line when the workspace's cloud platform — Google Drive, OneDrive, or SharePoint — has not landed the file on this machine yet; never a dead card), and a deliberate skip contributes NOTHING. The merged fire must not grow the brief past its existing caps, and an all-clear pad ("all meetings prepped") is forbidden the same way every other all-clear in this surface is.

Before moving on: re-check the pack against what you composed. An unplaced non-empty block = the turn is incomplete — place it, then continue. **A logged receipt never substitutes for an unplaced block (STOP-contract rule 6).**

**Tone:** crisp, direct. Opening order per the skill's Tone section: (1) the personified intro line (`"Morning, {first_name} — {brain_name} here with today's read."` — the ONLY greeting), (2) the digest header, (3) the synthesis lead. No other greeting ("Good morning!" / "Here's what's happening." — never). After that it's a status board, not a conversation.

**Voice match:** if `<WORKSPACE>/_hq/.claude/brand-voice-guidelines.md` exists, match user voice for the SUGGESTED FIRST MOVE line. Otherwise neutral professional.

**Save the snapshot NOW, in the persisted form.** Write the composed digest verbatim to `<WORKSPACE>/_hq/briefings/morning-<YYYY-MM-DD>.md` — the same snapshot Phase 3's Step 5 names, taken here because the text is final here. Every document pointer inside it stays WORKSPACE-RELATIVE (the `_hq/meetings/…` form Step D pinned): that is what makes one file mean one file on the other computer, and BRIEFMERGE §C exists to keep it that way. Do NOT save the converted chat form; the conversion happens in Phase 6, to the posted copy only.

# Phase 5 — Log the fire (BEFORE the post)

**Gate before logging (T3.2 FB-18 → FB-20 → BRIEFFIX1 Item C):** the receipt is bookkeeping, not delivery, and that has not changed — what changed is the ORDER. Log the receipt once the digest is COMPOSED and the snapshot is written, before Phase 6 posts it. The old gate said the reverse ("if the digest has not been posted, do not log") and the reverse is what produced a posted brief with no receipt on 2026-08-09: a fire that dies between the two now leaves a receipt and no post, which the degrade tier already blesses and the next fire can see, instead of a digest the substrate has no record of. (A degrade-tier fire per Phase 2.9 renders nothing except the degrade notice and the receipt still MUST be logged; withholding it is the Bug #98 class.) The widget half of this gate is retired with FB-20 — there is no widget to relay and no relay to gate on; if you called `show_widget` this turn, that is the violation, not the omission.

**This does NOT license stopping here.** The receipt is not the deliverable. A turn that logs and does not reach Phase 6 is an incomplete turn — it is simply now an incomplete turn that leaves a trace.

Build the telemetry block via `shared/scripts/telemetry.py` `build_pack_run_telemetry()` — same pattern as the other 5 orchestrators. Track connector calls + prompt/response sizes + duration. Merge into `pack_run.data` as `telemetry: {...}`. Silent — never narrated to chat. (v3.5.0+ — morning-brief was the only orchestrator missing this; `usage report` aggregation was incomplete for morning-brief fires until then.)

Append **ONE combined receipt covering both legs** via `shared/scripts/prep_leg.py` (SPEC BRIEFMERGE §D). It writes the SAME `pack_run` shape `receipts.log_receipt` has always written for `morning-brief` — no new receipt type, so every existing reader keeps working — with two fields added: `legs` (the leg-status map the watchdog reads) and `prep_leg` (per-meeting outcomes with their reasons). **NEVER hand-roll the receipt JSON**; hand-rolled shapes are the F-10b/F-49 drift class. ONE call per fire — a second double-logs the fire.

The receipt is owed on every completed fire, including the two no-delivery paths the STOP contract names, and now including a fire whose prep leg failed entirely: "brief ran, prep didn't" is precisely the state this receipt exists to make visible, and withholding it is the Bug #98 class with an extra leg.

```python
from prep_leg import log_combined_receipt
log_combined_receipt(
    WORKSPACE_ROOT,
    leg_result=leg,                           # from Phase 2.95 — never None, even on a whole-leg failure
    brief_status="ran",                       # "degraded" on a Phase 2.9 degrade-tier fire
    fired_via=lateness["receipt_fired_via"],  # from Phase 2.9 — manual | scheduled | catchup; never guess it
    duration_ms=elapsed_ms,
    late_tier=lateness["tier"] if lateness["tier"] in ("note", "degrade") else None,
    extra_data={
        "digest_path": "_hq/briefings/morning-<YYYY-MM-DD>.md",   # WORKSPACE-RELATIVE (§C) — asserted at write
        "sections_rendered": [...],
        # the commitment data.id for each numbered Needs Attention item, in the
        # order they are NUMBERED IN THE DIGEST YOU JUST COMPOSED — apply-choices
        # resolves `mark done [n]` against this list. MANDATORY whenever the
        # section rendered: a brief whose numbering was never recorded makes
        # every one-tap close on it ambiguous, and `brief_receipt` refuses those
        # closes rather than guessing (BRIEFFIX1 Item C). Empty list ONLY when
        # the section genuinely did not render.
        "needs_attention_ids": [...],
        "events_captured": N,
        "telemetry": {...},
    },
)
```

If the workspace has **zero commitment events** but ≥3 meeting events on file, the morning-briefing skill's Step 3b nudge ("💡 Commitments tab is empty even though you've had N meetings — say 'scan for commitments' to backfill") was already included in the digest tail. Do not duplicate it as a separate chat turn.

**7-day activity stopgap (v3.11.1 — REQUIRED).** Apply the morning-briefing skill's Step 3b 7-day filter verbatim: for every commitment that would surface in Needs Attention as overdue/stuck, look up the linked thread's max `ts` in events.jsonl across ALL event types. If the thread has any activity in the last 7 days, drop the commitment from the Needs Attention surface (the work is likely done — events.jsonl just doesn't have the resolution event yet). The three header counts continue to reflect raw workspace state; only the actionable surfaced list is filtered.

# Phase 6 — Post the digest, then STOP

**Convert the document links FIRST — this is the last thing that happens to the text (SPEC BRIEFFIX1 Item A).** The composed digest carries WORKSPACE-RELATIVE pointers, which is correct on disk and dead in chat: Cowork resolves a link against THIS computer's filesystem, so a relative href renders "this file can't be found on your computer" while the document sits in the synced folder. Run the posted copy — and only the posted copy — through the one chokepoint:

```python
from chat_output_renderer import absolutize_doc_links
post_text = absolutize_doc_links(digest_markdown, WORKSPACE_ROOT)
```

**On a cloud-mounted workspace, pass the web links too (v5.11.1, BUG-8538).** When `brief_path.is_session_scoped_path(WORKSPACE_ROOT + "/_hq")` is true, the `computer://` form the conversion falls back to is dead on the customer's machine — resolve each doc's web link on the workspace's OWN cloud platform and hand the resolver in:

```python
# Discover once, host preferred (never first-match — with Google Drive AND
# Microsoft 365 both connected, first-match can search the wrong drive):
#   tool_discovery.discover_drive_tool(tools, "search",
#       prefer_platform=tool_discovery.infer_workspace_drive_platform(WORKSPACE_ROOT))
# google_drive → Drive web link; onedrive / m365_sharepoint (the M365
# connector's file surface spells `sharepoint`, e.g. sharepoint_search) →
# OneDrive/SharePoint web URL. Lookup empty-handed + another drive connected
# (with or without an inferred preference) → try the other. Lookup failure is
# non-fatal.
post_text = absolutize_doc_links(digest_markdown, WORKSPACE_ROOT,
                                 drive_web_url=web_url_for_path)  # callable: relative path → web URL or ""
```

It rewrites every workspace-relative `.docx` / `.pdf` / `.xlsx` / `.pptx` link target to the machine-absolute `computer://` form (through `workspace_paths`' anchor machinery and `brief_path.get_brief_opener_url`, so a cloud-mounted workspace — Google Drive, OneDrive, or SharePoint — gets its web link instead) and leaves everything else byte-for-byte alone. **Never hand-write the absolute form into the composed text** — that would re-rot the snapshot you already saved, which is the bug in the other direction. **Never skip the call because "the links look fine"**: they look fine because they are the persisted form, which is exactly the failure. The rendered-payload scan refuses a payload still carrying a relative doc href, so a skipped conversion fails loudly rather than shipping a dead card.

**Then run the pre-flight check this surface already owes** (the OUTPUT CONTRACT's leak scanner, `references/SHARED_CHAT_OUTPUT_PROTOCOL.md` step 3) over the CONVERTED text, so it sees what the CEO will see:

```python
from chat_output_validator import validate_chat_output
check = validate_chat_output(post_text)   # .ok is False -> fix the source, do not post
```

A `dead_doc_link` violation means the conversion above did not happen or did not reach that link. Fix it at the source — never by hand-editing the URL into the composed text, which re-rots the snapshot you already saved.

**If a link genuinely cannot be converted, DROP THE LINE AND POST THE BRIEF.** There is one way this happens: the workspace root could not be resolved, so `absolutize_doc_links` returned the text untouched by design. Withholding the entire digest over one attachment is the wrong trade in a product whose stated posture is that a write without a surface is acceptable and a surface without a write is not — the CEO loses the calendar, the commitments and the money lines to save them from one link that would not have opened. Replace the offending link with the honest sentence the leg already uses for a file that has not landed (`Prep — 9:30 — syncing — open from your cloud drive`), re-run the pre-flight, and post. Say nothing else about it; the receipt from Phase 5 already records the fire.

**Then post it.** Output `post_text` as the chat turn body — nothing before it, nothing between it and the Links section.

If any briefs or files were referenced (Past Meetings docs, prep docs from earlier fires), add a **Links:** section per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" — one bulleted line per linked file, `computer://` artifact URLs built the same way (never a hand-assembled path). Skip the Links section entirely if nothing connects.

**STOP.** The chat turn is over. Do not narrate what just posted. Do not summarize sections. Do not preview tomorrow's fire. Do not append a "posted" confirmation of any kind — the receipt is already on disk from Phase 5 and a second marker is a second thing to keep in sync.

# Phase 7 — Failure handling (Rule 8)

Degradation and hard failure are different things, and the merged fire has to keep telling them apart now that it writes deliverables as well as a digest.

- **Connector flake / one source unreachable** — degrade gracefully. Note it in plain English inside the digest ("⚠️ Couldn't reach the calendar — check connection"), carry the reason into the receipt's `errors[]`, and finish the fire. A prep leg that could not read the calendar is a whole-leg degrade (Phase 2.95), not a failure of the fire.
- **Hard failure** (entities.json malformed, the workspace unwritable — the fire genuinely cannot proceed): stop, append a `scheduled_task_failure` event carrying the diagnostic verbatim, and surface ONE plain-English line ("Couldn't write this morning's brief — the workspace data looks corrupt. Run `weekly cleanup` to diagnose."). That event is the dead-letter the watchdog reads (`task_watchdog.check_task_failures`) — without it a fire that died mid-run leaves no trace anywhere, which is the silent-death class this whole merge exists to shrink.

NEVER silent-retry. NEVER expose tool names or error-class strings in chat.

---

## Why this orchestrator wraps the skill instead of reimplementing

The on-demand triggers `morning briefing` / `brief me` / `what do I need to know today` / `start my day` already fire the `morning-briefing` skill in Cowork. The scheduled task and the on-demand triggers MUST produce identical content — same sections, same urgency rules, same relationship-grouped layout. Reimplementing the logic in this orchestrator would create two divergent code paths for the same output.

The single source of truth lives at `skills/morning-briefing/SKILL.md`. This orchestrator is the thinnest possible wrapper: resolve paths, delegate to the skill, render the output as chat, log, stop. Plugin upgrades that change the morning-briefing format propagate automatically — this orchestrator inherits whatever the skill produces.

## What this orchestrator does NOT do

- Does NOT triage individual emails (that's `inbox` scheduled task — different orchestrator).
- Does NOT process meeting transcripts (that's `past-meetings`).
- DOES now generate per-meeting prep briefs — Phase 2.95, the leg that replaced the retired `upcoming-meetings` chat (SPEC BRIEFMERGE). It does NOT reimplement the generator: it invokes `skills/call-prep/SKILL.md` per meeting, the same one the on-demand "prep me for my 2pm" runs.
- Does NOT refresh prep later in the day. There is deliberately NO midday leg (M's ruling): a meeting booked after this fire is covered on demand by `call-prep`.
- Does NOT modify entities.json, MASTER_TRACKER, or any workspace VIEW state beyond the prep leg's own deliverables and receipts — the digest half stays read-only **toward views and entities. This line does NOT cancel Phase 2's passive-capture mandate** (BUG-8244 clarification): "Every connector read MUST emit corresponding events to events.jsonl per `shared/PASSIVE_CAPTURE.md`" stands in full — `interaction` events from the mail/calendar/chat reads are substrate CAPTURE, not digest mutation, and a brief that reads connectors without capturing them starves relationship cadence, dormancy, and every last-touch computation downstream. Read-only means: no entity writes, no tracker writes, no view regeneration, no commitment/decision mutations — capture events + the receipt are always in scope.
- Does NOT fabricate data when a connector times out — per the skill's Reliability section, output "⚠️ Couldn't reach [Gmail/Calendar/Slack] — check connection" and continue without that source's data.
- Does NOT fire on weekends if the cron is configured weekday-only (default). Manual trigger of `morning briefing` on a weekend still works via the skill's on-demand path.
