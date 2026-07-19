#!/usr/bin/env python3
"""
F-04 instruction-layer test — canonical-edit-surface staleness detector
precision (integration-2026-07).

THE BUG: the bridge's `stale_marker_pending` detection was a bare substring
search — a workspace doc that correctly EXPLAINED the old plugin location was
retired ("do not edit ... renamed ... retired") re-flagged as stale on every
bridge run, and auto-apply would have replaced a correct, detailed section
with a generic block.

THE FIX (v4.8.1): a marker hit is actionable only in a path/config context on
a line that still directs the reader at the retired location; keyword-only /
prose hits downgrade to a one-line advisory (no confirm item, no replacement
block, no migration event).

The fix is instruction-layer (the bridge's detection logic is executed by the
runtime from SKILL.md prose), so per the instruction-layer-gap gotcha this
test pins the load-bearing language in place — same guard class as the F13
helper-reference check.

stdlib-only, non-zero exit on failure (house convention — auto-discovered by
run_all.py, unit tier).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "command-room-update-bridge" / "SKILL.md"


def test_classification_language_present():
    print("test_classification_language_present")
    text = SKILL.read_text(encoding="utf-8")

    # The two-way classification must exist and be tied to F-04.
    assert "F-04" in text, "detection logic must cite the finding"
    assert "path/config context" in text, (
        "actionable hits must require a path/config context"
    )
    assert "Advisory hit" in text, "the advisory downgrade tier must exist"

    # The advisory tier's teeth: no confirm item, no replacement block.
    assert "NO confirm item and NO replacement block" in text

    # Ambiguity must default to advisory (wrong auto-replacement destroys
    # correct docs; wrong advisory costs one sentence).
    assert "classify it advisory" in text

    # Prose that quotes the old path verbatim inside a retirement warning is
    # still advisory — context wins over shape.
    assert "context wins over shape" in text


def test_trigger_gate_references_the_classification():
    print("test_trigger_gate_references_the_classification")
    text = SKILL.read_text(encoding="utf-8")
    # The file has one trigger gate per migration — find the canonical-edit-
    # surface one (the only gate that talks about "stale markers").
    gate_idx = text.find("**Trigger gate:** fires if ANY of the stale markers")
    assert gate_idx != -1, "canonical-edit-surface trigger-gate paragraph missing"
    gate_para = text[gate_idx:text.find("\n\n", gate_idx)]
    assert "actionable" in gate_para and "F-04" in gate_para, (
        "the migration's trigger gate must route through the F-04 "
        "classification, not a bare substring search"
    )


def test_migration_registry_comments_agree():
    print("test_migration_registry_comments_agree")
    # Second-eyes finding 4: the registry entries' inline comments must not
    # re-teach the retired bare-substring semantics ("ANY present means
    # PENDING") that the detection logic replaced — a runtime reading the
    # registry first would reproduce the exact F-04 false positive.
    text = SKILL.read_text(encoding="utf-8")
    assert "ANY present means migration is PENDING" not in text, (
        "a registry comment still teaches bare-substring stale-marker "
        "semantics; route it through the F-04 classification"
    )
    assert text.count("ACTIONABLE hit") >= 2, (
        "both canonical-edit-surface registry entries must reference the "
        "actionable-hit classification"
    )


TESTS = [
    test_classification_language_present,
    test_trigger_gate_references_the_classification,
    test_migration_registry_comments_agree,
]


def main() -> int:
    failures = 0
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  FAIL: {t.__name__}: {e}")
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
