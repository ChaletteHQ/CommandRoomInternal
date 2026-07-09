#!/usr/bin/env python3
"""
Unit tests for the v2.14.38+ canonical action standardization:
  - `snooze 3d` (fixed-duration snooze, replacing `snooze [duration]` for new widgets)
  - `not relevant` (60-day cooldown dismissal, replacing `skip` on REVIEW + inbox)
  - `add [text]` (REVIEW-item permissive textarea affirmative)
  - Display label override: `snooze 3d` → "Snooze (3 days)"

Plus regression coverage for canonical-action validator: every new action
must be accepted; existing ones still accepted; non-canonical still rejected.

Run via: python3 tests/run_v2_14_38_actions_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from chat_output_renderer import (  # noqa: E402
    CANONICAL_ACTIONS,
    CanonicalActionError,
    _action_display_label,
    is_canonical_action,
    render_chat_output_widget,
)


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}{(' — ' + detail) if detail else ''}")
        raise AssertionError(label)


# ---------- New canonical actions are in the set ----------


def test_snooze_3d_canonical():
    print("test_snooze_3d_canonical")
    _check("`snooze 3d` is canonical", is_canonical_action("snooze 3d"))
    _check("`snooze 3d` in CANONICAL_ACTIONS", "snooze 3d" in CANONICAL_ACTIONS)


def test_not_relevant_canonical():
    print("test_not_relevant_canonical")
    _check("`not relevant` is canonical", is_canonical_action("not relevant"))
    _check("`not relevant` in CANONICAL_ACTIONS", "not relevant" in CANONICAL_ACTIONS)


def test_add_text_canonical():
    print("test_add_text_canonical")
    _check("`add [text]` is canonical", is_canonical_action("add [text]"))
    _check("`add [text]` in CANONICAL_ACTIONS", "add [text]" in CANONICAL_ACTIONS)


def test_snooze_duration_kept_as_backcompat():
    print("test_snooze_duration_kept_as_backcompat")
    # Deprecated but kept for in-flight pre-v2.14.38 widgets
    _check("deprecated `snooze [duration]` still canonical", is_canonical_action("snooze [duration]"))


# ---------- Display labels ----------


def test_snooze_3d_display_label():
    print("test_snooze_3d_display_label")
    _check(
        "`snooze 3d` renders as 'Snooze (3 days)'",
        _action_display_label("snooze 3d") == "Snooze (3 days)",
        f"got: {_action_display_label('snooze 3d')!r}",
    )


def test_not_relevant_display_label_states_duration():
    """v4.5.2 S2 (F-59) REVERSES the v2.14.38 hide-the-TTL decision: every
    mute states its duration on the button. The hidden 60-day cooldown was
    the one-way-door trap M's dogfood flagged."""
    print("test_not_relevant_display_label_states_duration")
    label = _action_display_label("not relevant")
    _check("renders as 'Not relevant (60 days)'", label == "Not relevant (60 days)", f"got: {label!r}")


def test_add_text_display_label():
    print("test_add_text_display_label")
    _check(
        "`add [text]` renders as 'Add' (bracket stripped per default rule)",
        _action_display_label("add [text]") == "Add",
    )


# ---------- Validator integration (renderer accepts the new set) ----------


def test_renderer_accepts_review_item_with_new_action_set():
    """REVIEW item shape with the v2.14.38+ unified set."""
    print("test_renderer_accepts_review_item_with_new_action_set")
    data = {
        "widget_mode": "all_batch_widget",
        "sections": [{
            "title": "REVIEW",
            "items": [{
                "n": 5,
                "name": "Andrea Wetsel",
                "context_tag": "I think you talked Apr 28 — was tracking Apr 14.",
                "actions": ["5 add [text]", "5 not relevant", "5 add to my list"],
            }],
        }],
    }
    html = render_chat_output_widget(data)
    _check("renders without raising", isinstance(html, str) and len(html) > 500)


def test_renderer_accepts_pulse_person_with_new_deferral_cluster():
    """Pulse person-dormant item with snooze 3d + add to my list (no skip)."""
    print("test_renderer_accepts_pulse_person_with_new_deferral_cluster")
    data = {
        "widget_mode": "all_batch_widget",
        "sections": [{
            "title": None,
            "items": [{
                "n": 1,
                "icon": "👤",
                "name": "Bo Sample",
                "context_tag": "You usually talk every 5 days. It's been 18.",
                "metadata": [
                    ("Last contact", "18 days ago — Apr 18, Slack DM"),
                    ("Why they matter", "Direct report"),
                    ("Open context", "(no open thread tracked)"),
                    ("What's at stake", "Aug 4 cutover at risk"),
                ],
                "actions": [
                    "1 investigate", "1 draft re-engagement",
                    "1 schedule catchup [when]", "1 resolved",
                    "1 snooze 3d", "1 add to my list",
                ],
            }],
        }],
    }
    html = render_chat_output_widget(data)
    _check("renders without raising on new Pulse cluster", isinstance(html, str))
    _check("widget HTML includes 'Snooze (3 days)' label", "Snooze (3 days)" in html)


def test_renderer_accepts_inbox_email_with_not_relevant():
    """Inbox email item with snooze 3d + not relevant (no add to my list, no skip)."""
    print("test_renderer_accepts_inbox_email_with_not_relevant")
    data = {
        "widget_mode": "all_batch_widget",
        "sections": [{
            "title": None,
            "items": [{
                "n": 1, "icon": "✉",
                "name": "Sam",
                "subject": "Re: Q2 deck",
                "metadata": [("To", "sam@example.com"), ("Subject", "Re: Q2 deck")],
                "body_lines": ["Following up — sending Friday."],
                "actions": [
                    "1 send", "1 edit then send", "1 draft",
                    "1 escalate to memo", "1 snooze 3d", "1 not relevant",
                ],
            }],
        }],
    }
    html = render_chat_output_widget(data)
    _check("inbox shape with new dismissal cluster renders", isinstance(html, str))
    _check("button label states the duration (F-59)", ">Not relevant (60 days)<" in html)


def test_renderer_accepts_calendar_invite_minimal_cluster():
    """Calendar invite tighter cluster — no snooze, no add to my list."""
    print("test_renderer_accepts_calendar_invite_minimal_cluster")
    data = {
        "widget_mode": "all_batch_widget",
        "sections": [{
            "title": None,
            "items": [{
                "n": 1, "icon": "📅",
                "name": "Acme Discovery",
                "subject": "Acme Discovery Call",
                "context_tag": "Thu 3:00 PM — conflict with Bo 1:1",
                "actions": ["1 accept", "1 propose [time]", "1 decline [reason]", "1 not relevant"],
            }],
        }],
    }
    html = render_chat_output_widget(data)
    _check("calendar invite minimal cluster renders", isinstance(html, str))


def test_renderer_rejects_made_up_action():
    """Regression — non-canonical verbs still rejected after the v2.14.38 additions."""
    print("test_renderer_rejects_made_up_action")
    data = {
        "widget_mode": "all_batch_widget",
        "sections": [{
            "title": None,
            "items": [{
                "n": 1, "name": "X", "subject": "y",
                "actions": ["1 dismiss forever", "1 not relevant"],  # `dismiss forever` is bogus
            }],
        }],
    }
    try:
        render_chat_output_widget(data)
    except CanonicalActionError as e:
        _check("raises with the bogus verb in the message", "dismiss forever" in str(e))
        return
    raise AssertionError("expected CanonicalActionError on non-canonical verb")


def test_renderer_rejects_old_skip_replaced_with_typo():
    """Regression — `skip` is still canonical (not removed); but a typo isn't."""
    print("test_renderer_rejects_old_skip_replaced_with_typo")
    data = {
        "widget_mode": "all_batch_widget",
        "sections": [{
            "title": None,
            "items": [{
                "n": 1, "name": "X", "subject": "y",
                "actions": ["1 skp"],  # typo
            }],
        }],
    }
    try:
        render_chat_output_widget(data)
    except CanonicalActionError:
        _check("typo still rejected", True)
        return
    raise AssertionError("expected CanonicalActionError")


# ---------- End-to-end: the standardized full Pulse REVIEW item shape ----------


def test_e2e_pulse_review_with_unified_action_set():
    """The v2.14.38+ REVIEW pattern: every REVIEW namespace (a/b/c, d1/d2,
    e1/e2, r1/r2) uses the same affirmative + dismissal + defer cluster.
    """
    print("test_e2e_pulse_review_with_unified_action_set")
    data = {
        "widget_mode": "all_batch_widget",
        "sections": [{
            "title": "REVIEW",
            "items": [
                {"n": 5, "name": "Andrea Wetsel", "context_tag": "Update last contact?",
                 "actions": ["5 add [text]", "5 not relevant", "5 add to my list"]},
                {"n": 9, "icon": "🏢", "name": "Acme Co", "context_tag": "Track as prospect?",
                 "actions": ["9 add [text]", "9 not relevant", "9 add to my list"]},
                {"n": 10, "name": "Sam Sample", "context_tag": "Did 'Send pricing deck' get fulfilled?",
                 "actions": ["10 resolved", "10 not relevant", "10 add to my list"]},
            ],
        }],
    }
    html = render_chat_output_widget(data)
    _check("multi-REVIEW-item batch renders", isinstance(html, str))
    # All three flavors of the unified verb set should appear in the rendered buttons
    _check("'Add' button rendered", ">Add<" in html)
    _check("'Not relevant (60 days)' button rendered", ">Not relevant (60 days)<" in html)
    _check("'Add to my list' button rendered", ">Add to my list<" in html)
    # v4.5.2 S2 (F-59): the `resolved` wire id displays "Done" everywhere.
    _check("'Done' button rendered (CRU review)", ">Done<" in html)


def main():
    tests = [
        test_snooze_3d_canonical,
        test_not_relevant_canonical,
        test_add_text_canonical,
        test_snooze_duration_kept_as_backcompat,
        test_snooze_3d_display_label,
        test_not_relevant_display_label_states_duration,
        test_add_text_display_label,
        test_renderer_accepts_review_item_with_new_action_set,
        test_renderer_accepts_pulse_person_with_new_deferral_cluster,
        test_renderer_accepts_inbox_email_with_not_relevant,
        test_renderer_accepts_calendar_invite_minimal_cluster,
        test_renderer_rejects_made_up_action,
        test_renderer_rejects_old_skip_replaced_with_typo,
        test_e2e_pulse_review_with_unified_action_set,
    ]
    for t in tests:
        t()
    print(f"\n✓ all {len(tests)} tests passed")


if __name__ == "__main__":
    main()
