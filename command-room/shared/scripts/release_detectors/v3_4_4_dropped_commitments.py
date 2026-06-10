#!/usr/bin/env python3
"""
Detector for v3.4.4 — extends commitment-shape coverage from 2 variants to 5.

Returns the count of OPEN commitment events in the user's events.jsonl that
were in non-canonical shapes pre-v3.4.4 — these were silently dropped from the
daily cr-commitments fire because the filter only handled the canonical and
flat-new shapes. After v3.4.4, they surface.

If the count is non-zero, the update-bridge surfaces a prompt offering to
re-fire the Commitments task so the user sees the recovered items without
waiting for the next scheduled fire.

Detector is read-only. No mutation. Idempotent across re-runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

# cru_match.py is the canonical reader for commitment events. Reuse its
# load_open_commitments + _commitment_field helpers so this detector and the
# orchestrator-commitments filter classify shapes the same way.
_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cru_match import load_open_commitments  # noqa: E402


def _shape(ev: dict) -> str:
    """Classify a commitment event by shape. Same taxonomy as the v3.4.4
    workspace audit + orchestrator-commitments Phase 3 spec.
    """
    d = ev.get("data") or {}
    if "owner_id" in d:
        return "canonical"
    if "owner_person_id" in d:
        return "owner_person_id-variant"
    if "owner_name_proposed" in d:
        return "pending-review"
    if "owner_id" in ev:
        return "flat-new"
    if "owner" in ev:
        return "legacy"
    return "other"


def count_dropped_open_commitments(events_jsonl_path: str | Path) -> dict:
    """Return a `{applies, context}` dict for the update-bridge skill.

    `applies` is True when the user has at least one OPEN commitment in a
    non-canonical, non-pending-review shape. `context` carries `count` (total
    dropped open commitments) plus a `by_shape` breakdown for the prompt
    template if it wants to be more specific.
    """
    opens = load_open_commitments(events_jsonl_path)
    by_shape: dict[str, int] = {}
    for ev in opens:
        sh = _shape(ev)
        if sh in ("canonical", "pending-review"):
            continue
        by_shape[sh] = by_shape.get(sh, 0) + 1
    total = sum(by_shape.values())
    return {
        "applies": total > 0,
        "context": {
            "count": total,
            "by_shape": by_shape,
        },
    }


if __name__ == "__main__":
    # CLI invocation: pass events.jsonl path as arg, prints the detector result
    # as a single line of JSON. Used by the update-bridge skill via bash.
    import json
    path = sys.argv[1] if len(sys.argv) > 1 else "_hq/data/events.jsonl"
    print(json.dumps(count_dropped_open_commitments(path)))
