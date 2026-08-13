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
        discover_gmail_tool,
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
    / `"m365_sharepoint"` / `"google_calendar"` / `"outlook_calendar"`. Lets
    orchestrators branch on platform without re-parsing the tool_id. None when
    no match.
    """
    tool_id: Optional[str] = None
    reason: str = ""
    candidates_considered: int = 0
    platform: Optional[str] = None


import re as _re

# tool_id shape: mcp__<server-id>__<operation>
_TOOL_ID_RE = _re.compile(r"^mcp__(?P<server>.+?)__(?P<op>.+)$")

# Zapier signature operations. A UUID-namespaced Zapier leg carries no
# `mcp__zapier_` prefix (R12/H-H) — its tool names (`gmail_send_email`) mis-match
# as native Gmail. `get_configuration_url` is the Zapier "configure this Zap"
# tool and is a strong signature; a server exposing it is treated as Zapier even
# when it isn't pinned by server-id in the manifest.
_ZAPIER_SIGNATURE_OPS = frozenset({"get_configuration_url"})


def _server_id_of(tool_id: str):
    """Extract the MCP server-id from a fully-qualified tool id, or None."""
    m = _TOOL_ID_RE.match((tool_id or "").strip())
    return m.group("server") if m else None


def zapier_servers(tools, pinned_ids=None) -> set:
    """Server-ids that are the Zapier dispatch leg. Union of (a) ids pinned in
    the manifest (`workspace.connectors._zapier_server_ids`, authoritative —
    passed as `pinned_ids`) and (b) heuristic detection: any server exposing a
    Zapier signature op (`get_configuration_url`). This closes the H-H trap in
    BOTH the declared-backend path (pinned) and the empty-map fallback path
    (heuristic) — a UUID server with `gmail_*` tools resolves as Zapier, never
    native (R12)."""
    ids = set(pinned_ids or [])
    by_server = {}
    for t in tools:
        tid = getattr(t, "tool_id", t if isinstance(t, str) else "")
        sid = _server_id_of(tid)
        if not sid:
            continue
        op = tid.split("__")[-1].lower()
        by_server.setdefault(sid, set()).add(op)
    for sid, ops in by_server.items():
        if ops & _ZAPIER_SIGNATURE_OPS:
            ids.add(sid)
    return ids


def _is_zapier(tool_id: str, zapier_ids=None) -> bool:
    """True if this tool belongs to a Zapier server. Legacy `mcp__zapier_`
    prefix OR membership of the tool's server-id in `zapier_ids` (the R12 fix —
    UUID-namespaced Zapier legs have no prefix)."""
    if "mcp__zapier_" in (tool_id or ""):
        return True
    if zapier_ids:
        return _server_id_of(tool_id) in zapier_ids
    return False


# ============================================================================
# Cross-stack hint sets (v2.14.2+ — connector parity per CONTRACT.md Rule 21)
#
# Every native connector is addressable through abstracted helpers below.
# Same code paths work for both Google + Microsoft / alt stacks. Whatever's
# a native connector should work the same.
# ============================================================================

# Mail (send / reply / draft / search) — Gmail OR Outlook OR Superhuman.
#
# These hints only ever fire on a tool id that SPELLS the product
# (`mcp__abc__gmail_send_message`). A real UUID-namespaced connector does not:
# native Gmail ships `mcp__f12657a1__search_threads`, Superhuman ships
# `mcp__ec5e0bd5__create_or_update_draft`, and neither carries a product token
# anywhere in the id. Every mail helper below therefore falls back to the
# capability manifest's FINGERPRINTS — the provider is identified by the set of
# operations its server exposes, which is the only thing a UUID env leaves to
# match on (MAILSEAM item 2).
_MAIL_PLATFORM_HINTS = {
    "gmail": ("gmail", "google_mail"),
    "outlook": ("outlook", "microsoft_outlook", "ms_outlook", "office365_mail",
                "ms_graph_mail", "graph_mail"),
    "superhuman": ("superhuman",),
}

# Transcript — Granola OR Fireflies (extensible)
_TRANSCRIPT_PLATFORM_HINTS = {
    "granola": ("granola",),
    "fireflies": ("fireflies", "fireflies_ai", "firefliesai"),
}

# Drive / file storage — Google Drive OR OneDrive OR Microsoft 365 / SharePoint
#
# BUG-8538 (bug_received seq 8538): the Microsoft 365 connector's file surface
# spells "sharepoint" in its OPERATION name, not "onedrive" — the real captured
# connector (2026-07-11 connector-agnostic audit) ships
# `mcp__<uuid>__sharepoint_search`. The pre-v5.11.1 map matched only
# `google_drive/…` and `onedrive/…`, so a workspace on the M365/SharePoint
# stack could never resolve a web link for its briefs: the session-scoped
# branch fell through to the `computer://` form, a guaranteed dead link on a
# cloud-mounted workspace.
_DRIVE_PLATFORM_HINTS = {
    "google_drive": ("google_drive", "googledrive", "gdrive"),
    "onedrive": ("onedrive", "one_drive", "ms_onedrive"),
    # Tokens are FILE-SCOPED on purpose (review of PR #51, finding 1):
    # server-level tokens like `m365`/`microsoft365` were proposed in the
    # spec but failed verification — on a server whose id spells them, they
    # mis-bind NON-drive tools (`mcp__m365__outlook_email_search`) as the
    # drive tool, and only `sharepoint` is backed by the captured real
    # connector (`sharepoint_search`). `ms_files`/`graph_files` stay: they
    # contain the file token, so they cannot match a mail/calendar/chat op.
    "m365_sharepoint": ("sharepoint", "share_point", "ms_files",
                        "graph_files"),
}

# The Microsoft cloud-drive spellings are ONE family: a workspace synced
# through "OneDrive - <org>" is served by the same Microsoft 365 connector
# whose tool spells "sharepoint". Preference matching (below) compares
# families, never raw platform keys, so an inferred `onedrive` host prefers a
# `m365_sharepoint` tool and vice versa.
_DRIVE_PLATFORM_FAMILIES = {
    "google_drive": "google",
    "onedrive": "microsoft",
    "m365_sharepoint": "microsoft",
}


def _drive_family(platform: Optional[str]) -> Optional[str]:
    return _DRIVE_PLATFORM_FAMILIES.get(platform or "")

# Calendar — Google Calendar OR Outlook Calendar (Graph)
_CALENDAR_PLATFORM_HINTS = {
    "google_calendar": ("google_calendar", "googlecalendar"),
    "outlook_calendar": ("outlook_calendar", "microsoft_calendar", "ms_calendar",
                         "graph_calendar", "office365_calendar"),
}

# Team chat — Slack OR Microsoft Teams (CHATSCAN1 took the parity slot MC3
# left open, without touching a single call site, exactly as intended).
#
# Same caveat as the mail hints above: these substrings only ever fire on a
# tool id that SPELLS the product, and a real UUID-namespaced connector does
# not. The Microsoft 365 connector ships `mcp__<uuid>__teams_list_chats` and
# `mcp__<uuid>__chat_message_search` — no product token anywhere — so Teams is
# identified by the capability manifest's FINGERPRINTS, via
# `discover_chat_tool` below. Leaving it hint-only would have meant a Teams
# workspace silently having no chat backend at all.
_CHAT_PLATFORM_HINTS = {
    "slack": ("slack",),
    "ms365_teams": ("msteams", "ms_teams", "microsoft_teams", "teams_"),
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


def _capability_manifest():
    """The capability manifest module, or None when it can't be imported —
    discovery then keeps hint-only behavior rather than failing."""
    try:
        from connector_adapters import capabilities
        return capabilities
    except ImportError:
        try:
            from pathlib import Path as _P
            import sys as _sys
            _sys.path.insert(0, str(_P(__file__).resolve().parent))
            from connector_adapters import capabilities
            return capabilities
        except Exception:
            return None
    except Exception:
        return None


def _fingerprint_platforms(tools, category: str = "email") -> dict:
    """`server-id → provider`, resolved from the capability manifest's
    fingerprints (MAILSEAM item 2).

    The UUID-env answer to `_match_platform`: a server whose tool ids spell no
    product name is identified by WHICH OPERATIONS it exposes — the same
    fingerprint data `repair_backend` already re-pairs on, so there is one
    source of provider identity, not two. Only rows in `category` are eligible
    and Zapier rows never are: the dispatch leg's tool names contain 'gmail'
    but it is not a native mail backend (R12/H-H).

    {} when the manifest is unreadable — the caller keeps hint-only behavior.
    """
    cap = _capability_manifest()
    if cap is None:
        return {}
    by_server: dict = {}
    for t in tools:
        tid = getattr(t, "tool_id", t if isinstance(t, str) else "")
        sid = _server_id_of(tid)
        if sid:
            by_server.setdefault(sid, []).append(tid)
    out: dict = {}
    try:
        rows = cap.providers()
    except Exception:
        return {}
    for sid, ids in by_server.items():
        try:
            ranked = cap.match_fingerprint(ids)
        except Exception:
            continue
        for provider, _hits in ranked:
            row = rows.get(provider) or {}
            if row.get("is_zapier"):
                continue
            if (row.get("category") or "") != category:
                continue
            out[sid] = provider
            break
    return out


def _known_mail_products() -> str:
    """The mail products the capability manifest actually knows, by their own
    labels — so the degrade line can never name a product the seam doesn't
    support or omit one it does (the pre-MAILSEAM text said "Gmail or Outlook"
    on a build that had shipped Superhuman support)."""
    cap = _capability_manifest()
    labels = []
    if cap is not None:
        try:
            for name, row in sorted((cap.providers() or {}).items()):
                if row.get("category") == "email" and not row.get("is_zapier"):
                    labels.append(row.get("label") or name)
        except Exception:
            labels = []
    return ", ".join(labels) if labels else "your mail connector"


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
    zap_ids = zapier_servers(tools_list)

    for t in tools_list:
        candidates += 1
        if _is_zapier(t.tool_id, zap_ids):
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


def discover_zapier_send_tool(tools: Iterable[ToolDescriptor],
                              zapier_ids=None) -> DiscoveryResult:
    """Discover the user's Zapier-threaded-send Zap.

    Three matching paths in priority order (per EMAIL_DRAFT_PROTOCOL.md §3c
    v2.12.6+):

      1. Tool name contains `command_room_send_threaded_email` (slug variations
         allowed: `command-room-send-threaded-email`, with double underscores,
         etc.)
      2. Tool description contains `Command Room` AND (`Send Threaded Email`
         OR `threaded`)
      3. Permissive fallback: any Zapier tool whose name or description
         contains BOTH `gmail` (or `email`) AND (`send` OR `reply`). If
         multiple match, prefer ones containing `command`/`room`; fall back to
         first containing `send`. Calendar/Drive/Sheets tools won't match.

    R12/H-H: "Zapier" is now recognized in a UUID-namespaced env too — the
    server-id is matched against `zapier_ids` (the pinned
    `_zapier_server_ids` list, passed by the caller) plus the
    `get_configuration_url` signature heuristic. Without this, the UUID Zapier
    leg is invisible to this discovery (no `mcp__zapier_` prefix) and the send
    path silently can't fire.

    Returns the matched tool ID or None + plain-English reason.
    """
    tools_list = list(tools)
    zap = zapier_servers(tools_list, zapier_ids)
    candidates = 0

    # Path 1: name slug match
    name_slug_targets = (
        "command_room_send_threaded_email",
        "command-room-send-threaded-email",
        "command_room__send_threaded_email",
    )
    for t in tools_list:
        candidates += 1
        if not _is_zapier(t.tool_id, zap):
            continue
        tid_lower = t.tool_id.lower()
        name_lower = (t.name or "").lower()
        for tgt in name_slug_targets:
            if tgt in tid_lower or tgt in name_lower:
                return DiscoveryResult(tool_id=t.tool_id, candidates_considered=candidates)

    # Path 2: description fuzzy match
    for t in tools_list:
        if not _is_zapier(t.tool_id, zap):
            continue
        desc = (t.description or "").lower()
        if "command room" in desc and ("send threaded email" in desc or "threaded" in desc):
            return DiscoveryResult(tool_id=t.tool_id, candidates_considered=candidates)

    # Path 3: permissive fallback — any Zapier tool that's clearly a Gmail/email
    # send/reply action
    fallback_candidates = []
    for t in tools_list:
        if not _is_zapier(t.tool_id, zap):
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
    candidates = len(tools_list)
    # R12/H-H: exclude Zapier servers (pinned + heuristically detected) so a
    # UUID Zapier leg exposing `gmail_send_email` is never matched as native.
    zap_ids = zapier_servers(tools_list)
    # MAILSEAM item 2: the product-name hints miss every real connector, whose
    # ids are UUID-namespaced. Fingerprints answer for those.
    fp_platforms = _fingerprint_platforms(tools_list, "email")
    # Keywords are scanned in PRIORITY order rather than registry order, so a
    # server exposing both a precise and a broad tool always resolves to the
    # precise one — which tool you get stops depending on how the connector
    # happened to list them.
    for kw in operation_keywords:
        k = kw.lower().replace("_", "").replace("-", "")
        for t in tools_list:
            if _is_zapier(t.tool_id, zap_ids):
                continue
            platform = (_match_platform(t.tool_id, _MAIL_PLATFORM_HINTS)
                        or fp_platforms.get(_server_id_of(t.tool_id)))
            if not platform:
                continue
            tid_norm = t.tool_id.lower().replace("_", "").replace("-", "")
            if k in tid_norm:
                return DiscoveryResult(
                    tool_id=t.tool_id,
                    candidates_considered=candidates,
                    platform=platform,
                )
    return DiscoveryResult(
        tool_id=None,
        reason=(
            f"No native mail tool found for {operation_label}. Connect one of "
            f"{_known_mail_products()} in Cowork → Settings → Connectors."
        ),
        candidates_considered=candidates,
    )


# The mail SEARCH vocabulary, in priority order — one list, read by both the
# search helper and `discover_for_category`'s intent routing, so the two can
# never disagree about what a search tool looks like.
_MAIL_SEARCH_KEYWORDS = ("searchthreads", "emailsearch", "searchmessages",
                         "queryemail", "findmessages", "listthreads")


def _is_mail_search_intent(operation) -> bool:
    """True when `operation` is a search INTENT compiled by
    `connector_adapters/mail.py` rather than a connector tool name. False when
    the adapter can't be imported — the seam then keeps its old behavior
    instead of routing on a guess."""
    try:
        from connector_adapters.mail import is_search_intent
    except ImportError:
        try:
            from pathlib import Path as _P
            import sys as _sys
            _sys.path.insert(0, str(_P(__file__).resolve().parent))
            from connector_adapters.mail import is_search_intent
        except Exception:
            return False
    except Exception:
        return False
    return is_search_intent(operation)


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
    """Native mail search tool across every known provider.

    Gmail: `search_threads` / `search_messages`. Outlook: `outlook_email_search`
    / `search_messages` / `find_messages`. Superhuman: `query_email_and_calendar`
    (its structured query surface) or `list_threads`. Excludes Zapier.

    This is where every SEARCH INTENT lands — `in_sent`, `unread`,
    `message_id_lookup` and the rest are scopes compiled into a query by
    `connector_adapters/mail.py`, not tools, so `discover_for_category` routes
    them here (MAILSEAM item 1). The keyword list therefore has to cover the
    providers the seam claims to support: before this it named only the Gmail
    and Graph spellings, so a Superhuman workspace resolved no search tool at
    all and the fetch fell back to whatever the model improvised.
    """
    return _discover_mail_tool(
        tools,
        operation_keywords=list(_MAIL_SEARCH_KEYWORDS),
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
# Chat — the PROVIDER-AGNOSTIC resolver (SPEC CHATSCAN1 §2A)
# ============================================================================


def discover_chat_tool(
    tools: Iterable[ToolDescriptor],
    operation: str = "read_channel",
    declared: Optional[dict] = None,
    zapier_ids=None,
) -> DiscoveryResult:
    """Resolve a chat READ tool without knowing, or caring, which chat product
    the workspace runs.

    `discover_slack_tool` above stays exactly as it was — the capture leg
    calls it and its contract is unchanged — but it can only ever find Slack,
    which would have made every Teams workspace look like a workspace with no
    chat at all. This is the resolver the closure and context legs use.

    Order, mirroring the mail seam:
      1. DECLARED backend, server-id first (`discover_for_category`). This is
         the deterministic path and the only one immune to substring hazards.
      2. FINGERPRINT match against the capability manifest's `chat` rows — the
         answer for a UUID-namespaced connector whose tool ids spell no
         product name, which is what every real Microsoft 365 connector is.
      3. Product-name substring hints, which is what the pre-CHATSCAN1 Slack
         path did and still covers a connector that spells itself.

    `tool_id=None` means the workspace HAS no chat backend for this operation.
    Per the connector-down doctrine the calling leg then does not exist for
    this fire: no error, no mention to the user — and a receipt, written by
    the leg, so the silence is still provable.
    """
    tools_list = list(tools)

    if declared and declared.get("server_id"):
        res = discover_for_category("chat", operation, tools_list,
                                    declared=declared, zapier_ids=zapier_ids)
        if res.tool_id:
            return res
        # A declared backend that cannot serve THIS operation is a capability
        # gap, not a reason to go looking for some other product's tool: a
        # workspace that declared one chat backend must never be silently read
        # through another.
        return res

    zap = zapier_servers(tools_list, zapier_ids)
    op_norm = operation.lower().replace("_", "")
    eligible = [t for t in tools_list if not _is_zapier(t.tool_id, zap)]

    by_provider = _fingerprint_platforms(eligible, category="chat")
    if by_provider:
        soft = None
        for t in eligible:
            sid = _server_id_of(t.tool_id)
            provider = by_provider.get(sid) if sid else None
            if not provider:
                continue
            if op_norm in t.tool_id.lower().replace("_", ""):
                return DiscoveryResult(tool_id=t.tool_id,
                                       candidates_considered=len(tools_list),
                                       platform=provider)
            if soft is None:
                soft = (t, provider)
        if soft is not None:
            return DiscoveryResult(
                tool_id=soft[0].tool_id,
                reason=(f"matched the declared chat backend by capability "
                        f"fingerprint but operation hint {operation!r} is not "
                        f"in the tool ID"),
                candidates_considered=len(tools_list),
                platform=soft[1],
            )

    soft_hint = None
    for t in eligible:
        platform = _match_platform(t.tool_id, _CHAT_PLATFORM_HINTS)
        if not platform:
            continue
        if op_norm in t.tool_id.lower().replace("_", ""):
            return DiscoveryResult(tool_id=t.tool_id,
                                   candidates_considered=len(tools_list),
                                   platform=platform)
        if soft_hint is None:
            soft_hint = (t, platform)
    if soft_hint is not None:
        return DiscoveryResult(
            tool_id=soft_hint[0].tool_id,
            reason=(f"matched a chat tool but operation hint {operation!r} is "
                    f"not in the tool ID"),
            candidates_considered=len(tools_list),
            platform=soft_hint[1],
        )

    return DiscoveryResult(
        tool_id=None,
        reason="No chat backend is connected in this workspace.",
        candidates_considered=len(tools_list),
    )


# ============================================================================
# Drive / file storage — Google Drive OR OneDrive OR M365/SharePoint
# (v2.14.2+; M365/SharePoint + workspace-host preference v5.11.1, BUG-8538)
# ============================================================================

# Path markers that identify which cloud platform HOSTS a workspace folder.
# Checked against the workspace root, normalized to forward slashes, lowered,
# with a trailing slash appended — so a root that ENDS at the marker folder
# (".../Google Drive/My Drive") still matches. SharePoint is checked before
# OneDrive: a synced SharePoint library path can carry both spellings, and
# the more specific product wins.
_WORKSPACE_DRIVE_MARKERS = (
    ("m365_sharepoint", ("sharepoint",)),
    ("onedrive", ("onedrive", "one drive", "one_drive")),
    ("google_drive", ("google drive/", "googledrive/", "google_drive/",
                      "gdrive/", "my drive/", "shared drives/")),
)


def infer_workspace_drive_platform(workspace_root) -> Optional[str]:
    """Which drive platform hosts the WORKSPACE, read from its root path —
    `"google_drive"` / `"onedrive"` / `"m365_sharepoint"` / None (BUG-8538).

    When a customer has more than one drive connected (the reporting workspace
    had Google Drive AND Microsoft 365), first-match discovery can bind the
    platform that does NOT host the workspace and search it for a file that
    lives in the other — an empty lookup even where the hints all match. The
    decision has to come from the workspace mount, not tool order: pass this
    result as `discover_drive_tool(..., prefer_platform=...)`.

    None means the root carries no marker (a Cowork session-scoped mount
    `/sessions/<id>/mnt/<name>` usually doesn't) — the caller then keeps
    first-match behavior and should try the OTHER connected drive platform
    when the first lookup finds nothing.

    >>> infer_workspace_drive_platform("C:/Users/Sample/OneDrive - Stone Industries/Command Room")
    'onedrive'
    >>> infer_workspace_drive_platform("C:/Users/Sample/Google Drive/My Drive/Command Room")
    'google_drive'
    >>> infer_workspace_drive_platform("/sessions/abc/mnt/Command Room") is None
    True
    """
    root = str(workspace_root or "").replace("\\", "/").lower().rstrip("/")
    if not root:
        return None
    root += "/"
    for platform, markers in _WORKSPACE_DRIVE_MARKERS:
        if any(m in root for m in markers):
            return platform
    return None


def discover_drive_tool(
    tools: Iterable[ToolDescriptor],
    operation: str = "search_files",
    prefer_platform: Optional[str] = None,
) -> DiscoveryResult:
    """Discover a file-storage tool across Google Drive, OneDrive, or the
    Microsoft 365 / SharePoint connector (whose file surface spells
    `sharepoint`, e.g. `sharepoint_search` — BUG-8538).

    Used by skills that need to read/write client docs (intel-intake,
    workspace-ingest, memo-writer, etc.) and by the session-scoped brief
    opener lookup. Operation hints: `search_files`, `search`,
    `download_file`, `upload_file`, `list_recent`, etc.

    `prefer_platform` (v5.11.1) is the platform hosting the WORKSPACE —
    normally `infer_workspace_drive_platform(workspace_root)`. When more than
    one drive platform is connected, first-match returns whichever tool the
    registry happens to list first, which can bind the drive that does not
    hold the workspace's files. With a preference set, matching compares
    platform FAMILIES (`onedrive` and `m365_sharepoint` are both the
    Microsoft family), and a soft match on the preferred family outranks an
    exact operation match on the wrong family — the wrong family's drive
    cannot host the workspace file at all. Without it (None), behavior is
    exactly the pre-v5.11.1 first-match.
    """
    tools_list = list(tools)
    candidates = 0
    soft_match: Optional[ToolDescriptor] = None
    soft_platform: Optional[str] = None
    first_exact: Optional[tuple] = None
    preferred_exact: Optional[tuple] = None
    preferred_soft: Optional[tuple] = None
    prefer_family = _drive_family(prefer_platform)
    op_norm = operation.lower().replace("_", "")

    for t in tools_list:
        candidates += 1
        platform = _match_platform(t.tool_id, _DRIVE_PLATFORM_HINTS)
        if not platform:
            continue
        tid_norm = t.tool_id.lower().replace("_", "")
        if op_norm in tid_norm:
            if prefer_family is None:
                return DiscoveryResult(
                    tool_id=t.tool_id,
                    candidates_considered=candidates,
                    platform=platform,
                )
            if first_exact is None:
                first_exact = (t, platform)
            if preferred_exact is None and _drive_family(platform) == prefer_family:
                preferred_exact = (t, platform)
        else:
            if soft_match is None:
                soft_match = t
                soft_platform = platform
            if preferred_soft is None and _drive_family(platform) == prefer_family:
                preferred_soft = (t, platform)

    if preferred_exact is not None:
        return DiscoveryResult(
            tool_id=preferred_exact[0].tool_id,
            candidates_considered=candidates,
            platform=preferred_exact[1],
        )
    if preferred_soft is not None:
        return DiscoveryResult(
            tool_id=preferred_soft[0].tool_id,
            reason=(f"matched the workspace-hosting drive family but operation "
                    f"hint {operation!r} not in tool ID"),
            candidates_considered=candidates,
            platform=preferred_soft[1],
        )
    if first_exact is not None:
        return DiscoveryResult(
            tool_id=first_exact[0].tool_id,
            reason=(f"preferred drive platform {prefer_platform!r} exposes no "
                    f"matching tool — matched {first_exact[1]!r} instead; its "
                    "drive may not hold the workspace's files"),
            candidates_considered=candidates,
            platform=first_exact[1],
        )

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
            "No drive/file-storage MCP tool found. Connect Google Drive, "
            "OneDrive, or Microsoft 365 / SharePoint in Cowork → Settings → "
            "Connectors."
        ),
        candidates_considered=candidates,
    )


# ============================================================================
# Server-id-first resolution (A1 keystone) + fingerprint re-pair (A1b)
# ============================================================================


def discover_for_category(
    category: str,
    operation: str,
    tools: Iterable[ToolDescriptor],
    declared: Optional[dict] = None,
    zapier_ids=None,
) -> DiscoveryResult:
    """Server-id-first resolution — the primary discovery path (A1).

    When a backend is DECLARED for the category (`declared` = the
    `connector_config.declared_backend(category)` row, `{server_id, provider,
    label}`), find the tool ON THAT SERVER whose operation matches. This is
    deterministic and immune to the substring / H-H hazards entirely — the
    Zapier leg is never the declared email backend.

    When NO backend is declared (empty map), returns tool_id=None with a reason;
    the caller then falls back to the substring `discover_*` helper below, which
    IS today's behavior (R4). The `zapier_ids` set (from
    `workspace.connectors._zapier_server_ids`) is honored so a pinned Zapier
    server is excluded even on the fallback path."""
    tools_list = list(tools)
    zap = zapier_servers(tools_list, zapier_ids)
    if declared and declared.get("server_id"):
        sid = declared["server_id"]
        # MAILSEAM item 1 — an operation is not always a tool NAME. `in_sent`,
        # `unread`, `message_id_lookup` and their siblings are search INTENTS
        # that `connector_adapters/mail.py` compiles into a provider query.
        # Substring-matching one against tool ids resolved to None on EVERY
        # provider, Gmail included (no connector ships a tool called
        # `in_sent`), so the Sent read silently became something the model
        # improvised. An intent resolves through the adapter to the provider's
        # SEARCH tool, which is the thing that can actually run the scope.
        is_intent = category == "email" and _is_mail_search_intent(operation)
        op_candidates = (list(_MAIL_SEARCH_KEYWORDS) if is_intent
                         else [operation.lower().replace("_", "")])
        server_seen = False
        for t in tools_list:
            if _server_id_of(t.tool_id) == sid:
                server_seen = True
                break
        for op_norm in op_candidates:
            for t in tools_list:
                if _server_id_of(t.tool_id) != sid:
                    continue
                if _is_zapier(t.tool_id, zap):
                    continue
                if op_norm in t.tool_id.lower().replace("_", ""):
                    return DiscoveryResult(
                        tool_id=t.tool_id,
                        candidates_considered=len(tools_list),
                        platform=declared.get("provider"),
                    )
        if not server_seen:
            # R13 drift: the declared server-id is ABSENT from the fire-time
            # registry (reconnect rotated the UUID, or the connector is off
            # for this session). Distinct from capability-absent — the caller
            # runs detect_backend_drift and follows the R13 split
            # (interactive: confirm a re-pair; silent: skip the leg + flag).
            return DiscoveryResult(
                tool_id=None,
                reason=(
                    f"declared {category} backend (server {sid}, "
                    f"{declared.get('provider')}) is NOT PRESENT in this "
                    "session's tool registry — backend drift (R13); run "
                    "tool_discovery.detect_backend_drift and confirm a "
                    "re-pair interactively, or skip-and-flag on a silent fire."
                ),
                candidates_considered=len(tools_list),
            )
        return DiscoveryResult(
            tool_id=None,
            reason=(
                f"declared {category} backend (server {sid}, "
                f"{declared.get('provider')}) exposes no "
                + (f"search tool to run the {operation!r} scope"
                   if is_intent else f"{operation!r} tool")
                + " — capability absent; degrade per RELIABILITY.md."
            ),
            candidates_considered=len(tools_list),
        )
    return DiscoveryResult(
        tool_id=None,
        reason=(
            f"no declared {category} backend; caller falls back to substring "
            "discovery (empty-map = today's behavior, R4)."
        ),
        candidates_considered=len(tools_list),
    )


def detect_backend_drift(tools: Iterable[ToolDescriptor], declared: Optional[dict],
                         *, min_overlap: int = 2) -> Optional[dict]:
    """R13 drift-detect, code half. Given the fire-time tool list and a
    declared backend row ({server_id, provider, label}), returns None when the
    declared server is present (no drift). When it is ABSENT, groups the
    visible tools by server-id, fingerprints each server, and returns:

      {"declared_server_id", "declared_provider",
       "candidate_server_id": <the server whose fingerprint matches the
                               declared provider, or None>,
       "candidate_provider":  <its matched provider, or None>}

    The PROSE half decides what to do with it (never this function):
    interactive session → confirm the re-pair with the user, then re-pin via
    connector_config.set_declared_backend + a connector_backend_changed event
    (and a connector_detected event for the new server-id). Silent/scheduled
    session → SKIP that connector's leg this fire + flag for the next
    interactive session (a connector_detected event with
    fingerprint_matched) — never prompt, never ingest through an unconfirmed
    binding."""
    if not declared or not declared.get("server_id"):
        return None
    tools_list = list(tools)
    sid = declared["server_id"]
    by_server: dict = {}
    for t in tools_list:
        s = _server_id_of(t.tool_id)
        if s:
            by_server.setdefault(s, []).append(t.tool_id)
    if sid in by_server:
        return None  # declared server present — no drift
    want = (declared.get("provider") or "").lower() or None
    cand_sid = None
    cand_provider = None
    for s, ids in by_server.items():
        match = repair_backend(ids, min_overlap=min_overlap)
        if match and (want is None or match.lower() == want):
            cand_sid, cand_provider = s, match
            break
    return {
        "declared_server_id": sid,
        "declared_provider": declared.get("provider"),
        "candidate_server_id": cand_sid,
        "candidate_provider": cand_provider,
    }


def repair_backend(server_tool_ids, min_overlap: int = 2) -> Optional[str]:
    """Fingerprint re-pair (A1b): given the tool-name set of a reconnected
    server whose UUID changed, return the best-matching known provider (or
    None). The caller CONFIRMS with the user before re-pinning — interactive
    only; a silent/scheduled session skips-and-flags (R13). Delegates to the
    capability manifest's fingerprints."""
    try:
        from connector_adapters.capabilities import best_fingerprint_match
    except ImportError:
        from pathlib import Path as _P
        import sys as _sys
        _sys.path.insert(0, str(_P(__file__).resolve().parent))
        from connector_adapters.capabilities import best_fingerprint_match
    return best_fingerprint_match(server_tool_ids, min_overlap=min_overlap)


__all__ = [
    "ToolDescriptor",
    "DiscoveryResult",
    "discover_for_category",
    "detect_backend_drift",
    "repair_backend",
    "zapier_servers",
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
    # v5.11.1 BUG-8538 — workspace-host preference for multi-drive workspaces
    "infer_workspace_drive_platform",
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
