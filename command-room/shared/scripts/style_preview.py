#!/usr/bin/env python3
"""Style previews — show the client what a proposed style CHANGES, as
outputs, not knob names (SPEC STYLE1 §4 step 5, DD-4).

TWO PREVIEWS, TWO HONESTY LEVELS
--------------------------------
1. Document preview (`build_document_preview`): resolves the SAME chokepoint
   parameters `make_brief` consumes — format-by-kind, density, visual bias,
   page cap — under the current vs the proposed profile, via the REAL
   `output_profile` resolution functions (preview-is-behavior at the
   parameter level; pinned in run_style1_test.py). The sample paragraph pair
   is an illustrative rendering of the density difference, clearly labeled
   SAMPLE — the .docx bytes themselves are not diffed (timestamps make that
   meaningless), the parameters that shape them are.
2. Chat preview (`build_chat_preview`): the same canned answer composed under
   two personas, deterministically, labeled SAMPLE. It illustrates the knobs;
   it does not claim to be model output.

Placeholder names only (PRIVACY_POLICY roster) — this module's strings reach
client screens and test fixtures. No writes anywhere; pure functions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union
import os
import sys as _sys

_sys.path.insert(0, str(Path(__file__).resolve().parent))
from chat_persona import DEFAULT_CHAT_PERSONA  # noqa: E402
from output_profile import (  # noqa: E402
    DEFAULT_OUTPUT_PROFILE, get_output_profile, resolve_format_for_kind)

_PREVIEW_KINDS = ("call_prep", "one_pager", "board_pack")

_SAMPLE_TIGHT = (
    "Acme Co renewal: recommend holding price. Two open commitments "
    "(quote revision, Thursday walkthrough). Ask: approve the revised terms."
)
_SAMPLE_NARRATIVE = (
    "On the Acme Co renewal, the recommendation is to hold price. The "
    "relationship carries two open commitments — the quote revision Bo "
    "Sample owes back, and the Thursday walkthrough — and both land before "
    "the renewal date, which is what makes holding tenable. The ask on this "
    "page: approve the revised terms so the walkthrough can confirm them."
)


def _overlay_profile(base: Dict[str, Any],
                     proposed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (proposed or {}).items():
        if k in DEFAULT_OUTPUT_PROFILE:
            out[k] = v
    return out


def build_document_preview(
        workspace_root: Union[str, os.PathLike, None],
        proposed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolved rendering parameters under current vs proposed, plus a
    labeled sample paragraph pair when density changes.

    Preview-is-behavior: BOTH sides resolve through the real chokepoints —
    each profile is written into its own throwaway temp workspace and
    `get_output_profile` / `resolve_format_for_kind` run against it, so the
    preview can never drift from what `make_brief` would actually do. The
    client's workspace is never written."""
    import json as _json
    import tempfile as _tempfile

    current = get_output_profile(workspace_root)
    prop = _overlay_profile(current, proposed)

    def _facts(profile: Dict[str, Any]) -> Dict[str, Any]:
        with _tempfile.TemporaryDirectory() as td:
            tws = Path(td)
            cfg = tws / "_hq" / "data" / "skill_config"
            cfg.mkdir(parents=True)
            (cfg / "output_profile.json").write_text(
                _json.dumps({"config": profile}), encoding="utf-8")
            resolved = get_output_profile(tws)
            return {
                "density": resolved["density"],
                "visual_bias": resolved["visual_bias"],
                "default_format": resolved["default_format"],
                "format_by_kind": {
                    k: resolve_format_for_kind(k, tws)
                    for k in _PREVIEW_KINDS},
                "page_cap": dict(resolved.get("page_cap") or {}),
            }

    out: Dict[str, Any] = {
        "current": _facts(current),
        "proposed": _facts(prop),
        "changed": sorted(k for k in DEFAULT_OUTPUT_PROFILE
                          if current.get(k) != prop.get(k)),
    }
    if current["density"] != prop["density"]:
        out["sample"] = {
            "label": "SAMPLE — the same brief section under each setting",
            "current": (_SAMPLE_TIGHT if current["density"] == "tight"
                        else _SAMPLE_NARRATIVE),
            "proposed": (_SAMPLE_TIGHT if prop["density"] == "tight"
                         else _SAMPLE_NARRATIVE),
        }
    return out


_SAMPLE_QUESTION = "What's on my plate today?"


def build_chat_preview(current: Optional[Dict[str, Any]] = None,
                       proposed: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, str]:
    """The same canned answer composed under two personas. Deterministic,
    labeled SAMPLE. Composition rules mirror chat_persona._knob_line's
    vocabulary so the sample demonstrates exactly the knobs on offer."""
    def _compose(p: Dict[str, Any]) -> str:
        persona = dict(DEFAULT_CHAT_PERSONA)
        persona.update({k: v for k, v in (p or {}).items()
                        if k in DEFAULT_CHAT_PERSONA})
        greeting = "" if persona["encouragement"] == "minimal" else (
            "Good morning — nice close on the Acme Co quote yesterday. "
            if persona["encouragement"] == "warm" else "Morning. ")
        if persona["formality"] == "formal":
            greeting = "" if not greeting else "Good morning. "
        core = ("Three things: the Acme Co renewal call at 10, the quote "
                "revision Bo Sample owes back, and two overdue follow-ups.")
        why = ("" if persona["explanation_depth"] == "answers_first" else
               " The renewal leads because both open commitments land "
               "before the date — that's the leverage point.")
        walk = ("" if persona["brevity"] != "guided" else
                " Here's how I'd sequence it: take the call first, then "
                "chase the revision while it's fresh, and the follow-ups "
                "fit in the afternoon gap.")
        tail = ("" if persona["brevity"] == "terse" else
                (" Want the prep doc?" if persona["formality"] != "formal"
                 else " The preparation document is ready when you are."))
        return (greeting + core + why + walk + tail).strip()

    return {
        "label": f"SAMPLE — {_SAMPLE_QUESTION!r} answered under each style",
        "current": _compose(current or {}),
        "proposed": _compose(proposed or {}),
    }


__all__ = ["build_document_preview", "build_chat_preview"]
