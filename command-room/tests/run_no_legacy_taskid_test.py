#!/usr/bin/env python3
"""
Structural guard: no legacy `cr-`-prefixed taskId / source_skill drift in the
scheduled-orchestrator surfaces (FIX1 Batch A).

THE BUG CLASS THIS CATCHES
--------------------------
Pre-v2.14.27 the daily orchestrators registered with `cr-`-prefixed taskIds and
tagged every event they wrote `source_skill='cr-inbox'` / `'cr-commitments'` /
`'cr-past-meetings'` etc. The taskId rename dropped the prefix (registration +
`schedule_config.DEFAULT_SCHEDULES` use bare ids), but the orchestrator
reference files kept emitting the old `cr-*` source_skill values. Result:
events got tagged with a source_skill that no current reader filters on —
operator-report event rules, cleanup/dormancy, morning-brief filters, and the
pack_run fallback matcher all silently undercounted.

This guard asserts the ACTIVE drift is gone: no reference/skill/script EMITS a
`cr-*` source_skill in an assignment, and none of the five daily orchestrators
carries a `cr-*` taskId.

WHAT IS DELIBERATELY ALLOWED (and why the guard does NOT flag it)
----------------------------------------------------------------
The CLIENT-MIGRATION mandate (5 live workspaces with `cr-*` history in
append-only events.jsonl) REQUIRES the back-compat layer to *name* the legacy
forms so readers keep matching them. So descriptive prose like
``historical events written with `source_skill='cr-dont-forget'` remain valid``
is intentional and must survive. The guard targets only the EMIT shape (a
`cr-*` value assigned and immediately closed by `,` `)` `}` `]`) and the four
daily taskIds — never prose mentions, the `source_skill_compat` alias table,
CHANGELOG history, or the test corpus.

The `dont-forget`→`pulse` rename and the one-shot `cr-m1-backfill` /
`cr-historical-backfill-N` / `cr-refresh-workspace-map` taskIds are out of
scope: those are registered ids with their own documented migration story, not
the daily-orchestrator source_skill drift this batch fixes.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# A cr-* value assigned to source_skill AND immediately closed by call/dict
# punctuation — i.e. emitted, not described in prose.
# `cr-brain` is EXEMPT: it is the current LB1 Living-Brain card src (the value
# apply-choices dispatches the confirm card on), not a legacy daily-orchestrator
# tag — every LB1 surface sets `data_view["source_skill"] = "cr-brain"`, and
# `brain_proposals.build_card_view` stamps it for them. It is deliberately
# current, so the ban carves it out by name.
SOURCE_SKILL_EMIT = re.compile(
    r"""source_skill["']?\s*[:=]\s*["']cr-(?!brain["'])[a-z0-9-]+["']\s*[,)}\]]"""
)

# The four daily orchestrators must use bare taskIds. (dont-forget→pulse and the
# one-shot backfill/refresh taskIds are intentionally excluded — see docstring.)
TASKID_DRIFT = re.compile(
    r"""taskId["']?\s*[:=]\s*["']?cr-(?:inbox|commitments|past-meetings|upcoming-meetings)(?![-\w])"""
)

EXEMPT_FILES = {
    "CHANGELOG.md",
    "HISTORY.md",
    "run_no_legacy_taskid_test.py",
    "source_skill_compat.py",  # the alias table legitimately names cr-dont-forget
}

SCAN_EXTENSIONS = {".md", ".py", ".json", ".jsonl"}

# tests/ is excluded — back-compat fixtures (run_cru_match_test, run_decision_match_test,
# runtime_exercise_*) deliberately seed cr-* events to prove readers still match them.
SCAN_DIRS = ["skills", "shared", "references"]


def scan() -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    for d in SCAN_DIRS:
        scan_root = PLUGIN_ROOT / d
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SCAN_EXTENSIONS:
                continue
            if path.name in EXEMPT_FILES:
                continue
            if "__pycache__" in path.parts or "fixtures" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if SOURCE_SKILL_EMIT.search(line) or TASKID_DRIFT.search(line):
                    violations.append((path.relative_to(PLUGIN_ROOT), i, line.strip()))
    return violations


def main() -> int:
    violations = scan()
    if violations:
        print("FAIL — legacy cr-* taskId / source_skill drift found in active surfaces:")
        print()
        for path, line_no, line in violations:
            print(f"  {path}:{line_no}")
            print(f"    {line}")
            print()
        print(f"Total: {len(violations)} violation(s)")
        print()
        print("Emit bare source_skill ('inbox', 'commitments', 'past-meetings',")
        print("'upcoming-meetings', 'pulse') and register bare taskIds. Legacy cr-*")
        print("history stays valid via source_skill_compat.normalize_source_skill —")
        print("do NOT rewrite events.jsonl.")
        return 1
    print("OK — no legacy cr-* taskId / source_skill drift in orchestrator surfaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
