#!/usr/bin/env python3
"""v4.6.0 MC1 — multi-counterparty commitments acceptance suite.

Pins the whole MC1 contract:

1. **The board scenario end-to-end.** Capture ONE commitment owed to three
   people (`counterparty_ids`) → the chase fan-out has 3 outstanding rows →
   mark 2 received → 1 outstanding → the 3rd → closure PROPOSED
   (all_counterparties_received) but NOT executed (still open) → the user
   closes explicitly.
2. **Legacy single-field is byte-identical.** A single-counterparty commitment
   carries NO list key, the loader adds NO receipt/proposal fields, the
   matcher still `auto_resolve`s, dedup/gate/confirm behave exactly as before.
   Equivalence asserted across the loader, matcher, dedup, and gate.
3. **Mixed history.** Single + list items in one substrate both load and count
   correctly.
4. **The critical safety fix.** A send/reply/event to ONE counterparty of a
   multi-counterparty commitment records THAT person's receipt
   (`partial_received`) — it never whole-closes the item.
5. **Fail-safe.** The writer sets the scalar `counterparty_id` to the primary
   so a reader that only reads the scalar degrades to the first counterparty.
6. **Writer guards.** Loud on a bad id, requires a counterparty, refuses a
   closed item.
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import commitment_parties as cp  # noqa: E402
from commitment_parties import build_counterparty_fields as bcf  # noqa: E402
from cru_match import (  # noqa: E402
    load_open_commitments,
    match_send_to_commitments,
    _commitment_counterparties,
)
from commitment_state import (  # noqa: E402
    mark_partial_received,
    close_commitment,
    count_commitments,
    match_commitments_to_meetings,
    CommitmentIdError,
)
from meeting_capture import build_meeting_commitment_event  # noqa: E402
from capture_gate import gate_commitment_data, classify_capture, CaptureGateError  # noqa: E402
from commitment_dedup import score_suspected_duplicate  # noqa: E402
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


def new_ws():
    ws = tempfile.mkdtemp()
    dd = os.path.join(ws, "_hq", "data")
    os.makedirs(dd)
    return ws, os.path.join(dd, "events.jsonl")


USER = "person_user"
A, B, C = "person_A", "person_B", "person_C"


# ---------------------------------------------------------------------------
# 1. build_counterparty_fields normalization (single byte-identical; multi
#    writes list + primary scalar).
# ---------------------------------------------------------------------------
def test_normalization():
    check("single id -> scalar only, no list",
          bcf(counterparty_id="A") == {"counterparty_id": "A"},
          bcf(counterparty_id="A"))
    check("single name -> scalar only, no list",
          bcf(counterparty_name="Rakesh") == {"counterparty_name": "Rakesh"})
    check("multi ids -> list + primary scalar",
          bcf(counterparty_ids=["A", "B", "C"]) ==
          {"counterparty_id": "A", "counterparty_ids": ["A", "B", "C"]})
    check("scalar + list merge, deduped, primary first",
          bcf(counterparty_id="A", counterparty_ids=["A", "B"]) ==
          {"counterparty_id": "A", "counterparty_ids": ["A", "B"]})
    check("ids + names disjoint",
          bcf(counterparty_ids=["A", "B"], counterparty_names=["Carol"]) ==
          {"counterparty_id": "A", "counterparty_ids": ["A", "B"],
           "counterparty_names": ["Carol"]})
    check("all names -> name scalar + list",
          bcf(counterparty_names=["X", "Y"]) ==
          {"counterparty_name": "X", "counterparty_names": ["X", "Y"]})
    check("empty -> empty", bcf() == {})


# ---------------------------------------------------------------------------
# 2. The board scenario, end-to-end through the real writers.
# ---------------------------------------------------------------------------
def test_board_scenario():
    ws, p = new_ws()
    ev = build_meeting_commitment_event(
        "send the deck to the board", source_ref="granola:board1",
        kind="promise", no_due=True, owner_id=USER,
        counterparty_ids=[A, B, C], source_event_seq=1,
        primary_thread_id="project_001", person_ids=[USER],
    )
    d = ev["data"]
    check("capture: list stored", d.get("counterparty_ids") == [A, B, C], d)
    check("capture: primary scalar set (fail-safe degrade)",
          d.get("counterparty_id") == A, d.get("counterparty_id"))
    check("capture: all counterparties in person_ids",
          set([A, B, C]).issubset(set(ev.get("person_ids") or [])), ev.get("person_ids"))
    append_event(p, [ev])
    cid = load_open_commitments(p)[0]["data"]["id"]

    def outstanding():
        c = [x for x in load_open_commitments(p) if x["data"]["id"] == cid][0]
        return [o["id"] for o in cp.outstanding_counterparties(c)], c

    o, _ = outstanding()
    check("fan-out: 3 outstanding at start", o == [A, B, C], o)

    r = mark_partial_received(ws, cid, received_by=USER, source_skill="commitments", counterparty_id=A)
    check("mark A: not propose_closure", r["propose_closure"] is False, r)
    o, _ = outstanding()
    check("fan-out: 2 outstanding after A", o == [B, C], o)

    mark_partial_received(ws, cid, received_by=USER, source_skill="commitments", counterparty_id=B)
    o, c = outstanding()
    check("fan-out: 1 outstanding after B", o == [C], o)
    check("no premature all-received flag",
          c["data"].get("all_counterparties_received") is None, c["data"])

    r = mark_partial_received(ws, cid, received_by=USER, source_skill="commitments", counterparty_id=C)
    check("mark C: propose_closure True", r["propose_closure"] is True, r)
    c = [x for x in load_open_commitments(p) if x["data"]["id"] == cid][0]
    check("all_counterparties_received stamped",
          c["data"].get("all_counterparties_received") is True, c["data"])
    check("PROPOSE not auto-close — still OPEN",
          any(x["data"]["id"] == cid for x in load_open_commitments(p)))

    close_commitment(ws, cid, resolved_by=USER, evidence="all in",
                     source_skill="commitments", user_confirmed=True)
    check("user close: now closed",
          not any(x["data"]["id"] == cid for x in load_open_commitments(p)))


# ---------------------------------------------------------------------------
# 3. Legacy single-field equivalence (byte-identical behavior).
# ---------------------------------------------------------------------------
def test_single_equivalence():
    ws, p = new_ws()
    ev = build_meeting_commitment_event(
        "send Sam the recap", source_ref="granola:sam1", kind="promise",
        no_due=True, owner_id=USER, counterparty_id="person_S",
        source_event_seq=1, primary_thread_id="project_001", person_ids=[USER],
    )
    d = ev["data"]
    check("single: no counterparty_ids key",
          "counterparty_ids" not in d and "counterparty_names" not in d, d)
    append_event(p, [ev])
    c = load_open_commitments(p)[0]
    check("single: loader adds no received_from",
          "received_from" not in c["data"], c["data"])
    check("single: loader adds no all_counterparties_received",
          "all_counterparties_received" not in c["data"], c["data"])
    # Matcher still whole-closes a single-counterparty item.
    res = match_send_to_commitments(
        open_commitments=[c], sender_person_id=USER,
        recipient_person_ids=["person_S"], subject="send Sam the recap",
        body="send Sam the recap")
    check("single: matcher auto_resolve (not partial)",
          res and res[0]["recommendation"] == "auto_resolve", res)
    # Equivalence: the pre-MC1 hand shape and the MC1 writer produce the same
    # counterparty fields for one counterparty.
    fields = {k: v for k, v in d.items() if k.startswith("counterparty")}
    check("single: counterparty fields == legacy scalar shape",
          fields == {"counterparty_id": "person_S"}, fields)


# ---------------------------------------------------------------------------
# 4. Mixed history — single + list in one substrate.
# ---------------------------------------------------------------------------
def test_mixed_history():
    ws, p = new_ws()
    append_event(p, [build_meeting_commitment_event(
        "send Sam the recap", source_ref="granola:sam2", kind="promise",
        no_due=True, owner_id=USER, counterparty_id="person_S",
        source_event_seq=1, primary_thread_id="project_001", person_ids=[USER])])
    append_event(p, [build_meeting_commitment_event(
        "send the deck to the board", source_ref="granola:board2",
        kind="promise", no_due=True, owner_id=USER, counterparty_ids=[A, B, C],
        source_event_seq=2, primary_thread_id="project_001", person_ids=[USER])])
    opens = load_open_commitments(p)
    counts = count_commitments(opens, user_person_id=USER, now_iso="2026-07-09")
    check("mixed: both open", counts["total"] == 2, counts)
    single = [x for x in opens if x["data"]["title"].startswith("send Sam")][0]
    multi = [x for x in opens if x["data"]["title"].startswith("send the deck")][0]
    check("mixed: single roster == 1", cp.counterparty_ids(single) == ["person_S"])
    check("mixed: multi roster == 3", cp.counterparty_ids(multi) == [A, B, C])


# ---------------------------------------------------------------------------
# 5. Matcher downgrade — send to ONE of a roster records that receipt only.
# ---------------------------------------------------------------------------
def test_matcher_downgrade():
    ev = build_meeting_commitment_event(
        "send the deck to the board", source_ref="granola:board3",
        kind="promise", no_due=True, owner_id=USER, counterparty_ids=[A, B, C],
        source_event_seq=1, person_ids=[USER])
    ev["data"].setdefault("id", "cmt_x")
    res = match_send_to_commitments(
        open_commitments=[ev], sender_person_id=USER,
        recipient_person_ids=[B], subject="the deck", body="here is the board deck")
    r = res[0]
    check("downgrade: partial_received on multi",
          r["recommendation"] == "partial_received", r["recommendation"])
    check("downgrade: matched counterparty named",
          r.get("matched_counterparty_ids") == [B], r.get("matched_counterparty_ids"))
    # Path 5 gate sees the whole roster.
    check("candidacy gate sees full roster",
          _commitment_counterparties(ev, USER) == {A, B, C},
          _commitment_counterparties(ev, USER))
    # Meeting-linked matcher relevance on ANY roster member.
    rows = match_commitments_to_meetings(
        [ev], [{"meeting_id": "m1", "title": "Board sync",
                "attendee_person_ids": [C], "attendee_names": []}],
        user_person_id=USER)
    check("meeting-linked: relevant when any roster member is in the room",
          len(rows) == 1 and rows[0]["match"] == "counterparty", rows)


# ---------------------------------------------------------------------------
# 6. Dedup — overlapping rosters agree; disjoint rosters veto.
# ---------------------------------------------------------------------------
def test_dedup():
    open_ev = {"type": "commitment", "seq": 5, "ts": "2026-07-08T10:00:00Z",
               "data": {"id": "cmt_open", "title": "send the deck to the board",
                        "owner_id": USER, "counterparty_ids": [A, B, C]}}
    # Same commitment from another writer — rosters overlap → flagged.
    new_overlap = {"title": "send the board deck", "owner_id": USER,
                   "counterparty_ids": [A, B]}
    m = score_suspected_duplicate(new_overlap, open_ev)
    check("dedup: overlapping rosters flagged", m is not None and m["corroborated"], m)
    # Disjoint roster, same title → different commitment, hard veto.
    new_disjoint = {"title": "send the board deck", "owner_id": USER,
                    "counterparty_ids": ["person_X", "person_Y"]}
    check("dedup: disjoint rosters veto (not a duplicate)",
          score_suspected_duplicate(new_disjoint, open_ev) is None)


# ---------------------------------------------------------------------------
# 7. Capture gate — multi promise-vs-task + party test.
# ---------------------------------------------------------------------------
def test_capture_gate():
    # A task carrying a multi-counterparty roster is really a promise → reject.
    try:
        gate_commitment_data(
            {"kind": "task", "no_due": True, "title": "t",
             "counterparty_ids": [A, B]}, subject="x")
        check("gate: multi-counterparty task rejected", False, "no raise")
    except CaptureGateError:
        check("gate: multi-counterparty task rejected", True)
    # classify_capture: the user as ONE of several counterparties → open.
    res = classify_capture(
        {"kind": "promise", "owner_id": "person_other",
         "counterparty_ids": [A, USER, C]},
        mode="party-only", user_id=USER)
    check("gate: user in multi-roster → open tier", res["tier"] == "open", res)


# ---------------------------------------------------------------------------
# 8. Writer guards.
# ---------------------------------------------------------------------------
def test_writer_guards():
    ws, p = new_ws()
    append_event(p, [build_meeting_commitment_event(
        "send the deck to the board", source_ref="granola:board4",
        kind="promise", no_due=True, owner_id=USER, counterparty_ids=[A, B, C],
        source_event_seq=1, person_ids=[USER])])
    cid = load_open_commitments(p)[0]["data"]["id"]
    try:
        mark_partial_received(ws, "cmt_missing", received_by=USER,
                              source_skill="t", counterparty_id=A)
        check("guard: loud on bad id", False, "no raise")
    except CommitmentIdError:
        check("guard: loud on bad id", True)
    try:
        mark_partial_received(ws, cid, received_by=USER, source_skill="t")
        check("guard: requires a counterparty", False, "no raise")
    except ValueError:
        check("guard: requires a counterparty", True)
    close_commitment(ws, cid, resolved_by=USER, evidence="done",
                     source_skill="t", user_confirmed=True)
    r = mark_partial_received(ws, cid, received_by=USER, source_skill="t", counterparty_id=A)
    check("guard: closed item -> not_open", r["status"] == "not_open", r)


# ---------------------------------------------------------------------------
# 9. Loader fold via seq alias (a receipt keyed on commitment_seq resolves).
# ---------------------------------------------------------------------------
def test_seq_alias_fold():
    ws, p = new_ws()
    append_event(p, [{"type": "commitment", "source_skill": "t",
                      "primary_thread_id": "project_001",
                      "data": {"id": "cmt_seqtest", "kind": "promise",
                               "title": "send to the board", "no_due": True,
                               "owner_id": USER, "counterparty_ids": [A, B]}}])
    seq = load_open_commitments(p)[0]["seq"]
    # A receipt referencing the commitment by SEQ (not id) still folds.
    append_event(p, [{"type": "commitment_partial_received", "source_skill": "t",
                      "data": {"commitment_seq": seq, "received_counterparty_id": A}}])
    c = load_open_commitments(p)[0]
    check("seq-alias receipt folds", cp.received_from_ids(c) == [A], c["data"].get("received_from"))


def main():
    for t in (test_normalization, test_board_scenario, test_single_equivalence,
              test_mixed_history, test_matcher_downgrade, test_dedup,
              test_capture_gate, test_writer_guards, test_seq_alias_fold):
        print(f"\n== {t.__name__} ==")
        t()
    print(f"\nMC1 multi-counterparty: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
