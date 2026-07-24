#!/usr/bin/env python3
"""Canonical writer for `workspace.*` settings in entities.json (FB-plumbing
item 4 — receipt coverage for config writes).

WHY THIS EXISTS
---------------
Skill-level knobs (FRP1) live in `_hq/data/skill_config/<skill>.json` and every
write there emits a `skill_reconfigured` / `skill_first_run_configured` receipt
via `skill_config_writer.save_skill_config`. But a whole other class of settings
lives in the `workspace` block of entities.json — timezone, the Balance evening
window / personal-calendar declarations, and similar surface knobs — and those
were written by orchestrator/SKILL prose that hand-appended a
`workspace_setting_changed` event (or forgot to). A hand-rolled receipt is a
receipt waiting to be skipped: the big test found a Balance config write that
changed the substrate with NO event behind it.

This module is the code-guaranteed path: it persists the keys atomically AND
emits the typed `workspace_setting_changed` receipt in the SAME call, so the two
can never separate. One event PER changed key, in the exact shape the timezone
handler already used (`{key, old_value, new_value, triggered_by}`), so every
existing reader of `workspace_setting_changed` sees the familiar payload.

`workspace_setting_changed` is an EXISTING events-schema enum member — reused, no
schema change (the item-4 rule: check the enum first, reuse if present).

Mirrors `connector_config.py`'s entities-write pattern (nested-vs-flat container,
version bump, locked atomic write). stdlib + the shared writers only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

RECEIPT_EVENT_TYPE = "workspace_setting_changed"


def _entities_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "entities.json"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _load_full(workspace_root) -> Dict[str, Any]:
    try:
        return json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _workspace_container(ent: Dict[str, Any]) -> Dict[str, Any]:
    """The dict that holds `workspace`, honoring the nested (`entities.*`) vs
    flat entities.json shapes. Creates the `workspace` sub-dict in place and
    returns it (mirrors connector_config._workspace_container)."""
    inner = ent.get("entities") if isinstance(ent.get("entities"), dict) else None
    container = inner if inner is not None else ent
    ws = container.get("workspace")
    if not isinstance(ws, dict):
        ws = {}
        container["workspace"] = ws
    return ws


def _write_entities(workspace_root, ent: Dict[str, Any], holder: str) -> None:
    try:
        ent["version"] = int(ent.get("version", 0)) + 1
    except Exception:
        ent["version"] = 1
    from atomic_write import atomic_write_json_locked
    atomic_write_json_locked(_entities_path(workspace_root), ent, holder=holder)


def set_workspace_settings(
    workspace_root,
    changes: Dict[str, Any],
    *,
    source_skill: str,
    triggered_by: str = "user_explicit",
) -> Dict[str, Any]:
    """Persist one or more `workspace.<key>` settings AND emit the typed
    `workspace_setting_changed` receipt(s) in one call.

    `changes` maps each `workspace` key to its new value (e.g.
    `{"evening_start": "18:00", "balance_default_cadence_days": 14}`). A key
    whose new value equals the stored value is a no-op — neither written nor
    receipted (an idempotent re-save leaves no phantom change trail).

    Returns::

        {"changed": {<key>: {"old": ..., "new": ...}, ...},
         "unchanged": [<key>, ...],
         "events_emitted": <int>}

    One `workspace_setting_changed` event is appended per CHANGED key, shape
    `{key, old_value, new_value, triggered_by}` — the timezone-handler shape,
    so existing readers are unaffected. The events.jsonl append auto-stamps
    seq + ts inside the writer lock. Raises nothing beyond a genuinely broken
    workspace (unwritable entities.json) — the receipt and the write are one
    critical path, never independently swallowed."""
    if not isinstance(changes, dict) or not changes:
        return {"changed": {}, "unchanged": [], "events_emitted": 0}

    ent = _load_full(workspace_root)
    ws = _workspace_container(ent)

    changed: Dict[str, Dict[str, Any]] = {}
    unchanged: list = []
    for key, new_value in changes.items():
        old_value = ws.get(key)
        if old_value == new_value:
            unchanged.append(key)
            continue
        ws[key] = new_value
        changed[key] = {"old": old_value, "new": new_value}

    if not changed:
        return {"changed": {}, "unchanged": unchanged, "events_emitted": 0}

    _write_entities(workspace_root, ent, holder=source_skill)

    from atomic_write import atomic_append_jsonl
    events = [
        {
            # seq + ts auto-stamped inside the events.jsonl writer lock.
            "type": RECEIPT_EVENT_TYPE,
            "source_skill": source_skill,
            "data": {
                "key": key,
                "old_value": delta["old"],
                "new_value": delta["new"],
                "triggered_by": triggered_by,
            },
        }
        for key, delta in changed.items()
    ]
    atomic_append_jsonl(_events_path(workspace_root), events,
                        holder=f"{source_skill}.workspace_setting_changed")

    return {"changed": changed, "unchanged": unchanged,
            "events_emitted": len(events)}


__all__ = ["set_workspace_settings", "RECEIPT_EVENT_TYPE"]
