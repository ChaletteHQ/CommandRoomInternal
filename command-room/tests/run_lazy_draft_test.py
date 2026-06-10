#!/usr/bin/env python3
"""
Structural guard: email-draft skills follow LAZY Gmail-draft creation.

The rule (shared/EMAIL_DRAFT_PROTOCOL.md §1):

  Do NOT create Gmail drafts at fire time. Generate draft TEXT only and
  surface it in the editable chat-action widget. A Gmail draft is
  persisted ONLY when the user explicitly clicks `send` / `edit then
  send` / `draft`. `skip` makes NO Gmail call. This is the v3.13.7
  trust-bomb fix: the pre-v3.13.7 EAGER model created drafts in the
  user's Gmail before they approved anything (a day-1 churn signal a
  paying customer hit).

The deep audit (2026-05-29) flagged lazy-vs-eager as the single
most-exposed ADVISORY gate — declared in prose, enforced by NOTHING. The
prose-contract investigation (2026-05-30) then found inbox-triage +
follow-up-ritual still carried the retired eager-model phrasing as of
v3.15.0. This guard closes that gap two ways:

  1. POSITIVE — every on-demand email-emitting skill references
     EMAIL_DRAFT_PROTOCOL.md (the doc that defines lazy creation).

  2. NEGATIVE — no skill/reference prose carries the specific EAGER-model
     phrasings that mean "a Gmail draft already exists at fire time"
     (`eager-created`, `(eager creation)`, `already in Gmail Drafts
     (eager`, the old `"Saved as a Gmail draft"` context_tag).

Exempt: this test, CHANGELOG.md (historical audit trail), and
EMAIL_DRAFT_PROTOCOL.md (it EXPLAINS the retired eager model in past
tense). Note the NEGATIVE patterns are deliberately specific so the
legitimate past-tense explanations elsewhere — email-writer's "the
eager-Gmail-draft model is reversed in v3.13.7", the protocol's "created
drafts eagerly at fire time" — do NOT match.

Companion to shared/EMAIL_DRAFT_PROTOCOL.md §1.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# On-demand skills that emit a recipient-bound draft (EMAIL_DRAFT_PROTOCOL §15-21).
EMAIL_EMITTING_SKILLS = [
    "email-writer",
    "intro-broker",
    "follow-up-ritual",
    "thread-resurrection",
    "inbox-triage",
]
PROTOCOL_REF = "EMAIL_DRAFT_PROTOCOL"

# EAGER-model regression phrasings — current-tense "a Gmail draft already
# exists at fire time". Deliberately specific so past-tense historical
# explanations ("the eager model", "created drafts eagerly", "the
# eager-Gmail-draft model is reversed") do NOT match.
FORBIDDEN_EAGER_PATTERNS = [
    re.compile(r"eager-created"),
    re.compile(r"\(eager creation\)"),
    re.compile(r"already in Gmail Drafts \(eager"),
    re.compile(r'"Saved as a Gmail draft"'),
]

EXEMPT_FILES = {
    "run_lazy_draft_test.py",
    "CHANGELOG.md",
    "EMAIL_DRAFT_PROTOCOL.md",
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


def scan_eager() -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    for path in _iter_scan_paths():
        if not path.is_file() or path.suffix not in SCAN_EXTENSIONS:
            continue
        if path.name in EXEMPT_FILES or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for pat in FORBIDDEN_EAGER_PATTERNS:
                if pat.search(line):
                    violations.append((path.relative_to(PLUGIN_ROOT), i, line.strip()))
                    break
    return violations


def check_protocol_refs() -> list[str]:
    missing: list[str] = []
    for skill in EMAIL_EMITTING_SKILLS:
        skill_md = PLUGIN_ROOT / "skills" / skill / "SKILL.md"
        if not skill_md.exists():
            missing.append(f"{skill} (SKILL.md not found)")
            continue
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        if PROTOCOL_REF not in text:
            missing.append(f"{skill} (no {PROTOCOL_REF} reference)")
    return missing


def main() -> int:
    failed = False

    eager = scan_eager()
    if eager:
        failed = True
        print("FAIL — eager Gmail-draft phrasing found (violates EMAIL_DRAFT_PROTOCOL §1 lazy creation):")
        print()
        for path, line_no, line in eager:
            print(f"  {path}:{line_no}")
            print(f"    {line}")
            print()
        print(f"  {len(eager)} eager-phrasing violation(s). Drafts must be created LAZILY —")
        print("  only on the user's send / draft / edit-then-send click, never at fire time.")
        print()

    missing = check_protocol_refs()
    if missing:
        failed = True
        print("FAIL — email-emitting skills missing the lazy-draft protocol reference:")
        for m in missing:
            print(f"    {m}")
        print()
        print("  Every recipient-bound-draft skill MUST reference shared/EMAIL_DRAFT_PROTOCOL.md.")
        print()

    if failed:
        return 1
    print(
        f"OK — {len(EMAIL_EMITTING_SKILLS)} email-emitting skills reference the lazy protocol; "
        "no eager-draft phrasing in skill / shared / reference surfaces"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
