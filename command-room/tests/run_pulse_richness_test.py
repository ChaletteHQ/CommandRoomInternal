#!/usr/bin/env python3
"""
Unit tests for the v2.14.38+ Pulse richness validator in chat_output_renderer.py.

Tests the three blocking rules for Pulse (cr-dont-forget) person-dormant cards:
  1. All 4 mandatory metadata keys must be present.
  2. `Last contact` must include both a date AND a topic separator.
  3. If metadata references a Gmail/Granola/transcript URL, `original_thread`
     must be populated with a non-empty url.

Run via: python3 tests/run_pulse_richness_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from chat_output_renderer import (  # noqa: E402
    PulseRichnessError,
    _validate_pulse_richness,
    _is_pulse_person_dormant_item,
    render_chat_output_widget,
)


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}{(' — ' + detail) if detail else ''}")
        raise AssertionError(label)


def _valid_pulse_item(**overrides):
    """Baseline canonical Pulse person-dormant item used across tests."""
    item = {
        "n": 1,
        "icon": "👤",
        "name": "Bo Sample",
        "subject": None,
        "context_tag": "You usually talk every 5 days. It's been 18.",
        "original_thread": {
            "author": "Bo Sample <bo@example.com>",
            "date": "Apr 18, 2:11 PM",
            "subject": "NetSuite handoff",
            "body": "I'll send the updated mapping by end of next week...",
            "url": "https://mail.google.com/mail/u/0/#all/19de01d8fd988ea6",
        },
        "metadata": [
            ("Last contact", "18 days ago — Apr 18, Slack DM about Q3 OKR"),
            ("Why they matter", "Direct report · NetSuite migration lead"),
            ("Open context", "[NetSuite handoff still pending](https://mail.google.com/mail/u/0/#all/19de01d8fd988ea6)"),
            ("What's at stake", "NetSuite cutover Aug 4 — handoff doc gates 3 downstream tasks"),
        ],
        "actions": ["1 investigate", "1 draft re-engagement", "1 schedule catchup [when]", "1 resolved", "1 snooze [duration]", "1 skip"],
    }
    item.update(overrides)
    return item


def _wrap(item):
    return {"widget_mode": "all_batch_widget", "sections": [{"title": None, "items": [item]}]}


# ---------- Fingerprint tests ----------


def test_fingerprint_matches_canonical_pulse_item():
    print("test_fingerprint_matches_canonical_pulse_item")
    item = _valid_pulse_item()
    _check("👤 + investigate action matches", _is_pulse_person_dormant_item(item))


def test_fingerprint_skips_non_pulse_emoji():
    print("test_fingerprint_skips_non_pulse_emoji")
    item = _valid_pulse_item(icon="✉")
    _check("✉ icon does not match (inbox shape)", not _is_pulse_person_dormant_item(item))


def test_fingerprint_skips_pulse_emoji_without_pulse_actions():
    print("test_fingerprint_skips_pulse_emoji_without_pulse_actions")
    item = _valid_pulse_item(actions=["1 confirm", "1 skip"])
    _check("👤 + non-pulse actions does not match", not _is_pulse_person_dormant_item(item))


# ---------- Rule 1: mandatory metadata keys ----------


def test_passes_with_all_4_mandatory_keys():
    print("test_passes_with_all_4_mandatory_keys")
    _validate_pulse_richness(_wrap(_valid_pulse_item()))
    _check("4-key canonical item passes", True)


def test_blocks_missing_last_contact():
    print("test_blocks_missing_last_contact")
    item = _valid_pulse_item(metadata=[
        ("Why they matter", "Direct report"),
        ("Open context", "[thread](https://mail.google.com/u/0/#all/x)"),
        ("What's at stake", "Aug 4 cutover gates 3 tasks"),
    ])
    try:
        _validate_pulse_richness(_wrap(item))
    except PulseRichnessError as e:
        _check("raises with key-missing message", "Last contact" in str(e))
        return
    raise AssertionError("expected PulseRichnessError")


def test_blocks_silent_omission_of_three_keys():
    print("test_blocks_silent_omission_of_three_keys")
    item = _valid_pulse_item(metadata=[("Last contact", "18 days ago — Apr 18, Slack")])
    item.pop("original_thread", None)
    try:
        _validate_pulse_richness(_wrap(item))
    except PulseRichnessError as e:
        msg = str(e)
        _check("flags Why they matter missing", "Why they matter" in msg)
        _check("flags Open context missing", "Open context" in msg)
        _check("flags What's at stake missing", "What's at stake" in msg)
        return
    raise AssertionError("expected PulseRichnessError")


def test_accepts_explicit_fallback_strings():
    print("test_accepts_explicit_fallback_strings")
    item = _valid_pulse_item(metadata=[
        ("Last contact", "18 days ago — last touch unknown"),
        ("Why they matter", "(no role tracked yet)"),  # MLK1: the old copy suggested the retired `add to my list` verb
        ("Open context", "(no open thread tracked — `Investigate` will pull cross-references)"),
        ("What's at stake", "(warm relationship at risk of going cold)"),
    ])
    item.pop("original_thread", None)
    _validate_pulse_richness(_wrap(item))
    _check("explicit fallback strings pass (no source URL → no original_thread required)", True)


# ---------- Rule 2: Last contact must have date + topic ----------


def test_blocks_bare_last_contact_value():
    print("test_blocks_bare_last_contact_value")
    item = _valid_pulse_item(metadata=[
        ("Last contact", "18 days ago"),
        ("Why they matter", "Direct report"),
        ("Open context", "(no open thread tracked)"),
        ("What's at stake", "Aug 4 cutover at risk"),
    ])
    item.pop("original_thread", None)
    try:
        _validate_pulse_richness(_wrap(item))
    except PulseRichnessError as e:
        _check("raises on bare 'Last contact'", "too bare" in str(e) or "Last contact" in str(e))
        return
    raise AssertionError("expected PulseRichnessError")


def test_accepts_last_contact_with_em_dash():
    print("test_accepts_last_contact_with_em_dash")
    item = _valid_pulse_item(metadata=[
        ("Last contact", "18 days ago — Apr 18, Slack DM about Q3 OKR"),
        ("Why they matter", "Direct report"),
        ("Open context", "(no open thread tracked)"),
        ("What's at stake", "Aug 4 cutover at risk"),
    ])
    item.pop("original_thread", None)
    _validate_pulse_richness(_wrap(item))
    _check("em-dash separator passes", True)


def test_accepts_last_contact_explicit_fallback():
    print("test_accepts_last_contact_explicit_fallback")
    item = _valid_pulse_item(metadata=[
        ("Last contact", "(last touch context unavailable — open thread to read)"),
        ("Why they matter", "Direct report"),
        ("Open context", "(no open thread tracked)"),
        ("What's at stake", "Aug 4 cutover at risk"),
    ])
    item.pop("original_thread", None)
    _validate_pulse_richness(_wrap(item))
    _check("parenthetical fallback passes", True)


# ---------- Rule 3: source URL → original_thread required ----------


def test_blocks_gmail_url_without_original_thread():
    """Sam's exact bug: bare Pulse card referencing an email thread with
    no `original_thread` populated. The card had no link to the email and the
    description was sparse.
    """
    print("test_blocks_gmail_url_without_original_thread")
    item = _valid_pulse_item()
    item.pop("original_thread", None)
    try:
        _validate_pulse_richness(_wrap(item))
    except PulseRichnessError as e:
        _check("raises on Gmail URL + missing original_thread", "original_thread" in str(e))
        return
    raise AssertionError("expected PulseRichnessError — this is the Sam 2026-05-07 bug")


def test_blocks_granola_url_without_original_thread():
    print("test_blocks_granola_url_without_original_thread")
    item = _valid_pulse_item(metadata=[
        ("Last contact", "12 days ago — Apr 24, Aspen call"),
        ("Why they matter", "Account lead"),
        ("Open context", "[Apr 24 Aspen sync transcript](https://notes.granola.ai/d/abc123)"),
        ("What's at stake", "Q2 launch dependencies"),
    ])
    item.pop("original_thread", None)
    try:
        _validate_pulse_richness(_wrap(item))
    except PulseRichnessError as e:
        _check("raises on Granola URL + missing original_thread", "original_thread" in str(e))
        return
    raise AssertionError("expected PulseRichnessError")


def test_blocks_original_thread_with_empty_url():
    print("test_blocks_original_thread_with_empty_url")
    item = _valid_pulse_item(original_thread={
        "author": "Bo",
        "date": "Apr 18",
        "subject": "NetSuite",
        "body": "...",
        "url": "",
    })
    try:
        _validate_pulse_richness(_wrap(item))
    except PulseRichnessError as e:
        _check("raises on empty url field", "url" in str(e).lower())
        return
    raise AssertionError("expected PulseRichnessError")


def test_passes_self_commitment_with_no_source_url():
    print("test_passes_self_commitment_with_no_source_url")
    # Pure cadence-decay flag, no email/transcript anchoring → original_thread not required
    item = _valid_pulse_item(metadata=[
        ("Last contact", "21 days ago — Apr 16, last 1:1"),
        ("Why they matter", "Co-founder"),
        ("Open context", "(no open thread tracked — `Investigate` will pull cross-references)"),
        ("What's at stake", "Cofounder relationships need regular touch"),
    ])
    item.pop("original_thread", None)
    _validate_pulse_richness(_wrap(item))
    _check("self-commitment / pure cadence card passes without original_thread", True)


# ---------- Integration: render_chat_output_widget end-to-end ----------


def test_render_widget_blocks_bare_pulse_card():
    print("test_render_widget_blocks_bare_pulse_card")
    item = _valid_pulse_item(metadata=[("Last contact", "18 days ago")])
    item.pop("original_thread", None)
    data = _wrap(item)
    try:
        render_chat_output_widget(data)
    except PulseRichnessError:
        _check("render_chat_output_widget gates on PulseRichnessError", True)
        return
    raise AssertionError("expected PulseRichnessError from full render path")


def test_render_widget_accepts_canonical_pulse_card():
    print("test_render_widget_accepts_canonical_pulse_card")
    html = render_chat_output_widget(_wrap(_valid_pulse_item()))
    _check("canonical Pulse item renders", isinstance(html, str) and len(html) > 1000)
    _check("rendered HTML contains the Gmail thread URL", "19de01d8fd988ea6" in html)


# ---------- Coexistence: doesn't false-positive on other shapes ----------


def test_email_inbox_item_does_not_trigger_pulse_validator():
    print("test_email_inbox_item_does_not_trigger_pulse_validator")
    item = {
        "n": 1, "icon": "✉", "name": "Sam", "subject": "Q2 deck",
        "metadata": [("To", "sam@x.com"), ("Subject", "Q2 deck")],
        "body_lines": ["Sending tomorrow."],
        "actions": ["1 send", "1 draft", "1 snooze 3d"],
    }
    _validate_pulse_richness(_wrap(item))
    _check("inbox email item does not trigger Pulse validator", True)


def test_stale_project_item_does_not_trigger_pulse_validator():
    print("test_stale_project_item_does_not_trigger_pulse_validator")
    item = {
        "n": 2, "icon": "📁", "name": "Aspen Hardware",
        "context_tag": "Stale active.",
        "metadata": [("Status", "active"), ("Last activity", "Mar 28")],
        "actions": ["2 prep deep work", "2 investigate", "2 mark paused", "2 status check", "2 skip"],
    }
    _validate_pulse_richness(_wrap(item))
    _check("stale-project item does not trigger person-dormant validator", True)


def main():
    tests = [
        test_fingerprint_matches_canonical_pulse_item,
        test_fingerprint_skips_non_pulse_emoji,
        test_fingerprint_skips_pulse_emoji_without_pulse_actions,
        test_passes_with_all_4_mandatory_keys,
        test_blocks_missing_last_contact,
        test_blocks_silent_omission_of_three_keys,
        test_accepts_explicit_fallback_strings,
        test_blocks_bare_last_contact_value,
        test_accepts_last_contact_with_em_dash,
        test_accepts_last_contact_explicit_fallback,
        test_blocks_gmail_url_without_original_thread,
        test_blocks_granola_url_without_original_thread,
        test_blocks_original_thread_with_empty_url,
        test_passes_self_commitment_with_no_source_url,
        test_render_widget_blocks_bare_pulse_card,
        test_render_widget_accepts_canonical_pulse_card,
        test_email_inbox_item_does_not_trigger_pulse_validator,
        test_stale_project_item_does_not_trigger_pulse_validator,
    ]
    for t in tests:
        t()
    print(f"\n✓ all {len(tests)} tests passed")


if __name__ == "__main__":
    main()
