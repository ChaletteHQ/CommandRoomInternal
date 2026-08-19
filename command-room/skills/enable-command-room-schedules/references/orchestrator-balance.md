# RETIRED — Balance (taskId: balance)

**This chat is off the schedule (SPEC TASKRET1, M's ruling 2026-08-17).** It is no longer registered on any workspace, it is never offered again, and the update bridge switches off any live registration it finds — this is a READINESS retirement, the one class the product applies rather than proposes. `schedule_config.RETIRED_TASKS` is the membership test and `retirement_class("balance")` is the class test; never a name you remember.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this stub's entire contract is ONE plain-text line and then STOP. No widget, no widget-transport render, no calendar reads, no substrate writes — not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive. (The transport helper and the lateness helper are deliberately described here rather than named: a battery guard reads any occurrence of either symbol in a retirement stub as an instruction to run it, and it is right to.) **The marker at the head of this paragraph is load-bearing, not decoration:** `references/scheduled-task-bootloader.md` Step 2 greps the first 2000 bytes of this file for the literal `OUTPUT CONTRACT` and, when it is missing, aborts the fire with *"the plugin may be partially installed or corrupted — please reinstall Command Room"* (BUG-9517). Two shipped retirement stubs already trip that false alarm; this one must not become the third. If you edit this file, keep the marker inside the first 2000 bytes.

**PERSONAL-LANE CONTRACT, still in force.** This surface was owner-facing only, and retirement does not relax that. The one line below is the entire output; nothing from this fire — no name, no evening, no personal detail — may be read, restated, or carried into any other surface in the same session. There is nothing to leak because there is nothing to compute, and that is the safest shape this file has ever had.

The file is kept, not deleted, for one reason: a workspace that registered Balance before the retirement still has the task in its Scheduled list until the update reaches it, and its bootloader reads THIS path at fire time. Deleting the file would make that fire abort with a load error — the customer would see a broken chat instead of an explanation.

`source_skill='balance'` and every receipt this chat wrote stay parseable forever. Nothing is deleted from the event vocabulary; this chat simply stops writing to it.

---

## What to do when this fires

Post exactly the line below, as the ENTIRE chat turn, then STOP.

Build it from `schedule_config.retirement_line("balance")` rather than retyping it — the registry is what keeps this wording identical to the one the update bridge and `change-schedule` use, and three hand-typed copies of a sentence are three chances to describe the same retirement three ways.

> *Your Balance chat is off the schedule — it needs a connected personal calendar and months of history behind it before it can say anything true about your time, and without those it fires weekly with nothing to tell you. Nothing is lost — say `balance check` any time and it runs on the spot. It comes back when there's a personal calendar connected and enough history behind it to be honest about what has gone quiet.*

Do NOT offer to re-register it, do NOT propose an alternative schedule, and do NOT compute a nudge, read a calendar, or draft a reconnect "just this once." **Do not offer a `pause` either** — that is the ELIMINATED class's line, and here it would ask the customer to perform a tap the update has already taken.

## Where the work went — nowhere. It is on demand.

This is what separates a readiness retirement from an elimination: the surface was not replaced, it was taken off the clock.

| The Sunday chat did | Now |
|---|---|
| Surfaced the coldest personal tie pinned to a genuinely open evening, with the reconnect pre-drafted | `skills/balance/SKILL.md`, unchanged and fully live. Say `balance check` (or `how's my white space` / `plan a date night`) and the identical computation runs on the spot, under the same personal-lane firewall and the same propose-and-confirm rule — it still never books, sends, or spends without a click. |
| Refused honestly when no personal calendar was declared | The same refusal, on the on-demand run. That refusal is exactly why the scheduled fire came out: a weekly chat whose most common outcome is "I can't answer this yet" is a weekly reminder that the product cannot help, and most workspaces never declared a personal calendar. |
| Held the Sunday 8 AM slot | Nothing. The slot is empty again. |

Personal ties, `cadence_days`, and `workspace.personal_calendars` are untouched by this retirement — they are the customer's data, they keep their meaning, and they are what the on-demand surface reads. Declaring a personal calendar still works and still improves the on-demand answer; it simply no longer registers a weekly chat.

## What brings it back

The registry's `reoffer_when` is the customer-facing answer, and `retirement_line` above already quotes it. The internal gate is recorded in the `RETIRED_TASKS` entry's comment in `shared/scripts/schedule_config.py` — read it there rather than restating it here, so there is one place to update when the condition is met.
