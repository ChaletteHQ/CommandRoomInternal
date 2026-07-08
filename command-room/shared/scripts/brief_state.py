#!/usr/bin/env python3
"""
brief_state.py — COMPAT SHIM (Phase 2 Stage A, 2026-07).

The deterministic commitment-state computer that lived here (v3.14.8+) was
promoted to `shared/scripts/commitment_state.py` — the single projector for
commitment state and the home of the one counting API,
`commitment_counts()` / `count_commitments()` (Build Guide 2026-07-01 §4
Phase 2 Stage A).

This module stays as an import alias forever: every existing caller — tests,
skill prose that says `from brief_state import compute_brief_state`, the
morning brief, the commitments orchestrator — keeps working unchanged (the
same read-old-shapes-forever doctrine that governs event data governs code
entry points). New code imports `commitment_state` directly.

The `brief_state` EVENT TYPE (the Bug #99 audit event) is unchanged — it is
still written by `compute_and_log_brief_state` and read by
`latest_brief_state_event`, both of which now live in commitment_state.py.
"""
from __future__ import annotations

import sys

try:
    from commitment_state import (  # noqa: F401
        KIND_DEFAULT,
        RECENT_ACTIVITY_WINDOW_DAYS,
        RECONCILE_STALE_DAYS,
        commitment_counts,
        commitment_kind,
        compute_and_log_brief_state,
        compute_brief_state,
        count_commitments,
        is_overdue,
        latest_brief_state_event,
        load_open_commitments,
        reconcile_is_stale,
    )
except ImportError:
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from commitment_state import (  # noqa: F401
        KIND_DEFAULT,
        RECENT_ACTIVITY_WINDOW_DAYS,
        RECONCILE_STALE_DAYS,
        commitment_counts,
        commitment_kind,
        compute_and_log_brief_state,
        compute_brief_state,
        count_commitments,
        is_overdue,
        latest_brief_state_event,
        load_open_commitments,
        reconcile_is_stale,
    )

__all__ = [
    "RECENT_ACTIVITY_WINDOW_DAYS",
    "RECONCILE_STALE_DAYS",
    "KIND_DEFAULT",
    "commitment_kind",
    "load_open_commitments",
    "is_overdue",
    "reconcile_is_stale",
    "count_commitments",
    "commitment_counts",
    "compute_brief_state",
    "compute_and_log_brief_state",
    "latest_brief_state_event",
]
