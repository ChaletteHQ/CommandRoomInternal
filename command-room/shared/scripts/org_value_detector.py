#!/usr/bin/env python3
"""Org-value detector — observed account/contract value near a CLIENT org,
proposed through the Living Brain rails (SPEC HIST1 Part 2, step 12).

WHY THIS EXISTS
Part 1 shipped the grouped `org.money` object + its single confirm-only
writer (`org_writer.set_org_money`), so every dollar tag stops silently
disappearing for non-deal client relationships — but nothing POPULATES it
except an explicit user statement. This detector is the observed lane: it
spots a stated recurring/account value near a tracked client org
("they're a $120k/yr account", "the retainer is $8k a month") and proposes
it. **Every fire is tier="confirm"** — money is identity-adjacent trust,
never estimated, never auto (D4 / Bug #92); confirmation routes through
apply-choices → `set_org_money(confirmed=True)`, the only money writer.

FENCE vs deal_signal_detector (PIPE1 coordination): the deal detector owns
bare money amounts near an OPEN DEAL thread (deal.value). This detector
requires ACCOUNT-shaped language (retainer / per-year / recurring…) and
targets the ORG record — an org can legitimately carry both (deal value on
deal-thread items, account value on org-level items; intended, spec §8).
An amount with deal language and no account language never fires here.

QBO OPPORTUNISTIC LANE: this module is pure/substrate-only and cannot call
connector tools. Where a `qbo_*` sales tool is discoverable at skill
runtime, the SKILL (workspace-manager's org money handler) reads
sales-by-customer and passes the figure to `propose_org_value` — a
confirm proposal like any other, source-stamped "qbo:sales-by-customer".
QBO absent → no-op, no dependency (the money-cluster QBO gate).

STRUCTURE mirrors deal_signal_detector: pure detection + a separate
propose() entry point; money parsing and vocabulary IMPORTED, never forked.
Pure / substrate-only / no connectors. stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import event_refs  # noqa: E402 — threads_of only; event reads are org-scoped
from deal_signal_detector import (  # noqa: E402 — one vocabulary, never forked
    _parse_money,
    _source_phrase,
)
from prospect_conversion_detector import (  # noqa: E402
    _event_org_ids,
    _event_text,
)

TEXT_WINDOW_DAYS = 120
MAX_PROPOSALS_PER_RUN = 3

_DETECTOR = "org-value"

# Account-shaped language — the qualifier that separates a recurring/account
# value from deal-shaped money (which deal_signal_detector owns). Lowercased
# substring match near the amount.
ACCOUNT_MARKERS = (
    "account", "retainer", "/yr", "/year", "per year", "a year", "yearly",
    "annual", "annually", "/mo", "per month", "a month", "monthly",
    "recurring", "arr", "mrr", "ongoing engagement",
)

# Monthly-flavored markers — the proposal records what was OBSERVED
# (an mrr-flavored amount proposes mrr, yearly proposes account_value);
# never annualized, never estimated (D4).
_MONTHLY_MARKERS = ("/mo", "per month", "a month", "monthly", "mrr")

CONFIRM_TUPLES = [
    {"action": "confirm proposal"},
    {"action": "dismiss proposal"},
    {"action": "snooze proposal 7d"},
]

_RELATIONSHIP_IN_SCOPE = ("client", "partner")


def _entities(workspace_root: Path) -> dict:
    p = workspace_root / "_hq" / "data" / "entities.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["entities"] if isinstance(d.get("entities"), dict) else d


def detect_org_value_signals(workspace_root: str | Path) -> list[dict]:
    """Return observed org-value candidates, each as:
        {org_id, org_name, proposed_money: {account_value|mrr: N,
         source, as_of}, evidence, fingerprint, render_line}
    Detection only — never writes, never estimates. One candidate per org
    per run (first qualifying event wins). Empty list when nothing
    qualifies."""
    workspace_root = Path(workspace_root)
    ent = _entities(workspace_root)
    orgs = ent.get("orgs") or []
    threads = ent.get("threads") or ent.get("projects") or []

    tracked = {o["id"]: o for o in orgs
               if o.get("id") and o.get("status") != "archived"
               and o.get("relationship_type") in _RELATIONSHIP_IN_SCOPE}
    if not tracked:
        return []

    thread_org: dict[str, str] = {}
    for t in threads:
        # Canonical thread→org field is the SINGULAR affiliation_id (per
        # render_master_tracker/_org_of + integrity_check C2) — the same
        # chain deal_signal_detector/_thread_org and deal_state use.
        oid = (t.get("org") or t.get("org_id") or t.get("affiliation_id")
               or (t.get("affiliation_ids") or [None])[0])
        if oid and t.get("id"):
            thread_org[t["id"]] = oid

    # ORG-SCOPED read (PGUARD1 D1): the account mask + personal-lane drop
    # apply by design — masked history never drives a money proposal.
    from events_io import load_events_org_scoped
    events, _skipped = load_events_org_scoped(workspace_root)
    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(days=TEXT_WINDOW_DAYS)).strftime("%Y-%m-%d")
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

    candidates: list[dict] = []
    seen_orgs: set[str] = set()
    for ev in events:
        ts = str(ev.get("ts") or "")
        if ts and ts[:10] < cutoff:
            continue
        text = _event_text(ev)
        if not text:
            continue
        marker = next((m for m in ACCOUNT_MARKERS if m in text), None)
        if marker is None:
            continue  # deal-shaped or bare money is NOT this detector's
        amount = _parse_money(text)
        if amount is None:
            continue
        refs = set(_event_org_ids(ev))
        for tid in event_refs.threads_of(ev):
            if tid in thread_org:
                refs.add(thread_org[tid])
        for oid in refs & set(tracked):
            if oid in seen_orgs:
                continue
            org = tracked[oid]
            money_now = org.get("money") if isinstance(org.get("money"),
                                                       dict) else {}
            field = "mrr" if any(m in text for m in _MONTHLY_MARKERS) \
                else "account_value"
            if money_now.get(field) == amount:
                continue  # already on file — nothing to propose
            seen_orgs.add(oid)
            name = org.get("canonical_name") or oid
            src = _source_phrase(ev)
            amount_disp = f"${amount:,.0f}"
            candidates.append({
                "org_id": oid,
                "org_name": name,
                "proposed_money": {field: amount,
                                   "source": f"observed — {src}",
                                   "as_of": today},
                "evidence": f'"{marker}" + {amount_disp} in {src}',
                "fingerprint": f"ov:{oid}:{field}:{int(amount)}",
                "render_line": (
                    f"💰 {name} looks like a {amount_disp}"
                    f"{'/mo' if field == 'mrr' else '/yr'} account "
                    f"({src}) — confirm to record it (never estimated, "
                    "always sourced)."),
            })
    return candidates


def propose_org_value(
    workspace_root: str | Path,
    org_id: str,
    money: dict,
    *,
    evidence: str,
    source_ref: str,
    org_name: str = "",
    detector: str = _DETECTOR,
    render_line: str = "",
) -> dict:
    """Propose ONE org money figure (tier=confirm — money is NEVER auto,
    D4/Bug #92). The entry point for BOTH lanes: the substrate scan below
    and the skill-runtime QBO reader (workspace-manager passes the
    sales-by-customer figure here with source_ref="qbo:sales-by-customer").
    Confirmation applies via apply-choices → set_org_money(confirmed=True).
    Never call set_org_money from a detector path."""
    import brain_proposals

    if not isinstance(money, dict) or not money:
        raise ValueError("propose_org_value needs a non-empty money dict")
    title = org_name or org_id
    return brain_proposals.propose(
        workspace_root,
        kind="org_money",
        fingerprint=f"ov:{org_id}:"
                    + ":".join(f"{k}={money[k]}" for k in sorted(money)
                               if isinstance(money[k], (int, float))),
        evidence=evidence,
        action_tuples=CONFIRM_TUPLES,
        tier="confirm",
        detector=detector,
        render_line=render_line or (
            f"💰 A recorded value was observed for {title} — confirm to "
            "save it to the company record."),
        org_id=org_id,
        extra={"proposal_kind": "org_money",
               "proposed_money": dict(money, source=money.get("source")
                                      or source_ref),
               "org_name": title, "title": title,
               "source_ref": source_ref},
    )


def run_org_value_scan(
    workspace_root: str | Path,
    *,
    source_skill: str = _DETECTOR,
) -> dict:
    """Detect → propose each candidate (tier=confirm; ledger cooldown +
    open-dedup inside propose()). Capped per run, overflow counted.
    Returns {n_candidates, n_proposed, n_suppressed, n_capped}."""
    candidates = detect_org_value_signals(workspace_root)
    n_proposed = n_suppressed = n_capped = 0
    for c in candidates:
        if n_proposed >= MAX_PROPOSALS_PER_RUN:
            n_capped += 1
            continue
        result = propose_org_value(
            workspace_root, c["org_id"], c["proposed_money"],
            evidence=c["evidence"],
            source_ref=c["proposed_money"].get("source") or "observed",
            org_name=c["org_name"], render_line=c["render_line"])
        if result["status"] == "proposed":
            n_proposed += 1
        else:
            n_suppressed += 1
    return {"n_candidates": len(candidates), "n_proposed": n_proposed,
            "n_suppressed": n_suppressed, "n_capped": n_capped}


__all__ = [
    "TEXT_WINDOW_DAYS",
    "MAX_PROPOSALS_PER_RUN",
    "ACCOUNT_MARKERS",
    "detect_org_value_signals",
    "propose_org_value",
    "run_org_value_scan",
]


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    for c in detect_org_value_signals(ws):
        print(f"{c['org_name']:24s} — {c['evidence']}")
