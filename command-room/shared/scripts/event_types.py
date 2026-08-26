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
NON_DISMISSAL_RESOLUTION_REASONS: FrozenSet[str] = frozenset({
    REVIEW_EXPIRY_REASON,
    INGEST_KILL_REASON,
    DANGLING_TARGET_REASON,
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
