#!/usr/bin/env python3
"""PL.1 banned-word guard — customer-verbatim blockquotes in SKILL.md files.

Phase 4 PL sweep (2026-07-02), the in-repo counterpart of ship-gate guard G5.
Scans the canonical customer-facing render examples in every skill file — the
italic-blockquote pattern (`> *"..."*`) that run_customer_facing_voice_test.py
established as the customer-verbatim scope — plus release-manifest templates,
for the plain-language audit's banned vocabulary.

The rule this encodes (audit B, systemic finding #1): internal architecture
vocabulary must never appear in copy the CEO reads. Ban-in-rules plus
present-in-examples is worse than no rule — models copy the examples.

Scope notes:
- Fenced code blocks are stripped BEFORE extraction (LLM-internal spec).
- Backticked spans inside blockquotes are stripped (trigger phrases the
  customer literally types are allowed to be exact).
- "thread" is banned only when NOT an email/Slack/Gmail/text thread.
- Version numbers are banned in blockquotes everywhere (decide-once rule
  PL.4: versions live in verify diagnostics and changelogs only).

Run:
    PYTHONUTF8=1 python tests/run_pl_banned_words_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

# (pattern, human label). Case-insensitive. Conservative, high-confidence.
BANNED = [
    (r"\bsubstrates?\b", "substrate"),
    (r"\bre-?fires?\b", "re-fire"),
    (r"\bfires?\s+(?:at|on|when|again|automatically)\b", "fire(s) as scheduling verb"),
    (r"\bwidgets?\b", "widget"),
    (r"\borchestrators?\b", "orchestrator"),
    (r"\bconnectors?\b", "connector"),
    (r"\brender(?:s|ed|ing)?\b", "render"),
    (r"\bartifacts?\b", "artifact"),
    (r"(?<!email )(?<!Email )(?<!Slack )(?<!Gmail )(?<!text )\bthreads?\b", "thread (as project)"),
    (r"\btaskids?\b", "taskId"),
    (r"\bv\d+\.\d+(?:\.\d+)?\b", "version number"),
    (r"\bevents\.jsonl\b", "events.jsonl"),
    (r"\bentities\.json\b", "entities.json"),
    (r"\baliases\.json\b", "aliases.json"),
    (r"\b_hq/", "_hq/ path"),
    # CLAUDE.md / BUSINESS_CONTEXT.md are CUSTOMER-EDITED files (same exemption
    # as run_customer_facing_voice_test.CUSTOMER_EDITED_FILES) — naming them in
    # prose about the customer's own file is fine, so they are NOT banned here.
    (r"\bcooldowns?\b", "cooldown"),
    (r"\bpeople graph\b", "people graph"),
    (r"\bvoice corpus\b", "voice corpus"),
    (r"\bengagement edge\b", "engagement edge"),
    (r"\bbuffered\b", "buffered"),
    (r"\(0\.\d+\)", "confidence decimal"),
    (r"\bperson records?\b", "person record"),
    (r"\bentity records?\b", "entity record"),
    (r"\bworkstreams?\b", "workstream (say: project)"),
    (r"\bOrgs Map\b", "Orgs Map (renamed: Workspace Map)"),
    # snake_case enum shown in prose (two+ underscore-joined words). Catches
    # status enums / config keys leaking into questions and footers.
    (r"\b[a-z]+_[a-z]+(?:_[a-z]+)*\b", "snake_case token"),
]

BLOCKQUOTE_RE = re.compile(r'(?:^>\s*\*?\s*".+?"\s*\*?\s*\n?)+', re.MULTILINE)
FENCE_RE = re.compile(r"```[\s\S]*?```")
BACKTICK_SPAN_RE = re.compile(r"`[^`\n]*`")


def extract_customer_blockquotes(text: str) -> list[str]:
    no_code = FENCE_RE.sub("", text)
    return BLOCKQUOTE_RE.findall(no_code)


def scan(text: str) -> list[tuple[str, str]]:
    """Return (label, offending snippet) pairs for one blockquote block."""
    clean = BACKTICK_SPAN_RE.sub("", text)
    hits = []
    for pat, label in BANNED:
        for m in re.finditer(pat, clean, re.IGNORECASE):
            line = clean[max(0, m.start() - 40): m.end() + 40].replace("\n", " ").strip()
            hits.append((label, f"…{line}…"))
    return hits


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    violations: list[str] = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        for block in extract_customer_blockquotes(text):
            for label, snippet in scan(block):
                rel = skill_md.relative_to(ROOT)
                violations.append(f"{rel}: [{label}] {snippet}")

    if violations:
        print(f"FAIL — {len(violations)} banned-word violation(s) in customer blockquotes:\n")
        for v in violations:
            print(f"  ✗ {v}")
        print(
            "\nThese strings print to the CEO verbatim. Rewrite in plain English"
            " (see the PL glossary in shared/VOICE_CALIBRATION.md; audit:"
            " Chalette_CR_Skill_PlainLanguage_Audit_2026-07-01)."
        )
        return 1

    print("OK — no banned internal vocabulary in customer-verbatim blockquotes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
