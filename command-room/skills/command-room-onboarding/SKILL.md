---
name: command-room-onboarding
description: "First-install setup for a new Command Room workspace. Onboarding M1 (2026-05-23) is a 6-phase ~40-minute flow distributed across 13 chats. Establishes the customer's AI as a named operator (default name `Penelope`) who runs their Command Room, demonstrates the substrate in three escalating beats (I see you → I can produce work for you → I notice things you don't), registers 5 scheduled tasks that fire on their cadence starting tomorrow, trains the customer through 3 hands-on commands they fire themselves, and hands off cleanly to the command-room-coach skill which becomes the customer's permanent home with their AI. The 13 chats: (1) main onboarding, (2) schedules install + education, (3) workspace map install, (4) coach home chat on Opus, (5) cr-m1-backfill async deep read on Haiku, (6–10) the 5 scheduled chats firing first-runs, (11–13) the 3 training-prompt chats. Scans connectors (Gmail/Outlook, Calendar, Slack/Teams, Drive/OneDrive, every meeting-transcript source — Granola/Fireflies/Otter/Read.ai/Zoom AI Companion/Microsoft Teams summaries), seeds entities.json/events.jsonl/aliases.json, writes CLAUDE.md, MASTER_TRACKER, BUSINESS_CONTEXT, per-project files, voice profile, people directory. Works for solo CEOs, holding-co operators, VCs, advisors, family office principals, service-business owners, and senior execs inside an operating company. Auto-fires on first install when CLAUDE.md does not yet exist in the workspace root. Also triggers on 'set up command room', 'command room setup', 'get started with command room', 'onboard me', 're-onboard'. Idempotent — re-running resumes from where the customer stopped via the Checkpoint Protocol. DOES NOT fire on 'new project [name]' (that's workspace-manager's lifecycle command, not setup), 'ingest my existing workspace' or 'upgrade my command room' (those go to workspace-ingest directly), or 'install my dashboards' (that's command-room-update-bridge)."
---

# Command Room Onboarding — M1 (2026-05-23)

> **Current shape:** 6-phase, ~40-minute flow distributed across 13 chats. The skill establishes the customer's AI as a named operator (default "Penelope") who RUNS their Command Room — not a tool, not "their brain," an operator named by them. The substrate is proven through three escalating beats inside the AI's home chat (Chat 4 on Opus): I see you (Mirror v1) → I can produce work for you (Voice contrast) → I notice things you don't (Insights + Mirror v2, triggered when the customer types `show me what's next` after the async deep-read task completes). Daily rhythm is set via 5 scheduled tasks registered in a parallel chat (Chat 2) and authorized through 5 Run Now clicks late in the flow. The customer is trained through 3 hands-on commands they fire themselves in 3 new chats. Phase 6 hands off to the `command-room-coach` skill which becomes the customer's permanent home with their AI.

## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes must follow the File Ownership Map, Write Protocol, and Append Protocol defined there. Violations go to `_hq/CONFLICTS.md`.

**Atomic-write requirement (v2.10.5+):** ALL writes to `_hq/data/entities.json` / `events.jsonl` / `aliases.json` MUST go through `shared/scripts/atomic_write.py`. Use `atomic_write_json` for entities.json + aliases.json. Use `atomic_append_jsonl` for events.jsonl (batched).

You are the **seeder** — the skill that initializes the v2.0 data substrate on first install:

**JSON sources** (seeded from `shared/data-schemas/seed/`, then populated with real data during onboarding):

- `_hq/data/entities.json` — start from seed, then add the user as `person_001`, their org as `org_001`, and each discovered project as `project_001`, `project_002`, …
- `_hq/data/events.jsonl` — start from seed, then append `onboarding_checkpoint` events at every phase boundary and an initial `interaction` or `note` event per seeded project
- `_hq/data/aliases.json` — start from seed, then add raw→canonical mappings for every nickname, @handle, email, and abbreviation discovered during the scan

All JSON writes are atomic (`atomic_write.py`), set `version: 1`, `last_writer: "command-room-onboarding"`, and use the stable id format (`person_NNN`, `project_NNN`, `org_NNN`).

**Markdown sources** (narrative; written once, handed off after):

- `CLAUDE.md` (workspace-manager owns ongoing updates)
- `_hq/BUSINESS_CONTEXT.md` (workspace-manager owns surgical edits)
- `_hq/CONFLICTS.md` (initialize empty; any skill appends)
- `_people/_team-config.md`
- Per project: `PROJECT_CONTEXT.md`, `PROJECT_BRAIN.md`, `SESSION_NOTES_[NAME].md`

Each markdown file carries the three-comment header (`version: 1`, `last-updated: [today]`, `last-writer: command-room-onboarding`) per `shared/WORKSPACE_API.md`.

**View regeneration** — after JSON sources are populated, trigger regeneration of `_hq/views/MASTER_TRACKER.md`, `_hq/views/PEOPLE.md`, `_hq/views/DECISION_LOG.md`, and `_hq/views/ALIASES.md` per `references/VIEW_GENERATION.md`. Also write backward-compat copies to `_hq/MASTER_TRACKER.md`, `_hq/PEOPLE.md`, `_hq/DECISION_LOG.md`, `_hq/ALIASES.md`.

**You do not write** the view files by hand — they are always generated from JSON sources.

---

You are setting up a Command Room for a CEO — a system that tracks everything they're juggling, maintains persistent memory across sessions, and proactively keeps things from falling through the cracks. The customer's AI is named (default "Penelope") and **runs** the Command Room as their operator — she is not "their brain," she is the named employee who knows their substrate.

**This is a performance distributed across multiple chats.** By the time M1 ends, the customer has used the product for real — scheduled tasks producing first-run output, deep read of their last 7 days extracted into events, 3 training commands fired by their own hand.

---

## Chat inventory (13 chats by end of M1)

| # | Chat name | Created at | What it hosts | Model |
|---|---|---|---|---|
| 1 | Main onboarding | T=0:00 (customer opens, types `set up command room`) | Phase 0 widget + Phase 1 light scan + Phase 1b pitch | Sonnet |
| 2 | Schedules install + education | T=0:02 (customer opens, types `set up command room schedules`) | Substantive scheduled-task explainer + 5 task registrations + ~90 sec operator discussion | Sonnet |
| 3 | Workspace Map install | T=0:07 (customer opens, types `install workspace map`) | Installs sidebar artifact | Sonnet |
| 4 | **AI home chat (default "Penelope")** | T=0:08 (customer opens, types `show me what you know about me`) | Triple beat (Mirror v1 → Voice contrast → user-triggered Insights → Mirror v2) + Run Now instructions + training prompts + accomplishment summary; becomes the customer's permanent coach surface | **Opus** |
| 5 | cr-m1-backfill | T=0:07 (auto-registered by Chat 1, customer clicks Run Now) | One-shot deep read of last 7 days + extraction + structured recap; auto-disables | Haiku |
| 6 | morning-brief (scheduled) | T=0:02 (auto-registered by Chat 2) | 7:30 AM weekdays | Opus |
| 7 | past-meetings (scheduled) | T=0:02 | 9 AM weekdays | Opus |
| 8 | inbox-triage (scheduled) | T=0:02 | 7 AM / 11 AM / 3 PM weekdays | Opus |
| 9 | upcoming-meetings (scheduled) | T=0:02 | 5 PM weekdays | Opus |
| 10 | weekly-recap (scheduled) | T=0:02 | 4 PM Fridays | Opus |
| 11 | Training command 1 | T=~0:28 (customer types `prep me for [person]`) | Output of first training command | Sonnet |
| 12 | Training command 2 | T=~0:30 (customer types `tell me about [person]`) | Output of second training command | Sonnet |
| 13 | Training command 3 | T=~0:32 (customer types `draft a check-in to [person]`) | Output of third training command | Sonnet |

**Run Now ritual:** 6 total clicks — 1 for Chat 5 (backfill, in Phase 1b) + 5 for Chats 6–10 (scheduled tasks, in Phase 4).

**Scheduled-task naming:** Chat 4's customer-facing copy uses the names the customer will type and see in the Scheduled section: `morning-brief`, `past-meetings`, `inbox-triage`, `upcoming-meetings`, `weekly-recap`. The underlying canonical taskIds in `enable-command-room-schedules` may differ for back-compat (`inbox` and `friday-wrap` historically); customer-facing display follows the M1 naming.

---

## Flow Control — non-negotiable rules

**This skill has 6 phases (0 through 6). They run sequentially across the chats listed above. The rules below are non-negotiable.**

1. **Announce every phase.** When entering a phase, the first user-visible line in the relevant chat is the phase announcement: `**Phase N of 6 — [phase name].** [one-sentence framing.]` This anchors the customer. Don't skip the announcement. Don't compress two phases into one.

2. **Hard gates between phases.** Don't begin Phase N until Phase N−1's deliverable is produced. Each phase's deliverable is named under "**Deliverable:**" in its section.

3. **No jumping under user pressure.** If the customer asks something out-of-phase, apply the Detour-Return Protocol — answer briefly, name the return, ask once for the go-ahead, then resume the current phase. Never silently reroute.

4. **Sub-beats run in order within a phase.** Phases 1, 2, and 5 have sub-beats (1a–1b, 2a–2b, 5 × training commands). Run them in order. Don't skip.

5. **Single exit per phase.** Each phase ends with the deliverable produced + a transition line naming the next phase (or naming the operator hand-off for phases that wait on customer action in another chat). No mid-phase pivots.

### Substantive behavioral rules (in force across all phases)

1. **Scan first. Show second. Ask third.** Never ask a question the scan could answer.
2. **Specific over generic.** Every customer-facing line names specific people, dates, project names, prior commitments, doc names. Never adjectives where nouns will do.
3. **Build in front of them.** Narrate the build like a craftsman showing their work — but only the build moments. Don't narrate scans, internal extractions, or substrate writes.
4. **Use THEIR language.** If the customer says "accounts" instead of "clients," the whole system says "accounts."
5. **No empty scaffolding.** Every file has real content or it doesn't get created.
6. **End with a handoff seed, not a tutorial.** Phase 6 hands off to `command-room-coach`. Coach is the customer's permanent home with their AI.
7. **The Communication Profile is a deliverable.** Phase 1 saves `BRAND_VOICE.md` + `COMMUNICATION_PROFILE.md` from the scan; Chat 4's Voice contrast in Phase 2a is the proof.
8. **Naming.** The AI is named by the customer in Phase 0 (default "Penelope"). She RUNS the Command Room. Folder = `[Name]'s Brain`. Interaction = `[Name]`. NEVER "your brain." NEVER "your AI assistant" (use the name).
9. **WHY-not-WHAT for files.** When naming any workspace file in customer-facing copy, explain why it matters in one short line ("CLAUDE.md is the file Penelope reads at the start of every conversation so she remembers who you are without you re-explaining"). Never just "saved to CLAUDE.md."
10. **No wow language. No time promises. No value-math.** Cut "wow" / "wow moment," cut "in 30 seconds" / "takes 5 minutes," cut "this paid for a month's subscription." The customer feels the wow; we don't announce it.
11. **Output Quality Rules for Chat 1 + Chat 4 demo surfaces only.** Daily scheduled tasks aren't subject to these — over-application produces bloat. Demo surfaces are one-shot; richness here is the proof, richness in daily morning briefs is noise.
   - **Named references, not adjectives.** Specific people, dates, project names, prior commitments, doc names.
   - **Cross-references over isolated data.** Voice contrast Output 3 MUST name 2–3 specific cross-references pre-computed from events.jsonl + entities.json. That's what makes "five words got me more than seventy words did" land.
   - **Concrete > generic.** Specific named decisions on specific dates beat "team had differing views on timing."
   - **No padding.** Omit empty sections.

### Setup widget rendering (Phase 0 widget — direct visualize render)

The Phase 0 setup widget renders as a **single progressive-reveal widget** via `mcp__visualize__show_widget` called directly with the inline HTML from `references/step1_widget_v2.html` — bypassing `shared/scripts/chat_output_renderer.py::render_chat_output_widget` and the standard CR brand-strip wrapping. **Four questions** batched in one widget (role / email exclusions / timezone / AI name), Q1 active first, the others dimmed and locked. Q1 has two-level drill-down (top chip → sub-chips → optional refinement textbox). Q2 / Q3 / Q4 are single-level (top chip → either advance or open textbox for the Other-style chip). Each completed question collapses to a checkmarked summary line and unlocks the next. The final Q4 Finish click fires one consolidated `sendPrompt('apply choices: [...]')` that apply-choices dispatches back to this skill's "Reply handling — Phase 0 setup" section below.

**Why direct render, not `render_chat_output_widget`:** (a) Phase 0 is the customer's first widget surface — the brand-strip wrapping earns little before they've seen anything CR-branded; (b) decoupling Phase 0 from the shared renderer means a future renderer regression cannot take down onboarding (the truncation incident of 2026-05-17 was the precipitating cause); (c) the widget is self-contained — fewer moving parts is fewer failure modes. The scheduled-task orchestrators continue to render via `render_chat_output_widget` — that path is unchanged.

NEVER as an `AskUserQuestion` chip bar. NEVER as a numbered markdown list. NEVER as the old `widget_mode: "onboarding_setup"` Apply-all batched submit.

---

## Detour-Return Protocol (mid-phase, preserved verbatim)

If the customer asks an off-topic question mid-phase (e.g., *"wait, can you tell me what this Acme Co thing is?"* or *"what's my calendar look like tomorrow?"*), do not silently drop the onboarding thread. Follow this three-step protocol:

1. **Answer the question briefly** (1–3 sentences). Do not expand it into a full side task. If the answer genuinely requires a full skill run (e.g., *"draft me an email to Sam right now"*), confirm: *"Happy to — that'll pause setup. Want to handle it now, or finish setup first?"*

2. **Name the return.** Explicitly state the current phase and the next sub-beat: *"OK — back to setup. We were about to [next specific thing]."*

3. **Ask once for the go-ahead.** *"Ready to keep going?"* Wait for confirmation. If the customer introduces another question, loop this protocol — do not enter an infinite deferral.

Never silently resume. Never assume the detour is over. The return is always explicit.

---

## Checkpoint Protocol (mandatory at every phase boundary)

At the **start** of each phase (Phases 0 through 6), append an event to `_hq/data/events.jsonl`:

```json
{"id": "evt_...", "timestamp": "<ISO>", "type": "onboarding_checkpoint", "phase": "<N>", "phase_name": "<name>", "status": "in_progress", "last_step": "<one-line description of the phase just entered>", "last_writer": "command-room-onboarding"}
```

Update the same checkpoint (by appending a new one — events.jsonl is append-only) whenever a sub-beat completes that would be painful to re-do (widget submitted, scan finished, voice profile saved, cr-m1-backfill registered, Workspace Map installed, Chat 4 first message rendered, all 5 Run Now clicks confirmed, training commands done).

At the **end** of onboarding (after Phase 6 lands), append a final checkpoint with `phase: "6"` and `status: "complete"`. This is what Phase 0's existing-workspace guard reads to decide between the "already-set-up" route and the "resume" route.

**Also at end of onboarding (preserved from v2.7.13):** append a `plugin_install` event so future runs of `command-room-update-bridge` can read it as a baseline:

```json
{"id": "evt_...", "timestamp": "<ISO>", "type": "plugin_install", "version": "<plugin_version_from_plugin.json>", "installed_at": "<ISO>", "actor": "command-room-onboarding", "fresh": true}
```

`<plugin_version_from_plugin.json>` is the `version` field in the plugin's `.claude-plugin/plugin.json`. `"fresh": true` distinguishes a clean onboarding install from upgrade-in-place runs.

Cost: ~one JSONL append per meaningful boundary. Idempotent. Appends only.

### Phase identifiers (canonical for checkpoint events)

| `phase` | `phase_name` |
|---|---|
| `0` | `setup-widget` |
| `1` | `education-and-scan` |
| `2` | `triple-beat` |
| `3` | `compounding-loop` |
| `4` | `run-now-ritual` |
| `5` | `training-prompts` |
| `6` | `coach-handoff` |

Use these exact values. Analytics / replay tooling keys off them.

**Legacy phase identifiers** (pre-M1, the onboarding-v2 5-step phases `1`–`5` from v3.x lines + the pre-v2 phases `0`–`7` from v2.7.x–v2.14.x) are deprecated as top-level checkpoints. Workspaces with legacy phase events should be treated as their nearest M1 equivalent for resume-route detection:

- onboarding-v2 `1` (setup-settings) → M1 `0` (setup-widget) — re-run the widget; the new Q4 (AI name) will collect what was missing
- onboarding-v2 `2` (scan) → M1 `1` (education-and-scan)
- onboarding-v2 `3` (reveal-compressed) → M1 `2` (triple-beat); proceed to Chat 4
- onboarding-v2 `4` (build-workspace) → M1 `2` (triple-beat); workspace was built, Chat 4 hasn't opened yet
- onboarding-v2 `5` (built-summary-handoff) → M1 `6` (coach-handoff); treat as complete if `status: "complete"`, else resume at Phase 4 Run Now ritual
- pre-v2 phases `0`/`1` → M1 `0`; `2` → M1 `1`; `3` → M1 `2`; `4` → M1 `2`; `5`/`6`/`7` → M1 `6`

Phase 0's existing-workspace guard reads the most recent `onboarding_checkpoint` event and applies this mapping before deciding the resume route.

---

## Phase 0 — Setup widget (T=0:00 → T=0:02)

**Where:** Chat 1.

**Customer action:** Opens Cowork → clicks "+ New Chat" → types `set up command room` → Enter.

### 0a. Workspace guard (route detection — preserved from prior onboarding versions)

Probe the mounted folder for workspace-shape signals. Route one of five ways:

1. **Already on latest (M1 complete, or onboarding-v2 complete)** — `_hq/data/entities.json` exists AND orgs carry `scope` field AND the most recent `onboarding_checkpoint` event in `events.jsonl` has `status: "complete"` (or no checkpoint events exist at all — legacy installs).
   → Stop. Say: *"Your Command Room is all set up already. Say **'new project [Name]'** to add a project, **'scan my files'** to take another look at your tools, or **'what version am I on'** if you want a quick health check."*

2. **Legacy plugin (v1.4 / v1.7 / v1.8) in place** — `_hq/MASTER_TRACKER.md` exists AND `_hq/data/entities.json` does NOT exist.
   → Stop with: *"Looks like you've got an earlier version of Command Room here with data already in it. Setup won't move that over for you — say `upgrade my command room` and I'll bring your existing stuff into the new format first. Once that's done, we can run setup on top of it."*

3. **Legacy JSON (v2.0 / v2.1) in place** — `_hq/data/entities.json` exists AND orgs carry legacy `type` field (no `scope`).
   → Same instruction as case #2 — onboarding doesn't migrate; user runs `workspace-ingest` separately.

4. **Fresh workspace** — none of the above. No `_hq/` folder, no registry files.
   → Proceed with normal onboarding flow starting at Phase 0b.

5. **In-progress onboarding (checkpoint exists, not complete)** — `events.jsonl` contains one or more `onboarding_checkpoint` events AND the most recent has `status: "in_progress"`.
   → Resume, don't restart. Read the last checkpoint's `phase` field, apply the legacy-mapping table above if it's a pre-M1 phase identifier, then say: *"Picking up right where we left off. We were on [phase name — last thing we did]. Want to keep going?"* Wait for confirmation, then resume from the next sub-beat. Do NOT re-run earlier phases, re-scan connectors, or re-seed files.

The explicit phrase `"restart onboarding"` forces a full re-run regardless of checkpoint state (archive the existing `events.jsonl` to `_archive/events_restarted_YYYY-MM-DD.jsonl` first). Phrases that imply data migration (`"upgrade my command room"`, `"ingest my existing workspace"`) do NOT trigger this skill — they route to `workspace-ingest`.

### 0b. Auto-detection trigger + intro

On the customer's first message, if no `_hq/MASTER_TRACKER.md` exists AND 0a routed to "fresh," activate this skill automatically. No trigger phrase needed.

**Intro line (exact wording, first message only):**
> *"This is your Chalette Command Room. Give me about 10 minutes — I'll scan your email, calendar, and meeting tools, build your workspace, and show you what it does. Five quick questions to start."*

All subsequent references use **"Chalette Command Room"** or **"Command Room"**. The name is fixed.

If no tools are connected, pivot to the cold start path. Read **references/cold-start-path.md** for activation.

### 0c. Setup widget — 4 questions (M1)

The Phase 0 setup widget renders four questions in one progressive-reveal HTML widget per the "Setup widget rendering" section at the top of this file. Source: `references/step1_widget_v2.html`.

**The 4 questions:**

| Q | Topic | Storage field |
|---|---|---|
| Q1 | Workspace shape (run-company / senior-leader / investor / client-work / nonprofit / other) with sub-chip drill-down | `workspace.shape` + `workspace.shape_detail` + `workspace.seniority` |
| Q2 | Exclusion domains (anything to skip from scan) | `workspace.exclusion_domains` |
| Q3 | Timezone | `workspace.user_timezone` + `workspace.schedule_timezone` |
| Q4 **NEW M1** | AI name (default "Penelope") | `workspace.brain_name` |

**Q4 wording (rendered inline in the widget):**
> *"What do you want to call your AI? Default is Penelope. Pick anything that feels right — Jarvis, Alfred, your own first name. She runs your Command Room and shows up by name in every chat ('ask Penelope,' 'Penelope said'). Her workspace folder becomes `[Name]'s Brain`."*

Note: spec numbering inside Cowork session-notes documents may use "Q5" for this question when counting Q1's compound (role + seniority) as two questions. The widget surfaces 4 questions to the customer; the apply-choices payload carries 4 tuples.

**Pre-widget fire-marker (MANDATORY — apply-choices uses this to identify the source skill):**

Before calling `mcp__visualize__show_widget`, append a fire-marker event to `events.jsonl`:

```json
{
  "id": "evt_...",
  "timestamp": "<ISO>",
  "type": "onboarding_setup_widget_emitted",
  "source_skill": "command-room-onboarding",
  "phase": "0",
  "data": {"widget_kind": "step_1_setup_v3"},
  "last_writer": "command-room-onboarding"
}
```

`apply-choices` Step 2 reads this event (timestamp within last 60 min) to identify `command-room-onboarding` as the source. The `widget_kind: step_1_setup_v3` value distinguishes the M1 widget (4 questions including AI name) from the pre-M1 `step_1_setup_v2` (3 questions) and the pre-v2 `step_1_setup` (4 questions, no sub-chip drill-down).

**No renderer pre-flight needed.** The widget bypasses `chat_output_renderer.py` entirely.

**Widget call:**

1. Read the inline widget HTML from `references/step1_widget_v2.html` (same plugin directory). The file contains the complete `<div>` markup + `<style>` + `<script>` block — pass it through verbatim, no transformations.
2. Pass the HTML as the body of a single `mcp__visualize__show_widget` call. NO accompanying markdown chat text, no header line, no "here are your questions" preamble — the widget is the entire surface for Phase 0c per `shared/CHAT_ACTION_WIDGET.md` MUST rule #2.

Q1 chips (top + sub), Q2 / Q3 / Q4 chips are baked into the HTML — do NOT regenerate them dynamically. Any future change to the chip taxonomy is an edit to `references/step1_widget_v2.html`, not a string-template patch from this skill.

**Wire shape on submission (4 tuples, `sub` and `input` optional per tuple):**

```
apply choices: [
  {"n":1,"action":"run-company","sub":"holdco","input":"3 portcos, fintech + AI tools"},
  {"n":2,"action":"none"},
  {"n":3,"action":"eastern"},
  {"n":4,"action":"default"}
]
```

Examples of the optional fields:
- `n:1` (role) always has `action`; has `sub` whenever the user picked a sub-chip (every top-level except top-level `other`); has `input` whenever the user typed into the refinement textbox (required for any chip flagged `other` at any level, optional otherwise).
- `n:2` (email exclusions) has `action: none` (no `input`) or `action: exclude` with `input` (textbox required when `exclude` picked).
- `n:3` (timezone) has `action: pacific|mountain|central|eastern` (no `input`) or `action: other` with `input` (required).
- `n:4` (AI name) has `action: default` (no `input`, customer kept "Penelope") or `action: custom` with `input` (the customer-typed name, required).

### Reply handling — Phase 0 setup (apply-choices dispatches here)

When `apply-choices` parses an `apply choices: [...]` payload AND its Step 2 source-identification reads the `onboarding_setup_widget_emitted` fire-marker event with `source_skill: command-room-onboarding` AND `data.widget_kind: step_1_setup_v3`, it dispatches each `{n, action, sub?, input?}` tuple back to this section.

Process exactly 4 tuples in order. (Pre-M1 widgets emitted 3 tuples — `step_1_setup_v2` — and workspaces with prior payloads in `events.jsonl` are unaffected; the route key is `widget_kind`, not tuple count.)

**Item 1 (role) — map `action` + `sub` to schema-compatible enum + seniority:**

| Q1 `action` | Q1 `sub` | `workspace.shape` | `workspace.seniority` |
|---|---|---|---|
| `run-company` | `single-op-co` | `operating_business` | `owner` |
| `run-company` | `holdco` | `holding` | `owner` |
| `run-company` | `family-business` | `operating_business` | `owner` |
| `run-company` | `other` | `operating_business` | `owner` |
| `senior-leader` | `c-suite` | `operating_business` | `exec` |
| `senior-leader` | `vp` | `operating_business` | `exec` |
| `senior-leader` | `director-below` | `operating_business` | `exec` |
| `senior-leader` | `other` | `operating_business` | `exec` |
| `investor` | `gp-fund` | `fund` | `owner` |
| `investor` | `board-director` | `fund` | `owner` |
| `investor` | `independent-advisor` | `fund` | `owner` |
| `investor` | `family-office` | `fund` | `owner` |
| `investor` | `other` | `fund` | `owner` |
| `client-work` | `consulting` | `service_business` | `owner` |
| `client-work` | `agency` | `service_business` | `owner` |
| `client-work` | `professional-services` | `service_business` | `owner` |
| `client-work` | `other` | `service_business` | `owner` |
| `nonprofit` | `exec-director` | `nonprofit` | `owner` |
| `nonprofit` | `senior-staff` | `nonprofit` | `exec` |
| `nonprofit` | `board-member` | `nonprofit` | `owner` |
| `nonprofit` | `other` | `nonprofit` | `owner` |
| `other` | (none — top-level Other skips sub-chips) | `other` | `owner` |

Where `input` is present on item 1, store the verbatim text in `workspace.shape_freetext`. Where `sub` is present on item 1, store the value in `workspace.shape_detail`. If `sub` is absent (only happens for top-level `other`), omit `shape_detail`.

**Per-shape default impact (applied in Phase 1 scan inference):** `operating_business` → one primary org; `holding` → multiple primary orgs; `fund` → portfolio-first orientation; `service_business` → client-first; `nonprofit` → program-first; `other` → standard.

**Item 2 (email exclusions) — capture or none:**
- `none` → no exclusions table written. Set internal flag `email_exclusions_set: true` so the Phase 1 workspace-build skips the Excluded Domains section.
- `exclude` with `input` → parse comma-separated domains, store in `## Excluded Domains` table rendered during the workspace build.
- `exclude` with no `input` → cannot occur (the widget marks the Exclude textbox required and blocks Finish until non-empty). If somehow received, treat as `none`.

**Item 3 (timezone) — map `action` to IANA tz:**

| `action` value | IANA `user_timezone` |
|---|---|
| `pacific` | `America/Los_Angeles` |
| `mountain` | `America/Denver` |
| `central` | `America/Chicago` |
| `eastern` | `America/New_York` |
| `other` (with `input`) | Map to IANA per the table below |

Free-text mapping (apply when `action: other` with `input`):
- Arizona / Phoenix → `America/Phoenix`
- Hawaii / Honolulu → `Pacific/Honolulu`
- Alaska / Anchorage → `America/Anchorage`
- London / UK / BST → `Europe/London`
- Paris / Berlin / CET → `Europe/Paris`
- Tokyo / Japan / JST → `Asia/Tokyo`
- Sydney / AEST → `Australia/Sydney`
- Other → infer from city name to nearest IANA zone. If genuinely ambiguous, write `America/New_York` as fallback + log `tz_set_by: "fallback_default"` in entities.json + surface in apply-time ack: *"I wasn't sure exactly which zone '[input]' is — I went with Eastern for now. Just say 'set my timezone to [name]' and I'll fix it."*

**Item 4 (AI name — NEW M1) — map `action` to `workspace.brain_name`:**

| Q4 `action` | `input` | `workspace.brain_name` |
|---|---|---|
| `default` | (none) | `"Penelope"` |
| `custom` | (required) | verbatim `input` (trimmed, max 40 chars; if empty after trim, fallback to `"Penelope"`) |

The `brain_name` field is read by every chat surface that names the AI:
- Chat 4 opening line (`"Hi — I'm <BrainName>. I run your Command Room."`)
- The workspace folder display (`<BrainName>'s Brain`)
- Every operator-narration cue (`"Pop over to <BrainName>'s chat"`)
- The `cr-m1-backfill` recap (`"<BrainName> is now using all of this in your home chat"`)
- Every scheduled-task first-fire signature line
- The coach skill's render

Customers can change the name later via `change my AI name to [name]` (handled by `workspace-manager`).

Write to entities.json `workspace` block:

```json
"workspace": {
  "shape": "<from item 1>",
  "seniority": "<from item 1>",
  "shape_detail": "<from item 1 sub, if present>",
  "shape_freetext": "<from item 1 input, if present>",
  "user_timezone": "<from item 3>",
  "schedule_timezone": "<from item 3>",
  "first_go_months": 1,
  "brain_name": "<from item 4>",
  "tz_set_by": "user_explicit",
  "tz_set_at": "<ISO>"
}
```

The `first_go_months: 1` field is written here unconditionally per M1 — workspace-manager reads this on first `go [project]` lazy deep-load. Customers can override later with `set first-go to N months`. Omit `shape_detail` / `shape_freetext` keys entirely when the corresponding source field is absent — do not write empty strings.

**Apply-time response (short plain-English ack, NO new widget):**

The customer already confirmed by clicking Finish. Don't re-render the form. Surface a 2–3 line confirmation that names them, their shape, their tz, and their AI by name:

> *"Got it — [first name from signature if known, else "you"], [readable shape][, sub-label if present][, with the freetext if present]. Timezone: [readable TZ]. [Email exclusions confirmed or 'no domains excluded'.] Your AI is named [<BrainName>]. She's starting her scan now."*

Then immediately enter Phase 1 (don't wait for further customer input — the orchestration takes over from here).

**Deliverable:** widget emitted; on Finish, the 4 selections write `workspace.shape` + `seniority` + `shape_detail` (when sub-chip picked) + `shape_freetext` (when refinement textbox filled) + email exclusion list + `user_timezone` + `schedule_timezone` + `first_go_months` + `brain_name` to entities.json. Checkpoint event written with `phase: "0"`, `status: "in_progress"`.

**Transition:** Operator-cue at top of Phase 1a opens Chat 2 in parallel; light scan continues in Chat 1.

---

## Phase 1 — Education + scan + Workspace Map + backfill authorize (T=0:02 → T=0:08)

This phase is distributed across **3 chats**: Chat 1 (light scan + workspace build), Chat 2 (substantive scheduled-task education + 5 registrations), Chat 3 (workspace map install). All three run in parallel.

### Phase 1a — Chat 2 education + light scan in Chat 1 (T=0:02 → T=0:07)

**OPERATOR (verbal):** *"While [BrainName] scans your workspace — quick side task. Open a new chat and type `set up command room schedules`. We're going to spend a minute in that chat while she scans here."*

**CUSTOMER:** Opens new chat → types `set up command room schedules` → Enter → **Chat 2** opens, triggering `enable-command-room-schedules`.

That skill's Phase 0.5 (substantive Chat 2 explainer — see `skills/enable-command-room-schedules/SKILL.md`) delivers the vanilla-vs-Command-Room contrast on scheduled tasks, then registers 5 first-install tasks (`morning-brief`, `past-meetings`, `inbox-triage` / `inbox`, `upcoming-meetings`, `weekly-recap` / `friday-wrap`) and surfaces the install summary. **~60–90 sec of operator-customer discussion in Chat 2 while light scan continues in Chat 1.**

**OPERATOR:** *"Head back to your main chat. [BrainName]'s wrapping up pass one."*

**Meanwhile in Chat 1, the light scan runs:**

This is the connector scan that builds the entity chassis (Tier A + Tier B from the M1 backfill architecture). Streaming progress updates render in Chat 1 during the scan — narrate purpose and signal, never volume that feels invasive.

#### 1a.i — Connector inventory (preserved from prior versions — generalized)

**Pre-flight inventory (MANDATORY, before any scan starts).** List every connector detected in the current chat session AND every expected connector NOT detected. No silent skips. Format as a single chat message before announcing the scan beats:

> *"Connected and ready: M365, Calendar, Granola. Not seeing: Slack, Drive.*
>
> *If you were expecting one of those to be there, it's probably a permissions thing — check Cowork Settings → Connectors and make sure they're turned on for this chat. Then say `re-scan` and I'll try again. I'll keep going with what I have for now."*

Detection rule: a connector is "detected" iff at least one of its MCP tools is callable from the current session. Do NOT infer connector availability from app-level Cowork status — only from session-level tool availability.

**Meeting-transcript sources are generalized.** Map any of these to the same role:

- Granola (`mcp__granola__*`)
- Fireflies (`mcp__fireflies__*`)
- Otter (`mcp__otter__*`)
- Read.ai (`mcp__read*__*`)
- Zoom AI Companion (`mcp__zoom__*`)
- Microsoft Teams meeting summaries (`mcp__teams__*` with summary scope)

If multiple are wired, all contribute. The inventory line above names whichever ones are detected.

#### 1a.ii — Extract from connectors (Tier A + Tier B — 60-day metadata window)

| Source | Window | Adaptive cap (ceiling) | Tiered fetch |
|---|---|---|---|
| **Gmail / Outlook** | 60d sent metadata + 60d received metadata | 600 sent + 600 received (pull all if under) | Headers + snippets only by default. Full body for top-10 most-important threads. |
| **Calendar (Google or Outlook)** | 60d back + 14d forward | No cap (typically <500) | Full event records |
| **Slack / Teams** | last 7d | Channel list + last 7d user's own messages + DMs/@mentions | No body reads on others' messages |
| **Drive / OneDrive / SharePoint** | last 30d modified | Top folders + recent 50 files | **Names + paths + dates only — never full body reads in Phase 1** |
| **Meeting-transcript sources** | last 30d | Last 10 transcripts | Summaries only. Full text fetched in Phase 1b by `cr-m1-backfill` for the 7-day deep window. |

The 60-day metadata window is Tier B from the M1 backfill architecture — cadence baselines + entity-volume confirmation. The Tier C deep read (full content, 7d) runs in Chat 5 via `cr-m1-backfill`, not here.

**M365 parity:** if the user is on Microsoft 365, use Outlook + Outlook Calendar + OneDrive + Teams + SharePoint.

**Local filesystem folders are deliberately absent.** Workspace ingest is handled by `workspace-ingest` separately.

#### 1a.iii — Streaming progress in Chat 1 (during scan)

Render purpose-and-signal updates as the scan progresses:

> *"Pulled metadata for 2,317 emails (60 days)."*
> *"Identified 24 organizations from email domains."*
> *"Found 83 unique people, top 15 marked for deep tracking."*
> *"10 active workstreams clustered. 4 paused. 6 archived."*
> *"Voice profile reading 30 of your most recent sent emails. Already noticing: em-dashes as connective tissue, no 'hope you're well,' direct asks."*

These are streaming hints during work — not the final voice of the build. Specific numbers + named patterns; never generic.

#### 1a.iv — Classify silently + build the chassis

Extract: **Orgs + tree** (structural spine), **Projects** (distinct workstreams), **People** (top contacts by frequency), **Team** (recurring 1:1s), **Communication Style** (from sent emails).

**Infer the org tree per `references/ORG_AND_THREAD_MODEL.md`** — three-stage process: (1) identify the user's own primary org first via signature + outbound domain, (2) volume-tier all other orgs by interaction count, (3) classify silently.

For each inferred org, guess: `scope` (holding | operating | division | brand | fund | other), `parent_org_id` if domain patterns suggest hierarchy, `relationship_type` (operating | partner | board | advisory | investment | client | portfolio_company | beneficiary | vendor | prospect | service_provider | other), `is_primary_focus`, `tier` (primary | secondary | external | passive).

**Primary-affiliation gate — apply BEFORE any other tiering:**

1. From signature blocks + primary email domain + Slack workspace ownership, identify the user's own primary affiliation. Mark this org `is_primary_focus: true`, `tier: primary`, `relationship_type: operating`. Auto-confirm — this is anchor truth, not inference.
2. If `workspace.seniority == "exec"`, the primary org is the company the user works FOR (not one they own). Apply the same tier/relationship treatment but record `entities.engagements[]` of kind `operating` with the user's role from their signature.
3. If multiple primary orgs detected (holding company case), all get `tier: primary`.
4. EVERY other org gets tiered RELATIVE to the user's primary.

**Interaction-volume tiers (unchanged from prior versions):**

| Interactions / 30d | Default tier | Default relationship_type | Confidence |
|---|---|---|---|
| 1–5 | `external` | `vendor` (or `prospect` if outbound-heavy + sales-language) | low |
| 6–20 | `external` | `vendor` (or `service_provider` if recurring monthly) | medium |
| 21–50 | `secondary` | `client` (most common) or `partner` if reciprocal volume | medium-high |
| 51–199 | `secondary` | `client` / `partner` / `advisor` based on signal pattern | high |
| 200+ AND on user's primary email domain | `primary` | `operating` | high |

After org tree is drafted, group each detected workstream under its most-specific org. Each project gets a `kind` guess based on content signals.

#### 1a.v — Build all files (per `references/WORKSPACE_SCHEMA.md`)

Key files written during the scan, with narration:

- **CLAUDE.md** — generate from `references/claude-md-template.md`. Fill with identity, preferences, top 15–20 people, active projects, terms, and `brain_name` from Phase 0. ~80 lines max.
- **MASTER_TRACKER.md** — populated with real projects. Template in `references/templates.md`.
- **BUSINESS_CONTEXT.md** — customer's "About Me for the AI." Template in `references/templates.md`.
- **COMMUNICATION_PROFILE.md** + **BRAND_VOICE.md** — from the voice scan of last 60 days of sent emails.
- **PROJECT_CONTEXT.md** + **PROJECT_BRAIN.md** + **SESSION_NOTES_[NAME].md** — one set per project.
- **_people/ folder** — `_team-config.md` + one `PERSON.md` per detected team member.
- **DECISION_LOG.md**, **PEOPLE.md** — views regenerated from entities.json + events.jsonl.
- **entities.json** — `person_001` with `canonical_name`, `workspace.brain_name`, all org records, project records. `workspace.first_go_months: 1`.

After this sub-beat, entities.json has the full primary user + org + project picture written.

#### 1a.vi — Quick Commands install (silent)

Call `enable-quick-commands` silently. Required placeholders:
- `CEO_DISPLAY_NAME` from `entities.json` `person_001.canonical_name`
- `LAST_BUILT` ISO timestamp

~5 sec cost. Surface as a tiny inline notification ("Quick Commands pinned to sidebar"), no narration interruption.

### Phase 1b — Combined beat: workspace map + backfill authorize (T=0:07 → T=0:08)

**OPERATOR (verbal):** *"Head back to your main chat. [BrainName]'s wrapping up pass one."*

**Customer returns to Chat 1.**

**CHAT 1 SHOWS (combined beat, no gap):**
> *"Pass one complete. [BrainName] now knows your 24 orgs, 83 people, 10 active workstreams. She's just registered a deep-read task in the background — it'll pull your last 7 days at full depth (transcripts, emails, decisions). You'll see it in your Scheduled section as `cr-m1-backfill`.*
>
> *Two quick clicks:*
>
> *1. Open a new chat and type `install workspace map` — that puts your org tree in the sidebar.*
>
> *2. In your Scheduled section, click `cr-m1-backfill` then Run Now — that authorizes the deep read.*"

**Behind the scenes:** Chat 1 registers the `cr-m1-backfill` one-shot scheduled task per `references/m1-backfill-orchestrator.md` BEFORE surfacing the above message. The registration is:

- `taskId: "cr-m1-backfill"`
- `description: "M1 Backfill - Command Room (one-shot)"`
- `recurrence: "once"`
- `fireAt: "<now + 60 seconds>"` (gives the customer time to click Run Now and authorize)
- `notifyOnCompletion: true`
- `prompt`: the body of `references/m1-backfill-orchestrator.md` (verbatim, no bootloader pattern — this is a one-shot, not a recurring task; bootloader's "read fresh on every fire" value doesn't apply)

If registration fails, surface: *"Couldn't register the deep-read task — your daily scheduled chats will fill in the gap as they fire. You can also retry from your home chat."* Continue to the two-click instruction (Workspace Map still useful).

**CUSTOMER ACTIONS:**
1. New chat → `install workspace map` → Enter → **Chat 3** opens, completes ~30 sec via `enable-workspace-map`
2. Scheduled section → click `cr-m1-backfill` → **Chat 5** opens → Run Now → approve permissions

**CHAT 5 SHOWS (immediately after Run Now, per the m1-backfill orchestrator Phase 0):**
> *"Authorized. [BrainName] is now reading your last 7 days at full depth — every email body, every meeting transcript, every document you modified this week. Extracting commitments, decisions, and follow-ups as she goes.*
>
> *This runs in the background. You don't need to do anything else here. When she's done, this chat will show you a structured recap of your last week. Come back when [Operator] tells you it's ready."*

**OPERATOR:** *"Now open one more new chat — type `show me what you know about me`. That opens [BrainName]'s home chat where you'll spend most of your time with her."*

**CUSTOMER:** New chat → `show me what you know about me` → Enter → **Chat 4** opens on Opus.

**Deliverable:** Chat 1 scan complete + workspace built; Chat 2 schedules registered; Chat 3 Workspace Map installing; Chat 5 backfill authorized and running. Checkpoint event written with `phase: "1"`, `status: "in_progress"`.

**Transition:** Phase 2 starts in Chat 4 the moment it opens.

---

## Phase 2 — Triple beat in Chat 4 (T=0:08 → T=0:19)

**Where:** Chat 4 (the AI's home chat, on Opus). This phase is the customer-facing substantive proof — Mirror v1, Voice contrast, then user-triggered Insights + Mirror v2 after the backfill completes.

### Phase 2a — Mirror v1 + Voice contrast (T=0:08 → T=0:14)

When Chat 4 opens via `show me what you know about me`, the skill's first message is signed by name:

> *"Hi — I'm [BrainName]. I run your Command Room.*
>
> *I'll show you what I see in two passes. First, what's clear right now from the 60 days of metadata I've already read. Then once the deep read of your last 7 days finishes in the background (we authorized it a minute ago — it's running in your Scheduled section as `cr-m1-backfill`), I'll show you what I notice in the specifics. The first pass is wide, the second is sharp.*
>
> ***Pass one.***"

#### 2a.i — Mirror v1 (entity-level reflection)

8–12 lines of prose (NOT bullets, NOT a list). Read the customer back to themselves in plain English. Specific names, specific workstreams, specific decisions if available from the metadata scan.

**Cover these dimensions (pick 6–8 — not all, not none):**

1. **Who they are** — name (from signature), role (from `workspace.shape` + `seniority`), business(es) by name.
2. **What they're working on right now** — their 3–4 most-active workstreams by name, the 2–3 paused ones by name. The contrast between active and paused proves the whole portfolio was read.
3. **Who matters** — people with deep `_people/` profiles named explicitly.
4. **How they work** — one line from `WORKING_STYLE.md` / `CLAUDE.md` in their own words.
5. **How they write** — one line on voice register from `BRAND_VOICE.md`.
6. **A volume cue** — total activity volume calibrates the daily-rhythm pitch that follows.
7. **A soft spot** — something specific that wouldn't show up in a generic scan (silence anomaly, paused-but-still-active project).

After the Mirror prose lands, anchor it to a workspace file:

> *"What I just said is saved as files in your workspace folder, `[BrainName]'s Brain`. The main one is `CLAUDE.md` — that's the file I read at the start of every conversation so I remember who you are without you re-explaining. Open it any time and edit if I got something wrong — these are your files."*

#### 2a.ii — Voice contrast (3-way prompt-AND-output)

Pick a real open thread tied to a real person from the scan:
- Must have at least one related thread or prior commitment in the workspace data (events.jsonl from the 1a scan).
- If the thread's full body isn't in the top-10 already pulled, fetch on-demand (~1–2 sec). Customer may see a brief "pulling [Person]'s thread" beat.

**Output 3 richness mechanism (data-injected, NOT vibes):**

Before generating Output 3, deterministically pre-compute 2–3 specific cross-references from events.jsonl + entities.json for the chosen thread:

```
cross_refs = []
# 1. Older threads with the same person
older = events.find(channel="email", counterparty=person_id, ts < 60d).top_3()
cross_refs.extend(f"older thread '{e.summary}' from {e.ts.strftime('%b %d')}" for e in older)
# 2. Related people on different threads referring to the same project
related = events.find(project=primary_thread_id, ts < 30d).distinct_people() - {person_id}
cross_refs.extend(f"{p.name} mentioned this on the {date}" for p in related[:2])
# 3. Prior commitments touching the same project
prior_commits = events.find(type="commitment", project=primary_thread_id, ts < 90d).top_2()
cross_refs.extend(f"prior commitment: '{c.text}' from {c.ts.strftime('%b %d')}" for c in prior_commits)
# Cap at 3 cross_refs total. Skip if fewer than 1 are available — fall back to thin-data branch (see below).
```

Inject the resulting `cross_refs` list **by name** into the prompt that generates Output 3.

**The three-way render:**

> *"Now — how knowing this lets me write for you. Same prompt — 'draft a check-in to [Person]' — given to three different AI setups:"*

```
PROMPT 1 — Generic AI (ChatGPT, no context):
  "Draft a follow-up email to [Person] about [topic]."

OUTPUT 1:
  [Generic boilerplate — generic phrasing, no project, no voice match]

────────────────────────────────────────────────────────

PROMPT 2 — Smart Stranger (good AI, voice tuned, requires manual context):
  [Long prompt — 50–80 words — where the operator manually pastes
   project background, prior commitments, related people, sequencing
   concerns, voice instructions. Show the customer the EFFORT cost.]

OUTPUT 2:
  [Decent quality — close to Output 3 but BOUNDED by what the
   operator manually included in Prompt 2.]

────────────────────────────────────────────────────────

PROMPT 3 — Command Room:
  "email [Person] re [topic]"   (5 words or fewer)

OUTPUT 3:
  [Substantively RICHER than Output 2 — references the 2–3 specific
   cross_refs computed above by name. Voice matches BRAND_VOICE.md
   patterns. Customer reads it and sees Output 3 named older threads /
   related people / prior commitments that Output 2 didn't.]
```

**Critical:** Output 3 MUST exceed Output 2 by naming the pre-computed cross-references. The proof is "five words got me more than seventy words did," powered by memory.

After the contrast lands, anchor it to files:

> *"The third one references your [specific cross_ref 1], your [specific cross_ref 2], the [specific cross_ref 3]. That comes from `BRAND_VOICE.md` (your writing style, learned from your last 60 days of sent emails) plus my memory of [Person] from `entities.json`. Same files you can open and read yourself."*

#### 2a.iii — Closing message before wait

> *"That's pass one. The deep read is still finishing — give it another minute or two. **When [Operator] tells you it's done, type `show me what's next` and I'll walk you through what I notice in the specifics.** You can also pop over to `cr-m1-backfill` in your Scheduled section to see the structured recap of your last week — that's what I'm reading from."*

#### Thin-data branches

The thin-data lock from prior versions carries forward. Choose ONE branch based on (a) sent-email count for voice signal, (b) cross-ref availability for Output 3 richness:

- **Branch A — Full (10+ sent emails AND 2+ cross-refs available):** fire the full three-way render above.
- **Branch B — Thin (3–9 sent emails OR fewer than 2 cross-refs):** fire all three blocks but surface an explicit thin-data note upfront: *"I don't have a lot to work with yet — but here's the comparison with what I have. The more you use me, the sharper this gets."* Use whatever cross-refs exist; if only 1 is available, instruct the prompt to name that one and surface a single inferred "what to surface next" hook.
- **Branch C — No data (<3 sent emails AND zero cross-refs):** surface an explicit defer message and skip the three-way render: *"I haven't seen enough of your emails or meetings yet to run this side-by-side. After you write a few emails and run some meetings through me, I'll have your voice down and we can do it then."* Skip to Phase 2b. Do not fabricate.

**Never half-fire** — every customer gets either the full three-way, the thinned three-way with explicit caveat, or the clean defer message.

### Phase 2b — Backfill complete + parallel read + Insights + Mirror v2 (T=0:14 → T=0:19)

Around T=0:14, Chat 5 completes its backfill per `references/m1-backfill-orchestrator.md` Phase 5. Chat 5 shows the structured recap (volume / captured / most-active workstreams / most-engaged people / decisions / open commitments older than 5 days).

**OPERATOR (verbal):** *"OK — deep read's done. Two things in parallel: type `show me what's next` in your [BrainName] chat to kick off her observations, then while she's thinking, pop over to `cr-m1-backfill` and read the recap she just produced. By the time you come back, she'll be ready."*

**CUSTOMER:**
1. In Chat 4: types `show me what's next` → Enter (Chat 4 begins synthesizing Insights + Mirror v2)
2. Opens Chat 5: reads the last-week structured recap (~2–3 min)
3. Returns to Chat 4

The `show me what's next` trigger routes through `command-room-coach` (post-handoff), but in this M1 moment Chat 4 is mid-flight inside this onboarding skill. The synthesis logic below runs **here**, not from coach — coach takes over after Phase 6 closes.

**Synthesis on Chat 4 (Opus, cached context from Mirror v1 + backfill recap):**

When the customer types `show me what's next`, render 2–3 Insights followed by Mirror v2 enrichment. Both derive from the same one-pull context (prompt-cached on the assembled backfill payload).

#### 2b.i — Insights (2–3 cards)

Each insight in this shape:

```
**[Observation as a headline — specific, named, dated]**
[2–3 sentences: the numbers + the interpretation + the named cost]
[Optional: what it implies — an inference, not a feature pitch]
```

**Priority insight classes (run all four computations silently in the synthesis pass, surface the 2–3 with the strongest signal):**

| Class | What you compute | What it surfaces |
|---|---|---|
| **Substrate-integrity** | Capture-vs-close ratios on commitments / decisions / drafts. Detection events without response events. | "47 commitments captured, 0 resolved — the capture surface is working, the close-the-loop motion isn't yet." |
| **Status-vs-reality mismatch** | Threads tagged `active` whose last-activity is identical to `paused` threads. | "Project X is tagged active but is behaviorally identical to your paused threads (both at 50 days since last touch). Status is performing aspiration, not reality." |
| **Cadence anomaly on named person** | For each person with `last_interaction` set, compute normal cadence (median gap over prior 60d) and compare to current silence. Flag 3+ sigma deviations on high-stakes people only. | "[Person] hasn't sent you anything in 28 days. Prior 60 days the cadence was 3–4 days. 4-sigma silence on a primary relationship." |
| **Future-self conditional fired** | Scan session notes, CLAUDE.md, decision notes for past-tense conditionals: "if X by [date]...", "flag me if Y...", "kill if Z..." Check whether the condition has now fired. | "Your March 12 session notes wrote 'kill the [project] auto-update if not shipped by May 1.' It's May 21. The conditional fires." |

Cap at 3 insights. Pick the gut-punchers — denial signal is the wow signal.

#### 2b.ii — Mirror v2 refresh (specific moments from the 7-day deep read)

After Insights, name specific moments from the deep read that round out the picture from Mirror v1:

> *"And some specifics from your last 7 days that round out the picture from above:"*

3–5 bullet-level moments: named decisions on dates, customer onboardings, project status shifts, person-specific moves. All sourced from the cr-m1-backfill recap (which is itself in cached context).

**Deliverable:** Mirror v1 delivered, Voice contrast proven, COMMUNICATION_PROFILE + BRAND_VOICE saved (during Phase 1's workspace build), Insights + Mirror v2 surfaced post-backfill. Checkpoint event written with `phase: "2"`, `status: "in_progress"`.

**Transition:** Phase 3 lands as the next message in Chat 4 (no operator pause).

---

## Phase 3 — Compounding loop via v1/v2 contrast (T=0:19 → T=0:21)

**Where:** Chat 4 (immediately after Phase 2b's Insights + Mirror v2 land).

**CHAT 4 SHOWS:**
> *"Quick note — what you just saw between pass one and pass two is the compounding loop in 5 minutes. Pass one was metadata-level — entity counts, voice profile, top relationships. Pass two added specific moments: the [Project A] onboarding, the marketing-split decision, the [Person B] silence pattern, the [Person C] shipping status.*
>
> *That's 5 minutes of additional reading producing meaningfully sharper context. Scale it out: every meeting you process, every decision you log, every follow-up you send compounds the same way. The [BrainName] you talk to in 60 days knows you better than the one you're talking to right now."*

Pick the names from the actual Mirror v2 content — never generic placeholders in customer-facing copy.

**Deliverable:** compounding loop framed via the v1/v2 contrast that just landed. Checkpoint written with `phase: "3"`, `status: "in_progress"`.

**Transition:** Phase 4 starts as the next Chat 4 message.

---

## Phase 4 — Run Now ritual for 5 scheduled chats (T=0:21 → T=0:28)

**Where:** Chat 4 sends the instruction; customer authorizes in Cowork's Scheduled section.

**CHAT 4 SHOWS:**
> *"Now your scheduled chats. Open your Scheduled section in the sidebar. You'll see 5 chats waiting: `morning-brief`, `past-meetings`, `inbox-triage`, `upcoming-meetings`, `weekly-recap`. Click each one and hit Run Now to authorize. The first run for each will produce real output — that's what they'll look like tomorrow on their normal schedule. I'll wait here. Come back when you've done all 5."*

**CUSTOMER:** Navigates to each, clicks Run Now, reads each output briefly (~1 min per chat = ~5–7 min total).

**OPERATOR (verbal, narrating while customer works through the 5):** *"Notice [BrainName] is producing 5 different outputs in parallel right now — that's the multi-task thing she does that's hard to do with most AI. She's running the morning brief, your past meetings, your inbox, your upcoming meetings, and your weekly recap simultaneously — different chats, different contexts, all at once."*

Each scheduled chat's first-run content uses the backfill data + 60d metadata. The first-run output is the customer's first taste of what each task produces. The Cowork-side display names that the customer sees in the Scheduled section may differ from the spec names above (canonical taskIds: `inbox` displays as "Inbox - Command Room"; `friday-wrap` displays as "Friday Wrap - Command Room"). The customer-facing spec names in this skill follow the M1 vocabulary; the actual sidebar names are what `enable-command-room-schedules` registered.

**CUSTOMER:** Clicks back to Chat 4 when all 5 are done.

**Deliverable:** all 5 scheduled tasks authorized via Run Now; first-run outputs produced. Checkpoint written with `phase: "4"`, `status: "in_progress"`.

**Transition:** Phase 5 next message in Chat 4.

---

## Phase 5 — Training prompts (T=0:28 → T=0:35)

**Where:** Chat 4 sends the instructions; customer fires each training command in a new chat (Chats 11, 12, 13).

**CHAT 4 SHOWS:**
> *"Three commands to try yourself. Each one opens its own new chat — that's the pattern: one focused task per chat. Open a new chat in your sidebar for each.*
>
> *1. `prep me for [your next real meeting]` — I'll surface what I know about who you're meeting, what's open, what to lead with.*
>
> *2. `tell me about [the most important person on your calendar this week]` — full context on who they are, your history with them, what's currently open.*
>
> *3. `draft a check-in to [the silent person from above]` — same as the example above, but rendered fresh for you to actually send if you want.*
>
> *Try the first one. Come back here when it lands."*

**CUSTOMER:** For each command — opens new chat (Chats 11, 12, 13), types, reads, returns to Chat 4. ~6–7 min total.

**OPERATOR (verbal between each):** brief acknowledgment + nudge to next.

**Deliverable:** 3 training chats fired; customer has muscle memory for the one-task-per-chat pattern. Checkpoint written with `phase: "5"`, `status: "in_progress"`.

**Transition:** Phase 6 next message in Chat 4.

---

## Phase 6 — Accomplishment summary + coach handoff (T=0:35 → T=0:40)

**Where:** Chat 4 — the final M1 message in onboarding, after which Chat 4 becomes the customer's permanent coach surface.

**CHAT 4 SHOWS (final M1 message):**
> *"That's the build.*
>
> *Today:*
> *• Your workspace folder `[BrainName]'s Brain` is populated with 60 days of your context*
> *• Voice profile calibrated from your sent emails*
> *• 5 scheduled chats firing on their cadence starting tomorrow*
> *• [N] commitments captured, [N] decisions logged from your last 7 days*
> *• 3 training chats you can revisit anytime*
> *• Workspace Map pinned to your sidebar*
>
> *This chat is now your home with me. Come back here anytime — that's how you'll spend most of your time with me going forward. I'll be your coach.*
>
> *We're done for today. [Operator] will pick it up from here."*

Numbers come from the actual `cr-m1-backfill` recap + the workspace as built.

**OPERATOR (verbal):** *"That's M1. [BrainName]'s running. If we have time, I can walk you through one of the coach prompts live — or you can try it on your own this week. We'll spend Meeting 2 going deep on the projects + people you want me to focus on."*

[Operator may continue with coach demo or end the call here — operator's choice based on time remaining.]

### 6a. Final checkpoint + plugin_install event

Append the final `onboarding_checkpoint` event with `phase: "6"`, `status: "complete"`. This is what 0a's existing-workspace guard reads to detect "already-set-up" on next session.

Also append the `plugin_install` event with `fresh: true` (per Checkpoint Protocol section above) so `command-room-update-bridge` has a baseline.

### 6b. Coach handoff

The `command-room-coach` skill takes over Chat 4 from this point. Subsequent visits to Chat 4 (or any chat where the customer fires a coach trigger phrase) re-enter `command-room-coach`, not this onboarding skill. The customer's `show me what's next` mid-M1 invocation was an in-place call (handled inside Phase 2b above); from Phase 6 forward, that trigger routes through `command-room-coach`.

**Deliverable:** Phase 6 message delivered, final checkpoint + plugin_install events appended, Chat 4 surface handed off to `command-room-coach`. Onboarding ends here.

---

## Cold Start Path

If no connectors are available, read **references/cold-start-path.md** for the interview-based flow. Cold start preserves the same 6-phase structure but Phase 1's connector scan becomes a guided interview rather than connector-derived.

---

## Feature Coverage & Post-Onboarding

Read **references/feature-reference.md** for the full feature table showing where each capability appears across M1, M2/M3/M4 (deferred), and the operator-driven follow-up sessions.

---

## What this skill does NOT do

- Does not fire on lifecycle commands like "new project [name]" — that's `workspace-manager`.
- Does not re-onboard once complete unless the customer explicitly asks ("restart onboarding", "reset my command room", or invokes an upgrade trigger — see Phase 0a).
- Does not import existing customer files. Workspace ingest is handled by the separate `workspace-ingest` skill — invoked outside onboarding.
- Does not configure connectors directly — surfaces the inventory for the customer to authorize via Cowork settings.
- Does not register the 5 daily/weekly scheduled tasks directly. Operator opens Chat 2 at Phase 1a (during the scan) to fire `set up command room schedules` — `enable-command-room-schedules` is the only path that registers them. Onboarding only registers the one-shot `cr-m1-backfill` itself, in Phase 1b.
- Does not run the historical 12-month backfill. Skipped per M1; customers can extend per-project via `backfill [N] months on [project]` anytime.
- Does not run a live briefing inside Chat 1. Chat 4 handles the substantive proof.
- Does not do a project deep-dive or person deep-dive inside Chat 1. Both are M2-era and on-demand.
- Does not execute the daily-product surfaces (morning brief, etc.) — those land in their own scheduled chats during Phase 4's Run Now ritual.
- Does not write outside `[WORKSPACE_ROOT]` — all seeding happens in the customer's workspace per `shared/PLUGIN_BOUNDARY.md`.
- Does not modify `_hq/skills/**` or any custom skill code.
- Does not install the sidebar dashboards directly. Workspace Map installs via Chat 3 (operator opens at Phase 1b). Quick Commands installs silently in Chat 1 at the end of Phase 1a.
- Does not skip phase announcements. Every phase entry begins with the announcement template — no exceptions.
- Does not ask in-chat corrections on org tree / project list / people. All classification is silent; corrections happen post-meeting through workspace-manager's natural-language flow ("move X under Y", "merge those orgs" — workspace-manager handles the surgical edit).
- Does not own the post-handoff coach surface. After Phase 6, Chat 4 is `command-room-coach`'s — onboarding does not re-enter it.
