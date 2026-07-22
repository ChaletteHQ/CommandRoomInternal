#!/usr/bin/env python3
"""OBJ2-R — the supersession-aware reader fix (closes the OBJ2 review's F-1
reader gap: adjudicating a link now CHANGES what the movement read says).

Covers: (a) apply_reclassifications unit behavior — envelope + confidence
patched from the latest correction, latest-wins on competing corrections,
legacy data-level spellings cleared on the patched copy, untouched events
pass through, reclassification events stay in the stream; (b) the movement
delta the former waiver deliberately omitted — an activity-bound objective
reads "moving" from a confident signal primary-stamped on its OWN thread
(the new own-thread join) and falls back to "quiet" after the dismiss-shaped
unlink reclassification; (c) the confirm delta — a below-activity-floor
provisional signal (< thread_activity.CONFIDENCE_FLOOR) counts only after
the confirm-shaped confidence-1.0 reclassification; (d) a reclassification
event is never itself activity (its own envelope must not bump any thread);
(e) the detector reads patched envelopes — a dismissed pairing can never
re-propose even with NO ledger cooldown row (the reclassification, not the
cooldown, is the permanent answer).

Fixtures mirror real substrate shapes; all dates computed relative to today
(G14); placeholder names only (Sam Sample, Acme Pilot, lighthouse objective)."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "shared" / "scripts"))
import objective_link_detector as old  # noqa: E402
import objective_math as om  # noqa: E402
from thread_activity import (  # noqa: E402
    CONFIDENCE_FLOOR,
    apply_reclassifications,
    derive_from_events,
)

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
OBJ = "project_901"


# ===========================================================================
# (a) apply_reclassifications — unit behavior
# ===========================================================================
print("[a] apply_reclassifications unit")
events = [
    # carries EVERY legacy attribution spelling — the top-level project_id
    # mirror plus all three data-level fallbacks — so each clear in
    # apply_reclassifications is mutation-fenced individually
    {"seq": 1, "type": "commitment", "primary_thread_id": OBJ,
     "related_thread_ids": ["project_303"],
     "classification_confidence": 0.55, "project_id": OBJ,
     "data": {"id": "cmt_a", "project_id": OBJ, "primary_thread_id": OBJ,
              "thread_id": OBJ, "title": "x"}},
    {"seq": 2, "type": "interaction", "primary_thread_id": "project_101",
     "classification_confidence": 0.9, "data": {"summary": "untouched"}},
    # first correction of seq 1 (superseded by the later one below)
    {"seq": 3, "type": "reclassification", "supersedes_seq": 1,
     "primary_thread_id": "project_777", "related_thread_ids": [],
     "classification_confidence": 1.0, "data": {"reason": "first"}},
    # the LATEST correction of seq 1 — this one wins
    {"seq": 4, "type": "reclassification", "supersedes_seq": 1,
     "primary_thread_id": None, "related_thread_ids": ["project_303"],
     "classification_confidence": 1.0, "data": {"reason": "latest"}},
]
patched = apply_reclassifications(events)
check(len(patched) == 4 and [e.get("seq") for e in patched] == [1, 2, 3, 4],
      "order preserved, reclassification events kept in the stream")
p1 = patched[0]
check(p1["primary_thread_id"] is None
      and p1["related_thread_ids"] == ["project_303"]
      and p1["classification_confidence"] == 1.0,
      f"latest correction wins and patches the whole envelope: {p1}")
d1 = p1.get("data") or {}
check("project_id" not in p1
      and all(k not in d1
              for k in ("project_id", "primary_thread_id", "thread_id")),
      "EVERY legacy spelling cleared on the patched copy — top-level "
      "project_id + data project_id/primary_thread_id/thread_id (each one "
      "would resurrect the old attribution through event_thread_ids or "
      "objective_math._event_thread_id)")
check(patched[1] is events[1] or patched[1] == events[1],
      "untouched events pass through")
check(events[0]["primary_thread_id"] == OBJ
      and events[0]["project_id"] == OBJ
      and events[0]["data"]["project_id"] == OBJ
      and events[0]["data"]["primary_thread_id"] == OBJ
      and events[0]["data"]["thread_id"] == OBJ,
      "the ORIGINAL event object is never mutated (append-only posture)")

# ===========================================================================
# (b) movement delta: own-thread join counts a confident signal; the
#     dismiss-shaped unlink makes the objective quiet again
# ===========================================================================
print("[b] dismiss changes the movement read")


def _ws(evs):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [], "orgs": [], "engagements": [],
           "threads": [
        {"id": OBJ, "kind": "objective", "status": "active",
         "canonical_name": "Land three lighthouse clients",
         "objective": {"statement": "Land three lighthouse clients",
                       "binding": {"type": "activity",
                                   "entity_ids": ["project_101"]},
                       "opened_at": (NOW - timedelta(days=60)).strftime("%Y-%m-%d")}},
        {"id": "project_101", "kind": "project", "status": "active",
         "canonical_name": "Acme Pilot"},
    ]}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")
    return d


def _health_row(ws):
    inputs = om.load_objective_inputs(ws)
    rows = om.compute_objective_health(
        inputs["open_objectives"],
        objective_events=inputs["objective_events"],
        meeting_events=inputs["meeting_events"],
        deal_events=inputs["deal_events"],
        activity_by_thread=inputs["activity_by_thread"],
        threads_by_id=inputs["threads_by_id"],
        open_commitments=inputs["open_commitments"],
        today=TODAY)
    return next(r for r in rows if r["thread_id"] == OBJ)


# a confident signal 3 days ago, primary-stamped on the objective's OWN
# thread (the linked project has been quiet for 45 days)
base_events = [
    {"seq": 1, "ts": _iso(NOW - timedelta(days=45)), "type": "interaction",
     "source_skill": "inbox-triage", "primary_thread_id": "project_101",
     "classification_confidence": 0.9,
     "data": {"summary": "old linked-thread touch"}},
    {"seq": 2, "ts": _iso(NOW - timedelta(days=3)), "type": "commitment",
     "source_skill": "meeting-notes", "primary_thread_id": OBJ,
     "classification_confidence": 0.55,
     "data": {"id": "cmt_own", "title": "Sam Sample: draft the pitch",
              "status": "open", "origin": "user_stated"}},
]
ws = _ws(base_events)
row = _health_row(ws)
check(row["status"]["value"] == "moving"
      and not row["drift"]["flagged"],
      f"own-thread confident signal reads MOVING (the new join): {row['status']}")

# the dismiss-shaped unlink: seq 2's envelope loses the objective
ws2 = _ws(base_events + [
    {"seq": 3, "ts": _iso(NOW - timedelta(days=1)),
     "type": "reclassification", "source_skill": "apply-choices",
     "supersedes_seq": 2, "primary_thread_id": None,
     "related_thread_ids": [], "classification_confidence": 1.0,
     "data": {"old_primary_thread_id": OBJ, "new_primary_thread_id": None,
              "reason": "user declined objective-link proposal bp_x — "
                        "unlink from project_901"}},
])
row2 = _health_row(ws2)
check(row2["status"]["value"] == "quiet" and row2["drift"]["flagged"],
      f"after the unlink the objective is QUIET again — the dismissed "
      f"pairing no longer counts (the closed F-1 gap): {row2['status']}")
# the seam is adopted as a UNIT: the unlinked commitment must not come back
# through the suggested-move read either (open_commitments attribution reads
# the patched envelope, not the raw one)
check((row2.get("suggested_move") or {}).get("title")
      != "Sam Sample: draft the pitch",
      f"the unlinked commitment never resurfaces as the objective's own "
      f"suggested move: {row2.get('suggested_move')}")

# ===========================================================================
# (c) confirm delta: a below-floor signal counts only after the
#     confidence-1.0 confirming reclassification
# ===========================================================================
print("[c] confirm changes the movement read")
low_events = [
    {"seq": 1, "ts": _iso(NOW - timedelta(days=3)), "type": "commitment",
     "source_skill": "meeting-notes", "primary_thread_id": OBJ,
     "classification_confidence": round(CONFIDENCE_FLOOR - 0.10, 2),
     "data": {"id": "cmt_low", "title": "Sam Sample: below-floor signal",
              "status": "open", "origin": "user_stated"}},
]
ws3 = _ws(low_events)
row3 = _health_row(ws3)
check(row3["status"]["value"] == "quiet" and row3["drift"]["flagged"],
      f"below the activity floor a provisional signal does NOT count: "
      f"{row3['status']}")
ws4 = _ws(low_events + [
    {"seq": 2, "ts": _iso(NOW - timedelta(days=1)),
     "type": "reclassification", "source_skill": "apply-choices",
     "supersedes_seq": 1, "primary_thread_id": OBJ,
     "related_thread_ids": [], "classification_confidence": 1.0,
     "data": {"old_primary_thread_id": OBJ, "new_primary_thread_id": OBJ,
              "reason": "user confirmed objective-link proposal bp_y — "
                        "link to project_901"}},
])
row4 = _health_row(ws4)
check(row4["status"]["value"] == "moving" and not row4["drift"]["flagged"],
      f"the confirm-shaped confidence-1.0 correction makes the click COUNT: "
      f"{row4['status']}")

# ===========================================================================
# (d) a reclassification event is never itself activity
# ===========================================================================
print("[d] reclassification is a patch, not activity")
acts = derive_from_events(apply_reclassifications([
    {"seq": 1, "ts": _iso(NOW - timedelta(days=1)),
     "type": "reclassification", "supersedes_seq": 99,
     "primary_thread_id": OBJ, "related_thread_ids": [],
     "classification_confidence": 1.0, "data": {"reason": "orphan patch"}},
]))
check(acts == {}, f"a lone reclassification bumps NO thread: {acts}")

# ===========================================================================
# (e) the detector reads patched envelopes: a dismissed pairing never
#     re-proposes, even with NO cooldown row in the ledger
# ===========================================================================
print("[e] detector honors the unlink permanently")
cands = old.detect_objective_links(ws2)  # unlink written, NO decline ledger
check(all(c["target_id"] != "cmt_own" for c in cands),
      f"the unlinked pairing is invisible to the detector without any "
      f"cooldown row — the reclassification is the permanent answer: {cands}")
# and the confirm-shaped patch (confidence 1.0) also leaves the provisional
# lane — nothing to propose there either
check(old.detect_objective_links(ws4) == [],
      "a confirmed (confidence-1.0) envelope is no longer provisional")

print(f"OK — {PASS} checks passed")
