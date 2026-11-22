#!/usr/bin/env python3
"""The Hub's People tab and the people pinned to Home (cr1#1020, slice 3).

One read and one small store:

  person records  `people(workspace_root)`: everyone on file but the owner,
                  each with who they are (name, role, email), the company
                  they are at, how they are known (the fields the record
                  carries: relationship, category, introduced by), when they
                  were last in touch (the record's own date or the newest
                  meeting, mail or interaction naming them) and what is open
                  between the owner and them (the open commitments naming
                  them, each with its direction through
                  `commitment_state.bucket_of`, the one counting rule).
  stars           `_hq/data/hub-stars.json`, one row per star, `{person_id,
                  starred_at, choice_id}`; an unstar stamps `unstarred_at`
                  and keeps the row (history stays additive). Owner ruling
                  2026-11-11 on cr1#1020: a starred person on Home shows what
                  is open with them, not just the name.

Reads only the book and entities.json; the only write is the star store.
The read and the actions are surface-free, so the option D Hub (cr1#1013)
reuses them and only redraws the screen. Since cr1#1012 part 3d the read is
the person cards and the open list's per-person index; this module only
lays them out for the tab.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock  # type: ignore

STARS_REL = Path("_hq") / "data" / "hub-stars.json"
PEOPLE_CAP = 300
OPEN_CAP = 12
TITLE_CAP = 200
#: The events that count as being in touch with someone.
CONTACT_TYPES = frozenset({"interaction", "meeting", "meeting_processed", "email_sent",
                           "email_received", "outreach_sent"})
#: The record fields that say how a person is known, in the order shown.
KNOWN_FIELDS = (("relationship", ""), ("relationship_type", ""), ("category", ""),
                ("how_known", ""), ("introduced_by", "introduced by "), ("context", ""))
DIRECTION_WORDS = {"you_owe": "you owe", "owed_to_you": "they owe you", "other": "involves them",
                   "unowned": "no owner yet", "unconfirmed": "waiting for your yes"}
#: On every person row, empty or not (cr1#1530). The page's search line joins
#: role, company and email as text, on every Hub page published so far, and a
#: published page is replaced only when the owner presses Finish update: a
#: row without one of them would be found by a search for "undefined".
ROW_ALWAYS = ("id", "name", "role", "company", "email")
#: Built for the count beside it and never sent (cr1#1530): the page reads
#: `n_unlinked`, not the list.
ROW_NEVER = ("unlinked",)

STARRED_LINE = "Starred. {name} is pinned to Home."
STARRED_ALREADY_LINE = "{name} is already starred; nothing was written."
UNSTARRED_LINE = "Unstarred. {name} leaves Home."
UNSTARRED_ALREADY_LINE = "{name} is not starred; nothing was written."
NO_PERSON_LINE = "That person is not on file; nothing was written."
STORE_UNREADABLE_LINE = "Your stars could not be read, so nothing was written. The next run tries again."
UNKNOWN_VERB_LINE = "This surface does not take that press ({verb}); nothing was written."
#: "Send them their list" (cr1#1017, train 8 part A): the reason a list is not
#: offered, in the owner's words. The list itself is `chase_policy.compose_list`.
LIST_NO_EMAIL_LINE = "No email on file for {name}, so there is no one to send the list to."
LIST_OWN_ADDRESS_LINE = "This address is one of your own, so the list is not offered."


def _now(now_iso: Optional[str] = None) -> _dt.datetime:
    if now_iso:
        try:
            dt = _dt.datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            pass
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(value: Any) -> str:
    """The calendar day of a timestamp or date, `YYYY-MM-DD`, or empty."""
    s = str(value or "").strip()
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else ""


# ---------------------------------------------------------------- the stars

def stars_path(workspace_root) -> Path:
    return Path(workspace_root) / STARS_REL


def _load_for_write(workspace_root) -> Dict[str, Any]:
    path = stars_path(workspace_root)
    if not path.exists():
        return {"version": 1, "people": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"stars unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ValueError("stars unreadable: not an object")
    data.setdefault("version", 1)
    if not isinstance(data.get("people"), list):
        data["people"] = []
    return data


def load_stars(workspace_root) -> Dict[str, Any]:
    """The star store, or an empty one; never raises (a read never stops on it)."""
    try:
        return _load_for_write(workspace_root)
    except ValueError:
        return {"version": 1, "people": []}


def starred_ids(store: Dict[str, Any]) -> List[str]:
    """The people starred now, oldest star first."""
    rows = [r for r in store.get("people") or [] if isinstance(r, dict)
            and r.get("person_id") and not r.get("unstarred_at")]
    rows.sort(key=lambda r: str(r.get("starred_at") or ""))
    seen, out = set(), []
    for r in rows:
        if r["person_id"] not in seen:
            seen.add(r["person_id"])
            out.append(r["person_id"])
    return out


def _write(workspace_root, mutate) -> Dict[str, Any]:
    path = stars_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_write_lock(path, holder="hub-stars")
    try:
        try:
            store = _load_for_write(workspace_root)
        except ValueError:
            return {"outcome": "error", "line": STORE_UNREADABLE_LINE}
        result, changed = mutate(store)
        if changed:
            atomic_write_json(path, store)
        return result
    finally:
        release_write_lock(lock)


def star(workspace_root, person_id: Any, *, choice_id: Optional[str] = None,
         now_iso: Optional[str] = None) -> Dict[str, Any]:
    pid = str(person_id or "").strip()
    name = _names(workspace_root).get(pid)
    if not name:
        return {"outcome": "refused", "line": NO_PERSON_LINE}
    now = _iso(_now(now_iso))

    def mutate(store):
        if pid in starred_ids(store):
            return {"outcome": "already", "line": STARRED_ALREADY_LINE.format(name=name)}, False
        store["people"].append({"person_id": pid, "starred_at": now, "choice_id": choice_id or ""})
        return {"outcome": "applied", "line": STARRED_LINE.format(name=name)}, True

    return _write(workspace_root, mutate)


def unstar(workspace_root, person_id: Any, *, choice_id: Optional[str] = None,
           now_iso: Optional[str] = None) -> Dict[str, Any]:
    pid = str(person_id or "").strip()
    name = _names(workspace_root).get(pid) or "That person"
    now = _iso(_now(now_iso))

    def mutate(store):
        live = [r for r in store["people"] if isinstance(r, dict) and r.get("person_id") == pid
                and not r.get("unstarred_at")]
        if not live:
            return {"outcome": "already", "line": UNSTARRED_ALREADY_LINE.format(name=name)}, False
        for r in live:
            r["unstarred_at"] = now
            r["unstarred_by"] = choice_id or ""
        return {"outcome": "applied", "line": UNSTARRED_LINE.format(name=name)}, True

    return _write(workspace_root, mutate)


VERBS = ("star", "unstar")


def apply_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None, *,
                  now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{ok, results: [{id, outcome, line}]}` for star and unstar presses,
    each `{id, action, person_id}`."""
    results = []
    for c in choices or []:
        if not isinstance(c, dict):
            continue
        verb = str(c.get("action") or "").strip().lower()
        cid = str(c.get("id") or "").strip() or None
        try:
            if verb == "star":
                res = star(workspace_root, c.get("person_id"), choice_id=cid, now_iso=now_iso)
            elif verb == "unstar":
                res = unstar(workspace_root, c.get("person_id"), choice_id=cid, now_iso=now_iso)
            else:
                res = {"outcome": "refused", "line": UNKNOWN_VERB_LINE.format(verb=verb or "none")}
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the rest
            res = {"outcome": "error", "line": STORE_UNREADABLE_LINE, "error": f"{type(exc).__name__}: {exc}"}
        res = dict(res)
        res["id"] = c.get("id")
        results.append(res)
    return {"ok": True, "results": results}


# ---------------------------------------------------------------- the records

def _entities(workspace_root):
    from entities_io import entities_collection, read_entities_json

    try:
        ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
    except (FileNotFoundError, ValueError, OSError):
        return [], []
    people = [p for p in entities_collection(ent, "people") if isinstance(p, dict) and p.get("id")]
    orgs = [o for o in entities_collection(ent, "orgs") if isinstance(o, dict) and o.get("id")]
    return people, orgs


def _name(p: Dict[str, Any]) -> str:
    return str(p.get("display_name") or p.get("canonical_name") or p.get("name") or "").strip()


def _names(workspace_root) -> Dict[str, str]:
    people, _orgs = _entities(workspace_root)
    return {p["id"]: _name(p) for p in people if _name(p)}


def _owner_id(workspace_root, people: List[Dict[str, Any]]) -> str:
    # The one seam (G34): resolve_primary_user already falls back to the
    # person flags, so the flag is never spelled by hand here.
    try:
        from primary_user import resolve_primary_user

        return str(resolve_primary_user(workspace_root) or "")
    except Exception:  # noqa: BLE001
        return ""


def _sign(workspace_root) -> Dict[str, str]:
    """`{name, sign_off}` for drafts in the owner's name: the name from the
    advisor's sheet ("What should I call you?"), else the owner's record; the
    sign-off style from the email-writer's own setting, read the way its
    drafts read it. Read only; a missing answer is an empty name."""
    import chase_policy as cp

    name = ""
    try:
        import advisor_sheet

        name = str(((advisor_sheet.load_sheet(workspace_root).get("answers") or {}).get("name") or {}).get("text") or "")
    except Exception:  # noqa: BLE001 - no sheet falls back to the record
        name = ""
    if not name.strip():
        owner = _owner_id(workspace_root, [])
        name = _names(workspace_root).get(owner, "") if owner else ""
    style = cp.SIGN_OFF_DEFAULT
    try:
        from skill_config_writer import get_config

        style = str(get_config(workspace_root, "email-writer", {"sign_off": cp.SIGN_OFF_DEFAULT}).get("sign_off")
                    or cp.SIGN_OFF_DEFAULT)
    except Exception:  # noqa: BLE001 - an unreadable setting keeps the default
        pass
    return {"name": " ".join(name.split())[:80], "sign_off": style}


def list_offers(person: Dict[str, Any], items: List[Dict[str, Any]], *, sign: Dict[str, str],
                own: set) -> Dict[str, Any]:
    """`{owed_to_you: offer, you_owe: offer}` for one person, each direction
    present only when a confirmed row runs that way (cr1#1017 part A). An
    offer is `{to, subject, body, n}` to draft, or `{note, n}` to say why not.
    Built from every open row in that direction, not the tab's capped list;
    rows waiting for the owner's yes never travel. Pure."""
    import chase_policy as cp

    out: Dict[str, Any] = {}
    to = str(person.get("email") or "").strip()
    for direction in cp.LIST_WORDS:
        rows = [i for i in items if i.get("direction") == direction]
        if not rows:
            continue
        if not to:
            out[direction] = {"note": LIST_NO_EMAIL_LINE.format(name=person.get("name") or "this person"),
                              "n": len(rows)}
            continue
        if to.lower() in own:
            out[direction] = {"note": LIST_OWN_ADDRESS_LINE, "n": len(rows)}
            continue
        draft = cp.compose_list(person.get("name") or "", rows, direction,
                                sign_name=sign.get("name") or "", sign_off=sign.get("sign_off") or cp.SIGN_OFF_DEFAULT)
        if draft:
            out[direction] = {"to": to, **draft}
    return out


def _how_known(p: Dict[str, Any], names: Dict[str, str]) -> str:
    bits = []
    for key, prefix in KNOWN_FIELDS:
        v = p.get(key)
        if isinstance(v, dict):
            v = v.get("name") or v.get("value") or v.get("id")
        if not v or not isinstance(v, (str, int)):
            continue
        v = str(v).strip()
        if key == "introduced_by":
            v = names.get(v, v)
        text = (prefix + v.replace("_", " ")).strip()
        if text and text not in bits:
            bits.append(text)
    return " · ".join(bits)[:200]


def _row_people(ev: Dict[str, Any]) -> set:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    ids = {str(x) for x in (ev.get("person_ids") or []) if x}
    for key in ("owner_id", "counterparty_id"):
        if d.get(key):
            ids.add(str(d[key]))
    return ids


def company_of(p: Dict[str, Any], org_names: Dict[str, str]) -> str:
    """The company a person works at: the record's `primary_org_id` (the
    writer's canonical field), then the deprecated `org_id`, then a typed
    `company`. The one rule the People tab and the person cards share
    (cr1#1012 part 3a)."""
    for key in ("primary_org_id", "org_id"):
        name = org_names.get(str(p.get(key) or ""))
        if name:
            return name[:120]
    return str(p.get("company") or "")[:120]


def lean_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """The row as it travels (cr1#1530): who the person is always
    (`ROW_ALWAYS`), every other field only when it holds something, and
    never the list of unlinked rows (`ROW_NEVER`). The page reads a missing
    field as empty, so it shows the same; on a large book the empty fields
    and that list were a third of the feed. Pure."""
    return {k: v for k, v in row.items() if k in ROW_ALWAYS or (v and k not in ROW_NEVER)}


def people(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. `{generated_at, people: [...], starred: [ids], n_people}`: every
    person on file but the owner, most recently in touch first, each with
    their open items. Never writes; a missing entities.json answers no one.

    cr1#1012 part 3d: the tab reads the person cards (`person_cards`) for who
    they are, where they work, how they are known and when they were last in
    touch, and the open list's per-person index for what is open, so the
    tab, the card and the open list count one way. Rows that name a person
    without being linked to them are counted apart (`n_unlinked`), never in
    `open`. The headline `n_open` counts confirmed rows only; rows waiting
    for the owner's yes are `n_waiting` and `n_listed` is both (the owner's
    ruling on cr1#1012); `open` lists both.

    cr1#1530: each row travels lean (`lean_row`): a field that is empty,
    zero or false is left out, and a reader takes a missing field as empty."""
    import person_cards as pc

    root = Path(str(workspace_root))
    errors: List[str] = []
    try:
        cards, cards_from_log = pc.read_cards(root)
    except Exception:  # noqa: BLE001 - no cards answers no one, and says so
        cards, cards_from_log = {}, True
        errors.append("cards")
    try:
        opens, open_from_log = pc.open_by_person(root, now_iso)
    except Exception:  # noqa: BLE001 - no open list leaves every person's empty, and says so
        opens, open_from_log = {}, True
        errors.append("open")
    stars = starred_ids(load_stars(root))
    # cr1#1013 part 2: each person's next step, by the thread cards' one rule
    # (`thread_cards.pick_next`, ruling A9), from the same open rows.
    try:
        import open_list as ol
        from thread_cards import pick_next
        today = ol._clock(root, now_iso, ol._workspace_tz(root))[1]
    except Exception:  # noqa: BLE001 - no clock leaves the next step off, never a guessed one
        pick_next, today = None, None
    # cr1#1017 part A: the owner's name and sign-off for "Send them their
    # list", read once, and the owner's own addresses, never offered a list.
    sign = _sign(root)
    try:
        from contact_capture import own_addresses
        own = {str(a).strip().lower() for a in own_addresses(root)}
    except Exception:  # noqa: BLE001 - no addresses on file leaves the check to the email itself
        own = set()
    out = []
    for pid, card in cards.items():
        who = card.get("who") or {}
        if not who.get("name"):
            continue
        mine = opens.get(pid) or {}
        items = sorted(mine.get("open") or [],
                       key=lambda i: (i["due"] is None, i["due"] or "", i["title"]))
        for i in items:
            i["title"] = i["title"][:TITLE_CAP]
            i["direction_words"] = DIRECTION_WORDS.get(i["direction"], "")
        unlinked = mine.get("unlinked") or []
        lists = list_offers({"name": who["name"], "email": who.get("email") or ""}, items, sign=sign, own=own)
        nxt = (pick_next([i for i in items if i["direction"] == "you_owe"],
                         [i for i in items if i["direction"] == "owed_to_you"], today)
               if pick_next else None)
        out.append({
            "id": pid, "name": who["name"], "role": who.get("role") or "",
            "company": who.get("company") or "",
            "email": who.get("email") or "",
            "how_known": who.get("how_known") or "",
            "last_contact": (card.get("last_contact") or {}).get("day") or None,
            # The headline counts confirmed rows only; rows waiting for the
            # owner's yes are counted beside it (the owner's ruling on
            # cr1#1012, the same split as the overdue ruling of 2026-11-11).
            # `open` still lists both, each row with its own words.
            "open": items[:OPEN_CAP], "n_listed": len(items),
            "n_open": sum(1 for i in items if i["direction"] != "unconfirmed"),
            "n_waiting": sum(1 for i in items if i["direction"] == "unconfirmed"),
            "n_you_owe": sum(1 for i in items if i["direction"] == "you_owe"),
            "n_owed_to_you": sum(1 for i in items if i["direction"] == "owed_to_you"),
            "unlinked": unlinked[:OPEN_CAP], "n_unlinked": len(unlinked),
            "starred": pid in stars,
            "lists": lists,
            "next_step": ({"title": nxt["title"], "due": nxt.get("due") or None,
                           "overdue": bool(nxt.get("overdue")), "direction": nxt["direction"]}
                          if nxt else None),
        })
    out.sort(key=lambda r: (not r["starred"], -(int(r["last_contact"].replace("-", "")) if r["last_contact"] else 0),
                            r["name"].lower()))
    return {"generated_at": _iso(_now(now_iso)), "people": [lean_row(r) for r in out[:PEOPLE_CAP]], "n_people": len(out),
            "starred": [s for s in stars if s in cards], "errors": errors,
            "counted_from_log": bool(cards_from_log or open_from_log)}


__all__ = ["STARS_REL", "VERBS", "load_stars", "starred_ids", "star", "unstar",
           "apply_choices", "people", "stars_path", "list_offers", "lean_row"]
