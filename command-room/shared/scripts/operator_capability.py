#!/usr/bin/env python3
"""
operator_capability — capabilities the OPERATOR issues with a release (HELDOP1).

WHAT A CAPABILITY IS, AND WHY THIS IS NOT A CONFIG READER
=========================================================
Some dispositions are not a workspace's to choose. The held tier is the first:
routing the weakest captures out of sight is a decision the person who builds
Command Room makes, once, on their own book — not something each workspace
earns or asks for, and never by reading a workspace's own captures. This file
ships, so it is read where those captures live; say it the way it is true
there (REVIEW_HELDOP1 F-7/F-8).

The original design fenced that flip behind a MEASUREMENT taken inside the
workspace (`precision_gate`, on census records written by
`scripts/precision_report.py`). That fence was structurally unpassable
everywhere but the operator's own box, because the producer sits at repo root
and only `command-room/` is ever written to a client repo: the directory the
gate scanned was empty forever, and a permanently-red gate reads to its owner
as "your accuracy is bad" when it means "the instrument was never delivered."
M retired the bar on 2026-08-22. This module is what replaced it.

THE MECHANISM IS THE PAYLOAD, AND THAT IS THE FENCE
---------------------------------------------------
A grant lives in `shared/config/operator_capabilities.json` — a file in the
plugin payload. Payload is written in the staging repo, ships with a version,
and reaches a workspace only through push -> plugin-update -> promote. That is
the ONE writer, and it is a release action.

The read is anchored to THIS MODULE'S OWN LOCATION (`__file__`), never to a
workspace root, and this module accepts no workspace argument at all. That is
deliberate and it is the whole fence: a reader that could be pointed at a
workspace is a capability the workspace could grant itself. The architecture
already says which half is which — learned and customer state lives under
`_hq/`, plugin paths are overwritten on update — so a capability kept in the
payload is, by that same rule, not workspace state and not a knob.

Concretely: a person may write anything they like into their own skill config
(`_hq/config/`, the FRP1 store) — every key, including one named after a
capability — and this module's answer does not move. Pinned directly in
`tests/run_heldop1_test.py`, and proven by REMOVAL: point the read at a
workspace and that pin goes red.

FAIL CLOSED, ALWAYS
-------------------
A missing file, unreadable JSON, a wrong shape, an unknown name, or a `granted`
that is anything other than the boolean `True` all mean NOT GRANTED. There is
no path through this module that returns granted on an error, because the one
thing the fence it replaced was explicitly written never to do is fail open.

Read-only. Writes nothing, ever. Stdlib only.
"""
from __future__ import annotations

import json
from pathlib import Path

# The payload file. Anchored to this module, exactly as
# `surface_context._PROFILES_PATH` anchors its own shipped config — and NEVER
# to a workspace root. See the module docstring: this line is the fence.
CAPABILITIES_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "operator_capabilities.json"
)

# The capability names this module knows. An unknown name is NOT granted (and
# says so by code) rather than raising: a caller asking about a capability that
# no longer exists must get a refusal, not a crash on the refusal path.
HELD_TIER_ROUTING = "held_tier_routing"
KNOWN_CAPABILITIES = (HELD_TIER_ROUTING,)

# Blocker codes. Stable, UPPER_SNAKE, ordered most-structural first. The caller
# owns the words a human reads; this module hands back codes and booleans.
NOT_GRANTED = "NOT_GRANTED"
UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
NO_CAPABILITY_FILE = "NO_CAPABILITY_FILE"
UNREADABLE_CAPABILITY_FILE = "UNREADABLE_CAPABILITY_FILE"


def _load() -> tuple[dict, list]:
    """The payload grant table, or ({}, [code]). Never raises."""
    path = CAPABILITIES_PATH
    try:
        if not path.is_file():
            return {}, [NO_CAPABILITY_FILE]
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — see FAIL CLOSED in the docstring.
        return {}, [UNREADABLE_CAPABILITY_FILE]
    if not isinstance(raw, dict):
        return {}, [UNREADABLE_CAPABILITY_FILE]
    table = raw.get("capabilities")
    if not isinstance(table, dict):
        return {}, [UNREADABLE_CAPABILITY_FILE]
    return table, []


def capability_status(name: str) -> dict:
    """Has the operator issued `name` with this release?

    Returns:
      {"capability": <name>,
       "granted": bool,          # the ONLY field a fence should read
       "blockers": [CODE, ...],  # empty iff granted
       "granted_on": <str|None>, # provenance, for a human reading a receipt
       "source": <str>}          # the payload path consulted, for the same

    Takes NO workspace argument, by design. Nothing about a workspace can move
    this answer.
    """
    source = str(CAPABILITIES_PATH)
    if name not in KNOWN_CAPABILITIES:
        return {"capability": name, "granted": False,
                "blockers": [UNKNOWN_CAPABILITY, NOT_GRANTED],
                "granted_on": None, "source": source}
    table, blockers = _load()
    if blockers:
        return {"capability": name, "granted": False,
                "blockers": list(blockers) + [NOT_GRANTED],
                "granted_on": None, "source": source}
    entry = table.get(name)
    if not isinstance(entry, dict):
        return {"capability": name, "granted": False,
                "blockers": [NOT_GRANTED], "granted_on": None,
                "source": source}
    # `is True` and not truthiness: a string "true", a 1, or a non-empty list
    # are all shapes a hurried edit produces, and none of them is a decision.
    granted = entry.get("granted") is True
    on = entry.get("granted_on")
    return {"capability": name, "granted": granted,
            "blockers": [] if granted else [NOT_GRANTED],
            "granted_on": on if isinstance(on, str) else None,
            "source": source}


def is_granted(name: str) -> bool:
    """`capability_status(name)["granted"]`, for a caller that wants the bool."""
    return bool(capability_status(name).get("granted"))


__all__ = [
    "CAPABILITIES_PATH", "HELD_TIER_ROUTING", "KNOWN_CAPABILITIES",
    "NOT_GRANTED", "UNKNOWN_CAPABILITY", "NO_CAPABILITY_FILE",
    "UNREADABLE_CAPABILITY_FILE",
    "capability_status", "is_granted",
]
