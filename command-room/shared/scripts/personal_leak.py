#!/usr/bin/env python3
"""
Personal-content leak scanner (SPEC PGUARD1 D2) — the last line of defense
for the person/personal-data firewall.

WHY THIS EXISTS
---------------
The 2026-07-18 personal-side audit (item 7) found every leak scanner in the
plugin — docx_leak_scanner, chat_output_renderer's _LEAK_PATTERNS,
widget_transport's validate pass — blind to PERSONAL content. A personal
reminder ("call Mom", a family dinner) that reached a board pack or client
deliverable would sail through every gate, because the gates only knew about
internal IDs, substrate paths, and voice tells. This module adds the personal
axis, and the three validators wire it in SURFACE-GATED:

  - org / board / client / external surfaces  → BLOCKING finding
  - m_facing / owner surfaces                 → never blocks (personal content
                                                is legitimate there)
  - unknown / absent surface                  → never blocks (the risk rule:
                                                never default an m_facing
                                                surface to org; only a caller
                                                that DECLARES an org surface
                                                gets the block)

WHAT IT CAN AND CANNOT CATCH (stated honestly)
----------------------------------------------
Marker-based, not semantic. It catches the structural fingerprints a
personal-lane row leaves when it reaches a rendered surface: reminder ids
(`rem_<ULID>` — reminders should never render on ANY non-owner surface),
literal `personal: true` flags / `data-personal` attributes / `[personal]`
chips, `tie: personal` markers (BAL1's tie field), and the
balance-nudge event-type token. It cannot know that the STRING "dinner with
Sam" is personal — that classification lives on the ROW (`is_personal`), and
the row-level firewall (events_io.iter_events_org_scoped, reminders.py's
surface gate) is the layer that keeps classified rows out of org data views.
This scanner is the backstop for the row that slips through anyway.

Pure stdlib; no substrate reads.
"""
from __future__ import annotations

import re
from typing import List, Optional

# Reminder event family — mirrored from reminders.REMINDER_TYPES (kept as a
# literal so this module stays import-free / usable inside events_io without
# a cycle; run_personal_firewall_test pins the two lists equal).
_REMINDER_TYPES = ("reminder", "reminder_updated", "reminder_cleared")

# The BAL1 personal-lane event family: the Sunday nudge
# (balance.compute_balance) and the `book` confirm-path linkage
# (balance.record_actioned — a code writer since OI-3 B-1 2026-07-26; it was
# prose-only, which is what that finding closed). Type pinned in both SKILL
# prose sites, equality asserted by run_fu_pretest_pins_test.
_PERSONAL_EVENT_TYPES = ("balance_nudge_suggested", "balance_nudge_actioned")


# ---------------------------------------------------------------------------
# Surface classification — the gate that keeps this scanner from ever
# blocking an owner-facing render.
# ---------------------------------------------------------------------------

# Surfaces where personal content is a LEAK. Explicit allowlist of org tokens
# (normalized: lowercase, `_`→`-`): a surface tag must DECLARE itself org/
# board/client/external to get the blocking scan. Anything else — m_facing,
# staff-meeting, commitments, None, a tag we've never seen — is treated as
# not-org, per the PGUARD1 risk rule: never default an owner surface to org.
ORG_OUTPUT_SURFACES = frozenset({
    "org", "board", "client", "client-facing", "external",
    "board-pack", "board-pack-assembler", "advisor-export", "value-receipt",
})


def is_org_surface(surface: Optional[str]) -> bool:
    """True iff `surface` explicitly declares an org/board/client/external
    audience (the surfaces where a personal finding is BLOCKING). None or an
    unrecognized tag → False — the safe direction for owner surfaces."""
    if not surface or not isinstance(surface, str):
        return False
    return surface.strip().lower().replace("_", "-") in ORG_OUTPUT_SURFACES


# ---------------------------------------------------------------------------
# Row classification — is this event/row personal-lane?
# ---------------------------------------------------------------------------

def _data(row: dict) -> dict:
    d = row.get("data")
    return d if isinstance(d, dict) else {}


def is_personal(row, masks=None) -> bool:
    """True when `row` (an events.jsonl event dict) belongs to the personal
    lane and must never feed an org/board/client/external output:

      - a reminder-family row whose effective `personal` flag is true —
        explicit `data.personal: true`, or (for a bare `reminder`) the D3
        default: no business ref (`data.ref`) and no `primary_thread_id`.
        A person reference alone does NOT make it work (D3 — "call Mom").
        Flag-less `reminder_updated` / `reminder_cleared` rows are personal
        too: they carry only the reminder id (unclassifiable without a
        join), an `edit` can carry a revised personal summary, and no org
        surface consumes lane-management rows — unknown fails closed;
      - a BAL1 personal-lane row (`_PERSONAL_EVENT_TYPES`: the Sunday
        `balance_nudge_suggested` nudge and the `book` confirm-path
        `balance_nudge_actioned` linkage — type alone classifies);
      - a row carrying `tie: "personal"` (top-level or in data — BAL1's
        personal-tie marker on person-scoped rows);
      - when `masks` (a frozenset of account_ids from
        account_scope_gate.live_masks*) is given: a row whose account
        identity matches a live mask — masked-personal history.

    Never raises; a junk row is not personal (the account-scope wall and the
    defensive loaders own junk handling)."""
    try:
        if not isinstance(row, dict):
            return False
        t = row.get("type")
        d = _data(row)
        if t in _REMINDER_TYPES:
            personal = d.get("personal")
            if personal is None:
                if t == "reminder":
                    # Mirror of the reminders.py D3 default: org/thread refs
                    # make it work; a bare person reference does not.
                    personal = not (d.get("ref") or row.get("primary_thread_id"))
                else:
                    # reminder_updated / reminder_cleared carry only the
                    # reminder id — they cannot be classified without a join,
                    # an `edit` update can carry a revised personal summary,
                    # and no org surface consumes lane-management rows at
                    # all. Unknown → personal (fail closed for org output).
                    personal = True
            if bool(personal):
                return True
        if t in _PERSONAL_EVENT_TYPES:
            return True
        if row.get("tie") == "personal" or d.get("tie") == "personal":
            return True
        if masks:
            try:
                from account_scope_gate import _event_account_ids
            except ImportError:  # pragma: no cover — direct-path fallback
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from account_scope_gate import _event_account_ids
            if _event_account_ids(row) & set(masks):
                return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rendered-text scan — the validator-side backstop.
# ---------------------------------------------------------------------------

# Word-boundary-anchored patterns, same finding shape as
# docx_leak_scanner.scan_text_for_leaks so the validators can merge findings.
PERSONAL_LEAK_PATTERNS: list[tuple[str, re.Pattern]] = [
    # A reminder id on ANY rendered surface this scan runs on is a leak:
    # reminders render only on owner surfaces (show-my-reminders, the brief),
    # and those never invoke the org-gated scan.
    ("personal_reminder_id",
     re.compile(r"\brem_[0-9A-Za-z]{10,}\b", re.IGNORECASE)),
    # Literal personal flags — JSON-ish (`"personal": true`), key-value
    # (`personal: true` / `personal=true`), and the HTML wire attribute.
    ("personal_flag",
     re.compile(r"[\"']?personal[\"']?\s*[:=]\s*[\"']?true\b", re.IGNORECASE)),
    ("personal_wire_attr",
     re.compile(r"\bdata-personal\s*=\s*[\"']true[\"']", re.IGNORECASE)),
    # Rendered personal chips — `[personal]` / `(personal)` row badges.
    ("personal_chip",
     re.compile(r"[\[\(]\s*personal\s*[\]\)]", re.IGNORECASE)),
    # BAL1 tie marker rendered as text.
    ("personal_tie",
     re.compile(r"[\"']?tie[\"']?\s*[:=]\s*[\"']?personal\b", re.IGNORECASE)),
    # BAL1 personal-lane event type tokens (see _PERSONAL_EVENT_TYPES note).
    ("personal_event_type",
     re.compile(r"\bbalance_nudge_(?:suggested|actioned)\b", re.IGNORECASE)),
]


def scan_for_personal_leak(text_or_html) -> List[dict]:
    """Scan rendered output (chat text, widget HTML, extracted docx text) for
    personal-lane fingerprints. Returns findings shaped exactly like
    docx_leak_scanner.scan_text_for_leaks — {name, pattern, match, context} —
    empty list = clean. Never raises; the CALLER decides whether findings
    block (org/board/client surfaces) or are ignored (owner surfaces)."""
    if not text_or_html or not isinstance(text_or_html, str):
        return []
    findings: List[dict] = []
    for name, pat in PERSONAL_LEAK_PATTERNS:
        for m in pat.finditer(text_or_html):
            start, end = m.span()
            findings.append({
                "name": name,
                "pattern": pat.pattern,
                "match": m.group(0),
                "context": text_or_html[max(0, start - 20):
                                        min(len(text_or_html), end + 20)],
            })
    return findings


__all__ = [
    "ORG_OUTPUT_SURFACES",
    "PERSONAL_LEAK_PATTERNS",
    "is_org_surface",
    "is_personal",
    "scan_for_personal_leak",
]
