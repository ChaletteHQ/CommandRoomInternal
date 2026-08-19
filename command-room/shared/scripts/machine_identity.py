#!/usr/bin/env python3
"""Stable per-machine identity for receipts (SPEC SCHED1 §0-3, 2026-08-17).

THE FINDING (2026-08-17, Stage 0 of SCHED1 against the operator's live
ledger). `RECEIPT_CONTRACT.md` rule 4 says the `machine` field rides on every
receipt because "schedules are per-machine (F-38); without it readers can't
tell two machines from a double-registration bug". The field was never a
hardcoded constant — `receipts._machine_name()` has always stamped
`platform.node()` — but inside Cowork's sandbox `platform.node()` returns the
SAME string on every physical machine. 840 receipts written from two different
computers all carry the identical value, so the one field whose entire job is
telling two machines apart tells a reader nothing, and swapping the hostname
source for another sandbox-visible name fixes nothing either: every one of
them is a property of the sandbox, not of the computer under it.

THE MECHANISM. An out-of-band, machine-local marker file. Written once, read
on every receipt:

    ~/.command-room/machine_id

WHY HOME AND NOT THE WORKSPACE. The workspace is Drive-synced across the
operator's machines — a token stored beside the ledger would be copied to the
other computer within minutes and then confidently name the wrong one, which
is worse than the sandbox hostname because it looks correct. `Path.home()` is
not synced; a file there belongs to the machine it was written on.

WHY THE SUFFIX IS DERIVED, NOT RANDOM (deviation from the spec's "+ 4 hex",
recorded deliberately). The spec's shape is `<node>-<4 hex>`, minted on first
use. Minting from `secrets` makes the token stable only for as long as the
marker survives — and the sandbox home may not survive a session, which is
exactly the environment this whole problem lives in. A random mint on an
ephemeral home would churn a NEW token per fire: noisier than the constant it
replaced. So the four hex characters are derived from facts that outlive the
file — the node name plus the host's hardware address — and a wiped marker
re-mints the SAME token on the same computer. Random hex remains the fallback
for the case where NO hardware address is obtainable, because there the two
inputs are the sandbox's node name alone and a derivation over it would
certainly collapse two machines onto one token.

THE RESIDUAL, STATED HONESTLY (fix round, REVIEW_SCHED1 N-4). The derivation
branch carries a weaker version of the SAME exposure, from a different cause,
and the flag does NOT make it observable. `_MULTICAST_BIT` rejects only
Python's own random substitute for a missing hardware address; it cannot
reject a sandbox image that presents the same fixed UNICAST address on two
different computers. In that case both machines compute the same base and the
same seed, mint the identical token, persist it, and report `source: "marker"`
with `fallback: False` — nothing is flagged, and the field says exactly as
little as the hostname did. The marker file cannot rescue it either: the
derivation runs before the file exists on each machine. So this design trades
distinctness-under-identical-sandbox-facts (which a random mint would have
kept) for stability across an ephemeral home. The trade is deliberate, but it
is not free, and it is not verifiable from the repo: **the two-machine walk
step is the decision procedure.** Read `~/.command-room/machine_id` on each
computer after a fire and compare the `machine` field on receipts written from
each. If they collide, this field is still a constant and the migration bought
churn instead of an answer — the answer then is a random mint plus an accepted
churn cost, or a name the user types.

WHAT THE USER MAY DO. Overwrite the file with a friendly name ("laptop",
"office-pc") and receipts carry that instead. The file is read on every call
and never cached, precisely so a rename takes effect on the next fire rather
than on the next restart.

FAIL-SOFT, ALWAYS. A receipt write must never fail over identity
(RELIABILITY.md). Nothing here raises: an unreadable home, a read-only disk, a
platform without `platform.node()` all degrade to a named source and, when the
answer could not be persisted, to `machine_id_fallback: true` on the receipt so
a reader can tell "this token is durable" from "this token is this run's best
guess". The flag is present ONLY when true, so the ordinary receipt's shape is
unchanged.

SOURCES (`machine_identity()["source"]`):
  marker      — read from the marker file (the steady state, and the only
                source a user-chosen name can arrive through).
  created     — minted and persisted to the marker on this call.
  unpersisted — minted but the marker could not be written (read-only or
                absent home). Durable anyway when the derivation had a
                hardware seed; flagged either way.
  node        — no derivation possible AND nothing persisted: the raw
                pre-SCHED1 behaviour, `platform.node()`. Flagged.
"""
from __future__ import annotations

import hashlib
import platform
import re
import secrets
import uuid
from pathlib import Path
from typing import Optional

MARKER_DIRNAME = ".command-room"
MARKER_FILENAME = "machine_id"

# The receipt field that says "this token was not persisted" — spelled once so
# the writers and the tests read the same word.
FALLBACK_FIELD = "machine_id_fallback"

# Receipt fields are strings; 64 is the cap `receipts._machine_name` has always
# applied and readers already tolerate.
MAX_TOKEN = 64

# Python's `uuid.getnode()` documents that when it cannot obtain a hardware
# address it returns a RANDOM 48-bit number with the multicast bit set. A
# random value is not a machine fact, so it must not seed a "stable" token:
# seeded from one, a wiped marker would re-mint a DIFFERENT token on the same
# computer, which is the churn the derivation exists to avoid.
#
# WHAT IT DOES NOT DO (N-4): this rejects Python's own substitute, nothing
# else. A real-looking unicast address that the sandbox presents identically on
# two computers passes here and collides — see the module docstring's residual.
# Pinned in `run_sched1_machine_identity_test.py` [2b], in both directions.
_MULTICAST_BIT = 0x010000000000

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slug(text: Optional[str]) -> str:
    """A hostname reduced to the readable half of the token. `""` when empty."""
    if not isinstance(text, str):
        return ""
    out = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    return out[:32]


def _node_name() -> str:
    try:
        return platform.node() or ""
    except Exception:  # noqa: BLE001 — identity never raises
        return ""


def _hardware_seed() -> str:
    """A machine fact that outlives the marker file, or `""` when there is none."""
    try:
        node = uuid.getnode()
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(node, int) or node <= 0 or node & _MULTICAST_BIT:
        return ""   # randomly generated by uuid.getnode(), not a hardware fact
    return f"{node:012x}"


def _mint() -> tuple[str, bool]:
    """`(token, derived)` — the token this machine should carry.

    `derived` is True when the suffix came from a hardware fact, which is what
    makes the token survive a wiped marker file. False means the suffix is
    random and only the marker keeps it stable.
    """
    base = _slug(_node_name()) or "machine"
    seed = _hardware_seed()
    if seed:
        suffix = hashlib.sha256(f"{base}|{seed}".encode("utf-8")).hexdigest()[:4]
        return f"{base}-{suffix}"[:MAX_TOKEN], True
    return f"{base}-{secrets.token_hex(2)}"[:MAX_TOKEN], False


def marker_path(home=None) -> Optional[Path]:
    """Where the marker lives, or None when no home is resolvable.

    THE HOME MUST BE ABSOLUTE, and that rule earned itself during this build's
    own test run: a blank home string resolves to `Path(".")`, and the marker
    was created inside whatever directory the process happened to start in —
    which for a fire is the PLUGIN ROOT, a directory the plugin does not own
    and that is replaced on every upgrade. A relative home is not a home; it
    returns None and the caller degrades to the flagged fallback.
    """
    try:
        if home is None:
            root = Path.home()
        elif isinstance(home, str) and not home.strip():
            return None
        else:
            root = Path(home)
        if not root.is_absolute():
            return None
        return root / MARKER_DIRNAME / MARKER_FILENAME
    except Exception:  # noqa: BLE001
        return None


def _read_marker(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — missing, unreadable, a directory: all None
        return None
    # First non-blank line: a user editing this by hand leaves a trailing
    # newline, and some editors add a second one.
    for line in raw.splitlines():
        token = line.strip()
        if token:
            return token[:MAX_TOKEN]
    return None


def _write_marker(path: Optional[Path], token: str) -> Optional[str]:
    """Persist `token`, returning what is ON DISK afterwards (None if nothing).

    Exclusive-create, so two fires racing on first use settle on ONE token —
    the loser re-reads rather than overwriting a value the winner may already
    have stamped on a receipt.
    """
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(token + "\n")
        return token
    except FileExistsError:
        return _read_marker(path)
    except Exception:  # noqa: BLE001 — read-only disk, no permission, no home
        return None


def machine_identity(home=None) -> dict:
    """`{machine, source, fallback, path}` for this computer. Never raises.

    `machine` is None only when there is nothing at all to say — no marker, no
    node name, no hardware address — in which case callers stamp no field, the
    same as pre-SCHED1 behaviour on a machine whose `platform.node()` was empty.
    """
    path = marker_path(home)
    existing = _read_marker(path)
    if existing:
        return {"machine": existing, "source": "marker", "fallback": False,
                "path": str(path) if path else None}

    token, derived = _mint()
    written = _write_marker(path, token)
    if written:
        return {"machine": written, "source": "created", "fallback": False,
                "path": str(path) if path else None}

    if derived:
        # Durable without the file: the same computer re-derives the same token.
        # Still flagged — a reader deserves to know nothing was persisted.
        return {"machine": token, "source": "unpersisted", "fallback": True,
                "path": str(path) if path else None}

    node = _node_name()[:MAX_TOKEN]
    return {"machine": node or None, "source": "node", "fallback": True,
            "path": str(path) if path else None}


def machine_id(home=None) -> Optional[str]:
    """The machine token for receipts, or None when nothing is knowable."""
    return machine_identity(home).get("machine")


__all__ = [
    "FALLBACK_FIELD",
    "MARKER_DIRNAME",
    "MARKER_FILENAME",
    "MAX_TOKEN",
    "machine_id",
    "machine_identity",
    "marker_path",
]
