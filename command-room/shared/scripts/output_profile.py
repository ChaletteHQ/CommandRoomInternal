#!/usr/bin/env python3
"""Cross-skill output profile for every Command Room .docx deliverable (SPEC OUT2 §5).

WHY THIS EXISTS
---------------
The composer family gained per-skill knobs (FRP1) and standing directives (SCL1),
but some preferences are properties of the OUTPUT LAYER itself, not of any one
skill: how dense the prose runs, whether visuals lead, how long a given kind may
run. This module is the ONE place those knobs resolve, so `make_brief` applies
them uniformly and no skill re-implements them.

DEFAULTS-FIRST, PROFILE DORMANT (the brand.py posture, applied again)
---------------------------------------------------------------------
The overwhelming common case is a workspace with NO profile configured. For that
case `get_output_profile()` returns `DEFAULT_OUTPUT_PROFILE` byte-stably — no
warning, no event, no config required — and every default equals today's
behavior exactly (an absent file is indistinguishable from the pre-profile
world; same bytes out).

⛔ FENCE (SPEC OUT2 §5): there is NO first-run block for the output profile and
NO onboarding mention — it starts empty and is written ONLY by an explicit
`tune output` request or an insight-generator proposal (confirm-first). It is a
power-user surface until Wave 3. Do not add it to any first-run catalog.

STORAGE
-------
`_hq/data/skill_config/output_profile.json` — the FRP1 store, written through
`skill_config_writer.save_skill_config(ws, "output_profile", {...})` (atomic,
schema-validated, evented). This module is the READ path; it tolerates both the
writer's wrapper shape ({"config": {...}}) and a raw dict.

THE KNOBS (defaults first — the default IS today's behavior)
------------------------------------------------------------
  density        "tight" (default — today's spacing) | "narrative" (looser body
                 paragraph spacing for readers who prefer prose to scan-density)
  visual_bias    "tiles_first" (default — today's render order: tiles above
                 body within a section) | "prose_first" (body above tiles)
  page_cap       {} (default — no caps) | {<brief_kind>: <max pages int>}.
                 WARN-ONLY: make_brief estimates length and notes an over-cap
                 render on stderr; it never blocks or truncates a save.
  default_format "docx" (default) | "premium_html" (SPEC OUT5 — the shared
                 premium HTML brief, shared/scripts/premium_html.py). Applies
                 only to the LAUNCHED kinds (PREMIUM_LAUNCH_KINDS); everything
                 else renders docx regardless of profile. Unknown values
                 resolve back to "docx" silently.
  format_by_kind {} (default) | {<brief_kind>: "docx"|"premium_html"} (SPEC
                 OUT5). Per-kind override beating default_format. Unknown /
                 unlaunched kinds resolve to docx silently (the existing
                 posture); a trigger-level ask ("as a doc" / "as HTML") beats
                 the profile for that render — see resolve_format_for_kind.

Unknown keys and invalid values are IGNORED at resolution (a typo can never
silently reshape a document — it just keeps the default), and surfaced by
`validate_output_profile` for the writer path.

Stdlib only. Mirrors brand.py: read at render time, never cached across
workspaces, always safe to call, never raises for a client.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Optional, Union


DEFAULT_OUTPUT_PROFILE = {
    "density": "tight",
    "visual_bias": "tiles_first",
    "page_cap": {},
    "default_format": "docx",
    "format_by_kind": {},
}

_DENSITY_VALUES = frozenset({"tight", "narrative"})
_VISUAL_BIAS_VALUES = frozenset({"tiles_first", "prose_first"})
_FORMAT_VALUES = frozenset({"docx", "premium_html"})  # SPEC OUT5 (Wave 3)
_TOP_KEYS = frozenset(DEFAULT_OUTPUT_PROFILE)

# SPEC OUT5 §3c — the kinds a profile may flip to premium HTML at launch.
# Everything else renders docx regardless of profile (unknown / unlaunched
# kind → docx, silently — the existing posture). Grow this set as later OUT
# waves launch kinds; a client profile written early for an unlaunched kind is
# legal-but-inert until then.
PREMIUM_LAUNCH_KINDS = frozenset({
    "board_pack",
    "one_pager",
    "value_receipt",
    "research",
})

# Per-kind BASE format, consulted below format_by_kind but above
# default_format. research has rendered premium HTML since it shipped (its
# skill-level default) — an unconfigured workspace must keep that behavior, so
# its base is premium_html rather than inheriting the docx default. An explicit
# format_by_kind entry still wins (that is how a client pins research to docx).
_KIND_BASE_FORMAT = {
    "research": "premium_html",
}


def _load_saved(workspace_root: Union[str, "os.PathLike", None]) -> Optional[dict]:
    """The saved profile dict, or None. Tolerates the skill_config_writer wrapper
    ({"config": {...}}) and a raw dict. Missing / malformed => None (defaults),
    same defensive posture as brand._load_entities. Never raises."""
    if workspace_root is None:
        return None
    try:
        root = Path(workspace_root)
    except TypeError:
        return None
    path = root / "_hq" / "data" / "skill_config" / "output_profile.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    cfg = data.get("config") if isinstance(data.get("config"), dict) else data
    return cfg if isinstance(cfg, dict) else None


def get_output_profile(
    workspace_root: Union[str, "os.PathLike", None] = None,
) -> dict:
    """Resolve the output profile for a render.

    Resolution: DEFAULT_OUTPUT_PROFILE overlaid with the saved profile's KNOWN,
    VALID entries. An absent / malformed / empty profile = the defaults, byte-
    stably, with no warning and no event. Invalid values (bad density, unknown
    format, non-int page cap) keep the default — a typo can never silently
    reshape a document.

    Returns a fully-populated fresh dict (every DEFAULT key present). Safe to
    call at render time; never raises for a client.
    """
    out = copy.deepcopy(DEFAULT_OUTPUT_PROFILE)
    saved = _load_saved(workspace_root)
    if not saved:
        return out

    density = saved.get("density")
    if density in _DENSITY_VALUES:
        out["density"] = density

    bias = saved.get("visual_bias")
    if bias in _VISUAL_BIAS_VALUES:
        out["visual_bias"] = bias

    caps = saved.get("page_cap")
    if isinstance(caps, dict):
        out["page_cap"] = {
            k: v for k, v in caps.items()
            if isinstance(k, str) and isinstance(v, int)
            and not isinstance(v, bool) and v > 0
        }

    fmt = saved.get("default_format")
    if fmt in _FORMAT_VALUES:
        out["default_format"] = fmt

    by_kind = saved.get("format_by_kind")
    if isinstance(by_kind, dict):
        out["format_by_kind"] = {
            k: v for k, v in by_kind.items()
            if isinstance(k, str) and k.strip() and v in _FORMAT_VALUES
        }

    return out


def resolve_format_for_kind(
    brief_kind: str,
    workspace_root: Union[str, "os.PathLike", None] = None,
    *,
    override: Optional[str] = None,
) -> str:
    """The rendering backend for one deliverable: "docx" or "premium_html"
    (SPEC OUT5 §3c). Composers call this at render time and route to
    `brief_writer.make_brief` (docx) or `premium_html.make_premium_brief`
    (premium_html) — same payload either way; the gate stack is identical by
    construction (brief_gates).

    Resolution, most-specific first:
      1. Kind fence: a kind outside PREMIUM_LAUNCH_KINDS renders docx, always,
         silently — no override and no profile changes that (the existing
         unknown-value posture).
      2. `override` — the trigger-level ask ("as a doc" → "docx", "as HTML" →
         "premium_html") beats the profile for that render. Invalid / absent
         override falls through.
      3. `format_by_kind[brief_kind]` from the saved profile.
      4. The kind's base format (research → premium_html — its shipped
         default; an unconfigured workspace keeps research-as-HTML).
      5. `default_format` (docx unless tuned).

    Never raises; always returns a valid format string. An unconfigured
    workspace resolves docx for every kind except research — byte-identical
    to the pre-OUT5 world."""
    if brief_kind not in PREMIUM_LAUNCH_KINDS:
        return "docx"
    if override in _FORMAT_VALUES:
        return override
    profile = get_output_profile(workspace_root)
    fmt = profile["format_by_kind"].get(brief_kind)
    if fmt in _FORMAT_VALUES:
        return fmt
    if brief_kind in _KIND_BASE_FORMAT:
        return _KIND_BASE_FORMAT[brief_kind]
    return profile["default_format"]


def validate_output_profile(obj: object) -> list:
    """Return a list of human-readable problems with a profile object, or [] if
    it is a clean partial-or-full profile. For the WRITE path (`tune output` /
    insight-generator proposals) to call before `save_skill_config` — the read
    path never needs it (resolution is already typo-safe).

    Permissive by design (a profile is a PARTIAL override — every key optional),
    strict on the keys that ARE present."""
    problems: list = []
    if not isinstance(obj, dict):
        return [f"output profile must be an object, got {type(obj).__name__}"]

    for key in obj:
        if key not in _TOP_KEYS:
            problems.append(
                f"unknown output-profile key {key!r} (allowed: {sorted(_TOP_KEYS)})"
            )

    if "density" in obj and obj["density"] not in _DENSITY_VALUES:
        problems.append(
            f"density must be one of {sorted(_DENSITY_VALUES)}, got {obj['density']!r}"
        )
    if "visual_bias" in obj and obj["visual_bias"] not in _VISUAL_BIAS_VALUES:
        problems.append(
            f"visual_bias must be one of {sorted(_VISUAL_BIAS_VALUES)}, "
            f"got {obj['visual_bias']!r}"
        )
    if "default_format" in obj and obj["default_format"] not in _FORMAT_VALUES:
        problems.append(
            f"default_format must be one of {sorted(_FORMAT_VALUES)}, "
            f"got {obj['default_format']!r}"
        )
    if "format_by_kind" in obj:
        by_kind = obj["format_by_kind"]
        if not isinstance(by_kind, dict):
            problems.append(
                "format_by_kind must be an object of {brief_kind: 'docx'|'premium_html'}"
            )
        else:
            for k, v in by_kind.items():
                if not isinstance(k, str) or not k.strip():
                    problems.append(f"format_by_kind key {k!r} must be a brief-kind string")
                elif v not in _FORMAT_VALUES:
                    problems.append(
                        f"format_by_kind.{k} must be one of {sorted(_FORMAT_VALUES)}, "
                        f"got {v!r}"
                    )
    if "page_cap" in obj:
        caps = obj["page_cap"]
        if not isinstance(caps, dict):
            problems.append("page_cap must be an object of {brief_kind: max pages}")
        else:
            for k, v in caps.items():
                if not isinstance(k, str):
                    problems.append(f"page_cap key {k!r} must be a brief-kind string")
                elif isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                    problems.append(
                        f"page_cap.{k} must be a positive integer page count, got {v!r}"
                    )
    return problems


__all__ = [
    "DEFAULT_OUTPUT_PROFILE",
    "PREMIUM_LAUNCH_KINDS",
    "get_output_profile",
    "resolve_format_for_kind",
    "validate_output_profile",
]
