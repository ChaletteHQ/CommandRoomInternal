---
name: file-documents
surfaces: both
description: "Copy deliverable documents — employee files, 1:1 notes, contracts, meeting transcripts, decks, PDFs — from a source folder into your workspace's project folders, so the workspace becomes your filing system. Fires on: 'scan my desktop', 'scan my files', 'organize my downloads', 'sort my downloads', 'sort these into projects', 'file these documents'. Preview-and-confirm mapping (document to project) before any copy; originals never deleted; unmatched files flagged for your call, never guessed. Does NOT fire on 'ingest my chatgpt export' / 'import context' (ingest-context — data extraction, not file filing), 'ingest this folder' (workspace-ingest — the combined pipeline), or 'export that doc' / 'save that doc to my folder' (workspace-manager — brings home a Claude Doc made anyway; nothing is drafted, so never memo-writer)."
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

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Copy deliverable documents (employee files, 1:1 notes, contracts, meeting transcripts, decks, PDFs, etc.) from a source folder into your workspace's project folders so the workspace becomes your filing cabinet over time. FILING intent only: this fires the workspace-ingest pipeline with full context extraction PLUS file migration. Always shows a preview-and-confirm before any file copy. Copy-only (never moves or deletes). Backs up source folder first, writes an undo log. Triggers: 'file documents from [path]', 'file these documents', 'sort my downloads', 'sort these into projects', 'organize my downloads', 'organize my [folder]', 'scan my desktop', 'scan my files', 'file my [folder] into projects'. Use this when you want both: the context layer updated (people/projects/decisions inferred from the source) AND the source files actually copied into the matching project folders. DOES NOT fire on 'ingest context from [path]' (that's ingest-context — same context extraction, no file copy).

## The Access preamble this file refers to

Propagated by `scripts/dev/propagate_access_preamble.py`; the canonical copy is in `shared/WORKSPACE_ACCESS.md`.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
```
