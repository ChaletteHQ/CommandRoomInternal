---
name: workspace-ingest
description: "Underlying two-layer ingest pipeline: extract context (people, projects, decisions, memories) from a source AND optionally copy the source's actual documents into workspace project folders. Fires on: 'ingest this folder' / 'ingest folder [path]', 'ingest [source] into my workspace', 'bootstrap my workspace from [source]'. Preview-and-confirm before any write; AUGMENT mode on workspaces with existing data (dedupe makes re-runs no-ops); undo is snapshot-based and only removes what its own log recorded as created. Routes: pure context extraction lands in ingest-context behavior; pure file filing lands in file-documents behavior; this skill runs the combined pipeline. Does NOT fire on 'scan my desktop' / 'sort my downloads' alone (file-documents). Mode table, undo contract, and safety rails: Routing section in the body."
template_version: 2.0.0
---

# Workspace Ingest (v2.14.20+ — merged with context-ingestion)

Takes any folder you point at and does two things in one pass:

1. **Layer 1 — Context extraction (always-on, deterministic, idempotent).** Walks the source for people, projects, orgs, decisions, commitments, memories, and project-context narrative. Adds them to your existing data substrate (`_hq/data/entities.json` + `events.jsonl` + `aliases.json`). Atomic writes; new entities/events deduplicated against what's already there via id/fingerprint match. Reversible by editing the JSON.
2. **Layer 2 — Document filing (opt-in, with preview-and-confirm).** For source folders that contain actual documents (employee reviews, 1:1 notes, contracts, meeting transcripts, decks, deliverables), classifies each file by which project it belongs to, presents the classification as a preview widget, and copies confirmed files into the workspace's project folders. Source files are never moved or deleted. Workspace gradually becomes your filing cabinet.

The merged skill replaces both `workspace-ingest` (the data-substrate parser) and the now-retired `context-ingestion` skill (the random-files-into-projects sorter). v2.14.20 consolidated them because the user mental model is the same: "I have stuff somewhere, pull what's useful into Command Room." See `CHANGELOG.md` v2.14.20 for the design rationale.

**Default mode is AUGMENT.** The expected post-onboarding use case: a customer with an established workspace (entities.json already populated by onboarding) wants to pull in supplementary material — their ChatGPT memory, a folder of employee files, a legacy Downloads pile of meeting notes. The skill adds context + files on top; the existing workspace is preserved.

**BOOTSTRAP mode** (create the data substrate from scratch on first install) is rare in v2.14.20+ — onboarding handles the typical fresh-install case without invoking this skill. Bootstrap fires only when the target workspace genuinely lacks `_hq/data/entities.json` AND the source is a structured prior workspace (v1.x / v2.x / ChatGPT export). Random-folder ingest in bootstrap mode is rejected (random files don't make a viable substrate).

---

## Writer Contract

Before writing, read `shared/WORKSPACE_API.md`. All writes follow the File Ownership Map, Write Protocol, and Append Protocol.

You are the **one-time ingester + file migrator**. You write:

**Data substrate (from parsed registries):**
- `_hq/data/entities.json` (create from parsed source)
- `_hq/data/events.jsonl` (create, one event per parsed decision / meeting / interaction / status change / commitment)
- `_hq/data/aliases.json` (create from parsed nickname / @handle / abbreviation mappings)

**Migrated files (copied from source into correctly-shaped folders):**
- `[Project]/SESSION_NOTES_[Project].md` (migrated from v1.x `_hq/sessions/` or custom session notes)
- `[Project]/meetings/*.md` (migrated meeting transcripts, Granola exports, call notes)
- `[Project]/deliverables/*` (migrated PDFs, .docx, .xlsx, .pptx, markdown deliverables)
- `[Project]/PROJECT_CONTEXT.md` (seeded from parsed PROJECT_BRAIN.md content; this is the v2.7.1 replacement for per-project skills)
- `_hq/intel/*` (migrated intel / research / briefings)
- `_hq/briefings/*` (migrated morning briefings, automation scans, cleanups)

**Report + audit trail:**
- `_hq/INGEST_REPORT.md` (one-shot report of data + file migration — counts, file-by-file disposition, warnings, source shape, rollback path)
- `_hq/_ingest-queue/` (low-confidence files the CEO must classify manually before they move to final homes)
- `_hq/_ingest-undo.jsonl` (append-only log of every write this run makes — a `run_start` header line per run, then one line per file copy / merge / data write, with source path, destination path, sha256, timestamp, and whether the destination was CREATED by this run or already existed)
- `_archive/ingest_source_YYYY-MM-DD/` (full backup of the source folder — reference copy; the source is never mutated, so this is belt-and-suspenders, not the rollback point)
- `_archive/pre-ingest-data_[ts]/` (Phase 3.9 snapshot of the target workspace's `_hq/data/` taken BEFORE any data write — THIS is the rollback point for substrate; see Phase 3.9)

**Critical rules:**
- **Copy-first, never move.** Every file operation is a copy. The source folder is never mutated.
- **Never delete.** Even after successful migration, the source stays pristine. The CEO decides when (if ever) to delete the source.
- **Every write is logged** in `_ingest-undo.jsonl` so the CEO can ask "undo migration" and this skill can reverse the run: file copies reverse by deleting the destination (source is untouched), merges reverse by stripping the logged section, and substrate writes reverse from the Phase 3.9 snapshot + logged seq range. **Undo deletes ONLY what the undo log records as created by this run — never anything keyed on a file attribute like `last_writer`** (an AUGMENT merge stamps `last_writer` on the user's pre-existing substrate; deleting on that key destroys their data).
- **Content-hash guard.** If a destination file already exists with the same sha256 as the source, skip (idempotent re-run). If different sha256, append `.conflict-[ts].ext` and log — never silently overwrite.
- You **do not** modify anything in `[Project]/` folders other than writing the migrated files. You **do not** write views — that's view-generation's job. You **do not** delete the source folder.

After successful ingest, ownership transfers per the File Ownership Map. Re-running on a workspace that already has `_hq/data/entities.json` runs in AUGMENT mode — dedupe makes same-source re-runs no-ops (see Idempotency).

---

## When to Run

### Primary entry — direct user trigger (post-onboarding)

The most common case: a customer with an established workspace says one of the DIRECT trigger phrases (`ingest folder ~/Downloads/employee-files`, `ingest this folder`) to augment their workspace with new material. The focused phrases (`ingest context from [path]`, `scan my desktop`, `ingest my chatgpt export`, `file documents from [path]`) do NOT route here directly — they fire the `ingest-context` / `file-documents` alias skills per the alias split in the description, which enter this same pipeline with the layer pre-selected. Either way the skill detects AUGMENT mode (existing entities.json present), shows a preview, and adds context + files on top.

### Onboarding does NOT auto-invoke (as of v2.7.22 — preserved in v2.14.20)

Onboarding is a fresh-install performance: scan connectors → reveal what's there → seed entities.json → run a real briefing. It doesn't auto-fire `workspace-ingest`. Customers with prior data (v1.x workspace, ChatGPT export) run this skill BEFORE onboarding once, then onboard on top of the now-populated workspace; OR they onboard fresh first and ingest later when they have specific stuff to pull in.

### Mode detection

At Phase 1, the skill detects which mode applies:

| Target state | Source state | Mode |
|---|---|---|
| `_hq/data/entities.json` exists (workspace already onboarded) | Any source | **AUGMENT** — additive writes, dedupe against existing |
| `_hq/data/entities.json` missing (truly fresh workspace) | Structured (v1.x / v2.x / ChatGPT) | **BOOTSTRAP** — create substrate from parsed source (legacy migration path) |
| `_hq/data/entities.json` missing | Random folder of files | **REJECT** — surface plain English: *"Your Command Room isn't set up yet. Run `set up command room` first, then `ingest folder [path]` to pull these files in."* A folder of random files alone isn't enough to start your workspace from scratch. |

The mode determines Phase 4's write semantics (see Phase 4 below).

---

## Orchestration (9 Phases + smart-merge sub-pass in v2.7.4+)

### Phase 1: Accept source path + detect shape

Accept the source path from the user's trigger phrase (or prompt for one). Validate that it exists and is readable.

Detect the source shape using the **Shape Detection** rules immediately below (run here in Phase 1). If shape is ambiguous, ask the CEO: *"I'm seeing a couple of possibilities here — is this [guess A] or [guess B]?"* Pick based on response.

> These rules inspect the **source folder being ingested** to identify its format (`MASTER_TRACKER.md`, `entities.json`, `conversations.json`, etc. found in the *source*) — orientation only. They are NOT reads of the workspace's own Tier 2 view and never drive a workspace surface decision.

#### Shape Detection (order matters — take the first match)

1. **v2.x plugin detected:** Source has `_hq/data/entities.json` (or `data/entities.json` at source root). Read the file; if orgs carry legacy `type` field of `home` | `side` | `personal`, it's v2.0/v2.1 (Parser B). If orgs already have `scope` field, announce "Already on v2.x — nothing to ingest" and exit (not our job to re-ingest already-structured data; re-run migration-v2 Path B-equivalent only if explicitly asked).

2. **v1.x plugin detected:** Source has `_hq/MASTER_TRACKER.md` AND (`_hq/DECISION_LOG.md` OR ALIASES.md visible) AND PEOPLE.md exists with single-table shape (no section headers like `## Co-Owners`, `## Active Clients`, etc.). Also check for plugin-signature files: `WORKSPACE_SCHEMA.md`, `CHANGELOG.md`, a `.claude-plugin/plugin.json` at source root. These signal a plugin-built workspace.
   - **Sub-detect v1.4 vs v1.7 vs v1.8:** check plugin.json version if present. If absent: presence of COMMUNICATION_PROFILE.md → v1.7+; presence of automation-scanner/morning-briefing/stress-test outputs (under `_hq/briefings/` or `_hq/audit-reports/`) → v1.8. Default to v1.4 if unclear.
   - Route to **Parser A** — the parser handles v1.4 / v1.7 / v1.8 variation internally.

3. **Custom markdown detected:** Source has ONE of: `_hq/MASTER_TRACKER.md`, `_hq/MASTER_TRACKER.md` with missing DECISION_LOG + section-nested PEOPLE.md, or any `MASTER_TRACKER.md` at source root. Also covers shapes with PROJECT_BRAIN.md files in project folders. Route to **Parser C**.

4. **OpenAI ChatGPT export detected:** Source has `conversations.json` at the root, OR has both `chat.html` AND `user.json` at the root (the canonical OpenAI export bundle). The presence of `conversations.json` alone is the strongest signal. If source ALSO contains a `_hq/` directory or `MASTER_TRACKER.md`, do NOT route here — that's a Command Room shape that happens to have a stray `conversations.json` file; route to Parser A/B/C instead. Route to **Parser E** for clean ChatGPT-export folders.

5. **Random folder of files detected (v2.14.20+ — was the standalone `context-ingestion` skill):** Source is a folder containing actual deliverable documents — `.docx`, `.pdf`, `.xlsx`, `.pptx`, `.md`, `.txt`, images — but no Command Room registry files (no MASTER_TRACKER, no entities.json, no `conversations.json`). Typical sources: `~/Desktop/`, `~/Downloads/`, a legacy project folder copied from a prior consultant, an unzipped client document drop. Route to **Parser F** (Folder Mode — see `references/folder-mode-parser.md`). Parser F does:
   - Light Layer 1 context extraction: filename / metadata / content sniff to identify mentioned people, projects, orgs (low-confidence; everything flagged in INGEST_REPORT for the CEO to confirm).
   - Heavy Layer 2 file filing: classify each readable file by which existing project it belongs to, present a preview-and-confirm widget, copy confirmed files into project folders. This is the primary value of folder-mode ingest.

   AUGMENT mode required — Parser F refuses to run when `_hq/data/entities.json` doesn't exist (random files alone don't seed a viable workspace; the user should run onboarding first, then ingest).

6. **Generic fallback (structured-but-unrecognized):** None of the above matched cleanly. Source has SOME structured signal (a stray entities.json, partial registry files, scattered project folders without the v1.x / v2.x markers) but doesn't fit a known shape. Route to **Parser D** for best-effort extraction. Low confidence output; flagged heavily in INGEST_REPORT. Parser D is the LAST resort — Parser F handles the more common "random folder of documents" case.

**Ambiguity handling:** If two shapes match (e.g., both v1.x and custom-markdown signals present), ask the CEO which one it is. Show a 2-line summary of each interpretation and let them pick. Default choice is the shape with the most signals.

**Parser F vs Parser D distinction (v2.14.20+):** Parser F is for pure document folders (employee files / 1:1 notes / contracts / decks — content-bearing files, no Command Room registry artifacts at all). Parser D is for partial-registry structured-but-unrecognized shapes (someone's hand-built attempt at a workspace that doesn't match v1.x / v2.x / custom-markdown signals). When in doubt, lean Parser F — the document-filing path is more useful day-to-day; Parser D is rare.

Once the shape is detected, announce it to the CEO in PLAIN labels — never the internal shape names or version numbers. Map: v1.x/v2.x plugin workspace → "your older Command Room workspace"; custom markdown → "your folder of notes"; ChatGPT export → "your ChatGPT export"; document folder → "your folder of documents". Announce: *"Found [your older Command Room workspace / your folder of documents / your ChatGPT export] at [path]. I'll pull it into your new Command Room — your originals stay where they are, untouched. About a minute."*

Wait for explicit confirmation ("yes" / "go" / "proceed"). If no, exit without writing.

### Phase 2: Size gate, then backup source

**Size gate (v2.7.4+ — runs BEFORE the backup copy, not after).** Count the source folder's files and total bytes first. If the count exceeds **500 files** OR total bytes exceeds **500 MB** (whichever hits first), pause and ask the CEO before copying anything:

> *"That folder is big — [N] files / [S] MB. Pulling all of it at once can sometimes stall. Want me to: (a) go through everything now (slower, more risk it gets interrupted), (b) just pull the files you've actually touched in the last year (faster, safer, catches what matters), or (c) point me at a smaller subfolder?"*

Default recommendation is (b). If the CEO picks (b), restrict BOTH the backup below AND the Phase 5 discovery pass to files with `mtime ≥ now - 365d`. If (c), ask for the narrower path and re-run Phase 1 from the new root. Gating before the copy is the point — a 500 MB folder must not get fully backed up before the CEO has chosen a scope.

Then copy the (scoped) source folder tree into `_archive/ingest_source_YYYY-MM-DD/` (today's date). Include every in-scope file — even ones the parser won't read — because the CEO may want to reference them later.

Write `_archive/ingest_source_YYYY-MM-DD/.ingest-marker` with:
- `source_path` (the path the CEO pointed at)
- `detected_shape` (v1.x / v2.x / custom-markdown / generic)
- `target_version` (current plugin version, e.g., 2.5.0)
- `timestamp`
- `ingester_version` (plugin version)

### Phase 3: Route to parser per shape

Dispatch to the correct parser based on detection:

| Detected shape | Parser | Reference file | Default mode |
|---|---|---|---|
| v1.x plugin (v1.4 / v1.7 / v1.8 — markdown registries, no `data/` folder) | **Parser A** | `references/v1x-parser.md` | BOOTSTRAP or AUGMENT |
| v2.x plugin (v2.0 / v2.1 — JSON registries with legacy org `type` field) | **Parser B** | `references/v2x-parser.md` | BOOTSTRAP or AUGMENT |
| Custom markdown (pre-plugin hand-built, section-nested PEOPLE, no DECISION_LOG, etc.) | **Parser C** | `references/custom-markdown-parser.md` | BOOTSTRAP or AUGMENT |
| OpenAI ChatGPT export (unzipped folder with `conversations.json` + `user.json` + `chat.html`) | **Parser E** | `references/openai-export-parser.md` | BOOTSTRAP or AUGMENT |
| Random folder of documents (v2.14.20+ — folder-mode; absorbs former `context-ingestion`) | **Parser F** | `references/folder-mode-parser.md` | AUGMENT only — refuses BOOTSTRAP |
| Generic structured-but-unrecognized fallback | **Parser D** | `references/generic-fallback-parser.md` | BOOTSTRAP or AUGMENT |

Each parser produces three in-memory collections: **orgs[]**, **people[]**, **threads[]**, **events[]**, **aliases[]**. These flow into Phase 4 writes.

Each parser also runs the **connector-assisted inference pass** — for every org minted, probe connected Gmail / Calendar / Slack / Drive to populate `domains[]`, `slack_workspace_ids[]`, detect `parent_org_id` relationships, and refine `scope` + `relationship_type` + `is_primary_focus`. Inference rules are inline in THIS file — see the "Connector-Assisted Org Tree Inference (shared)" section below. This amplifies whatever the parser extracted; it does not replace it.

### Phase 3.5: Parse Completeness Check (hard gate)

Before any JSON is written, verify the parser didn't silently drop entries. This is the fix for the v2.7.5-era Parser C under-extraction bug (see references/HISTORY.md).

**Procedure — run for each structured registry the parser consumed:**

1. **PEOPLE.md heading audit.**
   - Count `^### ` entries in source PEOPLE.md (excluding any section explicitly marked skip in the parser's section handling table — e.g., "Data Gaps & Backfill Needed" in Parser C).
   - Compare to `len(people[])` returned by the parser.
   - If counts differ → abort Phase 4 write. Dump to `INGEST_REPORT.md`:
     - Every `###` heading found in source (with line numbers).
     - Every person record produced by parser (with `canonical_name`).
     - A diff showing which source headings have no matching person record.
   - Prompt the CEO: *"I pulled in N of M people from your records — looks like [list] didn't come through. Want me to (a) try again, (b) go ahead and flag the missing names so you can add them later, or (c) stop here?"*

2. **MASTER_TRACKER row audit.**
   - Count project/thread rows across all stage tables in source MASTER_TRACKER.md.
   - Compare to `len(threads[])`. Same diff + abort behavior on mismatch.

3. **ALIASES.md row audit (if present).**
   - Count table rows across all three alias tables (People / Projects / Orgs).
   - Compare to `len(aliases[])` from parser. Soft-warn on mismatch (some aliases legitimately unresolve — see Parser A P3); only hard-abort if delta > 25%.

4. **SESSION_NOTES session-boundary audit.**
   - For each project's SESSION_NOTES file, count `^## YYYY-MM-DD` or `^## Session NN` headings.
   - Compare to count of events with that primary_thread_id and a ts matching a session boundary date.
   - Soft-warn on mismatch; sessions can legitimately produce 0 events if they contain only narrative. Don't hard-abort.

**Output:** either a clean "Parse completeness verified — N people, M threads, P aliases, Q events" log line, or a hard-abort with the diff prompt above. Never silently write incomplete data.

### Phase 3.9: Snapshot the target substrate (hard gate — nothing writes before this)

Before ANY Phase 4 write, snapshot the target workspace's data directory:

1. Copy `_hq/data/` (entities.json, events.jsonl, aliases.json — every file present) to `_archive/pre-ingest-data_[ts]/` (ISO timestamp). If `_hq/data/` doesn't exist yet (BOOTSTRAP on a truly empty workspace), record that fact instead of copying — an empty snapshot is a valid snapshot.
2. Record the run header as the first undo-log line for this run, appended to `_hq/_ingest-undo.jsonl`:

```json
{"action":"run_start","ts":"<now>","mode":"AUGMENT|BOOTSTRAP","source_path":"<source>","snapshot":"_archive/pre-ingest-data_[ts]/","pre_ingest_max_seq":<highest seq in events.jsonl, or null>,"preexisting_data_files":["entities.json","events.jsonl"]}
```

This snapshot — not the source backup in `_archive/ingest_source_*` (which backs up the SOURCE folder, not the target) — is what Rollback and Undo restore from. The snapshot is archived, never deleted; retention follows the workspace archive policy.

### Phase 4: Write JSON sources (mode-aware — v2.14.20+)

All writes use `shared/scripts/atomic_write.py` (`atomic_write_json` for JSON files, `atomic_append_jsonl` for events.jsonl) — consistent with every other writer in the plugin (v2.14.20 retired the last hand-rolled tmp+rename holdout — see references/HISTORY.md).

**BOOTSTRAP mode** (target workspace has no `_hq/data/entities.json` yet — fresh-install with prior data):

Write in order, each atomically:

1. `_hq/data/entities.json` — canonical schema shape per `shared/data-schemas/entities.schema.json`: `{version: 1, last_updated: <ingest_ts>, last_writer: "workspace-ingest", entities: {people: [...], projects: [...], orgs: [...], engagements: [...]}, aliases: [...], workspace: {...}}`
2. `_hq/data/events.jsonl` — one event per line, sorted by `ts` ascending then by parse order. Use `atomic_append_jsonl` even for the initial create.

Validate each against `shared/data-schemas/*.schema.json` per the renderer-style validator pattern. On validation failure: roll back (restore `_hq/data/` from the Phase 3.9 snapshot), abort with full error trace. No partial writes leak. Log each created file in `_ingest-undo.jsonl` with `"action":"data_create"` so undo knows these files did not pre-exist.

**AUGMENT mode** (target workspace has existing entities.json + events.jsonl — typical post-onboarding case):

For each parsed entity / event collection, perform an additive merge against the existing data:

1. **Read the existing entities.json** via the canonical path. Use `data["entities"]` if nested per schema; else top-level (back-compat per v2.14.17).
2. **Dedupe parsed entities against existing**. Match strategy by entity type:
   - **People:** match on `email` (primary key), then `canonical_name` (case-insensitive), then `aliases[]` membership. New person → append. Existing person → MERGE conservatively: union `aliases[]`, union `affiliation_ids[]`, take latest non-empty `last_interaction`, preserve all other existing fields. Never overwrite a populated field with an empty one.
   - **Orgs:** match on `id` (canonical), then `domains[]` overlap (case-insensitive). New org → append. Existing → union `domains[]`, take latest `last_updated`.
   - **Projects (threads):** match on `id`, then `folder_name`, then `display_name` (case-insensitive). New project → append. Existing → union `stakeholder_person_ids[]`, take latest `last_activity`, preserve `status` and `stage` (don't overwrite from inferred lower-confidence values).
   - **Aliases:** key on `(raw, canonical_id)` tuple. Append new tuples; skip duplicates.
3. **Append new events** to events.jsonl. Match against existing events by `data.source_ref` first (if both have it), then by `(type, ts, primary_thread_id, data.title)` fingerprint. Skip duplicates. Use `atomic_append_jsonl` for the new events. After the batch, log the appended seq range in `_ingest-undo.jsonl`: `{"action":"events_append","first_seq":N,"last_seq":M,"count":K}` — undo strips exactly this range.
4. **Bump the entities.json `version` field** (monotonic) and set `last_writer: "workspace-ingest"`, `last_updated: <ingest_ts>`. (Note: `last_writer` on a merged file means "last touched by", NOT "created by" — undo must never key on it.)
5. **Validate** the merged result against the schema before atomic-writing. On failure: roll back to pre-merge state (restore `_hq/data/` from the Phase 3.9 snapshot), abort.

**Idempotency:** re-running ingest on the same source folder is safe. Every parsed entity / event has a stable id or fingerprint; second run finds matches and skips. `_ingest-undo.jsonl` records what was added on each run so even if the source changes between runs, undo still works.

### Phase 5: File discovery

After the data substrate is safely written, walk the source folder tree and enumerate every file that is not a structured-registry file (registries were already consumed in Phase 3). For each file, capture:

- `source_path` (absolute)
- `relative_path` (from source root)
- `size_bytes`
- `mtime` + `ctime`
- `ext` (lowercase)
- `sha256`
- `parent_folder_name` (last folder before filename)
- `grandparent_folder_name` (two up)
- `text_preview` (first 500 chars if text-parseable; empty for binary)

Skip:
- Anything in `_archive/` or already in `.git/`
- Files ≥ 100 MB (flag in report, don't copy — CEO decides)
- Hidden files starting with `.` (except meaningful ones: `.claude-plugin/plugin.json`)
- OS clutter (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `ehthumbs.db`)
- Files already consumed as registries in Phase 3 (tracked by the parser)

**Size gate — already applied.** The size gate ran BEFORE the Phase 2 backup; `files[]` arrives here already scoped to whatever the CEO chose there. Do not re-ask.

Produce an in-memory `files[]` array. No writes yet.

### Phase 6: File classification

For each discovered file, assign:

**File type** (pattern match on name, extension, folder, content):
- `session_notes` — name matches `SESSION_NOTES_*`, `session-notes-*`, `notes-YYYY-MM-DD*`, or contains dated-entry headings
- `meeting_transcript` — name matches `*Granola*`, `*transcript*`, `*call-notes*`, or folder is `meetings/`, `granola/`, `calls/`
- `project_context` — name matches `PROJECT_CONTEXT*`, `PROJECT_BRAIN*`, `CONTEXT.md`, `README-project*`
- `deliverable` — extension in {.docx, .xlsx, .pptx, .pdf, .key, .numbers, .pages} AND folder hints at "deliverables", "outputs", "final", "shared"
- `intel` — folder in {`intel/`, `research/`, `clippings/`, `reading/`} OR filename contains `intel-`, `research-`, `clip-`
- `briefing` — folder in {`briefings/`, `audit-reports/`, `cleanups/`, `morning-briefings/`} OR filename matches briefing patterns
- `decision_doc` — filename contains `DECISION`, `decision-memo`, `strategy-memo`
- `voice_sample` — filename matches `VOICE_SAMPLES*`, `voice-sample-*` (deprecated in v2.7.1 but captured for historical reference)
- `loose_doc` — default for anything that doesn't match a specific category

**Project assignment** (which project folder does this file belong to):
- **High confidence (≥0.9):** file is already inside a folder whose name matches a canonical thread's `folder_name`. Copy destination: obvious.
- **Medium confidence (0.7–0.9):** file contents mention a thread's canonical_name or a key alias ≥3 times, AND the filename or folder name has weak association. Needs CEO confirmation in preview.
- **Low confidence (0.4–0.7):** single mention or weak signal. Flag for `_ingest-queue/`.
- **Unknown (<0.4):** no signal. Route to `_ingest-queue/unclassified/`.

**Destination path** (computed based on type + project assignment):

| type | destination pattern |
|---|---|
| session_notes | `[Project]/SESSION_NOTES_[Project].md` (if one exists, append with dated header; if multiple historical files, concatenate chronologically) |
| meeting_transcript | `[Project]/meetings/[original_filename]` |
| project_context | `[Project]/PROJECT_CONTEXT.md` (if multiple, pick highest-mtime and stash others as `PROJECT_CONTEXT.archive-[ts].md`) |
| deliverable | `[Project]/deliverables/[original_filename]` |
| intel | `_hq/intel/[original_filename]` |
| briefing | `_hq/briefings/[original_filename]` |
| decision_doc | `[Project]/deliverables/memos/[original_filename]` if project known, else `_hq/memos/` |
| voice_sample | `_archive/voice-samples-historical/[original_filename]` (deprecated; preserved for reference only) |
| loose_doc + known project | `[Project]/_misc/[original_filename]` |
| loose_doc + unknown project | `_hq/_ingest-queue/unclassified/[original_filename]` |

Produce an in-memory `migration_plan[]` with one row per file: `{source_path, dest_path, type, confidence, project_id, reason, action}` where `action ∈ {copy, queue, skip, conflict}`.

### Phase 6.5: Registration gate (v3.16+)

**Why this exists.** A destination of the form `[Project]/…` will *create that folder on disk as a side effect of the copy* even when no thread record exists for it. That is how orphan project folders appear — a folder full of deliverables that the substrate has no record of, invisible to every daily flow and to `go [project]`. Filing must never bring a project folder into existence without the thread that owns it.

For every `migration_plan[]` row whose `dest_path` is under a top-level `[Project]/` folder (i.e. NOT under `_hq/`, `_archive/`, or an `_ingest-queue`/`_unrouted` bucket), confirm the destination project is **registered**: its `project_id` resolves to a thread in `entities.json`, OR the folder name exactly matches a thread `folder_name`. Then:

- **Registered →** file as planned.
- **Not registered, but a clear project name is implied** (high-confidence assignment to a name with no thread yet) → do **not** create the folder yourself (this skill is read-capable on the entity store only during its own Phase 4 writes; ad-hoc thread creation mid-filing is out of scope). Instead, in the Phase 7 preview, list these under a new bucket **"New project folders this would create"** and ask the CEO: *"[N] files want to go into projects I don't have a record of yet: [names]. Want me to set these up as real projects, or set them aside?"* On "set up" → hand the named threads to `workspace-manager` ("new project [name]") so they are registered + scaffolded the canonical way, THEN file. On "set aside" → reroute those rows to `_hq/_unrouted/[implied-name]/[original_filename]`.
- **Not registered and no clear project →** route to `_hq/_ingest-queue/unclassified/` as today.

This keeps the existing contract (orchestrators don't write the entity store directly; `workspace-manager` owns thread creation — see PROJECT_MAPPING_RULES "Forbidden behaviors") while closing the silent-folder-creation hole. The integrity checker's `C10.orphan_folder` finding is the backstop that catches any orphan that still slips through.

### Phase 7: Migration preview + CEO confirmation

Present a compact summary to the CEO before any file is copied:

```
Here's what I'm planning to file:

Filing into your projects:               412 files
  - Session notes:                        14
  - Meeting transcripts:                  89
  - Deliverables (docs, decks, sheets):  203
  - Intel and briefings:                  72
  - Other docs going into project folders: 34

Set aside for you to look at:             18 files
  - Not sure which project they belong to: 12
  - Couldn't place at all:                  6

Skipping:                                  3 files
  - Too big to copy (over 100 MB):          2 (names in the report)
  - Format I don't know how to read:        1

Already filed differently:                 0 files

Your source folder stays exactly as it is. If you change your mind, say "undo migration" and I'll roll everything back.
Look good?
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary (parser names, shape labels, and version numbers are internal — the announcement uses the plain labels from Phase 1).
- Bad: "Found your v1.8 plugin workspace — routing to Parser B."
- Good: "Found your older Command Room workspace. I'll pull it into your new Command Room — your originals stay untouched."

Allow three responses:
- **"yes" / "proceed":** continue to Phase 8
- **"show queue" / "show conflicts":** render the detailed file list for the named bucket, then re-prompt
- **"skip migration" / "data only":** skip Phase 8, write the report noting that file migration was declined, exit

If the CEO wants to reclassify a specific file before execution, accept "move [file] to [project]" or "queue [file]" and update `migration_plan[]` accordingly.

### Phase 7.5: Smart merge for context-bearing docs (v2.7.4+)

Before the generic copy loop runs in Phase 8, a dedicated merge pass runs for files classified as **context-bearing**: `session_notes`, `meeting_transcript`, `project_context`, `project_brain`, `decision_doc`.

**Rationale.** For these types, copy-as-separate-file produces clutter — three `PROJECT_CONTEXT` files in one project folder, or scattered meeting notes that should be consolidated. The value lives in the *content*, not the historical filenames.

**Pass logic:**

For each context-bearing file:
1. **Determine canonical target** — the canonical file that should own this content in the new workspace. E.g., `PROJECT_CONTEXT.md` for the project, `SESSION_NOTES_[Project].md` for session notes, `[Project]/meetings/[YYYY-MM-DD_topic].md` for meeting transcripts.
2. **If canonical target does not yet exist:** copy the source file to the target path (action stays `copy`). Log normally.
3. **If canonical target DOES exist** (either from onboarding's Phase 4 build or a prior ingest pass): **merge, don't overwrite.**
   - For `SESSION_NOTES_*`: append each source entry chronologically under a `## Ingested from [source filename] — [source mtime]` header. Preserve original ordering within the source.
   - For `PROJECT_CONTEXT.md` and `PROJECT_BRAIN.md`: append a `## Prior context (ingested [YYYY-MM-DD])` section at the bottom. Do not rewrite or re-order the existing content.
   - For `meeting_transcript`: route to `[Project]/meetings/` as usual but with filename `[YYYY-MM-DD]_[original-stem]_ingested.md` if a same-stem file already exists.
   - For `decision_doc`: append a `## Historical decision — ingested from [source]` section to the project's `DECISION_LOG.md` OR copy to `[Project]/deliverables/memos/` if no DECISION_LOG exists yet.
4. **Log the merge** in `_hq/_ingest-undo.jsonl` with `"action":"merge"` and both source + target paths, plus `"merged_section_header"` so undo can surgically strip the appended section.
5. **Remove the file from `migration_plan[]`** so Phase 8's copy loop doesn't re-process it.

**Important: Phase 7.5 is merge-only for context-bearing types.** Reference files (`intel`, `briefing`, `deliverable`, `loose_doc`, `voice_sample`) flow through Phase 8's copy loop unchanged — those are files, not context, and copy-as-separate is correct for them.

**After Phase 7.5 completes:** Phase 8 runs on the remaining (non-context-bearing) files and handles them with the existing copy / conflict / queue logic.

### Phase 8: Execute file migration (copies for non-context-bearing files)

For each row in `migration_plan[]` with `action == copy`:

1. Ensure destination folder exists (create recursively).
2. Check if destination file already exists.
   - If yes and `sha256` matches → skip silently (idempotent re-run).
   - If yes and `sha256` differs → rename the new file to `[stem].conflict-[YYYY-MM-DDTHHMMSS][ext]` and copy there. Log as conflict in `_ingest-undo.jsonl` and `INGEST_REPORT.md`.
3. Copy the file (never move).
4. Append one line to `_hq/_ingest-undo.jsonl`:

```json
{"ts":"2026-04-21T14:02:10Z","action":"copy","source":"/Users/.../old/foo.md","dest":"[Project]/meetings/foo.md","sha256":"abc...","size":12345,"confidence":0.92,"conflict":false}
```

5. If file type is `session_notes` and destination `SESSION_NOTES_[Project].md` already exists, append the source content under a dated header (`## Imported from [source_path] — ingested YYYY-MM-DD`) rather than overwriting.

For rows with `action == queue`:

- Copy to `_hq/_ingest-queue/[unclassified|low-confidence]/[original_filename]`.
- Write a companion `.meta.json` alongside each queued file describing: source_path, candidate projects with confidence scores, detected type, reason for queueing.
- Log in `_ingest-undo.jsonl` with `action: "queue"`.

For rows with `action == skip`:

- Do not copy. Log reason in report only.

After all file operations complete, append one `onboarding_step` event to `events.jsonl`:

```json
{"seq": <next>, "ts": "<now>", "type": "onboarding_step", "source_skill": "workspace-ingest",
 "data": {"step": "file_migration_complete", "counts": {"copied": N, "queued": N, "skipped": N, "conflicts": N}}}
```

### Phase 9: Write INGEST_REPORT + finish

Create `_hq/INGEST_REPORT.md` per the template in `references/ingest-report-template.md` (covers both data ingest and file migration). Append one final `onboarding_step` event:

```json
{"seq": <next>, "ts": "<now>", "type": "onboarding_step", "source_skill": "workspace-ingest",
 "data": {"step": "ingest_complete", "source_shape": "<shape>", "counts": {...}}}
```

Surface the `_ingest-queue/` to the CEO: *"18 files want your eyes on them — I wasn't sure where they belonged. Say `review ingest queue` to walk through them together, or come back to it later. No rush."*

Print the report summary and exit.

---

## Undo

On trigger: *"undo migration"*, *"undo ingest"*, *"roll back the migration"*, *"revert the ingest"*.

**The one rule: undo reverses ONLY what the undo log records this run as having done.** It never deletes anything based on a file attribute (an AUGMENT merge stamps `last_writer: "workspace-ingest"` on the user's pre-existing, merged substrate — a delete keyed on that attribute destroys their data). If there is no `run_start` line for the run being undone, refuse: *"I don't have a record of that run, so I won't guess at what to remove."*

1. Read `_hq/_ingest-undo.jsonl` in reverse order back to the run's `run_start` header line.
2. For each `copy` or `queue` row, delete the destination file (the source was never touched).
3. For each `conflict` row, delete the `.conflict-[ts]` destination file.
4. For each `merge` row, open the target file and strip exactly the section under the logged `merged_section_header` (the rest of the file predates this run — leave it).
5. **Substrate, by mode (from the `run_start` header):**
   - **BOOTSTRAP** (header shows no pre-existing data files, `data_create` rows present): archive the created `_hq/data/` files to `_archive/ingest-undone-data_[ts]/`, then remove them from `_hq/data/`. Only files with a `data_create` row this run — nothing else.
   - **AUGMENT:** restore `entities.json` and `aliases.json` from the `run_start` header's Phase 3.9 snapshot, and strip appended events from events.jsonl by the logged `events_append` seq range — but ONLY if no other writer has appended past the range since (check: current max seq == logged `last_seq`). If later events exist, do NOT rewrite events.jsonl (history is additive-only); instead restore entities.json/aliases.json from the snapshot and tell the user which N ingested events remain: *"I've restored your records to before the import. N imported history entries stay in the log because newer activity landed after them — they're deduped, so re-importing won't double them."*
6. After all deletes, remove now-empty destination folders (project `meetings/`, `deliverables/`, `_misc/`, `_ingest-queue/` subfolders).
7. Move `_hq/INGEST_REPORT.md` to `_archive/ingest-undone-data_[ts]/` and rename `_hq/_ingest-undo.jsonl` → `_hq/_ingest-undo-reverted-[ts].jsonl` (preserve for audit).
8. Announce: *"All set — rolled everything back. Your original folder is untouched, and I kept a backup copy just in case. Whenever you want to try again, just say the word."*

---

## Parser contracts (shared across shapes)

All parsers (A/B/C/D/E/F) emit the same in-memory collections with the same field contracts — **orgs[]**, **people[]**, **threads[]**, **events[]**, **aliases[]**. Full JSON field examples for every collection: `references/parser-contracts.md`. The load-bearing contract:

- **orgs[]:** `scope` ∈ {holding, operating, division, brand, fund, vendor, other}. `relationship_type` ∈ {operating, partner, board, advisory, investment, client, portfolio_company, beneficiary, vendor, referral, other}. `is_primary_focus` confirmed in onboarding Phase 2c, not at parse time.
- **threads[]:** the `project_` prefix on `thread_id` is retained for schema stability per the plugin-level `references/ORG_AND_THREAD_MODEL.md`; user-facing vocabulary is "project" (not "thread").
- **events[]:** each event follows the v2.2 schema in `shared/data-schemas/events.schema.json`. Every parsed event carries: `primary_thread_id` (owning thread); `related_thread_ids[]` (secondary threads mentioned, empty unless the entry spans multiple projects); `classification_confidence` = 1.0 for historical data parsed from static markdown (ground truth), 0.95 for decisions parsed from PROJECT_BRAIN glossaries, 0.9 for entries with fuzzy text signals; `person_ids[]` resolved from names in the entry; `org_ids[]` = affiliation of primary thread; `source_skill` = `"workspace-ingest"`; DEPRECATED `project_id` mirror of `primary_thread_id` for back-compat readers.
- **aliases[]:** confidence bands — **1.0** explicit alias (parenthetical in name header, explicit "aka" / "also known as" phrase); **0.9** high-signal inference (email alias + name match, folder name vs display name); **0.7** weak inference (glossary mention, contextual usage).

---

## Connector-Assisted Org Tree Inference (shared)

Every parser calls this pass after seeding orgs from source. Signals:

- **Email domain clustering** from Gmail — cluster sent/received by domain; each dominant cluster = likely org. Cross-reference source-parsed org names.
- **Calendar attendee clustering** — recurring-attendee groups suggest team/org boundaries.
- **Slack workspace mapping** — each workspace = one org (usually).
- **Drive folder clustering** — top-level folders often mirror org names.
- **Email signatures** — role + org title per person → confirms affiliation.
- **Legal/billing addresses** in contracts — holding vs operating entity boundaries.

For each source-parsed org, enrich with:
- `domains[]`, `slack_workspace_ids[]` from signals
- `parent_org_id` — if domain hierarchy suggests nesting (e.g., `@acme.example.com` parent, `@acmerestaurant.example.com` child)
- `scope` refinement — upgrade from default `operating` to `holding` if child-org signals detected; downgrade to `vendor` if only appears in invoices/contracts
- `relationship_type` refinement — match signature evidence
- `inferred_from[]` — list signal types that contributed

Confidence:
- 3+ signal sources → high, write as-is
- 1–2 signals → medium, flag for Phase 2c confirmation in onboarding
- 0 signals → write what source said, flag as "no connector confirmation"

If no connectors are available (cold start), skip this pass — source-parsed values stand.

---

## Idempotency

Re-running on a workspace that already has `_hq/data/entities.json`:

1. **AUGMENT is the normal path** — an existing entities.json is the skill's stated primary use case, not a stop condition. Run the Phase 4 AUGMENT merge; the dedupe rules (stable ids / fingerprints / sha256 content-hash guard) make a same-source re-run a no-op. Announce what the dedupe found: *"Looks like most of this is already in — I added the N new items and skipped the rest."*

Re-running after partial failure (some files written, not others — detected via an undo log whose last run has a `run_start` header but no completion event):

1. Detect the mixed state → announce "Something didn't finish cleanly last time. Best move is to roll it back and try again — say 'undo migration' and I'll roll it back." Offer the Undo path (which restores from that run's Phase 3.9 snapshot) + exit without writing. This partial-failure state is the ONLY one that blocks a re-run.

---

## Rollback

Any phase failure after Phase 3.9 (snapshot):

1. Restore `_hq/data/` from the Phase 3.9 snapshot (`_archive/pre-ingest-data_[ts]/` — recorded in the run's `run_start` undo-log line). Never blanket-delete `_hq/data/**`: in AUGMENT mode those files hold the user's pre-existing substrate.
2. Delete only files the undo log records this run as having created (copies, queue files, `data_create` rows).
3. Source folder was never touched — no restore needed.
4. Move any partially-written `_hq/INGEST_REPORT.md` to `_archive/` rather than deleting.
5. Append failure details to `_hq/CONFLICTS.md` (create if needed).
6. Announce: *"Rolled it back. Your original folder is untouched, and I kept a backup just in case."*

A failure between Phase 2 and Phase 3.9 needs no data restore (nothing wrote yet). Phase 1 failures (source not found, detection ambiguous) exit cleanly without touching anything.

---

## What It Doesn't Do

- **Does not modify the source folder.** Source stays pristine. Every file operation is a copy.
- **Does not move files.** Ever. Only copies. "Move" semantics belong to the CEO after they verify the migration.
- **Does not delete files.** The CEO decides if/when to delete the source folder after they're confident in the migration.
- **Does not silently overwrite.** Destination conflicts get `.conflict-[ts]` suffix and go in the report.
- **Does not touch cloud storage** (Dropbox, Google Drive, OneDrive) — NEXT RELEASE backlog item. Source must be local-filesystem accessible.
- Does not write views (`_hq/views/*`) — that's view-generation's job after ingest writes the JSON sources.
- Does not run automatically — invoked only by an explicit trigger phrase (directly, or via the ingest-context / file-documents alias skills).
- Does not re-ingest on repeat run — idempotent, detects existing state.
- Does not skip connector inference — always runs the enrichment pass (unless no connectors are available).
- Does not upgrade the plugin itself — that's the plugin update mechanism; this migrates customer data only.
- Does not ingest skill folders (`_hq/skills/**`) — skills are code, not entities.
- Does not guarantee perfect extraction — non-standard shapes drop to Parser D (generic) with warnings in INGEST_REPORT. Low-confidence files land in `_hq/_ingest-queue/` for manual review rather than being guessed.
- Does not write to `[Project]/PROJECT_CONTEXT.md` with invented content — only seeds from explicit source material (PROJECT_BRAIN.md, README-project.md). If no source exists, leaves PROJECT_CONTEXT.md unwritten for the CEO to fill in via onboarding Phase 2.

---

**End of workspace-ingest skill.** Parser details in `references/`.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Underlying pipeline for two intents: extract context from a source + (optionally) copy source files into workspace project folders. Two thin alias skills route to focused subsets of this pipeline — use ingest-context if you want context only (no file copies), use file-documents if you want both context + file copies into projects. Use workspace-ingest directly only for ambiguous intent — when you say 'review ingest queue', 'ingest folder [path]' and want the skill to detect what makes sense from the source content. Both layers copy-only (never moves or deletes), back up source folder first, write an undo log. Direct triggers (intent-ambiguous): 'ingest folder [path]', 'ingest this folder'. Default mode AUGMENT (existing workspace preserved); BOOTSTRAP mode only when workspace is empty. DOES NOT fire on focused triggers 'ingest context from [path]' (routes to ingest-context), 'file documents from [path]' (routes to file-documents), single-URL intake (intel-intake), or new-customer setup (command-room-onboarding).
