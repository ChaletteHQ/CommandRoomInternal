#!/usr/bin/env python3
"""
Regression guard for P0.4 (Phase 4 trust patch, 2026-07-02): usage-report
must read events.jsonl from the WORKSPACE, never relative to the plugin root.

THE BUG CLASS THIS CATCHES
--------------------------
usage-report's Step 2 preamble computed `WORKSPACE` and then never used it:
the Python ran with cwd=$PLUGIN_ROOT and opened `'_hq/data/events.jsonl'`
relative — a path that does not exist under the plugin root. Every cold fire
hit the "(Nothing to report yet…)" empty-state branch even with real data
present, so the skill was structurally incapable of ever reporting.

WHAT THIS GUARD ASSERTS
-----------------------
1. The documented Step 2 bash preamble exports WORKSPACE into the python env.
2. The documented python snippet resolves events.jsonl via
   os.environ['WORKSPACE'] and carries no plugin-root-relative open().
3. Functional, on a real-shape fixture: the exact read pattern the skill
   documents (env-var join + line-parse + pack_run filter +
   aggregate_pack_run_telemetry) finds telemetry-bearing events written by
   telemetry.build_pack_run_telemetry into a workspace that is NOT the cwd.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "shared" / "scripts"))

from telemetry import build_pack_run_telemetry, aggregate_pack_run_telemetry  # noqa: E402

SKILL = PLUGIN_ROOT / "skills" / "usage-report" / "SKILL.md"

failures = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


text = SKILL.read_text(encoding="utf-8")

# 1. Bash preamble passes WORKSPACE into the python process env.
check(
    'WORKSPACE="$WORKSPACE" python3' in text,
    "Step 2 bash preamble does not export WORKSPACE into the python env",
)

# 2. Python snippet resolves the events path from the env var, not cwd.
check(
    "os.environ['WORKSPACE']" in text or 'os.environ["WORKSPACE"]' in text,
    "Step 2 python snippet does not resolve events.jsonl via os.environ['WORKSPACE']",
)
check(
    re.search(r"open\(\s*['\"]_hq/data/events\.jsonl", text) is None,
    "Step 2 python snippet still opens '_hq/data/events.jsonl' relative to cwd "
    "(the plugin root) — the P0.4 bug shape",
)

# 3. Functional read on a real-shape fixture workspace that is NOT the cwd.
with tempfile.TemporaryDirectory() as tmp:
    ws = Path(tmp) / "workspace"
    (ws / "_hq" / "data").mkdir(parents=True)
    tel = build_pack_run_telemetry(
        prompt_text="x" * 400,
        response_text="y" * 800,
        connector_calls=[
            {"connector": "gmail", "op": "gmail.search_threads"},
            {"connector": "calendar", "op": "calendar.list_events"},
        ],
        duration_ms=1234,
    )
    ev = {
        "type": "pack_run",
        "ts": "2026-07-01T07:30:00-07:00",
        "source_skill": "inbox",
        "data": {"telemetry": tel},
    }
    with open(ws / "_hq" / "data" / "events.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")

    old_cwd = os.getcwd()
    os.chdir(PLUGIN_ROOT)  # the cwd the skill documents (cd "$PLUGIN_ROOT")
    try:
        os.environ["WORKSPACE"] = str(ws)
        events_path = os.path.join(os.environ["WORKSPACE"], "_hq/data/events.jsonl")
        with open(events_path, "r", encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        window_events = [e for e in events if e.get("type") == "pack_run"]
        check(len(window_events) == 1, "fixture pack_run event not found via WORKSPACE path")
        agg = aggregate_pack_run_telemetry(window_events)
        check(bool(agg), "aggregate_pack_run_telemetry returned empty on real-shape fixture")
    finally:
        os.chdir(old_cwd)
        os.environ.pop("WORKSPACE", None)

if failures:
    print(f"FAIL {len(failures)} of {checks} checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print(f"OK {checks} tests passed")
