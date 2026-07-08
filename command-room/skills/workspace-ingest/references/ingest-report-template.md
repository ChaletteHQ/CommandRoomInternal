# INGEST_REPORT.md template

Phase 9 writes `_hq/INGEST_REPORT.md` from this template (covers both data ingest and
file migration).

```markdown
# Command Room Ingest Report

**Date:** YYYY-MM-DD HH:MM:SS
**Source path:** [source path]
**Detected shape:** [v1.x | v2.x | custom-markdown | generic]
**Target plugin version:** [version]
**Backup location:** _archive/ingest_source_YYYY-MM-DD/
**Undo log:** _hq/_ingest-undo.jsonl

---

## Data counts

- **People parsed:** N (by section / group if applicable)
- **Orgs minted:** N total, N primary_focus, N holdings, N operating, N vendors, N other
- **Threads (projects) parsed:** N (by stage: active N, paused N, archived N)
- **Events emitted:** N total
  - decision: N
  - meeting: N
  - interaction: N
  - commitment: N
  - commitment_resolved: N
  - status_change: N
  - note: N
- **Aliases captured:** N (high-confidence N, inferred N)

## File migration counts

- **Total files discovered:** N
- **Copied (high confidence):** N
  - Session notes: N
  - Meeting transcripts: N
  - Deliverables (.docx/.xlsx/.pptx/.pdf): N
  - Intel / briefings: N
  - Loose docs → project `_misc/`: N
- **Queued for CEO review (`_hq/_ingest-queue/`):** N
  - Low-confidence project match: N
  - Unclassified: N
- **Skipped:** N
  - Too large (≥100 MB): N (list names)
  - Unknown / unparseable: N
- **Conflicts (destination existed, saved as `.conflict-[ts]`):** N (list)

## Per-project migration map

| Project | Session notes | Meetings | Deliverables | Misc | Total |
|---|---|---|---|---|---|
| NorthStar | 1 | 12 | 24 | 3 | 40 |
| ... | | | | | |

---

## Org Tree (as ingested; to be confirmed in onboarding Phase 2c)

[Rendered ASCII tree]

---

## Warnings

- **Unresolved aliases:** [list with source file + line references]
- **Missing person/org references:** [e.g., "MASTER_TRACKER row mentions 'Skyler Chen' but no PEOPLE.md entry"]
- **Format drift:** [e.g., "SESSION_NOTES for NorthStar uses non-standard dated-entry format; 3 entries skipped"]
- **Narrative-vs-structured contradictions:** [e.g., "MASTER_TRACKER shows project X as 'paused', but SESSION_NOTES shows active work last week"]
- **Compressed history blocks:** [noted as lossy where encountered]
- **Orphan events:** [events whose primary_thread_id didn't resolve to any thread record]

---

## Post-Ingest Verification

- [ ] Org tree confirmed by CEO (handled by onboarding Phase 2c)
- [ ] Run `cleanup` in first week to validate data integrity
- [ ] Spot-check views against source originals
- [ ] Review `classifier_feedback.jsonl` after one week of passive capture
- [ ] Walk `_hq/_ingest-queue/` and classify queued files ("review ingest queue")
- [ ] Spot-check 5 migrated files against source originals to verify content integrity
- [ ] Resolve any `.conflict-[ts]` files (pick winner, delete loser)

---

## Rollback

**Data only:** restore from `_archive/ingest_source_YYYY-MM-DD/` and re-run with a different shape detection or parser override.

**Full undo (data + files):** run `undo migration`. Reads `_hq/_ingest-undo.jsonl`, deletes every copied destination file (source is untouched), removes the data substrate files, preserves the undo log as an audit trail. See "Undo" section in the skill.
```
