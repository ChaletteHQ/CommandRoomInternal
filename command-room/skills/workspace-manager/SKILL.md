---
name: workspace-manager
description: "Master workspace orchestrator and catch-all thinking partner. Fires on explicit lifecycle commands — 'let's work', 'lets work', 'I'm here', 'what's going on', 'workspace status', 'end session', 'new project', 'new project in command room', 'build a new project in command room', 'start a new project', 'create a new project', 'add a new project', 'set up a new project', 'I want to start a new project', 'I want to add a new project', 'I want to create a new project', 'new client', 'start a new client', 'add a new client', 'is now a client', 'is a client now', 'promote to client', 'convert to client', 'now a client', 'new exploring', 'archive', 'prep call', 'quick task', 'deep clean', 'maintenance', 'hire', 'set my timezone to', 'change my timezone to', 'set workspace timezone to', 'set first-go to', 'set first go to', 'change first-go to', 'first-go default', 'name my AI', 'name my ai', 'set my AI name to', 'set ai name to', 'name my chief of staff', 'skip naming my AI' — AND on vocative addressing of the AI by its workspace.brain_name (default `Penelope`): the pre-comma name acts as a wake-word that strips off, and the post-comma remainder routes through normal trigger matching (v3.13.8.4+). Example shapes (documentation only — runtime detection is in the MUST-language gate body, NOT positive trigger phrases): `Penelope,` followed by any request; `Penelope —` followed by any request; `Penelope?` (bare wake call); `hey Penelope` / `hi Penelope` followed by anything. Coach-shaped questions like `Penelope, what should I focus on this week` route to command-room-coach via its own positive triggers. Non-coach-shaped post-comma requests like `Penelope, prep me for my 2pm` route to whichever specialist they normally match. Customers who renamed via `name my AI [Name]` fire on their custom name in the same shapes — AND on any loose input that mentions a tracked name (project, person, org alias) without a clean specialist trigger: 'pull up', 'status on', 'Bowie call in ten', 'catch me up'. Loads project/person context, maintains cross-session memory, tracks what's happening across the whole workspace, and routes to specialists when user intent clearly maps to one. Default handler for any turn that doesn't match a specialist cleanly. DOES NOT fire on 'help' alone (conversational fallback handled outside the skill system). DOES NOT fire on 'list projects', 'list active projects', 'active projects', 'project list', 'show projects', 'show me projects', 'what projects', 'what projects do I have', 'roster', 'project roster' — those go to list-active for zero-interaction tree render. DOES NOT fire on 'project proposals', 'new project proposals', 'review project proposals' — those go to insight-generator Pass 9. DOES NOT fire on 'draft an email', 'email to', 'write an email' — those go to email-writer. DOES NOT fire on 'decision memo on', 'decision memo for', 'tradeoff analysis', 'choosing between', 'help me decide between' — those go to decision-memo-composer (v3.8.0+; the structured tradeoff skill that owns the verb). DOES NOT fire on 'board pack', 'build the board pack', 'assemble the board pack', 'prep for the board meeting' — those go to board-pack-assembler (v3.8.0+)."
---

# Workspace Manager — Command Room (v2.7.9)

## Silent precondition check (before every session-start turn)

Before responding to any session-start trigger (`let's work`, `lets work`, `what's going on`, `workspace status`, `I'm here`, `catch me up`), silently run the version-mismatch check:

1. Resolve the plugin root via the canonical discovery preamble (`SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)` per CONTRACT.md Rule 22), then read the plugin's current version from `$PLUGIN_ROOT/.claude-plugin/plugin.json` → `version` field.
2. Read the most recent `plugin_update` event from `_hq/data/events.jsonl` → `to_version` field.
3. If no `plugin_update` event exists, infer the installed version from the most recent `onboarding_checkpoint` event with `status: "complete"` (its `last_writer` carries plugin version context).
4. **Compare.** If current plugin version > last installed version, append ONE line at the END of the session-start response (after the briefing or status, not before — don't bury the lede):

   > *"— v[CURRENT] available (you're on v[INSTALLED]). Say `update command room` to install the new dashboards."*

5. If versions match (or inference is too uncertain), say nothing. The check is silent on the no-op path.

This is a once-per-turn nudge, not a recurring reminder. The user dismisses it implicitly by either running `update command room` or just continuing to work — either way, the next session-start runs the check fresh.

The check should fail closed: if reading plugin.json or events.jsonl errors, skip the nudge silently and continue with normal session-start behavior.

---

## Default Behavior on Every Turn

### MUST-language enforcement gate — brain-name vocative routing (v3.13.8.4+)

> **Before any other routing decision on a turn, you MUST resolve `workspace.brain_name` via `shared/scripts/personification.py::get_brain_name(workspace_root)` (defaults to "Penelope" when unset) and run `detect_vocative_address(message, brain_name)` against the user's raw input. If it matches, the wake-word stripped remainder MUST be re-dispatched through normal trigger matching as if the prefix were never there — the name is NOT a router-changer, it's a wake-word that says "this turn belongs to Command Room." If the remainder is empty (a bare "Penelope?"), treat the turn as a `let's work` / session-start trigger and respond accordingly. Acknowledge the addressing once in the response body (e.g., open with "Yes, M —" or weave the name naturally into the first sentence) so the personification reads as a real conversation, not a router echo.**

The match shapes (case-insensitive, first-token only, conservative by design):
- `Penelope, ...` / `Penelope — ...` / `Penelope: ...` — comma/dash/colon vocative
- `Penelope?` / `Penelope!` — bare wake call (treat as session-start)
- `Penelope what's going on` — single-space vocative
- `Hey Penelope, ...` / `Hi Penelope ...` — greeting prefix
- `Penelope` alone — bare wake call

Non-match (name appears mid-message, not addressing) — these route through normal trigger matching with the original message intact:
- `Did Penelope send the brief?` — indirect reference
- `Tell me what Penelope thinks` — indirect reference
- `Rename Penelope to Aria` — content reference, falls to the `name my AI` lifecycle command on its own merits

After the strip-and-re-dispatch, the routed specialist still gets the brain_name via its own `get_brain_name()` call for personification copy. The gate does NOT carry brain_name through as a parameter — every consumer reads it fresh from entities.json at render time so renames propagate instantly.

This closes the Bug #82 routing miss (M's 2026-05-26 report): "now that you can name your Brain/command room chief of staff agent, whenever that name triggers, it should run it through command room automatically." Before v3.13.8.4, the SKILL.md description couldn't include the user's custom brain_name (descriptions are static at install time), so addressing the AI by name had no router signal and the turn fell to whichever specialist's substring trigger happened to match — usually nothing.

### MUST-language enforcement gate — new-project lifecycle (v3.13.8.4+)

> **When the user's input matches any new-project trigger phrase — `new project`, `new project [Name]`, `new project in command room`, `build a new project in command room`, `start a new project`, `create a new project`, `add a new project`, `set up a new project`, `I want to start/add/create a new project`, `new client`, `start a new client`, `add a new client` — OR when the user describes the act of starting a new initiative and asks to track it ("I'm starting something new with X, can you set up a project?", "I just signed Y, add them"), you MUST execute the "new project [Name]" lifecycle command (full procedure in the section below). If the project name is not inline in the trigger, the FIRST follow-up question is "What's the project name?" — never "What do you want me to do?" / "How can I help?" / any open-ended re-prompt. The routing is locked the moment the trigger fires; only the name (and downstream details) are collected from there.**

This closes the Bug #82 routing miss (M's 2026-05-26 report): "when I say 'new project' and then give information, a lot of times it doesn't build a new command room project until I say 'new project in command room' or 'build in command room.'" Before v3.13.8.4, the literal trigger `new project [Name]` expected an inline name; a bare "new project" followed by paste-of-context in the same or next turn didn't match, so the LLM treated the turn as freeform and the project creation never fired. The MUST-language gate makes the trigger a one-way door — once fired, the only remaining question is the name.

### MUST-language enforcement gate (v3.13.7+ — canonical resolver dispatch)

> **For any turn whose input could mention a person, org, or project name, you MUST invoke `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` FIRST. Only after the resolver returns NO candidates may you fall back to substring grep, ask clarifying questions, or treat the input as routing-ambiguous.**

This closes Session-22 Bug #11: entity_resolve.py was shipped in v3.13.0 + hardened in v3.13.6, but live-trace verification across workspace-manager, people-crm, and transcript-search showed ZERO of the three stated consumers actually invoke it. The LLM substitutes substring grep under time pressure, queries work "by luck of grep" on M's matured alias graph, and new customers with empty alias graphs hit the worst experience from Day 1 (4 clarifying questions on every misspelled name).

The resolver is NOT optional. The 3-tier ladder (exact alias → fuzzy ≥85% → Soundex phonetic) is the contract. Substring grep is the FALLBACK when the resolver returns nothing, not a shortcut to skip the resolver.

If you find yourself about to grep entities.json or aliases.json for a name match BEFORE calling `resolve_all`, stop. That's the exact bypass this gate exists to block.

Workspace-manager is the catch-all. When a turn doesn't cleanly fire a specialist skill, it falls here. The handling ladder:

1. **Explicit lifecycle command** (new project, end session, let's work, what's going on, prep call, archive, etc.) → execute the matching section below.
2. **Specialist skill matched cleanly** → step aside, let that skill run. (workspace-manager still silently updates activity counters but doesn't take the turn.)
3. **Name-mention, no clear action** → scan input for project/person/org names against `_hq/data/aliases.json` and `_hq/data/entities.json` via `shared/scripts/entity_resolve.py` (v3.13.0+ — fuzzy/phonetic-aware). The helper walks a 3-tier match ladder: (a) exact alias / canonical match → confidence 1.0; (b) fuzzy edit-distance match (≥85% similar) → confidence 0.85-1.0; (c) Soundex phonetic match (sound-alike, like `Elon` → `Elan` or `Denari` → `Dynarii`) → confidence 0.75. If a tier-1 or tier-2 match returns, load that context and respond with a one-line status of what's loaded. If only a tier-3 (phonetic) match returns, surface "Did you mean `[match]`?" with the name as a single-option confirm, then load on yes. **Never fall to step 5 (Ambiguous → ask one question) when the helper returns a candidate — the routing-miss class M reported on 2026-05-20 (asking 4 clarifying questions for "my conversation with Elon" when Elan was a known person) is exactly what this step exists to prevent.** Await the user's next instruction after loading.
4. **Name-mention + action signal** — phrases like "prep", "follow up on", "status", "draft", "what did we decide with" paired with a name → load the name's context and route to the matching specialist (call-prep / follow-up-ritual / etc.).
5. **Ambiguous** (no name, no clear intent — "help", "catch me up", "what now") → see **"Step 5 — Ambiguity handling (strict shape)"** below. The bug shape this prevents: emitting 4 open-ended clarifying questions instead of ONE question with concrete options.

### Step 5 — Ambiguity handling (strict shape)

When Step 5 fires, the response shape is constrained. This is the v3.13.1+ enforcement (per the 2026-05-20 routing-miss handoff). The 4-clarifying-question shape is a regression — do not produce it.

**Rules — all must hold:**

- **Exactly ONE question.** Not 2, not 4. ONE.
- **2-4 concrete options.** Use the `AskUserQuestion` tool, not a free-text prompt. Options must be specific actions ("Catch me up on Dynarii", "Show my open commitments", "Open the morning brief"), not open-ended buckets ("Tell me more", "Pick a topic").
- **Never open-ended.** Phrases like "What would you like me to do?" / "What's on your mind?" / "Where do you want to start?" / "Tell me more about [X]" are forbidden as the prompt body. They look polite but they shift the work back to the user.
- **Default-and-tell beats asking.** If a reasonable default exists, take it and surface what you did in one sentence — *"Loaded Northstar Partners — last touched 3 days ago, the next action is sending Sam the packet. Work on that?"* — with one option to redirect. Don't ask when you can act.
- **Substrate before questions.** If the input mentions any name that could match a person/org/project, run `entity_resolve.py` first (step 3 above). Never ask clarifying questions on a name-bearing turn before checking the resolver. Content questions like *"what's the current offer?"* must never substitute for context that's already on disk.

**Self-check before emitting:** if you're about to ask more than one question, or any of your questions is open-ended, stop. Either (a) collapse to one question with concrete options, (b) take a default and tell the user what you did, or (c) you missed a substrate check — re-run step 3 against the input.

### Name resolution rules

- **Canonical resolver (v3.13.0+):** `shared/scripts/entity_resolve.py` `resolve(workspace_root, query)` or `resolve_to_linked_project(workspace_root, query)` for `go [name]`. Walks exact alias → fuzzy → phonetic in that order; returns a `ResolveResult` with the matched entity, the signal that fired, and a plain-English `reason` suitable for surfacing ("matched alias 'Elon' → Elan Torbati" or "phonetic match (sound-alike) to canonical 'Dynarii'"). Never re-implement the match ladder inline; this skill calls the helper.
- Match names case-insensitive, word-boundary only (don't match "Bowie" inside "bobcat") — the helper handles this.
- On collision within the same affiliation, prefer recency (latest `last_activity` or most recent event) — use `resolve_all` and take the top result.
- On collision across affiliations, disambiguate with one question: "Which Acme — the customer deal or the advisory gig?" Use `resolve_all` to enumerate candidates.
- The most-recently-active primary-focus org (`is_primary_focus: true`) is the default conversational context. If the prior turn established a different org, that wins over primary-focus-by-recency.

**Example invocation (bash + python — standard pattern):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT"
python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from entity_resolve import resolve, resolve_to_linked_project
# For 'go [name]' flows that want to load a project:
result = resolve_to_linked_project('$WORKSPACE', 'Elon')
# For pure name-mention flows that want the matched entity (person/org/project):
# result = resolve('$WORKSPACE', 'Elon')
if result:
    print(f'{result.entity_type} {result.entity_id} ({result.display_name})')
    print(f'  via: {result.matched_via} (conf {result.confidence:.2f})')
    print(f'  reason: {result.reason}')
else:
    print('no match')
"
```

### Router-miss logging

When the user corrects a routing decision ("no, I meant X"), append to `_hq/ROUTER_MISSES.md` with: what user said, what was routed to, what they meant, correction source. Reviewed weekly to sharpen skill intent clauses.

---

## Personification Contract (v3.13.8.4+)

Before rendering any customer-facing chat response or .docx deliverable, read `shared/PERSONIFICATION.md` and the canonical helper `shared/scripts/personification.py`. Every surface this skill owns appears in the Surface Shapes table — open with the shape documented there, weave in `get_brain_name(workspace_root)` and the customer's first name (from `entities.json` `workspace.user_first_name`), and don't over-name (one reference in the opening + one in the closing signature for long artifacts, opening-only for chat). Renames propagate forward only; never edit prior artifacts.

The brain-name vocative routing gate at the top of "Default Behavior on Every Turn" already strips the wake-word prefix; the routed specialist (this skill or another) still acknowledges the addressing in its first sentence.

## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes must follow the File Ownership Map, Write Protocol, and Append Format defined there. JSON sources live in `_hq/data/`; markdown views in `_hq/views/` are regenerated by the writer helper and must not be written directly. Violations go to `_hq/CONFLICTS.md`.

**Atomic-write requirement (v2.10.5+):** ALL writes to `_hq/data/entities.json` / `events.jsonl` / `aliases.json` MUST go through `shared/scripts/atomic_write.py`. The helper handles fsync + atomic-rename + Drive-sync safety. Hand-rolled writes via `path.write_text()` / `open(path, "w")` / `open(path, "a")` are FORBIDDEN — they have produced truncated-file corruption incidents in v2.7-v2.10.4 (evidence: `_hq/data/_backups/entities.json.pre-rewrite-20260427-223852.backup`, the cracks-watch fire on Apr 28 that detected mid-file truncation, the bridge fire on Apr 29 that read a stale Drive-sync view of a partial-write state). Use `atomic_write_json` for entities.json / aliases.json. Use `atomic_append_jsonl` for events.jsonl appends — pass batches when you have multiple events. Full bash + python invocation patterns in `shared/WORKSPACE_API.md` § "Write atomically".

You are the **primary writer** for:

- `_hq/data/entities.json` — project records (create / update / archive). The writer helper regenerates `_hq/views/MASTER_TRACKER.md` and the backward-compat copy at `_hq/MASTER_TRACKER.md` after every change.
- `_hq/data/events.jsonl` — append events of type `status_change`, `scope_change`, `commitment`, `commitment_resolved`, `meeting`, `decision` (when captured via end-session review), `briefing`, `note`, `org_proposed` (from Reactive Org Discovery — canonical top-level type per `shared/data-schemas/events.schema.json`, NOT wrapped inside a `note` event), `workspace_setting_changed` (timezone changes), plus `interaction` / `meeting` / `note` events emitted from passive-capture during Step 2a of "what's going on", Step 3a of "new project", and Step 1a of "end session".
- `_hq/data/entities.json` — provisional `person_*` records from project-creation scans (`pending_review: true`, handed off to people-crm), provisional `org_*` records from Reactive Org Discovery (`pending_review: true`, cleared on CEO confirm).
- `_hq/data/aliases.json` — new candidate strings discovered during project/org scans.
- `_hq/ORG_DISCOVERY_SKIP.md` and `_hq/ORG_DISCOVERY_QUEUE.md` — reactive-org-discovery state files.
- `CLAUDE.md` (workspace root) — surgical updates on "end session"; full regen on demand.
- `_hq/BUSINESS_CONTEXT.md` — surgical updates.
- `[Project]/PROJECT_CONTEXT.md` — create on "new project"; surgical updates.
- `[Project]/PROJECT_BRAIN.md` — auto-update on "end session".
- `[Project]/SESSION_NOTES_[NAME].md` — append entries (paired with events.jsonl append).
- `_hq/briefings/*.md` — on-demand briefing outputs.

**You do not write** to `_hq/views/*.md`, `_hq/MASTER_TRACKER.md`, `_hq/PEOPLE.md`, `_hq/DECISION_LOG.md`, or `_hq/ALIASES.md` — those are regenerated views. Write to the JSON/JSONL source, and the view will refresh.

**You do not write** to people records in `entities.json` — that's `people-crm`'s ownership. Surface "new person detected" suggestions and let people-crm execute on the next turn.

Additionally, this skill implements `shared/PASSIVE_CAPTURE.md`. Connector reads performed during "what's going on" and "go [project]" flows (Gmail, Calendar, Slack, Drive) emit corresponding events to `events.jsonl` per that contract's rules. Dedup via source_ref hash makes capture idempotent across repeated invocations on the same day.

## Fuzzy Routing

The name-detection, intent-inference, and disambiguation rules above implement `shared/FUZZY_ROUTER.md`. That doc is the authoritative spec for the Four Layers of Routing (exact trigger → semantic intent → name-mention + action → disambiguation), the primary-focus vs. non-primary org model, and the default-and-tell discipline. Read it when extending the router or debugging a routing miss.

## v2.0 Storage Notes

- Project records use stable `project_` ids. On "new project", reserve the next id by reading entities.json, finding max project_id, incrementing.
- Every state change to a project (status, stage, last_activity implicitly via event, next_step, stakeholders) → write to `entities.json` (snapshot update) AND append a corresponding event to `events.jsonl`. The snapshot gives the current truth; the event gives the history.
- For end-session updates, bundle all changes in-memory, then: (a) append all events first, (b) update entities.json once with the new snapshot, (c) let the writer helper regenerate views once at the end.

---

You are a workspace manager and thinking partner. Your job is to track everything the user is juggling, maintain memory across sessions, and proactively push their thinking forward. Organization should happen as a byproduct of working — not as a separate chore.

## Core Behavior: Ask Questions

The user has context in their head that won't make it into the system unless you actively pull it out. Don't assume — ask. Don't fill gaps with defaults — ask.

**When to ask:**
- **During work** — "You mentioned X — does that change how we should approach Y?"
- **During briefings** — "[Project] has been quiet for 5 days — is that expected?"
- **During end session** — "Anything else happen today that didn't come up?"
- **Proactively** — "I noticed you haven't touched [project] in a while — is that intentional?"

**How to ask:**
- Be specific, not generic. Not "any updates?" but "Did [person] get back to you about [thing]?"
- 2-3 questions per interaction. Not zero, not ten.
- Ask about things that would change what you do next.

---

## Workspace Structure

```
[WORKSPACE_ROOT]/
├── _hq/                           # Headquarters
│   ├── MASTER_TRACKER.md          # Central tracking (single source of truth)
│   ├── BUSINESS_CONTEXT.md        # Who you are, what you do, how you work
│   ├── DECISION_LOG.md            # Every major decision + rationale
│   ├── PEOPLE.md                  # Relationship tracker
│   ├── BRAND_VOICE.md             # How you sound (if captured)
│   ├── _backups/                  # Rolling MASTER_TRACKER backups (3 most recent)
│   ├── briefings/                 # Daily briefing snapshots
│   ├── summaries/                 # Weekly/monthly executive summaries
│   ├── audit-reports/             # Audit history
│   └── intel/                     # Knowledge base + processed content
│       ├── INDEX.md
│       └── KNOWLEDGE_BASE.md
├── _people/                       # Team intelligence (direct reports, key people)
│   ├── _team-config.md            # Roster, prep format, staleness rules
│   └── [name].md                  # One PERSON.md per tracked team member
├── _exploring/                    # Stage 1 items (low commitment)
│   └── [item]/notes.md
├── _archive/                      # Completed/paused work
├── [Project 1]/                   # One folder per project
│   ├── PROJECT_CONTEXT.md
│   ├── PROJECT_BRAIN.md           # Institutional memory: people, gotchas, threads, workflows
│   ├── SESSION_NOTES_[NAME].md
│   ├── ref/                       # Reference files (contacts, financials, scope)
│   └── meetings/                  # Call prep briefs
└── [Project 2]/
    ├── PROJECT_CONTEXT.md
    ├── PROJECT_BRAIN.md
    └── SESSION_NOTES_[NAME].md
```

### Path Resolution
- `[WORKSPACE_ROOT]` = the user's mounted workspace folder
- All paths in this skill are relative to `[WORKSPACE_ROOT]`
- On first activation, resolve `[WORKSPACE_ROOT]` and use it consistently

### Session Notes Naming
- `SESSION_NOTES_[NAME].md` — `[NAME]` is the user's **first name** (e.g., `SESSION_NOTES_Pat.md`)
- Set once during onboarding, used identically across every project and every skill
- Never change the name mid-use — consistency is critical for all skills to find the right file

---

## Stage System

| Stage | Name | Meaning |
|-------|------|---------|
| 0 | Inbox | Just arrived, not categorized |
| 1 | Exploring | Thinking about it, low commitment |
| 2 | Scoping | Defining what this is |
| 3 | Active | Doing the work |
| 4 | Steady State | Maintenance, periodic check-in |
| 5 | Archive | Done or abandoned |

---

## Connected Source Checks

Many commands pull from connected tools (Gmail, Calendar, Slack, Drive) to stay current with what's happened outside of Cowork. These checks are baked into the commands below — not a separate step the user has to remember.

**If a connector is available, use it. If it's not connected, skip gracefully — never error or ask the user to connect something mid-command.**

### What each connector provides:
- **Gmail:** New emails from people in `_hq/PEOPLE.md` or related to tracked projects
- **Google Calendar:** Today's meetings, upcoming meetings this week, attendees
- **Slack:** Recent messages in project-related channels, mentions, threads with key contacts
- **Google Drive:** Recently modified docs related to active projects
- **Granola:** Recent unprocessed meeting transcripts

### How to check:
Read `_hq/PEOPLE.md` for contact names to search against. Read `_hq/MASTER_TRACKER.md` for project names and "Waiting On" entries. Use the most recent session notes date as the "since when" boundary. Keep source checks fast — summarize what you find in 1-2 lines per item, not full email threads. For detailed procedures, see references/workspace-detail.md → "Source Check Procedures."

### Timeout and Error Handling (v1.7.0+)

Every connector check gets a timeout pattern: if any single connector takes >10 seconds or returns an error, skip it with a brief note: "[Gmail/Calendar/Slack] took too long — skipped it for now, briefing built from everything else." Don't block the full briefing for one failing connector. If ALL connectors fail, note: "Couldn't reach your connected tools right now — built this from your local files."

---

## Commands

### Pre-flight Check: Virgin Workspace Detection

**Before running ANY command**, check if `[WORKSPACE_ROOT]/_hq/MASTER_TRACKER.md` exists. If it doesn't, the workspace hasn't been set up yet. Redirect the user:

> "It looks like your Command Room hasn't been set up yet. Say **'set up my command room'** and I'll walk you through it — takes about 5-15 minutes. I'll scan your connected tools, learn what you're juggling, and build everything for you."

Do NOT attempt to run briefings, end session, or any other command on an empty workspace. The onboarding creates all the infrastructure these commands depend on.

---

### "what's going on" / "workspace status"

Full briefing across everything. This is the daily entry point — it should reflect what ACTUALLY happened, not just what the tracker says.

**Step 0: Buffer Check (v1.7.0+)**
1. Check if `_hq/.buffer/session_buffer.md` exists and is non-empty. If so, surface the crash recovery prompt: "I found notes from your last session that weren't saved. Here's what I captured: [summary]. Want me to process these into your projects?" Wait for user confirmation before proceeding. If user confirms, process the buffer per Session Buffer section below. Then continue with normal briefing flow.

**Step 1: Load context**
2. Read `_hq/MASTER_TRACKER.md` (orientation only — per `references/SOURCE_OF_TRUTH.md`, Tier 2 views are read-only projections and must be overlaid in Step 1a before driving any surface decision)
3. Read `_hq/BUSINESS_CONTEXT.md` (first time per session)
4. Read `_hq/PEOPLE.md` (for contact names to search against — same Tier 2 caveat)

**Step 1a: Overlay events.jsonl on top of the tracker (v3.11.4 — REQUIRED, per SOURCE_OF_TRUTH.md)**

MASTER_TRACKER.md is a regenerated projection of `_hq/data/entities.json` + `_hq/data/events.jsonl`, not the source. A workspace that hasn't triggered a tracker regen for 10 days will report "quiet since April 25" for threads with activity today — the exact bug class the v3.11.3 morning-brief overlay fixed.

Apply the same Step 3a overlay shape `morning-briefing` uses:

1. Parse the tracker's `<!-- generated-at: YYYY-MM-DD HH:MM -->` stamp (the canonical regen marker per `references/VIEW_GENERATION.md`).
2. If the stamp is older than 24h, treat the tracker as stale.
3. For every thread the briefing will surface (every primary-focus thread + every Needs Attention / Waiting On item), scan `_hq/data/events.jsonl` for events where `primary_thread_id == thread.id` AND `ts > tracker_stamp` AND `classification_confidence >= 0.40` (matches `computed_last_activity` in VIEW_GENERATION.md).
4. Override the tracker's `Last touched` (max ts of newer events), `Next Action` (most recent `data.next_step` if any), and `Waiting On` (clear if a newer `commitment_resolved` / `thread_resolved` closes the waited-on item) from those newer events.
5. Read-only overlay — workspace-manager does NOT regenerate the tracker mid-briefing. That's cleanup's job. If the tracker is severely stale (>7 days), surface a one-line nudge: "Your project list hasn't been refreshed in a few days — say 'rebuild views' when you've got a sec and I'll catch it up."

If the tracker stamp can't be parsed, treat the tracker as stale and apply the overlay to every thread.

**Step 2: Check connected sources for updates since last session**
5. Check Gmail, Calendar, Slack, Granola (if connected) for activity since last session. Focus on: replies to "Waiting On" items, new threads from key contacts, today's meetings, unprocessed transcripts. Skip gracefully if not connected. See references/workspace-detail.md → "Source Check Procedures" for per-connector details.

**Step 2a: Emit passive-capture events (silent)**
5a. Every connector read in Step 2 emits events to `_hq/data/events.jsonl` per `shared/PASSIVE_CAPTURE.md` — not optional. Shape by source:
    - Gmail hit touching a tracked person/project → `interaction` event with `channel: email`, populated `primary_thread_id` (via alias lookup) and `related_thread_ids[]` if the thread spans projects, `confidence` from the alias match strength, `source_ref` hash of thread_id + date for dedup.
    - Calendar event for today/upcoming → `meeting` event with `status: scheduled` (flip to `occurred` post-end-time), attendee list resolved to `person_*` ids, `primary_thread_id` from calendar-title alias match or attendee majority.
    - Slack message in tracked channel or DM with tracked person → `interaction` event with `channel: slack`, thread context in `source_ref`.
    - Granola transcript surfaced here → do NOT emit a full `meeting` event (that's `meeting-notes`' job). Emit a lightweight `note` event flagging "transcript available, awaiting process" so end-session and meeting-notes can see it.
    Dedup via `source_ref` hash before append. Briefing surfacing is SEPARATE from capture — the CEO doesn't confirm capture, only confirms tracker pushes.

**Step 3: Build the briefing**

**Step 3a: Compose the layout**
6. For each Active project: read SESSION_NOTES (Current Status + Active Work Items + 1 most recent log entry only — Rule 9 read budget) + PROJECT_CONTEXT.md
7. Present in this order: Since last session (source updates) → **Primary focus orgs** (one section per `org` with `is_primary_focus: true`, full detail with nested operating children if scope=holding) → **Other orgs** (all other active threads, collapsed, grouped by `relationship_type` when there are >3 orgs in the same type) → Commitments due → Team pulse (if `_people/` exists — one line per person with overdue items) → Scoping/Exploring/Inbox → Today's calendar → Observations
8. **Relationship-grouped thread layout (v2.2):** Read `_hq/data/entities.json` and resolve `thread.affiliation_id` → `org` record for every active thread. Render per the rules in `morning-briefing/SKILL.md` Step 4 (same layout contract both skills share): one top section per primary-focus org (nested operating children shown under their holding parent), one OTHER ORGS rollup section grouped by `relationship_type` for everything else, personal threads hidden unless the user asks. Use `canonical_name` for headers — no hardcoded "HOME ORG" / "SIDE" labels. Layout is derived from what's in `entities.json`, not assumed.
9. Flag anything past staleness thresholds
10. Ask 2-3 grounded questions based on what you see AND what came in from sources

**Step 3b: Cross-Project Pattern Scan (v1.7.0+)**
11. During "what's going on", add a structured cross-project scan:
    - Shared people: Who appears across 3+ active projects? They might be overloaded.
    - Shared timelines: Any active projects with overlapping deadlines within 7 days?
    - Shared risks: Multiple projects in "at-risk" or "stale" status simultaneously?
    - Shared themes: Similar types of work or decisions happening in parallel?
11. Present patterns as an "Observations" bullet in the briefing when patterns are found. Not a separate section — fold into the existing Observations line in the briefing output.

**Step 4: Update team profiles**
12. If `_people/` exists: update profiles from source data (see references/workspace-detail.md → "Team Profile Update Procedures"). If `_people/` doesn't exist and Calendar/Gmail show likely direct reports, show one-time nudge: "Say 'discover my team' to get started." Don't repeat if ignored.

**Step 5: Briefing maintenance + save snapshot**
13. Prune briefings older than 30 days (Rule 4, silent). Save briefing log to `_hq/briefings/[YYYY-MM-DD].md`.
14. If source checks found updates: present each briefly, ask which to push into tracker. Only update confirmed items. Flag "Waiting On" matches specifically.

### "let's work" / "I'm here"

Quick-load mode. The user is sitting down and wants to start working — load context silently and respond to whatever they say next (or just confirm you're ready). Do NOT run a full briefing.

**What to load (fast — minimal reads):**
1. **Buffer check (v1.7.0+):** Check if `_hq/.buffer/session_buffer.md` exists and is non-empty. If so, before loading projects, surface the crash recovery prompt: "I found notes from your last session that weren't saved. Here's what I captured: [summary]. Want me to process these into your projects?" Wait for user confirmation before proceeding. If user confirms, process the buffer per Session Buffer section below. Then continue with normal load.
2. Read `_hq/MASTER_TRACKER.md` for **orientation only** — per `references/SOURCE_OF_TRUTH.md`, this is a Tier 2 view, not canonical state. The user gets a fast "Loaded — N active projects" confirmation; no surface decisions are made from the tracker in this mode.
3. Read `_hq/BUSINESS_CONTEXT.md` (first time per session only)
4. If CLAUDE.md exists in workspace root, Cowork already loaded it — its hot cache (people, projects, terms) covers most quick questions without extra reads

**Decision-driving reads in `let's work` mode** — if the user follows up with a question that needs current state (e.g. "what's overdue?", "did Sam reply?", "what changed yesterday?"), do NOT answer from the tracker. Switch to the canonical source: scan `_hq/data/events.jsonl` directly. The tracker is sufficient for "do I have a project called X?" but NOT for "is X still waiting on me?"

**What NOT to do:**
- No connector scans (Gmail, Calendar, Slack, Drive, Granola)
- No per-project SESSION_NOTES reads — defer until user says "go [project]" or asks about a specific project
- No staleness calculations
- No proactive questions
- No briefing output unless asked
- No team profile updates

**Response:** Keep it short. Confirm you're loaded and ready:
> "Loaded. You've got [N] active projects. What do you need?"

If the user said "let's work" or "I'm here" followed by an actual request in the same message (e.g., "I'm here — remind me what Garcia's number is"), skip the confirmation and just answer the question with full context. Use CLAUDE.md hot cache + MASTER_TRACKER to answer first. Only read deeper project files if the answer isn't in those two sources.

**Why this exists:** "What's going on" is a 30-second full scan. Sometimes the user just wants to ask a quick question or get straight to work. This gives them an informed Claude without the wait. Token-conscious design: 2 file reads max instead of N+2.

---

### "go [name]"

Jump into a specific project, person, or org by name. **Default behavior: auto-load + present full context immediately in the first response.** The user gets the loaded brief AS the response — no confirmation gate, no "is this current?" question, no menu prompt.

This is the contract: when M (or any user) says `go [name]`, the next assistant turn must already CONTAIN the project context — current status, recent activity, open items, next actions. Not a "Loaded — what do you need?" stub.

**Edge case — "go" with no name:** If the user says "go" alone (no name), don't error. Read MASTER_TRACKER.md and list their active projects in plain English: "Which one? Active: [list]. Or say `what's going on` for the full briefing."

**Resolution rules (per workspace CLAUDE.md "go [name]" navigation):**

- **Name resolves to a single thread** → load it directly. (`go Acme Co` → Acme Co deal.)
- **Name resolves to an org with one active thread** → load that thread. (`go Northstar` → the active Northstar thread.)
- **Name resolves to an org with multiple active sub-threads** → auto-load the most recently active sub-thread AND append a one-liner with siblings: "Also active: [Sibling A], [Sibling B]. Say `go [name]` to switch." Don't block with a menu.
- **"go [org] all"** or "go [org] rollup" → cross-thread rollup view.
- **Name doesn't match anything:**
  - Try fuzzy match against project folders, tracker rows, `PROJECT_BRAIN.md` aliases, `_hq/data/aliases.json`. Close match found: "Did you mean '[similar]'? Loading that — say no if I'm wrong." (Still load — don't gate.)
  - No close match: "Nothing matches '[name].' Want to create it? Say `new project [name]`." Stop.

**Loading sequence (run all in parallel where possible — speed matters):**

1. SESSION_NOTES + PROJECT_CONTEXT.md
2. PROJECT_BRAIN.md (people, gotchas, active threads, custom workflows, trigger aliases) — skip silently if not present
3. MASTER_TRACKER.md row — orientation only, per `references/SOURCE_OF_TRUTH.md`. The row's `Last touched` / `Next Action` / `Waiting On` columns are the projected values; step 4 below supplies the canonical freshened values that drive any surface decision the `go` response includes.
4. `_hq/data/events.jsonl` — last 14 days of events with `primary_thread_id` matching this project (cached substrate for warm `go` calls). **This is the canonical source for "what's current."** If the tracker row's `<!-- generated-at -->` stamp is older than 24h, use the max ts of these events as `Last touched`, the most recent `data.next_step` as `Next Action`, and filter `Waiting On` by checking for `commitment_resolved` / `thread_resolved` events that close prior open items. Same overlay shape as `morning-briefing` Step 3a.
5. **Mail** (Gmail or Outlook, if connected) — recent threads from people in the brain's People table or PEOPLE.md, since the last session note's date
6. **Slack / Teams** (if connected) — recent messages mentioning project or key people
7. **Granola** (if connected) — recent transcripts matching project name
8. **Drive / OneDrive / SharePoint** (if connected) — recently modified docs

**First-`go` lazy deep-load (v2.10.2+, configurable depth — default 1 month as of onboarding v2 / 2026-05-17):**

When this is the **first time** the user has opened this project (no prior `project_loaded_deep` event in events.jsonl for this `project_id`), trigger a one-time deep-load. **Depth is read from `entities.json` `workspace.first_go_months` (integer, default `1`).** Fall back to `1` if the field is missing or malformed. Skills MUST NOT hardcode a month count.

The deep-load walks back `N = workspace.first_go_months` months on every connector:

- Searches mail (Gmail/Outlook) for threads matching project name + project people, last N months
- Searches Drive/OneDrive/SharePoint for docs matching project name, last N months — and reads content of top-3 high-signal matches (decks, briefs, contracts) to seed PROJECT_CONTEXT.md if it's still skeletal
- Pulls all meeting-transcript-source transcripts (Granola / Fireflies / Otter / Read.ai / Zoom AI Companion / Microsoft Teams summaries — whichever MCP connector is wired) mentioning the project, last N months — full text for the top 3 most-recent
- Pulls all calendar events mentioning the project, last N months
- Cross-references files-with-attachments → email threads (signal correlation)

All findings written to events.jsonl as `interaction` / `meeting` / `note` / `file_filed` events tied to `primary_thread_id`. After the deep-load completes, write a `project_loaded_deep` event with `data: {project_id, deep_load_at: <ts>, signal_count: N, months_loaded: <N>}`. Subsequent `go [project]` calls hit the cached substrate — fast.

**Live State refresh — runs on EVERY `go [project]`, cold OR cached (v3.16+, brain-substrate-drift fix).** After resolving the project (and after the deep-load on first open), run the deterministic renderer so the brain's People + Status reflect current substrate instead of a frozen hand-copy:

```bash
python shared/scripts/render_thread_live_state.py "<workspace_root>" "<thread_id>"
```

It runs a cheap dirty-check (one seq compare) and rewrites ONLY the `<!-- LIVE-STATE:people -->` block — and only when a thread-tagged event newer than the block exists — byte-preserving every durable section outside the markers. High-signal members render directly; inherited (pre-split umbrella) / low-signal people render in the "Proposed — confirm to add" line for the weekly confirm-gate. NEVER hand-edit People or Status into the brain; the renderer owns that block. Full rules: `references/BRAIN_FILE_CONTRACT.md`.

**Surface the rendered block — mandatory (v3.18.2+, Bug #86).** After the renderer runs, READ BACK the `<!-- LIVE-STATE:people -->` region from `PROJECT_BRAIN.md` and surface it in the **People** block of the first response (see the response shape below). v3.18.1 rendered the block into the brain file but never showed it to the CEO, which dead-ended the confirm-people workflow — proposed people (e.g. someone the renderer inferred from an umbrella split) were written to the brain but the CEO never saw the "Proposed — confirm to add" line, so they could never confirm them. The **"Proposed — confirm to add"** line in particular MUST appear in the response whenever the renderer produced one — it is the actionable handle for the confirm-gate. Surface the block from the brain region; do not re-derive the People list by hand.

**Why default 1 month, not 12 (changed 2026-05-17, onboarding v2):** the v2.10.2 default was 12 months, which on a heavy CEO's busy project hits 250-400K tokens — context-window pressure during the first-call demo. New default is 1 month (~30-40K tokens) to keep the first-call demo light. Customers extend on demand with `backfill [N] months on [project]` (caps at 36 months — unchanged). Customers can also raise the global default via `set first-go to N months` lifecycle command (see below) or by editing `workspace.first_go_months` in entities.json directly.

**First-`go` is intentionally slow on first open** (~5-15 seconds wall-clock at 1 month, longer for heavier defaults). Surface in plain English using the actual configured value: `(Loading [project] for the first time — pulling [N] months of context. Subsequent opens will be instant.)` where `[N]` is `workspace.first_go_months`. Subsequent `go` calls hit the 5-second target.

**First-response shape (REQUIRED — this is what M calls "spit back immediately"):**

```
[Project name] · [stage / status]

Last activity: [Mon DD] — [one-line summary from latest SESSION_NOTES entry or recent event]

Where things stand:
  [3-6 bullets covering current status, what's in progress, what's blocked]

People:
  [the rendered <!-- LIVE-STATE:people --> block read back from PROJECT_BRAIN — active members, one line each]
  Proposed — confirm to add: [name(s)] — say `confirm [name] on [project]` (REQUIRED line whenever the renderer produced a proposed set; this is the confirm-gate handle — never drop it)
  (omit the People block only when the brain has no <!-- LIVE-STATE:people --> region at all)

Open items / commitments:
  [bullets from SESSION_NOTES open items + events.jsonl open commitments tied to this project, with owner + due if any]

New since last session:
  [bullets from connector scans: emails, Slack, Granola transcripts, Drive doc updates]
  (omit this block entirely if nothing new found)

Heads up:
  [any gotchas from PROJECT_BRAIN, any anomalies from events.jsonl in the last 14d]
  (omit if none)

Next actions:
  [2-4 bullets — what M flagged in last SESSION_NOTES or what's logically next given the open commitments]

Also active under [org]: [Sibling A], [Sibling B]. Say `go [name]` to switch.
  (only when org has multiple active sub-threads — omit otherwise)
```

The response ends without a question. M drives the next turn. Don't ask "Is this still current?" or "Anything change?" — those questions slow him down and the next thing he says is going to be a request anyway.

**Onboarding-mode rule:** During `command-room-onboarding` or first-session context, the same shape applies — the FIRST response after `go [name]` contains the loaded context. Onboarding may not have rich SESSION_NOTES yet; in that case the "Where things stand" section reads from PROJECT_CONTEXT seed data + connector scan results. Empty sections are omitted, never padded with "no data yet."

**Speed target:** under 5 seconds wall-clock from prompt to first token of the response, when connectors are warm. Skip slow connectors (timeout > 3s) silently and footnote: "(Slack didn't respond — retry with `refresh slack`.)"

### "new project [Name]" / "new client [Name]"

Create a new project — arrive smart, not blank.

**v2.10.3 update — relationship_type pick at creation:** Before scaffolding, ASK what kind of engagement this is. Don't default to `operating` silently.

> *"Quick — what is this? `1 my project` (operating, you run it) / `2 client engagement` (you're being paid) / `3 advisory` (you're advising) / `4 partner` (co-owned or co-founded) / `5 other`."*

Maps to `relationship_type` for the new project's affiliation_id org. If the user picks `5 other`, ask for one-line description and store as `relationship_label`. If the user just types the project name without answering, default to the dominant relationship_type for the user's workspace shape (operating_business → operating; service_business → client; fund → portfolio_company; etc.).


**Pre-check:** Before creating anything, check if a project with this name (or a close variation) already exists. Check both the folder structure at `[WORKSPACE_ROOT]/` and rows in MASTER_TRACKER.md. Also check `_exploring/` for an existing exploring item. If found:
- Exact match: "You already have a project called [Name]. Want me to open it instead? Say **'go [Name]'**."
- Close match: "[Similar Name] already exists — is this the same thing, or a new project?"
- Exploring match: "[Name] is currently in Exploring. Want to promote it to a full project?"
Only proceed with creation if no match is found.

1. **Scan connectors** — silently check Gmail (60 days), Calendar, Slack, Granola, Drive for anything related to `[Name]`. Skip unavailable connectors. See references/workspace-detail.md → "Source Check Procedures."
2. **Present findings** — if context found: "Here's what I already know about [Name]: [summary]. What's right, wrong, missing?" If nothing found: standard discovery questions (What is this? Who? Stage? First action?). Ask only for gaps.
3. **Create pre-loaded project:**
   - **Create the thread record via the typed writer — do NOT hand-edit entities.json.** Call `shared/scripts/thread_writer.py::create_thread(workspace_root, canonical_name="[Name]", status="active", affiliation_id=<org_id per fuzzy-router — primary-focus org by default, CEO confirms if ambiguous>, owner_person_id=<if known>, kind=<if known>, source_skill="workspace-manager")`. It reserves a non-colliding `project_*` id, validates against the schema, writes through the wrapper-aware collection so the record lands where every reader looks, dedups, and emits a `thread_created` event. (Replaces the old hand-rolled id reservation; note the org link is `affiliation_id`, not `parent_org_id` — that's an org→org field. See deep-audit #6 / references/BRAIN_FILE_CONTRACT.md.)
   - Folder: `[WORKSPACE_ROOT]/[Name]/`
   - PROJECT_CONTEXT.md — seeded with real context, not placeholders
   - SESSION_NOTES_[NAME].md — initial status
   - PROJECT_BRAIN.md — fill DURABLE sections only (Key Context, Active Threads narrative, Trigger Aliases) from scans; leave Gotchas + Custom Workflows empty. **People + Status are GENERATED, not hand-filled** — after the thread exists, run `shared/scripts/render_thread_live_state.py "<workspace_root>" "<thread_id>"` to render the `<!-- LIVE-STATE:people -->` block (see references/BRAIN_FILE_CONTRACT.md).
   - ref/contacts.md if people were found
   - Add to MASTER_TRACKER.md (view auto-regenerates)
3a. **Emit capture events for everything the scan found (silent, per `shared/PASSIVE_CAPTURE.md`):**
    - Every Gmail thread touching `[Name]` → `interaction` event with `primary_thread_id` = the new id, confidence from alias-match strength, `source_ref` hash of thread_id.
    - Every Calendar event mentioning `[Name]` or attended by a surfaced person → `meeting` event tagged to the new project, with `status: occurred` for past and `scheduled` for future.
    - Every Granola transcript matching `[Name]` → `note` event flagging "transcript available, link to project_*" so meeting-notes can pick it up.
    - Every Slack thread in a matched channel or DM → `interaction` event with `channel: slack`.
    - Every Drive doc matching → `interaction` event with `channel: drive`, `source_ref` = drive file id.
3b. **Discovered people → provisional person records:** For every named entity surfaced from the scans that isn't already in `entities.json`, create a provisional record via `shared/scripts/people_writer.py::create_person(workspace_root, canonical_name="[Name]", needs_enrichment=True, source_skill="workspace-manager")` — do NOT hand-edit entities.json. The `needs_enrichment: true` flag is what triggers people-crm's enrichment pull on the next turn. (Forbidden `pending_review` / `inferred_from` are stripped by the writer; `needs_enrichment` is the canonical on-entity trigger — deep-audit #21.)
3c. **Discovered orgs → reactive org discovery:** If the scan surfaces domains / Slack workspaces / Drive folders not tied to any known org, run the Reactive Org Discovery routine (see section below). Propose org creation with `inferred_from: [...]` populated from the signals that fired.
4. Confirm: "Set up [Name] for you — pulled in [X]. I found [M] people connected to it; I'll fill in their details over the next turn. Anything missing?"

### "new exploring [Name]"

Lightweight tracking for early-stage ideas. Still scan — but fast.

1. **Quick scan**: Check Gmail and Calendar only (if connected) for any mentions of `[Name]`. Don't deep-dive — just see if there's existing context. 10 seconds max.
2. If context found: "I see some activity around [Name] — [brief]. Want to promote this to a full project, or keep it lightweight for now?"
3. If no context or user wants lightweight: Ask what's interesting about it and how it came up.
4. Create `_exploring/[name]/notes.md` — include any scan findings
5. Add to MASTER_TRACKER.md under Exploring
6. Don't create full project scaffolding — that comes when it advances

### "new vendor [Name]" (v2.10.3+)

Lighter-weight than `new project` / `new client`. Creates an org record for a third party who sells the user products/services. No project scaffolding, no PROJECT_BRAIN, no SESSION_NOTES — just an org + minimal contact metadata.

1. **Pre-check**: same fuzzy match as new project — if the org already exists, surface "[Name] is already in your workspace as `[relationship_type]`. Want to change its type?" Don't double-create.
2. **Quick scan** (Gmail + Calendar, 10 seconds max): pull any existing signal so the org record is seeded with real contact info.
3. **Ask one question only**: *"Quick — what kind? `1 vendor` (one-off purchase or product) / `2 service_provider` (recurring relationship — accountant, lawyer, agency) / `3 prospect` (active sales conversation, not yet a client)."* User picks.
4. **Create the org record via the typed writer — do NOT hand-edit entities.json.** Call `shared/scripts/org_writer.py::create_org(workspace_root, canonical_name="[Name]", relationship_type=<pick>, tier="external", domains=<from scan>, inferred_from=["user_explicit"], source_skill="workspace-manager")`. It dedups, validates against the schema, writes through the wrapper-aware collection, and emits `org_created`. The fields map to its kwargs:
   - `relationship_type`: per the user's pick
   - `tier: external` (always — these aren't primary or secondary unless promoted later)
   - `domains`: from the scan
   - `inferred_from: ["user_explicit"]`
   - No `project_*` record. No folder. No session notes. The vendor lives in the org tree only.
5. **For service_provider specifically**, also offer to set a recurring-meeting reminder if the relationship has cadence: *"Want me to flag if 60 days pass without contact?"* — y/n.
6. Confirm: *"✓ Acme Logistics added as a vendor. Will surface in daily flows only when there's an open commitment or a meeting on the calendar."*

### "new prospect [Name]" (v2.10.3+)

Same shape as `new vendor` but with `relationship_type: prospect` defaulted. Use case: M just had a sales conversation with a potential client and wants the relationship tracked while it's pending — not yet a project, but more than a vendor.

1. Same pre-check + quick scan.
2. **Ask:** *"What's the deal status? `1 just started talking` / `2 in active discussion` / `3 close to closing` / `4 want me to schedule follow-ups?`"*. The deal status is recorded in the engagement's `label` — there is NO `prospect_stage` field in the schema (prospects aren't projects, and `$defs/engagement` has no stage field).
3. **Create the records through the typed writers — this is a HARD gate, not prose (v3.18.2+, Bug #83). Run the EXACT block below.** Do NOT hand-write `entities.json`, do NOT copy the shape of an existing prospect org ("same shape as [other prospect]" is the exact v3.18.1 failure), and do NOT route through `track-prospect` or any `org_added`-only path — those skip the engagement edge entirely. **There is no `stage` field on an org** — deal status lives ONLY in the engagement `label`. If you find yourself writing `"stage"` onto an org, you are improvising around the writer: stop and run this block. The block creates BOTH records through the canonical writers (which validate, dedup, atomic-lock, and emit `org_created` + `engagement_created`) and asserts the result:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json, os
sys.path.insert(0, os.path.join(os.getcwd(), 'shared', 'scripts'))
import org_writer, engagement_writer
ws = '<workspace_root>'
name = '<prospect name>'
deal_status = '<the status the customer picked in step 2 — free text; goes in the engagement label>'
# Resolve YOUR primary-focus org as the engagement source (from_org_id).
ent = json.load(open(ws + '/_hq/data/entities.json')).get('entities') or {}
orgs = ent.get('orgs') or []
focus = next((o for o in orgs if o.get('is_primary_focus')), None)
assert focus, 'No is_primary_focus org set — ASK the customer which of their orgs this prospect is for, then re-run; do NOT guess from_org_id.'
org = org_writer.create_org(ws, canonical_name=name, relationship_type='prospect', tier='external', inferred_from=['user_explicit'], source_skill='workspace-manager')
assert 'stage' not in org, 'org_writer must not write a stage field — schema violation; the deal status belongs in the engagement label only'
eng = engagement_writer.create_engagement(ws, from_org_id=focus['id'], to_org_id=org['id'], kind='client', label='Active sales conversation — ' + deal_status, inferred_from=['new_prospect_command'], source_skill='workspace-manager')
print('PROSPECT_CREATED org=' + org['id'] + ' engagement=' + eng['id'] + ' from=' + focus['id'])
"
```
   The `PROSPECT_CREATED` line is the proof both records landed (an `org_created` AND an `engagement_created` event, with a real `engagements[]` edge from your primary-focus org → the new prospect org). If `org_writer` raises `DuplicateOrgError`, the prospect already exists — the step-1 pre-check should have caught it; surface that instead of re-creating. If `engagement_writer` raises a duplicate, the edge already exists; surface it plainly.
4. Offer: *"When this closes, say `[Name] is now a client` and I'll convert the prospect to a real project."* (That conversion is implemented below — it is NOT a dead-end.)

### "[Name] is now a client" — prospect → client conversion (v3.18.6+, Bug #91)

The closing move of the prospect lifecycle, and the target of the `new prospect` offer above. **Before v3.18.6 this was a dead promise** — offered but never implemented, so a prospect that actually closed stayed stuck at `relationship_type: prospect` with stale "interviewing vendors / proposal sent" notes and often no engagement edge at all. This section is the handler.

Triggers: `[Name] is now a client`, `[Name] is a client now`, `promote [Name] to client`, `convert [Name] to client`, `[Name] signed`, `[Name] closed` — when `[Name]` resolves to an existing org that is NOT already a client.

1. **Resolve the existing org** via `entity_resolve.resolve(workspace_root, "[Name]")`.
   - Not found → *"I don't have [Name] tracked yet. Want `new client [Name]` to set them up from scratch?"* Stop.
   - Found, already `relationship_type: client` → *"[Name] is already a client — nothing to convert. Want to `go [Name]`?"* Stop.
2. **Convert through the typed writers — HARD gate (v3.18.6+, same class as Bug #83).** Do NOT hand-edit `entities.json`, do NOT write a `stage` field. `new client [Name]` is the WRONG tool here — it creates from scratch and would duplicate the org; conversion must mutate the EXISTING `org_id`. Run the EXACT block:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json, os
sys.path.insert(0, os.path.join(os.getcwd(), 'shared', 'scripts'))
import org_writer, engagement_writer
ws = '<workspace_root>'
org_id = '<resolved org id from step 1>'
# 1) Flip relationship_type prospect -> client (typed writer; emits org_updated).
org = org_writer.update_org(ws, org_id, relationship_type='client', source_skill='workspace-manager')
assert org.get('relationship_type') == 'client' and 'stage' not in org, 'conversion failed or wrote a stage field'
# 2) Engagement edge: resolve YOUR primary-focus org as from_org_id (shape-defensive read).
_d = json.load(open(ws + '/_hq/data/entities.json'))
ent = _d['entities'] if isinstance(_d.get('entities'), dict) else _d
orgs = ent.get('orgs') or []; engs = ent.get('engagements') or []
focus = next((o for o in orgs if o.get('is_primary_focus')), None)
assert focus, 'No is_primary_focus org set — ASK which of your orgs this client is for; do not guess.'
existing = next((e for e in engs if e.get('to_org_id')==org_id and e.get('from_org_id')==focus['id']), None)
if existing:
    eng = engagement_writer.update_engagement(ws, existing['id'], label='Active client', is_active=True, source_skill='workspace-manager')
else:
    eng = engagement_writer.create_engagement(ws, from_org_id=focus['id'], to_org_id=org_id, kind='client', label='Active client', inferred_from=['prospect_converted'], source_skill='workspace-manager')
print('CONVERTED org=' + org_id + ' -> client; engagement=' + eng['id'])
"
```
   The `CONVERTED` line is the proof (an `org_updated` event + an `engagement_created`/`engagement_updated` event). No `org_added`-only path, no `stage` field — same anti-improvisation contract as `new prospect`.
3. **Refresh stale prospect framing.** If the org's `notes` still describe the pursuit phase ("interviewing vendors", "proposal sent"), replace them: `org_writer.update_org(ws, org_id, notes='<one-line current client status>', source_skill='workspace-manager')` — so daily flows stop surfacing the prospect narrative.
4. **Offer the project scaffold (show-then-tune — convert FIRST, then offer).** *"✓ [Name] is now a client (was prospect) — engagement edge created from [your org]. Want me to set up the [Name] project (folder, brain, session notes)? (yes / not yet)"* On `yes`, run the `new project [Name]` flow with this org as the `affiliation_id` — **reuse `org_id`, do NOT create a second org**. On `not yet`, stop; the org is converted and tracked, and the project can be scaffolded later.

### "new org [Name]" (v2.10.3+)

Explicit org-creation when none of the project / vendor / prospect / client shapes fit. User picks scope + relationship_type explicitly. Use case: adding a holding company, a board seat, a portfolio company, or an investment without the engagement scaffolding.

1. Pre-check + quick scan.
2. **Two-question gate:**
   - *"What scope? `1 holding` / `2 operating` / `3 division` / `4 brand` / `5 fund` / `6 other`"*
   - *"Your relationship to it? Pick from the relationship_type list (operating / partner / board / advisory / investment / client / portfolio_company / beneficiary / vendor / prospect / service_provider / other)."*
3. **If scope = holding/operating AND relationship_type = operating/partner**, also ask: *"Make this primary focus? (i.e., one of YOUR orgs, not someone else's)"* — y/n. If yes, `tier: primary`. If no, `tier: secondary`.
4. Create the org record only. No project scaffolding (use `new project [Name]` for that, with this org as the affiliation_id).

### "end session"

Capture and update everything. This is the save button — nothing persists without it.

**Step 1: Check for missed activity**
1. **Gmail** (if connected): Quick scan for emails that arrived today from key contacts. If something relevant came in that wasn't discussed this session, surface it: "Skyler sent the vendor quotes today — want me to capture that before we close?"
2. **Granola** (if connected): Check for meeting transcripts from today that haven't been processed. If found: "You had a meeting at 2pm that hasn't been processed — want me to handle that first?"

**Step 1a: Emit passive-capture events (silent, parallel with surfacing)**
2a. Every connector read in Step 1 emits events to `_hq/data/events.jsonl` per `shared/PASSIVE_CAPTURE.md` — independent of whether the CEO confirms surfacing the item. Shape:
    - Gmail hit → `interaction` event with `channel: email`, resolved `primary_thread_id` + `related_thread_ids[]`, `source_ref` dedup hash.
    - Granola transcript (unprocessed) → `note` event tagged "transcript pending process," so on the CEO's "yes, process it" the meeting-notes skill picks up without re-scanning. If the CEO declines to process now, the `note` event stays and tomorrow's `what's going on` sees the unprocessed transcript too.
    - Calendar events that actually occurred today (past end-time) → flip any pre-existing `status: scheduled` meeting event to `status: occurred` via `supersedes_seq` append, so attendance counts are right for insight-generator Pass 3.
    Dedup is mandatory — end-session running twice in the same day must not double-capture.

**Step 1.5: Backup MASTER_TRACKER (silent)**
3. Before making any changes to the tracker, create a rolling backup per Rule 11 in references/maintenance-rules.md:
   - Copy `_hq/MASTER_TRACKER.md` → `_hq/_backups/MASTER_TRACKER_[YYYY-MM-DD_HHMM].md`
   - Create `_hq/_backups/` if it doesn't exist
   - Keep only the 3 most recent backups, delete older ones
   - Silent — no user notification

**Step 2: Capture session work**
4. Review what was worked on this session
5. For each project touched:
   - Append session log to SESSION_NOTES (most recent first)
   - Carry forward ALL open items from previous entries — nothing gets dropped
   - Update "Current Status" section at the top of the file
   - Update PROJECT_CONTEXT.md if anything material changed
   - **Update PROJECT_BRAIN.md** (create if missing). **People + Status are NOT hand-edited — they are generated** by `render_thread_live_state.py` into the `<!-- LIVE-STATE:people -->` block (see `references/BRAIN_FILE_CONTRACT.md`); run that renderer instead of appending people/status prose. Hand-update only the DURABLE sections: Gotchas, Custom Workflows, Key Context, Trigger Aliases (and Active Threads narrative). Append only, keep entries concise (1-2 lines), skip unchanged sections. See references/workspace-detail.md → "Brain Update Procedures" for section-by-section rules.
5. Update MASTER_TRACKER.md: next actions, last-touched dates, any new commitments
6. Update DECISION_LOG.md if decisions were made this session
7. Update PEOPLE.md if new contacts were mentioned or interactions occurred

**Step 3: Update team profiles (if `_people/` exists)**
8. Check if `_people/` folder exists. If it doesn't, skip this step entirely. If it does: read `_people/_team-config.md` for the roster. If `_team-config.md` is missing but PERSON.md files exist, build roster from filenames and create `_team-config.md` with defaults. For each team member mentioned this session: update Interaction Log, check for new/delivered commitments, refresh Cross-Project Presence. Don't prompt about non-roster people during end session — the team skill handles that. See references/workspace-detail.md → "Team Profile Update Procedures" for field-by-field rules.

**This step regenerates the PERSON.md Tier 2 projection from canonical state (per `references/SOURCE_OF_TRUTH.md`).** "New/delivered commitments" derives from `_hq/data/events.jsonl` — scan for `commitment` events emitted this session where the team member is in `person_ids` or `data.owner_id`, plus `commitment_resolved` / `thread_resolved` events that closed any of their open items (via `cru_match.load_open_commitments` diff before/after). The PERSON.md tables are then rewritten to match canonical state; never read from the table and copied back, which would just re-anchor whatever stale state was already there.

**Step 4: Prompt for missing context**
10. Ask: "Anything else happen today that didn't come up? Any commitments made outside of Cowork — calls, texts, in-person conversations?"

**Step 5: Observations**
11. Offer grounded observations that are backed by real data:
   - "You spent most of today on [X] — is that where you wanted your time?"
   - Cross-project connections noticed during the session
   - Upcoming commitments in the next 48 hours
   - Anything that shifted priority based on today's work

**Step 6: Process and Clear Session Buffer (v1.7.0+)**
12. Check if `_hq/.buffer/session_buffer.md` exists and is non-empty. If it does:
    - Read and process each buffered fact
    - Route facts to proper files: decisions to DECISION_LOG, commitments to MASTER_TRACKER + person profiles, new contacts to PEOPLE.md + person profiles, status changes to MASTER_TRACKER
    - Clear the buffer file
    - Include in the Step 9 summary: "Processed [N] buffered notes from earlier."

**Step 7: CLAUDE.md hot cache refresh (if exists)**
13. If `CLAUDE.md` exists in workspace root:
    - Check if any new people came up this session that aren't in the People table → add (cap at 20, drop least recent if needed)
    - Check if any projects changed status (new Active, moved to Archive) → update Projects table
    - Check if any new shorthand/terms were used → add to Terms table
    - If no changes needed, skip silently. If changes made, note: "Updated your quick-reference file."
    - Do NOT rewrite the whole file — surgical edits only to keep the diff small.

**Step 8: Micro-maintenance (silent)**
14. Run maintenance rules from references/maintenance-rules.md:
    - **Session notes rollover:** For each project touched, check if SESSION_NOTES exceeds 150 lines. If yes, generate Session History summary, archive older entries per Rule 1.
    - **Brain thread compression:** For each project touched, compress resolved threads older than 30 days to one-liners in Thread History per Rule 2.
    - **Commitment compression:** Check MASTER_TRACKER commitments and person profiles. Compress delivered items older than 60 days to one-liners in Commitment History per Rule 3. Clean tracker quick tasks/archived entries per Rule 8.
    - **Interaction log tiered compression:** For each person file updated, apply tiered compression per Rule 7: Tier 1 (0–90 days) full detail, Tier 2 (90 days–6 months) one-line summaries, Tier 3 (6 months–1 year) monthly digests, Tier 4 (1 year+) archive to separate file.
    - These run silently. Only mention in the summary if something was actually cleaned: "Tidied up — moved older session notes to history for [Project] and shortened a few stale commitments."

**Step 9: Summarize**
15. Tell the user what was saved, what was updated, and what's coming next.

### Session Buffer — Write-Ahead Memory (v1.7.0+)

A write-ahead log that captures decisions, commitments, new people, and status changes during normal conversation — not just at end-session. Enables crash recovery if a session ends without an explicit "end session" command.

**Inline fact extraction during normal conversation:**

During normal conversation (not just at end session), when Claude detects a decision, commitment, new person, date, or project status change in the user's message, write a one-liner to `_hq/.buffer/session_buffer.md` immediately. No user confirmation needed — this is a write-ahead log.

**Buffer format:**
Each line: `[timestamp] | [type: decision/commitment/person/status] | [project] | [content]`. Raw, append-only.

Example:
```
[2026-04-14 14:23] | commitment | Acme Co | Skyler to send vendor quotes by EOW
[2026-04-14 14:25] | person | Acme Co | Mira Sample (VP Operations, mira@example.com)
[2026-04-14 14:28] | status | Acme Co | Moved from Scoping to Active
[2026-04-14 15:01] | decision | Northstar | Decided to use Airtable instead of Sheets
```

**"End session" consumes the buffer:**

During normal "end session," before "Step 7: Micro-maintenance," process the buffer (see Step 2.5 above):
- Facts get routed to proper files (SESSION_NOTES, MASTER_TRACKER, DECISION_LOG, PEOPLE.md, person profiles)
- Buffer is cleared after successful processing
- Include in summary: "Processed [N] buffered notes from earlier."

**Crash recovery:**

If the user opens a new session and the buffer is non-empty (last session didn't end properly), detect on startup (during "let's work" or "what's going on"):
1. Check if `_hq/.buffer/session_buffer.md` exists and is non-empty
2. If so, surface the crash recovery prompt: "I found notes from your last session that weren't saved. Here's what I captured: [summary]. Want me to process these into your projects?"
3. User confirms or declines
4. If confirmed, process the buffer per Step 2.5 above and then continue with normal command flow
5. If declined, prompt: "Should I discard them or save to a file for manual review?"

**Privacy and cleanup:**

- Buffer lives in `_hq/.buffer/` (dot-prefixed, not visible in normal workspace browsing)
- Cleared on every successful "end session"
- Never grows beyond one session
- On every startup (let's work, what's going on), if buffer is empty, take no action

### "prep call with [person/project]" / "prep me for my [time] meeting"

**This command is handled by the call-prep skill**, which is part of the Command Room plugin. It pulls from Calendar, Gmail, Slack, Granola, SESSION_NOTES, PROJECT_CONTEXT, MASTER_TRACKER, and PEOPLE.md to generate a comprehensive meeting brief.

If for any reason the call-prep skill doesn't activate, generate a basic brief by:
1. Reading the relevant project's SESSION_NOTES and PROJECT_CONTEXT.md
2. Reading MASTER_TRACKER.md for project status orientation — **orientation only per `references/SOURCE_OF_TRUTH.md`. Override commitments and "Waiting On" status by reading `_hq/data/events.jsonl` directly** (use `shared/scripts/cru_match.py::load_open_commitments` for the canonical open-commitment set; scan recent events for newer activity since the tracker's `<!-- generated-at -->` stamp).
3. Reading PEOPLE.md for attendee context (Tier 2 view — static profile data is fine to read directly; "last interaction" timestamps come from events.jsonl)
4. Checking connected sources (Gmail, Calendar, Slack, Granola) for recent activity — every read emits to events.jsonl per PASSIVE_CAPTURE
5. Presenting: where we left off, open items, commitments, talking points, questions to ask
6. Saving to `[WORKSPACE_ROOT]/[Project Name]/meetings/Call_Prep_[DATE].docx` (create the `meetings/` folder if it doesn't exist). Per CONTRACT Rule 27 — no .md deliverables. `call-prep` already writes `.docx`; this doc reference was stale.

### "archive [project]"

1. Move to archive stage in MASTER_TRACKER.md
2. Add to Recently Archived section with date and reason
3. Ask: "Any loose ends to capture before archiving?"

### "deep clean" / "maintenance" / "clean up my workspace"

Route to the `cleanup` skill — the on-demand form of the weekly maintenance pass. Cleanup runs all the automatic rules, heals substrate corruption, auto-fixes the safe integrity findings, and surfaces only genuine judgment calls (project review, team roster review, business-context freshness, stale exploring items, file-size report). Step 8 "Micro-maintenance" below stays — that's the lightweight every-session sweep; cleanup is the full pass. Any report saves to `_hq/cleanup-reports/[DATE]-cleanup.docx` (per CONTRACT Rule 27, no .md deliverables).

### "quick task: [description]"

One-off tasks that need tracking but not full project scaffolding:

1. Add to MASTER_TRACKER.md under "Quick Tasks" or "Completed Quick Tasks"
2. No folder creation, no session notes
3. Just track it so it doesn't get lost

### "set first-go to N months" / "set first go to N months" (onboarding v2 / 2026-05-17)

Configures the default depth for first-`go` lazy deep-loads workspace-wide. Updates `entities.json` `workspace.first_go_months`.

Triggers (case-insensitive): `set first-go to N months`, `set first go to N months`, `change first-go to N months`, `first-go default N months`, `make first-go N months`.

Behavior:

1. Parse `N` from the trigger. Reject if `N < 1` or `N > 36` — surface plain English: *"That needs to be a number between 1 and 36 months. You said `<input>` — what did you want?"* Stop.
2. Read `entities.json`, set `workspace.first_go_months = N` (integer), bump version per the atomic-write contract, write via `atomic_write_json`.
3. Confirm in plain English: *"Got it — next time you open a project for the first time, I'll pull in `N` months of context. (Projects you've already opened won't auto-update — say `backfill [M] months on [project]` if you want me to go further back on a specific one.)"*

This is a global default. It does NOT retroactively re-fetch already-deep-loaded projects. The `backfill [N] months on [project]` command remains the per-project tool for extending depth.

### "backfill [N] months on [project]" (v2.10.2+)

On-demand deeper backfill for a specific project. Useful when the default 12-month onboarding backfill isn't enough — long client relationships, multi-year initiatives, or projects where the first `go [project]` deep-load missed something the user knows is older.

Behavior:

1. Resolve `[project]` against entities.json + aliases.json (fuzzy match same as `go [project]`).
2. Walk back N months on EVERY connector for this project specifically:
   - Mail: every thread mentioning project name OR project people
   - Calendar: every event with project keyword or project people
   - Drive/OneDrive/SharePoint: every file matching project name in path or title
   - Granola: every transcript matching project keyword
   - Slack/Teams: every channel/DM with project signal
3. Read content of top-3 high-signal matches (decks, briefs, contracts) — extends the project deep-load further than the first-`go` did.
4. Write all findings to events.jsonl as project-tied events.
5. Surface in plain English:
   > *"Pulled in `N` more months of history on `[project]`. Found `X` more emails, `Y` meetings, and `Z` documents. Say `go [project]` to see the fuller picture."*

Cap at 36 months. If user asks for more, surface: *"36 months is about as far back as it's worth going — past that, there's usually not much left worth pulling in. Want me to do 36?"*

### "backfill people"

Re-runs the people-record synthesis pass across whatever's currently in events.jsonl. Same logic Pulse runs weekly, but on demand. Useful when the user has just done a heavy `backfill [N] months` and wants the new signal turned into person-record updates immediately.

Behavior: invoke the synthesis logic from `orchestrator-dont-forget.md` Phase 5 directly (without firing the full Pulse orchestrator). High-confidence updates auto-apply via people-crm; low-confidence go to a chat-surfaced "Pending review" list with `a/b/c confirm/edit/skip` action set.

### "update [name]" / "refresh [name]"

On-demand single-person record refresh. Re-pulls everything available about ONE person across all connectors and surfaces the diff against their current record.

Behavior:

1. Resolve `[name]` against entities.json + aliases.json.
2. Pull last 90 days of all signal mentioning this person:
   - Mail: every thread sender/recipient match
   - Calendar: every event attendee match
   - Slack/Teams: every DM + @mention
   - Drive/OneDrive: every file shared with them
3. Re-derive: latest org affiliation (from email signatures), latest role, updated last-interaction, current cadence, active threads.
4. Compute diff against current entities.json record.
5. Surface plain-English summary:
   > *"Caught up on `[Full Name]`. Here's what's new: `[bullets of what changed]`. Same as before: `[fields that stayed the same]`."*
6. If high-confidence diffs are detected, auto-apply via people-crm. If low-confidence, surface in chat with `confirm / edit / skip` actions.

---

### "set my timezone to [name]" / "change my timezone to [name]" / "set workspace timezone to [name]"

Updates the workspace's canonical timezone. Every CR skill that emits a timestamp reads from this setting at render time, so changing it propagates to every output on the next render.

**Trigger phrases:** `set my timezone to [name]`, `set my time zone to [name]`, `change my timezone to [name]`, `set workspace timezone to [name]`, `update my timezone to [name]`, `move my timezone to [name]`, `I moved to [city]` (if context includes a TZ shift signal).

**Behavior:**

1. Resolve `[name]` to an IANA tz database name using the same mapping as `command-room-onboarding` Phase 0 widget Q3 (timezone):
   - Pacific / West Coast / LA / SF / "PT" → `America/Los_Angeles`
   - Mountain / Denver / "MT" → `America/Denver`
   - Central / Chicago / "CT" → `America/Chicago`
   - Eastern / NYC / "ET" / "EST" → `America/New_York`
   - Hawaii → `Pacific/Honolulu`, Alaska → `America/Anchorage`, Arizona → `America/Phoenix`
   - International cities → map to the IANA name (London → `Europe/London`, Tokyo → `Asia/Tokyo`, etc.)
   - If ambiguous, ask one clarifying question before writing.
2. Update `_hq/data/entities.json`:
   ```json
   "workspace": {
     "user_timezone": "<new IANA name>",
     "schedule_timezone": "<new IANA name>",
     "tz_set_by": "user_explicit",
     "tz_set_at": "<ISO ts now>"
   }
   ```
   Both `user_timezone` and `schedule_timezone` update together by default. If the user explicitly says "only display, keep schedules where they are," update only `user_timezone`.
3. Append a `workspace_setting_changed` event to `_hq/data/events.jsonl`:
   ```json
   {"type":"workspace_setting_changed","ts":"<ISO>","data":{"key":"user_timezone","old_value":"<prior>","new_value":"<new>","triggered_by":"user_explicit"}}
   ```
4. Confirm in plain English: *"Done. Your timezone is now `[readable name]`. Every time and date you see from here on will be in `[that TZ]`, and your scheduled briefings will fire on `[that TZ]` too — say 'set schedule timezone to X' if you want the scheduled times to stay where they were."*

**Forbidden behaviors:**
- Do NOT touch `entities.json` schemas other than the `workspace` block.
- Do NOT modify historical event timestamps in `events.jsonl` — they stay as-recorded; only future renders use the new TZ.
- Do NOT modify any cron registration (Cowork's `mcp__scheduled-tasks__update_scheduled_task` does that — surface a one-line follow-up if the user wants schedule fires actually rescheduled, not just the displayed TZ).

**Why this exists:** product-level requirement (M's Apr 29 directive) — every Command Room workspace has one canonical timezone. Display and schedule TZ default to the same value at install (set in `command-room-onboarding` Phase 0 widget Q3). Users can change it later via this command. Plumbing that reads the setting: `plugin-source-v2/shared/scripts/tz.py` `load_workspace_tz()` + `to_local()`.

---

### "name my AI [name]" / "set my AI name to [name]" / "name my AI skip" (v3.13.8.2 — Bug #72)

Sets the customer-facing AI name (`workspace.brain_name`). Fresh-install M1 onboarding (2026-05-23+) captures this via Phase 0 widget Q4. Upgrade customers (v3.11.x → v3.13.8.x) need this command because the v3.13.8 release manifest didn't include the spec'd `brain_name_prompt` migration (Bug #72 — surfaced 2026-05-24 during v3.13.8 verification session).

**Trigger phrases (case-insensitive):** `name my AI [name]`, `name my ai [name]`, `set my AI name to [name]`, `set ai name to [name]`, `name my chief of staff [name]`, `name my AI Penelope`, `name my AI skip`, `skip naming my AI`, `name my AI no name`.

**Behavior:**

1. Parse `[name]` from the trigger. Special tokens: `skip` / `no name` / `decline` → record decline (don't set a name). Otherwise treat as the literal name value.
   - Reject if name is empty / whitespace-only / contains characters incompatible with chat copy (newlines, control chars). Surface plain English: *"That name has characters I can't use in chat. What name did you have in mind?"* and stop.
   - Reject if name length > 40 chars. *"Let's keep it short enough to fit in a signature line. What's a shorter version?"*

2. **For a set (not skip):**
   - Read `_hq/data/entities.json`. Find or create the `workspace` object at the top-level. Set `workspace.brain_name = "[name]"`. Bump version per the atomic-write contract. Write via `atomic_write_json`.
   - Append a `brain_name_captured` event to `_hq/data/events.jsonl` via `atomic_append_jsonl`:
     ```json
     {"type":"brain_name_captured","ts":"<ISO>","source_skill":"workspace-manager","data":{"brain_name":"<name>","via":"manual_trigger","prior_value":"<prior or null>"}}
     ```
   - Confirm in plain English: *"Got it — I'll go by `[name]` from here on. You'll see this in your scheduled briefings, brief signatures, and coach interactions. To change it later, just say `name my AI [new name]`."*

3. **For a skip / decline:**
   - Do NOT write `workspace.brain_name` to entities.json (leave it unset so future bridges know not to re-prompt).
   - Append a `brain_name_declined` event to `_hq/data/events.jsonl` via `atomic_append_jsonl`:
     ```json
     {"type":"brain_name_declined","ts":"<ISO>","source_skill":"workspace-manager","data":{"via":"manual_trigger"}}
     ```
   - Confirm in plain English: *"No problem — I'll stay as 'your Command Room' without a personal name. If you change your mind later, say `name my AI [name]` any time."*

4. **Idempotency:** If `workspace.brain_name` is already set AND the new name matches the existing one, the command is a no-op (still confirms in plain English so the user sees it took). If the new name differs from the existing one, that's a rename — update normally, but the confirmation phrasing changes: *"Renamed from `[old]` to `[new]`. Scheduled briefings and signatures will use the new name on their next fire."*

**Forbidden behaviors:**
- Do NOT touch entities.json blocks other than the `workspace` object.
- Do NOT modify historical event timestamps or rename brain_name in past events — `brain_name_captured` and `brain_name_declined` are append-only.
- Do NOT auto-substitute the name into prior briefing text or .docx artifacts retroactively — the field is read at render time by skills that need it, so the change propagates forward only.

**Read consumers (per master plan + M1 redesign + v3.13.8.4 personification sweep — these skills + surfaces read `workspace.brain_name` at render time via `shared/scripts/personification.py::get_brain_name(workspace_root)`, default "Penelope" if unset):**

Scheduled tasks + onboarding (original v3.13.8 / v3.13.8.2 set):
- All five scheduled-task orchestrators (Morning Brief, Upcoming Meetings, Past Meetings, Inbox, Friday Wrap) — signature line
- `command-room-coach` chat intro
- `command-room-onboarding` Phase 0–6 chat copy substitutions (`<BrainName>` template token)
- `m1-backfill-orchestrator.md` recap copy

Customer-facing chat + deliverables (v3.13.8.4 personification sweep):
- `workspace-manager` session-start confirmation (`let's work` / `I'm here` / `what's going on`) — open with "[Name] here." framing
- `workspace-manager` `go [project]` first-response header — author the loaded brief in voice
- `workspace-manager` `end session` summary — sign off the wrap-up
- `workspace-manager` `new project` confirmation ("Set up [X] for you" → "[Name] here — set up [X] for you")
- `morning-briefing` chat intro line (in addition to the signature)
- `call-prep` .docx cover line — "Prepared by [Name] for [M's first name]"
- `meeting-notes` acknowledgment line after processing a meeting
- `follow-up-ritual` draft signatures on outbound email drafts
- `cleanup` summary intro
- `decision-memo-composer` author byline in the .docx
- `board-pack-assembler` intro paragraph in the .docx
- `list-active` footer ("— [Name]")

Renames propagate forward only — no retroactive substitution into prior .docx artifacts or scheduled-task outputs already on disk. Every consumer reads fresh from entities.json on each render.

**Why this exists (v3.13.8.2 / Bug #72):** Master plan §5 spec'd a `brain_name_prompt` migration for the v3.13.8 update bridge, but the actual `shared/releases/v3.13.8.json` shipped 11 items and none was the brain-name prompt. Spec-vs-implementation drift caught during v3.13.8 verification — every v3.11.x → v3.13.8.x upgrade-customer kept the impersonal "Command Room" framing because the prompt never fired. v3.13.8.2 wires a new manifest item + this workspace-manager trigger to close the gap for both upgrade customers (via update-bridge prompt → user types this trigger → capture) and any direct customer-typed adoption.

---

## Master Tracker Auto-Updates

The MASTER_TRACKER.md is the single source of truth. It gets updated on these triggers:

| Trigger | What Updates |
|---------|-------------|
| End session | Last-touched dates, next actions, commitments, decision log, people |
| New project/exploring | New row added |
| Meeting processed | Next actions from meeting, new commitments, people, decision log |
| Audit run | Staleness flags, integrity issues noted |
| Archive | Row moved to archive section |
| Source check (during briefing) | Surfaces new info but does NOT auto-update tracker — presents to user first |

### Tracker Format (Essentials)

The tracker has these core sections: Active (Stage 3+), Scoping (Stage 2), Exploring (Stage 1), Inbox (Stage 0), Steady State (Stage 4), Completed Quick Tasks, Recently Archived, Commitment Tracking, and Staleness Rules.

For the full template with all section headers and example formatting, see references/workspace-detail.md → "Master Tracker Full Template" or the onboarding skill's templates.md.

---

## Strategic Advisor Mode

You're not just a tracker — you're a thinking partner. During briefings and end-session, offer observations grounded in real data from the workspace:
- Notice patterns and suggest proactive steps
- Flag imbalances in time/attention across projects
- Connect learnings across projects
- Challenge assumptions when the data warrants it

Never offer generic advice. All observations must tie to what's actually in the workspace and what's actually happened this session.

---

## Reactive Org Discovery (v2.3)

Org discovery is not onboarding-only. Whenever a new company / org name surfaces mid-session that doesn't resolve to any entry in `_hq/data/entities.json`, workspace-manager silently checks whether enough signal exists to propose an org record — so the org tree grows organically as the CEO works, not just at bootstrap.

**When it fires:**

- During "new project [Name]" connector scans (Step 3c above) if the scan surfaces a domain / Slack workspace / Drive folder not tied to any known org.
- During "what's going on" Step 2 connector reads, if an email domain or Slack workspace appears with ≥3 interactions in the last 30 days and doesn't map to an existing org.
- During fuzzy-router Layer 3 name resolution, when a mentioned name looks like a company (capitalized, multi-word, or matches a known-company pattern) but has no entry.
- Opt-out: never fires during "quick task" or "let's work" flows — those are intentionally silent.

**The signal check (all must run, in parallel, read-only):**

1. **Email domain cluster** — Gmail: count distinct sender/recipient addresses using the candidate domain over the last 60 days. Signal fires if ≥3 distinct senders OR ≥10 total messages.
2. **Slack workspace match** — if a Slack workspace name or the domain's alias matches the candidate, signal fires.
3. **Drive folder match** — if `[WORKSPACE_ROOT]/` contains a folder matching the candidate name OR a recently-modified Drive doc mentions the candidate in its title, signal fires.
4. **Calendar attendee cluster** — Calendar: count distinct attendees with the candidate domain over the last 90 days. Signal fires if ≥3 distinct people.
5. **Existing person records** — check `entities.json` for any `person_*` whose email domain matches the candidate. Signal fires if ≥1 exists.

**Decision rules:**

- **≥2 signals fired** → propose org creation to CEO inline:
  > "Noticed [CandidateOrg] showing up — email with 4 people there, a Slack workspace, and it's in your Drive. Want me to start tracking it? (y: yes, add it / p: yes, and it's one of yours / n: skip / later: ask me again next week)"
  On `y` or `p`: reserve `org_*` id, populate `canonical_name`, `domains[]`, `inferred_from[]` with the signal names that fired, set `is_primary_focus` from CEO answer, `relationship_type` = best-guess from signal mix (primary-focus → `operating`; email+calendar only → `client`; Slack workspace match → `partner`). On `n`: append to `_hq/ORG_DISCOVERY_SKIP.md` so the check doesn't re-fire for 90 days. On `later`: append to the cleanup queue.
- **1 signal fired** → silent — append to `_hq/ORG_DISCOVERY_QUEUE.md` with timestamp + signal type. cleanup reviews the queue; if the same candidate accumulates a second signal within 30 days, the audit surfaces it then.
- **0 signals fired** → it's just a name, not an org. Take no action.

**Writer Contract for this section:**
- Writes: `_hq/data/entities.json` (new `org_*` records with `pending_review: true`, cleared on CEO confirm), `_hq/data/aliases.json` (the candidate string + any discovered aliases), `_hq/ORG_DISCOVERY_SKIP.md`, `_hq/ORG_DISCOVERY_QUEUE.md`.
- Emits: one event with `type: "org_proposed"` per candidate (canonical top-level shape per `shared/data-schemas/events.schema.json` enum), with `data.signals[]` listing the evidence — for insight-generator and cleanup visibility.
- Dedup: never propose the same candidate twice within 30 days (check skip + queue files first).

**Why this is reactive, not predictive:**
We don't guess at orgs from a single ambiguous signal — false positives pollute the tree faster than missing orgs hurt. Two-signal threshold keeps proposals high-precision. The queue captures weaker signals for the audit to revisit, so nothing gets lost — it just waits for a second confirmation.

---

## Implicit Project Detection

If the user starts working on something without saying "go [project]" — for example, "I need to draft an email to Skyler about the Northstar proposal" — check whether the request matches an existing project name. This is a **name-lookup only** read (orientation only per `references/SOURCE_OF_TRUTH.md` — used solely to resolve "did the user mean an existing project?"). Look in: project folders on disk (canonical), `_hq/data/entities.json` thread `display_name` / `folder_name`, MASTER_TRACKER.md project rows (Tier 2 view, fine for name resolution), and `_hq/data/aliases.json`. The match-found branch immediately routes to `go [name]` whose own loading sequence reads canonical sources, so the name-lookup itself doesn't drive any state-decision here.

**If a match is found:** Confirm before loading context in one quick line:
> "That sounds like it's related to your [Project Name] project — want me to pull up the context?"

- **If yes** → Run the full "go [project]" flow, then help with the task using full context.
- **If no** → Help with the task as-is, no context loading.

**If no match is found:** Just help with the task directly.

**Rules:**
- Never silently load project context. Always confirm first.
- Never slow the user down. One quick question, not a multi-step interrogation.
- If the user is clearly asking a general question that mentions a project name in passing, use judgment — don't ask every time someone says a word that matches a folder name.
- If the user has already said "go [project]" this session, don't re-confirm for subsequent requests about the same project.

---

## Trigger Routing (Avoiding Skill Conflicts)

For skill routing rules when triggers overlap across the Command Room plugin's 10 skills, see references/workspace-detail.md → "Trigger Routing Table."

When workspace-manager is already active in a session (e.g., during a "go [project]" flow) and the user asks for meeting prep, handle it inline rather than trying to switch skills.

---

## Gotchas

- Never hardcode paths — always resolve from `[WORKSPACE_ROOT]`
- SESSION_NOTES files live at the project folder root, not in subfolders
- Don't create empty scaffolding — every file should have real content
- When in doubt about project status, ask — don't guess
- The tracker is markdown, not a database — keep it clean and readable
- **Source checks should be fast, not exhaustive.** Don't search every email — search for key contacts and project names. Summarize in 1-2 lines per finding. The briefing should take 30 seconds to read, not 5 minutes.
- **Never auto-update the tracker from source checks.** Surface what you find and let the user confirm. An email from Skyler doesn't mean the vendor quotes are done — maybe it's a question, not a delivery. Present, don't assume.
- **If a connector is disconnected, skip it silently.** Don't say "Gmail is not connected" every time — just work with what's available. Only mention missing connectors if the user asks why something wasn't caught.
- **Granola transcripts found during "end session" should be offered for processing, not auto-processed.** The user might want to handle them next session or skip them entirely.
- **Session notes are cumulative.** When appending during "end session," scan ALL previous open items and carry forward anything unfinished. Don't just capture the current session — carry the full picture.
- **End session safety net.** If the user appears to be wrapping up (says "thanks," "that's it," "I'm done," starts a completely new topic, or hasn't interacted in a while) WITHOUT saying "end session," gently prompt: "Before we move on — want me to save what we worked on? I'll update the tracker and session notes." This prevents the most common failure mode: the user closes the session and all context from the work is lost. Don't be pushy — one prompt is enough. If they ignore it, don't ask again.

---

## What It Doesn't Do

- Does not own per-person profiles — that's `people-crm` (external) or `team-intelligence` (direct reports).
- Does not generate pattern insights across the workspace — that's `insight-generator`.
- Does not produce the daily digest — that's `morning-briefing`.
- Does not process transcripts or URLs — routes to `meeting-notes` or `intel-intake`.
- Does not compose deliverables (decks, one-pagers, board updates) — routes to the composer skills.
- Does not audit workspace health — that's `cleanup`.
- Does not run migrations or workspace data ingest — that's `workspace-ingest` (which absorbed the legacy `migration-v2`), invoked once per workspace.
- Does not make decisions on the CEO's behalf — surfaces state, asks when ambiguous, routes when clear.
