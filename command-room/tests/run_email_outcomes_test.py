#!/usr/bin/env python3
"""SPEC B6 — outcome tracking v1 tests (email replies + commitment punctuality).

House conventions per run_reconcile_sent_test.py: check(name, cond), OK/FAIL,
non-zero exit on any failure, auto-discovered by run_all.py.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import email_outcomes as eo  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws(events):
    ws = Path(tempfile.mkdtemp(prefix="b6_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else ""),
        encoding="utf-8",
    )
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps({"entities": {"workspace": {"user_email": "me@example.com",
                                               "user_person_id": "person_001"},
                                 "people": [{"id": "person_001", "email": "me@example.com"}]}}),
        encoding="utf-8",
    )
    return ws


def _read_events(ws):
    return [json.loads(l) for l in (ws / "_hq" / "data" / "events.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]


NOW = "2026-06-20T00:00:00Z"


def test_pending_loader():
    events = [
        {"seq": 1, "ts": "2026-06-18T00:00:00Z", "type": "email_sent", "data": {"recipient": "a@x.com", "draft_event_seq": 11}},
        {"seq": 2, "ts": "2026-06-18T00:00:00Z", "type": "email_sent", "data": {"recipient": "b@x.com", "draft_event_seq": 12}},
        {"seq": 3, "ts": "2026-06-18T00:00:00Z", "type": "email_sent", "data": {"recipient": "c@x.com", "draft_event_seq": 13}},
        {"seq": 9, "ts": "2026-06-18T00:00:00Z", "type": "email_outcome", "data": {"sent_event_seq": 1, "outcome": "replied"}},
        {"seq": 4, "ts": "2026-05-10T00:00:00Z", "type": "email_sent", "data": {"recipient": "old@x.com", "draft_event_seq": 14}},
    ]
    ws = _ws(events)
    pend = eo.load_pending_tracked_sends(ws / "_hq" / "data" / "events.jsonl", now_iso=NOW)
    seqs = {p["sent_event_seq"] for p in pend}
    check("pending excludes terminal + old send (exactly {2,3})", seqs == {2, 3})


def test_classify_reply_and_latency():
    sends = [{"sent_event_seq": 2, "draft_ref": 12, "recipient": "b@x.com", "sent_ts": "2026-06-01T00:00:00Z", "gmail_thread_id": "t2"}]
    states = {2: {"messages": [{"from": "Bob <b@x.com>", "ts": "2026-06-03T12:00:00Z", "message_id": "m1"}]}}
    r = eo.classify_outcomes(sends, states, user_email_addresses=["me@example.com"], now_iso=NOW)
    o = r["outcomes"][0]
    check("counterparty reply -> replied", o["outcome"] == "replied")
    check("latency_days correct to 0.1", o["latency_days"] == 2.5)
    check("reply carries reply_message_id", o["reply_message_id"] == "m1")


def test_classify_self_reply_not_a_reply():
    sends = [{"sent_event_seq": 3, "draft_ref": 13, "recipient": "c@x.com", "sent_ts": "2026-06-01T00:00:00Z"}]
    states = {3: {"messages": [{"from": "Me <me@example.com>", "ts": "2026-06-02T00:00:00Z", "message_id": "m2"}]}}
    r = eo.classify_outcomes(sends, states, user_email_addresses=["me@example.com"], now_iso=NOW)
    # user-self message is not a reply; sent 19 days ago -> no_reply_7d (terminal)
    check("user-self message is NOT a reply", r["outcomes"][0]["outcome"] == "no_reply_7d")


def test_classify_bounce_beats_reply():
    sends = [{"sent_event_seq": 4, "draft_ref": 14, "recipient": "d@x.com", "sent_ts": "2026-06-01T00:00:00Z"}]
    states = {4: {"messages": [
        {"from": "mailer-daemon@example.com", "ts": "2026-06-01T00:05:00Z", "message_id": "bounce1"},
        {"from": "Dee <d@x.com>", "ts": "2026-06-05T00:00:00Z", "message_id": "human1"},
    ]}}
    r = eo.classify_outcomes(sends, states, user_email_addresses=["me@example.com"], now_iso=NOW)
    check("bounce beats a later human reply", r["outcomes"][0]["outcome"] == "bounced")


def test_classify_pending_then_noreply():
    # <7 days, no reply -> pending (no event)
    sends = [{"sent_event_seq": 5, "draft_ref": 15, "recipient": "e@x.com", "sent_ts": "2026-06-18T00:00:00Z"}]
    r = eo.classify_outcomes(sends, {5: {"messages": []}}, user_email_addresses=["me@example.com"], now_iso=NOW)
    check("<7d no reply emits nothing", r["outcomes"] == [] and r["counts"]["still_pending"] == 1)
    # >=7 days, no reply -> no_reply_7d
    sends2 = [{"sent_event_seq": 6, "draft_ref": 16, "recipient": "f@x.com", "sent_ts": "2026-06-01T00:00:00Z"}]
    r2 = eo.classify_outcomes(sends2, {6: {"messages": []}}, user_email_addresses=["me@example.com"], now_iso=NOW)
    check(">=7d no reply -> no_reply_7d", r2["outcomes"][0]["outcome"] == "no_reply_7d")


def test_mixed_offset_iso_parses():
    sends = [{"sent_event_seq": 7, "draft_ref": 17, "recipient": "g@x.com", "sent_ts": "2026-06-01T00:00:00+00:00"}]
    states = {7: {"messages": [{"from": "g@x.com", "ts": "2026-06-02T00:00:00Z", "message_id": "m3"}]}}
    r = eo.classify_outcomes(sends, states, user_email_addresses=["me@example.com"], now_iso=NOW)
    check("mixed-offset ISO timestamps classify", r["outcomes"][0]["outcome"] == "replied")


def test_idempotence_and_event_shape():
    events = [{"seq": 2, "ts": "2026-06-01T00:00:00Z", "type": "email_sent",
               "data": {"recipient": "b@x.com", "draft_event_seq": 99, "gmail_thread_id": "t2"}}]
    ws = _ws(events)
    states = {2: {"messages": [{"from": "Bob <b@x.com>", "ts": "2026-06-03T00:00:00Z", "message_id": "m1"}]}}
    s1 = eo.watch_and_receipt(ws, states, now_iso=NOW)
    check("first watch writes one event", s1["events_written"] == 1)
    evs = [e for e in _read_events(ws) if e.get("type") == "email_outcome"]
    check("email_outcome carries sent_event_seq", evs[0]["data"]["sent_event_seq"] == 2)
    check("draft_ref equals original draft_event_seq", evs[0]["data"]["draft_ref"] == 99)
    maxseq = max(e["seq"] for e in _read_events(ws))
    check("new event seq is monotonic", evs[0]["seq"] == maxseq)
    s2 = eo.watch_and_receipt(ws, states, now_iso=NOW)
    check("re-run appends zero new events (idempotent)", s2["events_written"] == 0)


def test_receipt_summary_stable():
    ws0 = _ws([])
    s0 = eo.watch_and_receipt(ws0, {}, now_iso=NOW)
    check("0-checked summary is a stable string", isinstance(s0["summary"], str) and s0["checked"] == 0)


def test_commitment_punctuality():
    # Commitments identify via data.id (_commitment_id); closers reference via
    # data.commitment_id / target_id (the real shape asymmetry).
    events = [
        {"type": "commitment", "ts": "2026-05-20T00:00:00Z", "data": {"id": "c1", "due": "2026-06-01T00:00:00Z", "status": "open"}},
        {"type": "commitment_resolved", "ts": "2026-05-30T00:00:00Z", "data": {"commitment_id": "c1"}},
        {"type": "commitment", "ts": "2026-05-20T00:00:00Z", "data": {"id": "c2", "due": "2026-06-01T00:00:00Z", "status": "open"}},
        {"type": "commitment_resolved", "ts": "2026-06-05T00:00:00Z", "data": {"commitment_id": "c2"}},
        {"type": "commitment", "ts": "2026-04-20T00:00:00Z", "data": {"id": "c3", "due": "2026-05-01T00:00:00Z", "status": "open"}},
        {"type": "commitment", "ts": "2026-06-15T00:00:00Z", "data": {"id": "c4", "due": "2026-07-01T00:00:00Z", "status": "open"}},
        {"type": "commitment", "ts": "2026-06-15T00:00:00Z", "data": {"id": "c5", "status": "open"}},
        {"type": "commitment", "ts": "2026-05-20T00:00:00Z", "data": {"id": "c6", "due_date": "2026-06-20T00:00:00Z", "status": "open"}},
        {"type": "commitment_resolved", "ts": "2026-06-10T00:00:00Z", "data": {"commitment_id": "c6"}},
        {"type": "commitment_resolved", "ts": "2026-06-10T00:00:00Z", "data": {"commitment_id": "orphan"}},
        {"type": "commitment", "ts": "2026-05-20T00:00:00Z", "data": {"id": "c7", "due": "2026-06-15T00:00:00Z", "status": "open"}},
        {"type": "commitment_resolved", "ts": "2026-06-10T00:00:00Z", "data": {"target_id": "c7"}},
    ]
    r = eo.commitment_punctuality(events, as_of_iso="2026-06-10T00:00:00Z")
    by = {p["commitment_id"]: p["bucket"] for p in r["per_commitment"]}
    check("c1 on_time", by["c1"] == "on_time")
    check("c2 late", by["c2"] == "late")
    check("c3 open_past_due", by["c3"] == "open_past_due")
    check("c4 open_not_due", by["c4"] == "open_not_due")
    check("c5 no_due_date", by["c5"] == "no_due_date")
    check("c6 due_date-alias resolves on_time", by["c6"] == "on_time")
    check("orphan commitment_resolved ignored (no row)", "orphan" not in by)
    check("c7 target_id closer shape -> on_time", by["c7"] == "on_time")
    check("aggregates sum to 7 commitments", sum(r["aggregates"].values()) == 7)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_pending_loader()
    test_classify_reply_and_latency()
    test_classify_self_reply_not_a_reply()
    test_classify_bounce_beats_reply()
    test_classify_pending_then_noreply()
    test_mixed_offset_iso_parses()
    test_idempotence_and_event_shape()
    test_receipt_summary_stable()
    test_commitment_punctuality()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL email_outcomes tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
