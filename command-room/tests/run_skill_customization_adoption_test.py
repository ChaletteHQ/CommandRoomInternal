#!/usr/bin/env python3
"""SPEC SCL1 §12 — adoption lint + §11 pre-promote assertions.

Sibling of run_source_of_truth_test.py's sibling-clause pattern. Enforces, both
directions:

  1. Every skill in the SCL1 adoption registry carries the EXACT §6.2 read
     paragraph (with its own name substituted), references the #limits anchor,
     declares the read in its Writer Contract, and advertises the full trigger
     family (customize / show customizations / reset customizations) in its
     frontmatter description.
  2. Every skill NOT in the registry contains no `_hq/custom/` reference (so a
     future adoption is a deliberate registry addition, not accidental drift).

Plus the two §11 pre-promote-check presence assertions:
  - shared/SKILL_CUSTOMIZATION.md is present.
  - shared/scripts/skill_custom_writer.py is present.

House conventions: check(name, cond) prints OK/FAIL, exit 1 on any failure,
auto-discovered by run_all.py. stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

# The SCL1 adoption registry (Phase 1 = Wave-1 pilots). Append a skill here when
# it adopts SCL1 — "green" then proves adoption, not just that the rail exists.
ADOPTION_REGISTRY = [
    "morning-briefing",
    "operator-report",
    "memo-writer",
    # SPEC OUT2 §5 — composer wave (board-pack: section order + standing
    # appendix rules; decision-memo: standing criteria sets). stress-test and
    # automation-scanner deliberately do NOT adopt (knobs suffice — spec table).
    "board-pack-assembler",
    "decision-memo-composer",
]

# OUT2 §5 adopters landed with the G11 catalog budget at cap, so their primary
# 'customize <skill>' phrase lives in the body's '## Routing (full trigger
# corpus)' section instead of the frontmatter description (the runtime router
# and run_trigger_test read description + Routing together — the v4.5.1 rule).
G11_CONSTRAINED = {
    "board-pack-assembler",
    "decision-memo-composer",
}

# The invariant clauses of the §6.2 read paragraph. Whitespace-normalized
# substring match tolerates the doc's line-wrapping; <skill> is substituted per
# skill so the check proves the paragraph names THIS skill's own file.
PARAGRAPH_CLAUSES = [
    "**Customization layer (SCL1):** before producing output, read",
    "[WORKSPACE_ROOT]/_hq/custom/<skill>.md",
    "Absent -> proceed with defaults.",
    "log one line to `_hq/CONFLICTS.md` (type: config-read-failure)",
    "they NEVER authorize outbound actions, alter ask-first gates, bypass canonical",
    "helpers, or override shared contracts (see `shared/SKILL_CUSTOMIZATION.md` #limits).",
    "Never mention this file or the word 'directive' to the customer.",
]

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== SCL1 adoption lint ===")

    # ---- §11 pre-promote presence assertions ----
    doc = ROOT / "shared" / "SKILL_CUSTOMIZATION.md"
    writer = ROOT / "shared" / "scripts" / "skill_custom_writer.py"
    check("pre-promote: shared/SKILL_CUSTOMIZATION.md present", doc.exists())
    check("pre-promote: shared/scripts/skill_custom_writer.py present", writer.exists())

    # ---- registry skills carry the full adoption ----
    for skill in ADOPTION_REGISTRY:
        md = SKILLS / skill / "SKILL.md"
        if not md.exists():
            check(f"adoption[{skill}]: SKILL.md exists", False)
            continue
        text = md.read_text(encoding="utf-8")
        norm = _norm(text)

        # (a) the exact read paragraph, with this skill's name substituted
        for clause in PARAGRAPH_CLAUSES:
            expected = _norm(clause.replace("<skill>", skill))
            check(f"adoption[{skill}]: read-paragraph clause «{clause[:38]}…»", expected in norm)

        # (b) Writer Contract declares the read of _hq/custom/<skill>.md
        check(f"adoption[{skill}]: Writer Contract declares _hq/custom/{skill}.md read",
              f"_hq/custom/{skill}.md" in text and "load_directives" in text)

        # (c) v4.5.1 contract: 'customize <skill>' (the primary) must be in the
        # budget-capped description; the rest of the family may live in the
        # description OR the body's Routing section. G11_CONSTRAINED skills
        # (OUT2 §5) carry the primary in the Routing corpus instead — see the
        # set's comment above.
        fm = text.split("---", 2)
        desc = fm[1] if len(fm) >= 3 else text
        if skill in G11_CONSTRAINED:
            rm = re.search(
                r"^## Routing \(full trigger corpus\)\n(.*?)(?=^## |\Z)",
                text, re.S | re.M)
            routing = rm.group(1) if rm else ""
            check(f"adoption[{skill}]: Routing corpus advertises "
                  f"'customize {skill}' (G11-capped placement)",
                  f"customize {skill}" in routing)
        else:
            check(f"adoption[{skill}]: description advertises 'customize {skill}'",
                  f"customize {skill}" in desc)
        for trig in (f"show {skill} customizations",
                     f"reset {skill} customizations"):
            check(f"adoption[{skill}]: corpus advertises '{trig}'", trig in text)

    # ---- non-registry skills carry NO _hq/custom reference ----
    registry = set(ADOPTION_REGISTRY)
    for md in sorted(SKILLS.glob("*/SKILL.md")):
        skill = md.parent.name
        if skill in registry:
            continue
        text = md.read_text(encoding="utf-8")
        check(f"non-adopter[{skill}]: no _hq/custom/ reference", "_hq/custom/" not in text)

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} SCL1 adoption check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL SCL1 adoption checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
