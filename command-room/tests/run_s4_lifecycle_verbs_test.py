#!/usr/bin/env python3
"""S4 lifecycle verbs + mute ledger (v4.6.0) — regression battery.

  [1] EDIT WORDING round-trip: the projection shows the corrected
      title/summary (newest wins per field, independently of the due fold);
      history keeps the original bytes; loud no-match; empty edit rejected.
  [2] REASSIGN: the item leaves the user's you-owe and lands on the new
      owner; an UNCONFIRMED reassignment counts as unconfirmed and never
      enters a chase-eligible bucket until confirmed; closed items refuse;
      the gate rejects hand-built id-less / target-less reassigns.
  [3] SPLIT: N Stage-D-complete children (minted ids, kind, inherited
      owner/counterparty, provenance -> the original) + the original closed
      via commitment_superseded with the split note; the dedup hook never
      flags a child against its own parent; the C4 merge fold is skipped
      for split closers; idempotent; pending_review floor holds.
  [4] UNMUTE round-trip: a live dismissal renders in the ledger with its
      remaining TTL; clear_dismissal makes it inactive (readers agree);
      idempotent over already-inactive; loud on unknown seqs; expired
      dismissals are not live.
  [5] BATCH UNDO clears its mutes (F-20 P3a): clear_dismissals lifts every
      cached dismissal; one bad seq never aborts the rest; a cleared
      dismissal stops suppressing the show-my-list discuss item it targeted.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from commitment_state import (  # noqa: E402
    CommitmentIdError,
    PendingReviewError,
    count_commitments,
    edit_commitment_wording,
    reassign_commitment,
    split_commitment,
)
from cru_match import _commitment_field, _is_pending_review, load_open_commitments  # noqa: E402
from event_gate import EventGateError, append_event  # noqa: E402
from mute_ledger import (  # noqa: E402
    DismissalNotFoundError,
    active_dismissal_target_ids,
    clear_dismissal,
    clear_dismissals,
    live_mutes,
)

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


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ws(events):
    root = tempfile.mkdtemp(prefix="cr-s4-")
    data_dir = os.path.join(root, "_hq", "data")
    os.makedirs(data_dir)
    with open(os.path.join(data_dir, "events.jsonl"), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return root


def _events_path(root):
    return os.path.join(root, "_hq", "data", "events.jsonl")


def _raw_lines(root):
    with open(_events_path(root), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _commitment(seq, cid, title, *, owner="person_user", counterparty=None,
                kind="promise", source_ref=None, pending=False, ts=None):
    data = {"id": cid, "title": title, "status": "open", "kind": kind,
            "owner_id": owner}
    if counterparty:
        data["counterparty_id"] = counterparty
    if source_ref:
        data["source_ref"] = source_ref
    if pending:
        data["pending_review"] = True
        data["review_reason"] = "low-confidence extraction"
    return {"seq": seq, "ts": ts or "2026-07-08T10:00:00Z", "type": "commitment",
            "source_skill": "meeting-notes", "primary_thread_id": "project_001",
            "data": data}


USER = "person_user"
ERICK = "person_erick"


# ---------------------------------------------------------------------------
print("[1] edit wording round-trip")
# ---------------------------------------------------------------------------
root = _ws([_commitment(1, "cmt_A", "send Michele the invoce", owner=USER)])
res = edit_commitment_wording(root, "cmt_A", edited_by=USER,
                              source_skill="commitment-triage",
                              new_title="send Michele the invoice")
check("edit returns updated", res["status"] == "updated" and res["commitment_id"] == "cmt_A")
opens = load_open_commitments(_events_path(root))
check("projection shows corrected title",
      len(opens) == 1 and _commitment_field(opens[0], "title") == "send Michele the invoice",
      f"got {opens and _commitment_field(opens[0], 'title')!r}")
check("projection records the folding seq",
      isinstance((opens[0].get("data") or {}).get("title_updated_by_seq"), int))
raw = _raw_lines(root)
check("history keeps the original wording",
      raw[0]["data"]["title"] == "send Michele the invoce")

# newest wins; summary folds independently of title and of the due fold
edit_commitment_wording(root, "cmt_A", edited_by=USER, source_skill="commitments",
                        new_summary="the June invoice, not July")
append_event(_events_path(root), [{
    "type": "commitment_updated", "source_skill": "commitments",
    "data": {"commitment_id": "cmt_A", "new_due": "2026-07-20"},
}], holder="test")
edit_commitment_wording(root, "cmt_A", edited_by=USER, source_skill="commitments",
                        new_title="send Michele the JUNE invoice")
opens = load_open_commitments(_events_path(root))
d = opens[0].get("data") or {}
check("newest title wins", d.get("title") == "send Michele the JUNE invoice")
check("summary fold independent", d.get("summary") == "the June invoice, not July")
check("due fold untouched by wording edits", d.get("due") == "2026-07-20")

try:
    edit_commitment_wording(root, "cmt_NOPE", edited_by=USER,
                            source_skill="commitments", new_title="x")
    check("unknown id is loud", False)
except CommitmentIdError:
    check("unknown id is loud", True)
try:
    edit_commitment_wording(root, "cmt_A", edited_by=USER,
                            source_skill="commitments")
    check("empty edit rejected", False)
except ValueError:
    check("empty edit rejected", True)

# ---------------------------------------------------------------------------
print("[2] reassign — routes, and never chases until confirmed")
# ---------------------------------------------------------------------------
root = _ws([_commitment(1, "cmt_B", "prep the board numbers", owner=USER)])
counts = count_commitments(load_open_commitments(_events_path(root)),
                           user_person_id=USER, now_iso="2026-07-09")
check("starts in you-owe", counts["you_owe"] == 1 and counts["headline"]["you_owe"] == 1)

res = reassign_commitment(root, "cmt_B", reassigned_by=USER,
                          source_skill="commitments", new_owner_id=ERICK,
                          new_owner_name="Erick Sample",
                          reason="not mine — Erick owns it", confirmed=False)
check("reassign returns reassigned", res["status"] == "reassigned")
opens = load_open_commitments(_events_path(root))
check("owner is the new person",
      _commitment_field(opens[0], "owner_id") == ERICK)
check("unconfirmed reassignment is pending_review", _is_pending_review(opens[0]))
counts = count_commitments(opens, user_person_id=USER, now_iso="2026-07-09")
check("leaves your you-owe", counts["you_owe"] == 0 and counts["headline"]["you_owe"] == 0)
check("not in the chaseable owed-to-you bucket until confirmed",
      counts["headline"]["owed_to_you"] == 0 and counts["headline"]["unconfirmed"] == 1,
      f"headline={counts['headline']}")

# confirmed reassignment (the W4b Theirs->[name] / named chat phrase path)
reassign_commitment(root, "cmt_B", reassigned_by=USER, source_skill="commitments",
                    new_owner_id=ERICK, new_owner_name="Erick Sample",
                    reason="user named the owner", confirmed=True)
opens = load_open_commitments(_events_path(root))
check("confirmed reassignment clears pending_review", not _is_pending_review(opens[0]))
counts = count_commitments(opens, user_person_id=USER, now_iso="2026-07-09")
check("confirmed lands on the new owner's side",
      counts["headline"]["owed_to_you"] == 1 and counts["headline"]["unconfirmed"] == 0)

try:
    reassign_commitment(root, "cmt_B", reassigned_by=USER, source_skill="x")
    check("target-less reassign rejected", False)
except ValueError:
    check("target-less reassign rejected", True)
try:
    append_event(_events_path(root), [{
        "type": "commitment_reassigned", "source_skill": "test",
        "data": {"commitment_id": "cmt_B"},
    }], holder="test")
    check("gate rejects reassign with no new owner/counterparty", False)
except EventGateError:
    check("gate rejects reassign with no new owner/counterparty", True)

from commitment_state import close_commitment  # noqa: E402
close_commitment(root, "cmt_B", resolved_by=USER, evidence="done",
                 source_skill="test", user_confirmed=True)
res = reassign_commitment(root, "cmt_B", reassigned_by=USER,
                          source_skill="commitments", new_owner_id=USER)
check("reassigning a closed item refuses honestly", res["status"] == "not_open")

# ---------------------------------------------------------------------------
print("[3] split — N Stage-D children + superseded parent + provenance")
# ---------------------------------------------------------------------------
root = _ws([_commitment(1, "cmt_C", "send Michele the positioning brief and the invoice",
                        owner=USER, counterparty="person_michele",
                        source_ref="granola:abc123")])
res = split_commitment(root, "cmt_C", [
    {"title": "send Michele the positioning brief"},
    {"title": "send Michele the invoice", "due": "2026-07-15"},
], split_by=USER, source_skill="commitment-triage", user_confirmed=True)
check("split returns split + 2 children",
      res["status"] == "split" and len(res["children"]) == 2)
opens = load_open_commitments(_events_path(root))
check("parent closed, children open",
      {_commitment_field(o, "title") for o in opens} ==
      {"send Michele the positioning brief", "send Michele the invoice"},
      f"opens={[_commitment_field(o, 'title') for o in opens]}")
for o in opens:
    od = o.get("data") or {}
    check(f"child Stage-D complete ({od.get('title', '?')[:24]}…)",
          str(od.get("id", "")).startswith("cmt_")
          and od.get("kind") == "promise"
          and od.get("owner_id") == USER
          and od.get("counterparty_id") == "person_michele"
          and od.get("source_event_seq") == 1
          and od.get("split_from") == "cmt_C"
          and od.get("source_ref") == "granola:abc123")
    check("dedup hook never flags a child against its parent",
          not _is_pending_review(o),
          f"review_reason={od.get('review_reason')!r}")
    check("merge fold skipped for split closers", "merged_from" not in od)
closer = [e for e in _raw_lines(root) if e["type"] == "commitment_superseded"]
check("one split closer, note names the parts",
      len(closer) == 1
      and closer[0]["data"]["split_into"] == res["children"]
      and closer[0]["data"]["evidence"].startswith("split into 2 items")
      and closer[0]["data"]["commitment_id"] == "cmt_C")
check("child due honored",
      any((o.get("data") or {}).get("due") == "2026-07-15" for o in opens))

res2 = split_commitment(root, "cmt_C", [{"title": "a"}, {"title": "b"}],
                        split_by=USER, source_skill="x", user_confirmed=True)
check("re-splitting a closed parent is idempotent", res2["status"] == "already_resolved")
try:
    split_commitment(root, res["children"][0], [{"title": "only one"}],
                     split_by=USER, source_skill="x")
    check("fewer than 2 children rejected", False)
except ValueError:
    check("fewer than 2 children rejected", True)

root = _ws([_commitment(1, "cmt_P", "maybe do a thing", pending=True)])
try:
    split_commitment(root, "cmt_P", [{"title": "a"}, {"title": "b"}],
                     split_by=USER, source_skill="x")
    check("pending_review floor holds for split", False)
except PendingReviewError:
    check("pending_review floor holds for split", True)

# ---------------------------------------------------------------------------
print("[4] unmute round-trip")
# ---------------------------------------------------------------------------
now = datetime.datetime.now(datetime.timezone.utc)
snooze_until = (now + datetime.timedelta(days=60)).isoformat()
root = _ws([])
append_event(_events_path(root), [{
    "type": "chat_dismissal", "source_skill": "commitments",
    "data": {"target_id": "cmt_X", "reason": "not_relevant",
             "snooze_until": snooze_until},
}], holder="test")
rows = live_mutes(_raw_lines(root), _now_iso())
check("live mute renders with remaining TTL",
      len(rows) == 1 and 58 < rows[0]["remaining_days"] <= 60
      and rows[0]["ttl_label"].endswith("days left"),
      f"rows={rows}")
check("liveness filter suppresses the target",
      active_dismissal_target_ids(_raw_lines(root), _now_iso()) == {"cmt_X"})
seq = rows[0]["seq"]
res = clear_dismissal(root, seq, cleared_by=USER, source_skill="show-my-list")
check("unmute clears", res["status"] == "cleared")
check("ledger empty after unmute", live_mutes(_raw_lines(root), _now_iso()) == [])
check("liveness filter released",
      active_dismissal_target_ids(_raw_lines(root), _now_iso()) == set())
res = clear_dismissal(root, seq, cleared_by=USER, source_skill="show-my-list")
check("second unmute is an honest no-op", res["status"] == "already_inactive")
try:
    clear_dismissal(root, 99999, cleared_by=USER, source_skill="show-my-list")
    check("unknown dismissal seq is loud", False)
except DismissalNotFoundError:
    check("unknown dismissal seq is loud", True)

old_ts = (now - datetime.timedelta(days=3)).isoformat()
root = _ws([{"seq": 1, "ts": old_ts, "type": "chat_dismissal",
             "source_skill": "commitments", "data": {"target_id": "cmt_Y"}}])
check("expired 24h dismissal is not live",
      live_mutes(_raw_lines(root), _now_iso()) == [])
res = clear_dismissal(root, 1, cleared_by=USER, source_skill="show-my-list")
check("clearing an expired mute is a no-op", res["status"] == "already_inactive")

# ---------------------------------------------------------------------------
print("[5] batch undo clears its mutes (F-20 P3a)")
# ---------------------------------------------------------------------------
root = _ws([
    {"seq": 1, "ts": "2026-07-08T10:00:00Z", "type": "commitment_to_discuss",
     "source_skill": "commitments", "data": {"source_event_seq": 90,
                                             "summary": "raise the retainer"}},
])
for tgt in (1, "cmt_Z"):
    append_event(_events_path(root), [{
        "type": "chat_dismissal", "source_skill": "commitment-triage",
        "data": {"target_id": tgt,
                 "snooze_until": (now + datetime.timedelta(days=1)).isoformat()},
    }], holder="test")
active = active_dismissal_target_ids(_raw_lines(root), _now_iso())
check("both batch mutes live", len(active) == 2)
seqs = [r["seq"] for r in live_mutes(_raw_lines(root), _now_iso())]
results = clear_dismissals(root, seqs + [424242], cleared_by=USER,
                           source_skill="commitment-triage")
check("batch clear lifts every real mute, errors the bad seq",
      [r["status"] for r in results[:2]] == ["cleared", "cleared"]
      and results[2]["status"] == "error",
      f"results={results}")
check("nothing suppressed after batch undo",
      active_dismissal_target_ids(_raw_lines(root), _now_iso()) == set())
# the discuss item its skip targeted re-surfaces (show-my-list filter parity)
discuss_suppressed = 1 in active_dismissal_target_ids(_raw_lines(root), _now_iso())
check("show-my-list discuss item re-surfaces after undo", not discuss_suppressed)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
