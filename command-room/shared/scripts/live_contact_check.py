#!/usr/bin/env python3
"""
Shared live-contact-check helper for dormancy / cadence-break / pattern-flag skills.

WHY THIS EXISTS (v3.13.7+):

Session 22 testing (2026-05-21 → 2026-05-22) surfaced TWO parallel bugs:

- Bug #5 — the Pulse chat's Step 1b live-contact check was Gmail-only (that
  chat is RETIRED as of LIFECYCLE1; the bug and this helper long outlived it —
  every dormancy detector calls it, and the history is why the shape is what
  it is). The Calendar
  half was specified in the override-event schema (`source: "gmail" | "calendar"`)
  but never wired. Anyone whose contact cadence is meeting-based but NOT
  email-based (board members, recurring 1:1s, executive coaching) hit false
  "going quiet" flags.

- Bug #28 — dormant-customer-scan had NO live-check before flagging.
  Substrate-only scan flagged a dormant-looking client contact;
  meanwhile that contact had a
  calendar meeting 3 weeks ago that wasn't written to events.jsonl. Real
  CEO-trust miss caught only by Cowork's diagnostic confirm-prompt.

Both bugs collapsed into ONE shared architectural fix: every dormancy /
cadence-flagging skill should overlay live Gmail+Calendar signals on
substrate state BEFORE surfacing a flag. ONE helper, multiple consumers,
enforced via canonical-helper dispatch gate per the v3.13.7 enforcement-gate
architectural theme.

THE LLM-DRIVEN ORCHESTRATION CONTRACT

This helper does NOT invoke MCP tools directly (only the LLM-driven
orchestrator can do that). The contract is two-step:

  STEP 1 — caller asks the helper which tools to invoke:

      from live_contact_check import discover_live_check_tools

      lookup = discover_live_check_tools(available_tools)
      # lookup = {
      #   'mail_search_tool_id': 'mcp__abc__gmail_search_threads' | None,
      #   'mail_search_failed_reason': str | None,
      #   'calendar_tool_id': 'mcp__abc__google_calendar_find_events' | None,
      #   'calendar_failed_reason': str | None,
      # }

  STEP 2 — caller invokes the discovered tools, then asks the helper to merge:

      from live_contact_check import live_contact_check

      result = live_contact_check(
          workspace_root,
          person_id="person_005",
          external_signals={
              'gmail_last_iso':       '2026-05-20',          # date YYYY-MM-DD
              'gmail_detail':         {'subject': 'Q3 OKR sync', 'thread_url': 'https://mail...'},
              'calendar_last_iso':    '2026-05-15',
              'calendar_detail':      {'title': '1:1 coffee', 'event_url': 'https://calendar...'},
              'gmail_failed_reason':  None,
              'calendar_failed_reason': None,
          },
          window_days=7,
      )

      # result = {
      #   'last_contact_iso': '2026-05-20',
      #   'source': 'gmail',
      #   'sources_checked': ['substrate', 'gmail', 'calendar'],
      #   'sources_failed':  [],
      #   'substrate_iso':   '2026-05-09',
      #   'gmail_iso':       '2026-05-20',
      #   'calendar_iso':    '2026-05-15',
      #   'detail':          {'subject': 'Q3 OKR sync', 'thread_url': 'https://mail...'},
      #   'window_days':     7,
      #   'person_id':       'person_005',
      # }

WHEN TO CALL IT

ALWAYS before surfacing a dormancy / cadence-break / "going quiet" /
"pattern-break" flag. Per the v3.13.7 enforcement-gate contract, no
dormancy-flagging skill may emit a flag without first invoking
live_contact_check and respecting its result.

Consumers as of v3.13.7:
  - skills/enable-command-room-schedules/references/orchestrator-dont-forget.md
    (originally the Pulse chat's Phase 3 Step 1b — that chat is RETIRED per
    LIFECYCLE1; Bug #5's Calendar half lands here and every dormancy detector
    calls it)
  - skills/dormant-customer-scan/SKILL.md
    (Step 1 — Bug #28 lands here)

Future consumers (audit at v3.13.8): people-crm "who haven't I heard from",
transcript-search recency filters, weekly-recap "going quiet" promotion.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Iterable, Optional

from tool_discovery import (
    discover_calendar_tool,
    discover_mail_search_tool,
    ToolDescriptor,
)


SOURCE_SUBSTRATE = "substrate"
SOURCE_GMAIL = "gmail"
SOURCE_CALENDAR = "calendar"
SOURCE_NONE = "none"

ALL_SOURCES = (SOURCE_SUBSTRATE, SOURCE_GMAIL, SOURCE_CALENDAR)


def _entities_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "entities.json"


def _load_entities(workspace_root: Path) -> dict:
    return json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))


def _find_person(entities: dict, person_id: str) -> Optional[dict]:
    """Walk the entities.json structure tolerating both nested
    {entities: {people: [...]}} and flat {people: [...]} shapes.
    """
    if not person_id:
        return None
    candidates: list[Any] = []
    container = entities.get("entities") if isinstance(entities, dict) else None
    if isinstance(container, dict):
        candidates.append(container.get("people"))
    candidates.append(entities.get("people") if isinstance(entities, dict) else None)
    for people in candidates:
        if not isinstance(people, list):
            continue
        for p in people:
            if isinstance(p, dict) and p.get("id") == person_id:
                return p
    return None


def _coerce_iso_date(value: Optional[str]) -> Optional[str]:
    """Accept ISO date (YYYY-MM-DD) or ISO datetime; return YYYY-MM-DD string
    suitable for max() comparison alongside substrate `last_interaction` dates.
    Returns None for empty / unparseable input rather than raising — live
    signals are best-effort.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Date form: YYYY-MM-DD
    try:
        _dt.date.fromisoformat(s)
        return s
    except ValueError:
        pass
    # Datetime form: take the date prefix. Handle trailing Z and offsets.
    try:
        normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
        dt = _dt.datetime.fromisoformat(normalized)
        return dt.date().isoformat()
    except ValueError:
        # As a last resort, accept the first 10 chars if they look like a date.
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            try:
                _dt.date.fromisoformat(s[:10])
                return s[:10]
            except ValueError:
                return None
        return None


def discover_live_check_tools(tools: Iterable[ToolDescriptor]) -> dict:
    """Resolve the Gmail-search and Calendar-find tool IDs the caller needs
    in order to fetch live signals. Returns a dict the orchestrator can read
    directly; never raises.

    The orchestrator should:
      1. Call this once per fire (tool registry is stable for a session)
      2. If `mail_search_tool_id` is None, log `mail_search_failed_reason`
         to sources_failed and skip the Gmail half (continue with calendar +
         substrate)
      3. If `calendar_tool_id` is None, same for the Calendar half
      4. Invoke discovered tools to fetch latest signals
      5. Hand results to `live_contact_check()`

    Returns:
        {
          'mail_search_tool_id': str | None,
          'mail_search_failed_reason': str | None,
          'calendar_tool_id': str | None,
          'calendar_failed_reason': str | None,
        }
    """
    mail = discover_mail_search_tool(tools)
    cal = discover_calendar_tool(tools, operation="find_events")
    return {
        "mail_search_tool_id": mail.tool_id,
        "mail_search_failed_reason": mail.reason or None,
        "calendar_tool_id": cal.tool_id,
        "calendar_failed_reason": cal.reason or None,
    }


def live_contact_check(
    workspace_root: str | Path,
    person_id: str,
    *,
    external_signals: Optional[dict] = None,
    window_days: int = 7,
) -> dict:
    """Merge substrate `last_interaction` with live Gmail + Calendar signals.

    Args:
      workspace_root: absolute path to the user's workspace (the folder
        containing `_hq/data/entities.json`).
      person_id: canonical `person_NNN` ID. The caller is expected to have
        resolved this via entity_resolve.py BEFORE calling here — this
        function does NOT fuzzy-match names.
      external_signals: dict from the LLM-driven orchestrator after invoking
        the discovered Gmail + Calendar tools. Keys (all optional):
          - gmail_last_iso: ISO date or datetime string for the latest Gmail
            thread touching the person in the window (None if none / lookup
            failed)
          - gmail_detail: dict (subject, thread_url, etc.) — opaque to this
            helper; surfaced through `detail` if Gmail wins
          - gmail_failed_reason: str if the lookup failed (caller logs to
            sources_failed)
          - calendar_last_iso, calendar_detail, calendar_failed_reason:
            mirror of the above for Calendar
      window_days: how far back the caller is looking. Recorded in the
        result for traceability; not used in the merge math.

    Returns:
      Canonical dict (see module docstring for shape). Never raises on
      missing / malformed external signals — those become sources_failed
      entries. Raises FileNotFoundError if entities.json doesn't exist
      (caller's workspace is mis-pointed).
    """
    workspace_root = Path(workspace_root)
    external_signals = external_signals or {}

    # Substrate
    entities = _load_entities(workspace_root)
    person = _find_person(entities, person_id)
    if person is None:
        substrate_iso = None
        substrate_failed_reason = f"person {person_id!r} not found in entities.json"
    else:
        substrate_iso = _coerce_iso_date(person.get("last_interaction"))
        substrate_failed_reason = None

    # Gmail
    gmail_iso = _coerce_iso_date(external_signals.get("gmail_last_iso"))
    gmail_failed_reason = external_signals.get("gmail_failed_reason") or None
    gmail_detail = external_signals.get("gmail_detail") or None

    # Calendar
    calendar_iso = _coerce_iso_date(external_signals.get("calendar_last_iso"))
    calendar_failed_reason = external_signals.get("calendar_failed_reason") or None
    calendar_detail = external_signals.get("calendar_detail") or None

    # Merge — max across the three sources, with source attribution. Ties go
    # to the live signal (Calendar > Gmail > substrate) so the user sees the
    # freshest concrete touchpoint when timestamps coincide.
    candidates: list[tuple[str, Optional[str], Optional[dict]]] = [
        (SOURCE_CALENDAR, calendar_iso, calendar_detail),
        (SOURCE_GMAIL, gmail_iso, gmail_detail),
        (SOURCE_SUBSTRATE, substrate_iso, None),
    ]
    winning_source = SOURCE_NONE
    winning_iso: Optional[str] = None
    winning_detail: Optional[dict] = None
    for source, iso, detail in candidates:
        if iso is None:
            continue
        if winning_iso is None or iso > winning_iso:
            winning_iso = iso
            winning_source = source
            winning_detail = detail

    # Track which sources had a lookup failure (so callers can degrade with
    # an honest "I couldn't check Calendar" surface rather than silently
    # treating absence-of-signal as evidence-of-absence).
    sources_failed: list[str] = []
    if substrate_failed_reason:
        sources_failed.append(SOURCE_SUBSTRATE)
    if gmail_failed_reason:
        sources_failed.append(SOURCE_GMAIL)
    if calendar_failed_reason:
        sources_failed.append(SOURCE_CALENDAR)

    return {
        "last_contact_iso": winning_iso,
        "source": winning_source,
        "sources_checked": list(ALL_SOURCES),
        "sources_failed": sources_failed,
        "substrate_iso": substrate_iso,
        "gmail_iso": gmail_iso,
        "calendar_iso": calendar_iso,
        "detail": winning_detail,
        "window_days": window_days,
        "person_id": person_id,
        # Per-source failure reasons surfaced for the orchestrator to render
        # in plain English ("(Calendar lookup skipped — connector not
        # connected)") rather than silently treating as no-signal.
        "substrate_failed_reason": substrate_failed_reason,
        "gmail_failed_reason": gmail_failed_reason,
        "calendar_failed_reason": calendar_failed_reason,
    }


__all__ = [
    "discover_live_check_tools",
    "live_contact_check",
    "SOURCE_SUBSTRATE",
    "SOURCE_GMAIL",
    "SOURCE_CALENDAR",
    "SOURCE_NONE",
    "ALL_SOURCES",
]


if __name__ == "__main__":
    # Smoke test against a synthetic workspace fixture.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "_hq" / "data").mkdir(parents=True)
        (ws / "_hq" / "data" / "entities.json").write_text(
            json.dumps({
                "version": 1,
                "entities": {
                    "people": [
                        {"id": "person_001", "canonical_name": "Sam Sample",
                         "last_interaction": "2026-05-09"},
                        {"id": "person_002", "canonical_name": "Bo Stone",
                         "last_interaction": None},
                    ],
                },
            }),
            encoding="utf-8",
        )

        # Case 1: substrate only
        r = live_contact_check(ws, "person_001")
        assert r["last_contact_iso"] == "2026-05-09", r
        assert r["source"] == "substrate", r
        print("Case 1 (substrate only):", r["last_contact_iso"], r["source"])

        # Case 2: Gmail wins
        r = live_contact_check(ws, "person_001", external_signals={
            "gmail_last_iso": "2026-05-20", "gmail_detail": {"subject": "Q3"},
        })
        assert r["last_contact_iso"] == "2026-05-20", r
        assert r["source"] == "gmail", r
        assert r["detail"]["subject"] == "Q3", r
        print("Case 2 (Gmail wins):", r["last_contact_iso"], r["source"])

        # Case 3: Calendar wins (ties broken to live signals)
        r = live_contact_check(ws, "person_001", external_signals={
            "gmail_last_iso": "2026-05-15", "calendar_last_iso": "2026-05-22T18:00:00Z",
            "calendar_detail": {"title": "1:1"},
        })
        assert r["last_contact_iso"] == "2026-05-22", r
        assert r["source"] == "calendar", r
        print("Case 3 (Calendar wins):", r["last_contact_iso"], r["source"])

        # Case 4: substrate null + no signals → source=none
        r = live_contact_check(ws, "person_002")
        assert r["last_contact_iso"] is None, r
        assert r["source"] == "none", r
        print("Case 4 (no signal):", r["last_contact_iso"], r["source"])

        # Case 5: person not found
        r = live_contact_check(ws, "person_999")
        assert r["substrate_failed_reason"], r
        assert "substrate" in r["sources_failed"], r
        print("Case 5 (missing person):", r["substrate_failed_reason"])

        # Case 6: failure reason propagation
        r = live_contact_check(ws, "person_001", external_signals={
            "calendar_failed_reason": "Calendar MCP not connected",
        })
        assert "calendar" in r["sources_failed"], r
        assert r["source"] == "substrate", r
        print("Case 6 (Calendar failed):", r["sources_failed"])

        # Case 7: discover_live_check_tools returns shape
        fake_tools = [
            ToolDescriptor("mcp__abc__gmail_search_threads", "Gmail Search", ""),
            ToolDescriptor("mcp__abc__google_calendar_find_events", "Cal find", ""),
        ]
        d = discover_live_check_tools(fake_tools)
        assert d["mail_search_tool_id"], d
        assert d["calendar_tool_id"], d
        print("Case 7 (discovery):", d["mail_search_tool_id"], "/", d["calendar_tool_id"])

        print("\nAll live_contact_check smoke tests passed.")
