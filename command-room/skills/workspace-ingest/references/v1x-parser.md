# Parser A — v1.x Plugin Workspace → v2.x JSON

Handles v1.4 / v1.7 / v1.8 plugin-built workspaces. All three variants share the same core markdown registry shape; this parser detects which variant is present and applies the right extraction rules.

Lifted from `migration-v2` Path A (Phases 2–6) and adapted to emit into the shared workspace-ingest collections (orgs / people / threads / events / aliases) defined in `../SKILL.md` → "Parser contracts".

---

## Shape assumptions

```
<source>/
  _hq/
    MASTER_TRACKER.md              ← required
    PEOPLE.md                      ← required (single-table shape)
    DECISION_LOG.md                ← required
    ALIASES.md                     ← usually present; fall back to glossary mining if missing
    BUSINESS_CONTEXT.md            ← narrative only (not parsed to events)
    COMMUNICATION_PROFILE.md       ← v1.7+ only (optional)
    CONFLICTS.md                   ← narrative only
    briefings/**                   ← preserved, not parsed
    audit-reports/**               ← preserved, not parsed
    intel/**                       ← preserved, not parsed
  [Project Folder]/
    SESSION_NOTES_[NAME].md        ← dated entries → events
    PROJECT_CONTEXT.md             ← narrative only
    PROJECT_BRAIN.md               ← v1.7+ only; has Decisions Log + Glossary
    SUCCESS_CRITERIA.md            ← optional; feeds thread.success_criteria
  .claude-plugin/plugin.json       ← version detection
  CHANGELOG.md                     ← version detection fallback
  WORKSPACE_SCHEMA.md              ← version detection fallback
```

## Variant detection

Run after `_hq/` is confirmed present:

1. **Read `.claude-plugin/plugin.json`** if present — `version` field wins. `1.4.x` → v1.4 branch; `1.7.x` → v1.7 branch; `1.8.x` → v1.8 branch.
2. **Fallback signals** if plugin.json missing or version parse fails:
   - `_hq/COMMUNICATION_PROFILE.md` present → v1.7 or later
   - Any project folder has `PROJECT_BRAIN.md` → v1.7 or later
   - `_hq/briefings/**` or `_hq/audit-reports/**` populated → v1.8
   - None of the above → v1.4
3. **Variant differences** the parser must handle:
   | Variant | MASTER_TRACKER shape | PEOPLE.md | DECISION_LOG | Extra files |
   |---|---|---|---|---|
   | **v1.4** | single active-projects table + archive section | Name / Role / Company / Last Interaction / Notes cols | one dated entry per line | — |
   | **v1.7** | stage-split tables (Active / Scoping / Exploring / Inbox / Steady State) + Commitment Tracking table + Recently Archived | adds Communication Style column | decision block (title + context + decision + rationale) | COMMUNICATION_PROFILE, PROJECT_BRAIN per project |
   | **v1.8** | v1.7 shape + project_class column + archive_reason column | adds Aliases column | v1.7 shape + alternatives bullet | adds SUCCESS_CRITERIA, briefings, audit-reports |

## Phase-by-phase extraction

### P1. Parse `_hq/PEOPLE.md` → people[] + seed orgs[]

**Shape-variant handling — DO THIS FIRST.** v1.x PEOPLE.md is *nominally* single-table, but users often migrate in with a section-nested shape (H2 section headers + H3 per-entry), especially for v1.4 installs that preceded the single-table convention. Detect:

1. Scan PEOPLE.md for `^| ` table-row patterns in the first 100 lines.
2. If found → single-table branch (rules below).
3. If not found but `^### ` entries under `^## ` section headers exist → **delegate to `custom-markdown-parser.md` P1** (section-nested rules apply; same exhaustive iteration + verification gate).
4. If neither → flag shape ambiguous; fall through to generic-fallback-parser.md.

Never run single-table extraction against a section-nested file — it silently drops entries because the row-regex misses the heading/bullet format. The parse completeness check (see SKILL.md Phase 3.5) will catch it but prevention is cleaner.

**Single-table format.** For each row:

- `person_id` = `person_NNN` (monotonic from 001)
- `canonical_name` = Name column
- `aliases[]` = v1.8 Aliases column OR parenthetical in Name (`[CEO] (M)` → aliases `["M"]`) OR empty
- `role` = Role column
- `email` / `phone` = parse from Contact column (regex `\S+@\S+` for email; digit pattern for phone)
- `primary_org_id` = slug-resolve Company column → `org_<slug>` (reuse if seen; mint if new)
- `affiliation_ids` = [primary_org_id]
- `status` = `"active"` unless the row is struck-through or appears in an "Archived" section
- `communication_style` = v1.7+ Communication Style column; else empty
- `first_seen` = parse from Notes if present; else null
- `last_interaction` = parse date from Last Interaction column (ISO if parseable)
- `last_interaction_channel` = extract channel keyword from Last Interaction (Granola/Calendar/Email/Phone/Call); else null
- `notes` = Notes column (truncate at 500 chars; store full in `raw_notes` if needed)
- `project_ids` = [] (backfilled in P3)

For each Company value not yet in orgs[], mint:

- `org_id` = `org_<slug>` where slug = lowercased, non-alphanum → `_`
- `canonical_name` = Company (exact text)
- `scope` = `"operating"` (default; refined in connector pass)
- `relationship_type` = inferred from Role text: contains "Co-Owner"/"Partner" → `operating`; "Client"/"Customer" → `client`; "Advisor" → `advisory`; "Board" → `board`; "Investor"/"LP" → `investment`; "Vendor"/"Contractor" → `vendor`; else null
- `is_primary_focus` = null (confirmed in onboarding Phase 2c)
- `parent_org_id` = null (connector pass infers)
- `inferred_from` = `["people-md"]`
- `domains`, `slack_workspace_ids` = [] (connector pass populates)
- `first_seen`, `last_interaction` = null (filled in P4 after event parse)

### P2. Parse `_hq/MASTER_TRACKER.md` → threads[]

Handle both single-table (v1.4) and stage-split-table (v1.7/v1.8) layouts.

**For v1.4 single-table:**

- All rows default to `stage = "active"` unless row is under an "Archived" heading → `stage = "archived"`.
- Struck-through rows (`~~Name~~`) → `status = "archived"`.

**For v1.7/v1.8 stage-split:**

- Read headings: `## Active Projects` / `## Scoping` / `## Exploring` / `## Inbox` / `## Steady State` / `## Recently Archived`.
- `stage` = heading name lowercased.
- `status` = `"active"` for Active / Scoping / Exploring / Steady State; `"paused"` for Inbox; `"archived"` for Recently Archived.

**Per-row extraction (all variants):**

- `thread_id` = `project_NNN` (monotonic)
- `display_name` = Project column
- `folder_name` = slug-resolve against source's folder list; if no folder matches, flag warning and set folder_name = display_name
- `kind` = v1.8 project_class column OR inferred from Category column OR default `"initiative"`. Normalize to v2.x taxonomy: Client → `"advisory"`, My Business → `"operating"`, Product → `"product"`, Tool → `"infrastructure"`, else `"initiative"`
- `last_activity` = Last Touched column (ISO parse)
- `first_seen` = null (inferred from earliest SESSION_NOTES entry in P4)
- `next_step` = Next Action column (full text; max 500 chars)
- `notes` = Priority + What's Interesting columns concatenated
- `owner_person_id` = resolve Owner/Lead by name → person_id (fuzzy match; accept first/last/alias)
- `stakeholder_person_ids[]` = resolve Stakeholders/Team column by name, comma-split
- `affiliation_id` = resolve Company/Client column → org_id; if blank AND owner has primary_org_id, use owner's primary_org_id; else flag warning and leave null
- `parent_thread_id`, `spawned_from_thread_id` = null (populated by CEO later)
- `cross_refs` = [] (CEO populates later)

**v1.7/v1.8 Commitment Tracking table** (if present):

Each row → `commitment` event (queued for P4):

```json
{
  "type": "commitment",
  "ts": "<Date Mentioned>",
  "primary_thread_id": "<resolved from Project col>",
  "classification_confidence": 1.0,
  "data": {
    "description": "<Commitment text>",
    "due_date": "<Due By | null>",
    "status": "<Open | Done | Partial | Missed>"
  }
}
```

For each `Done` / `Missed` row, also emit a paired `commitment_resolved` event with `ts = Due By` (or today if missing) and `data.resolution = <status>`.

**Strike-through archived rows:** emit `status_change` event with `data.from = "<prior stage>"`, `data.to = "archived"`, `data.reason = <archive_reason column if v1.8, else null>`.

**Back-links:** after all threads parsed, for each thread's stakeholders, append `thread_id` to each person's `project_ids[]`.

### P3. Parse `_hq/ALIASES.md` → aliases[]

File has three tables (People / Projects / Orgs). Each row:

```
| Raw | Canonical |
| --- | --- |
| M | [CEO] |
```

For each row:

- Resolve `canonical` text → `canonical_id` by matching against entities minted in P1/P2.
- Emit `{raw, canonical_id, confidence: 1.0, added_ts: <ingest_ts>, added_by: "workspace-ingest"}`.
- If canonical doesn't resolve, log a warning and skip (collected in INGEST_REPORT).

**If ALIASES.md is missing** (rare; some v1.4 installs skipped it): glossary-mine:

1. Scan each `PROJECT_BRAIN.md` `## Glossary` section for `Term — Definition` lines; where the term matches an existing entity name, mint alias at confidence 0.7.
2. Scan PEOPLE.md parentheticals and email local-parts (before `@`) that differ from canonical_name first-name → mint alias at confidence 0.9.

### P4. Parse `_hq/DECISION_LOG.md` → decision events

Format varies by variant:

- **v1.4:** one line per decision, `YYYY-MM-DD: <decision text>`.
- **v1.7/v1.8:** multi-line block per decision with subfields:
  ```
  ## YYYY-MM-DD — <title>
  - **Context:** ...
  - **Decision:** ...
  - **Rationale:** ...
  - **Alternatives:** ... (v1.8 only)
  - **Decided by:** ...
  ```

Per decision:

```json
{
  "seq": <next>,
  "ts": "<date>",
  "type": "decision",
  "source_skill": "workspace-ingest",
  "primary_thread_id": "<resolved from context/title/affiliation>",
  "related_thread_ids": [],
  "classification_confidence": 1.0,
  "person_ids": ["<decided_by resolution>"],
  "org_ids": ["<primary_thread's affiliation>"],
  "data": {
    "title": "<title | first 60 chars>",
    "context": "<context | null>",
    "decision": "<decision text>",
    "rationale": "<rationale | null>",
    "alternatives": ["<alternatives if v1.8 | []>"],
    "decided_by_person_id": "<resolved | null>"
  },
  "project_id": "<primary_thread_id>"
}
```

### P5. Parse per-project `SESSION_NOTES_[NAME].md` → events

For each project folder, open its SESSION_NOTES file. Split on dated session boundaries:

- Primary pattern: `^## YYYY-MM-DD` or `^## Session NN — YYYY-MM-DD`
- Fallback pattern: `^### YYYY-MM-DD` (older v1.4 style)

Within each session block, extract structured sub-sections:

| Sub-header pattern | → event type(s) |
|---|---|
| `**Context:**` / `**Focus:**` / `## Context` | stored as `data.summary` on the session's main event |
| `**Decisions:**` / `## Decisions` | one `decision` event per bullet |
| `**Commitments:**` / `**Open Items:**` / `## Action Items` | one `commitment` event per bullet with status=`open` |
| `**Completed:**` / `**Done:**` / `## Shipped` | one `commitment_resolved` event per bullet (attempt to match prior commitment by fuzzy description; else emit standalone) |
| `**Meeting notes:**` / `## Meeting` / attendee list present | one `meeting` event with `data.attendee_person_ids[]` + `data.summary` + `data.duration_min` (if parseable) |
| `**Status change:**` / explicit `paused → active` / etc. | one `status_change` event with `data.from`/`data.to` |
| `**Scope change:**` | one `scope_change` event |
| Free-form body with no sub-sections | one `interaction` event with `data.channel = "in-person"` (or inferred) + `data.summary` + `data.direction` |

**Every event carries:**

- `seq` = monotonic (reserve as parsed)
- `ts` = session date
- `source_skill` = `"workspace-ingest"`
- `primary_thread_id` = the owning thread of this SESSION_NOTES file
- `related_thread_ids[]` = threads named in the entry that aren't the primary (resolve by name/alias)
- `classification_confidence` = 1.0 (historical ground truth)
- `person_ids[]` = resolved from names in the entry (fuzzy match against people[] + aliases[])
- `org_ids[]` = affiliation of primary thread; add affiliation of each related thread if distinct
- DEPRECATED `project_id` = `primary_thread_id`

**Cross-references:** if an entry mentions another project name (fuzzy match against other threads), add that thread's id to `related_thread_ids[]`.

### P6. Parse `_hq/PROJECT_BRAIN.md` per project (v1.7+ only)

Where present, each project's PROJECT_BRAIN has these sections (non-exhaustive):

- `## Decisions Log` — each dated line → `decision` event with `classification_confidence: 0.95` (slight discount vs DECISION_LOG because PROJECT_BRAIN is freer-form). Dedup against P4 decisions by (date, fuzzy-title-hash).
- `## Glossary` — term/definition pairs → aliases (confidence 0.7) where term matches an entity
- `## Commitments` — open commitments → `commitment` events (dedup against MASTER_TRACKER Commitment Tracking)
- `## Gotchas` / `## Lessons` — preserved as narrative only; **not parsed to events** (they're context, not facts)
- `## Key Facts` — preserved; not parsed

### P7. Derive `first_seen` / `last_interaction` from events

After all events parsed, for each org and person:

- `first_seen` = min(ts) across events where this entity appears
- `last_interaction` = max(ts)

Update entities.json in-place before the atomic write in the orchestrator's Phase 4.

### P8. Connector-assisted inference

Per `../SKILL.md` → "Connector-Assisted Org Tree Inference". Populates `domains`, `slack_workspace_ids`, refines `scope` + `relationship_type`, detects `parent_org_id` for nested holdings.

If no connectors available, skip (source-parsed values stand).

---

## Known v1.x quirks

- **v1.4 DECISION_LOG one-liners** often lack project context. Resolve `primary_thread_id` by: (a) explicit project name in the line → match thread; (b) dominant project mentioned in adjacent MASTER_TRACKER update (same date) → use that; (c) else assign to a synthetic HQ thread (`thread_hq`) and flag warning.
- **v1.4 PEOPLE.md rows without Company** → affiliate to `org_personal` (scope=other, scope_label=personal) and flag.
- **v1.7 Commitment Tracking table sometimes empty** — skip cleanly; no error.
- **v1.8 project_class values outside taxonomy** (e.g., "Passion Project") → normalize to `initiative` and log mapping in INGEST_REPORT.
- **Mixed-variant installs** (e.g., user manually added PROJECT_BRAINs to a v1.4 workspace) — apply v1.7 extraction rules to files that exist; don't require every v1.7+ file to be present.
- **Folder renames after archive:** archived threads may have `folder_name` values that no longer exist on disk — flag as warning but keep the thread record.

---

## Back-compat guardrails

- Never delete v1.x source files — source folder stays pristine (orchestrator backs up to `_archive/`).
- Every thread record preserves `v1x_project_class` (from v1.8 `project_class`) for round-trip fidelity.
- Every person record preserves `v1x_communication_style` (v1.7+) even if downstream views don't use it.
- Deprecated mirrors: events emit `project_id` in addition to `primary_thread_id` so v1.x-era readers still parse.

---

## Output hand-off

Parser A returns the five collections (orgs / people / threads / events / aliases) to orchestrator Phase 4 for atomic write + schema validation. No direct file writes from this parser.
