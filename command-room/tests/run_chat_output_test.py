#!/usr/bin/env python3
"""
Unit tests for chat_output_renderer.py + chat_output_validator.py.

Run via: python3 plugin-source-v2/tests/run_chat_output_test.py

Tests cover:
  - Renderer: header/sub-header, sections, items with all field combos,
    sub-items, dividers, Quick Read, bulk actions, italic body, pill rows,
    artifact links, sources
  - Validator: each category catches the right leaks, doesn't false-positive
    on valid output
  - Round-trip: render valid data → validate → 0 violations
  - Negative cases: render with bad data → validator catches each known leak
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts to path
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from chat_output_renderer import render_chat_output  # noqa: E402
from chat_output_validator import validate_chat_output  # noqa: E402


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}{(' — ' + detail) if detail else ''}")
        raise AssertionError(label)


# ---------- Renderer tests ----------


def test_minimal_render():
    print("test_minimal_render")
    data = {
        "header": "Inbox · Apr 28 · 1 priority thread",
        "sections": [
            {
                "title": None,
                "count": None,
                "items": [
                    {"n": 1, "icon": "✉", "name": "Sam Sample", "subject": "Q2 deck", "actions": ["1 send", "1 skip"]}
                ],
            }
        ],
    }
    out = render_chat_output(data)
    _check("contains header", "Inbox · Apr 28" in out)
    _check("contains item number prefix", "**1.**" in out)
    _check("contains bold name", "**Sam Sample**" in out)
    _check("contains pill marker", "▸ 1 send" in out)
    _check("ends with newline", out.endswith("\n"))


def test_full_email_item():
    print("test_full_email_item")
    data = {
        "header": "Inbox · Apr 28 · 1 priority thread. Drafts ready to review.",
        "sub_header": "Noise filtered (49 total): listings (24), marketing (11), calendar (9), security (2), self-test (3)",
        "sections": [
            {
                "title": "OVERDUE",
                "count": 1,
                "items": [
                    {
                        "n": 1,
                        "icon": "✉",
                        "name": "Sam Sample",
                        "subject": "Q2 deck — your thoughts",
                        "context_tag": "replies your thread, 2 days aging",
                        "metadata": [
                            ("Subject", "Re: Q2 deck — your thoughts"),
                            ("To", "sam@example.com"),
                        ],
                        "body_lines": [
                            "Hey Sam —",
                            "Got your renderer-pipeline diagnostic. Read it last night.",
                            "Real reply by Friday EOD.",
                            "MD",
                        ],
                        "sources": [{"label": "the diagnostic dump", "url": "https://mail.google.com/mail/u/0/#inbox/abc123"}],
                        "actions": ["1 send", "1 to drafts", "1 edit", "1 escalate to memo", "1 skip"],
                    }
                ],
            }
        ],
        "quick_read": "items 1+2 are technical replies that are mostly already drafted",
        "bulk_actions": ["send all", "to drafts all", "show more", "skip all"],
    }
    out = render_chat_output(data)
    _check("section header rendered", "## OVERDUE (1)" in out)
    _check("metadata Subject rendered", "Subject: Re: Q2 deck — your thoughts" in out)
    _check("To: rendered", "To: sam@example.com" in out)
    _check("Body: label present", "Body:" in out)
    _check("body italicized line 1", "*Hey Sam —*" in out)
    _check("body italicized line 2", "*Real reply by Friday EOD.*" in out)
    _check("source link rendered", "[the diagnostic dump]" in out)
    _check("Quick Read blockquote", "> **Quick read:**" in out)
    _check("bulk actions row", "▸ send all" in out)
    _check("divider between section and Quick Read", "---" in out)


def test_multiple_items_with_dividers():
    print("test_multiple_items_with_dividers")
    data = {
        "header": "test",
        "sections": [
            {
                "title": None,
                "items": [
                    {"n": 1, "icon": "✉", "name": "A", "actions": ["1 send"]},
                    {"n": 2, "icon": "✉", "name": "B", "actions": ["2 send"]},
                    {"n": 3, "icon": "✉", "name": "C", "actions": ["3 send"]},
                ],
            }
        ],
    }
    out = render_chat_output(data)
    # Should have 2 dividers between 3 items
    _check("two between-item dividers", out.count("\n---\n") == 2, f"got {out.count(chr(10) + '---' + chr(10))}")


def test_sub_items():
    print("test_sub_items")
    data = {
        "header": "test",
        "sections": [
            {
                "title": None,
                "items": [
                    {
                        "n": 1,
                        "icon": "📅",
                        "name": "Tate",
                        "subject": "AI tool onboarding",
                        "sub_items": [
                            {
                                "id": "1a",
                                "summary": "New person mentioned by Sam — Adam Sadanic",
                                "actions": ["1a add to [org]", "1a manually", "1a skip"],
                            },
                            {
                                "id": "1b",
                                "summary": "Vague timing: lunch with Drew Friday",
                                "actions": ["1b set [date]", "1b skip"],
                            },
                        ],
                    }
                ],
            }
        ],
    }
    out = render_chat_output(data)
    _check("sub-item 1a present", "**1a.**" in out)
    _check("sub-item 1b present", "**1b.**" in out)
    _check("sub-item 1a actions", "▸ 1a add to [org]" in out)
    _check("sub-item 1b actions", "▸ 1b set [date]" in out)


def test_artifact_link():
    print("test_artifact_link")
    data = {
        "header": "test",
        "sections": [
            {
                "title": None,
                "items": [
                    {
                        "n": 1,
                        "icon": "📅",
                        "name": "Mira",
                        "subject": "Pricing review",
                        "artifact_link": {
                            "label": "Open full brief",
                            "path": "./Mira Fragrances/meetings/Call_Prep_2026-04-29.docx",
                        },
                        "actions": ["open mira", "skip mira"],
                    }
                ],
            }
        ],
    }
    out = render_chat_output(data)
    _check("artifact link with 📄 prefix", "📄 [Open full brief]" in out)
    _check("artifact link path embedded", "Call_Prep_2026-04-29.docx" in out)


def test_required_n_field():
    print("test_required_n_field")
    data = {
        "header": "test",
        "sections": [{"title": None, "items": [{"icon": "✉", "name": "Missing N"}]}],
    }
    try:
        render_chat_output(data)
        _check("renderer rejected missing N", False, "renderer should have raised ValueError")
    except ValueError as e:
        _check("renderer rejected missing N", "n" in str(e).lower())


# ---------- Validator tests ----------


def test_validator_clean():
    print("test_validator_clean")
    text = (
        "Inbox · Apr 28 · 1 priority thread. Drafts ready to review.\n"
        "\n"
        "Noise filtered (49 total): listings (24), marketing (11)\n"
        "\n"
        "**1.** ✉ **Sam Sample** · \"Q2 deck\" — replies your thread\n"
        "\n"
        "Subject: Re: Q2 deck\n"
        "To: sam@example.com\n"
        "\n"
        "Body:\n"
        "*Hey Sam —*\n"
        "*Real reply Friday.*\n"
        "*MD*\n"
        "\n"
        "▸ 1 send  ▸ 1 to drafts  ▸ 1 skip\n"
    )
    result = validate_chat_output(text)
    _check("clean output passes", result.ok, result.summary())


def test_validator_catches_entity_id_leak():
    print("test_validator_catches_entity_id_leak")
    text = "**1.** Aspen Hardware (project_010) is stale.\n"
    result = validate_chat_output(text)
    _check("project_NNN flagged", any(v.category == "entity_id_leak" for v in result.violations))


def test_validator_catches_phase_label_leak():
    print("test_validator_catches_phase_label_leak")
    text = "Phase 7 — silent memory updates\nPhase 8 — chat turn\n"
    result = validate_chat_output(text)
    _check("phase label flagged", any(v.category == "phase_label_leak" for v in result.violations))


def test_validator_catches_internal_path_leak():
    print("test_validator_catches_internal_path_leak")
    text = "Logged: events.jsonl + staging_emissions.jsonl appended\n"
    result = validate_chat_output(text)
    _check("internal path flagged", any(v.category == "internal_path_leak" for v in result.violations))


def test_validator_catches_empty_subject():
    print("test_validator_catches_empty_subject")
    text = "**1.** ✉ **Sam** · \"Some subject\"\n\nSubject: Re:\nTo: sam@x.com\n"
    result = validate_chat_output(text)
    _check("empty Subject: Re: flagged", any(v.category == "empty_subject" for v in result.violations))


def test_validator_catches_telemetry():
    print("test_validator_catches_telemetry")
    text = "Logged: pack_run seq 203 (commitments, status ok). 3 draft_created events.\n"
    result = validate_chat_output(text)
    _check("pack_run seq flagged", any(v.category == "telemetry_narration" for v in result.violations))


def test_validator_catches_threshold_rationale():
    print("test_validator_catches_threshold_rationale")
    text = "(Degraded baseline mode for 14 of 17. Most are obs=1.)\n"
    result = validate_chat_output(text)
    _check("threshold rationale flagged", any(v.category == "threshold_rationale" for v in result.violations))


def test_validator_catches_missing_item_number():
    print("test_validator_catches_missing_item_number")
    # Item icon at start of line WITHOUT N. prefix anywhere nearby
    text = "✉ Sam Sample just emailed.\n"
    result = validate_chat_output(text)
    _check("missing N. flagged", any(v.category == "missing_item_number" for v in result.violations))


def test_validator_catches_flag_name():
    print("test_validator_catches_flag_name")
    text = "(--force, surfacing back to Apr 22)\n"
    result = validate_chat_output(text)
    _check("--force flagged", any(v.category == "flag_name_leak" for v in result.violations))


def test_validator_catches_engineer_phrasing():
    print("test_validator_catches_engineer_phrasing")
    text = "Files were force re-emitted with provenance footer applied.\n"
    result = validate_chat_output(text)
    _check("engineer phrasing flagged", any(v.category == "engineer_phrase_leak" for v in result.violations))


def test_round_trip():
    print("test_round_trip")
    data = {
        "header": "Inbox · Apr 28 · 1 priority thread",
        "sections": [
            {
                "title": "OVERDUE",
                "count": 1,
                "items": [
                    {
                        "n": 1,
                        "icon": "✉",
                        "name": "Sam Sample",
                        "subject": "Q2 deck",
                        "context_tag": "replies your thread",
                        "metadata": [("Subject", "Re: Q2 deck"), ("To", "sam@example.com")],
                        "body_lines": ["Hey Sam —", "Real reply Friday.", "MD"],
                        "actions": ["1 send", "1 skip"],
                    }
                ],
            }
        ],
        "quick_read": "scan and send",
    }
    rendered = render_chat_output(data)
    result = validate_chat_output(rendered)
    _check("rendered output validates clean", result.ok, result.summary())


def test_widget_action_input_attribute_contract():
    """Sam Apr 29 regression: Add-context textarea didn't appear on click.

    The click handler `crToggle` finds the textarea wrapper by matching the
    button's `data-action` to the wrapper's `data-input-for-action`. If those
    two strings ever drift apart for a given action, the lookup returns null
    and the textarea stays hidden. This test pins the contract: every action
    that triggers an input affordance must emit a button AND a wrapper, and
    their action attributes must be byte-identical.
    """
    from chat_output_renderer import render_chat_output_widget
    import re

    print("test_widget_action_input_attribute_contract")
    data = {
        "header": "Upcoming meetings",
        "sections": [{
            "title": None,
            "items": [{
                "n": 1,
                "name": "Sam Sample",
                "subject": "Q2 deck",
                "metadata": [],
                "body_lines": [],
                "actions": [
                    "add more context [text]",
                    "ask question [text]",
                    "push meeting [date]",
                    "skip",
                ],
            }],
        }],
        "widget_mode": "all_batch_widget",
    }
    html = render_chat_output_widget(data)
    # T2.2 row diet: verbs render as <option>s in the row's dropdown; the
    # contract is unchanged — an input-affordance action's option value and
    # its wrapper's data-input-for-action must be byte-identical.
    opt_actions = set(re.findall(
        r'<option value="([^"]+)" data-input-type="(?!none)[^"]+"', html
    ))
    wrapper_actions = set(re.findall(
        r'<div class="cr-action-input"[^>]*data-input-for-action="([^"]+)"', html
    ))
    expected_inputs = {
        "add more context [text]",
        "ask question [text]",
        "push meeting [date]",
    }
    _check(
        "all input-affordance verbs render as dropdown options",
        expected_inputs.issubset(opt_actions),
        f"missing options: {expected_inputs - opt_actions}",
    )
    _check(
        "every input-affordance option has a matching wrapper (byte-identical action)",
        expected_inputs.issubset(wrapper_actions),
        f"missing wrappers: {expected_inputs - wrapper_actions}",
    )
    _check(
        "wrapper lookup uses iterating dataset comparison (not querySelector w/ CSS.escape)",
        "dataset.inputForAction" in html and "querySelectorAll('.cr-action-input')" in html,
        "crWrap appears to use the old CSS.escape path",
    )


def test_cryptic_sub_id_namespaces_hidden():
    """Sam Apr 29: D1/U07/Person IDs in widgets read as cryptic codes.

    Sub-item IDs of the form `<letter><digits>` (e.g. d1/d2 dormant proposals,
    e1/e2 entity proposals, r1/r2 review items) are routing namespaces — they
    add no signal for the user. The widget should hide that label and show
    only the summary text. Parent-letter IDs (7a/7b) DO carry context (groups
    items under #7) and should still display.
    """
    from chat_output_renderer import render_chat_output_widget
    import re

    print("test_cryptic_sub_id_namespaces_hidden")
    data = {
        "header": "Pulse",
        "sections": [{
            "title": None,
            "items": [{
                "n": 1,
                "name": "Sam",
                "subject": "scope",
                "metadata": [],
                "body_lines": [],
                "actions": ["skip"],
                "sub_items": [
                    {"id": "1a", "summary": "Sub-item under parent 1", "actions": ["mark received", "skip"]},
                    {"id": "d1", "summary": "Aspen dormant?", "actions": ["active", "skip"]},
                    {"id": "e2", "summary": "Track Acme Co as prospect?", "actions": ["confirm [type]", "skip"]},
                ],
            }],
        }],
        "widget_mode": "all_batch_widget",
    }
    html = render_chat_output_widget(data)
    visible_ids = re.findall(r'<span class="cr-sub-id"><strong>([^<]+)</strong>', html)
    _check("parent-letter sub-id (1a) is visible", "1a" in visible_ids)
    _check("cryptic dormant namespace (d1) is suppressed", "d1" not in visible_ids)
    _check("cryptic entity namespace (e2) is suppressed", "e2" not in visible_ids)
    sub_data_ids = set(re.findall(r'data-sub-id="([^"]+)"', html))
    _check(
        "routing ids stay in data-sub-id even when label is hidden",
        {"d1", "e2", "1a"}.issubset(sub_data_ids),
    )


def test_per_subitem_note_field():
    """v2.14.33: per-sub-item context note field rendered for every sub-item.

    Pre-v2.14.33 the cr-item-note (always-visible context textarea added in
    v2.14.28) was only emitted for parent items. Sub-items 1a/1b/5c/etc.
    inherited their parent's note field, but the parent's note's
    data-note-for-n was the parent's `n` (e.g. "5"), so when the user picked
    a SUB-item action ("5a add as person to Org"), the apply-choices payload
    couldn't find a matching note (the lookup is by exact n match).

    M's testing 2026-05-07: "the context box does not open anything to write
    also, that needs to be an option for all items". Fix: emit a per-sub-item
    cr-item-note inside each cr-sub-item div, with data-note-for-n matching
    the sub-id.
    """
    from chat_output_renderer import render_chat_output_widget
    import re

    print("test_per_subitem_note_field")
    data = {
        "header": "Past Meetings",
        "sections": [{
            "title": None,
            "items": [{
                "n": 5,
                "name": "Sloan",
                "subject": "AI engineering hire",
                "metadata": [],
                "body_lines": ["track record"],
                "actions": [],
                "sub_items": [
                    {"id": "5a", "summary": "new person mentioned", "actions": ["5a add as person to Chalette Holdings", "5a add context [text]", "5a add to my list", "5a skip"]},
                    {"id": "5b", "summary": "scheduling coordinator", "actions": ["5b add as person to Chalette Holdings", "5b add context [text]", "5b add to my list", "5b skip"]},
                    {"id": "5c", "summary": "decision needed", "actions": ["5c decide [text]", "5c add context [text]", "5c add to my list", "5c skip"]},
                ],
            }],
        }],
        "widget_mode": "all_batch_widget",
    }
    html = render_chat_output_widget(data)

    # v2.14.36+ — collapsible "+ Add context" toggle replaces the always-visible
    # textarea. Each sub-item still gets its own note wrapper + note field with
    # data-note-for-n; the change is the textarea is hidden by default and a
    # toggle button reveals it. Test the wrapper, the toggle button, and the
    # textarea (now starts hidden).
    for sid in ("5a", "5b", "5c"):
        # T2.2 row diet: the outer div lost its (unused) data attr; the
        # toggle + field carry data-note-for-n, which is what the JS reads.
        pattern = f'data-note-for-n="{sid}">+ Add context</button>'
        _check(f"sub-item {sid} has its own note toggle", pattern in html)
        # The toggle button with the right note-for-n
        # T2.2 row diet: note toggles are JS-bound (no inline onclick).
        toggle_pattern = f'<button class="cr-note-toggle" type="button" data-note-for-n="{sid}">+ Add context</button>'
        _check(f"sub-item {sid} has its own '+ Add context' toggle button", toggle_pattern in html)
        # The hidden-by-default note input still has matching data-note-for-n
        input_pattern = f'data-note-for-n="{sid}" style="display:none;" />'
        _check(f"sub-item {sid} note input has matching data-note-for-n (hidden by default)", input_pattern in html)

    # Parent item should still have its own note field too (untouched by v2.14.33).
    _check(
        "parent item still has its own (non-sub) note field",
        'data-note-for-n="5">+ Add context</button>' in html,
    )

    # Total: 1 parent + 3 sub-items = 4 cr-note-field elements
    note_field_count = html.count('class="cr-note-field"')
    _check(
        f"4 note-field inputs rendered (1 parent + 3 sub-items), got {note_field_count}",
        note_field_count == 4,
    )

    # v2.14.36+ — verify all 4 toggle buttons (1 parent + 3 sub-items) rendered
    toggle_count = html.count('class="cr-note-toggle"')
    _check(
        f"4 '+ Add context' toggle buttons rendered (1 parent + 3 sub-items), got {toggle_count}",
        toggle_count == 4,
    )

    # v2.14.36+ — JS handler `crToggleNote` is wired up in the widget script
    _check(
        "crToggleNote JS handler present",
        "function crToggleNote(btn)" in html,
    )


def test_universal_add_context_button_on_pending_subitems():
    """v2.14.33: every pending sub-item action set carries `add context [text]`.

    M's repeated ask: he wants Add context as an option on EVERY item, not
    just missing-person/missing-org. Pre-v2.14.33, Vague timing / Decision
    needed / Sensitive decision sub-items had no Add context button. Per
    orchestrator-past-meetings.md v2.14.33+ — every pending type's action
    set includes `add context [text]`.

    This test verifies that when the orchestrator emits an action set
    INCLUDING `add context [text]`, the renderer renders an Add context
    button for that sub-item with the right input-wrapper plumbing.
    (Spec compliance lives in the orchestrator markdown; this is the
    renderer-side guarantee that the action label round-trips.)
    """
    from chat_output_renderer import render_chat_output_widget
    import re

    print("test_universal_add_context_button_on_pending_subitems")
    # Vague timing + decision-needed sub-items, both with the v2.14.33 universal add-context.
    data = {
        "header": "Past Meetings",
        "sections": [{
            "title": None,
            "items": [{
                "n": 4,
                "name": "Acme Logistics",
                "subject": "follow-up",
                "metadata": [],
                "body_lines": [],
                "actions": [],
                "sub_items": [
                    {"id": "4a", "summary": "vague timing", "actions": ["4a set date [when]", "4a add context [text]", "4a add to my list", "4a skip"]},
                    {"id": "4b", "summary": "decision needed", "actions": ["4b decide [text]", "4b add context [text]", "4b add to my list", "4b skip"]},
                ],
            }],
        }],
        "widget_mode": "all_batch_widget",
    }
    html = render_chat_output_widget(data)

    # Each sub-item's dropdown should carry the Add-context option (T2.2 —
    # verbs render as <option>s; the wrapper contract is unchanged).
    for sid in ("4a", "4b"):
        sel_match = re.search(
            rf'<select class="cr-action-select" data-n="{sid}"[^>]*>(.*?)</select>',
            html, re.DOTALL)
        _check(f"sub-item {sid} renders a verb dropdown", sel_match is not None)
        _check(
            f"sub-item {sid} dropdown carries add context [text]",
            sel_match is not None
            and 'value="add context [text]"' in sel_match.group(1),
        )
        # And a matching input wrapper for that option (the wrapper machinery — what's broken in field).
        wrapper_pattern = f'data-input-for-n="{sid}" data-input-for-action="add context [text]"'
        _check(f"sub-item {sid} has matching add-context input wrapper", wrapper_pattern in html)


def test_validate_rendered_widget_clean_passes():
    """v2.14.34: validate_rendered_widget passes when the renderer's output
    is shipped byte-for-byte (the canonical happy path)."""
    from chat_output_renderer import render_chat_output_widget, validate_rendered_widget

    print("test_validate_rendered_widget_clean_passes")
    data = {
        "header": "Commitments",
        "sections": [{"title": None, "items": [
            {"n": 1, "name": "D", "subject": "x",
             "metadata": [("To", "d@example.com"), ("Subject", "x")],
             "body_lines": ["a", "b"],
             "actions": ["1 send", "1 edit then send", "1 draft", "1 skip"]},
        ]}],
        "widget_mode": "all_batch_widget",
    }
    html = render_chat_output_widget(data)
    # Should not raise
    validate_rendered_widget(html)
    _check("clean rendered HTML passes validate_rendered_widget", True)


def test_validate_rendered_widget_catches_dropped_wrapper():
    """v2.14.34: the structural failure mode that bit cr-commitments on
    2026-05-07. Agent post-minified the renderer's HTML and dropped the
    `<div class="cr-item-inputs">` block, leaving buttons with no
    matching wrappers. validate_rendered_widget must catch this and
    raise WrapperContractError.
    """
    from chat_output_renderer import render_chat_output_widget, validate_rendered_widget, WrapperContractError
    import re

    print("test_validate_rendered_widget_catches_dropped_wrapper")
    data = {
        "header": "Commitments",
        "sections": [{"title": None, "items": [
            {"n": 1, "name": "D", "subject": "x",
             "metadata": [("To", "d@example.com"), ("Subject", "x")],
             "body_lines": ["a", "b"],
             "actions": ["1 send", "1 edit then send", "1 draft", "1 skip"]},
        ]}],
        "widget_mode": "all_batch_widget",
    }
    html = render_chat_output_widget(data)
    # Simulate the agent "minification" — drop the cr-item-inputs block
    mangled = re.sub(r'<div class="cr-item-inputs">.*?</div>(?=<div|</div>)', "", html, flags=re.DOTALL)
    _check(
        "manipulation actually changed the HTML",
        len(mangled) < len(html),
        f"original {len(html)} bytes, mangled {len(mangled)} bytes",
    )
    raised = False
    try:
        validate_rendered_widget(mangled)
    except WrapperContractError as e:
        raised = True
        msg = str(e)
        _check("error message names the missing action", "edit then send" in msg)
        _check("error message names the input type", "multi-field-email" in msg)
        _check(
            "error message guides agent to the fix",
            "byte-for-byte" in msg.lower() or "without modification" in msg.lower(),
        )
    _check("WrapperContractError raised on dropped wrapper", raised)


def test_validate_rendered_widget_no_input_actions_pass():
    """An item whose only actions are zero-input (skip / mark received)
    should pass validation even without any cr-action-input wrappers —
    the validator only flags buttons whose data-input-type !== "none"."""
    from chat_output_renderer import render_chat_output_widget, validate_rendered_widget

    print("test_validate_rendered_widget_no_input_actions_pass")
    data = {
        "header": "Self",
        "sections": [{"title": None, "items": [
            {"n": 4, "name": "Sam", "subject": "deep work",
             "metadata": [], "body_lines": [],
             "actions": ["4 prep deep work", "4 mark done", "4 skip"]},
        ]}],
        "widget_mode": "all_batch_widget",
    }
    html = render_chat_output_widget(data)
    validate_rendered_widget(html)  # no raise expected
    _check("zero-input action set passes validation", True)


def test_validate_rendered_widget_handles_subitem_wrappers():
    """Sub-item input-needing actions (e.g. `5a add context [text]`) also
    require their own wrapper, with data-input-for-n matching the sub-id.
    Must validate cleanly when emitted via the canonical renderer."""
    from chat_output_renderer import render_chat_output_widget, validate_rendered_widget, WrapperContractError
    import re

    print("test_validate_rendered_widget_handles_subitem_wrappers")
    data = {
        "header": "Past Meetings",
        "sections": [{"title": None, "items": [
            {"n": 5, "name": "Sloan", "subject": "hire",
             "metadata": [], "body_lines": ["topic"],
             "actions": [],
             "sub_items": [
                 {"id": "5a", "summary": "new person",
                  "actions": ["5a add as person to Chalette Holdings", "5a add context [text]", "5a skip"]},
                 {"id": "5c", "summary": "decision needed",
                  "actions": ["5c decide [text]", "5c add context [text]", "5c skip"]},
             ]},
        ]}],
        "widget_mode": "all_batch_widget",
    }
    html = render_chat_output_widget(data)
    validate_rendered_widget(html)
    _check("sub-item wrappers pass clean validation", True)

    # Strip wrapper for 5a's add context only and confirm the validator catches it
    mangled = re.sub(
        r'<div class="cr-action-input"[^>]*data-input-for-n="5a"[^>]*data-input-for-action="add context \[text\]"[^>]*>.*?</div>',
        "",
        html,
        count=1,
        flags=re.DOTALL,
    )
    raised = False
    try:
        validate_rendered_widget(mangled)
    except WrapperContractError as e:
        raised = True
        _check(
            "raised error correctly identifies the sub-id 5a",
            "'5a'" in str(e),
            f"got: {str(e)[:300]}",
        )
    _check("wrapper drop on a sub-item is caught", raised)


def test_checkmark_css_emits_proper_hex_escape():
    """v2.14.35: the cr-selected ::before content must be the literal CSS
    hex escape `\\2713` (six chars: backslash + 2713) so the browser
    parses U+2713 = ✓.

    Pre-v2.14.35 the Python source had `content: "\\2713"` (single
    backslash) inside a triple-quoted string, which Python parsed as an
    OCTAL escape: `\\271` = 0xB9 = `¹`, then literal `3`. The emitted
    CSS was `content: "¹3"` rendered as visible junk inside selected
    buttons. Cowork's 2026-05-07 diagnostic caught it.

    The fix double-escapes: `content: "\\\\2713"` in Python so the
    emitted CSS is `content: "\\2713"`. This test pins the contract.
    """
    from chat_output_renderer import render_chat_output_widget
    import re

    print("test_checkmark_css_emits_proper_hex_escape")
    # T2.2: row verbs are dropdowns, so the pressed-state button CSS ships
    # only on button widgets (conditional emission). The octal-escape
    # contract still guards the button block — pin it on the full sheet.
    import chat_output_renderer as _r
    html = _r._WIDGET_CSS_MIN
    m = re.search(r'cr-selected::before\s*\{\s*content:\s*"([^"]*)"', html)
    _check("cr-selected::before content rule found", m is not None)
    if m:
        emitted = m.group(1)
        _check(
            f"emitted content is the literal CSS escape `\\2713` (got {emitted!r})",
            emitted == "\\2713",
        )
        # Belt-and-suspenders: confirm no UTF-8 corruption byte sequence
        emitted_bytes = emitted.encode("utf-8")
        _check(
            "emitted bytes are 5 ASCII chars (no UTF-8 multi-byte)",
            emitted_bytes == b"\\2713",
            f"got {emitted_bytes!r}",
        )


def test_render_self_validates_wrapper_invariant():
    """v2.14.35: render_chat_output_widget runs validate_rendered_widget
    on its own output before returning. Defense in depth — even if the
    orchestrator forgets the explicit call, the renderer can never
    return HTML with a missing wrapper.

    This is more architectural than testable directly (the canonical
    render path can't have missing wrappers because the same code emits
    both buttons and wrappers). But we can confirm the validator is
    actually being called by checking that broken data shapes still
    work end-to-end (validate_rendered_widget should pass on any
    canonical render).
    """
    from chat_output_renderer import render_chat_output_widget

    print("test_render_self_validates_wrapper_invariant")
    # A spread of action shapes — multi-field-email, textarea, when-text,
    # zero-input — all must round-trip through the self-validation step
    # without raising.
    data = {
        "header": "Mixed",
        "sections": [{"title": None, "items": [
            {"n": 1, "name": "Email", "subject": "draft",
             "metadata": [("To", "x@y.com"), ("Subject", "foo")],
             "body_lines": ["body"],
             "actions": ["1 send", "1 edit then send", "1 draft", "1 push to [date]", "1 skip"]},
            {"n": 2, "name": "DeepWork", "subject": "task",
             "metadata": [], "body_lines": ["something"],
             "actions": ["2 prep deep work", "2 mark done", "2 skip"]},
            {"n": 3, "name": "Pending", "subject": "x",
             "metadata": [], "body_lines": ["x"],
             "actions": [],
             "sub_items": [
                 {"id": "3a", "summary": "vague", "actions": ["3a set date [when]", "3a add context [text]", "3a skip"]},
                 {"id": "3b", "summary": "decision", "actions": ["3b decide [text]", "3b add context [text]", "3b skip"]},
             ]},
        ]}],
        "widget_mode": "all_batch_widget",
    }
    # Should not raise — the renderer's self-validation passes for canonical output
    html = render_chat_output_widget(data)
    _check("mixed action shapes round-trip through self-validation", isinstance(html, str) and len(html) > 1000)


def test_onboarding_setup_widget_renders():
    """v3.4.1: widget_mode 'onboarding_setup' produces a renderable HTML
    widget with selection buttons + optional textarea inputs."""
    from chat_output_renderer import render_chat_output_widget

    print("test_onboarding_setup_widget_renders")
    data = {
        "widget_mode": "onboarding_setup",
        "header": "Quick setup",
        "sub_header": "Three questions",
        "items": [
            {
                "n": 1,
                "icon": "👤",
                "question": "Which best describes your role?",
                "options": [
                    {"action": "run-single-company", "label": "Run the whole company"},
                    {"action": "run-multiple-companies", "label": "Run multiple companies"},
                    {"action": "other", "label": "Other", "input_type": "textarea-text",
                     "placeholder": "Describe in your own words"},
                ],
            },
            {
                "n": 4,
                "icon": "🕐",
                "question": "Timezone?",
                "options": [
                    {"action": "pacific", "label": "Pacific"},
                    {"action": "eastern", "label": "Eastern"},
                ],
            },
        ],
    }
    html = render_chat_output_widget(data)
    _check("returns HTML string", isinstance(html, str) and len(html) > 1000)
    _check("contains widget header", "Quick setup" in html)
    _check("contains role question", "Which best describes your role?" in html)
    _check("contains canonical action label", 'data-action="run-single-company"' in html)
    _check("contains Other label with textarea-text input type", 'data-input-type="textarea-text"' in html)
    _check("contains pacific timezone button", 'data-action="pacific"' in html)
    _check("contains Apply all button", "Apply all" in html)
    _check("contains brand strip", "cr-brand-strip" in html)


def test_onboarding_setup_bypasses_canonical_actions():
    """v3.4.1: onboarding setup actions are NOT in CANONICAL_ACTIONS
    by design (they're selection labels, not action verbs). The
    renderer must skip the canonical-action validator for this mode."""
    from chat_output_renderer import render_chat_output_widget, CANONICAL_ACTIONS

    print("test_onboarding_setup_bypasses_canonical_actions")
    # Confirm onboarding selection labels are NOT in canonical actions
    _check("run-single-company not in CANONICAL_ACTIONS", "run-single-company" not in CANONICAL_ACTIONS)
    _check("pacific not in CANONICAL_ACTIONS", "pacific" not in CANONICAL_ACTIONS)
    _check("eastern not in CANONICAL_ACTIONS", "eastern" not in CANONICAL_ACTIONS)
    # Render with these non-canonical actions — should NOT raise
    data = {
        "widget_mode": "onboarding_setup",
        "header": "Setup",
        "items": [
            {"n": 1, "question": "Role?", "options": [
                {"action": "run-single-company", "label": "Run the whole company"},
                {"action": "nonprofit", "label": "Nonprofit"},
            ]},
        ],
    }
    html = render_chat_output_widget(data)
    _check("render completes without CanonicalActionError", len(html) > 500)


def test_onboarding_setup_textarea_inputs_emit_wrappers():
    """v3.4.1: options with input_type 'textarea-text' must emit matching
    .cr-action-input wrappers so the textarea opens on button click.
    Tests the structural invariant (validate_rendered_widget passes)."""
    from chat_output_renderer import render_chat_output_widget, validate_rendered_widget

    print("test_onboarding_setup_textarea_inputs_emit_wrappers")
    data = {
        "widget_mode": "onboarding_setup",
        "header": "Setup",
        "items": [
            {"n": 3, "question": "Exclusions?", "options": [
                {"action": "none", "label": "None"},
                {"action": "exclude", "label": "Exclude domains",
                 "input_type": "textarea-text", "placeholder": "lawfirm.example.com"},
            ]},
        ],
    }
    html = render_chat_output_widget(data)
    # The Other / Exclude option needs a matching wrapper
    _check(
        "exclude action has matching input wrapper",
        'data-input-for-n="3"' in html and 'data-input-for-action="exclude"' in html,
    )
    _check(
        "wrapper has placeholder text",
        'placeholder="lawfirm.example.com"' in html,
    )
    # validate_rendered_widget should pass — every input-needing button has its wrapper
    validate_rendered_widget(html)
    _check("validate_rendered_widget passes on onboarding setup HTML", True)


def test_path_leak_workspace_path_passes():
    """v3.6.0: chat output containing a path under the runtime-resolved
    $WORKSPACE passes the leak scanner.

    This is the canonical happy path — the orchestrator writes a brief to
    `$WORKSPACE/_hq/meetings/Brief.docx` and surfaces the absolute path in
    chat. The path is under the user's workspace; clicking it resolves;
    no leak.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate

    print("test_path_leak_workspace_path_passes")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    text = (
        "Saved your brief to /sessions/abc123/mnt/My Workspace/_hq/meetings/Brief.docx\n"
        "Click the link above to open it."
    )
    # Should not raise
    renderer_validate(text, workspace=workspace, plugin_root=plugin_root)
    _check("workspace-prefixed path passes the path-leak scanner", True)


def test_path_leak_foreign_drive_path_fails():
    """v3.6.0: chat output containing a Drive path NOT under the user's
    workspace raises LeakDetectedError. This is the v3.5.3 bug class —
    the author's local Drive path leaking into output for users whose
    workspace lives elsewhere.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate, LeakDetectedError

    print("test_path_leak_foreign_drive_path_fails")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    # The author's local Drive path leaks back into chat output
    text = "Saved your brief to /Users/asdas/Desktop/GoogleDrive/CommandRoom/_hq/meetings/Brief.docx"
    raised = False
    try:
        renderer_validate(text, workspace=workspace, plugin_root=plugin_root)
    except LeakDetectedError as e:
        raised = True
        msg = str(e)
        _check("error names the path-leak kind", "path-leak" in msg)
        _check("error includes the offending path", "/Users/asdas" in msg)
        _check(
            "error surfaces Rule 25 path-fix guidance",
            "Rule 25" in msg and "$WORKSPACE" in msg,
        )
    _check("LeakDetectedError raised on foreign Drive path", raised)


def test_path_leak_plugin_root_path_passes():
    """v3.6.0: a path under the installed plugin root passes the scanner.

    Legitimate use case: an orchestrator surfacing a config or
    documentation pointer like 'see your CHANGELOG at $PLUGIN_ROOT/CHANGELOG.md'.
    The plugin root is on the user's machine; clicking resolves; no leak.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate

    print("test_path_leak_plugin_root_path_passes")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    text = "Plugin docs at /sessions/abc123/mnt/.remote-plugins/plugin_xyz/CHANGELOG.md"
    # Should not raise
    renderer_validate(text, workspace=workspace, plugin_root=plugin_root)
    _check("plugin-root path passes the path-leak scanner", True)


def test_path_leak_no_paths_passes():
    """v3.6.0: chat output with no absolute paths passes cleanly even when
    workspace + plugin_root resolution is active. Sanity check that the
    path scanner doesn't false-positive on pathless text.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate

    print("test_path_leak_no_paths_passes")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    text = (
        "Inbox · Apr 28 · 1 priority thread. Drafts ready to review.\n"
        "**1.** ✉ **Sam Sample** · \"Q2 deck\" — replies your thread\n"
        "▸ 1 send  ▸ 1 skip\n"
    )
    renderer_validate(text, workspace=workspace, plugin_root=plugin_root)
    _check("pathless chat output passes cleanly", True)


def test_path_leak_no_op_when_workspace_unresolvable():
    """v3.6.0: when neither workspace nor plugin_root resolves (local pytest,
    no Cowork env), the path scanner no-ops rather than false-alarming.
    The static grep test guards source-doc leaks in CI; runtime no-op
    here is the correct behavior since we have no trusted prefix to
    compare against.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate, _scan_for_path_leaks

    print("test_path_leak_no_op_when_workspace_unresolvable")
    # Direct scanner call with explicit None workspace + None plugin_root
    # bypasses the env-var fallback (passing None to the kwargs would
    # trigger discovery). Use empty-string sentinels to force no-prefix mode.
    findings = _scan_for_path_leaks(
        "/Users/asdas/Desktop/GoogleDrive/CommandRoom/foo.docx",
        workspace="",
        plugin_root="",
    )
    _check(
        "scan no-ops when both prefixes are empty (no trusted prefix)",
        findings == [],
    )


def test_path_leak_windows_path_fails():
    """v3.6.0: Windows-style author paths (C:\\... and C:/...) get caught.
    Cross-platform coverage matters because Command Room runs on Mac, PC,
    and Cowork sandboxes — the author's local path on any of those
    platforms leaks the same way.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate, LeakDetectedError

    print("test_path_leak_windows_path_fails")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    text = "Brief saved at C:\\Users\\asdas\\Desktop\\Command Room\\Brief.docx"
    raised = False
    try:
        renderer_validate(text, workspace=workspace, plugin_root=plugin_root)
    except LeakDetectedError as e:
        raised = True
        # The error uses repr() so backslashes appear doubled in str(e).
        # Match against both `C:` and `asdas` to confirm the right path is named.
        _check("Windows backslash path flagged", "C:" in str(e) and "asdas" in str(e))
    _check("Windows-style author path is caught", raised)


def test_path_leak_cygwin_path_fails():
    """v3.6.0: Cygwin/MSYS-style Windows paths (/c/Users/...) get caught."""
    from chat_output_renderer import validate_chat_output as renderer_validate, LeakDetectedError

    print("test_path_leak_cygwin_path_fails")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    text = "Brief saved at /c/Users/asdas/Desktop/Command Room/Brief.docx"
    raised = False
    try:
        renderer_validate(text, workspace=workspace, plugin_root=plugin_root)
    except LeakDetectedError as e:
        raised = True
        _check("Cygwin-style /c/Users path flagged", "/c/Users/asdas" in str(e))
    _check("Cygwin-style author path is caught", raised)


def test_path_leak_home_relative_path_fails():
    """v3.6.0: home-relative paths (~/...) get caught. The author's
    `~/Desktop/Google Drive/...` was the original v3.5.3 bug class —
    these resolve to the AUTHOR'S home directory on the agent's compute
    machine, not the user's.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate, LeakDetectedError

    print("test_path_leak_home_relative_path_fails")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    text = "Brief saved at ~/Desktop/GoogleDrive/CommandRoom/Brief.docx"
    raised = False
    try:
        renderer_validate(text, workspace=workspace, plugin_root=plugin_root)
    except LeakDetectedError as e:
        raised = True
        _check("home-relative ~/Desktop path flagged", "~/Desktop" in str(e))
    _check("home-relative author path is caught", raised)


def test_path_leak_url_with_users_in_path_no_false_positive():
    """v3.6.0: HTTP/HTTPS URLs that happen to contain `/Users/` or `/home/`
    inside the URL path must NOT trip the path-leak scanner. The lookbehind
    `(?<![A-Za-z0-9.])` blocks matches preceded by alnum (which a URL host
    would always be). A bare `/Users/...` after whitespace IS a filesystem
    path; `https://example.com/Users/...` is not.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate

    print("test_path_leak_url_with_users_in_path_no_false_positive")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    text = (
        "Source: https://example.com/Users/asdas/feed.html\n"
        "Docs: https://docs.python.org/home/index.html\n"
    )
    renderer_validate(text, workspace=workspace, plugin_root=plugin_root)
    _check("HTTP URLs containing /Users/ or /home/ don't false-positive", True)


def test_path_leak_computer_href_path_caught():
    """v3.6.0: `computer:///...` clickable artifact hrefs (Rule 3) ARE
    scanned for path leaks even though hrefs are stripped from the
    primary ID-leak scan. A `computer:///` href pointing at the author's
    Drive path would 404 on user click — same bug class, different surface.

    Exercised via `paths_text=` to mirror how render_chat_output_widget
    feeds the scanner the href-preserved HTML.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate, LeakDetectedError

    print("test_path_leak_computer_href_path_caught")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    # ID-leak text has hrefs stripped (matches the render_chat_output_widget pattern)
    primary_text = '<a href="">📄 Open brief</a>'
    # Path-leak text preserves the href with a foreign Drive path inside it
    paths_text = '<a href="computer:///Users/asdas/Desktop/GoogleDrive/CommandRoom/Brief.docx">📄 Open brief</a>'
    raised = False
    try:
        renderer_validate(primary_text, paths_text=paths_text, workspace=workspace, plugin_root=plugin_root)
    except LeakDetectedError as e:
        raised = True
        _check("computer:/// path leak flagged", "/Users/asdas" in str(e))
    _check("foreign path inside computer:/// href is caught", raised)


def test_path_leak_url_encoded_workspace_path_passes():
    """v3.6.0: a workspace path inside a `computer:///` href is URL-encoded
    (`%20` for spaces). The normalizer URL-decodes before prefix
    comparison so the encoded path still resolves under the workspace.
    """
    from chat_output_renderer import validate_chat_output as renderer_validate

    print("test_path_leak_url_encoded_workspace_path_passes")
    workspace = "/sessions/abc123/mnt/My Workspace"
    plugin_root = "/sessions/abc123/mnt/.remote-plugins/plugin_xyz"
    primary_text = '<a href="">📄 Open brief</a>'
    # URL-encoded workspace path (spaces → %20)
    paths_text = '<a href="computer:///sessions/abc123/mnt/My%20Workspace/_hq/meetings/Brief.docx">📄 Open brief</a>'
    # Should not raise
    renderer_validate(primary_text, paths_text=paths_text, workspace=workspace, plugin_root=plugin_root)
    _check("URL-encoded workspace path normalizes + passes", True)


def test_onboarding_setup_missing_n_raises():
    """v3.4.1: items missing the required 'n' field should raise ValueError
    at render time so the orchestrator catches the data-shape bug fast."""
    from chat_output_renderer import render_chat_output_widget

    print("test_onboarding_setup_missing_n_raises")
    data = {
        "widget_mode": "onboarding_setup",
        "header": "Setup",
        "items": [{"question": "Role?", "options": [{"action": "x", "label": "X"}]}],
    }
    raised = False
    try:
        render_chat_output_widget(data)
    except ValueError as e:
        raised = True
        _check("error message names the missing field", "'n'" in str(e) or "n field" in str(e).lower())
    _check("ValueError raised for missing 'n'", raised)


def test_view_transition_css_echo_caught():
    """EW2+T (F-09): a Cowork platform `::view-transition-group` style block
    echoed into chat text is a leak. The token exists nowhere in
    plugin-produced HTML, so the pattern can't false-positive on widget
    scans — any hit is the byte-relay echo."""
    from chat_output_renderer import scan_for_id_leaks

    print("test_view_transition_css_echo_caught")
    echoed = "::view-transition-group(root) { animation-duration: 0.3s; }"
    findings = scan_for_id_leaks(echoed)
    _check("::view-transition echo flagged",
           any("platform CSS echo" in label for label, _ in findings))
    clean = scan_for_id_leaks("Here are today's drafts, ready for review.")
    _check("plain chat text stays clean",
           not any("platform CSS echo" in label for label, _ in clean))


def _js_open_string_lines(js: str) -> list[tuple[int, str, str]]:
    """Return (line_no, quote_char, line_excerpt) for every line of `js` that
    ENDS inside an open ' or " string literal. Our widget JS never spans a
    string across a raw newline (template literals are unused), so any hit is
    a Python-eaten escape — the t3 FB-1 bug class."""
    problems: list[tuple[int, str, str]] = []
    for ln, line in enumerate(js.split("\n"), 1):
        if line.strip().startswith("//"):
            # Whole-line comments never reach the page (_minify_js drops
            # them) and may carry apostrophes — skip, don't tokenize.
            continue
        i, n, q = 0, len(line), ""
        while i < n:
            c = line[i]
            if c == "\\":
                i += 2
                continue
            if q:
                if c == q:
                    q = ""
            elif c in ("'", '"'):
                q = c
            i += 1
        if q:
            problems.append((ln, q, line[:90]))
    return problems


def test_widget_js_string_literals_close_on_every_line():
    """t3 FB-1 regression (2026-07-16): the t2.2 block refactor dropped the
    double-escape on `out.join('\\\\n')` inside the NON-raw Python triple-quote
    holding _JS_CORE. Python ate the escape and emitted a literal newline
    inside a JS string literal — an unterminated string, a SyntaxError, and a
    100%-dead widget script: no change listeners, counter frozen at
    '0 of N selected', Apply never enables. Confirmed live on commitments,
    staff-meeting, and email-draft cards (one shared template).

    Same class as the v2.14.35 CSS `\\\\2713` octal-escape bug. This test
    tokenizes EVERY emitted JS block (source + minified + the composed
    template) and fails on any line that ends inside an open string literal,
    so no future Python-eaten escape can ship a dead script again."""
    import chat_output_renderer as cor

    print("test_widget_js_string_literals_close_on_every_line")
    blocks = {
        "_JS_SELECT": cor._JS_SELECT,
        "_JS_BUTTONS": cor._JS_BUTTONS,
        "_JS_NOTES": cor._JS_NOTES,
        "_JS_SKIP": cor._JS_SKIP,
        "_JS_CORE": cor._JS_CORE,
        "_JS_SELECT (min)": dict(cor._JS_FEATURE_BLOCKS_MIN)["cr-action-select"],
        "_JS_CORE (min)": cor._JS_CORE_MIN,
        "_WIDGET_JS_TEMPLATE (min)": cor._WIDGET_JS_TEMPLATE_MIN,
    }
    for name, js in blocks.items():
        bad = _js_open_string_lines(js)
        _check(
            f"{name}: every line closes its string literals",
            not bad,
            "; ".join(f"line {ln} open {q!r}: {txt}" for ln, q, txt in bad[:3]),
        )


def test_widget_js_parses_with_node_when_available():
    """Deepest structural layer renderable HTML allows offline: parse the
    fully-composed widget JS with Node when a `node` binary exists (skips
    silently when it doesn't — the string-literal tokenizer above is the
    always-on guard). A parse failure here is the FB-1 class: the entire
    script is dead in the iframe."""
    import shutil
    import subprocess
    import tempfile

    import chat_output_renderer as cor

    print("test_widget_js_parses_with_node_when_available")
    node = shutil.which("node")
    if not node:
        _check("node not on PATH — parse layer skipped (tokenizer still ran)", True)
        return
    js = (
        cor._WIDGET_JS_TEMPLATE_MIN
        .replace("__TOTAL_ITEMS__", "2")
        .replace("__CR_SRC__", '"test"')
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write("new Function(require('fs').readFileSync(__filename + '.src', 'utf8'));")
        probe = f.name
    with open(probe + ".src", "w", encoding="utf-8") as f:
        f.write(js)
    try:
        res = subprocess.run(
            [node, probe], capture_output=True, text=True, timeout=30
        )
        _check(
            "composed widget JS parses under Node",
            res.returncode == 0,
            (res.stderr or "").strip()[:200],
        )
    finally:
        import os as _os

        for p in (probe, probe + ".src"):
            try:
                _os.unlink(p)
            except OSError:
                pass


def test_dropdown_f58_f17_wiring_present():
    """t3 FB-1/FB-2: the F-58 visible-feedback + F-17 required-input contract,
    pinned on the DROPDOWN template (the t2.2 verb-select port). Renders a
    two-row commitments-shaped view and asserts the emitted page carries the
    full working wiring: the select change handler + its bind, the counter /
    Apply / apply-hold reason elements, armed-state CSS, and the F-17 payload
    riding the required option (data-input-required + data-input-thing) with
    its when-input wrapper + inline reason element."""
    from chat_output_renderer import render_chat_output_widget

    print("test_dropdown_f58_f17_wiring_present")
    data = {
        "source_skill": "commitment-triage",
        "header": "Commitments needing action",
        "sections": [
            {
                "title": "You owe",
                "items": [
                    {
                        "n": "cmt_fixture_a",
                        "display_n": 1,
                        "name": "Send the revised proposal to Alex Partner",
                        "actions": ["mark done", "push to [date]", "skip"],
                    },
                    {
                        "n": "cmt_fixture_b",
                        "display_n": 2,
                        "name": "Review the vendor contract draft",
                        "actions": ["mark done", "push to [date]", "skip"],
                    },
                ],
            }
        ],
    }
    html = render_chat_output_widget(data, wrapper="fragment")

    # F-58: the select handler is emitted AND bound.
    _check("crSel handler emitted", "function crSel(" in html)
    _check("change-event bind emitted", "addEventListener('change'" in html)
    _check("counter updater emitted", "function crUpdateCounter(" in html)
    # F-58: visible-feedback elements.
    _check("live counter present", 'id="cr-count"' in html)
    _check("Apply button present", 'id="cr-apply"' in html)
    _check("apply-hold reason line present", 'id="cr-apply-reason"' in html)
    _check("armed-state CSS present", ".cr-select-armed" in html)
    # F-17 on dropdowns: the required payload rides the option.
    _check(
        "required option carries data-input-required",
        'data-input-required="1"' in html,
    )
    _check("required option carries data-input-thing", "data-input-thing=" in html)
    _check("when-input wrapper rendered", 'data-input-type="when-text"' in html)
    _check("inline reason element rendered", "cr-input-reason" in html)
    # The emitted script must be the LIVE one — no unterminated strings.
    import re as _re

    m = _re.search(r"<script>(.*?)</script>", html, _re.DOTALL)
    _check("script block present", m is not None)
    bad = _js_open_string_lines(m.group(1))
    _check(
        "emitted page script has no open string literals",
        not bad,
        "; ".join(f"line {ln}: {txt}" for ln, _, txt in bad[:3]),
    )


def _t3_cluster_view() -> dict:
    """Shared t3 fixture: one commitment-shaped row (Done primary + the
    FB-3 'Later…' merge) and one email-shaped row (Send/Draft primaries,
    FB-10 editable body, FB-12 blockquote markers in body_lines).
    Placeholder people only; no dates (G14-clean)."""
    return {
        "source_skill": "commitments",
        "header": "Daily commitments",
        "sections": [
            {
                "title": "You owe",
                "items": [
                    {
                        "n": "cmt_fx_a",
                        "display_n": 1,
                        "name": "Send the revised proposal to Alex Partner",
                        "actions": ["resolved", "push to [date]", "drop", "skip"],
                    },
                ],
            },
            {
                "title": "Chase drafts",
                "items": [
                    {
                        "n": "cmt_fx_b",
                        "display_n": 2,
                        "name": "Sam Sample",
                        "subject": "Q2 deck follow-up",
                        "metadata": [
                            ("To", "sam@example.com"),
                            ("Subject", "Re: Q2 deck"),
                        ],
                        "body_lines": [
                            "> Hey Sam,",
                            "> ",
                            "> Following up on the Q2 deck — any blockers?",
                            "> Matthew",
                        ],
                        "actions": ["send", "edit then send", "draft", "skip"],
                    },
                ],
            },
        ],
    }


def test_later_merge_drops_snooze_from_dropdown():
    """t3 FB-3 (M ruling): a row carrying both `push to [date]` and
    skip/snooze renders ONE 'Later…' option — the separate snooze dropdown
    option is suppressed. Rows without `push to [date]` keep snooze. Wire
    ids frozen: the merged option's value stays `push to [date]`."""
    from chat_output_renderer import render_chat_output_widget

    print("test_later_merge_drops_snooze_from_dropdown")
    html = render_chat_output_widget(_t3_cluster_view(), wrapper="fragment")
    row1 = html.split('cr-action-select" data-n="cmt_fx_a"')[1].split("</select>")[0]
    _check("row with push-to shows Later…", "Later…" in row1)
    _check("row with push-to hides the snooze option", "Snooze" not in row1)
    _check("merged option keeps the frozen wire id",
           'value="push to [date]"' in row1)
    row2 = html.split('cr-action-select" data-n="cmt_fx_b"')[1].split("</select>")[0]
    _check("row without push-to keeps its snooze option", "Snooze (1 day)" in row2)


def test_primary_verbs_render_as_buttons():
    """t3 FB-4 (M ruling): commitment rows promote Done to a one-tap
    button; email rows promote Send + Draft. Promoted verbs leave the
    dropdown; the tail stays. Buttons carry the frozen wire attributes and
    bind through crToggle (no inline onclick)."""
    from chat_output_renderer import render_chat_output_widget

    print("test_primary_verbs_render_as_buttons")
    html = render_chat_output_widget(_t3_cluster_view(), wrapper="fragment")
    _check("Done primary button on the commitment row",
           'cr-action-primary" type="button" data-n="cmt_fx_a" '
           'data-action="resolved"' in html)
    _check("Send primary button on the email row",
           'data-n="cmt_fx_b" data-action="send"' in html)
    _check("Draft primary button on the email row",
           'data-n="cmt_fx_b" data-action="draft"' in html)
    row1_sel = html.split('cr-action-select" data-n="cmt_fx_a"')[1].split("</select>")[0]
    _check("promoted verb left the dropdown", 'value="resolved"' not in row1_sel)
    _check("button handler emitted", "function crToggle(" in html)
    _check("button CSS emitted (multi-class trigger)", ".cr-selected" in html)
    _check("cross-control exclusivity wired (button clears select)",
           "crQ('.cr-action-select').forEach" in html.split("function crToggle")[1].split("function ")[0])


def test_email_body_inline_editable():
    """t3 FB-10 (M ruling): the email body renders directly editable —
    contenteditable wrapper carrying data-original (the innerText-shaped
    queued text) so Apply serializes the CURRENT on-screen text and the
    orchestrator can diff for the voice-corrections log. Apply-time
    serialization + Reset restore live in the core JS."""
    from chat_output_renderer import render_chat_output_widget

    print("test_email_body_inline_editable")
    html = render_chat_output_widget(_t3_cluster_view(), wrapper="fragment")
    _check("body wrapper is contenteditable",
           '<div class="cr-eb-body" contenteditable="true"' in html)
    _check("data-original stamped", "data-original=" in html)
    _check("serializer emitted", "function crBodyText(" in html)
    _check("apply-time body diff emitted", "crInlineBody(" in html)
    _check("no separate Edit button rendered",
           'data-action="edit then send"' not in html.split("cr-action-primary")[1].split("</div>")[0])
    _check("editable-body CSS emitted", ".cr-eb-body[contenteditable]" in html)


def test_blockquote_markers_stripped_from_widget_body():
    """t3 FB-12: literal `> ` blockquote-convention prefixes in body_lines
    are markdown plumbing — the widget draws its own quote bar. Strip at
    render; storage stays untouched (M verified queued drafts land clean)."""
    from chat_output_renderer import render_chat_output_widget

    print("test_blockquote_markers_stripped_from_widget_body")
    html = render_chat_output_widget(_t3_cluster_view(), wrapper="fragment")
    body = html.split('<div class="cr-eb-body"')[1].split("</blockquote>")[0]
    _check("no literal '> ' prefix in displayed body", "&gt; " not in body)
    _check("body text renders", "Following up on the Q2 deck" in body)
    # A mid-line '>' is content, not a marker.
    from chat_output_renderer import _strip_blockquote_marker
    _check("mid-line > preserved",
           _strip_blockquote_marker("a > b") == "a > b")
    _check("leading marker stripped",
           _strip_blockquote_marker("> hello") == "hello")
    _check("bare marker becomes empty line",
           _strip_blockquote_marker(">") == "")


def test_later_route_and_when_parse():
    """t3 FB-3 dispatch helpers: later_route sends the user's own item to
    defer and everything else to snooze; parse_later_when handles the
    deterministic slice (bare days / ISO dates) and hands NL phrases back
    as None. Dates computed relative to today (G14)."""
    import datetime as _dt

    import commitment_state as cs

    print("test_later_route_and_when_parse")
    me = "person:001"
    own = {"data": {"owner_id": me}}
    theirs = {"data": {"owner_id": "person:002"}}
    unowned = {"data": {}}
    _check("own item routes to defer", cs.later_route(own, me) == "defer")
    _check("their item routes to snooze", cs.later_route(theirs, me) == "snooze")
    _check("unowned routes to snooze", cs.later_route(unowned, me) == "snooze")
    _check("unresolvable user degrades to snooze",
           cs.later_route(own, None) == "snooze")

    today = _dt.date.today()
    now_iso = today.isoformat() + "T09:00:00Z"
    _check("bare days parse",
           cs.parse_later_when("5", now_iso)
           == (today + _dt.timedelta(days=5)).isoformat())
    _check("days with unit parse",
           cs.parse_later_when("3 days", now_iso)
           == (today + _dt.timedelta(days=3)).isoformat())
    past = (today - _dt.timedelta(days=400)).isoformat()  # DATE_GUARD_OK: computed relative to today
    _check("ISO date passes through", cs.parse_later_when(past, now_iso) == past)
    _check("zero rejected", cs.parse_later_when("0", now_iso) is None)
    _check("NL phrase hands off", cs.parse_later_when("friday", now_iso) is None)
    _check("empty hands off", cs.parse_later_when("", now_iso) is None)


def main():
    tests = [
        test_minimal_render,
        test_full_email_item,
        test_multiple_items_with_dividers,
        test_sub_items,
        test_artifact_link,
        test_required_n_field,
        test_validator_clean,
        test_validator_catches_entity_id_leak,
        test_validator_catches_phase_label_leak,
        test_validator_catches_internal_path_leak,
        test_validator_catches_empty_subject,
        test_validator_catches_telemetry,
        test_validator_catches_threshold_rationale,
        test_validator_catches_missing_item_number,
        test_validator_catches_flag_name,
        test_validator_catches_engineer_phrasing,
        test_round_trip,
        test_widget_action_input_attribute_contract,
        test_cryptic_sub_id_namespaces_hidden,
        test_per_subitem_note_field,
        test_universal_add_context_button_on_pending_subitems,
        test_validate_rendered_widget_clean_passes,
        test_validate_rendered_widget_catches_dropped_wrapper,
        test_validate_rendered_widget_no_input_actions_pass,
        test_validate_rendered_widget_handles_subitem_wrappers,
        test_checkmark_css_emits_proper_hex_escape,
        test_render_self_validates_wrapper_invariant,
        test_onboarding_setup_widget_renders,
        test_onboarding_setup_bypasses_canonical_actions,
        test_onboarding_setup_textarea_inputs_emit_wrappers,
        test_path_leak_workspace_path_passes,
        test_path_leak_foreign_drive_path_fails,
        test_path_leak_plugin_root_path_passes,
        test_path_leak_no_paths_passes,
        test_path_leak_no_op_when_workspace_unresolvable,
        test_path_leak_windows_path_fails,
        test_path_leak_cygwin_path_fails,
        test_path_leak_home_relative_path_fails,
        test_path_leak_url_with_users_in_path_no_false_positive,
        test_path_leak_computer_href_path_caught,
        test_path_leak_url_encoded_workspace_path_passes,
        test_onboarding_setup_missing_n_raises,
        test_view_transition_css_echo_caught,
        test_widget_js_string_literals_close_on_every_line,
        test_widget_js_parses_with_node_when_available,
        test_dropdown_f58_f17_wiring_present,
        test_later_merge_drops_snooze_from_dropdown,
        test_primary_verbs_render_as_buttons,
        test_email_body_inline_editable,
        test_blockquote_markers_stripped_from_widget_body,
        test_later_route_and_when_parse,
    ]
    for t in tests:
        t()
    print(f"\n✓ all {len(tests)} tests passed")


if __name__ == "__main__":
    main()
