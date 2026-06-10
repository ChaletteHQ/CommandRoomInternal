#!/usr/bin/env python3
"""Resolve the workspace's primary user (the CEO) to a person_id — deterministically.

WHY THIS EXISTS (Bug #102)
Multiple surfaces need "which person_id is the user?" to attribute commitments
(you-owe vs they-owe), to match the user's sent mail in reconciliation, and to
compute the brief. They each checked an `is_user` / `is_primary_user` flag
inline — but on a real workspace NEITHER flag was set on any person, so they all
silently resolved to None. With no user, reconciliation can't attribute the
user's sends and the you-owe count breaks.

This is the single resolver. Fallback order (first hit wins):
  1. `workspace.user_person_id` — an explicit pointer (most authoritative).
  2. a person with `is_primary_user` or `is_user` == True (the legacy flags).
  3. a person whose canonical_name's first token == `workspace.user_first_name`
     (every workspace sets user_first_name at onboarding, so this always has a
     fallback signal even when the flags were never written).
  4. None — caller must handle (don't guess a random person).

Pure, stdlib-only, shape-defensive (flat or `entities`-wrapped).
"""
from __future__ import annotations

import json
from pathlib import Path


def _entities(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["entities"] if isinstance(d.get("entities"), dict) else d


def resolve_primary_user(workspace_root) -> str | None:
    """Return the primary user's person_id, or None if unresolvable."""
    try:
        ent = _entities(workspace_root)
    except Exception:
        return None
    return resolve_primary_user_from_entities(ent)


def resolve_primary_user_from_entities(ent: dict) -> str | None:
    """Same resolution against an already-loaded entities dict (no I/O)."""
    ws = ent.get("workspace") if isinstance(ent.get("workspace"), dict) else {}
    people = ent.get("people") or []

    # 1. explicit pointer
    pointer = ws.get("user_person_id")
    if pointer and any(p.get("id") == pointer for p in people):
        return pointer
    if pointer:
        return pointer  # honor it even if the person record isn't loaded here

    # 2. legacy flags
    for p in people:
        if p.get("is_primary_user") or p.get("is_user"):
            return p.get("id")

    # 3. first-name match against workspace.user_first_name
    fn = (ws.get("user_first_name") or "").strip().lower()
    if fn:
        # exact full-name or first-token match
        first_tok = None
        for p in people:
            cn = (p.get("canonical_name") or "").strip().lower()
            if not cn:
                continue
            toks = cn.split()
            if cn == fn or (toks and toks[0] == fn):
                return p.get("id")
            if first_tok is None and toks and toks[0] == fn:
                first_tok = p.get("id")
    return None


__all__ = ["resolve_primary_user", "resolve_primary_user_from_entities"]


if __name__ == "__main__":
    import sys
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    print(resolve_primary_user(ws))
