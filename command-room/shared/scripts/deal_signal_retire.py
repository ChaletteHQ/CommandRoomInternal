#!/usr/bin/env python3
"""Deal-signal retirement — a deal signal is retired by the FACT it asked
about, not by a nag counter (DEALNAG1, 2026-09-03).

WHY THIS EXISTS
The deal-signals job proposes "Command Room thinks [Org] is a live deal"
through the Living Brain rails and then leaves the proposal to the user
(confirm / dismiss / snooze) or to the 14-day TTL. Nothing bound the
proposal to the outcome it was asking about, so on a live workspace a deal
that was WON on Aug 18 was still nagging on Sep 2: the win closed the deal
thread (status resolved, outcome won), `deal_state.org_deal_coverage`
reads a terminal thread as "no coverage", and the Sunday detector proposed
CREATION again for an org that had just paid. The same shape re-proposed a
prospect the same day its previous proposal expired (expiry writes no
cooldown). Meanwhile the "looks like a client" nudge promoted every org the
`new prospect` command created, because that command records a kind=client
engagement edge for the sales conversation itself.

WHAT RETIRES A DEAL SIGNAL (the premise no longer holds)
  - a WON or LOST deal thread for the org (`deal.outcome`, the closed-won
    fact; `close_deal` calls `retire_deal_proposals` in the same turn);
  - a paid / signed event referencing the org (`deal_won`, `invoice_sent`,
    and the payment / agreement types in PAID_OR_SIGNED_EVENT_TYPES);
  - the prospect -> client conversion (`org_writer.update_org` flipping
    `relationship_type` to client calls `retire_deal_proposals` with reason
    `converted`).
Retirement is a `brain_proposal_resolved` tombstone with
`user_action: superseded`, `resolved_by: system` and `note: <reason>` —
the same 60-day ledger cooldown a decline gets, so a retired item cannot
re-propose — plus, for thread-bound rows, the `deal_update_dismissed`
record `resolve_proposal` already writes. The receipt for the job-time
sweep (`retire_settled`) is the `deal-signals` pack_run's own
`extra_data.n_retired` / `retired_by_reason`; a hook-time retirement's
receipt is the tombstone itself (stamped with the caller's source_skill).
No new receipt type: the dispatcher's due-ness rule takes the NEWEST
receipt under a task id regardless of type, so a hook-time receipt under
`deal-signals` would falsely satisfy the Sunday slot.

`settled_orgs` is also the ONE predicate `prospect_conversion_detector`
uses for "has a paid or signed signal" — a sizing / engagement record alone
is never a client signal (M's addendum, 2026-09-02).

Pure stdlib; every full-history read goes through `events_io`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import event_refs  # noqa: E402
from entities_io import entities_collection  # noqa: E402

# Event types that mean money changed hands or a signature landed. Defensive
# set: only `deal_won` and `invoice_sent` are registered in EVENT_TYPES.md
# today; the others are the shapes a payment / e-sign connector would write
# and cost nothing to honour when they arrive. An unknown type simply
# contributes no signal.
PAID_OR_SIGNED_EVENT_TYPES = frozenset({
    "deal_won", "invoice_sent", "invoice_paid", "payment_received",
    "agreement_signed", "contract_signed",
})

# The proposal kinds this module retires — the deal-signals detector's own
# two. `org_money` (an account-value ask) is a different question and is
# NOT retired by a win.
RETIRE_KINDS = frozenset({"deal_creation", "deal_update"})

RETIRE_REASONS = ("deal_won", "deal_lost", "converted", "paid_or_signed")

_TERMINAL_OUTCOMES = ("won", "lost")


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entities(workspace_root: Path) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["entities"] if isinstance(d.get("entities"), dict) else d


def _load_events(workspace_root) -> list[dict]:
    """Org-scoped full-history read (events_io) — the same seam the
    proposal projector reads through, so a masked account's history can
    neither open nor retire a deal signal."""
    from events_io import load_events_org_scoped

    if not (Path(workspace_root) / "_hq" / "data" / "events.jsonl").exists():
        return []
    events, _skipped = load_events_org_scoped(workspace_root)
    return events


def _thread_org_id(t: dict) -> Optional[str]:
    return (t.get("org") or t.get("org_id") or t.get("affiliation_id")
            or (t.get("affiliation_ids") or [None])[0])


def _event_org_ids(ev: dict) -> set[str]:
    out: set[str] = set()
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for r in (ev.get("org_ids") or []):
        out.add(r)
    for r in (d.get("org_ids") or []):
        out.add(r)
    for k in ("org_id", "primary_org_id", "to_org_id"):
        for src in (ev, d):
            v = src.get(k)
            if isinstance(v, str) and v:
                out.add(v)
    return {o for o in out if isinstance(o, str) and o.startswith("org_")}


def terminal_deal_threads(entities: dict) -> dict[str, str]:
    """thread_id -> outcome for every deal thread with a terminal outcome
    (won / lost). The closed-won fact lives HERE — engagement records carry
    no status field — so this is what a deal_update row is checked against."""
    out: dict[str, str] = {}
    for t in entities_collection(entities, "projects"):
        if not isinstance(t, dict) or t.get("kind") != "deal":
            continue
        deal = t.get("deal") if isinstance(t.get("deal"), dict) else {}
        outcome = deal.get("outcome")
        if outcome in _TERMINAL_OUTCOMES and t.get("id"):
            out[t["id"]] = outcome
    return out


def settled_orgs(entities: dict, events: list[dict]) -> dict[str, dict]:
    """org_id -> {reason, since, thread_id?} for every org that carries a
    paid-or-signed fact: a WON deal thread, or a PAID_OR_SIGNED event that
    references the org (directly, or through one of its threads).

    ONE predicate, two consumers, never forked: the deal-signals creation
    lane skips a settled org (a won deal is a pipeline record, not the lack
    of one), and prospect_conversion_detector requires a settled org before
    it says "looks like a client"."""
    out: dict[str, dict] = {}
    # CUTB item 4 — a win an undo put back (`deal_won_reversed`, written by
    # brain_undo when the promotion that manufactured the deal is reversed)
    # is not a paid-or-signed fact: neither its thread nor its event settles
    # the org. Same fold as list_closed_deals / won_rate_90d.
    reversed_threads: set[str] = set()
    for ev in events or []:
        if ev.get("type") == "deal_won_reversed":
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if d.get("thread_id"):
                reversed_threads.add(str(d["thread_id"]))
    threads = entities_collection(entities, "projects")
    thread_org: dict[str, str] = {}
    for t in threads:
        if not isinstance(t, dict):
            continue
        oid = _thread_org_id(t)
        if t.get("id") and oid:
            thread_org[t["id"]] = oid
        if t.get("kind") != "deal" or not oid:
            continue
        if str(t.get("id") or "") in reversed_threads:
            continue  # CUTB item 4
        deal = t.get("deal") if isinstance(t.get("deal"), dict) else {}
        if deal.get("outcome") == "won":
            since = str(deal.get("closed_at") or "")
            cur = out.get(oid)
            if cur is None or since > cur.get("since", ""):
                out[oid] = {"reason": "deal_won", "since": since,
                            "thread_id": t.get("id")}
    for ev in events or []:
        etype = ev.get("type")
        if etype not in PAID_OR_SIGNED_EVENT_TYPES:
            continue
        if etype == "deal_won" and reversed_threads:
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if str(d.get("thread_id") or "") in reversed_threads:
                continue  # CUTB item 4 — the win was put back
        refs = set(_event_org_ids(ev))
        for tid in event_refs.threads_of(ev):
            if tid in thread_org:
                refs.add(thread_org[tid])
        reason = "deal_won" if etype == "deal_won" else "paid_or_signed"
        since = str(ev.get("ts") or "")[:10]
        for oid in refs:
            cur = out.get(oid)
            if cur is None:
                out[oid] = {"reason": reason, "since": since}
            elif cur.get("reason") != "deal_won" and reason == "deal_won":
                out[oid] = {"reason": reason, "since": since}
    return out


def _open_deal_rows(workspace_root, *, now_iso: str) -> list[dict]:
    """Every deal-signal row with NO tombstone — including rows past their
    TTL that `cleanup`'s `expire_stale` has not yet stamped (CUTB item 1(c)).
    A lapsed-untombstoned row about a settled org was unreachable before:
    the projector hid it, so nothing retired it, so it sat forever neither
    open nor answered. Retiring it writes the same `superseded` tombstone an
    open row gets; a retired row can never re-render, whatever the clock."""
    import brain_proposals

    return [
        r for r in brain_proposals.open_brain_proposals(
            workspace_root, now_iso=now_iso, include_lapsed=True)
        if r.get("kind") in RETIRE_KINDS
    ]


def retire_deal_proposals(
    workspace_root,
    *,
    reason: str,
    source_skill: str,
    org_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict:
    """Retire every open deal-signal proposal bound to `org_id` and/or
    `thread_id` because `reason` (one of RETIRE_REASONS) made its premise
    false. Writes the superseded tombstone + ledger cooldown per row
    through `brain_proposals.resolve_proposal` (never a raw append).
    Idempotent: nothing open -> nothing written. Returns
    {n_retired, retired: [proposal ids], reason}."""
    if reason not in RETIRE_REASONS:
        raise ValueError(
            f"reason must be one of {RETIRE_REASONS}, got {reason!r}")
    if not org_id and not thread_id:
        raise ValueError("retire_deal_proposals needs org_id and/or thread_id")
    import brain_proposals

    now_iso = now_iso or _now_iso()
    retired: list[str] = []
    for row in _open_deal_rows(workspace_root, now_iso=now_iso):
        hit = ((org_id and row.get("org_id") == org_id)
               or (thread_id and row.get("thread_id") == thread_id))
        if not hit:
            continue
        res = brain_proposals.resolve_proposal(
            workspace_root, row["id"], "superseded",
            resolved_by="system", source_skill=source_skill, note=reason)
        if res.get("status") == "resolved":
            retired.append(row["id"])
    return {"n_retired": len(retired), "retired": retired, "reason": reason}


def retire_settled(
    workspace_root,
    *,
    source_skill: str = "deal-signals",
    now_iso: Optional[str] = None,
) -> dict:
    """The job-time sweep: retire every open deal-signal row whose premise
    the substrate already contradicts — a deal_update row on a terminal
    thread, or a deal_creation row on a settled org (won deal / paid-or-
    signed event). Runs FIRST inside `run_deal_signal_job` so the standing
    nags go before the detector looks for new ones, and its counts ride the
    job's pack_run receipt. Returns {n_retired, retired, by_reason}."""
    now_iso = now_iso or _now_iso()
    entities = _entities(workspace_root)
    events = _load_events(workspace_root)
    terminal = terminal_deal_threads(entities)
    settled = settled_orgs(entities, events)
    import brain_proposals

    retired: list[str] = []
    by_reason: dict[str, int] = {}
    for row in _open_deal_rows(workspace_root, now_iso=now_iso):
        reason = None
        tid = row.get("thread_id")
        oid = row.get("org_id")
        if tid and tid in terminal:
            reason = f"deal_{terminal[tid]}"
        elif oid and oid in settled:
            reason = settled[oid]["reason"]
        if reason is None:
            continue
        res = brain_proposals.resolve_proposal(
            workspace_root, row["id"], "superseded",
            resolved_by="system", source_skill=source_skill, note=reason)
        if res.get("status") == "resolved":
            retired.append(row["id"])
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {"n_retired": len(retired), "retired": retired,
            "by_reason": by_reason}


__all__ = [
    "PAID_OR_SIGNED_EVENT_TYPES",
    "RETIRE_KINDS",
    "RETIRE_REASONS",
    "terminal_deal_threads",
    "settled_orgs",
    "retire_deal_proposals",
    "retire_settled",
]


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    ent = _entities(Path(ws))
    evs = _load_events(ws)
    for oid, info in sorted(settled_orgs(ent, evs).items()):
        print(f"{oid:12s} settled: {info['reason']} since {info['since']}")
