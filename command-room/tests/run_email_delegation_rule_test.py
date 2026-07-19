#!/usr/bin/env python3
"""
SPEC EW1 guard — the email-delegation hard rule stays present on all four
surfaces that close the mid-turn email-draft bypass (Bug #104):

  1. references/claude-md-template.md — the two Session Rules bullets (the
     workspace CLAUDE.md is the ONE surface that binds every turn, including
     freelance mid-task turns no skill fired on).
  2. skills/command-room-update-bridge/SKILL.md — the `claude_md_email_rule_v1`
     migration (existing installs), registered in the Phase 4.5 registry with
     apply-once semantics.
  3. shared/CONTRACT.md — Rule 30 (email composition is email-writer's
     monopoly) + shared/EMAIL_DRAFT_PROTOCOL.md sub-step scope sentence.
  4. skills/email-writer/SKILL.md — the chained-invocation section.

Also asserts the rule TEXT itself is shippable: provider-clean (no mail
provider or provider-tool names — the rule must hold on any backend and pass
the connector-agnostic gate) and free of internal vocabulary (the customer
reads this text in their own CLAUDE.md every session).

Honesty note: this test enforces PRESENCE of the standing instruction, not
runtime behavior — same-turn mechanical enforcement is not reachable (SPEC
GATE2 §2e); check-deliverables remains the detection story.

Run via: python3 tests/run_email_delegation_rule_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

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


MARKER = "goes through the **email-writer** skill, end to end"
BULLET_2_STEM = "Never write email text directly with a mail connector tool"
MIGRATION_ID = "claude_md_email_rule_v1"

TEMPLATE = ROOT / "references" / "claude-md-template.md"
BRIDGE = ROOT / "skills" / "command-room-update-bridge" / "SKILL.md"
CONTRACT = ROOT / "shared" / "CONTRACT.md"
PROTOCOL = ROOT / "shared" / "EMAIL_DRAFT_PROTOCOL.md"
EMAIL_WRITER = ROOT / "skills" / "email-writer" / "SKILL.md"


def session_rules_block(template_text: str) -> str:
    """The ## Session Rules section inside the template block (up to the
    next ## heading)."""
    m = re.search(r"^## Session Rules\n(.*?)(?=^## )", template_text,
                  re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def rule_bullets(template_text: str) -> str:
    """Just the two EW1 bullets, for the cleanliness checks."""
    lines = [ln for ln in session_rules_block(template_text).splitlines()
             if MARKER in ln or BULLET_2_STEM in ln]
    return "\n".join(lines)


# ---------------------------------------------------------------- 1. template
print("1. Template — Session Rules carry the rule")
template_text = TEMPLATE.read_text(encoding="utf-8")
sr = session_rules_block(template_text)
check("template has a ## Session Rules section", bool(sr))
check("Session Rules contain the idempotency marker phrase", MARKER in sr)
check("Session Rules contain the never-compose-directly bullet",
      BULLET_2_STEM in sr)
check("marker appears exactly once in the template (no double-append)",
      template_text.count(MARKER) == 1)

# ------------------------------------------------- 2. rule text is shippable
print("2. Rule text — provider-clean + internal-vocabulary-clean")
bullets = rule_bullets(template_text)
check("extracted both rule bullets from the template",
      MARKER in bullets and BULLET_2_STEM in bullets)

# Provider tokens: the rule must be backend-neutral (connector-agnostic gate 1
# class — never name a provider, a provider tool, or a mail host in prose).
PROVIDER_TOKENS = [
    "gmail", "outlook", "superhuman", "zapier", "microsoft", "google",
    "create_draft", "send_draft", "gmail_send_email", "mcp__",
]
for tok in PROVIDER_TOKENS:
    check(f"rule text has no provider token: {tok!r}",
          tok.lower() not in bullets.lower())

# Internal vocabulary: the customer reads this text every session. Reuse the
# one-owner list from vocabulary_policy when importable; else a minimal inline
# fallback so the check never silently vanishes.
try:
    from vocabulary_policy import internal_vocab_patterns  # noqa: E402
    vocab = [(tid, re.compile(rx, re.IGNORECASE))
             for tid, rx in internal_vocab_patterns()]
    check("vocabulary_policy imported (one-owner token list)", True)
except Exception:  # pragma: no cover — fallback keeps the guard alive
    vocab = [(w, re.compile(re.escape(w), re.IGNORECASE))
             for w in ("substrate", "canonical renderer", "events.jsonl",
                       "entities.json", "orchestrator", "frontmatter",
                       "dispatch", "payload")]
    check("vocabulary_policy imported (one-owner token list)", False,
          "fell back to the inline list — investigate the import failure")
for tid, pat in vocab:
    if pat.search(bullets):
        check(f"rule text clean of internal vocab: {tid}", False,
              f"pattern {tid!r} matched the rule bullets")
        break
else:
    check("rule text clean of ALL internal-vocabulary patterns", True)

# ------------------------------------------------------------------ 3. bridge
print("3. Update bridge — migration registered")
bridge_text = BRIDGE.read_text(encoding="utf-8")
check(f"registry contains id {MIGRATION_ID!r}",
      f'id: "{MIGRATION_ID}"' in bridge_text)
check("registry entry carries the apply-once flag",
      "apply_once: true" in bridge_text)
check("registry entry's marker is the template's idempotency phrase",
      f'marker: "{MARKER}"' in bridge_text)
check("Phase 4.5 has the migration's apply section",
      f"### Migration: `{MIGRATION_ID}`" in bridge_text)
check("apply section carries both rule bullets verbatim",
      MARKER in bridge_text.split(f"### Migration: `{MIGRATION_ID}`")[-1]
      and BULLET_2_STEM in bridge_text.split(
          f"### Migration: `{MIGRATION_ID}`")[-1])
check("apply section carries the one-line update-summary notice",
      "every email now routes through your email-writer voice rules"
      in bridge_text)
check("detection logic documents the apply-once event check",
      "For `apply_once: true` migrations" in bridge_text)
check("Rule 6 notes the sanctioned new-section exception",
      "sanctioned exception" in bridge_text)

# ------------------------------------------- 4. contract + protocol + skill
print("4. CONTRACT Rule 30 + protocol scope + email-writer chained section")
contract_text = CONTRACT.read_text(encoding="utf-8")
check("CONTRACT.md contains Rule 30",
      re.search(r"^## Rule 30 — Email composition is email-writer's monopoly",
                contract_text, re.MULTILINE) is not None)
check("Rule 30 is honest about its enforcement status",
      "GUIDANCE at runtime" in contract_text)
check("Rule 30 names this test as the structural enforcement",
      "run_email_delegation_rule_test.py" in contract_text)

protocol_text = PROTOCOL.read_text(encoding="utf-8")
check("EMAIL_DRAFT_PROTOCOL scope covers sub-step drafts",
      "sub-steps of another skill" in protocol_text)
check("protocol sub-step sentence points at chaining email-writer",
      "chain email-writer" in protocol_text)

ew_text = EMAIL_WRITER.read_text(encoding="utf-8")
check("email-writer has the chained-invocation section",
      re.search(r"^## Chained invocation", ew_text, re.MULTILINE) is not None)
check("chained invocation skips trigger parsing only",
      "Skip trigger parsing" in ew_text)
check("chained invocation keeps entity-resolve + voice + widget flow",
      "entity-resolve" in ew_text.split("## Chained invocation")[-1]
      .split("## Writer Contract")[0]
      and "EMAIL_DRAFT_PROTOCOL" in ew_text.split("## Chained invocation")[-1]
      .split("## Writer Contract")[0])

# ------------------------------------------- 5. no frontmatter drift (EW1 §8)
print("5. No description/frontmatter changes (trigger surface untouched)")
fm = ew_text.split("---")[1] if ew_text.startswith("---") else ""
check("email-writer frontmatter does not mention chained invocation "
      "(EW1 is body-only; descriptions are the routing surface)",
      "chained" not in fm.lower() and "sub-step" not in fm.lower())

print()
print(f"Passed: {PASS}")
print(f"Failed: {FAIL}")
sys.exit(1 if FAIL else 0)
