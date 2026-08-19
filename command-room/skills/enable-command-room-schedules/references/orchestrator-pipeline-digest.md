# RETIRED — Pipeline Digest (taskId: pipeline-digest)

**This chat is off the schedule (SPEC TASKRET1, M's ruling 2026-08-17).** It is no longer registered on any workspace, it is never offered again, and the update bridge switches off any live registration it finds — this is a READINESS retirement, the one class the product applies rather than proposes. `schedule_config.RETIRED_TASKS` is the membership test and `retirement_class("pipeline-digest")` is the class test; never a name you remember.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this stub's entire contract is ONE plain-text line and then STOP. No widget, no widget-transport render, no connector reads, no substrate writes — not even a receipt. A retired chat that keeps writing receipts is a retired chat that still looks alive. (The transport helper and the lateness helper are deliberately described here rather than named: a battery guard reads any occurrence of either symbol in a retirement stub as an instruction to run it, and it is right to.) **The marker at the head of this paragraph is load-bearing, not decoration:** `references/scheduled-task-bootloader.md` Step 2 greps the first 2000 bytes of this file for the literal `OUTPUT CONTRACT` and, when it is missing, aborts the fire with *"the plugin may be partially installed or corrupted — please reinstall Command Room"* (BUG-9517). Two shipped retirement stubs already trip that false alarm; this one must not become the third. If you edit this file, keep the marker inside the first 2000 bytes.

The file is kept, not deleted, for one reason: a workspace that registered the digest before the retirement still has the task in its Scheduled list until the update reaches it, and its bootloader reads THIS path at fire time. Deleting the file would make that fire abort with a load error — the customer would see a broken chat instead of an explanation.

`source_skill='cr-pipeline'` and every `pack_run` this chat wrote stay parseable forever. Nothing is deleted from the event vocabulary; this chat simply stops writing to it — which also means the "movement since the last digest" window has no new anchors to compute against, and nothing downstream should try.

---

## What to do when this fires

Post exactly the line below, as the ENTIRE chat turn, then STOP.

Build it from `schedule_config.retirement_line("pipeline-digest")` rather than retyping it — the registry is what keeps this wording identical to the one the update bridge and `change-schedule` use, and three hand-typed copies of a sentence are three chances to describe the same retirement three ways.

> *Your Pipeline Digest chat is off the schedule — it reports on tracked deals every week, and deal tracking itself is being rebuilt underneath it. Nothing is lost — ask for the pipeline any time and you get the same report on the spot. It comes back when deal tracking has been rebuilt and the weekly report has something steady to read.*

Do NOT offer to re-register it, do NOT propose an alternative schedule, and do NOT compute or render any part of the report "just this once" — a stub that still knows how to draw the tile band is an invitation to draw it. **Do not offer a `pause` either** — that is the ELIMINATED class's line, and here it would ask the customer to perform a tap the update has already taken.

## Where the work went — nowhere. It is on demand.

This is what separates a readiness retirement from an elimination: the surface was not replaced, it was taken off the clock.

| The Tuesday chat did | Now |
|---|---|
| Rendered the ranked pipeline report — tile band, ranked rows, top-3 moves | `skills/pipeline-tracker/SKILL.md` § "The report", unchanged and fully live. Ask for the pipeline and the identical computation path runs on the spot. It was always ONE computation path shared with the on-demand ask; the digest was the scheduled caller, and only the caller is gone. |
| Reported movement since the last digest (opened / moved / closed / newly stalled) | Nothing on a schedule. The `deal_*` events still accumulate and the on-demand report still reads them; what is gone is the weekly comparison against a baseline that the tracking rework is about to move. |
| Pointed at the Staff Meeting when deal-kind proposals were pending | The Staff Meeting, unchanged — it was always the sole adjudication door (FB-20), and it still is. |

The `pipeline-tracker` skill's `digest.enabled` preference never registered anything by itself and still doesn't; with the taskId retired it records a preference nothing acts on. Leave any stored value alone — it is harmless, and rewriting a customer's stored preferences from a retirement is out of scope for every class.

## What brings it back

The registry's `reoffer_when` is the customer-facing answer, and `retirement_line` above already quotes it. The internal gate is recorded in the `RETIRED_TASKS` entry's comment in `shared/scripts/schedule_config.py` — read it there rather than restating it here, so there is one place to update when the condition is met.
