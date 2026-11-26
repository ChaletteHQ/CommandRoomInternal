#!/usr/bin/env python3
"""connector_reads: the one reader every Maintenance job calls before it
reads a source (cr1#667, rulings of 2026-11-03 on cr1#609).

A declared connector (`workspace.connectors.<category>`) carries a kind,
five dials, an include list, an exclude list and a scope; absent fields read
as the kind's defaults (`connector_config.connector_settings`). This module
answers, per kind and per PURPOSE, which declared connectors a job may read
and why it may not:

    for_job(workspace_root, "chat", purpose="close")
    -> [{category, kind, server_id, provider, label, dials, include,
         exclude, scope, reachable_hint, skip_reason, skip_kind}, ...]

`purpose` is one of `open`, `close`, `people`, `projects`. `skip_reason` is
set, in plain words, when the dial for that purpose is off (`skip_kind:
"dial"`), or when the connector is declared `desktop_only` and this is a
cloud fire, where it cannot be reached (`skip_kind: "reachability"`), or
when the owner said the source is personal (`skip_kind: "personal"`,
cr1#1163: never read for any purpose, never named as a gap). A leg
calls this first, records a skip honestly on its own receipt (the chat leg's
skip-receipt pattern), and reads nothing it was turned away from.

`allowed(record, name)` is the one place the lists are applied: an excluded
name is never read; with an include list, only the listed names are read. A
leg whose items always sit in a board or folder passes `must_match`, so one
with no name is not read under an include list either (cr1#759).

The run's receipt names the connectors the run did not READ, never one a
dial turned away (cr1#743). This process cannot see the fire's tools, so
nothing here can watch a read fail; what it can do is refuse to take a read
on trust. A leg asks for proof of each read (`read_outcome`: an item from
the connector, or the connector named in `connectors_read`) and names what
it did not get on its own receipt, with `unreachable_categories` over its
records for the reachability skips. The run's receipt then reads those, and
for a job that was on the fire and left no receipt it names what the job was
meant to read (`job_connectors`). `unreachable_declared` adds the
declarations a cloud fire cannot reach. Whether a fire is a cloud fire is
`maintenance_dispatcher.merged_seat`, the signal that already exists;
nothing new is read from the environment.

Read-only. Never raises on a broken workspace: an unreadable book answers
no connectors, which is the fail-closed side.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PURPOSES = ("open", "close", "people", "projects")

#: THE READER MAP (cr1#667 review, guard G84): every Maintenance job that
#: reads a connector source, and the kinds it reads. A job in this map must
#: call `for_job` / `for_category` in its leg before it reads the source;
#: a door-class job whose step text loads a kind's connector and is not in
#: this map is a guard failure. Lane 6 (cr1#668) registered its `tasks`,
#: `documents`, `deals` and `contracts` legs.
JOB_READS: Dict[str, tuple] = {
    "reconcile-sent": ("mail",),
    "reconcile-chat": ("chat",),
    "calendar-close": ("calendar",),
    "meeting-capture": ("meetings", "calendar"),  # cr1#1838: the capture pass reads the calendar (capture_calendar, capture_calendar_personal)
    "inbound-mail": ("mail",),  # cr1#664: opens and closes from inbound mail
    "tasks-capture": ("tasks",),
    "tasks-close": ("tasks",),
    "connector-proposals": ("documents", "deals", "contracts"),
}
#: What each job in the reader map reads its connectors FOR (cr1#743), the
#: same purposes its leg hands `for_job` / `for_category`. A connector whose
#: dial is on for any of them is one the job was meant to read; one turned
#: away on all of them is a dial skip, and a dial skip is never named.
JOB_PURPOSES: Dict[str, tuple] = {
    "reconcile-sent": ("close", "open", "people"),
    "reconcile-chat": ("close",),
    "calendar-close": ("close",),
    "meeting-capture": ("open", "close"),
    "inbound-mail": ("open", "close"),
    "tasks-capture": ("open", "people", "projects"),
    "tasks-close": ("close", "people", "projects"),
    "connector-proposals": ("close", "people", "projects"),
}
_PURPOSE_DIAL = {
    "open": "open_commitments",
    "close": "close_commitments",
    "people": "update_people",
    "projects": "update_projects",
}
SKIP_DIAL = "dial"
SKIP_REACHABILITY = "reachability"
#: The owner said the source is personal (cr1#1163): never read, for any
#: purpose, on any fire, and never named as a gap (like a dial turned off).
SKIP_PERSONAL = "personal"
SCOPE_COMPUTER = "computer"
SCOPE_CONTAINER = "container"


def _cc():
    try:
        import connector_config as cc
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import connector_config as cc
    return cc


def cloud_fire(env: Optional[dict] = None) -> bool:
    """True when this fire runs in the cloud container, where the connectors
    are the container's: the merged-seat signal the dispatcher already reads
    (`maintenance_dispatcher.merged_seat`). An unreadable seat is not a
    cloud fire."""
    try:
        from maintenance_dispatcher import merged_seat
    except Exception:  # noqa: BLE001
        return False
    try:
        return bool(merged_seat(env))
    except Exception:  # noqa: BLE001
        return False


def connector_scope(env: Optional[dict] = None) -> str:
    """`container` on a cloud fire, else `computer`: the word the run's
    receipt carries under `connector_scope`."""
    return SCOPE_CONTAINER if cloud_fire(env) else SCOPE_COMPUTER


def _with_skip(rec: Dict[str, Any], purpose: str, cloud: bool) -> Dict[str, Any]:
    cc = _cc()
    out = dict(rec)
    out["reachable_hint"] = rec.get("scope") or cc.SCOPE_ACCOUNT
    out["skip_reason"] = None
    out["skip_kind"] = None
    if rec.get("role") == cc.ROLE_PERSONAL:
        out["skip_kind"] = SKIP_PERSONAL
        out["skip_reason"] = (
            f"the {rec.get('label') or rec.get('category')} connection is "
            "personal, so it is never read")
        return out
    if rec.get("scope") == cc.SCOPE_DESKTOP_ONLY and cloud:
        out["skip_kind"] = SKIP_REACHABILITY
        out["skip_reason"] = (
            f"the {rec.get('label') or rec.get('category')} connection is "
            "attached only in the desktop app, so a scheduled fire cannot reach it")
        return out
    dial = _PURPOSE_DIAL[purpose]
    if not (rec.get("dials") or {}).get(dial):
        out["skip_kind"] = SKIP_DIAL
        out["skip_reason"] = (
            f"{dial} is off for the {rec.get('label') or rec.get('category')} "
            f"connection, so it is not read to {purpose}"
            + (" commitments" if purpose in ("open", "close") else ""))
    return out


def for_job(workspace_root, kind: str, *, purpose: str,
            env: Optional[dict] = None) -> List[Dict[str, Any]]:
    """Every declared connector of `kind`, each with its effective settings
    and, for this `purpose`, a `skip_reason` when the job may not read it.
    An unknown kind answers []; an unknown purpose is refused (a job that
    names a purpose nothing defines would read with no dial at all)."""
    purpose = str(purpose or "").strip().lower()
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}, got {purpose!r}")
    want = str(kind or "").strip().lower()
    cc = _cc()
    try:
        records = cc.declared_connector_settings(workspace_root)
    except Exception:  # noqa: BLE001 - a reader never raises
        return []
    cloud = cloud_fire(env)
    return [_with_skip(r, purpose, cloud) for r in records if r.get("kind") == want]


def for_category(workspace_root, category: str, *, purpose: str,
                 env: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """The one record for a declared category (the legs today read one
    connector per category), or None when it is undeclared."""
    purpose = str(purpose or "").strip().lower()
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}, got {purpose!r}")
    cc = _cc()
    try:
        rec = cc.connector_settings(category, workspace_root)
    except Exception:  # noqa: BLE001
        return None
    if rec is None:
        return None
    return _with_skip(rec, purpose, cloud_fire(env))


def _key(name) -> str:
    return str(name or "").strip().lstrip("#").strip().lower()


def allowed(record: Optional[Dict[str, Any]], *names, must_match: bool = False) -> bool:
    """May a job read an item (a channel, board, calendar, label or folder)
    on this connector? `names` are every spelling the item goes by: its id
    AND its display name, so a channel the owner named in words matches the
    connector's name for it, case-insensitively, a leading `#` ignored. An
    item with any spelling on the exclude list is never read; with an
    include list, only an item with a spelling on it is read; an item with
    no spelling at all (no names, or blank ones) has nothing to match on
    and is read. No record (an undeclared connector) means no lists: read.

    `must_match` is for a leg whose items always sit in a named place, a
    board or a folder (cr1#759): under a non-empty include list, an item with
    no spelling is not read either. It is not the default because a mail
    message can arrive with no label at all, the Sent fetch asks for none,
    and such a message stays read (cr1#757)."""
    keys = {k for k in (_key(n) for n in names) if k}
    if not record:
        return True
    include = {_key(x) for x in (record.get("include") or [])}
    if not keys:
        return not (must_match and include)
    exclude = {_key(x) for x in (record.get("exclude") or [])}
    if keys & exclude:
        return False
    if include and not (keys & include):
        return False
    return True


def unreachable_categories(records) -> List[str]:
    """The categories, sorted and de-duplicated, whose record carries a
    REACHABILITY skip. A dial skip is never reported as unreachable."""
    out = set()
    for r in records or []:
        if isinstance(r, dict) and r.get("skip_kind") == SKIP_REACHABILITY and r.get("category"):
            out.add(str(r["category"]))
    return sorted(out)


def unreachable_declared(workspace_root, env: Optional[dict] = None) -> List[str]:
    """The declared connectors this fire cannot reach by declaration: every
    `desktop_only` connector when the fire is a cloud fire; nothing on the
    computer that holds the desktop app. One whose every dial is off would
    not have been read from anywhere, so it is not a gap (cr1#743)."""
    if not cloud_fire(env):
        return []
    cc = _cc()
    try:
        records = cc.declared_connector_settings(workspace_root)
    except Exception:  # noqa: BLE001
        return []
    return sorted({str(r["category"]) for r in records
                   if r.get("scope") == cc.SCOPE_DESKTOP_ONLY and r.get("category")
                   and r.get("role") != cc.ROLE_PERSONAL
                   and _any_dial_on(r, PURPOSES)})


def _any_dial_on(record: Dict[str, Any], purposes) -> bool:
    dials = record.get("dials") or {}
    return any(dials.get(_PURPOSE_DIAL[p]) for p in purposes)


def job_connectors(workspace_root, job_id: str) -> List[str]:
    """The declared categories `job_id` was meant to read (cr1#743), sorted:
    of the kinds it reads, every connector with a dial on for one of its
    purposes. Read off the dials themselves, not `skip_kind`: on a cloud fire
    `_with_skip` answers reachability first, and that word must not turn a
    connector the owner switched off into a gap. A job outside the reader
    map, or an unreadable book, answers []."""
    kinds = JOB_READS.get(job_id) or ()
    purposes = JOB_PURPOSES.get(job_id) or ()
    if not kinds:
        return []
    cc = _cc()
    try:
        records = cc.declared_connector_settings(workspace_root)
    except Exception:  # noqa: BLE001 - a reader never raises
        return []
    return sorted({str(r["category"]) for r in records
                   if r.get("kind") in kinds and r.get("category")
                   and r.get("role") != cc.ROLE_PERSONAL
                   and _any_dial_on(r, purposes)})


def _categories(value) -> set:
    """A list of categories as a caller hands it: one bare word counts."""
    if isinstance(value, str):
        value = [value]
    return {str(c).strip() for c in value or () if str(c).strip()}


def read_outcome(workspace_root, job_id: str, records, *, seen=(),
                 connectors_read=None, fetch_blocked=None) -> tuple:
    """`(blocked, unread, unconfirmed)` for a leg about to record its run
    (cr1#743).

    A leg's input cannot tell a source that answered nothing from a read
    that never happened: both arrive as an empty list. So an empty answer is
    not taken on trust. A connector the job was meant to read counts as read
    only with proof: `seen`, the categories its items came from, or
    `connectors_read`, the categories the fire says answered. A fire that
    says nothing proves nothing, which is what keeps a local seat honest:
    its prompt has no step for these jobs at all.

    `unread` is every connector the job was meant to read and has no proof
    for, plus its reachability skips (`unreachable_categories`), sorted.
    `unconfirmed` is the part of `unread` nobody said anything about: with
    `connectors_read` handed in, a connector left out of it is the fire's own
    word that it did not answer; with none, the read may have worked, and the
    leg must say so rather than call it failed.
    `blocked` is the reason to record blocked, "" when the leg goes on:
    `fetch_blocked` as handed, or, when no connector it could reach has any
    proof, plain words naming them. Nothing proved means no item was
    attributed to a connector the job reads, so a blocked leg drops nothing
    it would have captured or closed. With some proof the leg runs over what
    it read and carries the rest on its receipt."""
    wanted = set(job_connectors(workspace_root, job_id))
    reach = set(unreachable_categories(records)) & wanted
    live = wanted - reach
    missing = live - (_categories(seen) | _categories(connectors_read))
    blocked = str(fetch_blocked or "").strip()
    if blocked:
        return blocked, sorted(wanted), []
    unconfirmed = sorted(missing) if connectors_read is None else []
    if live and missing == live:
        names = _joined(_labels(workspace_root, sorted(live)))
        return f"nothing confirmed a read of {names}", sorted(wanted), unconfirmed
    return "", sorted(missing | reach), unconfirmed


def _labels(workspace_root, categories) -> List[str]:
    """Each category's own label, in order, de-duplicated; the category
    itself when nothing declares one."""
    cc = _cc()
    try:
        declared = {r.get("category"): r.get("label")
                    for r in cc.declared_connector_settings(workspace_root)}
    except Exception:  # noqa: BLE001 - a reader never raises
        declared = {}
    out: List[str] = []
    for cat in categories:
        name = str(declared.get(cat) or cat)
        if name not in out:
            out.append(name)
    return out


def _joined(names: List[str]) -> str:
    return (", ".join(names[:-1]) + " and " + names[-1]) if len(names) > 1 else names[0]


def unread_line(workspace_root, not_read=(), unconfirmed=()) -> str:
    """The plain words for what a run did not read, "" with nothing to say
    (cr1#743): the receipt's `summary`, which `run maintenance` says.

    `not_read` is what a leg, or the fire through it, said did not answer.
    `unconfirmed` is what nobody vouched for either way: no receipt landed, or
    the fire said nothing. The read may have worked and the writer died, so
    those are never called failed. A connection in both is said once, as the
    firmer word."""
    firm = _categories(not_read)
    soft = _categories(unconfirmed) - firm
    out = []
    for cats, did, phrase in ((firm, "was not", "read"),
                              (soft, "was not", "confirmed as read")):
        names = _labels(workspace_root, sorted(cats))
        if len(names) == 1:
            out.append(f"The {names[0]} connection {did} {phrase} on this run.")
        elif names:
            out.append(f"The {_joined(names)} connections were not {phrase} on this run.")
    return " ".join(out)


# ---------------------------------------------------------------------------
# cr1#1179: the same wall for the CHATS. A chat reads its connectors in the
# conversation, where no Python step sees the tools, so the wall is asked
# once, before the first read (`shared/CHAT_WALL.md`), and obeyed: never a
# listed server, never a listed mailbox except its kept senders, nothing
# written from either. The writer side is structural
# (`account_scope_gate.enforce_scope` refuses a capture from a personal
# connector).
# ---------------------------------------------------------------------------

def chat_wall(workspace_root) -> Dict[str, Any]:
    """What a chat must never read, from the book as it stands.

    `{readable, connectors, mailboxes}`:
      * `connectors` - every declared connector whose role is personal:
        `{category, label, server_ids}` (the declared id and its recorded
        aliases). Never call a tool on these servers, for any purpose.
      * `mailboxes` - every classified account whose surface dial is off
        (a personal account's default, or one the owner dialled off):
        `{address, server_ids, except_senders}`. On a mail server that also
        carries a business mailbox, read only the business mailbox; a message
        from this one is never shown unless its sender is one the owner opted
        into surfacing (ACCOUNT_SCOPE's per-sender opt-in), and nothing from
        it is ever written.
      * `receipt` - on a readable book only: the receipt the chat hands its
        closing to show the wall was checked this turn (cr1#1357).
    An empty map reads everything (R4). An unreadable book answers an empty
    wall with `readable: False`: a chat cannot know what to hide, so it says
    nothing it read came from a checked wall. READ ONLY; never raises."""
    out: Dict[str, Any] = {"readable": False, "connectors": [], "mailboxes": []}
    try:
        cc = _cc()
        ent = cc.load_entities(workspace_root)
        if not isinstance(ent, dict) or not ent:
            return out
        out["readable"] = True
        for rec in cc.declared_connector_settings(workspace_root, ent):
            if rec.get("role") != cc.ROLE_PERSONAL:
                continue
            ids = []
            try:
                ids = list(cc.declared_backend_ids(rec["category"], workspace_root))
            except Exception:  # noqa: BLE001
                ids = []
            if rec.get("server_id") and rec["server_id"] not in ids:
                ids.insert(0, rec["server_id"])
            out["connectors"].append({"category": rec.get("category"),
                                      "label": rec.get("label") or rec.get("category"),
                                      "server_ids": ids})
        if cc.account_map_populated(entities=ent):
            for acc in cc.accounts(entities=ent):
                addr = cc._norm_addr(acc.get("address"))
                if not addr or cc._dials_for(acc).get("surface"):
                    continue
                kept = []
                ov = acc.get("overrides")
                senders = ov.get("senders") if isinstance(ov, dict) else None
                if isinstance(senders, dict):
                    for sender in senders:
                        o = cc.sender_scope_override(acc, sender)
                        if o and o.get("surface") is True:
                            kept.append(cc._norm_addr(sender))
                binds = acc.get("bindings") if isinstance(acc.get("bindings"), list) else []
                out["mailboxes"].append({
                    "address": addr,
                    "server_ids": [b["server_id"] for b in binds
                                   if isinstance(b, dict) and b.get("server_id")],
                    "except_senders": sorted(set(kept))})
    except Exception:  # noqa: BLE001 - a reader never raises
        return {"readable": False, "connectors": [], "mailboxes": []}
    # cr1#1357: the wall was really checked, so it hands back the receipt a
    # chat's closing counts it by. An unreadable book checked nothing: none.
    try:
        import turn_receipts  # type: ignore
        out["receipt"] = turn_receipts.issue("chat_wall")
    except Exception:  # noqa: BLE001 - no receipt, never a raise
        pass
    return out


def chat_tool_allowed(wall: Optional[Dict[str, Any]], tool_id: str) -> bool:
    """May a chat call this tool at all? False for a tool on a personal
    connector's server. A mail server is answered per mailbox
    (`chat_mail_allowed`), never here."""
    try:
        sid = _cc().server_id_of(tool_id)
    except Exception:  # noqa: BLE001
        sid = None
    if not sid or not isinstance(wall, dict):
        return True
    for c in wall.get("connectors") or []:
        if sid in (c.get("server_ids") or []):
            return False
    return True


def chat_mail_allowed(wall: Optional[Dict[str, Any]], *, account: Optional[str],
                      sender: Optional[str] = None) -> bool:
    """May a chat show this message? False when it came from a walled
    mailbox and its sender is not one the owner kept."""
    if not isinstance(wall, dict) or not account:
        return True
    from email.utils import parseaddr
    a = (parseaddr(str(account))[1] or str(account)).strip().lower()
    s = (parseaddr(str(sender or ""))[1] or str(sender or "")).strip().lower()
    for m in wall.get("mailboxes") or []:
        if m.get("address") == a:
            return bool(s) and s in (m.get("except_senders") or [])
    return True


__all__ = [
    "PURPOSES", "JOB_READS", "JOB_PURPOSES", "SKIP_DIAL", "SKIP_REACHABILITY",
    "SKIP_PERSONAL",
    "SCOPE_COMPUTER", "SCOPE_CONTAINER",
    "cloud_fire", "connector_scope", "for_job", "for_category", "allowed",
    "unreachable_categories", "unreachable_declared", "job_connectors",
    "read_outcome", "unread_line",
    "chat_wall", "chat_tool_allowed", "chat_mail_allowed",
]
