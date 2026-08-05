#!/usr/bin/env python3
"""
Loop 2 — dismissal-pattern learning / surface tuning (Phase 6, Round 1).

`chat_dismissal` (24h TTL) and `dont_forget_feedback` (14d TTL) are RE-SURFACING
timers, not preferences: skip the same chase for the same person every day and it
returns every day, forever. The user's most-repeated "no" teaches nothing. This
module turns those repeats into a durable, workspace-side suppression:

  MINE (insight-generator Pass 14, weekly)
    load_dismissals reads BOTH event families across all 8 widget surfaces and
    normalizes each to a stable fingerprint (surface + item_class + entity_id).
    count_repeats finds fingerprints dismissed 3+ times in 30 days;
    propose_suppressions turns those into plain-English suppression proposals.
    3-cap, 60-day cooldown, confirm/edit/skip — Pass 9/10 machinery, verbatim.

  STORE (workspace-side)
    _hq/data/surface-preferences.json — per-person / per-project / per-class
    suppression rules. EVERY widget orchestrator (inbox, commitments, staff-meeting,
    past-meetings, upcoming-meetings, friday-wrap, relationship-moves,
    morning-brief) calls is_suppressed() to filter items BEFORE rendering.

The 8 surfaces and their item-classes are not hard-coded here — a surface passes
whatever (surface, item_class, entity_id) triple it renders and asks whether it's
suppressed. `dismissal_fingerprint` is the ONE canonical key so a fingerprint
written by a dismissal writer and one checked by an orchestrator always agree.

Legacy `chat_dismissal` / `dont_forget_feedback` events that predate the
Phase-6 `data.fingerprint`/`data.surface`/`data.item_class` fields are handled by
deriving the triple best-effort from `source_skill` + `data.target_id`
(readers-handle-both-shapes-forever, per migration doctrine §3.1). All learned
state lives under `_hq/data/` — NEVER the plugin directory. stdlib only; pure
helpers take no clock and do no I/O.
"""
from __future__ import annotations

import hashlib
import json
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

MIN_COUNT = 3          # "dismissed 3+ times in 30 days" (Phase 6 spec)
WINDOW_DAYS = 30
CAP = 3
PASS_NAME = "pass14_surface_preferences"

# Which source_skill a legacy chat_dismissal came from → its surface. Used only
# to derive a fingerprint for events written before the Phase-6 payload fields.
_SKILL_TO_SURFACE = {
    "inbox-triage": "inbox",
    "inbox": "inbox",
    "cr-inbox": "inbox",
    "commitments": "commitments",
    "cr-commitments": "commitments",
    # FOSSIL rows (LIFECYCLE1 retired the Pulse chat). Kept forever: a stored
    # preference the CEO taught the system through that surface must keep
    # matching, and a suppression that silently stops applying is a surface
    # re-nagging about something already dismissed.
    "pulse": "pulse",
    "dont-forget": "pulse",
    "cr-dont-forget": "pulse",
    "past-meetings": "past-meetings",
    "cr-past-meetings": "past-meetings",
    "upcoming-meetings": "upcoming-meetings",
    "cr-upcoming-meetings": "upcoming-meetings",
    "friday-wrap": "friday-wrap",
    "relationship-moves": "relationship-moves",
    "morning-brief": "morning-brief",
}


def dismissal_fingerprint(surface: str, item_class: str, entity_id: Optional[str]) -> str:
    """THE canonical suppression key. A dismissal writer stamps this into
    `data.fingerprint`; an orchestrator recomputes the same value to check
    suppression. entity_id may be None for surface-wide classes."""
    eid = str(entity_id).lower() if entity_id is not None else ""
    raw = f"{(surface or '').lower()}\x00{(item_class or '').lower()}\x00{eid}"
    return "sfp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Normalize dismissals from BOTH event families
# ---------------------------------------------------------------------------

def normalize_dismissal(ev: dict) -> Optional[dict]:
    """Flatten one chat_dismissal OR dont_forget_feedback event to
    {surface, item_class, entity_id, fingerprint, ts, source_type}. Prefers the
    Phase-6 explicit fields; derives them for legacy events. Returns None for a
    non-dismissal event or one carrying no usable identity."""
    etype = ev.get("type")
    if etype not in ("chat_dismissal", "dont_forget_feedback"):
        return None
    data = ev.get("data") or {}
    ts = event_time(ev)

    # dont_forget_feedback: a Pulse "expected" / "just busy" reply on a person.
    # FOSSIL reader (LIFECYCLE1) — nothing writes this event any more, and the
    # "pulse" default below is CORRECT precisely because every event of this
    # type on disk came from that surface. Never re-point it at a live one.
    if etype == "dont_forget_feedback":
        entity_id = data.get("person_id") or ev.get("primary_thread_id")
        surface = data.get("surface") or "pulse"
        item_class = data.get("item_class") or "dormancy"
    else:  # chat_dismissal
        surface = data.get("surface") or _SKILL_TO_SURFACE.get(
            (ev.get("source_skill") or "").lower(), "")
        item_class = data.get("item_class") or "item"
        entity_id = data.get("entity_id") or data.get("person_id") or data.get("project_id")
        # A numeric `target_id` is a per-render event seq, NOT a stable entity —
        # only treat a target_id as an entity when it looks like an entity id.
        if entity_id is None:
            tgt = data.get("target_id")
            if isinstance(tgt, str) and tgt.split("_", 1)[0] in (
                    "person", "project", "org", "thread"):
                entity_id = tgt

    if not entity_id and not (data.get("surface") and data.get("item_class")):
        # A bare legacy dismissal keyed only to a per-day event seq is not a
        # stable pattern — skip it rather than invent a fingerprint that can
        # never recur.
        if not data.get("fingerprint"):
            return None

    fp = data.get("fingerprint") or dismissal_fingerprint(surface, item_class, entity_id)
    return {
        "surface": surface,
        "item_class": item_class,
        "entity_id": entity_id,
        "fingerprint": fp,
        "ts": ts,
        "source_type": etype,
    }


def load_dismissals(workspace_root, *, since_iso: Optional[str] = None) -> List[dict]:
    """All normalized dismissals (both families) in the window. Never raises."""
    out: List[dict] = []
    try:
        events = iter_events(Path(workspace_root) / "_hq" / "data", since_ts=since_iso)
    except Exception:
        return out
    for ev in events:
        if ev.get("type") not in ("chat_dismissal", "dont_forget_feedback"):
            continue
        norm = normalize_dismissal(ev)
        if norm is None:
            continue
        if since_iso and norm["ts"] and str(norm["ts"]) < str(since_iso):
            continue
        out.append(norm)
    return out


def count_repeats(rows: List[dict], *, min_count: int = MIN_COUNT) -> Dict[str, dict]:
    """Fingerprints dismissed at least `min_count` times. Returns
    {fingerprint: {count, latest_ts, surface, item_class, entity_id}}. Pure."""
    agg: Dict[str, dict] = {}
    for r in rows:
        fp = r.get("fingerprint")
        if not fp:
            continue
        slot = agg.setdefault(fp, {
            "count": 0, "latest_ts": None,
            "surface": r.get("surface"), "item_class": r.get("item_class"),
            "entity_id": r.get("entity_id"),
        })
        slot["count"] += 1
        ts = r.get("ts")
        if ts and (slot["latest_ts"] is None or str(ts) > str(slot["latest_ts"])):
            slot["latest_ts"] = ts
    return {fp: v for fp, v in agg.items() if v["count"] >= min_count}


def propose_suppressions(
    counts: Dict[str, dict],
    *,
    entity_names: Optional[Dict[str, str]] = None,
    existing_prefs: Optional[List[dict]] = None,
    cooldown_fingerprints: Optional[set] = None,
    cap: int = CAP,
) -> List[dict]:
    """Turn repeat-dismissal counts into suppression proposals, most-repeated
    first. Pure — no clock, no I/O.

    Returns up to `cap` proposals:
      {fingerprint, surface, item_class, entity_id, count, plain}
    Fingerprints already suppressed or in cooldown are dropped. `entity_names`
    maps entity_id → display name for the plain-English proposal text (the pass
    resolves names; this helper stays pure)."""
    names = entity_names or {}
    existing_fps = {p.get("fingerprint") for p in (existing_prefs or [])}
    cooling = cooldown_fingerprints or set()

    out: List[dict] = []
    for fp, c in sorted(counts.items(), key=lambda kv: -kv[1]["count"]):
        if fp in existing_fps or fp in cooling:
            continue
        name = names.get(c.get("entity_id") or "", c.get("entity_id") or "this")
        out.append({
            "fingerprint": fp,
            "surface": c.get("surface"),
            "item_class": c.get("item_class"),
            "entity_id": c.get("entity_id"),
            "count": c["count"],
            "plain": _plain(c["item_class"], name, c["count"], c.get("surface")),
        })
        if len(out) >= cap:
            break
    return out


def _plain(item_class: str, name: str, count: int, surface: Optional[str]) -> str:
    ic = (item_class or "").lower()
    if ic in ("chase", "commitment", "commitment_chase"):
        return f"You've skipped chasing {name} {count} times — never suggest chasing them?"
    if ic in ("stale_project", "stale", "stale-project"):
        return f"You've dismissed “{name} is going stale” {count} times — stop flagging it?"
    if ic in ("dormancy", "pattern_break", "pattern-break"):
        return f"You've cleared {name}'s “going quiet” flag {count} times — stop surfacing it?"
    return f"You've dismissed this {ic or 'item'} about {name} {count} times — stop surfacing it?"


# ---------------------------------------------------------------------------
# Store — _hq/data/surface-preferences.json (read by every orchestrator)
# ---------------------------------------------------------------------------

def _store_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "surface-preferences.json"


def load_surface_preferences(workspace_root) -> dict:
    """The learned suppression store, or an empty {version, suppressions:[]}.
    Treat-as-empty-if-missing. Never raises."""
    path = _store_path(workspace_root)
    if not path.exists():
        return {"version": 1, "suppressions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "suppressions": []}
    if not isinstance(data, dict):
        return {"version": 1, "suppressions": []}
    data.setdefault("version", 1)
    if not isinstance(data.get("suppressions"), list):
        data["suppressions"] = []
    return data


def suppression_from_proposal(proposal: dict, *, added_ts: str, note: str = "") -> dict:
    """Turn an APPROVED proposal into a store suppression. `added_ts` stamped by
    the caller (pure helper)."""
    return {
        "surface": proposal.get("surface"),
        "item_class": proposal.get("item_class"),
        "entity_id": proposal.get("entity_id"),
        "mode": "suppress",
        "reason": note or proposal.get("plain", ""),
        "fingerprint": proposal["fingerprint"],
        "added": added_ts,
    }


def write_surface_preferences(workspace_root, data: dict) -> Optional[Path]:
    """Atomically persist the suppression store. Never touches the plugin dir."""
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


def is_suppressed(
    prefs: dict,
    surface: str,
    item_class: str,
    entity_id: Optional[str] = None,
) -> bool:
    """True if a rendered item matches a suppression. THE filter every
    orchestrator calls before rendering. A suppression matches when its surface
    matches (or is "*"), its item_class matches (or is "*"), and its entity_id
    matches (or is null → class-wide). Pure; never raises."""
    if not prefs:
        return False
    supps = prefs.get("suppressions") if isinstance(prefs, dict) else None
    if not supps:
        return False
    s, ic = (surface or "").lower(), (item_class or "").lower()
    eid = (entity_id or "").lower()
    for r in supps:
        if r.get("mode") and r.get("mode") != "suppress":
            continue
        rs = (r.get("surface") or "*").lower()
        ric = (r.get("item_class") or "*").lower()
        reid = (r.get("entity_id") or "").lower()
        if rs not in ("*", s):
            continue
        if ric not in ("*", ic):
            continue
        if reid and reid != eid:
            continue
        return True
    return False


__all__ = [
    "MIN_COUNT", "WINDOW_DAYS", "CAP", "PASS_NAME",
    "dismissal_fingerprint", "normalize_dismissal", "load_dismissals",
    "count_repeats", "propose_suppressions", "load_surface_preferences",
    "suppression_from_proposal", "write_surface_preferences", "is_suppressed",
]
