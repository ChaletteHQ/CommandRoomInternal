#!/usr/bin/env python3
"""
T2.2 guard — the widget-transport session rules stay present on both surfaces
that close the FS-08/RV-1/RV-2 hand-composition + text-instead-of-widget
bypass (the layer that turned the F2 gate GREEN live, RV-3):

  1. references/claude-md-template.md — the two Session Rules bullets (the
     workspace CLAUDE.md is the ONE surface that reliably binds the runtime —
     the Bug #104 / EW1 precedent; skill-text mandates alone kept losing).
  2. skills/command-room-update-bridge/SKILL.md — the `claude_md_widget_rule_v1`
     migration (existing installs), registered in the Phase 4.5 registry with
     apply-once + silent_append semantics, EXACTLY mirroring
     `claude_md_email_rule_v1`.

Also asserts the template bullets and the migration's appended block are
BYTE-IDENTICAL (drift between the fresh-install text and the back-fill text
would make the marker/idempotency story diverge per install path), and that
the rule text is provider-clean.

PROMOTE-BLOCKING context (RV-3): without this layer, client runtimes
improvise exactly as the dogfood workspace did pre-rule.

Run via: python3 tests/run_widget_rule_migration_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

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


MARKER = "passed to show_widget"
BULLET_1_STEM = "produced by `widget_transport.render_and_persist`"
BULLET_2_STEM = "is not optional"
MIGRATION_ID = "claude_md_widget_rule_v1"

TEMPLATE = ROOT / "references" / "claude-md-template.md"
BRIDGE = ROOT / "skills" / "command-room-update-bridge" / "SKILL.md"


def session_rules_block(template_text: str) -> str:
    m = re.search(r"^## Session Rules\n(.*?)(?=^## )", template_text,
                  re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def widget_bullets(text: str) -> list[str]:
    """The two widget-rule bullet LINES, in order."""
    out = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("- ") and (
                BULLET_1_STEM in ln
                or (BULLET_2_STEM in ln and "render_and_persist" in ln)):
            out.append(ln.strip())
    return out


# ---------------------------------------------------------------- 1. template
print("1. Template — Session Rules carry both widget rules")
template_text = TEMPLATE.read_text(encoding="utf-8")
sr = session_rules_block(template_text)
check("template has a ## Session Rules section", bool(sr))
check("Session Rules contain the build-via-transport bullet",
      BULLET_1_STEM in sr)
check("Session Rules contain the show_widget-is-the-next-step bullet",
      BULLET_2_STEM in sr and 'transport["html"]' in sr)
check("bullet 2 names the two sanctioned text-instead-of-widget conditions",
      "over_budget" in sr and "transport itself failed" in sr)
check("idempotency marker present in the Session Rules", MARKER in sr)
tmpl_bullets = widget_bullets(sr)
check("exactly two widget-rule bullets in the template",
      len(tmpl_bullets) == 2, f"got {len(tmpl_bullets)}")

# ------------------------------------------------- 2. rule text is shippable
print("2. Rule text — provider/tool-host clean, generalized (no workspace-specific wording)")
bullets = "\n".join(tmpl_bullets)
for tok in ("gmail", "outlook", "superhuman", "zapier", "mcp__", "cowork"):
    check(f"rule text has no provider/host token: {tok!r}",
          tok.lower() not in bullets.lower())

# ------------------------------------------------------------------ 3. bridge
print("3. Update bridge — migration registered, EW1-mirrored")
bridge_text = BRIDGE.read_text(encoding="utf-8")
check(f"registry contains id {MIGRATION_ID!r}",
      f'id: "{MIGRATION_ID}"' in bridge_text)
# First occurrence = the WORKSPACE_MIGRATIONS registry entry; scope to its
# closing brace (the id also appears in detection prose + the Phase 4.5
# heading further down).
reg_entry = bridge_text.split(f'id: "{MIGRATION_ID}"', 1)[1].split("}")[0]
check("registry entry carries the apply-once flag",
      "apply_once: true" in reg_entry)
check("registry entry is a silent_append (no calibration question)",
      '"silent_append"' in reg_entry)
check("registry entry's marker is the idempotency phrase",
      f'marker: "{MARKER}"' in reg_entry)
check("Phase 4.5 has the migration's apply section",
      f"### Migration: `{MIGRATION_ID}`" in bridge_text)
apply_sec = bridge_text.split(f"### Migration: `{MIGRATION_ID}`")[-1]
check("apply section targets the Session Rules heading",
      "## Session Rules" in apply_sec)
check("apply section carries the missing-heading new-section exception",
      "sanctioned Rule-6 exception" in apply_sec
      or "sanctioned exception" in apply_sec)
check("apply section logs workspace_migration_applied",
      "workspace_migration_applied" in apply_sec)
check("apply section carries the one-line update-summary notice",
      "checked build path" in apply_sec)
check("apply section states the promote-blocking rationale",
      "promote-blocking" in apply_sec)
# FB-20 split the apply-once detection rule into TWO bullets: marker-gated
# migrations (this one — the marker check plus the adjudication gate) and
# marker-less ones (`staff_meeting_cadence_mwf_v1`, where live config can't be
# grepped so adjudication is the only gate). Anchor on the marker-gated bullet
# specifically — a bare `[-1]` split now lands on the marker-less bullet and
# silently asserts against the wrong enumeration.
check("detection logic lists the migration under apply-once semantics",
      MIGRATION_ID in bridge_text.split(
          "For `apply_once: true` migrations with a non-null `marker`")[-1]
      .split(":")[0])

# ------------------------------------- 4. template <-> migration byte parity
print("4. Template and migration append the SAME bullets (no drift)")
mig_bullets = widget_bullets(apply_sec)
check("migration apply section carries exactly two bullets",
      len(mig_bullets) == 2, f"got {len(mig_bullets)}")
check("bullet 1 byte-identical between template and migration",
      len(tmpl_bullets) > 0 and len(mig_bullets) > 0
      and tmpl_bullets[0] == mig_bullets[0])
check("bullet 2 byte-identical between template and migration",
      len(tmpl_bullets) > 1 and len(mig_bullets) > 1
      and tmpl_bullets[1] == mig_bullets[1])

print()
print(f"Passed: {PASS}")
print(f"Failed: {FAIL}")
sys.exit(1 if FAIL else 0)
