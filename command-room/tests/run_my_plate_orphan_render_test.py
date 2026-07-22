#!/usr/bin/env python3
"""Regression test — My Plate counterparty-unresolved (Bug #103 orphan) row render.

Two bugs, both in orchestrator-my-plate.md's counterparty-unresolved variant
(the SPEC CTS1 §8.2 orphaned promises), fixed 2026-07-21:

  BUG 1 — the variant defined `context_tag` but never said what `name` should
  be, so the composer filled it with "?" — the row rendered `1. ? · "..."`.
  The "?" is ugly and redundant with the row's own "who was this for?" tag.
  FIX: `name = None` (no lead marker); the SUBJECT is the row's primary text.

  BUG 2 — the same orphan rows attached an EMPTY `original_thread` stub because
  the spec called original_thread "mandatory when source_ref exists". For orphan
  promises the source_ref exists but the body can't hydrate, so the stub rendered
  as a bare "Original thread — Original thread" accordion.
  FIX: attach original_thread ONLY when it actually hydrates; omit the key when
  the body is unavailable.

This test renders the SPEC-COMPLIANT shapes through the real renderer and asserts
the fixed behavior. It deliberately does NOT assert the empty-stub failure mode
(that render behavior is the renderer guard's job — the "Original thread accordion"
build — and is expected to change).

Run via: python3 tests/run_my_plate_orphan_render_test.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from chat_output_renderer import _render_widget_item  # noqa: E402

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


# The counterparty-unresolved variant EXACTLY as the spec now documents it:
# name=None, subject carries the row's primary text, no metadata/body_lines,
# and NO original_thread key (source_ref present but unhydratable).
orphan = {
    "n": 2,
    "icon": None,
    "name": None,
    "subject": "Send Priya the shortlisted developer's profile link",
    "context_tag": "counterparty unresolved — who was this for?",
    "actions": ["2 reassign to [name]", "2 make task", "2 push to [date]",
                "2 resolved", "2 drop", "2 snooze 3d"],
}
orphan_html = _render_widget_item(orphan)

# BUG 1 — no "?" lead. With name=None the renderer must emit no cr-item-name
# span at all, and no bare "?" placeholder anywhere in the header.
check(
    "orphan row renders NO name-lead span (name=None)",
    'class="cr-item-name"' not in orphan_html,
    detail="name=None must skip the lead marker; a cr-item-name span means a placeholder leaked in",
)
check(
    "orphan row renders NO '?' placeholder lead",
    "<strong>?</strong>" not in orphan_html and "cr-item-name" not in orphan_html,
    detail=f"header: {orphan_html[:200]!r}",
)
check(
    "orphan row keeps the SUBJECT as its primary text",
    "shortlisted developer" in orphan_html,
    detail="the commitment subject must carry the row when there is no counterparty name",
)
check(
    "orphan row has NO dangling '· ' separator before the subject",
    'cr-item-subject">· ' not in orphan_html,
    detail=f"name=None must drop the name/subject separator too, not just the name span. header: {orphan_html[:220]!r}",
)
check(
    "orphan row still shows the 'who was this for?' tag",
    "who was this for?" in orphan_html,
)

# BUG 2 — no empty accordion. With original_thread OMITTED, no accordion renders.
check(
    "orphan row renders NO original_thread accordion (key omitted)",
    "cr-orig-summary" not in orphan_html and "Original thread —" not in orphan_html,
    detail="an unhydratable source_ref must attach nothing, not an empty 'Original thread — Original thread' stub",
)

# Positive control — a properly-hydrated original_thread on a normal PROMISED
# row DOES still render the accordion, so the BUG-2 assertion above can't pass
# just because accordions stopped rendering entirely.
hydrated = {
    "n": 1,
    "name": "Sam",
    "subject": "Send Q2 deck",
    "context_tag": "committed Apr 12, 16 days overdue",
    "original_thread": {
        "author": "Sam", "date": "Apr 12", "subject": "Q2 deck: status",
        "body": "Any update on the Q2 deck?", "url": "https://mail.example.com/t/1",
    },
    "metadata": [("To", "sam@example.com"), ("Subject", "Q2 deck: status")],
    "body_lines": ["Deck lands Friday EOD — pulling the final numbers now."],
    "actions": ["1 send", "1 draft", "1 push to [date]", "1 resolved"],
}
hydrated_html = _render_widget_item(hydrated)
check(
    "positive control: hydrated original_thread DOES render an accordion",
    "cr-orig-summary" in hydrated_html,
    detail="a real thread must still render — proves the BUG-2 assertion is meaningful",
)
check(
    "positive control: normal PROMISED row DOES render its name lead",
    'class="cr-item-name"' in hydrated_html and "Sam" in hydrated_html,
)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
