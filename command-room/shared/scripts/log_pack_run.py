#!/usr/bin/env python3
"""
Canonical pack_run telemetry helper (v3.13.8+ — Bug #61; v4.5.2 R1 — now a
thin wrapper over the receipt contract in `receipts.py`).

WHY
---

Pre-v3.13.8, only the Inbox + Don't Forget orchestrators emitted clean
pack_run events. v4.5.2's dogfood (FINDINGS_M_v451 F-10b/F-47/F-49/F-50)
then showed the deeper problem: this helper existed but the prose
orchestrators hand-rolled their receipt JSON instead of calling it, so every
skill drifted its own shape (`cr-` prefixed ids, underscore kinds, `late_tier`
vs `lateness_tier`, `kind`-only vs `task_id`-only payloads). The schema,
writer, and reader now live in `shared/scripts/receipts.py` — ONE contract.

THIS WRAPPER
------------

Kept for import back-compat (existing SKILL.md prose and tests call
`log_pack_run`). Behavior:

  - kind naming a scheduled task (any legacy spelling — `past_meetings`,
    `cr-commitments`, `dont_forget` all normalize) → delegates to
    `receipts.log_receipt`, which writes the canonical shape and validates
    the vocabulary.
  - kind NOT naming a scheduled task (`list`, `follow_up_pack`, ...) →
    writes the same canonical field set as a plain pack_run fire-marker
    (task_id/kind normalized, fired_via canonical, machine stamped) through
    the gated writer. On-demand fire-markers stay possible without
    registering a task.

`fired_via` accepts the legacy "user-trigger" spelling and normalizes it to
"manual" (canonical vocabulary: scheduled | manual | catchup — R2 wires the
detection; the field is schema-final as of R1).

USAGE
=====

    from log_pack_run import log_pack_run
    log_pack_run(
        workspace_root=workspace_root,
        kind="commitments",
        surfaced=7,
        duration_ms=int((time.monotonic() - t0) * 1000),
        source_skill="commitments",
        fired_via="scheduled",
    )
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from receipts import (  # noqa: E402
    CANONICAL_TASK_IDS,
    FIRED_VIA,
    log_receipt,
    normalize_fired_via,
    normalize_task_id,
    _machine_name,
)


def log_pack_run(
    *,
    workspace_root: str | Path,
    kind: str,
    surfaced: int,
    duration_ms: int,
    source_skill: str,
    fired_via: str = "scheduled",
    extra_data: dict | None = None,
) -> dict:
    """Append a canonical pack_run event to events.jsonl.

    Returns the event dict that was appended (for any caller that wants
    to surface telemetry mid-flight).
    """
    if not kind or not isinstance(kind, str):
        raise ValueError(f"pack_run `kind` must be a non-empty string; got {kind!r}")
    if not isinstance(surfaced, int) or surfaced < 0:
        raise ValueError(f"pack_run `surfaced` must be a non-negative int; got {surfaced!r}")
    if not isinstance(duration_ms, int) or duration_ms < 0:
        raise ValueError(f"pack_run `duration_ms` must be a non-negative int; got {duration_ms!r}")

    canonical = normalize_task_id(kind)
    via = normalize_fired_via(fired_via)
    if via not in FIRED_VIA:
        raise ValueError(f"fired_via must normalize to one of {sorted(FIRED_VIA)}; got {fired_via!r}")

    if canonical in CANONICAL_TASK_IDS:
        return log_receipt(
            workspace_root,
            canonical,
            receipt_type="pack_run",
            fired_via=via,
            surfaced=surfaced,
            duration_ms=duration_ms,
            extra_data=extra_data,
        )

    # Non-task fire-marker (show-my-list's `list`, follow_up_pack, ...):
    # same canonical field set, no registry gate.
    data = {
        "task_id": canonical,
        "kind": canonical,
        "status": "complete",
        "fired_via": via,
        "surfaced": surfaced,
        "duration_ms": duration_ms,
    }
    machine = _machine_name()
    if machine:
        data["machine"] = machine
    if extra_data:
        for k, v in extra_data.items():
            if k not in data:
                data[k] = v

    event = {
        "type": "pack_run",
        "source_skill": normalize_task_id(source_skill),
        "data": data,
    }
    from event_gate import append_event

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    append_event(events_path, event, holder=f"receipt:{canonical}")
    return event


__all__ = ["log_pack_run"]
