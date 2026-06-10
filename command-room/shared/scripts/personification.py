"""Customer-facing brain_name reader (v3.13.8.4).

Single canonical reader for `workspace.brain_name`. Every customer-facing
render path that wants to surface the AI's name calls this helper rather than
re-implementing the entities.json read inline.

Falls back to "Penelope" if entities.json is missing, malformed, or has no
brain_name set. Customer-facing copy must never fail to render because of a
missing field — the default is always safe to print.

Read at render time. Never cache across workspaces. Cheap enough to call once
per turn without optimization.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

DEFAULT_BRAIN_NAME = "Penelope"

# Vocative-addressing trigger shapes. The brain-name routing gate in
# workspace-manager uses this to detect when the user is addressing the AI
# by name rather than describing it. Keep in sync with the gate's spec.
_VOCATIVE_SEPARATORS = (",", "—", "-", ":", "?", "!", " ")


def get_brain_name(workspace_root) -> str:
    """Return workspace.brain_name from entities.json, or "Penelope" if unset.

    Args:
        workspace_root: Path-like pointing at the workspace root (the parent
            of `_hq/`). Both `str` and `pathlib.Path` accepted.

    Returns:
        The configured brain_name, or "Penelope" as the safe default.
    """
    entities_path = Path(workspace_root) / "_hq" / "data" / "entities.json"
    if not entities_path.exists():
        return DEFAULT_BRAIN_NAME
    try:
        entities = json.loads(entities_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DEFAULT_BRAIN_NAME
    if not isinstance(entities, dict):
        return DEFAULT_BRAIN_NAME
    workspace = entities.get("workspace")
    if not isinstance(workspace, dict):
        return DEFAULT_BRAIN_NAME
    name = workspace.get("brain_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return DEFAULT_BRAIN_NAME


def detect_vocative_address(message: str, brain_name: str) -> tuple[bool, str]:
    """Detect whether `message` opens by addressing the AI by name.

    Used by the workspace-manager brain-name routing gate. Returns
    `(matched, stripped_message)`. When matched, the stripped message is the
    remainder of the user's turn with the vocative prefix removed — pass that
    back through normal trigger matching to route to the right specialist.

    Match shapes (case-insensitive, first-token only):
      - "Penelope, what's overdue?"        → ("Penelope", remainder)
      - "Penelope — prep me for my 2pm"    → ("Penelope", remainder)
      - "Hey Penelope, draft an email..."  → ("Penelope", remainder)
      - "Penelope?"                        → ("Penelope", "") — bare wake call
      - "Penelope what's going on"         → ("Penelope", remainder)

    Non-match shapes (the name appears but not as direct address):
      - "Did Penelope send the brief?"    → name is referenced, not addressed
      - "Tell me what Penelope thinks"     → indirect reference

    The gate is intentionally conservative: only the FIRST token (or
    "hey/hi/hello [name]") counts. Mentions later in the message route
    through normal trigger matching, not the wake-word path.
    """
    if not message or not brain_name:
        return False, message
    stripped = message.lstrip()
    if not stripped:
        return False, message
    name_lower = brain_name.lower()
    lowered = stripped.lower()

    # Strip optional greeting prefix.
    for greeting in ("hey ", "hi ", "hello "):
        if lowered.startswith(greeting):
            stripped = stripped[len(greeting):].lstrip()
            lowered = stripped.lower()
            break

    if not lowered.startswith(name_lower):
        return False, message

    after_name = stripped[len(brain_name):]
    if not after_name:
        # Bare "Penelope" — treat as a wake call with empty remainder.
        return True, ""
    first_char = after_name[0]
    if first_char not in _VOCATIVE_SEPARATORS:
        # Name is a prefix of another word ("Penelopes", "Penelopina") —
        # not addressing.
        return False, message

    # Strip the separator + any following whitespace/dash chars.
    remainder = after_name.lstrip("".join(_VOCATIVE_SEPARATORS))
    return True, remainder


def brain_name_trigger_aliases(brain_name: str) -> Iterable[str]:
    """Render the vocative trigger phrases for a given brain_name.

    Used by docs/test fixtures that want to enumerate the addressing shapes
    the gate recognizes. Keep in sync with `detect_vocative_address`.
    """
    return (
        f"{brain_name}",
        f"{brain_name},",
        f"{brain_name} —",
        f"{brain_name}?",
        f"Hey {brain_name}",
        f"Hi {brain_name}",
    )


__all__ = [
    "DEFAULT_BRAIN_NAME",
    "get_brain_name",
    "detect_vocative_address",
    "brain_name_trigger_aliases",
]
