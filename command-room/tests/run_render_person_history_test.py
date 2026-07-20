#!/usr/bin/env python3
"""Tests for render_person_history (SPEC HIST1 D5) — derived stats, lineage
block, facts grouped by category, drop-empty, legacy tolerance, retraction
suppression, leak-scanner cleanliness, timeline points for call-prep, and
the dirty-checked cleanup regen. Fixtures mirror real substrate shapes
(legacy records missing new fields); dates relative to today (G14);
placeholder names only."""
import datetime
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import render_person_history as rph  # noqa: E402
from atomic_write import atomic_append_jsonl  # noqa: E402
from chat_output_renderer import scan_for_id_leaks  # noqa: E402
import people_writer as pw  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


TODAY = datetime.date.today()


def _d(days_ago: int) -> str:
    return (TODAY - datetime.timedelta(days=days_ago)).isoformat()


def _ws(people=None):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {
        "people": people if people is not None else [
            # Real-data shape: stored last_interaction is a STALE fossil the
            # renderer must never quote.
            {"id": "person_001", "canonical_name": "Sam Sample", "first_seen": _d(40),
             "role": "Advisor", "primary_org_id": "org_001", "last_interaction": _d(39)},
        ],
        "orgs": [
            {"id": "org_001", "canonical_name": "Acme Co"},
            {"id": "org_002", "canonical_name": "Northstar Partners"},
        ],
        "threads": [],
        "version": 1,
    }
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


# --- rich fixture: meetings + role change + two facts ---------------------
ws = _ws()
ep = ws / "_hq" / "data" / "events.jsonl"
atomic_append_jsonl(ep, [
    {"type": "meeting", "source_skill": "t", "ts": _d(30) + "T10:00:00+00:00",
     "person_ids": ["person_001"], "data": {"title": "Kickoff"}},
    {"type": "meeting", "source_skill": "t", "ts": _d(18) + "T10:00:00+00:00",
     "person_ids": ["person_001"], "data": {"title": "Pricing review"}},
    {"type": "interaction", "source_skill": "t", "ts": _d(6) + "T10:00:00+00:00",
     "person_ids": ["person_001"], "data": {"summary": "Email about scope"}},
])
pw.update_person(ws, "person_001", role="CRO", source_skill="people-crm")
pw.record_person_fact(ws, "person_001", "Prefers Signal over email",
                      "chat:user-statement", category="preference", source_skill="people-crm")
pw.record_person_fact(ws, "person_001", "Based in Austin",
                      "chat:user-statement", category="personal", source_skill="people-crm")

r = rph.render_person_history(ws, "person_001")
content = Path(r["path"]).read_text(encoding="utf-8")

check("Sam Sample" in content, "header carries the name")
check(f"**Last touch:** {_d(6)}" in content,
      "last touch is DERIVED from events — not the stale stored date")
check(_d(39) not in content, "the stored fossil date never renders")
check("**Cadence:** ~every" in content, "cadence number renders")
check("Advisor → CRO" in content, "lineage block shows old → new role")
check("**Preferences**" in content and "Prefers Signal over email" in content,
      "facts render grouped by category")
check("**Personal**" in content and "Based in Austin" in content,
      "second category renders")
check("## Timeline" in content and "Kickoff" in content,
      "timeline renders the touch history")
check("How we met" in content, "how-we-met line renders from the first touch")
check(scan_for_id_leaks(content) == [], "rendered view is leak-scanner clean")

# --- call-prep seam: durable timeline points ------------------------------
pts = rph.person_timeline_points(ws, "person_001")
check(len(pts) >= 3 and all(p.get("date") and p.get("label") for p in pts),
      "person_timeline_points yields dated, labeled points for build_relationship_timeline")
check(pts[-1]["date"] >= pts[0]["date"], "points run oldest → newest")

# --- retraction suppression (D3/S1 read-side) -----------------------------
evs = [json.loads(ln) for ln in ep.read_text(encoding="utf-8").splitlines() if ln.strip()]
fact_seq = next(e["seq"] for e in evs if e["type"] == "person_fact_observed"
                and e["data"]["fact"] == "Based in Austin")
atomic_append_jsonl(ep, [{
    "type": "entity_fact_retracted", "source_skill": "t",
    "data": {"target_id": "person_001", "retracts_seq": fact_seq,
             "source_ref": "undo:test"},
}])
r2 = rph.render_person_history(ws, "person_001")
content2 = Path(r2["path"]).read_text(encoding="utf-8")
check("Based in Austin" not in content2,
      "a retracted fact disappears from the next render")
check("Prefers Signal over email" in content2, "unretracted facts survive")

# --- drop-empty: a one-event person keeps no cadence/timeline -------------
ws2 = _ws(people=[{"id": "person_001", "canonical_name": "Sam Sample", "first_seen": _d(40)}])
atomic_append_jsonl(ws2 / "_hq" / "data" / "events.jsonl", [
    {"type": "meeting", "source_skill": "t", "ts": _d(9) + "T10:00:00+00:00",
     "person_ids": ["person_001"], "data": {"title": "Only touch"}},
])
c2 = Path(rph.render_person_history(ws2, "person_001")["path"]).read_text(encoding="utf-8")
check("## Timeline" not in c2, "one event → timeline section dropped (empty-frame rule)")
check("Cadence" not in c2, "one event → no cadence claim")
check("**Last touch:**" in c2, "the single touch still informs last-touch")

# --- legacy person with none of the new fields: header-only, no crash -----
ws3 = _ws(people=[{"id": "person_001", "canonical_name": "Sam Sample", "first_seen": _d(40)}])
c3 = Path(rph.render_person_history(ws3, "person_001")["path"]).read_text(encoding="utf-8")
check("# Sam Sample" in c3, "legacy zero-history person renders a header card without crashing")
check("## Timeline" not in c3 and "## Role & company history" not in c3,
      "zero-history card drops every empty section")
check(scan_for_id_leaks(c3) == [], "zero-history card is leak-clean")

# --- malformed line tolerated with the skipped banner ---------------------
ws4 = _ws()
ep4 = ws4 / "_hq" / "data" / "events.jsonl"
atomic_append_jsonl(ep4, [
    {"type": "meeting", "source_skill": "t", "ts": _d(3) + "T10:00:00+00:00",
     "person_ids": ["person_001"], "data": {"title": "Sync"}},
])
# simulate a mid-file corruption (test-side only — production never hand-appends)
ep4.write_text("{{{not json\n" + ep4.read_text(encoding="utf-8"), encoding="utf-8")
c4 = Path(rph.render_person_history(ws4, "person_001")["path"]).read_text(encoding="utf-8")
check("couldn't be read" in c4, "skipped-line banner surfaces the unreadable line")

# --- dirty-checked regen (cleanup 3.5d3 hook) -----------------------------
res = rph.regenerate_changed(ws)
check(res["refreshed"] == [], "an up-to-date view is not re-rendered (dirty check)")
atomic_append_jsonl(ep, [
    {"type": "interaction", "source_skill": "t", "ts": _d(0) + "T09:00:00+00:00",
     "person_ids": ["person_001"], "data": {"summary": "New touch"}},
])

# --- C18: integrity_check flags the now-stale view before the refresh -----
import integrity_check as ic  # noqa: E402
findings = ic.run_checks(ws)
check(any(f.check == "C18.entity_history_stale" and f.subject == "person_001"
          for f in findings),
      "integrity_check C18 flags a history view older than the newest "
      "event tagging that entity")

res2 = rph.regenerate_changed(ws)
check(res2["refreshed"] == ["person_001"],
      "a newer entity-tagged event triggers exactly that view's refresh")
findings2 = ic.run_checks(ws)
check(not any(f.check == "C18.entity_history_stale" for f in findings2),
      "after the refresh the C18 flag clears")

print(f"OK — all {PASS} render_person_history tests passed")
sys.exit(0)
