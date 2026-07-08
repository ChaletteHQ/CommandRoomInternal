#!/usr/bin/env python3
"""
source_skill back-compat (client migration, v3.19.x — FIX1 Batch A).

WHY
---

Pre-v2.14.27 the scheduled orchestrators tagged every event they wrote with a
`cr-`-prefixed `source_skill` (`cr-inbox`, `cr-commitments`, `cr-past-meetings`,
`cr-dont-forget`, ...). The taskId rename dropped the prefix, and the writers
now emit the bare forms (`inbox`, `commitments`, `past-meetings`, `pulse`).

The live per-client workspaces already have `events.jsonl` history written with
the legacy `cr-*` forms.
events.jsonl is APPEND-ONLY — we never rewrite history. So every downstream
READER that filters events by `source_skill` must accept BOTH the legacy
`cr-*` form and the bare form, or those workspaces' historical events silently
stop counting (operator-report undercounts, pack_run fallback links vanish,
repeat-chase escalation misfires).

This module is the single normalization point. Readers normalize both the
event's value and their filter target before comparing.

NOT-A-PREFIX-STRIP CASE
-----------------------

`dont-forget` was renamed to a NEW display name (`pulse`), not merely
de-prefixed — so `cr-dont-forget` must map to `pulse`, which a naive prefix
strip would get wrong. That single rename is encoded in `_LEGACY_ALIASES`;
everything else is a clean `cr-` strip.
"""
from __future__ import annotations


# Legacy source_skill values whose canonical bare form is NOT just the cr-strip.
_LEGACY_ALIASES = {
    "cr-dont-forget": "pulse",   # v2.14.27 renamed to display name "Pulse", not a prefix strip
    "dont-forget": "pulse",      # bare legacy form (if any workspace wrote it mid-migration)
}


def normalize_source_skill(value):
    """Canonicalize a source_skill value to its current bare form.

    - Known renames (`cr-dont-forget` → `pulse`) go through the alias table.
    - Any other `cr-`-prefixed value has the prefix stripped.
    - Everything else (already-bare, None, non-str) is returned unchanged.
    """
    if not isinstance(value, str):
        return value
    if value in _LEGACY_ALIASES:
        return _LEGACY_ALIASES[value]
    if value.startswith("cr-"):
        return value[3:]
    return value


def source_skill_matches(event_value, *canonical) -> bool:
    """True if `event_value` matches any of the `canonical` task slugs,
    tolerating the legacy `cr-*` prefix on either side.

    Use this anywhere a reader filters events by source_skill so a workspace
    whose history predates the v2.14.27 rename keeps matching:

        if source_skill_matches(ev.get("source_skill"), "commitments"): ...
        # matches both legacy 'cr-commitments' and bare 'commitments'
    """
    ev = normalize_source_skill(event_value)
    return any(ev == normalize_source_skill(c) for c in canonical)


__all__ = ["normalize_source_skill", "source_skill_matches"]
