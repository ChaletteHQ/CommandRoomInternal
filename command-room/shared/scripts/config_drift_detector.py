#!/usr/bin/env python3
"""Config-drift detector (SPEC LB2 §3b — FIRST_RUN_PROTOCOL "Override-drift",
mechanized; LB1 deviation 2's missing pending store).

THE CONTRACT (shared/FIRST_RUN_PROTOCOL.md § Lifecycle, "Override-drift"):
cleanup runs this weekly; a knob whose saved config is **older than 6 months**
AND has accumulated **≥ 5 contradicting signals** since it was configured gets
ONE re-offer proposal — `propose(kind="config_drift", tier="confirm")` on the
Living Brain rail, rendered on the STAFF MEETING only (`surface_hint` — a
config nudge is never urgent, so it never reaches the daily card).

**Cleanup stays READ-ONLY on prefs.** This module never writes
`_hq/data/skill_config/` — the proposal write is not a pref write. Confirm on
the row appends the re-offer `note` event the next coach session consumes to
re-offer THAT KNOB (the coach's tune flow is the only config writer); dismiss
takes the standard 60-day ledger cooldown per knob (upgrading the protocol's
old once-ever prose rule to a mechanical one); snooze is the shared 7d.
`propose()`'s own fingerprint dedup + cooldown ledger make the re-offer
once-per-knob — this module keeps no store of its own.

WHAT COUNTS AS A CONTRADICTING SIGNAL (mechanical, never inferred):
  1. A voice-corrections row (`_hq/voice/corrections-<skill>.jsonl`) newer
     than the knob's `configured_at` that names the knob: an explicit
     `config_knob` field equal to the knob name, OR — the protocol's own
     example, sign-off fights — a `correction_type: "tone"` row whose notes
     mention the sign-off when the knob IS the skill's sign-off knob.
  2. An event whose `data.config_override` names `{skill, knob}` — the
     forward vocabulary for apply-choices-style per-fire overrides of a
     configured default.
Untagged contradictions never count: the detector proposes only what it can
cite, so it can never invent a drift claim. Both channels are dormant until
writers tag signals — the machinery lands with LB2 (the FB-20 posture:
close the gap before the traffic exists).

stdlib only; all reads defensive; never raises into a caller.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The FIRST_RUN_PROTOCOL bar, verbatim: config > 6 months AND >= 5 signals.
DRIFT_MIN_AGE_DAYS = 183
DRIFT_MIN_SIGNALS = 5

# Knob names treated as "the sign-off knob" for signal channel 1's
# tone-correction rule (the protocol's named example).
_SIGNOFF_KNOBS = frozenset({"sign_off", "signoff", "sign_off_style"})

_DRIFT_ACTIONS = [{"action": "confirm proposal"},
                  {"action": "dismiss proposal"},
                  {"action": "snooze proposal 7d"}]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value) -> Optional[datetime]:
    from event_time import parse_ts

    return parse_ts(value)


def _configured_skills(workspace_root) -> List[dict]:
    """[{skill, configured_at, config}] from `_hq/data/skill_config/*.json`.
    Defensive: unreadable/shapeless files are skipped."""
    out: List[dict] = []
    cfg_dir = Path(workspace_root) / "_hq" / "data" / "skill_config"
    if not cfg_dir.is_dir():
        return out
    for p in sorted(cfg_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        cfg = data.get("config")
        if not isinstance(cfg, dict) or not cfg:
            continue
        out.append({"skill": p.stem,
                    "configured_at": data.get("configured_at") or "",
                    "config": cfg})
    return out


def _correction_signals(workspace_root, skill: str, knob: str,
                        since: datetime) -> int:
    """Channel 1 — knob-tagged voice-corrections rows newer than `since`."""
    path = Path(workspace_root) / "_hq" / "voice" / f"corrections-{skill}.jsonl"
    if not path.exists():
        return 0
    n = 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        ts = _parse_ts(row.get("timestamp"))
        if ts is None or ts <= since:
            continue
        if row.get("config_knob") == knob:
            n += 1
        elif (knob in _SIGNOFF_KNOBS
              and row.get("correction_type") == "tone"
              and "sign-off" in str(row.get("notes") or "").lower()):
            n += 1
    return n


def _override_signals(events: List[dict], skill: str, knob: str,
                      since: datetime) -> int:
    """Channel 2 — events whose data.config_override names {skill, knob}."""
    n = 0
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ov = data.get("config_override")
        if not isinstance(ov, dict):
            continue
        if ov.get("skill") != skill or ov.get("knob") != knob:
            continue
        ts = _parse_ts(ev.get("ts"))
        if ts is not None and ts > since:
            n += 1
    return n


def detect_config_drift(workspace_root, *,
                        now_iso: Optional[str] = None) -> List[dict]:
    """Pure DETECT: [{skill, knob, n_signals, configured_at}] for every knob
    past the protocol bar (config > DRIFT_MIN_AGE_DAYS old AND
    >= DRIFT_MIN_SIGNALS tagged contradictions since configured). Reads
    skill_config + corrections logs + events; writes NOTHING."""
    now = _parse_ts(now_iso or _now_iso())
    if now is None:
        return []
    try:
        from events_io import load_all
        events = load_all(workspace_root)
    except Exception:
        events = []
    out: List[dict] = []
    for rec in _configured_skills(workspace_root):
        configured = _parse_ts(rec["configured_at"])
        if configured is None:
            continue  # undatable config can never cross the age bar honestly
        if (now - configured) < timedelta(days=DRIFT_MIN_AGE_DAYS):
            continue
        for knob in rec["config"]:
            n = (_correction_signals(workspace_root, rec["skill"], knob,
                                     configured)
                 + _override_signals(events, rec["skill"], knob, configured))
            if n >= DRIFT_MIN_SIGNALS:
                out.append({"skill": rec["skill"], "knob": knob,
                            "n_signals": n,
                            "configured_at": rec["configured_at"]})
    return out


def run_drift_detector(workspace_root, *,
                       now_iso: Optional[str] = None) -> dict:
    """The weekly cleanup entry point: detect, then propose ONE
    `config_drift` row per drifted knob through `brain_proposals.propose()`
    (which supplies the once-per-knob discipline: open-row fingerprint dedup
    + the 60d decline cooldown ledger). Returns
    {candidates, proposed, suppressed} counts. Prefs are never written —
    the proposal is the only write, and it goes through the event gate."""
    now_iso = now_iso or _now_iso()
    from brain_proposals import propose

    candidates = detect_config_drift(workspace_root, now_iso=now_iso)
    proposed, suppressed = 0, 0
    for c in candidates:
        evidence = (f"the {c['knob']} setting on {c['skill']} was configured "
                    f"over 6 months ago and {c['n_signals']} recent "
                    "corrections have gone against it")
        try:
            r = propose(
                workspace_root,
                kind="config_drift",
                tier="confirm",
                fingerprint=f"config_drift:{c['skill']}:{c['knob']}",
                detector="config-drift",
                evidence=evidence,
                action_tuples=list(_DRIFT_ACTIONS),
                render_line=(f"your {c['skill']} keeps getting corrected on "
                             f"{c['knob']} — want to re-tune that setting?"),
                extra={
                    "surface_hint": "staff-meeting",
                    "skill": c["skill"],
                    "knob": c["knob"],
                    "title": f"{c['skill']} — {c['knob']} setting",
                },
            )
        except Exception:
            suppressed += 1
            continue
        if r.get("status") == "proposed":
            proposed += 1
        else:
            suppressed += 1
    return {"candidates": len(candidates), "proposed": proposed,
            "suppressed": suppressed}


__all__ = [
    "DRIFT_MIN_AGE_DAYS",
    "DRIFT_MIN_SIGNALS",
    "detect_config_drift",
    "run_drift_detector",
]
