#!/usr/bin/env python3
"""Tests for engagement_writer — typed writer for org<->org engagement edges
(v3.17.2). Mirrors the org_writer guarantees: validation, dedup, referential
integrity, atomic write through entities_io, event emission, immutability."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import engagement_writer as ew  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _ws(nested=False):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    orgs = [
        {"id": "org_001", "canonical_name": "Acme Co"},
        {"id": "org_002", "canonical_name": "Northstar Partners"},
    ]
    ent = {"people": [], "orgs": orgs, "threads": []}
    obj = {"version": 1, "entities": ent} if nested else {**ent, "version": 1}
    (data / "entities.json").write_text(json.dumps(obj), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _engagements(ws, nested=False):
    obj = json.loads((ws / "_hq" / "data" / "entities.json").read_text())
    ent = obj.get("entities") if isinstance(obj.get("entities"), dict) else obj
    return ent.get("engagements", [])


def _events(ws):
    txt = (ws / "_hq" / "data" / "events.jsonl").read_text()
    return [json.loads(ln) for ln in txt.splitlines() if ln.strip()]


# --- create (flat shape) ---
ws = _ws()
rec = ew.create_engagement(ws, from_org_id="org_001", to_org_id="org_002",
                           kind="deal", label="Pilot — AP automation")
check(rec["id"] == "engagement_001", "first id is engagement_001")
check(rec["kind"] == "deal" and rec["is_active"] is True, "fields set + is_active default")
check(len(_engagements(ws)) == 1, "landed in the engagements collection")
evs = _events(ws)
check(any(e["type"] == "engagement_created" for e in evs), "engagement_created event emitted")
check(evs[-1]["data"]["from_org_id"] == "org_001", "event carries endpoints")

# --- nested shape ---
ws2 = _ws(nested=True)
ew.create_engagement(ws2, from_org_id="org_001", to_org_id="org_002", kind="advisor")
check(len(_engagements(ws2, nested=True)) == 1, "nested-shape engagements collection handled")

# --- dedup ---
try:
    ew.create_engagement(ws, from_org_id="org_001", to_org_id="org_002", kind="deal")
    check(False, "dedup should have raised")
except ew.DuplicateEngagementError:
    check(True, "dedup raises DuplicateEngagementError")

rec2 = ew.create_engagement(ws, from_org_id="org_001", to_org_id="org_002",
                            kind="deal", skip_dedup=True)
check(rec2["id"] == "engagement_002", "skip_dedup mints engagement_002")

# --- referential integrity ---
try:
    ew.create_engagement(ws, from_org_id="org_001", to_org_id="org_999", kind="deal")
    check(False, "missing org should have raised")
except ValueError:
    check(True, "missing-org endpoint raises ValueError")

# --- invalid kind ---
try:
    ew.create_engagement(ws, from_org_id="org_001", to_org_id="org_002", kind="bogus")
    check(False, "bad kind should have raised")
except ValueError:
    check(True, "invalid kind raises ValueError")

# --- update + event ---
upd = ew.update_engagement(ws, "engagement_001", is_active=False, notes="closed")
check(upd["is_active"] is False and upd["notes"] == "closed", "update applies fields")
check(any(e["type"] == "engagement_updated" for e in _events(ws)), "engagement_updated event emitted")

# --- immutable endpoints ---
try:
    ew.update_engagement(ws, "engagement_001", from_org_id="org_002")
    check(False, "immutable from_org_id should have raised")
except ValueError:
    check(True, "from_org_id immutable on update")

print(f"OK — all {PASS} engagement_writer tests passed")
sys.exit(0)
