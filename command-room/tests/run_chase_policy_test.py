#!/usr/bin/env python3
"""Phase 6 Loop 6 — chase-policy learning from email outcomes + S3 noise rider."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import chase_policy as cp  # noqa: E402
import commitment_noise as cn  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws(events):
    ws = Path(tempfile.mkdtemp(prefix="chp_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    lines = [json.dumps({**e, "seq": i}) for i, e in enumerate(events, 1)]
    (ws / "_hq" / "data" / "events.jsonl").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")
    return ws


def _outcome(recipient, outcome, latency=None):
    return {"type": "email_outcome",
            "data": {"recipient": recipient, "outcome": outcome, "latency_days": latency}}


# --- Loop 6 chase policy ----------------------------------------------------

def test_below_total_floor():
    ws = _ws([_outcome("a@vendor.example.com", "no_reply_7d") for _ in range(5)])
    rows = cp.load_email_outcomes(ws)
    groups = cp.group_outcomes(rows, lambda r: "vendor")
    check("below ≥8 total floor → no proposals",
          cp.derive_chase_policy(groups) == [])


def test_quiet_group_chases_earlier():
    # 10 vendor threads, 5 no-reply (50%) → chase at day 3, escalate after 2.
    evs = [_outcome("v@vendor.example.com", "no_reply_7d") for _ in range(5)]
    evs += [_outcome("v@vendor.example.com", "replied", 4) for _ in range(5)]
    ws = _ws(evs)
    groups = cp.group_outcomes(cp.load_email_outcomes(ws), lambda r: "vendor")
    props = cp.derive_chase_policy(groups)
    check("quiet vendor group → proposal", len(props) == 1)
    check("chase at day 3", props[0]["chase_after_days"] == 3)
    check("escalate after 2 silent chases", props[0]["escalate_after_silent_chases"] == 2)


def test_group_floor_and_default_skip():
    # investor group has only 2 (below ≥3 group floor); vendor has 8 all replied
    # fast → but median 5 stays at default 7 → skipped as non-material.
    evs = [_outcome("i@inv.example.com", "replied", 1) for _ in range(2)]
    evs += [_outcome("v@vendor.example.com", "replied", 5) for _ in range(8)]
    ws = _ws(evs)
    groups = cp.group_outcomes(cp.load_email_outcomes(ws),
                               lambda r: "investor" if "inv" in r else "vendor")
    props = cp.derive_chase_policy(groups)
    check("small group not named; default-window group not proposed", props == [])


def test_store_and_accessor():
    ws = Path(tempfile.mkdtemp(prefix="chpst_"))
    check("missing policy → default window",
          cp.get_chase_window(cp.load_chase_policy(ws), "vendor") == (7, 3))
    policy = cp.load_chase_policy(ws)
    prop = {"relationship_type": "vendor", "chase_after_days": 3,
            "escalate_after_silent_chases": 2, "fingerprint": "chp_x"}
    policy["groups"]["vendor"] = cp.group_from_proposal(prop)
    cp.write_chase_policy(ws, policy)
    reloaded = cp.load_chase_policy(ws)
    check("policy round-trips",
          cp.get_chase_window(reloaded, "vendor") == (3, 2))
    check("unknown rtype falls back to default",
          cp.get_chase_window(reloaded, "advisor") == (7, 3))


# --- S3 rider commitment noise ---------------------------------------------

def _commit(cid, counterparty, resolution=None):
    evs = [{"type": "commitment",
            "data": {"id": cid, "kind": "promise", "title": "t",
                     "counterparty_name": counterparty}}]
    if resolution:
        evs.append({"type": "commitment_resolved",
                    "data": {"commitment_id": cid, "resolution": resolution}})
    return evs


def test_noise_analysis_and_proposal():
    evs = []
    # Noisy Sample Vendor: 8 resolved, 6 dropped (75%).
    for i in range(6):
        evs += _commit(f"cmt_n{i}", "Sample Vendor", "dropped")
    for i in range(2):
        evs += _commit(f"cmt_k{i}", "Sample Vendor", "done")
    # Clean counterparty: 8 resolved, all done.
    for i in range(8):
        evs += _commit(f"cmt_c{i}", "Real Client", "done")
    ws = _ws(evs)
    stats = cn.analyze_noise(ws)
    check("noisy source drop_rate 0.75", round(stats["Sample Vendor"]["drop_rate"], 2) == 0.75)
    props = cn.propose_noise_rules(stats)
    check("proposes for the noisy source only", len(props) == 1)
    check("names the noisy source", props[0]["name"] == "Sample Vendor")
    check("clean source not proposed",
          all(p["name"] != "Real Client" for p in props))


def test_noise_floor_and_cooldown():
    evs = []
    for i in range(4):  # only 4 resolved (< 8) even if all dropped
        evs += _commit(f"cmt_s{i}", "Tiny Vendor", "dropped")
    ws = _ws(evs)
    check("below ≥8 floor → nothing", cn.propose_noise_rules(cn.analyze_noise(ws)) == [])


def test_never_track_append_dedup():
    ws = Path(tempfile.mkdtemp(prefix="ntr_"))
    (ws / "_hq").mkdir(parents=True)
    check("first append writes",
          cn.append_never_track_rule(ws, "never-track: low-consequence items from Sample Vendor"))
    check("duplicate append is a no-op",
          cn.append_never_track_rule(ws, "never-track: low-consequence items from Sample Vendor") is False)
    rules = cn.load_never_track_rules(ws)
    check("rule readable back", any("Sample Vendor" in r for r in rules))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_below_total_floor()
    test_quiet_group_chases_earlier()
    test_group_floor_and_default_skip()
    test_store_and_accessor()
    test_noise_analysis_and_proposal()
    test_noise_floor_and_cooldown()
    test_never_track_append_dedup()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL chase_policy + commitment_noise tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
