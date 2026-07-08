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


__all__ = [
    "MIN_TOTAL", "MIN_GROUP", "DEFAULT_CHASE_DAYS", "CAP", "PASS_NAME",
    "load_email_outcomes", "group_outcomes", "derive_chase_policy",
    "load_chase_policy", "group_from_proposal", "write_chase_policy",
    "get_chase_window",
]
