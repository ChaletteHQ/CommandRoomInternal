#!/usr/bin/env python3
"""Tests for the SPEC HIST1 lineage layer in people_writer — the
person_role_changed / person_org_changed auto-emit at the update_person
before/after site, the backfill-churn gate, suppress_lineage, and
record_person_fact. House convention: check(cond, msg), non-zero exit on
fail, synthetic workspace, fixture dates computed RELATIVE TO TODAY
(hardcoded-future-date gotcha), placeholder names only (Sam Sample /
Acme Co — Rule 26)."""
import datetime
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import people_writer as pw  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


TODAY = datetime.date.today()
D30 = (TODAY - datetime.timedelta(days=30)).isoformat()


def _ws(people=None):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {
        "people": people if people is not None else [
            {"id": "person_001", "canonical_name": "Sam Sample",
             "first_seen": D30, "role": "Advisor", "primary_org_id": "org_001"},
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


def _events(ws):
    txt = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    return [json.loads(ln) for ln in txt.splitlines() if ln.strip()]


def _record(ws, pid="person_001"):
    doc = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    ent = doc.get("entities") if isinstance(doc.get("entities"), dict) else doc
    return next(p for p in ent["people"] if p["id"] == pid)


# --- role change emits person_role_changed AND person_updated -------------
ws = _ws()
pw.update_person(ws, "person_001", role="CRO", source_skill="people-crm")
evs = _events(ws)
types = [e["type"] for e in evs]
check("person_updated" in types, "role change still emits person_updated")
check("person_role_changed" in types, "role change emits person_role_changed")
rc = next(e for e in evs if e["type"] == "person_role_changed")
check(rc["data"]["from_role"] == "Advisor" and rc["data"]["to_role"] == "CRO",
      "lineage carries from_role/to_role — the prior title is preserved")
check(rc["data"]["person_id"] == "person_001", "lineage carries person_id")
check(_record(ws)["role"] == "CRO", "head field still updates")

# --- source_ref is synthesized, never null (S4/D10) -----------------------
upd_seq = next(e["seq"] for e in evs if e["type"] == "person_updated")
check(rc["data"]["source_ref"] == f"update:people-crm:{upd_seq}",
      "source_ref synthesized from the triggering update's seq")
check(rc["data"].get("summary"), "lineage carries a human-readable summary (change-feed input)")

# --- org change emits person_org_changed with both endpoints --------------
pw.update_person(ws, "person_001", primary_org_id="org_002", source_skill="people-crm")
evs = _events(ws)
oc = next(e for e in evs if e["type"] == "person_org_changed")
check(oc["data"]["from_org_id"] == "org_001" and oc["data"]["to_org_id"] == "org_002",
      "person_org_changed carries from/to org")
check(set(oc.get("org_ids") or []) == {"org_001", "org_002"},
      "top-level org_ids carries both endpoints (org-history join key)")

# --- name-only edit emits NEITHER lineage event ---------------------------
ws2 = _ws()
pw.update_person(ws2, "person_001", canonical_name="Sam Sample Jr", source_skill="people-crm")
types2 = [e["type"] for e in _events(ws2)]
check("person_updated" in types2, "name edit emits person_updated")
check("person_role_changed" not in types2 and "person_org_changed" not in types2,
      "a name-only edit emits no lineage event")

# --- backfill gate: filling an EMPTY role/org emits nothing ---------------
ws3 = _ws(people=[{"id": "person_001", "canonical_name": "Sam Sample",
                   "first_seen": D30}])  # legacy record: no role, no org
pw.update_person(ws3, "person_001", role="Advisor", primary_org_id="org_001",
                 source_skill="people-crm")
types3 = [e["type"] for e in _events(ws3)]
check("person_role_changed" not in types3 and "person_org_changed" not in types3,
      "legacy record missing role/org tolerated — first fill is enrichment, not a move")

# --- suppress_lineage: the migration path emits nothing -------------------
ws4 = _ws()
pw.update_person(ws4, "person_001", primary_org_id="org_002",
                 source_skill="migration", suppress_lineage=True)
types4 = [e["type"] for e in _events(ws4)]
check("person_org_changed" not in types4,
      "suppress_lineage=True (migration set) emits no lineage event")
check("person_updated" in types4, "suppressed path still emits person_updated")

# --- record_person_fact: appends the event, never mutates the record ------
ws5 = _ws()
before_record = dict(_record(ws5))
ev = pw.record_person_fact(ws5, "person_001", "Prefers Signal over email",
                           "chat:user-statement", category="preference",
                           source_skill="people-crm")
check(ev["type"] == "person_fact_observed", "record_person_fact appends person_fact_observed")
evs5 = _events(ws5)
fact = next(e for e in evs5 if e["type"] == "person_fact_observed")
check(fact["data"]["fact"] == "Prefers Signal over email"
      and fact["data"]["category"] == "preference"
      and fact["data"]["source_ref"] == "chat:user-statement",
      "fact payload carries fact/category/source_ref")
check(_record(ws5) == before_record, "record_person_fact does NOT mutate the person record")

# --- fact guards ----------------------------------------------------------
try:
    pw.record_person_fact(ws5, "person_001", "x", "")
    check(False, "empty source_ref should raise — facts are always sourced")
except ValueError:
    check(True, "empty source_ref raises (facts are always sourced)")
try:
    pw.record_person_fact(ws5, "person_001", "x", "chat:user-statement", category="bogus")
    check(False, "bad category should raise")
except ValueError:
    check(True, "unknown category raises")
try:
    pw.record_person_fact(ws5, "person_999", "x", "chat:user-statement")
    check(False, "unknown person should raise")
except KeyError:
    check(True, "unknown person_id raises KeyError (ENTITY_RESOLVE runs first)")

# --- machine attribution never emits lineage (review fix, D2/§8) ----------
# attribute_person_to_org is a domain-match auto-attach, not a confirmed
# career move — even a RE-attribution of an already-attached person (both
# sides non-empty, which the churn gate alone would let through) must not
# emit person_org_changed.
import org_writer as ow_lineage  # noqa: E402

ws6 = _ws()
doc6 = json.loads((ws6 / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
next(o for o in doc6["orgs"] if o["id"] == "org_002")["domains"] = ["northstar.example"]
(ws6 / "_hq" / "data" / "entities.json").write_text(json.dumps(doc6), encoding="utf-8")
# person_001 already carries primary_org_id=org_001; the domain matches org_002
matched, reason = ow_lineage.attribute_person_to_org(
    ws6, "person_001", work_domains=["northstar.example"], source_skill="apply-choices")
check(matched is not None and matched["id"] == "org_002",
      "re-attribution applied (fixture sanity)")
types6 = [e["type"] for e in _events(ws6)]
check("person_org_changed" not in types6,
      "machine re-attribution emits NO lineage event (suppress_lineage on the attach path)")
check("person_updated" in types6, "the attach itself still logs person_updated")

# --- no hand-rolled appends in the module ---------------------------------
src = (Path(__file__).resolve().parent.parent / "shared" / "scripts" / "people_writer.py").read_text(encoding="utf-8")
check(not re.search(r"open\([^)]*['\"]a['\"]", src),
      "people_writer has no direct open(..., 'a') append — atomic_append_jsonl only")

print(f"OK — all {PASS} people_writer lineage tests passed")
sys.exit(0)
