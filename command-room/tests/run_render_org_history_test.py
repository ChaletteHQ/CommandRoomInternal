#!/usr/bin/env python3
"""Tests for render_org_history (SPEC HIST1 D5 — the NET-NEW org history
surface): money tag via quantify, open-deal block (PIPE1), people-movement
joined/left from person_org_changed, context block from org_fact_observed,
seq-dedup (N1), zero-history header-only card, malformed-line tolerance,
leak-scanner cleanliness, dirty-checked regen. Fixtures mirror real
substrate shapes; dates relative to today (G14); placeholder names only."""
import datetime
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import render_org_history as roh  # noqa: E402
from atomic_write import atomic_append_jsonl  # noqa: E402
from chat_output_renderer import scan_for_id_leaks  # noqa: E402
import people_writer as pw  # noqa: E402
import org_writer as ow  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


TODAY = datetime.date.today()


def _d(days_ago: int) -> str:
    return (TODAY - datetime.timedelta(days=days_ago)).isoformat()


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {
        "people": [
            {"id": "person_001", "canonical_name": "Sam Sample", "first_seen": _d(40),
             "role": "VP Ops", "primary_org_id": "org_001"},
            {"id": "person_002", "canonical_name": "Quinn Stone", "first_seen": _d(40),
             "role": "CFO", "primary_org_id": "org_001"},
        ],
        "orgs": [
            # Real-data shapes: a legacy off-enum relationship_type on the
            # SECOND org (advisory-only, must not crash anything) and a
            # stale last_interaction fossil on the org under test.
            {"id": "org_001", "canonical_name": "Acme Co", "relationship_type": "client",
             "tier": "secondary", "first_seen": _d(40), "last_interaction": _d(39)},
            {"id": "org_002", "canonical_name": "Northstar Partners",
             "relationship_type": "network"},
        ],
        # Both `threads`-key and PIPE1 deal shapes.
        "threads": [
            {"id": "project_001", "display_name": "Acme Pilot", "affiliation_id": "org_001",
             "kind": "deal", "deal": {"value": 52000, "stage": "negotiating"}},
            {"id": "project_002", "display_name": "Acme Ops", "affiliation_id": "org_001"},
        ],
        "version": 1,
    }
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


# --- rich fixture ---------------------------------------------------------
ws = _ws()
ep = ws / "_hq" / "data" / "events.jsonl"
atomic_append_jsonl(ep, [
    {"type": "meeting", "source_skill": "t", "ts": _d(30) + "T10:00:00+00:00",
     "person_ids": ["person_001"], "org_ids": ["org_001"], "data": {"title": "Kickoff"}},
    # the SAME event reachable twice would double-count without seq-dedup —
    # this one carries the org directly AND via an affiliated thread (N1)
    {"type": "meeting", "source_skill": "t", "ts": _d(16) + "T10:00:00+00:00",
     "person_ids": ["person_001"], "org_ids": ["org_001"],
     "primary_thread_id": "project_002", "data": {"title": "Pricing review"}},
])
# money via the sanctioned writer; a recorded fact; a person moving away
ow.set_org_money(ws, "org_001", {"account_value": 120000, "source": "user statement"},
                 source_skill="workspace-manager", confirmed=True)
ow.record_org_fact(ws, "org_001", "Raised a Series A", "chat:user-statement",
                   category="company_news", source_skill="workspace-manager")
pw.update_person(ws, "person_002", primary_org_id="org_002", source_skill="people-crm")

r = roh.render_org_history(ws, "org_001")
content = Path(r["path"]).read_text(encoding="utf-8")

check("# Acme Co" in content, "header carries the org name")
check("$120K" in content, "money tag renders from the grouped money object via quantify")
check("client" in content and "secondary" in content, "relationship/tier line renders")
check("## Open deals" in content and "Acme Pilot" in content and "$52K deal" in content,
      "open-deal block renders the PIPE1 stated value — never estimated")
check("## People movement" in content and "Quinn Stone left" in content
      and "Northstar Partners" in content,
      "people-movement block shows who left and where they went")
check("## Context & news" in content and "Raised a Series A" in content,
      "context block renders the recorded org fact")
check(f"**Last touch:** {_d(0)}" in content or "**Last touch:**" in content,
      "derived last-touch renders")
check(_d(39) not in content, "the stored last_interaction fossil never renders")
check(scan_for_id_leaks(content) == [], "rendered org view is leak-scanner clean")

# seq-dedup: the double-reachable meeting appears exactly once in the timeline
check(content.count("Pricing review") == 1,
      "an event reachable via org_ids AND an affiliated thread renders once (N1)")

# meeting count feeds cadence from exactly two meetings
compiled = roh.compile_org_history(ws, "org_001")
check(compiled["people_count"] == 1,
      "people count reflects current affiliation (the mover left)")
check(compiled["deal_total"] == 1 and compiled["open_deal_rows"],
      "deal stats read the PIPE1 thread objects")

# --- zero-history org: header-only, no crash ------------------------------
c2 = Path(roh.render_org_history(ws, "org_002")["path"]).read_text(encoding="utf-8")
check("# Northstar Partners" in c2,
      "zero-history org (legacy off-enum relationship_type) renders header-only without crashing")
check("## Relationship timeline" not in c2 and "## Open deals" not in c2,
      "zero-history card drops every empty section")
check(scan_for_id_leaks(c2) == [], "zero-history card is leak-clean")

# --- retraction suppression -----------------------------------------------
evs = [json.loads(ln) for ln in ep.read_text(encoding="utf-8").splitlines() if ln.strip()]
fact_seq = next(e["seq"] for e in evs if e["type"] == "org_fact_observed")
atomic_append_jsonl(ep, [{
    "type": "entity_fact_retracted", "source_skill": "t",
    "data": {"target_id": "org_001", "retracts_seq": fact_seq,
             "source_ref": "undo:test"},
}])
c3 = Path(roh.render_org_history(ws, "org_001")["path"]).read_text(encoding="utf-8")
check("Raised a Series A" not in c3.split("## Context & news")[0] and
      "## Context & news" not in c3,
      "a retracted org fact disappears from the next render (block drops empty)")

# --- malformed line tolerated with the skipped banner ---------------------
ep.write_text("{{{not json\n" + ep.read_text(encoding="utf-8"), encoding="utf-8")
c4 = Path(roh.render_org_history(ws, "org_001")["path"]).read_text(encoding="utf-8")
check("couldn't be read" in c4, "skipped-line banner surfaces the unreadable line")

# --- dirty-checked regen (cleanup 3.5d3 hook) -----------------------------
res = roh.regenerate_changed(ws)
check("org_001" not in res["refreshed"], "an up-to-date view is not re-rendered")
atomic_append_jsonl(ep, [
    {"type": "interaction", "source_skill": "t", "ts": _d(0) + "T09:00:00+00:00",
     "org_ids": ["org_001"], "data": {"summary": "New touch"}},
])
res2 = roh.regenerate_changed(ws)
check("org_001" in res2["refreshed"],
      "a newer org-tagged event triggers that view's refresh")

print(f"OK — all {PASS} render_org_history tests passed")
sys.exit(0)
