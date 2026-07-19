#!/usr/bin/env python3
"""
G12 — no build/handoff artifacts inside the shipped plugin tree.

promote_core_to_clients.py copies the ENTIRE command-room/ tree (minus .git)
to every client repo, so anything that lands here ships to all clients.
Build reports and handoff docs are workspace process artifacts — they name
real people, real machines, and internal review verdicts. They belong in
the workspace handoffs folder (Penelopes Brain/Command Room/handoffs/),
never in the plugin.

Class instance that motivated this guard (HYG1 second-eyes review,
2026-07-13): EW1 committed BUILD_REPORT_ew1_2026-07-13.md under
command-room/handoffs/ — one `git add` away from fanning out to 11 client
repos with a collaborator's real name inside.

Fails if the plugin tree contains:
  - any file whose name starts with BUILD_REPORT / HANDOFF / FABLE_REVIEW
    (case-insensitive), or
  - any directory named handoffs/ (the landing zone itself — its existence
    invites the next report in).
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

BANNED_NAME_PREFIXES = ("build_report", "handoff", "fable_review")
BANNED_DIR_NAMES = {"handoffs"}


def scan() -> list[str]:
    violations: list[str] = []
    for path in PLUGIN_ROOT.rglob("*"):
        if "__pycache__" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(PLUGIN_ROOT)
        name = path.name.lower()
        if path.is_dir() and path.name.lower() in BANNED_DIR_NAMES:
            violations.append(f"{rel}{'/'}  (banned directory name)")
            continue
        if path.is_file() and name.startswith(BANNED_NAME_PREFIXES):
            violations.append(f"{rel}  (build/handoff artifact)")
    return violations


def main() -> int:
    violations = scan()
    if violations:
        print("FAIL — build/handoff artifacts found inside the shipped plugin tree:")
        print()
        for v in violations:
            print(f"  {v}")
        print()
        print("These files fan out to every client repo via")
        print("promote_core_to_clients.py. Move them to the workspace handoffs")
        print("folder (Penelopes Brain/Command Room/handoffs/) and git rm them")
        print("from the plugin tree.")
        return 1
    print("OK — no build/handoff artifacts in the plugin tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
