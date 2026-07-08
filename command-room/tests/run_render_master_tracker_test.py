#!/usr/bin/env python3
"""Tests for render_master_tracker.regenerate — the deterministic
MASTER_TRACKER.md renderer that ends the tracker freeze (v4.2.0).

Covers: determinism + counts, org-tree layout (primary / holding-nested /
OTHER ORGS), paused-blocked + archived sections, the 5 commitment shape
variants read through cru_match, low-confidence + closed-commitment exclusion,
both write paths, idempotence, and an empty workspace."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import render_master_tracker as r  # noqa: E402

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


# Org tree: Acme Co (primary, operating) ; Category Company (primary holding)
# with two operating children ; Northstar Partners (non-primary client).
ENT = {
    "orgs": [
        {"id": "org_001", "canonical_name": "Acme Co", "is_primary_focus": True,
         "relationship_type": "operating", "scope": "operating"},
        {"id": "org_holdco", "canonical_name": "Category Company", "is_primary_focus": True,
         "relationship_type": "operating", "scope": "holding"},
        {"id": "org_rest", "canonical_name": "Acme Restaurant", "parent_org_id": "org_holdco",
         "relationship_type": "operating", "scope": "operating"},
        {"id": "org_bake", "canonical_name": "Acme Bakery", "parent_org_id": "org_holdco",
         "relationship_type": "operating", "scope": "operating"},
        {"id": "org_ns", "canonical_name": "Northstar Partners", "is_primary_focus": False,
         "relationship_type": "client"},
    ],
    "threads": [
        {"id": "project_a", "display_name": "GTM Launch", "kind": "initiative",
         "status": "active", "stage": "build", "affiliation_id": "org_001",
         "owner_person_id": "person_001", "next_step": "ship beta", "first_seen": "2026-05-01"},
        {"id": "project_r", "display_name": "Menu Revamp", "kind": "initiative",
         "status": "active", "affiliation_id": "org_rest", "first_seen": "2026-05-02"},
        {"id": "project_b", "display_name": "Oven Deal", "kind": "deal",
         "status": "active", "affiliation_id": "org_bake", "first_seen": "2026-05-03"},
        {"id": "project_ns", "display_name": "Northstar Pilot", "kind": "deal",
         "status": "active", "affiliation_id": "org_ns", "first_seen": "2026-05-04"},
        {"id": "project_pause", "display_name": "Paused Thing", "kind": "initiative",
         "status": "paused", "affiliation_id": "org_001", "reason": "waiting on legal",
         "first_seen": "2026-04-01"},
        {"id": "project_arch", "display_name": "Old Thing", "kind": "initiative",
         "status": "archived", "affiliation_id": "org_001", "archived_at": "2026-05-10",
         "archive_reason": "completed", "first_seen": "2026-01-01"},
    ],
    "people": [
        {"id": "person_001", "canonical_name": "Sam Sample"},
        {"id": "person_002", "canonical_name": "Bo Stone"},
    ],
}

# Activity events drive last-activity + recency ordering.
EVENTS = [
    {"seq": 1, "ts": "2026-05-20", "type": "status_change", "primary_thread_id": "project_a"},
    {"seq": 2, "ts": "2026-05-25", "type": "meeting", "primary_thread_id": "project_r"},
    {"seq": 3, "ts": "2026-05-26", "type": "meeting", "primary_thread_id": "project_b"},
    # --- 5 commitment shape variants, all OPEN, all owned by Sam (person_001) ---
    # 1. canonical: data.owner_id / data.title / data.due / data.status
    {"seq": 10, "ts": "2026-05-21", "type": "commitment", "primary_thread_id": "project_a",
     "person_ids": ["person_001"],
     "data": {"id": "c_canon", "owner_id": "person_001", "title": "Send canonical recap",
              "due": "2026-06-01", "status": "open", "confidence": 0.9}},
    # 2. flat-new: top-level owner_id (not nested under data)
    {"seq": 11, "ts": "2026-05-21", "type": "commitment", "primary_thread_id": "project_a",
     "owner_id": "person_001", "title": "Send flat recap", "due": "2026-06-02",
     "status": "open", "data": {"id": "c_flat", "confidence": 0.9}},
    # 3. legacy `owner` (no _id suffix)
    {"seq": 12, "ts": "2026-05-21", "type": "commitment", "primary_thread_id": "project_a",
     "data": {"id": "c_legacy", "owner": "person_001", "title": "Send legacy recap",
              "due": "2026-06-03", "status": "open", "confidence": 0.9}},
    # 4. owner_person_id-variant (cr-past-meetings) + `state` + string confidence
    {"seq": 13, "ts": "2026-05-21", "type": "commitment", "primary_thread_id": "project_r",
     "data": {"id": "c_opid", "owner_person_id": "person_001", "title": "Send variant recap",
              "due_date": "2026-06-04", "state": "open", "confidence": "HIGH"}},
    # 5. pending_review (uncertain) — still surfaced if confidence clears the floor
    {"seq": 14, "ts": "2026-05-21", "type": "commitment", "primary_thread_id": "project_b",
     "data": {"id": "c_pending", "owner_id": "person_002", "title": "Maybe send pending recap",
              "due": "2026-06-05", "status": "open", "confidence": 0.8, "pending_review": True}},
    # low-confidence commitment — must be EXCLUDED from the table
    {"seq": 15, "ts": "2026-05-21", "type": "commitment", "primary_thread_id": "project_a",
     "data": {"id": "c_lowconf", "owner_id": "person_001", "title": "Low conf recap",
              "due": "2026-06-06", "status": "open", "confidence": 0.2}},
    # closed commitment + its resolver — must be EXCLUDED (load_open_commitments drops it)
    {"seq": 16, "ts": "2026-05-21", "type": "commitment", "primary_thread_id": "project_a",
     "data": {"id": "c_closed", "owner_id": "person_001", "title": "Already done recap",
              "due": "2026-06-07", "status": "open", "confidence": 0.9}},
    {"seq": 17, "ts": "2026-05-22", "type": "commitment_resolved", "primary_thread_id": "project_a",
     "data": {"commitment_id": "c_closed", "resolved_by": "person_001", "evidence": "sent"}},
]

# --- flat shape ---
ws = _ws(ENT, EVENTS, nested=False)
res = r.regenerate(ws)
content = (ws / "_hq" / "views" / "MASTER_TRACKER.md").read_text(encoding="utf-8")

check("# Master Tracker" in content, "title rendered")
check(res["active_threads"] == 4, f"4 active threads counted (got {res['active_threads']})")
check(res["primary_orgs"] == 2, f"2 primary orgs with active threads (got {res['primary_orgs']})")

# org-tree layout
check("## Acme Co" in content, "primary operating org section")
check("## Category Company" in content, "holding org section")
check("### Acme Restaurant" in content and "### Acme Bakery" in content,
      "operating children nested under holding")
check("## Other Orgs" in content and "### Client" in content and "Northstar Partners" in content,
      "non-primary org rolled into OTHER ORGS by relationship_type")
check("GTM Launch" in content and "ship beta" in content and "Sam Sample" in content,
      "thread row renders display_name + next_step + owner")

# paused / archived sections
check("## Paused / Blocked" in content and "Paused Thing" in content, "paused section")
check("## Recently Archived" in content and "Old Thing" in content, "archived section")
check("Paused Thing" not in content.split("## Paused / Blocked")[0],
      "paused thread not in the active org tables")

# --- 5 commitment shape variants all surface ---
check("## Open Commitments" in content, "open-commitments section")
for title in ("Send canonical recap", "Send flat recap", "Send legacy recap",
              "Send variant recap", "Maybe send pending recap"):
    check(title in content, f"commitment shape surfaced: {title}")
# Stage A: the headline is the CANONICAL total from count_commitments (all 6
# open, incl. the low-confidence provisional one) — the confidence floor only
# filters table ROWS. Reporting len(shown)=5 here was the tracker's
# Bug-#85-class divergence (2026-07-01 audit: tracker 54 vs replay 105).
check(res["open_commitments"] == 6,
      f"headline = canonical total 6, not the filtered row count (got {res['open_commitments']})")

# exclusions (from the TABLE, not the headline)
check("Low conf recap" not in content, "low-confidence commitment excluded from table rows")
check("Already done recap" not in content, "closed commitment excluded")
check(res["provisional_commitments"] == 1, "1 provisional (low-conf) commitment counted")

# owner resolves to a name, never a raw id
check("person_001" not in content, "no raw person ids leak into the view")
check("org_001" not in content and "project_a" not in content, "no raw entity ids leak")

# both write paths
check((ws / "_hq" / "MASTER_TRACKER.md").exists(), "back-compat copy written")
check((ws / "_hq" / "MASTER_TRACKER.md").read_text(encoding="utf-8") == content,
      "back-compat copy is byte-identical to the canonical view")

# --- nested shape (shape-defensiveness) ---
ws2 = _ws(ENT, EVENTS, nested=True)
res2 = r.regenerate(ws2)
content2 = (ws2 / "_hq" / "views" / "MASTER_TRACKER.md").read_text(encoding="utf-8")
check(res2["active_threads"] == 4 and "## Acme Co" in content2, "nested-shape entities handled")


def _strip_ts(c):
    return "\n".join(ln for ln in c.splitlines()
                     if "regenerated-at" not in ln and "· regenerated " not in ln)


# --- idempotency (stable apart from the timestamp header) ---
res3 = r.regenerate(ws)
content3 = (ws / "_hq" / "views" / "MASTER_TRACKER.md").read_text(encoding="utf-8")
check(_strip_ts(content) == _strip_ts(content3), "idempotent apart from timestamp")

# --- regenerate_if_changed: no-op on a quiet workspace ---
rc = r.regenerate_if_changed(ws)
check(rc["changed"] is False, "regenerate_if_changed is a no-op when nothing changed")

# regenerate_if_changed heals a missing back-compat copy
(ws / "_hq" / "MASTER_TRACKER.md").unlink()
rc2 = r.regenerate_if_changed(ws)
check(rc2["changed"] is True and (ws / "_hq" / "MASTER_TRACKER.md").exists(),
      "regenerate_if_changed restores a missing back-compat copy")

# --- empty workspace doesn't crash ---
ws5 = _ws({"orgs": [], "threads": [], "people": []}, [], nested=False)
r.regenerate(ws5)
check((ws5 / "_hq" / "views" / "MASTER_TRACKER.md").exists(),
      "empty workspace renders without crash")

print(f"OK — all {PASS} render_master_tracker tests passed")
sys.exit(0)
