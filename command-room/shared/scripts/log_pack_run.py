#!/usr/bin/env python3
"""
Canonical pack_run telemetry helper (v3.13.8+ — Bug #61).

WHY
---

Pre-v3.13.8, only the Inbox + Don't Forget orchestrators emitted clean
pack_run events. Past Meetings, Upcoming Meetings, Commitments, Pulse,
and Friday Wrap either omitted the event or emitted it with `kind: null`
and missing duration_ms — making ~60% of scheduled-task fires
unmeasurable in usage-report and weekly-audit.

THIS HELPER
-----------

A single, canonical emission point. Every orchestrator's final phase
calls `log_pack_run(...)` with:

  - kind: non-null; one of {inbox, commitments, past_meetings,
                            upcoming_meetings, pulse, friday_wrap,
                            list, follow_up_pack, dont_forget, ...}
  - surfaced: integer count of items rendered (0 for empty-state)
  - duration_ms: integer milliseconds from orchestrator start
  - source_skill: name of the orchestrator
  - fired_via: "scheduled" | "user-trigger"

USAGE
=====

    from log_pack_run import log_pack_run
    log_pack_run(
        workspace_root=workspace_root,
        kind="commitments",
        surfaced=7,
        duration_ms=int((time.monotonic() - t0) * 1000),
        source_skill="cr-commitments",
        fired_via="scheduled",
    )
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from atomic_write import atomic_append_jsonl  # noqa: E402
from next_seq import next_seq  # noqa: E402


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

    workspace_root = Path(workspace_root)
    events_path = workspace_root / "_hq" / "data" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)

    seq = next_seq(events_path)
    data = {
        "kind": kind,
        "surfaced": surfaced,
        "duration_ms": duration_ms,
        "fired_via": fired_via,
    }
    if extra_data:
        data.update(extra_data)

    event = {
        "seq": seq,
        "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "type": "pack_run",
        "source_skill": source_skill,
        "data": data,
    }
    atomic_append_jsonl(events_path, [event])
    return event


__all__ = ["log_pack_run"]
