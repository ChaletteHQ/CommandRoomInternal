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
import time
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

# SPEC TZFLAKE1 — how many times `ZoneInfo(tz_name)` is constructed before the
# not-found branch is believed, and the pause between attempts. `zoneinfo`
# resolves a key it has never seen by importing `tzdata.zoneinfo.<region>` and
# opening the resource file, and it maps EVERY load failure it recognises
# (ImportError / FileNotFoundError) to ZoneInfoNotFoundError — including
# transient ones a loaded machine can produce while the tz database is
# installed and healthy. Only the FIRST construction in a process is exposed:
# zoneinfo caches per key on success, so one transient there is one silent
# wrong-timezone answer that no later call can correct. A genuinely absent tz
# database fails every attempt identically, so retrying narrows the fallback
# to the case it was written for without changing what it means.
_ZONEINFO_ATTEMPTS = 3
_ZONEINFO_RETRY_SLEEP_S = 0.05


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
    # TZFIX v5.9.4: MERGE the two blocks instead of taking the first TRUTHY one.
    # A live workspace can carry BOTH: the code writers (connector_config.py,
    # contact_capture.py, reconcile_sent_commitments.py) create the INNER block
    # for their own keys (connectors / accounts / user_person_id /
    # sent_reconcile_cursor), while `user_timezone` is only ever written to the
    # TOP-LEVEL block (command-room-onboarding + workspace-manager's "set my
    # timezone"; entities.schema.json pins workspace_settings to top level).
    # The old first-truthy pick let a truthy-but-timezone-less INNER block shadow
    # the real top-level one, so a correctly configured workspace raised
    # TZResolutionError and every to_local()/format_local() caller broke.
    # Top level wins on conflict — it is the block the current writer of
    # user_timezone maintains, so "set my timezone" always takes effect.
    inner = entities.get("entities") if isinstance(entities.get("entities"), dict) else None
    inner_ws = (inner or {}).get("workspace")
    top_ws = entities.get("workspace")
    workspace = {
        **(inner_ws if isinstance(inner_ws, dict) else {}),
        **(top_ws if isinstance(top_ws, dict) else {}),
    }
    tz_name = workspace.get("user_timezone")
    if not tz_name:
        raise TZResolutionError(
            f"tz.py: {entities_path} has no workspace.user_timezone. Set it via "
            "command-room-onboarding or 'set my timezone to <name>'."
        )

    # SPEC TZFLAKE1 (2026-08-29). A ZoneInfoNotFoundError is retried before it
    # is believed — see the constants above for why one transient here is
    # otherwise a silent wrong-timezone answer for the rest of the call. The
    # cost of being wrong is not hypothetical: one first-construction
    # transient under a 12-worker battery sent a correctly configured fixture
    # down the UTC branch, stamped the evening pack's for_date one day
    # forward, and redded run_end_of_day_pack_test 342/343 while the suite
    # alone was green (2026-08-29, the fleet's first FLAKEFIX-class red since
    # honest1). A retry that succeeds still WARNS: a transient that self-heals
    # silently is a transient nobody ever counts.
    for attempt in range(1, _ZONEINFO_ATTEMPTS + 1):
        try:
            zone = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            if attempt < _ZONEINFO_ATTEMPTS:
                time.sleep(_ZONEINFO_RETRY_SLEEP_S)
            continue
        except Exception as exc:
            raise TZResolutionError(
                f"tz.py: invalid timezone name {tz_name!r} in entities.json ({exc})."
            ) from exc
        if attempt > 1:
            logger.warning(
                "tz.py: ZoneInfo(%r) failed transiently and succeeded on "
                "attempt %d — the tz database is installed; the load raced "
                "something on this machine.",
                tz_name, attempt,
            )
        return zone
    logger.warning(
        "tz.py: tz database missing for %r — %d attempts (likely Windows without `tzdata` package). "
        "Install `pip install tzdata` to enable workspace TZ rendering. Falling back to UTC.",
        tz_name, _ZONEINFO_ATTEMPTS,
    )
    return _utc_fallback()


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


def localize_date(
    ts: Union[str, None],
    workspace_path: Union[str, Path, None] = None,
) -> str:
    """Workspace-local DATE (YYYY-MM-DD) for an event/connector timestamp.

    The canonical F-12 helper (TZDATE1 built it inside render_decision_log;
    TZDATE2 hoisted it here and retired the per-renderer copies). Semantics:

      - non-string or empty input → ""
      - date-only input (exactly YYYY-MM-DD) → returned AS-IS — a value that
        never carried a time must not be TZ-shifted backwards
      - with a workspace path → `to_local(...)` then '%Y-%m-%d' (naive input
        read as UTC, per to_local's contract)
      - fallback → `ts[:10]`, the UTC spelling's date portion — taken only
        when localization genuinely cannot resolve (no path, workspace TZ
        unconfigured, unparseable timestamp; `to_local` raises and this
        helper swallows it, because a rendered view beats a traceback)

    Every call site MUST pass `workspace_path`. The TZDATE1 walk finding
    (F-12, P1) was exactly this omission: the decision-log renderer had a
    working helper and not one call site passed the path, so every timestamp
    fell through to the UTC slice and every decision logged after 5 PM
    Pacific rendered a day late — while the same file's regenerated-at
    header was correctly localized, the mixed-clock tell the walk caught.
    """
    if not isinstance(ts, str) or not ts:
        return ""
    # Date-only — keep as is
    if len(ts) == 10 and ts.count("-") == 2:
        return ts
    if workspace_path:
        try:
            local_dt = to_local(ts, workspace_path=workspace_path)
            if local_dt:
                return local_dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    # Fallback: take first 10 chars (ISO date portion)
    return ts[:10]


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
