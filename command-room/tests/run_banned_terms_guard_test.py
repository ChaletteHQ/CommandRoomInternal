#!/usr/bin/env python3
"""
v4.6.1 S3 guard — internal vocabulary can never ship on the two
customer-visible prose surfaces no other scanner watched (FINDINGS
F-05: "Internal dispatch layer ... payload ..." rendered verbatim in
the Plugins-UI skills list; the F-14 pile: substrate / canonical
renderer / audit marker narrated in chat).

The greppable banned-terms list is `INTERNAL_VOCAB` in
shared/scripts/vocabulary_policy.py — ONE owner; chat_output_validator
enforces the same list on rendered chat at runtime; this guard makes a
regression unshippable at battery time. Surfaces scanned:

  1. Every skill's frontmatter `description` (Cowork renders these to
     the customer in the Plugins UI — the F-05 surface).
  2. Every release manifest's headline / prompt_template /
     notice_template (the update-bridge speaks these to the customer).
     Manifests are also scanned for the shared MARKETING_WORDS (a
     product announcement saying "leverage" fails the same voice the
     gates enforce on drafts).

Deliberately NOT scanned here: SKILL.md body prose (internal
instructions legitimately use this vocabulary to talk TO the model)
and chat output (chat_output_validator's job at render time).

Run via: python3 tests/run_banned_terms_guard_test.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from vocabulary_policy import (  # noqa: E402
    INTERNAL_VOCAB,
    MARKETING_WORDS,
    internal_vocab_patterns,
)

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")


_VOCAB = [(tid, re.compile(rx, re.IGNORECASE))
          for tid, rx in internal_vocab_patterns()]
_MARKETING = [(w, re.compile(rf"\b{w}", re.IGNORECASE))
              for w in MARKETING_WORDS]


def frontmatter_description(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    m = re.search(r"^description:\s*(.+?)^(?:---|\w+:)", text, re.S | re.M)
    return m.group(1) if m else ""


def hits(text: str, patterns) -> list:
    return [(tid, p.search(text).group(0)) for tid, p in patterns if p.search(text)]


def main() -> int:
    # ------------------------------------------------------------------
    print("[1] the list itself — evidence-driven, importable, greppable")
    # ------------------------------------------------------------------
    check("INTERNAL_VOCAB is non-empty and every row carries evidence",
          len(INTERNAL_VOCAB) >= 8
          and all(len(row) == 3 and row[2] for row in INTERNAL_VOCAB))
    for needle in ("substrate", "dispatch_layer", "payload",
                   "canonical_machinery", "audit_marker", "run_summary_tag",
                   "bare_seq_ref"):
        check(f"list covers {needle}", any(t == needle for t, _ in _VOCAB))

    # ------------------------------------------------------------------
    print("[2] skill descriptions (the F-05 Plugins-UI surface) are clean")
    # ------------------------------------------------------------------
    skill_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    check("found the skill set", len(skill_files) >= 50, str(len(skill_files)))
    dirty = 0
    for f in skill_files:
        desc = frontmatter_description(f)
        found = hits(desc, _VOCAB)
        if found:
            dirty += 1
            check(f"description clean: {f.parent.name}", False, str(found))
    check("every skill description is free of internal vocabulary",
          dirty == 0, f"{dirty} dirty")
    # the F-05 offender specifically, plus its replacement framing
    ac = frontmatter_description(ROOT / "skills" / "apply-choices" / "SKILL.md")
    check("apply-choices no longer opens 'Internal dispatch layer'",
          "Internal dispatch layer" not in ac)
    check("apply-choices keeps its exact-prefix trigger contract",
          "'apply choices: '" in ac and "never on natural language" in ac)
    check("apply-choices tells the customer they never type it",
          "you never need to type this" in ac)

    # ------------------------------------------------------------------
    print("[3] release manifests (the update-bridge's spoken surface) are clean")
    # ------------------------------------------------------------------
    manifests = sorted((ROOT / "shared" / "releases").glob("*.json"))
    check("found release manifests", len(manifests) >= 10, str(len(manifests)))
    dirty = 0
    for f in manifests:
        doc = json.loads(f.read_text(encoding="utf-8"))
        surfaces = [doc.get("headline") or ""]
        for it in doc.get("items", []):
            surfaces.append(it.get("prompt_template") or "")
            surfaces.append(it.get("notice_template") or "")
        blob = "\n".join(surfaces)
        found = hits(blob, _VOCAB) + hits(blob, _MARKETING)
        if found:
            dirty += 1
            check(f"manifest clean: {f.name}", False, str(found))
    check("every manifest surface is free of internal + marketing vocabulary",
          dirty == 0, f"{dirty} dirty")

    # ------------------------------------------------------------------
    print("[4] one owner — the runtime validator reads the same list")
    # ------------------------------------------------------------------
    from chat_output_validator import PATTERNS
    validator_rx = {rx for cat, rx, _ in PATTERNS if cat == "internal_vocab_leak"}
    policy_rx = {rx for _, rx in internal_vocab_patterns()}
    check("chat_output_validator's internal_vocab_leak rows == the policy list",
          validator_rx == policy_rx,
          f"validator-only: {validator_rx - policy_rx}; "
          f"policy-only: {policy_rx - validator_rx}")

    # ------------------------------------------------------------------
    print(f"\n=== Summary: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        print("FAIL — internal vocabulary reached a customer surface")
        return 1
    print("OK — banned-terms guard holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
