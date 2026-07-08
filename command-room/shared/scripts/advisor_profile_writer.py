"""Canonical writer for Advisor Profiles (portable persona packs).

An Advisor Profile is a workspace-independent distillation of how a person
thinks, decides, and argues. It is forged in one Command Room and can be seated
as a 'persona' board member in another (consumed by the `boardroom` skill).

Three lifecycle operations live here:
  - write_local_advisor(...)  : persist an imported/modeled pack to the local
                                guest bench at _hq/data/advisors/<slug>.json
  - export_advisor(...)       : write a SHAREABLE pack (self-fidelity only) to
                                _hq/advisors/exported/AdvisorProfile_<Name>_<date>.json
  - import_advisor(...)       : read an external pack file, scrub + validate it,
                                store it locally, emit the imported event

All writes go through the atomic helpers (CONTRACT Rule / Gate 3). No raw
open(path, "w") anywhere.

Privacy invariant (load-bearing): a pack travels between workspaces where local
internal IDs (person_NNN / project_NNN / org_NNN / event seqs) are meaningless
and would leak the source workspace's structure. scrub_internal_ids() strips any
such tokens before a pack is ever written to a local store or an export file.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from atomic_write import atomic_write_json, atomic_append_jsonl
from next_seq import next_seq

SOURCE_SKILL = "advisor-export"
SCHEMA_VERSION = 1

# Tokens that are workspace-local and must never survive into a portable pack.
_INTERNAL_ID_RE = re.compile(r"\b(?:person|project|org|engagement|thread)_[0-9]{3,}\b")


def _events_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "events.jsonl"


def _advisors_dir(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "advisors"


def _exports_dir(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "advisors" / "exported"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "advisor").lower()).strip("-")
    return s or "advisor"


def scrub_internal_ids(obj: Any) -> Any:
    """Recursively strip workspace-local internal IDs from any string in the pack.

    Defensive backstop: the forge step should never put an ID in a pack, but this
    guarantees the invariant at the writer boundary regardless of caller behavior.
    """
    if isinstance(obj, str):
        return _INTERNAL_ID_RE.sub("", obj).strip()
    if isinstance(obj, list):
        return [scrub_internal_ids(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_internal_ids(v) for k, v in obj.items()}
    return obj


def validate_pack(pack: dict) -> list[str]:
    """Light structural validation. Returns a list of human-readable problems
    (empty == valid). Avoids a hard jsonschema dependency so the writer runs in
    any workspace."""
    problems: list[str] = []
    if pack.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    profile = pack.get("profile")
    if not isinstance(profile, dict):
        problems.append("missing 'profile' object")
    else:
        for req in ("display_name", "headline", "mandate_default"):
            if not profile.get(req):
                problems.append(f"profile.{req} is required")
    prov = pack.get("provenance")
    if not isinstance(prov, dict):
        problems.append("missing 'provenance' object")
    else:
        if prov.get("fidelity") not in ("self", "observed"):
            problems.append("provenance.fidelity must be 'self' or 'observed'")
        if prov.get("fidelity") == "observed" and prov.get("shareable"):
            problems.append("observed packs may not be shareable (shareable must be false)")
        if not prov.get("forged_on"):
            problems.append("provenance.forged_on is required")
    # Leak guard: no internal IDs may survive anywhere in the pack.
    if _INTERNAL_ID_RE.search(json.dumps(pack)):
        problems.append("pack contains workspace-local internal IDs (person_/project_/org_ ...)")
    return problems


def _emit(workspace_root: Path, event_type: str, data: dict) -> None:
    events_path = _events_path(workspace_root)
    event = {
        "seq": next_seq(events_path),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "source_skill": SOURCE_SKILL,
        "data": data,
    }
    atomic_append_jsonl(events_path, event)


def write_local_advisor(workspace_root: str | Path, pack: dict) -> Path:
    """Persist an imported or modeled pack to the local guest bench. Emits
    advisor_profile_imported (fidelity 'self', from someone else's export) or
    advisor_profile_modeled (fidelity 'observed', built locally)."""
    workspace_root = Path(workspace_root)
    pack = scrub_internal_ids(pack)
    problems = validate_pack(pack)
    if problems:
        raise ValueError("invalid advisor pack: " + "; ".join(problems))

    name = pack["profile"]["display_name"]
    dest = _advisors_dir(workspace_root) / f"{slugify(name)}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, pack)

    fidelity = pack["provenance"]["fidelity"]
    event_type = "advisor_profile_modeled" if fidelity == "observed" else "advisor_profile_imported"
    _emit(workspace_root, event_type, {
        "display_name": name,
        "role": pack["profile"].get("role"),
        "fidelity": fidelity,
        "source_label": pack["provenance"].get("workspace_origin_label"),
    })
    return dest


def export_advisor(workspace_root: str | Path, pack: dict) -> Path:
    """Write a SHAREABLE pack to the exports folder. Refuses anything that is not
    self-fidelity + shareable. Emits advisor_profile_exported."""
    workspace_root = Path(workspace_root)
    pack = scrub_internal_ids(pack)
    problems = validate_pack(pack)
    if problems:
        raise ValueError("invalid advisor pack: " + "; ".join(problems))

    prov = pack["provenance"]
    if prov.get("fidelity") != "self" or not prov.get("shareable"):
        raise PermissionError(
            "refusing to export: only self-forged, shareable packs may leave the "
            "workspace. Observed/modeled advisors stay local."
        )

    name = pack["profile"]["display_name"]
    today = date.today().isoformat()
    dest = _exports_dir(workspace_root) / f"AdvisorProfile_{slugify(name)}_{today}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, pack)

    _emit(workspace_root, "advisor_profile_exported", {
        "display_name": name,
        "role": pack["profile"].get("role"),
        "fidelity": "self",
        "shareable": True,
        "artifact_path": str(dest),
    })
    return dest


def import_advisor(workspace_root: str | Path, pack_path: str | Path) -> Path:
    """Read an external pack file a colleague shared, scrub + validate, store it
    in the local guest bench. Delegates the event emit to write_local_advisor."""
    pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
    return write_local_advisor(workspace_root, pack)


def list_advisors(workspace_root: str | Path) -> list[dict]:
    """Return the local guest bench: every stored advisor pack with its fidelity.
    Consumed by boardroom (to offer persona seats) and advisor-export list mode."""
    workspace_root = Path(workspace_root)
    advisors_dir = _advisors_dir(workspace_root)
    out: list[dict] = []
    if not advisors_dir.is_dir():
        return out
    for p in sorted(advisors_dir.glob("*.json")):
        try:
            pack = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "path": str(p),
            "display_name": pack.get("profile", {}).get("display_name"),
            "role": pack.get("profile", {}).get("role"),
            "fidelity": pack.get("provenance", {}).get("fidelity"),
        })
    return out
