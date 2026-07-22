# Parser F — Folder Mode (v2.14.20+)

Handles a folder of actual deliverable documents — `.docx`, `.pdf`, `.xlsx`, `.pptx`, `.md`, `.txt`, images. Typical sources: `~/Desktop/`, `~/Downloads/`, a legacy project folder copied from a prior consultant, an unzipped client document drop, a copy of an old engagement's deliverables.

This parser absorbs the former `context-ingestion` skill (retired in v2.14.20). Same flow, now part of `workspace-ingest`'s 2-layer architecture.

**Use case:** the customer has a workspace already (entities.json populated by onboarding) and wants to pull supplementary material in. *"I have a folder of employee files I'd like to file under each direct report"* / *"My Downloads pile has 30 random docs from the last 2 months"* / *"I want to pull my ChatGPT context but also file the actual conversation transcripts."*

**Scoping rule:** Parser F runs in AUGMENT mode only. If `_hq/data/entities.json` doesn't exist, refuse:

> *"Your workspace doesn't have a data substrate yet. Run `set up command room` first to onboard, then `ingest folder [path]` to pull these files in. Random files alone don't seed a viable workspace."*

This prevents accidental bootstrap-from-random-folder, which would create a noise-heavy empty workspace.

---

## Two-layer flow

Parser F is unique in that **both Layer 1 and Layer 2 fire** for every run. The other parsers (A-E) are mostly Layer 1 (data substrate) — Parser F is the primary Layer 2 driver because random folders are mostly about file filing.

### Layer 1 (light) — context extraction from filenames + metadata

Walk the folder. For each file:
- Capture filename, extension, size, creation date, last-modified date.
- For text-readable formats (`.txt`, `.md`, `.csv`, `.html`, `.json`): read content directly.
- For binary formats with skill readers (`.docx`, `.xlsx`, `.pptx`, `.pdf`): use the corresponding reader (docx, xlsx, pptx, pdf skills).
- For images / videos / executables / archives: inventory only (filename + size). DON'T attempt content extraction.

From the read content + filenames, low-confidence-extract:
- **People** mentioned in document headers, signature blocks, attendee lists. Match against existing `entities.json` people via canonical_name + email + aliases. Flag unmatched names as candidates (don't auto-create entities — surface in INGEST_REPORT for CEO confirmation).
- **Orgs** mentioned in letterheads, contract parties, email domains in cited correspondence. Same matching rule.
- **Projects** suggested by recurring filename patterns (e.g., 8 files starting with `NorthStar_` in a folder where `NorthStar` is already an existing project → high-confidence file-to-project mapping; 5 files starting with `RestaurantConcept_` where no such project exists → flag as candidate-new-project for CEO confirmation).

Layer 1 writes are conservative. Parser F never auto-creates a new person or org from a folder scan — too noisy. New entity candidates surface in the INGEST_REPORT for the CEO to confirm via the standard people-crm / workspace-manager entity-creation flow.

### Layer 2 (heavy) — document filing into project folders

This is the primary value of folder-mode. After Layer 1 extracts context, classify each readable file by which existing project (or `_hq/`) it belongs to:

**Classification categories:**

| Category | Destination | Behavior |
|---|---|---|
| `→ [Existing Project Name]` | `<project_folder>/ref/<filename>` | File relates to a tracked project. Copy into `ref/`. |
| `→ NEW: [Suggested Name]` | (deferred) | File suggests a project that doesn't exist yet. Surface as candidate-new-project in the preview. CEO can either approve the new project (workspace-manager creates it; file copies into the new `ref/`), or reroute to General. |
| `→ General` | `_hq/_filing/<filename>` | Useful business context, not project-specific. |
| `→ Skip` | (no copy) | Irrelevant, system files, duplicates of existing copies, unreadable. |

**Classification heuristics (rank by signal strength):**
1. Filename contains existing project name (case-insensitive) → that project. Strongest signal.
2. File content mentions existing project name 3+ times → that project.
3. File content mentions a known person from that project's stakeholder list → that project.
4. File content mentions an existing org → if that org has a primary project, route there; else General.
5. None of the above → General (with a note in the preview "I couldn't tell where this belongs").

**Hard caps for noise control:**
- Folder with > 200 readable files: ask CEO if they want to focus on a subfolder or file-type subset first. Don't try to classify hundreds of files in one widget.
- Per-project file cap: 25 files per project per ingest. If a project would receive more than 25, surface the overflow in INGEST_REPORT and ask CEO to split into a separate ingest run.

---

## Preview-and-confirm widget (Layer 2 surface)

Parser F's classification preview renders as a chat-action widget per `shared/CHAT_ACTION_WIDGET.md` — same canonical surface as the scheduled-task widgets. Per-category checkbox rows; CEO confirms before any file copy.

Widget shape (data view):

```python
data_view = {
    "widget_mode": "all_batch_widget",  # standard widget shape; not all_clear_summary
    "header": f"Folder ingest preview — {n_total} files in [{folder_path}]",
    "sub_header": f"Layer 1 (context) added {n_entities_proposed} entity candidates. Layer 2 (filing) is opt-in below.",
    "sections": [
        {
            "title": f"Existing projects ({n_to_existing} files)",
            "count": None,
            "items": [
                {
                    "n": "1",
                    "icon": "📁",
                    "name": project_name,
                    "context_tag": f"{n_files_for_project} files would land in {project_name}/ref/",
                    "body_lines": [f"- {filename}" for filename in files_for_project[:8]],  # first 8 inline; rest counted
                    "actions": ["1 confirm", "1 add context [text]", "1 skip"],
                }
                # ... one per project
            ],
        },
        {
            "title": f"Candidate new projects ({n_candidate_new})",
            "count": None,
            "items": [
                {
                    "n": "10",
                    "icon": "✨",
                    "name": candidate_name,
                    "context_tag": f"{n_files_for_candidate} files cluster around '{candidate_name}'",
                    "body_lines": [f"- {filename}" for filename in files_for_candidate[:8]],
                    "actions": ["10 confirm [text]", "10 skip"],
                    # confirm [text] opens textarea: "Create project as: [pre-filled candidate_name]"
                }
                # ...
            ],
        },
        {
            "title": f"General ({n_general} files)",
            "count": None,
            "items": [
                {
                    "n": "20",
                    "icon": "📂",
                    "name": "General business",
                    "context_tag": f"{n_general} files → _hq/_filing/",
                    "body_lines": [f"- {filename}" for filename in general_files[:8]],
                    "actions": ["20 confirm", "20 skip"],
                },
            ],
        },
        {
            "title": f"Unreadable / skipped ({n_skipped})",
            "count": None,
            "items": [
                {
                    "n": "30",
                    "icon": "⊘",
                    "name": "Skipped",
                    "context_tag": f"{n_skipped} files won't be filed (system / images / archives / duplicates)",
                    "body_lines": [f"- {filename}" for filename in skipped_files[:8]],
                    "actions": [],  # no actions — informational only
                },
            ],
        },
    ],
    "save_confirmation": None,
}
from widget_transport import render_and_persist
transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="workspace-ingest")
# Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) (EW2+T, F-15 —
# shared/CHAT_ACTION_WIDGET.md § Transport). Never hand-compose or post-process the HTML.
```

**Action semantics:**
- `confirm` (per project / per general bucket) — copy all files in that bucket into the destination folder. Each copy logs a `file_filed` event (v2.14.19+ — see schema).
- `add context [text]` — CEO types a per-bucket context note (e.g., "these are all from the Q3 review"). Note gets attached to the bucket's first `file_filed` event as `data.context`.
- `confirm [text]` (candidate new project only) — pre-fills with the suggested project name. CEO can edit. On confirm, fire `workspace-manager` `new project [edited_name]`, then copy files into the new project's `ref/`. (MLK1 retired the `add to my list` defer that used to sit here; skipping the bucket or leaving it unanswered covers "not now".)
- `skip` (per bucket) — drop the bucket entirely. No copy. No event.

**The widget is the only Layer 2 trigger.** Parser F NEVER copies files without explicit confirmation. Even in `auto_apply: true` mode (used by other parsers when called from onboarding), Parser F still requires a Layer 2 widget click — the file-filing decision is too consequential to skip CEO review.

---

## File copy execution (after Layer 2 confirmation)

For each confirmed file:

1. Compute destination path:
   - Existing project: `<workspace>/<project_folder>/ref/<filename>`
   - New project (just created): `<workspace>/<project_folder>/ref/<filename>`
   - General: `<workspace>/_hq/_filing/<filename>`

2. Check destination existence:
   - If destination doesn't exist: copy.
   - If destination exists with same sha256: skip (idempotent re-run).
   - If destination exists with different sha256: append `.conflict-<ts>.<ext>` to the destination filename and copy. Log warning.

3. Copy via `shutil.copy2` (preserves metadata). Source is never modified.

4. Append a `file_filed` event to events.jsonl per the v2.14.19+ schema:

```json
{
    "seq": <next>,
    "ts": <now ISO>,
    "type": "file_filed",
    "source_skill": "workspace-ingest",
    "primary_thread_id": "<destination project id, or null for General>",
    "person_ids": [],
    "data": {
        "source_path": "<original absolute path>",
        "destination_path": "<workspace-relative destination>",
        "sha256": "<file content hash>",
        "size_bytes": <int>,
        "category": "existing_project" | "new_project" | "general",
        "context": "<optional CEO note from add context [text]>"
    }
}
```

5. Append to `_hq/_ingest-undo.jsonl` so undo can reverse the copy.

6. After all confirmed buckets complete, append a `pack_run` event summarizing the fire (for telemetry).

---

## What Parser F does NOT do

- **Does NOT auto-create new entities.** Layer 1 finds candidates; surfaces them in INGEST_REPORT for the CEO to confirm via people-crm / workspace-manager. The folder-scan signal is too noisy to auto-write to entities.json.
- **Does NOT modify source files.** Every operation is a copy. Source folder stays pristine.
- **Does NOT deep-extract content for context unless explicitly asked.** Layer 1 does light filename + signature-block extraction. The CEO can run `tell me about <file>` after ingest to get a deeper read.
- **Does NOT bootstrap an empty workspace.** Refuses if `_hq/data/entities.json` is missing (see "Scoping rule" above).
- **Does NOT process > 200 files in one widget.** For larger folders, asks the CEO to subset first.
- **Does NOT auto-classify ambiguous files as `New project`.** Candidates surface for explicit confirmation, never auto-write.

---

End of Parser F spec.
