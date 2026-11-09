#!/usr/bin/env python3
"""
The `tasks` connector adapter (cr1#668, lane 6 of cr1#609): what a task tool
(a board of items with an assignee, a creator and a due date) hands the
tasks leg, and the capture rule the owner ruled on 2026-11-03.

THREE INTENTS, NOT A PRODUCT. The fire's connector step asks the declared
`tasks` connector for three things, whatever the product is:

  * `assigned_to_me`  - open items assigned to the owner, changed in the window;
  * `created_by_me`   - open items the owner created or assigned to someone
                        else, changed in the window;
  * `done_since`      - items marked done in the window (the close half).

Each read stays inside the connector's include list and never touches a
board on its exclude list (`connector_reads.allowed`, applied again here as
the belt for an item staged from one anyway).

One more read, by id, for the capture half (cr1#766): `tasks_leg` plans
`tracked_items`, the owner's open rows from this connector, because once
such an item is reassigned away no intent is sure to return it. It reads only
items whose board as last seen is allowed, and an answer on an excluded board
is dropped.

THE CAPTURE RULE (owner ruling, cr1#609, 2026-11-03), in `capture_class`:

  * assigned to the owner, with a due date      -> `promise` (the owner's own);
  * the owner created it or assigned it to someone else, with a due date
                                                -> `waiting_on` (owed to the owner);
  * no due date                                 -> people and project signal only;
  * the owner neither assignee nor creator      -> project signal, never a commitment.

No new commitment kind: a `promise` lands as the owner's own row and a
`waiting_on` as a row owned by the assignee, through the same capture gate
mail and meetings use (`tasks_leg.capture_tasks_and_receipt`).

THE POINTER. Every row the leg writes, and every close or proposal it makes,
carries `<provider>:<item id>` as `source_ref` (the canonical string every
reader already folds through `provenance.dedup_key_of`) and the structured
`task_source_ref` `{provider, kind: "task", board_id, board_name, item_id,
url}`. A done item whose pointer cannot be built is refused, never closed.

Pure: no network, no disk. stdlib only.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: The three questions the fire's connector step asks the tasks connector.
INTENT_ASSIGNED_TO_ME = "assigned_to_me"
INTENT_CREATED_BY_ME = "created_by_me"
INTENT_DONE_SINCE = "done_since"
INTENTS = (INTENT_ASSIGNED_TO_ME, INTENT_CREATED_BY_ME, INTENT_DONE_SINCE)

#: The fields one staged task item carries, in the order the plan names them.
TASK_ITEM_FIELDS = ("item_id", "board_id", "board_name", "title", "due",
                    "assignee_ids", "assignee_names", "assignee_person_ids",
                    "creator_id", "creator_name", "creator_person_id",
                    "done", "done_at", "created_at", "updated_at", "url")

#: The four answers `capture_class` gives.
CLASS_PROMISE = "promise"
CLASS_WAITING_ON = "waiting_on"
CLASS_NO_DUE = "no_due"
CLASS_NO_RELATION = "no_relation"
CAPTURE_CLASSES = (CLASS_PROMISE, CLASS_WAITING_ON, CLASS_NO_DUE,
                   CLASS_NO_RELATION)

REF_KIND = "task"


class TaskPointerError(ValueError):
    """A task item's pointer cannot be completed (no provider or no item
    id). The leg refuses the close; it never writes an unauditable one."""


def _s(v) -> str:
    return str(v if v is not None else "").strip()


def _list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [_s(x) for x in v if _s(x)]
    return [_s(v)] if _s(v) else []


def _key(v) -> str:
    return _s(v).lower()


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return _key(v) in ("true", "yes", "1", "done", "complete", "completed",
                       "closed")


def normalize_task_item(raw: Any, *, provider: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One staged item -> the leg's shape, or None when it is not an item
    (not a dict, or no id and no title). Accepts the common spellings a
    connector step stages (`id` / `gid`, `name`, `assignee` as one value or
    a list, `completed` / `status: done`, `completed_at`, `project` /
    `board`), and keeps every value as text."""
    if not isinstance(raw, dict):
        return None
    item_id = _s(raw.get("item_id") or raw.get("id") or raw.get("gid"))
    title = _s(raw.get("title") or raw.get("name"))
    if not item_id and not title:
        return None
    board_id = _s(raw.get("board_id") or raw.get("project_id")
                  or raw.get("list_id"))
    board_name = _s(raw.get("board_name") or raw.get("project_name")
                    or raw.get("board") or raw.get("project")
                    or raw.get("list_name"))
    assignee_ids = _list(raw.get("assignee_ids")) or _list(raw.get("assignee_id")) \
        or _list(raw.get("assignee"))
    assignee_names = _list(raw.get("assignee_names")) or _list(raw.get("assignee_name"))
    assignee_person_ids = _list(raw.get("assignee_person_ids")) \
        or _list(raw.get("assignee_person_id"))
    creator_id = _s(raw.get("creator_id") or raw.get("assigner_id")
                    or raw.get("created_by"))
    creator_name = _s(raw.get("creator_name") or raw.get("assigner_name"))
    creator_person_id = _s(raw.get("creator_person_id")
                           or raw.get("assigner_person_id"))
    status = _key(raw.get("status"))
    done = _truthy(raw.get("done")) or _truthy(raw.get("completed")) \
        or status in ("done", "complete", "completed", "closed")
    return {
        "provider": _s(raw.get("provider") or provider).lower(),
        "item_id": item_id,
        "board_id": board_id,
        "board_name": board_name,
        "title": title,
        "due": _s(raw.get("due") or raw.get("due_on") or raw.get("due_date"))[:10],
        "assignee_ids": assignee_ids,
        "assignee_names": assignee_names,
        "assignee_person_ids": assignee_person_ids,
        "creator_id": creator_id,
        "creator_name": creator_name,
        "creator_person_id": creator_person_id,
        "done": bool(done),
        "done_at": _s(raw.get("done_at") or raw.get("completed_at")),
        "created_at": _s(raw.get("created_at")),
        "updated_at": _s(raw.get("updated_at") or raw.get("modified_at")),
        "url": _s(raw.get("url") or raw.get("permalink_url")),
    }


def board_names(item: Dict[str, Any]) -> List[str]:
    """Every spelling the item's board goes by (id and display name), for
    `connector_reads.allowed`."""
    return [x for x in (_s(item.get("board_id")), _s(item.get("board_name"))) if x]


def _owner_match(ids: Iterable[str], names: Iterable[str], person_ids: Iterable[str],
                 *, owner_person_id: str, owner_ids, owner_names) -> bool:
    own_ids = {_key(x) for x in owner_ids or () if _key(x)}
    own_names = {_key(x) for x in owner_names or () if _key(x)}
    if owner_person_id and _key(owner_person_id) in {_key(p) for p in person_ids}:
        return True
    if own_ids and own_ids & {_key(x) for x in ids}:
        return True
    if own_names and own_names & {_key(x) for x in names}:
        return True
    return False


def owner_is_assignee(item, *, owner_person_id="", owner_ids=(), owner_names=()) -> bool:
    return _owner_match(item.get("assignee_ids") or [], item.get("assignee_names") or [],
                        item.get("assignee_person_ids") or [],
                        owner_person_id=owner_person_id, owner_ids=owner_ids,
                        owner_names=owner_names)


def owner_is_creator(item, *, owner_person_id="", owner_ids=(), owner_names=()) -> bool:
    return _owner_match([item.get("creator_id") or ""], [item.get("creator_name") or ""],
                        [item.get("creator_person_id") or ""],
                        owner_person_id=owner_person_id, owner_ids=owner_ids,
                        owner_names=owner_names)


def has_assignee(item) -> bool:
    """True when the item names any assignee at all."""
    return bool(list(item.get("assignee_ids") or []) + list(item.get("assignee_names") or [])
                + list(item.get("assignee_person_ids") or []))


def capture_class(item, *, owner_person_id="", owner_ids=(), owner_names=()) -> str:
    """THE capture rule (owner ruling, 2026-11-03). One of `CAPTURE_CLASSES`.

    Assigned to the owner wins over created by the owner: an item the owner
    created and assigned to themselves is the owner's own promise. An item
    the owner created with no other assignee has nobody on the other end to
    be waiting on, so it is signal, not a waiting-on."""
    kw = dict(owner_person_id=owner_person_id, owner_ids=owner_ids,
              owner_names=owner_names)
    mine = owner_is_assignee(item, **kw)
    made = owner_is_creator(item, **kw)
    # Not the owner's: any assignee it names is someone else.
    if not mine and not (made and has_assignee(item)):
        return CLASS_NO_RELATION
    if not _s(item.get("due")):
        return CLASS_NO_DUE
    return CLASS_PROMISE if mine else CLASS_WAITING_ON


def build_task_source_ref(item: Dict[str, Any], *, provider: Optional[str] = None) -> Dict[str, Any]:
    """The structured pointer for an item. Raises `TaskPointerError` when
    it cannot be completed."""
    prov = _s(item.get("provider") or provider).lower()
    item_id = _s(item.get("item_id"))
    if not prov or not item_id:
        raise TaskPointerError(
            "a task item needs its provider and its item id to be pointed at "
            f"(provider={prov!r}, item_id={item_id!r})")
    ref = {"provider": prov, "kind": REF_KIND, "item_id": item_id,
           "board_id": _s(item.get("board_id")),
           "board_name": _s(item.get("board_name"))}
    if _s(item.get("url")):
        ref["url"] = _s(item.get("url"))
    return ref


def source_ref_string(ref: Dict[str, Any]) -> str:
    """`<provider>:<item id>`, the canonical string the book's readers fold."""
    return f"{ref['provider']}:{ref['item_id']}"


def pointer_fields(ref: Dict[str, Any]) -> Dict[str, Any]:
    """Both spellings of the one pointer, for a row's `data`."""
    return {"source_ref": source_ref_string(ref), "task_source_ref": dict(ref)}


def plan_intents(*, after: Optional[str], include=(), exclude=()) -> List[Dict[str, Any]]:
    """The three reads the connector step makes, each bounded by the
    window's start and the connector's lists."""
    return [{"intent": name, "after": after, "include": list(include or []),
             "exclude": list(exclude or [])} for name in INTENTS]


__all__ = [
    "INTENTS", "INTENT_ASSIGNED_TO_ME", "INTENT_CREATED_BY_ME",
    "INTENT_DONE_SINCE", "TASK_ITEM_FIELDS", "CAPTURE_CLASSES",
    "CLASS_PROMISE", "CLASS_WAITING_ON", "CLASS_NO_DUE", "CLASS_NO_RELATION",
    "TaskPointerError", "normalize_task_item", "board_names",
    "owner_is_assignee", "owner_is_creator", "has_assignee",
    "capture_class", "build_task_source_ref", "source_ref_string",
    "pointer_fields", "plan_intents",
]
