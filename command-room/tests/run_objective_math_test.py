#!/usr/bin/env python3
"""Tests for objective_math — derived status, drift, ranking, and the
render helpers (SPEC OBJ1, DRAFT). Pure-function tests over synthetic
inputs plus one end-to-end loader smoke on a temp workspace. All dates are
computed relative to today (G14 — no hardcoded future dates)."""
import datetime
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import objective_math as om  # noqa: E402
import objective_state as os_  # noqa: E402
from thread_activity import ThreadActivity  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


TODAY = datetime.date.today()


def days_ago(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


def mk_event(etype, thread_id, days, data=None, person_ids=None, seq=None):
    e = {"type": etype, "ts": days_ago(days) + "T12:00:00+00:00",
         "primary_thread_id": thread_id, "data": data or {}}
    if person_ids:
        e["person_ids"] = person_ids
    if seq is not None:
        e["seq"] = seq
    return e


def obj_row(tid, name, binding, anchor=None, opened_days=60, owner=None):
    obj = {"statement": name, "binding": binding,
           "opened_at": days_ago(opened_days)}
    if anchor:
        obj["anchor_thread_id"] = anchor
    return {"thread_id": tid, "name": name, "owner_person_id": owner,
            "status": "active", "objective": obj, "malformed": False}


MEETING_BINDING = {"type": "meeting", "series_key": "weekly sales sync",
                   "series_match": "title_and_people",
                   "series_people": ["person_002"]}

# --- matches_series ---------------------------------------------------------
ev = mk_event("meeting", None, 1, {"title": "Weekly Sales Sync!",
                                   "source_ref": "granola:a"},
              person_ids=["person_002", "person_009"])
check(om.matches_series(ev, MEETING_BINDING),
      "series matches on normalized title + attendee overlap")
ev_no_people = mk_event("meeting", None, 1, {"title": "Weekly Sales Sync",
                                             "source_ref": "granola:b"},
                        person_ids=["person_009"])
check(not om.matches_series(ev_no_people, MEETING_BINDING),
      "title_and_people refuses a title match with zero attendee overlap")
title_only = {"type": "meeting", "series_key": "l10",
              "series_match": "title_only"}
check(om.matches_series(mk_event("meeting", None, 1, {"title": " L10 "}),
                        title_only),
      "title_only matches regardless of attendees (distinctive-name mode)")
check(not om.matches_series(mk_event("meeting", None, 1, {"title": "L11"}),
                            title_only),
      "title_only still requires the title to match")

# --- forum_instances (dedup + after) ---------------------------------------
mtgs = [
    mk_event("meeting", None, 20, {"title": "L10", "source_ref": "g:1"}),
    mk_event("meeting_processed", None, 20, {"title": "L10",
                                             "source_ref": "g:1"}),
    mk_event("meeting", None, 6, {"title": "L10", "source_ref": "g:2"}),
]
inst = om.forum_instances(mtgs, title_only)
check(len(inst) == 2, "meeting + its processed receipt dedup by source_ref")
inst = om.forum_instances(mtgs, title_only,
                          after=TODAY - datetime.timedelta(days=10))
check(len(inst) == 1, "after= filters to strictly newer instances")

# --- meeting path: fresh review vs drift ------------------------------------
rows = [obj_row("project_100", "Land three enterprise pilots",
                dict(MEETING_BINDING))]
oe = [mk_event("objective_review", "project_100", 3,
               {"status": "on_track", "source_ref": "g:2"})]
health = om.compute_objective_health(
    rows, objective_events=oe, meeting_events=mtgs, deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY)
h = health[0]
check(h["status"]["value"] == "on_track" and h["status"]["source"] == "review"
      and not h["status"]["stale"],
      "meeting path: directional from the latest stated review")
check(not h["drift"]["flagged"], "fresh review => no drift")

# two forum sessions after the last review => drift + stale
old_review = [mk_event("objective_review", "project_100", 40,
                       {"status": "on_track", "source_ref": "g:0"})]
forum = [mk_event("meeting", None, 20,
                  {"title": "Weekly Sales Sync", "source_ref": "g:1"},
                  person_ids=["person_002"]),
         mk_event("meeting", None, 6,
                  {"title": "Weekly Sales Sync", "source_ref": "g:2"},
                  person_ids=["person_002"])]
health = om.compute_objective_health(
    rows, objective_events=old_review, meeting_events=forum, deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY)
h = health[0]
check(h["drift"]["flagged"] and "2 sessions" in h["drift"]["reason"],
      "2 undiscussed forum sessions => drift with readable reason")
check(h["status"]["stale"], "stale directional keeps value but flags stale")
check(h["suggested_move"] is not None,
      "a drifting objective always carries a suggested move")

# --- self path: cadence, drift, graceful death ------------------------------
self_rows = [obj_row("project_101", "Delegate the ops review",
                     {"type": "self", "cadence_days": 7}, owner="person_001")]
fresh = [mk_event("objective_report", "project_101", 2,
                  {"status": "at_risk"})]
h = om.compute_objective_health(
    self_rows, objective_events=fresh, meeting_events=[], deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY)[0]
check(h["status"]["value"] == "at_risk" and h["status"]["source"] == "report"
      and not h["drift"]["flagged"],
      "self path: fresh report => directional, no drift")
check(h["severity"] >= 3 and h["suggested_move"] is not None,
      "a stated at_risk carries severity + a suggested move")

stale_rep = [mk_event("objective_report", "project_101", 16,
                      {"status": "on_track"})]
h = om.compute_objective_health(
    self_rows, objective_events=stale_rep, meeting_events=[], deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY)[0]
check(h["drift"]["flagged"] and not h["drift"]["death_proposal"],
      "2 missed cycles => drift, not yet the graceful-death ask")

dead = [mk_event("objective_report", "project_101", 30,
                 {"status": "on_track"})]
h = om.compute_objective_health(
    self_rows, objective_events=dead, meeting_events=[], deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY)[0]
check(h["drift"]["death_proposal"],
      "4+ missed cycles => the 'is this still an objective?' ask")

# never-reported: baseline is opened_at
never = om.compute_objective_health(
    self_rows, objective_events=[], meeting_events=[], deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY)[0]
check(never["drift"]["flagged"],
      "never-reported self objective drifts from opened_at")

# --- activity path ----------------------------------------------------------
deal_thread = {"id": "project_001", "kind": "deal",
               "canonical_name": "Acme pilot",
               "deal": {"stage": "negotiating", "outcome": None}}
act_rows = [obj_row("project_102", "Convert the Acme pilot",
                    {"type": "activity", "entity_ids": ["project_001"]})]
recent = {"project_001": ThreadActivity(
    seq=5, event_type="meeting",
    ts=datetime.datetime.now(datetime.timezone.utc)
    - datetime.timedelta(days=2))}
h = om.compute_objective_health(
    act_rows, objective_events=[], meeting_events=[], deal_events=[],
    activity_by_thread=recent, threads_by_id={"project_001": deal_thread},
    open_commitments=[], today=TODAY)[0]
check(h["status"]["value"] == "moving" and h["status"]["kind"] == "movement",
      "activity path: recent linked activity => 'moving', not directional")
check(not h["drift"]["flagged"], "recent activity => no drift")

quiet = {"project_001": ThreadActivity(
    seq=5, event_type="meeting",
    ts=datetime.datetime.now(datetime.timezone.utc)
    - datetime.timedelta(days=30))}
h = om.compute_objective_health(
    act_rows, objective_events=[], meeting_events=[], deal_events=[],
    activity_by_thread=quiet, threads_by_id={"project_001": deal_thread},
    open_commitments=[], today=TODAY)[0]
check(h["drift"]["flagged"] and h["status"]["value"] == "quiet",
      "30 quiet days => drift + honest 'quiet' status")

# unambiguous deal signal turns directional
won_thread = {"id": "project_001", "kind": "deal",
              "canonical_name": "Acme pilot",
              "deal": {"outcome": "won", "closed_at": days_ago(3)}}
h = om.compute_objective_health(
    act_rows, objective_events=[], meeting_events=[], deal_events=[],
    activity_by_thread=recent, threads_by_id={"project_001": won_thread},
    open_commitments=[], today=TODAY)[0]
check(h["status"]["value"] == "on_track" and h["status"]["source"] == "deal_won",
      "a won linked deal is the unambiguous directional read")

stage_fwd = [mk_event("deal_stage_changed", "project_001", 4,
                      {"from_stage": "qualified", "to_stage": "negotiating"})]
h = om.compute_objective_health(
    act_rows, objective_events=[], meeting_events=[], deal_events=stage_fwd,
    activity_by_thread=recent, threads_by_id={"project_001": deal_thread},
    open_commitments=[], today=TODAY)[0]
check(h["status"]["value"] == "on_track" and h["status"]["source"] == "deal_stage",
      "a recent forward stage move reads on_track")
stage_back = [mk_event("deal_stage_changed", "project_001", 4,
                       {"from_stage": "negotiating", "to_stage": "qualified"})]
h = om.compute_objective_health(
    act_rows, objective_events=[], meeting_events=[], deal_events=stage_back,
    activity_by_thread=recent, threads_by_id={"project_001": deal_thread},
    open_commitments=[], today=TODAY)[0]
check(h["status"]["value"] == "at_risk",
      "a recent backward stage move reads at_risk")

# --- suggested move priority ------------------------------------------------
commitments = [{"thread_id": "project_001", "title": "Send revised proposal",
                "owner_id": "person_002", "due": days_ago(-3)}]
h = om.compute_objective_health(
    act_rows, objective_events=[], meeting_events=[], deal_events=[],
    activity_by_thread=quiet, threads_by_id={"project_001": deal_thread},
    open_commitments=commitments, today=TODAY)[0]
check(h["suggested_move"]["kind"] == "commitment"
      and h["suggested_move"]["title"] == "Send revised proposal",
      "an open commitment on the linked work is the first-choice move")

owned_rows = [obj_row("project_101", "Delegate the ops review",
                      {"type": "self", "cadence_days": 7},
                      owner="person_002")]
h = om.compute_objective_health(
    owned_rows, objective_events=[], meeting_events=[], deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY, primary_user_id="person_001")[0]
check(h["suggested_move"]["kind"] == "poke_owner"
      and h["suggested_move"]["owner_id"] == "person_002",
      "a non-CEO owner is the second-choice move (who to poke)")

# --- ranking ----------------------------------------------------------------
blocked_rows = [
    obj_row("project_103", "Steady one", {"type": "self", "cadence_days": 7}),
    obj_row("project_104", "Blocked one", {"type": "self", "cadence_days": 7}),
]
evs = [mk_event("objective_report", "project_103", 1, {"status": "on_track"}),
       mk_event("objective_report", "project_104", 1, {"status": "blocked"})]
health = om.compute_objective_health(
    blocked_rows, objective_events=evs, meeting_events=[], deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY)
check(health[0]["thread_id"] == "project_104" and health[0]["severity"] >= 4,
      "a stated blocked ranks first")
check(health[1]["severity"] == 0, "a fresh on_track carries zero severity")

# --- due_self_reports -------------------------------------------------------
due = om.due_self_reports(health, blocked_rows, evs, TODAY)
check(due == [], "freshly reported objectives are not asked again")
due = om.due_self_reports(health, blocked_rows, [], TODAY)
check(len(due) == 2, "never-reported self objectives are due for the touch")

# --- brief_lines (FB-20: read-only, drop-empty) -----------------------------
check(om.brief_lines([]) == [], "zero objectives => emit nothing")
steady = om.compute_objective_health(
    [obj_row("project_103", "Steady one", {"type": "self"})],
    objective_events=[mk_event("objective_report", "project_103", 1,
                               {"status": "on_track"})],
    meeting_events=[], deal_events=[], activity_by_thread={},
    threads_by_id={}, open_commitments=[], today=TODAY)
lines = om.brief_lines(steady)
check(len(lines) == 1 and "steady" in lines[0],
      "all-steady => one calm focus line, no fabricated alarm")
drifting = om.compute_objective_health(
    self_rows, objective_events=stale_rep, meeting_events=[], deal_events=[],
    activity_by_thread={}, threads_by_id={}, open_commitments=[],
    today=TODAY)
lines = om.brief_lines(drifting, names_by_person_id={"person_001": "Sam"})
check(lines and "Objective drifting" in lines[0]
      and "show my objectives" in lines[0],
      "drifting => one line with the reason + the teach-the-phrase close")
check("?" not in lines[0], "brief lines never ask for input (FB-20)")

# --- recap_rows -------------------------------------------------------------
rr = om.recap_rows(drifting, names_by_person_id={"person_001": "Sam"})
check(rr and rr[0].startswith("- ") and "Drifting" in rr[0],
      "recap rows carry status + drift + move per objective")

# --- loader smoke (end-to-end on a temp workspace) --------------------------
d = Path(tempfile.mkdtemp())
data_dir = d / "_hq" / "data"
data_dir.mkdir(parents=True)
ent = {"version": 1, "people": [
    {"id": "person_001", "canonical_name": "Sam Sample",
     "is_primary_user": True}],
    "orgs": [], "threads": [], "engagements": []}
(data_dir / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
(data_dir / "events.jsonl").write_text("", encoding="utf-8")
t = os_.create_objective(d, statement="Ship the new operating cadence",
                         binding={"type": "self", "cadence_days": 7},
                         owner_person_id="person_001")
os_.record_report(d, t["id"], status="on_track", note="First week done")
inputs = om.load_objective_inputs(d)
check(len(inputs["open_objectives"]) == 1 and not inputs["skipped"],
      "loader assembles the open set with no skipped lines")
health = om.compute_objective_health(
    inputs["open_objectives"], objective_events=inputs["objective_events"],
    meeting_events=inputs["meeting_events"],
    deal_events=inputs["deal_events"],
    activity_by_thread=inputs["activity_by_thread"],
    threads_by_id=inputs["threads_by_id"],
    open_commitments=inputs["open_commitments"], today=TODAY,
    primary_user_id=inputs["primary_user_id"])
check(health[0]["status"]["value"] == "on_track"
      and not health[0]["drift"]["flagged"],
      "end-to-end: created + reported => on_track, no drift")

# --- F-2: pin the two hand-copied constants so they can never silently drift.
# objective_math keeps top-level imports minimal by design (it lazy-imports
# siblings), so rather than restructure that, we enforce equivalence here.
import re as _re  # noqa: E402
import thread_writer as _tw  # noqa: E402

# F-2a: objective_math._norm_title MUST equal objective_state.normalize_series_key.
# Fuzz a spread of titles (unicode, punctuation, whitespace, empty, None).
_norm_samples = [
    "Weekly Sales Sync", "1:1 with Bo", "  Q3   OKR   Review  ",
    "Café / Ops — Handbook!!", "ALL CAPS TITLE", "", "   ", None,
    "numbers 123 and symbols #@$%", "tab\tand\nnewline",
]
for _s in _norm_samples:
    check(om._norm_title(_s) == os_.normalize_series_key(_s),
          f"F-2a: _norm_title stays byte-identical to normalize_series_key ({_s!r})")

# F-2b: objective_math._DEAL_STAGE_ORDER MUST match thread_writer.DEAL_STAGES
# (thread_writer is the documented single source for the stage enum).
check(tuple(om._DEAL_STAGE_ORDER) == tuple(_tw.DEAL_STAGES),
      "F-2b: _DEAL_STAGE_ORDER stays identical to thread_writer.DEAL_STAGES")

print(f"OK - {PASS} checks passed")
