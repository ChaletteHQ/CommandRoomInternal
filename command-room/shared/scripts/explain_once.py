#!/usr/bin/env python3
"""The first-week explain-once line (SPEC_SURFACES2 §9, ONBOARD2).

Each surface gets ONE line, ONCE per workspace, the first time it fires
after onboarding — never asked, never repeated. ONBOARD2 owns arming it
(`arm`, called once at the end of onboarding); the consuming surface calls
`consume` and renders whatever it gets back, exactly once. Both sides read
the same two events, through `events_io`, and nothing else:

    explain_once_armed   {surface}          written once, at onboarding
    explain_once_shown    {surface}          written once, at first render

`consume` is idempotent by construction: it returns the line only when
`explain_once_armed` exists for that surface AND `explain_once_shown` does
not yet exist for it, and it writes `explain_once_shown` in that same call
— so a second call (this render, a retry, a re-fired brief) returns None.
An un-armed surface (onboarding never ran, or armed a different surface)
also returns None: nothing to explain, nothing shown.

THE FENCE: `consume`'s "already shown" check. Remove it and the line
renders every time the surface fires, not once — proven by removal in
`tests/run_onboard2_profile_test.py`.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from atomic_write import atomic_append_jsonl  # noqa: E402
from events_io import load_events_owner_scoped  # noqa: E402

EVENTS_SUBPATH = ("_hq", "data", "events.jsonl")

ARMED_EVENT = "explain_once_armed"
SHOWN_EVENT = "explain_once_shown"

# The line, per surface. One sentence, never a question, never repeated —
# this is the day-one promise ("you explain once, and it knows
# permanently") rendered back the first time the surface it describes
# actually fires.
EXPLAIN_ONCE_LINES: Dict[str, str] = {
    "morning-briefing": (
        "One thing before the brief: you explain once, and I keep it — no "
        "more re-explaining scope, priorities, or history."),
}

SURFACES = tuple(EXPLAIN_ONCE_LINES)


def _events_path(workspace_root) -> Path:
    return Path(workspace_root).joinpath(*EVENTS_SUBPATH)


def _rows_for(workspace_root, surface: str, event_type: str) -> list:
    # OWNER-TIER read (matches PROFILE1's own `_sweep()`) — `arm`/`consume`
    # only ever look for this module's own admin markers (never personal
    # content), and the surfaces that call `consume` (morning-briefing) are
    # owner-facing, not an org/external composer — see
    # `events_io.load_events_owner_scoped`'s own docstring for the split.
    out = []
    rows, _skipped = load_events_owner_scoped(workspace_root)
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != event_type:
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if str(data.get("surface") or "") == surface:
            out.append(row)
    return out


def is_armed(workspace_root, surface: str) -> bool:
    return bool(_rows_for(workspace_root, surface, ARMED_EVENT))


def is_shown(workspace_root, surface: str) -> bool:
    return bool(_rows_for(workspace_root, surface, SHOWN_EVENT))


def _append(workspace_root, event_type: str, surface: str,
           extra: Optional[dict] = None) -> None:
    event = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "type": event_type,
        "source_skill": "command-room-onboarding",
        "data": {"surface": surface, **(extra or {})},
    }
    try:
        atomic_append_jsonl(_events_path(workspace_root), event)
    except Exception:
        pass


def arm(workspace_root, surface: str) -> dict:
    """Arm the explain-once line for one surface. Idempotent: arming an
    already-armed (or already-shown) surface writes nothing a second time."""
    if surface not in SURFACES:
        raise ValueError(f"no explain-once line registered for {surface!r} "
                         f"(known: {', '.join(SURFACES)})")
    if is_armed(workspace_root, surface) or is_shown(workspace_root, surface):
        return {"surface": surface, "ran": False}
    _append(workspace_root, ARMED_EVENT, surface)
    return {"surface": surface, "ran": True}


def consume(workspace_root, surface: str) -> Optional[str]:
    """The line, exactly once. None on every call after the first, and on
    any surface never armed."""
    if surface not in SURFACES:
        return None
    if not is_armed(workspace_root, surface):
        return None
    if is_shown(workspace_root, surface):
        return None
    _append(workspace_root, SHOWN_EVENT, surface)
    return EXPLAIN_ONCE_LINES[surface]


__all__ = [
    "EXPLAIN_ONCE_LINES", "SURFACES", "ARMED_EVENT", "SHOWN_EVENT",
    "is_armed", "is_shown", "arm", "consume",
]
