---
name: meeting-notes
description: "Process meeting notes from Granola or pasted text into structured artifacts — decisions, action items, SESSION_NOTES + Master Tracker updates. Triggers: 'process meeting', 'process the last call', 'process the call', 'meeting notes', 'meeting notes from', 'analyze this call', 'debrief from', 'log the meeting', 'summarize the call', 'summarize the meeting', 'action items from the meeting', 'action items from the call'. Also handles first-run personalization settings — use when the user says 'tune meeting notes', 'tune meeting-notes', 'show meeting notes settings', 'show meeting-notes settings', 'reset meeting notes to defaults', 'reset meeting-notes to defaults'. DOES NOT fire on 'follow up', 'draft follow-ups', 'close the loop' — those go to follow-up-ritual. DOES NOT fire on 'prep me for' — that goes to call-prep."
---

## Skill Boundary (v2.1)

- **Use meeting-notes for:** structured logging after a meeting — extract, route to SESSION_NOTES, append to events.jsonl, update MASTER_TRACKER / DECISION_LOG / PEOPLE.
- **Use `follow-up-ritual` for:** the same meeting + drafted per-attendee follow-up emails ready to send. If user says "follow up on that call" / "close the loop" / "draft follow-ups", defer to follow-up-ritual — it invokes this skill internally for the logging step.
- **Use `call-prep` for:** before a meeting, not after. "Prep me for…" phrases never fire this skill.

If the user's phrasing is ambiguous ("process the call and send follow-ups"), call follow-up-ritual — it covers both.

---

# Meeting Notes Skill (v2.0)

## Personification Contract (v3.13.8.4+)

Before surfacing the post-processing acknowledgment, read `shared/PERSONIFICATION.md`. The chat acknowledgment after processing is FIRST PERSON, plain text (no backticks around the meeting name): "Got it, {first_name} — I've processed [Meeting Name]. {N} commitments captured, {M} decisions logged. Anything to add before I file it?" Never the third-person "{brain_name} processed…" shape — the AI speaks as itself.

**{N} and {M} come from the claim audit (Step 9 pre-render, v4.5.2), never from extraction intent.** The 2026-07 dogfood caught this exact ack claiming "3 decisions logged" with zero decision events on disk (F-46). Count the events after appending, then speak.

## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes must follow the File Ownership Map, Write Protocol, and Append Format defined there. JSON sources live in `_hq/data/`; markdown views in `_hq/views/` are regenerated and must not be written directly. Violations go to `_hq/CONFLICTS.md`.

You are a **primary appender** for `_hq/data/events.jsonl` — every meeting you process becomes at least one event:

- One `meeting` event with attendees, summary, transcript reference.
- One `decision` event per captured decision (Step 5b — **MANDATORY in both modes**, v4.5.2; same contract the scheduled past-meetings writer uses).
- One `commitment` event per captured action item. **Schema is non-negotiable — see `shared/COMMITMENT_SCHEMA.md` and Step 5e below for the exact shape.** v2.7.15+ uses the canonical `data` envelope; legacy flat shape is read-only.
- One `person_proposal` (or `person_update_proposal`) event per unknown name meaningfully involved (Step 5f, v4.5.2 — pending-review, never chat-only).
- One `meeting_processed` receipt per processing run (Step 9a, v4.5.2 — the canonical already-processed marker the detectors read).
- One `interaction` event per person (`channel: "meeting"`) if not already implicit.
- Optional `status_change` or `scope_change` events when the meeting shifts project state.

**Every event you emit carries** `primary_thread_id`, `related_thread_ids[]`, `cross_ref_reason`, and `classification_confidence` per `references/ORG_AND_THREAD_MODEL.md` (schema field names unchanged for stability; the concept is "project"). Meetings that touch multiple projects (a 1:1 that covers a deal AND a vendor issue) get one primary + N related — never forced into a single project.

You also append to `_hq/data/aliases.json` when you discover new raw-to-canonical mappings (new nicknames, new email forms). After your appends, the writer helper regenerates affected views (`DECISION_LOG.md`, `PEOPLE.md`, `MASTER_TRACKER.md`).

You **append** to `[Project]/SESSION_NOTES_[NAME].md` as a human-readable narrative duplicate of the events you just persisted. Both must succeed: if the events.jsonl append fails, skip the markdown append and log a conflict.

You **do not write** to `entities.json` projects (that's workspace-manager) or people (that's people-crm). For a **new person**, the durable record is the `person_proposal` event (Step 5f) — a chat suggestion ("say add [name]") on its own is NOT capture; dismiss that chat and the proposal is stranded forever (F-46 P2b). For a **project state change**, surface a suggestion: "Sounds like Project Y is now blocked — want me to mark it that way?" Owner skills execute on the next turn.

**Canonicalize every person and project reference via `aliases.json` before persisting any event.** No raw Gmail names or Slack handles in events. **And render from the canonical record too (v4.6.1 S3 / F-50 P2b):** every name the user SEES — the chat card header, SESSION_NOTES Attendees line, the meeting event's title, the brief docx — uses the resolved record's `canonical_name`, never the transcript's ASR spelling ("Myra Samples" rendered for a correctly-resolved Mira Sample). Transcript spellings survive only in verbatim evidence quotes and in an open `person_proposal`'s as-heard `name`. Full rule: `shared/ENTITY_RESOLVE_PROTOCOL.md` § Display names.

Additionally, this skill implements `shared/PASSIVE_CAPTURE.md`. The Granola connector read when processing a transcript emits corresponding events per that contract's rules (meeting event + per-decision events + per-commitment events, all dedup'd via source_ref hash).

---

## Overview

This skill transforms meeting recordings, transcripts, or summaries into actionable intelligence. It classifies every call into one primary project + any related projects, routes notes accordingly, updates the Master Tracker with commitments and next actions, and surfaces business context — scope changes, timeline pressure, relationship shifts, risks, and opportunities.

Works for **any business owner** — scaling fast, running operations, managing teams, navigating client relationships, or juggling multiple projects (deals, advisory boards, operating companies, vendors).

### What It Does

1. **Pulls the meeting source** — transcript from whichever transcript connector is wired (via `discover_transcript_tool()`), or process pasted text
2. **Extracts structured data** — decisions, action items, attendees, financial info, scope changes
3. **Routes to primary project** — saves SESSION_NOTES to `[WORKSPACE_ROOT]/[Project Folder Name]/SESSION_NOTES_[NAME].md` (where `[NAME]` = the user's first name, set during onboarding — e.g., `SESSION_NOTES_Pat.md`). To find the correct [NAME], look for the existing SESSION_NOTES file in the project folder (there should be exactly one file matching `SESSION_NOTES_*.md`). If no SESSION_NOTES file exists yet, check other project folders for the pattern, or check `_hq/BUSINESS_CONTEXT.md` for the user's name. If still unknown, ask: "What's your first name? I need it for your session notes files."
4. **Updates Master Tracker** — records commitments, last touched, next action, deadline pressure
5. **Updates ref files** — new contacts → contacts.md, scope/budget changes → scope.md, etc.
6. **Applies business lens** — flags what matters for strategy/execution (risks, opportunities, timeline pressure, relationship dynamics)
7. **Asks follow-up questions** — pulls context to understand implications, not just filing notes

---

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). All
three decisions are **show-then-tune (STT)** — the meeting is processed first, then one-tap
changes are offered. Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "commitment_capture": "silent",    # silent (auto-capture) | confirm_first
    "verbosity": "standard",           # standard | terse
    "new_person_handling": "surface",  # surface (suggest now) | batch_to_pulse
}
cfg = get_config(workspace_root, "meeting-notes", DEFAULTS)
```

`commitment_capture=silent` (default) auto-emits commitment events per Step 5e; `confirm_first`
surfaces them for a one-tap confirm before writing. `verbosity` sets SESSION_NOTES narrative depth.
`new_person_handling` controls the "I see a new person X — add to team?" suggestion: `surface` now
(default) vs `batch_to_pulse` (collect into the next Pulse instead of interrupting).

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "process the call", "meeting notes" | process with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "meeting-notes", DEFAULTS)` BEFORE rendering, then append the first-run block. |
| **Show settings** | "show meeting-notes settings" | render current config in plain English; no processing. |
| **Tune** | "tune meeting-notes" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → confirm. |
| **Reset** | "reset meeting-notes to defaults" | `wipe_skill_config(workspace_root, "meeting-notes")` → next fire is a first-fire again. |

**The first-run block (transport):** the three decisions ride as `fr1`/`fr2`/`fr3` items in a
"Make this yours" section at the BOTTOM of the Step 9 OPEN ITEMS widget (the documented fr-item
preselect exception — see `shared/CHAT_ACTION_WIDGET.md`). When Step 9 renders no widget this fire
(nothing open), use a 2–3 line FOOTER after the processing acknowledgment instead:

> *First time processing a meeting for you. I set 3 defaults: **I'll capture commitments
> automatically** · **keep notes at standard length** · **flag new people as I spot them**.
> Say "tune meeting notes" to change any, or just tell me ("confirm commitments before saving" /
> "keep notes terse").*

Tap/answer → apply-choices → `save_skill_config(..., is_reconfigure=True, origin="first_fire_override")`.
The block renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "confirm commitments before saving" / "ask me first" | `commitment_capture = confirm_first` |
| "just capture commitments automatically" | `commitment_capture = silent` |
| "keep notes terse" / "shorter notes" | `verbosity = terse` |
| "full detail in my notes" | `verbosity = standard` |
| "batch new-person suggestions to Pulse" / "stop interrupting about new people" | `new_person_handling = batch_to_pulse` |
| "surface new people as you find them" | `new_person_handling = surface` |

After applying: `save_skill_config(..., is_reconfigure=True)` + confirm in one line.

## Step 1: Get the Meeting Source

### Option A: Transcript Auto-Pull (Preferred)

Resolve the transcript source via `shared/scripts/tool_discovery.discover_transcript_tool()` — it finds whichever transcript connector is wired (Granola / Fireflies / Otter / Read.ai / Zoom AI Companion / Teams summaries). Never hardcode Granola tool names.

If Gmail, Calendar, or Slack are connected:

1. Check your calendar for recent meetings around the time/context the user mentions
2. If a meeting is found, pull the transcript automatically via the discovered tool
3. Confirm the meeting matches the user's description

If no calendar connector is active, no transcript tool is discovered, or no meeting is found, move to Option B.

### Option B: Fallback to Pasted Text

If the user provides meeting notes, a transcript, or a summary:
- Accept paste from email, Slack, Granola link, or manual notes
- Parse as-is; don't require a specific format

### Clarifying Questions (if needed)

If the meeting source is ambiguous, ask:
- "Which meeting are you referring to? (date, attendees, company/project name)"
- "Do you have a transcript, an email thread, or should I check your calendar?"

---

## Step 2: Parse and Extract

From the meeting source, extract:

| Item | Purpose | Notes |
|------|---------|-------|
| **Attendees** | Track who was in the room | First/last names, roles if mentioned |
| **Key Decisions** | What was decided | Who decided, any dissent or caveats |
| **Action Items** | Who owns what by when | Owner, deadline, description |
| **Financial Info** | Budget, revenue, pricing, costs | Amounts, currency, scope of spend |
| **Scope Changes** | New work, reduced scope, additions | What changed from baseline |
| **Timeline Pressure** | Accelerated deadlines, delays | Impact on plan |
| **Relationship Dynamics** | Tensions, alignments, trust shifts | Internal or client-facing |
| **Risks & Opportunities** | What could go wrong or right | Business impact |

---

## Step 3: Classify Projects (Primary + Related)

Every meeting resolves to **exactly one primary project** plus zero or more **related projects**. This is not branching logic — it runs the same way for every call. A 1:1 that covered one topic has one primary and an empty related list; a call that touched a deal, a vendor issue, and a recruiting update has one primary and two related projects. No separate single-project vs multi-project path.

For the full resolution rules and confidence model, read `references/ORG_AND_THREAD_MODEL.md` and `shared/PASSIVE_CAPTURE.md` (Project Resolution section). The summary that matters here:

1. Scan the extracted content. For each distinct topic, run the resolution order: direct marker → alias match → people clustering → org clustering → weak signal → no signal.
2. Score each candidate project 0.0–1.0. The highest-scoring project becomes `primary_thread_id` (schema field name unchanged); the rest become `related_thread_ids[]`, each with a one-line `cross_ref_reason`.
3. The overall `classification_confidence` for the primary project goes on every event produced by this meeting.
4. Bands: `≥0.75` auto; `0.40–0.75` provisional (silently flagged for weekly review); `<0.40` low-confidence (queued to weekly review as an ambiguous classification).

**Do not interrupt the user to confirm classifications.** Silent capture is the default — the weekly classification review (in `insight-generator`) is where the user confirms or corrects batched calls. The only exception is a brand-new person or a brand-new org surfaced by this meeting; those are owner-skill concerns (`people-crm`, `workspace-manager`) and are handled via the suggestion mechanism in the Writer Contract, not as a classification prompt.

**Never let an active "go [project]" session override content-based classification.** The skill routes based on what the meeting was about, not which project the user happened to be in when they invoked this skill. If the user is in Project A and the call was primarily about Project B, the primary_thread_id is Project B. Project A may appear in related_thread_ids if it was discussed at all.

**Routing effect:** The full SESSION_NOTES narrative is appended to the primary project's `SESSION_NOTES_[NAME].md`. Related projects receive a short cross-reference line in their own session notes — never a duplicate of the full narrative. events.jsonl carries the full event (with primary + related + confidence); the markdown views are regenerated from it.

---

## Step 4: Route to the Primary Project

The primary project from Step 3 owns the SESSION_NOTES narrative. Save the full entry to: `[WORKSPACE_ROOT]/[Project Folder Name]/SESSION_NOTES_[NAME].md`.

For each related project, append a short cross-reference line to its own `SESSION_NOTES_[NAME].md`:

```
## [Date] — Cross-ref from [Primary Project display_name]
Brief reason this project was touched: [cross_ref_reason]. Full notes live in [primary project path].
```

Only prompt the user for a destination when classification confidence is `<0.40` on the primary project AND no plausible fallback exists (genuinely unknown content, not a known project). In that case: "I'm not sure which project this call belongs to. Best guesses: [top 3]. Pick one, or I'll hold it for your weekly review." Everything above the low-confidence floor routes silently.

### SESSION_NOTES Format

> **Sync rule (v3.11.1+):** If you add, rename, or reorder any section in the template below, update [`skills/enable-command-room-schedules/references/orchestrator-past-meetings.md`](../enable-command-room-schedules/references/orchestrator-past-meetings.md) Phase 4 sections list **in the same commit**. Pre-v3.6.4 these drifted silently; the past-meetings orchestrator dropped any content from a section it didn't know about.

```markdown
# Session Notes

## Meeting: [Title/Context]
**Date:** YYYY-MM-DD | **Duration:** Xm | **Attendees:** [List]

### Decisions
- [Decision 1]: [Who, context, any caveats]
- [Decision 2]: ...

### Action Items
| Owner | Task | Deadline | Status |
|-------|------|----------|--------|
| [Name] | [Task] | [Date] | Pending |

### Financial
- [Item]: $[Amount] | [Scope/Note]
- Run total: $[X] (budget impact, if any)

### Scope Changes
- [What changed]: [From → To]
- Impact: [Business impact]

### Context & Follow-Up
- [Assumption or clarification needed]: [Why it matters]

### Business Lens
- **Risks:** [What could derail this]
- **Opportunities:** [What we could leverage]
- **Timeline:** [Pressure points]
- **Relationships:** [Key shifts or trust factors]
```

> For the full SESSION_NOTES template with detailed fields and examples, see references/meeting-notes-detail.md → "Full SESSION_NOTES Template"

---

## Step 5: Update Master Tracker

After routing SESSION_NOTES, update `[WORKSPACE_ROOT]/_hq/MASTER_TRACKER.md`:

For each project or client mentioned in the meeting:

| Field | Update |
|-------|--------|
| **Last Touched** | Today's date |
| **Next Action** | Most critical action item (owner + deadline) |
| **Commitments** | Any promises made (e.g., "deliver X by date Y") |
| **Timeline Status** | Accelerated, delayed, on-track, at-risk |
| **Relationship Status** | New, strong, fragile, warming, cooling |
| **Financial Impact** | New spend, revenue, margin shift ($ and direction) |

**Example update:**
```
| Project X | Last: 2026-04-08 | Next: [Aria] deliver spec by 2026-04-15 | Commitment: Beta launch by May | At-risk (scope creep) | Strong | +$50K budget |
```

## Step 5b: Log Decisions (MANDATORY in both modes — via decision-log's write protocol, never a direct view write)

> **v4.5.2 (F-46 P1):** this step is part of the mandatory data layer, exactly like Step 5e commitments. Pre-v4.5.2 it was gated to deep mode while the Step 9 chat card still rendered "DECISIONS LOGGED" — the skill claimed 3 decisions and wrote zero events; decision-revisit, weekly insights, and the decision log were blind to them. A decision that renders in the chat card MUST exist as a `decision` event first.

For each decision extracted in Step 2, append a `decision` event through the `decision-log` skill's write protocol (locked writer + view regeneration — per the Writer Contract at the top of this file: DECISION_LOG.md is a regenerated view; writing it by hand is forbidden). Build the event via the shared builder so the shape matches the past-meetings writer's contract, then append via the gate:

```python
# After the Rule 22 preamble + cd "$PLUGIN_ROOT" (same pattern as Step 5e)
import sys; sys.path.insert(0, "shared/scripts")
from meeting_capture import build_decision_event
from event_gate import append_event

ev = build_decision_event(
    "<what was decided — one standalone factual sentence>",
    source_ref="granola:<meeting_id>",          # same source_ref as the parent meeting event
    source_skill="meeting-notes",
    primary_thread_id="<same as parent meeting event>",
    person_ids=["<canonical decider id>", "<others party to it>"],
    project_id="<project_NNN — decision-log v3.13.0 mandate, when resolvable>",
    evidence="<verbatim or near-verbatim transcript quote>",
    rationale="<why — alternatives considered, if stated>",
    made_by="<who made the call, or 'Team consensus'>",
    source_event_seq=<seq of the parent meeting event>,
    confidence=<attribution confidence 0.0-1.0>,   # below 0.75 the builder forces data.pending_review: true
)
append_event("<WORKSPACE>/_hq/data/events.jsonl", [ev], holder="meeting-notes.decisions")
```

The speaker-attribution ambiguity guard (Step 5e) applies to decisions identically: ambiguous decider → `pending_review: true`, never an auto-pick. The cross-meeting fusion guardrail applies too — a decision whose evidence phrase does not appear in THIS transcript is not written.

After the appends, regenerate the view via `render_decision_log.regenerate("<WORKSPACE>")` (decision-log's renderer invocation pattern — silent per CONTRACT Rule 4).

Each event carries the fields the old hand-written entry carried:

```markdown
### [Date] — [Decision Title]        <- rendered BY the view regeneration, shown here for content shape only
**Project:** [project name]
**Decision:** [what was decided]
**Rationale:** [why — from meeting context]
**Made by:** [who made the call, or "Team consensus" if unclear]
**Impact:** [what changes as a result]
**Status:** Active
**Tags:** [relevant categories]
```

If no decisions were made in the meeting, skip this step. Don't create empty entries.

## Step 5c: Update People Database

If `[WORKSPACE_ROOT]/_hq/PEOPLE.md` doesn't exist, create it with a header: `# People\n> Auto-created by meeting-notes\n`

For each attendee from Step 2, check `[WORKSPACE_ROOT]/_hq/PEOPLE.md`:

- **Duplicate check first:** Search PEOPLE.md for the person's name (check for common variations — first name only, full name, nickname, different spelling like Skyler/Sara). If a likely match exists, update that entry instead of creating a new one. If unsure, ask the user: "Is [meeting attendee name] the same person as [existing entry name]?"

- **If the person is new** (no match found in PEOPLE.md): Add a profile entry:
  ```markdown
  ### [Full Name]
  - **Company:** [if mentioned or inferred]
  - **Role:** [if mentioned]
  - **How We Know Them:** Met in [meeting context/project name] meeting on [date]
  - **Projects:** [project this meeting belongs to]
  - **Last Interaction:** [today's date] — [meeting topic]
  - **Key Notes:** [anything notable about them from the meeting]
  - **Contact:** [email if available from calendar invite]
  ```

- **If the person already exists**: Update their **Last Interaction** date and add any new context to **Key Notes**. Add the project to **Projects** if not already listed.

Only add people who were meaningfully involved in the meeting. Skip generic attendees (e.g., a notetaker or admin who didn't participate).

---

## Step 5e: Append Commitment Events (MANDATORY when action items exist)

For **every action item** captured in Step 2's Action Items table, append one `commitment` event to `_hq/data/events.jsonl`. This is the canonical-shape required by `shared/COMMITMENT_SCHEMA.md` — read that file once if you've never written commitment events before, then follow the recipe below for each item.

**Trigger conditions — the capture floor (Stage D 2026-07; all must hold):** the action item has (1) a **clear owner** (an identifiable named person), (2) a **clear deliverable** (a specific artifact or decision, not "circle back"), and (3) a **real consequence** (someone is waiting on it, a date depends on it, or dropping it costs something). Vague action items ("we should think about X", "let's revisit") DO NOT qualify — skip them silently. This is the rule that cut one live workspace's open set 71→33: below-floor items bury real promises. See `COMMITMENT_SCHEMA.md` § "Extraction triggers" for full guidance. **Suppression rules:** if `_hq/config/commitment-rules.md` exists, read it BEFORE writing and skip any item matching a `never-track` pattern the user has taught.

**Learned extraction hints (Phase 6 Loop 5).** If `_hq/data/extraction-hints.md` exists, read it BEFORE extracting: `from extraction_hints import load_extraction_hints` → the returned lines are few-shot exemplars from documented misses (items the CEO had to log by hand within 24h of a meeting, clustered and approved by insight-generator's Loop 5 pass). Use them as additional positive examples of what SHOULD be captured — they extend the baked-in guidance; they never override the capture floor or a `never-track` rule. Missing file → no change. This is how the extractor improves from its own documented failures.

**Classify `data.kind` at capture (Stage D — REQUIRED; the gate rejects a kind-less commitment on the strict path):**
- Counterparty determinable (someone else owes it, or the user owes it TO someone) → `kind: "promise"`.
- Self-owed with NO counterparty (the user owes it to nobody but themselves) → `kind: "task"` — tasks live on the triage surface, never enter CRU matching, and never render in commitment aging.
- Scheduling intent ("set up the call with X", "lock time with Y") → `kind: "scheduling"`.
- "Let's discuss X" / agenda items → the existing `commitment_to_discuss` type (unchanged), NOT a commitment event with `kind: agenda`.
- Genuinely ambiguous → `kind: "promise"` with `data.pending_review: true` (existing flag; surfaces for review, never auto-closed).

**pending_review is default-on for low-confidence attribution (v4.5.2 safety inversion — MANDATORY).** CRU auto-resolution gates on `data.pending_review`: a low-confidence extraction that FORGETS the flag auto-resolves at high match with no human gate. So the rule is inverted — absence of the flag is an ASSERTION of high-confidence attribution, never a default. Set `data.pending_review: true` at capture whenever ANY of these hold:
- owner attribution is ambiguous or unresolved (incl. every `attribution_ambiguous` / `attribution_unknown` case below);
- a counterparty is named in the source but resolves to no person record;
- overall attribution confidence is below 0.75 (the `meeting_capture` builders enforce this floor for decisions; apply the same floor to commitments);
- the item is a sensitive category (firing / pricing / contract terms) — flag regardless of confidence, same rule the scheduled past-meetings writer runs.

If you cannot assert high confidence, you MUST set the flag. An ambiguous item without `pending_review` is a write defect, not a judgment call.

**Due-date nudge (S2):** every captured commitment proposes a `due` (from meeting language or a sensible default the user can push) OR carries explicit `data.no_due: true`. Undated items surface in the weekly triage, not the aging view — target is < 30% undated.

**Resolve the owner to a person id BEFORE appending.** Use `aliases.json` to canonicalize (e.g., "Mira" → `person_011`). If the owner is the user themselves ("I'll send the deck"), use the user's canonical id (the entity with `is_primary_user: true` or `is_user: true`). If you cannot resolve the owner to a person id, surface a one-line suggestion at the end of the meeting summary — but DO NOT skip the commitment; emit it with `owner_id: ""` and the title still set, so the commitment isn't lost.

**Speaker-attribution ambiguity guard (v3.2.3+):** before locking in `owner_id` from an alias lookup, check the meeting's attendee list for first-name collisions. If the Granola-tagged speaker name's first name matches MULTIPLE attendees on this call (e.g. "Rio" with both Rio Lange AND Rio Sample present), do NOT auto-pick — emit the commitment with `owner_id: ""`, add `data.attribution_ambiguous: true`, and add `data.attribution_candidates: [person_id_1, person_id_2, ...]`. Then add an explicit line to the meeting summary:

> *"Heads up — couldn't tell which '[speaker name]' was speaking on this call. Both [Name 1] and [Name 2] were there. Want to confirm who owns the [N] action items that came from them?"*

(Render the correct singular/plural from N — "the 1 action item" / "the 3 action items" — never "item(s)".)

Same guard applies for `decision` events. The bug class this closes: Granola has trouble disambiguating same-first-name attendees, and silently pre-v3.2.3 the resolver picked one — usually the alphabetically-first match in `aliases.json`. Memorialized failure: Sam's Category Company has two PMs (Rio Lange, Rio Sample); commitments by either one were attributed to the wrong person and the user had no signal that the attribution was uncertain. **Never auto-pick on ambiguous first-name attribution; surface for review.**

### Canonical commitment event shape

```json
{
  "seq": <reserved by writer helper>,
  "ts": "<ISO 8601 — the moment the commitment was MADE, i.e., the meeting start time>",
  "type": "commitment",
  "source_skill": "meeting-notes",
  "primary_thread_id": "<same as the parent meeting event's primary_thread_id>",
  "related_thread_ids": [],
  "classification_confidence": <inherit from the parent meeting event>,
  "person_ids": ["<owner_id>", "<counterparty_id — MUST be present when determinable (Stage E receipts)>", "<other people involved>"],
  "data": {
    "owner_id": "person_NNN",
    "counterparty_id": "person_NNN — who the deliverable is owed TO (or who owes it to the user). MUST populate when determinable (Stage E, F5): this feeds the CRU candidacy gate directly; without it the matcher falls back to title tokens and misses real completions (Bug #103 class). Retires requester_id/requester_person_id for NEW writes — readers keep the alias chain forever.",
    "counterparty_name": "<free-text name — SHOULD set when the counterparty is named in the transcript but resolves to no person record; the matcher matches recipient names against it>",
    "title": "<short verb-phrase, lowercase verb start, no trailing period, ≤120 chars>",
    "kind": "promise" | "task" | "scheduling",
    "due": "YYYY-MM-DD",
    "status": "open" | "overdue",
    "source_event_seq": <seq of the parent meeting event>,
    "source_ref": "granola:<meeting_id>"
  }
}
```

**Status calculation at write time:**
- If `due` is set and the parsed date is before today (UTC) → `status: "overdue"`
- Otherwise → `status: "open"`

**Title format examples:**
- ✅ "send updated pricing deck to Mira"
- ✅ "decide on Q2 hiring budget"
- ✅ "introduce Sam to Bo at Acme"
- ❌ "Send the deck." (no leading verb lowercase + trailing period)
- ❌ "follow up" (no specific deliverable)
- ❌ "discuss pricing" (no concrete deliverable)

**Dedup safety:** If you've already processed this Granola transcript before, check that you're not double-emitting commitments. The canonical already-processed marker is the `meeting_processed` receipt — check `meeting_capture.already_processed(workspace_root, source_ref)` (v4.5.2; a bare `meeting` event with the same `source_ref` also counts, for pre-receipt history). Match on `(source_ref, title)` — if both already exist for a `type: commitment` event, skip the append. The `scan-for-commitments` skill enforces this same dedup; the two skills are interchangeable for the same source data.

**Surface a count after appending:** at the end of the meeting summary, line: "Logged N commitments (M you owe, K others owe)." — gives the user immediate confirmation that the extraction fired. **N comes from the Step 9a3 claim audit (disk read-back), never from the extraction list** — F-50 shipped a 7-claimed/6-written off-by-one because the surface counted intent.

---

## Step 5e-bis: Close commitments fulfilled by this meeting (v3.11.1 — REQUIRED)

Commitments accumulate as "open" in events.jsonl forever unless something explicitly closes them. Before v3.11.1 only `follow-up-ritual` emitted `commitment_resolved`, which left meeting-notes as the largest open-commitment source — every commitment captured in Step 5e stayed open even when the same meeting that recorded the new commitment had attendees confirming the prior commitment was done. M's 2026-05-20 audit found 191 open commitments in his workspace, many already satisfied.

**Procedure (run BEFORE emitting new commitments in Step 5e, so a "delivered today" item isn't immediately re-opened):**

1. Load all open commitments for this meeting's `primary_thread_id` AND for each attendee `person_id` via `shared/scripts/cru_match.py::load_open_commitments(events_jsonl_path)`. This handles all 5 commitment shape variants (canonical, flat-new, legacy `owner`, `owner_person_id`-variant, pending-review).
2. For each open commitment, score it against the transcript using the existing CRU helpers (`cru_match.py` Path 3 — same scorer that past-meetings uses for transcript matches). HIGH-confidence completion language ("delivered", "sent it over", "done", "shipped") on a commitment owned by an attendee → auto-resolve. Schedule-shift language ("pushing to next week", "got delayed") → `commitment_updated`, NOT resolved.
3. For each auto-resolve, close through `commitment_state.close_commitment(workspace_root, <commitment_id>, resolved_by=<attendee_person_id_or_user_id>, evidence=<≤200-char quote-or-paraphrase from transcript>, source_skill="meeting-notes")` — THE closure path (Stage B 2026-07, F2; supersedes the build-and-append procedure). It normalizes legacy ids, refuses no-match ids loudly (`CommitmentIdError` → skip, never write an orphan tombstone), is idempotent over the full resolved-id set, and never auto-resolves a `pending_review` item (`PendingReviewError` → leave it for the review surface). The Path 3 scoring in step 2 is unchanged.
4. **Conservative auto-resolve only.** MEDIUM-confidence matches → emit `commitment_review_proposed` for next Pulse fire's one-click confirm surface, do NOT auto-close. The user-trust cost of falsely closing a commitment is much higher than the cost of leaving one open for a day.
5. **Silent.** Per CONTRACT Rule 24, do NOT narrate "auto-resolved 2 commitments" in the meeting summary. The user sees the result on the next Commitments fire (the resolved item simply doesn't appear).
6. **Dedup.** Handled by close_commitment itself — it checks the FULL resolved-id set and returns an `already_resolved` result instead of double-writing, so a same-turn race is a true no-op.

Same shape rules as follow-up-ritual's Step "Surface Open Commitments" — the two skills follow the same v3.4.5 decision-CRU pattern.

---

## Step 5f: Person Proposals for Unknown Names (MANDATORY in both modes — v4.5.2, F-46 P2b)

For each person who was **meaningfully involved** in the meeting (spoke, made a decision, took or received an action item, or was surfaced as an intro/prospect target) and resolves to NO person record via `aliases.json` / entities, write the durable proposal event — the same contract the scheduled past-meetings writer runs (its Phase 4.5b). Pre-v4.5.2 this skill surfaced "say add [name]" in chat only; if the user dismissed that chat, the name was stranded unrecorded forever while past-meetings wrote `person_proposal` events for the identical situation. Two capture writers, one contract — the event IS the capture; the chat line is just the pointer.

1. **Dedup first (people_writer contract, v3.2+):** call `people_writer.find_existing_person(workspace_root, name=..., email=..., aliases=...)`. On a match, emit a `person_update_proposal` referencing the existing `person_id` with the proposed delta — never a second new-person proposal.
2. **On a miss**, build + append the proposal:

```python
import sys; sys.path.insert(0, "shared/scripts")
from meeting_capture import build_person_proposal_event
from event_gate import append_event

ev = build_person_proposal_event(
    "<full name as heard>",
    source_ref="granola:<meeting_id>",
    source_skill="meeting-notes",
    primary_thread_id="<same as parent meeting event>",
    inferred_role="<role if stated, else None>",
    inferred_org="<org if stated/inferable, else None>",
    evidence="<the transcript line that surfaced them>",
    review_reason="<one line: why this needs the user's call>",
    confidence=<0.0-1.0>,
)
append_event("<WORKSPACE>/_hq/data/events.jsonl", [ev], holder="meeting-notes.person_proposals")
```

Every proposal carries `data.pending_review: true` unconditionally (the builder enforces it) — proposals are adjudicated by the user's Add / Not-relevant click, and they re-surface in review surfaces until adjudicated instead of dying with the chat.

3. **Chat surface:** the `new_person_handling` config still governs whether the suggestion interrupts now (`surface`) or batches to Pulse (`batch_to_pulse`) — but that setting governs the CHAT layer only. The event writes in both settings, in both processing modes. Skip generic attendees (notetaker, silent admin) — same "meaningfully involved" floor as Step 5c.

**This step never writes person records** — `person_proposal` / `person_update_proposal` events only; people-crm executes the create/update on adjudication via `people_writer` (apply-choices Step 3a).

---

## Step 5d: Update Team Profiles (if `_people/` exists)

If `[WORKSPACE_ROOT]/_people/_team-config.md` exists, check meeting attendees against the team roster:

1. **For each attendee who matches a roster entry:**
   - Append to their `_people/[name].md` Interaction Log: today's date, "meeting", one-line meeting summary, reference to session notes file
   - For each action item assigned to this person (from Step 2): add to their Active Commitments table with due date, source ("Meeting: [title] [date]"), status "Open"
   - If relationship dynamics were noted about this person in the Business Lens (Step 7): append a one-liner to their Working Style section — but only if it's CEO-relevant and substantive (not "was in the meeting")

2. **For attendees NOT on the roster but who were meaningfully involved:**
   - Do NOT auto-add. Instead, note at the end of the meeting summary: "💡 [Name] was in this meeting but isn't on your team roster. Say 'add [name] to my team' if you want to track them."
   - Only suggest this for people who spoke, made decisions, or took action items — not passive attendees.

3. **Check for commitment updates:**
   - If the meeting resolved or delivered a commitment that's already in a team member's profile (e.g., "Bowie presented the Aspen Project numbers"), update the status cell in the PERSON.md profile TABLE ONLY (a regenerated Tier-2 view) to "Delivered" with today's date — AND close the canonical commitment via `commitment_state.close_commitment(...)` per Step "CRU auto-resolve" above. **NEVER edit the commitment event's `data.status` in events.jsonl (F4)** — in-place status mutation is the forbidden write class; closure is a tombstone append only.

---

## Step 6: Update Ref Files as Needed

If the meeting revealed new information, update the appropriate ref files:

| File | Trigger | Update |
|------|---------|--------|
| `contacts.md` | New person mentioned | Add: name, role, company, email/phone if available |
| `scope.md` | Scope or scale changed | Document change: what was baseline, what's new, impact |
| `financials.md` | Budget, spend, or revenue mentioned | Add line item with date, amount, rationale |
| `risks.md` | Risk or blocker surfaced | Log risk: description, severity, mitigation |
| `timeline.md` | Deadline or schedule shifted | Update milestone, flag if accelerated or slipped |

Reference the SESSION_NOTES file in the update: `See [Project Name] SESSION_NOTES, [Date]`.

---

## Step 7: Business Lens — What Matters (deep mode only)

After processing, surface the **business lens**. Frame these for **any business owner**, not just consultants.

### Key areas to examine:

**Scope & Delivery** — Did scope creep? Is timeline still realistic? Who owns this?

**Financial Health** — Budget being consumed faster? Any new revenue or cost shifts? Visibility on P&L impact?

**Relationships & Trust** — How's the relationship (warming, cooling, stressed, strong)? Any misalignment or trust issues to address proactively?

**Risks & Execution** — What could derail this? Pressure to accelerate beyond capacity? Most likely failure mode?

**Opportunities** — Expand, cross-sell, or deepen? Could a shift unlock value? What's the play?

> For detailed business lens questions to ask the user, see references/meeting-notes-detail.md → "Business Lens Questions"

---

## Step 8: Ask 2-3 Follow-Up Questions (deep mode only)

After processing, ask the user 2-3 smart follow-up questions that pull context and surface implications. These should **not** be generic ("how did it go?"), but specific to what the meeting revealed.

### Examples (adapt to your business):

**If scope changed:**
- "Do you have the bandwidth to absorb this change, or do you need to negotiate timeline or resourcing?"

**If financial impact surfaced:**
- "At this burn rate, when do you recalculate your budget? Should we flag this for a repricing conversation?"

**If timeline accelerated:**
- "What was your original deadline plan? Is this new deadline still feasible without cutting quality or scope?"

**If relationship shifted:**
- "Is [person] expressing a genuine concern, or is there tension you need to surface directly?"

**If action items landed:**
- "Who's the single owner of the critical path item? Do they have the info and capacity to execute?"

---

## Gotchas & Failure Patterns

> For detailed analysis of 10 common failure patterns (risk, prevention, check), see references/meeting-notes-detail.md → "Gotchas Section"

**Quick checklist:**

1. Meeting notes filed but not acted on — Is Next Action dated and assigned?
2. Scope creep without financial adjustment — Does change have budget/timeline impact in Tracker?
3. Financial numbers not tracked — Can you trace every $ to a line item?
4. New contacts not recorded — Can you pull up a contact from 6 months ago?
5. Relationship shifts missed — Is relationship status updated after every meeting?
6. Action owner doesn't know they own it — Does owner have visibility?
7. Master Tracker becomes stale — Is timestamp recent?
8. Granola transcript incomplete — Does transcript span full meeting?
9. Follow-ups are generic — Are your questions actionable?
10. Attendees understood differently — Did everyone leave with same understanding?

---

## Brief Authoring Rules — forwardable-clean (v2.10.9+)

**The .docx brief is forwardable to other meeting attendees.** Treat it as such. The brief contains ONLY factual, third-person, shareable content:

- Attendees, date/time, duration
- Topics covered (factual recap, not editorializing)
- Decisions reached
- Action items with owner + deadline (factual record only — not "M needs to clarify with…" framing)

**Forbidden in the brief docx:**
- Internal asks ("M needs to think about…", "you should reach out to…", "your call on…")
- Follow-up email drafts of any kind, in any section, even labeled as drafts
- Clarifying questions or "needs your decision" framing
- Business Lens commentary (risks, opportunities, relationship dynamics — that's the Step 7 chat surface, not the brief)
- Per-attendee personalized notes that one attendee shouldn't see about another

**Required in the brief docx:**
- Footer is auto-applied by `brief_writer` as `Command Room` (centered, muted). No "Forwardable: yes" line is needed — the absence of internal asks IS the forwardability signal. (Pre-v2.14.32 the footer carried a self-attestation line; v2.14.32+ structurally enforces clean output, so the attestation became redundant.)

Anything that's M-facing only (clarifications, follow-up drafts, internal decisions) splits out of the brief and into the chat OPEN ITEMS section instead. The brief stays a clean shareable artifact; the open-items live in chat where M resolves them.

---

## Step 9 — Converged chat output (v2.10.9+ — four-section card, forwardable brief)

After all extraction + persistence steps complete, produce the chat surface as a four-section card: **RECAP → DECISIONS LOGGED → BRIEF link → OPEN ITEMS**. Same data shape used by the scheduled `cr-past-meetings` orchestrator — single chat-format contract for both on-demand `process meeting` and the 5pm scheduled sweep.


**Step 9a — Generate the .docx brief (v2.14.32+ — `brief_writer` flow).** Use `shared/scripts/brief_path.get_brief_path("past_meeting", slug, date)` to compute the absolute path under `_hq/meetings/`, then pipe structured section content as JSON to `shared/scripts/brief_writer.py` stdin (same flow as `orchestrator-past-meetings.md` Phase 4 step 7 — see that file for the canonical JSON shape and section list). `brief_writer` produces deterministic, polished output with a hard-coded `Command Room` footer; the pre-v2.14.32 docx-skill invocation pattern + `Forwardable: yes` footer line are dead. Brief content still follows the **Brief Authoring Rules** above — factual recap only, no internal asks, no follow-up drafts. Cache the absolute path.

**Step 9a2 — Write the `meeting_processed` receipt (v4.5.2, MANDATORY — F-46 P2a).** After the brief lands (so `brief_path` is known), append one `meeting_processed` event — the same receipt the scheduled past-meetings writer emits, and the canonical already-processed marker its Phase 3 dedup and the no-prep detectors read. F-50 proved the bare `meeting` event held off double-capture by accident, not contract; the receipt is the contract. This is a substrate event, NOT a `pack_run` run-receipt (that plumbing is owned elsewhere — do not touch it here).

```python
import sys; sys.path.insert(0, "shared/scripts")
from meeting_capture import build_meeting_processed_event
from event_gate import append_event

ev = build_meeting_processed_event(
    "<granola meeting_id>",
    source_ref="granola:<meeting_id>",           # same spelling as the parent meeting event
    source_skill="meeting-notes",
    primary_thread_id="<same as parent meeting event>",
    extracted_count=<decisions + commitments + proposals written this run>,
    pending_review_count=<how many of those carry data.pending_review>,
    brief_path="<workspace-relative BRIEF_PATH from Step 9a>",
)
append_event("<WORKSPACE>/_hq/data/events.jsonl", [ev], holder="meeting-notes.receipt")
```

On a deliberate user re-process of an already-processed meeting, still write the receipt but add `data.rerun_note` (the extracted-event dedup in Step 5e prevents double-capture; the second receipt documents the re-run honestly). Check `meeting_capture.already_processed(workspace_root, source_ref)` BEFORE processing to know which case you're in.

**Step 9a3 — Claim audit (v4.5.2, MANDATORY — gate on this before ANY closing summary).** The closing chat surface may enumerate ONLY what verified on disk. Count the events after appending, then speak:

```python
import sys; sys.path.insert(0, "shared/scripts")
from meeting_capture import count_meeting_writes

counts = count_meeting_writes("<WORKSPACE>", "granola:<meeting_id>")
# counts = {"meeting": 1, "meeting_processed": 1, "decision": 3, "commitment": 6, "person_proposal": 2, ...}
```

Every number in the ack line, the DECISIONS LOGGED section, and the "Logged N commitments" line comes from `counts` — never from extraction intent. If a count is lower than what you attempted to write, a write FAILED: say so plainly ("I captured 3 decisions but only 2 saved — say 'process meeting' again and I'll retry the missing one"), and never render the failed item as logged. The dogfood caught both failure directions: 3 decisions claimed / 0 written (F-46) and 7 claimed / 6 written (F-50) — neither may recur silently.

**Step 9b — Invoke `follow-up-ritual` silently.** **Recursion guard (shared invocation contract, P1.3):** this step runs ONLY when meeting-notes is the top-level skill. When meeting-notes was itself invoked FROM follow-up-ritual (its internal logging step), SKIP 9b entirely — follow-up-ritual already owns the draft layer, and re-invoking it loops the pair. Drafts per-attendee follow-up emails as TEXT only (lazy creation per `EMAIL_DRAFT_PROTOCOL.md`). The drafts surface in the next Inbox / Commitments fire where the user picks `N send` / `N draft`. **The drafts do NOT appear in the chat output of this skill, and do NOT appear in the brief docx.** This step matches `orchestrator-past-meetings.md` Phase 4 step 3 — same skill, same output destination.

**Step 9c — Render the 4-section chat card** through `chat_output_renderer.py` — the renderer landed in v2.12.4; there is no markdown fallback. The card has these sections in order:

1. **Header line:** `Processed meeting: [Title] · [date] [time range]`
2. **RECAP** — 3–5 factual bullet points summarizing what happened (third-person, shareable register — same content profile as the brief docx).
3. **DECISIONS LOGGED** — list of decisions, each shown with a "added to your decision log" annotation. Each decision listed here ALREADY EXISTS as a `decision` event in `events.jsonl` (written in Step 5b, both modes, at processing time — not on user click), and the section's count comes from the Step 9a3 claim audit. Render exactly `counts["decision"]` entries; a decision whose event failed to write goes in the failed-write line, never in this section.
4. **BRIEF** — link to the .docx with one-line metadata: `Clean recap — safe to forward`. **v3.13.0+ surface the brief as an H2 heading link at the BOTTOM of the chat turn** via `chat_output_renderer.doc_headline_link(label, artifact_url)`. Format: `## → **[{Meeting title} — Past Meeting Recap]({computer_url})**`. `present_files` is no longer the primary opener — per CONTRACT.md Rule 3 (v3.13.0+) it's demoted to a reveal-in-folder secondary; include the `present_files` call only if the user is likely to want to navigate the filesystem (rare for meeting recaps — default: skip).
5. **OPEN ITEMS** — interactive list of items requiring M's resolution. Each item is M-only (clarification needed, decision he must make, action with no resolved owner).

**Renderer pre-flight (v2.10.9+):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from chat_output_renderer import render_chat_output_widget; print('OK')"
```

If stdout is not exactly `OK`, ABORT and surface plain English: `(Quick hiccup on my side — couldn't post the formatted output. Say "process meeting" again in a moment and I'll retry.)` Do NOT fall back to hand-written prose.

```python
# Inside a python3 -c block invoked after `cd "$PLUGIN_ROOT"` (see preamble above)
import sys
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import render_chat_output_widget

data_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "meeting-notes",  # W4 (Phase 3) — stamped into every Apply-all tuple as src; apply-choices dispatches on it statelessly (no 60-min fire-marker window)
    "header": None,
    "sections": [{"title": None, "count": None, "items": [meeting_item]}],
    "save_confirmation": None,
}
html = render_chat_output_widget(data_view, wrapper="fragment")
# Call mcp__visualize__show_widget with html as the widget body
```

**Open-items surface — `show_widget` all-batch button widget (v2.10.9+).** Open items are M-only resolutions (a clarification needed, a decision M must make, an action with no resolved owner). They render as a `show_widget`-rendered card with per-item button rows; selections accumulate in widget local state, one "Apply all" button fires the consolidated `apply choices: [...]` payload that `apply-choices` skill catches and dispatches. See `shared/CHAT_ACTION_WIDGET.md` for the full widget spec. Probe results: `PROBE_RESULTS_past-meetings-open-items.md` (workspace root).

**Posting rule:** the post is the rendered widget HTML, surfaced via `mcp__visualize__show_widget`. Do NOT paraphrase, do NOT compose chat strings, do NOT prepend or append narration. The widget IS the surface.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "Got it — {brain_name} processed [Meeting]. Brief: Clean recap · no internal asks."
- Good: "Got it — I've processed [Meeting]. Brief: Clean recap — safe to forward."

**Step 9d — Surface .docx as the canonical H2 heading link below the widget (v3.13.0+).** After posting the widget, render the H2 link at the bottom of the chat turn:

```python
# v3.13.0+: per CONTRACT.md Rule 3 — H2 heading link is the canonical opener.
# present_files was demoted to reveal-in-folder secondary because Cowork's
# Windows card-click handler doesn't open .docx reliably (M's 2026-05-20 testing).
from chat_output_renderer import doc_headline_link
from brief_path import get_brief_artifact_url

label = f"{meeting_title} — Past Meeting Recap"
artifact_url = get_brief_artifact_url(absolute_docx_path)  # native computer:// form
h2_link = doc_headline_link(label, artifact_url)
# Output the h2_link as the LAST line of the chat turn (after the widget, after Sources, after everything else)
print(h2_link)
```

`present_files` is optional and discouraged for meeting recaps — if you include it, position it AFTER the H2 link (so the user sees the opener first and the reveal-in-folder card second as a convenience). The H2 `computer://` link is the canonical clickable surface; workspace-relative paths and `file://` URLs render as plain text.

**Follow-up drafts do NOT appear in the chat output.** Per Step 9b, drafts are staged silently via `follow-up-ritual` and surface in the next Inbox / Commitments fire. The processed-meeting card is for recap + decisions + brief link + open items only. M's reasoning (Apr 29 feedback): the brief is shareable and the chat output should match — internal-only content lives elsewhere.

**Business Lens + 2-3 follow-up questions are NOT in the default surface.** Those are gated behind `process deep` opt-in (see Processing Modes below). The default `process meeting` surface matches the scheduled fire — same scannable item shape, same auto-commit annotation, same brief card. Power-users who want the full Socratic treatment opt in explicitly.

---

## Processing Modes

### Default: Light Mode
The standard processing path. Fast, focused, minimal file operations. Use this unless the user explicitly asks for more.

1. **Get source** → Granola transcript or pasted text (Step 1)
2. **Extract** → Decisions, actions, financials, scope, relationships, risks (Step 2)
3. **Classify threads** → primary + related + confidence, silently (Step 3)
4. **Route** → SESSION_NOTES to primary project; cross-ref lines to related projects (Step 4)
5. **Update tracker** → Master Tracker: last touched, next action, commitments (Step 5)
6. **Append commitments** → one canonical commitment event per qualifying action item (Step 5e — MANDATORY)
7. **Append decisions** → one `decision` event per extracted decision via decision-log's write protocol (Step 5b — MANDATORY, v4.5.2)
8. **Append person proposals** → one `person_proposal` / `person_update_proposal` event per unknown name (Step 5f — MANDATORY, v4.5.2)
9. **Generate brief, write the `meeting_processed` receipt, run the claim audit, render via renderer** → Step 9 (v2.10.8+ format; 9a2/9a3 MANDATORY, v4.5.2)

**Skipped in light mode:** People database VIEW updates (5c), team profiles (5d), ref file updates (Step 6), Business Lens analysis (Step 7), follow-up questions (formerly Step 8 — gated to deep mode in v2.10.8+), email/calendar context check (old Step 3). These still exist — they run in deep mode. **The event data layer runs in BOTH modes and must not be skipped: commitment events (5e), decision events (5b), person proposals (5f), and the meeting_processed receipt + claim audit (9a2/9a3).** Pre-v4.5.2 the decision-log step was on this skipped list while the chat card still rendered "DECISIONS LOGGED" — that is F-46: the surface claimed writes the mode had skipped. Mode gating applies to views, analysis, and questions — never to substrate events.

### Deep Mode ("process deep", "full meeting analysis")
The full 12-step cascade. Use when the user says "process deep", "full analysis", "deep process", or explicitly asks for the complete treatment.

1. **Get source** → Granola transcript or pasted text
2. **Extract** → Decisions, actions, financials, scope, relationships, risks
3. **Classify threads** → primary + related + confidence (see `ORG_AND_THREAD_MODEL.md`)
4. **Check context** → Email/Calendar for additional related threads
5. **Route** → SESSION_NOTES to primary project; cross-refs to related projects
6. **Update tracker** → Master Tracker (last touched, next action, commitments)
7. **Append commitment events** → one canonical commitment event per qualifying action item (Step 5e — see `shared/COMMITMENT_SCHEMA.md`)
8. **Log decisions** → `decision` events via decision-log's write protocol (Step 5b); the DECISION_LOG view regenerates
9. **Append person proposals** → `person_proposal` / `person_update_proposal` events for unknown names (Step 5f), then update profiles in `_hq/PEOPLE.md` (Step 5c)
10. **Update team profiles** → If `_people/` exists, update interaction logs + commitments for team members
11. **Update ref files** → contacts, scope, financials, risks, timeline as needed
12. **Apply lens** → Surface business implications (scope, financials, relationships, risks, opportunities)
13. **Ask follow-ups** → 2-3 smart questions that pull context and surface implications
14. **Confirm action** → Ensure critical action items have owners and visibility

---

**Ready to process a meeting?** Share the Granola link, paste the transcript, or give me the meeting context and I'll do the rest.

---

## What It Doesn't Do

- Does not draft post-meeting follow-up emails — that's `follow-up-ritual`.
- Does not prep for upcoming meetings — that's `call-prep`.
- Does not write person records or project records directly — surfaces suggestions that `people-crm` / `workspace-manager` execute.
- Does not run as a scheduled task — fires only on explicit user request with a transcript in hand.
- Does not store raw transcript text in events.jsonl — summaries and source refs only.
