#!/usr/bin/env python3
"""Structural guard: every action verb emitted by any orchestrator must be
canonical (per `is_canonical_action()` in chat_output_renderer.py).

Why this exists: in v3.13.2 we discovered that `orchestrator-dont-forget.md`
had been emitting `landed` / `didnt land` / `snooze 14d` since v3.11.1 — verbs
that aren't in `CANONICAL_ACTIONS`. The renderer raises `CanonicalActionError`
on render, so any Pulse fire that surfaced an intro-followup-check item never
rendered the widget for 8 releases. The pre-existing `run_v2_14_38_actions_test.py`
catches "is this specific verb canonical" but does NOT crawl orchestrator files
to validate what they actually emit.

This test does the structural crawl: for every `actions: [...]` array literal
in every orchestrator-*.md file under `skills/enable-command-room-schedules/
references/`, parse out the action verbs and verify each is accepted by
`is_canonical_action()`. Same for skill SKILL.md files that build widget
data_views.

Run via: python3 tests/run_orchestrator_actions_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from chat_output_renderer import is_canonical_action  # noqa: E402


# Files to crawl. Orchestrators in references/, plus skills that build widgets.
FILES_TO_CRAWL = [
    ROOT / "skills" / "enable-command-room-schedules" / "references" / "orchestrator-dont-forget.md",
    ROOT / "skills" / "enable-command-room-schedules" / "references" / "orchestrator-commitments.md",
    ROOT / "skills" / "enable-command-room-schedules" / "references" / "orchestrator-inbox.md",
    ROOT / "skills" / "enable-command-room-schedules" / "references" / "orchestrator-past-meetings.md",
    ROOT / "skills" / "enable-command-room-schedules" / "references" / "orchestrator-upcoming-meetings.md",
    ROOT / "skills" / "email-writer" / "SKILL.md",
    ROOT / "skills" / "intro-broker" / "SKILL.md",
    ROOT / "skills" / "follow-up-ritual" / "SKILL.md",
    ROOT / "skills" / "thread-resurrection" / "SKILL.md",
    ROOT / "skills" / "inbox-triage" / "SKILL.md",
    ROOT / "skills" / "show-my-list" / "SKILL.md",
    ROOT / "skills" / "calendar-writer" / "SKILL.md",
    ROOT / "skills" / "workspace-ingest" / "SKILL.md",
    ROOT / "skills" / "scaffold-automation" / "SKILL.md",
]


# Strip leading tokenizer prefix only. A tokenizer is one of:
#   - `<n>` literal placeholder in spec docs
#   - `{i}` / `{n}` Python f-string placeholder
#   - Numeric/alphanumeric prefix: `1`, `12`, `1a`, `2b`, `Na` (digit-led OR N-led)
#   - `<slug>` / `<idx>` / `<sub>` literal placeholders
# BARE LOWERCASE WORDS (e.g. `edit`, `add`, `send`) are NEVER tokenizers — they're verbs.
TOKEN_PREFIX = re.compile(r"^(?:<[a-z]+>|\{[a-z_]+\}|\d+[a-z]*|N[a-z]?)\s+")

# Skip strings whose verb we can't statically resolve (placeholders we can't parse)
SKIP_IF_CONTAINS = ("{", "<", "[Slug]", "[slug]", "['")


def _extract_action_strings_from_array(line_content: str) -> list[str]:
    """Pull individual quoted strings out of a single-line 'actions: [...]'
    literal. Robust to single + double quotes, and to fragments like
    f"{i} send"."""
    out = []
    # Match both 'foo' and "foo" — and f-strings (their content can be statically extracted for the static suffix)
    for m in re.finditer(r'"([^"\n]+)"|\'([^\'\n]+)\'', line_content):
        s = m.group(1) if m.group(1) is not None else m.group(2)
        if s.strip():
            out.append(s)
    return out


def _normalize_action(action_str: str) -> str | None:
    """Strip the leading tokenizer (`<n>`, `1`, `{i}`, `1a`, etc.) and return
    the bare action verb. Return None if the string is unparseable as an
    action (e.g., it's an f-string placeholder we can't resolve)."""
    # F-string of pure placeholder: skip
    if action_str.startswith("{") and action_str.endswith("}"):
        return None
    # Static tokens with f-string prefix: try to strip the leading f-format
    cleaned = re.sub(r"^f?\{[^}]+\}\s+", "", action_str)
    cleaned = TOKEN_PREFIX.sub("", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    return cleaned


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return [(line_number, raw_action_string, normalized_verb), ...] for
    every quoted action verb found inside an `actions: [...]` array literal
    in `path`."""
    if not path.exists():
        return []
    findings = []
    in_actions_block = False
    actions_buffer = ""
    actions_start_line = 0
    bracket_depth = 0
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not in_actions_block:
            # Look for the start of an actions array
            m = re.search(r'["\']?actions["\']?\s*:?\s*[=]?\s*\[', line)
            if m:
                in_actions_block = True
                actions_start_line = idx
                actions_buffer = line[m.end():]
                bracket_depth = 1
                # Single-line case
                close_idx = actions_buffer.find("]")
                if close_idx >= 0:
                    bracket_depth = 0
                    in_actions_block = False
                    body = actions_buffer[:close_idx]
                    for s in _extract_action_strings_from_array(body):
                        verb = _normalize_action(s)
                        if verb:
                            findings.append((actions_start_line, s, verb))
                    actions_buffer = ""
        else:
            actions_buffer += "\n" + line
            if "]" in line:
                bracket_depth -= line.count("]")
                bracket_depth += line.count("[")
                if bracket_depth <= 0:
                    in_actions_block = False
                    body = actions_buffer.rsplit("]", 1)[0]
                    for s in _extract_action_strings_from_array(body):
                        verb = _normalize_action(s)
                        if verb:
                            findings.append((actions_start_line, s, verb))
                    actions_buffer = ""
    return findings


def main() -> int:
    failures: list[tuple[Path, int, str, str]] = []
    total_checked = 0
    for path in FILES_TO_CRAWL:
        findings = _scan_file(path)
        for line, raw, verb in findings:
            total_checked += 1
            # Skip f-string-derived placeholders we can't resolve
            if any(s in verb for s in SKIP_IF_CONTAINS):
                continue
            if not is_canonical_action(verb):
                failures.append((path, line, raw, verb))

    print(f"Crawled {len(FILES_TO_CRAWL)} files, checked {total_checked} action strings.")
    if failures:
        print(f"\nFAIL — {len(failures)} non-canonical action verb(s) found:\n")
        for path, line, raw, verb in failures:
            rel = path.relative_to(ROOT.parent)
            print(f"  {rel}:{line}")
            print(f"    raw:  {raw!r}")
            print(f"    verb: {verb!r}")
            print()
        print("Every action verb emitted by an orchestrator or skill MUST")
        print("match `is_canonical_action()` in chat_output_renderer.py — or")
        print("the renderer raises CanonicalActionError at render time.")
        print()
        print("Fix one of:")
        print("  (a) add the verb to CANONICAL_ACTIONS (chat_output_renderer.py)")
        print("  (b) rewrite the emitter to use an existing canonical verb")
        return 1

    print("OK — every emitted action verb is canonical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
