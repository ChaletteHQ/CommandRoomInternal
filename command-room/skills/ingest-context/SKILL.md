---
name: ingest-context
description: "Extract people, projects, decisions, and memories from a structured or loose source — a prior Command Room install, ChatGPT export, custom markdown notes, or a folder of mixed files — and add them to your existing entities.json + events.jsonl substrate. CONTEXT intent only: this fires the workspace-ingest pipeline with FILING disabled, so no documents get copied into project folders; only the canonical entity + event layer is updated. Triggers: 'ingest context from [path]', 'pull context from [path]', 'extract context from [path]', 'ingest my chatgpt export', 'import chatgpt', 'pull in my chatgpt history', 'migrate from v1.x', 'bring in my old Command Room', 'load context from [path]'. Use this when you want context but you do NOT want the source files copied into your workspace (e.g., the source is on someone else's machine, or you just want the relationships + history without duplicating documents). DOES NOT fire on 'file documents from [path]' (that's file-documents) or 'intel intake' (single URL → intel-intake skill)."
---

# ingest-context

Dispatches to `workspace-ingest` with the FILING layer disabled. Use when the user wants to pull context (people, projects, decisions, memories) out of a source WITHOUT copying any source files into their workspace project folders.

## Behavior

1. Resolve the source path from the user's invocation.
2. Invoke `workspace-ingest` Phases 1 through 4 (Accept source → Backup → Route parser → Parse Completeness Check → Write JSON sources). These are the CONTEXT-layer phases: they read the source, classify it, and append to `entities.json` + `events.jsonl`.
3. **SKIP** Phases 5 through 8 (File discovery, classification, migration preview, file execution). Those are the FILING-layer phases — they copy actual files into workspace project folders, which this skill explicitly does NOT do.
4. Invoke Phase 9 (Write INGEST_REPORT) with `filing_layer: skipped` so the report makes it clear no files were copied.

## Why this skill exists separately from workspace-ingest

User clarity. `workspace-ingest` handles both context and filing intents; a user typing `pull context from [path]` shouldn't have to read the full workspace-ingest description (which is ~700 chars and covers both intents) to figure out what they'll get. This skill's description is focused on context-only — same surface, narrower intent.

The actual pipeline lives in `workspace-ingest/SKILL.md` Phases 1-4 + Phase 9. This skill is a thin alias that routes to the right subset; it doesn't duplicate logic.

## What this skill does NOT do

- Does NOT copy any source files into workspace project folders. If you want filing too, use `workspace-ingest` (handles both) or `file-documents` (filing only).
- Does NOT modify entities for which workspace-manager / people-crm are the canonical writer. Routes proposals to those skills per `shared/WORKSPACE_API.md`.
- Does NOT fire on a single-URL intake — that's `intel-intake`.
- Does NOT replace `command-room-onboarding` for first-install setup.
