# RETIRED — Pulse (taskId: pulse)

**This chat is retired (SPEC LIFECYCLE1, M's ruling 2026-08-02). It is no longer registered on any new workspace and it is never offered again.**

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this stub's entire contract is ONE plain-text line and then STOP. No widget, no widget-transport render, no scans, no connector reads, no substrate writes — not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive. (The transport helper and the lateness helper are deliberately described here rather than named: a battery guard reads any occurrence of either symbol in a retirement stub as an instruction to run it, and it is right to.) **The marker at the head of this paragraph is load-bearing, not decoration:** `references/scheduled-task-bootloader.md` Step 2 greps the first 2000 bytes of this file for the literal `OUTPUT CONTRACT` and, on a miss, aborts the fire with *"the plugin may be partially installed or corrupted — please reinstall Command Room"* (BUG-9517). This file WAS that false alarm: from the retirement until SPEC_RETIREGATE1 the marker was absent, so every workspace that still had Pulse registered got a corruption warning instead of the retirement line below, and the line was unreachable because Step 2 runs before Step 3 ever reads this file. Registration applies a second, tighter window on the same literal — `enable-command-room-schedules` Step 1.A aborts the whole setup unless it appears in the first 1500 CHARACTERS — so if you edit this file, this block stays at the TOP, not merely early.

The file is kept, not deleted, for one reason: a workspace that registered Pulse before the retirement still has the task in its Scheduled list, and its bootloader reads THIS path at fire time. Deleting the file would make that fire fail with a load error — the customer would see a broken chat instead of an explanation. Nothing in Command Room disables a task the customer can see without asking (SPEC LIFECYCLE1 §4: propose, never silent), so the fire's job is now to explain itself and stop.

`source_skill='pulse'` (and the historical `cr-dont-forget` / `dont-forget` / `inbox-pulse` spellings) stay parseable forever — `receipts.py` normalizes all of them and three receipt shapes sit on disk. Nothing here is deleted from the event vocabulary; this chat simply stops writing to it.

**Every legacy taskId spelling is retired too, and none of them is a migration target any more.** If you find a registered task with `taskId: "cr-pulse"`, `cr-dont-forget`, or `cr-cracks-watch` — the pre-v2.14.27 variants the rename table used to send here — do NOT disable-and-re-register it as `pulse`. That was the correct move while the chat existed; the successor no longer does. Disable the legacy variant per `enable-command-room-schedules` Phase 1 and register nothing in its place.

---

## What to do when this fires

Post exactly the line below, as the ENTIRE chat turn, then STOP. No widget, no scans, no connector reads, no substrate writes of any kind — not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive.

Build it from `schedule_config.retirement_line("pulse")` rather than retyping it — the registry is what keeps this wording identical to the one the update bridge and `change-schedule` use, and three hand-typed copies of a sentence are three chances to describe the same retirement three ways. The blockquote below is a RENDERING of that call, reproduced here so a reader knows what to expect; it is pinned byte-equal to the renderer's output, so if the two ever disagree the registry is right and this file is stale.

> *Your Pulse chat is retired — it fired every weekday morning to say what the morning brief and the staff meeting already say. Say `pause pulse` and I'll switch it off; the quiet-project questions it used to raise now wait until you ask for them — say 'stalled projects' — and the housekeeping it did in the background runs weekly with everything else.*

Do NOT offer to re-register it, do NOT propose an alternative schedule, and do NOT run any part of the old prompt "just this once."

## Where the work went

| The old Pulse did | Now |
|---|---|
| Asked whether a quiet project should go dormant | `stalled projects` — the on-demand surface that already asks about quiet projects. It is the ONE asking surface for those rows (`brain_proposals.load_open_proposals(ws, "on-demand")`). |
| Proposed dormancy, flipped long-quiet projects to dormant, archived long-dormant ones, revived ones that came back | The weekly `lifecycle` job inside the `maintenance` task (`shared/scripts/lifecycle_pass.py`) — the same thresholds, in code, with a receipt. The archive leg goes through `thread_archive.archive_thread`, which the prose here never did. |
| Re-engagement drafts and a `schedule catchup` handler | `relationship-moves` owns proactive outreach (M's ruling 2026-08-02). For a catch-up meeting, `calendar-writer` / `change-schedule` phrasing. |
| The cracks / cadence narrative, the daily entity-proposal peek, org-drift synthesis, the review queue, the intro follow-up check | The morning brief, the staff meeting and the weekly `insight-generator` pass already carry these. The intro follow-up check is answerable on demand through `intro-broker` ("check my intros"). |
| Re-derived person records weekly | The `identity-reconcile` job (identity) and `entity_signal_detector` (role / org facts) — both already running, both stricter about what may auto-apply. |
