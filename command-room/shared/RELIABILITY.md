# Reliability Contract — v2.1

**Purpose:** Every skill in the Command Room plugin has to work reliably under flaky conditions — connectors time out, scheduled tasks fire when the CEO is offline, networks drop, rate limits hit. This contract defines the shared rules for graceful degradation, retry, state preservation, and failure reporting.

**Applies to every skill that:**
- Runs as a scheduled task (morning-briefing, cleanup, insight-generator, dormant-customer-scan).
- Touches a connector (Gmail, Calendar, Slack, Granola, Drive).
- Writes to workspace files (every skill, effectively).

**Read with:** `WORKSPACE_API.md`, `PASSIVE_CAPTURE.md`.

---

## Core Principles

> The primary user task always wins. Side effects and telemetry never block the primary output.

> Fail loudly in the log. Fail quietly in the UX. The CEO sees what they asked for, always.

> Idempotency is structural, not best-effort. Re-running any skill on the same inputs produces the same final state.

---

## Scheduled Task Reliability

Skills that fire as scheduled tasks (morning-briefing at 7:30am, cleanup Sundays, insight-generator Sunday evening) follow these rules:

### 1. Skip, don't fail, when context is missing

If a scheduled task fires and the workspace isn't ready (no entities.json, no BUSINESS_CONTEXT.md, no recent session activity), the skill must:
- Log a single line to `_hq/logs/scheduled-task-skips.log` — `YYYY-MM-DD HH:MM [skill-name] skipped: [reason]`
- Exit cleanly with no output.
- NEVER create an empty briefing, empty audit, or generic fallback content.

### 2. Holiday / vacation detection

Before producing output, check `_hq/BUSINESS_CONTEXT.md` for an "Out of office" marker (date range). If the CEO is OOO:
- morning-briefing still runs but output is titled "OOO BRIEFING — [date]" and contains only urgent-flagged items plus a "welcome back" summary on the return date.
- insight-generator defers until 2 days after return date.
- cleanup runs normally (maintenance is always valuable).

### 3. Weekend handling

- morning-briefing: skip on weekends UNLESS explicitly triggered manually. Its default schedule is weekdays only.
- cleanup: fires Sundays (intentional — the CEO reviews Monday AM).
- insight-generator: fires Sunday 19:00 (intentional — ready for Monday).
- dormant-customer-scan: weekly Sunday (same reason).

### 4. Missed-fire recovery

If a scheduled task was due but didn't fire (user's machine off, etc.), on the next session launch:
- morning-briefing: produces one catch-up briefing covering the gap window (max 3 missed days; beyond that, surface as "you were offline N days — want a full catch-up?").
- cleanup: runs at next opportunity; no catch-up needed — audit is a point-in-time snapshot.
- insight-generator: reruns once if the missed fire was <7 days ago; otherwise defers to next scheduled fire.

### 5. Scheduled task output delivery

Per each skill's delivery spec, output goes to:
- Slack DM (if connected and preferred)
- Gmail to the CEO's own address (if preferred)
- `_hq/briefings/` / `_hq/insights/` / `_hq/audit-reports/` (fallback — the CEO sees it on next session launch)

If the primary delivery channel fails (Slack down, Gmail rate-limited), fall back to file-save and continue — never drop the output.

---

## Connector Failure Handling

Every connector read follows this error protocol:

### 1. Timeout budget

- Single connector read timeout: 15 seconds.
- Full skill connector-scan timeout: 60 seconds (aggregate across all connectors).
- If budget exceeded, abort remaining reads, return partial output with a `⚠️ [Connector] skipped — timeout` line.

### 2. Graceful degradation

For every connector read, if the connector:
- Is not connected: silently skip. Never surface "Gmail is not connected" to the user — they know. Build the output from available sources.
- Returns an auth error (expired OAuth): surface ONCE per session: "⚠️ [Connector] needs reconnection — [link]." Save a flag in `_hq/logs/auth-failures.log` so subsequent skills in the same session don't re-prompt.
- Returns a rate-limit error: back off exponentially (wait 5s, 15s, 60s) up to 3 attempts. If still rate-limited, mark that connector unavailable for the rest of the session.
- Returns a 5xx: retry once after 3s. If still failing, treat as unavailable.
- Returns malformed data: skip the specific bad record, log to `_hq/CONFLICTS.md` with type `connector-malformed`, continue with the rest.

### 3. Cache of last-known-good

Skills that run daily (morning-briefing) may read a per-connector cache of the last successful fetch (stored at `_hq/caches/[connector]-last-good.json`, overwritten on every successful read). If a connector fails completely, the skill can fall back to the cache and flag: "⚠️ [Connector] unavailable — showing last-known-good from [timestamp]."

Caches are:
- Per-workspace, never shared (per PLUGIN_BOUNDARY rule 2).
- Auto-pruned: only the most recent successful fetch per connector is kept.
- Not considered memory — caches are regeneration-safe. Deleting `_hq/caches/` loses nothing the next run won't regenerate.

### 4. Never fabricate when data is missing

If a connector is unavailable and no cache exists, the skill must NOT fill the gap with guesses. Say "data unavailable," skip that section, continue.

---

## Write Reliability

Per `WORKSPACE_API.md`, all writes are transactional at the append level. Additional reliability rules:

### 1. Write ordering

For skills that do both a JSON append and a markdown append (per `PASSIVE_CAPTURE.md`):
1. Append to `events.jsonl` FIRST.
2. Append to the SESSION_NOTES or other markdown source SECOND.
3. If step 1 fails: skip step 2, log to `_hq/CONFLICTS.md`, surface "couldn't log this event — workspace may be stale. Try again or check `_hq/CONFLICTS.md`."
4. If step 1 succeeds and step 2 fails: log to CONFLICTS, flag "event captured but narrative notes incomplete — `events.jsonl` has the source of truth."

### 2. Idempotency via dedup

Every event append includes a dedup hash (see `PASSIVE_CAPTURE.md` rule on hashing). Before appending:
- Compute the hash.
- Check the last 200 events for a match.
- If matched, skip (no error, no log — silent success).

This makes re-running any skill safe. A user can "run morning-briefing" three times and still have one set of events captured.

### 3. Concurrent-write protection — known gap (v3.6.1)

**Current state, plainly:** the atomic-write helper (`shared/scripts/atomic_write.py`) guarantees no reader sees a torn or partial file. It does NOT guarantee that two concurrent writers both land in the final file. `atomic_append_jsonl` reads the existing file, appends in memory, and atomic-renames — it is not an O_APPEND write, despite the name. Two callers that race produce a last-writer-wins outcome.

**Failure mode:**

1. Skill A reads `events.jsonl` tail (seq=42), computes its new event with seq=43.
2. Skill B reads the same tail before A writes, also computes seq=43.
3. A's `atomic_append_jsonl` reads the file, appends, renames.
4. B's `atomic_append_jsonl` reads the file (with or without A's event depending on timing), appends with its stale seq=43, renames.

Outcome is either a duplicate seq (`cleanup` flags as `schema-violation` next Sunday) or B's rename overwrites A's renamed content — A's event is gone, no flag, no log.

**Practical exposure today:**

The plugin's default scheduled-task cron expressions in `enable-command-room-schedules` stagger most fires to non-overlapping minutes (6:30, 7:00, 8:30, 9:00, 17:00 weekdays). There is a known 7:00 collision between `morning-brief` and `inbox` once both are registered — the operator shifts one via `change schedule` per that skill's Phase 3 note.

The overlap that DOES happen in practice: on-demand event-emitting skills (`go [project]`, `process meeting`, `follow-up ritual`, anything captured via `passive_capture.py`) firing at any time, including during a scheduled-task window. CEO running `process the call` at 7:01 while `morning-briefing` is still in flight is the realistic scenario.

**Mitigations available today:**

- Avoid on-demand event-emitting skills during scheduled-task windows (6:30–9:00 and 17:00 weekdays on default cron).
- Run `cleanup` — duplicate-seq detection won't recover lost events but acts as a canary.

**Roadmap (deferred indefinitely as of v3.11.1):**

A `_hq/.writer.lock` with cross-platform serialization (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows, 30-second stale-lock timeout) has been on the backlog since v3.6.1. Originally tracked for v3.7.0; carried through v3.7–v3.11 without shipping. Honest status today: **not actively scheduled.** The exposure window is real — overlapping on-demand event writers + scheduled-task fires can silently overwrite — but bounded in practice (most workspaces fire one scheduled task at a time, and the mitigations below cover the common case). Shipping a lock that doesn't actually protect would be worse than the current honest gap; closing it requires testing against real concurrent-write + cloud-sync scenarios (Google Drive, OneDrive, iCloud), which has not been prioritized over the v3.7–v3.11 product work.

If you hit an actual events.jsonl corruption traced to overlapping writes, file a `report bug` — that's the trigger to actually schedule this. Until then, treat it as known-deferred risk.

### 4. Corrupted file recovery

If events.jsonl, entities.json, or aliases.json fails to parse:
- Automatically restore from the most recent valid backup at `_hq/backups/[file]_[date].backup` (if exists).
- Log to `_hq/CONFLICTS.md` with type `corruption-recovery`.
- Surface to the user: "⚠️ Detected corruption in [file]. Restored from backup of [date]. Any events in the gap may need to be re-captured."

Backups are auto-snapshotted daily by `cleanup`; retained for 14 days.

---

## Logging Convention

Every skill that hits a reliability edge case logs in a standard format:

```
[YYYY-MM-DDTHH:MM:SSZ] [skill-name] [event-type] [message]
```

Event types:
- `scheduled-task-skip` — scheduled fire that exited cleanly without producing output.
- `connector-timeout` — connector hit the 15s timeout.
- `connector-auth-failure` — OAuth needs refresh.
- `connector-rate-limit` — backed off due to rate limit.
- `dedup-hit` — event append was skipped because hash matched.
- `write-failure` — write protocol failed after retries.
- `corruption-recovery` — auto-restored from backup.

Logs rotate weekly — the writer-helper archives `_hq/logs/[type].log` to `_hq/logs/archive/YYYY-WW-[type].log.gz` and starts fresh.

---

## User-Facing Messaging

When a reliability event has to surface to the user:

### Keep it single-line and actionable.

- ✅ "⚠️ Gmail skipped — timeout. Re-run or check connection."
- ❌ "We were unable to fetch data from Gmail due to a network timeout. Please verify your internet connection and OAuth token validity, then re-run the command..."

### Never block the primary output.

The briefing / audit / brief comes first. Warnings go at the top or bottom as a brief note, not as a paragraph.

### Don't apologize.

- ✅ "Couldn't reach Slack. Showing the rest."
- ❌ "I'm sorry, but I wasn't able to..."

### Never invent explanations.

If you don't know why something failed, say "[Connector] unavailable" and move on. Don't speculate ("probably a network issue").

---

## What This Contract Does Not Do

- Does not handle catastrophic failures (workspace folder missing, disk full, OS-level errors). Those are surfaced as the system-level error with no graceful fallback — the user needs to fix infrastructure.
- Does not replace the user manually clicking "retry" — the CEO can always re-run a skill to get fresh data.
- Does not guarantee real-time freshness — Command Room is a near-real-time system, not a live one. A briefing might be 15 minutes stale; that's fine.

---

## Validation

`cleanup` checks these reliability invariants:

1. `_hq/logs/*.log` files exist and are actively written (no skill is silently swallowing errors).
2. `_hq/caches/` exists for any skill that uses caches.
3. No skill has more than 5 unresolved `connector-auth-failure` logs in the last 7 days (surface to CEO).
4. No skill has `write-failure` logs in the last 7 days (blocks auto-update until resolved).

---

**End of reliability contract.**
