#!/usr/bin/env python3
"""Detect prospects that look like they've become clients — and SUGGEST the
conversion. Never flips anything (Bug #92).

WHY THIS EXISTS
Bug #91 gave us a real `[Name] is now a client` conversion command, but it's
manual — a closed prospect stays mis-registered until the CEO remembers to run
it (a real prospect can sit at `relationship_type: prospect` long after it's
the furthest-along client). Auto-flipping `relationship_type` on inferred signal
would be wrong — it's a state change with downstream effects, and a fuzzy
"sounds like they signed" would mis-classify (the same false-positive trap as
auto-closing commitments). So this is DETECT-AND-SUGGEST: it surfaces a nudge
("[Name] looks like a client now — say `[Name] is now a client`"); the CEO
confirms; the flip happens through the Bug #91 typed-writer path.

SIGNALS (per prospect org)
  structural (HIGH confidence — low false-positive):
    - an ACTIVE engagement of kind client/partner points at the prospect org
      (you've recorded a client relationship but the org is still 'prospect'); or
    - an ACTIVE (non-archived) thread/project is affiliated with the prospect org
      (you're doing the work — it's a client).
  textual (MEDIUM confidence):
    - a recent event referencing the org carries client-conversion language
      ('signed', 'engagement agreement', 'kicked off', 'now a client',
      'active client', 'statement of work', 'retainer', ...) — and is not pure
      pursuit-phase noise.

Pure / substrate-only / no connectors / no mutation. stdlib only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import event_refs  # noqa: E402

# Default look-back for textual signals.
TEXT_WINDOW_DAYS = 120

# Client-conversion language. Lowercased substring match.
CONVERSION_MARKERS = (
    "signed", "engagement agreement", "agreement signed", "contract signed",
    "kicked off", "kickoff", "kick-off", "onboarded", "onboarding kicked",
    "now a client", "became a client", "is a client", "active client",
    "closed the deal", "deal closed", "statement of work", " sow ", "retainer",
    "first invoice", "engagement is live", "engaged us", "signed the engagement",
)
# Pursuit-phase phrases — presence of these does NOT count as conversion signal
# (a "prospect" is supposed to have these). Used only to avoid counting a bare
# pursuit event as conversion; a real marker above still wins.
_PURSUIT_ONLY = ("proposal sent", "interviewing", "pitching", "sent the proposal")

_TEXT_FIELDS = ("title", "summary", "notes", "text", "description", "label", "name")


def _entities(workspace_root: Path) -> dict:
    p = workspace_root / "_hq" / "data" / "entities.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["entities"] if isinstance(d.get("entities"), dict) else d


def _event_org_ids(ev: dict) -> set[str]:
    out: set[str] = set()
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for r in (ev.get("org_ids") or []):
        out.add(r)
    for r in (d.get("org_ids") or []):
        out.add(r)
    for k in ("org_id", "primary_org_id", "to_org_id", "from_org_id"):
        for src in (ev, d):
            v = src.get(k)
            if isinstance(v, str) and v:
                out.add(v)
    return {o for o in out if isinstance(o, str) and o.startswith("org_")}


def _event_text(ev: dict) -> str:
    parts: list[str] = []
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for src in (ev, d):
        for f in _TEXT_FIELDS:
            v = src.get(f)
            if isinstance(v, str):
                parts.append(v)
    return " ".join(parts).lower()


def _has_conversion_language(text: str) -> bool:
    if not text:
        return False
    return any(m in text for m in CONVERSION_MARKERS)


def detect_prospect_conversion_candidates(workspace_root: str | Path) -> list[dict]:
    """Return prospects that look converted, each as:
        {org_id, name, confidence: 'high'|'medium', reason, suggested_command,
         render_line}
    Sorted high-confidence first. Empty list when nothing qualifies.

    `render_line` is a ready-to-render verbatim nudge line (Bug #92b). Surfaces
    MUST render every candidate's `render_line` as-is and MUST NOT re-decide
    inclusion — the detector already owns who qualifies (active/paused/etc.).
    The morning brief dropped a true HIGH candidate on its own "looks paused"
    judgment while the cleanup surface rendered it: a surface second-guessing
    the detector is the #92b regression. Emitting the line here removes the
    surface's discretion entirely.
    """
    workspace_root = Path(workspace_root)
    ent = _entities(workspace_root)
    orgs = ent.get("orgs") or []
    engagements = ent.get("engagements") or []
    threads = ent.get("threads") or ent.get("projects") or []

    prospects = {o["id"]: o for o in orgs if o.get("id") and o.get("relationship_type") == "prospect"}
    if not prospects:
        return []

    # Structural signal A — active client/partner engagement pointing at the prospect.
    eng_hit: dict[str, str] = {}
    for e in engagements:
        to = e.get("to_org_id")
        if to in prospects and e.get("is_active", True) and e.get("kind") in ("client", "partner"):
            eng_hit[to] = e.get("kind") or "client"

    # Structural signal B — active thread affiliated with the prospect.
    thread_hit: dict[str, str] = {}
    for t in threads:
        if t.get("status") == "archived":
            continue
        affs = set(t.get("affiliation_ids") or [])
        if t.get("org"):
            affs.add(t["org"])
        if t.get("org_id"):
            affs.add(t["org_id"])
        for oid in affs & set(prospects):
            thread_hit.setdefault(oid, t.get("display_name") or t.get("id") or "a project")

    # Textual signal C — recent org-referencing events with conversion language.
    text_hit: dict[str, str] = {}
    events_path = workspace_root / "_hq" / "data" / "events.jsonl"
    if events_path.exists():
        events = event_refs.load_events(events_path)
        # Build thread→org map so thread-tagged events also attribute to the org.
        thread_org = {}
        for t in threads:
            oid = t.get("org") or t.get("org_id") or (t.get("affiliation_ids") or [None])[0]
            if oid:
                thread_org[t.get("id")] = oid
        for ev in events:
            text = _event_text(ev)
            if not _has_conversion_language(text):
                continue
            refs = set(_event_org_ids(ev))
            for tid in event_refs.threads_of(ev):
                if tid in thread_org:
                    refs.add(thread_org[tid])
            for oid in refs & set(prospects):
                if oid not in text_hit:
                    snippet = next((m for m in CONVERSION_MARKERS if m in text), "")
                    text_hit[oid] = snippet.strip()

    candidates: list[dict] = []
    for oid, org in prospects.items():
        name = org.get("canonical_name") or oid
        reasons = []
        confidence = None
        if oid in eng_hit:
            confidence = "high"
            reasons.append(f"an active {eng_hit[oid]} engagement points at them while they're still marked a prospect")
        if oid in thread_hit:
            confidence = "high"
            reasons.append(f"there's an active project ({thread_hit[oid]}) for them")
        if oid in text_hit:
            confidence = confidence or "medium"
            reasons.append(f"recent activity mentions \"{text_hit[oid]}\"")
        if not confidence:
            continue
        reason = "; ".join(reasons)
        candidates.append({
            "org_id": oid,
            "name": name,
            "confidence": confidence,
            "reason": reason,
            "suggested_command": f"{name} is now a client",
            "render_line": (
                f"🔄 {name} looks like a client now ({reason}) — "
                f"say `{name} is now a client` to convert"
            ),
        })

    candidates.sort(key=lambda c: (0 if c["confidence"] == "high" else 1, c["name"]))
    return candidates


__all__ = ["detect_prospect_conversion_candidates"]


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    for c in detect_prospect_conversion_candidates(ws):
        print(f"[{c['confidence']:6s}] {c['name']:24s} — {c['reason']}  →  \"{c['suggested_command']}\"")
