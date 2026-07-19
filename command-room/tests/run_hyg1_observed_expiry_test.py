#!/usr/bin/env python3
"""HYG1 Item 2 — observed-tier 30-day expiry, derive-on-read (the W4c
deferred decay). Pins:

1. A 29-day-old observed item counts, corroborates, and can promote.
2. A 31-day-old item is invisible: excluded from observed_counts' live
   count, from corroboration matching, from prep context, and
   promote_observed refuses it with a plain reason.
3. Promoted-then-aged is unaffected — promotion is permanent; the observed
   source aging changes nothing about the commitment.
4. observed_counts reports the expired count separately (cleanup's audit
   line); the live set-aside sentence never inflates.
5. Append-only doctrine: nothing is deleted — the expired event is still in
   the log.

Fixture dates are computed RELATIVE TO NOW (hardcoded-future-date gotcha);
`now` is passed explicitly where the API takes it, and boundary items sit
2 days from the threshold so wall-clock drift inside a run can't flip them.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import capture_gate as cg  # noqa: E402
from cru_match import load_open_commitments  # noqa: E402

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


NOW = dt.datetime.now(dt.timezone.utc)


def iso_days_ago(n):
    return (NOW - dt.timedelta(days=n)).isoformat()


def new_ws():
    ws = tempfile.mkdtemp()
    dd = os.path.join(ws, "_hq", "data")
    os.makedirs(dd)
    return ws, os.path.join(dd, "events.jsonl")


def obs_event(seq, oid, title, days_ago, *, counterparty_id="person_A"):
    """Direct fixture line with a controlled ts (the gate would re-stamp
    'now'; expiry math needs real ages)."""
    return {
        "seq": seq, "ts": iso_days_ago(days_ago),
        "type": "commitment_observed", "source_skill": "test",
        "primary_thread_id": None,
        "person_ids": [counterparty_id],
        "data": {"id": oid, "title": title, "tier": "observed",
                 "observed_reason": "between other people",
                 "kind": "promise", "counterparty_id": counterparty_id,
                 "source_ref": f"granola:src{seq}", "no_due": True},
    }


def corroborating_event(seq, title, days_ago, *, person="person_A"):
    return {
        "seq": seq, "ts": iso_days_ago(days_ago),
        "type": "interaction", "source_skill": "test",
        "person_ids": [person],
        "data": {"source_ref": f"gmail:cor{seq}", "summary": title},
    }


def write_events(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def main():
    print("=== HYG1 Item 2 — observed-tier 30d derive-on-read expiry ===\n")

    ws, p = new_ws()
    write_events(p, [
        obs_event(1, "obs_live_29d", "Stacy to send Rick the quarterly report", 29),
        obs_event(2, "obs_stale_31d", "Vendor to ship the replacement router", 31),
        corroborating_event(3, "Stacy said the quarterly report for Rick is coming", 1),
        corroborating_event(4, "vendor confirmed the replacement router shipment", 1,
                            person="person_A"),
    ])

    # --- expiry predicate ------------------------------------------------------
    events = list(cg._iter_ws_events(ws))
    live = next(e for e in events if e["data"].get("id") == "obs_live_29d")
    stale = next(e for e in events if e["data"].get("id") == "obs_stale_31d")
    check("29d item is NOT expired", cg.observed_expired(live, now=NOW) is False)
    check("31d item IS expired", cg.observed_expired(stale, now=NOW) is True)
    check("constant is 30 (M-tunable, flagged in the report)",
          cg.OBSERVED_EXPIRY_DAYS == 30)

    # --- counts: live-only + separate expired field ----------------------------
    counts = cg.observed_counts(ws, now=NOW)
    check("live count excludes the expired item", counts["observed"] == 1, counts)
    check("expired reported separately", counts["expired"] == 1, counts)
    check("by_reason reflects live items only",
          sum(counts["by_reason"].values()) == 1, counts)

    # --- corroboration: stale observation must not promote off a fresh event ---
    cor = cg.find_corroborations(ws, now=NOW)
    cor_ids = {c["observed"]["data"]["id"] for c in cor}
    check("live item corroborates", "obs_live_29d" in cor_ids, cor_ids)
    check("expired item does NOT corroborate", "obs_stale_31d" not in cor_ids,
          cor_ids)

    # --- promotion: live promotes; expired refuses with a plain reason ---------
    r = cg.promote_observed(ws, "obs_live_29d", corroborated_by="user",
                            source_skill="test")
    check("live item promotes", r.get("ok") is True, r)
    check("promotion opened a pending-review commitment",
          any((c.get("data") or {}).get("promoted_from") == "obs_live_29d"
              for c in load_open_commitments(p)))
    r2 = cg.promote_observed(ws, "obs_stale_31d", corroborated_by="user",
                             source_skill="test")
    check("expired item refuses promotion", r2.get("ok") is False, r2)
    check("refusal reason is plain and actionable",
          "expired" in (r2.get("reason") or "")
          and "capture it fresh" in (r2.get("reason") or ""), r2)

    # --- prep context: expired never resurfaces --------------------------------
    hits = cg.prep_context_observed(ws, ["person_A"])
    hit_ids = {(h.get("data") or {}).get("id") for h in hits}
    check("expired item absent from prep context", "obs_stale_31d" not in hit_ids,
          hit_ids)

    # --- promoted-then-aged: promotion is permanent ----------------------------
    ws2, p2 = new_ws()
    write_events(p2, [
        obs_event(1, "obs_old_promoted", "Team to draft the renewal terms", 45),
        # promoted 40 days ago, while the observed item was still live
        {"seq": 2, "ts": iso_days_ago(40), "type": "commitment",
         "source_skill": "test", "person_ids": ["person_A"],
         "data": {"id": "cmt_promoted_1", "kind": "promise", "status": "open",
                  "title": "Team to draft the renewal terms",
                  "promoted_from": "obs_old_promoted", "pending_review": True,
                  "source_ref": "granola:src1", "no_due": True}},
    ])
    check("promoted-then-aged observed source is not 'expired'",
          cg.observed_expired(
              next(e for e in cg._iter_ws_events(ws2)
                   if e["data"].get("id") == "obs_old_promoted"),
              promoted_ids={"obs_old_promoted"}, now=NOW) is False)
    counts2 = cg.observed_counts(ws2, now=NOW)
    check("promoted item is not counted expired", counts2["expired"] == 0, counts2)
    check("the promoted commitment is untouched (still open)",
          any((c.get("data") or {}).get("id") == "cmt_promoted_1"
              for c in load_open_commitments(p2)))
    r3 = cg.promote_observed(ws2, "obs_old_promoted", source_skill="test")
    check("re-promotion of a promoted item stays the idempotent no-op",
          r3.get("ok") is True and r3.get("already") is True, r3)

    # --- append-only: nothing was deleted ---------------------------------------
    with open(p, encoding="utf-8") as f:
        raw = f.read()
    check("expired event still in the log (append-only, never deleted)",
          '"obs_stale_31d"' in raw)

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
