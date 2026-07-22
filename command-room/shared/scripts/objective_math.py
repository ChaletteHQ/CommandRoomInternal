#!/usr/bin/env python3
"""
objective_math.py — derived status, drift, and ranking for standing
objectives (SPEC OBJ1, DRAFT). The read-side brain: every surface reads
THIS, never a stored stamp (there is none — thread_writer rejects a stored
status field).

Doctrine (mirrors deal_health / pipeline_math):
  - Pure functions, no I/O in the compute layer; `load_objective_inputs`
    is the one assembler that touches disk (via the canonical readers).
  - Status honesty follows the binding, branching on binding.type:
      meeting  -> directional ONLY from the latest stated objective_review;
      self     -> directional ONLY from the latest objective_report;
      activity -> "moving" / "quiet since [date]" from the linked entities'
                  own events, turning directional ONLY on an unambiguous
                  deal signal (outcome, or a stage move inside the
                  lookback). A spontaneous owner report (record_report is
                  valid on any binding) also counts — it is the owner's
                  word.
    Nothing here fabricates a directional status — the bug class this
    module exists to prevent.
  - A stale directional status decays honestly: the value keeps its as_of
    date and gains stale=True once the binding's cadence has lapsed; drift
    is a separate flag with a reason a human can read.
  - Every drifting objective gets ONE structured suggested move — surfaces
    render it; a bare flag is a contract violation (the SKILL enforces the
    render).

stdlib only.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Iterable, Optional

DIRECTIONAL = ("on_track", "at_risk", "off_track", "blocked")

DEFAULT_CONFIG = {
    # meeting path: forum instances since the last harvested review before
    # the objective counts as drifting ("not discussed for N cycles")
    "drift_meeting_cycles": 2,
    # self path: missed cadence cycles before drifting; a few more before
    # the graceful-death ask ("is this still an objective?")
    "drift_self_cycles": 2,
    "death_self_cycles": 4,
    # activity path: days without any event on the linked entities
    "quiet_activity_days": 21,
    # how far back a deal stage move still reads as directional signal
    "stage_move_lookback_days": 14,
    # the active-set soft cap (overflow proposes parking, never blocks)
    "active_cap": 7,
}

SEVERITY_POINTS = {
    "stated_blocked": 4,
    "stated_off_track": 4,
    "stated_at_risk": 3,
    "drifting": 2,
    "quiet": 1,
}

_DEAL_STAGE_ORDER = ("lead", "qualified", "proposal_sent", "negotiating")


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------

def _norm_title(title: str) -> str:
    # MUST stay byte-identical to objective_state.normalize_series_key —
    # the binding side and the harvest/drift side share one fingerprint.
    s = re.sub(r"[^a-z0-9\s]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _event_ts(e: dict) -> Optional[datetime.date]:
    raw = e.get("ts") or e.get("timestamp") or e.get("date")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _event_thread_id(e: dict) -> Optional[str]:
    return e.get("primary_thread_id") or (e.get("data") or {}).get("thread_id")


def _days_between(a: datetime.date, b: datetime.date) -> int:
    return (b - a).days


def matches_series(event: dict, binding: dict) -> bool:
    """Does a meeting-family event belong to the bound review forum?
    The series fingerprint: normalized title match, plus (for
    title_and_people) at least one usual-attendee overlap — the mode that
    disambiguates generic titles like '1:1'. title_only is the
    distinctive-name override (e.g. a named leadership call) where
    attendee churn must not break the match."""
    data = event.get("data") or {}
    title = data.get("title") or data.get("summary") or ""
    if _norm_title(title) != (binding.get("series_key") or ""):
        return False
    if (binding.get("series_match") or "title_and_people") == "title_only":
        return True
    people = set(binding.get("series_people") or [])
    if not people:
        return True  # defensive: legacy binding without people stored
    attendees = set(event.get("person_ids") or data.get("person_ids") or [])
    return bool(people & attendees)


def forum_objectives(open_objectives: list[dict], meeting_title: str,
                     attendee_person_ids: Optional[list[str]] = None
                     ) -> list[dict]:
    """Which open meeting-bound objectives claim THIS meeting as their
    review forum — the one call meeting-notes' harvest step makes before
    reading anything. Returns [{thread_id, name, statement}]. Empty list =
    no harvest work for this transcript (the overwhelmingly common case)."""
    probe = {"type": "meeting", "data": {"title": meeting_title},
             "person_ids": list(attendee_person_ids or [])}
    out: list[dict] = []
    for row in open_objectives or []:
        obj = row.get("objective")
        if not isinstance(obj, dict):
            continue
        binding = obj.get("binding") or {}
        if binding.get("type") != "meeting":
            continue
        if matches_series(probe, binding):
            out.append({"thread_id": row.get("thread_id"),
                        "name": row.get("name"),
                        "statement": obj.get("statement")})
    return out


def forum_instances(meeting_events: Iterable[dict], binding: dict,
                    after: Optional[datetime.date] = None) -> list[dict]:
    """Distinct review-forum meetings (deduped by source_ref so a meeting
    plus its meeting_processed receipt never double-counts), optionally
    only those strictly after a date."""
    seen: set[str] = set()
    out: list[dict] = []
    for e in meeting_events or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") not in ("meeting", "meeting_processed"):
            continue
        if not matches_series(e, binding):
            continue
        ts = _event_ts(e)
        if ts is None:
            continue
        if after is not None and ts <= after:
            continue
        ref = ((e.get("data") or {}).get("source_ref")
               or e.get("source_ref") or f"ts:{ts.isoformat()}")
        if ref in seen:
            continue
        seen.add(ref)
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# the compute layer (pure)
# ---------------------------------------------------------------------------

def _latest_stated(objective_events: list[dict], thread_id: str,
                   types: tuple[str, ...]) -> Optional[dict]:
    best = None
    best_key = None
    for e in objective_events or []:
        if e.get("type") not in types or _event_thread_id(e) != thread_id:
            continue
        ts = _event_ts(e)
        if ts is None:
            continue
        key = (ts, e.get("seq") or 0)
        if best_key is None or key > best_key:
            best, best_key = e, key
    return best


def _linked_ids(obj: dict) -> list[str]:
    binding = obj.get("binding") or {}
    ids = list(binding.get("entity_ids") or [])
    anchor = obj.get("anchor_thread_id")
    if anchor and anchor not in ids:
        ids.append(anchor)
    return ids


def _deal_signal(linked_ids: list[str], threads_by_id: dict,
                 deal_events: list[dict], today: datetime.date,
                 lookback_days: int) -> Optional[dict]:
    """The ONLY directional read the activity path allows: an unambiguous
    deal signal on a linked deal thread. Returns
    {value, source, as_of, detail} or None."""
    for tid in linked_ids:
        t = threads_by_id.get(tid)
        if not isinstance(t, dict) or t.get("kind") != "deal":
            continue
        deal = t.get("deal") if isinstance(t.get("deal"), dict) else None
        if deal and deal.get("outcome") == "won":
            return {"value": "on_track", "source": "deal_won",
                    "as_of": deal.get("closed_at"),
                    "detail": t.get("canonical_name") or tid}
        if deal and deal.get("outcome") == "lost":
            return {"value": "off_track", "source": "deal_lost",
                    "as_of": deal.get("closed_at"),
                    "detail": t.get("canonical_name") or tid}
    # recent stage moves (forward = on_track, backward = at_risk)
    best = None
    best_ts = None
    for e in deal_events or []:
        if e.get("type") != "deal_stage_changed":
            continue
        if _event_thread_id(e) not in linked_ids:
            continue
        ts = _event_ts(e)
        if ts is None or _days_between(ts, today) > lookback_days:
            continue
        if best_ts is None or ts > best_ts:
            best, best_ts = e, ts
    if best is not None:
        data = best.get("data") or {}
        try:
            fwd = (_DEAL_STAGE_ORDER.index(data.get("to_stage"))
                   > _DEAL_STAGE_ORDER.index(data.get("from_stage")))
        except ValueError:
            return None
        return {"value": "on_track" if fwd else "at_risk",
                "source": "deal_stage", "as_of": best_ts.isoformat(),
                "detail": data.get("to_stage")}
    return None


def _suggest_move(row: dict, obj: dict, open_commitments: list[dict],
                  primary_user_id: Optional[str]) -> dict:
    """ONE structured suggested move per surfaced objective — never a bare
    flag. Priority: an open commitment on the linked work (the concrete
    next step) > poke the non-CEO owner > raise it in its own forum >
    block time on it."""
    linked = set(_linked_ids(obj)) | {row["thread_id"]}
    best = None
    for c in open_commitments or []:
        if not isinstance(c, dict):
            continue
        if c.get("thread_id") in linked:
            if best is None or (c.get("due") or "9999") < (best.get("due") or "9999"):
                best = c
    if best is not None:
        return {"kind": "commitment", "title": best.get("title"),
                "owner_id": best.get("owner_id"), "due": best.get("due")}
    owner = row.get("owner_person_id")
    if owner and primary_user_id and owner != primary_user_id:
        return {"kind": "poke_owner", "owner_id": owner}
    binding = obj.get("binding") or {}
    if binding.get("type") == "meeting":
        return {"kind": "raise_in_forum",
                "series_key": binding.get("series_key")}
    return {"kind": "block_time"}


def compute_objective_health(
    open_objectives: list[dict],
    *,
    objective_events: list[dict],
    meeting_events: list[dict],
    deal_events: list[dict],
    activity_by_thread: dict,
    threads_by_id: dict,
    open_commitments: list[dict],
    today: datetime.date,
    primary_user_id: Optional[str] = None,
    config: Optional[dict] = None,
) -> list[dict]:
    """Per open objective: derived status, drift flag with a readable
    reason, one suggested move, severity. Pure — no I/O.

    Row shape:
      {thread_id, name, owner_person_id, binding_type, malformed,
       status: {value, kind: directional|movement|none, source, as_of,
                stale},
       drift: {flagged, reason, death_proposal},
       suggested_move: {...} | None,
       severity: int}
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})

    out: list[dict] = []
    for row in open_objectives or []:
        obj = row.get("objective")
        if row.get("malformed") or not isinstance(obj, dict):
            out.append({
                "thread_id": row.get("thread_id"), "name": row.get("name"),
                "owner_person_id": row.get("owner_person_id"),
                "binding_type": None, "malformed": True,
                "status": {"value": None, "kind": "none", "source": None,
                           "as_of": None, "stale": False},
                "drift": {"flagged": False, "reason": None,
                          "death_proposal": False},
                "suggested_move": None, "severity": 0,
            })
            continue

        binding = obj.get("binding") or {}
        b_type = binding.get("type")
        opened = None
        try:
            opened = datetime.date.fromisoformat(
                (obj.get("opened_at") or "")[:10])
        except ValueError:
            pass

        status = {"value": None, "kind": "none", "source": None,
                  "as_of": None, "stale": False}
        drift = {"flagged": False, "reason": None, "death_proposal": False}

        # The owner's word is valid on ANY binding — take the freshest of
        # review (meeting-bound only, enforced at the writer) and report.
        stated = _latest_stated(
            objective_events, row["thread_id"],
            ("objective_review", "objective_report"))
        stated_ts = _event_ts(stated) if stated else None
        if stated is not None and stated_ts is not None:
            status = {
                "value": (stated.get("data") or {}).get("status"),
                "kind": "directional",
                "source": ("review" if stated.get("type") == "objective_review"
                           else "report"),
                "as_of": stated_ts.isoformat(),
                "stale": False,
            }

        if b_type == "meeting":
            baseline = stated_ts or opened
            missed = forum_instances(meeting_events, binding, after=baseline)
            if len(missed) >= cfg["drift_meeting_cycles"]:
                drift = {"flagged": True,
                         "reason": (f"not discussed in its review meeting "
                                    f"for {len(missed)} sessions"),
                         "death_proposal": False}
                if status["kind"] == "directional":
                    status["stale"] = True
        elif b_type == "self":
            cadence = binding.get("cadence_days") or 7
            baseline = stated_ts or opened
            if baseline is not None:
                missed_cycles = _days_between(baseline, today) // max(cadence, 1)
                if missed_cycles >= cfg["drift_self_cycles"]:
                    drift = {"flagged": True,
                             "reason": (f"no status from its owner in "
                                        f"{missed_cycles} check-ins"),
                             "death_proposal":
                                 missed_cycles >= cfg["death_self_cycles"]}
                    if status["kind"] == "directional":
                        status["stale"] = True
        elif b_type == "activity":
            linked = _linked_ids(obj)
            signal = _deal_signal(linked, threads_by_id, deal_events, today,
                                  cfg["stage_move_lookback_days"])
            # an explicit owner report outranks a derived deal signal only
            # if fresher; otherwise the unambiguous signal wins
            if signal is not None and (
                    stated_ts is None
                    or (signal.get("as_of") or "") >= stated_ts.isoformat()):
                status = {"value": signal["value"], "kind": "directional",
                          "source": signal["source"],
                          "as_of": signal.get("as_of"), "stale": False}
            last = None
            # the objective's OWN thread joins the movement read (OBJ2
            # consumer fix): a signal the CEO confirmed as a move on this
            # objective is primary-stamped onto the objective thread —
            # before this join, confirming a link could never change the
            # quiet/moving read
            for tid in [row["thread_id"], *linked]:
                a = activity_by_thread.get(tid)
                if a is None:
                    continue
                a_date = a.ts.date() if hasattr(a.ts, "date") else a.ts
                if last is None or a_date > last:
                    last = a_date
            if last is None:
                last = opened
            if last is not None:
                quiet_days = _days_between(last, today)
                if quiet_days >= cfg["quiet_activity_days"]:
                    drift = {"flagged": True,
                             "reason": (f"no movement on its linked work "
                                        f"in {quiet_days} days"),
                             "death_proposal": False}
                    if status["kind"] != "directional" or status["stale"]:
                        status = {"value": "quiet", "kind": "movement",
                                  "source": "activity",
                                  "as_of": last.isoformat(), "stale": False}
                elif status["kind"] == "none":
                    status = {"value": "moving", "kind": "movement",
                              "source": "activity",
                              "as_of": last.isoformat(), "stale": False}

        severity = 0
        if status["kind"] == "directional" and not status["stale"]:
            severity += SEVERITY_POINTS.get(
                "stated_" + str(status["value"]), 0)
        if drift["flagged"]:
            severity += SEVERITY_POINTS["drifting"]
        if status.get("value") == "quiet":
            severity += SEVERITY_POINTS["quiet"]

        needs_move = drift["flagged"] or (
            status["kind"] == "directional"
            and status["value"] in ("at_risk", "off_track", "blocked"))
        move = (_suggest_move(row, obj, open_commitments, primary_user_id)
                if needs_move else None)

        out.append({
            "thread_id": row["thread_id"], "name": row.get("name"),
            "owner_person_id": row.get("owner_person_id"),
            "binding_type": b_type, "malformed": False,
            "status": status, "drift": drift,
            "suggested_move": move, "severity": severity,
        })

    out.sort(key=lambda r: (-r["severity"], r.get("name") or ""))
    return out


def due_self_reports(health_rows: list[dict], open_objectives: list[dict],
                     objective_events: list[dict],
                     today: datetime.date) -> list[dict]:
    """Which self-bound objectives the weekly touch should ASK about: the
    cadence has elapsed since the last report (or since creation). The
    single weekly batch — never a per-objective ping."""
    obj_by_id = {r["thread_id"]: r.get("objective") or {}
                 for r in open_objectives or []}
    due: list[dict] = []
    for h in health_rows or []:
        if h.get("binding_type") != "self" or h.get("malformed"):
            continue
        obj = obj_by_id.get(h["thread_id"]) or {}
        cadence = (obj.get("binding") or {}).get("cadence_days") or 7
        stated = _latest_stated(objective_events, h["thread_id"],
                                ("objective_report", "objective_review"))
        baseline = _event_ts(stated) if stated else None
        if baseline is None:
            try:
                baseline = datetime.date.fromisoformat(
                    (obj.get("opened_at") or "")[:10])
            except ValueError:
                baseline = None
        if baseline is None or _days_between(baseline, today) >= cadence:
            due.append(h)
    return due


# ---------------------------------------------------------------------------
# render helpers (drop-empty, leak-clean, read-only prose — FB-20)
# ---------------------------------------------------------------------------

_STATUS_WORDS = {
    "on_track": "on track", "at_risk": "at risk", "off_track": "off track",
    "blocked": "blocked", "moving": "moving", "quiet": "quiet",
}


def _move_phrase(move: Optional[dict],
                 names_by_person_id: Optional[dict] = None) -> str:
    names = names_by_person_id or {}
    if not move:
        return ""
    if move["kind"] == "commitment" and move.get("title"):
        return f"next step already on the floor: {move['title']}"
    if move["kind"] == "poke_owner":
        who = names.get(move.get("owner_id")) or "its owner"
        return f"worth a nudge to {who}"
    if move["kind"] == "raise_in_forum":
        return "worth putting on the next agenda of its review meeting"
    return "worth blocking 30 minutes on it"


def brief_lines(health_rows: list[dict], max_lines: int = 2,
                names_by_person_id: Optional[dict] = None) -> list[str]:
    """The morning-brief contribution: at most `max_lines` ready-to-render
    prose lines, drop-empty. Read-only per FB-20 — a surfaced line never
    asks for input; the weekly touch is where asks live. Line 1 is the
    single worst drifting/at-risk objective WITH its suggested move; line 2
    is the focus headline. Zero objectives -> emit nothing."""
    rows = [r for r in health_rows or [] if not r.get("malformed")]
    if not rows:
        return []
    lines: list[str] = []
    # An objective whose graceful-death ask is pending has left the nagging
    # business — the weekly touch asks "still an objective?"; the brief
    # stays quiet about it (no stale drift line firing forever).
    alertable = [r for r in rows
                 if not (r.get("drift") or {}).get("death_proposal")]
    worst = (alertable[0] if alertable and alertable[0]["severity"] > 0
             else None)
    if worst is not None:
        s = worst["status"]
        if worst["drift"]["flagged"]:
            desc = worst["drift"]["reason"]
        else:
            word = _STATUS_WORDS.get(s.get("value"), "needs a look")
            desc = f"{word} (as of {s.get('as_of')})" if s.get("as_of") else word
        move = _move_phrase(worst.get("suggested_move"), names_by_person_id)
        line = f"Objective drifting: {worst['name']} — {desc}"
        if move:
            line += f". {move.capitalize()}"
        line += ". Say `show my objectives` for the full picture."
        lines.append(line)
    if len(lines) < max_lines:
        n = len(rows)
        steady = [r for r in rows if r["severity"] == 0]
        death_pending = [r for r in rows
                         if (r.get("drift") or {}).get("death_proposal")]
        if worst is None and death_pending:
            # honest without nagging: these wait for the weekly touch
            lines.append(
                f"Objectives: {n} in focus — {len(death_pending)} waiting "
                "on your weekly check-in.")
        elif worst is None:
            lines.append(
                f"Objectives: all {n} in focus look steady — nothing "
                "needs your eyes today.")
        elif steady:
            lines.append(
                f"Objectives: {n} in focus, {len(steady)} steady.")
    return lines[:max_lines]


def recap_rows(health_rows: list[dict],
               names_by_person_id: Optional[dict] = None) -> list[str]:
    """The weekly-recap section body: one line per open objective, ranked
    worst-first (the section is capped upstream by recap density rules).
    Drop-empty."""
    names = names_by_person_id or {}
    out: list[str] = []
    for r in health_rows or []:
        if r.get("malformed"):
            out.append(f"- {r['name']} — needs repair (say `show my "
                       "objectives` to fix it)")
            continue
        s = r["status"]
        if s["kind"] == "directional":
            word = _STATUS_WORDS.get(s.get("value"), "unknown")
            stale = " (stale)" if s.get("stale") else ""
            bit = f"{word}{stale}"
            if s.get("as_of"):
                bit += f", as of {s['as_of']}"
        elif s["kind"] == "movement":
            bit = ("moving" if s.get("value") == "moving"
                   else f"quiet since {s.get('as_of')}")
        else:
            bit = "no signal yet"
        line = f"- {r['name']} — {bit}"
        if r["drift"]["flagged"]:
            line += f". Drifting: {r['drift']['reason']}"
            move = _move_phrase(r.get("suggested_move"), names)
            if move:
                line += f" — {move}"
        owner = names.get(r.get("owner_person_id"))
        if owner:
            line += f" (owner: {owner})"
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# the one assembler that touches disk
# ---------------------------------------------------------------------------

def load_objective_inputs(workspace_root) -> dict:
    """Assemble everything compute_objective_health needs, via the
    canonical readers only. Returns {open_objectives, objective_events,
    meeting_events, deal_events, activity_by_thread, threads_by_id,
    open_commitments, primary_user_id, skipped} — a non-empty `skipped`
    MUST surface as a banner (never swallowed)."""
    import json as _json
    from pathlib import Path as _Path

    import objective_state
    from cru_match import load_events_defensively, load_open_commitments
    from thread_activity import apply_reclassifications, derive_from_events

    ws = _Path(workspace_root)
    open_objectives = objective_state.list_open_objectives(ws)

    events_path = ws / "_hq" / "data" / "events.jsonl"
    events, skipped = ([], [])
    if events_path.exists():
        events, skipped = load_events_defensively(events_path)
    # OBJ2 consumer fix: honor reclassification supersession BEFORE any
    # derivation — a confirmed objective link counts, a dismissed one
    # stops counting. This surface adopts the patched seam as a unit
    # (activity + deal + objective + commitment-attribution reads all see
    # the same envelopes); other day-count surfaces still read raw by
    # deliberate scope (F-54).
    events = apply_reclassifications(events)
    objective_events = [e for e in events
                       if e.get("type") in objective_state.OBJECTIVE_EVENT_TYPES]
    meeting_events = [e for e in events
                      if e.get("type") in ("meeting", "meeting_processed")]
    deal_events = [e for e in events if e.get("type") in
                   ("deal_created", "deal_updated", "deal_stage_changed",
                    "deal_won", "deal_lost")]

    threads_by_id: dict = {}
    primary_user_id = None
    try:
        data = _json.loads((ws / "_hq" / "data" / "entities.json")
                           .read_text(encoding="utf-8"))
        container = (data.get("entities")
                     if isinstance(data.get("entities"), dict) else data)
        for t in (container.get("threads") or container.get("projects") or []):
            if isinstance(t, dict) and t.get("id"):
                threads_by_id[t["id"]] = t
        ws_settings = (container.get("workspace")
                       or data.get("workspace") or {})
        primary_user_id = ws_settings.get("user_id")
        if not primary_user_id:
            for p in container.get("people") or []:
                if isinstance(p, dict) and p.get("is_primary_user"):
                    primary_user_id = p.get("id")
                    break
    except (OSError, ValueError):
        pass

    open_commitments = []
    try:
        # attribution reads the PATCHED envelope (same seam as every input
        # above): a commitment the user unlinked from an objective must not
        # come back through the suggested-move read. load_open_commitments
        # owns open/closed state; only the thread attribution re-reads here.
        patched_by_seq = {e["seq"]: e for e in events
                          if isinstance(e, dict)
                          and isinstance(e.get("seq"), int)}
        for c in load_open_commitments(events_path) or []:
            if not isinstance(c, dict):
                continue
            d = c.get("data") or {}
            attributed = (patched_by_seq.get(c["seq"], c)
                          if isinstance(c.get("seq"), int) else c)
            open_commitments.append({
                "thread_id": _event_thread_id(attributed),
                "title": d.get("title"),
                "owner_id": (d.get("owner_id") or c.get("owner_id")
                             or d.get("owner_person_id")),
                "due": d.get("due"),
            })
    except Exception:
        open_commitments = []

    return {
        "open_objectives": open_objectives,
        "objective_events": objective_events,
        "meeting_events": meeting_events,
        "deal_events": deal_events,
        # derived over the SAME patched stream as every other input above —
        # never a fresh raw read (the two would disagree the moment a
        # reclassification exists)
        "activity_by_thread": derive_from_events(events),
        "threads_by_id": threads_by_id,
        "open_commitments": open_commitments,
        "primary_user_id": primary_user_id,
        "skipped": skipped,
    }


__all__ = [
    "DEFAULT_CONFIG",
    "SEVERITY_POINTS",
    "DIRECTIONAL",
    "matches_series",
    "forum_instances",
    "compute_objective_health",
    "due_self_reports",
    "brief_lines",
    "recap_rows",
    "load_objective_inputs",
]
