#!/usr/bin/env python3
"""
Canonical next-seq helper for events.jsonl (v3.13.8+ — Bug #41).

WHY THIS EXISTS
---------------

The events.jsonl substrate has two seq populations:

  1. Human-counter seqs — small monotonically increasing integers starting at 1
     and assigned by every canonical event writer. The intended scale.
  2. Nano-epoch seqs — historical artifacts (~1.77e18) from a brief window
     when one writer used `int(time.time_ns())` as its seq value. These are
     orders of magnitude larger than human-counter seqs.

Pre-v3.13.8, writers computed the next seq via `max(seqs) + 1`, which would
have made the next seq ALSO be a nano-epoch number after a single artifact
landed. To work around the artifact, some writers fell back to reading the
file tail; but the tail line may have NO seq field, and the dual-shape made
the "find next" logic fragile across writers (Bug #41).

THE FIX
-------

A single helper, `next_seq(events_jsonl_path)`, that:
  - reads the file line-by-line defensively
  - ignores non-dict lines, blank lines, malformed JSON
  - ignores any seq >= EPOCH_THRESHOLD (nano-epoch artifacts)
  - returns max(human-counter seqs) + 1

All event writers should call this rather than computing seq themselves.

CONCURRENCY NOTE (SPEC A1, v3.19.x)
-----------------------------------
A caller that reserves a seq via `next_seq()` and THEN appends separately, in
two steps, is still racy: a concurrent writer can take the same seq in the
gap between reserve and append. The safe path is to pass events WITHOUT a
`seq` field to `atomic_write.atomic_append_jsonl` — it auto-stamps `seq`
inside the events.jsonl writer lock, so reservation and write are one atomic
critical section. Use `next_seq()` only for read-only "what's the next seq"
display / diagnostics, not as a reserve-then-write-later pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union


# Seqs at or above this value are treated as nano-epoch artifacts and excluded
# from the "what's next" computation. 1e10 is comfortably above any plausible
# human-counter seq even for very heavy workspaces (~10 billion events) and
# comfortably below int(time.time_ns()) which is ~1.7e18 since 2023.
EPOCH_THRESHOLD = 10**10


def next_seq(events_jsonl_path: Union[str, Path]) -> int:
    """Return the next seq integer to assign to a new event.

    Heuristic: max(human-counter seqs in file) + 1, ignoring nano-epoch
    artifacts (seq >= EPOCH_THRESHOLD) and any line that doesn't parse as a
    JSON dict with a numeric `seq` field.

    Args:
      events_jsonl_path: path to events.jsonl (workspace-relative or absolute).

    Returns:
      The next seq integer to write (1 if the file is empty or doesn't exist).
    """
    path = Path(events_jsonl_path)
    if not path.exists():
        return 1

    max_human = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            seq = event.get("seq")
            if seq is None or not isinstance(seq, (int, float)) or isinstance(seq, bool):
                continue
            if seq < EPOCH_THRESHOLD:
                seq_int = int(seq)
                if seq_int > max_human:
                    max_human = seq_int

    return max_human + 1


__all__ = ["next_seq", "EPOCH_THRESHOLD"]


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: next_seq.py <events.jsonl path>", file=sys.stderr)
        raise SystemExit(2)
    print(next_seq(sys.argv[1]))
