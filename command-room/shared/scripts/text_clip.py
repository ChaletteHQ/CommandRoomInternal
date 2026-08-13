#!/usr/bin/env python3
"""text_clip — THE evidence truncator (BUG-8330 item 10).

16 bare `[:200]` slices across 10 writer files (plus a divergent `[:400]`
pair) cut stored evidence MID-WORD, and renderers interpolate the stored
string into sentences — the user read "Command Room Premise retrac — close
it?" because the truncation happened at write time with no word boundary
and no ellipsis. A correct ellipsizing truncator existed
(brain_proposals' title snipper) and nothing shared it.

`clip` is word-boundary + ellipsis, bounded: the result is ALWAYS ≤ n
characters including the ellipsis. Writers call it at the same place they
sliced; the stored string never ends mid-word again
(tests/run_text_clip_test.py guards the census sites).

stdlib only, import-light — safe for every writer module.
"""
from __future__ import annotations

# The one evidence budget (was 16 scattered magic 200s).
EVIDENCE_MAX_CHARS = 200
# Proposal-row evidence deliberately carries more context (the review card
# renders it as the row's whole basis). Named so the difference is a
# decision, not a drifted magic number.
PROPOSAL_EVIDENCE_MAX_CHARS = 400


def clip(text, n: int = EVIDENCE_MAX_CHARS) -> str:
    """Truncate `text` to at most `n` chars, cutting at a word boundary and
    appending an ellipsis. Short input passes through byte-identical (the
    common case costs one len()). None → "".

    The boundary search floors at 60% of the budget so a single enormous
    token (a URL, a base64 blob) still truncates instead of overflowing."""
    s = str(text) if text is not None else ""
    if len(s) <= n:
        return s
    if n <= 1:
        return "…"[:n]
    cut = s[: n - 1]
    ws = cut.rfind(" ")
    if ws >= int((n - 1) * 0.6):
        cut = cut[:ws]
    return cut.rstrip() + "…"


__all__ = ["EVIDENCE_MAX_CHARS", "PROPOSAL_EVIDENCE_MAX_CHARS", "clip"]
