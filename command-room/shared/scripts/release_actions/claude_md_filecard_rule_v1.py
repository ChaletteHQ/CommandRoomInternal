#!/usr/bin/env python3
"""claude_md_filecard_rule_v1: append the file-card rule to a workspace's
CLAUDE.md, once, without asking (FILECARD1, cr1#610; owner ruling 2026-11-03).

WHY
---
The owner wrote the rule into his own rules file on 2026-10-02: every file made
for him is saved in the folder and comes back in the chat as a clickable file
card, never as a path in words. On 2026-10-24 it existed nowhere else: not in
`references/claude-md-template.md`, not in any migration, so no client seat had
it, and on 2026-11-03 a call prep in the owner's own chat came back as a path
in words with no card (cr1#268). CONTRACT Rule 3 now carries the product's
half; this line is the seat's half, the one surface unconditionally in context
that wins the routing question (the Bug #104 / EW1 precedent).

Fresh workspaces get the line from `references/claude-md-template.md`. This
module back-fills every existing install: the update bridge (Phase 4.5) runs it
through the WRITE door (`plan run_writer`), beside the customer's files, under
the customer's writer id. "update command room" stays the trigger; nothing
applies itself (owner ruling 2026-11-03).

The mechanics live in `claude_md_rule_append`; this module is the constants
and the two entry points the bridge and the tests name. The template bullet
and the bridge's quoted block are pinned BYTE-IDENTICAL to `RULE_LINE`
(tests/run_filecard_rule_migration_test.py): one sentence, three carriers.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from release_actions import claude_md_rule_append as _base  # noqa: E402

SOURCE_SKILL = "command-room-update-bridge"
MIGRATION_ID = "claude_md_filecard_rule_v1"

#: The idempotency phrase. It lives inside `RULE_LINE`; a CLAUDE.md that
#: carries it anywhere reads as applied.
MARKER = "comes back in this chat as a clickable file card"

#: THE rule, in the customer's voice (CLAUDE.md speaks as the customer). The
#: template's Session Rules bullet and the bridge's quoted block are this
#: string, byte for byte.
RULE_LINE = (
    "- IMPORTANT: Every file you make for me — a document, a sheet, a deck "
    "or a note, Markdown included — is saved in my Command Room folder and "
    "comes back in this chat as a clickable file card, first, before the "
    "summary, because a path in words is not delivery; only when this chat "
    "has no file card does a link stand in for it, with one sentence saying "
    "so."
)

SESSION_RULES_HEADING = _base.SESSION_RULES_HEADING
TARGET_REL = _base.TARGET_REL
APPLIED_EVENT = _base.APPLIED_EVENT
STATUS_PENDING = _base.STATUS_PENDING
STATUS_ALREADY_PRESENT = _base.STATUS_ALREADY_PRESENT
STATUS_ADJUDICATED = _base.STATUS_ADJUDICATED
STATUS_NO_FILE = _base.STATUS_NO_FILE


def plan(workspace_root) -> Dict[str, Any]:
    """What a run would do, and why. A pure read: nothing is written."""
    return _base.plan_line(workspace_root, migration_id=MIGRATION_ID, marker=MARKER)


def render_appended(text: str) -> str:
    """`text` with `RULE_LINE` appended once, under `## Session Rules`."""
    return _base.render_appended(text, line=RULE_LINE, marker=MARKER)


def claude_md_filecard_rule_v1(events_jsonl_path, workspace_root,
                               detector_context: Optional[Dict[str, Any]] = None
                               ) -> Dict[str, Any]:
    """The action: append once, log once, or do nothing and say why."""
    return _base.apply_line(events_jsonl_path, workspace_root, detector_context,
                            migration_id=MIGRATION_ID, marker=MARKER, line=RULE_LINE,
                            source_skill=SOURCE_SKILL)


__all__ = [
    "APPLIED_EVENT", "MARKER", "MIGRATION_ID", "RULE_LINE", "SESSION_RULES_HEADING",
    "SOURCE_SKILL", "STATUS_ADJUDICATED", "STATUS_ALREADY_PRESENT", "STATUS_NO_FILE",
    "STATUS_PENDING", "TARGET_REL", "claude_md_filecard_rule_v1", "plan", "render_appended",
]
