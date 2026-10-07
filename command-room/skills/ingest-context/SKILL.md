---
name: ingest-context
surfaces: both
description: "Extract people, projects, decisions, and memories from a structured or loose source — a prior Command Room install, a ChatGPT export, custom markdown notes, or a folder of mixed files — and add them to this workspace's data layer with preview-and-confirm. Fires on: 'ingest my chatgpt export', 'import my old workspace', 'ingest these notes', 'bring in context from [source]', 'migrate from my previous setup'. Dedupes against existing entities, never overwrites confirmed data, snapshots before writing. Does NOT fire on 'ingest folder [path]' / 'scan my desktop' (workspace-ingest — the two-layer pipeline including file copying; this is the context-extraction intent), or 'sort these into projects' (file-documents)."
---

# ingest-context

Dispatches to `workspace-ingest` with the FILING layer disabled. Use when the user wants to pull context (people, projects, decisions, memories) out of a source WITHOUT copying any source files into their workspace project folders.

## Behavior

1. Resolve the source path from the user's invocation.
2. Invoke `workspace-ingest` Phases 1 through 4 (Accept source → Backup → Route parser → Parse Completeness Check → Write JSON sources). These are the CONTEXT-layer phases: they read the source, classify it, and append to `entities.json` + `events.jsonl`. ingest-context performs NO direct write — every event append is done by `workspace-ingest`, which routes through the locked writer `atomic_append_jsonl` (SPEC GATE1 / A1); see `shared/WORKSPACE_API.md` → Append Protocol §3.
3. **SKIP** Phases 5 through 8 (File discovery, classification, migration preview, file execution). Those are the FILING-layer phases — they copy actual files into workspace project folders, which this skill explicitly does NOT do.
4. Invoke Phase 9 (Write INGEST_REPORT) with `filing_layer: skipped`. The customer-readable report says it in plain English — "No files were copied — context only" — never the raw `filing_layer: skipped` token.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "Done — appended to entities.json + events.jsonl, filing_layer: skipped."
- Good: "Done — I've added the people, projects, and history to what I know. No files were copied — context only."

## Why this skill exists separately from workspace-ingest

User clarity. `workspace-ingest` handles both context and filing intents; a user typing `pull context from [path]` shouldn't have to read the full workspace-ingest description (which is ~700 chars and covers both intents) to figure out what they'll get. This skill's description is focused on context-only — same surface, narrower intent.

The actual pipeline lives in `workspace-ingest/SKILL.md` Phases 1-4 + Phase 9. This skill is a thin alias that routes to the right subset; it doesn't duplicate logic.

## What this skill does NOT do

- Does NOT copy any source files into workspace project folders. If you want filing too, use `workspace-ingest` (handles both) or `file-documents` (filing only).
- Does NOT modify entities for which workspace-manager / people-crm are the canonical writer. Routes proposals to those skills per `shared/WORKSPACE_API.md`.
- Does NOT fire on a single-URL intake — that's `intel-intake`.
- Does NOT replace `command-room-onboarding` for first-install setup.

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Extract people, projects, decisions, and memories from a structured or loose source — a prior Command Room install, ChatGPT export, custom markdown notes, or a folder of mixed files — and add them to your workspace's memory of people, projects, and history. CONTEXT intent only: this fires the workspace-ingest pipeline with FILING disabled, so no documents get copied into project folders; only the canonical entity + event layer is updated. Triggers: 'ingest context from [path]', 'pull context from [path]', 'extract context from [path]', 'ingest my chatgpt export', 'import chatgpt', 'pull in my chatgpt history', 'migrate from v1.x', 'bring in my old Command Room', 'load context from [path]'. Use this when you want context but you do NOT want the source files copied into your workspace (e.g., the source is on someone else's machine, or you just want the relationships + history without duplicating documents). DOES NOT fire on 'file documents from [path]' (that's file-documents) or 'intel intake' (single URL → intel-intake skill).

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
