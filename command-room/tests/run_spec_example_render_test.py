#!/usr/bin/env python3
"""Structural guard #2: leak-scan every literal string inside an example
`data_view = {...}` block in every spec doc.

Why this exists: the v3.13.2 structural orchestrator-actions test guards
against orphan action verbs. But there's a DIFFERENT recurring failure mode:
example body text inside spec docs containing phrases that trip the leak
scanner.

  - v3.13.3 caught it in `orchestrator-dont-forget.md`'s intro-followup-check
    (`context_tag` contained `"interaction events"`).
  - v3.13.4 caught the same class in `orchestrator-upcoming-meetings.md`'s
    upcoming-meetings example (`body_lines` contained `"Phase 1"`).

Both would have crashed at the renderer's post-render leak-scan gate with
`LeakDetectedError`. Render-time exception, not test-time. So an orchestrator
following the spec literally would never render its widget.

This test extracts every Python string literal inside every example
`data_view = {...}` block, runs it through the leak scanner (`scan_for_id_leaks`),
and reports any trip. Static + side-effect-free, no exec required.

Run via: python3 tests/run_spec_example_render_test.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from chat_output_renderer import scan_for_id_leaks  # noqa: E402


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
]


# Regex to capture ```python``` code blocks
CODE_BLOCK_RE = re.compile(r"```python\s*\n(.*?)\n```", re.DOTALL)

# Detect blocks that build a data_view literal (the user-facing payload).
DATA_VIEW_ASSIGN_RE = re.compile(r"^\s*data_view\s*=\s*\{", re.MULTILINE)
# Also catch items = [...] loops that build per-item dicts.
ITEMS_BUILD_RE = re.compile(r"^\s*items\.append\s*\(", re.MULTILINE)


def _extract_relevant_blocks(text: str) -> list[tuple[int, str]]:
    """Return (start_line, source) for every ```python``` block that builds
    a data_view or appends items (which feed into data_view)."""
    out = []
    for m in CODE_BLOCK_RE.finditer(text):
        block = m.group(1)
        if DATA_VIEW_ASSIGN_RE.search(block) or ITEMS_BUILD_RE.search(block):
            line = text.count("\n", 0, m.start()) + 1
            out.append((line, block))
    return out


def _extract_string_literals(source: str) -> list[tuple[int, str]]:
    """Walk the AST of `source` and yield (relative_line, value) for every
    string literal in the block — including f-string CONSTANT parts. The
    relative_line is 1-based within the block."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # Plain string constants
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip():
                out.append((getattr(node, "lineno", 0), node.value))
        # f-string CONSTANT parts (the literal text BETWEEN {} placeholders)
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    if part.value.strip():
                        out.append((getattr(part, "lineno", getattr(node, "lineno", 0)), part.value))
    return out


def main() -> int:
    total_strings = 0
    failures: list[tuple[Path, int, str, list[tuple[str, str]]]] = []

    for path in FILES_TO_CRAWL:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for block_line, block in _extract_relevant_blocks(text):
            for rel_line, literal in _extract_string_literals(block):
                total_strings += 1
                abs_line = block_line + rel_line  # approximate
                leaks = scan_for_id_leaks(literal)
                if leaks:
                    failures.append((path, abs_line, literal, leaks))

    print(f"Crawled {len(FILES_TO_CRAWL)} files, leak-scanned {total_strings} string literals from spec data_view / item blocks.")

    if failures:
        print(f"\nFAIL — {len(failures)} string literal(s) tripped the leak scanner:\n")
        for path, line, literal, leaks in failures:
            rel = path.relative_to(ROOT.parent)
            print(f"  {rel}:~{line}")
            display = literal if len(literal) < 120 else literal[:117] + "..."
            print(f"    literal: {display!r}")
            for kind, label in leaks:
                print(f"    leak:    {label} → matched {kind!r}")
            print()
        print("Every literal string inside an example data_view (or item dict)")
        print("in a spec doc MUST pass the leak scanner. A leak in the example")
        print("means an orchestrator copy-pasting the surrounding shape from")
        print("the spec will crash at the post-render leak-scan gate.")
        print()
        print("Fix: rewrite the literal so the offending substring is gone.")
        return 1

    print("\nOK — every spec-doc data_view example string is leak-scanner clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
