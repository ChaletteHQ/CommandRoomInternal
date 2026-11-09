#!/usr/bin/env python3
"""A captured task follows its board (cr1#766).

Once the tasks leg has captured a board item, capture skips it for good, so
a later change on the board never reached the commitment: a new due date,
a new title, a new assignee, an item reopened after its close. The book and
the board drifted apart.

`follow_items` compares every staged item whose row is already on the book
with the board state LAST SEEN for that row, and lands each change through
the existing writers, with the board item as the source pointer:

  * due date moved         -> ONE `commitment_updated` carrying `new_due`
                              (`_move_due`: `prior_due` kept, reversible)
  * retitled               -> `commitment_state.edit_commitment_wording`
  * reassigned             -> `commitment_state.reassign_commitment`,
                              confirmed: the board is the owner's own record
                              of who holds the item, and an unconfirmed row
                              would lapse through review expiry into a drop.
                              The machine word stays on the row, it is said
                              on the receipt's line, and a later hand-back
                              follows as another reassign.
  * reopened on the board  -> `commitment_state.reopen_commitment`, when the
                              row's standing close is the board's own (the
                              item's pointer, or the owner's confirm of the
                              board's own proposal) and the item is not
                              done; the other changes of the same transition
                              land after it, against the row as folded
  * on an excluded board   -> nothing on the commitment, done or not
                              (owner ruling on cr1#766, 2026-11-07: excluded
                              means excluded). The first time a followed item
                              is seen there, ONE record-keeping "left the
                              boards" marker notes the board it is on now, so
                              it is never named for a read by id again
                              (cr1#900, owner ruling 2026-11-07).

THE OWNER'S HAND WINS (cr1#901, owner ruling 2026-11-07). When the newest
act that set the row's owner is the owner's own (a reassignment by hand, a
`Mine`, a `not mine`), the board never moves the owner again. A board that
names someone else is noted once as the board's own value and said once on
the receipt; the owner's choice stands, and the owner's next word on the row
is the answer.

A done item on an included board whose row is open is NOT closed here: the
close half reads done items and decides between close and propose by the
connector's `close_mode`. A refused write is counted and named on the
receipt, never raised: a bad row must not cost the fire its captures.

THE BOARD IS FOLLOWED, NEVER OBEYED (review, 2026-11-07). A write happens
only when the BOARD'S OWN value changed, so the comparison is the board now
against the board as last seen, never against the row: the owner's own push,
rewording or confirmed reassignment stands until the board itself changes
that field, however often the item is touched (a comment moves `updated_at`
and changes nothing here). Last seen is the capture row's own due, title,
owner and board (so a row captured before this module needs no migration),
then the `board_state` on the newest record-keeping note this module wrote.
That holds for a tool with no `updated_at` too. A reopen needs the row's
standing close, read across the whole book, to be the board's: a close made
by hand or from mail is never reopened from here. Nothing from an excluded
board is written (no title, no date, no reopen), and a connector whose
capture dial is off is not followed.

Follow binds to the ORIGINAL capture row. A split or a sub-item copies the
item's `source_ref` onto its child rows, and those are the owner's own
breakdown: never read as the capture row, never written.

ANOTHER ITEM WITH THE SAME ID. A staged item on another board is the same
item, moved, when its creation time matches the row's; another creation
time is a different item sharing an id, counted, said on the summary line
and left alone. With no creation time on one side, the title decides.
Pure-ish: no network I/O.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connector_adapters import tasks as _tasks  # noqa: E402

#: The receipt fields this module fills, all present, zero when nothing moved.
EMPTY = {"n_followed": 0, "n_followed_updated": 0, "n_followed_reassigned": 0,
         "n_followed_reopened": 0, "n_followed_noted": 0,
         "n_followed_left": 0, "n_followed_refused": 0,
         "n_followed_collisions": 0}

#: `change` word -> its receipt count.
_COUNT_OF = {"updated": "n_followed_updated", "reassigned": "n_followed_reassigned",
             "reopened": "n_followed_reopened", "noted": "n_followed_noted",
             "left": "n_followed_left", "refused": "n_followed_refused"}

_NOT_ON_FILE = "the new assignee is not a person on file"

#: cr1#901: the `why` on a board owner the owner's own reassignment outranks.
OWNER_SET_BY_HAND = "owner_set_by_hand"

#: cr1#900: the reason on the one marker written when a followed item is
#: first seen on a board the owner excluded.
LEFT_BOARDS_NOTE = ("the item moved to a board you excluded; it is no longer "
                    "followed and its commitment is unchanged")


def _s(v) -> str:
    return str(v if v is not None else "").strip()


def _record_for(raw: dict, records: List[dict]) -> Optional[dict]:
    want = _s(raw.get("category") or raw.get("connector"))
    if want:
        for r in records:
            if r.get("category") == want:
                return r
        return None
    return records[0] if len(records) == 1 else None


def _is_child(row: dict) -> bool:
    """A row split from, or added under, the captured one (the writers copy
    the parent's `source_ref` onto it)."""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    return bool(data.get("split_from") or data.get("parent_id"))


def _capture_row(rows: List[dict]) -> Optional[dict]:
    """The item's capture row on the book (the newest `commitment` or
    `commitment_observed` pointing at it that is not a child of it), or
    None."""
    caps = [r for r in rows if r.get("type") in ("commitment", "commitment_observed")
            and not _is_child(r)]
    return caps[-1] if caps else None


def _parse(raw):
    from cru_match import _parse_ts
    return _parse_ts(_s(raw)) if _s(raw) else None


def board_seen(rows: List[dict]) -> Optional[str]:
    """The newest `item_updated_at` any row pointing at the item carries
    (the capture row's, or a note's), or None. A staged item no newer than
    this is a stale read, and never reopens a row."""
    best = None
    for r in rows:
        data = r.get("data") if isinstance(r.get("data"), dict) else {}
        when = _parse(data.get("item_updated_at"))
        if when is not None and (best is None or when > best[0]):
            best = (when, _s(data.get("item_updated_at")))
    return best[1] if best else None


def board_state(rows: List[dict]) -> Dict[str, str]:
    """The board as last seen for this row: the capture row's own due,
    title, owner and board, then the newest note's `board_state` over them.
    The capture row is the baseline, so a row with no note needs nothing
    migrated, and a change the OWNER made on the row is not in here."""
    cap = _capture_row(rows) or {}
    data = cap.get("data") if isinstance(cap.get("data"), dict) else {}
    ref = data.get("task_source_ref") if isinstance(data.get("task_source_ref"), dict) else {}
    state = {"due": _s(data.get("due"))[:10], "title": _s(data.get("title")),
             "owner_id": _s(data.get("owner_id")),
             "owner_name": _s(data.get("owner_external")),
             "owner_refused": "",
             "board_id": _s(ref.get("board_id")), "board_name": _s(ref.get("board_name"))}
    for r in rows:
        d = r.get("data") if isinstance(r.get("data"), dict) else {}
        if r.get("type") == "commitment_updated" and isinstance(d.get("board_state"), dict):
            state.update({k: _s(v) for k, v in d["board_state"].items() if k in state})
    return state


def left_boards(rows: List[dict]) -> bool:
    """cr1#900: is the newest board note on this row the "left the boards"
    marker? Then the item was last seen on a board the owner excluded, and
    nothing names it for a read by id; a later note (the item seen again on
    a followed board) takes that back."""
    newest = None
    for r in rows:
        d = r.get("data") if isinstance(r.get("data"), dict) else {}
        if r.get("type") == "commitment_updated" and isinstance(d.get("board_state"), dict):
            newest = d
    return bool(newest and newest.get("left_boards") is True)


def board_closed(closing_event: Optional[dict], ref_string: str, proposals=()) -> bool:
    """Did the board close this row? `closing_event` is the row's STANDING
    close, from the book's own closure fold (every closer type, every
    writer, not only the rows pointing at the item). True when it carries
    the item's pointer, or when it is the owner's Confirm of the board's own
    close proposal (`proposals`, the row's `commitment_review_proposed`
    rows): the confirm writes a `session:` pointer but keeps the proposal's
    evidence word for word. A close made by hand or from mail is neither."""
    from connector_adapters.provenance import dedup_key_of
    want = dedup_key_of(ref_string)
    if not want or not isinstance(closing_event, dict):
        return False
    data = closing_event.get("data") if isinstance(closing_event.get("data"), dict) else {}
    if dedup_key_of(data.get("source_ref")) == want:
        return True
    from event_seq import event_seq
    said = _s(data.get("evidence"))
    closed_at = event_seq(closing_event, context="tasks_follow.board_closed")
    for p in proposals or ():
        d = p.get("data") if isinstance(p.get("data"), dict) else {}
        asked_at = event_seq(p, context="tasks_follow.board_closed")
        if (said and said == _s(d.get("evidence"))
                and dedup_key_of(d.get("source_ref")) == want
                and asked_at is not None and closed_at is not None
                and asked_at < closed_at):
            return True
    return False


def _commitment_id(ev: dict) -> str:
    from cru_match import _commitment_id
    return str(_commitment_id(ev))


def _staged(items, records) -> List[Tuple[dict, dict, dict, str, str]]:
    """Each staged item once: `(item, rec, ref, ref string, dedup key)`. An
    owner-made item assigned to the owner comes back from both capture
    intents, so a pointer staged twice is ONE item, the newest read of it."""
    from connector_adapters.provenance import dedup_key_of

    picked: Dict[str, tuple] = {}
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        rec = _record_for(raw, records)
        if rec is None:
            continue
        item = _tasks.normalize_task_item(raw, provider=rec.get("provider"))
        if item is None:
            continue
        try:
            ref = _tasks.build_task_source_ref(item)
        except _tasks.TaskPointerError:
            continue
        ref_string = _tasks.source_ref_string(ref)
        key = dedup_key_of(ref_string) or ""
        have = picked.get(key)
        if have is not None and not _s(have[0].get("title")) and _s(item.get("title")):
            pass  # cr1#900: a whole read wins over an excluded answer's stub
        elif have is not None:
            old, new = _parse(have[0].get("updated_at")), _parse(item.get("updated_at"))
            if old is None or new is None or new <= old:
                continue
        picked[key] = (item, rec, ref, ref_string, key)
    return list(picked.values())


def _other_item(item: dict, state: Dict[str, str], cap: dict, *,
                excluded: bool = False) -> bool:
    """Is this a DIFFERENT item that shares the row's id? The pointer is
    `<provider>:<item id>`, and a tool that numbers items per board repeats
    ids. On the same board it is the same item. On another board the item's
    own creation time decides when both sides have one (a move keeps it,
    however much else changed); with none, another title is another item."""
    boards = [(state.get("board_id"), _s(item.get("board_id"))),
              (state.get("board_name"), _s(item.get("board_name")))]
    known = [(a, b) for a, b in boards if a and b]
    if not known or any(a == b for a, b in known):
        return False
    data = cap.get("data") if isinstance(cap.get("data"), dict) else {}
    born, here = _parse(data.get("item_created_at")), _parse(item.get("created_at"))
    if born is not None and here is not None:
        return born != here
    if excluded and not _s(item.get("title")):
        # cr1#900: the answer to a read by id that landed on an excluded
        # board is staged without its content; the row has no creation time
        # to test it against (`_unplaceable` handles one that does), so the
        # id it was read by stands.
        return False
    return _s(item.get("title")) != _s(state.get("title"))


def _unplaceable(item: dict, cap: dict) -> bool:
    """cr1#900: an excluded answer staged as a stub (no title) and without
    its creation time, for a row that knows its item's creation time. It
    cannot be told from another item sharing the id (a tool that numbers
    items per board repeats ids), so it is never taken for this one: no
    marker, no collision line, and the item stays followed."""
    data = cap.get("data") if isinstance(cap.get("data"), dict) else {}
    return (not _s(item.get("title")) and _parse(item.get("created_at")) is None
            and _parse(data.get("item_created_at")) is not None)


def follow_items(workspace_root, items, records, rows, opens, *, user_person_id,
                 owner_ids=(), owner_names=(), source_skill: str) -> Dict[str, Any]:
    """Compare every staged item that already has a row with the board state
    last seen for it and land the changes. `rows` is
    `tasks_leg.captured_rows`' answer, `opens` the open set the capture pass
    loaded. Returns the receipt fields (`EMPTY`'s keys plus `followed`, one
    entry per change, and `follow_errors`)."""
    from connector_reads import allowed

    out: Dict[str, Any] = dict(EMPTY)
    out["followed"] = []
    out["follow_errors"] = []
    open_by_id = {_commitment_id(ev): ev for ev in opens or [] if isinstance(ev, dict)}
    book = None
    owner_acts: Optional[Dict[str, dict]] = None

    def hand_owner(cid: str) -> Optional[dict]:
        nonlocal owner_acts
        if owner_acts is None:
            owner_acts = _owner_acts(workspace_root)
        return owner_acts.get(cid)
    for item, rec, ref, ref_string, key in _staged(items, records):
        have = rows.get(key) or []
        cap = _capture_row(have)
        if cap is None:
            continue
        state = board_state(have)
        cid = _commitment_id(cap)
        excluded = not allowed(rec, *_tasks.board_names(item), must_match=True)
        if excluded and _unplaceable(item, cap):
            continue  # cr1#900: nothing marked, nothing said, still followed
        if _other_item(item, state, cap, excluded=excluded):
            out["n_followed_collisions"] += 1
            continue
        out["n_followed"] += 1
        live = open_by_id.get(cid)
        try:
            by_board = False
            if live is None and not excluded and not item.get("done"):
                if book is None:
                    book = _book_closes(workspace_root)
                closing = book[0].closing_event(cid, cap.get("seq"))
                by_board = board_closed(closing, ref_string, book[1].get(cid))
            changes = _follow_one(
                workspace_root, item, ref, ref_string, cid, cap, live, state,
                excluded=excluded, user_person_id=user_person_id,
                owner_ids=owner_ids, owner_names=owner_names,
                source_skill=source_skill,
                closed_by_board=by_board,
                seen=board_seen(have),
                was_followed=allowed(rec, *_tasks.board_names(state), must_match=True),
                hand_owner=hand_owner)
        except Exception as exc:  # noqa: BLE001 - a bad row never costs the fire
            out["n_followed_refused"] += 1
            out["follow_errors"].append({"item_id": item.get("item_id"), "commitment_id": cid,
                                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        for change in changes:
            out[_COUNT_OF[change["change"]]] += 1
            out["followed"].append(dict(change, item_id=item.get("item_id"),
                                        commitment_id=cid, source_ref=ref_string))
    return out


def _book_closes(workspace_root) -> tuple:
    """`(closure index, {commitment id: its close proposals})`, one read."""
    from closure_index import build_closure_index
    from events_io import iter_events_org_scoped
    events = list(iter_events_org_scoped(workspace_root))
    proposals: Dict[str, List[dict]] = {}
    for ev in events:
        if ev.get("type") == "commitment_review_proposed":
            cid = _s((ev.get("data") or {}).get("commitment_id"))
            proposals.setdefault(cid, []).append(ev)
    return build_closure_index(events), proposals


def _open_row(workspace_root, cid: str) -> Optional[dict]:
    """The row as folded now (after a reopen, the capture row is out of
    date: it knows nothing of what followed it)."""
    from cru_match import load_open_commitments
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    for ev in load_open_commitments(str(events_path), workspace_root=workspace_root):
        if isinstance(ev, dict) and _commitment_id(ev) == cid:
            return ev
    return None


def _sets_owner(ev: dict) -> bool:
    """cr1#901: does this act set the row's owner (a reassignment naming an
    owner, a `Mine`, a `not mine`)? A counterparty restore does not."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if ev.get("type") == "commitment_reassigned":
        return bool(d.get("new_owner_id")) and d.get("counterparty_restored") is not True
    if ev.get("type") == "commitment_updated":
        return bool(d.get("owner_confirmed") or d.get("owner_cleared"))
    return False


def _owner_acts(workspace_root) -> Dict[str, dict]:
    """`{commitment id: the newest act that set its owner}`, kept only when
    that act is the owner's own (`event_types.is_customer_act`) and it set
    the owner: a reassignment by hand, a `not mine`, or a `Mine` that took
    the row back from someone else (a `Mine` that only confirmed the owner
    already on the row changes nothing and is not a reassignment). One read
    of the book. A reassignment the board follow made is the product's,
    never the owner's, so a row the board last moved is followed as
    before."""
    from event_types import is_customer_act
    from events_io import iter_events_org_scoped
    held: Dict[str, str] = {}
    newest: Dict[str, Optional[dict]] = {}
    for ev in iter_events_org_scoped(workspace_root):
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "commitment":
            held[_commitment_id(ev)] = _s(d.get("owner_id"))
            continue
        if not _sets_owner(ev):
            continue
        target = _s(d.get("commitment_id") or d.get("target_id") or ev.get("commitment_id"))
        if not target:
            continue
        to = "" if d.get("owner_cleared") else _s(d.get("new_owner_id"))
        # An act that leaves the owner where it was (a `Mine` on the owner's
        # own row, a `theirs to` naming the person already on it) moved
        # nothing and is not a reassignment.
        moved = to != held.get(target, "")
        held[target] = to
        if moved:
            newest[target] = ev if is_customer_act(ev) else None
    return {cid: ev for cid, ev in newest.items() if ev is not None}


def _follow_one(workspace_root, item, ref, ref_string, cid, cap, live, state, *, excluded,
                user_person_id, owner_ids, owner_names, source_skill,
                closed_by_board: bool = False, seen=None, was_followed: bool = True,
                hand_owner=None) -> List[dict]:
    """The changes for ONE item against ONE row, each landed as it is found."""
    import commitment_state as cs
    from capture_gate import parse_iso_date
    from event_types import MACHINE

    changes: List[dict] = []
    done = bool(item.get("done"))
    if excluded:
        # Nothing from a board the owner shut is written on the commitment:
        # no title, no date, no reopen, no close and no proposal (owner
        # ruling on cr1#766). cr1#900: the first sighting of a followed
        # open row there writes ONE marker naming the board it is on now,
        # so the plan never names it for a read by id again. Nothing else
        # from that board is kept, not even its clock.
        if live is not None and was_followed:
            now = dict(state, board_id=_s(item.get("board_id")),
                       board_name=_s(item.get("board_name")))
            _note_board_seen(workspace_root, cid, live, ref_string, "", now,
                             note=LEFT_BOARDS_NOTE, source_skill=source_skill,
                             left_boards=True)
            changes.append({"change": "left"})
        return changes
    if done:
        return changes  # the close half's, on an included board
    if live is None:
        # The row is closed (or observed, never open). An item open again on
        # the board reopens it only when the board closed it; anything else
        # is history. A read no newer than the board state last seen is a
        # stale one and proves nothing.
        now_on_board = _parse(item.get("updated_at"))
        stale = (now_on_board is not None and _parse(seen) is not None
                 and now_on_board <= _parse(seen))
        if cap.get("type") != "commitment" or not closed_by_board or stale:
            return changes
        res = cs.reopen_commitment(
            workspace_root, cid, reopened_by=MACHINE,
            reason="reopened on the board", source_skill=source_skill,
            source_ref=ref_string, actor=MACHINE,
            extra_data=dict(_tasks.pointer_fields(ref)))
        if res.get("status") == "reopened":
            changes.append({"change": "reopened", "evidence": "open again on the board"})
        else:
            if res.get("status") not in ("already_open", "not_closed"):
                changes.append({"change": "refused", "why": res.get("status")})
            return changes
        live = _open_row(workspace_root, cid)

    row = live if live is not None else cap
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    now = dict(state)
    moved = False
    for key, value in (("board_id", _s(item.get("board_id"))),
                       ("board_name", _s(item.get("board_name")))):
        if value and value != state.get(key):
            now[key] = value
            moved = True

    title = _s(item.get("title"))
    if title and title != state["title"]:
        now["title"] = title
        moved = True
        if title[:300] != _s(data.get("title"))[:300]:
            cs.edit_commitment_wording(workspace_root, cid, edited_by=MACHINE,
                                       source_skill=source_skill, new_title=title,
                                       reason=f"retitled on the board ({ref_string})",
                                       source_ref=ref_string)
            changes.append({"change": "updated", "field": "title", "to": title})

    note = ""
    due = _s(item.get("due"))[:10]
    if due != state["due"]:
        now["due"] = due
        moved = True
        if not due:
            # A date taken off the board says nothing about when it is owed:
            # the row keeps the date it has, and the note says so.
            note = "the due date was cleared on the board; the row keeps its date"
            changes.append({"change": "noted", "field": "due", "to": ""})
        elif not parse_iso_date(due):
            changes.append({"change": "refused", "field": "due",
                            "why": f"the board's due date is not a date: {due!r}"})
        elif due != _s(data.get("due"))[:10]:
            _move_due(workspace_root, cid, row, due, ref_string, source_skill=source_skill)
            changes.append({"change": "updated", "field": "due", "to": due})

    new_id, new_name = _board_owner(item, user_person_id=user_person_id, owner_ids=owner_ids,
                                    owner_names=owner_names, workspace_root=workspace_root)
    if _same_owner(state, new_id, new_name, item):
        if state.get("owner_refused"):
            now["owner_refused"] = ""  # the board went back: nobody is waiting
            moved = True
    elif hand_owner is not None and hand_owner(cid) is not None:
        # cr1#901: the owner set this row's owner by hand, and that stands
        # over the board. The board's new value is kept as the board's (so
        # it is said once, and a name later added to the people on file is
        # the same person, not news), and the receipt says so once. Nothing
        # on the row moves; the owner's next word on it is the answer.
        now.update(owner_id=new_id, owner_name="" if new_id else new_name,
                   owner_refused="")
        moved = True
        if not (new_id and new_id == _s(data.get("owner_id"))):
            note = note or ("the board names someone else; your own reassignment "
                            "stands")
            changes.append({"change": "noted", "field": "owner",
                            "to": new_id or new_name, "why": OWNER_SET_BY_HAND,
                            "to_name": _said_name(workspace_root, new_id, new_name,
                                                  user_person_id),
                            "title": _s(data.get("title"))})
    elif new_id and new_id in _other_side(data):
        # The board gave the item to the person it is owed to (or, on a
        # waiting-on row, to the owner it was owed to). Moving the owner
        # would leave one person on both sides, and a card telling someone
        # they owe themselves; a fresh read of the item would not capture it
        # at all. The row is left as it is and the receipt says so.
        now.update(owner_id=new_id, owner_name="", owner_refused="")
        moved = True
        note = note or ("the board gave this to the person it is owed to; "
                        "the row is left as it was")
        changes.append({"change": "noted", "field": "owner", "to": new_id,
                        "why": "owner_is_other_side"})
    elif new_id:
        now.update(owner_id=new_id, owner_name="", owner_refused="")
        moved = True
        if new_id != _s(data.get("owner_id")):
            res = cs.reassign_commitment(
                workspace_root, cid, reassigned_by=MACHINE, source_skill=source_skill,
                new_owner_id=new_id, reason=f"reassigned on the board ({ref_string})",
                confirmed=True, actor=MACHINE, source_ref=ref_string)
            if res.get("status") == "not_open":
                changes.append({"change": "refused", "field": "owner", "why": "not_open"})
            else:
                changes.append({"change": "reassigned", "to": new_id})
    elif new_name != state.get("owner_refused"):
        # `reassign_commitment` routes to a person on file and nowhere else.
        # Refused once, and kept apart from the owner last seen: the row stays
        # quiet while the board says the same, and the move lands the day
        # that name reaches a person on file.
        now["owner_refused"] = new_name
        moved = True
        changes.append({"change": "refused", "field": "owner", "why": _NOT_ON_FILE,
                        "who": new_name})
    if moved:
        _note_board_seen(workspace_root, cid, row, ref_string, _s(item.get("updated_at")),
                         now, note=note, source_skill=source_skill,
                         owner_held=any(c.get("why") == OWNER_SET_BY_HAND for c in changes))
    return changes


def _note_board_seen(workspace_root, cid, row, ref_string, item_updated_at, state, *,
                     note: str = "", source_skill, left_boards: bool = False,
                     owner_held: bool = False) -> None:
    """ONE record-keeping `commitment_updated` after the board changed: the
    board's own values now (`board_state`), the board's clock when it has
    one, and the item's pointer, so the next fire compares the board with
    itself and never with the owner's later write. Carries no `new_*`
    field, so the projector folds nothing from it (the record-keeping class
    the wording and owner writers already leave). Through the one append
    path."""
    from event_gate import append_event
    from event_types import MACHINE

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    data: Dict[str, Any] = {
        "commitment_id": cid,
        "board_state": dict(state),
        "board_followed": True,
        "source_ref": ref_string,
        "reason": (note or f"the board item changed ({ref_string})")[:200],
        "updated_by": MACHINE,
    }
    if item_updated_at:
        data["item_updated_at"] = item_updated_at
    if left_boards:
        data["left_boards"] = True  # cr1#900: the one "left the boards" marker
    if owner_held:
        data["owner_held"] = True  # cr1#901: the board's owner noted, the owner's stands
    if isinstance(row.get("seq"), int):
        data["commitment_seq"] = row["seq"]
    append_event(events_path, [{
        "type": "commitment_updated", "source_skill": source_skill,
        "primary_thread_id": row.get("primary_thread_id") or "",
        "data": data}], holder=source_skill)


def _move_due(workspace_root, cid, live, due, ref_string, *, source_skill) -> None:
    """The board's new due date on the row: ONE `commitment_updated` carrying
    `new_due`, the shape the projector folds and `restore_due` reverses,
    with `prior_due` and `brain_change_class: commitment_due` exactly as
    `apply_later`'s deferral writes them. Not `apply_later` itself: on a row
    owed TO the owner that writer snoozes (hides the row until the date)
    instead of moving its date, and a board's due date is a date, in both
    directions. Through the one append path."""
    import commitment_state as cs
    from event_gate import append_event
    from event_types import MACHINE

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    data: Dict[str, Any] = {
        "commitment_id": cid,
        "new_due": due,
        "prior_due": cs.effective_due(events_path, cid),
        "pushed_by": MACHINE,
        "reason": f"due date moved on the board ({ref_string})"[:200],
        "brain_change_class": cs.DUE_CHANGE_CLASS,
        "source_ref": ref_string,
    }
    if isinstance(live.get("seq"), int):
        data["commitment_seq"] = live["seq"]
    append_event(events_path, [{
        "type": "commitment_updated", "source_skill": source_skill,
        "primary_thread_id": live.get("primary_thread_id") or "",
        "data": data}], holder=source_skill)


def _other_side(data: dict) -> set:
    """The person ids on the other side of a row from its owner."""
    from commitment_parties import counterparty_ids
    return {str(i) for i in counterparty_ids(data or {}) if i}


def _board_owner(item, *, user_person_id, owner_ids, owner_names, workspace_root):
    """`(person id, name)` of who the board says owns the item, whatever its
    due date says: the owner when the owner is among the assignees, else the
    first other assignee (a person on file when the capture gate can place
    the name, else the name, else the tool's id, as capture records it),
    else `("", "")`: an item with no assignee says nothing about its owner."""
    if _tasks.owner_is_assignee(item, owner_person_id=user_person_id, owner_ids=owner_ids,
                                owner_names=owner_names):
        return user_person_id, ""
    pids = [p for p in item.get("assignee_person_ids") or [] if p and p != user_person_id]
    if pids:
        return pids[0], ""
    names = [n for n in item.get("assignee_names") or [] if _s(n)]
    if not names:
        ids = [i for i in item.get("assignee_ids") or [] if _s(i)]
        return "", (_s(ids[0]) if ids else "")
    try:
        from capture_gate import _person_id_for_name
        pid = _person_id_for_name(workspace_root, names[0], user_person_id=user_person_id) or ""
    except Exception:  # noqa: BLE001
        pid = ""
    return pid, ("" if pid else _s(names[0]))


def _said_name(workspace_root, person_id: str, name: str, user_person_id: str) -> str:
    """The name the receipt says for who the board names: "you" for the
    owner, a person on file by their name, a name not on file as the board
    wrote it, never a raw id ("someone else" when there is nothing better)."""
    if person_id and person_id == user_person_id:
        return "you"
    if person_id:
        try:
            import json
            doc = json.loads((Path(workspace_root) / "_hq" / "data" / "entities.json")
                             .read_text(encoding="utf-8"))
            inner = doc["entities"] if isinstance(doc.get("entities"), dict) else doc
            for person in inner.get("people") or []:
                if isinstance(person, dict) and _s(person.get("id")) == person_id:
                    return _s(person.get("canonical_name") or person.get("name")) \
                        or "someone else"
        except Exception:  # noqa: BLE001 - a receipt word never costs the fire
            pass
        return "someone else"
    return name or "someone else"


def _same_owner(state: Dict[str, str], new_id: str, new_name: str, item: dict) -> bool:
    """Does the board still name the owner it named last time? No assignee
    is no news. A person recorded by name who is now on file is the same
    person, not a reassignment."""
    if not new_id and not new_name:
        return True
    if new_id:
        if new_id == state.get("owner_id"):
            return True
    elif new_name == state.get("owner_name"):
        return True
    seen = state.get("owner_name")
    return bool(seen) and seen in [_s(x) for x in list(item.get("assignee_names") or [])
                                   + list(item.get("assignee_ids") or [])]


__all__ = ["EMPTY", "follow_items", "board_seen", "board_state", "board_closed", "left_boards",
           "OWNER_SET_BY_HAND", "LEFT_BOARDS_NOTE"]
