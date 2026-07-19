#!/usr/bin/env python3
"""HYG1 Item 1 — reconcile-sent auto-records per-person partial receipts
(the MC1 4.7 wire-up). Extends the MC1/BUG-3719 acceptance surface:

1. Multi-counterparty commitment + a sent message to ONE member → exactly one
   `commitment_partial_received` receipt; the item STAYS OPEN.
2. Second run over the same batch → zero new receipts (idempotent per
   (commitment, counterparty) against the accumulated received_from).
3. Name-only match (no resolved person id) → skipped + reported; an id is
   never guessed from a name token at write time.
4. All members received across runs → `all_counterparties_received` derived
   true, item STILL OPEN, closure PROPOSED (never executed).
5. BUG-3719 self-closure guard: a receipt is never recorded from the same
   message that opened the commitment.
6. Pure layer: a commitment with a partial receipt this run leaves `pending`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import reconcile_sent_commitments as rsc  # noqa: E402
import commitment_parties as cp  # noqa: E402
from cru_match import load_open_commitments, _commitment_id  # noqa: E402
from meeting_capture import build_meeting_commitment_event  # noqa: E402
from event_gate import append_event  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


USER = "person_user"
A, B, C = "person_A", "person_B", "person_C"


def new_ws():
    ws = tempfile.mkdtemp()
    dd = os.path.join(ws, "_hq", "data")
    os.makedirs(dd)
    with open(os.path.join(dd, "entities.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "entities": {"people": [], "orgs": [],
                                              "threads": []},
                   "workspace": {}}, f)
    return ws, os.path.join(dd, "events.jsonl")


def seed_board_commitment(p, *, source_ref="granola:board1"):
    ev = build_meeting_commitment_event(
        "send the board deck to the board", source_ref=source_ref,
        kind="promise", no_due=True, owner_id=USER,
        counterparty_ids=[A, B, C], source_event_seq=1,
        primary_thread_id="project_001", person_ids=[USER],
    )
    append_event(p, [ev])
    return load_open_commitments(p)[0]["data"]["id"]


def receipts_for(p, cid):
    c = [x for x in load_open_commitments(p) if x["data"]["id"] == cid]
    return set(cp.received_from_ids(c[0])) if c else set()


def send_to(person_id, *, mid, ts="2026-01-10T09:00:00"):
    return {"message_id": mid, "ts": ts, "recipient_person_ids": [person_id],
            "subject": "the board deck",
            "body": "here is the board deck I owed you"}


def main():
    print("=== HYG1 Item 1 — auto partial receipts (reconcile-sent) ===\n")

    # --- 1. one member receives -> one receipt, item stays open --------------
    ws, p = new_ws()
    cid = seed_board_commitment(p)
    receipt = rsc.reconcile_and_receipt(
        ws, [send_to(A, mid="m1")], user_person_id=USER,
        source_skill="reconcile-sent")
    check("one partial receipt recorded", receipt["n_partial_receipts"] == 1,
          receipt)
    check("receipt names the counterparty",
          receipt["partial"] and receipt["partial"][0]["counterparty_ids"] == [A],
          receipt.get("partial"))
    check("item NOT whole-closed (n_auto_closed == 0)",
          receipt["n_auto_closed"] == 0, receipt)
    check("item still open", any(_commitment_id(x) == cid
                                 for x in load_open_commitments(p)))
    check("received_from accumulated A", receipts_for(p, cid) == {A})
    check("no closure proposed yet (2 outstanding)",
          receipt["partial_propose_closure"] == [], receipt)
    check("audit event carries n_partial_receipts",
          rsc.validate_reconcile_ran(ws)["ok"] is True)
    with open(p, encoding="utf-8") as f:
        evs = [json.loads(l) for l in f if l.strip()]
    audit = [e for e in evs if e.get("type") == "sent_reconcile"][-1]
    check("sent_reconcile.data.n_partial_receipts == 1",
          audit["data"].get("n_partial_receipts") == 1, audit["data"])
    n_pr_events = sum(1 for e in evs
                      if e.get("type") == "commitment_partial_received")
    check("exactly one commitment_partial_received event", n_pr_events == 1)

    # --- 2. re-run the same batch -> zero new receipts (idempotent) ----------
    receipt2 = rsc.reconcile_and_receipt(
        ws, [send_to(A, mid="m1")], user_person_id=USER,
        source_skill="reconcile-sent")
    check("re-run records zero new receipts",
          receipt2["n_partial_receipts"] == 0, receipt2)
    with open(p, encoding="utf-8") as f:
        n_pr_events = sum(1 for l in f if '"commitment_partial_received"' in l)
    check("still exactly one receipt event after re-run", n_pr_events == 1)

    # --- 4. remaining members across runs -> propose closure, still open -----
    receipt3 = rsc.reconcile_and_receipt(
        ws, [send_to(B, mid="m2", ts="2026-01-11T09:00:00"),
             send_to(C, mid="m3", ts="2026-01-11T10:00:00")],
        user_person_id=USER, source_skill="reconcile-sent")
    check("two more receipts recorded", receipt3["n_partial_receipts"] == 2,
          receipt3)
    check("roster complete -> closure PROPOSED",
          len(receipt3["partial_propose_closure"]) == 1, receipt3)
    opens = [x for x in load_open_commitments(p) if _commitment_id(x) == cid]
    check("item STILL OPEN after full roster (propose, never auto-close)",
          len(opens) == 1)
    check("all_counterparties_received derived true",
          opens and opens[0]["data"].get("all_counterparties_received") is True,
          opens and opens[0]["data"])
    check("summary offers the close in plain words",
          "close it when ready" in receipt3["summary"], receipt3["summary"])

    # --- 3. name-only match -> skipped + reported, nothing written -----------
    # A CONFIRMED item with MIXED counterparties (one resolved id + one
    # free-text name) is the real shape here: a name-only capture is stamped
    # pending_review by the builder and never reaches the partial path at all
    # (the pending_review demotion runs first) — that shape stays in the
    # confirm flow, correctly. The mixed shape is where a name-token match
    # with no id must be skipped-and-reported, never guessed.
    ws2, p2 = new_ws()
    append_event(p2, [{
        "type": "commitment", "source_skill": "meeting-notes",
        "primary_thread_id": "project_001", "person_ids": [USER, A],
        "data": {"id": "cmt_hyg1_mixed", "kind": "promise", "status": "open",
                 "title": "send the launch recap to Ann Smith and the team",
                 "owner_id": USER, "no_due": True,
                 "source_ref": "granola:launch1",
                 "counterparty_id": A, "counterparty_ids": [A],
                 "counterparty_name": "Ann Smith",
                 "counterparty_names": ["Ann Smith"]},
    }])
    r = rsc.reconcile_and_receipt(
        ws2, [{"message_id": "m9", "ts": "2026-01-10T09:00:00",
               "recipient_person_ids": [], "recipient_names": ["Ann Smith", "ann"],
               "subject": "the launch recap",
               "body": "Ann Smith — here is the launch recap I owed you"}],
        user_person_id=USER, source_skill="reconcile-sent")
    check("name-only match writes NO receipt", r["n_partial_receipts"] == 0, r)
    check("name-only match is reported, not silently dropped",
          any(s["name"] == "Ann Smith" for s in r["partial_skipped_names"]),
          r["partial_skipped_names"])
    with open(p2, encoding="utf-8") as f:
        check("no commitment_partial_received event on the name-only path",
              not any('"commitment_partial_received"' in l for l in f))

    # --- 5. self-closure guard: origin message never records its own receipt -
    ws3, p3 = new_ws()
    ev = build_meeting_commitment_event(
        "send the board deck to the board", source_ref="gmail:origin1",
        kind="promise", no_due=True, owner_id=USER,
        counterparty_ids=[A, B], source_event_seq=1,
        primary_thread_id="project_001", person_ids=[USER],
    )
    append_event(p3, [ev])
    r = rsc.reconcile_and_receipt(
        ws3, [send_to(A, mid="origin1")],  # the SAME message that opened it
        user_person_id=USER, source_skill="reconcile-sent")
    check("self-closure guard: origin message records no receipt",
          r["n_partial_receipts"] == 0, r)

    # --- 6. pure layer: partial excludes the cid from pending ----------------
    opens_pure = load_open_commitments(p3)
    res = rsc.reconcile_sent(opens_pure, [send_to(A, mid="fresh1")],
                             user_person_id=USER)
    check("pure: partial recommendation carried",
          len(res["partial"]) == 1 and
          res["partial"][0]["receipts"][0]["counterparty_id"] == A, res)
    pending_ids = {x["commitment_id"] for x in res["pending"]}
    check("pure: cid with a partial receipt leaves pending",
          res["partial"][0]["commitment_id"] not in pending_ids)
    check("pure: nothing whole-closes", res["auto_close"] == [])

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
