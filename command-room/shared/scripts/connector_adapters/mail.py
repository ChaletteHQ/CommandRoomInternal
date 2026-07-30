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

# Superhuman phrases keyed by the same intent verbs. Superhuman's
# `query_email_and_calendar` takes a natural-language / structured filter, not
# operator strings, so the compiled form is the scope stated in the words its
# own surface uses. Deliberately NOT an invented API field spelling: a wrong
# field name is the silent kind of wrong (it filters nothing and the run still
# looks healthy), whereas a scope stated in plain words is something the caller
# can hand the connector in the connector's terms and cannot lose.
#
# Before MAILSEAM, Superhuman fell through to the unknown-provider branch and
# got its intent dict back verbatim — indistinguishable from "we have never
# heard of this provider", so the sent scope reached the connector only if the
# model happened to reconstruct it.
_SUPERHUMAN_OPS = {
    "unread": "unread",
    "in_inbox": "in the inbox",
    "in_sent": "in sent mail",
    "from_me": "sent by me",
    "not_draft": "not a draft",
    "from": "from {value}",
    "to": "to {value}",
    "subject": "with subject {value}",
    "newer_than": "newer than {value}",
    "after": "on or after {value}",
    "message_id_lookup": "with Message-ID header {value}",
}

# Every verb this module compiles, from the maps themselves — the vocabulary is
# never restated by hand. `tool_discovery` asks through `is_search_intent`
# before matching an operation against tool ids: no connector exposes a tool
# named `in_sent`, so a substring match on an intent verb resolves to nothing on
# every provider, Gmail included (MAILSEAM §1).
SEARCH_INTENTS = frozenset(
    set(_GMAIL_OPS) | set(_OUTLOOK_OPS) | set(_SUPERHUMAN_OPS) | {"any_of"}
)


def is_search_intent(operation) -> bool:
    """True when `operation` names a search INTENT this module compiles rather
    than a connector tool. An intent resolves to the provider's SEARCH tool;
    matching it against tool ids resolves to nothing anywhere."""
    return isinstance(operation, str) and operation.strip().lower() in SEARCH_INTENTS


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
    a natural-language scope STRING (its `query_email_and_calendar` takes a
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
    if p == "superhuman":
        # Phrases, comma-joined — a filter sentence, not an operator string.
        return _compile_ops(intent, _SUPERHUMAN_OPS, joiner=", ")
    # unknown provider only: structured filter, no operator string
    return dict(intent)


def _compile_ops(intent: Dict[str, Any], ops: Dict[str, str],
                 joiner: str = " ") -> str:
    parts: List[str] = []
    branches = intent.get("any_of")
    if isinstance(branches, (list, tuple)) and branches:
        compiled = [c for c in (_compile_ops(b, ops, joiner) for b in branches
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
    return joiner.join(parts)


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


__all__ = ["compile_search", "threading_field", "deep_link",
           "SEARCH_INTENTS", "is_search_intent"]
