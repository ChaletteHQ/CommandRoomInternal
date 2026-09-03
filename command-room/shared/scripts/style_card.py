#!/usr/bin/env python3
"""The style card — "show my style": every knob in plain English with its
provenance, plus the regenerated view file (SPEC STYLE1 §4 step 8).

V1 IS A READ SURFACE (the show-my-reminders posture): a formatted chat block
plus `_hq/views/STYLE.md`, with the change affordances named as the chat
phrases that own them ("tune how <name> talks", "tune my documents",
"recalibrate my style"). No widget in v1 — the style verbs are not in
CANONICAL_ACTIONS and G4 forbids paper-over verb additions; a future widget
rides the apply-choices registration when the taxonomy grows deliberately.
Recorded as a spec deviation in the STYLE1 BUILD record.

PROVENANCE comes straight off the style_changed lane (DD-6): for each knob,
the latest event that touched it wins; a configured knob with no event (a
pre-lane store) reads "configured". Origin → plain English:
  inferred_provisional  → "my read of you — say the word to change it"
  inferred_confirmed    → "my read, confirmed by you"
  asked                 → "you asked"
  recalibrated          → "recalibration you confirmed"

Views are outputs, never inputs (WORKSPACE_API): STYLE.md is regenerated
wholesale on every render; nothing reads it back.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_write import atomic_write_text  # noqa: E402
from chat_persona import (  # noqa: E402
    DEFAULT_CHAT_PERSONA, get_chat_persona, is_persona_configured)
from output_profile import DEFAULT_OUTPUT_PROFILE, get_output_profile  # noqa: E402
from personification import get_brain_name  # noqa: E402

# TZDATE3 — canonical date localizer (tz.py, hoisted TZDATE2). Guarded: a
# stripped install missing tz.py keeps the pre-TZDATE3 UTC-slice behavior
# exactly (matches the render_*.py precedent).
try:
    from tz import localize_date as _localize_date  # noqa: E402
except ImportError:
    def _localize_date(ts: str | None, workspace_path: str | None = None) -> str:
        return ts[:10] if isinstance(ts, str) and ts else ""

_ORIGIN_EN = {
    "inferred_provisional": "my read of you — say the word to change it",
    "inferred_confirmed": "my read, confirmed by you",
    "asked": "you asked",
    "recalibrated": "recalibration you confirmed",
}

_PERSONA_EN = {
    "brevity": {"terse": "Short and direct — answer first, three sentences max",
                "standard": "Standard length",
                "guided": "Walked through the thinking"},
    "explanation_depth": {"answers_first": "Answers first",
                          "reasoned": "Reasoning after the answer",
                          "teaching": "Teaching as we go"},
    "formality": {"informal": "Conversational", "standard": "Standard",
                  "formal": "Boardroom register"},
    "encouragement": {"minimal": "Straight to substance",
                      "standard": "Standard",
                      "warm": "Wins acknowledged out loud"},
    "jargon_level": {"plain": "Plain words", "industry": "Your shorthand",
                     "technical": "Technical is fine"},
}

_PROFILE_EN_KEYS = ("density", "visual_bias", "default_format",
                    "format_by_kind", "page_cap", "visual_first")


def _knob_provenance(workspace_root: Path) -> Dict[Tuple[str, str], dict]:
    """(layer, knob) → latest style_changed data touching it."""
    out: Dict[Tuple[str, str], dict] = {}
    p = workspace_root / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") != "style_changed":
            continue
        data = row.get("data") or {}
        for change in data.get("changes") or []:
            knob = change.get("knob")
            if knob:
                out[(str(data.get("layer")), str(knob))] = {
                    "origin": str(data.get("origin") or ""),
                    "ts": str(row.get("ts") or "")}
    return out


def _prov_phrase(prov: Optional[dict], workspace_path=None) -> str:
    if not prov:
        return "configured"
    phrase = _ORIGIN_EN.get(prov["origin"], "configured")
    # TZDATE3 — localized via `tz.localize_date` so a knob changed
    # late-evening-local doesn't get provenance-stamped a day forward.
    day = (_localize_date(prov["ts"], workspace_path) if workspace_path
           else prov["ts"][:10])
    return f"{phrase} ({day})" if day else phrase


def render(workspace_root: Union[str, os.PathLike],
           *, write_view: bool = True) -> Dict[str, str]:
    """Build the chat block and (by default) regenerate _hq/views/STYLE.md.
    Returns {"chat_block": ..., "view_path": ...}."""
    ws = Path(workspace_root)
    brain = get_brain_name(ws)
    persona = get_chat_persona(ws)
    profile = get_output_profile(ws)
    prov = _knob_provenance(ws)

    lines = [f"## Your style — how {brain} works with you", ""]

    lines.append(f"**How {brain} talks to you**")
    if not is_persona_configured(ws):
        lines.append(f"- Product voice (nothing personalized yet) — say "
                     f"\"recalibrate my style\" and {brain} will read how you "
                     "communicate and propose a fit.")
    else:
        for knob, value in persona.items():
            if value == DEFAULT_CHAT_PERSONA.get(knob):
                continue
            if knob == "never_line":
                label = str(value)
            elif knob == "humor_ok":
                label = "Light humor welcome" if value else "No humor"
            elif knob == "language":
                label = f"Responds in '{value}'"
            else:
                label = _PERSONA_EN.get(knob, {}).get(str(value), str(value))
            lines.append(f"- {label} — "
                         f"{_prov_phrase(prov.get(('chat_persona', knob)), ws)}")

    lines.append("")
    lines.append(f"**How {brain} shapes your documents**")
    changed = [k for k in _PROFILE_EN_KEYS
               if profile.get(k) != DEFAULT_OUTPUT_PROFILE.get(k)]
    if not changed:
        lines.append("- Standard document style — say \"tune my documents\" "
                     "to adjust density, visuals, or format.")
    else:
        for knob in changed:
            lines.append(f"- {knob.replace('_', ' ')}: {profile[knob]} — "
                         f"{_prov_phrase(prov.get(('output_profile', knob)), ws)}")

    lines += [
        "",
        "_Change any of it: \"tune how " + brain + " talks\" · \"tune my "
        "documents\" · \"recalibrate my style\" (re-reads your last 60 days "
        "and proposes only what changed) · \"undo\" reverses the last "
        "change._",
    ]
    chat_block = "\n".join(lines)

    view_path = ws / "_hq" / "views" / "STYLE.md"
    if write_view:
        view_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(view_path, chat_block + "\n")
    return {"chat_block": chat_block, "view_path": str(view_path)}


__all__ = ["render"]
