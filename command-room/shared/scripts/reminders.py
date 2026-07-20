#!/usr/bin/env python3
"""
Reminders v1 — builders + pure reader (v4.6.0 W4a, SPEC V4.5.2 Wave 4).

Reminders are the user's own pins: "remind me about X on Friday" → a row on
the morning brief from that day, EVERY day, until the user clears or pushes
it. They are NOT commitments — opposite lifecycle (pin-until-cleared: never
auto-ranked, never auto-faded, never chased) — so they are their own event
family (`reminder` / `reminder_updated` / `reminder_cleared`), never a
commitment kind. They never enter commitment buckets, counts, chase, or
triage (the loaders ignore them structurally: different `type`).

HARD RULE (gate-enforced in event_gate.py, re-enforced here, re-filtered on
read): `data.origin` is `user_explicit` ONLY. No skill, sweep, or scheduled
task may ever mint a reminder. A reminder exists because the user said
"remind me" — that is its entire identity.

Recurrence lives HERE, not on commitments (M decision 2026-07-09):
`repeat` is `"weekly"` | `"monthly"` | `{"every_days": N}` (cron-lite). There
is NO scheduler — a cleared repeating reminder re-arms because the READER
derives the next occurrence from the clear event + the repeat rule at render
time (`derive-next-on-read`, same append-only discipline as commitment_state).

Privacy: `personal: true` reminders render ONLY in M-facing surfaces (the
morning brief, show-my-reminders). `active_reminders` defaults to
`surface="client_facing"`, which EXCLUDES personal rows — a client-facing
render path that forgets to pass a surface gets the safe behavior. (Broader
still: no client-facing deliverable, team-intelligence output, or export
should render reminders at all; the default-deny filter is the hard floor,
not permission.) `personal` defaults to True when the reminder carries no
business reference (`ref` / `primary_thread_id`) — a tracked-PERSON
reference alone stays personal (PGUARD1 D3).

A reminder MAY carry `ref` (a commitment/event id) for context — "remind me
about the Pedro chase Friday". The ref is a POINTER, never a coupling:
clearing the reminder does not close the commitment, closing the commitment
does not clear the reminder.

Builders are construction-only (house convention, meeting_capture.py):
append through `event_gate.append_event`, which auto-stamps seq/ts inside
the writer lock and enforces the origin rule a second time.
"""
from __future__ import annotations

import calendar
import datetime as _dt
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

try:
    from event_gate import REMINDER_ORIGIN, append_event, new_reminder_id
except ImportError:  # direct-path import (tests, bash one-liners)
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from event_gate import REMINDER_ORIGIN, append_event, new_reminder_id

REMINDER_TYPES = ("reminder", "reminder_updated", "reminder_cleared")

# Escalation thresholds (M choice ②, 2026-07-08): a pinned reminder ignored
# 3 days renders bold; 7 days moves to the top of the brief. "Ignored" means
# no user touch (keep / push / edit) since the current occurrence started.
ESCALATE_BOLD_DAYS = 3
ESCALATE_TOP_DAYS = 7

# Upcoming window (M choice ①): remind_from within this many days renders in
# the lighter "Upcoming reminders" section — no daily ask yet.
UPCOMING_WINDOW_DAYS = 3

_ACTIONS = ("push", "keep", "edit")


class ReminderError(ValueError):
    """A reminder construction/validation failure. Fail loud at build time —
    the gate would reject it anyway; this error says why sooner."""


# -----------------------------------------------------------------------------
# Dates + repeat rules
# -----------------------------------------------------------------------------


def _as_date(value, field: str = "date") -> _dt.date:
    """Parse a date from a date/datetime or an ISO string (full timestamps
    tolerated — the date part wins)."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    s = str(value or "").strip()
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        raise ReminderError(
            f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}"
        )


def validate_repeat(repeat):
    """Normalize/validate a repeat rule. None passes through (one-shot).
    Accepted: "weekly" | "monthly" | {"every_days": N>=1} (cron-lite)."""
    if repeat is None:
        return None
    if repeat in ("weekly", "monthly"):
        return repeat
    if isinstance(repeat, dict):
        keys = set(repeat)
        n = repeat.get("every_days")
        if keys == {"every_days"} and isinstance(n, int) and not isinstance(
            n, bool
        ) and n >= 1:
            return {"every_days": n}
    raise ReminderError(
        f"repeat must be 'weekly', 'monthly', or {{'every_days': N>=1}}, "
        f"got {repeat!r}"
    )


def next_occurrence(occurrence, repeat) -> _dt.date:
    """The next occurrence strictly after `occurrence` per the repeat rule.
    Monthly clamps to the last day of a shorter month (Jan 31 → Feb 28)."""
    occ = _as_date(occurrence, "occurrence")
    rule = validate_repeat(repeat)
    if rule is None:
        raise ReminderError("next_occurrence needs a repeat rule (one-shot has none)")
    if rule == "weekly":
        return occ + _dt.timedelta(days=7)
    if rule == "monthly":
        year, month = (occ.year + 1, 1) if occ.month == 12 else (occ.year, occ.month + 1)
        day = min(occ.day, calendar.monthrange(year, month)[1])
        return _dt.date(year, month, day)
    return occ + _dt.timedelta(days=rule["every_days"])


# -----------------------------------------------------------------------------
# Builders — construction-only; append through event_gate.append_event.
# -----------------------------------------------------------------------------


def build_reminder_event(
    summary: str,
    *,
    remind_from,
    due=None,
    personal: Optional[bool] = None,
    ref: Optional[str] = None,
    repeat=None,
    origin: str = REMINDER_ORIGIN,
    source_skill: str = "show-my-reminders",
    primary_thread_id: Optional[str] = None,
    person_ids: Optional[List[str]] = None,
) -> dict:
    """"remind me about X on [day]" → one `reminder` event.

    `origin` exists in the signature ONLY so a wrong caller fails loud here
    instead of at the gate — anything but `user_explicit` raises.

    `personal` default (M choice ④, hardened SPEC PGUARD1 D3): True when the
    reminder references no BUSINESS entity — no `ref` (a commitment/event
    pointer) and no `primary_thread_id`. A `person_ids` reference alone does
    NOT flip it to work: "remind me to call Mom" / "dinner with [spouse]"
    stays personal even when the person is tracked in entities.json.
    (Pre-PGUARD1 the default treated any tracked-person reference as
    business, silently declassifying exactly the most sensitive personal
    reminders. When BAL1's per-person `tie` field lands, a person whose tie
    resolves NON-personal can re-tighten this; until then person refs are
    neutral — degrade-gracefully rule.) Pass `personal` explicitly to
    override either way.
    """
    if origin != REMINDER_ORIGIN:
        raise ReminderError(
            f"reminders are user-minted only: origin must be "
            f"'{REMINDER_ORIGIN}', got {origin!r} — no skill, sweep, or "
            "scheduled task may create a reminder"
        )
    if not summary or not str(summary).strip():
        raise ReminderError("reminder needs a non-empty summary")
    remind_from_d = _as_date(remind_from, "remind_from")
    if personal is None:
        personal = not (ref or primary_thread_id)
    data: dict = {
        "id": new_reminder_id(),
        "summary": str(summary).strip(),
        "remind_from": remind_from_d.isoformat(),
        "personal": bool(personal),
        "origin": REMINDER_ORIGIN,
    }
    if due is not None:
        data["due"] = _as_date(due, "due").isoformat()
    if ref:
        data["ref"] = str(ref)
    rule = validate_repeat(repeat)
    if rule is not None:
        data["repeat"] = rule
    return {
        "type": "reminder",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "person_ids": list(person_ids or []),
        "data": data,
    }


def build_reminder_updated_event(
    reminder_id: str,
    *,
    action: str = "push",
    remind_from=None,
    due=None,
    summary: Optional[str] = None,
    origin: str = REMINDER_ORIGIN,
    source_skill: str = "show-my-reminders",
) -> dict:
    """"push it to Friday" / "keep" / a wording fix → one `reminder_updated`.

    `push` REQUIRES `remind_from` (the new pin date; also re-arms a cleared
    one-shot). `keep` is an acknowledged touch — resets the escalation clock,
    changes nothing else. `edit` revises summary/due only.
    """
    if origin != REMINDER_ORIGIN:
        raise ReminderError(
            f"reminder_updated is user-minted only: origin must be "
            f"'{REMINDER_ORIGIN}', got {origin!r}"
        )
    if not reminder_id or not str(reminder_id).strip():
        raise ReminderError("reminder_updated needs the reminder's id")
    if action not in _ACTIONS:
        raise ReminderError(f"action must be one of {_ACTIONS}, got {action!r}")
    if action == "push" and remind_from is None:
        raise ReminderError("push needs remind_from (the new date)")
    data: dict = {
        "reminder_id": str(reminder_id),
        "action": action,
        "origin": REMINDER_ORIGIN,
    }
    if remind_from is not None:
        data["remind_from"] = _as_date(remind_from, "remind_from").isoformat()
    if due is not None:
        data["due"] = _as_date(due, "due").isoformat()
    if summary is not None and str(summary).strip():
        data["summary"] = str(summary).strip()
    return {"type": "reminder_updated", "source_skill": source_skill, "data": data}


def build_reminder_cleared_event(
    reminder_id: str,
    *,
    occurrence=None,
    note: str = "",
    origin: str = REMINDER_ORIGIN,
    source_skill: str = "show-my-reminders",
) -> dict:
    """"done with the reminder" → one `reminder_cleared`.

    For a repeating reminder the reader re-arms to the next occurrence after
    `occurrence` (defaults read-side to the occurrence active at clear time).
    NEVER touches a referenced commitment — if the underlying item is also
    done, that closes through commitment_state.close_commitment separately.
    """
    if origin != REMINDER_ORIGIN:
        raise ReminderError(
            f"reminder_cleared is user-minted only: origin must be "
            f"'{REMINDER_ORIGIN}', got {origin!r}"
        )
    if not reminder_id or not str(reminder_id).strip():
        raise ReminderError("reminder_cleared needs the reminder's id")
    data: dict = {"reminder_id": str(reminder_id), "origin": REMINDER_ORIGIN}
    if occurrence is not None:
        data["occurrence"] = _as_date(occurrence, "occurrence").isoformat()
    if note:
        data["note"] = str(note)
    return {"type": "reminder_cleared", "source_skill": source_skill, "data": data}


def capture_reminder(events_path, summary: str, **kwargs) -> dict:
    """Build + append in one call (the writer helper the capture phrases
    route through). Returns the built event — data.id is the handle later
    clear/push phrases reference. Append goes through event_gate.append_event
    (locked write, auto seq/ts, origin re-checked at the gate)."""
    ev = build_reminder_event(summary, **kwargs)
    append_event(events_path, ev, holder="reminders.capture_reminder")
    return ev


# -----------------------------------------------------------------------------
# Pure reader — derive state from events at read time (no scheduler, ever).
# -----------------------------------------------------------------------------


def _event_date(ev: dict) -> Optional[_dt.date]:
    for key in ("ts", "timestamp", "date"):
        v = ev.get(key)
        if v:
            try:
                return _dt.date.fromisoformat(str(v)[:10])
            except ValueError:
                continue
    return None


def active_reminders(
    events: Iterable[dict],
    today,
    *,
    surface: str = "client_facing",
) -> List[dict]:
    """Fold reminder events into the currently-active set (pure, read-time).

    `events` is any iterable of event dicts in append order (pass
    `events_io.iter_events(workspace_root)` output, or use
    `load_active_reminders`). `today` anchors pinned/upcoming/escalation.

    `surface` — `"m_facing"` (brief, show-my-reminders) includes
    `personal: true` rows; ANY other value excludes them. The default is the
    safe direction: a caller that never thought about privacy gets no
    personal reminders.

    Returns active reminders (cleared one-shots excluded; cleared repeats
    re-armed to their next occurrence), each:

        {id, summary, due, personal, ref, repeat,
         remind_from,            # the EFFECTIVE current occurrence (ISO date)
         status,                 # pinned | upcoming | scheduled
         days_pinned,            # 0 when not pinned
         escalation,             # none | bold | top   (M choice ②)
         last_touch}             # ISO date of last keep/push/edit, or None

    Sort order is render order: escalation-top first, then pinned (oldest pin
    first), then upcoming/scheduled by occurrence.

    Defense in depth: any reminder-family event whose data.origin is not
    `user_explicit` is IGNORED here — even a gate bypass (CR_EVENT_GATE=0)
    never renders a machine-minted reminder.
    """
    today_d = _as_date(today, "today")
    state: Dict[str, dict] = {}

    for ev in events:
        etype = ev.get("type")
        if etype not in REMINDER_TYPES:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("origin") != REMINDER_ORIGIN:
            continue  # machine-minted — never renders (hard rule, read side)

        if etype == "reminder":
            rid = data.get("id")
            if not rid:
                continue
            try:
                occ = _as_date(data.get("remind_from"), "remind_from")
            except ReminderError:
                continue
            personal = data.get("personal")
            if personal is None:
                # Read-side default mirrors the builder (PGUARD1 D3): only a
                # BUSINESS reference (ref / thread) makes it work — a person
                # reference alone stays personal, so a legacy flag-less
                # "call Mom" row is re-classified safely at read time too.
                personal = not (
                    data.get("ref")
                    or ev.get("primary_thread_id")
                )
            try:
                repeat = validate_repeat(data.get("repeat"))
            except ReminderError:
                repeat = None  # tolerate malformed history; treat as one-shot
            state[rid] = {
                "id": rid,
                "summary": data.get("summary", ""),
                "due": data.get("due"),
                "personal": bool(personal),
                "ref": data.get("ref"),
                "repeat": repeat,
                "occurrence": occ,
                "cleared": False,
                "last_touch": None,
            }
            continue

        rid = data.get("reminder_id")
        rec = state.get(rid)
        if rec is None:
            continue  # dangling mutation — tolerate on read

        if etype == "reminder_updated":
            if data.get("remind_from"):
                try:
                    rec["occurrence"] = _as_date(data["remind_from"], "remind_from")
                    rec["cleared"] = False  # push re-arms a cleared one-shot
                except ReminderError:
                    pass
            if data.get("due"):
                rec["due"] = data["due"]
            if data.get("summary"):
                rec["summary"] = data["summary"]
            touch = _event_date(ev)
            if touch is not None:
                rec["last_touch"] = touch
        else:  # reminder_cleared
            if rec["repeat"] is not None:
                base = rec["occurrence"]
                if data.get("occurrence"):
                    try:
                        base = _as_date(data["occurrence"], "occurrence")
                    except ReminderError:
                        pass
                nxt = next_occurrence(base, rec["repeat"])
                # A late clear serves every occurrence up to the clear date:
                # advance past it so a daily reminder cleared 5 days into its
                # pin re-arms for TOMORROW, not instantly with a stale
                # escalation clock.
                clear_date = _event_date(ev)
                while clear_date is not None and nxt <= clear_date:
                    nxt = next_occurrence(nxt, rec["repeat"])
                rec["occurrence"] = nxt
                rec["last_touch"] = None  # new cycle, fresh escalation clock
            else:
                rec["cleared"] = True

    out: List[dict] = []
    for rec in state.values():
        if rec["cleared"]:
            continue
        if rec["personal"] and surface != "m_facing":
            continue
        occ: _dt.date = rec["occurrence"]
        if occ <= today_d:
            status = "pinned"
            days_pinned = (today_d - occ).days
            anchor = occ
            lt = rec["last_touch"]
            if lt is not None and lt > anchor:
                anchor = lt
            ignored_days = (today_d - anchor).days
            if ignored_days >= ESCALATE_TOP_DAYS:
                escalation = "top"
            elif ignored_days >= ESCALATE_BOLD_DAYS:
                escalation = "bold"
            else:
                escalation = "none"
        else:
            days_pinned = 0
            escalation = "none"
            if (occ - today_d).days <= UPCOMING_WINDOW_DAYS:
                status = "upcoming"
            else:
                status = "scheduled"
        out.append(
            {
                "id": rec["id"],
                "summary": rec["summary"],
                "due": rec["due"],
                "personal": rec["personal"],
                "ref": rec["ref"],
                "repeat": rec["repeat"],
                "remind_from": occ.isoformat(),
                "status": status,
                "days_pinned": days_pinned,
                "escalation": escalation,
                "last_touch": rec["last_touch"].isoformat()
                if rec["last_touch"]
                else None,
            }
        )

    _status_rank = {"pinned": 0, "upcoming": 1, "scheduled": 2}
    out.sort(
        key=lambda r: (
            0 if r["escalation"] == "top" else 1,
            _status_rank[r["status"]],
            -r["days_pinned"],
            r["remind_from"],
            r["id"],
        )
    )
    return out


def load_active_reminders(
    workspace_root: Union[str, Path],
    today,
    *,
    surface: str = "client_facing",
) -> List[dict]:
    """Convenience wrapper: shard-aware read via events_io.iter_events, then
    the pure fold above. Same surface semantics (default excludes personal)."""
    try:
        from events_io import iter_events
    except ImportError:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from events_io import iter_events

    return active_reminders(iter_events(workspace_root), today, surface=surface)


__all__ = [
    "REMINDER_TYPES",
    "REMINDER_ORIGIN",
    "ESCALATE_BOLD_DAYS",
    "ESCALATE_TOP_DAYS",
    "UPCOMING_WINDOW_DAYS",
    "ReminderError",
    "validate_repeat",
    "next_occurrence",
    "build_reminder_event",
    "build_reminder_updated_event",
    "build_reminder_cleared_event",
    "capture_reminder",
    "active_reminders",
    "load_active_reminders",
]
