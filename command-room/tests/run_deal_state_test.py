#!/usr/bin/env python3
"""Tests for deal_state — the single deal writer/closure path (SPEC PIPE1).
Mirrors run_engagement_writer_test.py conventions: typed-writer validation,
event emission, idempotency, and the real-data fixture shapes (a pre-PIPE1
kind='deal' thread with NO deal object must be tolerated, offered adoption,
and never crash a reader)."""
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import deal_state as ds  # noqa: E402
import thread_writer as tw  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    orgs = [
        {"id": "org_me", "canonical_name": "My Holdco", "is_primary_focus": True,
         "relationship_type": "operating"},
        {"id": "org_acme", "canonical_name": "Acme Co",
         "relationship_type": "prospect"},
        {"id": "org_client", "canonical_name": "Northstar Partners",
         "relationship_type": "client"},
    ]
    ent = {"version": 1, "people": [], "orgs": orgs, "threads": [],
           "engagements": []}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _entities(ws):
    obj = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    return obj.get("entities") if isinstance(obj.get("entities"), dict) else obj


def _threads(ws):
    ent = _entities(ws)
    return ent.get("threads") or ent.get("projects") or []


def _events(ws, etype=None):
    txt = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    evs = [json.loads(ln) for ln in txt.splitlines() if ln.strip()]
    if etype:
        evs = [e for e in evs if e.get("type") == etype]
    return evs


# --- create_deal ------------------------------------------------------------
ws = _ws()
thread = ds.create_deal(ws, name="Acme pilot", org_id="org_acme", value=40000)
check(thread["kind"] == "deal", "create_deal thread has kind=deal")
check(thread["deal"]["stage"] == "lead", "create_deal defaults stage=lead")
check(thread["deal"]["value"] == 40000, "create_deal stores stated value")
check(thread["deal"].get("stage_entered") and thread["deal"].get("opened_at"),
      "create_deal stamps stage_entered + opened_at")
created = _events(ws, "deal_created")
check(len(created) == 1, "exactly one deal_created emitted")
check(created[0]["data"]["thread_id"] == thread["id"]
      and created[0]["data"]["org_id"] == "org_acme"
      and created[0]["data"]["stage"] == "lead"
      and created[0]["data"]["value"] == 40000,
      "deal_created payload carries thread_id/org_id/stage/value")
check(created[0].get("seq") is not None and created[0].get("ts"),
      "deal_created went through the gate (seq/ts auto-stamped)")

# org untouched by create (acceptance §7 item 2)
org_acme = next(o for o in _entities(ws)["orgs"] if o["id"] == "org_acme")
check(org_acme.get("relationship_type") == "prospect" and "stage" not in org_acme,
      "create_deal leaves the org untouched (no stage field, still prospect)")

# missing org rejected
try:
    ds.create_deal(ws, name="Ghost deal", org_id="org_nope")
    check(False, "create_deal on a missing org should raise")
except ds.DealStateError:
    check(True, "create_deal rejects a non-existent org")

# invalid stage rejected
try:
    ds.create_deal(ws, name="Bad stage deal", org_id="org_acme", stage="won")
    check(False, "invalid stage should raise")
except ds.DealStateError:
    check(True, "create_deal rejects a non-enum stage ('won' is an outcome)")

# --- thread_writer floor: deal object on a non-deal thread rejected ---------
try:
    tw.create_thread(ws, canonical_name="Not a deal", kind="initiative",
                     deal={"stage": "lead"}, source_skill="test")
    check(False, "deal object on kind=initiative should raise")
except ValueError:
    check(True, "thread_writer rejects a deal object on a non-deal thread")

try:
    tw.create_thread(ws, canonical_name="Bad enum deal", kind="deal",
                     deal={"stage": 3}, source_skill="test")
    check(False, "deal.stage=3 should raise")
except ValueError:
    check(True, "thread_writer rejects a non-enum deal.stage (semantics drift)")

try:
    tw.create_thread(ws, canonical_name="Reasonless loss", kind="deal",
                     deal={"stage": "lead", "outcome": "lost"},
                     source_skill="test")
    check(False, "outcome=lost without loss_reason should raise")
except ValueError:
    check(True, "thread_writer rejects outcome=lost without a loss_reason")

# --- update_deal ------------------------------------------------------------
r = ds.update_deal(ws, thread["id"], expected_close="2099-01-15")
check(r["status"] == "updated" and r["changed"] == {"expected_close": "2099-01-15"},
      "update_deal applies a field change")
check(len(_events(ws, "deal_updated")) == 1
      and _events(ws, "deal_updated")[0]["data"]["expected_close"] == "2099-01-15",
      "deal_updated emitted with the changed field")
try:
    ds.update_deal(ws, thread["id"], stage="qualified")
    check(False, "update_deal must refuse stage")
except ds.DealStateError:
    check(True, "update_deal refuses stage (set_stage owns moves)")

# --- set_stage ---------------------------------------------------------------
r = ds.set_stage(ws, thread["id"], "proposal_sent")
check(r["status"] == "moved" and r["from_stage"] == "lead"
      and r["to_stage"] == "proposal_sent", "set_stage moves lead -> proposal_sent")
t = next(x for x in _threads(ws) if x["id"] == thread["id"])
check(t["deal"]["stage"] == "proposal_sent" and t["deal"]["stage_entered"],
      "set_stage stamps stage_entered")
sc = _events(ws, "deal_stage_changed")
check(len(sc) == 1 and sc[0]["data"]["from_stage"] == "lead"
      and sc[0]["data"]["to_stage"] == "proposal_sent",
      "deal_stage_changed carries from/to")
# backward move allowed
r = ds.set_stage(ws, thread["id"], "lead", note="they went quiet, re-qualifying")
check(r["status"] == "moved" and r["from_stage"] == "proposal_sent"
      and r["to_stage"] == "lead", "backward stage move allowed (deals regress)")
r = ds.set_stage(ws, thread["id"], "lead")
check(r["status"] == "unchanged", "same-stage move is a no-op, no event")
check(len(_events(ws, "deal_stage_changed")) == 2, "no-op wrote no event")
try:
    ds.set_stage(ws, thread["id"], "closed_won")
    check(False, "invalid stage should raise")
except ds.DealStateError:
    check(True, "set_stage rejects non-enum stages")

# --- close_deal: lost requires a reason --------------------------------------
try:
    ds.close_deal(ws, thread["id"], "lost")
    check(False, "lost without loss_reason should raise")
except ds.DealStateError:
    check(True, "close_deal rejects lost without a valid loss_reason")
try:
    ds.close_deal(ws, thread["id"], "lost", loss_reason="too_expensive")
    check(False, "invalid loss_reason should raise")
except ds.DealStateError:
    check(True, "close_deal rejects a non-enum loss_reason")

# --- close_deal lost ----------------------------------------------------------
r = ds.close_deal(ws, thread["id"], "lost", loss_reason="price",
                  loss_note="budget cut")
check(r["status"] == "closed" and r["outcome"] == "lost", "close_deal lost closes")
t = next(x for x in _threads(ws) if x["id"] == thread["id"])
check(t["status"] == "archived", "lost deal thread archived")
check(t["deal"]["outcome"] == "lost" and t["deal"]["loss_reason"] == "price"
      and t["deal"]["closed_at"], "terminal fields stamped on the deal object")
lost = _events(ws, "deal_lost")
check(len(lost) == 1 and lost[0]["data"]["loss_reason"] == "price"
      and lost[0]["data"]["loss_note"] == "budget cut"
      and lost[0]["data"]["org_id"] == "org_acme",
      "deal_lost payload carries loss_reason/loss_note/org_id")
# idempotent — re-running is a no-op (acceptance §7 item 4)
r2 = ds.close_deal(ws, thread["id"], "lost", loss_reason="price")
check(r2["status"] == "already_closed", "second close returns already_closed")
check(len(_events(ws, "deal_lost")) == 1, "no duplicate deal_lost on re-close")
try:
    ds.update_deal(ws, thread["id"], value=1)
    check(False, "update on a closed deal should raise")
except ds.DealStateError:
    check(True, "update_deal refuses a closed deal (terminal is terminal)")

# --- close_deal won WITHOUT convert: suggestion only (acceptance §7 item 5) ---
won_thread = ds.create_deal(ws, name="Acme expansion", org_id="org_acme",
                            value=15000)
r = ds.close_deal(ws, won_thread["id"], "won")
check(r["status"] == "closed" and r["outcome"] == "won", "close_deal won closes")
t = next(x for x in _threads(ws) if x["id"] == won_thread["id"])
check(t["status"] == "resolved", "won deal thread resolved")
won = _events(ws, "deal_won")
check(len(won) == 1 and won[0]["data"]["org_id"] == "org_acme"
      and won[0]["data"]["value"] == 15000, "deal_won payload carries org_id/value")
check(r["converted"] is False
      and r["conversion_suggestion"] == "Acme Co is now a client",
      "plain won on a prospect org suggests conversion, does not run it")
org_acme = next(o for o in _entities(ws)["orgs"] if o["id"] == "org_acme")
check(org_acme["relationship_type"] == "prospect",
      "relationship_type UNCHANGED by plain mark-won")
r2 = ds.close_deal(ws, won_thread["id"], "won")
check(r2["status"] == "already_closed" and len(_events(ws, "deal_won")) == 1,
      "won close idempotent — no duplicate deal_won")

# --- close_deal won WITH convert_prospect: D6 one-utterance atomic result ----
d6_thread = ds.create_deal(ws, name="Acme phase two", org_id="org_acme",
                           value=60000)
r = ds.close_deal(ws, d6_thread["id"], "won", convert_prospect=True)
check(r["converted"] is True, "convert_prospect ran the conversion")
org_acme = next(o for o in _entities(ws)["orgs"] if o["id"] == "org_acme")
check(org_acme["relationship_type"] == "client" and "stage" not in org_acme,
      "org flipped prospect -> client through the typed writer, no stage field")
engs = _entities(ws).get("engagements") or []
check(any(e.get("from_org_id") == "org_me" and e.get("to_org_id") == "org_acme"
          and e.get("kind") == "client" and e.get("is_active", True)
          for e in engs),
      "engagement edge from the primary-focus org exists after conversion")
d6_won = [e for e in _events(ws, "deal_won")
          if e["data"]["thread_id"] == d6_thread["id"]]
check(len(d6_won) == 1 and d6_won[0]["data"].get("converted_prospect") is True,
      "deal_won carries converted_prospect on the D6 path")

# convert on a NON-prospect org is a plain close (no error, no flip needed)
c_thread = ds.create_deal(ws, name="Northstar renewal", org_id="org_client",
                          value=20000)
r = ds.close_deal(ws, c_thread["id"], "won", convert_prospect=True)
check(r["status"] == "closed" and r["converted"] is False
      and r["conversion_suggestion"] is None,
      "convert flag on a client-org deal closes plainly (nothing to convert)")

# no primary-focus org -> conversion refused BEFORE any write
ws2 = _ws()
ent_path = ws2 / "_hq" / "data" / "entities.json"
ent2 = json.loads(ent_path.read_text(encoding="utf-8"))
for o in ent2["orgs"]:
    o.pop("is_primary_focus", None)
ent_path.write_text(json.dumps(ent2), encoding="utf-8")
nf_thread = ds.create_deal(ws2, name="Acme pilot", org_id="org_acme", value=1000)
try:
    ds.close_deal(ws2, nf_thread["id"], "won", convert_prospect=True)
    check(False, "conversion without a primary-focus org should raise")
except ds.DealStateError:
    check(True, "conversion refused loudly when no primary-focus org is set")
t = next(x for x in _threads(ws2) if x["id"] == nf_thread["id"])
check(t["deal"].get("outcome") is None and t["status"] == "active",
      "refused conversion wrote NOTHING (deal still open, atomic-or-nothing)")

# --- real-data fixture: pre-PIPE1 kind=deal thread with NO deal object -------
legacy = tw.create_thread(ws, canonical_name="Beacon Logistics deal",
                          kind="deal", affiliation_id="org_client",
                          source_skill="test")
opens = ds.list_open_deals(ws)
legacy_row = next((r for r in opens if r["thread_id"] == legacy["id"]), None)
check(legacy_row is not None and legacy_row["untracked"] is True
      and legacy_row["deal"] is None,
      "pre-PIPE1 deal thread (no deal object) surfaces as untracked, no crash")
try:
    ds.set_stage(ws, legacy["id"], "qualified")
    check(False, "set_stage on an untracked deal should raise (adopt first)")
except ds.DealStateError:
    check(True, "set_stage refuses an untracked deal thread with the adopt hint")

adopted = ds.adopt_deal(ws, legacy["id"], stage="qualified", value=30000)
check(adopted["deal"]["stage"] == "qualified"
      and adopted["deal"]["value"] == 30000,
      "adopt_deal attaches the deal object")
adopt_evs = [e for e in _events(ws, "deal_created")
             if e["data"].get("adopted") is True]
check(len(adopt_evs) == 1 and adopt_evs[0]["data"]["thread_id"] == legacy["id"],
      "adoption emits deal_created with adopted=true")
try:
    ds.adopt_deal(ws, legacy["id"])
    check(False, "double adoption should raise")
except ds.DealStateError:
    check(True, "adopt_deal refuses a thread that already carries a deal")

# --- list_open_deals excludes terminals --------------------------------------
open_ids = {r["thread_id"] for r in ds.list_open_deals(ws)}
check(thread["id"] not in open_ids and won_thread["id"] not in open_ids,
      "closed deals excluded from the open set")
check(legacy["id"] in open_ids, "adopted open deal still in the open set")
closed_ids = {r["thread_id"] for r in ds.list_closed_deals(ws)}
check(thread["id"] in closed_ids and won_thread["id"] in closed_ids,
      "list_closed_deals returns the terminals")

# --- defensive events read: malformed line tolerated, skipped surfaced -------
with open(ws / "_hq" / "data" / "events.jsonl", "a", encoding="utf-8") as f:
    f.write("{this is not json}\n")
events, skipped = ds.load_deal_events(ws)
check(len(skipped) >= 1, "malformed events.jsonl line lands in skipped (banner rule)")
check(any(e["type"] == "deal_lost" for e in events)
      and all(e["type"].startswith("deal_") for e in events),
      "load_deal_events returns only deal_* events despite the bad line")

# --- A1 lock discipline: no hand-rolled appends in the module -----------------
src = (Path(__file__).resolve().parent.parent / "shared" / "scripts"
       / "deal_state.py").read_text(encoding="utf-8")
check(not re.search(r"open\([^)]*['\"]a['\"]", src),
      "deal_state has no direct append-mode open() — all writes via the "
      "typed writers + event gate")
check("append_event" in src and "thread_writer" in src,
      "deal_state routes through event_gate.append_event + thread_writer")

print(f"OK — all {PASS} deal_state tests passed")
sys.exit(0)
