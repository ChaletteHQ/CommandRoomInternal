#!/usr/bin/env python3
"""
Centralized MCP tool discovery — single source of truth for which tools each
orchestrator action uses, with namespace rules enforced.

Per shared/CONTRACT.md Rule 8 (Calendar HARD SCOPE): calendar NEVER goes
through Zapier. All calendar operations use native `mcp__*google_calendar_*`.

Per shared/CONTRACT.md Rule 9: orchestrators do NOT pick namespaces themselves.
They call the helpers in this module, which return matched tool IDs (or
None + reason) so the orchestrator can degrade gracefully with a plain-English
note instead of silently using the wrong tool.

USAGE (from an orchestrator's Phase 2 setup):

    from tool_discovery import (
        discover_calendar_tool,
        discover_gmail_send_tool,
        discover_zapier_send_tool,
        discover_granola_tool,
    )

    cal = discover_calendar_tool(available_tool_ids)
    if cal.tool_id is None:
        # surface plain English: "(Native Calendar MCP not available — ...)"
        ...

    zap = discover_zapier_send_tool(available_tools)  # tools w/ name + description
    if zap.tool_id:
        # use Zapier-threaded path on N send
        ...

The helpers return a `DiscoveryResult` dataclass with:
  - tool_id: matched tool ID (str) or None
  - reason: plain-English explanation if no match (str) or empty if matched
  - candidates_considered: count of tools scanned (int) — for diagnostics
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional


@dataclass
class ToolDescriptor:
    """Describes one available MCP tool. Orchestrator pulls these from Cowork's
    tool registry at fire time.
    """
    tool_id: str  # e.g. "mcp__abc123__google_calendar_find_events"
    name: str = ""  # human-friendly name from the MCP manifest
    description: str = ""  # human-friendly description from the manifest


@dataclass
class DiscoveryResult:
    """Outcome of a discovery call. tool_id present = matched; None = no match
    + plain-English reason.

    `platform` (v2.14.2+) names the stack that matched — `"gmail"` /
    `"outlook"` / `"granola"` / `"fireflies"` / `"google_drive"` / `"onedrive"`
    / `"google_calendar"` / `"outlook_calendar"`. Lets orchestrators branch on
    platform without re-parsing the tool_id. None when no match.
    """
    tool_id: Optional[str] = None
    reason: str = ""
    candidates_considered: int = 0
    platform: Optional[str] = None


def _is_zapier(tool_id: str) -> bool:
    return "mcp__zapier_" in (tool_id or "")


# ============================================================================
# Cross-stack hint sets (v2.14.2+ — connector parity per CONTRACT.md Rule 21)
#
# Every native connector is addressable through abstracted helpers below.
# Same code paths work for both Google + Microsoft / alt stacks. Whatever's
# a native connector should work the same.
# ============================================================================

# Mail (send / reply / draft / search) — Gmail OR Outlook
_MAIL_PLATFORM_HINTS = {
    "gmail": ("gmail", "google_mail"),
    "outlook": ("outlook", "microsoft_outlook", "ms_outlook", "office365_mail",
                "ms_graph_mail", "graph_mail"),
}

# Transcript — Granola OR Fireflies (extensible)
_TRANSCRIPT_PLATFORM_HINTS = {
    "granola": ("granola",),
    "fireflies": ("fireflies", "fireflies_ai", "firefliesai"),
}

# Drive / file storage — Google Drive OR OneDrive
_DRIVE_PLATFORM_HINTS = {
    "google_drive": ("google_drive", "googledrive", "gdrive"),
    "onedrive": ("onedrive", "one_drive", "ms_onedrive"),
}

# Calendar — Google Calendar OR Outlook Calendar (Graph)
_CALENDAR_PLATFORM_HINTS = {
    "google_calendar": ("google_calendar", "googlecalendar"),
    "outlook_calendar": ("outlook_calendar", "microsoft_calendar", "ms_calendar",
                         "graph_calendar", "office365_calendar"),
}

# Team chat — Slack today (v4.6.0 MC3); the map shape leaves room for Teams
# parity later without touching call sites (same Rule 21 posture as the rest).
_CHAT_PLATFORM_HINTS = {
    "slack": ("slack",),
}


def _match_platform(tool_id: str, platform_hints: dict) -> Optional[str]:
    """Returns the platform name (e.g. 'gmail', 'outlook') if the tool_id
    matches any of that platform's hint substrings. None if no match.
    """
    tid = (tool_id or "").lower()
    for platform_name, hints in platform_hints.items():
        for hint in hints:
            if hint in tid:
                return platform_name
    return None


# ============================================================================
# Calendar — native only, never Zapier (Google Calendar OR Outlook Calendar)
# ============================================================================


def discover_calendar_tool(
    tools: Iterable[ToolDescriptor],
    operation: str = "find_events",
) -> DiscoveryResult:
    """Discover the native Calendar tool for the given operation across BOTH
    Google Calendar and Outlook Calendar (Graph API).

    `operation` is a hint like `find_events`, `create_event`, `respond_to_event`,
    `update_event`. The function looks for a tool whose ID matches a native
    calendar platform AND the operation hint, EXCLUDING any Zapier-namespaced
    tools.

    v2.14.2+ — extended to detect Outlook Calendar via the
    `_CALENDAR_PLATFORM_HINTS` map. Per CONTRACT.md Rule 21, the orchestrator
    branches on `result.platform` to use the right adapter.

    Per CONTRACT.md Rule 8 — calendar NEVER through Zapier. If only Zapier
    calendar tools are exposed, returns no match with a plain-English reason.
    """
    tools_list = list(tools)
    candidates = 0
    soft_match: Optional[ToolDescriptor] = None
    soft_platform: Optional[str] = None

    for t in tools_list:
        candidates += 1
        if _is_zapier(t.tool_id):
            continue
        platform = _match_platform(t.tool_id, _CALENDAR_PLATFORM_HINTS)
        if not platform:
            continue
        tid_lower = t.tool_id.lower()
        if operation in tid_lower or operation.replace("_", "") in tid_lower.replace("_", ""):
            return DiscoveryResult(
                tool_id=t.tool_id,
                candidates_considered=candidates,
                platform=platform,
            )
        # Soft match — calendar platform OK but operation hint not in tool_id
        if soft_match is None:
            soft_match = t
            soft_platform = platform

    if soft_match is not None:
        return DiscoveryResult(
            tool_id=soft_match.tool_id,
            reason=f"matched native Calendar tool but operation hint {operation!r} not in tool ID",
            candidates_considered=candidates,
            platform=soft_platform,
        )

    # Check if Zapier-namespaced calendar tools exist — if so, the user has
    # Zapier Calendar configured but no native; surface a clear reason.
    for t in tools_list:
        if _is_zapier(t.tool_id):
            tid_lower = t.tool_id.lower()
            if _match_platform(tid_lower, _CALENDAR_PLATFORM_HINTS):
                return DiscoveryResult(
                    tool_id=None,
                    reason=(
                        "Native Calendar MCP not connected — only Zapier Calendar "
                        "exposed, which is out-of-scope per the calendar HARD SCOPE "
                        "rule. Connect Google Calendar or Outlook Calendar in Cowork "
                        "→ Settings → Connectors to enable calendar actions."
                    ),
                    candidates_considered=candidates,
                )

    return DiscoveryResult(
        tool_id=None,
        reason="No native Calendar MCP tool found (Google Calendar or Outlook Calendar).",
        candidates_considered=candidates,
    )


# ============================================================================
# Gmail send / draft / search — native (Zapier-namespaced explicitly excluded)
# ============================================================================

_GMAIL_NATIVE_HINTS = ("gmail", "google_mail")


def discover_gmail_tool(
    tools: Iterable[ToolDescriptor],
    operation: str,
) -> DiscoveryResult:
    """Discover a native Gmail MCP tool for an operation
    (`search_threads`, `create_draft`, `send_draft`, `send_message`,
    `create_label`, `get_thread`).

    Excludes Zapier-namespaced Gmail tools — Zapier-via-Gmail is handled via
    `discover_zapier_send_tool` per `EMAIL_DRAFT_PROTOCOL.md` §3c.
    """
    tools_list = list(tools)
    candidates = 0
    op_norm = operation.lower().replace("_", "")

    for t in tools_list:
        candidates += 1
        if _is_zapier(t.tool_id):
            continue
        tid_lower = t.tool_id.lower()
        if any(hint in tid_lower for hint in _GMAIL_NATIVE_HINTS):
            tid_norm = tid_lower.replace("_", "")
            if op_norm in tid_norm:
                return DiscoveryResult(tool_id=t.tool_id, candidates_considered=candidates)

    return DiscoveryResult(
        tool_id=None,
        reason=f"No native Gmail MCP tool found for operation {operation!r}.",
        candidates_considered=candidates,
    )


# ============================================================================
# Zapier-threaded-send tool — permissive matching per EMAIL_DRAFT_PROTOCOL §3c
# ============================================================================


def discover_zapier_send_tool(tools: Iterable[ToolDescriptor]) -> DiscoveryResult:
    """Discover the user's Zapier-threaded-send Zap.

    Three matching paths in priority order (per EMAIL_DRAFT_PROTOCOL.md §3c
    v2.12.6+):

      1. Tool name contains `command_room_send_threaded_email` (slug variations
         allowed: `command-room-send-threaded-email`, with double underscores,
         etc.)
      2. Tool description contains `Command Room` AND (`Send Threaded Email`
         OR `threaded`)
      3. Permissive fallback: any `mcp__zapier_*` tool whose name or description
         contains BOTH `gmail` (or `email`) AND (`send` OR `reply`). If
         multiple match, prefer ones containing `command`/`room`; fall back to
         first containing `send`. Calendar/Drive/Sheets tools won't match.

    Returns the matched tool ID or None + plain-English reason.
    """
    tools_list = list(tools)
    candidates = 0

    # Path 1: name slug match
    name_slug_targets = (
        "command_room_send_threaded_email",
        "command-room-send-threaded-email",
        "command_room__send_threaded_email",
    )
    for t in tools_list:
        candidates += 1
        if not _is_zapier(t.tool_id):
            continue
        tid_lower = t.tool_id.lower()
        name_lower = (t.name or "").lower()
        for tgt in name_slug_targets:
            if tgt in tid_lower or tgt in name_lower:
                return DiscoveryResult(tool_id=t.tool_id, candidates_considered=candidates)

    # Path 2: description fuzzy match
    for t in tools_list:
        if not _is_zapier(t.tool_id):
            continue
        desc = (t.description or "").lower()
        if "command room" in desc and ("send threaded email" in desc or "threaded" in desc):
            return DiscoveryResult(tool_id=t.tool_id, candidates_considered=candidates)

    # Path 3: permissive fallback — any Zapier tool that's clearly a Gmail/email
    # send/reply action
    fallback_candidates = []
    for t in tools_list:
        if not _is_zapier(t.tool_id):
            continue
        haystack = ((t.name or "") + " " + (t.description or "") + " " + t.tool_id).lower()
        has_mail = "gmail" in haystack or "email" in haystack
        has_send = "send" in haystack or "reply" in haystack
        # Exclude tools that are clearly NOT email send (Calendar, Drive, etc.)
        is_calendar = "calendar" in haystack
        is_drive = "drive" in haystack and "send" not in haystack[:30]
        if has_mail and has_send and not is_calendar and not is_drive:
            fallback_candidates.append(t)

    if len(fallback_candidates) == 1:
        return DiscoveryResult(
            tool_id=fallback_candidates[0].tool_id,
            candidates_considered=candidates,
            reason="permissive match (name didn't exactly match canonical slug)",
        )
    if len(fallback_candidates) > 1:
        # Prefer ones containing `command` or `room` in name
        for t in fallback_candidates:
            haystack = ((t.name or "") + " " + t.tool_id).lower()
            if "command" in haystack or "room" in haystack:
                return DiscoveryResult(
                    tool_id=t.tool_id,
                    candidates_considered=candidates,
                    reason="permissive match (preferring name containing 'command'/'room')",
                )
        # Otherwise pick first containing `send`
        for t in fallback_candidates:
            haystack = ((t.name or "") + " " + t.tool_id).lower()
            if "send" in haystack:
                return DiscoveryResult(
                    tool_id=t.tool_id,
                    candidates_considered=candidates,
                    reason="permissive match (multiple candidates, picked first containing 'send')",
                )

    return DiscoveryResult(
        tool_id=None,
        reason=(
            "Zapier send tool not detected. Confirm your Zap exists in Cowork → "
            "Settings → Connectors → Zapier, named exactly "
            "`Command Room — Send Threaded Email` (em-dash). If it's there with "
            "a different name, rename it. Otherwise see the setup guide."
        ),
        candidates_considered=candidates,
    )


# ============================================================================
# Granola — for past-meetings transcript fetch
# ============================================================================


def discover_granola_tool(tools: Iterable[ToolDescriptor], operation: str) -> DiscoveryResult:
    """Discover a Granola MCP tool for an operation
    (`get_meeting_transcript`, `list_meetings`, `get_meetings`, `query`).
    """
    tools_list = list(tools)
    candidates = 0
    op_norm = operation.lower().replace("_", "")

    for t in tools_list:
        candidates += 1
        tid_lower = t.tool_id.lower()
        name_lower = (t.name or "").lower()
        if "granola" in tid_lower or "granola" in name_lower:
            tid_norm = tid_lower.replace("_", "")
            if op_norm in tid_norm:
                return DiscoveryResult(tool_id=t.tool_id, candidates_considered=candidates)

    return DiscoveryResult(
        tool_id=None,
        reason=f"No Granola MCP tool found for operation {operation!r}.",
        candidates_considered=candidates,
    )


# ============================================================================
# Mail — abstraction across Gmail + Outlook (v2.14.2+)
# Per CONTRACT.md Rule 21: every native connector addressable through the same
# helper. Gmail/Outlook routed transparently; orchestrator branches on
# result.platform when adapter logic differs.
# ============================================================================


def _discover_mail_tool(
    tools: Iterable[ToolDescriptor],
    operation_keywords: list,
    operation_label: str,
) -> DiscoveryResult:
    """Generic mail-tool discovery — finds a native Gmail or Outlook tool whose
    ID matches the given operation keywords. Excludes Zapier (handled
    separately by discover_zapier_send_tool).
    """
    tools_list = list(tools)
    candidates = 0
    for t in tools_list:
        candidates += 1
        if _is_zapier(t.tool_id):
            continue
        platform = _match_platform(t.tool_id, _MAIL_PLATFORM_HINTS)
        if not platform:
            continue
        tid_norm = t.tool_id.lower().replace("_", "").replace("-", "")
        if any(k.replace("_", "") in tid_norm for k in operation_keywords):
            return DiscoveryResult(
                tool_id=t.tool_id,
                candidates_considered=candidates,
                platform=platform,
            )
    return DiscoveryResult(
        tool_id=None,
        reason=(
            f"No native mail tool found for {operation_label}. Connect Gmail or "
            "Outlook in Cowork → Settings → Connectors."
        ),
        candidates_considered=candidates,
    )


def discover_mail_send_tool(tools: Iterable[ToolDescriptor]) -> DiscoveryResult:
    """Native Gmail or Outlook send tool (NEW thread, NOT a reply).

    Looks for tools matching `send_message`, `send_email`, `send_mail`,
    `sendmessage`, etc. across both stacks. Excludes Zapier — Zapier routing
    happens separately via `discover_zapier_send_tool` per EMAIL_DRAFT_PROTOCOL
    §3c HARD SCOPE.
    """
    return _discover_mail_tool(
        tools,
        operation_keywords=["sendmessage", "sendemail", "sendmail"],
        operation_label="send",
    )


def discover_mail_reply_tool(tools: Iterable[ToolDescriptor]) -> DiscoveryResult:
    """Native Gmail or Outlook threaded-reply tool.

    Gmail: typically achieved by `send_draft` with a threadId, OR direct
    `reply_to_email`. Outlook: `reply_to_email` action. Both stacks expose
    something whose ID matches `reply_to`/`reply_message`/`replytomessage`.
    Excludes Zapier.
    """
    return _discover_mail_tool(
        tools,
        operation_keywords=["replytoemail", "replytomessage", "reply_message", "reply_to_email"],
        operation_label="threaded reply",
    )


def discover_mail_draft_tool(tools: Iterable[ToolDescriptor]) -> DiscoveryResult:
    """Native Gmail or Outlook draft-creation tool.

    Gmail: `create_draft`. Outlook: `create_draft` / `create_message_draft`.
    Excludes Zapier — drafts are NEVER sent through Zapier per
    EMAIL_DRAFT_PROTOCOL §3c.
    """
    return _discover_mail_tool(
        tools,
        operation_keywords=["createdraft", "create_message_draft", "draftmessage"],
        operation_label="draft",
    )


def discover_mail_search_tool(tools: Iterable[ToolDescriptor]) -> DiscoveryResult:
    """Native Gmail or Outlook search tool.

    Gmail: `search_threads` / `search_messages`. Outlook: `search_messages` /
    `find_messages`. Excludes Zapier.
    """
    return _discover_mail_tool(
        tools,
        operation_keywords=["searchthreads", "searchmessages", "findmessages", "search_threads"],
        operation_label="search",
    )


def discover_mail_thread_fetch_tool(tools: Iterable[ToolDescriptor]) -> DiscoveryResult:
    """Native Gmail `get_thread` or Outlook conversation-fetch equivalent.

    Used by orchestrators that need to read the FULL contents of a thread/
    conversation (e.g., `cr-inbox` building the original_thread block in the
    widget). Excludes Zapier.
    """
    return _discover_mail_tool(
        tools,
        operation_keywords=["getthread", "getconversation", "get_message", "fetchconversation"],
        operation_label="thread fetch",
    )


# ============================================================================
# Transcript — Granola OR Fireflies (v2.14.2+)
# ============================================================================


def discover_transcript_tool(
    tools: Iterable[ToolDescriptor],
    operation: str = "get_meeting_transcript",
) -> DiscoveryResult:
    """Discover a transcript-fetching tool across Granola OR Fireflies.

    `operation` hint: `get_meeting_transcript` / `list_meetings` / `get_meetings`
    / `query`. Returns the first matching tool ID + platform name. If neither
    is connected, returns None + plain-English direction.
    """
    tools_list = list(tools)
    candidates = 0
    soft_match: Optional[ToolDescriptor] = None
    soft_platform: Optional[str] = None
    op_norm = operation.lower().replace("_", "")

    for t in tools_list:
        candidates += 1
        platform = _match_platform(t.tool_id, _TRANSCRIPT_PLATFORM_HINTS)
        if not platform:
            continue
        tid_norm = t.tool_id.lower().replace("_", "")
        if op_norm in tid_norm:
            return DiscoveryResult(
                tool_id=t.tool_id,
                candidates_considered=candidates,
                platform=platform,
            )
        if soft_match is None:
            soft_match = t
            soft_platform = platform

    if soft_match is not None:
        return DiscoveryResult(
            tool_id=soft_match.tool_id,
            reason=f"matched transcript tool but operation hint {operation!r} not in tool ID",
            candidates_considered=candidates,
            platform=soft_platform,
        )

    return DiscoveryResult(
        tool_id=None,
        reason=(
            "No transcript MCP tool found. Connect Granola or Fireflies in Cowork → "
            "Settings → Connectors."
        ),
        candidates_considered=candidates,
    )


# ============================================================================
# Slack — read-side discovery for the commitment-capture leg (v4.6.0 MC3)
# ============================================================================


def discover_slack_tool(
    tools: Iterable[ToolDescriptor],
    operation: str = "read_channel",
) -> DiscoveryResult:
    """Discover a native Slack MCP tool for a read operation
    (`read_channel`, `read_thread`, `search_channels`, `search_users`,
    `read_user_profile`, `search_public_and_private`).

    Excludes Zapier-namespaced Slack tools — Zapier is for triggered
    automations, never connector reads. tool_id=None means Slack simply is
    not connected in this workspace: per the MC3 contract the calling leg
    silently doesn't exist (zero errors, zero mentions to the user) — the
    empty `reason` string here is deliberate diagnostics-only wording.
    """
    tools_list = list(tools)
    candidates = 0
    soft_match: Optional[ToolDescriptor] = None
    op_norm = operation.lower().replace("_", "")

    for t in tools_list:
        candidates += 1
        if _is_zapier(t.tool_id):
            continue
        platform = _match_platform(t.tool_id, _CHAT_PLATFORM_HINTS)
        if not platform:
            continue
        tid_norm = t.tool_id.lower().replace("_", "")
        if op_norm in tid_norm:
            return DiscoveryResult(
                tool_id=t.tool_id,
                candidates_considered=candidates,
                platform=platform,
            )
        if soft_match is None:
            soft_match = t

    if soft_match is not None:
        return DiscoveryResult(
            tool_id=soft_match.tool_id,
            reason=f"matched Slack tool but operation hint {operation!r} not in tool ID",
            candidates_considered=candidates,
            platform="slack",
        )

    return DiscoveryResult(
        tool_id=None,
        reason="No native Slack MCP tool found.",
        candidates_considered=candidates,
    )


# ============================================================================
# Drive / file storage — Google Drive OR OneDrive (v2.14.2+)
# ============================================================================


def discover_drive_tool(
    tools: Iterable[ToolDescriptor],
    operation: str = "search_files",
) -> DiscoveryResult:
    """Discover a file-storage tool across Google Drive OR OneDrive.

    Used by skills that need to read/write client docs (intel-intake,
    workspace-ingest, memo-writer, etc.). Operation hints:
    `search_files`, `download_file`, `upload_file`, `list_recent`, etc.
    """
    tools_list = list(tools)
    candidates = 0
    soft_match: Optional[ToolDescriptor] = None
    soft_platform: Optional[str] = None
    op_norm = operation.lower().replace("_", "")

    for t in tools_list:
        candidates += 1
        platform = _match_platform(t.tool_id, _DRIVE_PLATFORM_HINTS)
        if not platform:
            continue
        tid_norm = t.tool_id.lower().replace("_", "")
        if op_norm in tid_norm:
            return DiscoveryResult(
                tool_id=t.tool_id,
                candidates_considered=candidates,
                platform=platform,
            )
        if soft_match is None:
            soft_match = t
            soft_platform = platform

    if soft_match is not None:
        return DiscoveryResult(
            tool_id=soft_match.tool_id,
            reason=f"matched drive tool but operation hint {operation!r} not in tool ID",
            candidates_considered=candidates,
            platform=soft_platform,
        )

    return DiscoveryResult(
        tool_id=None,
        reason=(
            "No drive/file-storage MCP tool found. Connect Google Drive or OneDrive "
            "in Cowork → Settings → Connectors."
        ),
        candidates_considered=candidates,
    )


__all__ = [
    "ToolDescriptor",
    "DiscoveryResult",
    "discover_calendar_tool",
    "discover_gmail_tool",
    "discover_zapier_send_tool",
    "discover_granola_tool",
    # v2.14.2+ cross-stack abstractions
    "discover_mail_send_tool",
    "discover_mail_reply_tool",
    "discover_mail_draft_tool",
    "discover_mail_search_tool",
    "discover_mail_thread_fetch_tool",
    "discover_transcript_tool",
    "discover_drive_tool",
    # v4.6.0 MC3 — Slack commitment-capture leg
    "discover_slack_tool",
]


if __name__ == "__main__":
    # Smoke tests
    fake_tools = [
        ToolDescriptor("mcp__zapier__google_calendar_find_events", "Google Calendar: Find Events", "Lists events"),
        ToolDescriptor("mcp__abc123__google_calendar_create_event", "Create Event", "Native Calendar create"),
        ToolDescriptor("mcp__zapier__send_threaded_email_via_gmail", "Send Threaded Email via Gmail", "Reply to Email - Command Room"),
        ToolDescriptor("mcp__abc456__gmail_send_message", "Gmail Send Message", "Native Gmail send"),
        ToolDescriptor("mcp__abc789__granola_get_meeting_transcript", "Granola get meeting", ""),
    ]

    print("Calendar (find_events):", discover_calendar_tool(fake_tools, "find_events"))
    print("Gmail (send_message):", discover_gmail_tool(fake_tools, "send_message"))
    print("Zapier send (permissive):", discover_zapier_send_tool(fake_tools))
    print("Granola (get_meeting_transcript):", discover_granola_tool(fake_tools, "get_meeting_transcript"))

    # Negative cases
    only_zap = [
        ToolDescriptor("mcp__zapier__google_calendar_find_events", "Google Calendar: Find Events", "Lists events"),
    ]
    print("\nOnly-Zapier-Calendar case:", discover_calendar_tool(only_zap, "find_events"))
