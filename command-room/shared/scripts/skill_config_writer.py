#!/usr/bin/env python3
"""Skill config writer — atomic read/write for per-skill first-run config.

Each config-aware skill (customer-health-scorer, okr-tracker, 1on1-tracker,
weekly-review-coach, stalled-project-detection, etc.) stores its first-run
questionnaire answers at:

    _hq/data/skill_config/<skill-name>.json

This helper provides the canonical read/write API so every config-aware skill
shares the same atomic-write + schema-validation contract.

It emits a `skill_first_run_configured` event the first time a skill is
configured, and `skill_reconfigured` when the user re-runs the questionnaire
via "reconfigure <skill-name>".

USAGE:

    from skill_config_writer import load_skill_config, save_skill_config

    # First fire of a config-aware skill
    config = load_skill_config(workspace_root, "customer-health-scorer")
    if config is None:
        # Run questionnaire, gather answers
        answers = {"signal_weights": {...}, "tier_thresholds": {...}}
        save_skill_config(workspace_root, "customer-health-scorer", answers)
        config = load_skill_config(workspace_root, "customer-health-scorer")

    # Read the answers
    weights = config["config"]["signal_weights"]

PER CONTRACT.md Rule 25 + Bug #81 architectural fix:
All writes go through atomic_write_json / atomic_append_jsonl. Direct file
writes are FORBIDDEN.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from atomic_write import atomic_append_jsonl, atomic_write_json  # noqa: E402
from next_seq import next_seq  # noqa: E402

CONFIG_DIR_SUBPATH = ("_hq", "data", "skill_config")
EVENTS_PATH_SUBPATH = ("_hq", "data", "events.jsonl")


def _config_dir(workspace_root: Path) -> Path:
    return workspace_root.joinpath(*CONFIG_DIR_SUBPATH)


def _config_path(workspace_root: Path, skill_name: str) -> Path:
    return _config_dir(workspace_root) / f"{skill_name}.json"


def _events_path(workspace_root: Path) -> Path:
    return workspace_root.joinpath(*EVENTS_PATH_SUBPATH)


def load_skill_config(workspace_root: str | Path, skill_name: str) -> dict[str, Any] | None:
    """Return the stored config for a skill, or None if not configured yet.

    Args:
        workspace_root: workspace root (e.g., the operator's workspace folder).
        skill_name: the kebab-case skill name (matches the SKILL.md frontmatter
                    `name` field).

    Returns:
        The full config payload (dict with schema_version, configured_at,
        skill_name, config) or None if the skill hasn't been configured yet
        OR the file is malformed/unreadable.
    """
    path = _config_path(Path(workspace_root), skill_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_skill_config(
    workspace_root: str | Path,
    skill_name: str,
    config: dict[str, Any],
    schema_version: int = 1,
    is_reconfigure: bool | None = None,
) -> None:
    """Persist config atomically + emit skill_first_run_configured (or _reconfigured) event.

    Args:
        workspace_root: workspace root.
        skill_name: the kebab-case skill name.
        config: the skill-specific config dict (the "config" payload, not the
                full wrapper).
        schema_version: schema version of this config payload. Default 1.
        is_reconfigure: if None (default), auto-detected by checking whether a
                        config already exists for this skill. If True, emits
                        skill_reconfigured. If False, emits skill_first_run_configured.

    Side effects:
        - Writes config to `_hq/data/skill_config/<skill_name>.json` via atomic_write_json.
        - Appends `skill_first_run_configured` or `skill_reconfigured` event to events.jsonl
          via atomic_append_jsonl.
    """
    workspace_root = Path(workspace_root)
    config_dir = _config_dir(workspace_root)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = _config_path(workspace_root, skill_name)
    events_path = _events_path(workspace_root)

    # Auto-detect reconfigure vs first-run if not explicitly specified
    if is_reconfigure is None:
        is_reconfigure = config_path.exists()

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": schema_version,
        "configured_at": now_iso,
        "skill_name": skill_name,
        "config": config,
    }
    atomic_write_json(config_path, payload)

    # Emit the substrate event
    event_type = "skill_reconfigured" if is_reconfigure else "skill_first_run_configured"
    event = {
        "seq": next_seq(events_path),
        "ts": now_iso,
        "type": event_type,
        "source_skill": skill_name,
        "data": {
            "skill_name": skill_name,
            "schema_version": schema_version,
            "config_snapshot": config,
        },
    }
    # atomic_append_jsonl accepts either a list or a single dict
    atomic_append_jsonl(events_path, event)


def wipe_skill_config(workspace_root: str | Path, skill_name: str) -> bool:
    """Remove the stored config for a skill.

    Used by the "reconfigure <skill-name>" UX path — wipe the config so the
    next skill fire re-runs the questionnaire.

    Returns:
        True if a config file existed and was deleted; False if no config
        existed to wipe.
    """
    path = _config_path(Path(workspace_root), skill_name)
    if not path.exists():
        return False
    path.unlink()
    return True


def is_configured(workspace_root: str | Path, skill_name: str) -> bool:
    """Convenience predicate — has this skill been configured for this workspace?"""
    return _config_path(Path(workspace_root), skill_name).exists()


__all__ = [
    "load_skill_config",
    "save_skill_config",
    "wipe_skill_config",
    "is_configured",
]
