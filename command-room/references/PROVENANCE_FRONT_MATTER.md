# Provenance front-matter spec (RETIRED v3.12.0)

> **⚠ RETIRED.** This spec was a companion to `STAGING_CONVENTION.md` describing front-matter for files staged to `_hq/staging/[YYYY-MM-DD]/`. That staging path was never shipped — orchestrator deliverables route through `_hq/meetings/` via `shared/scripts/brief_path.py` and through typed deliverable subfolders per `MD_DELIVERABLE_POLICY.md`. The `_hq/staging/` path is now actively forbidden by the orchestrator output validator (leak-pattern scan).
>
> Files in those active deliverable paths (e.g. `_hq/meetings/Past_Meeting_*.docx`) carry provenance via the `.docx` document footer + the corresponding `events.jsonl` event (`pack_run`, `email_drafted`, `board_pack_assembled`, etc.) — not via YAML front-matter.
>
> This file is kept so historical CHANGELOG entries that reference it still resolve. Do not follow this spec; do not build against it.

---

## Historical content (retired — for reference only)

Every doc-shape file staged by a Command Room orchestrator to `_hq/staging/[YYYY-MM-DD]/` MUST start with this 5-line YAML front-matter. Without it, the file is debt: untraceable, undebuggable, and treated as an orphan by the auto-archive sweeper.

## Schema

```yaml
---
trigger: scheduled | state-change | user
fired_by: <skill-or-orchestrator-name> @ <ISO-8601-timestamp>
inputs: [<event_seq-or-id>, <project_id-or-empty>, <person_id-or-empty>]
ttl: 48h
review_url: file://<full-absolute-path-to-this-file>
---
```

## Field-by-field

### `trigger` (required)

One of three string values:

- `scheduled` — fired by a cron-style scheduled task (e.g., daily-morning-pack at 6:45 AM)
- `state-change` — fired by Phase 3's state-watcher in response to an events.jsonl change (v2.8.3+; not used until then)
- `user` — manually invoked by M (e.g., M typed `prep me for the 3pm` in chat and the output landed in staging)

The trigger field tells the reviewer instantly: was this auto-generated for me overnight, or did I ask for it? Different mental models for each.

### `fired_by` (required)

Format: `<orchestrator-or-skill-name> @ <timestamp>`

- The skill or scheduled-task name that produced the file: `cr-daily-morning-pack`, `call-prep`, `email-writer`, `dormant-customer-scan`, etc.
- The `@` separator
- ISO 8601 timestamp with timezone: `2026-04-28T06:48:33-07:00`

Examples:
- `fired_by: cr-daily-morning-pack @ 2026-04-28T06:48:33-07:00`
- `fired_by: call-prep @ 2026-04-28T10:14:02-07:00` (manual fire)

### `inputs` (required)

YAML list of 3 elements (use empty string for missing):

1. **event_seq or id** — the events.jsonl seq or id that triggered/seeded the work, e.g., `event_092`, or empty for time-triggered fires
2. **project_id** — entities.json project id, e.g., `project_007`, or empty if no project mapping
3. **person_id** — entities.json person id, e.g., `person_005`, or empty if N/A

Examples:
- `inputs: [event_092, project_007, person_005]` — fired by a state-change on event 92, mapped to a specific project and person
- `inputs: [, project_016, ]` — time-triggered, mapped to a project, no specific person
- `inputs: [, , ]` — time-triggered, no specific entity context (e.g., morning-brief-overall)

### `ttl` (required)

Time-to-live before auto-archive. Default: `48h`.

- Most staged items: `48h`
- High-volatility items (last-mile pre-meeting refresh, time-sensitive nudges): `24h` or even `4h`
- Long-form deliverables (memos, board updates, contracts): `168h` (1 week) — these expect slow review

The auto-archive sweeper reads this field; items past TTL move to `_hq/staging/_archive/[date]/`.

### `review_url` (required)

Format: `file://<full-absolute-path-to-this-file>`

This is the URL the deliverables-in-flight artifact (Phase 2, v2.8.2+) uses to open the file when the user clicks the row. On Windows the format is `file:///C:/.../filename.md`; on Mac/Linux it's `file:///Users/.../filename.md`.

Computing it: take the absolute path the orchestrator wrote the file to, prefix with `file://`. The orchestrator already knows this — it just wrote the file via `$WORKSPACE` resolved at runtime per `shared/CONTRACT.md` Rule 22. **NEVER fabricate a path or reuse an example path from these docs.** If `$WORKSPACE` resolution failed, omit `review_url` entirely rather than guess.

## Complete example — daily-morning-pack output for an external meeting

File: `_hq/staging/2026-04-28/meeting-prep_alexia-q3-review_1100.md`

```markdown
---
trigger: scheduled
fired_by: cr-daily-morning-pack @ 2026-04-28T06:48:33-07:00
inputs: [, project_007, person_003]
ttl: 48h
review_url: file:///$WORKSPACE/_hq/staging/2026-04-28/meeting-prep_alexia-q3-review_1100.md   # ← $WORKSPACE is resolved at write time, NOT this literal token
---

# Northstar Partners — Q3 Review (11:00 AM today)

**With:** Bo Sample · Dustin Sample

[... rest of call-prep skill output ...]
```

## Complete example — state-watcher fire (v2.8.3+) on a new Granola transcript

File: `_hq/staging/2026-04-28/followup-pack_summit-strategy-call_1430.md`

```markdown
---
trigger: state-change
fired_by: cr-state-watcher.granola-transcript @ 2026-04-28T14:32:11-07:00
inputs: [event_104, project_002, person_005]
ttl: 24h
review_url: file:///.../followup-pack_summit-strategy-call_1430.md
---

# Follow-up pack — Summit Strategy Call (concluded 2:30 PM)

[... follow-up-ritual skill output, including draft emails for each attendee ...]
```

## Why front-matter not metadata-elsewhere

Three options were considered:

1. **YAML front-matter on the file itself (chosen).** Pros: in-band, travels with the file, visible in any text editor. Cons: clutters the first 5 lines.
2. **Sidecar `.meta.json` files.** Pros: clean separation. Cons: doubles the file count, easy to drift out of sync, breaks if file is moved/renamed.
3. **Single index file** (e.g., `_hq/staging/[date]/_index.json`). Pros: single source of truth. Cons: the index becomes a coordination bottleneck — every orchestrator has to read+modify+write it; race conditions on parallel fires.

Front-matter wins on simplicity. The 5-line cost per file is trivial; the in-band visibility is the win.

## What the deliverables-in-flight artifact (Phase 2) does with this

The artifact (when shipped) reads each file in `_hq/staging/[today]/`, parses the front-matter, and:

- Hover row → tooltip shows `trigger`, `fired_by`, and `inputs` (so reviewer can see why this fired)
- Click → opens `review_url` in M's default editor / file viewer
- Click "skip" → moves to `_hq/staging/_archive/[date]/`, writes outcome row to `staging_outcomes.jsonl`
- Auto-prune → removes rows for items past their `ttl` (the file is moved by the archiver, the artifact reflects)

## Forbidden mistakes

- **Don't write a file without front-matter.** No exceptions. If you can't compute the inputs (e.g., `event_seq` is unclear), write empty strings, but keep all 5 fields present.
- **Don't use placeholder timestamps** (`<TIMESTAMP>`). Compute the real one from the system clock at write time.
- **Don't use relative paths in `review_url`.** The artifact opens this as a URL; relative paths break depending on the consumer's working directory.
- **Don't fork the schema.** If your skill needs additional metadata (e.g., voice-calibration profile used, project priority weight), put it in a separate YAML block AFTER the standard 5-line front-matter, in a `meta:` key. Don't add fields to the canonical front-matter.
