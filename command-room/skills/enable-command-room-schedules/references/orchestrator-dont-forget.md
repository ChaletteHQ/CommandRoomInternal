# RETIRED — Pulse (taskId: pulse)

**This chat is retired (SPEC LIFECYCLE1, M's ruling 2026-08-02). It is no longer registered on any new workspace and it is never offered again.**

The file is kept, not deleted, for one reason: a workspace that registered Pulse before the retirement still has the task in its Scheduled list, and its bootloader reads THIS path at fire time. Deleting the file would make that fire fail with a load error — the customer would see a broken chat instead of an explanation. Nothing in Command Room disables a task the customer can see without asking (SPEC LIFECYCLE1 §4: propose, never silent), so the fire's job is now to explain itself and stop.

`source_skill='pulse'` (and the historical `cr-dont-forget` / `dont-forget` / `inbox-pulse` spellings) stay parseable forever — `receipts.py` normalizes all of them and three receipt shapes sit on disk. Nothing here is deleted from the event vocabulary; this chat simply stops writing to it.

**Every legacy taskId spelling is retired too, and none of them is a migration target any more.** If you find a registered task with `taskId: "cr-pulse"`, `cr-dont-forget`, or `cr-cracks-watch` — the pre-v2.14.27 variants the rename table used to send here — do NOT disable-and-re-register it as `pulse`. That was the correct move while the chat existed; the successor no longer does. Disable the legacy variant per `enable-command-room-schedules` Phase 1 and register nothing in its place.

---

## What to do when this fires

Post exactly this, as the ENTIRE chat turn, then STOP. No widget, no scans, no connector reads, no substrate writes of any kind — not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive.

> *Your Pulse chat is retired — it fired every weekday morning to say what your morning brief and staff meeting already say. Say `pause pulse` and I'll switch it off. The quiet-project questions it used to raise now wait until you ask for them (say `stalled projects`), and the housekeeping it did in the background runs weekly with everything else.*

Build the line from `schedule_config.retirement_line("pulse")` rather than retyping it — the registry is what keeps this wording identical to the one the update bridge and `change-schedule` use, and three hand-typed copies of a sentence are three chances to describe the same retirement three ways.

Do NOT offer to re-register it, do NOT propose an alternative schedule, and do NOT run any part of the old prompt "just this once."

## Where the work went

| The old Pulse did | Now |
|---|---|
| Asked whether a quiet project should go dormant | `stalled projects` — the on-demand surface that already asks about quiet projects. It is the ONE asking surface for those rows (`brain_proposals.load_open_proposals(ws, "on-demand")`). |
| Proposed dormancy, flipped long-quiet projects to dormant, archived long-dormant ones, revived ones that came back | The weekly `lifecycle` job inside the `maintenance` task (`shared/scripts/lifecycle_pass.py`) — the same thresholds, in code, with a receipt. The archive leg goes through `thread_archive.archive_thread`, which the prose here never did. |
| Re-engagement drafts and a `schedule catchup` handler | `relationship-moves` owns proactive outreach (M's ruling 2026-08-02). For a catch-up meeting, `calendar-writer` / `change-schedule` phrasing. |
| The cracks / cadence narrative, the daily entity-proposal peek, org-drift synthesis, the review queue, the intro follow-up check | The morning brief, the staff meeting and the weekly `insight-generator` pass already carry these. The intro follow-up check is answerable on demand through `intro-broker` ("check my intros"). |
| Re-derived person records weekly | The `identity-reconcile` job (identity) and `entity_signal_detector` (role / org facts) — both already running, both stricter about what may auto-apply. |
