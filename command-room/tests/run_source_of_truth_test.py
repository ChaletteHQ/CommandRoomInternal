#!/usr/bin/env python3
"""
Structural guard: source-of-truth convergence (v3.11.4+).

The 2026-05-20 morning-brief bug bundle and the audit that followed surfaced
that four interlocking bugs shared one root cause:

  - writers emit one shape; readers look for another
  - projections (Tier 2 views) are read as if they're authoritative
  - no structural defense enforced convergence

The canonical contract lives in `references/SOURCE_OF_TRUTH.md`. This test
enforces it.

FIVE CHECKS (v3.12.0)
=====================

1) **Tier 2 view-overlay check.** Every SKILL.md and orchestrator reference
   that reads a view file (MASTER_TRACKER.md, PEOPLE.md, DECISION_LOG.md,
   `_hq/views/*.md`, `_people/[name].md`, `_team-config.md`, `PERSON.md`)
   to DRIVE A DECISION must also reference the canonical overlay pattern
   (events.jsonl scan, load_open_commitments, "overlay", "freshness", or a
   Tier 1 source file path) within 50 lines of the read — OR carry an
   explicit "orientation only" / "Tier 2" / "static-tier lookup" escape
   hatch.

2) **Closure-event canonical-id check.** Every writer that emits a closure
   event (commitment_resolved / thread_resolved / decision_resolved /
   commitment_review_dismissed) must use one of the canonical id fields
   accepted by `cru_match.load_open_commitments`:

     data.commitment_id (preferred for commitment_resolved)
     data.thread_id     (preferred for thread_resolved)
     data.id            (acceptable fallback)
     data.target_id     (legacy — accepted for backwards-compat, writers
                         should NOT emit this for new events as of v3.11.4)
     data.decision_id   (preferred for decision_resolved)
     data.supersedes_seq (for decision_superseded)

3) **Dead-event-type check.** Flags references to known-dead event types
   (`matter_resolved`, `meeting_resolved`) that no writer emits — same
   drift-prevention pattern as run_no_retired_skills_test.py.

4) **Schema-enum compliance (v3.12.0+).** Every documented event-write spec
   (`"type": "<name>"` JSON literals in SKILL.md and orchestrator prose)
   must use a type that exists in `shared/data-schemas/events.schema.json`
   enum. Pre-v3.12.0, ~10 event types were written but absent from the enum
   (`person_created`, `project_proposal`, `decision_revisit_scheduled`,
   etc.) — silently violating schema on every workspace audit. v3.12.0
   widened the enum to match reality; this check keeps the convergence
   locked.

   **Scope limitation:** catches drift in documented JSON examples in skill
   prose. Does NOT catch programmatic writes that build the dict from a
   variable (e.g., `_log_event(workspace_root, event_type, ...)` in
   `people_writer.py`). Those need a more invasive function-level test if
   that drift class becomes a recurring problem.

5) **View-generator-claim integrity (v3.12.0+).** Every view declared with
   `<!-- generator: X -->` in `references/VIEW_GENERATION.md` (or any other
   reference doc) must name a real skill in `skills/`, OR a recognized
   inline-compute owner from the allowed-special set, OR carry an explicit
   retired marker. Pre-v3.12.0 the doc declared `view-generator` as owner
   of 5 analytical views; no such skill existed; readers depended on files
   never created. v3.12.0 moved the projections inline to insight-generator
   and updated the declarations. This check prevents the ghost coming back.

WHY THIS LIVES IN A TEST AND NOT A CODE REVIEW
==============================================

The plugin's been bitten by this exact bug class at least four times in
May 2026:

  - 2026-05-17 commitment-shape-drift (Pulse dropped ~2/3 of commitments — shape
    drift between writers and readers)
  - 2026-05-20 morning-brief B1 (tz.py walk-up never resolved — silent UTC)
  - 2026-05-20 morning-brief B3 (MASTER_TRACKER stale, no overlay)
  - 2026-05-20 morning-brief B4 (thread_resolved vs commitment_resolved
    consumer mismatch)

Per memory `feedback_verify_consumers_before_ship.md`, "did I just ship a
feature nobody reads" is the right gate. This test IS the gate.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Folders the test inspects
SKILL_DIRS = [ROOT / "skills"]
REFERENCE_DIRS = [ROOT / "references", ROOT / "shared"]
SCRIPT_DIRS = [ROOT / "shared" / "scripts"]

# Tier 2 view file references that the overlay-check scans for
TIER_2_VIEW_PATTERNS = [
    r"_hq/MASTER_TRACKER\.md",
    r"_hq/PEOPLE\.md",
    r"_hq/DECISION_LOG\.md",
    r"_hq/views/[A-Z_]+\.md",
    r"MASTER_TRACKER\.md",  # without path prefix — caught by neighborhood check
    # v3.11.5: _people/ per-person profiles are also Tier 2 projections — their
    # commitment tables can lag events.jsonl, just like MASTER_TRACKER. Catch
    # reads of _people/[name].md and _team-config.md.
    r"_people/\[name\]\.md",
    r"_people/\[Name\]\.md",
    r"_team-config\.md",
    r"PERSON\.md",  # generic reference to per-person profile
]
TIER_2_VIEW_RE = re.compile("|".join(TIER_2_VIEW_PATTERNS))

# Tokens that prove the surrounding context honors the overlay rule
OVERLAY_TOKENS = [
    "events.jsonl",
    "load_open_commitments",
    "overlay",
    "generated-at",
    "Step 3a",
    "Step 1a",
    "Tier 1",
    "tracker_stamp",
    "computed_last_activity",
    "SOURCE_OF_TRUTH",
]

# Escape-hatch tokens that prove the read is orientation-only
ORIENTATION_ESCAPE_TOKENS = [
    "orientation only",
    "Tier 2 view",
    "not used for surface decisions",
    "not used for \"what's outstanding\"",
    "static-tier lookup",
    "human-readable copy",
    "fast orient",
    "legacy fallback",
    "fast-orient",
    "first-time-load",
    "for human reading",
]

NEIGHBORHOOD_LINES = 50

# Lines must contain a READ verb for the overlay check to fire. WRITE-side
# references to view files ("update MASTER_TRACKER", "+4 entries to PEOPLE.md")
# are not drift — they're documenting what the skill writes (which is the
# regenerator's job, not the consumer's drift problem).
READ_VERB_RE = re.compile(
    r"\b(read|reads|reading|scan|scans|scanning|search|searches|searching|"
    r"lookup|look up|query|queries|querying|check|checks|checking|find|finds|"
    r"pull|pulls|pulling|load|loads|loading|inspect|inspects|"
    r"derive|derived|derives|consume|consumes|consuming|fetch|fetches|fetching|"
    r"refer to|references)\b",
    re.IGNORECASE,
)
# Lines with these verbs are explicitly write-side and should be skipped
WRITE_VERB_RE = re.compile(
    r"\b(write|writes|writing|update|updates|updating|append|appends|appending|"
    r"save|saves|saving|regenerate|regenerates|regenerated|render|renders|rendered|"
    r"maintain|maintained by|maintains|projected|projection|generate|generated|"
    r"\+\d+ entries|persist|persisted|emit|emits|emitted|store|stores|stored)\b",
    re.IGNORECASE,
)
# Lines that are existence/setup checks rather than state reads
SETUP_CHECK_RE = re.compile(
    r"\b(doesn'?t exist|hasn'?t been set up|first-time setup|isn'?t set up|"
    r"file exists|not present|missing|template)\b",
    re.IGNORECASE,
)

# Closure-event field allow-list (writer-side)
CLOSURE_EVENT_TYPES = {
    "commitment_resolved",
    "thread_resolved",
    "decision_resolved",
    "commitment_review_dismissed",
}
CANONICAL_CLOSURE_ID_FIELDS = {
    "commitment_id",
    "thread_id",
    "id",
    "target_id",  # legacy, accepted
    "decision_id",
    "supersedes_seq",  # used for decision_superseded
}

# Files exempt from the overlay check — historical context only
OVERLAY_EXEMPT_FILES = {
    # The canonical contract docs name every view file by design
    "references/SOURCE_OF_TRUTH.md",
    "references/DATA_CONTRACT.md",
    "references/VIEW_GENERATION.md",
    "references/UPDATE_RULES.md",          # rules ABOUT updating views
    "references/PROVENANCE_FRONT_MATTER.md",  # schema doc
    "references/claude-md-template.md",    # template for user CLAUDE.md
    "references/MD_DELIVERABLE_POLICY.md", # write-side policy doc
    "references/HOW_COMMAND_ROOM_WORKS.md",  # narrative overview
    # Routing docs — name view files as routing targets, not as reads
    "shared/FUZZY_ROUTER.md",
    "shared/CONTRACT.md",
    "shared/RELIABILITY.md",
    "shared/PASSIVE_CAPTURE.md",
    "shared/WORKSPACE_API.md",
    "shared/COMMITMENT_SCHEMA.md",
    "shared/CHAT_ACTION_WIDGET.md",
    "shared/EMAIL_DRAFT_PROTOCOL.md",
    "shared/VOICE_CALIBRATION.md",
    "shared/PLUGIN_BOUNDARY.md",
    # workspace-ingest references parse legacy MD into canonical sources — a
    # one-time ingest, not a recurring read
    "skills/workspace-ingest/references/custom-markdown-parser.md",
    "skills/workspace-ingest/references/generic-fallback-parser.md",
    "skills/workspace-ingest/references/openai-export-parser.md",
    "skills/workspace-ingest/references/v1x-parser.md",
    "skills/command-room-onboarding/references/templates.md",
    # Audit / hygiene skills inspect every file by design
    "skills/weekly-audit/SKILL.md",
    # Test file itself
    "tests/run_source_of_truth_test.py",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_md_files(dirs):
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*.md"):
            yield p


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def _read_lines(p: Path):
    try:
        return p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Check 1 — Tier 2 view-overlay
# ---------------------------------------------------------------------------


def check_tier2_overlay():
    """Every view-file read in a SKILL.md / orchestrator must have an
    overlay sibling or an orientation escape hatch within NEIGHBORHOOD_LINES.
    """
    violations = []
    for p in _iter_md_files(SKILL_DIRS + REFERENCE_DIRS):
        rel = _rel(p)
        if rel in OVERLAY_EXEMPT_FILES:
            continue
        # Some non-spec docs (CHANGELOG, gotchas) are also exempt
        if rel.startswith("shared/releases/"):
            continue
        if rel.endswith("CHANGELOG.md") or rel.endswith("README.md"):
            continue

        lines = _read_lines(p)
        for i, line in enumerate(lines):
            if not TIER_2_VIEW_RE.search(line):
                continue
            # Skip bare mentions in comments / link refs / "Don't write to..."
            stripped = line.strip()
            if stripped.startswith("<!--") or stripped.startswith("//"):
                continue
            # Skip write-side, setup-check, and non-read references — those
            # are documenting what the skill writes or that the file exists,
            # not reading state from it.
            if WRITE_VERB_RE.search(line):
                continue
            if SETUP_CHECK_RE.search(line):
                continue
            # Must contain a read verb on the same line to count as a read
            if not READ_VERB_RE.search(line):
                continue

            # Look at the surrounding NEIGHBORHOOD_LINES window
            lo = max(0, i - NEIGHBORHOOD_LINES)
            hi = min(len(lines), i + NEIGHBORHOOD_LINES + 1)
            window = "\n".join(lines[lo:hi])

            has_overlay = any(tok in window for tok in OVERLAY_TOKENS)
            has_escape = any(tok in window for tok in ORIENTATION_ESCAPE_TOKENS)
            if has_overlay or has_escape:
                continue

            violations.append(
                f"{rel}:{i + 1}: reads Tier 2 view without overlay or "
                f"orientation-only escape hatch within ±{NEIGHBORHOOD_LINES} lines"
            )
    return violations


# ---------------------------------------------------------------------------
# Check 2 — Closure-event canonical-id
# ---------------------------------------------------------------------------

# Match a JSON-ish closure-event spec in skill prose:
#   {"type":"commitment_resolved","data":{"commitment_id": ...}}
# or any "type": "<closure>" within a few lines of "data": {"<field>"
CLOSURE_TYPE_RE = re.compile(
    r'"type"\s*:\s*"(' + "|".join(CLOSURE_EVENT_TYPES) + r')"'
)
DATA_FIELD_RE = re.compile(r'"data"\s*:\s*\{[^}]*?"([a-z_]+)"\s*:', re.DOTALL)


def check_closure_canonical_id():
    """Every documented closure-event-write spec must use a canonical id field."""
    violations = []
    for p in _iter_md_files(SKILL_DIRS + REFERENCE_DIRS):
        rel = _rel(p)
        if rel.endswith("CHANGELOG.md"):
            continue
        if rel in OVERLAY_EXEMPT_FILES:
            continue

        text = p.read_text(encoding="utf-8", errors="ignore")
        # Find each closure-type occurrence and inspect its data block
        for m in CLOSURE_TYPE_RE.finditer(text):
            closure_type = m.group(1)
            # Grab the next ~300 chars after the type marker — enough for one
            # JSON-ish data block on the same code-fence example.
            tail = text[m.start(): m.start() + 400]
            data_match = DATA_FIELD_RE.search(tail)
            if not data_match:
                continue  # spec sentence without a data block — skip
            field = data_match.group(1)
            if field not in CANONICAL_CLOSURE_ID_FIELDS:
                # Locate line number of the type marker
                line_no = text.count("\n", 0, m.start()) + 1
                violations.append(
                    f"{rel}:{line_no}: {closure_type} writer uses "
                    f"non-canonical id field 'data.{field}' "
                    f"(allowed: {sorted(CANONICAL_CLOSURE_ID_FIELDS)})"
                )
    return violations


# ---------------------------------------------------------------------------
# Check 3 — Dead event types
# ---------------------------------------------------------------------------

# Known-dead event-type names that the test will flag if they reappear as
# CONSUMER reads with no corresponding writer. Pre-v3.11.4
# build_process_meetings_input.py was the only consumer of these and didn't
# match any writer.
KNOWN_DEAD_EVENT_TYPES = {
    "matter_resolved",
    "meeting_resolved",
}


# ---------------------------------------------------------------------------
# Check 4 — Schema enum compliance (v3.12.0+)
# ---------------------------------------------------------------------------


def check_schema_enum_compliance():
    """Every event type emitted by a writer must be in events.schema.json enum.

    Pre-v3.12.0, ~10 event types were written by skills/scripts but absent from
    the schema enum (`person_created`, `project_proposal`, `decision_revisit_scheduled`,
    etc.) — every workspace validation would flag them. v3.12.0 widened the enum
    to match reality; this check keeps the convergence locked.
    """
    violations = []
    # Load the schema enum
    schema_path = ROOT / "shared" / "data-schemas" / "events.schema.json"
    if not schema_path.exists():
        return [f"{_rel(schema_path)}: schema file missing — cannot validate enum compliance"]
    try:
        import json as _json
        schema = _json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as exc:
        return [f"{_rel(schema_path)}: failed to parse ({exc})"]
    allowed = set(schema.get("properties", {}).get("type", {}).get("enum", []))
    if not allowed:
        return [f"{_rel(schema_path)}: type enum is empty or missing"]

    # Scan every documented event-write spec — looking for `"type": "<name>"`
    type_literal_re = re.compile(r'"type"\s*:\s*"([a-z_][a-z_0-9]*)"')
    files = list(_iter_md_files(SKILL_DIRS + REFERENCE_DIRS))
    for d in SCRIPT_DIRS:
        if d.exists():
            files.extend(d.rglob("*.py"))

    seen_types: set[str] = set()
    for p in files:
        rel = _rel(p)
        if rel.endswith("CHANGELOG.md"):
            continue
        if rel in OVERLAY_EXEMPT_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in type_literal_re.finditer(text):
            type_name = m.group(1)
            # Skip JSON Schema type-keyword values and chat-data-view shape tags
            if type_name in {
                "array", "boolean", "integer", "number", "object", "string", "null",
                # Chat-data-view shape tags (the renderer's `type:` field on item
                # dicts, distinct from events.jsonl `type:`)
                "email", "slack", "file", "anomaly", "stuck",
            }:
                continue
            seen_types.add(type_name)
            if type_name not in allowed:
                line_no = text.count("\n", 0, m.start()) + 1
                violations.append(
                    f"{rel}:{line_no}: writes event type '{type_name}' "
                    f"which is NOT in events.schema.json enum (would fail weekly-audit validation)"
                )
    return violations


# ---------------------------------------------------------------------------
# Check 5 — View-generator-claim integrity (v3.12.0+)
# ---------------------------------------------------------------------------


def check_view_generator_claims():
    """Every view declared with `<!-- generator: X -->` must name a real skill,
    inline-compute pattern, or explicit retired marker.

    Pre-v3.12.0, VIEW_GENERATION.md declared `<!-- generator: view-generator -->`
    for 5 analytical views (TIMELINE, RELATIONSHIPS, COMMITMENT_AGING, DORMANT,
    THEMES), but no `view-generator` skill exists. Readers (insight-generator,
    dormant-customer-scan) depended on files that never got created. v3.12.0
    moved these to inline computation by insight-generator and updated the
    generator declarations to match.
    """
    violations = []
    skill_names = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    # Allowed special generators (not skill names but valid declarations)
    allowed_special = {
        "insight-generator",  # canonical inline-computer of analytical views
        "people-crm",
        "decision-log",
        "workspace-manager",
        "weekly-audit",
        "meeting-notes",
    }
    generator_re = re.compile(r"<!--\s*generator:\s*([a-z\-_]+)(?:\s+\([^)]*\))?\s*-->")
    for p in _iter_md_files([ROOT / "references"]):
        rel = _rel(p)
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in generator_re.finditer(text):
            generator = m.group(1).strip()
            if generator in skill_names or generator in allowed_special:
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            violations.append(
                f"{rel}:{line_no}: view declares <!-- generator: {generator} --> "
                f"but no such skill exists in skills/"
            )
    return violations


def check_dead_event_types():
    """Flag references to known-dead event types so they don't drift back in."""
    violations = []
    # Scan both .md and .py
    files = list(_iter_md_files(SKILL_DIRS + REFERENCE_DIRS))
    for d in SCRIPT_DIRS:
        if d.exists():
            files.extend(d.rglob("*.py"))

    for p in files:
        rel = _rel(p)
        if rel.endswith("CHANGELOG.md"):
            continue
        if rel in OVERLAY_EXEMPT_FILES:
            continue
        # The test file itself names the dead types
        if rel == "tests/run_source_of_truth_test.py":
            continue

        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            for dead_type in KNOWN_DEAD_EVENT_TYPES:
                if dead_type not in line:
                    continue
                # Same-line escape hatch: honest deprecation notes
                # ("dropped", "no longer", "deprecated", "removed") are not
                # violations — they document the removal for future readers.
                if re.search(
                    r"\b(dropped|deprecated|no longer|removed|retired|legacy)\b",
                    line, re.IGNORECASE,
                ):
                    continue
                violations.append(
                    f"{rel}:{i + 1}: references dead event type "
                    f"'{dead_type}' — no writer in plugin emits this"
                )
    return violations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== source-of-truth convergence ===")

    tier2_violations = check_tier2_overlay()
    closure_violations = check_closure_canonical_id()
    dead_violations = check_dead_event_types()
    schema_violations = check_schema_enum_compliance()
    generator_violations = check_view_generator_claims()

    total_violations = (
        len(tier2_violations)
        + len(closure_violations)
        + len(dead_violations)
        + len(schema_violations)
        + len(generator_violations)
    )

    if tier2_violations:
        print(f"\n  ✗ Tier 2 view-overlay violations ({len(tier2_violations)}):")
        for v in tier2_violations:
            print(f"      {v}")
    else:
        print("  ✓ Tier 2 view-overlay check passes")

    if closure_violations:
        print(f"\n  ✗ Closure-event canonical-id violations ({len(closure_violations)}):")
        for v in closure_violations:
            print(f"      {v}")
    else:
        print("  ✓ Closure-event canonical-id check passes")

    if dead_violations:
        print(f"\n  ✗ Dead event-type violations ({len(dead_violations)}):")
        for v in dead_violations:
            print(f"      {v}")
    else:
        print("  ✓ Dead event-type check passes")

    if schema_violations:
        print(f"\n  ✗ Schema-enum compliance violations ({len(schema_violations)}):")
        for v in schema_violations:
            print(f"      {v}")
    else:
        print("  ✓ Schema-enum compliance check passes")

    if generator_violations:
        print(f"\n  ✗ View-generator-claim violations ({len(generator_violations)}):")
        for v in generator_violations:
            print(f"      {v}")
    else:
        print("  ✓ View-generator-claim check passes")

    print()
    if total_violations == 0:
        print("OK — source-of-truth convergence enforced")
        return 0
    print(f"FAIL — {total_violations} violation(s)")
    print(
        "\nSee references/SOURCE_OF_TRUTH.md for the canonical contract. "
        "Most fixes are one of: (a) add an events.jsonl overlay step next "
        "to a Tier 2 view read, (b) add an 'orientation only' escape hatch "
        "to a read that doesn't drive decisions, (c) rename a closure-event "
        "id field to canonical, (d) remove a dead event-type read."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
