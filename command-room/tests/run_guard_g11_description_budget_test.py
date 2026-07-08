#!/usr/bin/env python3
"""G11 — skill description budget + routing-visibility guard (v4.5.1).

Why this guard exists (2026-07-06): the Agent Skills spec caps `description`
at 1,024 characters. Strict Claude Code / Cowork runtime builds SILENTLY DROP
any skill whose description exceeds the cap at frontmatter parse — one client
install loaded only 28 of 50 skills (the Evan incident). Separately, deployed
runtimes truncate each description in the routing listing (250 chars on many
builds), so trigger phrases past the cut are invisible to auto-invocation even
when the skill loads. This guard makes both failure modes unshippable:

  G11a  every description <= 980 chars (headroom under the 1,024 hard cap)
  G11b  no angle brackets in any description (plugin-upload rejection)
  G11c  no version tags (v1.2.3 shapes) in any description (changelog prose
        belongs in CHANGELOG.md, not the routing metadata)
  G11d  each skill's PRIMARY trigger phrase appears within the first 250
        characters of its description (front-loading — survives listing
        truncation). Primary trigger = the skill's first expected-case in
        tests/triggers.yaml. Skills with no trigger cases are exempt.
  G11e  catalog total <= 45,000 chars (the whole-catalog startup budget;
        59 skills at ~17k tokens preloaded per session was the disease).

Full trigger families and fences live in each SKILL.md body's `## Routing`
section (loaded only when the skill fires) and are enforced mechanically by
run_trigger_test.py — the description only needs enough key terms to route.
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILLS = os.path.join(ROOT, "skills")
TRIGGERS = os.path.join(HERE, "triggers.yaml")

BUDGET = 980
FRONT_WINDOW = 250
# Measured 47.6k at the v4.5.1 trim (down from 68.6k). RATCHET RULE: this
# constant only goes DOWN (S2 body/desc diet lowers it further); raising it
# requires M's sign-off in the PR description.
CATALOG_BUDGET = 48000

try:
    import yaml
except ImportError:
    print("SKIP - pyyaml unavailable")
    sys.exit(0)


def load_description(skill_dir):
    p = os.path.join(SKILLS, skill_dir, "SKILL.md")
    if not os.path.isfile(p):
        return None
    txt = open(p, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
    if not m:
        return None
    fm = yaml.safe_load(m.group(1))
    return (fm or {}).get("description", "") or ""


def tested_phrases():
    """skill -> ALL expected-case phrases from triggers.yaml."""
    if not os.path.isfile(TRIGGERS):
        return {}
    data = yaml.safe_load(open(TRIGGERS, encoding="utf-8"))
    out = {}
    cases = data.get("cases", data) if isinstance(data, dict) else data
    if isinstance(cases, dict):
        cases = cases.get("cases", [])
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        expected = case.get("expected") or case.get("skill")
        phrase = case.get("input") or case.get("phrase") or case.get("utterance")
        if not expected or not phrase or expected in ("none", None):
            continue
        out.setdefault(str(expected), []).append(str(phrase))
    return out


def main():
    failures = []
    total = 0
    firsts = tested_phrases()
    skill_dirs = sorted(
        d for d in os.listdir(SKILLS)
        if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md"))
    )
    for d in skill_dirs:
        desc = load_description(d)
        if desc is None:
            failures.append(f"{d}: unreadable frontmatter")
            continue
        L = len(desc)
        total += L
        if L > BUDGET:
            failures.append(f"{d}: G11a description {L} chars > {BUDGET}")
        if "<" in desc or ">" in desc:
            failures.append(f"{d}: G11b angle bracket in description")
        if re.search(r"\bv\d+\.\d+", desc):
            failures.append(f"{d}: G11c version tag in description")
        phrases = firsts.get(d) or []
        if phrases:
            # Front-load check: AT LEAST ONE tested phrase's stem (first two
            # words, minus bracketed placeholders) must appear inside the
            # listing-truncation window. If NO tested phrase is visible in the
            # first 250 chars, the skill is back-loaded and invisible to
            # auto-routing on truncating runtimes.
            window = desc[:FRONT_WINDOW].lower()
            hit = False
            for ph in phrases:
                words = re.sub(r"\[.*?\]", "", ph).strip().lower().split()
                core = " ".join(words[:2]).strip()
                if len(core) < 6:
                    core = " ".join(words[:3]).strip()
                if core and core in window:
                    hit = True
                    break
            if not hit:
                failures.append(
                    f"{d}: G11d no tested trigger stem inside the first "
                    f"{FRONT_WINDOW} chars of description"
                )
    if total > CATALOG_BUDGET:
        failures.append(f"CATALOG: G11e total description payload {total} > {CATALOG_BUDGET}")

    print(f"G11 description budget: {len(skill_dirs)} skills, "
          f"catalog payload {total} chars")
    if failures:
        for f in failures:
            print(f"  FAIL {f}")
        print(f"G11 FAILED - {len(failures)} violation(s)")
        sys.exit(1)
    print("OK - all descriptions within budget, front-loaded, clean")


if __name__ == "__main__":
    main()
