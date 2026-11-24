#!/usr/bin/env python3
"""import_wall: what an import may write about a person (cr1#1577).

Every path that writes about a person checks the owner's wall: the Hub's
piles, the live mail and chat lines, the append gate for mail from an
off-limits sender. The two import skills had no such step. `workspace-ingest`
(and the aliases that enter it, `ingest-context` and `file-documents`) wrote
its parsed people, spellings, events and records itself, and `intel-intake`
named entities from the book in what it saved. This module is the one step
both call before any of those writes: handed what the import is about to
write, it hands back only what may be written, and how many items it left
out. The append gate (`account_scope_gate.enforce_scope`) and the record
wall (`enforce_record_scope`) refuse an import row or spelling that still
links a walled person, so a skipped call is not a silent leak.

WHO IS WALLED. Two lists the owner sets, never a question and never a topic
(owner rulings of 2026-11-21 on cr1#1579 and cr1#1587), read here from ONE
parse of the book and ONE parse of the mail rules store, so a second read
that fails half way can never open the wall:
  * off limits: the senders the gate reads (`account_scope_gate.
    off_limits_senders`: a per-sender "write off" override on an account,
    or onboarding's off-limits answer in `mail_rules`) and the people on
    file holding one of those addresses (`off_limits_person_ids`);
  * the personal lane: the people on file marked `tie: "personal"`
    (`personal_leak.personal_tie_ids`). This is stricter than the org
    reader, which keeps a personal person's row when it is bound to a work
    project: an import has no reason to bring a personal person in at all.
The suite pins that these sets equal the gate's own readers on a good book.

HOW A WALLED PERSON IS KNOWN.
  * By id: a book id, as a whole value or one token of a comma-joined list,
    and as a dict key.
  * By address: every address on their record and every off-limits address,
    with a `+tag` on the local part ignored.
  * By identity, for a person the import brings and an entity hit: their
    name or any alias equal to any name or alias on a walled record, one
    word included (the AUGMENT dedupe's own rule: a person it would merge
    into a walled record is walled first). "Last, First" reads as "First
    Last". Accents are folded and case is ignored.
  * In free text: each name on a walled record with a space in it, as
    written, with an optional middle initial or capitalised middle name
    between the first and last word, as "Last, First", as "Last First"
    (the surname first with no comma, cr1#1632 review; only with both words
    capitalised as written, so "Ms. Sample drew the plan" or "the price cliff"
    is never a walled "Drew Sample" or "Cliff Price", cr1#1639 review round
    2, and both words on one line, never across a line break), and joined
    ("first_last", "FirstLast", "first-last", "first.last", and "LastFirst"
    with nothing between: each word capitalised as written and the surname's
    tail in small letters, so "parked" and "PARKED" are never a walled "Ed
    Park"; a joined name written all in capitals, "SAMPLERIO", is not caught,
    cr1#1639 review rounds 3 and 4, ruling 67). An apostrophe in a name may be
    left out or written as a space ("O'Brien", "OBrien", "O Brien"), and a
    hyphen in one may be a space ("Sample-Stone", "Sample Stone"). A one-word name
    ("Mom", a first name alone) never matches free text: it would catch
    everybody who shares it. That is the one known gap.

IDS. An id one of the import's own people carries names that person: an
import's ids come from its source, and the same id in the book belongs to
somebody else by chance. Any other id names the book. With `same_ids` (an
older workspace of this same book, workspace-ingest Parsers A and B) the
import's ids ARE the book's: a person carrying a walled id is walled.

WHAT IS LEFT OUT. `items` is `{collection: [item, ...]}`, or the same object
read from `items_rel`, a file the caller landed in the workspace (the read
door caps how much an argument may carry, and an import is bigger).
  * A walled person, and an entity hit for one, is left out whole; their
    import id, names and addresses join the wall for the rest of the import.
  * A record not about a person (`orgs`, `threads`, `projects`,
    `engagements`, and a person who is not walled) keeps its place: a link
    is cut (a list member or dict key removed, an id cleared or taken out of
    a joined list, a nested entry whose only link was cut removed), and a
    text field that names one is emptied. A record whose own name names one
    is left out whole, and so is one whose `title` is exactly their name; a
    title that only mentions them ("Assistant to ...") is emptied.
  * Anything else (an event, an alias row, a fact, a note, a field, a
    document, a collection this module does not know) that names one by id,
    address or name is left out whole.
  * The id of every item left out joins the wall, and the screen runs again
    until nothing more drops (an entry on a dropped project, a close of a
    dropped promise).
Each item left out and each cut counts once; `carried` adds an earlier
call's count, so the run says one line. An import that touches nobody on
either list comes back exactly as it went in.

FAIL CLOSED. A wall that cannot be read stops the import before any write,
as the Hub's piles do (`hub_work.fence`). Stops: a book that is there and
does not parse, whose `entities` is not an object, whose people or accounts
are not lists, or whose overrides are not objects; a mail rules store that
is there and does not parse or whose rules are not a list; a folder that is
not there; items that are not the shape above. A missing book is an empty
wall (a first import on a fresh folder); the rules store is still read.

READ ONLY, on the read door (`RUN_HELPER_ALLOWLIST`). It never raises: the
answer is `{ok, stop, items, left_out, line, walled_ids}`. `line` is the one
plain sentence the reply says, naming no one; `walled_ids` are the book ids
a Phase 4 merge must never land on, and are never said.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: Where a caller lands the items for a call through the read door.
ITEMS_REL = "_hq/.system/import/screen_items.json"
#: Collections whose items are records not about a person: a link to a
#: walled person is cut and the record kept.
RECORD_COLLECTIONS = frozenset({"orgs", "threads", "projects", "engagements"})
#: The collection of the import's people, and of intel-intake's entity hits.
PEOPLE_COLLECTION = "people"
HIT_COLLECTION = "entity_hits"
#: A record's own name fields: when one names a walled person, the record is
#: about them and is left out whole. `title` (a job title) only when it is
#: exactly their name.
NAME_KEYS = frozenset({"canonical_name", "name", "display_name", "folder_name"})
TITLE_KEY = "title"
#: A record's own id fields per collection, never read as a link.
OWN_ID_KEYS = {
    "people": ("id", "person_id"),
    "orgs": ("id", "org_id"),
    "threads": ("id", "thread_id", "project_id"),
    "projects": ("id", "thread_id", "project_id"),
    "engagements": ("id", "engagement_id"),
}
#: The fields an identity is read from: a person's names and spellings, an
#: entity hit's name.
_NAME_FIELDS = ("canonical_name", "name", "display_name", "entity_name")
_ALIAS_FIELDS = ("aliases", "nicknames")

#: Said when nothing could be checked; the import stops and writes nothing.
STOP_LINE = ("I could not check this import against the people you keep off "
             "limits or personal, so I stopped before writing anything.")
REASON_UNREADABLE = "wall_unreadable"
REASON_BAD_ITEMS = "bad_items"


def left_out_line(n: int) -> str:
    """The one line the reply says when the screen left something out."""
    if n <= 0:
        return ""
    noun = "item" if n == 1 else "items"
    return (f"Left out {n} {noun} about people you keep off limits or personal; "
            "nothing about them was brought in.")


# ---------------------------------------------------------------- forms

#: cr1#1677: letters from Cyrillic and Greek that look like a Latin letter,
#: read as that letter (case kept), so a name spelled with one ("R" + a
#: Cyrillic small i + "o Sample") is still the name. Only letters drawn the
#: same as the Latin one; written as code points so the source stays ASCII.
_CONFUSABLE = {
    # Cyrillic small: a, e, o, p, c, y, x, i (Ukrainian), j, s (Macedonian), d (Komi)
    0x0430: "a", 0x0435: "e", 0x043E: "o", 0x0440: "p", 0x0441: "c", 0x0443: "y",
    0x0445: "x", 0x0456: "i", 0x0458: "j", 0x0455: "s", 0x0501: "d",
    # Cyrillic capital: A, B, E, K, M, H, O, P, C, T, X, I, J, S
    0x0410: "A", 0x0412: "B", 0x0415: "E", 0x041A: "K", 0x041C: "M", 0x041D: "H",
    0x041E: "O", 0x0420: "P", 0x0421: "C", 0x0422: "T", 0x0425: "X", 0x0406: "I",
    0x0408: "J", 0x0405: "S",
    # Greek capital: A, B, E, Z, H, I, K, M, N, O, P, T, Y, X
    0x0391: "A", 0x0392: "B", 0x0395: "E", 0x0396: "Z", 0x0397: "H", 0x0399: "I",
    0x039A: "K", 0x039C: "M", 0x039D: "N", 0x039F: "O", 0x03A1: "P", 0x03A4: "T",
    0x03A5: "Y", 0x03A7: "X",
    # Greek small: o (omicron), i (iota), v (nu)
    0x03BF: "o", 0x03B9: "i", 0x03BD: "v",
}


def _plain(text: Any, extra: bool = True) -> str:
    """Accents folded, case kept, spaces as they were. cr1#1677: NFKD (the
    compatibility forms, fullwidth letters and the like, as before), then any
    dash punctuation (Unicode category Pd: en dash, em dash, the Unicode
    hyphen) read as "-", any invisible format character (category Cf: a
    zero-width space or joiner, a soft hyphen, a direction mark) left out,
    and a Cyrillic or Greek look-alike letter read as its Latin letter. Only
    the wall's comparisons read this form; a value is kept as written.

    `extra=False` is the fold as it was before cr1#1677 (NFKD, accents
    only). The wall reads every name, address and value under BOTH
    (`_READS`, review of #1681): the new steps alone would lose a name main
    caught, one written in Cyrillic or Greek in capitals (the look-alike
    map is not closed under case), or one beside a Thai, CJK or Arabic
    letter across a zero-width space (left out, the two words join and the
    edge check fails)."""
    if not extra:
        s = unicodedata.normalize("NFKD", str(text or ""))
        return "".join(ch for ch in s if not unicodedata.combining(ch))
    out = []
    for ch in unicodedata.normalize("NFKD", str(text or "")):
        if unicodedata.combining(ch):
            continue
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        if cat == "Pd":
            out.append("-")
            continue
        out.append(_CONFUSABLE.get(ord(ch), ch))
    return "".join(out)


#: The two readings every comparison takes (review of #1681): the fold with
#: cr1#1677's steps, and the fold as main had it.
_READS = (True, False)


def _key(text: Any, extra: bool = True) -> str:
    """A name as identity compares it: accents folded, any case, one space,
    and "Last, First" read as "First Last"."""
    s = " ".join(_plain(text, extra).casefold().split())
    if s.count(",") == 1:
        last, first = (p.strip() for p in s.split(","))
        if last and first:
            s = f"{first} {last}"
    return s


def _addr(text: Any, extra: bool = True) -> str:
    """The bare address, lower case, with a `+tag` on the local part left out."""
    import mail_rules

    bare = mail_rules.address_of(_plain(text, extra))
    if not bare:
        return ""
    local, _, domain = bare.partition("@")
    return f"{local.split('+', 1)[0]}@{domain}"


def _stop(reason: str) -> Dict[str, Any]:
    return {"ok": False, "stop": True, "reason": reason, "items": {}, "left_out": 0,
            "line": STOP_LINE, "walled_ids": []}


# ---------------------------------------------------------------- the wall

def _load_book(root: Path) -> Optional[Dict[str, Any]]:
    """The book, parsed once; `{}` when there is none; None when it is there
    and cannot be told apart from a broken one."""
    path = root / "_hq" / "data" / "entities.json"
    if not path.exists():
        return {}
    try:
        from entities_io import read_entities_json

        doc = read_entities_json(path)
    except Exception:  # noqa: BLE001 - unreadable
        return None
    if not isinstance(doc, dict):
        return None
    if "entities" in doc and not isinstance(doc.get("entities"), dict):
        return None
    inner = doc.get("entities") if isinstance(doc.get("entities"), dict) else doc
    if inner.get("people") is not None and not isinstance(inner.get("people"), list):
        return None
    for block in (doc.get("workspace"), inner.get("workspace")):
        if block is None:
            continue
        if not isinstance(block, dict):
            return None
        accounts = block.get("accounts")
        if accounts is None:
            continue
        if not isinstance(accounts, list):
            return None
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            ov = acc.get("overrides")
            if ov is not None and not isinstance(ov, dict):
                return None
            if isinstance(ov, dict) and ov.get("senders") is not None \
                    and not isinstance(ov.get("senders"), dict):
                return None
    return doc


def _load_rules(root: Path) -> Optional[Dict[str, Any]]:
    """The mail rules store, parsed once; empty when there is none; None
    when it is there and its rules cannot be read as a list (the shared
    reader answers "nobody" for both, which is no answer for an import)."""
    import mail_rules

    path = mail_rules.store_path(root)
    if not path.exists():
        return {"rules": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - unreadable
        return None
    if not isinstance(data, dict):
        return None
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        return None
    return dict(data, rules=rules)


def _addresses(person: Dict[str, Any]) -> set:
    found = list(person.get("emails") or []) if isinstance(person.get("emails"), list) else []
    found.append(person.get("email"))
    return {a for a in (_addr(x, e) for x in found if isinstance(x, str) for e in _READS) if a}


def _identity(item: Dict[str, Any]) -> set:
    names = [item.get(k) for k in _NAME_FIELDS]
    for key in _ALIAS_FIELDS:
        if isinstance(item.get(key), list):
            names += item[key]
    return {k for k in (_key(n, e) for n in names if isinstance(n, str) for e in _READS) if k}


def _write_dial() -> str:
    """The account's write dial, by its own name in the account vocabulary
    (`connector_config`'s role defaults: the dial beside `surface`)."""
    import connector_config as cc

    return next(k for k in cc._ROLE_DEFAULT_DIALS["personal"] if k != "surface")


def _off_senders(doc: Dict[str, Any], rules: Dict[str, Any]) -> set:
    """The off-limits addresses, by the gate's two rules
    (`account_scope_gate.off_limits_senders`), applied to the one parse: a
    per-sender override that turns the write dial off, and onboarding's
    off-limits rule."""
    import connector_config as cc
    import mail_rules

    off: set = set()
    dial = _write_dial()
    for rec in cc.accounts(entities=doc):
        ov = rec.get("overrides") if isinstance(rec.get("overrides"), dict) else {}
        senders = ov.get("senders") if isinstance(ov.get("senders"), dict) else {}
        for sender in senders:
            got = cc.sender_scope_override(rec, sender)
            if got and got.get(dial) is False and _addr(sender):
                off.update(a for a in (_addr(sender, e) for e in _READS) if a)
    for rule in mail_rules.active_rules(rules):
        if rule.get("off_limits") and rule.get("mode") == "hide" and rule.get("match") == "sender":
            if _addr(rule.get("value")):
                off.update(a for a in (_addr(rule.get("value"), e) for e in _READS) if a)
    return off


def _personal_ids(people: List[Dict[str, Any]]) -> set:
    """The people marked personal (`personal_leak.personal_tie_ids`'s rule)."""
    return {str(p["id"]) for p in people if p.get("tie") == "personal"}


def _wall_from(doc: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    """Who is walled, from the one parse of each store."""
    off = _off_senders(doc, rules)
    inner = doc.get("entities") if isinstance(doc.get("entities"), dict) else doc
    people = [p for p in (inner.get("people") or []) if isinstance(p, dict) and p.get("id")]
    off_ids = {str(p["id"]) for p in people if _addresses(p) & off}
    personal_ids = _personal_ids(people)
    ids = off_ids | personal_ids
    addrs, identity = set(off), set()
    for p in people:
        if str(p["id"]) in ids:
            addrs |= _addresses(p)
            identity |= _identity(p)
    return {"ids": ids, "off_ids": off_ids, "personal_ids": personal_ids, "off": off,
            "addrs": addrs, "identity": identity}


def wall(workspace_root) -> Optional[Dict[str, Any]]:
    """`{ids, off_ids, personal_ids, off, addrs, identity}` for everybody
    walled, or None when the wall cannot be read (fail closed)."""
    try:
        if not workspace_root:
            return None
        root = Path(str(workspace_root))
        if not root.is_dir():
            return None
        doc = _load_book(root)
        rules = _load_rules(root)
        if doc is None or rules is None:
            return None
        return _wall_from(doc, rules)
    except Exception:  # noqa: BLE001 - fail closed
        return None


# ---------------------------------------------------------------- matching

_TOKEN_SPLIT = re.compile(r"[\s,;|]+")
_TOKEN_STRIP = "()[]{}<>\"'.:"
#: Not a letter or a digit on either side (an underscore or a dash may
#: join a name to a word beside it, as a file name does).
_EDGE_L = r"(?<![^\W_])"
_EDGE_R = r"(?![^\W_])"
_MIDDLE = r"(?:(?-i:[A-Z])\.?\s+|(?-i:[A-Z][a-z]+)\s+)?"
#: A `+tag` on an address's local part, as text may carry it.
_TAG = r"(?:\+[^@\s]*)?"


def _word_rx(word: str, cap: bool = False, lower_tail: bool = False) -> str:
    """One word of a walled name as a pattern: an apostrophe may be left out
    or be a space ("o'brien", "obrien", "o brien"), a hyphen may be a space
    ("sample-stone", "sample stone"); with `cap`, its first letter must be a
    capital as written (cr1#1639 review round 2), the rest in any case; with
    `lower_tail` too, the rest in small letters as written, except a letter
    right after an apostrophe or a hyphen ("OBrien", "SampleStone"; review
    round 3)."""
    out = []
    after_mark = False
    for i, ch in enumerate(word):
        if ch in "'’":
            out.append(r"(?:['’]|\s+)?")
            after_mark = True
            continue
        if ch == "-":
            out.append(r"(?:-|\s+)")
            after_mark = True
            continue
        if cap and i == 0 and ch.isalpha():
            out.append("(?-i:" + re.escape(ch.upper()) + ")")
        elif lower_tail and ch.isalpha() and not after_mark:
            out.append("(?-i:" + re.escape(ch.lower()) + ")")
        else:
            out.append(re.escape(ch))
        after_mark = False
    return "".join(out)


def _name_pattern(name: str) -> str:
    words = [_word_rx(w) for w in name.split()]
    first, last = words[0], words[-1]
    parts = name.split()
    forms = [r"\s+".join(words),
             first + r"\s+" + _MIDDLE + last,
             last + r",\s*" + first + r"(?:\s+(?-i:[A-Z])\.?)?",
             # cr1#1632 review: the surname first with no comma ("Sample Rio");
             # cr1#1639 review round 2: only with both words capitalised as
             # written, so an everyday sentence ("Ms. Sample will send") is kept.
             # Final review: the two words on one line ("[ \t]+"), so a line
             # ending "...Sample" and the next starting "May 12" is kept.
             _word_rx(parts[-1], cap=True) + r"[ \t]+" + _word_rx(parts[0], cap=True),
             first + r"[_.\-]?" + last,
             # cr1#1639 review round 2: the surname first, joined ("SampleRio");
             # round 3: each word capitalised and the surname's tail in small
             # letters as written, so "parked" / "Parked" (walled "Ed Park")
             # and "royal" ("Al Roy") are kept, and "PARKED" in capitals too.
             # Lowercase "samplerio" is no longer caught (accepted), nor bare
             # all-caps "SAMPLERIO" (ruling 67, round 4: a joined all-caps word
             # cannot be told from FINAL, LEGAL or TOTAL, and dropping those
             # silently hides the owner's own material).
             _word_rx(parts[-1], cap=True, lower_tail=True) + _word_rx(parts[0], cap=True)]
    return "|".join(forms)


class _Matcher:
    """Whether a value names a walled person: an id (a whole value or one
    token of a joined list), an address, or a name with a space in it."""

    def __init__(self, ids: set, addrs: set, identity: set):
        self.ids = set(ids)
        self.full = {n for n in identity if " " in n}
        parts = []
        for a in sorted(addrs, key=len, reverse=True):
            local, _, domain = a.partition("@")
            parts.append(r"(?<![\w.+-])" + re.escape(local) + _TAG + "@"
                         + re.escape(domain) + r"(?![\w-])")
        for n in sorted(self.full, key=len, reverse=True):
            parts.append(_EDGE_L + "(?:" + _name_pattern(n) + ")" + _EDGE_R)
        self.rx = re.compile("|".join(parts), re.IGNORECASE) if parts else None

    def _tokens(self, value: str) -> List[str]:
        return [t.strip(_TOKEN_STRIP) for t in _TOKEN_SPLIT.split(value) if t.strip(_TOKEN_STRIP)]

    def is_id(self, value: Any) -> bool:
        return isinstance(value, str) and value.strip() in self.ids

    def has_id(self, value: Any) -> bool:
        return isinstance(value, str) and any(t in self.ids for t in self._tokens(value))

    def cut_ids(self, value: str) -> Optional[str]:
        """The joined list without the walled ids; None when nothing is left."""
        kept = [p.strip() for p in re.split(r"[,;]", value)
                if p.strip() and p.strip().strip(_TOKEN_STRIP) not in self.ids]
        return ", ".join(kept) if kept else None

    def names(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return self.rx is not None and any(self.rx.search(_plain(value, e)) for e in _READS)

    def exact(self, value: Any) -> bool:
        return isinstance(value, str) and any(_key(value, e) in self.full for e in _READS)

    def touches(self, value: Any) -> bool:
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                if self.has_id(item) or self.names(item):
                    return True
            elif isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
        return False


#: An id shape that may join the wall when its item drops: a letter first and
#: a joiner in it (`person_002`, `cmt_x`, `p-7`), so a bare number or a word
#: never does.
_ID_SHAPE = re.compile(r"[A-Za-z][\w.-]{2,}")


def _own_ids(item: Any, collection: str) -> set:
    if not isinstance(item, dict):
        return set()
    keys = OWN_ID_KEYS.get(collection, ("id",))
    found = [item.get(k) for k in keys]
    data = item.get("data")
    if collection not in OWN_ID_KEYS and isinstance(data, dict):
        found.append(data.get("id"))
    return {s.strip() for s in found if isinstance(s, str) and _ID_SHAPE.fullmatch(s.strip())
            and any(c in s for c in "_-")}


def _person_walled(person: Dict[str, Any], w: Dict[str, Any], m: _Matcher,
                   same_ids: bool) -> bool:
    """A person the import brings is walled by who they are: an address, a
    name or alias equal to a walled record's, or a name with a space that
    names one; and, with `same_ids`, by a walled book id."""
    if same_ids and _own_ids(person, PEOPLE_COLLECTION) & w["ids"]:
        return True
    if _addresses(person) & w["addrs"]:
        return True
    names = _identity(person)
    if names & w["identity"]:
        return True
    return any(" " in n and m.names(n) for n in names)


def _has_link(value: Dict[str, Any]) -> bool:
    """Whether an entry still links somebody or something: a non-empty id
    field, or a key that is itself an id (a map keyed by person)."""
    for key, val in value.items():
        if not isinstance(key, str):
            continue
        if key == "id" or key.endswith("_id") or key.endswith("_ids"):
            if val not in (None, "", [], {}):
                return True
        elif _ID_SHAPE.fullmatch(key) and any(c.isdigit() for c in key):
            return True
    return False


class _Cut:
    """One record's walk: links and mentions cut, or the record sent whole."""

    def __init__(self, m: _Matcher, own_keys: Tuple[str, ...]):
        self.m, self.own_keys, self.n = m, own_keys, 0

    def walk(self, value: Any, top: bool) -> Tuple[Any, bool, bool]:
        """`(value cut, send the record whole, a link was cut here)`."""
        m = self.m
        if isinstance(value, dict):
            out: Dict[Any, Any] = {}
            linked = False
            for key, val in value.items():
                if isinstance(key, str) and m.is_id(key):
                    self.n += 1
                    linked = True
                    continue
                if top and key in self.own_keys:
                    out[key] = val
                    continue
                if isinstance(val, str):
                    if m.has_id(val):
                        out[key] = None if m.is_id(val) else m.cut_ids(val)
                        self.n += 1
                        linked = True
                        continue
                    if m.names(val):
                        if top and (key in NAME_KEYS or (key == TITLE_KEY and m.exact(val))):
                            return value, True, False
                        out[key] = ""
                        self.n += 1
                        continue
                    out[key] = val
                    continue
                new, whole, cut = self.walk(val, False)
                if whole:
                    return value, True, False
                if cut and isinstance(val, dict) and not _has_link(new):
                    linked = True
                    continue
                out[key] = new
                linked = linked or cut
            return out, False, linked
        if isinstance(value, list):
            out_list: List[Any] = []
            linked = False
            for val in value:
                if isinstance(val, str):
                    if m.is_id(val) or m.names(val):
                        self.n += 1
                        linked = True
                        continue
                    if m.has_id(val):
                        rest = m.cut_ids(val)
                        self.n += 1
                        linked = True
                        if rest:
                            out_list.append(rest)
                        continue
                    out_list.append(val)
                    continue
                new, whole, cut = self.walk(val, False)
                if whole:
                    return value, True, False
                if cut and isinstance(val, dict) and not _has_link(new):
                    linked = True
                    continue
                out_list.append(new)
                linked = linked or cut
            return out_list, False, linked
        return value, False, False


def _screen(items: Dict[str, list], w: Dict[str, Any], ids: set, addrs: set,
            identity: set, same_ids: bool) -> Tuple[Dict[str, list], int, set]:
    """One pass: the kept collections, the count, and the ids of what
    dropped whole."""
    m = _Matcher(ids, addrs, identity)
    walled = {"ids": ids, "addrs": addrs, "identity": identity}
    out: Dict[str, list] = {}
    count, dropped = 0, set()
    for name, rows in items.items():
        kept = []
        own_keys = OWN_ID_KEYS.get(name, ("id",))
        for item in rows:
            is_dict = isinstance(item, dict)
            if is_dict and name in (PEOPLE_COLLECTION, HIT_COLLECTION) \
                    and _person_walled(item, walled, m, same_ids and name == PEOPLE_COLLECTION):
                count += 1
                dropped |= _own_ids(item, name)
                continue
            if is_dict and (name in RECORD_COLLECTIONS or name == PEOPLE_COLLECTION):
                if not m.touches({k: v for k, v in item.items() if k not in own_keys}):
                    kept.append(item)
                    continue
                cut = _Cut(m, own_keys)
                new, whole, _linked = cut.walk(item, True)
                if whole:
                    count += 1
                    dropped |= _own_ids(item, name)
                    continue
                count += cut.n
                kept.append(new)
                continue
            if m.touches(item):
                count += 1
                dropped |= _own_ids(item, name)
                continue
            kept.append(item)
        out[name] = kept
    return out, count, dropped


def _read_items(root: Path, items_rel: Any) -> Optional[Dict[str, Any]]:
    try:
        source = (root / str(items_rel)).resolve()
        source.relative_to(root.resolve())
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - what cannot be read is not checked
        return None
    if isinstance(data, dict) and isinstance(data.get("items"), dict):
        return data["items"]
    return data if isinstance(data, dict) else None


def screen_import(workspace_root, items: Optional[Dict[str, Any]] = None, *,
                  items_rel: Optional[str] = None, same_ids: bool = False,
                  carried: int = 0) -> Dict[str, Any]:
    """`{ok, stop, items, left_out, line, walled_ids}`: the import's
    collections with everything about a walled person left out (the module
    docstring), the count left out (plus `carried`), the one line to say,
    and the book ids a merge must never land on. `stop: true` (the wall
    could not be read, or the items could not be checked) means write
    nothing and say `line`. Read only; never raises."""
    try:
        root = Path(str(workspace_root or ""))
        if items_rel is not None:
            if items is not None:
                return _stop(REASON_BAD_ITEMS)
            items = _read_items(root, items_rel)
            if items is None:
                return _stop(REASON_BAD_ITEMS)
        if items is None:
            items = {}
        if not isinstance(items, dict) or not all(
                isinstance(k, str) and isinstance(v, list) for k, v in items.items()):
            return _stop(REASON_BAD_ITEMS)
        try:
            carried = max(0, int(carried or 0))
        except (TypeError, ValueError):
            return _stop(REASON_BAD_ITEMS)
        w = wall(workspace_root)
        if w is None:
            return _stop(REASON_UNREADABLE)
        same_ids = bool(same_ids)
        people = [p for p in items.get(PEOPLE_COLLECTION) or [] if isinstance(p, dict)]
        import_ids: set = set()
        if not same_ids:
            for person in people:
                import_ids |= _own_ids(person, PEOPLE_COLLECTION)
        ids = set(w["ids"]) - import_ids
        addrs, identity = set(w["addrs"]), set(w["identity"])
        # A walled person's other names and addresses in the import are
        # theirs too, so a second entry for them is caught by either.
        found: set = set()
        while True:
            m = _Matcher(ids, addrs, identity)
            walled = {"ids": set(w["ids"]), "addrs": addrs, "identity": identity}
            more = [i for i, p in enumerate(people)
                    if i not in found and _person_walled(p, walled, m, same_ids)]
            if not more:
                break
            for i in more:
                found.add(i)
                ids |= _own_ids(people[i], PEOPLE_COLLECTION)
                addrs |= _addresses(people[i])
                identity |= _identity(people[i])
        # Whatever drops whole takes its id with it; run again until stable.
        while True:
            out, count, dropped = _screen(items, w, ids, addrs, identity, same_ids)
            if dropped <= ids:
                break
            ids |= dropped
        total = count + carried
        return {"ok": True, "stop": False, "items": out, "left_out": total,
                "line": left_out_line(total), "walled_ids": sorted(w["ids"])}
    except Exception:  # noqa: BLE001 - fail closed: nothing is written
        return _stop(REASON_UNREADABLE)


def text_checker(workspace_root):
    """A function that answers whether one text names a walled person (an
    id, an address or a name with a space, as `screen_import` reads free
    text), with the wall read ONCE (cr1#1586 fix round 4: the bring-in door
    screens each item as it reads it). None when the wall cannot be read:
    fail closed. Read only; never raises."""
    try:
        w = wall(workspace_root)
        if w is None:
            return None
        m = _Matcher(set(w["ids"]), set(w["addrs"]), set(w["identity"]))

        def check(text) -> bool:
            return True if not isinstance(text, str) else bool(m.touches(text))
        return check
    except Exception:  # noqa: BLE001 - fail closed
        return None


def walled_texts(workspace_root, texts: Any) -> Optional[List[bool]]:
    """For each text, whether it names a walled person (an id, an address or
    a name with a space, as `screen_import` reads free text), judged in ONE
    pass against the wall read once (cr1#1586 fix round 3: a bring-in item's
    own text, in chunks). None when the wall cannot be read or the texts are
    not a list of strings: fail closed, nothing is written. Read only; never
    raises."""
    try:
        if not isinstance(texts, list) or not all(isinstance(x, str) for x in texts):
            return None
        w = wall(workspace_root)
        if w is None:
            return None
        m = _Matcher(set(w["ids"]), set(w["addrs"]), set(w["identity"]))
        return [bool(m.touches(x)) for x in texts]
    except Exception:  # noqa: BLE001 - fail closed
        return None


__all__ = ["screen_import", "wall", "walled_texts", "text_checker", "left_out_line", "STOP_LINE", "ITEMS_REL",
           "RECORD_COLLECTIONS", "REASON_UNREADABLE", "REASON_BAD_ITEMS"]
