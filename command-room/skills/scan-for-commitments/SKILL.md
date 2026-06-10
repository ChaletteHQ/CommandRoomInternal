---
name: scan-for-commitments
description: "One-shot bulk scan over historic Granola transcripts and Gmail threads to retroactively populate `type: commitment` events in `_hq/data/events.jsonl`. Use this when the workspace has meetings/emails on file but no commitment events (the v2.7.13/14 commitment views show empty even though there's clearly work to track). Idempotent — re-running skips already-extracted commitments via (source_ref, title) dedup. Triggers: 'scan for commitments', 'backfill commitments', 'populate commitments', 'extract commitments from history', 'why don't I have any commitments', 'commitments are empty', 'commitments not showing up', 'where are my commitments', 'rebuild commitments from history', 'historical commitment scan', 'one-time commitment backfill'. DOES NOT fire on 'log a commitment' (workspace-manager owns explicit single logs) or 'show me my commitments' (just opens the commitments-tracker artifact)."
---

## Skill Boundary

- **Use scan-for-commitments for:** retroactive bulk extraction. The workspace has meeting events / interaction events from past Granola transcripts / Gmail threads, but no `type: commitment` events. This is the migration path that backfills them.
- **Use `meeting-notes` for:** real-time per-meeting commitment extraction going forward.
- **Use `inbox-triage` for:** real-time per-email commitment extraction going forward.
- **Use `workspace-manager` for:** explicit one-off commitment logging ("log that I owe Aria X by Friday").

If the user's phrasing is ambiguous ("get my commitments going"), prefer this skill — it fixes the empty-views problem in one pass.

---

# Scan for Commitments (v2.7.15)

## Why this exists

The Workspace Map sidebar artifact + the daily Commitments scheduled chat both aggregate from `type: commitment` events in `events.jsonl`. If there are zero such events, those surfaces look empty — even when the user has dozens of historical meetings + active email threads where commitments are obvious. (Pre-v2.9.0 had a People Network + Commitments Tracker sidebar pair too; those were retired when the daily-chat surface absorbed their content. Workspace Map is the only surviving sidebar consumer.)

Pre-v2.7.15, `meeting-notes` was supposed to write commitments but the contract was implicit (no schema doc, no explicit step). Many transcripts were processed without producing commitment events. `scan-for-commitments` is the one-shot fix.

This skill is also useful for: a fresh workspace ingest (workspace-ingest emits sparse `meeting` events without commitments), users migrating from prior versions, and periodic re-scans if the user adds Granola/Gmail history that pre-dates the install.

---

## Writer Contract

This skill is a primary appender to `_hq/data/events.jsonl`. It emits `type: commitment` events using the canonical v2.7.15+ schema documented in `shared/COMMITMENT_SCHEMA.md`. **`source_skill` MUST be `"scan-for-commitments"`** so the events are distinguishable from per-meeting writes (some users may want to roll back a bad scan; that requires being able to filter by source_skill).

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
- Window: `"last 30 days"` (default), `"last 90 days"`, `"all time"`
- Source: `"meetings only"` (skip Gmail), `"email only"` (skip Granola), `"all"` (default)
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
Want me to show you what I'd add first before I commit anything? (Y/N)
```

If the user says dry-run or doesn't answer in the affirmative, proceed in dry-run mode (Step 5).

### Step 2 — Pull the source artifacts

For each `meeting` event (within the time window):
- Read `data.source_ref` (e.g., `granola:abc123`) to get the Granola meeting id
- Resolve the transcript tool via `discover_transcript_tool()` from `shared/scripts/tool_discovery.py` (per CONTRACT.md Rules 9 + 21 — native parity across Granola / Fireflies). Call its `result.tool_id`'s `get_meeting_transcript` method. Do NOT hardcode a per-installation MCP UUID.

For each inbound `interaction` event with `channel: "email"` (within the time window):
- Read `data.source_ref` (e.g., `gmail:msg_xyz`) to get the message id
- Resolve the mail-thread-fetch tool via `discover_mail_thread_fetch_tool()` from `shared/scripts/tool_discovery.py` (Gmail / Outlook native parity). Call its `result.tool_id`'s thread/conversation fetch method. Do NOT hardcode a per-installation MCP UUID.

If a source artifact is unavailable (transcript deleted, email purged), log to `_hq/CONFLICTS.md` and skip that event. Do not abort the scan — partial backfill is better than no backfill.

### Step 3 — Extract commitments per source

Apply the trigger logic from `shared/COMMITMENT_SCHEMA.md` § "Extraction triggers":
1. Forward-looking deliverable
2. Specific artifact / decision (not vague)
3. Identifiable named owner

For each qualifying match in a transcript or email body, prepare a `type: commitment` event in canonical shape. Resolve owner names via `aliases.json` to canonical `person_id`. If owner can't be resolved, prepare the event with `owner_id: ""` and surface the unresolved name in the post-scan summary.

### Step 4 — Dedup before writing

Match on `(source_ref, title)`. If a `type: commitment` event already exists in events.jsonl with the same `data.source_ref` AND the same `data.title` (case-insensitive substring match — first 60 chars), skip it. This is the only safe way to make the scan idempotent across re-runs.

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

Say "commit" if you want me to add these. Otherwise nothing changes.
```

**Commit mode** — write events using the writer helper from `shared/WORKSPACE_API.md` (handles seq reservation, conflict logging, view regeneration). After all writes complete, regenerate `_hq/views/COMMITMENTS.md` (if the views generator exists) and surface a summary:

```
Done — added 14 commitments to your tracker.

- 9 they owe you (5 from Mira, 2 from Aria, 1 from Bowie, 1 from Quinn)
- 5 you owe (3 to Sam, 1 to Mira, 1 to Dustin)
- 2 already overdue (flagged in red in your commitments view)

3 candidates were too ambiguous to call either way — I set those aside for you to look at.
1 was already closed — I left it alone.

Open your Workspace Map or your daily commitments chat to see them.
```

---

## Step 6 — Recovery & rollback

If the user reviews the result and decides the scan produced bad data ("too many false positives", "wrong owner attribution"), they have two options:

**Option A — close them with a single resolved event (preferred for partial bad batches).** Walk events.jsonl back, identify the bad commitments by `source_skill: "scan-for-commitments"` and the timestamp window, and emit a `commitment_resolved` event for each with `data.evidence: "scan-for-commitments rollback YYYY-MM-DD"`.

**Option B — bypass the scan results in views (for total rollback).** Add a `_hq/data/scan_overrides.json` file with the scan timestamp; aggregator reads it and filters out commitments matching that source_skill + timestamp prefix. This is a v2.7.16 feature — currently Option A is the only path.

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

---

## Demo Beat

User installs Command Room, ingests their workspace (workspace-ingest produces sparse meeting events). Opens orgs-map / people-network / commitments-tracker — sees mostly empty.

Says: "Why don't I have any commitments?"

This skill fires, runs the audit, previews the scan, gets confirmation, adds 14 commitments, and the views populate immediately. From "empty workspace" to "full commitment tracker" in one trigger phrase. That's the demo.

---

## What It Doesn't Do

- Does not extract commitments from Slack — needs the slack connector wrapper that doesn't exist yet (v2.7.16 candidate).
- Does not extract from raw text the user pastes — that's `meeting-notes` (paste-fallback path).
- Does not auto-resolve commitments — resolution events are written by `follow-up-ritual` (when a meeting closes one) or by the user explicitly via the DCC's ✓ done batch.
- Does not run on a schedule — explicit-trigger only. The whole point is "one-shot bulk fix."
- Does not modify or remove existing events. Append-only.
- Does not invent commitments where the source language is ambiguous. If in doubt, skip and surface as "ambiguous candidate" in the conflict log.

---

## Connected Tools

- Transcript fetch — resolved via `discover_transcript_tool()` (`shared/scripts/tool_discovery.py`). Native Granola or Fireflies; never hardcoded per-installation UUIDs (Rule 21 native parity).
- Mail thread fetch — resolved via `discover_mail_thread_fetch_tool()`. Native Gmail (`get_thread`) or Outlook (`get_conversation`).
- `_hq/data/events.jsonl` — append-only writer
- `_hq/data/aliases.json` — read-only (owner resolution)
- `_hq/data/entities.json` — read-only (user_id resolution)
