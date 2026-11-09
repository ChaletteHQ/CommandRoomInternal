#!/usr/bin/env python3
"""
Loop 6 — chase-policy learning from email outcomes (Phase 6, Round 2).

`email_outcome` events (reply / no_reply_7d / bounced + `latency_days`, written
silently by reconcile-sent's outcome watch) are computed and REPORTED by
insight-generator Pass 7b — then never used. The commitments orchestrator chases
on a fixed 7-day cadence and email-writer drafts follow-ups blind to what
historically gets answered. This module extends Pass 7b from report-only to
propose-and-apply: derive per-relationship-type chase windows and escalation
timing, and (on approval) write them to `_hq/data/chase-policy.json`, which the
commitments orchestrator reads when bucketing and email-writer reads for
follow-up drafts.

Small-n floors are Pass 7b's, verbatim:
  - **≥8 terminal outcomes** in the window before the pass proposes anything.
  - **≥3 outcomes in a group** before that relationship_type gets a rule.

Grouping needs a recipient→relationship_type resolver (recipient email → person
→ org.relationship_type); the pass supplies it, so the derivation stays pure and
testable. stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    from events_io import iter_events
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events_io import iter_events  # type: ignore
    from event_time import event_time  # type: ignore

MIN_TOTAL = 8            # ≥8 terminal outcomes to surface at all (Pass 7b floor)
MIN_GROUP = 3            # ≥3 in a group to name it (Pass 7b floor)
DEFAULT_CHASE_DAYS = 7   # the fixed cadence the loop replaces
CAP = 3
PASS_NAME = "loop6_chase_policy"

_TERMINAL = {"replied", "no_reply_7d", "bounced"}


def load_email_outcomes(workspace_root, *, since_iso: Optional[str] = None) -> List[dict]:
    """Terminal email_outcome rows in the window: {recipient, outcome,
    latency_days, ts}. Never raises."""
    out: List[dict] = []
    try:
        events = iter_events(Path(workspace_root) / "_hq" / "data", since_ts=since_iso)
    except Exception:
        return out
    for ev in events:
        if ev.get("type") != "email_outcome":
            continue
        data = ev.get("data") or {}
        if data.get("outcome") not in _TERMINAL:
            continue
        ts = event_time(ev)
        if since_iso and ts and str(ts) < str(since_iso):
            continue
        out.append({"recipient": data.get("recipient"),
                    "outcome": data.get("outcome"),
                    "latency_days": data.get("latency_days"),
                    "ts": ts})
    return out


def group_outcomes(rows: List[dict], relationship_of: Callable[[str], str]) -> Dict[str, dict]:
    """Group terminal outcomes by relationship_type. `relationship_of(recipient)`
    resolves an email to a relationship_type (or 'other'). Returns
    {rtype: {replied, no_reply, bounced, total, latencies:[...]}}. Pure."""
    groups: Dict[str, dict] = {}
    for r in rows:
        rtype = relationship_of(r.get("recipient") or "") or "other"
        g = groups.setdefault(rtype, {"replied": 0, "no_reply": 0, "bounced": 0,
                                      "total": 0, "latencies": []})
        oc = r.get("outcome")
        g["total"] += 1
        if oc == "replied":
            g["replied"] += 1
            lat = r.get("latency_days")
            if isinstance(lat, (int, float)):
                g["latencies"].append(float(lat))
        elif oc == "no_reply_7d":
            g["no_reply"] += 1
        elif oc == "bounced":
            g["bounced"] += 1
    return groups


def _chase_fingerprint(rtype: str, chase_days: int) -> str:
    raw = f"{(rtype or '').lower()}\x00{chase_days}"
    return "chp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def derive_chase_policy(
    groups: Dict[str, dict],
    *,
    existing_policy: Optional[dict] = None,
    cooldown_fingerprints: Optional[set] = None,
    cap: int = CAP,
    min_total: int = MIN_TOTAL,
    min_group: int = MIN_GROUP,
) -> List[dict]:
    """Propose per-relationship-type chase windows. Pure. Returns up to `cap`
    proposals:
      {fingerprint, relationship_type, chase_after_days,
       escalate_after_silent_chases, reply_rate, median_latency_days,
       no_reply_rate, n, plain}
    A group that goes quiet a lot (high no-reply rate) is chased EARLIER than the
    7-day default; a fast-replying group keeps a tight window; escalation to a
    phone-call suggestion kicks in after 2 silent chases for high-no-reply groups.
    Groups below the floors, or matching the current default, or in cooldown, are
    skipped."""
    total_terminal = sum(g["total"] for g in groups.values())
    if total_terminal < min_total:
        return []
    existing_groups = (existing_policy or {}).get("groups", {}) if existing_policy else {}
    cooling = cooldown_fingerprints or set()

    out: List[dict] = []
    for rtype, g in sorted(groups.items(), key=lambda kv: -kv[1]["total"]):
        if g["total"] < min_group:
            continue
        reply_rate = g["replied"] / g["total"]
        no_reply_rate = g["no_reply"] / g["total"]
        median_latency = (round(statistics.median(g["latencies"]), 1)
                          if g["latencies"] else None)

        # Derive the window: quiet groups get chased earlier; a fast median reply
        # tightens the window toward that median.
        if no_reply_rate >= 0.40:
            chase_days, escalate = 3, 2
        elif median_latency is not None and median_latency <= 2:
            chase_days, escalate = max(2, int(median_latency) + 1), 3
        else:
            chase_days, escalate = DEFAULT_CHASE_DAYS, 3

        # Only propose a MATERIAL change from the current effective policy.
        cur = existing_groups.get(rtype, {})
        cur_days = cur.get("chase_after_days", DEFAULT_CHASE_DAYS)
        if chase_days == cur_days:
            continue
        fp = _chase_fingerprint(rtype, chase_days)
        if fp in cooling:
            continue
        out.append({
            "fingerprint": fp, "relationship_type": rtype,
            "chase_after_days": chase_days,
            "escalate_after_silent_chases": escalate,
            "reply_rate": round(reply_rate, 2), "no_reply_rate": round(no_reply_rate, 2),
            "median_latency_days": median_latency, "n": g["total"],
            "plain": _plain(rtype, no_reply_rate, chase_days, escalate),
        })
        if len(out) >= cap:
            break
    return out


def _plain(rtype: str, no_reply_rate: float, chase_days: int, escalate: int) -> str:
    if no_reply_rate >= 0.40:
        return (f"Your {rtype} threads go quiet about {round(no_reply_rate * 100)}% "
                f"of the time — chase at day {chase_days} instead of {DEFAULT_CHASE_DAYS}, "
                f"and suggest a call after {escalate} silent chases?")
    return (f"Chase your {rtype} threads at day {chase_days} instead of "
            f"{DEFAULT_CHASE_DAYS}?")


# ---------------------------------------------------------------------------
# Store — _hq/data/chase-policy.json (read by commitments orchestrator + email-writer)
# ---------------------------------------------------------------------------

def _store_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "chase-policy.json"


def load_chase_policy(workspace_root) -> dict:
    """The learned chase policy, or an empty {version, groups:{}}. Never raises."""
    path = _store_path(workspace_root)
    if not path.exists():
        return {"version": 1, "groups": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "groups": {}}
    if not isinstance(data, dict) or not isinstance(data.get("groups"), dict):
        return {"version": 1, "groups": {}}
    data.setdefault("version", 1)
    return data


def group_from_proposal(proposal: dict) -> dict:
    """The stored group config for an APPROVED proposal."""
    return {
        "chase_after_days": proposal["chase_after_days"],
        "escalate_after_silent_chases": proposal["escalate_after_silent_chases"],
        "reply_rate": proposal.get("reply_rate"),
        "no_reply_rate": proposal.get("no_reply_rate"),
        "median_latency_days": proposal.get("median_latency_days"),
        "fingerprint": proposal["fingerprint"],
    }


def write_chase_policy(workspace_root, data: dict) -> Optional[Path]:
    """Atomically persist the chase policy. Never touches the plugin directory."""
    try:
        from atomic_write import atomic_write_json
    except Exception:  # pragma: no cover
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from atomic_write import atomic_write_json  # type: ignore
    path = _store_path(workspace_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, data)
        return path
    except Exception:
        return None


def get_chase_window(policy: dict, relationship_type: str,
                     default_days: int = DEFAULT_CHASE_DAYS) -> tuple:
    """(chase_after_days, escalate_after_silent_chases) for a relationship_type,
    falling back to the default when unlearned. THE read the commitments
    orchestrator + email-writer call. Pure; never raises."""
    groups = (policy or {}).get("groups", {}) if isinstance(policy, dict) else {}
    g = groups.get(relationship_type) or {}
    return (g.get("chase_after_days", default_days),
            g.get("escalate_after_silent_chases", 3))


# ---------------------------------------------------------------------------
# The chase on one late promise (cr1#219): who, when, and the words
# ---------------------------------------------------------------------------
#
# A chase is one press on the Hub's Overdue tab, never drafted unasked (owner
# ruling on cr1#219, 2026-11-07). This module decides what the press would
# draft and whether to offer it; the page makes the draft through the seat's
# mail connector, and nothing here writes or sends anything.
#
# It goes to the row's OWNER, not its counterparty: on a row the user is owed,
# the user IS the counterparty, and a delegated row chases the delegate. Every
# word comes from the row's own fields, never from a message body.

#: Two reply gaps are an anecdote: the floor under a person's own reading.
MIN_OUTCOMES_FOR_MEDIAN = 3
#: How far back that reading looks.
OUTCOME_WINDOW_DAYS = 90
#: The events that mean this person and the owner were just in touch.
TOUCH_TYPES = frozenset({"email_sent", "outreach_sent", "email_received", "email_outcome", "interaction", "meeting"})
#: An `interaction` is a touch only when something passed between them; a name
#: mentioned in a chat is not (PASSIVE_CAPTURE, `chat_context`).
TOUCH_DIRECTIONS = frozenset({"inbound", "outbound"})
#: A bigger meeting says nothing about whether the two of them spoke.
MEETING_TOUCH_MAX_PEOPLE = 6
#: Where the promise came from, in a sentence to the person (`_row_origin`'s
#: source word). "The", not "our": a delegate may not have been there. A row
#: with no real source date names the kind of source alone, because the day it
#: was captured is not the day it was said.
_SOURCE_PHRASES = {"Meeting": ("from the meeting on {d}", "from a meeting"),
                   "Email": ("from the email on {d}", "from an email"),
                   "Slack": ("from the Slack thread on {d}", "from Slack")}
_NOTES_PHRASE = ("from my notes of {d}", "from my notes")
NO_ADDRESS_LINE = "No email on file for {name}, so there is nothing to chase with."
OWN_ADDRESS_LINE = "This one points back at your own address, so it is not chased."
WINDOW_LINE = "{what} {when}. Chase again from {next}."
SILENT_LINE = "Chased {n} times with no answer. A call may do more than another email."
CHASED_WORDS = "Chased"
HEARD_WORDS = "In touch with {name}"


def _short_day(day) -> str:
    return f"{day.strftime('%b')} {day.day}"


def person_chase_days(record: Optional[dict], policy: dict, latencies: List[float],
                      relationship_type: str = "") -> int:
    """How long silence is normal for this person, in days. Pure. What the
    owner taught first (`cadence_override_days` on the person), then their own
    reply gaps (the median, from `MIN_OUTCOMES_FOR_MEDIAN` of them), then the
    learned window for their relationship type, then the default."""
    taught = (record or {}).get("cadence_override_days")
    try:
        if taught is not None and float(taught) > 0:
            return max(1, int(round(float(taught))))
    except (TypeError, ValueError):
        pass
    if len(latencies) >= MIN_OUTCOMES_FOR_MEDIAN:
        return max(1, int(round(statistics.median(latencies))))
    return max(1, int(get_chase_window(policy, relationship_type)[0] or DEFAULT_CHASE_DAYS))


def compose_chase(name: str, title: str, *, due=None, source: str = "", source_day=None) -> dict:
    """`{subject, body}` for one late promise, the row's own fields only: its
    title, its day, and where it came from (the citation). Plain sentences, no
    dashes as punctuation. `name` is the person's name; `source_day` is the
    day it was said, if the row knows one."""
    first = str(name or "").split()[0] if str(name or "").strip() else "there"
    what = " ".join(str(title or "the open item").split())
    dated, undated = _SOURCE_PHRASES.get(source, _NOTES_PHRASE)
    parts = [f"Checking in on {what}", dated.format(d=_short_day(source_day)) if source_day else undated]
    if due:
        parts.append(f"due {_short_day(due)}")
    return {"subject": f"Quick check on {what}"[:200],
            "body": "\n".join([f"Hi {first},", "", ", ".join(parts) + ".", "", "Where does this stand?", ""])}


def chase_offer(row: dict, *, person: Optional[dict], addresses: List[str], own_addresses: set,
                days: int, escalate_after: int, last_touch=None, last_heard=None, chases: Optional[List] = None,
                today=None) -> dict:
    """Whether to offer one chase, and on what. Pure. `row` is `{title, due,
    source, source_day}` for a late row owed to the owner; `person` its owner's
    record; `last_touch` the newest day they and the owner were in touch,
    `last_heard` the newest day they answered or met (the owner's own mail is a
    touch and not an answer); `chases` the days this row was chased, to this
    person. Answers `{to, name,
    subject, body}` to offer it, or `{note}` to say why not. A touch inside
    `days` holds it. A chase holds it for `days` or the default, whichever is
    longer: a quick replier's habit is no reason to nag. Only the chases since
    they last answered count towards `escalate_after`: an answer starts them
    over, and sending the chase does not."""
    import datetime as _dt

    name = str((person or {}).get("display_name") or (person or {}).get("canonical_name")
               or (person or {}).get("name") or "")
    addrs = [str(a).strip().lower() for a in addresses if str(a).strip()]
    if any(a in own_addresses for a in addrs):
        return {"note": OWN_ADDRESS_LINE}
    if not addrs:
        return {"note": NO_ADDRESS_LINE.format(name=name or "this person")}
    chased = sorted(c for c in (chases or []) if c and (last_heard is None or c > last_heard))
    if len(chased) >= max(1, int(escalate_after)):
        return {"note": SILENT_LINE.format(n=len(chased))}
    if today is not None:
        if chased and (today - chased[-1]).days < max(days, DEFAULT_CHASE_DAYS):
            return {"note": WINDOW_LINE.format(what=CHASED_WORDS, when=_short_day(chased[-1]),
                                               next=_short_day(chased[-1] + _dt.timedelta(days=max(days, DEFAULT_CHASE_DAYS))))}
        if last_touch is not None and (today - last_touch).days < days:
            word = HEARD_WORDS.format(name=name.split()[0] if name else "them")
            return {"note": WINDOW_LINE.format(what=word, when=_short_day(last_touch),
                                               next=_short_day(last_touch + _dt.timedelta(days=days)))}
    return {"to": addrs[0], "name": name,
            **compose_chase(name, row.get("title"), due=row.get("due"), source=row.get("source") or "",
                            source_day=row.get("source_day"))}


def chase_touches(events, addresses_of: Dict[str, List[str]], day_of: Callable, today=None) -> tuple:
    """`({person_id: newest day in touch}, {person_id: newest day they
    answered or met})` from the book. Pure. `day_of` turns a stamp into the
    owner's calendar day. A touch is a mail either way, an inbound or outbound
    interaction, a reply on record, or a meeting that happened with few enough
    people in it; a draft is no touch, nothing reached anyone."""
    from event_refs import attendee_emails_of, meeting_person_ids, persons_of

    by_address = {a.lower(): pid for pid, addrs in addresses_of.items() for a in addrs}
    touched: Dict[str, object] = {}
    heard: Dict[str, object] = {}
    for ev in events or []:
        kind = ev.get("type") if isinstance(ev, dict) else None
        if kind not in TOUCH_TYPES:
            continue
        day = day_of(event_time(ev))
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if not day or (today is not None and day > today):
            continue
        if kind == "interaction" and d.get("direction") not in TOUCH_DIRECTIONS:
            continue
        if kind == "email_outcome" and d.get("outcome") != "replied":
            continue
        if kind == "meeting":
            who = set(meeting_person_ids(ev, by_address))
            strangers = [e for e in attendee_emails_of(ev) if e.lower() not in by_address]
            # The same guest can sit in both lists, and they cannot be paired.
            size = len(who) + max(len(strangers), len(d.get("attendees_external") or []))
            if str(d.get("status") or "") == "scheduled" or size > MEETING_TOUCH_MAX_PEOPLE:
                continue
        else:
            who = set(persons_of(ev))
            if d.get("sender_person_id"):
                who.add(d["sender_person_id"])
            for key in ("recipient", "to", "to_address"):
                val = d.get(key)
                for one in (val if isinstance(val, (list, tuple)) else [val]):
                    if str(one or "").strip().lower() in by_address:
                        who.add(by_address[str(one).strip().lower()])
        theirs = kind in ("email_received", "email_outcome", "meeting") or d.get("direction") == "inbound"
        for pid in who:
            if pid in addresses_of:
                if pid not in touched or day > touched[pid]:
                    touched[pid] = day
                if theirs and (pid not in heard or day > heard[pid]):
                    heard[pid] = day
    return touched, heard


def row_chases(events, day_of: Callable) -> Dict[str, List[tuple]]:
    """`{commitment_id: [(day, recipient)]}`: the drafts a press made
    (`data.chase_for`, cr1#219). Pure. The recipient rides along so a chase to
    the last owner of a reassigned row is not held against the new one."""
    out: Dict[str, List[tuple]] = {}
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("type") != "email_drafted":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        day = day_of(event_time(ev)) if d.get("chase_for") else None
        if day:
            out.setdefault(str(d["chase_for"]), []).append((day, str(d.get("recipient") or "").strip().lower()))
    return out


__all__ = [
    "MIN_OUTCOMES_FOR_MEDIAN", "OUTCOME_WINDOW_DAYS", "TOUCH_TYPES", "person_chase_days", "compose_chase",
    "chase_offer", "chase_touches", "row_chases",
    "MIN_TOTAL", "MIN_GROUP", "DEFAULT_CHASE_DAYS", "CAP", "PASS_NAME",
    "load_email_outcomes", "group_outcomes", "derive_chase_policy",
    "load_chase_policy", "group_from_proposal", "write_chase_policy",
    "get_chase_window",
]
