#!/usr/bin/env python3
"""Tests for the HIST1 Part 2 auto fact tier (spec step 10, D3/S1/S2) —
AUTO_ALLOWED["entity_fact_structured"] + the brain_undo retraction reverser
+ writer-level category enforcement + renderer suppression end-to-end.

Landed-together pin: the spec mandates the AUTO_ALLOWED entry, the reverser,
and the shape classification in ONE commit — these checks fail red if any
half is missing. Fixtures mirror real substrate shapes; dates relative to
today (G14); placeholder names only (Rule 26)."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import brain_proposals as bp  # noqa: E402
import brain_undo as bu  # noqa: E402
import org_writer as ow  # noqa: E402
import people_writer as pw  # noqa: E402
import render_org_history as roh  # noqa: E402
import render_person_history as rph  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [
        # Real-shape: a legacy person missing role/primary_org_id survives.
        {"id": "person_001", "canonical_name": "Sam Sample", "status": "active",
         "first_seen": "2026-01-05"},
    ], "orgs": [
        {"id": "org_001", "canonical_name": "Acme Co", "status": "active",
         "relationship_type": "client", "first_seen": "2026-01-05"},
    ], "threads": [], "engagements": []}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _events(ws, etype=None):
    out = []
    for line in (ws / "_hq" / "data" / "events.jsonl").read_text(
            encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if etype is None or ev.get("type") == etype:
            out.append(ev)
    return out


# --- 1. Registry: both halves landed together (D2 / step-10 one-commit) -----
check("entity_fact_structured" in bp.AUTO_ALLOWED,
      "entity_fact_structured is in AUTO_ALLOWED")
check(bu.has_reverser("entity_fact_structured"),
      "entity_fact_structured has a registered reverser")
check(bu.REVERSERS["entity_fact_structured"]["reverses_via"]
      == "entity_fact_retracted",
      "reverser declares entity_fact_retracted as its reversing event")
for cls in bp.AUTO_ALLOWED:
    check(bu.has_reverser(cls), f"AUTO_ALLOWED class {cls} has a reverser")

# The three AUTO_FACT_CATEGORIES copies stay equal (mirror-drift pin).
check(pw.AUTO_FACT_CATEGORIES == ow.AUTO_FACT_CATEGORIES
      == bp.AUTO_FACT_CATEGORIES == frozenset(
          {"preference", "contact", "personal"}),
      "AUTO_FACT_CATEGORIES copies equal across the three modules (S2)")
check("role" not in bp.AUTO_FACT_CATEGORIES
      and "company_news" not in bp.AUTO_FACT_CATEGORIES,
      "identity-adjacent categories are never auto-eligible (S2)")

# --- 2. Shape classification (N3) -------------------------------------------
check(bp.kind_shape("entity_fact_structured") == "hygiene",
      "entity_fact_structured ranks hygiene (below money/identity)")
check(bp.kind_shape("entity_fact") == "hygiene",
      "confirm-tier entity_fact ranks hygiene")
check(bp.kind_shape("person_update") == "identity",
      "person_update stays identity-shaped")

# --- 3. Writer-level enforcement (code-deep, S2) ----------------------------
ws = _ws()
raised = False
try:
    pw.record_person_fact(ws, "person_001", "Now the CFO", "sig:msg_001",
                          category="role", source_skill="test",
                          brain_batch_id="efb_TEST",
                          brain_change_class="entity_fact_structured")
except ValueError:
    raised = True
check(raised, "auto-class person fact with category=role raises (S2)")

raised = False
try:
    pw.record_person_fact(ws, "person_001", "Prefers Signal", "sig:msg_001",
                          category="preference", source_skill="test",
                          brain_batch_id="efb_TEST",
                          brain_change_class=None)
except ValueError:
    raised = True
check(raised, "batch id without change class raises (stamps travel together)")

raised = False
try:
    ow.record_org_fact(ws, "org_001", "Raised a Series A", "mail:msg_002",
                       category="company_news", source_skill="test",
                       brain_batch_id="efb_TEST",
                       brain_change_class="entity_fact_structured")
except ValueError:
    raised = True
check(raised, "auto-class org fact with category=company_news raises (S2)")

raised = False
try:
    pw.record_person_fact(ws, "person_001", "x", "ref",
                          category="preference", source_skill="test",
                          brain_batch_id="efb_TEST",
                          brain_change_class="some_other_class")
except ValueError:
    raised = True
check(raised, "unknown brain_change_class on a fact write raises")

# --- 4. Auto apply + batch undo end-to-end (D3/S1) --------------------------
BATCH = "efb_" + NOW.strftime("%Y%m%dT%H%M%SZ")
pw.record_person_fact(ws, "person_001", "Prefers Signal over email",
                      "sig:msg_010", category="preference",
                      source_skill="entity-signals",
                      brain_batch_id=BATCH,
                      brain_change_class="entity_fact_structured")
ow.record_org_fact(ws, "org_001", "Office moved to the Denver campus",
                   "cal:evt_011", category="contact",
                   source_skill="entity-signals",
                   brain_batch_id=BATCH,
                   brain_change_class="entity_fact_structured")
pfacts = _events(ws, "person_fact_observed")
ofacts = _events(ws, "org_fact_observed")
check(len(pfacts) == 1 and len(ofacts) == 1, "both auto facts appended")
check(pfacts[0]["data"].get("brain_batch_id") == BATCH
      and pfacts[0]["data"].get("brain_change_class")
      == "entity_fact_structured",
      "person fact carries both undo stamps")
check(ofacts[0]["data"].get("brain_batch_id") == BATCH,
      "org fact carries the batch stamp")

def _render_p(w, pid):
    rph.render_person_history(w, pid)
    return rph.view_path(w, pid).read_text(encoding="utf-8")


def _render_o(w, oid):
    roh.render_org_history(w, oid)
    return roh.view_path(w, oid).read_text(encoding="utf-8")


# Renders show the facts before the undo.
p_md = _render_p(ws, "person_001")
o_md = _render_o(ws, "org_001")
check("Prefers Signal over email" in p_md, "person render shows the fact")
check("Denver campus" in o_md, "org render shows the fact")

res = bu.undo_batch(ws, {"kind": "brain_batch", "batch_id": BATCH},
                    undone_by="person_001", source_skill="test-undo")
check(res["status"] == "undone" and res["n_undone"] == 2 and
      res["n_errors"] == 0, f"undo_batch reverses both facts: {res['status']}")
retractions = _events(ws, "entity_fact_retracted")
check(len(retractions) == 2, "one retraction appended per fact")
by_target = {r["data"]["target_id"]: r["data"] for r in retractions}
check(by_target.get("person_001", {}).get("retracts_seq")
      == pfacts[0]["seq"],
      "person retraction references the fact's seq")
check(by_target.get("org_001", {}).get("retracts_seq") == ofacts[0]["seq"],
      "org retraction references the fact's seq")
check(all(r["data"].get("source_ref") for r in retractions),
      "retractions carry a non-null source_ref (D2/S4 discipline)")
check(len(_events(ws, "brain_change_undone")) == 2,
      "one brain_change_undone marker per reversal")
check(len(_events(ws, "person_fact_observed")) == 1,
      "the fact event itself is never edited or deleted (additive undo)")

# Renderers suppress the retracted facts (Part 1 suppression honored).
p_md2 = _render_p(ws, "person_001")
o_md2 = _render_o(ws, "org_001")
check("Prefers Signal over email" not in p_md2,
      "retracted person fact disappears from the next render (S1)")
check("Denver campus" not in o_md2,
      "retracted org fact disappears from the next render (S1)")

# Double-undo is idempotent in effect (review pin): a second undo_batch on
# the same batch appends further retraction rows (append-only — nothing is
# edited), never un-retracts, never errors, and the render stays suppressed.
res2 = bu.undo_batch(ws, {"kind": "brain_batch", "batch_id": BATCH},
                     undone_by="person_001", source_skill="test-undo")
check(res2["status"] == "undone" and res2["n_errors"] == 0,
      f"second undo of the same batch is clean: {res2['status']}")
check("Prefers Signal over email" not in _render_p(ws, "person_001")
      and "Denver campus" not in _render_o(ws, "org_001"),
      "facts stay suppressed after a double undo (idempotent in effect)")
check(len(_events(ws, "person_fact_observed")) == 1,
      "double undo never edits or deletes the fact event")

# --- 5. Retraction is per-fact, not per-target ------------------------------
ws2 = _ws()
B2 = "efb_partial"
pw.record_person_fact(ws2, "person_001", "Prefers Signal", "sig:1",
                      category="preference", source_skill="entity-signals",
                      brain_batch_id=B2,
                      brain_change_class="entity_fact_structured")
pw.record_person_fact(ws2, "person_001", "Based near the Denver airport",
                      "chat:user-statement", category="personal",
                      source_skill="people-crm")  # explicit — NOT in batch
bu.undo_batch(ws2, {"kind": "brain_batch", "batch_id": B2},
              undone_by="person_001", source_skill="test-undo")
md = _render_p(ws2, "person_001")
check("Prefers Signal" not in md, "batch fact retracted")
check("Denver airport" in md,
      "an explicit user fact outside the batch survives the undo")

# --- 6. propose-path guard (future callers, S2) -----------------------------
ws3 = _ws()
TUPLES = [{"action": "confirm proposal"}, {"action": "dismiss proposal"}]
raised = False
try:
    bp.propose(ws3, kind="entity_fact_structured", fingerprint="ef:1",
               evidence="sig title", action_tuples=TUPLES, tier="auto",
               detector="entity-signals",
               change_class="entity_fact_structured",
               extra={"category": "role"})
except bp.BrainProposalError:
    raised = True
check(raised, "propose(tier=auto) with an identity-adjacent category raises")

r = bp.propose(ws3, kind="entity_fact_structured", fingerprint="ef:2",
               evidence="sig pref", action_tuples=TUPLES, tier="auto",
               detector="entity-signals",
               change_class="entity_fact_structured",
               extra={"category": "preference"})
check(r["status"] == "proposed",
      "propose-path legality holds for an auto-eligible category")
card = bp.select_confirm_card(ws3, "staff-meeting")
check(all(i.get("tier") != "auto" for i in card["items"]),
      "an auto-tier proposal never enters the confirm card (F5 parity)")

print(f"OK — {PASS} checks passed")
