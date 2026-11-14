"""Who owes whom on a promise, stored and kept current (cr1#1011 train 1).

Owner ruling of 2026-11-11 (option b): direction is STORED on the promise
when it is written and UPDATED when the promise changes hands.

Three fields travel together on a `commitment` event's data:

  owed_direction        "you_owe" (the workspace's primary user owns it) or
                        "owed_to_you" (somebody else owns it);
  owed_direction_owner  the owner id it was worked out for;
  owed_direction_user   the primary user id it was worked out for.

An event that hands a promise to a new owner (`commitment_reassigned`, or the
`commitment_updated` a `Mine` writes) carries the same three under `new_`
names, and the open-row fold copies them onto the row.

Only the event gate writes them (`stamp`, `stamp_new`): a caller's value is
replaced by the gate's own, and dropped when the gate cannot work one out.
Readers go through `stored_direction`, which answers None unless the stored
owner and user still match the row. So a value is never stale: an owner
moved by any path that stored nothing, or a re-provisioned seat, makes the
reader work direction out from the owner exactly as it did before.

The values are `attribution_doors.DIRECTION_YOU_OWE` /
`DIRECTION_OWED_TO_YOU`. stdlib only, no I/O except `user_beside_ledger`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

YOU_OWE = "you_owe"
OWED_TO_YOU = "owed_to_you"
VALUES = (YOU_OWE, OWED_TO_YOU)

FIELDS = ("owed_direction", "owed_direction_owner", "owed_direction_user")
NEW_FIELDS = tuple("new_" + f for f in FIELDS)


def _id(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def owner_of(ev) -> str:
    """The promise's owner, read exactly where `attribution_doors._owner_id`
    and `commitment_state.bucket_of` read it (`cru_match._commitment_field`:
    `owner_id`, `owner_person_id` or `owner`, in data first, then at the top
    level), so the stored value and the worked-out one can never disagree."""
    if not isinstance(ev, dict):
        return ""
    try:
        from cru_match import _commitment_field
        return _id(_commitment_field(ev, "owner_id"))
    except Exception:  # noqa: BLE001 - the same order, by hand
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        for src in (data, ev):
            for key in ("owner_id", "owner_person_id", "owner"):
                if src.get(key) not in (None, ""):
                    return _id(src.get(key))
        return ""


def direction_of(owner_id, user_id) -> Optional[str]:
    """`you_owe` / `owed_to_you`, or None when either side is unknown."""
    owner, user = _id(owner_id), _id(user_id)
    if not owner or not user:
        return None
    return YOU_OWE if owner == user else OWED_TO_YOU


def _fields(names, owner_id, user_id) -> dict:
    direction = direction_of(owner_id, user_id)
    if direction is None:
        return {}
    return dict(zip(names, (direction, _id(owner_id), _id(user_id))))


def stamp(data: dict, owner_id, user_id) -> dict:
    """A copy of a promise's data with the gate's own direction fields."""
    out = {k: v for k, v in data.items() if k not in FIELDS}
    out.update(_fields(FIELDS, owner_id, user_id))
    return out


def stamp_new(data: dict, new_owner_id, user_id) -> dict:
    """A copy of an owner-change event's data with the new direction."""
    out = {k: v for k, v in data.items() if k not in NEW_FIELDS}
    out.update(_fields(NEW_FIELDS, new_owner_id, user_id))
    return out


def carried(data: dict) -> dict:
    """The row patch an owner-change event makes: its stored new direction,
    or all three fields cleared when it stored none (an event written before
    this change, or one the gate could not work out)."""
    data = data if isinstance(data, dict) else {}
    return {f: data.get("new_" + f) or None for f in FIELDS}


def cleared() -> dict:
    """The row patch for an owner that is cleared (`not mine`)."""
    return {f: None for f in FIELDS}


def row_patch(row_data, event_data) -> dict:
    """The fold's patch for one owner change on one row. Keys are touched
    only when the event stored a direction or the row already carries one,
    so a row that never had a stored direction stays byte-identical."""
    patch = carried(event_data) if isinstance(event_data, dict) else cleared()
    row_data = row_data if isinstance(row_data, dict) else {}
    if any(v is not None for v in patch.values()) or any(f in row_data for f in FIELDS):
        return patch
    return {}


def stored_direction(row, user_id) -> Optional[str]:
    """The stored direction, only while it still fits the row: the stored
    owner is the row's owner and the stored user is the given user."""
    if not isinstance(row, dict):
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    direction = data.get("owed_direction")
    if direction not in VALUES:
        return None
    user = _id(user_id)
    owner = owner_of(row)
    if not user or not owner:
        return None
    if _id(data.get("owed_direction_owner")) != owner:
        return None
    if _id(data.get("owed_direction_user")) != user:
        return None
    if direction != direction_of(owner, user):
        return None
    return direction


def user_beside_ledger(events_jsonl_path) -> Optional[str]:
    """The primary user named by the entities.json next to a ledger, or None.
    Never raises."""
    try:
        path = Path(events_jsonl_path).parent / "entities.json"
        ent = json.loads(path.read_text(encoding="utf-8"))
        from primary_user import resolve_primary_user_from_entities
        return _id(resolve_primary_user_from_entities(ent)) or None
    except Exception:  # noqa: BLE001 - an unknown user stores nothing
        return None
