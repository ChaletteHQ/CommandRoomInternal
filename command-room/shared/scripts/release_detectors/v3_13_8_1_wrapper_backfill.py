"""v3.13.8.1 wrapper-source-seq-backfill detector — Bug #71.

Returns applies=True when the workspace has at least one legacy
commitment_to_discuss wrapper missing data.source_event_seq AND no prior
wrapper_source_seq_backfill event with v3.13.8.1 recovery_version exists.

The update-bridge surfaces a prompt explaining the backfill; the actual
migration runs via source_event_seq_backfill.run_backfill_if_needed().
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def has_legacy_wrappers(events_jsonl_path) -> dict:
    """Detector for the v3.13.8.1.wrapper_source_seq_backfill manifest item."""
    path = Path(events_jsonl_path)
    if not path.exists():
        return {"applies": False}

    try:
        from cru_match import load_events_defensively  # noqa
        from source_event_seq_backfill import RECOVERY_VERSION  # noqa
    except ImportError:
        # During the transitional window the new module may not yet be in
        # the runtime sys.path. Detector returns False so the prompt doesn't
        # fire before the new code lands.
        return {"applies": False}

    events, _skipped = load_events_defensively(path)

    # Idempotency: check for prior backfill with our version
    for ev in events:
        if ev.get("type") == "wrapper_source_seq_backfill":
            data = ev.get("data") or {}
            if data.get("recovery_version") == RECOVERY_VERSION:
                return {"applies": False}

    # Count legacy wrappers (commitment_to_discuss without data.source_event_seq
    # and not already marked needs_review by a prior partial attempt).
    legacy_count = 0
    for ev in events:
        if ev.get("type") != "commitment_to_discuss":
            continue
        data = ev.get("data") or {}
        if data.get("source_event_seq") is not None:
            continue
        if data.get("source_event_seq_match") == "needs_review":
            continue
        legacy_count += 1

    if legacy_count == 0:
        return {"applies": False}

    return {
        "applies": True,
        "context": {
            "legacy_wrapper_count": legacy_count,
        },
    }


__all__ = ["has_legacy_wrappers"]
