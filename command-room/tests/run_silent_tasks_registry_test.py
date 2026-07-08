#!/usr/bin/env python3
"""
Test battery for the SILENT_TASKS registry (Phase 3 / SPEC-2.3).

Acceptance from the spec: adding a dummy silent task to the dict registers
via all three loop paths with ZERO prose edits — proven here by driving the
same composition + iteration code the registration loops run, against a
registry with an injected dummy entry.
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
    print("== registry shape")
    expected = {"cleanup", "reconcile-sent", "monthly-report", "weekly-insights",
                "session-sweep"}
    check("the five silent tasks are registered", set(sc.SILENT_TASKS) == expected,
          repr(sorted(sc.SILENT_TASKS)))
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

    print("== is_silent_task classifies by registry, not name list")
    check("cleanup is silent", sc.is_silent_task("cleanup"))
    check("inbox is not silent", not sc.is_silent_task("inbox"))

    print("== prompt composition")
    p = sc.compose_silent_task_prompt("cleanup", "Penelopes Brain")
    check("basename substituted", "Penelopes Brain" in p and "{BASENAME}" not in p)
    check("prompt is the registered skill-invoking shape",
          p.startswith("# Command Room — weekly cleanup"))
    check("no frontmatter (Cowork doubling bug)", not p.lstrip().startswith("---"))
    try:
        sc.compose_silent_task_prompt("cleanup", "bad/path")
        check("path-shaped basename rejected", False)
    except ValueError:
        check("path-shaped basename rejected", True)
    try:
        sc.compose_silent_task_prompt("cleanup", "")
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
            }
        check("dummy composes alongside the real five",
              "dummy-probe" in composed and composed["dummy-probe"]["cron"] == "0 3 * * 0")
        check("all six compose cleanly", len(composed) == 6, repr(sorted(composed)))
        check("silent classification picks up the dummy", sc.is_silent_task("dummy-probe"))
    finally:
        del sc.SILENT_TASKS["dummy-probe"]
        del sc.DEFAULT_SCHEDULES["dummy-probe"]

    print("== SPEC-2.4 reconcile-sent cadence")
    spec = sc.DEFAULT_SCHEDULES["reconcile-sent"]
    check("default is 3x weekdays in one cron", spec["cron"] == "45 6,12,17 * * 1-5", spec["cron"])
    check("label matches cron_to_english render",
          sc.cron_to_english(spec["cron"]) == spec["label"],
          f'{sc.cron_to_english(spec["cron"])!r} vs {spec["label"]!r}')
    check("first fire still precedes morning-brief (6:45 anchor kept)",
          spec["cron"].startswith("45 6,"))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("silent_tasks registry battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
