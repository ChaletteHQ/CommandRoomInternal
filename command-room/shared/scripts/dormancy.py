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
The absolute tiers are chosen to sit ON the Pulse ratio thresholds (1.8/2.5)
so absolute and ratio signals rank together.

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
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import _now_iso, _parse_ts, load_events_defensively  # type: ignore
    from event_time import event_time  # type: ignore

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


def emit_dormancy_signal(
    workspace_root, entity_id: str, entity_type: str, gap_days: float,
    baseline_days: Optional[float], source_skill: str,
) -> Optional[int]:
    """Append one `dormancy_signal` event. Call this at the detector's existing
    flag point, AFTER its live-check gate (so a signal in the substrate is already
    live-verified). Returns the seq written, or None when there's no signal /
    on any error (never raises into a detector)."""
    score = normalize_score(gap_days, baseline_days)
    if score is None:
        return None
    try:
        from next_seq import next_seq
        from atomic_write import atomic_append_jsonl
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        seq = next_seq(str(events_path))
        atomic_append_jsonl(events_path, [{
            "seq": seq, "ts": _now_iso(), "type": "dormancy_signal",
            "source_skill": source_skill,
            "data": {
                "entity_id": entity_id, "entity_type": entity_type,
                "gap_days": gap_days, "baseline_days": baseline_days,
                "score": round(score, 4),
            },
        }])
        return seq
    except Exception:
        return None


def load_dormancy_signals(workspace_root, window_days: int = 14) -> List[dict]:
    """Return the max-score `dormancy_signal` per entity within the window, dropping
    any entity that has a NEWER eligible interaction than its signal (contrary
    evidence means the relationship isn't actually cooling anymore)."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events, _ = load_events_defensively(events_path)

    now = _parse_ts(_now_iso())
    floor = None
    if now is not None:
        from datetime import timedelta
        floor = now - timedelta(days=window_days)

    # Latest interaction ts per entity (person_ids member or primary_thread_id).
    last_interaction: Dict[str, object] = {}
    for ev in events:
        if ev.get("type") not in _INTERACTION_TYPES:
            continue
        dt = _parse_ts(event_time(ev))
        if dt is None:
            continue
        d = ev.get("data") or {}
        ids = set(ev.get("person_ids") or d.get("person_ids") or [])
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
    """The baseline Pulse should actually use: the wider of the computed cadence
    and the user-taught override. Widening (max) is the whole point — "just busy"
    means the real cadence is longer than the last few interactions implied.
    Pure; None-safe."""
    vals = [v for v in (computed_baseline, override_days) if v]
    return max(vals) if vals else None


def record_just_busy(
    workspace_root, person_id: str, observed_gap_days: float,
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
