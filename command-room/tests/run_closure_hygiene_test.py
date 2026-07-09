#!/usr/bin/env python3
"""Closure hygiene (v4.5.2 R1c) — state-check no-ops, the scan->append lock
span, explicit-id round-trip validation, and the report-only integrity audit.

  [1] double-close: the second close returns already_resolved and writes NO
      second tombstone (the 83-duplicate-tombstone class); batch path same.
  [2] concurrency: N threads racing to close the SAME id yield exactly one
      "closed" and one tombstone — the writer lock now spans scan->append,
      killing the read-then-write race between concurrent orchestrators.
      Also proves reentrancy: closing while already holding the lock works.
  [3] explicit-id write gate: custom string ids (commit_navid_… class)
      round-trip capture -> close; ids shaped like legacy seq aliases
      (bare digits / seq_N / event_N / commitment_seq_N) are REJECTED with
      guidance; padded ids are trimmed; whitespace-only ids get minted.
  [4] audit_closure_integrity: report-only — finds the orphan closure, the
      duplicate tombstones, the duplicate seqs, and classifies a legit
      thread closer as informational; substrate byte-identical after.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from commitment_state import (  # noqa: E402
    close_commitment,
    close_commitments,
)
from event_gate import EventGateError, append_event  # noqa: E402
from writer_lock import events_writer_lock  # noqa: E402

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


def _ws(events):
    root = tempfile.mkdtemp(prefix="cr-r1c-")
    data_dir = os.path.join(root, "_hq", "data")
    os.makedirs(data_dir)
    with open(os.path.join(data_dir, "events.jsonl"), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return root


def _events(root):
    p = os.path.join(root, "_hq", "data", "events.jsonl")
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _commitment(seq, cid, title="do the thing"):
    return {"seq": seq, "ts": "2026-07-01T10:00:00Z", "type": "commitment",
            "source_skill": "meeting-notes",
            "data": {"id": cid, "owner_id": "person_user", "title": title,
                     "status": "open", "kind": "promise"}}


def _tombstones(root, cid=None):
    return [e for e in _events(root) if e.get("type") == "commitment_resolved"
            and (cid is None or (e.get("data") or {}).get("commitment_id") == cid)]


def test_double_close_no_second_tombstone():
    print("\n[1] double-close returns already_resolved and writes NO second tombstone")
    root = _ws([_commitment(1, "cmt_a")])
    r1 = close_commitment(root, "cmt_a", resolved_by="person_user",
                          evidence="done", source_skill="apply-choices")
    check("first close lands", r1["status"] == "closed", r1)
    r2 = close_commitment(root, "cmt_a", resolved_by="person_user",
                          evidence="done again", source_skill="apply-choices")
    check("second close -> already_resolved", r2["status"] == "already_resolved", r2)
    check("exactly ONE tombstone on disk", len(_tombstones(root, "cmt_a")) == 1,
          _tombstones(root, "cmt_a"))

    # Batch path: same id twice in one batch — one close, one no-op note.
    root2 = _ws([_commitment(1, "cmt_b")])
    results = close_commitments(root2, [
        {"commitment_id": "cmt_b", "resolved_by": "person_user", "evidence": "e1"},
        {"commitment_id": "cmt_b", "resolved_by": "person_user", "evidence": "e2"},
    ], source_skill="apply-choices")
    statuses = sorted(r["status"] for r in results)
    check("batch: one closed + one already_resolved",
          statuses == ["already_resolved", "closed"], results)
    check("batch: exactly ONE tombstone", len(_tombstones(root2, "cmt_b")) == 1,
          _tombstones(root2, "cmt_b"))
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(root2, ignore_errors=True)


def test_concurrent_close_race():
    print("\n[2] the scan->append race: N racing closers produce ONE tombstone")
    root = _ws([_commitment(1, "cmt_race")])
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def closer(i):
        barrier.wait()
        try:
            r = close_commitment(root, "cmt_race", resolved_by="person_user",
                                 evidence=f"racer {i}", source_skill="commitments")
        except Exception as e:  # a timeout here would itself be a failure
            r = {"status": f"error:{type(e).__name__}"}
        with lock:
            results.append(r["status"])

    threads = [threading.Thread(target=closer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("exactly one racer closed", results.count("closed") == 1, results)
    check("the rest saw already_resolved",
          results.count("already_resolved") == 5, results)
    check("exactly ONE tombstone on disk after the race",
          len(_tombstones(root, "cmt_race")) == 1, _tombstones(root, "cmt_race"))

    # Reentrancy: closing while the caller already holds the writer lock.
    root2 = _ws([_commitment(1, "cmt_re")])
    events_path = os.path.join(root2, "_hq", "data", "events.jsonl")
    with events_writer_lock(events_path, holder="test-outer"):
        r = close_commitment(root2, "cmt_re", resolved_by="person_user",
                             evidence="nested", source_skill="commitments")
    check("close inside an already-held writer lock works (reentrant)",
          r["status"] == "closed", r)
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(root2, ignore_errors=True)


def test_explicit_id_gate():
    print("\n[3] explicit commitment ids: round-trip validation at the write gate")
    root = _ws([])
    events_path = os.path.join(root, "_hq", "data", "events.jsonl")

    # The custom-string class from the live substrate — MUST round-trip.
    custom = "commit_navid_2026-05-19_1"
    append_event(events_path, [{
        "type": "commitment", "source_skill": "meeting-notes",
        "data": {"id": custom, "owner_id": "person_user",
                 "title": "custom-id item", "kind": "promise"},
    }], holder="test")
    r = close_commitment(root, custom, resolved_by="person_user",
                         evidence="round trip", source_skill="apply-choices")
    check("custom-string id round-trips capture -> close",
          r["status"] == "closed" and r["commitment_id"] == custom, r)

    # Seq-alias-shaped explicit ids are rejected with guidance.
    for bad in ("86", "seq_86", "event_086", "commitment_seq_86", 42):
        try:
            append_event(events_path, [{
                "type": "commitment", "source_skill": "meeting-notes",
                "data": {"id": bad, "owner_id": "person_user",
                         "title": "bad id", "kind": "promise"},
            }], holder="test")
            check(f"id {bad!r} rejected", False, "gate accepted a seq-alias-shaped id")
        except EventGateError as e:
            ok = ("seq" in str(e) and "omit" in str(e)) or "non-string" in str(e)
            check(f"id {bad!r} rejected with guidance", ok, str(e)[:120])

    # Padded id is trimmed (stored form == lookup form).
    append_event(events_path, [{
        "type": "commitment", "source_skill": "meeting-notes",
        "data": {"id": "  commit_pad_x  ", "owner_id": "person_user",
                 "title": "padded", "kind": "promise"},
    }], holder="test")
    stored = [e for e in _events(root)
              if (e.get("data") or {}).get("title") == "padded"][0]
    check("padded explicit id trimmed at the gate",
          stored["data"]["id"] == "commit_pad_x", stored["data"])

    # Whitespace-only id -> minted cmt_<ulid> (same as absent).
    append_event(events_path, [{
        "type": "commitment", "source_skill": "meeting-notes",
        "data": {"id": "   ", "owner_id": "person_user",
                 "title": "blank id", "kind": "promise"},
    }], holder="test")
    stored = [e for e in _events(root)
              if (e.get("data") or {}).get("title") == "blank id"][0]
    check("whitespace-only id replaced with a minted cmt_<ulid>",
          str(stored["data"]["id"]).startswith("cmt_"), stored["data"])
    shutil.rmtree(root, ignore_errors=True)


def test_integrity_audit_report_only():
    print("\n[4] audit_closure_integrity — finds the known classes, changes nothing")
    from audit_closure_integrity import run_audit
    fixture = [
        _commitment(1, "cmt_a"),
        _commitment(2, "cmt_b"),
        # Legit closure of cmt_a, then a DUPLICATE tombstone on top (the
        # 83-class), written pre-R1c by a blind batch path.
        {"seq": 3, "ts": "2026-07-02T10:00:00Z", "type": "commitment_resolved",
         "source_skill": "apply-choices",
         "data": {"commitment_id": "cmt_a", "resolution": "done"}},
        {"seq": 4, "ts": "2026-07-02T10:00:05Z", "type": "commitment_resolved",
         "source_skill": "commitments",
         "data": {"commitment_id": "cmt_a", "resolution": "done"}},
        # The known TRUE orphan: an (effectively) empty resolution — no
        # resolvable reference at all (written before the gate existed).
        {"seq": 5, "ts": "2026-07-03T10:00:00Z", "type": "commitment_resolved",
         "source_skill": "log-resolution", "data": {}},
        # A thread_resolved that closes an actual THREAD (no commitment id
        # matches) — informational, NOT an orphan.
        {"seq": 6, "ts": "2026-07-03T11:00:00Z", "type": "thread_resolved",
         "source_skill": "workspace-manager",
         "data": {"thread_id": "thread_gtm_partner"}},
        # Duplicate seq (the cleanup-flagged 1655/1703/1957/3397 class).
        {"seq": 7, "ts": "2026-07-04T10:00:00Z", "type": "note",
         "source_skill": "session-sweep", "data": {}},
        {"seq": 7, "ts": "2026-07-04T10:00:01Z", "type": "note",
         "source_skill": "session-sweep", "data": {}},
        # Reopen + legitimate re-close: NOT a duplicate.
        {"seq": 8, "ts": "2026-07-05T10:00:00Z", "type": "commitment_resolved",
         "source_skill": "apply-choices",
         "data": {"commitment_id": "cmt_b", "resolution": "done"}},
        {"seq": 9, "ts": "2026-07-05T11:00:00Z", "type": "commitment_reopened",
         "source_skill": "commitment-triage",
         "data": {"commitment_id": "cmt_b", "reason": "triage undo"}},
        {"seq": 10, "ts": "2026-07-05T12:00:00Z", "type": "commitment_resolved",
         "source_skill": "commitment-triage",
         "data": {"commitment_id": "cmt_b", "resolution": "done"}},
    ]
    root = _ws(fixture)
    events_path = os.path.join(root, "_hq", "data", "events.jsonl")
    before = open(events_path, "rb").read()

    out = run_audit(root, limit=10)
    check("finds exactly the 1 true orphan (empty resolution)",
          len(out["orphans"]) == 1 and out["orphans"][0].get("seq") == 5,
          [(e.get("seq")) for e in out["orphans"]])
    check("finds exactly the 1 duplicate tombstone (cmt_a re-close)",
          len(out["duplicates"]) == 1 and out["duplicates"][0][0] == "cmt_a",
          [(c, e.get("seq")) for c, e in out["duplicates"]])
    check("reopen -> re-close is NOT a duplicate (cmt_b)",
          all(c != "cmt_b" for c, _ in out["duplicates"]), out["duplicates"])
    check("thread closer classified informational, not orphan",
          len(out["informational"]) == 1
          and out["informational"][0].get("seq") == 6, out["informational"])
    check("duplicate seq detected", list(out["dup_seqs"]) == [7], out["dup_seqs"])

    after = open(events_path, "rb").read()
    check("substrate byte-identical after the audit", before == after)
    src = open(os.path.join(PLUGIN_ROOT, "shared", "scripts",
                            "audit_closure_integrity.py"), encoding="utf-8").read()
    check("audit tool has no repair/apply path",
          'add_argument("--apply' not in src and '"w"' not in src
          and "atomic_append" not in src and "atomic_write" not in src, "")
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_double_close_no_second_tombstone()
    test_concurrent_close_race()
    test_explicit_id_gate()
    test_integrity_audit_report_only()
    print(f"\n=== Summary: {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
