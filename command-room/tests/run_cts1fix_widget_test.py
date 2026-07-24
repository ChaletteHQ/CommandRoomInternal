#!/usr/bin/env python3
"""CTS1FIX E1 — selected primary verb label must be dark-on-gold, not gold-on-gold.

A selected primary widget button carries cr-action + cr-action-primary +
cr-selected. The gold `color` on `.cr-action-primary` tied on specificity with
the dark `color` on `.cr-selected` and, being later in source, won — painting
the selected label gold on its own gold background (invisible). E1 adds a
3-class rule that outranks both. This test renders a view with a primary
button through the real renderer and asserts the emitted stylesheet carries
the new rule with the dark color. House convention: non-zero exit = fail.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from chat_output_renderer import render_chat_output_widget  # noqa: E402

view = {"surface": "commitments", "title": "t", "sections": [{"title": "S",
    "items": [{"n": "1", "name": "Row", "actions": ["resolved", "mark done"]}]}]}
html = render_chat_output_widget(view, wrapper="fragment")
assert "button.cr-action.cr-action-primary.cr-selected" in html, "E1 rule missing"
seg = html[html.find("button.cr-action.cr-action-primary.cr-selected"):][:120]
assert "#14110F" in seg, "E1 rule present but not dark"   # minifier may drop spaces
print("run_cts1fix_widget_test: E1 OK")

# A1 — a body edit auto-arms the row's Draft verb so Apply enables and the
# edited body serializes. Assert the two new functions and the listener bind
# survive into the emitted page for an email-shaped row.
view2 = {"surface": "commitments", "title": "t", "sections": [{"title": "S",
    "items": [{"n": "9", "name": "Nudge Bo",
        "metadata": [["To", "bo.sample@example.com"], ["Subject", "Ping"]],
        "body_lines": ["Hi Bo,", "", "Ping.", "", "Sam"],
        "actions": ["send", "draft", "snooze 3d"]}]}]}
html2 = render_chat_output_widget(view2, wrapper="fragment")
assert "function crRowArmed" in html2, "A1 crRowArmed missing"
assert "function crBodyEdited" in html2, "A1 crBodyEdited missing"
# The bind itself, not just the selector elsewhere: the minifier is line-level,
# so selector + addEventListener must share one emitted line.
assert any(".cr-eb-body[contenteditable]" in ln and "addEventListener" in ln
           for ln in html2.split("\n")), "A1 body-edit listener bind missing"
print("run_cts1fix_widget_test: A1 OK")

# C1a — crToggleNote must resolve the note field row-first, not by a global
# first-match on data-note-for-n. Assert the emitted crToggleNote carries the
# closest() row pin (both row classes) and the row-first querySelectorAll.
# The tightener may collapse "(row || document)" to "(row||document)".
import re  # noqa: E402
fn_start = html2.find("function crToggleNote")
assert fn_start != -1, "C1a crToggleNote missing from emitted page"
fn_seg = html2[fn_start:fn_start + 1200]
assert "closest(" in fn_seg, "C1a closest() missing"
assert ".cr-sub-item" in fn_seg, "C1a .cr-sub-item row class missing"
assert ".cr-item" in fn_seg, "C1a .cr-item row class missing"
assert re.search(r"\(row\s*\|\|\s*document\)", fn_seg), \
    "C1a row-first (row || document) querySelectorAll missing"
print("run_cts1fix_widget_test: C1a OK")

# C1b — structural regression for the empty-id collision: two sub_items with NO
# "id" key both emit data-note-for-n="". The row-scoped JS depends on each row
# containing exactly its own toggle+field pair with matching n — assert that
# containment property directly on the rendered HTML.
view3 = {"surface": "commitments", "title": "t", "sections": [{"title": "S",
    "items": [{"n": "p1", "name": "Parent", "actions": ["resolved", "add to my plate"],
        "sub_items": [{"summary": "Step one", "actions": ["resolved", "skip"]},
                      {"summary": "Step two", "actions": ["resolved", "skip"]}]}]}]}
html3 = render_chat_output_widget(view3, wrapper="fragment")
chunks = html3.split('class="cr-sub-item"')[1:]
assert len(chunks) == 2, f"C1b expected 2 sub-item chunks, got {len(chunks)}"
for idx, chunk in enumerate(chunks):
    toggles = re.findall(r'cr-note-toggle[^>]*data-note-for-n="([^"]*)"', chunk)
    fields = re.findall(r'cr-note-field[^>]*data-note-for-n="([^"]*)"', chunk)
    assert len(toggles) == 1, f"C1b sub-item {idx}: {len(toggles)} toggles, want 1"
    assert len(fields) == 1, f"C1b sub-item {idx}: {len(fields)} fields, want 1"
    assert toggles[0] == fields[0], \
        f"C1b sub-item {idx}: toggle n={toggles[0]!r} != field n={fields[0]!r}"
print("run_cts1fix_widget_test: C1b OK")

# F1 — TRAIN-MERGE review 2026-07-21 (F-1, HIGH): the A1 auto-arm composed with
# WG1-A's arm-IS-dispatch on single-item pages, so the FIRST input event in a
# single-item email body fired crBodyEdited -> crToggle -> crSingleDispatch ->
# crApplyAll -> sendPrompt with zero clicks — a `draft` M never approved
# (2026-07-15 draft-posture ruling). Neither side's tests composed the two.
# The fix guards the auto-arm on !crSingleItem; edit-then-click still ships the
# edited body because crApplyAll's FB-10 block serializes it at click time.
# The battery has no JS engine, so assert the composition at the string level:
# the minifier is line-level (never joins lines), so the guard and the auto-arm
# condition must share one emitted line inside crBodyEdited.
assert 'class="cr-card cr-card-single"' in html2, \
    "F1 probe not real — A1's one-item view no longer renders single-item"
fn = html2.find("function crBodyEdited")
assert fn != -1, "F1 crBodyEdited missing from emitted single-item page"
body_edited = html2[fn:html2.find("function crSingleDispatch", fn)]
assert body_edited.strip(), "F1 crBodyEdited segment extraction came up empty"
arm_lines = [ln for ln in body_edited.split("\n") if "crRowArmed(row)" in ln]
assert len(arm_lines) == 1, \
    f"F1 expected exactly one auto-arm condition line, got {len(arm_lines)}"
assert "!crSingleItem" in arm_lines[0], \
    "F1 REGRESSION: crBodyEdited auto-arm not guarded on crSingleItem — " \
    "first keystroke in a single-item email body dispatches an unapproved draft"
assert "crToggle(" in body_edited, \
    "F1 multi-item auto-arm gone — crBodyEdited no longer arms Draft at all " \
    "(the guard must skip single-item pages, not delete the Bug-A fix)"
print("run_cts1fix_widget_test: F1 OK")

# F1b — the multi-item side of the composition: a two-email-row page keeps the
# body-edit listener bound and is NOT single-item, so the runtime flag is false
# and the A1 auto-arm still fires there (Apply enables on a body edit).
view4 = {"surface": "commitments", "title": "t", "sections": [{"title": "S",
    "items": [{"n": "9", "name": "Nudge Bo",
        "metadata": [["To", "bo.sample@example.com"], ["Subject", "Ping"]],
        "body_lines": ["Hi Bo,", "", "Ping.", "", "Sam"],
        "actions": ["send", "draft", "snooze 3d"]},
        {"n": "10", "name": "Nudge Ada",
         "metadata": [["To", "ada.sample@example.com"], ["Subject", "Ping 2"]],
         "body_lines": ["Hi Ada,", "", "Ping.", "", "Sam"],
         "actions": ["send", "draft", "snooze 3d"]}]}]}
html4 = render_chat_output_widget(view4, wrapper="fragment")
assert 'class="cr-card cr-card-single"' not in html4, \
    "F1b two-row view unexpectedly rendered as single-item"
assert "function crBodyEdited" in html4, "F1b crBodyEdited missing on multi-item"
assert any(".cr-eb-body[contenteditable]" in ln and "addEventListener" in ln
           for ln in html4.split("\n")), "F1b body-edit listener bind missing"
print("run_cts1fix_widget_test: F1b OK")
