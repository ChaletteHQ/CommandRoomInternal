#!/usr/bin/env python3
"""
Loop 1 — inbox triage feedback (Phase 6, Round 1).

The inbox is the highest-frequency surface in the product, and every action the
CEO takes on it (send / edit-then-send / draft / skip per sender) is the single
strongest triage-relevance signal the product receives — and pre-Phase-6 it was
thrown away. This module closes the loop:

  CAPTURE (apply-choices, at dispatch time)
    build_triage_feedback_event(...) — one `triage_feedback` event per inbox
    action, appended through the gate (`append_event`). Records what the
    orchestrator decided (bucket_assigned, draft_offered) and what the CEO did
    (action_taken), keyed by sender + domain.

  LEARN (insight-generator Pass 13, weekly)
    load_triage_feedback -> aggregate_sender_signals -> propose_sender_rules.
    Proposes a sender/domain priority rule ONLY when behavior consistently
    contradicts the bucket the orchestrator assigned (you skip everything from a
    surfaced sender → demote; you engage fast with an un-surfaced sender →
    promote). 3-cap, 60-day fingerprint cooldown, confirm/edit/skip — the Pass
    9/10 machinery, verbatim.

  STORE (workspace-side)
    _hq/data/sender-priority-rules.json — a generalization of
    known-billing-domains.txt. The inbox orchestrator loads it in Phase 4
    scoring, AFTER the hardcoded rules and the financial-signal override, BEFORE
    ranking/rendering. apply_rules_to_score() is that read.

All learned state lives under `_hq/data/` in the customer workspace — NEVER the
plugin directory (overwritten on every update). stdlib only; the pure scoring
helpers take no clock and do no I/O so they unit-test deterministically.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

try:
    from events_io import iter_events
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events_io import iter_events  # type: ignore
    from event_time import event_time  # type: ignore

# --- Loop-1 tuning (Pass 13) -------------------------------------------------
WINDOW_DAYS = 30
# Small-n floor: never propose a rule off fewer than this many actions on the
# same sender/domain in the window (the inbox is high-volume; 4 is stricter than
# Pass 11's 3-correction floor precisely because a wrong demotion could bury a
# real high-value sender).
MIN_ACTIONS = 4
# The behavior must be at least this consistent (fraction of actions in the
# dominant direction) before a rule is proposed.
CONSISTENCY = 0.8
CAP = 3
# The priority delta a demote/promote rule applies in Phase-4 scoring. ±30 mirrors
# the hand-coded financial-signal override (+30) it generalizes.
DEMOTE_DELTA = -30
PROMOTE_DELTA = 30

PASS_NAME = "pass13_sender_priority"

# Actions the CEO can take on an inbox item, grouped by what they signal.
ENGAGE_ACTIONS = {"send", "edit then send", "edit_then_send", "draft", "edit then draft"}
DISMISS_ACTIONS = {"skip"}


# ---------------------------------------------------------------------------
# Capture (event builder — no seq/ts; append_event stamps inside the lock)
# ---------------------------------------------------------------------------

def _domain_of(sender: str) -> str:
    s = (sender or "").strip().lower()
    if "@" in s:
        return s.rsplit("@", 1)[-1]
    return s


def build_triage_feedback_event(
    *,
    sender: str,
    bucket_assigned: str,
    action_taken: str,
    draft_offered: bool,
    domain: Optional[str] = None,
    source_skill: str = "inbox-triage",
) -> dict:
    """A `triage_feedback` event for ONE inbox action. Return shape omits seq/ts
    (auto-stamped by `append_event`). `bucket_assigned` is the orchestrator's
    Phase-5 handling label (e.g. "surfaced", "noise:marketing", "fyi");
    `action_taken` is the canonical inbox verb the CEO clicked."""
    sender = (sender or "").strip().lower()
    dom = (domain or _domain_of(sender)).strip().lower()
    return {
        "type": "triage_feedback",
        "source_skill": source_skill,
        "data": {
            "sender": sender,
            "domain": dom,
            "bucket_assigned": bucket_assigned,
            "action_taken": action_taken,
            "draft_offered": bool(draft_offered),
        },
    }


# ---------------------------------------------------------------------------
# Load + aggregate (Pass 13)
# ---------------------------------------------------------------------------

def load_triage_feedback(workspace_root, *, since_iso: Optional[str] = None) -> List[dict]:
    """triage_feedback rows in the window, each flattened to
    {sender, domain, bucket_assigned, action_taken, draft_offered, ts}. Reads
    through the shard-transparent events reader; never raises."""
    out: List[dict] = []
    try:
        events = iter_events(Path(workspace_root) / "_hq" / "data", since_ts=since_iso)
    except Exception:
        return out
    for ev in events:
        if ev.get("type") != "triage_feedback":
            continue
        ts = event_time(ev)
        if since_iso and ts and str(ts) < str(since_iso):
            continue
        data = ev.get("data") or {}
        sender = str(data.get("sender", "")).strip().lower()
        out.append({
            "sender": sender,
            "domain": str(data.get("domain") or _domain_of(sender)).strip().lower(),
            "bucket_assigned": data.get("bucket_assigned", ""),
            "action_taken": data.get("action_taken", ""),
            "draft_offered": bool(data.get("draft_offered")),
            "ts": ts,
        })
    return out


def _direction(action: str) -> Optional[str]:
    a = re.sub(r"\s+", " ", (action or "").strip().lower())
    if a in ENGAGE_ACTIONS:
        return "engage"
    if a in DISMISS_ACTIONS:
        return "dismiss"
    return None


def _was_surfaced(bucket: str) -> bool:
    """A bucket counts as 'surfaced' unless it's an explicit noise/fyi demotion.
    The orchestrator writes 'noise:*' / 'fyi' for demoted items and 'surfaced'
    (or a priority label) for items it put in the top-5."""
    b = (bucket or "").strip().lower()
    return not (b.startswith("noise") or b == "fyi" or b == "demoted")


def aggregate_sender_signals(rows: List[dict]) -> Dict[tuple, dict]:
    """Group actions by (scope_kind, scope_value) at BOTH sender and domain
    granularity. Returns {(kind, value): {engage, dismiss, surfaced, total,
    senders:set}}. Domain rows aggregate every sender on that domain, so a whole
    newsletter domain can be demoted in one rule."""
    agg: Dict[tuple, dict] = {}

    def _bump(key, row, direction):
        slot = agg.setdefault(key, {"engage": 0, "dismiss": 0, "surfaced": 0,
                                    "total": 0, "senders": set()})
        slot[direction] += 1
        slot["total"] += 1
        if _was_surfaced(row.get("bucket_assigned", "")):
            slot["surfaced"] += 1
        if row.get("sender"):
            slot["senders"].add(row["sender"])

    for row in rows:
        direction = _direction(row.get("action_taken", ""))
        if direction is None:
            continue
        sender = row.get("sender")
        domain = row.get("domain")
        if sender:
            _bump(("sender", sender), row, direction)
        if domain:
            _bump(("domain", domain), row, direction)
    return agg


def sender_rule_fingerprint(scope_kind: str, scope_value: str, action: str) -> str:
    """Stable fingerprint for cooldown tracking (Pass 9/10 pattern)."""
    raw = f"{scope_kind}\x00{(scope_value or '').lower()}\x00{action}"
    return "spr_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def propose_sender_rules(
    agg: Dict[tuple, dict],
    *,
    existing_rules: Optional[List[dict]] = None,
    cooldown_fingerprints: Optional[set] = None,
    cap: int = CAP,
    min_actions: int = MIN_ACTIONS,
    consistency: float = CONSISTENCY,
) -> List[dict]:
    """Candidate priority rules where behavior consistently contradicts the
    assigned bucket. Pure — no clock, no I/O.

    Returns up to `cap` proposals, highest-signal first:
      {fingerprint, scope_kind, scope_value, action ('demote'|'promote'),
       delta, n, dominant, total, plain}
    A demote fires when a mostly-SURFACED scope is mostly SKIPPED; a promote
    fires when a mostly-UN-surfaced scope is mostly ENGAGED. Domain rules beat
    sender rules for the same behavior (broader fix), and a scope already covered
    by an existing rule or in cooldown is skipped."""
    existing = existing_rules or []
    cooling = cooldown_fingerprints or set()
    existing_scopes = {
        (r.get("match", {}).get("kind"), (r.get("match", {}).get("value") or "").lower())
        for r in existing
    }

    candidates: List[dict] = []
    for (kind, value), c in agg.items():
        total = c["total"]
        if total < min_actions:
            continue
        engage, dismiss, surfaced = c["engage"], c["dismiss"], c["surfaced"]
        # Demote: surfaced but skipped.
        if dismiss / total >= consistency and surfaced >= min_actions * 0.5:
            action, delta, dominant = "demote", DEMOTE_DELTA, dismiss
            plain = (f"You've skipped {dismiss} of the last {total} messages from "
                     f"{value} — stop surfacing them?")
        # Promote: not surfaced but engaged.
        elif engage / total >= consistency and (total - surfaced) >= min_actions * 0.5:
            action, delta, dominant = "promote", PROMOTE_DELTA, engage
            plain = (f"You act on almost everything from {value} "
                     f"({engage} of the last {total}) — always surface it near the top?")
        else:
            continue

        if (kind, (value or "").lower()) in existing_scopes:
            continue
        fp = sender_rule_fingerprint(kind, value, action)
        if fp in cooling:
            continue
        candidates.append({
            "fingerprint": fp,
            "scope_kind": kind,
            "scope_value": value,
            "action": action,
            "delta": delta,
            "n": dominant,
            "dominant": dominant,
            "total": total,
            "num_senders": len(c["senders"]),
            "plain": plain,
        })

    # Rank: strongest dominance first, domain rules before sender rules on a tie
    # (a domain rule fixes more with one proposal), then higher volume.
    candidates.sort(key=lambda x: (
        -(x["dominant"] / max(1, x["total"])),
        0 if x["scope_kind"] == "domain" else 1,
        -x["total"],
    ))
    # De-dup: if a domain rule and one of its member-sender rules both fire the
    # same action, keep only the domain rule.
    kept: List[dict] = []
    claimed_domains = {c["scope_value"] for c in candidates
                       if c["scope_kind"] == "domain"}
    for c in candidates:
        if c["scope_kind"] == "sender" and _domain_of(c["scope_value"]) in claimed_domains:
            continue
        kept.append(c)
        if len(kept) >= cap:
            break
    return kept


# ---------------------------------------------------------------------------
# Store — _hq/data/sender-priority-rules.json (read by orchestrator-inbox)
# ---------------------------------------------------------------------------

def _store_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "sender-priority-rules.json"


def load_sender_priority_rules(workspace_root) -> dict:
    """The learned rule store, or an empty {version, rules:[]} when absent.
    Treat-as-empty-if-missing — same posture as known-billing-domains.txt.
    Never raises."""
    path = _store_path(workspace_root)
    if not path.exists():
        return {"version": 1, "rules": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "rules": []}
    if not isinstance(data, dict):
        return {"version": 1, "rules": []}
    data.setdefault("version", 1)
    if not isinstance(data.get("rules"), list):
        data["rules"] = []
    return data


def rule_from_proposal(proposal: dict, *, added_ts: str, note: str = "") -> dict:
    """Turn an APPROVED proposal into a store rule. `added_ts` is stamped by the
    caller (the pass has a clock; this helper stays pure)."""
    return {
        "match": {"kind": proposal["scope_kind"], "value": proposal["scope_value"]},
        "action": proposal["action"],
        "delta": proposal["delta"],
        "reason": note or proposal.get("plain", ""),
        "fingerprint": proposal["fingerprint"],
        "added": added_ts,
    }


def write_sender_priority_rules(workspace_root, data: dict) -> Optional[Path]:
    """Atomically persist the rule store. Never touches the plugin directory."""
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


def apply_rules_to_score(
    base_score: float,
    *,
    sender: str,
    domain: Optional[str] = None,
    rules: Optional[List[dict]] = None,
) -> float:
    """Apply learned sender-priority rules to a base Phase-4 priority score.
    Called by the inbox orchestrator AFTER the hardcoded rules + financial-signal
    override and BEFORE ranking. Sender-scoped rules match on the full address;
    domain-scoped rules match on the right-of-@ domain. Deltas from every
    matching rule sum. Pure; never raises."""
    if not rules:
        return base_score
    sender = (sender or "").strip().lower()
    dom = (domain or _domain_of(sender)).strip().lower()
    score = base_score
    for r in rules:
        m = r.get("match") or {}
        kind, value = m.get("kind"), (m.get("value") or "").strip().lower()
        if not value:
            continue
        if kind == "sender" and value == sender:
            score += r.get("delta", 0)
        elif kind == "domain" and value == dom:
            score += r.get("delta", 0)
    return score


__all__ = [
    "WINDOW_DAYS", "MIN_ACTIONS", "CONSISTENCY", "CAP", "PASS_NAME",
    "DEMOTE_DELTA", "PROMOTE_DELTA",
    "build_triage_feedback_event", "load_triage_feedback",
    "aggregate_sender_signals", "sender_rule_fingerprint", "propose_sender_rules",
    "load_sender_priority_rules", "rule_from_proposal",
    "write_sender_priority_rules", "apply_rules_to_score",
]
