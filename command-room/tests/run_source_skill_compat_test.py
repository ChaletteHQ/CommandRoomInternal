#!/usr/bin/env python3
"""
Client-migration back-compat test (FIX1 Batch A, item 6 — promoted to MANDATORY).

The live per-client workspaces have events.jsonl history written with legacy
`cr-*` source_skill values.
The writers now emit bare forms; events.jsonl is append-only and is NEVER
rewritten. So every downstream reader must still match the old events.

This asserts the normalization layer + the canonical pack_run matcher logic
(`_is_for` in SHARED_CHAT_OUTPUT_PROTOCOL.md) accept BOTH forms — in particular
the spec's required case: an old `cr-inbox` event still matches an `inbox`
filter.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from source_skill_compat import normalize_source_skill, source_skill_matches  # noqa: E402


PASS = 0
FAIL = 0


def check(label: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# ---- normalize_source_skill -------------------------------------------------
check("cr-inbox normalizes to inbox", normalize_source_skill("cr-inbox") == "inbox")
check("cr-commitments normalizes to commitments",
      normalize_source_skill("cr-commitments") == "commitments")
check("cr-past-meetings normalizes to past-meetings",
      normalize_source_skill("cr-past-meetings") == "past-meetings")
check("cr-upcoming-meetings normalizes to upcoming-meetings",
      normalize_source_skill("cr-upcoming-meetings") == "upcoming-meetings")
# the one rename that is NOT a clean prefix strip
check("cr-dont-forget normalizes to pulse (rename, not strip)",
      normalize_source_skill("cr-dont-forget") == "pulse")
check("bare inbox is unchanged", normalize_source_skill("inbox") == "inbox")
check("non-cr value passes through", normalize_source_skill("meeting-notes") == "meeting-notes")
check("None passes through", normalize_source_skill(None) is None)

# ---- source_skill_matches ---------------------------------------------------
check("legacy cr-inbox matches inbox filter", source_skill_matches("cr-inbox", "inbox"))
check("bare inbox matches inbox filter", source_skill_matches("inbox", "inbox"))
check("legacy cr-dont-forget matches pulse filter",
      source_skill_matches("cr-dont-forget", "pulse"))
check("multi-target filter still matches legacy",
      source_skill_matches("cr-commitments", "inbox", "commitments", "past-meetings"))
check("unrelated value does not match", not source_skill_matches("calendar-writer", "inbox"))


# ---- the canonical pack_run matcher (mirror of _is_for in the protocol) ------
def _is_for(e, slug):
    d = e.get("data") or {}
    target = normalize_source_skill(slug)
    return any(
        normalize_source_skill(v) == target
        for v in (e.get("source_skill"), d.get("task_id"), d.get("kind"))
    )


legacy_inbox_event = {
    "type": "pack_run",
    "source_skill": "cr-inbox",      # pre-rename history on a live workspace
    "data": {"kind": None, "surfaced": 4},
}
check("an old cr-inbox pack_run still matches the inbox filter (REQUIRED)",
      _is_for(legacy_inbox_event, "inbox"))

new_inbox_event = {
    "type": "pack_run",
    "source_skill": "inbox",
    "data": {"kind": "inbox"},
}
check("a new bare inbox pack_run matches the inbox filter",
      _is_for(new_inbox_event, "inbox"))

legacy_pulse_event = {
    "type": "pack_run",
    "source_skill": "cr-dont-forget",  # Pulse history before the display-name rename
    "data": {},
}
check("an old cr-dont-forget pack_run matches the pulse filter",
      _is_for(legacy_pulse_event, "pulse"))


print()
if FAIL:
    print(f"FAIL — {FAIL} failed, {PASS} passed")
    sys.exit(1)
print(f"OK — source_skill back-compat: {PASS} passed")
sys.exit(0)
