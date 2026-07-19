#!/usr/bin/env python3
"""Canonical typed writer for threads/projects — the entity type that had NO
safe writer (deep-audit 2026-05-29, finding #6). Threads are the spine of the
workspace (every event carries a thread id; folders, briefings, the Orgs Map
all hang off them) yet were hand-rolled from LLM prose with a racy max+1 id and
no schema validation — the exact bug class org_writer/people_writer were built
to kill, left open for the most-written type.

Mirrors org_writer: ALLOWED/REQUIRED field maps, schema validation, dedup,
atomic locked write, and a canonical `thread_created` event. Routes ALL
collection access through `entities_collection` (the wrapper-aware floor) so a
new thread lands where the readers look on both nested and flat workspaces.

Schema-reality notes baked in (verified against the live substrate):
  - real threads carry `canonical_name` (schema's project def names only
    `display_name`) — both are allowed;
  - real statuses include `scoping` / `resolved` / `exploring` beyond the
    schema enum — VALID_STATUSES is the observed superset (schema enum should
    be widened to match);
  - `stage` is an integer per schema but legacy ingest parsers wrote it as a
    string — non-int stage is coerced to None;
  - `roster_overrides` (brain-substrate fix) is allowed.

stdlib only.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

from atomic_write import atomic_write_json_locked, atomic_append_jsonl  # noqa: E402
from entities_io import entities_collection  # noqa: E402

THREAD_ID_RE = re.compile(r"^project_[a-z0-9_]+$")

ALLOWED_THREAD_FIELDS = {
    "id", "canonical_name", "display_name", "folder_name", "status", "stage",
    "affiliation_id", "org_id", "parent_thread_id", "spawned_from_thread_id",
    "cross_refs", "kind", "project_class", "owner_person_id",
    "stakeholder_person_ids", "last_activity", "next_step", "success_criteria",
    "first_seen", "session_count", "dormancy_reviewed_at", "archived_at",
    "archive_reason", "roster_overrides", "deal",
}
REQUIRED_THREAD_FIELDS = {"id", "status"}

# --- Deal object (SPEC PIPE1) — allowed ONLY on kind="deal" threads. --------
# Single source for the enums; deal_state.py (the only writer of deal.* fields)
# imports these. entities.schema.json $defs.project.deal mirrors them.
DEAL_STAGES = ("lead", "qualified", "proposal_sent", "negotiating")
DEAL_LOSS_REASONS = (
    "no_decision", "price", "competitor", "diy", "timing", "bad_fit", "other",
)
DEAL_OUTCOMES = ("won", "lost")
ALLOWED_DEAL_FIELDS = {
    "value", "currency", "stage", "stage_entered", "expected_close",
    "forecast_category", "source", "outcome", "loss_reason", "loss_note",
    "opened_at", "closed_at",
}
DEAL_FORECAST_CATEGORIES = ("commit", "best_case", "pipeline")

# Observed superset (schema enum is missing exploring/scoping/resolved).
VALID_STATUSES = {
    "active", "dormant", "paused", "blocked", "archived",
    "exploring", "scoping", "resolved",
}

# Legacy → canonical guidance surfaced in validation errors.
FORBIDDEN_THREAD_FIELDS = {
    "name": "(use 'canonical_name')",
    "primary_project_id": "(that's an EVENT field — use 'parent_thread_id' on a thread)",
    "members": "(do NOT store membership — derive it via thread_roster.derive_roster)",
    "created_at": "(remove — track via thread_created event in events.jsonl)",
    "created_by": "(remove — track via thread_created event in events.jsonl)",
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.date.today().isoformat()


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


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower().strip())
    return re.sub(r"_+", "_", s).strip("_") or "thread"


def _threads(data: dict) -> list:
    """Live thread collection. Real data stores under `threads`; the legacy
    schema also names it `projects`. Prefer the one that already has rows."""
    threads = entities_collection(data, "threads")
    projects = entities_collection(data, "projects")
    if projects and not threads:
        return projects
    return threads


def _next_project_id(threads: list) -> str:
    max_n = 0
    for t in threads:
        m = re.match(r"^project_(\d{3,})$", t.get("id", ""))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"project_{max_n + 1:03d}"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _validate_thread(record: dict) -> None:
    extras = set(record) - ALLOWED_THREAD_FIELDS
    if extras:
        msgs = []
        for k in sorted(extras):
            hint = FORBIDDEN_THREAD_FIELDS.get(k, "(not in schema)")
            msgs.append(f"  - {k!r} → {hint}")
        raise ValueError(
            "thread record has fields not allowed by the schema. If you "
            "genuinely need a new field, update entities.schema.json $defs.project "
            "AND ALLOWED_THREAD_FIELDS first.\n" + "\n".join(msgs))
    missing = REQUIRED_THREAD_FIELDS - set(record)
    if missing:
        raise ValueError(f"thread record missing required fields: {sorted(missing)}")
    if not THREAD_ID_RE.match(record["id"]):
        raise ValueError(f"id must match ^project_[a-z0-9_]+$, got: {record['id']!r}")
    if record.get("status") not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(VALID_STATUSES)}, got: {record.get('status')!r}")
    stage = record.get("stage")
    if stage is not None and (not isinstance(stage, int) or isinstance(stage, bool)):
        raise ValueError(f"stage must be an integer or null, got: {stage!r}")
    if "deal" in record:
        _validate_deal(record)


def _validate_deal(record: dict) -> None:
    """SPEC PIPE1 — the deal object is allowed ONLY on kind='deal' threads,
    with enum-validated sales fields. Guidance style mirrors
    FORBIDDEN_THREAD_FIELDS: name the fix, not just the failure. Write deal.*
    ONLY through shared/scripts/deal_state.py (the single writer/closure
    path); this validation is the schema floor beneath it."""
    deal = record["deal"]
    if deal is None:
        raise ValueError("deal must be an object, not null — omit the field entirely")
    if not isinstance(deal, dict):
        raise ValueError(f"deal must be an object, got: {type(deal).__name__}")
    if record.get("kind") != "deal":
        raise ValueError(
            "a deal object is only allowed on kind='deal' threads "
            f"(this thread's kind: {record.get('kind')!r}). Set kind='deal' "
            "or drop the deal object.")
    extras = set(deal) - ALLOWED_DEAL_FIELDS
    if extras:
        raise ValueError(
            "deal object has fields not in the schema: "
            f"{sorted(extras)}. Allowed: {sorted(ALLOWED_DEAL_FIELDS)}. "
            "Update entities.schema.json $defs.project.deal AND "
            "ALLOWED_DEAL_FIELDS first if you genuinely need a new field.")
    stage = deal.get("stage")
    if stage is not None and stage not in DEAL_STAGES:
        raise ValueError(
            f"deal.stage must be one of {list(DEAL_STAGES)}, got: {stage!r}. "
            "Won/lost are the terminal deal.outcome, not stages; the integer "
            "project-lifecycle stage is a different field.")
    outcome = deal.get("outcome")
    if outcome is not None and outcome not in DEAL_OUTCOMES:
        raise ValueError(
            f"deal.outcome must be won, lost, or null, got: {outcome!r} "
            "(set only via deal_state.close_deal)")
    reason = deal.get("loss_reason")
    if reason is not None and reason not in DEAL_LOSS_REASONS:
        raise ValueError(
            f"deal.loss_reason must be one of {list(DEAL_LOSS_REASONS)}, got: {reason!r}")
    if outcome == "lost" and reason is None:
        raise ValueError(
            "deal.outcome='lost' requires a loss_reason "
            f"(one of {list(DEAL_LOSS_REASONS)}) — close via "
            "deal_state.close_deal, which enforces this")
    fc = deal.get("forecast_category")
    if fc is not None and fc not in DEAL_FORECAST_CATEGORIES:
        raise ValueError(
            f"deal.forecast_category must be one of {list(DEAL_FORECAST_CATEGORIES)} "
            f"or null, got: {fc!r}")
    value = deal.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ValueError(
            f"deal.value must be a number or null, got: {value!r} — never a "
            "string or range; store null + a note in deal.source for ranges")


def _coerce(record: dict) -> dict:
    """Drop forbidden keys and coerce a legacy string `stage` to None so an
    ingest-parser record lands canonical instead of failing."""
    out = {k: v for k, v in record.items() if k not in FORBIDDEN_THREAD_FIELDS}
    if isinstance(out.get("stage"), str):
        out["stage"] = None
    return out


def _log_event(ws: Path, event_type: str, record: dict, source_skill: str) -> None:
    event = {
        "ts": _now_iso(),
        "type": event_type,
        "source_skill": source_skill,
        "data": {
            "primary_thread_id": record.get("id"),
            "canonical_name": record.get("canonical_name") or record.get("display_name"),
            "status": record.get("status"),
        },
    }
    atomic_append_jsonl(_events_path(ws), [event])


def find_existing_thread(workspace_root: str | Path, *,
                         folder_name: str | None = None,
                         canonical_name: str | None = None) -> dict | None:
    """Match by folder_name exact, else canonical/display name (normalized)."""
    data = _load_entities(Path(workspace_root))
    threads = _threads(data)
    if folder_name:
        for t in threads:
            if t.get("folder_name") == folder_name:
                return t
    if canonical_name:
        target = _norm(canonical_name)
        for t in threads:
            if _norm(t.get("canonical_name") or t.get("display_name")) == target:
                return t
    return None


def create_thread(workspace_root: str | Path, *,
                  canonical_name: str,
                  status: str = "active",
                  folder_name: str | None = None,
                  kind: str | None = None,
                  affiliation_id: str | None = None,
                  org_id: str | None = None,
                  owner_person_id: str | None = None,
                  stakeholder_person_ids: list[str] | None = None,
                  parent_thread_id: str | None = None,
                  spawned_from_thread_id: str | None = None,
                  first_seen: str | None = None,
                  thread_id: str | None = None,
                  deal: dict | None = None,
                  source_skill: str = "unknown",
                  skip_dedup: bool = False) -> dict:
    """Create a new thread record. Dedups by folder_name → canonical_name,
    validates against the schema, writes through the wrapper-aware collection
    (so it lands where readers look), and emits a `thread_created` event.
    """
    workspace_root = Path(workspace_root)

    if not skip_dedup:
        existing = find_existing_thread(workspace_root, folder_name=folder_name,
                                        canonical_name=canonical_name)
        if existing is not None:
            raise ValueError(
                f"thread already exists: {existing.get('id')} "
                f"({existing.get('canonical_name') or existing.get('display_name')})")

    data = _load_entities(workspace_root)
    threads = _threads(data)

    record: dict[str, Any] = {
        "id": thread_id or _next_project_id(threads),
        "canonical_name": canonical_name.strip(),
        "folder_name": folder_name or _slugify(canonical_name),
        "status": status,
        "first_seen": first_seen or _today(),
    }
    if kind:                    record["kind"] = kind
    if affiliation_id:          record["affiliation_id"] = affiliation_id
    if org_id:                  record["org_id"] = org_id
    if owner_person_id:         record["owner_person_id"] = owner_person_id
    if stakeholder_person_ids:  record["stakeholder_person_ids"] = list(stakeholder_person_ids)
    if parent_thread_id:        record["parent_thread_id"] = parent_thread_id
    if spawned_from_thread_id:  record["spawned_from_thread_id"] = spawned_from_thread_id
    if deal is not None:        record["deal"] = deal

    record = _coerce(record)
    _validate_thread(record)

    threads.append(record)
    _save_entities(workspace_root, data, source_skill)
    _log_event(workspace_root, "thread_created", record, source_skill)
    return record


def update_thread(workspace_root: str | Path, thread_id: str, *,
                  source_skill: str = "unknown", **fields) -> dict:
    """Update allowed fields on an existing thread (e.g. status, last_activity,
    next_step, roster_overrides). Validates, atomic-writes, emits
    `thread_updated`."""
    workspace_root = Path(workspace_root)
    data = _load_entities(workspace_root)
    threads = _threads(data)
    target = next((t for t in threads if t.get("id") == thread_id), None)
    if target is None:
        raise ValueError(f"thread not found: {thread_id}")
    for k, v in fields.items():
        target[k] = v
    coerced = _coerce(target)
    target.clear(); target.update(coerced)
    _validate_thread(target)
    _save_entities(workspace_root, data, source_skill)
    _log_event(workspace_root, "thread_updated", target, source_skill)
    return target
