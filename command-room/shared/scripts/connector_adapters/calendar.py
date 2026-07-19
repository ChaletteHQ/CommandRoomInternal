#!/usr/bin/env python3
"""
Calendar data-model adapter (Layer A5) — one intent, N provider shapes.

Parallel to mail.py for the calendar category. Skills express intent
(`find_events`, `availability`, `create`, `rsvp`) with neutral field names; this
module maps to the provider's fields (Google `timeMin`/`timeMax`/`responseStatus`
vs Graph equivalents vs Superhuman-calendar). Calendar is native-only, never
Zapier (CONTRACT Rule 8) — that scope rule is enforced in tool_discovery, not
here; this module assumes it already holds.

Wired now: google_calendar, outlook_calendar, superhuman (calendar leg). Unknown
provider degrades (returns the neutral intent verbatim / None deep-link).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Neutral time-window field → provider field name.
_WINDOW_FIELDS = {
    "google_calendar": {"start": "timeMin", "end": "timeMax"},
    "outlook_calendar": {"start": "startDateTime", "end": "endDateTime"},
    "superhuman": {"start": "start", "end": "end"},
}

# RSVP acceptance field + accepted value per provider (A4 — the "am I going"
# signal differs by stack).
_RSVP = {
    "google_calendar": {"field": "responseStatus", "accepted": "accepted"},
    "outlook_calendar": {"field": "responseStatus", "accepted": "accepted"},
    "superhuman": {"field": "response_status", "accepted": "accepted"},
}

# Per-calendar/account routing field (N4 — Google exposes calendarId; Graph uses
# the calendar path; Superhuman routes by account).
_CALENDAR_ADDRESSING = {
    "google_calendar": "calendarId",
    "outlook_calendar": "calendarId",
    "superhuman": "account",
}

_DEEP_LINK = {
    "google_calendar": "https://calendar.google.com/calendar/event?eid={id}",
    "outlook_calendar": "https://outlook.office.com/calendar/item/{id}",
}


def window_fields(provider: Optional[str]) -> Dict[str, str]:
    """Neutral→provider field names for a time window. {} for unknown."""
    return dict(_WINDOW_FIELDS.get((provider or "").lower(), {}))


def compile_window(start: Any, end: Any, provider: Optional[str]) -> Dict[str, Any]:
    """Build the provider's time-window query args from neutral start/end."""
    fields = window_fields(provider)
    if not fields:
        return {"start": start, "end": end}
    return {fields["start"]: start, fields["end"]: end}


def rsvp_field(provider: Optional[str]) -> Dict[str, str]:
    """{field, accepted} for reading whether the user accepted an event."""
    return dict(_RSVP.get((provider or "").lower(), {"field": "responseStatus", "accepted": "accepted"}))


def is_accepted(event: Dict[str, Any], provider: Optional[str]) -> bool:
    """True iff the user's RSVP on `event` is accepted, using the provider's
    field name. Tolerant of a missing field (defaults to not-accepted)."""
    spec = rsvp_field(provider)
    val = event.get(spec["field"]) if isinstance(event, dict) else None
    return str(val or "").strip().lower() == spec["accepted"]


def calendar_addressing_field(provider: Optional[str]) -> Optional[str]:
    """The field that targets a specific calendar/account (N4). None = the
    connector can't target a specific calendar → caller degrades."""
    return _CALENDAR_ADDRESSING.get((provider or "").lower())


def deep_link(provider: Optional[str], native_id: Optional[str],
              returned_url: Optional[str] = None) -> Optional[str]:
    """Prefer the connector's returned URL; else a per-provider host; else None
    (degrade, never a broken link — N8)."""
    if returned_url:
        return returned_url
    host = _DEEP_LINK.get((provider or "").lower())
    if host and native_id:
        return host.format(id=native_id)
    return None


__all__ = [
    "window_fields", "compile_window", "rsvp_field", "is_accepted",
    "calendar_addressing_field", "deep_link",
]
