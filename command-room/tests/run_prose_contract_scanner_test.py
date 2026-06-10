#!/usr/bin/env python3
"""
Prose-contract scanner — validates model-executed SKILL.md prose against
the canonical data contracts. (TEST_BLINDSPOT_MAP.md §3, the keystone
guard from the 2026-05-30 prose-contract investigation.)

WHY THIS EXISTS
The whole test battery is code-shaped: it scans .py writers/readers. But
Command Room skills perform most of their substrate I/O by INSTRUCTING an
LLM in SKILL.md prose ("write a `project_loaded_deep` event", "tag with
`primary_project_id`", "set status to at-risk"). Nothing validates that
prose against the schema enums — which is exactly how a green battery
coexists with ~300 real findings. This guard reads the prose and checks
every claimed event-type write, deprecated field, and status value
against the canonical enums loaded from the schemas.

CLASSES (v1 — high-precision core)
  A — event-type WRITE: prose/fenced-code claims a write of a `type` that
      is NOT in events.schema.json properties.type.enum. (RP1)
  C — deprecated id FIELD: prose uses `primary_project_id` (not a real
      field at all) or writes the DEPRECATED `project_id` instead of the
      canonical `primary_thread_id`. (RP2)
  D — STATUS value: a set-status idiom uses a value outside the
      entities.schema.json project-status enum. (RP3)
  (Class B event-reads and Class E names land in a follow-up pass — they
   need the dangling-read cross-check and the PRIVACY_POLICY allowlist
   parse respectively; see TEST_BLINDSPOT_MAP §3.3-3.5.)

MODE
  REPORT_ONLY = True → prints findings, ALWAYS exits 0 (battery stays
  green while the backlog is worked down). Flip to False to make it a
  blocking guard once the prose track is clean. This is the
  report-then-block rollout the unified plan calls for.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PLUGIN_ROOT / "shared" / "data-schemas"

REPORT_ONLY = False  # blocking guard — the prose-contract class is locked shut (2026-05-30)

SCAN_DIRS = ["skills", "shared", "references"]
SCAN_EXTENSIONS = {".md"}

# Contract-DEFINING docs legitimately discuss types/fields/status in prose
# (they document the rules + show deprecated examples), so they are exempt —
# same rationale the existing guards use for PRIVACY_POLICY/CONTRACT.
EXEMPT_FILES = {
    "run_prose_contract_scanner_test.py",
    "CHANGELOG.md",
    "DATA_CONTRACT.md",
    "ORG_AND_THREAD_MODEL.md",
    "SOURCE_OF_TRUTH.md",
    "VIEW_GENERATION.md",
    "PRIVACY_POLICY.md",
    "CONTRACT.md",
    "MD_DELIVERABLE_POLICY.md",
    "EMAIL_DRAFT_PROTOCOL.md",
    "ENTITY_RESOLVE_PROTOCOL.md",
    "RELEASE_MANIFEST.md",
    "BRAIN_FILE_CONTRACT.md",
    "WORKSPACE_API.md",
    "PASSIVE_CAPTURE.md",
    "INGEST_SUBSTRATE_SYNC.md",
    "COMMITMENT_SCHEMA.md",
    "STAGING_CONVENTION.md",
    "PROVENANCE_FRONT_MATTER.md",
}


def _load_event_enum() -> set[str]:
    data = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
    return set(data["properties"]["type"]["enum"])


def _load_status_enums() -> set[str]:
    data = json.loads((SCHEMA_DIR / "entities.schema.json").read_text(encoding="utf-8"))
    defs = data.get("$defs", {})
    vals: set[str] = set()
    for kind in ("project", "person", "org"):
        try:
            vals.update(defs[kind]["properties"]["status"]["enum"])
        except (KeyError, TypeError):
            pass
    return vals


def _load_deprecated_fields() -> set[str]:
    fields: set[str] = {"primary_project_id", "related_project_ids"}  # not real fields at all
    data = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
    for fname, spec in data.get("properties", {}).items():
        if isinstance(spec, dict) and "DEPRECATED" in str(spec.get("description", "")):
            fields.add(fname)
    return fields


EVENT_ENUM = _load_event_enum()
STATUS_ENUM = _load_status_enums()
DEPRECATED_FIELDS = _load_deprecated_fields()

# `status` is overloaded across namespaces: project/person/org records, but
# also meeting events (scheduled/occurred), commitments (open/overdue/...),
# onboarding checkpoints (complete), and pack_run telemetry (ok/failed).
# Class D should flag only GENUINELY-unknown status tokens (the real RP3
# drift like `completed`, `at-risk`, `placeholder`, `prospect`), so the
# allowlist is the union of every legitimate status value across namespaces.
KNOWN_STATUS = STATUS_ENUM | {
    "scheduled", "occurred", "cancelled", "canceled",            # meeting events
    "open", "overdue", "resolved", "closed", "done", "delivered", "superseded",  # commitments
    "complete", "in_progress", "incomplete",                     # onboarding checkpoints
    "ok", "failed", "degraded", "partial", "success", "error",   # pack_run telemetry
    "sent", "draft", "snoozed", "pending", "dismissed",          # misc event statuses
}

# ---- extraction patterns ----
EMIT_VERB_RE = re.compile(r"\b(emit|writ|append|record|stamp|log|fire)", re.I)
PROSE_EVENT_TOKEN_RE = re.compile(r"`([a-z][a-z0-9_]+)`\s+events?\b")
TYPE_LITERAL_RE = re.compile(r"""["']type["']\s*:\s*["']([a-z][a-z0-9_]+)["']""")
STATUS_SET_RE = re.compile(r"(?<![.\w])(?i:status)[`'\"]?\s*[:=]\s*[`'\"]?([a-z][a-z_\-]{2,})")
# back-compat / deprecation-explanation lines are NOT violations
BACKCOMPAT_RE = re.compile(
    r"DEPRECATED|deprecat|back-?compat|backward|fall ?back|legacy|no longer"
    r"|instead of|primary_thread_id|migrat|readers? (?:use|should)|kept for",
    re.I,
)
# Class-D guards: ignore obvious non-status words that follow "status to/:" in prose
STATUS_NONVALUES = {
    "the", "this", "that", "a", "an", "is", "of", "on", "in", "to", "as", "its",
    "their", "your", "my", "active-ish", "match", "reflect", "whatever", "one",
    "show", "true", "false", "null", "none", "open", "closed", "current", "live",
}

# Class W — raw append-write to a substrate file (the Bug #81 class: a visible
# `open(events.jsonl, "a")` example the model copies over the canonical
# atomic_append_jsonl helper). We flag the copyable RECIPE only — never the
# inline "NEVER open(...,'a')" warnings or the contract docs' forbidden-pattern
# prose. A comment line or a negatively-framed line is guidance, not a recipe.
RAW_APPEND_RE = re.compile(r"""open\s*\([^)]*,\s*['"]a['"]""")
SUBSTRATE_CTX_RE = re.compile(r"events|\.jsonl", re.I)
RAW_WRITE_NEGATIVE_RE = re.compile(
    r"never|forbidden|do ?n[o']t|don't|instead of|bypass|avoid|wrong|"
    r"anti-?pattern|must not|do not use|not safe|torn",
    re.I,
)


def _iter_paths():
    for d in SCAN_DIRS:
        root = PLUGIN_ROOT / d
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in SCAN_EXTENSIONS:
                yield p


def scan() -> list[tuple[str, str, int, str]]:
    """Returns (klass, relpath, lineno, line)."""
    out: list[tuple[str, str, int, str]] = []
    for path in _iter_paths():
        if path.name in EXEMPT_FILES or "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(PLUGIN_ROOT))
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        in_fence = False
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue

            # Class A — fenced or inline "type": "x" literals
            for m in TYPE_LITERAL_RE.finditer(line):
                tok = m.group(1)
                if tok not in EVENT_ENUM and tok not in ("home", "side", "personal", "email", "slack", "call", "note", "in-person", "stuck", "anomaly"):
                    out.append(("A-write", rel, i, stripped))

            # Class A — prose emit of `tok` event (outside code fences)
            if not in_fence and EMIT_VERB_RE.search(line):
                for m in PROSE_EVENT_TOKEN_RE.finditer(line):
                    tok = m.group(1)
                    if tok not in EVENT_ENUM:
                        out.append(("A-write", rel, i, stripped))

            # Class C — deprecated / nonexistent id fields
            if not BACKCOMPAT_RE.search(line):
                if re.search(r"\bprimary_project_id\b|\brelated_project_ids\b", line):
                    out.append(("C-field", rel, i, stripped))
                elif re.search(r"\bproject_id\b", line) and re.search(
                    r"\bdata\b|event|jsonl|write|emit|stamp|tag|`primary_thread_id`", line, re.I
                ):
                    out.append(("C-field", rel, i, stripped))

            # Class D — status-set idiom with non-enum value
            if not in_fence:
                for m in STATUS_SET_RE.finditer(line):
                    val = m.group(1).lower()
                    if val in KNOWN_STATUS or val in STATUS_NONVALUES:
                        continue
                    out.append(("D-status", rel, i, stripped))

            # Class W — raw append-write recipe to a substrate file (Bug #81).
            # Skip comment lines and negatively-framed lines — those are
            # warnings/anti-examples, not patterns the model would copy.
            if (
                RAW_APPEND_RE.search(line)
                and SUBSTRATE_CTX_RE.search(line)
                and not stripped.startswith("#")
                and not RAW_WRITE_NEGATIVE_RE.search(line)
            ):
                out.append(("W-rawwrite", rel, i, stripped))
    return out


def _self_test_w_detector() -> list[str]:
    """Guard the guard: assert the W-rawwrite detector actually fires on the
    Bug #81 pattern and stays quiet on warnings/reads. A detector that silently
    stops matching is worse than none (false 'all clear'). Returns failures."""
    def fires(line: str) -> bool:
        s = line.strip()
        return bool(
            RAW_APPEND_RE.search(line)
            and SUBSTRATE_CTX_RE.search(line)
            and not s.startswith("#")
            and not RAW_WRITE_NEGATIVE_RE.search(line)
        )
    should_fire = [
        '    with open(events_jsonl_path, "a", encoding="utf-8") as f:',
        "with open(events_path, 'a', encoding='utf-8') as f:",
    ]
    should_not_fire = [
        "    # NEVER open(events_path, 'a') directly (see WORKSPACE_API.md)",
        'Hand-rolled writes via open(path, "a") are FORBIDDEN for events.jsonl',
        "with open(events_path, 'r', encoding='utf-8') as f:",
        "with open(logfile, 'a') as f:",
    ]
    fails = []
    for ln in should_fire:
        if not fires(ln):
            fails.append(f"W-detector should FIRE but did not: {ln!r}")
    for ln in should_not_fire:
        if fires(ln):
            fails.append(f"W-detector should be SILENT but fired: {ln!r}")
    return fails


def main() -> int:
    if not EVENT_ENUM:
        print("WARN — could not load event enum; scanner inert")
        return 0
    self_test_fails = _self_test_w_detector()
    if self_test_fails:
        print("FAIL — W-rawwrite detector self-test failed (the guard is broken):")
        for f in self_test_fails:
            print(f"  {f}")
        return 1
    findings = scan()
    by_class: dict[str, list] = {}
    for klass, rel, ln, line in findings:
        by_class.setdefault(klass, []).append((rel, ln, line))

    print(f"Prose-contract scanner — enums loaded: {len(EVENT_ENUM)} event types, "
          f"{len(STATUS_ENUM)} status values, {len(DEPRECATED_FIELDS)} deprecated fields")
    print(f"Scanned {SCAN_DIRS} (.md prose). Findings: {len(findings)}")
    print()
    labels = {
        "A-write": "A — event-type write NOT in schema enum (RP1)",
        "C-field": "C — deprecated/nonexistent id field, should be primary_thread_id (RP2)",
        "D-status": "D — status value outside the project-status enum (RP3)",
        "W-rawwrite": "W — raw open(events.jsonl,'a') write; use atomic_append_jsonl (Bug #81)",
    }
    for klass in ("A-write", "C-field", "D-status", "W-rawwrite"):
        items = by_class.get(klass, [])
        print(f"## {labels[klass]} — {len(items)}")
        for rel, ln, line in items:
            shown = line if len(line) <= 140 else line[:137] + "..."
            print(f"  {rel}:{ln}  {shown}")
        print()

    if not findings:
        print("OK — prose conforms to the canonical contracts.")
        return 0
    if REPORT_ONLY:
        print(f"REPORT-ONLY: {len(findings)} prose-contract finding(s). Battery stays GREEN.")
        print("Flip REPORT_ONLY=False once the prose track is clean to make this blocking.")
        return 0
    print(f"FAIL — {len(findings)} prose-contract violation(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
