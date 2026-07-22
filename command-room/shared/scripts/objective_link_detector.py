#!/usr/bin/env python3
"""Objective-link detector (SPEC OBJ2 §1B — Staff Meeting card OBJECTIVES
rail; the config_drift_detector pattern applied to objectives).

THE CONTRACT (OBJ2: Staff Meeting card OBJECTIVES group + the
apply-choices objective_link handlers): the card lists "provisional
classifications targeting objective threads + any open objective-kind
proposals". This module mechanizes the FIRST half: a captured
event whose classification envelope provisionally targets an OPEN standing
objective gets ONE link proposal — `propose(kind="objective_link",
tier="confirm")` on the Living Brain rail, rendered on the STAFF MEETING only
(`surface_hint` — adjudication is never urgent, so it never reaches the daily
card). No capture pipeline is hooked and nothing is re-extracted from content:
the detector consumes the stamps the envelope already carries.

WHAT COUNTS AS A PROVISIONAL CLASSIFICATION (mechanical, never inferred):
  - The event carries the classification envelope (`primary_thread_id` /
    `related_thread_ids` / `classification_confidence` — events.schema.json)
    and one of those thread ids is an open objective thread
    (kind='objective', per objective_state.list_open_objectives).
  - PROVISIONAL = `classification_confidence` below the auto-attach band
    (`meeting_capture.PENDING_REVIEW_CONFIDENCE_FLOOR`, the schema's
    ">=0.75 auto, <0.75 provisional/low-confidence" boundary —
    ORG_AND_THREAD_MODEL.md § classification bands), OR the writer's own
    `data.pending_review: true` stamp (the v4.5.2 safety inversion: absence
    of the flag is an assertion of high-confidence attribution).

WHAT NEVER PROPOSES:
  - Already bound: the event's `primary_thread_id` is the objective's
    anchor thread or one of its activity-binding `entity_ids` — the signal
    already belongs to a linked thread, so no attribution is needed
    (SPEC OBJ1 "Relevance capture"; the movement read joins on linked ids).
  - Already adjudicated-confirmed: a `brain_proposal_resolved` tombstone
    with this fingerprint and user_action applied/edited — the OBJ2 rule
    "adjudicated ones never re-list" (applied/edited tombstone dedup).
    (applied/edited are NOT ledger
    cooldowns — proposal_ledger's contract — so propose() alone would
    re-emit; this is the one dedup the detector must own.)
  - Open-row duplicates and the 60d decline cooldown are `propose()`'s own
    machinery — deliberately NOT pre-filtered here.
  - Masked/personal-lane events: reads go ONLY through the org-scoped seam
    (`events_io.load_events_org_scoped` — the same seam
    brain_proposals._load_events uses), so nothing masked can reach a
    proposal payload.

Fingerprint: `objective_link:<objective_id>:<target_id>` where target_id is
the STABLE id of the provisionally-linked item (`data.id` /
`data.commitment_id`, minted-once by event_gate; else the append-only `seq`)
— same run, same id, so dedup holds.

stdlib only; all reads defensive; never raises into a caller.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The auto-attach boundary, imported — not re-chosen: at/above this the
# envelope's attribution is asserted (auto band); below it the classification
# is provisional (events.schema.json classification_confidence bands).
from meeting_capture import PENDING_REVIEW_CONFIDENCE_FLOOR  # noqa: E402

_LINK_ACTIONS = [{"action": "confirm proposal"},
                 {"action": "dismiss proposal"},
                 {"action": "snooze proposal 7d"}]


def _load_events(workspace_root) -> list[dict]:
    """The org-scoped seam, verbatim from brain_proposals._load_events: the
    account mask + personal-lane drop apply by design, so a masked account's
    history can never drive a proposal row here."""
    try:
        from events_io import load_events_org_scoped
    except ImportError:
        from pathlib import Path as _Path
        sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from events_io import load_events_org_scoped

    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not path.exists():
        return []
    events, _skipped = load_events_org_scoped(workspace_root)
    # OBJ2 consumer fix: honor supersession before detecting — a dismissed
    # link's unlink reclassification removes the objective from the source
    # event's envelope, so the pairing can never re-propose after the 60d
    # ledger cooldown lapses (the reclassification IS the permanent answer;
    # the cooldown was only ever the short fence).
    from thread_activity import apply_reclassifications
    return apply_reclassifications(events)


def _data(ev: dict) -> dict:
    return ev.get("data") if isinstance(ev.get("data"), dict) else {}


def _stable_target_id(ev: dict) -> Optional[str]:
    """The provisionally-linked item's stable id: a minted `data.id` /
    `data.commitment_id` (written once, never re-minted — event_gate), else
    the append-only `seq`. No stable handle → no fingerprint → skip."""
    data = _data(ev)
    for key in ("id", "commitment_id"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    seq = ev.get("seq")
    if isinstance(seq, int):
        return f"seq{seq}"
    return None


def _is_provisional(ev: dict) -> bool:
    """The discovery-derived boundary: below the auto-attach band, or the
    writer's own pending_review stamp. A null/absent confidence with no flag
    is 'no meaningful classification' (schema) — not provisional."""
    if _data(ev).get("pending_review") is True:
        return True
    conf = ev.get("classification_confidence")
    return (isinstance(conf, (int, float))
            and conf < PENDING_REVIEW_CONFIDENCE_FLOOR)


def _envelope_thread_ids(ev: dict) -> list:
    ids = []
    primary = ev.get("primary_thread_id")
    if isinstance(primary, str) and primary:
        ids.append(primary)
    related = ev.get("related_thread_ids")
    if isinstance(related, list):
        ids.extend(t for t in related if isinstance(t, str) and t)
    return ids


def _confirmed_fingerprints(events: List[dict]) -> set:
    """objective_link fingerprints already adjudicated as confirmed
    (applied/edited tombstones). Declined/superseded ride propose()'s own
    ledger cooldown and are NOT collected here."""
    out = set()
    for ev in events:
        if ev.get("type") != "brain_proposal_resolved":
            continue
        data = _data(ev)
        if (data.get("kind") == "objective_link"
                and data.get("user_action") in ("applied", "edited")
                and data.get("fingerprint")):
            out.add(data["fingerprint"])
    return out


def _item_title(ev: dict) -> str:
    data = _data(ev)
    for key in ("title", "summary"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:120]
    return f"a captured {ev.get('type') or 'item'}"


def detect_objective_links(workspace_root) -> List[dict]:
    """Pure DETECT: [{objective_id, objective_name, target_id, fingerprint,
    item_title, source_event_seq}] for every provisional classification
    targeting an open objective thread that is not already bound or
    adjudicated-confirmed. Reads the org-scoped event seam + entities;
    writes NOTHING."""
    try:
        from objective_state import list_open_objectives
        open_rows = list_open_objectives(workspace_root)
    except Exception:
        return []
    objectives: dict[str, dict] = {}
    for row in open_rows:
        if row.get("malformed") or not row.get("thread_id"):
            continue  # a corrupt objective is a recreation surface, not a
            # link target — readers own that ask
        obj = row.get("objective") or {}
        binding = obj.get("binding") if isinstance(obj.get("binding"), dict) else {}
        bound = {t for t in (binding.get("entity_ids") or [])
                 if isinstance(t, str)}
        anchor = obj.get("anchor_thread_id")
        if isinstance(anchor, str) and anchor:
            bound.add(anchor)
        objectives[row["thread_id"]] = {"name": row.get("name") or row["thread_id"],
                                        "bound": bound}
    if not objectives:
        return []

    events = _load_events(workspace_root)
    if not events:
        return []
    confirmed = _confirmed_fingerprints(events)

    out: List[dict] = []
    seen: set = set()
    for ev in events:
        if not _is_provisional(ev):
            continue
        env_ids = _envelope_thread_ids(ev)
        targets = [t for t in env_ids if t in objectives]
        if not targets:
            continue
        target_id = _stable_target_id(ev)
        if target_id is None:
            continue
        primary = ev.get("primary_thread_id")
        for obj_id in targets:
            if primary and primary != obj_id and primary in objectives[obj_id]["bound"]:
                continue  # signal already belongs to a linked/anchor thread —
                # bound; the movement read joins on the linked ids
            fingerprint = f"objective_link:{obj_id}:{target_id}"
            if fingerprint in confirmed or fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append({
                "objective_id": obj_id,
                "objective_name": objectives[obj_id]["name"],
                "target_id": target_id,
                "fingerprint": fingerprint,
                "item_title": _item_title(ev),
                # The dismiss handler's supersedes_seq: it must ride the
                # proposal payload — dispatch never re-derives it from content
                # (OBJ2 §2).
                "source_event_seq": (ev.get("seq")
                                     if isinstance(ev.get("seq"), int)
                                     else None),
            })
    return out


def run_objective_link_detector(workspace_root) -> dict:
    """The weekly cleanup entry point: detect, then propose ONE
    `objective_link` row per provisional link through
    `brain_proposals.propose()` (which supplies the once-per-link discipline:
    open-row fingerprint dedup + the 60d decline cooldown ledger). Returns
    {candidates, proposed, suppressed} counts. Two consecutive runs mint no
    duplicates — the second run's rows all come back suppressed."""
    from brain_proposals import propose

    candidates = detect_objective_links(workspace_root)
    proposed, suppressed = 0, 0
    for c in candidates:
        evidence = (f"{c['item_title']} was provisionally tied to "
                    f"{c['objective_name']} — the match was not strong enough "
                    "to link automatically")
        try:
            r = propose(
                workspace_root,
                kind="objective_link",
                tier="confirm",
                fingerprint=c["fingerprint"],
                detector="objective-link",
                evidence=evidence,
                action_tuples=list(_LINK_ACTIONS),
                render_line=(f"{c['item_title']} looks like a move on "
                             f"{c['objective_name']} — link it?"),
                extra={
                    "surface_hint": "staff-meeting",
                    "objective_id": c["objective_id"],
                    "target_id": c["target_id"],
                    "source_event_seq": c["source_event_seq"],
                    "title": f"{c['objective_name']} — proposed link",
                },
            )
        except Exception:
            suppressed += 1
            continue
        if r.get("status") == "proposed":
            proposed += 1
        else:
            suppressed += 1
    return {"candidates": len(candidates), "proposed": proposed,
            "suppressed": suppressed}


__all__ = [
    "PENDING_REVIEW_CONFIDENCE_FLOOR",
    "detect_objective_links",
    "run_objective_link_detector",
]
