#!/usr/bin/env python3
"""
surface_context.py — which surface is this session rendering to? (SPEC_SLACK1 B-3/§10.3)

WHY THIS EXISTS
===============
SLACK1 ports Command Room to a second surface (Slack, via the always-on VM
listener) without forking the plugin. Surface identity therefore has to be
DATA, resolved one way everywhere:

  - The Slack listener's system-prompt append declares `SURFACE=slack` and
    exports the same value as an env var for scripts (B-3).
  - Cowork sets nothing — absent env means `cowork`, which keeps every
    existing desktop code path byte-identical by construction.

Presentation knobs for the slack surface live in
`shared/config/surface_profiles.json` (§10.3) — JSON-editable, shipped
through the normal plugin-update ritual, read here. The cowork profile is
deliberately empty: desktop presentation is the untouched default and this
module must never grow a knob that changes it.

SURFACE VOCABULARY
==================
`cowork` and `slack` — the same registry the `surfaces:` SKILL.md frontmatter
field draws its values from (`cowork | slack | both`; guard G24). An UNKNOWN
`SURFACE` env value raises `SurfaceError` loudly rather than falling through
to a default: an unlearned surface silently rendering as desktop is exactly
the silent-degradation class the validator culture exists to kill (the
audit-vocab unknown→error lesson, 2026-08-05).

NOTE: this is a different axis than `surface_split.py` (CTS1 — the
Waiting On / My Plate partition of the commitment lane) and than the
`surface=` audience tag on `validate_chat_output` (PGUARD1 — org vs owner
audience). This module answers "which RUNTIME is rendering", nothing else.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SURFACE_ENV_VAR = "SURFACE"
KNOWN_SURFACES = ("cowork", "slack")
DEFAULT_SURFACE = "cowork"

_PROFILES_PATH = Path(__file__).resolve().parent.parent / "config" / "surface_profiles.json"

# Baked-in floor for the slack profile — the JSON overrides these, but a
# missing/unreadable config file must degrade to WORKING slack output, not to
# a crash and not to unbounded output (Slack's hard cap is 50 blocks/message).
_SLACK_FALLBACK_PROFILE = {
    "block_budget": 50,
    "char_budget": 3000,
    "rows_per_page": 8,
    "column_collapse": [],
    "chart_mode": "native",
    "table_mode": "native",
    "verbosity": "compact",
    "body_preview_lines": 3,
}


class SurfaceError(ValueError):
    """Raised when the SURFACE env var carries a value outside KNOWN_SURFACES."""


def current_surface() -> str:
    """The active render surface: `cowork` (default, env absent/empty) or `slack`.

    Raises SurfaceError on an unknown value — never silently defaults an
    unlearned surface to desktop behavior.
    """
    raw = os.environ.get(SURFACE_ENV_VAR, "").strip().lower()
    if not raw:
        return DEFAULT_SURFACE
    if raw not in KNOWN_SURFACES:
        raise SurfaceError(
            f"SURFACE={raw!r} is not a known surface {KNOWN_SURFACES}. "
            "Fix the listener/service env — do not guess a fallback."
        )
    return raw


def load_profile(surface: str | None = None, *, skill: str | None = None) -> dict:
    """Presentation knobs for `surface` (default: current), with per-skill
    overrides merged over the surface default.

    slack: JSON `slack.default` merged over the baked-in floor, then
    `slack.per_skill[<skill>]` merged over that. cowork: always `{}` —
    desktop takes no knobs from this file by design.
    """
    surface = surface or current_surface()
    if surface not in KNOWN_SURFACES:
        raise SurfaceError(f"unknown surface {surface!r}")
    if surface == "cowork":
        return {}
    profile = dict(_SLACK_FALLBACK_PROFILE)
    try:
        raw = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Missing/corrupt config → the floor above. Loud enough downstream:
        # the emitter's budgets still hold, and G24 keeps the file existing.
        return profile
    surface_cfg = raw.get(surface) or {}
    profile.update(surface_cfg.get("default") or {})
    if skill:
        profile.update((surface_cfg.get("per_skill") or {}).get(skill) or {})
    return profile


__all__ = [
    "SURFACE_ENV_VAR",
    "KNOWN_SURFACES",
    "DEFAULT_SURFACE",
    "SurfaceError",
    "current_surface",
    "load_profile",
]
