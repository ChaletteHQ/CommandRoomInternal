# Receipt Contract — one schema, one writer, one reader (v4.5.2 R1)

**Code home:** `shared/scripts/receipts.py` — the schema constants, the writer
(`log_receipt`), and the reader (`iter_receipts` / `last_receipt_times` /
`count_runs`) live there. This document is the human-readable contract; the
module is the enforcement. `shared/scripts/log_pack_run.py` remains as a
back-compat wrapper that delegates here.

## Why this exists

The 2026-07 dogfood (FINDINGS_M_v451) showed one skill writing two receipt
shapes in one day (F-47 P2a), the same skill drifting `late_tier` vs
`lateness_tier` between runs (F-50 P2c), health check false-flagging Inbox
because its matcher read `task_id` while the receipt carried only `kind`
(F-10b/F-43), and usage-report missing ~10 runs because it read only
`pack_run` events under canonical spellings (F-49). Root cause: the prose
orchestrators each hand-rolled their receipt JSON. The fix is structural —
every writer calls one helper; every reader goes through one parser.

## The rules

1. **One canonical task_id spelling** — the hyphenated registry id
   (`commitments`, `past-meetings`, `upcoming-meetings`, `pulse`, ...).
   Never `cr-` prefixed, never underscored. `normalize_task_id()` is the
   only translation point.
2. **One lateness field: `late_tier`.** `lateness_tier` and bare `tier` are
   legacy spellings — parsed forever via `get_late_tier()`, never written
   again.
3. **`fired_via` ∈ {scheduled, manual, catchup}** on every receipt. R1 ships
   the field; R2 wires run-mode detection (a manual Run-now is `manual`, a
   post-wake catch-up is `catchup` — never a fabricated late scheduled
   fire). Legacy values (`user-trigger`, `scheduled_late_refire`) normalize
   read-side.
4. **`machine`** (hostname) rides on every receipt — schedules are
   per-machine (F-38); without it readers can't tell two machines from a
   double-registration bug.
5. **Readers parse ALL legacy shapes forever.** events.jsonl is append-only
   history. Back-compat lives read-side; history is NEVER migrated in place.
6. **Writers never hand-roll receipt JSON.** An orchestrator's final phase is
   one helper call. If a receipt needs a new field, it goes in `extra_data`
   (task-specific counts) or in this contract (vocabulary).

## Canonical receipt shape

```json
{"type": "pack_run", "source_skill": "past-meetings",
 "ts": "<UTC, auto-stamped inside the writer lock>",
 "data": {"task_id": "past-meetings", "kind": "past-meetings",
          "status": "complete", "fired_via": "scheduled",
          "surfaced": 3, "duration_ms": 41800,
          "late_tier": "note",
          "machine": "MDAVIDOV-PC",
          "telemetry": {"...": "build_pack_run_telemetry() output"},
          "errors": []}}
```

`kind` deliberately duplicates `task_id`: legacy readers key on `kind`.
`late_tier` appears only on late fires. Task-specific counts
(`meetings_processed`, `items_drafted_text`, `needs_attention_ids`, ...)
ride along via `extra_data`.

The `sent_reconcile` audit additionally carries `cursor_from` / `cursor_to` /
`sent_scanned_count` / `n_closed` / `n_pending` (the Bug #98-v3 ungameable
trace), `outcome_watch` counts when the watch ran, and — when the v4.6.2
sent-promise capture pass ran (BUG-3719) — `n_opened` / `n_capture_merged` /
`n_capture_observed` / `n_capture_errors`. Absent capture fields = a
pre-4.6.2 run or a fire whose caller passed no extracted items; readers
never treat absence as zero-with-certainty.

## Receipt types per task

| Task | Receipt type(s) | Run-counted type |
|---|---|---|
| morning-brief, upcoming-meetings, inbox, commitments, past-meetings, friday-wrap, relationship-moves, commitment-triage, weekly-insights, dormant-scan | `pack_run` | `pack_run` |
| pulse | `pack_run` + legacy `pulse_run` / `dont_forget_run` | all three |
| cleanup | `cleanup_run` (legacy `audit_run`) | both |
| reconcile-sent | `sent_reconcile` | `sent_reconcile` |
| session-sweep | `session_sweep_run` | `session_sweep_run` |
| monthly-report | `operator_report_generated` + `value_receipt_generated` | `operator_report_generated` only¹ |

¹ One monthly fire writes 1 operator report + 2-3 value receipts (month +
quarter + the F-36 idempotency dupes R4 fixes) — counting those as runs
would fabricate fires. `value_receipt_generated` stays a freshness signal.

weekly-insights: pre-v4.5.2 fires wrote no audit event; the analytical-view
file mtimes remain a freshness FALLBACK in the watchdog forever.

## Run counting (`count_runs`)

- Receipts of **different** types chained within 15 minutes are ONE run
  (a fire emitting its primary + secondary receipts).
- Receipts of the **same** type never merge — two `session_sweep_run`s four
  minutes apart are two real manual runs (F-08).
- Every requested task appears in the result, zero-filled — a task with no
  receipts reports 0 rather than vanishing from the table (F-49's
  missing-rows failure).

## Run-mode detection (v4.5.2 R2)

Every fire determines its run mode BEFORE the lateness check, and passes it
to both `check_lateness(fired_via=...)` and the closing `log_receipt`:

- **`scheduled`** — the session was started by Cowork's scheduler executing
  this task's registered bootloader prompt: a scheduled-task harness fire,
  with no human message initiating the turn. App-launch catch-up deliveries
  of a missed slot are still scheduler-initiated → detect as `scheduled`;
  `check_lateness` decides whether the receipt says `catchup`.
- **`manual`** — a human caused this fire: a typed trigger phrase, a
  wake-word, a Run Now click, a "re-fire" / "re-run" request in an open
  chat. If there is a human-authored message behind this fire, it is
  manual. **When uncertain, it is manual** — fail-safe by asymmetry: a
  mis-labeled manual costs one missing lateness note; a mis-labeled
  scheduled fabricates lateness history (F-47 P1a wrote three false
  late_fire receipts in one afternoon).
- **`catchup`** — never self-declared by detection. It is DERIVED:
  `check_lateness` returns `receipt_fired_via: "catchup"` when a
  scheduled-context fire is serving a genuinely missed slot (note/degrade
  tier). The receipt carries what the helper returned — nothing else.

Rules that follow from the mode:

1. **Manual fires never write `late_fire`**, never carry `late_tier`, and
   never narrate lateness or its cause. `check_lateness` enforces this in
   code (tier `manual`, no event); orchestrators must not hand-compute
   around it.
2. **Manual fires are interactive** — connector pre-scans (CRU legs, live
   contact checks) run exactly as on a scheduled fire. A run mode NEVER
   adds skip conditions: each phase's enumerated skips are exhaustive
   (F-47's improvised "scheduled autonomous run, no connector fetch" left
   "no email on file" for a contact who had emailed that day).
3. **Schedule changes never fire tasks.** change-schedule's re-anchor is a
   config write + cron update, nothing else — no catch-up run, no lateness
   math against slots the change itself created (F-51). Defensively,
   `check_lateness` refuses to score a slot older than the task's latest
   `schedule_config_changed` event.
4. **Scheduler `lastRunAt` stamps are untrusted.** The 2026-07-08 cleanup
   autopsy proved they land without execution (F-39: 9 tasks stamped at
   app launch, one receipt). Receipts are the only served/not-served truth
   — the served-slot marker in `check_lateness` and the watchdog's cadence
   math both read receipts only.

## Per-brief prep receipts (`prep_brief`, v4.5.2 S1)

Task-fire receipts answer "did the task run"; they cannot answer "does a prep
brief exist for THIS meeting" — which is exactly the question the morning
brief's no-prep flag asks, and why F-29 shipped a false "no prep brief" while
the file and the fire receipt were both on disk.

- **One `prep_brief` event per Call_Prep brief saved**, written via
  `receipts.log_prep_receipt` ONLY — both the scheduled auto-prep
  (orchestrator-upcoming-meetings) and on-demand 'prep me' (call-prep) call
  it after a successful `make_brief` save. Payload: `{meeting_id, slug,
  artifact, generated_by, fired_via, refreshed, machine}`.
- **The detector rule (F-29):** the "no prep" flag may render for a meeting
  ONLY when `receipts.prep_exists_for_meeting(ws, meeting_id)` is False.
  Never from folder globs, filename guesses, or memory.
- **Refresh-in-place (F-29b):** `refreshed: true` records that an existing
  brief was updated rather than a sibling minted. The filename identity is
  `prep_pipeline.prep_slug` (a pure function of the meeting id), so the same
  meeting always resolves to the same file.
- **NOT run-counted:** `prep_brief` is deliberately outside `RECEIPT_TYPES` —
  five briefs in one upcoming-meetings fire are five `prep_brief` events and
  ONE `pack_run`. Counting them as runs would fabricate fires.

## Named consumers of the reader

- `task_watchdog.py` (`last_receipts` + `late_signals` — health check,
  cleanup's weekly pass, morning-brief daily pass; `health_verdict` is the
  R3 truth-rules entry point every health surface renders from)
- usage-report (`count_runs` — the run-count table)
- `value_receipt.py` (briefing-delivery counting)
- command-room-update-bridge (via the watchdog)

## What this contract does NOT change

- Run-mode detection + late_fire semantics — shipped in R2 (§ Run-mode
  detection above; ledger + gate in `late_fire.py`).
- Health-check cadence claims — shipped in R3 (`task_watchdog.health_verdict`
  reads `late_fire` / `fired_via` / `late_tier` through this contract; fire
  history is never asserted without a receipt, and an empty machine-local
  registry is vantage-checked against `schedule_created` + receipts before
  any "not registered" claim — F-43/F-40).
- Value-receipt idempotency (F-36) and timestamp normalization (F-15) — R4.
- History rewrites — never.
