# Parser B — v2.0 / v2.1 Plugin Workspace → v2.x JSON (schema upgrade)

Handles workspaces already on the JSON substrate (entities.json + events.jsonl + aliases.json) but carrying the **legacy v2.0/v2.1 shape**: orgs have a `type` field (`home` | `side` | `personal`) instead of the v2.2+ trio of `scope` + `relationship_type` + `is_primary_focus`; threads have a single `org_id` instead of `affiliation_id`; events have a single `project_id` instead of `primary_thread_id` + `related_thread_ids[]` + `classification_confidence`.

No users are currently on v2.0/v2.1 in the wild (v2.2 shipped before external beta testers landed), but this parser is kept for completeness and to support any internal test installs that may surface.

Lifted from `migration-v2` Path B (Phases B2–B5) and adapted to emit into workspace-ingest's shared collections.

---

## Shape assumptions

```
<source>/
  _hq/
    data/
      entities.json              ← required; orgs carry legacy `type` field
      events.jsonl               ← required; events carry legacy `project_id` only
      aliases.json               ← required
    views/**                     ← regenerable, not parsed
    MASTER_TRACKER.md            ← backward-compat view copy; not re-parsed
    PEOPLE.md                    ← backward-compat view copy; not re-parsed
    DECISION_LOG.md              ← backward-compat view copy
    ALIASES.md                   ← backward-compat view copy
    ...narrative files           ← preserved
  [Project Folder]/
    SESSION_NOTES_*.md           ← NOT re-parsed (events already structured)
    ...narrative files           ← preserved
```

## Detection entry point

Orchestrator dispatches here when `_hq/data/entities.json` exists AND at least one org record has a `type` field of `home` / `side` / `personal` AND no org has a `scope` field. See `../SKILL.md` → Phase 1 "Shape Detection".

If any org already has `scope`, shape detector should emit "Already on v2.x — nothing to ingest" and exit before this parser ever runs.

---

## Why Parser B is lightweight

Unlike Parser A / C / D, Parser B does NOT re-parse markdown. The source data is already structured JSON. This parser performs an **in-place schema upgrade**:

- Org records: upgrade `type` → `scope` + `relationship_type` + `is_primary_focus` (+ detect `parent_org_id` via connector inference).
- Thread records: copy `org_id` → `affiliation_id` (keep `org_id` as DEPRECATED mirror); add empty `parent_thread_id` / `spawned_from_thread_id` / `cross_refs` fields.
- Event records: copy `project_id` → `primary_thread_id` and append `related_thread_ids: []` + `classification_confidence: 1.0` in-memory (events.jsonl is append-only; historical events are NOT rewritten — they validate against v2.2 schema because new fields are optional/nullable with fallback-read rules).

Parser B produces the same five in-memory collections (orgs / people / threads / events / aliases) for the orchestrator's atomic write.

---

## Phase-by-phase extraction

### P1. Load source JSON

Read:

- `<source>/_hq/data/entities.json` → parse envelope + people/threads/orgs arrays
- `<source>/_hq/data/events.jsonl` → parse line-by-line into events array
- `<source>/_hq/data/aliases.json` → parse envelope + mappings array

Validate each against the v2.0/v2.1 JSON Schema in `shared/data-schemas/legacy/v2.1/*.schema.json` (retained for back-compat). If validation fails, flag as potentially corrupted — parser continues best-effort with warnings in INGEST_REPORT.

### P2. Upgrade orgs[]

For each org record:

1. **Map legacy `type` → new fields** per this table. Ambiguous cases are flagged for Phase 2c confirmation in onboarding (parser marks them with `_needs_confirmation: true`).

   | Legacy `type` | New `scope` | New `relationship_type` | New `is_primary_focus` | Confidence |
   |---|---|---|---|---|
   | `home` | `operating` | `operating` | `true` | high |
   | `side` | `operating` | infer from org name: "Advisory" / "Advisor" → `advisory`; "Fund" / "LP" → `investment`; "Board" → `board`; "Portfolio" → `portfolio_company`; else `advisory` (default) — **mark for confirmation** | `false` | medium (needs confirmation) |
   | `personal` | `other` (with `scope_label: "personal"`) | `beneficiary` | `false` | high |
   | (unset / unknown) | `operating` (default) | null | null | low — flag warning |

2. **Preserve legacy `type` field** as DEPRECATED but do not delete (readers fall back).

3. **Detect `parent_org_id`** via connector inference pass (see `../SKILL.md` → "Connector-Assisted Org Tree Inference"). Email domain hierarchy often exposes holding → operating relationships that v2.0/v2.1 couldn't represent. For each detected parent/child link, set `parent_org_id` on the child; upgrade the parent's `scope` to `holding` if it wasn't already `operating`.

4. **Populate `domains[]`, `slack_workspace_ids[]`** from connector signals.

5. **Set `inferred_from[]`** — start with `["v2x-source"]`; append connector signal types during inference pass.

### P3. Upgrade threads[]

For each thread (v2.0/v2.1 calls them `projects` with `org_id`):

1. **Copy `org_id` → `affiliation_id`**. Retain `org_id` as DEPRECATED mirror.

2. **Validate affiliation specificity** — if `affiliation_id` points at an org that is now a holding (because P2 detected children), flag the thread for user review: the thread probably belongs to an operating child, not the holding. Mark `_needs_affiliation_review: true` in parser output; orchestrator surfaces these in onboarding Phase 2c.

3. **Add new v2.2+ fields**:
   - `parent_thread_id` = null
   - `spawned_from_thread_id` = null
   - `cross_refs` = []
   - `kind` — if `project_class` exists in the legacy record, copy to `kind`. Normalize to v2.x kind taxonomy (see Parser A → P2 rules). If absent, default `"initiative"`.

4. **Preserve every legacy field** not explicitly replaced (v2.x schema is additive).

### P4. Upgrade events[] (additive — no mutation of historical events)

v2.x events.jsonl is append-only. Do NOT rewrite existing event records on disk. Instead:

1. **For each historical event in memory**, normalize read-time representation:
   - `primary_thread_id` = legacy `project_id` (read-time fallback; not written back)
   - `related_thread_ids[]` = [] (implicit empty)
   - `classification_confidence` = 1.0 (implicit — historical data is ground truth)
   - `source_skill` = preserve if present; else `"legacy-v2x"`

2. **Validate each event** against the v2.2 events.schema.json. New fields are optional/nullable — historical events should pass cleanly. Any that fail validation get logged with (seq, ts, reason) in INGEST_REPORT.

3. **Emit one boundary event** that the orchestrator will append during Phase 4:
   ```json
   {
     "seq": "<next>",
     "ts": "<ingest_ts>",
     "type": "classification_review",
     "source_skill": "workspace-ingest",
     "primary_thread_id": null,
     "related_thread_ids": [],
     "classification_confidence": 1.0,
     "data": {
       "step": "v2x_to_latest_boundary",
       "notes": "All events before this seq have implicit classification_confidence: 1.0, primary_thread_id: <legacy project_id>, related_thread_ids: []. From this seq forward, events carry the multi-thread fields explicitly."
     }
   }
   ```

Historical events stay physically on disk in their original v2.0/v2.1 form. The boundary event marks the read-time inference cutoff. Post-ingest views + readers use the fallback rules above for events before the boundary.

### P5. Upgrade aliases[]

aliases.json shape is unchanged between v2.0/v2.1 and v2.x. Pass through unchanged. Bump envelope `version` field, set `last_writer: "workspace-ingest"`.

### P6. Preserve people[]

v2.0/v2.1 people records are already shape-compatible with v2.x (schema was additive from day one). Pass through. Fill any missing optional fields with defaults:

- `aliases[]` = [] if absent
- `affiliation_ids[]` = [primary_org_id] if absent
- `status` = `"active"` if absent
- `project_ids[]` = [] if absent (will be backfilled by thread back-links if needed)

### P7. Run connector-assisted inference pass

Per `../SKILL.md` → "Connector-Assisted Org Tree Inference". Populates `domains[]`, `slack_workspace_ids[]`, detects `parent_org_id`, refines `scope` and `relationship_type` for orgs marked `_needs_confirmation` in P2.

Orgs where the inference pass reached high-confidence (3+ signals) have `_needs_confirmation` cleared. Orgs with 1–2 signals keep the flag; orchestrator surfaces in onboarding Phase 2c for user confirmation.

---

## v2.0 vs v2.1 differences

Minor — both carry the same legacy shape. The one distinction:

- **v2.0:** early-beta envelope may have `version: 0` or be missing entirely on entities.json. Parser treats either as v2.0.
- **v2.1:** envelope reliably has `version: 1`. Adds `last_writer` field populated.

Parser B treats both identically for upgrade purposes. The envelope is rewritten with `version: 1` (or next monotonic), `last_updated: <ingest_ts>`, `last_writer: "workspace-ingest"` during orchestrator's atomic write.

---

## v2.x quirks

- **Envelope drift** — some early v2.0 installs lack the `{version, last_updated, last_writer}` envelope and store bare arrays. Parser wraps into the v2.x envelope during upgrade.
- **Missing scope_label for personal orgs** — if `type: "personal"` orgs lack `scope_label`, set to `"personal"` explicitly.
- **Events with `project_id: null`** — legacy HQ-scope events. Resolve `primary_thread_id` to synthetic `thread_hq` (mint if not present per Parser C's P8 rules).
- **Aliases with stale canonical_id** — if any aliases.json entry's `canonical_id` doesn't resolve to an entity in the upgraded collections, log warning + drop the alias.
- **Partial upgrade markers** — if source already has some orgs with `scope` field and others without (partial state from a failed prior migration), shape detection should have flagged this before reaching parser. Parser refuses to proceed and returns error.

---

## Back-compat guardrails

- Legacy `type` field on orgs preserved (DEPRECATED).
- Legacy `org_id` field on threads preserved (DEPRECATED).
- Legacy `project_id` field on events preserved — events.jsonl physically unchanged; only the in-memory upgraded view is emitted for the orchestrator's atomic write.
- Historical events keep their original `source_skill` values — never overwritten.
- View regeneration is the orchestrator's downstream concern; this parser doesn't touch views.

---

## Output hand-off

Parser B returns the five collections (orgs / people / threads / events / aliases) + a boundary event for Phase 4 append to orchestrator. Events collection contains the upgraded read-time view of historical events (not the raw lines — orchestrator's write step handles physical file concerns).

Orchestrator must preserve append-only semantics for events.jsonl: physically, historical event lines stay as they were; only the new boundary event + any post-boundary events append.
