#!/usr/bin/env python3
"""Jargon-guard pre-commit test (v3.14.4+).

Scans customer-facing surfaces in plugin source for schema / migration / JSON /
implementation-detail vocabulary that non-technical customers shouldn't see, and
for "type this phrase" shapes in surfaces where the system should be doing the
thing on the customer's behalf (per CONTRACT.md Rule 28).

Scope:
- All `shared/releases/v*.json` manifest items, scanning their `prompt_template`
  and `notice_template` fields.

Rules applied per item.action:

| action          | word-level jargon | "type X" shape |
|-----------------|-------------------|----------------|
| announce_only   | FAIL              | FAIL           |
| auto_apply      | FAIL              | FAIL           |
| instruct_user   | FAIL              | ALLOW (the whole point is to tell the customer what to type) |

Exit non-zero on any violation. Pre-commit hook + ship-cr-plugin pre-commit
gate both invoke this script.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RELEASES_DIR = ROOT / "shared" / "releases"


# Word-level jargon — high-confidence ban patterns. Case-insensitive.
# Conservative: every entry here is a hard ban on customer-facing surfaces.
# False-positive-prone words (substrate, orchestrator, backfill, quarantine
# used as common English) are NOT banned outright — only their plumbing-
# specific shapes are caught below.
JARGON_PATTERNS = [
    # File / schema vocabulary — never appropriate in a customer surface
    r"\bevents\.jsonl\b",
    r"\bentities\.json\b",
    r"\baliases\.json\b",
    r"\bworkspace_config\b",
    r"\bschema\b",
    r"\benum\b",
    # System internals — implementation detail, never customer-relevant
    r"\bMCP\b",
    r"\bmcp__",
    r"\bbootloader\b",
    r"\bdispatcher\b",
    r"\bpredicate\b",
    # Action-type literals (manifest implementation labels)
    r"\binstruct_user\b",
    r"\bauto_apply\b",
    r"\bannounce_only\b",
    # Internal taskId / filenames
    r"\borchestrator-[a-z-]+\.md\b",
    r"\bcr-[a-z-]+-pulse\b",
    r"\bcr-[a-z-]+-nudge\b",
]


# Plumbing-instruction patterns — these are the "type this phrase to do a
# migration/recovery/backfill" shapes that v3.14.4 bans. Customer should
# never have to type these; the system should be doing them via auto_apply.
# Allowed for instruct_user (the whole point), banned for announce_only +
# auto_apply.
PLUMBING_INSTRUCTION_PATTERNS = [
    r"\brun recovery\b",
    r"\brun (?:the )?(?:wrapper )?backfill\b",
    r"\brun (?:the )?migration\b",
    r"\bapply (?:the )?migration\b",
    r"\bre-?fire\b",
    r"\bre-?register\s+(?:your\s+)?(?:tasks|schedules|scheduled chats)\b",
    r"\bset up command room schedules\b",
    r"\brepair (?:my|your)\s+(?:activity log|substrate)\b",
]


def _scan_text(text: str, patterns: list[str]) -> list[str]:
    """Return list of pattern strings that matched the text."""
    hits = []
    for pat in patterns:
        if re.search(pat, text, flags=re.IGNORECASE):
            hits.append(pat)
    return hits


def scan_manifests() -> list[str]:
    """Walk every shared/releases/v*.json manifest and report jargon hits.

    Returns a list of human-readable violation strings.
    """
    violations = []
    if not RELEASES_DIR.exists():
        return violations

    for manifest_path in sorted(RELEASES_DIR.glob("v*.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            violations.append(f"{manifest_path.name}: malformed JSON — {e}")
            continue

        for item in data.get("items", []):
            item_id = item.get("id", "<unknown>")
            action = item.get("action", "<unknown>")

            # Pick the surface field per action type
            surface_fields = []
            if action == "auto_apply":
                if "notice_template" in item:
                    surface_fields.append(("notice_template", item["notice_template"]))
                if "fallback_prompt_template" in item:
                    surface_fields.append(("fallback_prompt_template", item["fallback_prompt_template"]))
            elif action in ("announce_only", "instruct_user"):
                if "prompt_template" in item:
                    surface_fields.append(("prompt_template", item["prompt_template"]))

            for field_name, text in surface_fields:
                # Word-level jargon — banned for ALL action types
                jargon_hits = _scan_text(text, JARGON_PATTERNS)
                for hit in jargon_hits:
                    violations.append(
                        f"{manifest_path.name} :: item `{item_id}` :: {field_name} :: "
                        f"jargon pattern /{hit}/ matched (forbidden in customer surfaces)"
                    )

                # Plumbing-instruction shapes — banned for announce_only and
                # auto_apply (system should be doing the thing). Allowed for
                # instruct_user (assistant name, workspace shape, etc.).
                if action in ("announce_only", "auto_apply"):
                    shape_hits = _scan_text(text, PLUMBING_INSTRUCTION_PATTERNS)
                    for hit in shape_hits:
                        violations.append(
                            f"{manifest_path.name} :: item `{item_id}` :: {field_name} :: "
                            f"plumbing-instruction /{hit}/ matched — action='{action}' should NOT "
                            f"tell the customer to run a migration/recovery/registration phrase; "
                            f"the system should be doing it via auto_apply"
                        )

    return violations


def main() -> int:
    violations = scan_manifests()
    if not violations:
        print("OK — no jargon or asking-shapes in customer-facing manifest surfaces")
        return 0

    print("FAIL — customer-facing surfaces contain jargon or asking-shapes:\n")
    for v in violations:
        print(f"  {v}")
    print(f"\nTotal: {len(violations)} violation(s)")
    print(
        "\nCONTRACT.md Rule 28: customer-facing prompt_template / notice_template fields\n"
        "MUST NOT contain schema / migration / JSON / implementation-detail vocabulary,\n"
        "and announce_only / auto_apply items MUST NOT ask the customer to type a phrase.\n"
        "If the question can be resolved by the system, resolve it (convert to auto_apply).\n"
        "If it must be asked, ask in plain English with concrete examples (and only via\n"
        "instruct_user). See references/RELEASE_MANIFEST.md 'Action types' for the contract."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
