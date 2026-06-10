#!/usr/bin/env python3
"""
Structural guard: no references to skills retired from this plugin.

v3.9.0 retired six skills from Command Room and moved them to the chalette
internal plugin (`chaletteholdings/chalette` v0.5.0+):

  - preview-command-room-widget
  - run-command-room-tests
  - beta-telemetry
  - voice-test
  - process-bug-report
  - speech-prep

Active skill SKILL.md files and shared/reference docs MUST NOT name those
retired skills as routing targets, monthly-refresh runners, or workflow
participants. The v3.9.0 cleanup pass missed ~15 such references; v3.11.1
swept them; this test keeps them from recurring.

Allowed mentions (NOT violations):

  - CHANGELOG.md — the historical audit trail of releases that retired the
    skills must say their names.
  - shared/releases/v*.json — release manifests narrate what each release
    did, including retirement.
  - This test file itself — the canonical retired-skill list lives here.
  - Lines that explicitly say "retired" / "moved to chalette" /
    "v3.9.0+" in the same line as the skill name — those are honest
    deprecation notes pointing readers at the new home. The check below
    looks for a `RETIRED_OK_PATTERN` token on the same line.

This is the v3.11.1+ structural defense against the bug class the
`feedback_manual_sweep_leaves_residue.md` memory describes: a manual
retirement sweep leaves ~30% residue without a structural guard.

Mirrors `run_no_md_deliverables_test.py` / `run_no_real_customer_names_test.py`
/ `run_no_hardcoded_drive_test.py` structurally.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Skills retired from this plugin in v3.9.0. Naming any of them in an
# active surface (SKILL.md / shared/ / references/) without an accompanying
# retired-status marker is a leak.
RETIRED_SKILLS = (
    "preview-command-room-widget",
    "run-command-room-tests",
    "beta-telemetry",
    "voice-test",
    "process-bug-report",
    "speech-prep",
)

# Token that, when present on the same line as a retired-skill mention,
# marks the mention as an honest deprecation note rather than a stale ref.
# Examples:
#   "Telemetry was retired from this plugin in v3.9.0 — beta-telemetry moved to chalette."  ✓ OK
#   "Run `voice-test` to check."                                                            ✗ violation
RETIRED_OK_PATTERN = re.compile(
    r"\b(?:retired|moved to chalette|chaletteholdings/chalette|v3\.9\.0\+|"
    r"retired from this plugin|until retired)\b",
    re.IGNORECASE,
)

EXEMPT_FILES = {
    "run_no_retired_skills_test.py",
    "CHANGELOG.md",
}

# shared/releases/ is the release-manifest directory; manifests narrate
# retirements as part of the release log.
EXEMPT_DIR_PARTS = {("shared", "releases")}

SCAN_EXTENSIONS = {".md", ".py"}
SCAN_DIRS = ["skills", "shared", "references"]


def _iter_scan_paths():
    for d in SCAN_DIRS:
        scan_root = PLUGIN_ROOT / d
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            yield path


def _is_exempt(path: Path) -> bool:
    if path.name in EXEMPT_FILES:
        return True
    rel = path.relative_to(PLUGIN_ROOT).parts
    for exempt_parts in EXEMPT_DIR_PARTS:
        if len(rel) >= len(exempt_parts) and rel[: len(exempt_parts)] == exempt_parts:
            return True
    return False


def scan() -> list[tuple[Path, int, str, str]]:
    violations: list[tuple[Path, int, str, str]] = []
    skill_re = re.compile(
        r"\b(" + "|".join(re.escape(s) for s in RETIRED_SKILLS) + r")\b"
    )
    for path in _iter_scan_paths():
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        if _is_exempt(path):
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            m = skill_re.search(line)
            if not m:
                continue
            # Honest deprecation note? Same-line retired-status marker excuses it.
            if RETIRED_OK_PATTERN.search(line):
                continue
            violations.append(
                (path.relative_to(PLUGIN_ROOT), i, m.group(1), line.strip())
            )
    return violations


def main() -> int:
    violations = scan()
    if violations:
        print("FAIL — references to retired skills found in active surfaces:")
        print()
        for path, line_no, skill, line in violations:
            print(f"  {path}:{line_no}  [{skill}]  {line}")
        print()
        print(
            "Each violation should be either (a) removed if no longer relevant,\n"
            "(b) rewritten to point at the chalette internal plugin if the user is\n"
            "Matthew (e.g., \"process-bug-report lives in the chalette plugin\"), or\n"
            "(c) annotated with a same-line 'retired' / 'moved to chalette' marker\n"
            "to make the deprecation status explicit."
        )
        return 1
    print("OK — no references to retired skills in active plugin surfaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
