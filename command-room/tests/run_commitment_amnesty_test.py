#!/usr/bin/env python3
"""Phase 2 Stage C acceptance test — F3 read-side amnesty + the one-time
repair script + F4 in-place-mutation kill, and (per the merge decision) the
closure PARITY / ROUND-TRIP test that runs only once amnesty is in.

The 2026-07-01 audit corpus this stage answers: 289 id-less/orphan closures
(≈252 auto-recoverable via the seq aliases) and 249 in-place status-mutated
rows (growing to 251 during the audit day).

IMPORTANT: the repair script is exercised on FIXTURES ONLY here. The live
run happens once, supervised, at dogfood time.
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

import repair_commitment_closures as repair  # noqa: E402
from commitment_state import (  # noqa: E402
    close_commitment,
    commitment_counts,
    compute_brief_state,
    count_commitments,
    load_open_commitments,
)
from event_gate import append_event  # noqa: E402

USER = "person_user"
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
        "people": [{"id": USER, "canonical_name": "Test User"}],
    }), encoding="utf-8")
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return ws


def events_path(ws):
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def read_events(ws):
    return [json.loads(l) for l in events_path(ws).read_text(encoding="utf-8").splitlines() if l.strip()]


def commitment(seq, cid, title, status="open", **extra):
    data = {"title": title, "owner_id": USER, "status": status}
    if cid:
        data["id"] = cid
    data.update(extra)
    return {"seq": seq, "ts": "2026-06-20T10:00:00Z", "type": "commitment",
            "source_skill": "meeting-notes", "primary_thread_id": f"t{seq}",
            "data": data}


def main():
    print("=== Stage C — F3 amnesty + repair script + F4 (Phase 2) ===\n")

    # ------------------------------------------------------------------
    print("[1] read-side amnesty — the seq aliases close dead letters with no write")
    # ------------------------------------------------------------------
    events = [
        commitment(10, "cmt_A", "Send Bob the deck"),
        commitment(11, "cmt_B", "Draft the SOW"),
        commitment(12, "cmt_C", "Reply to the investor"),
        # The workspace-manager catch-all class: closure carrying ONLY
        # source_event_seq (52 of these existed live).
        {"seq": 13, "ts": "2026-06-21T10:00:00Z", "type": "commitment_resolved",
         "source_skill": "workspace-manager",
         "data": {"source_event_seq": 10, "commitment_id": "cmt_A",
                  "resolved_by": USER, "evidence": "that's handled"}},
        # commitment_seq as a STRING digit, no usable id field content-wise.
        {"seq": 14, "ts": "2026-06-21T11:00:00Z", "type": "thread_resolved",
         "data": {"commitment_seq": "11", "kind": "commitment", "id": "wrapper_x"}},
        # seq alias pointing at a NON-commitment event — must match nothing.
        {"seq": 15, "ts": "2026-06-21T12:00:00Z", "type": "commitment_resolved",
         "data": {"commitment_id": "cmt_GHOST_TARGET", "source_event_seq": 13}},
    ]
    ws = make_workspace(events)
    opens = load_open_commitments(events_path(ws))
    titles = {(e.get("data") or {}).get("title") for e in opens}
    check("source_event_seq closes seq 10; string commitment_seq closes seq 11",
          titles == {"Reply to the investor"}, f"open titles={titles}")
    check("a seq alias pointing at a non-commitment event closes nothing extra",
          len(opens) == 1)

    # close_commitment's idempotency mirrors the amnesty chain.
    res = close_commitment(ws, "cmt_A", resolved_by=USER, evidence="x",
                           source_skill="test", user_confirmed=True)
    check("close_commitment sees a seq-alias closure as already_resolved (chains move together)",
          res["status"] == "already_resolved", f"{res}")

    # ------------------------------------------------------------------
    print("\n[2] repair script — preview mode analyzes, writes NOTHING")
    # ------------------------------------------------------------------
    fixture = [
        commitment(1, "cmt_OPEN1", "Send Rio the margin analysis"),
        commitment(2, "cmt_OPEN2", "Book the vendor walkthrough with Quinn"),
        commitment(3, "cmt_OPEN3", "Send the board deck to the directors"),
        commitment(4, None, "Legacy id-less commitment stays open"),
        # (a) orphan digit-string id → tier "seq" (read chain can't parse "2").
        {"seq": 5, "ts": "2026-06-21T10:00:00Z", "type": "commitment_resolved",
         "data": {"commitment_id": "2", "resolved_by": USER, "evidence": "done"}},
        # (b) id-less-in-effect closure (orphan id) with evidence matching
        # EXACTLY ONE open title at ≥0.8 → tier "title".
        {"seq": 6, "ts": "2026-06-21T11:00:00Z", "type": "commitment_resolved",
         "data": {"commitment_id": "cmt_ORPHAN_1",
                  "evidence": "Send Rio the margin analysis", "resolved_by": USER}},
        # (c) garbage → unrecoverable.
        {"seq": 7, "ts": "2026-06-21T12:00:00Z", "type": "commitment_resolved",
         "data": {"commitment_id": "cmt_ORPHAN_2",
                  "evidence": "totally unrelated text", "resolved_by": USER}},
        # (d) in-place-mutated row: closed-family status, no closure event.
        commitment(8, "cmt_MUT", "Mutated row from the cleanup chat",
                   status="closed"),
        # (e) pending_review target referenced by an orphan seq-spelling id —
        # must go to needs_confirm, never auto-closed.
        commitment(9, "cmt_PEND", "Uncertain extraction", pending_review=True),
        {"seq": 20, "ts": "2026-06-21T13:00:00Z", "type": "commitment_resolved",
         "data": {"commitment_id": "seq_9", "resolved_by": USER, "evidence": "maybe"}},
    ]
    ws = make_workspace(fixture)
    before = read_events(ws)
    plan = repair.analyze(ws)
    check("preview writes nothing", read_events(ws) == before)
    tiers = sorted(r["tier"] for r in plan["repairs"])
    check("plan: seq + title + mutation tiers found",
          tiers == ["mutation", "seq", "title"],
          f"tiers={tiers} repairs={plan['repairs']}")
    check("pending_review target routed to needs_confirm, not repairs",
          len(plan["needs_confirm"]) == 1
          and plan["needs_confirm"][0]["target_id"] == "cmt_PEND",
          f"{plan['needs_confirm']}")
    check("garbage closure reported unrecoverable for M's triage",
          any(u["closure_seq"] == 7 for u in plan["unrecoverable"]),
          f"{plan['unrecoverable']}")
    report = repair.render_report(plan)
    check("preview report says PREVIEW and nothing written",
          "PREVIEW" in report and "nothing written" in report)

    # ------------------------------------------------------------------
    print("\n[3] repair script — apply mode: snapshot first, additive tombstones only")
    # ------------------------------------------------------------------
    n_before = len(before)
    applied = repair.apply_repairs(ws, plan)
    after = read_events(ws)
    check("snapshot taken under _archive/ before any write",
          Path(applied["snapshot"]).exists()
          and "_archive" in applied["snapshot"]
          and "repair-snapshot" in applied["snapshot"],
          applied["snapshot"])
    snap_lines = Path(applied["snapshot"]).read_text(encoding="utf-8").splitlines()
    check("snapshot content == pre-repair events.jsonl",
          len(snap_lines) == n_before)
    check("history is additive — original lines untouched, tombstones appended",
          after[:n_before] == before and len(after) == n_before + applied["closed"],
          f"before={n_before} after={len(after)} closed={applied['closed']}")
    new_tombs = after[n_before:]
    check("3 tombstones written (seq, title, mutation tiers)",
          applied["closed"] == 3 and len(new_tombs) == 3, f"{applied}")
    check("tombstones carry source_skill closure-repair-2026-07 + repair_tier",
          all(t.get("source_skill") == "closure-repair-2026-07"
              and (t.get("data") or {}).get("repair_tier") in ("seq", "title", "mutation")
              for t in new_tombs),
          f"{new_tombs}")
    opens_after = load_open_commitments(events_path(ws))
    open_titles = {(e.get("data") or {}).get("title") for e in opens_after}
    # Closed by the repair: seq tier ("2" → cmt_OPEN2), title tier (cmt_OPEN1),
    # mutation tier (cmt_MUT was never open). Still open: the unmatched item,
    # the id-less legacy row, and the pending_review item awaiting M's confirm.
    check("repaired items closed; unmatched + pending_review + id-less stay open",
          open_titles == {"Send the board deck to the directors",
                          "Legacy id-less commitment stays open",
                          "Uncertain extraction"},
          f"{open_titles}")

    # Idempotent re-run: everything already resolved, nothing new written.
    plan2 = repair.analyze(ws)
    applied2 = repair.apply_repairs(ws, plan2)
    check("re-run is a no-op (idempotent)",
          applied2["closed"] == 0 and len(read_events(ws)) == len(after),
          f"{applied2}")

    # ------------------------------------------------------------------
    print("\n[4] parity / round-trip (runs AFTER amnesty per the merge decision)")
    # ------------------------------------------------------------------
    # Write-at-T0 / read-at-T+N through the REAL write path (gate) and the
    # REAL closure path, with a legacy dead-letter shape mixed in.
    ws = make_workspace([])
    ep = events_path(ws)
    append_event(ep, [
        {"type": "commitment", "source_skill": "meeting-notes",
         "primary_thread_id": "tA",
         "data": {"title": "Ship the proposal", "owner_id": USER,
                  "status": "open", "due": "2026-06-25", "kind": "promise"}},
        {"type": "commitment", "source_skill": "inbox-triage",
         "primary_thread_id": "tB",
         "data": {"title": "Chase the signed SOW", "owner_id": "person_bob",
                  "status": "open", "kind": "promise"}},
        {"type": "commitment", "source_skill": "scan-for-commitments",
         "primary_thread_id": "tC",
         "data": {"title": "Prep the QBR agenda", "owner_id": USER,
                  "status": "open", "due": "2026-06-28", "kind": "promise"}},
    ], holder="amnesty-test")
    evs = read_events(ws)
    ids = [e["data"]["id"] for e in evs]
    check("gate minted cmt_<ulid> ids at write time",
          all(i.startswith("cmt_") for i in ids), f"{ids}")

    # T+1: close one canonically, defer one, dead-letter-close one (legacy).
    close_commitment(ws, ids[0], resolved_by=USER, evidence="sent it",
                     source_skill="reconcile-sent")
    append_event(ep, [
        {"type": "commitment_updated", "source_skill": "commitments",
         "primary_thread_id": "tC",
         "data": {"commitment_id": ids[2], "new_due": "2026-07-20",  # DATE_GUARD_OK: due folded as data; counts take now_iso=NOW, never the real clock
                  "reason": "user push"}},
        # Legacy consumer still writing a seq-alias closure — amnesty reads it.
        {"type": "commitment_resolved", "source_skill": "workspace-manager",
         "primary_thread_id": "tB",
         "data": {"source_event_seq": evs[1]["seq"], "commitment_id": ids[1],
                  "resolved_by": USER, "evidence": "they delivered"}},
    ], holder="amnesty-test")

    opens = load_open_commitments(ep)
    check("round-trip: 1 open remains (closed + amnesty-closed both dropped)",
          len(opens) == 1
          and opens[0]["data"]["title"] == "Prep the QBR agenda", f"{len(opens)}")
    check("round-trip: deferred item carries the effective due (not overdue)",
          opens[0]["data"]["due"] == "2026-07-20")  # DATE_GUARD_OK: asserts the due-fold value itself, not a clock-derived status

    counts = commitment_counts(ws, now_iso=NOW)
    brief = compute_brief_state(open_commitments=opens, user_person_id=USER, now_iso=NOW)
    check("PARITY: len(opens) == commitment_counts.total == brief counts.total == 1",
          len(opens) == counts["total"] == brief["counts"]["total"] == 1,
          f"opens={len(opens)} counts={counts} brief={brief['counts']}")
    check("PARITY: stuck == 0 everywhere (deferral honored by every counter)",
          counts["stuck"] == 0 and brief["counts"]["stuck"] == 0)

    # ------------------------------------------------------------------
    print("\n[5] F4 — legacy in-place status=closed readable forever; no new mutation path")
    # ------------------------------------------------------------------
    ws = make_workspace([
        commitment(1, "cmt_L1", "Legacy closed row", status="closed"),
        commitment(2, "cmt_L2", "Legacy resolved row", status="resolved"),
        commitment(3, "cmt_L3", "Legacy superseded row", status="superseded"),
        commitment(4, "cmt_L4", "Still open", status="open"),
    ])
    opens = load_open_commitments(events_path(ws))
    check("legacy closed-family statuses excluded from the open set forever",
          len(opens) == 1 and opens[0]["data"]["id"] == "cmt_L4")
    c = count_commitments(opens, user_person_id=USER, now_iso=NOW)
    check("counts agree (legacy statuses honored by the counting API)",
          c["total"] == 1)

    def read(rel):
        return open(os.path.join(PLUGIN_ROOT, rel), encoding="utf-8").read()

    api = read("shared/WORKSPACE_API.md")
    check("WORKSPACE_API carries the F4 prohibition (in-place status mutation forbidden)",
          "F4" in api and "data.status" in api and "close_commitment" in api)
    schema = read("shared/COMMITMENT_SCHEMA.md")
    check("COMMITMENT_SCHEMA documents F3 amnesty + F4 prohibition + repair script",
          "commitment_seq" in schema and "F4" in schema
          and "repair_commitment_closures" in schema)
    mn = read("skills/meeting-notes/SKILL.md")
    check("meeting-notes profile-update step no longer invites event mutation",
          "PERSON.md profile TABLE ONLY" in mn and "NEVER edit the commitment event" in mn)
    wd = read("skills/workspace-manager/references/workspace-detail.md")
    check("workspace-detail commitment-table step no longer invites event mutation",
          "IN THIS MARKDOWN TABLE ONLY" in wd and "F4" in wd)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
