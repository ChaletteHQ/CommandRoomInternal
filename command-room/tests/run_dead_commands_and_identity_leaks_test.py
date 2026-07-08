#!/usr/bin/env python3
"""
Regression guard for P0.9, P0.11, P0.12 (Phase 4 trust patch, 2026-07-02).

THE BUG CLASSES THIS CATCHES
----------------------------
P0.9  — hardcoded counts in verbatim customer copy that drift when the
        referenced surface changes ("Five quick questions" vs the
        4-question v5 setup widget).
P0.11 — dead commands: customer-advertised phrases that resolve to no
        trigger anywhere in the plugin ("the most trust-destroying
        customer-facing bug class" — precursor to guard G6).
P0.12 — operator/real-name identity leaks in customer-visible normative
        prose and templates (precursor to guard G9's sweep).

Each check pins one verified instance from the 2026-07-01 merged audit so
the exact bug shape can't re-ship. The general classes get plugin-wide
guards in Phase P3 (G6/G7/G9); this test is the P0 down-payment.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILLS = PLUGIN_ROOT / "skills"

failures = []
checks = 0


def check_absent(rel_path: str, needle: str, why: str) -> None:
    global checks
    checks += 1
    text = (SKILLS / rel_path).read_text(encoding="utf-8")
    if needle in text:
        failures.append(f"{rel_path}: contains {needle!r} — {why}")


# P0.9 — hardcoded widget-question count
check_absent(
    "command-room-onboarding/SKILL.md",
    "Five quick questions",
    "hardcoded count vs the 4-question v5 widget; render no count (G7 class)",
)

# P0.11 — dead advertised commands
check_absent(
    "weekly-recap/SKILL.md",
    "`update [name]`",
    "no such trigger exists; the real command is 'tell me about [name]'",
)
check_absent(
    "workspace-manager/SKILL.md",
    "set schedule timezone to",
    "advertised command exists nowhere (and schedule moves are banned here)",
)
check_absent(
    "workspace-manager/SKILL.md",
    "'hire',",
    "dead trigger — no handler anywhere in the body",
)
check_absent(
    "command-room-update-bridge/SKILL.md",
    "recheck my org classifications",
    "routes to a skill that doesn't exist",
)
check_absent(
    "command-room-update-bridge/SKILL.md",
    "Three optional add-ons",
    "hardcoded promise against an empty ADDONS set; render from the set",
)
check_absent(
    "decision-log/SKILL.md",
    "workshop mode",
    "phantom mode — nothing implements it; point at decision-memo-composer / stress-test",
)

# P0.12 — identity leaks in customer-visible prose/templates
check_absent(
    "automation-scanner/SKILL.md",
    "ask M to do",
    "operator name in normative prose; say 'the user'",
)
check_absent(
    "enable-quick-commands/SKILL.md",
    "Bo, Quinn",
    "real names in shipped prose; use role placeholders",
)
check_absent(
    "enable-quick-commands/SKILL.md",
    "when M clicks",
    "operator name in normative prose; say 'the user'",
)
check_absent(
    "advisor-export/SKILL.md",
    "Show M the draft",
    "operator name in normative prose; say 'the user'",
)
check_absent(
    "level-up-command-room/SKILL.md",
    "M visited",
    "operator name in normative prose; say 'the user'",
)
check_absent(
    "board-pack-assembler/SKILL.md",
    "CHALETTE HOLDINGS",
    "real org in the customer-rendered template header; use ACME CO",
)

if failures:
    print(f"FAIL {len(failures)} of {checks} checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print(f"OK {checks} tests passed")
