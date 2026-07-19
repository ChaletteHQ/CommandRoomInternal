#!/usr/bin/env python3
"""Single theme source for every Command Room .docx deliverable (SPEC OUT1).

WHY THIS EXISTS
---------------
`brief_writer.py` used to hardcode its palette, fonts, and footer inline. That
made two things impossible without a code edit: (1) upgrading the default look,
and (2) letting a paying client carry their own brand. This module is the ONE
place a theme is resolved, so both become data, not code.

DEFAULTS-FIRST, BRAND DORMANT
-----------------------------
The overwhelming common case is a fresh, UNCONFIGURED workspace. For that case
`get_brand()` returns `DEFAULT_BRAND` byte-stably — no warning, no event, no
config required — and the default IS the premium deliverable (upgraded
typography + palette; still quiet-professional, no logo, no loud color).

The brand LAYER is wired now but stays dormant: nothing in onboarding asks about
branding, and no skill trigger sets it. Activation is data entry during a paid
customization engagement — M writes a `brand` object into the client's
`entities.json`:

    "workspace": { ..., "brand": { "palette": {"accent": "8A5A2B"},
                                    "logo_path": "_hq/brand/acme-mark.png" } }

or, for a single org's documents, onto that org's entity:

    {"id": "org_acme", "canonical_name": "Acme", "brand": { ... }}

Resolution is a deep-merge: DEFAULT_BRAND <- workspace.brand <- orgs[org_id].brand.
Any key a client omits keeps the (excellent) default. A missing brand object is
indistinguishable from the pre-brand world — same bytes out.

Mirrors `personification.py::get_brain_name`: read at render time, never cache
across workspaces, always safe to call, never raises for a client.

CONTRACT / PRIVACY (R26)
------------------------
`logo_path` renders a letterhead ONLY if the file actually exists on disk. A
configured-but-missing logo silently falls back to the quiet no-logo header —
never an error in a client chat. See brief_writer `_resolve_logo`.

Stdlib only.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Optional, Union

try:
    from read_alarm import record_read_alarm
except ImportError:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from read_alarm import record_read_alarm


# ---------------------------------------------------------------------------
# THE DEFAULT THEME — this is the deliverable (SPEC OUT1 §3c)
# ---------------------------------------------------------------------------
# Premium, quiet-professional, zero-config. The visible upgrade over the
# pre-OUT1 hardcoded look is TYPOGRAPHY (Georgia headings over Calibri body — an
# editorial serif/sans pairing) plus a refined navy + consistent accent + warm
# tile/zebra separation and shaded table headers.
#
# FONT RULE: default fonts must be universally installed (client machines are
# unknown). Georgia and Calibri both ship on stock Windows AND macOS; Consolas
# is the safe default mono. A CUSTOM font name is a per-client brand-object
# concern, NEVER a default — anything downloadable does not qualify here.
#
# Palette colors are 6-hex strings WITHOUT '#'. brief_writer converts to
# RGBColor at the point of use (see `_rgb`), and passes them straight to the
# cell-shading helper (which wants a bare hex). One representation, no drift.

DEFAULT_BRAND = {
    "palette": {
        "ink":        "1A1A1A",   # body text — near-black
        "heading":    "102A40",   # section headings / titles — refined deep navy
        "accent":     "2E7D6B",   # eyebrows, tile labels, timeline dates — dark teal
        "muted":      "6B6B6B",   # subtitle, footer, secondary — medium grey
        "rule":       "CFCFCF",   # header hairline rule — light grey
        "tile_bg":    "F4F1EC",   # stat-tile band background — warm cream
        "zebra":      "F7F5F1",   # table zebra stripe — a shade lighter than tile_bg
        "table_header": "102A40", # table header row fill — matches heading navy
        "col_header": "EFEBE5",   # matrix row-header (leftmost col) fill — warm grey
        "highlight":  "E8F1EE",   # recommended-row / matrix highlight — accent tint
        # Contract-review flag tints (SPEC OUT1 §4). Soft, print-safe fills.
        "flag_ok":    "E4F0E7",   # green tint — standard / favorable
        "flag_warn":  "FBF1DD",   # amber tint — worth a look
        "flag_bad":   "F7E2E0",   # red tint — off-market / push back
    },
    "fonts": {
        "heading": "Georgia",     # editorial serif — universally installed
        "body":    "Calibri",     # clean sans body — universally installed
        "mono":    "Consolas",    # safe default mono
    },
    "footer_line": "Command Room",
    "logo_path": None,            # None => quiet no-logo header (the default)
    "eyebrow_style": {
        "size": 9,                # pt
        "bold": True,
        "upper": True,            # render the eyebrow label uppercased
    },
}


# Keys a brand object may carry. Anything else is an unknown key (validation
# flags it; resolution ignores it so a typo can never silently theme a doc).
_PALETTE_KEYS = frozenset(DEFAULT_BRAND["palette"])
_FONT_KEYS = frozenset(DEFAULT_BRAND["fonts"])
_EYEBROW_KEYS = frozenset(DEFAULT_BRAND["eyebrow_style"])
_TOP_KEYS = frozenset(DEFAULT_BRAND)

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: Optional[dict]) -> dict:
    """Return a NEW dict: `base` with `override` merged in, recursively. Nested
    dicts merge key-by-key; a scalar (or absent key) in `override` replaces /
    leaves the base value. `base` is never mutated. Unknown keys in `override`
    are skipped at the top / palette / fonts / eyebrow levels — a client typo
    (`"acccent"`) can't silently theme the doc, it just keeps the default."""
    out = copy.deepcopy(base)
    if not isinstance(override, dict):
        return out
    for key, val in override.items():
        if key not in out:
            # Unknown key at this level — ignore (validation surfaces it).
            continue
        if isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        elif isinstance(out[key], dict) or isinstance(val, dict):
            # Type mismatch (client passed a scalar where a dict lives, or vice
            # versa) — keep the safe default rather than corrupt the theme.
            continue
        else:
            out[key] = val
    return out


def _load_entities(entities: Union[dict, str, "os.PathLike", None]) -> Optional[dict]:
    """Accept an already-loaded entities dict, a workspace_root path, or None.
    A path is read defensively (missing / malformed => None, same posture as
    personification.get_brain_name). Never raises."""
    if entities is None:
        return None
    if isinstance(entities, dict):
        return entities
    try:
        root = Path(entities)
    except TypeError:
        return None
    # A workspace_root path -> _hq/data/entities.json; a direct file path is
    # used as-is.
    if root.is_dir():
        entities_path = root / "_hq" / "data" / "entities.json"
    else:
        entities_path = root
    if not entities_path.exists():
        return None
    try:
        data = json.loads(entities_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # FS-15 — this exact fallback is the 2026-07-14 dogfood incident: a
        # sync cache served a truncated entities.json for ~90 minutes and
        # every document rendered DEFAULT_BRAND with no warning anywhere.
        # The fallback stays (a doc render must not crash on a bad read) but
        # the degradation goes ON THE RECORD so the brief / system-health
        # surface it loudly.
        record_read_alarm(entities_path, e, reader="brand")
        return None
    return data if isinstance(data, dict) else None


def _org_brand(entities: dict, org_id: Optional[str]) -> Optional[dict]:
    """The `brand` object on org `org_id`, or None. Orgs live at
    entities['entities']['orgs'] (a list); each org is {id, canonical_name, ...}."""
    if not org_id:
        return None
    ents = entities.get("entities")
    orgs = ents.get("orgs") if isinstance(ents, dict) else None
    if not isinstance(orgs, list):
        return None
    for org in orgs:
        if isinstance(org, dict) and org.get("id") == org_id:
            brand = org.get("brand")
            return brand if isinstance(brand, dict) else None
    return None


def get_brand(
    entities: Union[dict, str, "os.PathLike", None] = None,
    org_id: Optional[str] = None,
) -> dict:
    """Resolve the theme for a render.

    Resolution: DEFAULT_BRAND deep-merged with `workspace.brand` (if present),
    then deep-merged with `orgs[org_id].brand` (if present). An absent brand
    object at either level = the default is kept, byte-stably, with no warning
    and no event.

    Args:
      entities: an already-loaded entities.json dict, OR a workspace_root path
        (str / Path) to read it from, OR None. None => pure DEFAULT_BRAND (the
        module-level `BRAND = get_brand()` case in brief_writer).
      org_id: optional org id whose per-org brand overrides the workspace brand
        (for a document scoped to one client org). Absent org / absent org
        brand = workspace-level resolution only.

    Returns a fully-populated brand dict (every DEFAULT_BRAND key present). Safe
    to call at render time; never raises for a client.
    """
    ents = _load_entities(entities)
    if ents is None:
        return copy.deepcopy(DEFAULT_BRAND)

    workspace = ents.get("workspace")
    ws_brand = workspace.get("brand") if isinstance(workspace, dict) else None

    brand = _deep_merge(DEFAULT_BRAND, ws_brand if isinstance(ws_brand, dict) else None)
    brand = _deep_merge(brand, _org_brand(ents, org_id))
    return brand


# ---------------------------------------------------------------------------
# Validation (SPEC OUT1 §3b — schema is tested; this is the runtime guard)
# ---------------------------------------------------------------------------

def validate_brand(obj: object) -> list:
    """Return a list of human-readable problems with a brand object, or [] if it
    is a clean partial-or-full brand. Used by the schema/brand test and safe for
    a customization engagement to call before writing a brand into entities.json.

    Permissive by design (a brand object is a PARTIAL override — every key is
    optional), but strict on the shape of the keys that ARE present:
      - only known top-level / palette / font / eyebrow keys
      - palette values are 6-hex strings (no '#')
      - font values are non-empty strings
      - footer_line is a string; logo_path is a string or null
    """
    problems: list = []
    if not isinstance(obj, dict):
        return [f"brand must be an object, got {type(obj).__name__}"]

    for key in obj:
        if key not in _TOP_KEYS:
            problems.append(f"unknown brand key {key!r} (allowed: {sorted(_TOP_KEYS)})")

    palette = obj.get("palette")
    if palette is not None:
        if not isinstance(palette, dict):
            problems.append("palette must be an object")
        else:
            for k, v in palette.items():
                if k not in _PALETTE_KEYS:
                    problems.append(f"unknown palette key {k!r}")
                elif not (isinstance(v, str) and _HEX_RE.match(v)):
                    problems.append(f"palette.{k} must be a 6-hex color without '#', got {v!r}")

    fonts = obj.get("fonts")
    if fonts is not None:
        if not isinstance(fonts, dict):
            problems.append("fonts must be an object")
        else:
            for k, v in fonts.items():
                if k not in _FONT_KEYS:
                    problems.append(f"unknown font key {k!r}")
                elif not (isinstance(v, str) and v.strip()):
                    problems.append(f"fonts.{k} must be a non-empty font name")

    eyebrow = obj.get("eyebrow_style")
    if eyebrow is not None:
        if not isinstance(eyebrow, dict):
            problems.append("eyebrow_style must be an object")
        else:
            for k in eyebrow:
                if k not in _EYEBROW_KEYS:
                    problems.append(f"unknown eyebrow_style key {k!r}")

    if "footer_line" in obj and not isinstance(obj["footer_line"], str):
        problems.append("footer_line must be a string")
    if "logo_path" in obj and obj["logo_path"] is not None \
            and not isinstance(obj["logo_path"], str):
        problems.append("logo_path must be a string or null")
    return problems


__all__ = ["DEFAULT_BRAND", "get_brand", "validate_brand"]
