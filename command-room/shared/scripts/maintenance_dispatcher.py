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
now run as JOBS inside one `maintenance` task (cron `45 6,12,17 * * *`), and
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
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from schedule_config import DEFAULT_SCHEDULES, CronParseError  # noqa: E402
from task_watchdog import (  # noqa: E402
    MIN_GAP_MINUTES,
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
    # On Sundays it still runs BEFORE `age-out` (insertion order), which is
    # that sibling's ordering contract.
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
    # SWEEPSCHED1 — the CONFIRMED-pile drain. Sunday slot, ordered
    # IMMEDIATELY AFTER `review-expiry` and last of the Sunday group, for the
    # same reason `review-expiry` sits where it does and one more besides:
    # it argues FROM SILENCE, so it must read a substrate every other Sunday
    # leg has already finished writing — and `review-expiry` (daily since
    # UNCONFEXP1, so due on Sundays too) runs one step earlier because a row
    # it lapses is one this job then has no business looking at. Two drains
    # arguing from silence in the same fire have to be ordered, not
    # interleaved.
    #
    # WEEKLY, not weekdaily: the bar is 30 quiet days, so a daily pass would
    # re-derive the same pile six extra times a week to find nothing new.
    #
    # CONFIRM-FIRST for its first three fires — it proposes, closes nothing,
    # and leaves the offer as its receipt line; from the fourth it applies
    # unattended and reversibly. The counter is the job's own receipts, so
    # there is no flag anywhere for anyone to forget to flip.
    #
    # It rides the already-authorized `maintenance` taskId, so it registers
    # ZERO scheduled tasks on any machine, and a job id is not a
    # DEFAULT_SCHEDULES key, so `load_schedule_config`'s renamed-predecessor
    # carry-over cannot reach it. Entry point:
    # `commitment_backlog_sweep.run_age_out_job(ws, apply=True)`.
    "age-out": {
        "skill": "commitment-backlog-sweep commitment amnesty "
                 "(shared/scripts/commitment_backlog_sweep.py age-out "
                 "--apply — dry-run without the flag)",
        "nominal_cron": "0 17 * * 0",
        "description": "let agreed work that has gone silent age out "
                       "(reversible, one batch; proposes before it acts)",
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
    # Inserted BEFORE monthly-report so `age-out` keeps its pinned "review-
    # expiry then age-out, adjacent" ordering contract intact, and the
    # monthly reporting leg stays the registry's caboose.
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
    # Nominal midnight on the 1st -> due at the first fire on/after the 1st.
    # PARTITIONED (CATCHUP1 F-3): one report per missed month, each labelled
    # with its own month. A machine closed across a 1st loses that month
    # entirely without this — the next fire produces the NEWEST prior month
    # and the skipped one is never written.
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
    """
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
                           machine: Optional[str] = None) -> dict:
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
    """
    try:
        import path_repair as pr
    except Exception as exc:  # noqa: BLE001 — never let an import hiccup
        # block a fire; the fire-time guard degrades to "unchecked", not
        # "crashed".
        return {"blocked": False, "ok": True, "checked": False, "error": str(exc)}
    return pr.fire_time_guard(workspace_root, now=now, machine=machine)


def dispatch_plan(workspace_root, now: Optional[_dt.datetime] = None,
                  *, machine: Optional[str] = None,
                  fired_via: str = "scheduled") -> dict:
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

    A job registered `partitioned` (CATCHUP1) carries two more keys:
    `periods` — every unserved nominal slot since its last receipt, ISO,
    OLDEST FIRST, one deliverable owed per entry — and `periods_capped`,
    True when more periods were missed than `catchup.DEFAULT_PERIOD_CAP` and
    the oldest were dropped. The list always contains at least the slot the
    job is due for, so the prompt can iterate `periods` unconditionally for
    a partitioned job.
    """
    now = now or _now_local()
    if now.tzinfo is not None:
        now = _to_local_naive(now)

    root_repair = _check_and_repair_root(workspace_root, now=now, machine=machine)
    if root_repair.get("blocked"):
        return {
            "now": now.isoformat(),
            "due": [],
            "skipped_disabled": [],
            "root_repair": root_repair,
            "min_gap": {"skipped": False, "gap_minutes": None,
                       "predecessor_slot": None, "this_slot": None},
        }

    min_gap = _check_min_gap(workspace_root, now=now, fired_via=fired_via)
    if min_gap.get("skipped"):
        return {
            "now": now.isoformat(),
            "due": [],
            "skipped_disabled": [],
            "root_repair": root_repair,
            "min_gap": min_gap,
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
    return {
        "now": now.isoformat(),
        "due": due,
        "skipped_disabled": skipped_disabled,
        "root_repair": root_repair,
        "min_gap": min_gap,
    }


def due_jobs(workspace_root, now: Optional[_dt.datetime] = None,
            *, machine: Optional[str] = None,
            fired_via: str = "scheduled") -> list[dict]:
    """The jobs due at this fire, in execution order (see MAINTENANCE_JOBS —
    order is the contract, never parallelize). A fire with nothing due is a
    fast no-op: write the maintenance_receipt with empty lists and exit.
    `fired_via` — see `dispatch_plan` (MAINTGAP1 min-gap guard exemption)."""
    return dispatch_plan(workspace_root, now=now, machine=machine,
                         fired_via=fired_via)["due"]


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


def maintenance_receipt(
    workspace_root,
    jobs_due: Optional[Iterable] = None,
    jobs_completed: Optional[Iterable] = None,
    jobs_failed: Optional[Iterable] = None,
    *,
    skipped_disabled: Optional[Iterable] = None,
    fired_via: str = "scheduled",
    now: Optional[_dt.datetime] = None,
    root_repair_state: Optional[str] = None,
    skipped_min_gap: bool = False,
) -> dict:
    """Append THE one `maintenance_run` audit event for this fire, via the
    locked append gate (receipts.log_receipt -> event_gate.append_event).

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
    now = now or _now_local()
    if now.tzinfo is not None:
        now = _to_local_naive(now)
    try:
        task_cron = DEFAULT_SCHEDULES[MAINTENANCE_TASK_ID]["cron"]
        slots = expected_fires(task_cron, now=now, count=1)
        fired_at_slot = slots[0].isoformat() if slots else None
    except (KeyError, CronParseError):
        fired_at_slot = None

    from receipts import log_receipt

    extra_data = {
        "fired_at_slot": fired_at_slot,
        "jobs_due": _job_ids(jobs_due),
        "jobs_completed": _job_ids(jobs_completed),
        "jobs_failed": _job_ids(jobs_failed),
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

    return log_receipt(
        workspace_root,
        MAINTENANCE_TASK_ID,
        receipt_type=RECEIPT_EVENT_TYPE,
        fired_via=fired_via,
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
    parser.add_argument("--fired-via", default="scheduled", dest="fired_via",
                        help="'scheduled' (cron) or 'manual' (Run Now) — MAINTGAP1: "
                             "manual fires are exempt from the min-gap guard")
    args = parser.parse_args(argv)
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
    "validate_maintenance_ran",
    "write_fire_start_marker",
]


if __name__ == "__main__":
    sys.exit(main())
