#!/usr/bin/env python3
"""Org promotion — a prospect with a paid or signed fact BECOMES a client,
automatically (DEALNAG1, M's ruling 4 of 2026-09-03).

WHY THIS EXISTS
The workspace already knew. A deal was marked won, an invoice went out, an
agreement was signed — and the product's answer was to ask, every morning,
whether the org should be a client. M's ruling: *"deal signals and org
promotions apply themselves on a paid or signed signal, with a receipt, one
narration line and an undo — they are not decisions to route to a chat. A
question here was the defect."*

So this module applies the SAME conversion the `[Name] is now a client`
handler runs (org_writer flip + engagement edge — one path, never forked)
on the LB2 auto rail:

    propose(tier="auto", change_class="org_promotion")  ->  apply  ->
    resolve_proposal(..., "applied")                    in the SAME run

The auto proposal never rests open (LB2 auto lifecycle contract), so it is
adjudicated on no surface and counts toward nothing. What the person sees is
ONE line in the morning brief's CHANGED feed, fed by the `org_promoted`
event this module writes — the receipt (the event IS the receipt, PID1's
honesty rule), which also carries the `brain_batch_id` / `brain_change_class`
stamps a bare `undo` resolves through `brain_undo`.

THREE THINGS IT WILL NOT DO
  1. **Never promote without a paid or signed fact.** The predicate is
     `deal_signal_retire.settled_orgs` — a won deal thread, or a payment /
     signed-agreement / invoice event. A sizing or engagement record alone
     is not a signal and produces no promotion AND no proposal: silence.
  2. **Never re-promote what a human undid.** An `org_promoted` event whose
     seq a `brain_change_undone` marker names is a standing human answer;
     the org is skipped forever (`undone_by_user`), never re-asked. This is
     the `undone_auto_merges` precedent, one object over.
  3. **Never guess the engagement source.** The client edge hangs off the
     `is_primary_focus` org. With none on file the promotion is SKIPPED
     whole (`no_primary_focus`) — nothing half-written — and that org is the
     one case `prospect_conversion_detector` still surfaces as an ask,
     because the missing piece is a question only the person can answer.

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

from entities_io import entities_collection  # noqa: E402

PROMOTION_CHANGE_CLASS = "org_promotion"
PROMOTION_KIND = "org_promotion"
_DETECTOR = "deal-signals"

# Why a promotion did not happen — a closed vocabulary, so the sweep's
# report is readable and testable.
SKIP_REASONS = ("already_client", "undone_by_user", "no_primary_focus",
                "proposal_not_open", "error")


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entities(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["entities"] if isinstance(d.get("entities"), dict) else d


def _load_events(workspace_root) -> list[dict]:
    from events_io import load_events_org_scoped

    if not (Path(workspace_root) / "_hq" / "data" / "events.jsonl").exists():
        return []
    events, _skipped = load_events_org_scoped(workspace_root)
    return events


def primary_focus_org(entities: dict) -> Optional[dict]:
    """The org a client engagement hangs off. None when the workspace has
    not named one — the one condition that blocks a promotion."""
    for o in entities_collection(entities, "orgs"):
        if isinstance(o, dict) and o.get("is_primary_focus"):
            return o
    return None


def undone_promotions(workspace_root, events: Optional[list] = None) -> set[str]:
    """org_ids whose automatic promotion a human REVERSED — the durable
    negation this module reads before it applies anything (the
    `undone_auto_merges` precedent). An undo is a standing answer: that org
    is never promoted again and never asked about again."""
    from event_seq import event_seq

    events = _load_events(workspace_root) if events is None else events
    promoted_seq: dict[str, str] = {}
    for ev in events:
        if ev.get("type") != "org_promoted":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        seq = event_seq(ev)
        if d.get("org_id") and seq is not None:
            promoted_seq[f"seq:{seq}"] = d["org_id"]
    out: set[str] = set()
    for ev in events:
        if ev.get("type") != "brain_change_undone":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ref = str(d.get("change_ref") or "")
        if ref in promoted_seq:
            out.add(promoted_seq[ref])
    return out


def promotion_candidates(workspace_root, *, org_ids=None) -> list[dict]:
    """Prospect orgs carrying a paid or signed fact, as
    [{org_id, name, reason, since}] — what `promote_settled_prospects`
    applies. Read-only. `org_ids` scopes the read to one org (the
    close_deal hook's form)."""
    from deal_signal_retire import settled_orgs

    ent = _entities(workspace_root)
    events = _load_events(workspace_root)
    prospects = {o["id"]: o for o in entities_collection(ent, "orgs")
                 if isinstance(o, dict) and o.get("id")
                 and o.get("relationship_type") == "prospect"}
    if org_ids is not None:
        scope = set(org_ids)
        prospects = {k: v for k, v in prospects.items() if k in scope}
    out = []
    for oid, info in settled_orgs(ent, events).items():
        org = prospects.get(oid)
        if org is None:
            continue
        out.append({"org_id": oid,
                    "name": org.get("canonical_name") or oid,
                    "reason": info.get("reason") or "paid_or_signed",
                    "since": info.get("since") or ""})
    out.sort(key=lambda c: c["org_id"])
    return out


def _new_batch_id(workspace_root) -> str:
    """One id per ACT. CUTB (2026-09-06): the id used to hash only
    (workspace, second) — two promotions on one book inside the same wall-
    clock second (a `mark [org] won` and a `[Name] signed`, or the test's two
    wins back to back) shared a batch id, so `undo` of one reversed BOTH and
    the genuinely won org was offered for re-promotion. A per-call nonce keeps
    the ids distinct; the sweep still passes ONE id to every org it promotes
    in a run (that grouping is by argument, not by the clock). Shape
    unchanged: `prm_` + 10 hex."""
    import hashlib
    import uuid

    return "prm_" + hashlib.sha256(
        f"{workspace_root}|{_now_iso()}|{uuid.uuid4().hex}".encode("utf-8")).hexdigest()[:10]


def _rollback(ws, org_id, eng, created, prev_label, prev_active, prev_kind,
              *, source_skill: str) -> None:
    """REVIEW DEALNAG1 F-1 — put the record back after a promotion whose
    receipt could not be written. Same writers, same shape as the registered
    `org_promotion` reverser (relationship_type back to prospect; the edge
    this run CREATED is deactivated, one it UPDATED is restored). Each half
    is contained: a failed rollback must never mask the original failure,
    and it prints rather than raising.

    REVIEW DEALNAG1 N-1 — it also REPORTS. Returns
    {"ok": bool, "org_restored": bool, "engagement_restored": bool,
     "errors": [...]}. A doubly-failed write (receipt gone AND rollback
     refused) leaves an unreceipted `client` on the record, and the caller
     must be able to say so: a failure report that overstates its own
     recovery is worse than no report."""
    import engagement_writer
    import org_writer

    errors: list[str] = []
    org_restored = False
    try:
        org_writer.update_org(ws, org_id, relationship_type="prospect",
                              source_skill=source_skill)
        org_restored = True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"org flip: {type(exc).__name__}: {exc}")
        print(f"org_promotion: rollback of the org flip FAILED for {org_id}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
    eng_id = (eng or {}).get("id")
    if not eng_id:
        return {"ok": org_restored, "org_restored": org_restored,
                "engagement_restored": True, "errors": errors}
    engagement_restored = False
    try:
        if created:
            engagement_writer.update_engagement(
                ws, eng_id, source_skill=source_skill, is_active=False,
                label="Engagement edge from a rolled-back promotion")
        else:
            fields = {"is_active": bool(prev_active)
                      if prev_active is not None else True}
            if prev_label is not None:
                fields["label"] = prev_label
            if prev_kind is not None:
                fields["kind"] = prev_kind
            engagement_writer.update_engagement(
                ws, eng_id, source_skill=source_skill, **fields)
        engagement_restored = True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"engagement edge: {type(exc).__name__}: {exc}")
        print(f"org_promotion: rollback of the engagement edge FAILED for "
              f"{eng_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return {"ok": org_restored and engagement_restored,
            "org_restored": org_restored,
            "engagement_restored": engagement_restored, "errors": errors}


def promote_org(
    workspace_root,
    org_id: str,
    *,
    reason: str,
    since: str = "",
    source_skill: str,
    batch_id: Optional[str] = None,
    now_iso: Optional[str] = None,
    undone: Optional[set] = None,
    deal_thread_id: Optional[str] = None,
    won_seq: Optional[int] = None,
    deal_manufactured: bool = False,
    explicit: bool = False,
) -> dict:
    """Promote ONE settled prospect to client on the auto rail. Returns
    {"status": "promoted"|"skipped", ...} — `skipped` carries a `reason`
    from SKIP_REASONS and leaves the record unchanged. Atomic-or-nothing in
    both directions: every precondition is checked before the first write,
    and a receipt that cannot be written rolls the record writes back
    (`reason: "error"`, `rolled_back: True`) — a promotion nobody can see or
    undo must not stand. If the ROLLBACK also fails, the return says so
    (`rolled_back: False`, `rollback_failed: True`, `rollback_errors`) and a
    loud stderr line names the org left unreceipted: the one case a human
    has to fix by hand is the one case that must never be reported as
    recovered.

    CUTB items 3 + 4 (2026-09-06): `deal_thread_id` / `won_seq` name the
    deal whose win drove this promotion and `deal_manufactured` says that
    win was opened by `mark [org] won` with nothing on file — all three
    ride the `org_promoted` receipt so the registered reverser can put the
    manufactured deal back too (thread archived, `deal_won_reversed`
    marker). `explicit=True` is the person's own conversion request
    ("[Name] signed", `[Name] is now a client` over an open deal): it
    overrides a standing undo — a prior `undo` is an answer to the
    AUTOMATIC promotion, never to the person asking by name — and nothing
    else about the path changes (still receipted, still undoable)."""
    from brain_proposals import propose, resolve_proposal

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    ent = _entities(ws)
    org = next((o for o in entities_collection(ent, "orgs")
                if isinstance(o, dict) and o.get("id") == org_id), None)
    if org is None:
        return {"status": "skipped", "org_id": org_id, "reason": "error",
                "detail": "org not found"}
    if org.get("relationship_type") == "client":
        return {"status": "skipped", "org_id": org_id,
                "reason": "already_client"}
    # REVIEW DEALNAG1 F-5 — the sweep computes this ONCE and threads it in;
    # a single-org caller (the close_deal hook) still reads it here.
    # CUTB item 3 — an EXPLICIT conversion (the person's own word) is never
    # blocked by a standing undo of the automatic one.
    if not explicit and org_id in (undone_promotions(ws) if undone is None
                                   else undone):
        return {"status": "skipped", "org_id": org_id,
                "reason": "undone_by_user"}
    focus = primary_focus_org(ent)
    if focus is None:
        # Never guess which of the user's orgs the client belongs to (the
        # deal_state rule). Nothing is written; the ask surfaces instead.
        return {"status": "skipped", "org_id": org_id,
                "reason": "no_primary_focus"}

    name = org.get("canonical_name") or org_id
    batch_id = batch_id or _new_batch_id(ws)
    when = f" on {since}" if since else ""
    evidence = ("their deal was marked won" + when if reason == "deal_won"
                else "a paid or signed record" + when)
    res = propose(
        ws,
        kind=PROMOTION_KIND,
        fingerprint=f"org_promotion:{org_id}",
        evidence=evidence,
        # REVIEW DEALNAG1 F-4 — no snooze verb here. An auto row is applied
        # and resolved in the same run, so it is never adjudicated; carrying
        # a 7-day snooze contradicted M's ruling 5 (the standing window is 4
        # days) with a tuple nothing could ever click. The two verbs that
        # remain are the ones the undo path re-opens onto.
        action_tuples=[{"action": "confirm proposal"},
                       {"action": "dismiss proposal"}],
        tier="auto",
        change_class=PROMOTION_CHANGE_CLASS,
        detector=_DETECTOR,
        render_line=f"{name} is now a client ({evidence})",
        org_id=org_id,
        extra={"title": name, "org_name": name, "promotion_reason": reason},
    )
    if res.get("status") != "proposed":
        # An open row or a standing decline cooldown — the human's answer is
        # never steamrolled (the identity_reconcile posture).
        return {"status": "skipped", "org_id": org_id,
                "reason": "proposal_not_open", "detail": res.get("status")}

    import engagement_writer
    import org_writer
    from event_gate import append_event

    existing = engagement_writer.find_existing_engagement(
        ws, from_org_id=focus["id"], to_org_id=org_id)
    prev_label = (existing or {}).get("label")
    prev_active = (existing or {}).get("is_active")
    prev_kind = (existing or {}).get("kind")

    flipped = org_writer.update_org(
        ws, org_id, relationship_type="client", source_skill=source_skill)
    if existing:
        eng = engagement_writer.update_engagement(
            ws, existing["id"], label="Active client", is_active=True,
            kind="client", source_skill=source_skill)
        created = False
    else:
        eng = engagement_writer.create_engagement(
            ws, from_org_id=focus["id"], to_org_id=org_id, kind="client",
            label="Active client", inferred_from=["prospect_converted"],
            source_skill=source_skill)
        created = True

    # The receipt — and the undo anchor. `brain_batch_id` +
    # `brain_change_class` are what `recent_auto_batches` groups on and
    # `_changes_for_brain_batch` reads; the prev_* fields are what the
    # registered reverser puts back.
    try:
        append_event(ws / "_hq" / "data" / "events.jsonl", [{
            "type": "org_promoted",
            "source_skill": source_skill,
            "org_ids": [org_id],
            "data": {
                "org_id": org_id,
                "canonical_name": name,
                "from_relationship_type": "prospect",
                "to_relationship_type": "client",
                "promotion_reason": reason,
                "evidence": evidence,
                "engagement_id": eng.get("id"),
                "engagement_created": created,
                "prev_engagement_label": prev_label,
                "prev_engagement_active": prev_active,
                "prev_engagement_kind": prev_kind,
                "brain_batch_id": batch_id,
                "brain_change_class": PROMOTION_CHANGE_CLASS,
                "auto_predicate": f"paid_or_signed:{reason}",
                # CUTB items 3 + 4 — the deal behind the win, and whether
                # THIS act opened it (the reverser's anchors, item 4).
                **({"deal_thread_id": deal_thread_id} if deal_thread_id else {}),
                **({"won_seq": won_seq} if won_seq is not None else {}),
                **({"deal_manufactured": True} if deal_manufactured else {}),
                **({"explicit": True} if explicit else {}),
            },
        }], holder="org_promotion")
    except Exception as exc:  # noqa: BLE001 — REVIEW DEALNAG1 F-1
        # THE RECEIPT IS THE SAFETY NET, so a promotion that cannot write one
        # must not stand. Without this the org is left a client with no
        # receipt, no CHANGED line and no undoable batch — a permanent,
        # invisible change to the customer's own records, which is exactly
        # the outcome M's ruling depends on being impossible (he accepted
        # acting-without-asking on the strength of reversibility). The
        # rollback runs through the SAME typed writers the registered
        # reverser uses, so a failed promotion and an undone one leave the
        # record in the same shape. The receipt still FOLLOWS the fact
        # (PID1) — writing it first would make the receipt a plan.
        rb = _rollback(ws, org_id, eng, created, prev_label, prev_active,
                       prev_kind, source_skill=source_skill)
        note = (f"promotion rolled back: receipt write failed "
                f"({type(exc).__name__})") if rb["ok"] else (
                   f"promotion FAILED and could not be rolled back: receipt "
                   f"write failed ({type(exc).__name__})")
        resolve_proposal(ws, res["proposal_id"], "skipped",
                         resolved_by=source_skill, source_skill=source_skill,
                         note=note)
        if rb["ok"]:
            print(f"org_promotion: receipt write failed for {org_id}, "
                  f"promotion rolled back: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
        else:
            # N-1 — the loud case. The record is CHANGED, unreceipted, and
            # the rollback could not undo it; say exactly that rather than
            # claiming a recovery that did not happen.
            print(f"org_promotion: PROMOTION LEFT UNRECEIPTED on {org_id} — "
                  f"the receipt write failed ({type(exc).__name__}: {exc}) "
                  f"AND the rollback failed ({'; '.join(rb['errors'])}). The "
                  f"org may still be marked a client with no receipt and no "
                  f"undo; fix by hand.", file=sys.stderr)
        return {"status": "skipped", "org_id": org_id, "reason": "error",
                "detail": f"receipt write failed: {type(exc).__name__}: {exc}",
                "rolled_back": rb["ok"], "rollback_failed": not rb["ok"],
                "rollback_errors": rb["errors"]}
    resolve_proposal(ws, res["proposal_id"], "applied",
                     resolved_by=source_skill, source_skill=source_skill)
    return {"status": "promoted", "org_id": org_id, "name": name,
            "reason": reason, "since": since, "batch_id": batch_id,
            "engagement_id": eng.get("id"), "engagement_created": created,
            "deal_thread_id": deal_thread_id, "won_seq": won_seq,
            "deal_manufactured": bool(deal_manufactured),
            "record": flipped}


def promote_settled_prospects(
    workspace_root,
    *,
    source_skill: str = _DETECTOR,
    org_ids=None,
    batch_id: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict:
    """Promote every settled prospect (one batch, so one `undo` reverses
    the run). Returns {n_promoted, promoted, skipped, batch_id}. Idempotent:
    a second run promotes nothing. Never raises on one bad org — a failure
    is collected as a skip with reason `error`."""
    cands = promotion_candidates(workspace_root, org_ids=org_ids)
    if not cands:
        return {"n_promoted": 0, "promoted": [], "skipped": [],
                "batch_id": None}
    batch_id = batch_id or _new_batch_id(workspace_root)
    undone = undone_promotions(workspace_root)  # F-5: one read per sweep
    promoted, skipped = [], []
    for c in cands:
        try:
            res = promote_org(workspace_root, c["org_id"], reason=c["reason"],
                              since=c["since"], source_skill=source_skill,
                              batch_id=batch_id, now_iso=now_iso,
                              undone=undone)
        except Exception as exc:  # noqa: BLE001 — loud per item, contained
            print(f"org_promotion: {c['org_id']} failed: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            skipped.append({"org_id": c["org_id"], "reason": "error",
                            "detail": f"{type(exc).__name__}: {exc}"})
            continue
        (promoted if res["status"] == "promoted" else skipped).append(res)
    return {"n_promoted": len(promoted), "promoted": promoted,
            "skipped": skipped,
            "batch_id": batch_id if promoted else None}


__all__ = [
    "PROMOTION_CHANGE_CLASS",
    "PROMOTION_KIND",
    "SKIP_REASONS",
    "primary_focus_org",
    "undone_promotions",
    "promotion_candidates",
    "promote_org",
    "promote_settled_prospects",
]


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    for c in promotion_candidates(ws):
        print(f"{c['org_id']:12s} {c['reason']:14s} since {c['since']}")
