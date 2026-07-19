#!/usr/bin/env python3
"""Phase 2 Stage E acceptance test — F5 extraction receipts + pending-band
recall.

Pins:
1. **Counterparty receipts feed the candidacy gate.** A commitment whose
   extractor linked `counterparty_id` (or free-text `counterparty_name`)
   matches an outbound send even when person_ids is bare and the title names
   nobody — the structural fix for the Bug #103 recall class (live yield was
   4 closes / 644 scanned). Matching THRESHOLDS are untouched: the receipt
   opens candidacy; the title score still decides.
2. **The 0.30–0.55 pending band does not evaporate.** reconcile-sent persists
   each pending proposal as a `commitment_review_proposed` event (deduped
   against the open-proposal set); the Commitments chat surfaces them for
   one-click confirm/deny through close_commitment(). Cursor mechanics and
   reconcile-sent's isolation are untouched.
3. **pending_review honored end-to-end** — the confirm click closes a flagged
   commitment ONLY because it is an explicit user action.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from commitment_state import close_commitment  # noqa: E402
from cru_match import (  # noqa: E402
    build_commitment_review_dismissed_event,
    load_open_commitments,
    load_open_review_proposals,
    match_send_to_commitments,
)
from reconcile_sent_commitments import reconcile_and_receipt  # noqa: E402

USER = "person_user"

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def make_workspace(events):
    ws = tempfile.mkdtemp()
    data_dir = Path(ws) / "_hq" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "entities.json").write_text(json.dumps({
        "workspace": {"user_person_id": USER},
        "people": [{"id": USER, "canonical_name": "Test User",
                    "is_primary_user": True}],
    }), encoding="utf-8")
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return ws


def events_path(ws):
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def read_events(ws):
    return [json.loads(l) for l in events_path(ws).read_text(encoding="utf-8").splitlines() if l.strip()]


def commitment(seq, cid, title, *, kind="promise", ts="2026-06-20T10:00:00Z",
               person_ids=None, **extra):
    data = {"id": cid, "title": title, "owner_id": USER, "status": "open",
            "kind": kind}
    data.update(extra)
    ev = {"seq": seq, "ts": ts, "type": "commitment",
          "source_skill": "meeting-notes", "primary_thread_id": f"t{seq}",
          "data": data}
    if person_ids is not None:
        ev["person_ids"] = person_ids
    return ev


def main():
    print("=== Stage E — F5 extraction receipts + pending-band recall ===\n")

    # ------------------------------------------------------------------
    print("[1] counterparty receipts open the candidacy gate (Bug #103 fix)")
    # ------------------------------------------------------------------
    # Title carries the deliverable but NOT the recipient's name; person_ids
    # is bare. Pre-Stage-E this commitment was invisible to the matcher.
    with_id = commitment(1, "cmt_ID", "send over the margin analysis",
                         person_ids=[USER], counterparty_id="person_bob")
    with_name = commitment(2, "cmt_NAME", "send over the vendor summary",
                           person_ids=[USER], counterparty_name="Jordan Lee")
    bare = commitment(3, "cmt_BARE", "send over the board deck",
                      person_ids=[USER])

    results = match_send_to_commitments(
        open_commitments=[with_id, with_name, bare],
        sender_person_id=USER,
        recipient_person_ids=["person_bob"],
        subject="margin analysis",
        body="here's the margin analysis, sending it over now",
    )
    matched = {r["commitment_id"] for r in results}
    check("counterparty_id receipt makes the commitment a candidate",
          "cmt_ID" in matched, f"{matched}")
    check("no-receipt commitment with no title-token stays invisible (fallback unchanged)",
          "cmt_BARE" not in matched and "cmt_NAME" not in matched, f"{matched}")

    results = match_send_to_commitments(
        open_commitments=[with_id, with_name, bare],
        sender_person_id=USER,
        recipient_person_ids=[],           # unresolvable recipient — no id
        recipient_names=["Jordan Lee", "jlee"],
        subject="vendor summary",
        body="sending over the vendor summary as promised",
    )
    matched = {r["commitment_id"] for r in results}
    check("counterparty_name receipt matches a named-but-unresolved recipient",
          "cmt_NAME" in matched, f"{matched}")

    # Thresholds untouched: candidacy is open but the SCORE still decides.
    weak = match_send_to_commitments(
        open_commitments=[with_id],
        sender_person_id=USER,
        recipient_person_ids=["person_bob"],
        subject="lunch",
        body="want to grab lunch thursday?",
    )
    check("receipt opens candidacy only — an unrelated send still scores no_action",
          all(r["recommendation"] == "no_action" for r in weak), f"{weak}")

    # ------------------------------------------------------------------
    print("\n[2] reconcile-sent persists AMBIGUOUS pending matches (FS-11)")
    # ------------------------------------------------------------------
    # FS-11 (M ruling 2026-07-15): an UNAMBIGUOUS moderate match now
    # auto-closes; only MULTI-CANDIDATE AMBIGUITY (one send matching >1 open
    # commitment at moderate grade) stays a confirm proposal. Two near-duplicate
    # commitments → one send lands both in the pending band.
    ws = make_workspace([
        commitment(1, "cmt_AMBIG_A", "send sam the quarterly pricing recap deck",
                   person_ids=[USER, "person_sam"]),
        commitment(2, "cmt_AMBIG_B", "send sam the quarterly pricing summary recap",
                   person_ids=[USER, "person_sam"]),
    ])
    receipt = reconcile_and_receipt(
        ws,
        [{"message_id": "m1", "ts": "2026-07-01T09:00:00Z",
          "recipient_person_ids": ["person_sam"],
          "subject": "quick note",
          "body": "sam — attached the recap, thoughts welcome on budget timing agenda notes"}],
        user_person_id=USER,
        source_skill="reconcile-sent",
    )
    check("the ambiguous matches landed in the pending band (fixture sanity)",
          receipt["n_pending"] == 2 and receipt["n_auto_closed"] == 0,
          f"{receipt}")
    proposals = load_open_review_proposals(events_path(ws))
    check("ambiguous pending proposals PERSISTED as commitment_review_proposed w/ TTL",
          receipt["reviews_written"] == 2 and len(proposals) == 2
          and all(0.30 <= p["data"]["match_score"] < 0.55 for p in proposals)
          and all(p["data"].get("ttl_days") for p in proposals),
          f"reviews={receipt.get('reviews_written')} proposals={proposals}")
    check("ambiguous commitments stay OPEN (only unambiguous moderate auto-closes)",
          len(load_open_commitments(events_path(ws))) == 2)
    check("cursor advanced normally (mechanics untouched)",
          receipt["cursor_advanced"] and receipt["cursor_after"] == "2026-07-01T09:00:00Z")
    check("sent_reconcile audit event still written (isolation untouched)",
          any(e["type"] == "sent_reconcile" for e in read_events(ws)))

    # Re-run with a NEW send matching the same commitments → deduped.
    receipt2 = reconcile_and_receipt(
        ws,
        [{"message_id": "m2", "ts": "2026-07-01T10:00:00Z",
          "recipient_person_ids": ["person_sam"],
          "subject": "quick note again",
          "body": "sam — attached the recap once more, thoughts welcome on budget timing agenda notes"}],
        user_person_id=USER,
        source_skill="reconcile-sent",
    )
    check("open proposals deduped — no second review events for the same commitments",
          receipt2["reviews_written"] == 0
          and len(load_open_review_proposals(events_path(ws))) == 2,
          f"{receipt2}")

    # ------------------------------------------------------------------
    print("\n[2b] FS-11: an UNAMBIGUOUS moderate match auto-closes")
    # ------------------------------------------------------------------
    ws_auto = make_workspace([
        commitment(1, "cmt_UNAMBIG", "send sam the quarterly pricing recap deck",
                   person_ids=[USER, "person_sam"]),
    ])
    r_auto = reconcile_and_receipt(
        ws_auto,
        [{"message_id": "ma", "ts": "2026-07-01T09:00:00Z",
          "recipient_person_ids": ["person_sam"],
          "subject": "quick note",
          "body": "sam — attached the recap, thoughts welcome on budget timing agenda notes"}],
        user_person_id=USER,
        source_skill="reconcile-sent",
    )
    check("unambiguous moderate match auto-closes (M ruling: just close them)",
          r_auto["n_auto_closed"] == 1 and r_auto["n_pending"] == 0
          and load_open_commitments(events_path(ws_auto)) == [],
          f"{r_auto}")

    # ------------------------------------------------------------------
    print("\n[3] one-click confirm/deny through THE closure path")
    # ------------------------------------------------------------------
    # confirm → close_commitment with user_confirmed=True (works even on a
    # pending_review-flagged commitment — the click is the confirmation).
    p = load_open_review_proposals(events_path(ws))[0]
    closed_cid = p["data"]["commitment_id"]
    res = close_commitment(
        ws, closed_cid,
        resolved_by=USER,
        evidence=p["data"].get("evidence") or "confirmed from review",
        source_skill="commitments", user_confirmed=True,
    )
    open_after = {c["data"]["id"] for c in load_open_commitments(events_path(ws))}
    check("confirm closes the clicked commitment via close_commitment",
          res["status"] == "closed" and closed_cid not in open_after)
    remaining = load_open_review_proposals(events_path(ws))
    check("closing retires ONLY the clicked proposal (the other ambiguous one stays)",
          all(rp["data"]["commitment_id"] != closed_cid for rp in remaining)
          and len(remaining) == 1)

    # deny → commitment_review_dismissed; commitment stays open.
    ws = make_workspace([
        commitment(1, "cmt_DENY", "send quinn the audit checklist",
                   person_ids=[USER, "person_quinn"], pending_review=True),
        {"seq": 2, "ts": "2026-07-01T09:00:00Z", "type": "commitment_review_proposed",
         "source_skill": "reconcile-sent", "primary_thread_id": "t1",
         "data": {"commitment_id": "cmt_DENY", "proposed_resolution": "auto_resolve",
                  "match_score": 0.41, "evidence": "matched your sent message"}},
    ])
    from atomic_write import atomic_append_jsonl
    atomic_append_jsonl(events_path(ws), [build_commitment_review_dismissed_event(
        commitment_id="cmt_DENY", primary_thread_id="t1",
        source_skill="commitments", next_seq=3,
    )])
    check("deny retires the proposal; the commitment STAYS open",
          load_open_review_proposals(events_path(ws)) == []
          and len(load_open_commitments(events_path(ws))) == 1)

    # And the auto path STILL refuses the flagged commitment (F2 floor).
    try:
        close_commitment(ws, "cmt_DENY", resolved_by="sent_reconcile",
                         evidence="auto", source_skill="reconcile-sent")
        check("auto path still refuses pending_review", False)
    except Exception as e:
        check("auto path still refuses pending_review",
              type(e).__name__ == "PendingReviewError", type(e).__name__)

    # ------------------------------------------------------------------
    print("\n[4] source gates — receipts in producers, review surface wired")
    # ------------------------------------------------------------------
    def read(rel):
        return open(os.path.join(PLUGIN_ROOT, rel), encoding="utf-8").read()

    for rel in ("skills/meeting-notes/SKILL.md",
                "skills/scan-for-commitments/SKILL.md",
                "skills/inbox-triage/SKILL.md"):
        text = read(rel)
        check(f"{rel.split('/')[1]}: counterparty receipts + requester retirement",
              "counterparty_id" in text and "counterparty_name" in text
              and ("requester_id" in text or "requester" in text))

    orch = read("skills/enable-command-room-schedules/references/orchestrator-commitments.md")
    check("Commitments chat carries the Stage E review section (confirm → close_commitment)",
          "Phase 3.6" in orch and "load_open_review_proposals" in orch
          and "user_confirmed=True" in orch)
    pulse = read("skills/enable-command-room-schedules/references/orchestrator-dont-forget.md")
    check("Pulse review confirm migrated to close_commitment",
          "close_commitment" in pulse)
    schema = read("shared/COMMITMENT_SCHEMA.md")
    check("COMMITMENT_SCHEMA documents counterparty_id / counterparty_name receipts",
          "counterparty_id" in schema and "counterparty_name" in schema
          and "Bug #103" in schema)
    rec = read("shared/scripts/reconcile_sent_commitments.py")
    check("reconcile-sent persists the pending band (code)",
          "commitment_review_proposed" in rec and "load_open_review_proposals" in rec)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
