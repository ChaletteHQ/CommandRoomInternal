#!/usr/bin/env python3
"""SPEC A6 — the entity-resolution ladder is explained in exactly ONE place.

Blocking guard. The 3-tier ladder (exact alias -> fuzzy >=0.85 -> Soundex
phonetic 0.75) lives only in `shared/ENTITY_RESOLVE_PROTOCOL.md` (+ the .py +
tests + CHANGELOG + references/HISTORY). Any skill that re-explains the ladder
inline is the source-of-truth split this consolidation treats. Detection is by
ladder-anatomy SIGNATURES (Soundex, tier arrows, 85%/0.85, 3-tier), never the
bare word "fuzzy" (legit elsewhere: task-name fuzzy matching, ingest dedup).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

# Ladder-anatomy signatures — allowed ONLY in the canonical doc / code / tests /
# CHANGELOG / references/HISTORY (none of which are under the skills/*.md scan).
SIGNATURES = [
    re.compile(r"Soundex", re.IGNORECASE),
    re.compile(r"exact[ _]alias\s*(?:→|->|>)\s*fuzzy", re.IGNORECASE),
    # 85%/0.85 only counts as ladder anatomy when it qualifies "fuzzy" — a bare
    # confidence threshold (e.g. people-crm auto-apply >=0.85) is legitimate.
    re.compile(r"fuzzy[^.\n]{0,30}(?:≥\s*85\s*%|>=\s*0?\.85|≥\s*0?\.85)", re.IGNORECASE),
    re.compile(r"phonetic.*?(?:0\.75|0\.65)", re.IGNORECASE),
    re.compile(r"3-tier (?:ladder|match ladder|resolver)|3-tier:", re.IGNORECASE),
]

# query-first backwards-order regression on the resolver entrypoints.
BACKWARDS = re.compile(r"\bresolve(?:_all|_to_linked_project)?\(\s*query\b")

_failures = []


def check(name, cond, detail=""):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name + (f" :: {detail}" if detail else ""))


def _scan_md_files():
    for p in SKILLS_DIR.rglob("*.md"):
        # references/HISTORY.md is the allowed home for bug-narration anatomy.
        if p.name == "HISTORY.md" and p.parent.name == "references":
            continue
        yield p


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 1. No inline ladder re-explanation under skills/.
    sig_hits = []
    back_hits = []
    for p in _scan_md_files():
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if any(rx.search(line) for rx in SIGNATURES):
                sig_hits.append(f"{p.relative_to(ROOT)}:{i}")
            if BACKWARDS.search(line):
                back_hits.append(f"{p.relative_to(ROOT)}:{i}")
    check("no inline ladder-anatomy signatures under skills/", not sig_hits,
          "; ".join(sig_hits[:8]))
    check("no backwards resolve(query, ...) signature under skills/", not back_hits,
          "; ".join(back_hits[:8]))

    # 2. Coverage: any skill that USES the resolver must cite the protocol doc.
    miss = []
    for p in SKILLS_DIR.glob("*/SKILL.md"):
        text = p.read_text(encoding="utf-8")
        uses_resolver = "resolve_all(" in text or "entity_resolve.py" in text or "entity_resolve import" in text
        if uses_resolver and "ENTITY_RESOLVE_PROTOCOL" not in text:
            miss.append(p.parent.name)
    check("every resolver-using skill cites ENTITY_RESOLVE_PROTOCOL.md", not miss,
          ", ".join(miss))

    # 3. The canonical doc carries the decision table + the corrected 0.75 phonetic conf.
    doc = (ROOT / "shared" / "ENTITY_RESOLVE_PROTOCOL.md").read_text(encoding="utf-8")
    check("protocol doc has the decision table", "decision table" in doc.lower())
    check("protocol doc phonetic conf reads 0.75 (matches code), not 0.65", "0.65" not in doc)

    print()
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print("  - " + f)
        return 1
    print("ALL resolver_reference tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
