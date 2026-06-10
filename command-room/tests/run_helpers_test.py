#!/usr/bin/env python3
"""
Tests for the v2.13.0+ centralized helpers — `brief_path.py` and `tool_discovery.py`.

These cover the contract these helpers must satisfy:

  brief_path:
    - All briefs save under `_hq/meetings/`
    - Filenames follow Past_Meeting_<slug>_<date>.docx / Call_Prep_<slug>_<date>.docx
    - Slugs lowercase, hyphenated, alphanumeric-only
    - artifact URLs encode paths to computer:/// URLs
    - get_brief_path requires workspace_root

  tool_discovery:
    - Calendar discovery EXCLUDES Zapier-namespaced tools (CONTRACT.md Rule 8)
    - Calendar with only Zapier exposed → returns no match + clear plain-English reason
    - Gmail discovery EXCLUDES Zapier (Zapier handled separately for send-only)
    - Zapier send discovery uses 3-tier permissive matching
    - Zapier send rejects Calendar/Drive tools even if name has "send" or "email"
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shared", "scripts"))

from brief_path import (
    get_brief_filename,
    get_brief_path,
    get_brief_artifact_url,
)
from tool_discovery import (
    ToolDescriptor,
    discover_calendar_tool,
    discover_gmail_tool,
    discover_zapier_send_tool,
    discover_granola_tool,
    # v2.14.2+ cross-stack abstractions
    discover_mail_send_tool,
    discover_mail_reply_tool,
    discover_mail_draft_tool,
    discover_mail_search_tool,
    discover_mail_thread_fetch_tool,
    discover_transcript_tool,
    discover_drive_tool,
)


PASS = "  ✓"
FAIL = "  ✗"
results = {"pass": 0, "fail": 0, "failures": []}


def check(name, cond, expected=None, got=None):
    if cond:
        print(f"{PASS} {name}")
        results["pass"] += 1
    else:
        print(f"{FAIL} {name}")
        if expected is not None:
            print(f"      expected: {expected!r}")
            print(f"      got:      {got!r}")
        results["fail"] += 1
        results["failures"].append(name)


# ============================================================================
# brief_path tests
# ============================================================================

print("=== brief_path ===")

# Filename
check(
    "past_meeting filename",
    get_brief_filename("past_meeting", "Sam UX review", "2026-04-30")
    == "Past_Meeting_sam-ux-review_2026-04-30.docx",
    "Past_Meeting_sam-ux-review_2026-04-30.docx",
    get_brief_filename("past_meeting", "Sam UX review", "2026-04-30"),
)
check(
    "call_prep filename",
    get_brief_filename("call_prep", "Q2 deck", "2026-05-12")
    == "Call_Prep_q2-deck_2026-05-12.docx",
    "Call_Prep_q2-deck_2026-05-12.docx",
    get_brief_filename("call_prep", "Q2 deck", "2026-05-12"),
)

# Slug edge cases
check(
    "slug strips special chars",
    get_brief_filename("past_meeting", "Mira — Q3 (review)", "2026-04-30")
    == "Past_Meeting_mira-q3-review_2026-04-30.docx",
)
check(
    "empty slug fallback",
    get_brief_filename("past_meeting", "", "2026-04-30")
    == "Past_Meeting_untitled_2026-04-30.docx",
)

# Path
check(
    "path always under _hq/meetings/",
    "_hq/meetings/" in get_brief_path("/workspace", "past_meeting", "x", "2026-04-30"),
)
check(
    "path is forward-slash normalized on Windows-style root",
    "\\" not in get_brief_path("C:\\workspace", "call_prep", "x", "2026-04-30"),
)
_raised = False
try:
    get_brief_path("", "past_meeting", "x", "2026-04-30")
except (ValueError, Exception):
    _raised = True
check("path raises on empty workspace_root", _raised)

# Artifact URL
check(
    "artifact URL is computer:///",
    get_brief_artifact_url("/workspace/_hq/meetings/x.docx").startswith("computer:///"),
)
check(
    "artifact URL URL-encodes spaces",
    "%20" in get_brief_artifact_url("/Command Room/_hq/meetings/x y.docx"),
)
check(
    "artifact URL preserves forward slashes",
    "/" in get_brief_artifact_url("/workspace/_hq/meetings/x.docx"),
)


# ============================================================================
# tool_discovery tests
# ============================================================================

print("\n=== tool_discovery ===")

# Setup mock tools
mock_tools_full = [
    ToolDescriptor("mcp__abc__google_calendar_find_events", "Calendar Find", "Native"),
    ToolDescriptor("mcp__abc__google_calendar_create_event", "Calendar Create", "Native"),
    ToolDescriptor("mcp__zapier__google_calendar_find_events", "Zapier Cal", "Zap"),
    ToolDescriptor("mcp__abc__gmail_send_message", "Gmail Send", "Native"),
    ToolDescriptor("mcp__abc__gmail_create_draft", "Gmail Draft", "Native"),
    ToolDescriptor(
        "mcp__zapier__send_threaded_email_via_gmail",
        "Send Threaded Email via Gmail",
        "Reply to Email - Command Room Zap",
    ),
    ToolDescriptor("mcp__abc__granola_get_meeting_transcript", "Granola Get", "Native"),
]

# Calendar
r = discover_calendar_tool(mock_tools_full, "find_events")
check(
    "calendar prefers native over Zapier",
    r.tool_id == "mcp__abc__google_calendar_find_events",
    "mcp__abc__google_calendar_find_events",
    r.tool_id,
)

r = discover_calendar_tool(mock_tools_full, "create_event")
check(
    "calendar matches operation hint",
    r.tool_id == "mcp__abc__google_calendar_create_event",
)

# Calendar with ONLY Zapier exposed
only_zap_cal = [ToolDescriptor("mcp__zapier__google_calendar_find_events", "Zapier Cal", "Zap")]
r = discover_calendar_tool(only_zap_cal, "find_events")
check(
    "calendar with only-Zapier returns None",
    r.tool_id is None,
)
check(
    "calendar reason mentions HARD SCOPE",
    "HARD SCOPE" in r.reason or "Zapier Calendar" in r.reason,
    "...Zapier Calendar...HARD SCOPE...",
    r.reason,
)

# Gmail
r = discover_gmail_tool(mock_tools_full, "send_message")
check(
    "gmail picks native send",
    r.tool_id == "mcp__abc__gmail_send_message",
)

r = discover_gmail_tool(mock_tools_full, "create_draft")
check(
    "gmail picks native draft",
    r.tool_id == "mcp__abc__gmail_create_draft",
)

# Zapier send
r = discover_zapier_send_tool(mock_tools_full)
check(
    "zapier send found via permissive match",
    r.tool_id == "mcp__zapier__send_threaded_email_via_gmail",
)

# Zapier send NOT confused by Zapier Calendar
zap_cal_only = [
    ToolDescriptor("mcp__zapier__google_calendar_find_events", "Cal Find", "Lists events"),
]
r = discover_zapier_send_tool(zap_cal_only)
check(
    "zapier send rejects Calendar tool",
    r.tool_id is None,
)

# Granola
r = discover_granola_tool(mock_tools_full, "get_meeting_transcript")
check(
    "granola found",
    r.tool_id == "mcp__abc__granola_get_meeting_transcript",
)


# ============================================================================
# v2.14.2+ cross-stack abstractions tests
# ============================================================================

print("\n=== v2.14.2 cross-stack abstractions ===")

# Cross-stack mock tools
gmail_outlook_tools = [
    ToolDescriptor("mcp__abc__gmail_send_message", "Gmail Send", "Native Gmail send"),
    ToolDescriptor("mcp__abc__gmail_create_draft", "Gmail Draft", "Native Gmail draft"),
    ToolDescriptor("mcp__abc__gmail_search_threads", "Gmail Search", "Native Gmail search"),
    ToolDescriptor("mcp__abc__gmail_get_thread", "Gmail Thread Fetch", "Native"),
    ToolDescriptor("mcp__zapier__send_threaded_email_via_gmail", "Zap", "Reply Email"),
]

outlook_only_tools = [
    ToolDescriptor("mcp__xyz__outlook_send_message", "Outlook Send", "Microsoft Graph send"),
    ToolDescriptor("mcp__xyz__outlook_reply_to_email", "Outlook Reply", "Threaded reply"),
    ToolDescriptor("mcp__xyz__outlook_create_draft", "Outlook Draft", "Graph draft"),
    ToolDescriptor("mcp__xyz__outlook_search_messages", "Outlook Search", "Graph search"),
]

# Mail send — Gmail
r = discover_mail_send_tool(gmail_outlook_tools)
check(
    "mail send: Gmail detected",
    r.tool_id == "mcp__abc__gmail_send_message" and r.platform == "gmail",
)

# Mail send — Outlook only
r = discover_mail_send_tool(outlook_only_tools)
check(
    "mail send: Outlook detected when no Gmail",
    r.tool_id == "mcp__xyz__outlook_send_message" and r.platform == "outlook",
)

# Mail send — excludes Zapier
zapier_only = [ToolDescriptor("mcp__zapier__send_threaded_email_via_gmail", "Zap", "")]
r = discover_mail_send_tool(zapier_only)
check(
    "mail send: rejects Zapier-only (Zapier handled by separate helper)",
    r.tool_id is None,
)

# Mail draft — Outlook
r = discover_mail_draft_tool(outlook_only_tools)
check(
    "mail draft: Outlook detected",
    r.tool_id == "mcp__xyz__outlook_create_draft" and r.platform == "outlook",
)

# Mail search — Gmail
r = discover_mail_search_tool(gmail_outlook_tools)
check(
    "mail search: Gmail detected",
    r.tool_id == "mcp__abc__gmail_search_threads" and r.platform == "gmail",
)

# Mail thread fetch — Gmail
r = discover_mail_thread_fetch_tool(gmail_outlook_tools)
check(
    "mail thread fetch: Gmail detected",
    r.tool_id == "mcp__abc__gmail_get_thread" and r.platform == "gmail",
)

# Mail reply — Outlook
r = discover_mail_reply_tool(outlook_only_tools)
check(
    "mail reply: Outlook reply_to_email detected",
    r.tool_id == "mcp__xyz__outlook_reply_to_email" and r.platform == "outlook",
)

# Transcript — Granola
r = discover_transcript_tool(mock_tools_full, "get_meeting_transcript")
check(
    "transcript: Granola detected",
    r.tool_id == "mcp__abc__granola_get_meeting_transcript" and r.platform == "granola",
)

# Transcript — Fireflies fallback
fireflies_tools = [
    ToolDescriptor("mcp__qrs__fireflies_get_meeting_transcript", "Fireflies", "Get transcript"),
]
r = discover_transcript_tool(fireflies_tools, "get_meeting_transcript")
check(
    "transcript: Fireflies detected",
    r.tool_id == "mcp__qrs__fireflies_get_meeting_transcript" and r.platform == "fireflies",
)

# Drive — Google Drive
gdrive_tools = [ToolDescriptor("mcp__abc__google_drive_search_files", "GDrive Search", "")]
r = discover_drive_tool(gdrive_tools, "search_files")
check(
    "drive: Google Drive detected",
    r.tool_id == "mcp__abc__google_drive_search_files" and r.platform == "google_drive",
)

# Drive — OneDrive
onedrive_tools = [ToolDescriptor("mcp__xyz__onedrive_search_files", "OneDrive Search", "")]
r = discover_drive_tool(onedrive_tools, "search_files")
check(
    "drive: OneDrive detected",
    r.tool_id == "mcp__xyz__onedrive_search_files" and r.platform == "onedrive",
)

# Calendar — extended for Outlook Calendar
outlook_cal_tools = [
    ToolDescriptor("mcp__xyz__outlook_calendar_find_events", "Outlook Cal", "Graph events"),
]
r = discover_calendar_tool(outlook_cal_tools, "find_events")
check(
    "calendar: Outlook Calendar detected (v2.14.2+ extension)",
    r.tool_id == "mcp__xyz__outlook_calendar_find_events" and r.platform == "outlook_calendar",
)

# Calendar — Google still works
r = discover_calendar_tool(mock_tools_full, "find_events")
check(
    "calendar: Google Calendar still detected",
    r.tool_id is not None and r.platform == "google_calendar",
)


# ============================================================================
# Summary
# ============================================================================

print(f"\n=== {results['pass']} passed, {results['fail']} failed ===")
if results["fail"]:
    print("Failures:")
    for f in results["failures"]:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
