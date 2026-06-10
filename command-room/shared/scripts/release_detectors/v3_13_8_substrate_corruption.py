"""v3.13.8 substrate-corruption detector — Sub-bug #14b Layer 1.

Returns applies=True when the workspace's events.jsonl has malformed lines
(parseable JSON fails OR top-level non-dict) AND no prior corruption_recovery
event with the v3.13.8.1 recovery_version exists. The update-bridge surfaces
this with a friendly prompt explaining the recovery; the actual recovery runs
via the chained `recover_corruption.run_recovery_if_needed()` call.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add the scripts dir to sys.path so we can import the canonical helpers.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def has_malformed_events(events_jsonl_path) -> dict:
    """Detector for the v3.13.8.substrate_corruption_recovery manifest item."""
    path = Path(events_jsonl_path)
    if not path.exists():
        return {"applies": False}

    try:
        from cru_match import load_events_defensively  # noqa
        from recover_corruption import RECOVERY_VERSION  # noqa
    except ImportError:
        # During the transitional window (workspace updating from a prior
        # version), these modules may not yet be installed. The detector
        # returns False so the prompt doesn't fire before the new code lands.
        return {"applies": False}

    events, skipped = load_events_defensively(path)

    # Check for prior recovery with our version
    for ev in events:
        if ev.get("type") == "corruption_recovery":
            data = ev.get("data") or {}
            if data.get("recovery_version") == RECOVERY_VERSION:
                # Recovery already ran on this workspace; do not re-prompt.
                return {"applies": False}

    if not skipped:
        return {"applies": False}

    return {
        "applies": True,
        "context": {
            "count": len(skipped),
            "lines": [s["line"] for s in skipped[:10]],
        },
    }


__all__ = ["has_malformed_events"]
