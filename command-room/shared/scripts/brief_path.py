#!/usr/bin/env python3
"""
Centralized brief save path — single source of truth for where meeting briefs go.

Per shared/CONTRACT.md Rule 3: all meeting briefs save to `_hq/meetings/`. The
prior `[Project]/meetings/` (v2.10.8 - v2.12.5) didn't always resolve in
Cowork's sandbox; users hit "folder cannot be found" on click.

Every orchestrator that produces a brief MUST import `get_brief_path` and use
its return value. Do not hand-roll paths in orchestrator prompts.

Used by:
  - cr-past-meetings (Past_Meeting_*.docx)
  - cr-upcoming-meetings (Call_Prep_*.docx)
  - meeting-notes on-demand (Past_Meeting_*.docx via the same path)
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Literal


BriefType = Literal["past_meeting", "call_prep", "weekly_recap"]


def _slugify(text: str) -> str:
    """Lowercase, hyphen-separated, alphanum-only slug. Preserve readability —
    `Sam - Aria` becomes `sam-aria`, not `sam%20-%20aria` or similar URL-encoded mush."""
    if not text:
        return "untitled"
    s = text.lower().strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]+", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


def get_brief_filename(brief_type: BriefType, slug: str, date_iso: str) -> str:
    """Filename only (no directory). Date format YYYY-MM-DD.

    >>> get_brief_filename("past_meeting", "Sam UX review", "2026-04-30")
    'Past_Meeting_sam-ux-review_2026-04-30.docx'
    >>> get_brief_filename("call_prep", "Q2 deck", "2026-05-12")
    'Call_Prep_q2-deck_2026-05-12.docx'
    """
    if brief_type == "past_meeting":
        prefix = "Past_Meeting"
    elif brief_type == "call_prep":
        prefix = "Call_Prep"
    elif brief_type == "weekly_recap":
        # Weekly recaps are per-date, not per-thing. Slug is ignored; the date IS
        # the unique identifier. Filename: `Weekly_Recap_<YYYY-MM-DD>.docx`.
        # Added 2026-05-17 as part of the new `weekly-recap` skill (onboarding-v2).
        return f"Weekly_Recap_{date_iso}.docx"
    else:
        raise ValueError(f"Unknown brief_type: {brief_type!r}")
    return f"{prefix}_{_slugify(slug)}_{date_iso}.docx"


def get_brief_path(workspace_root: str, brief_type: BriefType, slug: str, date_iso: str) -> str:
    """Absolute path for a brief file.

    `workspace_root` is the user's Command Room workspace folder — whatever
    they mounted in Cowork, wherever it lives on their machine. Inside Cowork's
    sandbox it's typically under `/sessions/<id>/mnt/<basename>/`. The
    orchestrator resolves this from environment / mount-point detection at
    fire time per `shared/CONTRACT.md` Rule 22 (find `_hq/` under `mnt/`).
    NEVER substitute a literal path from these docstring examples — always
    pass the runtime-discovered value.

    Returns: forward-slash-normalized absolute path under
    `<workspace_root>/_hq/meetings/<filename>`. Always uses `/` separators for
    consistency across Windows/macOS/Linux + Cowork's sandbox.

    >>> get_brief_path("/workspace", "past_meeting", "sam", "2026-04-30")
    '/workspace/_hq/meetings/Past_Meeting_sam_2026-04-30.docx'
    """
    if not workspace_root:
        raise ValueError("workspace_root is required")
    fn = get_brief_filename(brief_type, slug, date_iso)
    # Normalize separators to forward-slash; absolute path joining uses `/`
    root_normalized = workspace_root.replace("\\", "/").rstrip("/")
    return f"{root_normalized}/_hq/meetings/{fn}"


def get_brief_artifact_url(absolute_path: str) -> str:
    """Convert an absolute brief path to a `computer://` URL for `artifact_link.url`.

    v3.13.0+ — Windows-form fix per the 2026-05-20 brief-link handoff:

      - **Windows absolute paths** (drive letter like `C:`): emit the literal
        native form — `computer://C:\\Users\\Sample\\Desktop\\Claude\\Command
        Room\\...\\file.docx`. TWO slashes (not three), backslashes preserved,
        spaces UNENCODED. This is the form Cowork's Windows resolver opens
        reliably (verified by M's testing on 2026-05-20). The pre-v3.13.0
        emission (`computer:///C:/.../Command%20Room/...` — three slashes,
        forward slashes, `%20`) failed silently on every space-containing
        workspace folder, including M's own `Command Room`. The doctests
        below now include a space-in-path case to lock the fix.

      - **POSIX absolute paths** (start with `/`): keep the existing
        `computer:///` + URL-encoded form. That was already working on
        macOS/Linux per the v2.14.1 fix; only the Windows-with-space case
        was broken.

    The path is opaque to the user (visible only in the href, not as label
    text — Rule 4 forbids visible paths in chat output).

    Background on why three different formats existed before v3.13.0:

      - `_hq/CONVENTIONS_SOURCE_LINKS.md` documented `computer:///C%3A%5C...`
        (encoded backslashes + colon) as the workspace convention.
      - Cowork's host examples documented `computer://C:\\...\\Command Room/
        file.docx` (native, no encoding) as the desktop-app-accepted form.
      - This helper emitted `computer:///C:/.../%20.../file` (three slashes,
        forward slashes, encoded space) — a third format matching neither.

    M's 2026-05-20 live testing settled the format question: the native form
    is what Cowork's Windows resolver actually opens. v3.13.0 aligns this
    helper with that form. `_hq/CONVENTIONS_SOURCE_LINKS.md` was updated to
    match in the same release.

    >>> get_brief_artifact_url("/workspace/_hq/meetings/Past_Meeting_x_2026-04-30.docx")
    'computer:///workspace/_hq/meetings/Past_Meeting_x_2026-04-30.docx'
    >>> get_brief_artifact_url("C:/Users/Sample/CommandRoom/_hq/meetings/x.docx")
    'computer://C:\\\\Users\\\\Sample\\\\CommandRoom\\\\_hq\\\\meetings\\\\x.docx'
    >>> get_brief_artifact_url("C:\\\\Users\\\\Sample\\\\Desktop\\\\Claude\\\\Command Room\\\\_hq\\\\meetings\\\\x.docx")
    'computer://C:\\\\Users\\\\Sample\\\\Desktop\\\\Claude\\\\Command Room\\\\_hq\\\\meetings\\\\x.docx'
    >>> get_brief_artifact_url("C:/Users/Sample/Desktop/Claude/Command Room/_hq/meetings/x.docx")
    'computer://C:\\\\Users\\\\Sample\\\\Desktop\\\\Claude\\\\Command Room\\\\_hq\\\\meetings\\\\x.docx'
    """
    if not absolute_path:
        return ""

    # Detect Windows-style absolute path: drive letter like "C:" at the start
    # (after any leading slashes). Normalize separators to forward-slash for
    # the detection only — we re-emit native backslashes for the URL.
    detect = absolute_path.replace("\\", "/").lstrip("/")
    is_windows = (
        len(detect) >= 2
        and detect[1] == ":"
        and detect[0].isalpha()
    )

    if is_windows:
        # v3.13.0+ native form. Cowork's Windows resolver opens this; URL-encoded
        # variants (%20 for space, %3A for colon, %5C for backslash) do NOT.
        # Emit: computer://C:\path\to\file.ext (TWO slashes, backslashes, unencoded space)
        # Re-emit with backslashes regardless of the input's separator style.
        native_path = detect.replace("/", "\\")
        return "computer://" + native_path

    # POSIX-style absolute path. Keep the existing computer:/// + URL-encoded
    # form — that was already working for non-Windows users (v2.14.1 fix).
    body = absolute_path.lstrip("/")
    segments = body.split("/")
    encoded_segments = [
        urllib.parse.quote(seg, safe=":") for seg in segments
    ]
    return "computer:///" + "/".join(encoded_segments)


def is_session_scoped_path(absolute_path: str) -> bool:
    """True when the path lives inside Cowork's per-session sandbox
    (`/sessions/<id>/...`) — a path that exists only while THAT session is
    alive and has no host-native equivalent the customer's machine can open.

    v5.9.2 — the QMG field reports (2026-07-28 "Past Meeting Briefs failed to
    load", 2026-07-31 "Failed report"): a workspace mounted from Google Drive
    resolves to a session-scoped root, so every `computer://` brief link we
    emitted pointed at a path the customer's machine could never open —
    "Failed to load local file." on click, even though the .docx saved fine
    and was sitting in their Drive. This predicate is how an orchestrator
    detects that shape BEFORE emitting a link that cannot work.

    A host-native root (Windows `C:\\...`, or a real local folder mount that
    resolves outside the sandbox) returns False — `computer://` stays the
    opener there, unchanged.

    >>> is_session_scoped_path("/sessions/abc123/mnt/Command Room/_hq/meetings/x.docx")
    True
    >>> is_session_scoped_path("C:/Users/Sample/Command Room/_hq/meetings/x.docx")
    False
    >>> is_session_scoped_path("/Users/sample/Command Room/_hq/meetings/x.docx")
    False
    >>> is_session_scoped_path("")
    False
    """
    if not absolute_path:
        return False
    normalized = absolute_path.replace("\\", "/")
    return normalized.startswith("/sessions/")


def get_brief_opener_url(absolute_path: str, drive_web_url: str = "") -> str:
    """The URL the deliverable link should actually open — Drive-aware (v5.9.2).

    `computer://` links only work when the path exists on the customer's own
    machine. For a workspace mounted from Google Drive / OneDrive, the resolved
    absolute path is session-scoped (see `is_session_scoped_path`) — there is
    no local file to open, so the `computer://` form is a guaranteed
    "Failed to load local file." Field-reported twice by QMG (2026-07-28,
    2026-07-31) before this helper existed.

    Resolution order:
      1. Session-scoped path + a `drive_web_url` supplied → the Drive web URL
         (the file IS in their Drive; the browser opens it everywhere).
      2. Anything else → `get_brief_artifact_url(absolute_path)`, unchanged —
         host-native paths keep the `computer://` opener that M's 2026-05-20
         testing validated.

    Orchestrators on a session-scoped workspace root MUST attempt the Drive
    web-link lookup (search `_hq/meetings/<filename>` via the discovered
    drive tool per `tool_discovery.discover_drive_tool()`) and pass it here.
    An empty `drive_web_url` falls back to the `computer://` form rather than
    dropping the link — a maybe-dead link with a working file card beside it
    beats no link at all.

    >>> get_brief_opener_url("/sessions/abc/mnt/CR/_hq/meetings/x.docx", "https://docs.google.com/document/d/f1/view")
    'https://docs.google.com/document/d/f1/view'
    >>> get_brief_opener_url("C:/Users/Sample/CR/_hq/meetings/x.docx", "https://docs.google.com/document/d/f1/view")
    'computer://C:\\\\Users\\\\Sample\\\\CR\\\\_hq\\\\meetings\\\\x.docx'
    >>> get_brief_opener_url("/sessions/abc/mnt/CR/_hq/meetings/x.docx")
    'computer:///sessions/abc/mnt/CR/_hq/meetings/x.docx'
    """
    if drive_web_url and is_session_scoped_path(absolute_path):
        return drive_web_url
    return get_brief_artifact_url(absolute_path)


def ensure_brief_directory(workspace_root: str) -> str:
    """Create `_hq/meetings/` if missing. Return the absolute directory path.

    Orchestrators call this in Phase 4 (or equivalent setup) before saving briefs.
    Idempotent: safe to call every fire.
    """
    if not workspace_root:
        raise ValueError("workspace_root is required")
    dir_path = os.path.join(workspace_root, "_hq", "meetings")
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


__all__ = [
    "BriefType",
    "get_brief_filename",
    "get_brief_path",
    "get_brief_artifact_url",
    "get_brief_opener_url",
    "is_session_scoped_path",
    "ensure_brief_directory",
]


if __name__ == "__main__":
    # Smoke tests
    print(get_brief_filename("past_meeting", "Sam UX review", "2026-04-30"))
    print(get_brief_filename("call_prep", "Q2 deck", "2026-05-12"))
    print(get_brief_path("/workspace", "past_meeting", "sam", "2026-04-30"))
    print(get_brief_artifact_url(
        "/c/Users/Sample/CommandRoom/_hq/meetings/Past_Meeting_x_2026-04-30.docx"
    ))
