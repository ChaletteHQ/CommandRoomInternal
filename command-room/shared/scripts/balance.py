#!/usr/bin/env python3
"""
Balance Guardian math (SPEC BAL1) — the personal white-space surface's
computation, in code (the Bug #99 model: never LLM arithmetic).

The weekly Sunday surface reads the PERSONAL lane only — `tie: "personal"`
people, personal reminders, and the open evenings on the declared personal +
family calendars cross-checked against business busy — and surfaces the single
most-starved personal relationship with real open evenings attached. The
firewall is the feature: everything this module emits is personal-lane
(`balance_nudge_suggested`, classified personal by `personal_leak.is_personal`
and dropped from every org-scoped read by `events_io.load_events_org_scoped`),
and this surface renders ONLY at `surface="m_facing"`.

Cadence semantics (D1(b) — load-bearing): `cadence_days` is the personal
RE-SURFACE interval ("date night every 14 days") and is read HERE ONLY. It is
the OPPOSITE of `cadence_override_days`, dormancy's max-widening SUPPRESSION
knob — `dormancy.effective_baseline` is never called from this module and
`cadence_days` never feeds any work-dormancy computation.

Not-configured != healthy: with no `workspace.personal_calendars` declared,
`compute_balance` returns `status: "not_configured"` and emits NOTHING — the
skill says "connect a personal calendar to turn on Balance", never all-clear.

Busy intervals are INJECTED by the caller (the skill fetches the declared
personal + family calendars and business availability through the connector
layer at fire time, localizes via tz.to_local, and passes the merged lists) —
same injection pattern as relationship_moves' thread_totals. Code cannot call
MCP connectors; code CAN compile the per-calendar query args
(`personal_calendar_query_specs`) so the fetch targets the declared calendars
through `connector_adapters/calendar.py::calendar_addressing_field`.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Dict, List, Optional

try:
    from cru_match import (_now_iso, _parse_ts, event_references_person,
                           load_events_defensively)
    from event_time import event_time
    import availability
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import (_now_iso, _parse_ts, event_references_person,  # type: ignore
                           load_events_defensively)
    from event_time import event_time  # type: ignore
    import availability  # type: ignore

# Interaction types that count as a personal touch — same eligible set the
# dormancy engine uses (one definition of "we talked" across the product).
_INTERACTION_TYPES = ("interaction", "meeting", "meeting_processed", "email_sent",
                      "email_drafted", "commitment")

# Default personal re-surface cadence (spouse date-night default, D2) when a
# tie carries no cadence_days. Workspace-tunable via
# workspace.balance_default_cadence_days.
DEFAULT_CADENCE_DAYS = 14.0

# Per-tie dedupe window: a tie nudged in the last 7 days is not re-nudged.
DEDUPE_WINDOW_DAYS = 7


def _load_entities(workspace_root) -> dict:
    import json
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _workspace_settings(entities: dict) -> dict:
    ws = entities.get("workspace")
    return ws if isinstance(ws, dict) else {}


def personal_ties(entities: dict) -> List[dict]:
    """Every active person record with `tie: "personal"`. Absence of the tie
    field means WORK (back-compat, D1) — this is the partition line
    relationship-moves reads the other side of."""
    people = entities.get("people") or []
    out = []
    for p in people:
        if not isinstance(p, dict):
            continue
        if p.get("tie") != "personal":
            continue
        if p.get("status") == "archived":
            continue
        out.append(p)
    return out


def tie_cadence_days(person_record: dict, default: float = DEFAULT_CADENCE_DAYS) -> float:
    """The personal re-surface interval for a tie. Reads `cadence_days` ONLY —
    never `cadence_override_days` (that is dormancy's suppression knob with
    the opposite meaning, D1(b))."""
    v = (person_record or {}).get("cadence_days")
    try:
        f = float(v) if v is not None else None
    except (TypeError, ValueError):
        f = None
    return f if f and f > 0 else float(default)


def personal_calendar_query_specs(
    entities: dict, *, now, horizon_days: int = 14, provider: Optional[str] = None,
) -> List[dict]:
    """Compile the per-calendar fetch specs for the declared personal/family
    calendars: one spec per `workspace.personal_calendars` entry, carrying the
    provider's addressing field (calendarId / account) + the compiled time
    window. The SKILL executes these through the discovered calendar tool —
    this helper only guarantees the fetch targets the DECLARED calendars."""
    try:
        from connector_adapters.calendar import calendar_addressing_field, compile_window
    except ImportError:  # pragma: no cover
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from connector_adapters.calendar import calendar_addressing_field, compile_window  # type: ignore

    ws = _workspace_settings(entities)
    cals = ws.get("personal_calendars") or []
    start = availability._parse_dt(now, "now")
    end = start + _dt.timedelta(days=int(horizon_days))
    field = calendar_addressing_field(provider)
    specs = []
    for cal in cals:
        cal_id = cal.get("id") if isinstance(cal, dict) else cal
        if not cal_id:
            continue
        spec = dict(compile_window(start.isoformat(), end.isoformat(), provider))
        if field:
            spec[field] = str(cal_id)
        label = cal.get("label") if isinstance(cal, dict) else None
        specs.append({"calendar": str(cal_id), "label": label, "query": spec})
    return specs


def white_space_debt(
    ties: List[dict], reminders: List[dict], slots: List[dict], now,
) -> List[dict]:
    """Pure decay-first ranking. `ties` rows carry {person_id, name, gap_days,
    cadence_days}; `slots` is availability.open_evenings output; `reminders`
    is the personal reminder rows (context for the surface, not rank input in
    v1). Returns candidates sorted by score desc, each with its breakdown.

    Rules (D2): score = gap_days / cadence_days; only starved ties
    (score >= 1.0) are candidates; a starved tie with ZERO open slots is
    SUPPRESSED, never surfaced as guilt — so with any open slot available,
    every candidate ranks with has_slot True; with none, the list is empty.
    A tie with unknown gap (no touch on record) cannot claim starvation and
    is excluded."""
    has_slot = bool(slots)
    out: List[dict] = []
    for t in ties or []:
        gap = t.get("gap_days")
        try:
            gap = float(gap) if gap is not None else None
        except (TypeError, ValueError):
            gap = None
        if gap is None:
            continue
        cadence = tie_cadence_days(t, default=t.get("_default_cadence") or DEFAULT_CADENCE_DAYS)
        score = round(gap / cadence, 6)
        if score < 1.0:
            continue
        if not has_slot:
            continue  # starvation with no actionable evening is noise (D2)
        out.append({
            "person_id": t.get("person_id") or t.get("id"),
            "name": t.get("name") or t.get("canonical_name"),
            "gap_days": gap,
            "cadence_days": cadence,
            "score": score,
        })
    out.sort(key=lambda c: (-c["score"], c["person_id"] or ""))
    return out


def _local_now_naive(workspace_root, now_iso):
    """Workspace-local wall-clock anchor for the evening computation
    (second-eyes fix, 2026-07-19). Event timestamps stay UTC, but the
    18:00–22:00 evening window is a LOCAL concept — anchoring it with the
    UTC clock evaluates \"evenings\" at 11 AM–3 PM for a Pacific workspace.
    Falls back to now_iso unchanged when the workspace TZ is unresolvable
    (test fixtures / unconfigured workspaces run on one clock anyway)."""
    try:
        from tz import to_local
        loc = to_local(now_iso, workspace_path=workspace_root)
        return loc.replace(tzinfo=None)
    except Exception:
        return now_iso


def _last_touch_days(events: List[dict], person: dict, now_dt) -> Optional[float]:
    """Days since the most recent touch for this tie: the max of the person
    record's `last_interaction` date and any eligible interaction event that
    references the person. None when no touch is on record."""
    best = None
    li = person.get("last_interaction")
    if li:
        dt = _parse_ts(str(li) if "T" in str(li) else f"{li}T00:00:00Z")
        if dt is not None:
            best = dt
    pid = person.get("id")
    if pid:
        for ev in events:
            if ev.get("type") not in _INTERACTION_TYPES:
                continue
            try:
                if not event_references_person(ev, pid):
                    continue
            except Exception:
                continue
            dt = _parse_ts(event_time(ev))
            if dt is not None and (best is None or dt > best):
                best = dt
    if best is None or now_dt is None:
        return None
    return max(0.0, (now_dt - best).total_seconds() / 86400.0)


def _excluded_ties(events: List[dict], now_dt) -> set:
    """Ties to skip this fire: a `balance_nudge_suggested` for the tie within
    the dedupe window, an active snooze, or a live dismissal (mute-ledger
    honored best-effort, the relationship_moves pattern)."""
    from datetime import timedelta
    floor = now_dt - timedelta(days=DEDUPE_WINDOW_DAYS) if now_dt else None

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
        ids = set(ev.get("person_ids") or d.get("person_ids") or [])
        # target_id/entity_id included (second-eyes fix, 2026-07-19): the
        # apply-choices snooze dispatch writes mute-ledger-shaped dismissals
        # (data.target_id = the tie's person_id); non-person tokens are
        # harmless here — exclusion is a membership test against person ids.
        for key in ("tie_person_id", "person_id", "target_id", "entity_id"):
            if d.get(key):
                ids.add(d[key])
        if not ids:
            continue
        if et == "balance_nudge_suggested":
            dt = _parse_ts(event_time(ev))
            if floor is None or (dt is not None and dt >= floor):
                excluded |= ids
        elif et == "dont_forget_snooze":
            # Bounded, never forever (second-eyes fix, 2026-07-19): honor
            # data.snooze_until when present; otherwise the dedupe-window
            # floor. Unbounded exclusion here would let a YEARS-old Pulse
            # snooze (person_009/person_013 have them on file) permanently
            # mute a tie the moment it is backfilled to personal.
            su = _parse_ts(str(d.get("snooze_until"))) if d.get("snooze_until") else None
            if su is not None:
                if now_dt is None or su >= now_dt:
                    excluded |= ids
            else:
                dt = _parse_ts(event_time(ev))
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


def compute_balance(
    workspace_root,
    horizon_days: int = 14,
    *,
    now: Optional[str] = None,
    personal_busy: Optional[List[dict]] = None,
    business_busy: Optional[List[dict]] = None,
    emit: bool = True,
) -> dict:
    """The weekly Balance computation. Returns one of:

      {"status": "not_configured", "reason": ...}    — no personal calendar
          declared; NOTHING emitted (refuse, never all-clear).
      {"status": "no_calendar_data", "reason": ...}  — calendars declared but
          the caller passed no busy data (None); NOTHING emitted — never
          propose an evening against an unknown calendar.
      {"status": "all_clear", ...}                   — nothing starved.
      {"status": "nudge", "nudge": {...}, ...}       — exactly ONE nudge; one
          `balance_nudge_suggested` appended when emit=True.

    `personal_busy` / `business_busy` are the fetched busy intervals for the
    declared personal+family calendars and the business calendar. Pass [] for
    a genuinely-empty calendar; None means "could not fetch" and refuses.
    """
    entities = _load_entities(workspace_root)
    ws = _workspace_settings(entities)
    cals = ws.get("personal_calendars") or []
    if not cals:
        return {"status": "not_configured",
                "reason": "no workspace.personal_calendars declared"}
    if personal_busy is None or business_busy is None:
        missing = [n for n, v in (("personal", personal_busy),
                                  ("business", business_busy)) if v is None]
        return {"status": "no_calendar_data",
                "reason": f"busy intervals not provided for: {', '.join(missing)}"}

    now_iso = now or _now_iso()
    now_dt = _parse_ts(now_iso)
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events, skipped = load_events_defensively(events_path)

    # Personal reminders — the reminders.py m_facing firewall, reused verbatim
    # (this is an OWNER surface; the copy the widget renders is personal-lane
    # by construction and never reaches an org read).
    try:
        from reminders import active_reminders
        personal_reminders = [
            r for r in active_reminders(events, str(now_iso)[:10], surface="m_facing")
            if r.get("personal")
        ]
    except Exception:
        personal_reminders = []

    slots = availability.open_evenings(
        list(personal_busy) + list(business_busy),
        now=_local_now_naive(workspace_root, now_iso),
        horizon_days=horizon_days,
        evening_start=ws.get("evening_start") or availability.DEFAULT_EVENING_START,
        evening_end=ws.get("evening_end") or availability.DEFAULT_EVENING_END,
        min_block_hours=ws.get("min_block_hours") or availability.DEFAULT_MIN_BLOCK_HOURS,
    )

    default_cadence = ws.get("balance_default_cadence_days") or DEFAULT_CADENCE_DAYS
    ties = []
    for p in personal_ties(entities):
        ties.append({
            "person_id": p.get("id"),
            "name": p.get("canonical_name"),
            "gap_days": _last_touch_days(events, p, now_dt),
            "cadence_days": p.get("cadence_days"),
            "_default_cadence": default_cadence,
        })

    ranked = white_space_debt(ties, personal_reminders, slots, now_iso)
    excluded = _excluded_ties(events, now_dt)
    ranked = [c for c in ranked if c["person_id"] not in excluded]

    base = {
        "status": "all_clear",
        "ties_considered": len(ties),
        "open_slots": slots,
        "personal_reminders": personal_reminders,
        "skipped_lines": len(skipped),
    }
    if not ranked:
        return base

    top = ranked[0]
    nudge = {
        "tie_person_id": top["person_id"],
        "kind": "reconnect",
        "evidence": [
            f"last touch {round(top['gap_days'])} days ago; cadence "
            f"{round(top['cadence_days'])} days"
        ],
        "gap_days": top["gap_days"],
        "baseline_days": top["cadence_days"],
        "open_slots": [s["start"] for s in slots],
        # `kind`, not `type` — a nested "type" literal reads as an event-type
        # write to the schema-enum guard (run_source_of_truth_test).
        "proposed_action": {
            "kind": "reservation" if slots else "call",
            "venue": None,
            "draft_event_seq": None,
        },
    }

    emit_failed = False
    if emit:
        try:
            from next_seq import next_seq
            from atomic_write import atomic_append_jsonl
            seq = next_seq(str(events_path))
            atomic_append_jsonl(events_path, [{
                "seq": seq, "ts": now_iso,
                "type": "balance_nudge_suggested",
                "source_skill": "balance",
                "person_ids": [top["person_id"]] if top["person_id"] else [],
                # personal: true always (D6) — belt on top of the type-level
                # classification personal_leak.is_personal already applies.
                "data": dict(nudge, personal=True),
            }])
        except Exception:
            # Surfaced, not swallowed (second-eyes fix, 2026-07-19): a failed
            # append means no audit trail AND no dedupe next fire — the skill
            # should say so rather than render as if the write landed.
            emit_failed = True

    out = dict(base)
    out["status"] = "nudge"
    out["nudge"] = nudge
    out["runner_ups"] = ranked[1:]
    if emit_failed:
        out["emit_failed"] = True
    return out


__all__ = [
    "DEFAULT_CADENCE_DAYS",
    "DEDUPE_WINDOW_DAYS",
    "personal_ties",
    "tie_cadence_days",
    "personal_calendar_query_specs",
    "white_space_debt",
    "compute_balance",
]


if __name__ == "__main__":  # smoke
    import json, sys, tempfile
    ws = Path(tempfile.mkdtemp())
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps({
        "people": [], "workspace": {}}), encoding="utf-8")
    print(json.dumps(compute_balance(ws), indent=2))  # -> not_configured
    sys.exit(0)
