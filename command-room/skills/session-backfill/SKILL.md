---
name: session-backfill
surfaces: both
description: "One-time supervised catch-up that recovers the commitments, decisions, interactions, and deliverables buried in the CEO's chat history from before Command Room was capturing them — the last 60 days of sessions, swept once. Shows a preview of exactly what it found and waits for a yes before writing anything; records each item with the same dedup and identity rules as the nightly pass; snapshots history first and never deletes. Fires on: 'backfill my history', 'sweep the last 60 days', 'recover my past chats', 'catch up my chat history', 'go back and capture my old chats'. Does NOT fire on 'run session sweep' / 'sweep my chats' (session-sweep — the recurring nightly forward pass), 'process the last call' (meeting-notes), or 'reconcile my sent mail' (reconcile-sent)."
---

# Session Backfill — one-time historical chat recovery (supervised)

Command Room only started remembering the CEO's ad-hoc chats when session-sweep
began running. Everything before that — commitments made in passing, decisions
reasoned out loud, deliverables produced by hand — is still sitting in the
transcript history, uncaptured. This skill sweeps the last 60 days of that
history ONCE, so the record catches up to where the nightly pass now keeps it.

**Supervised, run-once (ship-don't-run).** This is not a scheduled task and does
not auto-run. An operator runs it by hand during onboarding / dogfood, watches
the preview, and confirms. It writes nothing until the CEO says yes, snapshots
the history before it touches anything, and never deletes — a re-run is safe
because every write is deduped, so a second pass recovers only what the first
missed.

## Skill Boundary (v2.1)

- **Use session-backfill for:** the one-time supervised sweep of the last 60 days of CHAT session transcripts, with a preview-and-confirm before any write.
- **Use `session-sweep` for:** the recurring silent nightly forward pass. This backfill is the historical catch-up that gets a workspace to the point session-sweep maintains.
- **Use `past-meetings` / `meeting-notes` for:** MEETING transcripts (Granola / Fireflies / …), not chat sessions.
- **Use `workspace-ingest` for:** pulling context out of an external source folder (files, exports). This backfill reads the session-transcript history, not a folder.

## Writer Contract

Every write goes through the locked, gated helper — never a hand-rolled append:

- The recovered `commitment` / `decision` / `interaction` / `note` items **and** the one `session_backfill_run` audit event — via `session_sweep.backfill_and_receipt` (one call, AFTER confirmation). It snapshots `events.jsonl` to `_archive/` first, dedups every item on its content hash through the existing `.source_refs.idx`, and appends through `append_event()` (the F1 gatekeeper) — the exact write path the nightly sweep uses.
- The dedup-checked plan the preview renders — via `session_sweep.preview_items` (reads only; writes nothing).

Before writing to any workspace file, this skill follows `shared/WORKSPACE_API.md`. It implements `shared/PASSIVE_CAPTURE.md`: summaries + entity references + a source reference only, never raw transcript text. Reads: the session-transcript MCP, `entities.json`, `events.jsonl`. History stays additive-only (§3.1) — the snapshot is an archived safety copy, not a rewrite path; nothing is ever deleted.

## The job (do exactly this)

**Before any python snippet below (Rule 22):** resolve the plugin root and run every snippet from it — the cwd never persists and `shared/scripts` only resolves from the plugin root:

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

1. **Guard against a needless repeat.** A prior backfill is safe to re-run (dedup makes it idempotent), but tell the operator first:

   ```python
   import sys; sys.path.insert(0, "shared/scripts")
   from session_sweep import preview_items, backfill_and_receipt, validate_backfill_ran, last_backfill
   prior = last_backfill("<abs workspace root>")   # newest session_backfill_run, or None
   ```
   If `prior` is not None, say so plainly ("I already ran the 60-day catch-up on <date>; re-running only picks up anything new") and continue only if the operator wants to.

2. **Read the last 60 days of sessions.** Resolve the session-transcript MCP at runtime by tool-name (the server id is per-install — never hard-code it, same as Granola / Calendar). List every session whose last activity is within 60 days and read each transcript. (No session-transcript MCP → say so and stop; nothing to write. Skip-not-fail per `shared/RELIABILITY.md`.)

3. **Extract only what never became an event.** For each transcript, identify the concrete commitments, decisions, interactions, and deliverables; cross-reference the existing `events.jsonl` and DROP anything already logged. Build one item per survivor — the SAME shape session-sweep uses, including session-sweep's full commitment capture block (v4.5.2 C1 parity; the shared write core enforces it): `data.kind` classified with the promise-vs-task rule ("send X to [person]" is a promise, not a task), `data.due` proposed from source language (relatives resolved against the SESSION's date, not today's) or explicit `data.no_due: true`, `data.counterparty_id` or free-text `data.counterparty_name` (never a guessed id), `data.owner_id` when confident. Deliverables use `type: note` with `data.recovered_kind: "deliverable"`; summaries only, never raw text; resolve `person_ids` / `primary_thread_id` when confident:

   ```python
   items = [
     {"session_id": "<id>", "type": "commitment", "summary": "<the promise>",
      "data": {"kind": "promise", "due": "2026-05-02", "owner_id": "<primary user id>",
               "counterparty_id": "person_042"}, "person_ids": ["person_042"]},
     {"session_id": "<id>", "type": "commitment", "summary": "<a self-owed item, no date in source>",
      "data": {"kind": "task", "no_due": True, "owner_id": "<primary user id>"}},
     {"session_id": "<id>", "type": "decision",    "summary": "<what was decided>"},
     {"session_id": "<id>", "type": "interaction", "summary": "<who / what>",
      "data": {"channel": "session"}},
     {"session_id": "<id>", "type": "note", "summary": "<a deliverable you produced>",
      "data": {"recovered_kind": "deliverable"}},
   ]
   ```

4. **Preview — show the plan, write NOTHING yet.** Dedup-check the items and render a compact summary in the workspace-ingest style:

   ```python
   plan = preview_items("<abs workspace root>", items)
   # plan: would_recover, skipped_dedup (already logged), by_type, sample_summaries
   ```

   Present it like this (plain text — no jargon, no ids):

   ```
   Here's what I found in your last 60 days of chats that was never logged:

     Commitments:   12
     Decisions:      8
     Interactions:  31
     Deliverables:   5
     ─────────────────
     To add:        56 items
     Already on file (skipping):  9

   Your history stays exactly as it is until you say go — and I snapshot it
   first, so nothing is ever lost. Add these 56?
   ```

   Allow three responses:
   - **"yes" / "go" / "proceed"** → Step 5.
   - **"show me" / "show the list"** → render the `sample_summaries` (and read more transcripts for detail if asked), then re-ask.
   - **"no" / "cancel" / "not now"** → stop. Write nothing. Say the history is untouched.

5. **On confirmation — snapshot + write + receipt (ONE call).**

   ```python
   receipt = backfill_and_receipt("<abs workspace root>", items,
                                  days=60, sessions_scanned=<n_sessions_read>)
   # snapshots events.jsonl to _archive/ first, then appends the survivors +
   # the session_backfill_run receipt. receipt: events_recovered, by_type, snapshot.
   ```

6. **Self-validate against the event (mandatory).**

   ```python
   v = validate_backfill_ran("<abs workspace root>")
   # v["ok"] must be True. v carries events_recovered / sessions_scanned / last_ts.
   ```
   If `v["ok"]` is False, do not report success — re-run Step 5 or report the failure plainly.

7. **Report what landed.** One plain summary of the counts added, and where the pre-backfill snapshot lives (so the operator knows the history was safely copied first). Then note that the nightly session-sweep keeps it current from here — no need to run this again.

## What it does NOT do
- It does not run on a schedule and does not auto-run — an operator runs it by hand, once, and confirms the preview (ship-don't-run).
- It never writes before the CEO confirms — the preview is read-only.
- It never rewrites or deletes history — it snapshots to `_archive/` and appends only (§3.1); a re-run dedups, it does not double-count.
- It does not capture MEETING transcripts (Granola) — that's `past-meetings` / `meeting-notes`.
- It does not store raw transcript text — summaries, entity references, and a source reference only.

## Reliability
Follows `shared/RELIABILITY.md`: skip-not-fail when the workspace or the session-transcript MCP isn't ready; connector budgets respected; no fabricated data when a source is down. Because it is supervised and gated on an explicit confirm, a partial read simply shows a smaller preview — it never writes a guess.

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

> One-time supervised catch-up that recovers the commitments, decisions, interactions, and deliverables buried in the CEO's chat history from before Command Room was capturing them — the last 60 days of sessions, swept once. It shows a preview of exactly what it found and waits for a yes before it writes anything, then records each item to the CEO's history with the same dedup and identity rules the nightly pass uses. Snapshots the history first and never deletes. Use when the CEO (or the operator running for them) says 'backfill my history', 'sweep the last 60 days', 'recover my past chats', 'catch up my chat history', 'go back and capture my old chats'. DOES NOT fire on 'run session sweep' / 'sweep my chats' — that's session-sweep, the recurring nightly forward pass; this is the one-time historical catch-up. DOES NOT fire on 'process the last call' / 'past meetings' (meeting-notes / past-meetings — MEETING transcripts) or 'reconcile my sent mail' (reconcile-sent — Gmail).
