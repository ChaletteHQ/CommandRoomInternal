#!/usr/bin/env python3
"""T2.2 scope 4 — FS-18: audit outcomes reflect the handler's actual write
result, and the deal-creation existence predicate is ONE shared helper.

  a. apply_audit.build_apply_choices_applied_event derives outcome from the
     handler's return/raise — a refusing handler is NEVER "ok" (the RV-5
     false-success), no-ops count apart, unknown statuses are never
     optimistic.
  b. deal_state.org_deal_coverage treats an ACTIVE ENGAGEMENT THREAD as
     coverage, and deal_signal_detector consults the SAME helper — an org
     with an active engagement gets NO deal_creation proposal (the
     unconfirmable-zombie class).

House convention: non-zero exit = fail.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label)
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    # ---- a. FS-18a: outcome derivation ---------------------------------------
    from apply_audit import build_apply_choices_applied_event, derive_outcome
    from deal_state import DealStateError

    # The refusing-handler fixture: the exact RV-5 shape — a deal_creation
    # confirm whose writer refused (raised) while a sibling action landed.
    refused = DealStateError(
        "thread already exists: project_002 (Acme Co)")
    ev = build_apply_choices_applied_event(
        source="cr-brain",
        actions=[
            {"n": "bp_aaaaaaaaaaaa", "action": "confirm proposal",
             "handler_result": refused},
            {"n": "bp_bbbbbbbbbbbb", "action": "dismiss proposal",
             "handler_result": {"status": "resolved",
                                "proposal_id": "bp_bbbbbbbbbbbb"}},
            {"n": "commitment_seq_9", "action": "resolved",
             "handler_result": {"status": "already_resolved"}},
        ])
    rows = {r["n"]: r["outcome"] for r in ev["data"]["actions"]}
    check("refusing handler audits as error — NEVER ok (FS-18a)",
          rows["bp_aaaaaaaaaaaa"] == "error", str(rows))
    check("landed write audits ok", rows["bp_bbbbbbbbbbbb"] == "ok")
    check("idempotent no-op audits already_resolved (not ok, not error)",
          rows["commitment_seq_9"] == "already_resolved")
    check("n_errors counts the refusal", ev["data"]["n_errors"] == 1)
    check("event shape: type + source + n_choices",
          ev["type"] == "apply_choices_applied"
          and ev["data"]["source"] == "cr-brain"
          and ev["data"]["n_choices"] == 3)
    check("builder omits seq/ts (gate stamps them)",
          "seq" not in ev and "ts" not in ev)
    # Never-optimistic mapping details.
    check("needs_confirm is not a success",
          derive_outcome({"status": "needs_confirm"}) == "error")
    check("refused status is not a success",
          derive_outcome({"status": "refused"}) == "error")
    check("UNKNOWN status is never optimistic",
          derive_outcome({"status": "some_future_status"}) == "error")
    check("bare string result reads as an error message",
          derive_outcome("boom") == "error")
    check("duplicate_open counts as a no-op",
          derive_outcome({"status": "duplicate_open"}) == "already_resolved")
    # Review F-1: an entry that OMITS handler_result entirely (the reporter
    # never captured what the handler did) must audit "error" — only an
    # EXPLICIT None (pure no-write ack) is "ok".
    ev_f1 = build_apply_choices_applied_event(
        source="cr-brain",
        actions=[
            {"n": "1", "action": "confirm proposal"},            # absent key
            {"n": "2", "action": "skip", "handler_result": None},  # explicit None
        ])
    f1 = {r["n"]: r["outcome"] for r in ev_f1["data"]["actions"]}
    check("ABSENT handler_result audits as error (F-1)", f1["1"] == "error",
          str(f1))
    check("explicit None stays ok (pure no-write ack)", f1["2"] == "ok")
    check("F-1 absent key counts in n_errors", ev_f1["data"]["n_errors"] == 1)

    # ---- b. FS-18b: shared coverage predicate --------------------------------
    from deal_state import org_deal_coverage

    threads = [
        # org_A: active ENGAGEMENT thread (non-deal) — covered (RV-5 ruling)
        {"id": "project_002", "kind": "engagement", "status": "active",
         "org": "org_A", "display_name": "Acme Co build"},
        # org_B: open DEAL thread — covered
        {"id": "project_010", "kind": "deal", "status": "active",
         "org_id": "org_B", "deal": {"stage": "lead"}},
        # org_C: archived thread only — NOT covered
        {"id": "project_011", "kind": "engagement", "status": "archived",
         "affiliation_id": "org_C"},
        # org_D: terminal deal only — NOT covered
        {"id": "project_012", "kind": "deal", "status": "active",
         "org": "org_D", "deal": {"stage": "negotiating", "outcome": "lost"}},
    ]
    check("active engagement thread IS coverage",
          (org_deal_coverage(threads, "org_A") or {}).get("id") == "project_002")
    check("open deal thread IS coverage",
          (org_deal_coverage(threads, "org_B") or {}).get("id") == "project_010")
    check("archived thread is NOT coverage",
          org_deal_coverage(threads, "org_C") is None)
    check("terminal deal is NOT coverage",
          org_deal_coverage(threads, "org_D") is None)
    check("unknown org is NOT coverage",
          org_deal_coverage(threads, "org_Z") is None)

    # Detector alignment: same events, three orgs — creation proposed ONLY
    # for the truly uncovered one.
    from deal_signal_detector import detect_deal_signals

    ws = Path(tempfile.mkdtemp())
    d = ws / "_hq" / "data"
    d.mkdir(parents=True)
    now = _dt.datetime.now(_dt.timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    entities = {"entities": {
        "people": [],
        "orgs": [
            {"id": "org_A", "canonical_name": "Acme Co",
             "relationship_type": "prospect"},
            {"id": "org_C", "canonical_name": "Sample Hardware",
             "relationship_type": "prospect"},
        ],
        "threads": [
            {"id": "project_002", "kind": "engagement", "status": "active",
             "org": "org_A", "display_name": "Acme Co build"},
        ],
    }}
    (d / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    evs = [
        {"seq": 1, "type": "note", "ts": ts, "org_ids": ["org_A"],
         "data": {"summary": "sent them the proposal and pricing"}},
        {"seq": 2, "type": "note", "ts": ts, "org_ids": ["org_C"],
         "data": {"summary": "sent them the proposal and pricing"}},
    ]
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")
    cands = detect_deal_signals(ws)
    creations = {c["org_id"] for c in cands if c["kind"] == "deal_creation"}
    check("detector proposes creation for the UNCOVERED org",
          "org_C" in creations, str(cands))
    check("detector proposes NO creation for the engagement-covered org "
          "(FS-18b alignment)", "org_A" not in creations, str(creations))

    # Review F-4: the covered→self-heal branch resolves `superseded` (the
    # user clicked Confirm — "declined" would be an untruthful ledger row),
    # with the SAME 60d cooldown semantics as declined.
    import brain_proposals
    from proposal_ledger import active_cooldowns, load_rows

    res = brain_proposals.propose(
        ws, kind="deal_creation", fingerprint="deal:org_C:creation",
        evidence="proposal language", tier="confirm", detector="deal-signals",
        action_tuples=[{"action": "confirm proposal"},
                       {"action": "dismiss proposal"},
                       {"action": "snooze proposal 7d"}],
        org_id="org_C")
    check("fixture proposal emitted", res["status"] == "proposed")
    out = brain_proposals.resolve_proposal(
        ws, res["proposal_id"], "superseded",
        resolved_by="person_001", source_skill="apply-choices",
        note="already covered by Acme Co build")
    check("superseded is a legal resolution (F-4)",
          out["status"] == "resolved" and out["user_action"] == "superseded",
          str(out))
    row = load_rows(ws, "deal-signals")[-1]
    check("ledger row says superseded, not declined",
          row.get("user_action") == "superseded", str(row))
    now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    check("superseded fingerprint cools down 60d exactly like declined",
          "deal:org_C:creation" in active_cooldowns(ws, "deal-signals",
                                                    now_iso=now_iso))
    still_open = [p for p in brain_proposals.load_open_proposals(ws)
                  if p["id"] == res["proposal_id"]]
    check("superseded proposal is tombstoned out of the queue",
          not still_open)
    check("re-propose of the superseded fingerprint is suppressed",
          brain_proposals.propose(
              ws, kind="deal_creation", fingerprint="deal:org_C:creation",
              evidence="proposal language", tier="confirm",
              detector="deal-signals",
              action_tuples=[{"action": "confirm proposal"}],
              org_id="org_C")["status"] == "suppressed_cooldown")

    if failures:
        print(f"\nFS-18 FAIL — {len(failures)} of {checks}")
        return 1
    print(f"FS-18 outcome + coverage: {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
