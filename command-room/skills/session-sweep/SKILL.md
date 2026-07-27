---
name: session-sweep
description: "Silent nightly memory pass that catches the commitments, decisions, interactions, and deliverables the CEO produced in ad-hoc chats that never went through a Command Room skill — so nothing said in passing is lost. Reads sessions active since the last sweep, extracts only what never became a logged item, and records each through the standard write path with dedup. Renders nothing. Runs nightly in the maintenance task; manual: 'run session sweep', 'sweep my chats', 'sweep my sessions'. Does NOT fire on 'process the last call' (meeting-notes — MEETING transcripts), 'reconcile my sent mail' (reconcile-sent — Gmail), or 'backfill my history' / 'sweep the last 60 days' (session-backfill — the one-time supervised catch-up). Extraction rules and dedup contract: Routing section in the body."
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

Before writing to any workspace file, this skill follows `shared/WORKSPACE_API.md`. It implements `shared/PASSIVE_CAPTURE.md`: reading a session transcript on the CEO's behalf is the authorization to persist a summary of what it contains (never the raw transcript text — summaries + entity references + source reference only). Reads: the session-transcript MCP, `entities.json`, `events.jsonl`. No view renders; no other files touched.

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
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
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
   - **Owner:** set `data.owner_id` when confident — the primary user's id (`is_primary_user: true` in entities) for things the CEO owes; the named person's id for things owed to the CEO. Omit when not confident (capture always succeeds); the write core stamps `pending_review` on an ownerless promise.
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

4. **Write + receipt — ONE call.** It dedups every item on its content hash through `.source_refs.idx`, appends the survivors via `append_event()`, and lands the `session_sweep_run` receipt:

   ```python
   receipt = sweep_and_receipt("<abs workspace root>", items,
                               sessions_scanned=<n_sessions_read>, window_hours=24,
                               window_desc=<"since-cursor <ISO>" when a cursor scoped
                                            this run — the receipt records the REAL
                                            window, not the default label (F-08 P2c)>,
                               fired_via=<"scheduled" on the nightly fire;
                                          "manual" on a 'run session sweep' chat
                                          phrase or Run Now — v4.5.2 receipt contract>)
   ```

5. **Self-validate against the event (mandatory).**

   ```python
   v = validate_sweep_ran("<abs workspace root>", since_ts=cursor_before)
   # v["ok"] must be True. v carries events_recovered / sessions_scanned / last_ts.
   ```
   - `v["ok"] is True` → the sweep genuinely ran. Done.
   - `v["ok"] is False` → no fresh receipt landed. Do not report success; re-run Steps 2–4 or report the failure plainly.

6. **Surface only if something was recovered (otherwise stay silent — this is a background task).**
   - `receipt["events_recovered"] > 0` → one plain line, delivered the way other scheduled tasks deliver (per the workspace's delivery preference):

     > *"I caught 3 things from your chats that weren't on your list yet — [titles]. They're logged now."*

   - Nothing recovered → no output. Silence is correct; the receipt is the proof it ran.

## Self-guard
An empty window (no active sessions, no session-transcript MCP, or a fresh workspace) is a clean silent no-op: `sweep_and_receipt` with an empty item list still lands a zero-recovered receipt, so the watchdog sees the task fired without any noise reaching the CEO.

## What it does NOT do
- It does not render a brief or a commitments list — the brief and the list read what this pass wrote.
- It does not capture MEETING transcripts (Granola) — that's `past-meetings` / `meeting-notes`; this runs after them and takes only the leftovers.
- It does not store raw transcript text — summaries, entity references, and a source reference only (`shared/PASSIVE_CAPTURE.md`).
- It never re-captures an item already logged — the extraction drops known items and the content-hash dedup makes a re-run over the same window a no-op.
- It is not the historical catch-up — the supervised last-60-days sweep is `session-backfill`.

## Reliability
Runs as a scheduled task and follows `shared/RELIABILITY.md`: skip-not-fail when the workspace or the session-transcript MCP isn't ready, connector budgets respected, no fabricated data when a source is down (write an empty-window receipt and exit clean).

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Silent nightly memory pass that catches the commitments, decisions, interactions, and deliverables the CEO produced in ad-hoc chats that never went through a Command Room skill — so nothing said in passing is lost. It reads the transcripts of the sessions active since the last sweep, extracts only what never became a logged item, and records each one to the CEO's history with the same dedup and identity rules every other capture uses. It renders nothing: the morning brief and the commitments list read what this pass wrote. Runs daily as a job inside the maintenance background task (served at the first fire of the day, before the morning brief), and can be run by hand with 'run session sweep', 'sweep my sessions', 'sweep my chats', 'catch up my chat history'. DOES NOT fire on 'process the last call' / 'past meetings' — that's meeting-notes / past-meetings, which capture MEETING transcripts (Granola); this pass captures CHAT sessions and deliberately runs after them to catch only what they missed. DOES NOT fire on 'reconcile my sent mail' — that's reconcile-sent (Gmail). DOES NOT fire on 'backfill my history' / 'sweep the last 60 days' — that's the one-time historical backfill (session-backfill), a supervised preview-and-confirm run.
