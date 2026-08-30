#!/usr/bin/env python3
"""thread_subjects — SPEC THREADANN1: heavy threads get subjects, not
surgery.

WHY THIS EXISTS. The heaviest live thread in the fleet (project_015
"Business / GTM": 1,174 events, 702 in August alone, kind says `product`,
name says GTM) is really product + go-to-market + client work in one
folder — a brief drawn from a mixed folder is mush. But "operator notices
and reorganizes" does not exist at a client, and moving client records on a
heuristic tested on one workspace is the brittleness the fleet rule exists
to catch (CLUSTFAM1 — M's standing ruling: annotation beats fence).

SPEC §0 rulings, applied:
  1. RECORDS NEVER MOVE. Detection ANNOTATES: a `subjects` layer on the
     thread (derived labels + per-event subject tags), stored as
     `thread_annotation` events through the canonical writer here. The
     client's structure is untouched; undo is trivial (the annotation is
     just not re-written); nothing can orphan a record.
  2. Detection is EVIDENCE-SCORED, PROPOSE-FIRST: subject clusters derive
     from bound events (THREADBIND1's `thread_basis` + `org_ids` +
     `person_ids`-resolved-orgs + commitment/decision/interaction text).
     Below the floor -> no annotation, no noise. Runs in the weekly cleanup
     job (fleet cadence, zero new tasks — see skills/cleanup/SKILL.md
     Phase 3.5a-quater).
  3. CONSUMERS READ SUBJECTS, and only consumers change: call-prep and
     one-pager scope "this thread, this subject" when subjects exist;
     identical output when none do (`scope_events_to_subject` below is the
     byte-identity fence — the CLUSTCOUNT1 posture: no subjects -> the SAME
     list object comes back, untouched).
  4. ACTUAL SPLITS are PROPOSE-ONLY, one tap, undoable: "this looks like N
     things — split it?" through the standing Living Brain queue
     (`brain_proposals`, kind=`thread_split`, tier=`confirm`) — the split
     executor reuses the annotation's cluster assignment; `tha_` undo batch;
     never auto-executed, never nagged (one proposal per thread per 30
     days, ledgered in `_split_proposed_recently`).

GENERIC BY DESIGN. `annotate_thread` / `subjects_for` are written around an
`annotation_kind`-tagged `thread_annotation` event, not a `thread_subjects`-
only event — CLUSTFAM1's capture-dedup consumer rides the SAME writer/event
shape next train with a different `annotation_kind`. Nothing here assumes
"subjects" is the only annotation a thread will ever carry.

THE CHAIN mirrors thread_resolve.py's own doctrine: pure detection function,
separate writer, separate reader, fail-open queue row. stdlib only except
the plugin's own shared/scripts imports.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The event this module writes/reads — kind-tagged, generic (see module
#: docstring "GENERIC BY DESIGN"). `data.annotation_kind` names the content
#: class; this build only ever writes `ANNOTATION_KIND_SUBJECTS`.
ANNOTATION_TYPE = "thread_annotation"
ANNOTATION_KIND_SUBJECTS = "subjects"

#: The propose-only split row's brain_proposals kind (SPEC §0 ruling 4).
#: Rides the SAME Living Brain rail thread_resolve.py's thread_binding rows
#: do — no new queue, no new cap (mirrors the "Thread-binding lane" EVENT_
#: TYPES.md doctrine: an existing-rail proposal needs no new event type).
SPLIT_PROPOSAL_KIND = "thread_split"

#: SPEC §0 ruling 4 — "never nagged (one proposal per thread per 30 days)".
#: Narrower than, and independent of, `proposal_ledger`'s shared 60d
#: decline-only cooldown: this floor applies regardless of how the last
#: proposal for this thread resolved (declined, applied, or still open).
SPLIT_LEDGER_DAYS = 30

#: `tha_<...>` — the split executor's brain_undo batch prefix (SPEC §0
#: ruling 4's "tha_ undo batch"), same mint shape as commitment_cluster's
#: `clu_` and backfill_prep_briefs's `thb_`.
BATCH_PREFIX = "tha_"

#: brain_undo.REVERSERS key this module registers (see brain_undo.py).
SPLIT_CHANGE_CLASS = "thread_split"

DETECTOR_NAME = "thread-subjects"

#: A cluster must carry at least this many bound events to be a subject,
#: not noise — the evidence-scoring floor's size half (SPEC §0 ruling 2).
MIN_CLUSTER_EVENTS = 3

#: Together, the accepted clusters must account for at least this fraction
#: of the thread's bound events — a clean split of 20% of a thread and a
#: shrug on the rest is not "this thread is really N things", it's noise
#: with a label. The confidence-scoring floor's coverage half.
MIN_COVERAGE = 0.5

#: The word-signal fallback used only for events that carry NO org
#: evidence (org evidence always wins when present — same primacy ordering
#: THREADBIND1's own evidence chain uses). Common short/structural words are
#: excluded so two events don't cluster on "would" or "about".
_STOPWORDS = frozenset({
    "about", "after", "again", "their", "there", "these", "those", "which",
    "while", "would", "could", "should", "there's", "we're", "they're",
    "meeting", "called", "discuss", "discussed", "update", "follow",
    "please", "thanks", "review", "action", "items", "notes",
})


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


def _load_thread(workspace_root, thread_id, *, entities=None) -> Optional[dict]:
    if entities is None:
        entities = _load_entities(workspace_root)
    from entities_io import entities_collection

    for t in entities_collection(entities, "threads"):
        if isinstance(t, dict) and t.get("id") == thread_id:
            return t
    return None


# ---------------------------------------------------------------------------
# The cluster detector (PURE READ)
# ---------------------------------------------------------------------------


def _bound_events_for_thread(workspace_root, thread_id) -> list[dict]:
    """Every event this thread OWNS (`primary_thread_id == thread_id`,
    post-reclassification-fold, honoring any prior split/undo — SPEC §0
    ruling 4's re-tag mechanism), restricted to the content types
    `thread_activity.DEFAULT_ACTIVITY_TYPES` already treats as a thread's
    real activity (`meeting`, `commitment`, `decision`, `interaction` — the
    same "thread_basis + orgs + attendees + commitment text" evidence SPEC
    §0 ruling 2 names). Org-scoped (D4c/PGUARD1 — business context, never
    personal-lane, same posture `thread_resolve._meeting_bound_thread_id`
    takes)."""
    from events_io import load_events_org_scoped
    from thread_activity import DEFAULT_ACTIVITY_TYPES, apply_reclassifications

    events, _skipped = load_events_org_scoped(workspace_root)
    folded = apply_reclassifications(events)
    out = []
    for ev in folded:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") not in DEFAULT_ACTIVITY_TYPES:
            continue
        if ev.get("primary_thread_id") != thread_id:
            continue
        out.append(ev)
    return out


def _event_org_ids(ev: dict, entities: dict) -> set:
    """Direct `org_ids` first; else the union of each `person_ids` entry's
    asserted org(s) via `entity_resolve.person_org_ids` — the SAME primitive
    `thread_resolve._person_org_ids` now delegates to (zero duplicated
    org-evidence logic between the write-time resolver and this read-time
    detector)."""
    ids = {o for o in (ev.get("org_ids") or ()) if isinstance(o, str) and o}
    if ids:
        return ids
    person_ids = [p for p in (ev.get("person_ids") or ()) if isinstance(p, str) and p]
    if not person_ids:
        return set()
    from entity_resolve import person_org_ids

    orgs = set()
    for pid in person_ids:
        orgs |= person_org_ids(entities, pid)
    return orgs


def _significant_tokens(ev: dict) -> set:
    """Word-signal fallback (secondary evidence — see module docstring):
    lowercase alpha tokens of length >= 5 from the event's title/summary
    text, minus `_STOPWORDS`."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    parts = []
    for key in ("title", "summary", "text", "wording"):
        v = data.get(key)
        if isinstance(v, str):
            parts.append(v)
    text = " ".join(parts).lower()
    tokens = re.findall(r"[a-z]{5,}", text)
    return {t for t in tokens if t not in _STOPWORDS}


def _label_for_cluster(org_ids: set, member_events: list, entities: dict) -> str:
    if org_ids:
        oid = sorted(org_ids)[0]
        for o in (entities.get("orgs") or []):
            if isinstance(o, dict) and o.get("id") == oid:
                return o.get("canonical_name") or o.get("display_name") or oid
        return oid
    counter: Counter = Counter()
    for ev in member_events:
        counter.update(_significant_tokens(ev))
    if counter:
        return counter.most_common(1)[0][0].title()
    return "Untitled subject"


def detect_subject_clusters(workspace_root, thread_id, *, entities=None) -> dict:
    """PURE READ. SPEC §0 ruling 2's evidence-scored clustering.

    Union-find over the thread's bound events: two events join the SAME
    cluster when they share an org (the primary, most objective signal —
    the same evidence class THREADBIND1's write-time resolver uses).
    Events with NO org evidence fall back to a shared significant word as
    weaker, secondary corroboration. Clusters below `MIN_CLUSTER_EVENTS`
    are dropped as noise; among what's left, only clusters whose org sets
    are pairwise DISJOINT are accepted (an org shared between two clusters
    is not separable evidence — same "ambiguous, not weak" posture
    `thread_resolve`'s org-walk takes on a multi-thread org). Fewer than
    two accepted clusters, or accepted clusters covering less than
    `MIN_COVERAGE` of the thread's bound events, is BELOW THE FLOOR — no
    split read, no noise.

    Returns:
      {"status": "below_floor", "reason": ..., "n_events": N, "n_clustered": 0, "clusters": []}
      {"status": "clustered", "n_events": N, "n_clustered": M, "coverage": f,
       "clusters": [{"subject_id", "label", "org_ids", "event_seqs", "n_events"}, ...]}
    """
    events = _bound_events_for_thread(workspace_root, thread_id)
    n_events = len(events)
    if n_events < MIN_CLUSTER_EVENTS * 2:
        return {"status": "below_floor", "reason": "too_few_events",
                "n_events": n_events, "n_clustered": 0, "clusters": []}

    if entities is None:
        entities = _load_entities(workspace_root)

    parent = list(range(n_events))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    org_sets = [_event_org_ids(ev, entities) for ev in events]
    tok_sets = [_significant_tokens(ev) for ev in events]

    org_to_events: dict = {}
    for i, orgs in enumerate(org_sets):
        for o in orgs:
            org_to_events.setdefault(o, []).append(i)
    for idxs in org_to_events.values():
        for k in range(1, len(idxs)):
            union(idxs[0], idxs[k])

    # Word fallback ONLY for events carrying no org evidence at all — org
    # evidence always wins when present (SPEC §0 ruling 2's ordering).
    tok_to_events: dict = {}
    for i, toks in enumerate(tok_sets):
        if org_sets[i]:
            continue
        for t in toks:
            tok_to_events.setdefault(t, []).append(i)
    for idxs in tok_to_events.values():
        if len(idxs) >= 2:
            for k in range(1, len(idxs)):
                union(idxs[0], idxs[k])

    groups: dict = {}
    for i in range(n_events):
        groups.setdefault(find(i), []).append(i)

    raw_clusters = []
    for idxs in groups.values():
        if len(idxs) < MIN_CLUSTER_EVENTS:
            continue
        member_orgs: set = set()
        for i in idxs:
            member_orgs |= org_sets[i]
        raw_clusters.append({"member_idx": idxs, "org_ids": member_orgs})

    if len(raw_clusters) < 2:
        return {"status": "below_floor", "reason": "single_subject",
                "n_events": n_events, "n_clustered": 0, "clusters": []}

    raw_clusters.sort(key=lambda c: -len(c["member_idx"]))
    accepted = []
    used_orgs: set = set()
    for c in raw_clusters:
        if c["org_ids"] and (c["org_ids"] & used_orgs):
            continue
        accepted.append(c)
        used_orgs |= c["org_ids"]

    if len(accepted) < 2:
        return {"status": "below_floor", "reason": "not_separable",
                "n_events": n_events, "n_clustered": 0, "clusters": []}

    n_clustered = sum(len(c["member_idx"]) for c in accepted)
    coverage = (n_clustered / n_events) if n_events else 0.0
    if coverage < MIN_COVERAGE:
        return {"status": "below_floor", "reason": "low_coverage",
                "n_events": n_events, "n_clustered": n_clustered, "clusters": []}

    clusters_out = []
    for n, c in enumerate(accepted, start=1):
        member_events = [events[i] for i in c["member_idx"]]
        seqs = sorted(ev.get("seq") for ev in member_events
                      if isinstance(ev.get("seq"), int))
        clusters_out.append({
            "subject_id": f"subj_{n}",
            "label": _label_for_cluster(c["org_ids"], member_events, entities),
            "org_ids": sorted(c["org_ids"]),
            "event_seqs": seqs,
            "n_events": len(seqs),
        })

    return {"status": "clustered", "n_events": n_events,
            "n_clustered": n_clustered, "coverage": coverage,
            "clusters": clusters_out}


# ---------------------------------------------------------------------------
# The annotation writer + reader (SPEC §0 ruling 1)
# ---------------------------------------------------------------------------


def _clusters_signature(clusters) -> tuple:
    return tuple(sorted(
        (c.get("subject_id"), c.get("label"), tuple(c.get("event_seqs") or ()))
        for c in (clusters or ())
    ))


def annotate_thread(workspace_root, thread_id, clusters, *, source_skill: str,
                    detector: str = DETECTOR_NAME, n_events: int = 0,
                    n_clustered: int = 0) -> dict:
    """Write ONE `thread_annotation` event (`annotation_kind=subjects`) —
    additive, through the canonical gate. Records never move (SPEC §0
    ruling 1): this appends a derived-label layer alongside the thread, and
    nothing else. IDEMPOTENT: a re-detection producing the SAME cluster
    membership writes NOTHING (HONEST1 — a quiet re-run stays quiet; the
    cleanup receipt's `n_annotated` only counts real deltas).

    Returns {"status": "unchanged"} or {"status": "annotated", "event": ...}.
    """
    existing = subjects_for(workspace_root, thread_id)
    if _clusters_signature(clusters) == _clusters_signature(existing):
        return {"status": "unchanged"}

    from event_gate import append_event

    data = {
        "annotation_kind": ANNOTATION_KIND_SUBJECTS,
        "thread_id": thread_id,
        "subjects": clusters,
        "detector": detector,
        "n_events_considered": n_events,
        "n_events_clustered": n_clustered,
    }
    written = append_event(_events_path(workspace_root), [{
        "type": ANNOTATION_TYPE,
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": data,
    }], holder="thread_subjects")
    return {"status": "annotated", "event": written[0]}


def subjects_for(workspace_root, thread_id) -> list:
    """PURE READ. `subjects_for(thread_id) -> [{"subject_id", "label",
    "org_ids", "event_seqs", "n_events"}, ...]` — the latest `subjects`
    annotation for this thread, or `[]` when never annotated / below the
    detector's floor (SPEC §0 ruling 3's "identical when none do" input:
    callers branch on `if subjects_for(...):`).

    A subject entry whose every `event_seq` has since moved OFF this thread
    (SPEC §0 ruling 4's split re-tagging, honored via
    `thread_activity.apply_reclassifications`) is dropped — a subject
    describing zero of THIS thread's remaining events is not this thread's
    subject anymore; its event_seqs list is also narrowed to just the
    seqs still owned here, so a partially-split subject reports honestly.
    """
    from events_io import load_events_org_scoped
    from thread_activity import apply_reclassifications

    events, _skipped = load_events_org_scoped(workspace_root)

    latest = None
    latest_seq = -1
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != ANNOTATION_TYPE:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("annotation_kind") != ANNOTATION_KIND_SUBJECTS:
            continue
        if data.get("thread_id") != thread_id:
            continue
        seq = ev.get("seq") if isinstance(ev.get("seq"), int) else -1
        if seq >= latest_seq:
            latest_seq = seq
            latest = data.get("subjects") or []

    if not latest:
        return []

    folded = apply_reclassifications(events)
    still_here = {
        ev.get("seq") for ev in folded
        if isinstance(ev, dict) and ev.get("primary_thread_id") == thread_id
        and isinstance(ev.get("seq"), int)
    }

    out = []
    for subj in latest:
        seqs = [s for s in (subj.get("event_seqs") or ()) if s in still_here]
        if seqs:
            merged = dict(subj)
            merged["event_seqs"] = seqs
            merged["n_events"] = len(seqs)
            out.append(merged)
    return out


# ---------------------------------------------------------------------------
# Consumer-side subject scoping (SPEC §0 ruling 3 — the byte-identity fence)
# ---------------------------------------------------------------------------


def scope_events_to_subject(workspace_root, thread_id, events: list, *,
                            evidence: Optional[dict] = None) -> list:
    """call-prep / one-pager call this AFTER their own existing per-thread
    event gather. `events` is that already-gathered list.

    NO SUBJECTS ON THE THREAD -> returns `events` UNCHANGED — the SAME list
    object, not a copy — the byte-identity fence SPEC §0 ruling 3 pins
    (CLUSTCOUNT1 posture: a caller that never annotated anything sees
    provably identical output to the pre-THREADANN1 build).

    SUBJECTS EXIST -> narrows to the ONE subject whose `org_ids` best
    matches `evidence["attendee_org_ids"]` (the same evidence shape
    `thread_resolve`'s callers already pass); no match, or no evidence
    given, also returns `events` unchanged (an ambiguous or unmatched
    caller gets the full thread rather than a wrong guess — the "infer,
    never ask, floor never drops" posture applied to a read instead of a
    write).
    """
    subjects = subjects_for(workspace_root, thread_id)
    if not subjects:
        return events
    evidence = evidence or {}
    org_ids = {o for o in (evidence.get("attendee_org_ids") or ()) if o}
    if not org_ids:
        return events
    best = None
    for subj in subjects:
        if org_ids & set(subj.get("org_ids") or ()):
            best = subj
            break
    if best is None:
        return events
    wanted = set(best.get("event_seqs") or ())
    return [ev for ev in events if isinstance(ev, dict) and ev.get("seq") in wanted]


# ---------------------------------------------------------------------------
# The propose-only split row (SPEC §0 ruling 4, fail-open, capped, ignorable)
# ---------------------------------------------------------------------------


def _fingerprint_split(thread_id: str) -> str:
    return f"thread_split:{thread_id}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        v = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _split_proposed_recently(workspace_root, thread_id, *,
                             now: Optional[datetime] = None,
                             days: int = SPLIT_LEDGER_DAYS) -> bool:
    """Reads (never writes) every `brain_proposal` of kind=`thread_split`
    for this thread plus its `brain_proposal_resolved` tombstones, and
    refuses a re-propose within `days` of the LATEST relevant timestamp
    (opened OR resolved) — SPEC §0 ruling 4's own "never nagged (one
    proposal per thread per 30 days)" floor. Independent of
    `proposal_ledger`'s shared 60d DECLINE-only cooldown (that one only
    fires on decline; this fires regardless of how the last one resolved,
    including a still-open row)."""
    now = now or _now_utc()
    from events_io import load_events_org_scoped

    events, _skipped = load_events_org_scoped(workspace_root)
    latest: Optional[datetime] = None
    proposal_ids = set()
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "brain_proposal":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if data.get("kind") != SPLIT_PROPOSAL_KIND:
                continue
            if data.get("thread_id") != thread_id:
                continue
            proposal_ids.add(data.get("proposal_id"))
            dt = _parse_ts(ev.get("ts"))
            if dt and (latest is None or dt > latest):
                latest = dt
        elif ev.get("type") == "brain_proposal_resolved":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if data.get("proposal_id") not in proposal_ids:
                continue
            dt = _parse_ts(ev.get("ts"))
            if dt and (latest is None or dt > latest):
                latest = dt
    if latest is None:
        return False
    return (now - latest) < timedelta(days=days)


def propose_split(workspace_root, thread_id, clusters, *,
                  detector: str = DETECTOR_NAME,
                  source_event_seq: Optional[int] = None) -> dict:
    """Fire ONE `thread_split` proposal through the EXISTING adjudication
    queue (`brain_proposals.propose`, kind=`thread_split`, tier=`confirm`)
    for an evidence-scored `clustered` verdict. No-op
    (`{"status": "not_proposable"}`) for fewer than 2 clusters.
    `{"status": "suppressed_recent"}` inside the SPEC's own 30-day ledger
    window (`_split_proposed_recently`) — checked BEFORE calling
    `propose()`, ahead of its own 60d decline-only cooldown, so an applied
    OR still-open prior row also suppresses, not just a declined one.

    Reuses the SAME registered verbs (`confirm proposal` / `dismiss
    proposal`) thread_resolve.py's thread_binding rows already use — a
    reachable tap, not an invented one: apply-choices' cr-brain dispatch
    gets a NEW `thread_split` handler in this same build (see
    skills/apply-choices/SKILL.md), calling `execute_split` below. (THREADBIND1
    review F2 found `thread_binding` rows have NO confirm-side consumer;
    this module does not repeat that gap for its own kind.)
    """
    if len(clusters) < 2:
        return {"status": "not_proposable"}
    if _split_proposed_recently(workspace_root, thread_id):
        return {"status": "suppressed_recent"}

    from brain_proposals import propose

    names = ", ".join(c.get("label") or c.get("subject_id") for c in clusters)
    render_line = (f"This thread looks like {len(clusters)} things "
                  f"({names}) — split it?")
    total = sum(c.get("n_events", 0) for c in clusters)
    evidence_text = (f"{total} of the thread's bound events cluster into "
                     f"{len(clusters)} evidence-separable subjects "
                     "(disjoint orgs, each above the noise floor)")
    extra = {"parent_thread_id": thread_id, "clusters": clusters,
             "title": names}
    if source_event_seq is not None:
        extra["source_event_seq"] = source_event_seq

    return propose(
        workspace_root,
        kind=SPLIT_PROPOSAL_KIND,
        tier="confirm",
        fingerprint=_fingerprint_split(thread_id),
        detector=detector,
        evidence=evidence_text,
        action_tuples=[{"action": "confirm proposal"}, {"action": "dismiss proposal"}],
        render_line=render_line,
        thread_id=thread_id,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# The split executor (confirm-side only, propose-only per SPEC §0 ruling 4)
# ---------------------------------------------------------------------------


def _mint_batch_id(now: Optional[datetime] = None) -> str:
    """`tha_<UTC-to-second>-<8 hex>` — same mint shape as commitment_cluster's
    `clu_` and backfill_prep_briefs's `thb_`."""
    import os

    now = now or _now_utc()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{os.urandom(4).hex()}"


def execute_split(workspace_root, thread_id, clusters, *, source_skill: str,
                  user_confirmed: bool = False) -> dict:
    """SPEC §0 ruling 4 — the ONE-TAP CONFIRMED split. Never call this on a
    bare proposal; `user_confirmed=True` is a REQUIRED, explicit assertion
    that a human tapped confirm (mirrors `org_writer.set_org_money`'s
    `confirmed=True` contract) — this function raises otherwise.

    Records never move (SPEC §0 ruling 1's invariant holds for the parent
    thread too — its own record is untouched, only children are added):
      - ONE new `thread_created` child thread PER cluster
        (`thread_writer.create_thread`, `parent_thread_id=thread_id`).
      - Every clustered event gets ONE additive `reclassification` event
        (`supersedes_seq` = the ORIGINAL event's seq, new
        `primary_thread_id` = the owning child) — RE-TAGGED, never
        re-written. The clusters already partition the thread's bound
        events (no seq in two clusters — enforced below), so every
        retagged event is reachable in EXACTLY ONE child: no orphan, no
        double.
      - ONE `tha_` `brain_undo` batch (`brain_batch_id` + `brain_change_class
        ="thread_split"` stamped on every reclassification — NEVER on the
        summary receipt, so `brain_undo._changes_for_brain_batch` reverses
        exactly the retagging, not the receipt) — `brain_undo.undo_batch`
        on the returned `batch_ref` splits them back out (registered
        reverser: `brain_undo.REVERSERS["thread_split"]`).
      - ONE `thread_split_executed` receipt.

    Returns {"status": "split", "batch_id", "batch_ref",
    "children": [{"thread_id", "subject_id", "label", "event_seqs"}, ...],
    "n_retagged", "skipped_seqs"} — `skipped_seqs` (REVIEW THREADANN1
    F2/F3) are cluster seqs the thread no longer owned at confirm time
    (moved since propose); they are left where they now live, never
    re-tagged. A proposal with fewer than 2 clusters still owning any
    event — including a re-confirm of an already-executed split — raises
    (stale) instead of splitting on stale evidence.
    """
    if not user_confirmed:
        raise ValueError(
            "execute_split requires user_confirmed=True — SPEC THREADANN1 "
            "§0 ruling 4: a split is NEVER auto-executed")
    if len(clusters) < 2:
        raise ValueError("execute_split needs >=2 clusters — nothing to split")

    seen_seqs: set = set()
    for c in clusters:
        for s in (c.get("event_seqs") or ()):
            if s in seen_seqs:
                raise ValueError(
                    f"event seq {s} appears in more than one cluster — "
                    "clusters must partition the thread's events "
                    "(no orphan, no double)")
            seen_seqs.add(s)

    entities = _load_entities(workspace_root)
    parent = _load_thread(workspace_root, thread_id, entities=entities)
    if parent is None:
        raise ValueError(f"parent thread {thread_id!r} not found")

    from thread_writer import create_thread, thread_org_id
    from event_gate import append_event
    from events_io import load_events_org_scoped
    from thread_activity import apply_reclassifications

    # REVIEW THREADANN1 F2/F3 — the cluster assignment was frozen at PROPOSE
    # time; the confirm may land later. An event that has since moved OFF
    # this thread (a user reclassification, an earlier split — including a
    # re-dispatch of this very proposal) is not this thread's to move:
    # re-tagging it here would yank a record off whatever thread now owns
    # it — the exact "moved a record on a stale heuristic" failure ruling 1
    # exists to prevent. Filter every cluster to the seqs this thread STILL
    # owns post-fold (honest shrink, the same posture `subjects_for`
    # takes); a proposal gone entirely stale (fewer than 2 clusters with
    # any still-owned event — the re-confirm of an already-executed split
    # is the canonical case) REFUSES rather than guessing.
    all_events, _skipped = load_events_org_scoped(workspace_root)
    owned_now = {
        ev.get("seq") for ev in apply_reclassifications(all_events)
        if isinstance(ev, dict) and ev.get("primary_thread_id") == thread_id
        and isinstance(ev.get("seq"), int)
    }
    skipped_seqs = []
    live_clusters = []
    for c in clusters:
        live = [s for s in (c.get("event_seqs") or ()) if s in owned_now]
        skipped_seqs.extend(s for s in (c.get("event_seqs") or ())
                            if s not in owned_now)
        if live:
            lc = dict(c)
            lc["event_seqs"] = live
            live_clusters.append(lc)
    if len(live_clusters) < 2:
        raise ValueError(
            "stale split proposal — the thread's events have moved since "
            "this split was proposed (or it was already executed); nothing "
            "was split. Re-run detection for a fresh proposal.")

    org_id = thread_org_id(parent)
    batch_id = _mint_batch_id()

    children = []
    for c in live_clusters:
        child = create_thread(
            workspace_root,
            canonical_name=f"{parent.get('canonical_name')} — "
                           f"{c.get('label') or c.get('subject_id')}",
            status=parent.get("status", "active"),
            kind=parent.get("kind"),
            org_id=org_id,
            parent_thread_id=thread_id,
            source_skill=source_skill,
            skip_dedup=True,
        )
        children.append({
            "thread_id": child["id"],
            "subject_id": c.get("subject_id"),
            "label": c.get("label"),
            "event_seqs": list(c.get("event_seqs") or ()),
        })

    by_seq = {ev.get("seq"): ev for ev in all_events
             if isinstance(ev.get("seq"), int)}

    to_append = []
    for child in children:
        for seq in child["event_seqs"]:
            orig = by_seq.get(seq)
            if orig is None:
                continue
            to_append.append({
                "type": "reclassification",
                "source_skill": source_skill,
                "supersedes_seq": seq,
                "primary_thread_id": child["thread_id"],
                "related_thread_ids": orig.get("related_thread_ids") or [],
                "classification_confidence": 1.0,
                "data": {
                    "old_primary_thread_id": thread_id,
                    "new_primary_thread_id": child["thread_id"],
                    "old_related_thread_ids": orig.get("related_thread_ids") or [],
                    "new_related_thread_ids": orig.get("related_thread_ids") or [],
                    "reason": ("user confirmed thread-split proposal — "
                              f"moved to {child['label'] or child['thread_id']}"),
                    "supersedes_seq": seq,
                    "brain_batch_id": batch_id,
                    "brain_change_class": SPLIT_CHANGE_CLASS,
                },
            })

    written = (append_event(_events_path(workspace_root), to_append,
                            holder="thread_subjects")
              if to_append else [])

    append_event(_events_path(workspace_root), [{
        "type": "thread_split_executed",
        "source_skill": source_skill,
        "primary_thread_id": thread_id,
        "data": {
            "parent_thread_id": thread_id,
            "child_thread_ids": [c["thread_id"] for c in children],
            "n_events_retagged": len(written),
            "batch_id": batch_id,
        },
    }], holder="thread_subjects")

    return {"status": "split", "batch_id": batch_id,
           "batch_ref": {"kind": "brain_batch", "batch_id": batch_id},
           "children": children, "n_retagged": len(written),
           "skipped_seqs": sorted(skipped_seqs)}


# ---------------------------------------------------------------------------
# The cleanup-job sweep (SPEC §0 ruling 2 — "runs in the weekly cleanup
# job, fleet cadence, zero new tasks")
# ---------------------------------------------------------------------------


def scan_workspace(workspace_root, *, source_skill: str = "cleanup") -> dict:
    """Detect + annotate + propose over every non-archived thread. Pure
    detection is read-only; `annotate_thread` only writes on a real delta
    (HONEST1 — quiet re-runs stay quiet); `propose_split` only writes for a
    `clustered` verdict not already inside the 30-day ledger window.

    Returns {"n_threads_scanned", "n_annotated", "n_split_proposals"} —
    SPEC's own three cleanup-receipt counts, zero-written and never
    omitted per `skills/cleanup/SKILL.md` Phase 3.5a-quater."""
    entities = _load_entities(workspace_root)
    from entities_io import entities_collection

    threads = entities_collection(entities, "threads")
    n_scanned = 0
    n_annotated = 0
    n_split_proposals = 0
    for t in threads:
        if not isinstance(t, dict):
            continue
        if t.get("status") == "archived":
            continue
        thread_id = t.get("id")
        if not thread_id:
            continue
        n_scanned += 1
        result = detect_subject_clusters(workspace_root, thread_id, entities=entities)
        if result["status"] != "clustered":
            continue
        clusters = result["clusters"]
        outcome = annotate_thread(
            workspace_root, thread_id, clusters, source_skill=source_skill,
            n_events=result["n_events"], n_clustered=result["n_clustered"])
        if outcome["status"] == "annotated":
            n_annotated += 1
        proposal = propose_split(workspace_root, thread_id, clusters)
        if proposal.get("status") == "proposed":
            n_split_proposals += 1
    return {"n_threads_scanned": n_scanned, "n_annotated": n_annotated,
           "n_split_proposals": n_split_proposals}


__all__ = [
    "ANNOTATION_TYPE",
    "ANNOTATION_KIND_SUBJECTS",
    "SPLIT_PROPOSAL_KIND",
    "SPLIT_LEDGER_DAYS",
    "BATCH_PREFIX",
    "SPLIT_CHANGE_CLASS",
    "MIN_CLUSTER_EVENTS",
    "MIN_COVERAGE",
    "detect_subject_clusters",
    "annotate_thread",
    "subjects_for",
    "scope_events_to_subject",
    "propose_split",
    "execute_split",
    "scan_workspace",
]
