#!/usr/bin/env python3
"""Runtime exercise stub for the objectives skill (SPEC OBJ1, DRAFT).

Maps to cr-skill-builder Gates 13/15/17: happy path + edge cases exercised
against a synthetic workspace built on the fly — never a real workspace.
Covers: the three binding paths end-to-end, the meeting-harvest gate, drift
math + the graceful death arc, closure idempotency, defensive degradation
(malformed objective thread + a corrupt events line), and a leak scan over
every user-facing line the render helpers produce.

Hand-rolled _ok/_fail (no pytest), exit 0 green / 1 fail — run_all.py
classifies by the runtime_exercise filename marker.
"""
import datetime
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))

_ok: list = []
_fail: list = []


def check(cond, msg):
    (_ok if cond else _fail).append(msg)
    if not cond:
        print("  FAIL:", msg)


TODAY = datetime.date.today()


def days_ago(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


# Leak tokens that must never reach a CEO-facing line (Gate 9 vocabulary).
LEAK_TOKENS = ("events.jsonl", "entities.json", "project_", "person_",
               "objective_review", "objective_report", "source_skill",
               "thread_id", "confidence", "Phase ", "seq")


def leak_scan(lines, where):
    for ln in lines:
        for tok in LEAK_TOKENS:
            check(tok not in ln, f"leak token {tok!r} in {where}: {ln!r}")


def build_ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {
        "version": 1,
        "people": [
            {"id": "person_001", "canonical_name": "Sam Sample",
             "is_primary_user": True},
            {"id": "person_002", "canonical_name": "Bo Sample"},
        ],
        "orgs": [],
        "threads": [
            {"id": "project_001", "canonical_name": "Acme pilot",
             "folder_name": "acme_pilot", "status": "active", "kind": "deal",
             "first_seen": days_ago(90),
             "deal": {"stage": "negotiating", "stage_entered": days_ago(10),
                      "opened_at": days_ago(90)}},
        ],
        "engagements": [],
    }
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def main():
    import objective_math as om
    import objective_state as os_

    ws = build_ws()

    # --- happy path: the three binding paths ------------------------------
    t_meet = os_.create_objective(
        ws, statement="Land three enterprise pilots",
        binding={"type": "meeting", "series_key": "Weekly Sales Sync",
                 "series_match": "title_and_people",
                 "series_people": ["person_002"]},
        owner_person_id="person_001", anchor_thread_id="project_001")
    t_self = os_.create_objective(
        ws, statement="Delegate the weekly ops review",
        binding={"type": "self"}, owner_person_id="person_002")
    t_act = os_.create_objective(
        ws, statement="Convert the Acme pilot into a paying account",
        binding={"type": "activity", "entity_ids": ["project_001"]})
    check(len(os_.list_open_objectives(ws)) == 3,
          "three objectives open, one per binding path")

    # --- meeting-harvest gate (the Step 5g contract) ----------------------
    cands = om.forum_objectives(os_.list_open_objectives(ws),
                                "Weekly Sales Sync!",
                                attendee_person_ids=["person_002"])
    check([c["thread_id"] for c in cands] == [t_meet["id"]],
          "forum gate matches exactly the bound objective")
    check(om.forum_objectives(os_.list_open_objectives(ws),
                              "Random other call",
                              attendee_person_ids=["person_002"]) == [],
          "forum gate is empty for an unrelated meeting (the common case)")

    r = os_.record_review(ws, t_meet["id"], status="at_risk",
                          source_ref="granola:m77",
                          context="Two pilots stuck at security review",
                          meeting_title="Weekly Sales Sync")
    check(r["status"] == "reviewed", "harvest records the stated status")
    r = os_.record_review(ws, t_meet["id"], status="on_track",
                          source_ref="granola:m77")
    check(r["status"] == "already_reviewed",
          "reprocessing the same meeting is a NO-OP (idempotent harvest)")

    # --- weekly-touch reply path ------------------------------------------
    r = os_.record_report(ws, t_self["id"], status="on_track",
                          note="Handbook drafted", reported_by="person_002")
    check(r["status"] == "reported", "self-report records the owner's word")

    # --- derived health end-to-end ----------------------------------------
    inputs = om.load_objective_inputs(ws)
    check(not inputs["skipped"], "clean substrate reads with zero skips")
    health = om.compute_objective_health(
        inputs["open_objectives"], objective_events=inputs["objective_events"],
        meeting_events=inputs["meeting_events"],
        deal_events=inputs["deal_events"],
        activity_by_thread=inputs["activity_by_thread"],
        threads_by_id=inputs["threads_by_id"],
        open_commitments=inputs["open_commitments"], today=TODAY,
        primary_user_id=inputs["primary_user_id"])
    by_id = {h["thread_id"]: h for h in health}
    check(by_id[t_meet["id"]]["status"]["value"] == "at_risk"
          and by_id[t_meet["id"]]["status"]["source"] == "review",
          "meeting path derives the stated review status")
    check(by_id[t_self["id"]]["status"]["value"] == "on_track",
          "self path derives the reported status")
    check(by_id[t_act["id"]]["status"]["kind"] in ("movement", "none"),
          "activity path never fabricates a directional status from quiet")
    check(by_id[t_meet["id"]]["suggested_move"] is not None,
          "an at-risk objective carries a suggested move (never a bare flag)")

    # --- render surfaces: leak scan ---------------------------------------
    names = {"person_001": "Sam", "person_002": "Bo"}
    lines = om.brief_lines(health, names_by_person_id=names)
    check(0 < len(lines) <= 2, "brief contribution is 1-2 lines")
    leak_scan(lines, "brief_lines")
    rows = om.recap_rows(health, names_by_person_id=names)
    check(len(rows) == 3, "recap renders one row per open objective")
    leak_scan(rows, "recap_rows")

    # --- graceful-death arc (self path, cadence starved) ------------------
    ws2 = build_ws()
    t2 = os_.create_objective(ws2, statement="A starved objective",
                              binding={"type": "self", "cadence_days": 7})
    ent_path = ws2 / "_hq" / "data" / "entities.json"
    ent = json.loads(ent_path.read_text(encoding="utf-8"))
    for t in ent["threads"]:
        if t["id"] == t2["id"]:
            t["objective"]["opened_at"] = days_ago(35)
    ent_path.write_text(json.dumps(ent), encoding="utf-8")
    inputs2 = om.load_objective_inputs(ws2)
    h2 = om.compute_objective_health(
        inputs2["open_objectives"], objective_events=[], meeting_events=[],
        deal_events=[], activity_by_thread={}, threads_by_id={},
        open_commitments=[], today=TODAY)
    check(h2[0]["drift"]["death_proposal"],
          "a long-starved self objective reaches the graceful-death ask")
    lines2 = om.brief_lines(h2)
    check(all("drifting" not in ln.lower() for ln in lines2),
          "death-pending objective emits no drift nag in the brief")
    check(any("check-in" in ln for ln in lines2),
          "the brief still notes it honestly (waiting on the weekly touch)")

    # --- closure idempotency ----------------------------------------------
    r1 = os_.complete_objective(ws, t_act["id"], outcome_note="Signed")
    r2 = os_.complete_objective(ws, t_act["id"])
    check(r1["status"] == "closed" and r2["status"] == "already_closed",
          "completion is idempotent")
    r1 = os_.archive_objective(ws, t_self["id"], outcome_note="Ran its course")
    check(r1["status"] == "closed" and r1["outcome"] == "archived",
          "archive closes with the reason kept")

    # --- defensive degradation --------------------------------------------
    ev_path = ws / "_hq" / "data" / "events.jsonl"
    with ev_path.open("a", encoding="utf-8") as f:
        f.write("{this is not json\n")
    inputs3 = om.load_objective_inputs(ws)
    check(len(inputs3["skipped"]) == 1,
          "a corrupt events line is skipped AND surfaced, never swallowed")
    check(isinstance(inputs3["open_objectives"], list),
          "readers survive a corrupt line")

    ent = json.loads((ws / "_hq" / "data" / "entities.json")
                     .read_text(encoding="utf-8"))
    ent["threads"].append({"id": "project_990", "canonical_name": "Hand-made",
                           "folder_name": "hand_made", "status": "active",
                           "kind": "objective", "first_seen": days_ago(1)})
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps(ent), encoding="utf-8")
    rows = os_.list_open_objectives(ws)
    mal = [r for r in rows if r["malformed"]]
    check(len(mal) == 1, "a malformed objective thread surfaces, not crashes")
    h3 = om.compute_objective_health(
        rows, objective_events=[], meeting_events=[], deal_events=[],
        activity_by_thread={}, threads_by_id={}, open_commitments=[],
        today=TODAY)
    check(any(r["malformed"] for r in h3),
          "health math carries the malformed row honestly")

    # --- writer refusals stay loud ----------------------------------------
    # F-1: refuse must trip on the BINDING, not on a closed objective. t_self
    # is archived above (line ~184), so record_review would refuse via the
    # open-guard for the wrong reason. Use a FRESH OPEN self-bound objective so
    # the refusal proves status-honesty-follows-the-binding, not just _require_open.
    t_self_open = os_.create_objective(
        ws, statement="Tighten the weekly close checklist",
        binding={"type": "self"}, owner_person_id="person_002")
    try:
        os_.record_review(ws, t_self_open["id"], status="on_track",
                          source_ref="granola:x")
        check(False, "review on a non-meeting-bound objective must refuse")
    except os_.ObjectiveStateError:
        check(True, "status honesty follows the binding (writer refuses)")

    print(f"\nruntime_exercise_objectives: {len(_ok)} ok, {len(_fail)} failed")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
