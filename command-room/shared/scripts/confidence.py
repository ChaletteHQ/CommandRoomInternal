#!/usr/bin/env python3
"""
Shared confidence + match-score constants (v3.5.0+).

Before v3.5.0 every orchestrator hardcoded its own thresholds inline:
  - orchestrator-commitments.md Phase 3 filter:       confidence >= 0.7
  - orchestrator-past-meetings.md Phase 4 auto-commit: confidence >= 0.8
  - orchestrator-past-meetings.md Phase 4.5b people-crm: confidence >= 0.85
  - orchestrator-dont-forget.md Phase 5 auto-apply:    confidence >= 0.85
  - decision_match.py:                                 score >= 0.65
  - cru_match.py (Path 1 / Path 3 match scoring):     score >= 0.55 (HIGH), 0.30 (PENDING)

Five different numbers across the codebase, all called "confidence threshold"
or "match threshold" depending on which file you read. Local choices were
reasonable; the cross-orchestrator inconsistency made it hard to reason about
"high-confidence" globally and impossible to tune via a single dial.

v3.5.0 consolidates into two named families. Callers import from here.

TWO FAMILIES — they are semantically different:

EXTRACTION CONFIDENCE — how confident the EXTRACTION pipeline is that a
particular commitment / decision / person-record actually exists in the
source material (transcript, email, etc.). Stored on the event as
`data.confidence` (sometimes `classification_confidence`). Used by
downstream consumers as a quality filter: only surface events the extractor
believed in.

MATCH SCORE — how strongly a NEW event matches an EXISTING event for
cross-reference purposes (CRU layer: did this outbound email match this open
commitment? did this transcript chunk complete this open decision?). Computed
in cru_match.py / decision_match.py via Jaccard + overlap-coefficient.

These are different axes. A high-extraction-confidence commitment can have a
low-match-score to a particular email; that means "we're sure the commitment
exists, we're not sure this email is what fulfilled it." Conflating the two
under one threshold was the v3.5.0 audit finding.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# EXTRACTION CONFIDENCE — used by consumers to filter low-quality extractions
# ---------------------------------------------------------------------------

# Below this, we don't even surface the item in the daily Commitments fire.
# Equivalent to "the extractor isn't sure this is really a commitment."
# Used by: orchestrator-commitments Phase 3 filter.
CONFIDENCE_SURFACE_MIN = 0.7

# Auto-commit threshold for fresh extraction during past-meetings processing.
# Items above this are written committed=true to events.jsonl; items below
# go to pending_review.
# Used by: orchestrator-past-meetings Phase 4 step 4-5.
CONFIDENCE_AUTOCOMMIT = 0.8

# Auto-apply threshold for people-crm record changes detected during real-time
# transcript processing. Higher than autocommit because people-record changes
# write to entities.json and false-positives corrupt the canonical relationship
# layer.
# Used by: orchestrator-past-meetings Phase 4.5b people-crm pass,
#          orchestrator-dont-forget Phase 5 auto-apply.
CONFIDENCE_AUTOAPPLY_PEOPLE = 0.85


# ---------------------------------------------------------------------------
# MATCH SCORES — used by CRU layer to decide auto-resolve vs. pending review
# ---------------------------------------------------------------------------

# Cross-reference match (cru_match.match_send_to_commitments,
# match_transcript_to_commitments). At or above this score, auto-resolve the
# matched commitment silently. Below this but >= PENDING, write a
# commitment_review_proposed event for the confirm queue to surface for
# one-click confirm.
# Used by: cru_match.py HIGH_CONFIDENCE_THRESHOLD.
MATCH_SCORE_AUTO_RESOLVE = 0.55

# Below auto-resolve, above this: pending review surface. Below this: no action.
# Used by: cru_match.py PENDING_REVIEW_THRESHOLD.
MATCH_SCORE_PENDING_REVIEW = 0.30

# Decision-match auto-resolve threshold. Tighter than commitments because
# decision false-positives lose real history. No pending review path in v3.4.5;
# borderline matches simply don't act.
# Used by: decision_match.py DECISION_HIGH_CONFIDENCE_THRESHOLD.
DECISION_MATCH_AUTO_RESOLVE = 0.65


# ---------------------------------------------------------------------------
# Workspace-side overrides (Phase 6 Loop 4 — confidence calibration)
# ---------------------------------------------------------------------------
#
# The constants above are the SHIPPED defaults. insight-generator's Loop 4
# calibration pass measures the confirm-rate of each confidence band from a
# workspace's own `commitment_review_proposed` / `commitment_review_dismissed`
# / `commitment_resolved` outcomes and, when a band is consistently right (or
# consistently reversed) over a small-n floor, proposes a per-workspace override
# — user-approved through the review widget, written to
# `_hq/data/confidence-overrides.json`. These accessors are the read side: same
# pattern as the voice-block overrides over baked-in blocks. They ALWAYS fall
# back to the shipped constant, so a workspace with no override behaves exactly
# as before (every existing caller that imports the constant directly is
# unaffected; callers opt into calibration by calling the accessor with a
# workspace root).
import json as _json
from pathlib import Path as _Path
from typing import Optional as _Optional

_BAKED = {
    "CONFIDENCE_SURFACE_MIN": CONFIDENCE_SURFACE_MIN,
    "CONFIDENCE_AUTOCOMMIT": CONFIDENCE_AUTOCOMMIT,
    "CONFIDENCE_AUTOAPPLY_PEOPLE": CONFIDENCE_AUTOAPPLY_PEOPLE,
    "MATCH_SCORE_AUTO_RESOLVE": MATCH_SCORE_AUTO_RESOLVE,
    "MATCH_SCORE_PENDING_REVIEW": MATCH_SCORE_PENDING_REVIEW,
    "DECISION_MATCH_AUTO_RESOLVE": DECISION_MATCH_AUTO_RESOLVE,
}


def _overrides_path(workspace_root) -> _Path:
    return _Path(workspace_root) / "_hq" / "data" / "confidence-overrides.json"


def load_overrides(workspace_root) -> dict:
    """The `{name: value}` override map, or {} when absent/unreadable. Never
    raises. Only known threshold names with sane [0,1] values are honored."""
    if workspace_root is None:
        return {}
    path = _overrides_path(workspace_root)
    if not path.exists():
        return {}
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    thresholds = data.get("thresholds") if isinstance(data, dict) else None
    if not isinstance(thresholds, dict):
        return {}
    out = {}
    for k, v in thresholds.items():
        if k in _BAKED and isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0:
            out[k] = float(v)
    return out


def get_threshold(name: str, workspace_root=None) -> float:
    """A threshold by name, honoring a workspace-side override when present, else
    the shipped constant. Unknown name → KeyError (a typo should fail loud)."""
    if name not in _BAKED:
        raise KeyError(f"unknown confidence threshold {name!r}")
    if workspace_root is not None:
        ov = load_overrides(workspace_root)
        if name in ov:
            return ov[name]
    return _BAKED[name]


# Named convenience accessors (workspace_root=None → shipped constant).
def surface_min(workspace_root=None) -> float:
    return get_threshold("CONFIDENCE_SURFACE_MIN", workspace_root)


def autocommit(workspace_root=None) -> float:
    return get_threshold("CONFIDENCE_AUTOCOMMIT", workspace_root)


def autoapply_people(workspace_root=None) -> float:
    return get_threshold("CONFIDENCE_AUTOAPPLY_PEOPLE", workspace_root)


def match_score_auto_resolve(workspace_root=None) -> float:
    return get_threshold("MATCH_SCORE_AUTO_RESOLVE", workspace_root)


def match_score_pending_review(workspace_root=None) -> float:
    return get_threshold("MATCH_SCORE_PENDING_REVIEW", workspace_root)


def decision_match_auto_resolve(workspace_root=None) -> float:
    return get_threshold("DECISION_MATCH_AUTO_RESOLVE", workspace_root)


# ---------------------------------------------------------------------------
# NON-SCORE SETTINGS (WATCHGATE §2.7)
# ---------------------------------------------------------------------------
#
# The accessors above clamp to [0,1] because every name they serve is a SCORE.
# WATCHGATE adds three knobs that are not scores at all — a posture level, a
# window measured in days, a per-fire question cap — and clamping those to
# [0,1] would silently zero every one of them. So they live in their own
# family, in a `settings` block of the SAME override file, with their own
# integer validation and their own bounds.
#
# THESE ARE NOT A CLIENT SETTINGS SURFACE. A plain-language posture toggle may
# write this file later; nothing in this module offers one.
#
# Invalid value -> the shipped default, LOUDLY. A knob that silently falls
# back reads exactly like a knob that was never set, and the difference
# matters when someone has deliberately turned a workspace up to level 3.

# The posture level (1 Handle it | 2 Balanced | 3 Check with me). Ships at 2.
POSTURE_LEVEL = 2
# How long an unproven promise is watched before its fate is decided.
WATCH_WINDOW_DAYS = 14
# How many questions ONE staff-meeting fire may surface. Overflow carries.
STAFFMEETING_ASK_CAP = 5

# name -> (shipped default, minimum, maximum). Bounds are sanity rails, not
# policy: a window of 0 days makes every watch expire instantly, and a cap of
# 0 makes every question vanish — both are indistinguishable from the feature
# being off, which is not a state a typo should be able to reach.
_BAKED_SETTINGS = {
    "POSTURE_LEVEL": (POSTURE_LEVEL, 1, 3),
    "WATCH_WINDOW_DAYS": (WATCH_WINDOW_DAYS, 1, 365),
    "STAFFMEETING_ASK_CAP": (STAFFMEETING_ASK_CAP, 1, 50),
}


def _warn(message: str) -> None:
    import sys as _sys
    _sys.stderr.write(f"[confidence] {message}\n")


def load_setting_overrides(workspace_root) -> dict:
    """The `{name: int}` override map for the non-score knobs, or {}.

    Reads the `settings` block, falling back to `thresholds` for these names
    (a hand-edited file may put them in the block it already had). Never
    raises. Every rejection is reported on stderr naming the key, the value
    and the default that will be used instead."""
    if workspace_root is None:
        return {}
    path = _overrides_path(workspace_root)
    if not path.exists():
        return {}
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _warn(f"could not read {path.name} — using shipped defaults")
        return {}
    if not isinstance(data, dict):
        return {}
    merged: dict = {}
    for block in ("thresholds", "settings"):
        section = data.get(block)
        if isinstance(section, dict):
            for k, v in section.items():
                if k in _BAKED_SETTINGS:
                    merged[k] = v
    out: dict = {}
    for k, v in merged.items():
        default, low, high = _BAKED_SETTINGS[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            _warn(f"{k}={v!r} is not a number — using shipped default {default}")
            continue
        if float(v) != int(v):
            _warn(f"{k}={v!r} is not a whole number — using shipped default {default}")
            continue
        iv = int(v)
        if not (low <= iv <= high):
            _warn(f"{k}={iv} is outside {low}..{high} — using shipped "
                  f"default {default}")
            continue
        out[k] = iv
    return out


def get_setting(name: str, workspace_root=None) -> int:
    """A non-score setting by name, honoring a valid workspace override, else
    the shipped constant. Unknown name → KeyError (a typo should fail loud)."""
    if name not in _BAKED_SETTINGS:
        raise KeyError(f"unknown setting {name!r}")
    if workspace_root is not None:
        ov = load_setting_overrides(workspace_root)
        if name in ov:
            return ov[name]
    return _BAKED_SETTINGS[name][0]


def posture_level(workspace_root=None) -> int:
    return get_setting("POSTURE_LEVEL", workspace_root)


def watch_window_days(workspace_root=None) -> int:
    return get_setting("WATCH_WINDOW_DAYS", workspace_root)


def staffmeeting_ask_cap(workspace_root=None) -> int:
    return get_setting("STAFFMEETING_ASK_CAP", workspace_root)


def _read_store(workspace_root) -> dict:
    """The whole override document, or {} — so a writer can rewrite ONE block
    without discarding the other."""
    path = _overrides_path(workspace_root)
    if not path.exists():
        return {}
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _persist_store(workspace_root, store: dict) -> _Optional[_Path]:
    try:
        from atomic_write import atomic_write_json
    except Exception:  # pragma: no cover
        import sys as _sys
        _sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from atomic_write import atomic_write_json  # type: ignore
    path = _overrides_path(workspace_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, store)
        return path
    except Exception:
        return None


def write_overrides(workspace_root, thresholds: dict) -> _Optional[_Path]:
    """Atomically persist the score overrides. Only known, in-range names are
    written.

    Reads the existing document first and replaces ONLY the `thresholds`
    block: this file gained a second family (WATCHGATE's `settings`), and a
    calibration write that rebuilt the document from scratch would silently
    reset a workspace's posture every time a threshold moved."""
    clean = {k: float(v) for k, v in (thresholds or {}).items()
             if k in _BAKED and isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0}
    store = _read_store(workspace_root)
    store["version"] = store.get("version") or 1
    store["thresholds"] = clean
    return _persist_store(workspace_root, store)


def write_settings(workspace_root, settings: dict) -> _Optional[_Path]:
    """The `settings`-block twin of `write_overrides` — same preserve-the-other
    -block rule, same silent-drop of anything unknown or out of range."""
    clean: dict = {}
    for k, v in (settings or {}).items():
        if k not in _BAKED_SETTINGS:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if float(v) != int(v):
            continue
        _default, low, high = _BAKED_SETTINGS[k]
        if low <= int(v) <= high:
            clean[k] = int(v)
    store = _read_store(workspace_root)
    store["version"] = store.get("version") or 1
    store["settings"] = clean
    return _persist_store(workspace_root, store)


__all__ = [
    "POSTURE_LEVEL",
    "WATCH_WINDOW_DAYS",
    "STAFFMEETING_ASK_CAP",
    "load_setting_overrides",
    "get_setting",
    "posture_level",
    "watch_window_days",
    "staffmeeting_ask_cap",
    "write_settings",
    "CONFIDENCE_SURFACE_MIN",
    "CONFIDENCE_AUTOCOMMIT",
    "CONFIDENCE_AUTOAPPLY_PEOPLE",
    "MATCH_SCORE_AUTO_RESOLVE",
    "MATCH_SCORE_PENDING_REVIEW",
    "DECISION_MATCH_AUTO_RESOLVE",
    "load_overrides",
    "get_threshold",
    "surface_min",
    "autocommit",
    "autoapply_people",
    "match_score_auto_resolve",
    "match_score_pending_review",
    "decision_match_auto_resolve",
    "write_overrides",
]
