#!/usr/bin/env python3
"""Living Brain undo — the reverser registry + batch undo (SPEC LB1, D5).

WHY THIS EXISTS
The auto-apply tier is only safe because every auto-applied change class has a
REGISTERED, TESTED reverser — `brain_proposals.propose(tier="auto")` refuses
any change class without one (D2: the policy is code, not prose). This module
is that registry, plus the batch undo API that generalizes the four shipped
undo patterns (reconcile-sent reopen, triage batch undo, mute clears, the
narrated "Say `undo` to reverse this." affordance).

DOCTRINE
  - **All undo is additive.** A reverser appends the class's existing
    reversing event (`commitment_reopened`, `chat_dismissal_cleared`, a
    status→archived `person_updated`/`org_updated`) through the class's
    single writer — never a hand-rolled write, never an edit/delete of prior
    events (`CHAT_ACTION_WIDGET.md` § Undo: "Never edit or delete prior
    events."; event_gate enforces).
  - Every reversal appends ONE `brain_change_undone` narration-trail marker
    `{change_ref, reverser}` AFTER the reversing event, so the change feed
    can say "undid N changes" with traceable refs.
  - Bare `undo` routing stays with the narrating surface (D5 — no new global
    trigger); surfaces call `undo_batch` with the batch ref their own
    narration advertised.

BATCH REFS
Two shapes, both resolvable from the substrate alone:
  - `{"kind": "sent_reconcile", "seq": <audit seq>}` — reverses the
    commitment closes that sent-mail reconcile run narrated (the
    `commitment_resolved` events with `resolved_by == "sent_reconcile"`
    appended between the previous `sent_reconcile` audit and this one).
  - `{"kind": "brain_batch", "batch_id": "<id>"}` — reverses every change
    event stamped `data.brain_batch_id == batch_id` by its
    `data.brain_change_class` reverser. This is the shape LB2's auto
    detectors write (R1 structured-fact person/org creation stamps both
    fields at write time so this module can archive them later).
    POLICY1-B DD-5 — the id may be a RUN or a GROUP. Automatic closes carry
    a group batch (`<run>-<8hex>`, one per thread / meeting / source) with
    `data.parent_batch_id = <run>`; a ref naming the run resolves every
    group under it (`brain_batch_id == id OR parent_batch_id == id`), a ref
    naming one group resolves that group alone. `undo <group>`, `undo
    <run>` and `undo all` (the newest run) are the three verbs; the listing
    (`recent_auto_batches`) nests groups under their run so a fresh chat can
    offer all three.

stdlib only. Loud per-item failures collected, never aborts the batch.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


class BrainUndoError(ValueError):
    """Unknown reverser / unresolvable batch ref."""


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _entities_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "entities.json"


def _load_events(workspace_root) -> list[dict]:
    import event_refs

    path = _events_path(workspace_root)
    if not path.exists():
        return []
    return event_refs.load_events(path)


# ---------------------------------------------------------------------------
# Reversers — one per change class. Each takes (workspace_root, change, *,
# undone_by, source_skill) and returns the reversing-writer's result dict.
# `change` is a dict carrying the class-specific target id(s) plus
# `change_ref` (the audit anchor the brain_change_undone marker records).
# ---------------------------------------------------------------------------

def _reverse_commitment_close(workspace_root, change, *, undone_by, source_skill):
    from commitment_state import reopen_commitment

    return reopen_commitment(
        workspace_root,
        change["commitment_id"],
        reopened_by=undone_by,
        reason=change.get("reason") or "brain undo — batch reversal",
        source_skill=source_skill,
    )


def _reverse_commitment_due(workspace_root, change, *, undone_by, source_skill):
    """POLICY1-B (b) — put the due back EXACTLY to what the deferral
    recorded as `prior_due` (a date, or None = no date)."""
    from commitment_state import restore_due

    return restore_due(
        workspace_root,
        change["commitment_id"],
        prior_due=change.get("prior_due"),
        actor_id=undone_by,
        reason=change.get("reason") or "brain undo — due date put back",
        source_skill=source_skill,
    )


def _reverse_commitment_close_from_observed(workspace_root, change, *, undone_by,
                                            source_skill):
    """POLICY1-B DD-7 / F-1 — an observed-tier guess the calendar closer
    promoted and closed goes BACK TO ITS TIER; the promoted row is not
    reopened (no question is manufactured)."""
    from calendar_close import reverse_observed_close

    return reverse_observed_close(
        workspace_root, change["commitment_id"],
        observed_id=change.get("observed_id"), meeting_seq=change.get("meeting_seq"),
        undone_by=undone_by, source_skill=source_skill)


def _reverse_commitment_park(workspace_root, change, *, undone_by, source_skill):
    """POLICY1-B DD-6 — un-park a row an automatic leg parked."""
    from commitment_state import unpark_commitment

    return unpark_commitment(
        workspace_root, change["commitment_id"], unparked_by=undone_by,
        source_skill=source_skill, reason="brain undo — un-parked")


def _reverse_commitment_parks(workspace_root, changes, *, undone_by, source_skill):
    """F-6 — the batch twin: N parks un-parked under one lock, one scan
    (`commitment_state.unpark_commitments`). Returns one result per change,
    in the same order."""
    from commitment_state import unpark_commitments

    ids = [c["commitment_id"] for c in changes]
    by_id = {r["commitment_id"]: r for r in unpark_commitments(
        workspace_root, ids, unparked_by=undone_by, source_skill=source_skill,
        reason="brain undo — un-parked")}
    return [by_id.get(c["commitment_id"], {"status": "not_found",
                                             "commitment_id": c["commitment_id"]})
            for c in changes]


def _reverse_commitment_reassign(workspace_root, change, *, undone_by,
                                 source_skill):
    """POLICY1-B F-9 — put the counterparty back EXACTLY to what the
    reassign recorded as `prior_counterparty_id` (a person, or None)."""
    from commitment_state import restore_counterparty

    return restore_counterparty(
        workspace_root,
        change["commitment_id"],
        prior_counterparty_id=change.get("prior_counterparty_id"),
        prior_counterparty_name=change.get("prior_counterparty_name"),
        actor_id=undone_by,
        reason=change.get("reason") or "brain undo — counterparty put back",
        source_skill=source_skill,
    )


def _reverse_commitment_merge(workspace_root, change, *, undone_by, source_skill):
    # AUTOAPPLY §4c — split an auto-merged duplicate back out. Additive: the
    # supersede event STAYS in history and reopen_commitment appends the
    # reversing `commitment_reopened`, so the reopened item is its own record
    # again. The survivor's folded `merged_source_refs` are a harmless
    # residue — read-side provenance only, and no double-render, because the
    # reopened item projects from its own commitment event.
    #
    # THE UNDO HAS TO STICK (review F-1). Reopening alone was not a reversal:
    # the reopened item still carries `data.auto_merge_of` — the stamp is on
    # an append-only capture event and cannot be erased — so the very next
    # `apply_auto_merges` fire silently re-applied the merge the user had
    # just reversed. §4a's reverser already answers this shape by minting a
    # CONFIRM-tier row so "the system can NEVER silently re-auto-link the
    # same pair"; this is §4c's equivalent, and it lands the pair back on the
    # FLAG TIER — a visible question — rather than back on the auto rail.
    # The durable half is `undone_auto_merges` below, read by the applier at
    # apply time — a review flag is the USER'S to clear ("Keep both"), and
    # the reversal has to outlive that answer.
    from commitment_state import flag_duplicate_for_review, reopen_commitment

    cid = change.get("commitment_id")
    if not cid:
        raise BrainUndoError(
            "commitment_merge reversal needs the SUPERSEDED commitment_id "
            "(the writer stamps it on the supersede event's data)")
    result = reopen_commitment(
        workspace_root,
        cid,
        reopened_by=undone_by,
        reason=change.get("reason") or "brain undo — split a merged duplicate",
        source_skill=source_skill,
    )
    survivor_id = change.get("superseded_by") or change.get("survivor_id")
    if survivor_id:
        try:
            flagged = flag_duplicate_for_review(
                workspace_root, cid,
                suspected_duplicate_of=str(survivor_id),
                score=change.get("auto_merge_score"),
                # F-3: NOT "the automatic merge". Nothing in this system merges
                # on its own — a merge is a button the user pressed — and saying
                # otherwise on a review surface contradicts the never-auto-merge
                # pillar to the one reader who just reversed it by hand.
                reason="you reversed the merge — merge these by "
                       "hand or keep both",
                flagged_by=undone_by, source_skill=source_skill,
            )
            result["review_row"] = flagged.get("status")
        except Exception as exc:  # loud per-item, contained per-batch
            result["review_row"] = f"error: {type(exc).__name__}: {exc}"
    return result


# UNCONFIRM1 — the queue's two USER-GESTURE reversals.
#
# BOTH GO THROUGH THE QUEUE'S OWN WRAPPERS, NOT THE WRITERS (review SF-7).
# The first draft called `restore_review_flags` / `reopen_commitment` directly,
# which was a SECOND, WEAKER path to the same state change: on an item somebody
# had independently reassigned after the confirm, the wrapper returned
# `touched_since_confirm` while the reverser cheerfully returned `restored`;
# on an item that was never confirmed at all, the reverser also returned
# `restored`. "Fences extended, never forked" is a hard constraint, and a
# reverser that skips the bar the surface enforces is a fork with a registry
# entry. Every bar — the touch bar, the never-confirmed refusal, MF-2's
# attested-closure gate, idempotence — now applies identically whichever road
# the undo arrives by.
#
# A REFUSAL IS AN ERROR HERE, not a silent success: `undo_batch` counts a
# raising reverser as a per-item error and does NOT append the
# `brain_change_undone` marker, which is exactly right — nothing was reversed.
# An already-in-that-state result is NOT a refusal and does not raise, matching
# `_reverse_commitment_merge`'s treatment of `already_open`.

_ALREADY_STATUSES = frozenset({"already_unconfirmed", "already_undone"})


def _one_queue_result(out: dict, cid: str, verb: str) -> dict:
    """Unwrap a one-id queue-wrapper return, raising on a refusal."""
    results = out.get("results") or []
    entry = results[0] if results else {}
    status = entry.get("status")
    if status in ("restored", "undone") or status in _ALREADY_STATUSES:
        return {"status": status, "commitment_id": entry.get("commitment_id",
                                                             cid),
                "queue_result": out}
    raise BrainUndoError(
        f"{verb} reversal refused for {cid!r}: {status} — "
        f"{entry.get('detail') or 'the queue would not reverse it'}")


def _reverse_commitment_confirm(workspace_root, change, *, undone_by,
                                source_skill):
    # Additive: the confirm's `commitment_updated` stays in history and
    # `restore_review_flags` (reached through `undo_confirm_items`) appends the
    # reversing one, so the item is a queue member again carrying its ORIGINAL
    # review_reason and its ORIGINAL duplicate link, both read off the capture
    # event — nothing has to be cached between the two gestures.
    #
    # THE WRITER MATTERS. Before this existed, the only additive reverser of a
    # confirm was `flag_duplicate_for_review`, which requires a
    # `suspected_duplicate_of`; used as an un-confirm it wrote an empty target,
    # which the projector ignores and the on-disk history keeps forever as a
    # duplicate pair that never existed.
    from needs_review_queue import undo_confirm_items

    cid = change.get("commitment_id")
    if not cid:
        raise BrainUndoError(
            "commitment_confirm reversal needs the commitment_id the confirm "
            "cleared (needs_review_queue.confirm_items stamps it on the "
            "commitment_updated event's data)")
    out = undo_confirm_items(workspace_root, [cid], restored_by=undone_by,
                             source_skill=source_skill)
    return _one_queue_result(out, str(cid), "commitment_confirm")


def _reverse_commitment_done(workspace_root, change, *, undone_by,
                             source_skill):
    # Reverse an `already done` attestation. TWO steps inside the wrapper, and
    # the order is forced: both review-flag writers refuse a CLOSED item, so
    # the reopen lands first. And the reopen alone is not the reversal — the
    # Done wrote a confirm before it closed, so a bare reopen leaves an OPEN,
    # CONFIRMED item, not the queue member the user had before they tapped.
    # That is the closed-corpse blind spot's twin, and it is why this mirrors
    # `_reverse_commitment_merge`'s reopen-then-re-flag shape.
    from needs_review_queue import undo_done_items

    cid = change.get("commitment_id")
    if not cid:
        raise BrainUndoError(
            "commitment_done reversal needs the commitment_id the Done "
            "closed (needs_review_queue.done_items stamps it on the "
            "commitment_resolved event's data)")
    out = undo_done_items(workspace_root, [cid], restored_by=undone_by,
                          source_skill=source_skill)
    return _one_queue_result(out, str(cid), "commitment_done")


def _reverse_chat_dismissal(workspace_root, change, *, undone_by, source_skill):
    from mute_ledger import clear_dismissals

    results = clear_dismissals(
        workspace_root,
        [change["dismissal_seq"]],
        cleared_by=undone_by,
        source_skill=source_skill,
        reason="brain undo — batch reversal",
        via="brain_undo",
    )
    return results[0] if results else {"status": "error", "error": "no result"}


def _reverse_person_org_creation(workspace_root, change, *, undone_by, source_skill):
    # R1 — the auto-created identity reverser: archive, never delete. The
    # record and its person_created/org_created history stay on file; the
    # status flip writes the additive person_updated/org_updated event
    # (data.before preserved by the writer).
    if change.get("person_id"):
        from people_writer import update_person

        rec = update_person(
            workspace_root, change["person_id"],
            source_skill=source_skill, status="archived",
        )
        return {"status": "archived", "person_id": change["person_id"], "record": rec}
    if change.get("org_id"):
        from org_writer import update_org

        rec = update_org(
            workspace_root, change["org_id"],
            source_skill=source_skill, status="archived",
        )
        return {"status": "archived", "org_id": change["org_id"], "record": rec}
    raise BrainUndoError("person_org_creation reversal needs person_id or org_id")


def _reverse_entity_fact_structured(workspace_root, change, *, undone_by,
                                    source_skill):
    # HIST1 Part 2 (D3/S1) — facts are append-only with NO status to flip:
    # "archive the event" is undefined here. The reverser APPENDS the
    # declared entity_fact_retracted event {target_id, retracts_seq};
    # render_person_history / render_org_history suppress a fact whose seq
    # a later retraction references (shipped in Part 1, suppression in
    # EVERY block). The fact event itself stays in history — provenance is
    # never edited or deleted.
    from event_gate import append_event

    target_id = change.get("person_id") or change.get("org_id")
    if not target_id:
        raise BrainUndoError(
            "entity_fact_structured reversal needs person_id or org_id on "
            "the fact event's data (the writers stamp it — a batch row "
            "without one is malformed)")
    ref = str(change.get("change_ref") or "")
    try:
        retracts_seq = int(ref.split(":", 1)[1])
    except (IndexError, ValueError):
        raise BrainUndoError(
            f"entity_fact_structured reversal needs a seq-bearing "
            f"change_ref, got {ref!r}")
    append_event(_events_path(workspace_root), [{
        "type": "entity_fact_retracted",
        "source_skill": source_skill,
        "data": {
            "target_id": target_id,
            "retracts_seq": retracts_seq,
            "reason": change.get("reason") or "brain undo — batch reversal",
            # Facts are always sourced (D2/S4) — the retraction inherits
            # the discipline; synthesized ref, never null.
            "source_ref": f"undo:{source_skill}:{ref}",
        },
    }], holder="brain_undo")
    return {"status": "retracted", "target_id": target_id,
            "retracts_seq": retracts_seq}


def _reverse_person_link(workspace_root, change, *, undone_by, source_skill):
    # UXR1 D3 — reverse ONE auto-link tombstone: (1) remove the written
    # link (reopen the mention proposal the same_as tombstone closed — the
    # additive person_proposal_reopened marker; the auto path writes NO
    # alias, so the record itself is already alias-free. AUTOAPPLY §4a
    # widened gate (a) to admit email-corroborated links whose spelling
    # differs, and deliberately did NOT start writing aliases for them —
    # precisely so this reverser stays COMPLETE), then (2) re-open a
    # CONFIRM-tier person_link proposal carrying the original evidence so
    # the decision comes back to a human — and so the next reconcile run
    # can NEVER silently re-auto-link the same pair (propose(tier="auto")
    # dedups against the open confirm row's fingerprint). The tombstones
    # stamp link_fingerprint/matched_name/link_evidence at write time; a
    # batch's N member tombstones re-propose once (fingerprint dedup).
    result = _reverse_person_proposal_tombstone(
        workspace_root, change, undone_by=undone_by, source_skill=source_skill)
    fingerprint = change.get("link_fingerprint")
    if fingerprint:
        from brain_proposals import propose

        try:
            # UXR1 D4 — the re-opened ask renders decision-grade too: the
            # record is re-fetched by id so the differentiator (org > email
            # > last touched) shows what a confirm would link to.
            matched = {"id": change.get("person_id"),
                       "canonical_name": change.get("matched_name") or ""}
            try:
                import json as _json

                ents = _json.loads(
                    (Path(workspace_root) / "_hq" / "data" / "entities.json")
                    .read_text(encoding="utf-8"))
                ents = ents.get("entities") if isinstance(
                    ents.get("entities"), dict) else ents
                for p in ents.get("people") or []:
                    if p.get("id") == change.get("person_id"):
                        matched = p
                        break
            except Exception:
                pass
            from identity_reconcile import person_link_ask_line

            line = person_link_ask_line(
                workspace_root, change.get("alias") or "this name",
                matched, str(change.get("link_evidence") or ""))
            reopened = propose(
                workspace_root,
                kind="person_link",
                fingerprint=str(fingerprint),
                evidence=str(change.get("link_evidence") or ""),
                action_tuples=[{"action": "confirm proposal"},
                               {"action": "dismiss proposal"},
                               {"action": "snooze proposal 7d"}],
                tier="confirm",
                detector="identity-reconcile",
                render_line=f"{line} (you undid the automatic link)",
                person_id=change.get("person_id"),
                extra={"title": change.get("alias") or "",
                       "alias_name": change.get("alias") or "",
                       "matched_name": change.get("matched_name") or ""},
            )
            result["confirm_row"] = reopened.get("status")
        except Exception as exc:  # loud per-item, contained per-batch
            result["confirm_row"] = f"error: {type(exc).__name__}: {exc}"
    return result


def _reverse_day_intent(workspace_root, change, *, undone_by, source_skill):
    # SPEC BK1 — reverse "tomorrow is about X". Like every reverser here the
    # reversal is ADDITIVE: `day_intent.reverse_day_intent` appends a NEW
    # day_intent for the same for_date that either RESTORES the record this
    # one superseded or RETRACTS the day outright (`data.retracted`, which the
    # reader answers as None). The reversed event stays in history forever.
    #
    # THE ANCHOR IS THE SEQ, read off `change_ref` exactly as the
    # entity_fact_structured reverser reads its retraction target — a
    # day_intent has no id of its own, and its position in the for_date's
    # append order IS its identity.
    #
    # NEVER ADD THIS CLASS TO `brain_proposals.AUTO_ALLOWED`. A day_intent is
    # a statement about the CEO's own day; nothing may make one on its own,
    # which is the same rule the UNCONFIRM1 user-gesture classes carry.
    from day_intent import reverse_day_intent

    ref = str(change.get("change_ref") or "")
    try:
        target_seq = int(ref.split(":", 1)[1])
    except (IndexError, ValueError):
        raise BrainUndoError(
            f"day_intent reversal needs a seq-bearing change_ref, got {ref!r}")
    return reverse_day_intent(workspace_root, target_seq,
                              undone_by=undone_by, source_skill=source_skill)


def _reverse_prep_brief_thread_backfill(workspace_root, change, *, undone_by,
                                        source_skill):
    # SPEC THREADBIND1 §0 ruling 3 — reverse ONE backfilled prep_brief
    # binding. The `prep_brief` receipt the backfill targeted is append-only
    # (it is a RECEIPT, never rewritten), so the reversal cannot touch it —
    # it appends `prep_brief_thread_backfill_undone`, and
    # `thread_resolve._meeting_bound_thread_id` folds that in as "this
    # meeting's backfilled binding no longer counts" (the marker itself
    # stays in history, same additive-reversal doctrine as every reverser in
    # this module).
    meeting_id = str(change.get("meeting_id") or "")
    if not meeting_id:
        raise BrainUndoError(
            "prep_brief_thread_backfill reversal needs the meeting_id the "
            "backfill bound (backfill_prep_briefs.apply_backfill stamps it "
            "on the prep_brief_thread_backfilled event's data)")
    from event_gate import append_event

    events_path = _events_path(workspace_root)
    ev = append_event(events_path, {
        "type": "prep_brief_thread_backfill_undone",
        "source_skill": source_skill,
        "data": {"meeting_id": meeting_id, "undone_by": undone_by},
    }, holder=source_skill)
    return {"status": "undone", "meeting_id": meeting_id, "event": ev}


def _reverse_thread_split(workspace_root, change, *, undone_by, source_skill):
    # SPEC THREADANN1 §0 ruling 4 — reverse ONE retagged event from a
    # confirmed thread split. Additive, same doctrine as every reverser
    # here: the split's own reclassification events and the new child
    # thread records STAY in history (records never move / never delete —
    # SPEC §0 ruling 1's invariant). This appends a RESTORING
    # `reclassification` event with the SAME `supersedes_seq` (the
    # ORIGINAL event the split retagged) — `thread_activity.
    # apply_reclassifications`'s "highest reclassifying seq wins" means
    # this later-seq restoring event wins the fold, so the original event
    # reads as owned by the parent again.
    #
    # The child thread the batch created is archived (status -> archived,
    # never deleted — the same posture `_reverse_person_org_creation`
    # takes on an auto-created record) once its LAST retagged event has
    # been restored; `undo_batch` calls this reverser once per retagged
    # event, so the archive is idempotent-guarded (skip if already
    # archived) rather than performed N times.
    supersedes_seq = change.get("supersedes_seq")
    old_primary = change.get("old_primary_thread_id")
    new_primary = change.get("new_primary_thread_id")
    if supersedes_seq is None or not old_primary:
        raise BrainUndoError(
            "thread_split reversal needs supersedes_seq + "
            "old_primary_thread_id on the reclassification event (the "
            "split executor stamps both in `data`)")
    from event_gate import append_event
    from thread_activity import apply_reclassifications

    events_path = _events_path(workspace_root)

    # REVIEW THREADANN1 F5 — undo restores ONLY what the split still owns.
    # If the user (or any later write) has since moved this event somewhere
    # OTHER than the child this batch retagged it to, that later move is
    # the newer truth; appending a restoring reclassification here would
    # win the fold and CLOBBER it. Skip, honestly, with no write — same
    # left-in-place posture the executor's own stale-seq guard takes.
    current_events = _load_events(workspace_root)
    current_primary = None
    for _fev in apply_reclassifications(current_events):
        if (isinstance(_fev, dict) and _fev.get("seq") == supersedes_seq
                and _fev.get("type") != "reclassification"):
            current_primary = _fev.get("primary_thread_id")
            break
    if new_primary and current_primary != new_primary:
        return {"status": "undone", "skipped": "moved_since_split",
                "supersedes_seq": supersedes_seq,
                "current_primary_thread_id": current_primary,
                "note": "this event was moved again after the split — the "
                        "later move is the newer truth; left in place"}

    # REVIEW THREADANN1 F4 — restore the ORIGINAL related_thread_ids (the
    # split's reclassification preserved them; stamping [] here erased
    # them from the folded read on undo, breaking the byte-identical
    # round-trip the spec acceptance pins).
    related = change.get("old_related_thread_ids")
    if not isinstance(related, list):
        related = []
    ev = append_event(events_path, {
        "type": "reclassification",
        "source_skill": source_skill,
        "supersedes_seq": supersedes_seq,
        "primary_thread_id": old_primary,
        "related_thread_ids": related,
        "classification_confidence": 1.0,
        "data": {
            "old_primary_thread_id": new_primary,
            "new_primary_thread_id": old_primary,
            "old_related_thread_ids": related,
            "new_related_thread_ids": related,
            "reason": "brain undo — thread split reversed",
        },
    }, holder=source_skill)
    result = {"status": "restored", "supersedes_seq": supersedes_seq,
             "restored_to": old_primary}
    # REVIEW THREADANN1 F5 — after THIS restore, does the child still own
    # anything? (The event just restored no longer counts; anything else —
    # including an event the user added to the child AFTER the split —
    # keeps the child alive: archiving a thread that still owns live
    # records would strand them. `current_events` was loaded before this
    # restore's append, so exclude this seq explicitly.)
    child_still_owns_any = any(
        isinstance(_fev, dict) and _fev.get("type") != "reclassification"
        and _fev.get("primary_thread_id") == new_primary
        and _fev.get("seq") != supersedes_seq
        for _fev in apply_reclassifications(current_events)
    )
    if new_primary and child_still_owns_any:
        result["child_archive"] = "kept_alive_still_owns_events"
        return {"status": "undone", **result, "event": ev}
    if new_primary:
        try:
            # THE chokepoint (ARCHFIX census — exactly three modules may set
            # a thread's status to "archived": thread_archive.py itself,
            # deal_state.py, objective_state.py). This reverser calls
            # `archive_thread`, it does not stamp `update_thread(status=
            # "archived")` directly, so it is never a fourth. Idempotent —
            # `undo_batch` calls this reverser once per retagged event, and
            # `archive_thread` on an already-archived child is a documented
            # no-op ("already_archived", writes nothing).
            import thread_archive

            arch = thread_archive.archive_thread(
                workspace_root, new_primary,
                reason="thread split reversed — the split-created thread "
                      "has no more events of its own",
                source_skill=source_skill, regenerate_view=False)
            result["child_archive"] = arch.get("status")
        except Exception as exc:  # per-item, contained per-batch
            result["child_archive_error"] = f"{type(exc).__name__}: {exc}"
    return {"status": "undone", **result, "event": ev}


def _reverse_binding_backfill(workspace_root, change, *, undone_by,
                              source_skill):
    # SPEC_BACKFILL1 §M.4 — reverse ONE accepted re-bind from the R3
    # binding backfill. Additive, same doctrine as `_reverse_thread_split`
    # directly below (the idiom this mirrors): the backfill's own
    # `reclassification` stays in history; this appends a RESTORING
    # `reclassification` with the SAME `supersedes_seq` re-asserting the
    # ORIGINAL envelope — later reclassifying seq wins the fold, so the
    # event reads exactly as it did before the batch.
    #
    # Moved-since guard (the THREADANN1 F5 posture): if a LATER write moved
    # this event somewhere other than what THIS batch set — the primary no
    # longer matches, or the additively-added target thread is no longer
    # among the event's refs — that later move is the newer truth; restoring
    # here would clobber it. Skip honestly, no write. (Shared limitation
    # with thread_split: a later ADDITIVE change on top of this batch's is
    # folded away by the restore — the guard catches moves, not additions.)
    #
    # NO gauge rebuild here — a per-change rebuild would walk the whole
    # substrate N times per sitting. `backfill_bindings.undo` (the CLI
    # wrapper) rebuilds ONCE after the batch; a bare `undo` through another
    # surface leaves the artifact honestly stale (`events_max_seq` says so)
    # until the next scheduled refresh.
    supersedes_seq = change.get("supersedes_seq")
    old_primary = change.get("old_primary_thread_id")
    new_primary = change.get("new_primary_thread_id")
    target_tid = change.get("target_thread_id")
    if supersedes_seq is None or not target_tid:
        raise BrainUndoError(
            "binding_backfill reversal needs supersedes_seq + "
            "target_thread_id on the reclassification event (the backfill "
            "stamps both in `data`)")
    from event_gate import append_event
    from event_refs import threads_of
    from thread_activity import apply_reclassifications

    current_events = _load_events(workspace_root)
    current = None
    for _fev in apply_reclassifications(current_events):
        if (isinstance(_fev, dict) and _fev.get("seq") == supersedes_seq
                and _fev.get("type") != "reclassification"):
            current = _fev
            break
    if current is not None:
        current_primary = current.get("primary_thread_id")
        if current_primary != new_primary:
            return {"status": "undone", "skipped": "moved_since_backfill",
                    "supersedes_seq": supersedes_seq,
                    "current_primary_thread_id": current_primary,
                    "note": "this event was moved again after the backfill "
                            "— the later move is the newer truth; left in "
                            "place"}
        if (new_primary != target_tid
                and target_tid not in threads_of(current)):
            return {"status": "undone", "skipped": "moved_since_backfill",
                    "supersedes_seq": supersedes_seq,
                    "note": "the backfilled related-ref was already removed "
                            "by a later write — left in place"}

    related = change.get("old_related_thread_ids")
    if not isinstance(related, list):
        related = []
    restoring = {
        "type": "reclassification",
        "source_skill": source_skill,
        "supersedes_seq": supersedes_seq,
        "related_thread_ids": related,
        "classification_confidence": 1.0,
        "data": {
            "old_primary_thread_id": new_primary,
            "new_primary_thread_id": old_primary,
            "old_related_thread_ids": related,
            "new_related_thread_ids": related,
            "reason": "brain undo — binding backfill reversed",
        },
    }
    # A bound-nowhere accept's original primary is NONE — the key is omitted
    # rather than stamped null (the fold reads an absent key as None either
    # way; a null in the canonical slot is a write-shape smell).
    if old_primary:
        restoring["primary_thread_id"] = old_primary
    ev = append_event(_events_path(workspace_root), [restoring],
                      holder=source_skill)
    return {"status": "undone", "supersedes_seq": supersedes_seq,
            "restored_to": old_primary, "event": ev[0]}


def _reverse_person_proposal_tombstone(workspace_root, change, *, undone_by,
                                       source_skill):
    # T2.2 (backlog sweep) — reverse an expire/skip tombstone on a person
    # proposal: append the additive person_proposal_reopened marker; the
    # confirm_flow reader honors the LAST writer, so the proposal re-surfaces.
    # PID1 D8: a tombstone on a SEQ-LESS proposal carries proposal_fingerprint
    # instead — the reopen marker carries the same key (the reader folds both).
    from event_gate import append_event
    from event_seq import coerce_seq

    raw_seq = change.get("proposal_seq")
    fingerprint = change.get("proposal_fingerprint")
    if raw_seq is None and not fingerprint:
        raise BrainUndoError(
            "person_proposal_tombstone reversal needs proposal_seq (or, for "
            "a seq-less proposal, proposal_fingerprint — D8)")
    # UNDOGUARD: `int(seq)` here raised a bare ValueError/TypeError on a
    # malformed proposal_seq, which `undo_batch` catches as a per-item error
    # with an opaque message. Coerce through the one helper and fail with a
    # sentence that names the field.
    seq = coerce_seq(raw_seq, context="proposal_seq")
    if raw_seq is not None and seq is None:
        if not fingerprint:
            raise BrainUndoError(
                f"person_proposal_tombstone reversal got an unreadable "
                f"proposal_seq {raw_seq!r} ({type(raw_seq).__name__}) and no "
                "proposal_fingerprint to fall back on — the tombstone is "
                "malformed and cannot be anchored")
        raw_seq = None
    data = {
        "reopened_by": undone_by,
        "reason": change.get("reason") or "brain undo — batch reversal",
    }
    if seq is not None:
        data["proposal_seq"] = seq
    else:
        data["proposal_fingerprint"] = str(fingerprint)
    append_event(_events_path(workspace_root), [{
        "type": "person_proposal_reopened",
        "source_skill": source_skill,
        "data": data,
    }], holder="brain_undo")
    if seq is not None:
        return {"status": "reopened", "proposal_seq": seq}
    return {"status": "reopened", "proposal_fingerprint": str(fingerprint)}


# change_class -> {reverse, reverses_via, description}. `reverses_via` names
# the additive reversing event the callable appends — documentation the
# tests assert so the registry can't silently drift from the doctrine.
def _reverse_org_promotion(workspace_root, change, *, undone_by, source_skill):
    """DEALNAG1 — reverse ONE automatic prospect -> client promotion: flip
    `relationship_type` back to prospect and put the engagement edge back
    the way it was (deactivate the edge the promotion CREATED; restore the
    label / kind / active flag of one it UPDATED). Both writes go through
    the typed writers, so the reversal is additive history like every other
    reverser here — records never move, the org_promoted event stays on
    file, and the change-feed line keeps its refs.

    CUTB item 4 (2026-09-06) — when the receipt says the deal behind the
    win was MANUFACTURED by the same act (`deal_manufactured`, the
    `mark [org] won`-with-nothing-on-file path), the undo puts that back
    too: the thread is archived through `thread_archive.archive_thread`
    (never deleted) and ONE `deal_won_reversed` marker is written naming
    the thread and the won event's seq — add-beside, the `deal_won` event
    and the deal object stay as written. `deal_state.list_closed_deals`,
    `pipeline_math.won_rate_90d` and `deal_signal_retire.settled_orgs`
    fold the marker, so the reversed win is in no closed-deals list, no
    90-day rate and no report. A win the PERSON closed by hand on a real
    deal is not touched: undoing the promotion restores exactly the state
    before the automatic act — deal won, org a prospect — and nothing
    else (an undo that reverses more than the product did is a defect of
    the same rank as one that reverses less). Idempotent on the marker:
    a thread already reversed gets no second marker."""
    org_id = change.get("org_id")
    if not org_id:
        raise BrainUndoError(
            "org_promotion reversal needs org_id on the org_promoted event's "
            "data (org_promotion.promote_org stamps it — a batch row without "
            "one is malformed)")
    from org_writer import update_org

    rec = update_org(workspace_root, org_id, source_skill=source_skill,
                     relationship_type="prospect")
    eng_id = change.get("engagement_id")
    eng_result = None
    if eng_id:
        from engagement_writer import update_engagement

        if change.get("engagement_created"):
            eng_result = update_engagement(
                workspace_root, eng_id, source_skill=source_skill,
                is_active=False,
                label="Engagement edge from an undone promotion")
        else:
            fields = {"is_active": bool(change.get("prev_engagement_active",
                                                   True))}
            if change.get("prev_engagement_label") is not None:
                fields["label"] = change["prev_engagement_label"]
            if change.get("prev_engagement_kind") is not None:
                fields["kind"] = change["prev_engagement_kind"]
            eng_result = update_engagement(
                workspace_root, eng_id, source_skill=source_skill, **fields)
    out = {"status": "demoted", "org_id": org_id,
           "relationship_type": rec.get("relationship_type"),
           "engagement_id": eng_id,
           "engagement": eng_result}
    # CUTB item 4 — the manufactured deal goes back too.
    deal_tid = change.get("deal_thread_id")
    if change.get("deal_manufactured") is True and deal_tid:
        from deal_state import won_reversals
        from event_gate import append_event
        from thread_archive import archive_thread

        already = won_reversals(workspace_root).get(str(deal_tid))
        arch = archive_thread(
            workspace_root, str(deal_tid),
            reason="undo — this deal was opened by `mark won` with nothing "
                   "on file, and the person put it back",
            source_skill=source_skill)
        out["deal_thread_id"] = str(deal_tid)
        out["deal_archived"] = arch.get("status")
        if already is None:
            ev = {"type": "deal_won_reversed", "source_skill": source_skill,
                  "primary_thread_id": str(deal_tid),
                  "org_ids": [org_id],
                  "data": {"thread_id": str(deal_tid), "org_id": org_id,
                           "won_seq": change.get("won_seq"),
                           "reversed_by": undone_by,
                           "reason": "brain undo — the promotion that opened "
                                     "and won this deal was put back"}}
            append_event(_events_path(workspace_root), [ev], holder="brain_undo")
            out["won_reversed"] = True
        else:
            out["won_reversed"] = "already_reversed"
    return out


def _reverse_commitment_preset(workspace_root, change, *, undone_by, source_skill):
    """QUIET1 D1 — reverse ONE preset stamp (`commitment-policy.preset`):
    put the PREVIOUS config back exactly through the typed writer, or clear
    the key when there was none before (the manifest auto_apply's case —
    a workspace that had no stored posture goes back to having none, and
    reads the shipped fallback again). The stamp's own
    `skill_first_run_configured` / `skill_reconfigured` row stays in
    history; the restore is a second config event (or a wipe plus this
    batch's `brain_change_undone` marker)."""
    from skill_config_writer import save_skill_config, wipe_skill_config
    skill = change.get("skill_name")
    if not skill:
        raise BrainUndoError(
            "commitment_preset reversal needs skill_name on the stamp "
            "event's data (quiet.stamp_preset stamps it — a batch row "
            "without one is malformed)")
    prev = change.get("prev_config")
    if change.get("prev_config_present") and isinstance(prev, dict):
        save_skill_config(workspace_root, skill, dict(prev), is_reconfigure=True,
                          origin="undo",
                          event_extra={"undone_by": undone_by,
                                       "restores_batch": change.get("brain_batch_id")})
        return {"status": "restored", "skill_name": skill, "config": prev}
    wiped = wipe_skill_config(workspace_root, skill)
    return {"status": "cleared" if wiped else "already_clear",
            "skill_name": skill, "config": None}


REVERSERS: dict[str, dict] = {
    "commitment_close": {
        "reverse": _reverse_commitment_close,
        "reverses_via": "commitment_reopened",
        "description": "reopen a commitment closed on HIGH sent-mail evidence "
                       "(the reconcile-sent shipped precedent)",
    },
    # AUTOAPPLY §4c: the auto-merge tier is legal ONLY because this reverser
    # exists, and it lands in the SAME commit as the AUTO_ALLOWED row (the
    # step-10 mandate). Splitting is additive — the supersede stays history.
    "commitment_merge": {
        "reverse": _reverse_commitment_merge,
        "reverses_via": "commitment_reopened + a flag-tier "
                        "commitment_updated (review_flags_set)",
        "description": "split an auto-merged duplicate back out (the "
                       "supersede event stays in history; the survivor's "
                       "folded refs are read-side provenance only); the pair "
                       "returns to the human as a flag-tier question and is "
                       "never re-merged automatically",
    },
    # UNCONFIRM1 (2026-08-03) — the two needs-your-call USER GESTURES.
    #
    # NEITHER OF THESE MAY EVER JOIN `brain_proposals.AUTO_ALLOWED`, now or
    # later. Registering a reverser is only ONE half of the auto-tier legality
    # test (`brain_proposals.propose(tier="auto")` requires membership in
    # AUTO_ALLOWED *and* a registered reverser); this build does not touch the
    # other half. Both of these reverse a USER GESTURE, and nothing may make a
    # user gesture on its own.
    # POLICY1-B (b) — a `push to [date]` deferral. Reverses to the exact
    # prior due, including NO date (ATTENDED_TEST_v5.27.0 B2.4). A user
    # gesture: never in AUTO_ALLOWED.
    # POLICY1-B F-9 — the counterparty a lapse default applied. Reverses
    # to the exact prior counterparty, including NONE.
    # POLICY1-B DD-6 / DD-9 — a park an automatic leg wrote (D11's quiet
    # owed-to-you rows). Un-parks; the row was never closed.
    # POLICY1-B DD-7 / F-1 — the calendar closer's close of an OBSERVED-tier
    # guess. Reverses by returning the guess to its tier, never by reopening
    # the promoted row (which would be a question nobody ever saw).
    "commitment_close_from_observed": {
        "reverse": _reverse_commitment_close_from_observed,
        "reverses_via": "commitment_updated (promotion_reversed)",
        "description": "put a set-aside scheduling guess the calendar closer "
                       "closed back where it was, off the plate",
    },
    "commitment_park": {
        "reverse": _reverse_commitment_park,
        "reverse_many": _reverse_commitment_parks,   # F-6: one lock for a run of parks
        "reverses_via": "commitment_updated (status_hint null)",
        "description": "un-park a resting row the product parked on its own",
    },
    "commitment_reassign": {
        "reverse": _reverse_commitment_reassign,
        "reverses_via": "commitment_reassigned (counterparty_restored; "
                        "counterparty_cleared when it had none)",
        "description": "put a counterparty back where it was before a "
                       "default was applied — including back to nobody",
    },
    "commitment_due": {
        "reverse": _reverse_commitment_due,
        "reverses_via": "commitment_updated (new_due = prior_due, or "
                        "new_due null + due_cleared)",
        "description": "put a deferred due date back exactly where it was — "
                       "including back to no date",
    },
    "commitment_confirm": {
        "reverse": _reverse_commitment_confirm,
        "reverses_via": "commitment_updated (review_flags_set)",
        "description": "un-confirm an unconfirmed extraction the user "
                       "confirmed — it returns to the needs-your-call queue "
                       "carrying its original reason; the confirm stays in "
                       "history",
    },
    "commitment_done": {
        "reverse": _reverse_commitment_done,
        "reverses_via": "commitment_reopened + a commitment_updated "
                        "(review_flags_set)",
        "description": "reverse an 'Already done' attestation — the item "
                       "reopens AND returns to the queue unconfirmed, never a "
                       "closed corpse; both the confirm and the closure stay "
                       "in history",
    },
    "chat_dismissal": {
        "reverse": _reverse_chat_dismissal,
        "reverses_via": "chat_dismissal_cleared",
        "description": "clear a mute/snooze written by a brain action",
    },
    # R1 (M ruling 2026-07-14): person/org creation from a STRUCTURED
    # CONNECTOR FACT is the one identity-shaped class allowed on the auto
    # tier — additive only, and ONLY because this reverser exists. The
    # detector itself is LB2; the policy row + reverser land now so LB2
    # needs no policy change.
    "person_org_creation_structured_fact": {
        "reverse": _reverse_person_org_creation,
        "reverses_via": "person_updated/org_updated (status → archived)",
        "description": "archive an auto-created contact/org (never delete; "
                       "history and provenance stay on file)",
    },
    # HIST1 Part 2 (D3/S1/S2): the structured-fact auto tier is legal ONLY
    # because this reverser exists (landed in the SAME commit as the
    # AUTO_ALLOWED entry, per the spec's step-10 mandate). Retraction is
    # additive — the renderers do the forgetting.
    "entity_fact_structured": {
        "reverse": _reverse_entity_fact_structured,
        "reverses_via": "entity_fact_retracted",
        "description": "retract an auto-noted structured fact (append the "
                       "retraction event; the history renderers suppress "
                       "the fact — the event itself is never edited)",
    },
    # SPEC BK1 (2026-08-16) — "tomorrow is about X" is a USER GESTURE, so this
    # reverser exists for the `undo` affordance the chat path advertises, NOT
    # to license an auto tier. Like the two UNCONFIRM1 classes it may never
    # join `brain_proposals.AUTO_ALLOWED`: nothing may state the CEO's own
    # intent on its own. Reversal is additive — the write stays in history and
    # the restore/retraction is appended.
    "day_intent": {
        "reverse": _reverse_day_intent,
        "reverses_via": "day_intent (the restoring record, or a "
                        "retraction when there was nothing before it)",
        "description": "take back what you said the day was about — the "
                       "previous answer comes back if there was one, "
                       "otherwise the day goes quiet again; the original "
                       "stays in history",
    },
    # T2.2 (FS-11b-extended backlog sweep): the sweep's expire tombstones are
    # undoable — the reverser appends person_proposal_reopened (additive; the
    # reader honors last-writer), so `undo` after a sweep restores the queue.
    "person_proposal_tombstone": {
        "reverse": _reverse_person_proposal_tombstone,
        "reverses_via": "person_proposal_reopened",
        "description": "reopen a person proposal the backlog sweep expired "
                       "or skipped (tombstone stays in history)",
    },
    # UXR1 D3 (M ruling 2026-07-21): the exact-unique-clean auto-link is
    # legal on the auto tier ONLY because this reverser exists (landed in
    # the same commit as the AUTO_ALLOWED entry). Undo reopens the mention
    # proposal AND re-opens a confirm-tier person_link row carrying the
    # original evidence — the reopened confirm row is also the re-auto
    # fence (propose(tier="auto") dedups against its open fingerprint).
    "person_link": {
        "reverse": _reverse_person_link,
        "reverses_via": "person_proposal_reopened + a confirm-tier "
                        "person_link brain_proposal",
        "description": "unwind an automatic name-mention link (no alias is "
                       "ever written on the auto rail, so this reversal is "
                       "complete); the decision returns to the human as a "
                       "confirm row",
    },
    # SPEC THREADBIND1 §0 ruling 3 — the 30-day prep_brief backfill
    # one-shot's own `thb_` batch. NEVER a candidate for
    # brain_proposals.AUTO_ALLOWED: the backfill is propose-first and
    # human-confirmed by design (§0 ruling 1's fail-open posture), the same
    # UNCONFIRM1 reasoning as commitment_confirm/commitment_done above.
    "prep_brief_thread_backfill": {
        "reverse": _reverse_prep_brief_thread_backfill,
        "reverses_via": "prep_brief_thread_backfill_undone",
        "description": "un-bind a thread the 30-day prep_brief backfill "
                       "assigned — the receipt's own backfill marker stays "
                       "in history; the undo marker is what "
                       "thread_resolve reads as 'no longer bound'",
    },
    # SPEC THREADANN1 §0 ruling 4 — the ONE-TAP confirmed thread-split's own
    # `tha_` batch. NEVER a candidate for `brain_proposals.AUTO_ALLOWED`:
    # a split is propose-first and human-confirmed by design (ruling 4's
    # "never auto-executed"), the same UNCONFIRM1/day_intent/
    # prep_brief_thread_backfill reasoning above.
    # SPEC_BACKFILL1 §M.4 — the R3 binding-backfill one-shot's own `bkf_`
    # batch. NEVER a candidate for `brain_proposals.AUTO_ALLOWED`: the
    # one-shot is 100% propose-confirm BY M'S RULING (spec §0.1 — no auto
    # lane exists in the module at all), the same UNCONFIRM1/thread_split
    # reasoning as its neighbors; any future auto tier lives in a separate
    # maintenance job gated on the §V measured-precision gate, never here.
    "binding_backfill": {
        "reverse": _reverse_binding_backfill,
        "reverses_via": "reclassification (restoring, supersedes the SAME "
                        "original event — later seq wins the fold)",
        "description": "release a re-bind the R3 binding backfill applied "
                       "— the backfill's own reclassification stays in "
                       "history; the original envelope reads back through "
                       "the fold",
    },
    "thread_split": {
        "reverse": _reverse_thread_split,
        "reverses_via": "reclassification (restoring, supersedes the "
                        "SAME original event) + a status->archived on "
                        "the split-created child thread",
        "description": "move a retagged event back to its parent thread "
                       "after a confirmed split, and archive the child "
                       "once its last event is restored — the split's own "
                       "reclassification events and the child thread "
                       "record stay in history (records never move)",
    },
    # DEALNAG1 (M ruling 4) — the automatic prospect -> client promotion.
    # THE reason the auto tier is legal here: both halves of the conversion
    # are reversible through the typed writers, and the reversal is a
    # standing answer (org_promotion.undone_promotions never promotes or
    # asks about that org again).
    "org_promotion": {
        "reverse": _reverse_org_promotion,
        "reverses_via": "relationship_type flip back to prospect + the "
                        "engagement edge deactivated (created) or restored "
                        "(updated) — both additive org_updated / "
                        "engagement_updated events; when the receipt says "
                        "the deal was manufactured by the same act (CUTB "
                        "item 4), the thread is archived and ONE "
                        "deal_won_reversed marker names the won event",
        "description": "put an org back to prospect after an automatic "
                       "promotion — the org_promoted receipt stays in "
                       "history and the org is never auto-promoted again; "
                       "a deal `mark won` opened with nothing on file goes "
                       "back too",
    },
    # QUIET1 D1 (M ruling 7, 2026-09-03) — the manifest auto_apply that
    # stamps `light` on every client workspace without a stored posture is
    # legal ONLY because this reverser exists (same commit — the step-10
    # mandate). Also the batch `ask me more` / `ask me less` write. The
    # reversal restores the previous stored config exactly, or clears the
    # key when there was none.
    "commitment_preset": {
        "reverse": _reverse_commitment_preset,
        "reverses_via": "skill_reconfigured (the previous config written "
                        "back) or the key cleared when there was none",
        "description": "put back how often it asks you — the previous "
                       "setting returns exactly, or the workspace goes back "
                       "to having no setting at all",
    },
    # CUT-A (M ruling R-A, 2026-09-06) — `turn on / off closing on
    # evidence` (`commitment_policy_pass.set_transcript_closes`). Same
    # store, same stamp shape, same reverser: the previous stored config
    # returns exactly (preset and switch together), or the key is cleared.
    "commitment_transcript_closes": {
        "reverse": _reverse_commitment_preset,
        "reverses_via": "skill_reconfigured (the previous config written "
                        "back) or the key cleared when there was none",
        "description": "put back whether promises close on meeting "
                       "evidence — the previous setting returns exactly",
    },
}


def has_reverser(change_class: str) -> bool:
    """The D2 legality half `brain_proposals.propose(tier='auto')` checks."""
    return change_class in REVERSERS


# ---------------------------------------------------------------------------
# Batch resolution
# ---------------------------------------------------------------------------

def _changes_for_sent_reconcile(events: list[dict], audit_seq: int) -> list[dict]:
    """The commitment closes narrated by ONE sent_reconcile run: every
    `commitment_resolved` with resolved_by == "sent_reconcile" appended after
    the PREVIOUS sent_reconcile audit event and before/at this one.

    UNDOGUARD: seq is read through `event_seq.event_seq`, never
    `ev.get("seq") or 0`. One string seq in the live substrate raised
    TypeError here and denied the ENTIRE listing — the safety net the auto
    tier rests on, taken down by one row. An event with no readable seq has
    no position in a half-open `(prev_seq, audit_seq]` window, so it is
    SKIPPED rather than defaulted to 0 (which silently placed all 1,168
    seq-less rows outside every window anyway, while pretending otherwise)."""
    from event_seq import event_seq

    prev_seq = 0
    found = False
    for ev in events:
        if ev.get("type") != "sent_reconcile":
            continue
        seq = event_seq(ev)
        if seq is None:
            # A seq-less audit event cannot anchor a window. Skip it rather
            # than let it reset prev_seq to 0 and widen the batch to
            # everything before it.
            continue
        if seq == audit_seq:
            found = True
            break
        prev_seq = seq
    if not found:
        raise BrainUndoError(f"no sent_reconcile audit event at seq {audit_seq}")
    out: list[dict] = []
    for ev in events:
        seq = event_seq(ev)
        if seq is None:
            continue
        if not (prev_seq < seq <= audit_seq):
            continue
        if ev.get("type") != "commitment_resolved":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("resolved_by") != "sent_reconcile":
            continue
        cid = data.get("commitment_id") or data.get("id") or data.get("target_id")
        if not cid:
            continue
        out.append({
            "change_class": "commitment_close",
            "change_ref": f"seq:{seq}",
            "commitment_id": cid,
        })
    return out


PARENT_BATCH_KEY = "parent_batch_id"
UNDO_GROUP_KEY = "undo_group"


def _in_batch(data: dict, batch_id: str) -> bool:
    """DD-5 — a row belongs to `batch_id` when it IS that batch or sits in a
    group UNDER that run. A group id matches only its own rows."""
    return (data.get("brain_batch_id") == batch_id
            or data.get(PARENT_BATCH_KEY) == batch_id)  # DD-5: a run ref resolves its groups


def _changes_for_brain_batch(events: list[dict], batch_id: str) -> list[dict]:
    from event_seq import event_seq

    out: list[dict] = []
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if not _in_batch(data, batch_id):
            continue
        cls = data.get("brain_change_class")
        if not cls:
            continue
        # UNDOGUARD: the ref is minted from the NORMALIZED seq, so it matches
        # the normalized key `undone_auto_merges` indexes pairs under. A raw
        # `f"seq:{1957.0}"` would mint "seq:1957.0" and never pair with
        # "seq:1957" — a silently-wrong sibling of the crash.
        change = {
            "change_class": cls,
            "change_ref": f"seq:{event_seq(ev)}",
        }
        for key in ("commitment_id", "dismissal_seq", "person_id", "org_id",
                    "proposal_seq", "proposal_fingerprint",
                    # POLICY1-B (b) — the due reverser's anchor. Read with
                    # `in`, not `is not None`: a prior_due of null IS the
                    # answer ("it had no date").
                    "prior_due",
                    # UXR1 D3 — the person_link reverser's re-propose payload
                    # (stamped on the same_as tombstones at auto-link time).
                    "alias", "link_fingerprint", "link_evidence",
                    "matched_name",
                    # AUTOAPPLY §4c — the merge reverser needs BOTH sides to
                    # name the pair it is putting back on the flag tier
                    # (supersede_commitment stamps superseded_by + the score).
                    "superseded_by", "auto_merge_score",
                    # SPEC THREADBIND1 §0 ruling 3 — the prep_brief backfill
                    # reverser's anchor: which meeting's binding to undo.
                    "meeting_id",
                    # SPEC THREADANN1 §0 ruling 4 — the thread_split
                    # reverser's anchor: which original event to restore
                    # (supersedes_seq — the reclassification's own
                    # top-level field is ALSO mirrored into `data` so this
                    # data-driven scan can see it) and which thread pair
                    # to restore it between. `old_related_thread_ids`
                    # (REVIEW THREADANN1 F4) is what lets the reverser put
                    # the ORIGINAL related ids back instead of erasing
                    # them with [].
                    "supersedes_seq", "old_primary_thread_id",
                    "new_primary_thread_id", "old_related_thread_ids",
                    # SPEC_BACKFILL1 §M.4 — the binding_backfill reverser's
                    # second anchor: WHICH thread this batch bound the event
                    # to (for a bound-elsewhere row the primary pair is
                    # unchanged, so the target is the only way to know what
                    # to check before restoring).
                    "target_thread_id",
                    # DEALNAG1 — the org_promotion reverser's anchors: which
                    # engagement edge to put back, whether the promotion
                    # created it, and what it looked like before.
                    "engagement_id", "engagement_created",
                    "prev_engagement_label", "prev_engagement_active",
                    "prev_engagement_kind",
                    # CUTB item 4 — the deal behind the win, and whether the
                    # same act opened it (then the undo puts it back too).
                    "deal_thread_id", "won_seq", "deal_manufactured",
                    # QUIET1 — the commitment_preset reverser's anchors:
                    # which config store, whether anything was stored
                    # before, and what it was (a dict, or None).
                    "skill_name", "prev_config_present", "prev_config",
                    "brain_batch_id"):
            if data.get(key) is not None:
                change[key] = data[key]
        if "prior_due" in data and "prior_due" not in change:
            change["prior_due"] = data["prior_due"]  # (b): null is a value here
        for key in ("prior_counterparty_id", "prior_counterparty_name"):
            if key in data and key not in change:
                change[key] = data[key]  # F-9: null is a value here too
        for key in ("observed_id", "meeting_seq"):  # DD-7 / F-1 — the observed reverser's anchors
            if data.get(key) is not None and key not in change:
                change[key] = data[key]
        out.append(change)
    return out


def undone_auto_merges(workspace_root) -> set:
    """The `(superseded_id, survivor_id)` pairs a human has REVERSED — the
    durable negation `commitment_dedup.apply_auto_merges` reads before it
    applies a stamped merge (AUTOAPPLY §4c, review F-1).

    THE DEFECT THIS CLOSES: the auto-merge stamp lives on the capture event
    and the substrate is append-only, so `_reverse_commitment_merge` cannot
    erase it. Reopening the item therefore left it in a state where the very
    next fire re-applied the merge the user had just reversed — an undo that
    does not survive one fire is not a reversal, and REVERSIBLE is the
    predicate licensing the auto tier at all. §4a answers its half of this
    shape by minting a confirm-tier row whose fingerprint blocks a re-auto;
    the applier reads THIS rather than relying on the same proposal-dedup
    side effect, because a proposal expires on its TTL and resolves the
    moment the user answers it, while a reversal has to outlive both.

    Lives here because this module owns the `brain_change_undone` marker
    `undo_batch` appends after a reverser runs (`data.change_ref ==
    "seq:<that event's seq>"`, `data.reverser == "commitment_merge"`); it
    pairs that against the `commitment_superseded` carrying
    `data.auto_merge` (which names both sides), written by
    `commitment_state.supersede_commitment` — read-only here.

    PAIR-keyed, never id-keyed: reversing "A merges into B" says nothing
    about "A merges into C" — a different decision on different evidence.
    That is also what keeps the negation from over-blocking a fresh pair.

    Read-only."""
    from event_seq import event_seq

    pair_by_seq: dict = {}
    markers: list = []
    for ev in _load_events(workspace_root):
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "commitment_superseded" and d.get("auto_merge"):
            cid = d.get("commitment_id")
            survivor = d.get("superseded_by") or d.get("survivor_id")
            # UNDOGUARD: normalized before it becomes a dict key. This index is
            # the durable negation that stops a reversed auto-merge being
            # re-applied on the next fire; a key that fails to match reads as
            # "never undone" and silently re-merges what the user just split.
            seq = event_seq(ev)
            if cid and survivor and seq is not None:
                pair_by_seq[str(seq)] = (str(cid), str(survivor))
        elif ev.get("type") == "brain_change_undone" and \
                d.get("reverser") == "commitment_merge":
            markers.append(str(d.get("change_ref") or ""))
    undone: set = set()
    for ref in markers:
        if not ref.startswith("seq:"):
            continue
        pair = pair_by_seq.get(ref[4:])
        if pair:
            undone.add(pair)
    return undone


def resolve_batch(workspace_root, batch_ref: dict) -> List[dict]:
    """Resolve a batch ref into concrete change records (no writes)."""
    if not isinstance(batch_ref, dict) or "kind" not in batch_ref:
        raise BrainUndoError(f"unresolvable batch ref: {batch_ref!r}")
    events = _load_events(workspace_root)
    kind = batch_ref["kind"]
    if kind == "sent_reconcile":
        return _changes_for_sent_reconcile(events, int(batch_ref["seq"]))
    if kind == "brain_batch":
        return _changes_for_brain_batch(events, str(batch_ref["batch_id"]))
    raise BrainUndoError(f"unknown batch kind: {kind!r}")


RECENT_BATCH_LIST_DAYS = 7


def recent_auto_batches(workspace_root, *, days: int = RECENT_BATCH_LIST_DAYS,
                        now_iso: Optional[str] = None) -> List[dict]:
    """AUTOAPPLY §8 — the batches a bare `undo` can offer in a FRESH chat.

    THE GAP THIS CLOSES: in the moment, and an hour later in the same chat,
    bare `undo` routes off the narrating surface's own advertised batch ref
    (D5, unchanged). Next Monday in a new chat there is no narration in
    context, so `undo` had no route at all — the affordance the auto tier's
    safety rests on simply vanished with the conversation.

    Returns newest-first `[{batch_ref, kind, label, n_changes, ts}]` over
    both resolvable shapes: `brain_batch` groupings (any event stamped
    `data.brain_batch_id` + `data.brain_change_class`) and `sent_reconcile`
    audits. `days` bounds the LISTING only — reversal legality never
    expires, because every reverser is additive and therefore always safe;
    a 7-day window just matches change-feed relevance.

    Read-only. The caller renders the list and calls `undo_batch` with the
    chosen `batch_ref`."""
    from event_seq import event_seq
    from event_time import event_time, parse_ts

    now = parse_ts(now_iso) if now_iso else _now_utc()
    cutoff = now - _timedelta(days=days) if now else None
    events = _load_events(workspace_root)

    batches: dict = {}
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        when = parse_ts(event_time(ev))
        if cutoff is not None and when is not None and when < cutoff:
            continue
        bid = data.get("brain_batch_id")
        if bid and data.get("brain_change_class"):
            slot = batches.setdefault(
                str(bid), {"batch_ref": {"kind": "brain_batch",
                                         "batch_id": str(bid)},
                           "kind": "brain_batch", "n_changes": 0,
                           "ts": event_time(ev), "classes": set(),
                           "parent_ref": None, "group": None,
                           "children": []})
            slot["n_changes"] += 1
            slot["classes"].add(data.get("brain_change_class"))
            if event_time(ev) > slot["ts"]:
                slot["ts"] = event_time(ev)
            # DD-5 — a grouped row names its run and its group key; the run
            # gets a slot of its own (it may have no rows directly on it).
            parent = data.get(PARENT_BATCH_KEY)
            if parent and str(parent) != str(bid):
                slot["parent_ref"] = str(parent)
                slot["group"] = str(data.get(UNDO_GROUP_KEY) or "")
                run = batches.setdefault(
                    str(parent), {"batch_ref": {"kind": "brain_batch",
                                                "batch_id": str(parent)},
                                  "kind": "brain_batch", "n_changes": 0,
                                  "ts": event_time(ev), "classes": set(),
                                  "parent_ref": None, "group": None,
                                  "children": []})
                run["n_changes"] += 1
                run["classes"].add(data.get("brain_change_class"))
                if event_time(ev) > run["ts"]:
                    run["ts"] = event_time(ev)
        elif ev.get("type") == "sent_reconcile":
            # UNDOGUARD: `int(ev["seq"])` raised ValueError on a non-numeric
            # string and TypeError on a list/dict. event_seq returns None for
            # anything unusable and the audit is skipped — one malformed
            # audit row must never deny the whole `undo` listing.
            audit_seq = event_seq(ev)
            if audit_seq is None:
                continue
            key = f"sent_reconcile:{audit_seq}"
            n = len(_changes_for_sent_reconcile(events, audit_seq))
            if not n:
                continue
            batches[key] = {"batch_ref": {"kind": "sent_reconcile",
                                          "seq": audit_seq},
                            "kind": "sent_reconcile", "n_changes": n,
                            "ts": event_time(ev), "classes": {"commitment_close"},
                            "parent_ref": None, "group": None, "children": []}

    out = []
    for slot in batches.values():
        classes = sorted(slot.pop("classes"))
        slot["label"] = _batch_label(classes, slot["n_changes"])
        if slot.get("group"):
            slot["label"] = f"{slot['label']} — {_group_phrase(slot['group'])}"
        out.append(slot)
    # DD-5 — nest every group under its run. A group whose run fell outside
    # the window (or was never stamped) stays a top-level entry.
    by_id = {b["batch_ref"]["batch_id"]: b for b in out if b["kind"] == "brain_batch"}
    top = []
    for b in out:
        b.setdefault("children", [])
        b.setdefault("parent_ref", None)
        b.setdefault("group", None)
        parent = b.get("parent_ref")
        if parent and parent in by_id:
            by_id[parent]["children"].append(b)
        else:
            top.append(b)
    for b in top:
        b["children"].sort(key=lambda c: c["ts"], reverse=True)
    top.sort(key=lambda b: b["ts"], reverse=True)
    return top


def _group_phrase(group_key: str) -> str:
    """The group key as a person would say it: a meeting, a project, or the
    run itself. Never the raw ref — it is read by the person choosing what
    to reverse."""
    g = str(group_key or "")
    if g.startswith("granola:") or g.startswith("meeting:"):
        return "one meeting"
    if g.startswith("project_") or g.startswith("thread_"):
        return "one project"
    if g.startswith("gmail:") or g.startswith("mail:") or g.startswith("outlook:"):
        return "one message"
    return "the whole run"


def newest_run(batches: List[dict]) -> Optional[dict]:
    """DD-5 `undo all` — the newest listed run (top-level entry). None when
    nothing automatic is listed."""
    return batches[0] if batches else None


def undo_listing_lines(batches: List[dict]) -> List[str]:
    """The bare-`undo` listing, numbered newest first, one line per run and
    one indented line per group under it (DD-5 / DD-10). `1` reverses the
    run, `1a` / `1b` reverse one group. Labels, never class names."""
    lines: List[str] = []
    for i, b in enumerate(batches, 1):
        day = str(b.get("ts") or "")[:10]
        lines.append(f"{i}. {b['label']} — {day}")
        kids = b.get("children") or []
        for j, c in enumerate(kids):
            letter = chr(ord("a") + j) if j < 26 else str(j + 1)
            lines.append(f"   {i}{letter}. {c['label']} (undo just this group)")
    return lines


def batch_ref_for_ordinal(batches: List[dict], token: str) -> Optional[dict]:
    """`1` -> the run's ref, `1a` -> its first group's ref, `all` -> the
    newest run. None when the token names nothing listed."""
    t = str(token or "").strip().lower()
    if not t:
        return None
    if t == "all":
        run = newest_run(batches)
        return run["batch_ref"] if run else None
    import re as _re
    m = _re.match(r"^(\d+)([a-z]?)$", t)
    if not m:
        return None
    i = int(m.group(1)) - 1
    if i < 0 or i >= len(batches):
        return None
    if not m.group(2):
        return batches[i]["batch_ref"]
    j = ord(m.group(2)) - ord("a")
    kids = batches[i].get("children") or []
    if j < 0 or j >= len(kids):
        return None
    return kids[j]["batch_ref"]


# Change class → the phrase a human recognizes. Never the class name itself:
# the list is read by the person deciding whether to reverse it, and
# "commitment_merge ×1" is not a thing anyone said or saw happen.
_CLASS_PHRASES = {
    "commitment_close": "closed a commitment",
    "commitment_merge": "merged a duplicate capture",
    # UNCONFIRM1 — never written by an auto detector (neither class is in
    # AUTO_ALLOWED), so these phrases exist for a surface that narrates a
    # USER's own batch. A class name is not a thing anyone said or saw happen.
    "commitment_confirm": "confirmed a captured item",
    "commitment_due": "moved a due date",
    "commitment_reassign": "put a name on an item",
    "commitment_park": "parked a resting item",
    "commitment_close_from_observed": "closed a set-aside scheduling guess",
    "commitment_done": "said a captured item was already done",
    # SPEC BK1 — never written by an auto detector; the phrase exists for a
    # surface narrating the CEO's OWN batch, and "day_intent ×1" is not a
    # thing anyone said or saw happen.
    "day_intent": "set what the day is about",
    "person_link": "linked a name to an existing contact",
    "person_org_creation_structured_fact": "added a contact",
    "entity_fact_structured": "noted a fact",
    "person_proposal_tombstone": "cleared an identity row",
    "chat_dismissal": "muted a row",
    "prep_brief_thread_backfill": "bound a past brief to a thread",
    "thread_split": "moved an event back after a thread split",
    # BACKFILL2 — the binding-review widget's `bkf_` batch, listed by a bare
    # `undo` in a fresh chat. Never written by an auto detector.
    "binding_backfill": "filed a past record under its project",
    # DEALNAG1 — what the person actually saw happen in the CHANGED feed.
    "org_promotion": "promoted a prospect to client",
    # QUIET1 — the preset stamp (manifest auto_apply, `ask me more/less`).
    "commitment_preset": "set how often it asks you",
    # CUT-A — the closing-on-evidence switch (`turn on/off closing on evidence`).
    "commitment_transcript_closes": "set whether promises close on meeting evidence",
}


def _batch_label(classes: list, n: int) -> str:
    phrases = [_CLASS_PHRASES.get(c, c.replace("_", " ")) for c in classes]
    head = phrases[0] if len(phrases) == 1 else " + ".join(phrases[:3])
    return f"{head}{'' if n == 1 else f' (×{n})'}"


def _now_utc():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _timedelta(**kw):
    from datetime import timedelta

    return timedelta(**kw)


def undo_batch(
    workspace_root,
    batch_ref: dict,
    *,
    undone_by: str,
    source_skill: str,
) -> dict:
    """Reverse everything one narrated batch changed (D5 — the commitment-
    triage batch-undo pattern, generalized). Per change: run the class's
    registered reverser (its additive reversing event), then append ONE
    `brain_change_undone` marker. Per-item failures are collected; the batch
    never aborts. Returns {status, n_undone, n_errors, results}."""
    from event_gate import append_event

    changes = resolve_batch(workspace_root, batch_ref)
    events_path = _events_path(workspace_root)
    batch_label = (
        f"{batch_ref.get('kind')}:{batch_ref.get('seq', batch_ref.get('batch_id'))}"
    )
    results: list[dict] = []
    n_undone = 0
    n_errors = 0
    n_already = 0   # F-9 (review): a row already in the restored state is not "undone"

    def _record(change, cls, reversed_result):
        nonlocal n_undone, n_already
        # REVIEW_POLICY1B F-9 — a reverser answering `already_open` /
        # `already_unconfirmed` / `already_undone` / `not_open` reversed
        # nothing: count it apart, write no marker for it.
        st = (reversed_result or {}).get("status") if isinstance(reversed_result, dict) else None
        if isinstance(st, str) and (st.startswith("already_") or st == "not_open"):
            results.append({"status": "already", "change": change, "result": reversed_result})
            n_already += 1
            return
        append_event(events_path, {
            "type": "brain_change_undone",
            "source_skill": source_skill,
            "data": {
                "change_ref": change["change_ref"],
                "reverser": cls,
                "batch_ref": batch_label,
                "undone_by": undone_by,
            },
        }, holder="brain_undo")
        results.append({"status": "undone", "change": change,
                        "result": reversed_result})
        n_undone += 1

    # POLICY1-B F-9 — reverse NEWEST FIRST (a stack, not a queue). A batch
    # that wrote a reassign and then a confirm must un-confirm before it
    # un-reassigns: the confirm reverser refuses a row "touched since the
    # confirm", and the restore written first would be that touch.
    # F-6 — a RUN of same-class changes whose reverser offers `reverse_many`
    # goes through it as one call (one lock, one scan), still newest-first.
    ordered = list(reversed(changes))  # F-9: LIFO
    i = 0
    while i < len(ordered):
        change = ordered[i]
        cls = change["change_class"]
        entry = REVERSERS.get(cls)
        if entry is None:
            results.append({"status": "error", "change": change,
                            "error": f"no reverser registered for {cls!r}"})
            n_errors += 1
            i += 1
            continue
        many = entry.get("reverse_many")
        if many is not None:
            j = i
            while j < len(ordered) and ordered[j]["change_class"] == cls:
                j += 1
            run = ordered[i:j]
            try:
                outs = many(workspace_root, run, undone_by=undone_by, source_skill=source_skill)
            except Exception as exc:
                for ch in run:
                    results.append({"status": "error", "change": ch,
                                    "error": f"{type(exc).__name__}: {exc}"})
                    n_errors += 1
                i = j
                continue
            for ch, res in zip(run, outs):
                _record(ch, cls, res)
            i = j
            continue
        try:
            reversed_result = entry["reverse"](
                workspace_root, change,
                undone_by=undone_by, source_skill=source_skill,
            )
        except Exception as exc:  # loud per-item, contained per-batch
            results.append({"status": "error", "change": change,
                            "error": f"{type(exc).__name__}: {exc}"})
            n_errors += 1
            i += 1
            continue
        _record(change, cls, reversed_result)
        i += 1
    status = "undone" if n_undone and not n_errors else (
        "partial" if n_undone else ("empty" if not changes else
                                    ("already" if n_already and not n_errors else "error")))
    return {"status": status, "n_undone": n_undone, "n_errors": n_errors,
            "n_already": n_already, "results": results}


__all__ = [
    "REVERSERS",
    "RECENT_BATCH_LIST_DAYS",
    "PARENT_BATCH_KEY", "UNDO_GROUP_KEY",
    "has_reverser",
    "recent_auto_batches",
    "newest_run", "undo_listing_lines", "batch_ref_for_ordinal",
    "undone_auto_merges",
    "resolve_batch",
    "undo_batch",
    "BrainUndoError",
]
