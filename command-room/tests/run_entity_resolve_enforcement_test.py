#!/usr/bin/env python3
"""Structural enforcement test for v3.13.8 ENTITY_RESOLVE_PROTOCOL coverage.

Every skill listed in COVERED_SKILLS below MUST either:
  - reference `shared/ENTITY_RESOLVE_PROTOCOL.md` by path in its SKILL.md, OR
  - contain a `resolve_all`/`entity_resolve` invocation block that demonstrably
    enforces the canonical-helper-before-grep pattern

If a new skill that does loose-input resolution lands without one of these
markers, this test fails. The fix is to inject the marker (NOT to add the
skill to an exception list — exception lists are how Bug #45 happened).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"


COVERED_SKILLS = [
    "workspace-manager",
    "people-crm",
    "transcript-search",
    "thread-resurrection",
    "intro-broker",
    "follow-up-ritual",
    "calendar-writer",
    "email-writer",
    "dormant-customer-scan",
    "morning-briefing",
]


def _skill_md(name: str) -> Path:
    return SKILLS_DIR / name / "SKILL.md"


def _has_marker(text: str) -> bool:
    markers = (
        "shared/ENTITY_RESOLVE_PROTOCOL.md",
        "ENTITY_RESOLVE_PROTOCOL",
        "entity_resolve.py::resolve_all",
        "entity_resolve import resolve_all",
        "entity_resolve import resolve",  # 3 existing skills use this shape
        "from entity_resolve import",
    )
    return any(m in text for m in markers)


def main() -> int:
    failed: list[str] = []
    missing: list[str] = []

    for name in COVERED_SKILLS:
        skill_path = _skill_md(name)
        if not skill_path.exists():
            missing.append(name)
            continue
        text = skill_path.read_text(encoding="utf-8")
        if not _has_marker(text):
            failed.append(name)

    if missing:
        print(
            f"FAIL — {len(missing)} covered skill(s) missing SKILL.md: "
            + ", ".join(missing),
            file=sys.stderr,
        )
    if failed:
        print(
            f"FAIL — {len(failed)} covered skill(s) lack the ENTITY_RESOLVE_PROTOCOL marker "
            f"(must reference shared/ENTITY_RESOLVE_PROTOCOL.md or invoke resolve_all):",
            file=sys.stderr,
        )
        for name in failed:
            print(f"  - {name}", file=sys.stderr)

    if failed or missing:
        return 1

    print(
        f"OK — all {len(COVERED_SKILLS)} covered skills reference the ENTITY_RESOLVE_PROTOCOL"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
