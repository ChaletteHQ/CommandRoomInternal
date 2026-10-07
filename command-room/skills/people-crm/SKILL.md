---
name: people-crm
surfaces: both
description: "Fires on: 'who is [name]', 'tell me about [name]', 'who do I know at [company]'. Never walk into a meeting or dinner wondering who-is-this-again. The relationship memory: who someone is, how you know them, what you last discussed, what's open between you. Also fires on 'what did [name] and I last discuss', 'prep me for dinner with [name]', 'add [name] to my contacts', 'quick, who is [name] again'. Owns person facts: 'remember [fact] about [name]' ('remember Sam prefers Signal'), 'note that [name] [fact]' — appends a sourced fact to their history. Builds and reads per-person records from email, meetings, and notes. Does NOT fire on 'prep me for my 2pm' (call-prep — the meeting brief), 'prep for 1:1 with [direct report]' (team-intelligence), 'who should I reach out to' (relationship-moves), or 'model [name] as an advisor' (advisor-export)."
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
- **Account-scope on connector-derived records (connector-agnostic-v1, ACCOUNT_SCOPE §2):** when a person/org record is derived from a CONNECTOR READ (a sender on triaged mail, a meeting attendee from a transcript), pass the read's provenance to the writer — `create_person(..., provenance=<the read's provenance dict>)` or `account_address=<the mailbox it arrived through>` (NOT the contact's own email). The record wall (`account_scope_gate.enforce_record_scope`) rejects an out-of-scope account's contact before the entities.json write. A manual add ("add Dustin to my contacts") passes no provenance kwargs and is never walled.
- **Promote-queue confirm/demote (R8, ACCOUNT_SCOPE §8):** inbox-triage writes `person_proposal` events (`data.promote_queue: true`) for mixed-account senders not in the entity graph. When the user CONFIRMS ("file it" / promotes the proposal), create the person as a **user-confirmed add — NO provenance kwargs** (the user is the authority; the record wall is for unconfirmed connector derivations); future mail from that sender is then in scope by association (the wall passes events referencing resolved entities on mixed accounts). Append a `person_proposal_resolved` event pointing at the proposal. When the user DEMOTES ("keep personal" / "this is actually personal"), do NOT create a record; write the teaching signal via `connector_config.set_sender_scope_override(root, <account>, <sender>, write_to_business=False, reason="user demoted")` so the proposal never re-fires. The write dial stays fail-closed throughout — a classification error hides business mail (safe), never pollutes records (H-G).

### Bash gate: the Access preamble before any person write (v3.2+ MANDATORY; PEOPLEWRITE2)

Before any person-write step in this skill or its callers, resolve through the Access preamble:

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

The door's own answers are the abort check (PEOPLEWRITE2). A `not_in_manifest` or `not_allowed_writer` answer, or any other envelope with `ok: false`, means the writer cannot run on this seat: that is ONE sentence, the preamble's rule 6, and `writer_identity_required` means say the envelope's `line`, verbatim, as the whole answer. Never import the writer in a shell to test it, never run `people_writer.py` as a command, and never fall back to direct `entities.json` edits.

### Writer call shape

Three door forms, in this order. Render each with `workspace_access.py plan run_helper` or `plan run_writer` and paste what it prints, verbatim; every form answers ONE envelope, and the writer's own answer is its `result`.

```bash
# 1. ALWAYS dedup first: a READ, beside the data. It writes nothing.
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"aliases": ["<each alias the CEO gave>"], "email": "<the address, or null>", "name": "<the full name>", "workspace_root": "<WS>"}, "name": "people_writer:door_find_person"}'

# 2a. `result.match` is a record: change THAT record, never a parallel one.
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"last_interaction": "<YYYY-MM-DD>", "person_id": "<result.match.id from step 1>", "workspace_root": "<WS>"}, "name": "people_writer:door_update_person"}'

# 2b. `result.match` is null: create. The writer runs its own dedup again.
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"aliases": ["<each alias>"], "canonical_name": "<the full name>", "first_seen": "<YYYY-MM-DD>", "notes": "<one line>", "primary_org_id": "<the org's id from Gate 1's entity_resolve:resolve_all on the company name, or null; never the company name>", "role": "<the role>", "workspace_root": "<WS>"}, "name": "people_writer:door_create_person"}'
```

The branches, by the writer's own answer (`result`), each of which has written nothing:

- `result.ok: false`, `reason: "ambiguous"` (step 1 or 2b): a lone first name or an alias fits a record that may or may not be this person. Render the disambiguation widget from `result.candidates` (same person, a different person with the same first name, or skip), never a first pick (Bug #19), and never show an id.
- `reason: "duplicate"` (2b): the writer's dedup found a record step 1 did not (usually by email). Say `result.line`, then treat `result.existing_id` as the match and run 2a on it.
- `reason: "single_word_name"`, `"unknown_person"`, `"identity_field"` (2a: a change never carries `id` or `suppress_lineage`), `"out_of_scope"` or `"unreadable"` (step 1: the people file could not be read, so nothing may be created): say `result.line`, verbatim, and stop. Ask for the full name on the first.

Fields a change can carry are the schema's (the Writer Contract above); a field the schema refuses is a stop, never a retry with another key.

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

> **Gate 1 — Name resolution.** Before answering any "who is X" / "tell me about X" / "people at Y" / "prep me for [person]" query, you MUST resolve the name FIRST, through the door, in ONE call (ROUTE3, walk finding F-T2-14: a typed "who is" once skipped the resolver, opened the people file in a python body in the mounted folder, and answered "the only one on file" while a second record of that first name sat unlisted). Render it with `workspace_access.py plan run_helper --json '…'` and paste what it prints, verbatim; the shape is:
>
> ```bash
> python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"include_open_proposals": true, "query": "<the name as the CEO typed it>", "workspace_root": "<WS>"}, "name": "entity_resolve:resolve_all"}'
> ```
>
> The envelope's `result` is the resolver's candidate list, in the resolver's order (IDN-01: a fuller record before a one-word stub that shares its first name, then the most recent activity). `ok:false` is a stop, never a hand retry. **Never read the people records any other way**: no heredoc, no grep, no python body over the folder, no copy of a data file into this session. An empty `result` means there is no record of that name; say so in one line. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the ladder and the tiers.
>
> **The answer's shape (IDN-01; ROUTE3).** The resolver's FIRST candidate is the person you answer about. EVERY other candidate it returned, one-word stubs included, is named after that answer on ONE line that begins exactly `Also on file:`, each name followed by its org or role in parentheses when the record carries one, separated by semicolons. Never drop a candidate the resolver returned, and never say that someone is the only person of that name on file when the resolver returned more than one candidate. When the resolver returned one candidate there is no such line at all. A Tier-3 ambiguous lone first name still gets the disambiguation widget (Bug #19); this line is for an answer that has a clear first candidate.
>
> **The `open_proposal` hit (WG1-B D-B5).** When the ONLY result carries `entity_type: "open_proposal"`, the workspace has no record yet but a PENDING add-person proposal — never answer cold "I don't know who X is". Surface the proposal instead, snippet included: *"[Name] has a pending add-person proposal from [date] — '[evidence, ~140 chars]'. Add them / not relevant?"* Adjudication routes through the EXISTING confirm flow exactly as the staff-meeting person row dispatches (`add person` / `proposal not relevant` / `snooze proposal 7d` via apply-choices — no new wire); a mention is corroboration, never an auto-confirm.
>
> **Gate 2 — Commitment surface.** Before surfacing ANY commitment-related state for a person ("what's owed to them," "what they owe me," "what's open with them"), you MUST invoke `shared/scripts/cru_match.py::load_open_commitments(events_jsonl_path)` and filter the resulting list by the resolved `person_id`. Raw grep on events.jsonl is NOT acceptable — it doesn't apply closure-event suppression (commitments closed via `commitment_resolved` / `thread_resolved` / `commitment_superseded`) and produces stale or inflated commitment surfaces.
>
> **`data.pending_review` is NOT true — those are UNCONFIRMED extractions, not open commitments (INTAKE).** Run the result through `cru_match.split_pending_review(...)` and surface the confirmed half as this person's state. If they have pending rows, that is ONE labelled pointer line and never rows: *"N unconfirmed with them — say `needs your call`."* A guess rendered as "what they owe you" is the same inflated surface Gate 2 exists to prevent, arriving through a different door.
>
> **Gate 2b — a proved item CLOSES here; it is never offered (EXIT1; M's ruling 2026-09-07).** MANDATORY, immediately after Gate 2 and BEFORE you render a line about what is open with this person: run
>
> ```bash
> python3 -c "
> import sys, json; sys.path.insert(0, 'shared/scripts')
> from exit_doors import apply_fact_closes
> print(json.dumps(apply_fact_closes('<workspace_root>', only_person_ids=['<the resolved person_id>'])))
> "
> ```
>
> then render what is open. If `receipt_line` is non-empty, say it VERBATIM as its own line before the open list — one line, plain words, the `undo` in it. **NEVER write a sentence offering to close things** ("say the word and I'll close them", "want me to close these four?"): a kept promise the record already proves is closed where the product notices it, and this surface is one of the places it notices. The regression is the 2026-09-07 `pull up` that listed four mail-proved items and offered to close them instead. It writes nothing when nothing is proved, so it is safe on every render.

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
(`shared/CONTRACT.md` Rule 26) — never real contacts.

### Auto-add path (RICH context — FS-11, M ruling 2026-07-15)

When a person surfaces with **substance already attached** — a named attendee
in a processed meeting, a sender on a triaged thread, a person named with role
+ org in a source the CEO is acting on — auto-add them (M: "yes, add people
with rich context") through `people_writer:door_auto_add_person` on the write door, NOT the sparse
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

Through the write door, one form (the Access preamble above resolves first; PEOPLEWRITE2):

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"account_address": "<the mailbox the mail or invite arrived through, or omit the key for a manual add>", "canonical_name": "Quinn Sample", "email": "quinn@example.com", "email_provenance": {"meeting_id": "<the meeting id>", "source": "meeting"}, "primary_org_id": "<the org id>", "role": "VP Ops", "workspace_root": "<WS>"}, "name": "people_writer:door_auto_add_person"}'
```

`result.status` `added`: narrate "Added Quinn Sample." and promise no `undo` (the door's auto-add stamps no undo batch, so a bare `undo` cannot find it; the reverse is the archive in rule 3, by `people_writer:door_update_person` with `"status": "archived"` when the CEO asks). `result.status` `needs_confirm`: name `result.matches` by canonical name and ask before creating; nothing was written. `result.ok: false`: the same branches as the Writer call shape (`duplicate`, `ambiguous`, `single_word_name`, `out_of_scope`), nothing written.

### Person facts — `remember [fact] about [name]` / `note that [name] [fact]` (SPEC HIST1 D8)

An explicit user statement of one atomic fact about a person ("remember Sam prefers Signal", "note that Sam Sample prefers morning meetings"). The user is the authority — no proposal, no confirm card. Facts are ADDITIVE, SOURCED events; they never touch the person record (no notes-blob append, no new record field — the history renderer compiles them on read).

1. **ENTITY_RESOLVE first, for every name-bearing form.** The Gate 1 door call above (`entity_resolve:resolve_all` through `run_helper`), never an import. A lone-first-name hit that is Tier-3 ambiguous gets the disambiguation widget, never a first-pick (Bug #19); the fact writer is never called on an unresolved id. A name that resolves to a tracked ORG is workspace-manager's org-fact handler: hand it over.
2. **Write through the ONE fact writer, on the write door** (Rule 22 discovery preamble required, then ONE form; PEOPLEWRITE2):

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
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"category": "preference", "fact": "Prefers Signal over email", "person_id": "<the resolved person_id>", "source_ref": "chat:user-statement", "workspace_root": "<WS>"}, "name": "people_writer:door_record_person_fact"}'
```

`category` is optional: one of preference / contact / personal / role / company_news / other when it's clear; omit the key when it isn't. `source_ref` names where the fact came from (`chat:user-statement` for a direct statement; a message/meeting ref when the user is reacting to one). `result.ok: false` with `reason: "unknown_person"` wrote nothing: say `result.line`, verbatim; an envelope `ok: false` is the one-sentence stop above (`writer_identity_required`: its `line`, verbatim). 3. **Ack in one line** ("Noted, Sam Sample prefers Signal. It'll show in his history and call prep."). The fact appears in the person's history view (`go [person]`) and in call-prep's relationship context on the next render.

**Fences:** a role/company CHANGE ("Sam is now CRO at Acme Co") is a field change through the write door, `people_writer:door_update_person` (proposal-gated per the Writer Contract); the lineage trail is emitted automatically by the writer; do NOT also record it as a fact. Prose-INFERRED facts (a transcript "sounds like…") are never written directly: they ride the confirm rail as proposals (`entity_signal_detector.run_entity_signal_scan` writes them; never hand-write one).

**Structured auto-noting (HIST1 Part 2 — the ONE nuance to "enrichment doesn't auto-save"):** when the cross-source enrichment scan surfaces an atomic NON-identity fact from a STRUCTURED connector field (a signature block's "Prefers Signal" line, a calendar location field — never model inference over prose), it may be auto-noted as an additive fact EVENT via `entity_signal_detector.apply_structured_facts` — the person RECORD still never auto-updates, `role`/`company_news` facts still demote to confirm (S2), every auto batch is one-`undo` reversible, and the morning brief's CHANGED line narrates the count. Surface the returned `undo_line` in chat when anything was noted. Everything else in the "Fresh from your tools:" section stays exactly as documented below: shown, not saved, until the user decides.

### Personal ties — "[name] is my wife/husband/partner/mom/dad/kid" (SPEC BAL1 D1)

A family/personal relationship statement is a FIELD change, not a fact: ENTITY_RESOLVE the name, then `run_writer people_writer:door_update_person` with `{"person_id": "<person_id>", "role": "<the stated relationship, e.g. Wife>", "tie": "personal", "workspace_root": "<WS>"}` (rendered with `plan run_writer`, pasted verbatim). The `tie: "personal"` marker moves the person into the Balance surface's lane and OUT of every work surface: relationship-moves drops them and every dormancy emitter skips them at its source gate. Never infer the tie from a transcript; only an explicit user statement sets it (an inferred family relationship rides the confirm rail like any proposal). The reverse ("actually [name] is a client contact") sets `tie='work'`. A cadence statement ("set date-night cadence to 2 weeks", "remind me to call Mom every 3 weeks" said as a cadence, not a reminder) sets `cadence_days` (days) on the same record through the same `people_writer:door_update_person` form; `cadence_days` is read ONLY by the Balance surface and never touches work dormancy math (it is NOT `cadence_override_days`).

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

**The record header is COMPOSED, never typed (CUT-C item 8 — ATTENDED_TEST_v5.28.0 B4.4 printed `person_201` in the header):** the `### [Full Name]` line is `narration_names.person_header(WORKSPACE_ROOT, <resolved person_id>)` (`shared/scripts/narration_names.py`) rendered verbatim — the person's name, then role and org when on record, and never the id; a person the index cannot name renders "(no name on file)". Every other line that names a project, org or person resolves the id to its name the same way (`narration_names.humanize`).

**MANDATORY narration scan (CUT-C item 8, mirrors apply-choices Step 4):** before posting the profile, the relationship brief or any "Fresh from your tools" section, run `validate_chat_output(<the whole text>)` from `chat_output_renderer.py`. It raises `LeakDetectedError` on a raw id, an event or field name, a path or a score. ABORT the post and rewrite the offending line. NEVER catch the error and post anyway.

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
   - For each past event: emit `meeting` event with `status: occurred`, built via `meeting_capture.build_meeting_event()` (BUG-8244 canonical binding: top-level `person_ids` = attendees resolved, `data.attendees` = every invitee EMAIL verbatim, `data.attendees_external` = unmatched names).
   - For each future event: emit `meeting` event with `status: scheduled`, same builder and binding.
   - Backfill `meeting_count_90d`, infer `typical_meeting_cadence` (weekly/biweekly/monthly/one-off).

3. **Granola (90-day pull):**
   - Search transcripts where the person was an attendee.
   - For each hit: emit `note` event flagging "transcript available, person_[id]" — do NOT emit full `meeting` + per-decision + per-commitment events (that's meeting-notes' job; don't shadow-process). The 90-day crawl is a backfill scan, not a targeted read, so it legitimately DEFERS full capture per `shared/INGEST_SUBSTRATE_SYNC.md`'s "do not crawl beyond surfaced results" clause.
   - **BUT do not silently lose a new person (v3.14.6+):** if a hit transcript has NO `meeting` event (`granola:<id>` `source_ref` absent from events.jsonl) AND it names a person not in `entities.json`, queue a `person_proposal` (pending_review) for that person via `shared/scripts/people_writer.py` `find_existing_person` dedup — instead of a bare `note`. The confirm queue surfaces it. This honors the don't-shadow-process rule (no full `meeting`/decision/commitment writes from the crawl) while closing the gap where an unprocessed transcript's new attendee vanishes (M, 2026-05-28).
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
