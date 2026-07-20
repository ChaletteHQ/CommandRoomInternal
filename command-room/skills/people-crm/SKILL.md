---
name: people-crm
description: "Never walk into a meeting or dinner wondering who-is-this-again. The relationship memory: who someone is, how you know them, what you last discussed, what's open between you. Fires on: 'who is [name]', 'tell me about [name]', 'who do I know at [company]', 'what did [name] and I last discuss', 'prep me for dinner with [name]', 'add [name] to my contacts', 'quick, who is [name] again'. Owns person facts: 'remember [fact] about [name]' ('remember Sam prefers Signal'), 'note that [name] [fact]' — appends a sourced fact to their history. Builds and reads per-person records from email, meetings, and notes. Does NOT fire on 'prep me for my 2pm' (call-prep — the meeting brief), 'prep for 1:1 with [direct report]' (team-intelligence), 'who should I reach out to' (relationship-moves), or 'model [name] as an advisor' (advisor-export). Record shape and resolution rules: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use people-crm for:** maintaining and querying the relationship layer — person records, last-interaction dates, notes, project + org connections.
- **Use `team-intelligence` for:** direct reports and leadership team specifically — extends people-crm with commitment tracking, 1:1 prep, and team-wide cadence.
- **Use `call-prep` for:** meeting-specific context that reads from people-crm.
- **Org-tree aware:** person records carry `org_ids[]` (an array — people can belong to multiple orgs) and `primary_org_id` (the most specific operating org for default context). Email domain, Slack workspace, and calendar attendee signals feed org inference (reactive discovery state lives in `_hq/ORG_DISCOVERY_SKIP.md` / `_hq/ORG_DISCOVERY_QUEUE.md`).

## Writer Contract

- **Primary writer for:** person records in `_hq/data/entities.json` (canonical ownership).
- **Canonical schema:** `shared/data-schemas/entities.schema.json` `$defs.person`. Required: `id` (`person_NNN`), `canonical_name`, `first_seen` (ISO date). Optional: `aliases[]`, `role`, `primary_org_id`, `affiliation_ids[]`, `email` (singular), `project_ids[]`, `last_interaction`, `notes`, `communication_style`, `reports_to_id`, `status`, `tie` (`work`/`personal` — SPEC BAL1; absent = work), `cadence_days` (BAL1 personal re-surface interval, read only by the Balance surface). **No other keys.**
- **Forbidden hand-rolled keys** (observed in wild, blocked by validator): `display_name` (use `canonical_name`), `name` (use `canonical_name`), `normalized_name` (remove), `emails` plural (use `email`), `current_org_id` (use `primary_org_id`), `org_ids` (use `affiliation_ids`), `first_seen_at` (use `first_seen`, date only), `last_seen` (use `last_interaction`), `last_interaction_at` (use `last_interaction`), `first_seen_source` / `confidence` / `inferred_from` (record in `events.jsonl`, not on the entity), `role_at_primary_org` (use `role`), `thread_associations` (use `project_ids`), `pending_review` / `enriched_at` / `enriched_from` / `low_signal` (gate via `events.jsonl`).
- **Writer helper (v3.2+ MANDATORY):** ALL person creates / updates / merges / repairs go through `shared/scripts/people_writer.py`. Never hand-roll JSON for a person record. The helper validates against the schema, dedups against existing records, atomic-writes via `atomic_write_json`, and logs `person_created` / `person_updated` / `person_merged` / `person_repaired` events. Direct edits to `entities.json["people"]` are FORBIDDEN — they recur the v3.0/v3.1 bug class where the agent invented different shapes on different fires (`person_063` Rio Sample, `person_064` Dustin Sample duplicate of `person_004`).
- **Dedup before create:** `find_existing_person(workspace_root, name=..., email=..., aliases=...)` is REQUIRED before `create_person`. Match order: email exact (case-insensitive) → alias case-insensitive → `canonical_name` whitespace-normalized. Existing record found → call `update_person(existing_id, ...)` instead, never create a parallel record.
- **Regenerates:** `_hq/views/PEOPLE.md` and the backward-compat `_hq/PEOPLE.md` after every write.
- **Appends to:** `_hq/data/aliases.json` when new person aliases are confirmed.
- **Does not write to:** `events.jsonl` directly in most flows (the writer helper logs the entity-event; passive-capture handles interaction events), `classifier_feedback.jsonl`. **EXCEPTION (v2.3):** the New Person Enrichment Pipeline appends `interaction`, `meeting`, and `note` events during initial backfill — this is the ONE declared write path to `events.jsonl`, scoped to new `person_*` records flagged for review via `events.jsonl`, gated by a 14-day `enriched_at` cooldown event, dedup'd via `source_ref` hash. Note: enrichment-state flags live in events.jsonl, NOT as fields on the person entity.
- **Consumes from passive capture:** every inbound/outbound interaction event (v2.2 shape with `primary_thread_id` + `related_thread_ids[]` + `org_ids[]`) updates the relevant person's `last_interaction` date and associations via `update_person`. Associations inherit classification confidence — provisional and low-confidence events do not promote a project/org onto a person record until confirmed via `insight-generator` Pass 8.
- **Conflict boundary:** team-intelligence shares the `person` entity type but scopes writes to `reports_to_id = CEO` records. No two skills write the same person record concurrently — people-crm is canonical owner, team-intelligence extends with commitment tracking (separate field namespace). Both go through `people_writer.py`.
- **Atomic-write enforcement (v2.10.5+):** `people_writer.py` calls `atomic_write_json` internally — callers must NOT bypass to direct `path.write_text()` / `open(path, "w")`. Direct file writes have produced truncated-file incidents in v2.7-v2.10.4 and shape-drift incidents in v3.0-v3.1.
- **Account-scope on connector-derived records (connector-agnostic-v1, ACCOUNT_SCOPE §2):** when a person/org record is derived from a CONNECTOR READ (a sender on triaged mail, a meeting attendee from a transcript), pass the read's provenance to the writer — `create_person(..., provenance=<the read's provenance dict>)` or `account_address=<the mailbox it arrived through>` (NOT the contact's own email). The record wall (`account_scope_gate.enforce_record_scope`) rejects an out-of-scope account's contact before the entities.json write. A manual add ("add Bob to my contacts") passes no provenance kwargs and is never walled.
- **Promote-queue confirm/demote (R8, ACCOUNT_SCOPE §8):** inbox-triage writes `person_proposal` events (`data.promote_queue: true`) for mixed-account senders not in the entity graph. When the user CONFIRMS ("file it" / promotes the proposal), create the person as a **user-confirmed add — NO provenance kwargs** (the user is the authority; the record wall is for unconfirmed connector derivations); future mail from that sender is then in scope by association (the wall passes events referencing resolved entities on mixed accounts). Append a `person_proposal_resolved` event pointing at the proposal. When the user DEMOTES ("keep personal" / "this is actually personal"), do NOT create a record; write the teaching signal via `connector_config.set_sender_scope_override(root, <account>, <sender>, write_to_business=False, reason="user demoted")` so the proposal never re-fires. The write dial stays fail-closed throughout — a classification error hides business mail (safe), never pollutes records (H-G).

### Bash gate — pre-flight import check (v3.2+ MANDATORY)

Before any person-write step in this skill or its callers, execute:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from people_writer import create_person, update_person, find_existing_person, merge_person_into, DuplicatePersonError; print('OK')"
```

If stdout is not exactly `OK`, ABORT and surface plain English: `(I couldn't save that contact just now — I'll flag it and retry next time.)` Log the diagnostic detail to `_hq/CONFLICTS.md`, never into chat. Do NOT fall back to direct `entities.json` edits.

### Writer call shape

```python
import sys
sys.path.insert(0, 'shared/scripts')
from people_writer import (
    create_person, update_person, find_existing_person,
    merge_person_into, DuplicatePersonError,
)

# 1. Always dedup first.
existing = find_existing_person(
    workspace_root,
    name="Rio Sample",
    email=None,
    aliases=["Rio N"],
)

# 2. Branch on result.
if existing:
    update_person(workspace_root, existing["id"],
                  last_interaction="2026-04-30",
                  source_skill="people-crm")
else:
    try:
        create_person(
            workspace_root,
            canonical_name="Rio Sample",
            primary_org_id="org_005",
            role="Project Manager",
            aliases=["Rio N"],
            notes="Project manager at Summit Company.",
            first_seen="2026-04-30",
            source_skill="people-crm",
        )
    except DuplicatePersonError as e:
        # The dedup helper missed a match the writer caught. Surface the
        # existing id to the user instead of creating a parallel record.
        # (e.g., if the user's input matched an alias the caller didn't
        # pass into find_existing_person.)
        ...
```

CLI form for bash callers is documented in the docstring of `people_writer.py`.

---

# People CRM

**For:** CEOs who need instant context on the people they work with — past conversations, shared projects, and next steps.

## What It Does

**Person-record ownership (canonical paragraph — IDENTICAL in people-crm and team-intelligence; edit both or neither):** `_hq/data/entities.json` person records are the ONE canonical person store. **people-crm** owns record lifecycle and core fields (create, name, role, emails, orgs, last-interaction) for EVERY person, internal or external. **team-intelligence** is a scoped extension over the direct-report subset: it never creates person records, and its commitment signal lives in `events.jsonl` `commitment` events (owner_id = the report), not in person-record fields. `_hq/views/PEOPLE.md` and the `_people/` PERSON.md profiles are Tier 2 projections — orientation reading, never writes, never state.

Maintains the relationship layer in the person records of `_hq/data/entities.json` — `_hq/views/PEOPLE.md` is the human-readable view regenerated after every write, never the store. Every person you interact with gets a record so you can instantly answer "who is this person?" or "what did we last discuss?"

The records auto-update from:
- Meeting notes (new attendees get added automatically)
- Email threads (Gmail connections pull sender/recipient data)
- Manual entry when you meet someone new

Tracks what matters: company, role, how you know them, projects they're connected to, last interaction date, key notes, and contact info.

## How to Use

### MUST-language enforcement gates (v3.13.7+ — canonical reader dispatch)

People-CRM has TWO canonical helpers that the read paths MUST invoke. Bypassing either was flagged in Session-22 testing (Bugs #11 + #23) as the root cause of "queries work by luck of grep" behavior.

> **Gate 1 — Name resolution.** Before answering any "who is X" / "tell me about X" / "people at Y" / "prep me for [person]" query, you MUST invoke `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` FIRST to resolve the name. Only after the resolver returns NO candidates may you fall back to substring grep on entities.json. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the ladder, tiers, and fallback rules.
>
> **Gate 2 — Commitment surface.** Before surfacing ANY commitment-related state for a person ("what's owed to them," "what they owe me," "what's open with them"), you MUST invoke `shared/scripts/cru_match.py::load_open_commitments(events_jsonl_path)` and filter the resulting list by the resolved `person_id`. Raw grep on events.jsonl is NOT acceptable — it doesn't apply closure-event suppression (commitments closed via `commitment_resolved` / `thread_resolved` / `commitment_superseded`) and produces stale or inflated commitment surfaces.

Why both are required: implementation exists; SKILL.md previously referenced them; runtime traces (Session 22, Phases 2D + 2G) showed the LLM substituted raw grep under time pressure. Output looked plausible because M's mature alias graph happened to make substring grep accurate enough. New customers with empty graphs hit the worst case.

If you find yourself about to `grep` for a name OR `grep` for commitment events BEFORE calling the canonical helpers, stop. That's the exact bypass these gates exist to block.

**Know Someone**

```
"Who is [Person]?"
"Tell me about [Person]"
"What do I know about [Person]?"
"[Person] context"
```

You get their full profile: company, role, how you know them, projects, last interaction, and key notes.

**Prep for Personal Connection**

```
"Prep me for dinner with [Person]"
"Get me ready to call [Person]"
"Context for meeting with [Person]"
"What should I know before talking to [Person]?"
```

You get a relationship brief: last interaction, what you discussed, open items, things to mention, shared projects.

**Explore Your Network**

```
"People at [Company]"
"Who do I know at [Company Name]?"
"My contacts at [Company]"
"How many people do I know there?"
"People across all [Holding Name] operating companies"
```

See everyone in your network from that org and their context. If the query names a holding, the skill walks the org tree and returns people across all child operating companies (clearly grouped by which child they belong to).

**Relationship Health**

```
"Who haven't I talked to in a while?"
"Relationships going cold"
"Last interactions"
"People I should catch up with"
```

Surfaces relationships that are stale so you can prioritize reconnection.

**Add & Update**

```
"Add [Person] to my network — works at [Company] as [Role], met at [event/introduction]"
"Update [Person]'s role — now [new role]"
"Log interaction with [Person] — discussed [topic]"
```

Manually add new contacts or update existing ones.

### Add-person elicit path (v4.8.1 — F13; extends the Bug #19 no-silent-create fix)

When an add trigger arrives **sparse** — a name with no substance to store
("add a new person: Quinn", "add Quinn to my contacts" with no org / role /
email / context) — do NOT create, and do NOT render a bare elicit form either.
The order is fixed:

1. **Never silent-create.** Unchanged (Bug #19). A sparse add always goes
   through an elicit step before any write.
2. **Dedup BEFORE the form renders — both helpers, in code:**
   `people_writer.find_existing_person(workspace_root, name=<input>)`
   (catch `MultipleCandidatesError` — its `.candidates` are matches, not an
   error condition) **and** `people_writer.list_same_name_people(workspace_root,
   <input>)` for the token-level same-first-name list that
   `find_existing_person`'s exact tiers cannot see.
3. **Name the matches in the form header.** If either call surfaced records,
   the elicit form's header MUST list them by canonical name with one-line
   context (org / role when present) and offer them as pick-existing choices
   alongside the create fields: *"You already have Quinn Sample (Acme Co)
   and Quinn Stone (Northstar Partners) — one of them? If it's someone new,
   add a detail below so the new Quinn doesn't collide."* Prospectively
   acknowledging collision risk WITHOUT naming the existing people is the
   exact F13 failure — the CEO can't disambiguate against a list they can't
   see.
4. **Pick-existing routes to update, never create.** Selecting a listed match
   becomes `update_person` on that record; only an explicit "someone new" +
   at least one distinguishing detail proceeds to `create_person` (whose own
   dedup remains the final gate, unchanged).

Zero matches → render the plain elicit form (name pre-filled, ask for org /
role / email / how-you-met) — no invented "possible duplicates" line.

Same-first-name examples above use the approved placeholder people
(`references/PRIVACY_POLICY.md`) — never real contacts.

### Auto-add path (RICH context — FS-11, M ruling 2026-07-15)

When a person surfaces with **substance already attached** — a named attendee
in a processed meeting, a sender on a triaged thread, a person named with role
+ org in a source the CEO is acting on — auto-add them (M: "yes, add people
with rich context") through `people_writer.auto_add_person`, NOT the sparse
elicit form. That helper enforces the two guardrails so auto-creation stays
safe:

1. **Same-name dedup gate runs BEFORE every auto-add.** `auto_add_person`
   calls `list_same_name_people` internally and returns a
   `needs_confirm` result (with `matches`) when any existing person
   shares a name token — auto-add DOWNGRADES to the confirm/elicit path in that
   case (never silently forks a duplicate). Only a zero-match name auto-creates.
2. **Capture the email — but only from an OBSERVED source (F-08 extends to
   capture).** Pass the address AND its `email_provenance` (the message /
   meeting it was observed in). An address you cannot trace to an observed
   source — a domain-pattern guess, a coworker's shape, "most likely" — is
   NEVER stored: pass no email (or expect `email_dropped_no_provenance=True`).
   The same ban that governs sending (F-08) governs what lands on the record.
3. **Undo = archive, never delete.** The auto-add narrates in the change feed
   with an `undo`; undo sets the record `status: "archived"` via
   `update_person` (the R1 archive-never-delete reverser `brain_undo`
   registers), never a hard delete — history is preserved.

```python
from people_writer import auto_add_person
res = auto_add_person(ws, canonical_name="Quinn Sample", email="quinn@example.com",
                      email_provenance={"source": "meeting", "meeting_id": "..."},
                      role="VP Ops", primary_org_id="org_...")
# res["status"] == "added"  → narrate "Added Quinn Sample — say `undo` to remove."
# res["status"] == "needs_confirm" → surface res["matches"], ask before creating.
```

### Person facts — `remember [fact] about [name]` / `note that [name] [fact]` (SPEC HIST1 D8)

An explicit user statement of one atomic fact about a person ("remember Sam prefers Signal", "note that Sam Sample prefers morning meetings"). The user is the authority — no proposal, no confirm card. Facts are ADDITIVE, SOURCED events; they never touch the person record (no notes-blob append, no new record field — the history renderer compiles them on read).

1. **ENTITY_RESOLVE first — every name-bearing form.** `entity_resolve.resolve_all(workspace_root, <name>)` per Gate 1 above. A lone-first-name hit that is Tier-3 ambiguous gets the disambiguation widget, never a first-pick (Bug #19); `record_person_fact` is never called on an unresolved id. A name that resolves to a tracked ORG is workspace-manager's org-fact handler — hand it over.
2. **Write through the ONE fact writer** (Rule 22 discovery preamble required, then):

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys; sys.path.insert(0, 'shared/scripts')
from people_writer import record_person_fact
record_person_fact('<workspace_root>', '<person_id>', 'Prefers Signal over email', 'chat:user-statement', category='preference', source_skill='people-crm')
print('OK')
"
```

`category` is optional — one of preference / contact / personal / role / company_news / other when it's clear; omit when it isn't. `source_ref` names where the fact came from (`chat:user-statement` for a direct statement; a message/meeting ref when the user is reacting to one). 3. **Ack in one line** ("Noted — Sam Sample prefers Signal. It'll show in his history and call prep."). The fact appears in the person's history view (`go [person]`) and in call-prep's relationship context on the next render.

**Fences:** a role/company CHANGE ("Sam is now CRO at Acme Co") is an `update_person` field change (proposal-gated per the Writer Contract) — the lineage trail is emitted automatically by the writer; do NOT also record it as a fact. Prose-INFERRED facts (a transcript "sounds like…") are never written directly — they ride the confirm rail as proposals (`entity_signal_detector.run_entity_signal_scan` writes them; never hand-write one).

**Structured auto-noting (HIST1 Part 2 — the ONE nuance to "enrichment doesn't auto-save"):** when the cross-source enrichment scan surfaces an atomic NON-identity fact from a STRUCTURED connector field (a signature block's "Prefers Signal" line, a calendar location field — never model inference over prose), it may be auto-noted as an additive fact EVENT via `entity_signal_detector.apply_structured_facts` — the person RECORD still never auto-updates, `role`/`company_news` facts still demote to confirm (S2), every auto batch is one-`undo` reversible, and the morning brief's CHANGED line narrates the count. Surface the returned `undo_line` in chat when anything was noted. Everything else in the "Fresh from your tools:" section stays exactly as documented below: shown, not saved, until the user decides.

### Personal ties — "[name] is my wife/husband/partner/mom/dad/kid" (SPEC BAL1 D1)

A family/personal relationship statement is a FIELD change, not a fact: ENTITY_RESOLVE the name, then `update_person(workspace_root, <person_id>, source_skill='people-crm', tie='personal', role=<the stated relationship, e.g. 'Wife'>)`. The `tie: "personal"` marker moves the person into the Balance surface's lane and OUT of every work surface: Pulse Phase 3 skips them, relationship-moves drops them, the dormancy emitters skip them. Never infer the tie from a transcript — only an explicit user statement sets it (an inferred family relationship rides the confirm rail like any proposal). The reverse ("actually [name] is a client contact") sets `tie='work'`. A cadence statement — "set date-night cadence to 2 weeks", "remind me to call Mom every 3 weeks" said as a cadence (not a reminder) — sets `cadence_days` (days) on the same record via `update_person`; `cadence_days` is read ONLY by the Balance surface and never touches work dormancy math (it is NOT `cadence_override_days`).

## Person Profile Format

Each person profile has this structure:

```markdown
### [Full Name]
- **Primary Org:** [most specific operating org — e.g., "Acme Tech (operating)"]
- **Other Orgs:** [any additional orgs this person is associated with, with relationship_type tags]
- **Role:** [job title / role]
- **How We Know Them:** [context — met at X, introduced by Y, worked together at Z, client contact]
- **Projects:** [linked project display_names separated by commas]
- **Last Interaction:** [date] — [brief context of what you discussed]
- **Key Notes:** [what to remember about this person — personality, preferences, context, open items]
- **Contact:** [email and/or phone if known]
```

### Example

```markdown
### Skyler Sample
- **Primary Org:** Acme Tech (operating)
- **Other Orgs:** Acme Holdings (holding, parent of Acme Tech)
- **Role:** VP of Product
- **How We Know Them:** Introduced by Sam Sample; met at Product Leaders conference in 2025
- **Projects:** Acme Tech Partnership, Product Strategy Review
- **Last Interaction:** 2026-03-15 — Discussed roadmap priorities; she's interested in Q3 roadmap for our integration feature
- **Key Notes:** Fast decision-maker, prefers async over meetings, cares deeply about user onboarding, mentioned team is 6 months behind on mobile. Open item: send her the feature spec.
- **Contact:** skyler@example.com / 415-555-0100
```

Rendered view groups people by primary-focus org first (per `morning-briefing` Step 4 layout rules), then other orgs rolled up by `relationship_type`.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "Threads: project_012, project_020 (person record updated)"
- Good: "Projects: Acme Tech Partnership, Product Strategy Review"

## Key Queries

**Instant Context**

```
"Who is [Person]?" — Full profile
"Quick brief on [Person]" — Just the essentials
"Why do I know [Person]?" — How you met
"What's [Person]'s role?" — Current position
```

**Relationship Strength**

```
"When did I last talk to [Person]?" — Last interaction date
"What did we discuss last?" — Context of last meeting
"Open items with [Person]" — Any unresolved topics
"Is [Person] connected to [Project]?" — Project overlap
```

**Network-Level**

```
"People at [Company]" — Everyone you know there
"How many people do I know at [Company]?" — Network size at a company
"Who at [Company] works in [department]?" — Filtered by function
"My network by company" — List of companies and contact count
```

**Relationship Health**

```
"Who haven't I talked to in 30 days?" — Stale relationships
"Who haven't I talked to in 90 days?" — Very cold
"Relationships I should nurture" — Mapped to projects/value
"Last interactions across my network" — Activity by contact
```

**Prep**

```
"Prep me for [Person]'s visit"
"Get me ready for dinner with [Person]"
"Refresh me on [Person] before the meeting"
"What should I ask [Person]?"
```

You get their full context plus suggested conversation starters.

## Triggers

- "who is"
- "tell me about"
- "add contact"
- "add person"
- "people at"
- "who haven't I talked to"
- "prep me for dinner with"
- "relationship check"
- "people crm"
- "contacts"
- "my network"
- "catch up with"

## Connected Tools

- **meeting-notes** — Automatically pulls attendees and adds them to PEOPLE.md if new
- **Gmail** — Can auto-update from email senders/recipients (with permission)
- **call-prep** — References PEOPLE.md for relationship context
- **Granola** — Pulls meeting notes to populate "Last Interaction" and "Key Notes"
- **MASTER_TRACKER** — Cross-references people with active projects
- **cleanup (`--summary` mode)** — Surfaces relationship updates in weekly/monthly summaries

## Gotchas

- **Privacy Matters:** PEOPLE.md contains personal information. Store securely, don't share lightly.
- **Last Interaction Auto-Updates:** The database automatically updates "Last Interaction" whenever you have a meeting, email exchange, or Slack thread with someone. Check it before you think you need to update it.
- **Company Changes:** When someone moves companies, update their "Company" field but keep "How We Know Them" unchanged so you don't lose context.
- **Threads Are Loose:** "Threads" is a reference field — it links to thread display_names via `primary_thread_id` values seen on events but doesn't manage them. Update manually when relationships end or new ones start.
- **Org inheritance:** If a person's email domain matches an org's `domains[]`, the skill auto-proposes that org association but waits for confirmation. Multi-org people (e.g., advisors, board members) carry multiple `org_ids[]`.
- **Contact Info Completeness:** Email is usually available; phone is often not. Use what you have, ask for what you don't.
- **Key Notes Can Grow:** These can get long over time. Keep them useful by trimming stale notes and surfacing what actually matters.
- **Duplicate Detection:** If you interact with someone under different names (shortened name, nickname, formal name), the skill asks if it's the same person before creating duplicates.
- **Cross-Source Enrichment Doesn't Auto-Save:** When you run "who is [person]?", the parallel scan from Gmail, Calendar, Slack, Drive, and the transcript connector shows up in "Fresh from your tools:" but doesn't auto-update the person record. You decide what's worth saving. This keeps the profile clean and intentional.
- **Enrichment Requires Connected Sources:** If Gmail or Slack is not connected, those sections will be empty. The enrichment is only as good as your connected tools.
- **Team Member Enrichment Caches During Briefing:** For `_people/` contacts, enrichment runs silently and caches during "what's going on". If you've just had a meeting or email that hasn't synced yet, the cache may be slightly behind real-time.

## What It Doesn't Do

- It's not a full CRM — no deal tracking, pipeline, or sales forecasting
- It doesn't integrate with LinkedIn directly (but you can copy profile links into "Key Notes")
- It doesn't auto-populate from an external contact list (you build it as you go)
- It doesn't track financial relationships or compensation
- It doesn't create action items (reference other people in decision-log or MASTER_TRACKER for that)

## Update Frequency

**Automatic Updates:**
- Last Interaction date updates whenever meeting notes or emails are processed
- New people are added when they appear as meeting attendees

**Manual Updates:**
- Role changes, company moves, key notes
- New contact info when you receive it
- Relationship status ("active", "warm", "cold") if you want to add that

## Cross-Source Auto-Enrichment (v1.7.0+)

When you ask "who is [person]?" or any person-first query, the skill runs a parallel scan across all connected sources BEFORE presenting the profile. This enrichment brings in real-time context from your tools without auto-updating the permanent profile.

**What Gets Scanned**

- **Gmail:** Last 5 emails to/from this person (subject lines + dates, not full bodies)
- **Calendar:** Next/last 3 meetings with this person
- **Slack:** Last 5 messages from or mentioning this person
- **Drive:** Recent shared docs with this person
- **Granola:** Last meeting transcript involving this person

**How It Presents**

The stored profile (the entities.json person record, read via the PEOPLE.md view) displays first. Then a "Fresh from your tools:" section appends anything new from the live scan. You decide what to save back — nothing auto-writes to the record from an enrichment scan.

**Staleness Indicator**

Each data source shows how fresh it is: "Last email: 2 days ago. Last meeting: 1 week ago. Last Slack: today." This helps you spot cold relationships at a glance.

**For Team Members vs. Contacts**

- **Team members** (profiles in `_people/`): Enrichment runs silently during "what's going on" and caches results. Fresh context surfaces in the daily briefing automatically.
- **Non-team contacts** (PEOPLE.md only): Enrichment runs on-demand when you explicitly ask "who is [person]?"

**Example**

```
Who is Skyler Sample?

[PEOPLE.md profile displays]

Fresh from your tools:
- Last email: skyler@example.com (Subject: "Q3 roadmap follow-up" — 2 days ago)
- Last meeting: Product Strategy Review, Mar 28, 2026 (Calendar)
- Slack: "Interested in seeing the mobile spec ASAP" (5 days ago)
- Shared doc: "Q3 Integration Plan" (Drive, last edited by you 1 week ago)
- Granola: Meeting on Mar 28 — discussed roadmap priorities and Q3 timeline

Save any of this to her Key Notes?
```

## New Person Enrichment Pipeline (v2.3)

When ANY skill (workspace-manager during "new project," meeting-notes when a new attendee surfaces, onboarding during bootstrap, manual add) creates a `person_*` record with `needs_enrichment: true` (via `people_writer.create_person(..., needs_enrichment=True)`), people-crm runs a one-shot enrichment pull on the next turn — BEFORE the CEO sees the record surfaced. Difference from the on-demand enrichment above: this is a silent backfill to populate the permanent record, not a transient read.

**Trigger:** A new `person_*` record in `_hq/data/entities.json` with `needs_enrichment: true`. This is the canonical ON-ENTITY flag, set by `people_writer.create_person(..., needs_enrichment=True)`. It REPLACES the old `pending_review` / `inferred_from` trigger — both are forbidden on the person entity by people_writer and were silently stripped, so enrichment never fired when creation correctly went through the typed writer (deep-audit #21).

**Pipeline (per connector, in order, all silent):**

1. **Gmail (30-day pull):**
   - Search for thread participation by the person's email address(es).
   - For each thread hit: emit `interaction` event with `channel: email`, `primary_project_id` resolved via alias-match (fallback: attendee-majority if the thread spans multiple known projects), `source_ref` dedup hash of `thread_id + date + person_id`.
   - Backfill `first_contact` (oldest email date), `last_interaction` (newest), `recent_threads[]` (top 5 by recency).

2. **Calendar (90-day pull, past + 14 days future):**
   - Search events by attendee email.
   - For each past event: emit `meeting` event with `status: occurred`, attendee list resolved.
   - For each future event: emit `meeting` event with `status: scheduled`.
   - Backfill `meeting_count_90d`, infer `typical_meeting_cadence` (weekly/biweekly/monthly/one-off).

3. **Granola (90-day pull):**
   - Search transcripts where the person was an attendee.
   - For each hit: emit `note` event flagging "transcript available, person_[id]" — do NOT emit full `meeting` + per-decision + per-commitment events (that's meeting-notes' job; don't shadow-process). The 90-day crawl is a backfill scan, not a targeted read, so it legitimately DEFERS full capture per `shared/INGEST_SUBSTRATE_SYNC.md`'s "do not crawl beyond surfaced results" clause.
   - **BUT do not silently lose a new person (v3.14.6+):** if a hit transcript has NO `meeting` event (`granola:<id>` `source_ref` absent from events.jsonl) AND it names a person not in `entities.json`, queue a `person_proposal` (pending_review) for that person via `shared/scripts/people_writer.py` `find_existing_person` dedup — instead of a bare `note`. The next Pulse fire surfaces it. This honors the don't-shadow-process rule (no full `meeting`/decision/commitment writes from the crawl) while closing the gap where an unprocessed transcript's new attendee vanishes (M, 2026-05-28).
   - Backfill `last_granola_transcript_ref` for quick CEO access.

4. **Slack (30-day pull):**
   - Search for DMs with the person, then mentions in tracked channels.
   - For each hit: emit `interaction` event with `channel: slack`, `source_ref` = channel_id + message_ts + person_id.
   - Backfill `slack_handle` if resolvable, `primary_slack_channels[]` (top 3 by frequency).

5. **Infer org affiliation:**
   - From email domains of Gmail threads, calendar-event organizer domains, and Slack workspace membership → determine `affiliation_org_ids[]`. Cross-reference `entities.json` for existing org matches. If a domain doesn't match any known org, flag for Reactive Org Discovery (Fix C in workspace-manager).
   - Set `is_primary_focus_person: true` if the person's orgs include any `is_primary_focus` org.

**On completion:**
- Clear the flag via the typed writer — `people_writer.update_person(workspace_root, person_id, needs_enrichment=False, source_skill="people-crm")`. Do NOT hand-edit the record.
- Enrichment state (`enriched_at` / `enriched_from` / `low_signal`) lives in events.jsonl, NOT on the entity (all three are forbidden person fields).
- Emit a single event of type `person_enriched` with `data: {enriched_at, enriched_from, counts: {emails: N, meetings: M, transcripts: T, slack_hits: S}, low_signal: <bool>}`.
- Surface to the CEO on next turn only if ≥3 hits: "Pulled together what I have on [Name] — found [N] prior emails and [M] past meetings. Anything you want me to add?"
- If 0 hits across all connectors, still clear `needs_enrichment` (via `update_person`) and set `low_signal: true` in the `person_enriched` event data (NOT on the record) so briefings treat it as tentative.

**Rules:**
- **Never double-enrich.** Check `enriched_at` before running; skip if set within the last 14 days.
- **Dedup is mandatory** via `source_ref` hash. The pipeline must be safely re-runnable.
- **Respect privacy scope.** Per `shared/PLUGIN_BOUNDARY.md`, no event body content — only metadata (subjects, dates, attendee lists). Body stays in the source tool.
- **Budget the pull.** Aggregate 60s cap across all connectors; if timeout hits, mark `enrichment_partial: true` and retry on the next person-first query.
- **Writer Contract.** This pipeline is the ONLY silent connector pull people-crm performs. All other connector reads (the on-demand enrichment above) stay transient.

## Workflow Integration

**After Each Meeting:**
1. meeting-notes skill processes transcript
2. Any new attendees automatically added to PEOPLE.md
3. "Last Interaction" dates auto-update
4. You can manually add notes if useful

**Weekly Reviews:**
1. Run "who haven't I talked to in 30 days?" to identify relationships to nurture
2. Use "people at [company]" to prepare for upcoming meetings at that organization
3. Check "open items with [person]" to ensure follow-ups don't slip

**Before External Meetings:**
1. "Prep me for [Person]" to refresh on relationship context
2. Check "people at [Company]" if you're meeting multiple people there
3. Review "what did we discuss last?" to avoid re-covering old ground

## Next Steps

- Use **call-prep** to pull relationship context into meeting briefs
- Use **people-crm** data in **cleanup (`--summary`)** to surface relationship activity
- Reference people in **decision-log** if they influenced decisions
- Use for **MASTER_TRACKER** project context (who's involved, who to loop in)

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Never walk into a meeting or dinner wondering who-is-this-again. Owns the CEO's relationship layer — auto-builds person records from every meeting, email, and Slack thread, and answers relationship queries instantly. Use when the CEO says 'who is', 'who is Aria', 'who is again', 'who do I know at', 'who do I know', 'add [name] to my contacts', 'to my contacts', 'refresh aliases', 'rebuild my aliases', 'what did Bowie and I last discuss', 'last discuss', 'what did we last discuss', 'prep me for dinner', 'prep me for dinner with', 'tell me about' — a PERSON ('tell me about Mira', 'tell me about Mira from the board'), resolved against your contacts. Writes to the person records in entities.json (canonical ownership) and regenerates _hq/views/PEOPLE.md. DOES NOT fire on 'prep me for my 2pm' (that's call-prep, which reads people-crm records) or 'team status / my direct reports' (that's team-intelligence, which extends people-crm for direct reports). DOES NOT fire on 'tell me about [a project or org you track]' (workspace-manager — 'go [name]' context load) or 'tell me about [an unknown company]' (research).

> Person-fact verbs (SPEC HIST1 D8): 'remember [fact] about [name]', 'note that [name] [fact]' — an explicit user statement appends a sourced person_fact_observed via people_writer.record_person_fact; ENTITY_RESOLVE gates every name-bearing form (lone first names disambiguate, never first-pick). Machine-matchable stems for the mechanical matcher: 'remember Sam prefers Signal', 'note that'. A name that resolves to a tracked ORG hands over to workspace-manager's org money & facts handler ('[Org] is a $[N] account' and org facts live there).
