#!/usr/bin/env python3
"""Tests for objective_state — the single objective writer/closure path
(SPEC OBJ1, DRAFT). Mirrors run_deal_state_test.py conventions:
typed-writer validation, event emission, idempotency, and defensive
tolerance of a malformed shape (a kind='objective' thread with NO objective
object must be surfaced honestly and never crash a reader)."""
import datetime
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import objective_state as os_  # noqa: E402
import thread_writer as tw  # noqa: E402

PASS = 0

# computed relative to today (G14) — the writer stores horizons blind, so any
# future date works; never hardcode one
FUTURE = (datetime.date.today() + datetime.timedelta(days=170)).isoformat()
FUTURE2 = (datetime.date.today() + datetime.timedelta(days=260)).isoformat()


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    people = [
        {"id": "person_001", "canonical_name": "Sam Sample",
         "is_primary_user": True},
        {"id": "person_002", "canonical_name": "Bo Sample"},
    ]
    threads = [
        {"id": "project_001", "canonical_name": "Acme pilot",
         "folder_name": "acme_pilot", "status": "active", "kind": "deal",
         "first_seen": "2026-01-01"},
        {"id": "project_002", "canonical_name": "Ops revamp",
         "folder_name": "ops_revamp", "status": "active", "kind": "initiative",
         "first_seen": "2026-01-01"},
    ]
    ent = {"version": 1, "people": people, "orgs": [], "threads": threads,
           "engagements": []}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _entities(ws):
    obj = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    return obj.get("entities") if isinstance(obj.get("entities"), dict) else obj


def _threads(ws):
    ent = _entities(ws)
    return ent.get("threads") or ent.get("projects") or []


def _events(ws, etype=None):
    txt = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    evs = [json.loads(ln) for ln in txt.splitlines() if ln.strip()]
    if etype:
        evs = [e for e in evs if e.get("type") == etype]
    return evs


# --- series-key normalization (ONE normalizer for both sides) ---------------
check(os_.normalize_series_key("Weekly Sales Sync!") == "weekly sales sync",
      "normalize_series_key lowercases and strips punctuation")
check(os_.normalize_series_key("  L10  ") == "l10",
      "normalize_series_key collapses whitespace")

# --- create: meeting binding ------------------------------------------------
ws = _ws()
thread = os_.create_objective(
    ws,
    statement="Land three enterprise pilots",
    binding={"type": "meeting", "series_key": "Weekly Sales Sync",
             "series_match": "title_and_people",
             "series_people": ["person_001", "person_002"]},
    owner_person_id="person_001",
    horizon=FUTURE,
    anchor_thread_id="project_001",
)
check(thread["kind"] == "objective", "create thread has kind=objective")
obj = thread["objective"]
check(obj["statement"] == "Land three enterprise pilots", "statement stored")
check(obj["binding"]["series_key"] == "weekly sales sync",
      "series_key stored normalized")
check(obj["binding"]["series_match"] == "title_and_people",
      "series_match stored")
check(obj.get("opened_at") and obj.get("horizon") == FUTURE,
      "opened_at + horizon stamped")
check(obj.get("anchor_thread_id") == "project_001", "anchor stored")
check("outcome" not in obj or obj.get("outcome") is None,
      "no terminal outcome at creation")
check(thread.get("owner_person_id") == "person_001",
      "owner lands on the thread's existing owner_person_id field")
created = _events(ws, "objective_created")
check(len(created) == 1, "exactly one objective_created emitted")
check(created[0]["data"]["thread_id"] == thread["id"]
      and created[0]["data"]["binding_type"] == "meeting"
      and created[0]["data"]["owner_id"] == "person_001",
      "objective_created payload carries thread_id/binding_type/owner_id")
check(created[0].get("seq") is not None and created[0].get("ts"),
      "objective_created went through the gate (seq/ts auto-stamped)")
check(created[0].get("related_thread_ids") == ["project_001"],
      "anchor rides related_thread_ids (the relevance-capture join)")
OBJ_ID = thread["id"]

# meeting binding without people under title_and_people rejected
try:
    os_.create_objective(ws, statement="Ghost forum objective",
                         binding={"type": "meeting", "series_key": "1:1",
                                  "series_match": "title_and_people"})
    check(False, "title_and_people without series_people should raise")
except os_.ObjectiveStateError:
    check(True, "title_and_people requires series_people")

# title_only needs no people (the distinctive-name override)
t2 = os_.create_objective(
    ws, statement="Ship the operating cadence",
    binding={"type": "meeting", "series_key": "L10",
             "series_match": "title_only"})
check(t2["objective"]["binding"]["series_match"] == "title_only"
      and not t2["objective"]["binding"].get("series_people"),
      "title_only binding valid without series_people")

# --- create: self binding (cadence default) ---------------------------------
t3 = os_.create_objective(
    ws, statement="Personal: delegate weekly ops review",
    binding={"type": "self"})
check(t3["objective"]["binding"]["cadence_days"] == 7,
      "self binding defaults cadence_days=7 (the weekly touch)")

# --- create: activity binding ----------------------------------------------
t4 = os_.create_objective(
    ws, statement="Convert the Acme pilot into a paying account",
    binding={"type": "activity", "entity_ids": ["project_001"]})
check(t4["objective"]["binding"]["entity_ids"] == ["project_001"],
      "activity binding stores linked thread ids")

# activity binding to a non-existent thread rejected
try:
    os_.create_objective(ws, statement="Bind to nothing",
                         binding={"type": "activity",
                                  "entity_ids": ["project_999"]})
    check(False, "activity binding to missing thread should raise")
except os_.ObjectiveStateError:
    check(True, "activity binding requires existing threads")

# anchoring to another objective rejected (anchor the real work, not the meta)
try:
    os_.create_objective(ws, statement="Meta objective",
                         binding={"type": "self"},
                         anchor_thread_id=OBJ_ID)
    check(False, "anchoring to an objective thread should raise")
except os_.ObjectiveStateError:
    check(True, "anchor must be the underlying project/deal thread")

# empty statement rejected
try:
    os_.create_objective(ws, statement="  ", binding={"type": "self"})
    check(False, "empty statement should raise")
except os_.ObjectiveStateError:
    check(True, "statement must be non-empty")

# --- thread_writer floor: objective object on a non-objective kind ----------
try:
    tw.create_thread(ws, canonical_name="Sneaky thread", kind="initiative",
                     objective={"statement": "x", "binding": {"type": "self"}})
    check(False, "objective object on kind='initiative' should raise")
except ValueError:
    check(True, "thread_writer floor rejects objective on non-objective kind")

# a stored status field is the named bug class — rejected at the floor
try:
    tw.create_thread(ws, canonical_name="Status smuggler", kind="objective",
                     objective={"statement": "x", "binding": {"type": "self"},
                                "status": "on_track"})
    check(False, "a stored objective.status should raise")
except ValueError:
    check(True, "thread_writer floor rejects a stored status field")

# --- update_objective -------------------------------------------------------
r = os_.update_objective(ws, OBJ_ID, horizon=FUTURE2)
check(r["status"] == "updated" and r["changed"] == {"horizon": FUTURE2},
      "update_objective changes horizon")
r = os_.update_objective(ws, OBJ_ID, horizon=FUTURE2)
check(r["status"] == "unchanged", "no-op update returns unchanged, no event")
check(len(_events(ws, "objective_updated")) == 1,
      "exactly one objective_updated after change + no-op")
try:
    os_.update_objective(ws, OBJ_ID, binding={"type": "self"})
    check(False, "update_objective touching binding should raise")
except os_.ObjectiveStateError:
    check(True, "binding changes are rebind_objective's job")
r = os_.update_objective(ws, OBJ_ID, owner_person_id="person_002")
check(r["changed"] == {"owner_id": "person_002"},
      "owner reassignment flows through update_objective")

# --- rebind_objective -------------------------------------------------------
r = os_.rebind_objective(ws, OBJ_ID, {"type": "self", "cadence_days": 14})
check(r["status"] == "rebound" and r["binding_type"] == "self",
      "rebind replaces the binding")
r = os_.rebind_objective(ws, OBJ_ID, {"type": "self", "cadence_days": 14})
check(r["status"] == "unchanged", "identical rebind is a no-op")

# --- record_review (meeting path) -------------------------------------------
# OBJ_ID is now self-bound: review must refuse (status honesty follows binding)
try:
    os_.record_review(ws, OBJ_ID, status="on_track",
                      source_ref="granola:m1")
    check(False, "record_review on a non-meeting-bound objective should raise")
except os_.ObjectiveStateError:
    check(True, "record_review refuses non-meeting-bound objectives")

r = os_.record_review(ws, t2["id"], status="at_risk", source_ref="granola:m1",
                      context="Two pilots stalled at security review",
                      meeting_title="L10")
check(r["status"] == "reviewed" and r["stated_status"] == "at_risk",
      "record_review writes the stated status")
r = os_.record_review(ws, t2["id"], status="on_track", source_ref="granola:m1")
check(r["status"] == "already_reviewed",
      "same source_ref is idempotent (no double harvest per meeting)")
check(len(_events(ws, "objective_review")) == 1,
      "exactly one objective_review after harvest + replay")
try:
    os_.record_review(ws, t2["id"], status="fine", source_ref="granola:m2")
    check(False, "non-enum status should raise")
except os_.ObjectiveStateError:
    check(True, "record_review rejects a non-enum status")

# --- record_report (owner's word, any binding) ------------------------------
r = os_.record_report(ws, t4["id"], status="on_track",
                      note="Contract in redline", reported_by="person_001")
check(r["status"] == "reported",
      "record_report accepts the owner's word on an activity-bound objective")
rep = _events(ws, "objective_report")
check(len(rep) == 1 and rep[0]["data"]["status"] == "on_track"
      and rep[0]["data"]["reported_by"] == "person_001",
      "objective_report payload carries status + reporter")

# --- closure paths ----------------------------------------------------------
r = os_.complete_objective(ws, t4["id"], outcome_note="Signed 2-year deal")
check(r["status"] == "closed" and r["outcome"] == "completed",
      "complete_objective closes")
t4_after = next(t for t in _threads(ws) if t["id"] == t4["id"])
check(t4_after["status"] == "resolved"
      and t4_after["objective"]["outcome"] == "completed"
      and t4_after["objective"].get("closed_at"),
      "completion flips thread to resolved + stamps outcome/closed_at")
r = os_.complete_objective(ws, t4["id"])
check(r["status"] == "already_closed",
      "second completion is a NO-OP (idempotent, no duplicate event)")
check(len(_events(ws, "objective_completed")) == 1,
      "exactly one objective_completed emitted")

r = os_.archive_objective(ws, t3["id"], outcome_note="No longer a priority")
check(r["status"] == "closed" and r["outcome"] == "archived",
      "archive_objective closes")
t3_after = next(t for t in _threads(ws) if t["id"] == t3["id"])
check(t3_after["status"] == "archived",
      "archive flips thread to archived")

# closed objectives are not editable
try:
    os_.update_objective(ws, t4["id"], horizon="2027-01-01")  # DATE_GUARD_OK: the writer refuses the closed objective before any date logic runs — the value can never flip this test
    check(False, "editing a closed objective should raise")
except os_.ObjectiveStateError:
    check(True, "terminal objectives are not editable")
try:
    os_.record_report(ws, t4["id"], status="on_track")
    check(False, "reporting on a closed objective should raise")
except os_.ObjectiveStateError:
    check(True, "no reports on a closed objective")

# --- readers ----------------------------------------------------------------
open_rows = os_.list_open_objectives(ws)
open_ids = {r["thread_id"] for r in open_rows}
check(OBJ_ID in open_ids and t2["id"] in open_ids
      and t4["id"] not in open_ids and t3["id"] not in open_ids,
      "list_open_objectives excludes closed, includes open")
closed_rows = os_.list_closed_objectives(ws)
check({r["thread_id"] for r in closed_rows} == {t3["id"], t4["id"]},
      "list_closed_objectives returns exactly the closed set")

# malformed shape: a hand-made kind='objective' thread with no objective
# object must surface honestly, never crash
ent_path = ws / "_hq" / "data" / "entities.json"
ent = json.loads(ent_path.read_text(encoding="utf-8"))
container = ent.get("entities") if isinstance(ent.get("entities"), dict) else ent
(container.get("threads") or container.get("projects")).append(
    {"id": "project_990", "canonical_name": "Hand-made objective",
     "folder_name": "hand_made", "status": "active", "kind": "objective",
     "first_seen": "2026-01-01"})
ent_path.write_text(json.dumps(ent), encoding="utf-8")
rows = os_.list_open_objectives(ws)
mal = next(r for r in rows if r["thread_id"] == "project_990")
check(mal["malformed"] is True and mal["objective"] is None,
      "malformed objective thread surfaces with malformed=True, no crash")
try:
    os_.record_report(ws, "project_990", status="on_track")
    check(False, "writing to a malformed objective should raise")
except os_.ObjectiveStateError:
    check(True, "writers refuse a malformed objective thread loudly")

# events reader filters to the family
evs, skipped = os_.load_objective_events(ws)
check(all(e["type"] in os_.OBJECTIVE_EVENT_TYPES for e in evs) and not skipped,
      "load_objective_events returns only objective_* events, none skipped")
check(len(evs) == len(_events(ws)) - len(
    [e for e in _events(ws) if e["type"] not in os_.OBJECTIVE_EVENT_TYPES]),
    "objective event count consistent with raw log")

print(f"OK - {PASS} checks passed")
