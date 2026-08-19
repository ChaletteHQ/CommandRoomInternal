#!/usr/bin/env python3
"""Corroboration for NEGATIVE mail claims (MAILTRUST1).

Any single mail read — thread-fetch with FULL_CONTENT included — can only
ever prove presence, never absence. On 2026-07-29 `get_thread` returned 5
messages when 6 existed, and the mandated thread-fetch rule (added after the
2026-05-20 Dustin / Rio Designs incident) was followed and still failed; a
differently-shaped `in:anywhere newer_than:3d` sweep found the missing
message immediately. So before any surface asserts "no reply" / "went
quiet" / "nothing arrived" — or lets the v5.6.0 reply-closure rail write a
close on the strength of what a thread appears to (not) contain — the
per-thread fetch must be corroborated by a broad recency sweep of a
DIFFERENT shape (`newer_than:Nd` scoped wide, not `from:<addr>` — the
failing case had a from:-scoped search go stale too).

`corroborated: False` means the reads disagree: the caller MUST NOT assert
absence, and the closure rail holds the item instead of writing either way.
A wrong hold is recoverable; a wrong close is not.

Pure, stdlib only. The caller makes the MCP reads and passes them in.
"""
from __future__ import annotations


def _messages_of(read) -> list[dict]:
    """Accept a raw message list, or any dict carrying `messages`."""
    if read is None:
        return []
    if isinstance(read, dict):
        read = read.get("messages") or []
    return [m for m in read if isinstance(m, dict)]


def _msg_id(m: dict) -> str:
    for k in ("message_id", "messageId", "id", "native_id"):
        v = m.get(k)
        if v:
            return str(v)
    # No id at all — degrade to a content-ish identity so an id-less shape
    # still unions instead of crashing.
    return f"ts:{m.get('ts') or m.get('date') or ''}|s:{m.get('subject') or ''}"


def _thread_of(m: dict) -> str | None:
    for k in ("thread_id", "threadId", "conversation_id"):
        v = m.get(k)
        if v:
            return str(v)
    return None


def corroborate_absence(primary_read, corroborating_read, *, thread_id) -> dict:
    """Compare a per-thread fetch against a differently-shaped broad sweep.

    `primary_read` — the thread-fetch result (list of messages or a dict with
    `messages`). `corroborating_read` — the recency-sweep result; messages
    from other threads are filtered out by `thread_id` when they carry one
    (a sweep row with no thread marker is assumed relevant — over-inclusion
    can only make the check stricter, never let a miss through).

    Returns::

        {"corroborated": bool,      # the sweep saw nothing the fetch missed
         "delta": int,              # messages the primary read was short by
         "messages": [...],         # union, primary-first
         "max_count": int,          # max messages seen across reads
         "primary_count": int, "corroborating_count": int,
         "missing_message_ids": [...], "thread_id": str}

    A caller that gets `corroborated: False` must not assert absence — it
    says the reads disagree and names what it could not confirm.
    """
    thread_id = str(thread_id)
    primary = _messages_of(primary_read)
    sweep = [m for m in _messages_of(corroborating_read)
             if _thread_of(m) in (None, thread_id)]

    primary_ids = {_msg_id(m) for m in primary}
    union: list[dict] = list(primary)
    missing: list[str] = []
    seen = set(primary_ids)
    for m in sweep:
        mid = _msg_id(m)
        if mid not in seen:
            seen.add(mid)
            union.append(m)
            missing.append(mid)

    return {
        "corroborated": not missing,
        "delta": len(missing),
        "messages": union,
        "max_count": max(len(primary), len(sweep), len(union)),
        "primary_count": len(primary),
        "corroborating_count": len(sweep),
        "missing_message_ids": missing,
        "thread_id": thread_id,
    }
