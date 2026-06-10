#!/usr/bin/env python3
"""Runtime smoke test for the Workspace Map / Orgs Map renderer (Bug found in A88).

WHY THIS EXISTS
During the v3.18.4 verify pass (A88, `install my dashboards`), the Orgs Map
build crashed with a TypeError: build_workspace_map_input.py:612 called
`_format_last_built(now)` with one argument, but build_dcc_input.py changed that
shared helper to `_format_last_built(now, workspace_root)` in v3.11.1 and the
call site was never updated. The Workspace Map renderer was broken for EVERYONE
on the affected versions — every `install workspace map` / bridge Orgs Map
install hit it. No unit test ran the renderer end-to-end, so the arity drift
shipped silently.

This test runs the renderer as a subprocess against a minimal synthetic
workspace and asserts it exits cleanly and produces output. It would have caught
the TypeError (which fires on every run, regardless of data). Any future
signature drift in a helper the renderer calls re-trips this.

(named runtime_exercise_* so run_all.py files it in the runtime tier.)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN_ROOT / "shared" / "scripts" / "build_workspace_map_input.py"


def main() -> int:
    if not SCRIPT.exists():
        print(f"FAIL — renderer not found at {SCRIPT}")
        return 1

    tmp = Path(tempfile.mkdtemp())
    data_dir = tmp / "_hq" / "data"
    data_dir.mkdir(parents=True)
    # Minimal but realistic: a primary-focus org, one project under it, one person.
    entities = {
        "entities": {
            "orgs": [{"id": "org_001", "canonical_name": "Acme", "is_primary_focus": True}],
            "threads": [{"id": "project_001", "display_name": "Deal", "org": "org_001", "status": "active"}],
            "people": [{"id": "person_001", "canonical_name": "Sam Sample", "primary_org_id": "org_001"}],
        }
    }
    (data_dir / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    (data_dir / "events.jsonl").write_text("", encoding="utf-8")
    out = tmp / "out.json"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--workspace-root", str(tmp),
         "--output", str(out),
         "--now", "2026-05-31T12:00:00Z"],
        capture_output=True, text=True, timeout=60,
    )

    ok = True
    if proc.returncode != 0:
        ok = False
        print(f"FAIL — renderer exited {proc.returncode}")
        print("STDERR:", proc.stderr.strip()[:800])
        # Surface the exact regression signature if present.
        if "_format_last_built" in proc.stderr or "positional argument" in proc.stderr:
            print(">>> This is the A88 arity-drift regression (build_workspace_map_input "
                  "calling a helper with the wrong number of args).")
    else:
        if not out.exists():
            ok = False
            print("FAIL — renderer exited 0 but produced no output file")
        else:
            try:
                payload = json.loads(out.read_text(encoding="utf-8"))
            except Exception as e:  # noqa
                ok = False
                print(f"FAIL — output is not valid JSON: {e!r}")
                payload = {}
            for key in ("LAST_BUILT", "ORGS_JSON", "PROJECTS_JSON"):
                if key not in payload:
                    ok = False
                    print(f"FAIL — output missing expected key {key!r}")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    if ok:
        print("OK — Workspace Map renderer runs end-to-end and emits valid output "
              "(LAST_BUILT helper called with correct arity).")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
