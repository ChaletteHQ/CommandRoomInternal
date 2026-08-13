# Parser C — Custom Markdown (Pre-Plugin Hand-Built) → v2.x JSON

Handles workspaces that were built by hand before installing the Command Room plugin. Shape looks markdown-native (like v1.x) but diverges from plugin conventions: non-standard folder names (`_hq/` instead of `_hq/`), section-nested PEOPLE.md, no DECISION_LOG, decisions embedded in PROJECT_BRAIN / SESSION_NOTES / MASTER_TRACKER, project folders as siblings of the HQ folder instead of children.

Lifted from `migration-v2` Path C spec (see `Command Room/data/CommandRoom_PathC_Spec_2026-04-21.md`) and adapted to emit into workspace-ingest's shared collections.

---

## Shape assumptions

```
<source>/
  _hq/  (or _hq/ — either; pointer written to CLAUDE.md)
    MASTER_TRACKER.md              ← required
    PEOPLE.md                      ← required, SECTION-NESTED (not single table)
    PROJECT_BRAIN.md               ← HQ-level brain; has Decisions Log, Glossary, Commitments
    SESSION_NOTES_HQ.md            ← HQ-level sessions
    BUSINESS_CONTEXT.md            ← narrative only (preserved)
    HOLDCO.md                      ← org-tree seed (time-allocation signal)
    CLIENT_ROSTER.md               ← supplementary project signal
    WORKING_STYLE.md               ← narrative only
    PENDING_GLOBAL.md              ← commitments pool (optional)
    processed-meetings.json        ← optional; meeting backfill
    skills/**                      ← NOT PARSED (preserved in place)
    audit-reports/**, intel/**     ← NOT PARSED (preserved in place)
    _archive/**                    ← NOT PARSED (preserved in place)
  [Project Folder]/                ← sibling of _hq/, NOT child
    SESSION_NOTES_[NAME].md
    PROJECT_CONTEXT.md             ← narrative
    PROJECT_BRAIN.md               ← has Decisions Log + Commitments + Gotchas + Aliases
    SUBPROJECTS.md                 ← sub-thread records (optional)
    deliverables/**, data/**, ref/**  ← NOT PARSED
  CLAUDE.md                        ← preserved; pointer added
  SESSION_NOTES.md                 ← top-level (rare); treated as HQ supplementary
```

## Detection entry point

Orchestrator dispatches here when `_hq/MASTER_TRACKER.md` OR `_hq/MASTER_TRACKER.md` exists AND (`DECISION_LOG.md` missing OR PEOPLE.md is section-nested). See `../SKILL.md` → Phase 1 "Shape Detection".

## HQ path resolution

Determine the HQ folder name on entry:

1. If `_hq/` exists → HQ = `_hq/`.
2. Else if `_hq/` exists → HQ = `_hq/`.
3. Else flag mismatch and exit; shape detection shouldn't have routed here.

Record the detected HQ path; emit a pointer to the target workspace's `CLAUDE.md` after ingest:

```markdown
## Command Room — HQ Path

hq_folder: _hq
```

This is appended (not overwritten) to CLAUDE.md during orchestrator Phase 5. Plugin skills check this pointer before defaulting to `_hq/`.

---

## Phase-by-phase extraction

### P1. Parse HQ `PEOPLE.md` (section-nested) → people[] + seed orgs[]

**CRITICAL — Exhaustive iteration contract.** The most common P1 bug is under-extraction: parser samples a few `###` entries per section and moves on, leaving dozens of entries behind. This is not optional — every `###` heading in PEOPLE.md MUST produce exactly one `people[]` or `orgs[]` record (unless it's in a skip-section per the handling table below).

**Mandatory extraction algorithm:**

1. **Index pass (do this FIRST, before field extraction):**
   - Scan the entire PEOPLE.md top-to-bottom.
   - Enumerate every `^## ` heading and every `^### ` heading with their line numbers.
   - Emit a pre-parse map: `{section_header_line: [entry_line_1, entry_line_2, ...], ...}`
   - Log the total `###` count as `source_person_heading_count` for later verification.

2. **Section-scoped iteration:**
   - For each `## ` section, take the full range of `### ` entries UP TO the next `## ` boundary (not just the first or few). Every single `###` heading inside the range becomes a candidate record.
   - Do NOT sample, do NOT early-exit a section after the first few entries, do NOT skip entries because fields look sparse — emit a record even if only the `###` name is present.

3. **Per-entry extraction with defensive field defaults:**
   - An entry with ALL bullet fields missing or "—" is still a valid entity — emit the person record with whatever's available.
   - Empty Contact / Last Interaction / Role fields → null, NOT a skip trigger.
   - "TBD — Primary Contact" or similarly placeholder heading names → emit person with `"status": "active"`, `"needs_enrichment": true`, `"placeholder_reason": "<verbatim heading>"`, flag in INGEST_REPORT.
   - Entries listed under `## Data Gaps & Backfill Needed` are NOT entities — skip per the section handling table.

4. **Post-extraction verification (hard gate — blocks Phase 4 write):**
   - Count the `### ` entries actually extracted into `people[]` (excluding those from skip-sections).
   - Compare to `source_person_heading_count` minus skip-section heading count.
   - **If mismatch:** abort Phase 4 write, dump a diff to `INGEST_REPORT.md` listing every `###` heading in source vs every person record produced, and prompt the CEO: *"Parser extracted N of M person entries from PEOPLE.md. Missing: [list]. Re-run extraction, or proceed with partial?"*
   - This is fail-fast: better to halt than silently lose dozens of people.

**Section handling table** (section header → parser treatment):

| Section header | Treatment |
|---|---|
| `## Co-Owners & Partners` | person; `relationship_type = operating`; primary_org = Company field |
| `## Active Clients` | person; `relationship_type = client_contact`; primary_org = Company |
| `## Prospects & Deals` | person; `relationship_type = prospect` |
| `## [Project] Team` (e.g., "Acme Co Team") | person; `affiliation_ids[]` includes section-header org |
| `## Advisors & Connectors` | person; `relationship_type = advisory` |
| `## Software Vendor Contacts` | **person OR org** depending on name shape — if entry has human first+last name, person; if it's a company name, org with `scope = vendor` |
| `## Family/Personal` | person; mint personal org (`org_personal`) with `scope = other`, `scope_label = personal` |
| `## Referral Network` | person; `relationship_type = referral` |
| `## Data Gaps & Backfill Needed` | **skip** (not an entity record; log to warnings) |
| Any other section | person; flag section name as `source_section` on person record |

**Per-entry format** (inside each section):

```
### [Canonical Name] ([Alias if parens])
- **Company:** [Org Name]
- **Role:** [Role text]
- **How We Know Them:** [Free-form]
- **Projects:** [comma-sep project names]
- **Last Interaction:** [YYYY-MM-DD] — [channel] [description]
- **Key Notes:** [Free-form]
- **Contact:** [email and/or phone]
```

**Field extraction:**

- `person_id` = `person_NNN` monotonic
- `canonical_name` = text before parens in `### [Name]`
- `aliases[]` = parenthetical alternates + italicized variants in body
- `primary_org_id` = slug-resolved Company field
- `affiliation_ids[]` = [primary_org_id] + any org inferred from section header (e.g., "Acme Co Team" section → add `org_acme_company`)
- `role` = Role field
- `notes` = `"How We Know Them"` + `"Key Notes"` concatenated with ` — ` delimiter
- `first_contact` = null (best-effort: earliest date in the PEOPLE.md entry or oldest related event after P3)
- `last_interaction` = parse date from Last Interaction line
- `last_interaction_channel` = keyword extract from same line (Granola / Calendar / Email / Phone / Call / In-person / Slack)
- `email` / `phone` = regex from Contact line
- `status` = `"active"` unless the Data Gaps section or an "Archived" section
- `project_ids[]` = resolved from Projects field (comma-split, name-match against threads[] after P3)

**Org seeding:** for each unique Company or section-header-org, mint if not present:

- `org_id` = `org_<slug>`
- `canonical_name` = Company text (exact)
- `scope` = default `"operating"`; override to `"vendor"` for Software Vendor section, `"other"` + `scope_label: "personal"` for Family/Personal
- `relationship_type` = per section table above
- `is_primary_focus` = null (confirmed in onboarding Phase 2c)
- `parent_org_id` = null (connector pass + HOLDCO.md parse infer)
- `inferred_from[]` = `["people-md-section", "section-header-<name>"]`

### P2. Parse HQ `HOLDCO.md` → org tree seed + primary-focus hints

If HOLDCO.md exists, read section "Time Allocation" or similar table. Each row typically shows `Business | % of Time`. Highest-% entries hint at `is_primary_focus: true` candidates — but DO NOT set it here; surface as suggestions in INGEST_REPORT.

Also parse explicit parent/child statements in prose (e.g., "[Holding Co] (parent) — operating companies: Acme Property, Command Room"):

- Where prose clearly names a parent org and child orgs → set `parent_org_id` on each child.
- Confidence tag: `inferred_from += ["holdco-md-prose"]`.

### P3. Parse HQ `MASTER_TRACKER.md` → threads[] + commitment events

MASTER_TRACKER in custom shape typically has 5 stage tables + Commitment Tracking + Recently Archived. Apply the same v1.7-style extraction rules as Parser A's P2, with these custom-shape adjustments:

- **Folder resolution** — project folders are **siblings of the HQ folder**, not children. Search the source root directory (parent of HQ folder) for matching folder names. If the tracker row says `"Sam / Acme Co"` and folder is `Acme Co/`, emit alias and set `folder_name = Acme Co`.
- **Kind normalization** — custom shapes often use freeform Category values (`My Business`, `Client`, `Tool`, `Acquisition`, `Exploring`). Normalize per Parser A rules; add `kind = "acquisition"` for acquisition-flavored categories.
- **Owner default** — if the row has no explicit Owner, owner = the operator's person record (from `## Co-Owners & Partners` section, first entry).
- **Affiliation** — `affiliation_id` rules:
  - Client projects → the client's org (e.g., NorthStar project → `org_northstar`)
  - Own business projects → the operating org (e.g., Acme Property → `org_acme_property`, itself a child of `org_holding_co` after connector pass)
  - Internal HQ work → `org_holding_hq` (mint if not present, scope=operating, relationship_type=operating)

**Commitment Tracking table** → `commitment` events; pair with `commitment_resolved` events per Parser A rules.

### P4. Parse HQ `PROJECT_BRAIN.md` → HQ-scope decision/commitment events

HQ-level PROJECT_BRAIN typically has sections including:

- `## Decisions Log` — each dated line → `decision` event
- `## Key Facts` — **not parsed** (preserved as narrative context)
- `## Glossary` — term/definition pairs → aliases[] at confidence 0.7 where term matches an entity
- `## Commitments` / `## Open Commitments` — each bullet → `commitment` event with status=`open`
- `## Risks` / `## Gotchas` / `## Lessons` — **not parsed** (narrative context)

**Decisions Log entries:**

Format: `- YYYY-MM-DD: <decision text>` (often single-line; sometimes multi-line with `  - Because: <rationale>` follow-up).

Per decision:

- `primary_thread_id` = `thread_hq` (synthetic HQ thread — mint if not present; see P8)
- `related_thread_ids[]` = populate with any thread names mentioned in the decision text
- `classification_confidence` = 0.95 (PROJECT_BRAIN is a brain-dump, not a formal decision log)
- `data.title` = first 60 chars before `—` / `.` / `:` / newline
- `data.decision` = full line text
- `data.rationale` = text after `because`/`since` tokens if present; else null
- `person_ids[]` = [the operator's person_id]
- `org_ids[]` = [primary thread's affiliation] (typically `org_holding_co`)

### P5. Parse per-project `PROJECT_BRAIN.md` → thread-scope decision/commitment events

Same extraction rules as P4, but with:

- `primary_thread_id` = the project's thread_id
- `org_ids[]` = that project's affiliation
- `classification_confidence` = 0.95
- Glossary aliases get confidence 0.7 (same as P4)

Dedup decisions against P4 (HQ scope) and P6 (session notes) by (date, fuzzy-title-hash at 0.9 similarity).

### P6. Parse `SESSION_NOTES_*.md` (HQ + per-project) → events

HQ and per-project SESSION_NOTES get identical treatment:

**Session boundary patterns** (custom shape is looser than plugin shape):

- Primary: `^## Session NN — YYYY-MM-DD` or `^## Session — YYYY-MM-DD`
- Secondary: `^## YYYY-MM-DD` or `^### YYYY-MM-DD — <title>` or `^### Session NN`
- Fallback: any H2/H3 with a parseable ISO date → session boundary

**Sub-section extraction** (apply same table as Parser A's P5 + custom-shape additions):

| Sub-header | → event type |
|---|---|
| `**Context:**` / `### Focus:` / `## Context` | `data.summary` on the session's main event |
| `**Decisions:**` / `## Decisions Made` | `decision` event per bullet |
| `**Infrastructure changes:**` / `**Data fixes:**` / `**Shipped:**` | `interaction` event per bullet, channel=`"work-session"` |
| `**Deliverables created:**` | `interaction` event per bullet, channel=`"deliverable"` |
| `**Open items:**` / `## Open Items` | `commitment` event per bullet, status=`open` |
| `**Accomplished:**` / `**What's next:**` | one summary `note` event for the block |
| `### Session NN` / `### [Title]` fallback (no structured sub-sections) | single `meeting` event with whole-session text as summary — still built via `meeting_capture.build_meeting_event()` (empty binding is legal here: a work session has no invitees; the builder keeps the shape canonical) |

**Multi-thread events** (HQ SESSION_NOTES heavily): when an entry references multiple projects, populate `related_thread_ids[]` with resolved project ids. Primary = owning thread (HQ thread for HQ notes; project thread for project notes).

**Per-event fields:**

- `primary_thread_id` = owning thread
- `related_thread_ids[]` = secondary threads
- `classification_confidence` = 1.0 (historical ground truth)
- `person_ids[]` = M + resolved names
- `org_ids[]` = affiliation of primary + each related thread
- `ts` = session date
- `source_skill` = `"workspace-ingest"`
- DEPRECATED `project_id` mirror

**Compressed history blocks** (e.g., `## Compressed History (Sessions 1-4)`) → single `note` event with `data.text = <compressed block contents>`. Do NOT attempt reconstruction — compression is lossy by design. Flag in INGEST_REPORT.

### P7. Derive aliases from multiple sources

Custom shape has no dedicated ALIASES.md. Build aliases from:

1. **Parenthetical alternates in PEOPLE.md `### [Name] ([Alias])`** — confidence 1.0 (explicit).
2. **Glossary sections in all PROJECT_BRAIN.md files** — confidence 0.7 (glossary mention).
3. **`aka` / `also known as` / `fka` phrases in notes** — confidence 0.9.
4. **MASTER_TRACKER row names vs folder names** — when row `"Sam / Acme Co"` → folder `Acme Co/`, mint aliases `"Sam"` and `"Acme Co"` → thread, confidence 0.9.
5. **Email local-parts differing from canonical first-name** — confidence 0.9.

Unresolved aliases (raw without matching canonical_id) → log to INGEST_REPORT warnings.

### P8. Mint synthetic HQ thread

Custom-shape HQ work (HQ PROJECT_BRAIN decisions, HQ SESSION_NOTES sessions) needs a thread to attach to. If no explicit HQ thread exists in MASTER_TRACKER:

```json
{
  "thread_id": "thread_hq",
  "display_name": "[Company] HQ",
  "folder_name": "_hq",
  "kind": "operating",
  "stage": "active",
  "status": "active",
  "affiliation_id": "org_holding_co",
  "owner_person_id": "person_001",
  "stakeholder_person_ids": [],
  "first_seen": "<earliest HQ session date>",
  "last_activity": "<latest HQ session date>",
  "notes": "Synthetic HQ thread minted during ingest to anchor HQ-scope events.",
  "parent_thread_id": null,
  "spawned_from_thread_id": null,
  "cross_refs": []
}
```

### P9. Derive first_seen / last_interaction + run connector inference

Per Parser A's P7 + P8: compute `first_seen`/`last_interaction` across events per entity, then run `../SKILL.md` connector-assisted inference pass. Custom shape especially benefits from connector signals because HOLDCO.md and BUSINESS_CONTEXT.md contain prose that signals holding/operating relationships — the inference pass confirms those against connector evidence.

---

## Custom-shape quirks

- **Missing DECISION_LOG** — decisions live in PROJECT_BRAIN / SESSION_NOTES / MASTER_TRACKER. Aggregate across sources with fuzzy-hash dedup. Never assume a dedicated log exists.
- **PEOPLE.md section headers are the org signal** — "Acme Co Team" section == org_acme_company regardless of whether MASTER_TRACKER has a matching project.
- **Project folders as siblings of HQ** — always search the source root (parent of HQ folder), never `_hq/[project]/`.
- **"Sam / Acme Co" style row names** — slash-delimited display names encode the primary contact + company. Split at `/` and resolve each side; emit aliases.
- **Freeform Commitments lists** mixed with Done/Missed items — detect resolution markers (`✅`, `~~Done~~`, `[DONE]`, `[x]`) and emit paired `commitment_resolved` events.
- **BUSINESS_CONTEXT.md prose often encodes the org tree** — extract explicit parent statements (`"X is a subsidiary of Y"`, `"Z operating entities: A, B, C"`) and feed them into org tree before connector pass.
- **SUBPROJECTS.md** when present → child thread records with `parent_thread_id` set to the enclosing project thread. Each subproject row = one thread_id.
- **Cross-project references in HQ SESSION_NOTES are dense** — be aggressive about populating `related_thread_ids[]`. Fuzzy-match any capitalized project-name-shaped token against threads[].
- **processed-meetings.json if present** → backfill `meeting` events via `meeting_capture.build_meeting_event()` (BUG-8244 canonical binding: `person_ids` resolved + `data.attendees` emails + `data.attendees_external` names) + Granola links. Dedup against SESSION_NOTES-parsed meetings by date + title fuzzy-hash.
- **WORKING_STYLE.md, PENDING_GLOBAL.md, GOTCHAS.md at HQ level** — PENDING_GLOBAL.md contents → `commitment` events (status=open). Others narrative-only.

---

## Default decisions for parser ambiguities

These 10 defaults are lifted from the Path C spec (`CommandRoom_PathC_Spec_2026-04-21.md` → "Default Decisions for Parser Ambiguities") and apply in all custom-shape ingests unless overridden:

1. **Decision taxonomy (PROJECT_BRAIN vs SESSION_NOTES vs MASTER_TRACKER):** Dedup by date + fuzzy-title-hash across all three sources.
2. **Commitment dedup (tracker table vs Open Items lists):** Fuzzy match description + due_date; MASTER_TRACKER status wins on conflicts.
3. **Person vs Org:** Humans → person; companies/vendors/teams → org. "X Company Team" is a grouping, not an entity.
4. **Project boundary (folders with sub-folders):** Top-level folder = thread. Sub-folders = areas, unless SUBPROJECTS.md lists them as child threads.
5. **Session granularity:** One event per semantic item (a session with 3 decisions + 2 commitments = 5 events, not 1).
6. **Narrative-vs-structured contradictions:** Flag to INGEST_REPORT + CONFLICTS.md; MASTER_TRACKER structured status wins.
7. **Last Interaction channel:** Preserve as `channel` field on interaction events.
8. **Skill versioning:** Out of scope. Skills are code, not entities; not ingested.
9. **Archived projects:** Emit `status_change` event (active → archived) at archive date. Keep thread record with `status: archived`.
10. **Compressed session blocks:** Parse as single `note` event; accept lossy.

---

## Back-compat guardrails

- Custom HQ folder name (`_hq`) preserved — never renamed. Pointer written to `CLAUDE.md`.
- All custom skills (`_hq/skills/**`) untouched — they're code, not data.
- All project folders sibling to HQ untouched — only HQ folder gets new `data/` + `views/` subfolders written by orchestrator.
- Original MASTER_TRACKER.md / PEOPLE.md / PROJECT_BRAIN.md preserved — replaced at backward-compat paths only after orchestrator Phase 8 regenerates views (orchestrator's concern, not this parser's).

---

## Output hand-off

Parser C returns the five collections (orgs / people / threads / events / aliases) + an HQ-path-pointer side-channel record (HQ folder name) to orchestrator Phase 4. Orchestrator handles atomic writes, schema validation, and CLAUDE.md pointer append.
