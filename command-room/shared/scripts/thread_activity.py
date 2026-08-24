#!/usr/bin/env python3
"""THE thread-activity derivation — one rule, every surface (v4.5.2 C3).

WHY THIS EXISTS
---------------

FINDINGS F-54 (2026-07-08 dogfood): `stalled projects` reported "Acme Co —
43 days quiet" on a day TWO Acme Co meetings were processed and 10
commitments written. Root cause chain:

  1. `entities.json thread.last_activity` is a FOSSIL. The cleanup autopsy
     (F-61, before/after snapshot of a full successful run) proved the field
     is byte-identical after cleanup's projection refresh — NO code path
     maintains it. It froze at whatever the last ingest wrote (~May 27 on
     M's workspace).
  2. stall_detector's event scan read the thread id from `data.project_id` /
     `data.primary_thread_id` — but canonical v2.2 events carry
     `primary_thread_id` at the event's TOP LEVEL (DATA_CONTRACT.md). On
     real substrates the scan matched ZERO events, so the fossil field won
     every comparison. The unit tests stayed green because their fixture
     mirrored the code's wrong assumption instead of the real event shape
     (the realdata-fixture bug class).
  3. `related_thread_ids[]` was never scanned, so cross-referenced activity
     didn't count at all.
  4. The retired Pulse chat's Phase 4a derived its own `last_event_date` with
     different rules,
     so the two surfaces quoted different day-counts for the same project on
     the same day (F-54: 21d vs 37d).

This module is the consolidation: ONE scan that resolves every thread an
event touches (top-level `primary_thread_id`, top-level `related_thread_ids`,
the deprecated top-level `project_id` mirror, plus the legacy data-level
spellings — parsed forever, reader back-compat over rewrite), honors the
documented `computed_last_activity` confidence rule (VIEW_GENERATION.md:
`classification_confidence >= 0.40` when present), and reads through
events_io so rotated shards stay visible.

Consumers (v4.5.2): stall_detector (stalled-projects), lifecycle_pass
(orchestrator-dont-forget.md). HYG1 Item 3 added: entity_resolve recency
tiebreak, build_workspace_map_input, build_dcc_input. The fossil-readers
follow-through added the two remaining hand-rolled derivations:
render_master_tracker and list-active/render_tree (both via
derive_from_events with ALL_TYPES — renderer "last touched" semantics,
where every event type counts).

Timestamps are normalized through event_time (R7): the live substrate
carries three spellings (`ts` ×3533, `timestamp` ×156, `date` ×17 at the
2026-07-01 audit). Before this, the scan parsed top-level `ts` only, so a
thread whose latest activity was a legacy-spelled event silently read
staler than it is — the mirror image of the fossil-reader bug class.

THE DEPRECATION RULE (settled here, once)
-----------------------------------------

`thread.last_activity` is DEPRECATED as of v4.5.2. No writer maintains it.
Staleness/recency claims MUST derive from events at read time via this
module. The stored field may be consulted ONLY when a thread has zero
events (fresh-ingest workspaces legitimately carry a record stamp before
any event exists) — it may NEVER override or blend with derived activity.
A thread with same-day substrate activity must be structurally incapable
of reading as stalled.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The documented computed_last_activity floor (VIEW_GENERATION.md; same
# constant render_master_tracker enforces). Events with a numeric
# classification_confidence below this don't count as thread activity.
# Events WITHOUT the field count — infrastructure writers omit it.
CONFIDENCE_FLOOR = 0.40

# Default "what counts as activity" set — mirrors stall_detector's
# DEFAULT_CONFIG so the on-demand list and the lifecycle job agree out of the
# box. Callers with a saved stalled-projects config pass its
# activity_event_types instead (BOTH surfaces must pass the same set).
DEFAULT_ACTIVITY_TYPES = frozenset({"meeting", "commitment", "decision", "interaction"})

# Sentinel: every event type counts. Renderer semantics — MASTER_TRACKER's
# "Last Activity" column and the list-active tree mean "last touched", so a
# thread_updated or stage change legitimately bumps them. Day-count surfaces
# (stalled-projects, lifecycle_pass) must keep passing a real type set — the F-54
# contract is that surfaces quoting a day-count share ONE set. A sentinel
# object (not the string "all") so it can never be mistaken for an iterable
# of type names.
ALL_TYPES = object()


class ExcludedTypes:
    """"Every event type counts EXCEPT these" — the third shape
    `activity_types` accepts, beside a real type set and `ALL_TYPES`.

    Deliberately NOT a set, a frozenset subclass or a NamedTuple: those are
    all iterable, and an iterable here would be silently swallowed by the
    `frozenset(activity_types)` branch as an INCLUSION set — the exclusion
    would invert into "only these types count" and the caller would still go
    green. A plain opaque object cannot be mistaken for a type set; a caller
    that hands one to something expecting names gets a TypeError, loudly.

    `excluded` is materialized as a frozenset of type NAMES so the membership
    test in the fold stays O(1).
    """

    __slots__ = ("excluded",)

    def __init__(self, excluded: Iterable[str]) -> None:
        self.excluded = frozenset(excluded)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"ExcludedTypes({sorted(self.excluded)!r})"


# The commitment-lifecycle writers — the system's own bookkeeping ABOUT a
# commitment, as opposed to a human touching the thread the commitment sits
# on. Enumerated from the WRITERS (SPEC BOOKENDS1 §8 / GATE0 §4, re-read at
# this base), type -> writer:
#
#   commitment                    commitment_state._mint_child_commitments,
#                                 .create_personal_task (and the capture paths)
#   commitment_updated            commitment_state.apply_later,
#                                 .edit_commitment_wording,
#                                 .confirm_commitment_owner,
#                                 .clear_review_flags (the review-flag clear),
#                                 .flag_duplicate_for_review,
#                                 .restore_review_flags,
#                                 .mark_asked / .clear_asked (OVERDUE1's
#                                   `data.asked_set` / `data.asked_cleared` —
#                                   these are DATA FLAGS on this type, not
#                                   types of their own, so excluding
#                                   `commitment_updated` is what excludes
#                                   them; that writer additionally stamps no
#                                   `primary_thread_id` at all),
#                                 watch_gate.park_in_watch / .clear_watch
#                                   (the watch parks),
#                                 cru_match.build_commitment_updated_event
#   commitment_resolved           commitment_state.close_commitment,
#                                 cru_match.build_commitment_resolved_event
#   commitment_reopened           commitment_state.reopen_commitment
#   commitment_superseded         commitment_state.supersede_commitment,
#                                 .split_commitment
#   commitment_reassigned         commitment_state.reassign_commitment
#   commitment_reclassified       commitment_state.promote_task_to_commitment
#   commitment_partial_received   commitment_state.mark_partial_received
#   commitment_review_proposed    cru_match.build_pending_review_event
#   commitment_review_dismissed   cru_match.build_commitment_review_dismissed_event
#   chat_dismissal /              commitment_state.apply_later and its clear
#     chat_dismissal_cleared
#
# Deliberately NOT excluded, each for a stated reason:
#   thread_resolved             commitment_state.resolve_thread — a THREAD-level
#                               close, not bookkeeping about one commitment.
#                               A human resolving a thread is real movement.
#   commitment_observed         capture_gate (the observed tier) and the
#   commitment_to_discuss       `add to my list` verb. Both ARE lifecycle
#                               writes by nature, but they sit OUTSIDE the set
#                               that GATE0 measured, and this build ships the
#                               set M ruled on — adding them unmeasured would
#                               make the 42 -> 49 number in the BUILD record
#                               false. Named here so the gap is visible; owed
#                               a follow-up measurement, not a silent widening.
BOOKEND_EXCLUDED_TYPES = frozenset({
    "commitment",
    "commitment_updated",
    "commitment_resolved",
    "commitment_reopened",
    "commitment_superseded",
    "commitment_reassigned",
    "commitment_reclassified",
    "commitment_partial_received",
    "commitment_review_proposed",
    "commitment_review_dismissed",
    "chat_dismissal",
    "chat_dismissal_cleared",
})

# THE set both day-count bookends pass (SPEC BOOKENDS1). Every event type
# counts EXCEPT the commitment-lifecycle writers above — i.e. the full type
# set MINUS those, expressed as an exclusion rather than a materialized
# frozenset so that a newly-registered event type keeps counting instead of
# silently falling out of a hardcoded list.
#
# WHY IT IS NOT `ALL_TYPES`: the morning brief and the evening pack both quote
# a day-count, and `derive_from_events` has always said never to pass
# ALL_TYPES from such a surface (the F-54 contract: surfaces quoting ONE
# day-count share ONE set). Both passed it anyway. With the filter off, the
# `commitment_resolved` written when one item on a thread is CLOSED marks that
# whole thread active for seven days and hides every OTHER open item on it —
# measured on real substrate as 7 items hidden on one day (GATE0, 2026-08-23:
# morning needs_attention 42 -> 49).
#
# WHY IT IS NOT `DEFAULT_ACTIVITY_TYPES`: that set is {meeting, commitment,
# decision, interaction}, so adopting it would ALSO stop `thread_updated`,
# `deal_won`, `email_outcome`, `objective_updated` and every other
# non-lifecycle type counting — a second, unrelated narrowing that GATE0
# measured separately (51, not 49) and that nobody ruled on.
#
# WHAT THIS DOES NOT CHANGE (the C3 answer, DD-3): the canonical derivation,
# the RECL1 reclassification fold and the 0.40 confidence floor all still
# apply exactly as before. The C3 migration's fear was a bespoke max(ts) scan
# or a dropped fold — losing the derivation. This changes only WHICH event
# types count. Do not re-widen this to ALL_TYPES to "restore last-touched
# semantics": last-touched is the RENDERER contract (MASTER_TRACKER's column,
# the list-active tree), and those two callers keep ALL_TYPES on purpose.
BOOKEND_ACTIVITY_TYPES = ExcludedTypes(BOOKEND_EXCLUDED_TYPES)


class ThreadActivity(NamedTuple):
    seq: Optional[int]
    event_type: str
    ts: datetime


def _iter_events(workspace_root: Path) -> Iterable[dict]:
    """events_io (shard-transparent) with a defensive active-file fallback —
    same pattern as receipts._iter_events."""
    try:
        import events_io

        yield from events_io.iter_events(workspace_root)
        return
    except Exception:
        pass
    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict):
                    yield ev
    except OSError:
        return


def event_thread_ids(ev: dict) -> list[str]:
    """Every thread id an event touches, canonical fields first.

    Canonical (DATA_CONTRACT v2.2): top-level `primary_thread_id` +
    top-level `related_thread_ids[]`; top-level `project_id` is the
    deprecated one-cycle mirror. Legacy data-level spellings
    (`data.project_id`, `data.primary_thread_id`) are parsed forever —
    append-only history stays readable.
    """
    ids: list[str] = []
    seen: set[str] = set()

    def _add(value) -> None:
        if isinstance(value, str) and value and value not in seen:
            ids.append(value)
            seen.add(value)

    _add(ev.get("primary_thread_id"))
    related = ev.get("related_thread_ids")
    if isinstance(related, list):
        for rid in related:
            _add(rid)
    _add(ev.get("project_id"))
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    _add(data.get("project_id"))
    _add(data.get("primary_thread_id"))
    return ids


def apply_reclassifications(events: Iterable[dict]) -> list[dict]:
    """Fold `reclassification` events into the envelopes they supersede
    (OBJ2 consumer fix — the read-side half of the Pass-8 idiom).

    A `reclassification` event carries `supersedes_seq` naming the event
    whose classification it corrects, plus the corrected envelope
    (`primary_thread_id` / `related_thread_ids` / `classification_confidence`).
    The write side is append-only — the original event is never edited — so
    any reader that derives thread attribution from raw events silently
    ignores the correction. This helper is the seam that honors it: returns
    a new list where each superseded event is a SHALLOW COPY with the
    envelope keys patched from its latest correction (highest reclassifying
    seq wins when several supersede the same event). Everything else passes
    through untouched, order preserved; reclassification events themselves
    are kept in the stream (they are not in DEFAULT_ACTIVITY_TYPES, so they
    never count as activity — they exist to patch, not to be activity).

    Chains do NOT recurse: a reclassification always supersedes the
    ORIGINAL event's seq (the write contract — apply-choices and Pass 8
    both read `source_event_seq` off the original), so one hop is the
    whole story. Pure; no I/O. Adopted fleet-wide by every recency
    surface (RECL1): objective_math + the objective_link detector fold
    directly; every other thread/org recency reader opts in through the
    `honor_reclassifications` kwarg on the two derivation entry points
    (the greppable adoption ledger). The default stays False — raw
    history remains available to audit/integrity readers, and per-item
    attribution readers (§6 of SPEC_RECL1) are each a separate F-54
    per-surface decision."""
    patches: dict[int, dict] = {}
    src = events if isinstance(events, list) else list(events)
    for ev in src:
        if not isinstance(ev, dict) or ev.get("type") != "reclassification":
            continue
        sup = ev.get("supersedes_seq")
        if sup is None:
            sup = (ev.get("data") or {}).get("supersedes_seq") \
                if isinstance(ev.get("data"), dict) else None
        if not isinstance(sup, int):
            continue
        rseq = ev.get("seq") if isinstance(ev.get("seq"), int) else -1
        prev = patches.get(sup)
        if prev is None or rseq >= prev.get("_rseq", -1):
            patches[sup] = {
                "_rseq": rseq,
                "primary_thread_id": ev.get("primary_thread_id"),
                "related_thread_ids": (ev.get("related_thread_ids")
                                       if isinstance(
                                           ev.get("related_thread_ids"), list)
                                       else []),
                "classification_confidence":
                    ev.get("classification_confidence"),
            }
    if not patches:
        return list(src)
    out: list[dict] = []
    for ev in src:
        seq = ev.get("seq") if isinstance(ev, dict) else None
        patch = patches.get(seq) if isinstance(seq, int) else None
        if patch is None:
            out.append(ev)
            continue
        patched = dict(ev)
        patched["primary_thread_id"] = patch["primary_thread_id"]
        patched["related_thread_ids"] = list(patch["related_thread_ids"])
        if patch["classification_confidence"] is not None:
            patched["classification_confidence"] = \
                patch["classification_confidence"]
        # legacy data-level spellings would resurrect the old attribution
        # through event_thread_ids' fallback parse — clear them on the copy
        if isinstance(patched.get("data"), dict):
            d = dict(patched["data"])
            d.pop("project_id", None)
            d.pop("primary_thread_id", None)
            d.pop("thread_id", None)
            patched["data"] = d
        patched.pop("project_id", None)
        out.append(patched)
    return out


def _event_dt(ev: dict) -> Optional[datetime]:
    """Parsed, UTC-aware event timestamp, honoring all three live field
    spellings (`ts` → `timestamp` → `date`) via event_time (R7). Defensive
    fallback keeps the original ts-only parse so a missing sibling module
    never bricks a staleness read (never-brick posture)."""
    try:
        from event_time import event_dt

        dt = event_dt(ev)
        return _ts_key(dt) if dt is not None else None
    except ImportError:
        pass
    try:
        return _ts_key(datetime.fromisoformat(ev["ts"].replace("Z", "+00:00")))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def derive_from_events(
    events: Iterable[dict],
    activity_types: Optional[Iterable[str]] = None,
    confidence_floor: float = CONFIDENCE_FLOOR,
    honor_reclassifications: bool = False,
) -> dict[str, ThreadActivity]:
    """The C3 fold over an in-memory event iterable — same rules as
    derive_thread_activity, for callers that already hold the events
    (render_master_tracker, list-active/render_tree load them once for
    other columns too).

    activity_types: one of three shapes —
      * a type set (None → DEFAULT_ACTIVITY_TYPES): only these types count;
      * the ALL_TYPES sentinel: renderer "last touched" semantics, every
        event type counts. Never pass ALL_TYPES from a surface that quotes
        a day-count (F-54 contract);
      * an ExcludedTypes object: every type counts EXCEPT the named ones.
        This is the shape the day-count bookends pass
        (BOOKEND_ACTIVITY_TYPES) — it keeps last-touched breadth for
        everything a human does to a thread while refusing to count the
        system's own bookkeeping about a commitment as the thread having
        moved.

    honor_reclassifications: when True, fold apply_reclassifications over
    the stream first, so user-approved corrections (Pass 8 edits,
    objective_link confirm/dismiss, merges) move activity with the event
    (RECL1). Default False is FROZEN — the raw path stays byte-identical
    for non-adopting callers; passing True IS the per-surface adoption
    decision (F-54: surfaces quoting the same day-count adopt together).
    """
    if honor_reclassifications:
        events = apply_reclassifications(events)
    excluded: frozenset[str] = frozenset()
    if activity_types is ALL_TYPES:
        types = None
    elif isinstance(activity_types, ExcludedTypes):
        types = None
        excluded = activity_types.excluded
    else:
        types = frozenset(activity_types) if activity_types is not None else DEFAULT_ACTIVITY_TYPES
    last: dict[str, ThreadActivity] = {}

    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if types is not None and etype not in types:
            continue
        if etype in excluded:
            continue
        conf = ev.get("classification_confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool) and conf < confidence_floor:
            continue
        thread_ids = event_thread_ids(ev)
        if not thread_ids:
            continue
        ts = _event_dt(ev)
        if ts is None:
            continue
        seq = ev.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            seq = None
        # Store UTC-aware so downstream day-count arithmetic against an
        # aware `now` never mixes naive/aware (the F-15 legacy writer mix).
        record = ThreadActivity(seq=seq, event_type=etype or "", ts=ts)
        for tid in thread_ids:
            prior = last.get(tid)
            if prior is None or record.ts > prior.ts:
                last[tid] = record

    return last


def derive_thread_activity(
    workspace_root: str | Path,
    activity_types: Optional[Iterable[str]] = None,
    confidence_floor: float = CONFIDENCE_FLOOR,
    honor_reclassifications: bool = False,
) -> dict[str, ThreadActivity]:
    """{thread_id: most-recent ThreadActivity} derived from events at read
    time. THE staleness baseline — never the stored last_activity field.

    Args:
        workspace_root: folder containing `_hq/data/events.jsonl`.
        activity_types: which event types count as activity. None →
            DEFAULT_ACTIVITY_TYPES; an ExcludedTypes object → every type
            counts except the named ones (see derive_from_events). Pass the
            user's saved stalled-projects `activity_event_types` — and pass
            the SAME set from every surface that quotes a day-count, or the
            numbers diverge (F-54's 21d-vs-37d split). The morning brief and
            the evening pack both pass BOOKEND_ACTIVITY_TYPES for exactly
            that reason.
        confidence_floor: events with numeric classification_confidence
            below this are skipped (absent field = counted).
        honor_reclassifications: True = fold user-approved corrections
            into the stream before deriving (RECL1 adoption; see
            derive_from_events). Default False is the frozen raw path.

    Recency is decided by the event timestamp (the honest signal); `seq` is
    carried for traceability. An event with an unparseable/missing
    timestamp is skipped — it can't be placed on a timeline.
    """
    return derive_from_events(
        _iter_events(Path(workspace_root)),
        activity_types=activity_types,
        confidence_floor=confidence_floor,
        honor_reclassifications=honor_reclassifications,
    )


def _ts_key(ts: datetime) -> datetime:
    """Order-safe comparison key across naive/aware timestamps (the F-15
    legacy writer mix): naive stamps are taken as UTC — the same assumption
    stall_detector has always made for date-only fields."""
    from datetime import timezone as _tz

    if ts.tzinfo is None:
        return ts.replace(tzinfo=_tz.utc)
    return ts.astimezone(_tz.utc)


__all__ = [
    "derive_thread_activity",
    "derive_from_events",
    "apply_reclassifications",
    "event_thread_ids",
    "ThreadActivity",
    "DEFAULT_ACTIVITY_TYPES",
    "ALL_TYPES",
    "ExcludedTypes",
    "BOOKEND_EXCLUDED_TYPES",
    "BOOKEND_ACTIVITY_TYPES",
    "CONFIDENCE_FLOOR",
]
