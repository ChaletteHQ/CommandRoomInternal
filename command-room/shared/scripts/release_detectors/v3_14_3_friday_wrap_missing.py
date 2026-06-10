"""v3.14.3 friday_wrap_missing detector — surfaced 2026-05-26.

Returns applies=True when:
  - The workspace is NOT fresh (at least one `schedule_created` event exists)
  - AND no `schedule_created` event with `data.taskId == "friday-wrap"` exists
  - AND no prior `schedule_created` event for friday-wrap was later disabled (i.e.,
    `friday-wrap` is genuinely absent, not user-removed)

Same release-manifest gap pattern as Bug #72 (brain_name_prompt). A beta
customer on a pre-v3.11.0 install (6 scheduled tasks: morning-brief,
upcoming-meetings, past-meetings, inbox, commitments, pulse, no friday-wrap)
updated to v3.14.2 and silently never got friday-wrap because update-bridge's
Phase 4.7 add-missing logic only covers the pre-M1 → M1 transition (adding
inbox), not the friday-wrap gap.

Wiring (v3.14.4+): consumed by update-bridge Phase 4.7 as a SILENT-add gate —
when applies=True the bridge silently registers friday-wrap (no question), per
CONTRACT.md Rule 28. The v3.14.3 instruct_user manifest item was removed in
v3.14.4 in favor of that silent-add path.

Note (v3.14.5): the old `friday_wrap_declined` decline-suppression branch was
removed. It read an event type that NO writer ever produced (left over from the
pre-v3.14.4 nag design, where the customer was *asked* and could decline).
Deliberate removal of a registered friday-wrap is already protected: the
original `schedule_created` event persists in the append-only log, so
`has_friday_wrap` stays True and this detector returns applies=False — the task
is never force-re-added.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def is_friday_wrap_missing(events_jsonl_path) -> dict:
    """Detector for the v3.14.2.friday_wrap_missing manifest item."""
    events_path = Path(events_jsonl_path)
    if not events_path.exists():
        # Fresh workspace — enable-command-room-schedules registers friday-wrap
        # as part of the M1 first-install set, so no gap to surface.
        return {"applies": False}

    try:
        from cru_match import load_events_defensively  # noqa
    except ImportError:
        # Conservative — don't double-prompt on workspaces we can't fully inspect.
        return {"applies": False}

    events, _skipped = load_events_defensively(events_path)

    has_any_schedule = False
    has_friday_wrap = False

    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_type = ev.get("type")
        if ev_type == "schedule_created":
            has_any_schedule = True
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if data.get("taskId") == "friday-wrap":
                has_friday_wrap = True

    if not has_any_schedule:
        # Fresh-ish workspace that hasn't run enable-command-room-schedules yet.
        # The first-install path will register friday-wrap; nothing for this
        # detector to do.
        return {"applies": False}

    if has_friday_wrap:
        return {"applies": False}

    return {"applies": True, "context": {}}


__all__ = ["is_friday_wrap_missing"]
