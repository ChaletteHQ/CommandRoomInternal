#!/usr/bin/env python3
"""
v4.6.2 BUG-3719 — email-SENT commitment capture gap (critical, 2026-07-09).

The miss: an operator replied to a customer thread the same day, making a
promise in their own sent words. No commitment event was ever written —
inbox-triage's extractor covers both directions but only sees
`is:unread in:inbox` threads (read + replied → never a candidate), and
reconcile-sent only CLOSED commitments (a promise never logged can never be
reconciled). The item aged silently until the customer chased.

Fix under test (Sweep scope):
  1. reconcile-sent OPENS a `type: commitment` (owner = user) when a sent
     reply carries a commissive with no matching open item —
     `reconcile_and_receipt(..., sent_commitment_items=...)` →
     `sent_capture.capture_sent_items` → the shared capture block
     (capture_gate) + W4c relevance gate + one locked append;
  2. cross-channel restatement dedup vs the open set
     (`capture_gate.matches_open_commitment`: shared non-user party +
     content-token overlap) so a sent restatement of a meeting-/triage-
     sourced commitment MERGES instead of double-tracking;
  3. scan-for-commitments' Sent pass shares the same module (append=False —
     the scan folds events into its own batch).

Acceptance tests (from the case study, adopted verbatim):
  A1. inbound customer ask + operator sent reply "I'll send corrected
      invoices next week" → exactly ONE open commitment attributed to the
      operator;
  A2. a commitment made only in email (never in a meeting, thread never
      triaged) appears in the tracker / brief / Pulse — asserted against the
      shared substrate those surfaces read (`load_open_commitments` +
      `commitment_counts` headline);
  A3. reconcile-sent opens a commitment from a sent promise with no prior
      log entry, and does NOT duplicate one that exists;
  A4. re-runs are idempotent (no re-capture of the same sent message).

House conventions: check(label, cond), exit 1 on any failure, auto-discovered
by run_all.py. Uses the workspace_mini fixture (real substrate shapes) —
synthetic Sample names only (Rule 26).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from output_exercise_lib import copy_fixture  # noqa: E402
import capture_gate as cg  # noqa: E402
import sent_capture as sc  # noqa: E402
from cru_match import load_open_commitments  # noqa: E402
from primary_user import resolve_primary_user  # noqa: E402
from reconcile_sent_commitments import (  # noqa: E402
    reconcile_and_receipt,
    validate_reconcile_ran,
)

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _events(ws: Path) -> list[dict]:
    out = []
    for line in _events_path(ws).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _opens(ws: Path) -> list[dict]:
    return load_open_commitments(str(_events_path(ws)))


def _titles(opens) -> set:
    return {
        str((o.get("data") or {}).get("title")
            or (o.get("data") or {}).get("summary") or "")
        for o in opens
    }


# Fixture facts (tests/fixtures/workspace_mini): person_001 Sam Sample = the
# primary user (org_001, relationship_type self); person_003 Dustin /
# person_004 Mira = org_002 (customer); person_006 Quinn = org_003 (advisor).

_REPLY = {
    "message_id": "sm_1001",
    "ts": "2026-07-08T17:20:00Z",
    "recipient_person_ids": ["person_003"],
    "recipient_names": ["Dustin Sample", "dustin"],
    "subject": "Re: recurring invoice discrepancies",
    "body": ("Thanks for flagging this. I'll get the billing reconciled "
             "internally and send you corrected, re-itemized invoices next week."),
}

_REPLY_ITEM = {
    "message_id": "sm_1001",
    "ts": "2026-07-08T17:20:00Z",
    "title": "send Dustin corrected, re-itemized invoices",
    "kind": "promise",
    "due": "2026-07-15",
    "counterparty_id": "person_003",
    "evidence": "I'll get the billing reconciled internally and send you corrected, re-itemized invoices next week.",
    "org_id": "org_002",
    "person_ids": ["person_003"],
    "classification_confidence": 0.92,
}


def test_a1_sent_promise_opens_exactly_one():
    print("test_a1_sent_promise_opens_exactly_one — acceptance 1 (the case study replay)")
    ws = copy_fixture()
    user = resolve_primary_user(str(ws))
    base = _opens(ws)
    receipt = reconcile_and_receipt(
        str(ws), [_REPLY],
        user_person_id=user,
        source_skill="reconcile-sent",
        sent_commitment_items=[dict(_REPLY_ITEM)],
        fired_via="scheduled",
    )
    after = _opens(ws)
    check("receipt reports exactly one opened", receipt["n_opened"] == 1,
          f"n_opened={receipt['n_opened']} capture={receipt['capture']}")
    check("open set grew by exactly one", len(after) == len(base) + 1,
          f"{len(base)} -> {len(after)}")
    new = [o for o in after
           if (o.get("data") or {}).get("source_ref") == "gmail:sm_1001"]
    check("the new item is on disk with gmail source_ref", len(new) == 1)
    if new:
        d = new[0].get("data") or {}
        check("owner is the operator (the user)", d.get("owner_id") == user,
              f"owner={d.get('owner_id')!r}")
        check("counterparty receipt landed", d.get("counterparty_id") == "person_003")
        check("kind classified promise", d.get("kind") == "promise")
        check("ts backdated to the send time",
              str(new[0].get("ts") or "").startswith("2026-07-08"),
              f"ts={new[0].get('ts')!r}")
        check("person_ids carry both parties",
              set(new[0].get("person_ids") or ()) >= {user, "person_003"})
    check("summary line surfaces the open",
          "1 new promise" in receipt["summary"], receipt["summary"])
    # The audit event is the ungameable trace (Bug #98-v3 doctrine).
    v = validate_reconcile_ran(str(ws))
    check("audit validates as a real run", v.get("ok") is True, str(v))
    audits = [e for e in _events(ws) if e.get("type") == "sent_reconcile"]
    ad = (audits[-1].get("data") or {}) if audits else {}
    check("audit carries the capture counts",
          ad.get("n_opened") == 1 and ad.get("n_capture_merged") == 0
          and ad.get("n_capture_errors") == 0, str(ad))
    return ws, user


def test_a2_email_only_commitment_reaches_surfaces(ws, user):
    print("test_a2_email_only_commitment_reaches_surfaces — acceptance 2")
    # Tracker, morning brief, and Pulse all render from the same substrate:
    # the load_open_commitments projection + the commitment_counts headline.
    # An email-only capture must be indistinguishable there from any other.
    from commitment_state import commitment_counts

    opens = _opens(ws)
    check("email-only promise is in the open projection",
          "send Dustin corrected, re-itemized invoices" in _titles(opens))
    counts = commitment_counts(str(ws), user_person_id=user,
                               now_iso="2026-07-09T00:00:00Z")
    head = counts.get("headline") or {}
    you_owe_ids = [
        o for o in opens
        if (o.get("data") or {}).get("owner_id") == user
        and not (o.get("data") or {}).get("pending_review")
    ]
    check("headline you_owe counts it (confident attribution, no flag)",
          head.get("you_owe") == len(you_owe_ids),
          f"headline={head}")
    check("headline total covers the full open set",
          head.get("total") == len(opens), f"headline={head}")


def test_a3_no_duplicate_of_existing():
    print("test_a3_no_duplicate_of_existing — acceptance 3 (cross-channel restatement merges)")
    ws = copy_fixture()
    user = resolve_primary_user(str(ws))
    from event_gate import append_event

    # A meeting-sourced commitment already tracks the promise.
    append_event(_events_path(ws), [{
        "type": "commitment",
        "source_skill": "meeting-notes",
        "primary_thread_id": "project_001",
        "person_ids": [user, "person_004"],
        "data": {
            "title": "send Mira the corrected invoices",
            "kind": "promise",
            "due": "2026-07-15",
            "owner_id": user,
            "counterparty_id": "person_004",
            "source_ref": "granola:mtg_777",
            "status": "open",
        },
    }], holder="test")
    base = _opens(ws)
    items = [
        {   # restatement of the tracked promise, different channel + wording
            "message_id": "sm_2001", "ts": "2026-07-08T18:00:00Z",
            "title": "send corrected invoices over to Mira",
            "kind": "promise", "due": "2026-07-15",
            "counterparty_id": "person_004", "person_ids": ["person_004"],
            "classification_confidence": 0.9,
        },
        {   # genuinely new promise — must open
            "message_id": "sm_2002", "ts": "2026-07-08T18:05:00Z",
            "title": "send Quinn the revised advisory agreement",
            "kind": "promise", "no_due": True,
            "counterparty_id": "person_006", "person_ids": ["person_006"],
            "classification_confidence": 0.9,
        },
    ]
    res = sc.capture_sent_items(str(ws), items, user_person_id=user,
                                source_skill="reconcile-sent")
    after = _opens(ws)
    check("restatement merged, not double-tracked", res["n_merged"] == 1,
          str(res["merged"]))
    check("merge recorded the tracked item it folded into",
          res["merged"] and "send Mira the corrected invoices"
          in res["merged"][0]["merged_into_title"], str(res["merged"]))
    check("the genuinely new promise opened", res["n_opened"] == 1,
          str(res["opened"]))
    check("open set grew by exactly one (never two)",
          len(after) == len(base) + 1, f"{len(base)} -> {len(after)}")
    check("no second invoices item on disk",
          sum(1 for t in _titles(after) if "corrected invoices" in t) == 1)
    return ws, user, items


def test_a4_idempotent_reruns(ws, user, items):
    print("test_a4_idempotent_reruns — acceptance 4")
    base = _opens(ws)
    res = sc.capture_sent_items(str(ws), [dict(i) for i in items],
                                user_person_id=user,
                                source_skill="reconcile-sent")
    check("re-run opens nothing", res["n_opened"] == 0, str(res["opened"]))
    check("the previously-opened message is skipped by (source_ref, title)",
          res["n_skipped"] == 1, str(res["skipped_existing"]))
    check("the restatement re-merges (still not written)",
          res["n_merged"] == 1, str(res["merged"]))
    check("open set unchanged", len(_opens(ws)) == len(base))
    check("already_captured sees the landed message",
          sc.already_captured(str(ws), "sm_2002",
                              "send Quinn the revised advisory agreement"))
    # Full-orchestrator idempotency too: same fetch, same items, second fire.
    ws2 = copy_fixture()
    r1 = reconcile_and_receipt(str(ws2), [_REPLY], user_person_id=user,
                               source_skill="reconcile-sent",
                               sent_commitment_items=[dict(_REPLY_ITEM)])
    n_after_first = len(_opens(ws2))
    r2 = reconcile_and_receipt(str(ws2), [_REPLY], user_person_id=user,
                               source_skill="reconcile-sent",
                               sent_commitment_items=[dict(_REPLY_ITEM)])
    check("orchestrator re-fire: first opens, second skips",
          r1["n_opened"] == 1 and r2["n_opened"] == 0,
          f"{r1['n_opened']}/{r2['n_opened']}")
    check("self-closure guard: the origin message never closes its own promise",
          r2["n_auto_closed"] == 0, f"n_auto_closed={r2['n_auto_closed']}")
    check("orchestrator re-fire: open set unchanged",
          len(_opens(ws2)) == n_after_first)


def test_user_excluded_from_party_match():
    print("test_user_excluded_from_party_match — user-overlap alone never merges")
    ws = copy_fixture()
    user = resolve_primary_user(str(ws))
    # Fixture seq 5 'Send Northstar the pricing sheet' is owner=user with NO
    # counterparty — heavy content overlap with the new item below, but the
    # only shared party is the user. Merging on that would swallow a distinct
    # promise into an unrelated item.
    base = _opens(ws)
    res = sc.capture_sent_items(str(ws), [{
        "message_id": "sm_3001", "ts": "2026-07-08T19:00:00Z",
        "title": "send Dustin the pricing sheet",
        "kind": "promise", "no_due": True,
        "counterparty_id": "person_003", "person_ids": ["person_003"],
        "classification_confidence": 0.9,
    }], user_person_id=user, source_skill="reconcile-sent")
    check("no merge on user-only party overlap", res["n_merged"] == 0,
          str(res["merged"]))
    check("the distinct promise opened", res["n_opened"] == 1
          and len(_opens(ws)) == len(base) + 1)


def test_gate_failures_loud_not_fatal():
    print("test_gate_failures_loud_not_fatal — the capture block + error channel")
    ws = copy_fixture()
    user = resolve_primary_user(str(ws))
    base = _opens(ws)
    res = sc.capture_sent_items(str(ws), [
        {   # S2 violation: neither due nor no_due
            "message_id": "sm_4001", "ts": "2026-07-08T19:10:00Z",
            "title": "send Mira the revised proposal", "kind": "promise",
            "counterparty_id": "person_004",
        },
        {   # Stage-E violation: task carrying a counterparty
            "message_id": "sm_4002", "ts": "2026-07-08T19:11:00Z",
            "title": "book flights for the offsite", "kind": "task",
            "no_due": True, "counterparty_id": "person_004",
        },
        {   # valid — must land despite the two failures above
            "message_id": "sm_4003", "ts": "2026-07-08T19:12:00Z",
            "title": "send Avery the onboarding checklist", "kind": "promise",
            "no_due": True, "counterparty_id": "person_007",
            "person_ids": ["person_007"], "classification_confidence": 0.9,
        },
    ], user_person_id=user, source_skill="reconcile-sent")
    check("both malformed items land in errors, loudly", res["n_errors"] == 2,
          str(res["errors"]))
    check("errors name the gate rule",
          any("due" in e["error"] for e in res["errors"])
          and any("promise, not a task" in e["error"] for e in res["errors"]),
          str(res["errors"]))
    check("the valid item still opened (batch not poisoned)",
          res["n_opened"] == 1 and len(_opens(ws)) == len(base) + 1)
    # Builder-level fail-loud contracts.
    try:
        sc.build_sent_commitment_event("send the deck", message_id="m",
                                       kind="promise", user_person_id="",
                                       no_due=True)
        check("empty user fails loud (Bug #102)", False)
    except sc.SentItemError as e:
        check("empty user fails loud (Bug #102)", "resolve_primary_user" in str(e))
    ev = sc.build_sent_commitment_event(
        "send the summary to the new vendor contact", message_id="m2",
        kind="promise", user_person_id="person_001", no_due=True,
        counterparty_name="Jordan Vendor")
    check("pending_review inversion stamps unresolved counterparty",
          ev["data"].get("pending_review") is True
          and "no person record" in ev["data"].get("review_reason", ""))


def test_relevance_gate_routing():
    print("test_relevance_gate_routing — W4c gate on the sent leg")
    ws = copy_fixture()
    user = resolve_primary_user(str(ws))
    from skill_custom_writer import add_directive

    add_directive(str(ws), cg.CAPTURE_POLICY_SKILL, "for org_002: observed-only",
                  origin="learned")
    base = _opens(ws)
    res = sc.capture_sent_items(str(ws), [
        {   # undated promise to the observed-only org → set aside
            "message_id": "sm_5001", "ts": "2026-07-08T20:00:00Z",
            "title": "send Dustin the meeting recap notes",
            "kind": "promise", "no_due": True,
            "counterparty_id": "person_003", "org_id": "org_002",
            "person_ids": ["person_003"], "classification_confidence": 0.9,
        },
        {   # dated promise to the SAME org → caution rail beats the override
            "message_id": "sm_5002", "ts": "2026-07-08T20:01:00Z",
            "title": "send Dustin the signed addendum",
            "kind": "promise", "due": "2026-07-14",
            "counterparty_id": "person_003", "org_id": "org_002",
            "person_ids": ["person_003"], "classification_confidence": 0.9,
        },
    ], user_person_id=user, source_skill="reconcile-sent")
    after = _opens(ws)
    check("observed-only override sets the undated item aside",
          res["n_observed"] == 1, str(res))
    check("set-aside item is a commitment_observed event, not an open item",
          any(e.get("type") == cg.OBSERVED_TYPE
              and (e.get("data") or {}).get("source_ref") == "gmail:sm_5001"
              for e in _events(ws)))
    check("caution rail: the dated item opens despite the override",
          res["n_opened"] == 1 and len(after) == len(base) + 1, str(res))


def test_pre_close_baseline():
    print("test_pre_close_baseline — a restatement merges into an item this fire just closed")
    ws = copy_fixture()
    user = resolve_primary_user(str(ws))
    from event_gate import append_event
    from commitment_state import close_commitments

    append_event(_events_path(ws), [{
        "type": "commitment",
        "source_skill": "meeting-notes",
        "person_ids": [user, "person_004"],
        "data": {"title": "send Mira the updated renewal terms",
                 "kind": "promise", "due": "2026-07-10", "owner_id": user,
                 "counterparty_id": "person_004", "status": "open",
                 "source_ref": "granola:mtg_888"},
    }], holder="test")
    opens_pre = _opens(ws)
    target = [o for o in opens_pre
              if (o.get("data") or {}).get("source_ref") == "granola:mtg_888"]
    close_commitments(str(ws), [{
        "commitment_id": (target[0].get("data") or {}).get("id"),
        "resolved_by": "sent_reconcile",
        "evidence": "matched an outbound send",
    }], source_skill="reconcile-sent")
    n_after_close = len(_opens(ws))
    res = sc.capture_sent_items(str(ws), [{
        "message_id": "sm_6001", "ts": "2026-07-08T21:00:00Z",
        "title": "send the updated renewal terms to Mira",
        "kind": "promise", "due": "2026-07-10",
        "counterparty_id": "person_004", "person_ids": ["person_004"],
        "classification_confidence": 0.9,
    }], user_person_id=user, opens=opens_pre, source_skill="reconcile-sent")
    check("restatement merges against the pre-close baseline",
          res["n_merged"] == 1 and res["n_opened"] == 0, str(res))
    check("the closed item is not resurrected and nothing new opened",
          len(_opens(ws)) == n_after_close)


def test_scan_pass_construction_only():
    print("test_scan_pass_construction_only — append=False for the scan's single-batch contract")
    ws = copy_fixture()
    user = resolve_primary_user(str(ws))
    before = _events_path(ws).read_text(encoding="utf-8")
    res = sc.capture_sent_items(str(ws), [{
        "message_id": "sm_7001", "ts": "2026-07-01T10:00:00Z",
        "title": "send Noor the partnership one-pager",
        "kind": "promise", "no_due": True,
        "counterparty_id": "person_008", "person_ids": ["person_008"],
        "classification_confidence": 0.9,
    }], user_person_id=user, source_skill="scan-for-commitments",
        append=False)
    check("events returned for the scan's own batch",
          len(res.get("events") or []) == 1
          and res["events"][0]["type"] == "commitment")
    check("nothing written in construction-only mode",
          _events_path(ws).read_text(encoding="utf-8") == before)
    check("scan source_skill stamped for rollback filtering",
          res["events"][0]["source_skill"] == "scan-for-commitments")


def test_backcompat_receipt_shape():
    print("test_backcompat_receipt_shape — no items passed = pre-4.6.2 behavior")
    ws = copy_fixture()
    user = resolve_primary_user(str(ws))
    receipt = reconcile_and_receipt(str(ws), [], user_person_id=user,
                                    source_skill="reconcile-sent")
    check("capture pass absent by default",
          receipt["capture"] is None and receipt["n_opened"] == 0
          and receipt["opened"] == [])
    audits = [e for e in _events(ws) if e.get("type") == "sent_reconcile"]
    ad = (audits[-1].get("data") or {}) if audits else {}
    check("audit carries NO capture fields when the pass didn't run",
          "n_opened" not in ad, str(ad))
    check("summary is the classic no-mail line",
          "nothing to reconcile" in receipt["summary"], receipt["summary"])


def main() -> int:
    ws, user = test_a1_sent_promise_opens_exactly_one()
    test_a2_email_only_commitment_reaches_surfaces(ws, user)
    ws3, user3, items3 = test_a3_no_duplicate_of_existing()
    test_a4_idempotent_reruns(ws3, user3, items3)
    test_user_excluded_from_party_match()
    test_gate_failures_loud_not_fatal()
    test_relevance_gate_routing()
    test_pre_close_baseline()
    test_scan_pass_construction_only()
    test_backcompat_receipt_shape()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
