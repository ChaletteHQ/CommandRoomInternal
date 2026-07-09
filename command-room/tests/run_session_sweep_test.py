#!/usr/bin/env python3
"""
Session-sweep write core (Phase 5 Memory layer, R1).

Exercises shared/scripts/session_sweep.py against a real-shape workspace copy
(fixtures/workspace_mini — live event/entity shapes, not toy data). Regresses
the invariants that make the nightly sweep safe:

  - dedup is on the CONTENT hash, never the session-level source_ref (two items
    of one session both land; re-running the whole batch recovers nothing);
  - recovered events go through append_event() so commitments get a cmt_<ulid>
    id + a title, and a kindless commitment fails loud;
  - deliverables land as notes tagged recovered_kind;
  - a session_sweep_run receipt lands on every run (incl. a zero-recovered
    no-op) and validate_sweep_ran reads it back;
  - the fixture's events.jsonl still parses + stays seq-monotonic after writes.
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
import session_sweep as ss  # noqa: E402

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


def _events(ws: Path) -> list[dict]:
    ep = ws / "_hq" / "data" / "events.jsonl"
    out = []
    for line in ep.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def test_hash_and_ref():
    print("test_hash_and_ref")
    check("source_ref is session:{id}", ss.session_source_ref("abc") == "session:abc")
    try:
        ss.session_source_ref("")
        check("empty session_id rejected", False)
    except ValueError:
        check("empty session_id rejected", True)
    h1 = ss.content_hash("session:s1", "decision", "Go with vendor B")
    h2 = ss.content_hash("session:s1", "decision", "go with vendor b")   # case/space normalized
    h3 = ss.content_hash("session:s1", "commitment", "Go with vendor B")  # type differs
    check("hash is 12 hex", len(h1) == 12 and all(c in "0123456789abcdef" for c in h1))
    check("hash normalizes case/whitespace", h1 == h2)
    check("hash separates by type", h1 != h3)


def test_sweep_recover_and_dedup():
    print("test_sweep_recover_and_dedup")
    ws = copy_fixture()
    before = len(_events(ws))
    items = [
        {"session_id": "s1", "type": "commitment",
         "summary": "Send Acme the revised pricing sheet",
         "data": {"kind": "task", "no_due": True},
         "person_ids": ["person_042"]},
        {"session_id": "s1", "type": "decision", "summary": "Go with vendor B for logistics"},
        {"session_id": "s1", "type": "interaction",
         "summary": "Caught up with Dana on the Q3 plan", "data": {"channel": "session"}},
        {"session_id": "s1", "type": "deliverable", "summary": "Drafted the board update memo"},
        # within-batch duplicate of the decision (same session + type + summary):
        {"session_id": "s1", "type": "decision", "summary": "Go with vendor B for logistics"},
    ]
    r = ss.sweep_and_receipt(ws, items, sessions_scanned=1)
    check("4 distinct items recovered (within-batch dup dropped)", r["events_recovered"] == 4, repr(r))
    check("1 skipped as dedup", r["skipped_dedup"] == 1, repr(r))
    check("by_type has all four families",
          r["by_type"] == {"commitment": 1, "decision": 1, "interaction": 1, "note": 1}, repr(r["by_type"]))

    evs = _events(ws)
    added = evs[before:]
    by_type = [e["type"] for e in added]
    check("receipt is the last event", added[-1]["type"] == "session_sweep_run", repr(by_type))
    check("four recovered + one receipt appended", len(added) == 5, repr(by_type))

    cmt = next(e for e in added if e["type"] == "commitment")
    note = next(e for e in added if e["type"] == "note")
    inter = next(e for e in added if e["type"] == "interaction")
    check("recovered commitment got a cmt_ id", str(cmt["data"].get("id", "")).startswith("cmt_"), repr(cmt["data"]))
    check("recovered commitment carries its kind", cmt["data"].get("kind") == "task")
    check("recovered commitment title set from summary",
          cmt["data"].get("title") == "Send Acme the revised pricing sheet")
    check("deliverable stored as note tagged recovered_kind", note["data"].get("recovered_kind") == "deliverable")
    check("interaction carries source_ref + summary (payload contract)",
          inter["data"].get("source_ref") == "session:s1" and bool(inter["data"].get("summary")))
    check("every recovered item shares the session provenance ref",
          all(e["data"].get("source_ref") == "session:s1"
              for e in added if e["type"] in ss.SWEEPABLE_TYPES))
    check("person_ids lifted to top level for linkage", cmt.get("person_ids") == ["person_042"])

    # Idempotency — re-running the same window recovers nothing.
    r2 = ss.sweep_and_receipt(ws, items, sessions_scanned=1)
    check("re-run recovers nothing (idempotent)", r2["events_recovered"] == 0, repr(r2))
    check("re-run skips all five", r2["skipped_dedup"] == 5, repr(r2))

    # Seq monotonicity preserved after our appends.
    seqs = [e.get("seq") for e in _events(ws) if isinstance(e.get("seq"), int)]
    check("seqs strictly increasing after writes", seqs == sorted(seqs) and len(seqs) == len(set(seqs)))


def test_kindless_commitment_fails_loud():
    print("test_kindless_commitment_fails_loud")
    ws = copy_fixture()
    try:
        ss.sweep_and_receipt(ws, [{"session_id": "s9", "type": "commitment",
                                   "summary": "owe Sam the deck"}], sessions_scanned=1)
        check("kindless commitment rejected", False)
    except ss.SweepItemError:
        check("kindless commitment rejected", True)


def test_noop_still_writes_receipt():
    print("test_noop_still_writes_receipt")
    ws = copy_fixture()
    before = len(_events(ws))
    r = ss.sweep_and_receipt(ws, [], sessions_scanned=0)
    check("empty window recovers nothing", r["events_recovered"] == 0)
    v = ss.validate_sweep_ran(ws)
    check("no-op run still lands a receipt (watchdog proof)", v["ran"] and v["ok"], repr(v))
    added = _events(ws)[before:]
    check("exactly one event appended (the receipt)",
          len(added) == 1 and added[0]["type"] == "session_sweep_run", repr(added))


def test_validate_since_ts():
    print("test_validate_since_ts")
    ws = copy_fixture()
    ss.sweep_and_receipt(ws, [], sessions_scanned=0)
    last = ss.validate_sweep_ran(ws)["last_ts"]
    check("receipt at/after its own ts is ok", ss.validate_sweep_ran(ws, since_ts=last)["ok"] is True)
    check("no receipt after a future cursor is not ok",
          ss.validate_sweep_ran(ws, since_ts="2999-01-01T00:00:00+00:00")["ok"] is False)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== session-sweep write core (Phase 5 / R1) ===")
    test_hash_and_ref()
    test_sweep_recover_and_dedup()
    test_kindless_commitment_fails_loud()
    test_noop_still_writes_receipt()
    test_validate_since_ts()
    print()
    if FAIL:
        print(f"FAIL — {FAIL} of {PASS + FAIL} checks failed")
        return 1
    print(f"OK — all {PASS} session-sweep checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
