---
name: file-documents
description: "Copy deliverable documents (employee files, 1:1 notes, contracts, meeting transcripts, decks, PDFs, etc.) from a source folder into your workspace's project folders so the workspace becomes your filing cabinet over time. FILING intent only: this fires the workspace-ingest pipeline with full context extraction PLUS file migration. Always shows a preview-and-confirm before any file copy. Copy-only (never moves or deletes). Backs up source folder first, writes an undo log. Triggers: 'file documents from [path]', 'file these documents', 'sort my downloads', 'sort these into projects', 'organize my downloads', 'organize my [folder]', 'scan my desktop', 'scan my files', 'file my [folder] into projects'. Use this when you want both: the context layer updated (people/projects/decisions inferred from the source) AND the source files actually copied into the matching project folders. DOES NOT fire on 'ingest context from [path]' (that's ingest-context — same context extraction, no file copy)."
---

# file-documents

Dispatches to `workspace-ingest` with the full pipeline active (both CONTEXT and FILING layers). Use when the user wants source files actually copied into matching project folders, not just the entity/event layer updated.

## Behavior

1. Resolve the source path from the user's invocation.
2. Invoke `workspace-ingest` Phases 1 through 9 in full — accept source, backup, route parser, parse completeness check, write JSON sources, file discovery, file classification, migration preview-and-confirm, execute migration, write INGEST_REPORT.
3. The preview-and-confirm in Phase 7 is the safety gate. User sees the full list of `<source file> → <destination project folder>` mappings + any items routed to the unrouted bucket; user explicitly says go before any copies happen.

## Why this skill exists separately from workspace-ingest

User clarity. `workspace-ingest` handles both context-only and full-filing intents; a user typing `sort my downloads into projects` shouldn't have to read the full workspace-ingest description (which covers both intents) to figure out what they'll get. This skill's description is focused on the filing intent — same surface, narrower intent.

The actual pipeline lives in `workspace-ingest/SKILL.md`. This skill is a thin alias that routes there with full filing enabled; it doesn't duplicate logic.

## What this skill does NOT do

- Does NOT skip the preview-and-confirm step. Every file copy is shown to the user before execution. There is no "auto-file without confirm" mode.
- Does NOT move or delete source files. Copy-only. Source folder is backed up before any work.
- Does NOT modify entities for which workspace-manager / people-crm are the canonical writer. Routes proposals to those skills per `shared/WORKSPACE_API.md`.
- Does NOT fire on a single-URL intake — that's `intel-intake`.
- Does NOT replace `command-room-onboarding` for first-install setup.
