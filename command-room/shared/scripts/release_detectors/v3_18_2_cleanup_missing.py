"""v3.18.2 cleanup_missing detector — surfaced 2026-05-31 (Bug #82).

Returns applies=True when:
  - The workspace is NOT fresh (at least one `schedule_created` event exists)
  - AND no `schedule_created` event with `data.taskId == "cleanup"` exists

The `cleanup` Sunday self-maintenance task (v3.17.0 headline) is NOT one of the
7 chat orchestrators — it is registered separately via
`enable-command-room-schedules` Step 1.D and is intentionally absent from
`ORCHESTRATOR_MAP`. Both the update-bridge "are scheduled tasks configured?"
check and the enable-schedules idempotency check enumerate only the 7 chat
taskIds, so they are structurally blind to `cleanup`: an existing customer who
updates (clients ~v3.14.4) sees "all chats already registered" and never gets
the Sunday cleanup registered. Same release-manifest gap pattern as Bug #72
(brain_name_prompt) and the v3.14.3 friday_wrap gap.

Wiring (v3.18.2+): consumed by command-room-update-bridge Phase 4.7 as a
SILENT-add gate — when applies=True the bridge silently registers `cleanup`
(no question), per CONTRACT.md Rule 28. enable-command-room-schedules Phase 5.9
asserts the same thing on every direct `set up command room schedules` run.

Deliberate removal of a registered cleanup task is protected the same way
friday-wrap is: the original `schedule_created` event persists in the
append-only log, so `has_cleanup` stays True and this detector returns
applies=False — the task is never force-re-added.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def is_cleanup_missing(events_jsonl_path) -> dict:
    """Detector for the v3.18.2 cleanup_missing gap (Bug #82)."""
    events_path = Path(events_jsonl_path)
    if not events_path.exists():
        # Fresh workspace — enable-command-room-schedules registers cleanup as
        # part of the M1 first-install set (Step 1.D), so no gap to surface.
        return {"applies": False}

    try:
        from cru_match import load_events_defensively  # noqa
    except ImportError:
        # Conservative — don't double-prompt on workspaces we can't fully inspect.
        return {"applies": False}

    events, _skipped = load_events_defensively(events_path)

    has_any_schedule = False
    has_cleanup = False

    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "schedule_created":
            has_any_schedule = True
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if data.get("taskId") == "cleanup":
                has_cleanup = True

    if not has_any_schedule:
        # Fresh-ish workspace that hasn't run enable-command-room-schedules yet.
        # The first-install path (Step 1.D) will register cleanup; nothing to do.
        return {"applies": False}

    if has_cleanup:
        return {"applies": False}

    return {"applies": True, "context": {}}


__all__ = ["is_cleanup_missing"]
