---
name: scan-for-commitments
description: "One-shot bulk scan over historic Granola transcripts and Gmail threads to retroactively populate `type: commitment` events in `_hq/data/events.jsonl`. Use this when the workspace has meetings/emails on file but no commitment events (the an earlier version/14 commitment views show empty even though there's clearly work to track). Idempotent — re-running skips already-extracted commitments via (source_ref, title) dedup. Triggers: 'scan for commitments', 'backfill commitments', 'populate commitments', 'extract commitments from history', 'why don't I have any commitments', 'commitments are empty', 'commitments not showing up', 'where are my commitments', 'rebuild commitments from history', 'historical commitment scan', 'one-time commitment backfill'. DOES NOT fire on 'log a commitment' (workspace-manager owns explicit single logs) or 'show me my commitments' (just opens the commitments-tracker artifact)."
---

## Skill Boundary (v2.1; Slack leg v4.6.0)

- **Use scan-for-commitments for:** retroactive bulk extraction. The workspace has meeting events / interaction events from past Granola transcripts / Gmail threads, but no `type: commitment` events. This is the migration path that backfills them.
- **Also use it for the Slack source (v4.6.0 MC3):** when the Slack connector is present, every scan includes a recent-window pass over the channels and DMs the user participates in ("scan slack for commitments" runs the Slack leg alone). When Slack is not connected, this leg does not exist — no errors, no mention of Slack anywhere in the output.
- **Use `meeting-notes` for:** real-time per-meeting commitment extraction going forward.
- **Use `inbox-triage` for:** real-time per-email commitment extraction going forward.
- **Use `workspace-manager` for:** explicit one-off commitment logging ("log that I owe Aria X by Friday").

If the user's phrasing is ambiguous ("get my commitments going"), prefer this skill — it fixes the empty-views problem in one pass.

---

# Scan for Commitments

## Why this exists

The Workspace Map sidebar artifact + the daily Commitments scheduled chat both aggregate from `type: commitment` events in `events.jsonl`. If there are zero such events, those surfaces look empty — even when the user has dozens of historical meetings + active email threads where commitments are obvious. (Pre-v2.9.0 had a People Network + Commitments Tracker sidebar pair too; those were retired when the daily-chat surface absorbed their content. Workspace Map is the only surviving sidebar consumer.)

Earlier releases expected `meeting-notes` to write commitments but the contract was implicit (no schema doc, no explicit step). Many transcripts were processed without producing commitment events. `scan-for-commitments` is the one-shot fix.

This skill is also useful for: a fresh workspace ingest (workspace-ingest emits sparse `meeting` events without commitments), users migrating from prior versions, and periodic re-scans if the user adds Granola/Gmail history that pre-dates the install.

---

## Writer Contract

This skill is a primary appender to `_hq/data/events.jsonl`. It emits `type: commitment` events using the canonical schema documented in `shared/COMMITMENT_SCHEMA.md`. **`source_skill` MUST be `"scan-for-commitments"`** so the events are distinguishable from per-meeting writes (some users may want to roll back a bad scan; that requires being able to filter by source_skill).

**Append through the locked writer (SPEC GATE1 / A1).** Every commitment event MUST be written via `atomic_append_jsonl`, NOT a hand-rolled `next_seq`+`open('a')` or a raw `>>`. The helper reserves the seq and writes inside the cross-process writer lock (`_hq/data/.writer.lock`) so a concurrent append can't lose an event or duplicate a seq. Batch the whole scan into ONE call — `atomic_append_jsonl(events_path, [e1, e2, ...], holder="scan-for-commitments")` (the cost is the read+rewrite, so once-per-batch beats once-per-event). Omit `seq`/`ts` — auto-stamped. See `shared/WORKSPACE_API.md` → Append Protocol §3.

This skill DOES NOT modify or remove existing events. Append-only is non-negotiable per the events schema. If a previous scan produced bad commitments, the user can either ignore them (they'll age out over time) or run `commitment_resolved` events to close them — but this skill never edits the past.

---

## How to Use

```
"Scan for commitments"
"Backfill commitments"
"Populate commitments from history"
"Why don't I have any commitments?"
"Commitments are empty — fix it"
"Rebuild commitments from history"
"Extract commitments from past meetings"
"One-time commitment backfill"
```

Optional modifiers:
- Window: `"last 30 days"` (default), `"last 90 days"`, `"all time"` — the **Slack leg keeps its own 7-day default** regardless (chat is far noisier than mail; a stated window like "last 30 days" widens Slack too, but "all time" caps Slack at 30 days — chat history beyond that is archaeology, not open promises)
- Source: `"meetings only"` (skip Gmail + Slack), `"email only"` (skip Granola + Slack), `"slack only"` / `"scan slack for commitments"` (just the Slack leg), `"all"` (default)
- Mode: `"dry-run"` (count and preview, don't write) vs `"commit"` (default)

---

## How It Works

### Step 1 — Audit the current state

Read `_hq/data/events.jsonl` and count:
- `meeting` events
- `interaction` events with `data.channel == "email"`
- `commitment` events (existing — may be zero)
- `commitment_resolved` / `thread_resolved` events

Surface the audit to the user before scanning:

```
Here's what I'm seeing:
- 11 meetings on record
- 47 inbound email threads
- No commitments tracked yet — that's what I'll fix
- 4 commitments already closed (I'll leave those alone)

I'll look across the 11 meetings and 47 email threads. Takes about 3-5 minutes.
Want me to show you the list first before I add anything? (yes / no)
```

If the Slack connector is present (Step 2's discovery gate), the audit adds one line: "I'll also look through your Slack channels and DMs from the last 7 days." If it's absent, Slack is never mentioned — anywhere in this skill's output.

If the user answers **yes** (or says "dry-run"), run the Step 5 dry-run preview first. If they answer **no** — they don't want to see the list first — proceed straight to commit mode. If they don't answer, default to the dry-run preview (the safe path).

### Step 2 — Pull the source artifacts

For each `meeting` event (within the time window):
- Read `data.source_ref` (e.g., `granola:abc123`) to get the Granola meeting id
- Resolve the transcript tool via `discover_transcript_tool()` from `shared/scripts/tool_discovery.py` (per CONTRACT.md Rules 9 + 21 — native parity across Granola / Fireflies). Call its `result.tool_id`'s `get_meeting_transcript` method. Do NOT hardcode a per-installation MCP UUID.

For each inbound `interaction` event with `channel: "email"` (within the time window):
- Read `data.source_ref` (e.g., `gmail:msg_xyz`) to get the message id
- Resolve the mail-thread-fetch tool via `discover_mail_thread_fetch_tool()` from `shared/scripts/tool_discovery.py` (Gmail / Outlook native parity). Call its `result.tool_id`'s thread/conversation fetch method. Do NOT hardcode a per-installation MCP UUID.

If a source artifact is unavailable (transcript deleted, email purged), log to `_hq/CONFLICTS.md` and skip that event. Do not abort the scan — partial backfill is better than no backfill.

**Slack source (v4.6.0 MC3).** Unlike the meeting/email legs, Slack does not start from existing events — it scans the connector directly over a recent window:

1. **Discovery gate:** resolve tools via `discover_slack_tool()` from `shared/scripts/tool_discovery.py` (operations: `search_channels`, `read_channel`, `read_thread`, `search_users`, `read_user_profile`). If `tool_id` is None, **the Slack leg does not exist for this scan** — skip it with zero errors and zero mentions (don't say "Slack not connected"; a workspace without Slack should never hear the word). If discovery succeeds but a call fails mid-scan (auth expired, rate limit), that's connector-down: finish the other sources and add ONE honest line to the summary ("I couldn't reach Slack just now — that part of the scan is incomplete"). Never fabricate, never abort the whole scan.
2. **Identity first:** resolve the user's own Slack member id(s) once per scan — search users for the primary-user entity's name/email (`entities.json`, `resolve_primary_user`) and confirm via profile. If the user's Slack identity can't be resolved, the direction split below is impossible — skip the Slack leg with the one honest line rather than guessing.
3. **Scope + cost bounds:** channels and DMs the user participates in, last **7 days** by default, hard message cap via `slack_capture.cap_messages` (default 400, newest kept). If the cap trims anything, the post-scan summary says how much was set aside — a silently-capped scan reads as full coverage.
4. **Hygiene:** every raw message goes through `slack_capture.normalize_message` — bot posts, join/leave/topic noise, deleted messages, and empty text never reach extraction; edited messages are read at their LATEST text. Thread replies count as messages (fetch via `read_thread` when a parent in the window has replies).
5. **Direction split (`slack_capture.classify_direction`):** the user's OWN sent messages are the **primary promise source** (what I promised); messages **naming the user** (mention or name) are the **owed-to-you source**. Everything else is third-party↔third-party chatter — out of scope for this leg (the builder refuses it; W4c's relevance gate + observed tier will store those as meeting/channel context when it lands — this leg is already shaped for that gate to drop in at capture).

### Step 3 — Extract commitments per source

Apply the trigger logic from `shared/COMMITMENT_SCHEMA.md` § "Extraction triggers" — the capture floor (Stage D 2026-07):
1. **Clear owner** — an identifiable named person
2. **Clear deliverable** — a specific artifact / decision (not vague)
3. **Real consequence** — someone is waiting, a date depends on it, or dropping it costs something

Below-floor items are skipped (they bury real promises — the rule that cut one live open set 71→33). If `_hq/config/commitment-rules.md` exists, read it BEFORE writing and skip any item matching a user-taught `never-track` pattern.

For each qualifying match in a transcript or email body, prepare a `type: commitment` event in canonical shape. **For Slack messages, build the event through `slack_capture.build_slack_commitment_event()` (`shared/scripts/slack_capture.py`) — it enforces this entire block in code** (Stage-D kind, S2 due-nudge, Stage-E counterparty, pending_review inversion, `source_ref: slack:<permalink>`, ts backdated to the message time) and fails loud on anything the extraction skipped; resolve relative due phrases ("by Friday") against the MESSAGE's date, not the scan date. **Classify `data.kind` at capture (Stage D — REQUIRED; the gate rejects a kind-less commitment on the strict path):** counterparty determinable → `"promise"`; self-owed with no counterparty → `"task"`; scheduling intent → `"scheduling"`; genuinely ambiguous → `"promise"` + `data.pending_review: true`. **Counterparty receipts (Stage E, F5 — REQUIRED when determinable):** populate `data.counterparty_id` (and include it in `person_ids`) with who the deliverable is owed TO / who owes it; when the counterparty is named but has no person record, SHOULD set free-text `data.counterparty_name`. These feed the CRU candidacy gate directly (the Bug #103 recall fix); `requester_id` is retired for NEW writes (readers keep the alias chain forever). **Due-date nudge (S2):** propose a `due` from the source language OR set explicit `data.no_due: true` — undated items go to the weekly triage, not the aging view. Resolve owner names via `aliases.json` to canonical `person_id`. If owner can't be resolved, prepare the event with `owner_id: ""` and surface the unresolved name in the post-scan summary.

### Step 4 — Dedup before writing

Match on `(source_ref, title)`. If a `type: commitment` event already exists in events.jsonl with the same `data.source_ref` AND the same `data.title` (case-insensitive substring match — first 60 chars), skip it. This is the only safe way to make the scan idempotent across re-runs. The Slack leg uses the same key, codified — `slack_capture.already_captured(workspace_root, permalink, title)` — with the permalink as the per-message `source_ref` anchor. Cross-SOURCE duplicates (the same real promise made in Slack and again in an email) are NOT this step's job: the capture-time semantic dedup layer (`commitment_dedup`, v4.6.0 C4) fires inside the append itself and flags suspects for the confirm flow.

Also skip commitments where the source is already covered by a `commitment_resolved` or `thread_resolved` event for the same `source_ref` — there's no point creating a commitment that's already known to be done.

### Step 5 — Dry-run vs commit

**Dry-run mode** — print to stdout (and save a preview to `_hq/scans/COMMITMENT_SCAN_PREVIEW_[YYYY-MM-DD_HH-MM].md`):

```
Here's what I'd add — 14 commitments:

From the Acme Q1 sync meeting:
- They owe you: Mira — send updated pricing deck (due 2026-05-02)
- They owe you: Mira — confirm Q2 launch window (no due date)
- You owe them: review the deck and send feedback (due 2026-05-05)

From the "RE: Acme MSA" email thread:
- They owe you: Aria — return redlined MSA (due 2026-04-30) — already overdue
...

Say "add them" and I'll put these in your tracker. Otherwise nothing changes.
```

(Accept "add them", "add these", or "commit" as the confirmation — but only ever advertise "add them"; never ask the CEO to speak git.)

**Commit mode** — write the whole batch through the Writer Contract's single locked append: `atomic_append_jsonl(events_path, [e1, e2, ...], holder="scan-for-commitments")`, omitting `seq`/`ts` (auto-stamped inside the lock). One call for the whole scan — never per-event appends, never a hand-rolled writer. After the append completes, regenerate `_hq/views/COMMITMENTS.md` (if the views generator exists) and surface a summary:

```
Done — added 14 commitments to your tracker.

- 9 they owe you (5 from Mira, 2 from Aria, 1 from Bowie, 1 from Quinn)
- 5 you owe (3 to Sam, 1 to Mira, 1 to Dustin)
- 2 already overdue (flagged in red in your commitments list)

3 were too vague to call either way — I set those aside for you to look at.
1 was already closed — I left it alone.

Open your Workspace Map or your daily commitments chat to see them.
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "Want me to show you what I'd add first before I commit anything? (Y/N)"
- Good: "Want me to show you the list first before I add anything? (yes / no)"

---

## Step 6 — Recovery & rollback

If the user reviews the result and decides the scan produced bad data ("too many false positives", "wrong owner attribution"): **close the bad commitments with resolved events.** Walk events.jsonl back, identify the bad commitments by `source_skill: "scan-for-commitments"` and the timestamp window, and emit a `commitment_resolved` event for each with `data.evidence: "scan-for-commitments rollback YYYY-MM-DD"`.

**Recommended:** dry-run first (Step 5). The cost of running the scan twice is small; the cost of polluting events.jsonl with bad commitments is meaningful.

---

## Triggers

```
"scan for commitments"
"backfill commitments"
"populate commitments"
"extract commitments from history"
"why don't I have any commitments"
"commitments are empty"
"commitments not showing up"
"where are my commitments"
"rebuild commitments from history"
"historical commitment scan"
"one-time commitment backfill"
"scan slack for commitments"
"extract commitments from slack"
```

Does NOT fire on:
- "log a commitment" → workspace-manager
- "show me my commitments" → just navigate / open commitments-tracker
- "follow up" → follow-up-ritual
- "process meeting" → meeting-notes (which now writes commitments per-meeting going forward)

---

## Gotchas

- **Don't run on a stale Granola token.** If the connector returns transcript-not-found for half the meetings, the scan will look like it succeeded on what it could read but silently skip the rest. The audit (Step 1) should flag if many source_refs are unreachable.
- **Owner resolution depends on `aliases.json`.** If a meeting attendee uses a nickname that's not in aliases yet (e.g., transcript says "Mir" but entities.json has "Mira Sample"), the commitment will land with `owner_id: ""` and need manual fixup. Surface these as a list at the end so the user can add aliases and re-run (re-running is idempotent — already-extracted commitments won't be re-written).
- **Don't infer commitments from meeting summaries alone.** The `meeting` event's `data.notes` field is often a 1-line summary, not the full transcript. Pull the actual transcript via Granola MCP — that's where commitment language lives. Skipping the MCP pull is what caused the v2.7.13/14 implicit-extraction failure to begin with.
- **Don't over-extract from "we should" / "we could".** These are discussion artifacts, not commitments. The schema doc lists qualifying vs disqualifying patterns — read it once per scan run.
- **Slack: the permalink is the dedup anchor — fetch it, never synthesize it.** Use the permalink the connector returns for the message. A hand-built channel+ts URL that differs from the connector's spelling breaks idempotency (the same message re-captures on every scan).
- **Slack: casual chat reads as commitment language.** "I'll take a look" / "on it" in Slack usually isn't a deliverable. The Stage-D capture floor (owner + deliverable + consequence) applies with full force — when in doubt in Slack, the answer is skip. Emoji-only replies, reactions, and acknowledgments are never commitments.
- **Slack: direction is not attribution.** The direction split decides which messages to READ; the owner still comes from the language. "Can you send me the deck by Friday?" in the user's OWN message is owed-to-you (the counterparty owes it), not a user promise.

---

## What It Doesn't Do

- Does not extract third-party↔third-party Slack items — only what the user promised (own messages) or is owed (messages naming them) becomes an open commitment. Observed-tier storage for the rest arrives with W4c's relevance gate, which applies to this leg at capture.
- Does not send, post, or react in Slack — read-only, ever.
- Does not add a Slack schedule — the Slack leg runs inside this skill's explicit trigger only; the existing daily inbox/commitments cadence covers surfacing (a dedicated Slack schedule is a future product decision, not this skill's call).
- Does not extract from raw text the user pastes — that's `meeting-notes` (paste-fallback path).
- Does not auto-resolve commitments — resolution events are written by `follow-up-ritual` (when a meeting closes one) or by the user explicitly clicking ✓ done in the daily Commitments chat widget (dispatched through apply-choices).
- Does not run on a schedule — explicit-trigger only. The whole point is "one-shot bulk fix."
- Does not modify or remove existing events. Append-only.
- Does not invent commitments where the source language is ambiguous. If in doubt, skip and surface as "ambiguous candidate" in the conflict log.

---

## Connected Tools

- Transcript fetch — resolved via `discover_transcript_tool()` (`shared/scripts/tool_discovery.py`). Native Granola or Fireflies; never hardcoded per-installation UUIDs (Rule 21 native parity).
- Mail thread fetch — resolved via `discover_mail_thread_fetch_tool()`. Native Gmail (`get_thread`) or Outlook (`get_conversation`).
- Slack reads — resolved via `discover_slack_tool()` (read-only: channel history, threads, channel/user search, profiles). Absent = the Slack leg doesn't exist; present-but-failing = one honest line, scan continues (skip-not-fail).
- `_hq/data/events.jsonl` — append-only writer
- `_hq/data/aliases.json` — read-only (owner resolution)
- `_hq/data/entities.json` — read-only (user_id resolution)
