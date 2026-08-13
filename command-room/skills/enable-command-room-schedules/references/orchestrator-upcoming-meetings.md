# RETIRED — Upcoming Meetings (taskId: upcoming-meetings)

**This chat is retired (SPEC BRIEFMERGE, M's ruling 2026-08-08). It is no longer registered on any new workspace and it is never offered again.**

The file is kept, not deleted, for one reason: a workspace that registered Upcoming Meetings before the retirement still has the task in its Scheduled list, and its bootloader reads THIS path at fire time. Deleting the file would make that fire fail with a load error — the customer would see a broken chat instead of an explanation. Nothing in Command Room disables a task the customer can see without asking (propose, never silent — the same posture LIFECYCLE1 set for Pulse), so the fire's job is now to explain itself and stop.

`source_skill='upcoming-meetings'` (and the historical `cr-upcoming-meetings` / `cr-meetings-today` spellings) stay parseable forever — `receipts.py` normalizes all of them, and every `prep_brief` receipt this chat ever wrote keeps the morning brief's no-prep detector honest. Nothing here is deleted from the event vocabulary; this chat simply stops writing to it.

**Every legacy taskId spelling is retired too, and none of them is a migration target any more.** If you find a registered task whose id still carries the pre-v2.14.27 `cr-` prefix — `cr-upcoming-meetings`, or the older `cr-meetings-today` the rename table used to send here — do NOT disable-and-re-register it under the bare id. That was the correct move while the chat existed; the successor no longer does. Disable the legacy variant per `enable-command-room-schedules` Phase 1 and register nothing in its place.

---

## What to do when this fires

Post exactly this, as the ENTIRE chat turn, then STOP. No widget, no calendar reads, no prep generation, no connector reads, no substrate writes of any kind — not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive. Generating prep from here would be worse than useless: the morning brief already prepped these meetings an hour ago, and a second generator writing over the same files is exactly the duplicate-brief class `prep_slug` exists to prevent.

> *Your Upcoming Meetings chat is retired — it fired half an hour before your morning brief to prep the same meetings the brief already walks you through. Say `pause upcoming meetings` and I'll switch it off. Your morning brief now preps today's meetings itself before it writes — same briefs, same folder, one chat — and anything booked after it runs is one ask away: say `prep me for my 2pm`.*

Build the line from `schedule_config.retirement_line("upcoming-meetings")` rather than retyping it — the registry is what keeps this wording identical to the one the update bridge and `change-schedule` use, and three hand-typed copies of a sentence are three chances to describe the same retirement three ways.

Do NOT offer to re-register it, do NOT propose an alternative schedule, and do NOT run any part of the old prompt "just this once."

## Where the work went

| The old Upcoming Meetings chat did | Now |
|---|---|
| Fetched today's remaining meetings at 6:30 AM | The morning-brief fire's **prep leg** (`references/orchestrator-morning-brief.md` Phase 2.95) makes that same calendar pass, once, and hands the result forward to the digest — no second discovery pass anywhere in the fire. |
| Generated one call-prep document per meeting through the five-block pipeline | The same generator, invoked from that leg: `skills/call-prep/SKILL.md` end to end. Same folder, same filenames, same refresh-in-place identity — the ONE-GENERATOR contract (v4.5.2 S1) with one caller fewer. **This file deliberately names no render route of its own**: it is retired, it produces nothing, and a retirement stub that still spells out how to write a document is an invitation to write one. |
| Honored the call-prep `auto_fire` knob before prepping anything | Read at the same point in the leg, from the same `get_config` call. `off` still means nothing is generated on the scheduled fire; `morning_of` still drops tomorrow's meetings from the auto-prep set. |
| Posted a per-meeting widget with push / snooze buttons | Nothing. The morning brief is read-only (FB-20) and prep outcomes are LINES in its meeting section, not a card. Rescheduling is `calendar-writer` / `change-schedule`; adjudication is the staff meeting. |
| Wrote its own `pack_run` fire receipt | The merged fire writes ONE receipt carrying both legs (`prep_leg.log_combined_receipt`), so the watchdog can finally see "the brief ran, the prep didn't" — which is the sentence nothing could say while this was a separate task that could die in silence. |
| Covered meetings booked later in the day, badly (it only ever ran once) | `call-prep` on demand — "prep me for my 2pm". That was already the honest answer; it is now the only one. There is deliberately NO midday refresh leg. |
