#!/usr/bin/env python3
"""Unit test for reconcile_sent_commitments (Bug #85 layer 1 — stale resolution).

Asserts the Sent-mail reconciliation core:
  - a strong subject/body match on a you-owe commitment → auto_close (HIGH);
  - a weak-but-nonzero match → pending (MEDIUM), never auto-closed;
  - a commitment the user does NOT own is never closed by their send;
  - each commitment appears at most once (best send wins);
  - the cursor advances to the max message ts;
  - to_resolved_events() builds canonical commitment_resolved events whose
    data.commitment_id round-trips through load_open_commitments (i.e. the
    closer actually closes the original).
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import reconcile_sent_commitments as rsc  # noqa: E402
from cru_match import _commitment_id, match_send_to_commitments  # noqa: E402

USER = "person_user"
BOB = "person_bob"

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


def commitment(seq, owner, title, recipients):
    return {
        "seq": seq,
        "type": "commitment",
        "primary_thread_id": "thread_x",
        "person_ids": recipients,
        "data": {"id": f"c{seq}", "owner_id": owner, "title": title, "status": "open"},
    }


def main():
    print("=== reconcile_sent_commitments (Bug #85) ===\n")

    # Two you-owe commitments to Bob + one Bob-owes-user (should never close on a user send).
    opens = [
        commitment(1, USER, "Send Bob the Q2 financial review deck", [BOB]),
        commitment(2, USER, "Schedule the onboarding call with the new vendor", [BOB]),
        commitment(3, BOB, "Bob to send the signed contract back", [BOB]),
    ]

    # A send that strongly matches commitment #1.
    sent = [{
        "message_id": "m1",
        "ts": "2026-05-30T09:00:00",
        "recipient_person_ids": [BOB],
        "subject": "Q2 financial review deck",
        "body": "Bob, here is the Q2 financial review deck I owed you. Sending it now.",
    }]

    res = rsc.reconcile_sent(opens, sent, user_person_id=USER)
    auto_ids = {c["commitment_id"] for c in res["auto_close"]}
    pending_ids = {c["commitment_id"] for c in res["pending"]}

    print("[1] strong match on a you-owe commitment auto-closes")
    check("commitment #1 is in auto_close", _commitment_id(opens[0]) in auto_ids, res["auto_close"])

    print("\n[2] the Bob-owes-user commitment is never closed by the user's send")
    check("commitment #3 NOT closed", _commitment_id(opens[2]) not in (auto_ids | pending_ids))

    print("\n[3] unrelated you-owe commitment isn't force-closed")
    check("commitment #2 not auto-closed by an unrelated send",
          _commitment_id(opens[1]) not in auto_ids)

    print("\n[4] cursor advances to the max message ts")
    check("cursor_ts == latest send ts", res["cursor_ts"] == "2026-05-30T09:00:00", res["cursor_ts"])

    print("\n[5] empty inputs are safe no-ops")
    empty = rsc.reconcile_sent([], [], user_person_id=USER)
    check("empty → no closures", empty["auto_close"] == [] and empty["pending"] == [])
    nouser = rsc.reconcile_sent(opens, sent, user_person_id="")
    check("missing user_person_id → no closures", nouser["auto_close"] == [])

    print("\n[6] dedup — two sends matching the same commitment yield one closure")
    sent2 = sent + [{
        "message_id": "m2",
        "ts": "2026-05-31T10:00:00",
        "recipient_person_ids": [BOB],
        "subject": "Q2 financial review deck (resend)",
        "body": "Resending the Q2 financial review deck.",
    }]
    res2 = rsc.reconcile_sent(opens, sent2, user_person_id=USER)
    cnt = sum(1 for c in res2["auto_close"] if c["commitment_id"] == _commitment_id(opens[0]))
    check("commitment #1 appears exactly once in auto_close", cnt == 1, f"count={cnt}")
    check("cursor advanced to the newer send", res2["cursor_ts"] == "2026-05-31T10:00:00", res2["cursor_ts"])

    print("\n[7] to_resolved_events builds canonical closers that round-trip")
    events = rsc.to_resolved_events(res["auto_close"], source_skill="morning-briefing", seq_start=500)
    check("one resolved event per closure", len(events) == len(res["auto_close"]))
    if events:
        ev = events[0]
        check("event type is commitment_resolved", ev.get("type") == "commitment_resolved", ev)
        check("event carries data.commitment_id matching the original",
              ev["data"]["commitment_id"] == _commitment_id(opens[0]), ev["data"])
        check("seqs are assigned from seq_start", ev["seq"] == 500, ev.get("seq"))
        check("resolved_by tags the sent-reconcile path",
              ev["data"]["resolved_by"] == "sent_reconcile", ev["data"])

    # Round-trip: the closer should drop the original from the open set.
    from cru_match import load_open_commitments  # noqa
    import json, tempfile
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for ev in opens:
            f.write(json.dumps(ev) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    remaining = {_commitment_id(c) for c in load_open_commitments(path)}
    os.unlink(path)
    check("auto-closed commitment #1 no longer in the open set",
          _commitment_id(opens[0]) not in remaining, remaining)
    check("untouched commitments #2/#3 remain open",
          _commitment_id(opens[1]) in remaining and _commitment_id(opens[2]) in remaining, remaining)

    test_self_closure_guard_cross_format()
    test_reconcile_and_receipt()
    test_audit_event_and_validator()
    test_recipient_name_recall_fallback()

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


def test_self_closure_guard_cross_format():
    """R16 HARD INVARIANT (BUG-3719): a message must never close the commitment
    it opened — ACROSS provenance formats. The guard keys on the canonical
    dedup key, so a legacy `gmail:<Id>` source_ref (any case) and a structured
    provenance re-observation of the SAME message reduce to one identity.
    Written BEFORE the guard was migrated off the byte-compare (which missed
    case drift and structured provenance)."""
    print("\n[8] self-closure guard holds across old/new provenance formats")
    body = "Bob, sending the Q2 financial review deck I owed you."
    subj = "Q2 financial review deck"

    # (a) LEGACY spelling with case drift: opened with gmail:MsgCASE, the same
    # message re-fetched with a lowercased id. Byte-compare misses this.
    c_legacy = commitment(11, USER, "Send Bob the Q2 financial review deck", [BOB])
    c_legacy["data"]["source_ref"] = "gmail:MsgCASE"
    res = rsc.reconcile_sent([c_legacy], [{
        "message_id": "msgcase", "ts": "2026-06-01T09:00:00",
        "recipient_person_ids": [BOB], "subject": subj, "body": body,
    }], user_person_id=USER)
    check("legacy source_ref (case-drifted) never closes its own promise",
          res["auto_close"] == [] and res["pending"] == [],
          str(res["auto_close"] or res["pending"]))

    # (b) STRUCTURED provenance on the commitment (new writers): same message
    # observed via message_id. Must also be excluded.
    c_struct = commitment(12, USER, "Send Bob the Q2 financial review deck", [BOB])
    c_struct["data"]["provenance"] = {"provider": "gmail", "native_id": "MsgCASE"}
    res2 = rsc.reconcile_sent([c_struct], [{
        "message_id": "MSGCASE", "ts": "2026-06-01T09:05:00",
        "recipient_person_ids": [BOB], "subject": subj, "body": body,
    }], user_person_id=USER)
    check("structured-provenance commitment never closed by its own message",
          res2["auto_close"] == [] and res2["pending"] == [],
          str(res2["auto_close"] or res2["pending"]))

    # (c) A DIFFERENT message must still close it (the guard must not
    # over-exclude and freeze the commitment open forever).
    res3 = rsc.reconcile_sent([c_legacy], [{
        "message_id": "otherMsg", "ts": "2026-06-02T09:00:00",
        "recipient_person_ids": [BOB], "subject": subj, "body": body,
    }], user_person_id=USER)
    check("a different message still closes the commitment",
          len(res3["auto_close"]) == 1, str(res3))


def test_recipient_name_recall_fallback():
    """Bug #103: the matcher missed real email completions because the recipient
    gate required a resolved recipient person_id to be in the commitment's
    person_ids — but extraction often doesn't link the counterparty (Bob/Jake had
    only the user) or the counterparty has no email (Don). The fix matches the
    recipient NAME against the commitment title. These fixtures mirror the real
    shapes found on M's workspace."""
    print("\n[10] recipient-name recall fallback (Bug #103) — real shapes")
    # Counterparty NOT linked into person_ids (only the user).
    c_jordan = commitment(1, USER, "Send Jordan Lee a product summary", [USER])
    # Counterparty linked but the person has no email -> id can't resolve from the send.
    c_sam = commitment(2, USER, "Send Sam a recap email", [USER, "person_sam"])
    opens = [c_jordan, c_sam]

    # OLD behavior (no recipient_names) — both miss.
    old_jordan = match_send_to_commitments(
        open_commitments=opens, sender_person_id=USER,
        recipient_person_ids=["person_jordan"],  # resolved but NOT in person_ids
        subject="Command Room product summary", body="how it's built and how it works")
    check("without recipient_names, an unlinked counterparty MISSES (the #103 bug)",
          all(r["recommendation"] == "no_action" for r in old_jordan), old_jordan)

    # NEW behavior — recipient name token in the title opens the gate.
    new_jordan = match_send_to_commitments(
        open_commitments=opens, sender_person_id=USER,
        recipient_person_ids=["person_jordan"],
        recipient_names=["Jordan Lee", "jlee"],
        subject="Command Room product summary", body="how it's built and how it works")
    hit = [r for r in new_jordan if r["recommendation"] != "no_action"]
    check("with recipient_names, 'Send Jordan Lee…' matches via title fallback",
          any("Jordan Lee" in r["title"] for r in hit), new_jordan)

    new_sam = match_send_to_commitments(
        open_commitments=opens, sender_person_id=USER,
        recipient_person_ids=[],  # Sam has no email — can't resolve to an id
        recipient_names=["Sam", "sam"],
        subject="Recap from our call", body="Sam, here's the recap I owed you.")
    check("with recipient_names, 'Send Sam a recap' matches even with NO resolved id",
          any("Send Sam" in r["title"] and r["recommendation"] != "no_action" for r in new_sam),
          new_sam)

    # Guard against over-matching: an unrelated recipient name must NOT match a
    # commitment whose title doesn't contain it.
    none_match = match_send_to_commitments(
        open_commitments=[commitment(3, USER, "Send the Q2 board deck to the directors", [USER])],
        sender_person_id=USER, recipient_person_ids=[], recipient_names=["Zephyr", "zephyr"],
        subject="hello", body="unrelated")
    check("a recipient name NOT in the title does not force a match (no false positive)",
          all(r["recommendation"] == "no_action" for r in none_match), none_match)


def test_audit_event_and_validator():
    """Bug #98-v3: every reconcile run emits a sent_reconcile AUDIT event, and
    validate_reconcile_ran reads it back. This is the ungameable enforcement —
    a printed 'reconciled' sentence with no audit event fails validation, which
    is exactly the hole the v3.18.9 narration-gate had (the model gamed it by
    feeding curated data + printing a truthful line)."""
    import json
    print("\n[9] sent_reconcile AUDIT event + validate_reconcile_ran (Bug #98-v3)")
    opens = [commitment(1, USER, "Send Bob the Q2 deck", [BOB])]
    sent = [{"message_id": "m1", "ts": "2026-05-30T09:00:00", "recipient_person_ids": [BOB],
             "subject": "Q2 deck", "body": "Here is the Q2 deck I owed you."}]
    root = _build_ws(opens, cursor="2026-05-01T00:00:00")
    ev_path = os.path.join(root, "_hq", "data", "events.jsonl")

    # BEFORE any run: validator must say it did NOT run (no audit event).
    v0 = rsc.validate_reconcile_ran(root)
    check("no run yet -> validator ok=False (can't fake 'ran')", v0["ok"] is False, v0)

    rsc.reconcile_and_receipt(root, sent, user_person_id=USER, source_skill="reconcile-sent")

    rows = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
    audits = [r for r in rows if r.get("type") == "sent_reconcile"]
    check("exactly one sent_reconcile audit event written", len(audits) == 1, audits)
    d = audits[0].get("data") or {}
    check("audit carries cursor_from / cursor_to / sent_scanned_count",
          d.get("cursor_from") == "2026-05-01T00:00:00"
          and d.get("cursor_to") == "2026-05-30T09:00:00"
          and d.get("sent_scanned_count") == 1, d)

    # AFTER a real run: validator confirms it, matched to THIS run's cursor_from.
    v1 = rsc.validate_reconcile_ran(root, since_cursor="2026-05-01T00:00:00")
    check("validator ok=True after a real run", v1["ok"] is True, v1)
    check("validator reports the cursor delta + scan count",
          v1["cursor_to"] == "2026-05-30T09:00:00" and v1["sent_scanned_count"] == 1, v1)

    # A stale audit (wrong since_cursor) must NOT pass for this run.
    v2 = rsc.validate_reconcile_ran(root, since_cursor="2024-01-01T00:00:00")
    check("stale audit (cursor_from mismatch) -> ok=False", v2["ok"] is False, v2)

    # An empty-fetch run STILL emits an audit (proves the fetch happened, found nothing).
    root2 = _build_ws([commitment(1, USER, "x", [BOB])], cursor="2026-05-01T00:00:00")
    rsc.reconcile_and_receipt(root2, [], user_person_id=USER, source_skill="reconcile-sent")
    v3 = rsc.validate_reconcile_ran(root2)
    check("0-scan run still emits an audit (ran, found nothing) -> ok=True",
          v3["ok"] is True and v3["sent_scanned_count"] == 0, v3)

    import shutil
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(root2, ignore_errors=True)


def _build_ws(opens, cursor):
    """Temp workspace with _hq/data/{entities.json,events.jsonl}. Returns root."""
    import json, tempfile
    root = tempfile.mkdtemp(prefix="cr-rsc-recv-")
    data = os.path.join(root, "_hq", "data")
    os.makedirs(data)
    entities = {"version": 1, "workspace": {"sent_reconcile_cursor": cursor},
                "orgs": [], "people": [], "threads": [], "engagements": []}
    with open(os.path.join(data, "entities.json"), "w", encoding="utf-8") as f:
        json.dump(entities, f)
    with open(os.path.join(data, "events.jsonl"), "w", encoding="utf-8") as f:
        for ev in opens:
            f.write(json.dumps(ev) + "\n")
    return root


def _read_cursor(root):
    import json
    d = json.load(open(os.path.join(root, "_hq", "data", "entities.json"), encoding="utf-8"))
    return d["workspace"]["sent_reconcile_cursor"]


def test_reconcile_and_receipt():
    """Bug #98: the orchestrator must do ALL the I/O (write events + advance the
    cursor) and return a receipt whose fields only exist because the work ran.
    This is what kills the v3.18.5 theater — a skip leaves no receipt and no
    substrate trace, both checkable."""
    import json
    from cru_match import load_open_commitments

    print("\n[8] reconcile_and_receipt — end-to-end I/O + tamper-proof receipt (Bug #98)")
    opens = [
        commitment(1, USER, "Send Bob the Q2 financial review deck", [BOB]),
        commitment(2, USER, "Schedule the onboarding call with the new vendor", [BOB]),
    ]
    sent = [{
        "message_id": "m1", "ts": "2026-05-30T09:00:00", "recipient_person_ids": [BOB],
        "subject": "Q2 financial review deck",
        "body": "Bob, here is the Q2 financial review deck I owed you.",
    }]
    root = _build_ws(opens, cursor="2026-05-01T00:00:00")
    ev_path = os.path.join(root, "_hq", "data", "events.jsonl")

    receipt = rsc.reconcile_and_receipt(root, sent, user_person_id=USER, source_skill="morning-briefing")

    check("receipt proves a run (ran=True)", receipt.get("ran") is True, receipt)
    check("n_auto_closed == 1", receipt["n_auto_closed"] == 1, receipt)
    check("events_written == 1", receipt["events_written"] == 1, receipt)
    check("summary is code-generated and mentions the close",
          "closed 1" in receipt["summary"], receipt["summary"])

    # The write actually landed in substrate (the checkable artifact).
    rows = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
    resolved = [r for r in rows if r.get("type") == "commitment_resolved"
                and (r.get("data") or {}).get("resolved_by") == "sent_reconcile"]
    check("a commitment_resolved/sent_reconcile event was appended to events.jsonl",
          len(resolved) == 1, resolved)
    check("the auto-closed commitment is gone from the open set",
          _commitment_id(opens[0]) not in {_commitment_id(c) for c in load_open_commitments(ev_path)})

    # Cursor advanced IN SUBSTRATE (not just reported).
    check("cursor advanced in entities.json", _read_cursor(root) == "2026-05-30T09:00:00", _read_cursor(root))
    check("receipt cursor_before/after reflect the move",
          receipt["cursor_before"] == "2026-05-01T00:00:00"
          and receipt["cursor_after"] == "2026-05-30T09:00:00"
          and receipt["cursor_advanced"] is True, receipt)

    # Idempotency: re-running the SAME (now-stale) batch closes nothing new and
    # never moves the cursor backwards.
    receipt2 = rsc.reconcile_and_receipt(root, sent, user_person_id=USER, source_skill="morning-briefing")
    rows2 = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
    resolved2 = [r for r in rows2 if r.get("type") == "commitment_resolved"]
    check("re-run closes nothing new (idempotent)", len(resolved2) == 1, len(resolved2))
    check("re-run does not move the cursor backwards", _read_cursor(root) == "2026-05-30T09:00:00")

    # Fail-loud distinguishing: an EMPTY fetch still returns a receipt (proof of
    # run) but closes nothing and leaves the cursor put — distinct from a skip,
    # which leaves NO receipt at all.
    root2 = _build_ws([commitment(1, USER, "Send Bob the deck", [BOB])], cursor="2026-05-01T00:00:00")
    receipt3 = rsc.reconcile_and_receipt(root2, [], user_person_id=USER, source_skill="morning-briefing")
    check("empty fetch still returns a receipt (ran=True)", receipt3.get("ran") is True, receipt3)
    check("empty fetch closes nothing, cursor unchanged",
          receipt3["n_auto_closed"] == 0 and receipt3["cursor_advanced"] is False, receipt3)
    check("empty-fetch summary says nothing-to-reconcile (honest, not faked)",
          "nothing to reconcile" in receipt3["summary"], receipt3["summary"])

    import shutil
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(root2, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
