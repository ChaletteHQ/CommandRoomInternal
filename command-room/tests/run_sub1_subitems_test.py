#!/usr/bin/env python3
"""SPEC SUB1 — commitment sub-items (M ruling 2026-07-16, all 8 accepted).

Pins the whole family contract:

1. **Writer** (`commitment_state.add_subitems`): real-commitment children
   carrying `data.parent_id` (canonical id verbatim) + `data.parent_seq`;
   inheritance via the shared `_mint_child_commitments` loop; guards — ≥1
   titled child, closed parent → not_open, pending_review floor, one level
   deep, the D-8 cap (12 open children).
2. **Gate** (`event_gate`): parent_id sanity — non-string / self-parent /
   seq-alias-shaped all reject.
3. **Loader fold** (`cru_match.load_open_commitments`): child stamps
   (parent_id re-point, parent_title, parent_closed for orphans), parent
   stamps (subitem_ids, n_subitems_open/done, all_subitems_resolved,
   next_subitem_due annotation-only), C4 merge re-point.
4. **Counts D2** (`count_commitments`): top-level partition — parent with 3
   open children counts as 1; invariant holds; additive keys absent when no
   sub-items exist (byte-identical pre-SUB1 output).
5. **Closure D3** (`close_commitment`): OpenSubitemsError, cascade ordering
   (children first), pending_review child blocks an unconfirmed cascade
   atomically, reopen semantics (parent only; children individually).
6. **Chase D5**: `cru_eligible` excludes live sub-items (orphans stay);
   matchers downgrade a parent-with-open-children auto_resolve to
   pending_review; movement bubbles child activity to the parent.
7. **Dedup D6**: child never flags against its parent or a sibling.
8. **Surfaces D6**: triage view nests families; pagination family-atomic;
   split refuses a parent with open children.

Fixture dates are all strictly in the past (G14: past dates only move
further into the past); the as-of clock is pinned via now_iso.
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

import commitment_state  # noqa: E402
from commitment_state import (  # noqa: E402
    MAX_SUBITEMS_PER_PARENT,
    OpenSubitemsError,
    PendingReviewError,
    add_subitems,
    close_commitment,
    compute_brief_state,
    count_commitments,
    match_commitments_to_meetings,
    reopen_commitment,
    split_commitment,
    stale_tasks,
    supersede_commitment,
)
from cru_match import (  # noqa: E402
    _commitment_id,
    cru_eligible,
    load_open_commitments,
    match_send_to_commitments,
    parent_blocks_auto_resolve,
    partition_subitems,
)

USER = "person_user"
OTHER = "person_blake"
NOW = "2026-07-02T08:00:00Z"

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
        "people": [{"id": USER, "canonical_name": "Test User"},
                   {"id": OTHER, "canonical_name": "Blake Sample"}],
    }), encoding="utf-8")
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return ws


def events_path(ws):
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def read_events(ws):
    return [json.loads(l)
            for l in events_path(ws).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def commitment(seq, cid, title, ts="2026-06-20T10:00:00Z", **data_extra):
    data = {"id": cid, "title": title, "kind": "promise",
            "owner_id": USER, "status": "open"}
    data.update(data_extra)
    return {"seq": seq, "ts": ts, "type": "commitment",
            "source_skill": "meeting-notes", "primary_thread_id": f"t{seq}",
            "data": data}


def family_workspace():
    """One parent (seq 1) + a plain sibling item (seq 2), children added via
    the real writer."""
    ws = make_workspace([
        commitment(1, "cmt_PARENT", "Prepare the board pack",
                   counterparty_id=OTHER),
        commitment(2, "cmt_PLAIN", "Send Jordan the recap"),
    ])
    res = add_subitems(ws, "cmt_PARENT",
                       [{"title": "Draft the financials section",
                         "due": "2026-06-28"},
                        {"title": "Collect the KPI sheet"},
                        {"title": "Send the pack"}],
                       added_by=USER, source_skill="commitment-triage",
                       user_confirmed=True)
    return ws, res


def main():
    print("=== SUB1 — commitment sub-items ===\n")

    # ------------------------------------------------------------------
    print("[1] add_subitems writer — real-commitment children, inheritance")
    # ------------------------------------------------------------------
    ws, res = family_workspace()
    check("status subitems_added, 3 children",
          res["status"] == "subitems_added" and len(res["children"]) == 3, res)
    evs = read_events(ws)
    kids = [e for e in evs if (e.get("data") or {}).get("parent_id")]
    check("3 child commitment events on disk", len(kids) == 3)
    k0 = kids[0]["data"]
    check("child carries parent_id verbatim + parent_seq",
          k0["parent_id"] == "cmt_PARENT" and k0["parent_seq"] == 1, k0)
    check("child inherits kind/owner/counterparty",
          k0["kind"] == "promise" and k0["owner_id"] == USER
          and k0["counterparty_id"] == OTHER, k0)
    check("child got a fresh cmt_ id", k0["id"].startswith("cmt_")
          and k0["id"] != "cmt_PARENT")
    check("child override honored (due on child 0 only)",
          k0.get("due") == "2026-06-28"
          and "due" not in kids[1]["data"], k0)
    check("no closer written — the parent stays open (decomposition, not split)",
          not [e for e in evs if e.get("type") in
               ("commitment_resolved", "commitment_superseded")])

    # guards
    try:
        add_subitems(ws, "cmt_PARENT", [], added_by=USER,
                     source_skill="commitment-triage")
        check("empty children refused", False)
    except ValueError:
        check("empty children refused", True)
    try:
        add_subitems(ws, "cmt_PARENT", [{"title": "   "}], added_by=USER,
                     source_skill="commitment-triage")
        check("untitled child refused", False)
    except ValueError:
        check("untitled child refused", True)
    # one level deep — a child cannot become a parent
    try:
        add_subitems(ws, res["children"][0], [{"title": "grandchild"}],
                     added_by=USER, source_skill="commitment-triage")
        check("grandchild refused (one level deep)", False)
    except ValueError as e:
        check("grandchild refused (one level deep)", "sub-item" in str(e))
    # D-8 cap: 3 open already; 10 more would make 13
    try:
        add_subitems(ws, "cmt_PARENT",
                     [{"title": f"step {i}"} for i in range(10)],
                     added_by=USER, source_skill="commitment-triage")
        check("cap 12 enforced (loud error above)", False)
    except ValueError as e:
        check("cap 12 enforced (loud error above)",
              str(MAX_SUBITEMS_PER_PARENT) in str(e))
    # exactly-at-cap is allowed (3 + 9 = 12)
    res12 = add_subitems(ws, "cmt_PARENT",
                         [{"title": f"step {i}"} for i in range(9)],
                         added_by=USER, source_skill="commitment-triage")
    check("cap boundary: 12 open children allowed",
          res12["status"] == "subitems_added")
    # closed parent → not_open
    ws2 = make_workspace([commitment(1, "cmt_X", "Done thing")])
    close_commitment(ws2, "cmt_X", resolved_by=USER, evidence="done",
                     source_skill="test", user_confirmed=True)
    r = add_subitems(ws2, "cmt_X", [{"title": "late step"}], added_by=USER,
                     source_skill="commitment-triage")
    check("closed parent → not_open", r["status"] == "not_open", r)
    # pending_review floor
    ws3 = make_workspace([commitment(1, "cmt_PR", "Flagged capture",
                                     pending_review=True)])
    try:
        add_subitems(ws3, "cmt_PR", [{"title": "step"}], added_by=USER,
                     source_skill="commitment-triage")
        check("pending_review floor (unconfirmed refused)", False)
    except PendingReviewError:
        check("pending_review floor (unconfirmed refused)", True)
    r = add_subitems(ws3, "cmt_PR", [{"title": "step"}], added_by=USER,
                     source_skill="commitment-triage", user_confirmed=True)
    check("pending_review + user_confirmed=True proceeds",
          r["status"] == "subitems_added")

    # ------------------------------------------------------------------
    print("\n[2] event_gate parent_id sanity")
    # ------------------------------------------------------------------
    from event_gate import EventGateError, append_event
    ws4 = make_workspace([])
    for bad, label in ((123, "non-string"), ("", "empty"),
                       ("seq_7", "seq-alias-shaped"),
                       ("commitment_seq_9", "seq-alias-shaped 2")):
        try:
            append_event(events_path(ws4), [{
                "type": "commitment", "source_skill": "test",
                "data": {"id": f"cmt_ok_{label.replace(' ', '_')}",
                         "title": "x", "kind": "promise", "status": "open",
                         "parent_id": bad}}], holder="test")
            check(f"gate rejects {label} parent_id", False)
        except EventGateError:
            check(f"gate rejects {label} parent_id", True)
    try:
        append_event(events_path(ws4), [{
            "type": "commitment", "source_skill": "test",
            "data": {"id": "cmt_self", "title": "x", "kind": "promise",
                     "status": "open", "parent_id": "cmt_self"}}],
            holder="test")
        check("gate rejects self-parent", False)
    except EventGateError:
        check("gate rejects self-parent", True)

    # ------------------------------------------------------------------
    print("\n[3] loader fold — parent/child stamps")
    # ------------------------------------------------------------------
    ws, res = family_workspace()
    c1, c2, c3 = res["children"]
    opens = load_open_commitments(events_path(ws))
    parent = next(c for c in opens if _commitment_id(c) == "cmt_PARENT")
    pd = parent["data"]
    check("parent stamps: subitem_ids in append order",
          pd.get("subitem_ids") == [c1, c2, c3], pd.get("subitem_ids"))
    check("parent stamps: 3 open / 0 done",
          pd.get("n_subitems_open") == 3 and pd.get("n_subitems_done") == 0)
    check("all_subitems_resolved ABSENT while children open",
          "all_subitems_resolved" not in pd)
    check("next_subitem_due = min open-child effective due",
          pd.get("next_subitem_due") == "2026-06-28")
    check("D-7: parent's own due UNTOUCHED (annotation only)",
          "due" not in pd or pd.get("due") in (None, ""), pd.get("due"))
    child = next(c for c in opens if _commitment_id(c) == c1)
    check("child stamps: parent_title",
          child["data"].get("parent_title") == "Prepare the board pack")
    check("child stamps: no parent_closed while parent open",
          "parent_closed" not in child["data"])

    # deferring the dated child shifts next_subitem_due (effective due)
    from event_gate import append_event as _append
    _append(events_path(ws), [{
        "type": "commitment_updated", "source_skill": "test",
        "data": {"commitment_id": c1, "new_due": "2026-07-01"}}],
        holder="test")
    opens = load_open_commitments(events_path(ws))
    parent = next(c for c in opens if _commitment_id(c) == "cmt_PARENT")
    check("next_subitem_due honors the child's DEFERRED (effective) due",
          parent["data"].get("next_subitem_due") == "2026-07-01",
          parent["data"].get("next_subitem_due"))

    # close two children → 1 open / 2 done; close last → propose stamp
    close_commitment(ws, c1, resolved_by=USER, evidence="done",
                     source_skill="test", user_confirmed=True)
    close_commitment(ws, c2, resolved_by=USER, evidence="done",
                     source_skill="test", user_confirmed=True)
    opens = load_open_commitments(events_path(ws))
    parent = next(c for c in opens if _commitment_id(c) == "cmt_PARENT")
    check("progress updates: 1 open / 2 done",
          parent["data"].get("n_subitems_open") == 1
          and parent["data"].get("n_subitems_done") == 2)
    close_commitment(ws, c3, resolved_by=USER, evidence="done",
                     source_skill="test", user_confirmed=True)
    opens = load_open_commitments(events_path(ws))
    parent = next(c for c in opens if _commitment_id(c) == "cmt_PARENT")
    check("last child closed → all_subitems_resolved stamped, parent STILL OPEN",
          parent["data"].get("all_subitems_resolved") is True)

    # orphan rule: cascade crash window simulated with a raw fixture
    ws5 = make_workspace([
        commitment(1, "cmt_P", "Parent thing"),
        commitment(2, "cmt_C", "Orphan step", parent_id="cmt_P", parent_seq=1,
                   ts="2026-06-21T10:00:00Z"),
        {"seq": 3, "ts": "2026-06-22T10:00:00Z", "type": "commitment_resolved",
         "source_skill": "test", "data": {"commitment_id": "cmt_P",
                                          "resolved_by": USER,
                                          "evidence": "x",
                                          "resolution": "done"}},
    ])
    opens = load_open_commitments(events_path(ws5))
    orphan = next(c for c in opens if _commitment_id(c) == "cmt_C")
    check("orphan child stamped parent_closed + keeps parent_title",
          orphan["data"].get("parent_closed") is True
          and orphan["data"].get("parent_title") == "Parent thing")
    top, subs = partition_subitems(opens)
    check("orphan partitions TOP-LEVEL (real open work, never vanishes)",
          [_commitment_id(c) for c in top] == ["cmt_C"] and not subs)

    # C4 merge re-point: children of a superseded parent belong to the survivor
    ws6 = make_workspace([
        commitment(1, "cmt_DUP", "Board pack (dup capture)"),
        commitment(2, "cmt_SURV", "Prepare the board pack"),
    ])
    add_subitems(ws6, "cmt_DUP", [{"title": "Step under dup"}],
                 added_by=USER, source_skill="commitment-triage",
                 user_confirmed=True)
    supersede_commitment(ws6, "cmt_SURV", "cmt_DUP", merged_by=USER,
                         source_skill="commitment-triage",
                         user_confirmed=True)
    opens = load_open_commitments(events_path(ws6))
    kid = next(c for c in opens if (c["data"].get("parent_id")))
    surv = next(c for c in opens if _commitment_id(c) == "cmt_SURV")
    check("merge re-point: child's parent_id → survivor (read-side)",
          kid["data"]["parent_id"] == "cmt_SURV"
          and "parent_closed" not in kid["data"], kid["data"])
    check("survivor carries the transferred child in its stamps",
          surv["data"].get("n_subitems_open") == 1)
    # ...and the WRITER honors the same re-point: closing the survivor
    # without close_subitems refuses.
    try:
        close_commitment(ws6, "cmt_SURV", resolved_by=USER, evidence="done",
                         source_skill="test", user_confirmed=True)
        check("writer sees transferred children (cascade guard fires)", False)
    except OpenSubitemsError:
        check("writer sees transferred children (cascade guard fires)", True)

    # ------------------------------------------------------------------
    print("\n[4] counts D2 — top-level partition + additive keys")
    # ------------------------------------------------------------------
    ws, res = family_workspace()
    opens = load_open_commitments(events_path(ws))
    counts = count_commitments(opens, user_person_id=USER, now_iso=NOW)
    h = counts["headline"]
    check("acceptance #2: total counts the parent once and no child",
          counts["total"] == 2 and h["total"] == 2, counts["total"])
    check("invariant over the top-level partition",
          h["you_owe"] + h["owed_to_you"] + h["unowned"] + h["unconfirmed"]
          == h["total"])
    check("subitems_open == 3", h.get("subitems_open") == 3)
    check("subitems_done_of_open_parents == 0",
          h.get("subitems_done_of_open_parents") == 0)
    check("by_kind counts top-level only",
          counts["by_kind"].get("promise") == 2, counts["by_kind"])
    # no-subitems workspace: byte-identical output (absent keys)
    ws7 = make_workspace([commitment(1, "cmt_A", "Plain item"),
                          commitment(2, "cmt_B", "Other item")])
    counts7 = count_commitments(load_open_commitments(events_path(ws7)),
                                user_person_id=USER, now_iso=NOW)
    check("no sub-items → additive keys ABSENT (never a guessed 0)",
          "subitems_open" not in counts7["headline"]
          and "subitems_done_of_open_parents" not in counts7["headline"])
    check("no sub-items → headline keys byte-identical to pre-SUB1",
          sorted(counts7["headline"].keys())
          == ["overdue", "owed_to_you", "total", "unconfirmed", "unowned",
              "you_owe"], sorted(counts7["headline"].keys()))
    # a closed child of an open parent feeds the done key
    close_commitment(ws, res["children"][0], resolved_by=USER,
                     evidence="done", source_skill="test",
                     user_confirmed=True)
    counts = count_commitments(load_open_commitments(events_path(ws)),
                               user_person_id=USER, now_iso=NOW)
    check("done child counts in subitems_done_of_open_parents",
          counts["headline"].get("subitems_done_of_open_parents") == 1
          and counts["headline"].get("subitems_open") == 2)

    # ------------------------------------------------------------------
    print("\n[5] closure D3 — refuse-until-confirm cascade")
    # ------------------------------------------------------------------
    ws, res = family_workspace()
    c1, c2, c3 = res["children"]
    try:
        close_commitment(ws, "cmt_PARENT", resolved_by=USER, evidence="done",
                         source_skill="test", user_confirmed=True)
        check("acceptance #4a: OpenSubitemsError without close_subitems", False)
    except OpenSubitemsError as e:
        check("acceptance #4a: OpenSubitemsError without close_subitems",
              "3 open" in str(e), str(e))
    n_before = len(read_events(ws))
    r = close_commitment(ws, "cmt_PARENT", resolved_by=USER, evidence="done",
                         source_skill="test", user_confirmed=True,
                         close_subitems=True)
    check("cascade closes, returns closed_subitems in close order",
          r["status"] == "closed"
          and r.get("closed_subitems") == [c1, c2, c3], r)
    evs = read_events(ws)[n_before:]
    closer_targets = [e["data"]["commitment_id"] for e in evs
                      if e.get("type") == "commitment_resolved"]
    check("acceptance #4b: children close FIRST, parent LAST",
          closer_targets == [c1, c2, c3, "cmt_PARENT"], closer_targets)
    check("child closers carry evidence 'parent closed'",
          all(e["data"]["evidence"] == "parent closed" for e in evs
              if e.get("type") == "commitment_resolved"
              and e["data"]["commitment_id"] != "cmt_PARENT"))
    opens = load_open_commitments(events_path(ws))
    check("whole family closed", len(opens) == 1
          and _commitment_id(opens[0]) == "cmt_PLAIN")
    # acceptance #4c: undo — reopen parent reopens PARENT ONLY; children
    # reopen individually (per-item tombstones)
    reopen_commitment(ws, "cmt_PARENT", reopened_by=USER,
                      reason="triage undo", source_skill="test")
    opens = load_open_commitments(events_path(ws))
    check("reopen(parent) reopens the parent only",
          {_commitment_id(c) for c in opens} == {"cmt_PARENT", "cmt_PLAIN"})
    for k in (c1, c2, c3):
        reopen_commitment(ws, k, reopened_by=USER, reason="triage undo",
                          source_skill="test")
    opens = load_open_commitments(events_path(ws))
    check("batch undo round-trips all four open",
          len(opens) == 5)  # parent + 3 children + plain

    # pending_review child blocks an UNCONFIRMED cascade atomically. The
    # capture-flagged shape is the one a direct close blocks on (children
    # are born confirmed via add_subitems, so this is the legacy/edge shape
    # the guard exists for — "exactly as it blocks a direct close").
    ws8 = make_workspace([
        commitment(1, "cmt_P8", "Parent"),
        commitment(2, "cmt_C8", "Flagged step", parent_id="cmt_P8",
                   parent_seq=1, pending_review=True,
                   ts="2026-06-21T10:00:00Z"),
    ])
    n_before = len(read_events(ws8))
    try:
        close_commitment(ws8, "cmt_P8", resolved_by="sent_reconcile",
                         evidence="auto", source_skill="reconcile-sent",
                         close_subitems=True)
        check("pending_review child blocks unconfirmed cascade", False)
    except PendingReviewError as e:
        check("pending_review child blocks unconfirmed cascade",
              "cmt_C8" in str(e), str(e))
    check("blocked cascade wrote NOTHING (atomic refuse)",
          len(read_events(ws8)) == n_before)
    # ...and the SAME cascade proceeds on an explicit user confirmation.
    r = close_commitment(ws8, "cmt_P8", resolved_by=USER, evidence="done",
                         source_skill="commitment-triage",
                         close_subitems=True, user_confirmed=True)
    check("confirmed cascade closes the flagged child too",
          r["status"] == "closed" and r.get("closed_subitems") == ["cmt_C8"])

    # split guard: a parent with open children refuses to split
    ws, res = family_workspace()
    try:
        split_commitment(ws, "cmt_PARENT",
                         [{"title": "A"}, {"title": "B"}],
                         split_by=USER, source_skill="commitment-triage",
                         user_confirmed=True)
        check("split refuses a parent with open sub-items", False)
    except ValueError as e:
        check("split refuses a parent with open sub-items",
              "sub-item" in str(e))
    r = split_commitment(ws, "cmt_PLAIN", [{"title": "A"}, {"title": "B"}],
                         split_by=USER, source_skill="commitment-triage",
                         user_confirmed=True)
    check("split still works on a childless item (regression)",
          r["status"] == "split" and len(r["children"]) == 2)

    # ------------------------------------------------------------------
    print("\n[6] chase D5 — cru_eligible, matcher downgrade, movement bubble-up")
    # ------------------------------------------------------------------
    ws, res = family_workspace()
    opens = load_open_commitments(events_path(ws))
    elig_ids = {_commitment_id(c) for c in cru_eligible(opens)}
    check("acceptance #5: live sub-items never in cru_eligible",
          elig_ids == {"cmt_PARENT", "cmt_PLAIN"}, elig_ids)
    # orphans stay eligible
    opens5 = load_open_commitments(events_path(ws5))
    check("orphan child IS cru-eligible (real open work)",
          {_commitment_id(c) for c in cru_eligible(opens5)} == {"cmt_C"})
    # parent_blocks_auto_resolve predicate
    parent = next(c for c in opens if _commitment_id(c) == "cmt_PARENT")
    plain = next(c for c in opens if _commitment_id(c) == "cmt_PLAIN")
    check("parent_blocks_auto_resolve: parent True / plain False",
          parent_blocks_auto_resolve(parent)
          and not parent_blocks_auto_resolve(plain))
    # matcher downgrade end-to-end (Path 1): identical high-score send —
    # the childless control auto-resolves, the parent proposes.
    ws9 = make_workspace([
        commitment(1, "cmt_CTRL", "Send Blake the board pack",
                   counterparty_id=OTHER),
        commitment(2, "cmt_PAR9", "Send Blake the board pack",
                   counterparty_id=OTHER),
    ])
    add_subitems(ws9, "cmt_PAR9", [{"title": "Collect the KPI sheet"}],
                 added_by=USER, source_skill="commitment-triage",
                 user_confirmed=True)
    opens9 = load_open_commitments(events_path(ws9))
    results = match_send_to_commitments(
        open_commitments=opens9, sender_person_id=USER,
        recipient_person_ids=[OTHER], recipient_names=["Blake"],
        subject="board pack", body="sending over the board pack now")
    rec_by_id = {r["commitment_id"]: r["recommendation"] for r in results}
    check("matcher: childless control auto-resolves",
          rec_by_id.get("cmt_CTRL") == "auto_resolve", rec_by_id)
    check("matcher: parent with open children downgrades to pending_review",
          rec_by_id.get("cmt_PAR9") == "pending_review", rec_by_id)
    check("matcher: the child itself never scored (cru_eligible)",
          not any(cid not in ("cmt_CTRL", "cmt_PAR9") for cid in rec_by_id))

    # movement bubble-up (acceptance #6): parent captured 40 days ago, child
    # closed yesterday-ish → parent NOT stuck.
    from commitment_activity import derive_commitment_movement
    ws10 = make_workspace([
        commitment(1, "cmt_OLD", "Big deliverable", ts="2026-05-20T10:00:00Z"),
        commitment(2, "cmt_STEP", "Step one", parent_id="cmt_OLD",
                   parent_seq=1, ts="2026-05-20T10:05:00Z"),
        commitment(3, "cmt_LONE", "Untouched other item",
                   ts="2026-05-20T10:10:00Z"),
        {"seq": 4, "ts": "2026-07-01T10:00:00Z", "type": "commitment_resolved",
         "source_skill": "test", "data": {"commitment_id": "cmt_STEP",
                                          "resolved_by": USER,
                                          "evidence": "done",
                                          "resolution": "done"}},
    ])
    movement = derive_commitment_movement(events_path(ws10))
    opens10 = load_open_commitments(events_path(ws10))
    counts10 = count_commitments(opens10, user_person_id=USER, now_iso=NOW,
                                 movement=movement)
    check("child closure bubbles: parent not stuck at 21+ days",
          counts10["headline"]["stuck"] == 1,  # only cmt_LONE
          counts10["headline"])
    check("parent's movement anchor is the child's closure",
          movement["cmt_OLD"].ts.isoformat().startswith("2026-07-01"))
    # stale_tasks: task-kind child of a moving parent is never nudged
    ws11 = make_workspace([
        commitment(1, "cmt_PT", "Active parent", ts="2026-05-01T10:00:00Z"),
        commitment(2, "cmt_TCH", "Old task step", kind="task",
                   parent_id="cmt_PT", parent_seq=1,
                   ts="2026-05-01T10:05:00Z"),
        {"seq": 3, "ts": "2026-07-01T10:00:00Z", "type": "commitment_updated",
         "source_skill": "test", "data": {"commitment_id": "cmt_PT",
                                          "new_due": "2026-07-01"}},
    ])
    movement11 = derive_commitment_movement(events_path(ws11))
    opens11 = load_open_commitments(events_path(ws11))
    stale = stale_tasks(opens11, NOW, movement=movement11)
    check("task child ages with the parent's movement (no nudge)",
          stale == [], [(_commitment_id(s)) for s in stale])

    # review fix F-1: after a C4 merge, a transferred child's activity must
    # bubble to the SURVIVOR's id — classify/stale look up movement by the
    # PROJECTED id, so a bubble landing on the merged-away id is invisible
    # and the survivor false-alarms "stuck" while its steps are moving.
    ws12 = make_workspace([
        commitment(1, "cmt_DUPP", "Board pack (dup capture)",
                   ts="2026-05-20T10:00:00Z"),
        commitment(2, "cmt_SRV", "Prepare the board pack",
                   ts="2026-05-20T10:02:00Z"),
        commitment(3, "cmt_STP", "Step one", parent_id="cmt_DUPP",
                   parent_seq=1, ts="2026-05-20T10:05:00Z"),
        {"seq": 4, "ts": "2026-05-21T10:00:00Z",
         "type": "commitment_superseded", "source_skill": "commitment-triage",
         "data": {"commitment_id": "cmt_DUPP", "superseded_by": "cmt_SRV",
                  "survivor_id": "cmt_SRV", "resolution": "duplicate"}},
        {"seq": 5, "ts": "2026-07-01T10:00:00Z", "type": "commitment_resolved",
         "source_skill": "test", "data": {"commitment_id": "cmt_STP",
                                          "resolved_by": USER,
                                          "evidence": "done",
                                          "resolution": "done"}},
    ])
    movement12 = derive_commitment_movement(events_path(ws12))
    check("merge re-point: transferred child's closure bubbles to the SURVIVOR",
          "cmt_SRV" in movement12
          and movement12["cmt_SRV"].ts.isoformat().startswith("2026-07-01"),
          {k: v.ts.isoformat() for k, v in movement12.items()})
    opens12 = load_open_commitments(events_path(ws12))
    counts12 = count_commitments(opens12, user_person_id=USER, now_iso=NOW,
                                 movement=movement12)
    check("merged survivor not stuck while its transferred step moves",
          counts12["headline"]["stuck"] == 0, counts12["headline"])

    # ------------------------------------------------------------------
    print("\n[7] dedup D6 — parent + sibling guards")
    # ------------------------------------------------------------------
    from commitment_dedup import score_suspected_duplicate
    ws, res = family_workspace()
    opens = load_open_commitments(events_path(ws))
    parent = next(c for c in opens if _commitment_id(c) == "cmt_PARENT")
    kid1 = next(c for c in opens if _commitment_id(c) == res["children"][0])
    new_child = {"parent_id": "cmt_PARENT", "parent_seq": 1,
                 "title": "Prepare the board pack — financials",
                 "owner_id": USER, "counterparty_id": OTHER}
    check("child never flags against its own parent",
          score_suspected_duplicate(new_child, parent) is None)
    check("child never flags against a sibling",
          score_suspected_duplicate(new_child, kid1) is None)
    unrelated = commitment(99, "cmt_UNREL",
                           "Prepare the board pack — financials",
                           counterparty_id=OTHER)
    check("child vs UNRELATED open still compares (guard is narrow)",
          score_suspected_duplicate(new_child, unrelated) is not None)

    # ------------------------------------------------------------------
    print("\n[8] surfaces D6 — brief rows, meeting-match, triage nesting, pagination")
    # ------------------------------------------------------------------
    ws, res = family_workspace()
    opens = load_open_commitments(events_path(ws))
    brief = compute_brief_state(open_commitments=opens, user_person_id=USER,
                                now_iso=NOW)
    na_ids = {r["commitment_id"] for r in brief["needs_attention"]}
    check("acceptance #5: no child in needs_attention",
          na_ids == {"cmt_PARENT", "cmt_PLAIN"}, na_ids)
    prow = next(r for r in brief["needs_attention"]
                if r["commitment_id"] == "cmt_PARENT")
    check("brief parent row carries progress fields",
          prow.get("n_subitems_open") == 3 and prow.get("n_subitems_done") == 0
          and prow.get("next_subitem_due") == "2026-06-28")
    plain_row = next(r for r in brief["needs_attention"]
                     if r["commitment_id"] == "cmt_PLAIN")
    check("no-subitem row carries NO progress keys (absent, not 0)",
          "n_subitems_open" not in plain_row)
    # meeting-match keeps seeing children, with the parent link (F-44)
    rows = match_commitments_to_meetings(
        opens, [{"meeting_id": "m1", "title": "Board sync",
                 "attendee_person_ids": [OTHER],
                 "attendee_names": ["Blake Sample"]}],
        user_person_id=USER)
    kid_rows = [r for r in rows if r.get("parent_id")]
    check("meeting-match sees children and stamps parent_id/parent_title",
          kid_rows and all(r["parent_id"] == "cmt_PARENT"
                           and r["parent_title"] == "Prepare the board pack"
                           for r in kid_rows), rows)

    # triage view nesting + family-atomic pagination (acceptance #7)
    from surface_drivers import build_commitment_triage_view
    view = build_commitment_triage_view(ws, now_iso=NOW)
    all_items = [it for s in view["sections"] for it in s["items"]]
    check("triage: children never top-level rows",
          {it["n"] for it in all_items} == {"cmt_PARENT", "cmt_PLAIN"},
          {it["n"] for it in all_items})
    fam = next(it for it in all_items if it["n"] == "cmt_PARENT")
    check("triage: family nests — 3 sub-rows with child ids VERBATIM",
          [s["id"] for s in fam["sub_items"]] == list(res["children"]))
    check("triage: progress chip in the parent's context tag",
          "sub-items 0/3" in fam["context_tag"], fam["context_tag"])
    check("triage: child verbs exclude never-track (parent-level only)",
          all("never track this" not in s["actions"]
              for s in fam["sub_items"]))
    check("triage: header appends the additive sub-items note",
          "(+3 sub-items)" in view["header"], view["header"])
    from chat_output_renderer import paginate_data_view, render_chat_output_widget
    html = render_chat_output_widget(view)
    check("triage widget renders through the full validator chain",
          all(cid in html for cid in res["children"]))
    sliced = paginate_data_view(view, page=1, page_size=1)
    page_items = [it for s in sliced["sections"] for it in s["items"]]
    check("family-atomic pagination: page holds the WHOLE family",
          len(page_items) == 1 and len(page_items[0].get("sub_items", [])) == 3
          and sliced["pagination"]["total_items"] == 2)
    # propose-closure line after the last child closes
    for k in res["children"]:
        close_commitment(ws, k, resolved_by=USER, evidence="done",
                         source_skill="test", user_confirmed=True)
    view = build_commitment_triage_view(ws, now_iso=NOW)
    fam = next(it for s in view["sections"] for it in s["items"]
               if it["n"] == "cmt_PARENT")
    check("triage: propose line renders when all sub-items done",
          fam.get("annotations") == ["all sub-items done — close it?"], fam)
    # orphan note
    view5 = build_commitment_triage_view(ws5, now_iso=NOW)
    orow = next(it for s in view5["sections"] for it in s["items"]
                if it["n"] == "cmt_C")
    check("triage: orphan renders top-level with 'was part of' note",
          "was part of: Parent thing" in orow["context_tag"],
          orow["context_tag"])

    # ------------------------------------------------------------------
    print("\n[9] instruction layer (G13) + verb registration")
    # ------------------------------------------------------------------
    from verb_taxonomy import taxonomy_row, required_input_thing
    row = taxonomy_row("add subitems [items]")
    check("verb row registered", row is not None
          and row["verb"] == "Add sub-items" and row["event"] == "commitment"
          and row["input"] == "required")
    check("required-input thing wired",
          required_input_thing("add subitems [items]") == "list of items")

    def read(rel):
        return (Path(PLUGIN_ROOT) / rel).read_text(encoding="utf-8")

    check("apply-choices dispatches add_subitems + cascade + undo caching",
          all(tok in read("skills/apply-choices/SKILL.md") for tok in
              ("add_subitems", "close_subitems=True", "closed_subitems",
               "OpenSubitemsError")))
    check("commitment-triage SKILL carries the sub-items contract",
          all(tok in read("skills/commitment-triage/SKILL.md") for tok in
              ("add_subitems", "all sub-items done — close it?",
               "OpenSubitemsError", "Family-atomic")))
    check("commitments orchestrator: children never chased + propose rules",
          all(tok in read("skills/enable-command-room-schedules/references/"
                          "orchestrator-commitments.md") for tok in
              ("add_subitems", "parent_blocks_auto_resolve",
               "Sub-item filter")))
    check("CHAT_ACTION_WIDGET disambiguates render-grouping vs data sub-items",
          "DATA relationship" in read("shared/CHAT_ACTION_WIDGET.md"))
    check("WORKSPACE_API names the one sub-item writer",
          "add_subitems" in read("shared/WORKSPACE_API.md"))
    check("extraction-never-hierarchies pinned in both extractor skills",
          "parent_id" in read("skills/scan-for-commitments/SKILL.md")
          and "parent_id" in read("skills/meeting-notes/SKILL.md"))

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
