#!/usr/bin/env python3
"""Tests for SPEC HIST1 Part A — the grouped org `money` object:
org_writer.set_org_money (confirm-only, sourced, merge semantics),
record_org_fact, and the B1 quantify proof (the one-line _money_part
candidate edit that makes nested org.money render — asserted in BOTH
directions, guarding against a revert of the edit). House conventions:
check(), synthetic workspace, relative dates, placeholder names."""
import datetime
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import org_writer as ow  # noqa: E402
import quantify  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


TODAY = datetime.date.today()
D20 = (TODAY - datetime.timedelta(days=20)).isoformat()


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {
        "people": [],
        "orgs": [{"id": "org_001", "canonical_name": "Acme Co",
                  "relationship_type": "client", "first_seen": D20}],
        "threads": [{"id": "project_001", "display_name": "Acme Ops",
                     "affiliation_id": "org_001"}],
        "version": 1,
    }
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _events(ws):
    txt = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    return [json.loads(ln) for ln in txt.splitlines() if ln.strip()]


def _org(ws, oid="org_001"):
    doc = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    ent = doc.get("entities") if isinstance(doc.get("entities"), dict) else doc
    return next(o for o in ent["orgs"] if o["id"] == oid)


# --- confirm gate ---------------------------------------------------------
ws = _ws()
try:
    ow.set_org_money(ws, "org_001", {"account_value": 120000, "source": "user statement"})
    check(False, "set_org_money without confirmed=True must raise")
except ValueError:
    check(True, "set_org_money refuses without confirmed=True (money is confirm-only, D4)")
check(_events(ws) == [], "the refused write left no event behind")

# --- happy path: grouped object + org_updated with the delta in before ----
rec = ow.set_org_money(ws, "org_001",
                       {"account_value": 120000, "source": "user statement"},
                       source_skill="workspace-manager", confirmed=True)
money = rec.get("money")
check(isinstance(money, dict) and money["account_value"] == 120000,
      "grouped money object written")
check(money.get("source") == "user statement" and money.get("as_of"),
      "money is sourced with an as_of date")
evs = _events(ws)
upd = next(e for e in evs if e["type"] == "org_updated")
check("money" not in upd["data"]["before"],
      "first set: before-snapshot shows no money (the delta is visible)")

# second set MERGES and the before-snapshot carries the old money
ow.set_org_money(ws, "org_001", {"arr": 96000, "source": "user statement"},
                 source_skill="workspace-manager", confirmed=True)
m2 = _org(ws)["money"]
check(m2["account_value"] == 120000 and m2["arr"] == 96000,
      "partial update merges into the existing money object")
upd2 = [e for e in _events(ws) if e["type"] == "org_updated"][-1]
check(upd2["data"]["before"].get("money", {}).get("account_value") == 120000,
      "second set: before-snapshot carries the prior money (change-feed delta)")

# --- guards ---------------------------------------------------------------
ws_fresh = _ws()
try:
    ow.set_org_money(ws_fresh, "org_001", {"account_value": 5}, source_skill="t", confirmed=True)
    check(False, "missing source should raise")
except ValueError:
    check(True, "first money write without a source raises (always sourced)")
# ...but a partial UPDATE inherits the existing source (merge semantics)
m_partial = ow.set_org_money(ws, "org_001", {"mrr": 10000},
                             source_skill="t", confirmed=True)["money"]
check(m_partial["source"] == "user statement" and m_partial["mrr"] == 10000,
      "a sourced money object keeps its source across partial updates")
try:
    ow.set_org_money(ws, "org_001", {"valuation": 1, "source": "x"}, source_skill="t", confirmed=True)
    check(False, "unknown money key should raise")
except ValueError:
    check(True, "unknown money key raises (inner keys mirror quantify._MONEY_FIELDS)")
try:
    ow.set_org_money(ws, "org_001", {"account_value": "a lot", "source": "x"},
                     source_skill="t", confirmed=True)
    check(False, "non-numeric money value should fail validation")
except ValueError:
    check(True, "_validate_org rejects a non-numeric money figure (never a prose estimate)")
# sole-writer guard (review fix): the direct update_org path is refused —
# set_org_money is the ONLY way org.money lands (D4 / Bug #92)
try:
    ow.update_org(ws, "org_001", money={"account_value": 1}, source_skill="t")
    check(False, "direct update_org(money=...) must be refused")
except ValueError as e:
    check("set_org_money" in str(e),
          "update_org refuses a direct money write and redirects to set_org_money")
check(_org(ws)["money"]["account_value"] == 120000,
      "the refused direct write left the sanctioned money object untouched")

# --- B1 quantify proof: nested org.money renders through the trace --------
ws_q = _ws()
ow.set_org_money(ws_q, "org_001", {"account_value": 120000, "source": "user statement"},
                 source_skill="workspace-manager", confirmed=True)
entities = json.loads((ws_q / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
commitment = {"type": "commitment", "primary_thread_id": "project_001"}
tag = quantify.money_time_tag(commitment, entities)
check(tag == "$120K",
      f"commitment on an affiliated NON-deal thread renders $120K from org.money "
      f"with no deal and no flat org money field (got {tag!r})")

# WITHOUT the edit: hide the nested object from the candidate append and the
# flat-key trace must come up empty — this is the regression the reviewer
# caught (a revert of the one-line edit turns every org-money tag None).
class _FlatOnlyOrg(dict):
    def get(self, key, default=None):
        if key == "money":
            return None  # what pre-B1 _money_part effectively saw
        return dict.get(self, key, default)


orig_resolve = quantify._resolve_org
try:
    quantify._resolve_org = lambda item, ents: _FlatOnlyOrg(orig_resolve(item, ents) or {})
    check(quantify.money_time_tag(commitment, entities) is None,
          "without the B1 candidate edit the nested money is invisible — tag is None")
finally:
    quantify._resolve_org = orig_resolve

# a deal thread's stated value still beats org money (PIPE1 coordination)
entities["threads"].append({"id": "project_002", "affiliation_id": "org_001",
                            "kind": "deal", "deal": {"value": 52000, "stage": "negotiating"}})
deal_c = {"type": "commitment", "primary_thread_id": "project_002"}
check(quantify.money_time_tag(deal_c, entities) == "$52K deal",
      "a stated per-deal figure beats the org-level account value on deal-thread items")

# absent money -> None (no fabrication)
ws2 = _ws()
ents2 = json.loads((ws2 / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
check(quantify.money_time_tag({"type": "commitment", "primary_thread_id": "project_001"}, ents2) is None,
      "absent money renders no tag — never an estimate")

# --- record_org_fact ------------------------------------------------------
before_rec = dict(_org(ws))
ev = ow.record_org_fact(ws, "org_001", "Raised a Series A", "chat:user-statement",
                        category="company_news", source_skill="workspace-manager")
check(ev["type"] == "org_fact_observed" and ev["org_ids"] == ["org_001"],
      "record_org_fact appends org_fact_observed with top-level org_ids")
check(_org(ws) == before_rec, "record_org_fact does NOT mutate the org record")
try:
    ow.record_org_fact(ws, "org_001", "x", "  ")
    check(False, "blank source_ref should raise")
except ValueError:
    check(True, "org fact without source_ref raises")
try:
    ow.record_org_fact(ws, "org_999", "x", "chat:user-statement")
    check(False, "unknown org should raise")
except KeyError:
    check(True, "unknown org_id raises KeyError")

print(f"OK — all {PASS} org_writer money/fact tests passed")
sys.exit(0)
