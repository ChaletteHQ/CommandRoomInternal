#!/usr/bin/env python3
"""Wiring guard for the v3.18.2 cleanup Phase 3.5a brain-migration gate (Bug #84).

ROOT CAUSE this guards against: the v3.18.1 scheduled cleanup logged
"migrate_brain_live_state.py doesn't exist in this version — Phase 3.5a skipped
gracefully." The script EXISTS at shared/scripts/release_actions/
migrate_brain_live_state.py; the runtime mis-resolved the path (looked for it
without the release_actions/ segment) and turned an import miss into a silent
"feature not in this version" skip. Only the render ran; the real migration
(Status block, commitments→pointers, legacy-doc consolidation) never did.

The fix hardens Phase 3.5a: resolve PLUGIN_ROOT explicitly (cwd-independent),
assert-import the module, and fail LOUD on import error instead of skipping.
This guard asserts:
  1. the migration script is actually present at the release_actions/ path;
  2. it imports cleanly from PLUGIN_ROOT;
  3. cleanup/SKILL.md Phase 3.5a resolves PLUGIN_ROOT, assert-imports, and
     explicitly forbids the "doesn't exist → skip gracefully" behavior.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
CLEANUP = os.path.join(PLUGIN_ROOT, "skills", "cleanup", "SKILL.md")
MIGRATE = os.path.join(PLUGIN_ROOT, "shared", "scripts", "release_actions", "migrate_brain_live_state.py")

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def main():
    print("=== v3.18.2 cleanup Phase 3.5a brain-migration gate (Bug #84) ===\n")

    print("[1] migration script is present at the canonical release_actions/ path")
    check(
        "migrate_brain_live_state.py exists under release_actions/",
        os.path.isfile(MIGRATE),
        f"missing {MIGRATE} — the path the runtime is told to import from",
    )

    print("\n[2] the module imports cleanly and exposes migrate_brain")
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts", "release_actions"))
    try:
        import migrate_brain_live_state as m
        import render_thread_live_state as r
        imported = True
    except Exception as e:  # noqa
        imported = False
        print(f"      import error: {e!r}")
    check("import migrate_brain_live_state + render_thread_live_state", imported)
    if imported:
        check("migrate_brain_live_state.migrate_brain exists", hasattr(m, "migrate_brain"))
        check("render_thread_live_state.default_brain_path exists", hasattr(r, "default_brain_path"))

    print("\n[3] cleanup/SKILL.md Phase 3.5a is hardened (PLUGIN_ROOT + assert-import + fail-loud)")
    body = open(CLEANUP, encoding="utf-8").read() if os.path.isfile(CLEANUP) else ""
    check("cleanup SKILL.md present", bool(body), f"missing {CLEANUP}")
    # Isolate Phase 3.5a.
    s = body.find("### 3.5a")
    e = body.find("### 3.5b", s) if s != -1 else -1
    sec = body[s:e] if (s != -1 and e != -1) else ""
    check("Phase 3.5a section located", bool(sec))
    check(
        "resolves PLUGIN_ROOT explicitly (cwd-independent)",
        "PLUGIN_ROOT" in sec and ".remote-plugins/plugin_*" in sec,
        "Phase 3.5a must resolve PLUGIN_ROOT rather than rely on the working directory",
    )
    check(
        "imports from the release_actions/ path",
        "release_actions" in sec and "migrate_brain_live_state" in sec,
        "must point at shared/scripts/release_actions/migrate_brain_live_state.py",
    )
    check(
        "assert-imports and fails LOUD (no graceful skip)",
        "ABORT Phase 3.5a" in sec and "ImportError" in sec,
        "an import miss must be a loud ABORT, not a skip",
    )
    check(
        "explicitly forbids the 'doesn't exist in this version → skip' behavior",
        "skip" in sec.lower() and ("not a missing-feature" in sec.lower() or "never a silent" in sec.lower() or "NOT a missing-feature" in sec),
        "must call out and forbid the v3.18.1 graceful-skip behavior",
    )
    check(
        "names the WRONG path the runtime mis-resolved to",
        "shared/scripts/migrate_brain_live_state.py" in sec,
        "should name the without-release_actions path as the wrong one, to block the mis-resolution",
    )

    print("\n[4] Phase 3.5a + 3.5b read entities shape-defensively (Bug #84-followup)")
    # The migration/render loops must handle FLAT entities.json (threads at top level,
    # no 'entities' wrapper — M's real shape). The brittle `.get('entities') or {}`
    # silently yielded 0 threads on a flat file → migration no-op (found in A84 verify).
    sec_b_start = body.find("### 3.5b")
    sec_b_end = body.find("### 3.5c", sec_b_start) if sec_b_start != -1 else -1
    sec_b = body[sec_b_start:sec_b_end] if (sec_b_start != -1 and sec_b_end != -1) else ""
    for label, block in (("3.5a", sec), ("3.5b", sec_b)):
        check(
            f"{label} uses the shape-defensive isinstance read",
            "isinstance(_d.get('entities')" in block,
            f"{label} must read `_d['entities'] if isinstance(_d.get('entities'), dict) else _d` (flat OR wrapped)",
        )
        check(
            f"{label} no longer uses the brittle `.get('entities') or {{}}` read",
            ".get('entities') or {}" not in block,
            f"{label} still has the brittle read that yields 0 threads on a flat workspace",
        )

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
