#!/usr/bin/env python3
"""apply-choices audit-event builder (T2.2 — FS-18a).

WHY THIS EXISTS
RV-5 caught a FALSE-SUCCESS audit: a `confirm proposal` whose handler refused
(nothing written) was logged `outcome: "ok"` in the `apply_choices_applied`
event — the batch reporter recorded the DISPATCH, not the WRITE. An audit
event that says "ok" over a refusal poisons every downstream reader
(usage-report, corrections passes, the change feed's honesty).

THE RULE (pinned in apply-choices Step 5): an action's `outcome` derives from
the HANDLER'S ACTUAL RESULT — the writer's return dict / raised error —
never from "the handler was called". This module mechanizes the derivation
so the mapping can't drift per session.

USAGE (apply-choices Step 5):

    from apply_audit import build_apply_choices_applied_event
    event = build_apply_choices_applied_event(
        source="cr-brain",
        actions=[{"n": n, "action": verb, "handler_result": result_or_error},
                 ...])
    # append via atomic_append_jsonl / event_gate (seq/ts auto-stamped)

`handler_result` per action is REQUIRED — one of:
  - the handler's return dict (its `status` field drives the outcome),
  - an Exception instance or its string (→ "error"),
  - None ONLY for pure no-write actions (skip/snooze acks) → "ok".

stdlib only.
"""
from __future__ import annotations

from typing import Any, List

# APPLYAUDIT1 (defect register, bug_received seq 9517 item C) — THE VOCABULARY
# IS NOW CENSUS-DERIVED, NOT INCIDENT-DERIVED.
#
# Three weeks of live Apply actions audited 31.3% "errored" because the three
# sets below had drifted ~54 statuses behind the handlers apply-choices
# actually dispatches into: `mine` (confirm_commitment_owner → "confirmed"),
# `make task` / `promote` (promote_task_to_commitment → "reclassified" /
# "already_task" / "already_promise"), `push to [date]` (no status-returning
# writer at all) and `add to my list` (orphan_note returned `outcome`, not
# `status`) each errored on EVERY dispatch, successes included. The fix was
# never one more word: v5.9.3 added exactly one ("done"), WATCHGATE and ARCHFIX
# one each before it, and the class survived all three.
#
# So the vocabulary is now maintained against a CENSUS, and the census is a
# fence: `tests/run_fs18_outcome_coverage_test.py` derives the module list from
# the apply-choices dispatch table itself (skills/apply-choices/SKILL.md),
# walks every handler those modules expose plus everything they call, and
# fails if ANY status literal it finds is missing from exactly one of the three
# sets below. Add a handler status anywhere on that rail and the suite goes red
# until it is classified here. That is the point — a status must never again
# reach `derive_outcome` unclassified and be told "error" by fall-through.
#
# The fall-through itself is UNCHANGED and stays never-optimistic (:derive_
# outcome). It now means what it always claimed to: a genuinely unknown word.

# status strings that mean "the write landed" (the handlers' own vocabulary).
_OK_STATUSES = frozenset({
    "ok", "closed", "resolved", "applied", "added", "created", "updated",
    "proposed", "archived", "undone", "reopened", "cleared", "dismissed",
    "snoozed", "sent", "drafted", "registered", "merged", "reassigned",
    "split", "promoted",
    # PLATE1 P3: `commitment_state.disown_commitment` (`not mine` on the
    # plate) — one commitment_updated landed: owner cleared, question
    # written, row parked. A write that happened; the page-set stops
    # offering the row on THIS page (it re-renders under PARKED next fire).
    "disowned",
    # WATCHGATE: a weak proposal answered by a bulk gesture is PARKED on
    # watch rather than closed. That is a write that landed and a row that
    # has been dealt with — the page-set should stop offering it, exactly as
    # it stops offering a closed one.
    "watching",
    # BACKFILL2: `backfill_widget.apply_choices` — a `not this project` tap
    # whose rejection LANDED in the binding_backfill_run receipt (the
    # precision dataset's "no" half). A write that happened and a row that
    # is dealt with; the page-set should stop offering it.
    "recorded",
    # DONE1 v5.9.3: `needs_review_queue.done_items` renames its landed closure
    # from the writer's "closed" to the verb's own word — `{"status": "done"}`
    # is a commitment_updated + commitment_resolved pair already on disk. It
    # was the ONE handler status the audit vocabulary never learned, so a
    # correct `already done` was logged outcome "error", n_errors 1 (caught
    # live 2026-08-05), and page_snapshot kept offering a row whose write had
    # landed.
    "done",
    # --- APPLYAUDIT1: the landed-write words the census found unclassified ---
    #   confirmed        commitment_state.confirm_commitment_owner — the `mine`
    #     verb. 20 of the register's 93 false errors, and the nastiest shape:
    #     `confirmed_open` (a REFUSAL) was classified while `confirmed` (the
    #     landed write) was not, so the vocabulary knew the failure word and
    #     not the success word.
    #   reclassified     commitment_state.promote_task_to_commitment — `make
    #     task` / `promote`; an additive commitment_reclassified marker.
    #   superseded       commitment_state.supersede_commitment — the merge leg.
    #   received         commitment_state.mark_partial_received.
    #   restored         commitment_state.restore_review_flags — the un-confirm.
    #   flagged          commitment_state.flag_duplicate_for_review.
    #   subitems_added   commitment_state.add_subitems — the parent stays open
    #     BY DESIGN; the children landing is the write.
    #   deferred         commitment_state.apply_later, defer leg (APPLYAUDIT1
    #     part 3) — commitment_updated carrying data.new_due.
    #   noted            orphan_note.reroute_orphan_note — `add to my list`;
    #     one `note` event on the resolved person/thread.
    #   held             mute_ledger.hold_item — a dated chat_dismissal IS the
    #     write; "held" is not a refusal (that is held_weak_evidence).
    #   actioned         balance.record_actioned — the follow-on linkage.
    #   moved            deal_state.set_stage.
    #   reported         objective_state.record_report.
    #   rebound          objective_state.rebind_objective.
    #   written          day_intent.write_day_intent — the tomorrow block.
    #   active           org_writer.create_org RETURNS THE NEW RECORD, whose
    #     own `status` field is "active". The record is the handler_result, so
    #     this string reaches derive_outcome and means the org was created.
    #   complete         commitment_backlog_sweep's run receipt — the sweep
    #     finished (its refusal twin, "blocked", is already classified below).
    "confirmed", "reclassified", "superseded", "received", "restored",
    "flagged", "subitems_added", "deferred", "noted", "held", "actioned",
    "moved", "reported", "rebound", "written", "active", "complete",
    # PERSONLOOP1 — person_candidates.resolve_candidate on the `not a person`
    # answer. ONE person_candidate_suppressed row landed, and the row must
    # stop being offered: the question has been answered permanently.
    "suppressed",
    # HELDREVIEW1 — the held-tier review's one verb and the two switches
    # beside it. `captured` is an objection landing (one held_review_objection
    # row; the commitment itself is deliberately untouched — the write is the
    # note, and there is nothing else it was supposed to do). `enabled` is
    # `turn_on_held` having gone through held_tier.enable_held_routing, which
    # is a routing write and a receipt.
    "captured", "enabled",
})
# statuses that mean "nothing needed writing" — honest no-ops, counted apart.
_NOOP_STATUSES = frozenset({
    "already_resolved", "already_closed", "already_inactive", "duplicate_open",
    "suppressed_cooldown", "already_merged", "noop",
    # WATCHGATE: the row was already parked, so this answer wrote nothing.
    # An honest no-op, not a failure and not a second park.
    "already_watching",
    # PLATE1 P3: a second `not mine` on an already-disowned row writes
    # nothing — an honest no-op, never a second question.
    "already_disowned",
    # ARCHFIX: thread_archive.archive_thread on an already-archived thread
    # writes nothing — no second status_change. Without this row the honest
    # no-op maps to the unknown-status default ("error"), inflating n_errors
    # and leaving an archived project still offered in the page-set.
    "already_archived",
    # DONE1 v5.9.3: `confirm_items` / `done_items` report an id that is no
    # longer a queue member as `not_pending` — their own docstrings call it
    # "idempotent-safe … an honest ack, not a second tombstone". Every branch
    # that produces it (already confirmed, already closed, never in the queue)
    # leaves the row OUT of the pending_review set this page-set offers, so
    # counting it as landed can never hide a row still awaiting this queue's
    # decision. Nothing was written, so it is a no-op, not an "ok".
    "not_pending",
    # --- APPLYAUDIT1: the idempotent no-ops the census found unclassified ---
    # Every one of these is a handler saying "the workspace is ALREADY in the
    # state you asked for". Nothing was written and nothing is owed, so the
    # page-set must stop offering the row — the same reason `already_resolved`
    # has always suppressed. Counting them as errors is what kept re-offering
    # rows the user had already answered, which is how they got clicked twice.
    #   already_task / already_promise / already_scheduling / already_agenda —
    #     promote_task_to_commitment, built as "already_" + new_kind, so a
    #     literal grep CANNOT see any of them. The census registers that site
    #     explicitly and DERIVES the four words from
    #     commitment_state.KIND_VALUES_SAFE, so a fifth kind arrives here as a
    #     red suite rather than as a silent fall-through. apply-choices only
    #     dispatches task/promise today; the other two are classified because
    #     the writer can emit them, not because a verb reaches them.
    #   already_open        commitment_state.reopen_commitment.
    #   already_noted       orphan_note — DOGFIX1 identity idempotency.
    #   already_actioned    balance.record_actioned, keyed on the CARD.
    #   already_held        mute_ledger.hold_item — a repeat must not silently
    #     extend the clock, so it writes nothing.
    #   already_unconfirmed / already_undone — the queue's undo rails (SF-5).
    #   duplicate_open_legacy — brain_proposals.propose; the proposal already
    #     stands under a pre-migration fingerprint.
    #   unchanged           deal_state.set_stage / update_deal, objective_state
    #     .rebind_objective — asked for the value it already has.
    #   exists              people_writer.add_person_alias — the spelling was
    #     already known to that record.
    #   empty               brain_undo.undo_batch over a batch with nothing
    #     reversible; no change, no failure.
    #   already             brain_undo.undo_batch (POLICY1-B fix F-9): every
    #     reverser in the batch answered already_* / not_open — the rows were
    #     in the restored state before the gesture, nothing moved, no marker
    #     written. Distinct from `undone` so a second undo never reports the
    #     first undo's count.
    "already_task", "already_promise", "already_scheduling", "already_agenda",
    "already_open", "already_noted",
    "already_actioned", "already_held", "already_unconfirmed",
    "already_undone", "duplicate_open_legacy", "unchanged", "exists", "empty",
    "already",
    # BACKFILL2: `backfill_bindings.apply` reached from the binding widget's
    # rail (`backfill_widget.apply_choices`). `no_op` = nothing adjudicated,
    # nothing written (the no-auto fence's operational half); `skipped` = a
    # seq that is no longer an applicable row (already bound, rejected
    # earlier, or no longer name-matched) — honest no-ops, never failures.
    "no_op", "skipped",
    # --- PERSONLOOP1: person_candidates.resolve_candidate ---------------------
    #   already_suppressed  the name was already set aside; a second
    #     tombstone for one decision is the 83-duplicate-row class, so the
    #     writer declines. Nothing owed, nothing written.
    #   already_on_file     somebody added the person between the render and
    #     the tap. No record was created — but the DRAIN still ran, so the
    #     rows blocked on the name are cleared and the row must stop being
    #     offered. An honest no-op on the create, not a failure.
    "already_suppressed", "already_on_file",
    # HELDREVIEW1 — the review's objection is ONE capture per row, forever:
    # a second click on a row that already carries one writes nothing and says
    # so. That is the verb working as designed (an objection is a note, never
    # a queue), so it is a no-op and not a refusal. `ignored` is the same
    # shape for an empty id — nothing to object to, nothing written.
    "already_captured", "ignored",
})
# statuses that mean the handler REFUSED or could not complete the write.
_REFUSED_STATUSES = frozenset({
    "error", "refused", "failed", "needs_confirm", "blocked", "invalid",
    # WATCHGATE / BULKGUARD holds: the handler deliberately declined to write
    # because the row could not be proved. Named explicitly rather than left
    # to the unknown-status default, so the reason is legible in the source
    # instead of inferred from a fall-through.
    "held_weak_evidence", "held_pending_review", "confirmed_open",
    # BACKFILL2: the binding widget's snapshot fence — the substrate moved
    # past the propose snapshot the row's wire id carried, so
    # backfill_bindings.apply refused and NOTHING was written. Named so the
    # refusal is a decision on the record, not the unknown-status default.
    "stale_snapshot",
    # CUT-C item 5 (ATTENDED_TEST_v5.28.0 B2.5): a `push to [date]` that
    # arrived with NO date is refused by the dispatcher before any writer runs
    # (`commitment_state.later_missing_ack`). Nothing moved, so the row is
    # not dealt with: an error outcome, never ok, and the page-set keeps
    # showing it.
    "missing_when",
    # DONE1 v5.9.3: the rest of the needs-your-call queue's refusal/failure
    # vocabulary. "error" is already the right outcome for each, but by
    # FALL-THROUGH — and a fall-through is indistinguishable from a status the
    # vocabulary simply never learned (which is exactly how "done" hid). Named
    # here so the classification is a decision on the record:
    #   not_individually_named — the DONE1 gesture bar; the row is untouched
    #     and still owed, so the page-set must keep offering it.
    #   not_found — the id resolved to no commitment; nothing written.
    #   confirmed_not_closed — done_items' (a)-landed/(b)-failed half: the item
    #     is a confirmed OPEN commitment, so the attested close did NOT happen.
    #   has_subitems — drop_items refusing a parent with open children; the
    #     queue never cascades silently, so the row stays open.
    #   not_open — a writer pass-through (clear_review_flags / restore_review_
    #     flags on a closed item). It reads as a no-op on the confirm rail but
    #     as a real refusal on the undo rail ("that one is closed — undoing an
    #     'already done' reopens it first"), where the user still owes an
    #     action. One string, two meanings: FS-18a says take the
    #     never-optimistic one.
    "not_individually_named", "not_found", "confirmed_not_closed",
    "has_subitems", "not_open",
    # ATTRIB1-B door 1 (`attribution_doors.apply_counterparty_pick`): the
    # three ways a WHO-IS-YOU pick is refused, each writing nothing, each a
    # decision on the record rather than the unknown-status default:
    #   malformed      — the tuple was not `confirm_counterparty:<id>:<pid>`,
    #     so no row was even identified.
    #   no_question    — the row carries no `who_is_you` question (it was
    #     answered on another surface, or never asked); the pick has nothing
    #     to apply and the user still sees the row wherever it lives.
    #   not_an_option  — the person is not one the question OFFERED. Routing
    #     the item to somebody the ladder never proposed is a reassignment,
    #     which is `reassign to [name]`'s contract and not this verb's, so
    #     the pick refuses and says which verb to use.
    "malformed", "no_question", "not_an_option",
    # --- APPLYAUDIT1: the refusals the census found unclassified -------------
    # "error" was already the outcome for each of these, but by FALL-THROUGH,
    # and the whole lesson of this defect is that a fall-through hides an
    # unlearned word. Each is now a decision on the record:
    #   declined            orphan_note — nothing resolved, NOTHING written;
    #     the user is handed a line asking for a person or project. Work is
    #     still owed, so this must not suppress the row.
    #   not_confirmed / touched_since_confirm / not_a_done — the queue's undo
    #     rails refusing an id they do not own or that moved underneath them.
    #   not_reopened        undo_done_items' reopen leg failed; item stays
    #     closed and nothing else was written.
    #   reopened_only       the undo's HALF-landed shape — reopened but still
    #     confirmed. Half a write is not a write.
    #   not_closed / not_parked — watch_gate.confirm_review_rows' two failure
    #     legs; the row is untouched either way.
    #   digest_members_missing — proposal_digests refusing a grouped row that
    #     arrived without its members. Explicitly "nothing was written for it".
    #   partial             brain_undo.undo_batch when some reversers threw;
    #     never-optimistic says a partial batch is not a success.
    #   open                a commitment RECORD's own lifecycle field, reached
    #     by the census through create_personal_task / the sub-item minter. It
    #     is not a handler outcome today; if one ever returned it, it would be
    #     asserting the item is STILL OPEN — nothing landed. Classified into
    #     the never-optimistic bucket for exactly that reading.
    "declined", "not_confirmed", "touched_since_confirm", "not_a_done",
    "not_reopened", "reopened_only", "not_closed", "not_parked",
    "digest_members_missing", "partial", "open",
})


def derive_outcome(handler_result: Any) -> str:
    """Map a handler's actual result to the audit outcome enum
    ("ok" | "already_resolved" | "error").

    - Exception (instance or string that looks like one) → "error".
    - dict: `status` in the OK set → "ok"; no-op set → "already_resolved";
      refused set → "error". An UNKNOWN status maps to "error" — when the
      reporter can't prove the write landed, it must not claim it did
      (FS-18a is exactly the optimistic default).
    - None → "ok" (reserved for pure no-write actions).
    """
    if handler_result is None:
        return "ok"
    if isinstance(handler_result, BaseException):
        return "error"
    if isinstance(handler_result, str):
        return "error"  # a bare string result is an error message by convention
    if isinstance(handler_result, dict):
        status = str(handler_result.get("status") or "").lower()
        if status in _OK_STATUSES:
            return "ok"
        if status in _NOOP_STATUSES:
            return "already_resolved"
        if status in _REFUSED_STATUSES:
            return "error"
        return "error"  # unknown status: never optimistic
    return "error"


def build_apply_choices_applied_event(*, source: str, actions: List[dict]) -> dict:
    """Build the ONE `apply_choices_applied` audit event for a batch. Each
    entry in `actions` is {n, action, handler_result} (+ optional
    `note`). Outcomes derive from handler_result via derive_outcome — the
    caller cannot pass an outcome directly (that's the FS-18a hole).

    Returns the event dict WITHOUT seq/ts (the append gate auto-stamps).
    """
    rows = []
    n_errors = 0
    for a in actions:
        # Review F-1: an entry that OMITS handler_result entirely is a
        # reporter bug — the caller never captured what the handler did, so
        # the outcome cannot be claimed "ok". Only an EXPLICIT None (a pure
        # no-write ack) maps to "ok"; the absent key maps to "error".
        if "handler_result" not in a:
            outcome = "error"
        else:
            outcome = derive_outcome(a.get("handler_result"))
        if outcome == "error":
            n_errors += 1
        row = {"n": a.get("n"), "action": a.get("action"), "outcome": outcome}
        if a.get("note"):
            row["note"] = str(a["note"])[:200]
        rows.append(row)
    return {
        "type": "apply_choices_applied",
        "source_skill": "apply-choices",
        "data": {
            "source": source,
            "n_choices": len(actions),
            "actions": rows,
            "n_errors": n_errors,
        },
    }


__all__ = ["derive_outcome", "build_apply_choices_applied_event"]
