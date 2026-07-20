#!/usr/bin/env python3
"""
v4.5.2 R2 regression battery — lateness ledger + run-mode detection.

Replays the REAL 2026-07-08 dogfood day (FINDINGS_M_v451) chronologically
against one fixture workspace, with receipts in the REAL legacy shapes the
live substrate carries (`cr-commitments` morning shape, `cr-past-meetings`
+ `late_tier` catch-up shape, `dont_forget_run` pulse shape) — per the
realdata-fixture doctrine: unit-green code crashes on real shapes unless
the fixtures mirror them.

The day under replay:

  00:10  past-meetings catch-up serves Tuesday's missed 17:00 slot
         (F-33 — the ONE legitimate late_fire of the dogfood: 430 min,
         tier note, receipt says catchup).
  08:48  commitments scheduled fire, on time (18 min — tier none).
  09:19  pulse scheduled fire, on time under the old 9:00 cron.
  14:26  F-47 trigger 1: manual commitments re-run — the dogfood wrote a
         FALSE 356-min late_fire against the slot served at 8:48.
  14:46  change-schedule moves pulse 9:00 → 9:30 (schedule_config_changed).
  14:46  F-47 trigger 2: manual past-meetings fire — the dogfood wrote a
         FALSE 1306-min late_fire against Tuesday's slot, already served
         by the 00:10 catch-up.
  14:47  F-47 trigger 3 / F-51: pulse activation after the cron change —
         the dogfood wrote a FALSE 317-min late_fire against a 9:30 slot
         that only existed because of the change (pulse ran 9:19 under the
         old cron), then spawned a phantom catch-up run.

Acceptance (the R2 spec's exact bar): the three F-47 triggers replayed
produce ZERO late_fire events — in BOTH run-mode detections (manual, and
the misdetected-as-scheduled worst case, where the served-slot ledger and
the schedule-change guard must catch them structurally); F-33's legitimate
case produces exactly ONE late_fire with the real lateness, and the
pack_run carries late_tier + fired_via=catchup.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import late_fire as lf  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def local_iso(naive: dt.datetime) -> str:
    """Fixture stamps are built machine-local then made aware, so the
    replay is TZ-portable (lateness math is machine-local by contract)."""
    return naive.astimezone().isoformat()


def make_workspace(tmp: Path, pulse_cron: str) -> Path:
    ws = tmp / "ws"
    data = ws / "_hq" / "data"
    data.mkdir(parents=True, exist_ok=True)
    entities = {"workspace": {
        "user_timezone": "America/Los_Angeles",
        "schedule_config": {
            "waiting-on": {"cron": "30 8 * * 1-5", "label": "8:30 AM weekdays", "enabled": True},  # CTS1: the migrated commitments cron carries to waiting-on
            "past-meetings": {"cron": "0 17 * * 1-5", "label": "5 PM weekdays", "enabled": True},
            "pulse": {"cron": pulse_cron, "label": "pulse", "enabled": True},
        },
    }}
    (data / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    return ws


def append_raw(ws: Path, event: dict):
    """Seed history exactly as the live substrate carries it — raw line,
    hand-rolled legacy shape, no writer gate (these shapes predate R1)."""
    p = ws / "_hq" / "data" / "events.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def late_fire_events(ws: Path) -> list:
    p = ws / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "late_fire":
            out.append(ev)
    return out


def main():
    # The replay day: Wednesday 2026-07-08 (the real dogfood day).
    WED = dt.date(2026, 7, 8)
    TUE = dt.date(2026, 7, 7)

    with tempfile.TemporaryDirectory() as td:
        ws = make_workspace(Path(td), pulse_cron="0 9 * * 1-5")

        # Seed: past-meetings last ran Tuesday MORNING — Tuesday's 17:00
        # slot was genuinely missed (machine closed). Real legacy shape.
        append_raw(ws, {
            "type": "pack_run", "seq": 3501,
            "ts": local_iso(dt.datetime.combine(TUE, dt.time(9, 0))),
            "source_skill": "cr-past-meetings",
            "data": {"kind": "past_meetings", "status": "complete"},
        })

        print("== 00:10 — F-33: the ONE legitimate late_fire (must keep working)")
        r = lf.check_lateness(ws, "past-meetings", fired_via="scheduled",
                              now=dt.datetime.combine(WED, dt.time(0, 10)))
        check("genuinely missed slot -> tier note", r["tier"] == "note", repr(r))
        check("real lateness: 430 minutes (Tue 17:00 -> Wed 00:10)",
              r["lateness_minutes"] == 430, repr(r["lateness_minutes"]))
        check("receipt_fired_via is catchup (F-51's improvised pattern, standardized)",
              r["receipt_fired_via"] == "catchup", repr(r))
        check("exactly one late_fire written", len(late_fire_events(ws)) == 1)
        ev = late_fire_events(ws)[0]
        check("late_fire carries real lateness + catchup",
              ev.get("data", {}).get("lateness_minutes") == 430
              and ev.get("data", {}).get("fired_via") == "catchup", repr(ev))
        check("banner states facts, no fabricated cause",
              r["banner"] and "asleep" not in r["banner"] and "Past Meetings" in r["banner"],
              repr(r["banner"]))

        # The catch-up run's own receipt goes through the R1 writer with
        # what the helper returned — pack_run carries late_tier (F-34).
        from receipts import iter_receipts, log_receipt
        rec = log_receipt(ws, "past-meetings", fired_via=r["receipt_fired_via"],
                          late_tier=r["tier"], surfaced=1)
        # Re-stamp the receipt's ts to the fixture clock (the gate stamps
        # wall-clock now; the replay needs 00:15 Wed as the served marker).
        p = ws / "_hq" / "data" / "events.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines()
        patched = []
        for line in lines:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                patched.append(line)
                continue
            if e.get("type") == "pack_run" and e.get("data", {}).get("task_id") == "past-meetings":
                e["ts"] = local_iso(dt.datetime.combine(WED, dt.time(0, 15)))
                line = json.dumps(e)
            patched.append(line)
        p.write_text("\n".join(patched) + "\n", encoding="utf-8")
        got = [x for x in iter_receipts(ws, task_ids=["past-meetings"])
               if x["type"] == "pack_run" and x["late_tier"]]
        check("catch-up pack_run carries late_tier=note + fired_via=catchup (read back)",
              got and got[-1]["late_tier"] == "note" and got[-1]["fired_via"] == "catchup",
              repr(got))

        print("== 08:48 — waiting-on scheduled fire, on time (real morning shape; CTS1 successor of commitments)")
        r = lf.check_lateness(ws, "waiting-on", fired_via="scheduled",
                              now=dt.datetime.combine(WED, dt.time(8, 48)))
        check("18 min -> tier none, receipt_fired_via scheduled",
              r["tier"] == "none" and r["receipt_fired_via"] == "scheduled", repr(r))
        append_raw(ws, {  # the REAL morning receipt shape (F-47 P2a verbatim fields)
            "type": "pack_run", "seq": 3560,
            "ts": local_iso(dt.datetime.combine(WED, dt.time(8, 48))),
            "source_skill": "commitments",
            "data": {"task_id": "cr-commitments",
                     "fired_at": local_iso(dt.datetime.combine(WED, dt.time(8, 48))),
                     "outcome": "complete", "surfaced_total": 7,
                     "open_total_after_filter": 63},
        })

        print("== 09:19 — pulse scheduled fire, on time under the OLD 9:00 cron")
        r = lf.check_lateness(ws, "pulse", fired_via="scheduled",
                              now=dt.datetime.combine(WED, dt.time(9, 19)))
        check("19 min -> tier none", r["tier"] == "none", repr(r))
        append_raw(ws, {  # the real pulse receipt shape F-49 missed
            "type": "dont_forget_run", "seq": 3585,
            "ts": local_iso(dt.datetime.combine(WED, dt.time(9, 19))),
            "source_skill": "dont-forget",
            "data": {"kind": "dont_forget", "status": "complete"},
        })

        baseline = len(late_fire_events(ws))
        check("baseline before the three triggers: still exactly one late_fire",
              baseline == 1, repr(baseline))

        print("== 14:26 — F-47 trigger 1: manual waiting-on re-run (dogfood: false 356-min late_fire)")
        r = lf.check_lateness(ws, "waiting-on", fired_via="manual",
                              now=dt.datetime.combine(WED, dt.time(14, 26)))
        check("manual fire -> tier manual, zero lateness, no banner",
              r["tier"] == "manual" and r["lateness_minutes"] == 0
              and r["banner"] is None and r["suppressed"] == "manual_fire", repr(r))
        check("manual fire -> receipt_fired_via manual", r["receipt_fired_via"] == "manual")
        r2 = lf.check_lateness(ws, "waiting-on", fired_via="scheduled",
                               now=dt.datetime.combine(WED, dt.time(14, 26)))
        check("worst case (misdetected as scheduled): served-slot ledger suppresses "
              "(8:30 slot served by the 8:48 cr-commitments receipt — legacy shape "
              "parsed AND bridged across the CTS1 rename via TASK_PREDECESSORS)",
              r2["tier"] == "none" and r2["suppressed"] == "slot_already_served", repr(r2))
        check("no late_fire written by trigger 1", len(late_fire_events(ws)) == baseline)

        print("== 14:46 — change-schedule moves pulse 9:00 -> 9:30 (F-51 setup)")
        entities_path = ws / "_hq" / "data" / "entities.json"
        entities = json.loads(entities_path.read_text(encoding="utf-8"))
        entities["workspace"]["schedule_config"]["pulse"] = {
            "cron": "30 9 * * 1-5", "label": "9:30 AM weekdays", "enabled": True}
        entities_path.write_text(json.dumps(entities), encoding="utf-8")
        append_raw(ws, {  # the receipt change-schedule Step 6 writes
            "type": "schedule_config_changed", "seq": 3598,
            "ts": local_iso(dt.datetime.combine(WED, dt.time(14, 46))),
            "source_skill": "change-schedule",
            "data": {"changes": [{"task_id": "pulse", "cron": "30 9 * * 1-5", "enabled": True}]},
        })

        print("== 14:46 — F-47 trigger 2: manual past-meetings fire (dogfood: false 1306-min late_fire)")
        r = lf.check_lateness(ws, "past-meetings", fired_via="manual",
                              now=dt.datetime.combine(WED, dt.time(14, 46)))
        check("manual fire -> tier manual, no event", r["tier"] == "manual", repr(r))
        r2 = lf.check_lateness(ws, "past-meetings", fired_via="scheduled",
                               now=dt.datetime.combine(WED, dt.time(14, 46)))
        check("worst case: Tuesday's slot was SERVED by the 00:15 catch-up — no second "
              "late_fire, ever", r2["tier"] == "none"
              and r2["suppressed"] == "slot_already_served", repr(r2))
        check("no late_fire written by trigger 2", len(late_fire_events(ws)) == baseline)

        print("== 14:47 — F-47 trigger 3 / F-51: pulse activation after the cron change "
              "(dogfood: false 317-min late_fire + phantom catch-up)")
        r = lf.check_lateness(ws, "pulse", fired_via="manual",
                              now=dt.datetime.combine(WED, dt.time(14, 47)))
        check("manual context -> tier manual, no event", r["tier"] == "manual", repr(r))
        r2 = lf.check_lateness(ws, "pulse", fired_via="scheduled",
                               now=dt.datetime.combine(WED, dt.time(14, 47)))
        check("worst case: the 9:30 slot was minted BY the 14:46 change (pulse ran 9:19 "
              "under the old cron) — never scored",
              r2["tier"] == "none" and r2["suppressed"] == "slot_created_by_schedule_change",
              repr(r2))
        check("no late_fire written by trigger 3", len(late_fire_events(ws)) == baseline)

        print("== the day's ledger")
        evs = late_fire_events(ws)
        check("F-47's three triggers replayed: ZERO false late_fires; F-33's case: "
              "exactly ONE (the acceptance bar)", len(evs) == 1, repr(evs))

        print("== schedule-change guard does not shadow a REAL missed fire after the change")
        # Change made Wednesday; Thursday's 9:30 slot then genuinely missed
        # (machine closed); fire lands Thursday 14:00 -> real 270-min... 4.5h
        # -> note tier. The guard only kills slots OLDER than the change.
        THU = dt.date(2026, 7, 9)
        r = lf.check_lateness(ws, "pulse", fired_via="scheduled",
                              now=dt.datetime.combine(THU, dt.time(14, 0)), emit=False)
        check("Thursday's genuinely missed 9:30 -> tier note (270 min), not suppressed",
              r["tier"] == "note" and r["lateness_minutes"] == 270
              and r["suppressed"] is None, repr(r))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("v4.5.2 R2 run-mode battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
