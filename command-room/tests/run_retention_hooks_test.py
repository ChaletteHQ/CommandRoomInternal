#!/usr/bin/env python3
"""SPEC RET1 — structural assertions over the shipped retention surfaces.

Updated for the Command Room build (2026-06) that STRIPPED all scheduled-task
generation from onboarding: the Phase 2b backfill preflight gate and the Phase 6c
day-1/week-1 registrations are gone. This test now asserts their REMOVAL (and that
onboarding registers no scheduled tasks at all), while still covering schema
enum +2, training telemetry, the Phase 0a recovery matrix, the deterministic coach
thresholds, and the selection-algorithm.md pointer resolution. The day-1/week-1
orchestrator reference files are retained (unwired) and still structurally checked.

House conventions: check(name, cond) prints OK/FAIL, non-zero exit on any FAIL,
auto-discovered by run_all.py. Reads are utf-8 (the surfaces carry ≥/σ/em-dash).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ONB = ROOT / "skills" / "command-room-onboarding" / "SKILL.md"
COACH = ROOT / "skills" / "command-room-coach" / "SKILL.md"
SCHEMA = ROOT / "shared" / "data-schemas" / "events.schema.json"
DAY1 = ROOT / "skills" / "command-room-onboarding" / "references" / "day1-checkin-orchestrator.md"
WEEK1 = ROOT / "skills" / "command-room-onboarding" / "references" / "week1-followup-orchestrator.md"
SELALG = ROOT / "skills" / "command-room-coach" / "references" / "selection-algorithm.md"
CATALOG = ROOT / "skills" / "command-room-coach" / "references" / "deliverable-catalog.md"

_failures = []


def check(name: str, cond: bool) -> None:
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _slice(text: str, start: str, end: str) -> str:
    i = text.find(start)
    j = text.find(end, i + 1) if i != -1 else -1
    if i == -1:
        return ""
    return text[i : (j if j != -1 else len(text))]


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    onb = _read(ONB)
    coach = _read(COACH)
    day1 = _read(DAY1)
    week1 = _read(WEEK1)
    selalg = _read(SELALG)

    # --- Schema ---
    schema = json.loads(_read(SCHEMA))
    enum = schema["properties"]["type"]["enum"] if "properties" in schema else schema["definitions"]["event"]["properties"]["type"]["enum"]  # tolerate shape
    for t in ("m1_training_prompt_shown", "m1_training_prompt_fired"):
        check(f"schema enum has {t} exactly once", enum.count(t) == 1)
    # Near-miss self-check: new types not within edit distance 2 of any OTHER type.
    new_types = {"m1_training_prompt_shown", "m1_training_prompt_fired"}
    near = []
    for nt in new_types:
        for other in enum:
            if other == nt or other in new_types:
                continue
            if _edit_distance(nt, other) <= 2:
                near.append((nt, other))
    check("new types not within edit distance 2 of any other canonical type", not near)

    # --- Phase 2b: Insights from the scan, NO backfill gate (stripped 2026-06) ---
    gate = _slice(onb, "### Phase 2b", "## Phase 3")
    # The gate machinery is the m1_backfill_started/_complete events — those must be gone.
    # (A passing narrative mention that the cr-m1-backfill TASK was removed is fine.)
    check("2b no longer waits on the backfill gate (no m1_backfill_* events)", "m1_backfill" not in gate)
    check("2b computes Insights from the 60-day scan", "60-day" in gate)
    check("2b fires immediately (no preflight gate / no wait)",
          ("no preflight gate" in gate.lower()) or ("no waiting" in gate.lower()) or ("fire immediately" in gate.lower()))
    check("2b still triggers on the retry phrase", "show me what's next" in gate)
    check("2b points to weekly-recap for the deeper read", "weekly-recap" in gate)

    # --- One-shot orchestrators ---
    for label, txt, taskid, taskdata in (
        ("day1", day1, "cr-day1-checkin", "day1-checkin"),
        ("week1", week1, "cr-week1-followup", "week1-followup"),
    ):
        check(f"{label} orchestrator declares recurrence once", 'recurrence: "once"' in txt)
        check(f"{label} orchestrator names its taskId", taskid in txt)
        check(f"{label} orchestrator self-disables via update_scheduled_task", "update_scheduled_task" in txt and "enabled=false" in txt)
        check(f"{label} orchestrator emits pack_run with its task_id", "pack_run" in txt and f'"{taskdata}"' in txt)
    check("day1 orchestrator reads the morning brief snapshot", "_hq/briefings/morning-" in day1)
    check("day1 orchestrator has a weekend/no-brief branch", "Weekend" in day1 or "weekend" in day1)
    for src in ("meeting_processed", "decision", "commitment", "corrections-"):
        check(f"week1 orchestrator names delta source {src}", src in week1)
    check("week1 orchestrator has an all-zero branch", "All four zero" in week1 or "all four zero" in week1 or "re-engagement" in week1)

    # --- Phase 6c: retention one-shots REMOVED (stripped 2026-06) ---
    p6 = _slice(onb, "### 6c.", "### 6b.")
    check("Phase 6c marks the retention one-shots removed", "removed" in p6.lower())
    check("Phase 6c no longer registers via create_scheduled_task", "create_scheduled_task" not in p6)
    check("Phase 6c points the customer to the opt-in schedules command",
          "set up command room schedules" in p6)
    # Onboarding-wide guard: the skill registers NO scheduled tasks of any kind.
    check("onboarding never calls create_scheduled_task anywhere", "create_scheduled_task" not in onb)
    check("onboarding no longer registers the cr-m1-backfill deep-read task",
          "register the `cr-m1-backfill`" not in onb and "registers the `cr-m1-backfill`" not in onb)

    # --- Telemetry ---
    p5 = _slice(onb, "## Phase 5", "## Phase 6")
    check("Phase 5 emits m1_training_prompt_shown", "m1_training_prompt_shown" in p5)
    check("Phase 5 emits m1_training_prompt_fired", "m1_training_prompt_fired" in p5)
    check("Phase 5 shown payload names command_slot", "command_slot" in p5)
    check("Phase 5 shown payload names resolved_target_entity_id", "resolved_target_entity_id" in p5)
    check("coach Phase 1 computes training_prompts_fired", "training_prompts_fired" in coach)
    check("coach Phase 2C has the <=1-fired complexity restriction", ("fired ≤ 1" in coach) or ("fired <= 1" in coach))

    # --- Phase 0a recovery ---
    route = _slice(onb, "5. **In-progress onboarding", "### 0b.")
    check("Phase 0a route-5 has a 6-hour threshold", "6 hours" in route or "6-hour" in route or "6h" in route)
    check("Phase 0a route-5 names the dangerous-phase set {0,1,2}", "{0, 1, 2}" in route or "{0,1,2}" in route)
    check("Phase 0a >6h branch offers both resume and restart", ("pick up where we left off" in route) and ("start fresh" in route))
    check("Phase 0a route-5 names the defensive ts/timestamp read", "`ts`" in route and "`timestamp`" in route)

    # --- Coach thresholds ---
    p2b = _slice(coach, "## Phase 2B", "## Phase 2C")
    check("coach 2B substrate floor >=10", ("≥ 10" in p2b) or (">= 10" in p2b))
    check("coach 2B substrate close-rate 15%", "15%" in p2b)
    check("coach 2B cadence top-15 floor", "top-15" in p2b)
    check("coach 2B cadence >=4 prior events", ("≥ 4" in p2b) or (">= 4" in p2b))
    check("coach 2B status-vs-reality 30-day threshold", "30 days" in p2b)
    check("coach 2B no longer says gut-punchers", "gut-punchers" not in coach)
    check("coach 2B has a deterministic rank tiebreaker", "rank" in p2b and "future-self conditionals first" in p2b)

    # --- Selection algorithm ---
    check("selection-algorithm.md exists", SELALG.exists())
    check("selection-algorithm names project ranking key", "7-day mention count" in selalg)
    check("selection-algorithm names people ranking key", "7-day interaction count" in selalg)
    check("selection-algorithm has the training-complexity gate", "training_prompts_fired" in selalg)
    check("coach SKILL no longer says 'when written'", "when written" not in coach)
    check("deliverable-catalog pointer resolves to an existing file", CATALOG.exists() and "selection-algorithm.md" in _read(CATALOG) and SELALG.exists())

    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL retention_hooks tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
