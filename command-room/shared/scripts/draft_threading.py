#!/usr/bin/env python3
"""Reply-draft threading assertion (DRAFTTHREAD1).

The connector's draft-update operation carries no reply-to parameter, so
patching a reply draft in place rebuilds the message without its
In-Reply-To / References headers and the mail backend silently reassigns it
to a NEW thread — the tool returns the draft id as if it had succeeded, and
the `Re:` subject makes the detachment invisible in the drafts list.
(Observed 2026-07-29: the draft landed in thread <detached-thread-id> while
the conversation was <original-thread-id>.)

The contract rule (CONTRACT.md, mail-draft capability) is: a reply draft is
NEVER patched in place — any content change is a fresh create carrying the
reply-to reference. This module is the enforcement half: passing the
reply-to id is not proof the threading survived, so every reply-draft
create is asserted after the fact. It catches every future variant of the
failure, including connector-side regressions.

Pure, stdlib only. The caller makes the MCP calls and passes results in.
"""
from __future__ import annotations


class ReplyDraftDetachedError(RuntimeError):
    """The created reply draft landed on a different thread than the
    conversation it was meant to continue. The draft EXISTS but is orphaned —
    sending it would start a new conversation instead of replying."""

    def __init__(self, draft_thread_id: str, expected_thread_id: str,
                 draft_id: str = ""):
        self.draft_thread_id = draft_thread_id
        self.expected_thread_id = expected_thread_id
        self.draft_id = draft_id
        super().__init__(
            f"reply draft{f' {draft_id}' if draft_id else ''} DETACHED from its "
            f"conversation: draft landed in thread {draft_thread_id!r} but the "
            f"conversation is {expected_thread_id!r}. The reply-to headers did "
            f"not survive — do not send this draft; recreate it with the "
            f"reply-to reference and delete the orphan (the connector has no "
            f"delete-draft tool, so the user must delete it by hand — name it)."
        )


def _thread_of(obj) -> str | None:
    if not isinstance(obj, dict):
        return None
    for k in ("thread_id", "threadId", "conversation_id", "conversationId"):
        v = obj.get(k)
        if v:
            return str(v)
    # Some connectors nest the created draft under "draft" / "message".
    for k in ("draft", "message"):
        inner = obj.get(k)
        if isinstance(inner, dict):
            v = _thread_of(inner)
            if v:
                return v
    return None


def _draft_id_of(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    for k in ("draft_id", "draftId", "id"):
        v = obj.get(k)
        if v:
            return str(v)
    for k in ("draft", "message"):
        inner = obj.get(k)
        if isinstance(inner, dict):
            v = _draft_id_of(inner)
            if v:
                return v
    return ""


def assert_reply_threaded(created_draft, expected_thread_id) -> dict:
    """Assert a just-created reply draft is on the conversation's thread.

    `created_draft` — the connector's create-draft response (any dict shape;
    common thread-id spellings and one level of nesting are resolved).
    `expected_thread_id` — the conversation's thread id.

    Returns {"verified": True, "thread_id": ..., "draft_id": ...} on match.
    Raises ReplyDraftDetachedError on mismatch — loud, naming both ids.
    Returns {"verified": False, "reason": ...} when the response carries no
    thread id at all (nothing to compare — the caller must SAY the threading
    could not be verified rather than presenting the draft as threaded).
    """
    expected = str(expected_thread_id or "")
    if not expected:
        return {"verified": False,
                "reason": "no expected thread id was supplied — nothing to "
                          "assert against"}
    got = _thread_of(created_draft)
    draft_id = _draft_id_of(created_draft)
    if got is None:
        return {"verified": False, "draft_id": draft_id,
                "reason": "the create-draft response carries no thread id — "
                          "threading could not be verified; say so instead of "
                          "presenting the draft as threaded"}
    if got != expected:
        raise ReplyDraftDetachedError(got, expected, draft_id)
    return {"verified": True, "thread_id": got, "draft_id": draft_id}
