#!/usr/bin/env python3
"""SPEC A8 — validate the workspace_mini regression fixture. Unit tier. House
conventions: exit 1 on any failure, auto-discovered by run_all.py.

Renaming an event type in the schema that the fixture uses turns this RED (the
validator reads the enum live) — the intended schema-evolution tripwire."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import output_exercise_lib as lib  # noqa: E402

FIXTURE = lib.FIXTURE
_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def main() -> int:
    # ---- entities present + flat live shape (threads key, not legacy projects) ----
    ent = json.loads((FIXTURE / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    root = ent.get("entities", ent)
    people = root.get("people", [])
    orgs = root.get("orgs", [])
    threads = root.get("threads", [])
    check("entities: 8 people", len(people) == 8)
    check("entities: 3 orgs", len(orgs) == 3)
    check("entities: 4 threads (flat live shape, not legacy 'projects')",
          len(threads) == 4 and "projects" not in root)
    person_ids = {p["id"] for p in people}
    org_ids = {o["id"] for o in orgs}
    thread_ids = {t["id"] for t in threads}
    live_ids = person_ids | org_ids | thread_ids

    # ---- every event validates + seq strictly increasing ----
    events = []
    ep = FIXTURE / "_hq" / "data" / "events.jsonl"
    for i, line in enumerate(ep.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    ev_violations = []
    for ev in events:
        ev_violations += [f"seq {ev.get('seq')}: {v}" for v in lib.validate_event(ev)]
    check("events: all pass the schema mini-validator", not ev_violations,
          "; ".join(ev_violations[:5]))
    seqs = [e["seq"] for e in events]
    check("events: seq strictly increasing from 1", seqs == list(range(1, len(seqs) + 1)))

    # ---- the derivations the memo exercise depends on ----
    pricing = [e["seq"] for e in events
               if e.get("type") == "decision"
               and e.get("primary_thread_id") == "project_001"
               and "pricing" in (e.get("data", {}).get("topic", "") + e.get("data", {}).get("summary", "")).lower()]
    check("events: exactly 3 pricing decisions on project_001 (seqs 8,14,20)",
          pricing == [8, 14, 20], str(pricing))
    # a malformed legacy commitment exists (flat 'owner' string + 'state', no owner_id)
    legacy = [e for e in events if e.get("type") == "commitment"
              and "owner" in e.get("data", {}) and "owner_id" not in e.get("data", {})]
    check("events: one malformed legacy commitment (defensive-reader bait)", len(legacy) == 1)

    # ---- alias liveness: every mapping points at a live id ----
    aliases = json.loads((FIXTURE / "_hq" / "data" / "aliases.json").read_text(encoding="utf-8"))
    dead = []
    for group in aliases.get("mappings", {}).values():
        for m in group:
            if m.get("canonical_id") not in live_ids:
                dead.append(m.get("canonical_id"))
    check("aliases: every mapping points at a live entity id", not dead, str(dead))

    # ---- project folder present ----
    check("project folder exists with PROJECT_BRAIN + SESSION_NOTES",
          (FIXTURE / "Acme Co - Sourcing Bot" / "PROJECT_BRAIN.md").exists()
          and (FIXTURE / "Acme Co - Sourcing Bot" / "SESSION_NOTES.md").exists())

    # ---- integrity_check subprocess (read-only) ----
    proc = subprocess.run(
        [sys.executable, str(ROOT / "shared" / "scripts" / "integrity_check.py"), str(FIXTURE)],
        capture_output=True, text=True, timeout=60)
    # integrity_check exits 1 on ERROR-level findings; warnings are tolerated.
    check("integrity_check: no ERROR-level findings on the fixture",
          proc.returncode == 0,
          (proc.stdout or proc.stderr).strip()[-300:])

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} fixture check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL workspace_mini fixture checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
