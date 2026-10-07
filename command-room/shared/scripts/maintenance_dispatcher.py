#!/usr/bin/env python3
"""
Maintenance dispatcher — the due-jobs engine for the single `maintenance`
scheduled task (MAINT1, 2026-07).

WHY ONE TASK
------------
Every new taskId needs a manual Run Now per client machine (Cowork's one-time
permission gate), so every release that added a silent task created a
fleet-wide silent-failure risk: the task registers on update but never fires,
and nothing visible breaks (`task_watchdog.check_tasks`'s `never_authorized`
status exists only to detect it after the fact). The five silent tasks
(cleanup / reconcile-sent / monthly-report / weekly-insights / session-sweep)
now run as JOBS inside one `maintenance` task (cron `45 6,12,16,17 * * *`,
the hourly-minimum form BOOT3 shipped 2026-09-19), and
any future silent job lands inside the already-authorized taskId — zero
client action, ever.

DUE-NESS IS CODE, NEVER LLM-JUDGED
----------------------------------
One uniform rule for every job, whatever its cadence: a job is DUE iff its
last receipt (via the v4.5.2 R1 receipt contract) is older than the most
recent nominal-cron slot <= now. No receipt ever = due. The registered
maintenance prompt calls `due_jobs()` and executes what it returns, in order —
it never decides due-ness from the prompt (the Bug #99 hand-rolled-arithmetic
class). "Due since last receipt" also beats fixed crons on reliability: a
laptop closed through Sunday evening means cleanup is still due at Monday
6:45 and runs then — missed work self-heals instead of skipping a cycle.

FAILURE CONTAINMENT = JOBS STAY DUE
-----------------------------------
Each job's success criterion is its OWN existing receipt validator
(validate_reconcile_ran, validate_sweep_ran, cleanup_run, the insights
pack_run, the report events) — the dispatcher never vouches for a job. A job
that fails or gets cut off writes no receipt, so it is still due at the next
fire: self-healing by construction. The dispatcher's own `maintenance_run`
audit event records jobs_due / jobs_completed / jobs_failed so the watchdog
and cleanup's Monday note can surface chronic failures.

ORDER IS THE CONTRACT
---------------------
`MAINTENANCE_JOBS` insertion order is execution order and is load-bearing:
reconcile-sent runs FIRST at the 6:45 slot so the 7:00 morning brief reads an
already-reconciled substrate (Bug #98-v3's original reason for the 6:45
anchor), and weekly-insights runs AFTER cleanup (synthesis wants a settled
substrate). Never parallelize the jobs.

A SKIPPED RECEIPT IS VOIDED BY THE CONFIG CHANGE THAT REMOVES ITS REASON
------------------------------------------------------------------------
(SPEC BRIEFFIX1 Item D, 2026-08-09)

The uniform rule above treats every receipt alike: a receipt after the slot
means the slot was served. That is right for a COMPLETED run and wrong for a
SKIPPED one, because a skip is not work — it is a recorded answer to a
question the workspace was asked at that moment ("is a chat backend
declared?"), and a config change can make that answer obsolete before the next
slot arrives.

Lived on 2026-08-09: the chat leg skipped at 22:05 with "no chat backend is
declared", the user declared one at 22:15, and the leg stayed inert until
Monday 06:45 — a whole weekend of chat closures nobody was watching for —
because Friday's skip receipt was serving Friday's slot. Nothing was broken;
the rule simply could not see that the reason had evaporated.

So a job may declare `voided_by`: event shapes whose appearance AFTER a
skipped receipt voids it for dueness. COMPLETED receipts serve their slot
unconditionally — a config change never re-runs finished work. It lives on the
registry ROW rather than in the due function so the next config-skipped leg
inherits the behaviour by declaring one line, instead of by someone
remembering this paragraph.

PARTITIONED JOBS PROCESS EVERY MISSED PERIOD (CATCHUP1, 2026-07-28)
-------------------------------------------------------------------
Due-ness above answers "should this job run"; for most jobs that is the whole
question, because their work is a current-state pass or a cursor-driven span
that self-heals whenever it next runs. A `partitioned` job is different: each
PERIOD is its own deliverable under its own label. Miss the 1st of August and
the September fire produces August's report — and July's is lost forever,
because "the previous full calendar month" is measured from `now` and the
job's due rule (`expected_fires(count=1)`) structurally cannot see that two
periods went unserved. So a partitioned job's due dict additionally carries
`periods` — every unserved nominal slot since its last receipt, oldest first
(`catchup.missed_periods`) — and the registered prompt produces ONE
deliverable per entry. Non-partitioned jobs are untouched: no `periods` key,
same dict they have always returned.

THE MIN-GAP GUARD — FIRST-FIRE-WINS BY CONSTRUCTION (MAINTGAP1, 2026-08-27;
CLOSED FOR REAL BY FIREGAP2, 2026-08-28)
----------------------------------------------------------------------------
CAPSLOT1's cross-product outer cron fires the `maintenance` task in :30/:45
PAIRS 15 minutes apart (M's Option A ruling accepted the pairs' side effect
of moving session-sweep to 6:30 and the Sunday family to 17:30 as design —
see the schedule_config.py comment on this task's row). REVIEW CAPSLOT1's F2
flagged the pair-overlap risk this creates: receipts land at job
COMPLETION, so a :30 fire's job chain still in flight when the :45 fire
reads receipts 15 minutes later is indistinguishable from an unserved one,
and every not-yet-receipted job — including `--apply` jobs — would be listed
due again by the :45 fire, worst case on the Sunday 7-job family, EVERY
WEEK. `dispatch_plan`'s guard: a `scheduled` fire whose PREDECESSOR
maintenance fire's own START landed fewer than `task_watchdog.MIN_GAP_MINUTES`
(20) minutes before THIS fire's own slot dispatches NOTHING — due-ness is
never even evaluated for a single job. `_check_min_gap` is the check; it
runs BEFORE due-ness, right after the PATHREPAIR1 root check, so it is a
true zero-cost early exit. The :30/:45 pairs thereby get first-fire-wins
semantics: the :45 twin of any pair is a guaranteed, receipted no-op
(`skipped_min_gap: true` on its `maintenance_run` receipt).

THE FIRE-START MARKER (SPEC_FIREGAP2, 2026-08-28 — "the guard sees fires
START, not only fires that have already finished"). MAINTGAP1's first cut
could only read the predecessor's own `maintenance_run` receipt
(`fired_at_slot` on that receipt — the fire-start side of a COMPLETED fire,
since the receipt's own append timestamp reflects completion, not the
nominal instant that triggered it), and that receipt is written at
COMPLETION — the empty-due exit or step 4 — AFTER the fire's job chain
finishes. So a receipt-LESS predecessor still IN FLIGHT (a :30 chain still
mid-run when the :45 dispatcher reads receipts) was invisible to the guard
by construction: REVIEW_MAINTGAP1_2026-08-27.md F1 named this precisely —
"no detector confined to the existing receipts can see that state" — and
left it for the maintainer, exactly the "commission a fire-START marker"
option that finding's own closing line offered.

SPEC_FIREGAP2 §0 ruling 1 is that marker. Every SCHEDULED maintenance fire
that actually clears both the root-repair gate and this same min-gap guard
writes a tiny start marker — `write_fire_start_marker`, one atomically
written, machine-stamped JSON sidecar at
`_hq/.system/maintenance_fire_start_marker.json` — BEFORE due-ness is ever
evaluated (the CRU walk ledger's own `_hq/.system/` sidecar discipline,
`eod_incremental.py`, imitated: one JSON, atomic write, best-effort, never
substrate). `_check_min_gap` reads MARKER-OR-RECEIPT, whichever names the
NEWER slot, as the predecessor: a :30 chain that has only just started
already has a marker naming its own slot, so the :45 fire sees it
immediately, from its first second — REVIEW MAINTGAP1 F1's window, closed,
not merely narrowed. The claim that the :30/:45 pair overlap is closed
MECHANICALLY, independent of how long the :30 twin's job chain actually
ran, is now true; this paragraph is what makes it true (SPEC_FIREGAP2,
retiring the caveat REVIEW_MAINTGAP1_2026-08-27.md F1 asked the maintainer
to resolve).

STALE MARKERS SELF-EXPIRE (SPEC_FIREGAP2 §0 ruling 3). A crashed :30 chain
must never wedge the cadence behind a marker no completion receipt will
ever follow: a start marker older than `FIRE_MARKER_STALE_MINUTES` (90)
reads as a DEAD chain, not an in-flight one, and the guard falls back to
the receipt-only predecessor exactly as MAINTGAP1 always did.

A marker is written ONLY once THIS fire has itself cleared the guard —
never by a fire the guard just skipped, or the "predecessor" would ratchet
forward by 15 minutes every pair, forever, and the guard could never
reopen — and ONLY for `scheduled` fires (SPEC_FIREGAP2 §0 ruling 2, closing
REVIEW MAINTGAP1 F2). F2 was a second, independent gap: predecessor
detection read ANY `maintenance_run` receipt regardless of `fired_via`, so
a manual Run Now press in the 6:30-6:44 window could receipt slot 6:30 and
suppress the 6:45 SCHEDULED reconcile legs until 12:30 — a manual fire
silently arming the guard against the very cadence it never asked to
gate. `_newest_fired_at_slot` now skips manual receipts when picking the
newest, and manual fires never write a start marker: the gap check
compares against the last SCHEDULED fire's start only, on both signals.
The manual fire ITSELF stays exempt from being refused (MAINTGAP1 §0.2,
kept, RUNNOW1 posture) — a person's press is never suppressed by this
guard either.

A machine with no marker file at all — a pre-FIREGAP2 install, or the very
first fire since upgrade — degrades to MAINTGAP1's original receipt-only
behavior byte-for-byte: the marker read returns None and `_check_min_gap`
falls straight through to the receipt comparison, exactly as it always
did. `fired_via: "manual"` (Run Now) stays EXEMPT unconditionally — the
RUNNOW1 posture: a person pressing the button is never refused by a
robot's spacing rule. A wake after missed slots is unaffected: the
predecessor's slot (from either signal) is whatever was last actually
served, which after any real sleep is always > MIN_GAP_MINUTES behind the
wake fire's own slot, so catch-up fires normally with no special-casing.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from schedule_config import DEFAULT_SCHEDULES, CronParseError  # noqa: E402
from task_watchdog import (  # noqa: E402
    MIN_GAP_MINUTES,
    _FIRE_GRACE,
    _now_local,
    _to_local_naive,
    expected_fires,
    expected_fires_multi,
    last_receipts,
)


# The job registry — insertion order IS execution order (see module docstring).
# `nominal_cron` is each job's own cadence, evaluated by the uniform due rule;
# the task's actual fire slots come from DEFAULT_SCHEDULES["maintenance"].
# `skill` names what the registered prompt executes for the job (customer copy
# never shows these — job identity stays internal vocabulary).
MAINTENANCE_JOBS: dict[str, dict] = {
    # Weekday fires only (dow 1-5 in the nominal cron): due at every weekday
    # slot, exactly the pre-MAINT1 reconcile-sent cadence (SPEC-2.4).
    "reconcile-sent": {
        "skill": "reconcile-sent",
        "nominal_cron": "45 6,12,17 * * 1-5",
        "description": "close commitments completed by mail sent outside the product",
    },
    # CHATSCAN1 — the chat closure leg, ordered IMMEDIATELY BESIDE the mail
    # one and on the identical nominal cron. M's ruling 2026-08-06: closing
    # and review from chat are wired into the maintenance cadence exactly like
    # mail, first-class and registered, never an on-demand extra. Same slot as
    # `reconcile-sent` and ordered after it so both legs land before the 7:00
    # brief reads the substrate. It rides the already-authorized `maintenance`
    # taskId, so it adds ZERO scheduled tasks on any machine. A workspace with
    # no declared chat backend still runs it: the leg skips silently and
    # writes its skip receipt, which is what keeps "no chat backend" readable
    # apart from "swept and found nothing". Entry point:
    # chat_reconcile.reconcile_chat_and_receipt.
    "reconcile-chat": {
        "skill": "reconcile-sent (chat leg — shared/scripts/chat_reconcile.py)",
        "nominal_cron": "45 6,12,17 * * 1-5",
        "description": "close commitments discharged in the declared chat backend",
        # BRIEFFIX1 Item D — this leg's ONE skip reason is "no chat backend is
        # declared", and declaring one is exactly the event below. A skip
        # receipt written before that declaration is answering a question the
        # workspace has since answered differently, so it stops serving its
        # slot and the leg runs at the next fire instead of at the next slot.
        "voided_by": (
            {"type": "connector_backend_changed", "match": {"category": "chat"}},
        ),
        "voided_reason": ("its last run skipped because no chat backend was "
                          "declared, and one has been declared since"),
    },
    # SPEC EODSPEED1 (2026-08-26) — the End of Day's capture leg, run
    # INCREMENTALLY during the day so the 5 PM close finds the day's meetings
    # already captured and re-verifies instead of fetching. Ordered directly
    # after the two reconcile legs and on their identical weekday cron:
    # the 12:45 slot is the one that moves the day's fetching off the close
    # (the 6:45 slot picks up the prior evening; 17:45 drains stragglers for
    # the NEXT day's close). It rides the already-authorized `maintenance`
    # taskId, so it registers ZERO scheduled tasks on any machine — the
    # piggyback IS the design, and a new registration was explicitly the
    # thing to avoid (a fleet-wide never_authorized risk per release).
    #
    # The job executes the close's OWN Phase D contract, verbatim —
    # orchestrator-past-meetings.md Phases 3 → 4.8, same canonical writers,
    # same admission gates, no relaxed floors — under the EODSPEED1
    # incremental rules stated in that file: SILENT (writes and receipts
    # only; the close remains the one narrator), window from
    # `catchup_window("meeting-capture", floor_hours=24)`, receipt via
    # `eod_incremental.log_capture_pass_receipt` (a pack_run under THIS job
    # id — NEVER a receipt under `past-meetings`, which would arm
    # skip_render against the real close). A machine that never runs this
    # job loses nothing: the close's own window computation is untouched and
    # degrades to fetch-at-close exactly.
    # CAPSLOT1 (2026-08-27) — gained the 16:30 pre-close slot, weekdays only,
    # so the incremental capture leg's last chance to help the 17:00 close
    # moves from 12:45 to 16:30 (the EODSPEED1 live test measured a
    # structural 12:45->17:00 blind window: every workday had a 4h15m gap
    # in which meetings landed but nothing captured them ahead of the
    # close). The nominal cadence is a TUPLE of two clean cron strings
    # rather than one cron folding 16 into the same field as 6/12/17: the
    # sibling reconcile legs below keep their unchanged "45 6,12,17 * * 1-5"
    # cadence exactly, and a SINGLE expression covering all four hours
    # would need minute="30,45" — which, per standard cron field semantics,
    # cross-products EVERY listed hour against EVERY listed minute (6:30 and
    # 12:30 and 16:45 and 17:30 all becoming nominal slots nobody wants),
    # quadrupling this job's real fire count for zero benefit. The tuple
    # form keeps this job's own due-ness EXACT — read by
    # `task_watchdog.expected_fires_multi` (see its docstring), not by
    # `expected_fires`/`schedule_config.parse_cron` directly, both of which
    # only accept a single string. The outer `maintenance` TASK's own
    # registered cron (`schedule_config.DEFAULT_SCHEDULES["maintenance"]`)
    # is a different story — that one genuinely is a single string handed
    # to Cowork's registrar, and DOES cross-product (see its own comment).
    "meeting-capture": {
        "skill": ("end-of-day capture leg, incremental "
                  "(orchestrator-past-meetings.md Phase D under the "
                  "EODSPEED1 incremental rules; receipt via "
                  "eod_incremental.log_capture_pass_receipt)"),
        "nominal_cron": ("45 6,12,17 * * 1-5", "30 16 * * 1-5"),
        "description": ("capture the day's meetings as they land, so the "
                        "evening close reconciles instead of fetching"),
    },
    # Nominal midnight daily -> due once per day, served at the FIRST fire of
    # the day (6:45). Evening chats sweep the next morning, still BEFORE the
    # 7:00 brief, so the brief sees them.
    "session-sweep": {
        "skill": "session-sweep",
        "nominal_cron": "0 0 * * *",
        "description": "promote unlogged commitments and decisions from ad-hoc chats",
    },
    # Nominal Sunday 17:00 -> due at the Sunday 17:45 fire; still due Monday
    # 6:45 if the laptop was closed (the self-heal the old fixed cron lacked).
    "cleanup": {
        "skill": "cleanup",
        "nominal_cron": "0 17 * * 0",
        "description": "weekly workspace tidy + brain self-heal",
    },
    # Same Sunday slot as cleanup, ordered AFTER it: settled substrate first.
    "weekly-insights": {
        "skill": "insight-generator",
        "nominal_cron": "0 17 * * 0",
        "description": "recompute the analytical views from the settled week",
    },
    # SPEC_LEARN1 D6 — same Sunday slot, ordered AFTER weekly-insights and
    # BEFORE deal-signals: the substrate has settled by then, and Monday's
    # brief opens with the narration of what this job changed. Entry point:
    # `learning_pass.run_learning_job` — three automatic legs (voice block,
    # prep section weights, workspace exemplar), each proposed through the
    # auto rail, each with a registered reverser, each narrated once and
    # reversible with one word. Nothing here asks a question.
    "learning": {
        "skill": "learning pass (shared/scripts/learning_pass.py)",
        "nominal_cron": "0 17 * * 0",
        "description": "promote repeated corrections into behaviour: how I "
                       "write for you, which prep sections render, which "
                       "document shape is the standard",
    },
    # LB1 — same Sunday slot, ordered after insights: the deal-signal
    # detector proposes over the settled week's events, so Monday's card
    # (and the Monday 9 AM Staff Meeting, where registered) opens with a
    # fresh queue. Entry point: deal_signal_detector.run_deal_signal_job —
    # detection + brain_proposals.propose(tier="confirm") only; nothing
    # mutates a deal until the user confirms through apply-choices.
    "deal-signals": {
        "skill": "deal-signal detector (shared/scripts/deal_signal_detector.py)",
        "nominal_cron": "0 17 * * 0",
        "description": "propose observed deal stage/value/creation changes for confirmation",
    },
    # PID1 D7 — the identity reconciler: same Sunday slot, ordered AFTER
    # deal-signals (settled substrate first; Monday's Staff Meeting opens
    # with a fresh, clustered identity queue). Entry point:
    # identity_reconcile.run_identity_reconcile(workspace_root, apply=True,
    # caps=STEADY_CAPS) — auto-adds ride the R1 rail (narrated + batch-
    # undoable), links/merges are propose-only, caps spill narrated.
    "identity-reconcile": {
        "skill": "identity reconciler (shared/scripts/identity_reconcile.py "
                 "--apply — dry-run without the flag)",
        "nominal_cron": "0 17 * * 0",
        "description": "reconcile person identities: auto-add corroborated "
                       "people, link/merge-propose the rest for review",
    },
    # LIFECYCLE1 — the project lifecycle pass, same Sunday slot, ordered LAST
    # of the Sunday group: it reads the settled week AND it reads the expiry
    # tombstones `cleanup`'s `brain_proposals.expire_stale` sweep writes
    # earlier in this same fire (an ask that expired unanswered is the
    # precondition for the active->dormant flip, so running before cleanup
    # would delay every flip by a week). Entry point:
    # lifecycle_pass.run_lifecycle_pass(workspace_root, apply=True) — dormancy
    # asks ride the LB2 confirm rail (on-demand rows, never a scheduled
    # surface), and the dormant->archived leg goes through
    # thread_archive.archive_thread, THE archive chokepoint.
    "lifecycle": {
        "skill": "project lifecycle pass (shared/scripts/lifecycle_pass.py "
                 "--apply — dry-run without the flag)",
        "nominal_cron": "0 17 * * 0",
        "description": "ask about projects gone quiet; retire and revive the "
                       "ones the lifecycle rules already decided",
    },
    # SCHEDVIEW1 5.2 — THE DEDUP APPLY. The capture gate STAMPS an auto-merge
    # (`commitment_dedup.flag_suspected_duplicates`) and structurally cannot
    # apply it there: the new event is not on disk yet. Until this row the
    # apply half had exactly one caller in the whole tree — the commitments
    # scheduled chat — so on a seat where that chat is paused (the shipped
    # posture since FOLD1-A) a stamped duplicate sat open beside the row it
    # was stamped a merge of, indefinitely (attended test v5.31.0, B1.1).
    #
    # FIRST OF THE LEGS THAT JUDGE ROWS, ahead of `review-expiry`, and the
    # order is the contract for one reason: a merge is an IDENTITY operation
    # — it says two rows are one promise — and every leg behind it argues
    # ABOUT rows. A drain that reaches a duplicate before the merge does
    # judges one promise twice in one fire. It is NOT first of the daily legs
    # and should not be: the CAPTURE legs above it (`meeting-capture`,
    # `session-sweep`) run earlier, so a duplicate captured in this same fire
    # is stamped before this leg reaches it. The pinned ordering contract of the legs that follow
    # (review-expiry -> question-expiry -> calendar-close -> exit-doors ->
    # binding-gauge) is unchanged; this row sits in front of all of it.
    #
    # DAILY, nominal midnight -> due once a day, served at the day's FIRST
    # fire (6:45), before the 7:00 brief reads the plate.
    #
    # QUIET-RUN SEMANTICS, the binding-gauge posture: a fire with nothing
    # stamped writes nothing and receipts nothing, so the job stays due and
    # re-derives at the next fire for the price of one open-set read. The
    # dead-fire fallback (`commitment_dedup.has_stale_auto_merge_stamp`,
    # which makes CAPTURE go back to asking when a stamp ages out unapplied)
    # is untouched and stays the second line of defence — this job is what
    # should stop it ever firing, not a replacement for it.
    #
    # It rides the already-authorized `maintenance` taskId, so it registers
    # ZERO scheduled tasks on any machine, and a job id is not a
    # DEFAULT_SCHEDULES key, so `load_schedule_config`'s renamed-predecessor
    # carry-over cannot reach it (the 2026-08-19 seam). Entry point:
    # `commitment_dedup.run_dedup_apply_job(ws, apply=True)`.
    "dedup-apply": {
        "skill": "duplicate merge apply (shared/scripts/commitment_dedup.py "
                 "--workspace <root> --apply — dry-run without --apply; the "
                 "script writes its own pack_run receipt on a run that "
                 "merged something, leaves NO trace on a run with nothing "
                 "stamped, and surfaces NOTHING to the CEO)",
        "nominal_cron": "0 0 * * *",
        "description": "collapse a capture the gate already decided is the "
                       "same promise as an open row (reversible, one batch)",
    },
    # REVSCHED1 §3-2 / UNCONFEXP1 — the unconfirmed-pile drain. Ordered after
    # every substrate-writing leg above it, and the order is the contract for
    # a specific reason: this job argues FROM SILENCE, so it must read the
    # substrate the rest of the fire has already finished writing. A row that
    # a reconcile leg or the session sweep touched twenty seconds earlier has
    # moved, and moving is exactly what should keep it out of a lapse.
    #
    # DAILY since UNCONFEXP1 (M's ruling, 2026-08-30: an unconfirmed
    # extraction "can nag for like a day or two", then it closes out —
    # superseding REVSCHED1's weekly cadence, which fit the old 14-day bar).
    # With `UNCONFIRMED_NAG_DAYS = 2` a weekly pass would let a lapsed guess
    # keep nagging up to 8 days; nominal midnight -> due once per day, served
    # at the day's FIRST fire (6:45), before the 7:00 brief — so the brief's
    # CHANGED line can disclose what lapsed the same morning (change_feed).
    # It is the FIRST of the silence-drains, which is what makes the rest of
    # the fire safe to order behind it (insertion order is the contract). Its
    # old Sunday sibling `age-out` is gone — R1, 2026-09-13 — and that changes
    # nothing here: this drain's place was never defined by what followed it.
    #
    # It rides the already-authorized `maintenance` taskId, so it registers
    # ZERO scheduled tasks on any machine — and, because a job id is not a
    # DEFAULT_SCHEDULES key, it cannot inherit a retired predecessor's
    # `enabled: false` through `load_schedule_config`'s renamed-predecessor
    # override carry-over (the seam that silently disabled the end-of-day task
    # on 2026-08-19). Entry point:
    # `commitment_backlog_sweep.run_review_expiry_job(ws, apply=True)`.
    "review-expiry": {
        "skill": "commitment-backlog-sweep review amnesty "
                 "(shared/scripts/commitment_backlog_sweep.py review-expiry "
                 "--apply — dry-run without the flag)",
        "nominal_cron": "0 0 * * *",
        "description": "lapse unconfirmed captures nobody answered inside the "
                       "review window (reversible, one batch)",
    },
    # TTL1 (SPEC_FLOW1 Lane G, 2026-09-07) — THE QUESTION-EXPIRY ENGINE.
    #
    # Ordered AFTER `review-expiry` and BEFORE `calendar-close`, on the same
    # daily cron as the first. It used to sit between `review-expiry` and
    # SWEEPSCHED1's `age-out`; R1 (M, 2026-09-13) deleted that job, and this
    # engine keeps its place and its reason unchanged.
    #
    # AFTER `review-expiry`: the review lapse owns the capture-card rows with
    # no likely answer (the let-go half), so it must finish before this engine
    # reads what is left. Everything this engine then settles — the four-day
    # chip, the seven-day deal signal, the fourteen-day housekeeping row, and
    # (post-IDENT1) the fourteen-day identity question — resolves to its
    # class's default under ONE batch id with ONE `undo`.
    #
    # BEFORE THE CLOSERS: with SWEEPSCHED1 retired this is the LAST leg in
    # the fire that argues from silence, and the two that remain run in BAR
    # ORDER — review-expiry's two days, then this engine's two to fourteen.
    # The rule the deleted job wrote for its own place survives as the reason
    # this one keeps its: "a row it lapses is one this job then has no
    # business looking at". It applies here word for word, because the
    # proposal class's default runs
    # `commitment_policy_pass.resolve_stale_chips`, which CLOSES commitment
    # rows through `commitment_state.close_commitment` — a row this engine
    # closes today is a row the evidence closers below must not then re-read
    # as untouched.
    #
    # DAILY, not weekly, for the same reason UNCONFEXP1 moved the review
    # drain: the shortest lifetime in the registry is two days, and a weekly
    # pass would let a question sit for its window plus most of a week.
    #
    # It rides the already-authorized `maintenance` taskId, so it registers
    # ZERO scheduled tasks on any machine, and a job id is not a
    # DEFAULT_SCHEDULES key, so `load_schedule_config`'s renamed-predecessor
    # carry-over cannot reach it. Entry point:
    # `question_ttl.run_question_expiry(ws, apply=True)`; the per-workspace
    # switch `questions.expire_to_default` turns it off by word (default ON,
    # fail-to-default), in which case the run is a no-op with a receipt line
    # that says so.
    #
    # DUENESS reads the engine's OWN receipt — `question_expiry_run`,
    # registered in `receipts.py` (CANONICAL_TASK_IDS, RECEIPT_TYPES and
    # `_TYPE_IMPLIES_TASK`, one writer) and therefore derived into
    # `task_watchdog.RECEIPT_SPECS`. It is written ONLY on a run that settled
    # something, the GAUGEJOB1 quiet-run posture deliberately: a day on which
    # no question was old enough leaves no trace, so the job stays due and
    # re-derives at the next fire (cheap), rather than banking a ledger row
    # that says a run happened when nothing did.
    "question-expiry": {
        "skill": "question expiry (shared/scripts/question_ttl.py --apply "
                 "— dry-run without the flag)",
        "nominal_cron": "0 0 * * *",
        "description": "let every question that nobody answered settle to its "
                       "default (reversible, one batch a run)",
    },
    # R1 (M, 2026-09-13) — THE `age-out` JOB IS GONE. SWEEPSCHED1's 30-day
    # confirmed-pile drain used to sit here, between `question-expiry` and
    # `calendar-close`. It closed 48 agreed rows and parked 51 more on one
    # unattended Sunday fire at a bar nobody had ruled, while night 10 had
    # already ruled the silence door at 45 rest / 60 let-go. M's ruling is
    # one sentence: EXIT1's `exit-doors` leg below is THE ONLY SILENCE DOOR
    # in the product. Three silence bars live at once was the defect; one
    # is the fix.
    #
    # The id is NOT erased from history: `receipts.CANONICAL_TASK_IDS`,
    # `event_types.MACHINE_SOURCE_SKILLS` and `schedule_config.DISPLAY_NAMES`
    # all keep their `age-out` rows forever, exactly as `pulse` keeps its
    # row after LIFECYCLE1 retired that chat — receipts already on disk have
    # to keep parsing and keep reading as English. What is gone is the job
    # that WRITES them. `release_actions.retire_age_out_job` switches it off
    # on every seat that still carries it in `schedule_config`.
    #
    # The ORDERING CONTRACT is therefore now, in registry order:
    #   review-expiry -> question-expiry -> calendar-close -> exit-doors
    #   -> binding-gauge
    # and the reasoning the deleted rows carried survives unchanged in the
    # rows that remain: the drains that argue from SILENCE run in bar order
    # and before the closers that argue from EVIDENCE.
    # POLICY1-B DD-7 — the CALENDAR CLOSER for `scheduling` rows (M ruling
    # 2, 2026-09-03: scheduling rows book silently and the calendar closer
    # finishes them). DAILY, ordered AFTER `review-expiry` and
    # `question-expiry` (both drains argue from silence; this closer argues
    # from EVIDENCE and runs once they are done). Confirm-first for its first
    # three fires (it plans, closes nothing, leaves the offer as its receipt
    # line), then closes on one run batch with a group per meeting. It reads
    # the book AND the observed tier (ATTRIB1-B diverts undated scheduling
    # rows there). Entry point:
    # `calendar_close.run_calendar_close_job(ws, apply=True)`.
    "calendar-close": {
        "skill": "calendar closer for scheduling rows "
                 "(shared/scripts/calendar_close.py --workspace <root> "
                 "--apply — dry-run without the flag)",
        "nominal_cron": "0 0 * * *",  # daily at the day's first fire, beside review-expiry and binding-gauge (the dispatcher serves by slot, so the spec's :15 offset had no meaning and broke the midnight-served fixture convention)
        "description": "close a scheduling item once the meeting it was "
                       "about has happened with the other side in the room "
                       "(reversible, one batch; proposes before it acts)",
    },
    # EXIT1 (SPEC_FLOW1 Lane B) — the two exit rails a SCHEDULE can own.
    #
    # DAILY, ordered immediately AFTER `calendar-close` and before the gauge,
    # which keeps the registry's standing shape exactly: every writing leg
    # first, the measurement last. It is the last writing leg because it is
    # the one that argues from silence about rows the earlier legs may have
    # just closed — a row the calendar closer finishes in this same fire must
    # not also be rested by this one for having been quiet.
    #
    # TWO of the three routes run here. The paid-or-signed leg closes a row
    # whose agreement was signed or whose invoice was paid; the silence leg
    # rests a row nobody has touched in six weeks and lets go one nobody has
    # touched in two months, one batch and one `undo` each. The CALENDAR leg
    # is deliberately NOT run from here — `calendar-close` is its own job in
    # this same fire, and its probation counts its own receipts, so firing it
    # twice per fire would spend two of a row's three offers in one pass
    # (`exit_doors.apply_fact_closes(run_calendar=False)` is that fence).
    # The third route — closing on the customer's own word — cannot be a
    # scheduled job at all: it reads a transcript, and a transcript arrives
    # when a call is captured, not when a clock strikes.
    #
    # It rides the already-authorized `maintenance` taskId, so it registers
    # ZERO scheduled tasks on any machine. Entry point:
    # `exit_doors.run_exit_doors_job(ws, apply=True)`.
    "exit-doors": {
        "skill": "exit doors — paid-or-signed closes and the silence drain "
                 "(shared/scripts/exit_doors.py --workspace <root> --apply "
                 "— dry-run without the flag)",
        "nominal_cron": "0 0 * * *",
        "description": "close what the record shows finished, rest what "
                       "nobody has touched in six weeks and let go what "
                       "nobody has touched in two months (reversible, one "
                       "batch each)",
    },
    # GAUGEJOB1 (memory program R1 prerequisite) — the binding-gauge refresh.
    # READER1 shipped the writer (`binding_gauge.write_gauge`) and the reader
    # (`load_thread_knowledge._load_gauge`) with nothing running the writer on
    # a schedule, so every reader saw the honest "unmeasured" forever. This
    # job is the missing cadence.
    #
    # DAILY, nominal midnight -> due once per day, served at the day's FIRST
    # fire (6:45), and ordered LAST of the substrate-facing legs — after BOTH
    # silence-drains — deliberately: the gauge is a pure MEASUREMENT (it
    # writes only its own `_hq/data/binding_gauge.json` sidecar artifact,
    # never substrate), so it must read the substrate every writing leg of
    # this same fire has already finished with, and it stamps the
    # post-drain events high-water mark (`events_max_seq`) — which is what
    # keeps the reader's stale_substrate flag honest for the rest of the day.
    # Daily is affordable: measured 2026-08-31 at live scale (~13k events, 43
    # threads) a full build_gauge pass — RECL1 fold included — runs ~2.5s.
    # The artifact's own staleness detection covers intra-day drift between
    # runs.
    #
    # Inserted BEFORE monthly-report so the silence-drains keep their pinned
    # "review-expiry then question-expiry" ordering contract intact, and the
    # monthly reporting leg stays the registry's caboose. (This row was
    # written when `review-expiry` and SWEEPSCHED1's `age-out` were an
    # adjacent pair; TTL1 put `question-expiry` between them in 2026-09-07 and
    # R1 deleted `age-out` in 2026-09-13. Nothing here changes: the gauge
    # still runs after every writing leg of the fire, silence-drains
    # included, which is the only property this row depends on.)
    #
    # It rides the already-authorized `maintenance` taskId, so it registers
    # ZERO scheduled tasks on any machine, and a job id is not a
    # DEFAULT_SCHEDULES key, so `load_schedule_config`'s renamed-predecessor
    # carry-over cannot reach it (the 2026-08-19 seam). The registered prompt
    # is UNTOUCHED: this row's `skill` string carries the complete invocation,
    # and the prompt's step 2 executes each due job's skill in plan order —
    # the script writes its own pack_run receipt on a CHANGE run and leaves
    # NO trace on a quiet one (binding_gauge's QUIET-RUN SEMANTICS note: a
    # quiet receipt would advance the very high-water mark whose standstill
    # made the run quiet, so on a zero-movement day the job simply stays due
    # and quietly exits at each fire — a ~2s accepted trade), and it never
    # says anything to the CEO. Entry point:
    # `binding_gauge.run_gauge_refresh_job(ws, apply=True)`.
    "binding-gauge": {
        "skill": "binding-gauge refresh (shared/scripts/binding_gauge.py "
                 "<workspace_root> --job --apply — dry-run without --apply; "
                 "the script writes its own pack_run receipt on a change "
                 "run, leaves no trace on a quiet one, and surfaces NOTHING "
                 "to the CEO — its verdicts reach surfaces through "
                 "load_thread_knowledge)",
        "nominal_cron": "0 0 * * *",
        "description": "re-measure per-project binding trust so memory "
                       "surfaces read a fresh gauge instead of a stale one",
    },
    # MEASURE1 (SPEC_FLOW1 Lane D) — the daily FLOW MEASURE: rows in, rows
    # out by route, the open plate and its pages, written as one
    # `flow_measure` ledger row per COMPLETE workspace-local day. Night 10
    # rebuilds the intake door, the exit door and the plate on one claim —
    # the plate shrinks — and nothing on any book records whether it did.
    # This job is that record.
    #
    # DAILY, nominal midnight -> due once a day, served at the day's FIRST
    # fire (6:45), and ordered IMMEDIATELY AFTER `binding-gauge` for the
    # same reason the gauge sits where it does and one more besides. Like
    # the gauge it is a pure MEASUREMENT — it writes only its own
    # `flow_measure` rows and its own receipt, never substrate — so it must
    # read the substrate every writing leg of this same fire has already
    # finished with. And it must run after the gauge specifically, because
    # the gauge's own receipt is a ledger event: measuring before it would
    # count the fire's own bookkeeping into a different day than the gauge
    # stamps. It stays BEFORE `monthly-report`, which remains the registry's
    # caboose.
    #
    # HEALTH1 (2026-09-07) moved the health block off the morning brief and
    # onto the maintenance run's weekly note; this job is the DAILY sibling
    # of that move — the measure belongs to the maintenance run's own job
    # registry, beside the health work, and never to a customer surface. Its
    # one customer-facing sentence is the weekly wrap's line, composed by
    # `flow_measure.wrap_line`, and nothing on the brief or the day-close
    # says a word about it.
    #
    # FOLD1-A SAFE BY CONSTRUCTION. When maintenance starts firing three to
    # four times a day, every fire after the first finds every complete day
    # already carrying its row: the job writes NOTHING, receipts NOTHING and
    # stays due, which costs a few milliseconds and cannot produce four
    # contradicting rows for one date. There is no superseding row and none
    # is needed — a closed day's input does not change on the clock, and
    # rewriting an old row is exactly what LEDGERFENCE1 forbids.
    #
    # It rides the already-authorized `maintenance` taskId, so it registers
    # ZERO scheduled tasks on any machine, and a job id is not a
    # DEFAULT_SCHEDULES key, so `load_schedule_config`'s renamed-predecessor
    # carry-over cannot reach it (the 2026-08-19 seam). Entry point:
    # `flow_measure.run_measure_job(ws, apply=True)`.
    "daily-measure": {
        "skill": ("daily flow measure (shared/scripts/flow_measure.py "
                  "<workspace_root> --apply — dry-run without --apply; the "
                  "script writes one flow_measure row per complete day plus "
                  "its own pack_run receipt, leaves NO trace on a fire with "
                  "nothing new to measure, and surfaces NOTHING to the CEO — "
                  "its one sentence reaches the weekly wrap through "
                  "flow_measure.wrap_line)"),
        "nominal_cron": "0 0 * * *",
        "description": "count what came in, what went out and by which door, "
                       "so the plate's shrinking is a measurement and not a "
                       "claim",
    },
    # Nominal midnight on the 1st -> due at the first fire on/after the 1st.
    # PARTITIONED (CATCHUP1 F-3): one report per missed month, each labelled
    # with its own month. A machine closed across a 1st loses that month
    # entirely without this — the next fire produces the NEWEST prior month
    # and the skipped one is never written.
    # BOOT3 (2026-09-19) — the DST re-projection. A scheduled chat in the
    # merged Claude app is registered with a UTC cron, which is a fixed instant
    # of the day; the customer's 7 AM is not. Without this job the morning brief
    # fires at 6 AM all winter, silently, until somebody touches the schedule.
    # This leg compares the offset stored on each trigger with the offset the
    # workspace's timezone is actually on and PLANS one update per drifted
    # chat; the maintenance fire executes the plans. A quiet job by design: on
    # the ~363 days a year when nothing has shifted it finds nothing, writes
    # nothing and says nothing. On the two that have, it writes
    # `schedule_refreshed(field="cron_utc")` and NOT `schedule_config_changed`,
    # because the customer did not move anything — the clocks did. Entry point:
    # `schedule_backend.plan_realign`.
    "schedule-realign": {
        "skill": "schedule realign (shared/scripts/schedule_backend.py"
                 "::plan_realign — plans only, the fire executes)",
        "nominal_cron": "0 0 * * *",
        "description": "keep each scheduled chat on the time you picked when "
                       "the clocks change",
    },
    "monthly-report": {
        "skill": "operator-report + value-receipt",
        "nominal_cron": "0 0 1 * *",
        "description": "monthly operating report + value receipt for the prior month",
        "partitioned": True,
    },
}

# OPT-IN jobs (SPEC OUT7). A separate registry from MAINTENANCE_JOBS so the
# silent-job execution ORDER above stays the load-bearing contract it is (the
# order pin never sees these). An optional job is NEVER due unless the
# workspace explicitly opted it in — `schedule_config.maintenance_jobs.<id> =
# {"enabled": true}`, written only after a propose-and-confirm through
# enable-command-room-schedules. This is the "never auto-registered" posture in
# code: the job rides inside the already-authorized `maintenance` task (zero
# client Run-Now), but it stays inert until the user turns it on, and its own
# pack_run receipt self-limits it to its nominal cadence thereafter. Due
# optional jobs run AFTER the core jobs.
OPTIONAL_JOBS: dict[str, dict] = {
    # The monthly KPI scorecard (SPEC OUT7 §3c route 2). Renders the prior
    # month's scorecard via scorecard.py through the board-pack render path;
    # writes a pack_run receipt tagged monthly-scorecard so the uniform due
    # rule self-limits it to once a month. Opt-in ONLY.
    "monthly-scorecard": {
        "skill": "scorecard (shared/scripts/scorecard.py) via the board-pack render path",
        "nominal_cron": "0 0 1 * *",
        "description": "monthly KPI scorecard for the prior month (opt-in)",
        "opt_in": True,
        # Same class as monthly-report (CATCHUP1 F-4): a scorecard IS its
        # month. One per missed period, never one standing in for several.
        "partitioned": True,
    },
}

MAINTENANCE_TASK_ID = "maintenance"
RECEIPT_EVENT_TYPE = "maintenance_run"

# CHATSCAN1 V1b — THE CLOSURE ROSTER.
#
# The set of legs a maintenance fire is expected to close commitments through.
# Declared HERE, separately from MAINTENANCE_JOBS, and that separation is the
# whole mechanism: if a leg were only ever "expected" because it appeared in
# the job registry, then deleting it from the registry would delete the
# expectation too, and a fire that reconciled mail and not chat would report
# a clean, complete run. The roster is the independent statement of what
# SHOULD be there, so a missing leg reads as a GAP instead of a smaller plan.
#
# A gap is not an error — a workspace can legitimately disable a leg — but it
# is never invisible: it lands on the receipt as `roster_gap`, and
# `validate_maintenance_ran` refuses to call such a run complete.
RECONCILE_LEGS: tuple = ("reconcile-sent", "reconcile-chat")


def roster_gap(jobs=None) -> list:
    """Which closure legs the registry is MISSING, in roster order.

    `jobs` defaults to the live `MAINTENANCE_JOBS` keys. A leg the workspace
    explicitly disabled is NOT a gap — it is a registered leg that was turned
    off, which `skipped_disabled` already records honestly. A gap means the
    leg is not in the roster's registry at all: nothing will run it, nothing
    will report it, and without this check nothing would say so."""
    registered = set(jobs if jobs is not None else MAINTENANCE_JOBS.keys())
    return [leg for leg in RECONCILE_LEGS if leg not in registered]


def _all_job_ids() -> list[str]:
    """Every job id whose receipts drive due-ness — core plus optional."""
    return list(MAINTENANCE_JOBS) + list(OPTIONAL_JOBS)


def _optin_enabled(overrides: dict, job_id: str) -> bool:
    """An opt-in job is due-eligible ONLY when the workspace turned it on
    (`{"enabled": true}` in schedule_config.maintenance_jobs). Absent /
    malformed / anything but an explicit True = not opted in (never auto)."""
    ov = overrides.get(job_id)
    return isinstance(ov, dict) and ov.get("enabled") is True


def _job_overrides(workspace_root) -> dict:
    """Job-level overrides from entities.json
    `workspace.schedule_config.maintenance_jobs.<job_id>` — change-schedule can
    pause ONE job (`{"enabled": false}`) without touching the task. Defensive:
    missing/corrupt config means no overrides."""
    # SPEC SYNC1 B1 — route the entities.json read through the (dormant)
    # resolver; byte-identical to `_hq/data/entities.json` with no override.
    try:
        from data_root import resolve as _resolve_data_root
        path = _resolve_data_root(workspace_root) / "entities.json"
    except Exception:
        path = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    sc = ws.get("schedule_config") if isinstance(ws.get("schedule_config"), dict) else {}
    mj = sc.get("maintenance_jobs")
    return mj if isinstance(mj, dict) else {}


SKIPPED_STATUS = "skipped"


def _newest_receipt(workspace_root, job_id) -> tuple:
    """`(status, machine-local naive dt)` for this job's newest RECEIPT, or
    `(None, None)`.

    Deliberately returns the receipt's own instant rather than reusing the
    dueness `last` (review F11). `task_watchdog.last_receipts` takes the newer
    of {receipt, analytical-view mtime} for jobs that declare `views`, so
    `last` is not always a receipt timestamp — and comparing a voiding EVENT
    against a view's file mtime while reading the status from a receipt would
    be two clocks in one decision. No job declares both today; the divergence
    is one registry row away, and it would be invisible when it arrived.

    Best-effort like everything else here: an unreadable substrate must never
    crash a fire, and "unknown" falls through to the unchanged uniform rule.
    """
    try:
        from receipts import iter_receipts

        rows = iter_receipts(workspace_root, task_ids=[job_id])
    except Exception:  # noqa: BLE001
        return None, None
    newest, newest_dt = None, None
    for r in rows or []:
        dt = r.get("dt")
        if dt is None:
            continue
        if newest_dt is None or dt > newest_dt:
            newest, newest_dt = r, dt
    if newest is None:
        return None, None
    status = newest.get("status")
    return (status if isinstance(status, str) else None,
            _to_local_naive(newest_dt))


def _voiding_event_after(workspace_root, specs, after: _dt.datetime) -> Optional[str]:
    """The newest event matching any of `specs` that is NEWER than `after`
    (machine-local naive), as an ISO string — or None.

    `specs` are the registry's own `voided_by` rows: `{"type": ...,
    "match": {<data key>: <value>}}`. Matching on the DATA payload rather than
    on the type alone is what keeps a mail-backend change from re-arming the
    chat leg — same event type, different category, unrelated fact.
    """
    try:
        import events_io
        from event_time import event_dt

        events = events_io.iter_events(workspace_root)
    except Exception:  # noqa: BLE001
        return None
    wanted = []
    for spec in specs or ():
        if not isinstance(spec, dict) or not spec.get("type"):
            continue
        wanted.append((spec["type"], dict(spec.get("match") or {})))
    if not wanted:
        return None
    newest = None
    try:
        for ev in events:
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if not any(etype == t and all(data.get(k) == v for k, v in m.items())
                       for t, m in wanted):
                continue
            dt = event_dt(ev)
            if dt is None:
                continue
            local = _to_local_naive(dt)
            if local is None or local <= after:
                continue
            if newest is None or local > newest:
                newest = local
    except Exception:  # noqa: BLE001
        return None
    return newest.isoformat() if newest else None


def _skip_rearm(workspace_root, job_id, spec, last: _dt.datetime) -> Optional[str]:
    """Is this job's served slot VOIDED because the config changed under a
    SKIPPED receipt? Returns the voiding event's ISO instant, or None.

    Two conditions, both required and both deliberate:
      * the newest receipt says `skipped` — a completed run serves its slot
        unconditionally, because a config change is not a reason to redo
        finished work;
      * a declared voiding event is NEWER than that receipt — the reason the
        run skipped is gone.

    "Newer than that receipt" means newer than the RECEIPT's own instant, not
    than the dueness `last` this function is handed: for a job declaring
    `views`, `last` can be an analytical-view mtime, and one decision must not
    straddle two clocks (review F11). `last` stays the parameter because it is
    the correct fallback when the receipt read fails.

    Only ever consulted for a job that declares `voided_by`, so the common
    path pays nothing.
    """
    specs = spec.get("voided_by")
    if not specs:
        return None
    status, receipt_dt = _newest_receipt(workspace_root, job_id)
    if status != SKIPPED_STATUS:
        return None
    return _voiding_event_after(workspace_root, specs, receipt_dt or last)


_MIN_GAP = _dt.timedelta(minutes=MIN_GAP_MINUTES)


def _coerce_now(now):
    """A `now` argument as a machine-local naive datetime.

    HEAL1 — the access layer hands a helper its arguments as JSON, so a
    merged seat's `plan run_helper` line can only pass an ISO STRING where a
    local caller passes a datetime. Every entry point that takes `now` reads
    it through here so the two callers cannot diverge. A datetime is handled
    exactly as before (this is the same two lines every caller already ran);
    an unparseable string degrades to the real clock rather than crashing a
    surface, the same direction as every other read in this module.
    """
    if now is None:
        return _now_local()
    if isinstance(now, str):
        try:
            now = _dt.datetime.fromisoformat(now.strip().replace("Z", "+00:00"))
        except ValueError:
            return _now_local()
    if getattr(now, "tzinfo", None) is not None:
        return _to_local_naive(now)
    return now


def _newest_fired_at_slot(workspace_root) -> Optional[_dt.datetime]:
    """MAINTGAP1 — the predecessor maintenance fire's own SLOT: the newest
    `maintenance_run` receipt's `fired_at_slot` field, machine-local naive.

    This is the FIRE-START side deliberately, never the receipt's own append
    timestamp: `maintenance_receipt` writes its event only after the fire's
    due jobs have all run, so the event's own `ts` reflects completion, not
    the nominal instant that triggered the fire. `fired_at_slot` is computed
    the same way for every fire regardless of how long job execution took
    (see `maintenance_receipt` below — same `expected_fires` call, same
    task cron), which is what makes gap math between two fires deterministic
    instead of a function of how slow one of them happened to run.

    FIREGAP2 §0 ruling 2 (closing REVIEW MAINTGAP1 F2) — a receipt whose
    `fired_via` is `manual` is SKIPPED when picking the newest: a Run Now
    press must never become the predecessor a scheduled fire's gap is
    measured against. A receipt with no `fired_via` field at all (written
    before MAINTGAP1 threaded it, or by a caller that never set it) counts
    AS scheduled — the CLI's own `--fired-via` default and the pre-MAINTGAP1
    receipt shape both already treated an unlabelled fire as scheduled, and
    this filter must not start silently discarding history that predates
    the field.

    Best-effort like every other substrate read in this module: no prior
    SCHEDULED `maintenance_run` receipt (first-ever fire, or every receipt on
    record is manual), an unreadable substrate, or a malformed/missing field
    all return None — the gap check degrades to 'not skipped', the safe
    direction (a fire that SHOULD dispatch never silently doesn't because a
    read failed).
    """
    try:
        from receipts import iter_receipts

        rows = iter_receipts(workspace_root, task_ids=[MAINTENANCE_TASK_ID])
    except Exception:  # noqa: BLE001
        return None
    newest_row, newest_dt = None, None
    for r in rows or []:
        dt = r.get("dt")
        if dt is None:
            continue
        raw = r.get("raw")
        data = raw.get("data") if isinstance(raw, dict) else None
        fired_via = data.get("fired_via") if isinstance(data, dict) else None
        if fired_via == "manual":
            continue  # FIREGAP2 ruling 2 — never a predecessor
        if newest_dt is None or dt > newest_dt:
            newest_row, newest_dt = r, dt
    if newest_row is None:
        return None
    raw = newest_row.get("raw")
    data = raw.get("data") if isinstance(raw, dict) else None
    slot_iso = data.get("fired_at_slot") if isinstance(data, dict) else None
    if not isinstance(slot_iso, str) or not slot_iso.strip():
        return None
    try:
        parsed = _dt.datetime.fromisoformat(slot_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _to_local_naive(parsed)


# ---------------------------------------------------------------------------
# FIREGAP2 — the fire-start marker (SPEC_FIREGAP2 §0 ruling 1)
# ---------------------------------------------------------------------------
#
# One JSON sidecar naming the last SCHEDULED fire's own slot, written BEFORE
# that fire's due-ness is ever evaluated — the CRU walk ledger's own
# `_hq/.system/` discipline (`eod_incremental.py`: one JSON, atomic write,
# machine-stamped, best-effort, bookkeeping about work already receipted
# through canonical writers, never substrate) imitated for a single-slot
# record rather than a keyed one, because there is only ever one "most
# recent fire in flight" to remember.

FIRE_MARKER_RELPATH = "_hq/.system/maintenance_fire_start_marker.json"

# How long a start marker stays honorable with no completion receipt to
# back it up (SPEC_FIREGAP2 §0 ruling 3). Past this, the chain it named is
# presumed dead, not merely slow — the guard must never wedge the cadence
# behind a crashed fire that will never write the receipt this marker was
# standing in for.
FIRE_MARKER_STALE_MINUTES = 90
_MARKER_STALE = _dt.timedelta(minutes=FIRE_MARKER_STALE_MINUTES)


def _marker_path(workspace_root) -> Path:
    return Path(workspace_root) / FIRE_MARKER_RELPATH


def write_fire_start_marker(workspace_root, *, now: _dt.datetime) -> dict:
    """FIREGAP2 §0 ruling 1 — stamp THIS scheduled fire's own slot before its
    due-ness is ever evaluated, so a chain still in flight is visible to the
    next fire's gap check from its first second rather than only at
    completion.

    Called from `dispatch_plan` exactly once, only after a `scheduled` fire
    has itself cleared both the root-repair gate and this same min-gap
    guard — never for a fire the guard just skipped (that would ratchet the
    recorded "predecessor" forward every 15 minutes forever and the guard
    could never reopen) and never for a `manual` fire (ruling 2 — only a
    SCHEDULED fire's start counts as a predecessor at all).

    `now` is this module's own testable clock thread (every other timestamp
    here takes the same parameter) — `started_at` is stamped from it, not
    the real wall clock, so a fixture that writes a marker and one that
    later reads it for staleness agree on what "90 minutes" means.

    Best-effort like every other write in this module: a marker that fails
    to persist costs the NEXT fire the sight-line improvement, never THIS
    one — due-ness evaluation proceeds unaffected, and the guard simply
    degrades to MAINTGAP1's original receipt-only behavior on the next
    fire. Returns `{"recorded": bool}`.

    NOT UNDER THE HELPER DOOR (MF-M2-2, 2026-09-20). HEAL1 put
    `maintenance_dispatcher:catch_up_plan` on `workspace_access
    .RUN_HELPER_ALLOWLIST`, whose one rule is that a helper reads or computes
    and never writes. That call reaches this function only through
    `dispatch_plan`'s `fired_via == "scheduled"` branch, which
    `catch_up_plan` never takes — but "never takes" there is a constant it
    passes, not a shape, and ORCH1's transitive write-scan is a static
    instrument built precisely because a writer two frames below an
    allow-listed name is invisible to an argument-level promise (review F-8).
    The layer stamps `CR_HELPER_DOOR` on every helper child process and that
    is the one condition under which this marker is refused. A real
    scheduled fire, a legacy seat's in-shell python and every in-process
    import write it exactly as before, byte for byte.
    """
    if os.environ.get("CR_HELPER_DOOR"):
        return {"recorded": False, "held": True, "reason": "helper_door"}
    try:
        task_cron = DEFAULT_SCHEDULES[MAINTENANCE_TASK_ID]["cron"]
        slots = expected_fires(task_cron, now=now, count=1)
        fired_at_slot = slots[0].isoformat() if slots else None
    except (KeyError, CronParseError):
        fired_at_slot = None
    if fired_at_slot is None:
        return {"recorded": False}
    payload = {"fired_at_slot": fired_at_slot, "started_at": now.isoformat()}
    try:
        from receipts import machine_fields

        payload.update(machine_fields())
    except Exception:  # noqa: BLE001 — a missing machine stamp never blocks
        pass                                       # the marker itself
    try:
        from atomic_write import atomic_write_json

        path = _marker_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)
        return {"recorded": True}
    except Exception:  # noqa: BLE001
        return {"recorded": False}


def _marker_predecessor_slot(workspace_root, *, now: _dt.datetime) -> Optional[_dt.datetime]:
    """FIREGAP2 §0 rulings 1 and 3 — the fire-start marker's own slot,
    honored only when it is not STALE.

    Returns None on: no marker file at all (a pre-FIREGAP2 machine, or the
    very first fire since upgrade — the ruling-1 degrade clause: absence
    means the guard falls back to MAINTGAP1's original receipt-only
    behavior, byte-identical), unparseable/malformed contents, or a marker
    whose `started_at` is more than `FIRE_MARKER_STALE_MINUTES` behind `now`
    (ruling 3 — a crashed chain, not an in-flight one; a genuinely slow
    chain that DID complete has its own receipt by then anyway, so nothing
    is lost by no longer trusting the marker past this point).
    """
    try:
        raw = _marker_path(workspace_root).read_text(encoding="utf-8")
        marker = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(marker, dict):
        return None
    slot_iso = marker.get("fired_at_slot")
    started_iso = marker.get("started_at")
    if not isinstance(slot_iso, str) or not isinstance(started_iso, str):
        return None
    try:
        slot = _dt.datetime.fromisoformat(slot_iso.replace("Z", "+00:00"))
        started = _dt.datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    slot = _to_local_naive(slot)
    started = _to_local_naive(started)
    if slot is None or started is None:
        return None
    if now - started > _MARKER_STALE:
        return None  # ruling 3 — stale-expired, a dead chain
    return slot


def _check_min_gap(workspace_root, *, now: _dt.datetime, fired_via: str) -> dict:
    """MAINTGAP1 §0 rulings 1-3, extended by SPEC_FIREGAP2 §0 — the
    fire-start gap guard.

    A `scheduled` fire whose predecessor maintenance fire's own START landed
    fewer than `MIN_GAP_MINUTES` before THIS fire's own slot dispatches
    NOTHING (ruling 1). "Predecessor's own start" is MARKER-OR-RECEIPT,
    whichever names the NEWER slot (FIREGAP2 §0 ruling 1): the fire-start
    marker (`_marker_predecessor_slot`, not stale-expired — ruling 3) when
    one exists, else the newest SCHEDULED `maintenance_run` receipt
    (`_newest_fired_at_slot`, which itself already excludes manual receipts
    — FIREGAP2 §0 ruling 2). A marker and a receipt for the SAME fire always
    agree on the slot value (both are `expected_fires(task_cron, ...)` over
    the fire's own clock), so "whichever is newer" only ever matters when
    the marker names a fire STILL in flight that has no receipt yet —
    exactly the case REVIEW_MAINTGAP1_2026-08-27.md F1 could not see.
    `fired_via == "manual"` is exempt unconditionally (ruling 2 — RUNNOW1).
    A catch-up wake needs no special-casing (ruling 3): the predecessor's
    slot is whatever was last actually served, which after a genuine sleep
    is always far more than MIN_GAP_MINUTES behind the wake fire's own
    slot.

    Returns {"skipped": bool, "gap_minutes": float|None, "predecessor_slot":
    iso|None, "this_slot": iso|None}. Never raises — every failure mode
    (unparseable task cron, no prior receipt, no marker, unreadable
    substrate) degrades to `skipped: False`, the same "never let a read
    failure silently refuse a fire that should run" direction as the rest
    of this module.
    """
    empty = {"skipped": False, "gap_minutes": None,
             "predecessor_slot": None, "this_slot": None}
    if fired_via == "manual":
        return dict(empty)
    try:
        task_cron = DEFAULT_SCHEDULES[MAINTENANCE_TASK_ID]["cron"]
        this_slots = expected_fires(task_cron, now=now, count=1)
    except (KeyError, CronParseError):
        return dict(empty)
    if not this_slots:
        return dict(empty)
    this_slot = this_slots[0]
    predecessor_slot = _newest_fired_at_slot(workspace_root)
    marker_slot = _marker_predecessor_slot(workspace_root, now=now)
    if marker_slot is not None and marker_slot >= this_slot:
        # SELF-REFERENCE GUARD, marker side only. A predecessor's marker, by
        # definition, names a STRICTLY EARLIER slot than this fire's own —
        # a marker naming THIS slot or later is not a distinct predecessor,
        # it is THIS fire's own start marker (written by an earlier call
        # within the same dispatch, e.g. a caller that invokes
        # `dispatch_plan`/`due_jobs` more than once for one `now`, as several
        # of this file's own callers and this module's test battery both
        # do). Without this, that marker would be read back as its own
        # predecessor with a zero-minute gap and self-skip forever.
        # Receipt-sourced candidates are NOT filtered this way — an equal-
        # slot RECEIPT genuinely means a distinct earlier fire already
        # served this exact slot, and skipping on that is MAINTGAP1's
        # original, unchanged behavior (a receipt is only ever written at
        # completion, so a fire cannot see its own receipt before it
        # finishes — no self-reference is possible on that side).
        marker_slot = None
    if marker_slot is not None and (
        predecessor_slot is None or marker_slot > predecessor_slot
    ):
        # FIREGAP2 §0 ruling 1 — the marker names a fire that started MORE
        # RECENTLY than the newest completed receipt on record, which can
        # only happen when that fire is still in flight (no receipt yet) or
        # its receipt simply hasn't been read here before the marker was —
        # either way, the marker is the truer "when did the predecessor
        # start" answer.
        predecessor_slot = marker_slot
    if predecessor_slot is None:
        out = dict(empty)
        out["this_slot"] = this_slot.isoformat()
        return out
    gap = this_slot - predecessor_slot
    gap_minutes = gap.total_seconds() / 60.0
    skipped = _dt.timedelta(0) <= gap < _MIN_GAP
    return {
        "skipped": skipped,
        "gap_minutes": gap_minutes,
        "predecessor_slot": predecessor_slot.isoformat(),
        "this_slot": this_slot.isoformat(),
    }


def _check_and_repair_root(workspace_root, *, now: Optional[_dt.datetime] = None,
                           machine: Optional[str] = None,
                           repair: bool = True) -> dict:
    """SPEC PATHREPAIR1 — the dispatch preamble's root-validation leg (ruling
    3: 'inside the maintenance task's dispatch preamble'), called from
    `dispatch_plan` BEFORE due-ness is ever evaluated (ruling 1: detection
    without repair is an alarm; repair without detection is a lottery — this
    is the code chokepoint that guarantees both run, every fire, regardless
    of whether the registered prompt remembers to ask).

    `machine` is an optional identity override, forwarded to
    `path_repair.fire_time_guard` — real callers never pass it (production
    wants the real per-machine identity); tests pass one explicitly so a
    multi-machine fixture never touches this box's own identity marker.

    Returns {"blocked": bool, "ok": bool, ...}. `blocked=True` means the
    caller must dispatch NOTHING this fire (the acceptance pin: 'dead root +
    no candidate -> alarm class emitted, dispatch skipped loudly').

    THE CHEAP CHECK (ruling 3): read this workspace's OWN registration
    record and ask whether its stored root still carries the marker. On a
    healthy workspace this costs one stat() call plus an idempotent
    fingerprint backfill for a config that has never carried one (so a
    FUTURE rename has evidence to repair against) — already-fingerprinted
    configs are untouched.

    ON A DEAD STORED ROOT: `path_repair.repair` is asked to repoint the
    stale self-reported value to `workspace_root` ITSELF via
    `trusted_candidate` — no filesystem scan, no guess. `workspace_root` is
    not a candidate this function is proposing; it is the directory this
    code is DEMONSTRABLY executing against (Cowork's bash discovery /
    CONTRACT.md Rule 22 already resolved and verified it before this script
    was ever invoked). Passing it corrects the stale self-reference.

    UNREPAIRABLE (repair() still refuses — e.g. the stored fingerprint
    disagrees with this live root) is loud by construction: `dispatch_plan`
    returns `due: []` plus this dict under `root_repair`, so ANY reader of
    the plan (the registered prompt, a test) sees an explicit refusal
    instead of an empty-due silence. `task_alarm.classify_dead_root`
    independently recomputes the SAME verdict for the next rendered
    surface (morning-brief / end-of-day / system-health) — the TASKALARM1-
    visible failure class ruling 1 requires, without this function ever
    writing a duplicate signal itself.

    A thin wrapper over `path_repair.fire_time_guard` — the SAME function
    the scheduled-task bootloader's fire-time leg calls (item 3 of the
    spec's work list), so there is exactly one place this decision is made.

    `repair=False` (HEAL1) ASKS THE SAME QUESTION AND ANSWERS IT WITHOUT
    WRITING. The guard above is a repairer: on a healthy root it backfills a
    missing fingerprint, and on a dead-but-repairable one it repoints the
    stored value and receipts it. Both are right for a FIRE and wrong for a
    PLAN — and wrong in a way that matters now, because `catch_up_plan` sits
    on `workspace_access.RUN_HELPER_ALLOWLIST`, whose one rule is that a
    helper reads or computes and never writes (access-layer review B-1: a
    gated writer on that list turned the helper verb into a write door). So
    the planning path composes the verdict out of `path_repair`'s own PURE
    parts — the registration, the vantage, `plan_repair` — and takes no
    branch that writes. A root that a fire WOULD repair does not block a
    plan: the plan is not the thing that repairs it, and refusing to plan
    would be an outage where the fire has a fix.

    NOT UNDER THE HELPER DOOR (MF-M2-2, 2026-09-20). `repair=True` is this
    function's DEFAULT, and a default is a promise about arguments, not about
    shape: ORCH1's transitive write-scan follows the repairing branch down
    into `path_repair.fire_time_guard` and finds real writes below an
    allow-listed name. `CR_HELPER_DOOR` — stamped by the layer on every
    helper child process — pins the read-only answer regardless of what the
    caller asked for, so the promise `run_helper` makes in its own docstring
    ("this verb writes nothing at all") is a shape here too. Off the door,
    both branches are byte-for-byte what they were.
    """
    if os.environ.get("CR_HELPER_DOOR"):
        return _plan_root_check(workspace_root, machine=machine)
    if not repair:
        return _plan_root_check(workspace_root, machine=machine)
    try:
        import path_repair as pr
    except Exception as exc:  # noqa: BLE001 — never let an import hiccup
        # block a fire; the fire-time guard degrades to "unchecked", not
        # "crashed".
        return {"blocked": False, "ok": True, "checked": False, "error": str(exc)}
    return pr.fire_time_guard(workspace_root, now=now, machine=machine)


def _plan_root_check(workspace_root, *, machine: Optional[str] = None) -> dict:
    """The root-validation verdict composed out of `path_repair`'s PURE parts
    — the registration, the vantage, `plan_repair` — and nothing that writes.

    Split out of `_check_and_repair_root` by MF-M2-2 so the read-only answer
    is reachable from the helper door without the repairing branch being in
    the same body: a static scan that follows calls (ORCH1's, seven frames)
    reads shapes, not the arguments a caller happens to pass. The returned
    dicts are the ones HEAL1's `repair=False` path already returned, key for
    key, including the no-`read_only` shape on an import failure.
    """
    try:
        import path_repair as pr
    except Exception as exc:  # noqa: BLE001 — never let an import hiccup
        # block a fire; the fire-time guard degrades to "unchecked", not
        # "crashed".
        return {"blocked": False, "ok": True, "checked": False, "error": str(exc)}
    try:
        reg = pr.registration(workspace_root)
        vantage = pr.current_vantage(workspace_root, machine=machine)
        verdict = pr.plan_repair(reg, trusted_candidate=workspace_root,
                                 vantage=vantage)
    except Exception as exc:  # noqa: BLE001 — a read failure is "unchecked"
        return {"blocked": False, "ok": True, "checked": False,
                "read_only": True, "error": str(exc)}
    cls = verdict.get("class")
    blocked = cls not in ("alive", "unknown", "repaired")
    return {"blocked": blocked, "ok": not blocked, "checked": True,
            "read_only": True, "repaired": False, "plan_class": cls,
            "reason": verdict.get("reason")}


def dispatch_plan(workspace_root, now: Optional[_dt.datetime] = None,
                  *, machine: Optional[str] = None,
                  fired_via: str = "scheduled",
                  mode: Optional[str] = None,
                  repair_root: bool = True) -> dict:
    """The full fire plan: which jobs are due (ordered), which were skipped by
    a job-level disable. Machine-local naive `now` (the clock cron evaluates
    in); defaults to the real clock. `fired_via` ("scheduled" | "manual")
    gates the MAINTGAP1 min-gap guard below — pass the same word the fire
    used to determine its own run mode (DOGFIX1-style); a Run Now press is
    "manual" and is always exempt.

    Returns {"now": iso, "due": [job dicts], "skipped_disabled": [job ids],
    "root_repair": {...}, "min_gap": {...}}. Each due dict: {job_id, skill,
    description, reason, last_receipt (iso|None), slot (iso)}.

    `root_repair` (SPEC PATHREPAIR1) is the dispatch preamble's verdict —
    see `_check_and_repair_root`. When it reports `blocked: True` (the
    workspace's registered root is dead and could not be auto-repaired with
    high confidence), `due` and `skipped_disabled` are both forced empty:
    NOTHING dispatches this fire, and the refusal rides the return value
    itself rather than an empty-due silence a reader could mistake for "all
    caught up".

    `min_gap` (MAINTGAP1, extended by FIREGAP2) is the fire-start gap
    guard's verdict — see `_check_min_gap`. When it reports `skipped: True`
    (a `scheduled` fire whose predecessor maintenance fire's own START —
    marker-or-receipt, whichever is newer — landed under
    `task_watchdog.MIN_GAP_MINUTES` ago), `due` and `skipped_disabled` are
    both forced empty exactly like the `root_repair.blocked` case above —
    checked AFTER the root-repair gate (a dead root is a different, more
    urgent refusal) and BEFORE any job's due-ness is evaluated, so this is a
    true zero-cost early exit: not one job-level receipt read happens on a
    min-gap-skipped fire. When `min_gap.skipped` is False and `fired_via`
    is `scheduled`, THIS fire immediately writes its own fire-start marker
    (SPEC_FIREGAP2 §0 ruling 1, `write_fire_start_marker`) before due-ness
    is evaluated — so a chain that turns out to run long is visible to the
    NEXT fire's gap check from this instant, not only once this fire's own
    completion receipt lands. A `manual` fire, and a fire the guard itself
    just skipped, never write one (ruling 2; and see `write_fire_start_marker`'s
    own docstring for why a skipped fire must not).

    A job whose newest receipt is `skipped` and whose registry row declares a
    `voided_by` event that has since fired is due ANYWAY (BRIEFFIX1 Item D) —
    its due dict carries `skip_voided_at` and a reason naming the change.
    Completed receipts are unaffected.

    `mode` (HEAL1, SPEC_NIGHTM2 §4 amendment (a)) is the HOST mode this
    dispatch is being planned from — `workspace_access`'s vocabulary
    (`vm` / `local` / `cowork_legacy` / `container`), never `env_detect`'s.
    `None` means "the caller has no opinion", and that is today's behaviour
    byte for byte: nothing is refused. `container` is the one mode that
    changes anything — a process in the cloud container has no workspace on
    its own host, so every job with an `--apply` leg is refused there (see
    `apply_class_jobs`, and REVIEW M-1: that is NOT every job that writes)
    and named on `refused_container` with `refused_line`. The rest are still
    planned, because a container CAN run them through the access layer's
    `run_helper`. This is gap analysis §0.25's posture applied one level up:
    refuse and say where the work belongs, never a silent stage-and-compute
    fallback.

    `repair_root=False` (HEAL1) makes this whole call READ-ONLY: the
    root-validation leg answers without repairing and no fire-start marker
    is written (that one is already `scheduled`-only). It is what
    `catch_up_plan` passes, because that function is reachable through the
    access layer's helper verb, which allows reads and computes and nothing
    else. The default is True and every existing caller is unchanged.

    A job registered `partitioned` (CATCHUP1) carries two more keys:
    `periods` — every unserved nominal slot since its last receipt, ISO,
    OLDEST FIRST, one deliverable owed per entry — and `periods_capped`,
    True when more periods were missed than `catchup.DEFAULT_PERIOD_CAP` and
    the oldest were dropped. The list always contains at least the slot the
    job is due for, so the prompt can iterate `periods` unconditionally for
    a partitioned job.
    """
    now = _coerce_now(now)

    root_repair = _check_and_repair_root(workspace_root, now=now,
                                         machine=machine, repair=repair_root)
    if root_repair.get("blocked"):
        return {
            "now": now.isoformat(),
            "due": [],
            "skipped_disabled": [],
            "root_repair": root_repair,
            "min_gap": {"skipped": False, "gap_minutes": None,
                       "predecessor_slot": None, "this_slot": None},
            "mode": mode,
            "refused_container": [],
            "refused_line": "",
            "refused_reason": "",
            "refused_next": "",
        }

    min_gap = _check_min_gap(workspace_root, now=now, fired_via=fired_via)
    if min_gap.get("skipped"):
        return {
            "now": now.isoformat(),
            "due": [],
            "skipped_disabled": [],
            "root_repair": root_repair,
            "min_gap": min_gap,
            "mode": mode,
            "refused_container": [],
            "refused_line": "",
            "refused_reason": "",
            "refused_next": "",
        }

    if fired_via == "scheduled":
        # FIREGAP2 §0 ruling 1 — THIS fire has now cleared both gates and is
        # genuinely about to dispatch: stamp its own start BEFORE due-ness
        # is evaluated, so the next fire's guard can see this one from its
        # first second rather than only at completion. Never for a `manual`
        # fire (ruling 2) and never for a fire the guard above just skipped
        # (write_fire_start_marker's own docstring — that would ratchet the
        # recorded predecessor forward every 15 minutes, forever).
        write_fire_start_marker(workspace_root, now=now)

    overrides = _job_overrides(workspace_root)
    lasts = last_receipts(workspace_root, _all_job_ids())

    def _due_dict(job_id: str, spec: dict) -> Optional[dict]:
        try:
            # expected_fires_multi accepts a plain cron string OR a tuple of
            # them (CAPSLOT1) — identical to expected_fires for every job
            # whose nominal_cron is still a single string.
            slots = expected_fires_multi(spec["nominal_cron"], now=now, count=1)
        except CronParseError:
            return None  # unparseable registry cron — never crash a fire
        if not slots:
            return None
        slot = slots[0]
        last = lasts.get(job_id)
        voided_at = None
        if last is not None and last >= slot:
            voided_at = _skip_rearm(workspace_root, job_id, spec, last)
            if voided_at is None:
                return None  # already served this slot
        if voided_at is not None:
            reason = (f"skipped at {last.isoformat()} — "
                      f"{spec.get('voided_reason') or 'the reason it skipped no longer applies'} "
                      f"(recorded {voided_at})")
        else:
            reason = ("no run recorded yet" if last is None else
                      f"last ran {last.isoformat()}, its {slot.isoformat()} slot has passed")
        d = {
            "job_id": job_id,
            "skill": spec["skill"],
            "description": spec["description"],
            "reason": reason,
            "last_receipt": last.isoformat() if last else None,
            "slot": slot.isoformat(),
        }
        if voided_at is not None:
            # Named on the due dict so the fire's own receipt can say WHY this
            # job ran outside its slot — a job appearing off-cadence with no
            # recorded cause is the next reader's mystery.
            d["skip_voided_at"] = voided_at
        if spec.get("partitioned"):
            # Each period is its own deliverable — enumerate every one that
            # went unserved, oldest first. Best-effort like everything the
            # dispatcher does: if the enumeration fails, fall back to the one
            # slot the due rule already computed, so a partitioned job never
            # loses its normal fire to a catch-up failure.
            try:
                from catchup import DEFAULT_PERIOD_CAP, missed_periods

                # Ask for one MORE than the cap so `periods_capped` is exact:
                # a gap of exactly cap periods dropped nothing, and flagging
                # it would have the prompt report a shortfall on a clean
                # sweep. Only a cap+1-th period proves something fell off.
                periods = missed_periods(spec["nominal_cron"], last, now=now,
                                         cap=DEFAULT_PERIOD_CAP + 1)
                capped = len(periods) > DEFAULT_PERIOD_CAP
                if capped:
                    periods = periods[-DEFAULT_PERIOD_CAP:]
            except Exception:  # noqa: BLE001 — never crash a fire
                periods, capped = [], False
            if not periods:
                periods = [slot]
                capped = False
            d["periods"] = [p.isoformat() for p in periods]
            d["periods_capped"] = capped
        return d

    due: list[dict] = []
    skipped_disabled: list[str] = []
    # Core silent jobs — always considered (opt-OUT via {"enabled": false}).
    for job_id, spec in MAINTENANCE_JOBS.items():
        override = overrides.get(job_id)
        if isinstance(override, dict) and override.get("enabled") is False:
            skipped_disabled.append(job_id)
            continue
        d = _due_dict(job_id, spec)
        if d is not None:
            due.append(d)
    # Opt-IN jobs — considered ONLY when the workspace turned them on; they run
    # after the core jobs and never appear in the order-is-the-contract pin.
    for job_id, spec in OPTIONAL_JOBS.items():
        if not _optin_enabled(overrides, job_id):
            continue  # never auto: no confirmation => not registered => not due
        d = _due_dict(job_id, spec)
        if d is not None:
            due.append(d)
    # night 11b trial merge (merged-tree review): the composer's nothing-due
    # line says "Next up: <job> at <slot>" and read a key nobody wrote. The
    # next slot is the earliest expected fire strictly after `now` across
    # every enabled job (a week of fires is enough for any registered cron).
    next_up = None
    try:
        horizon = now + _dt.timedelta(days=8)
        cands = []
        for job_id, spec in list(MAINTENANCE_JOBS.items()) + [
                (j, s) for j, s in OPTIONAL_JOBS.items()
                if _optin_enabled(overrides, j)]:
            if job_id in skipped_disabled:
                continue
            try:
                fires = expected_fires_multi(spec["nominal_cron"], now=horizon,
                                             count=64)
            except CronParseError:
                continue
            future = [f for f in fires if f > now]
            if future:
                cands.append((min(future), job_id))
        if cands:
            slot, job_id = min(cands)
            next_up = {"job_id": job_id, "slot": slot.isoformat()}
    except Exception:  # pragma: no cover - never crash a fire over a hint
        next_up = None
    # HEAL1 — the container refusal, applied AFTER due-ness so the plan still
    # says what is owed and only says where it cannot be paid. A refused job
    # is NOT marked served anywhere: it keeps its receipt-less state and is
    # due again the moment the same plan is asked for from a host that holds
    # the data.
    refused_container: list[str] = []
    refused_line = ""
    if mode == CONTAINER_MODE:
        write_class = apply_class_jobs()
        kept = []
        for d in due:
            if d["job_id"] in write_class:
                refused_container.append(d["job_id"])
            else:
                kept.append(d)
        due = kept
        if refused_container:
            refused_line = CONTAINER_REFUSAL_LINE
    return {
        "now": now.isoformat(),
        "due": due,
        "skipped_disabled": skipped_disabled,
        "root_repair": root_repair,
        "min_gap": min_gap,
        "next_up": next_up,
        "mode": mode,
        "refused_container": refused_container,
        "refused_line": refused_line,
        # FIX ROUND 1, REVIEW M-3 — the machine half of the refusal, ON the
        # plan. It was two exported constants with no caller, which is a
        # sentence about an envelope nobody reads; a caller that already
        # reads the access layer's envelopes now reads these off the plan
        # exactly as it reads `reason` / `next` off a refusal there.
        "refused_reason": CONTAINER_REFUSAL_REASON if refused_container else "",
        "refused_next": CONTAINER_REFUSAL_NEXT if refused_container else "",
    }


def due_jobs(workspace_root, now: Optional[_dt.datetime] = None,
            *, machine: Optional[str] = None,
            fired_via: str = "scheduled",
            mode: Optional[str] = None) -> list[dict]:
    """The jobs due at this fire, in execution order (see MAINTENANCE_JOBS —
    order is the contract, never parallelize). A fire with nothing due is a
    fast no-op: write the maintenance_receipt with empty lists and exit.
    `fired_via` — see `dispatch_plan` (MAINTGAP1 min-gap guard exemption).
    `mode` — see `dispatch_plan` (HEAL1's container refusal).

    IT IS DELIBERATELY *NOT* ON `workspace_access.RUN_HELPER_ALLOWLIST`, and
    this sentence used to claim the opposite (REVIEW M-1's class, caught in
    the same pass). `catch_up_plan` is the listed name: both reach
    `dispatch_plan`, whose root-validation leg repairs and whose `scheduled`
    path stamps a fire-start marker, and only `catch_up_plan` refuses both
    branches by construction. On that list the caller is whatever a model
    pasted, so a helper that takes a gated write on an argument does not
    belong there."""
    return dispatch_plan(workspace_root, now=now, machine=machine,
                         fired_via=fired_via, mode=mode)["due"]


def _job_ids(jobs: Optional[Iterable]) -> list[str]:
    """Normalize a jobs argument (ids or due-dicts) to a clean id list."""
    out: list[str] = []
    for j in jobs or ():
        if isinstance(j, dict):
            jid = j.get("job_id")
        else:
            jid = j
        if isinstance(jid, str) and jid.strip():
            out.append(jid.strip())
    return out


#: DOORS1 1.5 (record B2.9 / B2.7) — JOBS THAT DELIBERATELY LEAVE NO RECEIPT
#: ON A QUIET RUN.
#:
#: THE PHANTOM. The scoring rule every maintenance caller follows is "a job
#: counts as completed only when its OWN validator confirmed its OWN receipt;
#: an unreceipted job goes in `jobs_failed` and stays due." For most jobs that
#: is exactly right and is what makes the fire self-healing. For these two it
#: produces a lie: both write a receipt only on a run that CHANGED something,
#: on purpose (`question-expiry` because a day with no question old enough
#: should leave no trace; `binding-gauge` because a quiet receipt would
#: advance the very high-water mark whose standstill made the run quiet). So a
#: correct, complete, nothing-to-do run was scored FAILED — the attended test
#: watched `question-expiry` marked failed in SEVEN of nine scheduled runs
#: between 09-10 and 09-12, and the maintenance report carried those seven
#: phantom failures to the customer.
#:
#: THE FIX IS THE SCORER, NOT THE RECEIPT. Writing a quiet receipt would undo
#: the posture each job has a stated reason for — and for `binding-gauge` it
#: would actively corrupt the measurement. `job_counts_as_complete` below is
#: the one predicate; the registered prompt and `cleanup/SKILL.md` Step 0 both
#: route it instead of judging by receipt presence alone.
QUIET_RUN_JOBS = frozenset({"question-expiry", "binding-gauge",
                            "schedule-realign"})

#: T2 FIRE1 MUST 3 (FIX-7; the second computer's walk, 2026-09-26). The jobs
#: RULED skill-class on the merged fire: the fire may try them, but one that
#: does not finish there is left honestly DUE (in `jobs_due`, in neither
#: `jobs_completed` nor `jobs_failed`, D-6), never reported failed for not
#: running - the walk's meeting-capture fetched twelve meetings, was
#: abandoned, and the End of Day's close captures the same meetings. The
#: watchdog reads this set: a job here that the newest `maintenance_run`
#: left due is never "hasn't recorded any work"; it gets the existing
#: waiting sentence (`CONTAINER_REFUSAL_LINE`) once.
MERGED_SKILL_CLASS_JOBS = frozenset({"meeting-capture"})


def job_counts_as_complete(job_id, *, receipt_validated: bool,
                           run_reported_nothing_due: bool = False) -> bool:
    """Did this job finish? THE scoring predicate for one maintenance job.

    `receipt_validated` is the job's own validator answering about its own
    receipt — unchanged, and still the only way an ordinary job passes.

    `run_reported_nothing_due` is the job's OWN RETURN saying it found nothing
    to do. It is a second measurement, not a second opinion: the caller has
    just run the job and holds its result, so this never infers "probably
    quiet" from an absent receipt. A job outside `QUIET_RUN_JOBS` is scored on
    its receipt whatever it reported, because for those an absent receipt IS
    the failure signal that keeps the fire self-healing.
    """
    if receipt_validated:
        return True
    return bool(run_reported_nothing_due) and str(job_id) in QUIET_RUN_JOBS


# ---------------------------------------------------------------------------
# HEAL1 — maintenance catches up from the surfaces people type
# (SPEC_MERGEFIX1 §4 HEAL1, amended by SPEC_NIGHTM2_LANES §4)
# ---------------------------------------------------------------------------
#
# WHY. On a merged seat a scheduled fire reaches a cloud container with no
# folder attached, so the one `maintenance` task that keeps a book healthy
# stops running and nothing on any customer surface says so. The surfaces the
# customer still types — `morning briefing`, `end of day`, `weekly recap` —
# are the only moments the workspace is reliably open. So they carry the
# upkeep: ask the dispatcher what is due, run it FIRST, receipt it ONCE, and
# say nothing about it.
#
# R-M1 (M's standing commitment ruling 2026-09-03, taken as the default
# 2026-09-19): RUN IT, do not ask. Every job's writes are already undoable by
# door, and a reversible action beats a question.
#
# R-M2: the Sunday family never runs from a weekday surface. A Monday brief
# that opens with a multi-minute cleanup pass is the wrong first surface of
# the day; `weekly recap` and `run maintenance` are the two doors it keeps.
#
# NOTHING HERE NARRATES. NUMBER1 3.9 / HEALTH1 took the plumbing's own
# condition off every customer surface, and a catch-up is the plumbing. What
# the catch-up's jobs DID to the reader's own rows still reaches them, through
# the CHANGED strip's machine-batch partition, credited by door with its undo
# — that is `surface_drivers.CHANGED_MACHINE_BATCH_CATEGORIES`, unchanged by
# this build and re-pinned on the new receipt shape.

#: The surfaces that may run due jobs, and WHICH family each may run.
#: `weekday` = everything except the Sunday family; `all` = the whole
#: registry. The two `all` doors are R-M2's two doors, and this dict is the
#: only place that rule is written down.
CATCH_UP_SURFACES: dict = {
    "morning-brief": "weekday",
    "end-of-day": "weekday",
    "friday-wrap": "all",
    "run-maintenance": "all",
}

#: Spellings a caller may hand in, mapped to the task ids above. The product's
#: own vocabulary for these surfaces is `schedule_config.DEFAULT_SCHEDULES`'s
#: task ids, and the receipt's `triggered_by` carries THOSE — one spelling on
#: the ledger, whatever the caller typed.
CATCH_UP_SURFACE_ALIASES: dict = {
    "morning-briefing": "morning-brief",
    "morning briefing": "morning-brief",
    "brief": "morning-brief",
    "end of day": "end-of-day",
    "eod": "end-of-day",
    "weekly-recap": "friday-wrap",
    "weekly recap": "friday-wrap",
    "wrap": "friday-wrap",
    "run maintenance": "run-maintenance",
    "maintenance": "run-maintenance",
    # FIX ROUND 1, REVIEW L-1 — THE READ SURFACES HAD NO SPACED SPELLINGS.
    # Every RUN surface above carries the words a person types; the two read
    # surfaces carried only their task ids, so `catch_up_surface("staff
    # meeting")` — the exact phrase the spec's fifth pass line types — came
    # back `None` and the caller was told the surface was unknown. Harmless
    # while every production caller passes a canonical id, and a wrong
    # answer to a question this module exposes, on the eve of a migration
    # that hands these helpers words a model typed.
    "staff meeting": "staff-meeting",
    "what's on my plate": "my-plate",
    "whats on my plate": "my-plate",
    "my plate": "my-plate",
}

#: RUNNOW1's door, and the one surface here that is a PERSON ASKING.
#:
#: `run maintenance` is in the dict above so R-M2's rule — which surfaces may
#: run the Sunday family — has exactly one home. But it is not a catch-up: it
#: is somebody typing the words, and MAINTGAP1 ruling 2 has always exempted
#: that from the gap guard for the reason a Run Now press exists at all. The
#: staleness gate is exempted with it, and for the same reason: a person who
#: asks for their upkeep to run gets a fire, not a sentence explaining that
#: the last slot was served. The three TYPED SURFACES are the opposite case —
#: nobody asked for the upkeep there, it rides in front of something else —
#: which is why both gates bind them.
CATCH_UP_ALWAYS_SURFACES = frozenset({"run-maintenance"})

#: HEAL1 behaviour 4 — the READ surfaces. They never run a job: they are the
#: two surfaces a reader opens to look at their own rows, and a multi-minute
#: pass in front of a glance is not an upkeep win. They print TRUTH1's line
#: when maintenance is more than one weekday stale and that is all they do.
CATCH_UP_READ_SURFACES = frozenset({"staff-meeting", "my-plate", "plate"})

#: `workspace_access`'s host-mode word for "this process is in the cloud
#: container and no workspace is on this host". Carried as a literal rather
#: than imported so this module never depends on the access layer at import
#: time (it runs in three environments, one of which has neither).
CONTAINER_MODE = "container"

#: The machine half of the container refusal — the access layer's own
#: vocabulary, so a caller that already reads envelopes reads this the same
#: way (gap analysis §0.25: refuse and name where the work belongs).
#:
#: FIX ROUND 1, REVIEW M-3: these two ride ON THE PLAN (`refused_reason` /
#: `refused_next`, beside `refused_line`) and are read from there. They were
#: exported constants with no caller and a pin that compared two literals,
#: which proved nothing about what any caller sees.
CONTAINER_REFUSAL_REASON = "no_workspace_on_this_host"
CONTAINER_REFUSAL_NEXT = "device_bash"

#: The customer half. One plain sentence, no product part named, no count —
#: it says the upkeep is waiting for the machine that holds the folder. It is
#: NOT printed on a brief, an end of day or a weekly recap (those surfaces
#: stay silent about plumbing, behaviour 2); it is what `run maintenance`
#: and the health check have to say when someone asks directly.
CONTAINER_REFUSAL_LINE = (
    "Some of that upkeep has to run on the computer that holds your files, "
    "so it is waiting for the next time this runs there.")

#: The four jobs SPEC_NIGHTM2 §4 names as the write class. The live set is
#: DERIVED (see `apply_class_jobs`) so a job added to the registry with an
#: apply leg is fenced the day it lands instead of the day someone remembers
#: this tuple; these four are the non-vacuity floor the suite pins against
#: that derivation, exactly the "pin against the producing constant" trap
#: inverted — the spec's names are carried as literals here.
CONTAINER_REFUSED_JOBS_NAMED = ("dedup-apply", "review-expiry",
                                "question-expiry", "calendar-close")

#: The registry's own marker for an APPLY leg: the job's `skill` string names
#: an `--apply` flag. It marks a job that changes the reader's own rows — not
#: a job that writes (nearly every job writes its own receipt; REVIEW M-1).
_APPLY_MARKER = "--apply"


def apply_class_jobs(jobs: Optional[dict] = None) -> frozenset:
    """The job ids WITH AN `--apply` LEG — derived from the registry's own
    `skill` strings, never a hand-kept list.

    FIX ROUND 1, REVIEW M-1 — THIS IS NOT "EVERY JOB THAT WRITES", AND THE
    NAME NOW SAYS SO. It was called `write_class_jobs` and its docstring
    claimed every writer was refused in the container. That was wider than
    the code by a long way: `cleanup`, `session-sweep`, `reconcile-sent`,
    `reconcile-chat`, `meeting-capture`, `weekly-insights`, `learning`,
    `deal-signals` and `schedule-realign` all write at minimum their own
    substrate receipt — `job_counts_as_complete` REQUIRES one — and every
    one of them is kept, not refused.

    What the derivation really picks out is the jobs whose own CLI leg
    carries an apply flag: the ones that change the reader's OWN rows
    (expire a question, close a calendar item, apply a dedup). Those are the
    ones it is worth refusing far from the data, because a refused one stays
    owed and nothing of the reader's is half-changed. A job that only
    receipts its own run is left planned: refusing it would stop the upkeep
    from recording that it happened, which is the failure this lane exists
    to end.

    THE DEEPER ANSWER IS A CLASS ON THE REGISTRY, not a string search —
    recorded as the seam it is (R-HEAL1-3 default: keep the derivation,
    rename it, correct the sentences).
    """
    registry = jobs if jobs is not None else dict(MAINTENANCE_JOBS)
    out = set()
    for job_id, spec in registry.items():
        skill = str((spec or {}).get("skill") or "")
        if _APPLY_MARKER in skill:
            out.add(job_id)
    return frozenset(out)


def _cron_is_sunday_only(cron) -> bool:
    """True when EVERY cron string in `cron` fires on Sunday and no other day.

    `cron` is a plain string or a tuple of them (CAPSLOT1). These are
    five-field crons (minute hour day-of-month month day-of-week), the shape
    `schedule_config`'s parser takes, so the day-of-week field is the FIFTH
    and `0` alone is Sunday. A `*` or any other day makes the job a weekday
    job for this purpose, which is the safe direction: the Sunday family is
    the set that gets HELD BACK, so a misread widens what a weekday brief
    runs rather than silently dropping work.
    """
    crons = cron if isinstance(cron, (tuple, list)) else [cron]
    saw = False
    for c in crons:
        fields = str(c or "").split()
        if len(fields) < 5:
            return False
        if fields[4].strip() != "0":
            return False
        saw = True
    return saw


#: The six jobs SPEC_MERGEFIX1 §4 names as the Sunday family. Same posture as
#: `CONTAINER_REFUSED_JOBS_NAMED`: the live set is derived from the registry's
#: own crons, and these names are the floor the suite holds that derivation to.
SUNDAY_FAMILY_NAMED = ("cleanup", "weekly-insights", "learning", "deal-signals",
                       "identity-reconcile", "lifecycle")


def sunday_family(jobs: Optional[dict] = None) -> frozenset:
    """The jobs whose own cadence is Sunday-only — R-M2's held-back family,
    derived from `MAINTENANCE_JOBS`' own `nominal_cron` values.

    Derived rather than listed so a seventh Sunday job is held back from a
    Monday brief the day it is registered. The suite pins that this set
    CONTAINS `SUNDAY_FAMILY_NAMED` and contains no weekday job, so the
    derivation can grow but cannot drift off the spec's six.
    """
    registry = jobs if jobs is not None else dict(MAINTENANCE_JOBS)
    return frozenset(job_id for job_id, spec in registry.items()
                     if _cron_is_sunday_only((spec or {}).get("nominal_cron")))


def catch_up_surface(surface) -> Optional[str]:
    """The canonical task id for a caller-supplied surface word, or None."""
    # A curly apostrophe is the same word (L-1: the phrases above are typed,
    # and every editor that autocorrects turns `what's` into `what’s`).
    key = str(surface or "").strip().lower().replace("’", "'")
    if not key:
        return None
    key = CATCH_UP_SURFACE_ALIASES.get(key, key)
    if key in CATCH_UP_SURFACES or key in CATCH_UP_READ_SURFACES:
        return key
    return None


def catch_up_family(surface) -> Optional[str]:
    """`weekday` | `all` for a surface that may run jobs; None otherwise."""
    return CATCH_UP_SURFACES.get(catch_up_surface(surface) or "")


def _newest_maintenance_receipt_dt(workspace_root) -> Optional[_dt.datetime]:
    """The newest `maintenance_run` receipt's own instant, machine-local
    naive — ANY `fired_via`, deliberately.

    `_newest_fired_at_slot` skips manual receipts because a Run Now press
    must never become the predecessor a SCHEDULED fire's gap is measured
    against. This reader answers a different question: "when did this book
    last get its upkeep, by any route". A catch-up twenty minutes ago did
    the work whoever typed it was owed, so it counts here, and the second
    surface of the morning finds nothing to do.

    Best-effort like every other read in this module: an unreadable
    substrate returns None, which reads as "never" and lets the catch-up
    run. A redundant pass is the safe direction; a silently skipped one is
    the failure this lane exists to end.
    """
    try:
        from receipts import iter_receipts

        rows = iter_receipts(workspace_root, task_ids=[MAINTENANCE_TASK_ID])
    except Exception:  # noqa: BLE001
        return None
    newest = None
    for r in rows or []:
        if r.get("type") != RECEIPT_EVENT_TYPE:
            continue
        dt = r.get("dt")
        if dt is None:
            continue
        if newest is None or dt > newest:
            newest = dt
    return _to_local_naive(newest) if newest is not None else None


def _weekdays_between(earlier: Optional[_dt.datetime],
                      later: _dt.datetime) -> Optional[int]:
    """Whole weekdays (Mon–Fri) strictly between two dates, or None when
    `earlier` is None. Saturday and Sunday are not days the upkeep was
    expected, so a Friday receipt read on Monday is ONE weekday old."""
    if earlier is None:
        return None
    a, b = earlier.date(), later.date()
    if b <= a:
        return 0
    n = 0
    cur = a + _dt.timedelta(days=1)
    while cur <= b:
        if cur.weekday() < 5:
            n += 1
        cur += _dt.timedelta(days=1)
    return n


def maintenance_staleness(workspace_root,
                          now: Optional[_dt.datetime] = None) -> dict:
    """Has this book's maintenance fallen behind? READ/COMPUTE ONLY.

    Returns `{"stale": bool, "slot": iso|None, "last_receipt": iso|None,
    "weekdays_stale": int|None, "grace_minutes": int, "reason": str}`.

    THE RULE, and the reading it takes (recorded because the spec's sentence
    admits two). SPEC_MERGEFIX1 §4 says the surface catches up when "the
    newest `maintenance_run` receipt is older than the most recent nominal
    maintenance slot + `_FIRE_GRACE`". Read literally — receipt < slot +
    grace — every book on earth is stale ninety minutes after every slot,
    including one whose fire landed on time, and the spec's own pass line
    ("a copy whose newest `maintenance_run` is 30 minutes old: NO catch-up")
    would fail. The reading taken here is the one that makes both sentences
    true at once and is what `_FIRE_GRACE` means everywhere else in the
    product (dispatch jitter + long fires, `task_watchdog:207`): the receipt
    is stale when it is older than the most recent nominal slot BY MORE THAN
    the grace. A slot served late still served it; a slot never served at
    all falls behind by the whole interval.

    No slot (an unparseable or absent maintenance cron) is never stale — a
    book with no cadence has nothing to be behind.
    """
    now = _coerce_now(now)
    slot = None
    try:
        task_cron = DEFAULT_SCHEDULES[MAINTENANCE_TASK_ID]["cron"]
        slots = expected_fires(task_cron, now=now, count=1)
        slot = slots[0] if slots else None
    except (KeyError, CronParseError):
        slot = None
    last = _newest_maintenance_receipt_dt(workspace_root)
    weekdays = _weekdays_between(last, now)
    if slot is None:
        return {"stale": False, "slot": None,
                "last_receipt": last.isoformat() if last else None,
                "weekdays_stale": weekdays,
                "grace_minutes": int(_FIRE_GRACE.total_seconds() // 60),
                "reason": "no nominal maintenance slot on this book"}
    if last is None:
        return {"stale": True, "slot": slot.isoformat(), "last_receipt": None,
                "weekdays_stale": None,
                "grace_minutes": int(_FIRE_GRACE.total_seconds() // 60),
                "reason": "no maintenance run recorded yet"}
    behind = slot - last
    stale = behind > _FIRE_GRACE
    return {
        "stale": bool(stale),
        "slot": slot.isoformat(),
        "last_receipt": last.isoformat(),
        "weekdays_stale": weekdays,
        "grace_minutes": int(_FIRE_GRACE.total_seconds() // 60),
        "reason": ("the most recent upkeep slot went unserved"
                   if stale else "the most recent upkeep slot was served"),
    }


# ---------------------------------------------------------------------------
# The rendered LEG (FIX3 F3-6, ruling R-RW-5)
# ---------------------------------------------------------------------------
#: For every job that HAS a command line, the command line — as argv parts,
#: with `{root}` where the workspace goes. The registry's `skill` field is
#: prose written for a reader; this is the string a fire pastes. They are kept
#: apart on purpose: the 2026-09-21 re-walk ran four upkeep jobs inside a
#: hand-typed brief and all four stamped themselves `scheduled`, because the
#: surface's instruction was "execute each job's skill end to end" and a skill
#: has no place to put a flag.
#:
#: A job with no row here is a SKILL, not a script. Its `leg` comes back empty
#: and the `env` below is what carries the answer instead — which is why the
#: environment exists at all rather than every caller being asked to thread a
#: flag it may have nowhere to put.
JOB_LEGS: dict = {
    "review-expiry": ("python3 shared/scripts/commitment_backlog_sweep.py "
                      "review-expiry --workspace {root} --apply"),
    "question-expiry": ("python3 shared/scripts/question_ttl.py "
                        "--workspace {root} --apply"),
    "calendar-close": ("python3 shared/scripts/calendar_close.py "
                       "--workspace {root} --apply"),
    "exit-doors": ("python3 shared/scripts/exit_doors.py "
                   "--workspace {root} --apply"),
    "binding-gauge": ("python3 shared/scripts/binding_gauge.py "
                      "{root} --job --apply"),
    "daily-measure": ("python3 shared/scripts/flow_measure.py "
                      "{root} --apply"),
    "dedup-apply": ("python3 shared/scripts/commitment_dedup.py "
                    "--workspace {root} --apply"),
    "learning": ("python3 shared/scripts/learning_pass.py "
                 "--workspace {root} --apply"),
    "identity-reconcile": ("python3 shared/scripts/identity_reconcile.py "
                           "--workspace {root} --apply"),
    # MAINTJOBS1 MUST 3 - pure Python, no connector: a script job, so the
    # merged fire runs it through `run_job` like the rest of this map.
    "deal-signals": ("python3 shared/scripts/deal_signal_detector.py "
                     "--workspace {root} --apply"),
    "lifecycle": ("python3 shared/scripts/lifecycle_pass.py "
                  "--workspace {root} --apply"),
}


def job_leg(job_id: str, workspace_root, surface: str, fired_via: str = "manual"):
    """`(leg, env)` for ONE job run by a typed surface.

    `leg` is the whole command, ending in the two flags that make the receipt
    say what actually happened; `env` is the same two answers as variables, for
    a job whose runner is a skill rather than a script and for any composer the
    leg reaches in a process of its own.
    """
    if surface is None:
        # MAINTJOBS1 MUST 1 - a SCHEDULED fire's plan (`catch_up_plan` with
        # the scheduled word) is asked by nobody: its legs end
        # `--fired-via scheduled` and carry no `--triggered-by`, exactly the
        # flags the legacy prompt's own CLI call has always passed.
        env = {"CR_FIRED_VIA": fired_via}
        template = JOB_LEGS.get(job_id)
        if not template:
            return "", env
        return (template.format(root=_quoted(workspace_root))
                + " --fired-via " + fired_via), env
    env = {"CR_FIRED_VIA": fired_via, "CR_TRIGGERED_BY": surface}
    template = JOB_LEGS.get(job_id)
    if not template:
        return "", env
    leg = template.format(root=_quoted(workspace_root))
    return (leg + " --fired-via " + fired_via
            + " --triggered-by " + surface), env


#: How long ONE job's own run may take through the write door before the
#: door reports it as a failure. Generous: the longest leg (the learning pass)
#: reads the whole book.
RUN_JOB_TIMEOUT_S = 600


def run_job(workspace_root, job_id: str, *, fired_via: Optional[str] = None,
            triggered_by: Optional[str] = None) -> dict:
    """Run ONE script-class maintenance job — the write door's single entry
    for the whole registry (ORCH2, Night M3 §4 "the scheduled chains").

    WHY ONE ENTRY. On the Require-this-computer shape the maintenance fire
    cannot run a job's CLI in its own shell: the files are on the customer's
    machine. So the job runs through `plan run_writer`, and the writer list
    names THIS function rather than one entry per script — the registry
    (`JOB_LEGS`) is the list, so a new job needs a row there and nothing on
    the door.

    WHAT IT RUNS is exactly the job's own leg: the command `job_leg` renders,
    as an argument list (never through a shell), from the plugin root this
    module ships in. The two answers that make the job's own receipt honest
    come from the environment the door sets — `CR_FIRED_VIA` (the bootloader
    exports `scheduled`; `plan` forwards it) and `CR_TRIGGERED_BY` when a typed
    surface caused the run — unless the caller names them. A scheduled fire
    therefore records `scheduled`, and a job run with nothing forwarded passes
    no flag at all, which is what the legacy CLI path has always done.

    A job with no leg is a SKILL, not a script: it is refused here by name
    (`not_a_script_job`) and runs through its own skill instead. Never raises
    for a job's own failure — the return says what happened.
    """
    import subprocess

    template = JOB_LEGS.get(str(job_id or ""))
    if not template:
        return {"job_id": job_id, "ran": False, "reason": "not_a_script_job"}
    via = (fired_via or os.environ.get("CR_FIRED_VIA") or "").strip() or None
    trig = (triggered_by or os.environ.get("CR_TRIGGERED_BY") or "").strip() or None
    argv = []
    for part in template.split():
        argv.append(str(workspace_root) if part == "{root}" else part)
    if argv and argv[0] == "python3":
        argv[0] = sys.executable
    if via:
        argv += ["--fired-via", via]
    if trig:
        argv += ["--triggered-by", trig]
    plugin_root = Path(__file__).resolve().parent.parent.parent
    try:
        proc = subprocess.run(argv, cwd=str(plugin_root), capture_output=True,
                              text=True, timeout=RUN_JOB_TIMEOUT_S,
                              env=dict(os.environ))
    except subprocess.TimeoutExpired:
        return {"job_id": job_id, "ran": False, "reason": "timeout",
                "timeout_s": RUN_JOB_TIMEOUT_S}
    output = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                output = json.loads(line)
            except ValueError:
                output = None
            break
    return {"job_id": job_id, "ran": True, "returncode": proc.returncode,
            "fired_via": via, "triggered_by": trig, "output": output,
            "stdout_tail": (proc.stdout or "")[-1500:],
            "stderr_tail": (proc.stderr or "")[-1500:]}


#: The rendered command head every merged-branch step below starts with — the
#: form `workspace_access.py plan <verb>` prints, with `$RT` the installed
#: runtime `discover` answered. Spelled once so the three steps cannot drift.
_PLAN_HEAD = 'python3 "$RT/shared/scripts/workspace_access.py"'

#: The canonical v6 Access preamble's home and its sentinels. The maintenance
#: prompt is registered standalone — not through the bootloader — so nothing
#: else resolves the plugin root, the environment or the device hand-off for
#: it (review F-4, ORCH2 fix pass 1). The block is READ from its one source at
#: compose time, never retyped here, so it cannot drift from the 66 carriers
#: `propagate_access_preamble.py` keeps byte-identical.
_ACCESS_DOC = Path(__file__).resolve().parent.parent / "WORKSPACE_ACCESS.md"
_PREAMBLE_BEGIN = "# >>> CR ACCESS PREAMBLE v6"
_PREAMBLE_END = "# <<< CR ACCESS PREAMBLE v6 <<<"

#: The one sentence that says WHERE every access-layer line is rendered on the
#: merged shapes (IDENT1 I-11's rule, for this prompt): in this session, where
#: the account lives, and only the printed line crosses to the device.
def _merged_render_rule(pair: str = "") -> str:
    """`MERGED_RENDER_RULE` with the registering seat's writer pair in front
    of `python3` on its render form (MF-26); an empty `pair` is the constant,
    byte for byte."""
    return (
    "WHERE EACH LINE IS RENDERED. On a merged seat resolve with "
    "`workspace_access.py discover` exactly as the preamble's rule 1 says "
    "(its block through `mcp__remote-devices__device_bash` when that tool is "
    "in this run; in this shell when the folder is mounted here), and keep "
    "WS and RT from its answer. Every access-layer line below is RENDERED IN "
    "THIS SESSION - `cd \"$PLUGIN_ROOT\" && CR_FIRED_VIA=scheduled "
    + pair + "python3 "
    "shared/scripts/workspace_access.py plan <verb> --json '...'` - and the "
    "ONE line it prints runs in this shell when the folder is mounted here, "
    "or is pasted into `mcp__remote-devices__device_bash` unchanged when the "
    "folder is on the customer's computer. Never run `plan` on the device: "
    "the account this task runs under lives in this session, so a line "
    "rendered on the device carries no writer identity and every write it "
    "names is refused.\n"
    )


MERGED_RENDER_RULE = _merged_render_rule()


def _pair_vars() -> str:
    """MF-26 (merge-fix 2; the merge-fix reader's R-1 HIGH). The two variables
    the REGISTERING seat's writer pair rides on, in front of `python3` on
    every M-branch line - variables on the line, never an `export` (an export
    has the same survival problem as the folder export it replaces). They are
    placeholders here; `writer_identity.bake_pair` substitutes and
    shape-checks them, the same function the chats' bootloaders are baked by.
    """
    from writer_identity import (WRITER_DERIVATION_PLACEHOLDER,
                                 WRITER_ID_PLACEHOLDER)
    return (f"CR_WRITER_ID={WRITER_ID_PLACEHOLDER} "
            f"CR_WRITER_DERIVATION={WRITER_DERIVATION_PLACEHOLDER} ")


#: The one sentence a baked prompt adds after the render rule (MF-26): the
#: values are the task's identity, handed in at registration, and they stay on
#: every line.
BAKED_PAIR_RULE = (
    "The CR_WRITER_ID and CR_WRITER_DERIVATION values on these lines were "
    "baked in when this task was registered: they are who this task writes "
    "as, so keep them in front of python3 on every line exactly as written.\n"
)


def access_preamble_block() -> str:
    """The canonical v6 Access preamble, sentinel to sentinel, read from
    `shared/WORKSPACE_ACCESS.md`. Raises ValueError when the source is
    missing or its sentinels are not a single pair — a registration must
    never compose a maintenance prompt with no way to reach the files."""
    try:
        text = _ACCESS_DOC.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"the Access preamble source is unreadable: {exc}")
    if text.count(_PREAMBLE_BEGIN) != 1 or text.count(_PREAMBLE_END) != 1:
        raise ValueError("the Access preamble source does not carry exactly "
                         "one pair of v6 sentinels")
    start = text.index(_PREAMBLE_BEGIN)
    end = text.index(_PREAMBLE_END) + len(_PREAMBLE_END)
    return text[start:end]


#: MAINTJOBS1 MUST 4 (ruling R-RW3-4) - the maintenance fire's ONE permitted
#: final message. A fire's final response is what the phone shows for the
#: task; the 2026-09-23 fire closed its silent contract with a paragraph
#: naming `staleness.stale`, the dispatcher and the receipt it wrote. The
#: contract is now: the last message is EMPTY; when the app requires one, it
#: is exactly this and nothing else - no variable, file, count or mechanism.
#: Validated by `chat_output_validator` with the fire families on (pinned);
#: the run-log audit's silent-fire check reads this same constant.
SILENT_CLOSE_LINE = "Done."


def final_response_rule() -> str:
    """The one FINAL RESPONSE sentence both branches of the maintenance prompt
    end with (MUST 4), composed from `SILENT_CLOSE_LINE`.

    T2 FIRE1 MUST 1 (R-WALK-4): `Done.` closes ONLY a run that reached its
    workspace. On the second computer (2026-09-26) two fires found no folder,
    asked for none, and closed `Done.` - SUCCEEDED on the app, nothing on the
    book. So the rule names the condition, and F0's stop sentence is the
    close of a run that reached nothing (`fire_close_line` is the same rule
    in code)."""
    return (
        "FINAL RESPONSE. The last message of this run is empty; if the app "
        "requires a message, it is exactly `" + SILENT_CLOSE_LINE + "` and "
        "nothing else - never a sentence that names a field, a file, a "
        "count, a step, a tool or what ran, and never a summary of this "
        "run. `" + SILENT_CLOSE_LINE + "` closes only a run that reached "
        "its workspace (an access-layer line answered `ok: true`, or the "
        "files were on this filesystem); a run that reached no workspace "
        "ends on F0's sentence, never on `" + SILENT_CLOSE_LINE + "`.\n")


# ---------------------------------------------------------------------------
# T2 FIRE1 MUST 1 (FIX-1, F-OFF-5/6, ruling R-WALK-4) - reach the folder
# first, and never close a run that reached nothing with `Done.`
# ---------------------------------------------------------------------------
#
# THE WALK. A task the product registers on a merged seat starts WITHOUT the
# app's per-task folder permission. The visible chats' bootloaders carry a
# Step 0 that asks for the folder (`get_device_info`, then ONE
# `device_request_folder_access`); the maintenance body, registered
# standalone, carried none. Its fire hit "No folders are connected", never
# asked, and closed `Done.` - twice, SUCCEEDED on the app, nothing on the
# book. Ruling R-WALK-4: request once, and when there is still no workspace
# stop with the ONE Trusted-folders sentence; `Done.` never closes a run
# that reached no workspace.

#: The reason the folder request names - the chat's own display name, the
#: bootloaders' form ("Command Room's <CHAT_DISPLAY_NAME> needs its workspace
#: folder"); a customer reads it on the Allow card.
FOLDER_REQUEST_REASON = "Command Room's Maintenance needs its workspace folder"

#: The two refusal reasons that mean "this run cannot see the folder" - the
#: access layer's own spellings (`workspace_access.R_NOT_ATTACHED` /
#: `R_NO_WORKSPACE`), pinned equal by the lane suite.
NO_FOLDER_REASONS = ("folder_not_attached", "no_workspace_on_this_host")

_UNSAFE_PATH_CHARS = ('"', "'", "`", "$", "\n", "\r", "\x00")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/])")


def no_workspace_line() -> str:
    """The ONE sentence a maintenance fire that reached no workspace stops
    with: `schedule_config.TRUSTED_FOLDERS_PARAGRAPH`, read from its one
    home and never retyped (R-WALK-4)."""
    from schedule_config import TRUSTED_FOLDERS_PARAGRAPH

    return TRUSTED_FOLDERS_PARAGRAPH


def workspace_reached(envelopes) -> bool:
    """Did this run reach its workspace? True when ANY access-layer envelope
    it received answered `ok: true` with the folder attached - the one fact
    `Done.` is allowed to stand on. A refusal, a missing envelope, or no
    access-layer line at all is False."""
    for env in envelopes or ():
        if (isinstance(env, dict) and env.get("ok") is True
                and env.get("folder_attached") is True):
            return True
    return False


def fire_close_line(envelopes, reenabled=None) -> str:
    """The maintenance fire's final message, decided from what it received.

    No workspace reached -> `no_workspace_line()` (never `Done.`). Reached
    -> `SILENT_CLOSE_LINE`, unless the fire switched its own chat back on
    (MUST 4), when it is that ONE line instead."""
    if not workspace_reached(envelopes):
        return no_workspace_line()
    said = reenabled_line(reenabled) if reenabled else ""
    return said or SILENT_CLOSE_LINE


# ---------------------------------------------------------------------------
# T2 FIRE1 MUST 4 (ruling R-WALK-1) - a fire that finds its own chat switched
# off by the app after a missed slot switches it back on, and SAYS so
# ---------------------------------------------------------------------------
#
# THE WALK (laptop, 2026-09-25): the Claude app switched Maintenance and the
# Morning Brief OFF after one morning with the computer closed; nothing in the
# product said so. R-WALK-1: a fire MAY re-enable its own chat, and it says so
# in ONE plain line - never silent, never twice. A chat the CUSTOMER paused
# (`schedule_config` carries `enabled: false` for it) is never switched on.

#
# CHATSON1 (hotfix, 2026-10-06, ChaletteHQ/cr1#98): the same walk showed the
# Morning Brief off too, and this path only ever looked at the fire's own
# chat - every other registered chat stayed off until somebody noticed. The
# fire now checks EVERY chat on the live roster (`chats_state`), under the
# same guards: the scheduler's listing must show the trigger off, a chat the
# customer paused is never touched, a retired or superseded id is never
# touched, and it is still said in ONE line - naming every chat it switched on.

#: The one line, with the chat's display name in the one slot. It names the
#: chat and nothing about tools or ids; validated with the fire families on.
REENABLED_LINE_FORM = ("Your {chat} chat had been switched off by the Claude "
                       "app after a missed run; it is back on now.")

#: CHATSON1 - the same line for several chats at once, their display names
#: joined ("Morning Brief, Inbox and Maintenance") in the one slot.
REENABLED_LINES_FORM = ("Your {chats} chats had been switched off by the "
                        "Claude app after a missed run; they are back on now.")


def _join_names(names) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def reenabled_line(task_ids) -> str:
    """The ONE line for the chats this fire switched back on, or "".

    `task_ids` is one chat id (a string) or an iterable of the ids it switched
    on; the line is rendered ONCE, naming each distinct chat once in the
    order handed in, however many times an id was handed in (R-WALK-1: once,
    never twice). An id with no display name is left out of the line."""
    if isinstance(task_ids, str):
        ids = [task_ids]
    else:
        ids = [t for t in (task_ids or ()) if isinstance(t, str) and t]
    if not ids:
        return ""
    from schedule_config import DISPLAY_NAMES

    names = []
    for task_id in ids:
        chat = DISPLAY_NAMES.get(task_id)
        if chat and chat not in names:
            names.append(chat)
    if not names:
        return ""
    if len(names) == 1:
        return REENABLED_LINE_FORM.format(chat=names[0])
    return REENABLED_LINES_FORM.format(chats=_join_names(names))


#: T3 FIRE3 MUST 1 - the re-enable's ONE receipt: its writer (on the write
#: door) and its reason (in `schedule_config.CONFIG_CHANGE_REASONS`).
REENABLE_WRITER = "schedule_config:log_schedule_config_change"
REENABLE_REASON = "reenabled_after_missed_run"


def own_chat_state(workspace_root, task_id: str = "maintenance",
                   listed=None) -> dict:
    """What this fire needs to switch its OWN chat back on - READ ONLY.

    `{task_id, trigger_id, paused_by_customer, reenable, check_listing}`:
    `trigger_id` from the stored trigger map
    (`schedule_backend.read_trigger_map`); a chat the customer paused
    (`schedule_config` says `enabled: false`) or one with no stored trigger
    has `reenable: None` and `check_listing: False`.

    T2B FIRE3B MUST 1 (F-T2-13): `reenable` is never offered on the stored
    trigger alone. The walk's 12:45 plan carried the switch-on call and the
    "it is back on now" line while the chat was ON and firing; one fire that
    obeyed it would have told the customer a false thing. So:
      * `listed` omitted (the plan's own answer): `reenable: None` and
        `check_listing: True` when there is a trigger to look for - the fire
        lists the scheduler's chats once and asks again with `listed`;
      * `listed` given (the scheduler's raw rows, `id` / `trigger_id` and
        `enabled`): `reenable` is the call, the receipt it then owes through
        the write door and the one line, ONLY when `needs_reenable` finds
        THIS chat's trigger reading `enabled: False`; else None."""
    out = {"task_id": task_id, "trigger_id": None,
           "paused_by_customer": False, "reenable": None,
           "check_listing": False}
    try:
        from schedule_backend import CLOUD_TOOL_MAP, read_trigger_map
        from schedule_config import load_schedule_config
    except Exception:  # noqa: BLE001 - an older tree: nothing to offer
        return out
    try:
        row = read_trigger_map(workspace_root).get(task_id) or {}
    except Exception:  # noqa: BLE001
        row = {}
    trigger_id = row.get("trigger_id") if isinstance(row, dict) else None
    out["trigger_id"] = str(trigger_id) if trigger_id else None
    try:
        config = load_schedule_config(
            Path(workspace_root) / "_hq" / "data" / "entities.json")
        paused = (config.get(task_id) or {}).get("enabled") is False
    except Exception:  # noqa: BLE001
        paused = False
    out["paused_by_customer"] = bool(paused)
    if not out["trigger_id"] or paused:
        return out
    # T3 FIRE3 MUST 1 (D-T2-3, D-T3-3): an ENABLE is a config change, and the
    # lateness ledger reads `schedule_config_changed` only (SCHED1), so the
    # one receipt is that row through its one writer, with its own reason;
    # the `schedule_refreshed` row (stamped `origin: bridge`) is dropped here.
    # T2B FIRE3B MUST 1 gates WHEN it is offered (the `listed` check below);
    # Train 3 decides WHAT it owes (this one row).
    offer = {
        "call": {"tool": CLOUD_TOOL_MAP["update"],
                 "args": {"trigger_id": out["trigger_id"], "enabled": True}},
        "receipts": [{"name": REENABLE_WRITER,
                      "args": {"changes": [{"task_id": task_id, "cron": None,
                                            "enabled": True}],
                               "source_skill": "maintenance",
                               "extra_data": {"reason": REENABLE_REASON}}}],
        "line": reenabled_line(task_id),
    }
    if listed is None:
        out["check_listing"] = True
        return out
    # LOWS2 row 5 (FIRE3B N-1): a listing that is not a list is no listing,
    # answered as a plain no rather than a raise through the read door.
    if not isinstance(listed, (list, tuple)):
        listed = []
    if needs_reenable({"trigger_id": out["trigger_id"], "reenable": offer},
                      listed):
        out["reenable"] = offer
    return out


def needs_reenable(own_chat, listed) -> bool:
    """True only when the scheduler's own listing shows THIS chat's trigger
    switched off and the plan offered to switch it on. `listed` is the
    scheduler's raw rows (`id` / `trigger_id`, `enabled`).

    LOWS2 row 5 (FIRE3B N-2): EVERY row naming this chat's trigger must read
    `enabled: False` (and at least one must); a second row reading on, or a
    row with no `enabled` at all, is not a switched-off chat."""
    if not isinstance(own_chat, dict) or not own_chat.get("reenable"):
        return False
    want = own_chat.get("trigger_id")
    if not want:
        return False
    matched = [row for row in (listed or ()) if isinstance(row, dict)
               and str(row.get("id") or row.get("trigger_id")) == str(want)]
    return bool(matched) and all(row.get("enabled") is False for row in matched)


def reenable_candidates(workspace_root) -> list:
    """CHATSON1 - the chat ids a maintenance fire may switch back on.

    Every id with a stored trigger (`schedule_backend.read_trigger_map`) that
    is on the live roster (`schedule_config.DEFAULT_SCHEDULES`) - and so never
    a retired, renamed or superseded id, which the roster does not carry: a
    retired chat is the customer's to switch off (LIFECYCLE1) and a superseded
    one was switched off on purpose (MAINT1). The fire's own chat comes first;
    the rest in roster order. A customer's pause is applied by `own_chat_state`
    per id, not here."""
    try:
        from schedule_backend import read_trigger_map
        from schedule_config import DEFAULT_SCHEDULES, RETIRED_TASKS, SUPERSEDED_BY
    except Exception:  # noqa: BLE001 - an older tree: nothing to offer
        return []
    try:
        stored = read_trigger_map(workspace_root)
    except Exception:  # noqa: BLE001
        return []
    superseded = {t for ids in SUPERSEDED_BY.values() for t in ids}
    out = []
    for task_id in ["maintenance"] + [t for t in DEFAULT_SCHEDULES
                                      if t != "maintenance"]:
        if task_id in RETIRED_TASKS or task_id in superseded:
            continue
        row = stored.get(task_id)
        if isinstance(row, dict) and row.get("trigger_id"):
            out.append(task_id)
    return out


def chats_state(workspace_root, listed=None) -> dict:
    """CHATSON1 - `own_chat_state` for EVERY chat on the roster - READ ONLY.

    `{chats, reenable, check_listing, line}`: `chats` is one `own_chat_state`
    answer per candidate id (`reenable_candidates`, each carrying its
    `task_id`); `reenable` is the list of the offers among them that are not
    null - each with the `task_id` it switches on - and is EMPTY until the
    scheduler's rows are handed in as `listed` (the plan's own answer offers
    nothing and sets `check_listing` true when there is anything to look
    for, exactly as `own_chat_state` does); `line` is the ONE line naming
    every chat in `reenable`, "" when there are none. The guards are the
    per-id ones: the listing must show that chat's trigger off, and a chat
    the customer paused is never offered."""
    out = {"chats": [], "reenable": [], "check_listing": False, "line": ""}
    for task_id in reenable_candidates(workspace_root):
        state = own_chat_state(workspace_root, task_id, listed)
        out["chats"].append(state)
        if state.get("check_listing"):
            out["check_listing"] = True
        offer = state.get("reenable")
        if offer:
            out["reenable"].append(dict(offer, task_id=task_id))
    out["line"] = reenabled_line([o["task_id"] for o in out["reenable"]])
    return out


def _own_chat_steps(pair: str) -> str:
    """M1b - the fire's own chat (MUST 4, R-WALK-1), on every verdict.

    T2B FIRE3B MUST 1 (F-T2-13): the plan never carries the switch-on call.
    The step lists the scheduler's chats ONCE, hands those rows to ONE read
    of `chats_state` with `listed`, and acts only on the entries of
    `reenable` in THAT answer.

    CHATSON1 (#98): the read is `chats_state`, every chat on the roster, not
    `own_chat_state`, the fire's own; the answer's `reenable` is a list, one
    entry per chat the listing showed off, and the line names them all."""
    return (
        "    ITS OWN CHAT AND THE OTHERS (M1b), before the verdict below and "
        "on every verdict but `root_blocked`: the plan's `chats.reenable` is "
        "always empty; act on nothing in the plan itself. When "
        "`chats.check_listing` is true, list the scheduler's chats ONCE, "
        "then ask ONE question beside the data "
        "with those rows, each as {\"id\": <its id>, \"enabled\": <true or "
        "false>} and nothing else -\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"listed": [<the rows>], "workspace_root": '
                  '"<WS>"}, "name": "maintenance_dispatcher:chats_state"}') +
        "    For EACH entry of THAT answer's `reenable` (one per Command Room "
        "chat the listing showed switched off by the app after a missed run "
        "- this chat, the Morning Brief, the Inbox, any of them; never one "
        "the customer paused): make its `call` exactly, and only when that "
        "call succeeded write each of its `receipts` through the write door "
        "with the arguments it carries plus \"workspace_root\" -\n"
        + _m_line(pair, "run_writer",
                  '{"args": {<the receipt args>, "workspace_root": "<WS>"}, '
                  '"name": "' + REENABLE_WRITER + '"}') +
        "    - and, when every call succeeded, this run's final message is "
        "the answer's `line`, once, instead of `" + SILENT_CLOSE_LINE
        + "`; when only some succeeded, it is the `line` of the first entry "
        "whose call did, once; a call that failed writes no receipt and says "
        "nothing about it. `reenable` empty (every chat is on, the customer "
        "paused the rest, or none is stored) or `check_listing` false -> "
        "never switch anything on and say nothing about it.\n")


def _checked_workspace_path(workspace_path) -> Optional[str]:
    """The workspace folder's absolute path on the customer's computer, as
    the folder request names it, or None. A path with a quote, a backtick, a
    `$` or a line break is refused (`ValueError`): it is pasted into a tool
    call verbatim, so a mangled value is a stop, never an escape."""
    if workspace_path is None:
        return None
    text = str(workspace_path).strip()
    if not text:
        return None
    if any(ch in text for ch in _UNSAFE_PATH_CHARS):
        raise ValueError("workspace_path carries a character that cannot be "
                         "pasted into the folder request")
    if not _ABSOLUTE_PATH_RE.match(text):
        raise ValueError("workspace_path must be an absolute path")
    return text


def folder_step(workspace_path: Optional[str] = None) -> str:
    """F0 - the folder-request step, the visible bootloaders' Step 0 for the
    maintenance body (MUST 1). It opens the baked branch, so it comes before
    the Access preamble and before any access-layer line.

    `workspace_path` is the folder's absolute path on the customer's computer
    as the registering seat saw it (`CR_DEVICE_WORKSPACE`), handed in by the
    composer. Without it this body has no path to ask for: F0 still refuses
    to go on with no folder and stops on the same sentence (R-WALK-4 (b)),
    but it cannot make the request (a); the callers that compose the
    registered body are named in the lane record's seams."""
    path = _checked_workspace_path(workspace_path)
    stop = no_workspace_line()
    head = (
        "F0. REACH THE WORKSPACE FOLDER FIRST - before the preamble below and "
        "before any access-layer line. A scheduled run can start with no "
        "folder attached, and a run that never reaches the folder has done "
        "nothing.\n"
        "    Call `mcp__remote-devices__get_device_info`. Not in your tool "
        "list -> an older app, whose folder is mounted here: skip F0.\n")
    if path:
        ask = (
            "    Its `connectedFolders` already holds `" + path + "` -> go on "
            "to the preamble and request nothing. Otherwise call, exactly "
            "ONCE in this run:\n"
            "    `mcp__remote-devices__device_request_folder_access(paths=[\""
            + path + "\"], reason=\"" + FOLDER_REQUEST_REASON + "\")`\n"
            "    Granted -> go on. Anything else - no answer yet, a refusal, "
            "or still no folder -> post EXACTLY this as the whole final "
            "message and STOP (write nothing, read nothing, never ask a "
            "second time):\n")
    else:
        ask = (
            "    Its `connectedFolders` already holds this workspace's folder "
            "-> go on to the preamble. Otherwise this body names no folder "
            "path to request, so post EXACTLY this as the whole final "
            "message and STOP (write nothing, read nothing):\n")
    return (head + ask + "    > " + stop + "\n"
            "    The same stop, word for word, when any access-layer line in "
            "this run answers `" + NO_FOLDER_REASONS[0] + "` or `"
            + NO_FOLDER_REASONS[1] + "`: never a second request, and never "
            "the `" + SILENT_CLOSE_LINE + "` close.\n")


#: MAINTJOBS1 MUST 2 - the DOOR jobs: neither a bare script leg nor
#: prose-only. Each is run on the merged shape by three sub-steps: a READ
#: helper that plans it beside the data, the model's connector step in this
#: session, and a WRITER on the write door that lands its rows and its own
#: receipt. `job_id -> {"plan": helper, "writer": writer}`; rows are added
#: one job at a time as each is driven end to end.
#: WAVE 2 MAINTHARD2 MUST 2 (the EODHARD3 shape): the two reconcile jobs
#: hand the connector's batch AS IT CAME, staged as bytes by `args_file` in
#: the maintenance fire's own folder (`fire: maintenance`, D-W2-3), and the
#: writer reads it by `rel` and keeps the plan's fields in code. Nothing that
#: grows with the mail crosses the door as a walked argument, and nothing
#: depends on the chat trimming. The staged writers and the clear.
MAINT_FIRE = "maintenance"
MAINT_STAGE_WRITER = "eod_helpers:stage_fire_input"
MAINT_SENT_WRITER = "eod_helpers:reconcile_sent_staged"
MAINT_CHAT_WRITER = "eod_helpers:reconcile_chat_staged"
MAINT_CLEAR_WRITER = "eod_helpers:clear_fire_staging"


def _stage_recipe(kind_words: str, file_name: str) -> str:
    """The shared STAGE sentence of both reconcile legs: the batch whole, one
    list, the plan's names, the portable base64 recipe (never a python body,
    G69), and the count."""
    return (
        "STAGE the batch as the connector's answer came: ONE JSON list "
        "holding every " + kind_words + ", one object per message, in ONE "
        "stage (a batch is never split). Each object carries the plan's "
        "`message_fields` under the plan's names (where the connector spells "
        "one differently, add it under the plan's name); those fields are "
        "enough, so write nothing more: anything else an object carries is "
        "dropped in code. Write that list to a file in this session's own scratch with "
        "your own file-writing tool, then build the stage's file from it in "
        "the same scratch with `{ printf '{\"content_base64\": \"'; base64 < "
        "<the list file> | tr -d '\\n'; printf '\"}'; } > <your scratch>/"
        + file_name + "` (never a python body), and stage it with "
        "`n_returned` how many messages the connector returned (0 when it "
        "returned none):\n")


def _stage_refused_rule() -> str:
    return (
        "      The stage answered `ok` false (more than one pass can read, or "
        "the batch did not reach it) -> leave the job due and post nothing "
        "(its `line` says why in this fire's own words; the FINAL RESPONSE "
        "rule stands): run nothing more for it except the clear below, "
        "never stage it again and never hand the batch any other way. ")


def _clear_line(pair: str) -> str:
    return (
        "      Then, whatever the stage and the write answered, clear this "
        "fire's staged files ONCE, so no mail rests in the folder between "
        "fires:\n"
        + _m_line(pair, "run_writer",
                  '{"args": {"fire": "' + MAINT_FIRE + '", "workspace_root": '
                  '"<WS>"}, "name": "' + MAINT_CLEAR_WRITER + '"}'))


def _sent_steps(pair: str) -> str:
    """M2's reconcile-sent sub-steps (MAINTJOBS1 MUST 2; WAVE 2 MAINTHARD2
    MUST 2: staged by file, trimmed in code)."""
    return (
        "    reconcile-sent - PLAN:\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"workspace_root": "<WS>"}, "name": '
                  '"reconcile_sent_commitments:plan_sent_window"}') +
        "      `ready` false -> leave it due. CONNECTOR (here): load the mail "
        "connector with the host's tool search for each of the plan's "
        "`connector_queries`; for each of the workspace's mail accounts, ONE "
        "Sent fetch with the plan's `intent` (Sent mail after `after`; `ts` "
        "is the connector's own send time, `recipient_emails` the To/CC "
        "addresses verbatim). No connector in this run -> leave it due. "
        + _stage_recipe("message every account returned",
                        "cr_maint_stage_sent.json")
        + _m_line(pair, "run_writer",
                  '{"args": {"fire": "' + MAINT_FIRE + '", "kind": '
                  '"sent_messages", "n_returned": <how many messages the '
                  'connector returned>, "workspace_root": "<WS>"}, '
                  '"args_file": "<your scratch>/cr_maint_stage_sent.json", '
                  '"name": "' + MAINT_STAGE_WRITER + '"}')
        + _stage_refused_rule() +
        "WRITE by the rel the stage answered (the plan's values in a JSON "
        "file in this session's own scratch, named by `args_file`):\n"
        + _m_line(pair, "run_writer",
                  '{"args": {"fire": "' + MAINT_FIRE + '", "sent_rel": "<the '
                  'rel the stage answered>", "workspace_root": "<WS>"}, '
                  '"args_file": "<your scratch>/cr_reconcile_sent.json", '
                  '"name": "' + MAINT_SENT_WRITER + '"}') +
        "      the file holds {\"exclude_captured_since\": <the plan's "
        "fire_start>, \"provider\": <the plan's>, \"source_skill\": "
        "\"reconcile-sent\", \"user_person_id\": <the plan's>}. Score it with "
        "score_door_job (SCORE below).\n"
        + _clear_line(pair)
    )


def _chat_steps(pair: str) -> str:
    """M2's reconcile-chat sub-steps (MAINTJOBS1 MUST 2; WAVE 2 MAINTHARD2
    MUST 2: staged by file, trimmed in code)."""
    return (
        "    reconcile-chat - PLAN:\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"workspace_root": "<WS>"}, "name": '
                  '"chat_reconcile:plan_chat_scan"}') +
        "      `ready` false -> leave it due. `provider` null -> no chat "
        "backend: stage nothing and run WRITE below with `chat_rel` null (it "
        "lands the skip receipt; say nothing). Otherwise CONNECTOR (here): "
        "load the chat connector with the host's tool search for the plan's "
        "`connector_queries`, then read the messages after `after` as the "
        "plan's `scan_plan` says (its `mode`; a `per_chat_scan` is partial "
        "and its note rides the receipt). No connector in this run -> leave "
        "it due. "
        + _stage_recipe("message the read returned",
                        "cr_maint_stage_chat.json")
        + _m_line(pair, "run_writer",
                  '{"args": {"fire": "' + MAINT_FIRE + '", "kind": '
                  '"chat_messages", "n_returned": <how many messages the '
                  'connector returned>, "workspace_root": "<WS>"}, '
                  '"args_file": "<your scratch>/cr_maint_stage_chat.json", '
                  '"name": "' + MAINT_STAGE_WRITER + '"}')
        + _stage_refused_rule() +
        "WRITE by the rel the stage answered (the plan's values in a JSON "
        "file in this session's own scratch, named by `args_file`):\n"
        + _m_line(pair, "run_writer",
                  '{"args": {"chat_rel": "<the rel the stage answered>", '
                  '"fire": "' + MAINT_FIRE + '", "workspace_root": "<WS>"}, '
                  '"args_file": "<your scratch>/cr_reconcile_chat.json", '
                  '"name": "' + MAINT_CHAT_WRITER + '"}') +
        "      the file holds {\"exclude_captured_since\": <the plan's "
        "fire_start>, \"provider\": <the plan's>, \"scan_plan\": <the "
        "plan's>, \"source_skill\": \"reconcile-chat\", \"user_chat_ids\": "
        "[<the user's own ids on that chat backend>], \"user_names\": [<the "
        "user's own names there>], \"user_person_id\": <the plan's>} (without "
        "the user's own ids the leg cannot tell the user's messages from "
        "anyone else's). Score it with score_door_job (SCORE below).\n"
        + _clear_line(pair)
    )


def _capture_steps(pair: str) -> str:
    """M2's meeting-capture sub-steps (MAINTJOBS1 MUST 2). The capture leg is
    the End of Day's Phases 3-4.8, already on the door in that file's merged
    lines (ORCH2): this job runs THOSE lines, verbatim, over its own window,
    and ends with its own receipt - never a second, relaxed capture path."""
    return (
        "    meeting-capture - PLAN (the window):\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"task_id": "meeting-capture", "workspace_root": '
                  '"<WS>"}, "name": "eod_helpers:catchup_window"}') +
        "      CONNECTOR and WRITES: run skills/enable-command-room-schedules/"
        "references/orchestrator-past-meetings.md Phases 3 through 4.8 "
        "exactly as that file's merged-seat lines spell them - every line "
        "through the door, the same writers and admission gates "
        "(`meeting_capture:route_meeting_captures` and its rows through "
        "`append_jsonl`) - over THIS window instead of the close's, "
        "SILENTLY: no chat, no widget, no push; Phase C, Phase 5's day-close "
        "receipt and Phase 6 do not run here. No meeting-notes connector in "
        "this run -> leave it due. Then the pass's ONE receipt (a pack_run "
        "under meeting-capture, never under past-meetings):\n"
        + _m_line(pair, "run_writer",
                  '{"args": {"capture_leg_ms": <ms>, "n_meetings": <n>, '
                  '"n_processed": <n>, "n_skipped": <n>, "window": <the '
                  'plan\'s>, "workspace_root": "<WS>"}, "name": '
                  '"eod_incremental:log_capture_pass_receipt"}') +
        "      Score it with score_door_job (SCORE below). A pass that does "
        "not finish in this "
        "run is left DUE - in jobs_due, never in jobs_failed: on this shape "
        "meeting-capture is skill-class, and the End of Day's close captures "
        "the same meetings.\n"
    )


def _sweep_steps(pair: str) -> str:
    """M2's session-sweep sub-steps (MAINTJOBS1 MUST 2)."""
    return (
        "    session-sweep - PLAN:\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"workspace_root": "<WS>"}, "name": '
                  '"session_sweep:plan_sweep"}') +
        "      CONNECTOR (here): when this run has a session-transcript tool, "
        "list the sessions active after the plan's `after`, read each, and "
        "extract only what never became an event (skills/session-sweep/"
        "SKILL.md Step 3), one item per survivor with the plan's "
        "`item_fields`; with no such tool, `items` is empty and "
        "`sessions_scanned` is 0 - the receipt still lands. WRITE (the items "
        "in a JSON file in this session's own scratch):\n"
        + _m_line(pair, "run_writer",
                  '{"args": {"workspace_root": "<WS>"}, "args_file": "<your '
                  'scratch>/cr_session_sweep.json", "name": '
                  '"session_sweep:sweep_and_receipt"}') +
        "      the file holds {\"items\": [...], \"sessions_scanned\": "
        "<n>, \"window_desc\": <the plan's>}. Score it with "
        "score_door_job (SCORE below).\n"
    )


def _realign_steps(pair: str) -> str:
    """M2's schedule-realign sub-steps (MAINTJOBS1 SHOULD 6)."""
    return (
        "    schedule-realign - PLAN:\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"workspace_root": "<WS>"}, "name": '
                  '"maintenance_dispatcher:plan_schedule_realign"}') +
        "      `nothing_due` true -> a quiet job: write nothing and count it "
        "complete. `tasks` empty and `refused` not -> leave it due (a chat "
        "the scheduler would not move). Otherwise CONNECTOR (here): for each "
        "entry in `tasks`, "
        "make its `call` with the scheduler tool it names, exactly those "
        "arguments. No scheduler tool in this run -> leave it due. Only for "
        "a call that succeeded, WRITE each of its `receipts`, in order, "
        "through the write door with the arguments it carries plus "
        "\"workspace_root\":\n"
        + _m_line(pair, "run_writer",
                  '{"args": {<the receipt args>, "workspace_root": "<WS>"}, '
                  '"name": "schedule_refresh:log_schedule_refreshed"}') +
        _m_line(pair, "run_writer",
                '{"args": {<the receipt args>, "workspace_root": "<WS>"}, '
                '"name": "schedule_backend:record_trigger_map"}') +
        "      A call that failed writes nothing (the drift is seen again "
        "tomorrow).\n"
    )


DOOR_JOBS: dict = {
    "schedule-realign": {
        "plan": "maintenance_dispatcher:plan_schedule_realign",
        "writer": "schedule_backend:record_trigger_map",
        "writers": ("schedule_refresh:log_schedule_refreshed",
                    "schedule_backend:record_trigger_map"),
        "validator": None,
        "steps": _realign_steps,
    },
    "session-sweep": {
        "plan": "session_sweep:plan_sweep",
        "writer": "session_sweep:sweep_and_receipt",
        "validator": "session_sweep:validate_sweep_ran",
        "steps": _sweep_steps,
    },
    "meeting-capture": {
        "plan": "eod_helpers:catchup_window",
        "writer": "eod_incremental:log_capture_pass_receipt",
        "validator": "receipts:iter_receipts",
        "steps": _capture_steps,
    },
    "reconcile-chat": {
        "plan": "chat_reconcile:plan_chat_scan",
        # WAVE 2 MAINTHARD2 MUST 2: the staged writer, fed by a stage
        "writer": MAINT_CHAT_WRITER,
        "writers": (MAINT_STAGE_WRITER, MAINT_CHAT_WRITER,
                    MAINT_CLEAR_WRITER),
        "validator": "chat_reconcile:validate_chat_reconcile_ran",
        "steps": _chat_steps,
    },
    "reconcile-sent": {
        "plan": "reconcile_sent_commitments:plan_sent_window",
        # WAVE 2 MAINTHARD2 MUST 2: the staged writer, fed by a stage
        "writer": MAINT_SENT_WRITER,
        "writers": (MAINT_STAGE_WRITER, MAINT_SENT_WRITER,
                    MAINT_CLEAR_WRITER),
        "validator": "reconcile_sent_commitments:validate_reconcile_ran",
        "steps": _sent_steps,
    },
}


def _plain_plan(value):
    """A dataclass plan (`CallPlan` / `Refusal`) as plain JSON - the door
    stringifies anything that is not a dict, list or scalar."""
    import dataclasses

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _plain_plan(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _plain_plan(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_plan(v) for v in value]
    return value


def plan_schedule_realign(workspace_root, on_date=None) -> dict:
    """The schedule-realign job PLANNED beside the data - READ ONLY
    (MAINTJOBS1 SHOULD 6; on `workspace_access.RUN_HELPER_ALLOWLIST`).

    `schedule_backend.plan_realign` over the STORED trigger map, answered as
    plain JSON the fire can act on: one `tasks` entry per drifted chat with
    the scheduler `call` to make (`tool` + `args`) and, in order, the
    `receipts` it then owes - each a write-door name and its arguments
    (`schedule_refresh:log_schedule_refreshed`, then
    `schedule_backend:record_trigger_map`). `refused` names a drifted chat
    whose cron cannot be re-projected (left alone, never rewritten).
    `nothing_due` is true when no stored offset drifted: a quiet job, so the
    fire writes nothing and counts it complete. Writes nothing."""
    import schedule_backend as sb

    day = on_date
    if isinstance(day, str) and day:
        day = _dt.date.fromisoformat(day[:10])
    out = sb.plan_realign(workspace_root=workspace_root, on_date=day)
    plans = [_plain_plan(p) for p in out.get("plans") or []]
    receipts = [_plain_plan(r) for r in out.get("receipts") or []]
    tasks = []
    for plan in plans:
        task_id = plan.get("task_id")
        owed = []
        for name, kwargs in receipts:
            if (kwargs or {}).get("task_id") != task_id:
                continue
            args = dict(kwargs)
            if name == "schedule_refresh.log_schedule_refreshed":
                args.setdefault("source_skill", "maintenance")
            owed.append({"name": name.replace(".", ":", 1), "args": args})
        tasks.append({"task_id": task_id,
                      "call": {"tool": plan.get("tool"), "args": plan.get("args")},
                      "receipts": owed})
    refused = [_plain_plan(d) for d in out.get("drifted") or []
               if isinstance(d, dict) and d.get("refusal") is not None]
    return {"job_id": "schedule-realign", "tasks": tasks, "refused": refused,
            "nothing_due": not tasks and not refused, "ready": True}


def job_class(job_id: str) -> str:
    """`script` (a `JOB_LEGS` row, run by `run_job`), `door` (a `DOOR_JOBS`
    row) or `skill` (prose only - left due on the merged shape)."""
    if job_id in JOB_LEGS:
        return "script"
    if job_id in DOOR_JOBS:
        return "door"
    return "skill"


def skill_jobs_left_due() -> list:
    """The maintenance jobs with NO script leg — skills, run by a model on a
    seat that holds the files. The merged branch cannot run them from here,
    so on that shape they stay due (the maintenance chain is PARTIAL there:
    ORCH2 fix pass 1, ruling R-ORCH2-2). Derived from the registry, never
    typed, so the record's list is the code's list."""
    return [job for job in MAINTENANCE_JOBS if job_class(job) == "skill"]


# ---------------------------------------------------------------------------
# T3 FIRE3 MUST 4 (FIRE1 S-6, MAINTJOBS1 S-4) - a job run through the door is
# scored beside the data, never by whether a receipt showed up in the chat
# ---------------------------------------------------------------------------
#
# THE WALK. The merged fire listed `dedup-apply` as FAILED: it ran, found
# nothing stamped and - by its registry's own words - left no trace. The M2
# text told the fire to score "by its own validator", a read it cannot make
# through the door (no validator is on the read list), so the fire scored by
# receipt presence and called a correct quiet run a failure.

#: The three answers `score_door_job` gives, and the lists M3 files them in:
#: `completed` -> jobs_completed, `due` -> jobs_due only, `failed` ->
#: jobs_failed.
DOOR_JOB_OUTCOMES = ("completed", "due", "failed")

#: Jobs whose run with nothing to do leaves NO receipt by design and says so
#: in the registry (`dedup-apply`: "leaves NO trace on a run with nothing
#: stamped"). With no receipt and no refusal they are `completed`.
NOTHING_STAMPED_JOBS = frozenset({"dedup-apply"})


def _envelope_refused(envelope) -> bool:
    """A door envelope (or a `run_job` answer inside it) that says the job
    did not run: `ok: false`, `ran: false`, or a non-zero exit."""
    if not isinstance(envelope, dict):
        return False
    if envelope.get("ok") is False:
        return True
    result = envelope.get("result", envelope)
    if isinstance(result, dict):
        if result.get("ran") is False:
            return True
        code = result.get("returncode")
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return True
    return False


def _score_since(workspace_root, since_iso) -> Optional[_dt.datetime]:
    """The fire's start as a local naive datetime: `since_iso` when given,
    else the FIREGAP2 start marker this fire landed (`mark_fire_start`).
    None when neither parses - then no receipt can be proven to be THIS
    run's, and none is counted (the safe direction)."""
    text = since_iso
    if not text:
        try:
            marker = json.loads(_marker_path(workspace_root).read_text(
                encoding="utf-8"))
            text = marker.get("started_at") if isinstance(marker, dict) else None
        except Exception:  # noqa: BLE001
            text = None
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = _dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _to_local_naive(parsed)


def _realign_landed_since(workspace_root, since) -> bool:
    """T3 FIRE3 fix round 1 (REVIEW N-1): schedule-realign's door path writes
    no `pack_run`; what it owes after a call that succeeded is a
    `schedule_refreshed` row (`data.field == "cron_utc"`, `source_skill`
    `maintenance`, `plan_schedule_realign`) plus the trigger-map re-stamp.
    That row, landed at or after the fire's start, is the job's receipt.
    Read through `events_io` (`iter_receipts` skips the type)."""
    try:
        import events_io
        from receipts import event_dt
        from schedule_refresh import SCHEDULE_REFRESHED
        for ev in events_io.iter_events(workspace_root):
            if not isinstance(ev, dict) or ev.get("type") != SCHEDULE_REFRESHED:
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            skill = ev.get("source_skill") or data.get("source_skill")
            if data.get("field") != "cron_utc" or skill != "maintenance":
                continue
            when = _to_local_naive(event_dt(ev))
            if when is not None and when >= since:
                return True
    except Exception:  # noqa: BLE001 - an unreadable ledger proves nothing
        return False
    return False


#: W2 SCORE2 MUST 2 (REVIEW_T3_FIRE3 N-4). The SCRIPT jobs whose registry
#: rows say they leave no trace on a quiet run (`question-expiry` 490,
#: `binding-gauge` 614, `daily-measure` 665). As script entries they have no
#: plan, so the M2 text's `nothing_due` is never true for them and a correct
#: quiet run scored `failed / no_receipt`. With no receipt and a call that
#: ran clean they are `completed / quiet_run`. A sibling of `QUIET_RUN_JOBS`,
#: not that set: it names `schedule-realign` (whose door path owes a
#: `schedule_refreshed` row, FIRE3 L-1) and not `daily-measure`, and DOORS1
#: pins its membership.
QUIET_SCRIPT_JOBS = frozenset({"question-expiry", "binding-gauge",
                               "daily-measure"})


def _ran_clean(refused, envelope, nothing_due) -> bool:
    """The call is known to have run and not been refused: its plan said
    `nothing_due`, the fire said `refused: false` in so many words (the M2
    text's own field), or an envelope reads `ran: true` with returncode 0.
    An omitted `refused` proves nothing (a malformed call stays failed)."""
    if nothing_due or refused is False:
        return True
    if not isinstance(envelope, dict) or _envelope_refused(envelope):
        return False
    result = envelope.get("result", envelope)
    if not isinstance(result, dict) or result.get("ran") is not True:
        return False
    code = result.get("returncode")
    return isinstance(code, int) and not isinstance(code, bool) and code == 0


def score_door_job(workspace_root, job_id="", since_iso=None, *,
                   refused: Optional[bool] = None, envelope=None,
                   nothing_due: bool = False) -> dict:
    """Score ONE maintenance job the merged fire ran through the door - READ
    ONLY (on `workspace_access.RUN_HELPER_ALLOWLIST`).

    Answers `{job_id, outcome, why}`, `outcome` one of `DOOR_JOB_OUTCOMES`:
      * `due` / `skill_class_left_due` with `detail: "refused"` - W2 SCORE2
        MUST 1 (D-T2B-8, REVIEW_T2B_REBASE3 N-3): a `MERGED_SKILL_CLASS_JOBS`
        job the door refused or that crashed, on a merged seat (this
        process's environment; through the door the child carries the merged
        env), unless its own receipt landed since the start. The receipt
        writer (`merged_rescore`) would move it to due anyway; the scorer now
        says so first, so the fire's answer and its receipt agree. `detail`
        keeps the true refusal visible. On a legacy seat: as below;
      * `failed` / `refused` - the door refused the job or it did not run
        (`refused` true, or an `envelope` with `ok: false` / `ran: false` /
        a non-zero exit);
      * `completed` / `receipt` - a receipt of the job's OWN types
        (`receipts.RECEIPT_TYPES`) landed since the fire's start
        (`since_iso`, else the fire's start marker);
      * `completed` / `nothing_stamped` - a `NOTHING_STAMPED_JOBS` job with
        no receipt and no refusal (the registry's own words);
      * `completed` / `quiet_run` - a `QUIET_RUN_JOBS` job whose own plan or
        return said nothing was due (`nothing_due`), through
        `job_counts_as_complete`; or (W2 SCORE2 MUST 2) a `QUIET_SCRIPT_JOBS`
        job with no receipt whose call ran clean (`_ran_clean`);
      * `due` / `skill_class_left_due` - a `MERGED_SKILL_CLASS_JOBS` job with
        no receipt (D-6: never failed for not finishing here);
      * `failed` / `no_receipt` - anything else with no receipt, including a
        call that names no job (`job_id` defaults to "" so a malformed call
        answers the safe direction instead of raising through the door).
    Writes nothing."""
    job = str(job_id or "")
    out = {"job_id": job, "outcome": "failed", "why": "no_receipt"}
    did_refuse = bool(refused) or _envelope_refused(envelope)
    # SCORE2 MUST 1: a skill-class job on a merged seat is left due whatever
    # the door answered, so its refusal falls through to the receipt read.
    left_due_anyway = (did_refuse and job in MERGED_SKILL_CLASS_JOBS
                       and merged_seat())
    if did_refuse and not left_due_anyway:
        out["why"] = "refused"
        return out
    since = _score_since(workspace_root, since_iso)
    if since is not None:
        try:
            from receipts import RECEIPT_TYPES, iter_receipts
            types = (RECEIPT_TYPES.get(job) or {}).get("types") or frozenset()
            rows = iter_receipts(workspace_root, task_ids=[job])
        except Exception:  # noqa: BLE001 - an unreadable ledger proves nothing
            types, rows = frozenset(), []
        for row in rows:
            when = _to_local_naive(row.get("dt"))
            if row.get("type") in types and when is not None and when >= since:
                out.update(outcome="completed", why="receipt")
                return out
        if job == "schedule-realign" and _realign_landed_since(workspace_root,
                                                               since):
            out.update(outcome="completed", why="receipt")
            return out
    if left_due_anyway:
        out.update(outcome="due", why="skill_class_left_due", detail="refused")
        return out
    if job in NOTHING_STAMPED_JOBS:
        out.update(outcome="completed", why="nothing_stamped")
    elif job in QUIET_SCRIPT_JOBS and _ran_clean(refused, envelope,
                                                 nothing_due):
        # SCORE2 MUST 2: a quiet script run is not a phantom failure.
        out.update(outcome="completed", why="quiet_run")
    elif job_counts_as_complete(job, receipt_validated=False,
                                run_reported_nothing_due=bool(nothing_due)):
        out.update(outcome="completed", why="quiet_run")
    elif job in MERGED_SKILL_CLASS_JOBS:
        out.update(outcome="due", why="skill_class_left_due")
    return out


def _score_steps(pair: str) -> str:
    """M2's scoring rule for every `script` and `door` entry (MUST 4)."""
    return (
        "    SCORE each `script` and `door` entry the same way, after its "
        "write, by this answer - never by whether a receipt appeared in "
        "this chat:\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"job_id": "<the entry\'s job_id>", '
                  '"nothing_due": <true only when its plan said nothing_due>, '
                  '"refused": <true when its write answered ok false, ran '
                  'false, or a non-zero returncode>, "workspace_root": '
                  '"<WS>"}, "name": '
                  '"maintenance_dispatcher:score_door_job"}') +
        "      `outcome` completed -> jobs_completed; due -> jobs_due only; "
        "failed -> jobs_failed.\n"
        # W2 SCORE2 MUST 1 (D-T2B-8): the scorer answers `due` for this class
        # itself, so the fire's lists and its receipt agree.
        "      A skill-class job ("
        + ", ".join(sorted(MERGED_SKILL_CLASS_JOBS)) +
        ") on this shape is left due whatever the door answered: its answer "
        "is due, and it is filed in jobs_due, never in jobs_failed.\n"
    )


def merged_branch_steps(writer_id: Optional[str] = None,
                        writer_derivation: Optional[str] = None, *,
                        workspace_path: Optional[str] = None) -> str:
    """The maintenance prompt's MERGED-SEAT branch, rendered (ORCH2).

    On the Require-this-computer shape the maintenance fire runs where the
    files are NOT: every in-process step of the legacy prompt opens nothing
    there. This branch is the same fire through the access layer — the
    due-jobs question through the read door (`catch_up_plan`, HEAL1's), each
    script job through the write door (`run_job`, one entry for the whole
    registry), and the ONE `maintenance_run` receipt composed beside the data
    by its own writer and landed through `append_jsonl`. Composed into the
    registered prompt by `schedule_config.compose_silent_task_prompt`; the
    legacy steps below it are untouched and remain the legacy seat's path.

    Its HEAD is the canonical v6 Access preamble and the rendering rule
    (`MERGED_RENDER_RULE`): this prompt is registered standalone, so without
    them the branch below named a `CR_ENV` nothing had resolved and a device
    shell nothing had said how to reach (review F-4). The branch is PARTIAL
    by design: script jobs run through the door; the skill jobs
    (`skill_jobs_left_due()`) are left due for a seat that holds the files.

    THE PAIR IS BAKED IN (MF-26, the merge-fix reader's R-1). `plan` derives
    the writer pair in this session only while `CR_DEVICE_WORKSPACE` is still
    exported in the same shell - and "no earlier export survived" is a real
    shape. With no pair every writer refuses, no `maintenance_run` lands and
    the chain goes receipt-dark. So the REGISTERING seat's pair (handed in by
    the caller from `schedule_config.silent_prompt_pair` - never read from the
    synced identity file here, D-1) goes in front of `python3` on the render
    form and on every M line, through `writer_identity.bake_pair`. Both or
    neither: a half or malformed pair is `ValueError(BAD_PAIR_LINE)`. No pair
    -> today's text, byte for byte (every seat with no pair to hand).

    T2 FIRE1 MUST 1: a baked branch opens with `folder_step(workspace_path)`
    (F0). `workspace_path` is ignored with no pair: the pair-less text stays
    today's, byte for byte.
    """
    baked = writer_id is not None or writer_derivation is not None
    if baked:
        # MAINTJOBS1 - the merged shape's branch is the BAKED one (a pair is
        # handed in only by a registering merged seat); every change below
        # is gated on it, and the pair-less text is today's, byte for byte.
        # T2 FIRE1 MUST 1: the baked branch OPENS with F0 (the folder
        # request), before the preamble and every access-layer line.
        from writer_identity import bake_pair
        return bake_pair(folder_step(workspace_path)
                         + _baked_branch_text(_pair_vars()), writer_id,
                         writer_derivation)
    pair = ""
    text = (
        "ACCESS PREAMBLE (CONTRACT Rule 22 v6) - run this block first, on "
        "every seat; it resolves PLUGIN_ROOT and CR_ENV, and WORKSPACE on a "
        "seat whose files are on this filesystem:\n"
        "```bash\n" + access_preamble_block() + "\n```\n"
        + _merged_render_rule(pair) + (BAKED_PAIR_RULE if baked else "") +
        "WHERE THE FILES ARE decides which branch runs. When the Access "
        "preamble resolves CR_ENV=merged_cloud, or the workspace is reached "
        "through the device tools, the files are on the customer's machine "
        "and steps 0-4 below open nothing here: run THIS branch (M1-M3) "
        "instead, every verb rendered by `workspace_access.py plan <verb>` "
        "and pasted verbatim, INCLUDING the variables in front of python3 "
        "(they carry the writer identity and CR_FIRED_VIA=scheduled to the "
        "host that holds the data). Never run a job's command in this "
        "session's own shell, never `python3 -c` against the workspace, "
        "never compose a receipt by hand.\n"
        "M1. Ask what is due, beside the data:\n"
        f"    {pair}{_PLAN_HEAD} run_helper --json "
        "'{\"args\": {\"surface\": \"maintenance\", \"workspace_root\": "
        "\"<WS>\"}, \"name\": \"maintenance_dispatcher:catch_up_plan\"}'\n"
        "    `reason` root_blocked -> step 1's registration sentence, no "
        "receipt, STOP. `staleness.stale` false -> this slot was already "
        "served (the min-gap twin): run M3 with empty lists and "
        "\"skipped_min_gap\": true, then exit silently.\n"
        "M2. For each entry in `jobs`, in order, one at a time: an entry "
        "whose `leg` is non-empty runs through the WRITE door -\n"
        f"    {pair}{_PLAN_HEAD} run_writer --json "
        "'{\"args\": {\"job_id\": \"<the entry's job_id>\", "
        "\"workspace_root\": \"<WS>\"}, \"name\": "
        "\"maintenance_dispatcher:run_job\"}'\n"
        "    The job writes its own receipt through its own writer; score it "
        "exactly as step 3 says (job_counts_as_complete over its own "
        "validator's answer). An entry whose `leg` is empty is a SKILL: it is "
        "not run from here on this shape - leave it due (in jobs_due, in "
        "neither jobs_completed nor jobs_failed) for a fire on a seat that "
        "holds the files. `refused_container` entries stay due the same "
        "way.\n"
        "M3. ONE maintenance_run receipt, composed beside the data by its "
        "own writer, then landed through the one door:\n"
        f"    {pair}{_PLAN_HEAD} run_helper --json "
        "'{\"args\": {\"fired_via\": \"scheduled\", \"jobs_completed\": "
        "[<ids>], \"jobs_due\": [<ids>], \"jobs_failed\": [<ids>], "
        "\"skipped_disabled\": [<from the plan>], \"skipped_min_gap\": false, "
        "\"triggered_by\": null, \"workspace_root\": \"<WS>\"}, \"name\": "
        "\"morning_brief_helpers:plan_maintenance_receipt\"}'\n"
        f"    {pair}{_PLAN_HEAD} append_jsonl --json "
        "'{\"holder\": \"maintenance\", \"rel\": \"_hq/data/events.jsonl\", "
        "\"rows\": [<every row in rows from the receipt answer above, in "
        "order>]}'\n"
        "Otherwise - a legacy or local seat, whose files are on this "
        "filesystem - run steps 0-4 below exactly as written.\n\n"
    )
    return text


def _m_line(pair: str, verb: str, payload: str) -> str:
    """One M-branch verb line: four spaces, the pair, the rendered head."""
    return f"    {pair}{_PLAN_HEAD} {verb} --json '{payload}'\n"


def _baked_branch_text(pair: str) -> str:
    """The merged branch as a registering merged seat bakes it (MAINTJOBS1).

    Differences from the pair-less text, each gated on the pair:
      * M1 keys on ONE field, `verdict` (MUST 1, R-RW3-7), and lands the
        fire-start marker the read door held (`mark_fire_start`);
      * M2 runs each job by its `class` - `script` through `run_job`, `door`
        through its own plan helper, connector step and writer (MUST 2),
        `skill` left due;
      * M3 composes with `skipped_min_gap` from the verdict.
    """
    return (
        "ACCESS PREAMBLE (CONTRACT Rule 22 v6) - run this block first, on "
        "every seat; it resolves PLUGIN_ROOT and CR_ENV, and WORKSPACE on a "
        "seat whose files are on this filesystem:\n"
        "```bash\n" + access_preamble_block() + "\n```\n"
        + _merged_render_rule(pair) + BAKED_PAIR_RULE +
        "WHERE THE FILES ARE decides which branch runs. When the Access "
        "preamble resolves CR_ENV=merged_cloud, or the workspace is reached "
        "through the device tools, the files are on the customer's machine "
        "and steps 0-4 below open nothing here: run THIS branch (M1-M3) "
        "instead, every verb rendered by `workspace_access.py plan <verb>` "
        "and pasted verbatim, INCLUDING the variables in front of python3 "
        "(they carry the writer identity and CR_FIRED_VIA=scheduled to the "
        "host that holds the data). Never run a job's command in this "
        "session's own shell, never an improvised python body against the "
        "workspace, "
        "never compose a receipt by hand.\n"
        "M1. Ask what is due, beside the data:\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"surface": "maintenance", "workspace_root": '
                  '"<WS>"}, "name": "maintenance_dispatcher:catch_up_plan"}') +
        _own_chat_steps(pair) +
        "    ONE field decides: `verdict`. Never decide from `staleness`, "
        "`min_gap` or `catch_up` - they explain the verdict and never "
        "overrule it.\n"
        "    `skip_served` -> this slot was already served: run M3 with empty "
        "lists and \"skipped_min_gap\": true, then end.\n"
        "    `root_blocked` -> write nothing, run nothing, end (the next "
        "surface the customer opens names it).\n"
        "    `nothing_due` -> run M3 with empty lists and "
        "\"skipped_min_gap\": false, then end.\n"
        "    `run` -> when `fire_marker.held` is true, land this fire's start "
        "marker first:\n"
        + _m_line(pair, "run_writer",
                  '{"args": {"workspace_root": "<WS>"}, "name": '
                  '"maintenance_dispatcher:mark_fire_start"}') +
        "    then M2.\n"
        "M2. For each entry in `jobs`, in order, one at a time, by its "
        "`class`. `script` runs through the WRITE door -\n"
        + _m_line(pair, "run_writer",
                  '{"args": {"job_id": "<the entry\'s job_id>", '
                  '"workspace_root": "<WS>"}, "name": '
                  '"maintenance_dispatcher:run_job"}') +
        "    The job writes its own receipt through its own writer; score it "
        "with score_door_job (SCORE below). `skill` is not run from here on "
        "this shape - "
        "leave it due (in jobs_due, in neither jobs_completed nor "
        "jobs_failed) for a fire on a seat that holds the files. "
        "`refused_container` entries stay due the same way.\n"
        + _door_job_steps(pair) + _score_steps(pair) +
        "M2b. On `run`, after the jobs, sweep the lock litter (moves only, "
        "never a delete; a file it cannot move is named in its `left`):\n"
        + _m_line(pair, "run_writer",
                  '{"args": {"workspace_root": "<WS>"}, "name": '
                  '"cleanup_actions:sweep_lock_litter"}') +
        "M3. ONE maintenance_run receipt, composed beside the data by its "
        "own writer, then landed through the one door:\n"
        + _m_line(pair, "run_helper",
                  '{"args": {"fired_via": "scheduled", "jobs_completed": '
                  '[<ids>], "jobs_due": [<ids>], "jobs_failed": [<ids>], '
                  '"skipped_disabled": [<from the plan>], "skipped_min_gap": '
                  'false, "triggered_by": null, "workspace_root": "<WS>"}, '
                  '"name": "morning_brief_helpers:plan_maintenance_receipt"}') +
        _m_line(pair, "append_jsonl",
                '{"holder": "maintenance", "rel": "_hq/data/events.jsonl", '
                '"rows": [<every row in rows from the receipt answer above, in '
                'order>]}') +
        "    Then the run ends.\n"
        + final_response_rule() +
        "Otherwise - a legacy or local seat, whose files are on this "
        "filesystem - run steps 0-4 below exactly as written.\n\n"
    )


def _door_job_steps(pair: str) -> str:
    """M2's `door` entries, one sub-step block per `DOOR_JOBS` row, in
    registry order (MAINTJOBS1 MUST 2). Empty until a door job exists."""
    if not DOOR_JOBS:
        return ""
    out = ("    `door` runs in three sub-steps, each job its own: its PLAN "
           "beside the data (a read), its CONNECTOR step here in this "
           "session, and its WRITER through the write door, which lands the "
           "job's rows and its own receipt under this task's identity. A "
           "`door` job whose connector is not in this run stays due exactly "
           "like a `skill` entry.\n")
    for job_id in MAINTENANCE_JOBS:
        spec = DOOR_JOBS.get(job_id)
        if spec:
            out += spec["steps"](pair)
    return out


def _quoted(value) -> str:
    """A workspace path as one shell word. A basename with a space in it is
    the ordinary case here, not the exotic one."""
    text = str(value)
    return text if (text and " " not in text) else '"' + text + '"'


#: MAINTJOBS1 MUST 1 (R-RW3-7, "one answer"). The ONE field a scheduled
#: maintenance fire obeys. Every other field on a scheduled plan explains it
#: and never contradicts it: `jobs` is empty on every verdict but `run`, and
#: `catch_up` is true exactly when the verdict is `run`.
VERDICT_RUN = "run"
VERDICT_SKIP_SERVED = "skip_served"
VERDICT_ROOT_BLOCKED = "root_blocked"
VERDICT_NOTHING_DUE = "nothing_due"
SCHEDULED_VERDICTS = (VERDICT_RUN, VERDICT_SKIP_SERVED, VERDICT_ROOT_BLOCKED,
                      VERDICT_NOTHING_DUE)

#: The surface a scheduled fire's plan is asked for. Only the maintenance
#: task's own question becomes a SCHEDULED plan; a typed brief, an end of day
#: or a weekly recap stays a catch-up whatever run mode its own fire carries.
SCHEDULED_PLAN_SURFACES = frozenset({"run-maintenance"})


def _plan_fired_via(explicit) -> Optional[str]:
    """`scheduled` when the fire says so - the argument, else the run mode
    the door forwards (`CR_FIRED_VIA`); None otherwise (today's answer)."""
    word = explicit if explicit is not None else os.environ.get("CR_FIRED_VIA")
    return "scheduled" if str(word or "").strip().lower() == "scheduled" else None


def catch_up_plan(workspace_root, surface,
                  now: Optional[_dt.datetime] = None,
                  mode: Optional[str] = None,
                  machine: Optional[str] = None,
                  *,
                  fired_via: Optional[str] = None) -> dict:
    """WHAT a typed surface owes the plumbing before it gathers. READ/COMPUTE
    ONLY — this decides, it never runs a job and never writes a receipt.

    ONE ANSWER ON A SCHEDULED FIRE (MAINTJOBS1 MUST 1, ruling R-RW3-7). The
    maintenance task's own fire asks this with surface `maintenance` and the
    run mode `scheduled` (the argument, or `CR_FIRED_VIA` forwarded by the
    door). That is not a person asking, so it gets `_scheduled_catch_up_plan`:
    `fired_via: scheduled`, the staleness AND min-gap gates both bind, and ONE
    field - `verdict` - decides. On 2026-09-23 this function answered such a
    fire `catch_up: true` with six jobs AND `staleness.stale: false`, labelled
    it `manual`, and the fire obeyed the second half. Every other call - the
    typed `run maintenance`, every catch-up surface - is today's answer, byte
    for byte.

    It is on `workspace_access.RUN_HELPER_ALLOWLIST` for that reason: on a
    merged seat the model asks this question through the access layer with
    one `plan run_helper` line, executes the returned jobs' own legs the same
    way, and writes the single receipt with `plan append_jsonl`.

    THE THREE GATES, in order, each of which alone stops the catch-up:

      1. The surface. A read surface (`staff meeting`, `what's on my plate`)
         never runs a job; an unknown word never runs a job.
      2. Staleness (`maintenance_staleness`). A book whose most recent
         upkeep slot was served is not caught up again, however much a
         long-cadence job says it is due.
    Gates 2 and 3 do not bind `run maintenance` — see
    `CATCH_UP_ALWAYS_SURFACES`: that one is a person asking, and a person who
    asks gets a fire.

      3. The min-gap guard, ASKED AS A SCHEDULED FIRE ON PURPOSE. MAINTGAP1
         ruling 2 exempts `manual` unconditionally, because a Run Now press
         is a person asking for a fire and must always get one. A catch-up
         is not that: nobody asked for it, it rides in front of a surface
         they DID ask for, and running the Sunday family twice in fifteen
         minutes because two surfaces were opened in a row is exactly the
         pair-overlap MAINTGAP1 exists to prevent. So first-fire-wins holds
         here and the guard is asked with the word that makes it answer.

    Then the family filter (R-M2) and, in `container` mode, the write-class
    refusal — both inside `dispatch_plan`, so there is one home for each.

    `mode` is normally RESOLVED for the caller by
    `surface_drivers.resolved_host_mode`, which asks the access layer about
    the book this call was handed (fix round 1, REVIEW H-1 — nothing used to
    pass a mode at all). Two container shapes, and they are different
    answers:

      * THE REAL ONE — a cloud container with no folder attached. The root
        this call names is not on this host, the root check blocks, and the
        plan comes back with the access layer's own `refused_reason` /
        `refused_next` plus one sentence, never `root_blocked` (which would
        tell a customer their workspace is broken when it is simply
        elsewhere).
      * THE BELT — a container that somehow does hold the book. Then the
        write-class jobs are refused one by one and named. The access layer
        refuses a container-mode `run_helper` / `append_jsonl` before this
        code is reached, so that branch is a second fence and is recorded as
        one; it is not the thing standing between a container and a write.
    """
    now = _coerce_now(now)
    canonical = catch_up_surface(surface)
    if (canonical in SCHEDULED_PLAN_SURFACES
            and _plan_fired_via(fired_via) == "scheduled"):
        return _scheduled_catch_up_plan(workspace_root, canonical, now=now,
                                        mode=mode, machine=machine)
    family = CATCH_UP_SURFACES.get(canonical or "")
    base = {
        "surface": canonical,
        "triggered_by": canonical,
        "read_surface": canonical in CATCH_UP_READ_SURFACES,
        "family": family,
        "mode": mode,
        "fired_via": "manual",
        "now": now.isoformat(),
        "catch_up": False,
        "jobs": [],
        "held_sunday": [],
        "refused_container": [],
        "refused_line": "",
        "refused_reason": "",
        "refused_next": "",
        "host_mode": mode,
        "skipped_disabled": [],
        "staleness": None,
        "min_gap": None,
        "reason": "",
    }
    if canonical is None:
        base["reason"] = "unknown_surface"
        return base
    if canonical in CATCH_UP_READ_SURFACES:
        base["reason"] = "read_surface"
        base["staleness"] = maintenance_staleness(workspace_root, now=now)
        return base
    staleness = maintenance_staleness(workspace_root, now=now)
    base["staleness"] = staleness
    asked_for = canonical in CATCH_UP_ALWAYS_SURFACES
    if not asked_for and not staleness["stale"]:
        base["reason"] = "not_stale"
        return base
    if not asked_for:
        min_gap = _check_min_gap(workspace_root, now=now,
                                 fired_via="scheduled")
        base["min_gap"] = min_gap
        if min_gap.get("skipped"):
            base["reason"] = "min_gap"
            return base
    plan = dispatch_plan(workspace_root, now=now, machine=machine,
                         fired_via="manual", mode=mode, repair_root=False)
    base["skipped_disabled"] = list(plan.get("skipped_disabled") or [])
    base["refused_container"] = list(plan.get("refused_container") or [])
    base["refused_line"] = plan.get("refused_line") or ""
    base["refused_reason"] = plan.get("refused_reason") or ""
    base["refused_next"] = plan.get("refused_next") or ""
    if plan.get("root_repair", {}).get("blocked"):
        # FIX ROUND 1, REVIEW H-1 — THE ONE CONTAINER CASE THAT IS REAL.
        # A cloud container has no folder attached, so the root this call was
        # handed is not on this host and the root check blocks. Answering
        # `root_blocked` there says "your workspace is broken", which is
        # false and unactionable; the true answer is the access layer's own
        # one — the work belongs on the machine that holds the files — and
        # it is the state the merged seat is actually in tonight.
        if mode == CONTAINER_MODE:
            base["reason"] = CONTAINER_REFUSAL_REASON
            base["refused_line"] = CONTAINER_REFUSAL_LINE
            base["refused_reason"] = CONTAINER_REFUSAL_REASON
            base["refused_next"] = CONTAINER_REFUSAL_NEXT
            return base
        base["reason"] = "root_blocked"
        return base
    held = sunday_family() if family == "weekday" else frozenset()
    jobs, held_ids = [], []
    for d in plan.get("due") or []:
        if d["job_id"] in held:
            held_ids.append(d["job_id"])
        else:
            jobs.append(d)
    # FIX3 F3-6: every job comes back with the command that runs it and the
    # two variables that make its own receipt honest. A surface that pastes
    # `job["leg"]` cannot accidentally record a person's typed brief as a
    # scheduled fire, which is what all four upkeep receipts did on
    # 2026-09-21.
    for job in jobs:
        leg, leg_env = job_leg(job.get("job_id", ""), workspace_root,
                               canonical, fired_via="manual")
        job["leg"] = leg
        job["env"] = leg_env
    base["jobs"] = jobs
    # The family filter outranks the container refusal for anything it holds
    # back: a weekday surface neither RUNS a Sunday job nor REFUSES one, it
    # holds it for the two doors that own it. `dispatch_plan` has no surface
    # and so cannot make that distinction — it refuses every write job in the
    # container — and a refused list carrying jobs this surface was never
    # going to run would read as "the container is why cleanup did not run",
    # which is false.
    #
    # Gated on `held` (the FAMILY), never on `held_ids` (what happened to be
    # left in `due`): in container mode a Sunday job that writes has already
    # been moved out of `due` into the refused list, so it never reaches the
    # loop above. Today four of the six Sunday jobs are read-class and the
    # distinction is invisible; on a registry where every Sunday job writes,
    # gating on `held_ids` would put the whole family on a weekday brief's
    # refused list and name none of them as held.
    if held:
        for j in base["refused_container"]:
            if j in held and j not in held_ids:
                held_ids.append(j)
        base["refused_container"] = [j for j in base["refused_container"]
                                     if j not in held]
        if not base["refused_container"]:
            base["refused_line"] = ""
            base["refused_reason"] = ""
            base["refused_next"] = ""
    base["held_sunday"] = held_ids
    if not jobs:
        base["reason"] = "nothing_due"
        return base
    base["catch_up"] = True
    base["reason"] = "ok"
    return base


def _scheduled_catch_up_plan(workspace_root, canonical: str, *,
                             now: _dt.datetime, mode: Optional[str] = None,
                             machine: Optional[str] = None) -> dict:
    """The maintenance task's own scheduled fire, planned. READ/COMPUTE ONLY
    under the helper door (the fire-start marker is HELD there - see below).

    The gates, in order, and the verdict each gives:
      1. staleness - the most recent slot was served -> `skip_served`;
      2. min-gap   - a predecessor fire started under MIN_GAP_MINUTES before
                     this slot (marker or receipt) -> `skip_served`;
      3. the root  - the registration check (read-only) blocks ->
                     `root_blocked` (in `container` mode the access layer's
                     own refusal rides `refused_*`, as on a catch-up);
      4. due-ness  - nothing due -> `nothing_due`, else `run`.
    `jobs` is empty on every verdict but `run`; each job's `leg` ends
    `--fired-via scheduled` with no `--triggered-by` (nobody asked), and its
    `class` says how the fire runs it (`job_class`).

    THE FIRE-START MARKER (FIREGAP2) is written here exactly as a scheduled
    `dispatch_plan` writes it - once both gap gates and the root gate have
    cleared, whatever is due. Off the door (a legacy seat's in-shell call)
    it lands in this call. Under the helper door `write_fire_start_marker`
    HOLDS it (a read door writes nothing), `fire_marker.held` says so, and
    the fire lands it through the write door (`mark_fire_start`) before its
    first job.
    """
    base = {
        "surface": canonical,
        "triggered_by": None,
        "read_surface": False,
        "family": CATCH_UP_SURFACES.get(canonical),
        "mode": mode,
        "fired_via": "scheduled",
        "now": now.isoformat(),
        "verdict": VERDICT_SKIP_SERVED,
        "catch_up": False,
        "jobs": [],
        "held_sunday": [],
        "refused_container": [],
        "refused_line": "",
        "refused_reason": "",
        "refused_next": "",
        "host_mode": mode,
        "skipped_disabled": [],
        "staleness": None,
        "min_gap": None,
        "fire_marker": None,
        "reason": "",
        # T2 FIRE1 MUST 4 (R-WALK-1): the fire's own chat, on every verdict.
        "own_chat": own_chat_state(workspace_root),
        # CHATSON1 (#98): every roster chat, the same way; M1b reads this one.
        "chats": chats_state(workspace_root),
    }
    staleness = maintenance_staleness(workspace_root, now=now)
    base["staleness"] = staleness
    min_gap = _check_min_gap(workspace_root, now=now, fired_via="scheduled")
    base["min_gap"] = min_gap
    if not staleness["stale"]:
        base["reason"] = "not_stale"
        return base
    if min_gap.get("skipped"):
        base["reason"] = "min_gap"
        return base
    # Read-only (`repair_root=False`) and exempt from the min-gap guard it
    # already cleared above; the marker is this function's own write below.
    plan = dispatch_plan(workspace_root, now=now, machine=machine, mode=mode,
                         repair_root=False, fired_via="manual")
    base["skipped_disabled"] = list(plan.get("skipped_disabled") or [])
    base["refused_container"] = list(plan.get("refused_container") or [])
    base["refused_line"] = plan.get("refused_line") or ""
    base["refused_reason"] = plan.get("refused_reason") or ""
    base["refused_next"] = plan.get("refused_next") or ""
    if plan.get("root_repair", {}).get("blocked"):
        base["verdict"] = VERDICT_ROOT_BLOCKED
        base["skipped_disabled"] = []
        if mode == CONTAINER_MODE:
            base["reason"] = CONTAINER_REFUSAL_REASON
            base["refused_line"] = CONTAINER_REFUSAL_LINE
            base["refused_reason"] = CONTAINER_REFUSAL_REASON
            base["refused_next"] = CONTAINER_REFUSAL_NEXT
            return base
        base["reason"] = "root_blocked"
        return base
    base["fire_marker"] = write_fire_start_marker(workspace_root, now=now)
    jobs = list(plan.get("due") or [])
    for job in jobs:
        leg, leg_env = job_leg(job.get("job_id", ""), workspace_root, None,
                               fired_via="scheduled")
        job["leg"] = leg
        job["env"] = leg_env
        job["class"] = job_class(job.get("job_id", ""))
        # T2B FIRE3B MUST 3 (D-T2B-6): meeting-capture keeps `class: door`
        # (the merged fire does run it through the door); the flag says what
        # the receipt does when it does not finish (`merged_rescore`), so the
        # plan and the receipt writer agree. Absent on every other job.
        if job.get("job_id") in MERGED_SKILL_CLASS_JOBS:
            job["left_due_if_unfinished"] = True
    if not jobs:
        base["verdict"] = VERDICT_NOTHING_DUE
        base["reason"] = "nothing_due"
        return base
    base["jobs"] = jobs
    base["verdict"] = VERDICT_RUN
    base["catch_up"] = True
    base["reason"] = "ok"
    return base


def mark_fire_start(workspace_root) -> dict:
    """The WRITE door's half of a scheduled plan (MAINTJOBS1 MUST 1): land
    this fire's FIREGAP2 start marker, which `catch_up_plan` held because a
    read door writes nothing. On `workspace_access.RUN_WRITER_ALLOWLIST`.
    The writer is named first (`receipts.require_writer_identity`): a merged
    seat with no forwarded identity refuses in one sentence and writes
    nothing. Returns `write_fire_start_marker`'s answer."""
    from receipts import require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    return write_fire_start_marker(workspace_root, now=_coerce_now(None))


def run_catch_up(workspace_root, surface, *, runner,
                 now: Optional[_dt.datetime] = None,
                 mode: Optional[str] = None,
                 machine: Optional[str] = None) -> dict:
    """Run what `catch_up_plan` returned, in registry order, then receipt ONCE.

    `runner(job_dict)` executes one job exactly as `cleanup/SKILL.md` Step 0
    and the registered maintenance prompt do — this module has never executed
    a job and does not start here; it is handed the executor. The runner's
    return is read for the two facts the scorer needs, in either of the two
    shapes a caller naturally has:

      * a dict: `{"receipt_validated": bool, "reported_nothing_due": bool}`
      * a bool: the job's own validator's answer, nothing quiet claimed.

    Scoring goes through `job_counts_as_complete` — the one predicate, never
    widened here (DOORS1 1.5: seven phantom `question-expiry` failures rode
    a caller that judged by receipt presence alone).

    THE RECEIPT IS ONE, AND IT IS ONLY WRITTEN WHEN JOBS RAN. A plan that
    caught nothing writes nothing: the watchdog reads `maintenance_run` for
    task freshness, and an empty receipt from a surface that did no upkeep
    would make a dead scheduler look alive — the same reason cleanup Step 0
    writes no receipt for a nothing-due manual poke, and the same shape as
    M's own book on 2026-09-19.
    """
    plan = catch_up_plan(workspace_root, surface, now=now, mode=mode,
                         machine=machine)
    plan["ran"] = []
    plan["completed"] = []
    plan["failed"] = []
    plan["receipt"] = None
    if not plan.get("catch_up"):
        return plan
    for job in plan["jobs"]:
        job_id = job["job_id"]
        plan["ran"].append(job_id)
        try:
            result = runner(job)
        except Exception:  # noqa: BLE001 — a job that blew up stays due
            plan["failed"].append(job_id)
            continue
        if isinstance(result, dict):
            validated = bool(result.get("receipt_validated"))
            quiet = bool(result.get("reported_nothing_due"))
        else:
            validated, quiet = bool(result), False
        if job_counts_as_complete(job_id, receipt_validated=validated,
                                  run_reported_nothing_due=quiet):
            plan["completed"].append(job_id)
        else:
            plan["failed"].append(job_id)
    plan["receipt"] = maintenance_receipt(
        workspace_root,
        jobs_due=plan["ran"],
        jobs_completed=plan["completed"],
        jobs_failed=plan["failed"],
        skipped_disabled=plan.get("skipped_disabled"),
        fired_via="manual",
        triggered_by=plan["triggered_by"],
        now=now,
    )
    return plan


def _effective_fired_via(explicit):
    """The seat's answer for a composer whose default claims a slot (M-3).
    Import-tolerant: a receipt never fails over its run mode."""
    try:
        from receipts import effective_fired_via
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit


def merged_seat(env: Optional[dict] = None) -> bool:
    """True on a merged seat: the sandbox VM (`receipts._is_vm_seat`) or an
    environment ENV1 reads as `merged_cloud`. Import-tolerant: a receipt
    never fails over this question, and an unreadable seat is not merged."""
    try:
        from receipts import _is_vm_seat

        if _is_vm_seat(env):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from writer_identity import detected_mode

        environ = dict(os.environ) if env is None else dict(env)
        return detected_mode(environ) == "merged_cloud"
    except Exception:  # noqa: BLE001
        return False


def merged_rescore(jobs_due, jobs_failed, *, env: Optional[dict] = None,
                   jobs_completed=None):
    """`(jobs_due, jobs_failed, rescored)` as the receipt records them.

    T2B FIRE3B MUST 2 (F-T2-12, the receipt half). FIX-7 ruled
    `MERGED_SKILL_CLASS_JOBS` skill-class on the merged fire: one it does not
    finish is left DUE, in neither completed nor failed. The walk's 12:45
    fire fetched fifteen meetings, landed nothing, and handed
    `meeting-capture` in `jobs_failed`; the receipt took it as handed. So on
    a merged seat such a job is moved out of `jobs_failed`, kept (or put) in
    `jobs_due`, and named in `rescored`. Every other job, and every job on a
    legacy or local seat, is recorded exactly as handed.

    W2 SCORE2 MUST 1: `score_door_job` now answers `due` for this class on a
    merged seat, so a fire that follows it hands nothing to move and
    `rescored` stays empty; this move is the belt. `jobs_completed` (FIRE3B
    N-5): a job handed in BOTH completed and failed still leaves failed and
    is still named in `rescored`, but is not added to due."""
    due = list(jobs_due or [])
    failed = list(jobs_failed or [])
    completed = set(_job_ids(jobs_completed))
    moving = [j for j in failed if j in MERGED_SKILL_CLASS_JOBS]
    if not moving or not merged_seat(env):
        return due, failed, []
    rescored = []
    for job in moving:
        if job not in rescored:
            rescored.append(job)
        if job not in due and job not in completed:
            due.append(job)
    return due, [j for j in failed if j not in MERGED_SKILL_CLASS_JOBS], \
        rescored


def maintenance_receipt(
    workspace_root,
    jobs_due: Optional[Iterable] = None,
    jobs_completed: Optional[Iterable] = None,
    jobs_failed: Optional[Iterable] = None,
    *,
    skipped_disabled: Optional[Iterable] = None,
    # NOT `"scheduled"` (re-verify M-3). A literal default here is an
    # EXPLICIT value by the time the resolver sees it, so it short-circuits
    # the merged-seat branch and this composer records a slot nobody claimed.
    # `effective_fired_via(None)` answers `"scheduled"` on every non-VM seat,
    # so a legacy caller that omits the argument is byte-identical.
    fired_via: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    root_repair_state: Optional[str] = None,
    skipped_min_gap: bool = False,
    triggered_by: Optional[str] = None,
    env: Optional[dict] = None,
) -> dict:
    """Append THE one `maintenance_run` audit event for this fire, via the
    locked append gate (receipts.log_receipt -> event_gate.append_event).

    T2B FIRE3B MUST 2 (F-T2-12, the receipt half): on a merged seat a job in
    `MERGED_SKILL_CLASS_JOBS` handed in `jobs_failed` is left DUE instead
    (`merged_rescore`), and `data.rescored` names what moved. `env` is the
    seat's environment for that one question (None reads this process's);
    legacy and local seats are unchanged.

    This is the dispatcher's own receipt — it records what was due and what
    landed, and the watchdog reads it for task freshness. It is NOT a job
    receipt: a job counts as completed only when its own validator confirmed
    its own receipt; never list a job in jobs_completed without that.

    `root_repair_state` (SPEC PATHREPAIR1 v2 item 2) rides this SAME
    receipt as `root_repair.get("state")` — "ALIVE" / "DEAD" / "UNKNOWN" —
    when the caller supplies one. This is the "one diagnostic count on the
    maintenance receipt, zero-written" ruling 1/2/3 asks for on an UNKNOWN
    fire: the vantage's own verdict is durably visible on the audit trail
    without a NEW event ever being written and without alarming anyone.
    Omitted (never `None` on the record) when the caller doesn't pass one —
    a receipt written outside the dispatcher preamble (or by an older
    caller) never gains a field it has no opinion about.

    `skipped_min_gap` (MAINTGAP1 §0 ruling 1) — True when this fire's
    dispatch was refused by `_check_min_gap` (the predecessor maintenance
    fire's own start landed under MIN_GAP_MINUTES ago). Unlike
    `root_repair_state`, this field is ALWAYS written (default False, never
    omitted): `_newest_fired_at_slot` — one of the guard's two OWN detectors
    for the next fire, the other being FIREGAP2's fire-start marker
    (`_marker_predecessor_slot`, a separate sidecar file, not this receipt)
    — reads THIS field's sibling `fired_at_slot`, not this field itself, so
    there is no back-compat reason to hide it on an ordinary fire, and an
    explicit `False` is what lets a reader tell "checked, not skipped" apart
    from "an old receipt with no opinion" at a glance.
    """
    now = _coerce_now(now)
    try:
        task_cron = DEFAULT_SCHEDULES[MAINTENANCE_TASK_ID]["cron"]
        slots = expected_fires(task_cron, now=now, count=1)
        fired_at_slot = slots[0].isoformat() if slots else None
    except (KeyError, CronParseError):
        fired_at_slot = None

    from receipts import log_receipt

    due_ids, failed_ids, rescored = merged_rescore(
        _job_ids(jobs_due), _job_ids(jobs_failed), env=env,
        jobs_completed=_job_ids(jobs_completed))
    extra_data = {
        "fired_at_slot": fired_at_slot,
        "jobs_due": due_ids,
        "jobs_completed": _job_ids(jobs_completed),
        "jobs_failed": failed_ids,
        "skipped_disabled": _job_ids(skipped_disabled),
        # CHATSCAN1 V1b — computed at WRITE time from the live registry,
        # never handed in by the caller. A caller-supplied value would be
        # a claim; this is a measurement, and it is the difference between
        # a fire that is genuinely complete and one that is missing a
        # closure leg nobody noticed had gone.
        "roster_gap": roster_gap(),
        "reconcile_legs": list(RECONCILE_LEGS),
        "skipped_min_gap": bool(skipped_min_gap),
    }
    if root_repair_state is not None:
        extra_data["root_repair_state"] = root_repair_state
    if rescored:
        # T2B FIRE3B MUST 2 - the correction is on the book: which jobs the
        # caller handed in as failed and this receipt left due instead.
        # Omitted when nothing moved, so no other receipt gains the field.
        extra_data["rescored"] = rescored
    if triggered_by:
        # HEAL1 (SPEC_NIGHTM2 §4 amendment b) — ADDITIVE, and omitted on every
        # fire that was not triggered by a typed surface, so no existing
        # reader gains a field it has no opinion about. `fired_via` still says
        # HOW the fire ran (`manual`); this says WHO asked for it, and it is
        # the only way a later reader can tell a catch-up in front of a brief
        # apart from someone typing `run maintenance`.
        extra_data["triggered_by"] = str(triggered_by)

    return log_receipt(
        workspace_root,
        MAINTENANCE_TASK_ID,
        receipt_type=RECEIPT_EVENT_TYPE,
        # M-3: the parameter above defaults to `scheduled`, which is a
        # CLAIM on a seat where nothing said so. A merged seat reads
        # its environment instead; every other seat is unchanged.
        fired_via=_effective_fired_via(fired_via),
        extra_data=extra_data,
    )


def validate_maintenance_ran(
    workspace_root,
    since: Optional[_dt.datetime] = None,
) -> dict:
    """Read the newest `maintenance_run` audit event back and confirm the
    dispatcher actually ran (the Bug #98-v3 posture: enforcement binds to the
    substrate artifact, never a narrated sentence).

    `since` (naive = machine-local) restricts ok to an event newer than that
    instant — pass the pre-fire clock to confirm THIS fire's receipt landed.
    Returns {"ok": bool, "reason": str|None, "dt": iso|None, plus the payload
    lists when an event exists}.
    """
    from receipts import iter_receipts

    if since is not None and since.tzinfo is not None:
        since = _to_local_naive(since)

    newest = None
    newest_dt = None
    for r in iter_receipts(workspace_root, task_ids=[MAINTENANCE_TASK_ID]):
        if r["type"] != RECEIPT_EVENT_TYPE:
            continue
        dt_local = _to_local_naive(r["dt"]) if r["dt"] is not None else None
        if newest is None or (
            dt_local is not None and (newest_dt is None or dt_local > newest_dt)
        ):
            newest, newest_dt = r, dt_local
    if newest is None:
        return {"ok": False, "dt": None,
                "reason": "no maintenance_run audit event — the dispatcher did not run"}
    if since is not None and (newest_dt is None or newest_dt <= since):
        return {"ok": False, "dt": newest_dt.isoformat() if newest_dt else None,
                "reason": "newest maintenance_run audit event predates this fire"}
    data = newest["raw"].get("data") if isinstance(newest["raw"].get("data"), dict) else {}
    gap = data.get("roster_gap") or []
    out = {
        "ok": True,
        "reason": None,
        "dt": newest_dt.isoformat() if newest_dt else None,
        "fired_at_slot": data.get("fired_at_slot"),
        "jobs_due": data.get("jobs_due") or [],
        "jobs_completed": data.get("jobs_completed") or [],
        "jobs_failed": data.get("jobs_failed") or [],
        "skipped_disabled": data.get("skipped_disabled") or [],
        # HEAL1 — the surface that asked for this fire, when one did.
        "triggered_by": data.get("triggered_by"),
        # CHATSCAN1 V1b — a closure leg missing from the roster is reported
        # every time this receipt is read back, not only at the fire that
        # wrote it.
        "roster_gap": list(gap),
        "reconcile_legs": data.get("reconcile_legs") or list(RECONCILE_LEGS),
    }
    if gap:
        # NOT ok. A run that closed commitments through some of its declared
        # channels and silently not the others is the "visibly incomplete vs
        # silently partial" distinction this whole check exists to draw — and
        # a validator that answered True here would be the thing making it
        # silent.
        out["ok"] = False
        out["reason"] = (
            "this maintenance run reconciled only part of what it is supposed "
            "to: no leg is registered for " + ", ".join(gap))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: print the due-jobs plan for a workspace as JSON.

    python3 maintenance_dispatcher.py <workspace_root> [--now ISO]
    """
    import argparse

    parser = argparse.ArgumentParser(description="Command Room maintenance due-jobs plan")
    parser.add_argument("workspace_root", help="absolute path to the workspace root")
    parser.add_argument("--now", default=None,
                        help="frozen machine-local ISO datetime (testing/simulation)")
    parser.add_argument("--triggered-by", default=None,
                        dest="triggered_by",
                        help="the surface that asked for this run")
    # KEEPS its `scheduled` default (FIX3 F3-6 point 4): this CLI IS
    # the scheduled task's own body, whose prompt exports
    # CR_FIRED_VIA=scheduled, and a catch-up reaches the dispatcher
    # through `catch_up_plan` rather than through here.
    parser.add_argument("--fired-via", default="scheduled", dest="fired_via",
                        help="'scheduled' (cron) or 'manual' (a person asked) — MAINTGAP1: "
                             "manual fires are exempt from the min-gap guard")
    # T2 FIRE1 SHOULD (F-OFF-8 second half): step 0's mount-freshness
    # preflight as a plugin command, so the baked body carries no improvised
    # `python3 -c` body for a fire to copy.
    parser.add_argument("--preflight", action="store_true",
                        help="print substrate_health.preflight_freshness as JSON")
    args = parser.parse_args(argv)
    if args.preflight:
        import substrate_health as _sh

        print(json.dumps(_sh.preflight_freshness(args.workspace_root)))
        return 0
    # FIX3 F3-6: export what this run was asked by, so every composer
    # below reads it from one place instead of being threaded through
    # a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    now = None
    if args.now:
        try:
            now = _dt.datetime.fromisoformat(args.now)
        except ValueError:
            print(json.dumps({"error": f"unparseable --now value: {args.now!r}"}))
            return 2
    plan = dispatch_plan(args.workspace_root, now=now, fired_via=args.fired_via)
    print(json.dumps(plan, indent=2))
    return 0


__all__ = [
    "MAINTENANCE_JOBS",
    "OPTIONAL_JOBS",
    "MAINTENANCE_TASK_ID",
    "RECEIPT_EVENT_TYPE",
    "RECONCILE_LEGS",
    "FIRE_MARKER_RELPATH",
    "FIRE_MARKER_STALE_MINUTES",
    "roster_gap",
    "dispatch_plan",
    "due_jobs",
    "maintenance_receipt",
    "merged_seat",
    "merged_rescore",
    "QUIET_RUN_JOBS",
    "MERGED_SKILL_CLASS_JOBS",
    "DOOR_JOB_OUTCOMES",
    "NOTHING_STAMPED_JOBS",
    "REENABLE_REASON",
    "REENABLE_WRITER",
    "score_door_job",
    "job_counts_as_complete",
    "CATCH_UP_SURFACES",
    "CATCH_UP_SURFACE_ALIASES",
    "CATCH_UP_READ_SURFACES",
    "CATCH_UP_ALWAYS_SURFACES",
    "CONTAINER_MODE",
    "CONTAINER_REFUSAL_LINE",
    "CONTAINER_REFUSAL_REASON",
    "CONTAINER_REFUSAL_NEXT",
    "CONTAINER_REFUSED_JOBS_NAMED",
    "SUNDAY_FAMILY_NAMED",
    "apply_class_jobs",
    "sunday_family",
    "catch_up_surface",
    "catch_up_family",
    "maintenance_staleness",
    "catch_up_plan",
    "run_catch_up",
    "run_job",
    "merged_branch_steps",
    "FOLDER_REQUEST_REASON",
    "NO_FOLDER_REASONS",
    "folder_step",
    "no_workspace_line",
    "workspace_reached",
    "fire_close_line",
    "REENABLED_LINE_FORM",
    "reenabled_line",
    "own_chat_state",
    "needs_reenable",
    "reenable_candidates",
    "chats_state",
    "validate_maintenance_ran",
    "write_fire_start_marker",
]


if __name__ == "__main__":
    sys.exit(main())
