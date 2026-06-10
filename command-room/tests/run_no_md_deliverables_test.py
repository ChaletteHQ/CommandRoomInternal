#!/usr/bin/env python3
"""
Structural guard: no .md deliverables in user-facing output paths.

The rule (CONTRACT.md Rule 27, codified in references/MD_DELIVERABLE_POLICY.md):

  - Polished outputs the user opens to read = .docx (or .pptx / .xlsx as
    appropriate). Word and Pages render .md badly; saving deliverables
    as .md causes readability complaints from customers.

  - Files Claude reads as context/memory = .md is fine. Briefings,
    insights, intel, view files (TIMELINE / DECISION_LOG /
    MASTER_TRACKER / PEOPLE / RELATIONSHIPS), session notes, PROJECT_*
    files, voice corpus, transcripts, etc. — all stay .md.

This test enforces the rule by scanning skill SKILL.md files and other
plugin docs for path strings that look like .md deliverables.

Two pattern classes:

  1. Directory-based: any path matching
     `(deliverables|audit-reports|operator-reports|dormant|one-pagers
       |memos|board-packs|email_drafts|speeches)/<filename>.md`
     fails. Those directories are deliverable-only by convention.

  2. Filename-based: any filename matching the deliverable-prefix
     pattern (`FollowUp_`, `OnePager_`, `Memo_`, `StressTest_`,
     `SpeechPrep_`, `DORMANT_SCAN_`, etc.) ending in `.md` fails,
     regardless of directory.

Exempt: this test file, CHANGELOG.md (historical audit trail), the
policy doc itself, and CONTRACT.md (defines the rule with examples).

Companion to references/MD_DELIVERABLE_POLICY.md (the rule) and
shared/CONTRACT.md Rule 27 (the contract that points at this test as
the enforcement mechanism). Mirrors the v3.5.3 / v3.6.2 / v3.6.3
structural-guard pattern.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Directory-based forbidden patterns. Any path containing one of these
# directories followed by a .md filename is a deliverable-as-md leak.
FORBIDDEN_DIR_PATTERN = re.compile(
    r"/(?:deliverables|audit-reports|operator-reports|dormant"
    r"|one-pagers|memos|board-packs|email_drafts|speeches|summaries)/"
    r"[^\s`'\"<>]+\.md\b"
)

# Filename-based forbidden patterns. Filenames matching these prefixes
# are deliverables regardless of directory.
FORBIDDEN_FILENAME_PATTERN = re.compile(
    r"\b(?:FollowUp|OnePager|Memo|StressTest|SpeechPrep|DORMANT_SCAN"
    r"|Call_Prep|Past_Meeting|BoardPack|ContractReview|DecisionMemo)"
    r"_[^\s`'\"<>]*\.md\b"
)

EXEMPT_FILES = {
    "run_no_md_deliverables_test.py",
    "CHANGELOG.md",
    "MD_DELIVERABLE_POLICY.md",
    "CONTRACT.md",
}

SCAN_EXTENSIONS = {".md", ".py"}

SCAN_DIRS = ["skills", "shared", "references"]


def _iter_scan_paths():
    for d in SCAN_DIRS:
        scan_root = PLUGIN_ROOT / d
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            yield path


def scan() -> list[tuple[Path, int, str, str]]:
    violations: list[tuple[Path, int, str, str]] = []
    for path in _iter_scan_paths():
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        if path.name in EXEMPT_FILES:
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_DIR_PATTERN.search(line):
                violations.append(
                    (path.relative_to(PLUGIN_ROOT), i, "forbidden-dir", line.strip())
                )
                continue
            if FORBIDDEN_FILENAME_PATTERN.search(line):
                violations.append(
                    (path.relative_to(PLUGIN_ROOT), i, "forbidden-filename", line.strip())
                )
    return violations


def main() -> int:
    violations = scan()
    if violations:
        print("FAIL — .md deliverables found in plugin source:")
        print()
        for path, line_no, kind, line in violations:
            print(f"  {path}:{line_no}  ({kind})")
            print(f"    {line}")
            print()
        print(f"Total: {len(violations)} violation(s)")
        print()
        print("Rule 27 of shared/CONTRACT.md: polished outputs the user opens")
        print("to read MUST NOT be saved as .md. Word and Pages render .md")
        print("badly; customers report readability issues. See")
        print("references/MD_DELIVERABLE_POLICY.md for the full policy")
        print("(deliverable vs context/memory distinction).")
        print()
        print("Fix: change the file extension to .docx (or .pptx / .xlsx as")
        print("appropriate) and route the write through")
        print("shared/scripts/brief_writer.py if a template exists. Email")
        print("drafts should go to Gmail Drafts via Zapier, not saved as a")
        print("file at all.")
        return 1
    print("OK — no .md deliverables in skill / shared / reference surfaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
