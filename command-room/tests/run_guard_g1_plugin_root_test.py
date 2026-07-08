#!/usr/bin/env python3
"""Guard G1 — `<plugin-root>` placeholder purge + path-resolution preambles.

CONTRACT Rule 22 calls the `<plugin-root>` grep a RELEASE BLOCKER: the
placeholder resolves to nothing at runtime, so any snippet carrying it
silently no-ops. Root cause was the FIRST_RUN_PROTOCOL boilerplate copied
into 13 skills (Phase 4 P3.1 purged them; this guard keeps them out).

Also enforces the companion invariant: any SKILL.md / orchestrator reference
that uses the cwd-relative `sys.path.insert(0, "shared/scripts")` snippet
must ALSO carry a plugin-root discovery preamble (PLUGIN_ROOT= / cd into the
plugin) somewhere in the file — the relative insert only works after the
preamble establishes the cwd, and a file that ships the insert without the
preamble reproduces the weekly-recap Phase 2 KeyError class.

Run: PYTHONUTF8=1 python tests/run_guard_g1_plugin_root_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = [ROOT / "skills", ROOT / "shared"]
# HISTORY is the archive — extracted narratives may quote old broken snippets.
EXEMPT = {"references/HISTORY.md", "shared/CONTRACT.md"}  # CONTRACT Rule 22 defines the blocker and quotes the banned form

PREAMBLE_TOKENS = ("PLUGIN_ROOT", "plugin_root =", "CLAUDE_PLUGIN_ROOT")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    violations: list[str] = []
    for d in SCAN_DIRS:
        for p in sorted(d.rglob("*.md")):
            rel = p.relative_to(ROOT).as_posix()
            if any(rel.endswith(e) for e in EXEMPT):
                continue
            text = p.read_text(encoding="utf-8")
            if "<plugin-root>" in text:
                n = text.count("<plugin-root>")
                violations.append(f"{rel}: {n}× `<plugin-root>` placeholder (Rule 22 RELEASE BLOCKER — use the discovery preamble + $PLUGIN_ROOT)")
            if 'sys.path.insert(0, "shared/scripts")' in text and not any(
                tok in text for tok in PREAMBLE_TOKENS
            ):
                violations.append(f"{rel}: relative sys.path.insert with NO plugin-root discovery preamble anywhere in the file")

    if violations:
        print(f"FAIL — {len(violations)} G1 violation(s):\n")
        for v in violations:
            print(f"  ✗ {v}")
        return 1
    print("OK — no <plugin-root> placeholders; every relative sys.path snippet has a discovery preamble")
    return 0


if __name__ == "__main__":
    sys.exit(main())
