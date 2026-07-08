#!/usr/bin/env python3
"""Guard G4 — prescribed widget actions vs CANONICAL_ACTIONS.

Scans every `"actions": [...]` string-array literal in skills/ (SKILL.md +
orchestrator references) and validates each verb against the renderer's
CANONICAL_ACTIONS frozenset — the P1.1 bug class where five skills spec'd
widgets the renderer rejects at fire time.

Item-number prefixes ("1 send" → "send"), fr-prefixes ("fr1 ..."), f-string
prefixes and bracket parameters are normalized the same way the renderer
does. Dict-shaped arrays (audit-event payloads) are skipped — only string
arrays prescribe buttons.

Run: PYTHONUTF8=1 python tests/run_guard_g4_widget_verbs_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from chat_output_renderer import CANONICAL_ACTIONS  # noqa: E402

ACTIONS_RE = re.compile(r'"actions"\s*:\s*\[([^\]]*)\]')
STR_RE = re.compile(r'(?:f?")([^"]+)"')


def normalize(verb: str) -> str:
    v = verb.strip()
    v = re.sub(r"^\{[^}]*\}\s*", "", v)          # f-string index prefix
    v = re.sub(r"^(?:fr)?\d+[a-z]?\s+", "", v)   # item-number prefix
    return v.strip()


def is_canonical(verb: str) -> bool:
    if verb in CANONICAL_ACTIONS:
        return True
    # parameterized forms: "push to 2026-05-05" matches "push to [date]"
    for c in CANONICAL_ACTIONS:
        if "[" in c:
            stem = c.split("[")[0].strip()
            if stem and verb.startswith(stem):
                return True
    return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    violations: list[str] = []
    for p in sorted((ROOT / "skills").rglob("*.md")):
        rel = p.relative_to(ROOT).as_posix()
        text = p.read_text(encoding="utf-8")
        for m in ACTIONS_RE.finditer(text):
            body = m.group(1)
            if "{" in body and '"n"' in body:
                continue  # dict-shaped (event payload), not button strings
            for s in STR_RE.finditer(body):
                verb = normalize(s.group(1))
                if not verb or "<" in verb:
                    continue  # placeholder like "<verb>"
                if not is_canonical(verb):
                    line_no = text.count("\n", 0, m.start()) + 1
                    violations.append(f"{rel}:{line_no}: action '{verb}' not in CANONICAL_ACTIONS")

    if violations:
        print(f"FAIL — {len(violations)} non-canonical prescribed action(s):\n")
        for v in violations:
            print(f"  ✗ {v}")
        print("\nEither respec onto an existing canonical verb or extend the ONE deliberation set (renderer + CHAT_ACTION_WIDGET + apply-choices together).")
        return 1
    print(f"OK — every prescribed action validates against CANONICAL_ACTIONS ({len(CANONICAL_ACTIONS)} verbs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
