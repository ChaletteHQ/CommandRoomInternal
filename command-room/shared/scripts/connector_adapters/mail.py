#!/usr/bin/env python3
"""
Mail data-model adapter (Layer A4) — one intent, N provider shapes.

Skills express INTENT (`unread`, `since`, `from`, `in_sent`, `not_draft`), never
a provider query string. This module compiles intent → the connected provider's
query/field/URL. Adding a provider = a row here, never a change in skill prose
(CONTRACT Rule 21). Provenance is owned by `provenance.py`; this module owns
search-query compilation, threading fields, and deep-link rendering.

Providers wired now (per YAGNI + Step-0 evidence): gmail, superhuman, outlook.
An unknown provider degrades: unknown search → return the intent dict verbatim
(the agent asks the connector in its own terms); unknown deep-link → None (N8,
degrade the affordance, never emit a broken link).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Gmail search operators keyed by intent verb. Values are format templates.
_GMAIL_OPS = {
    "unread": "is:unread",
    "in_inbox": "in:inbox",
    "in_sent": "in:sent",
    "from_me": "from:me",
    "not_draft": "-in:draft",
    "from": "from:{value}",
    "to": "to:{value}",
    "subject": "subject:{value}",
    "newer_than": "newer_than:{value}",
    "after": "after:{value}",
    "rfc822msgid": "rfc822msgid:{value}",
    # Neutral alias for the RFC 822 Message-ID header lookup — skill prose
    # uses THIS name so the provider operator spelling lives only here.
    "message_id_lookup": "rfc822msgid:{value}",
}

# Outlook / Graph $search + $filter fragments. Graph uses KQL-ish search plus
# OData filters; we emit search terms and let the connector's tool assemble.
_OUTLOOK_OPS = {
    "unread": "isRead:false",
    "in_inbox": "parentFolder:inbox",
    "in_sent": "parentFolder:sentitems",
    "from_me": "from:me",
    "not_draft": "parentFolder:ne:drafts",
    "from": "from:{value}",
    "to": "to:{value}",
    "subject": "subject:{value}",
    "newer_than": "received>={value}",
    "after": "received>={value}",
    # RFC 822 Message-ID header lookup — Graph's internetMessageId property.
    "message_id_lookup": "internetMessageId:{value}",
}

# Deep-link hosts per provider — used ONLY as a last resort when the connector
# returns no URL of its own (prefer the returned URL always — A4 + Rule 13).
_DEEP_LINK = {
    "gmail": "https://mail.google.com/mail/u/0/#all/{id}",
    "outlook": "https://outlook.office.com/mail/inbox/id/{id}",
    # superhuman: no stable public deep-link host — always use the returned URL,
    # else degrade (N8). Deliberately absent.
}

_THREADING_FIELD = {
    "gmail": "threadId",
    "outlook": "conversationId",
    "superhuman": "thread_id",
}


def compile_search(intent: Dict[str, Any], provider: Optional[str]) -> Any:
    """Compile an intent dict → a provider query.

    Gmail/Outlook → a single query STRING built from operators. Superhuman →
    the intent dict passed through (its `query_email_and_calendar` takes a
    natural-language / structured filter, not Gmail operators). Unknown
    provider → the intent dict verbatim (agent asks in the connector's terms).

    Disjunction: an `any_of: [<intent>, <intent>, …]` key compiles each branch
    and OR-joins them in a parenthesized group — e.g. the self-reply broaden
    path `{"any_of": [{"in_inbox": True}, {"in_sent": True}], "not_draft": True}`
    compiles on Gmail to `(in:inbox OR in:sent) -in:draft`. Without this, OR-
    bearing queries are inexpressible through the seam and flat-AND-join to a
    near-empty result (post-build review fix 3). For pass-through providers the
    `any_of` structure survives verbatim inside the intent dict."""
    p = (provider or "").lower()
    if p == "gmail":
        return _compile_ops(intent, _GMAIL_OPS)
    if p == "outlook":
        return _compile_ops(intent, _OUTLOOK_OPS)
    # superhuman + unknown: structured filter, no operator string
    return dict(intent)


def _compile_ops(intent: Dict[str, Any], ops: Dict[str, str]) -> str:
    parts: List[str] = []
    branches = intent.get("any_of")
    if isinstance(branches, (list, tuple)) and branches:
        compiled = [c for c in (_compile_ops(b, ops) for b in branches
                                if isinstance(b, dict)) if c]
        if len(compiled) == 1:
            parts.append(compiled[0])
        elif compiled:
            parts.append("(" + " OR ".join(compiled) + ")")
    for key, val in intent.items():
        if key == "any_of":
            continue
        tmpl = ops.get(key)
        if tmpl is None:
            continue
        if "{value}" in tmpl:
            if val in (None, "", False):
                continue
            parts.append(tmpl.format(value=val))
        else:
            # flag-style operator — include only when truthy
            if val:
                parts.append(tmpl)
    return " ".join(parts)


def threading_field(provider: Optional[str]) -> Optional[str]:
    """The field name that carries a thread/conversation id for reply-in-thread
    (A4). None for an unknown provider (caller degrades)."""
    return _THREADING_FIELD.get((provider or "").lower())


def deep_link(provider: Optional[str], native_id: Optional[str],
              returned_url: Optional[str] = None) -> Optional[str]:
    """Render an 'Open in …' link. ALWAYS prefer the URL the connector returned
    (Rule 13). Fall back to a per-provider host only when known. None when
    neither is available — the caller drops the affordance (N8), never emits a
    broken link."""
    if returned_url:
        return returned_url
    host = _DEEP_LINK.get((provider or "").lower())
    if host and native_id:
        return host.format(id=native_id)
    return None


__all__ = ["compile_search", "threading_field", "deep_link"]
