#!/usr/bin/env python3
"""
SPEC SYNC1 B1 — the `_hq/data` location resolver (ships DORMANT per D-1).

WHY DORMANT
-----------
The handoff's R1 wanted `_hq/data` relocated OUT of the Drive mirror to end the
multi-machine clobber class. Grounding found the conflict that makes that a
separate, M-gated migration rather than part of this build: scheduled fires run
in Cowork sandboxes that reach the substrate ONLY via the workspace mount (the
row-17 marker's own quarantine_path is `/sessions/.../mnt/Penelopes Brain/_hq/
data/...`). Moving `_hq/data` to a non-synced local path orphans every Cowork
and scheduled fire — they'd see an empty substrate. So THIS build ships the
resolver + a migration script, but does NOT migrate M's workspace: the default
path is unchanged, back-compat is a hard requirement, and every existing
workspace resolves byte-identically to today.

RESOLUTION ORDER
----------------
  1. env `CR_DATA_ROOT` (the real relocation mechanism; needs no substrate read
     and breaks the chicken-and-egg below).
  2. `workspace.data_root` in entities.json — READ FROM THE DEFAULT PATH. The
     pointer that says where data lives must itself live at a known location, so
     the pointer is always read from `<root>/_hq/data/entities.json`; only the
     OTHER data files follow it. (Dormant today: the key is absent, so this
     never fires.)
  3. default `<root>/_hq/data` — unchanged forever for un-migrated workspaces.

Every script-side seam that constructs `_hq/data` should route through
`resolve()` so a future migration flips one env var, not fifty files. The
migration itself is `scripts/migrate_data_root.py` (ships, not run).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_ENV_VAR = "CR_DATA_ROOT"


def _default(root: Path) -> Path:
    return root / "_hq" / "data"


def _read_pointer(entities_path: Path) -> Optional[str]:
    """`workspace.data_root` from entities.json, or None. Defensive: a missing
    or unparseable entities.json is 'no pointer', never an error."""
    try:
        data = json.loads(entities_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ws = data.get("workspace")
    if not isinstance(ws, dict):
        return None
    dr = ws.get("data_root")
    if isinstance(dr, str) and dr.strip():
        return dr.strip()
    return None


def resolve(workspace_root) -> Path:
    """The `_hq/data` directory for `workspace_root`, honoring (in order) the
    CR_DATA_ROOT env var, the entities.json `workspace.data_root` pointer read
    from the default path, then the default. With no override this returns
    `<workspace_root>/_hq/data` — byte-identical to the pre-SYNC1 constant, so
    every existing seam behaves exactly as before (back-compat, D-5)."""
    env = os.environ.get(_ENV_VAR)
    if isinstance(env, str) and env.strip():
        return Path(env.strip())
    root = Path(workspace_root)
    default = _default(root)
    pointer = _read_pointer(default / "entities.json")
    if pointer:
        return Path(pointer)
    return default


def is_overridden() -> bool:
    """True when the env override is set — used by the migration script / tests
    and by diagnostics that want to note a relocated substrate."""
    env = os.environ.get(_ENV_VAR)
    return isinstance(env, str) and bool(env.strip())


__all__ = ["resolve", "is_overridden"]
