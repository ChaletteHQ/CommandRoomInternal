#!/usr/bin/env python3
"""Phase 2 Stage D acceptance test — the kinds split.

Pins the ratification condition (§3.1, 2026-07-01): the policy layer is a
CODE-LEVEL kind filter with a test that fails if any surface reads around the
projector — plus the gate's required-at-capture flip, the reclassification
fold, S4 undo (reopen), S5 task aging + promote, S6 migration markers, and
the C17 integrity flags.
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

import migrate_commitment_kinds as migrate  # noqa: E402
from commitment_state import (  # noqa: E402
    close_commitment,
    count_commitments,
    promote_task_to_commitment,
    reopen_commitment,
    stale_tasks,
)
from cru_match import (  # noqa: E402
    cru_eligible,
    load_open_commitments,
    match_send_to_commitments,
)

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


def commitment(seq, cid, title, *, kind=None, owner=USER, ts="2026-06-20T10:00:00Z",
               person_ids=None, **extra):
    data = {"id": cid, "title": title, "owner_id": owner, "status": "open"}
    if kind:
        data["kind"] = kind
    data.update(extra)
    ev = {"seq": seq, "ts": ts, "type": "commitment",
          "source_skill": "meeting-notes", "primary_thread_id": f"t{seq}", "data": data}
    if person_ids is not None:
        ev["person_ids"] = person_ids
    return ev


def main():
    print("=== Stage D — kinds split: policy layer / fold / undo / aging / migration ===\n")

    # ------------------------------------------------------------------
    print("[1] POLICY (ratified condition): task kind NEVER enters CRU matching")
    # ------------------------------------------------------------------
    # A task whose title matches the send PERFECTLY must still never resolve.
    task = commitment(1, "cmt_T", "send bob the pricing deck", kind="task",
                      person_ids=[USER, "person_bob"])
    promise = commitment(2, "cmt_P", "send bob the pricing deck", kind="promise",
                         person_ids=[USER, "person_bob"])
    results = match_send_to_commitments(
        open_commitments=[task, promise],
        sender_person_id=USER,
        recipient_person_ids=["person_bob"],
        subject="pricing deck",
        body="sending bob the pricing deck as promised",
    )
    matched = {r["commitment_id"] for r in results}
    check("perfect-score task is invisible to Path 1; promise matches",
          "cmt_T" not in matched and "cmt_P" in matched, f"{matched}")
    check("cru_eligible drops tasks and keeps everything else",
          [c["data"]["id"] for c in cru_eligible([task, promise])] == ["cmt_P"])
    # Reading around the projector: a raw event list STILL gets the filter,
    # because the filter lives inside the matchers, not at the call sites.
    raw = [{"seq": 9, "type": "commitment",
            "data": {"id": "cmt_RAW", "title": "send bob the pricing deck",
                     "owner_id": USER, "status": "open", "kind": "task"}}]
    around = match_send_to_commitments(
        open_commitments=raw, sender_person_id=USER,
        recipient_person_ids=["person_bob"], subject="pricing deck",
        body="sending bob the pricing deck",
    )
    check("a surface reading around the projector STILL can't match a task",
          around == [], f"{around}")

    # ------------------------------------------------------------------
    print("\n[2] reclassification fold — the marker is a read-side label change")
    # ------------------------------------------------------------------
    ws = make_workspace([
        commitment(1, "cmt_A", "Draft the QBR notes", kind="promise"),
        {"seq": 2, "ts": "2026-06-21T10:00:00Z", "type": "commitment_reclassified",
         "source_skill": "kind-migration-2026-07",
         "data": {"target_id": "cmt_A", "target_seq": 1, "new_kind": "task",
                  "reason": "S6 partition"}},
    ])
    opens = load_open_commitments(events_path(ws))
    check("projector applies the override (promise → task) with provenance",
          len(opens) == 1 and opens[0]["data"]["kind"] == "task"
          and opens[0]["data"]["kind_overridden_by_seq"] == 2,
          f"{opens and opens[0]['data']}")
    c = count_commitments(opens, user_person_id=USER, now_iso=NOW)
    check("by_kind counts the EFFECTIVE kind", c["by_kind"] == {"task": 1}, f"{c['by_kind']}")
    check("original event untouched on disk",
          read_events(ws)[0]["data"]["kind"] == "promise")

    # promote flips it back — additive marker, never delete/recreate.
    res = promote_task_to_commitment(ws, "cmt_A", source_skill="commitment-triage")
    evs = read_events(ws)
    opens = load_open_commitments(events_path(ws))
    check("promote appends a marker (label change) and the projector flips kind back",
          res["status"] == "reclassified"
          and len(evs) == 3 and evs[2]["type"] == "commitment_reclassified"
          and opens[0]["data"]["kind"] == "promise",
          f"res={res} n={len(evs)}")
    check("promote is idempotent at the effective-kind level",
          promote_task_to_commitment(ws, "cmt_A", source_skill="commitment-triage")
          ["status"] == "already_promise")
    check("a promoted item becomes CRU-eligible again",
          [x["data"]["id"] for x in cru_eligible(opens)] == ["cmt_A"])

    # ------------------------------------------------------------------
    print("\n[3] S4 undo — reopen is additive and order-aware")
    # ------------------------------------------------------------------
    ws = make_workspace([commitment(1, "cmt_U", "Send the recap", kind="promise")])
    close_commitment(ws, "cmt_U", resolved_by=USER, evidence="triaged: done",
                     source_skill="commitment-triage", user_confirmed=True)
    check("closed → not open", load_open_commitments(events_path(ws)) == [])
    res = reopen_commitment(ws, "cmt_U", reopened_by=USER, reason="triage undo",
                            source_skill="commitment-triage")
    opens = load_open_commitments(events_path(ws))
    check("reopen brings it back; tombstone stays in history",
          res["status"] == "reopened" and len(opens) == 1
          and any(e["type"] == "commitment_resolved" for e in read_events(ws)))
    check("reopen on an open item is a no-op",
          reopen_commitment(ws, "cmt_U", reopened_by=USER, reason="again",
                            source_skill="test")["status"] == "already_open")
    res2 = close_commitment(ws, "cmt_U", resolved_by=USER, evidence="re-done",
                            source_skill="commitment-triage", user_confirmed=True)
    check("a reopened item can be re-closed (idempotency is order-aware)",
          res2["status"] == "closed"
          and load_open_commitments(events_path(ws)) == [], f"{res2}")

    # ------------------------------------------------------------------
    print("\n[4] S5 task aging — 30-day staleness, code-enforced")
    # ------------------------------------------------------------------
    opens = [
        commitment(1, "cmt_OLD_T", "Old self task", kind="task", ts="2026-05-20T10:00:00Z"),
        commitment(2, "cmt_NEW_T", "Fresh self task", kind="task", ts="2026-06-25T10:00:00Z"),
        commitment(3, "cmt_OLD_P", "Old promise", kind="promise", ts="2026-05-01T10:00:00Z"),
    ]
    stale = stale_tasks(opens, NOW)
    check("only the 30d+ TASK is stale (old promises age on the aging view, not here)",
          [s["data"]["id"] for s in stale] == ["cmt_OLD_T"],
          f"{[s['data']['id'] for s in stale]}")

    # ------------------------------------------------------------------
    print("\n[5] S6 migration script — dry-run default, additive markers, no double-count")
    # ------------------------------------------------------------------
    fixture = [
        # self-owed, no counterparty signal → task
        commitment(1, "cmt_SELF", "Organize the reading list", kind="promise",
                   person_ids=[USER]),
        # requester present → stays promise (requester IS the counterparty)
        commitment(2, "cmt_REQ", "Send Sam the recap", kind="promise",
                   person_ids=[USER], requester_id="person_sam"),
        # other person involved → stays promise
        commitment(3, "cmt_OTHER", "Send Bob the deck", kind="promise",
                   person_ids=[USER, "person_bob"]),
        # someone else owns it → untouched
        commitment(4, "cmt_THEIRS", "Bob returns the contract", kind="promise",
                   owner="person_bob"),
        # self-owed but pending_review → confirm list, never silent
        commitment(5, "cmt_PEND", "Maybe draft the memo", kind="promise",
                   person_ids=[USER], pending_review=True),
        # already a task → nothing to do (idempotence base case)
        commitment(6, "cmt_ALREADY", "Existing task", kind="task", person_ids=[USER]),
    ]
    ws = make_workspace(fixture)
    before = read_events(ws)
    plan = migrate.analyze(ws)
    check("dry-run writes nothing", read_events(ws) == before)
    check("partition: exactly the bare self-owed promise goes to task",
          [r["target_id"] for r in plan["to_task"]] == ["cmt_SELF"],
          f"{plan['to_task']}")
    check("pending_review routed to confirm, never silently reclassified",
          [r["target_id"] for r in plan["needs_confirm"]] == ["cmt_PEND"])
    applied = migrate.apply_markers(ws, plan)
    after = read_events(ws)
    check("apply appends exactly the planned additive markers",
          applied["markers_written"] == 1
          and after[:len(before)] == before
          and after[-1]["type"] == "commitment_reclassified"
          and after[-1]["source_skill"] == "kind-migration-2026-07")
    opens = load_open_commitments(events_path(ws))
    kinds = {o["data"]["id"]: o["data"].get("kind") for o in opens}
    check("effective kinds after migration",
          kinds["cmt_SELF"] == "task" and kinds["cmt_REQ"] == "promise"
          and kinds["cmt_ALREADY"] == "task", f"{kinds}")
    plan2 = migrate.analyze(ws)
    check("re-run plans nothing (idempotent)", plan2["to_task"] == [], f"{plan2['to_task']}")

    # ------------------------------------------------------------------
    print("\n[6] C17 integrity flags — the Monday-note mutation detectors")
    # ------------------------------------------------------------------
    import integrity_check as ic
    ws = make_workspace([
        commitment(1, "cmt_OK", "Fine item", kind="promise"),
        # in-place mutated row (closed-family status, straight on the event)
        commitment(2, "cmt_MUT", "Mutated row", kind="promise", status="closed"),
        # _cleanup_* key residue
        {"seq": 3, "ts": "2026-06-21T10:00:00Z", "type": "interaction",
         "_cleanup_batch": "2026-05-28",
         "data": {"summary": "x", "source_ref": "mail:1"}},
    ])
    # fixture uses data.status override via commitment(**extra)
    findings = ic.run_checks(Path(ws))
    codes = {f.check for f in findings}
    check("C17.cleanup_keys fires on _cleanup_* residue", "C17.cleanup_keys" in codes, f"{codes}")
    check("C17.inplace_status fires on closed-family data.status",
          "C17.inplace_status" in codes, f"{codes}")

    # ------------------------------------------------------------------
    print("\n[7] source gates — producers classify at capture; surfaces wired")
    # ------------------------------------------------------------------
    def read(rel):
        return open(os.path.join(PLUGIN_ROOT, rel), encoding="utf-8").read()

    for rel in ("skills/meeting-notes/SKILL.md",
                "skills/scan-for-commitments/SKILL.md",
                "skills/inbox-triage/SKILL.md"):
        text = read(rel)
        check(f"{rel.split('/')[1]}: kind at capture + capture floor + rules file",
              "data.kind" in text and "capture floor" in text.lower()
              and "commitment-rules.md" in text)

    # v4.5.2 S2 — the verb registry lives in shared/scripts/verb_taxonomy.py;
    # the renderer derives CANONICAL_ACTIONS from the table.
    taxonomy = read("shared/scripts/verb_taxonomy.py")
    for verb in ('"drop"', '"not mine"', '"make task"', '"promote"', '"never track this"'):
        check(f"CANONICAL_ACTIONS carries triage verb {verb}", verb in taxonomy)

    check("CHAT_ACTION_WIDGET documents the triage surface + undo",
          "Commitment Triage" in read("shared/CHAT_ACTION_WIDGET.md")
          and "reopen_commitment" in read("shared/CHAT_ACTION_WIDGET.md"))
    check("apply-choices carries the commitment-triage dispatch",
          "commitment-triage" in read("skills/apply-choices/SKILL.md")
          and "reopen_commitment" in read("skills/apply-choices/SKILL.md"))
    check("triage skill exists with the Friday opt-in posture",
          "NOT first-install" in read("skills/commitment-triage/SKILL.md"))
    check("orchestrator + map + schedule wired",
          "commitment-triage" in read("skills/enable-command-room-schedules/references/orchestrator-map.json")
          and '"commitment-triage"' in read("shared/scripts/schedule_config.py")
          and "check_lateness" in read("skills/enable-command-room-schedules/references/orchestrator-commitment-triage.md"))
    check("daily Commitments widget filters task kind (surfacing only)",
          "Kind filter (Phase 2 Stage D" in read("skills/enable-command-room-schedules/references/orchestrator-commitments.md"))
    check("COMMITMENT_AGING excludes tasks",
          'kind != "task"' in read("references/VIEW_GENERATION.md"))
    check("COMMITMENT_SCHEMA documents required-at-capture + the policy layer",
          "REQUIRED AT CAPTURE" in read("shared/COMMITMENT_SCHEMA.md")
          and "cru_eligible" in read("shared/COMMITMENT_SCHEMA.md"))
    check("cleanup Monday note carries the C17 flag step",
          "C17" in read("skills/cleanup/SKILL.md"))

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
