#!/usr/bin/env python3
"""What the Hub page may list by itself (cr1#1538).

The Hub's Inbox lists the seat's newest mail and chat messages while the page
is open, through the seat's own connectors. The personal wall and off-limits
are ruled logic the tasks enforce; the page cannot read the book, so the feed
carries the answers it needs, and this module builds them from the SAME
functions the tasks use, changing none of them:

  * which mailboxes to list: `inbox_helpers.fetch_plan_and_line`, the Hub
    Inbox task's own fetch plan, asked with no account list (the page does
    not call the connector's account tool), which is its strictest path;
  * which mailboxes are walled: every classified account marked personal,
    and every mailbox the chats' wall lists (`connector_reads.chat_wall`:
    an account whose surface dial is off). A walled mailbox is never on the
    plan; and because an unaddressed fetch could land on one, a plan with an
    unaddressed entry is replaced, while any mailbox is walled, by the
    classified mailboxes that are not walled, each by its address;
  * who is off limits: `account_scope_gate.off_limits_senders`, and, as the
    tasks also check the person a sender resolves to
    (`account_scope_gate.off_limits_person_ids`), every address and the full
    name of each of those people;
  * whether the chat connector may be read at all: `chat_wall`'s connectors.

Off-limits addresses and names leave here as SHA-256 hex of the value
trimmed, lower-cased and with runs of spaces made one (`digest`), never as
text: the page hashes what it compares, so the page's own data never holds
who is off limits. Walled mailboxes and the owner's own addresses and names
stay as text (the page has to name a mailbox on its call).

READ ONLY. Never raises: anything that cannot be read, or a list longer than
`LIST_CAP`, answers a closed wall (`ok: False`), and the page then lists
nothing of its own.

    python shared/scripts/hub_live.py <workspace>     # prints the wall as JSON
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: Said on the page when the wall could not be read (fail closed).
UNREADABLE_LINE = ("New mail is not listed here, because your account "
                   "settings could not be read.")
#: Said when every classified mailbox is walled, so none may be listed.
WALLED_ONLY_LINE = ("New mail is not listed here, because none of your "
                    "mailboxes is set to be shown.")
#: Said when a list is too long for the page to check in full (fail closed).
TOO_MANY_LINE = ("New mail is not listed here, because your account settings "
                 "hold more than this page can check.")
#: The most entries one list carries. A longer list closes the wall: a cut
#: list would let through whoever was cut.
LIST_CAP = 500


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def digest(value: Any) -> str:
    """SHA-256 hex of the value trimmed, lower-cased, runs of spaces one."""
    return hashlib.sha256(_norm(value).encode("utf-8")).hexdigest()


def _closed(line: str) -> Dict[str, Any]:
    return {"ok": False, "plan": [], "blocked": line, "walled": [], "off_limits": [],
            "off_limits_names": [], "known_names": [], "hashed": "sha256", "own": [], "own_names": [],
            "chat_ok": False}


def _full_names(person: Dict[str, Any]) -> set:
    """A person's names as a chat would show them: the name on file, and an
    alias only when it is a full name (a first name alone would catch
    another person who shares it)."""
    names = set()
    for key in ("canonical_name", "name", "display_name"):
        name = _norm(person.get(key))
        if name:
            names.add(name)
    for alias in person.get("aliases") or []:
        name = _norm(alias)
        if " " in name:
            names.add(name)
    return names


def _people(workspace_root) -> List[Dict[str, Any]]:
    import connector_config as cc

    ent = cc.load_entities(workspace_root)
    inner = ent.get("entities") if isinstance(ent.get("entities"), dict) else ent
    return [p for p in (inner.get("people") or []) if isinstance(p, dict)]


def _off_limits(workspace_root) -> tuple:
    """`(addresses, names)`: the gate's own senders, plus every address and
    the full names of each person on file one of them belongs to."""
    import account_scope_gate as gate
    from people_writer import get_person_emails

    off = set(gate.off_limits_senders(workspace_root))
    addrs = {_norm(a) for a in off if a}
    names: set = set()
    pids = gate.off_limits_person_ids(workspace_root, off) if off else set()
    if pids:
        for person in _people(workspace_root):
            if str(person.get("id") or "") not in pids:
                continue
            addrs |= {_norm(a) for a in get_person_emails(person) if a}
            names |= _full_names(person)
    return addrs, names


def _own_names(workspace_root) -> List[str]:
    """The owner's own full names, for a chat message that says who wrote it
    by name alone."""
    try:
        from primary_user import resolve_primary_user

        uid = str(resolve_primary_user(workspace_root) or "")
    except Exception:  # noqa: BLE001
        uid = ""
    names: set = set()
    for person in _people(workspace_root):
        # the one resolver's answer (`primary_user`), never a flag read here
        if uid and str(person.get("id") or "") == uid:
            names |= _full_names(person)
    return sorted(names)


def wall(workspace_root) -> Dict[str, Any]:
    """`{ok, plan, blocked, walled, off_limits, off_limits_names,
    known_names, hashed, own, own_names, chat_ok}`.

    `plan` is the mail fetch, one entry per mailbox to list (`acting_email`
    None for the connector's one unaddressed mailbox); empty with `blocked`
    set to the plain sentence when nothing may be listed. `walled` are the
    mailboxes never listed. `off_limits` and `off_limits_names` are hashes
    (`digest`; `hashed` names the method). `own` and `own_names` are the
    owner's own addresses and names (a message the owner wrote is nothing
    that arrived). `chat_ok` is False when the chat connector is marked
    personal or the wall could not be read. `known_names` (cr1#1571) are
    hashes of the full names of everyone on file who is not off limits: a
    chat group names its members by name alone, and while anyone is off
    limits a group is listed only when every member is someone known;
    left out when the list is too long to carry (then no group lists)."""
    if not workspace_root:
        return _closed(UNREADABLE_LINE)
    try:
        import connector_config as cc
        import connector_reads
        import inbox_helpers

        cwall = connector_reads.chat_wall(workspace_root)
        if not cwall.get("readable"):
            return _closed(UNREADABLE_LINE)
        plan, line = inbox_helpers.fetch_plan_and_line(str(workspace_root), None)
        accounts = cc.accounts(workspace_root)
        walled = {_norm(a.get("address")) for a in accounts if inbox_helpers._is_personal(a)}
        walled |= {_norm(m.get("address")) for m in cwall.get("mailboxes") or []}
        walled.discard("")
        # A plan entry is never a walled mailbox, whatever named it.
        plan = [p for p in plan if _norm(p.get("acting_email")) not in walled]
        # An unaddressed fetch reads whichever mailbox the connector puts
        # first, which could be a walled one: name the others instead.
        if walled and any(not p.get("acting_email") for p in plan):
            named: List[str] = []
            for a in accounts:
                addr = str(a.get("address") or "").strip()
                if addr and _norm(addr) not in walled and addr not in named:
                    named.append(addr)
            plan = [{"acting_email": addr} for addr in named]
            if not plan:
                line = WALLED_ONLY_LINE
        addrs, names = _off_limits(workspace_root)
        chat_ok = not any(str(c.get("category") or "") == "chat" for c in cwall.get("connectors") or [])
        own = sorted(_norm(a) for a in inbox_helpers.own_addresses(str(workspace_root)) if a)
        own_names = _own_names(workspace_root)
        known: set = set()
        for person in _people(workspace_root):
            full = _full_names(person)
            if not full & names:
                known |= full
        known -= names
        if max(len(walled), len(addrs), len(names), len(own), len(own_names), len(plan)) > LIST_CAP:
            return _closed(TOO_MANY_LINE)
        return {"ok": True,
                "plan": [{"acting_email": p.get("acting_email") or None} for p in plan],
                "blocked": None if plan else (line or inbox_helpers.PERSONAL_ONLY_LINE),
                "walled": sorted(walled),
                "off_limits": sorted(digest(a) for a in addrs),
                "off_limits_names": sorted(digest(n) for n in names),
                "known_names": sorted(digest(n) for n in known) if len(known) <= LIST_CAP else None,
                "hashed": "sha256",
                "own": own, "own_names": own_names, "chat_ok": chat_ok}
    except Exception:  # noqa: BLE001 - fail closed: the page lists nothing of its own
        return _closed(UNREADABLE_LINE)


if __name__ == "__main__":
    print(json.dumps(wall(sys.argv[1] if len(sys.argv) > 1 else None), indent=2))
