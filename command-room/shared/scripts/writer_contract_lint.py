#!/usr/bin/env python3
"""
Writer-Contract lint (SPEC GATE1, item 4 — the event-write surface).

WHY THIS EXISTS
---------------
A1's writer lock only protects event writes that actually reach
`atomic_append_jsonl`. The lint that was supposed to enforce this only ever
existed as PROSE in cleanup/SKILL.md + WORKSPACE_API.md ("scans all SKILL.md
files for the Writer Contract HEADER") — and a header is not a write path. A
skill could carry the boilerplate "## Writer Contract — read WORKSPACE_API.md"
header and STILL hand-roll a `next_seq`+`open('a')` append (or a raw `>>`) that
dodges the cross-process lock entirely. decision-log was the confirmed clean
bypass (v3.20.0 §3): it declared a `decision`-event append but its body named
neither the helper nor any append-routing script.

This module is the executable version of that lint. For every skill that
DECLARES an `events.jsonl` append in its SKILL.md, it asserts the BODY names the
locked writer `atomic_append_jsonl` OR a known append-routing helper script that
itself routes through it — not just that a Writer-Contract header is present.

It is FLAG-ONLY at runtime: `cleanup` surfaces findings in the Monday note (a
non-technical "one of your skills writes the activity log the unsafe way" line),
never blocking. The teeth are (a) a permanent guard test so a NEW appender skill
that names no helper is caught in CI, and (b) the decision-log fix it verifies.

Stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

# The canonical locked writer. Naming this directly always passes.
LOCKED_WRITER = "atomic_append_jsonl"

# Append-routing helper scripts — each VERIFIED (build-time grep, 2026-06-14) to
# call atomic_append_jsonl for its event writes. A skill that drives its event
# append through one of these "routes through a script that does," which the
# SPEC accepts as equivalent to naming the helper directly.
ROUTING_HELPERS = frozenset({
    "cru_match",
    "people_writer",
    "engagement_writer",
    "thread_writer",
    "org_writer",
    "objective_state",  # OBJ1 — the sole objective writer; routes through thread_writer + event_gate.append_event (atomic_append_jsonl)
    "log_pack_run",
    "value_receipt",
    "brief_state",
    "commitment_state",  # brief_state's promoted home (Phase 2 Stage A) — same writer
    "brief_writer",
    "advisor_profile_writer",
    "reconcile_sent_commitments",
    "sent_capture",  # BUG-3719 (v4.6.2) — capture_sent_items routes through event_gate.append_event -> atomic_append_jsonl
    "stall_detector",
    "skill_config_writer",
    "decision_match",
    "source_event_seq_backfill",
    "recover_corruption",
    "session_sweep",  # Phase 5 — session_sweep._sweep routes through append_event -> atomic_append_jsonl (the nightly sweep + the R2 backfill share it)
})

# A line "declares an append" when it ties an append/emit/write verb to
# events.jsonl. Read-only mentions ("Reads from", "read-only consumer of",
# "for search/retrieval", "count") are excluded so we don't flag the many
# skills that merely READ the event log.
_DECLARE_RE = re.compile(
    r"(append(s|er)?|emit(s|ted)?|writes?|primary (writer|appender))",
    re.IGNORECASE,
)
_EVENTS_RE = re.compile(r"events\.jsonl", re.IGNORECASE)
_READONLY_LINE_RE = re.compile(
    r"(read[- ]only|reads? from|read-only consumer|for search|for retrieval|"
    r"\bcount\b|does not (modify|write)|never (rewrite|edit|mutate))",
    re.IGNORECASE,
)


def _strip_frontmatter(text: str) -> str:
    """Drop the YAML frontmatter block (between the leading `---` fences). The
    `description:` field routinely mentions 'events.jsonl' for trigger/context
    prose; it is metadata, never a write path, so it must not count as an
    append declaration."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    nl = text.find("\n", end + 1)
    return text[nl + 1:] if nl != -1 else ""


# Section headers that OPEN an append-declaration block (a Writer-Contract
# "Appends to:" / "Primary writer/appender" block whose bullets name the files
# the skill writes). Within such a block, an events.jsonl bullet is an append
# declaration even though the verb lives on the header line, not the bullet.
_APPEND_BLOCK_OPEN_RE = re.compile(
    r"^\s*\**\s*(appends? to|primary (writer|appender)|writes:)\b",
    re.IGNORECASE,
)
# Section headers that CLOSE the append block (the declaration has moved on to
# reads / non-writes).
_APPEND_BLOCK_CLOSE_RE = re.compile(
    r"^\s*\**\s*(reads?( from)?|read-only|never writes?|does not write|"
    r"read protocol|conflict boundary|reads \()",
    re.IGNORECASE,
)


def declares_event_append(text: str) -> bool:
    """True if the SKILL.md body declares it APPENDS to events.jsonl (vs only
    reading it). The YAML frontmatter is excluded (description prose is not a
    write path). Two shapes are recognized:

      1. Inline — an append/emit/write verb on the same line as `events.jsonl`
         ("append a pack_run event to events.jsonl").
      2. Block — an `**Appends to:**` / `**Primary appender**` section header,
         then an `events.jsonl` bullet beneath it (the verb is on the header,
         the file on the bullet — the most common Writer-Contract shape).
    """
    in_append_block = False
    for line in _strip_frontmatter(text).split("\n"):
        # Update block state from section headers first.
        if _APPEND_BLOCK_OPEN_RE.search(line):
            in_append_block = True
        elif _APPEND_BLOCK_CLOSE_RE.search(line):
            in_append_block = False

        if not _EVENTS_RE.search(line):
            continue
        if _READONLY_LINE_RE.search(line):
            continue
        # Shape 1: inline verb + events.jsonl on the same line.
        if _DECLARE_RE.search(line):
            return True
        # Shape 2: an events.jsonl bullet inside an open append block.
        if in_append_block:
            return True
    return False


def names_locked_writer(text: str) -> bool:
    """True if the body names the locked writer directly OR an append-routing
    helper script that routes through it."""
    if LOCKED_WRITER in text:
        return True
    return any(h in text for h in ROUTING_HELPERS)


def lint_skill_text(name: str, text: str) -> Optional[Dict]:
    """Lint one SKILL.md's text. Returns a finding dict when the skill declares
    an events.jsonl append but names neither the locked writer nor a routing
    helper; else None."""
    if not declares_event_append(text):
        return None
    if names_locked_writer(text):
        return None
    return {
        "skill": name,
        "check": "writer_contract.event_append_not_locked",
        "reason": (
            f"{name} declares an events.jsonl append but its body names neither "
            f"`{LOCKED_WRITER}` nor a known append-routing helper "
            f"({', '.join(sorted(ROUTING_HELPERS))}). A header pointer to "
            f"WORKSPACE_API.md is not a write path — the append may dodge the "
            f"A1 writer lock. Add the explicit append recipe (see "
            f"shared/WORKSPACE_API.md Append Protocol §3)."
        ),
    }


def lint_skill_event_writes(plugin_root) -> List[Dict]:
    """Scan every skills/*/SKILL.md under `plugin_root` and return the findings
    for skills whose event-append path doesn't reach the locked writer.

    FLAG-ONLY: callers (cleanup) surface these; they never block. Returns an
    empty list on a clean tree."""
    root = Path(plugin_root)
    skills_dir = root / "skills"
    findings: List[Dict] = []
    if not skills_dir.is_dir():
        return findings
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name = skill_md.parent.name
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        finding = lint_skill_text(name, text)
        if finding:
            findings.append(finding)
    return findings


def _resolve_plugin_root() -> Path:
    """This file lives at <root>/shared/scripts/writer_contract_lint.py."""
    return Path(__file__).resolve().parent.parent.parent


if __name__ == "__main__":
    import json
    import sys

    root = Path(sys.argv[1]) if len(sys.argv) > 1 else _resolve_plugin_root()
    out = lint_skill_event_writes(root)
    print(json.dumps({"findings": out, "count": len(out)}, indent=2))
    # Exit 0 always — this is a flag-only lint, not a ship gate.
    sys.exit(0)
