#!/usr/bin/env python3
"""Spec 3 — pre-onboarding seed hook (onboarding_seed.py) + SKILL.md touchpoints.

Covers: find/load + light validation; absent-file no-op; declared-entity /
alias / pre-answer / sensitivities accessors; directives ingest routing
(skill-scoped -> skill_custom_writer origin=calibration; workspace-scoped handed
back); the onboarding_seed_ingested event; the archive move (root removed,
_hq/data copy written) + idempotency; and the five onboarding SKILL.md
touchpoints.

House conventions: check(name, cond) prints OK/FAIL, exit 1 on any failure,
auto-discovered by run_all.py. stdlib only.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import onboarding_seed as osd  # noqa: E402
import skill_custom_writer as scw  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="seed_ws_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


SEED = {
    "version": 1,
    "created_ts": "2026-07-08T17:00:00Z",
    "created_by": "operator@example.com",
    "interview": {"call_date": "2026-07-08", "duration_minutes": 40},
    "client": {
        "canonical_name": "Sam Sample",
        "role_title": "CEO",
        "seniority": "owner",
        "timezone": "America/New_York",
        "brain_name_preference": "Athena",
    },
    "orgs": [
        {"name": "Acme Co", "scope": "operating", "is_primary_focus": True,
         "relationship_type": "operating", "aliases": ["Acme", "the shop"]},
    ],
    "projects": [
        {"name": "Northstar Deal", "org_name": "Acme Co", "kind": "deal",
         "priority": "top", "aliases": ["the AZ deal"]},
    ],
    "people": [
        {"name": "Rio Lange", "org_name": "Acme Co", "role": "COO",
         "deep_track": True, "aliases": ["Rio"]},
    ],
    "priorities": [
        {"statement": "Close the Northstar deal", "linked_entity_name": "Northstar Deal"},
    ],
    "voice": {"self_described": ["short, no pleasantries"], "sample_phrases": ["let's move"]},
    "sensitivities": ["the departing-CFO thread"],
    "directives": [
        {"directive": "Group the brief by company, then urgency.", "applies_to": "morning-briefing"},
        {"directive": "Always pair revenue with margin percent.", "applies_to": "operator-report"},
        {"directive": "Refer to deals as 'engagements' everywhere.", "applies_to": "workspace"},
    ],
    "open_questions": ["What's the target close date?"],
}


def _write_seed(ws: Path, obj) -> Path:
    p = ws / "ONBOARDING_SEED.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _events(ws: Path) -> list[dict]:
    p = ws / "_hq" / "data" / "events.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== Spec 3: onboarding seed hook ===")

    # ---- absent file = zero behavior change ----
    ws = _ws()
    check("absent: find_seed -> None", osd.find_seed(ws) is None)
    check("absent: ingest -> None (no-op)", osd.ingest(ws) is None)

    # ---- malformed / missing-required -> None ----
    ws = _ws()
    _write_seed(ws, {"version": 1})  # missing required keys
    check("invalid: missing required keys -> load_seed None", osd.load_seed(ws / "ONBOARDING_SEED.json") is None)
    (ws / "ONBOARDING_SEED.json").write_text("{ not json", encoding="utf-8")
    check("invalid: unparseable -> load_seed None", osd.load_seed(ws / "ONBOARDING_SEED.json") is None)

    # ---- accessors on a valid pack ----
    ws = _ws()
    _write_seed(ws, SEED)
    seed = osd.load_seed(ws / "ONBOARDING_SEED.json")
    check("valid: load_seed returns dict", isinstance(seed, dict))
    pa = osd.pre_answers(seed)
    check("pre_answers: timezone + brain_name + seniority",
          pa.get("timezone") == "America/New_York" and pa.get("brain_name") == "Athena"
          and pa.get("seniority") == "owner")
    ent = osd.declared_entities(seed)
    check("declared_entities: 1 org / 1 project / 1 person",
          len(ent["orgs"]) == 1 and len(ent["projects"]) == 1 and len(ent["people"]) == 1)
    aliases = osd.declared_aliases(seed)
    # self-map + declared aliases for each of the 3 entities
    flat = {(canon, alias) for canon, alias, _ in aliases}
    check("declared_aliases: self-map + alias present",
          ("Acme Co", "Acme Co") in flat and ("Acme Co", "the shop") in flat
          and ("Northstar Deal", "the AZ deal") in flat and ("Rio Lange", "Rio") in flat)
    check("sensitivities: extracted", osd.sensitivities(seed) == ["the departing-CFO thread"])
    check("voice_notes: present", "short, no pleasantries" in osd.voice_notes(seed).get("self_described", []))

    # ---- full ingest ----
    ws = _ws()
    seed_path = _write_seed(ws, SEED)
    summary = osd.ingest(ws)
    check("ingest: returns a summary", isinstance(summary, dict))

    # directives: skill-scoped -> skill_custom_writer; workspace -> handed back
    applied_skills = {s for s, _ in summary["directives"]["applied"]}
    check("directives: morning-briefing + operator-report applied via SCL1 writer",
          applied_skills == {"morning-briefing", "operator-report"})
    mb = scw.load_directives(ws, "morning-briefing")
    check("directives: morning-briefing directive written with origin=calibration",
          len(mb) == 1 and mb[0]["origin"] == "calibration")
    check("directives: workspace-scoped handed back for CLAUDE.md fold",
          summary["directives"]["workspace"] == ["Refer to deals as 'engagements' everywhere."])

    # event
    evs = [e for e in _events(ws) if e["type"] == "onboarding_seed_ingested"]
    check("event: exactly one onboarding_seed_ingested", len(evs) == 1)
    if evs:
        d = evs[0]["data"]
        check("event: payload counts + source",
              d["counts"]["orgs"] == 1 and d["counts"]["directives_applied"] == 2
              and d["counts"]["people"] == 1 and d["sensitivities"] == 1
              and evs[0]["source_skill"] == "command-room-onboarding")

    # archive move + idempotency
    check("archive: file moved to _hq/data/onboarding-seed.json",
          (ws / "_hq" / "data" / "onboarding-seed.json").exists())
    check("archive: root ONBOARDING_SEED.json removed", not seed_path.exists())
    check("idempotent: second ingest is a no-op (None)", osd.ingest(ws) is None)

    # ---- SKILL.md touchpoints ----
    onb = (ROOT / "skills" / "command-room-onboarding" / "SKILL.md").read_text(encoding="utf-8")
    check("touchpoint: 1a.0 pre-flight seed check", "1a.0 — Pre-flight seed check" in onb)
    check("touchpoint: announces the pre-call brief", "Found your pre-call brief" in onb)
    check("touchpoint: declared = anchor truth beside the gate",
          "same authority as the primary-affiliation gate" in onb.lower()
          or "SAME authority as the primary-affiliation gate" in onb)
    check("touchpoint: respects sensitivities", "sensitivities" in onb)
    check("touchpoint: Phase 0 pre-answers", "Seed pre-answers" in onb)
    check("touchpoint: Mirror upgrade (told me vs didn't mention)",
          "what you found they didn't mention" in onb.lower() or "didn't mention" in onb)
    onb_low = onb.lower()
    check("touchpoint: voice secondary + correct citation",
          "secondary evidence" in onb_low
          and "not in `voice_calibration.md`" in onb_low)

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} seed-hook check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL onboarding seed-hook checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
