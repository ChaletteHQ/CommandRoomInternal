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
    from cru_match import _now_iso, _parse_ts, load_events_defensively, load_open_commitments, split_pending_review, _commitment_field
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import _now_iso, _parse_ts, load_events_defensively, load_open_commitments, split_pending_review, _commitment_field  # type: ignore
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
    # INTAKE — an unconfirmed extraction can never make someone "overdue":
    # it is a queue member, not a promise anyone has agreed to.
    for ev in split_pending_review(load_open_commitments(str(events_path)))[0]:
        due = _parse_ts(_commitment_field(ev, "due")) if isinstance(_commitment_field(ev, "due"), str) else None
        if due is None or now_dt is None or now_dt <= due:
            continue
        overdue_days = (now_dt - due).total_seconds() / 86400.0
        d = ev.get("data") or {}
        for pid in (ev.get("person_ids") or d.get("person_ids") or []):
            out[pid] = max(out.get(pid, 0.0), overdue_days)
    return out


def _primary_user_id(workspace_root) -> Optional[str]:
    """The workspace's own CEO, resolved through THE shared seam
    (`primary_user.resolve_primary_user` — the same one DONE1 attributes
    attestations with). Defensive: an unresolvable user excludes nobody, which
    is exactly the pre-LIFECYCLE1 behaviour and never a crash."""
    try:
        from primary_user import resolve_primary_user
        return resolve_primary_user(workspace_root)
    except Exception:
        return None


def _personal_tie_ids(workspace_root) -> set:
    """person_ids carrying `tie: "personal"` (SPEC BAL1 D1.1(2) backstop).
    Defensive: an unreadable entities.json excludes nobody (the source gate
    still holds) rather than crashing the surface."""
    try:
        import json
        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            rec.get("id") for rec in (data.get("people") or [])
            if isinstance(rec, dict) and rec.get("tie") == "personal" and rec.get("id")
        }
    except Exception:
        return set()


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
            # Bounded, never forever (BAL1 second-eyes follow-up, 2026-07-19;
            # mirrors balance._excluded_ties): honor data.snooze_until when
            # present; otherwise the within_days floor. Unbounded exclusion
            # let one Pulse snooze mute a person from the weekly outreach
            # pack permanently.
            su = _parse_ts(str(d.get("snooze_until"))) if d.get("snooze_until") else None
            if su is not None:
                if now is None or su >= now:
                    excluded |= ids
            else:
                if floor is None or (dt is not None and dt >= floor):
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
    load_open_commitments) — a masked account's history never seeds a move.

    BAL1 D1.1(2) — consumer backstop: `tie: "personal"` people NEVER appear
    here, whatever emitted their signal. The detectors' own source gates skip
    personal ties before any emit; this drop at the load/score boundary guards
    signals that slipped in via another emitter or pre-exist in the log.
    Personal ties belong to the Balance surface only.

    LIFECYCLE1 §7c — the resolved primary user is dropped at the same
    boundary: a weekly outreach pack that tells the CEO to reach out to
    himself is not a ranking bug, it is a candidate-set bug."""
    try:
        import dormancy
        signals = dormancy.load_dormancy_signals(workspace_root)
    except Exception:
        signals = []
    now_dt = _parse_ts(now or _now_iso())
    commits = commitment_overdue if commitment_overdue is not None else _load_overdue(workspace_root, now_dt)
    ranked = score_candidates(signals, thread_totals or {}, commits, now=now)

    personal_ids = _personal_tie_ids(workspace_root)
    excluded = _recently_excluded(workspace_root)
    # LIFECYCLE1 §7c — the CEO is never a candidate for outreach to himself.
    # The live 2026-08-03 pack suggested he nudge his own record, tagged
    # "overdue commitment in play": `_load_overdue` credits overdue days to
    # every person_id on an open commitment, and the CEO is on most of his
    # own, so the commitment term alone carried him over score > 0 with no
    # dormancy signal at all. Excluded at the same load/score boundary as the
    # personal ties, for the same reason — a candidate set is only as honest
    # as who it refuses to put in it.
    self_id = _primary_user_id(workspace_root)
    ranked = [c for c in ranked
              if c["person_id"] not in excluded
              and c["person_id"] not in personal_ids
              and (self_id is None or c["person_id"] != self_id)
              and c["score"] > 0]
    top = ranked[:top_n]

    if emit and top:
        try:
            from atomic_write import atomic_append_jsonl
            events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
            # No hand-stamped seq (BUG-8330 item 7) — the appender allocates
            # the whole batch monotonically inside the writer lock.
            rows = [{
                "ts": now or _now_iso(),
                "type": "relationship_move_suggested", "source_skill": "relationship-moves",
                "data": {
                    "person_id": c["person_id"], "thread_ref": None,
                    "score": c["score"], "components": c["components"],
                    "evidence": [], "opener_draft_event_seq": None,
                },
            } for c in top]
            atomic_append_jsonl(events_path, rows)
        except Exception:
            pass
    return top


def _people_names(workspace_root) -> Dict[str, str]:
    """person_id -> canonical_name (defensive; handles both the top-level and
    `entities`-wrapped shapes). Empty map on an unreadable entities.json."""
    try:
        import json
        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        ent = data.get("entities") if isinstance(data.get("entities"), dict) \
            else data
        return {
            rec["id"]: rec["canonical_name"]
            for rec in (ent.get("people") or [])
            if isinstance(rec, dict) and rec.get("id")
            and rec.get("canonical_name")
        }
    except Exception:
        return {}


def _moves_context_tag(components: dict, gap_days, baseline_days) -> str:
    """The row's why-now line, substrate-derived only: dormancy gap/cadence
    days from the person's own dormancy_signal when one exists, plus which
    score components are actually in play. Never fabricates a number —
    components are normalized/capped, so days come ONLY from the signal."""
    parts = []
    if gap_days is not None:
        parts.append(f"{int(gap_days)}d since last touch")
    if baseline_days:
        parts.append(f"{int(baseline_days)}d cadence")
    if not parts and (components or {}).get("dormancy"):
        parts.append("gone quiet")
    if (components or {}).get("commitment_overdue"):
        parts.append("overdue commitment in play")
    return " · ".join(parts) or "relationship move suggested"


def moves_rows_from_candidates(candidates: List[dict],
                               workspace_root) -> List[dict]:
    """WG1-B D-B4 — the deterministic candidate→row adapter for SCHEDULED
    staff-meeting fires (big-test row 10a: the scheduled path had no way to
    turn `compute_relationship_moves` candidates into renderable rows, so the
    moves section silently vanished on every scheduled fire).

    Bare candidates ({person_id, score, components}) become full data-view
    items the renderer accepts: wire id `n` (`move:<person_id>`), the RESOLVED
    display name (an unresolvable person_id is SKIPPED with a stderr note —
    a raw id never renders, D-B1's principle), a substrate-derived why-now
    context tag (the person's dormancy_signal gap/cadence days when present),
    and the canonical connector-free verbs `nudge` / `snooze 3d` /
    `not relevant`. Nudge is compose-on-CLICK (WG1-A D-A4): no email is
    composed at fire time, so scheduled fires stay connector-free.

    Interactive fires never call this — they keep the full email-writer chain
    and email-shaped rows exactly as before (row 27 verified that path live)."""
    import sys
    names = _people_names(workspace_root)
    gap_by_pid: Dict[str, tuple] = {}
    try:
        import dormancy
        for s in dormancy.load_dormancy_signals(workspace_root):
            d = s.get("data") or {}
            pid = d.get("entity_id")
            if pid and d.get("entity_type") in ("person", None):
                gap_by_pid[pid] = (d.get("gap_days"), d.get("baseline_days"))
    except Exception:
        gap_by_pid = {}
    rows: List[dict] = []
    for c in candidates or []:
        pid = c.get("person_id")
        name = names.get(pid)
        if not name:
            sys.stderr.write(
                f"[relationship_moves] moves adapter: candidate {pid!r} has "
                "no resolvable person record — row skipped (a raw id never "
                "renders).\n")
            continue
        gap, baseline = gap_by_pid.get(pid, (None, None))
        rows.append({
            "n": f"move:{pid}",
            "name": name,
            "context_tag": _moves_context_tag(c.get("components") or {},
                                              gap, baseline),
            "actions": ["nudge", "snooze 3d", "not relevant"],
            "data": {
                "id": f"move:{pid}",
                "person_id": pid,
                "kind": "relationship_move",
            },
        })
    return rows


__all__ = ["score_candidates", "compute_relationship_moves",
           "moves_rows_from_candidates"]
