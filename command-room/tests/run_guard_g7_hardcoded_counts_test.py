#!/usr/bin/env python3
"""Guard G7 — hardcoded task/chat/question counts in customer-verbatim copy.

The registry (schedule_config.py ORCHESTRATOR_MAP / FIRST_INSTALL_TASK_IDS /
SILENT_TASKS) is the only source of truth for how many scheduled chats exist;
question counts belong to the widget that renders them. Both audits found
7 different hardcoded answers (5/6/7/8/9/12) plus the shipped
"Five quick questions" vs the four-question widget.

Scope: customer-verbatim blockquotes (`> *"..."*`) in skills/ — the same
extraction scope as run_pl_banned_words_test.py. A count-word adjacent to
tasks/chats/questions inside a verbatim block fails; prose marked
"illustrative" in the surrounding 200 chars is allowed.

Run: PYTHONUTF8=1 python tests/run_guard_g7_hardcoded_counts_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

BLOCKQUOTE_RE = re.compile(r'(?:^>\s*\*?\s*".+?"\s*\*?\s*\n?)+', re.MULTILINE)
FENCE_RE = re.compile(r"```[\s\S]*?```")
COUNT_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s+"
    r"(?:scheduled\s+)?(tasks?|chats?|questions?)\b",
    re.IGNORECASE,
)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    violations: list[str] = []
    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        rel = skill_md.relative_to(ROOT).as_posix()
        text = FENCE_RE.sub("", skill_md.read_text(encoding="utf-8"))
        for bm in BLOCKQUOTE_RE.finditer(text):
            block = bm.group(0)
            for m in COUNT_RE.finditer(block):
                ctx_start = max(0, bm.start() - 200)
                context = text[ctx_start: bm.end() + 200].lower()
                if "illustrative" in context or "example below" in context:
                    continue
                # "[N] tasks" template placeholders are fine — only literals fail
                pre = block[max(0, m.start() - 1): m.start()]
                if pre == "[":
                    continue
                line_no = text.count("\n", 0, bm.start() + m.start()) + 1
                violations.append(
                    f"{rel}:{line_no}: literal count in customer copy — “{m.group(0)}” (render from the registry / the widget's real count)"
                )

    if violations:
        print(f"FAIL — {len(violations)} hardcoded count(s) in customer-verbatim copy:\n")
        for v in violations:
            print(f"  ✗ {v}")
        return 1
    print("OK — no hardcoded task/chat/question counts in customer-verbatim copy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
