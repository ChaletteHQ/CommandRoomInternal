#!/usr/bin/env python3
"""Tests for render_people_view.regenerate — the deterministic PEOPLE.md
registry renderer that ends the people-view drift (v3.17.1)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import render_people_view as r  # noqa: E402

PASS = 0


def check(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def _ws(entities, events=None, nested=False):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    obj = {"version": 1, "entities": entities} if nested else {**entities, "version": 1}
    (data / "entities.json").write_text(json.dumps(obj), encoding="utf-8")
    (data / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in (events or [])), encoding="utf-8"
    )
    return d


# All names use approved placeholders (Sam/Bo/Quinn/Mira/Rio + Sample/Stone).
ENT = {
    "people": [
        {"id": "person_001", "canonical_name": "Sam Sample", "role": "CEO",
         "primary_org_id": "org_001", "first_seen": "2026-01-01",
         "emails": ["sam@example.com"]},
        {"id": "person_002", "canonical_name": "Bo Stone",
         "primary_org_id": "org_002", "first_seen": "2026-02-01"},
        {"id": "person_003", "canonical_name": "Quinn Sample", "first_seen": "2026-03-01"},
        {"id": "person_004", "canonical_name": "Mira Stone", "status": "archived",
         "first_seen": "2025-01-01"},
    ],
    "orgs": [
        {"id": "org_001", "canonical_name": "Acme Co", "is_primary_focus": True,
         "relationship_type": "operating", "scope": "operating"},
        {"id": "org_002", "canonical_name": "Northstar Partners",
         "relationship_type": "client", "is_primary_focus": False},
    ],
    "threads": [],
}
EVENTS = [
    {"seq": 1, "ts": "2026-05-20", "type": "interaction", "source_skill": "x",
     "person_ids": ["person_001"], "data": {}},
    {"seq": 2, "ts": "2026-05-25", "type": "meeting", "source_skill": "x",
     "classification_confidence": 0.2, "person_ids": ["person_001"], "data": {}},  # low-conf, ignored
]

# --- flat shape ---
ws = _ws(ENT, EVENTS, nested=False)
res = r.regenerate(ws)
content = (ws / "_hq" / "views" / "PEOPLE.md").read_text(encoding="utf-8")
check(res["active"] == 3, "3 active people counted")
check(res["archived"] == 1, "1 archived counted")
check("Sam Sample" in content, "primary-org person rendered")
check("## Acme Co" in content, "primary-focus org section rendered")
check("## Other Orgs" in content and "Northstar Partners" in content, "other-org grouping")
check("## Unaffiliated" in content and "Quinn Sample" in content, "unaffiliated section")
check("## Archived (1)" in content and "Mira Stone" in content, "archived section")
check("Last interaction:** 2026-05-20" in content, "last-interaction from high-conf event")
check("2026-05-25" not in content, "low-confidence event ignored for last-interaction")
check("sam@example.com" in content, "email rendered")
check((ws / "_hq" / "PEOPLE.md").exists(), "back-compat copy written")

# --- nested shape (shape-defensiveness) ---
ws2 = _ws(ENT, EVENTS, nested=True)
res2 = r.regenerate(ws2)
content2 = (ws2 / "_hq" / "views" / "PEOPLE.md").read_text(encoding="utf-8")
check(res2["active"] == 3 and "Sam Sample" in content2, "nested-shape entities handled")


def _strip_ts(c):
    return "\n".join(ln for ln in c.splitlines() if "regenerated" not in ln)


# --- idempotency (stable apart from regenerated-at header) ---
res3 = r.regenerate(ws)
content3 = (ws / "_hq" / "views" / "PEOPLE.md").read_text(encoding="utf-8")
check(_strip_ts(content) == _strip_ts(content3), "idempotent apart from timestamp")

# --- deprecated singular email field ---
ws4 = _ws({"people": [{"id": "person_009", "canonical_name": "Rio Sample",
                       "email": "rio@example.com", "first_seen": "2026-01-01"}],
           "orgs": [], "threads": []}, [], nested=False)
r.regenerate(ws4)
check("rio@example.com" in (ws4 / "_hq" / "views" / "PEOPLE.md").read_text(encoding="utf-8"),
      "deprecated singular email handled")

# --- empty workspace doesn't crash ---
ws5 = _ws({"people": [], "orgs": [], "threads": []}, [], nested=False)
r.regenerate(ws5)
check((ws5 / "_hq" / "views" / "PEOPLE.md").exists(), "empty workspace renders without crash")

print(f"OK — all {PASS} render_people_view tests passed")
sys.exit(0)
