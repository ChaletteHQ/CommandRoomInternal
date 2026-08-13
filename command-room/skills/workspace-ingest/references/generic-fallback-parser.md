# Parser D — Generic Fallback (Unknown Shape, Best-Effort)

Handles source folders that match none of the known shapes (v1.x plugin, v2.x plugin, custom markdown). Pure best-effort extraction — scan the folder for anything that looks like person records, project records, decisions, or meeting notes, and pull what can be confidently extracted. Low confidence by design; flagged heavily in INGEST_REPORT so the CEO knows what landed and what didn't.

Typical triggers for Parser D:

- Folder of loose markdown notes with no `_hq/` or registry structure
- Exported Notion / Obsidian / Roam vault without plugin conventions
- Google Drive dump of client notes, meeting transcripts, decision memos
- Granola transcripts only (no other structure)
- Legacy workspace predating v1.4

---

## Entry point

Orchestrator dispatches here when shape detection fails to match Parsers A / B / C. See `../SKILL.md` → Phase 1 "Shape Detection".

Parser D announces its approach to the CEO before parsing:

> "I don't recognize this folder shape, so I'll do best-effort extraction. I'll scan every markdown file for person mentions, project mentions, decisions, and meeting notes — and pull what I can. Anything I'm unsure about gets flagged in the report for you to review. This extraction is low-confidence by design; you'll want to spot-check results."

---

## Scope

**In scope (scanned and best-effort parsed):**

- `*.md`, `*.markdown`, `*.txt` files — any depth in source folder
- Folder names that look like project folders (stable CamelCase or Title Case names at any depth)
- Filenames that look like dated session notes (`YYYY-MM-DD-*.md`, `session-YYYY-MM-DD.md`, etc.)
- Granola-style transcript files (`transcript*.md`, `meeting-*.md`)

**Out of scope:**

- Binary files (`.pdf`, `.docx`, `.xlsx`, `.pptx`) — mention existence in INGEST_REPORT but don't attempt extraction
- Code files (`.py`, `.js`, `.ts`, `.go`, `.sh`, etc.) — skipped
- Hidden folders (`.git/`, `.obsidian/`, `.vscode/`) — skipped
- Folders under `archive/`, `_archive/`, `.backups/`, `.trash/`, `Trash/` — skipped
- Media (`.png`, `.jpg`, `.mp4`, etc.) — skipped
- Files > 5 MB — skipped with warning (too large to safely parse for entity extraction)

---

## Extraction passes (run in order)

### Pass 1: Inventory the source

Walk the source tree. Build a file inventory:

- For each markdown/text file: path, size, modified date, first 200 chars, line count
- Detect dated filename patterns: `YYYY-MM-DD*`, `*-YYYY-MM-DD.*`, `session-*`
- Detect plausible project folders: depth-1 and depth-2 directories with CamelCase/Title Case names that contain at least one markdown file
- Detect plausible "registry" files by filename: `PEOPLE*.md`, `*-contacts.md`, `team.md`, `people.md`, `MASTER*.md`, `projects.md`, `tasks.md`, `decisions*.md`, `meetings.md`

Store inventory results for subsequent passes to reference.

### Pass 2: Identify candidate people sources

Scan inventory for files that look like person registries:

- Exact or case-insensitive match: `people.md`, `PEOPLE.md`, `contacts.md`, `team.md`, `roster.md`
- Heuristic match: any file where >30% of lines match `^[-*] \*\*[A-Z][a-z]+ [A-Z]` (bulleted person pattern) or `^### [A-Z][a-z]+` (heading person pattern)

For each candidate:

- Apply multi-format extraction (see below) to pull `canonical_name`, `email`, `role`, `org_name` where parseable.
- Confidence: 0.7 (heuristic match on registry) or 0.5 (heuristic match on arbitrary prose).
- Fields missing from the file → left null; filled opportunistically from Pass 4 (email mentions, etc.).

**Multi-format person extraction:**

- **Bulleted list:** `- **Name** — Role, Company. email@x.com, +1-555-1234`
- **Heading-based:** `### Name` followed by `- Role:`, `- Company:`, `- Email:` style bullets
- **Table:** pipe-separated rows with header row detected by keywords (`name`, `role`, `company`, `email`, `phone`)
- **Prose:** first+last name pattern followed by role/company within 100 chars — use sparingly, confidence 0.5

### Pass 3: Identify candidate project sources

Scan inventory for files/folders that look like project indexes:

- Exact filename matches: `MASTER_TRACKER.md`, `projects.md`, `tasks.md`, `backlog.md`, `initiatives.md`
- Heuristic: folders with a stable name at depth 1 or 2 that contain session-notes-like files
- Prose mentions: lines of the form `- [x] Project: <name>` or `## Project: <name>`

For each candidate project identified:

- Mint a thread record with:
  - `thread_id` = `project_NNN` monotonic
  - `display_name` = best candidate name (filename stem / heading text / folder name)
  - `folder_name` = folder containing the project's files (if any); else null
  - `kind` = `"initiative"` (default; no taxonomy signal available)
  - `stage` / `status` = `"active"` (default); if filename/folder contains `archive`/`archived`/`done`, set `"archived"`
  - `first_seen` / `last_activity` = oldest / newest file modified-date within the folder
  - `affiliation_id` = null (filled in Pass 6 via connector inference if possible; else flagged)
  - All other fields null/empty
  - `inferred_from` = `["generic-fallback-heuristic"]`
- Confidence: 0.6 (heuristic detection).

### Pass 4: Scan for event signals

Walk every markdown/text file. For each:

- **Date-headed sections** (`^## YYYY-MM-DD`, `^### YYYY-MM-DD`, `^## [Weekday], [Month] DD, YYYY`) → session boundary; emit one `interaction` event per section with:
  - `ts` = parsed date
  - `primary_thread_id` = resolved from file's parent folder (if matched in Pass 3); else `thread_hq_unassigned` (synthetic fallback)
  - `classification_confidence` = 0.7 (date header heuristic)
  - `data.summary` = first 200 chars of section body
  - `data.channel` = inferred (see below)

- **Decision keywords** — lines containing `decided`, `decision:`, `✅ decided`, `— agreed to` → `decision` event at confidence 0.6
- **Commitment keywords** — lines containing `will <verb>`, `TODO`, `- [ ]`, `action item`, `next step` → `commitment` event at confidence 0.5
- **Meeting keywords** — files with `transcript`, `meeting`, `call` in filename, OR sections with `## Attendees:` / `Participants:` → `meeting` event via `meeting_capture.build_meeting_event()` (BUG-8244: parsed names resolved → top-level `person_ids[]`; parsed emails → `data.attendees[]`; unresolved names → `data.attendees_external[]`)

**Channel inference:**

- Filename contains `granola` / `transcript` → `channel: "granola"`
- Filename contains `slack` → `channel: "slack"`
- Filename contains `email` / `gmail` → `channel: "email"`
- Section heading mentions "call" / "meeting" → `channel: "call"`
- Else → `channel: "note"`

### Pass 5: Extract aliases opportunistically

Across all files:

- `aka` / `a.k.a.` / `also known as` / `fka` phrases → alias at confidence 0.9
- Parenthetical alternates after a name (`[CEO] (M)`) → alias at 0.9
- Email local-parts that differ from detected first-name → alias at 0.8
- `@handle` mentions within 50 chars of a resolved person name → alias at 0.7

### Pass 6: Attempt connector inference

Run `../SKILL.md` → "Connector-Assisted Org Tree Inference" if any connectors available. This pass is especially valuable in generic-fallback mode because source-parsed signal is thin — connector evidence often doubles the confident data volume.

For each org mentioned in person/project extraction:

- Populate `domains[]`, `slack_workspace_ids[]` from connectors
- Detect `parent_org_id` from domain hierarchy
- Set `scope` based on signal volume (holding if child signals detected; operating default; vendor if only invoice/contract signals)
- Set `relationship_type` from email signature / calendar invite patterns

Orgs that get 3+ signal sources → mark high-confidence.
Orgs that get 0 signals and were minted purely from prose extraction → mark very-low-confidence; flag in INGEST_REPORT as "weak mention only, may not be a real org".

### Pass 7: Mint synthetic defaults for orphans

- If any events got `primary_thread_id = thread_hq_unassigned`, mint a synthetic thread:
  ```json
  {
    "thread_id": "thread_hq_unassigned",
    "display_name": "Unassigned / Generic",
    "folder_name": null,
    "kind": "initiative",
    "stage": "active",
    "status": "active",
    "affiliation_id": null,
    "owner_person_id": null,
    "notes": "Synthetic catch-all thread for events parsed from files that didn't match a project folder. Review and reassign.",
    "parent_thread_id": null,
    "inferred_from": ["generic-fallback-orphan-bucket"]
  }
  ```

- If no people records got extracted but a workspace user exists (detected from `git config user.name` in the folder if any, or from email signals), mint a minimal `person_001` for the user so events have someone to link to.

---

## Confidence banding

Parser D's confidence bands are intentionally lower than other parsers:

| Extraction type | Confidence |
|---|---|
| Structured registry file matched by exact name | 0.8 |
| Bulleted list in registry-named file | 0.7 |
| Prose mention of name + role + company within 100 chars | 0.5 |
| Date-headed section → interaction event | 0.7 |
| Decision-keyword line → decision event | 0.6 |
| Commitment-keyword line → commitment event | 0.5 |
| Meeting-filename → meeting event | 0.7 |
| `aka` / `fka` phrase alias | 0.9 |
| Parenthetical alias | 0.9 |
| Email-local-part alias | 0.8 |

Any event emitted below confidence 0.7 goes into the `classifier_feedback.jsonl` review queue (not the main events.jsonl until confirmed). The orchestrator handles this routing during Phase 4 write.

---

## Heavy INGEST_REPORT flagging

Parser D's output gets extensive flagging in `_hq/INGEST_REPORT.md`:

- **Source shape note:** "Generic fallback — no recognized plugin or custom-markdown shape detected. Extraction is best-effort."
- **Coverage summary:** N files scanned, N parsed, N skipped (list with reasons)
- **Per-entity confidence distribution:** histogram of confidence scores
- **Low-confidence entities:** explicit list of people/orgs/threads minted below 0.7 — CEO-visible "please review these"
- **Orphan events:** events that couldn't be tied to a thread — landed in `thread_hq_unassigned`
- **Unknown file types:** list of non-markdown files found (for CEO awareness)
- **Recommended next step:** "Spot-check the INGEST_REPORT entity lists against your source folder. If the extraction is reasonable, confirm. If not, reset and provide a more structured shape."

---

## Quirks

- **Pure dump of Granola transcripts** — treat each transcript as a meeting event. Resolve attendees by prose. Mint a synthetic `thread_hq_unassigned` for all of them if no project structure exists.
- **Obsidian vault with `[[wikilinks]]`** — wikilink targets that look like person names → person entity; wikilink targets that look like project names → thread entity; all wikilinks contribute to alias inference.
- **Notion export** — each page is a markdown file. Folder structure often maps to database/page hierarchy. Treat top-level folders as potential project containers.
- **Roam export** — block references (`((blockID))`) are noise; strip and log.
- **Hybrid shapes** (e.g., partial v1.x registry + random notes) — Parser D handles the random notes; if v1.x signals were present, shape detection should have routed to Parser A. If it routed here, log the hybrid finding in INGEST_REPORT.
- **Non-English content** — parse but flag; entity extraction heuristics are tuned for English. Confidence reduced by 0.2 across the board for non-English files.
- **Timestamped filename dumps** (e.g., `2026-01-15-1247-call-with-client.md`) — treat as individual meeting events with `ts` parsed from filename.
- **Stream-of-consciousness notes** — low signal-to-noise. Confidence capped at 0.5 even for strong-looking matches.

---

## Back-compat guardrails

- Source folder never modified.
- Parser D never overwrites prior extraction — if re-run against the same source (which shape detection should block), orchestrator handles state check.
- Every entity minted has `inferred_from[]` including `"generic-fallback-heuristic"` so downstream views can filter low-confidence material.

---

## Output hand-off

Parser D returns the five collections (orgs / people / threads / events / aliases) to orchestrator Phase 4. Events below confidence 0.7 are flagged with `_needs_review: true` so orchestrator can route them to `_hq/data/classifier_feedback.jsonl` instead of `events.jsonl`.

The INGEST_REPORT produced here leans heavier than other parsers' reports — the CEO needs more visibility into what landed and why.
