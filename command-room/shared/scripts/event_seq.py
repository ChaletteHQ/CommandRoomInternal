#!/usr/bin/env python3
"""
Read-side event-`seq` normalization (SPEC UNDOGUARD — 2026-07).

WHY THIS EXISTS
`seq` is the monotonic human counter every ordering, windowing and
back-reference in the substrate hangs off. It is NOT reliably an int on
disk. At the 2026-07-28 audit of the live workspace, across 6,298 events:

    int            5,129
    None / absent  1,168
    str                1   (`intel_logged`, 2026-06-22, seq "1957")
    duplicates         0
    backwards         86

One string row was enough to take `undo` down for the ENTIRE workspace:
`brain_undo` compared `ev.get("seq") or 0` in a range test, and
`'>' not supported between instances of 'str' and 'int'` aborted the whole
listing — not the row, the listing. The release notes promise customers
"all of it is reversible — say undo in any chat"; on any workspace carrying
one bad-typed row that sentence was false, and the failure was total.

THE `or 0` TRAP THIS REPLACES
`ev.get("seq") or 0` looks like a None-guard. It is not one. It is a
FALSY-default, and it is wrong in both directions:

  - `None or 0` -> 0. Every window in the codebase is a positive half-open
    range (`prev_seq < seq <= audit_seq`, `prev_seq` starting at 0), so a
    seq of 0 satisfies NO window. All 1,168 seq-less events are therefore
    silently OUTSIDE every batch — invisible to resolution, not "tolerated".
  - It collapses `seq=0` and `seq=None` into one value, so a reader cannot
    tell "the zeroth event" from "no seq at all".
  - `"1957" or 0` -> `"1957"`. A non-empty string is TRUTHY, so it sails
    past the guard unconverted and reaches the comparison. That asymmetry
    is the whole bug: the idiom accidentally handles one malformed shape
    and cannot see the other.

So the answer to "whatever guards the None case is the natural home for
type coercion" is: nothing guards it. There was no home. This module is it.

DOCTRINE — normalize at READ time, never rewrite history
This mirrors `event_time.py` exactly, and for the same ratified reason:
the substrate is append-only and "HISTORY IS NEVER REWRITTEN (additive-only
forever), so every reader normalizes at read time through this helper."
A malformed seq is repaired in the reader's hands, never on disk.

    event_seq(ev)      -> int | None   the event's comparable seq
    coerce_seq(value)  -> int | None   the scalar form (data.proposal_seq,
                                       data.dismissal_seq, retracts_seq, …)
    malformed_seqs(evs)-> list[dict]   the operator-visible trace

`None` means "this event has no usable position in the ledger". Callers must
handle that EXPLICITLY — skip it, or fall back to timestamp order. Never
`or 0` it back into a real value; that is the defect this module replaces.

VISIBILITY (SPEC UNDOGUARD acceptance #2)
A row whose seq could not be read must not vanish without a trace. Every
MALFORMED value (wrong type, or a string that is not a number) emits one
deduplicated `[event_seq]` line on stderr, and `malformed_seqs()` returns
the full list for a health check or an operator query. An ABSENT seq is not
warned — 1,168 of them are a known historical shape, not an anomaly, and
warning per row would bury the signal it exists to raise.

SCOPE BOUNDARY — the nano-epoch rule is NOT applied here
`atomic_write._file_max_seq` and `next_seq.py` ignore seqs >= 1e10 as
nano-epoch artifacts (`int(time.time_ns())`, ~1.77e18, written by
pre-v3.13.8 writers). That is a WRITE-side contract about which value to
allocate next. It is deliberately NOT mirrored here: on the read side a
nano-epoch seq is still a real integer on a real event, and dropping it
would make that event invisible to exactly the windowing this module
exists to keep honest. Ints pass through as-is.

stdlib only.
"""
from __future__ import annotations

import sys
from typing import Any, List, Optional

# One warning per distinct malformed value per process. The live substrate
# carries a single bad row today, but a workspace that grows a systematic
# bad writer must not spray one line per event through a customer's chat.
_WARNED: set = set()


def _warn(raw: Any, context: str, recovered: Optional[int]) -> None:
    """One deduplicated stderr line per distinct malformed value.

    The outcome is stated precisely: a recovered value is NOT excluded, and
    saying otherwise would send an operator hunting for a row that is in
    fact being read correctly."""
    key = (type(raw).__name__, repr(raw)[:80], context)
    if key in _WARNED:
        return
    _WARNED.add(key)
    where = f" ({context})" if context else ""
    outcome = (
        f"recovered as {recovered} for this read" if recovered is not None
        else "UNREADABLE — the row is excluded from seq-ordered windows"
    )
    sys.stderr.write(
        f"[event_seq] malformed seq {raw!r} of type {type(raw).__name__}"
        f"{where} — the substrate's seq must be an integer; {outcome}. "
        f"History is append-only and is never rewritten, so the value stays "
        f"as-is on disk. List every such row with "
        f"event_seq.malformed_seqs().\n"
    )


def coerce_seq(value: Any, *, context: str = "") -> Optional[int]:
    """The scalar seq coercion: a comparable int, or None when unusable.

    Coerce where safe, refuse where not (SPEC UNDOGUARD §4.1):
      - `int`            -> itself. The 5,129-row majority; the fast path.
      - `bool`           -> None. `True == 1` is a Python accident, never a
                            seq; silently reading `True` as event #1 would
                            put a malformed row INSIDE a real window.
      - `float`          -> int when integral (1957.0); None otherwise, since
                            a fractional seq has no position in a counter.
      - `str`            -> int when it is a clean integer literal. "1957"
                            is unambiguous and recovers the row's REAL
                            position, which is strictly better than dropping
                            a genuine event out of its window. Warned either
                            way, because a string seq is still a writer bug.
      - anything else    -> None.

    `None`/absent returns None WITHOUT a warning — that is the known
    historical shape, not an anomaly."""
    if value is None:
        return None
    if isinstance(value, bool):
        _warn(value, context, None)
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        recovered = int(value) if value.is_integer() else None
        _warn(value, context, recovered)
        return recovered
    if isinstance(value, str):
        try:
            recovered = int(value.strip())
        except ValueError:
            recovered = None
        _warn(value, context, recovered)
        return recovered
    _warn(value, context, None)
    return None


def event_seq(ev: Any, *, context: str = "") -> Optional[int]:
    """The event's comparable seq, or None when it has no usable position.

    The read-side analogue of `event_time(ev)`. Callers MUST branch on None
    rather than defaulting it to 0 — see the `or 0` trap in the module
    docstring."""
    if not isinstance(ev, dict):
        return None
    return coerce_seq(
        ev.get("seq"),
        context=context or str(ev.get("type") or "")[:40],
    )


def malformed_seqs(events) -> List[dict]:
    """Every event whose `seq` is present but unreadable as an integer —
    the operator-visible trace behind the stderr warnings.

    An ABSENT/None seq is not malformed (it is the 1,168-row historical
    shape) and is not returned here. Returns
    `[{index, seq, seq_type, type, ts, coerced}]` in file order; `coerced`
    is the recovered int when the value was safely convertible (a numeric
    string), else None.

    Read-only — nothing is written, and no history is edited."""
    out: List[dict] = []
    for i, ev in enumerate(events or []):
        if not isinstance(ev, dict):
            continue
        raw = ev.get("seq")
        if raw is None:
            continue
        if isinstance(raw, int) and not isinstance(raw, bool):
            continue
        out.append({
            "index": i,
            "seq": raw,
            "seq_type": type(raw).__name__,
            "type": ev.get("type"),
            "ts": ev.get("ts"),
            "coerced": coerce_seq(raw, context="malformed_seqs"),
        })
    return out


__all__ = ["event_seq", "coerce_seq", "malformed_seqs"]
