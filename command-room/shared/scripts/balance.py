#!/usr/bin/env python3
"""
Balance Guardian math (SPEC BAL1) — the personal white-space surface's
computation, in code (the Bug #99 model: never LLM arithmetic).

The weekly Sunday surface reads the PERSONAL lane only — `tie: "personal"`
people, personal reminders, and the open evenings on the declared personal +
family calendars cross-checked against business busy — and surfaces the single
most-starved personal relationship with real open evenings attached. The
firewall is the feature: everything this module emits is personal-lane (the
Sunday `balance_nudge_suggested` from `compute_balance` and the `book`
confirm-path `balance_nudge_actioned` from `record_actioned` — both classified
personal by `personal_leak.is_personal` and dropped from every org-scoped read
by `events_io.load_events_org_scoped`), and this surface renders ONLY at
`surface="m_facing"`.

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

# Per-tie SUGGEST-side dedupe window: a tie nudged in the last 7 days is not
# re-nudged by the next fire (`_excluded_ties`). This is the ONLY thing the
# constant governs. The confirm-path writer deliberately does NOT use it —
# `record_actioned` keys idempotency on the identity of the card being clicked
# (tie + source_nudge_seq), not on elapsed time (OI3FIX F-1, 2026-07-26).
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
            # floor. Unbounded exclusion here would let a YEARS-old (and now
            # unrepeatable — LIFECYCLE1 retired the chat that wrote them)
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
      {"status": "nudge", "nudge": {...}, "nudge_seq": int|None, ...}
          — exactly ONE nudge; one `balance_nudge_suggested` appended when
          emit=True.

    `nudge_seq` is the seq of the row that was actually appended, and it is the
    card's IDENTITY: the widget row carries it and the `book` confirm hands it
    straight back as `record_actioned(source_nudge_seq=...)`, which is what
    makes the confirm path idempotent per card instead of per clock (OI3FIX
    F-1). It is None — honestly, never faked — when `emit=False` or the append
    failed (`emit_failed`); there is no suggestion event to link a confirm to
    in either case.

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

    # BAL1 FB-plumbing item 3 — zero personal ties is a REFUSAL, never an
    # all-clear. A workspace with a personal calendar connected but nobody
    # tagged `tie: "personal"` has given Balance nothing to protect — rendering
    # "white space looks healthy" there is a false all-clear (the same honesty
    # line as the unconfigured refusal, one step later in setup). Emit nothing
    # and tell the user what's missing: who counts. Distinct status so the
    # SKILL.md renders the tie-specific refusal, not the calendar one.
    if not ties:
        return {"status": "no_personal_ties",
                "reason": "no personal ties tagged yet — tell me who counts",
                "ties_considered": 0}

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
    emitted_seq = None
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
            # Only after the append RETURNS — a seq handed out for a row that
            # never landed would key a confirm to a nonexistent suggestion.
            emitted_seq = seq
        except Exception:
            # Surfaced, not swallowed (second-eyes fix, 2026-07-19): a failed
            # append means no audit trail AND no dedupe next fire — the skill
            # should say so rather than render as if the write landed.
            emit_failed = True

    out = dict(base)
    out["status"] = "nudge"
    # `nudge` stays byte-equal to the emitted payload (run_balance_test pins
    # `ev["data"] == dict(res["nudge"], personal=True)`), so the card's seq
    # rides ONE level up rather than as a self-reference inside the row.
    out["nudge"] = nudge
    out["nudge_seq"] = emitted_seq
    out["runner_ups"] = ranked[1:]
    if emit_failed:
        out["emit_failed"] = True
    return out


class BalanceWriterError(ValueError):
    """Bad input to the confirm-path writer. Loud, never silent — a linkage
    written against a missing tie is worse than no linkage at all."""


_ACTION_KINDS = ("reservation", "call")


def record_actioned(
    workspace_root,
    *,
    tie_person_id: str,
    source_nudge_seq: Optional[int] = None,
    draft_event_seq: Optional[int] = None,
    venue: Optional[str] = None,
    hold_start: Optional[str] = None,
    action_kind: str = "reservation",
    source_skill: str = "balance",
) -> dict:
    """THE confirm-path writer: the single gated closure for a `book` click
    (OI-3 B-1, 2026-07-26).

    Before this existed, `balance_nudge_actioned` had no named writer anywhere
    in the tree — both prose sites (balance SKILL Step 4.3 and the
    apply-choices `balance` dispatch) told the executing model to "append the
    follow-on linkage", and `personal_leak.py` only CLASSIFIED the type. An
    executing model had to hand-roll the append on the one path in this skill
    that holds a calendar slot and queues an outbound draft — the Bug #81
    architectural class (Gate 3) sitting on top of the F-15 invisible-code
    class (Gate 17). This is the `objective_state` shape: one function, one
    event, loud on bad input, and — since OI3FIX — idempotent on the IDENTITY
    of the thing itself, the way `objective_state` keys on entity state rather
    than on elapsed time.

    Called ONLY from the apply-choices `balance` dispatch, and only after BOTH
    user-click-gated legs have run: the tentative personal-calendar hold
    (calendar-writer's Phase 5/6 consent path) and the draft queue. Nothing
    here books, sends, or spends — it records that the user's click happened.

    IDEMPOTENCY IS KEYED ON IDENTITY, NOT ON A CLOCK (OI3FIX F-1, 2026-07-26).
    The key is `(tie_person_id, source_nudge_seq)` — the tie plus the seq of
    the `balance_nudge_suggested` row this click answers. That gives three
    behaviours a time window could not give at any setting:

      * the SAME card clicked twice is a NO-OP permanently, with no decay —
        strictly stronger than a 7-day window;
      * a genuinely NEW card writes a new linkage, correctly, even a minute
        later — no duration left to configure, no product ruling deferred;
      * a corrected confirm carrying a new seq LANDS instead of vanishing.

    The first version keyed on `(tie, DEDUPE_WINDOW_DAYS)`. That made one
    number do two incompatible jobs — a double-click guard (which wants
    seconds) and a second-booking policy (never ruled) — and it silently
    discarded a corrected confirm: disk kept the old venue while the ack said
    "already held". On the one path in this skill that holds a calendar slot
    and queues an outbound draft, that is a defect, not a tuning choice.

    Args:
      tie_person_id: the tie's `person_id` VERBATIM off the widget row. Must
        name an active `tie: "personal"` person — the lane is the point.
      source_nudge_seq: `compute_balance`'s `nudge_seq` for the fire that
        rendered this card, VERBATIM off the widget row. REQUIRED — it is the
        dedupe key, and a missing one would collapse every click on this tie
        into a single identity, which is the bug this replaces. It also closes
        the linkage back to the suggestion it answers (previously the row
        referenced the draft but never the nudge).
      draft_event_seq: seq of the queued venue-outreach draft event, or None
        when the reconnect carried no email leg (SKILL Step 3).
      venue / hold_start: the confirmed venue name and the ISO start of the
        held evening, both optional. Personal-lane content by construction —
        which is exactly why the type pin matters.
      action_kind: mirrors the nudge's `proposed_action.kind`.

    Returns {"status": "actioned" | "already_actioned", ...}. `already_actioned`
    is the honest NO-OP for a re-dispatched click on the SAME card; it carries
    the `recorded` values already on disk, plus a `diverged` map naming any
    field the caller passed differently, so the ack can say what was kept
    rather than implying nothing was lost.

    Raises BalanceWriterError on an empty/unknown tie, a work-lane tie, a
    missing or non-integer source nudge seq, a non-integer draft seq, or an
    unknown action_kind.
    """
    if not tie_person_id or not str(tie_person_id).strip():
        raise BalanceWriterError(
            "tie_person_id is required — pass the widget row's person_id "
            "verbatim; a linkage with no tie is unreadable and unrevocable")
    tie_person_id = str(tie_person_id).strip()
    if action_kind not in _ACTION_KINDS:
        raise BalanceWriterError(
            f"action_kind must be one of {list(_ACTION_KINDS)}, got: "
            f"{action_kind!r}")
    if source_nudge_seq is None:
        raise BalanceWriterError(
            "source_nudge_seq is required — pass compute_balance's "
            "`nudge_seq` verbatim off the widget row. It is the idempotency "
            "key; without it every click on this tie shares one identity and "
            "a corrected confirm is silently discarded. A None here means the "
            "suggestion row never landed (emit=False, or emit_failed) — "
            "re-fire balance rather than linking to a nudge that isn't on "
            "disk")
    # bool BEFORE int: isinstance(True, int) is True, so a truthy flag would
    # otherwise pass as seq 1 — and on the DEDUPE KEY that silently collides a
    # click with whatever card really carried seq 1.
    if isinstance(source_nudge_seq, bool) or not isinstance(source_nudge_seq, int):
        raise BalanceWriterError(
            f"source_nudge_seq must be an int seq, got: {source_nudge_seq!r}")
    if draft_event_seq is not None and (
            isinstance(draft_event_seq, bool)
            or not isinstance(draft_event_seq, int)):
        raise BalanceWriterError(
            f"draft_event_seq must be an int seq or None, got: "
            f"{draft_event_seq!r}")

    entities = _load_entities(workspace_root)
    tie = next((p for p in personal_ties(entities)
                if p.get("id") == tie_person_id), None)
    if tie is None:
        raise BalanceWriterError(
            f"{tie_person_id!r} is not an active personal tie — Balance's "
            "confirm path writes the PERSONAL lane only (a work tie belongs "
            "to relationship-moves; the two partition the entity set)")

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events, _skipped = load_events_defensively(events_path)

    # Idempotency: THIS card already closed. Identity only — no clock is read
    # here, which is also why nothing in this scan can flip on the hour the
    # suite runs. A row written before source_nudge_seq existed carries None
    # and therefore matches no real card, so it blocks nothing.
    hold_start = str(hold_start) if hold_start else None
    for ev in events:
        if ev.get("type") != "balance_nudge_actioned":
            continue
        d = ev.get("data") or {}
        if d.get("tie_person_id") != tie_person_id:
            continue
        if d.get("source_nudge_seq") != source_nudge_seq:
            continue
        prior = d.get("proposed_action") or {}
        recorded = {
            "venue": prior.get("venue"),
            "draft_event_seq": prior.get("draft_event_seq"),
            "hold_start": d.get("hold_start"),
            "action_kind": prior.get("kind"),
        }
        passed = {"venue": venue, "draft_event_seq": draft_event_seq,
                  "hold_start": hold_start, "action_kind": action_kind}
        # The NO-OP is correct here (same card, same click) but it must not be
        # SILENT: name every field the caller passed differently so the ack can
        # say what was kept instead of implying nothing was lost.
        diverged = {k: {"recorded": recorded[k], "passed": passed[k]}
                    for k in recorded if recorded[k] != passed[k]}
        out = {"status": "already_actioned",
               "tie_person_id": tie_person_id,
               "source_nudge_seq": source_nudge_seq,
               "seq": ev.get("seq"),
               "draft_event_seq": recorded["draft_event_seq"],
               "recorded": recorded}
        if diverged:
            out["diverged"] = diverged
        return out

    # `kind`, not `type`, on the nested dict — a nested "type" literal reads as
    # an event-type write to the schema-enum guard (run_source_of_truth_test).
    # Shape mirrors the `balance_nudge_suggested` payload so the two lane rows
    # are join-compatible.
    data = {
        "tie_person_id": tie_person_id,
        # The linkage back to the suggestion it answers — an id, not tie-plus-
        # time proximity.
        "source_nudge_seq": source_nudge_seq,
        "kind": "reconnect",
        # personal: true always (D6) — belt on top of the type-level
        # classification personal_leak.is_personal already applies.
        "personal": True,
        "proposed_action": {
            "kind": action_kind,
            "venue": venue,
            "draft_event_seq": draft_event_seq,
        },
    }
    if hold_start:
        data["hold_start"] = hold_start

    from event_gate import append_event
    append_event(events_path, [{
        "type": "balance_nudge_actioned",
        "source_skill": source_skill,
        "person_ids": [tie_person_id],
        "data": data,
    }], holder=source_skill)
    return {"status": "actioned", "tie_person_id": tie_person_id,
            "source_nudge_seq": source_nudge_seq,
            "draft_event_seq": draft_event_seq}


__all__ = [
    "DEFAULT_CADENCE_DAYS",
    "DEDUPE_WINDOW_DAYS",
    "BalanceWriterError",
    "personal_ties",
    "tie_cadence_days",
    "personal_calendar_query_specs",
    "white_space_debt",
    "compute_balance",
    "record_actioned",
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
