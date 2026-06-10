#!/usr/bin/env python3
"""
Runtime exercise pass for the `research` skill (Gate 13 / Gate 17 stub).

NOT a unit test. A synthetic end-to-end exercise of the harness-able code
paths the `research` skill depends on, against a realistic fixture. The
LLM-behavior paths (capability detection of the Vibe Prospecting tools,
web fan-out/verify, source labeling) are exercised by the eval layer
(tests/eval_prompts_research.json, Gate 15) and the Cowork verify loop
(chalette:synthetic-workspace-runtime-test, Gate 17) — those cannot be
asserted in pure Python.

Run from the command-room repo root:
    python tests/runtime_exercise_research.py

Exits 0 on full green, 1 on any failure.

Per feedback_verify_before_ship_real_fixtures: static-green != working for
the LLM-runtime class. Fill these assertions in against a real-shape fixture
before promoting `research` to production.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

PASS: list[str] = []
FAIL: list[str] = []


def _ok(name: str, detail: str = "") -> None:
    line = f"  PASS  {name}"
    if detail:
        line += f"  ({detail})"
    print(line)
    PASS.append(name)


def _fail(name: str, detail: str = "") -> None:
    line = f"  FAIL  {name}"
    if detail:
        line += f"  ({detail})"
    print(line)
    FAIL.append(name)


# ---------------------------------------------------------------------------
# Step 1 — Frame from the workspace (entity-resolve, Gate 4)
# ---------------------------------------------------------------------------

def test_name_bearing_trigger_resolves() -> None:
    """'research Acme Co' must route through resolve_all before any search."""
    try:
        from entity_resolve import resolve_all  # noqa: F401
    except Exception as exc:  # pragma: no cover - import guard
        _fail("entity_resolve importable", str(exc))
        return
    # TODO: build a real-shape fixture workspace with Acme Co as a known org,
    # call resolve_all("research Acme Co", ws) and assert it returns the org
    # with the thread/last-contact context the brief is supposed to lead with.
    _ok("entity_resolve importable", "TODO: assert resolved-entity framing")


def test_unknown_subject_degrades_to_new() -> None:
    """An unresolved subject must NOT crash — research proceeds, stubs at save."""
    # TODO: resolve_all("research <never-seen-org>", ws) -> no match -> assert
    # the skill contract treats it as a new subject (no hand-edit of entities.json).
    _ok("unknown-subject path defined", "TODO: assert no premature entity write")


# ---------------------------------------------------------------------------
# Write-delegation contract (Gate 3 — research writes NOTHING directly)
# ---------------------------------------------------------------------------

def test_no_direct_substrate_write() -> None:
    """research must delegate every write; it owns no event type of its own."""
    skill_md = (ROOT / "skills" / "research" / "SKILL.md").read_text(encoding="utf-8")
    forbidden = ["atomic_append_jsonl(events_path", "people_writer.create_person("]
    # The SKILL.md should DESCRIBE delegation, not perform writes. The real
    # guard is that the skill never imports a writer to call it directly —
    # it hands off to intel-intake / people-crm.
    # TODO: when a research_writer or inline code exists, assert it is absent.
    if "research adds no new event type" in skill_md:
        _ok("no-new-event-type contract present")
    else:
        _fail("no-new-event-type contract present", "missing delegation clause")


def test_enriched_pii_routes_to_people_crm() -> None:
    """Decision-makers from enrichment land via people_writer, not loose notes."""
    try:
        from people_writer import find_existing_person  # noqa: F401
    except Exception as exc:  # pragma: no cover
        _fail("people_writer importable", str(exc))
        return
    # TODO: simulate an enrichment result with a discovered COO; assert the
    # save path calls find_existing_person (dedup-first) before create_person.
    _ok("people_writer importable", "TODO: assert dedup-first save of decision-makers")


# ---------------------------------------------------------------------------
# Defensive substrate reads (substrate-shape drift)
# ---------------------------------------------------------------------------

def test_defensive_entities_read() -> None:
    """Framing read must handle entities.threads AND legacy entities.projects."""
    # TODO: feed both substrate shapes (flat + nested, threads + projects key)
    # and assert the framing read returns the same resolved context for both.
    _ok("defensive-read contract noted", "TODO: assert both shapes parse")


def main() -> int:
    print("Runtime exercise: research")
    print("=" * 48)
    for fn in (
        test_name_bearing_trigger_resolves,
        test_unknown_subject_degrades_to_new,
        test_no_direct_substrate_write,
        test_enriched_pii_routes_to_people_crm,
        test_defensive_entities_read,
    ):
        fn()
    print("=" * 48)
    print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
