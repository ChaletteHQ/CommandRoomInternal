#!/usr/bin/env python3
"""READER1 — the canonical substance-event classifier.

Promoted from the binding-gauge audit script (SPEC_READER1 §6.1, feasibility
#5b): the coverage denominator and the gauge's repeatability both need ONE
shared notion of "substance" — the events a human would call the record of the
work (commitments, decisions, meetings, interactions, drafted artifacts) as
opposed to the system's own bookkeeping (gate runs, pack runs, connector
reads, cleanup sweeps).

Producer/consumer pairing: the gauge (R0/R3) writes per-thread coverage using
this classifier; `load_thread_knowledge` reports that persisted verdict. If
the two ever counted "substance" differently the ratio would be fiction, which
is why this module exists instead of two private copies.

Pure, stdlib-only, import-safe from anywhere. No I/O, no workspace access.
"""
from __future__ import annotations

__all__ = [
    "FAMILY_OF_TYPE",
    "SUBSTANCE_FAMILIES",
    "NAMED_SYSTEM_TYPES",
    "family_of",
    "is_substance_type",
    "is_substance_event",
    "event_type_of",
]

# ---------------------------------------------------------------------------
# Family table (verbatim promotion of the gauge's `fam(...)` registrations)
# ---------------------------------------------------------------------------

FAMILY_OF_TYPE: dict[str, str] = {}


def _fam(family: str, *types: str) -> None:
    for t in types:
        FAMILY_OF_TYPE[t] = family


_fam("commitment", "commitment", "commitment_observed", "commitment_updated",
     "commitment_reopened", "commitment_to_discuss", "commitment_update",
     "commitment_partial_received", "commitment_reassigned",
     "commitment_reclassified", "commitment_superseded")
_fam("commitment_resolved", "commitment_resolved")
_fam("decision", "decision", "decision_pending", "decision_reaffirmed",
     "decision_revisit_scheduled")
_fam("decision_resolved", "decision_resolved", "decision_superseded")
_fam("meeting", "meeting", "meeting_processed", "meeting_reprocessed",
     "meeting_scheduled")
_fam("interaction", "interaction", "note", "intel_logged", "email_sent",
     "email_drafted", "email_outcome", "outreach_sent", "outreach_drafted",
     "follow_up", "follow_up_draft", "followup_pack_drafted", "reminder")
_fam("other_substance", "memo_drafted", "one_pager_drafted",
     "decision_memo_drafted", "contract_reviewed", "board_pack_assembled",
     "deal_created", "deal_won", "deal_lost", "thread_resolved",
     "thread_resurrected", "status_change", "project_status_change",
     "prospect_stage_changed", "file_filed", "day_intent")

# The families that count as substance. commitment_resolved / decision_resolved
# ARE substance: a closure is a fact about the work, not about the system.
SUBSTANCE_FAMILIES: frozenset[str] = frozenset({
    "commitment", "commitment_resolved", "decision", "decision_resolved",
    "meeting", "interaction", "other_substance",
})

# System types kept VISIBLE under their own name (the gauge's per-thread
# family breakdown lists them individually rather than lumping into
# other_system). Everything else unrecognized classifies as "other_system".
NAMED_SYSTEM_TYPES: frozenset[str] = frozenset({
    "gate_ran", "pack_run", "connector_read", "cleanup_run", "thread_updated",
})


def family_of(ev_type: str | None) -> str:
    """Family of a raw event-type string.

    Returns one of SUBSTANCE_FAMILIES, a NAMED_SYSTEM_TYPES member (kept
    visible under its own name), or "other_system". Unknown/None fails to
    "other_system" — an unregistered new type is never silently substance,
    so the coverage denominator can only undercount, not inflate.
    """
    fam = FAMILY_OF_TYPE.get(ev_type or "")
    if fam:
        return fam
    if ev_type in NAMED_SYSTEM_TYPES:
        return ev_type
    return "other_system"


def is_substance_type(ev_type: str | None) -> bool:
    """True when the raw type string classifies into a substance family."""
    return family_of(ev_type) in SUBSTANCE_FAMILIES


def event_type_of(ev: dict) -> str | None:
    """The event's type across both schema layers (`type`, legacy `event`)."""
    if not isinstance(ev, dict):
        return None
    t = ev.get("type") or ev.get("event")
    return t if isinstance(t, str) and t else None


def is_substance_event(ev: dict) -> bool:
    """True when the event dict is a substance event. Never raises; a junk
    row is not substance."""
    return is_substance_type(event_type_of(ev))
