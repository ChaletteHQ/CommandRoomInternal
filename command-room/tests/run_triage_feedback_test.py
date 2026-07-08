#!/usr/bin/env python3
"""Phase 6 Loop 1 — triage_feedback capture + Pass 13 sender-priority proposals."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import triage_feedback as tf  # noqa: E402
from event_gate import gate_events  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _ws(rows):
    ws = Path(tempfile.mkdtemp(prefix="tf_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    lines = []
    seq = 1
    for r in rows:
        ev = tf.build_triage_feedback_event(**r["kw"])
        ev["seq"] = seq
        ev["ts"] = r["ts"]
        lines.append(json.dumps(ev))
        seq += 1
    (ws / "_hq" / "data" / "events.jsonl").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")
    return ws


def test_event_builder_gate_clean():
    ev = tf.build_triage_feedback_event(
        sender="News@Promo.Example.com", bucket_assigned="surfaced",
        action_taken="skip", draft_offered=True)
    check("builder lowercases sender", ev["data"]["sender"] == "news@promo.example.com")
    check("builder derives domain", ev["data"]["domain"] == "promo.example.com")
    check("builder omits seq/ts", "seq" not in ev and "ts" not in ev)
    # Passes the strict enum gate (triage_feedback is registered).
    out = gate_events([ev], strict_enum=True)
    check("triage_feedback passes the gate", out and out[0]["type"] == "triage_feedback")


def test_demote_proposal():
    # 6 skips of a surfaced newsletter domain → demote.
    rows = [{"ts": _iso(i), "kw": {
        "sender": f"news{i%2}@promo.example.com", "bucket_assigned": "surfaced",
        "action_taken": "skip", "draft_offered": False}} for i in range(6)]
    ws = _ws(rows)
    loaded = tf.load_triage_feedback(ws, since_iso=_iso(30))
    check("loads 6 rows", len(loaded) == 6)
    agg = tf.aggregate_sender_signals(loaded)
    props = tf.propose_sender_rules(agg)
    demote = [p for p in props if p["action"] == "demote"]
    check("proposes a demote", len(demote) >= 1)
    check("demote prefers the domain scope",
          any(p["scope_kind"] == "domain" and p["scope_value"] == "promo.example.com"
              for p in demote))
    check("demote delta is negative", demote[0]["delta"] == tf.DEMOTE_DELTA)


def test_promote_proposal():
    # 5 fast sends to an un-surfaced sender → promote.
    rows = [{"ts": _iso(i), "kw": {
        "sender": "vip@client.example.com", "bucket_assigned": "fyi",
        "action_taken": "send", "draft_offered": True}} for i in range(5)]
    ws = _ws(rows)
    agg = tf.aggregate_sender_signals(tf.load_triage_feedback(ws, since_iso=_iso(30)))
    props = tf.propose_sender_rules(agg)
    promote = [p for p in props if p["action"] == "promote"]
    check("proposes a promote", len(promote) >= 1)
    check("promote delta positive", promote and promote[0]["delta"] == tf.PROMOTE_DELTA)


def test_small_n_floor_and_consistency():
    # 3 actions only (< MIN_ACTIONS) → nothing.
    agg = tf.aggregate_sender_signals([
        {"sender": "a@aa.example.com", "domain": "aa.example.com",
         "bucket_assigned": "surfaced", "action_taken": "skip",
         "draft_offered": False} for _ in range(3)])
    check("below MIN_ACTIONS proposes nothing", tf.propose_sender_rules(agg) == [])
    # Mixed 3 skip / 3 send (consistency 0.5) → nothing.
    mixed = [{"sender": "b@bb.example.com", "domain": "bb.example.com",
              "bucket_assigned": "surfaced", "action_taken": a, "draft_offered": False}
             for a in ["skip"] * 3 + ["send"] * 3]
    check("inconsistent behavior proposes nothing",
          tf.propose_sender_rules(tf.aggregate_sender_signals(mixed)) == [])


def test_cooldown_and_existing_suppression():
    rows = [{"sender": f"n{i}@promo.example.com", "domain": "promo.example.com",
             "bucket_assigned": "surfaced", "action_taken": "skip",
             "draft_offered": False} for i in range(6)]
    agg = tf.aggregate_sender_signals(rows)
    fp = tf.sender_rule_fingerprint("domain", "promo.example.com", "demote")
    check("cooldown suppresses the fingerprint",
          tf.propose_sender_rules(agg, cooldown_fingerprints={fp}) == [])
    existing = [{"match": {"kind": "domain", "value": "promo.example.com"},
                 "action": "demote", "delta": -30}]
    check("existing rule suppresses re-proposal",
          tf.propose_sender_rules(agg, existing_rules=existing) == [])


def test_cap():
    rows = []
    for d in range(6):
        rows += [{"sender": f"x@dom{d}.example.com", "domain": f"dom{d}.example.com",
                  "bucket_assigned": "surfaced", "action_taken": "skip",
                  "draft_offered": False} for _ in range(5)]
    props = tf.propose_sender_rules(tf.aggregate_sender_signals(rows), cap=3)
    check("3-cap honored", len(props) == 3)


def test_store_roundtrip_and_scoring():
    ws = Path(tempfile.mkdtemp(prefix="tfstore_"))
    store = tf.load_sender_priority_rules(ws)
    check("missing store reads empty", store == {"version": 1, "rules": []})
    prop = {"scope_kind": "domain", "scope_value": "promo.example.com",
            "action": "demote", "delta": -30, "fingerprint": "spr_x", "plain": "p"}
    store["rules"].append(tf.rule_from_proposal(prop, added_ts=_iso(0)))
    tf.write_sender_priority_rules(ws, store)
    reloaded = tf.load_sender_priority_rules(ws)
    check("store round-trips", len(reloaded["rules"]) == 1)
    rules = reloaded["rules"]
    check("domain demote applies to member sender",
          tf.apply_rules_to_score(100, sender="a@promo.example.com", rules=rules) == 70)
    check("non-matching sender unaffected",
          tf.apply_rules_to_score(100, sender="a@other.example.com", rules=rules) == 100)
    check("empty rules is identity",
          tf.apply_rules_to_score(100, sender="a@promo.example.com", rules=[]) == 100)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_event_builder_gate_clean()
    test_demote_proposal()
    test_promote_proposal()
    test_small_n_floor_and_consistency()
    test_cooldown_and_existing_suppression()
    test_cap()
    test_store_roundtrip_and_scoring()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL triage_feedback tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
