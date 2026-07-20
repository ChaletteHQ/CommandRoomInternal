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

ARCHITECTURE (v4.5.2 C3 — FINDINGS F-54):

The v3.14.1.2 "field is primary, events supplemental" doctrine was BACKWARDS
and its event scan never matched real events anyway:

  1. `thread.last_activity` is a fossil — the cleanup autopsy (F-61)
     proved NO code path maintains it. Ranking by it produced "Acme Co —
     43 days quiet" on a day with two Acme meetings and ten commitments
     written.
  2. The event scan read thread ids from `data.project_id` /
     `data.primary_thread_id`; canonical events carry `primary_thread_id`
     (+ `related_thread_ids[]`) at the event's TOP LEVEL, so on real
     substrates the scan matched nothing and the fossil always won. The
     old unit fixture mirrored the code's wrong assumption, keeping tests
     green (the realdata-fixture bug class).

v4.5.2 derivation rule: staleness derives from EVENTS at read time via the
shared `thread_activity.derive_thread_activity()` helper (top-level +
related + legacy id spellings, confidence floor, shard-transparent). Events
STRICTLY beat the stored field — no max() blend; a blend re-opens the F-54
hole whenever a scan under-matches. The deprecated `last_activity` field is
consulted ONLY for threads with zero event history (fresh-ingest record
stamps), then `first_seen`. A thread with same-day substrate activity is
structurally incapable of flagging.

Pulse Phase 4 (orchestrator-dont-forget.md) MUST use the same helper with
the same activity_event_types so both surfaces quote the same day-count
(F-54's 21d-vs-37d cross-surface split).

`apply_live_check()` is the dormant-scan-discipline gate (F-57): the
orchestrator live-checks flagged threads against Gmail/Calendar and this
function drops (with reasons) any flag whose live signal lands under
threshold — substrate-quiet + live-active = not stalled, say why.

This module ships READ-ONLY `detect_stalled_projects()`. The write side
(`record_stall_state_changes()` — emits `project_stalled_flagged` events on
state change) lands in v3.14.2 when pulse + weekly-audit get wired in.
(The stalled-projects SKILL's pack_run scan receipt is written by the
orchestrator via receipts.log_receipt, not from this read-only helper.)

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
from thread_activity import derive_thread_activity  # noqa: E402


class StallFlag(TypedDict):
    thread_id: str  # canonical: id is still `project_NNN` for schema stability
    thread_status: str
    days_since_activity: int
    threshold_days: int
    last_event_seq: int | None
    last_event_type: str | None
    baseline_source: str  # "event_scan" | "last_activity" (zero-event fallback) | "first_seen"
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
        - events via thread_activity.derive_thread_activity (shard-transparent;
          top-level primary_thread_id + related_thread_ids + legacy spellings)
          filtered by activity event types from config.
        - _hq/data/skill_config/stalled-projects.json — optional, defaults used
          otherwise.

    Returns:
        List of StallFlag dicts, one per stalled thread. Empty if no threads
        exist, no entities.json, or all threads are under threshold.
    """
    workspace_root = Path(workspace_root)
    cfg = _load_config(workspace_root)

    entities_path = workspace_root / "_hq" / "data" / "entities.json"

    if not entities_path.exists():
        return []

    try:
        raw = json.loads(entities_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    threads = _extract_threads(raw)
    if not threads:
        return []

    # THE staleness baseline: derived from events at read time (C3 rule).
    # The stored thread.last_activity field is a fossil — zero-event
    # fallback only, inside _evaluate_thread.
    activity_types = set(cfg["activity_event_types"])
    last_activity_by_thread = derive_thread_activity(
        workspace_root, activity_types=activity_types
    )

    now = datetime.now(timezone.utc)
    flags: list[StallFlag] = []

    for thread in threads:
        # PIPE1 fence (D7): kind='deal' threads report through the pipeline
        # surface (deal_health's per-stage rot thresholds) — flagging them
        # here too would double-alarm the same quiet deal with the wrong,
        # project-generic threshold.
        if thread.get("kind") == "deal":
            continue
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


def _parse_stamp(value) -> datetime | None:
    """Date-only or full datetime string → aware datetime (UTC assumed when
    no tz). None on anything unparseable."""
    if not value or not isinstance(value, str):
        return None
    try:
        if "T" in value:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            ts = datetime.fromisoformat(value + "T00:00:00+00:00")
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _evaluate_thread(
    thread: dict,
    cfg: dict,
    last_activity_by_thread: dict,
    now: datetime,
) -> StallFlag | None:
    """Return StallFlag if thread is stalled, None otherwise.

    Baseline selection (C3 rule — in strict priority order, never blended):
      1. Event-scan most-recent ts (thread_activity derivation). If ANY
         event touches the thread, this IS the baseline — the stored field
         can never override derived activity (F-54).
      2. Zero events only: the deprecated thread.last_activity record stamp
         (fresh-ingest workspaces carry one before any event exists).
      3. Neither: thread.first_seen.
      4. Nothing: skip rather than false-flag.
    """
    thread_id = thread.get("id")
    if not thread_id:
        return None

    status = thread.get("status") or thread.get("stage") or "active"
    threshold_key = _STATUS_TO_THRESHOLD_KEY.get(status, "active_days")
    if threshold_key is None:
        return None  # archived — never flag
    threshold = cfg["thresholds"].get(threshold_key, 14)

    event_baseline = last_activity_by_thread.get(thread_id)
    if event_baseline is not None:
        baseline_ts = event_baseline.ts
        baseline_source = "event_scan"
        last_seq = event_baseline.seq
        last_type = event_baseline.event_type
    elif (field_ts := _parse_stamp(thread.get("last_activity"))) is not None:
        # DEPRECATED-field fallback — legitimate only because zero events
        # exist for this thread; see thread_activity.py deprecation rule.
        baseline_ts = field_ts
        baseline_source = "last_activity"
        last_seq = None
        last_type = None
    else:
        # Zero-history fallback — use thread first_seen
        baseline_ts = _parse_stamp(thread.get("first_seen"))
        if baseline_ts is None:
            return None  # truly unknown — skip rather than false-flag
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
        "threshold_days": threshold,
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


def apply_live_check(
    flags: list[StallFlag],
    live_signals: dict[str, dict],
    now: datetime | None = None,
) -> tuple[list[StallFlag], list[dict]]:
    """The dormant-scan-discipline gate (F-57 pattern, code-enforced merge).

    The orchestrator live-checks every flagged thread against Gmail +
    Calendar (scoped to the thread's stakeholders / org — see the
    stalled-projects SKILL.md live-check step) and passes what it found:

        live_signals = {
            "project_012": {
                "live_last_iso": "2026-07-06",       # ISO date or datetime
                "source": "gmail",                    # "gmail" | "calendar"
                "detail": {"subject": "...", ...},    # optional, opaque
            },
            "project_017": {},                        # checked, no signal
        }

    Rules:
      - A flag whose live signal is MORE RECENT than its substrate baseline
        gets its day-count recomputed from the live date. Under threshold →
        DROPPED (substrate-quiet + live-active = not stalled); the dropped
        record carries `drop_reason` so the surface can say why (F-57's
        "Summit — substrate-dormant, live 2 days ago" honesty).
      - Still over threshold on the live date → kept, with the honest
        (smaller) day-count and `live_checked: True`.
      - No signal / absent thread key → kept unchanged. The CALLER is
        responsible for surfacing connector failures honestly ("I couldn't
        check live email just now...") — absence of signal here is treated
        as checked-and-quiet.

    Returns (kept_flags, dropped_records).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    kept: list[StallFlag] = []
    dropped: list[dict] = []

    for flag in flags:
        signal = live_signals.get(flag["thread_id"]) or {}
        live_ts = _parse_stamp(signal.get("live_last_iso"))
        if live_ts is None:
            kept.append(flag)
            continue
        live_days = (now - live_ts).days
        if live_days >= flag["days_since_activity"]:
            # Live signal is older than (or equal to) what the substrate
            # already knew — it changes nothing.
            kept.append(flag)
            continue
        source = signal.get("source") or "live"
        if live_days < flag["threshold_days"]:
            dropped.append({
                **flag,
                "live_last_iso": signal.get("live_last_iso"),
                "live_source": source,
                "live_detail": signal.get("detail"),
                "drop_reason": (
                    f"quiet in saved history, but live {source} shows a touch "
                    f"{live_days} day{'s' if live_days != 1 else ''} ago — not stalled"
                ),
            })
            continue
        updated: StallFlag = {
            **flag,
            "days_since_activity": live_days,
            "recommended_action": _recommended_action(
                flag["thread_status"], live_days, "event_scan"
            ),
        }
        updated["live_checked"] = True  # type: ignore[typeddict-unknown-key]
        updated["live_source"] = source  # type: ignore[typeddict-unknown-key]
        kept.append(updated)

    return kept, dropped


__all__ = [
    "detect_stalled_projects",
    "apply_live_check",
    "StallFlag",
    "DEFAULT_CONFIG",
    "SKILL_NAME",
]
