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


def _reverse_person_proposal_tombstone(workspace_root, change, *, undone_by,
                                       source_skill):
    # T2.2 (backlog sweep) — reverse an expire/skip tombstone on a person
    # proposal: append the additive person_proposal_reopened marker; the
    # confirm_flow reader honors the LAST writer, so the proposal re-surfaces.
    from event_gate import append_event

    seq = change.get("proposal_seq")
    if seq is None:
        raise BrainUndoError("person_proposal_tombstone reversal needs proposal_seq")
    append_event(_events_path(workspace_root), [{
        "type": "person_proposal_reopened",
        "source_skill": source_skill,
        "data": {
            "proposal_seq": int(seq),
            "reopened_by": undone_by,
            "reason": change.get("reason") or "brain undo — batch reversal",
        },
    }], holder="brain_undo")
    return {"status": "reopened", "proposal_seq": int(seq)}


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
    # T2.2 (FS-11b-extended backlog sweep): the sweep's expire tombstones are
    # undoable — the reverser appends person_proposal_reopened (additive; the
    # reader honors last-writer), so `undo` after a sweep restores the queue.
    "person_proposal_tombstone": {
        "reverse": _reverse_person_proposal_tombstone,
        "reverses_via": "person_proposal_reopened",
        "description": "reopen a person proposal the backlog sweep expired "
                       "or skipped (tombstone stays in history)",
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
                    "proposal_seq"):
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
