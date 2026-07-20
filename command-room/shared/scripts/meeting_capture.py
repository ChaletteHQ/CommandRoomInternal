#!/usr/bin/env python3
"""
Meeting-writer parity helpers (v4.5.2 C2 — F-46 P1/P2a/P2b, F-50 P2a).

The 2026-07 dogfood proved the two meeting writers ran different contracts:
past-meetings wrote `decision` + `person_proposal` + `meeting_processed`
events; meeting-notes claimed the same writes in chat and wrote none of them
("Decisions logged (3)" with zero decision events on disk — F-46). This module
gives meeting-notes (and any transcript writer) the SAME event shapes
past-meetings emits, plus the claim-audit read-back that makes "count the
events, then speak" a one-call primitive (the `validate_sweep_ran` doctrine:
the audit event is the proof, not the narration — Bug #98 family).

Builders are construction-only (same convention as `cru_match.build_*_event` /
`decision_match.build_*_event`): the caller appends through
`event_gate.append_event` / `atomic_append_jsonl`, which auto-stamp seq/ts
inside the writer lock. Omit `ts` unless you are backdating to meeting time.

Safety inversion (SPEC V4.5.2 "Safety, fold into C1/C2"): CRU auto-resolution
gates on `data.pending_review`, so a low-confidence extraction that FORGETS
the flag auto-resolves with no human gate. Builders here treat the flag as
default-on below the confidence floor — absence of the flag is an assertion
of high-confidence attribution, never an accident.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Dict, Iterable, List, Optional

try:
    from events_io import iter_events
except ImportError:  # direct-path import (tests, bash one-liners)
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events_io import iter_events

# Attribution-confidence floor: at/above this the writer may assert its
# attribution (no flag); below it `pending_review: true` is REQUIRED. Matches
# the auto-band floor in ORG_AND_THREAD_MODEL.md (≥0.75 auto).
PENDING_REVIEW_CONFIDENCE_FLOOR = 0.75

# Event families the claim-audit counts for one processed meeting. A closing
# chat summary may enumerate ONLY numbers read back from these.
MEETING_WRITE_TYPES = (
    "meeting",
    "meeting_processed",
    "decision",
    "commitment",
    "person_proposal",
    "person_update_proposal",
)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _norm_ref_keys(ref) -> set:
    """Normalized membership keys for a meeting reference. Live substrate
    carries BOTH `granola:<id>` and bare `<id>` spellings for the same meeting
    (observed drift, F-50 window) — index both so readers match either."""
    keys = set()
    s = str(ref or "").strip().lower()
    if not s:
        return keys
    keys.add(s)
    if ":" in s:
        prefix, _, tail = s.partition(":")
        if prefix in ("granola", "fireflies", "otter", "zoom", "teams") and tail:
            keys.add(tail)
    return keys


# -----------------------------------------------------------------------------
# Builders — same contract past-meetings writes (the reference implementation).
# -----------------------------------------------------------------------------


def build_meeting_commitment_event(
    title: str,
    *,
    source_ref: str,
    kind: str,
    due: Optional[str] = None,
    no_due: bool = False,
    owner_id: str = "",
    owner_external: str = "",
    counterparty_id: Optional[str] = None,
    counterparty_name: Optional[str] = None,
    counterparty_ids: Optional[List[str]] = None,
    counterparty_names: Optional[List[str]] = None,
    evidence: str = "",
    meeting_date: Optional[str] = None,
    source_event_seq: Optional[int] = None,
    primary_thread_id: Optional[str] = None,
    person_ids: Optional[List[str]] = None,
    classification_confidence: Optional[float] = None,
    pending_review: bool = False,
    review_reason: str = "",
    urgency: Optional[str] = None,
    source_skill: str = "meeting-notes",
) -> dict:
    """One extracted meeting commitment → one canonical `commitment` event
    dict, with the SHARED capture block enforced in code (v4.6.1 W4c
    consolidation — `capture_gate.gate_commitment_data`: Stage-D kind, S2
    due-nudge resolved against the MEETING's date, promise-vs-task, the
    pending_review inversion). This closes the meeting leg's C1/C2 parity gap
    the same way slack_capture / session_sweep already had it in code — the
    transcript writers no longer depend on prose alone to run the block.

    Construction only — append the batch through `event_gate.append_event`
    (ids minted and seq stamped inside the writer lock; C4's semantic dedup
    fires there). Raises ValueError on anything the extraction must go back
    and do."""
    try:
        from capture_gate import gate_commitment_data, parse_iso_date
    except ImportError:  # pragma: no cover — direct-path import
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capture_gate import gate_commitment_data, parse_iso_date

    title = (title or "").strip()
    if not title:
        raise ValueError("a meeting commitment needs a non-empty title")
    if not (source_ref or "").strip():
        raise ValueError(f"meeting commitment '{title}' needs a source_ref")

    due_str = (due or "").strip()
    data: dict = {
        "title": title,
        "kind": kind,
        "due": due_str,
        "source_ref": source_ref,
        # Origin discriminator (ACCOUNT_SCOPE §4a): meeting-transcript capture
        # is a connector read — the account-scope wall treats it STRICT.
        "origin": "connector",
    }
    if no_due:
        data["no_due"] = True
    if owner_id:
        data["owner_id"] = owner_id
    elif owner_external:
        data["owner_external"] = owner_external
    # MC1: normalize scalar + list counterparty inputs — single stays
    # byte-identical (scalar only), multi writes the list + primary scalar.
    from commitment_parties import build_counterparty_fields
    data.update(build_counterparty_fields(
        counterparty_id=counterparty_id, counterparty_name=counterparty_name,
        counterparty_ids=counterparty_ids, counterparty_names=counterparty_names,
    ))
    if evidence:
        data["evidence"] = evidence[:200]
    if meeting_date:
        data["meeting_date"] = meeting_date
    if source_event_seq is not None:
        data["source_event_seq"] = source_event_seq
    if urgency:
        data["urgency"] = urgency
    if pending_review:
        data["pending_review"] = True
        if review_reason:
            data["review_reason"] = review_reason

    gate_commitment_data(
        data,
        subject=f"meeting commitment {source_ref}",
        classification_confidence=classification_confidence,
    )

    data["status"] = "open"
    if due_str and parse_iso_date(due_str):
        if _dt.date.fromisoformat(due_str[:10]) < _dt.datetime.now(
            _dt.timezone.utc
        ).date():
            data["status"] = "overdue"

    # Stage E: resolved owner/counterparty ids are person references — the
    # dual-layer reader links via person_ids.
    pids = [p for p in (person_ids or []) if p]
    if owner_id and owner_id not in pids:
        pids.append(owner_id)
    from commitment_parties import counterparty_ids as _cp_ids
    for _cid in _cp_ids(data):  # MC1: every resolved counterparty
        if _cid not in pids:
            pids.append(_cid)

    ev: dict = {
        "type": "commitment",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "person_ids": pids,
        "data": data,
    }
    if classification_confidence is not None:
        ev["classification_confidence"] = classification_confidence
    return ev


def build_decision_event(
    summary: str,
    *,
    source_ref: str,
    source_skill: str = "meeting-notes",
    primary_thread_id: Optional[str] = None,
    person_ids: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    evidence: str = "",
    rationale: str = "",
    made_by: str = "",
    source_event_seq: Optional[int] = None,
    confidence: Optional[float] = None,
    pending_review: bool = False,
    classification_confidence: Optional[float] = None,
) -> dict:
    """One extracted decision → one `decision` event (F-46 P1: the write that
    was claimed and skipped). Shape matches the past-meetings writer
    (`summary`, `committed`, `evidence`, `source_ref`) plus decision-log's
    v3.13.0 `project_id` mandate when the caller can resolve it.

    `pending_review` is FORCED on when `confidence` is below the floor —
    passing a low confidence without the flag is the exact bug class the
    safety inversion closes.
    """
    if not summary or not str(summary).strip():
        raise ValueError("decision event needs a non-empty summary")
    if confidence is not None and confidence < PENDING_REVIEW_CONFIDENCE_FLOOR:
        pending_review = True
    data: dict = {
        "summary": str(summary).strip(),
        "source_ref": source_ref,
        "committed": not pending_review,
    }
    if evidence:
        data["evidence"] = evidence
    if rationale:
        data["rationale"] = rationale
    if made_by:
        data["made_by"] = made_by
    if project_id:
        data["project_id"] = project_id
    if source_event_seq is not None:
        data["source_event_seq"] = source_event_seq
    if confidence is not None:
        data["confidence"] = confidence
    if pending_review:
        data["pending_review"] = True
    ev: dict = {
        "type": "decision",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "person_ids": list(person_ids or []),
        "data": data,
    }
    if classification_confidence is not None:
        ev["classification_confidence"] = classification_confidence
    return ev


def build_person_proposal_event(
    name: str,
    *,
    source_ref: str,
    source_skill: str = "meeting-notes",
    primary_thread_id: Optional[str] = None,
    inferred_role: Optional[str] = None,
    inferred_org: Optional[str] = None,
    evidence: str = "",
    review_reason: str = "",
    confidence: float = 0.7,
) -> dict:
    """One unknown name → one `person_proposal` event (F-46 P2b: meeting-notes
    surfaced 'say add [name]' in chat only; dismiss the chat and the proposal
    is stranded forever). Proposals are pending-review BY DEFINITION — the
    flag is unconditional, the user's Add/Not-relevant click adjudicates.

    Dedup contract (caller's job, per past-meetings 4.5b / people_writer):
    call `people_writer.find_existing_person` FIRST; on a match emit a
    `person_update_proposal` referencing the existing id instead.
    """
    if not name or not str(name).strip():
        raise ValueError("person_proposal needs a non-empty name")
    data: dict = {
        "name": str(name).strip(),
        "inferred_role": inferred_role,
        "inferred_org": inferred_org,
        "confidence": confidence,
        "pending_review": True,
        "source_ref": source_ref,
    }
    if evidence:
        data["evidence"] = evidence
    if review_reason:
        data["review_reason"] = review_reason
    return {
        "type": "person_proposal",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": data,
    }


def build_unidentified_attendee_event(
    meeting_source_ref: str,
    *,
    attendee_hint: str,
    attendee_email: Optional[str] = None,
    primary_thread_id: Optional[str] = None,
    source_skill: str = "meeting-notes",
    evidence: str = "",
) -> dict:
    """An UNNAMED meeting speaker/attendee → ONE `unidentified_attendee_
    observed` annotation event (SPEC PID1 D5) — NEVER a person proposal.

    `build_person_proposal_event` raises on an empty name by design; the
    live no-name "proposals" came from skill texts working around that
    raise. This builder is the sanctioned path instead: named humans get
    person proposals, unnamed speakers get annotations. Annotations are
    FULLY SILENT (§0-4 ruling) — never a queue row; the only render is one
    count line in the weekly staff meeting. The Sunday `identity-reconcile`
    job joins them against calendar-invitee/participant metadata and later
    mail on the same address, promoting into the identity tiers only when a
    name appears (resolution is recorded in the job receipt's
    `annotations_resolved`, read by `identity_reconcile.
    load_open_annotations`).

    `attendee_hint` is the source's own label ("speaker 2", "att-7").
    `attendee_email` ONLY when the source metadata literally carries the
    address (Granola participant metadata, calendar invitee) — never
    guessed (F-08 extends to capture).
    """
    if not meeting_source_ref or not str(meeting_source_ref).strip():
        raise ValueError("unidentified_attendee_observed needs a "
                         "meeting_source_ref")
    if not attendee_hint or not str(attendee_hint).strip():
        raise ValueError("unidentified_attendee_observed needs an "
                         "attendee_hint (the source's own speaker label)")
    data: dict = {
        "meeting_source_ref": str(meeting_source_ref).strip(),
        "attendee_hint": str(attendee_hint).strip(),
        "attendee_email": (str(attendee_email).strip()
                           if attendee_email else None),
    }
    if evidence:
        data["evidence"] = evidence
    return {
        "type": "unidentified_attendee_observed",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": data,
    }


def build_meeting_processed_event(
    meeting_id: str,
    *,
    source_ref: Optional[str] = None,
    source_skill: str = "meeting-notes",
    primary_thread_id: Optional[str] = None,
    extracted_count: int = 0,
    pending_review_count: int = 0,
    brief_path: Optional[str] = None,
    processed_at: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """The processing receipt (F-46 P2a: past-meetings emits one, meeting-notes
    didn't — the no-prep / already-processed detectors read receipts, and
    dedup holding off the bare `meeting` event was luck, not contract).
    Substrate event, NOT a pack_run receipt (R1 owns those)."""
    if not meeting_id or not str(meeting_id).strip():
        raise ValueError("meeting_processed needs a meeting_id")
    if source_ref is None:
        mid = str(meeting_id)
        source_ref = mid if ":" in mid else f"granola:{mid}"
    data: dict = {
        "meeting_id": str(meeting_id),
        "source_ref": source_ref,
        "processed_at": processed_at or _now_iso(),
        "extracted_count": int(extracted_count),
        "pending_review_count": int(pending_review_count),
    }
    if brief_path:
        data["brief_path"] = brief_path
    if title:
        data["title"] = title
    return {
        "type": "meeting_processed",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "data": data,
    }


# -----------------------------------------------------------------------------
# Claim audit — count what's on disk, then speak.
# -----------------------------------------------------------------------------


def count_meeting_writes(
    workspace_root,
    source_ref: str,
    *,
    types: Iterable[str] = MEETING_WRITE_TYPES,
) -> Dict[str, int]:
    """Events actually on disk for one meeting, by type (shard-aware via
    events_io). Matches `data.source_ref` OR `data.meeting_id`, tolerant of
    the bare-id vs `granola:`-prefixed drift. This read-back — never the
    extraction intent — is what the closing chat summary enumerates."""
    wanted = set(types)
    ref_keys = _norm_ref_keys(source_ref)
    counts: Dict[str, int] = {t: 0 for t in wanted}
    if not ref_keys:
        return counts
    for ev in iter_events(workspace_root):
        etype = ev.get("type")
        if etype not in wanted:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ev_keys = _norm_ref_keys(data.get("source_ref")) | _norm_ref_keys(
            data.get("meeting_id")
        )
        if ev_keys & ref_keys:
            counts[etype] += 1
    return counts


def already_processed(workspace_root, source_ref: str) -> bool:
    """True when a `meeting_processed` receipt exists for this meeting — the
    canonical already-processed marker (dedup must not depend on the bare
    `meeting` event; F-50 proved that only held by accident)."""
    counts = count_meeting_writes(
        workspace_root, source_ref, types=("meeting_processed",)
    )
    return counts.get("meeting_processed", 0) > 0


def verify_claims(
    workspace_root,
    source_ref: str,
    claims: Dict[str, int],
) -> dict:
    """The claim-audit gate: compare what the writer INTENDS to say against
    what is on disk. Returns {ok, counts, mismatches}; ok is True only when
    every claimed count equals the disk count (F-50 P2a: claimed 7 decisions,
    wrote 6 — 'at least' is not honesty). A failed write must be spoken as a
    failed write, not absorbed into the claim."""
    counts = count_meeting_writes(
        workspace_root, source_ref, types=set(MEETING_WRITE_TYPES) | set(claims)
    )
    mismatches = []
    for etype, claimed in claims.items():
        actual = counts.get(etype, 0)
        if int(claimed) != actual:
            mismatches.append(
                {"type": etype, "claimed": int(claimed), "on_disk": actual}
            )
    return {"ok": not mismatches, "counts": counts, "mismatches": mismatches}


__all__ = [
    "build_meeting_commitment_event",
    "PENDING_REVIEW_CONFIDENCE_FLOOR",
    "MEETING_WRITE_TYPES",
    "build_decision_event",
    "build_person_proposal_event",
    "build_unidentified_attendee_event",
    "build_meeting_processed_event",
    "count_meeting_writes",
    "already_processed",
    "verify_claims",
]
