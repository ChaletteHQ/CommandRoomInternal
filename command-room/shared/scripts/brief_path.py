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
    >>> get_brief_artifact_url("/mnt/user-data/outputs/Memo_sample_2026-01-02.docx")
    ''
    """
    if not absolute_path:
        return ""

    # DELIV1 — a container scratch path is not a file on anybody's computer.
    # The fence sits at the MINT rather than at one caller, so no surface can
    # hand a customer a link into a session that ended when the turn did.
    if is_container_scratch_path(absolute_path):
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


#: The container's outbound scratch (breakdown §3.4). A document is BUILT here
#: and then landed; the scratch path exists in one process, on no machine the
#: customer owns, and is therefore never an opener URL.
CONTAINER_SCRATCH_PREFIX = "/mnt/user-data/"


def is_container_scratch_path(absolute_path: str) -> bool:
    """True when the path is a container-local scratch path.

    `/mnt/user-data/outputs/...` is where a merged-seat render happens and
    `/mnt/user-data/uploads/...` is where a staged copy of a device file lands.
    Neither exists on the customer's computer, so neither may ever become a
    link — not a `computer://` one (there is no such local file) and not a web
    one (there is no such web file either).

    >>> is_container_scratch_path("/mnt/user-data/outputs/Memo_sample_2026-01-02.docx")
    True
    >>> is_container_scratch_path("C:/Users/Sample/CR/_hq/meetings/x.docx")
    False
    """
    if not absolute_path:
        return False
    return absolute_path.replace("\\", "/").startswith(CONTAINER_SCRATCH_PREFIX)


def is_absolute_local_path(path: str) -> bool:
    """True for a path that names a location on SOME machine's filesystem.

    A POSIX path starting `/`, or a Windows path starting with a drive letter.
    Everything else — most importantly a workspace-RELATIVE path, which is what
    `deliverables.land` answers with when a run does not know the customer-side
    spelling of the workspace folder — is not a location and never a link.

    >>> is_absolute_local_path("/Users/sample/CR/_hq/meetings/x.docx")
    True
    >>> is_absolute_local_path("C:/Users/Sample/CR/x.docx")
    True
    >>> is_absolute_local_path("Sample Org/deliverables/x.docx")
    False
    >>> is_absolute_local_path("")
    False
    """
    if not path:
        return False
    normalized = str(path).replace("\\", "/")
    if normalized.startswith("/"):
        return True
    return len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha()


def device_absolute(absolute_path: str, workspace_root: str,
                    device_root: str) -> str:
    """A path under the sandbox VM's mount → the same file on the customer's PC.

    The VM mounts the customer's folder at `$HOME/mnt/<basename>` and spells
    every file under a per-session root; the desktop app knows the folder by
    its real Windows path and bakes it into each scheduled chat at
    registration (`<WORKSPACE_ABSOLUTE_PATH>`). The same file therefore has two
    spellings, and exactly one of them can be opened: the PC's.

    Returns "" — never a guess — when either root is missing or the file is not
    inside `workspace_root`. An empty answer is what lets the caller drop the
    link instead of emitting a dead one.

    >>> device_absolute("/sessions/rcw-1/mnt/CR/_hq/meetings/x.docx",
    ...                 "/sessions/rcw-1/mnt/CR", "C:/Users/Sample/CR")
    'C:/Users/Sample/CR/_hq/meetings/x.docx'
    >>> device_absolute("/sessions/rcw-1/mnt/CR/_hq/meetings/x.docx", "", "C:/x")
    ''
    """
    if not absolute_path or not workspace_root or not device_root:
        return ""
    target = absolute_path.replace("\\", "/")
    root = workspace_root.replace("\\", "/").rstrip("/")
    if not root or not target.startswith(root + "/"):
        return ""
    return device_root.replace("\\", "/").rstrip("/") + target[len(root):]


def get_brief_opener_url(absolute_path: str, drive_web_url: str = "", *,
                         workspace_root: str = "",
                         device_root: str = "") -> str:
    """The URL the deliverable link should actually open — Drive-aware (v5.9.2).

    `computer://` links only work when the path exists on the customer's own
    machine. For a workspace mounted from Google Drive / OneDrive, the resolved
    absolute path is session-scoped (see `is_session_scoped_path`) — there is
    no local file to open, so the `computer://` form is a guaranteed
    "Failed to load local file." Field-reported twice by QMG (2026-07-28,
    2026-07-31) before this helper existed.

    `drive_web_url` is the file's web URL on WHICHEVER cloud platform hosts
    the workspace — a Google Drive / Docs link, a OneDrive share link, or a
    SharePoint document URL. The name says "drive" generically, not "Google
    Drive": v5.9.2 shipped with Drive-only lookup INSTRUCTIONS upstream, and
    a OneDrive/SharePoint-hosted workspace stayed on dead `computer://` links
    (BUG-8538, bug_received seq 8538 — the THIRD link-delivery report from
    the same customer). The helper itself never cared which platform the URL
    came from; the fix is in the lookup, `tool_discovery.discover_drive_tool`
    + `infer_workspace_drive_platform`.

    v5.33 (DELIV1) adds the merged-seat case. There the document is built in a
    container scratch and landed on the customer's machine through the access
    layer, so the file the chat is about lives at a path the RENDERING process
    never sees. Two new rules follow, both about not emitting a link to a file
    that exists only in this session:

      * a container scratch path (`/mnt/user-data/...`) is NEVER a link, in any
        mode — nothing on the customer's computer is there;
      * a sandbox-VM path (`/sessions/rcw-.../mnt/<basename>/...`) becomes the
        PC path when `workspace_root` + `device_root` are supplied — the
        desktop app bakes that absolute Windows path into every scheduled chat
        — and `computer://` then opens the real file (CONTRACT Rule 3);
      * a sandbox-VM path that could NOT be mapped is never a link either.

    Resolution order:
      1. A container scratch path → "" (no link; the caller says the words and
         drops the href).
      2. `device_root` supplied and the path is inside `workspace_root` → the
         `computer://` opener for the PC path.
      3. Session-scoped path + a `drive_web_url` supplied → the cloud web URL
         (the file IS in their cloud drive; the browser opens it everywhere).
      4. Session-scoped path with nothing to map it and no web URL → "".
      5. A path that is not absolute → "" (a workspace-relative pointer is a
         real thing this helper may be handed once a landing did not learn the
         customer-side root; it is not a location on anybody's machine).
      6. Anything else → `get_brief_artifact_url(absolute_path)`, unchanged —
         host-native paths keep the `computer://` opener that M's 2026-05-20
         testing validated, and a host-native seat's answer is byte-for-byte
         what it was before this change (both new arguments default to "").

    **Rule 4 REVERSES v5.9.2's fallback, deliberately (review F-1).** That
    fallback minted `computer:///sessions/<id>/...` when no web URL was found,
    on the reasoning that a maybe-dead link beats no link. Two things have
    changed since. The link is not maybe-dead, it is dead — `computer://`
    resolves against the reader's own machine and no such path is there — and
    the string carries a SESSION ID into a sentence the customer reads, which
    M's standing rule forbids on any customer surface. `saved_to_meetings_footer`
    already degrades to the words alone on an empty answer, which is the honest
    shape: the words are always true, the href is only there when it opens
    something.

    Orchestrators on a session-scoped workspace root MUST still attempt the
    cloud web-link lookup (search `_hq/meetings/<filename>` through the drive
    tool discovered with `tool_discovery.discover_drive_tool(tools, "search",
    prefer_platform=tool_discovery.infer_workspace_drive_platform(root))` —
    preferring the platform that hosts the workspace when several drives are
    connected) and pass it here; that lookup is now the only thing standing
    between a Drive-mounted workspace and a link-free footer.

    >>> get_brief_opener_url("/sessions/abc/mnt/CR/_hq/meetings/x.docx", "https://docs.google.com/document/d/f1/view")
    'https://docs.google.com/document/d/f1/view'
    >>> get_brief_opener_url("/sessions/abc/mnt/CR/_hq/meetings/x.docx", "https://stoneindustries.sharepoint.com/:w:/r/Documents/x.docx")
    'https://stoneindustries.sharepoint.com/:w:/r/Documents/x.docx'
    >>> get_brief_opener_url("C:/Users/Sample/CR/_hq/meetings/x.docx", "https://docs.google.com/document/d/f1/view")
    'computer://C:\\\\Users\\\\Sample\\\\CR\\\\_hq\\\\meetings\\\\x.docx'
    >>> get_brief_opener_url("/sessions/abc/mnt/CR/_hq/meetings/x.docx")
    ''
    >>> get_brief_opener_url("/mnt/user-data/outputs/x.docx")
    ''
    >>> get_brief_opener_url("Sample Org/deliverables/x.docx")
    ''
    >>> get_brief_opener_url("/sessions/rcw-1/mnt/CR/_hq/meetings/x.docx",
    ...                      workspace_root="/sessions/rcw-1/mnt/CR",
    ...                      device_root="C:/Users/Sample/CR")
    'computer://C:\\\\Users\\\\Sample\\\\CR\\\\_hq\\\\meetings\\\\x.docx'
    """
    if device_root:
        on_device = device_absolute(absolute_path, workspace_root, device_root)
        if on_device:
            return get_brief_artifact_url(on_device)
    if is_session_scoped_path(absolute_path):
        # The web URL is the one link that works for this shape. Without it
        # there is nothing to emit: see rule 4 above.
        return drive_web_url or ""
    if not is_absolute_local_path(absolute_path):
        return ""
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


# ---------------------------------------------------------------------------
# THE FOOTER — "saved to your meetings folder", and the link (SPEC FIXTRAIN
# 6.2, last surface).
#
# The wrap and the prep both end by telling the customer where the document
# went. On 09-11 and 09-13 they ended by PRINTING THE PATH — the internal
# folder name, in plain text, as the label. The path belongs in the href,
# where the operating system needs it; it has never belonged in a sentence.
#
# The allow-list that lets `_hq/meetings/` through the leak gate stays exactly
# as it is, and this is why it is narrow: it exists so a LINK to the meetings
# folder survives the scan, not so the folder can be named in prose. This
# composer is the shape that distinction is supposed to take — words for the
# reader, path inside the link, one function both surfaces call.
# ---------------------------------------------------------------------------

FOOTER_TEXT = "Saved to your meetings folder"


def saved_to_meetings_footer(absolute_path: str, *, label: str = "",
                             drive_web_url: str = "",
                             workspace_root: str = "",
                             device_root: str = "") -> str:
    """One markdown line: plain words, with the document behind the link.

    `label` is the link text — the document's own subject in the customer's
    words ("Friday wrap", "Prep for the Stone Supply call"). It defaults to
    "open it", which is honest and names nothing internal. The path is never
    the label, and the folder is never spelled out: "your meetings folder" is
    what a person calls it.

    Returns the words alone when there is no path to link (a document that was
    not written is not a broken link, it is no link) — and the same when the
    only path we hold is a container scratch or an unmappable VM path, which is
    DELIV1's whole point: the words are always true, the href is only there
    when it opens something.
    """
    text = label.strip() or "open it"
    if not absolute_path:
        return f"{FOOTER_TEXT}."
    url = get_brief_opener_url(absolute_path, drive_web_url,
                               workspace_root=workspace_root,
                               device_root=device_root)
    if not url:
        return f"{FOOTER_TEXT}."
    return f"{FOOTER_TEXT} — [{text}]({url})"


__all__ = [
    "BriefType",
    "CONTAINER_SCRATCH_PREFIX",
    "device_absolute",
    "get_brief_filename",
    "get_brief_path",
    "get_brief_artifact_url",
    "get_brief_opener_url",
    "is_absolute_local_path",
    "is_container_scratch_path",
    "is_session_scoped_path",
    "ensure_brief_directory",
    "FOOTER_TEXT",
    "saved_to_meetings_footer",
]


if __name__ == "__main__":
    # Smoke tests
    print(get_brief_filename("past_meeting", "Sam UX review", "2026-04-30"))
    print(get_brief_filename("call_prep", "Q2 deck", "2026-05-12"))
    print(get_brief_path("/workspace", "past_meeting", "sam", "2026-04-30"))
    print(get_brief_artifact_url(
        "/c/Users/Sample/CommandRoom/_hq/meetings/Past_Meeting_x_2026-04-30.docx"
    ))
