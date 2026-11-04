#!/usr/bin/env python3
"""claude_md_nomd_rule_v1: append the no-Markdown rule to a workspace's
CLAUDE.md, once, without asking (FILECARD1, cr1#610; owner ruling 2026-11-03).

WHY
---
`deliverables.land` refuses a `.md` as a deliverable and CONTRACT Rule 27
forbids one, so a Markdown deliverable can only be a freelance write: a turn
that no skill owns, writing a document by hand. Nothing in the rules file told
such a turn the format, and clients opened raw Markdown in Word. This line
tells it: a document about the owner's work is a Word, PDF, spreadsheet or
deck file, never a Markdown text file. It sits beside the file-card line
(`claude_md_filecard_rule_v1`) as the second of the two lines cr1#610 asks
for, each its own migration and its own applied row, so a customer who deletes
one keeps the other.

Fresh workspaces get the line from `references/claude-md-template.md`. This
module back-fills every existing install through the update bridge's Phase 4.5,
through the WRITE door, under the customer's writer id. "update command room"
stays the trigger; nothing applies itself (owner ruling 2026-11-03).

The mechanics live in `claude_md_rule_append`. The template bullet and the
bridge's quoted block are pinned BYTE-IDENTICAL to `RULE_LINE`
(tests/run_filecard_rule_migration_test.py).
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
MIGRATION_ID = "claude_md_nomd_rule_v1"

#: The idempotency phrase. It lives inside `RULE_LINE`.
MARKER = "never a Markdown text file"

#: THE rule, in the customer's voice. Template bullet and bridge block are this
#: string, byte for byte.
RULE_LINE = (
    "- IMPORTANT: A document about my work that I will read or forward — a "
    "prep, memo, one-pager, recap or report — is a Word, PDF, spreadsheet "
    "or deck file in my Command Room folder, never a Markdown text file, "
    "because Markdown opened in Word shows raw markup instead of a "
    "document; Markdown is only for the working notes Command Room keeps "
    "for itself."
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


def claude_md_nomd_rule_v1(events_jsonl_path, workspace_root,
                           detector_context: Optional[Dict[str, Any]] = None
                           ) -> Dict[str, Any]:
    """The action: append once, log once, or do nothing and say why."""
    return _base.apply_line(events_jsonl_path, workspace_root, detector_context,
                            migration_id=MIGRATION_ID, marker=MARKER, line=RULE_LINE,
                            source_skill=SOURCE_SKILL)


__all__ = [
    "APPLIED_EVENT", "MARKER", "MIGRATION_ID", "RULE_LINE", "SESSION_RULES_HEADING",
    "SOURCE_SKILL", "STATUS_ADJUDICATED", "STATUS_ALREADY_PRESENT", "STATUS_NO_FILE",
    "STATUS_PENDING", "TARGET_REL", "claude_md_nomd_rule_v1", "plan", "render_appended",
]
