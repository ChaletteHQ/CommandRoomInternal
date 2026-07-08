#!/usr/bin/env python3
"""SPEC CON1 — voice-block coverage guard. VOICE_CALIBRATION.md requires every
CEO-facing composer to bake in a Voice Block + a `voice_block_last_refreshed`
frontmatter date. This asserts that, and catches a NEW composer that adds the
frontmatter field without being registered here (or being explicitly exempted).

House conventions: check(name, cond) prints OK/FAIL, exit 1 on any failure,
auto-discovered by run_all.py.

Negative check (manual, per acceptance): delete email-writer's '## Voice Block'
section and this test fails.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

# Register-specific composers that DEFINE their own `## Voice Block` section.
REQUIRED_SECTION = [
    "email-writer",
    "memo-writer",
    "one-pager-composer",
    "follow-up-ritual",
]

# Structured-document composers that calibrate from the shared "docs and deliverables"
# register (BRAND_VOICE / VOICE_CALIBRATION) + the mechanical B2 voice-tell gate, rather
# than a per-skill Voice Block section. They still carry the frontmatter date and MUST
# cite VOICE_CALIBRATION.md + run the voice-tell gate.
REQUIRED_CALIBRATED = [
    "decision-memo-composer",
    "board-pack-assembler",
]

REQUIRED = REQUIRED_SECTION + REQUIRED_CALIBRATED

# Writing-adjacent skills that DELEGATE drafting to a composer above, so they do
# NOT own their own Voice Block. Each carries a documented reason here.
EXEMPT = {
    "inbox-triage": "drafts replies via the email-writer chain — no own Voice Block",
    "intro-broker": "chains to email-writer for the actual draft/send",
    "relationship-moves": "drafts openers via the email-writer chain (SPEC REL1 D6)",
    "calendar-writer": "writes calendar event text, not CEO-facing prose composition",
}

_FM_RE = re.compile(r"^voice_block_last_refreshed:\s*\S", re.MULTILINE)
_VB_RE = re.compile(r"(^|\n)#{1,4}\s*Voice Block\b", re.IGNORECASE)
# Writing-verb heuristic for the "new composer" net.
_WRITE_DESC_RE = re.compile(r"\b(draft|compose|write|writing)\b", re.IGNORECASE)

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[:end]
    return text[:1000]


def main() -> int:
    for name in REQUIRED:
        p = SKILLS / name / "SKILL.md"
        if not p.exists():
            check(f"{name}: SKILL.md exists", False)
            continue
        text = p.read_text(encoding="utf-8")
        check(f"{name}: has voice_block_last_refreshed frontmatter",
              bool(_FM_RE.search(_frontmatter(text))))
        if name in REQUIRED_SECTION:
            check(f"{name}: has a Voice Block section", bool(_VB_RE.search(text)))
        else:
            # Calibrated-from-shared-register composer: must cite VOICE_CALIBRATION.md
            # AND run the mechanical voice-tell gate (so voice IS enforced, just not via
            # a per-skill block).
            check(f"{name}: cites VOICE_CALIBRATION.md + runs the voice-tell gate",
                  "VOICE_CALIBRATION.md" in text and "voice-tell" in text.lower())

    # Coverage net: any skill carrying the voice_block_last_refreshed field MUST be a
    # registered composer (REQUIRED). A new composer that adds the field without being
    # listed here trips this — the intended "new skill is listed or exempted" guard.
    known = set(REQUIRED) | set(EXEMPT)
    stray: list[str] = []
    write_unregistered: list[str] = []
    for p in sorted(SKILLS.glob("*/SKILL.md")):
        name = p.parent.name
        text = p.read_text(encoding="utf-8")
        if _FM_RE.search(_frontmatter(text)) and name not in REQUIRED:
            stray.append(name)
        # A writing-verb description that isn't registered anywhere is worth a look,
        # but only flag if it ALSO has the frontmatter field (strong composer signal)
        # — avoids flagging every skill whose description merely says "write".
    check("no stray composer carries the frontmatter field unregistered", not stray)
    if stray:
        print(f"     stray: {', '.join(stray)} — add to REQUIRED or EXEMPT")

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} voice-block coverage check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL voice-block coverage checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
