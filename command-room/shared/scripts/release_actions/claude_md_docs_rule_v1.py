#!/usr/bin/env python3
"""claude_md_docs_rule_v1 — append the document-routing rule to a workspace's
CLAUDE.md, once, without asking (DOCS1 D-1, 2026-09-24; DOCSFENCE1 before it).

WHY
---
Since the host platform's 2026-09-16 "one Claude" merge every seat carries a
built-in `docs` skill whose instructions say: when a request sounds like a
document and names no file format, create the page FIRST, before any file
read or plan. That preempts this plugin's routing. On 2026-09-18 "process my
call" then "prep me for the call" produced a claude.ai page and nothing else:
no brief in the folder, no meeting row, no receipt, no leak scan. Every
defence the product has runs AFTER a Command Room skill fires, and the host
runs no hooks, so the ONE surface unconditionally in context that wins the
routing question is the workspace's own CLAUDE.md (the Bug #104 / EW1
precedent, re-proven by the widget rules and the research rule).

Fresh workspaces get the rule from `references/claude-md-template.md`. This
module back-fills every existing install: the update bridge (Phase 4.5) runs
it through the WRITE door (`plan run_writer`), beside the customer's files,
under the customer's writer id.

WHAT IT DOES
------------
    plan(workspace_root)      -> what a run would do; a pure read
    claude_md_docs_rule_v1(events_jsonl_path, workspace_root, detector_context)
                              -> the auto_apply action contract
                                 (references/RELEASE_MANIFEST.md)

  * The marker phrase (`MARKER`) is already in CLAUDE.md -> nothing; ran False.
    That is the idempotency gate AND the "deliberate presence" case: a seat
    that pasted the rule by hand reads as applied.
  * The ledger already carries a `workspace_migration_applied` or a
    `workspace_migration_skipped` row for `MIGRATION_ID` -> nothing. A customer
    who deletes the rule after it was applied is respected; the bridge never
    re-adds (the FB-5 rule: a skip is as durable as an apply).
  * Otherwise: ONE line (`DOCS_RULE_LINE`) appended at the end of the
    `## Session Rules` bullet list; when the heading is missing, a new
    `## Session Rules` section at the end of the file carrying only that line
    (the sanctioned Rule-6 exception the email / widget / research rule
    migrations use). Nothing else in the file moves. Then ONE
    `workspace_migration_applied` row whose `migration_id` is this module's
    constant — the id the bridge gates on and the id in the customer's record
    cannot drift into two strings.

The rule text is the module's constant, and the template bullet and the
bridge's quoted block are pinned BYTE-IDENTICAL to it (tests/run_docs1_test.py,
tests/run_docs_rule_migration_test.py): one sentence, three carriers, no drift.

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

SOURCE_SKILL = "command-room-update-bridge"
MIGRATION_ID = "claude_md_docs_rule_v1"

#: The idempotency phrase. It lives inside `DOCS_RULE_LINE`; a CLAUDE.md that
#: carries it anywhere reads as applied.
MARKER = "never as a Claude Doc"

#: THE rule, in the customer's voice (CLAUDE.md speaks as the customer). The
#: template's Session Rules bullet and the bridge's quoted block are this
#: string, byte for byte.
DOCS_RULE_LINE = (
    "- IMPORTANT: Anything about my work that comes out as a document — a prep, "
    "brief, memo, one-pager, recap, or notes — is produced by the Command Room "
    "skill that owns it and saved in the Command Room folder, never as a Claude "
    "Doc, page, or artifact, even when the composer's Output is set to Docs or "
    "Slides; the built-in docs skill can't see my workspace and nothing it makes "
    "lands on my files."
)

SESSION_RULES_HEADING = "## Session Rules"
TARGET_REL = "CLAUDE.md"
APPLIED_EVENT = "workspace_migration_applied"

STATUS_PENDING = "pending"
STATUS_ALREADY_PRESENT = "already_present"
STATUS_ADJUDICATED = "adjudicated"
STATUS_NO_FILE = "no_claude_md"


class DocsRuleInvalid(RuntimeError):
    """The constant no longer reads as a rule — refuse to write it anywhere."""


def _validate_rule_line() -> None:
    if MARKER not in DOCS_RULE_LINE or not DOCS_RULE_LINE.startswith("- IMPORTANT:"):
        raise DocsRuleInvalid("DOCS_RULE_LINE must be an IMPORTANT bullet carrying the marker")
    try:
        import claude_md_guard  # noqa: WPS433 - the template's own rule-language gate

        if not claude_md_guard.is_rule_language(DOCS_RULE_LINE):
            raise DocsRuleInvalid("DOCS_RULE_LINE is not rule language")
    except ImportError:
        pass
    if "\n" in DOCS_RULE_LINE:
        raise DocsRuleInvalid("DOCS_RULE_LINE is one line")


def _target(workspace_root) -> Path:
    return Path(str(workspace_root)) / TARGET_REL


def _adjudicated(workspace_root) -> bool:
    try:
        import migration_adjudication

        return bool(migration_adjudication.is_suppressed(workspace_root, MIGRATION_ID))
    except Exception:  # noqa: BLE001 - an unreadable ledger is "unadjudicated"
        return False


def plan(workspace_root) -> Dict[str, Any]:
    """What a run would do, and why. A pure read: nothing is written."""
    target = _target(workspace_root)
    out: Dict[str, Any] = {"migration_id": MIGRATION_ID, "target_file": TARGET_REL,
                           "heading_present": False, "status": STATUS_PENDING,
                           "reason": ""}
    if not target.is_file():
        out.update({"status": STATUS_NO_FILE,
                    "reason": "the workspace has no CLAUDE.md to append to"})
        return out
    text = target.read_text(encoding="utf-8")
    out["heading_present"] = _heading_index(text.splitlines()) is not None
    if MARKER in text:
        out.update({"status": STATUS_ALREADY_PRESENT,
                    "reason": "the rule is already in CLAUDE.md"})
        return out
    if _adjudicated(workspace_root):
        out.update({"status": STATUS_ADJUDICATED,
                    "reason": "a prior run applied or skipped this migration; "
                              "a deleted rule is respected"})
        return out
    return out


def _heading_index(lines) -> Optional[int]:
    for i, line in enumerate(lines):
        if line.strip() == SESSION_RULES_HEADING:
            return i
    return None


def render_appended(text: str) -> str:
    """`text` with `DOCS_RULE_LINE` appended once, under `## Session Rules`.

    Pure. The byte diff against `text` is the one line (plus the section
    heading when it had to be created at the end of the file).
    """
    if MARKER in text:
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
        body += ["", SESSION_RULES_HEADING, "", DOCS_RULE_LINE]
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
    lines.insert(insert_at, DOCS_RULE_LINE)
    return "\n".join(lines) + ("\n" if trailing_newline else "")


def claude_md_docs_rule_v1(events_jsonl_path, workspace_root,
                           detector_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The action: append once, log once, or do nothing and say why."""
    ctx = dict(detector_context or {})
    _validate_rule_line()
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

    target = _target(workspace_root)
    before = target.read_text(encoding="utf-8")
    after = render_appended(before)
    if MARKER not in after or after.count(DOCS_RULE_LINE) != 1:
        return {"success": False, "ran": False, "context": context,
                "error": "the rendered CLAUDE.md does not carry the rule exactly once",
                "fallback_prompt": None}
    from atomic_write import atomic_write_text

    atomic_write_text(target, after)
    if MARKER not in target.read_text(encoding="utf-8"):
        return {"success": False, "ran": False, "context": context,
                "error": "CLAUDE.md was written but the rule is not in it",
                "fallback_prompt": None}

    from event_gate import append_event

    row = {"type": APPLIED_EVENT, "source_skill": SOURCE_SKILL,
           "data": {"migration_id": MIGRATION_ID, "target_file": TARGET_REL,
                    "from_version": ctx.get("from_version"),
                    "to_version": ctx.get("to_version"),
                    "actor": SOURCE_SKILL,
                    "created_section": not planned["heading_present"]}}
    events_path = (Path(str(events_jsonl_path)) if events_jsonl_path
                   else Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl")
    stamped = append_event(events_path, [row], holder=SOURCE_SKILL)
    context.update({"status": "applied", "created_section": not planned["heading_present"],
                    "event_seq": (stamped[0].get("seq") if stamped else None)})
    return {"success": True, "ran": True, "context": context, "error": None,
            "fallback_prompt": None}


__all__ = [
    "APPLIED_EVENT", "DOCS_RULE_LINE", "MARKER", "MIGRATION_ID", "SESSION_RULES_HEADING",
    "SOURCE_SKILL", "STATUS_ADJUDICATED", "STATUS_ALREADY_PRESENT", "STATUS_NO_FILE",
    "STATUS_PENDING", "TARGET_REL", "claude_md_docs_rule_v1", "plan", "render_appended",
]
