#!/usr/bin/env python3
"""
Relationship Moves ranking (SPEC REL1 Part 2) — the weekly proactive surface's
math, in code (Bug #99 model).

score = 0.5*normalized_dormancy + 0.3*thread_context + 0.2*min(overdue/10, 1)
  - normalized_dormancy = clamp(max dormancy_signal score / 4.0, 0, 1)  (the thesis)
  - thread_context      = clamp(thread_resurrection_total / 10, 0, 1)    (leverage + hook)
  - commitment_overdue  = min(overdue_days / 10, 1)                      (sharp why-now, capped)

`score_candidates` is pure + exactly tested. `compute_relationship_moves` wires
substrate reads, collapses threads into one card per person (D6), dedupes anyone
already emailed / suggested in the last 7 days or snoozed/dismissed, and emits one
`relationship_move_suggested` per returned candidate. Never pads below the real
candidate count.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

try:
    from cru_match import _now_iso, _parse_ts, load_events_defensively, load_open_commitments, _commitment_field
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import _now_iso, _parse_ts, load_events_defensively, load_open_commitments, _commitment_field  # type: ignore
    from event_time import event_time  # type: ignore


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_candidates(
    signals: List[dict], threads: Dict[str, float], commitments: Dict[str, float],
    now: Optional[str] = None,
) -> List[dict]:
    """Pure ranking. `signals` = dormancy_signal dicts (entity_type person);
    `threads` = {person_id: thread_total}; `commitments` = {person_id: overdue_days}.
    Returns candidates sorted by score desc, each with its component breakdown."""
    dormancy_by_person: Dict[str, float] = {}
    for s in signals:
        d = s.get("data") or {}
        if d.get("entity_type") not in ("person", None):
            continue
        pid = d.get("entity_id")
        if not pid:
            continue
        sc = d.get("score") or 0
        if pid not in dormancy_by_person or sc > dormancy_by_person[pid]:
            dormancy_by_person[pid] = sc

    people = set(dormancy_by_person) | set(threads or {}) | set(commitments or {})
    out: List[dict] = []
    for pid in people:
        nd = _clamp((dormancy_by_person.get(pid, 0.0)) / 4.0)
        tc = _clamp((threads or {}).get(pid, 0.0) / 10.0)
        cc = min((commitments or {}).get(pid, 0.0) / 10.0, 1.0)
        score = round(0.5 * nd + 0.3 * tc + 0.2 * cc, 6)
        out.append({
            "person_id": pid,
            "score": score,
            "components": {
                "dormancy": round(nd, 6),
                "thread_context": round(tc, 6),
                "commitment_overdue": round(cc, 6),
            },
        })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def _load_overdue(workspace_root, now_dt) -> Dict[str, float]:
    """Max overdue-days per person from open commitments past their due date."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    out: Dict[str, float] = {}
    for ev in load_open_commitments(str(events_path)):
        due = _parse_ts(_commitment_field(ev, "due")) if isinstance(_commitment_field(ev, "due"), str) else None
        if due is None or now_dt is None or now_dt <= due:
            continue
        overdue_days = (now_dt - due).total_seconds() / 86400.0
        d = ev.get("data") or {}
        for pid in (ev.get("person_ids") or d.get("person_ids") or []):
            out[pid] = max(out.get(pid, 0.0), overdue_days)
    return out


def _recently_excluded(workspace_root, within_days: int = 7) -> set:
    """Persons to exclude: emailed / suggested in the window, or actively
    snoozed / dismissed."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events, _ = load_events_defensively(events_path)
    now = _parse_ts(_now_iso())
    floor = None
    if now is not None:
        from datetime import timedelta
        floor = now - timedelta(days=within_days)
    # v4.6.0 S4 — chat_dismissal exclusion honors the mute ledger: an unmuted
    # (chat_dismissal_cleared) dismissal no longer suppresses the person.
    # Best-effort: if the ledger helper is unavailable, every dismissal's seq
    # counts as live (pre-S4 behavior, never a crash).
    live_dismissal_seqs = None
    try:
        from mute_ledger import live_mutes
        live_dismissal_seqs = {
            row["seq"] for row in live_mutes(events, _now_iso())
            if row.get("seq") is not None
        }
    except Exception:
        live_dismissal_seqs = None
    excluded: set = set()
    for ev in events:
        et = ev.get("type")
        d = ev.get("data") or {}
        dt = _parse_ts(event_time(ev))
        ids = set(ev.get("person_ids") or d.get("person_ids") or [])
        pid = d.get("person_id")
        if pid:
            ids.add(pid)
        if et in ("email_sent", "relationship_move_suggested"):
            if floor is None or (dt is not None and dt >= floor):
                excluded |= ids
        elif et == "dont_forget_snooze":
            excluded |= ids
        elif et == "chat_dismissal":
            if (
                live_dismissal_seqs is None
                or ev.get("seq") is None
                or ev.get("seq") in live_dismissal_seqs
            ):
                excluded |= ids
    return excluded


def compute_relationship_moves(
    workspace_root, *, top_n: int = 3, thread_totals: Optional[Dict[str, float]] = None,
    commitment_overdue: Optional[Dict[str, float]] = None, now: Optional[str] = None,
    emit: bool = True,
) -> List[dict]:
    """Load dormancy signals, score, dedupe, emit one `relationship_move_suggested`
    per returned candidate. `thread_totals` (thread-resurrection Phase 2 math, done
    SKILL-side) and `commitment_overdue` may be injected; otherwise commitments are
    read from substrate and threads default to none. Returns ≤ top_n — never pads.

    R5 scope masks are honored TRANSITIVELY: both inputs are mask-filtered at
    their source (dormancy.load_dormancy_signals and cru_match.
    load_open_commitments) — a masked account's history never seeds a move."""
    try:
        import dormancy
        signals = dormancy.load_dormancy_signals(workspace_root)
    except Exception:
        signals = []
    now_dt = _parse_ts(now or _now_iso())
    commits = commitment_overdue if commitment_overdue is not None else _load_overdue(workspace_root, now_dt)
    ranked = score_candidates(signals, thread_totals or {}, commits, now=now)

    excluded = _recently_excluded(workspace_root)
    ranked = [c for c in ranked if c["person_id"] not in excluded and c["score"] > 0]
    top = ranked[:top_n]

    if emit and top:
        try:
            from next_seq import next_seq
            from atomic_write import atomic_append_jsonl
            events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
            seq = next_seq(str(events_path))
            rows = []
            for c in top:
                rows.append({
                    "seq": seq, "ts": now or _now_iso(),
                    "type": "relationship_move_suggested", "source_skill": "relationship-moves",
                    "data": {
                        "person_id": c["person_id"], "thread_ref": None,
                        "score": c["score"], "components": c["components"],
                        "evidence": [], "opener_draft_event_seq": None,
                    },
                })
                seq += 1
            atomic_append_jsonl(events_path, rows)
        except Exception:
            pass
    return top


__all__ = ["score_candidates", "compute_relationship_moves"]
