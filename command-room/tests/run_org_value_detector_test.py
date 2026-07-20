#!/usr/bin/env python3
"""Tests for org_value_detector (SPEC HIST1 Part 2, step 12) — observed
account/contract value near a CLIENT org is PROPOSED (tier=confirm, money
is never estimated/auto per D4/Bug #92), fenced off deal_signal_detector's
bare-money lane, deduped against money already on file, and applied only
through set_org_money(confirmed=True) on the user's click.

Fixtures mirror real substrate shapes; dates relative to today (G14);
placeholder names only (Rule 26)."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import brain_proposals as bp  # noqa: E402
import org_value_detector as ovd  # noqa: E402
from org_writer import set_org_money  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
T_RECENT = NOW - timedelta(days=5)
T_STALE = NOW - timedelta(days=ovd.TEXT_WINDOW_DAYS + 15)


def _ws(orgs=None):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [], "orgs": orgs if orgs is not None else [
        {"id": "org_001", "canonical_name": "Acme Co", "status": "active",
         "relationship_type": "client", "first_seen": "2026-01-05"},
        {"id": "org_002", "canonical_name": "Globex Co", "status": "active",
         "relationship_type": "prospect", "first_seen": "2026-02-01"},
    ], "threads": [], "engagements": []}
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


# --- 1. Shape registration (step 12 rides the money carve-out) --------------
check(bp.kind_shape("org_money") == "money",
      "org_money ranks money (D3 ordering)")
check("org_money" in bp._MONEY_PROSE,
      "org_money has FB-20 carve-out prose (unmapped money kinds render "
      "no sentence)")

# --- 2. Detection: account language + amount near a CLIENT org --------------
ws = _ws()
_raw_append(ws, [
    {"ts": _iso(T_RECENT), "type": "interaction", "source_skill": "cr-inbox",
     "org_ids": ["org_001"],
     "data": {"summary": "Acme Co is a $120k/yr account for us now"}},
    # Prospect org — out of scope (deal detector's territory).
    {"ts": _iso(T_RECENT), "type": "interaction", "source_skill": "cr-inbox",
     "org_ids": ["org_002"],
     "data": {"summary": "Globex Co retainer would be $50k a year"}},
    # Bare money, NO account language — the deal lane, never this one.
    {"ts": _iso(T_RECENT), "type": "note", "source_skill": "intel-intake",
     "org_ids": ["org_001"],
     "data": {"summary": "Acme Co budget approval came in at $75,000"}},
    # Stale — outside the window.
    {"ts": _iso(T_STALE), "type": "note", "source_skill": "intel-intake",
     "org_ids": ["org_001"],
     "data": {"summary": "Acme Co was an $80k annual account back then"}},
])
cands = ovd.detect_org_value_signals(ws)
check(len(cands) == 1, f"exactly one candidate (client org, in-window, "
      f"account-shaped): {len(cands)}")
c0 = cands[0]
check(c0["org_id"] == "org_001", "the client org qualified")
check(c0["proposed_money"].get("account_value") == 120000.0,
      f"parsed $120k -> 120000: {c0['proposed_money']}")
check(c0["proposed_money"].get("source", "").startswith("observed"),
      "money.source records provenance (never estimated)")
check(c0["proposed_money"].get("as_of"),
      "as_of stamped (sourced money discipline)")
check("confirm" in c0["render_line"], "render_line asks for the confirm")

# Monthly-flavored language proposes mrr — observed, never annualized.
ws2 = _ws()
_raw_append(ws2, [
    {"ts": _iso(T_RECENT), "type": "interaction", "source_skill": "cr-inbox",
     "org_ids": ["org_001"],
     "data": {"summary": "The Acme Co retainer is $8k a month going forward"}},
])
cands2 = ovd.detect_org_value_signals(ws2)
check(len(cands2) == 1 and cands2[0]["proposed_money"].get("mrr") == 8000.0,
      f"monthly language -> mrr, not an annualized account_value: {cands2}")

# Money already on file at the same figure → nothing to propose.
ws3 = _ws(orgs=[
    {"id": "org_001", "canonical_name": "Acme Co", "status": "active",
     "relationship_type": "client", "first_seen": "2026-01-05",
     "money": {"account_value": 120000, "source": "user statement",
               "as_of": "2026-06-01", "currency": "USD"}},
])
_raw_append(ws3, [
    {"ts": _iso(T_RECENT), "type": "interaction", "source_skill": "cr-inbox",
     "org_ids": ["org_001"],
     "data": {"summary": "Acme Co is a $120k/yr account"}},
])
check(ovd.detect_org_value_signals(ws3) == [],
      "a figure already on file re-observed proposes nothing")

# Thread-referenced path: the event names NO org directly — only a thread
# whose CANONICAL singular `affiliation_id` links it to the client org
# (real-data fixture gotcha; review fix — the first cut read only the
# plural affiliation_ids and silently missed canonical-shaped threads).
ws_t = _ws()
ent_t = json.loads((ws_t / "_hq" / "data" / "entities.json")
                   .read_text(encoding="utf-8"))
ent_t["threads"] = [{"id": "project_001", "canonical_name": "Acme Retainer",
                     "affiliation_id": "org_001", "status": "active"}]
(ws_t / "_hq" / "data" / "entities.json").write_text(
    json.dumps(ent_t), encoding="utf-8")
_raw_append(ws_t, [
    {"ts": _iso(T_RECENT), "type": "interaction", "source_skill": "cr-inbox",
     "primary_thread_id": "project_001",
     "data": {"summary": "The retainer runs $45k per year on this one"}},
])
cands_t = ovd.detect_org_value_signals(ws_t)
check(len(cands_t) == 1 and cands_t[0]["org_id"] == "org_001"
      and cands_t[0]["proposed_money"].get("account_value") == 45000.0,
      f"thread-only reference resolves via canonical affiliation_id: {cands_t}")

# Legacy org missing money/relationship fields tolerated (real-data gotcha).
ws4 = _ws(orgs=[
    {"id": "org_009", "canonical_name": "Initech Co", "status": "active",
     "relationship_type": "client"},  # no first_seen, no money
])
_raw_append(ws4, [
    {"ts": _iso(T_RECENT), "type": "note", "source_skill": "cr-inbox",
     "org_ids": ["org_009"],
     "data": {"summary": "Initech Co annual retainer lands at $30k"}},
])
check(len(ovd.detect_org_value_signals(ws4)) == 1,
      "legacy org shape (no money/first_seen) detects without crashing")

# --- 3. Scan proposes confirm-tier; nothing ever writes money ---------------
res = ovd.run_org_value_scan(ws)
check(res["n_proposed"] == 1, f"scan proposes the candidate: {res}")
props = _events(ws, "brain_proposal")
check(len(props) == 1 and props[0]["data"]["tier"] == "confirm",
      "org_money rides tier=confirm — money is NEVER auto (D4/Bug #92)")
check(props[0]["data"]["kind"] == "org_money"
      and props[0]["data"]["proposed_money"]["account_value"] == 120000.0,
      "proposal embeds the proposed money verbatim for apply-choices")
ent_after = json.loads((ws / "_hq" / "data" / "entities.json")
                       .read_text(encoding="utf-8"))
check(all("money" not in o for o in ent_after["orgs"]),
      "detection/proposal NEVER touches the org record")

# Re-run → open-fingerprint dedup.
res2 = ovd.run_org_value_scan(ws)
check(res2["n_proposed"] == 0 and res2["n_suppressed"] == 1,
      "re-run suppresses via open-proposal dedup")

# Confirm card ranks it money-first and the brief carve-out names it.
card = bp.select_confirm_card(ws, "staff-meeting")
check(card["items"] and card["items"][0]["kind"] == "org_money"
      and card["items"][0]["shape"] == "money",
      "org_money row reaches the card money-ranked")
prose = bp.money_prose_lines(card["items"])
check(prose and "account value" in prose[0] and "Acme Co" in prose[0],
      f"FB-20 money carve-out sentence renders: {prose}")

# --- 4. The apply path (what apply-choices dispatches on confirm) -----------
money = dict(props[0]["data"]["proposed_money"])
rec = set_org_money(ws, "org_001",
                    {k: v for k, v in money.items()
                     if k in ("account_value", "mrr", "source", "as_of")},
                    source_skill="apply-choices", confirmed=True)
check(rec["money"]["account_value"] == 120000.0,
      "confirmed proposal applies through the ONE money writer")

# --- 5. QBO/skill-runtime lane: propose_org_value proposes, never applies ---
ws5 = _ws()
r = ovd.propose_org_value(
    ws5, "org_001", {"account_value": 90000,
                     "source": "qbo:sales-by-customer",
                     "as_of": NOW.strftime("%Y-%m-%d")},
    evidence="QBO sales-by-customer", source_ref="qbo:sales-by-customer",
    org_name="Acme Co")
check(r["status"] == "proposed", "QBO figure proposes")
qprops = _events(ws5, "brain_proposal")
check(qprops[0]["data"]["tier"] == "confirm"
      and qprops[0]["data"]["proposed_money"]["source"]
      == "qbo:sales-by-customer",
      "QBO lane is confirm-tier with QBO provenance")
ent5 = json.loads((ws5 / "_hq" / "data" / "entities.json")
                  .read_text(encoding="utf-8"))
check(all("money" not in o for o in ent5["orgs"]),
      "the QBO lane never applies the figure itself")

# --- 6. Per-run cap: overflow counted, never silent -------------------------
ws6 = _ws(orgs=[
    {"id": f"org_{i:03d}", "canonical_name": f"Sample Org {i}",
     "status": "active", "relationship_type": "client"}
    for i in range(1, ovd.MAX_PROPOSALS_PER_RUN + 3)])
_raw_append(ws6, [
    {"ts": _iso(T_RECENT), "type": "note", "source_skill": "cr-inbox",
     "org_ids": [f"org_{i:03d}"],
     "data": {"summary": f"Sample Org {i} annual account worth ${i}0k"}}
    for i in range(1, ovd.MAX_PROPOSALS_PER_RUN + 3)])
res6 = ovd.run_org_value_scan(ws6)
check(res6["n_proposed"] == ovd.MAX_PROPOSALS_PER_RUN
      and res6["n_capped"] == 2,
      f"per-run cap enforced, overflow counted: {res6}")

print(f"OK — {PASS} checks passed")
