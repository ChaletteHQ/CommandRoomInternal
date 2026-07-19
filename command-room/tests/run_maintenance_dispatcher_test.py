#!/usr/bin/env python3
"""
Test battery for shared/scripts/maintenance_dispatcher.py (MAINT1).

Covers the spec's §6 plan: the uniform receipts-older-than-last-nominal-slot
due rule per job and per cadence, ordering (reconcile first, cleanup before
insights), Sunday catch-up, monthly once-per-month, the Saturday/weekday
split, sweep once-per-day, job-level disable overrides, the maintenance_run
receipt + validator round-trip, self-heal (a failed job stays due), malformed
substrate lines, the migration-plan structure (5 disables + 1 create,
idempotent), and the connector-agnostic sent_reconcile receipt shape
(structured provenance, provider != gmail — the v4.7.0 shapes MAINT1 must
ride on).

FIXTURE DATES: computed relative to a FROZEN anchor (Monday 2026-07-06) and
passed to due_jobs(now=...) — never compared against the real clock, so this
battery can never go red when a hardcoded "future" date passes (the MC3
time-bomb gotcha). Event timestamps are written as UTC ISO derived from the
intended machine-local wall time, mirroring how live receipts round-trip
through event_time -> _to_local_naive.

House convention: check(name, cond), non-zero exit, stdlib only.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import maintenance_dispatcher as md  # noqa: E402
import schedule_config as sc  # noqa: E402
import task_watchdog as tw  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Frozen anchor — 2026-07-06 was a Monday; 07-05 Sunday, 07-04 Saturday.
# All receipts/fires are derived from these, never from the real clock.
# ---------------------------------------------------------------------------
MON = dt.datetime(2026, 7, 6)
SUN = dt.datetime(2026, 7, 5)
SAT = dt.datetime(2026, 7, 4)
FRI = dt.datetime(2026, 7, 3)
PREV_SUN = dt.datetime(2026, 6, 28)


def at(day: dt.datetime, hour: int, minute: int) -> dt.datetime:
    return day.replace(hour=hour, minute=minute)


def iso_at(local_naive: dt.datetime) -> str:
    """UTC ISO for the given machine-local wall time — the same round trip
    live receipts take (writer stamps UTC; readers localize)."""
    return local_naive.astimezone().astimezone(dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def receipt_event(job_id: str, local_dt: dt.datetime) -> dict:
    """A receipt in the exact shape each job's real writer leaves."""
    if job_id == "reconcile-sent":
        # Connector-agnostic v4.7.0 shape: provider tag + structured
        # provenance, NOT a legacy gmail:<id> string (R16).
        return {
            "seq": 1, "ts": iso_at(local_dt), "type": "sent_reconcile",
            "source_skill": "reconcile-sent",
            "data": {
                "task_id": "reconcile-sent", "kind": "reconcile-sent",
                "status": "complete", "fired_via": "scheduled",
                "cursor_from": "2026-06-30T00:00:00Z",
                "cursor_to": iso_at(local_dt),
                "sent_scanned_count": 4, "n_closed": 1, "n_pending": 0,
                "provider": "superhuman",
                "closures": [{
                    "provenance": {
                        "connector": "mcp", "provider": "superhuman",
                        "native_id": "sh_msg_8842", "account_id": "acct_biz",
                    },
                }],
            },
        }
    if job_id == "session-sweep":
        return {"seq": 1, "ts": iso_at(local_dt), "type": "session_sweep_run",
                "source_skill": "session-sweep",
                "data": {"events_recovered": 2, "sessions_scanned": 3,
                         "fired_via": "scheduled"}}
    if job_id == "cleanup":
        return {"seq": 1, "ts": iso_at(local_dt), "type": "cleanup_run",
                "data": {"actions_taken": []}}
    if job_id == "weekly-insights":
        return {"seq": 1, "ts": iso_at(local_dt), "type": "pack_run",
                "source_skill": "weekly-insights",
                "data": {"task_id": "weekly-insights", "kind": "weekly-insights",
                         "status": "complete", "fired_via": "scheduled"}}
    if job_id == "monthly-report":
        return {"seq": 1, "ts": iso_at(local_dt),
                "type": "operator_report_generated", "data": {}}
    if job_id == "deal-signals":
        return {"seq": 1, "ts": iso_at(local_dt), "type": "pack_run",
                "source_skill": "deal-signals",
                "data": {"task_id": "deal-signals", "kind": "deal-signals",
                         "status": "complete", "fired_via": "scheduled"}}
    raise AssertionError(job_id)


def make_workspace(tmp: Path, receipts=(), entities=None, raw_lines=()) -> Path:
    ws = tmp / "Penelopes Brain"
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    with open(data / "events.jsonl", "w", encoding="utf-8") as f:
        for seq, ev in enumerate(receipts, start=1):
            ev = dict(ev)
            ev["seq"] = seq
            f.write(json.dumps(ev) + "\n")
        for line in raw_lines:
            f.write(line + "\n")
    (data / "entities.json").write_text(
        json.dumps(entities if entities is not None else {"workspace": {}}),
        encoding="utf-8")
    return ws


def due_ids(ws, now):
    return [j["job_id"] for j in md.due_jobs(ws, now=now)]


def main():
    print("== registry shape (D2)")
    check("job order is the contract: reconcile -> sweep -> cleanup -> insights -> deal-signals -> monthly",
          list(md.MAINTENANCE_JOBS) == [
              "reconcile-sent", "session-sweep", "cleanup",
              "weekly-insights", "deal-signals", "monthly-report"],
          repr(list(md.MAINTENANCE_JOBS)))
    for jid, spec in md.MAINTENANCE_JOBS.items():
        check(f"{jid}: has skill/nominal_cron/description",
              all(k in spec for k in ("skill", "nominal_cron", "description")))
    check("maintenance task in DEFAULT_SCHEDULES at 45 6,12,17 * * *",
          sc.DEFAULT_SCHEDULES.get("maintenance", {}).get("cron") == "45 6,12,17 * * *")
    check("no old silent taskId left in DEFAULT_SCHEDULES",
          not any(t in sc.DEFAULT_SCHEDULES for t in
                  ("cleanup", "reconcile-sent", "monthly-report",
                   "weekly-insights", "session-sweep")))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        print("== fresh workspace: everything due at the Monday 6:45 fire, ordering exact")
        ws = make_workspace(tmp / "fresh")
        plan = md.dispatch_plan(ws, now=at(MON, 6, 46))
        check("all six jobs due, exact registry order",
              [j["job_id"] for j in plan["due"]] == [
                  "reconcile-sent", "session-sweep", "cleanup",
                  "weekly-insights", "deal-signals", "monthly-report"],
              repr([j["job_id"] for j in plan["due"]]))
        check("no-receipt jobs carry the never-ran reason",
              all(j["reason"] == "no run recorded yet" for j in plan["due"]))
        check("nothing skipped on a fresh workspace", plan["skipped_disabled"] == [])

        print("== per-job: receipt newer than the last nominal slot -> not due; older -> due")
        ws = make_workspace(tmp / "perjob", receipts=[
            receipt_event("reconcile-sent", at(MON, 6, 50)),   # served today's 6:45
            receipt_event("session-sweep", at(MON, 6, 51)),    # served today
            receipt_event("cleanup", at(SUN, 17, 50)),         # served Sunday 17:00 slot
            receipt_event("weekly-insights", at(SUN, 17, 58)), # served Sunday
            receipt_event("deal-signals", at(SUN, 17, 59)),    # served Sunday (LB1)
            receipt_event("monthly-report", at(dt.datetime(2026, 7, 1), 6, 50)),
        ])
        check("noon fire after a served morning: nothing due",
              due_ids(ws, at(MON, 12, 0)) == [], repr(due_ids(ws, at(MON, 12, 0))))
        check("12:45 slot passes -> reconcile-sent due again (3x weekday cadence kept)",
              due_ids(ws, at(MON, 12, 46)) == ["reconcile-sent"],
              repr(due_ids(ws, at(MON, 12, 46))))

        print("== Sunday 17:45 fire: cleanup + insights due, cleanup ordered first")
        ws = make_workspace(tmp / "sunday", receipts=[
            receipt_event("reconcile-sent", at(FRI, 17, 46)),
            receipt_event("session-sweep", at(SUN, 6, 46)),
            receipt_event("cleanup", at(PREV_SUN, 17, 50)),
            receipt_event("weekly-insights", at(PREV_SUN, 18, 0)),
            receipt_event("deal-signals", at(PREV_SUN, 18, 5)),
            receipt_event("monthly-report", at(dt.datetime(2026, 7, 1), 6, 50)),
        ])
        check("Sunday evening: exactly cleanup, insights, deal-signals — in order",
              due_ids(ws, at(SUN, 17, 46)) == ["cleanup", "weekly-insights", "deal-signals"],
              repr(due_ids(ws, at(SUN, 17, 46))))

        print("== Monday 6:45 after a missed Sunday: both still due (catch-up self-heal)")
        got = due_ids(ws, at(MON, 6, 46))
        check("cleanup + insights still due Monday morning",
              "cleanup" in got and "weekly-insights" in got, repr(got))
        check("cleanup still ordered before insights",
              got.index("cleanup") < got.index("weekly-insights"), repr(got))
        check("reconcile-sent joins the Monday fire (its 6:45 slot)",
              got[0] == "reconcile-sent", repr(got))

        print("== monthly: due first fire on/after the 1st; not due again same month")
        ws = make_workspace(tmp / "monthly", receipts=[
            receipt_event("monthly-report", at(dt.datetime(2026, 6, 1), 6, 50)),
        ])
        check("July 1st fire: monthly due (June receipt < July slot)",
              "monthly-report" in due_ids(ws, at(dt.datetime(2026, 7, 1), 6, 46)))
        ws = make_workspace(tmp / "monthly2", receipts=[
            receipt_event("monthly-report", at(dt.datetime(2026, 7, 1), 6, 50)),
        ])
        check("mid-month fire: monthly not due after this month's receipt",
              "monthly-report" not in due_ids(ws, at(dt.datetime(2026, 7, 15), 6, 46)))

        print("== Saturday fire: weekday-cron job not due; sweep once per day only")
        ws = make_workspace(tmp / "saturday", receipts=[
            receipt_event("reconcile-sent", at(FRI, 17, 46)),
            receipt_event("session-sweep", at(FRI, 6, 46)),
            receipt_event("cleanup", at(PREV_SUN, 17, 50)),
            receipt_event("weekly-insights", at(PREV_SUN, 18, 0)),
            receipt_event("deal-signals", at(PREV_SUN, 18, 5)),
            receipt_event("monthly-report", at(dt.datetime(2026, 7, 1), 6, 50)),
        ])
        check("Saturday 6:45: only the daily sweep is due (reconcile's cron is weekday-only)",
              due_ids(ws, at(SAT, 6, 46)) == ["session-sweep"],
              repr(due_ids(ws, at(SAT, 6, 46))))
        ws2 = make_workspace(tmp / "saturday2", receipts=[
            receipt_event("reconcile-sent", at(FRI, 17, 46)),
            receipt_event("session-sweep", at(SAT, 6, 47)),  # swept this morning
            receipt_event("cleanup", at(PREV_SUN, 17, 50)),
            receipt_event("weekly-insights", at(PREV_SUN, 18, 0)),
            receipt_event("deal-signals", at(PREV_SUN, 18, 5)),
            receipt_event("monthly-report", at(dt.datetime(2026, 7, 1), 6, 50)),
        ])
        check("Saturday 12:45 after the morning sweep: nothing due (sweep once/day)",
              due_ids(ws2, at(SAT, 12, 46)) == [],
              repr(due_ids(ws2, at(SAT, 12, 46))))

        print("== job-level disable override (change-schedule's job pause)")
        ws = make_workspace(tmp / "disabled", entities={
            "workspace": {"schedule_config": {
                "maintenance_jobs": {"cleanup": {"enabled": False}},
            }},
        })
        plan = md.dispatch_plan(ws, now=at(SUN, 17, 46))
        check("disabled job excluded from due",
              "cleanup" not in [j["job_id"] for j in plan["due"]],
              repr(plan["due"]))
        check("disabled job recorded in skipped_disabled",
              plan["skipped_disabled"] == ["cleanup"], repr(plan))
        check("other jobs unaffected by the override",
              "weekly-insights" in [j["job_id"] for j in plan["due"]])

        print("== connector-agnostic receipt shape (v4.7.0 structured provenance)")
        ev = receipt_event("reconcile-sent", at(FRI, 17, 46))
        line = json.dumps(ev)
        check("fixture receipt carries structured provenance, provider != gmail",
              '"provider": "superhuman"' in line and "gmail:" not in line, line[:200])
        ws = make_workspace(tmp / "agnostic", receipts=[ev])
        check("structured-provenance sent_reconcile drives due-ness: served Friday -> not due Saturday",
              "reconcile-sent" not in due_ids(ws, at(SAT, 6, 46)))
        check("...and due again at the next weekday slot (Monday 6:45)",
              "reconcile-sent" in due_ids(ws, at(MON, 6, 46)))

        print("== maintenance_receipt + validate_maintenance_ran round trip")
        ws = make_workspace(tmp / "receipt")
        v0 = md.validate_maintenance_ran(ws)
        check("validator is False before any run", v0["ok"] is False, repr(v0))
        before = dt.datetime.now() - dt.timedelta(seconds=1)
        md.maintenance_receipt(
            ws,
            jobs_due=["reconcile-sent", "cleanup"],
            jobs_completed=["reconcile-sent"],
            jobs_failed=["cleanup"],
            skipped_disabled=["weekly-insights"],
        )
        lines = [json.loads(l) for l in
                 (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines()
                 if l.strip()]
        runs = [e for e in lines if e.get("type") == "maintenance_run"]
        check("exactly ONE maintenance_run event per fire", len(runs) == 1, repr(len(runs)))
        d = runs[0]["data"]
        check("payload carries due/completed/failed/skipped + slot + canonical identity",
              d.get("jobs_due") == ["reconcile-sent", "cleanup"]
              and d.get("jobs_completed") == ["reconcile-sent"]
              and d.get("jobs_failed") == ["cleanup"]
              and d.get("skipped_disabled") == ["weekly-insights"]
              and "fired_at_slot" in d
              and d.get("task_id") == "maintenance", repr(d))
        check("event auto-stamped by the gate (seq + ts present)",
              isinstance(runs[0].get("seq"), int) and isinstance(runs[0].get("ts"), str))
        v1 = md.validate_maintenance_ran(ws, since=before)
        check("validator is True after the run (since pre-fire clock)",
              v1["ok"] is True and v1["jobs_failed"] == ["cleanup"], repr(v1))
        v2 = md.validate_maintenance_ran(ws, since=dt.datetime.now() + dt.timedelta(hours=1))
        check("a stale event does not validate a newer fire", v2["ok"] is False, repr(v2))

        print("== self-heal: a job with no receipt stays due on the next call")
        ws = make_workspace(tmp / "selfheal", receipts=[
            receipt_event("cleanup", at(SUN, 17, 50)),
            # weekly-insights FAILED this fire — no receipt landed
        ])
        got = due_ids(ws, at(SUN, 18, 30))
        check("completed job (cleanup) not due; failed job (insights) still due",
              "cleanup" not in got and "weekly-insights" in got, repr(got))

        print("== malformed substrate lines never crash the dispatcher")
        ws = make_workspace(tmp / "malformed",
                            receipts=[receipt_event("cleanup", at(SUN, 17, 50))],
                            raw_lines=[
                                "not json at all {{{",
                                json.dumps({"type": 123, "data": "nope"}),
                                json.dumps({"type": "sent_reconcile", "ts": "garbage-ts",
                                            "data": None}),
                                json.dumps({"type": "pack_run", "data": {"kind": None}}),
                                "",
                            ])
        try:
            got = due_ids(ws, at(MON, 6, 46))
            check("due_jobs survives malformed lines", True)
            check("clean receipt still honored among the garbage",
                  "cleanup" not in got, repr(got))
        except Exception as e:  # noqa: BLE001
            check("due_jobs survives malformed lines", False, repr(e))
            check("clean receipt still honored among the garbage", False)

        print("== migration plan (D5): five disables + one create, idempotent")

        def migration_plan(registered_enabled):
            # Mirrors the Step 1.D / Phase 5.9 / bridge Phase 4.7 loop bodies —
            # the same convention run_silent_tasks_registry_test uses.
            plan = {"create": [], "disable": []}
            for tid in sc.SILENT_TASKS:
                if tid not in registered_enabled:
                    plan["create"].append(tid)
                for old in sc.SUPERSEDED_BY.get(tid, ()):
                    if old in registered_enabled:
                        plan["disable"].append(old)
            return plan

        legacy = {"morning-brief", "inbox", "cleanup", "reconcile-sent",
                  "monthly-report", "weekly-insights", "session-sweep"}
        plan = migration_plan(legacy)
        check("legacy install: exactly one create (maintenance)",
              plan["create"] == ["maintenance"], repr(plan))
        check("legacy install: all five old ids disabled, none deleted",
              sorted(plan["disable"]) == ["cleanup", "monthly-report",
                                          "reconcile-sent", "session-sweep",
                                          "weekly-insights"], repr(plan))
        migrated = (legacy - set(plan["disable"])) | {"maintenance"}
        plan2 = migration_plan(migrated)
        check("re-run on the migrated state is a no-op (idempotent)",
              plan2 == {"create": [], "disable": []}, repr(plan2))

        print("== regressions riding this change")
        check("later_add_task_ids: exactly the 5 chat ids (staff-meeting joined in LB1)",
              sc.later_add_task_ids() == frozenset({
                  "commitments", "pulse", "relationship-moves",
                  "commitment-triage", "staff-meeting"}),
              repr(sorted(sc.later_add_task_ids())))
        check("FIRST_INSTALL swaps the five for maintenance",
              "maintenance" in sc.FIRST_INSTALL_TASK_IDS
              and not any(t in sc.FIRST_INSTALL_TASK_IDS for t in
                          ("cleanup", "reconcile-sent", "monthly-report",
                           "weekly-insights", "session-sweep")))
        check("SILENT_TASKS is exactly one entry (maintenance)",
              set(sc.SILENT_TASKS) == {"maintenance"}, repr(sorted(sc.SILENT_TASKS)))

        # Watchdog parity: superseded overrides + the maintenance_jobs store
        # are never drift.
        ws = make_workspace(tmp / "parity", entities={
            "workspace": {"schedule_config": {
                "reconcile-sent": {"cron": "0 6 * * 1-5", "enabled": True},
                "maintenance_jobs": {"cleanup": {"enabled": False}},
                "cr-inbox": {"cron": "0 7 * * 1-5", "enabled": True},
            }},
        })
        (ws / "_hq" / "workspace_config.json").write_text(json.dumps({
            "workspace_basename": ws.name,
            "registered_taskIds": sorted(
                (sc.FIRST_INSTALL_TASK_IDS | {"cleanup", "reconcile-sent",
                                              "monthly-report", "weekly-insights",
                                              "session-sweep"})),
        }), encoding="utf-8")
        parity = tw.check_schedule_parity(ws)
        check("parity ignores superseded-id overrides and the maintenance_jobs store",
              parity["orphan_overrides"] == ["cr-inbox"], repr(parity))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("maintenance dispatcher battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
