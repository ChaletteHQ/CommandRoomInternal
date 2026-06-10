#!/usr/bin/env python3
"""
Unit tests for the Zapier-send helpers in zapier_send.py.

These cover the bug Sam surfaced twice (2026-05-07 + 2026-05-12): Zapier's
`gmail.reply_to_message` action's `thread_id` parameter wants the RFC 822
`Message-ID` header value of the latest message (the `<...@mail.gmail.com>`
string), NOT Gmail's internal hex resource ID. The v2.14.38 helper was
returning the hex resource ID, which Zapier rejects with "Requested entity
was not found". v3.2.2+ returns the header value.

Run via: python3 tests/run_zapier_send_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from zapier_send import (  # noqa: E402
    ZapierPayloadError,
    build_zapier_send_payload,
    extract_latest_message_id,
)


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}{(' — ' + detail) if detail else ''}")
        raise AssertionError(label)


def _msg_with_message_id(hex_id: str, header_value: str, snippet: str = "") -> dict:
    """Build a Gmail-API-shaped message with payload.headers carrying Message-ID."""
    return {
        "id": hex_id,
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Test subject"},
                {"name": "From", "value": "sender@example.com"},
                {"name": "Message-ID", "value": header_value},
            ],
        },
    }


# ---------- extract_latest_message_id — primary path (standard Gmail API) ----------


def test_extracts_message_id_header_from_standard_gmail_shape():
    """Standard Gmail API: payload.headers contains Message-ID header."""
    print("test_extracts_message_id_header_from_standard_gmail_shape")
    response = {
        "messages": [
            _msg_with_message_id("19de01d8fd988ea6", "<original@mail.gmail.com>", "first"),
            _msg_with_message_id("19e036260f1c5ddd", "<latest@mail.gmail.com>", "latest"),
        ]
    }
    result = extract_latest_message_id(response)
    _check("returns Message-ID header value of latest message", result == "<latest@mail.gmail.com>")
    _check("preserves angle brackets (RFC 822 syntax)", result.startswith("<") and result.endswith(">"))


def test_single_message_thread_returns_its_header():
    """Sterling's case: one message, one Message-ID header."""
    print("test_single_message_thread_returns_its_header")
    response = {"messages": [_msg_with_message_id("hex1", "<sterling@mail.gmail.com>")]}
    _check("single-message thread returns its header", extract_latest_message_id(response) == "<sterling@mail.gmail.com>")


def test_header_name_is_case_insensitive():
    """RFC 822 says header names are case-insensitive — 'Message-ID', 'message-id', etc."""
    print("test_header_name_is_case_insensitive")
    for variant in ("Message-ID", "message-id", "Message-Id", "MESSAGE-ID"):
        response = {
            "messages": [{
                "payload": {"headers": [{"name": variant, "value": "<x@mail.gmail.com>"}]},
            }]
        }
        result = extract_latest_message_id(response)
        _check(f"finds header with name={variant!r}", result == "<x@mail.gmail.com>")


def test_handles_wrapped_thread_shape():
    """Some MCPs wrap the response: {thread: {messages: [...]}}"""
    print("test_handles_wrapped_thread_shape")
    response = {"thread": {"messages": [
        _msg_with_message_id("abc", "<one@mail.gmail.com>"),
        _msg_with_message_id("xyz", "<two@mail.gmail.com>"),
    ]}}
    _check("unwraps {thread: {messages: [...]}}", extract_latest_message_id(response) == "<two@mail.gmail.com>")


def test_handles_direct_list_response():
    """Rare: response IS the messages array."""
    print("test_handles_direct_list_response")
    response = [
        _msg_with_message_id("a", "<a@mail.gmail.com>"),
        _msg_with_message_id("b", "<b@mail.gmail.com>"),
        _msg_with_message_id("c", "<c@mail.gmail.com>"),
    ]
    _check("accepts direct messages list", extract_latest_message_id(response) == "<c@mail.gmail.com>")


# ---------- Fallback shapes ----------


def test_handles_flattened_headers_at_message_top_level():
    """Some MCPs flatten: message.headers (list), not message.payload.headers."""
    print("test_handles_flattened_headers_at_message_top_level")
    response = {
        "messages": [{
            "id": "h1",
            "headers": [
                {"name": "Subject", "value": "Test"},
                {"name": "Message-ID", "value": "<flat@mail.gmail.com>"},
            ],
        }]
    }
    _check("falls back to top-level headers list", extract_latest_message_id(response) == "<flat@mail.gmail.com>")


def test_handles_dict_shaped_headers():
    """Some MCPs use a dict instead of a list for headers."""
    print("test_handles_dict_shaped_headers")
    response = {
        "messages": [{
            "id": "h2",
            "headers": {"Subject": "Test", "Message-ID": "<dict@mail.gmail.com>"},
        }]
    }
    _check("falls back to dict-shaped headers", extract_latest_message_id(response) == "<dict@mail.gmail.com>")


def test_handles_direct_message_id_header_field():
    """Some MCPs surface the parsed header value as a top-level field."""
    print("test_handles_direct_message_id_header_field")
    response = {
        "messages": [{
            "id": "h3",
            "messageIdHeader": "<parsed@mail.gmail.com>",
        }]
    }
    _check("uses messageIdHeader field when present", extract_latest_message_id(response) == "<parsed@mail.gmail.com>")


# ---------- Microsoft Graph / Outlook shapes ----------


def test_outlook_graph_value_collection_shape():
    """Microsoft Graph returns collections as {value: [...]}; each message
    surfaces the RFC 822 header as a top-level `internetMessageId` field."""
    print("test_outlook_graph_value_collection_shape")
    response = {
        "value": [
            {"id": "AAMkAGI...", "internetMessageId": "<original-outlook@outlook.com>", "subject": "Q2 deck"},
            {"id": "AAMkAGJ...", "internetMessageId": "<latest-outlook@outlook.com>", "subject": "Re: Q2 deck"},
        ]
    }
    result = extract_latest_message_id(response)
    _check("unwraps Microsoft Graph {value: [...]} collection", result == "<latest-outlook@outlook.com>")
    _check("preserves angle brackets on Outlook Message-ID too", result.startswith("<") and result.endswith(">"))


def test_outlook_mcp_items_wrapper_shape():
    """Some Outlook MCP wrappers use {items: [...]} instead of Graph's {value: [...]}"""
    print("test_outlook_mcp_items_wrapper_shape")
    response = {
        "items": [
            {"id": "msg1", "internetMessageId": "<one@outlook.com>"},
            {"id": "msg2", "internetMessageId": "<two@outlook.com>"},
        ]
    }
    _check("unwraps {items: [...]} variant", extract_latest_message_id(response) == "<two@outlook.com>")


def test_outlook_messages_array_with_internet_message_id():
    """Outlook MCPs that normalize to the Gmail-shaped {messages: [...]} key
    still surface RFC 822 Message-ID as internetMessageId per Graph convention."""
    print("test_outlook_messages_array_with_internet_message_id")
    response = {
        "messages": [
            {"id": "AAMk1", "internetMessageId": "<m1@outlook.com>"},
            {"id": "AAMk2", "internetMessageId": "<m2@outlook.com>"},
        ]
    }
    _check("extracts internetMessageId from normalized Outlook shape", extract_latest_message_id(response) == "<m2@outlook.com>")


# ---------- Error cases ----------


def test_blocks_empty_thread():
    print("test_blocks_empty_thread")
    try:
        extract_latest_message_id({"messages": []})
    except ZapierPayloadError as e:
        _check("raises on empty messages array", "empty" in str(e).lower())
        return
    raise AssertionError("expected ZapierPayloadError")


def test_blocks_none_response():
    print("test_blocks_none_response")
    try:
        extract_latest_message_id(None)
    except ZapierPayloadError:
        _check("raises on None response", True)
        return
    raise AssertionError("expected ZapierPayloadError")


def test_blocks_message_without_message_id_header():
    """Message has hex resource ID but no Message-ID header — should NOT return the hex.
    Hex resource IDs are the v2.14.38 bug; the helper must raise instead of returning them."""
    print("test_blocks_message_without_message_id_header")
    response = {"messages": [{"id": "19e036260f1c5ddd", "snippet": "no headers field"}]}
    try:
        extract_latest_message_id(response)
    except ZapierPayloadError as e:
        msg = str(e).lower()
        _check("error names the missing Message-ID header", "message-id" in msg or "header" in msg)
        _check("error points orchestrator at native Gmail fallback", "native" in msg.lower() or "fall" in msg.lower())
        return
    raise AssertionError("expected ZapierPayloadError when no Message-ID header present")


def test_blocks_unrecognized_response_shape():
    print("test_blocks_unrecognized_response_shape")
    try:
        extract_latest_message_id({"foo": "bar"})
    except ZapierPayloadError as e:
        _check("raises with helpful error naming the keys it saw", "foo" in str(e))
        return
    raise AssertionError("expected ZapierPayloadError")


# ---------- build_zapier_send_payload ----------


def test_builds_minimal_payload_with_header_value():
    print("test_builds_minimal_payload_with_header_value")
    p = build_zapier_send_payload(
        latest_message_id="<latest@mail.gmail.com>",
        to="josh@example.com",
        subject="Re: Q2 deck",
        body="Following up.",
    )
    _check("thread_id field gets the Message-ID header value", p["thread_id"] == "<latest@mail.gmail.com>")
    _check("to passes through", p["to"] == "josh@example.com")
    _check("subject passes through", p["subject"] == "Re: Q2 deck")
    _check("body passes through", p["body"] == "Following up.")
    _check("no cc when empty", "cc" not in p)
    _check("no bcc when empty", "bcc" not in p)


def test_includes_cc_bcc_when_provided():
    print("test_includes_cc_bcc_when_provided")
    p = build_zapier_send_payload(
        latest_message_id="<a@mail.gmail.com>",
        to="a@x.com",
        subject="s",
        body="b",
        cc="cc@x.com",
        bcc="bcc@x.com",
    )
    _check("cc included", p["cc"] == "cc@x.com")
    _check("bcc included", p["bcc"] == "bcc@x.com")


def test_blocks_empty_message_id():
    """Empty string can't pass through."""
    print("test_blocks_empty_message_id")
    try:
        build_zapier_send_payload(latest_message_id="", to="a", subject="s", body="b")
    except ZapierPayloadError:
        _check("rejects empty message_id", True)
        return
    raise AssertionError("expected ZapierPayloadError on empty message_id")


def test_blocks_hex_format_message_id():
    """The v2.14.38 bug shape: hex resource ID like '19e036260f1c5ddd' must be
    rejected because Zapier returns 'Requested entity was not found' on it."""
    print("test_blocks_hex_format_message_id")
    try:
        build_zapier_send_payload(
            latest_message_id="19e036260f1c5ddd",
            to="a@x.com",
            subject="s",
            body="b",
        )
    except ZapierPayloadError as e:
        msg = str(e).lower()
        _check("error explains the right format", "format" in msg or "header" in msg)
        _check("error references RFC 822 angle-bracket shape", "<" in str(e) or "rfc" in msg)
        return
    raise AssertionError("expected ZapierPayloadError on hex format")


def test_blocks_missing_required_fields():
    print("test_blocks_missing_required_fields")
    cases = [
        {"to": "", "subject": "s", "body": "b"},
        {"to": "a", "subject": "", "body": "b"},
        {"to": "a", "subject": "s", "body": ""},
    ]
    for c in cases:
        try:
            build_zapier_send_payload(latest_message_id="<m@mail.gmail.com>", **c)
        except ZapierPayloadError:
            continue
        raise AssertionError(f"expected ZapierPayloadError for case {c}")
    _check("blocks empty to/subject/body", True)


# ---------- End-to-end (Sam's exact bug shape) ----------


def test_daniel_2026_05_12_thread_end_to_end():
    """Replicates Sam's 2026-05-12 bug: 4 replies forked on the recipient side
    because the helper was returning Gmail's hex resource ID instead of the
    RFC 822 Message-ID header value. v3.2.2+ returns the header value, which
    is what Zapier needs for proper In-Reply-To / References headers.
    """
    print("test_daniel_2026_05_12_thread_end_to_end")
    # Andy's thread, 2 messages, latest has a real Message-ID header
    andy_thread = {
        "id": "19de01d8fd988ea6",  # Gmail thread-level ID — Zapier rejects
        "messages": [
            _msg_with_message_id(
                "19de01d8fd988ea6",
                "<CAB-original-andy@mail.gmail.com>",
                "First Apr 30 message",
            ),
            _msg_with_message_id(
                "19e036260f1c5ddd",  # Gmail message-RESOURCE ID — also Zapier rejects
                "<CAB-latest-andy-reply@mail.gmail.com>",
                "Andy's reply today",
            ),
        ]
    }
    latest = extract_latest_message_id(andy_thread)
    payload = build_zapier_send_payload(
        latest_message_id=latest,
        to="andy@example.com",
        subject="Re: catching up",
        body="Sounds good — let's grab time Tuesday.",
    )
    _check(
        "payload thread_id is the latest Message-ID header value",
        payload["thread_id"] == "<CAB-latest-andy-reply@mail.gmail.com>",
    )
    _check(
        "payload thread_id is NOT Gmail's thread-level hex",
        payload["thread_id"] != andy_thread["id"],
    )
    _check(
        "payload thread_id is NOT the message-resource hex either",
        payload["thread_id"] != "19e036260f1c5ddd",
    )


def main():
    tests = [
        # extract_latest_message_id — happy path + variants
        test_extracts_message_id_header_from_standard_gmail_shape,
        test_single_message_thread_returns_its_header,
        test_header_name_is_case_insensitive,
        test_handles_wrapped_thread_shape,
        test_handles_direct_list_response,
        # extract_latest_message_id — fallback shapes
        test_handles_flattened_headers_at_message_top_level,
        test_handles_dict_shaped_headers,
        test_handles_direct_message_id_header_field,
        # extract_latest_message_id — Microsoft Graph / Outlook shapes
        test_outlook_graph_value_collection_shape,
        test_outlook_mcp_items_wrapper_shape,
        test_outlook_messages_array_with_internet_message_id,
        # extract_latest_message_id — error cases
        test_blocks_empty_thread,
        test_blocks_none_response,
        test_blocks_message_without_message_id_header,
        test_blocks_unrecognized_response_shape,
        # build_zapier_send_payload
        test_builds_minimal_payload_with_header_value,
        test_includes_cc_bcc_when_provided,
        test_blocks_empty_message_id,
        test_blocks_hex_format_message_id,
        test_blocks_missing_required_fields,
        # End-to-end
        test_daniel_2026_05_12_thread_end_to_end,
    ]
    for t in tests:
        t()
    print(f"\n✓ all {len(tests)} tests passed")


if __name__ == "__main__":
    main()
