---
name: session-sweep
surfaces: both
description: "Silent nightly memory pass that catches the commitments, decisions, interactions, and deliverables the CEO produced in ad-hoc chats that never went through a Command Room skill — so nothing said in passing is lost. Reads sessions active since the last sweep, extracts only what never became a logged item, and records each through the standard write path with dedup. Renders nothing. Runs nightly in the maintenance task; manual: 'run session sweep', 'sweep my chats', 'sweep my sessions'. Does NOT fire on 'process the last call' (meeting-notes — MEETING transcripts), 'reconcile my sent mail' (reconcile-sent — Gmail), or 'backfill my history' / 'sweep the last 60 days' (session-backfill — the one-time supervised catch-up)."
---

# Session Sweep — silent nightly transcript-to-history promotion

This task is all write-side work and no reader-facing output. Command Room only
remembers what a writing skill happened to capture; anything the CEO does in an
ad-hoc chat that never fires one is lost — a commitment made in passing, a
decision reasoned out loud, a deliverable produced by hand. Session transcripts
are readable from inside a scheduled task, so a nightly pass can promote that
episodic layer into the canonical history after the fact.

The writes ARE the job, so there is nothing to deprioritize them against — the
same single-responsibility reason the silent `cleanup` and `reconcile-sent`
tasks run reliably (Bug #98 doctrine: an invisible substrate write loses to a
visible deliverable when co-located, so this never folds into a brief).

## Skill Boundary (v2.1)

- **Use session-sweep for:** the silent nightly pass over CHAT session transcripts — recovering commitments / decisions / interactions / deliverables that never became logged items. It renders nothing.
- **Use `past-meetings` / `meeting-notes` for:** MEETING transcripts (Granola / Fireflies / Otter / …). Those run earlier; this pass runs after them and captures only what they left behind.
- **Use `reconcile-sent` for:** closing commitments the CEO completed by sending mail straight from Gmail.
- **Use `session-backfill` for:** the one-time supervised sweep of the last 60 days of history (preview-and-confirm). This skill is the recurring forward pass; that one is the historical catch-up.

## Writer Contract

Every write goes through the locked, gated helper — never a hand-rolled append:

- The recovered `commitment` / `decision` / `interaction` / `note` items **and** the one `session_sweep_run` audit event — via `session_sweep.sweep_and_receipt` (one call). It dedups through the existing `.source_refs.idx` sidecar and appends through `append_event()` (the F1 gatekeeper), so swept events get the same seq/ts stamping, schema-enum validation, and commitment identity (`cmt_<ulid>` + required `data.kind`) as any other writer. There is exactly one append path.
- **The narrative leg (SPEC SESSSTORY1, 2026-08-27) rides the SAME call.** Pass `sessions=[{session_id, thread_id, for_date, chapter_lines}, ...]` to `sweep_and_receipt` and it composes + appends each session's notes-file block through `session_narrative.compose_and_append` before landing the receipt — see Step 3.5 and Step 4 below. This is what turns "end session" from the only place the human-readable session-notes BLOCK gets written into a courtesy: facts were already end-session-proof (95.3% write live); this leg makes the narrative end-session-proof too.

Before writing to any workspace file, this skill follows `shared/WORKSPACE_API.md`. It implements `shared/PASSIVE_CAPTURE.md`: reading a session transcript on the CEO's behalf is the authorization to persist a summary of what it contains (never the raw transcript text — summaries + entity references + source reference only). Reads: the session-transcript MCP, `entities.json`, `events.jsonl`, that session's own `session_chapter` events (`session_chapter.chapter_lines_for`). No view renders; the narrative leg is the ONE exception that touches a file outside `_hq/data/` — and only the `.swept.md` QUARANTINE SIDECAR next to an ALREADY-EXISTING `SESSION_NOTES*.md` (SPEC SESSQUAR1: swept blocks land in `SESSION_NOTES[_NAME].swept.md`, never inline — the "end session" ritual is the confirming touch that promotes them). No notes file → no sidecar, no write (§0 Ruling 4).

## When this runs

| Mode | Trigger |
|------|---------|
| **Scheduled (primary)** | a job inside the `maintenance` background task (MAINT1) — due once per day, served at the day's FIRST fire (~6:45 AM): yesterday's chats, including evening ones after past-meetings, are swept before the 7:00 morning brief reads the substrate |
| **Manual** | "run session sweep", "sweep my sessions", "sweep my chats", "catch up my chat history" |

The extraction is mechanical (classify each transcript line as a real commitment / decision / interaction / deliverable, or not), so this task is fine to run on a fast, low-cost model.

## Enforcement is on the EVENT, not a sentence (Bug #98)

Success is a substrate artifact a validator reads back, never a narration:

- A real `session_sweep_run` audit event must land carrying `sessions_scanned` and `events_recovered`. It lands on EVERY run, including a clean no-op (zero recovered) — that receipt is the watchdog's proof the task fired.
- `validate_sweep_ran` reads it back and confirms it is THIS run's.
- If you cannot show that event, **the task FAILED** — say so plainly; do not narrate a success.

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

1. **Determine the window.** Read the last sweep's timestamp — it is the cursor for "sessions active since we last swept":

   ```python
   import sys; sys.path.insert(0, "shared/scripts")
   from session_sweep import last_sweep, validate_sweep_ran, sweep_and_receipt
   prior = last_sweep("<abs workspace root>")            # newest session_sweep_run, or None
   cursor_before = validate_sweep_ran("<abs workspace root>")["last_ts"]   # ISO or None
   ```
   Use the later of `cursor_before` and now-minus-24h as the floor (a 24h floor on a first run or a long gap; the dedup makes an over-wide window safe, so err wide).

2. **Resolve the session-transcript MCP at runtime and list active sessions.** The session-info server id is per-install (like the Granola and Calendar servers) — resolve it at runtime by tool-name, never hard-code it. List the sessions whose last activity is at/after the Step-1 floor. Read each session's transcript. (No session-transcript MCP available, or no sessions in the window → skip to Step 4 with an empty item list; the receipt still lands. Skip-not-fail per `shared/RELIABILITY.md`.)

3. **Extract only what never became an event.** For each transcript, identify the concrete commitments, decisions, interactions, and deliverables. Then cross-reference the recent `events.jsonl` window and DROP anything already logged (past-meetings, reconcile-sent, or a writing skill already captured it) — this pass recovers only the misses.

   **Commitments run the SAME capture block as every other commitment writer** (`scan-for-commitments` Step 3 is the reference text; meeting-notes and past-meetings comply — the sweep is not exempt). For every recovered commitment, per `shared/COMMITMENT_SCHEMA.md`:

   - **Kind (Stage D — REQUIRED):** counterparty determinable → `"promise"`; self-owed with no counterparty → `"task"`; scheduling intent → `"scheduling"`; genuinely ambiguous → `"promise"` + `data.pending_review: true`. **"Send X to [person]" has a counterparty — it is a promise, not a task.** A task is self-owed with NO counterparty by definition; the write core rejects a task that names one.
   - **Due (S2 due-nudge — REQUIRED):** propose `data.due` (`YYYY-MM-DD`) from the source language OR set `data.no_due: true` explicitly — silence is not an option; the write core rejects a commitment with neither. Resolve relative phrases against the session's own date: in a Jul 7 chat, "before tomorrow's call" → `2026-07-08`, "Thursday" → the next Thursday. An undated capture is invisible in every due-ranked surface on exactly the day it matters (the F-31 → F-44 chain).
   - **Counterparty (Stage E — REQUIRED when determinable):** set `data.counterparty_id` (canonical `person_NNN`, auto-joined into `person_ids` by the write core) for who the deliverable is owed TO / who owes it. When the counterparty is named but resolves to no person record, set free-text `data.counterparty_name` — never guess a person_id (unchanged rule), and never drop the name on the floor: without a counterparty receipt the item never enters chase or meeting matching.
   - **Owner:** set `data.owner_id` when confident — the primary user's id (resolve with `primary_user.resolve_primary_user`, never by scanning entities for `is_primary_user: true`; per SPEC USERKEY1/USERKEY2 the canonical pointer is `workspace.user_id` and the flag is only the seam's flag fallback, behind every pointer spelling it reads) for things the CEO owes; the named person's id for things owed to the CEO. Omit when not confident (capture always succeeds); the write core stamps `pending_review` on an ownerless promise.
   - **pending_review (safety inversion):** when attribution confidence is low — unresolved counterparty name, ambiguous owner — set `data.pending_review: true` yourself. The write core ALSO stamps it deterministically for those cases (absence of the flag is not consent; nothing auto-resolves a flagged item without a human confirmation), so a forgotten flag cannot slip a low-confidence capture past the human gate.
   - **Relevance routing (W4c — automatic, in the write core):** the sweep core classifies every recovered commitment through the shared relevance gate (`capture_gate.classify_capture`): items where the workspace owner is a party open as usual; third-party↔third-party and can't-confidently-attribute items are stored set-aside (`commitment_observed` — kept and searchable, but no open item, no count, no ask). Anything carrying a due date or a money amount always opens regardless. Extract normally — do NOT pre-filter third-party items yourself; the record is the point, the routing is the core's job. The receipt's per-type counts show how many landed set-aside.

   For each survivor build one item:

   ```python
   items = [
     # commitment — full capture block (kind + due/no_due + counterparty):
     {"session_id": "<id>", "type": "commitment",
      "summary": "Send the positioning briefs to Quinn before tomorrow's call",
      "data": {"kind": "promise", "due": "2026-07-08",
               "owner_id": "<primary user id>", "counterparty_id": "person_017"},
      "person_ids": ["person_017"]},
     # counterparty named but unresolved -> counterparty_name, no guessed id
     # (the write core stamps pending_review):
     {"session_id": "<id>", "type": "commitment",
      "summary": "Soft-sell the video-testimonial idea to Mira at tomorrow's call",
      "data": {"kind": "promise", "due": "2026-07-08",
               "owner_id": "<primary user id>", "counterparty_name": "Mira"}},
     # genuinely no date in the source -> say so explicitly:
     {"session_id": "<id>", "type": "commitment",
      "summary": "Move Rio to the new version as the first user",
      "data": {"kind": "task", "no_due": True, "owner_id": "<primary user id>"}},
     {"session_id": "<id>", "type": "decision",   "summary": "<what was decided>"},
     {"session_id": "<id>", "type": "interaction","summary": "<who / what>",
      "data": {"channel": "session"}, "person_ids": ["person_017"]},
     # a deliverable produced in the chat -> a note tagged recovered_kind:
     {"session_id": "<id>", "type": "note", "summary": "<what was produced>",
      "data": {"recovered_kind": "deliverable"}},
   ]
   ```
   Resolve `person_ids` / `primary_thread_id` against `entities.json` when confident; omit when not (capture always succeeds). Set top-level `classification_confidence` (0–1) on recovered commitments — the daily surfaces filter on it, and the write core stamps `pending_review` below threshold. Summaries only — never the raw transcript text.

3.5. **Build the narrative descriptor for each session (SPEC SESSSTORY1).** For every session in the window that did **not** run "end session" this run (check `session_narrative.already_composed("<abs workspace root>", session_id)` — a session the ritual already wrapped gets skipped here too, not just inside the write core), gather:

   ```python
   import session_chapter
   chapter_lines = session_chapter.chapter_lines_for("<abs workspace root>", session_id)
   # No chapters for this session? Advisory, not mandatory (§0 Ruling 1) — fall
   # back to your OWN short summary of the session's natural work boundaries,
   # read straight off the transcript you already have open for Step 3. Keep
   # each line to one sentence, same discipline as a chapter marker itself:
   # this is the ONE place in the whole pass a fresh transcript read is
   # allowed to feed the narrative, and it still never reaches a receipt —
   # only the composed .md block, exactly like a chapter-derived line would.
   sessions.append({
       "session_id": session_id,
       "thread_id": <primary_thread_id you resolved for this session's items, or None>,
       "for_date": <the session's own local date (tz.py), NOT today>,
       "chapter_lines": chapter_lines,  # or your transcript-derived fallback lines
   })
   ```

   Never invent thread_id or for_date — an unresolvable thread means the leg silently skips that session's file write (events still land; §0 Ruling 4), and for_date must be the day the session actually happened so a sweep that runs late doesn't misdate the entry.

4. **Write + receipt — ONE call.** It dedups every item on its content hash through `.source_refs.idx`, appends the survivors via `append_event()`, composes+appends each session's narrative block via `session_narrative.compose_and_append` (Step 3.5's `sessions` list), and lands the `session_sweep_run` receipt — `n_narratives_composed` counts how many blocks actually landed:

   ```python
   receipt = sweep_and_receipt("<abs workspace root>", items,
                               sessions_scanned=<n_sessions_read>, window_hours=24,
                               window_desc=<"since-cursor <ISO>" when a cursor scoped
                                            this run — the receipt records the REAL
                                            window, not the default label (F-08 P2c)>,
                               # fired_via: omit on the nightly fire or a catch-up (the
                               # seat decides); pass "manual" on a 'run session sweep'
                               # chat phrase or Run Now (v4.5.2 receipt contract)
                               sessions=sessions)  # Step 3.5's list; omit/empty is a
                                                    # clean no-op, same skip-not-fail
                                                    # posture as an empty items list
   # receipt["n_narratives_composed"] — zero-written, always present.
   ```

5. **Self-validate against the event (mandatory).**

   ```python
   v = validate_sweep_ran("<abs workspace root>", since_ts=cursor_before)
   # v["ok"] must be True. v carries events_recovered / sessions_scanned / last_ts.
   ```
   - `v["ok"] is True` → the sweep genuinely ran. Done.
   - `v["ok"] is False` → no fresh receipt landed. Do not report success; re-run Steps 2–4 or report the failure plainly.

6. **Surface only if something was recovered or composed (otherwise stay silent — this is a background task).**
   - `receipt["events_recovered"] > 0` → one plain line, delivered the way other scheduled tasks deliver (per the workspace's delivery preference):

     > *"I caught 3 things from your chats that weren't on your list yet — [titles]. They're logged now."*

   - `receipt["n_narratives_composed"] > 0` (and nothing recovered) → still silent per the same background-task posture; the composed session-notes blocks are a courtesy for the CEO to find on the next "go [project]", not a thing worth interrupting for. Never narrate WHAT a composed block says (§0 scope fence: counts-only outside the notes file itself).
   - Nothing recovered and nothing composed → no output. Silence is correct; the receipt is the proof it ran.

## Self-guard
An empty window (no active sessions, no session-transcript MCP, or a fresh workspace) is a clean silent no-op: `sweep_and_receipt` with an empty item list still lands a zero-recovered receipt, so the watchdog sees the task fired without any noise reaching the CEO. Same posture for the narrative leg — an empty/omitted `sessions` list still lands `n_narratives_composed: 0` on the very same receipt.

## What it does NOT do
- It does not render a brief or a commitments list — the brief and the list read what this pass wrote.
- It does not capture MEETING transcripts (Granola) — that's `past-meetings` / `meeting-notes`; this runs after them and takes only the leftovers.
- It does not store raw transcript text — summaries, entity references, and a source reference only (`shared/PASSIVE_CAPTURE.md`); the narrative leg's composed .md block carries only chapter one-liners and structural counts, and the marker event it lands is counts-only, never the block's own text.
- It never re-captures an item already logged — the extraction drops known items and the content-hash dedup makes a re-run over the same window a no-op. Same rule for a session's narrative: dedup'd against BOTH this leg's own prior run AND against "end session" already having composed it (`session_narrative.already_composed`).
- It never creates a `SESSION_NOTES*.md` file that doesn't already exist (§0 Ruling 4) — a project with no notes convention gets events only, silently, forever (not scaffolded — that is cleanup's `backfill_session_notes`, a different call this skill never makes).
- It is not the historical catch-up — the supervised last-60-days sweep is `session-backfill`, and it never runs the narrative leg (no retroactive rewriting of old notes files).

## Reliability
Runs as a scheduled task and follows `shared/RELIABILITY.md`: skip-not-fail when the workspace or the session-transcript MCP isn't ready, connector budgets respected, no fabricated data when a source is down (write an empty-window receipt and exit clean).

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

> Silent nightly memory pass that catches the commitments, decisions, interactions, and deliverables the CEO produced in ad-hoc chats that never went through a Command Room skill — so nothing said in passing is lost. It reads the transcripts of the sessions active since the last sweep, extracts only what never became a logged item, and records each one to the CEO's history with the same dedup and identity rules every other capture uses. It renders nothing: the morning brief and the commitments list read what this pass wrote. Runs daily as a job inside the maintenance background task (served at the first fire of the day, before the morning brief), and can be run by hand with 'run session sweep', 'sweep my sessions', 'sweep my chats', 'catch up my chat history'. DOES NOT fire on 'process the last call' / 'past meetings' — that's meeting-notes / past-meetings, which capture MEETING transcripts (Granola); this pass captures CHAT sessions and deliberately runs after them to catch only what they missed. DOES NOT fire on 'reconcile my sent mail' — that's reconcile-sent (Gmail). DOES NOT fire on 'backfill my history' / 'sweep the last 60 days' — that's the one-time historical backfill (session-backfill), a supervised preview-and-confirm run.
