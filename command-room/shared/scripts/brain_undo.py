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

import re
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
    """Every event on the book, rotated shards included.

    CLOSETRUTH1 3.3 — through `events_io`, the one full-history reader
    (standing fence 6). The doctrine this serves is already written at
    `recent_auto_batches`: reversal legality never expires, because every
    reverser is additive. A reader that could not see a rotated shard would
    quietly make it expire at the turn of a year — "I can't find that batch"
    for an act that is still perfectly reversible. `events_io` also puts an
    unreadable or truncated shard ON THE RECORD (its read-alarm sidecar)
    instead of serving a short history silently, which is the difference
    that matters here: an undo listing that is short for an unsaid reason is
    worse than one that fails loudly.
    """
    from events_io import load_all

    path = _events_path(workspace_root)
    if not path.exists():
        return []
    return load_all(workspace_root)


# ---------------------------------------------------------------------------
# Reversers — one per change class. Each takes (workspace_root, change, *,
# undone_by, source_skill) and returns the reversing-writer's result dict.
# `change` is a dict carrying the class-specific target id(s) plus
# `change_ref` (the audit anchor the brain_change_undone marker records).
# ---------------------------------------------------------------------------

def _undo_actor_is_person(undone_by) -> bool:
    """ATTRIB2 fix round 1 (reviewer F-1/F-2) — `undo_batch` resolves the
    actor ONCE for the whole gesture; a reverser that calls a writer which
    resolves again must not let it RE-DECIDE. `undone_by` arrives here
    already resolved, so its own spelling is the answer: a person id means
    the batch was ruled the customer's, and the inner writer is told so with
    the customer's escape (`user_confirmed=True`). A rail's name means the
    batch was ruled the machine's, and re-resolving under the same background
    `source_skill` returns the same rail name — idempotent either way.

    Without this, a person's typed `undo` on a surface that ALSO runs
    unattended work (`cleanup`, `session-sweep`, `meeting-notes`) lost their
    id inside `reopen_commitment`, one layer under the resolution that had
    just kept it."""
    from event_types import is_person_actor
    return is_person_actor(undone_by)


def _reverse_commitment_close(workspace_root, change, *, undone_by, source_skill):
    from commitment_state import reopen_commitment

    return reopen_commitment(
        workspace_root,
        change["commitment_id"],
        reopened_by=undone_by,
        reason=change.get("reason") or "brain undo — batch reversal",
        source_skill=source_skill,
        user_confirmed=_undo_actor_is_person(undone_by),
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


def _reverse_counter_evidence_reopen(workspace_root, change, *, undone_by,
                                     source_skill):
    """EXIT1 FIX ROUND 2 (review R-4) — a put-back is an act, so it has an
    undo. Their reply put a row an exit rail had closed back on the plate;
    undoing that means CLOSING IT AGAIN, exactly as it was closed: the same
    `resolved_by`, the same evidence sentence, the same resolution, carried
    on the reopen when it was written. Nothing reverses a reversal by
    guessing, so a reopen that carries no record of the closure it undid is
    refused rather than closed on invented evidence."""
    from commitment_state import close_commitment

    prior_by = str(change.get("prior_resolved_by") or "")
    prior_skill = str(change.get("prior_source_skill") or "")
    if not prior_by or not prior_skill:
        return {"status": "refused", "commitment_id": change.get("commitment_id"),
                "detail": "the closure this put back was not recorded"}
    # EXIT1 FIX ROUND 3 (review L-1) — and WHICH ROUTE closed it. Without it
    # the re-closed row fell out of its own route's count and was filed
    # under "other" the second time round.
    extra = {"brain_change_class": "commitment_close",
             "reclosed_after_put_back": True,
             "undone_by": undone_by}
    if str(change.get("prior_exit_route") or "").strip():
        extra["exit_route"] = str(change["prior_exit_route"]).strip()
    return close_commitment(
        workspace_root, change["commitment_id"],
        resolved_by=prior_by,
        evidence=str(change.get("prior_evidence")
                     or "closed again — the put-back was undone"),
        resolution=str(change.get("prior_resolution") or "done"),
        # The RAIL closes again, not the session lane: this id came off the
        # rail's own audit row, not out of a model reading the substrate
        # (CLOSEID2 is about the latter, and this is the former).
        source_skill=prior_skill,
        source_ref=change.get("prior_source_ref"),
        extra_data=extra)


def _reverse_counter_evidence_unrest(workspace_root, change, *, undone_by,
                                     source_skill):
    """EXIT1 FIX ROUND 2 (review R-5) — the other side coming back about a
    resting item takes it off the resting list. Undoing that rests it again,
    with the reason and the actor the original rest carried."""
    from commitment_state import park_commitments

    reason = str(change.get("prior_park_reason") or "")
    parked_by = str(change.get("prior_parked_by") or "")
    if not reason or not parked_by:
        return {"status": "refused", "commitment_id": change.get("commitment_id"),
                "detail": "the rest this undid was not recorded"}
    res = park_commitments(
        workspace_root,
        [{"commitment_id": change["commitment_id"], "reason": reason,
          "extra_data": {"exit_route": "silence", "re_rested": True,
                         "undone_by": undone_by}}],
        parked_by=parked_by, source_skill=parked_by)
    return res[0] if res else {"status": "refused",
                               "commitment_id": change.get("commitment_id")}


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
        user_confirmed=_undo_actor_is_person(undone_by),
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


def _reverse_person_alias_dropped(workspace_root, change, *, undone_by,
                                  source_skill):
    # IDENT1 rule 4 — put back a nickname the collision rule dropped. The
    # drop was two writes (the resolution mapping and the record's own
    # aliases array) and `add_person_alias` makes exactly those two, which
    # is why this reversal is COMPLETE rather than approximately complete.
    #
    # Re-adding is refused when the spelling has since been saved onto a
    # DIFFERENT record — the adder raises rather than silently re-pointing,
    # and that refusal is correct: putting this one back would recreate the
    # ambiguity the drop existed to end, on top of somebody's later decision.
    person_id = change.get("person_id")
    alias = change.get("alias")
    if not person_id or not alias:
        raise BrainUndoError(
            "person_alias_dropped reversal needs person_id and alias")
    from people_writer import add_person_alias

    res = add_person_alias(workspace_root, person_id, alias,
                           source_skill=source_skill)
    return {"status": res.get("status"), "person_id": person_id,
            "alias": alias}


def _reverse_person_split(workspace_root, change, *, undone_by, source_skill):
    # IDENT1 rule 4 — RE-FOLD. The split made exactly one write: a new record
    # carrying the second person's name and the address the two of them share.
    # The record they were split OUT of was never touched, so putting things
    # back is two flips on the new record and nothing else:
    #
    #   1. status -> archived (R1's archive-never-delete — the name, the
    #      history and the provenance stay on file);
    #   2. the shared address CLEARED off it.
    #
    # Step 2 is what makes this its own reverser rather than R1's. An archived
    # record still sits in `entities.json`, and `find_existing_person`'s Tier 1
    # walks every record regardless of status — so an archived split record
    # still holding the shared address is a second Tier-1 answer for that
    # address, and which one a lookup returns comes down to list order. That is
    # a reversal that leaves residue, which is not a reversal.
    person_id = change.get("person_id")
    if not person_id:
        raise BrainUndoError("person_split reversal needs person_id")
    from people_writer import update_person

    rec = update_person(workspace_root, person_id, source_skill=source_skill,
                        status="archived", email=None, suppress_lineage=True)
    return {"status": "refolded", "person_id": person_id, "record": rec}


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


def _reverse_brain_proposal_expiry(workspace_root, change, *, undone_by,
                                   source_skill):
    """TTL1 — put back a Living-Brain proposal (a deal signal, a housekeeping
    row) that the question-expiry engine let go when nobody answered it.

    THE REASON THIS EXISTS AT ALL. The shipped expiry sweep
    (`brain_proposals.expire_stale`) writes the identical tombstone with no
    batch and no change class, so nothing could ever put one back: an
    automatic drop with no way home. TTL1's engine may only let a question go
    BECAUSE this reverser exists — the same rule the auto tier lives under.

    Additive, like every reverser here: the tombstone stays in history and a
    `brain_proposal_reopened` marker is appended; `brain_proposals`'s
    projector honours the LAST writer, so the proposal re-surfaces on the
    Staff Meeting exactly where it was."""
    from event_gate import append_event

    pid = change.get("proposal_id")
    if not pid:
        raise BrainUndoError(
            "brain_proposal_expiry reversal needs the proposal_id the "
            "expiry tombstone names — without it there is nothing to reopen")
    data = {
        "proposal_id": str(pid),
        "reopened_by": undone_by,
        "reason": change.get("reason") or "brain undo — batch reversal",
    }
    if change.get("question_class"):
        data["question_class"] = str(change["question_class"])
    append_event(_events_path(workspace_root), [{
        "type": "brain_proposal_reopened",
        "source_skill": source_skill,
        "data": data,
    }], holder="brain_undo")
    return {"status": "reopened", "proposal_id": str(pid)}


# change_class -> {reverse, reverses_via, description}. `reverses_via` names
# the additive reversing event the callable appends — documentation the
# tests assert so the registry can't silently drift from the doctrine.
def _reverse_deal_leg(workspace_root, change, *, undone_by, source_skill):
    """CLOSETRUTH1 3.2 (M's ruling 5, 2026-09-13) — PUT THE WIN BACK. One
    helper, called from BOTH handles on the same act: the promotion's
    reverser and the win's own reverser. Returns a dict describing what it
    did, or None when the change names no deal.

    THE RULE M RULED: `mark [org] won` + `undo` reverses everything — deal,
    org, win — on every path. Before this, the deal leg ran only when the
    receipt said the win had been MANUFACTURED by the same act, so the
    2026-09-07 walk (no deal on file) reversed whole and the 2026-09-13 walk
    (a real deal on file) reversed the org alone: the deal stayed closed-won,
    the thread stayed resolved, the 90-day win rate kept counting it, and the
    product told the person there was no way back.

    What it puts back, per path:
      * a MANUFACTURED deal — the thread this act opened is archived (never
        deleted), exactly as it has been since CUTB item 4. There is no prior
        status to restore: before the act, the thread did not exist.
      * a REAL deal — the deal object drops its terminal `outcome` and
        `closed_at` and goes back to the stage it was at, and the thread goes
        back to the status it had before the close.
    Both paths write ONE `deal_won_reversed` marker naming the thread and the
    won event's seq, which is what `deal_state.won_reversals` folds out of
    `list_closed_deals` and `pipeline_math.won_rate_90d`.

    IDEMPOTENT BY CONSTRUCTION, because a single batch may carry both handles
    (the `deal_won` row and the `org_promoted` row of one act): the marker is
    written once, the archive writer is already idempotent, and restoring a
    deal that is no longer closed is a no-op."""
    deal_tid = change.get("deal_thread_id")
    if not deal_tid:
        return None
    from deal_state import won_reversals
    from event_gate import append_event

    deal_tid = str(deal_tid)
    org_id = change.get("org_id")
    out: dict = {"deal_thread_id": deal_tid}
    already = won_reversals(workspace_root).get(deal_tid)
    if change.get("deal_manufactured") is True:
        from thread_archive import archive_thread

        arch = archive_thread(
            workspace_root, deal_tid,
            reason="undo — this deal was opened by `mark won` with nothing "
                   "on file, and the person put it back",
            source_skill=source_skill)
        out["deal_archived"] = arch.get("status")
    else:
        out.update(_restore_closed_deal(workspace_root, deal_tid, change,
                                        source_skill=source_skill))
    if already is None:
        ev = {"type": "deal_won_reversed", "source_skill": source_skill,
              "primary_thread_id": deal_tid,
              "org_ids": [org_id] if org_id else [],
              "data": {"thread_id": deal_tid,
                       **({"org_id": org_id} if org_id else {}),
                       "won_seq": change.get("won_seq"),
                       "reversed_by": undone_by,
                       "reason": "brain undo — the win was put back, and the "
                                 "deal with it"}}
        append_event(_events_path(workspace_root), [ev], holder="brain_undo")
        out["won_reversed"] = True
    else:
        out["won_reversed"] = "already_reversed"
    return out


def _restore_closed_deal(workspace_root, thread_id, change, *, source_skill):
    """The REAL-deal half of `_reverse_deal_leg`: the deal object goes back to
    the stage it was at with no terminal outcome, and the thread goes back to
    the status it had. Through `thread_writer.update_thread` — the same typed
    writer `close_deal` used to make the change — so the reversal is one
    validated, receipted record write like every other reverser here.

    A deal that is already open again (a second `undo` on one act, or the
    person re-opened it by hand) is left alone and says so."""
    import json as _json
    from thread_writer import DEAL_STAGES, update_thread

    path = _entities_path(workspace_root)
    try:
        data = _json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BrainUndoError(
            f"cannot read the records to put the deal back: {exc}") from exc
    from entities_io import entities_collection

    thread = next((t for t in entities_collection(data, "projects")
                   if isinstance(t, dict) and t.get("id") == thread_id), None)
    if thread is None:
        return {"deal_restored": "thread_not_found"}
    deal = thread.get("deal")
    if not isinstance(deal, dict) or not deal.get("outcome"):
        return {"deal_restored": "already_open"}
    new_deal = {k: v for k, v in deal.items()
                if k not in ("outcome", "closed_at", "loss_reason",
                             "loss_note")}
    prev_stage = change.get("prev_deal_stage")
    if prev_stage in DEAL_STAGES:
        new_deal["stage"] = prev_stage
    elif new_deal.get("stage") not in DEAL_STAGES:
        # The receipt predates the anchor and the record carries no usable
        # stage. `lead` is the floor of the fixed stage set — the honest
        # place for a deal whose progress nobody wrote down — and the status
        # quo (a closed-won deal nobody can reverse) is the defect.
        new_deal["stage"] = DEAL_STAGES[0]
    fields = {"deal": new_deal}
    prev_status = change.get("prev_thread_status")
    if prev_status:
        fields["status"] = str(prev_status)
    elif thread.get("status") in ("resolved", "archived"):
        fields["status"] = "active"
    update_thread(workspace_root, thread_id, source_skill=source_skill,
                  **fields)
    return {"deal_restored": "restored",
            "deal_stage": new_deal.get("stage"),
            "thread_status": fields.get("status", thread.get("status"))}


def _reverse_deal_won(workspace_root, change, *, undone_by, source_skill):
    """CLOSETRUTH1 3.2 — reverse ONE win, on its own. The handle a win closed
    BY WORD ("mark Sample Supply won", "Sample Supply signed") carries in its
    own right, so a person who reverses it does not have to have promoted an
    org first. When the same batch also carries the promotion, both rows run
    and the deal leg lands exactly once (`_reverse_deal_leg` is idempotent)."""
    if not change.get("deal_thread_id"):
        raise BrainUndoError(
            "deal_won reversal needs deal_thread_id on the deal_won event's "
            "data (deal_state.close_deal stamps it — a batch row without one "
            "is malformed)")
    out = _reverse_deal_leg(workspace_root, change, undone_by=undone_by,
                            source_skill=source_skill)
    return {"status": "win_reversed", **(out or {})}


def _reverse_org_promotion(workspace_root, change, *, undone_by, source_skill):
    """DEALNAG1 — reverse ONE automatic prospect -> client promotion: flip
    `relationship_type` back to prospect and put the engagement edge back
    the way it was (deactivate the edge the promotion CREATED; restore the
    label / kind / active flag of one it UPDATED). Both writes go through
    the typed writers, so the reversal is additive history like every other
    reverser here — records never move, the org_promoted event stays on
    file, and the change-feed line keeps its refs.

    CLOSETRUTH1 3.2 (M's ruling 5, 2026-09-13) — THE DEAL LEG RUNS ON EVERY
    PATH. `_reverse_deal_leg` is the whole of it: the `deal_won_reversed`
    marker, the manufactured thread archived or the real deal put back at the
    stage it was at with the thread at the status it had. This used to be
    gated on `deal_manufactured`, and that split was written down here as
    doctrine — the retired sentence is not restated, because a reader must
    not be able to mistake it for the rule. M ruled against it on 2026-09-13,
    after `mark won` + `undo` left a closed-won deal
    with nothing paid or signed standing on his book, in the closed-deals
    list and in the 90-day win rate, with the product saying there was no way
    back. One rule now: undo reverses all of it. Idempotent — a thread whose
    win is already reversed gets no second marker, which is what lets the
    win's OWN handle (`deal_won`) sit in the same batch."""
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
    # CLOSETRUTH1 3.2 — the deal goes back too, on EVERY path. One helper,
    # shared with the win's own handle, idempotent so a batch carrying both
    # rows writes one marker and puts the deal back once.
    leg = _reverse_deal_leg(workspace_root, change, undone_by=undone_by,
                            source_skill=source_skill)
    if leg:
        out.update(leg)
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
    from skill_config_writer import save_skill_config, wipe_skill_config_result
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
    # DEL1 fix round 1 (M-3): the wipe reports what actually happened. On a
    # mount that refuses the delete the file is renamed aside and the reverse
    # is a real "cleared"; when the rename is refused too the config is still
    # readable, and a reverse that claimed success there would tell the
    # customer their settings went back when they did not.
    wiped = wipe_skill_config_result(workspace_root, skill)
    if wiped["left_in_place"]:
        status = "left_in_place"
    elif wiped["cleared"]:
        status = "cleared"
    else:
        status = "already_clear"
    return {"status": status, "skill_name": skill, "config": None}


def _reverse_brief_residue(workspace_root, change, *, undone_by, source_skill):
    """CUSTOM2 — put the brief's free-text note file back exactly as it read
    before the migration trimmed it.

    The settings half of the same batch is reversed by
    `_reverse_commitment_preset` (the previous config, exactly). This half is
    the note file: `_hq/custom/<skill>.md`, restored BYTE FOR BYTE from the
    text the migration stamped on its own receipt, through the atomic writer
    every other write to that file uses. A batch with no `prev_custom_text` is
    malformed — the migration always stamps one, including the empty string
    for a workspace that had no file."""
    from atomic_write import atomic_write_text
    skill = change.get("custom_skill")
    if not skill:
        raise BrainUndoError(
            "brief_residue reversal needs custom_skill on the receipt "
            "(brief_settings.migrate_directives stamps it)")
    prev = change.get("prev_custom_text")
    if prev is None:
        raise BrainUndoError(
            "brief_residue reversal needs prev_custom_text on the receipt — "
            "without the previous text there is nothing to put back")
    path = Path(workspace_root) / "_hq" / "custom" / f"{skill}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, prev)
    return {"status": "restored", "custom_skill": skill,
            "bytes": len(prev.encode("utf-8"))}


def _reverse_schedule_fold(workspace_root, change, *, undone_by, source_skill):
    """FOLD1A — put a folded fire's live registration back on. `change` is
    the `schedule_config_changed` event's own data (via the `"changes"`
    allowlist key), carrying the exact rows `fold_scheduled_fires.apply_plan`
    disabled: `[{"task_id", "cron", "enabled": False}, ...]`.

    Puts the workspace's OWN `workspace.schedule_config` store back, and
    writes the matching audit event, through the SAME sanctioned writer the
    fold used (`schedule_config.apply_fold_state` — never a hand-rolled
    event, and never the audit writer alone).

    FIX ROUND 1, REVIEW_FOLD1A F-1 (the mirror half). This used to call
    `log_schedule_config_change` directly and so restored NOTHING, because
    nothing had been set: the fold's own write never reached the store
    either. Both halves now move the same container, so `fold_is_active`
    reads True after the fold and False again after this call.

    The restore is EXACT, not "set enabled true": `apply_fold_state` is
    handed the pre-fold override the fold stamped on its own event
    (`schedule_config.FOLD_PREV_OVERRIDES_KEY`), so an id that had NO
    override before the fold has its key removed again — the read falls
    back through `DEFAULT_SCHEDULES` exactly as it did — and an id that
    carried the customer's own cron/label gets that dict back verbatim.
    Writing a bare `enabled: true` would leave a key the customer never
    made, which is the orphan-override class SCHEDINH1 exists to stop. A
    batch with no stamp (an older seat) falls back to a plain `enabled:
    true`, which is still the honest restore for a fold that only ever
    moved that one field.

    This reverses the SUBSTRATE half only, same asymmetry as the disable
    itself (see `fold_scheduled_fires.py`'s module docstring): the surface
    that dispatches `undo` also needs to re-issue the seam's
    `schedule_backend.plan_update(task_id, enabled=True)` for each `task_id`
    this returns, mirroring the fold's own disable-then-record order in reverse
    (record first is fine on the way back — a substrate record that says
    "on" a moment before the live call lands is the safe direction, unlike
    the false-off case the forward path guards against)."""
    from schedule_config import (FOLD_PREV_OVERRIDES_KEY, FoldStateError,
                                 apply_fold_state)

    rows = change.get("changes") or []
    task_ids = [r.get("task_id") for r in rows if isinstance(r, dict) and r.get("task_id")]
    if not task_ids:
        raise BrainUndoError(
            "schedule_fold_disable reversal needs a non-empty 'changes' list "
            "on the stamp event's data (fold_scheduled_fires.apply_plan "
            "writes it via schedule_config.apply_fold_state — a batch row "
            "without one is malformed)")
    prev = change.get(FOLD_PREV_OVERRIDES_KEY)
    prev = prev if isinstance(prev, dict) else None
    try:
        written = apply_fold_state(
            workspace_root, task_ids, enabled=True, source_skill=source_skill,
            prev_overrides=prev,
            extra_data={"reason": "fold1a_undo", "undone_by": undone_by,
                        "restores_batch": change.get("brain_batch_id")})
    except FoldStateError as exc:
        raise BrainUndoError(f"could not restore the folded chats: {exc}") from exc
    # The STORE write is the act and it raises rather than half-succeeding;
    # the audit event is best-effort per RELIABILITY.md, so a restore whose
    # event could not be appended is still a restore and says so.
    return {"status": "restored", "task_ids": task_ids,
            "logged": written["logged"]}


# ---------------------------------------------------------------------------
# LEARN1 — the three learning reversers (SPEC_LEARN1 DD-2, amendments A2/A4).
#
# ARCHIVE, NEVER DELETE. Nothing in a customer workspace is removed by any of
# these: the file the automatic change wrote is MOVED into an `_archive/`
# folder beside its store and the prior bytes are written back over the live
# path. There is no `unlink` and no `rmtree` anywhere in this family, and the
# suite greps for both — an undo that destroys the thing it undid is not an
# undo.
# ---------------------------------------------------------------------------

def _archive_move(path, archive_dir, stem_suffix: str):
    """Move `path` under `archive_dir` with a timestamped name. Returns the
    archive path, or None when there was nothing there to move."""
    import shutil
    from pathlib import Path as _P

    path = _P(path)
    if not path.exists():
        return None
    archive_dir = _P(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{path.stem}_{stem_suffix}{path.suffix}"
    n = 1
    while target.exists():
        n += 1
        target = archive_dir / f"{path.stem}_{stem_suffix}-{n}{path.suffix}"
    shutil.move(str(path), str(target))
    return target


def _undo_stamp(undone_by: str) -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _reverse_voice_block_update(workspace_root, change, *, undone_by,
                                source_skill):
    # SPEC_LEARN1 A2 — put the previous voice block back. The snapshot on the
    # event is the WHOLE prior FILE (`previous_markdown`, header included),
    # not the block body, so the restore is byte-for-byte rather than the
    # content-equal-after-header-strip bar A2 settled for: writing the bytes
    # back directly beats re-deriving them through a writer that stamps a
    # fresh `Last refreshed:` line on every call.
    #
    # `had_override` is the other half. When the apply CREATED the override
    # where none existed, restoring "the previous file" means there must be
    # NO file afterwards — `load_voice_block_override` has to return None
    # again, because its presence is what email-writer's staleness read keys
    # on. The file is archived, never deleted.
    from pathlib import Path as _P

    skill = str(change.get("skill") or "")
    if not skill:
        raise BrainUndoError(
            "voice_block_update reversal needs the skill the block belongs to "
            "(learning_pass stamps it on the voice_block_updated event)")
    voice_dir = _P(workspace_root) / "_hq" / "voice"
    live = voice_dir / f"voice-block-{skill}.md"
    stamp = _undo_stamp(undone_by)
    archived = _archive_move(live, voice_dir / "_archive", stamp)
    previous = change.get("previous_markdown")
    had_override = bool(change.get("had_override"))
    restored = False
    if had_override and isinstance(previous, str) and previous:
        from atomic_write import atomic_write_text

        live.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(live, previous)
        restored = True
    from event_gate import append_event

    ev = append_event(_events_path(workspace_root), {
        "type": "voice_block_reverted",
        "source_skill": source_skill,
        "data": {"skill": skill, "restored": restored,
                 "had_override": had_override,
                 "archived_to": str(archived) if archived else None,
                 "undone_by": undone_by},
    }, holder="brain_undo")
    return {"status": "undone", "skill": skill, "restored": restored,
            "archived_to": str(archived) if archived else None, "event": ev}


def _reverse_voice_correction(workspace_root, change, *, undone_by,
                              source_skill):
    # LEARNFIX1 1.2 — take back a correction the person said in passing. The
    # apply (`voice_corrections.log_in_passing`) stamped the skill and the
    # correction's fingerprint on its own event, so this reverser never
    # re-derives which row it means from the text.
    #
    # IT IS AN APPEND, NOT A CUT. `retract_correction` writes a retraction row
    # and the corrected line stays on disk byte-for-byte; `load_corrections`
    # is what stops serving it. This is the whole of M's ruling R-20: the
    # 2026-09-16 chat had no reverser here, offered a menu, and then backed
    # the log up and rewrote it by hand.
    import voice_corrections as vc

    skill = str(change.get("skill") or "")
    fingerprint = str(change.get("correction_fingerprint") or "")
    if not skill or not fingerprint:
        raise BrainUndoError(
            "voice_correction reversal needs the skill and the correction's "
            "fingerprint (log_in_passing stamps both on the "
            "voice_correction_logged event)")
    retracted = vc.retract_correction(
        workspace_root, skill=skill, fingerprint=fingerprint,
        reason="undo", undone_by=undone_by)
    if not retracted:
        # Already taken back, or never landed. Either way nothing moved, and
        # `undo_batch` counts an `already_` apart instead of writing a marker
        # for a change that did not happen.
        return {"status": "already_undone", "skill": skill}
    from event_gate import append_event

    ev = append_event(_events_path(workspace_root), {
        "type": "voice_correction_retracted",
        "source_skill": source_skill,
        "data": {"skill": skill,
                 "correction_fingerprint": fingerprint,
                 "undone_by": undone_by},
    }, holder="brain_undo")
    return {"status": "undone", "skill": skill, "event": ev}


def _reverse_prep_section_weight(workspace_root, change, *, undone_by,
                                 source_skill):
    # SPEC_LEARN1 DD-2 — put each section weight back to the `from` value the
    # apply recorded on its own event. The reverser never re-derives what the
    # weight used to be; a re-derivation would read the value the apply just
    # wrote. Restores the `config` payload exactly (the file's `configured_at`
    # stamp moves, as it does on every config write).
    from prep_grading import set_section_weights

    changes = change.get("changes")
    if not isinstance(changes, list) or not changes:
        raise BrainUndoError(
            "prep_section_weight reversal needs the `changes` list the apply "
            "wrote (each row carries its own `from` value)")
    restore = []
    for row in changes:
        if not isinstance(row, dict):
            continue
        restore.append({"meeting_type": row.get("meeting_type"),
                        "section": row.get("section"),
                        "weight": row.get("from")})
    written = set_section_weights(workspace_root, restore,
                                  origin="learning_undo")
    from event_gate import append_event

    ev = append_event(_events_path(workspace_root), {
        "type": "prep_weights_reverted",
        "source_skill": source_skill,
        "data": {"changes": written["changes"], "undone_by": undone_by},
    }, holder="brain_undo")
    return {"status": "undone", "changes": written["changes"], "event": ev}


def _reverse_exemplar_promotion(workspace_root, change, *, undone_by,
                                source_skill):
    # SPEC_LEARN1 A4 — archive the learned exemplar and restore the text that
    # was there before. `previous_text` is null when the promotion created the
    # workspace exemplar: then the whole kind folder is archived so
    # `exemplars.get_exemplar` falls back to the shipped seed, which is what
    # "before" actually was. `exemplar_2.md` is left exactly where it is — it
    # is history, and the applied-fingerprint exclusion is what stops a second
    # promotion from rotating the true prior out of it.
    from pathlib import Path as _P

    kind = str(change.get("kind") or "")
    if not kind or "/" in kind or "\\" in kind:
        raise BrainUndoError(
            "exemplar_promotion reversal needs the exemplar kind "
            "(learning_pass stamps it on the exemplar_promoted event)")
    base = _P(workspace_root) / "_hq" / "exemplars"
    live = base / kind / "exemplar_1.md"
    stamp = _undo_stamp(undone_by)
    archived = _archive_move(live, base / "_archive" / kind, stamp)
    previous = change.get("previous_text")
    restored = False
    if isinstance(previous, str) and previous:
        from atomic_write import atomic_write_text

        live.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(live, previous)
        restored = True
    from event_gate import append_event

    ev = append_event(_events_path(workspace_root), {
        "type": "exemplar_reverted",
        "source_skill": source_skill,
        "data": {"kind": kind, "restored": restored,
                 "archived_to": str(archived) if archived else None,
                 "undone_by": undone_by},
    }, holder="brain_undo")
    return {"status": "undone", "kind": kind, "restored": restored,
            "archived_to": str(archived) if archived else None, "event": ev}


REVERSERS: dict[str, dict] = {
    # SPEC_LEARN1 — the three learning classes. Each lands in the SAME commit
    # as its `brain_proposals.AUTO_ALLOWED` row (the step-10 mandate): a class
    # cannot apply without a reverser, and a reverser without the class row is
    # dead code.
    "voice_block_update": {
        "reverse": _reverse_voice_block_update,
        "reverses_via": "voice_block_reverted",
        "description": "put back the way I wrote for you before I picked up "
                       "a repeated rewrite (the prior block, byte for byte; "
                       "the learned one is archived, never deleted)",
    },
    # LEARNFIX1 1.2 (M's ruling R-20) — a correction said in passing. Lands
    # in the SAME commit as the class `voice_corrections.log_in_passing`
    # writes, the step-10 mandate again.
    "voice_correction": {
        "reverse": _reverse_voice_correction,
        "reverses_via": "voice_correction_retracted",
        "description": "take back something you told me in passing about how "
                       "to write for you (the lesson is retracted by an "
                       "append; the line you said stays in the record)",
    },
    "prep_section_weight": {
        "reverse": _reverse_prep_section_weight,
        "reverses_via": "prep_weights_reverted",
        "description": "put a prep section back to the length it had before "
                       "it was quietened for a kind of meeting",
    },
    "exemplar_promotion": {
        "reverse": _reverse_exemplar_promotion,
        "reverses_via": "exemplar_reverted",
        "description": "put back the document shape that was the standard "
                       "before a repeated structural edit promoted a new one",
    },
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
    # EXIT1 FIX ROUND 2 (review R-4) — their reply putting a closed row back
    # is the product's act, so it is reversible like every other act it makes.
    "counter_evidence_reopen": {
        "reverse": _reverse_counter_evidence_reopen,
        "reverses_via": "commitment_resolved (the original closure written "
                        "back, word for word)",
        "description": "close again an item their reply had put back on "
                       "your plate",
    },
    # EXIT1 FIX ROUND 2 (review R-5) — the un-rest their reply triggers.
    "counter_evidence_unrest": {
        "reverse": _reverse_counter_evidence_unrest,
        "reverses_via": "commitment_updated (status_hint parked, with the "
                        "original rest's own reason and actor)",
        "description": "rest again an item their reply had put back on your "
                       "working list",
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
    # TTL1 (SPEC_FLOW1 Lane G, 2026-09-07) — the question-expiry engine's own
    # let-go. NEVER a candidate for `brain_proposals.AUTO_ALLOWED`: this class
    # reverses the product's own timeout, not a proposal it applied, and the
    # engine is not a detector. Registered in the SAME commit as the writer,
    # per the step-10 mandate — an automatic drop with no way back is exactly
    # what `brain_proposals.expire_stale` has been doing silently.
    # The class name's home is `question_ttl.PROPOSAL_EXPIRY_CHANGE_CLASS`;
    # it is spelled literally here so this module keeps importing nothing at
    # module scope, and the two spellings are pinned equal by the TTL1 suite.
    "brain_proposal_expiry": {
        "reverse": _reverse_brain_proposal_expiry,
        "reverses_via": "brain_proposal_reopened",
        "description": "put back a question the product let go when nobody "
                       "answered it (the expiry stays in history)",
    },
    # UXR1 D3 (M ruling 2026-07-21): the exact-unique-clean auto-link is
    # legal on the auto tier ONLY because this reverser exists (landed in
    # the same commit as the AUTO_ALLOWED entry). Undo reopens the mention
    # proposal AND re-opens a confirm-tier person_link row carrying the
    # original evidence — the reopened confirm row is also the re-auto
    # fence (propose(tier="auto") dedups against its open fingerprint).
    # IDENT1 rule 4 — the collision rule's own class. Saving a nickname had
    # been a one-way write since it shipped; this pair (the removal writer
    # and this reverser) is what licenses the rule to run on its own.
    "person_alias_dropped": {
        "reverse": _reverse_person_alias_dropped,
        "reverses_via": "add_person_alias (the same two writes the drop "
                        "reversed — the resolution mapping and the record's "
                        "own aliases array)",
        "description": "put back a nickname the collision rule dropped; "
                       "refused when the spelling now belongs to someone "
                       "else, which would recreate the ambiguity on top of "
                       "a later human decision",
    },
    "person_split": {
        "reverse": _reverse_person_split,
        "reverses_via": "person_updated (status -> archived, the shared "
                        "address cleared off the split record)",
        "description": "put a person back into the record they were split "
                        "out of; the address resolves where it did before",
    },
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
                        "engagement_updated events; plus the deal leg on "
                        "EVERY path (CLOSETRUTH1 3.2): ONE deal_won_reversed "
                        "marker naming the won event, the manufactured "
                        "thread archived or the real deal put back at its "
                        "prior stage with the thread at its prior status",
        "description": "put an org back to prospect after an automatic "
                       "promotion — the org_promoted receipt stays in "
                       "history and the org is never auto-promoted again; "
                       "the deal behind the win goes back with it",
    },
    # CLOSETRUTH1 3.2 (M's ruling 5, 2026-09-13) — the WIN'S OWN handle.
    # `deal_state.close_deal` stamps every won close with a batch id and
    # this class, so a win is reversible whether or not it promoted an org:
    # before this, the only handle was the promotion, and a win on an org
    # that was already a client had no way back at all. When one act writes
    # both rows they share a batch id, and `_reverse_deal_leg` is idempotent,
    # so one `undo` puts the deal back once.
    "deal_won": {
        "reverse": _reverse_deal_won,
        "reverses_via": "ONE deal_won_reversed marker naming the thread and "
                        "the won event's seq (which deal_state.won_reversals "
                        "folds out of list_closed_deals and the 90-day rate) "
                        "+ the deal put back at its prior stage with the "
                        "thread at its prior status, through the typed "
                        "thread writer; the deal_won event itself stays in "
                        "history",
        "description": "put back a deal you marked won — the deal returns to "
                       "the stage it was at, the project to the state it was "
                       "in, and the win stops counting in your win rate",
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
    # CUSTOM2 — the ten axes of the morning brief and the day-close
    # (`brief_settings.apply_settings` / `apply_sentence` / `apply_template` /
    # `reset_settings`). Same store and same reverser as the three below: the
    # act writes BOTH config stores under one batch, so the batch carries two
    # rows of this class and each puts its own store back exactly. A separate
    # class so a bare `undo` says the brief moved, not the question posture.
    "brief_settings": {
        "reverse": _reverse_commitment_preset,
        "reverses_via": "skill_reconfigured (the previous config written "
                        "back) or the key cleared when there was none",
        "description": "put your morning brief and your day-close back to "
                       "how they read before — the previous settings return "
                       "exactly, or the workspace goes back to having none",
    },
    # CUSTOM2 — the other half of a migration batch: the free-text note file
    # the migration trimmed once its lines became settings.
    "brief_residue": {
        "reverse": _reverse_brief_residue,
        "reverses_via": "the note file rewritten to its previous text",
        "description": "put your standing notes about the brief back exactly "
                       "as they were written",
    },
    # SPEC_FLOW1 — the flow switches (`turn off adding people
    # automatically`, and its siblings; `commitment_policy_pass.
    # set_flow_switch`). Same store and same reverser as the two above; a
    # separate class so a bare `undo` names the switch that moved.
    "flow_switch": {
        "reverse": _reverse_commitment_preset,
        "reverses_via": "skill_reconfigured (the previous config written "
                        "back) or the key cleared when there was none",
        "description": "put back one of the automatic-act settings — the "
                       "previous setting returns exactly",
    },
    # TTL1 fix round 1 (reviewer F-3) — the question-expiry switch, the same
    # writer and the SAME reverser as the two settings above (the previous
    # stored config comes back exactly, or the key is cleared). It has its
    # own class only so the bare `undo` listing names the right setting.
    "question_expiry_switch": {
        "reverse": _reverse_commitment_preset,
        "reverses_via": "skill_reconfigured (the previous config written "
                        "back) or the key cleared when there was none",
        "description": "put back whether questions nobody answers settle "
                       "to their default — the previous setting returns "
                       "exactly",
    },
    # FOLD1A (SPEC_FLOW1 Lane H) — the `fold1a_fire_fold_v1` update migration
    # that disables Waiting On and My Plate's live registration is legal as
    # an `auto_apply` ONLY because this reverser exists (same discipline as
    # every other row here). NEVER a candidate for `brain_proposals.
    # AUTO_ALLOWED` beyond the one release migration that already writes it —
    # this class exists to make THAT one act reversible, not to license a
    # second automatic writer.
    # NIGHT 11a fix round (REVIEW_NIGHT11A_MERGED_TREE N-2 / N-18): two
    # classes that writers already stamped and no row here knew, so the bare
    # `undo` listed them and `undo_batch` answered "no reverser registered"
    # under receipts that promised the way back. Both ride the config
    # reverser: the writers carry `skill_name` / `prev_config_present` /
    # `prev_config` on their own event.
    "profile_change": {
        "reverse": _reverse_commitment_preset,
        "reverses_via": "skill_reconfigured (the previous profile config "
                        "written back) or the store cleared when there was "
                        "none",
        "description": "put your profile page back to how it read before "
                       "the last thing you told it - the previous settings "
                       "return exactly",
    },
    "chat_persona_correction": {
        "reverse": _reverse_commitment_preset,
        "reverses_via": "skill_reconfigured (the previous chat persona "
                        "written back)",
        "description": "put how I answer you in chat back to the length it "
                       "was before you said it was too long",
    },
    "schedule_fold_disable": {
        "reverse": _reverse_schedule_fold,
        "reverses_via": "schedule_config_changed (enabled: true for every "
                        "folded id in the batch)",
        "description": "bring a folded chat back exactly as it was — "
                       "Waiting On and My Plate's live registration is "
                       "switched back on in one word",
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


# ---------------------------------------------------------------------------
# LEDGERFENCE1 — `undo` after a re-run of a meeting has ONE meaning
# ---------------------------------------------------------------------------
#
# ATTENDED_TEST_v5.29.0 B1.2. A meeting was reprocessed; the next thing said
# was `undo`. The right answer was "nothing to reverse" — the closer was off
# and the re-run had changed nothing. Instead the chat improvised: it dropped
# a pending row through the queue's drop door, marked a real decision
# `decision_superseded` with a "retracted, not superseded" note, hand-edited
# two session-notes files and regenerated the brief. None of it through
# `brain_undo`, so none of it carried a reversal marker and none of it was
# itself undoable. A real decision now files as superseded by nothing.
#
# The cause was not judgment; it was that `undo` after a re-run had no
# defined meaning and no batch to name. A re-run's writes are appended by
# skill PROSE through `event_gate.append_event`, so there was no run id to
# stamp and nothing for the bare-`undo` handler to resolve.
#
# THE BATCH ID IS THE RECEIPT. Every capture run already ends with one
# `meeting_processed` receipt, and its seq is a position in the ledger — the
# same anchor `sent_reconcile` has used since LB1. So the re-run's batch ref
# is `{"kind": "meeting_reprocess", "seq": <that receipt's seq>}`, resolving
# to every reversible change this run made to THIS meeting's rows: the events
# between the previous `meeting_processed` for the same meeting (exclusive)
# and this one (inclusive) that carry a `brain_change_class` and name the
# meeting. Nothing needed stamping, and it works on ledgers already written.
#
# What it deliberately does NOT reverse: the rows the run CAPTURED. A capture
# has no registered reverser (there is no additive event that un-observes a
# promise), and inventing one on the spot is exactly what B1.2 did. They are
# counted and named in the answer instead, and the honest sentence is the
# whole product: reverse the batch, or say nothing to reverse.

_MEETING_SOURCE_PREFIXES = ("granola", "fireflies", "otter", "zoom", "teams")

# The types one capture run writes (mirrors `meeting_capture.
# MEETING_WRITE_TYPES`, named here so this module does not import a 5,000-line
# capture module to read two constants).
_CAPTURE_TYPES = ("commitment", "decision", "person_proposal",
                  "person_update_proposal", "objective_review")


def _meeting_ref_keys(ref) -> set:
    """Normalized membership keys for a meeting reference — the live
    substrate carries both `granola:<id>` and bare `<id>` for the same
    meeting (`meeting_capture._norm_ref_keys`, same contract)."""
    keys = set()
    s = str(ref or "").strip().lower()
    if not s:
        return keys
    keys.add(s)
    if ":" in s:
        prefix, _, tail = s.partition(":")
        if prefix in _MEETING_SOURCE_PREFIXES and tail:
            keys.add(tail)
    return keys


def _event_meeting_keys(ev: dict) -> set:
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return (_meeting_ref_keys(data.get("source_ref"))
            | _meeting_ref_keys(data.get("meeting_id")))


KEYLESS_RECEIPT_LINE = ("Nothing to reverse — that run left no trace of what "
                        "it touched.")

# FIX ROUND 2 (review finding F-13). The other end of the same refusal: a
# receipt with NO receipt of any meeting below it. There is then no lower
# bound on the run's window at all, so "this run's own work" reaches back to
# the start of history — the reviewer's probe (i) reversed an old change on
# the same meeting alongside this run's, two instead of one. Bounding by the
# workspace's first event is not a bound, it IS all of history, so the answer
# is the same one a keyless receipt gets: refuse, and say so plainly. Exposure
# on the 2026-09-07 book: exactly one receipt of 423.
UNBOUNDED_WINDOW_LINE = ("Nothing to reverse — I can't tell where that run's "
                         "own work starts, so I won't guess at it.")

# FIX ROUND 3 (review finding F-20). A capture run and the receipt that ends
# it are ONE process: it reads a transcript, writes its rows, and stamps the
# receipt, all in one sitting. So a stamped change sitting a few seconds below
# a receipt is that run's own work — reversing it is exactly what `undo` after
# a re-run is FOR — while a change from an EARLIER sitting is separated from
# it by the gap between two sittings. An hour is far longer than any run takes
# and far shorter than the gap to the previous one; the reviewer's own probe
# had the two eight weeks apart. A row or a receipt with no readable time
# cannot be placed either way, and an unplaced row stays at risk: this bar is
# only ever allowed to make the refusal NARROWER on evidence.
_RUN_BURST_SECONDS = 3600


def _same_sitting(receipt: dict, ev: dict) -> bool:
    """Was `ev` written in the same sitting as the run `receipt` closes?

    True only when BOTH rows carry a readable time and they are within
    `_RUN_BURST_SECONDS` of each other. No time on either side means the
    question cannot be answered, and the answer is then no — this helper only
    ever narrows the F-13 refusal, and only on evidence."""
    from event_time import event_dt

    a, b = event_dt(receipt), event_dt(ev)
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= _RUN_BURST_SECONDS


def _reprocess_window(events: list[dict], audit_seq: int):
    """(previous receipt seq, this receipt, this meeting's ref keys) for the
    `meeting_processed` receipt at `audit_seq`. Raises when no such receipt
    exists — a ref that names nothing is never silently widened to
    everything.

    FIX ROUND 1 (review finding F-3). A receipt that names NO meeting — no
    `source_ref`, no `meeting_id` — used to leave `keys` empty, and both
    filters below were written as `if keys and ...`, so an empty key set
    turned them both off: the batch became every stamped change in the
    window, other meetings' rows included, and with no previous receipt the
    window was all of history. M's book carries five such receipts, and
    392 of 423 receipts have no previous receipt for their meeting. So a
    keyless receipt REFUSES here. The reasoning is the module's own, already
    written one line down for a seq-less receipt: never default to something
    that widens the batch. `undo_after_reprocess` turns this refusal into
    the plain sentence — nothing to reverse, that run left no trace of what
    it touched — which is the honest answer and, unlike a wide reversal, is
    not itself an act the customer has to undo.
    """
    from event_seq import event_seq

    receipt = None
    for ev in events:
        if ev.get("type") != "meeting_processed":
            continue
        if event_seq(ev) == audit_seq:
            receipt = ev
            break
    if receipt is None:
        raise BrainUndoError(
            f"no meeting_processed receipt at seq {audit_seq}")
    keys = _event_meeting_keys(receipt)
    if not keys:
        err = BrainUndoError(
            f"the meeting_processed receipt at seq {audit_seq} names no "
            f"meeting; refusing rather than widening the batch to the whole "
            f"window (LEDGERFENCE1 F-3)")
        err.refusal = "keyless"
        raise err
    prev_seq = 0
    prev_any = 0
    for ev in events:
        if ev.get("type") != "meeting_processed":
            continue
        seq = event_seq(ev)
        # A seq-less receipt cannot anchor a window (UNDOGUARD, the
        # `sent_reconcile` reasoning): skipped, never defaulted to 0, which
        # would widen the batch to everything before it.
        if seq is None or seq >= audit_seq:
            continue
        if seq > prev_any:
            prev_any = seq
        if not (_event_meeting_keys(ev) & keys):
            continue
        if seq > prev_seq:
            prev_seq = seq
    # F-3, the second half: the `prev_seq == 0` case, decided rather than
    # inherited. A meeting being processed for the FIRST time has no earlier
    # receipt of its own, so the low bound falls back to the newest receipt
    # of ANY meeting below this one — capture runs are serialized and each
    # ends with a receipt, so that is where this run's own writes begin.
    # (The review's other option — bounding by the receipt's own
    # `processed_at` — is wrong on the live data: `processed_at` is the
    # receipt's own stamp, a median 0.11 s before its `ts`, so it would empty
    # every first-run window rather than bound it.)
    #
    # FIX ROUND 2 (review finding F-13). When NEITHER bound exists the window
    # used to stay open on the low side — all of history, filtered only by
    # the meeting — and the reviewer's probe (i) reversed an old change on
    # that meeting alongside this run's, two instead of one. An unbounded
    # window is not a window, so this refuses, exactly as a keyless receipt
    # does and for the same reason: never default to something that widens
    # the batch. Bounding by the workspace's first event instead would not be
    # a bound at all — it IS the start of history.
    #
    # It refuses only when the open low side would actually SWEEP something:
    # a stamped, reversible change below this receipt naming this meeting and
    # written in an EARLIER sitting. A first-ever receipt with nothing
    # reversible under it has an empty window either way, and that case keeps
    # the ordinary, friendlier answer — "that re-run didn't change anything" —
    # instead of a refusal that would sound like a fault where there is none.
    #
    # FIX ROUND 3 (review finding F-20). The same friendlier answer is owed to
    # a first-ever re-run with ONLY ITS OWN work under it: reversing that run
    # is the whole point of the surface, and `at_risk` was counting the run's
    # own rows against it. `_RUN_BURST_SECONDS` is the one thing that tells
    # them apart — a run and its receipt are one sitting; a previous run's
    # work is a sitting away. Exposure either way is one receipt of 423 on the
    # 2026-09-07 book.
    if not (prev_seq or prev_any):
        at_risk = [
            ev for ev in events
            if (event_seq(ev) or 0) < audit_seq
            and isinstance(ev.get("data"), dict)
            and ev["data"].get("brain_change_class")
            and (_event_meeting_keys(ev) & keys)
            and not _same_sitting(receipt, ev)
        ]
        if at_risk:
            err = BrainUndoError(
                f"the meeting_processed receipt at seq {audit_seq} has no "
                f"receipt of any meeting below it, so this run's window has "
                f"no lower bound and {len(at_risk)} earlier change(s) on "
                f"this meeting would be swept in; refusing rather than "
                f"reaching back to the start of history (LEDGERFENCE1 F-13)")
            err.refusal = "unbounded"
            raise err
    return (prev_seq or prev_any), receipt, keys


def _changes_for_meeting_reprocess(events: list[dict],
                                   audit_seq: int) -> list[dict]:
    """Every REVERSIBLE change one capture run made to its own meeting's
    rows: an event inside the run's window that carries a
    `brain_change_class` and names the meeting.

    A capture (a new `commitment` / `decision` row) is not in here — see the
    module note above. `reprocess_report` counts those separately so the
    answer can say what the run did without pretending it can be un-said."""
    from event_seq import event_seq

    prev_seq, _receipt, keys = _reprocess_window(events, audit_seq)
    in_window = []
    for ev in events:
        seq = event_seq(ev)
        if seq is None or not (prev_seq < seq <= audit_seq):
            continue
        if not (_event_meeting_keys(ev) & keys):
            continue
        in_window.append(ev)
    return _changes_for_brain_batch(in_window, _ANY_BATCH)


PARENT_BATCH_KEY = "parent_batch_id"
UNDO_GROUP_KEY = "undo_group"

# Sentinel for `_changes_for_brain_batch`: take every stamped change in the
# list handed in, whatever batch it belongs to. The reprocess window has
# already narrowed the list to one run on one meeting, so the batch id is not
# the filter here — the window is.
_ANY_BATCH = object()


def _in_batch(data: dict, batch_id: str) -> bool:
    """DD-5 — a row belongs to `batch_id` when it IS that batch or sits in a
    group UNDER that run. A group id matches only its own rows.

    LEDGERFENCE1 — `_ANY_BATCH` means "the caller already narrowed the list"
    (the meeting-reprocess window), so membership is whatever is stamped."""
    if batch_id is _ANY_BATCH:
        return True
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
                    # TTL1 — the Living-Brain proposal id an expiry
                    # tombstone names. The `brain_proposal_expiry` reverser
                    # anchors on it (a brain proposal is keyed by its own
                    # `proposal_id`, not by a seq).
                    "proposal_id", "question_class",
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
                    # SPEC_FLOW1 (review R-4) — the put-back reverser's
                    # anchors: what the closure it undid actually said, so
                    # the row can be closed again word for word.
                    "prior_resolved_by", "prior_evidence", "prior_resolution",
                    "prior_source_ref", "prior_source_skill",
                    # ...and (fix round 3, review L-1) which route closed it.
                    "prior_exit_route",
                    # SPEC_FLOW1 (review R-5) — the un-rest reverser's
                    # anchors: what the rest it undid said, and who made it.
                    "prior_park_reason", "prior_parked_by",
                    # FOLD1A — the schedule_fold_disable reverser's anchor:
                    # `schedule_config.log_schedule_config_change`'s own row
                    # shape, `[{"task_id", "cron", "enabled"}, ...]` — the
                    # exact list this batch's `schedule_config_changed` event
                    # carried, so the reverser can flip every row's `enabled`
                    # back without re-deriving which ids it touched. FIX
                    # ROUND 1 (F-1): `fold_prev_overrides` rides beside it —
                    # the pre-fold override per id, so the restore puts the
                    # store back to what it WAS rather than to a bare
                    # `enabled: true` the customer never wrote.
                    "changes", "fold_prev_overrides",
                    # SPEC_LEARN1 — the three learning reversers' anchors:
                    # which skill's voice block, whether an override existed
                    # before (`had_override` rides the null-tolerant loop
                    # below, since False is a value here), which exemplar
                    # kind, and the prior text/markdown snapshots themselves.
                    "skill", "previous_markdown", "kind", "previous_text",
                    # LEARNFIX1 1.2 — the `voice_correction` reverser's only
                    # anchor besides `skill`: WHICH correction row to retract.
                    # A deliberate widening of this pinned key set, cited here
                    # and in `_reverse_voice_correction`, on the same grounds
                    # as `custom_skill` below: the row is identified by a
                    # content fingerprint, so no existing key can carry it and
                    # the retraction has nothing to aim at without this one.
                    "correction_fingerprint",
                    # CUSTOM2 — the brief-settings residue reverser's two
                    # anchors: WHICH `_hq/custom/<skill>.md` note file the
                    # migration trimmed, and what it said before the trim.
                    # A deliberate widening of this pinned key set, cited
                    # here and in `_reverse_brief_residue`: the residue is
                    # free TEXT, so `prev_config` (a dict) cannot carry it
                    # and the restore has nothing to write back without
                    # these two.
                    "custom_skill", "prev_custom_text",
                    "brain_batch_id"):
            if data.get(key) is not None:
                change[key] = data[key]
        if "prior_due" in data and "prior_due" not in change:
            change["prior_due"] = data["prior_due"]  # (b): null is a value here
        for key in ("prior_counterparty_id", "prior_counterparty_name",
                    # SPEC_LEARN1: `had_override: False` and
                    # `previous_markdown: null` / `previous_text: null` are
                    # each a real answer ("there was nothing there"), so they
                    # ride the `in data` loop rather than the truthy one.
                    "had_override"):
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


def batch_ref_label(batch_ref: dict) -> str:
    """`"<kind>:<seq or batch id>"` — the string an undo marker records as
    `data.batch_ref`. ONE composer (TTL1 fix round 1, reviewer F-10), so a
    reader asking "was this batch reversed" can take it apart the same way
    it was put together."""
    return (f"{(batch_ref or {}).get('kind')}:"
            f"{(batch_ref or {}).get('seq', (batch_ref or {}).get('batch_id'))}")


def batch_id_from_ref(label) -> str:
    """The batch id inside an undo marker's `batch_ref` label, or "".

    THE READER FOR `batch_ref_label` (fix round 1, reviewer F-10). Surfaces
    that asked "did an `undo` already reverse this run" were testing
    `batch_id in label` — a SUBSTRING test, which folds the wrong run away
    whenever one id happens to sit inside another marker's label. The label
    has exactly one shape and this takes it apart on that shape."""
    s = str(label or "")
    return s.split(":", 1)[1].strip() if ":" in s else s.strip()


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
    if kind == "meeting_reprocess":
        return _changes_for_meeting_reprocess(events, int(batch_ref["seq"]))
    raise BrainUndoError(f"unknown batch kind: {kind!r}")


def reprocess_batch_ref(workspace_root, source_ref=None,
                        events: Optional[list] = None) -> Optional[dict]:
    """The batch ref for the NEWEST capture run — of one meeting when
    `source_ref` names it, of any meeting otherwise. `None` when this
    workspace has never processed a meeting.

    Read-only. This is what a re-run's own chat passes to
    `undo_after_reprocess`; a fresh chat reaches the same run through
    `recent_auto_batches`."""
    from event_seq import event_seq

    evs = _load_events(workspace_root) if events is None else events
    want = _meeting_ref_keys(source_ref) if source_ref else set()
    best = None
    for ev in evs:
        if ev.get("type") != "meeting_processed":
            continue
        seq = event_seq(ev)
        if seq is None:
            continue
        if want and not (_event_meeting_keys(ev) & want):
            continue
        if best is None or seq > best:
            best = seq
    if best is None:
        return None
    return {"kind": "meeting_reprocess", "seq": best}


def reprocess_report(workspace_root, batch_ref: dict) -> dict:
    """What one capture run did, read-only: `{n_reversible, n_captured,
    changes}`. `n_captured` counts the rows the run OPENED (a capture has no
    reverser — see the module note) so the answer can name them instead of
    improvising a way to un-say them."""
    from event_seq import event_seq

    events = _load_events(workspace_root)
    audit_seq = int(batch_ref["seq"])
    prev_seq, _receipt, keys = _reprocess_window(events, audit_seq)
    changes = _changes_for_meeting_reprocess(events, audit_seq)
    n_captured = 0
    for ev in events:
        seq = event_seq(ev)
        if seq is None or not (prev_seq < seq <= audit_seq):
            continue
        if ev.get("type") not in _CAPTURE_TYPES:
            continue
        if not (_event_meeting_keys(ev) & keys):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("brain_change_class"):
            continue  # already counted as a reversible change
        n_captured += 1
    return {"n_reversible": len(changes), "n_captured": n_captured,
            "changes": changes}


def reprocess_undo_line(result: dict) -> str:
    """The ONE sentence a chat says after `undo` on a re-run. Composed here,
    not in prose, so every surface says the same thing and none of them can
    improvise a drop, a supersede, or a hand-edited file when the honest
    answer is that there is nothing to reverse."""
    n = int((result or {}).get("n_undone") or 0)
    captured = int((result or {}).get("n_captured") or 0)
    errors = int((result or {}).get("n_errors") or 0)
    tail = ("" if not captured else
            f" It did add {captured} new item{'' if captured == 1 else 's'} to "
            f"your list; those stay until you act on them.")
    if n:
        head = (f"Put back {n} thing{'' if n == 1 else 's'} that re-run "
                f"changed.")
        if errors:
            head += (f" {errors} could not be put back — say so to whoever "
                     f"set up your Command Room.")
        return head + tail
    if errors:
        return ("I could not put that re-run back — nothing was changed by "
                "this attempt." + tail)
    return "Nothing to reverse — that re-run didn't change anything." + tail


def undo_after_reprocess(workspace_root, source_ref=None, *,
                         undone_by: str, source_skill: str,
                         batch_ref: Optional[dict] = None) -> dict:
    """`undo` after a meeting re-run, with exactly one meaning (LEDGERFENCE1;
    ATTENDED_TEST_v5.29.0 B1.2): reverse THAT run's own batch, or say nothing
    to reverse. Never a drop, never a supersede, never a file edit.

    Returns the `undo_batch` shape plus `n_captured`, `nothing_to_reverse`
    and `line` — relay `line` verbatim.

    A run whose receipt names no meeting REFUSES (fix round 1, F-3): the
    answer is that there is nothing to reverse because the run left no trace
    of what it touched. Reversing "whatever is in the window" would be a
    wrong act the customer never asked for and would then have to undo
    themselves — the one case where M's standing rule (a reversible wrong
    action beats a question) does not apply, because the wrong action IS the
    reversal."""
    ref = batch_ref or reprocess_batch_ref(workspace_root, source_ref)
    if ref is None:
        return {"status": "empty", "n_undone": 0, "n_errors": 0,
                "n_already": 0, "results": [], "n_captured": 0,
                "nothing_to_reverse": True, "batch_ref": None,
                "line": "Nothing to reverse — that re-run didn't change "
                        "anything."}
    try:
        report = reprocess_report(workspace_root, ref)
    except BrainUndoError as exc:
        # Two refusals, two sentences (fix round 2, F-13): a receipt that
        # names no meeting, and a receipt with nothing below it to bound the
        # run's own window. Both answer "nothing to reverse"; neither
        # pretends to know what the run touched.
        unbounded = getattr(exc, "refusal", "keyless") == "unbounded"
        return {"status": "empty", "n_undone": 0, "n_errors": 0,
                "n_already": 0, "results": [], "n_captured": 0,
                "nothing_to_reverse": True, "batch_ref": ref,
                "refused": ("run's window has no lower bound" if unbounded
                            else "receipt names no meeting"),
                "line": (UNBOUNDED_WINDOW_LINE if unbounded
                         else KEYLESS_RECEIPT_LINE)}
    if not report["n_reversible"]:
        out = {"status": "empty", "n_undone": 0, "n_errors": 0,
               "n_already": 0, "results": [],
               "n_captured": report["n_captured"],
               "nothing_to_reverse": True, "batch_ref": ref}
        out["line"] = reprocess_undo_line(out)
        return out
    out = undo_batch(workspace_root, ref, undone_by=undone_by,
                     source_skill=source_skill)
    out["n_captured"] = report["n_captured"]
    out["nothing_to_reverse"] = out.get("n_undone", 0) == 0
    out["batch_ref"] = ref
    out["line"] = reprocess_undo_line(out)
    return out


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


_USER_MARK_RE = re.compile(r"<!--/?cr:ut-->")


def _title_carries_path_or_id(title: str) -> bool:
    """The second fence on a receipt title (CLOSETRUTH1 3.4, kept at the
    trial merge): a data-file name, an `_hq/` path or a wire id in a title
    never prints - the vague line stands in. Vocabulary words do not trip
    this; the gate blanks those as the customer's own."""
    try:
        from surface_leak_patterns import (substrate_path_patterns,
                                           carries_surface_id)
    except Exception:  # pragma: no cover
        return False
    if carries_surface_id(title):
        return True
    for _name, pat in substrate_path_patterns():
        if re.search(pat, title, re.IGNORECASE):
            return True
    return False


def phrase_door_lines(resolution: dict) -> List[str]:
    """The sentences the chat says for a `resolve_undo_phrase` answer that is
    not a single batch - composed WITHOUT the customer's typed words (merged-
    tree review F-6: a typed phrase can carry an id-shaped token, and a
    sentence quoting it would be refused by the gate with nothing to rewrite).
    `no_match` -> one line + the listing; `ambiguous` -> the candidates."""
    status = str((resolution or {}).get("status") or "")
    if status == "ambiguous":
        return (["More than one recent change fits that — which one?"]
                + undo_listing_lines(list((resolution or {}).get("candidates") or [])))
    if status == "no_match":
        return (["Nothing on the recent list fits those words — here it is again:"]
                + undo_listing_lines(list((resolution or {}).get("candidates")
                                          or (resolution or {}).get("batches") or [])))
    return []


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


#: CLOSETRUTH1 3.4 — what an undo puts back, in the words a person used for
#: it. Keyed by change class; the door phrase comes from `_CLASS_PHRASES`, so
#: there is one vocabulary and no class name can reach a surface through here.
_UNDO_PUT_BACK = {
    "commitment_close": "back on your list",
    "commitment_park": "back on your list",
    "commitment_merge": "split apart again",
    "commitment_close_from_observed": "back where it was",
    "org_promotion": "back to a prospect",
    "deal_won": "open again",
}


#: CLOSETRUTH1 fix round 1 (review F-2) — the change classes whose row is NOT
#: a commitment, and the field on the change record that DOES point at
#: something nameable. `_row_titles` is keyed on `commitment_id` / `id`, and a
#: promotion or a win carries neither, so both legs of the ONE act 3.2 exists
#: for fell to "an item with no name on file" — twice in the same receipt,
#: which is worse than saying nothing. The value is an entity id and is never
#: printed: it is looked up in the name index and the NAME is what is
#: composed, through the same `safe_name` and the same gate as every other
#: line here.
_UNDO_NAMED_BY = {"org_promotion": "org_id", "deal_won": "deal_thread_id"}

#: The deal stages `thread_writer` allows, in the words a person reads them
#: in. The stored spelling is snake_case machine text; a receipt says
#: "proposal sent", never `proposal_sent`.
_STAGE_WORDS = {"lead": "a lead", "qualified": "qualified",
                "proposal_sent": "proposal sent",
                "negotiating": "negotiating"}


#: CLOSETRUTH1 fix round 1 (review at-merge note 4) — the id spellings this
#: composer refuses ON ITS OWN, before `safe_name` is asked.
#:
#: WHY THE COMPOSER CARRIES ITS OWN DOOR. `narration_names.safe_name` defers
#: to `surface_leak_patterns.carries_surface_id`, whose commitment pattern is
#: `cmt_` + SIX or more characters — the hex spelling. A row titled `cmt_016`
#: or `seq_86` has a short NUMERIC tail, passes that pattern, passes the chat
#: gate, and reaches a receipt line verbatim. Widening the shared patterns is
#: LEAK3's, and it is the right fix; this is the local one, so that the fix
#: landing there is a second line of defence and not the first.
#:
#: Deliberately narrow: an underscore-joined plugin id PREFIX followed by a
#: tail of digits or word characters, and THE WHOLE TITLE must be that and
#: nothing else.
#:
#: Fix round 2 (review F-12). Round 1 applied this with `.search()`, so the
#: nineteen prefixes — `project`, `org`, `deal`, `meeting`, `thread`, `batch`
#: among them — caught ordinary prose that merely CONTAINS such a token:
#: "Finish project_alpha spec" and "Draft the deal_terms memo" both lost their
#: names and printed as "an item with no name on file". The docstring below
#: always said "a title that IS an id"; the match is anchored now so the code
#: says it too. `cmt_016` and `seq_86` — the short numeric spellings the
#: shared patterns still pass — are refused exactly as before.
_ID_SHAPED_TITLE_RE = re.compile(
    r"(?:cmt|seq|commitment|project|person|org|thread|deal"
    r"|batch|bp|prop|proposal|eng|engagement|meeting|msg|evt|event|pcand)"
    r"_[0-9A-Za-z]+",
    re.IGNORECASE)


def _never_an_id(candidate):
    """`candidate` unless it carries an id shape; None when it does.

    Returning None rather than a scrubbed string is deliberate: a title that
    IS an id has no name in it to salvage, and `safe_name`'s honest fallback
    ("an item with no name on file") is a true sentence. Half-printing an id
    is the defect, not the cure.

    `fullmatch`, not `search` (fix round 2, review F-12): the test is whether
    the WHOLE title is an id, which is what the sentence above claims. A prose
    title that happens to contain an underscore-joined word keeps its name."""
    text = str(candidate or "").strip()
    if not text:
        return None
    return None if _ID_SHAPED_TITLE_RE.fullmatch(text) else text


def _row_titles(events: List[dict]) -> dict:
    """{commitment id -> the newest title written for it}. Titles are what a
    receipt names a row by; ids are what it must never name one by."""
    out: dict = {}
    for ev in events or []:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = d.get("commitment_id") or d.get("id")
        title = d.get("title")
        if cid and isinstance(title, str) and title.strip():
            out[str(cid)] = title.strip()
    return out


def undo_receipt_lines(workspace_root, undo_result: dict, *,
                       batch: Optional[dict] = None) -> List[str]:
    """THE composer for what an undo says back. Plain sentences, nothing else.

    CLOSETRUTH1 3.4 (leaks 8 and 11). There was no composer: every surface
    that reported an undo wrote its own sentence, and on 2026-09-13 those
    sentences carried a raw row id, a project id, a batch id, the names of
    two functions, the names of two data files and a backups folder — all of
    it on the customer's screen, twice in one day.

    Every line here is built from two things only: the NAME of the thing
    that moved and the DOOR's phrase (`_CLASS_PHRASES`, the same vocabulary
    the listing uses). Nothing else is admitted — not the batch id, not the
    change class, not a seq.

    The name comes from the row's own TITLE where the row is a commitment,
    and from the name index where it is not: a promotion names an org and a
    win names a deal thread, and `_UNDO_NAMED_BY` says which id on the
    change record points at which. Either way the candidate goes through
    `_never_an_id` (this composer's own door, for the short numeric id
    shapes the shared patterns still allow) and then through
    `narration_names.safe_name`, which refuses a candidate that is an id in
    disguise and says "no name on file" instead. A win also says which
    stage its deal went back to, in the words of `_STAGE_WORDS`.

    Then every composed line is put through `validate_chat_output` as machine
    text before it is returned. A line that cannot pass the gate is not
    printed with a warning; it is replaced by the count sentence, which is
    always true and never carries a name. A receipt that leaks is worse than
    a receipt that is vague.
    """
    from chat_output_renderer import validate_chat_output
    from narration_names import safe_name

    results = [r for r in (undo_result or {}).get("results") or []]
    n_ok = sum(1 for r in results if r.get("status") == "undone")
    n_err = int((undo_result or {}).get("n_errors") or 0)
    titles = _row_titles(_load_events(workspace_root)) if results else {}

    def _gated(line: str, fallback: str, *, raw_title: str = "") -> str:
        # Merged-tree review F-2 (night 11b): the row title inside `line` is
        # the customer's own words, marked through the renderer's ONE emitter
        # (`mark_field`, the widget's `name` rule) so the VOCABULARY half of
        # the scan blanks it - "past-meetings", "12/15 checks", a lane code
        # - the same split the plate and the widget already use. The second
        # fence this lane pinned stays: a title naming a data file, an `_hq/`
        # path or an id falls back to the vague line rather than print it.
        try:
            from chat_output_renderer import blank_user_text
            if raw_title and _title_carries_path_or_id(raw_title):
                return fallback
            validate_chat_output(line, vocab_text=blank_user_text(line))
        except Exception:  # noqa: BLE001 — LeakDetectedError or anything else
            return fallback
        return _USER_MARK_RE.sub("", line)

    lines: List[str] = []
    label = (batch or {}).get("label")
    head_n = f"{n_ok} thing{'' if n_ok == 1 else 's'}"
    head = (f"Put {head_n} back — {label}." if label
            else f"Put {head_n} back.")
    lines.append(_gated(head, f"Put {head_n} back."))

    name_idx = None
    # Which stage each deal went back to. It is reported by whichever leg
    # actually ran the restore — a `mark won` batch carries BOTH handles and
    # the second one finds the deal already open — so it is collected across
    # the whole run rather than read off the row's own result.
    stage_by_thread: dict = {}
    for r in results:
        rd = r.get("result") if isinstance(r.get("result"), dict) else {}
        tid = rd.get("deal_thread_id")
        if tid and rd.get("deal_stage"):
            stage_by_thread[str(tid)] = str(rd["deal_stage"])
    for r in results:
        if r.get("status") != "undone":
            continue
        change = r.get("change") if isinstance(r.get("change"), dict) else {}
        cls = str(change.get("change_class") or "")
        cid = change.get("commitment_id") or change.get("id")
        raw = titles.get(str(cid)) if cid else None
        if not raw:
            # F-2: not every reversible row is a commitment. A promotion
            # names an org and a win names a deal thread; both are on the
            # change record already.
            key = _UNDO_NAMED_BY.get(cls)
            ent_id = change.get(key) if key else None
            if ent_id:
                if name_idx is None:
                    from narration_names import name_index
                    name_idx = name_index(workspace_root)
                raw = name_idx.get(str(ent_id))
        title = safe_name(_never_an_id(raw),
                          fallback="an item with no name on file")
        where = _UNDO_PUT_BACK.get(cls, "back the way it was")
        tail = ""
        if cls == "deal_won":
            stage = stage_by_thread.get(
                str(change.get("deal_thread_id") or ""))
            if stage in _STAGE_WORDS:
                tail = f", at {_STAGE_WORDS[stage]}"
        from chat_output_renderer import mark_field as _mark_field
        marked = _mark_field({"name": title}, "name", title, surface="widget")
        lines.append(_gated(f"  {marked} — {where}{tail}.",
                            f"  one item — {where}.", raw_title=title))

    if n_err:
        lines.append(f"{n_err} could not go back — say `undo` again to retry.")
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


#: CLOSETRUTH1 3.3 — the DOORS a person names out loud, keyed by the batch-id
#: prefix each door mints. A phrase like "the three items you let go" names a
#: door, not a change class: the silence door and an own-word close both write
#: `commitment_close`, and only the prefix tells them apart. Words only —
#: nothing here is ever printed, it is read off what the person typed.
_DOOR_WORDS: dict = {
    "sil_": {"let", "go", "letgo", "silence", "silent", "untouched",
             "touched", "quiet", "dropped", "resting", "rested"},
    # CLOSETRUTH1 fix round 1 (review F-3): `my`, `own` and `word` are OUT.
    # They are function words, not door words — "undo my last change" names
    # no door, yet it scored 2 here and resolved to an own-word batch, which
    # is a WRITE made on a guess. The same function already strips `my` from
    # the label-overlap signal below for exactly this reason; the door signal
    # was the half that never got the treatment. What is left carries the
    # meaning on its own: a person naming this door says they said it, told
    # it, used their own WORDS (plural), or did it themselves.
    "own_": {"said", "told", "words", "myself"},
    "prf_": {"proof", "proved", "evidence", "fact", "facts"},
    "cal_": {"calendar", "meeting", "meetings", "met", "event"},
    "cru_": {"sent", "mail", "email", "send", "message"},
    "swb_": {"backlog", "sweep", "cleanup", "cleared"},
    "qex_": {"question", "questions", "unanswered", "expired"},
    "prm_": {"won", "win", "client", "prospect", "promoted", "promotion"},
    "bkf_": {"filed", "binding", "project", "backfill"},
}

#: The counting words a person uses before they reach for digits.
_NUMBER_WORDS: dict = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "a": 1, "an": 1, "both": 2,
}

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")


def _phrase_tokens(text) -> list:
    import re as _re

    return [t for t in _re.split(r"[^a-z0-9]+", str(text or "").lower()) if t]


def _phrase_count(tokens: list):
    """The number of items the person named, or None. `undo` / `undo all`
    name no count, and a count that is not there must never filter."""
    for t in tokens:
        if t.isdigit():
            n = int(t)
            if 1 <= n <= 999:
                return n
    for i, t in enumerate(tokens):
        if t in _NUMBER_WORDS:
            # "a"/"an" only count when they sit in front of a noun, never as
            # the article in "undo a batch" — so require a following token.
            if t in ("a", "an") and i == len(tokens) - 1:
                continue
            return _NUMBER_WORDS[t]
    return None


def _phrase_day(tokens: list, now):
    """The day the person named as a date, or None. Understands today,
    yesterday, last night, this morning and a bare weekday inside the
    listing window."""
    import datetime as _dt

    if now is None:
        return None
    today = now.date()
    joined = " ".join(tokens)
    if "yesterday" in tokens or "last night" in joined:
        return today - _dt.timedelta(days=1)
    if "today" in tokens or "this morning" in joined or "tonight" in tokens:
        return today
    for t in tokens:
        if t in _WEEKDAYS:
            want = _WEEKDAYS.index(t)
            for back in range(0, 8):
                d = today - _dt.timedelta(days=back)
                if d.weekday() == want:
                    return d
    return None


def batch_ref_for_phrase(batches: List[dict], text, *, now_iso=None) -> dict:
    """CLOSETRUTH1 3.3 — resolve a DESCRIPTION to one listed batch.

    "undo the three items you let go" is what a person actually types; before
    this the only accepted tokens were `all`, an ordinal, and `na`
    (`batch_ref_for_ordinal`), so the 2026-09-13 walk answered a plain
    description with a three-way menu and a filename.

    Resolution is by the three things the person can see on the listing: the
    DOOR the batch came out of (its batch-id prefix), the COUNT of rows it
    changed, and the DAY it happened. A count or a day that is not in the
    phrase never filters. Scoring is deliberately dull — every signal is one
    point, and a tie is a tie.

    Returns, always a dict, never a guess:
      {"status": "resolved",  "batch_ref": {...}, "batch": {...}}
      {"status": "ambiguous", "candidates": [batch, ...]}   -> list them
      {"status": "no_match",  "candidates": []}             -> say so

    NEVER returns a ref on an ambiguous phrase. Undo is a write; two
    plausible batches is a question, not a coin toss.
    """
    from event_time import parse_ts

    tokens = _phrase_tokens(text)
    if not tokens:
        return {"status": "no_match", "candidates": []}
    now = parse_ts(now_iso) if now_iso else _now_utc()
    want_n = _phrase_count(tokens)
    want_day = _phrase_day(tokens, now)
    token_set = set(tokens)

    flat: list = []
    for b in batches or []:
        flat.append(b)
        for kid in (b.get("children") or []):
            flat.append(kid)

    scored: list = []
    for b in flat:
        bid = str((b.get("batch_ref") or {}).get("batch_id") or "")
        label_tokens = set(_phrase_tokens(b.get("label")))
        score = 0
        door_hit = False
        for prefix, words in _DOOR_WORDS.items():
            if bid.startswith(prefix) and (token_set & words):
                score += 2
                door_hit = True
                break
        overlap = token_set & label_tokens
        # "items" / "things" / "the" carry no information about which batch.
        overlap -= {"the", "a", "an", "items", "item", "things", "thing",
                    "undo", "put", "back", "that", "those", "you", "i", "my"}
        if overlap:
            score += 1
        if not (door_hit or overlap):
            continue
        if want_n is not None:
            if int(b.get("n_changes") or 0) != want_n:
                continue
            score += 1
        if want_day is not None:
            ts = parse_ts(b.get("ts"))
            if ts is None or ts.date() != want_day:
                continue
            score += 1
        scored.append((score, b))

    if not scored:
        return {"status": "no_match", "candidates": []}
    top = max(s for s, _ in scored)
    winners = [b for s, b in scored if s == top]
    if len(winners) == 1:
        return {"status": "resolved", "batch_ref": winners[0]["batch_ref"],
                "batch": winners[0]}
    return {"status": "ambiguous", "candidates": winners}


#: CLOSETRUTH1 fix round 1 (review F-5) — how far back the phrase door looks
#: when the RECENT listing holds nothing that fits the words. 3.3's own pass
#: line is "a batch in a rotated shard → found", and a rotated shard is by
#: definition older than the 7-day listing window, so the phrase door could
#: never reach one even though the ledger read behind it has been
#: shard-transparent all along. Reversal legality never expires — every
#: reverser is additive — so widening the SEARCH costs nothing; the 7 days
#: stay the default because they are what the listing shows, and a batch the
#: person can see is the one they usually mean.
PHRASE_WIDER_DAYS = 3650


def resolve_undo_phrase(workspace_root, text, *,
                        batches: Optional[List[dict]] = None,
                        now_iso=None,
                        wider_days: int = PHRASE_WIDER_DAYS) -> dict:
    """THE phrase door as a surface calls it: the recent listing first, the
    whole book second.

    `batch_ref_for_phrase` is pure — it searches the list it is handed and
    nothing else — and the shipped caller hands it `recent_auto_batches`,
    whose window is 7 days. So a batch sitting in a rotated shard answered
    `no_match` to its own description, which is the one thing 3.3 promised
    would work and the one thing the skill prose claimed it did.

    Two passes, and only ever two: the listing the person can see, then, ONLY
    when that knows nothing, the same resolver over the whole book. An
    `ambiguous` first pass is NOT widened — more candidates never turn a
    question into an answer, and the person is already being asked.

    Returns `batch_ref_for_phrase`'s dict with one key added: `widened`,
    True when the answer came from the second pass, so a surface can say
    "that one is from a while back" instead of pretending it was on the list.
    """
    listed = (batches if batches is not None
              else recent_auto_batches(workspace_root, now_iso=now_iso))
    hit = batch_ref_for_phrase(listed, text, now_iso=now_iso)
    hit["widened"] = False
    if hit["status"] != "no_match":
        return hit
    wide = recent_auto_batches(workspace_root, days=wider_days,
                               now_iso=now_iso)
    if len(wide) <= len(listed):
        return hit
    second = batch_ref_for_phrase(wide, text, now_iso=now_iso)
    second["widened"] = second["status"] != "no_match"
    return second


# Change class → the phrase a human recognizes. Never the class name itself:
# the list is read by the person deciding whether to reverse it, and
# "commitment_merge ×1" is not a thing anyone said or saw happen.
_CLASS_PHRASES = {
    # SPEC_LEARN1 — what the person actually saw happen. Never a class name,
    # never a skill name, never a store.
    "voice_block_update": "changed how I write for you",
    # LEARNFIX1 1.2 — what the person saw happen: they said something in
    # passing and the answers got shorter. Never "logged a correction".
    "voice_correction": "took what you said about how to write for you",
    "prep_section_weight": "shortened a prep section that keeps coming up empty",
    "exemplar_promotion": "took a new document shape as the standard",
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
    # TTL1 — never a class name on a customer surface. "let go a
    # question nobody answered" is the thing that actually happened.
    "brain_proposal_expiry": "let go a question nobody answered",
    "chat_dismissal": "muted a row",
    "prep_brief_thread_backfill": "bound a past brief to a thread",
    "thread_split": "moved an event back after a thread split",
    # BACKFILL2 — the binding-review widget's `bkf_` batch, listed by a bare
    # `undo` in a fresh chat. Never written by an auto detector.
    "binding_backfill": "filed a past record under its project",
    # DEALNAG1 — what the person actually saw happen in the CHANGED feed.
    "org_promotion": "promoted a prospect to client",
    # CLOSETRUTH1 3.2 — the win itself, in the words the person used.
    "deal_won": "marked a deal won",
    # QUIET1 — the preset stamp (manifest auto_apply, `ask me more/less`).
    "commitment_preset": "set how often it asks you",
    # CUT-A — the closing-on-evidence switch (`turn on/off closing on evidence`).
    "commitment_transcript_closes": "set whether promises close on meeting evidence",
    # SPEC_FLOW1 — the flow switches.
    "flow_switch": "changed one of the automatic-act settings",
    # CUSTOM2 — the shape of the morning brief and the day-close, and the
    # standing notes the migration folded into it.
    "brief_settings": "changed the shape of your morning brief",
    "brief_residue": "moved your standing notes about the brief into settings",
    # NIGHT 11a fix round - the profile page's own doors and the in-passing
    # "too long" step.
    "profile_change": "changed something on your profile page",
    "chat_persona_correction": "made my chat answers shorter because you said so",
    # IDENT1 — the identity acts the product makes on its own. The
    # `person_split` phrase is here BECAUSE the path behind it is now built
    # and has a reverser; G57 is what keeps that true, rather than a memory.
    "person_alias_dropped": "dropped a nickname that pointed at two people",
    "person_split": "gave a second person their own record",
    # SPEC_FLOW1 — the put-back (review R-4) and the un-rest (review R-5).
    "counter_evidence_reopen": "put an item back when they came back about it",
    "counter_evidence_unrest": "took an item off the resting list when they "
                               "came back about it",
    # TTL1 fix round 1 (reviewer F-3) — the switch, in the customer's words.
    "question_expiry_switch": "set whether old questions answer themselves",
    # FOLD1A — the update's own fold of Waiting On and My Plate.
    "schedule_fold_disable": "folded two scheduled chats into the morning brief",
}


#: CLOSETRUTH1 3.2 — class sets that are the LEGS OF ONE ACT, not a run of
#: several. `mark [org] won` writes a `deal_won` row and an `org_promoted`
#: row under one batch id; listing it as "marked a deal won + promoted a
#: prospect to client (×2)" tells the person two things happened and invites
#: them to think they must reverse two. One act, one phrase, no multiplier.
_COMPOSED_ACTS: dict = {
    frozenset({"deal_won", "org_promotion"}):
        "marked a deal won and made them a client",
    # LEARNFIX1 1.2 — "too long" is ONE thing the person said. It writes the
    # lesson and steps the answers shorter under one batch id; listing it as
    # two phrases with a (×2) tells them two things happened and invites the
    # three-way fork this lane exists to remove.
    frozenset({"chat_persona_correction", "voice_correction"}):
        "made my answers shorter because you said so",
}


def _batch_label(classes: list, n: int) -> str:
    composed = _COMPOSED_ACTS.get(frozenset(classes))
    if composed:
        return composed
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
    actor=None,
    user_confirmed: bool = False,
) -> dict:
    """Reverse everything one narrated batch changed (D5 — the commitment-
    triage batch-undo pattern, generalized). Per change: run the class's
    registered reverser (its additive reversing event), then append ONE
    `brain_change_undone` marker. Per-item failures are collected; the batch
    never aborts. Returns {status, n_undone, n_errors, results}.

    ATTRIB2 (2026-09-07) — WHO reversed it. The actor is resolved ONCE here
    and handed to every reverser, so the whole gesture reads one way:
    `commitment_reopened`, `deal_won_reversed` and the `brain_change_undone`
    marker all name the same actor. A reversal a fire or a chat performs on
    its OWN judgment is the machine's — pass `actor=event_types.MACHINE`, and
    a background `source_skill` resolves to the rail's own name whatever the
    call site passed. A person's typed `undo` on their own surface is
    untouched. THE REGRESSION: seqs 15503-15506, where the maintenance fire's
    two self-corrections went down as `person_001` and the brief reported them
    as M's.

    `user_confirmed=True` — THE CUSTOMER'S ESCAPE (fix round 1, reviewer
    F-1/F-2), the same one `close_commitment` has always given the closure
    family. A person who TYPED or TAPPED `undo` keeps their person id and
    their credit whatever surface carried the gesture, including a surface
    that also runs unattended work. Pass it from every dispatch a human just
    made; it is honoured only when `undone_by` is a real person id, so it can
    never launder a fire's own reversal into the customer's.
    """
    from event_gate import append_event
    from event_types import ACTOR_KIND_KEY as _AKK
    from event_types import resolve_actor as _resolve_actor

    undone_by, _actor_kind = _resolve_actor(
        undone_by if actor is None else actor, source_skill=source_skill,
        user_confirmed=user_confirmed)

    changes = resolve_batch(workspace_root, batch_ref)
    events_path = _events_path(workspace_root)
    batch_label = batch_ref_label(batch_ref)
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
                _AKK: _actor_kind,
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
    "batch_ref_for_phrase", "resolve_undo_phrase", "PHRASE_WIDER_DAYS",
    "undo_receipt_lines",
    "undone_auto_merges",
    "batch_ref_label",
    "batch_id_from_ref",
    "resolve_batch",
    "undo_batch",
    # LEDGERFENCE1 — `undo` after a meeting re-run has one meaning.
    "reprocess_batch_ref", "reprocess_report", "reprocess_undo_line",
    "undo_after_reprocess", "KEYLESS_RECEIPT_LINE",
    "UNBOUNDED_WINDOW_LINE",
    "BrainUndoError",
]
