"""Evidence the owner has already answered (cr1#763).

One job: the (commitment, evidence pointer) pairs a closing job must not
apply again, because the owner has already answered that exact evidence.

WHY. The inbound-mail job and the tasks job re-read their window at every
fire. Nothing remembered which message or board item had already been
applied to which commitment, so an undone close was closed again a few
hours later by the same message, and a dismissed proposal came back from
the same board item. The owner's own answer was overridden by a re-read.

WHAT COUNTS AS AN ANSWER, read off the ledger and never stored twice:

  * a close (`commitment_resolved`) that a later `commitment_reopened`
    reversed: the close's own `source_ref` is spent for that commitment,
    whoever reopened it (the owner's `undo`, or a counter-evidence put-back),
    and so is the proposal that close confirmed, if it confirmed one;
  * a proposal (`commitment_review_proposed`) the owner dismissed
    (`commitment_review_dismissed`): the proposal's `source_ref` is spent for
    that commitment. A seq-addressed dismissal spends exactly that proposal;
    a legacy dismissal (no `proposal_seq`) spends every proposal the
    commitment carried up to that point. A system retract (the policy pass
    withdrawing its own proposal, or any other reason
    `is_non_dismissal_closure` names) is not the owner's answer and spends
    nothing.

New evidence, a different message or a later change to the board item, still
applies: a board item's events carry `evidence_version` (its done and updated
times), so the owner's answer is to the item as it stood. Read-only, pure over the
events it is given, never raises into a job.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def evidence_key(source_ref) -> str:
    """The identity form of a pointer, so two spellings of one artifact
    (legacy `gmail:` anchor, the resolved provider's key, a case-folded
    row) fold onto one key. '' when nothing resolves."""
    if source_ref in (None, ""):
        return ""
    try:
        from connector_adapters.provenance import dedup_key_of
        return str(dedup_key_of(source_ref) or "").strip()
    except Exception:  # noqa: BLE001 - a key that cannot fold compares raw
        return str(source_ref).strip().lower()


def _answer_key(data: dict) -> str:
    """The key an event's own pointer spends: its `evidence_ref` (the mail
    proposal's, which must not carry a `source_ref`: that key arms the chip
    timeout) or its `source_ref`, at the `evidence_version` it was applied
    at when it names one."""
    key = evidence_key(data.get("evidence_ref") or data.get("source_ref"))
    version = str(data.get("evidence_version") or "").strip()
    return f"{key}@{version}" if key and version else key


def answered_evidence(workspace_root=None, *, events=None) -> set:
    """`{(commitment_id, evidence key)}` the owner has answered. `events`
    (the owner-scoped ledger) is read from `workspace_root` when absent."""
    if events is None:
        if workspace_root is None:
            return set()
        try:
            from events_io import load_events_owner_scoped
            events, _skipped = load_events_owner_scoped(workspace_root)
        except Exception:  # noqa: BLE001
            return set()
    try:
        from closure_index import closer_target_id, reversed_closer_positions
        reversed_at = reversed_closer_positions(events)
    except Exception:  # noqa: BLE001
        return set()
    from event_types import is_non_dismissal_closure

    out: set = set()
    by_seq: dict = {}          # proposal seq -> (cid, key)
    by_cid: dict = {}          # cid -> [((cid, key), seq, evidence)] in append order
    for pos, ev in enumerate(events or []):
        if not isinstance(ev, dict):
            continue
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if et == "commitment_resolved":
            if pos not in reversed_at:
                continue
            cid = closer_target_id(ev)
            key = _answer_key(d)
            if cid and key:
                out.add((str(cid), key))
            # The owner's confirm closes under a session pointer, not the
            # proposal's: undoing it answers the proposal it confirmed, the
            # one it names or whose evidence it carries. Any other proposal
            # on the item is still unanswered.
            shown = str(d.get("evidence") or "")
            for pair, seq, words in by_cid.get(str(cid), []):
                if seq == d.get("applied_proposal_seq") or (shown and words == shown):
                    out.add(pair)
        elif et == "commitment_review_proposed":
            cid = str(d.get("commitment_id") or "")
            key = _answer_key(d)
            if not cid or not key:
                continue
            pair = (cid, key)
            seq = ev.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                by_seq[seq] = pair
            by_cid.setdefault(cid, []).append((pair, seq, str(d.get("evidence") or "")))
        elif et == "commitment_review_dismissed":
            if is_non_dismissal_closure(d):
                continue
            pseq = d.get("proposal_seq")
            if isinstance(pseq, int) and not isinstance(pseq, bool):
                hit = by_seq.get(pseq)
                if hit:
                    out.add(hit)
                continue
            cid = str(d.get("commitment_id") or ev.get("commitment_id") or "")
            if cid:
                out.update(pair for pair, _seq, _words in by_cid.get(cid, []))
    return out


def messages_used_for_proposals(workspace_root=None, *, events=None) -> set:
    """`{evidence key}`: the messages some proposal rests on, on any item, in
    any state (cr1#764). Such a message stays a proposal: a message the rail
    could not call for one item alone does not become sure of the other once
    the first is answered. Read from `evidence_ref`, the pointer the mail
    proposals carry."""
    if events is None:
        if workspace_root is None:
            return set()
        try:
            from events_io import load_events_owner_scoped
            events, _skipped = load_events_owner_scoped(workspace_root)
        except Exception:  # noqa: BLE001
            return set()
    out: set = set()
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("type") != "commitment_review_proposed":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        key = evidence_key(d.get("evidence_ref"))
        if key:
            out.add(key)
    return out


def is_answered(answered: Iterable, commitment_id, source_ref, *, version=None) -> bool:
    """Has the owner already answered `source_ref` for `commitment_id`, at
    this `version` of it? An answer written with no version covers the
    pointer whatever its version."""
    key = evidence_key(source_ref)
    if not key or not commitment_id:
        return False
    pairs = set(answered or ())
    if (str(commitment_id), key) in pairs:
        return True
    version = str(version or "").strip()
    return bool(version) and (str(commitment_id), f"{key}@{version}") in pairs


__all__ = ["evidence_key", "answered_evidence", "messages_used_for_proposals",
           "is_answered"]
