#!/usr/bin/env python3
"""Tests for Bug #44 dead-chrome fix (v3.13.8 §2.11).

Verifies:
  - Gate 6 (_validate_send_class_email_addresses) raises when an item with
    send-class actions has a placeholder To: that isn't a valid email
  - `add email then send` is a canonical action that lets degraded items
    through Gate 6 (since they don't carry send/draft until the user types
    an email)
  - A valid To: passes the gate
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from chat_output_renderer import (  # noqa: E402
    CANONICAL_ACTIONS,
    DataShapeError,
    render_chat_output_widget,
)


_BASE_VIEW = {
    "header": "Test header",
    "sections": [
        {
            "title": "TEST",
            "count": 1,
            "items": [
                {
                    "n": 1,
                    "icon": "✉",
                    "name": "Recipient",
                    "subject": "Subject",
                    "metadata": [
                        ("Subject", "Test"),
                        ("To", "Daniel (no email)"),
                    ],
                    "body_lines": ["Hello"],
                    "actions": ["1 send", "1 draft", "1 snooze 3d"],
                }
            ],
        }
    ],
}


def test_placeholder_email_with_send_blocked() -> None:
    """An item with send action + 'Daniel (no email)' To: should be blocked."""
    try:
        render_chat_output_widget(_BASE_VIEW, wrapper="fragment")
        raise AssertionError("expected DataShapeError on placeholder email")
    except DataShapeError as e:
        msg = str(e)
        assert "Bug #44" in msg or "valid email" in msg or "add email then send" in msg, msg
    print("PASS test_placeholder_email_with_send_blocked")


def test_valid_email_passes() -> None:
    """Replace To: with a real email and the same view should pass."""
    view = {
        "header": "Test header",
        "sections": [
            {
                "title": "TEST",
                "count": 1,
                "items": [
                    {
                        "n": 1,
                        "icon": "✉",
                        "name": "Recipient",
                        "subject": "Subject",
                        "metadata": [
                            ("Subject", "Test"),
                            ("To", "real@example.com"),
                        ],
                        "body_lines": ["Hello"],
                        "actions": ["1 send", "1 draft", "1 snooze 3d"],
                    }
                ],
            }
        ],
    }
    html = render_chat_output_widget(view, wrapper="fragment")
    assert "Recipient" in html or "real@example.com" in html
    print("PASS test_valid_email_passes")


def test_name_email_combo_passes() -> None:
    """`Recipient Name <real@example.com>` is the standard combo form."""
    view = {
        "header": "Test header",
        "sections": [
            {
                "title": "TEST",
                "count": 1,
                "items": [
                    {
                        "n": 1,
                        "icon": "✉",
                        "name": "Recipient",
                        "subject": "Subject",
                        "metadata": [
                            ("Subject", "Test"),
                            ("To", "Recipient <real@example.com>"),
                        ],
                        "body_lines": ["Hello"],
                        "actions": ["1 send", "1 draft", "1 snooze 3d"],
                    }
                ],
            }
        ],
    }
    render_chat_output_widget(view, wrapper="fragment")
    print("PASS test_name_email_combo_passes")


def test_add_email_then_send_is_canonical() -> None:
    """The new recovery verb is in CANONICAL_ACTIONS."""
    assert "add email then send" in CANONICAL_ACTIONS
    print("PASS test_add_email_then_send_is_canonical")


def main() -> int:
    test_placeholder_email_with_send_blocked()
    test_valid_email_passes()
    test_name_email_combo_passes()
    test_add_email_then_send_is_canonical()
    print("\nALL dead-chrome (Bug #44) tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
