#!/usr/bin/env python3
"""Guard G6 — advertised-trigger validation.

Every command a customer is told to say in verbatim copy must actually route.
Dead advertised commands were the most trust-destroying bug class in both
audits ("update [name]", "set schedule timezone to X", "recheck my org
classifications", "workshop mode" all shipped).

Scope: `Say "<phrase>"` / `say '<phrase>'` / say **<phrase>** shapes inside
customer-verbatim blockquotes AND in plain template lines of SKILL.md files.
Each advertised phrase must match ≥1 skill's positive triggers via the same
matcher run_trigger_test.py uses (bracket placeholders in the phrase are
neutralized before matching).

Run: PYTHONUTF8=1 python tests/run_guard_g6_advertised_triggers_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from run_trigger_test import (  # noqa: E402
    extract_triggers, skill_matches, normalize,
)

SKILLS = ROOT / "skills"

SAY_RE = re.compile(
    r'[Ss]ay(?:ing)?\s+(?:\*\*)?"([^"\n]{3,60})"(?:\*\*)?'      # "..." (apostrophes OK inside)
    r"|[Ss]ay(?:ing)?\s+(?:\*\*)?'([^'\n]{3,60})'(?:\*\*)?"     # '...'
    r"|[Ss]ay(?:ing)?\s+(?:\*\*)?`([^`\n]{3,60})`(?:\*\*)?"     # `...`
    r"|[Ss]ay\s+\*\*([^*\n]{3,60})\*\*"                          # **...**
)

# Phrases that are prose fragments, not commands (filtered post-match)
NON_COMMANDS = {"yes", "no", "undo", "done", "skip", "ok", "add them", "commit"}
# Reply-word affordances dispatched by the surrounding flow, not the router
REPLY_WORDS_MAX = 2  # 1-2 word phrases are widget/flow replies, not triggers


def neutralize(phrase: str) -> str:
    p = re.sub(r"\[[^\]]+\]", "Acme", phrase)   # [name]/[topic] -> stand-in
    p = re.sub(r"\{[^}]+\}", "Acme", p)
    return p.strip()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Build the trigger registry exactly like run_trigger_test does
    registry: dict[str, dict] = {}
    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        m = re.search(r'description:\s*"(.*?)"\s*\n', text, re.S) or re.search(
            r"description:\s*(.+)\n", text
        )
        if not m:
            continue
        # v4.5.1: the declared routing corpus = description + the body's
        # '## Routing (full trigger corpus)' section (same rule as
        # run_trigger_test.py) — descriptions are budget-capped by G11.
        corpus = m.group(1)
        rm = re.search(r"^## Routing \(full trigger corpus\)\n(.*?)(?=^## |\Z)",
                       text, re.S | re.M)
        if rm:
            corpus += "\n" + rm.group(1)
        pos, neg = extract_triggers(corpus)
        registry[skill_md.parent.name] = {"positive": pos, "negative": neg,
                                          "raw_desc": corpus}

    violations: list[str] = []
    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        rel = skill_md.relative_to(ROOT).as_posix()
        text = skill_md.read_text(encoding="utf-8")
        for m in SAY_RE.finditer(text):
            raw = next(g for g in m.groups() if g).strip()
            pre = text[max(0, m.start() - 60): m.start()].lower()
            # Example dialog / AI-speech contexts are not advertised commands.
            # NB: `pre` ends right where the say-token begins, so filter on
            # the SUBJECT before "say": example dialog ("if they say"),
            # negated speech ("don't say"), and non-user speakers ("notes
            # say", "the review can say") are not advertised commands.
            if any(k in pre for k in ("might", "user say", "if they", "if the",
                                       "e.g.", "example", "respond", "reply",
                                       "i say", "i'll", "says:", "you say",
                                       "instead of", "never", "don't", "do not",
                                       "notes", "review can", "better to",
                                       "email", "ack")):
                continue
            # Sentence-like fragments and cut-off apostrophe captures.
            if raw.endswith((".", ",")) or raw.endswith(" didn") or raw.startswith(("huh", "oh ")):
                continue
            # In-flow reply syntax (#N item refs, <angle> placeholders) is
            # dispatched by the surrounding flow, not the router.
            if "#" in raw or "<" in raw:
                continue
            phrase = neutralize(raw)
            norm = normalize(phrase)
            if not norm or norm in NON_COMMANDS:
                continue
            if len(norm.split()) <= REPLY_WORDS_MAX:
                continue
            # Self-advertisement: a skill quoting its own trigger (often with
            # [bracket] params, which never match mechanically — the LLM
            # router handles them) routes to itself by construction. Verified
            # by literal presence in the skill's own trigger description.
            own = registry.get(skill_md.parent.name, {"raw_desc": ""})
            if raw and raw in own.get("raw_desc", ""):
                continue
            matched = [
                name for name, tr in registry.items()
                if skill_matches(phrase, tr["positive"], tr["negative"])
            ]
            if not matched:
                line_no = text.count("\n", 0, m.start()) + 1
                violations.append(f'{rel}:{line_no}: advertises "{raw}" — routes NOWHERE')

    if violations:
        print(f"FAIL — {len(violations)} dead advertised command(s):\n")
        for v in violations:
            print(f"  ✗ {v}")
        print("\nAdd the phrase to the owning skill's trigger list, or stop advertising it.")
        return 1
    print("OK — every advertised command routes to a real trigger")
    return 0


if __name__ == "__main__":
    sys.exit(main())
