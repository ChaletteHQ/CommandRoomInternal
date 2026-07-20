# Data Contract — Command Room Workspace (v2.2)

**Version:** 2.2
**Companion to:** `shared/WORKSPACE_API.md` (writer rules), `shared/data-schemas/` (JSON Schemas), `references/VIEW_GENERATION.md` (how markdown views are regenerated), `references/ORG_AND_THREAD_MODEL.md` (org tree + thread kinds)
**Supersedes:** v1.8, v2.0, v2.1 DATA_CONTRACT.md

In v2.2, context is **JSON-first with a nested org tree and multi-thread events**. The source of truth for every person, thread (formerly "project"), org, decision, meeting, and commitment is a small number of structured files under `_hq/data/`. Markdown files that skills and users read (MASTER_TRACKER.md, PEOPLE.md, DECISION_LOG.md, ALIASES.md) are **regenerated views** — always current, never edited by hand.

**What changed in v2.2 vs v2.0/v2.1:**
- **Orgs are nested.** Every org has `parent_org_id`, `scope` (holding/operating/division/brand/fund/other), `relationship_type`, and `is_primary_focus`. Holdings roll up operating children. No single reserved home org — the tree replaces it.
- **Events carry multiple threads.** `primary_thread_id` + `related_thread_ids[]` + `classification_confidence` replace the single `project_id`. Every classified event has a confidence score (0.0–1.0) and a cross-ref map explaining why related threads were included.
- **Classification is a first-class loop.** Reclassifications are new events with `supersedes_seq` — prior events are never mutated. `classifier_feedback.jsonl` is an append-only learning signal consumed by future classification passes.
- **Append-only is enforced.** `events.jsonl` and `classifier_feedback.jsonl` are verified against a rolling tail hash on every audit run.

**Writer Contract signatures remain stable.** Skills written against v2.0/v2.1 rules continue to work in v2.2 — the writer helper underneath routes writes to the new shape. `project_id` is preserved as a DEPRECATED mirror of `primary_thread_id` on v2.2 events for one migration cycle.

---

## Why JSON + events.jsonl

Three reasons:

1. **Single canonical shape per entity.** A person is described once in `entities.json`, not repeated across four markdown files that can drift.
2. **Append-only event log enables replay.** Every meeting, decision, commitment is a row in `events.jsonl` that can be walked forward or backward. Views are projections of the event stream over the entity state.
3. **Portable off Claude.** Any future runtime (Electra, a non-Claude model, custom tooling) can consume the same JSON + JSONL without needing to parse markdown heuristically.

Users never touch these files. Skills write through the API. Markdown views are generated for human reading and skill convenience.

---

## Storage Layout

```
_hq/
├── data/
│   ├── entities.json            # canonical registry: people, threads, orgs (nested tree)
│   ├── events.jsonl             # append-only event log (multi-thread + confidence)
│   ├── aliases.json             # raw → canonical mappings
│   ├── classifier_feedback.jsonl # append-only learning signal (Pass 8 review actions)
│   └── .lock                    # soft lock file (advisory, checked by writer helper)
├── views/                       # regenerated markdown, never edited by hand
│   ├── MASTER_TRACKER.md        # projected from entities.json (threads) + events.jsonl (activity), org-tree grouped
│   ├── PEOPLE.md                # projected from entities.json (people) + events (interactions), primary-org grouped
│   ├── DECISION_LOG.md          # projected from events.jsonl (type=decision)
│   ├── ALIASES.md               # projected from aliases.json
│   ├── ORG_TREE.md              # projected from entities.json (orgs) — nested visualization
│   ├── TIMELINE.md
│   ├── RELATIONSHIPS.md
│   ├── AGING.md
│   ├── DORMANT.md
│   └── THEMES.md
├── BUSINESS_CONTEXT.md          # free-form narrative, still markdown-native (rarely changes)
├── CONFLICTS.md                 # append-only, still markdown
├── briefings/
├── summaries/
├── audit-reports/
├── intel/
└── .write-log.md                # optional audit log
```

**Key rule:** the files under `_hq/views/` are outputs, not inputs. Skills that need to display or quote from them should still read them (it's faster and human-readable). Skills that need to **make changes** go through the JSON source.

### Backward compatibility

For ease of migration and user habit, `_hq/MASTER_TRACKER.md` / `_hq/PEOPLE.md` / `_hq/DECISION_LOG.md` / `_hq/ALIASES.md` remain at their original paths as **copies** of the files in `_hq/views/`. User queries for these files succeed exactly as in v1.8. The writer helper keeps them in sync.

---

## `_hq/data/entities.json`

Canonical registry of all entities. Full schema: `shared/data-schemas/entities.schema.json`.

**Top-level shape:**

```json
{
  "version": 42,
  "last_updated": "2026-04-20T14:32:00",
  "last_writer": "meeting-notes",
  "entities": {
    "people": [ { "id": "person_042", ... } ],
    "threads": [ { "id": "project_008", ... } ],
    "orgs": [ { "id": "org_acme_co", ... } ]
  }
}
```

Note: the key is `threads` in v2.2, but thread IDs retain the `project_` prefix for stability. User-facing language is "thread"; the ID prefix is schema-internal.

### Person record

```json
{
  "id": "person_042",
  "canonical_name": "Sam Sample",
  "aliases": ["Sammy", "SS", "@ssample"],
  "role": "COO at Acme Restaurant",
  "primary_org_id": "org_acme_restaurant",
  "org_ids": ["org_acme_restaurant", "org_acme_co"],
  "email": "john@example.com",
  "thread_ids": ["project_008", "project_014"],
  "first_seen": "2026-02-14",
  "last_interaction": "2026-04-18",
  "communication_style": "Direct, prefers bullets, skimmed reader",
  "reports_to_id": null,
  "status": "active",
  "notes": "Primary contact for sourcing bot rollout",
  "inferred_from": ["email_domain", "calendar_attendee"]
}
```

**Required:** `id`, `canonical_name`, `first_seen`.
**IDs are stable and never reused.** Format: `person_` + zero-padded integer (minimum 3 digits). Monotonic; the writer helper reserves the next id before creating a record. `primary_org_id` points to the most specific operating org; `org_ids[]` includes every org the person is associated with (useful for advisors, board members, people who span holding + operating).

### Thread record (project_NNN)

```json
{
  "id": "project_008",
  "folder_name": "Acme Co/sourcing-bot",
  "display_name": "Sourcing Bot Rollout",
  "kind": "initiative",
  "affiliation_id": "org_acme_restaurant",
  "parent_thread_id": null,
  "spawned_from_thread_id": null,
  "cross_refs": [],
  "status": "active",
  "stage": 3,
  "owner_person_id": "person_042",
  "stakeholder_person_ids": ["person_042", "person_051"],
  "last_activity": "2026-04-18",
  "next_step": "Finalize supplier onboarding spec",
  "success_criteria": [
    "Supplier matching accuracy >90%",
    "Sam's office approves pilot by May 15"
  ],
  "first_seen": "2026-02-14",
  "archived_at": null,
  "archive_reason": null
}
```

**Required:** `id`, `folder_name`, `kind`, `affiliation_id`, `status`, `first_seen`.
`kind` ∈ {`initiative`, `deal`, `advisory`, `investment`, `board`, `relationship`, `theme`, `concern`, `ritual`, `personal`, `other`}.
`affiliation_id` is the most specific operating org; the tree walk via `parent_org_id` resolves holding context.
**`folder_name` must match an actual folder on disk** (workspace root, case-sensitive). `cleanup` verifies this every run.
**`last_activity` is DEPRECATED (v4.5.2, F-54/F-61):** no code maintains it — an unmaintained ingest-era stamp. Never rank or compute staleness from it; derive recency from events at read time via `shared/scripts/thread_activity.py` (zero-event threads may read it as a floor only). Full rule in `ORG_AND_THREAD_MODEL.md`.

### Org record (nested tree)

```json
{
  "id": "org_acme_co",
  "canonical_name": "Acme Co",
  "aliases": ["AcmeCo", "Acme Co"],
  "parent_org_id": null,
  "scope": "holding",
  "relationship_type": "operating",
  "is_primary_focus": true,
  "domains": ["acme.example.com"],
  "slack_workspace_ids": [],
  "status": "active",
  "inferred_from": ["user", "email_signature"],
  "notes": null
}
```

Operating child example:

```json
{
  "id": "org_acme_restaurant",
  "canonical_name": "Acme Restaurant",
  "aliases": [],
  "parent_org_id": "org_acme_co",
  "scope": "operating",
  "relationship_type": "operating",
  "is_primary_focus": true,
  "domains": ["acmerestaurant.example.com"],
  "slack_workspace_ids": ["T0ABC123"],
  "status": "active",
  "inferred_from": ["email_domain", "slack_workspace"],
  "notes": null
}
```

**Required:** `id`, `canonical_name`, `scope`, `relationship_type`, `is_primary_focus`, `status`.
`scope` ∈ {`holding`, `operating`, `division`, `brand`, `fund`, `other`}.
`relationship_type` ∈ {`operating`, `partner`, `board`, `advisory`, `investment`, `client`, `portfolio_company`, `beneficiary`, `other`}.
`is_primary_focus: true` means this org gets a top-level section in briefings and views. At least one primary-focus org must exist (cleanup invariant 15).
`inferred_from[]` carries the connector signals that surfaced this org during onboarding (email domain, Slack workspace, calendar attendee clustering, Drive folder, email signature) OR `"user"` for manually added orgs.
**No org cycles.** The writer helper rejects writes that would create a `parent_org_id` cycle. cleanup invariant 10 re-checks.

---

## `_hq/data/events.jsonl`

Append-only newline-delimited JSON. One event per line. Full schema: `shared/data-schemas/events.schema.json`.

**Line shape (v2.2):**

```json
{
  "seq": 1247,
  "ts": "2026-04-20T14:32:00",
  "type": "decision",
  "source_skill": "meeting-notes",
  "primary_thread_id": "project_008",
  "related_thread_ids": ["project_014"],
  "cross_ref_reason": {"project_014": "Sourcing bot decision touches Bid Leveler thread"},
  "classification_confidence": 0.87,
  "org_ids": ["org_acme_restaurant", "org_acme_co"],
  "person_ids": ["person_042"],
  "source_ref_hash": "sha256:a3f9...",
  "supersedes_seq": null,
  "project_id": "project_008",
  "data": {...}
}
```

**Required fields:** `seq`, `ts`, `type`, `source_skill`, `data`.
**Required on classified events:** `primary_thread_id`, `classification_confidence`. Infrastructure events (`audit_run`, `classification_review`, `onboarding_step`, `boundary marker`) may set these to `null`.
**DEPRECATED:** `project_id` is preserved as a mirror of `primary_thread_id` on v2.2 events for one migration cycle — consumers should read `primary_thread_id` and ignore `project_id`.

### Event types and their data payloads

| Type | data payload fields | Used by |
|---|---|---|
| `meeting` | `{title, duration_min, attendee_person_ids, summary, transcript_ref}` | meeting-notes |
| `decision` | `{title, context, decision, rationale, alternatives[], decided_by_person_id}` | decision-log, meeting-notes, workspace-manager |
| `commitment` | `{description, owner_person_id, due_date, status}` | meeting-notes, workspace-manager |
| `commitment_resolved` | `{commitment_seq, resolution}` | workspace-manager, meeting-notes |
| `interaction` | `{channel, summary, direction}` where channel ∈ (email\|meeting\|slack\|call\|in-person) | meeting-notes, passive capture |
| `status_change` | `{from, to, reason}` (applies to the thread referenced by `primary_thread_id`) | workspace-manager |
| `scope_change` | `{description, delta}` | workspace-manager |
| `intel_logged` | `{source_url, title, tags[], knowledge_base_entry_title}` | intel-intake |
| `briefing` | `{mode, window, summary_ref}` | workspace-manager |
| `audit_run` | `{mode, score, violations[], tail_hash}` | cleanup |
| `onboarding_step` | `{step, notes}` | command-room-onboarding |
| `classification_review` | `{scan_window_start, scan_window_end, candidate_count, reviewed_count, confirmed_count, changed_count, skipped_count}` | insight-generator (Pass 8) |
| `reclassification` | `{from_primary, to_primary, reason, review_session_seq}` (paired with `supersedes_seq`) | insight-generator, meeting-notes, workspace-manager |
| `note` | `{text}` | any skill (free-form) |
| `other` | any | reserved |

### Classification confidence bands

| Band | Range | Routing |
|---|---|---|
| High | ≥ 0.75 | Auto-tag; no user prompt |
| Provisional | 0.40–0.75 | Tag provisionally; silently queue for Pass 8 batched review |
| Low | < 0.40 | Tag as unclassified; explicit Pass 8 review required |

### Append rules

1. Reserve the next `seq` from the writer helper. `seq` is the current max + 1.
2. Set `ts` to the actual event time (not the write time) when known.
3. Compute `source_ref_hash` from the source identifier (Gmail message-id, Calendar event-id, Slack ts) for dedup.
4. Write the line with a trailing newline.
5. The writer helper fsyncs the file after append.
6. **Never rewrite or reorder existing lines.** To correct, append a new `reclassification` event with `supersedes_seq` pointing to the old `seq`. cleanup verifies append-only via rolling tail hash (invariant 9).

### Why JSONL, not JSON

- Appends are O(1). No re-parsing the full file.
- Tail-reading for recent events is trivial.
- Tolerant to partial writes (a corrupted tail line is skippable).
- Works with standard streaming parsers across any language.

---

## `_hq/data/aliases.json`

Raw-form to canonical-id registry. Full schema: `shared/data-schemas/aliases.schema.json`.

**Shape:**

```json
{
  "version": 17,
  "last_updated": "2026-04-20T14:32:00",
  "last_writer": "meeting-notes",
  "mappings": {
    "people": [
      { "raw": "Johnny", "canonical_id": "person_042", "confidence": 1.0, "added_ts": "2026-02-14T09:00:00", "added_by": "command-room-onboarding" },
      { "raw": "JD", "canonical_id": "person_042", "confidence": 0.95, "added_ts": "2026-03-02T10:12:00", "added_by": "meeting-notes" },
      { "raw": "@jdoe", "canonical_id": "person_042", "confidence": 1.0, "added_ts": "2026-02-14T09:00:00", "added_by": "command-room-onboarding" }
    ],
    "projects": [
      { "raw": "AcmeCo", "canonical_id": "project_008", "confidence": 1.0, "added_ts": "2026-02-14T09:00:00", "added_by": "command-room-onboarding" }
    ],
    "orgs": []
  }
}
```

**Lookup is case-insensitive + trimmed.** `"  johnny  "` matches `"Johnny"`. Multiple raws can map to the same canonical id (that's the point).

---

## Free-form markdown files

Some files stay markdown-native in v2.0 because their content is narrative, not structured:

| File | Reason it stays markdown |
|---|---|
| `BUSINESS_CONTEXT.md` | Free-form prose; low change rate |
| `PROJECT_CONTEXT.md` (per project) | Per-project brief; prose-heavy |
| `PROJECT_BRAIN.md` (per project) | Free-form institutional memory |
| `CONFLICTS.md` | Append log; human-readable by design |
| `CLAUDE.md` | Generated hot cache; markdown per Cowork convention |
| `briefings/*.md`, `summaries/*.md`, `audit-reports/*.md` | Point-in-time reports |
| `intel/KNOWLEDGE_BASE.md`, `intel/*.md` | Narrative research |
| `SESSION_NOTES_[NAME].md` (per project) | Append log; human-readable by design |

These files all carry the v1.8 version headers (three HTML comments) where applicable and follow the same Append Format.

**Key distinction:** markdown files in the list above are **sources** (user reads and/or edits via surgical updates). Markdown files under `_hq/views/` are **outputs** (regenerated, never edited).

---

## Person, thread, and org name rules (v2.2)

- Inside structured data (entities.json, events.jsonl, aliases.json, classifier_feedback.jsonl), references use `id` (`person_042`, `project_008`, `org_acme_restaurant`).
- Inside markdown sources (BUSINESS_CONTEXT, PROJECT_CONTEXT, PROJECT_BRAIN, SESSION_NOTES), references use `canonical_name` / `display_name` as readable text.
- Inside regenerated views (`_hq/views/*`), references use `canonical_name` / `display_name` with the id in parentheses on first occurrence per document: `John Doe (person_042)`, `Sourcing Bot Rollout (project_008)`, `Acme Restaurant (org_acme_restaurant)`.
- Inside CONFLICTS.md, always include both for debuggability.
- User-facing language is **"thread"** throughout. The schema `project_` ID prefix is internal and never surfaces in views or UI copy. Briefings say "3 threads waiting on you", not "3 projects".
- Org rendering follows `morning-briefing` Step 4: primary-focus orgs first (with nested operating children under holdings), remaining orgs rolled into OTHER ORGS grouped by `relationship_type`.

---

## Validation (offline)

`cleanup` runs on every invocation and validates against the full v2.2 invariant set documented in `skills/cleanup/SKILL.md` → "Schema Validation (all modes) — v2.2". Highlights:

1. JSON source integrity: schemas parse, seq unique + monotonic, all id refs resolve, `supersedes_seq` valid, `reclassification` events always carry `supersedes_seq`.
2. Append-only guarantee: `events.jsonl` + `classifier_feedback.jsonl` tail hash matches last audit's stored `tail_hash`.
3. Org tree integrity: no cycles via `parent_org_id`, at least one `is_primary_focus: true` org exists, every org has non-empty `inferred_from[]`.
4. Thread integrity: every `affiliation_id` resolves to an active org; threads affiliated to a holding with operating children are flagged as drift.
5. Classifier health: provisional backlog ≤ 25 or age ≤ 14 days; low-confidence backlog ≤ 5; signal drift < 40% change rate over 30 days; every `classifier_feedback` row's `event_seq` resolves.
6. Disk reality: `folder_name` on active threads exists on disk; no orphan thread folders.
7. View freshness: views re-render byte-identically to disk; generated views render the org tree per `morning-briefing` Step 4.
8. Markdown health: versioned files have three-comment header; SKILL.md files carry the v2.2 Writer Contract.
9. Plugin boundary: no customer strings leaked into plugin source; no writes outside workspace scope; telemetry payloads allowlist-compliant; no connector cache in plugin dirs.

Violations don't auto-fix — they surface in the audit report and append to CONFLICTS.md with types `schema-violation` / `boundary-violation` / `classifier-drift`.

---

## Migration to v2.2

The `migration-v2` skill handles two paths. See `skills/migration-v2/SKILL.md` for the full procedure.

**Path A: v1.8 → v2.2 (full migration)**
1. Backup current `_hq/` to `_archive/_hq_v1.8_backup_YYYY-MM-DD/`.
2. Parse v1.8 markdown into `entities.json` threads + people.
3. Mint org IDs (slug-style) from parsed orgs.
4. **Phase 2b — connector-driven org tree inference**: walk email domains, Slack workspaces, calendar attendees, Drive folders, email signatures to propose the nested org tree with scope + relationship_type + is_primary_focus hints.
5. **Phase 2c — user confirmation**: present the inferred tree as a table; user confirms, edits, or rejects each org.
6. Emit events.jsonl entries in v2.2 shape with `classification_confidence: 1.0` (historical ground truth) and DEPRECATED `project_id` mirror.
7. Regenerate all views from JSON sources.
8. Report: source version, target version, org tree, counts, classifier_feedback trend baseline.

**Path B: v2.0/v2.1 → v2.2 (in-place upgrade)**
1. B0: detect schema version from entities.json version header + org record shape.
2. B1: backfill org records with `parent_org_id: null`, `scope` / `relationship_type` / `is_primary_focus` derived from legacy fields:
   - legacy `type: "home"` → `scope: "operating"`, `relationship_type: "operating"`, `is_primary_focus: true`
   - legacy `type: "side"` → `scope: "operating"`, `relationship_type: "other"`, `is_primary_focus: false`
   - legacy `type: "partner"` → `scope: "operating"`, `relationship_type: "partner"`, `is_primary_focus: false`
3. B2: backfill thread records with `affiliation_id`, `kind`, `parent_thread_id` / `spawned_from_thread_id` / `cross_refs` empty.
4. B3: bump entities.json version; leave events.jsonl untouched.
5. B4: **append a single "boundary marker" event** to events.jsonl (`type: "other"`, `data: {note: "v2.2 boundary marker — events at and after this seq carry v2.2 multi-thread shape; earlier events are v2.0/v2.1 and read-only"}`) — preserves append-only guarantee.
6. B5: regenerate all views.

Both paths are idempotent. Running twice after a successful run is a no-op.

---

**End of v2.2 data contract.**
