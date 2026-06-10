#!/usr/bin/env python3
"""
Structural guard: no hardcoded Drive paths in skill prompts or references.

The bug class this catches (v3.5.2 incident): example paths like
`~/Desktop/Google Drive/Command Room/` in reference docs and docstrings
leaked back into chat output for users whose actual workspace lived
elsewhere. Users clicked the path, hit "folder not found," lost trust.

Rule 25 of shared/CONTRACT.md: path output MUST come from runtime-resolved
$WORKSPACE, never from docstring examples. This test enforces that no
LLM-readable surface (skill prompts, shared/* references, scripts'
docstrings) contains a literal Drive path that could be improvised back
into output.

CHANGELOG.md is exempt — it's an audit trail of past incidents, not an
LLM-input surface. Connector documentation that mentions Drive as a data
source (not a save destination) is also fine.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_PATH_PATTERNS = [
    r"Desktop/Google Drive/Command Room",
    r"Desktop\\Google Drive\\Command Room",
    r"/Users/asdas/Desktop/Google Drive",
    r"C:/Users/asdas/Desktop/Google Drive",
    r"/c/Users/asdas/Desktop/Google Drive",
]

EXEMPT_FILES = {
    "CHANGELOG.md",
    "run_no_hardcoded_drive_test.py",
}

SCAN_EXTENSIONS = {".md", ".py", ".json", ".jsonl"}

SCAN_DIRS = ["skills", "shared", "references", "tests"]


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
            if "__pycache__" in path.parts:
                continue
            if "fixtures" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                for pattern in FORBIDDEN_PATH_PATTERNS:
                    if re.search(pattern, line):
                        violations.append((path.relative_to(PLUGIN_ROOT), i, line.strip()))
    return violations


def main() -> int:
    violations = scan()
    if violations:
        print("FAIL — hardcoded Drive paths found in LLM-readable surfaces:")
        print()
        for path, line_no, line in violations:
            print(f"  {path}:{line_no}")
            print(f"    {line}")
            print()
        print(f"Total: {len(violations)} violation(s)")
        print()
        print("Rule 25 of shared/CONTRACT.md: path output MUST use runtime-resolved")
        print("$WORKSPACE, never doc examples. Replace literal Drive paths with")
        print("clearly-fake placeholders like `<workspace-root>` or `$WORKSPACE`.")
        return 1
    print("OK — no hardcoded Drive paths in skill/reference/script surfaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
