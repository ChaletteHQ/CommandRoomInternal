#!/usr/bin/env python3
"""
Canonical writer for engagement records in entities.json (v3.17.2+).

Mirrors org_writer.py for engagements — the non-ownership org↔org edges
(fund engagement, portfolio company, deal, advisory, vendor, etc.). Every skill
that creates / updates an engagement MUST go through this module; direct
hand-rolled entities.json writes are forbidden (the bug class people_writer /
org_writer were built to kill, left open for engagements).

Why this exists:
  workspace-manager's `new prospect` command was documented as a raw
  entities.json mutation referencing an engagement writer that did not exist
  (deep-audit handoff §4). Prospect-pipeline tracking was minted by hand with a
  racy sequential id and no schema check. This module is the fix.

PUBLIC API:
  - find_existing_engagement(workspace_root, *, from_org_id, to_org_id, kind=None) -> dict | None
  - create_engagement(workspace_root, *, from_org_id, to_org_id, kind, ...) -> dict
  - update_engagement(workspace_root, engagement_id, **fields) -> dict

INVARIANTS:
  - All writes go through atomic_write_json_locked (cross-process lock + parse check).
  - All collection access goes through entities_io.entities_collection (wrapper-aware).
  - All writes log an event (engagement_created / engagement_updated).
  - Dedup-before-create on (from_org_id, to_org_id, kind).
  - Referential integrity: both endpoints must reference existing orgs.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from atomic_write import atomic_write_json_locked, atomic_append_jsonl  # noqa: E402
from entities_io import entities_collection  # noqa: E402


# Canonical engagement schema fields, mirrored from entities.schema.json
# $defs.engagement.properties. DO NOT add without updating the schema first.
ALLOWED_ENGAGEMENT_FIELDS = {
    "id", "from_org_id", "to_org_id", "kind",
    "label", "notes", "started_at", "ended_at", "is_active", "inferred_from",
}
REQUIRED_ENGAGEMENT_FIELDS = {"id", "from_org_id", "to_org_id", "kind"}
ENGAGEMENT_KINDS = {
    "fund_engagement", "portfolio", "deal", "client", "vendor", "advisor", "partner_other",
}
ENGAGEMENT_ID_RE = re.compile(r"^engagement_[0-9]{3,}$")


class DuplicateEngagementError(Exception):
    def __init__(self, engagement_id, from_org_id, to_org_id, kind):
        self.engagement_id = engagement_id
        super().__init__(
            f"duplicate engagement: {engagement_id} ({from_org_id} -> {to_org_id}, "
            f"kind={kind!r}). Use update_engagement({engagement_id}, ...) to extend, "
            f"or skip_dedup=True for a separate record."
        )


def _now_iso() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _entities_path(ws: Path) -> Path:
    return Path(ws) / "_hq" / "data" / "entities.json"


def _events_path(ws: Path) -> Path:
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def _load_entities(ws: Path) -> dict:
    return json.loads(_entities_path(ws).read_text(encoding="utf-8"))


def _save_entities(ws: Path, data: dict, source_skill: str) -> None:
    data["version"] = int(data.get("version", 0)) + 1
    data["last_updated"] = _now_iso()
    data["last_writer"] = source_skill
    atomic_write_json_locked(_entities_path(ws), data, holder=source_skill)


def _next_engagement_id(engagements: list[dict]) -> str:
    max_n = 0
    for e in engagements:
        m = re.match(r"^engagement_(\d{3,})$", e.get("id", ""))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"engagement_{max_n + 1:03d}"


def _validate_engagement(record: dict) -> None:
    extras = set(record) - ALLOWED_ENGAGEMENT_FIELDS
    if extras:
        raise ValueError(
            "engagement record has fields not allowed by the schema. Update "
            "shared/data-schemas/entities.schema.json $defs.engagement AND "
            "ALLOWED_ENGAGEMENT_FIELDS first. Offending: " + ", ".join(sorted(extras))
        )
    missing = REQUIRED_ENGAGEMENT_FIELDS - set(record)
    if missing:
        raise ValueError(f"engagement missing required fields: {sorted(missing)}")
    if not ENGAGEMENT_ID_RE.match(record["id"]):
        raise ValueError(f"id must match ^engagement_[0-9]{{3,}}$, got: {record['id']!r}")
    if record["kind"] not in ENGAGEMENT_KINDS:
        raise ValueError(f"kind must be one of {sorted(ENGAGEMENT_KINDS)}, got: {record['kind']!r}")
    for date_field in ("started_at", "ended_at"):
        v = record.get(date_field)
        if v is not None:
            try:
                datetime.date.fromisoformat(v)
            except (ValueError, TypeError):
                raise ValueError(f"{date_field} must be ISO date YYYY-MM-DD or null, got: {v!r}")


def _org_exists(orgs: list[dict], org_id: str) -> bool:
    return any(o.get("id") == org_id for o in orgs)


def _log_event(ws: Path, event_type: str, record: dict, source_skill: str, before=None) -> None:
    data: dict[str, Any] = {
        "engagement_id": record.get("id"),
        "from_org_id": record.get("from_org_id"),
        "to_org_id": record.get("to_org_id"),
        "kind": record.get("kind"),
    }
    if before is not None:
        data["before"] = before
    atomic_append_jsonl(_events_path(ws), [{
        "ts": _now_iso(),
        "type": event_type,
        "source_skill": source_skill,
        "data": data,
    }])


# ---------- public API ----------

def find_existing_engagement(workspace_root, *, from_org_id, to_org_id, kind=None):
    data = _load_entities(Path(workspace_root))
    for e in entities_collection(data, "engagements"):
        if e.get("from_org_id") == from_org_id and e.get("to_org_id") == to_org_id:
            if kind is None or e.get("kind") == kind:
                return e
    return None


def create_engagement(
    workspace_root,
    *,
    from_org_id: str,
    to_org_id: str,
    kind: str,
    label: str | None = None,
    notes: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    is_active: bool = True,
    inferred_from: list[str] | None = None,
    source_skill: str = "workspace-manager",
    skip_dedup: bool = False,
) -> dict:
    ws = Path(workspace_root)
    data = _load_entities(ws)
    engagements = entities_collection(data, "engagements")
    orgs = entities_collection(data, "orgs")

    for endpoint, oid in (("from_org_id", from_org_id), ("to_org_id", to_org_id)):
        if not _org_exists(orgs, oid):
            raise ValueError(
                f"{endpoint}={oid!r} does not reference an existing org. "
                f"Create the org via org_writer.create_org first."
            )

    if not skip_dedup:
        existing = next(
            (e for e in engagements
             if e.get("from_org_id") == from_org_id
             and e.get("to_org_id") == to_org_id
             and e.get("kind") == kind),
            None,
        )
        if existing:
            raise DuplicateEngagementError(existing.get("id"), from_org_id, to_org_id, kind)

    record: dict[str, Any] = {
        "id": _next_engagement_id(engagements),
        "from_org_id": from_org_id,
        "to_org_id": to_org_id,
        "kind": kind,
        "is_active": bool(is_active),
    }
    for opt_key, opt_val in (
        ("label", label), ("notes", notes),
        ("started_at", started_at), ("ended_at", ended_at),
    ):
        if opt_val is not None:
            record[opt_key] = opt_val
    if inferred_from:
        record["inferred_from"] = list(inferred_from)

    _validate_engagement(record)
    engagements.append(record)
    _save_entities(ws, data, source_skill)
    _log_event(ws, "engagement_created", record, source_skill)
    return record


def update_engagement(workspace_root, engagement_id: str, *, source_skill: str = "workspace-manager", **fields) -> dict:
    ws = Path(workspace_root)
    data = _load_entities(ws)
    engagements = entities_collection(data, "engagements")
    rec = next((e for e in engagements if e.get("id") == engagement_id), None)
    if rec is None:
        raise ValueError(f"engagement not found: {engagement_id!r}")
    before = dict(rec)
    for k, v in fields.items():
        if k in ("id", "from_org_id", "to_org_id"):
            raise ValueError(f"{k} is immutable on an engagement")
        rec[k] = v
    _validate_engagement(rec)
    _save_entities(ws, data, source_skill)
    _log_event(ws, "engagement_updated", rec, source_skill, before=before)
    return rec


def main(argv: list[str]) -> int:
    print("engagement_writer — import and call create_engagement / update_engagement.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
