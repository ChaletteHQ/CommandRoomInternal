#!/usr/bin/env python3
"""
Test battery for the registration-aware schedule view + R2 parity check
(Phase 3 — corrected R1 + R2, sparse-config semantics).

Fixture is the live-workspace shape that produced the ghost-task defect:
11 registered tasks, relationship-moves enabled in DEFAULT_SCHEDULES but
NOT registered, one sparse morning-brief override, plus a legacy cr-*
orphan override.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import schedule_config as sc  # noqa: E402
import task_watchdog as tw  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


# M's live registered set at the 2026-07-01 audit (bare ids), plus the
# first-install tasks added since — session-sweep (Phase 5) is first-install,
# so a HEALTHY workspace registers it (a fixture missing it would be a genuine
# first-install ghost, which is what the parity check below asserts against).
# MAINT1: the five silent tasks became jobs inside the single `maintenance`
# task, so a HEALTHY post-migration workspace registers the 7 chats +
# maintenance (a fixture missing maintenance would be a genuine first-install
# ghost, which is what the parity check below asserts against).
# CTS1: a HEALTHY post-split workspace registers waiting-on + my-plate
# (the retired `commitments` id is disabled, not registered).
REGISTERED = {
    "morning-brief", "upcoming-meetings", "inbox", "waiting-on", "my-plate",
    "pulse", "past-meetings", "friday-wrap", "maintenance",
}


def make_workspace(tmp: Path) -> Path:
    ws = tmp / "ws"
    (ws / "_hq" / "data").mkdir(parents=True)
    entities = {"workspace": {"schedule_config": {
        # the one real override (sparse semantics: entry == operator customized)
        "morning-brief": {"cron": "0 6 * * 1-5", "label": "6 AM weekdays", "enabled": True},
        # a legacy orphan override — taskId retired v2.14.27
        "cr-inbox": {"cron": "0 7 * * 1-5", "label": "7 AM weekdays", "enabled": True},
    }}}
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    (ws / "_hq" / "workspace_config.json").write_text(
        json.dumps({"workspace_basename": "ws", "registered_taskIds": sorted(REGISTERED)}),
        encoding="utf-8")
    return ws


def main():
    print("== later_add derivation (derive, don't duplicate)")
    later = sc.later_add_task_ids()
    check("later-add = defaults minus first-install",
          later == frozenset(sc.DEFAULT_SCHEDULES) - sc.FIRST_INSTALL_TASK_IDS)
    check("relationship-moves / waiting-on / my-plate / pulse are later-add",
          {"relationship-moves", "waiting-on", "my-plate", "pulse"} <= later,
          repr(sorted(later)))  # CTS1: commitments split into waiting-on + my-plate

    with tempfile.TemporaryDirectory() as td:
        ws = make_workspace(Path(td))
        entities = ws / "_hq" / "data" / "entities.json"

        print("== load_schedule_view — the R1 partition")
        view = sc.load_schedule_view(entities, REGISTERED)
        check("all default tasks in the view", len(view) == len(sc.DEFAULT_SCHEDULES))
        rm = view["relationship-moves"]
        check("the ghost: relationship-moves is enabled-by-default but NOT registered",
              rm["enabled"] and not rm["registered"] and rm["later_add"], repr(rm))
        check("registered task renders registered",
              view["inbox"]["registered"] and not view["inbox"]["later_add"])
        check("sparse override wins for morning-brief",
              view["morning-brief"]["cron"] == "0 6 * * 1-5", repr(view["morning-brief"]))
        check("silent flag present for grouping",
              view["maintenance"]["silent"] and not view["inbox"]["silent"])
        view_empty = sc.load_schedule_view(entities, set())
        check("empty registered set -> everything honestly not-added",
              all(not s["registered"] for s in view_empty.values()))

        print("== R2 parity check — detect + report, no writes")
        before = entities.read_text(encoding="utf-8")
        parity = tw.check_schedule_parity(ws, REGISTERED)
        check("later-add ghost classified as expected (silent class for R3)",
              sorted(parity["ghost_later_add"]) == ["balance", "commitment-triage", "pipeline-digest", "relationship-moves", "staff-meeting"],
              repr(parity))  # commitment-triage joined in Phase 2 Stage D; balance in BAL1; pipeline-digest in PIPE1 Part 2
        check("no first-install ghosts on the healthy fixture",
              parity["ghost_first_install"] == [], repr(parity))
        check("legacy cr-* orphan override flagged",
              parity["orphan_overrides"] == ["cr-inbox"], repr(parity))
        check("parity check wrote NOTHING (config stays sparse)",
              entities.read_text(encoding="utf-8") == before)

        # first-install ghost: drop inbox from the registered set
        parity2 = tw.check_schedule_parity(ws, REGISTERED - {"inbox"})
        check("missing first-install task IS flagged",
              parity2["ghost_first_install"] == ["inbox"], repr(parity2))

        # falls back to workspace_config.json registered_taskIds
        parity3 = tw.check_schedule_parity(ws)
        check("defaults to workspace_config registered_taskIds",
              sorted(parity3["ghost_later_add"]) == ["balance", "commitment-triage", "pipeline-digest", "relationship-moves", "staff-meeting"],
              repr(parity3))  # commitment-triage joined in Phase 2 Stage D; balance in BAL1; pipeline-digest in PIPE1 Part 2

        print("== schedule_parity_checked is a registered event type")
        from event_types import load_event_types
        check("schedule_parity_checked in the enum",
              "schedule_parity_checked" in load_event_types())

    print("== R8 workspace-time -> machine-time conversion")
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "ws"
        (ws / "_hq" / "data").mkdir(parents=True)
        (ws / "_hq" / "data" / "entities.json").write_text(
            json.dumps({"workspace": {"user_timezone": "UTC"}}), encoding="utf-8")
        import datetime as dt
        machine_offset = dt.datetime.now().astimezone().utcoffset()
        h, m = sc.workspace_time_to_machine(8, 0, ws)
        expected = (dt.datetime(2026, 1, 1, 8, 0) + machine_offset).time()
        check("8am workspace(UTC) converts by the machine's current offset",
              (h, m) == (expected.hour, expected.minute), f"got {(h, m)}, want {(expected.hour, expected.minute)}")
        # unresolvable workspace TZ -> unconverted fallback
        h2, m2 = sc.workspace_time_to_machine(9, 30, Path(td) / "nope")
        check("unresolvable TZ falls back to the unconverted time", (h2, m2) == (9, 30))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("schedule view + parity battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
