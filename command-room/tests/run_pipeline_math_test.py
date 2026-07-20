#!/usr/bin/env python3
"""Tests for pipeline_math + deal_health (SPEC PIPE1) — hand-computed
vectors, exact expected values, no tolerance. ALL fixture dates are computed
RELATIVE TO TODAY (the hardcoded-future-date gotcha: a literal date fed to a
real-clock path goes red the day the calendar passes it). The fixture
includes a pre-PIPE1 untracked deal-thread row (real-data fixture gotcha) —
the math must pass it through, never crash on it."""
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import deal_health as dh  # noqa: E402
import pipeline_math as pm  # noqa: E402
import quantify  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


TODAY = datetime.date.today()


def d(days_ago):
    return (TODAY - datetime.timedelta(days=days_ago)).isoformat()


def row(tid, name, *, value=None, stage="lead", opened_ago=10,
        stage_entered_ago=None, expected_close=None, forecast=None,
        untracked=False):
    if untracked:
        return {"thread_id": tid, "name": name, "org_id": "org_x",
                "status": "active", "deal": None, "untracked": True}
    deal = {"stage": stage, "opened_at": d(opened_ago),
            "stage_entered": d(stage_entered_ago
                               if stage_entered_ago is not None else opened_ago)}
    if value is not None:
        deal["value"] = value
        deal["currency"] = "USD"
    if expected_close is not None:
        deal["expected_close"] = expected_close
    if forecast is not None:
        deal["forecast_category"] = forecast
    return {"thread_id": tid, "name": name, "org_id": "org_x",
            "status": "active", "deal": deal, "untracked": False}


# --- The 6-deal fixture (spec §6 vectors) -------------------------------------
# t1 Acme pilot      40k negotiating  active yesterday, has next step, commit
# t2 Beta rollout    12k proposal_sent, quiet 9d (>7 threshold) -> rotting
# t3 Gamma retainer  25k lead, active 2d ago, NO open commitment -> no_next_step
# t4 Delta expansion 28k qualified, closes today (this month), best_case
# t5 Epsilon deal    24k qualified, closes today (this month)
# t6 Zeta legacy      8k lead, opened 100d ago -> zombie (90d default),
#                     expected_close 40d ago -> close_date_passed
deals = [
    row("t1", "Acme pilot", value=40000, stage="negotiating", opened_ago=10,
        stage_entered_ago=3, forecast="commit"),
    row("t2", "Beta rollout", value=12000, stage="proposal_sent",
        opened_ago=20, stage_entered_ago=9),
    row("t3", "Gamma retainer", value=25000, stage="lead", opened_ago=5),
    row("t4", "Delta expansion", value=28000, stage="qualified", opened_ago=8,
        expected_close=TODAY.isoformat(), forecast="best_case"),
    row("t5", "Epsilon deal", value=24000, stage="qualified", opened_ago=8,
        expected_close=TODAY.isoformat()),
    row("t6", "Zeta legacy", value=8000, stage="lead", opened_ago=100,
        expected_close=d(40)),
]
untracked = row("t7", "Beacon Logistics deal", untracked=True)
all_rows = deals + [untracked]


def act(days_ago):
    return SimpleNamespace(
        ts=datetime.datetime.combine(
            TODAY - datetime.timedelta(days=days_ago), datetime.time(9, 0)))


activity = {"t1": act(1), "t2": act(9), "t3": act(2), "t4": act(1),
            "t5": act(1), "t6": act(2)}
next_steps = {"t1", "t2", "t4", "t5", "t6"}  # t3 has NO open commitment

health = dh.compute_deal_health(
    all_rows, activity_by_thread=activity,
    open_commitment_thread_ids=next_steps, today=TODAY,
    zombie_days=dh.zombie_threshold_days([]))
by_id = {h["thread_id"]: h for h in health}

# --- flags, exactly ------------------------------------------------------------
check(by_id["t1"]["flags"] == [], "t1 healthy — no flags")
check(by_id["t2"]["flags"] == ["rotting"],
      "t2 rotting (9d quiet > 7d proposal_sent threshold)")
check(by_id["t3"]["flags"] == ["no_next_step"],
      "t3 flagged no_next_step (zero open commitments on the thread)")
check(by_id["t4"]["flags"] == [] and by_id["t5"]["flags"] == [],
      "t4/t5 healthy (expected_close today is not passed)")
check(by_id["t6"]["flags"] == ["close_date_passed", "zombie"],
      "t6 close_date_passed + zombie (100d age > 90d default, <5 wins)")
check(by_id["t7"]["untracked"] is True and by_id["t7"]["flags"] == [],
      "untracked pre-PIPE1 row passes through with no rot math, no crash")
check(by_id["t2"]["days_quiet"] == 9 and by_id["t2"]["days_in_stage"] == 9,
      "t2 day math exact (quiet 9, in-stage 9)")
check(by_id["t6"]["age_days"] == 100, "t6 age exact (100d)")
check(by_id["t6"]["severity"] == 7 and by_id["t2"]["severity"] == 3
      and by_id["t3"]["severity"] == 2,
      "severity points exact (zombie 4 + close_date_passed 3 = 7; rotting 3; "
      "no_next_step 2)")

# --- tiles (spec vectors) -------------------------------------------------------
check(pm.open_pipeline_value(all_rows) == 137000.0, "open pipeline value 137000")
n, total = pm.closing_this_month(all_rows, TODAY)
check(n == 2 and total == 52000.0, "closing this month: 2 deals / 52000")
check(pm.stalled_count(health) == 2,
      "stalled count 2 (one rotting, one no_next_step; zombie is its own bucket)")

terminal = [
    {"type": "deal_won", "ts": d(10)},
    {"type": "deal_won", "ts": d(30)},
    {"type": "deal_lost", "ts": d(15)},
    {"type": "deal_lost", "ts": d(80)},
    {"type": "deal_won", "ts": d(200)},   # outside the 90d window — ignored
    {"type": "deal_created", "ts": d(5)},  # non-terminal — ignored
]
check(pm.won_rate_90d(terminal, TODAY) == 0.5,
      "won-rate 90d = 0.5 from 2 won + 2 lost inside the window")
check(pm.won_rate_90d(terminal[:3], TODAY) is None,
      "won-rate drops (None) below 4 terminal events — no misleading 100%")

check(pm.haircut_value(all_rows) == 0.9 * 40000 + 0.6 * 28000,
      "haircut = 36000 + 16800 = 52800 (only categorized deals)")
no_cat = [row("x1", "X", value=10000, stage="lead")]
check(pm.haircut_value(no_cat) is None,
      "haircut is None when no open deal carries a forecast_category")

# --- value_by_org (SPEC OUT3B / S3 vectors) -----------------------------------
def orow(tid, name, org_id, value):
    deal = {"stage": "lead"}
    if value is not None:
        deal["value"] = value
        deal["currency"] = "USD"
    return {"thread_id": tid, "name": name, "org_id": org_id,
            "status": "active", "deal": deal, "untracked": False}

vbo_rows = [
    orow("d1", "Acme pilot", "org_acme", 40000),
    orow("d2", "Acme expansion", "org_acme", 60000),   # same org -> summed
    orow("d3", "Northstar retainer", "org_north", 90000),
    orow("d4", "Priceless lead", "org_acme", None),    # no value -> contributes nothing
    {"thread_id": "d5", "name": "Untracked thread", "org_id": "org_ghost",
     "status": "active", "deal": None, "untracked": True},  # no deal -> nothing
]
org_names = {"org_acme": "Acme Co", "org_north": "Northstar Partners"}
vbo = pm.value_by_org(vbo_rows, org_names=org_names)
check(vbo == [{"label": "Acme Co", "value": 100000.0},
              {"label": "Northstar Partners", "value": 90000.0}],
      "value_by_org: per-org sum, value-desc; Acme 40k+60k=100k > Northstar 90k; "
      "None-value + deal-less rows contribute nothing")
# Reader-facing labels: an org_id absent from the map falls back to the deal name.
vbo_unmapped = pm.value_by_org([orow("d6", "Solo deal", "org_unknown", 15000)],
                               org_names=org_names)
check(vbo_unmapped == [{"label": "Solo deal", "value": 15000.0}],
      "value_by_org: unmapped org_id labels from the deal's own reader-facing name")
check(pm.value_by_org([]) == [] and pm.value_by_org(vbo_rows[3:]) == [],
      "value_by_org: [] when no open deal carries a value (never a $0 bar)")

tiles = pm.pipeline_tiles(all_rows, health, terminal, TODAY)
check(tiles == [
    {"label": "Open pipeline", "value": "$137K"},
    {"label": "Closing this month", "value": "2 · $52K"},
    {"label": "Stalled", "value": "2"},
    {"label": "Won rate 90d", "value": "50%"},
    {"label": "Weighted", "value": "$53K"},
], "tile band exact (5 tiles, drop-empty shape)")
check(pm.pipeline_tiles([], [], [], TODAY) == [],
      "zero open deals -> no tile band at all")
# a fixture with nothing derivable but the counts: only Stalled renders
bare = [row("b1", "Bare deal", stage="lead", opened_ago=1)]
bare_health = dh.compute_deal_health(
    bare, activity_by_thread={"b1": act(0)},
    open_commitment_thread_ids={"b1"}, today=TODAY)
bare_tiles = pm.pipeline_tiles(bare, bare_health, [], TODAY)
check(bare_tiles == [{"label": "Stalled", "value": "0"}],
      "unvalued pipeline drops every dollar tile — real zero stalled renders")

# tile shape passes the render chokepoint
from components import validate_tiles  # noqa: E402
validate_tiles(tiles)
check(True, "tile band validates against components.validate_tiles")

# --- ranking, exact order -------------------------------------------------------
check(pm.rank_score(by_id["t6"]) == 7008 and pm.rank_score(by_id["t2"]) == 3012
      and pm.rank_score(by_id["t3"]) == 2025 and pm.rank_score(by_id["t1"]) == 40,
      "rank scores exact (severity*1000 + value/1000)")
ranked = [r["thread_id"] for r in pm.rank_deals(health)]
check(ranked == ["t6", "t2", "t3", "t1", "t4", "t5", "t7"],
      "ranking exact: zombie > rotting > no-next-step > healthy by value; "
      "untracked row sinks last")

# --- zombie threshold from won-cycle history ------------------------------------
def won(opened_ago, closed_ago):
    return {"deal": {"outcome": "won", "opened_at": d(opened_ago),
                     "closed_at": d(closed_ago)}}


check(dh.median_won_cycle_days([won(30, 10)] * 4) is None,
      "median won-cycle needs >= 5 wins (None below)")
wins = [won(40, 10), won(50, 10), won(35, 5), won(60, 10), won(90, 20)]
# cycles: 30, 40, 30, 50, 70 -> sorted 30,30,40,50,70 -> median 40
check(dh.median_won_cycle_days(wins) == 40, "median won-cycle exact (40d)")
check(dh.zombie_threshold_days(wins) == 80, "zombie threshold = 2 x median (80d)")
check(dh.zombie_threshold_days([]) == 90, "zombie default 90d before 5 wins")

# --- quantify: thread deal.value beats org money fields (acceptance §7 item 8) --
ents = {
    "threads": [{"id": "project_017", "affiliation_id": "org_acme",
                 "kind": "deal", "deal": {"stage": "negotiating",
                                          "value": 40000}}],
    "orgs": [{"id": "org_acme", "canonical_name": "Acme Co"}],
}
deal_thread = ents["threads"][0]
check(quantify.money_time_tag(deal_thread, ents, now=TODAY.isoformat()) == "$40K deal",
      "quantify renders $40K deal from deal.value with NO org money field")
commitment = {"type": "commitment", "primary_thread_id": "project_017",
              "data": {"due": d(12)}}
check(quantify.money_time_tag(commitment, ents, now=TODAY.isoformat())
      == "12d late · $40K deal",
      "a commitment on the deal thread inherits the deal.value money part")
ents_conflict = {
    "threads": [{"id": "project_018", "affiliation_id": "org_rich",
                 "kind": "deal", "deal": {"stage": "lead", "value": 7000}}],
    "orgs": [{"id": "org_rich", "canonical_name": "Rich Co",
              "deal_value": 999999}],
}
check(quantify.money_time_tag(ents_conflict["threads"][0], ents_conflict,
                              now=TODAY.isoformat()) == "$7K deal",
      "thread deal.value takes precedence over the org's money fields")
no_value = {"threads": [{"id": "project_019", "affiliation_id": "org_x",
                         "kind": "deal", "deal": {"stage": "lead"}}],
            "orgs": [{"id": "org_x", "canonical_name": "X"}]}
check(quantify.money_time_tag(no_value["threads"][0], no_value,
                              now=TODAY.isoformat()) is None,
      "no stated value -> no money tag, never an estimate")

# --- prospects_not_in_pipeline (PIPE1 D9.1 reconciliation line) ---------------
recon_ents = {
    "orgs": [
        # uncovered prospect — the reconciliation line's subject
        {"id": "org_gap", "canonical_name": "Beacon Logistics",
         "relationship_type": "prospect"},
        # prospect with an OPEN deal thread — covered, never counted
        {"id": "org_dealt", "canonical_name": "Acme Co",
         "relationship_type": "prospect"},
        # prospect with an active ENGAGEMENT thread — FS-18b coverage counts
        {"id": "org_engaged", "canonical_name": "Northwind",
         "relationship_type": "prospect"},
        # client org — not a prospect, out of scope for this line
        {"id": "org_client", "canonical_name": "Summit",
         "relationship_type": "client"},
        # archived prospect — not open pursuit, dropped
        {"id": "org_gone", "canonical_name": "Vendor Corp",
         "relationship_type": "prospect", "status": "archived"},
    ],
    "threads": [
        {"id": "project_500", "kind": "deal", "status": "active",
         "org_id": "org_dealt", "deal": {"stage": "lead"}},
        {"id": "project_501", "kind": "product", "status": "active",
         "org_id": "org_engaged"},
        # resolved deal thread on the gap org — terminal, NOT coverage
        {"id": "project_502", "kind": "deal", "status": "resolved",
         "org_id": "org_gap", "deal": {"stage": "proposal_sent"}},
    ],
}
gap = pm.prospects_not_in_pipeline(recon_ents)
check([r["org_id"] for r in gap] == ["org_gap"],
      "prospects_not_in_pipeline counts ONLY uncovered open prospects "
      "(open deal + active engagement + client + archived all excluded)")
check(gap[0]["name"] == "Beacon Logistics",
      "reconciliation rows carry the reader-facing org name")
check(pm.prospects_not_in_pipeline({"entities": recon_ents}) == gap,
      "wrapper-shaped entities.json reads identically")
check(pm.prospects_not_in_pipeline({"orgs": [], "threads": []}) == [],
      "no prospects -> empty list (the surface drops the line at zero)")

print(f"OK — all {PASS} pipeline_math/deal_health/quantify tests passed")
sys.exit(0)
