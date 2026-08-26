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
try:
    from text_clip import clip  # noqa: E402
except ImportError:  # pragma: no cover — direct-path fallback
    import sys as _sys_tc
    from pathlib import Path as _Path_tc
    _sys_tc.path.insert(0, str(_Path_tc(__file__).resolve().parent))
    from text_clip import clip  # noqa: E402

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


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _now_iso() -> str:
    return _clock_now().isoformat()


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


def build_meeting_event(
    title: str,
    *,
    source_ref: str,
    source_skill: str = "meeting-notes",
    primary_thread_id: Optional[str] = None,
    org_ids: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    attendees: Optional[List[str]] = None,
    attendees_external: Optional[List[str]] = None,
    summary: Optional[str] = None,
    meeting_type: Optional[str] = None,
    duration_min: Optional[int] = None,
    status: Optional[str] = None,
    ts: Optional[str] = None,
    source_had_attendees: Optional[bool] = None,
) -> dict:
    """The one sanctioned constructor for a `meeting` event (BUG-8244).

    Before this existed the `meeting` event was the ONLY primary event with no
    builder: 16 writer sites improvised 4 incompatible attendee shapes, the
    highest-volume writer (meeting-notes) had a prose promise and no recipe,
    and a client workspace shipped every one of its meeting events with no
    person binding — which silently degraded 19 downstream readers
    (relationship cadence read weeks-since-contact for people met daily).

    Canonical binding, BOTH halves:
      * top-level `person_ids[]` — attendees resolved against entities.json
      * `data.attendees[]`      — invitee EMAILS verbatim from the source
        (calendar invite / transcript backend), so identity-reconcile can
        corroborate merges and later resolution can repair what today's
        entities.json cannot match
      * `data.attendees_external[]` — display names with no entities match

    `data.attendee_person_ids` / `data.attendee_emails` are legacy read-only
    variants (readers fold them via `event_refs.meeting_person_ids`); this
    builder never emits them and no new writer may.

    An empty binding is legal (PASSIVE_CAPTURE: a meeting with no matchable
    attendees captures with `person_ids: []`) but it is always EXPLICIT: both
    keys are present even when empty, and when the caller says the source DID
    carry attendees (`source_had_attendees=True`) an empty result additionally
    stamps `data.binding_missing: true` so the claim audit and the backfill
    can find it. Capture is never blocked over metadata — flag, don't drop.

    Strings without an "@" passed as `attendees` are rerouted to
    `attendees_external`: the emails-as-names drift is exactly how the
    4-shape fork happened, so the builder refuses to write it.
    """
    if not str(title or "").strip() and not str(summary or "").strip():
        raise ValueError("meeting event needs a title or summary")
    if not str(source_ref or "").strip():
        raise ValueError("meeting event needs a source_ref (dedup key)")

    pids: List[str] = []
    for p in person_ids or []:
        p = str(p or "").strip()
        if p and p not in pids:
            pids.append(p)

    emails: List[str] = []
    external: List[str] = []
    for a in attendees or []:
        a = str(a or "").strip()
        if not a:
            continue
        if "@" in a:
            low = a.lower()
            if low not in emails:
                emails.append(low)
        elif a not in external:
            external.append(a)
    for n in attendees_external or []:
        n = str(n or "").strip()
        if n and n not in external:
            external.append(n)

    data: dict = {
        "title": str(title or "").strip(),
        "source_ref": str(source_ref).strip(),
        "attendees": emails,
        "attendees_external": external,
    }
    if summary:
        data["summary"] = str(summary).strip()
    if meeting_type:
        data["meeting_type"] = str(meeting_type).strip()
    if duration_min is not None:
        data["duration_min"] = int(duration_min)
    if status:
        data["status"] = str(status).strip()
    if source_had_attendees and not (pids or emails or external):
        data["binding_missing"] = True

    ev: dict = {
        "type": "meeting",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "person_ids": pids,
        "data": data,
    }
    if org_ids:
        ev["org_ids"] = [str(o).strip() for o in org_ids if str(o or "").strip()]
    if ts:
        ev["ts"] = ts
    return ev


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
        data["evidence"] = clip(evidence)
    if meeting_date:
        data["meeting_date"] = meeting_date
    if source_event_seq is not None:
        data["source_event_seq"] = source_event_seq
    # BUG-8330 item 16 — `data.urgency` RETIRED from the write: it was
    # written here and by slack_capture and read by NOTHING (the schema
    # claimed morning-briefing consumed it; no such reader exists). The
    # parameter stays for caller compat; the value is dropped. Historic
    # rows keep their field (append-only). Wire a real reader before ever
    # re-adding the stamp — guard G29 fails a writer-without-reader field.
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
        if _dt.date.fromisoformat(due_str[:10]) < _clock_now().date():
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

      `floor_reasons`   which FLOOR_* condition gated each below-floor item,
                        keyed by the STABLE code (`FLOOR_NO_CONSEQUENCE`, …) —
                        never by the row-copy sentence, which is an author's
                        wording and not a name. `parse_floor_reasons` is the
                        reader; it takes the prose-keyed receipts already on
                        disk too, so no tally loses its history.
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

    `n_fusion_inert` (PREC1) rides the same receipt: how many written rows the
    fusion guardrail could not check at all. It is NOT a tier and NOT a subset
    of one — it cuts across book / review / observed — so it is never added to
    another count. Before it existed, a fire with no transcript produced a
    receipt identical to a fully-verified one, which is what let the FLOOR3
    replay find 20 unflagged unverifiable rows in a population of 131.

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
                "n_floor_gated", "n_deduped", "n_fusion_inert"):
        if key in summary:
            out[key] = int(summary.get(key) or 0)
    floor_reasons: dict = {}
    for verdict in (routed.get("verdicts") or []):
        v = verdict or {}
        if v.get("duplicate"):
            # FLOOR3 E — a twin that folded into a survivor was never written,
            # and counting its floor verdict here would say the floor bit twice
            # on one act. The re-measure reads this tally to decide whether the
            # floor is mis-tuned; it has to count ROWS, not extractions.
            continue
        # `floor_code` when the verdict carries one, the prose when it does not
        # (a caller holding a pre-fix verdict dict); `floor_reason_code` reads
        # both and buckets an unrecognised third spelling as `legacy`.
        code = floor_reason_code(v.get("floor_code") or v.get("floor_reason"))
        if code:
            floor_reasons[code] = floor_reasons.get(code, 0) + 1
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


def meeting_binding_audit(workspace_root, source_ref: str) -> dict:
    """BUG-8244 claim-audit extension: does the `meeting` event for this
    source_ref carry a person binding? Returns
    {found, bound, binding_missing_flagged} — `bound` is True when ANY
    binding variant is present (top-level person_ids, data.attendees /
    attendee_person_ids / attendee_emails / attendees_external);
    `binding_missing_flagged` mirrors the builder's explicit
    `data.binding_missing` stamp. The closing summary surfaces an unbound
    meeting the same way it surfaces a failed write: plainly. A meeting
    written with no binding and no flag is the write defect that left a
    client workspace fully unbound and its weekly cadence read wrong."""
    ref_keys = _norm_ref_keys(source_ref)
    out = {"found": False, "bound": False, "binding_missing_flagged": False}
    if not ref_keys:
        return out
    try:
        from event_refs import attendee_emails_of, meeting_person_ids
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from event_refs import attendee_emails_of, meeting_person_ids
    for ev in iter_events(workspace_root):
        if ev.get("type") != "meeting":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ev_keys = _norm_ref_keys(data.get("source_ref")) | _norm_ref_keys(
            data.get("meeting_id")
        )
        if not (ev_keys & ref_keys):
            continue
        out["found"] = True
        if data.get("binding_missing"):
            out["binding_missing_flagged"] = True
        if (meeting_person_ids(ev) or attendee_emails_of(ev)
                or (data.get("attendees_external") or [])):
            out["bound"] = True
        if out["bound"]:
            break
    return out


def _norm_name(name) -> str:
    """Case/whitespace-normalized person name for support checks."""
    return re.sub(r"\s+", " ", str(name or "").strip()).lower()


def brief_counterparty(meeting_event, *, email_index=None,
                       exclude_person_ids=(), exclude_names=(),
                       resolved_names=None, association=None) -> dict:
    """THE BRIEF BINDER (SPEC BRIEFBIND1, BUG-8244 / G28 family). Who a
    brief's headline may name, derived from the cited meeting's OWN
    participant record — and from nothing else.

    THE DEFECT THIS FENCES. An evening-close brief was headlined with a
    person who appears nowhere on the cited meeting's participant record —
    right topic cluster, wrong human, filled by association from a recent
    joint session after a participant-less sibling capture left a vacuum. A
    wrong binding is strictly worse than a missing one: it looks complete,
    and downstream surfaces (call-prep context, relationship history,
    commitment attribution) key person context on it.

    THE EVIDENCE RULE. The counterparty derives from the record the brief
    CITES: `person_ids` (every legacy variant, folded by
    `event_refs.meeting_person_ids`, resolved through `email_index` when
    given) plus `data.attendees_external` names. An empty record renders
    UNBOUND — the existing G28 contract — and is never filled from topical
    or temporal association.

    `association` is accepted so a call site can hand over whatever
    association evidence it holds (a recent joint session's names, a
    calendar neighbor) and have the refusal on the record: it is echoed back
    as `association_ignored` and NOTHING in this function reads it to pick a
    name — neither when the record binds nor when it is empty. That
    non-consultation is pinned by mutation in
    `tests/run_meetcount1_briefbind1_mutation_test.py`.

    `exclude_person_ids` / `exclude_names`: the operator's own identity —
    the headline names the counterparty, not the CEO. `resolved_names`:
    optional {person_id: display_name} so the return can carry names for
    bound ids.

    Returns `{"bound", "person_ids", "names", "unbound_reason",
    "association_ignored"}`.
    """
    ev = meeting_event if isinstance(meeting_event, dict) else {}
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    try:
        from event_refs import meeting_person_ids
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from event_refs import meeting_person_ids
    excluded_ids = {str(x) for x in (exclude_person_ids or ()) if x}
    excluded_names = {_norm_name(x) for x in (exclude_names or ()) if x}
    person_ids = sorted(
        pid for pid in meeting_person_ids(ev, email_index)
        if pid and str(pid) not in excluded_ids)
    names = []
    lookup = resolved_names if isinstance(resolved_names, dict) else {}
    for pid in person_ids:
        nm = lookup.get(pid)
        if nm and _norm_name(nm) not in excluded_names:
            names.append(str(nm))
    for nm in (data.get("attendees_external") or []):
        if isinstance(nm, str) and nm.strip() \
                and _norm_name(nm) not in excluded_names \
                and _norm_name(nm) not in {_norm_name(n) for n in names}:
            names.append(nm.strip())
    association_ignored = [str(a) for a in (association or ()) if a]
    if person_ids or names:
        # The record's own evidence, and nothing else, decides the binding.
        return {"bound": True, "person_ids": person_ids, "names": names,
                "unbound_reason": None,
                "association_ignored": association_ignored}
    # An empty record renders UNBOUND (the G28 contract) — never
    # association-filled: the vacuum is exactly where the wrong name got in.
    return {"bound": False, "person_ids": [], "names": [],
            "unbound_reason": "the cited record names no participants",
            "association_ignored": association_ignored}


def brief_claim_audit(meeting_event, claimed, *, email_index=None,
                      resolved_names=None) -> dict:
    """G28's MIS-binding half (SPEC BRIEFBIND1). Does the cited record
    SUPPORT the counterparty a brief names?

    `meeting_binding_audit` answers "is the record bound at all" — the
    UN-binding class BUG-8244 fixed. It cannot see the strictly worse
    failure: a record that names person A under a brief that names person B.
    This audit takes the claim itself and reds on exactly that.

    `claimed`: the person the brief headlines — a person_id or a display
    name. Falsy `claimed` asserts the brief rendered UNBOUND.

    Verdicts (`{"record_present", "supported", "reason"}`):
      * record present, claim among its participants  -> supported
      * record present, claim absent from it          -> NOT supported —
        the MIS-binding case ("the cited record does not name them")
      * record empty, brief names someone             -> NOT supported —
        the association-fill case
      * record empty, brief unbound                   -> supported (the
        G28 contract shape)
    """
    ev = meeting_event if isinstance(meeting_event, dict) else {}
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    try:
        from event_refs import meeting_person_ids
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from event_refs import meeting_person_ids
    person_ids = {str(p) for p in meeting_person_ids(ev, email_index)}
    external = [n for n in (data.get("attendees_external") or [])
                if isinstance(n, str) and n.strip()]
    record_present = bool(person_ids or external)
    claim = str(claimed or "").strip()
    if not claim:
        if record_present:
            return {"record_present": True, "supported": False,
                    "reason": "the record names participants and the brief "
                              "renders unbound"}
        return {"record_present": False, "supported": True,
                "reason": "empty record, unbound brief — the G28 contract"}
    if not record_present:
        return {"record_present": False, "supported": False,
                "reason": "the cited record names no participants — a named "
                          "counterparty here is association-filled"}
    supported_names = {_norm_name(n) for n in external}
    lookup = resolved_names if isinstance(resolved_names, dict) else {}
    for pid in person_ids:
        nm = lookup.get(pid)
        if nm:
            supported_names.add(_norm_name(nm))
    if claim in person_ids or _norm_name(claim) in supported_names:
        return {"record_present": True, "supported": True,
                "reason": "named on the cited record"}
    return {"record_present": True, "supported": False,
            "reason": "the cited record does not name them"}


def meeting_ref_keys(ref) -> set:
    """PUBLIC name for the meeting-reference key derivation (`_norm_ref_keys`).

    Exported (SPEC EODFIX1 §2-4) so the End of Day's catch-up sweep can build a
    receipt index in ONE pass while still agreeing with `already_processed`
    meeting-for-meeting. Two spellings of "is this the same meeting" is exactly
    the drift that made a record look processed to one path and unprocessed to
    another; a cross-module import of the one derivation is the fix, and a
    private name would have guaranteed a second copy."""
    return _norm_ref_keys(ref)


def already_processed(workspace_root, source_ref: str) -> bool:
    """True when a `meeting_processed` receipt exists for this meeting — the
    canonical already-processed marker (dedup must not depend on the bare
    `meeting` event; F-50 proved that only held by accident).

    THERE ARE TWO ANSWERS BY DESIGN, AND THIS IS THE NARROWER ONE. This
    function asks "was work extracted from it". The End of Day catch-up sweep
    asks "is it still owed", and a `meeting_skipped` event answers that one
    too — a deliberate exclusion is handled, not owed — so the sweep counts
    `meeting_discovery.PROCESSED_RECEIPT_TYPES` rather than this predicate
    alone. Neither side counts a bare `meeting` record. Said here as well as
    there because a reader who starts from this docstring and assumes it is
    the only answer is how the two-definitions bug got written in the first
    place (review N-7)."""
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
# FLOOR2 (2026-08-06) — the two junk MODES the V1 interim re-measure found on
# real transcripts. Both are things the five conditions above cannot see,
# because both are visible only by reading the MEETING, not the sentence:
#   J-1 an action performed DURING the call ("just click that right now")
#       carries an owner, a deliverable and a consequence, so it clears every
#       condition above — what it lacks is a deliverable that survives the
#       meeting.
#   J-2 an offer the SAME conversation then took back ("you don't need to").
#       Scored in isolation the sentence is a real promise; nothing re-read the
#       rest of the transcript before writing it.
FLOOR_DONE_IN_MEETING = "already done during the call, nothing left afterwards"
FLOOR_SUPERSEDED_IN_MEETING = "taken back later in the same conversation"

# The same five conditions under their STABLE names.
#
# The strings above are ROW COPY — a below-floor row's `review_reason` shows one
# of them to a human, and that is the only reason they are sentences. They were
# ALSO, until this fix, the key `capture_telemetry` tallied `floor_reasons`
# under, so a live receipt carried `{"nothing depends on it": 25}`. Benign (a
# sentence is not a title, and the COUNTS-ONLY contract held) but useless as a
# tally: the key is a phrase an author may reword for clarity at any time, and
# the moment one does, every prior receipt's bucket silently splits in two and
# the per-reason number V1's floor re-tune reads stops adding up. Two names for
# two jobs — a code to count by, prose to read.
FLOOR_CODE_NO_OWNER = "FLOOR_NO_OWNER"
FLOOR_CODE_NO_DELIVERABLE = "FLOOR_NO_DELIVERABLE"
FLOOR_CODE_NO_CONSEQUENCE = "FLOOR_NO_CONSEQUENCE"
FLOOR_CODE_RETOLD = "FLOOR_RETOLD"
FLOOR_CODE_NOT_ACCEPTED = "FLOOR_NOT_ACCEPTED"
FLOOR_CODE_DONE_IN_MEETING = "FLOOR_DONE_IN_MEETING"
FLOOR_CODE_SUPERSEDED_IN_MEETING = "FLOOR_SUPERSEDED_IN_MEETING"

# Every key a receipt written from here can carry, plus the one bucket a READER
# needs for the receipts that already exist.
FLOOR_CODES = {
    FLOOR_NO_OWNER: FLOOR_CODE_NO_OWNER,
    FLOOR_NO_DELIVERABLE: FLOOR_CODE_NO_DELIVERABLE,
    FLOOR_NO_CONSEQUENCE: FLOOR_CODE_NO_CONSEQUENCE,
    FLOOR_RETOLD: FLOOR_CODE_RETOLD,
    FLOOR_NOT_ACCEPTED: FLOOR_CODE_NOT_ACCEPTED,
    FLOOR_DONE_IN_MEETING: FLOOR_CODE_DONE_IN_MEETING,
    FLOOR_SUPERSEDED_IN_MEETING: FLOOR_CODE_SUPERSEDED_IN_MEETING,
}
FLOOR_CODE_VALUES = frozenset(FLOOR_CODES.values())
# FLOOR2 C1: the catch-all bucket is itself spelled as a FLOOR_ code, so the
# WRITE-side invariant is total — every key `capture_telemetry` can emit
# matches FLOOR_CODE_RE, including the one that means "I did not recognise
# this". It was `"legacy"`, which is the one value a receipt could carry that
# a `^FLOOR_[A-Z_]+$` pin would have to carve an exception for, and an
# invariant with an exception is a convention. Readers key off the CONSTANT
# (`parse_floor_reasons` re-keys either spelling), so the rename costs a
# reader nothing.
FLOOR_CODE_LEGACY = "FLOOR_LEGACY"

# The shape every `floor_reasons` key on a receipt has (FLOOR2 C1's pin). A
# tally keyed by anything else is a sentence someone may reword — the exact
# drift the code enum exists to stop.
FLOOR_CODE_RE = re.compile(r"^FLOOR_[A-Z_]+$")


def floor_reason_code(reason) -> str:
    """The stable code for a floor verdict, from the code OR the prose.

    Accepts either spelling so a caller holding a pre-fix verdict (or a
    persisted receipt key) resolves to the same bucket. Anything unrecognised
    is `FLOOR_CODE_LEGACY` — never dropped, never guessed at. "" for no floor
    verdict at all, so `if code:` reads exactly like `if floor:` did.
    """
    text = str(reason or "").strip()
    if not text:
        return ""
    if text in FLOOR_CODE_VALUES:
        return text
    return FLOOR_CODES.get(text, FLOOR_CODE_LEGACY)


def parse_floor_reasons(mapping) -> dict:
    """A persisted `floor_reasons` tally, re-keyed to codes. THE reader.

    Receipts written before this fix are keyed by prose. Those keys still
    resolve — `floor_reason_code` reads both spellings — and a key that matches
    NEITHER (an older wording, a hand-edited receipt) lands in the `legacy`
    bucket rather than being dropped: the whole point of the tally is that the
    total adds up, and a silently discarded key is the failure mode that made
    the mis-tuned floor invisible in the first place. Counts from different
    keys that map to the same code are summed. Non-integer values contribute 0
    but still create their bucket, so "this reason occurred" survives even when
    "how many" does not.
    """
    out: dict = {}
    if not isinstance(mapping, dict):
        return out
    for key, value in mapping.items():
        code = floor_reason_code(key)
        if not code:
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            n = 0
        out[code] = out.get(code, 0) + n
    return out

FUSION_REVIEW_REASON = (
    "extracted phrase not in the source transcript — check the source before "
    "confirming"
)

# The verbatim window the fusion check requires, per
# orchestrator-past-meetings.md § Cross-meeting fusion guardrail.
FUSION_MIN_WORDS = 5

# The THREE outcomes of the fusion check. There have always been three; only
# two were ever expressible, and the missing one is the defect PREC1 opens
# with.
#
#   verified  the evidence (or the title) locates verbatim in the transcript
#             this capture cites. The guardrail ran and passed.
#   refused   it does not locate. The guardrail ran and refused —
#             `FUSION_REVIEW_REASON`, queue row, `data.fusion_unverified`.
#   inert     the guardrail COULD NOT RUN: no transcript was supplied, or
#             neither evidence nor title carries `FUSION_MIN_WORDS` to check.
#
# `fusion_refusal_reason` collapses verified and inert to the same "" — which
# is right for ROUTING (skip-not-fail: refuse only what you can positively
# establish is absent) and wrong for every reader downstream, because it makes
# a capture written without a transcript indistinguishable from one that
# passed. FLOOR3_REPLAY_2026-08-14 §3 measured the cost: 20 of 131 live
# captures carry evidence that is not verbatim from their transcript and
# NOTHING on the row says so. Absence of a refusal was reading as a pass.
FUSION_VERIFIED = "verified"
FUSION_REFUSED = "refused"
FUSION_INERT = "inert"

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
    # EXCH1 rider — dependency stated as a noun. A row whose own captured text
    # called the item "the standing obstacle on the closing rock" was stamped
    # "nothing depends on it": the class knew the verb forms of blocking and
    # none of the nouns people use for the same fact.
    | \b(?:obstacle|blocker|roadblock|bottleneck)\b
    | \bhinges\s+on\b
    | \bcritical\s+path\b
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
    # PREC1 — the NEGATED form of the member above. English asks the polite
    # favour as "if you don't mind …" far more often than "do you mind …", and
    # the class carried only the positive polarity, so the whole family leaked.
    #
    # AUTHORED FROM THE REAL `data.evidence` STRINGS AND MEASURED BEFORE IT WAS
    # KEPT — the FLOOR3-v1 discipline. A class written against a DESCRIPTION of
    # the data (the audit's quote column, the spec's characterisation of a
    # register) is the mistake this whole family exists to stop.
    #
    # MEASURED by replaying the live substrate through both code sets with
    # `scripts/replay_captures.py`: 930 comparable meeting-derived captures,
    # 503 of them booked by the old code. This member moves 4 of those 503 off
    # the book. One is seq 8248 — labelled J-1 by the V1 re-measure, and the
    # item FLOOR3 v2 could not reach from the done-in-meeting side because the
    # discharge cue is absent; read as an unaccepted REQUEST the phrase is
    # right there in the evidence. Of the other three, one is the same shape
    # unlabelled and two are rows the live substrate already has in the queue,
    # which is corroboration rather than exposure. It fires on ZERO of the 16
    # audit-labelled REAL items, and REAL-items-wrongly-gated is unchanged at
    # 3 — FLOOR3 v2's standard, which is the number that decides whether a
    # hardening round ships at all.
    #
    # SECOND PERSON IS LOAD-BEARING. `you don't mind` is a request; `I don't
    # mind` is an ACCEPTANCE, and the broader spelling gated it — one of the
    # ways this family over-tightens. Both spellings score identically on the
    # live corpus, so the narrower one costs nothing and cannot make that
    # mistake on a corpus we have not seen. `would you mind` needs no member of
    # its own: `\b(?:can|could|would|will)\s+you\b` already carries it.
    | \byou\s+(?:don'?t|wouldn'?t)\s+mind\b
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


def fusion_status(data: dict, transcript_text) -> str:
    """THE cross-meeting fusion guardrail, in code (A3), reporting all THREE of
    its outcomes: `FUSION_VERIFIED` / `FUSION_REFUSED` / `FUSION_INERT`.

    `orchestrator-past-meetings.md` has REQUIRED this since v2.14.19 — "a 5+
    word substring of data.title / data.evidence actually appears in the
    transcript text of the meeting it's being attributed to" — as prose, with
    no code and no test. The audit's sample seq 6100 carried an evidence string
    that appears nowhere in its cited transcript, which is exactly what the
    guardrail exists to make impossible.

    SKIP-NOT-FAIL is unchanged as a ROUTING rule: only `FUSION_REFUSED` moves a
    lane, because the check refuses only what it can positively establish is
    absent. What changes is that the two ways of not-refusing are no longer the
    same answer. Pure — the caller supplies the transcript it already loaded
    (never re-fetch)."""
    haystack = _normalize_for_fusion(transcript_text)
    if not haystack:
        # No transcript reached the capture. Nothing was checked, and the row
        # has to say so — this is the exact shape behind FLOOR3_REPLAY §3.
        return FUSION_INERT
    data = data or {}
    for field in ("evidence", "title"):
        needle = _normalize_for_fusion(data.get(field))
        words = needle.split()
        if len(words) < FUSION_MIN_WORDS:
            continue
        if needle in haystack:
            return FUSION_VERIFIED
        for i in range(len(words) - FUSION_MIN_WORDS + 1):
            if " ".join(words[i:i + FUSION_MIN_WORDS]) in haystack:
                return FUSION_VERIFIED
        return FUSION_REFUSED
    # A transcript was supplied and neither field carries enough words to look
    # for. Structurally un-anchorable: no transcript will ever verify this row,
    # and every transcript-reading floor check is inert on it for the same
    # reason (`_evidence_anchor` has nothing to anchor).
    return FUSION_INERT


def fusion_refusal_reason(data: dict, transcript_text) -> str:
    """The routing face of the guardrail: "" when the capture is provably
    grounded in the transcript it cites OR when the check could not run; the
    refusal reason otherwise.

    DERIVED from `fusion_status` rather than re-deciding — one predicate, two
    readers. A second copy of the walk is how the two faces drift apart."""
    return (FUSION_REVIEW_REASON
            if fusion_status(data, transcript_text) == FUSION_REFUSED else "")


# =============================================================================
# FLOOR2 — the two junk modes only the MEETING can show (2026-08-06).
# =============================================================================
#
# The five conditions in `capture_floor_reason` read ONE sentence: the title and
# whatever evidence the extractor saved beside it. The V1 interim re-measure
# (17 transcript-verified captures) found the confirmed lane still ~18% junk,
# and BOTH misses were shapes a sentence cannot show:
#
#   J-1  a live screen-share walkthrough: the speaker talks the other side
#        through a one-time setup step and tells them to click it right now —
#        an action done ON the call. Owner, deliverable and consequence all
#        present, so every existing condition passes. What is absent is a
#        deliverable that OUTLIVES the meeting. (Paraphrased: the real
#        utterance is a customer's own words, and this file ships to every
#        client repo. The fixture in `tests/run_floor2_test.py` carries the
#        SHAPE, which is what the checks are written against.)
#   J-2  "I should be able to do <name>'s and get that information on our one
#        on one tomorrow" — an offer the same conversation then redirected
#        ("you don't need his instance to achieve this"). The extractor scores
#        the sentence in isolation; nothing re-read the REST of the transcript.
#
# So these two checks are a second floor LAYER, and the layer is defined by
# what it reads: the transcript, not the sentence. They run only after the
# sentence-level floor has cleared (an item already below the floor has its
# reason; a second one adds nothing) AND after the FUSION check has cleared
# (F-2: reading the transcript is only meaningful once the evidence is known to
# BE transcript), and their verdict routes exactly like the first layer's —
# REVIEW, never a drop (Option A standing).
#
# CONSERVATIVE BY CONSTRUCTION, because the reviewed floor's own precision is
# ~50% and a noisy addition costs more than it saves:
#   * Every check is INERT unless it can positively establish its condition —
#     no transcript, no locatable evidence span, no cue: BOOK.
#   * J-1 refuses to fire on anything with a post-meeting surface. A due date,
#     a money amount, or a send/share/follow-up verb means the deliverable
#     survives the call by construction, whatever "right now" appears beside it.
#   * J-2 needs BOTH halves of "the same conversation took THIS back": a
#     retraction cue in the transcript after the evidence span AND inside the
#     same stretch of conversation, AND a lexical tie to what was being
#     discussed. Proximity alone was tried and rejected during the build — on a
#     dense call every clean promise sits within a minute of somebody saying
#     "never mind" about something else, and a check that gates four real
#     promises to catch one junk row is the over-tightening V2 exists to stop.
#
# TRAP CLOSED (spec §5): the re-scan normalizes through `_FUSION_WORD_RE` — the
# SAME class the fusion check uses, and for the same reason. An asymmetric
# normalizer across these two sources is what the CAPTUREFLOW MF-1 apostrophe
# pin closed: a transcript writes `don’t` with a curly apostrophe, an evidence
# string writes `don't` straight, and a class that keeps the apostrophe makes
# them different words. Everything below reads the normalized token stream, so
# every pattern here is written in ITS vocabulary: contractions are already
# split (`don t`, `let s`, `we ll`), and there is no punctuation at all.

# How far after the evidence span a supersession still reads as "the same
# stretch of conversation". ~120 words is under a minute of speech. Builder
# constants, tunable, pinned by test at both ends.
SUPERSEDE_WINDOW_WORDS = 120
# Shared content tokens required between the retraction and the topic. TWO,
# not one: a single shared word is the coincidence rate of ordinary English.
SUPERSEDE_MIN_SHARED = 2
# How much transcript BEFORE the evidence span counts as the item's topic. The
# extracted evidence is one sentence; what the retraction answers is the
# exchange that set it up, and the words it re-uses live there.
SUPERSEDE_CONTEXT_WORDS = 40
# How much verbatim transcript rides along as the superseding quote, and as the
# retraction's own local window for the lexical test.
SUPERSEDE_QUOTE_WORDS = 16
# RETIRED by FLOOR3 v2. This was the window for FLOOR2's completion-ack leg,
# which read the tail only after an evidence-side cue had already fired. v2
# reads the tail directly and carries its own, tighter window
# (`DISCHARGE_WINDOW_WORDS`), measured rather than assumed.

# An action taken ON the call, in the words people use for it.
#
# DEICTIC ONLY (F-1, review 2026-08-06). This class was first written wide —
# it also carried a bare `right now`, `real quick`, `go ahead and`, and a bare
# `while we're on/at it` — and the review measured what that cost: 7 of 7
# authored clean promises in the cue-without-veto quadrant gated ("I'll go
# ahead and update the contract terms", "I'll start building the report right
# now", "let me fix the rounding bug real quick", "while we're on it I'll
# document the escalation path"), and on the live corpus 1 of 475 booked rows
# gated falsely ("I couldn't get the ad context button right now").
#
# The cut those numbers argue for: a bare temporal adverb is not an in-call
# marker. "go ahead and" / "real quick" / a free-standing "right now" are what
# people say about work they are about to START, and the thing that outlives
# the call is the work, not the adverb. What actually marks an action
# PERFORMED on the call is DEIXIS — a referent that only exists in the room
# ("click THAT now", "right here", "while we're on the call"). So only the
# deictic forms survive; the fillers are gone. The J-1 signature is unaffected
# (it is deictic: "click that right now"), and the one measured live false
# gate stops firing.
_IN_MEETING_NOW_RE = re.compile(
    r"""(?ix)
      \bright\s+here\b
    | \bright\s+there\b
    | \bas\s+we\s+speak\b
    | \bwhile\s+(?:we|you|i)\s+(?:re|are|am)\s+
      (?:on\s+(?:the\s+)?(?:call|phone|line|zoom)
        | here
        | in\s+(?:the\s+)?(?:call|meeting))\b
    | \b(?:do|click|hit|press|tap|type|enter|check)\s+(?:that|this|it)\s+
      (?:right\s+)?now\b
    | \blet\s+s\s+(?:just\s+)?do\s+(?:that|this|it)\s+(?:right\s+)?now\b
    """
)

# The veto. Any of these and the deliverable OUTLIVES the meeting, so J-1 has
# nothing to say — "I'll send the deck right now" is a real promise with a real
# artifact, not a walkthrough step.
_POST_MEETING_SURFACE_RE = re.compile(
    r"""(?ix)
      \b(?:send|sends|sending|email|emails|emailing|share|shares|sharing|
           circulate|circulates|deliver|delivers|forward|forwards|
           draft|drafts|drafting|write\s+up|writes\s+up|
           put\s+together|pull\s+together|follow\s+up|follows\s+up|
           get\s+back\s+to|loop\s+in|schedule|schedules|book|books|
           invite|invites|introduce|introduces)\b
    | \bafter\s+(?:the|this|our)\s+(?:call|meeting)\b
    | \b(?:later\s+today|tonight|tomorrow|next\s+week|this\s+week|
           by\s+(?:eod|eow|cob))\b
    | \bby\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b
    """
)

# Someone saying, later in the transcript, that the thing just happened.
# EXPLICIT COMPLETIONS ONLY. "there we go" / "that worked" / "perfect" were
# drafted here and cut: they are said about anything, they carry no topic words
# to tie them to the item, and this leg's only tie is proximity — a weak cue
# plus a weak tie is a guess, and a guess routes to BOOK.
_COMPLETION_ACK_RE = re.compile(
    r"""(?ix)
      \b(?:it|that)\s+s\s+done\b
    | \b(?:ok|okay|alright)\s+(?:it\s+s\s+|that\s+s\s+)?done\b
    | \bdone\s+and\s+done\b
    | \b(?:i|we)\s+(?:just\s+)?(?:did|clicked|hit|entered|typed|verified)\s+
      (?:it|that)\b
    | \b(?:got|all)\s+(?:it|that)\s+done\b
    | \b(?:it|that)\s+s\s+(?:verified|confirmed|set\s+up)\s+now\b
    """
)

# =============================================================================
# FLOOR3 — the J-1 DEMO shape (2026-08-13).
# =============================================================================
#
# The V1 FULL re-measure (2026-08-12, `_hq/audit-reports/V1_REMEASURE_2026-08-12
# .md`) FAILED both targets — confirmed-lane junk 6/15 = 40%, overall 9/25 =
# 36% — and 8 of the 9 junk items were J-1, the class FLOOR2 shipped a check
# for. The check caught none of them, and the reason is structural rather than
# a vocabulary gap:
#
#   `_POST_MEETING_SURFACE_RE` ran as an UNCONDITIONAL veto over `title +
#   evidence` BEFORE either positive signal. It fires on the mere presence of a
#   send / share / email / invite verb, on the premise that such a verb means
#   the deliverable outlives the call. In the DEMO / ONBOARDING register — where
#   the whole live week's junk lives — that premise is exactly inverted: the
#   artifact is produced in the room while the other side watches, and "let me
#   send you that here" is a completed act, not a promise.
#
# The proof is one sample item: its evidence was "send that over to me", its
# transcript said "Okay. That's done." a few turns later, and `_COMPLETION_ACK_
# RE` ALREADY MATCHED that acknowledgment. The veto threw the evidence away
# before the ack leg ever ran. The defect is the ORDERING, not the classes.
#
# So the veto splits by what it can actually establish:
#
#   * A future TIME marker ("tomorrow", "by Friday", "after the call") is
#     direct evidence the deliverable outlives the meeting. It stays absolute —
#     `_FUTURE_TIME_RE`, checked first, and nothing below overrides it (nor the
#     due-date / money rail, which is older and equally absolute).
#   * A delivery VERB is not. It is a guess, and demo junk is made of delivery
#     verbs with no times attached. `_POST_MEETING_SURFACE_RE` therefore keeps
#     its force only over the ONE-LEGGED FLOOR2 deictic signal, which is where
#     a lexical counter-cue is a fair tiebreak. Positive, TIED evidence that an
#     act was discharged in the room now outranks it.
#
# Everything added here reports the SAME verdict, `FLOOR_DONE_IN_MEETING`.
# These are three ways to observe one thing; a fourth enum member would tell a
# reader nothing and would cost the C1 receipt pin an exception.
#
# All patterns below are written in the NORMALIZED FUSION VOCABULARY, like
# every FLOOR2 class: contractions are already split (`that s going out`,
# `i m sending`) and there is no punctuation. A pattern written with an
# apostrophe in it matches nothing, silently.

# The temporal half of `_POST_MEETING_SURFACE_RE`, and the only half that
# survives as an absolute veto. An explicit future time is not a guess about
# whether the deliverable outlives the call — it is the speaker saying so.
_FUTURE_TIME_RE = re.compile(
    r"""(?ix)
      \bafter\s+(?:the|this|our)\s+(?:call|meeting|session|demo)\b
    | \b(?:later\s+today|later\s+this|tonight|tomorrow|
           this\s+(?:afternoon|evening|week|month|quarter)|
           in\s+the\s+morning|
           next\s+(?:week|month|quarter|year)|
           by\s+(?:eod|eow|eom|cob))\b
    | \b(?:by|on|next|this|come)\s+
      (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b
    | \b(?:end\s+of\s+(?:the\s+)?(?:day|week|month|quarter)|
           over\s+the\s+weekend|
           in\s+(?:a\s+(?:couple|few)|two|three)\s+(?:days|weeks|hours))\b
    | \bby\s+the\s+(?:\d{1,2}(?:st|nd|rd|th)?|end)\b
    | \bbefore\s+(?:the\s+)?(?:board|next|end)\b
    """
)
# WIDENED IN FIX ROUND 1 (CONFIRM review F6). This class became the ONLY
# absolute temporal veto the moment the delivery-verb half was demoted, and it
# only knew six ways to say "later" — so "let me send you the deck here next
# Tuesday" and "I'm texting them this afternoon" were CONFIRMED gating. Every
# addition errs toward BOOK: a phrase that is ambiguous between past and future
# ("on Monday") vetoes, which is the safe direction for this check.

# D leg 1 — a delivery act framed as happening IN THE ROOM. Two families, one
# is enough, and both need the deictic frame: it is the frame and not the verb
# that says the artifact changed hands here.
#
# `over to you` / `over to me` are deliberately ABSENT. "I'm going to get these
# deliverables over to you" is the single most ordinary post-call promise in
# the sample (a confirmed REAL capture), and admitting that frame would gate
# it. The markers kept are ones that can only mean the room: `here` (guarded
# below), the chat, the screen, and a delivery verb bound to `right now`.
# =============================================================================
# FLOOR3 v2 — the cue is in the TRANSCRIPT, not the sentence (2026-08-14).
# =============================================================================
#
# v1 of this layer was replayed against 110 real captures before it shipped and
# caught ZERO of the 9 known junk items. The classes had been written against
# the V1 audit report's QUOTE COLUMN — quotes an auditor assembled while reading
# transcripts, which blend an item's evidence with the speech around it — and
# not against the `data.evidence` strings the substrate actually holds. The two
# are different text:
#
#   audit quote                   data.evidence
#   "…Sorry. That's going out."   "I'm gonna change center to be earlier. Yeah,
#                                  I think five is a good time."
#   "…I got you right now."       "130 is fine. 130 is fine. If you want to
#                                  send me a meeting request, that would be
#                                  great."
#
# An entire signal was built on "that's going out". That phrase appears in NO
# evidence string in the population. Measured leg-1 hit rate across 110 real
# items: 3, and all 3 failed the second leg.
#
# THE CORRECTION IS A RELOCATION, NOT A REWRITE. Every phrase those classes
# looked for is real and is in the record — it sits in the TAIL, the transcript
# that FOLLOWS the item's evidence span. That is where the auditor was reading
# when they wrote the quotes. So the cue class moves to the tail, and the
# evidence-side classes are deleted rather than re-tuned:
#
#   seq 7933  tail: "sorry that's going out, so we're gonna get that done"
#   seq 8334  tail: "…or just copy paste, and I actually have Slack open"
#   seq 8348  tail: "I got you right now"
#   seq 8345  tail: "she just did, she just did"
#   seq 8601  tail: "send, yes please"
#
# MEASURED on the same 110 captures: 6 of 9 junk items gated, 1 non-junk row
# touched — and that row's own tail says the work was done, so it may not be a
# miss at all. With W below, 7 of 9. This class is not a guess about how people
# talk; it is a transcription of how these people did talk, checked against
# every capture they did not.
#
# Note what the relocation makes SAFE. `I have <thing> open` was cut from v1
# during a fix round because, read from the item's own evidence, it swallowed
# "I have to follow up" — the most ordinary promise shape in English. Read from
# the NEIGHBOURING transcript it is a different claim: not "this promise sounds
# live" but "the room did it". Several members are back for exactly that
# reason, and they are safe here for exactly that reason.

# How far past the evidence a discharge cue still reads as being about THIS
# item. 30 tokens is roughly a couple of turns. Measured at 20/30/45/60: the
# junk yield saturates at 30 (3 -> 6 items) and the false-positive count does
# not move until 60.
DISCHARGE_WINDOW_WORDS = 30

_IN_ROOM_DISCHARGE_RE = re.compile(
    r"""(?ix)
      # the act reported in flight
      \b(?:that|it|this)\s+s\s+going\s+out\b
    | \b(?:that|it|this)\s+is\s+going\s+out\b
    | \b(?:i|we)\s+just\s+sent\s+(?:it|that|you)\b
      # instant provision
    | \bi\s+got\s+you\s+(?:right\s+now|on\s+(?:that|this|it))\b
      # the speaker's own hands on it, in the room
    | \bi\s+(?:actually\s+)?have\s+\w+\s+open\b
      # someone reporting it done — including about a third party, which the
      # evidence-side classes could never see ("she just did")
    | \b(?:he|she|they)\s+just\s+did\b
    | \b(?:i|we)\s+just\s+did\s+(?:it|that)\b
    | \b(?:it|that)\s+s\s+done\b
    | \b(?:ok|okay|alright)\s+(?:it\s+s\s+|that\s+s\s+)?done\b
    | \b(?:it|that)\s+came\s+through\b
      # consent to a hand-over happening NOW, bound to a delivery verb. A bare
      # "yeah please" is ordinary politeness and it produced the only clear
      # false positive in the measurement ("but yeah please let me know").
    | \bsend\s+(?:it\s+|that\s+)?(?:yes|yeah|yep)\s+please\b
    | \b(?:yes|yeah|yep)\s+please\s+send\b
    """
)

# The demo register, for W. TWO families of screen-share language, because
# there are two ways people run one and v1 modelled only the first:
#
#   GUIDED    the other side drives and the presenter gives UI instructions —
#             "click the", "scroll down", "top right".
#   NARRATED  the presenter drives and describes — "share my screen", "just to
#             show you", "watch this", "take a look at".
#
# v1 shipped the guided set alone. The real demos in this corpus are narrated,
# so `demo_register` was FALSE on the one true demo meeting in the population
# and W never fired on the item it was written for. Measured with both sets:
# that meeting scores 6 distinct families and no other meeting in the window
# scores above 2 — the threshold separates them by a wide margin rather than a
# hair.
_DEMO_CUE_RES = tuple(re.compile(p, re.I | re.X) for p in (
    # guided
    r"\b(?:click|tap)\s+(?:on\s+)?(?:the|that|this|it)\b",
    r"\bgo\s+to\s+the\s+\w+\s+(?:tab|page|screen|menu)\b",
    r"\byou\s+ll\s+see\b | \byou\s+can\s+see\s+(?:it|that|the)\b",
    r"\bhit\s+(?:save|enter|submit|send|refresh)\b",
    r"\b(?:top|bottom)\s+(?:right|left)\b",
    r"\bscroll\s+(?:down|up)\b",
    r"\bover\s+on\s+the\s+(?:left|right)\b",
    r"\bpull\s+up\s+the\s+\w+\b",
    r"\btype\s+in\s+(?:the|your)\b",
    r"\bright\s+(?:there|here)\s+(?:on|in)\s+the\b",
    # narrated
    r"\b(?:share|sharing)\s+my\s+screen\b",
    r"\b(?:let\s+me|i\s+m\s+going\s+to|i\s+ll)\s+(?:just\s+)?show\s+you\b",
    r"\bdemonstrate\b",
    r"\bwatch\s+(?:this|what)\b",
    r"\b(?:take|taking)\s+a\s+look\s+at\b",
    r"\byou\s+ll\s+notice\b",
    r"\bunder\s+the\s+hood\b",
    r"\bwalk\s+you\s+through\b",
))
# How many DISTINCT cue FAMILIES make a transcript a walkthrough. Three, not
# one: a single "click that" happens in ordinary calls, and W is the highest
# false-gate risk in this build so it carries the tightest leg.
DEMO_REGISTER_MIN_CUES = 3

# W leg 2 — an instruction aimed at the PRODUCT, not at a person. In a demo
# these are prompts fired at the tool on screen and they ran during the call;
# captured as commitments they are pure junk. Two things carry the weight and
# both are required: a live-prompt filler (`just` / `go ahead and`), which is
# how people talk TO a tool and not to a colleague, and a DEICTIC object
# (`this` / `that`), which names the thing on screen. `the` is not admitted —
# "the report" is a report anywhere.
_PRODUCT_IMPERATIVE_RE = re.compile(
    r"""(?ix)
      \b(?:can|could)\s+you\s+(?:just\s+)?go\s+ahead\s+and\s+
        (?:update|change|add|remove|delete|pull|run|generate|regenerate|
           make|show|give|write|rewrite|redo|fix|refresh|rerun|re\s+run)\s+
        (?:me\s+|us\s+)?(?:this|that)\b
    | \b(?:can|could)\s+you\s+just\s+
        (?:update|regenerate|rerun|re\s+run|refresh|redo|pull|run)\s+
        (?:me\s+|us\s+)?(?:this|that)\b
    """
)


# The counterparty redirecting the plan, or the owner replacing the
# deliverable — a retraction of the thing just offered.
_SUPERSEDE_CUE_RE = re.compile(
    r"""(?ix)
      \b(?:you|we|i|they)\s+don\s+t\s+(?:need|have\s+to)\b
    | \b(?:you|we|i|they)\s+re\s+not\s+going\s+to\s+need\b
    | \bno\s+need\s+(?:to|for)\b
    | \bnever\s+mind\b
    | \bnevermind\b
    | \bscratch\s+that\b
    | \bforget\s+(?:that|it|about\s+that)\b
    | \bdisregard\s+(?:that|it)\b
    | \bhold\s+off\s+on\b
    | \binstead\s+(?:let\s+s|we\s+ll|i\s+ll|we\s+can|of\s+that)\b
    | \b(?:let\s+s|we\s+ll|i\s+ll|we\s+can|let\s+me)\s+\w+
      (?:\s+\w+){0,5}\s+instead\b
    | \bactually\s+(?:let\s+s|we\s+ll|i\s+ll|we\s+can|don\s+t|hold\s+off)\b
    | \b(?:that|it)\s+s\s+not\s+(?:necessary|needed)\b
    | \b(?:that|it)\s+won\s+t\s+be\s+necessary\b
    | \b(?:skip|drop)\s+(?:that|it)\s+(?:for\s+now|then)\b
    """
)

# =============================================================================
# EXCH1 — reason assignment adjudicates the EXCHANGE, not the line (2026-08-24).
# =============================================================================
#
# The first operator walk of the held-review reading chair pulled four
# below-floor rows against their originating transcripts and found three whose
# stated reason is factually false, all one shape: the verdict was computed
# from the utterance that TRIGGERED the capture, and nothing read the turns
# that follow it. A commitment does not live in a line — it lives in an
# exchange: offer, then acceptance, then sometimes a revision of scope.
#
#   actual exchange                     what the classifier saw   its label
#   offer -> acceptance next turn       the offer                 "never accepted"
#   request -> "yeah" next turn         the request               "never accepted"
#   offer -> scope narrowed, still on   the narrowing             "taken back"
#
# SIBLING OF FLOOR3 v2, NOT A DUPLICATE. v2 established the doctrine — the cue
# is in the transcript, not the sentence — and applied it to what the FLOOR
# reads (the J-1 discharge check moved to the tail). REASON ASSIGNMENT still
# read one line: NOT_ACCEPTED is a pure function of the evidence string, and
# the supersession check read the tail only to find a retraction cue, never to
# notice the same speaker re-committing right after it. This layer carries the
# v2 doctrine into the two verdicts that adjudicate agreement.
#
# WHERE THE WINDOW ENDS. Acceptance is answered where conversation answers —
# in the reply. The three confirmed failures all carry the acceptance in the
# immediately following turn, so the window is DISCHARGE_WINDOW_WORDS' scale
# (30 tokens ~ a couple of turns, the bound FLOOR3 v2 measured at 20/30/45/60
# and found saturated at 30), with one refinement: a BARE affirmative ("yeah",
# "okay") counts only inside the first ACCEPTANCE_IMMEDIATE_WORDS tokens —
# the reply turn itself — because a bare "yeah" thirty tokens later is about
# whatever is being discussed by then. A STRONG acceptance ("that's perfect",
# "sounds good, send it over") carries its own tie and reads anywhere in the
# window. Nothing reads further: an acceptance three minutes later answers a
# different exchange, and the cost of missing it is a row that stays VISIBLE
# in the review queue — the safe direction by the 2026-08-01 ruling.
ACCEPTANCE_WINDOW_WORDS = 30
# The reply turn: where a bare affirmative is still an answer to THIS offer.
# ~12 tokens covers a speaker label plus a short sentence.
ACCEPTANCE_IMMEDIATE_WORDS = 12
# How far past a retraction cue a re-commitment still reads as the same
# breath. MEASURED, not authored: on the confirmed narrowing case the
# speaker's definitive re-commitment ("I'll send you a kind of overall
# model and then kind of a recap…") lands 44 tokens after the cue — the
# revision is a speaker talking through what they WILL do instead, which is
# a few sentences, not one. 60 covers it with headroom and is still half of
# SUPERSEDE_WINDOW_WORDS, so a "re-commitment" can never be further from
# its cue than the cue is allowed to be from the offer.
RECOMMIT_WINDOW_WORDS = 60

# Acceptance that carries its own weight — phrases that only mean "yes, do
# that". Fusion vocabulary: contractions pre-split, no punctuation.
_ACCEPTANCE_STRONG_RE = re.compile(
    r"""(?ix)
      \b(?:that|it)\s+s\s+perfect\b
    | \bsounds\s+(?:good|great|perfect|like\s+a\s+plan)\b
    | \bthat\s+(?:works|would\s+work)\b
    | \bworks\s+for\s+(?:me|us)\b
    | \bthat\s+would\s+be\s+(?:great|perfect|awesome|helpful|amazing)\b
    | \blet\s+s\s+do\s+(?:it|that)\b
    | \bwill\s+do\b
    | \bhappy\s+to\b
    | \byes\s+please\b
    | \bplease\s+do\b
    | \bgo\s+for\s+it\b
    | \b(?:i|we)\s+can\s+do\s+that\b
      # REVIEW F-1 R-1: the intensifiers guard against the NEGATION CLASS,
      # not the literal word — "do not", "no way", "never", and the split
      # contractions all flip the polarity. "no problem"/"no worries" are
      # carved back out: those are acceptances wearing a negative (the same
      # ruling the decline vocabulary records).
    | \bfor\s+sure\b
        (?!\s+(?:not|never|do\s+not|don\s+t|won\s+t|can\s+t
               |cannot|will\s+not
               |no(?!\s+(?:problem|worries)\b))\b)
    | \babsolutely\b
        (?!\s+(?:not|never|do\s+not|don\s+t|won\s+t|can\s+t
               |cannot|will\s+not
               |no(?!\s+(?:problem|worries)\b))\b)
    | \bdefinitely\b
        (?!\s+(?:not|never|do\s+not|don\s+t|won\s+t|can\s+t
               |cannot|will\s+not
               |no(?!\s+(?:problem|worries)\b))\b)
    | \bsweet\b
      # REVIEW F-1 R-1: the handshake "deal" must not fire when the next word
      # says the deal COLLAPSED. "is/'s dead|off" stays out of this list on
      # purpose — that shape is the tie rule's live witness (decline twin
      # anchored at the same token), and both layers are pinned by removal.
    | \bdeal\b(?!\s+(?:fell|collapsed|died|went)\b)
    """
)

# The bare affirmative — an answer only where the token stream says a reply
# could be. Both live shapes were found by replaying the real substrate:
#
#   REPLY     after a turn marker ("Them: Yeah."). The backend stamps turns
#             as `Me:` / `Them:`, which normalize to bare `me` / `them`
#             tokens, so "marker then affirmative" is a reply.
#   MERGED    at the immediate head of the tail with speech continuing
#             through it ("…when you have it? Yeah. Because my next step…").
#             This backend routinely folds a counterparty's short interjection
#             into the current speaker's turn (its attribution is documented
#             as unreliable), so a head-position affirmative is ambiguous
#             between a mis-attributed reply and the speaker's own discourse
#             glue. It counts, because the two costs are not symmetric: the
#             glue reading leaves a live commitment silently hidden, the
#             reply reading leaves a dead row visible in review.
#   …but NOT  turn-FINAL: an affirmative the floor changes hands right after
#             ("…or something like that too. Yeah.  Them: …") is the offering
#             speaker's own trailing filler, and the replay caught exactly
#             that shape rescuing a row it should not have.
#
# Label-free transcripts have no markers, so only the head-position leg and
# strong acceptances can ever fire there — the conservative direction.
_ACCEPTANCE_BARE_RE = re.compile(
    r"\b(?:yeah|yes|yep|yup|sure|ok|okay|perfect)\b"
)
_TURN_MARKER_TOKENS = frozenset(("me", "them"))
# How many tokens past a head-position affirmative to look for the floor
# changing hands (the turn-final shape above): the marker lands within a
# token or two of the filler in every observed instance.
_TURN_FINAL_LOOKAHEAD = 2

# REVIEW F-1c — the bare leg learns polarity. "Yeah, no." is the canonical
# colloquial refusal: the affirmative is discourse glue and the NEGATION
# right behind it is the answer. A bare candidate is skipped when a negation
# head lands within _BARE_NEG_LOOKAHEAD tokens of it — unless the negation
# is itself softened into an acceptance ("no problem", "not a problem",
# "no worries"), the same carve-out the strong leg records. The lookahead
# never crosses a turn marker: the floor changing hands ends the answer,
# and the next speaker's "no" is about their own sentence.
_BARE_NEG_TOKENS = frozenset(("no", "not", "nope", "never", "cannot"))
_BARE_NEG_SPLIT_HEADS = frozenset(("won", "can", "don"))
_BARE_NEG_SOFTENERS = frozenset(("problem", "worries", "issue"))
_BARE_NEG_LOOKAHEAD = 4


def _bare_polarity_flipped(window: list, pos: int) -> bool:
    """Does a negation inside the lookahead flip this bare affirmative?"""
    look = window[pos + 1: pos + 1 + _BARE_NEG_LOOKAHEAD]
    for i, tok in enumerate(look):
        if tok in _TURN_MARKER_TOKENS:
            break
        neg = tok in _BARE_NEG_TOKENS or (
            tok in _BARE_NEG_SPLIT_HEADS
            and i + 1 < len(look) and look[i + 1] == "t")
        if not neg:
            continue
        softened = any(t in _BARE_NEG_SOFTENERS
                       for t in look[i + 1: i + 3])
        if not softened:
            return True
    return False

# The reply DECLINING the offer. A decline before any acceptance leaves the
# verdict standing. Deliberately absent: "no worries" / "no problem" — those
# are acceptances wearing a negative.
_ACCEPTANCE_DECLINE_RE = re.compile(
    r"""(?ix)
      \bdon\s+t\s+(?:worry|bother)\b
    | \bno\s+need\b
    | \byou\s+don\s+t\s+have\s+to\b
    | \bnah\b
    | \bno\s+(?:that|it)\s+s\s+(?:ok|okay|fine|alright)\b
    | \bwe\s+re\s+(?:good|fine|all\s+set)\b
    | \bi\s+m\s+good\b
    | \blet\s+s\s+not\b
    | \bmaybe\s+later\b
    | \bnot\s+(?:yet|now|necessary)\b
    | \b(?:absolutely|definitely|for\s+sure)\s+(?:not|never|no\s+way)\b
    | \bno\s+deal\b
    | \bdeal\s+(?:is|s)\s+(?:dead|off)\b
    | \bdeal\s+(?:fell\s+through|collapsed)\b
    """
)

# First-person future delivery — the shape of a speaker re-committing to the
# act. Verb-anchored: "I'll send" re-commits, "we'll skip that" does not.
_RECOMMIT_RE = re.compile(
    r"""(?ix)
      \b(?:i|we)\s+(?:ll|will|can)\s+(?:just\s+)?
        (?:send|give|get|put|pull|share|shoot|draft|write|make|do|have|
           circulate|deliver|forward|schedule|book|email|text)\b
    | \b(?:i\s+m|we\s+re)\s+(?:gonna|going\s+to)\s+(?:just\s+)?
        (?:send|give|get|put|pull|share|shoot|draft|write|make|do|have|
           circulate|deliver|forward|schedule|book|email|text)\b
    | \blet\s+me\s+(?:send|give|get|put|pull|draft|write|share|shoot)\b
    """
)


def _fusion_token_spans(text) -> list:
    """`[(token, start, end, source_text)]` over the FUSION token class, with
    offsets back into the source string so a match can be quoted VERBATIM.

    `.lower()` is length-preserving for every character this class can match;
    the guard below degrades to the lowered text on the rare locale-dependent
    expansion rather than slicing at a shifted offset."""
    raw = str(text or "")
    low = raw.lower()
    if len(low) != len(raw):  # pragma: no cover — locale-dependent expansion
        raw = low
    return [(m.group(0), m.start(), m.end(), raw)
            for m in _FUSION_WORD_RE.finditer(low)]


def _locate_span(hay_tokens: list, needle: str):
    """Where `needle` sits in the transcript's token stream, by the SAME anchor
    rule the fusion check uses: the whole phrase, else its first
    `FUSION_MIN_WORDS`-gram. Returns `(start, end)` token indices — `end` is
    advanced by the needle's FULL length from the anchor, so the "remainder"
    never re-reads the tail of the item's own evidence. None when the phrase is
    too short to anchor or is not there at all (check inert — skip-not-fail)."""
    words = _normalize_for_fusion(needle).split()
    if len(words) < FUSION_MIN_WORDS:
        return None
    n = len(hay_tokens)
    for gram_len in (len(words), FUSION_MIN_WORDS):
        gram = words[:gram_len]
        for i in range(0, n - gram_len + 1):
            if hay_tokens[i:i + gram_len] == gram:
                return (i, min(n, i + len(words)))
    return None


def _content_set(text) -> set:
    """Stopword-stripped content tokens, in the fusion vocabulary."""
    return {t for t in _normalize_for_fusion(text).split()
            if len(t) > 1 and t not in _FLOOR_STOPWORDS}


def _evidence_anchor(data: dict, spans: list):
    """The item's own words located in the transcript — evidence first, title
    second (the fusion check's field order). None = nothing to anchor on."""
    hay = [s[0] for s in spans]
    for field in ("evidence", "title"):
        at = _locate_span(hay, (data or {}).get(field))
        if at:
            return at
    return None


def demo_register(transcript_text=None) -> bool:
    """FLOOR3 W leg 1 — is this transcript a screen-share walkthrough?

    True when at least `DEMO_REGISTER_MIN_CUES` distinct cue FAMILIES appear.
    Families, not matches: a presenter who says "click the" nine times about
    nine buttons is not more of a walkthrough than one who says it once, and
    counting matched strings made a single sentence enough (fix round 1, F5).
    Pure; a property of the meeting, computed from the fetched transcript."""
    text = _normalize_for_fusion(transcript_text)
    if not text:
        return False
    hits = 0
    for cue in _DEMO_CUE_RES:
        if cue.search(text):
            hits += 1
            if hits >= DEMO_REGISTER_MIN_CUES:
                return True
    return False


def _tail_after_evidence(data: dict, transcript_text, window: int):
    """The `window` tokens of transcript that FOLLOW the item's evidence span,
    as one normalized string. "" when there is no transcript or the evidence
    cannot be anchored — every caller is then inert (skip-not-fail)."""
    spans = _fusion_token_spans(transcript_text)
    if not spans:
        return ""
    at = _evidence_anchor(data, spans)
    if not at:
        return ""
    return " ".join([s[0] for s in spans][at[1]:at[1] + window])


def done_in_meeting_reason(data: dict, transcript_text=None) -> str:
    """J-1 — the item was DISCHARGED inside the meeting. "" when it was not, or
    when the check cannot establish that it was.

    Signals, first hit wins. FLOOR2 shipped (a); FLOOR3 v1 added four
    evidence-side signals that a replay against 110 real captures measured at
    ZERO catches, and v2 replaced them with the one below (see the banner):

      0.  hard veto — a parseable due date, a money amount, or an explicit
          future TIME in the item's own words. ABSOLUTE: the speaker has said
          the deliverable outlives the meeting, so nothing after this runs.
      1.  IN-ROOM DISCHARGE. The transcript that FOLLOWS the item's evidence
          says the act happened — it went out, it came through, someone has
          the tool open, someone reports it done. This reads the room, not the
          sentence, which is the whole correction v2 makes.
      W.  demo-register imperative: the meeting is a walkthrough (guided or
          narrated) and the item is a prompt fired at the product on screen.
      --- delivery-verb veto (`_POST_MEETING_SURFACE_RE`) applies from here ---
      a.  deictic in-call instruction ("click that right now"). FLOOR2's, and
          still vetoed: it is one-legged and reads only the sentence, so a
          lexical counter-cue is a fair tiebreak against it.

    WHY THE DISCHARGE CHECK OUTRANKS THE DELIVERY-VERB VETO. The veto fires on
    any send / share / invite verb, on the premise that such a verb means the
    deliverable outlives the call. In the demo register that premise is
    inverted — the artifact is produced in the room — and the sample item that
    proves it reads "Send that over to me." with "I actually have Slack open"
    a few turns later. The veto is an inference; the tail is evidence. Evidence
    wins, and the veto keeps its force only over the one-legged (a).

    Pure — the caller supplies the transcript it already loaded."""
    data = data or {}
    title = str(data.get("title") or "")
    evidence = str(data.get("evidence") or "")

    try:
        from capture_gate import carries_due_or_money, parse_iso_date
    except ImportError:  # pragma: no cover — direct-path import
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capture_gate import carries_due_or_money, parse_iso_date
    blob = _normalize_for_fusion(f"{title} {evidence}")
    ev_norm = _normalize_for_fusion(evidence) if evidence.strip() else ""

    # 1 — the room says it happened, and this runs BEFORE the date rails.
    #
    # That ordering is the one doctrine change v2 makes, so here is the
    # argument. A due date and a future-time phrase are INFERENCES about what
    # will happen: they say a deliverable is expected later, from which the
    # rails conclude it cannot already be done. The tail is an OBSERVATION
    # about what did happen: someone in the room said it went out, came
    # through, or is done. An observation of the past outranks an inference
    # about the future — that is not a preference, it is what the two kinds of
    # statement are worth.
    #
    # The rails were also demonstrably reading the wrong thing on this corpus.
    # Both junk items they were protecting carry a date that describes the
    # SUBJECT rather than a deadline: "move tonight's dinner reservation to
    # 5pm" (due = the dinner) and "send the Wednesday 1:30 working-session
    # invite" (due = the session). Both acts were performed on the call.
    #
    # MEASURED before making the change, on 103 real anchorable captures:
    # putting this check above the rails gates 6 of 9 known junk items instead
    # of 4, gates ZERO audit-labelled REAL items either way, and touches one
    # unlabelled row whose own tail says the work was done. The rails keep
    # their full force over everything below.
    #
    # Inert without a transcript, and inert when the evidence cannot be
    # anchored in one (skip-not-fail). Note what the second case costs: a
    # capture written with paraphrased evidence can never be reached here,
    # however plainly the meeting discharged it — a measured 15% of the live
    # population, and a cap on this whole layer rather than a rounding error.
    # See the intake item on fusion running inert without leaving a trace.
    tail = _tail_after_evidence(data, transcript_text, DISCHARGE_WINDOW_WORDS)
    if tail and _IN_ROOM_DISCHARGE_RE.search(tail):
        return FLOOR_DONE_IN_MEETING

    if parse_iso_date(data.get("due")) or carries_due_or_money(data):
        return ""
    if _FUTURE_TIME_RE.search(blob):
        return ""

    # W — the walkthrough prompt. Four legs, all required. `owner_external`
    # vetoes it because a named outside person owing something is a real ask
    # however the meeting sounded, and the surface veto vetoes it because
    # "update this and send it to the team" produces something that leaves the
    # room.
    if (ev_norm
            and not str(data.get("owner_external") or "").strip()
            and not _POST_MEETING_SURFACE_RE.search(blob)
            and _PRODUCT_IMPERATIVE_RE.search(ev_norm)
            and demo_register(transcript_text)):
        return FLOOR_DONE_IN_MEETING

    if _POST_MEETING_SURFACE_RE.search(blob):
        return ""

    # (a) — FLOOR2's deictic instruction, unchanged and still vetoed above.
    if ev_norm and _IN_MEETING_NOW_RE.search(ev_norm):
        return FLOOR_DONE_IN_MEETING
    return ""


def accepted_in_exchange(data: dict, transcript_text=None) -> dict:
    """EXCH1 — did the turns FOLLOWING the trigger line accept the offer?
    Returns `{"accepted": bool, "quote": str}`; the quote is verbatim
    transcript, sliced by the matched tokens' own offsets.

    Runs only where a NOT_ACCEPTED verdict is about to stand, and only on the
    fetched transcript. Acceptance in a following turn beats absence of
    acceptance in the trigger line — the trigger line is an offer or a
    request, and offers are answered by the OTHER party, in the next turn,
    which is exactly the text the one-line verdict never read.

    Three cue classes, resolved by position:
      strong    a phrase that only means "yes, do that" — anywhere in the
                ACCEPTANCE_WINDOW_WORDS tail.
      bare      "yeah" / "okay" — only inside ACCEPTANCE_IMMEDIATE_WORDS,
                the reply turn, where it is still an answer to THIS line.
      decline   a refusal. A decline AT OR BEFORE the first acceptance wins:
                "no need — " followed by polite noise is not an acceptance,
                and "absolutely not" / "deal is dead" anchor a refusal at the
                cue word itself (REVIEW F-1 — the tie reads as the refusal).

    Inert (never rescues) when there is no transcript or the evidence cannot
    be anchored in it — the same skip-not-fail contract as every transcript
    check, and the same anchoring the fusion check uses, so evidence that
    fusion would refuse can never be "accepted" here (the F-2 dependency
    holds by construction rather than by ordering).

    Pure — the caller supplies the transcript it already loaded."""
    out = {"accepted": False, "quote": ""}
    spans = _fusion_token_spans(transcript_text)
    if not spans:
        return out
    at = _evidence_anchor(data or {}, spans)
    if not at:
        return out
    tail_from = at[1]
    window = [s[0] for s in spans][tail_from:tail_from
                                   + ACCEPTANCE_WINDOW_WORDS]
    if not window:
        return out
    joined = " ".join(window)

    strong = _ACCEPTANCE_STRONG_RE.search(joined)
    strong_idx = (len(joined[:strong.start()].split())
                  if strong is not None else None)
    # The bare leg walks tokens, not text: it needs "did a turn marker come
    # first", which is a property of token order.
    bare_idx = None
    marker_seen = False
    for pos, tok in enumerate(window[:ACCEPTANCE_IMMEDIATE_WORDS]):
        if tok in _TURN_MARKER_TOKENS:
            marker_seen = True
            continue
        if not _ACCEPTANCE_BARE_RE.fullmatch(tok):
            continue
        if _bare_polarity_flipped(window, pos):
            # REVIEW F-1c — "Yeah, no." The affirmative is glue and the
            # negation behind it is the answer. Keep walking: a later bare
            # affirmative may still be a clean one.
            continue
        if marker_seen:  # REPLY — a marker put the floor on the other side
            bare_idx = pos
            break
        if pos <= 1 and not any(
                t in _TURN_MARKER_TOKENS
                for t in window[pos + 1:pos + 1 + _TURN_FINAL_LOOKAHEAD]):
            # MERGED head-position affirmative, and not turn-final.
            bare_idx = pos
            break
    if strong_idx is None and bare_idx is None:
        return out
    idx = min(i for i in (strong_idx, bare_idx) if i is not None)
    decline = _ACCEPTANCE_DECLINE_RE.search(joined)
    # REVIEW F-1: <= not < — when the refusal phrase is HEADED by the cue word
    # itself ("absolutely not", "deal is dead") both REs anchor at the same
    # token, and a tie must read as the refusal it is.
    if decline is not None and len(joined[:decline.start()].split()) <= idx:
        return out
    first = spans[tail_from + idx]
    last = spans[min(len(spans) - 1, tail_from + idx + 5)]
    out["accepted"] = True
    out["quote"] = clip(first[3][first[1]:last[2]].strip())
    return out


def superseded_in_meeting(data: dict, transcript_text=None) -> dict:
    """FLOOR2 B (J-2) — the SAME conversation took the offer back. Returns
    `{"reason": "", "quote": ""}` when it did not (or when the check cannot
    run), else the floor reason and the VERBATIM superseding quote.

    Runs on the FETCHED transcript, never on a summary of it, and only on the
    REMAINDER after the item's own evidence span — a retraction that precedes
    the offer is not a retraction of it. THREE conditions, all required:

      near    the cue falls within `SUPERSEDE_WINDOW_WORDS` of the span (the
              same stretch of conversation), and
      about   its own words share `SUPERSEDE_MIN_SHARED` content tokens with
              the item's TOPIC — the title, the evidence, and the
              `SUPERSEDE_CONTEXT_WORDS` of transcript that led into it, because
              a retraction answers the exchange and not the one sentence an
              extractor kept, and
      final   (EXCH1) no topic-tied first-person re-commitment follows the cue
              within `RECOMMIT_WINDOW_WORDS`. A retraction requires the thing
              itself being called off — a speaker who says "I don't need to
              [go that deep] — I'll send you an overall model" has narrowed
              the SCOPE and re-committed to the ACT, and reading the narrowing
              as a retraction is the confirmed defect this leg closes. The
              tie is one shared content token between the re-commitment's own
              `SUPERSEDE_QUOTE_WORDS` stretch and the topic: the verb-anchored
              `_RECOMMIT_RE` supplies the commitment shape, and the shared
              token is what says it is the same act being delivered.

    Proximity alone gates every clean promise on a dense call (measured during
    the build: four of four); the lexical tie is what makes "never mind" about
    the parking validation stop being a verdict on the pricing sheet.

    Pure — the caller supplies the transcript it already loaded.

    Cross-meeting supersession is deliberately NOT in scope (that is
    decision-superseded territory); this reads one transcript only."""
    out = {"reason": "", "quote": ""}
    spans = _fusion_token_spans(transcript_text)
    if not spans:
        return out
    data = data or {}
    at = _evidence_anchor(data, spans)
    if not at:
        return out
    start = at[1]
    tokens = [s[0] for s in spans]
    remainder = tokens[start:]
    if not remainder:
        return out
    joined = " ".join(remainder)
    # Char offset -> token index over the joined remainder (single-space join).
    offsets = []
    pos = 0
    for tok in remainder:
        offsets.append(pos)
        pos += len(tok) + 1

    # The topic: what the item says, plus the run-up to it in the transcript.
    lead = tokens[max(0, at[0] - SUPERSEDE_CONTEXT_WORDS):at[1]]
    want = _content_set(
        f"{data.get('title') or ''} {data.get('evidence') or ''} "
        f"{' '.join(lead)}")
    import bisect as _bisect
    for m in _SUPERSEDE_CUE_RE.finditer(joined):
        idx = _bisect.bisect_right(offsets, m.start()) - 1
        if idx < 0 or idx >= SUPERSEDE_WINDOW_WORDS:
            continue  # not this stretch of conversation any more
        window = remainder[idx:idx + SUPERSEDE_QUOTE_WORDS]
        if len(want & {t for t in window
                       if len(t) > 1 and t not in _FLOOR_STOPWORDS}) \
                < SUPERSEDE_MIN_SHARED:
            continue  # a retraction of something else
        # EXCH1 `final` — the exchange's last word wins. A cue followed by the
        # speaker re-committing to the same act is a revision of scope, not a
        # retraction; a LATER cue still gets its own scan, so "I'll send it —
        # actually never mind" retracts and "never mind the deep dive — I'll
        # send the overall model" does not.
        follow = remainder[idx:idx + RECOMMIT_WINDOW_WORDS]
        fj = " ".join(follow)
        m2 = _RECOMMIT_RE.search(fj)
        if m2 is not None:
            # The tie reads the re-commitment's OBJECT — the words after the
            # matched verb phrase — never the phrase itself. "I'll send…"
            # shares "send" with half the topic set of every send-shaped
            # item, and a tie the verb can satisfy alone reads "never mind
            # X, separately I'll send Y" as X surviving.
            region = fj[m2.end():].split()[:SUPERSEDE_QUOTE_WORDS]
            if want & {t for t in region
                       if len(t) > 1 and t not in _FLOOR_STOPWORDS}:
                continue  # scope narrowed, still committed — not called off
        # The quote is sliced out of the SOURCE text by the matched tokens' own
        # offsets, so it is verbatim including its punctuation — never a
        # re-rendering of the normalized form.
        first = spans[start + idx]
        last = spans[min(len(spans) - 1,
                         start + idx + SUPERSEDE_QUOTE_WORDS - 1)]
        out["reason"] = FLOOR_SUPERSEDED_IN_MEETING
        out["quote"] = clip(first[3][first[1]:last[2]].strip())
        return out
    return out


def transcript_floor_reason(data: dict, transcript_text=None) -> dict:
    """The FLOOR2 layer as one call: `{"reason", "code", "quote"}`, all "" when
    the item clears it. J-1 is tested first — an action already performed is a
    stronger statement about the item than a later redirect."""
    reason = done_in_meeting_reason(data, transcript_text)
    if reason:
        return {"reason": reason, "code": floor_reason_code(reason),
                "quote": ""}
    hit = superseded_in_meeting(data, transcript_text)
    if hit["reason"]:
        return {"reason": hit["reason"],
                "code": floor_reason_code(hit["reason"]),
                "quote": hit["quote"]}
    return {"reason": "", "code": "", "quote": ""}


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
             "floor_reason": str, "floor_code": str, "fusion_reason": str,
             "fusion_status": str, "relevance_reason": str,
             "superseding_quote": str}.

    `floor_reason` is the sentence a human reads on the row; `floor_code` is the
    same verdict's stable name, and it is what a tally is keyed by. Both are ""
    when the item cleared the floor. `superseding_quote` is set only by the
    FLOOR2 supersession check, and it is verbatim transcript.

    `fusion_status` is one of `FUSION_VERIFIED` / `FUSION_REFUSED` /
    `FUSION_INERT`, and it is computed for EVERY verdict — including the ones
    that return before the fusion branch. A below-floor row whose evidence
    could never be anchored is exactly as unverified as a booked one; hiding
    that behind an early return would rebuild the blind spot one tier down."""
    data = _probe_data(item)

    # Computed once, up front, and carried on every return path. Pure, so
    # hoisting it above the floor changes no verdict — PRECEDENCE (floor, then
    # fusion, then relevance) is a property of the RETURNS below, not of where
    # this line sits.
    fusion_state = fusion_status(data, transcript_text)

    floor = capture_floor_reason(data)
    # EXCH1 — acceptance in a following turn beats absence of acceptance in
    # the trigger line. The NOT_ACCEPTED condition is the one floor verdict
    # that is a claim about the EXCHANGE ("never accepted") computed from one
    # line, so it alone gets the transcript's answer before it stands. The
    # check anchors the evidence the same way fusion does — unanchorable
    # evidence is inert, never rescued — and a rescued item still walks every
    # gate below: fusion can refuse it, the FLOOR2 layer can find it
    # discharged or genuinely retracted, and relevance still assigns its lane.
    if floor == FLOOR_NOT_ACCEPTED \
            and accepted_in_exchange(data, transcript_text)["accepted"]:
        floor = ""
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
                "floor_code": floor_reason_code(floor),
                "fusion_reason": "", "fusion_status": fusion_state,
                "relevance_reason": "",
                "superseding_quote": ""}

    fusion = (FUSION_REVIEW_REASON if fusion_state == FUSION_REFUSED else "")
    if fusion:
        return {"tier": TIER_REVIEW, "reason": fusion, "floor_reason": "",
                "floor_code": "",
                "fusion_reason": fusion, "fusion_status": fusion_state,
                "relevance_reason": "",
                "superseding_quote": ""}

    # FLOOR2 — the second floor layer, the one that reads the MEETING. It runs
    # only on items the sentence-level floor cleared (an item already below the
    # floor has its reason, and a second one tells a reader nothing new) AND
    # only after FUSION has cleared them (F-2, review 2026-08-06): this layer
    # reads the transcript on the assumption the item's evidence is genuine
    # transcript, and establishing exactly that is what the fusion check does.
    # Run first, it answered "already done during the call" for an item whose
    # evidence was nowhere in the call — masking the refusal and undercounting
    # fusion telemetry. Fusion refusal wins; the dependency now runs the way it
    # points.
    deeper = transcript_floor_reason(data, transcript_text)
    if deeper["reason"]:
        # Always REVIEW, never the observed tier — unlike the first layer,
        # which sends a plainly-someone-else's item to observed. These two
        # verdicts carry something a reader needs (and, for supersession, a
        # quote the observed writer has nowhere to put): the question "did we
        # hear this right?" belongs in the queue where it can be answered in
        # one tap. Never a drop either way (Option A).
        return {"tier": TIER_REVIEW, "reason": deeper["reason"],
                "floor_reason": deeper["reason"],
                "floor_code": deeper["code"],
                "fusion_reason": "", "fusion_status": fusion_state,
                "relevance_reason": "",
                "superseding_quote": deeper["quote"]}

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
            "floor_code": "",
            "fusion_reason": "", "fusion_status": fusion_state,
            "relevance_reason": verdict["reason"],
            "superseding_quote": ""}


# =============================================================================
# FLOOR3 E — cross-lane dedup.
# =============================================================================
#
# The V1 re-measure's 9th junk item was not a junk CAPTURE at all: two rows,
# seq 8334 and 8339, carried the SAME sentence from the SAME meeting — one on
# the book, one in the queue. `route_meeting_captures` routed and BUILT each
# item as it walked them, so nothing in the batch ever compared two items to
# each other, and because the lane verdict is computed per item the twins could
# land in different lanes and neither would know about the other.
#
# The pass below is the smallest close: group the batch by the ACT, keep one
# row per act. It is a COLLAPSE, never a drop — the survivor is a real row with
# a real verdict, and the count of what folded into it rides along.
#
# Scope is ONE meeting's batch. Cross-meeting duplication is the dedup/digest
# family and is a different problem with a different key.

# Which lane wins when twins disagree. The most-scrutinized: if either twin
# tripped a floor, that verdict is about the shared underlying ACT and holds
# whichever row survives. Losing a floor verdict to a coin flip would make the
# queue's contents depend on extractor ordering.
_TIER_RANK = {TIER_REVIEW: 3, TIER_BOOK: 2, TIER_OBSERVED: 1, TIER_SKIP: 0}


def _owner_key(item: dict) -> tuple:
    """Who owes it. Compared by EQUALITY — a different owner is a different
    commitment, and there is no reading of the evidence that makes two owners
    one."""
    d = item or {}
    return (str(d.get("owner_id") or "").strip().lower(),
            str(d.get("owner_external") or "").strip().lower())


def _field_compatible(a: dict, b: dict, field: str) -> bool:
    """Equal, or absent on one side — the `_due_compatible` shape."""
    va = str((a or {}).get(field) or "").strip().lower()
    vb = str((b or {}).get(field) or "").strip().lower()
    return (not va) or (not vb) or va == vb


def _same_counterparty(a: dict, b: dict) -> bool:
    """Is the same person waiting for it?

    Per field, EQUAL OR ABSENT — not tuple equality (fix round 2, review R1).
    Round 1 put the counterparty in the key because "send the deck" to Bo and
    to Quinn, from one sentence, are two promises and the pass was deleting
    one. Tuple equality fixed that and broke the case the pass was built for:
    on the live ledger the 8334/8339 twins carry the SAME title, the SAME
    evidence and the SAME owner, and differ only in that one extraction
    resolved the counterparty to an id and the other kept the name. Two
    spellings of one person read as two people, and the pair stopped
    collapsing.

    Equal-or-absent satisfies both: the Bo/Quinn pair carries two DIFFERENT
    ids, so it still splits; the live twins carry an id on one side and a name
    on the other, so they still fold.

    The residual risk is honest and small: an id and a name that denote
    DIFFERENT people read as compatible here, because this function is pure
    and cannot resolve either. It only matters for two items that already
    share a title, an utterance, an owner and a due date."""
    return (_field_compatible(a, b, "counterparty_id")
            and _field_compatible(a, b, "counterparty_name"))


def _same_utterance(a: dict, b: dict, spans: list,
                    at_a=None, at_b=None) -> bool:
    """Do these two captures quote the same words? Their evidence spans located
    in the transcript overlap, or — with nothing to locate against — their
    normalized evidence strings are equal.

    `at_a` / `at_b` are the callers' already-computed anchors; the pairwise
    walk in `collapse_duplicate_captures` resolves each item's anchor once and
    passes it in, instead of re-scanning the transcript on every comparison."""
    if spans:
        if at_a is None:
            at_a = _evidence_anchor(a, spans)
        if at_b is None:
            at_b = _evidence_anchor(b, spans)
        if at_a and at_b:
            return at_a[0] < at_b[1] and at_b[0] < at_a[1]
    ev_a = _normalize_for_fusion((a or {}).get("evidence"))
    ev_b = _normalize_for_fusion((b or {}).get("evidence"))
    return bool(ev_a) and ev_a == ev_b


def _same_act(a: dict, b: dict) -> bool:
    """Do they name the same act? Title content sets EQUAL.

    This condition is why the pass is safe. One sentence legitimately yields
    TWO commitments — "I'll send the deck and schedule the follow-up" — and
    collapsing those would lose a real one, which is strictly worse than the
    duplicate row it would prevent.

    EQUALITY, not containment (fix round 1, CONFIRM review F7). The first cut
    accepted one set contained in the other, and that CONFIRMED-collapsed
    "review the contract" into "review the contract with legal" — plausibly the
    same act, plausibly two, and the pass has no way to tell. Containment buys
    a few more collapses of near-identical extractions and risks deleting a
    commitment nobody will ever know was there; equality still folds the
    identical re-extractions this pass exists for. If the V1 re-take shows
    twins escaping on a title-word difference, widen it THEN, with the pair in
    hand."""
    ta = _content_set((a or {}).get("title"))
    tb = _content_set((b or {}).get("title"))
    if not ta and not tb:
        return True
    return bool(ta) and ta == tb


def _due_compatible(a: dict, b: dict) -> bool:
    da = str((a or {}).get("due") or "").strip()
    db = str((b or {}).get("due") or "").strip()
    return (not da) or (not db) or da == db


def collapse_duplicate_captures(items, verdicts, transcript_text=None) -> dict:
    """FLOOR3 E — fold twin captures of one act into one row.

    `items` and `verdicts` are index-aligned (verdict i belongs to item i).
    Returns `{"keep": [idx…], "absorbed": {survivor_idx: n}}`; `keep` is in
    ascending index order, so the surviving events come out in the batch order
    the receipt's counts and the queue's grouping both already read.

    NOT pure — it stamps `duplicate: True` on the verdicts it absorbs, in
    place, because that is what keeps the floor tally counting written ROWS
    rather than extractions. Said plainly here because the first cut claimed
    purity in this docstring while doing exactly this (review F9).

    Two captures are the same act only when ALL FOUR hold — same utterance,
    same act (title content sets EQUAL), same parties (owner AND counterparty),
    compatible due. Conjunctive on purpose: precision over recall, because a
    false collapse silently loses a commitment and a missed one only leaves the
    duplicate row that exists today."""
    items = list(items or [])
    verdicts = list(verdicts or [])
    spans = _fusion_token_spans(transcript_text)
    # Anchor each item ONCE. The pairwise walk below asked for the same anchor
    # every comparison, which made the pass quadratic in transcript scans —
    # 5.6s on a 70-item batch over a 15k-token transcript (review F12).
    anchors = [_evidence_anchor(item, spans) if spans else None
               for item in items]
    def _verdict(i):
        """The verdict for item i, or an empty one. Guarded because `items` and
        `verdicts` are only aligned by CONTRACT — a caller that passes ragged
        lists used to raise IndexError from inside a sort key (review F9)."""
        return verdicts[i] if i < len(verdicts) and isinstance(
            verdicts[i], dict) else {}

    groups: List[List[int]] = []
    for i, item in enumerate(items):
        for group in groups:
            head = items[group[0]]
            if (_owner_key(item) == _owner_key(head)
                    and _same_counterparty(item, head)
                    and _due_compatible(item, head)
                    and _same_act(item, head)
                    and _same_utterance(item, head, spans,
                                        anchors[i], anchors[group[0]])):
                group.append(i)
                break
        else:
            groups.append([i])

    keep: List[int] = []
    absorbed: dict = {}
    for group in groups:
        if len(group) == 1:
            keep.append(group[0])
            continue
        # The DATED twin wins first (review F8). A collapse must not be the one
        # place a due date disappears, and the date is not merely more
        # information — this module treats it as ABSOLUTE: `done_in_meeting_
        # reason` refuses to gate a dated item at all. So when one twin carries
        # a date and the other's J-1 verdict fired only because its extraction
        # dropped it, the date is the better-founded of the two verdicts.
        #
        # Then the winning TIER, so the surviving row keeps a verdict that
        # MATCHES its lane — picking a survivor first and then overwriting its
        # tier would put one twin's lane on the other's reason.
        winner = max(group, key=lambda i: (
            1 if str((items[i] or {}).get("due") or "").strip() else 0,
            _TIER_RANK.get(_verdict(i).get("tier"), 0),
            -i))
        keep.append(winner)
        absorbed[winner] = len(group) - 1
        for i in group:
            if i != winner and i < len(verdicts) and isinstance(verdicts[i],
                                                               dict):
                # Kept in the batch record (it IS something the extractor
                # produced) but marked, so the floor tally does not count one
                # act twice — `capture_telemetry` reads this.
                verdicts[i]["duplicate"] = True
    keep.sort()
    return {"keep": keep, "absorbed": absorbed}


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
    attendee_records=None,
    now_iso: Optional[str] = None,
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
                   "n_floor_gated","n_deduped","n_fusion_inert"}}

    `review` now carries BOTH kinds of queue row: fusion refusals
    (`data.fusion_unverified`) and below-floor captures (`data.floor_gated`,
    M's 2026-08-01 ruling). `n_floor_gated` is a subset of `n_review`, not a
    fifth tier. `skipped` is near-empty by construction since the ruling —
    nothing below the floor goes there.

    PREC1: every written row whose fusion guardrail ran INERT carries
    `data.fusion_inert = True` and is counted in `n_fusion_inert`. The stamp
    moves NO lane — routing still refuses only what the guardrail positively
    establishes is absent — it exists so that the absence of a refusal stops
    reading as a pass.

    ATTENDEE1 — `attendee_records` is THIS MEETING's attendee list, as the
    caller already fetched it (dicts with a name and an email, the connector's
    prose participant block, or plain strings — `attendee_evidence.
    normalize_attendee_records` takes all three and PRESERVES the name/email
    pair, which the persisted `meeting` event does not). When supplied, a
    capture whose counterparty or owner failed person-resolution and whose name
    exact-normalizes onto an attendee OF THIS MEETING carrying an email gets
    that person CREATED and its id filled in, before anything is built — so the
    capture routes clean instead of minting a pending row nobody can answer.

    THIS IS THE ONE PLACE THIS FUNCTION WRITES, and it is deliberate: the
    "construction only" contract held because no upstream code chokepoint
    existed where resolution failure was observable, and this is that point.
    Everything else is unchanged.

    `attendee_records=None` — the default, and every shipped caller — is
    BYTE-IDENTICAL to before: nothing is consulted, no writer is imported, and
    the items are passed through by object identity. `now_iso` is the fire's
    own clock reading, handed down so nothing here reads a live clock (G14);
    absent, the batch id degrades to a constant and the creation is still fully
    reversible.

    `summary` gains `n_auto_created` (additive — no existing count moves) and
    the return gains `auto_created` + `receipt_lines`, which the surface MUST
    render: an auto-creation with no receipt is the CAPTUREFLOW silent-drop
    class inverted.

    Construction only, plus that one seam — append `book + review + observed`
    through `event_gate.append_event` in ONE call, exactly as before."""
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
    # PREC1 — rows the fusion guardrail could not check at all. Counted over
    # SURVIVORS only, for the same reason `floor_reasons` is: a twin that
    # folded into another row was never written, and a receipt that counts it
    # is counting extractions rather than rows.
    n_fusion_inert = 0

    # PASS 1 — the verdict for every item. Separated from the write so PASS 2
    # can compare items to each other; before FLOOR3 this loop routed and BUILT
    # in one walk, which is why nothing ever noticed a batch holding the same
    # act twice.
    staged = [dict(raw or {}) for raw in (items or [])]
    for item in staged:
        verdict = admit_meeting_capture(
            item, transcript_text=transcript_text, capture_context=ctx,
            org_override=override)
        verdicts.append({"title": str(item.get("title") or "").strip(),
                         **verdict})

    # PASS 2 — fold twins of one act into one row (FLOOR3 E).
    collapsed = collapse_duplicate_captures(staged, verdicts, transcript_text)
    keep = set(collapsed["keep"])
    absorbed = collapsed["absorbed"]
    n_deduped = sum(absorbed.values())

    # PASS 2b — ATTENDEE1 §3-2, THE EVIDENCE SEAM. It sits HERE, between the
    # verdicts and the build, and both boundaries are load-bearing:
    #
    #   AFTER the verdicts, so routing is untouched. `admit_meeting_capture`
    #   scores party-only relevance off the item as extracted; seeding first
    #   would hand it a resolved counterparty and could move a row between
    #   lanes. Whether an evidenced party SHOULD change the relevance verdict
    #   is a real question and a separate one — it is not the pending stamp,
    #   and this build does not answer it.
    #
    #   BEFORE the build, because `build_meeting_commitment_event` is where
    #   `capture_gate.gate_commitment_data` mints the pending stamp. Filling
    #   the id in here means the stamp evaluates its OWN shipped rules against
    #   a graph that now contains the person — it is never told to skip a
    #   check. `capture_gate.py` is byte-identical on this branch.
    #
    # Only SURVIVORS that will actually be built as commitments are offered:
    # a collapsed twin was never written, and the observed/skip tiers have no
    # counterparty stamp to answer for.
    auto_created: List[dict] = []
    auto_receipt: List[str] = []
    n_auto_created = 0
    # Review F-4 — the seam distinguishes "created" from "was already on
    # file", and this boundary has to carry BOTH or the distinction dies here:
    # dropping it would leave an already-on-file counted nowhere at all on the
    # production path, which is worse than the mis-bucketing F-4 reported.
    n_already_on_file = 0
    if attendee_records is not None:
        try:
            from attendee_evidence import seed_people_for_items
        except ImportError:  # pragma: no cover — direct-path import
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from attendee_evidence import seed_people_for_items

        eligible = [i for i in sorted(keep)
                    if verdicts[i]["tier"] in (TIER_BOOK, TIER_REVIEW)]
        seeded = seed_people_for_items(
            [staged[i] for i in eligible],
            workspace_root=workspace_root,
            source_ref=source_ref,
            attendee_records=attendee_records,
            now_iso=now_iso,
            source_skill=source_skill,
        )
        for slot, i in enumerate(eligible):
            staged[i] = seeded["items"][slot]
        auto_created = list(seeded["created"])
        auto_receipt = list(seeded["receipt_lines"])
        n_auto_created = int(seeded["n_created"])
        n_already_on_file = int(seeded.get("n_already_on_file") or 0)

    # PASS 3 — build the survivors, in batch order.
    for idx, item in enumerate(staged):
        if idx not in keep:
            continue
        title = str(item.get("title") or "").strip()
        verdict = verdicts[idx]
        tier = verdict["tier"]
        floor_gated = bool(verdict["floor_reason"])
        # PREC1 — the stamp. It rides EVERY lane, because the question it
        # answers ("was this row's evidence ever checked against the meeting?")
        # is the same question in all three, and a stamp that only marks the
        # book lane would leave the queue's own rows reading as verified.
        inert = verdict.get("fusion_status") == FUSION_INERT

        if tier == TIER_OBSERVED:
            try:
                obs_ev = build_observed_event(
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
                )
                if absorbed.get(idx):
                    obs_ev.setdefault("data", {})["duplicate_absorbed"] = \
                        absorbed[idx]
                if inert:
                    obs_ev.setdefault("data", {})["fusion_inert"] = True
                    n_fusion_inert += 1
                observed.append(obs_ev)
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
                    verdicts[idx]["tier"] = TIER_SKIP
                    continue
                # A FLOOR-gated row is never dropped (M ruling): the observed
                # writer refusing it sends it to the queue, not to nothing.
                tier = TIER_REVIEW
                verdicts[idx]["tier"] = TIER_REVIEW

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
            verdicts[idx]["tier"] = TIER_SKIP
            continue
        if attribution_extra:
            ev["data"].update(attribution_extra)
        if absorbed.get(idx):
            # FLOOR3 E — a COUNT of the twins that folded in, never their
            # titles: this rides an event whose counts reach the receipt, and
            # "counts only, never a title" is that contract.
            ev["data"]["duplicate_absorbed"] = absorbed[idx]
        if inert:
            # PREC1 — the guardrail never ran on this row. A boolean, not a
            # sentence: the row's own reason field belongs to whatever verdict
            # routed it, and this is a property of the CHECK, not of the item.
            ev["data"]["fusion_inert"] = True
            n_fusion_inert += 1
        if tier == TIER_REVIEW:
            # The two ways a row lands in the queue are marked apart, because
            # they are different questions to a reader and — for `floor_gated`
            # — because a future config toggle needs something to key on.
            if floor_gated:
                ev["data"]["floor_gated"] = True
                # FLOOR2 B: the retraction rides BESIDE the original evidence,
                # both verbatim from the same transcript. A row that says "the
                # call took this back" and cannot show the words is a verdict
                # the user has to take on faith.
                quote = str(verdict.get("superseding_quote") or "").strip()
                if quote:
                    ev["data"]["superseding_quote"] = clip(quote)
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
        # ATTENDEE1 §0-4 — the people this pass created and the lines the
        # surface renders for them. Empty lists on every unstamped call, so a
        # caller that never passes `attendee_records` sees the same shape it
        # always did plus two empties.
        "auto_created": auto_created,
        "receipt_lines": auto_receipt,
        "summary": {
            "n_book": len(book),
            "n_review": len(review),
            "n_observed": len(observed),
            "n_skipped": len(skipped),
            # ATTENDEE1 §3-5 — people created from attendee evidence in this
            # fire. Additive: it is a count of RECORDS, not of rows, so it is a
            # subset of nothing and is never added to another number.
            "n_auto_created": n_auto_created,
            # Review F-4 — evidence matched somebody ALREADY on file. Its own
            # key, never folded into the line above: nobody was added, and a
            # count that says otherwise is a receipt for work that did not
            # happen. Also a count of records, also a subset of nothing.
            "n_already_on_file": n_already_on_file,
            # A SUBSET of n_review, not a fifth tier: V1 has to be able to
            # read the floor's yield apart from the fusion guardrail's, and
            # one combined review number cannot answer that.
            "n_floor_gated": n_floor_gated,
            # FLOOR3 E — twins folded into a survivor. Not a tier and not a
            # skip: these rows were never written, so no other count moves.
            "n_deduped": n_deduped,
            # PREC1 — rows written with the fusion guardrail INERT. Cuts
            # ACROSS the tiers (a book row and a queue row can both be inert),
            # so it is a subset of nothing and is never added to another count.
            # This is the number the V1 re-measure had no way to read: on the
            # audited population 20 of 131 rows were in this class and the
            # substrate recorded none of them.
            "n_fusion_inert": n_fusion_inert,
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
    "meeting_ref_keys",
    "already_processed",
    "verify_claims",
    # CAPTUREFLOW source gates
    "FLOOR_NO_OWNER",
    "FLOOR_NO_DELIVERABLE",
    "FLOOR_NO_CONSEQUENCE",
    "FLOOR_RETOLD",
    "FLOOR_NOT_ACCEPTED",
    "FLOOR_DONE_IN_MEETING",
    "FLOOR_SUPERSEDED_IN_MEETING",
    "FLOOR_CODE_NO_OWNER",
    "FLOOR_CODE_NO_DELIVERABLE",
    "FLOOR_CODE_NO_CONSEQUENCE",
    "FLOOR_CODE_RETOLD",
    "FLOOR_CODE_NOT_ACCEPTED",
    "FLOOR_CODE_DONE_IN_MEETING",
    "FLOOR_CODE_SUPERSEDED_IN_MEETING",
    "FLOOR_CODES",
    "FLOOR_CODE_VALUES",
    "FLOOR_CODE_LEGACY",
    "FLOOR_CODE_RE",
    "SUPERSEDE_WINDOW_WORDS",
    "SUPERSEDE_MIN_SHARED",
    "SUPERSEDE_QUOTE_WORDS",
    "DISCHARGE_WINDOW_WORDS",
    "DEMO_REGISTER_MIN_CUES",
    "ACCEPTANCE_WINDOW_WORDS",
    "ACCEPTANCE_IMMEDIATE_WORDS",
    "RECOMMIT_WINDOW_WORDS",
    "accepted_in_exchange",
    "demo_register",
    "collapse_duplicate_captures",
    "floor_reason_code",
    "parse_floor_reasons",
    "FUSION_REVIEW_REASON",
    "FUSION_MIN_WORDS",
    "FUSION_VERIFIED",
    "FUSION_REFUSED",
    "FUSION_INERT",
    "fusion_status",
    "TIER_BOOK",
    "TIER_REVIEW",
    "TIER_OBSERVED",
    "TIER_SKIP",
    "capture_floor_reason",
    "fusion_refusal_reason",
    "done_in_meeting_reason",
    "superseded_in_meeting",
    "transcript_floor_reason",
    "admit_meeting_capture",
    "route_meeting_captures",
]
