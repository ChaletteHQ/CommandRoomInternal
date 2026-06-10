#!/usr/bin/env python3
"""Stall detector — canonical helper for surfacing stalled threads.

A "thread" is the canonical CR container for a workstream (formerly called a
"project" — ids are still `project_NNN` for schema stability but the
user-facing language is "thread"). See `references/ORG_AND_THREAD_MODEL.md`.

A thread is "stalled" when no substrate activity (meeting, commitment,
decision, or interaction) has touched it within a configurable per-status
threshold:

  active     → 14 days (default)
  exploring  → 30 days (default)
  paused     → 45 days (default)
  blocked    → 14 days (default)  — blocked too long means it needs unblock
  dormant    → 90 days (default)  — mostly for archive review
  archived   → never flagged

ARCHITECTURE (v3.14.1.2 — bugfix release):

The v3.14.1 detector had three bugs that prevented it from working on real
workspaces:
  1. Read `entities.projects` instead of `entities.threads` (schema drift —
     records were renamed to threads but the helper wasn't updated).
  2. Looked at top-level keys when the canonical schema nests them under
     an `entities` wrapper.
  3. Status enum was 3 values (active/exploring/dormant); the canonical
     state machine has 6 (active/exploring/paused/blocked/dormant/archived).

v3.14.1.2 fixes all three. Plus uses the canonical `last_activity` field on
the thread record as the primary baseline (each thread tracks its own last
activity stamp; events are supplemental).

This module ships READ-ONLY `detect_stalled_projects()`. The write side
(`record_stall_state_changes()` — emits `project_stalled_flagged` events on
state change) lands in v3.14.2 when pulse + weekly-audit get wired in.

PER CONTRACT.md Rule 25 + Bug #81 architectural fix: any writes (none in
v3.14.1.x) go through atomic_append_jsonl. Direct file writes are FORBIDDEN.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).parent))
from skill_config_writer import load_skill_config  # noqa: E402


class StallFlag(TypedDict):
    thread_id: str  # canonical: id is still `project_NNN` for schema stability
    thread_status: str
    days_since_activity: int
    last_event_seq: int | None
    last_event_type: str | None
    baseline_source: str  # "last_activity" | "event_scan" | "first_seen"
    recommended_action: str


# All 6 canonical statuses per ORG_AND_THREAD_MODEL v2.10.3+
DEFAULT_CONFIG = {
    "thresholds": {
        "active_days": 14,
        "exploring_days": 30,
        "paused_days": 45,
        "blocked_days": 14,
        "dormant_days": 90,
        # "archived" is intentionally absent — archived threads are never flagged
    },
    "activity_event_types": ["meeting", "commitment", "decision", "interaction"],
    "surface_locations": ["pulse_phase_9", "friday_wrap"],
}

# Threshold key per status. Returns None for "never flag" statuses.
_STATUS_TO_THRESHOLD_KEY = {
    "active": "active_days",
    "exploring": "exploring_days",
    "paused": "paused_days",
    "blocked": "blocked_days",
    "dormant": "dormant_days",
    "archived": None,  # archived threads are never flagged
}

SKILL_NAME = "stalled-projects"


def detect_stalled_projects(workspace_root: str | Path) -> list[StallFlag]:
    """Return list of currently-stalled threads. READ-ONLY — no event writes.

    Reads:
        - _hq/data/entities.json — threads (with both top-level and nested-
          under-`entities` shape support, plus legacy `projects` key fallback).
        - _hq/data/events.jsonl — filtered by primary_thread_id / project_id +
          activity event types from config.
        - _hq/data/skill_config/stalled-projects.json — optional, defaults used
          otherwise.

    Returns:
        List of StallFlag dicts, one per stalled thread. Empty if no threads
        exist, no entities.json, or all threads are under threshold.
    """
    workspace_root = Path(workspace_root)
    cfg = _load_config(workspace_root)

    entities_path = workspace_root / "_hq" / "data" / "entities.json"
    events_path = workspace_root / "_hq" / "data" / "events.jsonl"

    if not entities_path.exists():
        return []

    try:
        raw = json.loads(entities_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    threads = _extract_threads(raw)
    if not threads:
        return []

    # Build per-thread last-activity record from events.jsonl as a supplement
    # to the thread's own `last_activity` field
    activity_types = set(cfg["activity_event_types"])
    last_activity_by_thread = _scan_last_activity(events_path, activity_types)

    now = datetime.now(timezone.utc)
    flags: list[StallFlag] = []

    for thread in threads:
        flag = _evaluate_thread(thread, cfg, last_activity_by_thread, now)
        if flag is not None:
            flags.append(flag)

    return flags


def _extract_threads(raw: dict) -> list[dict]:
    """Defensive extraction — handles both canonical (nested under `entities`)
    and flat (top-level) entities.json shapes, plus both `threads` (canonical
    per ORG_AND_THREAD_MODEL) and `projects` (legacy schema key).

    Returns the threads list, deduping by `id` if both shapes happen to
    coexist.
    """
    container = raw.get("entities") if isinstance(raw.get("entities"), dict) else raw
    threads: list[dict] = []
    seen_ids: set[str] = set()

    # Prefer canonical `threads` key
    for record in container.get("threads", []) or []:
        if isinstance(record, dict) and record.get("id") and record["id"] not in seen_ids:
            threads.append(record)
            seen_ids.add(record["id"])

    # Fall back to legacy `projects` key
    for record in container.get("projects", []) or []:
        if isinstance(record, dict) and record.get("id") and record["id"] not in seen_ids:
            threads.append(record)
            seen_ids.add(record["id"])

    return threads


def _load_config(workspace_root: Path) -> dict:
    saved = load_skill_config(workspace_root, SKILL_NAME)
    if saved is None:
        return DEFAULT_CONFIG
    cfg = saved.get("config") or DEFAULT_CONFIG
    # Merge against defaults so configs saved before v3.14.1.2 (which only had
    # active/exploring/dormant thresholds) still work — paused/blocked/dormant
    # fall back to the default values.
    merged_thresholds = {**DEFAULT_CONFIG["thresholds"], **(cfg.get("thresholds") or {})}
    return {
        "thresholds": merged_thresholds,
        "activity_event_types": cfg.get("activity_event_types") or DEFAULT_CONFIG["activity_event_types"],
        "surface_locations": cfg.get("surface_locations") or DEFAULT_CONFIG["surface_locations"],
    }


def _scan_last_activity(
    events_path: Path, activity_types: set[str]
) -> dict[str, tuple[int, str, datetime]]:
    """Walk events.jsonl, return {thread_id: (max_seq, event_type, ts)}.

    Looks at both `data.project_id` (canonical) and `data.primary_thread_id`
    (newer events). Returns by the resolved thread id either way.
    """
    last: dict[str, tuple[int, str, datetime]] = {}
    if not events_path.exists():
        return last

    try:
        with events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") not in activity_types:
                    continue
                data = ev.get("data") or {}
                thread_id = data.get("project_id") or data.get("primary_thread_id")
                if not thread_id:
                    continue
                seq = ev.get("seq", 0)
                if not isinstance(seq, (int, float)) or isinstance(seq, bool):
                    continue
                try:
                    ts = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
                except (AttributeError, KeyError, ValueError):
                    continue
                prior = last.get(thread_id)
                if prior is None or seq > prior[0]:
                    last[thread_id] = (int(seq), ev["type"], ts)
    except OSError:
        return {}

    return last


def _evaluate_thread(
    thread: dict,
    cfg: dict,
    last_activity_by_thread: dict[str, tuple[int, str, datetime]],
    now: datetime,
) -> StallFlag | None:
    """Return StallFlag if thread is stalled, None otherwise.

    Baseline selection (in priority order):
      1. Most recent of (event-scan most-recent ts) vs (thread.last_activity field)
         — whichever is more recent wins. baseline_source = "last_activity" or
         "event_scan" depending on which won.
      2. If neither exists, fall back to thread.first_seen.
      3. If nothing, skip.
    """
    thread_id = thread.get("id")
    if not thread_id:
        return None

    status = thread.get("status") or thread.get("stage") or "active"
    threshold_key = _STATUS_TO_THRESHOLD_KEY.get(status, "active_days")
    if threshold_key is None:
        return None  # archived — never flag
    threshold = cfg["thresholds"].get(threshold_key, 14)

    # Try event-scan baseline
    event_baseline = last_activity_by_thread.get(thread_id)
    event_ts: datetime | None = None
    event_seq: int | None = None
    event_type: str | None = None
    if event_baseline is not None:
        event_seq, event_type, event_ts = event_baseline

    # Try thread's own last_activity stamp
    last_activity_str = thread.get("last_activity")
    field_ts: datetime | None = None
    if last_activity_str:
        try:
            # Accept date-only or full datetime; assume UTC if no tz
            if "T" in last_activity_str:
                field_ts = datetime.fromisoformat(last_activity_str.replace("Z", "+00:00"))
            else:
                field_ts = datetime.fromisoformat(last_activity_str + "T00:00:00+00:00")
        except ValueError:
            field_ts = None

    # Pick the most-recent baseline
    if event_ts is not None and field_ts is not None:
        if event_ts >= field_ts:
            baseline_ts = event_ts
            baseline_source = "event_scan"
            last_seq = event_seq
            last_type = event_type
        else:
            baseline_ts = field_ts
            baseline_source = "last_activity"
            last_seq = None
            last_type = None
    elif event_ts is not None:
        baseline_ts = event_ts
        baseline_source = "event_scan"
        last_seq = event_seq
        last_type = event_type
    elif field_ts is not None:
        baseline_ts = field_ts
        baseline_source = "last_activity"
        last_seq = None
        last_type = None
    else:
        # Zero-history fallback — use thread first_seen
        first_seen_str = thread.get("first_seen")
        if not first_seen_str:
            return None  # truly unknown — skip rather than false-flag
        try:
            if "T" in first_seen_str:
                baseline_ts = datetime.fromisoformat(first_seen_str.replace("Z", "+00:00"))
            else:
                baseline_ts = datetime.fromisoformat(first_seen_str + "T00:00:00+00:00")
        except ValueError:
            return None
        baseline_source = "first_seen"
        last_seq = None
        last_type = None

    days = (now - baseline_ts).days
    if days < threshold:
        return None

    return {
        "thread_id": thread_id,
        "thread_status": status,
        "days_since_activity": days,
        "last_event_seq": last_seq,
        "last_event_type": last_type,
        "baseline_source": baseline_source,
        "recommended_action": _recommended_action(status, days, baseline_source),
    }


def _recommended_action(status: str, days: int, baseline_source: str) -> str:
    if baseline_source == "first_seen":
        return f"Created {days} days ago with no activity since — decide whether to start or archive."
    if status == "dormant":
        return f"{days} days dormant — archive or revive."
    if status == "paused":
        return f"{days} days paused — confirm it's still on hold or resume."
    if status == "blocked":
        return f"{days} days blocked — unblock or change approach."
    if status == "exploring":
        return f"{days} days exploring without movement — decide if it's still worth pursuing."
    return f"{days} days since last activity — worth a touch this week."


__all__ = ["detect_stalled_projects", "StallFlag", "DEFAULT_CONFIG", "SKILL_NAME"]
