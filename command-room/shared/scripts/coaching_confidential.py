#!/usr/bin/env python3
"""The coaching confidentiality tier (SPEC_SURFACES2 PROFILE1; design memo §7).

WHY THIS EXISTS
---------------
More than half of the executives who take coaching keep it away from their
board, and the memo makes that a product requirement rather than a courtesy:
coaching notes live in their own tier, they are never in a shared artifact,
never on a team surface, and never on the brief or the wrap. The gate that
already enforces the personal tier (`personal_leak`) is the precedent this
module copies, with ONE deliberate difference in the default direction.

THE DIFFERENCE, STATED. `personal_leak` fails OPEN on an undeclared surface:
a brief legitimately carries personal rows, so only a caller that DECLARES an
org audience gets the block. Coaching fails CLOSED: only a caller that
DECLARES itself a coaching surface is exempt, and an absent or unknown
surface is treated as non-coaching. A confidentiality tier that leaks
whenever a writer forgets to name itself is not a tier.

WHAT IT CATCHES, HONESTLY
-------------------------
Marker-based, like every other leak scanner here. It catches the structural
fingerprints a coaching row leaves when it reaches a surface it should never
reach: the store path itself, the coaching note/session id shapes, the
relationship object's own field names, and the tier marker the coaching
writers stamp on their own text. It cannot know that the sentence "he keeps
deferring the finance conversation" came from a coaching note — that
classification lives on the ROW, and the row-level rule (coaching notes are
read only by the coach surfaces) is the layer that keeps classified rows out.
This scanner is the backstop for the row that slips through anyway.

Pure stdlib. Reads nothing.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional

# The tier's home on disk. Everything under it is coaching-confidential.
COACHING_DIR_PARTS = ("_hq", "coaching")
COACHING_DIR_TOKEN = "_hq/coaching"

# The ONLY surfaces allowed to render coaching text. A surface not named here
# — including an absent one — is non-coaching and a finding is BLOCKING.
COACHING_SURFACES = frozenset({
    "coach",                 # the coach skill's own conversation
    "command-room-coach",
    "coaching",
    "profile-coaching",      # the profile page's own "what you are working on"
    "coaching-export",       # the seat owner's own export of their notes
    # BRIEF2 (SPEC_SURFACES2_11c 2.2 item 3; review F-3, ruling R-10). THE
    # ONE EXCEPTION ON THE BRIEF, AND IT IS A TAG, NOT A HOLE. The morning
    # brief is `morning-brief` — named in NON_COACHING_SURFACES_NAMED below
    # and scanned like any other non-coaching surface. This tag is what the
    # brief declares INSTEAD, and only on the fire where it actually renders
    # the one stated coaching line for a seat that walked the chosen door and
    # left the render switch on (`surface_drivers.brief_coaching_line`'s three
    # gates). `coaching_doors.relationship()`'s docstring has demanded exactly
    # this since PROFILE1 — "every caller that renders from this must declare
    # a coaching surface to the leak scan" — and until this tag existed the
    # line passed the promise by sitting on a path that ran no scan at all.
    # A declared exception a reviewer can grep for is a tier; an unscanned
    # path is not.
    # FIX ROUND 2, REVIEW R1 — AND IT COVERS ONE SENTENCE, NOT THE PAGE.
    # This tag is never handed to `is_coaching_surface` as the brief's
    # surface: that returned early and switched the scan off for the whole
    # brief on exactly the coached mornings. The brief scans as
    # `morning-brief` always, and `surface_drivers.brief_coaching_scan_target`
    # reads this tag to blank the ONE declared line out of the text first.
    "morning-brief-coaching-line",
})

# The surfaces the memo names explicitly as forbidden. Kept as a NAMED set so
# a reviewer can see the four the ruling lists, even though the gate below is
# "anything not in COACHING_SURFACES".
NON_COACHING_SURFACES_NAMED = frozenset({
    "morning-brief", "brief", "end-of-day", "weekly-wrap", "wrap",
    "team", "team-intelligence", "board", "client", "external", "deliverable",
})

# The marker every coaching writer stamps on text it produces, so a note that
# is copied into another surface carries its own tier with it.
COACHING_TEXT_MARKER = "[coaching-note]"

_COACHING_PATTERNS: list[tuple[str, str]] = [
    ("coaching_store_path", r"_hq/coaching\b"),
    ("coaching_note_marker", r"\[coaching-note\]"),
    ("coaching_note_id", r"\bcoach_note_[0-9A-Za-z]+\b"),
    ("coaching_session_id", r"\bcoach_session_[0-9A-Za-z]+\b"),
    ("coaching_relationship_field",
     r"\b(?:coaching_stance|coaching_push|coaching_boundaries|"
     r"coaching_behaviour|coaching_stakeholders)\b"),
]


def coaching_leak_patterns() -> list[tuple[str, str]]:
    """(name, regex) pairs — the tier's fingerprints. Copy, never mutate."""
    return list(_COACHING_PATTERNS)


COACHING_LEAK_LABELS = frozenset(name for name, _p in _COACHING_PATTERNS)


def is_coaching_surface(surface: Optional[str]) -> bool:
    """True iff `surface` explicitly declares itself a coaching surface.

    None, "", a non-string and an unrecognized tag are all NON-coaching —
    the fail-closed direction. This is the inverse of
    `personal_leak.is_org_surface` on purpose; see the module docstring."""
    if not surface or not isinstance(surface, str):
        return False
    return surface.strip().lower().replace("_", "-") in COACHING_SURFACES


def scan_for_coaching_leak(text_or_html) -> List[dict]:
    """Findings for coaching-tier markers in `text_or_html`. Never raises."""
    if not text_or_html or not isinstance(text_or_html, str):
        return []
    findings: List[dict] = []
    for name, pattern in _COACHING_PATTERNS:
        for m in re.finditer(pattern, text_or_html):
            start, end = m.span()
            findings.append({
                "name": name,
                "pattern": pattern,
                "match": m.group(0),
                "context": text_or_html[max(0, start - 20):
                                        min(len(text_or_html), end + 20)],
                "tier": "coaching",
            })
    return findings


def excluded_paths(paths: Iterable) -> List[str]:
    """The subset of `paths` that sits inside the coaching tier — what a
    shared-artifact writer must drop from its inputs before it composes."""
    out: List[str] = []
    for p in paths or ():
        s = str(p).replace("\\", "/")
        if COACHING_DIR_TOKEN in s:
            out.append(str(p))
    return out


def mark(text: str) -> str:
    """Stamp a block of coaching prose with the tier marker, so the same text
    pasted onto another surface is caught by the scan above."""
    if not text:
        return text
    return f"{COACHING_TEXT_MARKER} {text}"


__all__ = [
    "COACHING_DIR_PARTS", "COACHING_DIR_TOKEN", "COACHING_SURFACES",
    "NON_COACHING_SURFACES_NAMED", "COACHING_TEXT_MARKER",
    "COACHING_LEAK_LABELS", "coaching_leak_patterns", "is_coaching_surface",
    "scan_for_coaching_leak", "excluded_paths", "mark",
]
