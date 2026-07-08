#!/usr/bin/env python3
"""
Runtime exercise for the `boardroom` skill (Gate 13 / Gate 17).

Exercises the harness-able paths: the bench config round-trips through
skill_config_writer, the new event type is registered in the schema enum, and
boardroom's consumer read of the advisor guest bench (list_advisors) works. The
LLM-judgment paths (per-seat parallel reasoning, conflict-map synthesis, the
.docx memo) are covered by the eval layer + Cowork verify loop.

Run from the command-room repo root:
    python tests/runtime_exercise_boardroom.py

Exits 0 on full green, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import skill_config_writer as scw  # noqa: E402
import advisor_profile_writer as apw  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def _ok(name: str, detail: str = "") -> None:
    print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    PASS.append(name)


def _fail(name: str, detail: str = "") -> None:
    print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
    FAIL.append(name)


def test_event_type_in_enum() -> None:
    schema = json.loads((ROOT / "shared" / "data-schemas" / "events.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["type"]["enum"]
    missing = [t for t in ("board_convened", "advisor_profile_imported", "advisor_profile_modeled") if t not in enum]
    if missing:
        _fail("new event types registered in enum", f"missing {missing}")
    else:
        _ok("new event types registered in enum")


def test_bench_config_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "_hq" / "data").mkdir(parents=True)
        bench = {
            "seats": [
                {"seat_id": "cfo", "type": "archetype", "name": "CFO",
                 "mandate": "Can we afford it?"},
                {"seat_id": "guest_sam", "type": "persona", "name": "Sam Sample",
                 "mandate": "Cash-first", "persona_ref": "_hq/data/advisors/sam-sample.json"},
            ]
        }
        scw.save_skill_config(ws, "boardroom", bench)
        loaded = scw.load_skill_config(ws, "boardroom")
        if not loaded:
            _fail("bench config round-trips")
            return
        seats = loaded["config"]["seats"]
        types = {s["type"] for s in seats}
        if types == {"archetype", "persona"} and len(seats) == 2:
            _ok("bench config round-trips (archetype + persona seats)")
        else:
            _fail("bench config round-trips", str(seats))


def test_bench_six_seat_cap_contract() -> None:
    # The cap is enforced in the SKILL.md setup flow (LLM-side); this asserts the
    # contract is documented so the runtime check has a spec to verify against.
    skill = (ROOT / "skills" / "boardroom" / "SKILL.md").read_text(encoding="utf-8")
    if "six" in skill.lower() and "cap" in skill.lower():
        _ok("six-seat cap documented in SKILL.md")
    else:
        _fail("six-seat cap documented in SKILL.md")


def test_consumes_advisor_bench() -> None:
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "_hq" / "data").mkdir(parents=True)
        apw.write_local_advisor(ws, {
            "schema_version": 1,
            "profile": {"display_name": "Bo Sample", "headline": "Risk lens",
                        "mandate_default": "What breaks?"},
            "provenance": {"fidelity": "observed", "shareable": False, "forged_on": "2026-06-12"},
        })
        rows = apw.list_advisors(ws)
        if rows and rows[0]["display_name"] == "Bo Sample":
            _ok("boardroom can read advisor guest bench (consumer wire)")
        else:
            _fail("boardroom can read advisor guest bench", str(rows))


def main() -> int:
    print("Runtime exercise: boardroom")
    print("=" * 48)
    for fn in (
        test_event_type_in_enum,
        test_bench_config_roundtrip,
        test_bench_six_seat_cap_contract,
        test_consumes_advisor_bench,
    ):
        fn()
    print("=" * 48)
    print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
