#!/usr/bin/env python3
"""
Runtime exercise for the `advisor-export` skill (Gate 13 / Gate 17).

Exercises the harness-able code paths in shared/scripts/advisor_profile_writer.py
against a real temp workspace: the self/observed export gate, the internal-ID
scrub, structural validation, local-bench write + event emit, and list_advisors.
The LLM-judgment paths (distilling heuristics/positions from substrate) are
covered by the eval layer + Cowork verify loop.

Run from the command-room repo root:
    python tests/runtime_exercise_advisor_export.py

Exits 0 on full green, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import advisor_profile_writer as apw  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def _ok(name: str, detail: str = "") -> None:
    print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    PASS.append(name)


def _fail(name: str, detail: str = "") -> None:
    print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
    FAIL.append(name)


def _self_pack() -> dict:
    return {
        "schema_version": 1,
        "profile": {
            "display_name": "Sam Sample",
            "role": "Co-founder & CFO",
            "org_label": "Acme Co",
            "headline": "Cash-first operator who wants the unit economics before the vision.",
            "mandate_default": "Can we afford it, and what does it do to runway?",
            "decision_heuristics": ["unit economics before narrative"],
            "priorities": ["runway", "gross margin"],
            "risk_posture": "aggressive on growth, conservative on cash",
            "known_positions": [{"topic": "pricing", "position": "raise before discounting"}],
            "pushback_patterns": ["challenges any plan with no payback period"],
            "communication_style": "blunt, numbers-first",
            "blind_spots": ["underweights brand/long-game"],
        },
        "provenance": {
            "fidelity": "self",
            "shareable": True,
            "forged_by_label": "Penelope",
            "source_signal_summary": "14 decisions, 9 transcripts, voice profile",
            "forged_on": "2026-06-12",
            "workspace_origin_label": "Sam Sample's Command Room",
        },
    }


def _events(ws: Path) -> list[dict]:
    p = ws / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_scrub_strips_internal_ids() -> None:
    dirty = {"a": "see person_001 and project_042", "b": ["org_999 here"]}
    cleaned = apw.scrub_internal_ids(dirty)
    blob = json.dumps(cleaned)
    if "person_001" in blob or "project_042" in blob or "org_999" in blob:
        _fail("scrub strips internal IDs", blob)
    else:
        _ok("scrub strips internal IDs")


def test_validate_rejects_observed_shareable() -> None:
    pack = _self_pack()
    pack["provenance"]["fidelity"] = "observed"
    pack["provenance"]["shareable"] = True
    problems = apw.validate_pack(pack)
    if any("observed" in p for p in problems):
        _ok("validate rejects observed+shareable")
    else:
        _fail("validate rejects observed+shareable", str(problems))


def test_self_write_and_export() -> None:
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "_hq" / "data").mkdir(parents=True)
        local = apw.write_local_advisor(ws, _self_pack())
        if not local.exists():
            _fail("self pack writes to local bench")
            return
        _ok("self pack writes to local bench", local.name)
        exported = apw.export_advisor(ws, _self_pack())
        if exported.exists() and exported.name.startswith("AdvisorProfile_"):
            _ok("self pack exports", exported.name)
        else:
            _fail("self pack exports")
        types = [e["type"] for e in _events(ws)]
        if "advisor_profile_imported" in types and "advisor_profile_exported" in types:
            _ok("emits imported + exported events")
        else:
            _fail("emits imported + exported events", str(types))


def test_observed_export_blocked() -> None:
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "_hq" / "data").mkdir(parents=True)
        pack = _self_pack()
        pack["provenance"]["fidelity"] = "observed"
        pack["provenance"]["shareable"] = False
        local = apw.write_local_advisor(ws, pack)
        types = [e["type"] for e in _events(ws)]
        if "advisor_profile_modeled" not in types:
            _fail("observed write emits modeled event", str(types))
            return
        _ok("observed write emits modeled event")
        try:
            apw.export_advisor(ws, pack)
        except PermissionError:
            _ok("observed export is blocked")
        else:
            _fail("observed export is blocked", "export did not raise")


def test_list_advisors() -> None:
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "_hq" / "data").mkdir(parents=True)
        apw.write_local_advisor(ws, _self_pack())
        rows = apw.list_advisors(ws)
        if len(rows) == 1 and rows[0]["display_name"] == "Sam Sample" and rows[0]["fidelity"] == "self":
            _ok("list_advisors returns the bench")
        else:
            _fail("list_advisors returns the bench", str(rows))


def main() -> int:
    print("Runtime exercise: advisor-export")
    print("=" * 48)
    for fn in (
        test_scrub_strips_internal_ids,
        test_validate_rejects_observed_shareable,
        test_self_write_and_export,
        test_observed_export_blocked,
        test_list_advisors,
    ):
        fn()
    print("=" * 48)
    print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
