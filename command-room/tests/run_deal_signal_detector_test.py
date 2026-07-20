#!/usr/bin/env python3
"""Tests for deal_signal_detector — the first detector through the Living
Brain rails (SPEC LB1 D7; absorbs PIPE1 Part 2). The detector NEVER writes
deal fields; emission is propose-and-confirm only. Fixtures mirror real
substrate shapes; dates relative to today; placeholder org names only."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import deal_signal_detector as dsd  # noqa: E402
import brain_proposals as bp  # noqa: E402

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
    ent = {"version": 1, "people": [], "orgs": [
        {"id": "org_acme", "canonical_name": "Acme Co",
         "relationship_type": "prospect"},
        {"id": "org_north", "canonical_name": "Northwind",
         "relationship_type": "client"},
        {"id": "org_vendor", "canonical_name": "Vendor Corp",
         "relationship_type": "vendor"},
    ], "threads": [
        {"id": "project_200", "kind": "deal", "status": "active",
         "org_id": "org_north", "display_name": "Northwind retainer",
         "deal": {"stage": "qualified"}},
        {"id": "project_300", "kind": "deal", "status": "resolved",
         "org_id": "org_acme", "display_name": "Old Acme deal",
         "deal": {"stage": "proposal_sent"}},
    ], "engagements": []}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _raw_append(ws, rows):
    path = ws / "_hq" / "data" / "events.jsonl"
    existing = path.read_text(encoding="utf-8")
    seq = existing.count("\n")
    lines = []
    for r in rows:
        seq += 1
        r.setdefault("seq", seq)
        r.setdefault("ts", _iso(NOW - timedelta(days=2)))
        lines.append(json.dumps(r))
    path.write_text(existing + "".join(l + "\n" for l in lines),
                    encoding="utf-8")


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


# --- marker → proposal mapping ------------------------------------------------
ws = _ws()
_raw_append(ws, [
    # stage signal on the OPEN Northwind deal
    {"type": "meeting", "source_skill": "meeting-notes",
     "org_ids": ["org_north"], "related_thread_ids": ["project_200"],
     "data": {"title": "Northwind sync",
              "summary": "they verbally agreed to move ahead"}},
    # won-language on the same deal (imported CONVERSION_MARKERS vocabulary)
    {"type": "interaction", "source_skill": "session-sweep",
     "org_ids": ["org_north"],
     "data": {"summary": "Northwind signed the engagement agreement"}},
    # money near the open deal (no value on file)
    {"type": "note", "source_skill": "meeting-notes",
     "org_ids": ["org_north"],
     "data": {"summary": "retainer scoped at $25k for the first phase"}},
    # creation signal on Acme (prospect, no OPEN deal thread — project_300 is resolved)
    {"type": "interaction", "source_skill": "session-sweep",
     "org_ids": ["org_acme"],
     "data": {"summary": "Acme asked for pricing on the pilot"}},
    # vendor org — not tracked as prospect/client, must not propose
    {"type": "interaction", "source_skill": "session-sweep",
     "org_ids": ["org_vendor"],
     "data": {"summary": "Vendor Corp sent the proposal for their tooling"}},
])
cands = dsd.detect_deal_signals(ws)
kinds = sorted((c["kind"], c["proposal_kind"]) for c in cands)
check(("deal_update", "stage") in kinds, "stage marker → stage proposal")
check(("deal_update", "won") in kinds, "won language → won proposal")
check(("deal_update", "value") in kinds, "money amount → value proposal")
check(("deal_creation", "creation") in kinds,
      "deal-shaped signal on untracked org → CREATION proposal (M's Part 2 add)")
check(not any(c["org_id"] == "org_vendor" for c in cands),
      "vendor orgs never propose")
stage = next(c for c in cands if c["proposal_kind"] == "stage")
check(stage["proposed_stage"] == "negotiating" and stage["thread_id"] == "project_200",
      "stage proposal names the stage + embeds the thread id verbatim")
value = next(c for c in cands if c["proposal_kind"] == "value")
check(value["proposed_value"] == 25000.0, "money parse ($25k → 25000)")
for c in cands:
    check(c["render_line"].strip() != "" and c["fingerprint"],
          f"candidate carries render_line + fingerprint ({c['proposal_kind']})")

# a deal already AT the marked stage does not re-propose that stage
ws2 = _ws()
ent = json.loads((ws2 / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
ent["threads"][0]["deal"]["stage"] = "negotiating"
(ws2 / "_hq" / "data" / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
_raw_append(ws2, [
    {"type": "meeting", "source_skill": "meeting-notes",
     "org_ids": ["org_north"],
     "data": {"summary": "still negotiating the redlines"}},
])
check(not any(c["proposal_kind"] == "stage"
              for c in dsd.detect_deal_signals(ws2)),
      "no stage proposal when the deal already sits at that stage")

# --- the job: propose-only, receipt, cooldown --------------------------------
before_ent = (ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8")
res = dsd.run_deal_signal_job(ws, fired_via="manual")
check(res["n_candidates"] == len(cands) and res["n_proposed"] == len(cands),
      "job proposes every candidate")
check((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8")
      == before_ent,
      "the detector NEVER writes deal fields (entities.json untouched)")
check(len(_events(ws, "brain_proposal")) == len(cands),
      "one brain_proposal per candidate")
n_legacy = len(_events(ws, "deal_update_proposed"))
n_updates = sum(1 for c in cands if c["kind"] == "deal_update")
check(n_legacy == n_updates,
      "reserved PIPE1 type written alongside for deal_update kinds only")
receipts = [e for e in _events(ws, "pack_run")
            if e["data"].get("task_id") == "deal-signals"]
check(len(receipts) == 1 and receipts[0]["data"]["fired_via"] == "manual",
      "job writes its deal-signals pack_run receipt via log_receipt")
check(dsd.validate_deal_signals_ran(ws)["ok"], "receipt validator reads it back")

# re-run: open-dedup suppresses everything, receipt still writes
res2 = dsd.run_deal_signal_job(ws, fired_via="scheduled")
check(res2["n_proposed"] == 0 and res2["n_suppressed"] == len(cands),
      "re-run suppresses all open fingerprints")

# declined proposal honors the 60d ledger cooldown on the NEXT run
target = next(i for i in bp.load_open_proposals(ws)
              if i["kind"] == "deal_creation")
bp.resolve_proposal(ws, target["id"], "declined",
                    resolved_by="person_001", source_skill="apply-choices")
res3 = dsd.run_deal_signal_job(ws, fired_via="scheduled")
check(res3["n_proposed"] == 0,
      "declined creation proposal does not re-propose (ledger cooldown)")
check(len(_events(ws, "deal_update_dismissed")) == 0,
      "creation decline writes no thread-keyed dismissal (no thread exists)")

# fresh workspace with no signal: clean zero
ws3 = _ws()
res4 = dsd.run_deal_signal_job(ws3, fired_via="scheduled")
check(res4["n_candidates"] == 0 and res4["n_proposed"] == 0,
      "quiet workspace proposes nothing")

# --- D9.1 deal genesis: sales-typed meeting, zero marker text -----------------
# Live-shape mirror (real-data fixture gotcha): the meeting event carries NO
# org reference at all — primary_thread_id resolves to the CEO's OWN product
# thread and person_ids holds only the CEO; the prospect org appears only in
# the title. This is byte-for-byte the live gap shape D9.1 exists to close.
ws4 = _ws()
_raw_append(ws4, [
    {"type": "meeting", "source_skill": "past-meetings",
     "primary_thread_id": "project_900", "person_ids": ["person_001"],
     "data": {"title": "Acme Co — platform follow-up (Jordan Reyes)",
              "source_ref": "granola:g1", "duration_min": 60,
              "attendees_external": ["Jordan Reyes"],
              "meeting_type": "sales"}},
])
g = [c for c in dsd.detect_deal_signals(ws4) if c["kind"] == "deal_creation"]
check(len(g) == 1 and g[0]["org_id"] == "org_acme",
      "D9.1 genesis: unreferenced sales-typed meeting reaches the org via the "
      "bounded name lane (the live gap shape)")
check("sales-typed meeting" in g[0]["evidence"],
      "genesis evidence names the sales-typed meeting, not phantom language")
check("say `new deal" in g[0]["nudge_line"],
      "genesis nudge_line teaches the live `new deal` trigger")

# structural lane: org_ids stamped on the meeting event (the step-8 fix) —
# genesis fires with no name match needed and no marker text.
ws5 = _ws()
_raw_append(ws5, [
    {"type": "meeting", "source_skill": "past-meetings",
     "org_ids": ["org_acme"], "person_ids": ["person_001"],
     "data": {"title": "intro sync", "source_ref": "granola:g2",
              "meeting_type": "sales"}},
])
g5 = [c for c in dsd.detect_deal_signals(ws5) if c["kind"] == "deal_creation"]
check(len(g5) == 1 and g5[0]["org_id"] == "org_acme",
      "D9.1 genesis: org_ids-stamped sales meeting proposes creation structurally")

# a NON-sales meeting with no pursuit language proposes nothing — the intro
# call that should NOT manufacture a pipeline entry (spec's rejected case).
ws6 = _ws()
_raw_append(ws6, [
    {"type": "meeting", "source_skill": "past-meetings",
     "org_ids": ["org_acme"],
     "data": {"title": "Acme Co intro chat", "source_ref": "granola:g3",
              "meeting_type": "internal_1_1"}},
    {"type": "meeting", "source_skill": "past-meetings",
     "org_ids": ["org_acme"],
     "data": {"title": "Acme Co coffee catch-up", "source_ref": "granola:g4"}},
])
check(dsd.detect_deal_signals(ws6) == [],
      "non-sales / untyped meetings with no pursuit language propose nothing")

# an org with an OPEN deal never gets a genesis proposal from a sales meeting
# (no duplicate — spec acceptance #12).
ws7 = _ws()
_raw_append(ws7, [
    {"type": "meeting", "source_skill": "past-meetings",
     "org_ids": ["org_north"],
     "data": {"title": "Northwind working session", "source_ref": "granola:g5",
              "meeting_type": "sales"}},
])
check(not any(c["kind"] == "deal_creation"
              for c in dsd.detect_deal_signals(ws7)),
      "sales meeting on an org with an open deal proposes no genesis duplicate")

# confirm-path closes the loop: create_deal (what apply-choices dispatches on
# confirm) → exactly one deal exists → re-detection proposes no genesis.
import deal_state  # noqa: E402
t = deal_state.create_deal(ws4, name="Acme platform", org_id="org_acme",
                           source_skill="apply-choices")
ent4 = json.loads((ws4 / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))


def _torg(th):
    return (th.get("org") or th.get("org_id") or th.get("affiliation_id")
            or (th.get("affiliation_ids") or [None])[0])


n_open_acme = sum(1 for th in ent4["threads"]
                  if th.get("kind") == "deal" and _torg(th) == "org_acme"
                  and th.get("status") not in ("resolved", "archived"))
check(n_open_acme == 1, "confirming creates exactly ONE deal")
check(not any(c["kind"] == "deal_creation"
              for c in dsd.detect_deal_signals(ws4)),
      "post-confirm re-detection proposes no second genesis (coverage holds)")

# org_ids scoping (the meeting-time hook): only the passed orgs' candidates
# come back; the surface never hand-filters (Bug #92b stays in code).
ws8 = _ws()
_raw_append(ws8, [
    {"type": "meeting", "source_skill": "past-meetings",
     "org_ids": ["org_acme"],
     "data": {"title": "scoping", "source_ref": "granola:g6",
              "meeting_type": "sales"}},
    {"type": "interaction", "source_skill": "session-sweep",
     "org_ids": ["org_north"],
     "data": {"summary": "Northwind verbally agreed to move ahead"}},
])
scoped = dsd.detect_deal_signals(ws8, org_ids=["org_acme"])
check([c["org_id"] for c in scoped] == ["org_acme"],
      "org_ids scoping returns only the meeting's own orgs")
check(len(dsd.detect_deal_signals(ws8)) == 2,
      "unscoped detection still sees both orgs' signals")

# propose_candidates (the scoped hook's emitter): proposes through the brain
# rails but writes NO deal-signals pack_run receipt — that receipt belongs to
# the scheduled job alone.
counts = dsd.propose_candidates(ws8, scoped)
check(counts["n_proposed"] == 1,
      "propose_candidates emits the scoped proposal")
check(len([e for e in _events(ws8, "pack_run")
           if e.get("data", {}).get("task_id") == "deal-signals"]) == 0,
      "the meeting-time hook writes no deal-signals job receipt")
check(len(_events(ws8, "brain_proposal")) == 1,
      "scoped propose goes through the Living Brain gate")

print(f"OK — {PASS} checks passed")
