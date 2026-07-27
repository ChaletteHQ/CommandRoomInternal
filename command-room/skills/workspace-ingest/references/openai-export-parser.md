# Parser E — OpenAI ChatGPT Export (v2.7.23+)

Handles ChatGPT export ZIPs that the user has unzipped into a folder. Specifically targets the data export bundle ChatGPT generates from `Settings → Data Controls → Export Data`. Pure chat-history extraction — pull people, projects, decisions, commitments, and conversation events out of `conversations.json`, plus optional memory text from `user.json`.

Lower confidence by design than v1.x / v2.x plugin parsers because chat content is noisy: a conversation about a hypothetical "Acme" example is indistinguishable from a real client named Acme without external corroboration. Confidence floors apply throughout. Everything questionable lands in INGEST_REPORT for the CEO to triage.

**Use case:** users migrating from ChatGPT to Claude / Cowork who want their accumulated context (people they've discussed, projects they've worked on, decisions they've made, ChatGPT memory) seeded into Command Room rather than starting cold.

---

## Entry point

Orchestrator dispatches here when shape detection finds OpenAI-export-shaped files in the source folder. See `../SKILL.md` → Phase 1 "Shape Detection" — Parser E is checked before Parser D's generic fallback.

Parser E announces its approach to the CEO before parsing:

> *"This looks like an unzipped ChatGPT export. I'll walk your conversation history and pull out: (a) people you've talked about with another person more than a few times, (b) project clusters in your chat titles, (c) decisions and commitments you stated in user messages, (d) any memory text from your account export. Chat content is noisy — I'll be conservative and flag everything for review in the INGEST_REPORT. Plan on spot-checking before trusting the entities."*

---

## Detection signals (orchestrator's Shape Detection Phase 1)

Source folder is OpenAI export-shaped if **at least one** of:

1. **`conversations.json` at source root** — the canonical export filename. Strongest signal.
2. **`chat.html` at source root** — present in every export, complementary to the JSON.
3. **`user.json` at source root** — user metadata + sometimes memory.
4. **`message_feedback.json` at source root** — message reactions data, only present in OpenAI exports.

Presence of `conversations.json` alone routes to Parser E. Presence of just `chat.html` + `user.json` (without `conversations.json`) is suspicious — surface a warning and ask the CEO if they extracted the export incompletely.

**Negative signal — do NOT route here if:** source folder also contains a `_hq/` directory or `MASTER_TRACKER.md`. That's a Command Room shape; Parsers A/B/C/D handle it. Don't double-process.

---

## Scope

**In scope (parsed):**

- `conversations.json` — primary input. Array of conversation objects.
- `user.json` — user metadata + memory (if present).
- `message_feedback.json` — optional, used to weight high-quality conversations.

**Out of scope (skipped, mentioned in report):**

- `chat.html` — the rendered HTML version is duplicated content from `conversations.json`.
- `model_comparisons.json` — A/B test data, irrelevant for entity extraction.
- `shared_conversations.json` — public-shared chats, redundant with `conversations.json`.
- Image attachments / file uploads — flagged but not migrated (they're inside the ZIP folder structure but don't map to Command Room's project folders).

---

## Conversations.json shape (reference)

The export file is an array of conversation objects. Each has:

```json
{
  "title": "Strategy doc for NorthStar Q2",
  "create_time": 1707891234.567,
  "update_time": 1707981234.567,
  "id": "abc-uuid",
  "current_node": "<node-uuid>",
  "mapping": {
    "<root-uuid>": {
      "id": "<root-uuid>",
      "message": null,
      "parent": null,
      "children": ["<child-uuid>"]
    },
    "<child-uuid>": {
      "id": "<child-uuid>",
      "message": {
        "id": "...",
        "author": {"role": "user", "name": null},
        "create_time": 1707891234.567,
        "content": {"content_type": "text", "parts": ["Hey, can you help me think through Q2 margins for NorthStar?"]},
        "status": "finished_successfully"
      },
      "parent": "<root-uuid>",
      "children": ["<assistant-uuid>"]
    },
    ...
  }
}
```

The `mapping` is a tree keyed by node UUID. Walk from the root (node with `parent: null`) following `children[]` to reconstruct the conversation. Each `message.content.parts` is an array of strings — concatenate for the message body. `message.author.role` is `user` / `assistant` / `system` / `tool`. The user's stated content lives in messages where `author.role == "user"`.

Timestamps are unix epochs (float seconds). Convert to ISO 8601 for events.jsonl.

---

## Extraction passes (run in order)

### Pass 1: Inventory the export

1. Validate `conversations.json` exists and parses as a JSON array of objects with `mapping` field.
2. Read `user.json` if present — capture `user.email`, `user.name`, and any `memory` field (some exports include extracted memory text; older exports don't).
3. Read `message_feedback.json` if present — build a set of `message_id`s with positive feedback (these are higher-signal messages; weight their content extractions slightly higher).
4. Count total conversations, total messages, date range (min `create_time` → max `update_time`). Surface in INGEST_REPORT.

### Pass 2: Walk every conversation, extract message text

For each conversation object:

1. Find the root node (node with `parent: null`) in `mapping`.
2. DFS down `children[0]` chain (or any deepest path — ChatGPT export typically has a linear conversation, but branching exists for "Edit and submit" rewrites; pick the most-recent branch).
3. For each visited message node where `message != null` AND `message.author.role` ∈ `{user, assistant}`:
   - Concatenate `message.content.parts` (skip if `content_type != "text"` — ignore image/file message types for entity extraction).
   - Capture `author.role`, `create_time`, the joined text.
4. Output a flat per-conversation record:

```json
{
  "conversation_id": "abc-uuid",
  "title": "Strategy doc for NorthStar Q2",
  "create_time": "2026-02-14T10:13:54Z",
  "update_time": "2026-02-15T08:33:54Z",
  "user_messages": ["Hey, can you help me think through Q2 margins for NorthStar?", ...],
  "assistant_messages": ["Sure — here's a framework...", ...],
  "user_text": "<all user messages joined>",
  "all_text": "<user + assistant joined chronologically>"
}
```

This intermediate structure is the input for Pass 3 onward. Don't write it to disk; keep in memory.

### Pass 3: People extraction (entity discovery — high-noise zone)

The hardest extraction. Chat content mentions LOTS of names — most of them fictional examples ChatGPT generated, hypothetical prospects, names from articles being discussed, etc. Apply aggressive filters:

**Step 3a — extract candidate names.** Across all conversation `all_text` blobs, regex-scan for capitalized name patterns: `^|\s([A-Z][a-z]{2,15})( [A-Z][a-z]{2,15})?\s|$` (one or two-token names). Build a frequency map: `{name: [conversation_id, ...]}`.

**Step 3b — apply confidence filters.** A candidate name becomes a `people[]` record only if **all** of:

- Mentioned in **3+ distinct conversations** (filters out one-off article references).
- Appears in **user messages** (not only assistant messages — the assistant invents names; the user names real people).
- Token doesn't match common-noun pattern: not `["Today", "Tomorrow", "Monday", ...weekday names, "January", ...month names, "Q1", "Q2", ...]`.
- Token doesn't appear in the OpenAI fictional-name blocklist (curated): `Aria, Bowie, Carol, Reed, Eve, Frank, Grace, Heidi, Olga, Judy, Mallory, Niaj, Oscar, Peggy, Rupert, Sybil, Trent, Victor, Walter, ...` — common cryptography/example names ChatGPT gravitates to.
- If connectors are available (Gmail, Calendar): cross-check — does this name appear as a contact, sender, recipient, or attendee anywhere in the connector data? If yes, confidence boost. If no, **default confidence 0.4** and flag for INGEST_REPORT review.

**Step 3c — confidence assignment:**

| Signal | Confidence |
|---|---|
| 5+ conversations + appears in connector contacts | 0.9 |
| 5+ conversations, user messages, no connector match | 0.6 (flag) |
| 3-4 conversations + connector match | 0.7 |
| 3-4 conversations, no connector match | 0.4 (flag heavily) |
| 1-2 conversations | drop entirely; not enough signal |

**Step 3d — minted person record:**

```json
{
  "person_id": "person_NNN",
  "canonical_name": "Mira Sample",
  "aliases": ["Mira"],
  "role": "",
  "email": "<from connector cross-ref if found, else empty>",
  "primary_org_id": "<from connector cross-ref or empty>",
  "first_contact": "<earliest conversation create_time mentioning them>",
  "last_interaction": "<latest>",
  "notes": "Mentioned in N conversations from ChatGPT export between [date range].",
  "confidence": 0.6,
  "extraction_source": "chatgpt-export"
}
```

The `notes` field is critical — it tells the CEO where this person came from and why confidence is low. They can delete in one line if it's a false positive.

**Hard cap:** **20 people maximum** from a ChatGPT export, sorted by confidence descending. More than that is almost certainly noise; surface a one-line note: *"I capped people extraction at 20 to keep noise low. Run scan with `--people-cap=50` if you want more — but expect the long tail to be junk."* (The flag is a future feature; for now just cap at 20 and explain in the report.)

### Pass 4: Project / topic clustering

Conversations with related titles often cluster around a real project or topic. Approach:

**Step 4a — title tokenization.** For each conversation, lowercase the title and tokenize on whitespace + punctuation. Drop stopwords (the, a, an, for, of, with, etc.) and ChatGPT-generic words (chat, conversation, help, question, idea, brainstorm, draft).

**Step 4b — cluster by shared meaningful tokens.** Group conversations by their largest shared n-gram (2-3 tokens) appearing in 3+ titles. E.g., *"NorthStar Q2 margins"*, *"NorthStar margin analysis"*, *"NorthStar margins update"* → cluster on `["northstar", "margin"]` → likely project name "NorthStar".

**Step 4c — cluster confidence:**

| Signal | Confidence |
|---|---|
| 5+ conversations sharing a multi-token cluster + cluster name appears as a connector folder/channel | 0.8 |
| 5+ conversations clustered, no connector match | 0.5 (flag) |
| 3-4 conversations clustered + connector match | 0.6 |
| 3-4 conversations clustered, no connector match | 0.3 (drop or flag aggressively) |

**Step 4d — minted thread (project) record:**

```json
{
  "thread_id": "project_NNN",
  "display_name": "NorthStar margin analysis",
  "folder_name": "NorthStar",
  "kind": "advisory",
  "stage": "exploring",
  "status": "active",
  "affiliation_id": null,
  "owner_person_id": "<user's person_id>",
  "first_seen": "<earliest cluster conversation create_time>",
  "last_activity": "<latest>",
  "notes": "Inferred from N ChatGPT conversations sharing title pattern '[cluster tokens]'. Not yet confirmed.",
  "confidence": 0.5,
  "extraction_source": "chatgpt-export"
}
```

Default `stage: "exploring"` — these are CEO-thinking-about-it conversations, not necessarily live work. Default `kind: "advisory"` (least committal). The CEO confirms / adjusts in onboarding Phase 2c if they re-run onboarding after ingest, or manually via `new project` lifecycle commands.

**Hard cap:** **15 projects maximum** from ChatGPT export. Same rationale as people cap.

### Pass 5: Decisions and commitments (user-stated only)

Scan **user messages only** (not assistant — the assistant suggests; only user statements count as decisions/commitments). For each user message text:

**Decision patterns** (case-insensitive substring match):

- `i decided to`
- `i'm going with`
- `we're going with`
- `let's go with`
- `we're moving forward with`
- `final decision:`
- `decided:`

**Commitment patterns:**

- `i'll [verb]` (forward-looking)
- `i'll send`
- `i'll get back to`
- `i'll have it`
- `i owe you`
- `by [day-of-week or date]` adjacent to user-stated commitment language

For each match:

1. Extract the surrounding sentence (or up to 200 chars context).
2. Try to resolve any person name in the sentence to an existing `people[]` entry — if matched, attach `person_ids[]`.
3. Try to resolve project association to existing `threads[]` via the conversation's project cluster (if any) — if matched, attach `primary_thread_id`.
4. Confidence:
   - Decision/commitment language unambiguous + person + project resolved → 0.7
   - Pattern matched but person and project unresolved → 0.4 (flag)
   - Pattern matched but the surrounding context is hypothetical (e.g., the user wrote "if I decided to X, then Y" — `if` upstream) → drop entirely

**Output one event per qualifying match:**

```json
{
  "seq": <next>,
  "ts": "<conversation create_time>",
  "type": "decision",
  "source_skill": "workspace-ingest",
  "primary_thread_id": "<resolved or null>",
  "person_ids": ["<resolved or empty>"],
  "classification_confidence": 0.7,
  "data": {
    "title": "<extracted sentence, ≤120 chars>",
    "context": "<surrounding 200 chars>",
    "decided_by_person_id": "<user's person_id>",
    "extraction_source": "chatgpt-export",
    "source_conversation_id": "<conversation uuid>",
    "source_conversation_title": "<title>"
  }
}
```

Same shape for commitment events with `type: "commitment"` per `shared/COMMITMENT_SCHEMA.md` (note: extracted commitments are status `open` by default; the parser does NOT compute overdue from chat-stated due dates because the dates may be relative ("by Friday") and ambiguous about which Friday).

**Hard cap:** **30 decisions + 30 commitments maximum.** ChatGPT users often verbalize lots of "I'll do X" while brainstorming; only the highest-confidence subset has real-world value. Sort by confidence descending, take top 30 of each.

### Pass 6: Memory extraction (if `user.json` includes it)

Some OpenAI exports include extracted memory text under `user.memory` or `user.profile`. Check both keys.

If memory text is present:

1. Append the verbatim memory text to a new `_hq/_ingested-chatgpt-memory.md` file with a header:

   ```markdown
   # ChatGPT memory — ingested [YYYY-MM-DD]

   The text below is verbatim from your ChatGPT account export (`user.json`). Review and copy whatever's still relevant into `_hq/BUSINESS_CONTEXT.md` and your `CLAUDE.md` Preferences section. This file is reference-only — Command Room doesn't read it on session start.

   ---

   <verbatim memory text>
   ```

2. Surface in INGEST_REPORT under "ChatGPT memory captured": *"Memory text from your ChatGPT export saved to `_hq/_ingested-chatgpt-memory.md`. Walk through it once and migrate the still-relevant parts to BUSINESS_CONTEXT.md."*

If `user.json` doesn't have a `memory` field (older exports don't), skip silently.

### Pass 7: Per-conversation event minting (lightweight, optional)

For every conversation with **5+ user messages** AND **a project cluster resolved** (Pass 4), emit one `interaction` event:

```json
{
  "seq": <next>,
  "ts": "<conversation create_time>",
  "type": "interaction",
  "source_skill": "workspace-ingest",
  "primary_thread_id": "<resolved cluster project>",
  "person_ids": ["<user's person_id>"],
  "classification_confidence": 0.5,
  "data": {
    "channel": "chatgpt",
    "title": "<conversation title>",
    "duration_proxy": "<count of messages>",
    "summary": "<first user message, ≤300 chars>",
    "extraction_source": "chatgpt-export",
    "source_conversation_id": "<conversation uuid>"
  }
}
```

Skip conversations with <5 user messages (too short to be meaningful) and conversations not resolved to a project cluster (interaction event with no thread_id is noise).

**Hard cap:** **50 interaction events** per ingest. Sort by `update_time` descending; take the most-recent 50. Older conversations get a one-line summary in INGEST_REPORT but no event.

### Pass 8: Aliases

Aliases come from name-alternation observed in user messages. E.g., the user writes both *"Mira"* and *"Mira Sample"* across different conversations referring to the same person → alias `Mira → person_NNN`.

**Approach:**

For each `people[]` record produced in Pass 3, scan user messages for short-form alternates (single first name, initials, common nicknames):

- If `canonical_name` is "Mira Sample" and "Mira" appears in same conversations → emit alias `{raw: "Mira", canonical_id: <person_id>, confidence: 0.7}`.
- Single-letter or two-letter initials (M, MP) — emit only if appearing in a context that disambiguates (e.g., a user message ending with "— M" as a sign-off mimicking email style).

**Hard cap:** **2 aliases per person.** Aliases generated by chat are inherently lower confidence than explicit-from-source aliases (Parser A/B); don't over-mint.

---

## File migration (Phase 7.5 / Phase 8 of orchestrator)

**Conversations themselves do NOT migrate as files** to project folders. Chat history isn't a deliverable; it's source material that already produced entities and events. Migrating the JSON or HTML rendering would just clutter the workspace.

**`user.json`'s memory text** does migrate as `_hq/_ingested-chatgpt-memory.md` (handled in Pass 6 above, not the standard migration loop).

**Image / file attachments inside the export folder** (typical export ZIP includes a subfolder of attached images/files): flag in INGEST_REPORT but do NOT auto-migrate. The CEO can decide later if any of those attachments belong in a project's `deliverables/` folder. Treat as out-of-scope.

**The original export ZIP** stays at `_archive/ingest_source_YYYY-MM-DD/` per the standard backup pattern. CEO can re-extract or delete.

---

## INGEST_REPORT addendum (Parser-E-specific)

Parser E adds a section to INGEST_REPORT.md beyond the standard counts:

```markdown
## ChatGPT export details

**Export date range:** [earliest conversation] → [latest conversation]
**Total conversations:** N
**Total messages parsed:** N (user: N, assistant: N)
**Conversations with 5+ user messages:** N (these became interaction events)

### Extraction confidence distribution

- People (capped at 20): high-confidence N, medium N, low (flagged) N
- Projects (capped at 15): high-confidence N, medium N, low (flagged) N
- Decisions (capped at 30): high-confidence N, medium N, low (flagged) N
- Commitments (capped at 30): high-confidence N, medium N, low (flagged) N
- Interactions (capped at 50): all confidence ≤ 0.5 by design

### What you should review

Chat content is noisy. Spot-check before trusting:

1. **Walk `_hq/data/entities.json` people array.** Anyone whose `notes` field starts with "Mentioned in N conversations from ChatGPT export" — confirm they're real and you actually work with them. Delete anyone who isn't real (cleanest fix: edit entities.json directly, the next view-regen sweep removes them from PEOPLE.md).

2. **Walk `_hq/data/entities.json` threads (projects) array.** Same — anyone whose `notes` field starts with "Inferred from N ChatGPT conversations" needs your confirmation. Default stage is "exploring" — promote to "active" only if it's live work.

3. **Walk decisions and commitments in events.jsonl.** Filter `extraction_source == "chatgpt-export"`. These are "I decided" / "I'll do X" statements you made in chat — some are real plans, some were brainstorming-aloud. Delete the brainstorms.

4. **Read `_hq/_ingested-chatgpt-memory.md` if present** and copy relevant sections to `_hq/BUSINESS_CONTEXT.md` + `CLAUDE.md` Preferences.

### Caveats

- Names from fictional examples in chat conversations may have leaked through Pass 3's filters. The OpenAI-fictional-name blocklist catches the obvious ones (Aria/Bowie/etc.); custom-domain fictionals (industry-specific examples ChatGPT generated) might not be filtered.
- Hypothetical decisions ("if I decided to X") may have leaked through Pass 5's heuristic. Spot-check the surrounding context.
- The interaction events (Pass 7) carry confidence 0.5 by design — they're context, not authoritative history. Don't rely on them for time-sensitive queries until you've confirmed the underlying project clusters are real.
```

---

## What Parser E does NOT do

- **Does not write `chat.html` or `model_comparisons.json` content** to the workspace. Those files are skipped (the JSON is duplicated content; the comparisons are A/B-test data with no Command Room value).
- **Does not migrate image attachments** from the export. Flagged for CEO review only.
- **Does not assign `email` to extracted people** unless a connector cross-ref provides it. The export doesn't include email addresses for people the user mentioned in chat.
- **Does not auto-confirm extracted entities.** Default confidence is low and everything goes through onboarding's Phase 2c confirmation if onboarding re-runs after ingest, or stays low-confidence in entities.json until the CEO edits them.
- **Does not extract assistant-suggested entities.** Only user-stated content counts. The assistant invents names, decisions, and projects in the course of helping the user think; those are not real entities.
- **Does not handle non-English chat content** specially. Pattern matching is English-only (decision/commitment phrases, name-pattern regex). Multi-language exports may under-extract; flag in report.
- **Does not deduplicate against pre-existing entities.json content.** If the user has already onboarded and built entities, then runs `workspace-ingest` against a ChatGPT export, Parser E refuses (per the orchestrator's idempotency rule — see SKILL.md). Run on a fresh workspace OR after explicitly clearing entities, OR ChatGPT-export ingest happens BEFORE onboarding.

---

**End of Parser E spec.** Returns control to orchestrator Phase 4 (write JSON sources) with `orgs[]` (empty for ChatGPT export — no org structure in chat), `people[]`, `threads[]`, `events[]`, `aliases[]`. Orchestrator writes them per the standard contract.
