#!/usr/bin/env python3
"""
Trigger Collision Test Harness

Loads every SKILL.md in plugin-source-v2/skills/ and extracts trigger
vocabulary from its frontmatter `description` field. Then runs every
phrase in tests/triggers.yaml against all skills and checks:

  PASS: exactly one skill matches, and it's the expected one.
  FAIL: zero skills match (missing trigger).
  FAIL: multiple skills match (collision).
  FAIL: one skill matched, but wrong skill (mis-triggered).

Usage:
    python tests/run_trigger_test.py

Exit codes:
    0 — all tests passed
    1 — one or more failures (details printed)

This is NOT a perfect model of Claude's real trigger-matching behavior
(which uses LLM-level description-matching). It's a heuristic that
catches the obvious cases: overlapping literal triggers, missing verbs,
and negative-trigger violations. It will miss semantic-only matches.

For production discipline, run before every plugin version bump.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml required. Install with: pip install pyyaml --break-system-packages")
    sys.exit(2)


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
TRIGGERS_FILE = PLUGIN_ROOT / "tests" / "triggers.yaml"


# --------------------------------------------------------------------
# Frontmatter parsing
# --------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(skill_md_path: Path) -> dict:
    """Extract YAML frontmatter from a SKILL.md file."""
    text = skill_md_path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        print(f"  ⚠️  YAML parse error in {skill_md_path}: {e}")
        return {}


# --------------------------------------------------------------------
# Trigger extraction
# --------------------------------------------------------------------

# Heuristic: pull quoted trigger phrases out of the description.
# SKILL.md descriptions wrap example triggers in single or double quotes.
# Backticks are intentionally NOT matched — they're used liberally for skill-name
# references, task IDs, file paths, code identifiers, and management verbs that
# aren't user-facing triggers. Convention across the plugin is single quotes for
# trigger phrases; the 9 outlier skills that wrapped trigger phrases in backticks
# pre-v3.11.2 were converted in v3.11.2 to match the convention.
#
# Apostrophe-aware matching:
#   - Opening single-quote must be preceded by non-alphanumeric (or start of string)
#     — so contractions like `wouldn't` and digit-apostrophes like `Chat 1's`
#     don't get interpreted as opening a quote.
#   - Inner content may contain `'<letter>` (e.g., `what's`, `let's`) without
#     terminating the trigger.
#   - Closing quote must be followed by non-letter (or end of string).
#
# v3.4.2 fix: added `0-9` to the opening-quote lookbehind exclusion. Previously
# `Chat 1's later steps` in the onboarding skill description caused the regex
# to treat `1's` as an opening quote, consuming everything up to the actual
# trigger list and missing `'set up command room'`. The false negative
# manifested as a trigger-test failure on "set up command room" with no actual
# production impact (Cowork's LLM router does semantic matching, not regex),
# but it polluted the test signal.
TRIGGER_QUOTE_RE = re.compile(
    r"(?<![a-zA-Z0-9])'([^']*(?:'[a-zA-Z][^']*)*)'(?![a-zA-Z])"
    r"|\"([^\"]{2,80})\""
)

# Negative triggers — described as "does NOT fire on", "DOES NOT fire on", etc.
NEGATIVE_RE = re.compile(
    r"(?:does\s*not\s*(?:trigger|fire)\s*on|DO\s*NOT\s*trigger\s*on|NOT\s*fire\s*on)\s*[:\-]?\s*(.{0,400})",
    re.IGNORECASE,
)


def _findall_triggers(text: str) -> list[str]:
    """Return list of trigger strings found in text (handles both quote styles)."""
    out = []
    for m in TRIGGER_QUOTE_RE.finditer(text):
        phrase = m.group(1) if m.group(1) is not None else m.group(2)
        if phrase and 2 <= len(phrase) <= 80:
            out.append(phrase)
    return out


def extract_triggers(description: str) -> tuple[list[str], list[str]]:
    """
    Return (positive_triggers, negative_triggers) for a skill.

    Positive triggers: quoted phrases we treat as owned by this skill.
    Negative triggers: quoted phrases this skill explicitly disclaims.
    """
    if not description:
        return [], []

    # Find a "does not fire on" clause; triggers inside it are negative.
    neg_clauses = NEGATIVE_RE.findall(description)
    negative = []
    for clause in neg_clauses:
        negative.extend(_findall_triggers(clause))

    # Positive triggers = all quoted phrases minus any that appear in the
    # negative clauses.
    all_quoted = _findall_triggers(description)
    positive = [t for t in all_quoted if t not in negative]

    return (
        [normalize(t) for t in positive],
        [normalize(t) for t in negative],
    )


def normalize(phrase: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation for matching."""
    p = phrase.lower().strip()
    p = re.sub(r"[^\w\s]", " ", p)
    p = re.sub(r"\s+", " ", p)
    return p.strip()


# --------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------

def _word_contains(haystack: str, needle: str) -> bool:
    """Return True if `needle` appears in `haystack` with word boundaries."""
    if not needle:
        return False
    pattern = r"\b" + re.escape(needle) + r"\b"
    return bool(re.search(pattern, haystack))


def skill_matches(phrase: str, positive: list[str], negative: list[str]) -> bool:
    """
    Does `phrase` match this skill?

    Rule: phrase matches if ANY positive trigger is contained (word-boundary)
    in the phrase, AND no negative trigger is contained in the phrase.

    This models Claude's real trigger discovery more tightly than raw substring
    matching: `trigger ⊆ phrase` (trigger is fully contained). A phrase shorter
    than a trigger does NOT count as a match.
    """
    p = normalize(phrase)

    for neg in negative:
        if _word_contains(p, neg):
            return False

    for pos in positive:
        if _word_contains(p, pos):
            return True

    return False


# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------

def main() -> int:
    # Load all skills
    skills = {}
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        folder = skill_md.parent.name
        # Skip deprecated folders
        if folder.startswith("_deprecated"):
            continue
        fm = parse_frontmatter(skill_md)
        desc = fm.get("description", "")
        # v4.5.1: descriptions are budget-capped (G11) and carry only the
        # front-loaded stems; the FULL trigger corpus lives in the body's
        # '## Routing (full trigger corpus)' section. The mechanical matcher
        # reads BOTH — the runtime router matches semantically on the
        # description, while the declared family (and its fences) is the
        # description + Routing section together.
        body = skill_md.read_text(encoding="utf-8")
        rm = re.search(r"^## Routing \(full trigger corpus\)\n(.*?)(?=^## |\Z)",
                       body, re.S | re.M)
        corpus = desc + ("\n" + rm.group(1) if rm else "")
        pos, neg = extract_triggers(corpus)
        skills[folder] = {"positive": pos, "negative": neg}

    print(f"Loaded {len(skills)} skills.\n")

    # Load test cases
    if not TRIGGERS_FILE.exists():
        print(f"ERROR: {TRIGGERS_FILE} not found.")
        return 2
    cases = yaml.safe_load(TRIGGERS_FILE.read_text(encoding="utf-8"))

    # Run each case
    passed = 0
    failed = []

    for case in cases:
        phrase = case["phrase"]
        expected = case["expected"]

        matches = [
            name for name, tr in skills.items()
            if skill_matches(phrase, tr["positive"], tr["negative"])
        ]

        # P2 2026-07-02: `expected: none` asserts NO skill fires — used for
        # deliberately-unowned utterances (calendar reads, small talk,
        # out-of-scope requests). A match here is a hijack regression.
        if expected in ("none", None):
            if len(matches) == 0:
                passed += 1
            else:
                failed.append({
                    "phrase": phrase, "expected": "none",
                    "reason": f"HIJACK: {matches[0]} fired on an unowned utterance",
                    "matched": matches,
                })
            continue

        if len(matches) == 0:
            failed.append({
                "phrase": phrase, "expected": expected,
                "reason": "NO SKILL MATCHED (missing trigger)",
                "matched": [],
            })
        elif len(matches) > 1:
            failed.append({
                "phrase": phrase, "expected": expected,
                "reason": f"COLLISION: {len(matches)} skills matched",
                "matched": matches,
            })
        elif matches[0] != expected:
            failed.append({
                "phrase": phrase, "expected": expected,
                "reason": f"MIS-TRIGGERED: matched {matches[0]}, expected {expected}",
                "matched": matches,
            })
        else:
            passed += 1

    # Report
    total = len(cases)
    print(f"Passed: {passed}/{total}")
    print(f"Failed: {len(failed)}/{total}\n")

    if failed:
        print("=" * 70)
        print("FAILURES")
        print("=" * 70)
        for f in failed:
            print(f"\n  Phrase: {f['phrase']!r}")
            print(f"  Expected: {f['expected']}")
            print(f"  Reason: {f['reason']}")
            if f["matched"]:
                print(f"  Matched: {', '.join(f['matched'])}")
        print("\n" + "=" * 70)
        return 1

    print("All trigger tests passed. ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
