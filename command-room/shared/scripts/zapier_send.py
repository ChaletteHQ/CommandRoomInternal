#!/usr/bin/env python3
"""
Zapier-send helpers — single source of truth for building the payload that
gets passed to the user's `Command Room — Send Threaded Email` Zap.

Per `EMAIL_DRAFT_PROTOCOL.md` §3c v3.2.2+: Zapier's mail `reply_to_message`
action (both Gmail and Microsoft 365 / Outlook variants) exposes a parameter
LABELED `thread_id`, but the param actually wants the **RFC 822 Message-ID
header value** from the latest message in the thread — the
`<something@mail.gmail.com>` (or `<...@outlook.com>`) string — so Zapier can
set proper `In-Reply-To` / `References` MIME headers on the outgoing reply.

This applies identically to Gmail and Outlook: RFC 822 is the email protocol
spec; both providers use the same header semantics. The helper works on
responses from either provider — Gmail's `get_thread` (with `payload.headers`)
or Microsoft Graph's `/me/messages` (with top-level `internetMessageId`).

Why a helper is needed: native Gmail and native Outlook MCPs are draft-only
in M's workspace — they create drafts via `create_draft` but lack a send
capability. Zapier is the only send path. The `N send` and `N edit then send`
actions in every orchestrator dispatch through Zapier; `N draft` (v2.14.4+
consolidated verb — the legacy `N to drafts` is no longer canonical) goes
through native MCPs. See `EMAIL_DRAFT_PROTOCOL.md` for the full dispatch map.

NOT what Zapier wants:
  - Gmail's thread-level ID (e.g. `19de01d8fd988ea6`): on multi-message threads,
    this differs from the latest message ID, so headers go wrong.
  - Gmail's message-RESOURCE ID (e.g. `19e036260f1c5ddd`): Zapier returns
    "Requested entity was not found" — this is Gmail's internal hex, NOT
    the RFC 822 header value.

What Zapier wants:
  - The RFC 822 `Message-ID` header VALUE: `<CAGNbKj-...@mail.gmail.com>`
    (including the angle brackets). Found in
    `messages[-1].payload.headers[name="Message-ID"].value` on the standard
    Gmail API get_thread response.

Why the v2.14.38 fix wasn't enough:
  - v2.14.38 correctly identified that Zapier wants "the latest message ID"
    not the thread-level ID. But the helper returned the message's
    *resource ID* (Gmail's hex), which is the WRONG FORMAT.
  - For single-message threads where the Message-ID header value happens to
    contain a substring matching the resource ID, threading works by accident.
    For multi-message threads with a fresh Message-ID, Zapier rejects with
    "Requested entity was not found".
  - This is the bug Sam hit twice: 2026-05-07 (forked Josh's thread) and
    2026-05-12 (4 of 4 replies forked on the recipient side).

Diagnosis from Sam's 2026-05-12 Cowork session reading Zapier's actual
behavior: the field accepts header-format values; rejects hex resource IDs.

USAGE (from an orchestrator's `N send` reply handler at fire time):

    # 1. Fetch the thread via native Gmail MCP.
    thread_response = mcp_call("gmail.get_thread", {"thread_id": source_thread_id})

    # 2. Extract the latest message ID from the response.
    from zapier_send import extract_latest_message_id, build_zapier_send_payload
    latest_message_id = extract_latest_message_id(thread_response)

    # 3. Build the canonical payload and invoke the Zap.
    payload = build_zapier_send_payload(
        latest_message_id=latest_message_id,
        to=recipient_email,
        subject=subject,
        body=body_text,
    )
    mcp_call(zapier_send_tool_id, payload)

Why this is a helper instead of just a doc note: the v2.14.x agent-improvises-
around-canonical-paths failure class. Doc-only fixes have shipped before and
agents have improvised their way around them. The helper centralizes the
extraction so the dispatch logic can't get the param wrong.
"""
from __future__ import annotations

from typing import Any


class ZapierPayloadError(ValueError):
    """Raised when the Gmail thread response shape can't yield a latest message
    ID. Covers: empty messages array, missing `id` field on messages, response
    that isn't shaped like a Gmail get_thread output.

    The fix is at the orchestrator level — re-fetch the thread, or fall back
    to native Gmail threaded send (per EMAIL_DRAFT_PROTOCOL §3c step 2) if the
    thread is genuinely unfetchable.
    """


def _find_message_id_header(message: dict) -> str | None:
    """Walk a Gmail message object looking for the RFC 822 `Message-ID` header.

    Standard Gmail API shape: `message.payload.headers` is a list of
    `{"name": "Header-Name", "value": "..."}`. Header names are case-insensitive
    per RFC 822 ("Message-ID", "message-id", "Message-Id" all valid).

    Some MCPs flatten the shape and expose `message.headers` at the top level
    instead of nesting under `payload`. Some expose the value directly on a
    field like `messageIdHeader`. Try all in priority order.

    Returns the raw header value (including angle brackets, e.g.
    `<CAGNbKj-...@mail.gmail.com>`) or None if not found.
    """
    if not isinstance(message, dict):
        return None

    # Path 1: standard Gmail API — message.payload.headers
    payload = message.get("payload")
    if isinstance(payload, dict):
        headers = payload.get("headers")
        if isinstance(headers, list):
            for h in headers:
                if isinstance(h, dict):
                    name = h.get("name", "")
                    if isinstance(name, str) and name.lower() == "message-id":
                        value = h.get("value")
                        if isinstance(value, str) and value.strip():
                            return value.strip()

    # Path 2: flattened — message.headers at top level
    headers = message.get("headers")
    if isinstance(headers, list):
        for h in headers:
            if isinstance(h, dict):
                name = h.get("name", "")
                if isinstance(name, str) and name.lower() == "message-id":
                    value = h.get("value")
                    if isinstance(value, str) and value.strip():
                        return value.strip()

    # Path 3: dict-shaped headers — message.headers["Message-ID"]
    if isinstance(headers, dict):
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == "message-id":
                if isinstance(value, str) and value.strip():
                    return value.strip()

    # Path 4: direct top-level field with header-shaped value.
    # Some MCPs expose the parsed RFC 822 Message-ID as a top-level field:
    #   - `internetMessageId` — Microsoft Graph / Outlook (the canonical Outlook shape)
    #   - `messageIdHeader` / `Message-ID` / etc — various Gmail MCP wrappers
    # Only accept values shaped like `<...>` to avoid grabbing Gmail's hex resource ID
    # (which never has angle brackets) or other non-RFC-822 values.
    for key in ("internetMessageId", "messageIdHeader", "message_id_header", "Message-ID", "messageIdRfc"):
        value = message.get(key)
        if isinstance(value, str) and value.strip().startswith("<") and value.strip().endswith(">"):
            return value.strip()

    return None


def extract_latest_message_id(thread_response: Any) -> str:
    """Extract the LATEST message's RFC 822 `Message-ID` header value from a
    Gmail get_thread response.

    Returns a string like `<CAGNbKj-...@mail.gmail.com>` (with angle brackets)
    — that's the value Zapier's `gmail.reply_to_message` action wants for its
    `thread_id` parameter (despite the misleading param name) so it can write
    proper `In-Reply-To` / `References` MIME headers on the outbound reply.

    The Gmail API returns messages in chronological order — `messages[-1]` is
    the most recent.

    Robust to several response shapes:
      - Standard Gmail API: `{"messages": [{"payload": {"headers": [...]}}, ...]}`
      - Wrapped Gmail: `{"thread": {"messages": [...]}}`
      - Microsoft Graph collection: `{"value": [{"internetMessageId": "<...>", ...}, ...]}`
      - Outlook MCP wrappers: `{"items": [...]}` or `{"messages": [...]}` with `internetMessageId` per message
      - Direct list (rare): `[{...}, ...]`
      - Flattened-headers MCPs: `{"messages": [{"headers": [...]}, ...]}`

    Raises ZapierPayloadError if the response shape can't yield a Message-ID
    header value. **Does NOT fall back to the hex resource ID** — that value
    is the wrong format for Zapier and was the cause of the v2.14.38-era bug.

    If the helper raises, the orchestrator should fall through to native Gmail
    threaded send (per EMAIL_DRAFT_PROTOCOL §3a) rather than retry with the
    wrong format.
    """
    if thread_response is None:
        raise ZapierPayloadError("thread_response is None — get_thread call may have failed")

    # Unwrap common shapes — Gmail and Microsoft Graph (Outlook) variants
    if isinstance(thread_response, dict):
        if "messages" in thread_response:
            messages = thread_response["messages"]                  # Gmail standard
        elif "thread" in thread_response and isinstance(thread_response["thread"], dict):
            messages = thread_response["thread"].get("messages")    # Gmail wrapped
        elif "value" in thread_response and isinstance(thread_response["value"], list):
            messages = thread_response["value"]                     # Microsoft Graph collection
        elif "items" in thread_response and isinstance(thread_response["items"], list):
            messages = thread_response["items"]                     # some Outlook MCP wrappers
        else:
            raise ZapierPayloadError(
                "thread_response dict has no `messages` / `thread.messages` / `value` / `items` key — "
                "is this a Gmail or Outlook thread/conversation response? Got keys: "
                f"{sorted(thread_response.keys())}"
            )
    elif isinstance(thread_response, list):
        messages = thread_response
    else:
        raise ZapierPayloadError(
            f"thread_response is {type(thread_response).__name__}; expected dict or list"
        )

    if not messages:
        raise ZapierPayloadError(
            "messages array is empty — thread has no messages? "
            "If the thread was deleted, fall back to native Gmail threaded send."
        )

    if not isinstance(messages, list):
        raise ZapierPayloadError(
            f"messages field is {type(messages).__name__}; expected list"
        )

    latest = messages[-1]
    if not isinstance(latest, dict):
        raise ZapierPayloadError(
            f"latest message is {type(latest).__name__}; expected dict"
        )

    message_id_header = _find_message_id_header(latest)
    if message_id_header:
        return message_id_header

    # Diagnostic — what shape did we actually get?
    sample_keys = sorted(latest.keys())[:10]
    payload_keys = []
    if isinstance(latest.get("payload"), dict):
        payload_keys = sorted(latest["payload"].keys())[:10]
    raise ZapierPayloadError(
        "Could not find RFC 822 Message-ID header on the latest message. "
        "Zapier's reply_to_message action requires this header value (e.g. "
        "`<CAGNbKj-...@mail.gmail.com>`); the Gmail hex resource ID is rejected "
        "with 'Requested entity was not found'. Orchestrator should fall through "
        "to native Gmail threaded send (EMAIL_DRAFT_PROTOCOL §3a). "
        f"Latest message keys: {sample_keys}; payload keys: {payload_keys}"
    )


def build_zapier_send_payload(
    latest_message_id: str,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
) -> dict:
    """Build the canonical payload for the user's `Command Room — Send Threaded
    Email` Zap.

    The `thread_id` field is misleadingly named — it actually wants the RFC
    822 `Message-ID` header value from the latest message in the thread (e.g.
    `<CAGNbKj-...@mail.gmail.com>`). Pass `extract_latest_message_id(
    get_thread_response)` here, which returns exactly that. Do NOT pass:
      - the Gmail thread-level ID (`19de01d8fd988ea6`-style hex)
      - the Gmail message-resource ID (`19e036260f1c5ddd`-style hex)

    Both hex variants are rejected by Zapier with "Requested entity was not
    found" (verified 2026-05-12 by Sam's Cowork session).

    Empty `cc` / `bcc` strings are dropped from the payload — Zapier's Gmail
    Reply action treats empty strings as literal recipients in some
    configurations, which has broken sends in the past.
    """
    if not latest_message_id:
        raise ZapierPayloadError(
            "latest_message_id is empty — call extract_latest_message_id() first "
            "and pass its return value here. Empty thread_id sends as a NEW thread."
        )
    # Sanity-check the format: Message-ID headers always look like `<...>`.
    # If the caller passes a bare hex string (old bug shape), surface clearly.
    if not (latest_message_id.startswith("<") and latest_message_id.endswith(">")):
        raise ZapierPayloadError(
            f"latest_message_id has wrong format: {latest_message_id!r}. "
            "Zapier wants the RFC 822 Message-ID header value (e.g. "
            "`<CAGNbKj-...@mail.gmail.com>`) including the angle brackets, "
            "NOT a Gmail hex ID. Use extract_latest_message_id() to get the "
            "right value; if that helper raises, fall through to native Gmail "
            "threaded send."
        )
    if not to:
        raise ZapierPayloadError("to is empty — Zapier rejects sends without a recipient")
    if not subject:
        raise ZapierPayloadError("subject is empty — required by Zapier Gmail Reply action")
    if not body:
        raise ZapierPayloadError("body is empty — required by Zapier Gmail Reply action")

    payload = {
        "thread_id": latest_message_id,  # misleadingly named; actually wants message_id
        "to": to,
        "subject": subject,
        "body": body,
    }
    if cc:
        payload["cc"] = cc
    if bcc:
        payload["bcc"] = bcc
    return payload


__all__ = [
    "ZapierPayloadError",
    "extract_latest_message_id",
    "build_zapier_send_payload",
]


if __name__ == "__main__":
    # Smoke test — uses the canonical Gmail API shape with payload.headers
    sample = {
        "messages": [
            {
                "id": "19de01d8fd988ea6",
                "snippet": "First message Apr 30",
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<CABxc1d-original-msg@mail.gmail.com>"},
                        {"name": "Subject", "value": "Q2 deck"},
                    ],
                },
            },
            {
                "id": "19e036260f1c5ddd",
                "snippet": "Latest reply today",
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<CABxc1d-latest-reply@mail.gmail.com>"},
                        {"name": "Subject", "value": "Re: Q2 deck"},
                    ],
                },
            },
        ]
    }
    print("Latest Message-ID:", extract_latest_message_id(sample))
    print("Payload:", build_zapier_send_payload(
        latest_message_id=extract_latest_message_id(sample),
        to="josh@example.com",
        subject="Re: Q2 deck",
        body="Following up — see attached.",
    ))
