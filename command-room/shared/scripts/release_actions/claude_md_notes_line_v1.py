#!/usr/bin/env python3
"""claude_md_notes_line_v1: rewrite a workspace CLAUDE.md's "Project notes"
line to the one session-notes name, once, without asking (cr1#1580 review
round 1, coordinator decision under the owner's ruling 1 of cr1#1579).

WHY
---
Onboarding wrote `- Project notes: [Project Name]/SESSION_NOTES_<first
name>.md` into every seat's CLAUDE.md from the old template. CLAUDE.md is the
one file every turn loads, including turns no skill owns, so on a live seat
that line keeps telling a freelance turn to write the owner's first-name file,
and a project's notes split across two files. Skills now ask
`session_notes_file` for the file; this line is the last place the old rule
lives. Fresh workspaces get the ruled line from
`references/claude-md-template.md`; this module back-fills existing installs
through the update bridge's Phase 4.5, through the WRITE door, under the
customer's writer id.

WHAT IT DOES
------------
  * Exactly one line that opens `- Project notes:` and names a session-notes
    file other than the ruled one -> that line's text becomes `RULED_LINE`;
    every other byte of the file, its line endings included, stays. Then ONE
    `workspace_migration_applied` row.
  * The ruled line is already there, or there is no such line, or there are
    several (a hand-edited file this module will not guess about) -> nothing.
  * The ledger already carries an applied or skipped row for the id ->
    nothing. An owner who puts the old line back is respected.

Additive in effect, reversible (edit the line back), no data loss. Signature
per references/RELEASE_MANIFEST.md "Action contract". The ruled line is
pinned byte-identical to the template's line
(tests/run_session_notes_name_test.py, section I).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from release_actions import claude_md_rule_append as _base  # noqa: E402

SOURCE_SKILL = "command-room-update-bridge"
MIGRATION_ID = "claude_md_notes_line_v1"

#: The template's line, byte for byte.
RULED_LINE = "- Project notes: [Project folder]/SESSION_NOTES_<folder name>.md"

#: A "Project notes" line naming a session-notes file, any spelling.
_NOTES_LINE_RE = re.compile(r"^- Project notes:.*SESSION_NOTES.*\.md\s*$")

TARGET_REL = _base.TARGET_REL
APPLIED_EVENT = _base.APPLIED_EVENT
STATUS_PENDING = _base.STATUS_PENDING
STATUS_ALREADY_PRESENT = _base.STATUS_ALREADY_PRESENT
STATUS_ADJUDICATED = _base.STATUS_ADJUDICATED
STATUS_NO_FILE = _base.STATUS_NO_FILE
STATUS_NO_LINE = "no_notes_line"
STATUS_SEVERAL = "several_notes_lines"


def _old_lines(text: str) -> List[int]:
    """Indexes (in `splitlines(keepends=True)`) of the lines to rewrite."""
    out = []
    for i, raw in enumerate(text.splitlines(keepends=True)):
        line = raw.rstrip("\r\n")
        if line != RULED_LINE and _NOTES_LINE_RE.match(line):
            out.append(i)
    return out


def plan(workspace_root) -> Dict[str, Any]:
    """What a run would do, and why. A pure read: nothing is written."""
    target = Path(str(workspace_root)) / TARGET_REL
    out: Dict[str, Any] = {"migration_id": MIGRATION_ID, "target_file": TARGET_REL,
                           "status": STATUS_PENDING, "reason": ""}
    if not target.is_file():
        out.update({"status": STATUS_NO_FILE,
                    "reason": "the workspace has no CLAUDE.md"})
        return out
    text = target.read_bytes().decode("utf-8")
    old = _old_lines(text)
    if not old:
        present = RULED_LINE in text.splitlines()
        out.update({"status": STATUS_ALREADY_PRESENT if present else STATUS_NO_LINE,
                    "reason": ("the ruled line is already in CLAUDE.md" if present
                               else "CLAUDE.md names no session-notes file")})
        return out
    if len(old) > 1:
        out.update({"status": STATUS_SEVERAL,
                    "reason": "CLAUDE.md has more than one notes line; left as it is"})
        return out
    if _base._adjudicated(workspace_root, MIGRATION_ID):
        out.update({"status": STATUS_ADJUDICATED,
                    "reason": "a prior run applied or skipped this migration; "
                              "a line the owner put back is respected"})
    return out


def render_rewritten(text: str) -> str:
    """`text` with its one old notes line replaced by `RULED_LINE`, that
    line's own ending kept. Pure; unchanged when there is not exactly one."""
    lines = text.splitlines(keepends=True)
    old = _old_lines(text)
    if len(old) != 1:
        return text
    raw = lines[old[0]]
    ending = raw[len(raw.rstrip("\r\n")):]
    lines[old[0]] = RULED_LINE + ending
    return "".join(lines)


def claude_md_notes_line_v1(events_jsonl_path, workspace_root,
                            detector_context: Optional[Dict[str, Any]] = None
                            ) -> Dict[str, Any]:
    """The action: rewrite the one line once, log once, or do nothing and say
    why."""
    ctx = dict(detector_context or {})
    try:
        from receipts import require_writer_identity

        require_writer_identity(workspace_root=workspace_root)
    except ImportError:
        pass
    planned = plan(workspace_root)
    context: Dict[str, Any] = {"migration_id": MIGRATION_ID, "status": planned["status"],
                               "target_file": TARGET_REL, "report": planned["reason"]}
    if planned["status"] != STATUS_PENDING:
        return {"success": True, "ran": False, "context": context, "error": None,
                "fallback_prompt": None}
    target = Path(str(workspace_root)) / TARGET_REL
    before = target.read_bytes().decode("utf-8")
    after = render_rewritten(before)
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    if (after == before or len(changed) != 1 or changed[0][1] != RULED_LINE
            or len(before.splitlines()) != len(after.splitlines())):
        return {"success": False, "ran": False, "context": context,
                "error": "the rewrite would not change exactly the one notes line",
                "fallback_prompt": None}
    from atomic_write import atomic_write_text

    atomic_write_text(target, after)
    if RULED_LINE not in target.read_bytes().decode("utf-8").splitlines():
        return {"success": False, "ran": False, "context": context,
                "error": "CLAUDE.md was written but the ruled line is not in it",
                "fallback_prompt": None}
    from event_gate import append_event

    row = {"type": APPLIED_EVENT, "source_skill": SOURCE_SKILL,
           "data": {"migration_id": MIGRATION_ID, "target_file": TARGET_REL,
                    "from_version": ctx.get("from_version"),
                    "to_version": ctx.get("to_version"),
                    "actor": SOURCE_SKILL}}
    events_path = (Path(str(events_jsonl_path)) if events_jsonl_path
                   else Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl")
    stamped = append_event(events_path, [row], holder=SOURCE_SKILL)
    context.update({"status": "applied",
                    "event_seq": (stamped[0].get("seq") if stamped else None)})
    return {"success": True, "ran": True, "context": context, "error": None,
            "fallback_prompt": None}


__all__ = [
    "APPLIED_EVENT", "MIGRATION_ID", "RULED_LINE", "SOURCE_SKILL",
    "STATUS_ADJUDICATED", "STATUS_ALREADY_PRESENT", "STATUS_NO_FILE",
    "STATUS_NO_LINE", "STATUS_PENDING", "STATUS_SEVERAL", "TARGET_REL",
    "claude_md_notes_line_v1", "plan", "render_rewritten",
]
