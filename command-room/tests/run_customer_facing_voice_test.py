#!/usr/bin/env python3
"""
Structural guard: customer-facing voice in release-manifest prose.

Strings in `shared/releases/v*.json` that get surfaced to customers via
the update-bridge — top-level `headline` and per-item `prompt_template`
fields — must be plain English in first-person AI voice. No internal
identifiers, file paths, module names, bug numbers, multi-decimal version
strings, or canonical-id leaks should appear in the prose the customer
actually reads.

Trigger phrases the customer types are allowed (anything inside backticks).
The literal default brain name "Penelope" is allowed. Everything else gets
the same plain-English-or-skip discipline as customer-facing render output.

Run from the command-room repo root:
    python3 tests/run_customer_facing_voice_test.py

Exits 0 on pass, 1 on any violations.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES_DIR = ROOT / "shared" / "releases"
SKILLS_DIR = ROOT / "skills"


# Files the customer directly edits during onboarding (workspace-root configs).
# Allowed in SKILL.md customer prose when the prose is literally about that
# file. NOT allowed in release-manifest prose (manifests should describe
# behavior, not file names).
CUSTOMER_EDITED_FILES = {"CLAUDE.md", "BUSINESS_CONTEXT.md", "WORKING_STYLE.md", "BRAND_VOICE.md"}


# Each rule is (compiled_pattern, label, hint_when_violated)
RULES = [
    # Internal identifiers — function names, event types, helper modules
    (
        re.compile(
            r"\b("
            r"atomic_append|atomic_write|atomic_append_jsonl|multi_write_context|"
            r"next_seq|cru_match|brief_writer|load_open_commitments|"
            r"format_customer_message|render_chat_output_widget|widget_transport|"
            r"brain_name_captured|brain_name_declined|brain_name_prompt|"
            r"workspace_setting_changed|wrapper_source_seq_backfill|"
            r"corruption_recovery|pack_run|recovery_marker|recovery_version|"
            r"contact_email_captured|followup_pack_drafted|commitment_superseded|"
            r"render_and_persist|render_decision_log|migrate_persons|"
            r"backfill_org_attribution|source_event_seq_backfill|"
            r"_validate_person|_validate_org|"
            r"has_legacy_wrappers|needs_brain_name_prompt|has_malformed_events"
            r")\b"
        ),
        "internal-identifier",
        "rewrite in plain English — what does the user experience differently?",
    ),
    # Module / script paths
    (
        re.compile(r"\b(release_detectors|shared/scripts)[./]\w+|\b\w+\.py\b"),
        "module-or-script-path",
        "drop the path — describe the behavior, not the file",
    ),
    # Internal data-file references in prose
    (
        re.compile(
            r"\b(events\.jsonl|entities\.json|aliases\.json|workspace_config\.json|scheduled_tasks\.json|classifier_feedback\.jsonl)\b"
        ),
        "data-file-name-leak",
        "say 'your activity log' / 'your workspace memory' — never the file name",
    ),
    # _hq/ + MASTER_TRACKER + PEOPLE.md + DECISION_LOG.md
    (
        re.compile(r"\b_hq/\w+|MASTER_TRACKER\.md|PEOPLE\.md|DECISION_LOG\.md|BRAND_VOICE\.md|CLAUDE\.md|BUSINESS_CONTEXT\.md|WORKING_STYLE\.md"),
        "internal-file-path",
        "drop the file path — describe what the customer sees instead",
    ),
    # Canonical ID format leaks
    (
        re.compile(r"\b(project|person|org|thread)_\d{3,}\b"),
        "canonical-id-leak",
        "use the entity's display name, never its internal id",
    ),
    # MCP tool ids
    (
        re.compile(r"\bmcp__\w+"),
        "mcp-tool-id-leak",
        "describe the capability, not the tool's internal id",
    ),
    # Bug references — fine internally, NOT in customer prose
    (
        re.compile(r"\b(Sub-?bug|Bug)\s*#\s*\d+", re.IGNORECASE),
        "bug-reference-leak",
        "customers don't track bug numbers — describe the symptom they noticed",
    ),
    # Spec / doc references
    (
        re.compile(r"§\s*\d+(\.\d+)*|master\s+plan\s+§?\s*\d+", re.IGNORECASE),
        "spec-section-reference",
        "customers don't read the spec — describe the change in user terms",
    ),
    # Multi-decimal version strings as referents in prose (e.g., "v3.13.8 — ...")
    # Allow versions inside backticks (covered by stripping) and inside the
    # manifest's top-level "version" field (not scanned here). Flag prose-level
    # mentions like "v3.13.8 added ..." or "pre-v3.13.8 ..."
    (
        re.compile(r"\bv?\d+\.\d+(\.\d+){1,3}\b"),
        "version-string-in-prose",
        "customers don't track plugin versions — say 'I added' / 'I tightened'",
    ),
    # Tier / Phase references (architectural jargon)
    (
        re.compile(r"\b(Tier\s+[12345]|Phase\s+\d+(\.\d+)?)\b"),
        "architectural-jargon",
        "drop tier / phase numbers — describe the user-visible change",
    ),
    # Known internal architectural nouns that leak into prose
    (
        re.compile(
            r"\b("
            r"freelance render|byte-relay|canonical reader|defensive read|"
            r"detector module|detector function|prompt template|"
            r"chrome\s+(Gate\s+\d+|recovery)|"
            r"file-URI|file:// URI"
            r")\b",
            re.IGNORECASE,
        ),
        "architectural-noun",
        "describe the user-visible effect, not the internal mechanism",
    ),
]


def strip_backticks(s: str) -> str:
    """Remove anything inside backticks — those are user-facing literals
    (trigger phrases, name placeholders the user types). They're allowed
    even if they look technical."""
    return re.sub(r"`[^`]*`", "", s)


def scan_string(s: str, *, allow_customer_edited_files: bool = False) -> list[tuple[str, str, str]]:
    """Return list of (label, matched_text, hint) violations in this string.
    Backtick-delimited substrings are stripped before scanning.

    `allow_customer_edited_files`: when True, file names in CUSTOMER_EDITED_FILES
    (CLAUDE.md, BUSINESS_CONTEXT.md, etc.) don't count as internal-file-path
    leaks — these are workspace-root configs the customer directly edits, so
    referring to them by name in customer-facing prose is fine. Used when
    scanning SKILL.md customer prose; NOT used when scanning release manifests
    (manifests should describe behavior, not file names)."""
    stripped = strip_backticks(s)
    out = []
    for pat, label, hint in RULES:
        for m in pat.finditer(stripped):
            matched = m.group(0)
            if (
                allow_customer_edited_files
                and label == "internal-file-path"
                and matched in CUSTOMER_EDITED_FILES
            ):
                continue
            out.append((label, matched, hint))
    return out


def extract_customer_blockquotes(text: str) -> list[str]:
    """Extract italicized prose blockquotes (the > *"..."* pattern) — these
    are the canonical customer-facing render examples in SKILL.md spec text.
    Strips code blocks first (those are LLM-internal spec, not customer prose).
    Returns a list of blockquote strings."""
    # Strip fenced code blocks first
    no_code = re.sub(r"```[\s\S]*?```", "", text)
    # Match runs of italic-blockquote lines (`> *"..."*` shape, possibly multi-line)
    blocks = re.findall(r'(?:^>\s*\*?\s*".+?"\s*\*?\s*\n?)+', no_code, re.MULTILINE)
    return blocks


def main() -> int:
    # Force UTF-8 stdout so the ✓/✗ status glyphs don't raise UnicodeEncodeError
    # on a Windows console (cp1252). Best-effort; never fatal.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not RELEASES_DIR.exists():
        print(f"FAIL — releases dir not found at {RELEASES_DIR}")
        return 1

    manifests = sorted(RELEASES_DIR.glob("v*.json"))
    if not manifests:
        print(f"FAIL — no release manifests found in {RELEASES_DIR}")
        return 1

    # Enforcement cutoff: manifests at or after this version must be clean
    # (FAIL on violation). Older manifests are grandfathered as warnings only.
    # As of the v3.13.8 voice sweep, every manifest in shared/releases/ has
    # been rewritten in plain-English first-person voice, so the cutoff is
    # 0 (enforce all). Raising the floor would suppress future regressions
    # on older manifests if they get touched again.
    ENFORCE_FROM = (0, 0, 0)

    def version_tuple(name: str) -> tuple[int, ...]:
        # "v3.13.8.1.json" -> (3, 13, 8, 1)
        m = re.match(r"v(\d+(?:\.\d+)*)\.json$", name)
        if not m:
            return ()
        return tuple(int(p) for p in m.group(1).split("."))

    errors: list[tuple[Path, str, str, str, str]] = []
    warnings: list[tuple[Path, str, str, str, str]] = []
    files_clean = 0
    files_scanned = 0

    for mpath in manifests:
        files_scanned += 1
        ver = version_tuple(mpath.name)
        enforced = ver >= ENFORCE_FROM
        try:
            data = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception as e:
            (errors if enforced else warnings).append(
                (mpath, "<parse>", "json-parse-error", str(e), "fix the manifest JSON")
            )
            continue

        file_violations = []

        # Top-level headline
        headline = data.get("headline", "")
        if isinstance(headline, str):
            for label, matched, hint in scan_string(headline):
                file_violations.append(("headline", label, matched, hint))

        # Per-item prompt_template
        for idx, item in enumerate(data.get("items", [])):
            iid = item.get("id", f"item[{idx}]")
            tmpl = item.get("prompt_template", "")
            if isinstance(tmpl, str):
                for label, matched, hint in scan_string(tmpl):
                    file_violations.append((f"items[{iid}].prompt_template", label, matched, hint))

        if file_violations:
            bucket = errors if enforced else warnings
            for path_to_field, label, matched, hint in file_violations:
                bucket.append((mpath, path_to_field, label, matched, hint))
        else:
            files_clean += 1

    # Second pass: scan SKILL.md italic blockquotes (customer-facing render examples).
    # Uses the same RULES but allows customer-edited workspace-root file names
    # (CLAUDE.md, BUSINESS_CONTEXT.md, etc. — customers explicitly edit those).
    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md")) if SKILLS_DIR.exists() else []
    skills_scanned = 0
    skills_clean = 0
    for sk in skill_files:
        skills_scanned += 1
        text = sk.read_text(encoding="utf-8")
        blocks = extract_customer_blockquotes(text)
        file_violations = []
        for block in blocks:
            for label, matched, hint in scan_string(block, allow_customer_edited_files=True):
                file_violations.append(("customer_blockquote", label, matched, hint))
        if file_violations:
            # SKILL.md violations always enforced (no grandfathering — we just swept these).
            for path_to_field, label, matched, hint in file_violations:
                errors.append((sk, path_to_field, label, matched, hint))
        else:
            skills_clean += 1

    enforce_str = ".".join(str(p) for p in ENFORCE_FROM)
    print(f"=== customer-facing voice check — {files_scanned} manifests + {skills_scanned} SKILL.md files scanned ===")

    # Group both for readable output
    def group_by_file(violations):
        out: dict[Path, list[tuple[str, str, str, str]]] = {}
        for mpath, field, label, matched, hint in violations:
            out.setdefault(mpath, []).append((field, label, matched, hint))
        return out

    if warnings:
        warn_by_file = group_by_file(warnings)
        print(f"\n  ⚠ {len(warnings)} grandfathered warning(s) across {len(warn_by_file)} pre-v{enforce_str} manifest(s):")
        for mpath, vlist in sorted(warn_by_file.items()):
            rel = mpath.relative_to(ROOT)
            print(f"    {rel}: {len(vlist)} issue(s)")

    if errors:
        err_by_file = group_by_file(errors)
        print(f"\n  ✗ {len(errors)} violation(s) across {len(err_by_file)} enforced manifest(s):")
        for mpath, vlist in sorted(err_by_file.items()):
            rel = mpath.relative_to(ROOT)
            print(f"\n    {rel}:")
            for field, label, matched, hint in vlist:
                print(f"      [{label}] {field}: {matched!r}")
                print(f"        → {hint}")
        print()
        print(f"FAIL — {len(errors)} violation(s) in {len(err_by_file)} enforced manifest(s)")
        print()
        print("Customer-facing voice rules (see RULES in this test):")
        print("  - No internal identifiers (function names, event types, helper modules)")
        print("  - No file paths or module paths")
        print("  - No bug numbers, spec section references, or version strings as referents")
        print("  - No canonical ids (project_NNN / person_NNN / org_NNN)")
        print("  - No architectural jargon (Tier N / Phase N / freelance render / canonical reader)")
        print("  - Trigger phrases inside backticks are fine — that's what the user types")
        print("  - Speak in first-person AI voice: 'I noticed...' / 'I tightened...'")
        return 1

    print(f"\n  ✓ all {files_scanned - len(group_by_file(warnings))} enforced manifest(s) clean")
    print(f"  ✓ all {skills_clean} of {skills_scanned} SKILL.md customer-blockquote(s) clean")
    if warnings:
        print(f"  (note: {len(warnings)} pre-v{enforce_str} warnings shown above — address as time permits)")
    print()
    print("OK — customer-facing prose passes voice check (manifests + SKILL.md italic blockquotes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
