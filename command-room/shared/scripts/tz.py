"""
Workspace timezone helper — single source of truth for user-facing time rendering.

Read entities.json `workspace.user_timezone` once per resolve. Skills emitting
timestamps in chat call `to_local(value, workspace_path=...)` to convert from
upstream connector formats (Granola naive ISO, Calendar RFC 3339 with offset,
Gmail RFC 2822) to the workspace TZ.

Design contract (v3.11.1 — bug-fix for morning-brief silent UTC fallback):
- Connector inputs are NEVER trusted to be in user TZ. Always normalize.
- The chat renderer does NOT auto-convert; skills MUST localize before emitting.
- Callers MUST supply the workspace root, either as `workspace_path=` kwarg or via
  the `CR_WORKSPACE` env var. Walking up from this file's location was unreliable
  inside plugin clones (`shared/scripts/tz.py` lives outside the workspace, so the
  walk-up never resolved `_hq/data/entities.json` and the silent UTC fallback
  caused wrong-but-plausible timestamps to render — see B1 of the 2026-05-20
  morning-brief bug report).
- If no workspace_path resolves, raise `TZResolutionError`. Callers may catch and
  degrade to a printed warning ("⚠️ TZ unresolved — times shown as UTC") but the
  failure is no longer silent.

History:
- v2.10.9 (2026-04-29) — initial implementation. M's workspace TZ = America/Los_Angeles.
- v2.14.17 — tolerate both entities.json shapes (nested under `entities` and flat).
- v3.11.1 (2026-05-20) — require explicit workspace_path; raise on failure
  instead of silent UTC fallback.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional, Union

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # Python <3.9
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore

logger = logging.getLogger(__name__)


class TZResolutionError(RuntimeError):
    """Raised when the workspace timezone cannot be resolved.

    Callers should surface a plain-English message per CONTRACT.md Rule 8
    and degrade gracefully (e.g. fall back to UTC + an explicit "⚠️ TZ
    unresolved" tag in the rendered output) rather than letting the
    exception propagate to chat as a traceback.
    """


_ENTITIES_REL = Path("_hq") / "data" / "entities.json"


def _resolve_workspace_path(workspace_path: Union[str, Path, None]) -> Optional[Path]:
    """Resolve the workspace root via explicit arg → env var → None.

    No walk-up fallback. Plugin source lives OUTSIDE the workspace clone, so
    walking up from this file's location consistently failed to find
    entities.json in production — the prior silent UTC fallback masked the
    miss for ~7 months.
    """
    if workspace_path:
        p = Path(workspace_path).expanduser().resolve()
        if (p / _ENTITIES_REL).exists():
            return p
        # Caller passed a path but entities.json isn't there — fall through to
        # env var so a misconfigured caller doesn't silently override a working
        # CR_WORKSPACE.
        logger.warning(
            "tz.py: workspace_path=%s missing %s; trying CR_WORKSPACE.",
            p, _ENTITIES_REL,
        )
    env_ws = os.environ.get("CR_WORKSPACE", "").strip()
    if env_ws:
        p = Path(env_ws).expanduser().resolve()
        if (p / _ENTITIES_REL).exists():
            return p
        logger.warning(
            "tz.py: CR_WORKSPACE=%s missing %s.",
            p, _ENTITIES_REL,
        )
    return None


def _utc_fallback():
    """Return a usable UTC timezone, surviving environments where ZoneInfo lacks tzdata."""
    try:
        return ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return timezone.utc


def load_workspace_tz(workspace_path: Union[str, Path, None] = None):
    """Read entities.json `workspace.user_timezone`. Return a tzinfo (ZoneInfo).

    Raises `TZResolutionError` if the workspace can't be located or the
    user_timezone isn't set. The tz-database-missing branch (Windows without
    `tzdata` package) is the one case that still falls back to UTC — that's a
    runtime install gap, not a workspace misconfiguration, and surfacing it as
    a warning is the right call.
    """
    root = _resolve_workspace_path(workspace_path)
    if root is None:
        raise TZResolutionError(
            "tz.py: no workspace path supplied. Pass workspace_path=… or set "
            "CR_WORKSPACE in the environment. Walk-up resolution was removed "
            "in v3.11.1 because it never worked from inside the plugin clone."
        )

    entities_path = root / _ENTITIES_REL
    try:
        with entities_path.open("r", encoding="utf-8") as f:
            entities = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise TZResolutionError(
            f"tz.py: failed to read {entities_path} ({exc})."
        ) from exc

    # v2.14.17: tolerate both shapes — newer onboarding writes the workspace block
    # nested under `entities`; older shape had it at top level.
    inner = entities.get("entities") if isinstance(entities.get("entities"), dict) else None
    workspace = (inner or {}).get("workspace") or entities.get("workspace") or {}
    tz_name = workspace.get("user_timezone")
    if not tz_name:
        raise TZResolutionError(
            f"tz.py: {entities_path} has no workspace.user_timezone. Set it via "
            "command-room-onboarding or 'set my timezone to <name>'."
        )

    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "tz.py: tz database missing for %r (likely Windows without `tzdata` package). "
            "Install `pip install tzdata` to enable workspace TZ rendering. Falling back to UTC.",
            tz_name,
        )
        return _utc_fallback()
    except Exception as exc:
        raise TZResolutionError(
            f"tz.py: invalid timezone name {tz_name!r} in entities.json ({exc})."
        ) from exc


def to_local(
    value: Union[str, datetime, None],
    *,
    workspace_path: Union[str, Path, None] = None,
) -> Optional[datetime]:
    """Convert an upstream connector timestamp to the workspace's user_timezone.

    Accepts:
      - ISO 8601 string with offset (Calendar RFC 3339, e.g. "2026-04-29T16:57:00-07:00")
      - ISO 8601 string with `Z` suffix (UTC, e.g. "2026-04-29T23:57:00Z")
      - ISO 8601 naive string (Granola, e.g. "2026-04-29T23:57:00") — ASSUMED UTC
      - RFC 2822 string (Gmail, e.g. "Wed, 29 Apr 2026 23:57:00 +0000")
      - datetime object (aware OR naive — naive ASSUMED UTC)
      - None — passes through

    Returns: aware datetime in workspace TZ, or None if input was None.

    Raises:
      - `TZResolutionError` if no workspace_path resolves (no silent UTC).
      - `ValueError` if the string can't be parsed in any of the supported shapes.

    Callers should surface plain-English errors per Rule 8.
    """
    if value is None:
        return None

    workspace_tz = load_workspace_tz(workspace_path=workspace_path)

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = _parse_string_timestamp(value)
    else:
        raise TypeError(
            f"to_local() expects str, datetime, or None — got {type(value).__name__}"
        )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(workspace_tz)


def _parse_string_timestamp(value: str) -> datetime:
    """Try ISO 8601 first, then RFC 2822. Raise ValueError if both fail."""
    s = value.strip()

    iso_candidate = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        return datetime.fromisoformat(iso_candidate)
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt
    except (TypeError, ValueError):
        pass

    raise ValueError(
        f"to_local(): could not parse timestamp string {value!r} as ISO 8601 or RFC 2822."
    )


def format_local(
    value: Union[str, datetime, None],
    fmt: str = "%Y-%m-%d %H:%M %Z",
    *,
    workspace_path: Union[str, Path, None] = None,
) -> str:
    """Convenience: convert + format in one call. Returns empty string for None input.

    Default format renders like '2026-04-29 16:57 PDT'. Pass a custom fmt string
    if you want something different (e.g. '%a %b %-d, %-I:%M %p' for 'Wed Apr 29, 4:57 PM').

    Raises `TZResolutionError` if the workspace TZ can't be resolved.
    """
    local = to_local(value, workspace_path=workspace_path)
    if local is None:
        return ""
    return local.strftime(fmt)
