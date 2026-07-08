#!/usr/bin/env python3
"""
Regression guard for P0.8 (Phase 4 trust patch, 2026-07-02): the
command-room-update-bridge canonical-edit-surface migration must never
re-plant retired source-of-truth instructions into workspace docs.

THE BUG CLASS THIS CATCHES
--------------------------
The `canonical_edit_surface_*` migrations (Phase 4.5) surface the contents of
`skills/command-room-update-bridge/references/canonical_edit_surface_for_*.md`
as copy-paste replacement blocks for the user's CLAUDE.md and
_hq/INFRASTRUCTURE.md. Through v4.4.0 those reference files still declared the
retired Option B model (`~/.claude/plugins/marketplaces/commandroom1/` as
canonical) — so a firing migration instructed the user to paste retired
instructions into their workspace docs. An agent following the planted doc
then builds against a dead-end clone (this actually happened 2026-06-22).

WHAT THIS GUARD ASSERTS
-----------------------
1. Both replacement reference files name the cr1 model: the
   `~/repos/cr1-canonical/command-room/` edit surface and the
   `ChaletteHQ/cr1` staging repo.
2. Neither replacement file contains a stale marker (`commandroom1`,
   `plugin-source-v3`) — otherwise a freshly-migrated workspace file would
   immediately re-flag as pending (infinite migration loop).
3. The update-bridge SKILL.md migration entries carry BOTH stale markers, so
   files "fixed" to the retired Option B text in an earlier migration re-flag.
4. `references/HOW_COMMAND_ROOM_WORKS.md` (the in-plugin orientation doc the
   replacement blocks cite) does not re-declare a marketplace clone canonical.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_REFS = PLUGIN_ROOT / "skills" / "command-room-update-bridge" / "references"
BRIDGE_SKILL = PLUGIN_ROOT / "skills" / "command-room-update-bridge" / "SKILL.md"
HOW_IT_WORKS = PLUGIN_ROOT / "references" / "HOW_COMMAND_ROOM_WORKS.md"

REPLACEMENT_FILES = [
    BRIDGE_REFS / "canonical_edit_surface_for_claude_md.md",
    BRIDGE_REFS / "canonical_edit_surface_for_infrastructure_md.md",
]

STALE_MARKERS = ["commandroom1", "plugin-source-v3"]
CANONICAL_TOKENS = ["~/repos/cr1-canonical/command-room/", "ChaletteHQ/cr1"]

failures = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


for ref in REPLACEMENT_FILES:
    check(ref.exists(), f"{ref.name}: file missing")
    if not ref.exists():
        continue
    text = ref.read_text(encoding="utf-8")
    for token in CANONICAL_TOKENS:
        check(token in text, f"{ref.name}: canonical token {token!r} missing")
    for marker in STALE_MARKERS:
        check(
            marker not in text,
            f"{ref.name}: contains stale marker {marker!r} — a migrated file "
            "would immediately re-flag as pending",
        )
    check(
        "IS the canonical edit surface for the Command Room plugin.** Path: `~/.claude"
        not in text,
        f"{ref.name}: still declares a marketplace clone canonical (Option B text)",
    )

skill_text = BRIDGE_SKILL.read_text(encoding="utf-8")
check(
    skill_text.count('markers: ["plugin-source-v3", "commandroom1"]') == 2,
    "update-bridge SKILL.md: expected BOTH canonical_edit_surface migration "
    "entries to carry the two-marker list "
    '`markers: ["plugin-source-v3", "commandroom1"]`',
)

how_text = HOW_IT_WORKS.read_text(encoding="utf-8")
check(
    "marketplaces/commandroom1" not in how_text,
    "HOW_COMMAND_ROOM_WORKS.md: still points at the retired marketplace clone",
)
check(
    "~/repos/cr1-canonical/command-room/" in how_text,
    "HOW_COMMAND_ROOM_WORKS.md: cr1 canonical edit surface not documented",
)

if failures:
    print(f"FAIL {len(failures)} of {checks} checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print(f"OK {checks} tests passed")
