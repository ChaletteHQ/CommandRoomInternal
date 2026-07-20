#!/usr/bin/env python3
"""
Regression guard for the W5 waiting-on surface (Phase 4 rider, 2026-07-02).

WHAT SHIPPED
------------
The reliability spec's W5 `waiting-on chase` — held gated through Phase 3 on
the commitments kinds split (Stage D) and counterparty receipts (Stage E),
both now merged — is enabled as orchestrator-commitments Phase 3.8: a
Tue/Thu-only WAITING ON section for owed-to-you items with a prior outbound
touch ≥3 weekdays old and no reply, one-tap nudge drafts via email-writer.
It rides the existing commitments task; nothing new registers.

WHAT THIS GUARD ASSERTS
-----------------------
1. Phase 3.8 exists in the orchestrator with the Tue/Thu gate, the
   substrate-only qualification, the no-double-surfacing rule, and the
   outreach_sent-based baseline.
2. Every action the section prescribes is already in CANONICAL_ACTIONS —
   W5 must not smuggle in verbs the renderer rejects (the P1.1 bug class).
3. The two gate-documentation surfaces (system-health SKILL.md,
   task_watchdog.py) no longer describe waiting-on as gated/unshipped —
   stale sediment next to its replacement is the #1 audit root cause.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "shared" / "scripts"))

from chat_output_renderer import CANONICAL_ACTIONS  # noqa: E402

ORCH = (
    PLUGIN_ROOT
    / "skills"
    / "enable-command-room-schedules"
    / "references"
    / "orchestrator-commitments.md"
)
SYS_HEALTH = PLUGIN_ROOT / "skills" / "system-health" / "SKILL.md"
WATCHDOG = PLUGIN_ROOT / "shared" / "scripts" / "task_watchdog.py"

failures = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


orch = ORCH.read_text(encoding="utf-8")

# CTS1 §4.1: the whole chat is named "Waiting On" now, so the Phase 3.8
# section was mandatorily renamed to "NUDGED — NO REPLY" (one phrase must not
# mean two things in one product). The section itself — the W5 quiet chased
# tail — is unchanged.
check("Phase 3.8 — NUDGED — NO REPLY" in orch, "Phase 3.8 NUDGED — NO REPLY section missing")
check("⏳ WAITING ON" not in orch,
      "a section titled WAITING ON survives inside the Waiting On chat (CTS1 §4.1 collision)")
check(
    "machine-local weekday is Tuesday or Thursday" in orch,
    "Tue/Thu machine-local gate missing",
)
check(
    "substrate-only" in orch and "outreach_sent" in orch,
    "substrate-only qualification / outreach_sent baseline missing",
)
check(
    "≥ 3 weekdays old" in orch,
    "3-weekday no-reply baseline missing",
)
check(
    "NOT already rendering as an actionable row" in orch,
    "no-double-surfacing rule missing",
)
check(
    "never add them to the header numbers twice" in orch,
    "count_commitments double-count guard missing",
)

# W5 actions must all be canonical — no smuggled verbs.
w5_actions = ["send", "draft", "mark received", "snooze 3d"]
for a in w5_actions:
    check(
        a in CANONICAL_ACTIONS,
        f"WAITING ON action {a!r} is not in CANONICAL_ACTIONS — renderer would reject it",
    )

# Gate documentation updated, not annotated (sediment rule).
sh = SYS_HEALTH.read_text(encoding="utf-8")
check(
    "waiting-on chase` pack (gated" not in sh,
    "system-health still documents waiting-on chase as gated",
)
check("Phase 3.8" in sh, "system-health does not point at the shipped Phase 3.8 surface")

wd = WATCHDOG.read_text(encoding="utf-8")
check(
    "gated on commitment-task-split" not in wd,
    "task_watchdog still documents waiting-on chase as gated",
)
check("Phase 3.8" in wd, "task_watchdog does not point at the shipped Phase 3.8 surface")

if failures:
    print(f"FAIL {len(failures)} of {checks} checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print(f"OK {checks} tests passed")
