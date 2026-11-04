# RETIRED: Staff Meeting (taskId: staff-meeting)

**This chat is off the schedule (the owner's ruling 2026-10-23, cr1#176).** It is no longer registered on any workspace, it is never offered again, and the update bridge switches off any live registration it finds. This is a READINESS retirement, the one class the product applies rather than proposes. `schedule_config.RETIRED_TASKS` is the membership test and `retirement_class("staff-meeting")` is the class; the registry, not this file, is what the bridge, `change-schedule` and `system-health` read.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this stub's entire contract is ONE plain-text line and then STOP. No widget, no widget-transport render, no queue read, no substrate writes, not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive. (The transport helper, the lateness helper and the surface driver are deliberately described here rather than named: a bare mention of any of them is how a stub grows a phase back.)

**THE RETIREMENT GATE this marker clears (RETIREGATE1, BUG-9517):** the bootloader reads the first 2000 bytes of every orchestrator file looking for the OUTPUT CONTRACT marker above before it runs the file; a stub without it aborts the fire with the "no canonical OUTPUT CONTRACT marker" message instead of saying the one line. The marker is load-bearing, not boilerplate: keep it exactly where it is.

The file is kept, not deleted, for one reason: a workspace that registered the Staff Meeting before the retirement still has the task in its Scheduled list until the update reaches it, and its bootloader reads THIS path at fire time. Deleting the file would make that fire abort with a load error, and the customer would see a broken chat instead of an explanation.

`source_skill='staff-meeting'` and every receipt this chat wrote stay parseable forever. Nothing is deleted from the event vocabulary; the scheduled fire simply stops writing to it. The on-demand surface keeps writing its own `staff-meeting` receipt, exactly as before.

---

## What to do when this fires

Post exactly the line below, as the ENTIRE chat turn, then STOP.

Build it from `schedule_config.retirement_line("staff-meeting")` rather than retyping it. The registry is what keeps this wording identical to the one the update bridge and `change-schedule` use, and three hand-typed copies of a sentence are three chances to describe the same retirement three ways.

> *Your Staff Meeting chat is off the schedule — the questions it collected already reach you on their own: your morning brief names any deal signal and counts the rest, and End of Day asks the one or two that matter. Nothing is lost: say `staff meeting` any time and the same full list opens on the spot, one tap per answer. It comes back when you want the full list on a fixed day again.*

Do NOT offer to re-register it, do NOT propose an alternative schedule, and do NOT open the queue, render the list or answer a question "just this once." **Do not offer a `pause` either**: that is the ELIMINATED class's line, and here it would ask the customer to perform a tap the update has already taken.

## Where the work went: on demand, and the daily chats

This is what separates a readiness retirement from an elimination: the surface was not replaced, it was taken off the clock.

| The scheduled chat did | Now |
|---|---|
| Rendered the whole confirm queue (money, identity, housekeeping, objectives) with one-tap answers | `staff meeting` / `run our staff meeting`, owned by `skills/system-health/SKILL.md` Step 5. The same driver over the same queue, page one and every page after it. |
| Put deal signals in front of the owner three mornings a week | The morning brief names each money row in one sentence and points at `staff meeting`, every weekday. |
| Put identity, housekeeping and objective questions in front of the owner | The morning brief counts the whole queue ("N things need your eyes") and points at `staff meeting`; unanswered questions still take their default when their lifetime runs out. |
| Grouped meeting captures | End of Day asks at most two of them, and `needs your call` shows them all on demand. |
| Asked about names the people graph keeps missing | `needs your call` shows them on demand, and so does `staff meeting`. |
| Said how many meeting speakers are still unnamed | `staff meeting` on demand says the same one line. |
| Asked whether watched items are still open, and closed the ones past their window | Page one of `staff meeting` on demand runs the same pass. |
| Held the Monday, Wednesday and Friday 9 AM slots | Nothing. The slots are empty again. |

The queue, the proposals, the receipts and the people records are untouched by this retirement. They are the customer's data, they keep their meaning, and they are what the on-demand surface and the daily chats read.

## What brings it back

The registry's `reoffer_when` is the customer-facing answer, and `retirement_line` above already quotes it. The internal gate is recorded in the `RETIRED_TASKS` entry's comment in `shared/scripts/schedule_config.py`. Read it there rather than restating it here, so there is one place to update when the condition is met.
