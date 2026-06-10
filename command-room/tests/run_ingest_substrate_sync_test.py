#!/usr/bin/env python3
"""Structural enforcement test for the INGEST → SUBSTRATE SYNC protocol (v3.14.6+).

THE BUG CLASS THIS GUARDS
-------------------------
Entity extraction (people via people_writer, commitments/decisions via
meeting-notes, orgs via org_proposed) historically lived ONLY inside the
scheduled orchestrators (cr-past-meetings, cr-inbox). Every ON-DEMAND path that
also pulls a transcript or email body — transcript-search, call-prep,
people-crm's 90-day crawl — was built read-only and bypassed that extraction.

Result (M, 2026-05-28): "I pulled a transcript for a certain reason, and it had
an individual, and that person was never actually added as a new individual."
The meeting had never been processed; the on-demand pull read it, showed what
was asked, and discarded the new person.

THE CONTRACT (shared/INGEST_SUBSTRATE_SYNC.md)
----------------------------------------------
No transcript or email is ever read on-demand without its entities being
reconciled into substrate. Every skill that fetches a raw transcript/email
on-demand MUST reference shared/INGEST_SUBSTRATE_SYNC.md and either run the
reconcile pass or state an explicit exemption.

WHAT THIS TEST CHECKS
---------------------
Every skill in COVERED_SKILLS references the protocol (by path or name). If a new
on-demand transcript/email reader lands without the marker, this fails. The fix
is to wire the reconcile pass (or document the exemption IN the skill) — NEVER to
add the skill to a silent exception list, which is how this bug class recurs.

Mirrors run_entity_resolve_enforcement_test.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
PROTOCOL_PATH = ROOT / "shared" / "INGEST_SUBSTRATE_SYNC.md"

# Skills that fetch a raw transcript or email body on-demand (NOT via an
# already-emitted `meeting` event). These are the paths that can encounter an
# unprocessed source and must reconcile it. Scheduled orchestrators
# (cr-past-meetings, cr-inbox) are NOT here — they ARE the extraction layer.
COVERED_SKILLS = [
    "transcript-search",
    "call-prep",
    "people-crm",
]


def _skill_md(name: str) -> Path:
    return SKILLS_DIR / name / "SKILL.md"


def _has_marker(text: str) -> bool:
    markers = (
        "shared/INGEST_SUBSTRATE_SYNC.md",
        "INGEST_SUBSTRATE_SYNC",
        "reconcile pass",
    )
    return any(m in text for m in markers)


def main() -> int:
    failed: list[str] = []
    missing: list[str] = []

    if not PROTOCOL_PATH.exists():
        print(
            f"FAIL — protocol doc missing: {PROTOCOL_PATH.relative_to(ROOT)}",
            file=sys.stderr,
        )
        return 1

    for name in COVERED_SKILLS:
        skill_path = _skill_md(name)
        if not skill_path.exists():
            missing.append(name)
            continue
        text = skill_path.read_text(encoding="utf-8")
        if not _has_marker(text):
            failed.append(name)

    if missing:
        print(
            f"FAIL — {len(missing)} covered skill(s) missing SKILL.md: "
            + ", ".join(missing),
            file=sys.stderr,
        )
    if failed:
        print(
            f"FAIL — {len(failed)} on-demand transcript/email reader(s) lack the "
            f"INGEST_SUBSTRATE_SYNC marker (must reference "
            f"shared/INGEST_SUBSTRATE_SYNC.md and run the reconcile pass or state "
            f"an exemption):",
            file=sys.stderr,
        )
        for name in failed:
            print(f"  - {name}", file=sys.stderr)

    if failed or missing:
        return 1

    print(
        f"OK — all {len(COVERED_SKILLS)} on-demand transcript/email readers "
        f"reference the INGEST_SUBSTRATE_SYNC protocol"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
