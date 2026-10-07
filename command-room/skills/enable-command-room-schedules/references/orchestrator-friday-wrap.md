# Orchestrator prompt — Friday Wrap

This file is the EXACT prompt the bootloader cats and executes for `taskId: friday-wrap`. Fires 4:00 PM Friday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES`. NEW in v3.11.0 — first weekly-rhythm scheduled task. First-install default (one of 4 tasks registered on a fresh workspace).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. Friday Wrap is a **markdown chat post**, not a widget — it's a recap, not an action surface. Renderer-validator gates do NOT apply here (no item-block parser, no button-action enforcement). The leak scanner DOES still apply (no entity-ID leaks, no email address leaks, no internal phase labels).

**Brief save path:** Friday Wrap produces a `.docx` artifact via the `weekly-recap` skill's existing Phase 5.B save path — `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx`. NEVER write to `_hq/staging/<today>/` (forbidden by the leak scanner — that path is reserved for scheduled-task email drafts).

**⛔ DELIVERABLE RENDER GATE (DOCFENCE4).** That `.docx` MUST come out of `weekly-recap`'s Phase 5.B `brief_writer` path — delegating the recap does not delegate this gate, because the file lands here:

- **NEVER hand-roll the recap** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or leaking recap (the v3.20.0 failure mode). This task fires on a schedule with nobody in the room, so a bypass here is not one bad document, it is a standing weekly one nobody is watching — and the recap sweeps seven days of every connected source, which is the widest surface any single artifact in this system carries.
- **NEVER create, render, copy, upload, or update the recap — or any part, derivative, or restatement of it ("the week in numbers", "a summary", "the highlights") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/meetings/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "so the team can read it Monday", "as a copy alongside the canonical file" — **nor a direct instruction**: "put this week's recap in a Google Doc so I can send it round" is a request this gate refuses, not an override. Hand back the `.docx` link and let the user forward the file itself.

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. **The unattended rule, documents included (IDENT1 I-16; DOCS1 D-3):** this fire never asks, never suggests a connector, and never produces a document anywhere but the folder — the recap is the `.docx` under `_hq/meetings/` through the write door, and no line it posts names or offers a Claude Doc, a page or a deck (that file's § The unattended rule → Documents). Surface link blocks per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" pattern adapted for a non-widget post.

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

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and the orchestrator file path; every read, helper and write below goes through the access layer as the Access preamble at the end of this file resolves it (CONTRACT Rule 22 v6; MIGRATE3-FW), and each file read is one `plan read` of its workspace-relative path, never a file opened in a shell. Continue with:

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)`. **v3.11.1+ contract:** `workspace_path` is REQUIRED on every call. Catch `TZResolutionError` and render the digest header with a "⚠️ Couldn't resolve workspace TZ — times shown as UTC" note rather than aborting.
- Read `<WORKSPACE>/_hq/data/entities.json`. Capture the primary user: **resolve the id with `primary_user.resolve_primary_user(<WORKSPACE>)`, asked through the door as `friday_wrap_helpers:week_facts` (the skill's Phase 2 form), never by scanning for a flag** (SPEC USERKEY1). The canonical pointer is `workspace.user_id`; the `is_primary_user: true` person flag is only the seam's flag fallback, behind every pointer spelling it reads, and is unset on most real workspaces, so a flag scan returns nobody and this wrap's internal/external split then silently treats the user as an outsider. Read that person's first name + email + timezone off the resolved record.
- Read `<WORKSPACE>/_hq/data/aliases.json` for canonicalization during connector scans.
- Read `<WORKSPACE>/CLAUDE.md` if it exists (hot cache for people, projects, terms — supplies most quick references without per-file reads).
- Discover available connectors: Mail (Gmail or Outlook MCP — NEVER Zapier for read), Calendar (native Google or Outlook), Slack/Teams, Drive/OneDrive/SharePoint, every meeting-transcript source MCP wired (Granola / Fireflies / Otter / Read.ai / Zoom AI Companion / Microsoft Teams summaries). Per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE: Zapier is send-only; reads use native MCP.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"env_date": "<session date>", "fired_via": "<scheduled|manual>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:lateness"}'
```

The helper calls `check_lateness('<workspace_root>', 'friday-wrap', fired_via='<scheduled|manual>', env_date='<session date>', emit=True)` exactly as the old block did, beside the data (MIGRATE3-FW). The answer is the verdict this file already reads (its own `tier`, `banner`, `degrade_notice`, `directive`, `ack`, `clock`, `receipt_fired_via`, `rerun_of`) plus `pending_rows`: every row the check writes on the way (the clock record, the `late_fire` telemetry on the note and degrade tiers, and behind a `skip_render` directive the honest `skipped` receipt) comes back instead of being written. **Append `pending_rows` FIRST, before anything is posted: every element, in order, nothing dropped:**

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "friday-wrap", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the lateness answer above, in order>]}'
```

When `pending_rows` is empty there is nothing to append; never compose a row of your own.

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
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (Phase 4 — Post the recap): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Execute the weekly-recap skill (by-project mode, 7-day window)

Read `skills/weekly-recap/SKILL.md`. Execute its Phases 1-6 verbatim against the current workspace + connectors, with these orchestrator-imposed defaults:

**Visual pass note (SPEC OUT2 §3):** the weekly-recap skill's visual pass is PART of the skill's phases, and on this path its page render is not run: the document is built beside the data (`friday_wrap_helpers:land_recap`), never on this session's filesystem, so the skill logs the skip through the door (`friday_wrap_helpers:plan_visual_gate`, then ONE `append_jsonl`) and moves on. Nothing lands in `_hq/` beyond the canonical .docx and that `visual_gate` audit row; never install a renderer from a scheduled task.

- **Window (SPEC CATCHUP1 F-2):** since the last successful Friday Wrap, floored at the 7-day default and ceilinged at 30 days, the skill's "Window definition" section owns the computation. Call `catchup.catchup_window(<workspace_root>, "friday-wrap", floor_hours=168, cap_days=30, fired_via=lateness["receipt_fired_via"], scheduled_only=True)` through the door, the skill's own form (`friday_wrap_helpers:catchup_window`), and hand its **`start_aware` / `end_aware`** to the skill's Phase 1 instead of `[now - 7d, now]`. A normal weekly fire gets exactly the 7 days it always had; a fire after a missed Friday covers both weeks, because a fixed 7-day window means the skipped week is never recapped and the next fire does not reach back over it. When it returns `extended: true`, the recap headline names the real span (*"the last 12 days"*, never *"this week"*). `fired_via` comes from Phase 2.9 and is never guessed, a human typing "weekly recap" gets the plain 7 days.
- **Window clock (SPEC CATCHUP1 F-1):** the connector queries get `start_aware` / `end_aware` — the offset-carrying instants — never the naive `start` / `end`. The skill's Phase 1 is documented in workspace TZ; `catchup_window`'s math is machine-local because the scheduler is. Handing a bare naive value across that seam is LATETZ's failure class and is invisible on any machine where the two clocks agree. The skill's "Window definition" section owns the full rule; keep the naive pair for the receipt's `window_start` / `window_end`, which are read back by the next fire's machine-local math.
- **Grouping mode:** by-project (the skill's default — customer's mental model is project-shaped).
- **Connector caps:** use the skill's defaults (250 received + 250 sent emails, 200 Slack messages, 100 Drive files, 50 transcripts, all calendar events that occurred). **These were tuned for 7 days and are unchanged; the window above can be 30.** So the skill's **COVERAGE HONESTY GATE** (its Phase 2, SPEC CATCHUP1 F-2) is in force on every `extended: true` fire: a capped read that comes back at its cap means this fire SAMPLED the span rather than covering it — the headline says so, and the receipt below carries `window_incomplete_before` so the next fire reaches back instead of starting after this one.
- **Commitment capture:** per the skill's Phase 3, which extracts the commitments in the week's freshly-captured meetings, gates and composes them beside the data (`friday_wrap_helpers:plan_commitment_captures`) and lands them with ONE `append_jsonl`; it hands off to no other skill. Commitments captured from the week's meetings deepen the recap's commitment counts.
- **Output surfaces:** inline chat (markdown recap) + saved `.docx` at `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx` per the skill's Phase 5.
- **Surface-preference filter (Phase 6 Loop 2):** when the recap surfaces per-person/per-project callouts the CEO could act on, drop any the CEO has taught the system to stop surfacing, asked beside the data, never imported here: `python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"items": [{"entity_id": "<person/project id>", "item_class": "<class>"}], "surface": "friday-wrap", "workspace_root": "<WS>"}, "name": "inbox_helpers:filter_by_preferences"}'` answers `{kept, dropped}` by `surface_preferences.is_suppressed(prefs, "friday-wrap", item_class=..., entity_id=...)`; keep the `kept` callouts only. Missing store → no-op; the recap's counts and substrate are untouched.
- **First-Run Personalization (SPEC FRP1).** Apply the weekly-recap skill's "First-Run Personalization" section. Read the recap's knobs via `get_config(WORKSPACE, "weekly-recap", DEFAULTS)`, the skill's door form `friday_wrap_helpers:recap_config`. On the FIRST fire only (`not is_configured(WORKSPACE, "weekly-recap")`): `save_skill_config(WORKSPACE, "weekly-recap", DEFAULTS)` through the write door (`skill_config_writer:save_skill_config`, the skill's form) before rendering, then append the one-time **footer** form of the first-run block after the recap (this orchestrator is a markdown post, NOT a widget, the footer, not fr-items, is the correct transport here, and MUST-NOT rule 5 does not apply). The footer renders exactly once ever, gated by `is_configured`.

Every connector read MUST emit corresponding events to `<WORKSPACE>/_hq/data/events.jsonl` per `shared/PASSIVE_CAPTURE.md` and the weekly-recap skill's batched `atomic_append_jsonl` pattern. Dedup via `source_ref_hash` so re-fires don't double-count.

# Phase 4 — Post the recap

Output the rendered markdown recap as the chat turn body. Follow the exact format from `skills/weekly-recap/SKILL.md` Phase 4 — Headline, Top Decisions, Your Plate This Week (the plate's `wrap` cut — `plate_view.wrap_cut`, rendered verbatim, EVERY Parked row with its reason — CUT-PLATE, 2026-09-06; SPEC PLATE1 night 2; *CORRECTION 2026-09-15 (F-5): the standalone `plate_view.wrap_relay_check` call that stood here is retired — `quiet.wrap_post` below runs it over the final text*), Notable Meetings, Email Threads of Note, New People Surfaced, Anomalies, By-Project Breakdown, What the System Did, Decided for You / Still Waiting (`quiet.wrap_sections`, rendered verbatim — SPEC QUIET1 D6; both drop-empty), the week against the word (`quiet.wrap_coaching_blocks`, rendered verbatim — the skill's §8d; every part drop-empty), What now as STATEMENTS. Omit sections with no real content (the skill's "no placeholders" rule).

**⛔ THE WRAP NEVER ASKS (SPEC_SURFACES2_11c WRAP2, M's design rule of 2026-09-06).** The brief and the wrap never ask a question; the Staff Meeting is where questions live and End of Day may carry two. This fire carried TWO asks on every run until 2026-09-14 — the objectives self-report ("20 seconds: how do these stand?") and the "What now" ASK block — and it fires on a schedule with nobody in the room, so a question here is one nobody is there to notice. Before posting, hand the FINAL text of the chat turn to the ONE door and post what comes back (REVIEW_NIGHT11C H-6, 2026-09-15) — it runs the ask fence, the score fence and the plate relay check together, so none of the three can be skipped one at a time:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"relayed": "<plate text, the cut, byte-exact>", "spans": [<every user_spans entry of every dict in wrap2 docx_sections>], "text": "<the FINAL text of the chat turn>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:wrap_post"}'
```

`friday_wrap_helpers:wrap_post` runs `post_text = wrap_post(post_text, relayed=plate["text"], spans=[...], workspace_root=WORKSPACE_ROOT)` (`quiet.wrap_post`) beside the data (MIGRATE3-FW). The answer is `{text, rows}` when the three fences pass: **post `text`**, and `rows` is empty. When a fence raises, the raise comes back as data: `{ok: false, reason, line, detail, rows}`. `rows` then holds the ONE `surface_failed` receipt the raise wrote under the `friday-wrap` task (F-3, 2026-09-15: this fire runs with nobody in the room, so a wrap that refuses to post and leaves no receipt is a job the watchdog reads as never fired). Land it through the door, every element, in order:

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "friday-wrap", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the wrap_post answer above, in order>]}'
```

When `rows` is empty there is nothing to append. `detail` names the sentence the fence refused and is never posted: rewrite that sentence as a statement with a phrase and ask the door again. When the text cannot be put right, `line` is the whole chat turn.

This replaces the standalone `plate_view.wrap_relay_check` step in Phase 4 above: `wrap_post` runs it. **Post its return value, never the text you handed in.**

`relayed` is the plate cut and `spans` is every `user_spans` entry the week-against-the-word composer hands back; neither is optional. A customer's own row title may end in a question mark — relayed whole in the cut, or composed AROUND by the accountability report and next week's three — and both kinds are blanked before the fence reads the post. Every other word is the product's own and may not carry a question mark at all (REVIEW_WRAP2 F1, 2026-09-15). On a raise, rewrite the sentence as a statement with a phrase — never catch it, never post over it, and never move the question into the `.docx`.

**Tone:** crisp, direct, no preamble. The recap stands on its own — don't introduce it with "Here's your week" or "Wrapping up the week." If the workspace CLAUDE.md carries the persona block (`## How {brain_name} talks to …`), apply it to the chat framing — it outranks this section's tone defaults (STYLE1 D6).

**Voice match:** if `<WORKSPACE>/_hq/.claude/brand-voice-guidelines.md` exists, match user voice for the "What Now" section. Otherwise neutral professional.

After the recap, add a **Briefs:** section per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section" — one bulleted line pointing at the saved `.docx`:

```markdown
**Briefs:**

- [Weekly Recap — <Mon DD> to <Mon DD>](computer:///<encoded path>) — saved as `.docx` in `_hq/meetings/`
```

On the access layer that one line is the skill's 5.C footer and heading link, composed beside the data by `friday_wrap_helpers:post_turn` from the landing's `rel` (MIGRATE3-FW): never hand a `computer:` link across the door in any argument, because the door's argument fence refuses it before anything runs.

If `_hq/meetings/` save failed for any reason (brief_writer error, path-resolution failure), surface a one-line footnote in plain English (*"⚠️ Couldn't save the .docx — the recap above is the working copy."*) and skip the Briefs section entirely.

# Phase 5 — Log the fire + close

Build the telemetry block via `shared/scripts/telemetry.py` `build_pack_run_telemetry()` — same pattern as the other orchestrators. Track connector calls + prompt/response sizes + duration. Merge into `pack_run.data` as `telemetry: {...}`. The value is the INNER block — `build_pack_run_telemetry(...)["telemetry"]` — never the wrapper the builder returns for merging: a receipt carrying `data.telemetry.telemetry` reads as a fire with no measurement at all. Silent — never narrated to chat.

It is asked beside the data, and the answer is the INNER block:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"connector_calls": [<one entry per connector call, as the other orchestrators pass them>], "duration_ms": <elapsed_ms>, "workspace_root": "<WS>"}, "name": "inbox_helpers:pack_run_telemetry"}'
```

Append the fire receipt via the canonical helper (`shared/scripts/receipts.py`, v4.5.2 R1, **NEVER hand-roll the receipt JSON**; hand-rolled shapes are the F-10b/F-49 drift class). This receipt is REQUIRED on every fire, friday-wrap ran receiptless all of dogfood week (F-39/F-43). `log_receipt` runs beside the data with its append held (MIGRATE3-FW): the row that comes back is the one it writes, and it lands through the one door:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"duration_ms": <elapsed_ms>, "extra_data": {<the extra_data below>}, "fired_via": "<the Phase 2.9 receipt_fired_via>", "late_tier": "<the Phase 2.9 tier>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:plan_wrap_receipt"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "friday-wrap", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the receipt answer above, in order>]}'
```

`fired_via` is the Phase 2.9 `receipt_fired_via` (manual, scheduled or catchup; never guess it). `late_tier` may be handed as the raw tier: the helper keeps it only on `note` and `degrade` and drops every other value, the old block's own conditional. `extra_data` carries these keys and no others. The call the helper makes, as a contract (never run here; the door runs it beside the data):

```text
log_receipt(WORKSPACE_ROOT, "friday-wrap", fired_via=<receipt_fired_via>, duration_ms=<elapsed_ms>, late_tier=<note or degrade, else None>,
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
        # SPEC_SURFACES2_11c WRAP2 4.2 item 2 — next week's three, by id, in
        # the order they rendered. `quiet.wrap_coaching_blocks(...)`
        # ["receipt_extra"] hands you this dict; merge it, never re-derive
        # it and never add a second receipt call to carry it. NEXT Friday's
        # accountability section reads it straight back off this receipt
        # (`quiet.last_next_week_ids`), so a fire that drops it leaves next
        # week's wrap with nothing to report against. Omit the key entirely
        # on a week that named nothing.
        "next_week_ids": <quiet.wrap_coaching_blocks(...)["receipt_extra"], merged>,
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

**A wrap that could not compose a block still posts, and the fire's own receipt says which (REVIEW_NIGHT11C H-4, 2026-09-15).** `quiet.wrap_coaching_blocks` degrades block by block — an unreadable store costs one section, never the Friday post — and hands back `wrap2["failed_blocks"]`. Merge it into the receipt below when it is non-empty (`"failed_blocks": wrap2["failed_blocks"]`), omit the key entirely when it is empty, and **never narrate it in chat**: a missing section is not the reader's problem.

That is the difference between a missing section and a dead surface. The wrap now has vocabulary for the second one too — `surface_drivers.SURFACE_FAILED_WRAP`, registered on the `friday-wrap` task in `receipts.RECEIPT_TYPES` — for a fire that could not render at all. Before this the wrap had none, so a dead wrap left a receipt-less silence the watchdog reads as a job that never fired.

**Close the earned door here, after the post — never at compose (REVIEW_NIGHT11C H-1, 2026-09-15).** Same place, same reason as `next_week_ids`: the once-ever offer row may only be written for a line the reader actually saw.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"offer_pattern_key": "<wrap2 offer_pattern_key; an empty string when this week offered nothing>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:record_offer"}'
```

`friday_wrap_helpers:record_offer` is `quiet.wrap_record_offer(WORKSPACE_ROOT, wrap2)` through the WRITE door (MIGRATE3-FW): it writes the once-ever offer row, and with no key it writes nothing (a no-op when this week offered nothing). The skill's Phase 6 makes the same call first; this one then answers `recorded: false, already: true` and writes nothing, so the door is closed once per fire.

`coaching_doors.earned_offer_due` answers False forever once that row exists, so a row written before the ask fence, the score fence, the relay check and the leak gate have let the post out spends the offer on a wrap that never reached anybody.

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
