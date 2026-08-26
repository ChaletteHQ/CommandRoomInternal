#!/usr/bin/env python3
"""Per-client chat persona — how the assistant SPEAKS to this client
(SPEC STYLE1 §4 step 1).

WHY THIS EXISTS
---------------
PERSONIFICATION.md gives every workspace the same global voice. STYLE1 adds a
per-client layer: a small closed set of knobs (brevity, explanation depth,
formality, ...) inferred from the client's own record, confirmed by the client,
and rendered as a LIVE-STATE block into the CLAUDE.md hot cache — the one
context surface the Cowork runtime provably loads every session. This module is
the READ path plus the deterministic renderer for that block. It never writes;
writes go through skill_config_writer.save_skill_config(ws, "chat_persona", …)
so every change is atomic, schema-validated, and evented.

DEFAULTS-FIRST, DORMANT (the output_profile.py posture, applied again)
---------------------------------------------------------------------
The overwhelming common case is a workspace with NO persona configured. For
that case get_chat_persona() returns DEFAULT_CHAT_PERSONA byte-stably — no
warning, no event, no file — and persona_lines() returns [] so the CLAUDE.md
persona block is never created. An absent store is indistinguishable from the
pre-STYLE1 world (AC-1 dormancy).

BOUNDS (SPEC STYLE1 D5 — hard fence)
------------------------------------
The persona adjusts TONE only. Every knob is a closed enum; the one free-text
knob (never_line) must start with "Never " and stay short, and the renderer
refuses any line that smells like an attempt to disable output machinery
(exec header, ASK block, leak scan, receipts, widgets). Style can shorten how
Bob talks; it can never strip what the contracts guarantee.

CONFIRM DISCIPLINE (D4/D7)
--------------------------
This module has no opinion about WHEN values change — that lives with the
callers: command-room-onboarding may write the initial set with
origin="inferred_provisional" (the ONLY unconfirmed write, D7), and
workspace-manager writes tune/recalibrate results after an explicit confirm.
Every write is paired with a style_changed event built by
build_style_changed_event() below and appended via event_gate.append_event.

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# Knob vocabulary (SPEC STYLE1 DD-5 — closed enums, defaults = today's voice)
# ---------------------------------------------------------------------------

DEFAULT_CHAT_PERSONA: Dict[str, Any] = {
    "brevity": "standard",            # terse | standard | guided
    "explanation_depth": "answers_first",  # answers_first | reasoned | teaching
    "formality": "standard",          # informal | standard | formal
    "encouragement": "standard",      # minimal | standard | warm
    "humor_ok": False,                # bool
    "jargon_level": "plain",          # plain | industry | technical
    "language": "en",                 # BCP47-ish short tag; "en" = today
    "never_line": "",                 # optional single concrete negative rule
}

_ENUMS: Dict[str, frozenset] = {
    "brevity": frozenset({"terse", "standard", "guided"}),
    "explanation_depth": frozenset({"answers_first", "reasoned", "teaching"}),
    "formality": frozenset({"informal", "standard", "formal"}),
    "encouragement": frozenset({"minimal", "standard", "warm"}),
    "jargon_level": frozenset({"plain", "industry", "technical"}),
}

# never_line hygiene: must be a single short "Never …" sentence, and must not
# read as an attempt to switch off output machinery (D5 bounds fence).
_NEVER_LINE_MAX = 90
_NEVER_LINE_RE = re.compile(r"^Never\s+\S", re.IGNORECASE)
# REVIEW_STYLE1 F-3 — [\s-]+ not \s+: "exec-header" / "ASK-block" /
# "leak-scan" are the same bounds violation in a hyphenated coat, and a
# fence a hyphen walks through is prose, not a fence.
_BOUNDS_BANNED = re.compile(
    r"exec(utive)?[\s-]+header|ask[\s-]+block|leak[\s-]*scan|receipt|widget|"
    r"personal[\s-]+lane|scrub|guard|contract",
    re.IGNORECASE)

_LANGUAGE_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,8})?$")

CONFIG_SUBPATH = ("_hq", "data", "skill_config", "chat_persona.json")

# Persona block line budget (SPEC STYLE1 D3 ruling): heading + at most 7
# rendered lines. Priority order decides what survives if a workspace somehow
# configures everything at once.
PERSONA_LINE_BUDGET = 8
_PRIORITY = ("brevity", "explanation_depth", "formality", "never_line",
             "encouragement", "jargon_level", "humor_ok", "language")


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

def _store_path(workspace_root: Union[str, os.PathLike]) -> Path:
    return Path(workspace_root).joinpath(*CONFIG_SUBPATH)


def _load_saved(workspace_root: Union[str, os.PathLike, None]) -> Optional[dict]:
    """Tolerates both the skill_config_writer wrapper ({"config": {...}}) and a
    raw dict; missing/malformed ⇒ None. Mirrors output_profile._load_saved."""
    if workspace_root is None:
        return None
    p = _store_path(workspace_root)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(raw, dict) and isinstance(raw.get("config"), dict):
        return raw["config"]
    return raw if isinstance(raw, dict) else None


def _valid_value(knob: str, value: Any) -> bool:
    if knob in _ENUMS:
        return isinstance(value, str) and value in _ENUMS[knob]
    if knob == "humor_ok":
        return isinstance(value, bool)
    if knob == "language":
        return isinstance(value, str) and bool(_LANGUAGE_RE.match(value))
    if knob == "never_line":
        return (isinstance(value, str) and value == "") or (
            isinstance(value, str)
            and len(value) <= _NEVER_LINE_MAX
            and bool(_NEVER_LINE_RE.match(value))
            and not _BOUNDS_BANNED.search(value))
    return False


def get_chat_persona(
        workspace_root: Union[str, os.PathLike, None] = None) -> Dict[str, Any]:
    """Resolved persona: DEFAULT_CHAT_PERSONA overlaid with known, VALID saved
    entries only. An invalid value silently keeps the default — a typo can
    never reshape how the assistant talks. Returns a fresh fully-populated
    dict. Never raises."""
    out = dict(DEFAULT_CHAT_PERSONA)
    saved = _load_saved(workspace_root)
    if not saved:
        return out
    for knob, value in saved.items():
        if knob in out and _valid_value(knob, value):
            out[knob] = value
    return out


def is_persona_configured(
        workspace_root: Union[str, os.PathLike, None]) -> bool:
    """True only when a store exists AND carries at least one known knob.
    Dormancy gate for the CLAUDE.md block: unconfigured workspaces must stay
    byte-identical to pre-STYLE1 (AC-1)."""
    saved = _load_saved(workspace_root)
    return bool(saved) and any(k in DEFAULT_CHAT_PERSONA for k in saved)


def validate_chat_persona(obj: object) -> List[str]:
    """Problem list for a candidate persona dict (empty = clean). Unknown keys
    are reported (skill_config_writer will also reject them loudly)."""
    problems: List[str] = []
    if not isinstance(obj, dict):
        return ["persona must be a dict"]
    for knob, value in obj.items():
        if knob not in DEFAULT_CHAT_PERSONA:
            problems.append(f"unknown knob: {knob}")
        elif not _valid_value(knob, value):
            problems.append(f"invalid value for {knob}: {value!r}")
    return problems


# ---------------------------------------------------------------------------
# Renderer — the CLAUDE.md persona block body
# ---------------------------------------------------------------------------

def _knob_line(knob: str, value: Any, first_name: str) -> Optional[str]:
    """Concrete, verifiable phrasing (STYLE1 research addendum: specificity
    beats adjectives). Default values render nothing — the block carries only
    what differs from the product voice."""
    if value == DEFAULT_CHAT_PERSONA.get(knob):
        return None
    who = first_name or "the user"
    if knob == "brevity":
        return {"terse": ("Keep chat answers short — lead with the answer; "
                          "three sentences max before any list or table."),
                "guided": (f"Walk {who} through the thinking — show the why "
                           "before the recommendation.")}.get(value)
    if knob == "explanation_depth":
        return {"reasoned": ("Give the reasoning in one tight paragraph after "
                             "the answer, not before it."),
                "teaching": (f"Explain unfamiliar mechanics as you go — "
                             f"{who} wants to learn the machinery, not just "
                             "the output.")}.get(value)
    if knob == "formality":
        return {"formal": ("Keep a boardroom register — no slang, no "
                           "exclamation points."),
                "informal": ("Keep it conversational — contractions and plain "
                             "talk, like a trusted colleague.")}.get(value)
    if knob == "encouragement":
        return {"warm": "Acknowledge wins out loud before moving on.",
                "minimal": ("Skip pleasantries and encouragement — straight "
                            "to substance.")}.get(value)
    if knob == "humor_ok":
        return "Light humor is welcome when the moment allows." if value else None
    if knob == "jargon_level":
        return {"industry": (f"Use {who}'s own industry shorthand without "
                             "defining it."),
                "technical": ("Technical vocabulary is fine — no need to "
                              "simplify.")}.get(value)
    if knob == "language":
        return (f"Respond in the '{value}' language unless {who} writes to "
                "you in another.")
    if knob == "never_line":
        return str(value) if value else None
    return None


def persona_lines(
        workspace_root: Union[str, os.PathLike, None] = None,
        *, brain_name: str = "", first_name: str = "") -> List[str]:
    """The persona block body for CLAUDE.md, ≤ PERSONA_LINE_BUDGET lines
    including the heading. [] when the workspace has no configured persona —
    the caller must then render NO block at all (dormancy)."""
    if not is_persona_configured(workspace_root):
        return []
    persona = get_chat_persona(workspace_root)
    body: List[str] = []
    for knob in _PRIORITY:
        line = _knob_line(knob, persona.get(knob), first_name)
        if line:
            body.append(f"- {line}")
        if len(body) >= PERSONA_LINE_BUDGET - 1:
            break
    if not body:
        # Configured store, all values = defaults: an explicit "product voice"
        # confirmation still renders nothing — defaults are the absence of a
        # block, not a block that says "defaults".
        return []
    speaker = brain_name or "the assistant"
    head = f"## How {speaker} talks to {first_name}" if first_name else (
        f"## How {speaker} talks to you")
    return [head] + body


# ---------------------------------------------------------------------------
# Event builder (construction-only — caller appends via event_gate)
# ---------------------------------------------------------------------------

def build_style_changed_event(
        *, layer: str, origin: str, changes: List[Dict[str, Any]],
        source_skill: str, evidence: str = "") -> Dict[str, Any]:
    """One style_changed event per confirmed batch per layer (EVENT_TYPES.md
    style lane). layer ∈ {chat_persona, output_profile}; origin ∈
    {inferred_provisional, inferred_confirmed, asked, recalibrated}.
    changes = [{"knob", "from", "to"}, ...]. Construction only — no seq, no
    ts; event_gate.append_event stamps those inside the writer lock.
    (A proposal_ref field was deliberately NOT added: D4 ruled the proposal/
    drift machinery off, so it would be a written field with no reader —
    the G29 dead-field class.)"""
    if layer not in ("chat_persona", "output_profile"):
        raise ValueError(f"unknown style layer: {layer}")
    if origin not in ("inferred_provisional", "inferred_confirmed",
                      "asked", "recalibrated"):
        raise ValueError(f"unknown style origin: {origin}")
    if not changes:
        raise ValueError("style_changed with no changes is not an event")
    data: Dict[str, Any] = {
        "layer": layer,
        "origin": origin,
        "changes": [
            {"knob": str(c.get("knob")),
             "from": c.get("from"),
             "to": c.get("to")} for c in changes],
    }
    if evidence:
        data["evidence"] = evidence
    return {"type": "style_changed", "source_skill": source_skill,
            "data": data}


__all__ = [
    "DEFAULT_CHAT_PERSONA",
    "PERSONA_LINE_BUDGET",
    "get_chat_persona",
    "is_persona_configured",
    "validate_chat_persona",
    "persona_lines",
    "build_style_changed_event",
]
