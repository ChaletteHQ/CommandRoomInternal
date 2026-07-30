# Reliability Contract — v2.1

**Purpose:** Every skill in the Command Room plugin has to work reliably under flaky conditions — connectors time out, scheduled tasks fire when the CEO is offline, networks drop, rate limits hit. This contract defines the shared rules for graceful degradation, retry, state preservation, and failure reporting.

**Applies to every skill that:**
- Runs as a scheduled task (morning-briefing, cleanup, insight-generator, dormant-customer-scan).
- Touches a connector (Gmail, Calendar, Slack, Granola, Drive).
- Writes to workspace files (every skill, effectively).

**Read with:** `WORKSPACE_API.md`, `PASSIVE_CAPTURE.md`, `SUBAGENT_VERIFICATION.md` (any orchestrator that fans out subagents re-verifies numeric claims through canonical helpers before rendering — R6).

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

### 4. Missed-fire recovery + late-fire tiers (Phase 3 / R4)

When Cowork fires a missed slot late (machine was off/asleep), every CHAT orchestrator determines its run mode (`shared/RECEIPT_CONTRACT.md` § Run-mode detection) and computes its lateness at the top of the run via `shared/scripts/late_fire.py::check_lateness` — machine-local math (cron evaluates on the machine clock; workspace TZ is presentation-only) — and branches:

- **Manual fire (typed trigger / Run Now / re-run)** — never late: no tier math, no banner, no event (v4.5.2 R2 — F-47 P1a wrote three false late_fires in one afternoon before this gate).
- **< 3h late** — run normally, no mention.
- **3–24h late** — run normally; the output opens with one plain line naming the scheduled time. Facts only — no cause is ever asserted (the "computer was likely asleep" narratives were fabrications, F-47/F-50).
- **> 24h late** — degrade: the stale surface is NOT rendered, but every substrate write the task owes (events, view updates, the pack_run receipt) still lands silently, then one line tells the CEO the full surface was skipped and the next morning brief folds in what mattered.
- **Silent task classes are EXEMPT** (membership = the `SILENT_TASKS` registry in `schedule_config.py`, never a name list): late is fine for the `maintenance` task (MAINT1 — it carries the reconcile / sweep / cleanup / insights / monthly jobs) — silent work always runs in full, and a job missed by a late or skipped fire stays due and self-heals at the next fire (`maintenance_dispatcher.due_jobs`).

Lateness is scored ONLY against an UNSERVED slot (v4.5.2 R2): the task's newest substrate receipt is the served-slot marker — a slot with a receipt after it never produces a second late_fire; a slot older than the task's latest `schedule_config_changed` was minted by the change (F-51) and is never scored; scheduler `lastRunAt` stamps are never consulted (they land without execution — F-39). A `late_fire` event logs on the 3h+ tiers with `fired_via: catchup`; `late_fire.detect_chronic_lateness` (consumed by cleanup's Monday note) proposes a better default time after >24h-late fires in 3 of 4 weeks — the actual move always goes through change-schedule, and a schedule change never fires the task.

### 4b. A late fire processes the whole missed span, not just its nominal window (CATCHUP1)

Dispatch recovery above answers *"does the missed work still run?"*. This answers *"once it runs, what does it look at?"* — and they are different questions with different failure modes. A window measured as a fixed offset from `now` silently drops any closure longer than the offset: a three-day gap and a 24-hour window means two days of meetings are never processed, and the receipt that lands looks identical to a healthy one.

- **Span windows** — every surface whose window is `[now − N, now]` computes it through `shared/scripts/catchup.py` `catchup_window(workspace_root, task_id, floor_hours=N, cap_days=30)` instead. The span runs from the last SUCCESSFUL run, never narrower than the nominal window (a fire an hour ago still covers its normal ground) and never wider than 30 days. No receipt ever means the nominal window — a fresh install does not back-process a month. Live at `past-meetings` (24h) and `friday-wrap` (168h).
- **Partitioned periods** — where each period is its own labelled deliverable (the monthly operator report + value receipt, the opt-in monthly scorecard), one report per missed period via `catchup.missed_periods`, oldest first. "The previous full calendar month" is not a substitute for a month that was skipped; the dispatcher hands these jobs a `periods` list and the registered prompt iterates it.
- **Batch caps must be honest — on EVERY surface the window widened.** A fire that cannot finish its widened window records `window_incomplete_before` on its receipt via `catchup.receipt_window_marker`, and the next window resumes from that value. Without it, widening the window just relocates the orphaning bug behind a green receipt. This is one rule with two shapes, and a fix on only one of them is the bug on the other: where the cap truncates a chronologically sorted list (`past-meetings`, 5 meetings a fire) the marker is the **oldest unprocessed item** and it advances as the backlog drains; where the cap drops the **lowest-ranked** items scattered across the span (`weekly-recap`'s six per-connector budgets, all sized for 7 days against a window that can be 30) no contiguous tail exists, the marker is the **window's own start**, and the next fire re-covers the span — free, because capture dedups on `source_ref_hash`. A fire that sampled its window says *sampled* in any headline that would otherwise claim coverage.
- **A widened window crosses a clock boundary, and it crosses it once.** The span math here is machine-local naive because the scheduler is (R8). A connector query is not: it needs an unambiguous instant. So `catchup_window` returns both forms — naive `start` / `end` for receipt math and span comparisons, offset-carrying `start_aware` / `end_aware` for every connector call — and `catchup.to_connector_iso` is the single promotion point. Never hand a naive machine-local value to `tz.to_local`, which documents naive input as assumed-UTC and will move it by the machine's whole offset. This is LATETZ's failure class, and it is invisible wherever the workspace TZ and the machine TZ agree — agreement is not correctness, it is the bug not showing.
- **Best-effort, always.** Every catch-up computation falls back to the nominal window on any failure. Catch-up widens a window; it is never the reason a fire produces nothing.
- **Catch-up is silent.** The widened span is not narrated as a lateness event — `late_fire` already owns that surface. The only place a wider window may surface is where a headline would otherwise make a false claim about what was covered ("the last 12 days", never "this week"; a counter labelled "Last 24h" over a three-day sweep).
- **Scheduled path only, where the distinction matters.** A human typing "weekly recap" means last week, not five weeks — those call sites pass `scheduled_only=True` with the fire's real `fired_via`, and anything that does not normalize into `{scheduled, catchup}` gets the plain nominal window (DOGFIX1's fail-safe direction).

Legacy per-skill recovery notes (still true, now subordinate to the tiers above):
- morning-briefing: a next-session catch-up briefing may cover up to 3 missed days; beyond that, surface as "you were offline N days — want a full catch-up?".
- cleanup: runs at next opportunity; no catch-up needed — the audit is a point-in-time snapshot.
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

> **Honest mechanism (SPEC CON1):** these are **targets the skill self-polices**, not a hard ceiling — there is no shared timeout wrapper around connector/MCP calls, and the runtime does not interrupt a slow call at 15s. The numbers are a budget the model is asked to honor (stop scanning past ~60s and return partial output) plus the shape of the `connector-timeout` event a skill logs when it gives up. Treat them as a service-level *intent*, not an enforced limit. A real shared wrapper is future work; until it exists, do not claim these are guaranteed.

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

### 3. Concurrent-write protection — events.jsonl writer lock (shipped v3.19.x, SPEC A1)

**Current state, plainly:** `atomic_append_jsonl` writes to `events.jsonl` inside a cross-process **writer lock**, so the whole read → seq-stamp → atomic-rename sequence is one critical section. Two concurrent callers can no longer lose an event or duplicate a seq. The atomic-write helper already guaranteed no reader sees a torn/partial file; the lock now also guarantees both racing writers land.

**The race this closes (pre-A1 behavior):**

1. Skill A reads `events.jsonl` tail (seq=42), computes its new event with seq=43.
2. Skill B reads the same tail before A writes, also computes seq=43.
3. A's `atomic_append_jsonl` reads the file, appends, renames.
4. B's `atomic_append_jsonl` reads the file, appends with its stale seq=43, renames.

Outcome was either a duplicate seq or B's rename silently overwriting A's event (no flag, no log). On Windows the racing `os.replace` could additionally raise `PermissionError [WinError 5]`. Measured pre-A1: 8 processes × 25 concurrent appends produced **25 of 200** events (175 lost). With the lock: 200/200, seq 1..200, zero duplicates.

**How the lock works** (`shared/scripts/writer_lock.py`, `events_writer_lock`):

- **Primary mechanism:** an OS byte-range lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows) on a dedicated, never-rewritten file `_hq/data/.writer.lock`. The kernel releases the lock automatically when the holder process dies, so **a crashed writer recovers with zero manual cleanup** (measured reacquire < 0.01s).
- **Cloud-sync safe:** the lock file's content (a single byte) never changes and the file is never renamed/deleted — zero OneDrive/Drive sync churn. Holder diagnostics go to a sibling `.writer.lock.info` file that the locking logic never reads.
- **Fallback:** if the OS lock syscall raises `OSError` (some network/virtual mounts refuse byte-range locks), it falls back to the sentinel lock (`.writer.lock.sentinel`, 30s stale timeout + pid-liveness reclaim). The write still lands.
- **Timeout + jitter:** 30s default timeout, jittered retry to avoid lockstep contention. On timeout `atomic_append_jsonl` raises `TimeoutError` with an actionable message.
- **Reentrant** per process+thread, so a skill that appends while already holding the lock never deadlocks itself.

**Contention telemetry:** best-effort counters in `_hq/.system/lock_stats.json` (`waits`, `total_wait_ms`, `timeouts`, `fallback_sentinel_acquires`, `last_timeout`), written only when a wait exceeded 100ms (no write on the uncontended fast path). `cleanup` surfaces these in its Monday note and resets them. Telemetry failures are always swallowed — they can never break a write.

**Scope note:** the lock is cooperative and covers `events.jsonl` only. `entities.json` / `aliases.json` keep their own sentinel lock (`atomic_write.acquire_write_lock`); a process holding both must acquire the events lock **second** to avoid an ordering deadlock. Writers that bypass `atomic_append_jsonl` entirely (forbidden per `WORKSPACE_API.md`) remain unprotected — the event-contract guard tests are the enforcement layer there.

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
