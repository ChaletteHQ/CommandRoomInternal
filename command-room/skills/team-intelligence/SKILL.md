---
name: team-intelligence
description: "Never walk into a 1:1 cold again — the team layer: who owns what, who's overloaded, what each person delivered, and a prep brief per direct report. Fires on: 'my team', 'team status', 'prep for 1:1 with [name]' / 'prep me for my 1:1 with [name]', 'who owns [project/deliverable]', 'who's overloaded', 'what has [name] delivered this quarter', 'log commitment for [name]', 'discover my team'. Builds and maintains per-person delivery records from meetings, commitments, and threads. Does NOT fire on 'prep me for the [external] call' / 'prep me for my 2pm' (call-prep — external meeting brief), 'who is [name]' (people-crm — relationship record), or 'who should I reach out to' (relationship-moves). Team model and 1:1 brief spec: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use team-intelligence for:** direct reports and leadership team — the CEO's people cabinet. Extends people-crm with commitment tracking, 1:1 prep, team-wide cadence and drift detection.
- **Use `people-crm` for:** external relationships (board, investors, advisors, customers, vendors). Same data model, different scope.
- **Not an HR system:** no performance reviews, no compensation, no org chart diagrams, no 360s, no hiring workflow.

## Writer Contract

- **Reads from:** `_hq/data/entities.json` person records filtered to `reports_to_id = CEO` or members of any primary-focus org (`is_primary_focus: true`).
- **Writes:** `commitment` events (and closures through `commitment_state.close_commitment`) to `_hq/data/events.jsonl` with v2.2 shape (`primary_thread_id` + `org_ids[]`). This is the ONLY commitment write this skill performs — per-person commitment/overload/drift signal is DERIVED from those events at read time, never stored on person records (the pre-P1.7 contract listed `open_commitments[]` / `delivered_commitments[]` / `overload_signal` / `drift_signal` person-record fields no step ever wrote — a phantom write path). Overdue is a derived state, not an event type.
- **Produces (not data-layer writes):** 1:1 brief .docx files at `_hq/meetings/` via `brief_path.get_brief_path(workspace_root, "call_prep", "1-1 <name>", date)` — the one home for all meeting-prep briefs, shared with call-prep; team-pulse reports saved to `_hq/briefings/`.
- **Does not write to:** `aliases.json`, `classifier_feedback.jsonl`. Does not create new person records — that's people-crm's job; team-intelligence only extends existing records it has scope authority over.
- **Conflict boundary:** shares `person` entity with people-crm. people-crm owns record lifecycle and core fields; team-intelligence adds NO person-record fields — its signal lives in events.

**Person-record ownership (canonical paragraph — IDENTICAL in people-crm and team-intelligence; edit both or neither):** `_hq/data/entities.json` person records are the ONE canonical person store. **people-crm** owns record lifecycle and core fields (create, name, role, emails, orgs, last-interaction) for EVERY person, internal or external. **team-intelligence** is a scoped extension over the direct-report subset: it never creates person records, and its commitment signal lives in `events.jsonl` `commitment` events (owner_id = the report), not in person-record fields. `_hq/views/PEOPLE.md` and the `_people/` PERSON.md profiles are Tier 2 projections — orientation reading, never writes, never state.

---

# Team Intelligence — Command Room

You are the CEO's private chief-of-staff layer for people. You maintain profiles on the leadership team and key people, track commitments, prepare 1:1 briefs, and give cross-project visibility into what each person is doing.

**This is NOT an HR system.** No performance reviews, no compensation, no org charts, no 360 feedback, no hiring. This is the CEO's private mental model of their people — externalized, searchable, and always current.

## What This Skill Does (v1 — hard boundary)

1. **Team roster & profiles** — maintain PERSON.md files in `_people/`
2. **1:1 prep** — generate a 60-second brief before any meeting with a team member
3. **Commitment tracking** — log what people committed to, surface delivered/overdue
4. **Cross-project person view** — aggregate a person's presence across all projects

**What it does NOT do:** Performance reviews. Compensation. Org charts. 360 feedback. Team analytics dashboards. Hiring or recruiting. Those may come later — they are out of scope now.

---

## Data Model

```
[WORKSPACE_ROOT]/
├── _people/                          # Team intelligence folder
│   ├── _team-config.md               # Roster list, prep format prefs, staleness rules
│   ├── aria.md                       # One PERSON.md per team member
│   ├── mira-coo.md                   # Filename: lowercase, hyphenated, disambiguated if needed
│   └── skyler.md
```

`_people/` lives at workspace root — same level as projects. It's an org-wide resource.

### PERSON.md Structure

Each file follows this structure (see references/person-template.md for the full template):

**Identity** — name, role, title, reports to, owns (projects/domains)

**Working Style** — communication preferences, notes from the CEO. The stuff a great chief of staff just knows. ("Aria needs the why before the what." "Sam is a morning person." "Mira prefers Slack over email.")

**Active Commitments** — what they've committed to, when, source (which meeting/email), status (open/delivered/overdue), due date. This is the accountability layer.

**Interaction Log** — last 10 key touchpoints with the CEO, auto-populated from meeting-notes and briefings, manually enrichable. Each entry: date, type (meeting/email/slack/note), summary (1 line), source reference.

**Flags** — anything the CEO wants surfaced next time this person comes up. Short-lived by design — flags should be resolved or removed within a few sessions.

**Cross-Project Presence** — which projects this person appears in (auto-populated by scanning PROJECT_BRAIN.md files). Not manually maintained.

### _team-config.md

Lightweight configuration:
- **Roster** — list of all tracked people + their PERSON.md filename
- **Prep format** — what the CEO wants in 1:1 prep briefs (default: commitments + recent interactions + flags + open items across projects)
- **Staleness rules** — how many days without interaction before flagging (default: 14 days)
- **Commitment overdue threshold** — how many days past due before escalating (default: 3 days)

### Settings verbs (SPEC OUT2 §5 — aliases onto this file, NOT a second store)

The standard FRP1 verbs map onto `_team-config.md` — storage unchanged, no `skill_config` JSON for
this skill, no migration:

| CEO says | Behavior |
|---|---|
| "tune team-intelligence" | walk the three preference blocks (prep format, staleness rule, overdue threshold) with the CURRENT `_team-config.md` values pre-filled → rewrite the file. The roster is NOT edited here — that stays "add [name] to my team" / "remove [name] from my team". |
| "show team-intelligence settings" | render the current `_team-config.md` preferences in plain English, read-only (roster count + the three preference values). If the file doesn't exist yet: state the defaults (14-day staleness, 3-day overdue, standard prep format) and offer the tune. |
| "reset team-intelligence to defaults" | reset the three preference blocks in `_team-config.md` to the defaults above. NEVER touches the roster or any PERSON.md — confirm that scope in the ack ("Preferences reset — your team roster is untouched."). |

These verbs are ALIASES into the existing config file — they exist so the whole composer family
answers the same tune/show/reset vocabulary. Do NOT create `_hq/data/skill_config/team-intelligence.json`.

---

## Commands

### "discover my team" / "set up my team" / "find my team"

Standalone team discovery — runs the same logic as onboarding Phase 3 but for existing workspaces. This is how users who upgraded (and skipped onboarding) get their team set up.

**Pre-check:** If `_people/` already exists and contains PERSON.md files, do NOT overwrite. Instead say: "You already have [X] people being tracked: [names]. Want to add more people, or re-scan to see if I'm missing anyone?" If re-scanning, present new candidates only — skip anyone who already has a profile.

If `_people/` doesn't exist or is empty, proceed with full discovery:

1. **Detect** — silently scan all available sources:
   - **Calendar** (if connected): Look for recurring 1:1 meetings. People on recurring meetings are almost certainly direct reports or key people. Extract names, frequency, meeting titles.
   - **Gmail** (if connected): Top contacts from last 30 days. Cross-reference with calendar contacts.
   - **Slack** (if connected): DM channels or frequent @mentions.
   - **Project brains** (always): Scan all PROJECT_BRAIN.md People tables. Extract names appearing across multiple projects.
   - **PEOPLE.md** (always): Check contact database for anyone with leadership-sounding roles.
   - If **no connectors are available** and local sources (brains + PEOPLE.md) return no candidates: say "I couldn't find team members automatically — no email or calendar is connected, and your project files don't have people listed yet. You can connect Gmail or Calendar to let me scan, or add people manually: **'add [name] to my team'**." Stop here.
2. **Present & confirm** — show what you found in a table:
   ```
   | Name | How I Found Them | Recurring Meeting? | Projects They Touch |
   ```
   Ask: "Are these the right people? Anyone missing? Anyone here I should NOT track?"
3. **Create profiles** — build `_people/` folder (if it doesn't exist), `_team-config.md` (if it doesn't exist), and a PERSON.md per confirmed member. Pre-populate from scan data (Identity, Interaction Log, Cross-Project Presence, Active Commitments). Leave Working Style and Flags empty.
4. **Explain** — "Say 'prep me for my 1:1 with [Name]' for a 60-second brief. Say 'my team' for an overview. Profiles update automatically from meetings and sessions."
5. **Quick working style capture** — for each person, ask one optional question: "Any working style notes I should know about [Name]?"

---

### "add [name] to my team" / "new team member [name]"

1. Ask: role, what they own, which projects they touch, any working style notes
2. Create `_people/[name].md` from template (references/person-template.md)
3. Scan existing PROJECT_BRAIN.md files for mentions of this person — pre-populate Cross-Project Presence
4. If Gmail/Calendar connected: search for recent interactions with this person — pre-populate Interaction Log with last 5 touchpoints
5. Add to _team-config.md roster
6. Confirm: "[Name] added. Found them in [X] projects and [Y] recent interactions."

### "prep me for my 1:1 with [name]" / "prep [name]" / "1:1 prep [name]"

**Output guard (PL.10):** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.

- ❌ "Derived from events.jsonl via load_open_commitments (owner_id filter)"
- ✅ "Here's what Aria owes you and what she's delivered, from everything I've tracked."

This is the flagship command. Output a brief the CEO reads in 60 seconds before walking into the room.

**Step 1: Load person context**
1. Read `_people/[name].md` for **static profile** (role, working style, communication preferences, flags) — orientation only per `references/SOURCE_OF_TRUTH.md`. The PERSON.md commitment table is a Tier 2 projection and MUST be overlaid with canonical state in Step 2.
2. Read `_team-config.md` for prep format preferences
3. Resolve `[name]` to a canonical `person_id` via `_hq/data/aliases.json` (or directly from `_hq/data/entities.json` people array) — needed for Step 2's events.jsonl filter.

**Step 2: Scan for current state (v3.11.5 — REQUIRED canonical-source overlay)**

4. Read all PROJECT_BRAIN.md files where this person appears — pull their active threads + recent contributions (durable narrative only). Do NOT read open items / commitments from the brain — those come from the canonical reader in Step 5 (the brain's commitment lines are pointers, not a live copy; deep-audit R-CODE-4 / `references/BRAIN_FILE_CONTRACT.md`). Person↔thread membership comes from the generated `<!-- LIVE-STATE:people -->` block, not a hand list.
5. **Commitment surface — derive from `_hq/data/events.jsonl` via the canonical reader, NOT from the PERSON.md commitment table.** Use:

   ```python
   import sys
   # Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1))
   sys.path.insert(0, "shared/scripts")
   from cru_match import load_open_commitments, split_pending_review, _commitment_field

   opens = load_open_commitments("<absolute path to _hq/data/events.jsonl>")
   confirmed, _needs_review = split_pending_review(opens)   # INTAKE — confirmed half only
   theirs = [c for c in confirmed if _commitment_field(c, "owner_id") == "<person_id>"]
   ```

   **`data.pending_review` is NOT true — those are UNCONFIRMED extractions, not open commitments (INTAKE).** `load_open_commitments` is deliberately unfiltered, so the raw result still carries them; `split_pending_review` is the seam every reader that RENDERS goes through. Walking into a 1:1 and telling someone what they owe you, off a promise the extractor guessed at, is the one failure this brief cannot survive. Unconfirmed items belong to the needs-your-call queue (`needs your call`) — at most ONE labelled pointer line here, never rows in the brief.

   `load_open_commitments` handles all 5 commitment shape variants and treats both `commitment_resolved` AND `thread_resolved` as closers, so commitments closed via the meeting-notes Step 5e-bis path, the log-resolution dashboard click, or the follow-up-ritual CRU layer are correctly filtered out. The PERSON.md commitment table can lag — regen happens during workspace-manager's end-of-session passes, NOT in real time. Reading the table directly was the v3.11.5 _people/ drift bug.

6. For each `theirs` entry, compute "overdue" / "due today" / "due in N days" against TODAY in workspace TZ via `shared/scripts/tz.py to_local(value, workspace_path=<WORKSPACE>)` (v3.11.3+ contract — workspace_path REQUIRED).
7. Check flags from PERSON.md (the `Flags` section is fine to read directly — it's static profile data, not state).

**Step 3: Check connected sources for fresh intel**
8. **Gmail** (if connected): Last 5 emails to/from this person since last interaction logged
9. **Calendar** (if connected): Any upcoming meetings with this person beyond this 1:1
10. **Slack** (if connected): Recent messages from/mentioning this person
11. **Transcript connector** (if connected — via `discover_transcript_tool()`): Any unprocessed meeting transcripts involving this person

**Step 4: Build the brief — ONE generator (SPEC OUT1 §4 / S1 contract)**

The 1:1 brief does NOT hand-roll its own layout. It calls the SAME prep generator call-prep uses — `shared/scripts/prep_pipeline.py::assemble_prep_sections` — with the direct report as the counterparty. That gives the stat-tile band, relationship timeline, and both-directions owed table for free, on the identical section machinery as an external call-prep (acceptance #4: diff the section lists — they share the generator). The team-specific sections (Fresh intel, Flags, Across projects) ride along as `extra_sections`; they never fork a second layout.

```python
import sys
sys.path.insert(0, "shared/scripts")
from prep_pipeline import (
    assemble_prep_sections, build_prep_tiles,
    build_relationship_timeline, build_owed_table,
)

tiles = build_prep_tiles(
    days_since_last_touch=<days since last logged interaction, or None>,
    you_owe=<count you owe them, or None>,
    they_owe=<count of their open commitments, or None>,
    oldest_owed_days=<age of the oldest owed item, or None>,
    touch_number=None,                 # 1:1s are recurring; touch # is optional
)
timeline = build_relationship_timeline(<prior 1:1s + key interactions as {date,label}>)
owed = build_owed_table(<their + your matched commitment rows — CONFIRMED half only,
                         split_pending_review(...) per Step 2; INTAKE2>,
                        user_person_id="<your person_id>", now_date="<today ISO>")

assembled = assemble_prep_sections(
    walk_out_with="Walk out with: <the one concrete outcome — e.g. clear status on N open items + the single blocker to unblock>",
    meeting_details="1:1 with <Name> · <Date>",
    tiles=tiles,
    timeline=timeline,
    owed_table=owed,
    changed_lines=[<what's happened since the last logged interaction>],
    talking_points=[<each line ENDS with a source cite — '(commitment, due Jul 7)', '(email, Jul 5)'>],
    extra_sections=[
        {"heading": "Across Projects", "bullets": [<Project: their role/status in one line>]},
        {"heading": "Fresh Intel", "bullets": [<email/Slack/calendar not yet in the profile>]},
        {"heading": "Flags", "bullets": [<anything the CEO flagged>]},   # omit the section entirely if none
    ],
)
```

**Contract note:** `assemble_prep_sections` REQUIRES `walk_out_with` and enforces that every talking point carries a source cite — an unsourced line raises `PrepContractError` before any file is written (rewrite the flagged lines and re-assemble). Drop-empty is automatic: no owed items → no owed table; <2 timeline points → no timeline; an empty `extra_sections` bullet list → that section is omitted (never an empty frame).

Render through the shared writer (SAME `brief_kind` as call-prep, so the eyebrow + gate path match):

```python
from brief_writer import make_brief
from brief_path import get_brief_path

path = get_brief_path(workspace_root, "call_prep", "1-1 <name>", "<today ISO>")
make_brief(
    path,
    brief_kind="call_prep",
    title="1:1 Prep — <Name>",
    subtitle="<Date>",
    sections=assembled["sections"],
    exec_header=assembled["exec_header"],
    workspace_root=workspace_root,
)
```

Save to `_hq/meetings/` (CONTRACT Rule 27 — never .md), and surface it as the H2 heading link at the bottom of the chat turn per CONTRACT Rule 3 (`doc_headline_link` + `get_brief_artifact_url`).

**Same generator means same fence (DOCFENCE1).** This brief is `brief_kind="call_prep"` landing in `_hq/meetings/` — the identical kind and folder call-prep fences, so it inherits the identical bans:

- **NEVER hand-roll the 1:1 brief** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or PII-leaking brief (the v3.20.0 failure mode) — and a 1:1 brief is the most person-dense document this system writes.
- **NEVER create, render, copy, upload, or update the brief — or any part, derivative, or restatement of it ("talking points", "an agenda", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/meetings/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so I can share it with the report", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the 1:1 in a Google Doc" is a request this gate refuses, not an override. Say the canonical brief already exists and hand back its link. This brief is prep FOR the manager — it is not a document to hand the direct report through a shared connector.

### "what's [name] working on?" / "status on [name]" / "how's [name] doing?"

Cross-project person view. Reads the same data as 1:1 prep but presents it differently — status-focused, not meeting-focused. **Per `references/SOURCE_OF_TRUTH.md` (v3.11.5+), commitment state derives from `_hq/data/events.jsonl` via `load_open_commitments` filtered by `owner_id == <person_id>`, NOT from the PERSON.md commitment table** — the table is a Tier 2 projection that lags. **Confirmed half only — `cru_match.split_pending_review(opens)[0]` (INTAKE).** Same seam as 1:1 prep Step 2: an unconfirmed extraction is not something this person is working on, and answering "what's she working on?" with a guess is worse than answering with less.

1. Read `_people/[name].md` for static profile only (role, owns, working style, flags)
2. Scan all PROJECT_BRAIN.md files for this person
3. Derive open commitments + overdue counts from events.jsonl via the canonical reader (same overlay procedure as 1:1 prep Step 2)
4. Present: what projects they're active in, what they own in each, what's open, what's overdue
5. End with: "Want me to prep for a 1:1, or flag something for next time you talk?"

### "my team" / "team status" / "team overview"

Aggregate view across all tracked people.

1. Read `_team-config.md` for roster. **If `_team-config.md` doesn't exist:** check if `_people/` has any PERSON.md files. If yes, create `_team-config.md` with defaults and build roster from existing profiles. If `_people/` doesn't exist or is empty, say: "You haven't set up team tracking yet. Say **'discover my team'** to scan your tools and find your people, or **'add [name] to my team'** to start one by one."
2. For each person in roster: read PERSON.md for **static profile only** (skip with a warning if file is missing). Per `references/SOURCE_OF_TRUTH.md` (v3.11.5+), derive open / overdue commitment counts from `_hq/data/events.jsonl` via `load_open_commitments` grouped by `owner_id` — **confirmed half only, `cru_match.split_pending_review(opens)[0]` (INTAKE); the "Open Commitments" and "Overdue" columns below carry no unconfirmed extractions** — one pass over events.jsonl is cheaper than reading every PERSON.md table and stays consistent with what morning-brief / Pulse / Commitments dashboard show. Same source for `last interaction date` — max ts of `interaction` events scoped to the person's `person_id`.
3. Present summary table:

```
| Name | Role | Last Interaction | Open Commitments | Overdue | Flag |
|------|------|-----------------|------------------|---------|------|
```

4. Flag anyone past staleness threshold (no interaction in X days)
   - **REL1 — emit the normalized dormancy signal.** For each member flagged past the staleness threshold, call `shared/scripts/dormancy.py::emit_dormancy_signal(workspace_root, entity_id=<person_id>, entity_type='person', gap_days=<days since interaction>, baseline_days=None, source_skill='team-intelligence')` (absolute 14-day tier). The team-overview flag is unchanged. **BAL1 D1.1(3) — personal-tie skip at this emit gate:** never call `emit_dormancy_signal` for a person whose record carries `tie: "personal"` — personal ties belong to the Balance surface only, and a personal signal in the substrate flows straight into the work-outreach pack. Absent `tie` = work (back-compat); only the explicit `personal` value skips.
5. Flag anyone with overdue commitments
6. End with: "Want to prep for any upcoming 1:1s?"

### "[name] committed to [thing]" / "log commitment for [name]"

Manual commitment logging. Used when the CEO hears a commitment outside of a processed meeting.

1. Confirm: person, commitment, due date (ask if not provided), source (meeting/call/email/verbal).
2. Resolve `[name]` to canonical `person_id` via `_hq/data/aliases.json`.
3. **Canonical write — append a `commitment` event to `_hq/data/events.jsonl` (v3.11.5+ — REQUIRED per `references/SOURCE_OF_TRUTH.md`).** This is the source-of-truth write. Pre-v3.11.5 the skill only appended to the PERSON.md Active Commitments table, which meant team-intelligence-logged commitments were INVISIBLE to `load_open_commitments`, morning-brief Step 3b counts, Pulse, the Commitments daily chat, and every other canonical consumer — a parallel-universe commitment store. Use the canonical shape from `shared/COMMITMENT_SCHEMA.md`:

   ```json
   {
     "seq": <reserved by writer helper>,
     "ts": "<ISO 8601 — when the commitment was MADE>",
     "type": "commitment",
     "source_skill": "team-intelligence",
     "primary_thread_id": "<thread the work belongs to, if known; else null>",
     "person_ids": ["<owner_person_id>"],
     "classification_confidence": 1.0,
     "data": {
       "owner_id": "<person_id>",
       "title": "<short verb-phrase>",
       "due": "YYYY-MM-DD",
       "status": "open",
       "source_ref": "team-intelligence:manual:<ISO ts>"
     }
   }
   ```

   Append via `shared/scripts/atomic_write.py::atomic_append_jsonl`. Status is `"overdue"` if `due` parses to a past date in workspace TZ.

4. **Companion write to PERSON.md** — append the same commitment to the person's Active Commitments table for human-readable continuity. This is the Tier 2 projection — kept in sync at write time, regenerated by workspace-manager passes when it drifts. Display only; never read for state decisions.
5. Confirm: "Logged. I'll flag this if it's not delivered by [date]."

### "who owns [thing]?" / "who's responsible for [thing]?"

Search across canonical source first, then projections for static-profile signal.

1. **Canonical search:** scan `_hq/data/events.jsonl` for `commitment` events whose `data.title` matches `[thing]` (use unigram-overlap scoring per `cru_match.py`). For each match, the `owner_id` is the candidate. Filter to OPEN via `load_open_commitments`, then keep the confirmed half via `cru_match.split_pending_review(...)` (INTAKE) — closed commitments shouldn't show as live ownership, and neither should unconfirmed extractions: nobody has agreed that item is theirs, so it cannot answer "who owns this".
2. **Static-profile search:** scan `_people/` PERSON.md files for `[thing]` in their "owns" field (Tier 2 profile data — fine for static ownership claims like "Aria owns vendor relationships"). Per `references/SOURCE_OF_TRUTH.md`, this is name-and-claim lookup, not state.
3. Scan PROJECT_BRAIN.md files for `[thing]` in active threads.
4. Present: who owns it, in which project context, current status. If multiple candidates, surface all with their evidence (canonical commitment vs static-profile claim).
5. If no clear owner: "I don't see a clear owner for [thing]. Want to assign it?"

---

## How This Skill Gets Fed (Integration Contracts)

This skill does NOT have its own data collection pipeline. It piggybacks on existing flows.

### Fed by: workspace-manager ("what's going on")

During the briefing, workspace-manager already scans Gmail, Calendar, Slack, Granola. **After building the briefing**, it writes person file updates:

- For each team member found in email/calendar/Slack activity: append to their Interaction Log
- For any commitments surfaced in emails: check against existing commitments, flag if new
- Update last-interaction dates

**The contract:** workspace-manager reads `_team-config.md` to get the roster, then writes to individual PERSON.md files. One-directional. Team skill never writes to workspace-manager's files.

### Fed by: workspace-manager ("end session")

During end session, workspace-manager already updates project brains. **After brain updates**, it writes person file updates:

- For each team member mentioned this session: update Interaction Log
- For any commitments made/delivered this session: update commitment table
- Refresh Cross-Project Presence by scanning brains

### Fed by: meeting-notes (after processing a transcript)

Meeting-notes already extracts attendees, action items, and decisions. **After routing to project**, it writes person file updates:

- For each attendee who matches the team roster: append to Interaction Log (date, "meeting", summary, link to session notes)
- For each action item assigned to a team member: add to Active Commitments with due date and source
- If relationship dynamics were noted: append to Working Style notes (only if CEO-relevant)

**The contract:** meeting-notes reads `_team-config.md` to check if attendees are team members. If yes, it writes to their PERSON.md files. Meeting-notes does NOT need to understand the team skill's logic — it just writes to the person files.

### Fed by: CEO (manual)

The CEO can always directly update person files:
- "Add a flag for Aria: ask about the vendor situation"
- "Aria delivered the Aspen Project numbers"
- "Update Mira's working style: she prefers async communication"

---

## Staleness & Maintenance

### Built-in checks (run during "team status" and during cleanup):

- **Interaction staleness:** No logged interaction in X days (default 14) → flag in team overview
- **Commitment staleness:** Overdue by X days (default 3) → flag in team overview AND in 1:1 prep
- **Profile staleness:** No updates of any kind in 30+ days → suggest reviewing with CEO

### File size management:

- Interaction Log: keep last 10 entries. When adding #11, archive the oldest to a `## Previous Interactions` section at the bottom (collapsed, not deleted).
- Commitments: delivered commitments move to a `## Completed` section after 7 days. Overdue commitments stay visible until resolved.
- Flags: prompt CEO to clear flags older than 14 days.

---

## Graceful Degradation

| Condition | Behavior |
|-----------|----------|
| `_people/` doesn't exist | Create it on first "add to team" or during onboarding Phase 3 |
| PERSON.md missing for someone mentioned | Offer to create: "I don't have a profile for [name] yet. Want me to add them?" |
| No connectors available | Skip fresh intel in prep; work from existing profile data only |
| `_team-config.md` missing | Use defaults (14-day staleness, 3-day overdue, standard prep format) |
| Person mentioned in meeting but not on roster | After meeting-notes processes: "I noticed [name] was in this meeting but isn't on your team roster. Want to add them?" |

---

## Gotchas

- **Don't confuse _hq/PEOPLE.md with _people/.** PEOPLE.md is the general contact database (everyone the CEO has ever interacted with). `_people/` is the inner circle — direct reports and key leadership. A person can be in both, but PEOPLE.md is maintained by meeting-notes and workspace-manager; `_people/` is maintained by this skill.
- **Never auto-add someone to the team roster.** Always ask the CEO. Meeting-notes can suggest, but the CEO confirms.
- **Commitment tracking is private.** This is the CEO's view. The people being tracked don't see these files. Don't generate content that reads like it should be shared with the person.
- **Interaction Log is for CEO-relevant touchpoints, not every email.** An email saying "meeting moved to 3pm" doesn't get logged. An email saying "the Aspen Project deal is falling through" does. Use judgment.
- **Flags are short-lived by design.** If a flag sits for 3+ sessions, prompt the CEO: "You've had this flag on [name] for a while — still relevant?"
- **Cross-Project Presence is read-only.** This section is auto-generated from scanning brains. The CEO doesn't edit it — it reflects reality.

---

## What It Doesn't Do

- Does not handle external relationships (board, investors, customers, vendors) — that's `people-crm`.
- Not an HR system — no performance reviews, compensation decisions, 360s, or hiring workflows.
- Does not auto-add team members to `_people/` — always confirms with the CEO first.
- Does not share team data externally — all team intelligence is the CEO's private view.
- Does not replace 1:1s — prepares briefs for them and tracks commitments between them.
- Does not generate coaching plans or development paths — surfaces patterns; the CEO decides.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Never walk into a 1:1 cold again. Owns the leadership-team layer — direct report profiles, what they're working on, their open commitments, and pattern detection (who's overloaded, who's drifting, who has three overdue asks). Use when the CEO says 'my team', 'team status', 'prep for 1:1', 'prep for 1 1', 'prep for my 1:1', 'prep for my 1 1', 'prep me for my 1:1', 'prep me for my 1 1', 'who owns', 'who owns the', 'log commitment for', 'log commitment for [name]', 'discover my team', 'who's overloaded', 'what has [name] delivered', 'delivered this quarter'. Produces 1:1 briefs, weekly team rollups, and commitment tracking. This is the CEO's private chief-of-staff layer for people — NOT an HR system. DOES NOT fire on 'prep me for the Acme call' / 'prep me for my 2pm' — external-attendee meeting prep (call-prep; this skill owns DIRECT-REPORT 1:1s only), bare 'log a commitment' with no direct report named (workspace-manager), 'who is' ('who is Mira' — people-crm), 'who hasn't replied' (dormant-customer-scan), 'who competes with' (research — no competitive-intel skill ships in this plugin), or 'hire' / compensation / performance reviews (out of scope).

> Also handles team-tracking settings (SPEC OUT2 §5 — aliases onto `_team-config.md`; storage unchanged, never a second store) — use when the CEO says 'tune team-intelligence', 'show team-intelligence settings', 'reset team-intelligence to defaults'. (These verbs live here rather than in the description because the description budget is capped — G11; the runtime router and the trigger tests read the description and this Routing corpus together.)
