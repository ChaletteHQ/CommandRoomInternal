#!/usr/bin/env python3
"""One decode per log line inside one door call (v6.11, Hub Refresh speed).

WHY THIS EXISTS
A Refresh step read the same event log through four to six readers (the
defensive loader, the plain iterator, the stale-read and seq checks, the
reference and integrity scans), and each one decoded every line itself: on a
26,000-row book the plate step alone ran 130,000 `json.loads` calls.
`events_io.OneLogRead` already shared each reader's own answer; this shares
the decode under them, so one line is decoded once for the whole call.

HOW
`decode_line(text)` is `json.loads(text)`. Inside an open `OneLogRead`
(`begin` / `end`), the answer is memoised by the line's EXACT text: the
memo is content-addressed, so an append during the run is a new key and
nothing can go stale; a line that does not decode raises the same error
every time it is asked. Outside a `OneLogRead` nothing is kept.

THE DISCIPLINE: shared answers are read only
Every reader that asked for the same line gets the same object, as
`OneLogRead` already hands one loader's entries to every caller. No reader
may change one. `FREEZE_SHARED` (tests only) freezes every shared answer,
so a reader that writes into one raises at once
(`tests/run_log_decode_test.py` runs the whole Refresh frozen).

stdlib only.
"""
from __future__ import annotations

import json
from typing import Any, Optional

#: Tests set this True to freeze every shared answer (a write raises).
FREEZE_SHARED = False

_MEMO: Optional[dict] = None
_DEPTH = 0


class _Bad:
    __slots__ = ("exc",)

    def __init__(self, exc: Exception):
        self.exc = exc


class FrozenDict(dict):
    """A dict no reader may change (tests)."""

    def _no(self, *a, **k):
        raise TypeError("a shared log row is read only (log_decode)")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _no  # type: ignore[assignment]
    __ior__ = _no  # type: ignore[assignment]


class FrozenList(list):
    """A list no reader may change (tests)."""

    def _no(self, *a, **k):
        raise TypeError("a shared log row is read only (log_decode)")

    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = sort = reverse = _no  # type: ignore[assignment]
    __iadd__ = __imul__ = _no  # type: ignore[assignment]


def _freeze(v: Any) -> Any:
    if isinstance(v, dict):
        return FrozenDict((k, _freeze(x)) for k, x in v.items())
    if isinstance(v, list):
        return FrozenList(_freeze(x) for x in v)
    return v


def begin() -> None:
    """Open the shared memo (nested opens share the outer one)."""
    global _MEMO, _DEPTH
    if _DEPTH == 0:
        _MEMO = {}
    _DEPTH += 1


def end() -> None:
    """Close it; the outermost close drops every shared answer."""
    global _MEMO, _DEPTH
    _DEPTH = max(0, _DEPTH - 1)
    if _DEPTH == 0:
        _MEMO = None


def active() -> bool:
    return _MEMO is not None


def decode_line(text: str) -> Any:
    """`json.loads(text)`, shared inside an open `OneLogRead`."""
    memo = _MEMO
    if memo is None:
        return json.loads(text)  # nothing is shared outside a OneLogRead
    hit = memo.get(text)
    if hit is None:
        try:
            val = json.loads(text)
            hit = _freeze(val) if FREEZE_SHARED else val
        except ValueError as exc:
            hit = _Bad(exc)
        memo[text] = hit
    if isinstance(hit, _Bad):
        raise hit.exc
    return hit
