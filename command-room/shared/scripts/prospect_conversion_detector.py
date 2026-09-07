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

**M RULING 4 (2026-09-03) — a paid or signed signal no longer ASKS.** The
promotion applies itself (`org_promotion.promote_org`: relationship flip +
engagement edge, `org_promoted` receipt, one CHANGED line, undoable), so a
settled prospect is NOT a candidate here — asking about a fact the
workspace has already written down was the defect. This detector is now the
AMBIGUOUS lane only, which is exactly M's "only genuinely ambiguous cases
surface at all":

SIGNALS (per prospect org)
  paid or signed but NOT promotable (HIGH confidence): the org carries a
    paid or signed fact AND the workspace has no `is_primary_focus` org to
    hang the client engagement off, so `org_promotion` refuses to guess and
    skips (`no_primary_focus`). The fact is certain; the destination is
    not — a real question, and the only one this lane still raises on a
    settled org.
  textual (MEDIUM confidence):
    - a recent event referencing the org carries client-conversion language
      ('signed', 'engagement agreement', 'kicked off', 'now a client',
      'active client', 'statement of work', 'retainer', ...) — and is not pure
      pursuit-phase noise.

  RETIRED as signals (DEALNAG1 — M's addendum 2026-09-02): an active
  client/partner ENGAGEMENT record and an active AFFILIATED THREAD. Both
  used to read as HIGH, and both are what the `new prospect` command writes
  for the sales conversation itself (a kind=client edge labelled "Active
  sales conversation" plus a prospect thread) — so a prospect created for
  sizing, with a first call still weeks out, was "looks like a client now"
  the next morning. A sizing / engagement record alone is never a client
  signal; the promotion nudge requires a paid or signed fact.

Pure / substrate-only / no connectors / no mutation. stdlib only.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import event_refs  # noqa: E402
from entities_io import entities_collection  # noqa: E402

# Bilingual overlay (Spanish beta) — inert for English installs. See
# shared/scripts/lexicon.py + references/SPANISH_BUILD_PLAN.md.
try:
    import lexicon as _lex
except Exception:  # pragma: no cover
    _lex = None


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _conversion_markers():
    """Merged conversion-marker tuple, or the English default when the overlay
    is inactive/absent (production path). NOTE: es.json also carries a
    ``pursuit_only`` list for parity with the design, but the core's
    ``_PURSUIT_ONLY`` constant is currently unused, so those entries are inert
    until a future version reconnects that gate."""
    if _lex is None:
        return CONVERSION_MARKERS
    return _lex.load_lexicon_terms("prospect_conversion", "conversion_markers", CONVERSION_MARKERS)


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
    return any(m in text for m in _conversion_markers())


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
    threads = entities_collection(ent, "projects")

    prospects = {o["id"]: o for o in orgs if o.get("id") and o.get("relationship_type") == "prospect"}
    if not prospects:
        return []
    del engagements  # DEALNAG1 — an engagement record is not a client signal

    # Textual signal C — recent org-referencing events with conversion language.
    text_hit: dict[str, str] = {}
    # REVIEW DEALNAG1 F-3 — ONE door. This read used to be a raw
    # `event_refs.load_events`, while `org_promotion` (which decides whether
    # the same org is promoted instead of asked about) reads through the
    # org-scoped seam. On a workspace with a masked account the two could
    # disagree about the same fact, and the disagreement would land in the
    # `no_primary_focus` branch — the one case that still asks. Both sides
    # now read `events_io.load_events_org_scoped`, so a masked account's
    # history can neither promote an org nor raise a question about one.
    events: list[dict] = []
    events_path = workspace_root / "_hq" / "data" / "events.jsonl"
    if events_path.exists():
        from events_io import load_events_org_scoped

        events, _skipped = load_events_org_scoped(workspace_root)
        # Build thread→org map so thread-tagged events also attribute to the org.
        thread_org = {}
        for t in threads:
            oid = t.get("org") or t.get("org_id") or (t.get("affiliation_ids") or [None])[0]
            if oid:
                thread_org[t.get("id")] = oid
        cutoff = (_clock_now(workspace_root)
                  - timedelta(days=TEXT_WINDOW_DAYS)).strftime("%Y-%m-%d")
        for ev in events:
            ts = str(ev.get("ts") or "")
            if ts and ts[:10] < cutoff:
                continue  # the documented window, now enforced (lb1 review F4)
            text = _event_text(ev)
            if not _has_conversion_language(text):
                continue
            refs = set(_event_org_ids(ev))
            for tid in event_refs.threads_of(ev):
                if tid in thread_org:
                    refs.add(thread_org[tid])
            for oid in refs & set(prospects):
                if oid not in text_hit:
                    snippet = next((m for m in _conversion_markers() if m in text), "")
                    text_hit[oid] = snippet.strip()

    # Paid-or-signed (the ONE structural signal) — a won deal thread or a
    # paid / signed event, through the shared predicate. Under M's ruling 4
    # these orgs are PROMOTED automatically, so they surface here ONLY when
    # the promotion cannot run: no primary-focus org to attach the client
    # engagement to. An org whose promotion a human UNDID never appears
    # (the undo is a standing answer), and neither does one already
    # promoted (it is not a prospect any more).
    from deal_signal_retire import settled_orgs
    from org_promotion import primary_focus_org, undone_promotions

    settled = {}
    if primary_focus_org(ent) is None:
        undone = undone_promotions(workspace_root, events)
        settled = {oid: info for oid, info in settled_orgs(ent, events).items()
                   if oid in prospects and oid not in undone}

    candidates: list[dict] = []
    for oid, org in prospects.items():
        name = org.get("canonical_name") or oid
        reasons = []
        confidence = None
        if oid in settled:
            confidence = "high"
            info = settled[oid]
            since = f" on {info['since']}" if info.get("since") else ""
            # HYGIENE9 (f) / REVIEW_MERGED_v5280 F-11 — "primary org" and
            # "engagement" are record words a non-technical reader trips on.
            tail = ("but I don't know which of your companies they're a "
                    "client of")
            if info.get("reason") == "deal_won":
                reasons.append(f"their deal was marked won{since} while they're still marked a prospect, {tail}")
            else:
                reasons.append(f"a paid or signed record{since} points at them while they're still marked a prospect, {tail}")
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
                f"say `{name} is now a client`"
            ),
        })

    candidates.sort(key=lambda c: (0 if c["confidence"] == "high" else 1, c["name"]))
    return candidates


__all__ = ["detect_prospect_conversion_candidates"]


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    for c in detect_prospect_conversion_candidates(ws):
        print(f"[{c['confidence']:6s}] {c['name']:24s} — {c['reason']}  →  \"{c['suggested_command']}\"")
