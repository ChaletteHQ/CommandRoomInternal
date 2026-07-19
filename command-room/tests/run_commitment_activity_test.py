#!/usr/bin/env python3
"""commitment_activity test — the real stuck/blocked metric + shard pruning
(v4.6.0 MC2).

R1b proved the old "stuck" number was overdue-by-due-date wearing a false
caption ("no movement 21d or blocked on a person" — computed nowhere). This
suite locks the REAL metric:

  1. Derivation: last movement = newest state-change event (updated /
     reclassified / reopened / outreach_sent / draft_created) touching the
     commitment id (id chains + F3 seq aliases + legacy seq spellings),
     capture ts as the floor. Non-movement events (chat_dismissal, meeting)
     never count as movement.
  2. Classification: stuck = open AND no movement 21+ days, OR blocked;
     blocked = the newest movement is an unanswered outbound chase
     (outreach_sent) to a named person. blocked ⊆ stuck.
  3. The one-function rule (F-54): headline["stuck"] / headline["blocked"]
     come from count_commitments(..., movement=...) which delegates to
     classify_commitments; all three surface paths return identical numbers.
     Without a movement map the keys are ABSENT — "not computed" never
     renders as 0. The deprecated top-level counts["stuck"] alias keeps its
     R1b meaning (== overdue) untouched.
  4. Shard pruning (the cheap perf half): load_open_commitments(path,
     since_ts=...) drops whole year-shards below the floor by filename.
     Pruned load == full load when the window covers all commitment-family
     events; a commitment stranded in a pruned shard VANISHES (the
     documented caller contract — this test proves why it matters).
  5. stale_tasks keys on the same movement map: a 40-day-old task updated
     3 days ago is not "still on your plate?" noise.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from commitment_activity import (  # noqa: E402
    STUCK_DAYS,
    classify_commitments,
    derive_commitment_movement,
    event_commitment_refs,
)
from commitment_state import (  # noqa: E402
    commitment_counts,
    compute_brief_state,
    count_commitments,
    stale_tasks,
)
from cru_match import load_open_commitments  # noqa: E402

passed = 0
failed = 0

NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat()
USER = "person_user"


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name} :: {detail}")


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _workspace(events, shards=None):
    """Temp workspace; `events` -> active events.jsonl, `shards` -> optional
    {year: [events]} sibling shard files."""
    root = tempfile.mkdtemp(prefix="mc2_test_")
    data = os.path.join(root, "_hq", "data")
    os.makedirs(data)
    with open(os.path.join(data, "events.jsonl"), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    for year, evs in (shards or {}).items():
        with open(os.path.join(data, f"events-{year}.jsonl"), "w", encoding="utf-8") as f:
            for ev in evs:
                f.write(json.dumps(ev) + "\n")
    return root


def _commitment(seq, days_ago, cid=None, kind=None, counterparty=None, owner=USER):
    d = {"title": f"c{seq}", "status": "open", "owner_id": owner}
    if cid:
        d["id"] = cid
    if kind:
        d["kind"] = kind
    if counterparty:
        d["counterparty_name"] = counterparty
    return {"seq": seq, "ts": _iso(days_ago), "type": "commitment",
            "source_skill": "test", "data": d}


def _events_path(root):
    return os.path.join(root, "_hq", "data", "events.jsonl")


def test_derivation_and_classification():
    print("\n[1] derivation: movement events, floors, id chains; "
          "[2] classification: stuck / blocked / neither")
    events = [
        # A — captured 30d ago; a chat_dismissal 5d ago touches it but is NOT
        # movement → stuck (events-but-no-movement, the spec's case 1).
        _commitment(1, 30, cid="cmt_a"),
        {"seq": 10, "ts": _iso(5), "type": "chat_dismissal", "source_skill": "test",
         "data": {"commitment_id": "cmt_a"}},
        # B — chase outbound 3d ago to a named person, no reply → blocked
        # (and stuck) even though movement is recent (spec's case 2).
        _commitment(2, 40, cid="cmt_b"),
        {"seq": 11, "ts": _iso(3), "type": "outreach_sent", "source_skill": "commitments",
         "data": {"commitment_id": "cmt_b", "counterparty_name": "Pedro", "via": "gmail"}},
        # C — updated yesterday → neither (spec's case 3).
        _commitment(3, 40, cid="cmt_c"),
        {"seq": 12, "ts": _iso(1), "type": "commitment_updated", "source_skill": "test",
         "data": {"commitment_id": "cmt_c", "change_summary": "scope tightened"}},
        # D — chased 10d ago, then updated 2d ago → the update is newest, so
        # NOT blocked, and movement is recent → neither.
        _commitment(4, 50, cid="cmt_d"),
        {"seq": 13, "ts": _iso(10), "type": "outreach_sent", "source_skill": "commitments",
         "data": {"commitment_id": "cmt_d", "counterparty_name": "Aria"}},
        {"seq": 14, "ts": _iso(2), "type": "commitment_updated", "source_skill": "test",
         "data": {"commitment_id": "cmt_d"}},
        # E — captured 5d ago, zero events → capture floor, not stuck.
        _commitment(5, 5, cid="cmt_e"),
        # F — id-less commitment (synthesized commitment_seq_6); chase 30d ago
        # references it via the F3 seq alias AND names nobody, and the
        # commitment has no counterparty → movement yes, blocked no; 30d since
        # movement → stuck via no_movement.
        _commitment(6, 45),
        {"seq": 15, "ts": _iso(30), "type": "outreach_sent", "source_skill": "commitments",
         "data": {"commitment_seq": 6, "via": "gmail"}},
        # G — chase 2d ago via LEGACY seq spelling in commitment_id, event
        # names nobody but the COMMITMENT carries a counterparty → blocked
        # falls back to the commitment's own named person.
        _commitment(7, 45, cid="cmt_g", counterparty="Michele Sample"),
        {"seq": 16, "ts": _iso(2), "type": "outreach_sent", "source_skill": "commitments",
         "data": {"commitment_id": "seq_7", "via": "gmail"}},
        # H — draft_created (staged, never sent) 2d ago → movement (not stuck
        # by age: captured 40d ago but the draft moved it) and NOT blocked.
        _commitment(8, 40, cid="cmt_h"),
        {"seq": 17, "ts": _iso(2), "type": "draft_created", "source_skill": "commitments",
         "data": {"commitment_id": "cmt_h"}},
    ]
    root = _workspace(events)
    opens = load_open_commitments(_events_path(root))
    movement = derive_commitment_movement(_events_path(root))
    cls = classify_commitments(opens, movement, NOW_ISO)
    stuck_ids = {r["commitment_id"] for r in cls["stuck"]}
    blocked_ids = {r["commitment_id"] for r in cls["blocked"]}
    by_id = {r["commitment_id"]: r for r in cls["stuck"]}

    check("A: events-but-no-movement 21d+ -> stuck", "cmt_a" in stuck_ids, stuck_ids)
    check("A: chat_dismissal did not count as movement",
          movement["cmt_a"].event_type == "commitment", movement.get("cmt_a"))
    check("A: reason is no_movement", by_id.get("cmt_a", {}).get("reason") == "no_movement")
    check("B: unanswered chase -> blocked", "cmt_b" in blocked_ids, blocked_ids)
    check("B: blocked is also stuck (blocked subset rule)", "cmt_b" in stuck_ids)
    check("B: blocked_on carries the named person",
          by_id.get("cmt_b", {}).get("blocked_on") == "Pedro", by_id.get("cmt_b"))
    check("C: updated yesterday -> neither",
          "cmt_c" not in stuck_ids and "cmt_c" not in blocked_ids)
    check("D: chase answered by a later update -> not blocked, not stuck",
          "cmt_d" not in stuck_ids and "cmt_d" not in blocked_ids)
    check("E: young capture, no events -> not stuck", "cmt_e" not in stuck_ids)
    check("F: seq-alias chase counted as movement",
          movement.get("commitment_seq_6") is not None
          and movement["commitment_seq_6"].event_type == "outreach_sent",
          movement.get("commitment_seq_6"))
    check("F: nameless chase does not block (no named person anywhere)",
          "commitment_seq_6" not in blocked_ids)
    check("F: 30d-old movement -> stuck via no_movement",
          by_id.get("commitment_seq_6", {}).get("reason") == "no_movement")
    check("G: legacy seq id spelling resolves; blocked_on falls back to the "
          "commitment's counterparty",
          by_id.get("cmt_g", {}).get("blocked_on") == "Michele Sample",
          by_id.get("cmt_g"))
    check("H: staged draft is movement but never blocks",
          "cmt_h" not in stuck_ids and "cmt_h" not in blocked_ids)
    check("STUCK_DAYS is the documented 21", STUCK_DAYS == 21)

    ids, seqs = event_commitment_refs(
        {"data": {"commitment_id": "x", "target_id": "y", "commitment_seq": "7",
                  "source_event_seq": 8, "target_seq": 9}})
    check("ref extractor honors all id + seq alias fields",
          ids == ["x", "y"] and seqs == [7, 8, 9], (ids, seqs))
    shutil.rmtree(root, ignore_errors=True)


def test_headline_export_and_surface_parity():
    print("\n[3] headline export: stuck/blocked keys, absence semantics, "
          "surface parity, deprecated alias untouched")
    events = [
        _commitment(1, 30, cid="cmt_a"),                       # stuck (no movement)
        _commitment(2, 40, cid="cmt_b"),                       # blocked
        {"seq": 11, "ts": _iso(3), "type": "outreach_sent", "source_skill": "commitments",
         "data": {"commitment_id": "cmt_b", "counterparty_name": "Pedro"}},
        _commitment(3, 40, cid="cmt_c"),                       # neither
        {"seq": 12, "ts": _iso(1), "type": "commitment_updated", "source_skill": "test",
         "data": {"commitment_id": "cmt_c"}},
    ]
    root = _workspace(events)
    opens = load_open_commitments(_events_path(root))
    movement = derive_commitment_movement(_events_path(root))

    with_m = count_commitments(opens, user_person_id=USER, now_iso=NOW_ISO,
                               movement=movement)
    without_m = count_commitments(opens, user_person_id=USER, now_iso=NOW_ISO)
    check("headline stuck = 2 (no-movement + blocked)",
          with_m["headline"]["stuck"] == 2, with_m["headline"])
    check("headline blocked = 1", with_m["headline"]["blocked"] == 1)
    check("no movement map -> stuck/blocked ABSENT (not 0)",
          "stuck" not in without_m["headline"] and "blocked" not in without_m["headline"],
          without_m["headline"])
    check("R4 partition invariant untouched",
          with_m["headline"]["you_owe"] + with_m["headline"]["owed_to_you"]
          + with_m["headline"]["unowned"] + with_m["headline"]["unconfirmed"]
          == with_m["headline"]["total"] == 3)
    check("deprecated top-level stuck alias still equals overdue (R1b meaning kept)",
          with_m["stuck"] == with_m["overdue"] == 0, (with_m["stuck"], with_m["overdue"]))

    brief = compute_brief_state(
        open_commitments=opens, user_person_id=USER, now_iso=NOW_ISO,
        commitment_movement=movement)["counts"]["headline"]
    wrapper = commitment_counts(root, user_person_id=USER, now_iso=NOW_ISO)["headline"]
    check("brief == chat/triage headline (incl. stuck/blocked)",
          brief == with_m["headline"], (brief, with_m["headline"]))
    check("brief == workspace wrapper headline (wrapper self-derives movement)",
          brief == wrapper, (brief, wrapper))
    shutil.rmtree(root, ignore_errors=True)


def test_shard_pruning():
    print("\n[4] shard pruning: pruned == full when the window covers all "
          "commitment activity; a pruned-away commitment vanishes (the contract)")
    this_year = NOW.year
    old1, old2 = this_year - 2, this_year - 1
    noise = lambda seq, year: {  # noqa: E731
        "seq": seq, "ts": f"{year}-06-01T10:00:00+00:00", "type": "meeting",
        "source_skill": "test", "data": {"title": "old sync"}}
    active = [
        _commitment(100, 30, cid="cmt_new"),
        _commitment(101, 40, cid="cmt_closed"),
        {"seq": 102, "ts": _iso(2), "type": "commitment_resolved", "source_skill": "test",
         "data": {"commitment_id": "cmt_closed", "resolution": "done",
                  "resolved_by": USER, "evidence": "test"}},
    ]
    root = _workspace(active, shards={
        old1: [noise(i, old1) for i in range(1, 11)],
        old2: [noise(i, old2) for i in range(11, 21)],
    })
    p = _events_path(root)
    full = load_open_commitments(p)
    pruned = load_open_commitments(p, since_ts=f"{this_year}-01-01")
    check("pruned load == full load for the window",
          [e["seq"] for e in full] == [e["seq"] for e in pruned] == [100],
          ([e["seq"] for e in full], [e["seq"] for e in pruned]))
    check("since_ts=None default returns the identical full projection",
          [e["seq"] for e in load_open_commitments(p)] == [100])

    # Cache separation: full and windowed results never serve each other.
    again_full = load_open_commitments(p)
    again_pruned = load_open_commitments(p, since_ts=f"{this_year}-01-01")
    check("memo cache keys full vs windowed separately",
          [e["seq"] for e in again_full] == [e["seq"] for e in again_pruned] == [100])
    shutil.rmtree(root, ignore_errors=True)

    # The contract violation, proven: a commitment CAPTURED in an old shard
    # is invisible to a pruned load — that is exactly why callers may pass
    # since_ts only when the window covers the whole commitment history.
    stranded = {"seq": 1, "ts": f"{old2}-03-01T10:00:00+00:00", "type": "commitment",
                "source_skill": "test",
                "data": {"id": "cmt_old", "title": "old promise", "status": "open",
                         "owner_id": USER}}
    root2 = _workspace([_commitment(50, 10, cid="cmt_now")], shards={old2: [stranded]})
    p2 = _events_path(root2)
    full2 = {e["data"]["id"] for e in load_open_commitments(p2)}
    pruned2 = {e["data"]["id"] for e in load_open_commitments(p2, since_ts=f"{this_year}-01-01")}
    check("full load sees the old-shard commitment", full2 == {"cmt_old", "cmt_now"}, full2)
    check("pruned load DROPS it (documented caller contract, not a safe default)",
          pruned2 == {"cmt_now"}, pruned2)
    shutil.rmtree(root2, ignore_errors=True)


def test_stale_tasks_movement():
    print("\n[5] stale_tasks keys on the same movement derivation")
    events = [
        _commitment(1, 40, cid="cmt_t1", kind="task"),   # untouched 40d -> stale
        _commitment(2, 40, cid="cmt_t2", kind="task"),   # updated 3d ago -> fresh
        {"seq": 10, "ts": _iso(3), "type": "commitment_updated", "source_skill": "test",
         "data": {"commitment_id": "cmt_t2"}},
    ]
    root = _workspace(events)
    opens = load_open_commitments(_events_path(root))
    movement = derive_commitment_movement(_events_path(root))
    with_m = {(e.get("data") or {}).get("id") for e in stale_tasks(opens, NOW_ISO, movement=movement)}
    without_m = {(e.get("data") or {}).get("id") for e in stale_tasks(opens, NOW_ISO)}
    check("with movement: recently-updated task is NOT stale",
          with_m == {"cmt_t1"}, with_m)
    check("without movement: capture-age fallback (pre-MC2 behavior)",
          without_m == {"cmt_t1", "cmt_t2"}, without_m)
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_derivation_and_classification()
    test_headline_export_and_surface_parity()
    test_shard_pruning()
    test_stale_tasks_movement()
    print(f"\n=== Summary: {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
