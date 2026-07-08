#!/usr/bin/env python3
"""
Regression guard for P0.7 (Phase 4 trust patch, 2026-07-02): morning-brief's
`mark done [n]` must have a receiving route in apply-choices.

THE BUG CLASS THIS CATCHES
--------------------------
morning-briefing advertised a one-tap `mark done [n]` on every Needs
Attention item, "routing to apply-choices' existing commitment_resolved
writer" — but apply-choices Step 2's closed source-orchestrator registry had
no morning-brief entry, so every mark-done landed in "Couldn't tell which
task this belongs to." The advertised affordance was structurally dead.
Merged with the Audit B finding: Step 5 still prescribed Slack-DM/email
scheduled delivery — a model following it literally posts the digest to a
surface where mark-done can't work at all.

WHAT THIS GUARD ASSERTS
-----------------------
1. apply-choices Step 2 registry has a `morning-brief` source entry that
   closes via commitment_state.close_commitment and resolves [n] against
   the pack_run's needs_attention_ids.
2. morning-briefing + its orchestrator record needs_attention_ids on the
   pack_run receipt (the id mapping the route depends on).
3. Scheduled delivery defers to the Morning Brief chat post; Slack DM is
   no longer the default channel; the 2000-char Slack cap is gone.
4. The apply-choices description no longer undercounts the orchestrator set
   at 5.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
APPLY = PLUGIN_ROOT / "skills" / "apply-choices" / "SKILL.md"
BRIEF = PLUGIN_ROOT / "skills" / "morning-briefing" / "SKILL.md"
ORCH = (
    PLUGIN_ROOT
    / "skills"
    / "enable-command-room-schedules"
    / "references"
    / "orchestrator-morning-brief.md"
)

failures = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


apply_text = APPLY.read_text(encoding="utf-8")
brief_text = BRIEF.read_text(encoding="utf-8")
orch_text = ORCH.read_text(encoding="utf-8")

# 1. Registry entry exists and uses the canonical closure path
check(
    "- `morning-brief` (" in apply_text,
    "apply-choices Step 2 registry has no morning-brief source entry — mark done has no route",
)
mb_entry = apply_text.split("- `morning-brief` (", 1)[-1].split("\n", 1)[0]
check(
    "close_commitment" in mb_entry,
    "morning-brief entry does not close via commitment_state.close_commitment",
)
check(
    "needs_attention_ids" in mb_entry,
    "morning-brief entry does not resolve [n] against needs_attention_ids",
)

# 2. The id mapping is recorded at fire time
check(
    "needs_attention_ids" in brief_text,
    "morning-briefing SKILL.md does not record needs_attention_ids on the pack_run",
)
check(
    "needs_attention_ids" in orch_text,
    "orchestrator-morning-brief.md pack_run example lacks needs_attention_ids",
)

# 3. Slack-DM delivery retired as default
check(
    "If Slack is connected and preferred (default)" not in brief_text,
    "Step 5 still prescribes Slack DM as the default scheduled delivery",
)
check(
    "2000 characters" not in brief_text,
    "Step 5 still carries the Slack 2000-char cap",
)
check(
    "The Morning Brief chat IS the surface" in brief_text,
    "Step 5 scheduled mode does not defer to the Morning Brief chat post",
)

# 4. Description count
check(
    "all 5 CR scheduled-task orchestrators" not in apply_text,
    "apply-choices description still says 'all 5 CR scheduled-task orchestrators'",
)

if failures:
    print(f"FAIL {len(failures)} of {checks} checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print(f"OK {checks} tests passed")
