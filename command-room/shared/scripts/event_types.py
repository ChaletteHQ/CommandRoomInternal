#!/usr/bin/env python3
"""
Canonical event-type vocabulary loader (Phase 1 Foundation, 2026-07).

ENUM HOME DECISION (final — later phases register events HERE, nowhere else):
`shared/data-schemas/events.schema.json` is the ONE home of the event-type
enum. It was already enforced before this module existed
(run_source_of_truth_test.py Check 4 scans every documented
`"type": "<name>"` literal against it), so duplicating the list as a Python
literal would create a second source of truth that drifts. This module makes
the same enum runtime-readable for the append gatekeeper (event_gate.py)
without ever owning a copy of the list.

Corrected 2026-07-25: this docstring used to claim a second enforcer —
"weekly-audit validates live events against it." No such skill exists in core,
and `is_known_type` has exactly one caller (the write gate). NOTHING validates
event types on the read side. What live substrate actually holds is documented
in PRE_REGISTRY_FOSSILS at the bottom of this module.

To register a new event type:
  1. Add it to the `type` enum in shared/data-schemas/events.schema.json.
  2. Document its writer + named consumers in shared/EVENT_TYPES.md
     (no consumer-less writes — Writes-checklist item 5).
Nothing else. The gatekeeper, the source-of-truth test, and weekly-audit all
pick it up from the schema.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import FrozenSet, Optional

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "data-schemas" / "events.schema.json"
)
_ENUM_CACHE: Optional[FrozenSet[str]] = None

# Ratified 2026-07-01 (M, A-vs-B review): every commitment event carries a
# required `data.kind` discriminator. The policy layer (tasks never enter CRU,
# 30-day staleness, promote-to-commitment) keys off this label — Phase 2
# Stage D enforces it as a code-level kind filter.
KIND_VALUES: FrozenSet[str] = frozenset({"promise", "task", "scheduling", "agenda"})

# THE closure-target chain — write-accept and read-honor derive from this
# one list (BUG-8330 item 3: data.thread_id was honored on read but absent
# from the gate's accept list, and top-level target_id was accepted nowhere;
# two lists WILL drift, one cannot). Each limb is (scope, field): scope
# "data" reads ev["data"][field], scope "" reads ev[field]. Order is the
# read-side priority — closure_index.closer_target_id walks it verbatim.
COMMITMENT_CLOSURE_ID_CHAIN = (
    ("data", "commitment_id"),
    ("data", "thread_id"),
    ("data", "id"),
    ("data", "target_id"),
    ("", "commitment_id"),
    ("", "thread_id"),
    ("", "id"),
    ("", "target_id"),
)

# The two F3-amnesty seq-alias fields (data scope) — both map seq → the
# commitment EVENT at that seq.
COMMITMENT_CLOSURE_SEQ_FIELDS = ("commitment_seq", "source_event_seq")

# Flat data-scope view of the chain + the seq aliases, kept for the gate's
# error message and older callers. Derived — never edit independently.
COMMITMENT_CLOSURE_ID_FIELDS = tuple(
    field for scope, field in COMMITMENT_CLOSURE_ID_CHAIN if scope == "data"
) + COMMITMENT_CLOSURE_SEQ_FIELDS

# THE decision-supersede target chains (SUPERSEQ1, walk finding F-11,
# 2026-08-29 — the THIRD instance of writer/reader field drift after BUG-8330
# item 3 and the 2026-08-13 decision-log drift). Same one-list-not-two
# reasoning as COMMITMENT_CLOSURE_ID_CHAIN: the schema/gate accept a
# TOP-LEVEL `supersedes_seq` on any event ("if this event corrects or
# overrides an earlier event, its seq number"), and decision-revisit's write
# contract documents that field as THE link between a superseding write and
# the ruling it retires — but the decision-log renderer honored only the
# data-scope spellings on `decision_superseded` events, so a ruling
# superseded ONLY via the field rendered TWICE (old and new both active)
# until the other mechanism retired it. The attended walk hit this live on
# restamped rulings. Readers bind to these chains through
# `decision_supersede_targets`; the SUPERSEQ1 guard test asserts the chains
# cover the schema/gate-accepted shapes AND that both readers
# (render_decision_log, decision_match) are bound to this one implementation.
#
# Each limb is (scope, field): scope "data" reads ev["data"][field], scope ""
# reads ev[field]. Order is read-side priority; all matching limbs key the
# reader's maps (tolerant multi-key join, deduplicated by row identity).

# Shapes on a decision OVERLAY event (`decision_superseded`) naming the
# ruling it retires.
DECISION_SUPERSEDE_ID_CHAIN = (("data", "decision_id"),)
DECISION_SUPERSEDE_SEQ_CHAIN = (
    ("data", "original_decision_seq"),
    ("data", "supersedes_seq"),
    ("data", "decision_event_seq"),
    ("", "supersedes_seq"),
)

# Shapes on a `decision` event itself — a RESTAMP: the new ruling carries the
# seq of the one it replaces (decision-revisit SKILL contract: "`supersedes_seq`
# field links the two"). Deliberately narrower than the overlay chain:
# `original_decision_seq` / `decision_event_seq` are overlay-target vocabulary
# and name nothing on a decision row.
DECISION_RESTAMP_SEQ_CHAIN = (
    ("", "supersedes_seq"),
    ("data", "supersedes_seq"),
)


# Shapes on a `decision_resolved` event naming the ruling it closes out — the
# EXECUTION closer ("we signed it", "implementation kicked off"), as opposed to
# a supersede ("a later ruling replaced it"). DECSHAPES1 (2026-09-02).
#
# WHY THIS IS THE SAME TWO CHAINS. `decision_resolved` and
# `decision_superseded` are both decision OVERLAY events, they are handled by
# the same `if et in (...)` limb in decision_match's loader, the gate applies
# no decision-specific validation to either, and the schema's top-level
# `supersedes_seq` ("if this event corrects or overrides an earlier event, its
# seq number") is accepted on both. SUPERSEQ1 noted the resolved closer was
# still id-only in decision_match and left it — the builder writes
# `data.decision_id` always. But "the current builder only writes one spelling"
# is exactly the sentence that preceded all three prior instances of the drift
# class: the reader is narrower than what the gate accepts, and the day some
# writer (a repair pass, a migration, a hand append) uses an accepted spelling,
# the ruling is closed on the ledger and open in the reader with nothing
# reporting anything. So the resolved closer reads the SAME accepted
# vocabulary the supersede closer does, both readers bind to one walker, and
# the guard drives every limb through both.
DECISION_RESOLVE_ID_CHAIN = DECISION_SUPERSEDE_ID_CHAIN
DECISION_RESOLVE_SEQ_CHAIN = DECISION_SUPERSEDE_SEQ_CHAIN

# THE self-status shapes on a `decision` event — the WRITE-TIME status
# snapshot (DECSHAPES1, closing SUPERSEQ1 delta #6/#7).
#
# THE READER/READER DISAGREEMENT THIS ENDS. `decision_match.load_open_decisions`
# has always honored a decision's own `data.status` — anything other than
# "superseded"/"resolved" is open — reading it through its `_decision_field`
# alias table (`status`, `state`; data scope first, then top level). The
# decision-log renderer never looked at the field at all, so the SAME decision
# was closed for the matcher and ACTIVE in the customer-facing view. That is
# the drift class with the readers swapped: not writer-vs-reader, but
# reader-vs-reader over a field one of them alone knows about.
#
# WHY THE NAIVE FIX IS WRONG. The obvious repair — "renderer: if status ==
# superseded, render superseded" — inverts latest-signal-wins. The status is
# stamped at CREATE time and never updated (nothing in the vocabulary rewrites
# an appended event); a `decision_reaffirmed` written afterwards is the exact
# event a human reaches for when they read the log and disagree with it, and
# WALKFIX1 FR-3 made supersede non-terminal precisely so that repair works. A
# self-status honored unconditionally would be terminal again — and worse than
# the bug it fixes, because it cannot even be pointed at by a repair event.
#
# THE FOLD. The self-field is a SIGNAL DATED AT THE DECISION'S OWN EVENT TIME,
# folded into the same latest-signal-wins ordering as every other closer. A
# reaffirm written LATER out-ranks it (the ruling is back); a reaffirm written
# EARLIER does not (it predates the statement it would be overturning). The
# decision's own time is the honest stamp: that is when the writer made the
# claim, and it is the earliest any signal on this decision can be — so a
# self-status is out-ranked by every genuine later repair and out-ranks nothing
# that came after it.
#
# Alias order mirrors `decision_match._decision_field` exactly (data scope
# across all aliases, then top level) so the two readers cannot disagree about
# WHICH field they read, only agree on what it means.
DECISION_SELF_STATUS_CHAIN = (
    ("data", "status"),
    ("data", "state"),
    ("", "status"),
    ("", "state"),
)

# The self-status values that CLOSE a ruling, normalized lowercase. Everything
# else — "active", "Active", a legacy spelling, or no status at all — is open.
# Kept as a mapping so the value a writer stamps and the status bucket a reader
# folds to are one decision, in one place.
DECISION_CLOSING_SELF_STATUSES = {
    "superseded": "superseded",
    "resolved": "resolved",
}


def _walk_supersede_chain(ev, chain):
    """All non-empty values along a (scope, field) chain, first-seen order,
    duplicates collapsed (one event spelling a target two ways names ONE
    target)."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    out = []
    for scope, field in chain:
        holder = data if scope == "data" else ev
        value = holder.get(field)
        if value in (None, ""):
            continue
        if not any(value == seen for seen in out):
            out.append(value)
    return out


def decision_supersede_targets(ev):
    """(target_ids, target_seqs) this event names as superseded rulings.

    - `decision_superseded`: every accepted spelling — id chain + seq chain,
      both scopes.
    - `decision`: the restamp link only (`supersedes_seq`, either scope),
      excluding its own seq — a decision can never retire itself.
    - any other type / non-dict: ((), ()) — nothing else is a
      decision-supersede carrier.

    THE single implementation both decision readers bind to (SUPERSEQ1). Do
    not re-derive these fields in a reader — that is the drift class this
    function retires.
    """
    if not isinstance(ev, dict):
        return ((), ())
    etype = ev.get("type")
    if etype == "decision_superseded":
        return (
            tuple(_walk_supersede_chain(ev, DECISION_SUPERSEDE_ID_CHAIN)),
            tuple(_walk_supersede_chain(ev, DECISION_SUPERSEDE_SEQ_CHAIN)),
        )
    if etype == "decision":
        own_seq = ev.get("seq")
        seqs = tuple(
            s for s in _walk_supersede_chain(ev, DECISION_RESTAMP_SEQ_CHAIN)
            if own_seq is None or s != own_seq
        )
        return ((), seqs)
    return ((), ())


def decision_resolve_targets(ev):
    """(target_ids, target_seqs) this event names as RESOLVED (closed-out)
    rulings.

    `decision_resolved` only — every accepted spelling, the same id + seq
    chains the supersede closer reads (see DECISION_RESOLVE_ID_CHAIN for why
    they are the same two chains). Any other type / non-dict: ((), ()).

    THE single implementation both decision readers bind to (DECSHAPES1).
    """
    if not isinstance(ev, dict):
        return ((), ())
    if ev.get("type") != "decision_resolved":
        return ((), ())
    return (
        tuple(_walk_supersede_chain(ev, DECISION_RESOLVE_ID_CHAIN)),
        tuple(_walk_supersede_chain(ev, DECISION_RESOLVE_SEQ_CHAIN)),
    )


def decision_self_status(ev):
    """The CLOSING self-status a `decision` event stamps on itself, normalized
    to "superseded" / "resolved" — or None when the decision is open.

    Reads DECISION_SELF_STATUS_CHAIN in priority order (data scope first, then
    top level), first non-empty value wins, matching
    `decision_match._decision_field` exactly. Comparison is
    case-insensitive and whitespace-tolerant: legacy writers stamp "Active",
    and "Superseded" must not read as open just because it is title-cased.

    Only a `decision` event carries a self-status — on an overlay event the
    `status` field would be the OVERLAY's own state, not the ruling's.

    This is a SIGNAL, not a verdict. Callers fold it at the decision's own
    event time into latest-signal-wins alongside supersede / resolve /
    reaffirm; a later reaffirm out-ranks it. See DECISION_SELF_STATUS_CHAIN
    for why honoring it unconditionally would be wrong.
    """
    if not isinstance(ev, dict) or ev.get("type") != "decision":
        return None
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for scope, field in DECISION_SELF_STATUS_CHAIN:
        holder = data if scope == "data" else ev
        value = holder.get(field)
        if value in (None, ""):
            continue
        return DECISION_CLOSING_SELF_STATUSES.get(str(value).strip().lower())
    return None


# THE non-dismissal closure reasons (SPEC REVAMN1 §0-3). Same one-list-not-two
# reasoning as the chain above, for a different pair of readers.
#
# WHAT `dropped` MEANS TO A LEARNER. Two passes mine closed commitments for a
# per-counterparty suppression signal — `capture_gate.propose_gate_directives`
# (proposes an observed-only override for a whole org) and
# `commitment_noise.analyze_noise` (proposes a never-track rule for a source).
# Both read `resolution == "dropped"` as the user saying "this was not worth
# tracking", and at enough of them they propose to STOP CAPTURING that
# counterparty. That inference is sound for the per-row Drop verb, where the
# user looked at one item and let it go.
#
# It is NOT sound for a bulk lapse. The review-tier verbs close a pile the user
# never adjudicated row by row: an expiry closes what nobody answered inside the
# window, and an ingest kill closes a cluster because ONE INGEST was bad. Read as
# dismissals they would teach the workspace to stop hearing a counterparty it has
# no complaint about — and REVAMN1 §0-3 rules the opposite way: a re-mention
# after an expiry is fresh evidence and must be captured again.
#
# So a `dropped` closure carrying one of these reasons is a lapse, not a
# dismissal. Everything else — including a hand Drop and the confirmed-tier
# age-out — is unchanged.
RESOLUTION_REASON_KEY = "resolution_reason"
REVIEW_EXPIRY_REASON = "review_expired"
INGEST_KILL_REASON = "ingest_killed"
# REFINT1 — the drain's terminal reason, on a `commitment_review_dismissed`
# (NOT a `commitment_resolved`: there is no commitment to resolve — that is
# the defect). A proposal whose target was never created is closed as
# unresolvable, and the event says so instead of the row staying invisible
# forever. Written under RESOLUTION_REASON_KEY — the SAME key the lapse
# family uses — so `is_non_dismissal_closure` is the one reader that tells a
# human's Skip from the system draining an orphan, and it is IN the frozenset
# below for the same reason the expiry and ingest-kill reasons are: a learner
# that read a drain tombstone as the CEO saying "not relevant" would move the
# calibration bands off events no human ever adjudicated
# (confidence_calibration.load_review_outcomes is the named reader).
DANGLING_TARGET_REASON = "target_never_created"

# POLICY1-A (M ruling 2026-09-03) — the reason an AUTOMATIC close states about
# itself: a later transcript said the work was done, the match met the close
# bar, and nobody was asked. It rides RESOLUTION_REASON_KEY on a
# `commitment_resolved` with `resolution: done` and travels with
# `data.confirmed_by: "transcript"` and the verbatim completion turn as the
# evidence.
#
# It is deliberately NOT in NON_DISMISSAL_RESOLUTION_REASONS: that frozenset
# names LAPSES (a `dropped` nobody answered), and this is a `done`. It has its
# own reader below for the one thing that must not mistake it for a human:
# Loop-4 calibration measures the confirm-rate of each band from proposal
# outcomes, and counting the machine's own closes as confirmations would teach
# the calibrator that its band is right on evidence no person ever graded
# (confidence_calibration.load_review_outcomes is the named reader).
AUTO_TRANSCRIPT_CLOSE_REASON = "auto_closed_transcript_evidence"
AUTO_CLOSE_CONFIRMED_BY = "transcript"
# POLICY1-B DD-7 (fix F-1) — the calendar closer's door, ADDED BESIDE the
# transcript's, never displacing it: a scheduling row (or an observed-tier
# scheduling guess) closed because the meeting it was about happened with
# the other side present. Same writer door (`close_commitment(confirmed_by=)`,
# requires a quote and a pointer), its own word so readers can tell them apart.
AUTO_CLOSE_CONFIRMED_BY_CALENDAR = "calendar"
AUTO_CLOSE_CONFIRMED_BY_VALUES = (AUTO_CLOSE_CONFIRMED_BY, AUTO_CLOSE_CONFIRMED_BY_CALENDAR)


def is_automatic_transcript_close(data) -> bool:
    """True when this `commitment_resolved` event's data marks the machine's
    own close on transcript evidence (M ruling 2026-09-03). Takes the event's
    `data` dict so neither the KEY nor the values are ever hand-spelled at a
    call site."""
    if not isinstance(data, dict):
        return False
    if str(data.get(RESOLUTION_REASON_KEY) or "").strip().lower() == AUTO_TRANSCRIPT_CLOSE_REASON:
        return True
    return str(data.get("confirmed_by") or "").strip().lower() == AUTO_CLOSE_CONFIRMED_BY


def is_automatic_calendar_close(data) -> bool:
    """POLICY1-B DD-7 — True when this `commitment_resolved` data marks the
    calendar closer's own close of an UNCONFIRMED guess (`confirmed_by:
    calendar`). Kept apart from the transcript reader on purpose: Loop-4
    calibration and the feed's `closed_unconfirmed` count are the
    transcript's; this one has its own feed line."""
    if not isinstance(data, dict):
        return False
    return str(data.get("confirmed_by") or "").strip().lower() == AUTO_CLOSE_CONFIRMED_BY_CALENDAR
# POLICY1-A (M ruling 2026-09-03, REVIEW_MERGED_v5280 F-2) — the review-expiry
# job's chip leg WITHDRAWS a standing chip nobody answered inside
# `PROPOSAL_TTL_DAYS`: a `commitment_review_dismissed` naming the proposal
# (`data.proposal_seq`) under this reason. It is the system retracting its own
# evidence line, not the customer saying "not relevant" — so it is IN the
# frozenset below for exactly the reason the dangling drain is: Loop-4
# calibration must not count it as a dismissal, and the independent-touch bar
# on `undo confirm` must not refuse the customer's own undo with "you skipped
# its review row" over a row the machine withdrew (probe P6 found both).
POLICY_RETRACT_REASON = "policy_retracted"
NON_DISMISSAL_RESOLUTION_REASONS: FrozenSet[str] = frozenset({
    REVIEW_EXPIRY_REASON,
    INGEST_KILL_REASON,
    DANGLING_TARGET_REASON,
    POLICY_RETRACT_REASON,
})

# REFINT1 — the commitment-REFERENCE family: event types whose data points at
# a commitment that a reader will assume exists. Lives HERE, beside
# COMMITMENT_CLOSURE_ID_CHAIN and THREAD_BOUND_TYPES, for the same
# one-list-not-two reason: the gate's reference wall (event_gate 4d) and any
# future read-side audit must answer "which types reference commitments"
# identically. `commitment_review_dismissed` is deliberately absent — a
# dismissal claims no work exists, and it is the drain's own terminal event.
COMMITMENT_REFERENCE_TYPES: FrozenSet[str] = frozenset({
    "commitment_review_proposed",
    "commitment_updated",
})


def is_non_dismissal_closure(data) -> bool:
    """True when this `commitment_resolved` event's data marks a LAPSE rather
    than a dismissal — i.e. a suppression learner must not count it.

    Takes the event's `data` dict so neither the KEY nor the values are ever
    hand-spelled at a call site: a reader that spells `resolution_reason`
    itself is a reader that can drift from the writer by one character and
    still be green.
    """
    if not isinstance(data, dict):
        return False
    reason = str(data.get(RESOLUTION_REASON_KEY) or "").strip().lower()
    return reason in NON_DISMISSAL_RESOLUTION_REASONS


# Legacy seq-alias id spellings (F2/F3): bare int 86, "86", "seq_86",
# "event_086", "commitment_seq_86" — all resolved read-side as "the commitment
# event at seq N". Lives here (the shared vocabulary home) because BOTH sides
# need it: commitment_state.normalize_commitment_id resolves these on read,
# and event_gate REJECTS an explicit data.id that matches this namespace on
# write (v4.5.2 R1c) — a custom id shaped like a seq alias would shadow seq
# references and make closures resolve to the wrong commitment.
import re as _re  # noqa: E402

LEGACY_SEQ_ID_RE = _re.compile(r"^(?:commitment_seq_|event_|seq_)?0*(\d+)$")


def load_event_types() -> FrozenSet[str]:
    """The canonical type enum, loaded once from events.schema.json.

    Returns an empty frozenset when the schema is missing/unparseable — the
    gatekeeper treats an empty enum as "validation unavailable" (it warns
    rather than rejecting everything on a broken install).
    """
    global _ENUM_CACHE
    if _ENUM_CACHE is None:
        try:
            schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
            enum = schema.get("properties", {}).get("type", {}).get("enum", [])
            _ENUM_CACHE = frozenset(t for t in enum if isinstance(t, str))
        except (OSError, json.JSONDecodeError):
            _ENUM_CACHE = frozenset()
    return _ENUM_CACHE


def is_known_type(event_type) -> bool:
    """True when `event_type` is registered in the schema enum. An empty enum
    (schema unreadable) admits everything — see load_event_types()."""
    types = load_event_types()
    if not types:
        return True
    return isinstance(event_type, str) and event_type in types


# --- Thread binding (THREADSTAMP1, 2026-08-23) ------------------------------
# ONE list, for the same "two lists WILL drift, one cannot" reason as the
# closure chain above — and this one had already drifted three ways when the
# defect was re-derived:
#
#   integrity_check C14   {meeting, interaction, commitment, decision,
#                          follow_up, note, insight}
#   backfill_substrate    {meeting, decision, draft_created, commitment,
#                          commitment_captured, follow_up, thread_resolved,
#                          memo, brief, call_prep}
#
# and `backfill_substrate` claimed in a comment that it "mirrors integrity_check
# C14's scope", which it did not. The detector counted six types the repair tool
# would not touch, and the repair tool touched six types the detector never
# counted. THREADSTAMP1 takes the UNION and puts it here, so the checker, the
# repair tool, the append gate's warning and the capture gate's derivation all
# read the same vocabulary.
#
# Union, not intersection: this set answers "would a missing thread id make this
# row invisible to a surface that filters on the field", and the answer is yes
# for every member of both lists. Widening the detector's scope raises C14's
# WARN count on existing workspaces — that count was always understated, and
# C14 is a warning, not a gate.
#
# DETECTION-ONLY MEMBERS. Six of these are NOT in the schema enum, so nothing
# can write one today (event_gate rejects an unregistered type on both entries):
# `brief`, `call_prep`, `commitment_captured`, `follow_up`, `insight`, `memo`.
# They stay in the set because both readers of it — C14 and the backfill — walk
# HISTORICAL substrate, where those rows exist. The write-side consumers (the
# gate warning, the capture-gate derivation) can never see them, so their
# presence costs nothing there. `run_threadstamp1_test` pins that split so a
# future registration or removal is a deliberate edit rather than a surprise.
THREAD_BOUND_TYPES: FrozenSet[str] = frozenset({
    "brief",
    "call_prep",
    "commitment",
    "commitment_captured",
    "decision",
    "draft_created",
    "follow_up",
    "insight",
    "interaction",
    "meeting",
    "memo",
    "note",
    "session_chapter",
    "thread_resolved",
})

# The payload spellings a thread reference is DERIVED FROM, in ladder order.
#
# THE CONSTRAINT THAT SHAPES THIS TUPLE (BUG-8330 item 12 / FX-5 / FIX ROUND 2).
# Presence of `primary_thread_id` is load-bearing for the personal firewall:
# `personal_leak.business_thread_id` RESOLVES every thread-id spelling on a row
# against the workspace's business thread register, and a resolving id is the
# override that lets a tie-touching row onto an org surface. So a derivation
# that promotes a ref INTO `primary_thread_id` must not hand the firewall a
# signal it could not already see, or the derivation quietly re-opens the leak
# that item 12 was filed for and that FX-5 re-opened twice.
#
# These three fields are exactly `personal_leak._THREAD_REF_FIELDS` minus the
# destination field, and the firewall reads them in BOTH scopes already. That
# makes the derivation provably FIREWALL-NEUTRAL: `is_personal`'s verdict on a
# row is identical before and after the stamp, because the resolver was already
# reading the source field. `run_threadstamp1_test` pins the tuple relationship
# AND the behavioural consequence.
#
# `related_thread_ids[0]` is DELIBERATELY NOT A RUNG, and that is a deviation
# from SPEC_THREADSTAMP1 DD-1's literal ladder, taken under the same spec's
# instruction that the BUG-8330 analysis constrains the derivation. That field
# is NOT in `_THREAD_REF_FIELDS`, so deriving from it is the one rung that
# WOULD move a row across the firewall — a personal-tie row whose sole related
# thread resolves to a business thread would go from withheld to rendered. It
# is also written by exactly ONE Python site repo-wide — `objective_state`'s
# `objective_created`, a type that is not in `THREAD_BOUND_TYPES` and that
# already carries a real `primary_thread_id` — so no row the ladder can reach
# has a related thread as its only reference: the rung buys nothing and costs
# the one property worth protecting here.
THREAD_REF_DERIVE_FIELDS = ("thread_id", "project_id", "primary_project_id")

# The canonical destination.
PRIMARY_THREAD_FIELD = "primary_thread_id"

# An org id parked in a thread slot is a type error, not a thread reference
# (backfill_substrate FIX 1 exists to relocate exactly these). Never derive one.
_ORG_ID_PREFIX = "org_"


def _thread_scope(ev):
    d = ev.get("data") if isinstance(ev, dict) else None
    return d if isinstance(d, dict) else {}


def derive_primary_thread_id(ev) -> Optional[str]:
    """The thread reference this event ALREADY CARRIES, promoted to a value the
    canonical slot can hold — or None when it carries none.

    Walks `THREAD_REF_DERIVE_FIELDS` in ladder order, envelope scope before
    `data` scope for each field (the order `backfill_substrate._derive_thread`
    has always used, kept verbatim so the gate and the repair tool derive
    identically — `run_threadstamp1_test` pins the parity).

    NEVER INVENTS AND NEVER RETURNS A SENTINEL. No thread reference in the
    payload means None, and every caller writes the field absent rather than
    empty. An `org_*` id in a thread slot is a type error and is skipped, not
    promoted.
    """
    if not isinstance(ev, dict):
        return None
    data = _thread_scope(ev)
    for field in THREAD_REF_DERIVE_FIELDS:
        for holder in (ev, data):
            value = holder.get(field)
            if not isinstance(value, str):
                continue
            value = value.strip()
            if value and not value.startswith(_ORG_ID_PREFIX):
                return value
    return None


def has_thread_ref(ev) -> bool:
    """True when the row carries ANY thread reference at all — the canonical
    field, a derivable spelling, or a non-empty `related_thread_ids`.

    This is the event gate's warning predicate, and it is deliberately WIDER
    than `derive_primary_thread_id`: a row whose only reference is a related
    thread is not derivable (see the tuple note above) but it is also not
    thread-LESS, and warning about it would be false. The warning fires only on
    rows that name no thread anywhere.
    """
    if not isinstance(ev, dict):
        return False
    if derive_primary_thread_id(ev) is not None:
        return True
    data = _thread_scope(ev)
    for holder in (ev, data):
        value = holder.get(PRIMARY_THREAD_FIELD)
        if isinstance(value, str) and value.strip():
            return True
        related = holder.get("related_thread_ids")
        if isinstance(related, list) and any(
            isinstance(r, str) and r.strip() for r in related
        ):
            return True
    return False


# --- Pre-registry fossils (2026-07-25) --------------------------------------
# Event types that EXIST in live substrate but are deliberately NOT in the
# enum. Surveyed on a real workspace: 52 unregistered types across ~200 rows,
# every one of them written before the append gate went strict on 2026-07-02
# (event_gate Phase 4) — the newest fossil write is dated 2026-07-02 itself.
# Nothing has written an unregistered type since, because nothing CAN: the
# gate raises EventGateError on both entries.
#
# They stay unregistered on purpose. The enum is the WRITE permission list —
# `is_known_type` is consulted by exactly one caller, event_gate, on append.
# Registering these would re-legalize writing them and undo the drift fix the
# registry exists for. It would also break the registry's own admission rule
# (EVENT_TYPES.md: a registered type names a writer AND a named consumer);
# these have neither. run_source_of_truth_test Check 4 is green, which is the
# proof no current skill prose or shared script declares one as a write.
#
# This set is the READ-side companion: it lets an auditor tell "expected
# historical row" from "new unregistered type, which is a defect". It is
# NEVER consulted on the write path.
#
# Extending it: only for a type already present in shipped substrate and
# written before 2026-07-02. A type you want to write goes in the enum, with
# a writer + consumer row in EVENT_TYPES.md — never here.
PRE_REGISTRY_FOSSILS: FrozenSet[str] = frozenset({
    "apply_choices_audit", "apply_choices_dispatch", "apply_choices_dispatched",
    "apply_choices_processed", "apply_dispatch", "artifact_refreshed",
    "artifact_updated", "chat_action", "cleanup_residue_removed",
    "commitment_update", "correction", "corruption-recovery",
    "cracks_watch_action", "cracks_watch_run", "cracks_watch_snooze",
    "decision_pending", "entity_creation_requested", "entity_search",
    "follow_up", "follow_up_draft", "list_item_added", "meeting_reprocessed",
    "noise_filter_review_needed", "org_added", "org_archived", "org_deleted",
    "org_membership", "org_proposal_confirmed", "org_review_pending",
    "outreach_drafted", "owner_remap", "packaging_problem",
    "pending_enrichment", "pending_review", "pending_review_resolved",
    "pending_review_skipped", "person_context_captured", "person_context_note",
    "person_enrichment_pending", "person_merge_proposed",
    "person_record_review_queued", "person_review_pending", "probe_click",
    "project_status_change", "prospect_stage_changed", "reclassification_batch",
    "scan_completed", "schedule_skipped", "schedule_updated", "session_close",
    "session_end", "substrate_cleanup",
})


def is_pre_registry_fossil(event_type) -> bool:
    """True when `event_type` is a documented pre-gate historical type.

    Read-side only. A row of this type in events.jsonl is expected and not an
    install defect; a row of ANY OTHER unregistered type is. Never call this
    from a write path — `is_known_type` is the write gate, and a fossil is
    deliberately not writable.
    """
    return isinstance(event_type, str) and event_type in PRE_REGISTRY_FOSSILS


__all__ = [
    "KIND_VALUES",
    "LEGACY_SEQ_ID_RE",
    "COMMITMENT_CLOSURE_ID_FIELDS",
    "DECISION_SUPERSEDE_ID_CHAIN",
    "DECISION_SUPERSEDE_SEQ_CHAIN",
    "DECISION_RESTAMP_SEQ_CHAIN",
    "DECISION_RESOLVE_ID_CHAIN",
    "DECISION_RESOLVE_SEQ_CHAIN",
    "DECISION_SELF_STATUS_CHAIN",
    "DECISION_CLOSING_SELF_STATUSES",
    "decision_supersede_targets",
    "decision_resolve_targets",
    "decision_self_status",
    "PRE_REGISTRY_FOSSILS",
    "RESOLUTION_REASON_KEY",
    "REVIEW_EXPIRY_REASON",
    "INGEST_KILL_REASON",
    "DANGLING_TARGET_REASON",
    "COMMITMENT_REFERENCE_TYPES",
    "NON_DISMISSAL_RESOLUTION_REASONS",
    "THREAD_BOUND_TYPES",
    "THREAD_REF_DERIVE_FIELDS",
    "PRIMARY_THREAD_FIELD",
    "derive_primary_thread_id",
    "has_thread_ref",
    "is_non_dismissal_closure",
    "load_event_types",
    "is_known_type",
    "is_pre_registry_fossil",
]


if __name__ == "__main__":
    types = sorted(load_event_types())
    print(f"{len(types)} registered event types")
    for t in types:
        print(f"  {t}")
