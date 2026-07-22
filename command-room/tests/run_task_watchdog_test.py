#!/usr/bin/env python3
"""
Test battery for shared/scripts/task_watchdog.py (Phase 3 Reliability, W1;
task topology updated in MAINT1 — the five silent tasks became jobs inside
the single `maintenance` task).

Real-shape fixtures: a synthetic workspace with events.jsonl receipts in the
exact shapes the orchestrators write (pack_run with kind/source_skill,
sent_reconcile, cleanup_run, maintenance_run), workspace_config.json with
registered_taskIds, and scheduler records shaped like list_scheduled_tasks
output.

Acceptance case from SPEC-2.1: a synthetic workspace with a 5-day-dead
morning-brief produces exactly ONE plain-English flag for it.

House convention: non-zero exit on any failure; stdlib only.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import task_watchdog as tw  # noqa: E402
from schedule_config import DEFAULT_SCHEDULES, SILENT_TASKS  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def _utc_iso(delta: dt.timedelta) -> str:
    return (dt.datetime.now(dt.timezone.utc) - delta).isoformat().replace("+00:00", "Z")


def make_workspace(tmp: Path, events, registered_ids, registered_days_ago=30) -> Path:
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
            "registered_at": _utc_iso(dt.timedelta(days=registered_days_ago)),
            "registered_taskIds": registered_ids,
        }),
        encoding="utf-8",
    )
    return ws


def pack_run(source_skill, kind, delta):
    return {
        "type": "pack_run",
        "ts": _utc_iso(delta),
        "source_skill": source_skill,
        "data": {"kind": kind, "surfaced": 3, "duration_ms": 1200, "fired_via": "scheduled"},
    }


def maintenance_run(delta, **payload):
    data = {"task_id": "maintenance", "kind": "maintenance", "status": "complete",
            "fired_via": "scheduled", "jobs_due": [], "jobs_completed": [],
            "jobs_failed": [], "skipped_disabled": []}
    data.update(payload)
    return {"type": "maintenance_run", "ts": _utc_iso(delta),
            "source_skill": "maintenance", "data": data}


def main():
    print("== expected_fires — pure machine-local cron math")
    fires = tw.expected_fires("0 16 * * 5", now=dt.datetime(2026, 7, 1, 9, 0), count=2)
    check("weekly cron walks back to the last two Fridays",
          fires == [dt.datetime(2026, 6, 26, 16, 0), dt.datetime(2026, 6, 19, 16, 0)],
          repr(fires))
    fires = tw.expected_fires("0 7 * * 1-5", now=dt.datetime(2026, 7, 6, 6, 0), count=2)
    check("weekday cron skips the weekend (Mon 6 AM → Fri, Thu)",
          fires == [dt.datetime(2026, 7, 3, 7, 0), dt.datetime(2026, 7, 2, 7, 0)],
          repr(fires))
    fires = tw.expected_fires("45 6,12,17 * * 1-5", now=dt.datetime(2026, 7, 1, 13, 0), count=2)
    check("multi-hour cron orders same-day fires newest-first",
          fires == [dt.datetime(2026, 7, 1, 12, 45), dt.datetime(2026, 7, 1, 6, 45)],
          repr(fires))
    fires = tw.expected_fires("0 7 1 * *", now=dt.datetime(2026, 7, 15, 9, 0), count=2)
    check("monthly cron resolves prior months",
          fires == [dt.datetime(2026, 7, 1, 7, 0), dt.datetime(2026, 6, 1, 7, 0)],
          repr(fires))

    registered = ["morning-brief", "upcoming-meetings", "past-meetings", "inbox",
                  "friday-wrap", "maintenance"]

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        events = [
            # morning-brief: DEAD for 5 days (the SPEC-2.1 acceptance case)
            pack_run("morning-brief", "morning_brief", dt.timedelta(days=5)),
            # inbox: healthy — fired 2 hours ago
            pack_run("inbox", "inbox", dt.timedelta(hours=2)),
            # legacy-shape receipt still matches (cr- prefix era)
            pack_run("cr-upcoming-meetings", None, dt.timedelta(hours=3)),
            # past-meetings healthy
            pack_run("past-meetings", "past_meetings", dt.timedelta(hours=20)),
            # friday-wrap: weekly, fired within the last week
            pack_run("friday-wrap", "friday_wrap", dt.timedelta(days=4)),
            # maintenance: healthy — fired 2 hours ago (MAINT1)
            maintenance_run(dt.timedelta(hours=2),
                            jobs_due=["session-sweep"],
                            jobs_completed=["session-sweep"]),
            # the maintenance JOBS leave their own receipts (job freshness):
            {"type": "cleanup_run", "ts": _utc_iso(dt.timedelta(days=2)), "data": {"actions_taken": []}},
            {"type": "operator_report_generated", "ts": _utc_iso(dt.timedelta(days=10)), "data": {}},
            {"type": "session_sweep_run", "ts": _utc_iso(dt.timedelta(hours=6)),
             "data": {"events_recovered": 2, "sessions_scanned": 3}},
            {"type": "sent_reconcile", "ts": _utc_iso(dt.timedelta(hours=5)),
             "data": {"task_id": "reconcile-sent", "kind": "reconcile-sent",
                      "status": "complete", "fired_via": "scheduled",
                      "sent_scanned_count": 2, "n_closed": 0}},
            # PID1 — the identity reconciler job's own receipt (same Sunday
            # cadence as cleanup; same fixture age keeps it fresh).
            {"type": "identity_reconcile_run", "ts": _utc_iso(dt.timedelta(days=2)),
             "data": {"task_id": "identity-reconcile", "kind": "identity-reconcile",
                      "batch_id": "idr_fixture", "status": "complete",
                      "fired_via": "scheduled"}},
        ]
        ws = make_workspace(tmp, events, registered)
        # weekly-insights job receipt = view mtimes (pre-v4.5.2 fallback)
        views = ws / "_hq" / "views"
        views.mkdir(parents=True)
        (views / "TIMELINE.md").write_text("fresh", encoding="utf-8")

        reports = tw.check_tasks(ws)
        by_task = {r["task"]: r for r in reports}

        print("== statuses")
        check("5-day-dead morning-brief is late", by_task["morning-brief"]["status"] == "late",
              by_task["morning-brief"]["status"])
        check("fresh inbox is ok", by_task["inbox"]["status"] == "ok", by_task["inbox"]["status"])
        check("legacy cr-prefixed receipt still counts (upcoming-meetings ok)",
              by_task["upcoming-meetings"]["status"] == "ok", by_task["upcoming-meetings"]["status"])
        check("maintenance task is ok on a fresh maintenance_run receipt",
              by_task["maintenance"]["status"] == "ok", by_task["maintenance"]["status"])
        check("maintenance silent flag comes from the SILENT_TASKS registry",
              by_task["maintenance"]["silent"] is True and by_task["inbox"]["silent"] is False)
        check("the five old silent taskIds are NOT reported as tasks anymore",
              not any(t in by_task for t in ("cleanup", "reconcile-sent",
                                             "monthly-report", "weekly-insights",
                                             "session-sweep")), repr(sorted(by_task)))
        check("later-add relationship-moves is not_registered, not a failure",
              by_task["relationship-moves"]["status"] == "not_registered")

        print("== per-JOB freshness (MAINT1 D8)")
        jobs = {f["job"]: f for f in tw.check_maintenance_jobs(ws)}
        check("all seven jobs have a job-level check (deal-signals joined in "
              "LB1; identity-reconcile in PID1)",
              sorted(jobs) == ["cleanup", "deal-signals", "identity-reconcile",
                               "monthly-report", "reconcile-sent",
                               "session-sweep", "weekly-insights"],
              repr(sorted(jobs)))
        check("fresh job receipts are ok (reconcile 5h / sweep 6h / cleanup 2d / monthly 10d)",
              all(jobs[j]["status"] == "ok" for j in
                  ("reconcile-sent", "session-sweep", "cleanup",
                   "monthly-report", "identity-reconcile")),
              repr({j: f["status"] for j, f in jobs.items()}))
        check("weekly-insights job uses the view-mtime fallback receipt",
              jobs["weekly-insights"]["status"] == "ok", repr(jobs["weekly-insights"]))
        task_recs = [{"taskId": t, "enabled": True, "prompt": "x"} for t in registered]
        verdict = tw.health_verdict(ws, task_records=task_recs)
        check("vantage guard not tripped (scheduler records provided)",
              verdict["vantage"] is None)
        check("healthy jobs add no job-level problems to the verdict",
              not any(p.startswith("maintenance:") for p in verdict["problems"]),
              repr(verdict["problems"]))

        print("== plain-English surface")
        lines = tw.plain_english_lines(reports)
        mb_lines = [l for l in lines if "Morning Brief" in l]
        check("SPEC-2.1 acceptance: exactly ONE flag for the dead morning-brief",
              len(mb_lines) == 1, repr(mb_lines))
        # v4.5.2 R3 (F-10/F-43/F-47): the pre-R3 line asserted "the computer
        # was asleep" — a cause the watchdog cannot know. Facts + action only.
        check("late line asserts no cause (sleep narrative banned), names the action",
              "asleep" not in mb_lines[0] and "Run Now" in mb_lines[0]
              and "can't tell" in mb_lines[0], mb_lines[0] if mb_lines else "")
        check("ok tasks emit nothing",
              not any("Inbox" in l for l in lines), repr(lines))
        check("later-add ghost emits nothing (change-schedule owns that render)",
              not any("Relationship Moves" in l for l in lines), repr(lines))
        check("no jargon in any line",
              not any(tok in l for l in lines for tok in
                      ("pack_run", "events.jsonl", "_hq", "taskId", "cron")), repr(lines))

        print("== never_authorized (W2 — the class MAINT1 shrinks to one grant)")
        ws_na = make_workspace(Path(td) / "na", [], ["maintenance"])
        na = {r["task"]: r for r in tw.check_tasks(ws_na)}
        check("registered maintenance with zero receipts is never_authorized",
              na["maintenance"]["status"] == "never_authorized",
              na["maintenance"]["status"])
        na_lines = tw.plain_english_lines(list(na.values()))
        check("never_authorized line names the one-time permission",
              any("permission" in l and "Maintenance" in l for l in na_lines), repr(na_lines))

        print("== stale JOB inside a healthy maintenance task (chronic-failure surface)")
        stale_events = [
            # the task has been firing for weeks, three times recently
            maintenance_run(dt.timedelta(days=20)),
            maintenance_run(dt.timedelta(days=10)),
            maintenance_run(dt.timedelta(hours=2)),
            # four jobs healthy...
            {"type": "session_sweep_run", "ts": _utc_iso(dt.timedelta(hours=5)),
             "data": {"events_recovered": 0, "sessions_scanned": 1}},
            {"type": "cleanup_run", "ts": _utc_iso(dt.timedelta(days=2)), "data": {"actions_taken": []}},
            pack_run("weekly-insights", "weekly_insights", dt.timedelta(days=3)),
            {"type": "operator_report_generated", "ts": _utc_iso(dt.timedelta(days=10)), "data": {}},
            # ...but reconcile-sent stopped writing receipts 10 days ago
            {"type": "sent_reconcile", "ts": _utc_iso(dt.timedelta(days=10)),
             "data": {"task_id": "reconcile-sent", "kind": "reconcile-sent",
                      "status": "complete", "fired_via": "scheduled",
                      "sent_scanned_count": 2, "n_closed": 0}},
        ]
        ws_stale = make_workspace(Path(td) / "stale", stale_events, registered)
        jf = {f["job"]: f for f in tw.check_maintenance_jobs(ws_stale)}
        check("the 10-day-silent reconcile job is stale",
              jf["reconcile-sent"]["status"] == "stale", repr(jf["reconcile-sent"]))
        v2 = tw.health_verdict(ws_stale, task_records=task_recs)
        check("verdict carries the job problem under a namespaced id",
              "maintenance:reconcile-sent" in v2["problems"], repr(v2["problems"]))
        job_lines = [l for l in v2["lines"] if "Reconcile Sent" in l]
        check("one plain-English line: task running, job recorded nothing, one action",
              len(job_lines) == 1 and "Run Now" in job_lines[0]
              and "Maintenance" in job_lines[0], repr(job_lines))
        check("summary does not claim everything's running while a job is stale",
              "Everything's running" not in v2["summary_line"], v2["summary_line"])
        check("brief watchdog line counts the job problem",
              tw.brief_watchdog_line(ws_stale, verdict=v2) is not None)

        print("== receipt_gap (fired per scheduler, wrote nothing)")
        recs = [{"taskId": "past-meetings", "lastRunAt": _utc_iso(dt.timedelta(hours=1)),
                 "enabled": True, "prompt": "x"}]
        # Rebuild with a stale past-meetings receipt + fresh lastRunAt.
        # 10 days, NOT 4: "late" requires missing BOTH of the two most recent
        # expected fires, and a now-relative 4-day offset crossed the
        # (fire-time − 90min grace) cutoff late in the local day — the suite
        # flipped red after ~15:30 local and green before it (time-of-day
        # flake, root-caused 2026-07-21). 10 days is beyond both fires on any
        # sane cron at any hour. G14 can't catch this class (relative
        # offsets, not date literals) — keep fixture offsets FAR from the
        # cron grid, never near a boundary.
        events2 = [pack_run("past-meetings", "past_meetings", dt.timedelta(days=10))]
        ws2 = make_workspace(Path(td) / "b", events2, ["past-meetings"])
        reports2 = tw.check_tasks(ws2, task_records=recs)
        pm = {r["task"]: r for r in reports2}["past-meetings"]
        check("fresh lastRunAt + stale receipt flags receipt_gap", pm["receipt_gap"] is True, repr(pm))
        # v4.5.2 R2 (F-39 / C3 autopsy): lastRunAt stamps land WITHOUT
        # execution — 9 tasks stamped at app launch, one receipt. Receipts
        # are the only served/not-served truth, so a fresh stamp with a
        # 4-day-old receipt is LATE (+ receipt_gap), never "ok". Pre-R2
        # this assertion was the inverse and encoded the trust-the-stamp bug.
        check("stamped-but-receiptless run is late, not ok (stamps untrusted)",
              pm["status"] == "late", pm["status"])
        gap_lines = tw.plain_english_lines(reports2)
        check("late+receipt_gap line says 'shows a recent run… nothing recorded', no sleep guess",
              any("recorded" in l and "Past Meetings" in l for l in gap_lines)
              and not any("asleep" in l for l in gap_lines), repr(gap_lines))

        print("== workspace binding")
        check("healthy binding returns None", tw.check_workspace_binding(ws) is None)
        cfg_path = ws / "_hq" / "workspace_config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["workspace_basename"] = "Command Room (old name)"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        finding = tw.check_workspace_binding(ws)
        check("renamed folder is detected with the fix named",
              finding is not None and "set up command room schedules" in finding["fix"], repr(finding))
        lines = tw.plain_english_lines([], binding=finding)
        check("binding line is plain English + actionable",
              len(lines) == 1 and "renamed" in lines[0], repr(lines))

    print("== prompt version stamps (W4)")
    recs = [
        {"taskId": "inbox", "prompt": "# Scheduled task bootloader — inbox\nplugin-version: 4.4.0\n..."},
        {"taskId": "pulse", "prompt": "# Scheduled task bootloader — pulse\nplugin-version: 4.2.0\n..."},
        {"taskId": "maintenance", "prompt": "# Command Room — maintenance..."},
    ]
    findings = tw.check_prompt_versions(recs, "4.4.0")
    by = {f["task"]: f for f in findings}
    check("current stamp is silent", "inbox" not in by, repr(findings))
    check("stale stamp is flagged", by.get("pulse", {}).get("stale") is True, repr(findings))
    check("unstamped silent prompt is informational, not stale",
          by.get("maintenance", {}).get("stamped") is False and by["maintenance"]["stale"] is False)

    print("== registry invariants")
    check("every SILENT_TASKS entry has a DEFAULT_SCHEDULES row",
          all(t in DEFAULT_SCHEDULES for t in SILENT_TASKS), repr(sorted(SILENT_TASKS)))
    check("every enabled default task has a receipt spec",
          all(t in tw.RECEIPT_SPECS for t in DEFAULT_SCHEDULES), repr(sorted(DEFAULT_SCHEDULES)))
    from maintenance_dispatcher import MAINTENANCE_JOBS
    check("every maintenance job has a receipt spec (job freshness readable)",
          all(t in tw.RECEIPT_SPECS for t in MAINTENANCE_JOBS), repr(sorted(MAINTENANCE_JOBS)))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("task_watchdog battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
