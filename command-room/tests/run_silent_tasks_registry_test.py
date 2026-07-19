#!/usr/bin/env python3
"""
Test battery for the SILENT_TASKS registry (Phase 3 / SPEC-2.3; topology
updated in MAINT1, 2026-07).

Acceptance from SPEC-2.3: adding a dummy silent task to the dict registers
via all three loop paths with ZERO prose edits — proven here by driving the
same composition + iteration code the registration loops run, against a
registry with an injected dummy entry.

MAINT1 acceptance layered on top: the registry holds exactly ONE entry
(`maintenance`), the five old silent taskIds are SUPERSEDED_BY data (disable,
never delete), and the 6:45 anchor (reconcile before the 7:00 morning brief)
survives as the maintenance task cron + the reconcile-sent job's nominal cron.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import schedule_config as sc  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def main():
    print("== registry shape (MAINT1: one task)")
    check("the registry holds exactly one task: maintenance",
          set(sc.SILENT_TASKS) == {"maintenance"}, repr(sorted(sc.SILENT_TASKS)))
    for tid, spec in sc.SILENT_TASKS.items():
        check(f"{tid}: has description/reason/prompt/notify",
              all(k in spec for k in ("description", "reason", "prompt", "notify")))
        check(f"{tid}: cron derivable from DEFAULT_SCHEDULES (no duplicate cron key)",
              "cron" not in spec and tid in sc.DEFAULT_SCHEDULES)
        check(f"{tid}: in FIRST_INSTALL_TASK_IDS (silent tasks auto-register)",
              tid in sc.FIRST_INSTALL_TASK_IDS)
        check(f"{tid}: prompt carries the BASENAME placeholder",
              "{BASENAME}" in spec["prompt"])
        check(f"{tid}: reason is one plain-English line (no jargon)",
              "\n" not in spec["reason"] and not any(
                  tok in spec["reason"] for tok in ("_hq", "events.jsonl", "taskId", "SKILL")))

    print("== MAINT1 supersede data (D5)")
    check("SUPERSEDED_BY maps maintenance to exactly the five old silent ids",
          sorted(sc.SUPERSEDED_BY.get("maintenance", [])) == [
              "cleanup", "monthly-report", "reconcile-sent",
              "session-sweep", "weekly-insights"],
          repr(sc.SUPERSEDED_BY))
    check("no superseded id remains in DEFAULT_SCHEDULES",
          not any(t in sc.DEFAULT_SCHEDULES
                  for ids in sc.SUPERSEDED_BY.values() for t in ids))
    check("no superseded id remains in FIRST_INSTALL_TASK_IDS",
          not any(t in sc.FIRST_INSTALL_TASK_IDS
                  for ids in sc.SUPERSEDED_BY.values() for t in ids))
    check("every SUPERSEDED_BY key is a registry task",
          all(k in sc.SILENT_TASKS for k in sc.SUPERSEDED_BY))

    print("== is_silent_task classifies by registry, not name list")
    check("maintenance is silent", sc.is_silent_task("maintenance"))
    check("inbox is not silent", not sc.is_silent_task("inbox"))
    check("superseded cleanup is no longer a registered silent TASK "
          "(it lives on as a maintenance job)", not sc.is_silent_task("cleanup"))

    print("== prompt composition")
    p = sc.compose_silent_task_prompt("maintenance", "Penelopes Brain")
    check("basename substituted", "Penelopes Brain" in p and "{BASENAME}" not in p)
    check("prompt is the registered skill-invoking shape",
          p.startswith("# Command Room — maintenance"))
    check("no frontmatter (Cowork doubling bug)", not p.lstrip().startswith("---"))
    check("prompt delegates due-ness to the dispatcher, never the model",
          "maintenance_dispatcher" in p and "NEVER judge due-ness" in p)
    check("prompt binds job success to each job's own receipt validator",
          "validate_reconcile_ran" in p and "validate_maintenance_ran" in p)
    check("prompt forbids parallel jobs (order is the contract)",
          "never in parallel" in p)
    try:
        sc.compose_silent_task_prompt("maintenance", "bad/path")
        check("path-shaped basename rejected", False)
    except ValueError:
        check("path-shaped basename rejected", True)
    try:
        sc.compose_silent_task_prompt("maintenance", "")
        check("empty basename rejected", False)
    except ValueError:
        check("empty basename rejected", True)

    print("== SPEC-2.3 acceptance: dummy task registers through the loop with zero prose edits")
    sc.SILENT_TASKS["dummy-probe"] = {
        "description": "Dummy probe (silent)",
        "reason": "test-only dummy",
        "notify": False,
        "prompt": "# Dummy probe\n\nWorkspace basename is {BASENAME}.",
    }
    sc.DEFAULT_SCHEDULES["dummy-probe"] = {"cron": "0 3 * * 0", "label": "3 AM Sundays", "enabled": True}
    try:
        # This mirrors the Step 1.D / Phase 5.9 / bridge Phase 4.7 loop bodies:
        composed = {}
        for tid in sc.SILENT_TASKS:
            composed[tid] = {
                "prompt": sc.compose_silent_task_prompt(tid, "Penelopes Brain"),
                "cron": sc.DEFAULT_SCHEDULES[tid]["cron"],
                "description": sc.SILENT_TASKS[tid]["description"],
                "notify": sc.SILENT_TASKS[tid]["notify"],
                "supersedes": list(sc.SUPERSEDED_BY.get(tid, ())),
            }
        check("dummy composes alongside maintenance",
              "dummy-probe" in composed and composed["dummy-probe"]["cron"] == "0 3 * * 0")
        check("both compose cleanly", len(composed) == 2, repr(sorted(composed)))
        check("silent classification picks up the dummy", sc.is_silent_task("dummy-probe"))
        check("a registry task without a SUPERSEDED_BY entry disables nothing",
              composed["dummy-probe"]["supersedes"] == [])
    finally:
        del sc.SILENT_TASKS["dummy-probe"]
        del sc.DEFAULT_SCHEDULES["dummy-probe"]

    print("== MAINT1 cadence (carries SPEC-2.4's anchors forward)")
    spec = sc.DEFAULT_SCHEDULES["maintenance"]
    check("maintenance fires 3x DAILY in one cron", spec["cron"] == "45 6,12,17 * * *",
          spec["cron"])
    check("label matches cron_to_english render",
          sc.cron_to_english(spec["cron"]) == spec["label"],
          f'{sc.cron_to_english(spec["cron"])!r} vs {spec["label"]!r}')
    check("first fire still precedes morning-brief (6:45 anchor kept)",
          spec["cron"].startswith("45 6,"))
    from maintenance_dispatcher import MAINTENANCE_JOBS
    check("reconcile-sent job keeps the SPEC-2.4 weekday nominal cadence",
          MAINTENANCE_JOBS["reconcile-sent"]["nominal_cron"] == "45 6,12,17 * * 1-5")
    check("every maintenance job's nominal cron parses",
          all(sc.parse_cron(j["nominal_cron"]) for j in MAINTENANCE_JOBS.values()))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("silent_tasks registry battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
