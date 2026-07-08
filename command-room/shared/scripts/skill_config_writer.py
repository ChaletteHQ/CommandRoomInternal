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

# Settings-layer C4 — per-skill allowed-key schema. A skill listed here has its
# config keys validated at write (unknown key -> loud reject); a skill absent from
# the map is permissive (back-compat). Loaded once, lazily.
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "data-schemas" / "skill_config.schema.json"
_SCHEMA_CACHE: dict[str, list[str]] | None = None


def _config_schema() -> dict[str, list[str]]:
    """Return {skill: [allowed keys]} from skill_config.schema.json. Never raises —
    a missing/corrupt schema degrades to {} (permissive everywhere), because a
    guardrail must never itself block a write."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE
    try:
        data = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        skills = data.get("skills", {})
        _SCHEMA_CACHE = {k: list(v) for k, v in skills.items() if isinstance(v, list)}
    except (OSError, json.JSONDecodeError, AttributeError):
        _SCHEMA_CACHE = {}
    return _SCHEMA_CACHE


def validate_skill_config(skill_name: str, config: dict[str, Any]) -> list[str]:
    """Return the list of unknown top-level keys in `config` for `skill_name`.

    Empty list = valid (or the skill is not schema-registered, i.e. permissive).
    Non-raising — used by the write-time guard (which raises on a non-empty result)
    and by cleanup's read-only config lint (which reports, never raises)."""
    schema = _config_schema()
    if skill_name not in schema:
        return []
    allowed = set(schema[skill_name])
    return sorted(k for k in (config or {}) if k not in allowed)


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
    origin: str | None = None,
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

    # Settings-layer C4 — validate config keys against the per-skill schema BEFORE
    # any write. Unknown keys are a shape-drift bug (the config analogue of the
    # commitment shape-drift class); reject loudly so a typo/rename can never
    # silently persist a key no reader honors. Permissive for unregistered skills.
    unknown = validate_skill_config(skill_name, config)
    if unknown:
        allowed = _config_schema().get(skill_name, [])
        raise ValueError(
            f"Unknown config key(s) for {skill_name}: {unknown}. "
            f"Allowed keys: {sorted(allowed)}. "
            f"(If this is a new knob, add it to shared/data-schemas/skill_config.schema.json "
            f"in the same commit as the DEFAULTS change — CONTRACT Rule 29.)"
        )

    config_dir = _config_dir(workspace_root)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = _config_path(workspace_root, skill_name)
    events_path = _events_path(workspace_root)

    # Auto-detect reconfigure vs first-run if not explicitly specified
    if is_reconfigure is None:
        is_reconfigure = config_path.exists()

    # SPEC FRP1 — `origin` lets usage-report/coach tell a silent default-acceptance
    # apart from active personalization. Auto: a first fire that just persists the
    # defaults is "first_fire_defaults"; a reconfigure is "tune". Callers override
    # with first_fire_override | m1_batch | drift_reoffer as appropriate.
    if origin is None:
        origin = "tune" if is_reconfigure else "first_fire_defaults"

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
            "origin": origin,
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


def _deep_merge(base: dict, over: dict) -> dict:
    """Recursive merge of `over` onto a copy of `base` (nested dicts merge; scalars
    and lists from `over` win). Never mutates either input."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_config(
    workspace_root: str | Path, skill_name: str, defaults: dict[str, Any]
) -> dict[str, Any]:
    """SPEC FRP1 read path — the saved config deep-merged OVER `defaults`.

    A v+1 skill that adds a new decision won't break an old saved config: the new
    key simply falls back to its default while every previously-saved choice is
    honored. Returns a fresh dict; never mutates `defaults`. No saved config (or an
    unreadable one) → a copy of `defaults`."""
    saved = load_skill_config(workspace_root, skill_name)
    cfg = None
    if isinstance(saved, dict):
        cfg = saved.get("config") if isinstance(saved.get("config"), dict) else saved
    return _deep_merge(defaults, cfg or {})


def lint_skill_configs(workspace_root: str | Path) -> dict[str, list[str]]:
    """Settings-layer C4 — read-only weekly config lint for cleanup.

    Scans every live `_hq/data/skill_config/<skill>.json` and returns
    {skill: [dangling/unknown keys]} for any schema-registered skill whose saved
    config carries a key not in the schema (a deprecated key after a rename, or
    drift). Only reports skills with at least one offending key. Never raises —
    an unreadable config file is skipped. cleanup surfaces the findings in its
    Monday note and can heal via a release-manifest migration; this helper never
    edits config itself (read-only on prefs, FRP1 precedent)."""
    workspace_root = Path(workspace_root)
    cfg_dir = _config_dir(workspace_root)
    out: dict[str, list[str]] = {}
    if not cfg_dir.exists():
        return out
    for p in sorted(cfg_dir.glob("*.json")):
        skill = p.stem
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cfg = data.get("config") if isinstance(data, dict) else None
        if not isinstance(cfg, dict):
            continue
        dangling = validate_skill_config(skill, cfg)
        if dangling:
            out[skill] = dangling
    return out


__all__ = [
    "load_skill_config",
    "save_skill_config",
    "get_config",
    "wipe_skill_config",
    "is_configured",
    "validate_skill_config",
    "lint_skill_configs",
]
