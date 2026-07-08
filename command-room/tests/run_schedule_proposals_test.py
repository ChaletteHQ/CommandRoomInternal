#!/usr/bin/env python3
"""
Test battery for shared/scripts/schedule_proposals.py (Phase 3, corrected R3).

Real-shape fixtures: entities.json orgs with relationship_type, events.jsonl
dormancy_signal history, schedule_add_proposed suppression records. The
qualified fixture mirrors the 2026-07-01 audit workspace (12 prospects, 8
clients, months of dormancy signal, relationship-moves unregistered).
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


def make_workspace(tmp, prospects=12, clients=8, dormancy_days=30, prior_proposals=()):
    ws = Path(tmp)
    (ws / "_hq" / "data").mkdir(parents=True)
    orgs = (
        [{"id": f"org_p{i}", "relationship_type": "prospect"} for i in range(prospects)]
        + [{"id": f"org_c{i}", "relationship_type": "client"} for i in range(clients)]
        + [{"id": "org_x", "relationship_type": "client", "status": "archived"}]
    )
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps({"orgs": orgs, "workspace": {}}), encoding="utf-8")
    with open(ws / "_hq" / "data" / "events.jsonl", "w", encoding="utf-8") as f:
        for d in range(dormancy_days):
            f.write(json.dumps({"type": "dormancy_signal", "ts": _utc_iso(d + 1),
                                "data": {"entity_type": "person"}}) + "\n")
        for tid, days_ago in prior_proposals:
            f.write(json.dumps({"type": "schedule_add_proposed", "ts": _utc_iso(days_ago),
                                "source_skill": "cleanup", "data": {"taskId": tid}}) + "\n")
    return ws


REG = {"morning-brief", "inbox", "cleanup"}  # relationship-moves NOT registered


def main():
    print("== thresholds live in ONE table")
    check("relationship-moves thresholds present",
          sp.PROPOSAL_THRESHOLDS["relationship-moves"]["min_prospect_plus_client_orgs"] == 8
          and sp.PROPOSAL_THRESHOLDS["relationship-moves"]["min_dormancy_signal_days"] == 14)
    check("dormant-customer-scan threshold present",
          sp.PROPOSAL_THRESHOLDS["dormant-customer-scan"]["min_client_orgs"] == 5)

    with tempfile.TemporaryDirectory() as td:
        print("== the audit-workspace shape proposes relationship-moves")
        ws = make_workspace(Path(td) / "a")
        before = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
        props = sp.propose_later_add_tasks(ws, REG)
        check("exactly one proposal", len(props) == 1, repr(props))
        check("it's relationship-moves (never both)",
              props and props[0]["task"] == "relationship-moves", repr(props))
        check("line cites the real counts + routes through the existing add path",
              props and "12 prospects" in props[0]["line"] and "8 clients" in props[0]["line"]
              and "add relationship moves" in props[0]["line"], repr(props))
        check("proposing wrote NOTHING (propose-never-register, read-only check)",
              (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8") == before)

        print("== substrate-readiness gate")
        ws2 = make_workspace(Path(td) / "b", dormancy_days=5)
        props = sp.propose_later_add_tasks(ws2, REG)
        check("org mix alone is not enough (<14 dormancy days) -> lighter alternative instead",
              len(props) == 1 and props[0]["task"] == "dormant-customer-scan", repr(props))

        print("== org-mix gate")
        ws3 = make_workspace(Path(td) / "c", prospects=2, clients=3)
        props = sp.propose_later_add_tasks(ws3, REG)
        check("under-qualified workspace proposes nothing", props == [], repr(props))

        print("== registered relationship-moves shuts the whole family off")
        props = sp.propose_later_add_tasks(ws, REG | {"relationship-moves"})
        check("no proposal when relationship-moves is registered (it consumes dormancy)",
              props == [], repr(props))

        print("== suppression (proposed 2 weeks ago -> quiet; 8 weeks ago + declined -> alternative)")
        ws4 = make_workspace(Path(td) / "d", prior_proposals=[("relationship-moves", 14)])
        props = sp.propose_later_add_tasks(ws4, REG)
        check("recent proposal suppresses relationship-moves; declined -> lighter alternative offered",
              len(props) == 1 and props[0]["task"] == "dormant-customer-scan", repr(props))
        ws5 = make_workspace(Path(td) / "e",
                             prior_proposals=[("relationship-moves", 14), ("dormant-customer-scan", 14)])
        props = sp.propose_later_add_tasks(ws5, REG)
        check("both recently proposed -> silence (no weekly nag)", props == [], repr(props))
        ws6 = make_workspace(Path(td) / "f", prior_proposals=[("relationship-moves", 60)])
        props = sp.propose_later_add_tasks(ws6, REG)
        check("proposal older than the 6-week window may re-surface (suppression expired, per R3)",
              len(props) == 1 and props[0]["task"] == "relationship-moves", repr(props))

        print("== log_proposal writes the suppression record through the gate")
        ok = sp.log_proposal(ws, "relationship-moves")
        check("log_proposal succeeds", ok is True)
        events = [json.loads(l) for l in
                  (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        rec = [e for e in events if e.get("type") == "schedule_add_proposed"]
        check("schedule_add_proposed landed with seq/ts stamped",
              len(rec) == 1 and "seq" in rec[0] and rec[0]["data"]["taskId"] == "relationship-moves", repr(rec))
        props = sp.propose_later_add_tasks(ws, REG)
        check("the logged record now suppresses relationship-moves",
              not any(p["task"] == "relationship-moves" for p in props), repr(props))

        print("== no jargon in any proposal line")
        for w in (ws, ws2, ws4):
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
