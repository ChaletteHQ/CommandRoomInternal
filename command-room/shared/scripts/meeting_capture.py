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
import re
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
    "objective_review",
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
    workspace_root=None,
) -> dict:
    """One unknown name → one `person_proposal` event (F-46 P2b: meeting-notes
    surfaced 'say add [name]' in chat only; dismiss the chat and the proposal
    is stranded forever). Proposals are pending-review BY DEFINITION — the
    flag is unconditional, the user's Add/Not-relevant click adjudicates.

    Dedup contract (caller's job, per past-meetings 4.5b / people_writer):
    call `people_writer.find_existing_person` FIRST; on a match emit a
    `person_update_proposal` referencing the existing id instead.

    WG1-B D-B3 (writer-side belt-and-suspenders): pass `workspace_root` and a
    name that `org_writer.find_existing_org` resolves to a tracked ORG is
    REFUSED as a person — the returned event is an `org_proposal` instead
    (the live TDX-Arena shape: name/signal/source_ref/pending_review), so an
    org-shaped payload never enters the person queue at the writer. The org
    rail's own existence gate then drops it at render when the org is already
    on file — honest, actioned semantics for free. Default None keeps every
    existing caller byte-identical.
    """
    if not name or not str(name).strip():
        raise ValueError("person_proposal needs a non-empty name")
    if workspace_root is not None:
        org = None
        try:
            from org_writer import find_existing_org
            org = find_existing_org(workspace_root, name=str(name).strip())
        except Exception:
            org = None
        if org is not None:
            org_data: dict = {
                "name": str(name).strip(),
                "signal": (evidence or review_reason
                           or f"mentioned as an org in {source_ref}"),
                "source_ref": source_ref,
                "pending_review": True,
                "confidence": confidence,
            }
            return {
                "type": "org_proposal",
                "source_skill": source_skill,
                "primary_thread_id": primary_thread_id,
                "data": org_data,
            }
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


def capture_telemetry(routed: Optional[dict]) -> dict:
    """The admission gates' own counts for ONE meeting, in receipt shape.

    `route_meeting_captures` has always RETURNED these; nothing persisted them,
    and that gap is what made a mis-tuned floor undetectable by construction:
    on a real audited week 27% of meeting captures were refused below the floor
    and the substrate carried no record that anything had been refused at all —
    so the acceptance re-measure had nothing to read. This is the smallest
    honest close: the counts ride the `meeting_processed` receipt both legs
    already write. No new event type, no new file, no new fire.

    COUNTS ONLY — never a title, never an evidence string. Two per-reason
    tallies ride along, both still counts and not content:

      `floor_reasons`   which FLOOR_* condition gated each below-floor item.
                        Since M's 2026-08-01 ruling those items are ROUTED
                        (to the queue, or to observed when someone else
                        plainly owes it) rather than dropped, so this is the
                        number that says how hard the floor is biting — the
                        one a re-tune actually reads. `n_floor_gated` is the
                        queue-routed share of it, and it is a SUBSET of
                        `n_review`, never added to it.
      `skipped_reasons` the genuine residue only. Near-empty by construction
                        now; a non-trivial value here means something the
                        canonical builder or the observed writer refused, and
                        is worth a look rather than a shrug.

    Accepts the whole `route_meeting_captures` return, or just its `summary`
    (the per-reason tallies need the full return — a bare summary yields the
    counts alone). Returns {} for None so a caller with no routing to report
    writes nothing at all."""
    if not routed:
        return {}
    summary = routed.get("summary") if isinstance(routed.get("summary"), dict) \
        else routed
    out: dict = {}
    for key in ("n_book", "n_review", "n_observed", "n_skipped",
                "n_floor_gated"):
        if key in summary:
            out[key] = int(summary.get(key) or 0)
    floor_reasons: dict = {}
    for verdict in (routed.get("verdicts") or []):
        reason = str((verdict or {}).get("floor_reason") or "").strip()
        if reason:
            floor_reasons[reason] = floor_reasons.get(reason, 0) + 1
    if floor_reasons:
        out["floor_reasons"] = floor_reasons
    reasons: dict = {}
    for row in (routed.get("skipped") or []):
        reason = str((row or {}).get("reason") or "").strip()
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    if reasons:
        out["skipped_reasons"] = reasons
    return out


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
    capture_summary: Optional[dict] = None,
) -> dict:
    """The processing receipt (F-46 P2a: past-meetings emits one, meeting-notes
    didn't — the no-prep / already-processed detectors read receipts, and
    dedup holding off the bare `meeting` event was luck, not contract).
    Substrate event, NOT a pack_run receipt (R1 owns those).

    `capture_summary` is the `route_meeting_captures` return (or its
    `summary`): its counts land on `data.capture_counts` — the only place the
    substrate records that the gates refused anything. Omit it and the receipt
    is byte-identical to the pre-CAPTUREFLOW shape, so every existing reader is
    untouched (additive key on an open `data` object)."""
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
    counts = capture_telemetry(capture_summary)
    if counts:
        data["capture_counts"] = counts
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


# =============================================================================
# CAPTUREFLOW — the source gates (2026-08-01). ONE helper, both meeting legs.
# =============================================================================
#
# WHY THIS EXISTS — the diagnosis, recorded where the code lives.
#
# The capture-load audit found three admission defects on the meeting path and
# they all have the SAME root cause: the meeting legs never enter the shared
# capture-gate code path at all.
#
#   * `capture_gate.classify_capture` (the W4c relevance gate, whose
#     DEFAULT_MODE has been `party-only` since v4.6.1) has exactly two callers
#     in the tree — `sent_capture` and `session_sweep`. Neither meeting leg
#     calls it, and neither leg's text names it. Party-only was never
#     "interpreted loosely" on the meeting path; it was never consulted.
#   * `meeting_capture.build_meeting_commitment_event` — the builder that DOES
#     run `capture_gate.gate_commitment_data` — is referenced by ZERO skill
#     texts. Both legs hand-build the commitment dict from a JSON template in
#     prose and append it through `event_gate.append_event`, which enforces
#     identity + kind and nothing about relevance, promise quality, or
#     provenance. (The instruction-layer gap class, G13 inverted.)
#   * The fusion guardrail (`orchestrator-past-meetings.md` § Cross-meeting
#     fusion guardrail) is prose with no code behind it, so a capture whose
#     evidence appears nowhere in its cited transcript is written unhindered.
#
# So the fix is one shared admission helper that both legs call BEFORE they
# append anything, and that decides — in one place — which of four places a
# meeting-extracted item goes:
#
#   book      an ordinary open commitment
#   review    a `pending_review` commitment: it lands in the needs-your-call
#             queue, never in the open book (INTAKE's split). Two kinds of row
#             arrive here — a fusion refusal (`fusion_unverified`), and, since
#             M's 2026-08-01 ruling, a below-floor capture (`floor_gated`).
#   observed  `commitment_observed` — kept, searchable, feeds prep, no open
#             item, no count, no row (the W4c observed tier)
#   skip      not written at all. Since the ruling this is a NEAR-EMPTY tier:
#             the floor no longer routes here. What is left is the honest
#             residue — an item the canonical builder itself refuses to
#             construct, or a third-party observed row the observed writer
#             refuses — and each one names why.
#
# PRECEDENCE — floor, then fusion, then relevance:
#   1. FLOOR (A1). Is this a commitment at all? Below-floor items go to the
#      REVIEW tier, or to observed when someone ELSE plainly owes it.
#   2. FUSION (A3). Can the evidence be found in the source transcript? A
#      capture that fails a check we could actually run is refused to `review`.
#   3. RELEVANCE (A2). Is the user a party? `capture_gate.classify_capture`,
#      with the mode + org override the policy layer already resolves.
#
# M RULING 2026-08-01 (spec §A1, and it SUPERSEDES the skip-silently line this
# module shipped with): a below-floor capture is NEVER silently dropped. The
# second-eyes review measured the shipped floor against the audit's own
# hand-judged 25-item sample and got precision 50% / recall 33% — for every
# junk capture the floor stopped it also destroyed a real promise, and the
# destroyed side left no event, no counter and no row, so nobody could ever
# find out. A heuristic that wrong cannot be given a silent delete. So the
# floor's verdict is now a ROUTING decision, not a deletion: the item lands in
# the grouped queue carrying `data.floor_gated: true` and its `FLOOR_*` reason
# as `review_reason`, where a human answers it in one pass. The marker exists
# so a FUTURE config toggle can hide floor-routed rows once M has run with
# them — the toggle itself is deliberately NOT built.
#
# The asymmetric caution rail (a dated / money item ALWAYS surfaces as open)
# is a RELEVANCE-layer rail — its own docstring scopes it to "regardless of
# mode or override". It still does NOT lift an item over the floor: a retold
# commitment with a date is still a retold commitment, and floor routing never
# puts anything on the BOOK. What the rail decides is which non-book place a
# below-floor item goes: `build_observed_event` refuses dated/money items by
# construction, so a below-floor DATED item goes to the queue rather than to
# the observed tier. Under the old ruling that same rail sent it nowhere at
# all — a documented "ALWAYS surfaces as open" doctrine resolving to "surfaces
# nowhere", which is the collision the ruling closes. Pinned by test.

FLOOR_NO_OWNER = "no identifiable owner"
FLOOR_NO_DELIVERABLE = "no concrete deliverable"
FLOOR_NO_CONSEQUENCE = "nothing depends on it"
FLOOR_RETOLD = "retold from an earlier conversation, not committed here"
FLOOR_NOT_ACCEPTED = "discussed, never accepted as a commitment"

FUSION_REVIEW_REASON = (
    "extracted phrase not in the source transcript — check the source before "
    "confirming"
)

# The verbatim window the fusion check requires, per
# orchestrator-past-meetings.md § Cross-meeting fusion guardrail.
FUSION_MIN_WORDS = 5

TIER_BOOK = "book"
TIER_REVIEW = "review"
TIER_OBSERVED = "observed"
TIER_SKIP = "skip"

_FLOOR_STOPWORDS = frozenset(
    "a an the to of for and or on in with by at from about that this it its "
    "is are was were be being been will would should could can may might "
    "must do does did have has had i we you he she they them their our your "
    "my me us up out over into onto per via as so then than if when while "
    "not no yes ok okay".split()
)

# A title whose HEAD is one of these is vague unless what follows it names a
# real object. "follow up" is below the floor; "follow up on the pricing sheet"
# is not. These are the exact shapes meeting-notes/SKILL.md already calls out
# ("circle back", "we should think about X", "discuss pricing").
_VAGUE_HEAD_RE = re.compile(
    r"(?i)^\s*(?:(?:we|i|they|he|she|you)\s+)?"
    r"(?:(?:should|shall|will|'ll|need\s+to|want\s+to|plan\s+to|ought\s+to|"
    r"(?:are|is|am)\s+going\s+to|may|might|could)\s+)?"
    r"(?:follow[-\s]?ups?|circle\s+back|touch\s+base|revisit|reconnect|"
    r"regroup|think\s+about|thinking\s+about|consider|discuss|talk\s+about|"
    r"talk\s+through|catch\s+up|check\s+in|keep\s+in\s+touch|stay\s+in\s+touch|"
    r"look\s+into|explore|sync(?:\s+up)?|connect|figure\s+out|see\s+about|"
    r"keep\s+an\s+eye\s+on|monitor|brainstorm|review\s+options|"
    r"keep\s+\S+\s+posted|keep\s+\S+\s+in\s+the\s+loop)\b"
)

# "a date depends on it" / "dropping it costs something", in the words people
# actually use. A counterparty ("someone is waiting on it") and a money amount
# are tested separately.
_CONSEQUENCE_RE = re.compile(
    r"""(?ix)
      \bby\s+(?:mon|tues?|wed|thur?s?|fri|sat|sun|jan|feb|mar|apr|may|jun|
                jul|aug|sep|oct|nov|dec|tomorrow|tonight|monday|tuesday|
                wednesday|thursday|friday|saturday|sunday|next\s|end\s+of|
                eod|eow|cob|then|\d)
    | \b(?:deadline|due\s+(?:date|by|on)|drop[- ]dead)\b
    | \bbefore\s+(?:the|our|their|we|they|it|he|she|monday|tuesday|wednesday|
                   thursday|friday|the\s+call)\b
    | \bahead\s+of\s+the\b
    | \bin\s+time\s+for\b
    | \bso\s+(?:that\s+)?(?:we|they|i|you|it)\s+can\b
    | \b(?:is|are|'s)?\s*block(?:ing|ed)\b
    | \bwaiting\s+on\b
    | \bholds?\s+up\b
    | \bfor\s+(?:the\s+)?(?:board|launch|kickoff|close|closing|renewal|
                            review|audit|filing|deadline)\b
    | \botherwise\s+we\b
    """
)

# Language that says a promise was actually MADE here.
_COMMIT_LANG_RE = re.compile(
    r"""(?ix)
      \b(?:i|we|he|she|they)\s*(?:'ll|\s+will|\s+am\s+going\s+to|
                                  \s+are\s+going\s+to|\s+is\s+going\s+to)\b
    | \b(?:i|we)\s+can\s+(?:have|get|send|put)\b
    | \blet\s+me\s+(?:send|get|put|pull|draft|write|set)\b
    | \b(?:will\s+(?:send|get|have|share|draft|circulate|deliver|write|set))\b
    | \b(?:on\s+it|will\s+do|consider\s+it\s+done|you'?ll\s+have\s+it|
           sending\s+(?:it|that|those)\s+over|i'?m\s+(?:sending|drafting|
           putting))\b
    | \b(?:agreed\s+to|committed\s+to|signed\s+up\s+to)\b
    """
)

# Language that says the thing was floated, hedged, requested, or refused —
# never accepted. (The audit's "unaccepted request" and "refusal" shapes.)
_HEDGE_LANG_RE = re.compile(
    r"""(?ix)
      \b(?:can|could|would|will)\s+you\b
    | \bdo\s+you\s+mind\b
    | \bany\s+chance\b
    | \bif\s+you\s+(?:can|could)\b
    | \b(?:we|someone|somebody|you)\s+should\b
    | \bit\s+would\s+be\s+(?:great|good|helpful|nice)\b
    | \b(?:maybe|perhaps|possibly|hopefully|ideally|eventually|
           at\s+some\s+point|down\s+the\s+road|one\s+of\s+these\s+days)\b
    | \b(?:i|we)\s+(?:don'?t|do\s+not)\s+think\b
    | \b(?:i|we)\s+(?:can'?t|cannot|won'?t|will\s+not)\b
    | \bnot\s+(?:able|going)\s+to\b
    | \bno\s+promises\b
    | \b(?:i|we)'?ll\s+(?:try|see)\b
    | \bwe'?ll\s+see\b
    | \bmight\s+be\s+able\s+to\b
    | \bthinking\s+about\b
    | \bnot\s+sure\s+(?:if|whether|we|i)\b
    """
)

# The item was made somewhere ELSE and is only being recounted here.
_RETOLD_RE = re.compile(
    r"""(?ix)
      \blast\s+(?:time|week|month|call|meeting|conversation|session)\b
    | \bpreviously\b
    | \bas\s+(?:i|we|you|they)\s+(?:said|mentioned|discussed|agreed)\b
    | \balready\s+(?:said|told|mentioned|committed|agreed|promised)\b
    | \bon\s+(?:our|the)\s+(?:last|previous|earlier)\s+
        (?:call|meeting|conversation)\b
    | \bfrom\s+(?:our|the)\s+(?:last|previous|earlier)\s+
        (?:call|meeting|conversation)\b
    | \b(?:back\s+in|earlier\s+this)\s+(?:january|february|march|april|may|
        june|july|august|september|october|november|december|week|month|year)\b
    | \bthe\s+(?:other|previous)\s+(?:day|week|call|meeting)\b
    """
)

_WORD_RE = re.compile(r"[a-z0-9']+")


def _floor_tokens(text) -> list:
    return [
        t for t in _WORD_RE.findall(str(text or "").lower())
        if len(t) > 1 and t not in _FLOOR_STOPWORDS
    ]


def _probe_data(item: dict) -> dict:
    """The `data`-shaped probe the pure floor/relevance tests read, built from
    a builder-kwargs item. Never appended — construction only."""
    data = {
        "title": item.get("title") or "",
        "kind": item.get("kind"),
        "evidence": item.get("evidence") or "",
    }
    for key in ("due", "no_due", "owner_id", "owner_external",
                "counterparty_id", "counterparty_name", "counterparty_ids",
                "counterparty_names", "attribution_ambiguous",
                "attribution_unknown", "attribution_candidates"):
        if item.get(key) not in (None, "", [], False):
            data[key] = item[key]
    return data


def _has_owner_signal(data: dict) -> bool:
    """(1) of the floor: an identifiable named person owns it.

    An item the speaker-attribution guard deliberately parked
    (`attribution_ambiguous` / `attribution_unknown`, owner_id "") HAS an owner
    signal — a human said it and the candidates are named; what is unresolved
    is WHICH human. The floor tests promise quality, not attribution
    (attribution is `capture_gate.gate_commitment_data`'s job, and conflating
    the two is what made the confirmed lane 20% junk). Only a capture with no
    owner reference of any kind fails here.
    """
    if str(data.get("owner_id") or "").strip():
        return True
    if str(data.get("owner_external") or "").strip():
        return True
    if data.get("attribution_candidates"):
        return True
    return bool(data.get("attribution_ambiguous")
                or data.get("attribution_unknown"))


def _has_counterparty(data: dict) -> bool:
    try:
        from commitment_parties import (counterparty_ids as _cp_ids,
                                        counterparty_names as _cp_names)
        return bool(_cp_ids(data) or _cp_names(data))
    except Exception:  # pragma: no cover — degrade to the scalar fields
        return bool(data.get("counterparty_id") or data.get("counterparty_name"))


def capture_floor_reason(data: dict) -> str:
    """THE capture floor, in code (A1). Returns "" when the item clears it, or
    the plain-language reason it does not.

    `meeting-notes/SKILL.md` has stated this floor since Stage D 2026-07 —
    (1) a clear owner, (2) a concrete deliverable, (3) a real consequence — and
    nothing enforced it: 8 of 25 sampled past-meetings captures were
    discussed-only, and two of those sat in the CONFIRMED lane because their
    owner field happened to resolve. Prose contracts don't hold; code
    chokepoints do.

    Pure — no I/O, no workspace. Order is by how cheaply the reason reads, and
    the first failing condition is the one reported."""
    data = data or {}
    title = str(data.get("title") or "")
    evidence = str(data.get("evidence") or "")
    blob = f"{title} {evidence}"

    if not _has_owner_signal(data):
        return FLOOR_NO_OWNER

    # (2) a concrete deliverable — a specific artifact or decision, not
    # "circle back".
    toks = _floor_tokens(title)
    if len(toks) < 2:
        return FLOOR_NO_DELIVERABLE
    head = _VAGUE_HEAD_RE.match(title)
    if head and len(_floor_tokens(title[head.end():])) < 2:
        return FLOOR_NO_DELIVERABLE

    # The retold shape (audit seq 6447): the promise exists, it was just made
    # on a different call. Capturing it again re-opens work already tracked.
    if _RETOLD_RE.search(blob):
        return FLOOR_RETOLD

    # The unaccepted-request / refusal shape (audit seq 6456). Only testable
    # when the extractor saved evidence; with no evidence there is no language
    # to read and this condition stays silent rather than guessing.
    if evidence.strip() and _HEDGE_LANG_RE.search(evidence) \
            and not _COMMIT_LANG_RE.search(evidence):
        return FLOOR_NOT_ACCEPTED

    # (3) a real consequence — someone is waiting, a date depends on it, or
    # dropping it costs something.
    try:
        from capture_gate import carries_due_or_money, parse_iso_date
    except ImportError:  # pragma: no cover — direct-path import
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capture_gate import carries_due_or_money, parse_iso_date
    if parse_iso_date(data.get("due")):
        return ""
    if _has_counterparty(data):
        return ""
    if carries_due_or_money(data):
        return ""
    if _CONSEQUENCE_RE.search(blob):
        return ""
    return FLOOR_NO_CONSEQUENCE


# The fusion check's OWN token class — deliberately narrower than
# `_WORD_RE`, which keeps the ASCII apostrophe as a word character. Review
# 2026-08-01: keeping it made the normalizer ASYMMETRIC across the exact two
# sources this check compares. A transcript writes the contraction with a
# curly apostrophe (U+2019, what every transcript backend emits) and an
# extracted evidence string writes it straight (or the reverse), so
# `i'll get the sow` and `i ll get the sow` never matched — a REAL capture
# refused to pending_review with no way for anyone to see why. Dropping the
# apostrophe from the class makes both sides `i ll`, and also stops a quoted
# evidence phrase (`'hello'`) from tokenizing differently to the unquoted
# transcript word. Fusion-only: `_floor_tokens` keeps `_WORD_RE`.
_FUSION_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize_for_fusion(text) -> str:
    return " ".join(_FUSION_WORD_RE.findall(str(text or "").lower()))


def fusion_refusal_reason(data: dict, transcript_text) -> str:
    """THE cross-meeting fusion guardrail, in code (A3). "" when the capture
    is provably grounded in the transcript it cites, or when the check cannot
    run; the refusal reason otherwise.

    `orchestrator-past-meetings.md` has REQUIRED this since v2.14.19 — "a 5+
    word substring of data.title / data.evidence actually appears in the
    transcript text of the meeting it's being attributed to" — as prose, with
    no code and no test. The audit's sample seq 6100 carried an evidence string
    that appears nowhere in its cited transcript, which is exactly what the
    guardrail exists to make impossible.

    SKIP-NOT-FAIL. No transcript text (the caller could not fetch one) and a
    too-short evidence/title both leave the check INERT: this refuses only what
    it can positively establish is absent. Pure — the caller supplies the
    transcript it already loaded (never re-fetch)."""
    haystack = _normalize_for_fusion(transcript_text)
    if not haystack:
        return ""
    data = data or {}
    for field in ("evidence", "title"):
        needle = _normalize_for_fusion(data.get(field))
        words = needle.split()
        if len(words) < FUSION_MIN_WORDS:
            continue
        if needle in haystack:
            return ""
        for i in range(len(words) - FUSION_MIN_WORDS + 1):
            if " ".join(words[i:i + FUSION_MIN_WORDS]) in haystack:
                return ""
        return FUSION_REVIEW_REASON
    return ""


def admit_meeting_capture(
    item: dict,
    *,
    transcript_text=None,
    capture_context: Optional[dict] = None,
    org_override: Optional[str] = None,
) -> dict:
    """The admission verdict for ONE meeting-extracted item. Pure.

    `item` is the same kwargs mapping `build_meeting_commitment_event` takes.
    `capture_context` is `capture_gate.workspace_capture_context(root)`,
    resolved ONCE per meeting by the caller; `org_override` is
    `capture_gate.resolve_capture_mode(root, org_id=…, org_name=…)`.

    Returns {"tier": book|review|observed|skip, "reason": str,
             "floor_reason": str, "fusion_reason": str,
             "relevance_reason": str}."""
    data = _probe_data(item)

    floor = capture_floor_reason(data)
    if floor:
        # Below the floor — and since M's 2026-08-01 ruling that is a routing
        # verdict, never a deletion (see the PRECEDENCE note above).
        #
        # It reaches the observed tier when someone else plainly owes it and
        # the observed writer will accept it (the caution rail refuses
        # dated/money items there by construction). EVERYTHING ELSE — the
        # user's own below-floor items, and every dated/money one — goes to
        # the REVIEW tier, where it is a `pending_review` row in the grouped
        # queue: visible, answerable, and never on the book.
        try:
            from capture_gate import carries_due_or_money
            rail = carries_due_or_money(data)
        except Exception:  # pragma: no cover
            rail = False
        ctx = capture_context or {}
        user_id = ctx.get("user_id")
        owner = str(data.get("owner_id") or "")
        someone_else_owes = bool(owner and user_id and owner != user_id)
        tier = TIER_OBSERVED if (someone_else_owes and not rail) else TIER_REVIEW
        return {"tier": tier, "reason": floor, "floor_reason": floor,
                "fusion_reason": "", "relevance_reason": ""}

    fusion = fusion_refusal_reason(data, transcript_text)
    if fusion:
        return {"tier": TIER_REVIEW, "reason": fusion, "floor_reason": "",
                "fusion_reason": fusion, "relevance_reason": ""}

    ctx = capture_context or {}
    try:
        from capture_gate import DEFAULT_MODE, classify_capture
    except ImportError:  # pragma: no cover — direct-path import
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capture_gate import DEFAULT_MODE, classify_capture
    verdict = classify_capture(
        data,
        mode=ctx.get("mode") or DEFAULT_MODE,
        user_id=ctx.get("user_id"),
        user_names=ctx.get("user_names") or (),
        team_ids=ctx.get("team_ids") or frozenset(),
        known_ids=ctx.get("known_ids") or frozenset(),
        org_override=org_override,
    )
    tier = TIER_BOOK if verdict["tier"] == "open" else TIER_OBSERVED
    return {"tier": tier, "reason": verdict["reason"], "floor_reason": "",
            "fusion_reason": "", "relevance_reason": verdict["reason"]}


def route_meeting_captures(
    items: Iterable[dict],
    *,
    workspace_root,
    source_ref: str,
    transcript_text=None,
    meeting_date: Optional[str] = None,
    org_id: Optional[str] = None,
    org_name: Optional[str] = None,
    primary_thread_id: Optional[str] = None,
    source_skill: str = "meeting-notes",
) -> dict:
    """THE meeting-capture admission path. Both meeting legs call this — it is
    the one place the floor, the fusion guardrail and party-only scoping run,
    and it hands back events that are ready to append.

    Every `items` entry is the kwargs mapping `build_meeting_commitment_event`
    already takes (title / kind / due / no_due / owner_id / owner_external /
    counterparty_* / evidence / person_ids / classification_confidence / …).
    `transcript_text` is the transcript the caller ALREADY loaded for this
    meeting — never re-fetched here, and omitting it leaves only the fusion
    check inert (skip-not-fail).

    Returns:
      {"book": [commitment events], "review": [pending_review commitments],
       "observed": [commitment_observed events],
       "skipped": [{"title", "reason"}], "verdicts": [{...}],
       "summary": {"n_book","n_review","n_observed","n_skipped",
                   "n_floor_gated"}}

    `review` now carries BOTH kinds of queue row: fusion refusals
    (`data.fusion_unverified`) and below-floor captures (`data.floor_gated`,
    M's 2026-08-01 ruling). `n_floor_gated` is a subset of `n_review`, not a
    fifth tier. `skipped` is near-empty by construction since the ruling —
    nothing below the floor goes there.

    Construction only — append `book + review + observed` through
    `event_gate.append_event` in ONE call, exactly as before."""
    try:
        from capture_gate import (build_observed_event, resolve_capture_mode,
                                  workspace_capture_context)
    except ImportError:  # pragma: no cover — direct-path import
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capture_gate import (build_observed_event, resolve_capture_mode,
                                  workspace_capture_context)

    ctx = workspace_capture_context(workspace_root)
    override = resolve_capture_mode(workspace_root, org_id=org_id,
                                    org_name=org_name)
    # `resolve_capture_mode` already folds the global mode; it is passed as the
    # override so a per-org rule beats the context's global value, and the
    # fail-open track-everything context (unresolvable primary user) still
    # wins — that is what `workspace_capture_context` forces it to.
    if ctx.get("mode") == "track-everything":
        override = "track-everything"

    book: List[dict] = []
    review: List[dict] = []
    observed: List[dict] = []
    skipped: List[dict] = []
    verdicts: List[dict] = []

    n_floor_gated = 0

    for raw in items or []:
        item = dict(raw or {})
        title = str(item.get("title") or "").strip()
        verdict = admit_meeting_capture(
            item, transcript_text=transcript_text, capture_context=ctx,
            org_override=override)
        verdicts.append({"title": title, **verdict})
        tier = verdict["tier"]
        floor_gated = bool(verdict["floor_reason"])

        if tier == TIER_OBSERVED:
            try:
                observed.append(build_observed_event(
                    title,
                    source_ref=source_ref,
                    reason=verdict["reason"],
                    kind=item.get("kind"),
                    owner_id=item.get("owner_id") or "",
                    owner_external=item.get("owner_external") or "",
                    counterparty_id=item.get("counterparty_id"),
                    counterparty_name=item.get("counterparty_name"),
                    evidence=item.get("evidence") or "",
                    primary_thread_id=(item.get("primary_thread_id")
                                       or primary_thread_id),
                    person_ids=item.get("person_ids"),
                    classification_confidence=item.get(
                        "classification_confidence"),
                    source_skill=source_skill,
                ))
                continue
            except Exception as exc:
                if not floor_gated:
                    # A RELEVANCE-observed row (A2, party-only) the observed
                    # writer refused. Unchanged by the ruling: there is no
                    # queue disposition for a third-party item, so it is
                    # skipped and says why.
                    #
                    # The EXCEPTION CLASS, never its message. Re-verify
                    # 2026-08-01: interpolating `exc` put the commitment's
                    # TITLE into this reason — `build_observed_event` names the
                    # item in its own error text — and since the telemetry
                    # round that reason became a `skipped_reasons` KEY on the
                    # persisted `meeting_processed` receipt. Demonstrated end
                    # to end: a title reached `data.capture_counts`, against
                    # this module's own "COUNTS ONLY — never a title" contract
                    # and the same promise in both leg texts. The suite's
                    # no-title check could not catch it: its fixture skips
                    # nothing, so it asserted over a receipt with no
                    # `skipped_reasons` key at all. Every other reason on this
                    # path is a bounded constant (a FLOOR_* string or one of
                    # `classify_capture`'s fixed verdicts); keeping the class
                    # name keeps this one bounded too, and still diagnostic.
                    skipped.append({
                        "title": title,
                        "reason": f"{verdict['reason']} "
                                  f"(the observed writer refused it: "
                                  f"{type(exc).__name__})"})
                    verdicts[-1]["tier"] = TIER_SKIP
                    continue
                # A FLOOR-gated row is never dropped (M ruling): the observed
                # writer refusing it sends it to the queue, not to nothing.
                tier = TIER_REVIEW
                verdicts[-1]["tier"] = TIER_REVIEW

        if tier == TIER_SKIP:
            # Unreachable from the floor since the ruling; kept so any future
            # verdict source still has an honest, named disposition.
            skipped.append({"title": title, "reason": verdict["reason"]})
            continue

        kwargs = dict(item)
        kwargs.pop("title", None)
        kwargs.setdefault("source_ref", source_ref)
        kwargs.setdefault("meeting_date", meeting_date)
        kwargs.setdefault("primary_thread_id", primary_thread_id)
        kwargs.setdefault("source_skill", source_skill)
        kwargs.pop("attribution_ambiguous", None)
        kwargs.pop("attribution_unknown", None)
        attribution_extra = {
            k: item[k] for k in ("attribution_ambiguous",
                                 "attribution_unknown",
                                 "attribution_candidates")
            if item.get(k) not in (None, "", [], False)
        }
        kwargs.pop("attribution_candidates", None)
        if tier == TIER_REVIEW:
            kwargs["pending_review"] = True
            kwargs["review_reason"] = verdict["reason"]
        try:
            ev = build_meeting_commitment_event(title, **kwargs)
        except ValueError:
            if not floor_gated:
                raise
            # The canonical builder refuses to CONSTRUCT it (its own identity
            # / kind contract, not the floor's). A below-floor item is the
            # likeliest thing to be malformed, and one of them must not take
            # the whole meeting's write down with it. This is the residue the
            # skip tier now holds, and it names itself.
            skipped.append({"title": title,
                            "reason": f"{verdict['reason']} "
                                      f"(the capture could not be built)"})
            verdicts[-1]["tier"] = TIER_SKIP
            continue
        if attribution_extra:
            ev["data"].update(attribution_extra)
        if tier == TIER_REVIEW:
            # The two ways a row lands in the queue are marked apart, because
            # they are different questions to a reader and — for `floor_gated`
            # — because a future config toggle needs something to key on.
            if floor_gated:
                ev["data"]["floor_gated"] = True
                n_floor_gated += 1
            else:
                ev["data"]["fusion_unverified"] = True
            review.append(ev)
        else:
            book.append(ev)

    return {
        "book": book,
        "review": review,
        "observed": observed,
        "skipped": skipped,
        "verdicts": verdicts,
        "summary": {
            "n_book": len(book),
            "n_review": len(review),
            "n_observed": len(observed),
            "n_skipped": len(skipped),
            # A SUBSET of n_review, not a fifth tier: V1 has to be able to
            # read the floor's yield apart from the fusion guardrail's, and
            # one combined review number cannot answer that.
            "n_floor_gated": n_floor_gated,
        },
    }


__all__ = [
    "build_meeting_commitment_event",
    "PENDING_REVIEW_CONFIDENCE_FLOOR",
    "MEETING_WRITE_TYPES",
    "build_decision_event",
    "build_person_proposal_event",
    "build_unidentified_attendee_event",
    "build_meeting_processed_event",
    "capture_telemetry",
    "count_meeting_writes",
    "already_processed",
    "verify_claims",
    # CAPTUREFLOW source gates
    "FLOOR_NO_OWNER",
    "FLOOR_NO_DELIVERABLE",
    "FLOOR_NO_CONSEQUENCE",
    "FLOOR_RETOLD",
    "FLOOR_NOT_ACCEPTED",
    "FUSION_REVIEW_REASON",
    "FUSION_MIN_WORDS",
    "TIER_BOOK",
    "TIER_REVIEW",
    "TIER_OBSERVED",
    "TIER_SKIP",
    "capture_floor_reason",
    "fusion_refusal_reason",
    "admit_meeting_capture",
    "route_meeting_captures",
]
