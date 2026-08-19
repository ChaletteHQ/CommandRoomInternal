# RETIRED — Commitment Triage (taskId: commitment-triage)

**This chat is off the schedule (SPEC TASKRET1, M's ruling 2026-08-17).** It is no longer registered on any workspace, it is never offered again, and the update bridge switches off any live registration it finds — this is a READINESS retirement, the one class the product applies rather than proposes. `schedule_config.RETIRED_TASKS` is the membership test and `retirement_class("commitment-triage")` is the class test; never a name you remember.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this stub's entire contract is ONE plain-text line and then STOP. No widget, no widget-transport render, no connector reads, no substrate writes — not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive. (The transport helper and the lateness helper are deliberately described here rather than named: a battery guard reads any occurrence of either symbol in a retirement stub as an instruction to run it, and it is right to.) **The marker at the head of this paragraph is load-bearing, not decoration:** `references/scheduled-task-bootloader.md` Step 2 greps the first 2000 bytes of this file for the literal `OUTPUT CONTRACT` and, when it is missing, aborts the fire with *"the plugin may be partially installed or corrupted — please reinstall Command Room"* (BUG-9517). Two shipped retirement stubs already trip that false alarm; this one must not become the third. If you edit this file, keep the marker inside the first 2000 bytes.

The file is kept, not deleted, for one reason: a workspace that registered Commitment Triage before the retirement still has the task in its Scheduled list until the update reaches it, and its bootloader reads THIS path at fire time. Deleting the file would make that fire abort with a load error — the customer would see a broken chat instead of an explanation.

`source_skill='commitment-triage'` stays parseable forever — `receipts.py` keeps the id in `CANONICAL_TASK_IDS` and every `pack_run` this chat ever wrote stays readable. Nothing is deleted from the event vocabulary; this chat simply stops writing to it.

---

## What to do when this fires

Post exactly the line below, as the ENTIRE chat turn, then STOP.

Build it from `schedule_config.retirement_line("commitment-triage")` rather than retyping it — the registry is what keeps this wording identical to the one the update bridge and `change-schedule` use, and three hand-typed copies of a sentence are three chances to describe the same retirement three ways.

> *Your Commitment Triage chat is off the schedule — a weekly pass over the whole list only helps once the list is trustworthy, and the sorting that decides what belongs on it is still being built. Nothing is lost — say `triage my commitments` any time and the same full review runs on the spot. It comes back when the backlog sorts itself well enough that a weekly review is reading a real list.*

Do NOT offer to re-register it, do NOT propose an alternative schedule, and do NOT run any part of the old prompt "just this once." **Do not offer a `pause` either** — that is the ELIMINATED class's line, and here it would ask the customer to perform a tap the update has already taken.

## Where the work went — nowhere. It is on demand.

This is what separates a readiness retirement from an elimination: the surface was not replaced, it was taken off the clock.

| The Friday chat did | Now |
|---|---|
| Rendered the full open commitment set, oldest first, with the triage verb row (done / defer / drop / not mine / make task / promote / never-track + undo) | `skills/commitment-triage/SKILL.md`, unchanged and fully live. Say `triage my commitments` and the identical Steps 1–4 run on the spot. The skill was never retired — only its 3 PM Friday fire was. |
| Swept 30-day task staleness ("still on your plate?") | The same Step, in the same skill, on the same on-demand run. |
| Fired weekly whether or not the list was worth reviewing | Nothing. That is the retirement: a weekly pass over a list whose sorting is still being built teaches the reader to skip the pass, and that habit outlives the fix. |

## What brings it back

The registry's `reoffer_when` is the customer-facing answer, and `retirement_line` above already quotes it. The internal gate is recorded in the `RETIRED_TASKS` entry's comment in `shared/scripts/schedule_config.py` — read it there rather than restating it here, so there is one place to update when the condition is met.
