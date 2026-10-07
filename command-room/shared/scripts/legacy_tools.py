#!/usr/bin/env python3
"""Legacy tool-name shim — the ONE legal home of the old Cowork tool strings.

Night M1, GUARD1 (spec `SPEC_NIGHTM1_LANES_2026-09-19.md` §5 item 1; gap
analysis §7.2.2 exemption 1; environment breakdown §6.2).

WHY THIS FILE EXISTS
--------------------
The merged claude.ai + Cowork app no longer exposes the tools Command Room was
written against: `mcp__scheduled-tasks__*`, `mcp__cowork__*`,
`mcp__workspace__*`, `mcp__writeback__*`, the sidebar's `window.cowork`
runtime, and lowercase connector prefixes such as `mcp__granola__`. Un-merged
seats (the v5.29.0 desktop Cowork fleet) still expose them, so the strings
cannot simply be deleted: something has to RECOGNISE an old id when it appears
in a legacy tool list and say which seam now owns that behaviour.

That is this module, and only this module. Guard G67
(`tests/run_guard_g67_legacy_tool_names_test.py`) fails the battery on any
legacy tool string anywhere under `skills/`, `shared/` or `references/` except
this file, `references/HISTORY.md` and a reviewed `.md` waiver. Prose names the
SEAM (`schedule_backend.plan_list`, `workspace_access.run_helper`,
`legacy_tools.is_legacy`), never a dead tool id.

WHAT IT IS NOT
--------------
Not a dispatcher: it calls nothing, imports nothing from the seams it names,
and has no side effects. It answers two questions about a string.

3.10-safe on purpose — it may run in the sandbox VM (Python 3.10.12).
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# Seam modules named by the map. Strings, never imports: this module must stay
# importable in the sandbox VM where most of the plugin is absent.
SEAM_SCHEDULE = "schedule_backend"
SEAM_ACCESS = "workspace_access"
SEAM_DELIVERABLES = "deliverables"
SEAM_DISCOVERY = "tool_discovery"
SEAM_RETIRED = "retired"

# old tool id (or prefix) -> (seam module, seam operation).
#
# Keys are matched EXACTLY first, then by longest prefix, so both a full id
# (`mcp__scheduled-tasks__list_scheduled_tasks`) and a bare family prefix
# (`mcp__cowork__`) resolve. Every literal Guard G67 scans for appears here,
# and G67 pins that correspondence in BOTH directions.
LEGACY_TOOL_MAP: Dict[str, Tuple[str, str]] = {
    # --- Row 4: scheduling (breakdown §6.2, §7) -------------------------------
    # The merged app schedules through `mcp__claude-code-remote__*_trigger`
    # with UTC cron; `schedule_backend` plans, normalises and never calls.
    "mcp__scheduled-tasks__list_scheduled_tasks": (SEAM_SCHEDULE, "plan_list"),
    "mcp__scheduled-tasks__create_scheduled_task": (SEAM_SCHEDULE, "plan_create"),
    "mcp__scheduled-tasks__update_scheduled_task": (SEAM_SCHEDULE, "plan_update"),
    "mcp__scheduled-tasks__delete_scheduled_task": (SEAM_SCHEDULE, "plan_delete"),
    "mcp__scheduled-tasks__run_scheduled_task": (SEAM_SCHEDULE, "plan_fire"),
    "mcp__scheduled-tasks__list_task_runs": (SEAM_SCHEDULE, "plan_list_runs"),
    "mcp__scheduled-tasks__": (SEAM_SCHEDULE, "plan_list"),
    # --- Row 1 / row 7: the workspace and file delivery -----------------------
    "mcp__cowork__request_cowork_directory": (SEAM_ACCESS, "folder_request"),
    "mcp__cowork__present_files": (SEAM_DELIVERABLES, "present"),
    "mcp__cowork__create_artifact": (SEAM_RETIRED, "sidebar_artifact"),
    "mcp__cowork__update_artifact": (SEAM_RETIRED, "sidebar_artifact"),
    "mcp__cowork__list_artifacts": (SEAM_RETIRED, "sidebar_artifact"),
    "mcp__cowork__delete_artifact": (SEAM_RETIRED, "sidebar_artifact"),
    "mcp__cowork__": (SEAM_ACCESS, "folder_request"),
    "mcp__workspace__bash": (SEAM_ACCESS, "run_helper"),
    "mcp__workspace__workspace_info": (SEAM_ACCESS, "discover"),
    "mcp__workspace__": (SEAM_ACCESS, "run_helper"),
    "mcp__writeback__propose_update": (SEAM_ACCESS, "write"),
    "mcp__writeback__": (SEAM_ACCESS, "write"),
    # --- Row 8: the sidebar runtime (no successor; RETIRE1 removes the files) -
    "window.cowork": (SEAM_RETIRED, "sidebar_runtime"),
    "callMcpTool": (SEAM_RETIRED, "sidebar_call"),
    "runScheduledTask": (SEAM_RETIRED, "sidebar_run_scheduled"),
    # --- Row 3: connector prefixes are display names now ----------------------
    # Lowercase `mcp__granola__` was the old id; the live name is
    # `mcp__Granola__` (display name, spaces -> underscores, case preserved).
    "mcp__granola__": (SEAM_DISCOVERY, "display_name_prefix"),
}

# Longest first, so `mcp__cowork__present_files` never resolves through the
# bare `mcp__cowork__` family row.
_KEYS_BY_LENGTH = tuple(sorted(LEGACY_TOOL_MAP, key=len, reverse=True))


def translate(tool_id: str) -> Optional[Tuple[str, str]]:
    """Return `(seam_module, seam_op)` for a legacy tool id, else `None`.

    Exact match wins; otherwise the longest matching prefix. Case-sensitive on
    purpose: `mcp__Granola__list_meetings` is a LIVE name and must not
    translate, while lowercase `mcp__granola__*` is the dead one.
    """
    if not tool_id or not isinstance(tool_id, str):
        return None
    hit = LEGACY_TOOL_MAP.get(tool_id)
    if hit is not None:
        return hit
    for key in _KEYS_BY_LENGTH:
        if tool_id.startswith(key):
            return LEGACY_TOOL_MAP[key]
    return None


def is_legacy(tool_id: str) -> bool:
    """True when `tool_id` names a tool that only un-merged seats still have."""
    return translate(tool_id) is not None


def seam_for(tool_id: str) -> Optional[str]:
    """`"schedule_backend.plan_list"`-style seam name, else `None`.

    The sentence a surface should print names this, never the tool id.
    """
    hit = translate(tool_id)
    if hit is None:
        return None
    return "%s.%s" % hit


def legacy_ids_in(tool_names) -> list:
    """Every name in `tool_names` that is a legacy id, in the order given.

    `tool_names` may be strings or objects carrying a `tool_id`/`name`
    attribute (the shape `tool_discovery` already accepts).
    """
    found = []
    for item in tool_names or ():
        name = item
        if not isinstance(name, str):
            name = getattr(item, "tool_id", None) or getattr(item, "name", None)
        if isinstance(name, str) and is_legacy(name):
            found.append(name)
    return found
