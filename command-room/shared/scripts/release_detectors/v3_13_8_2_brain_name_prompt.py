"""v3.13.8.2 brain_name_prompt detector — Bug #72.

Returns applies=True when:
  - entities.json workspace section lacks `brain_name`
  - AND no prior `brain_name_captured` event exists in events.jsonl
  - AND no prior `brain_name_declined` event exists in events.jsonl

Both decline events and capture events are idempotency-strong — once the user
either named their AI OR explicitly skipped, the bridge stops re-prompting.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def needs_brain_name_prompt(events_jsonl_path) -> dict:
    """Detector for the v3.13.8.2.brain_name_prompt manifest item.

    The detector accepts the events.jsonl path (per the standard detector
    signature used by the bridge) and derives the entities.json path from it.
    """
    events_path = Path(events_jsonl_path)
    if not events_path.exists():
        # Fresh workspace — onboarding will handle brain_name via the M1
        # widget Q4 path; the bridge doesn't need to prompt.
        return {"applies": False}

    # Check entities.json for an existing brain_name first (cheap path).
    entities_path = events_path.parent / "entities.json"
    if entities_path.exists():
        try:
            entities = json.loads(entities_path.read_text(encoding="utf-8"))
            workspace = entities.get("workspace") if isinstance(entities, dict) else None
            if isinstance(workspace, dict):
                brain_name = workspace.get("brain_name")
                if brain_name and isinstance(brain_name, str) and brain_name.strip():
                    return {"applies": False}
        except (json.JSONDecodeError, OSError):
            # entities.json malformed or unreadable — don't block; treat as
            # if no brain_name set and let the events.jsonl check decide.
            pass

    # Check events.jsonl for captured or declined events.
    try:
        from cru_match import load_events_defensively  # noqa
    except ImportError:
        # During the transitional window the canonical reader may not be
        # importable yet. Be conservative — return False so we don't double-
        # prompt on workspaces we can't fully inspect.
        return {"applies": False}

    events, _skipped = load_events_defensively(events_path)
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_type = ev.get("type")
        if ev_type == "brain_name_captured":
            return {"applies": False}
        if ev_type == "brain_name_declined":
            return {"applies": False}

    return {
        "applies": True,
        "context": {
            "default_name": "Penelope",
        },
    }


__all__ = ["needs_brain_name_prompt"]
