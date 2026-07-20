#!/usr/bin/env python3
"""
confirm_flow.py — THE unconfirmed-commitment confirm-flow selectors
(v4.6.1 W4b; FINDINGS F-13 P2b / F-56 — the persisting owner
misattributions this flow exists to catch).

THE GAP THIS CLOSES
===================
Before W4b, needs-confirm items sat age-buried in the full triage pool (a
fresh ambiguous capture was 17 pages deep behind 50-day-old items), and
person_proposals appeared ONCE in whatever widget captured them — dismiss
that chat and the proposal was stranded pending_review forever.

The principle: **unconfirmed items don't age into the pool — they escalate
to confirmation.**

  select_confirm_items(...)          the daily "Needs a quick confirm" set:
                                     every capture younger than the 7-day
                                     escalation pin that is pending_review,
                                     unowned, or carries a C4
                                     suspected_duplicate_of flag (v4.6.1 S3:
                                     widened from 24h — items aged 1-7 days
                                     used to fall in a hole between the
                                     daily section and the pin)
  load_open_person_proposals(...)    unadjudicated person_proposal /
                                     person_update_proposal events — these
                                     re-surface EVERY day until adjudicated
                                     (the stranding fix), no age window
  select_promotion_proposals(...)    kind=task items that gained a
                                     resolvable counterparty → "Make it a
                                     commitment?" proposals (4.6 fold-in;
                                     PROPOSE only, never auto — S2's
                                     shipped `promote` verb adjudicates)
  select_unconfirmed_escalation(...) 7d+ unconfirmed → pinned "Unconfirmed"
                                     block at the TOP of the triage widget
                                     (never age-buried); 30d+ → weekly
                                     cleanup proposes Drop (manual click)
  confirm_pointer_line(n)            the morning brief's ONE pointer line —
                                     None when the confirm set is empty
                                     (the line renders only when non-empty)
  build_person_proposal_resolved_event(...)
                                     the proposal tombstone (Add person /
                                     Same as / Not relevant all write it, so
                                     adjudicated proposals stop re-surfacing)

GUARDRAILS (restated wherever these rows render)
================================================
- Unconfirmed items NEVER enter chase — no auto-email on a guessed owner.
  Selection here changes SURFACING only; chase eligibility is enforced in
  cru_match / the orchestrator filters.
- Unconfirmed items count ONLY in the headline "unconfirmed" bucket
  (commitment_state.count_commitments — R4's one bucket export); nothing
  here re-derives counts.
- Proposals re-surface daily until adjudicated. Adjudication is an event
  (owner confirm / reassign / close / promote / person_proposal_resolved),
  never a rendering side effect.

Pure functions over caller-supplied data wherever possible; the only file
I/O is the explicitly-named load_open_person_proposals.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re as _re
import sys
from pathlib import Path
from typing import Iterable, Optional

try:
    from cru_match import (
        _commitment_field,
        _commitment_id,
        _is_pending_review,
        load_events_defensively,
    )
    from commitment_state import commitment_kind
    from event_time import event_dt, parse_ts
except ImportError:  # pragma: no cover — direct-path import fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import (
        _commitment_field,
        _commitment_id,
        _is_pending_review,
        load_events_defensively,
    )
    from commitment_state import commitment_kind
    from event_time import event_dt, parse_ts

# 7+ days unconfirmed → pinned Unconfirmed block at the TOP of triage.
ESCALATION_PIN_DAYS = 7
# The daily confirm window runs right up to the escalation pin — every
# unadjudicated amber item is either in the daily section (< 7d) or the
# pinned Unconfirmed block (7d+); nothing falls between (S3, W4b gap fix).
CONFIRM_WINDOW_HOURS = ESCALATION_PIN_DAYS * 24
# 30+ days unconfirmed → weekly cleanup proposes Drop (still a manual click).
ESCALATION_DROP_DAYS = 30

PROPOSAL_TYPES = ("person_proposal", "person_update_proposal")
PROPOSAL_RESOLUTIONS = ("person_added", "same_as", "not_relevant")

# FB-8 (T3) — LEGACY person proposals (older skill versions + freelance
# writers) carry the as-heard fields under several spellings; only the
# current builder writes `name`. On the live substrate the dominant name
# carriers are `inferred_name` and `proposed_name` (plus one `display_name`)
# — reading `name` alone rendered EVERY legacy identity row nameless. The
# reader coalesces first-non-empty so no consumer ever sees a nameless row
# when the event carried a name. Order: current shape first, then the
# legacy spellings by observed frequency.
PERSON_NAME_KEYS = ("name", "inferred_name", "proposed_name", "display_name")
PERSON_ROLE_KEYS = ("inferred_role", "proposed_role", "role")
PERSON_ORG_KEYS = ("inferred_org", "proposed_org", "inferred_org_name",
                   "proposed_org_name", "proposed_org_canonical_name",
                   "org_hint")
PERSON_EVIDENCE_KEYS = ("evidence", "signal", "note", "context", "reason")


def _first_str(data: dict, keys) -> Optional[str]:
    """First non-empty string value across the legacy field spellings."""
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def compute_proposal_fingerprint(ev_type, name, captured_ts) -> str:
    """D8 (SPEC PID1) — the content fingerprint that makes a SEQ-LESS person
    proposal adjudicatable: sha256(type|normalized name|captured_ts)[:12],
    computed IDENTICALLY by the reader (`load_open_person_proposals`) and by
    any tombstone writer. Events are immutable (event_gate doctrine), so a
    freelance-written `seq: null` proposal can never gain a seq — without
    this path such rows are unadjudicatable forever.

    ACTIVATION RULE: the fingerprint path activates ONLY for seq-less rows.
    Int-seq matching is untouched and always preferred."""
    norm = _re.sub(r"\s+", " ", (name or "").strip().lower())
    basis = f"{ev_type or ''}|{norm}|{captured_ts or ''}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


# -----------------------------------------------------------------------------
# The three amber classes
# -----------------------------------------------------------------------------


def unconfirmed_classes(ev: dict) -> list[str]:
    """The amber classes a PROJECTED open commitment belongs to (pass
    load_open_commitments output — the adjudication folds must already be
    applied, so a Mine-confirmed item correctly returns []).

      pending_review       extraction/CRU/reassignment flagged it uncertain
      unowned              no resolvable owner_id (extraction gap)
      suspected_duplicate  C4's capture-time semantic dedup flagged it

    Empty list == confirmed (not a confirm-flow item).
    """
    classes: list[str] = []
    if _is_pending_review(ev):
        classes.append("pending_review")
    if not _commitment_field(ev, "owner_id"):
        classes.append("unowned")
    d = ev.get("data") or {}
    if d.get("suspected_duplicate_of"):
        classes.append("suspected_duplicate")
    return classes


def is_unconfirmed(ev: dict) -> bool:
    """True iff the projected commitment is in any amber class."""
    return bool(unconfirmed_classes(ev))


def _commitment_row(ev: dict, classes: list[str]) -> dict:
    """One selector row — everything a confirm surface renders, nothing more.
    `commitment_id` is the canonical data.id VERBATIM (widget identity
    contract, Stage B)."""
    from commitment_parties import (
        counterparty_ids as _cp_ids,
        counterparty_names as _cp_names,
        primary_counterparty_id as _p_cp_id,
        primary_counterparty_name as _p_cp_name,
    )
    d = ev.get("data") or {}
    return {
        "commitment_id": _commitment_id(ev),
        "title": _commitment_field(ev, "title") or "",
        "kind": commitment_kind(ev),
        "due": _commitment_field(ev, "due"),
        "owner_id": _commitment_field(ev, "owner_id"),
        "owner_external": d.get("owner_external"),
        # MC1: primary (first) counterparty for single-value renders + the
        # full roster so a Theirs-routing surface can show every recipient.
        "counterparty_id": _p_cp_id(ev),
        "counterparty_name": _p_cp_name(ev),
        "counterparty_ids": _cp_ids(ev),
        "counterparty_names": _cp_names(ev),
        "classes": classes,
        "review_reason": d.get("review_reason"),
        "suspected_duplicate_of": d.get("suspected_duplicate_of"),
        "suspected_duplicate_score": d.get("suspected_duplicate_score"),
        "captured_ts": (ev.get("ts") or ev.get("timestamp") or ev.get("date") or ""),
        "seq": ev.get("seq"),
    }


def _age_days(ev: dict, now: Optional[_dt.datetime]) -> Optional[float]:
    captured = event_dt(ev)
    if captured is None or now is None:
        return None
    return (now - captured).total_seconds() / 86400.0


def select_confirm_items(
    open_commitments: list[dict],
    now_iso: str,
    *,
    window_hours: int = CONFIRM_WINDOW_HOURS,
    dismissed_ids: Optional[Iterable[str]] = None,
) -> list[dict]:
    """The daily confirm section's commitment rows: every open commitment
    CAPTURED within the last `window_hours` (default: the full stretch up to
    the 7-day escalation pin) that is pending_review, unowned, or carries a
    C4 suspected_duplicate_of flag. Confirmed items are out by definition
    (empty amber class list); adjudicated items are out because the
    projector already folded the adjudication (Mine/Theirs cleared
    pending_review; merge/drop closed them out of the open set). Items at
    `window_hours`+ are the escalation selector's job — the two windows
    tile with no gap.

    Pass the PROJECTED open set (load_open_commitments). `dismissed_ids` —
    optional set of live-muted target ids (mute_ledger.
    active_dismissal_target_ids) so a snoozed row stays quiet for its TTL.
    Oldest first (the longest-waiting confirm leads). Pure — no I/O.
    """
    now = parse_ts(now_iso)
    dismissed = set(dismissed_ids or ())
    out: list[dict] = []
    for ev in open_commitments:
        classes = unconfirmed_classes(ev)
        if not classes:
            continue
        age = _age_days(ev, now)
        if age is None or age < 0 or age > window_hours / 24.0:
            continue
        cid = _commitment_id(ev)
        if cid in dismissed or str(ev.get("seq")) in dismissed:
            continue
        out.append(_commitment_row(ev, classes))
    out.sort(key=lambda r: r["captured_ts"])
    return out


def select_unconfirmed_escalation(
    open_commitments: list[dict],
    now_iso: str,
    *,
    pin_days: int = ESCALATION_PIN_DAYS,
    drop_days: int = ESCALATION_DROP_DAYS,
) -> dict:
    """The escalation split over the PROJECTED open set:

      pin          unconfirmed for `pin_days`+ — the dedicated "Unconfirmed"
                   block pinned at the TOP of the triage widget, never
                   age-sorted into the pool
      propose_drop the `drop_days`+ subset (also in `pin`) — weekly cleanup
                   proposes Drop for these in its Monday note (PROPOSE only:
                   the drop is still a manual click, never automatic)

    Rows carry `days_unconfirmed`. Oldest first. Pure — no I/O.
    """
    now = parse_ts(now_iso)
    pin: list[dict] = []
    propose_drop: list[dict] = []
    for ev in open_commitments:
        classes = unconfirmed_classes(ev)
        if not classes:
            continue
        age = _age_days(ev, now)
        if age is None or age < pin_days:
            continue
        row = _commitment_row(ev, classes)
        row["days_unconfirmed"] = int(age)
        pin.append(row)
        if age >= drop_days:
            propose_drop.append(row)
    pin.sort(key=lambda r: r["captured_ts"])
    propose_drop.sort(key=lambda r: r["captured_ts"])
    return {"pin": pin, "propose_drop": propose_drop}


# -----------------------------------------------------------------------------
# Kind auto-promotion proposals (4.6 fold-in — PROPOSE only, never auto)
# -----------------------------------------------------------------------------


def select_promotion_proposals(
    open_commitments: list[dict],
    *,
    dismissed_ids: Optional[Iterable[str]] = None,
) -> list[dict]:
    """kind=task items that have gained a RESOLVABLE counterparty (a
    counterparty_id — via reassign, edit, or a later corroboration writer):
    the confirm section proposes "Make it a commitment?" using S2's shipped
    `promote` verb. PROPOSE only — nothing here reclassifies; the user's
    promote click dispatches commitment_state.promote_task_to_commitment.

    Adjudication is structural: promote flips the effective kind (the row
    drops out because the projected kind is no longer task); a mute lands
    the id in `dismissed_ids` for its TTL. Proposals re-surface daily until
    one of those happens. Pass the PROJECTED open set (kind overrides and
    reassignment folds already applied). Pure — no I/O.
    """
    from commitment_parties import primary_counterparty_id as _p_cp_id
    dismissed = set(dismissed_ids or ())
    out: list[dict] = []
    for ev in open_commitments:
        if commitment_kind(ev) != "task":
            continue
        # MC1: a task that gained ANY resolvable counterparty is promotable;
        # use the primary for the proposal row.
        cp = _p_cp_id(ev)
        if not cp:
            continue
        cid = _commitment_id(ev)
        if cid in dismissed or str(ev.get("seq")) in dismissed:
            continue
        d = ev.get("data") or {}
        out.append({
            "commitment_id": cid,
            "title": _commitment_field(ev, "title") or "",
            "due": _commitment_field(ev, "due"),
            "counterparty_id": cp,
            "counterparty_name": d.get("counterparty_name"),
            "captured_ts": (ev.get("ts") or ev.get("timestamp") or ""),
            "seq": ev.get("seq"),
        })
    out.sort(key=lambda r: r["captured_ts"])
    return out


# -----------------------------------------------------------------------------
# Person proposals — the stranding fix
# -----------------------------------------------------------------------------


def _workspace_root_from_events_path(events_jsonl_path):
    """<ws>/_hq/data/events.jsonl → <ws>. Returns None when the path is not in
    that canonical layout (fail-open: no derivation ⇒ no suppression)."""
    try:
        p = Path(events_jsonl_path).resolve()
        if p.parent.name == "data" and p.parent.parent.name == "_hq":
            return p.parent.parent.parent
    except Exception:
        pass
    return None


def person_name_on_file(workspace_root, name) -> bool:
    """FS-19 — does `name` confidently resolve to an existing contact?

    Reuses `find_existing_person` verbatim (that resolver is where Bug #19
    lived — we do NOT re-implement its tiers):
      - a returned record → full multi-token name exact (Tier 2), or email
        exact (Tier 1) for a caller that passes one: unambiguously the same
        person → True.
      - MultipleCandidatesError → a lone first-name / alias-only hit (Tier 3):
        DELIBERATELY ambiguous ("Kevin" could be a different, new Kevin) →
        False; this stays a human decision, never an auto-suppress.
      - no match → False.
    Fail-open on any error (show the row rather than silently hide an identity
    decision). THE one predicate — the loader's suppress_on_file and
    brain_proposals.person_proposal_already_on_file both route through here so
    the rule can never fork."""
    name = (name or "").strip()
    if not name or workspace_root is None:
        return False
    try:
        from people_writer import MultipleCandidatesError, find_existing_person

        try:
            return find_existing_person(workspace_root, name=name) is not None
        except MultipleCandidatesError:
            return False
    except Exception:
        return False


def load_open_person_proposals(
    events_jsonl_path,
    *,
    dismissed_target_ids: Optional[Iterable[str]] = None,
    suppress_on_file: bool = False,
) -> list[dict]:
    """Every person_proposal / person_update_proposal event NOT yet
    adjudicated by a person_proposal_resolved tombstone. These re-surface
    daily until adjudicated — no age window, by design (F-46 P2b: a
    proposal must never die with the chat that captured it).

    `suppress_on_file=True` (FS-19) drops an ADD-type proposal
    (`person_proposal`) whose name confidently resolves to an existing contact
    — so every RENDER surface reading this loader (staff meeting, morning
    brief, commitments chat) agrees by construction that an already-on-file
    person is not a "track them?" row. Opt-in because the person-backlog sweep
    reads with the default False: it MUST see on-file collisions to hold them
    for a same-name confirm. `person_update_proposal`s are never suppressed —
    existence is their premise. The workspace root is derived from the events
    path's canonical layout; a non-canonical path fails open (no suppression).

    Adjudication chain: a person_proposal_resolved whose data.proposal_seq
    matches the proposal's seq retires it permanently (Add person / Same as
    / Not relevant all write one — see build_person_proposal_resolved_event).
    `dismissed_target_ids` (mute_ledger set, matched against str(seq))
    additionally quiets a snoozed row for its TTL without adjudicating it.

    Returns rows oldest first:
      {seq, type, name, person_id, inferred_role, inferred_org, evidence,
       review_reason, source_ref, captured_ts}

    name / inferred_role / inferred_org / evidence / source_ref are
    COALESCED across the legacy field spellings (PERSON_*_KEYS — FB-8): the
    live substrate carries `inferred_name` / `proposed_name` / `display_name`
    rows written by older skill versions, and reading `name` alone rendered
    every one of them as a nameless identity row.
    """
    path = Path(events_jsonl_path)
    if not path.exists():
        return []
    events, _skipped = load_events_defensively(path)
    resolved_seqs: set = set()
    resolved_fps: set = set()
    proposals: list[dict] = []
    for ev in events:
        et = ev.get("type") or ""
        d = ev.get("data") or {}
        if et in ("person_proposal_resolved", "person_proposal_reopened"):
            ps = d.get("proposal_seq")
            if isinstance(ps, str) and ps.strip().isdigit():
                ps = int(ps.strip())
            if isinstance(ps, int) and not isinstance(ps, bool):
                # T2.2 (backlog-sweep undo): a later person_proposal_reopened
                # lifts the tombstone — events are in seq order, so the last
                # writer wins; a re-resolve after a reopen re-tombstones.
                if et == "person_proposal_resolved":
                    resolved_seqs.add(ps)
                else:
                    resolved_seqs.discard(ps)
            else:
                # D8 (PID1): the fingerprint tombstone path for SEQ-LESS
                # proposals — activates only when no int seq is carried.
                fp = d.get("proposal_fingerprint")
                if isinstance(fp, str) and fp.strip():
                    if et == "person_proposal_resolved":
                        resolved_fps.add(fp.strip())
                    else:
                        resolved_fps.discard(fp.strip())
        elif et in PROPOSAL_TYPES:
            proposals.append(ev)
    dismissed = set(dismissed_target_ids or ())
    ws_root = _workspace_root_from_events_path(path) if suppress_on_file else None
    out: list[dict] = []
    for ev in proposals:
        seq = ev.get("seq")
        if isinstance(seq, int) and seq in resolved_seqs:
            continue
        if str(seq) in dismissed:
            continue
        d = ev.get("data") or {}
        # FB-8: coalesce across the legacy field spellings — the as-heard
        # name is load-bearing downstream ("{name — badge · evidence ·
        # consequence}"); `source_refs` (list) is the plural legacy spelling.
        name = _first_str(d, PERSON_NAME_KEYS)
        # D8 (PID1): a seq-less row (freelance-written `seq: null`) folds via
        # its content fingerprint — the ONLY way it can ever be adjudicated.
        # Int-seq rows never compute one (activation rule).
        fingerprint = None
        if not (isinstance(seq, int) and not isinstance(seq, bool)):
            fingerprint = compute_proposal_fingerprint(
                ev.get("type"), name,
                ev.get("ts") or ev.get("timestamp") or "")
            if fingerprint in resolved_fps:
                continue
        # FS-19 — already a contact? An ADD-type proposal whose name
        # confidently resolves to an existing record never surfaces (see
        # person_name_on_file); update-type rows are exempt.
        if suppress_on_file and ev.get("type") == "person_proposal" \
                and person_name_on_file(ws_root, name):
            continue
        source_ref = _first_str(d, ("source_ref", "source"))
        if not source_ref:
            refs = d.get("source_refs")
            if isinstance(refs, list) and refs and isinstance(refs[0], str):
                source_ref = refs[0].strip() or None
        out.append({
            "seq": seq,
            "type": ev.get("type"),
            "name": name,
            "person_id": d.get("person_id"),
            "inferred_role": _first_str(d, PERSON_ROLE_KEYS),
            "inferred_org": _first_str(d, PERSON_ORG_KEYS),
            "evidence": _first_str(d, PERSON_EVIDENCE_KEYS),
            "review_reason": d.get("review_reason"),
            "source_ref": source_ref,
            "captured_ts": (ev.get("ts") or ev.get("timestamp") or ""),
            # D8: present ONLY on seq-less rows (None otherwise) — the
            # tombstone key downstream writers pass back.
            "fingerprint": fingerprint,
        })
    out.sort(key=lambda r: r["captured_ts"])
    return out


def build_person_proposal_resolved_event(
    proposal_seq: Optional[int],
    *,
    resolution: str,
    source_skill: str,
    person_id: Optional[str] = None,
    alias: Optional[str] = None,
    note: str = "",
    proposal_fingerprint: Optional[str] = None,
) -> dict:
    """The proposal tombstone. `resolution` ∈ {person_added, same_as,
    not_relevant}:

      person_added  Add person — people_writer.create_person ran (pass the
                    new person_id)
      same_as       Same as [existing] — people_writer.add_person_alias ran
                    (pass the existing person_id + the alias spelling saved)
      not_relevant  the proposal is noise — permanent tombstone, nothing
                    else written

    Append via event_gate.append_event (seq/ts auto-stamped) — never
    hand-rolled. The entity write happens FIRST (apply-choices Step 3a
    path); the tombstone records the outcome so the proposal stops
    re-surfacing.

    D8 (PID1): a SEQ-LESS proposal (freelance-written `seq: null`) is
    tombstoned by `proposal_fingerprint` instead — pass proposal_seq=None
    plus the fingerprint the reader computed (the row's `fingerprint` field,
    from `compute_proposal_fingerprint`). ACTIVATION RULE: the fingerprint
    path exists only for seq-less rows; when an int seq is available it MUST
    be passed (int matching stays the preferred, untouched contract).
    """
    if resolution not in PROPOSAL_RESOLUTIONS:
        raise ValueError(
            f"invalid resolution {resolution!r} (allowed: {PROPOSAL_RESOLUTIONS})"
        )
    if isinstance(proposal_seq, str) and proposal_seq.strip().isdigit():
        proposal_seq = int(proposal_seq.strip())
    if proposal_seq is None:
        if not (isinstance(proposal_fingerprint, str)
                and proposal_fingerprint.strip()):
            raise ValueError(
                "proposal_seq must be the proposal event's integer seq — or, "
                "for a seq-less proposal ONLY, pass proposal_fingerprint (D8)")
        data: dict = {"proposal_fingerprint": proposal_fingerprint.strip(),
                      "resolution": resolution}
        if person_id:
            data["person_id"] = person_id
        if alias:
            data["alias"] = alias
        if note:
            data["note"] = note[:200]
        return {
            "type": "person_proposal_resolved",
            "source_skill": source_skill,
            "data": data,
        }
    if not isinstance(proposal_seq, int) or isinstance(proposal_seq, bool):
        raise ValueError("proposal_seq must be the proposal event's integer seq")
    data = {"proposal_seq": proposal_seq, "resolution": resolution}
    if person_id:
        data["person_id"] = person_id
    if alias:
        data["alias"] = alias
    if note:
        data["note"] = note[:200]
    return {
        "type": "person_proposal_resolved",
        "source_skill": source_skill,
        "data": data,
    }


# -----------------------------------------------------------------------------
# The morning brief's one pointer line
# -----------------------------------------------------------------------------


def confirm_pointer_line(n_items: int) -> Optional[str]:
    """The ONE morning-brief pointer — None when the confirm section is
    empty (the line renders only when non-empty; never pad). `n_items` is
    the confirm section's full row count for the day: 24h commitment rows +
    open person proposals + promotion proposals."""
    if not isinstance(n_items, int) or n_items <= 0:
        return None
    if n_items == 1:
        return ("1 item needs a 10-second confirm — "
                "it's in your Commitments chat.")
    return (f"{n_items} items need a 10-second confirm — "
            "they're in your Commitments chat.")


__all__ = [
    "CONFIRM_WINDOW_HOURS",
    "ESCALATION_PIN_DAYS",
    "ESCALATION_DROP_DAYS",
    "PROPOSAL_TYPES",
    "PROPOSAL_RESOLUTIONS",
    "PERSON_NAME_KEYS",
    "PERSON_ROLE_KEYS",
    "PERSON_ORG_KEYS",
    "PERSON_EVIDENCE_KEYS",
    "unconfirmed_classes",
    "is_unconfirmed",
    "select_confirm_items",
    "select_unconfirmed_escalation",
    "select_promotion_proposals",
    "load_open_person_proposals",
    "person_name_on_file",
    "build_person_proposal_resolved_event",
    "compute_proposal_fingerprint",
    "confirm_pointer_line",
]
