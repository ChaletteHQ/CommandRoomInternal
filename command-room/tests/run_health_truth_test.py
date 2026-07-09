#!/usr/bin/env python3
"""
Health-check truth rules (v4.5.2 R3) — F-43 and F-40 replayed from the real
substrate shapes (FINDINGS_M_v451).

F-43 (local health check, 2026-07-08): "all 12 fired on their normal
schedule" while (a) weekly-insights + monthly-report were registered that
morning with ZERO receipts (fire history invented), (b) past-meetings had a
430-min late_fire receipt ON DISK that the reader ignored, and (c) the
Friday Wrap's Wednesday-12:20-AM receiptless catch-up was simultaneously
flagged AND counted inside "everything's running" (self-contradiction).

F-40 (cloud chat, 2026-07-08): the machine-local scheduler registry read
empty from a remote session; the check trusted it and reported "none of your
scheduled tasks are registered" + a fix that would have double-registered
all 12 — while the substrate it COULD read carried schedule_created events
and same-morning pack_runs.

The fixtures mirror the real on-disk shapes (legacy kind-only receipts,
cr-prefixed ids, dont_forget_run, scheduled_late_refire) per the realdata-
fixture convention. House convention: non-zero exit on failure; stdlib only.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import task_watchdog as tw  # noqa: E402

FAILURES = []

REGISTERED_12 = [
    "morning-brief", "upcoming-meetings", "inbox", "commitments", "pulse",
    "past-meetings", "friday-wrap", "cleanup", "reconcile-sent",
    "monthly-report", "weekly-insights", "session-sweep",
]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def _utc_iso(delta: dt.timedelta) -> str:
    return (dt.datetime.now(dt.timezone.utc) - delta).isoformat().replace("+00:00", "Z")


def _local_naive_iso(delta: dt.timedelta) -> str:
    return (dt.datetime.now() - delta).isoformat(timespec="minutes")


def make_workspace(tmp: Path, events, registered_ids, registered_at_delta) -> Path:
    ws = tmp / "Penelopes Brain"
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    with open(data / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    (data / "entities.json").write_text(json.dumps({"workspace": {}}), encoding="utf-8")
    (ws / "_hq" / "workspace_config.json").write_text(
        json.dumps({
            "workspace_root": str(ws),
            "workspace_basename": ws.name,
            "registered_at": _utc_iso(registered_at_delta),
            "registered_taskIds": registered_ids,
        }),
        encoding="utf-8",
    )
    return ws


def main():
    print("== next_fire — forward cron math (the never-fired render's time source)")
    nf = tw.next_fire("0 13 * * 5", now=dt.datetime(2026, 7, 1, 9, 0))  # Wed
    check("weekly cron walks forward to the coming Friday",
          nf == dt.datetime(2026, 7, 3, 13, 0), repr(nf))
    nf = tw.next_fire("0 16 * * 3", now=dt.datetime(2026, 7, 1, 9, 0))  # Wed, before 16:00
    check("same-day future slot is the next fire",
          nf == dt.datetime(2026, 7, 1, 16, 0), repr(nf))
    nf = tw.next_fire("0 7 1 * *", now=dt.datetime(2026, 7, 15, 9, 0))
    check("monthly cron resolves to the 1st of next month",
          nf == dt.datetime(2026, 8, 1, 7, 0), repr(nf))

    # ------------------------------------------------------------------
    print("== F-43 replay — never-fired + on-disk late_fire + receiptless catch-up")
    # Real shapes: legacy kind-only inbox receipt, cr-commitments id,
    # dont_forget_run for pulse, scheduled_late_refire on the catch-up.
    h = dt.timedelta
    events = [
        # The healthy morning stack (receipts hours old, normal fires).
        {"type": "pack_run", "ts": _utc_iso(h(hours=3)), "source_skill": "morning-brief",
         "data": {"task_id": "morning-brief", "kind": "morning-brief",
                  "fired_via": "scheduled", "status": "complete"}},
        {"type": "pack_run", "ts": _utc_iso(h(hours=4)), "source_skill": "upcoming-meetings",
         "data": {"kind": "upcoming_meetings", "surfaced": 5}},
        {"type": "pack_run", "ts": _utc_iso(h(hours=3)),
         "data": {"kind": "inbox"}},  # F-43's kind-only inbox receipt
        {"type": "pack_run", "ts": _utc_iso(h(hours=2)),
         "data": {"task_id": "cr-commitments", "fired_at": _utc_iso(h(hours=2)),
                  "outcome": "complete"}},  # F-40-obs: cr- prefixed id
        {"type": "dont_forget_run", "ts": _utc_iso(h(hours=2)), "data": {}},  # pulse legacy
        {"type": "cleanup_run", "ts": _utc_iso(h(days=2)), "data": {"actions_taken": []}},
        {"type": "sent_reconcile", "ts": _utc_iso(h(hours=5)),
         "data": {"sent_scanned": 15, "n_closed": 0}},
        {"type": "session_sweep_run", "ts": _utc_iso(h(hours=10)),
         "data": {"events_recovered": 2}},
        # past-meetings: 430-min late catch-up, late_fire event + receipt ON
        # DISK (F-43 P1b's exact state, F-34's shapes).
        {"type": "late_fire", "ts": _utc_iso(h(hours=8)), "source_skill": "past-meetings",
         "data": {"taskId": "past-meetings", "tier": "note", "lateness_minutes": 430,
                  "scheduled_for": _local_naive_iso(h(hours=8, minutes=430)),
                  "fired_via": "catchup"}},
        {"type": "pack_run", "ts": _utc_iso(h(hours=8)), "source_skill": "past-meetings",
         "data": {"kind": "past_meetings", "late_tier": "note",
                  "fired_via": "scheduled_late_refire", "surfaced": 2}},
        # friday-wrap: last REAL receipt 16 days ago; the Wednesday catch-up
        # left only a scheduler stamp (F-39/F-43 P2c).
        {"type": "pack_run", "ts": _utc_iso(h(days=16)), "source_skill": "friday-wrap",
         "data": {"kind": "friday_wrap"}},
        # weekly-insights + monthly-report: registered this morning, ZERO
        # receipts (F-43 P1a — the two whose history was invented).
    ]
    # lastRunAt mirrors each receipt's time (a normal fire updates both);
    # friday-wrap's stamp is the 12:20 AM catch-up that left NO receipt.
    stamp_deltas = {
        "morning-brief": h(hours=3), "upcoming-meetings": h(hours=4),
        "inbox": h(hours=3), "commitments": h(hours=2), "pulse": h(hours=2),
        "past-meetings": h(hours=8), "friday-wrap": h(hours=8),
        "cleanup": h(days=2), "reconcile-sent": h(hours=5),
        "session-sweep": h(hours=10),
    }
    records = []
    for tid in REGISTERED_12:
        rec = {"taskId": tid, "enabled": True, "prompt": "x"}
        if tid in stamp_deltas:  # weekly-insights/monthly-report: lastRunAt empty (disk truth)
            rec["lastRunAt"] = _utc_iso(stamp_deltas[tid])
        records.append(rec)

    with tempfile.TemporaryDirectory() as td:
        ws = make_workspace(Path(td), events, REGISTERED_12, h(hours=6))
        v = tw.health_verdict(ws, task_records=records)

        check("no vantage finding on a local check (registry visible)", v["vantage"] is None)

        by_task = {r["task"]: r for r in v["reports"]}
        check("never-fired tasks are never_fired, not fabricated history",
              by_task["weekly-insights"]["status"] == "never_fired"
              and by_task["monthly-report"]["status"] == "never_fired",
              repr({t: by_task[t]["status"] for t in ("weekly-insights", "monthly-report")}))
        check("first_run_pending holds exactly the two never-fired tasks",
              sorted(v["first_run_pending"]) == ["monthly-report", "weekly-insights"],
              repr(v["first_run_pending"]))
        fr_lines = [l for l in v["info_lines"] if "first run" in l]
        check("first-run lines say 'hasn't had its first run yet' + name the next fire",
              len(fr_lines) == 2 and all("hasn't had its first run yet" in l for l in fr_lines)
              and all("next scheduled run is" in l for l in fr_lines), repr(fr_lines))

        pm = by_task["past-meetings"]
        check("on-disk late_fire is READ: past-meetings is caught_up, not silently ok",
              pm["caught_up"] is True and v["caught_up"] == ["past-meetings"], repr(pm))
        check("catchup carries the dates (fired_at + scheduled_for) and the tier",
              (pm["catchup"] or {}).get("scheduled_for")
              and (pm["catchup"] or {}).get("tier") == "note"
              and (pm["catchup"] or {}).get("lateness_minutes") == 430, repr(pm["catchup"]))
        cu_lines = [l for l in v["info_lines"] if "caught up" in l]
        check("catch-up line is dated ('caught up [when] — its [slot] run')",
              len(cu_lines) == 1 and "Past Meetings" in cu_lines[0]
              and "didn't happen on time" in cu_lines[0], repr(cu_lines))
        check("legacy scheduled_late_refire receipt normalizes to catchup",
              pm["last_fired_via"] == "catchup", repr(pm["last_fired_via"]))

        fw = by_task["friday-wrap"]
        check("receiptless catch-up: friday-wrap is late + receipt_gap (stamp untrusted)",
              fw["status"] == "late" and fw["receipt_gap"] is True, repr(fw))
        fw_lines = [l for l in v["lines"] if "Friday Wrap" in l]
        check("friday-wrap line attributes the run claim to the schedule, dated",
              len(fw_lines) == 1 and fw_lines[0].startswith("The schedule shows")
              and "hasn't recorded any work" in fw_lines[0], repr(fw_lines))
        check("friday-wrap is a problem, past-meetings is not",
              v["problems"] == ["friday-wrap"], repr(v["problems"]))

        check("the 8 genuinely-on-schedule tasks are the ONLY on-schedule claims",
              sorted(v["on_schedule"]) == sorted([
                  "morning-brief", "upcoming-meetings", "inbox", "commitments",
                  "pulse", "cleanup", "reconcile-sent", "session-sweep"]),
              repr(v["on_schedule"]))
        buckets = [v["on_schedule"], v["caught_up"], v["first_run_pending"], v["problems"]]
        all_ids = [t for b in buckets for t in b]
        check("internal consistency: every task in exactly ONE bucket, 12 counted",
              len(all_ids) == 12 and len(set(all_ids)) == 12, repr(buckets))
        check("summary never claims 'All 12' (F-43 P1a's exact lie)",
              "All 12" not in v["summary_line"] and "8 of 12" in v["summary_line"],
              v["summary_line"])
        check("summary enumerates the non-normal buckets honestly",
              "1 caught up late" in v["summary_line"]
              and "2 are waiting on their first run" in v["summary_line"]
              and "1 needs attention" in v["summary_line"], v["summary_line"])
        every_line = v["lines"] + v["info_lines"] + [v["summary_line"]]
        check("no fabricated cause anywhere (the 'asleep' class is banned)",
              not any("asleep" in l or "usage limit" in l for l in every_line),
              repr(every_line))
        check("no jargon in any surface line",
              not any(tok in l for l in every_line for tok in
                      ("pack_run", "late_fire", "events.jsonl", "_hq", "taskId",
                       "cron", "fired_via", "receipt")), repr(every_line))

    # ------------------------------------------------------------------
    print("== F-40 replay — empty registry from a cloud vantage")
    sched_created = [
        {"type": "schedule_created", "ts": _utc_iso(dt.timedelta(days=30)),
         "data": {"taskId": tid}} for tid in REGISTERED_12
    ]
    fresh_runs = [
        {"type": "pack_run", "ts": _utc_iso(dt.timedelta(hours=2)),
         "source_skill": "morning-brief",
         "data": {"task_id": "morning-brief", "kind": "morning-brief",
                  "fired_via": "scheduled", "machine": "MDAVIDOV-PC"}},
        {"type": "sent_reconcile", "ts": _utc_iso(dt.timedelta(hours=4)),
         "data": {"machine": "MDAVIDOV-PC"}},
    ]
    with tempfile.TemporaryDirectory() as td:
        ws = make_workspace(Path(td), sched_created + fresh_runs,
                            REGISTERED_12, dt.timedelta(days=30))
        v = tw.health_verdict(ws, task_records=[])
        check("empty registry + receipted substrate → vantage finding, not outage",
              v["vantage"] is not None and v["vantage"]["check"] == "registry_vantage",
              repr(v["vantage"]))
        check("verdict says the scheduler is out of view",
              "can't see your scheduler from this chat" in v["summary_line"],
              v["summary_line"])
        check("verdict points to a local (non-cloud) chat, names no registration fix",
              "local (non-cloud) chat" in v["summary_line"]
              and "set up command room schedules" not in v["summary_line"],
              v["summary_line"])
        check("no per-task claims from the blind vantage (no 'not registered' lines)",
              v["lines"] == [] and v["reports"] == [] and v["problems"] == [],
              repr(v["lines"]))
        check("fresh receipts vouch that the tasks look alive, with the machine named",
              v["vantage"]["receipts_fresh"] is True
              and v["vantage"]["machine"] == "MDAVIDOV-PC"
              and "look alive" in v["summary_line"], v["summary_line"])

        # Registry visible → guard stands down.
        check("any visible record disables the guard",
              tw.detect_registry_vantage(
                  ws, [{"taskId": "morning-brief", "enabled": True}]) is None)

    print("== F-40 variants — stale substrate + genuinely fresh install")
    with tempfile.TemporaryDirectory() as td:
        stale = [
            {"type": "schedule_created", "ts": _utc_iso(dt.timedelta(days=60)),
             "data": {"taskId": "morning-brief"}},
            {"type": "pack_run", "ts": _utc_iso(dt.timedelta(days=20)),
             "source_skill": "morning-brief", "data": {"kind": "morning-brief"}},
        ]
        ws = make_workspace(Path(td), stale, ["morning-brief"], dt.timedelta(days=60))
        v = tw.health_verdict(ws, task_records=[])
        check("stale substrate: still the vantage verdict, honestly uncertain",
              v["vantage"] is not None and v["vantage"]["receipts_fresh"] is False
              and "can't tell from here whether" in v["summary_line"], v["summary_line"])

    with tempfile.TemporaryDirectory() as td:
        ws = make_workspace(Path(td), [], [], dt.timedelta(days=0))
        check("fresh install (no history anywhere): no vantage finding",
              tw.detect_registry_vantage(ws, []) is None)
        v = tw.health_verdict(ws, task_records=[])
        check("fresh install verdict is honest 'not set up yet' (no fabricated counts)",
              "aren't set up yet" in v["summary_line"]
              and "ran on their normal schedule" not in v["summary_line"],
              v["summary_line"])
        check("fresh install collapses the N identical missing-task flags",
              v["lines"] == [], repr(v["lines"]))

    # ------------------------------------------------------------------
    print("== problems still surface (never_authorized flow intact)")
    with tempfile.TemporaryDirectory() as td:
        ws = make_workspace(Path(td), [
            {"type": "pack_run", "ts": _utc_iso(dt.timedelta(hours=3)),
             "source_skill": "morning-brief", "data": {"kind": "morning-brief"}},
        ], ["morning-brief", "inbox"], dt.timedelta(days=30))
        recs = [{"taskId": "morning-brief", "enabled": True,
                 "lastRunAt": _utc_iso(dt.timedelta(hours=3))},
                {"taskId": "inbox", "enabled": True}]
        v = tw.health_verdict(ws, task_records=recs)
        check("30-day receiptless registered task lands in problems (never_authorized)",
              "inbox" in v["problems"]
              and {r["task"]: r for r in v["reports"]}["inbox"]["status"] == "never_authorized",
              repr(v["problems"]))
        # The 8 unregistered first-install tasks are real problems here too
        # (a partial registration IS breakage) — the assertion is only that
        # no warned task leaks into the on-schedule claim.
        check("a warned task never counts inside the on-schedule claim",
              "inbox" not in v["on_schedule"] and v["on_schedule"] == ["morning-brief"]
              and "attention" in v["summary_line"], v["summary_line"])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("health-truth battery (R3): ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
