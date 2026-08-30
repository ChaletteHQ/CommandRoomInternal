#!/usr/bin/env python3
"""thread_resolve — SPEC THREADBIND1: ONE shared resolver for write-time
thread binding.

WHY THIS EXISTS. A live-workspace audit (2026-08-26) found 1,955 movement
events with NO thread binding: prep_brief 277/277 (100% — its only link is a
raw calendar `meeting_id`), interaction ~83%, commitment_resolved ~40%,
commitment ~25%, meeting_processed ~34%. BUG-8244 fixed this class for
`meeting` events (`meeting_capture.build_meeting_event`'s caller resolves the
project and passes `primary_thread_id` in) and never generalized. This module
is the generalization: one resolver, importable by any canonical writer that
has a workspace_root and some evidence in hand at the moment it is about to
append an event with no thread id.

THE CHAIN (SPEC §0 ruling 1, evidence order — first hit wins):
  0. Explicit caller id. A caller that already knows the thread need not
     guess — `resolve_thread_binding` is a no-op pass-through for this case,
     confidence 1.0.
  1. The MEETING'S OWN BINDING (BUG-8244 machinery). If the evidence names a
     meeting (`meeting_id` / `source_ref`), and a `meeting` event for it
     already carries a resolved `primary_thread_id` (or a `prep_brief`
     receipt for it already carries a resolved `data.thread_id` — THREADBIND1
     ruling 3), inherit that binding. The meeting was already resolved once;
     re-deriving it from attendees a second time would be strictly worse
     evidence than what is already on file.
  2. Attendees' org -> the org's PRIMARY thread — call-prep's own §2 rule
     ("Project resolution", `skills/call-prep/SKILL.md`), reused rather than
     re-derived: `entity_resolve.linked_projects_for_org` is the SAME
     candidate computation `entity_resolve.resolve_to_linked_project` uses
     for its org branch (extracted in this spec, zero duplication — the
     equivalence pin in `tests/run_thread_resolve_test.py` proves it).
     Exactly ONE non-archived thread under the attendees' org is confident
     evidence (mirrors `shared/PASSIVE_CAPTURE.md`'s "org clustering" band,
     confidence 0.55); two or more is a QUESTION, not weak evidence — same
     posture `attendee_evidence.find_evidence`'s ambiguity fence takes for
     identity (two matches under one name refuses rather than picks one).
  3. No evidence clears the floor -> UNBOUND. That is TODAY's behavior, not
     a regression: an event that would have shipped with `primary_thread_id:
     None` before this module existed still does. The floor never drops
     (SPEC's own fleet rule) — infer, never ask, and never block a write on
     a guess.

BELOW THE FLOOR: `bind_event_thread` leaves the event exactly as built
(byte-identical) and, when there is something concrete to ask about (the
`org_ambiguous` case — this module never proposes on bare `no_evidence`,
there is nothing for a human to confirm there), fires ONE
`brain_proposals.propose(kind="thread_binding", tier="confirm")` row through
the EXISTING adjudication queue (SPEC §0 ruling 2 — the PERSONLOOP1 lane).
No new cap is invented here: `select_confirm_card`'s standing
`MAX_SLOTS_PER_DETECTOR` / `DAILY_CONFIRM_CAP` and `expire_stale`'s TTL sweep
already bound and age out this detector's rows exactly like every other one.

PURE ABOVE THE WRITE LINE. `resolve_thread_binding` reads entities.json / events.jsonl
and writes nothing. Only `propose_binding_row` (called from
`bind_event_thread` for the ambiguous case) writes, and it writes through the
one sanctioned entry point (`brain_proposals.propose`) — no second proposal
rail.

stdlib only except the plugin's own shared/scripts imports.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Mirrors PASSIVE_CAPTURE.md's "org clustering" confidence band exactly
# (§ Thread Resolution, step 4: "All involved people affiliate to the same
# org, and that org has exactly one active thread of the relevant kind.
# Confidence: 0.55."). Below this, a write stays unbound (today's behavior).
BIND_CONFIDENCE_FLOOR = 0.55

CONFIDENCE_EXPLICIT = 1.0
CONFIDENCE_MEETING_BINDING = 0.9
CONFIDENCE_ORG_SINGLE_THREAD = BIND_CONFIDENCE_FLOOR
CONFIDENCE_NONE = 0.0

BASIS_EXPLICIT = "explicit"
BASIS_MEETING_BINDING = "meeting_binding"
BASIS_ORG_SINGLE_THREAD = "org_single_thread"
BASIS_ORG_AMBIGUOUS = "org_ambiguous"
BASIS_ATTENDEE_ORG_AMBIGUOUS = "attendee_org_ambiguous"
BASIS_NO_ORG_THREAD = "no_org_thread"
BASIS_NO_EVIDENCE = "no_evidence"

# Bases the ambiguity queue row is worth proposing for — there is a real
# candidate set a human can tap between. `no_evidence` / `no_org_thread`
# have nothing concrete to confirm, so this module stays silent on them
# (SPEC's fail-open posture: uncertain-with-a-question surfaces; uncertain-
# with-nothing-to-ask does not become a nag).
PROPOSABLE_BASES = frozenset({BASIS_ORG_AMBIGUOUS, BASIS_ATTENDEE_ORG_AMBIGUOUS})

DETECTOR_NAME = "thread-resolve"
PROPOSAL_KIND = "thread_binding"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _entities_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "entities.json"


def _load_entities(workspace_root) -> dict:
    import json

    p = _entities_path(workspace_root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _txt(v) -> str:
    return v.strip() if isinstance(v, str) else ""


# ---------------------------------------------------------------------------
# Step 1 — the meeting's own binding (BUG-8244 machinery)
# ---------------------------------------------------------------------------


#: The one additive marker `scripts/backfill_prep_briefs.py` writes — a
#: `prep_brief` receipt is append-only, so a POST-HOC bind cannot mutate it
#: in place (SPEC THREADBIND1 §0 ruling 3, the `commitment_reclassified`
#: precedent applied to a different append-only record). See
#: `shared/EVENT_TYPES.md` § "Thread-binding lane".
BACKFILL_MARKER_TYPE = "prep_brief_thread_backfilled"
BACKFILL_UNDO_TYPE = "prep_brief_thread_backfill_undone"


def _meeting_bound_thread_id(workspace_root, *, meeting_id=None,
                              source_ref=None) -> Optional[str]:
    """Has this meeting already been resolved once? Reads (never writes)
    every `meeting` event, every `prep_brief` receipt (THREADBIND1 ruling 3
    — a receipt binds going forward too), and every still-live
    `prep_brief_thread_backfilled` marker (the 30-day one-shot's own write —
    a marker with a later `prep_brief_thread_backfill_undone` for the same
    meeting reads as ABSENT, not bound) whose reference matches, and returns
    the first non-empty resolved thread id found.

    Matching goes through `meeting_capture.meeting_ref_keys` — the SAME
    normalization `already_processed` / the End of Day catch-up sweep use —
    so this agrees with the rest of the product about what "the same
    meeting" means rather than inventing a second definition.
    """
    ref = _txt(meeting_id) or _txt(source_ref)
    if not ref:
        return None
    try:
        from meeting_capture import meeting_ref_keys
    except ImportError:
        return None
    wanted = meeting_ref_keys(ref)
    if not wanted:
        return None

    # Org-scoped (D4c/PGUARD1 — the mask is the default, not an opt-in): a
    # meeting/prep_brief/backfill-marker binding is business context, never
    # personal-lane, so masking out an account-reclassified or
    # personal-tie row here is correct, not merely compliant — the same
    # posture `brain_proposals._load_events` takes.
    from events_io import load_events_org_scoped

    events, _skipped = load_events_org_scoped(workspace_root)

    undone_refs = set()
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == BACKFILL_UNDO_TYPE:
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            mid = _txt(data.get("meeting_id"))
            if mid:
                undone_refs |= meeting_ref_keys(mid)

    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if etype == "meeting":
            candidate_ref = data.get("source_ref")
            tid = _txt(ev.get("primary_thread_id"))
        elif etype == "prep_brief":
            candidate_ref = data.get("meeting_id")
            tid = _txt(data.get("thread_id"))
        elif etype == BACKFILL_MARKER_TYPE:
            candidate_ref = data.get("meeting_id")
            if meeting_ref_keys(candidate_ref) & undone_refs:
                continue
            tid = _txt(data.get("thread_id"))
        else:
            continue
        if not tid:
            continue
        if meeting_ref_keys(candidate_ref) & wanted:
            return tid
    return None


# ---------------------------------------------------------------------------
# Step 2 — attendees' org -> the org's primary thread
# ---------------------------------------------------------------------------


def _person_org_ids(entities: dict, person_id: str) -> set:
    """The org(s) a person's record asserts. `primary_org_id` when set (the
    schema's own primacy field — the one `entity_resolve`'s person branch
    reads too); else their `affiliation_ids`, ALL of them. REVIEW THREADBIND1
    F3: the first cut fell back to `affiliation_ids[0]`, but the schema
    documents NO ordering on that list ("all orgs this person is affiliated
    with") — for a multi-affiliation person with no primary, entry [0] is an
    ARBITRARY pick, and binding an org's single thread off it produced a
    confident (floor-confidence) bind that flipped with list order. A
    multi-org person with no declared primary IS ambiguous evidence, so all
    their orgs flow into the caller's set and the size>1 refusal does its
    job (PASSIVE_CAPTURE band 4's own wording — "all involved people
    affiliate to the SAME org" — is only satisfiable when the set collapses
    to one).

    SPEC THREADANN1 extracted this exact logic to
    `entity_resolve.person_org_ids` so `thread_subjects`'s cluster detector
    gets the identical org-evidence read; this wrapper delegates, unchanged
    behavior, pinned by the pre-existing `run_thread_resolve_test.py`."""
    from entity_resolve import person_org_ids

    return person_org_ids(entities, person_id)


def _evidence_org_ids(evidence: dict, workspace_root) -> set:
    """The distinct org ids implied by the evidence — direct `attendee_org_ids`
    first, else the union of each attendee person's asserted org(s)
    (`primary_org_id`, falling back to ALL of their `affiliation_ids` — see
    `_person_org_ids`). Multiple attendees naming DIFFERENT orgs — or one
    attendee whose record itself names several with no primary — is an
    ambiguity (there is no single "the attendees' org"), so the caller
    treats a set of size > 1 as unresolved, same as a multi-thread org."""
    direct = {_txt(o) for o in (evidence.get("attendee_org_ids") or ()) if _txt(o)}
    if direct:
        return direct
    person_ids = [_txt(p) for p in (evidence.get("attendee_person_ids") or ())
                  if _txt(p)]
    if not person_ids:
        return set()
    entities = _load_entities(workspace_root)
    orgs = set()
    for pid in person_ids:
        orgs |= _person_org_ids(entities, pid)
    return orgs


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def resolve_thread_binding(evidence: dict, *, workspace_root) -> dict:
    """`resolve_thread_binding(evidence) -> {"thread_id", "confidence", "basis",
    "candidates", "org_id"}` — SPEC §0 ruling 1's evidence chain, in order.
    PURE READ: touches entities.json and events.jsonl, writes nothing.

    `evidence` (all keys optional):
      explicit_thread_id  — caller already knows the thread; pass-through.
      meeting_id / source_ref — the meeting this write is about.
      attendee_person_ids  — person ids of attendees, evaluated for their
                              `primary_org_id`.
      attendee_org_ids     — org ids directly, bypassing person lookup.

    `candidates` is populated ONLY on `org_ambiguous` (the case a queue row
    can meaningfully ask about) — a list of `{"thread_id", "name"}`.
    """
    evidence = evidence or {}

    explicit = _txt(evidence.get("explicit_thread_id"))
    if explicit:
        return {"thread_id": explicit, "confidence": CONFIDENCE_EXPLICIT,
                "basis": BASIS_EXPLICIT, "candidates": [], "org_id": None}

    bound = _meeting_bound_thread_id(
        workspace_root, meeting_id=evidence.get("meeting_id"),
        source_ref=evidence.get("source_ref"))
    if bound:
        return {"thread_id": bound, "confidence": CONFIDENCE_MEETING_BINDING,
                "basis": BASIS_MEETING_BINDING, "candidates": [],
                "org_id": None}

    org_ids = _evidence_org_ids(evidence, workspace_root)
    if len(org_ids) > 1:
        return {"thread_id": None, "confidence": CONFIDENCE_NONE,
                "basis": BASIS_ATTENDEE_ORG_AMBIGUOUS, "candidates": [],
                "org_id": None}
    if len(org_ids) == 1:
        org_id = next(iter(org_ids))
        from entity_resolve import linked_projects_for_org

        candidates = linked_projects_for_org(workspace_root, org_id)
        if len(candidates) == 1:
            proj = candidates[0]
            return {"thread_id": proj.get("id"),
                    "confidence": CONFIDENCE_ORG_SINGLE_THREAD,
                    "basis": BASIS_ORG_SINGLE_THREAD, "candidates": [],
                    "org_id": org_id}
        if len(candidates) > 1:
            return {"thread_id": None, "confidence": CONFIDENCE_NONE,
                    "basis": BASIS_ORG_AMBIGUOUS,
                    "candidates": [
                        {"thread_id": c.get("id"),
                         "name": c.get("canonical_name") or c.get("display_name")
                                 or c.get("id")}
                        for c in candidates],
                    "org_id": org_id}
        return {"thread_id": None, "confidence": CONFIDENCE_NONE,
                "basis": BASIS_NO_ORG_THREAD, "candidates": [],
                "org_id": org_id}

    return {"thread_id": None, "confidence": CONFIDENCE_NONE,
            "basis": BASIS_NO_EVIDENCE, "candidates": [], "org_id": None}


# ---------------------------------------------------------------------------
# The queue row (fail-open, capped, ignorable — SPEC §0 ruling 2)
# ---------------------------------------------------------------------------


def _fingerprint(evidence: dict, result: dict) -> str:
    """One row per (org, candidate-set) — a repeat write against the same
    unresolved org does not mint a second row (propose()'s own fingerprint
    dedup + 60d decline cooldown apply, same as every other detector)."""
    org_id = result.get("org_id") or "none"
    ids = ",".join(sorted(c["thread_id"] for c in result.get("candidates", [])
                          if c.get("thread_id")))
    return f"thread_binding:{org_id}:{ids}"


def propose_binding_row(workspace_root, evidence: dict, result: dict, *,
                         detector: str = DETECTOR_NAME,
                         title: str = "",
                         source_event_seq: Optional[int] = None) -> dict:
    """Fire ONE `thread_binding` proposal through the EXISTING adjudication
    queue (`brain_proposals.propose`) for a below-floor, ASKABLE verdict
    (`basis` in `PROPOSABLE_BASES`). No-op (`{"status": "not_proposable"}`)
    for every other basis — there is nothing a human could usefully tap.

    Capped by the SAME standing mechanism every other detector rides
    (`select_confirm_card`'s `MAX_SLOTS_PER_DETECTOR` / `DAILY_CONFIRM_CAP`,
    `expire_stale`'s TTL sweep) — this module invents no cap of its own."""
    if result.get("basis") not in PROPOSABLE_BASES:
        return {"status": "not_proposable"}
    candidates = result.get("candidates") or []
    if not candidates:
        return {"status": "not_proposable"}

    from brain_proposals import propose

    names = ", ".join(c["name"] for c in candidates if c.get("name"))
    subject = title or "this item"
    render_line = (f"{subject} could belong to more than one thread "
                   f"({names}) — which one?")
    evidence_text = (f"the attendees' org has {len(candidates)} active "
                     f"threads and no single one is clearly the match")
    # No `surface_hint` — a thread-binding row is exactly the kind of "needs
    # your eyes" item the standing morning queue exists for (SPEC §0 ruling
    # 2), so it renders on the named daily surfaces like every other confirm-
    # tier row. `ON_DEMAND_SURFACE_HINT` ("on-demand") is a DEMOTION
    # sentinel (STAFFCUT §3.7) that HIDES a row from every named surface —
    # setting it here would silently suppress the very queue row this
    # ruling requires.
    extra = {"candidates": candidates, "title": subject}
    if result.get("org_id"):
        extra["org_id"] = result["org_id"]
    if source_event_seq is not None:
        extra["source_event_seq"] = source_event_seq

    return propose(
        workspace_root,
        kind=PROPOSAL_KIND,
        tier="confirm",
        fingerprint=_fingerprint(evidence, result),
        detector=detector,
        evidence=evidence_text,
        action_tuples=[{"action": "confirm"}, {"action": "dismiss proposal"}],
        render_line=render_line,
        org_id=result.get("org_id"),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# The writer-side integration point
# ---------------------------------------------------------------------------


def bind_event_thread(workspace_root, event: dict, *, evidence: dict = None,
                       detector: str = DETECTOR_NAME) -> dict:
    """Call this on an already-CONSTRUCTED event dict, right before a
    canonical writer appends it, when the event carries no explicit
    `primary_thread_id`. Mutates and returns `event`.

    BYTE-IDENTICAL WHEN ALREADY BOUND OR WHEN THERE IS NOTHING TO INFER: an
    event that already carries a `primary_thread_id`, or resolves to
    `no_evidence` / `no_org_thread`, comes back with no field touched — the
    floor is never below today (SPEC's fleet rule). Bound: stamps
    `primary_thread_id` + `data.thread_basis`. Ambiguous-with-candidates:
    leaves the event unbound and fires ONE capped queue row
    (`propose_binding_row`) so nothing is silently guessed and nothing is
    silently lost either.
    """
    if _txt(event.get("primary_thread_id")):
        return event
    result = resolve_thread_binding(evidence or {}, workspace_root=workspace_root)
    if result["thread_id"] and result["confidence"] >= BIND_CONFIDENCE_FLOOR:
        event["primary_thread_id"] = result["thread_id"]
        data = event.get("data")
        if not isinstance(data, dict):
            data = {}
            event["data"] = data
        data["thread_basis"] = result["basis"]
        return event
    if result["basis"] in PROPOSABLE_BASES:
        title = ""
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        title = _txt(data.get("title"))
        try:
            propose_binding_row(workspace_root, evidence or {}, result,
                                detector=detector, title=title,
                                source_event_seq=data.get("source_event_seq"))
        except Exception:
            # Fail-open (SPEC §0 ruling 2): a queue-row misfire must never
            # block or corrupt the write it is annotating. The event ships
            # unbound, exactly as it would have with no resolver at all.
            pass
    return event


__all__ = [
    "BIND_CONFIDENCE_FLOOR",
    "CONFIDENCE_EXPLICIT",
    "CONFIDENCE_MEETING_BINDING",
    "CONFIDENCE_ORG_SINGLE_THREAD",
    "CONFIDENCE_NONE",
    "BASIS_EXPLICIT",
    "BASIS_MEETING_BINDING",
    "BASIS_ORG_SINGLE_THREAD",
    "BASIS_ORG_AMBIGUOUS",
    "BASIS_ATTENDEE_ORG_AMBIGUOUS",
    "BASIS_NO_ORG_THREAD",
    "BASIS_NO_EVIDENCE",
    "PROPOSABLE_BASES",
    "DETECTOR_NAME",
    "PROPOSAL_KIND",
    "resolve_thread_binding",
    "propose_binding_row",
    "bind_event_thread",
]
