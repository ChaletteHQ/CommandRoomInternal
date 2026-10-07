#!/usr/bin/env python3
"""`export my profile` - the profile page as a document.

SPEC_SURFACES2 PROFILE1.

WHY THIS IS ITS OWN FILE
------------------------
The page composer does not need the document writer, and it must not import
it. `profile.py` is imported by the coaching doors, by the workspace-
instruction renderer and by the trust map; the moment it also reached
`brief_writer`, every module importing IT counted as a document renderer to
the runtime render-family guard, which derives its population from the import
graph rather than from a list. That swept in `coaching_doors` and
`render_claude_md` - neither of which produces a document - and, because the
composer's module name is an ordinary English word, it turned every skill
file that used that word into a candidate renderer. One import edge, fifteen
false positives.

So the render lives here, on its own, and the import graph says what is
actually true: one module writes the document, and it is this one.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import profile as _profile  # noqa: E402


def export_profile(workspace_root, output_path: Optional[str] = None) -> str:
    """The page as a document, through `brief_writer` like every other
    on-demand render. Returns the written path."""
    ws = Path(workspace_root)
    rendered = _profile.render(ws, write_view=False)
    if output_path is None:
        out_dir = ws / "_hq" / "profile"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir /
                          f"Your_Profile_{_dt.date.today().isoformat()}.docx")
    try:
        from personification import get_brain_name
        brain = get_brain_name(ws)
    except Exception:
        brain = "Command Room"
    sections = []
    for key, title in _profile.SECTIONS:
        rows = rendered["sections"].get(key) or []
        if rows:
            sections.append({"heading": title,
                             "bullets": [f"{r['text']} - {r['provenance']}"
                                         for r in rows]})
        else:
            sections.append({"heading": title,
                             "body": _profile.HONESTY_LINES[key]})
    from brief_writer import make_brief
    return make_brief(
        output_path,
        brief_kind="profile",
        title="Your profile",
        subtitle=f"What {brain} knows about you, and where each line came from",
        sections=sections,
        workspace_root=str(ws),
        contract="report",
    )


__all__ = ["export_profile"]
