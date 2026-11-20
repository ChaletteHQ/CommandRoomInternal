"""cr1#1366: may an undo lift a hide on an item without misstating it?

A Later on an item owed to the owner hides the item (`chat_dismissal`) and
moves nothing else. Its undo lifts that hide (`mute_ledger.clear_dismissal`
with `as_undo=True`, the cr1#1364 door, which already refuses under a newer
hide). This module adds the other half of the contract: the undo promises
the item reads as it did before the Later, so when the item's date or its
wait has changed since the hide, the undo would put it back to something it
never was, and it is refused before anything is written.

The test is the item's own state, read through the one projection every
surface reads (`cru_match.load_open_commitments`, `commitment_wait.item_waits`):
its date and its wait just before the hide (by its line in the book),
against now. A newer date or wait that was itself put back since reads as
before, so a newest-first walk back is never refused. Lifting a hide never writes a date or a wait, so a
newer change that left the same value is not erased by it.

A hide whose own batch also ends a wait on the item is left to that batch's
wait-end reverser, which judges the date and the wait by seq (cr1#1361,
cr1#1364); its hide leg then refuses with it (`brain_undo.undo_batch`).

Pure: the caller passes the events it read inside the events lock.
"""
from __future__ import annotations

from typing import Iterable, Optional


def _seq(ev) -> Optional[int]:
    sq = ev.get("seq") if isinstance(ev, dict) else None
    return sq if isinstance(sq, int) and not isinstance(sq, bool) else None


def _commitment_target(events: list, raw) -> Optional[str]:
    """The commitment id a hide's target names, in any spelling, or None
    when it names no commitment."""
    from mute_ledger import _item_keys
    from cru_match import _commitment_id

    keys = _item_keys(events, raw)
    for ev in events:
        if (ev.get("type") or ev.get("event")) == "commitment" and str(_commitment_id(ev)) in keys:
            return str(_commitment_id(ev))
    return None


def _due_of(events: list, cid: str) -> Optional[str]:
    from cru_match import _commitment_id as cid_of, load_open_commitments

    for row in load_open_commitments("", events=events):
        if str(cid_of(row)) == cid:
            v = (row.get("data") or {}).get("due")
            return str(v) if v else None
    return None


def _wait_of(events: list, cid: str, now_iso: str):
    from commitment_wait import item_waits

    w = item_waits(events, now_iso).get(cid)
    return (w["wait_on"], w["since"]) if w else None


def changed_since_hide(events: Iterable[dict], dismissal_seq, now_iso: str) -> bool:
    """True when the item the hide at `dismissal_seq` holds has a different
    date or wait now than just before the hide, so lifting it would not put
    the item back as it was. False for a hide on anything that is not a
    commitment, and for a hide whose own batch ends a wait on the item."""
    events = [ev for ev in events if isinstance(ev, dict)]
    at = next((i for i, ev in enumerate(events) if ev.get("type") == "chat_dismissal"
               and _seq(ev) == dismissal_seq), None)
    if at is None:
        return False
    own = events[at]
    d = own.get("data") or {}
    if d.get("target_id") in (None, ""):
        return False
    cid = _commitment_target(events, d["target_id"])
    if cid is None:
        return False
    bid = d.get("brain_batch_id")
    if bid:
        from commitment_wait import WAIT_END_CLASS, _target

        if any((ev.get("data") or {}).get("brain_batch_id") == bid
               and (ev.get("data") or {}).get("brain_change_class") == WAIT_END_CLASS
               and _target(ev.get("data") or {}, ev) == cid for ev in events):
            return False
    # Review round: "before" is the book up to the hide's own line, by
    # position, as the ledger's clears are judged (`_is_cleared`). A split
    # by seq dropped every row that carries none (older captures), so a
    # hide on such an item always read as changed, and filed a row whose
    # seq runs backwards on the wrong side of the hide.
    before = events[:at]
    if _due_of(before, cid) != _due_of(events, cid):
        return True
    return _wait_of(before, cid, now_iso) != _wait_of(events, cid, now_iso)
