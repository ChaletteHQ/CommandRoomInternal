#!/usr/bin/env python3
"""Onboarding seed-pack ingest (Spec 3 — the pre-onboarding seed hook).

An operator may drop an `ONBOARDING_SEED.json` at the client workspace root
before install day, distilled from a recorded pre-onboarding interview
(schema: shared/data-schemas/onboarding_seed.schema.json). If present,
command-room-onboarding Phase 1a ingests it as **anchor truth** — declared
orgs/projects/people/aliases/priorities carry the same authority as the
primary-affiliation gate; the connector scan enriches and adds, but never
overrides a declared fact.

This module does the mechanical, id-independent parts of the ingest so the
onboarding prose (which mints entity ids during the scan) stays the single
owner of entities.json:

  - find + load + light-validate the pack
  - expose declared entities / aliases / Phase-0 pre-answers / voice / sensitivities
    for the onboarding scan to consume (seed-first, then enrich)
  - ingest the pack's `directives[]` through the SCL1 writer
    (skill-scoped -> _hq/custom/<skill>.md, origin='calibration'; workspace-scoped
    handed back for onboarding to fold into CLAUDE.md/BUSINESS_CONTEXT)
  - append the `onboarding_seed_ingested` event (registered enum; consumers:
    coach, usage-report, update-bridge)
  - move the pack to _hq/data/onboarding-seed.json (additive — the original is
    relocated, never deleted content)

Absent file = zero behavior change (returns None). Never raises to the caller.

Note on voice (correction registry 2026-07-01): the interview transcript is
SPOKEN voice and is secondary to written-voice evidence. That principle lives in
command-room-onboarding/SKILL.md's Phase-1 voice-scan section — NOT in
VOICE_CALIBRATION.md (the schema's original citation was wrong). onboarding feeds
seed.voice into BRAND_VOICE.md as secondary evidence per that section.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from atomic_write import atomic_append_jsonl, atomic_write_json  # noqa: E402
import skill_custom_writer as scw  # noqa: E402

SEED_FILENAME = "ONBOARDING_SEED.json"
ARCHIVE_SUBPATH = ("_hq", "data", "onboarding-seed.json")
EVENTS_SUBPATH = ("_hq", "data", "events.jsonl")

_REQUIRED_TOP = ("version", "created_ts", "created_by", "interview", "client")


# ---------------------------------------------------------------------------
# Find + load
# ---------------------------------------------------------------------------


def find_seed(workspace_root: str | Path) -> Path | None:
    """Return the path to ONBOARDING_SEED.json at the workspace root, or None."""
    p = Path(workspace_root) / SEED_FILENAME
    return p if p.exists() else None


def load_seed(path: str | Path) -> dict[str, Any] | None:
    """Parse + light-validate a seed pack. Returns the dict, or None if the file
    is missing, unparseable, or missing a required top-level key. Never raises."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(k in data for k in _REQUIRED_TOP):
        return None
    client = data.get("client")
    if not isinstance(client, dict) or not client.get("canonical_name"):
        return None
    return data


# ---------------------------------------------------------------------------
# Declared truth accessors (for the onboarding entity-build to seed first)
# ---------------------------------------------------------------------------


def pre_answers(seed: dict[str, Any]) -> dict[str, Any]:
    """Phase-0 setup answers the pack pre-fills (timezone, brain name). Keys are
    omitted when the pack didn't cover them — the widget asks for the rest."""
    client = seed.get("client", {}) if isinstance(seed.get("client"), dict) else {}
    out: dict[str, Any] = {}
    if client.get("timezone"):
        out["timezone"] = client["timezone"]
    if client.get("brain_name_preference"):
        out["brain_name"] = client["brain_name_preference"]
    if client.get("seniority"):
        out["seniority"] = client["seniority"]  # drives the primary-affiliation gate
    return out


def declared_entities(seed: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Normalized declared orgs/projects/people for the scan to seed FIRST
    (anchor truth), then enrich. Names, not ids — ids are minted downstream."""
    def _lst(key: str) -> list[dict[str, Any]]:
        v = seed.get(key)
        return [x for x in v if isinstance(x, dict) and x.get("name")] if isinstance(v, list) else []
    return {"orgs": _lst("orgs"), "projects": _lst("projects"), "people": _lst("people")}


def declared_aliases(seed: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(canonical_name, alias, kind) tuples for onboarding to write into
    aliases.json once ids are minted. Includes each entity's own name as a
    self-mapping. kind ∈ {people, orgs, projects}."""
    out: list[tuple[str, str, str]] = []
    ent = declared_entities(seed)
    for kind in ("orgs", "projects", "people"):
        for e in ent[kind]:
            name = e["name"]
            out.append((name, name, kind))
            for a in e.get("aliases", []) or []:
                if isinstance(a, str) and a.strip():
                    out.append((name, a.strip(), kind))
    return out


def sensitivities(seed: dict[str, Any]) -> list[str]:
    v = seed.get("sensitivities")
    return [s for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []


def voice_notes(seed: dict[str, Any]) -> dict[str, Any]:
    v = seed.get("voice")
    return v if isinstance(v, dict) else {}


# ---------------------------------------------------------------------------
# Directives ingest (through the SCL1 writer)
# ---------------------------------------------------------------------------


def ingest_directives(workspace_root: str | Path, seed: dict[str, Any]) -> dict[str, list]:
    """Ingest the pack's directives[]. Skill-scoped directives (applies_to = a
    skill name) go through skill_custom_writer.add_directive(origin='calibration')
    — the file waits for that skill's SCL1 adoption if it hasn't adopted yet.
    Workspace-scoped directives (applies_to = 'workspace' or omitted) are handed
    back for onboarding to fold into CLAUDE.md/BUSINESS_CONTEXT.

    Returns {applied: [(skill, directive_id)], workspace: [text], rejected: [(text, reason)]}.
    """
    workspace_root = Path(workspace_root)
    result: dict[str, list] = {"applied": [], "workspace": [], "rejected": []}
    directives = seed.get("directives")
    if not isinstance(directives, list):
        return result
    for d in directives:
        if not isinstance(d, dict):
            continue
        text = (d.get("directive") or "").strip()
        if not text:
            continue
        applies_to = (d.get("applies_to") or "workspace").strip()
        if applies_to in ("", "workspace"):
            result["workspace"].append(text)
            continue
        res = scw.add_directive(workspace_root, applies_to, text,
                                origin="calibration", source_skill="command-room-onboarding")
        if res.get("ok"):
            result["applied"].append((applies_to, res["directive_id"]))
        else:
            result["rejected"].append((text, res.get("reason")))
    return result


# ---------------------------------------------------------------------------
# Event + archive
# ---------------------------------------------------------------------------


def _emit_ingested(workspace_root: Path, seed: dict[str, Any], summary: dict[str, Any]) -> None:
    ent = declared_entities(seed)
    payload = {
        "seed_version": seed.get("version"),
        "created_by": seed.get("created_by"),
        "call_date": (seed.get("interview", {}) or {}).get("call_date"),
        "counts": {
            "orgs": len(ent["orgs"]),
            "projects": len(ent["projects"]),
            "people": len(ent["people"]),
            "priorities": len(seed.get("priorities", []) or []),
            "directives_applied": len(summary.get("directives", {}).get("applied", [])),
            "aliases": len(declared_aliases(seed)),
        },
        "sensitivities": len(sensitivities(seed)),
        "moved_to": "_hq/data/onboarding-seed.json",
    }
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "onboarding_seed_ingested",
        "source_skill": "command-room-onboarding",
        "data": payload,
    }
    try:
        atomic_append_jsonl(workspace_root.joinpath(*EVENTS_SUBPATH), event)
    except Exception:
        pass


def archive_seed(workspace_root: str | Path, seed_path: str | Path, seed: dict[str, Any]) -> Path:
    """Relocate the pack to _hq/data/onboarding-seed.json (atomic) and remove the
    root copy. Additive doctrine: content is moved, never lost. Returns the dest."""
    workspace_root = Path(workspace_root)
    dest = workspace_root.joinpath(*ARCHIVE_SUBPATH)
    atomic_write_json(dest, seed)
    try:
        Path(seed_path).unlink()
    except OSError:
        pass
    return dest


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def ingest(workspace_root: str | Path) -> dict[str, Any] | None:
    """Full pre-flight ingest. Returns a summary dict onboarding uses to render
    the announce line + the Phase-2 Mirror upgrade, or None if no pack is present.

    The summary carries the declared truth (entities/aliases/pre-answers/voice/
    sensitivities) so onboarding seeds those FIRST, then lets the scan enrich.
    """
    workspace_root = Path(workspace_root)
    path = find_seed(workspace_root)
    if path is None:
        return None
    seed = load_seed(path)
    if seed is None:
        return None

    directives = ingest_directives(workspace_root, seed)
    summary = {
        "seed": seed,
        "pre_answers": pre_answers(seed),
        "declared": declared_entities(seed),
        "aliases": declared_aliases(seed),
        "sensitivities": sensitivities(seed),
        "voice": voice_notes(seed),
        "directives": directives,
        "priorities": seed.get("priorities", []) or [],
    }
    _emit_ingested(workspace_root, seed, summary)
    summary["archived_to"] = str(archive_seed(workspace_root, path, seed))
    return summary


__all__ = [
    "find_seed",
    "load_seed",
    "pre_answers",
    "declared_entities",
    "declared_aliases",
    "sensitivities",
    "voice_notes",
    "ingest_directives",
    "archive_seed",
    "ingest",
]
