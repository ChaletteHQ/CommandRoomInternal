#!/usr/bin/env python3
"""
Tests for writer_contract_lint (SPEC GATE1, item 4 — the event-write surface).

House conventions: stdlib only, plain asserts, prints PASS per test, exits 0 on
pass / 1 on failure (run_all.py classifies this as a unit suite).

Covers:
  - a skill that DECLARES an events.jsonl append but names no locked writer is
    flagged (the decision-log bypass class)
  - naming atomic_append_jsonl directly clears the flag
  - naming an append-routing helper (cru_match) clears the flag
  - read-only mentions of events.jsonl are NOT flagged
  - frontmatter `description:` prose mentioning events.jsonl is NOT flagged
  - the real tree: decision-log (the confirmed fix) + meeting-notes + the
    GATE1-fixed appenders are all clean; the lint returns 0 findings overall
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from writer_contract_lint import (  # noqa: E402
    declares_event_append,
    lint_skill_event_writes,
    lint_skill_text,
    names_locked_writer,
)


def test_bypass_is_flagged() -> None:
    text = (
        "## Writer Contract\n\n"
        "Before writing, read `shared/WORKSPACE_API.md`.\n\n"
        "**Appends to:**\n"
        "- `_hq/data/events.jsonl` — event type `foo_happened` with `{a, b}`.\n"
    )
    finding = lint_skill_text("synthetic-bypass", text)
    assert finding is not None, "an append-declaring skill with no helper must flag"
    assert finding["skill"] == "synthetic-bypass"
    assert "atomic_append_jsonl" in finding["reason"]
    print("PASS test_bypass_is_flagged")


def test_naming_helper_clears() -> None:
    text = (
        "**Appends to:**\n"
        "- `_hq/data/events.jsonl` — event type `foo`.\n\n"
        "Write it via `atomic_append_jsonl(events_path, [event])`.\n"
    )
    assert lint_skill_text("synthetic-ok", text) is None
    print("PASS test_naming_helper_clears")


def test_routing_helper_clears() -> None:
    text = (
        "**Appends to:**\n"
        "- `_hq/data/events.jsonl` — `commitment_resolved` via `cru_match`.\n"
    )
    assert lint_skill_text("synthetic-routed", text) is None
    print("PASS test_routing_helper_clears")


def test_readonly_not_flagged() -> None:
    text = (
        "**Reads from:**\n"
        "- `_hq/data/events.jsonl` — every `type == \"decision\"` event.\n"
        "Read `_hq/data/events.jsonl` and count the open commitments.\n"
    )
    assert not declares_event_append(text), "read-only mentions must not declare an append"
    assert lint_skill_text("synthetic-reader", text) is None
    print("PASS test_readonly_not_flagged")


def test_frontmatter_description_not_flagged() -> None:
    text = (
        "---\n"
        "name: foo\n"
        'description: "Extract decisions and append them to your events.jsonl '
        'substrate. Triggers: ..."\n'
        "---\n\n"
        "# Foo\n\n"
        "This skill reads `_hq/data/events.jsonl` for context only.\n"
    )
    assert lint_skill_text("synthetic-frontmatter", text) is None, (
        "a description-field mention of events.jsonl must not count as an append"
    )
    print("PASS test_frontmatter_description_not_flagged")


def test_helper_detection_predicates() -> None:
    assert names_locked_writer("call atomic_append_jsonl(...)")
    assert names_locked_writer("routes through people_writer")
    assert not names_locked_writer("just a plain header pointer to WORKSPACE_API.md")
    print("PASS test_helper_detection_predicates")


def test_decision_log_is_clean() -> None:
    """The confirmed v3.20.0 bypass — decision-log — must now pass the lint
    (it carries the explicit atomic_append_jsonl recipe added in GATE1)."""
    dl = (ROOT / "skills" / "decision-log" / "SKILL.md").read_text(encoding="utf-8")
    assert "atomic_append_jsonl" in dl, "decision-log must name the locked writer"
    assert lint_skill_text("decision-log", dl) is None, (
        "decision-log must pass the writer-contract lint after the GATE1 fix"
    )
    print("PASS test_decision_log_is_clean")


def test_meeting_notes_is_clean() -> None:
    mn = (ROOT / "skills" / "meeting-notes" / "SKILL.md").read_text(encoding="utf-8")
    assert lint_skill_text("meeting-notes", mn) is None
    print("PASS test_meeting_notes_is_clean")


def test_real_tree_is_clean() -> None:
    """The whole shipped tree must be clean — every event-appending skill names
    the locked writer or a routing helper. A NEW appender that doesn't will turn
    this red, which is the point (permanent guard)."""
    findings = lint_skill_event_writes(ROOT)
    assert findings == [], (
        "writer-contract lint found event-append bypasses: "
        + ", ".join(f["skill"] for f in findings)
    )
    print(f"PASS test_real_tree_is_clean (0 findings across skills/)")


def main() -> int:
    test_bypass_is_flagged()
    test_naming_helper_clears()
    test_routing_helper_clears()
    test_readonly_not_flagged()
    test_frontmatter_description_not_flagged()
    test_helper_detection_predicates()
    test_decision_log_is_clean()
    test_meeting_notes_is_clean()
    test_real_tree_is_clean()
    print("\nALL writer_contract_lint tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
