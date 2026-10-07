# Orchestrator prompt — Morning Brief

This file is the EXACT prompt the bootloader cats and executes for `taskId: morning-brief`. Fires 7:00 AM weekdays local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES`. NEW in onboarding-v2 / 2026-05-17. First-install default (one of 3 tasks registered on a fresh workspace).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Morning Brief is a **markdown chat post**, not a widget — it's a digest, not an action surface. Renderer-validator gates do NOT apply here (no item-block parser, no button-action enforcement). The leak scanner DOES still apply (no entity-ID leaks, no email address leaks, no internal phase labels).

**Brief save path:** Morning Brief does NOT produce a `.docx` brief deliverable. It posts the digest inline and (optionally) saves a markdown snapshot to `_hq/briefings/morning-<YYYY-MM-DD>.md` per the morning-briefing skill's Step 5 saved-snapshot path. NEVER write to `_hq/staging/<today>/` (forbidden by the leak scanner — that path is reserved for scheduled-task email drafts).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface link blocks per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" pattern adapted for a non-widget post.

**Project routing:** thread / project resolution per `references/PROJECT_MAPPING_RULES.md`.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the existing `morning-briefing` skill. The skill at `skills/morning-briefing/SKILL.md` is the source of truth for the digest's format, section ordering, urgency rules, and relationship-grouped thread layout. This orchestrator's job is to (a) resolve plugin + workspace paths, (b) run the PREP LEG first (Phase 2.95 — SPEC BRIEFMERGE §A), (c) execute the morning-briefing skill's connector/context gather (Steps 1-4) verbatim, (d) run the ONE-command brief-pack driver as the LAST gathering step (T3.2 FB-18 — gather first, driver last, place immediately), (e) COMPOSE the digest as PROSE with every pack block placed and NO widget (FB-20) and save the snapshot (Phase 4), (f) log ONE combined `pack_run` receipt covering both legs (Phase 5), (g) POST the digest once (Phase 6), (h) STOP.

**Receipt BEFORE post (SPEC BRIEFFIX1 Item C, M's ruling 2026-08-09).** The receipt is written in Phase 5 and the digest posts in Phase 6, in that order, and the order is the point. Both orders lose something when a fire dies in the middle; they do not lose the same thing. Receipt-then-post leaves a receipt and no post — which the degrade tier already blesses (a write without a surface is a state this product has always accepted) and which the next fire can see. Post-then-receipt leaves a digest on screen that the substrate has no record of: the watchdog reads it as a fire that never happened, and `mark done [n]` resolves against an OLDER brief's numbering, so a one-tap close lands on the wrong item silently. That is the Bug #98 class with a wrong-close on top, and it happened on 2026-08-09. If the post then fails, the fire is still auditable. Do NOT add a "posted!" confirmation event to compensate — the receipt shape is unchanged and every existing reader must keep working.

**Two legs, one fire (SPEC BRIEFMERGE, M's ruling 2026-08-08).** The separate Upcoming Meetings chat is RETIRED; its prep generation is this fire's first leg. Two things follow and neither is optional: prep runs BEFORE the digest composes, so the meeting section links files that already exist; and the prep leg can NEVER kill the brief — every failure degrades to a line and the brief always renders (Phase 2.95). The `.docx` prep briefs this leg writes to `_hq/meetings/` are documented deliverables, separate from the chat-output surface the STOP CONTRACT governs. **The unattended rule, documents included (IDENT1 I-16; DOCS1 D-3):** those briefs are the ONLY documents this fire produces, and they land in the folder through the write door — the digest's prep pointers name the landed files by their own sentence and never a Claude Doc, a page or a deck (`SHARED_CHAT_OUTPUT_PROTOCOL.md` § The unattended rule → Documents).

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

**Self-check before posting anything:** if you're about to write text AFTER the digest + Links section, ask: "is this required by spec?" If no → don't post it. **And the inverse check (t3 FB-9 / FB-20): before STOPPING, confirm the Phase 5 receipt landed and every non-empty block of Phase 3.9's CR-BRIEF-PACK was placed — the lead FIRST (the plate's number, rows and pointer — CUT-PLATE), CHANGED lines cited, money sentences in the body, the fold lines below the fold. A turn that stops with an unplaced pack block is INVALID, not "done early." Confirm the INVERSE of that check too (HEALTH1, 2026-09-07): the health lines (alarms, watchdog, dark-surface, schedule-refresh) were NOT placed anywhere — `pack["health_lines"]` is always `[]`, and a turn that prints one of these four is the regression, not a turn that omits them. And confirm the inverse of the inverse (FB-20): `show_widget` was NOT called this turn. (Degrade-tier fires excepted per Phase 2.9 — the degrade notice is the whole output.)**

---

You are firing the Command Room "Morning Brief" chat. Today is the LOCAL date now. You're producing the morning digest before the user starts their workday.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — whether by cron or manual `re-run` trigger. Multiple fires per day are allowed. A `pack_run` event writes at the end of every fire for audit trail.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and the orchestrator file path. Every access-layer line in this file (`workspace_access.py` with a verb) runs through the Access preamble at the end of this file, which resolves `<WS>` and `$RT` on every seat, the merged one included (MIGRATE3-MB). Continue with:

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)`. **v3.11.1+ contract:** `workspace_path` is REQUIRED — pass the resolved `<WORKSPACE>` path on every call (or set `CR_WORKSPACE` in the subprocess env). The prior walk-up resolver was removed because it never resolved inside the plugin clone and silently rendered UTC. If `to_local` raises `TZResolutionError`, surface "⚠️ Couldn't resolve workspace TZ — times shown as UTC" in the digest header and continue rendering with raw UTC; do not let the exception abort the fire.
- Every site in this orchestrator that renders a connector timestamp (Gmail `internalDate`, Calendar event start/end, Slack `ts`) MUST pass `workspace_path=<WORKSPACE>` to `to_local()` / `format_local()`. No exceptions.
- Read `<WORKSPACE>/_hq/data/entities.json`. Capture the primary user — **resolve the id with `primary_user.resolve_primary_user(<WORKSPACE>)`, never by scanning for a flag** (SPEC USERKEY1). The canonical pointer is `workspace.user_id`; the `is_primary_user: true` person flag is only the seam's flag fallback, behind every pointer spelling it reads, and is unset on most real workspaces, so a flag scan returns nobody and every you-owe/they-owe number below is computed against an empty user. Read that person's first name + email + timezone off the resolved record.
- Read `<WORKSPACE>/_hq/data/aliases.json` for canonicalization during connector scans.
- Read `<WORKSPACE>/CLAUDE.md` if it exists (hot cache for people, projects, terms — supplies most quick references without per-file reads).
- Read `<WORKSPACE>/_hq/MASTER_TRACKER.md` (project list, statuses, next actions, waiting-on).
- Resolve connectors through the seam: calendar + mail via `tool_discovery.discover_for_category(<category>, "<op>", tools, declared=connector_config.declared_backend(<category>))`, falling back to the `discover_*` helpers when no backend is declared (empty map = today's behavior, R4); Slack via `discover_slack_tool`. NEVER Zapier for reads (the seam excludes Zapier legs automatically). On drift (declared backend NOT PRESENT) in a scheduled fire: skip-and-flag per SHARED_CHAT_OUTPUT_PROTOCOL § Connector drift (R13) — never prompt from a silent fire. Per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE: Zapier is send-only; reads use native MCP.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"env_date": "<session date>", "fired_via": "<scheduled|manual>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:lateness"}'
```

The helper calls `check_lateness('<workspace_root>', 'morning-brief', fired_via='<scheduled|manual>', env_date='<session date>', emit=True)`
exactly as the old block did, beside the data. The answer is the verdict this file already reads — its
own `tier`, `banner`, `degrade_notice`, `directive`, `ack`, `clock`,
`receipt_fired_via`, `rerun_of` — plus `pending_rows`. The check runs exactly
as it always has, and every row it writes on the way comes back instead of
being written: the clock record, the `late_fire` telemetry on the note and
degrade tiers, and — behind a `skip_render` directive — the honest `skipped`
receipt for this fire. **Append `pending_rows` FIRST, before anything is
posted — every element, in order, nothing dropped:**

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "morning-brief", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the lateness answer above, in order>]}'
```

When `pending_rows` is empty there is nothing to append. Never compose a row of
your own, and never append the same row twice.

**A REFUSED STEP STOPS THE FIRE, AND THE STOP IS RECORDED (MIGRATE3-MB fix round 1).** From here to the post, when any door answer is `ok: false` (a refusal, or `reason: timeout`: the door kills a step that runs past its budget, and the step's own failure branch never runs), or a helper's `result` carries `error`, the fire is over: do not run another step, do not retry, do not build anything by hand. The ONE exception is the one this file names for a single prep (Phase 2.95: a prep that fails is a line, never a stop). Run this ONE line, with `step` the name of the form that was refused:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"mode": "<scheduled|manual per Phase 2.9 run mode>", "step": "<the refused form name>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:record_fire_stopped"}'
```

It writes the Morning Brief's `surface_failed` receipt, so a stopped morning is never partial and silent, and answers `line`: post that one sentence, verbatim, as the whole output, and STOP (no Phase 5 receipt of your own, no digest). When the refusal itself carried a `line` (a writer that could not name who it writes for), post THAT line instead; the receipt writer is refused the same way and there is nothing else to run.

**The layer carries the workspace, the clock and the environment for you (CLOCK1).** Every helper process the verbs start gets `CR_WORKSPACE`, `TZ` (the workspace's own timezone), `CR_ENV`, `CR_HOST_MODE` and the forwarded writer identity, so a helper can never be left guessing which workspace it is in, cannot fail to cross-check the clock, and never stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


**Read `directive` BEFORE the tier — it is the render decision (SPEC SCHED1).** If `directive` is `skip_render`, the slot this fire is serving was ALREADY delivered: a receipt for it is on the ledger, and the helper has already written the honest `skipped` receipt for this fire. Post the returned `ack` line, exactly as returned, as the ENTIRE output of this fire — no surface, no widget, no sections, no Sources block, and no receipt of your own — then STOP. Do not re-derive whether it "really" ran, do not render a shortened version, and do not read the tier as the decision: on this path the tier is `none`, `none` means "run normally", and that reading is what delivered three duplicate full surfaces in one day. `directive` is present on every tier and is `null` on all the others, so this is one unconditional check rather than a special case to remember. A `manual` fire never carries it — a human who asks for the surface gets the surface.

**And `tier: "rerun"` is the OPPOSITE instruction — it RENDERS (SPEC RUNNOW1).** `skip_render` is bounded: the helper returns it only within two hours of the receipt that served the slot, which is the duplicate the skip exists to catch — a catch-up and a scheduled fire landing the same edition minutes apart. A fire that calls itself `scheduled` and arrives LATER for a served slot comes back with `directive: null`, `tier: "rerun"` and an `ack`. Post that `ack` as the OPENING line of this fire's output, then render this surface IN FULL, exactly as on any other tier. Past two hours the fire is a person pressing Run Now, and no run mode a fire reports about itself can tell you otherwise — the standing ruling is that a person who asks gets what they asked for. **Carry the returned `rerun_of` onto this fire's receipt, and carry it at the ONE place this file writes its `pack_run` receipt — the receipt call whose own `extra_data` block already spells the key out for you.** The writer differs by surface, so take the one THIS file names and no other: never add a second receipt call to carry the field, and never hand-roll a receipt JSON. **This is load-bearing, not bookkeeping.** A receipt carrying `rerun_of` is excluded from the served-slot marker, which is what lets the person press again in five minutes and get the surface again; a re-run receipt written WITHOUT it reads as an ordinary scheduled delivery and re-arms the skip against the next press for two hours — the refusal this build exists to remove, arriving by a different door. Post no lateness banner and no `degrade_notice` on this tier: the slot WAS delivered, so nothing was missed and nothing is stale-by-omission.

Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (Phase 6 — the digest post): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The `late_fire` telemetry on note/degrade tiers came back in `pending_rows` and the append above landed it (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

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
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "morning_brief_helpers:prep_config"}'
```

The answer's `config` is `skill_config_writer.get_config(workspace_root,
"call-prep", {"auto_fire": "24h"})`; read `config["auto_fire"]`.

- **`24h`** (default) — prep every kept meeting.
- **`morning_of`** — prep only meetings starting TODAY in the workspace timezone.
- **`off`** — generate NO `.docx` on this scheduled fire. Every kept meeting is recorded `skipped` with that reason; the brief's calendar section is unchanged, and the fire still writes every substrate record it owes (a suppressed deliverable must never drop a silent write — Bug #98 class). Say nothing about the knob and never name it.

This gate governs the SCHEDULED leg only. A manual "prep me for my 2pm" always runs `call-prep` regardless.

**Step C — run the leg.**

The leg is `prep_leg.run_prep_leg(discover, generate, workspace_root=...)`,
and it runs beside the data in two verbs around the generator, because the
generator is you running call-prep:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"meetings": [<the Step A meetings, each {"meeting_id","title","time_label","start"}>], "workspace_root": "<WS>"}, "name": "morning_brief_helpers:prep_leg_plan"}'
```

The answer is `{started_at, reuse, generate}` — the leg's own reuse rule
(below) decided which meetings are already prepped. For every meeting in
`generate`, run `skills/call-prep/SKILL.md` end to end (Step B's knob still
applies: a meeting the knob skips is reported `null`), then hand what each run
returned back to the leg:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"generated": {"<meeting_id>": <the dict call-prep returned, null for a deliberate skip, or {"error": "<why it raised>"}>}, "meetings": [<the same Step A meetings>], "started_at": "<started_at from the plan answer, unchanged>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:prep_leg_result"}'
```

The answer IS the leg result — call it `leg`. `run_prep_leg` ran for real on
the far side, over the meetings Step A found, with a generator that replays
what each call-prep run reported; its reuse rule, per-meeting isolation,
workspace-relative fence and same-fire receipt proof all applied. A Step A
failure is passed as `"discover_error": "<why>"` instead of `meetings`, and the
whole leg degrades to its one banner.

`discover()` returns the Step A meetings as `{"meeting_id": <calendar event id>, "title": ..., "time_label": "2:15", "start": <the instance's own start, ISO from the calendar>}` — `time_label` is what the degrade line says back to the CEO, so it is the same short form the calendar section prints ("2:15", not "14:15" and not a full timestamp). **`start` is REQUIRED for reuse to be possible (BRIEFFIX1 Item B).** A calendar id is stable across a recurring series, so the id alone cannot say which standup a prep was written for; the start is the field that differs. Pass the calendar's own start value — a meeting handed over without one is never reused, it is regenerated, which is safe and merely wasteful.

**A meeting that is already prepped is `reused`, not re-generated and not re-labelled (SPEC BRIEFFIX1 Item B).** `run_prep_leg` asks the substrate — `prep_brief` receipts, F-29, never a folder listing — whether a prep was written TODAY for THIS MEETING INSTANCE (the calendar id AND the instance's own start), and when one was it does NOT call the generator. The instance half is not optional: a recurring meeting's id is identical every week, so an id-only test hands over last week's document, and an age-based window cannot help because for anything daily or weekday-cadenced the window sits inside the recurrence interval. Anything unproven — no start on the receipt, no start on the meeting, a prep from an earlier day — regenerates. The outcome is `reused`, it renders the SAME link line the CEO wants, and the row records the `source_receipt_seq` it leaned on. Do not force a rebuild to "earn" the link: a fresh document that duplicates a fresh document is work for the machine's benefit. Do not report it as `ran` either — `ran` means this fire generated something, and on 2026-08-09 a fire reported `ran` over a prep built six hours earlier, which is how a stale document was handed over as fresh. The word for the true thing exists now; use it. The leg applies this per meeting and hands you the result — there is nothing to decide here beyond not overriding it.

`generate(meeting)` runs **`skills/call-prep/SKILL.md` end to end** for that meeting and returns `{"brief_path": <workspace-relative>, "sources": {"mail": "read"|"absent"}}`, or `None` for a deliberate skip.

**`sources` — which sources the prep actually got (SPEC PREPSEAM1 DD-3).** The generator is the only thing that touches a connector, so it reports and the leg records. `"read"` = a backend resolved and the read happened. `"absent"` = no mail backend resolves at all, which is NOT a failure — the mail-derived blocks simply do not exist for that prep, the brief never mentions it, and the meeting stays `ran`. A mail backend that IS declared but ERRORS is the third case and it does not travel in this dict: **raise**, and `run_prep_leg` records that meeting `degraded` with the reason. Swallowing a connector error and returning a brief anyway is the exact shape that let a prep leg produce zero preps and still read as a clean fire; if it happens regardless, report `"failed"` so the row confesses it and the watchdog raises `prep-ran-with-failed-source`. Omitting `sources` entirely is legal and means "did not report" — never "read". **Every key and value is one bare token** (letters, digits, underscores, 40 chars max): this dict lands in append-only canonical state that no leak scan reads, so a state word is a WORD, never a sentence and never an explanation with an address or a name in it. `normalize_sources` replaces anything else with `unrecognised` rather than persisting it — the reason belongs in the raised exception, which the leg records as the meeting's `reason`. There is no scheduled variant of the generator: the ONE-GENERATOR CONTRACT (v4.5.2 S1) says depth comes only from the call-prep `depth` setting, never from which path fired, and this leg is now the only scheduled caller. Everything that governs a prep brief — the five-block gathering, the deliverable render gate, the owed-table pending split, the name-spelling rule, the visual pass, and the `receipts.log_prep_receipt` call — lives in that skill and is read fresh at fire time. Three arguments differ from the on-demand path: pass `generated_by="morning-brief"`, `fired_via=<the Phase 2.9 receipt_fired_via>`, and `meeting_start=<the meeting's own start>` to `log_prep_receipt`. The last one is what lets tomorrow's fire tell this prep apart from the next instance of the same recurring meeting; omit it and every future fire regenerates instead of reusing.

**Step D — paths are WORKSPACE-RELATIVE (SPEC BRIEFMERGE §C — the attachment-rot fix).**

Every file-pointer this fire persists — `brief_path` on a prep receipt, `digest_path` on the fire receipt — is stored workspace-relative (`_hq/meetings/<file>.docx`), never absolute. An absolute path is valid only on the machine and in the session that wrote it: a fire in a cloud session writes pointers that are dead the moment the session ends, and a fire on one computer writes pointers the other computer cannot open. Convert with `workspace_paths.to_workspace_relative(path, WORKSPACE_ROOT)` and let `workspace_paths.assert_workspace_relative` refuse anything absolute at the write — `run_prep_leg` and `prep_leg.log_combined_receipt` both call it for you, so the only way to persist an absolute path from this fire is to hand-roll a write around them. Don't.

Legacy rows keep their absolute values forever — events.jsonl is append-only history and nothing here rewrites it. `workspace_paths.normalize_persisted_path` resolves them at READ time instead.

**Step E — carry the result forward. Both handoffs are mandatory:**

- Phase 3 Step 2 builds `todays_meetings` from the leg's meetings — no second calendar query.
- Phase 4 renders the meeting-section lines from `prep_leg.meeting_lines(leg, workspace_root=WORKSPACE_ROOT)`. That helper REFUSES to run without the leg's result (`LegNotRunError`) — the ordering fence in code, not in convention.
  - On every seat that line is the Phase 4 item 7 door form naming `morning_brief_helpers:prep_meeting_lines` (MIGRATE3-MB fix round 1), which runs `prep_leg.meeting_lines` beside the data.

# Phase 3 — Execute the morning-briefing skill (the connector/context gather — runs BEFORE the Phase 3.9 driver)

**Every substrate read and write those steps make goes through the door lines the SKILL carries (MIGRATE3-MB).** Where this file names a module by function below (`chat_context`, `load_thread_knowledge`, `surface_preferences`, a passive-capture append), that is what the SKILL's door line runs beside the data, never an import in this session's shell; on a seat whose workspace is not in this session, a step with no door line is left out and said in the preamble's one sentence (rule 6), never improvised.

Read `skills/morning-briefing/SKILL.md`. Execute its Steps 1-4 verbatim against the current workspace + connectors — the skill steps below own the CONNECTOR half (calendar, mail, Slack) and the digest composition prep. The substrate blocks (counts, CHANGED lines, alarms, watchdog, the money sentences, the queue pointer) arrive from Phase 3.9's pack, which runs AFTER these steps as the LAST action before posting (T3.2 FB-18 — gather first, driver last, place immediately):

- **Step 1 — Load core context** (already done in Phase 2 above; do not re-read).
- **Step 2 — Scan connected sources.** **Today's calendar is ALREADY IN HAND from Phase 2.95's prep leg — reuse it; do not query the calendar again for today (SPEC BRIEFMERGE §A: no second discovery pass).** Add only tomorrow's first event, which the leg does not need. Build `todays_meetings` for Step 3d from the leg's meetings, and take each meeting's prep status from the leg's outcomes rather than re-deriving it. Calendar today + tomorrow's first event; Mail unread/important from last 18h, filtered by people in PEOPLE.md + project-related subjects + flagged; the declared chat backend's unread DMs and mentions from last 18h, plus project channels (resolve the tool with `tool_discovery.discover_chat_tool` — never name a chat product). Per the skill's caps: top 10 emails, top 5 chat items. **Chat context leg (SPEC CHATSCAN1 §C) — a leg INSIDE this same Step 2, never a second sweep:** run `chat_context.run_chat_context(workspace_root, chat_messages, tracked_entities, provider=chat_seam.resolve_chat_provider(workspace_root), scan_plan=chat_seam.plan_scan(provider), budget=ReadBudget())` over the fetch you already have. Its `context_line` is ONE sentence placed inside an existing section and is NEVER a row — the needs-attention lane still shows at most 5, and the brief's row count must be identical with this leg on and off. An undeclared chat backend returns a skipped block: render nothing, say nothing. Append `coverage_note` verbatim to any line that would otherwise read as full chat coverage. **Self-reply filter (v3.11.1 — REQUIRED):** apply the skill's Step 2 "Self-reply filter" verbatim. For every email-thread candidate, fetch the thread's latest message and compare `From:` to the primary user's email (resolve the user with `primary_user.resolve_primary_user` and read the email off THAT record — never scan for an `is_primary_user: true` record, which per SPEC USERKEY1/USERKEY2 is only the seam's flag fallback, behind every pointer spelling it reads including the canonical `workspace.user_id`; an unresolved user turns this filter off silently and the brief hands back threads the user already answered). If the latest message is FROM the primary user, DROP the thread from Needs Attention and Overnight Inbox — the user already responded. This filter applies to scheduled fires; the default **in-inbox** query alone is insufficient because the mail search surfaces earlier inbound messages in threads the user has since replied to.
  - **The chat context leg runs through the door (MIGRATE3-MB fix round 1).** Where the bullet above names `chat_context.run_chat_context`, run the SKILL's ONE door line naming `morning_brief_helpers:chat_context` over the fetch you already have; it runs that function beside the data. The fire passes NO `tracked_entities` (the helper builds them from the book beside the data, because the book does not fit through the door) and at most 80 messages, the newest, each carrying only `ts`, `text` and its pointer fields.
- **Step 3 — Check tracker for urgency.** Scan MASTER_TRACKER for overdue commitments, stale waiting-on items (7+ days), today's deadlines, urgent flags. Apply Step 3b commitments aggregation from `events.jsonl` (`type: commitment` not closed by a later `commitment_resolved` / `thread_resolved` event). The header counts come from **Phase 3.9's pack (`brief_state.headline` — the driver, which runs AFTER these gather steps, runs `compute_and_log_brief_state` internally; never call it yourself, never hand-compute the counts — leave the header slot to be filled from the pack in Phase 4)**, matching the skill's Step 3d — its `counts["headline"]` buckets are LOGGED, not rendered (SPEC PLATE1 night 2 / NUMBERS1 R-1 — the brief's one commitment number is `plate.line`, Phase 3.9); never hand-compute them, never fold unowned/unconfirmed into a direction, and never render an inventory line of them. **Step 3a per-project lines (v3.11.1 overlay; payload-fed since READER1 ADOPT4 — REQUIRED):** apply the skill's Step 3a and its "MANDATORY context load — the per-project lines" section verbatim — parse the tracker's `<!-- generated-at -->` stamp; for every thread that renders its own detail line under an org section or Other relationships, load the canonical thread payload (`load_thread_knowledge(workspace_root, thread_id, "brief-line")`, ONE call per rendered line, none for a collapsed or hidden thread) and take the line's substance — Next, open items, Waiting On — from that payload and only from it; take `Last touched` / quiet-N from the `thread_activity` map Step 3d already derives once for the whole fire. Override `Last touched` / `Next Action` / `Waiting On` from those. Names, never ids; a null name renders as "an unnamed contact", never the id. The tracker is a snapshot, not a live view; without this overlay a scheduled fire on a workspace whose tracker hasn't been regenerated in 10 days will surface stale "quiet since April 25" copy for threads that had activity today. **This is the ONLY leg of this fire that touches the reader:** the calendar / mail / chat legs, every Phase 3.9 pack block (Needs Attention, alarms, CHANGED, watchdog, dark-surface, schedule-refresh, "Captured since your last close", money, queue pointer), the opener, and this bootloader-read orchestrator's own plumbing are unchanged by it. On a `not_ready` / `unmeasured` gauge the line renders with zero coverage apparatus; on `empty_payload` the line renders the tracker's copy alone — never fabricated thread context.
  - **A scheduled fire skips the SKILL's Step 3d door form and its append (MIGRATE3-MB fix round 1).** Phase 3.9's pack writer computes and logs the brief state beside the data, once; running the Step 3d form too would compute it twice and log a second `brief_state` row.
- **Step 4 — Build the digest (compose here, post in Phase 6 — after the Phase 3.9 driver and after the Phase 5 receipt).** Apply the relationship-grouped thread layout from the skill's Step 4: every thread's `affiliation_id` resolves to its org; primary-focus orgs render prominently; non-primary roll up under "OTHER ORGS" with `relationship_type` badges. Section headers use `canonical_name`, not hardcoded labels. Omit any section with no content — never pad. Number the Needs Attention items and keep the rendered order: the item→commitment-id mapping writes into the `pack_run` receipt as `needs_attention_ids` (Phase 5) so a later `mark done [n]` resolves. SUGGESTED FIRST MOVE at the end — one sentence. **Surface-preference filter (Phase 6 Loop 2):** before finalizing Needs Attention, drop any item the CEO has taught the system to stop surfacing — `from surface_preferences import load_surface_preferences, is_suppressed`; keep an item only if `not is_suppressed(prefs, "morning-brief", item_class=<class>, entity_id=<person/project id>)`. Missing store → no-op; the substrate is untouched.
- **Step 4b — Background-task watchdog line: RETIRED FROM THIS SURFACE (HEALTH1, 2026-09-07 — supersedes the v4.6.1 S3 light daily pass).** `pack["watchdog_line"]` is always `None` — the driver no longer makes the `task_watchdog.brief_watchdog_line` call at all, and you must not call it yourself either, before or after. This used to print inside `pack["health_lines"]`, LAST; never pad an all-clear line into the brief — the honest answer now is that this pass simply does not run on this surface. The same finding (e.g. "2 of your background tasks need attention") surfaces in more detail once a week in `cleanup`'s Monday note (its own deep pass, unchanged); `health check` (system-health) still owns the on-demand deep answer.
- **Step 5 — Deliver: executes in PHASES 4-6, never here (T3.2 FB-18).** The digest cannot post before Phase 3.9's driver has run — its pack fills the header counts, the alarm lines, the money sentences, and the pointer line. The delivery is then split across three phases and the split is load-bearing (BRIEFFIX1 Item C): Phase 4 composes it and, in scheduled mode (this is one), writes the rendered digest to `<WORKSPACE>/_hq/briefings/morning-<YYYY-MM-DD>.md` per the skill's "save to file" branch; Phase 5 logs the receipt; Phase 6 posts it inline in this chat turn.
- **First-Run Personalization (SPEC FRP1).** Apply the morning-briefing skill's "First-Run Personalization" section, through the access layer (BRIEFDOOR1 SHOULD 6): read the brief's knobs with ONE `run_helper` line naming `morning_brief_helpers:brief_config` (the answer is `{config, configured, defaults}` — `get_config` over the defaults, and `is_configured`). On the FIRST fire only (`configured` is false): the skill's `run_writer` line naming `skill_config_writer:save_skill_config` with the answer's `defaults`, before rendering — a write, so it goes through the write door and never runs in-process, then append the one-time **footer** form of the first-run block after the digest (this orchestrator is a markdown post, NOT a widget, so the footer — not fr-items — is the correct transport, and MUST-NOT rule 5 does not apply). The footer renders exactly once ever, gated by `is_configured`.

Every connector read MUST emit corresponding events to `<WORKSPACE>/_hq/data/events.jsonl` per `shared/PASSIVE_CAPTURE.md`. Use `atomic_append_jsonl` from `shared/scripts/atomic_write.py` for batched appends. Dedup via `source_ref_hash` so re-fires don't double-count overnight email reads.

# Phase 3.9 — The one-command brief pack (t3 FB-9; de-carded FB-20 — the LAST gathering step, prose only)

**Why this phase runs LAST (T3.2 FB-18, kept):** a live 2026-07-16 scheduled fire ran this driver FIRST, then ~10 connector/context steps, and the pack's blocks went unplaced — a driver whose output sits 10 steps behind the post step gets forgotten. Gather first, driver last, place immediately. That ordering stands even though the widget it originally protected is gone (FB-20): the pack's prose blocks are just as skippable as a widget was.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"mode": "<scheduled|manual per Phase 2.9 run mode>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:run_morning_brief_pack", "timeout_s": 540}'
```

**The pack is built beside the data, through the write door (MIGRATE3-MB, F-T2-15).** The writer runs the SAME builder the `surface_drivers.py morning-brief` CLI wraps (`build_morning_brief_pack`), on the host that holds the folder: it logs the `brief_state` row, keeps the pack's audit copy and mints the plate's display numbers exactly as the CLI did. Never run that CLI in this session's own shell: on a merged seat the session holds no workspace, which is why this fire used to stop before it started. The answer's `pack` is the CR-BRIEF-PACK the CLI used to print after `CR-BRIEF-PACK: `, and it is the whole contract: place its blocks unchanged, exactly as the map below says. When the answer carries `surface_failed: true`, the brief could not render: its `line` is the one sentence for this fire, the failure receipt is already written, and nothing else is posted. `ok: false` from the door (a refusal or `reason: timeout`) is the stop rule under Phase 2.9: one `record_fire_stopped` line, its one sentence, STOP. The line carries `"timeout_s": 540` on purpose: on a large book the pack build can run longer than the door's default 150 seconds (the 2026-09-27 review measured 71 seconds on a copy of a real 330 row book through the door on a busy machine, and a worst-case plant timed out), and a build cut off by the budget is a brief that never posts. That value overrides, for this one verb, the Access preamble's "150 s budget": where the tool you run the line with takes a timeout of its own, give it the same 540 seconds, or the shell cuts the call before the door does.

**⛔ THE BRIEF RENDERS NO WIDGET (FB-20 — M's ruling 2026-07-16, "the morning brief should just be a morning brief").** This surface is READ-ONLY. It has no card, no rows, no buttons, no `show_widget` call, and no relay obligation — the driver emits no `CR-WIDGET-HTML` block and no `CR-REQUIRED-NEXT-STEP` banner, because there are no bytes to relay. **Do NOT call `mcp__visualize__show_widget` from this orchestrator, ever, for any reason** — not for the pack, not to "helpfully" render the queue, not as a fallback when the prose feels thin. A widget posted from a morning-brief fire is a CONTRACT VIOLATION even if it renders beautifully. Adjudication happens at the **staff meeting** (Mon/Wed/Fri, or any time the user says `staff meeting`) — that is the sole surface where items get confirmed, and the pointer line below is how the brief hands off to it.

**Every non-empty pack field is a MANDATORY placement in this turn** (the placement map lives in Phase 4):

- `lead` → THE FIRST CONTENT BLOCK (CUT-PLATE, 2026-09-06 — M: "we want to show less options to clients — they are overwhelmed"). `lead.lines` verbatim, in order, directly under the digest header: the plate's ONE number (`plate.line`), then LINE TWO and the one coaching line when the pack carries them (`day_intent.line`, `coaching_line` — BRIEF2, see the two map items below), then the numbered rows (`plate.rows`), ONE pointer (`plate.pointer`). Print the LIST, never rebuild it from the parts. Nothing sits above it but the header and the persona-permitted intro line — no alarm, no paragraph, no CHANGED, no reminder. The driver refuses a pack whose composed order puts any count above the number (`surface_drivers.assert_number_leads`); this file is scanned for the same order by `tests/run_cutplate_test.py`.
- `fold_lines` → BELOW THE FOLD, verbatim, in order (the resting line, then the queue pointer), printed together with the skill's Step 3g confirm pointer and the dated-personal echo near the end of the digest (map item 4). These are the counts that used to compete with the number.
- `health_lines` → NEVER PLACED (map item 5 — HEALTH1, 2026-09-07, superseding CUT-PLATE's "LAST"). `pack["health_lines"]` is always `[]`: `alarm_lines` (the duplicate-entry warning, a stale view — FS-04/05/06/15), `watchdog_line`, `dark_surface_lines` and `schedule_refresh_announce_lines` do not render on this surface at all — not first, where the v5.28.0 attended test found them, not last, where CUT-PLATE moved them. M's ruling, reading his own brief: "this should not be shown", ruled in full for all four kinds. The weekly `cleanup` maintenance run is the one place they are reported now.
- `alarm_lines` → NEVER PLACED (HEALTH1; CUT-PLATE's "at the END of the digest" is superseded). Still computed on the pack for the maintenance reader; this orchestrator never prints it.
- `day_intent.line` → LINE TWO, already second inside `lead.lines` (BRIEF2 2.2 item 2, 2026-09-14). "Today is about …" — the READER'S OWN sentence from last night's close, read back through `day_intent.load_day_intent`, stated origins only. Empty when nothing was stated, and then nothing prints: never "nothing on file", never a prompt to state one. `day_intent.contains_digit` is a MARK, not a line — it says the reader's sentence carries a figure the brief's count fence deliberately did not reconcile (user text answers a different question than the plate's number). Never print the mark, never reword the items.
- `coaching_line` → THE ONE COACHING LINE, already third inside `lead.lines` when non-empty (BRIEF2 2.2 item 3). A statement, never a question. The driver gates it on all three doors (the coaching shape is not `observed`, the `coaching_line` render switch is on, the coaching object names a behaviour) and hands back `""` otherwise — print what is there or print nothing, and never compose a coaching sentence of your own on this surface.
- `explain_once_line` → THE CLOSING LINE, verbatim, alone, as the LAST thing rendered (BRIEF2 2.2 item 4). **The pack already consumed it — never call `explain_once.consume` from this fire.** `""` on every fire after the first and on a workspace onboarding never armed; print nothing then.
- `changed.lines` → the CHANGED contract line MUST cite them, ALL of them, verbatim and in order (FS-09 — never "Nothing material" over a non-empty feed). BRIEF2 2.2 item 1: this list is no longer capped at three. Every line for a door the product walked through on the reader's own rows — a lapse batch, a rest batch, a silent close, each with count, door and `undo` — prints; only the ordinary lines are capped, at three, by the driver. Never re-cut or summarise the list.
- `brief_state.headline` → COMPUTED AND LOGGED, NOT A LINE (SPEC PLATE1 night 2 / NUMBERS1 R-1). The driver already ran `compute_and_log_brief_state` (the Step-3d derivation — `commitment_state.compute_brief_state` under the hood) and logged the `brief_state` audit event — do NOT call it again, and do NOT render its buckets as an inventory line ("you owe / owed to you / no clear owner / overdue" — retired from the rendered brief; the numbers stay on the event for trends and the book page). The brief's ONE commitment number is `plate.line` below.
- `plate` → THE PLATE'S BRIEF CUT (SPEC PLATE1 night 2 — D7 `brief`). `plate.line` is the ONE number ("N on your plate today" — the PLATE'S OPEN COUNT, the same integer the board's header states; NUMBER1 3.1 / R-26, and it is no longer DO IT + CHASE), and `plate.breakdown` is the line printed directly under it ("N open · M want you today") which states what the attention count now means; `plate.rows[*].line` are the NEEDS ATTENTION rows — at most 5, the top DO IT then the top CHASE rows, each leading with its block's verb (`Do:` / `Chase:`), already gated (a row this fire's drops removed is absent — `plate.excluded_ids`) (the fatigue rule's question is the Staff Meeting's now — no brief row carries an `ask_line`, REVIEW_NIGHT11C H-5); `plate.pointer` is the one pointer ("…and N more — say `what's on my plate` for the rest."), empty when the cut is the whole plate. Render them in the order given and do NOT add rows back from anywhere. `plate.refused: true` → print `plate.line` (the one plain refusal sentence) and render NO rows (D8). `brief_state.needs_attention` / `needs_attention_more_line` stay on the pack as the gated lane the verdicts were computed over — they are NOT a second row list to print. The lane is bound at the RENDER, never at the derivation: `brief_state.headline` still counts everything, because a cap is a render bound and never a silence.
- `watchdog_line` → NEVER PLACED (HEALTH1; see Phase 4's map item 5). The driver no longer even calls `task_watchdog.brief_watchdog_line` for this pack — `pack["watchdog_line"]` is always `None`. `cleanup`'s weekly deep pass reports the same finding in more detail.
- `dark_surface_lines` → NEVER PLACED (TASKALARM1 — see Phase 4's map item 5b). The driver hard-codes `[]` and does not call `task_alarm.dark_surface_lines` from this fire at all — the call itself, not only the print, moved to `cleanup`'s weekly pass, because the helper marks its own render-once ledger the instant it is READ.
- `schedule_refresh_announce_lines` → NEVER PLACED (BRIDGESIL1 — see Phase 4's map item 5c). Same reason as `dark_surface_lines`: `schedule_refresh.announce_lines` is a render-once ledger, so the call moved to `cleanup`'s weekly pass rather than being called here and discarded.
- `prior_captures_lines` → its own small section, body of the digest, near the calendar/meeting material (MORNCAP1 — see Phase 4's map item 4d). "Captured since your last close": meetings the background/catch-up passes briefed after the last day-close receipt. Render verbatim, in the order given — never re-sort, never re-cap (already capped at 3 with an "...and N more, filed." tail). Section header prints ONLY when the list is non-empty; an empty list means the section does not exist in the digest at all (Ruling §0.3 — no "nothing was captured" filler).
- `money_lines` → verbatim, one sentence each (FB-20's ONE carve-out — see Phase 4's map item 5).
- `queue_pointer.line` → verbatim, ONE line, BELOW THE FOLD as the last of `fold_lines` (map item 4) — never above the number.

Run the driver ONCE per fire (idempotent-single-call — a re-run to "refresh" double-logs the brief state, the RV-3 class). In a `degrade`-tier fire the driver still runs (its writes are substrate the task owes) but nothing renders except the degrade notice.

# Phase 4 — Compose the digest (+ the pack placements) and save the snapshot

**Order (FB-20 + BRIEFFIX1 Item C):** there is no relay step, and there is no post step HERE either — this phase produces the digest text and writes it to disk; Phase 5 receipts it; Phase 6 posts it. Build the rendered markdown digest in the exact format from `skills/morning-briefing/SKILL.md` Step 4 — header line, THE LEAD (`pack["lead"]`: the plate's number, rows, pointer — first, CUT-PLATE), the synthesis lead and CHANGED / DECIDE / NEEDED, calendar section, the money lines, OVERNIGHT INBOX, per-org thread sections, the fold lines, SUGGESTED FIRST MOVE, then the closing preps chip. **No health lines anywhere (HEALTH1, 2026-09-07) — CUT-PLATE's trailing health block is retired, not relocated within this digest.**

**Pack placement map (t3 FB-9 / FB-20 — every non-empty CR-BRIEF-PACK block lands, no exceptions):**

1. `lead` — THE FIRST CONTENT BLOCK, verbatim, directly under the header (CUT-PLATE, 2026-09-06): `plate.line` — the ONE commitment number, where the six-number commitments line used to be (SPEC PLATE1 night 2 / NUMBERS1 R-1: "these numbers are so big") — then `plate.rows`, then `plate.pointer`. `brief_state.headline` is logged, never rendered as a line; CLUSTCOUNT1's `information_line` rides the `brief_state` event and the book page, not this surface. Never add a second number beside `plate.line`, never render block totals here (P4 — those are `plate` / `board` only). Nothing sits above the lead but the header and the persona-permitted intro line: the alarms that used to be item 1 are now item 5, at the end.
1b. `plate.rows` + `plate.pointer` — the rows of the lead (SPEC PLATE1 night 2). Number `plate.rows[*].line` from 1 in the order given (at most 5: the top DO IT rows, then the top CHASE rows — `plate_view.render_plate(view, "brief")` owns the cut and every word, including the leading `Do:` / `Chase:` block verb, which is what the row wants and never a button: this surface is read-only prose, FB-20). Print each row's `line` exactly as given — the brief asks nothing and no row carries a question (1b-bis below). Then `plate.pointer` verbatim when non-empty — never invent one, never round it. `brief_state.resting_line` does NOT print here: it is a count and it sits in `fold_lines` (item 4). Never re-rank, never top the section up from your own Step-3 scan, never print `brief_state.needs_attention` as a second list, never repeat the rows under a later "Needs attention" heading (that body section now carries only the money and detector lines). `needs_attention_ids` on the Phase 5 receipt is `[row.id for row in plate.rows]` in this exact order — that is what `mark done [n]` resolves against (BRIEFFIX1 Item C). The one number above still counts the rows the gates left out of the cut (`plate.excluded_ids`); it is the plate's number and reads the same on every surface.
2. `changed.lines` — folded into the CHANGED contract line (one narration slot; substance first), BELOW the lead. Print every line the pack hands you: the batch lines are uncapped by design (BRIEF2 2.2 item 1) and the ordinary ones are already cut to three.

  **1b-bis. CORRECTION, 2026-09-15 (REVIEW_NIGHT11C H-5) — THE OVERDUE ASK LEFT THIS SURFACE. THE BRIEF NEVER ASKS.**

  This block used to say *"THE OVERDUE ASK LIVES HERE NOW"* and instructed the fire to render a row's `ask_line` — *"Send the pricing sheet — 8 days overdue. Done, new date, or drop?"* — verbatim as its label. **That instruction is retired and the paragraphs carrying it are gone.** M's design rule of 2026-09-06 is that the brief and the wrap never ask; the Staff Meeting is where questions live. FOLD1-B moved the fork there in night 11c.

  **The brief cannot carry the ask any more, and that is in code, not in your judgement.** `surface_drivers` builds this pack with `ask=False`, and `end_of_day` stamps an `ask_line` onto a row only when that flag is true — so **no brief row can carry one**. The Staff Meeting's `OVERDUE — new date` section asks instead, once, and the marker is written there. Phase 6.1 below still runs and is now a no-op on this surface; it is kept because the call is harmless and its absence would read as a decision.

  **So: render every row's plain `line` and nothing else.** If you find yourself composing "Done, new date, or drop?" — or any question — on this surface, that is the defect this correction exists to stop. The pack's own ask fence sees the lines the pack composed; a question you add after the pack is a question nothing can see.

  **`brief_state.resting_line`**, when non-empty, prints verbatim BELOW THE FOLD as the first of `fold_lines` (item 4) — never beside the lead. It says how many overdue items are waiting on an answer and where to find them. An empty string means nothing is resting: do not narrate that, and never write a "0 resting" line of your own. Resting rows are not in `needs_attention` at all and you do not go looking for them — they are still inside `needs_attention_total`, which is why the denominator does not move when the lane goes quiet.

  **And the write half is Phase 6.1, AFTER the post.** It is a prose-called step and it is not optional: see that phase.
3. `money_lines` — verbatim, one sentence each, in the digest body, under the Needs attention heading (the plate rows are in the lead and are not repeated there). **The ONE money carve-out (FB-20):** a deal signal is the single class the brief still names outright, because a deal that goes quiet for a day is the one silence with a price tag. These sentences are PROPOSE-ONLY and carry no verbs — each one already routes the user to `staff meeting`, which is where the confirm happens by chat phrase. Never add buttons to them, never invent a "confirm?" affordance, never act on one from this turn. Empty list → nothing renders; **never** pad an all-clear ("no new deals today" — never).
4. `fold_lines` — BELOW THE FOLD (CUT-PLATE), near the end of the digest, before Suggested next steps: verbatim, in order — `brief_state.resting_line` then `queue_pointer.line` — followed by the skill's Step 3g confirm pointer and the dated-personal echo, one line each, each only when non-empty. The queue pointer is the brief's entire handoff to the adjudication surface; its count is the driver's, computed from the same projector the staff meeting renders — **never recount it, never adjust it, never round it, never soften it** ("a few things need your eyes" is a lie about a number you were handed). None of these lines may move above the lead.
5. `health_lines` — RETIRED FROM THIS SURFACE (HEALTH1, 2026-09-07), superseding CUT-PLATE's "LAST". Never print this block. `pack["health_lines"]` is always `[]`: `alarm_lines` (FS-04/05/06/15 — the duplicate-entry warning, a stale view, unreadable entries), `watchdog_line`, `dark_surface_lines` and `schedule_refresh_announce_lines` render on the brief NOWHERE any more — CUT-PLATE moved them from item 1 (the top, where the v5.28.0 attended test found them) to item 5 (the end); M, reading his own brief, ruled "this should not be shown" and it was ruled in full for all four kinds. Nothing is silenced: the weekly `cleanup` maintenance run is the one place all four are now composed and reported, and the only surface where a cleanup pass can actually be offered and run. If you find yourself about to place any of these four fields on this turn, stop — after Suggested next steps the digest goes straight to the closing preps chip, nothing between them.
5b. `dark_surface_lines` (TASKALARM1) — same retirement. Do not call `task_alarm.dark_surface_lines` from this fire at all (not merely "don't print it") — the driver already doesn't; a per-task "X has not fired in N days" / "was never set up on this machine" line marks its own render-once ledger the instant it is READ, so calling it here and discarding the result would consume the finding before `cleanup`'s weekly pass — the surface that now owns it — ever saw it.
5c. `schedule_refresh_announce_lines` (BRIDGESIL1) — same retirement. The ONE narration a customer used to see here for a silently-moved cron/label now surfaces once a week in the Monday note instead ("Your Inbox task now runs 7:30 AM weekdays — a Command Room update moved it; say 'change my schedule' if you'd rather move it back"); `schedule_refresh.announce_lines` is a render-once ledger too, so this driver hard-codes an empty list rather than calling it and throwing the result away.
6. **`prior_captures_lines` (SPEC MORNCAP1) — "captured since your last close".** A small labeled section in the digest BODY (near the calendar/meeting material, NOT the tail — this is narration of NEW artifacts the reader hasn't seen yet, not a dark-surface alarm). Render a short header ("**Captured since your last close:**") only when the list is non-empty, then the lines verbatim, in the order given — `morning_capture.narrated_since_close` already capped it at 3 with an "...and N more, filed." tail and already deduped it against the same meeting narrating twice (its own render-once ledger, keyed by meeting, not by date — SO A MEETING NEVER RE-NARRATES ON A LATER BRIEF EITHER). Each line already links the brief file that was written for it and states what came of it (n commitments, n decisions) — never re-derive either. A meeting the close itself receipted as deferred (`window_incomplete_before`) that this pass recovered renders as **"caught up overnight: X"**, never as a fresh capture — do not reword this sentence or fold it into the plain capture phrasing. Empty list → the section does not exist in the digest at all; never a "nothing was captured overnight" line (Ruling §0.3, COVERQUIET1's complaint pre-honored).
7. **Prep-leg lines (SPEC BRIEFMERGE §A/§B)** — `prep_leg.meeting_lines(leg, workspace_root=<WORKSPACE>)`, rendered inside the existing Today's-calendar section. They are LINES, not a section: a whole-leg degrade contributes ONE banner line, a per-meeting failure contributes ONE line naming the meeting and the phrase that regenerates it, a successful prep contributes the workspace-relative link (or the honest `syncing — open from your cloud drive` line when the workspace's cloud platform — Google Drive, OneDrive, or SharePoint — has not landed the file on this machine yet; never a dead card), and a deliberate skip contributes NOTHING. The merged fire must not grow the brief past its existing caps, and an all-clear pad ("all meetings prepped") is forbidden the same way every other all-clear in this surface is.

   **The lines come through the read door (MIGRATE3-MB fix round 1).** Never call `prep_leg.meeting_lines` in this session: it asks the filesystem whether each brief has landed, and a session that holds no workspace answers "syncing" for every prep. Run ONE read-door line, beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"leg": <leg, from Phase 2.95, as it came back>, "workspace_root": "<WS>"}, "name": "morning_brief_helpers:prep_meeting_lines"}'
```

   The answer's `lines` ARE the lines, in order: place them in the calendar section and build item 7b's chips from them. The leg is one row per meeting of the day (about 30 walked items a meeting), far under the door's limit.

7b. **The closing repeat (SPEC WALKSMALL1 Part B)** — the digest's LAST line repeats the LINKED prep outcomes as compact chips: `Today's preps: [9:00](_hq/meetings/…docx) · [11:00](_hq/meetings/…docx)`. Time label as link text, the same workspace-relative pointer as the target, ` · ` between them. This renders the `meeting_lines` result you already hold a SECOND time — it is not a second call and not a second derivation, so there is no second leg read and no second calendar pass to disagree with the first. Chips are LINKED outcomes only: a `syncing` line, a per-meeting regenerate line and the whole-leg banner stay in the calendar section, because a chip that opens nothing is the dead card §A refuses. No linked outcome → **no line at all**: no header, no "no preps today". One line does not grow the brief past its caps; a section would, which is why this is still LINES. Pointers stay workspace-relative here — the snapshot is written in that form and Phase 6's ONE chokepoint converts the posted copy. It is NOT the chat `Links:` section (Phase 6), which is a post-time affordance and may name documents from earlier fires.

Before moving on: re-check the pack against what you composed. An unplaced non-empty block = the turn is incomplete — place it, then continue. **A logged receipt never substitutes for an unplaced block (STOP-contract rule 6).**

**Tone:** crisp, direct. Opening order per the skill's Tone section: (1) the personified intro line (`"Morning, {first_name} — {brain_name} here with today's read."` — the ONLY greeting permitted, and it renders ONLY if the persona block permits: when the workspace CLAUDE.md carries the persona block `## How {brain_name} talks to …` and it says skip pleasantries — or its Never-line forbids greeting openers — OMIT the intro line and open directly with the header; STYLE1 D6, the persona outranks this shape), (2) the digest header, (3) THE LEAD — `pack["lead"]` verbatim (the plate's number, rows, pointer — CUT-PLATE), (4) the synthesis lead. No other greeting ("Good morning!" / "Here's what's happening." — never). After that it's a status board, not a conversation.

**Voice match:** if `<WORKSPACE>/_hq/.claude/brand-voice-guidelines.md` exists, match user voice for the SUGGESTED FIRST MOVE line. Otherwise neutral professional.

**Save the snapshot NOW, in the persisted form.** Write the composed digest verbatim to `<WORKSPACE>/_hq/briefings/morning-<YYYY-MM-DD>.md` — the same snapshot Phase 3's Step 5 names, taken here because the text is final here. Every document pointer inside it stays WORKSPACE-RELATIVE (the `_hq/meetings/…` form Step D pinned): that is what makes one file mean one file on the other computer, and BRIEFMERGE §C exists to keep it that way. Do NOT save the converted chat form; the conversion happens in Phase 6, to the posted copy only.

# Phase 5 — Log the fire (BEFORE the post)

**Gate before logging (T3.2 FB-18 → FB-20 → BRIEFFIX1 Item C):** the receipt is bookkeeping, not delivery, and that has not changed — what changed is the ORDER. Log the receipt once the digest is COMPOSED and the snapshot is written, before Phase 6 posts it. The old gate said the reverse ("if the digest has not been posted, do not log") and the reverse is what produced a posted brief with no receipt on 2026-08-09: a fire that dies between the two now leaves a receipt and no post, which the degrade tier already blesses and the next fire can see, instead of a digest the substrate has no record of. (A degrade-tier fire per Phase 2.9 renders nothing except the degrade notice and the receipt still MUST be logged; withholding it is the Bug #98 class.) The widget half of this gate is retired with FB-20 — there is no widget to relay and no relay to gate on; if you called `show_widget` this turn, that is the violation, not the omission.

**This does NOT license stopping here.** The receipt is not the deliverable. A turn that logs and does not reach Phase 6 is an incomplete turn — it is simply now an incomplete turn that leaves a trace.

Build the telemetry block via `shared/scripts/telemetry.py` `build_pack_run_telemetry()` — same pattern as the other 5 orchestrators. Track connector calls + prompt/response sizes + duration. Merge into `pack_run.data` as `telemetry: {...}`. The value is the INNER block — `build_pack_run_telemetry(...)["telemetry"]` — never the wrapper the builder returns for merging: a receipt carrying `data.telemetry.telemetry` reads as a fire with no measurement at all. Silent — never narrated to chat. (v3.5.0+ — morning-brief was the only orchestrator missing this; `usage report` aggregation was incomplete for morning-brief fires until then.)

Append **ONE combined receipt covering both legs** via `shared/scripts/prep_leg.py` (SPEC BRIEFMERGE §D). It writes the SAME `pack_run` shape `receipts.log_receipt` has always written for `morning-brief` — no new receipt type, so every existing reader keeps working — with two fields added: `legs` (the leg-status map the watchdog reads) and `prep_leg` (per-meeting outcomes with their reasons). **NEVER hand-roll the receipt JSON**; hand-rolled shapes are the F-10b/F-49 drift class. ONE call per fire — a second double-logs the fire.

The receipt is owed on every completed fire, including the two no-delivery paths the STOP contract names, and now including a fire whose prep leg failed entirely: "brief ran, prep didn't" is precisely the state this receipt exists to make visible, and withholding it is the Bug #98 class with an extra leg.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"brief_status": "<ran, or degraded on a Phase 2.9 degrade-tier fire>", "duration_ms": <elapsed_ms>, "extra_data": {<the extra_data block below>}, "fired_via": "<the Phase 2.9 receipt_fired_via>", "late_tier": "<note|degrade, else null>", "leg_result": <leg, from Phase 2.95 -- never null, even on a whole-leg failure>, "workspace_root": "<WS>"}, "name": "morning_brief_helpers:plan_combined_receipt"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "morning-brief", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the receipt answer above, in order>]}'
```

`prep_leg.log_combined_receipt` runs beside the data with its append held —
the call below, its arguments exactly the plan line's — and the row it would
write comes back in `rows`: its own shape, its own pointer assert, the writer's
identity stamp. ONE append lands it. `fired_via` is the Phase 2.9
`receipt_fired_via` (manual | scheduled | catchup) — never guess it — and
`late_tier` is the tier only on `note`/`degrade`. The call, and its
`extra_data` block:

```text
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
        # SPEC MORNCAP1 — the pack's `n_prior_captures_narrated`, copied
        # VERBATIM. Zero-written, NEVER omitted: 0 when nothing was pending,
        # never a dropped key just because the section didn't render. This
        # is the total pending count (capped-render or not — see the pack
        # field's own docstring), not the count of lines actually printed.
        "n_prior_captures_narrated": pack["n_prior_captures_narrated"],
        # SPEC RERUNFAN1 — on a `rerun` tier ONLY, and it is what lets
        # the NEXT press render. A receipt carrying `rerun_of` is excluded
        # from the served-slot marker (a re-run is a delivery a PERSON asked
        # for, not the scheduled delivery of a slot); one written WITHOUT it
        # reads as an ordinary scheduled delivery and re-arms the two-hour
        # skip, so the next press is refused. Copy `lateness["rerun_of"]`
        # verbatim; OMIT the key entirely on every other tier.
        "rerun_of": <lateness["rerun_of"], or omit on any other tier>,
        "telemetry": {...},
    },
)
```

If the workspace has **zero commitment events** but ≥3 meeting events on file, the morning-briefing skill's Step 3b nudge ("💡 Commitments tab is empty even though you've had N meetings — say 'scan for commitments' to backfill") was already included in the digest tail. Do not duplicate it as a separate chat turn.

**7-day activity stopgap (v3.11.1 — REQUIRED).** Apply the morning-briefing skill's Step 3b 7-day filter verbatim: for every commitment that would surface in Needs Attention as overdue/stuck, look up the linked thread's max `ts` in events.jsonl across ALL event types. If the thread has any activity in the last 7 days, drop the commitment from the Needs Attention surface (the work is likely done — events.jsonl just doesn't have the resolution event yet). The three header counts continue to reflect raw workspace state; only the actionable surfaced list is filtered.

# Phase 6 — Post the digest, then STOP

**Convert the document links FIRST — this is the last thing that happens to the text (SPEC BRIEFFIX1 Item A).** The composed digest carries WORKSPACE-RELATIVE pointers, which is correct on disk and dead in chat: Cowork resolves a link against THIS computer's filesystem, so a relative href renders "this file can't be found on your computer" while the document sits in the synced folder. Run the posted copy — and only the posted copy — through the one chokepoint:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"text": "<digest_markdown, verbatim>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:post_text"}'
```

The answer's `text` is `post_text` — `chat_output_renderer.absolutize_doc_links(digest_markdown, WORKSPACE_ROOT)`, run beside the data (BRIEFDOOR1 SHOULD 6), where the root is.

**On a cloud-mounted workspace, pass the web links too (v5.11.1, BUG-8538).** When `brief_path.is_session_scoped_path(WORKSPACE_ROOT + "/_hq")` is true, the `computer://` form the conversion used to fall back to is dead on the customer's machine — resolve each doc's web link on the workspace's OWN cloud platform and hand the resolver in:

```text
Discover once, host preferred (never first-match — with Google Drive AND
Microsoft 365 both connected, first-match can search the wrong drive):
  tool_discovery.discover_drive_tool(tools, "search",
      prefer_platform=tool_discovery.infer_workspace_drive_platform(WORKSPACE_ROOT))
google_drive → Drive web link; onedrive / m365_sharepoint (the M365
connector's file surface spells `sharepoint`, e.g. sharepoint_search) →
OneDrive/SharePoint web URL. Lookup empty-handed + another drive connected
(with or without an inferred preference) → try the other. Lookup failure is
non-fatal. Hand what you found to the same verb as a MAP, relative path →
web link (a callable cannot cross a command line; a path the map does not
carry gets the empty string, the conversion's own honest no-href form):
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"drive_web_urls": {"<relative path>": "<web link>"}, "text": "<digest_markdown, verbatim>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:post_text"}'
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

## Phase 6.1 — AFTER the post: record what was asked (SPEC EODSYNTH1 R-3, inheriting SPEC OVERDUE1)

Once the turn is posted, one call, silent, no chat output:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"brief_state": <pack["brief_state"], verbatim>, "workspace_root": "<WS>"}, "name": "morning_brief_helpers:plan_lane_asked"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "morning-brief", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the mark answer above, in order>]}'
```

The write door runs `end_of_day.mark_lane_asked(WORKSPACE_ROOT, pack["brief_state"], source_skill="morning-brief")` beside the data with its appends held (a writer: its idempotency read takes the ledger lock) (BRIEFDOOR1 SHOULD 6); the rows it would have written come back, and the append lands them. When `rows` is empty there is nothing to append.

**One call that takes the lane whole** — it reads `brief_state["asked_ids"]` and writes one additive `commitment_updated` per row through `commitment_state.mark_asked`, stamped with THIS surface's id so "which surface asked" stays readable. Do not loop, do not pick rows, do not hand it a list you built: which rows were asked about was decided in the driver and is not a judgement to re-make here.

**AFTER the post, and that is the opposite of the receipt on purpose.** The receipt goes first because it is what the numbers on screen resolve against. This goes last because the mark means *the CEO has been asked* — write it before the question reaches the screen and a fire that dies mid-turn rests a row nobody ever saw a question about. It never raises and it never blocks: a mark that fails to write costs one repeated row tomorrow morning, which is the pre-OVERDUE1 behaviour and a survivable one.

The write is deliberately not movement, so asking about a quiet item does not make it read as freshly touched — that fence lives in `commitment_activity`, not here. Nothing about this step is narrated in chat, and it happens after the STOP below rather than before it: it posts nothing.

**STOP.** The chat turn is over. Do not narrate what just posted. Do not summarize sections. Do not preview tomorrow's fire. Do not append a "posted" confirmation of any kind — the receipt is already on disk from Phase 5 and a second marker is a second thing to keep in sync. (Phase 6.1 above is a silent WRITE, not output — it adds nothing to the turn.)

# Phase 7 — Failure handling (Rule 8)

Degradation and hard failure are different things, and the merged fire has to keep telling them apart now that it writes deliverables as well as a digest.

- **Connector flake / one source unreachable** — degrade gracefully, and **say nothing about it in the digest** (M's ruling R3, 2026-09-13 — SPEC SURFACEFIX1 5.1). Leave the section that leg would have filled out of the brief, carry the reason into the receipt's `errors[]` and the pack's `connector_gaps`, and finish the fire. The health check and the weekly maintenance report are where a reader learns a leg was not read; the brief never carries a reachability line of any shape. A prep leg that could not read the calendar is a whole-leg degrade (Phase 2.95), not a failure of the fire.
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
- Does NOT fabricate data when a connector times out — per the skill's Reliability section, leave that source's section out of the digest entirely, carry the gap into `connector_gaps` for the health check and the maintenance report, and continue. The digest itself says nothing about the gap (R3, SPEC SURFACEFIX1 5.1).
- Does NOT fire on weekends if the cron is configured weekday-only (default). Manual trigger of `morning briefing` on a weekend still works via the skill's on-demand path.

## The Access preamble this file refers to

Propagated by `scripts/dev/propagate_access_preamble.py`; the canonical copy is in `shared/WORKSPACE_ACCESS.md`.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
```
