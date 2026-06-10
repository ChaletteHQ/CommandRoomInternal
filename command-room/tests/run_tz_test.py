#!/usr/bin/env python3
"""
Tests for `shared/scripts/tz.py` v3.11.1+ contract.

The 2026-05-20 morning-brief bug surfaced that the prior walk-up resolver
silently returned UTC whenever it ran from inside the plugin clone. v3.11.1
removed the walk-up and made `workspace_path` a required arg (or `CR_WORKSPACE`
env var). These tests lock in:

  - to_local(value, workspace_path=…) succeeds when the path resolves and
    user_timezone is set.
  - CR_WORKSPACE env var works as a fallback when workspace_path is omitted.
  - Walk-up resolution was removed: passing no workspace_path and no env var
    raises TZResolutionError (NOT silent UTC fallback).
  - load_workspace_tz raises TZResolutionError when entities.json has no
    workspace.user_timezone (not silent UTC).
  - format_local round-trip renders in the configured workspace TZ.

The bug pattern: tz.py's stderr warnings are invisible in chat output, so any
silent UTC fallback was wrong-but-plausible and shipped to users for ~7 months.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared" / "scripts"))

from tz import (  # noqa: E402
    TZResolutionError,
    format_local,
    load_workspace_tz,
    to_local,
)

PASS = "  ✓"
FAIL = "  ✗"
results = {"pass": 0, "fail": 0, "failures": []}


def check(name, cond, expected=None, got=None):
    if cond:
        print(f"{PASS} {name}")
        results["pass"] += 1
    else:
        print(f"{FAIL} {name}")
        if expected is not None:
            print(f"      expected: {expected!r}")
            print(f"      got:      {got!r}")
        results["fail"] += 1
        results["failures"].append(name)


def _make_workspace(tmpdir: Path, tz_name: str | None) -> Path:
    """Build a fake workspace dir with _hq/data/entities.json + workspace block."""
    ws = tmpdir / "workspace"
    (ws / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    block: dict = {}
    if tz_name is not None:
        block = {"workspace": {"user_timezone": tz_name}}
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps({"entities": block}), encoding="utf-8"
    )
    return ws


print("=== tz.py v3.11.1 contract ===")

# Save env so test never poisons subsequent runs / dev shells.
_saved_cr_workspace = os.environ.pop("CR_WORKSPACE", None)
try:
    with tempfile.TemporaryDirectory() as _tmp:
        tmp = Path(_tmp)

        # --- happy path: explicit workspace_path ---
        ws_la = _make_workspace(tmp, "America/Los_Angeles")
        try:
            dt = to_local("2026-05-20T20:00:00Z", workspace_path=ws_la)
            ok = dt is not None and dt.utcoffset() is not None and dt.tzinfo is not None
            check("to_local resolves with explicit workspace_path", ok)
        except Exception as exc:
            check("to_local resolves with explicit workspace_path", False, "no exception", repr(exc))

        # --- happy path: CR_WORKSPACE env var ---
        os.environ["CR_WORKSPACE"] = str(ws_la)
        try:
            dt = to_local("2026-05-20T20:00:00Z")
            check("to_local resolves via CR_WORKSPACE env var", dt is not None)
        finally:
            os.environ.pop("CR_WORKSPACE", None)

        # --- no walk-up: no path + no env var → raises ---
        raised = False
        try:
            to_local("2026-05-20T20:00:00Z")
        except TZResolutionError:
            raised = True
        except Exception as exc:
            check("missing workspace raises TZResolutionError (not other)", False, "TZResolutionError", type(exc).__name__)
        check("missing workspace raises TZResolutionError", raised)

        # --- workspace exists but no user_timezone → raises ---
        ws_blank = _make_workspace(tmp / "blank", None)
        raised = False
        try:
            load_workspace_tz(workspace_path=ws_blank)
        except TZResolutionError:
            raised = True
        check("missing user_timezone raises TZResolutionError", raised)

        # --- format_local rendering uses the resolved zone ---
        out = format_local(
            "2026-05-20T20:00:00Z",
            fmt="%Y-%m-%d %H:%M %Z",
            workspace_path=ws_la,
        )
        # 2026-05-20 20:00 UTC → 13:00 PDT (LA is UTC-7 in May).
        check(
            "format_local renders in workspace TZ",
            out.startswith("2026-05-20 13:00") and ("PDT" in out or "PST" in out or "-07" in out or "-08" in out),
            "2026-05-20 13:00 PDT",
            out,
        )

        # --- None passes through ---
        check("to_local(None) passes through", to_local(None, workspace_path=ws_la) is None)
        check("format_local(None) → empty string", format_local(None, workspace_path=ws_la) == "")

        # --- bad path supplied but CR_WORKSPACE works ---
        os.environ["CR_WORKSPACE"] = str(ws_la)
        try:
            dt = to_local("2026-05-20T20:00:00Z", workspace_path=tmp / "does-not-exist")
            check("bad workspace_path falls through to CR_WORKSPACE", dt is not None)
        finally:
            os.environ.pop("CR_WORKSPACE", None)

finally:
    if _saved_cr_workspace is not None:
        os.environ["CR_WORKSPACE"] = _saved_cr_workspace

print()
print(f"{results['pass']} passed, {results['fail']} failed")
if results["fail"]:
    for f in results["failures"]:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
