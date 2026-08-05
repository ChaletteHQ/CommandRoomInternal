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


def _changes_for_brain_batch(events: list[dict], batch_id: str) -> list[dict]:
    from event_seq import event_seq

    out: list[dict] = []
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("brain_batch_id") != batch_id:
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
                    # UXR1 D3 — the person_link reverser's re-propose payload
                    # (stamped on the same_as tombstones at auto-link time).
                    "alias", "link_fingerprint", "link_evidence",
                    "matched_name",
                    # AUTOAPPLY §4c — the merge reverser needs BOTH sides to
                    # name the pair it is putting back on the flag tier
                    # (supersede_commitment stamps superseded_by + the score).
                    "superseded_by", "auto_merge_score"):
            if data.get(key) is not None:
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
                           "ts": event_time(ev), "classes": set()})
            slot["n_changes"] += 1
            slot["classes"].add(data.get("brain_change_class"))
            if event_time(ev) > slot["ts"]:
                slot["ts"] = event_time(ev)
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
                            "ts": event_time(ev), "classes": {"commitment_close"}}

    out = []
    for slot in batches.values():
        classes = sorted(slot.pop("classes"))
        slot["label"] = _batch_label(classes, slot["n_changes"])
        out.append(slot)
    out.sort(key=lambda b: b["ts"], reverse=True)
    return out


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
    "commitment_done": "said a captured item was already done",
    "person_link": "linked a name to an existing contact",
    "person_org_creation_structured_fact": "added a contact",
    "entity_fact_structured": "noted a fact",
    "person_proposal_tombstone": "cleared an identity row",
    "chat_dismissal": "muted a row",
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
    for change in changes:
        cls = change["change_class"]
        entry = REVERSERS.get(cls)
        if entry is None:
            results.append({"status": "error", "change": change,
                            "error": f"no reverser registered for {cls!r}"})
            n_errors += 1
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
            continue
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
    status = "undone" if n_undone and not n_errors else (
        "partial" if n_undone else ("empty" if not changes else "error"))
    return {"status": status, "n_undone": n_undone, "n_errors": n_errors,
            "results": results}


__all__ = [
    "REVERSERS",
    "RECENT_BATCH_LIST_DAYS",
    "has_reverser",
    "recent_auto_batches",
    "undone_auto_merges",
    "resolve_batch",
    "undo_batch",
    "BrainUndoError",
]
