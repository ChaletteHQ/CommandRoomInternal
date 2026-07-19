#!/usr/bin/env python3
"""Deal-signal detector — observed stage/value/creation signals, proposed
through the Living Brain rails (SPEC LB1 D7; absorbs PIPE1 Part 2).

WHY THIS EXISTS
PIPE1 shipped the deal object + the single writer (`deal_state.py`) and
reserved `deal_update_proposed`/`deal_update_dismissed` for the observed
lane. This detector is that lane: it scans recent events on prospect/client
orgs for stage markers, verbal-agreement language, won language, money
amounts near a deal thread, and deal-shaped signal on orgs with NO open deal
thread (M's Part 2 scope addition: propose deal CREATION — propose-and-
confirm only, never silent). Emission is ONLY through
`brain_proposals.propose(tier="confirm")` — observed signals never auto-flip
(PIPE1 D6 / Bug #92); confirmation routes through apply-choices →
`deal_state`, the only deal writer.

STRUCTURE mirrors `prospect_conversion_detector.py`: pure detection returns
candidate dicts with ready-to-render `render_line`s (Bug #92b — surfaces
render verbatim, never re-decide inclusion); a separate job entry point does
the propose() emission + receipt for the `deal-signals` MAINTENANCE_JOBS row.

Won-language markers are IMPORTED from prospect_conversion_detector
(CONVERSION_MARKERS) — one vocabulary, never forked.

Pure / substrate-only / no connectors. stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import event_refs  # noqa: E402
from prospect_conversion_detector import (  # noqa: E402
    CONVERSION_MARKERS,
    _event_org_ids,
    _event_text,
)

# Look-back for textual signals (matches the prospect detector's window).
TEXT_WINDOW_DAYS = 120

# Observed stage markers — lowercased substring match, mapped to the ACTIVE
# stage the signal suggests (won/lost are never stages; won-language routes
# through close_deal on confirm).
STAGE_MARKERS: dict[str, tuple[str, ...]] = {
    "proposal_sent": (
        "sent the proposal", "proposal sent", "sent over the proposal",
        "sent our proposal", "proposal is out", "submitted the proposal",
        "sent them the quote", "quote sent",
    ),
    "negotiating": (
        "verbal agreement", "verbally agreed", "agreed verbally",
        "negotiating", "redlines", "redlining", "contract review",
        "reviewing the contract", "handshake deal",
    ),
}

# Deal-shaped language that qualifies an org with NO open deal thread for a
# CREATION proposal (weaker than a stage marker on purpose — creation needs
# pursuit signal, not progress signal).
CREATION_MARKERS = (
    "proposal", "pricing", "quote", "scope of work", "discovery call",
    "pitch", "rfp", "budget for", "engagement letter",
)

# Money amounts near a deal thread ($12,000 / $12k / 12,000 dollars).
_MONEY_RE = re.compile(
    r"(?:\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k|m)?)"
    r"|(?:(\d{1,3}(?:,\d{3})+|\d+)\s?(?:dollars|usd))",
    re.IGNORECASE,
)

_DETECTOR = "deal-signals"
TASK_ID = "deal-signals"


def _entities(workspace_root: Path) -> dict:
    p = workspace_root / "_hq" / "data" / "entities.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["entities"] if isinstance(d.get("entities"), dict) else d


def _parse_money(text: str):
    m = _MONEY_RE.search(text)
    if not m:
        return None
    raw = m.group(1) or m.group(3)
    if not raw:
        return None
    try:
        val = float(raw.replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    if suffix == "k":
        val *= 1_000
    elif suffix == "m":
        val *= 1_000_000
    return val


_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _source_phrase(ev) -> str:
    """FS-10 — a short, dated, provenance-honest source phrase for the row
    evidence line ("your Jul 8 sent mail"). Never invents a date: if the event
    has no parseable ts, the phrase is date-free. The source noun comes from
    the event's own type / source_skill, never guessed."""
    if not isinstance(ev, dict):
        return ""
    ts = str(ev.get("ts") or "")
    date_str = ""
    if len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
        try:
            mo, day = int(ts[5:7]), int(ts[8:10])
            date_str = f"{_MONTHS[mo]} {day}"
        except (ValueError, IndexError):
            date_str = ""
    etype = str(ev.get("type") or "")
    skill = str(ev.get("source_skill") or "")
    tag = f"{etype} {skill}".lower()
    if "sent" in tag or "reconcile" in tag or "email_sent" in etype:
        noun = "sent mail"
        owned = "your "
    elif "email" in tag or "inbox" in tag or "thread" in tag:
        noun, owned = "email thread", "a "
    elif "meeting" in tag or "granola" in tag or "transcript" in tag:
        noun, owned = "meeting notes", "your "
    elif "slack" in tag:
        noun, owned = "Slack message", "a "
    else:
        noun, owned = "activity", ""
    if date_str:
        return f"{owned}{date_str} {noun}"
    return f"{owned}recent {noun}".strip()


def detect_deal_signals(workspace_root: str | Path) -> list[dict]:
    """Return observed deal-signal candidates, each as:
        {kind: 'deal_update'|'deal_creation', org_id, org_name, thread_id?,
         proposal_kind: 'stage'|'value'|'won', proposed_stage?,
         proposed_value?, evidence, fingerprint, render_line}

    Detection only — never writes. `render_line` is verbatim-render (Bug
    #92b): the surface MUST NOT re-decide inclusion. One candidate per
    (target, proposal_kind) — first qualifying event wins."""
    workspace_root = Path(workspace_root)
    ent = _entities(workspace_root)
    orgs = ent.get("orgs") or []
    threads = ent.get("threads") or ent.get("projects") or []

    tracked = {
        o["id"]: o for o in orgs
        if o.get("id") and o.get("relationship_type") in ("prospect", "client")
    }
    if not tracked:
        return []

    # Open deal threads per org (deal_state doctrine: kind == "deal",
    # non-terminal status).
    open_deals: dict[str, dict] = {}
    thread_org: dict[str, str] = {}
    for t in threads:
        oid = t.get("org") or t.get("org_id") or t.get("affiliation_id") \
            or (t.get("affiliation_ids") or [None])[0]
        if t.get("id") and oid:
            thread_org[t["id"]] = oid
        if t.get("kind") != "deal":
            continue
        if t.get("status") in ("resolved", "archived"):
            continue
        if oid in tracked:
            open_deals.setdefault(oid, t)

    # FS-18b — the no-tracked-deal predicate is SHARED with the confirm
    # handler (deal_state.org_deal_coverage, one helper never forked): an org
    # with an ACTIVE ENGAGEMENT THREAD is covered too. Pre-T2.2 the detector
    # checked only kind='deal' threads, so it proposed deal CREATION for orgs
    # whose confirm the create path then refused — an unconfirmable zombie
    # proposal (the live Category case, RV-5).
    from deal_state import org_deal_coverage

    covered: set[str] = set()
    for oid in tracked:
        if org_deal_coverage(threads, oid) is not None:
            covered.add(oid)

    events_path = workspace_root / "_hq" / "data" / "events.jsonl"
    if not events_path.exists():
        return []
    events = event_refs.load_events(events_path)

    candidates: list[dict] = []
    seen: set[str] = set()

    def _push(kind, oid, *, proposal_kind, evidence, thread=None,
              proposed_stage=None, proposed_value=None, source_ev=None):
        org = tracked[oid]
        name = org.get("canonical_name") or oid
        tid = thread.get("id") if thread else None
        fingerprint = f"deal:{tid or oid}:{proposal_kind}" + (
            f":{proposed_stage}" if proposed_stage else "")
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        deal_name = (thread.get("display_name") if thread else None) or name
        # FS-10 row shape: title = the org/deal name (the row's own header);
        # render_line = "{kind badge} · {evidence with date} · {consequence}".
        # Brand-clean (no emoji, no em dash); the `·` separators match the
        # agreed shape "Acme Co — likely deal · proposal language in
        # your Jul 8 sent mail · no pipeline record".
        src = _source_phrase(source_ev)
        ev_dated = f"{evidence} in {src}" if src else evidence
        if kind == "deal_creation":
            badge, consequence = "likely deal", "no pipeline record"
        elif proposal_kind == "won":
            badge, consequence = "sounds won", "still open in your pipeline"
        elif proposal_kind == "value":
            badge, consequence = "has a number attached", "value not recorded"
        else:
            badge = f"looks moved to {proposed_stage.replace('_', ' ')}"
            consequence = "pipeline stage is behind"
        line = f"{badge} · {ev_dated} · {consequence}"
        cand = {
            "kind": kind,
            "org_id": oid,
            "org_name": name,
            "title": deal_name,
            "proposal_kind": proposal_kind,
            "evidence": ev_dated,
            "fingerprint": fingerprint,
            "render_line": line,
        }
        if tid:
            cand["thread_id"] = tid
        if proposed_stage:
            cand["proposed_stage"] = proposed_stage
        if proposed_value is not None:
            cand["proposed_value"] = proposed_value
        candidates.append(cand)

    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(days=TEXT_WINDOW_DAYS)).strftime("%Y-%m-%d")
    for ev in events:
        ts = str(ev.get("ts") or "")
        if ts and ts[:10] < cutoff:
            continue  # review F4: the window is enforced, not decorative
        text = _event_text(ev)
        if not text:
            continue
        refs = set(_event_org_ids(ev))
        for tid in event_refs.threads_of(ev):
            if tid in thread_org:
                refs.add(thread_org[tid])
        refs &= set(tracked)
        if not refs:
            continue
        for oid in refs:
            thread = open_deals.get(oid)
            if thread is not None:
                # Progress signals on an OPEN deal.
                for stage, markers in STAGE_MARKERS.items():
                    hit = next((m for m in markers if m in text), None)
                    if hit and (thread.get("deal") or {}).get("stage") != stage:
                        _push("deal_update", oid, proposal_kind="stage",
                              proposed_stage=stage, thread=thread,
                              evidence=f'"{hit}" language', source_ev=ev)
                won_hit = next((m for m in CONVERSION_MARKERS if m in text), None)
                if won_hit:
                    _push("deal_update", oid, proposal_kind="won",
                          thread=thread,
                          evidence=f'"{won_hit.strip()}" language', source_ev=ev)
                money = _parse_money(text)
                if money is not None and not (thread.get("deal") or {}).get("value"):
                    _push("deal_update", oid, proposal_kind="value",
                          proposed_value=money, thread=thread,
                          evidence="a money amount", source_ev=ev)
            elif oid not in covered:
                # NO coverage at all — no open deal thread AND no active
                # engagement thread (FS-18b) — creation signal (M's Part 2
                # scope addition; propose-and-confirm only, never silent).
                hit = next((m for m in CREATION_MARKERS if m in text), None)
                stage_hit = any(m in text
                                for ms in STAGE_MARKERS.values() for m in ms)
                if hit or stage_hit:
                    _push("deal_creation", oid, proposal_kind="creation",
                          evidence=f'{hit or "deal-shaped"} language',
                          source_ev=ev)
    return candidates


def run_deal_signal_job(workspace_root: str | Path, *, fired_via: str = "scheduled") -> dict:
    """The `deal-signals` MAINTENANCE_JOBS entry point: detect → propose each
    candidate through the Living Brain rails (tier=confirm, ledger cooldown +
    open-dedup enforced inside propose()) → write the job's pack_run receipt.
    Returns {n_candidates, n_proposed, n_suppressed, receipt}."""
    import brain_proposals
    from receipts import log_receipt

    candidates = detect_deal_signals(workspace_root)
    n_proposed = 0
    n_suppressed = 0
    for c in candidates:
        action_tuples = [
            {"action": "confirm proposal"},
            {"action": "dismiss proposal"},
            {"action": "snooze proposal 7d"},
        ]
        extra = {"proposal_kind": c["proposal_kind"]}
        for k in ("proposed_stage", "proposed_value", "org_name", "title"):
            if c.get(k) is not None:
                extra[k] = c[k]
        result = brain_proposals.propose(
            workspace_root,
            kind=c["kind"],
            fingerprint=c["fingerprint"],
            evidence=c["evidence"],
            action_tuples=action_tuples,
            tier="confirm",
            detector=_DETECTOR,
            render_line=c["render_line"],
            thread_id=c.get("thread_id"),
            org_id=c["org_id"],
            extra=extra,
        )
        if result["status"] == "proposed":
            n_proposed += 1
        else:
            n_suppressed += 1
    receipt = log_receipt(
        workspace_root, TASK_ID,
        receipt_type="pack_run",
        fired_via=fired_via,
        surfaced=n_proposed,
        extra_data={"n_candidates": len(candidates),
                    "n_suppressed": n_suppressed},
    )
    return {"n_candidates": len(candidates), "n_proposed": n_proposed,
            "n_suppressed": n_suppressed, "receipt": receipt}


def validate_deal_signals_ran(workspace_root: str | Path) -> dict:
    """Enforcement binds to the receipt artifact, never narration (the
    reconcile-sent doctrine). ok=True when a deal-signals pack_run exists."""
    from receipts import iter_receipts

    latest = None
    for r in iter_receipts(workspace_root, task_ids=[TASK_ID]):
        latest = r
    if latest is None:
        return {"ok": False, "ran": False,
                "reason": "no deal-signals receipt on file"}
    return {"ok": True, "ran": True, "receipt": latest}


__all__ = [
    "STAGE_MARKERS",
    "CREATION_MARKERS",
    "TASK_ID",
    "detect_deal_signals",
    "run_deal_signal_job",
    "validate_deal_signals_ran",
]


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    for c in detect_deal_signals(ws):
        print(f"[{c['kind']:13s}] {c['org_name']:24s} — {c['evidence']}")
