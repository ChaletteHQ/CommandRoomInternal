#!/usr/bin/env python3
"""
Dormancy normalization (SPEC REL1 Part 1) — one relationship, one dormancy story.

The plugin had FIVE incompatible dormancy definitions (dormant-customer-scan,
Pulse per-person, thread-resurrection, team-intelligence, plus the project
state-machine). The same relationship could read "dormant" in one surface and
"fine" in another. This module lets every relationship detector ALSO emit one
shared `dormancy_signal` event carrying a CODE-computed normalized score, so
consumers (the new relationship-moves surface) read one normalized story per
entity and rank comparably across detectors.

Score math is in code (Bug #99 model — five LLM-side arithmetic sites is the
failure class this kills):
  - baseline present  → score = gap_days / baseline_days
  - baseline null     → absolute tier: 14-29d→1.8, 30-59d→2.5, >=60d→4.0,
                        <14d→None (too early; no signal)
The absolute tiers were chosen to sit ON the ratio thresholds (1.8/2.5) the
retired Pulse chat used, so absolute and ratio signals rank together.

Project lifecycle (30/60/180) stays in the ORG_AND_THREAD_MODEL state machine —
`entity_type: "project"` is deliberately NOT a dormancy_signal (the signal
vocabulary is about RELATIONSHIPS: person | org | thread).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

try:
    from cru_match import _now_iso, _parse_ts, load_events_defensively
    from event_time import event_time
    from event_refs import email_person_index, meeting_person_ids
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import _now_iso, _parse_ts, load_events_defensively  # type: ignore
    from event_time import event_time  # type: ignore
    from event_refs import email_person_index, meeting_person_ids  # type: ignore

_INTERACTION_TYPES = ("interaction", "meeting", "meeting_processed", "email_sent",
                      "email_drafted", "commitment")


def normalize_score(gap_days: float, baseline_days: Optional[float]) -> Optional[float]:
    """Normalized dormancy score. Returns None when there is no signal (a sub-14-day
    gap with no baseline is too early to flag)."""
    if baseline_days:
        try:
            return gap_days / baseline_days
        except ZeroDivisionError:
            return None
    if gap_days < 14:
        return None
    if gap_days < 30:
        return 1.8
    if gap_days < 60:
        return 2.5
    return 4.0


def _is_personal_tie(workspace_root, person_id) -> bool:
    """True when this person's record carries `tie: "personal"` (SPEC BAL1 D1).
    Defensive by design: an unreadable or oddly-shaped entities.json returns
    False, which is the pre-gate behaviour — the gate must never be the reason
    a work signal disappears."""
    if not person_id:
        return False
    try:
        import json
        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        ent = data.get("entities") if isinstance(data.get("entities"), dict) \
            else data
        for rec in (ent.get("people") or []):
            if isinstance(rec, dict) and rec.get("id") == person_id:
                return rec.get("tie") == "personal"
    except Exception:
        return False
    return False


def emit_dormancy_signal(
    workspace_root, entity_id: str, entity_type: str, gap_days: float,
    baseline_days: Optional[float], source_skill: str,
) -> Optional[int]:
    """Append one `dormancy_signal` event. Call this at the detector's existing
    flag point, AFTER its live-check gate (so a signal in the substrate is already
    live-verified). Returns the seq written, or None when there's no signal /
    on any error (never raises into a detector)."""
    # BAL1 D1.1(1) — THE personal-tie source gate, now in CODE.
    #
    # It used to live as prose in the Pulse orchestrator's Phase 3: "skip every
    # person whose record carries tie: personal BEFORE any computation or
    # emit". LIFECYCLE1 retired that chat, and a gate whose only home is a file
    # you can delete is a gate with a deletion date. Every emitter routes
    # through this function, so this is the chokepoint the prose was describing
    # — and a NEW emitter now inherits the gate instead of having to remember
    # a paragraph. The per-skill skips (dormant-customer-scan,
    # team-intelligence) and the consumer backstop in relationship_moves are
    # unchanged; defence in depth, not a replacement.
    #
    # A spouse or parent going quiet is not a work signal, and the moment a
    # personal tie's dormancy enters the substrate it flows into
    # relationship-moves and the weekly WORK-outreach pack. Absent `tie` means
    # work (back-compat); only the explicit `personal` value skips. Defensive:
    # an unreadable entities.json blocks nobody, exactly as before.
    if entity_type == "person" and _is_personal_tie(workspace_root, entity_id):
        return None
    score = normalize_score(gap_days, baseline_days)
    if score is None:
        return None
    try:
        from atomic_write import atomic_append_jsonl
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        # Seq comes back from the append (BUG-8330 item 7) — allocated
        # inside the writer lock, never pre-computed.
        written = atomic_append_jsonl(events_path, [{
            "ts": _now_iso(), "type": "dormancy_signal",
            "source_skill": source_skill,
            "data": {
                "entity_id": entity_id, "entity_type": entity_type,
                "gap_days": gap_days, "baseline_days": baseline_days,
                "score": round(score, 4),
            },
        }])
        return written[0].get("seq")
    except Exception:
        return None


def load_dormancy_signals(workspace_root, window_days: int = 14) -> List[dict]:
    """Return the max-score `dormancy_signal` per entity within the window, dropping
    any entity that has a NEWER eligible interaction than its signal (contrary
    evidence means the relationship isn't actually cooling anymore)."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events, _ = load_events_defensively(events_path)
    # R5 reader-honor: dormancy never counts (or resurfaces) a scope-masked
    # account's history. Defensive — failure leaves events unfiltered.
    try:
        from account_scope_gate import filter_masked_events
        events = filter_masked_events(events)
    except Exception:
        pass

    now = _parse_ts(_now_iso())
    floor = None
    if now is not None:
        from datetime import timedelta
        floor = now - timedelta(days=window_days)

    # BUG-8244: an email→person index so meeting events whose binding lives
    # in attendee-email fields still count as contact. Without this, the
    # contrary-evidence gate below never fired for meeting-only relationships
    # and stale dormancy signals survived forever (fed relationship-moves'
    # "haven't talked in a while" drafts about people the user meets weekly).
    email_idx: Dict[str, str] = {}
    try:
        import json as _json
        ent_path = Path(workspace_root) / "_hq" / "data" / "entities.json"
        if ent_path.exists():
            email_idx = email_person_index(
                _json.loads(ent_path.read_text(encoding="utf-8")))
    except Exception:
        pass

    # Latest interaction ts per entity: the RAW person_ids read (unfiltered —
    # dormancy entity ids are person/org/thread and legacy rows carry loose
    # spellings) UNIONED with the shared normalizer's fold (attendee variants
    # + resolved emails), plus primary_thread_id.
    last_interaction: Dict[str, object] = {}
    for ev in events:
        if ev.get("type") not in _INTERACTION_TYPES:
            continue
        dt = _parse_ts(event_time(ev))
        if dt is None:
            continue
        d = ev.get("data") or {}
        ids = set(ev.get("person_ids") or []) | set(d.get("person_ids") or [])
        ids |= set(meeting_person_ids(ev, email_idx))
        pt = ev.get("primary_thread_id") or d.get("primary_thread_id")
        if pt:
            ids.add(pt)
        for eid in ids:
            if eid not in last_interaction or dt > last_interaction[eid]:
                last_interaction[eid] = dt

    best: Dict[str, dict] = {}
    for ev in events:
        if ev.get("type") != "dormancy_signal":
            continue
        sig_dt = _parse_ts(event_time(ev))
        if floor is not None and sig_dt is not None and sig_dt < floor:
            continue
        d = ev.get("data") or {}
        eid = d.get("entity_id")
        if not eid:
            continue
        # Drop if a newer eligible interaction exists than this signal.
        li = last_interaction.get(eid)
        if li is not None and sig_dt is not None and li > sig_dt:
            continue
        score = d.get("score") or 0
        if eid not in best or score > (best[eid].get("data") or {}).get("score", -1):
            best[eid] = ev
    return list(best.values())


# ---------------------------------------------------------------------------
# Quick Win B (Phase 6) — Pulse "just busy" updates the cadence baseline
# ---------------------------------------------------------------------------
#
# Before Phase 6, Pulse's "ignore — just busy" (`resolved`) on a person-dormancy
# item only wrote a 14-day suppression (`dont_forget_feedback`): the model of the
# relationship never changed, so the SAME flag returned two weeks later, forever.
# Now the reply ALSO widens a persisted per-person cadence baseline, so the model
# of the relationship improves instead of being repeatedly overridden. Pulse's
# dormancy math floors the computed cadence with this override (effective_baseline),
# so a person the CEO has said "this gap is normal for them" about stops tripping
# the flag at that gap.

def cadence_override_days(person_record: dict) -> Optional[float]:
    """The user-taught cadence baseline on a person record, or None. Old records
    without the field read as None (readers-handle-both-shapes, §3.1)."""
    if not isinstance(person_record, dict):
        return None
    v = person_record.get("cadence_override_days")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def effective_baseline(
    computed_baseline: Optional[float], override_days: Optional[float]
) -> Optional[float]:
    """The baseline a dormancy detector should actually use: the wider of the computed cadence
    and the user-taught override. Widening (max) is the whole point — "just busy"
    means the real cadence is longer than the last few interactions implied.
    Pure; None-safe."""
    vals = [v for v in (computed_baseline, override_days) if v]
    return max(vals) if vals else None


def record_just_busy(
    workspace_root, person_id: str, observed_gap_days: float,
    # FOSSIL default (LIFECYCLE1): the only surface that ever called this was
    # the retired Pulse chat's "just busy" reply, so the default names it
    # honestly. A NEW caller passes its own id — never inherit this one.
    #
    # ⚠ THIS FUNCTION CURRENTLY HAS NO LIVE CALLER, and that is a known,
    # RULED-ON loss rather than an oversight (LIFECYCLE1 fix round, SF-5).
    # The dormancy row's shipped verbs are `active` / `archive` / `snooze 14d`
    # — there is no "just busy" among them — so nothing widens
    # `cadence_override_days` any more and the Quick-Win-B cadence-teaching
    # loop is INERT: the CEO can no longer tell the system "this gap is normal
    # for this person" and have the model of that relationship move. The
    # reader half still works (`effective_baseline` honours any override
    # already on a record), so nothing already taught is lost.
    # Re-homing the verb is deliberately OUT OF SCOPE here — it belongs with a
    # ruling on the weekly moves batch, which is where a "just busy" answer
    # would now naturally live. Do not quietly re-point this at a surface to
    # make the orphan go away; that decision is not this module's to make.
    *, source_skill: str = "pulse",
) -> Optional[float]:
    """Widen a person's persisted cadence baseline after a "just busy" reply.
    Sets `cadence_override_days = max(existing, observed_gap_days)` via the
    canonical people writer (never a direct entities.json write). Returns the new
    override, or None on any error (never raises into the reply handler). The
    14-day `dont_forget_feedback` suppression is written separately by the reply
    handler — this is the additive model update on top of it."""
    try:
        from people_writer import update_person, _load_entities, entities_collection
        data = _load_entities(Path(workspace_root))
        people = entities_collection(data, "people")
        rec = next((p for p in people if p.get("id") == person_id), None)
        if rec is None:
            return None
        existing = cadence_override_days(rec) or 0.0
        new_val = max(existing, float(observed_gap_days or 0))
        if new_val <= 0:
            return None
        update_person(workspace_root, person_id,
                      source_skill=source_skill, cadence_override_days=new_val)
        return new_val
    except Exception:
        return None


__all__ = [
    "normalize_score", "emit_dormancy_signal", "load_dormancy_signals",
    "cadence_override_days", "effective_baseline", "record_just_busy",
]


if __name__ == "__main__":  # smoke
    import sys, tempfile, json
    ws = Path(tempfile.mkdtemp())
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    emit_dormancy_signal(ws, "person_1", "person", 42, 21, "dormant-customer-scan")
    print(json.dumps(load_dormancy_signals(ws), indent=2))
    sys.exit(0)
