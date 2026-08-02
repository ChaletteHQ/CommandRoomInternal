# Workspace Maintenance Rules

> These rules govern automatic cleanup across the Command Room workspace.
> They execute silently as part of existing flows — the user never has to run maintenance manually.
> Thresholds are overridable via CUSTOM_CONFIG.md if it exists.

---

## When Maintenance Runs

Maintenance is NOT a standalone command. It piggybacks on flows that already read the workspace:

| Trigger | Maintenance Actions |
|---------|-------------------|
| **"end session"** | Micro-cleanup: session notes rollover check, brain thread pruning, commitment archival |
| **"what's going on"** | Read-time cleanup: briefing snapshot pruning, stale flag refresh |
| **cleanup** | Full sweep: all rules below, plus reporting on what was cleaned |
| **"deep clean"** (manual) | Everything audit does + interactive: suggests archiving, profile pruning, context compression |

---

## Rule 1: Session Notes Rollover

**Problem:** SESSION_NOTES files grow indefinitely. After 6 months of daily use, a single file can exceed 20KB — consuming massive context window on every "go [project]."

**Threshold:** When a SESSION_NOTES file exceeds 150 lines (roughly 8-10KB):

**Action (on "end session"):**
1. Keep the `## Current Status` and `## Active Work Items` sections intact (these are the live state)
2. Keep the 5 most recent session log entries
3. Before archiving, generate a `## Session History` summary section in the active file — one line per archived entry: `- [DATE]: [5-word summary of what happened]`. This stays in the active file permanently so the CEO can see at a glance what happened on any date and ask for detail if needed.
4. Move older full entries to `SESSION_NOTES_[NAME]_archive_[YYYY].md` in the same project folder
5. Add a reference line at the bottom of active file: `> Full entries archived to SESSION_NOTES_[NAME]_archive_2026.md`
6. Log in session: "Rolled over session notes for [Project] — [X] older entries archived."

**Never delete.** Always archive. The CEO should be able to find old entries if needed.

**If the CEO asks about something in the archive:** Read the archive file to answer. The Session History summary tells you which date to look at.

---

## Rule 2: Project Brain Thread Compression

**Problem:** Active Threads table in PROJECT_BRAIN.md accumulates entries. Resolved threads sit indefinitely, adding noise to every "go [project]" briefing.

**Threshold:** Any thread marked "Resolved"/"Closed"/"Done" for more than 30 days:

**Action (on "end session"):**
1. Threads resolved less than 30 days ago: leave in Active Threads table as-is. They might reopen and the context is still relevant.
2. Threads resolved more than 30 days ago: compress in-place to a single-line entry in a `## Thread History` section at the bottom of the brain file. Format: `- [Thread name] — resolved [date], [outcome in ~10 words]` (e.g., "Vendor selection — resolved Apr 2, went with Option B at $45K")
3. Don't touch threads that are still active, waiting, or in-progress
4. Thread History section grows indefinitely but each entry is one line, so the cost is minimal

**Why 30 days, not 14:** A resolved thread can reopen. Two weeks is too aggressive — a CEO might circle back after a 3-week vacation or a delayed response from a vendor. 30 days gives enough buffer that if it's coming back, it's still there.

**Why compress, not delete:** The brain is institutional memory. A thread that was resolved last quarter might be exactly the context needed when a similar issue comes up. One-line summaries are cheap to store and scan.

---

## Rule 3: Commitment Compression

**Problem:** Active Commitments tables in MASTER_TRACKER and PERSON.md files grow with delivered items. But commitment history IS the track record — deleting it undermines the team intelligence skill's core value.

**Principle: Never delete commitment data. Compress it.**

**Thresholds:**
- Delivered commitments older than 60 days → compress to one-liner
- MASTER_TRACKER commitment section exceeds 30 active rows → compress delivered items

**Action (on "end session"):**
1. In MASTER_TRACKER: delivered commitments older than 60 days get compressed from full table rows to one-line entries in a `## Commitment History` section: `- [Person]: [commitment summary] — delivered [date]`. History section grows indefinitely (one line each — cheap).
2. In PERSON.md files: delivered commitments stay in the `## Completed Commitments` table for 60 days with full detail (due date, source, delivered date). After 60 days, compress to a one-liner in a `## Commitment History` section: `- Delivered [commitment] ([date])`. Never trim, never delete. This is the person's track record.
3. MASTER_TRACKER active commitments (not yet delivered): never touch. These are live accountability.

**Why no trimming on person profiles:** "What has Bowie delivered this year?" is a killer feature. If we delete the evidence after 30 days, we've destroyed the thing the CEO is paying for. One-line history entries cost almost nothing — 50 commitments compressed = 50 lines = ~2KB. That's fine.

**Why 60 days, not 30:** The CEO needs full detail for recent deliveries (was it on time? what was the source? any notes?). After 60 days, the pattern matters more than the detail.

---

## Rule 4: Briefing Snapshot Pruning

**Problem:** Every "what's going on" saves a briefing to `_hq/briefings/`. Daily use = 365 files/year.

**Threshold:** Briefing files older than 30 days.

**Action (on "what's going on", before saving new briefing):**
1. Check `_hq/briefings/` for files older than 30 days
2. Move files older than 30 days into `_archive/briefings/` (archived, never deleted — preserve the filename)
3. Keep the most recent 30 briefings in place regardless of age (safety net)
4. No logging needed — this is silent housekeeping

**Archive-only policy (Command Room build, 2026-06):** nothing here is ever deleted. Aged caches are *moved* to `_archive/` so a non-technical CEO is never surprised by a vanished file.

**Exception:** If a briefing filename contains "important" or "keep", leave it in place (don't even archive it).

---

## Rule 5: Audit Report Pruning

**Problem:** cleanup generates reports that accumulate in `_hq/cleanup-reports/`.

**Threshold:** Audit reports older than 90 days.

**Action (during cleanup, before saving new report):**
1. Keep the 12 most recent audit reports in place (roughly quarterly coverage)
2. Move older ones into `_archive/cleanup-reports/` silently (archived, never deleted)
3. No logging needed

---

## Rule 6: 1:1 Prep Archival

**Problem:** Every "prep me for my 1:1" saves a prep brief to `_people/prep/`. With 5 direct reports and weekly 1:1s, that's 260 files/year.

**Threshold:** Prep files older than 14 days.

**Action (during team-intelligence 1:1 prep, before saving new prep):**
1. Move prep files older than 14 days into `_archive/people-prep/` (archived, never deleted)
2. Keep the 3 most recent preps per person in place regardless of age
3. No logging needed — preps are point-in-time snapshots, not permanent records

---

## Rule 7: Interaction Log Tiered Compression

**Problem:** Person profiles have interaction logs that archive old entries to "Previous Interactions." Over time, Previous Interactions grows unbounded. Hard-trimming destroys the relationship history that makes the team intelligence skill valuable.

**Principle: Compress progressively, never delete. Recent interactions need detail. Old interactions need existence proof.**

**Tiers:**

| Tier | Age | Format | Example |
|------|-----|--------|---------|
| **Tier 1: Full Detail** | 0–90 days | Keep as-is — date, type, summary, source | `2026-03-15 — Meeting — Discussed Q2 roadmap, agreed on 3 priorities (source: 1:1 prep)` |
| **Tier 2: One-Line Summaries** | 90 days – 6 months | Compress to: `[DATE]: [type] — [5-word summary]` | `2026-01-10: Email — Sent revised budget proposal` |
| **Tier 3: Monthly Digests** | 6 months – 1 year | One line per month: `[MONTH YYYY]: [X] interactions — [key theme]` | `Oct 2025: 8 interactions — onboarding and role setup` |
| **Tier 4: Annual Archive** | 1 year+ | Move to `_people/[name]_interaction_archive_[YYYY].md`. One-line summary stays in main file: `> [YYYY] archive: [X] interactions. See [name]_interaction_archive_[YYYY].md` | Archive file with full Tier 3 digests; main file keeps the pointer |

**Action (on "end session", when writing person file updates):**
1. Scan the Previous Interactions section of each updated person file
2. Apply tier compression based on entry dates:
   - Entries 0–90 days old: leave at full detail (Tier 1)
   - Entries 90 days – 6 months old: compress in-place to one-line summaries (Tier 2)
   - Entries 6 months – 1 year old: roll up into monthly digest lines (Tier 3)
   - Entries older than 1 year: move to archive file, leave pointer in main file (Tier 4)
3. No notification to CEO unless Tier 4 archive is created for the first time (mention once: "Archived [name]'s 2025 interaction history — still searchable on request.")

**Action (during cleanup / deep clean):**
- Run the same tiered compression across ALL person files (not just ones touched this session)
- Report: "Compressed interaction logs: [X] entries across [Y] profiles"

**Why tiers instead of hard-trim:** "What was our relationship with Aria like last summer?" is a question the CEO will ask. Hard-trimming to 20 entries means 6 months of history vanishes. Tiered compression keeps the answer available — just progressively less detailed. Monthly digests cost ~12 lines per year per person. That's negligible.

**When the CEO asks about archived interactions:** Read the archive file. The monthly digests tell you which months to focus on. If they need exact entries, the archive has Tier 3 digests that point to the right timeframe; session notes and meeting notes have the raw detail.

**Override:**
```
interaction_tier1_days: 120      # default: 90
interaction_tier2_months: 9      # default: 6
interaction_tier3_months: 18     # default: 12
```

---

## Rule 8: Master Tracker Hygiene

**Problem:** the Recently Archived section grows indefinitely. (CTS1 2026-07: the "Quick Tasks" / "Completed Quick Tasks" markdown lane is RETIRED — quick tasks are `kind: task` commitment events now, closed through `close_commitment` like everything else. If a legacy "Quick Tasks" section with live rows is found, run the one-time migration in workspace-manager's "quick task:" handler — convert live rows to events, file "Completed Quick Tasks" rows under `## Archived (history)`, remove the sections. Never re-scaffold them.)

**Thresholds:**
- Recently Archived entries older than 90 days → move to the tracker's `## Archived (history)` section (the archived project folder still exists)

**Action (on "end session"):**
1. Check the section. Move entries past threshold into a `## Archived (history)` section at the bottom of MASTER_TRACKER.md (create it if missing) — never delete a row; the tracker keeps its own history in place.
2. Log once in session summary if anything was cleaned: "Filed [X] old entries into the tracker's archive section."

---

## Rule 9: Context Window Budget

**Problem:** "What's going on" reads MASTER_TRACKER + BUSINESS_CONTEXT + PEOPLE.md + session notes for every active project + brains + connector data. As the workspace grows, this pre-load can consume 50%+ of the context window before the user asks anything.

**Mitigation (read-time optimization, not cleanup):**
1. **Session notes:** Read only `## Current Status` and `## Active Work Items` + the 1 most recent session log entry. Do NOT read the full file during briefings. Full file is only read during "go [project]".
2. **Project brains:** Read only People, Gotchas, and Active Threads. Skip Resolved Threads, Custom Workflows, and Key Context during briefings. Full brain is read during "go [project]".
3. **PEOPLE.md:** Read only names and roles for matching against email/calendar. Don't load full profiles during briefing.
4. **Person profiles:** During briefing, only check commitment status and last-interaction date. Don't load full profiles. Full profiles loaded only during "prep [name]" or "team status".
5. **Connector data:** Summarize in 1-2 lines per item found. Don't include full email bodies or message threads.

This is a read optimization, not a cleanup rule. It doesn't delete anything — it just controls how much gets loaded.

---

## Rule 10: Annual Rollover

**Problem:** After a full year of use, even with per-file maintenance, the workspace as a whole has accumulated significant history.

**Action (during first cleanup of January, or on "deep clean"):**
1. Archive previous year's briefings to `_hq/briefings/archive-[YYYY]/`
2. Archive previous year's cleanup reports to `_hq/cleanup-reports/archive-[YYYY]/`
3. Session notes files that were rolled over get their archive files moved to a `_archive/session-history/` folder
4. DECISION_LOG.md entries older than 12 months get moved to `_hq/DECISION_LOG_archive_[YYYY].md`
5. Prompt CEO to review: "It's a new year. Want to review which projects are still active vs. should be archived? I can walk through each one."

---

## Deep Clean Command ("deep clean" / "maintenance" / "clean up my workspace")

This is the manual trigger for comprehensive maintenance. Runs all rules above PLUS interactive judgment calls:

0. **Substrate integrity check (v3.16+; weekly since v3.19.x / SPEC CLEAN1).** Before anything else, run `python3 shared/scripts/integrity_check.py <workspace_root>` and fold its findings into the report. This is the deterministic version of the consistency checks cleanup describes in prose — it catches orphan folders (`C10`), threads whose `folder_name` no longer exists on disk (`C9`, e.g. a project moved/archived without updating its record), missing `PROJECT_BRAIN.md` (`C11`), missing `SESSION_NOTES` (`C11b`), dangling event references (`C7`), dead aliases (`C8`), unresolved affiliations/cycles (`C2`/`C3`/`C4`), and duplicate event `seq` collisions (`C12`, a real multi-machine append hazard). ERROR-severity findings (duplicate seq, unresolved required refs) MUST be surfaced prominently — they indicate possible data loss. The checker is strictly read-only; it never fixes. Feed its `C10`/`C11`/`C11b`/`C9` output into step 2, the brain-backfill rule (Rule 15), and the session-notes backfill (Rule 16) so the fixes target exactly what's broken.

   > **Weekly vs. deep clean (D2).** The structural-folder subset of these checks — orphan folders (`C10`), missing brains (`C11`), missing session notes (`C11b`) — is **NOT deep-clean-only**. The weekly `cleanup` fire runs it on every pass via its Phase 1.0 code block (`integrity_check.scan_project_structure`), because orphans and missing files accumulate seven days a week. Deep clean keeps the fuller pass above (the complete `integrity_check` run plus the interactive judgment steps below). The pre-CLEAN1 model — full integrity_check only on deep clean — let drift stay invisible between rare deep-clean runs.
1. Run all automatic rules (1-9) and report what was cleaned
2. **Project review:** For each project, show last activity date + stage. Ask: "Still active? Archive? Change stage?"
2.5. **PROJECT_BRAIN backfill (Rule 15).** For every active project folder the integrity check flagged `C11.missing_brain`, scaffold a `PROJECT_BRAIN.md` from `references/project-brain-template.md` — see Rule 15 for the procedure. A project without a brain has no institutional memory layer; this closes the gap for projects that were created outside the "new project" lifecycle (filed-into folders, hand-created folders, legacy migrations).
3. **Team roster review:** For each person in `_people/`, show last interaction date. Ask: "Still tracking? Remove?"
4. **Business context freshness:** Read BUSINESS_CONTEXT.md "Last Updated" date. If older than 60 days, prompt for update.
5. **Stale exploring items:** Any exploring items older than 60 days → prompt to commit or archive
6. **PEOPLE.md dedup:** Scan for potential duplicate entries (similar names, same company). Present for merge.
7. **File size report:** List any file over target size (from WORKSPACE_SCHEMA). Offer to compress/split.
8. Save a maintenance report to `_hq/cleanup-reports/[DATE]-cleanup.docx` (per CONTRACT Rule 27, no .md deliverables).

**This is the command that M runs as part of the recurring service.** It does the judgment-heavy work that automatic rules can't handle.

---

## Rule 11: MASTER_TRACKER Rolling Backup

**Problem:** MASTER_TRACKER.md is the workspace's most-read view — the orientation surface every skill and every human glance starts from. It is a *projection* of `_hq/data/entities.json` + `events.jsonl` (canonical state is those files, per `references/SOURCE_OF_TRUTH.md`), so a lost tracker is recoverable by rerunning `render_master_tracker.py`; what a backup preserves is any hand-authored section the renderer does not own, plus a readable snapshot of how things looked before this session touched them.

**Principle: Keep 3 timestamped copies in the one backup folder — `_hq/data/_backups/`, the same place `atomic_write` restores substrate files from. Archive anything older — never delete. Simple rolling window.**

**Action (on "end session", BEFORE any tracker updates):**
1. Check if `[WORKSPACE_ROOT]/_hq/data/_backups/` exists. Create it if not.
2. Copy `_hq/MASTER_TRACKER.md` to `_hq/data/_backups/MASTER_TRACKER_[YYYY-MM-DD_HHMM].md` (24-hour time, e.g., `MASTER_TRACKER_2026-04-14_0930.md`).
3. List all files in `_hq/data/_backups/` matching the `MASTER_TRACKER_*.md` pattern.
4. Sort by filename (which sorts by date since the format is chronological).
5. If more than 3 files **match that pattern**, move everything except the 3 most recent MATCHES into `_archive/backups/` (archived, never deleted). Rotation is scoped to `MASTER_TRACKER_*.md` and nothing else: `_hq/data/_backups/` is shared with the `entities.json` / `aliases.json` backups, which are the only thing `atomic_write` can restore a substrate file from. Reading step 5 as "keep 3 files in the folder" would delete the substrate's safety net on every end session — the hygiene rule is the 3 newest **of each backed-up file**, not 3 files total.
6. No logging to the user unless asked. This is silent infrastructure.

**Why 3 copies live:** Enough to recover from a bad session (today's backup), compare against yesterday, and have one more safety net, all close at hand. Older copies aren't clutter to throw away — they're moved to `_archive/backups/` so the recovery trail is never broken.

**Why before updates, not after:** The backup should capture the state BEFORE this session's changes. If the current session corrupts the tracker, the most recent backup is the clean pre-session state.

**Recovery:** If the user says "restore my tracker" or "my tracker is broken," the first move is to **rerun the renderer** (`render_master_tracker.regenerate(workspace_root)`) — the tracker is generated, so a fresh render from canonical substrate beats any copy. Fall back to the most recent backup in `_hq/data/_backups/` only when the substrate itself is the problem; show the backup date and let the user confirm before replacing anything.

**Override:**
```
tracker_backup_count: 5    # default: 3
```

---

## Rule 12: Archive Search Index Generation (v1.7.0+)

**Problem:** After session notes roll over (Rule 1), older entries live in archive files that are never loaded during normal operations. When the user asks "what happened with Acme in January?" Claude has to guess which archive file to read.

**Action (during Rule 1 rollover, step 3 — after archiving entries):**
1. Check if `SESSION_NOTES_[NAME]_index.md` exists in the project folder. Create it if not.
2. For each entry being archived, append a row to the index:
   Format: `| [Date] | [Topics/Keywords — 5-8 words] | [Key People mentioned] | [Archive File] |`
3. The index is append-only. Never rewrite existing rows.
4. When the user asks a historical question, read the index first to identify which archive file and date range to look at. Only then read the specific archive entries.

**Index format:**
```markdown
# Session Notes Index — [Project Name]
| Date | Topics | People | Archive File |
|------|--------|--------|-------------|
| 2026-01-15 | Vendor selection, budget approval | Skyler, Skyler | SESSION_NOTES_Pat_archive_2026.md |
| 2026-01-22 | Timeline revised, scope reduced | Bowie | SESSION_NOTES_Pat_archive_2026.md |
```

**Cost:** ~1KB per year per project. Negligible.

**Override:**
```
archive_index_enabled: true    # default: true (set false to disable)
```

---

## Rule 13: Semantic Search Preparation (v1.7.0+)

**Problem:** Keyword indexes (#12 above) work for exact matches but miss conceptual queries like "when did we discuss changing our pricing strategy?" The archive might use different words ("revised pricing model," "rate card update") that a keyword search won't find.

**Two-tier approach: keyword index (Rule 12) is the foundation. Semantic search layers on top when available.**

**Action (during Rule 1 rollover, after index generation):**
1. Check if embedding generation is available (Python environment with sentence-transformers, or an embedding API key in CUSTOM_CONFIG.md).
2. **If available:** For each archived entry, generate an embedding vector from the entry's content. Store as a JSON sidecar: `SESSION_NOTES_[NAME]_embeddings.json` alongside the archive file. Each entry maps a session date to its embedding vector + a 1-line summary.
3. **If not available:** Skip silently. Rule 12's keyword index is the fallback. Log: "Semantic search unavailable — using keyword index only."
4. At query time: if embeddings exist, embed the user's question, cosine-similarity match against the index, read only the top-3 matching archive entries. If no embeddings, fall back to Rule 12's keyword index.

**Embedding sidecar format:**
```json
{
  "model": "all-MiniLM-L6-v2",
  "entries": [
    {
      "date": "2026-01-15",
      "summary": "Vendor selection and budget approval for Phase 2",
      "archive_file": "SESSION_NOTES_Pat_archive_2026.md",
      "embedding": [0.123, -0.456, ...]
    }
  ]
}
```

**When the CEO asks about archived content:**
1. Check for embeddings file → cosine search → read top matches
2. If no embeddings → check keyword index → read matched entries
3. If no index → brute-force scan archive files (last resort)

**Cost:** ~5KB per year per project for embeddings. ~1KB for keyword index. Both negligible.

**Dependencies:** Embedding generation requires Python or an API. Cowork may not support this natively — may need a scheduled Python script. Build Rule 12 first, add embeddings when infrastructure is available.

**Override:**
```
semantic_search_enabled: true    # default: true (set false to disable)
embedding_model: all-MiniLM-L6-v2   # default model
```

---

## Rule 14: Email Exclusion Filter (v1.7.1+)

**Problem:** CEOs exchange emails with attorneys, accountants, and other sensitive parties. These emails may contain privileged communications, sensitive financial documents, or credentials. The system must provide a configurable, per-user filter that prevents any Command Room skill from reading or storing excluded emails.

**Principle: Never read or store an excluded email. Skip it entirely. The exclusion lists are configured per user in CLAUDE.md and captured during onboarding.**

**This rule applies to EVERY skill that scans email — onboarding, morning-briefing, workspace-manager, meeting-notes, call-prep, people-crm, workspace-ingest, automation-scanner, and any future skill that reads Gmail.**

**The filter has three layers. An email that matches ANY layer is skipped entirely — never read, never summarized, never stored, never referenced.**

### Layer 1: Domain Exclusion

Before processing any email, check the sender's email address against the user's `## Excluded Domains` list in CLAUDE.md. If the sender's address contains any listed domain, skip the entire email.

**Purpose:** Block all emails from specific organizations — law firms, accounting firms, financial advisors, or any party the CEO designates.

**Configuration:** Captured during onboarding (Phase 3). The CEO provides domains. Stored in CLAUDE.md. The CEO can add or remove domains anytime by saying "add [domain] to my exclusion list" or "remove [domain] from my exclusion list."

### Layer 2: Document Type Exclusion

Before processing any email, check the subject, body, and attachment filenames against the user's `## Excluded Document Types` list in CLAUDE.md. If any match (case-insensitive), skip the entire email.

**Purpose:** Block emails containing sensitive financial documents — tax forms, financial statements, legal filings, or any document type the CEO designates.

**Matching:** Check for the document type term anywhere in the email subject, body text, attachment filenames, or link text. Case-insensitive. Partial matches count (e.g., "1099" matches "1099-MISC" and "Form 1099").

### Layer 3: Keyword Exclusion

Before processing any email, check the subject and body against the user's `## Excluded Keywords` list in CLAUDE.md. If any keyword appears (case-insensitive), skip the entire email.

**Purpose:** Block emails containing sensitive terms — passwords, credentials, or any keyword the CEO designates.

### Logging

When emails are skipped by any layer of this filter, log only the count — never the sender, subject, or content. Format: `[Email exclusion: skipped N emails]`. This appears in the scan summary so the CEO knows the filter is working.

### No Override

This filter cannot be disabled via CUSTOM_CONFIG.md. It is always on. The exclusion lists themselves are fully user-configurable (add/remove anytime), but the filtering behavior itself cannot be turned off.

**Filter precedence:** This filter runs BEFORE any content extraction. If an email matches any layer, processing stops immediately for that email.

### Example CLAUDE.md Configuration

```markdown
## Excluded Domains
| Domain | Reason |
|--------|--------|
| coxcastle.com | Legal |
| rsmus.com | Accounting |
| wittconsulting.com | Accounting |

## Excluded Document Types
| Document Type |
|---------------|
| K1 |
| 1099 |
| Tax Return |
| Personal Financial Statement |

## Excluded Keywords
| Keyword |
|---------|
| password |
```

---

## Override via CUSTOM_CONFIG.md

All thresholds can be overridden (override list at the bottom of this section). The two backfill rules below — symmetric brain (Rule 15) and session-notes (Rule 16) backfills — are documented here adjacent to the override config.

## Rule 15: PROJECT_BRAIN Backfill (v3.16+)

**Problem:** `PROJECT_BRAIN.md` is created only by the "new project" lifecycle (workspace-manager) and updated only on "end session." Any project folder that came into existence another way — filed into by workspace-ingest, hand-created, carried in from a legacy migration — has no brain. A project without a brain has no institutional-memory layer: "go [project]" loads nothing, gotchas and trigger aliases are lost. On the live workspace this affected 8 of 13 projects.

**Trigger:** Runs inside Deep Clean (step 2.5), driven by the integrity checker's `C11.missing_brain` findings. Can also run standalone when the user says "backfill brains" / "missing brains."

**Action:**
1. For each flagged project folder, read whatever context already exists in that folder — `PROJECT_CONTEXT.md`, `SESSION_NOTES*.md`, recent deliverables — plus the project's record + events in the substrate.
2. Scaffold `PROJECT_BRAIN.md` from the canonical template at the plugin-root `references/project-brain-template.md` (sections: People, Gotchas, Active Threads, Custom Workflows, Key Context, Trigger Aliases).
3. Populate from the context read in step 1; leave a section empty rather than inventing content. Follow the section-by-section rules in `references/workspace-detail.md` → "Brain Update Procedures" — identical to the "end session" brain-update path so backfilled brains are indistinguishable from organically-grown ones.
4. Never overwrite an existing brain. This rule only *creates* missing ones; ongoing maintenance of existing brains is Rule 2.

**Why scaffold-not-skip:** a missing brain silently degrades every future "go [project]." Backfilling once converts the project to the same memory model as every other, so the next "end session" maintains it normally.

---

## Rule 16: SESSION_NOTES Backfill (v3.19.x / SPEC CLEAN1 — D3)

**Problem:** the symmetric gap to Rule 15. `integrity_check` had `C11.missing_brain` + a brain backfill (Rule 15), but no missing-session-notes check and no backfill for the reverse direction. A project folder created outside the "new project" lifecycle (filed-into by ingest, hand-created, legacy migration) can have a brain or context but no `SESSION_NOTES` file — so the next "end session" has nowhere to roll its notes and the project has no running log.

**Trigger:** runs inside the weekly `cleanup` fire (Phase 3c), driven by the structural scan's `C11b.missing_session_notes` findings. Also runs on deep clean (step 2.5 alongside Rule 15) and standalone on "backfill session notes" / "missing notes".

**Action:**
1. For each flagged project folder, call `cleanup_actions.backfill_session_notes(workspace_root, folder_name)`.
2. The helper scaffolds `SESSION_NOTES_[NAME].md` from `references/session-notes-template.md`: a minimal header, the provenance line *"Backfilled by cleanup on [date] — no prior session notes existed,"* and empty `## Current Status` / `## Active Work Items` sections.
3. **Never overwrite.** The helper refuses (returns None, writes nothing) if ANY `SESSION_NOTES*.md` — live, archive, or index — already exists in the folder. This rule only *creates* missing files; ongoing maintenance of existing notes is Rule 1. On the 5 live client workspaces this is the guarantee that hand-written notes are never clobbered.

**Why scaffold-not-skip:** mirrors Rule 15 — a backfilled notes file converts the project to the same model as every other, so the next "end session" rolls into it normally.

---

```markdown
## Maintenance Overrides
session_notes_rollover_lines: 200      # default: 150
brain_thread_compress_days: 45         # default: 30
commitment_compress_days: 90           # default: 60
briefing_retention_days: 60            # default: 30
audit_retention_days: 180              # default: 90
prep_retention_days: 21                # default: 14
interaction_tier1_days: 120            # default: 90
interaction_tier2_months: 9            # default: 6
interaction_tier3_months: 18           # default: 12
tracker_quick_task_age: 90             # default: 60
tracker_archive_age: 120               # default: 90
tracker_backup_count: 5                # default: 3
archive_index_enabled: true            # default: true (set false to disable)
semantic_search_enabled: true          # default: true (set false to disable)
embedding_model: all-MiniLM-L6-v2      # default model
```

If CUSTOM_CONFIG.md doesn't exist or doesn't have a maintenance section, use defaults.
