#!/usr/bin/env python3
"""
v4.6.1 S3 regression — display names come from the resolved record,
never the raw transcript spelling (FINDINGS F-50 P2b: "Myra Samples"
rendered in a widget and a meeting event title while person resolution
had correctly matched person_093 = Mira Sample).

Two layers:

  1. FUNCTIONAL — the exact F-50 P2b shape: a person record with the
     canonical spelling + an aliases.json mapping for the ASR spelling.
     Resolving the RAW spelling must return the record, and the string a
     surface renders (ResolveResult.display_name /
     get_person_display_names(record)[0]) must be the CANONICAL spelling.
  2. PROSE GUARD — the render rule exists where renderers read:
     ENTITY_RESOLVE_PROTOCOL (the authored rule), CONTRACT Rule 4,
     CHAT_ACTION_WIDGET leak-prevention, orchestrator-past-meetings,
     orchestrator-upcoming-meetings, meeting-notes. A refactor that drops
     the rule from any of these fails here.

Run via: python3 tests/run_names_from_entities_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from entity_resolve import resolve  # noqa: E402
from people_writer import get_person_display_names  # noqa: E402

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


def make_workspace() -> Path:
    """The F-50 P2b shape: canonical record + alias for the ASR spelling."""
    ws = Path(tempfile.mkdtemp(prefix="s3_names_"))
    d = ws / "_hq" / "data"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("", encoding="utf-8")
    (d / "entities.json").write_text(json.dumps({
        "version": 1,
        "people": [
            {"id": "person_093", "canonical_name": "Mira Sample",
             "aliases": ["Myra Samples"], "first_seen": "2026-01-01"},
        ],
    }), encoding="utf-8")
    (d / "aliases.json").write_text(json.dumps({
        "mappings": {"people": [
            {"raw": "Myra Samples", "canonical_id": "person_093"},
        ]},
    }), encoding="utf-8")
    return ws


def main() -> int:
    # ------------------------------------------------------------------
    print("[1] functional — raw ASR spelling resolves; rendered name is canonical")
    # ------------------------------------------------------------------
    ws = make_workspace()
    res = resolve(ws, "Myra Samples")
    check("the raw ASR spelling resolves to the person record",
          res is not None and res.record.get("id") == "person_093",
          repr(res))
    if res is not None:
        check("display_name is the CANONICAL spelling, not the ASR spelling",
              res.display_name == "Mira Sample", res.display_name)
        check("display_name never echoes the query's raw spelling",
              res.display_name != "Myra Samples")
        names = get_person_display_names(res.record)
        check("get_person_display_names leads with the canonical spelling",
              names and names[0] == "Mira Sample", str(names))
        check("the raw spelling stays available for MATCHING (alias corpus)",
              "Myra Samples" in names, str(names))
    # canonical query still works and renders identically
    res2 = resolve(ws, "Mira Sample")
    check("the canonical spelling resolves to the same record",
          res2 is not None and res2.record.get("id") == "person_093")

    # ------------------------------------------------------------------
    print("[2] prose guard — the render rule exists where renderers read")
    # ------------------------------------------------------------------
    surfaces = {
        "shared/ENTITY_RESOLVE_PROTOCOL.md": [
            "## Display names",          # the authored rule section
            "canonical_name",
            "Myra Samples",           # the concrete failure, cited
        ],
        "shared/CONTRACT.md": [
            "never a transcript/ASR",    # Rule 4 companion clause
        ],
        "shared/CHAT_ACTION_WIDGET.md": [
            "RESOLVED record's spelling",
        ],
        ("skills/enable-command-room-schedules/references/"
         "orchestrator-past-meetings.md"): [
            "Name spelling",
            "F-50 P2b",
        ],
        ("skills/enable-command-room-schedules/references/"
         "orchestrator-upcoming-meetings.md"): [
            "Name spelling",
            "F-50 P2b",
        ],
        "skills/meeting-notes/SKILL.md": [
            "render from the canonical record",
        ],
        "skills/email-writer/SKILL.md": [
            "canonical_name",
        ],
    }
    for rel, needles in surfaces.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            check(f"{rel} carries {needle!r}", needle in text)

    # ------------------------------------------------------------------
    print(f"\n=== Summary: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        print("FAIL — names-from-entities render contract regressed")
        return 1
    print("OK — names-from-entities render contract holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
