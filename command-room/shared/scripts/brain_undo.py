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
    # alias by construction, gate (a) requires normalized-exact names, so
    # the record itself is already alias-free), then (2) re-open a
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

    seq = change.get("proposal_seq")
    fingerprint = change.get("proposal_fingerprint")
    if seq is None and not fingerprint:
        raise BrainUndoError(
            "person_proposal_tombstone reversal needs proposal_seq (or, for "
            "a seq-less proposal, proposal_fingerprint — D8)")
    data = {
        "reopened_by": undone_by,
        "reason": change.get("reason") or "brain undo — batch reversal",
    }
    if seq is not None:
        data["proposal_seq"] = int(seq)
    else:
        data["proposal_fingerprint"] = str(fingerprint)
    append_event(_events_path(workspace_root), [{
        "type": "person_proposal_reopened",
        "source_skill": source_skill,
        "data": data,
    }], holder="brain_undo")
    if seq is not None:
        return {"status": "reopened", "proposal_seq": int(seq)}
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
        "description": "unwind an automatic name-mention link (no alias was "
                       "written — gate (a) requires exact names); the "
                       "decision returns to the human as a confirm row",
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
    the PREVIOUS sent_reconcile audit event and before/at this one."""
    prev_seq = 0
    found = False
    for ev in events:
        if ev.get("type") != "sent_reconcile":
            continue
        seq = ev.get("seq") or 0
        if seq == audit_seq:
            found = True
            break
        prev_seq = seq
    if not found:
        raise BrainUndoError(f"no sent_reconcile audit event at seq {audit_seq}")
    out: list[dict] = []
    for ev in events:
        seq = ev.get("seq") or 0
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
    out: list[dict] = []
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("brain_batch_id") != batch_id:
            continue
        cls = data.get("brain_change_class")
        if not cls:
            continue
        change = {
            "change_class": cls,
            "change_ref": f"seq:{ev.get('seq')}",
        }
        for key in ("commitment_id", "dismissal_seq", "person_id", "org_id",
                    "proposal_seq", "proposal_fingerprint",
                    # UXR1 D3 — the person_link reverser's re-propose payload
                    # (stamped on the same_as tombstones at auto-link time).
                    "alias", "link_fingerprint", "link_evidence",
                    "matched_name"):
            if data.get(key) is not None:
                change[key] = data[key]
        out.append(change)
    return out


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
    "has_reverser",
    "resolve_batch",
    "undo_batch",
    "BrainUndoError",
]
