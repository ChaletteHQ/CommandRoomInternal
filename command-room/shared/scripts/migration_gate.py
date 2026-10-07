#!/usr/bin/env python3
"""The migration gate - an un-migrated chain stops with one sentence and zero
writes on the merged shapes (IDENT1 I-15; M 2026-09-22 23:4x PDT; spec §4c).

WHY THIS EXISTS. Only some scheduled chains have moved onto the workspace
access layer. A chain that has not still carries python bodies that open the
workspace directly; on the merged seat's fire shapes those bodies open nothing
(the container has no workspace) or open it outside the door (no writer
identity), and a fire that improvises around them is how the ledger got
session tokens and a customer got a diagnosis pushed to their phone. So before
a scheduled fire reads its orchestrator, it asks ONE question: is this chain
migrated? Not migrated, on a merged shape -> the chat's one sentence, nothing
written, STOP. Every legacy and local seat runs exactly as today.

THE ONE PARSER. `read_allowance` is the only reader of the census's
"Allowance" section - guard G69 imports it from here - so the guard that
polices the instruction layer and the gate that stops a fire can never
disagree about which files are still censused. `references/ORCH_MIGRATION_
CENSUS.md` is generated from the tree and ships in the runtime (this module
names it, which is what puts it in the manifest).

MIGRATED means: the orchestrator carries the v6 Access preamble AND its
allowance count is zero (absent from the allowance = zero). Where the
orchestrator file cannot be read (the staged runtime never ships `skills/`),
the census alone answers - it is the tree's own generated count, and it is
the half that says whether a python body still opens the workspace.

Every failure answers `stop: False` (the R-FIX3-2 direction: a gate that
cannot read never silences a working chat).

3.10-safe, stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

CENSUS_REL = "references/ORCH_MIGRATION_CENSUS.md"
ALLOWANCE_HEADING = "## Allowance — files that may still open the workspace in python"
_ALLOWANCE_RE = re.compile(r"^-\s+`([^`]+)`\s+—\s+(\d+)\s+block", re.M)
PREAMBLE_MARKER = "# >>> CR ACCESS PREAMBLE v6"
MERGED_MODE = "merged_cloud"

#: The one sentence an un-migrated chain says on a merged shape. No tool, no
#: file, no mode word - the chat's own name and what will change it.
NOT_MIGRATED_LINE = ("{name} can't run from the cloud on this computer yet, so "
                     "it did nothing today; it will once Command Room's next "
                     "update reaches it.")


def _plugin_root(plugin_root) -> Path:
    return Path(plugin_root) if plugin_root else _HERE.parent.parent


def read_allowance(plugin_root=None) -> Dict[str, int]:
    """`{rel: count}` from the census's allowance section, or `{}` when the
    document or its heading is missing (G69 reads that as its own red)."""
    path = _plugin_root(plugin_root) / CENSUS_REL
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if ALLOWANCE_HEADING not in text:
        return {}
    tail = text.split(ALLOWANCE_HEADING, 1)[1]
    return {rel: int(count) for rel, count in _ALLOWANCE_RE.findall(tail)}


def is_migrated(plugin_root, orchestrator_rel: str) -> bool:
    """True when the chain has moved onto the access layer (see the module
    docstring for the one rule)."""
    rel = str(orchestrator_rel or "").replace("\\", "/").strip()
    if not rel:
        return False
    root = _plugin_root(plugin_root)
    allowance = read_allowance(root)
    if not allowance and not (root / CENSUS_REL).is_file():
        return True  # no census to read: never a stop (R-FIX3-2)
    if allowance.get(rel, 0) != 0:
        return False
    target = root / rel
    if target.is_file():
        try:
            return PREAMBLE_MARKER in target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return True
    return True


def _display_name(task_id: Optional[str], orchestrator_rel: str) -> str:
    tid = str(task_id or "").strip()
    if not tid:
        stem = Path(str(orchestrator_rel)).stem
        tid = stem[len("orchestrator-"):] if stem.startswith("orchestrator-") else stem
    try:
        import schedule_config

        return schedule_config.task_display_name(tid)
    except Exception:  # noqa: BLE001
        return tid.replace("-", " ").title()


def gate(orchestrator_rel: str, env_mode: str = "", device_tools: bool = False,
         task_id: Optional[str] = None, plugin_root=None) -> Dict[str, Any]:
    """`{migrated, stop, line}` - `stop` only when NOT migrated AND the run is
    on a merged shape (`env_mode == "merged_cloud"`: the device and the mount
    branches). A legacy or local seat never stops here."""
    out: Dict[str, Any] = {"migrated": True, "stop": False, "line": ""}
    try:
        migrated = is_migrated(plugin_root, orchestrator_rel)
        out["migrated"] = migrated
        if migrated or str(env_mode or "").strip() != MERGED_MODE:
            return out
        line = NOT_MIGRATED_LINE.format(name=_display_name(task_id, orchestrator_rel))
        try:
            from chat_output_validator import validate_chat_output

            if not validate_chat_output(line).ok:
                return out
        except ImportError:
            pass
        out.update({"stop": True, "line": line, "device_tools": bool(device_tools)})
    except Exception:  # noqa: BLE001 - a gate that cannot read never stops a fire
        return {"migrated": True, "stop": False, "line": ""}
    return out


__all__ = ["ALLOWANCE_HEADING", "CENSUS_REL", "NOT_MIGRATED_LINE", "gate",
           "is_migrated", "read_allowance"]
