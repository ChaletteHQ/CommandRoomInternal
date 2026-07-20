#!/usr/bin/env python3
"""
Test battery for shared/scripts/schedule_proposals.py (Phase 3 R3; LB1 R4).

Real-shape fixtures: entities.json orgs with relationship_type, events.jsonl
dormancy_signal history, schedule_add_proposed suppression records. The
qualified fixture mirrors the 2026-07-01 audit workspace (12 prospects, 8
clients, months of dormancy signal, no moves surface registered).

LB1 R4 (M ruling 2026-07-14): the standalone relationship-moves chat is no
longer PROPOSED — the staff-meeting takes its slot (it absorbs the moves as
a section). Existing relationship-moves registrations stay untouched; on
those workspaces only the queue-readiness path proposes the staff meeting.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import schedule_proposals as sp  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def _utc_iso(days_ago):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def make_workspace(tmp, prospects=12, clients=8, dormancy_days=30,
                   prior_proposals=(), open_proposals=0, open_deals=0):
    ws = Path(tmp)
    (ws / "_hq" / "data").mkdir(parents=True)
    orgs = (
        [{"id": f"org_p{i}", "relationship_type": "prospect"} for i in range(prospects)]
        + [{"id": f"org_c{i}", "relationship_type": "client"} for i in range(clients)]
        + [{"id": "org_x", "relationship_type": "client", "status": "archived"}]
    )
    threads = [
        {"id": f"project_d{i}", "kind": "deal", "status": "active",
         "canonical_name": f"Deal {i}", "affiliation_id": f"org_p{i}",
         "deal": {"stage": "lead"}}
        for i in range(open_deals)
    ]
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps({"orgs": orgs, "threads": threads, "workspace": {}}),
        encoding="utf-8")
    with open(ws / "_hq" / "data" / "events.jsonl", "w", encoding="utf-8") as f:
        for d in range(dormancy_days):
            f.write(json.dumps({"type": "dormancy_signal", "ts": _utc_iso(d + 1),
                                "data": {"entity_type": "person"}}) + "\n")
        for tid, days_ago in prior_proposals:
            f.write(json.dumps({"type": "schedule_add_proposed", "ts": _utc_iso(days_ago),
                                "source_skill": "cleanup", "data": {"taskId": tid}}) + "\n")
        # open Living Brain proposals (the LB1 queue-readiness OR-path)
        for n in range(open_proposals):
            f.write(json.dumps({
                "type": "brain_proposal", "ts": _utc_iso(1),
                "source_skill": "deal-signals",
                "data": {"proposal_id": f"bp_fixture{n:06d}", "kind": "deal_update",
                         "fingerprint": f"fx{n}", "tier": "confirm",
                         "ttl_days": 14, "detector": "deal-signals"}}) + "\n")
    return ws


REG = {"morning-brief", "inbox", "cleanup"}  # no moves surface registered


def main():
    print("== thresholds live in ONE table")
    check("staff-meeting thresholds present (R4 — it took relationship-moves' slot)",
          sp.PROPOSAL_THRESHOLDS["staff-meeting"]["min_prospect_plus_client_orgs"] == 8
          and sp.PROPOSAL_THRESHOLDS["staff-meeting"]["min_dormancy_signal_days"] == 14
          and sp.PROPOSAL_THRESHOLDS["staff-meeting"]["min_open_brain_proposals"] == 3)
    check("relationship-moves is NO LONGER a proposal target (R4)",
          "relationship-moves" not in sp.PROPOSAL_THRESHOLDS)
    check("dormant-customer-scan threshold present",
          sp.PROPOSAL_THRESHOLDS["dormant-customer-scan"]["min_client_orgs"] == 5)

    with tempfile.TemporaryDirectory() as td:
        print("== the audit-workspace shape proposes the staff meeting")
        ws = make_workspace(Path(td) / "a")
        before = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
        props = sp.propose_later_add_tasks(ws, REG)
        check("exactly one proposal", len(props) == 1, repr(props))
        check("it's staff-meeting (never both; never relationship-moves)",
              props and props[0]["task"] == "staff-meeting", repr(props))
        check("line cites the real counts + routes through the existing add path",
              props and "12 prospects" in props[0]["line"] and "8 clients" in props[0]["line"]
              and "add staff meeting" in props[0]["line"], repr(props))
        check("proposing wrote NOTHING (propose-never-register, read-only check)",
              (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8") == before)

        print("== queue-readiness OR-path (LB1)")
        wsq = make_workspace(Path(td) / "q", prospects=2, clients=3,
                             dormancy_days=0, open_proposals=4)
        props = sp.propose_later_add_tasks(wsq, REG)
        check("an unqualified mix still proposes on >=3 open brain proposals",
              len(props) == 1 and props[0]["task"] == "staff-meeting", repr(props))
        check("queue-path line cites the queue + the add phrase",
              props and "4 suggestions" in props[0]["line"]
              and "add staff meeting" in props[0]["line"], repr(props))

        print("== substrate-readiness gate")
        ws2 = make_workspace(Path(td) / "b", dormancy_days=5)
        props = sp.propose_later_add_tasks(ws2, REG)
        check("org mix alone is not enough (<14 dormancy days) -> lighter alternative instead",
              len(props) == 1 and props[0]["task"] == "dormant-customer-scan", repr(props))

        print("== org-mix gate")
        ws3 = make_workspace(Path(td) / "c", prospects=2, clients=3)
        props = sp.propose_later_add_tasks(ws3, REG)
        check("under-qualified workspace proposes nothing", props == [], repr(props))

        print("== R4: a registered relationship-moves narrows the offer to the queue path")
        props = sp.propose_later_add_tasks(ws, REG | {"relationship-moves"})
        check("mix-qualified but RM-registered: no proposal (moves value covered, queue empty)",
              props == [], repr(props))
        wsr = make_workspace(Path(td) / "r", open_proposals=5)
        props = sp.propose_later_add_tasks(wsr, REG | {"relationship-moves"})
        check("RM-registered + standing queue: staff meeting still offered (queue path)",
              len(props) == 1 and props[0]["task"] == "staff-meeting", repr(props))

        print("== registered staff-meeting shuts the whole family off")
        props = sp.propose_later_add_tasks(ws, REG | {"staff-meeting"})
        check("no proposal when staff-meeting is registered",
              props == [], repr(props))

        print("== suppression (proposed 2 weeks ago -> quiet; 8 weeks ago + declined -> alternative)")
        ws4 = make_workspace(Path(td) / "d", prior_proposals=[("staff-meeting", 14)])
        props = sp.propose_later_add_tasks(ws4, REG)
        check("recent proposal suppresses staff-meeting; declined -> lighter alternative offered",
              len(props) == 1 and props[0]["task"] == "dormant-customer-scan", repr(props))
        ws5 = make_workspace(Path(td) / "e",
                             prior_proposals=[("staff-meeting", 14), ("dormant-customer-scan", 14)])
        props = sp.propose_later_add_tasks(ws5, REG)
        check("both recently proposed -> silence (no weekly nag)", props == [], repr(props))
        ws6 = make_workspace(Path(td) / "f", prior_proposals=[("staff-meeting", 60)])
        props = sp.propose_later_add_tasks(ws6, REG)
        check("proposal older than the 6-week window may re-surface (suppression expired, per R3)",
              len(props) == 1 and props[0]["task"] == "staff-meeting", repr(props))
        ws7 = make_workspace(Path(td) / "g", prior_proposals=[("relationship-moves", 14)])
        props = sp.propose_later_add_tasks(ws7, REG)
        check("a PRIOR relationship-moves offer counts as offered-before (R4) -> lighter alternative",
              len(props) == 1 and props[0]["task"] == "dormant-customer-scan", repr(props))

        print("== PIPE1 Part 2: the pipeline digest proposes only on a live pipeline")
        wsd = make_workspace(Path(td) / "pd", prospects=2, clients=3,
                             dormancy_days=0, open_deals=2)
        props = sp.propose_later_add_tasks(wsd, REG)
        check("open deals + unqualified staff-meeting mix -> digest proposed",
              len(props) == 1 and props[0]["task"] == "pipeline-digest", repr(props))
        check("digest line cites the deal count + the add phrase",
              props and "2 open deals" in props[0]["line"]
              and "add pipeline digest" in props[0]["line"], repr(props))
        wsd0 = make_workspace(Path(td) / "pd0", prospects=2, clients=3,
                              dormancy_days=0, open_deals=0)
        check("zero open deals -> NO digest (the >=1-open-deal gate, in code)",
              sp.propose_later_add_tasks(wsd0, REG) == [], repr(None))
        check("registered digest never re-proposed",
              sp.propose_later_add_tasks(wsd, REG | {"pipeline-digest"}) == [])
        wsd2 = make_workspace(Path(td) / "pd2", prospects=2, clients=3,
                              dormancy_days=0, open_deals=2,
                              prior_proposals=[("pipeline-digest", 14)])
        check("recent digest proposal suppressed (6-week window)",
              sp.propose_later_add_tasks(wsd2, REG) == [])
        wsd3 = make_workspace(Path(td) / "pd3", open_deals=2)
        props = sp.propose_later_add_tasks(wsd3, REG)
        check("staff meeting still wins the round when both qualify (never two proposals)",
              len(props) == 1 and props[0]["task"] == "staff-meeting", repr(props))
        wsd4 = make_workspace(Path(td) / "pd4", open_deals=1,
                              prior_proposals=[("staff-meeting", 14)])
        props = sp.propose_later_add_tasks(wsd4, REG)
        check("suppressed staff meeting yields the round to the digest",
              len(props) == 1 and props[0]["task"] == "pipeline-digest", repr(props))
        check("1 open deal renders singular, no jargon",
              props and "1 open deal " in props[0]["line"]
              and "taskId" not in props[0]["line"], repr(props))

        print("== log_proposal writes the suppression record through the gate")
        ok = sp.log_proposal(ws, "staff-meeting")
        check("log_proposal succeeds", ok is True)
        events = [json.loads(l) for l in
                  (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        rec = [e for e in events if e.get("type") == "schedule_add_proposed"]
        check("schedule_add_proposed landed with seq/ts stamped",
              len(rec) == 1 and "seq" in rec[0] and rec[0]["data"]["taskId"] == "staff-meeting", repr(rec))
        props = sp.propose_later_add_tasks(ws, REG)
        check("the logged record now suppresses staff-meeting",
              not any(p["task"] == "staff-meeting" for p in props), repr(props))

        print("== no jargon in any proposal line")
        for w in (ws, ws2, ws4, wsq):
            for prop in sp.propose_later_add_tasks(w, REG):
                check(f"{prop['task']}: line is customer-ready",
                      not any(tok in prop["line"] for tok in
                              ("taskId", "cron", "dormancy_signal", "_hq", "events.jsonl", "register")),
                      prop["line"])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("schedule proposals battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
