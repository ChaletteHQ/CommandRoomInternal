#!/usr/bin/env python3
"""One card per person (cr1#1012, train 2 part 3a).

WHAT A CARD HOLDS
-----------------
For every person on file but the owner (merged, deleted and archived people
left out, as on the Hub's People tab):

  who            name, role, company, how they are known (the record);
  last_contact   the newest day the owner was in touch, from the log's
                 contact rows (meetings, interactions, mail sent and
                 received, outreach) and the record's own stored date;
  rhythm         the usual gap between contacts (the median of the gaps,
                 two contacts or more) and the gap the owner taught, if any;
  facts          the facts on file by kind (`by_kind`), each with the
                 record it came from, a fact taken back left out;
  tie            `personal` when the person is a personal tie.

Rulings, owner, 2026-11-10/12:
  A4  a field shows only when a named job fills it. A section nothing fills
      is absent, never an empty heading, and each section names its `job`.
  C3  cards are for display; recall goes to the source. Each fact carries
      its `source_ref` and its row (`seq`), so a reader that needs the words
      goes to the record, never to the card.
Next step per person is not on the card: #110's closing note (ruling A9)
moves it to train 3. The "handle with care" line is the owner's own note,
filled by `people_writer.door_set_person_field` (part 3b), and marked
`owner_only` on the card.

WHERE IT LIVES
--------------
`_hq/data/now/people/cards.json`, written whole by `open_list.rebuild` (the
Maintenance run) beside the open list and its indexes, with the same build
stamps plus the aliases file's hash. Nothing that depends on the clock is
stored: the open rows both ways are read live, by `card_view`, from the
open list's per-person index. A reader that finds the file stale works the
cards out from the log in memory, says so (`counted_from_log`), and never
writes.

OWNER ONLY. The file holds personal-tie people and facts of every kind,
including personal ones, and says so (`audience: "owner"`). Only the
owner's own surfaces read it (the Hub's People tab is one); an org-facing
reader must not, or must filter it through `personal_leak` first.

Read-only except `write_cards`. stdlib only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_json  # noqa: E402

CARDS_DIR_REL = "_hq/data/now/people"
CARDS_FILE = "cards.json"
CARDS_VERSION = 3  # 2: `who` carries the address (cr1#1012 part 3d)
#                    3: a merged duplicate's facts and contacts (cr1#1136)

#: The jobs that fill each section (A4): named on the card.
JOB_RECORD = "the person's record"
JOB_CONTACTS = "the contact log and the record's own date"
JOB_RHYTHM = "the contact log, and the gap the owner taught"
JOB_FACTS = "facts noted from meetings, mail and chat"
JOB_OPEN = "the open list"
JOB_CARE = "the owner's own note, set through people-crm"


def _dir(ws) -> Path:
    return Path(ws) / CARDS_DIR_REL


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _contacts(root: Path) -> tuple:
    """({person id: [day, ...]}, {person id: {day: {merged record name}}})
    of every contact row, through the same door and the same contact types
    the Hub's People tab reads. A row under a record since merged away
    counts for the kept person (cr1#1136), and the second map names the
    merged record each such day came from."""
    from events_io import load_events_org_scoped
    from people_view import CONTACT_TYPES, _day, _row_people
    from render_person_history import _seq_of, follow_merge, merged_into
    out: Dict[str, List[str]] = {}
    via: Dict[str, Dict[str, set]] = {}
    if not (root / "_hq" / "data" / "events.jsonl").exists():
        return out, via
    scoped, _skipped = load_events_org_scoped(root, drop_personal=False)
    step = merged_into(scoped)
    for ev in scoped:
        if not isinstance(ev, dict) or ev.get("type") not in CONTACT_TYPES:
            continue
        day = _day(ev.get("ts"))
        if not day:
            continue
        seq = _seq_of(ev)
        whose: Dict[str, set] = {}
        for x in _row_people(ev):
            pid, was = follow_merge(step, x, seq)
            whose.setdefault(pid, set())
            if was:
                whose[pid].add(was)
        for pid, names in whose.items():
            out.setdefault(pid, []).append(day)
            if names:
                via.setdefault(pid, {}).setdefault(day, set()).update(names)
    return out, via


def _facts(root: Path) -> Dict[str, Dict[str, list]]:
    """{person id: {kind: [{fact, day, source_ref, seq, confidence}, ...]}},
    a fact taken back (`entity_fact_retracted`) left out, newest first. A
    fact noted under a record since merged away shows on the kept person
    with `merged_from` naming that record (cr1#1136)."""
    from events_io import load_events_owner_scoped
    from render_person_history import (CATEGORY_LABELS, FACT_TYPE,
                                       _retracted_seqs, _seq_of,
                                       follow_merge, merged_into)
    from people_view import _day
    events, _skipped = load_events_owner_scoped(str(root))
    taken = _retracted_seqs(events)
    step = merged_into(events)
    out: Dict[str, Dict[str, list]] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != FACT_TYPE:
            continue
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq in taken:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        pid = str(d.get("person_id") or "")
        text = str(d.get("fact") or "").strip()
        if not pid or not text:
            continue
        kind = d.get("category") if d.get("category") in CATEGORY_LABELS else "other"
        row = {"fact": text[:400], "day": _day(ev.get("ts")) or "",
               "source_ref": str(d.get("source_ref") or ""), "seq": seq,
               "confidence": d.get("confidence") or ""}
        to, was = follow_merge(step, pid, _seq_of(ev))
        if to != pid:
            row["merged_from"] = {"id": pid, "name": was}
            pid = to
        out.setdefault(pid, {}).setdefault(kind, []).append(row)
    for kinds in out.values():
        for rows in kinds.values():
            rows.sort(key=lambda r: (r["day"], str(r["seq"])), reverse=True)
    return out


def _email_of(p: dict) -> str:
    """The record's address: the singular field, else the first of the list."""
    one = str(p.get("email") or "").strip()
    if not one:
        many = p.get("emails") if isinstance(p.get("emails"), list) else []
        one = next((str(e).strip() for e in many if isinstance(e, str) and e.strip()), "")
    return one[:200]


def build_cards(ws, only: Optional[set] = None) -> Dict[str, dict]:
    """{person id: card}, or only the cards of the ids in `only` (a reader
    that needs one card works out one). Writes nothing."""
    from dormancy import cadence_override_days
    import math
    from people_view import (_day, _entities, _how_known, _name, _owner_id,
                             company_of)
    from render_person_history import _median_gap_days
    root = Path(ws)
    ppl, orgs = _entities(root)
    owner = _owner_id(root, ppl)
    names = {p["id"]: _name(p) for p in ppl if _name(p)}
    org_names = {o["id"]: _name(o) for o in orgs if _name(o)}
    contacts, contacts_via = _contacts(root)
    facts = _facts(root)
    cards: Dict[str, dict] = {}
    for p in ppl:
        pid = str(p.get("id") or "")
        if not pid or pid == owner or not names.get(pid):
            continue
        if only is not None and pid not in only:
            continue
        if str(p.get("status") or "").lower() in ("merged", "deleted", "archived"):
            continue
        card: Dict[str, Any] = {}
        who = {"name": names[pid],
               "role": str(p.get("role") or p.get("title") or "")[:120],
               "company": company_of(p, org_names),
               "email": _email_of(p),
               "how_known": _how_known(p, names)}
        # Part 3b: the facts the owner set through their doors, on the
        # record. A field shows only when it is there (A4).
        if p.get("category"):
            who["category"] = p["category"]
        intro = p.get("introduced_by")
        if isinstance(intro, dict) and intro.get("person_id"):
            who["introduced_by"] = {
                "name": names.get(str(intro["person_id"]), intro.get("name") or ""),
                "channel": intro.get("channel"), "confidence": intro.get("confidence"),
                "evidence": intro.get("evidence"), "date": intro.get("date")}
        phones = [x for x in (p.get("phones") or []) if isinstance(x, str) and x]
        if phones:
            who["phones"] = phones
        card["who"] = {**{k: v for k, v in who.items() if v}, "job": JOB_RECORD}
        care = str(p.get("handle_with_care") or "").strip()
        if care:
            # The owner's own line, for the owner's surfaces only.
            card["handle_with_care"] = {"text": care, "owner_only": True,
                                        "job": JOB_CARE}
        days = contacts.get(pid, [])
        stored = _day(p.get("last_interaction") or p.get("last_contact")) or ""
        last = max([stored] + days) if (stored or days) else ""
        if last:
            card["last_contact"] = {"day": last, "job": JOB_CONTACTS}
            came = sorted((contacts_via.get(pid) or {}).get(last) or ())
            if came:
                # cr1#1136: that day was logged under a record since merged in.
                card["last_contact"]["merged_from"] = came
        usual = _median_gap_days(days)
        taught = cadence_override_days(p)
        if taught is not None and not (math.isfinite(taught) and taught > 0):
            taught = None
        if usual is not None or taught:
            rhythm: Dict[str, Any] = {"job": JOB_RHYTHM}
            if usual is not None:
                rhythm["usual_gap_days"] = usual
            if taught:
                rhythm["taught_gap_days"] = int(taught) if float(taught).is_integer() else taught
            card["rhythm"] = rhythm
        if facts.get(pid):
            card["facts"] = {"by_kind": facts[pid], "job": JOB_FACTS}
        if str(p.get("tie") or "").lower() == "personal":
            card["tie"] = {"value": "personal", "job": JOB_RECORD}
        cards[pid] = card
    return cards


def _stamps(doc: dict, ws) -> dict:
    return {"version": CARDS_VERSION, "audience": "owner",
            "built_at": doc.get("built_at"),
            "log_lines": doc.get("log_lines"), "log_sha256": doc.get("log_sha256"),
            "entities_sha256": doc.get("entities_sha256")}


def write_cards(ws, doc: dict) -> dict:
    """Write every card for an open list document just built. Called only
    by `open_list.rebuild`."""
    cards = build_cards(ws)
    atomic_write_json(_dir(ws) / CARDS_FILE, {**_stamps(doc, ws), "cards": cards})
    return {"cards": len(cards)}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _current(ws, doc) -> bool:
    import open_list
    return (isinstance(doc, dict) and isinstance(doc.get("cards"), dict)
            and doc.get("version") == CARDS_VERSION
            and open_list.stamps_current(ws, doc))


def read_cards(ws, only: Optional[set] = None) -> tuple:
    """(cards, counted_from_log). A stale or missing file works out only the
    cards in `only` when it is given. Never writes."""
    try:
        doc = json.loads((_dir(ws) / CARDS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if _current(ws, doc):
        return doc["cards"], False
    return build_cards(ws, only), True


def card_view(ws, who: str, now_iso=None) -> dict:
    """One person's card, with the open rows both ways read live from the
    open list (`open_list_index.person_view`) and a pointer to the full
    list. `who` is a name, an alias, a first name or an id, resolved the way
    the open list resolves it. Writes nothing."""
    import open_list_index as oli
    ws = Path(ws)
    found = oli.resolve_person(ws, who)
    if found.get("status") == "found":
        ids = [found["id"]] + [c.get("id") for c in found.get("also") or []]
    elif found.get("status") == "ask":
        ids = [c.get("id") for c in found.get("candidates") or []]
    else:
        return found
    cards, from_log = read_cards(ws, set(ids))
    # A merged, archived or deleted record has no card: the name means the
    # person who has one. Two who both have one stay a question.
    holders = [i for i in ids if i in cards]
    if not holders:
        return {"status": "not_found", "query": who}
    if found.get("status") == "ask" and len(holders) > 1:
        return {**found, "candidates": [c for c in found["candidates"]
                                        if c.get("id") in holders]}
    pid = holders[0]
    live = oli.person_view(ws, pid, now_iso)
    out = {"status": "found", "person_id": pid, "card": cards[pid],
           "counted_from_log": from_log}
    top = {k: (live.get(k) or [])[:3] for k in ("you_owe", "owes_you")}
    if any(top.values()):
        out["open"] = {**top,
                       "n_you_owe": len(live.get("you_owe") or []),
                       "n_owes_you": len(live.get("owes_you") or []),
                       "full_list": {"command": "person", "person_id": found["id"]},
                       "job": JOB_OPEN}
    return out


#: The open list's per-person keys, as the People tab names the direction.
#: `other` is the index's catch-all: a row naming them that is neither theirs
#: to do for the owner nor the owner's to do for them. It may well have an
#: owner, so it is never called ownerless.
OPEN_DIRECTIONS = (("you_owe", "you_owe"), ("owes_you", "owed_to_you"),
                   ("other", "other"))


def open_by_person(ws, now_iso=None) -> tuple:
    """({person id: {"open": [...], "unlinked": [...]}}, counted_from_log):
    every person's open rows in one read of the open list's per-person index,
    split as `open_list_index.person_view` splits one person's: confirmed rows
    by direction, rows waiting for a yes as `unconfirmed`, observed-only and
    snoozed rows left out, and the rows that name a person without being
    linked to them apart (`unlinked`). Each row is `{id, title, due,
    direction, overdue}`. Writes nothing (cr1#1012 part 3d: the Hub's People
    tab reads this instead of counting the log its own way)."""
    import open_list as ol
    import open_list_index as oli
    ws = Path(ws)
    tzinfo = ol._workspace_tz(ws)
    now, today = ol._clock(ws, now_iso, tzinfo)
    entries, list_doc, from_log = oli._read(ws, "people", oli.BY_PERSON_FILE,
                                            now, tzinfo)
    by_id = {str(r.get("id")): r for r in list_doc.get("rows") or []}

    def row(r: dict, direction: str) -> dict:
        return {"id": r.get("id"), "title": str(r.get("title") or "")[:240],
                "due": r.get("due") or None, "direction": direction,
                "overdue": bool(r.get("overdue"))}

    out: Dict[str, dict] = {}
    for pid, e in (entries or {}).items():
        opens: List[dict] = []
        for key, direction in OPEN_DIRECTIONS:
            for r in oli._shape(by_id, e.get(key) or [], today, now):
                if oli._visible(r):
                    opens.append(row(r, direction if r.get("confirmed")
                                     else "unconfirmed"))
        unlinked = []
        for u in e.get("unlinked") or []:
            shaped = oli._shape(by_id, [u.get("id")], today, now)
            if shaped and oli._visible(shaped[0]):
                unlinked.append({**row(shaped[0], "unlinked"),
                                 "field": u.get("field")})
        if opens or unlinked:
            out[str(pid)] = {"open": opens, "unlinked": unlinked}
    return out, from_log


__all__ = ["CARDS_DIR_REL", "CARDS_FILE", "build_cards", "write_cards",
           "read_cards", "card_view", "open_by_person"]
