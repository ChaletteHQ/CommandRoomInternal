#!/usr/bin/env python3
"""Read `_hq/workspace_config.json` for an update without ever wiping it.

THE DEFECT. Every writer of this file read it, fell back to `{}` on ANY read
failure, changed a key or two and atomically wrote the result back. A file
that exists but cannot be read at that moment - half-synced by a cloud drive,
truncated by an interrupted write, held by another process, not valid JSON -
was therefore replaced by a file holding only the writer's own keys:
`registered_taskIds`, `registered_at`, `workspace_basename`, the timezone, the
trigger map and every other field were lost. The watchdog then read every
chat as unregistered.

THE RULE. A file that does not exist reads as `{}` (a first write may create
it). A file that exists and cannot be read as a JSON object raises
`ConfigUnreadable`, and the caller writes nothing. An atomic write keeps a
bad read from corrupting the file; this keeps a bad read from erasing it.

Readers that only look (no write follows) keep their own lenient `{}`
fallback: a missing field on a read is safe, an erased file is not.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_RELPARTS = ("_hq", "workspace_config.json")

#: The reason a writer names when it refused to write over an unreadable file.
UNREADABLE_REASON = "config_unreadable"

#: The one sentence a caller says when a writer refused (cr1#1504). Plain
#: words: the customer never sees the file's name.
CONFIG_UNREADABLE_LINE = (
    "Your schedule record in the Command Room folder could not be read just "
    "now (it may still be syncing), so it was left exactly as it was and "
    "nothing was written over it. Try again in a few minutes.")


class ConfigUnreadable(OSError):
    """`workspace_config.json` exists but could not be read as a JSON object,
    so nothing may be written back over it."""


def config_path(workspace_root) -> Path:
    return Path(workspace_root).joinpath(*CONFIG_RELPARTS)


def read_for_update(path) -> dict:
    """The config as a dict, for a read-modify-write.

    Missing file -> `{}`. Present but unreadable, not valid JSON, or valid
    JSON that is not an object -> `ConfigUnreadable` (never `{}`). A UTF-8
    byte-order mark (Windows PowerShell 5.1 saves one by default) is read
    past, never counted as unreadable (cr1#1504)."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigUnreadable(f"{path}: could not be read ({type(exc).__name__})") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigUnreadable(f"{path}: not valid JSON") from exc
    if not isinstance(data, dict):
        raise ConfigUnreadable(f"{path}: not a JSON object")
    return data


def refused() -> dict:
    """What a writer adds to its answer when it wrote nothing because the
    file could not be read: `{reason, line}`, so every caller says the same
    sentence instead of dropping the refusal."""
    return {"reason": UNREADABLE_REASON, "line": CONFIG_UNREADABLE_LINE}


__all__ = ["CONFIG_RELPARTS", "CONFIG_UNREADABLE_LINE", "ConfigUnreadable",
           "UNREADABLE_REASON", "config_path", "read_for_update", "refused"]
