#!/usr/bin/env python3
"""Guard G3 — dead-reference gate.

Two invariants:
1. Every `references/<name>.md` / `{SKILL_DIR}/references/<name>.md` citation
   in a SKILL.md must resolve to a real file — skill-local references/ first,
   then plugin-root references/. (intel-intake ran for four releases citing
   three reference files that never existed; this makes that impossible.)
2. `{SKILL_DIR}/../..`-style workspace paths are banned — the plugin dir is
   never inside the workspace (P1.8); workspace data resolves via Rule 22.

Run: PYTHONUTF8=1 python tests/run_guard_g3_dead_refs_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

REF_RE = re.compile(r"(?:\{SKILL_DIR\}/)?references/([A-Za-z0-9_\-\.]+\.md)\b")
# Files whose mentions are prose about the WORKSPACE's own files, not plugin refs
WORKSPACE_DOCS = {"HISTORY.md"}  # plugin-root references/HISTORY.md is real; keep resolving it


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    plugin_refs = {p.name for p in (ROOT / "references").glob("*.md")}
    # Cross-skill citations resolve against every skill's references dir —
    # the orchestrator files live under enable-command-room-schedules/references/
    # and are cited plugin-wide via relative links.
    all_skill_refs = {p.name for p in SKILLS.glob("*/references/*.md")}
    violations: list[str] = []

    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        skill_dir = skill_md.parent
        rel = skill_md.relative_to(ROOT).as_posix()
        text = skill_md.read_text(encoding="utf-8")

        if "{SKILL_DIR}/../.." in text:
            # allowed only as the quoted historical bad-example in a gotcha
            for m in re.finditer(r"^.*\{SKILL_DIR\}/\.\./\.\..*$", text, re.M):
                line = m.group(0)
                if "pre-P1.8" in line or "was the" in line:
                    continue
                line_no = text.count("\n", 0, m.start()) + 1
                violations.append(f"{rel}:{line_no}: {{SKILL_DIR}}/../.. workspace path (plugin dir is never inside the workspace — Rule 22)")

        local_refs = {p.name for p in (skill_dir / "references").glob("*.md")} if (skill_dir / "references").exists() else set()
        for m in REF_RE.finditer(text):
            name = m.group(1)
            if name in local_refs or name in plugin_refs or name in all_skill_refs or name in WORKSPACE_DOCS:
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            violations.append(f"{rel}:{line_no}: cites references/{name} — no such file (skill-local or plugin-root)")

    if violations:
        print(f"FAIL — {len(violations)} dead reference(s):\n")
        for v in violations:
            print(f"  ✗ {v}")
        return 1
    print("OK — every references/ citation resolves; no plugin-relative workspace paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
