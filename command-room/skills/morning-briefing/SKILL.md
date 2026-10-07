---
name: morning-briefing
surfaces: both
description: "Proactive daily digest — calendar, important email summary, overdue follow-ups, urgent items. Triggers: 'morning briefing', 'daily briefing', 'brief me' (bare, or 'brief me on today'), 'what do I need to know today', 'start my day'. Plus 'tune morning-briefing' and 'customize morning-briefing'. Shape of the brief, as settings: 'customize my morning brief', 'show me my brief with that', 'reset my brief', 'start my brief from a template', or one sentence naming any part of its shape — 'group my brief by workstream', 'lead with my calendar', 'keep it short enough for my phone'. DOES NOT fire on 'triage my inbox', 'process my inbox', 'what's in my inbox' — those go to inbox-triage for a deep classification + drafts pass. DOES NOT fire on 'brief me on the' / 'brief me about' (a topic brief — any subject that isn't today — workspace-manager loads the project and answers; one-pager-composer writes it up). DOES NOT fire on 'change my schedule' (change-schedule)."
---

## Step R: RESOLVE — once, before anything else, on every seat (BRIEFDOOR1)

This skill touches the customer's files ONLY through the workspace access
layer (`shared/WORKSPACE_ACCESS.md`, CONTRACT Rule 22 v6 — the Access preamble
at the bottom of this file). Every read below is one `run_helper` verb, every
write one `run_writer`, `write` or `append_jsonl` verb, and there is no python
block left in this file that opens a workspace file. On a merged seat this
chat runs in a container and the folder is on the customer's own computer —
an inline body opens nothing there, and a writer imported into a shell there
cannot name the account it writes for, so it refuses in one sentence and
saves nothing. That is exactly how the typed brief of 2026-09-23 rendered in
full and landed zero rows.

**THE FORM — every access line in this file is written this way:**

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan <verb> --json '{...}'
```

It is RENDERED HERE, in this chat's own shell, and it prints ONE line. On a
merged seat that printed line — `CR_PLAN_ORIGIN=container` and the writer
pair in front of `python3 "<the staged runtime>/…"` — is pasted into the
device shell UNCHANGED, and its one JSON envelope is the answer. On a legacy or local seat the same printed line runs
in this shell. The account this chat runs under lives in THIS shell, so the
line is never rendered on the device and a verb is never run from the staged
runtime by hand: a line rendered there carries no writer, and every write it
names is refused. The variables in front of `python3` are this chat's own
facts — the folder and the runtime from the resolve below, substituted into
every line as text so a fresh shell still carries them (shell state does not
survive between tool calls), and `CR_TRIGGERED_BY=morning-brief`, which is
how every receipt this turn writes says which surface asked for it. `<WS>` in
every line is the workspace root from the same resolve.

**Merged seat** (`CR_ENV=merged_cloud` and the device tools are present):

1. **Discover.** Run the Access preamble's four lines, then `cd "$PLUGIN_ROOT"
   && python3 shared/scripts/workspace_access.py discover`, and hand the
   block it prints to the device shell. Keep its one JSON line: `WS` = its
   `ws`, `RT` = its `staged_root`, `BRAIN` = its `brain_file`.
2. **Check the runtime.** `cd "$PLUGIN_ROOT" && python3
   shared/scripts/workspace_access.py expect`, here. `runtime_present: false`,
   or a discovery `manifest_sha` that is null or differs from the one
   `expect` printed, is a STOP: post exactly *"Command Room's runtime is not
   installed in your workspace yet, so this brief did nothing. Say `update
   command room` and ask again."* and end the turn. There is no container
   fallback and no hand-run of a verb from the staged runtime.
3. **Name the folder.** `DEVICE` = the entry in `get_device_info`'s
   `connectedFolders` whose last path segment is `WS`'s last path segment —
   the folder as the customer's own computer spells it. Substitute it for
   `<DEVICE>` and `RT` for `<RT>` in every line below, and `export
   CR_DEVICE_WORKSPACE="$DEVICE" CR_STAGED_ROOT="$RT"` in the shell you
   render from. Without `<DEVICE>` the rendered line carries no writer pair
   and every write this brief makes is refused.
4. **Read the brain file.** When `BRAIN` is not null, `plan read` it first —
   `{"rel": "CLAUDE.md"}` in THE FORM — before anything else this brief does.
5. **Never set `CR_FIRED_VIA`.** A typed brief is not a scheduled fire; the
   receipts land `fired_via: manual` because nothing here says otherwise.

**Legacy or local seat** (the files are on this filesystem): the Access
preamble's four lines resolve `$PLUGIN_ROOT` and `$WORKSPACE`. `<DEVICE>` and
`<WS>` are `$WORKSPACE`, `<RT>` is `$PLUGIN_ROOT`, and the printed line runs in
this shell with `CR_WORKSPACE="$WORKSPACE"` in front of it. No pair is
rendered on these seats and none is needed: their writers keep the identity
they have always carried.

An `ok:false` envelope is a stop: say what could not run in one plain
sentence, never why, and never run the operation another way. A
`writer_identity_required` envelope carries its own `line`; that sentence is
the whole answer about it.

## Deterministic state computer (mandatory, v3.14.8+)

> **The commitment header counts (`counts["headline"]`: you owe / owed to you / unowned / unconfirmed, plus overdue — v4.5.2 R4, the one bucket export) and the "ball is on you" Needs-Attention list MUST come from `shared/scripts/commitment_state.py::compute_brief_state(...)` (promoted from brief_state.py in Phase 2 Stage A; `brief_state` remains a working import alias) — NOT from re-deriving the open/overdue/whose-turn/drop rules in prose. Its `counts` block delegates to `commitment_state.count_commitments` — the ONE counting API every surface shares. This is the single source of truth for that computation; Steps 3b/3c/3c-bis below document the rules the function already implements and tell you how to gather its inputs. Your job at runtime is to FETCH the inputs (open commitments, per-thread latest-sender, calendar events, per-thread last-activity) and pass them in, then RENDER what comes back. Do not recompute the drops yourself — the function decides, and it is unit-tested (`tests/run_brief_state_test.py`).**

The drop logic drifts when re-derived in prose (the v3.14.7 calendar-close bug was one instance — see references/HISTORY.md). `compute_brief_state` collapses it into one tested function so the same inputs always produce the same surfaced list. See Step 3d for the call.

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

For the open-commitment / overdue-follow-up sections, you MUST call `shared/scripts/cru_match.py::load_open_commitments(events_jsonl_path)` — do NOT hand-roll an events.jsonl scan. The canonical helper handles closure-suppression (v3.11.4 `data.target_id` defensive id-field), malformed-line tolerance (Sub-bug #14b 2-layer defense), and dual-shape confidence values. If `load_events_defensively()` reports `skipped` lines (substrate corruption), surface a soft banner to the user: "A few entries in your activity log look incomplete — I'll tidy those up during this weekend's cleanup." Do NOT silently filter.

**INTAKE (2026-07-31) — what you RENDER is the confirmed half.** `load_open_commitments` is deliberately unfiltered: it is the projection primitive, so it still carries UNCONFIRMED extractions (`data.pending_review`) the extractor merely guessed at. Any list of rows this brief surfaces — Needs Attention, overdue-by-attendee, the team flags — takes `cru_match.split_pending_review(opens)[0]` and renders that half only; the unconfirmed items are needs-your-call queue members and reach the CEO as ONE labelled pointer line, never as rows folded into the day's work. There are exactly TWO exceptions, and neither is a licence to widen the rule.

**Exception 1 — the raw list you hand to the counting API.** `compute_brief_state` / `count_commitments` need the pending rows IN the input, because deriving the `unconfirmed` pointer from them is their job (the two code blocks below say so on the line).

**Exception 2 — the meeting-linked calendar sub-lines (`state["meeting_linked"]`, the F-44 carve-out).** Those rows do NOT come from a list you split: `compute_brief_state` builds `meeting_linked` over the FULL open set deliberately — pending rows included, by design — because an item about a meeting happening today must not be hidden from the CEO walking into that room. They arrive pre-built from the helper and are LABELLED at the point of render (see Today's calendar, below), which is what keeps them asks-to-confirm rather than settled facts. Do not "fix" them here by re-splitting: the split would have to happen inside the helper, and whether it should is an open product-doctrine decision (INTAKE2 build record §10), not something this file may decide. Every OTHER row list in this brief is governed by the rule above, with no third exception.

For any name-bearing follow-up ("brief me on Sam's status"), call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` before grep. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.1)

- **Use morning-briefing for:** the 60-second daily scan. Calendar + 1-line email summaries + tracker urgency + Slack digest. Designed for scheduled fire at 7:30am weekdays.
- **Use `inbox-triage` for:** the deep email pass — 5-bucket classification (Reply Now / Decision Needed / FYI / Discard / Deep Read) with 2-3 drafted replies. Runs on demand or in sequence after morning-briefing.
- **Pair pattern:** morning-briefing runs first (context), inbox-triage runs second (email action). User can trigger both with "brief me + triage my inbox" or schedule them in sequence.

The email section of morning-briefing is intentionally summary-only — if the user wants drafts, they call inbox-triage.

## Personification Contract (v3.13.8.4+)

Before rendering the briefing, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The briefing chat intro line uses the shape `"Morning, {first_name} — {brain_name} here with today's read."` (default `{brain_name}` = `"Penelope"`); the scheduled-task .docx signature line is `"— {brain_name}"` (already implemented in v3.13.8 scheduled-task orchestrators). Don't over-name — one reference in the intro + one in the signature is the rhythm. **Persona precedence (STYLE1 D6):** that intro shape is the DEFAULT, not a mandate — if the workspace CLAUDE.md carries the persona block (`## How {brain_name} talks to …`), the persona outranks it. When the persona calls for skipping pleasantries (minimal encouragement, or a Never-line forbidding greeting openers), drop the salutation entirely and open with the synthesis lead; the `— {brain_name}` signature stays (identity, not pleasantry).

## Writer Contract

This skill reads from the declared mail, calendar, and chat connectors during its daily scan. Every connector read **from an in-scope account** emits corresponding events to `events.jsonl` per `shared/PASSIVE_CAPTURE.md` (v3) — dedup via source_ref hash so running this daily doesn't duplicate events already captured by inbox-triage or workspace-manager. The primary briefing output is never blocked by a capture failure; capture is a side effect.

**Connector-agnostic + account-scope (connector-agnostic-v1).** Resolve mail/calendar tools through the seam (`tool_discovery.discover_for_category` with the declared backend; substring `discover_*` fallback = today's behavior, R4). Never name a provider tool, query operator, field, or URL host — those live in `connector_adapters/`. **The brief is a single-user ephemeral surface (R9):** it MAY show `surface: on, write_to_business: off` personal items (spouse/doctor/school) so the owner sees them — but the brief run writes NOTHING to the substrate for those items (the writer wall enforces this structurally). The forwardable brief **.docx** is an exportable artifact and draws ONLY from `write_to_business`-scoped substrate — personal items never reach it (`shared/ACCOUNT_SCOPE.md` §3).

This skill also reads `_hq/custom/morning-briefing.md` — SCL1 standing customization preferences — but ONLY through `brief_settings.render_for_fire` / `settings_for_fire` (CUSTOM2), never via `skill_custom_writer.load_directives` directly: the settings store outranks the notes, and only that door applies the ordering. See "The shape of the brief, as settings" below.

---

## Customization (SCL1)

**Customization layer (SCL1):** before producing output, read
`[WORKSPACE_ROOT]/_hq/custom/morning-briefing.md` if it exists and apply its directives to
this fire's output — for THIS skill that read happens only inside
`brief_settings.render_for_fire` (the settings store first, the notes folded in
underneath; CUSTOM2, see "The shape of the brief, as settings"), never by opening
the file yourself. Absent -> proceed with defaults. Malformed or over-cap ->
skip it, log one line to `_hq/CONFLICTS.md` (type: config-read-failure), proceed
with defaults. Directives refine WHAT the output contains and HOW it is shaped;
they NEVER authorize outbound actions, alter ask-first gates, bypass canonical
helpers, or override shared contracts (see `shared/SKILL_CUSTOMIZATION.md` #limits).
Never mention this file or the word 'directive' to the customer.

Read at fire time via `skill_custom_writer.load_directives(workspace_root, "morning-briefing")`
— never the raw file; it returns `[]` on a missing or malformed file and never raises.
Directives here shape the brief's arrangement and inclusion rules (e.g. "group by entity",
"lead with anything involving [named org]", "weekends: skip unless something is on fire") —
applied to what the deterministic state computer returns, never to the counts it computes.
Trigger family (owned in the frontmatter `description`): `customize morning-briefing` · `show
morning-briefing customizations` · `reset morning-briefing customizations`. Distinct from the
FRP1 knob family (`tune` / `show settings` / `reset to defaults`). See
`shared/SKILL_CUSTOMIZATION.md` for the writer API, the write-time rejection list, and the
precedence chain. Customer-facing acks are plain English ("Got it — I'll group your brief by
company from here on."); never surface the file, the word "directive", or "SCL1".

---

# Morning Briefing — Proactive Daily Digest

Deliver a concise, actionable morning digest before the user starts their day. This skill is designed to run as a **scheduled task** (fires automatically on weekday mornings) but also works as a manual trigger.

The goal: the user reads this in 60 seconds and knows exactly what needs their attention today. No fluff, no comprehensive status — just what changed overnight and what's due.

## When This Runs

| Mode | Trigger | Output |
|------|---------|--------|
| **Scheduled** | Fires via the `morning-brief` scheduled task (weekdays, per schedule config) | The orchestrator posts the digest as a markdown chat post in the Morning Brief chat |
| **Manual** | "morning briefing", "daily briefing", "brief me" | Same digest, displayed in chat |

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`).
All three decisions are **show-then-tune (STT)** — the brief always renders first, then offers
one-tap changes. Nothing here blocks the digest (CONTRACT Rule 17). Read config through
`get_config` — never the raw file.

The settings are read through the access layer, one verb, in THE FORM
(Step R) — rendered here, the printed line pasted where the data is:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "morning_brief_helpers:brief_config"}'
```

The answer is `{config, configured, defaults}`: `config` is
`skill_config_writer.get_config(workspace_root, "morning-briefing", DEFAULTS)` —
the saved choices deep-merged over the defaults — and `configured` is
`is_configured(...)`. The DEFAULTS have one home, `morning_brief_helpers.BRIEF_DEFAULTS`:

```json
{
  "depth": "headline",
  "leads_with": "synthesis",
  "going_quiet": {"enabled": true}
}
```

`depth` is `headline` (headline-first + Top 3) or `full`; `leads_with` is
`synthesis`, `calendar` or `commitments`; `going_quiet.enabled` turns the
"Going quiet" section on or off.

**The first-fire save is a WRITE, so it goes through the write door.**
`save_skill_config` writes the settings file AND its
`skill_first_run_configured` event, which makes it a writer — never a helper,
and never a `plan write` of a JSON file you composed (that loses the event).
On the FIRST fire only (`configured` is false):

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_writer --json '{"args": {"config": <the defaults from the answer above>, "skill_name": "morning-briefing", "workspace_root": "<WS>"}, "name": "skill_config_writer:save_skill_config"}'
```

The Tune mode's write is the same line with `"is_reconfigure": true`, through `plan run_writer` and never in a shell.
The Reset mode (`wipe_skill_config`, table below) has NO door route (the raw writer takes a file name the chat supplies, so it is kept off the write list; unmigrated):
on a seat whose session holds no workspace, say in one sentence that resetting the brief settings is not available on this computer yet, and change nothing.

`depth` selects Step 4 layout (`headline` = synthesis lead + Top 3 + scannable; `full` = every
section expanded). `leads_with` sets what the digest opens with. `going_quiet.enabled=False`
suppresses the "Going quiet" section. These read via `get_config`; the legacy CLAUDE.md
"Briefing Delivery" prose remains a fallback ONLY for the delivery-channel knob (Slack/email/file)
which is not a first-run decision.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "morning briefing", scheduled fire | render the digest with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "morning-briefing", DEFAULTS)` BEFORE rendering, then append the first-run block AFTER the digest. |
| **Show settings** | "show morning-briefing settings" | render current config in plain English; no digest. |
| **Tune** | "tune morning-briefing" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-render the digest with new settings. |
| **Reset** | "reset morning-briefing to defaults" | `wipe_skill_config(workspace_root, "morning-briefing")` → next fire is a first-fire again. Unmigrated: on a seat whose session holds no workspace, typed Reset changes nothing and says so in one sentence (above). |

**The first-run block (transport split):**

- **On-demand chat fire (this skill, manual mode):** a 2–3 line FOOTER after the digest (chat
  output, no widget — so MUST-NOT rule 5 does not apply):
  > *First time briefing you. I made 3 calls: **headline-first depth** · **leads with synthesis** ·
  > **Going-quiet section on**. Say "tune morning-briefing" to change any, or just tell me
  > ("lead with my calendar" / "go full detail").*
- **Scheduled fire (`orchestrator-morning-brief.md`):** the same three decisions ride as
  `fr1`/`fr2`/`fr3` items in a "Make this yours" section at the BOTTOM of the all-batch widget
  (the documented fr-item preselect exception — see `shared/CHAT_ACTION_WIDGET.md`). Tap →
  apply-choices `{n:"fr1", ...}` → `save_skill_config(..., is_reconfigure=True, origin="first_fire_override")`.

The block renders exactly once ever (`is_configured` gate), on whichever surface fires first.

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "go full detail" / "show me everything" | `depth = full` |
| "keep it short" / "headline only" | `depth = headline` |
| "lead with my calendar" | `leads_with = calendar` |
| "lead with what I owe" / "open with commitments" | `leads_with = commitments` |
| "lead with the synthesis" / "open with the theme" | `leads_with = synthesis` |
| "turn off going-quiet" / "stop the going-quiet section" | `going_quiet.enabled = False` |
| "show me going-quiet again" | `going_quiet.enabled = True` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-render the digest + confirm in one line. Day/time of the scheduled fire is NOT a morning-briefing setting — that's `change-schedule` (the morning-brief task), not tune.

## The shape of the brief, as settings (CUSTOM2)

Ten named parts of this brief are settings, not a conversation to be had again:
**how it is grouped · what comes first · how deep each section goes · the words it
uses · how long it runs · when something counts as late · what it leaves out · how
far back it looks · whose voice it is in · whether the coaching line shows.**

`shared/scripts/brief_settings.py` owns all ten — the defaults, the questions, the
sentences that set them, the resets, the five templates, the render, and the
migration. This section narrates it; it never restates it.

**Every fire reads settings first and the free-text notes second.** One call, and
it is not optional — a fire that reads `_hq/custom/morning-briefing.md` directly
has skipped the ordering rule:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"surface": "morning-briefing", "view": <the view: number_line, rows, sections>, "workspace_root": "<WS>"}, "name": "morning_brief_helpers:render_for_fire"}'
```

The answer is `brief_settings.render_for_fire(workspace_root, view,
surface="morning-briefing")` itself — call it `out`.

`render_for_fire` is `settings_for_fire` and `render_surface` in one call, and it
is the door to use: it reads the store, folds the leftover free text in
UNDERNEATH the settings, and renders. A note that speaks about something already
set is dropped, and `out["notes"]["overridden"]` says so. Stated outranks
written-down, always.

Reading the two halves yourself is allowed and the ordering is not optional —
`render_surface` REFUSES free text it was not also handed the list of stated
settings, because a fold done without it silently lets the note file outrank
the reader:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"surface": "morning-briefing", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:settings_for_fire"}'
```

The answer is `{settings, notes, stated}` —
`settings_for_fire(workspace_root, "morning-briefing")`'s pair, and
`stated_axes(...)`, the list `render_surface` must be handed with the notes.

**Rendering with them.** Hand `render_surface` the day's content and the settings;
it groups, filters, folds, shortens and orders, and it runs `assert_number_leads`
over what it composed. The plate's number line always leads — a preference cannot
move it, and the vocabulary never rewrites it.

```python
view = {"number_line": plate["line"], "rows": rows, "sections": sections}
out = render_surface(view, settings, surface="morning-briefing")
```

**Do not supply a coaching line here — it is already in the lead.** The
`coaching_line` setting is a render switch and nothing more: it says whether a
coaching line would print, never whether this brief is allowed one. Whether
coaching is earned and chosen is a different question with its own doors, and
nothing in THIS section reads them. Since BRIEF2 (2026-09-14) the DRIVER reads
all three gates — the coaching shape, this switch, and a behaviour on the
coaching object — composes the one line itself and places it inside
`pack["lead"]`, where it is scanned by the leak gate and fenced by
`assert_brief_never_asks` like every other sentence the brief writes. So this
`view` passes **no** `coaching_line` key: doing so would print the same line
twice, once from the lead and once from `render_surface`'s own branch.

`out["said"]` carries anything the settings did that the reader has to be told
about — a filter naming something that is not on the plate today is ignored
rather than obeyed, and says so, because a filter may empty the surface but it
may never do it silently under a number that keeps counting. Those lines are
already in `out["lines"]`, directly under the number.

**Mode dispatch — the shape conversation:**

| Mode | Trigger | Behavior |
|---|---|---|
| **Walk it** | "customize my morning brief" | `conversation_step(view, settings, answers=...)` — render today's brief, ask ONE question, take the answer, call it again. The re-render is the return value, so every answer is followed by the brief as it now reads. On `is_keep(text)`: `apply_settings(...)` once, print its `receipt`, stop. |
| **One sentence** | "group my brief by workstream", "lead with my calendar", "keep it short enough for my phone" — or any one sentence naming one of the ten | `apply_sentence(workspace_root, text)` — applies on its own, no walk. Print the returned `receipt`. Unparsed → print the returned `reason` and ask which part they meant. **A sentence that says what they do NOT want is refused, never inverted** — "never group my brief by person" sets nothing, and the returned `reason` asks what they would like instead. |
| **Preview** | "show me my brief with that" | `render_surface(view, working_settings)` and print it. Nothing is written until "keep it". |
| **Start from a shape** | "start my brief from a template" — then the name (workstream, people, calendar-first, money, minimal) | `apply_template(workspace_root, name)`. |
| **Reset** | "reset my brief" | `reset_settings(workspace_root)` — all ten back to how they shipped, receipted, undoable. One axis: `reset_settings(..., axes=["organization"])`. |
| **Move the notes in** | first fire after this release, or "customize my morning brief" on a workspace with standing notes | `migrate_directives(workspace_root)` — every note that names a setting becomes one, at most five lines stay as free text, one receipt, one `undo` that restores the notes byte for byte. Notes past the cap are taken OUT of the file and the receipt quotes each one by name, because `undo` is no use to a reader who was not told there was something to undo. A note that says what they do not want stays free text — it is never read as a setting for the thing it rules out. `plan_migration` shows the whole thing first without writing. |

Every one of these is ONE act with ONE receipt and ONE `undo`. The settings write
lands in BOTH this skill's store and `end-of-day`'s, under one batch — which is why
"group my brief by workstream" moves the morning brief, the day-close and the
weekly wrap's grouping in a single sentence.

**The five templates** (`brief_settings.TEMPLATES`): **workstream** — everything
under the workstream it belongs to, what you owe first. **people** — grouped by who
it involves. **calendar-first** — the day as it will actually run. **money** —
anything with a number attached, first. **minimal** — one line a section, short
enough for a phone. The workstream template is the shape a workstream-organized
reader used to maintain by hand in free text; nothing of any particular workspace
travels in it, only the settings.

## Workspace Structure Reference

- Tracker: `[WORKSPACE_ROOT]/_hq/MASTER_TRACKER.md`
- Business context: `[WORKSPACE_ROOT]/_hq/BUSINESS_CONTEXT.md`
- People: `[WORKSPACE_ROOT]/_hq/PEOPLE.md`
- Team: `[WORKSPACE_ROOT]/_people/` (if exists)
- Projects: `[WORKSPACE_ROOT]/[Project Name]/SESSION_NOTES_[NAME].md`

## Step 0: Catch the upkeep up first (HEAL1 — on demand only, and silently)

**Skip this whole step on a scheduled fire.** A seat whose scheduler works already runs the background upkeep on its own cadence. A seat whose scheduler cannot reach the workspace never gets here at all. This step exists for the one moment that is reliable on every seat: the customer opened their workspace and typed.

**Never judge for yourself whether the upkeep is owed — ask, and do what comes back.** One verb, in THE FORM (Step R), on every seat:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"surface": "morning-brief", "workspace_root": "<WS>"}, "name": "maintenance_dispatcher:catch_up_plan"}'
```

Its `result` is the plan — call it `plan`.

1. **`plan["catch_up"]` is false → do nothing, go to Step 1.** It is false whenever the last upkeep slot was served, whenever another surface caught up minutes ago, and whenever nothing is due. Write no receipt for a false plan: an empty one would make a stopped scheduler look alive.
**Run each job by its OWN leg, not by its name (FIX3 F3-6, ruling R-RW-5).** A row in `plan["jobs"]` whose `leg` is not empty is a SCRIPT job, and it runs through the write door — ONE line per job, in THE FORM, naming the job and the two answers this brief owes it:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_writer --json '{"args": {"fired_via": "manual", "job_id": "<the job's job_id>", "triggered_by": "morning-brief", "workspace_root": "<WS>"}, "name": "maintenance_dispatcher:run_job"}'
```

The job writes its own rows and its own receipt through its own writers, beside the data, under the writer the line carries — `fired_via: manual`, `triggered_by: morning-brief`, never `scheduled`. It is complete when the envelope is `ok: true` and its `result` says `ran: true` with `returncode: 0`; anything else is a failed job. Never paste `job["leg"]` into a shell yourself: that runs the job where no writer is named, and on a merged seat it refuses. Where the job is a SKILL rather than a script `leg` is empty: run that skill, and its receipt call takes the two answers as ARGUMENTS — `fired_via="manual"` on every one, and `triggered_by="morning-brief"` wherever the composer takes it — on EVERY seat, both branches. An argument wins; the environment is read only on a merged seat (ruling R-M3-10), so on a legacy seat a composer that saw only the exported variables would write `scheduled` beside `triggered_by: morning-brief`, which contradicts itself. The receipt calls, as this brief makes them:

```text
session_sweep.sweep_and_receipt(WORKSPACE_ROOT, items, sessions_scanned=n, fired_via="manual", triggered_by="morning-brief")
eod_incremental.log_capture_pass_receipt(WORKSPACE_ROOT, fired_via="manual", window=window, n_meetings=n, n_processed=n, n_skipped=n)
```

These arguments are THIS brief's, and they override the job skill's own default: the session-sweep skill's receipt step says `scheduled` for its nightly fire, and a typed brief is not that fire, so inside this catch-up the sweep's receipt call carries `fired_via="manual", triggered_by="morning-brief"` exactly as above, on every seat. The sent-mail and chat jobs are the reconcile-sent skill's own: this brief never reconciles, it READS what that task wrote. When the plan hands one to this catch-up, run that skill end to end with the same two answers passed to its own receipt calls.
 This is not decoration: on 2026-09-21 four upkeep jobs ran inside a hand-typed morning brief and all four recorded themselves as a scheduled fire, because "execute each job's skill end to end" gives a flag nowhere to go.

2. **True → run `plan["jobs"]` BEFORE this brief gathers**, in the order they come back, one at a time, never in parallel — a script job by its one `run_writer` line above, a skill job end to end. Never widen what counts as complete by hand. The weekday family only — the Sunday family is held back for `weekly recap` and `run maintenance`, and the plan has already done that filtering.
3. **Then ONE receipt, and only because jobs ran** — the `maintenance_run` row, composed where the data is by its own writer with its append held, then landed. Never typed:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"fired_via": "manual", "jobs_completed": [<job ids>], "jobs_due": [<job ids>], "jobs_failed": [<job ids>], "triggered_by": "morning-brief", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:plan_maintenance_receipt"}'
```

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan append_jsonl --json '{"holder": "morning-brief", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the maintenance receipt answer above, in order>]}'
```

4. **`plan["refused_container"]` is not empty → those jobs are not yours to run from here.** They write, and a write only happens where the files are. They stay owed and the next run from the right side of the bridge picks them up. `plan["refused_line"]` is the sentence for `run maintenance` and the health check — never for this surface.
5. **SAY NOTHING ABOUT ANY OF IT.** Not a count, not "caught up first", not a mention. The customer asked for a briefing, not for a status report on the machinery. What the jobs DID to their own rows reaches them exactly the way it always has — through CHANGED, credited by door, each batch with its one undo. The condition of the plumbing belongs to the health check and the weekly maintenance report and nowhere else, and this surface's own fence reds by name if those words reach the text.

## Step 1: Load Core Context (Fast)

Read only what's needed — this must be lightweight. Each read is ONE `plan read` in THE FORM (Step R) — `{"rel": "_hq/MASTER_TRACKER.md"}`, `{"rel": "CLAUDE.md"}` — never a file opened in a shell:
1. Read `_hq/MASTER_TRACKER.md` — project list, commitments, next actions, waiting-on
2. Read `CLAUDE.md` if it exists (hot cache for people, projects, terms)
3. Do NOT read per-project session notes or brains — this is a scan, not a deep dive (the per-project context load in Step 3a carries no narrative section, by profile, so it keeps this rule rather than bending it)

## Step 2: Scan Connected Sources

Check each available connector. Skip gracefully if not connected — never error.

### Calendar (if connected)
- Pull today's events + tomorrow's first event (this is the **display** fetch for the "Today's calendar" section — narrow on purpose).
- **On the scheduled fire, today's events are ALREADY IN HAND (SPEC BRIEFMERGE §A).** The fire's prep leg made this exact pull before the digest started composing, so reuse its meetings and add only tomorrow's first event. Do not query the calendar twice in one fire — the leg IS the discovery pass, and a second one can disagree with the first about what today holds. On the on-demand path ("brief me") there is no leg, and this bullet's own fetch stands unchanged.
- **Do not reuse this narrow pull for the scheduling-verification gate.** Step 3c-bis needs a *much wider* window (~7 days back through ~30 days forward) to see that a "book/lock/propose time" item is already on the calendar days out. Reusing this today/tomorrow pull there starves the gate — a meeting four days from now looks unbooked and the brief tells the CEO to redo it (Bug #93). Step 3c-bis issues its own wide `list_events`.
- For each event: title, time, attendees, project association (match against tracker)
- Flag: meetings with no prep brief, back-to-back blocks, meetings with people who have overdue commitments. The "overdue commitments by attendee" check derives from `_hq/data/events.jsonl` via `load_open_commitments` filtered by `owner_id in <attendee_person_ids>` — **confirmed half only, `cru_match.split_pending_review(opens)[0]`** (per `references/SOURCE_OF_TRUTH.md` — never from PERSON.md commitment tables, which can lag). Flagging a meeting because someone "has overdue commitments" that are really unconfirmed extractions puts a guess between the CEO and the person he is about to sit down with.
- **On the scheduled fire the prep OUTCOMES come from the leg, and the leg's failures become lines (SPEC BRIEFMERGE §A/§B).** Render them with `prep_leg.meeting_lines(leg, workspace_root=...)`: a prepped meeting gets a workspace-relative link (or `syncing — open from your cloud drive` when the workspace's cloud platform — Google Drive, OneDrive, or SharePoint — has not landed the file on this machine yet; never a dead card); a meeting whose prep failed gets one line naming it and the phrase that regenerates it; a whole-leg failure gets ONE banner and the brief renders regardless. The helper refuses to run without the leg's result, which is what keeps prep strictly before render. A deliberate skip renders nothing, and there is no all-clear line — prep outcomes are LINES, never a section of their own, and never a pad. They render in exactly TWO places and nowhere else: the lines themselves inside Today's calendar, and the closing repeat below.
  - **The closing repeat — `Today's preps:` (SPEC WALKSMALL1 Part B).** The digest's LAST line repeats the same linked outcomes as compact chips, in the shape the Step 4 template shows (that template is the one home for the shape — do not restate it here). Link text is the meeting's time label (its title when the calendar gave no time), the target is the SAME workspace-relative pointer the calendar line already carried, and the separator is ` · `. This is a re-render of lines you already have — one `meeting_lines` result, rendered twice — not a second call and not a second derivation. Only LINKED outcomes become chips: a `syncing` line, a failed prep's regenerate line and a whole-leg banner stay where they are, in the calendar section, because a chip that opens nothing is the dead card this surface has always refused. The block is **absent entirely when no prep produced a link** — no header, no "no preps today", nothing. It lives inside the digest body, so the snapshot keeps workspace-relative pointers and the posted copy is converted by the ONE chokepoint (`chat_output_renderer.absolutize_doc_links`, BRIEFFIX1 Item A); it is NOT the chat `Links:` section, which is a post-time affordance and may name documents from earlier fires.
  - **A meeting already prepped renders the SAME link, under the honest word (SPEC BRIEFFIX1 Item B).** When a `prep_brief` receipt written today names THIS meeting instance (the calendar id and the instance's own start — a recurring meeting's id repeats, its start does not), the leg does not regenerate: the outcome is `reused`, the link line is identical to a freshly-generated one, and the row names the receipt it leaned on. Nothing about the digest changes — the CEO wanted the document, not a report on which fire built it — and nothing here should distinguish the two on screen. `ran` means THIS fire generated it and is never a synonym for "a prep exists".
  - **The links here are WORKSPACE-RELATIVE and stay that way through composition.** The saved snapshot keeps that form; the chat copy is converted once at post time by `chat_output_renderer.absolutize_doc_links` (SPEC BRIEFFIX1 Item A). Do not write an absolute path into the digest text — a relative href posted raw is a card that cannot open, and an absolute one saved to disk is a pointer the other machine cannot read.
- **The "no prep" flag reads receipts, ONLY receipts (v4.5.2 S1 — F-29):** for each of today's meetings, `from receipts import prep_exists_for_meeting; prep_exists_for_meeting(workspace_root, <calendar event id>)`. The `⚠️ no prep` flag may render ONLY when that returns False. NEVER answer "was this meeting prepped?" from folder globs, filename/slug guesses, or memory — that detector/writer mismatch is how the brief claimed "no prep brief" for a 9:15 call while the prep file AND its fire receipt were both on disk (reproduced 2-for-2 days in the v4.5.1 dogfood). Both prep paths now write a per-brief `prep_brief` receipt via `receipts.log_prep_receipt`; the receipt is the contract. A meeting the calendar gives no id for (rare) gets NO flag rather than a guessed one.
- **Build the `todays_meetings` input for Step 3d (v4.5.2 C1 — REQUIRED when the calendar is connected):** for each of today's events, resolve attendee emails to person_ids via `entities.json`/`aliases.json` and collect attendee display names PLUS their alias spellings from `aliases.json` — `{"meeting_id": <event id>, "title": <event title>, "attendee_person_ids": [...], "attendee_names": [...]}`. Step 3d passes this to `compute_and_log_brief_state`, which matches open commitments to today's meetings by counterparty OR name-mention in the item's own text (`commitment_state.match_commitments_to_meetings`). **A missing due date must not make a meeting-relevant item invisible** — the F-44 failure was sweep-recovered items about that morning's 9:15 appearing nowhere in the brief because every ranking bucket keyed on `due`.

### Email (if connected)
- Search for unread or important emails from the last 18 hours
- Filter by: people in PEOPLE.md, project-related subjects, flagged/starred
- Limit to 10 most relevant — summarize each in one line
- Flag: anything that looks like a reply to a "Waiting On" item in the tracker

**Self-reply filter (v3.11.1 — REQUIRED).** The default **in-inbox** query hides messages the user already sent in reply — meaning a thread where M responded an hour ago still shows up as "unread or important from last 18h" and surfaces under Needs Attention as if it's still waiting. For every candidate thread:

1. Fetch the thread's latest message via the resolved thread-fetch tool — `tool_discovery.discover_mail_thread_fetch_tool(tools, declared=connector_config.declared_backend("email"))`; the seam resolves the declared backend first and refuses to substitute another product's tool.
2. Compare the latest message's `From:` header to the primary user's email — resolve the user with `primary_user.resolve_primary_user("<workspace root>")` and read the email off THAT record, never by scanning `entities.json` for `is_primary_user: true` (SPEC USERKEY1: the canonical pointer is `workspace.user_id`, the flag is only the seam's flag fallback behind every pointer spelling it reads, and is unset on most real workspaces).
3. If the latest message in the thread is FROM the primary user, **drop the thread entirely** from Needs Attention and Overnight Inbox. M already handled it; surfacing it as outstanding is wrong.
4. The check must run on the thread's LATEST message, not the message that matched the original query (the mail search may have surfaced an earlier inbound message in a thread the user has since replied to).

If the connector supports it, broaden the initial query with the **inbox-or-sent, not-draft** intent — a DISJUNCTION, expressed as `{"any_of": [{"in_inbox": true}, {"in_sent": true}], "not_draft": true}` and compiled per provider by `connector_adapters/mail.py` `compile_search` (never a hardcoded operator string; on Gmail this reproduces the original parenthesized OR-group query byte-for-byte) — and then run step 1-3. This is cheaper than a per-candidate thread fetch but produces the same outcome — threads where M is the latest sender get dropped. Pick whichever path the connected mail tool supports; both are acceptable as long as the latest-sender check is applied.

### Chat (the declared chat backend, if connected)
- Check for unread DMs and mentions from the last 18 hours
- Check project-related channels for activity
- Limit to 5 most relevant — summarize each in one line
- Resolve the read tool with `tool_discovery.discover_chat_tool(tools, operation, declared=<the declared chat row>)` — the provider-agnostic resolver. **Never name a chat product in this skill's reasoning or output.** One vocabulary, one seam: every difference between backends lives in the capability manifest and `shared/scripts/connector_adapters/chat.py`, so "if it's [product] then…" is always the wrong shape here.

**Chat context + memory (SPEC CHATSCAN1 §C) — one line, never a row.** This is a leg INSIDE this same Step 2 sweep and reuses the fetch above; do not open a second pass over the same window.

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"chat_messages": [<the chat fetch Step 2 already made: at most 80 messages, the newest, each carrying ONLY ts, text and its pointer fields>], "chat_messages_total": <how many messages the fetch held before the 80 were chosen>, "workspace_root": "<WS>"}, "name": "morning_brief_helpers:chat_context"}'
```

**Never hand the tracked entities in (MIGRATE3-MB fix round 1).** They are the whole book, and the book never crosses the door as an argument: it grows with the customer, and the door's argument cap is headroom, not a carrier for it. The helper builds them itself, beside the data, from `entities.json` and `aliases.json` (`morning_brief_helpers.tracked_entities_for_chat`). The messages stop at `morning_brief_helpers.CHAT_CONTEXT_MAX_MESSAGES` (80, the newest), each with only `ts`, `text` and the pointer fields the leg reads (`provider`, `chat_or_channel_id` and the message's own id fields, or one `ref`): nothing else rides along. When the fetch held more, the answer carries `message_cap_line`, one pinned sentence naming how many were not read: print it verbatim, once, below the fold (MIGRATE3-MB fix round 2; a cap never truncates silently).

The helper resolves the provider (`chat_seam.resolve_chat_provider`), plans the
scan (`chat_seam.plan_scan(provider, date_filtered=True)`) and runs
`run_chat_context(workspace_root, chat_messages, tracked_entities,
provider=provider, scan_plan=plan, budget=ReadBudget())` beside the data. Its
answer is the leg's block — call it `ctx` — plus `pending_rows`: the bounded
entity touches the leg writes. The helper writes nothing, so **append them —
every element, in order, nothing dropped:**

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan append_jsonl --json '{"holder": "morning-brief", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the chat context answer above, in order>]}'
```

When `pending_rows` is empty there is nothing to append.

- `provider is None` → the leg returns a skipped block and there is nothing to render. Say nothing; the skip is receipted, not announced.
- `tracked_entities` is `[{"id","kind","names":[…]}]` resolved against `entities.json` / `aliases.json`. **Resolve them — never match a bare token you have not tied to a record.** A wrong entity touch lands in that person's permanent history, which is worse than a missed one.
- `ctx["context_line"]` is at most ONE sentence and goes inside an EXISTING section. **It is never a row.** The needs-attention lane still shows at most 5 rows via `commitment_state.cap_needs_attention` — this leg cannot add a sixth, and the brief's row count must be identical with the chat leg on and off.
- The leg also appends a bounded per-entity touchpoint to entity history (one roll-up per entity per run, capped, with the overflow counted in `ctx["n_touches_spilled"]`). That is how the workspace learns from chat without the book growing. It writes no message text — the pointer is the read-through.
- **The read budget is spent by the leg, not by you.** `ReadBudget` bounds how many CONVERSATIONS one fire will read (one unit per channel or chat), drained oldest-first; a conversation the budget cannot afford is not examined at all, so it cannot mint an entity touch. Pass the budget in and let `run_chat_context` spend it — do not pre-filter the fetch yourself, or the bound stops being measurable.
- `ctx["deferred_windows"]` / `ctx["n_conversations_deferred"]` non-empty means the budget was spent before the window was finished; the remainder is picked up OLDEST FIRST next run. It belongs on the receipt, not in the CEO's morning.
- `ctx["coverage_note"]` (set when the backend can only sweep chat partially) must be appended verbatim to any line that would otherwise read as full chat coverage.

## Step 3: Check Tracker for Urgency

Scan MASTER_TRACKER.md for:
- **Overdue commitments:** Any commitment past its due date → flag with days overdue
- **Stale waiting-on items:** Anything in "Waiting On" with no activity in 7+ days → flag for follow-up
- **Today's deadlines:** Any commitment due today → highlight prominently
- **Urgent flags:** Any project with "urgent" or "critical" in its next action or notes

If `_people/` exists (v3.11.5+ — REQUIRED canonical-source derivation per `references/SOURCE_OF_TRUTH.md`):

The team overdue / dormancy counts MUST derive from `_hq/data/events.jsonl` via `load_open_commitments`, NOT from each PERSON.md file's commitment table. The PERSON.md commitment table is a Tier 2 projection that lags — reading it directly was the v3.11.5 _people/ drift bug (see references/HISTORY.md). **Take the confirmed half before you count: `cru_match.split_pending_review(opens)[0]`** — an unconfirmed extraction is not a commitment a team member owes, and letting one through here is how a person gets flagged "3+ overdue" for work nobody agreed to.

Procedure:

1. Load the team roster from `_people/_team-config.md` → list of person ids (resolve names via `aliases.json` if the roster uses display names).
2. Call `load_open_commitments(events.jsonl)` once. Group results by `owner_id`.
3. **Overdue check** — for each team member, count commitments where `_commitment_field(ev, "due")` parses to a past date in workspace TZ (via `tz.py to_local(due, workspace_path=<WORKSPACE>)`). Flag anyone with 3+ overdue.
4. **Dormancy check** — for each team member, find max ts of any `interaction` / `meeting` / `commitment` event in events.jsonl where `event_references_person(ev, person_id)` is true (per `cru_match.event_references_person`, which handles all shape variants). If max ts is >14 days ago, note as dormant.

PERSON.md files are still fine to read for static profile context (role, working style, flags) — just not for the overdue / dormancy counts that drive the surfaced flag list.

### Step 3a: Layer the live substrate on top of the tracker — the per-project lines (v3.11.1 overlay; payload-fed since READER1 ADOPT4 — REQUIRED)

MASTER_TRACKER.md is a **periodic snapshot**, not a live view. It's regenerated when entities or events change, but a workspace that hasn't triggered a regen for 10 days will surface stale "Last touched" / "quiet since" values for projects that had activity today (the 2026-05-20 overlay incident — see references/HISTORY.md § Overlay bug class).

**Required overlay procedure — apply before rendering ANY per-project detail line (the `• [Thread] — Next: … | Last touched: …` lines under the org sections and Other relationships) and its "Last touched" / "Waiting On" / "Next Action" values:**

1. Read the tracker's stamp. MASTER_TRACKER.md is generated with `<!-- generated-at: YYYY-MM-DD HH:MM -->` near the top (per `references/VIEW_GENERATION.md`). Parse it. If both the comment-style stamp and a body line like `> Last updated: …` are present, the comment-style stamp wins. If it can't be parsed, treat the tracker as stale.
2. **Substance — the canonical thread payload, one call per rendered line.** For every thread that renders its own detail line, load `load_thread_knowledge(workspace_root, thread_id, "brief-line")` per the MANDATORY per-project context load below — never a by-hand pass over the substrate for the thread's state. The payload is the live truth the tracker line is a snapshot of, so it runs on every fire regardless of the stamp's age; the stamp decides only how much of the tracker's own copy survives (step 3). Threads collapsed into "+ N more", hidden personal threads, and threads the digest does not name get NO call.
3. Override from the payload:
   - **Next Action** → if the payload's `open_commitments` carries a confirmed row whose `ts` is newer than the tracker stamp, the newest such row's title is the Next. Otherwise keep the tracker's Next Action. (Within 24h of the stamp the tracker's copy is current enough to stand when the payload adds nothing newer.)
   - **Waiting On** → the payload's open rows owned by someone other than the user ARE the wait, named via `owner_name`. A tracker Waiting On item that no longer appears among the payload's open rows is closed — the projection is closure-folded at the source — so clear it. Never re-derive a closure from a by-hand read of the ledger.
   - **Decisions on the record** → only when the line's shape calls for one (a decision-shaped Next), from the payload's `decisions` section — supersession already folded, so a superseded call never renders as the standing one.
4. **Recency — the fire's own map, never a per-thread rescan.** **Last touched** and the `⚠️ quiet [X] days` marker come from the `thread_activity` map Step 3d already derives ONCE for every thread in the fire (`derive_from_events` with `activity_types=BOOKEND_ACTIVITY_TYPES, honor_reclassifications=True` — the canonical, reclassification-honoring fold), rendered with `to_local(ts, workspace_path=<WORKSPACE>)` per B1. A thread the map has no entry for keeps the tracker's value. One fold, one day-count per fire (F-54): the per-project line and the drop rules quote the same recency, and the payload carries no recency section on purpose.
5. The overlay is read-only. **Do not** regenerate MASTER_TRACKER.md from morning-brief — that's workspace-manager's job. Just render with the freshened values.

Better to over-overlay than to ship a digest that says "quiet since April 25" about work that happened today.

(The original acceptance criteria for this overlay are recorded in references/HISTORY.md § Overlay bug class.)

### MANDATORY context load — the per-project lines (READER1 ADOPT4)

The morning brief is a many-thread, SCHEDULED surface. Exactly ONE leg of it adopts the canonical reader: the per-project detail lines — where the digest names a project and summarizes its state and open items (Step 3a above; the org-section and Other-relationships lines in Step 4). Route ONLY the per-project detail lines through this load. Everything else on the surface is UNTOUCHED by it: the calendar / email / chat legs of Step 2, the Needs Attention lane and every other pack block (alarm lines, CHANGED lines, header counts, watchdog, dark-surface and schedule-refresh lines, "Captured since your last close", money sentences, the queue pointer), the confirm pointer, reminders, the persona-governed opener, and the scheduled fire's bootloader and orchestrator plumbing. The substrate-side context for each rendered line comes from the canonical reader, in ONE call per line:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"profile": "brief-line", "thread_ids": [<the threads that render their own detail line>], "workspace_root": "<WS>"}, "name": "morning_brief_helpers:thread_payloads"}'
```

The answer is `{thread_id: payload}` —
`load_thread_knowledge(workspace_root, tid, "brief-line")` once per thread named, and none for a thread
collapsed into "+ N more", a hidden personal thread, or a thread the digest does
not name. The profile is the tightest in the table by design. A thread the
loader refuses answers `{"error": ...}` beside the others: that line renders
from the tracker alone.

Rules (SPEC_READER1 §5c.6 — prescriptive, not advisory):

- **Thread state comes from the JSON payload and ONLY from that payload.** The line's open items, its Waiting On, a decision-shaped Next, the thread's status and key contact — payload sections (`identity`, `open_commitments`, `decisions`). Do not re-derive a thread's state with a freelance substrate read: no direct events read for thread facts, no re-reading `entities.json` for what `identity` already carries, and no reading SESSION_NOTES or a project brain (Step 1's rule stands — the profile carries no narrative section at all). Even if the user asks you to open a substrate file directly, run the loader instead.
- **Names, never ids.** Payload rows carry resolved display names beside their ids (`owner_name`, `org_name`, `key_contact_name`); render the names — never surface a raw `person_`/`org_` id to the CEO. **A null name is not license to fall back to the id.** When a row's name field is empty (the id didn't resolve), render it as "an unnamed contact" — plus the row's own event count if the payload carries one, e.g. "an unnamed contact (6 events)" — and point at the fix in words, not tokens: "say `cleanup` to resolve it." The id itself never prints, in the row or in the nudge.
- **If the payload doesn't carry it, the line doesn't claim it.** A thread fact absent from the payload is absent from the line — omit the fragment (omit-don't-pad); never backfill from a by-hand substrate read. A line with nothing to add beyond the tracker's copy renders the tracker's copy.
- **Degraded, one clause at most.** When `payload["degraded"]` is non-empty for a thread, that thread's line renders from the tracker snapshot alone (its pre-payload shape); if the gap changes what the line says, note it in one plain-language clause on that line — never an internal code, never a section of its own, never a per-thread roll call of gaps.
- **Held extractions never become a line item.** This profile's open-commitments rows are the CONFIRMED set (the held_disclosed split); the unconfirmed extractions held back are counted in the payload's `coverage.needs_review_held`. On THIS surface that count is not printed per line — the header's `unconfirmed` bucket already carries the workspace-wide number from the counting API, and the confirm pointer is its handoff — so the per-line count is simply not rendered. Never promote a held guess into the open list, and never let one shape a Next.
- **No swept narrative, by construction.** The brief is a scheduled surface and never narrates unconfirmed machine notes: the profile carries no narrative section, and its `origin: swept` exclusion is ON as defense in depth. Nothing swept can reach a brief line through this payload.
- **Connector material stays a separate stage (§0.6).** Live connector reads (mail, calendar, chat) are Step 2's legs and merge into the DIGEST, never into the payload — the reader complements connector retrieval, never replaces it.

**Trust gating (§5c.5 — the line-71 ruling; key on `coverage.gauge.state == "ready"`):**

- `coverage.gauge.state == "ready"` → the payload is trusted context. Its provenance and coverage notes may inform the digest's EXISTING disclosure lines. Surface behavior is otherwise unchanged.
- Any other state (`not_ready`, `unmeasured`) → render the line exactly as specified above, with NO coverage apparatus: no gauge language, no coverage line, no ask-first question, no mention of measurement state — on the line, anywhere in the digest, or in chat. Until R3's backfill lands, coverage disclosure activates only on gauge-READY threads.
- `coverage.empty_payload` true → the substrate holds nothing reachable for this thread. Do NOT fabricate thread context: the line renders the tracker snapshot's values alone, and where the tracker has none the thread renders as its name and status only. Never render thread context the payload did not supply.

### Step 3a-bis: Read what the reconcile-sent task closed — the brief is a READER, not the reconciler (v3.18.12 — Bug #98-v3)

**The brief no longer fetches sent mail or runs reconciliation.** A dedicated silent pass — the reconcile-sent job, FIRST in the `maintenance` task's 6:45 AM fire, before this brief (MAINT1) — does the actual sent-mail fetch, closes commitments the CEO completed by emailing someone directly, advances the cursor, and emits a `sent_reconcile` audit event. The brief just **reads** what that pass already wrote.

**Why it moved (Bug #98-v3):** co-locating an invisible substrate write with a visible deliverable loses every time — three in-brief attempts were all skipped in real use (full post-mortem in references/HISTORY.md § Bug #98). Reconciliation now lives in its own single-purpose task where it IS the job. The brief's role here is two things only:

1. **Surface what was already closed (read, don't do) — through the change feed (LB1).** The read-back now rides ONE narration slot, not two: `change_feed.changes_since(<last brief ts>)` (`shared/scripts/change_feed.py`; the t3 FB-9 driver pack delivers these as `changed.lines` — consume the pack, don't re-derive) aggregates the `sent_reconcile` closures alongside everything else the system did (sweep recoveries, resolved/expired proposals, undos), each line traceable to its audit event. Its closed-from-sent line carries the undo affordance verbatim (*"Closed N commitments matched to your sent mail — say `undo` to reopen any."*). These lines feed the CHANGED contract line in Step 4 — do not ALSO render a separate reconcile tail line (one slot, not two). Do NOT fetch sent mail or run the reconciliation matcher yourself — that's the reconcile-sent task's job; you are reporting its result, not producing it. Enforcement stays on the audit events (the feed is a READER — it can't fake "closed N" any more than this brief could).

2. **The deterministic soften floor (your job, and it's reliable because it's cheap + computed).** `compute_brief_state` (Step 3d) takes `sent_reconcile_cursor` and returns `reconcile_stale` (True when the cursor is absent or >1 day old) plus a per-item `reconcile_stale` flag. **When `reconcile_stale` is True, you MUST soften every you-owe / "ball is on you" item** — render it as *"you may have already handled this"* rather than "reply to / send / follow up with". In normal operation the 6:45 task advances the cursor before you run, so `reconcile_stale` is False and nothing softens. If that task didn't fire (cursor stale), the floor catches it and you still never send the CEO to redo done work. This is the protection that held across all three earlier failures.

   **The soften carries no reason (M's ruling R3, 2026-09-13 — SPEC SURFACEFIX1 5.1).** The clause this line used to carry — *"I haven't been able to check your sent mail since [cursor date]"* — is a connector-reachability sentence, the same class as the health lines M ruled off the brief on 2026-09-07, and it is now **off this surface entirely**. So is the separate staleness line that used to sit under the commitments list. The hedge is the whole of what the reader needs; **why** a leg was not read is a plumbing condition and its home is the health check (`system-health`) and the weekly maintenance report (`cleanup`), which read the same `connector_gaps` the brief's pack still carries for them. **Never render a sentence on the brief about a source being unreachable, behind, stale, or not checked** — not under the commitments, not in a footer, not as a parenthetical on a row. Never block or delay the brief on a gap, and never fetch sent mail yourself to "fix" it (that is the reconcile-sent task's job, per Step 3a-bis).

**Why this finally works.** Enforcement is on the EVENT, not a narration: the reconcile-sent task's success is a `sent_reconcile` audit event a validator reads back from `events.jsonl` — a cursor delta backed by a scan count can't be faked the way a sentence can (the gamed v3.18.9 receipt gate is in references/HISTORY.md § Bug #98). The brief can't fake "closed N" either — it reads the real `commitment_resolved` events or it has nothing to report.

**Substrate alarms — RETIRED FROM THIS SURFACE (HEALTH1, 2026-09-07 — M's ruling supersedes CUT-PLATE).** M, reading his own brief: "this should not be shown." The one-command pack still computes `alarm_lines` (the pack returns it from `substrate_health.substrate_alarm_lines(WORKSPACE_ROOT)` — a harmless, side-effect-free read other readers also use), but **you never place it.** `pack["health_lines"]` is always `[]` on this surface — not last, not softened, nowhere — for all four kinds M ruled on together: the substrate alarms (the log-clobber, unreadable-records, read-time-corruption and duplicate-entry lines FS-04/05/06/15 exist for), the watchdog line, the dark-surface line and the schedule-refresh line. None of it is silenced: the weekly `cleanup` maintenance run is now the one place all four are composed and reported, and the only surface where a cleanup pass can actually be offered and run. If you find yourself about to print anything from `pack["health_lines"]`, `pack["alarm_lines"]`, `pack["watchdog_line"]`, `pack["dark_surface_lines"]` or `pack["schedule_refresh_announce_lines"]` on this surface, stop — that content belongs in `skills/cleanup/SKILL.md`'s Monday note, not here. Fenced by `tests/run_health1_test.py`, which scans this rendered template the same way `tests/run_cutplate_test.py` scans it for the number-leads order.

### Step 3b: Aggregate commitments from events.jsonl (v2.7.15+, v3.4.5+ shape-aware)

Scan `_hq/data/events.jsonl` for `type: commitment` events that haven't been closed by a later `commitment_resolved` / `thread_resolved` event.

**Mark-done affordance (v3.18.3+ — Bug #85; receiving route registered P0.7 2026-07).** Every item surfaced under "Needs Attention" carries a one-tap **`mark done [n]`** action. The route: the fire records `data.needs_attention_ids` on its `pack_run` event — the commitment id (`data.id` verbatim) for each numbered Needs Attention item, in render order — and apply-choices Step 2's `morning-brief` source entry resolves `[n]` through `brief_receipt.resolve_mark_done` and closes through `commitment_state.close_commitment` (the canonical closure path — never a hand-built `commitment_resolved` append). This is the manual close path the 7-day stopgap below was waiting on — the CEO closes a stale "you owe" item in one tap instead of seeing it re-surface daily. Pair it with the auto-close from Step 3a-bis: the system closes what it can prove from Sent mail, and `mark done` covers the rest.

**The numbering is only as good as the receipt behind it (SPEC BRIEFFIX1 Item C).** Position 3 in today's brief and position 3 in yesterday's are different items, so the affordance is only safe while the newest recorded `needs_attention_ids` belongs to the newest brief. When a brief posts without its receipt, that stops being true and `resolve_mark_done` REFUSES in plain English rather than closing whatever is at that position. Which is why the fire writes its receipt BEFORE it posts: the ordering is what keeps the map and the screen in step.

**Prospect-conversion nudge (v3.18.7+ — Bug #92, detect-and-nudge — CONDITIONAL, cheap).** Call `shared/scripts/prospect_conversion_detector.py::detect_prospect_conversion_candidates(workspace_root)` (a fast substrate-only read — no connector fetch, unlike Step 3a-bis). This is the same detector the coach and weekly cleanup use; surfacing it in the daily brief is the highest-visibility nudge. **NEVER auto-flip `relationship_type`** — only surface the suggestion; the CEO runs the Bug #91 `[Name] is now a client` conversion. If the detector returns no candidates, emit nothing (this is a conditional line, NOT a mandatory one — contrast Step 3a-bis's required status line).

**Render EVERY candidate verbatim — do NOT second-guess the detector (v3.18.9+ — Bug #92b).** Each candidate carries a ready-to-render `render_line`. Add that line, exactly as returned, to Needs attention for **every** candidate the detector returns. You MUST NOT apply your own judgment about whether a prospect's project "looks paused", "isn't really active", or "isn't worth surfacing" — the detector already decided who qualifies (it owns the active/archived/paused call), and a surface that drops a candidate on its own discretion is the #92b regression (see references/HISTORY.md). If the detector returns 3 candidates, exactly 3 `🔄` lines appear. The detector is the source of truth for inclusion; your only job is to render its lines.

**Deal-rot line (SPEC PIPE1, D8 — CONDITIONAL, at most ONE line, same slot pattern as the prospect nudge).** A fast substrate-only read: `deal_state.list_open_deals` → `deal_health.compute_deal_health` (recency via `derive_thread_activity(ws, honor_reclassifications=True)` — RECL1, the SAME call shape pipeline-tracker uses so the two surfaces quote one day-count (F-54); next-step via the already-loaded open-commitment set — reuse this fire's, no second scan). Emit AT MOST ONE line, only when at least one open deal is `rotting` or `close_date_passed`: the single worst offender by `pipeline_math.rank_score`, in plain words with the teach-the-phrase close — *"💼 [Deal] ([Org], $40K) has been quiet 12 days in negotiating — say `show my pipeline` to act."* No stats block, no tile band, no second deal (the brief gets the top move; the pipeline report gets the dashboard). Zero flagged deals → emit nothing. Untracked deal threads never surface here.

**Objectives lines (SPEC OBJ1, DRAFT — CONDITIONAL, at most TWO lines, same slot pattern).** A fast substrate-only read: `objective_math.load_objective_inputs` → `compute_objective_health` → `brief_lines(health, max_lines=2, names_by_person_id=<people map>)` — the helper owns line selection and phrasing (line 1: the single worst drifting/at-risk objective WITH its suggested move and the `show my objectives` teach-phrase; line 2: the focus headline). Render its returned lines verbatim, exactly as the prospect nudge renders `render_line` (the helper decided; the surface renders). Zero objectives → the helper returns `[]` → emit nothing. READ-ONLY per FB-20 — these lines surface and suggest, they never ask for input, never render a widget; the weekly touch (Friday Wrap) is where objective asks live, and an objective whose graceful-death ask is pending emits no drift line here (the helper enforces the suppression via the death flag).

**Use the shared shape-aware reader (v3.4.5+ — MANDATORY).** Five distinct commitment-event shapes exist in production workspaces per `shared/COMMITMENT_SCHEMA.md`: canonical (`data.owner_id`), flat-new (top-level `owner_id`), legacy (`owner` no suffix), `owner_person_id`-variant (with `data.state` instead of `data.status`), and pending-review (filtered OUT of the morning count — those go to the needs-your-call queue; see the counting note below, which must agree with this line). Direct reads of `data.owner_id` only catch shape #1 — silently drops ~42% of commitments in M's workspace. Always invoke through the helper:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "morning_brief_helpers:open_commitments"}'
```

The answer is `{opens, fields, n_open}`, and it is what these lines used to compute in this shell — they now run beside the data:

- `from cru_match import _commitment_field, _commitment_confidence, load_open_commitments` — the shape-aware reader.
- `# load_open_commitments handles the filter logic` (status, closed-by-resolved, canonical/legacy shape across all 5 variants) in one call.
- `opens = load_open_commitments("<absolute path to _hq/data/events.jsonl>")` — RAW ON PURPOSE (INTAKE): the input to the counting API, pending rows included.

Deriving the unconfirmed pointer from the pending rows is
count_commitments' job. Filter this list and the queue pointer reads zero. Rows
you RENDER are routed per the enforcement note above, not read off this list.
`fields` is the per-event field read for each row, done by the shape-aware
reader — `_commitment_field(ev, "owner_id")`, `_commitment_field(ev, "due")`,
`_commitment_field(ev, "status")` and `_commitment_confidence(ev)` — so no
field is ever read off one of the five shapes by hand.

Counts come from `commitment_state.compute_brief_state(...).counts` — which is `commitment_state.count_commitments(...)` verbatim, the one counting API (skip pending-review shape — those go to the needs-your-call queue (`needs_review_queue` — the on-demand `needs your call` surface and the staff meeting's FROM YOUR MEETINGS fold; the retired Pulse chat's CRU-review section carried this until LIFECYCLE1), not the morning count):
- **You owe:** `owner_id == <user_id>`
- **They owe:** `owner_id` non-empty and `!= <user_id>`
- **Unassigned:** `owner_id` null/missing — an extraction gap, but still an open commitment
- **Overdue:** `due` parses to a past date (re-evaluated at read time). NOT "stuck" — stuck is the movement metric (headline["stuck"], v4.6.0 MC2), a different number.

**Canonical-total parity (v3.18.5+, Bug #85 A85-followup — MANDATORY).** The header total the brief reports MUST equal `counts.total` (= `you_owe + they_owe + unowned`) — the SAME number the coach reports. **INTAKE (2026-07-31): `counts.total` is NO LONGER `len(load_open_commitments(...))`, and that length must never be substituted for it.** The raw load is confirmed + unconfirmed; the partition this brief renders is the one `commitment_state.count_commitments` states, verbatim: *"Invariant (INTAKE): you_owe + owed_to_you + unowned == total, and `unconfirmed` sits OUTSIDE that partition."* So the three direction buckets and `total` are the CONFIRMED items (`cru_match.split_pending_review(opens)[0]`), and `unconfirmed` is its own labelled line — the needs-your-call queue pointer — never added into the total, never folded into a direction. Do NOT report `you_owe + they_owe` as the total: that silently drops ownerless commitments (the v3.18.4 16-vs-18 split — see references/HISTORY.md § Bug #85). Surface the unassigned items rather than hiding them — in customer copy the word is plain: "2 with no clear owner", never "2 unassigned" (e.g. "13 you owe · 3 they owe · 2 with no clear owner" → 18 total). The Bug #85 rule is unchanged — never `you_owe + they_owe`, never a confidence- or staleness-filtered subset; the pending exclusion is the one documented carve-out and it carries its own visible counter. Coach and brief agree because both read the counting API, not because either takes a `len()` of the raw load.

**Information count (CLUSTCOUNT1, 2026-08-26) — state it when it is there, say nothing when it is not.** `counts["headline"]` may carry `information_count` + `information_line` (e.g. `"9 items, 41 rows"`, where 41 IS `counts.total` restated as the row half of the sentence) — the same one-line-per-real-world-item read the queues already give (`needs your call`, commitment-triage), run here by `compute_brief_state` over the confirmed open set. Both keys are ADDITIVE and PRESENT ONLY when the workspace actually has something to fold; a workspace with nothing to cluster carries neither key. **When `information_line` is present, print it verbatim in place of the bare total** ("9 items, 41 rows" instead of just "41") — never recompute it, never round it, and never invent one when the key is absent (silence is the correct render — the bare total stands alone, not a guessed "N items, N rows" restating it twice).

**7-day activity stopgap (v3.11.1 — REQUIRED for Needs Attention overdue surfacing).** Commitments accumulate as "open" in events.jsonl because no `commitment_resolved` event fires when the work actually completes (scale of the problem in references/HISTORY.md § Bug #85). Until the full B4 fix lands (meeting-notes / follow-up-ritual emitting `commitment_resolved` + a documented manual close path), the morning-brief "Needs Attention" overdue list MUST filter out commitments whose linked thread has had activity in the last 7 days:

1. For each commitment that would otherwise be surfaced as Stuck/overdue under Needs Attention, look up the linked thread (`primary_thread_id`).
2. Find the max `ts` across all events in events.jsonl where `primary_thread_id == <thread>` (any type — `interaction`, `meeting`, `commitment`, `commitment_resolved`, `intel_logged`, etc.). 
3. If that max `ts` is within the last 7 days, **drop the commitment** from Needs Attention — the work is probably done, just not formally closed, and surfacing it as overdue is noise.
4. The header counts (you owe / they owe / unassigned / total) STILL count all open commitments — the canonical `counts.total` is unaffected by this filter; only the surfaced Needs Attention items are filtered. The header preserves the true workspace state (and equals the coach's count); the surfaced list is the actionable subset.

This stopgap is removed when meeting-notes and follow-up-ritual reliably emit `commitment_resolved` for fulfilled items (planned for the B4 full fix).

If the workspace has **zero commitment events** but ≥3 meeting events on file, surface a one-line nudge in the briefing tail: `"💡 I don't have any commitments tracked yet, even though you've had N meetings — say 'scan for commitments' and I'll pull them out of your past meetings."` This is the discoverability hook for `scan-for-commitments` — most users won't know it exists otherwise.

### Step 3c: Latest-sender re-verification on EVERY "ball is on you" item (v3.13.7+ — MUST-language enforcement gate)

> **For every item the digest would surface as "ball is on you" — fresh inbox scan items, carried-over items from earlier same-day briefs (morning → evening), commitments-derived items from Step 3b — you MUST fetch the linked thread via the seam-resolved thread-fetch tool, requesting FULL message content, and read the LATEST message's `From:` header. If the latest message in the thread is FROM the primary user (M), DROP THE ITEM. The ball is not on the user; they already replied.**

No exceptions for "we already checked this in Step 2" or "this is cached state from an earlier fire." Every "needs your reply" / "propose times" / "ball is on you" surface fires the latest-sender check at digest-build time. This is the structural defense Session-22 Bug #2 documented as missing — the morning→evening re-surface of an already-answered thread (see references/HISTORY.md § Session-22 Bug #2).

The fix is unconditional re-verification:

1. Build the candidate set for "ball is on you" (fresh inbox + carry-over from Step 3b commitments + going-quiet items where the user owes the reply).
2. For each candidate, fetch the thread via the seam-resolved thread-fetch tool with full message content. Read the latest message's `From:` field. **Use a real thread-id, not a message-id** — thread-fetch tools want the thread-level id (the field name is per-provider, via `connector_adapters.mail.threading_field(provider)`); passing a message-id errors. A search result row gives you both, so pass the thread's id, never the message's id.
3. If `From == primary_user.email`, drop the item. Surface nothing for that thread under "ball is on you."
4. **Fail CLOSED on any thread-fetch error — never infer the latest sender from a search snippet (Bug #93, sub-cause c).** If the thread fetch errors for a candidate (wrong id type, API failure, thread not found), you have NOT confirmed the ball is on the user. Do one retry with the corrected thread-level id; if it still fails, **drop the item** rather than surfacing it on a guess (the live failure is memorialized in references/HISTORY.md § Bug #93). A snippet is the message the search matched, not the thread's latest message; inferring latest-sender from it re-introduces exactly the bug Step 3c exists to kill. No confirmation → no surface.
5. The 7-day activity stopgap in Step 3b is COMPATIBLE with this gate (Step 3b can still pre-filter; Step 3c is the final say). When in doubt, Step 3c wins because it reads the actual latest message, not an inferred state.

**Full message content is required, not optional.** Lightweight metadata fetches (subject + date only) don't carry the `From:` field reliably across mail-connector adapter shims. The full-content fetch is the only path that guarantees the latest-sender field is populated. Worth the extra connector cost — this gate fires once per surfaced item, typically 5-15 items per brief.

If the connector supports a single batched thread fetch for multiple thread ids, prefer that. Otherwise per-candidate calls. Don't skip the check to save calls; the trust cost of one false "ball is on you" surface dwarfs the connector cost of 15 thread fetches.

### Step 3c-bis: Calendar-action re-verification on scheduling "ball is on you" items (v3.14.7+ — MUST-language enforcement gate)

> **The Step 3c latest-sender check only recognizes an EMAIL reply as "the user handled it." A scheduling thread almost always closes on the CALENDAR, not in the inbox — the user replies by creating an invite, so the thread's latest message is still the counter-party's and Step 3c keeps the item surfaced. For every candidate that would surface as "reply to X to lock/propose/confirm a time", "set up the call with X", or any scheduling-flavored "ball is on you" item, you MUST also check the calendar before surfacing. If a calendar event exists with that counter-party that the user organized OR the counter-party has accepted, AND it was created/updated at or after the counter-party's last inbound message, DROP THE ITEM — the loop is closed on the calendar.**

This is the surfacing-layer twin of the Path 5 fix (`shared/scripts/cru_match.py::match_calendar_to_commitments`, daily backstop in `orchestrator-commitments.md` Phase 2.7). Path 5 closes the substrate commitment on the daily Commitments fire; this gate stops the morning brief from surfacing a stale "reply to X" item in the window before that fire runs — and catches inbox-derived items that were never a tracked commitment at all (the 2026-05-29 live scheduling-close bug — see references/HISTORY.md § v3.14.7).

Procedure (runs only for scheduling-flavored candidates — detect via `cru_match.detect_scheduling_intent` on the item text, or the obvious surface phrasing "lock / propose times / set up the call / find time / confirm the time / put on the calendar"):

1. Resolve the counter-party on the thread/item to a `person_id` (+ their email via `entities.json` / `aliases.json`).
2. Query the calendar (the declared calendar backend, resolved via `discover_calendar_tool(tools, operation="find_events", declared=connector_config.declared_backend("calendar"))` — always pass `declared=`, same as the mail seams; native only, never Zapier, per CONTRACT Rule 8) for events involving that person from ~7 days ago through ~30 days ahead. **This is a DEDICATED wide fetch — do NOT reuse Step 2's today/tomorrow display pull (Bug #93, sub-cause b — see references/HISTORY.md).** The whole point of this gate is to catch a meeting booked days out; a narrow window makes the booked item look unbooked and it gets surfaced as "you still owe this". Issue the wide events query (window ≈ now−7d through now+30d; the neutral start/end are mapped to the provider's window fields by `connector_adapters/calendar.py`) before deciding any scheduling item surfaces. If you already pulled a wide window this fire, reuse that — but never the narrow display pull.
3. **Drop the item** if any matching event meets either bar:
   - the user is the organizer/creator and the event was created/updated at or after the counter-party's last inbound message on the thread, OR
   - the counter-party has accepted the invite (via `connector_adapters/calendar.py::is_accepted`, which reads the provider's RSVP-acceptance field rather than a hardcoded field name).
4. If no such event exists, keep the item — the ball really is on the user.

Step 3c (email latest-sender) and Step 3c-bis (calendar) are both final-say drops: an item surfaces only if it survives BOTH. When in doubt, drop — a missed "you already did this" is far cheaper to trust than a false "you still owe this." This also means a counter-party invite-acceptance (e.g. "Lyra accepted the call") is treated as a close signal here, not discarded as inbox calendar-noise.

### Step 3d: Compute the surfaced state deterministically (v3.14.8+ — MANDATORY)

Steps 3b/3c/3c-bis describe the rules; **`compute_brief_state` is the code that applies them.** Do not re-implement the open/overdue counting or the three drops by hand — gather the inputs and call the function. It is the same-inputs-same-output guarantee that stops this logic from drifting fire to fire.

**What crosses the door, and its ceiling (MIGRATE3-MB fix round 1).** The door refuses an argument that walks past its argument cap, so this form carries only what scales with the DAY, bounded: `thread_activity` is NOT handed in (it is the book's; the helper derives it beside the data with the pack's own canonical derivation, so the bullet below that builds it is for a seat whose files are local, never for the door line); `calendar_events` go through `morning_brief_helpers.trim_calendar_events` first, a pure plugin call that keeps at most `BRIEF_STATE_MAX_CALENDAR_EVENTS` (30), the soonest by start, each with only the five fields the drop reads (the helper applies the same cap again). When the trim left events out, the answer carries `calendar_cap_line`, one pinned sentence naming how many: print it verbatim, once, below the fold (MIGRATE3-MB fix round 2; a cap never truncates silently). **A scheduled fire skips this form and its append entirely:** Phase 3.9's pack writer computes and logs the brief state beside the data.

Gather (this is the connector work — only the FETCH is yours, not the decisions):
- `opens` = `load_open_commitments(events_jsonl_path)` (Step 3b) — the RAW list, pending rows included. `compute_brief_state` derives the `unconfirmed` pointer from them and keeps them out of every other tally itself; pre-filtering this input is how the queue pointer silently reads zero.
- `threads` = for each linked thread you expanded in Step 3c, `{thread_id: {"latest_sender_is_user": <bool from the get_thread latest-message From: check>}}`.
- `calendar_events` = the native-Calendar `list_events` results from Step 3c-bis, each resolved to `{attendee_person_ids, summary, created_ts, accepted_by, calendar_event_id}` (attendee emails → person_ids via `aliases.json`/`entities.json`).
- `thread_activity` = the CANONICAL derivation over the Step 3b events (C3 — do NOT inline your own max(ts) scan; this is the same helper every other recency surface reads): ONE command — `from thread_activity import BOOKEND_ACTIVITY_TYPES, derive_from_events` then `thread_activity = {tid: act.ts.isoformat() for tid, act in derive_from_events(events, activity_types=BOOKEND_ACTIVITY_TYPES, honor_reclassifications=True).items()}`. Every event type counts EXCEPT the commitment-lifecycle writers, reclassifications folded (RECL1), related threads credited, legacy ts/thread-id spellings parsed, and the standard 0.40 confidence floor applied — an unconfirmed low-confidence classification no longer silently mutes an overdue item. (C3 migration 2026-07-22, the RECL1 M3 spun-off follow-up; the pre-migration hand-rolled scan counted primary-thread ids only and ignored the floor — the canonical derivation is deliberately the fleet-consistent read.) **The type set is `BOOKEND_ACTIVITY_TYPES`, never `ALL_TYPES` (SPEC BOOKENDS1, 2026-08-23).** This step used to instruct `ALL_TYPES` — the filter OFF — so closing ONE item on a thread counted as that thread moving and hid every other open item on it for a week; measured on real substrate as 7 items on one day. The evening pack derives its `thread_activity` from the same constant, which is what lets the two bookends quote ONE day-count (F-54). `ALL_TYPES` stays correct for the renderers that mean literal "last touched" (the Master Tracker column, the list-active tree) — it is wrong here, where the answer decides whether to stop telling the CEO about something they still owe.
- `todays_meetings` = the list built in Step 2's calendar scan (meeting_id / title / attendee_person_ids / attendee_names incl. alias spellings). Omit only when the calendar is unavailable.

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"calendar_events": <list built above, at most 30, through morning_brief_helpers.trim_calendar_events>, "calendar_events_total": <how many events the window held before the trim>, "now_iso": "<current time ISO, workspace TZ-aware>", "plate_open": <the plate's open count when you hold the plate, else omit>, "sent_reconcile_cursor": "<workspace.sent_reconcile_cursor, or null>", "threads": <dict built above>, "todays_meetings": <list built in Step 2>, "workspace_root": "<WS>"}, "name": "morning_brief_helpers:brief_state"}'
```

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan append_jsonl --json '{"holder": "morning-briefing", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the brief state answer above, in order>]}'
```

The helper reads the RAW open set itself — RAW ON PURPOSE (INTAKE), the same reason as Step 3b: the counting API owns the pending partition, so it must receive the pending rows. These are the lines it runs beside the data:

- `from cru_match import load_open_commitments` — the Step 3d import for the same counting-API input.
- `opens = load_open_commitments("<absolute path to _hq/data/events.jsonl>")` — handed to `compute_brief_state` unfiltered.

It resolves the user with `resolve_primary_user` (deterministic, Bug #102 — never guess).
MUST use it — NOT a hand-rolled count (Bug #99). It runs `compute_brief_state`
and composes the `brief_state` audit row `compute_and_log_brief_state(workspace_root, open_commitments=opens, user_person_id=..., now_iso=...)` writes —
the CODE's real numbers, so a bypass is detectable (a brief with no brief_state
event hand-rolled). Its answer is `{state, pending_rows}`; `pending_rows` is
that audit row (empty when the same render already landed inside the refire
guard), and it is appended above, every element, in order. Hand-rolling the
counts/drops — even when they happen to match — is the #99 bug: the drop rules
(calendar / email-reply / recent-activity / reconcile_stale) are subtle and WILL
drift if re-derived in prose. Render ONLY from `state`:

```text
# Render from state — do NOT recompute:
#   state["counts"]["headline"] → the commitments line's numbers (you_owe /
#     owed_to_you / unowned / unconfirmed / overdue / stuck / blocked —
#     v4.5.2 R4 + v4.6.0 MC2, the ONE bucket export every surface renders;
#     F-47 P2b / F-56). Never fold unowned or unconfirmed into a direction.
#     headline["stuck"]/["blocked"] are the REAL movement metric (MC2 —
#     commitment_activity.py; compute_and_log_brief_state derives the
#     movement map automatically). The deprecated TOP-LEVEL counts["stuck"]
#     key is still overdue-by-due-date and still never renders (R1b).
#   state["needs_attention"] → the "ball is on you" items under Needs Attention
#     (SUB1: top-level items only — a sub-item never gets its own brief line;
#     when a row carries n_subitems_open/n_subitems_done, render the progress
#     inline — "2 of 3 sub-items done · next: [step]" — from those keys plus
#     next_subitem_due; a row carrying all_subitems_resolved renders "all
#     sub-items done — close it?" as a PROPOSE, never an auto-close)
#   state["meeting_linked"] → open items relevant to TODAY's meetings, matched
#     by counterparty or name-mention (v4.5.2 C1 / F-44). Render under the
#     matched calendar event (Step 4 "you two have open business" sub-lines).
#     These carry no due-date requirement and are NOT subject to the drops —
#     never suppress one because it is undated, task-kind, or on a recently
#     active thread. Items flagged pending_review render as needing a
#     confirm ("captured from a chat — confirm it's yours"), never as
#     settled fact and never with an auto-chase affordance. SUB1: these
#     rows DO include sub-items (F-44 — a step relevant to today's meeting
#     surfaces on the day of the meeting); a row carrying parent_id/
#     parent_title renders with "part of: [parent title]".
#   state["dropped"] → diagnostic only; never shown to the user (Rule 4/9)
#   state["reconcile_stale"] → Bug #98-v2 floor. If True (cursor absent or >1 day
#     old), reconciliation is behind: render EVERY needs_attention item softened
#     ("you may have already handled this"), NOT as "reply/send/follow up".
#     The hedge carries NO reason (R3, SPEC SURFACEFIX1 5.1): why a leg was not
#     read belongs to the health check and the maintenance report, never here.
#     Items also carry a per-item reconcile_stale flag. This is the
#     deterministic guarantee that a skipped reconcile (Step 3a-bis) can never
#     tell the CEO to redo done work.
```

**Bound the lane before you render it (CAPTUREFLOW §D, 2026-08-01 — MANDATORY).** `state["needs_attention"]` is the FULL list by design and it is not a render list: on a live workspace it was 72 rows, which is a backlog dump, not a brief. Pass it through the cap:

```python
from commitment_state import cap_needs_attention
lane = cap_needs_attention(state["needs_attention"], now_iso="<the fire's ISO now>")
# lane["shown"]      -> at most 5 rows, ranked due-then-age. Render THESE, in THIS order.
# lane["more_line"]  -> print verbatim as the section's last line when non-empty.
# lane["n_total"]    -> the honest full count if you need to say one.
```

Never re-rank what it hands back and never top the section up from your own Step-3 scan: a 14-day rotation inside the function pins any item that has sat below the fold too long into the visible set, so nothing is suppressed forever, and reordering breaks that. The cap is a RENDER bound and never a silence — `state["counts"]` stays unfiltered and the header numbers still count everything (the :299 doctrine). The pack carries the same lane pre-capped on `brief_state.needs_attention`; this is the same bound on the path you drive yourself, so the two fires agree.

`state["counts"]` is computed and logged, never rendered as a line (PLATE1 night 2 / NUMBERS1 R-1 — the brief's one number is the plate's, Step 3i). `lane["shown"]` is the GATED you-owe lane: it is what the drops and the fatigue rule were computed over, and on the driven path (the pack, Step 3h/3i) the driver folds its verdicts into the plate's cut — the rows that PRINT are `pack["plate"]["rows"]`. `state["dropped"]` is for diagnostics only — it explains why an item was suppressed (`calendar_action` / `email_reply` / `recent_activity`); never surface it in chat. If you can't fetch a given input (connector down), pass what you have — the function degrades gracefully (a missing `threads`/`calendar_events`/`thread_activity` just means that drop isn't applied; the item surfaces, which is the safe direction).

### Step 3i: The plate's brief cut — ONE number, the top rows, one pointer (SPEC PLATE1 night 2)

The commitments the brief shows are the PLATE's — the same model `what's on my plate` renders (`shared/scripts/plate_view.py`: action block → project → horizon), cut for this surface by the one renderer. The pack (`morning_brief_helpers:morning_pack`, Step 3h — run once, last) already built it as `pack["plate"]`:

- `line` — ONE number: "[N] on your plate today" — the PLATE'S OWN open count, the same integer the board's header states (NUMBER1 3.1, R-26, M 2026-09-17). It used to be the attention count (DO IT + CHASE), which read "53" over a board of 314 on 2026-09-16.
- `breakdown` — the line UNDER it, "[N] open · [M] want you today", composed by `plate_view.render_plate` and already second in `lead["lines"]`. It is a breakdown of the number above it, never a second answer to the same question, which is why `surface_drivers.assert_single_open_count` passes over it. Print it verbatim or not at all; never compose a count of your own here.
- `rows` — at most 5: the top DO IT rows then the top CHASE rows, each `{id, block, verb, line}`; `line` leads with `Do:` / `Chase:` (the block's verb — what the row wants). Rows the brief's own gates dropped this fire are already left out (`excluded_ids`). **No row carries a question** — the fatigue rule's fork is the Staff Meeting's now, and this pack is built with `ask=False` (REVIEW_NIGHT11C H-5, 2026-09-15).
- `pointer` — "…and N more — say `what's on my plate` for the rest." when the plate holds more than the cut; empty otherwise (render nothing).
- `refused` + `line` — on a workspace with no resolvable owner: the one plain sentence, no rows (D8).

**They render FIRST (CUT-PLATE, 2026-09-06 — M's rule: "we want to show less options to clients — they are overwhelmed").** The driver composes `pack["lead"]` — the number line, then the rows, then the one pointer — and Step 4 prints it verbatim as the FIRST content of the brief, directly under the header: no warning, no paragraph, no CHANGED line and no reminder above it. The counts that used to compete with the number sit below the fold (`pack["fold_lines"]`: the resting line and the queue pointer, plus Step 3g's confirm pointer) — and **`pack["health_lines"]` is always empty (HEALTH1, 2026-09-07): the substrate alarms, the duplicate-entry warning included, the watchdog line, the dark-surface line and the schedule-refresh line render on this surface NOWHERE, not even last.** M ruled the whole health block off the brief; the weekly `cleanup` maintenance run reports all four now. The driver's own fence (`surface_drivers.assert_number_leads`) refuses a pack whose composed order puts any count above the number; `tests/run_cutplate_test.py` runs the same scanner over this template, and `tests/run_health1_test.py` reds by name if any health line reappears here. Nothing here is re-derived by hand: no second load, no re-ranking, no hand-composed pointer. A turn with no pack in hand runs the same two functions beside the data, as one writer line in THE FORM (Step R) — building the plate mints its display numbers, so it is a writer:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_writer --json '{"args": {"exclude_ids": [<ids of state["dropped"]>], "now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:plate_lines"}'
```

— `plate_view.build_plate` then `plate_view.render_plate(view, "brief", False, exclude_ids=…)`, no `ask_lines`: the brief does not ask (REVIEW_NIGHT11C H-5) — and render the returned `text` verbatim.

### Step 3e: ONE gated source for EVERY "ball is on you" actionable — including Top 3 moves (v3.18.9+ — MUST-language enforcement gate, Bug #93)

> **Every actionable anywhere in the digest that tells the CEO they owe someone an action — "reply to X", "follow up with Y", "book / lock / propose a time with Z", "send the X to W", "get back to V" — MUST be drawn from the gated candidate set, NOT synthesized freehand from your raw inbox/calendar reads. There are two legitimate sources, and ONLY these two: (1) an item in `state["needs_attention"]` (already survived the 3c/3c-bis/7-day drops), or (2) an inbox-derived item that you have personally run through the Step 3c latest-sender check AND the Step 3c-bis calendar check this fire. If an actionable came from neither path, it does not appear — not in Needs attention, not in Top 3 moves, not in Suggested next steps.**

This closes the #93 trust-killer. The Top-3-moves and Suggested-next-steps sections are written in Step 4 as a *separate synthesis* over the morning's inbox/calendar scan — and that synthesis bypassed `compute_brief_state` entirely, so items the gates had already dropped (a meeting booked days out, a thread the CEO already replied to) reappeared at the head of the brief as "do this now" (live failures in references/HISTORY.md § Bug #93). Telling the CEO to redo finished work is the same trust-killer as the #85 class.

The rule, concretely:
1. **Tracked-commitment actionables** (you owe X per events.jsonl) come ONLY from `state["needs_attention"]`. If `compute_brief_state` dropped it (it's in `state["dropped"]`), it is handled — it may NOT be promoted into Top 3 moves on your own judgment that it "still feels open". The function already applied the calendar / latest-sender / recent-activity drops; second-guessing it is the bug.
2. **Inbox-derived actionables** (a "reply to X" that is NOT a tracked commitment — e.g. an overnight email that needs an answer) are legitimate to surface, but ONLY after you run the SAME two checks the gates apply: Step 3c (`get_thread` latest-sender, fail-closed on error) AND, if it's scheduling-flavored, Step 3c-bis (wide calendar fetch). An inbox-derived "reply to X" where X's thread shows the CEO as latest sender, or where a matching invite is already booked, is dropped exactly like a tracked one.
3. **Non-owing moves are unaffected.** "Read [doc] before the 2pm", "prep for the Acme call", "decide on the pricing" — moves that don't assert the CEO owes a *reply/booking/send* to a counter-party — are normal Top-3-moves and don't go through the drops. The gate governs *ball-is-on-you* actionables specifically.

When in doubt, drop: a missed "you already did this" costs nothing; a false "go redo this" costs trust. This gate is the single chokepoint — there is no second, ungated path to a "ball is on you" line.

### Step 3f: Load active reminders (v4.6.0 W4a — the user's own pins)

Reminders are the user's explicit "remind me about X on [day]" pins — their own event lane (`reminder` / `reminder_updated` / `reminder_cleared`), **never commitments**. They do NOT enter `compute_brief_state`, the commitment counts, Needs Attention, chase, or triage — do not fold them in, do not count them anywhere. Load them with the canonical pure reader — one verb, in THE FORM (Step R); never hand-scan the ledger for reminder types:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"surface": "m_facing", "today": "<today, workspace TZ date>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:reminders"}'
```

The answer is `{rows}` — `reminders.load_active_reminders(workspace_root, today, surface="m_facing")`, the brief being an owner-facing surface where personal reminders render. Each row:

```text
{id, summary, due, personal, ref, repeat, remind_from,
 status: pinned|upcoming|scheduled, days_pinned,
 escalation: none|bold|top, last_touch}
```

Render rules (M's four settled choices, 2026-07-08):

- **Pinned** (`status == "pinned"`): renders EVERY day until cleared or pushed — a pinned reminder never auto-fades. Each row carries the three affordances as plain chat phrases — done (*"done with the reminder"*), defer (*"defer it to [day]"* / *"push it to [day]"* — same move, and the taxonomy's widget label is **Later…**), keep — plus the daily ask ("Still want this pinned?" phrasing varies naturally). Rows with `escalation == "bold"` render bold (pinned + ignored 3 days); rows with `escalation == "top"` (7 days) LEAVE this section and render directly under the lead — the number first, the pin second (CUT-PLATE; see the template). A reminder with `ref` renders its context inline ("about: the Rio chase") but done/push/keep only ever write reminder events — never a commitment closure. If the user ALSO says the underlying item is done, that closes separately through the canonical closure path.
- **Upcoming reminders** (`status == "upcoming"`, within 3 days): lighter render — one line each, no ask, no affordance row.
- `status == "scheduled"` rows do NOT render in the brief (they show in `show my reminders`).
- Both sections render only when non-empty (never pad). Reminder rows never mention `personal`, event names, ids, or the word "reminder lane" — plain English only.
- These phrases route to the `show-my-reminders` skill's writer helpers (`shared/scripts/reminders.py`) — the brief itself stays read-only toward views and entities (its writes remain the pack_run receipt AND the Writer Contract's passive-capture `interaction`/`meeting` events, which are substrate capture, not brief mutation — BUG-8244 clarification: "read-only" was being read as cancelling capture, which starves cadence/dormancy/last-touch downstream).

### Step 3g: Confirm-section pointer count (v4.6.1 W4b — one number, read-only)

The Waiting On VIEW (CTS1's daily commitments surface; the scheduled chat is PAUSED and the view answers on demand — `show waiting`, SPEC SURFACEFIX1 5.5) opens with the "Needs a quick confirm" section (W4b; the selector covers every unadjudicated amber capture younger than the 7-day escalation pin). The brief carries ONE pointer line when that section will be non-empty — never the rows themselves (the triage point is the Staff Meeting; the brief just points, and it points with a PHRASE). Compute the count with the same selectors that chat uses — one verb, in THE FORM (Step R):

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:confirm_lines"}'
```

The answer is `{n_confirm, pointer}`: beside the data the helper takes the live dismissals (`mute_ledger.active_dismissal_target_ids`), the open person proposals with people already on file left out (FS-19), and adds `select_confirm_items` + `select_promotion_proposals` over the raw open set to `count_person_rows` — CLUSTERS, one person = one row, the same projection the queue renders (PID1), never raw proposal events.

`pointer` is the exact line the template renders — when it is None the line is OMITTED entirely (never pad, never render "0 items"). These items are ALREADY inside `headline["unconfirmed"]` (they count nowhere else); the pointer adds no numbers to the commitments line, and reminders (Step 3f) are a different lane entirely — never fold the two. **It renders BELOW THE FOLD (CUT-PLATE):** with the driver's `pack["fold_lines"]`, near the end of the digest, never above the plate's number.

### Step 3h: The money carve-out + the queue pointer (SPEC FB-20 — the brief is read-only)

**The brief does NOT adjudicate anything (FB-20 — M's ruling 2026-07-16: "the morning brief should just be a morning brief").** The LB1 "Needs your eyes" confirm card is **RETIRED from this surface**. No card, no rows, no buttons, no `show_widget` — a widget posted from a brief fire is a contract violation regardless of its contents. The proposal queue is adjudicated at the **staff meeting** (Mon/Wed/Fri by default, or any time the user says `staff meeting`), which is now the sole adjudication surface. This step is what the brief says about that queue instead.

**MANDATORY — DO NOT SKIP (FS-09). Time-of-day INVARIANT.** Both blocks below render whenever the driver's pack carries them, EVERY fire — a late-night "day-close" framing does NOT license dropping them. A brief that says "nothing open tonight" over a non-empty pack is the FS-09 failure. The editorial voice may shift with the hour; the mandated blocks do not.

**The pack already computed both blocks** (t3 FB-9 — the one-command pack, below). Render `money_lines` and `queue_pointer.line` from the pack VERBATIM. Do not re-derive either one. On a typed brief the pack is ONE writer line, run once, LAST in the gather, in THE FORM (Step R). **A scheduled fire skips this line and the two below it:** its orchestrator builds the pack itself, in scheduled mode, as its own last gathering step — one pack per fire, never two:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_writer --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:morning_pack"}'
```

Its `result` is `{pack, pending_rows, pack_file}`. `pack` is what every later step calls `pack`. The pack's rows land with ONE append — every element, in order, nothing dropped (Step 3d's `brief_state` row is this fire's one audit row, so the pack does not hand back a second):

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan append_jsonl --json '{"holder": "morning-brief", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the pack answer above, in order>]}'
```

and its audit copy with ONE write — the `rel` and the `data` exactly as `pack_file` carries them:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan write --json '{"data": "<pack_file.data from the pack answer above, verbatim>", "expected_mtime": null, "rel": "<pack_file.rel from the pack answer above>"}'
```

When `pending_rows` is empty there is nothing to append. The direct path, for a turn that has no pack in hand — the same projector, the same cap — is one verb:

```bash
cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"now_iso": "<now ISO>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:money_lines"}'
```

Its answer is `{money_lines, count}`: beside the data the helper runs `brain_proposals.load_open_proposals(workspace_root, "staff-meeting")` with auto-tier items out and `brain_proposals.money_prose_lines(queue, cap=3)` over it — the money sentences (`pack["money_lines"]`) and the queue's count (`pack["queue_pointer"]["count"]`).

- **`money_lines` — THE ONE EXCEPTION, narrowed by M ruling R-B (2026-09-06).** Money-class proposals are the single class the brief still names outright, as digest PROSE, one sentence each — and since R-B that means the ACCOUNT-VALUE ask only (`org_money`: *"Command Room spotted an account value for Northwind — say `staff meeting` to confirm."*). **The deal question is silent: "Command Room thinks [Org] is a live deal" no longer renders anywhere.** The deal-signals detector still records its `deal_creation` / `deal_update` proposals, but the projector withholds them from every named surface (`brain_proposals.WITHHELD_KINDS`), so they put no sentence here, no row on the Staff Meeting card and nothing in the pointer count; an org becomes a client only on a paid / signed / won fact, automatically, and the person hears about THAT as the CHANGED line with `undo` (DEALNAG1 / M ruling 4). Never re-derive a deal sentence from the detector, a proposal event or a meeting card — if the pack's `money_lines` is empty about deals, the brief is silent about deals. The sentences that remain are **propose-only and carry no verbs**: never attach buttons, never invent a "confirm?" affordance, never act on one from a brief turn. The confirm is a chat phrase at the staff meeting. Place them with NEEDS ATTENTION in the digest body. Empty → nothing renders; **never** pad an all-clear ("no new deals today" — never).
- **`queue_pointer.line` — the handoff.** ONE line, verbatim, BELOW THE FOLD — it is the last of `pack["fold_lines"]`, printed in the fold block near the end of the digest, never above the plate's number (CUT-PLATE): *"7 things need your eyes — say `staff meeting`."* That is the brief's entire adjudication affordance. The count comes from the same projector the staff meeting renders (same surface, same held/mute filters), so it can never over-promise — **never recount it, never adjust it, never round it, never soften it into "a few things."** Nothing queued → the driver returns an empty line and nothing renders (drop-empty; never "0 things need your eyes", never an all-clear pad).
- The Step 3g confirm-pointer stays as-is — it counts the Waiting On view's OWN confirm section; this is the cross-detector queue. An item can legitimately appear in both; that is not a bug.
- **First-run gate (FRP1) — RETIRED with the card.** `skill_config/system-health.json` key `daily_confirm_card` no longer gates this surface: there is no card to gate, and the two blocks above are substrate truth the brief always owes. The key is still read by the surfaces that do render the card. (An `"off"` value never suppressed the queue anyway — it reached the user through the Staff Meeting and `what's waiting on me`, which is now the only path by design.)

## Step 4: Build the Digest

Format the output as a structured, scannable digest. Skip any section that has nothing to report — never pad an empty section into existence. (The template below shows every POSSIBLE section; a typical day renders a handful.)

**THE NUMBER LEADS (CUT-PLATE, 2026-09-06 — read before the template).** The first content under the header is `pack["lead"]` verbatim: the plate's one number, the top rows, one pointer. Every other count in the brief either folds into that pointer or sits below the fold (`pack["fold_lines"]` + Step 3g's confirm pointer + the dated-personal echo, printed together near the end). **The health lines — the duplicate-entry warning, a stale-view alarm, the watchdog, the dark-surface and schedule-refresh lines — do not print anywhere on this template (HEALTH1, 2026-09-07: `pack["health_lines"]` is always `[]`).** CUT-PLATE used to print them LAST; M's ruling removes them from the brief in full, to the weekly `cleanup` Monday note instead. The v5.28.0 attended test saw the opposite of CUT-PLATE's order (a warning, a paragraph, CHANGED, then the number, with four counts competing around it); the driver refuses that order (`assert_number_leads`), and `tests/run_cutplate_test.py` / `tests/run_health1_test.py` scan this template for both the old competing-count order and any health line's return.

**Relationship-grouped thread layout (v2.2):** Active threads render in groups derived from the org tree, not a fixed home/side split. Authoritative rules — read these in order before rendering:

1. Every thread's `org_id` resolves to an `org` record in `entities.json` (`org_id` is the canonical thread→org field per ENTITY1; `affiliation_id` is a legacy alias still present on older records — read it as a fallback, never write it). Use the **most specific** level available — `org_acme_restaurant`, not the holding `org_acme_co` — so threads appear under the operating unit they belong to.
2. Groups in the briefing are defined by `org.is_primary_focus`:
   - **Primary focus orgs** render prominently and in full detail. There can be **more than one** (a portfolio / holding-co operator may have 2–4). Render them in the order of `last_interaction` (most recent first), with holding orgs rendering as a parent header with operating children nested beneath.
   - **Non-primary orgs** (`is_primary_focus: false`) roll up into a single OTHER ORGS section, grouped by `relationship_type` (board / advisory / investment / client / portfolio_company / beneficiary / partner / other) and collapsed by default.
   - Threads with `org_id: "personal"` (or the legacy `affiliation_id: "personal"`) are hidden unless the user explicitly asks for them.
3. Section headers use `canonical_name` directly — no hardcoded labels like "HOME ORG" or "SIDE". If the workspace has exactly one primary focus org, that org's name becomes the top section; if multiple, each renders as its own top section.
4. Nested rendering: when a primary focus org has children (scope=holding with operating children), render the holding as a section header and list each operating child as a subheader with its threads underneath. If a holding has ≥4 operating children, collapse the least-recently-active ones into "+ N more" with a show-all option.
5. `relationship_type` badges appear inline next to each thread's org label when non-obvious (e.g., `[board]`, `[advisory]`, `[investment]`). For threads where the CEO has `relationship_type: "operating"`, no badge is rendered — that's the default assumption for primary focus.
6. Briefing layout is derived at render time from what's present in `entities.json`. Do not hardcode a single-org or dual-org shape.

**v3.13.0+ — top-down layout with synthesis lead, top-3 moves up top, going-quiet promoted, and momentum delta.** Per M's 2026-05-20 feedback #30, the digest opens with the answer to "what should I do" before the lower-priority context — top-3 moves render right after the synthesis lead (design history in references/HISTORY.md § v3.13.0).

**Synthesis-lead test.** Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md`: the lead must name a dated moment AND say what CHANGED. Bare metric counts are not a lead.
```
GOOD: The May 14 CEO-group talk was the anchor — $45K early pipeline, two
      booked demos, and it opened the non-profit logistics wedge now running
      as a thread. Everything else this month supported it.
BAD:  This month had a lot of activity. Closed some deals, shipped features.
BAD:  May saw 6 commitments, 4 decisions, 15 emails.   (metrics, no meaning)
```
Test: does the lead name a dated moment AND say what CHANGED in the business? If the commitment counts alone tell the same story, the lead is redundant — rewrite for shape, not numbers.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "Your activity log has 3 incomplete entries — recovery pending in next update."
- GOOD: "A few entries in your activity log look incomplete — I'll tidy those up during this weekend's cleanup."

```
Morning briefing — [pack["date_header"] VERBATIM — "Monday, September 7, 2026"]

[THE LEAD — `pack["lead"]["lines"]` VERBATIM, in order, the FIRST content of the brief (CUT-PLATE 2026-09-06; SPEC PLATE1 night 2 — D7 `brief`; NUMBERS1 R-1, M 2026-08-22: "these numbers are so big"). Nothing but the header (and the persona-permitted intro line) sits above it: no warning, no paragraph, no CHANGED, no reminder. It is the plate's ONE number, then LINE TWO and the one coaching line when the pack carries them (BRIEF2, both described below and both already IN `lead["lines"]` — print the list, never rebuild it), then the top rows numbered from 1 (at most 5: the top DO IT rows then the top CHASE rows — `plate_view.render_plate(view, "brief")` owns the cut and every word, including the leading `Do:` / `Chase:` block verb, which is what the row wants, never a button: this surface is read-only prose, FB-20), then ONE pointer.]
[N] on your plate today
[N] open · [M] want you today
[LINE TWO — what today is about, `pack["day_intent"]["line"]`, already second in `lead["lines"]`. It is the reader's OWN sentence, said at last night's close ("tomorrow is about closing the Stone renewal") and read back this morning through `day_intent.load_day_intent` — STATED origins only, so a draft the product guessed at never renders as fact. Empty when nothing was stated, and then NOTHING prints: never "nothing on file", never a prompt to state one (this surface does not ask). Do not compose this sentence, do not re-read the store, do not reword the reader's items.]
[THE ONE COACHING LINE — `pack["coaching_line"]`, third when it is non-empty. A STATEMENT, never a question: *"If {behaviour} comes up today, you said you would do it rather than defer it."* It renders only for a seat that opened a coaching door AND left the render switch on AND has a behaviour on its coaching object; the driver gates all three and hands back "" otherwise. Print it verbatim or print nothing — never write a coaching sentence of your own on this surface, and never a second voice. On the fire that carries this line the driver DECLARES THAT ONE SENTENCE to the confidentiality scan — the sentence is taken out of the text and every other word the brief composed is still held to the ordinary rule, so a coaching note in any other slot is refused even on a coached morning; on every other fire the brief declares nothing and the same scan refuses any coaching-tier content that reaches it. That is in code (`surface_drivers.assert_no_coaching_leak`), not in your judgement.]
1. Do: [title] · [name] · due [date] · OVERDUE — [evidence chip]
2. Chase: [title] · [name] — quiet [N] days — [evidence chip]
…and [N] more — say `what's on my plate` for the rest.
[The number line is `pack["plate"]["line"]` VERBATIM — one number, the
plate's own. `N` = DO IT + CHASE on the plate (`plate_view.build_plate` block
totals) — the same number `what's on my plate` shows, by construction. Zero →
the line reads "Nothing is waiting on you today." (the renderer's own words;
never compose either). The six-number inventory line that used to sit here is
RETIRED from the rendered brief — un-rendered, not unbuilt:
`state["counts"]["headline"]` is still computed and still logged on the
`brief_state` event for trends, receipts and the book page. Never add a
second number beside the plate's: no "you owe / owed to you / no clear owner
/ overdue" inventory, no block totals (those live on `plate` and `board` only
— P4). The rows are `pack["plate"]["rows"][*].line` VERBATIM, in the order
given — **plain rows, no question on any of them** (the fatigue rule's
fork moved to the Staff Meeting; this pack is built with `ask=False` and no
row can carry an `ask_line` — REVIEW_NIGHT11C H-5, 2026-09-15); the pointer is `pack["plate"]["pointer"]` verbatim when non-empty
(never invent one, never round the count). Never re-rank, never top the
section up from your own scan, never add a row the pack did not hand you.
The driver already applied the brief's gates AT THE CUT: a plate row that
compute_brief_state dropped this fire (calendar action / email reply /
recent activity — `pack["plate"]["excluded_ids"]`) is not printed, while the
one number still counts it (it is the plate's number). `mark done [n]`
resolves `n` against `pack["plate"]["rows"][n-1].id` — that list, in that
order, is what the receipt records as `needs_attention_ids` (Step 3b /
BRIEFFIX1 Item C). `brief_state.needs_attention` stays on the pack as the
gated lane the drops and the ask were computed over; it is not a second row
list to render. On a workspace whose owner cannot be resolved
`pack["plate"]["refused"]` is true and `line` is the one plain refusal
sentence: print it here and render NO rows (D8 — no surface renders lanes
without a primary user). `brief_state.resting_line` does NOT print here — it
is a count, and it sits below the fold with the other counts.]

[Escalated reminders — ONLY rows with escalation == "top" (pinned + ignored 7 days). Renders directly under the lead, above the synthesis lead, per M's escalation choice (CUT-PLATE moved it under the number: the number is the first thing, the pin is the second). Skip entirely when none.]
📌 **You've been carrying this [N] days: [summary]** — done, defer it to a day, or say keep.

[Synthesis lead — one-line theme = the exec-header VERDICT (EXEC1 element 1).] Distill the day in a single sentence:
"Today is gated by the 4:45 negotiation with Acme; everything else is supporting cast."
"Heavy on Acme ops review; the plugin work is the asynchronous backbone."
Match Friday Wrap's lead-paragraph pattern — one anchor moment + theme. Skip
if nothing distinctive (then jump to commitments line).

[The 30-second contract — the three EXEC1 lines, rendered right after the synthesis lead (chat-surface form per shared/EXECUTIVE_OUTPUT_STANDARD.md element 1). This SUBSUMES the standalone "Momentum delta" line below — CHANGED absorbs it; do not render both.]
[QUIET1 D7 — the return summary. When the driver pack carries `changed.return_summary` (the person has not answered or opened a surface for two days or more), print its `header` VERBATIM as the line directly above CHANGED — *"While you were out (19 days): 14 closed on evidence, 9 parked, 3 need you."* — and let the CHANGED lines beneath it be the feed over that longer window (the pack already widened `changed.since_ts`). Never a row list, and never more than the pack's own CHANGED lines — which since BRIEF2 is every batch line plus three ordinary ones, not a flat three. When `return_summary` is null, print nothing here.]
[QUIET1 D3 — the step-down line. When the pack carries a non-empty `quiet_line`, print it VERBATIM once, directly after CHANGED: *"I've stopped asking — the next answer you give brings the questions back, or say `ask me more`."* The driver marks it narrated the moment it hands it out, so a re-run gets an empty `quiet_line` and prints nothing — never compose this sentence yourself.]
CHANGED   [what moved since yesterday's brief — named people/threads + numbers/dates, OR "Nothing material since [last brief]." LB1: this line now ALSO carries what the system did on its own — print `pack["changed"]["lines"]` VERBATIM, in order, all of them (Step 3a-bis). **BRIEF2 (2026-09-14): the cap is no longer "three lines".** The driver (`surface_drivers.brief_changed_lines`) prints EVERY line for a door the product walked through on the reader's own rows — a lapse batch, a rest batch, a silent close, each with its count, its door and its `undo` phrase — and caps only the ORDINARY lines at three. A batch line is the one thing on this strip the reader may need to reverse, and M's design rule of 2026-09-06 (item 4) says a cap never hides what matters; on 2026-09-13 the old flat cap of three hid a 48-row lapse and a 51-row rest batch behind three housekeeping lines. Never re-cut, re-rank or summarise the list — print it. The feed's closed-from-sent line keeps its `undo` affordance verbatim. One narration slot — never a separate reconcile tail line or a second "what I did" block. **MANDATORY (FS-09): when `changes_since` returns any lines, CHANGED MUST cite them — you may NOT write "Nothing material" over a non-empty feed. The feed lines are traceable to audit events; report them, don't editorialize them away.**]  (this is the former Momentum-delta line)
DECIDE    [Your one decision today: X — when a decision-shaped item exists (a decision_pending item on today's meeting threads, or a decide-shaped needs_attention item — both already in compute_brief_state, NO new fetch). Else: "Nothing — execution day."]
NEEDED    [the single most important reader-action today, OR "Nothing from you."]
[Concreteness floor: each line carries a named entity, number, or date, OR uses the explicit nothing-form. The generic-summary shapes ("key developments", "several updates", "busy week across", "lots of movement") are banned — run scan_for_generic_summary on these lines.]

[Pinned — Step 3f rows with status == "pinned" (minus the escalation-top rows already rendered up top). EVERY active pin renders EVERY day until cleared or pushed. escalation == "bold" rows render bold. Skip the section when empty.]
Pinned
📌 [summary] [— about: [ref context]] [· due [date]] — day [days_pinned+1]. Done, push, or keep?
📌 **[summary]** — [days_pinned] days now. Done, push it to a day, or keep?

[Upcoming reminders — Step 3f rows with status == "upcoming" (remind_from within 3 days). One light line each, NO ask, no affordances. Skip when empty.]
Upcoming reminders
· [summary] — from [Weekday]

[Reminders are the user's own pins — NOT commitments. They never appear in the lead, Needs Attention, or Top 3 moves, and nothing chases them.]

[No overdue count and no movement count render on this surface any more —
both are inventory (NUMBERS1 D1/D3). The plate's rows carry their own
OVERDUE badge and their own "quiet N days" reason, which is where a reader
meets those facts: on the item, not as a total.]

[Top 3 moves before noon — the answer to "what should I do." This is the most important section. Surface it right after commitments so it's seen in the first 15 seconds.]
Top 3 moves today
1. [action] — [why now / what unlocks]
2. [action] — [why now / what unlocks]
3. [action] — [why now / what unlocks]
[Each move is specific (not "review the Acme thing" — "read [person]'s [artifact] against the [doc]"). Rank by: (a) gates a meeting today, (b) deadline today/tomorrow, (c) highest-revenue dependency.]
[GATE (Bug #93): any move here that is a "ball is on you" actionable — reply / follow up / book / propose times / send / confirm with a counter-party — MUST come from the Step 3e gated set (state["needs_attention"], or an inbox item you ran through the 3c + 3c-bis checks this fire). NEVER promote an item compute_brief_state dropped, and never surface a reply/booking move you have not latest-sender + calendar verified. Non-owing moves (read / prep / decide) are exempt.]

[Momentum delta is now the CHANGED line of the 30-second contract above (EXEC1 subsumption) — not a separate block. If it's been ≥3 days since the last brief, CHANGED falls back to the honest steady-state form rather than a hard-to-summarize backfill.]

[Going quiet — promoted from the old "Other Orgs" buried footer.]
Going quiet — [N relationships]
⚠️ [Person/Org] — [N] days since last contact, usual cadence: [baseline], last topic: [topic]
[List the top 3-5 going-quiet relationships ranked by relationship value × deviation
from cadence baseline. Promoted to top-tier per M's feedback #30 (see
references/HISTORY.md § v3.13.0).]

Today's calendar ([X] events)
• [TIME] — [Event title] ([attendees]) [⚠️ no prep / 🔗 related to Project X / ✓ already wrapped]
    ↳ open with [name]: [commitment title] [— due [date] / no date set] [· needs a quick confirm]
[The ↳ sub-lines are state["meeting_linked"] rows rendered under their matched
event (v4.5.2 C1 / F-44) — every open item whose counterparty is in the room or
whose text names someone in the room, INCLUDING undated ones; a missing due
date must not hide an item on the day of the meeting. "no date set" renders
plainly, never as a blank. pending_review rows carry the "needs a quick
confirm" tag and are asks-to-confirm, not settled facts. Omit the sub-line
only when the row is already rendered verbatim in Needs Attention.]
[H2 link to the call-prep brief if one was generated, per CONTRACT Rule 3 — clickable, opens in side panel.]
[SPEC BRIEFMERGE: on the scheduled fire these are the prep leg's lines, rendered
verbatim from prep_leg.meeting_lines — a link per prepped meeting, one
regenerate line per failed one, one banner if the leg failed whole. Lines, not
a section; nothing at all for a skipped meeting; never an all-clear.]

[Week-ahead horizon.]
This week ahead: [Wed/Thu light · 3 demos Friday · Acme contract due Mon].
[One line. Keeps the user oriented past today without dragging the brief long.]

Needs attention
[THE ROWS ARE IN THE LEAD (CUT-PLATE). The plate's numbered rows — `pack["plate"]["rows"]`, `Do:` / `Chase:` — print at the top of the brief, once, and are never repeated here. This section carries only what is left: the money sentences and the promotion-detector lines below. Skip the heading when both are empty.]
🟡 [Item waiting on user sign-off / etc.] — [context]
🔄 [Prospect that looks converted] looks like a client now ([reason]) — say `[Name] is now a client`
[One 🔄 line per detector candidate, rendered verbatim from its `render_line` — render ALL of them, never a subset (Bug #92b). If the detector returns nothing, skip only the 🔄 lines — never an all-clear. DEALNAG1 + M's ruling 4 (2026-09-03): a prospect with a PAID OR SIGNED fact is not asked about at all — the promotion applies itself (`org_promotion`), and the person hears about it as ONE line in the CHANGED feed with the standing `undo`, never as a question. What is left here is the ambiguous lane only: signing LANGUAGE in recent activity (medium), and the one settled org whose promotion could not run because no primary-focus org is set. A sizing or engagement record alone (the `new prospect` command's own kind=client "Active sales conversation" edge, an active prospect thread) is NOT a client signal and produces nothing — no line, no proposal, silence. Do not re-derive a candidate from the entities file.]
[If nothing at all: skip this section — but a non-empty `pack["plate"]["rows"]` is never nothing.]

Overnight inbox ([X] worth your attention from [Y] total)
📧 [Sender]: [one-line summary] — [why ranked first: "first because it gates today's 4:45 call"]
📧 [Sender]: [one-line summary] — [why ranked: "decision needed before Wed deadline"]
[Top 5 max. Apply self-reply filter per v3.11.1 — drop threads where M is latest sender. Show sort reasoning inline so the order isn't a black box.]

[Primary focus org sections — one per is_primary_focus=true org.]
[Every thread line below — here, under nested holdings, and under Other
relationships — is a PER-PROJECT DETAIL LINE: its Next / open items / Waiting On
come from that thread's canonical payload (Step 3a + the MANDATORY per-project
context load — one `"brief-line"` call per line rendered, none for a thread
collapsed into "+ N more"); its Last touched / quiet-N come from the Step 3d
thread_activity map. Names, never ids. The payload never adds a line the layout
rules below would not render, and never grows the brief past its caps.]

Command Room
  External (business / GTM)
    • [Thread A] — Next: [action] | Last touched: [date]
    • [Thread B] — Next: [action] | Last touched: [date]
  Internal (plugin build)
    • Plugin ship — Next: [internal-only action]
    [Internal vs External subsections. Pre-v3.13.0 mixed M's CR plugin
    self-development with external client commitments in one list per #6a/#23a.
    Visually separate so client-facing work and internal build work don't blur.
    This split applies only when the user IS the builder of the Command Room
    plugin (M-specific case); regular users see only one section.]

[Acme Co]
  • [Thread C] — Next: [action] | Last touched: [date]
  • [Thread D] — Next: [action] | ⚠️ quiet [X] days

[For nested holdings — render holding as header, operating children indented:]
Summit Company [holding]
  └── Acme Restaurant
      • [Thread E] — Next: [action]
  └── Acme Bakery
      • [Thread F] — Next: [action] | ⚠️ quiet [X] days

Other relationships — [N threads across N orgs]
[Now shorter than pre-v3.13.0, because high-signal aging-out relationships were
promoted to Going quiet above. This section lists only orgs with active threads
that don't fit a primary-focus org. Collapse to top 4 by last_activity if >6;
append "+ N more".]
• [Thread G] ([org canonical_name] [advisory]) — Next: [action]

[Personal threads hidden unless the user explicitly asks for them.]

[Sources section — include ONLY if there's something to cite (Gmail
threads, Granola transcripts, Drive docs that informed the brief). If empty,
omit the "Sources:" header entirely.]
Sources: [optional — only if cited]
- [Title — date](url)

[BELOW THE FOLD (CUT-PLATE) — the counts that used to compete with the
number, together, one line each, verbatim: `pack["fold_lines"]` in the order
given (the resting line, then the queue pointer), then Step 3g's confirm
pointer, then the dated-personal echo. Each line renders only when non-empty;
none may ever move above the lead. The queue pointer's count is the driver's,
computed from the same projector the staff meeting renders — never recount
it, never round it, never soften it.]
[N] overdue items are resting until you answer them — they're on your plate, say `what's on my plate`.
[N] things need your eyes — say `staff meeting`.
[N] new items need a 10-second confirm — say `staff meeting`.
[N] personal item[s] due today — say `what's on my plate`.
[**EVERY POINTER NAMES A PHRASE, NEVER A CHAT (SPEC SURFACEFIX1 5.4 / amendment B-5, 2026-09-13).** Waiting On and My Plate are PAUSED — folded into this brief by FOLD1A — so "they're in your Waiting On chat" points at a chat the reader cannot open, which the v5.30.0 attended test read on two renders (B2.9). A pointer is only worth rendering if the phrase it names ANSWERS: `staff meeting`, `what's on my plate`, `show waiting` and `needs your call` all route standalone. If you are about to write "it's in your [X] chat", write the phrase instead; if there is no phrase, drop the line.]
[Dated-Personal echo (CTS1 §4.2, RULED 2026-07-16) — rendered ONLY when at
least one owner-me effective-kind-task item is DUE TODAY
(surface_split.partition_surfaces(opens, user_id)["personal"] filtered to
effective due == today). DATED items only — never echo the undated Personal
tail here (the 30+ day stale tail rides Friday triage's "still on your
plate?" sweep, and the full list is the My Plate chat's job). Zero
dated-today → omit the line entirely.]

Suggested next steps
[If the Top 3 moves section above captured the morning's shape, this section is optional or
collapsed. Otherwise: 3-5 more specific next-action items by project.]

[NO HEALTH LINES ON THIS SURFACE, EVER (HEALTH1, 2026-09-07 — M's ruling
supersedes CUT-PLATE, which used to place `pack["health_lines"]` here,
verbatim, LAST). `pack["health_lines"]` is always `[]`: the substrate alarms
(the duplicate-entry warning, a stale view, unreadable entries — FS-04/
05/06/15 + SYNC1), the watchdog line, the dark-surface lines and the
schedule-refresh lines print on this template NOWHERE — not first (where
CUT-PLATE found them, opening the v5.28.0 brief), not last (where CUT-PLATE
moved them), not softened, not re-narrated. They are not dropped: the weekly
`cleanup` maintenance run is the one place all four now render, and the only
surface where a cleanup pass can actually be offered and run. If a health
line ever appears anywhere in this template again, `tests/run_health1_test.py`
reds by name. The closing preps chip line (below) is the digest's last line
(WALKSMALL1), directly after Suggested next steps — nothing sits between them.]

Today's preps: [9:00](_hq/meetings/Call_Prep_[slug]_[date].docx) · [11:00](_hq/meetings/Call_Prep_[slug]_[date].docx)
[THE CLOSING REPEAT (SPEC WALKSMALL1 Part B). ONE line, last in the digest,
re-rendering the SAME linked outcomes the calendar section already carried —
prep links were reachable only by scrolling back into the calendar, which is
where they stopped being used. Chips only: time label as link text, the
workspace-relative pointer as the target, ` · ` between them. Linked outcomes
only — syncing lines, regenerate lines and the whole-leg banner stay in the
calendar section. No links → this line does not exist. Never a header with
nothing under it, never "no preps today". See Step 3's prep-outcomes bullet
for the full rule.]
```

**THE WHOLE TURN GOES THROUGH ONE DOOR (routed in from LEAK4, night 11d; R-25).** Every sentence this turn posts — the brief and anything after it — renders through `surface_composers.post`; nothing is composed after the composer runs; no file name, function, key or id is named in explanation. On 2026-09-15 the on-demand brief posted a clean gated answer and then a paragraph that named a field, five writer ids and a record field (leak instance 10): the composer had done its job and the turn kept talking past it. Render `post(...)`'s return AS THE ENTIRE REPLY, the way the weekly wrap already renders `quiet.wrap_post`'s. If you have something to say about how the brief was built, it does not go on this surface.

[L — VOCABULARY SCRUB FOR CLIENT PORTABILITY.] When morning-briefing ships for users other than M, scrub M-internal vocabulary on render:
- "EOS 2.0 wedge" → just "EOS" or the canonical phrase the user uses
- "v3 ship-flow shakedown" → drop the version reference, use plain English
- "IP-attorney check before any EOS pitch" → use the user's own framing if it exists in their session notes; otherwise keep generic
- M's specific project nicknames stay (those are personal language M wants); but Chalette-specific build terminology gets generic substitutes for non-M users.

This scrub runs at render time — read the user's session notes / project context for the language they actually use, and prefer that vocabulary over the abstract operator-class terminology.

## Step 5: Deliver the Digest

### Scheduled mode (running as a scheduled task):

The Morning Brief chat IS the surface. The `morning-brief` orchestrator (registered by enable-command-room-schedules, back-filled by command-room-update-bridge) composes the digest, saves the snapshot copy to `_hq/briefings/morning-[YYYY-MM-DD].md`, records the `pack_run` receipt (including `data.needs_attention_ids` so `mark done [n]` resolves — see Step 3b), and THEN posts it as a markdown chat post in that persistent scheduled chat. **That order is the contract, not an implementation detail (SPEC BRIEFFIX1 Item C):** receipt before post, so a fire that dies mid-way leaves a receipt with no post — a state this product already tolerates — instead of a posted brief the substrate has no record of, which reads as a fire that never happened and puts `mark done [n]` on the wrong numbering. The post is markdown end to end — **no widget on any fire (FB-20)**: Step 3h's money sentences and queue-pointer line are prose inside that same digest, and `show_widget` is never called from this surface. Do not send the digest anywhere else on a scheduled fire.

**Legacy delivery-channel fallback (explicit opt-in only):** if the user's `CLAUDE.md` / `_hq/BUSINESS_CONTEXT.md` carries an explicit "Briefing Delivery" preference naming Slack or email, honor it as an ADDITIONAL copy (Slack DM, or Gmail with subject `Morning briefing — [Day, Month DD]`). Never infer this from a connector merely being connected, and note that `mark done [n]` only works in the Morning Brief chat.

### Manual mode (user triggered in chat):
- Display the digest directly in chat
- No file save needed (the "what's going on" command handles full briefing saves)
- **Record the fire BEFORE you post it, whenever the digest carries a numbered Needs Attention section (SPEC BRIEFFIX1 Item C / F1 — REQUIRED).** A hand-run brief posts the same numbered items the scheduled one does, and `mark done [n]` resolves those numbers against a recorded list. Post without recording and the newest list on file belongs to a DIFFERENT brief — so the affordance either closes the wrong item or refuses. Neither is acceptable when the CEO is looking at a numbered list you just wrote. The incident that produced this rule was exactly this: a hand-run fire that posted numbered actions and recorded nothing.

  The receipt is composed where the data is, by its own writer with its append held, and landed with ONE append — two lines in THE FORM (Step R):

  ```bash
  cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"brief_status": "ran", "extra_data": {"needs_attention_ids": [<pack["plate"]["rows"][*].id, in render order>]}, "fired_via": "manual", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:plan_combined_receipt"}'
  ```

  ```bash
  cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan append_jsonl --json '{"holder": "morning-brief", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the receipt answer above, in order>]}'
  ```

  Beside the data the helper runs `prep_leg.log_combined_receipt` with its append held — the same writer the scheduled fire's receipt comes from. No `leg_result` is passed: on demand there IS no prep leg, and the helper states that honestly as `prep_leg.skipped_leg(SKIP_NO_LEG)`. `fired_via` is `manual` — a typed trigger is never scheduled.

  Same helper, same `pack_run` shape, same field the scheduled fire writes — never a hand-rolled receipt. `skipped_leg(SKIP_NO_LEG)` states the honest reason: this path never had a prep leg, which is different from a leg that failed and different again from a leg suppressed by lateness. If the section did not render, there is nothing to number and no receipt is owed.
- **Same link conversion (SPEC BRIEFFIX1 Item A).** A document link is a card that either opens or does not, and that does not depend on how the brief was triggered. Whatever is about to reach chat goes through `chat_output_renderer.absolutize_doc_links(text, <workspace root>)` first — beside the data, one verb in THE FORM (Step R), whose answer's `text` is what you post; the workspace-relative form is for what is written to disk, never for what is posted:

  ```bash
  cd "$PLUGIN_ROOT" && CR_DEVICE_WORKSPACE="<DEVICE>" CR_STAGED_ROOT="<RT>" CR_TRIGGERED_BY=morning-brief python3 shared/scripts/workspace_access.py plan run_helper --json '{"args": {"text": "<the composed digest, verbatim>", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:post_text"}'
  ```

### The narration scan — both modes (CUT-C item 8, MANDATORY)

The pack builder already scans every line it composes (the pack's builder runs `validate_chat_output` over the plate cut, the CHANGED / DECIDE / NEEDED lines, the money lines and every pointer). Whatever YOU compose on top of the pack — the opening paragraph, a per-project sentence, a Pinned note, the sign-off — is scanned the same way before it posts: run `validate_chat_output(<the composed digest text>)` from `chat_output_renderer.py`; it raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, a wire id), an event or field name, a path or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize`). NEVER catch the error and post anyway.

## Tone

Direct and specific, like a calm chief of staff. **Opening order (the one canonical answer):** (1) the personified intro line from the Personification section — `"Morning, {first_name} — {brain_name} here with today's read."` — renders first and is the ONLY greeting permitted, AND it renders only if the persona block permits: when the workspace CLAUDE.md persona block (`## How {brain_name} talks to …`) says skip pleasantries or its Never-line forbids greeting openers, omit the intro line and open directly with (2); (2) the `Morning briefing — [pack["date_header"]]` header — DATE1 (ATTENDED_TEST_v5.29.0 B2.1): print `pack["date_header"]` VERBATIM ("Monday, September 7, 2026"), never compute the weekday yourself — the composer (`due_reanchor.render_today_header`) is workspace-timezone-anchored, and a free-text weekday is the exact regression the v5.29.0 attended test caught (one render said "Sunday, September 7" on a Monday while the same workspace's other renders that day said "Monday"); (3) THE LEAD — `pack["lead"]` verbatim: the plate's one number, the top rows, one pointer (CUT-PLATE — the number is the first thing the reader meets); (4) the synthesis lead. No other greeting anywhere ("Good morning!" / "Here's what's happening!" — never). The content itself reads as friendly plain English, not engineer status-board ("3 commitments aging past 14 days" is fine; "DRIFT: 3 commitments aged past threshold" is not). Per CONTRACT Rule 4 — no all-caps section headers, no scores, no internal mechanism names.

**Closing line — the day-one explain-once line (ONBOARD2, SPEC_SURFACES2 §9; rewired by BRIEF2 2026-09-14).** **The pack has already consumed it. Do NOT call `explain_once.consume` from this skill.** Print `pack["explain_once_line"]` verbatim, alone, as the very last thing rendered — never above THE LEAD, never touching the Opening order above — and print nothing when it is empty. Onboarding arms the line once per workspace (Phase 6a2); the first brief to actually fire after that consumes it INSIDE the pack, which is what puts the one sentence the product says about itself above the pack's three fences instead of below them (the prose used to call `consume` after the pack, so a question planted in `EXPLAIN_ONCE_LINES` would have reached the reader untouched by `assert_brief_never_asks`). Every fire after the first gets "" back — `explain_once.consume` is what makes "once" true, not this prose, so don't gate it on "is this the first brief I've ever run" from memory, and don't call it a second time to check.

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Gotchas

- **Scheduling threads close on the calendar, not the inbox.** The latest-sender check (Step 3c) only sees email replies. When the user answers "can we set a time?" by creating a calendar invite, the thread's newest *message* is still the counter-party's, so the email-only check keeps surfacing "reply to X to lock the time" for days (the v3.14.7 live bug). Step 3c-bis is the fix — for any scheduling-flavored "ball is on you" item you MUST also check the calendar and drop it if the user organized / the counter-party accepted a matching event. A counter-party invite-acceptance is a close signal, not inbox noise.
- **Don't duplicate "what's going on."** This briefing is shorter and proactive — it fires before the user asks. "What's going on" is the comprehensive interactive version. They complement each other.
- **Don't update the tracker.** This is read-only toward the tracker, entities, and views — surface what you find; don't change them. The user decides what to act on during their actual work session. **Read-only does NOT cover passive capture** (BUG-8244): the Writer Contract's `interaction`/`meeting` event emissions from connector reads are MANDATORY on every fire — skipping them because "the brief is read-only" starves relationship cadence and every last-touch computation.
- **Don't read session notes.** The tracker has enough for a morning scan. Per-project deep dives happen on "go [project]." Keep this fast. The per-project context load (Step 3a) keeps this true by profile — it carries no narrative section, so it never opens a session-notes file either.
- **Respect quiet periods.** If the tracker shows no active projects (all Steady State or Archived), output a minimal briefing: "Quiet day. Calendar: [events]. Inbox: [count] new." Don't pad.
- **Weekend handling.** If configured as a weekday-only scheduled task, this won't fire on weekends. If the user manually says "morning briefing" on a weekend, run it normally — they're choosing to check in.
- **First-time setup.** If `_hq/MASTER_TRACKER.md` doesn't exist, this workspace hasn't been set up. Output: "Looks like your Command Room isn't set up yet. Say 'set up my command room' and I'll walk you through it." Don't attempt to scan.
- **Connector failures.** If a connector times out or errors, skip it — and say nothing about it on the brief (R3, SPEC SURFACEFIX1 5.1). Leave out the section that leg would have filled rather than rendering an empty or hedged one, put the gap in the pack's `connector_gaps` where the health check and the maintenance report read it, and don't let one failure block the whole briefing.
- **Morning briefing files are ephemeral.** Files saved to `_hq/briefings/morning-*.md` follow the same 30-day pruning as regular briefings (Rule 4). They're snapshots, not permanent records.

## Reliability

This skill runs as a scheduled task (weekdays 7:30am) and must implement `shared/RELIABILITY.md`. Key rules: skip-not-fail when workspace isn't ready (log to `_hq/logs/scheduled-task-skips.log`, exit clean, never produce empty briefings), OOO detection via `_hq/BUSINESS_CONTEXT.md` (render an OOO-mode briefing with only urgent items), missed-fire recovery (produce one catch-up covering the gap window, max 3 days), 15s per-connector / 60s aggregate timeout budget with graceful degradation, and last-known-good cache at `_hq/caches/[connector]-last-good.json` when a connector fails. Never fabricate data when a connector is unavailable — leave the section out, carry the gap into the pack's `connector_gaps` for the health check and the maintenance report, and continue. **Never say it on the brief** (M's ruling R3, 2026-09-13 — SPEC SURFACEFIX1 5.1): a reachability sentence is a plumbing condition and the brief is not its home. `run_health1_test.py` reds on any such sentence in this file.

## What It Doesn't Do

- Does not triage individual emails or draft replies — that's `inbox-triage`.
- Does not produce deep per-meeting prep itself — that's `call-prep`. On the SCHEDULED fire the orchestrator runs `call-prep` as the fire's first leg and this digest reads its outcomes (SPEC BRIEFMERGE); on the on-demand path nothing is generated and "prep me for my 2pm" is still the way to get one.
- Does not update MASTER_TRACKER, entities.json, or any other workspace state — this skill is read-only, and since FB-20 it is read-only in the stronger sense too: it renders no card, so nothing can be adjudicated, confirmed, or applied from a brief. (It no longer writes the LB1 card's shown-markers either — with no card rendered there is nothing to mark shown, and the staff meeting sees the full queue.)
- Does not generate insights or pattern analysis — that's `insight-generator`.
- Does not deliver on weekends by default — manual trigger only on weekends.

## Routing (full trigger corpus)

The settings-trigger family for this skill, relocated verbatim from the pre-G11-diet description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> The brief's shape, in one sentence — every one of the ten axes is reachable on its own, and these are the sentences `brief_settings.AXES` advertises: 'group my brief by workstream', 'lead with my calendar', 'one line per section', 'call them promises instead of commitments', 'keep it short enough for my phone', 'do not flag anything until it is 3 days overdue', 'leave [a workstream] out of my brief', 'cover the last 3 days', 'use my usual voice', 'add the coaching line to my brief'. The description registers the class; this is the enumeration.

> Also handles first-run personalization settings — use when the user says 'tune morning-briefing', 'show morning-briefing settings', 'reset morning-briefing to defaults'. Also takes standing customization preferences — use when the user says 'customize morning-briefing', 'show morning-briefing customizations', 'reset morning-briefing customizations'.

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
