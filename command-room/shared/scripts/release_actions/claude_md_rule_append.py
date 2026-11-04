#!/usr/bin/env python3
"""The one way a rule line is appended to a workspace's CLAUDE.md, once,
without asking (FILECARD1, cr1#610; the shape `claude_md_docs_rule_v1` set on
2026-09-24 and this module lifts out so a second and third line do not copy
240 lines each).

A rule migration is four constants and two thin functions. The constants:

    MIGRATION_ID   the id the bridge gates on and the applied row carries
    MARKER         the idempotency phrase, a substring of the line
    RULE_LINE      the IMPORTANT bullet, in the customer's voice, one line
    SOURCE_SKILL   the writer named on the row

The functions, `plan(workspace_root)` and `<migration_id>(events_jsonl_path,
workspace_root, detector_context)`, call `plan_line` and `apply_line` here.

WHAT apply_line DOES
--------------------
  * The marker is already in CLAUDE.md -> nothing; ran False. That is the
    idempotency gate AND the "deliberate presence" case: a seat that pasted
    the rule by hand reads as applied.
  * The ledger already carries a `workspace_migration_applied` or a
    `workspace_migration_skipped` row for the id -> nothing. A customer who
    deletes the rule after it was applied is respected; the bridge never
    re-adds (the FB-5 rule: a skip is as durable as an apply).
  * Otherwise: ONE line appended at the end of the `## Session Rules` bullet
    list; when the heading is missing, a new `## Session Rules` section at the
    end of the file carrying only that line (the sanctioned Rule-6 exception
    the email, widget, research and docs rule migrations use). Nothing else in
    the file moves. Then ONE `workspace_migration_applied` row whose
    `migration_id` is the module's constant.

Additive, reversible (delete the line), no data loss. Signature per
references/RELEASE_MANIFEST.md "Action contract".
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

SESSION_RULES_HEADING = "## Session Rules"
TARGET_REL = "CLAUDE.md"
APPLIED_EVENT = "workspace_migration_applied"

STATUS_PENDING = "pending"
STATUS_ALREADY_PRESENT = "already_present"
STATUS_ADJUDICATED = "adjudicated"
STATUS_NO_FILE = "no_claude_md"


class RuleLineInvalid(RuntimeError):
    """The constant no longer reads as a rule: refuse to write it anywhere."""


def validate_rule_line(line: str, marker: str) -> None:
    """Refuse a constant that is not one IMPORTANT bullet carrying its marker
    and reading as rule language to the template guard."""
    if marker not in line or not line.startswith("- IMPORTANT:"):
        raise RuleLineInvalid("the rule line must be an IMPORTANT bullet carrying the marker")
    if "\n" in line:
        raise RuleLineInvalid("the rule line is one line")
    try:
        import claude_md_guard  # noqa: WPS433 - the template's own rule-language gate

        if not claude_md_guard.is_rule_language(line):
            raise RuleLineInvalid("the rule line is not rule language")
    except ImportError:
        pass


def _target(workspace_root) -> Path:
    return Path(str(workspace_root)) / TARGET_REL


def _adjudicated(workspace_root, migration_id: str) -> bool:
    try:
        import migration_adjudication

        return bool(migration_adjudication.is_suppressed(workspace_root, migration_id))
    except Exception:  # noqa: BLE001 - an unreadable ledger is "unadjudicated"
        return False


def _heading_index(lines) -> Optional[int]:
    for i, line in enumerate(lines):
        if line.strip() == SESSION_RULES_HEADING:
            return i
    return None


def plan_line(workspace_root, *, migration_id: str, marker: str) -> Dict[str, Any]:
    """What a run would do, and why. A pure read: nothing is written."""
    target = _target(workspace_root)
    out: Dict[str, Any] = {"migration_id": migration_id, "target_file": TARGET_REL,
                           "heading_present": False, "status": STATUS_PENDING,
                           "reason": ""}
    if not target.is_file():
        out.update({"status": STATUS_NO_FILE,
                    "reason": "the workspace has no CLAUDE.md to append to"})
        return out
    text = target.read_text(encoding="utf-8")
    out["heading_present"] = _heading_index(text.splitlines()) is not None
    if marker in text:
        out.update({"status": STATUS_ALREADY_PRESENT,
                    "reason": "the rule is already in CLAUDE.md"})
        return out
    if _adjudicated(workspace_root, migration_id):
        out.update({"status": STATUS_ADJUDICATED,
                    "reason": "a prior run applied or skipped this migration; "
                              "a deleted rule is respected"})
        return out
    return out


def render_appended(text: str, *, line: str, marker: str) -> str:
    """`text` with `line` appended once, under `## Session Rules`.

    Pure. The byte diff against `text` is the one line (plus the section
    heading when it had to be created at the end of the file).
    """
    if marker in text:
        return text
    lines = text.split("\n")
    trailing_newline = text.endswith("\n")
    if trailing_newline:
        lines = lines[:-1]
    head = _heading_index(lines)
    if head is None:
        body = list(lines)
        while body and not body[-1].strip():
            body.pop()
        body += ["", SESSION_RULES_HEADING, "", line]
        return "\n".join(body) + "\n"
    end = len(lines)
    for j in range(head + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    insert_at = head + 1
    for j in range(head + 1, end):
        if lines[j].lstrip().startswith("- "):
            insert_at = j + 1
    lines.insert(insert_at, line)
    return "\n".join(lines) + ("\n" if trailing_newline else "")


def apply_line(events_jsonl_path, workspace_root,
               detector_context: Optional[Dict[str, Any]], *,
               migration_id: str, marker: str, line: str,
               source_skill: str) -> Dict[str, Any]:
    """The action: append once, log once, or do nothing and say why."""
    ctx = dict(detector_context or {})
    validate_rule_line(line, marker)
    try:
        from receipts import require_writer_identity

        require_writer_identity(workspace_root=workspace_root)
    except ImportError:
        pass
    planned = plan_line(workspace_root, migration_id=migration_id, marker=marker)
    context: Dict[str, Any] = {"migration_id": migration_id, "status": planned["status"],
                               "target_file": TARGET_REL, "report": planned["reason"]}
    if planned["status"] != STATUS_PENDING:
        return {"success": True, "ran": False, "context": context, "error": None,
                "fallback_prompt": None}

    target = _target(workspace_root)
    before = target.read_text(encoding="utf-8")
    after = render_appended(before, line=line, marker=marker)
    if marker not in after or after.count(line) != 1:
        return {"success": False, "ran": False, "context": context,
                "error": "the rendered CLAUDE.md does not carry the rule exactly once",
                "fallback_prompt": None}
    from atomic_write import atomic_write_text

    atomic_write_text(target, after)
    if marker not in target.read_text(encoding="utf-8"):
        return {"success": False, "ran": False, "context": context,
                "error": "CLAUDE.md was written but the rule is not in it",
                "fallback_prompt": None}

    from event_gate import append_event

    row = {"type": APPLIED_EVENT, "source_skill": source_skill,
           "data": {"migration_id": migration_id, "target_file": TARGET_REL,
                    "from_version": ctx.get("from_version"),
                    "to_version": ctx.get("to_version"),
                    "actor": source_skill,
                    "created_section": not planned["heading_present"]}}
    events_path = (Path(str(events_jsonl_path)) if events_jsonl_path
                   else Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl")
    stamped = append_event(events_path, [row], holder=source_skill)
    context.update({"status": "applied", "created_section": not planned["heading_present"],
                    "event_seq": (stamped[0].get("seq") if stamped else None)})
    return {"success": True, "ran": True, "context": context, "error": None,
            "fallback_prompt": None}


__all__ = [
    "APPLIED_EVENT", "RuleLineInvalid", "SESSION_RULES_HEADING", "STATUS_ADJUDICATED",
    "STATUS_ALREADY_PRESENT", "STATUS_NO_FILE", "STATUS_PENDING", "TARGET_REL",
    "apply_line", "plan_line", "render_appended", "validate_rule_line",
]
