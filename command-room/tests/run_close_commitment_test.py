#!/usr/bin/env python3
"""Phase 2 Stage B acceptance test — close_commitment(), THE single closure
path (F2), plus the closer-migration source gates.

Pins the failure modes that produced the live dead-letter corpus (408 closures
ever written, only 43 matched):

1. **The bare-int closure (regression).** An artifact fired
   `log resolved: 86` and the closer wrote the tombstone `"86"` verbatim — it
   matched no commitment id, so the item stayed open forever while the UI said
   done. close_commitment normalizes every legacy seq spelling (int 86, "86",
   "seq_86", "event_086", "commitment_seq_86") to the canonical id.
2. **Orphan tombstones.** 74 closures carried ids matching nothing. A no-match
   id now raises CommitmentIdError and writes NOTHING.
3. **Tail-window idempotency.** log-resolution checked only the last 200
   lines; anything older could be re-closed. close_commitment checks the FULL
   resolved-id set.
4. **pending_review auto-resolve.** Never — PendingReviewError unless the
   caller passes user_confirmed=True from an explicit user action.

Fixtures use real event shapes written through the Phase 1 gate where
relevant (canonical envelope, gate-stamped seq/ts).
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

from commitment_state import (  # noqa: E402
    CommitmentIdError,
    PendingReviewError,
    close_commitment,
    close_commitments,
    load_open_commitments,
    normalize_commitment_id,
    _scan_commitment_index,
)

USER = "person_user"

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
        "people": [{"id": USER, "canonical_name": "Test User"}],
    }), encoding="utf-8")
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return ws


def read_events(ws):
    p = Path(ws) / "_hq" / "data" / "events.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def commitment(seq, cid, title, **data_extra):
    data = {"id": cid, "title": title, "owner_id": USER, "status": "open"}
    data.update(data_extra)
    return {"seq": seq, "ts": "2026-06-20T10:00:00Z", "type": "commitment",
            "source_skill": "meeting-notes", "primary_thread_id": f"t{seq}",
            "data": data}


def main():
    print("=== close_commitment — the single closure path (Phase 2 Stage B, F2) ===\n")

    # ------------------------------------------------------------------
    print("[1] the bare-int closure — every legacy seq spelling normalizes")
    # ------------------------------------------------------------------
    for raw in (86, "86", "seq_86", "event_086", "commitment_seq_86"):
        ws = make_workspace([commitment(86, "cmt_TARGET", "Send the recap")])
        res = close_commitment(
            ws, raw,
            resolved_by=USER, evidence="user marked complete",
            source_skill="log-resolution", user_confirmed=True,
        )
        evs = read_events(ws)
        closer = [e for e in evs if e.get("type") == "commitment_resolved"]
        opens = load_open_commitments(Path(ws) / "_hq" / "data" / "events.jsonl")
        check(
            f"{raw!r} → canonical id, item actually closes",
            res["status"] == "closed"
            and len(closer) == 1
            and closer[0]["data"]["commitment_id"] == "cmt_TARGET"
            and closer[0]["data"]["resolution"] == "done"
            and opens == [],
            f"res={res} closer={closer} opens={len(opens)}",
        )

    # A commitment with NO explicit data.id — the synthesized commitment_seq_<n>
    # fallback is its canonical id; a bare int still closes it.
    ws = make_workspace([{
        "seq": 42, "ts": "2026-06-20T10:00:00Z", "type": "commitment",
        "primary_thread_id": "t42",
        "data": {"title": "Id-less legacy commitment", "owner_id": USER, "status": "open"},
    }])
    res = close_commitment(ws, 42, resolved_by=USER, evidence="done",
                           source_skill="workspace-manager", user_confirmed=True)
    opens = load_open_commitments(Path(ws) / "_hq" / "data" / "events.jsonl")
    check("bare int closes an id-less commitment via the synthesized id",
          res["status"] == "closed"
          and res["commitment_id"] == "commitment_seq_42" and opens == [],
          f"res={res} opens={len(opens)}")

    # seq/ts came from the gate's auto-stamp, not the caller.
    evs = read_events(ws)
    closer = [e for e in evs if e["type"] == "commitment_resolved"][0]
    check("closure event is gate-stamped (seq + ts present)",
          isinstance(closer.get("seq"), int) and bool(closer.get("ts")),
          f"closer={closer}")
    check("closure inherits the commitment's primary_thread_id",
          closer.get("primary_thread_id") == "t42", f"{closer}")

    # ------------------------------------------------------------------
    print("\n[2] orphan tombstones refused — loud CommitmentIdError, nothing written")
    # ------------------------------------------------------------------
    ws = make_workspace([commitment(1, "cmt_REAL", "Real item")])
    for raw in ("999", "cmt_NOPE", "commitment_seq_777", ""):
        try:
            close_commitment(ws, raw, resolved_by=USER, evidence="x",
                             source_skill="test", user_confirmed=True)
            check(f"no-match {raw!r} raises", False, "no exception raised")
        except CommitmentIdError:
            check(f"no-match {raw!r} raises CommitmentIdError", True)
    check("nothing was written for refused ids",
          all(e["type"] == "commitment" for e in read_events(ws)),
          f"{read_events(ws)}")

    # ------------------------------------------------------------------
    print("\n[3] idempotency over the FULL resolved-id set (not last-200-lines)")
    # ------------------------------------------------------------------
    events = [commitment(1, "cmt_OLD", "Ancient item")]
    events.append({"seq": 2, "ts": "2026-06-21T10:00:00Z", "type": "commitment_resolved",
                   "source_skill": "log-resolution", "primary_thread_id": "t1",
                   "data": {"commitment_id": "cmt_OLD", "resolved_by": USER,
                            "evidence": "closed long ago"}})
    # 250 filler events push the closure far beyond any 200-line tail window.
    for i in range(3, 253):
        events.append({"seq": i, "ts": "2026-06-22T10:00:00Z", "type": "interaction",
                       "source_skill": "inbox-triage",
                       "data": {"summary": f"filler {i}", "source_ref": f"mail:{i}"}})
    ws = make_workspace(events)
    res = close_commitment(ws, "cmt_OLD", resolved_by=USER, evidence="re-close attempt",
                           source_skill="log-resolution", user_confirmed=True)
    n_closers = sum(1 for e in read_events(ws) if e["type"] == "commitment_resolved")
    check("re-close far beyond the tail window → already_resolved, no duplicate",
          res["status"] == "already_resolved" and n_closers == 1,
          f"res={res} closers={n_closers}")

    # Closure via a legacy alias spelling is also seen (mirror of the loader).
    ws = make_workspace([
        commitment(7, "cmt_SEVEN", "Aliased closure"),
        {"seq": 8, "ts": "2026-06-21T10:00:00Z", "type": "thread_resolved",
         "data": {"target_id": "cmt_SEVEN", "kind": "commitment"}},
    ])
    res = close_commitment(ws, "cmt_SEVEN", resolved_by=USER, evidence="x",
                           source_skill="test", user_confirmed=True)
    check("thread_resolved/target_id closure recognized (loader-chain mirror)",
          res["status"] == "already_resolved", f"{res}")

    # ------------------------------------------------------------------
    print("\n[4] pending_review floor — never auto-resolved")
    # ------------------------------------------------------------------
    ws = make_workspace([commitment(5, "cmt_PEND", "Uncertain extraction",
                                    pending_review=True)])
    try:
        close_commitment(ws, "cmt_PEND", resolved_by="sent_reconcile",
                         evidence="matched an outbound send", source_skill="reconcile-sent")
        check("auto path raises PendingReviewError", False, "no exception")
    except PendingReviewError:
        check("auto path raises PendingReviewError", True)
    check("nothing written by the refused auto-close",
          all(e["type"] == "commitment" for e in read_events(ws)))
    res = close_commitment(ws, "cmt_PEND", resolved_by=USER,
                           evidence="user confirmed done", source_skill="apply-choices",
                           user_confirmed=True)
    check("explicit user confirmation closes it", res["status"] == "closed", f"{res}")

    # ------------------------------------------------------------------
    print("\n[5] contract details — resolution vocabulary, evidence cap, extra_data")
    # ------------------------------------------------------------------
    ws = make_workspace([commitment(1, "cmt_A", "Item A"),
                         commitment(2, "cmt_B", "Item B")])
    try:
        close_commitment(ws, "cmt_A", resolved_by=USER, evidence="x",
                         source_skill="test", resolution="finished",
                         user_confirmed=True)
        check("invalid resolution rejected", False, "no exception")
    except ValueError as e:
        check("invalid resolution rejected", not isinstance(e, CommitmentIdError))
    res = close_commitment(ws, "cmt_A", resolved_by=USER, evidence="e" * 500,
                           source_skill="triage", resolution="dropped",
                           user_confirmed=True,
                           extra_data={"resolved_via_wrapper_seq": 99})
    ev = res["event"]
    check("resolution=dropped + evidence truncated + extra_data carried",
          ev["data"]["resolution"] == "dropped"
          and len(ev["data"]["evidence"]) == 200
          and ev["data"]["resolved_via_wrapper_seq"] == 99, f"{ev['data']}")
    check("extra_data cannot override canonical keys",
          close_commitment(ws, "cmt_B", resolved_by=USER, evidence="x",
                           source_skill="test", user_confirmed=True,
                           extra_data={"commitment_id": "cmt_FORGED"})
          ["event"]["data"]["commitment_id"] == "cmt_B")

    # ------------------------------------------------------------------
    print("\n[6] close_commitments batch — one bad id never loses the real closes")
    # ------------------------------------------------------------------
    ws = make_workspace([commitment(1, "cmt_X", "X"), commitment(2, "cmt_Y", "Y")])
    results = close_commitments(ws, [
        {"commitment_id": "cmt_X", "resolved_by": "sent_reconcile", "evidence": "sent"},
        {"commitment_id": "cmt_GHOST", "resolved_by": "sent_reconcile", "evidence": "sent"},
        {"commitment_id": "cmt_Y", "resolved_by": "sent_reconcile", "evidence": "sent"},
    ], source_skill="reconcile-sent")
    statuses = [r["status"] for r in results]
    opens = load_open_commitments(Path(ws) / "_hq" / "data" / "events.jsonl")
    check("batch: closed, error, closed — and both real items are closed",
          statuses == ["closed", "error", "closed"] and opens == [],
          f"statuses={statuses} opens={len(opens)}")

    # ------------------------------------------------------------------
    print("\n[7] normalize_commitment_id — pure normalizer contract")
    # ------------------------------------------------------------------
    ws = make_workspace([commitment(86, "cmt_TARGET", "Send the recap")])
    idx = _scan_commitment_index(Path(ws) / "_hq" / "data" / "events.jsonl")
    check("canonical id passes through",
          normalize_commitment_id("cmt_TARGET", idx) == "cmt_TARGET")
    check("zero-padded event_086 maps via seq",
          normalize_commitment_id("event_086", idx) == "cmt_TARGET")
    try:
        normalize_commitment_id("87", idx)
        check("unknown seq raises", False)
    except CommitmentIdError:
        check("unknown seq raises", True)

    # ------------------------------------------------------------------
    print("\n[8] source-side gates — every closer names the single path")
    # ------------------------------------------------------------------
    def read(rel):
        return open(os.path.join(PLUGIN_ROOT, rel), encoding="utf-8").read()

    closers = {
        "skills/log-resolution/SKILL.md": "log-resolution",
        "skills/apply-choices/SKILL.md": "apply-choices",
        "skills/workspace-manager/SKILL.md": "workspace-manager catch-all",
        "skills/meeting-notes/SKILL.md": "meeting-notes",
        "skills/follow-up-ritual/SKILL.md": "follow-up-ritual",
        "skills/calendar-writer/SKILL.md": "calendar-writer",
        "skills/enable-command-room-schedules/references/orchestrator-commitments.md":
            "orchestrator-commitments",
        "skills/enable-command-room-schedules/references/orchestrator-inbox.md":
            "orchestrator-inbox",
        "skills/enable-command-room-schedules/references/orchestrator-past-meetings.md":
            "orchestrator-past-meetings",
    }
    for rel, label in closers.items():
        text = read(rel)
        check(f"{label} closes via close_commitment", "close_commitment" in text)

    # No skill prose instructs build_commitment_resolved_event + append anymore.
    for rel in closers:
        text = read(rel)
        stale = ("build_commitment_resolved_event(" in text
                 and "close_commitment" not in text)
        check(f"{rel.split('/')[-1]} carries no un-migrated build-and-append closure",
              not stale)

    py = read("shared/scripts/reconcile_sent_commitments.py")
    check("reconcile-sent writes closures via close_commitments",
          "close_commitments" in py and "to_resolved_events(auto_close" not in py)

    widget = read("shared/CHAT_ACTION_WIDGET.md")
    check("widget contract: data.id embedded verbatim + close_commitment dispatch",
          "data.id" in widget and "verbatim" in widget and "close_commitment" in widget)

    sot = read("references/SOURCE_OF_TRUTH.md")
    check("SOURCE_OF_TRUTH names close_commitment as the canonical closure writer",
          "close_commitment" in sot)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
