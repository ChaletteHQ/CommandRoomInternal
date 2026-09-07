#!/usr/bin/env python3
"""LICGATE1 SPIKE — throwaway probe, NOT the shipping gate.

WHAT THIS IS FOR
----------------
One question, answered cheaply: when the write chokepoint refuses, does the
Cowork runtime HONOR the refusal, or does Claude route around it and write the
substrate some other way (bash heredoc, inline python one-liner)?

That is the `synthetic-workspace-runtime-test` bug class — "static analysis
passes but runtime substitutes an inferior path" — and it decides whether a
license gate at this seam is viable AT ALL. No crypto, no token, no expiry
here: a sentinel file stands in for "license expired" so the probe measures
runtime behaviour and nothing else.

DELETE THIS FILE before any release. It lives on branch `licgate-spike` only.

TRIGGER
-------
A file named `.license_expired` inside the workspace's `_hq/data/` directory,
resolved from the path being written. Sentinel FILE rather than env var on
purpose: CONTRACT.md line 278 records that each Cowork `mcp__workspace__bash`
call is independent with no env carryover, so an env-var trigger would not
survive between the turn that sets it and the turn that writes.

POSTURE (the shape the real gate inherits)
------------------------------------------
Fail OPEN on every ambiguity. The ONLY path that raises is: sentinel file
positively found. Missing workspace, unreadable directory, permission error,
import failure, anything unexpected -> return silently and let the write
proceed. A gate that blocks a paying client on ambiguity is worse than no gate.
"""

from __future__ import annotations

from pathlib import Path

SENTINEL_NAME = ".license_expired"

# How far up from the written path to look for `_hq/data`. Substrate writes
# land at `<workspace>/_hq/data/<file>`, so 4 covers that plus a subdirectory
# or two without ever walking out to the filesystem root.
_MAX_WALK_UP = 4


class LicenseExpired(Exception):
    """Raised when the spike sentinel is present.

    The message is deliberately written for the CEO reading it in chat, not
    for a developer reading a traceback: the real gate's refusal has to tell a
    paying client what happened and how to fix it, and the probe should test
    that surface too.
    """


def _sentinel_for(path: Path) -> Path | None:
    """The sentinel path governing `path`, or None if no `_hq/data` is above it.

    Walks up looking for a `_hq/data` directory. Returns None rather than
    guessing when the shape is unfamiliar — the fail-open posture starts here.
    """
    here = path.parent
    for _ in range(_MAX_WALK_UP):
        if here.name == "data" and here.parent.name == "_hq":
            return here / SENTINEL_NAME
        candidate = here / "_hq" / "data"
        if candidate.is_dir():
            return candidate / SENTINEL_NAME
        if here.parent == here:
            break
        here = here.parent
    return None


def check(path) -> None:
    """Raise LicenseExpired iff the sentinel is positively present.

    Every other outcome returns None and the write proceeds.
    """
    try:
        sentinel = _sentinel_for(Path(path))
        if sentinel is None:
            return
        if not sentinel.is_file():
            return
    except LicenseExpired:
        raise
    except Exception:
        # Fail open, loudly to nobody. An unreadable filesystem is not
        # evidence of an expired licence.
        return

    raise LicenseExpired(
        "Command Room is paused — this workspace's licence is no longer "
        "active, so nothing new can be written to your memory. Your existing "
        "data is untouched and still readable. Contact Chalette to reactivate."
    )
