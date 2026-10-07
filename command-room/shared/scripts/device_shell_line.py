"""device_shell_line — the ONE sentence for a merged session that has no device shell (findings fix 6, BRG-02; PARALLEL-B lane B, 2026-09-24).

Some PC-linked sessions arrive without `mcp__remote-devices__device_bash`
(only the file stage/commit tools) — measured, cause unknown (BRG-02, C3).
The access layer then answers `no_workspace_on_this_host` and a skill that
presses on improvises. This module answers the question UP FRONT, from the
tool list the session was given, before any skill reads: None when the shell
is there (under its display name or a UUID-named server), else one validated
sentence the skill posts as its whole turn.

`workspace_access.py` is fenced tonight (BRIEFDOOR1), so the caller is a seam:
the access preamble's discovery line should consult this before `discover`
(record, Seams).

3.10-safe.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

__all__ = ["missing_device_shell_line", "device_shell_present", "DEVICE_SHELL_TOOL",
           "MISSING_DEVICE_SHELL_LINE"]

#: The display-named form the merged app gives the device shell.
DEVICE_SHELL_TOOL = "mcp__remote-devices__device_bash"

#: The one sentence. No tool name, no path, no session id, no mechanism; it
#: names what is true and the two things the customer can do about it.
MISSING_DEVICE_SHELL_LINE = (
    "This chat cannot reach your folder from here; open it on the computer "
    "your folder is linked to, or link this computer."
)

#: A device_bash tool named by a UUID-shaped server segment (CON-05: the
#: platform sometimes names connectors by UUID) still counts as present.
_DEVICE_BASH_RE = re.compile(r"^mcp__[A-Za-z0-9_-]+__device_bash$")


def _names(tools: Optional[Iterable]) -> set:
    out = set()
    for t in tools or []:
        if isinstance(t, str):
            out.add(t.strip())
        elif isinstance(t, dict):
            out.add(str(t.get("name") or t.get("tool_id") or "").strip())
    return {n for n in out if n}


def device_shell_present(tools: Optional[Iterable]) -> bool:
    """True when the session's tool list carries a device shell — the display
    name, or any `mcp__<server>__device_bash` spelling (a UUID-named server
    included)."""
    names = _names(tools)
    if DEVICE_SHELL_TOOL in names:
        return True
    return any(_DEVICE_BASH_RE.match(n) for n in names)


def missing_device_shell_line(tools: Optional[Iterable]) -> Optional[str]:
    """None when the device shell is present; else the one sentence, passed
    through `chat_output_validator.validate_chat_output` (a line that fails
    the gate raises — the sentence is a constant, so that is a build error,
    never a customer-facing one)."""
    if device_shell_present(tools):
        return None
    line = MISSING_DEVICE_SHELL_LINE
    try:
        from chat_output_validator import validate_chat_output
    except ImportError:  # a copy staged without the validator still answers
        return line
    result = validate_chat_output(line)
    if not getattr(result, "ok", True):
        raise RuntimeError("the missing-device-shell line does not pass the gate: "
                           + "; ".join(str(v) for v in getattr(result, "violations", [])))
    return line
