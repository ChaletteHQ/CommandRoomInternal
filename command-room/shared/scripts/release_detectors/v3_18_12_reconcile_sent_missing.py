"""v3.18.12 reconcile_sent_missing detector — Bug #98-v3.

Returns applies=True when:
  - The workspace is NOT fresh (at least one `schedule_created` event exists)
  - AND no `schedule_created` event with `data.taskId == "reconcile-sent"` exists

The silent daily `reconcile-sent` task (v3.18.12 — closes commitments the CEO
completed by emailing someone directly) is NOT one of the 7 chat orchestrators —
it registers separately via `enable-command-room-schedules` Step 1.D (the
SILENT_TASKS registry loop, Phase 3 / SPEC-2.3) and is
absent from `ORCHESTRATOR_MAP`. The update-bridge "are scheduled tasks
configured?" check enumerates only the chat taskIds, so it is structurally blind
to it — exactly the gap Bug #82 documented for `cleanup`. An existing customer who
updates would never get the reconcile task without this silent-add gate.

Wiring (v3.18.12+): consumed by command-room-update-bridge Phase 4.7 as a
SILENT-add gate — when applies=True the bridge silently registers `reconcile-sent`
(no question, per CONTRACT.md Rule 28). enable-command-room-schedules Phase 5.9
asserts the same on every direct `set up command room schedules` run.

Deliberate removal is protected the same way friday-wrap/cleanup are: the
original `schedule_created` event persists in the append-only log, so
`has_reconcile_sent` stays True and this detector returns applies=False.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def is_reconcile_sent_missing(events_jsonl_path) -> dict:
    """Detector for the v3.18.12 reconcile_sent_missing gap (Bug #98-v3)."""
    events_path = Path(events_jsonl_path)
    if not events_path.exists():
        return {"applies": False}

    try:
        from cru_match import load_events_defensively  # noqa
    except ImportError:
        return {"applies": False}

    events, _skipped = load_events_defensively(events_path)

    has_any_schedule = False
    has_reconcile_sent = False
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "schedule_created":
            has_any_schedule = True
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if data.get("taskId") == "reconcile-sent":
                has_reconcile_sent = True

    if not has_any_schedule:
        return {"applies": False}
    if has_reconcile_sent:
        return {"applies": False}
    return {"applies": True, "context": {}}


__all__ = ["is_reconcile_sent_missing"]
