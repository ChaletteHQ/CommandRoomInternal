#!/usr/bin/env python3
"""Tests for org_activity.derive_org_activity (SPEC HIST1 D6) — org recency
derives from events (direct org_ids[] AND via an affiliated thread), honors
the confidence floor, and NEVER reads the org.last_interaction fossil.
Fixture dates computed RELATIVE TO TODAY (hardcoded-future-date gotcha)."""
import datetime
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import org_activity as oa  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


TODAY = datetime.date.today()


def _d(days_ago: int) -> str:
    return (TODAY - datetime.timedelta(days=days_ago)).isoformat()


def _ws(events, orgs=None, threads=None):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {
        "people": [],
        "orgs": orgs if orgs is not None else [
            {"id": "org_001", "canonical_name": "Acme Co", "first_seen": _d(60)},
        ],
        "threads": threads if threads is not None else [
            {"id": "project_001", "display_name": "Acme Ops", "affiliation_id": "org_001"},
        ],
        "version": 1,
    }
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    lines = "".join(json.dumps(e) + "\n" for e in events)
    (data / "events.jsonl").write_text(lines, encoding="utf-8")
    return d


# --- direct org_ids[] + via affiliated thread; newest wins ----------------
ws = _ws([
    {"seq": 1, "ts": _d(20) + "T10:00:00+00:00", "type": "meeting",
     "source_skill": "t", "org_ids": ["org_001"], "data": {"title": "Kickoff"}},
    {"seq": 2, "ts": _d(5) + "T10:00:00+00:00", "type": "interaction",
     "source_skill": "t", "primary_thread_id": "project_001",
     "data": {"summary": "Email"}},
])
act = oa.derive_org_activity(ws)
check("org_001" in act, "org reached both directly and via its thread")
check(act["org_001"].ts.date().isoformat() == _d(5),
      "newest event wins — the thread-carried touch is the derived recency")
check(act["org_001"].seq == 2, "seq carried for traceability")

# --- confidence floor -----------------------------------------------------
ws2 = _ws([
    {"seq": 1, "ts": _d(20) + "T10:00:00+00:00", "type": "meeting",
     "source_skill": "t", "org_ids": ["org_001"], "data": {"title": "Kickoff"}},
    {"seq": 2, "ts": _d(1) + "T10:00:00+00:00", "type": "interaction",
     "source_skill": "t", "org_ids": ["org_001"],
     "classification_confidence": 0.2, "data": {"summary": "low-confidence guess"}},
])
act2 = oa.derive_org_activity(ws2)
check(act2["org_001"].ts.date().isoformat() == _d(20),
      "a sub-0.40-confidence event does not count as activity")

# --- the fossil is never read --------------------------------------------
ws3 = _ws(
    [{"seq": 1, "ts": _d(40) + "T10:00:00+00:00", "type": "meeting",
      "source_skill": "t", "org_ids": ["org_001"], "data": {"title": "Old sync"}}],
    orgs=[{"id": "org_001", "canonical_name": "Acme Co",
           "first_seen": _d(60), "last_interaction": _d(1)}],  # fossil claims yesterday
)
act3 = oa.derive_org_activity(ws3)
check(act3["org_001"].ts.date().isoformat() == _d(40),
      "derived recency ignores the org.last_interaction fossil entirely")

# --- zero-event org: absent from the result (first_seen floor is caller's) -
ws4 = _ws([], orgs=[{"id": "org_001", "canonical_name": "Acme Co",
                     "first_seen": _d(60), "last_interaction": _d(1)}])
act4 = oa.derive_org_activity(ws4)
check("org_001" not in act4,
      "zero-event org has NO derived activity — callers fall back to the "
      "first_seen floor only, never the fossil")

# --- HIST1 payload shapes count (data.org_id on fact events) --------------
ws5 = _ws([
    {"seq": 1, "ts": _d(3) + "T10:00:00+00:00", "type": "org_fact_observed",
     "source_skill": "t", "org_ids": ["org_001"],
     "data": {"org_id": "org_001", "fact": "Raised a Series A",
              "source_ref": "chat:user-statement"}},
])
act5 = oa.derive_org_activity(ws5)
check(act5["org_001"].event_type == "org_fact_observed",
      "a recorded org fact bumps the org's derived recency")

# --- type filter (day-count surfaces pass a real set) ---------------------
act6 = oa.derive_org_activity(ws5, activity_types={"meeting", "interaction"})
check("org_001" not in act6,
      "a passed type set filters — day-count surfaces share one real set (F-54)")

print(f"OK — all {PASS} org_activity tests passed")
sys.exit(0)
