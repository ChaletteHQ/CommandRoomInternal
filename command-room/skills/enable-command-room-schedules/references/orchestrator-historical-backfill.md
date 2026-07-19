# Orchestrator prompt — Historical Backfill (one-shot chunk)

> LATE-FIRE EXEMPT (one-shot backfill chunk): this is not a recurring reader-facing chat, so the shared lateness tiers do not apply — run in full whenever fired. Documented v4.5.1-era hygiene so the omission reads as a contract, not a gap.

This file is the EXACT prompt registered with `create_scheduled_task` for `taskId: cr-historical-backfill-N` (one-shot, fires once at the scheduled time then expires). One-time per chunk. Multiple chunks are scheduled at install time by `enable-command-room-schedules` Phase 4 to walk back the user's last 12 months of metadata.

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`.
**Atomic-write requirement (v2.10.5+):** ALL appends to `events.jsonl` MUST use `shared/scripts/atomic_write.py atomic_append_jsonl`. Backfill chunks emit hundreds of events per run — batch them via `atomic_append_jsonl(path, [event1, event2, ..., eventN])` (one helper call per batch, NOT per event) for efficiency + Drive-sync safety. Same rule for the `.backfill_cursor` resume marker — write via `atomic_write_text`. See `shared/WORKSPACE_API.md` § "Append Protocol — events.jsonl" for the full pattern.

---

You are firing a Command Room **Historical Backfill** chunk. Single purpose: walk one window of past time across every connector, capture metadata only, write events.jsonl. **Never read message bodies, transcripts, or file content during a backfill chunk.** Metadata = sender / recipient / subject / date / attendees / titles / paths / mtimes. Bodies are out of scope here — they get fetched lazily when the user opens a specific project.

The window for THIS chunk is passed in the prompt template (specific dates substituted at registration time):

> Chunk: N of M
> Window start: YYYY-MM-DD
> Window end: YYYY-MM-DD
> Tier: light | medium | heavy

# Phase 1 — Idempotency gate

Read `_hq/data/.backfill_cursor` (single-line file with `last_completed_chunk: N`). If this chunk's N ≤ `last_completed_chunk`, skip silently — already done.

If `.backfill_cursor` doesn't exist, treat as fresh — proceed.

If a previous chunk's pack_run event in events.jsonl shows `status: in_progress` for ≥ 2 hours (stale lock), proceed anyway and overwrite the lock.

# Phase 2 — Setup

- Read entities.json + aliases.json (these get extended by this chunk, not replaced).
- Read M's primary email + first name.
- Discover available connector MCPs:
  - Gmail OR Outlook (whichever is connected; both possible — handle both)
  - Google Calendar OR Outlook Calendar
  - Drive OR OneDrive (and SharePoint if Microsoft 365)
  - Granola
  - Slack OR Teams
- For each available connector, identify its search/list API.

# Phase 3 — Per-connector metadata sweep (window-bounded)

**Hard rule per connector:** capture metadata ONLY. Never call `read_file_content`, never fetch full message bodies, never download attachments, never pull full transcripts.

## Mail (Gmail or Outlook)

Query: messages where `received >= window_start AND received <= window_end`.

For each message capture: `{thread_id, message_id, sender_email, sender_name (if in From: signature), recipients, subject, date, snippet (≤200 chars from API response — already there, no extra fetch needed), labels, has_attachments_bool}`.

Batch with pageSize 100. Append to events.jsonl in append-only fashion, one event per thread (NOT per message — group messages by thread to keep event count tractable):

```jsonl
{"type":"interaction","ts":"<ISO>","seq":<seq>,"data":{"channel":"email","thread_id":"<id>","subject":"<subject>","participants":["<email>", "..."],"first_message_date":"<date>","last_message_date":"<date>","message_count":<N>,"has_attachments":<bool>,"snippet":"<≤200 char snippet>","source_ref":"<thread_url>","inferred_from":["historical_backfill"]}}
```

Resolve `participants` against entities.json. For unrecognized email addresses, append a provisional person record with `inferred_from: ["historical_backfill"]`, `pending_review: true`, `first_seen: <window_end>`. Don't try to enrich now — Pulse's weekly synthesis pass picks up provisional records.

## Calendar (Google Calendar or Outlook Calendar)

Query: events where `start >= window_start AND start <= window_end`. List events; capture `{event_id, title, start, end, attendees (with emails), location, event_url (the deep-link the calendar connector returns — `connector_adapters/calendar.py::deep_link` prefers it), organizer_email}`.

Per event:
```jsonl
{"type":"meeting","ts":"<ISO>","seq":<seq>,"data":{"event_id":"<id>","title":"<title>","start":"<ISO>","end":"<ISO>","attendees":["<email>","..."],"location":"<loc>","status":"occurred","source_ref":"<gcal:event_id or the connector event id>","inferred_from":["historical_backfill"]}}
```

For new attendee emails, same provisional-person logic as mail.

## Drive / OneDrive / SharePoint

Query: files modified in window. Cap at top 500 most-recently-modified per cloud service.

Per file:
```jsonl
{"type":"file_filed","ts":"<ISO>","seq":<seq>,"data":{"file_id":"<id>","name":"<name>","path":"<path>","mtime":"<ISO>","mime_type":"<mime>","owner":"<email>","sharing":["<email>","..."],"webViewLink":"<url>","inferred_from":["historical_backfill"]}}
```

NEVER `read_file_content` or `download_file_content` here. Names + paths + dates only.

For SharePoint, list each shared site CEO has access to; per site list recent files in the same shape.

## Granola

Query: meetings (transcripts) in window. List only — do NOT fetch full transcript text.

Per transcript:
```jsonl
{"type":"note","ts":"<ISO>","seq":<seq>,"data":{"note_id":"<id>","title":"<title>","date":"<date>","attendees":["<email>","..."],"duration_seconds":<N>,"source_ref":"<granola_url>","inferred_from":["historical_backfill"]}}
```

## Slack / Teams

For Slack: list channels CEO is in. Per channel, get count of M's own messages in window (NOT channel-history bodies). For DMs, get list of DM partners + last-message date.

For Teams: equivalent via Graph API.

This is intentionally light — Slack/Teams metadata is the lowest-signal source for "what projects/people exist" because most signal is in mail + calendar + Drive. We just want to ensure people with whom M has DM cadence get person records.

Per DM partner:
```jsonl
{"type":"interaction","ts":"<ISO>","seq":<seq>,"data":{"channel":"slack","participants":["<m>","<other>"],"last_message_date":"<date>","message_count_in_window":<N>,"inferred_from":["historical_backfill"]}}
```

# Phase 4 — Project clustering pass (lightweight)

After all connector sweeps, scan the chunk's events for clusters that look like projects:

- 3+ emails sharing the same set of attendees with a recurring subject pattern → likely an active thread
- 2+ calendar events with the same attendee set + recurring title → likely a working group
- 5+ files in the same Drive folder with the same sharing list → likely a project folder

For each cluster, IF the project doesn't already exist in entities.json, append a provisional project record. **Auto-status based on activity recency (v2.10.3+)** — clusters that ended a long time ago shouldn't pollute the active workspace:

| Latest signal age | Auto-assigned `status` |
|---|---|
| ≤ 30 days old | `active` (proposed for review) |
| 31–60 days | `dormant` (proposed; visible only via `go [project]` until revived) |
| 61–180 days | `dormant` (older, less likely to be alive — same surfacing) |
| > 180 days | `archived` (proposed; never surfaces in daily flows) |

```jsonl
{"type":"project_proposed","ts":"<ISO>","seq":<seq>,"data":{"proposed_name":"<inferred>","attendees":["<email>","..."],"signal_count":<N>,"signal_types":["email","calendar","drive"],"earliest_signal":"<date>","latest_signal":"<date>","auto_status":"<active|dormant|archived>","inferred_from":["historical_backfill_clustering"],"pending_review":true}}
```

These are PROPOSED projects — they don't enter `entities.projects[]` automatically. The user reviews them via `cleanup` or via insight-generator Pass 9. The `auto_status` field tells the proposal review whether to default the new project to active vs dormant vs archived if the user confirms the proposal.

This is the rule that prevents the 12-month historical backfill from cluttering the active workspace with closed deals from 8 months ago — they land as `archived` proposals, invisible in daily flows, accessible by name if the user ever needs them.

# Phase 5 — Update the resume cursor + log pack_run

Write `_hq/data/.backfill_cursor` with `last_completed_chunk: N` (single-line file, overwrite).

Append (OMIT `seq`/`ts` — the append gate auto-stamps both inside the writer lock, `ts` in UTC; a hand-typed "now" was the F-15 naive-local-clock bug class, v4.5.2 R4.)
```jsonl
{"type":"pack_run","data":{"kind":"historical_backfill","chunk_n":N,"of":M,"window_start":"<date>","window_end":"<date>","tier":"<light|medium|heavy>","status":"complete","mail_threads":<count>,"calendar_events":<count>,"drive_files":<count>,"granola_notes":<count>,"slack_dms":<count>,"new_provisional_persons":<count>,"new_proposed_projects":<count>,"errors":[],"duration_seconds":<N>}}
```

# Phase 6 — Chat output (per Rule 9 — minimal, plain English)

Because backfill runs in the BACKGROUND (one-shot scheduled task fires while user is doing other things), the chat surface should be quiet. Surface a single one-line confirmation:

```
✓ Historical context · chunk N of M done · ingested [W weeks] of metadata · resume cursor updated
```

If errors occurred:
```
⚠ Historical context · chunk N of M complete with degradations · X items skipped · retry will run on tomorrow's Upcoming Meetings fire
```

If nothing happened (idempotent skip):
```
(Historical context chunk N already complete — skipped.)
```

Per Rule 9: NO `pack_run seq XYZ logged`, NO entity counts that read like telemetry. Just the user-facing one-liner.

# Phase 7 — Failure handling (Rule 8)

- Connector rate limit: write what you have, log to `errors[]` in pack_run, surface plain-English `(Connector rate-limited — N items captured, M skipped. Will retry on the next Upcoming Meetings fire.)`
- Connector unavailable: skip that connector for this chunk, log to `errors[]`, surface `(N connector unavailable — chunk completed without it.)`
- entities.json malformed: stop, log `scheduled_task_failure`, surface plain-English diagnostic. DO NOT corrupt the cursor.
- Hard failure (filesystem unwritable, etc.): stop, log, surface, leave cursor untouched. Tomorrow's Upcoming Meetings fire detects the stale chunk and fires a recovery.

# What this orchestrator does NOT do

- Does NOT read message bodies, transcripts, or file content. Metadata only.
- Does NOT modify existing entities.json records (only appends provisional person/project records).
- Does NOT auto-promote provisional records to canonical (that's Pulse's weekly synthesis pass for people; cleanup for projects).
- Does NOT fire follow-on chunks itself — the schedule was set by `enable-command-room-schedules` Phase 4.
- Does NOT block on user input. Pure background scan.
