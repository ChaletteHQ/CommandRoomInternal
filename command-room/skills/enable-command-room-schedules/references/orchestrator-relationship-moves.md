# RETIRED — Relationship Moves (taskId: relationship-moves)

**This chat is off the schedule (M's ruling 2026-09-24, the v5.33.0 train).** It is no longer registered on any workspace, it is never offered again, and the update bridge switches off any live registration it finds — this is a READINESS retirement, the one class the product applies rather than proposes. `schedule_config.RETIRED_TASKS` is the membership test and `retirement_class("relationship-moves")` is the class; the registry, not this file, is what the bridge, `change-schedule` and `system-health` read.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this stub's entire contract is ONE plain-text line and then STOP. No widget, no widget-transport render, no calendar or mail reads, no substrate writes — not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive. (The transport helper and the lateness helper are deliberately described here rather than named: a bare mention of either is how a stub grows a phase back.)

**THE RETIREMENT GATE this marker clears (RETIREGATE1, BUG-9517):** the bootloader reads the first 2000 bytes of every orchestrator file looking for the OUTPUT CONTRACT marker above before it runs the file; a stub without it aborts the fire with the "no canonical OUTPUT CONTRACT marker" message instead of saying the one line. The marker is load-bearing, not boilerplate: keep it exactly where it is.

The file is kept, not deleted, for one reason: a workspace that registered Relationship Moves before the retirement still has the task in its Scheduled list until the update reaches it, and its bootloader reads THIS path at fire time. Deleting the file would make that fire abort with a load error — the customer would see a broken chat instead of an explanation.

`source_skill='relationship-moves'` and every receipt this chat wrote stay parseable forever. Nothing is deleted from the event vocabulary; this chat simply stops writing to it.

---

## What to do when this fires

Post exactly the line below, as the ENTIRE chat turn, then STOP.

Build it from `schedule_config.retirement_line("relationship-moves")` rather than retyping it — the registry is what keeps this wording identical to the one the update bridge and `change-schedule` use, and three hand-typed copies of a sentence are three chances to describe the same retirement three ways.

> *Your Relationship Moves chat is off the schedule — its Sunday outreach picks already ride the Staff Meeting as 'This week's moves', so a separate Sunday chat said the same thing a day early. Nothing is lost — say `who should I reach out to` any time and the same picks run on the spot, and the Staff Meeting still carries them every week. It comes back when you want the Sunday outreach picks as their own chat again.*

Do NOT offer to re-register it, do NOT propose an alternative schedule, and do NOT compute the picks, read a calendar, or draft an outreach "just this once." **Do not offer a `pause` either** — that is the ELIMINATED class's line, and here it would ask the customer to perform a tap the update has already taken.

## Where the work went — the Staff Meeting, and on demand

This is what separates a readiness retirement from an elimination: the surface was not replaced, it was taken off the clock, and its picks already had a second home.

| The Sunday chat did | Now |
|---|---|
| Ranked who to reach out to this week from dormancy, cadence and open threads, with the outreach pre-drafted | `skills/relationship-moves/SKILL.md`, unchanged and fully live. Say `who should I reach out to` and the identical computation runs on the spot, under the same propose-and-confirm rule — it still never sends without you. |
| Posted the picks on Sunday evening for Monday | The Staff Meeting's "This week's moves" section (LB1 R4) carries the same picks every Monday, Wednesday and Friday, beside the confirm queue — which is why a separate Sunday chat said the same thing a day early. |
| Held the Sunday 5 PM slot | Nothing. The slot is empty again. |

Dormancy baselines, cadence days and the people records are untouched by this retirement — they are the customer's data, they keep their meaning, and they are what both the Staff Meeting section and the on-demand surface read.

## What brings it back

The registry's `reoffer_when` is the customer-facing answer, and `retirement_line` above already quotes it. The internal gate is recorded in the `RETIRED_TASKS` entry's comment in `shared/scripts/schedule_config.py` — read it there rather than restating it here, so there is one place to update when the condition is met.
