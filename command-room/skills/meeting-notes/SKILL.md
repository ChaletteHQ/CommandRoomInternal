---
name: meeting-notes
description: "Process meeting notes from Granola or pasted text into structured artifacts — decisions, action items, SESSION_NOTES + Master Tracker updates. Triggers: 'process meeting', 'process the last call', 'process the call', 'meeting notes', 'meeting notes from', 'analyze this call', 'debrief from', 'log the meeting', 'summarize the call', 'summarize the meeting', 'action items from the meeting', 'action items from the call'. DOES NOT fire on 'follow up', 'draft follow-ups', 'close the loop' — those go to follow-up-ritual. DOES NOT fire on 'prep me for' — that goes to call-prep."
---

## Skill Boundary (v2.1)

- **Use meeting-notes for:** structured logging after a meeting — extract, route to SESSION_NOTES, append to events.jsonl, update MASTER_TRACKER / DECISION_LOG / PEOPLE.
- **Use `follow-up-ritual` for:** the same meeting + drafted per-attendee follow-up emails ready to send. If user says "follow up on that call" / "close the loop" / "draft follow-ups", defer to follow-up-ritual — it invokes this skill internally for the logging step.
- **Use `call-prep` for:** before a meeting, not after. "Prep me for…" phrases never fire this skill.

If the user's phrasing is ambiguous ("process the call and send follow-ups"), call follow-up-ritual — it covers both.

---

# Meeting Notes Skill (v2.0)

## Personification Contract (v3.13.8.4+)

Before surfacing the post-processing acknowledgment, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The chat acknowledgment after processing uses the shape `"Got it, {first_name} — {brain_name} processed `[Meeting Name]`. {N} commitments captured, {M} decisions logged. Anything to add before I file it?"` Default `{brain_name}` = `"Penelope"`.

## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes must follow the File Ownership Map, Write Protocol, and Append Format defined there. JSON sources live in `_hq/data/`; markdown views in `_hq/views/` are regenerated and must not be written directly. Violations go to `_hq/CONFLICTS.md`.

You are a **primary appender** for `_hq/data/events.jsonl` — every meeting you process becomes at least one event:

- One `meeting` event with attendees, summary, transcript reference.
- One `decision` event per captured decision.
- One `commitment` event per captured action item. **Schema is non-negotiable — see `shared/COMMITMENT_SCHEMA.md` and Step 5e below for the exact shape.** v2.7.15+ uses the canonical `data` envelope; legacy flat shape is read-only.
- One `interaction` event per person (`channel: "meeting"`) if not already implicit.
- Optional `status_change` or `scope_change` events when the meeting shifts project state.

**Every event you emit carries** `primary_thread_id`, `related_thread_ids[]`, `cross_ref_reason`, and `classification_confidence` per `references/ORG_AND_THREAD_MODEL.md` (schema field names unchanged for stability; the concept is "project"). Meetings that touch multiple projects (a 1:1 that covers a deal AND a vendor issue) get one primary + N related — never forced into a single project.

You also append to `_hq/data/aliases.json` when you discover new raw-to-canonical mappings (new nicknames, new email forms). After your appends, the writer helper regenerates affected views (`DECISION_LOG.md`, `PEOPLE.md`, `MASTER_TRACKER.md`).

You **append** to `[Project]/SESSION_NOTES_[NAME].md` as a human-readable narrative duplicate of the events you just persisted. Both must succeed: if the events.jsonl append fails, skip the markdown append and log a conflict.

You **do not write** to `entities.json` projects (that's workspace-manager) or people (that's people-crm). When you discover a new person or a project state change that warrants canonical update, surface a suggestion: "I see a new person 'X' mentioned — add to team?" / "This meeting shifts Project Y from active to blocked — confirm?" Owner skills execute on the next turn.

**Canonicalize every person and project reference via `aliases.json` before persisting any event.** No raw Gmail names or Slack handles in events.

Additionally, this skill implements `shared/PASSIVE_CAPTURE.md`. The Granola connector read when processing a transcript emits corresponding events per that contract's rules (meeting event + per-decision events + per-commitment events, all dedup'd via source_ref hash).

---

## Overview

This skill transforms meeting recordings, transcripts, or summaries into actionable intelligence. It classifies every call into one primary project + any related projects, routes notes accordingly, updates the Master Tracker with commitments and next actions, and surfaces business context — scope changes, timeline pressure, relationship shifts, risks, and opportunities.

Works for **any business owner** — scaling fast, running operations, managing teams, navigating client relationships, or juggling multiple projects (deals, advisory boards, operating companies, vendors).

### What It Does

1. **Pulls the meeting source** — Granola transcript if available, or process pasted text
2. **Extracts structured data** — decisions, action items, attendees, financial info, scope changes
3. **Routes to primary project** — saves SESSION_NOTES to `[WORKSPACE_ROOT]/[Project Folder Name]/SESSION_NOTES_[NAME].md` (where `[NAME]` = the user's first name, set during onboarding — e.g., `SESSION_NOTES_Pat.md`). To find the correct [NAME], look for the existing SESSION_NOTES file in the project folder (there should be exactly one file matching `SESSION_NOTES_*.md`). If no SESSION_NOTES file exists yet, check other project folders for the pattern, or check `_hq/BUSINESS_CONTEXT.md` for the user's name. If still unknown, ask: "What's your first name? I need it for your session notes files."
4. **Updates Master Tracker** — records commitments, last touched, next action, deadline pressure
5. **Updates ref files** — new contacts → contacts.md, scope/budget changes → scope.md, etc.
6. **Applies business lens** — flags what matters for strategy/execution (risks, opportunities, timeline pressure, relationship dynamics)
7. **Asks follow-up questions** — pulls context to understand implications, not just filing notes

---

## Step 1: Get the Meeting Source

### Option A: Granola Auto-Pull (Preferred)

If Gmail, Calendar, or Slack are connected:

1. Check your calendar for recent meetings around the time/context the user mentions
2. If a meeting is found, pull the Granola transcript automatically
3. Confirm the meeting matches the user's description

If no calendar connector is active or no meeting is found, move to Option B.

### Option B: Fallback to Pasted Text

If the user provides meeting notes, a transcript, or a summary:
- Accept paste from email, Slack, Granola link, or manual notes
- Parse as-is; don't require a specific format

### Clarifying Questions (if needed)

If the meeting source is ambiguous, ask:
- "Which meeting are you referring to? (date, attendees, company/project name)"
- "Do you have a Granola transcript, email, or should I check your calendar?"

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

Only prompt the user for a destination when classification confidence is `<0.40` on the primary project AND no plausible fallback exists (genuinely unknown content, not a known project). In that case: "Couldn't confidently route this call. Candidates: [top 3]. Pick one or let me queue it for the weekly review." Everything above the low-confidence floor routes silently.

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

## Step 5b: Update Decision Log

If `[WORKSPACE_ROOT]/_hq/DECISION_LOG.md` doesn't exist, create it with a header: `# Decision Log\n> Auto-created by meeting-notes\n`

For each decision extracted in Step 2, append to `[WORKSPACE_ROOT]/_hq/DECISION_LOG.md`:

```markdown
### [Date] — [Decision Title]
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

**Trigger conditions (all must hold):** the action item has (1) a forward-looking deliverable, (2) a specific artifact or decision (not "circle back"), (3) an identifiable named owner. Vague action items ("we should think about X", "let's revisit") DO NOT qualify — skip them silently. See `COMMITMENT_SCHEMA.md` § "Extraction triggers" for full guidance.

**Resolve the owner to a person id BEFORE appending.** Use `aliases.json` to canonicalize (e.g., "Mira" → `person_011`). If the owner is the user themselves ("I'll send the deck"), use the user's canonical id (the entity with `is_primary_user: true` or `is_user: true`). If you cannot resolve the owner to a person id, surface a one-line suggestion at the end of the meeting summary — but DO NOT skip the commitment; emit it with `owner_id: ""` and the title still set, so the commitment isn't lost.

**Speaker-attribution ambiguity guard (v3.2.3+):** before locking in `owner_id` from an alias lookup, check the meeting's attendee list for first-name collisions. If the Granola-tagged speaker name's first name matches MULTIPLE attendees on this call (e.g. "Rio" with both Rio Lange AND Rio Sample present), do NOT auto-pick — emit the commitment with `owner_id: ""`, add `data.attribution_ambiguous: true`, and add `data.attribution_candidates: [person_id_1, person_id_2, ...]`. Then add an explicit line to the meeting summary:

> *"Heads up — couldn't tell which '[speaker name]' was speaking on this call. Both [Name 1] and [Name 2] were there. Want to confirm who owns the [N] action item(s) that came from them?"*

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
  "person_ids": ["<owner_id>", "<other people involved>"],
  "data": {
    "owner_id": "person_NNN",
    "title": "<short verb-phrase, lowercase verb start, no trailing period, ≤120 chars>",
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

**Dedup safety:** If you've already processed this Granola transcript before (the meeting event already exists in events.jsonl with the same `source_ref`), check that you're not double-emitting commitments. Match on `(source_ref, title)` — if both already exist for a `type: commitment` event, skip the append. The `scan-for-commitments` skill enforces this same dedup; the two skills are interchangeable for the same source data.

**Surface a count after appending:** at the end of the meeting summary, line: "Logged N commitments (M you owe, K others owe)." — gives the user immediate confirmation that the extraction fired.

---

## Step 5e-bis: Close commitments fulfilled by this meeting (v3.11.1 — REQUIRED)

Commitments accumulate as "open" in events.jsonl forever unless something explicitly closes them. Before v3.11.1 only `follow-up-ritual` emitted `commitment_resolved`, which left meeting-notes as the largest open-commitment source — every commitment captured in Step 5e stayed open even when the same meeting that recorded the new commitment had attendees confirming the prior commitment was done. M's 2026-05-20 audit found 191 open commitments in his workspace, many already satisfied.

**Procedure (run BEFORE emitting new commitments in Step 5e, so a "delivered today" item isn't immediately re-opened):**

1. Load all open commitments for this meeting's `primary_thread_id` AND for each attendee `person_id` via `shared/scripts/cru_match.py::load_open_commitments(events_jsonl_path)`. This handles all 5 commitment shape variants (canonical, flat-new, legacy `owner`, `owner_person_id`-variant, pending-review).
2. For each open commitment, score it against the transcript using the existing CRU helpers (`cru_match.py` Path 3 — same scorer that past-meetings uses for transcript matches). HIGH-confidence completion language ("delivered", "sent it over", "done", "shipped") on a commitment owned by an attendee → auto-resolve. Schedule-shift language ("pushing to next week", "got delayed") → `commitment_updated`, NOT resolved.
3. For each auto-resolve, build the event via `cru_match.build_commitment_resolved_event(commitment_id=..., resolved_by=<attendee_person_id_or_user_id>, primary_thread_id=<meeting_thread>, source_skill="meeting-notes", evidence=<≤200-char quote-or-paraphrase from transcript>, next_seq=<next>)` and append via `atomic_append_jsonl`.
4. **Conservative auto-resolve only.** MEDIUM-confidence matches → emit `commitment_review_proposed` for next Pulse fire's one-click confirm surface, do NOT auto-close. The user-trust cost of falsely closing a commitment is much higher than the cost of leaving one open for a day.
5. **Silent.** Per CONTRACT Rule 9, do NOT narrate "auto-resolved 2 commitments" in the meeting summary. The user sees the result on the next Commitments fire (the resolved item simply doesn't appear).
6. **Dedup.** If the prior commitment was already closed by an earlier event (`commitment_resolved` / `thread_resolved` referencing its id), skip — `load_open_commitments` filters these out, but if a same-turn race emits two resolvers, the second is a no-op write that's idempotent on commitment_id.

Same shape rules as follow-up-ritual's Step "Surface Open Commitments" — the two skills follow the same v3.4.5 decision-CRU pattern.

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
   - If the meeting resolved or delivered a commitment that's already in a team member's profile (e.g., "Bowie presented the Aspen Project numbers"), update that commitment's status to "Delivered" with today's date.

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

## Step 7: Business Lens — What Matters

After processing, surface the **business lens**. Frame these for **any business owner**, not just consultants.

### Key areas to examine:

**Scope & Delivery** — Did scope creep? Is timeline still realistic? Who owns this?

**Financial Health** — Budget being consumed faster? Any new revenue or cost shifts? Visibility on P&L impact?

**Relationships & Trust** — How's the relationship (warming, cooling, stressed, strong)? Any misalignment or trust issues to address proactively?

**Risks & Execution** — What could derail this? Pressure to accelerate beyond capacity? Most likely failure mode?

**Opportunities** — Expand, cross-sell, or deepen? Could a shift unlock value? What's the play?

> For detailed business lens questions to ask the user, see references/meeting-notes-detail.md → "Business Lens Questions"

---

## Step 8: Ask 2-3 Follow-Up Questions

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

> **Downstream changes required to fully implement:** `shared/scripts/chat_output_renderer.py` and `references/orchestrator-past-meetings.md` must be updated to match this new structure. Until those land, this skill's chat output may render in the prior format. Track in `BACKLOG.md` under "meeting-notes v2.10.9 chat output structure."

**Step 9a — Generate the .docx brief (v2.14.32+ — `brief_writer` flow).** Use `shared/scripts/brief_path.get_brief_path("past_meeting", slug, date)` to compute the absolute path under `_hq/meetings/`, then pipe structured section content as JSON to `shared/scripts/brief_writer.py` stdin (same flow as `orchestrator-past-meetings.md` Phase 4 step 7 — see that file for the canonical JSON shape and section list). `brief_writer` produces deterministic, polished output with a hard-coded `Command Room` footer; the pre-v2.14.32 docx-skill invocation pattern + `Forwardable: yes` footer line are dead. Brief content still follows the **Brief Authoring Rules** above — factual recap only, no internal asks, no follow-up drafts. Cache the absolute path.

**Step 9b — Invoke `follow-up-ritual` silently.** Drafts per-attendee follow-up emails as TEXT only (lazy creation per `EMAIL_DRAFT_PROTOCOL.md`). The drafts surface in the next Inbox / Commitments fire where the user picks `N send` / `N draft`. **The drafts do NOT appear in the chat output of this skill, and do NOT appear in the brief docx.** This step matches `orchestrator-past-meetings.md` Phase 4 step 3 — same skill, same output destination.

**Step 9c — Render the 4-section chat card.** Through `chat_output_renderer.py` (when downstream renderer update lands) or via direct markdown until then. The card has these sections in order:

1. **Header line:** `Processed meeting: [Title] · [date] [time range]`
2. **RECAP** — 3–5 factual bullet points summarizing what happened (third-person, shareable register — same content profile as the brief docx).
3. **DECISIONS LOGGED** — list of decisions, each shown with a "added to your decision log" annotation. Each decision becomes a `decision` event in `events.jsonl` via the existing `decision-log` skill at processing time (not on user click).
4. **BRIEF** — link to the .docx with one-line metadata: `Clean recap · forwardable · no internal asks`. **v3.13.0+ surface the brief as an H2 heading link at the BOTTOM of the chat turn** via `chat_output_renderer.doc_headline_link(label, artifact_url)`. Format: `## → **[{Meeting title} — Past Meeting Recap]({computer_url})**`. `present_files` is no longer the primary opener — per CONTRACT.md Rule 3 (v3.13.0+) it's demoted to a reveal-in-folder secondary; include the `present_files` call only if the user is likely to want to navigate the filesystem (rare for meeting recaps — default: skip).
5. **OPEN ITEMS** — interactive list of items requiring M's resolution. Each item is M-only (clarification needed, decision he must make, action with no resolved owner).

**Renderer pre-flight (v2.10.9+):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from chat_output_renderer import render_chat_output_widget; print('OK')"
```

If stdout is not exactly `OK`, ABORT and surface plain English: `(Quick hiccup on my side — couldn't post the formatted output. I'll have it for you shortly.)` Do NOT fall back to hand-written prose.

```python
# Inside a python3 -c block invoked after `cd "$PLUGIN_ROOT"` (see preamble above)
import sys
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import render_chat_output_widget

data_view = {
    "widget_mode": "all_batch_widget",
    "header": None,
    "sections": [{"title": None, "count": None, "items": [meeting_item]}],
    "save_confirmation": None,
}
html = render_chat_output_widget(data_view, wrapper="fragment")
# Call mcp__visualize__show_widget with html as the widget body
```

**Open-items surface — `show_widget` all-batch button widget (v2.10.9+).** Open items are M-only resolutions (a clarification needed, a decision M must make, an action with no resolved owner). They render as a `show_widget`-rendered card with per-item button rows; selections accumulate in widget local state, one "Apply all" button fires the consolidated `apply choices: [...]` payload that `apply-choices` skill catches and dispatches. See `shared/CHAT_ACTION_WIDGET.md` for the full widget spec. Probe results: `PROBE_RESULTS_past-meetings-open-items.md` (workspace root).

**Posting rule:** the post is the rendered widget HTML, surfaced via `mcp__visualize__show_widget`. Do NOT paraphrase, do NOT compose chat strings, do NOT prepend or append narration. The widget IS the surface.

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

Pre-v3.13.0 Step 9d used `mcp__cowork__present_files` as the primary surface. That call is now optional and discouraged for meeting recaps — if you include it, position it AFTER the H2 link (so the user sees the opener first and the reveal-in-folder card second as a convenience).

Cowork emits an inline interactive card immediately after the chat output. This is the only mechanism that produces a clickable file surface in Cowork (workspace-relative paths and `file://` URLs render as plain text — verified Apr 29).

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
7. **Generate brief + render via renderer** → Step 9 (v2.10.8+ — converged with cr-past-meetings format)

**Skipped in light mode:** Decision log (5b), People database (5c), team profiles (5d), ref file updates (Step 6), Business Lens analysis (Step 7), follow-up questions (formerly Step 8 — gated to deep mode in v2.10.8+), email/calendar context check (old Step 3). These still exist — they run in deep mode. **Commitment events (Step 5e) run in BOTH modes — they're the data layer behind every commitment view in the workspace and must not be skipped.**

### Deep Mode ("process deep", "full meeting analysis")
The full 12-step cascade. Use when the user says "process deep", "full analysis", "deep process", or explicitly asks for the complete treatment.

1. **Get source** → Granola transcript or pasted text
2. **Extract** → Decisions, actions, financials, scope, relationships, risks
3. **Classify threads** → primary + related + confidence (see `ORG_AND_THREAD_MODEL.md`)
4. **Check context** → Email/Calendar for additional related threads
5. **Route** → SESSION_NOTES to primary project; cross-refs to related projects
6. **Update tracker** → Master Tracker (last touched, next action, commitments)
7. **Append commitment events** → one canonical commitment event per qualifying action item (Step 5e — see `shared/COMMITMENT_SCHEMA.md`)
8. **Update decision log** → Append decisions to `_hq/DECISION_LOG.md`
9. **Update people** → Add new attendees or update existing profiles in `_hq/PEOPLE.md`
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
