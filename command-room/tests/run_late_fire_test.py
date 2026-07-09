#!/usr/bin/env python3
"""
Test battery for shared/scripts/late_fire.py (Phase 3 Reliability, R4).

Real-shape fixtures: a synthetic workspace with entities.json (including a
user cron override) and events.jsonl; late_fire events written through the
append_event() gate and read back by the chronic-lateness consumer.

Covers: the three behavior tiers + silent-class exemption + unknown
fallback, machine-local math, user-override respect, telemetry through the
gate, degrade-still-writes doctrine (event lands even on degrade), the
friday-wrap 13:00 new-install default, and chronic-late proposal both
sides of the threshold.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import late_fire as lf  # noqa: E402
from schedule_config import DEFAULT_SCHEDULES  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def make_workspace(tmp: Path, overrides=None) -> Path:
    ws = tmp / "ws"
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    entities = {"workspace": {"user_timezone": "America/Los_Angeles"}}
    if overrides:
        entities["workspace"]["schedule_config"] = overrides
    (data / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def main():
    print("== defaults changed by R4")
    fw = DEFAULT_SCHEDULES["friday-wrap"]
    check("friday-wrap default is Friday 13:00 (new installs only)",
          fw["cron"] == "0 13 * * 5" and fw["label"] == "1 PM Fridays", repr(fw))
    check("tier thresholds live in ONE constant",
          lf.LATENESS_TIERS["note"] == dt.timedelta(hours=3)
          and lf.LATENESS_TIERS["degrade"] == dt.timedelta(hours=24))

    # Fixed machine-local clock: Wed 2026-07-01. morning-brief cron 0 7 * * 1-5.
    with tempfile.TemporaryDirectory() as td:
        ws = make_workspace(Path(td))

        print("== tiers (machine-local math, fixed now)")
        r = lf.check_lateness(ws, "morning-brief", now=dt.datetime(2026, 7, 1, 7, 40), emit=False)
        check("40 min late -> none (run normally, no mention)",
              r["tier"] == "none" and r["banner"] is None, repr(r))
        r = lf.check_lateness(ws, "morning-brief", now=dt.datetime(2026, 7, 1, 12, 30), emit=False)
        check("5.5h late -> note with banner", r["tier"] == "note" and r["banner"], repr(r))
        # v4.5.2 R2 (F-47/F-50): the banner states facts, never a cause —
        # the dogfood logged four fabricated "computer was likely asleep"
        # narratives in one day. Pre-R2 this assertion REQUIRED "asleep".
        check("banner is plain English facts (display name, no invented cause, no jargon)",
              "Morning Brief" in r["banner"] and "asleep" not in r["banner"]
              and not any(tok in r["banner"] for tok in ("cron", "taskId", "late_fire")),
              r["banner"])
        # Friday 13:00 slot fired Sunday morning — the friday-wrap-on-Sunday case
        r = lf.check_lateness(ws, "friday-wrap", now=dt.datetime(2026, 7, 5, 11, 16), emit=False)
        check("friday-wrap on Sunday -> degrade", r["tier"] == "degrade", repr(r))
        check("degrade notice says skipped + next-brief carry",
              r["degrade_notice"] and "Skipped the full Friday Wrap" in r["degrade_notice"]
              and "Morning Brief" in r["degrade_notice"], repr(r["degrade_notice"]))
        check("scheduled_for is the machine-local Friday 13:00",
              r["scheduled_for"] == "2026-07-03T13:00:00", r["scheduled_for"])

        print("== exemptions + fallbacks")
        r = lf.check_lateness(ws, "reconcile-sent", now=dt.datetime(2026, 7, 5, 11, 0), emit=False)
        check("silent class is exempt (registry membership, not a name list)",
              r["tier"] == "exempt" and r["banner"] is None and r["degrade_notice"] is None)
        r = lf.check_lateness(ws, "not-a-task", now=dt.datetime(2026, 7, 1, 9, 0), emit=False)
        check("unknown task -> unknown tier (run normally, never block)", r["tier"] == "unknown")

        print("== user cron override wins over the default")
        ws2 = make_workspace(Path(td) / "b",
                             overrides={"friday-wrap": {"cron": "0 16 * * 5", "label": "4 PM Fridays", "enabled": True}})
        r = lf.check_lateness(ws2, "friday-wrap", now=dt.datetime(2026, 7, 3, 17, 30), emit=False)
        check("customized 4 PM cron -> 1.5h late -> none (not judged against the new default)",
              r["tier"] == "none", repr(r))

        print("== telemetry through the append_event gate")
        r = lf.check_lateness(ws, "friday-wrap", now=dt.datetime(2026, 7, 5, 11, 16), emit=True)
        check("degrade tier logs late_fire (degrade still writes — Bug #98 doctrine)",
              r["event_logged"] is True, repr(r))
        lines = [json.loads(l) for l in
                 (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        lf_events = [e for e in lines if e.get("type") == "late_fire"]
        check("exactly one late_fire event landed", len(lf_events) == 1, repr(lines))
        ev = lf_events[0] if lf_events else {}
        check("event carries taskId + tier + lateness + scheduled_for",
              ev.get("data", {}).get("taskId") == "friday-wrap"
              and ev.get("data", {}).get("tier") == "degrade"
              and ev.get("data", {}).get("lateness_minutes", 0) > 24 * 60
              and ev.get("data", {}).get("scheduled_for"), repr(ev))
        check("gate stamped seq + ts (went through the locked writer)",
              "seq" in ev and "ts" in ev, repr(ev))

        print("== chronic-lateness proposal (the late_fire consumer)")
        events_path = ws / "_hq" / "data" / "events.jsonl"
        from event_gate import append_event
        now = dt.datetime(2026, 7, 5, 12, 0)
        # 3 distinct ISO weeks of degrade fires for friday-wrap; 1 for inbox
        for days_ago, tid in ((0, "friday-wrap"), (7, "friday-wrap"), (14, "friday-wrap"), (7, "inbox")):
            when = (dt.datetime(2026, 7, 5, 11, 0) - dt.timedelta(days=days_ago)).astimezone()
            append_event(events_path, {
                "type": "late_fire", "ts": when.isoformat(), "source_skill": tid,
                "data": {"taskId": tid, "tier": "degrade", "lateness_minutes": 2000,
                         "scheduled_for": "x"},
            }, holder="test")
        props = lf.detect_chronic_lateness(ws, now=now)
        by = {p["task"]: p for p in props}
        check("3-of-4-weeks task gets a proposal", "friday-wrap" in by, repr(props))
        check("1-week task does NOT (below threshold)", "inbox" not in by, repr(props))
        if "friday-wrap" in by:
            line = by["friday-wrap"]["line"]
            check("proposal routes through change-schedule, plain English",
                  "change my schedule" in line and "Friday Wrap" in line
                  and not any(tok in line for tok in ("cron", "late_fire", "taskId")), line)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("late_fire battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
