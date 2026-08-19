#!/usr/bin/env python3
"""Resolve the workspace's primary user (the CEO) to a person_id — deterministically.

WHY THIS EXISTS (Bug #102)
Multiple surfaces need "which person_id is the user?" to attribute commitments
(you-owe vs they-owe), to match the user's sent mail in reconciliation, and to
compute the brief. They each checked an `is_user` / `is_primary_user` flag
inline — but on a real workspace NEITHER flag was set on any person, so they all
silently resolved to None. With no user, reconciliation can't attribute the
user's sends and the you-owe count breaks.

THE KEYS THIS READS (SPEC USERKEY1, widened by SPEC USERKEY2)
The schema-canonical pointer is `workspace.user_id` — `workspace_settings.
user_id` in `shared/data-schemas/entities.schema.json`, described there as the
"Canonical signal — preferred over the legacy `is_primary_user` person field".
Until USERKEY1 this resolver read `workspace.user_person_id` instead: a spelling
that appears ZERO times in the schema and that nothing in this codebase writes.
A workspace that stored only the canonical key — no legacy person flags, no
first-name match luck — therefore resolved to None on EVERY consumer of this
seam, silently. It is the fourth instance of the dual-spelling settings class
(v5.9.4 fixed the tz / connector_config / objective_math readers).

USERKEY2 then found the pointer has a THIRD spelling in the wild:
`workspace.primary_user_id`. On a client workspace storing only that one, every
pointer leg missed, the flags were unset and there was no first-name to match,
so the resolver returned None for a full day and the headline's you-owe /
they-owe split inverted against a None owner. The spelling is in our own
universe too — `tests/fixtures/workspace_mini/_hq/data/entities.json` carries it
as the workspace identity key, so our shipped fixture models precisely the
workspace this resolver could not resolve (it only resolves there because that
fixture ALSO sets a person flag). Its writer is unknown: nothing in this repo
writes any pointer spelling (see the note below), so the likeliest origin is an
older provisioning path. Read-side tolerance only — the schema stays
canonical-only, because a schema that blesses three spellings invites a fourth.
That makes this the FIFTH instance of the dual-spelling settings class, and the
first in which one setting turned out to have three spellings rather than two.

This is the single resolver. Fallback order (first hit wins):
  1. `workspace.user_id` — the schema-canonical pointer, and the one the
     schema documents as "preferred over the legacy `is_primary_user` person
     field". A workspace that records its owner as a pointer records it here.
  2. `workspace.user_person_id` — TOLERATED LEGACY. A workspace that hand-set
     it during the Bug #102 era keeps working. When more than one spelling is
     present and they DISAGREE, canonical wins: `user_id` is the key the schema
     designates, so a disagreeing `user_person_id` is by construction the
     deprecated one.
  3. `workspace.primary_user_id` — TOLERATED LEGACY, third spelling (SPEC
     USERKEY2). Same doctrine as leg 2 and for the same reason: canonical is
     FIRST, so on any disagreement the schema's key wins and a legacy spelling
     can only ever supply an answer nothing else had.

     NOTE FOR WHOEVER IS HERE NEXT — there is no WRITER for any pointer.
     Verified across shared/scripts/ and every skill's prose: nothing in this
     repo sets `workspace.user_id`, `workspace.user_person_id`,
     `workspace.primary_user_id`, or the `is_primary_user` person flag.
     Onboarding writes `person_001` and the other `workspace.*` settings and
     stops. So a freshly onboarded workspace still reaches this resolver with
     NO pointer and NO flag, and depends entirely on step 5's first-name
     match. Fixing the read side (USERKEY1, USERKEY2) was necessary and is not
     sufficient; the write side is a separate, unbuilt piece of work. Do not
     read "canonical" here as "the one something maintains" — read it as "the
     one the schema designates".
  4. a person with `is_primary_user` or `is_user` == True (the legacy flags).
  5. a person whose canonical_name's first token == `workspace.user_first_name`
     (every workspace sets user_first_name at onboarding, so this always has a
     fallback signal even when the flags were never written).
  6. None — caller must handle (don't guess a random person).

Pure, stdlib-only, shape-defensive (flat or `entities`-wrapped). The settings
block is MERGED across both shapes rather than first-truthy-picked — same
discipline as the v5.9.4 tz.py fix: a truthy inner `entities.workspace` must not
shadow a top-level `workspace` that still holds the key. Inner wins on conflict.
"""
from __future__ import annotations

import json
from pathlib import Path

# The pointer spellings this seam reads, canonical first. Named constants so the
# USERKEY1 census guard can assert the order from the source of truth rather
# than re-spelling it (a hand-spelled derived constant only pins the spelling).
# Canonical MUST stay first: precedence on a disagreeing workspace is the whole
# doctrine, and every legacy member added after it can only supply an answer no
# earlier key had.
CANONICAL_POINTER_KEY = "user_id"
LEGACY_POINTER_KEY = "user_person_id"
LEGACY_PRIMARY_POINTER_KEY = "primary_user_id"   # SPEC USERKEY2
POINTER_KEYS = (
    CANONICAL_POINTER_KEY,
    LEGACY_POINTER_KEY,
    LEGACY_PRIMARY_POINTER_KEY,
)


def _container(d: dict) -> dict:
    """The entity container, whichever shape the file is in."""
    if not isinstance(d, dict):
        return {}
    inner = d.get("entities")
    return inner if isinstance(inner, dict) else d


def _workspace_settings(d: dict) -> dict:
    """The workspace settings block, merged across both shapes.

    Accepts either the raw file dict or an already-unwrapped container (the two
    coincide for the flat shape, and for an unwrapped container the merge is the
    same dict twice — so callers of `resolve_primary_user_from_entities` see no
    behaviour change). Merge, don't first-truthy-pick: a truthy inner block must
    not shadow a top-level one that still holds the key. Inner wins on conflict.
    """
    if not isinstance(d, dict):
        return {}
    top = d.get("workspace")
    inner = _container(d).get("workspace")
    return {
        **(top if isinstance(top, dict) else {}),
        **(inner if isinstance(inner, dict) else {}),
    }


def _people(d: dict) -> list:
    """The people records, defensively — a list of dicts and nothing else.

    THE SEAM IS THE DEFENSIVE PLACE (review N-1). Every caller of this module
    routes here now, so a malformed `people` must degrade here rather than
    raise into whatever exception clause each caller happens to carry. The
    inline resolvers this seam replaced guarded their own loops with
    `isinstance(p, dict)`; when they were routed through the seam that guard
    went with them, and `objective_math` — whose clause is
    `except (OSError, ValueError)` — turned a workspace it used to resolve
    cleanly into an uncaught AttributeError.

    Non-list `people` (an object, a string, a number) yields no people at all
    rather than iterating into keys or characters. An unidentifiable user
    excludes nobody; a malformed entities file must never crash a caller.
    """
    raw = _container(d).get("people")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict)]


def _entities(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    return json.loads(p.read_text(encoding="utf-8"))


def resolve_primary_user(workspace_root) -> str | None:
    """Return the primary user's person_id, or None if unresolvable."""
    try:
        raw = _entities(workspace_root)
    except Exception:
        return None
    return resolve_primary_user_from_entities(raw)


def resolve_primary_user_from_entities(ent: dict) -> str | None:
    """Same resolution against an already-loaded entities dict (no I/O)."""
    ws = _workspace_settings(ent)
    people = _people(ent)

    # 1-3. explicit pointer — canonical first, both legacy spellings tolerated.
    # A workspace carrying MORE THAN ONE resolves to the canonical one:
    # `user_id` is the key the SCHEMA designates, so a disagreeing
    # `user_person_id` / `primary_user_id` is by construction the deprecated
    # one. (Not "the one a writer maintains" — see the docstring note: this repo
    # has no writer for any pointer.)
    for key in POINTER_KEYS:
        pointer = ws.get(key)
        if not pointer:
            continue
        if any(p.get("id") == pointer for p in people):
            return pointer
        return pointer  # honor it even if the person record isn't loaded here

    # 4. legacy flags
    for p in people:
        if p.get("is_primary_user") or p.get("is_user"):
            return p.get("id")

    # 5. first-name match against workspace.user_first_name
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


__all__ = [
    "resolve_primary_user",
    "resolve_primary_user_from_entities",
    "CANONICAL_POINTER_KEY",
    "LEGACY_POINTER_KEY",
    "LEGACY_PRIMARY_POINTER_KEY",
    "POINTER_KEYS",
]


if __name__ == "__main__":
    import sys
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    print(resolve_primary_user(ws))
