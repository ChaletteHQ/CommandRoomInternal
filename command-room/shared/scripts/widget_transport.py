#!/usr/bin/env python3
"""
File-URI transport helper for chat widgets (v3.13.8+ — Bug #32).

WHY THIS EXISTS
---------------

Pre-v3.13.8, every widget surface went through:

    html = render_chat_output_widget(data)        # canonical, validated
    mcp__visualize__show_widget(content=html)     # 40-80KB byte-relay

The byte-relay step pushed the entire validated HTML through a tool
parameter. Agents observed this cost (large parameter payloads) and
rationally chose freelance render paths for many surfaces (email-writer,
intro-broker, follow-up-ritual, thread-resurrection, calendar-writer),
bypassing the validators (canonical actions, data shape, leak scanner) at
the final transport step.

Root cause was structural, not contractual: as long as canonical = byte-
relay, freelance = direct, agents will keep picking freelance.

THE FIX
-------

`render_and_persist()` renders via the canonical path (so all validators
fire), writes the validated HTML to a workspace-local file, and returns
both the HTML string AND a file:// URI. The caller can hand the URI to
`mcp__visualize__show_widget` instead of the full bytes — same end-user
result, but the canonical path is no longer the expensive path.

USAGE
=====

From any widget orchestrator:

    from widget_transport import render_and_persist
    transport = render_and_persist(
        data_view=data,
        wrapper="fragment",
        persist_dir=workspace_root / "_hq" / ".system" / "widgets",
    )
    # transport["html"]      — the validated HTML
    # transport["file_uri"]  — file:// URI suitable for show_widget
    # transport["path"]      — disk path (Path object)
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Optional


_BOM = "﻿"


def _atomic_write_widget_html(path: Path, html: str, wrapper: str) -> None:
    """Atomic write of validated widget HTML.

    Handles two cases:
      - wrapper="fragment": persist the fragment as-is. When the file is
        opened standalone in a browser the user will get a working render
        because the fragment includes <style> + content + <script>; for
        proper UTF-8 handling we prepend a BOM so the browser doesn't have
        to guess (Bug #40 — mojibake on standalone open).
      - wrapper="document": already has <!DOCTYPE> + <head><meta charset>;
        write as-is.

    Per §3.4 of the v3.13.8 plan, do NOT inject <meta charset> into a
    fragment that may later be sent to show_widget — that violates the
    contract. The BOM is invisible to show_widget's parser but tells
    standalone browsers to use UTF-8.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if wrapper == "fragment":
        content = _BOM + html
    else:
        content = html
    # Atomic write via temp + rename
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8", newline="")
    os.replace(str(tmp), str(path))


_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_filename(stem: str) -> str:
    s = _SAFE_NAME_RE.sub("-", stem).strip("-")
    return s or "widget"


def render_and_persist(
    *,
    data_view: dict,
    wrapper: str = "fragment",
    persist_dir: str | Path,
    name_hint: Optional[str] = None,
) -> dict:
    """Canonical render-and-persist transport.

    Renders the widget via `chat_output_renderer.render_chat_output_widget`
    (all validators fire), writes the validated HTML to a file inside
    `persist_dir`, and returns a transport dict.

    Args:
      data_view: same data shape that render_chat_output_widget expects.
      wrapper: "fragment" (default, for show_widget) or "document"
        (standalone HTML file).
      persist_dir: directory in which to write the validated HTML. Typically
        `<workspace>/_hq/.system/widgets/` so widgets are kept out of the
        regular workspace tree.
      name_hint: optional prefix for the on-disk filename (otherwise we
        derive one from the data_view's surface tag).

    Returns:
      {
        "html":     the validated HTML string,
        "file_uri": "file:///abs/path/to/widget.html",
        "path":     Path object for the persisted file,
      }

    Raises:
      Whatever `render_chat_output_widget` raises (CanonicalActionError,
      DataShapeError, LeakDetectedError, WrapperContractError).
    """
    # Import here to avoid a circular import at module load time
    from chat_output_renderer import render_chat_output_widget

    html = render_chat_output_widget(data_view, wrapper=wrapper)

    persist_dir = Path(persist_dir)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    surface = name_hint or data_view.get("surface") or "widget"
    filename = f"{_safe_filename(surface)}_{ts}.html"
    out_path = persist_dir / filename

    _atomic_write_widget_html(out_path, html, wrapper=wrapper)

    # file:// URI — absolute path, forward slashes, three slashes after file:
    file_uri = "file:///" + str(out_path.resolve()).replace(os.sep, "/").lstrip("/")

    return {
        "html": html,
        "file_uri": file_uri,
        "path": out_path,
    }


__all__ = ["render_and_persist"]
