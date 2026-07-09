#!/usr/bin/env python3
"""
Canonical event-type vocabulary loader (Phase 1 Foundation, 2026-07).

ENUM HOME DECISION (final — later phases register events HERE, nowhere else):
`shared/data-schemas/events.schema.json` is the ONE home of the event-type
enum. It was already enforced by two independent consumers before this module
existed (run_source_of_truth_test.py Check 4 scans every documented
`"type": "<name>"` literal against it; weekly-audit validates live events
against it), so duplicating the list as a Python literal would create a
second source of truth that drifts. This module makes the same enum
runtime-readable for the append gatekeeper (event_gate.py) without ever
owning a copy of the list.

To register a new event type:
  1. Add it to the `type` enum in shared/data-schemas/events.schema.json.
  2. Document its writer + named consumers in shared/EVENT_TYPES.md
     (no consumer-less writes — Writes-checklist item 5).
Nothing else. The gatekeeper, the source-of-truth test, and weekly-audit all
pick it up from the schema.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import FrozenSet, Optional

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "data-schemas" / "events.schema.json"
)
_ENUM_CACHE: Optional[FrozenSet[str]] = None

# Ratified 2026-07-01 (M, A-vs-B review): every commitment event carries a
# required `data.kind` discriminator. The policy layer (tasks never enter CRU,
# 30-day staleness, promote-to-commitment) keys off this label — Phase 2
# Stage D enforces it as a code-level kind filter.
KIND_VALUES: FrozenSet[str] = frozenset({"promise", "task", "scheduling", "agenda"})

# Fields on a commitment_resolved event that carry a readable commitment
# identity. Mirrors the read-side chain in cru_match.load_open_commitments
# (commitment_id preferred; id fallback; target_id legacy) plus the two
# seq-alias fields the F3 amnesty maps seq → commitment through. A closure
# carrying NONE of these is a dead letter the moment it's written — the
# gatekeeper rejects it at append time (fail loud, not silent).
COMMITMENT_CLOSURE_ID_FIELDS = (
    "commitment_id",
    "id",
    "target_id",
    "commitment_seq",
    "source_event_seq",
)

# Legacy seq-alias id spellings (F2/F3): bare int 86, "86", "seq_86",
# "event_086", "commitment_seq_86" — all resolved read-side as "the commitment
# event at seq N". Lives here (the shared vocabulary home) because BOTH sides
# need it: commitment_state.normalize_commitment_id resolves these on read,
# and event_gate REJECTS an explicit data.id that matches this namespace on
# write (v4.5.2 R1c) — a custom id shaped like a seq alias would shadow seq
# references and make closures resolve to the wrong commitment.
import re as _re  # noqa: E402

LEGACY_SEQ_ID_RE = _re.compile(r"^(?:commitment_seq_|event_|seq_)?0*(\d+)$")


def load_event_types() -> FrozenSet[str]:
    """The canonical type enum, loaded once from events.schema.json.

    Returns an empty frozenset when the schema is missing/unparseable — the
    gatekeeper treats an empty enum as "validation unavailable" (it warns
    rather than rejecting everything on a broken install).
    """
    global _ENUM_CACHE
    if _ENUM_CACHE is None:
        try:
            schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
            enum = schema.get("properties", {}).get("type", {}).get("enum", [])
            _ENUM_CACHE = frozenset(t for t in enum if isinstance(t, str))
        except (OSError, json.JSONDecodeError):
            _ENUM_CACHE = frozenset()
    return _ENUM_CACHE


def is_known_type(event_type) -> bool:
    """True when `event_type` is registered in the schema enum. An empty enum
    (schema unreadable) admits everything — see load_event_types()."""
    types = load_event_types()
    if not types:
        return True
    return isinstance(event_type, str) and event_type in types


__all__ = [
    "KIND_VALUES",
    "LEGACY_SEQ_ID_RE",
    "COMMITMENT_CLOSURE_ID_FIELDS",
    "load_event_types",
    "is_known_type",
]


if __name__ == "__main__":
    types = sorted(load_event_types())
    print(f"{len(types)} registered event types")
    for t in types:
        print(f"  {t}")
