#!/usr/bin/env python3
"""followup_draft.py: a meeting's follow-up, drafted after the meeting and
waiting on the Hub for the owner's one press (cr1#1745).

WHY THIS EXISTS
After a meeting the owner pressed "Write it up now" and "Write the
follow-up", each opening a chat. Maintenance's meeting-capture step already
writes each ended meeting up silently. It now also drafts the follow-up, in
the owner's voice through email-writer's rules (the follow-up-ritual path),
and hands the text here. This module only keeps it: one `followup_draft`
row in the book per meeting occurrence. No mail connector is called and
nothing reaches the mailbox. The Hub's wrap-up page shows the draft, and the
owner's press makes the mailbox draft or sends it (the page's own direct
mail path, recorded at the next Apply).

THE FENCE (fail closed)
No draft is kept for a meeting on the personal calendar, a block of the
owner's own time, a meeting a "don't draft for meetings like this" rule
matches, or one whose people cannot be checked. Which calendar the
meeting is on is read from the book too (review round 2): the meeting's own
`meeting` row must say "business" (`data.calendar`, stamped where the
meeting is captured). A row that says personal, or says nothing, gets no
draft, whatever the caller passes. Who was on the meeting is
read from the book, never taken from the caller: the ref must name a
processed meeting (its `meeting_processed` receipt) whose own `meeting` row
says who attended. Anyone on that row who is off limits or has a personal
tie means no draft, whoever the caller addresses it to; so does a
recipient who was not on the meeting, and a draft with nobody else left on
it. The caller's `calendar` and `solo` can only refuse, never allow. The
words are screened too: a subject, body or title that names someone hidden
(any way their record is written, or their address) is refused. The read
side (`feed_followups`) checks all of it again as the wall stands then, and
leaves out a draft a skip rule now matches.

THE SKIP RULES
`_hq/data/followup-skip.json`: `{"rules": [{key, title, added_at}]}`. A
key is the meeting's id when it recurs (the occurrences share it), else its
title with digits and dates taken out, folded. Written by the Hub's
"Don't draft for meetings like this" press through `apply_skip`, with an
Undo. A rule also takes a draft already waiting off the Hub (the row stays
in the book, so the Undo brings it back).

stdlib only. Never sends.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

EVENT = "followup_draft"
SOURCE_SKILL = "follow-up-ritual"
SKIP_FILE = ("_hq", "data", "followup-skip.json")
SUBJECT_CAP = 200
BODY_CAP = 8000
#: the feed doc holds at most this many drafts (the newest), so it stays small
FEED_CAP = 10
SKIP_CAP = 200

PERSONAL_LINE = "That meeting is on your personal calendar, so no follow-up is drafted for it."
SOLO_LINE = "That is a block of your own time, so no follow-up is drafted for it."
SKIPPED_LINE = "You said not to draft for meetings like this one."
HELD_LINE = "Who is on that meeting could not be checked, so no follow-up is drafted for it."
HIDDEN_LINE = "Someone on that follow-up is off limits or on your personal side, so it was not drafted."
NOBODY_LINE = "Nobody else on that meeting can be written to, so no follow-up is drafted for it."
EMPTY_LINE = "That follow-up has no subject or no words, so nothing was kept."
ALREADY_LINE = "A follow-up is already drafted for that meeting."
KEPT_LINE = "Follow-up drafted. It waits on the Hub; nothing is sent until you press."
SKIP_ADDED_LINE = "No follow-up will be drafted for meetings like this one."
SKIP_REMOVED_LINE = "Follow-ups will be drafted for meetings like this one again."
SKIP_BAD_LINE = "That press names no meeting, so no rule was changed."
NO_CALENDAR_LINE = "Your book does not say which calendar that meeting is on, so no follow-up is drafted for it."
NOT_PROCESSED_LINE = "That meeting has not been written up, so no follow-up is drafted for it."
NOT_ON_LINE = "That follow-up is addressed to someone who was not on the meeting, so it was not drafted."
NAMED_LINE = "That follow-up names someone who is off limits or on your personal side, so it was not drafted."


def _root(workspace_root) -> Path:
    return Path(str(workspace_root))


def _events(root: Path) -> List[dict]:
    from events_io import load_events_owner_scoped

    return [e for e in load_events_owner_scoped(str(root))[0] if isinstance(e, dict)]


def _ref_key(ref: Any) -> str:
    import hub_meeting

    return hub_meeting._ref_key(ref)


def skip_key(meeting_id: Any = "", title: Any = "", recurring: Any = False) -> str:
    """The rule key of a meeting: its id when it recurs, else its title with
    digits and dates taken out, folded. "" when there is nothing to key on."""
    mid = str(meeting_id or "").strip()
    if recurring is True and mid:
        return "id:" + mid[:200]
    t = re.sub(r"[\d/.:-]+", " ", str(title or "")).casefold()
    t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()
    return ("title:" + t[:200]) if t else ""


def skip_rules(workspace_root) -> Optional[List[Dict[str, Any]]]:
    """The rules, or None when the file is there and cannot be read (no
    draft is kept then: the owner's word cannot be checked)."""
    p = _root(workspace_root).joinpath(*SKIP_FILE)
    if not p.exists():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    rules = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(rules, list):
        return None
    return [r for r in rules if isinstance(r, dict) and str(r.get("key") or "")]


def _write_rules(workspace_root, rules: List[Dict[str, Any]]) -> None:
    p = _root(workspace_root).joinpath(*SKIP_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_bytes(json.dumps({"rules": rules[-SKIP_CAP:]}, indent=2).encode("utf-8"))
    tmp.replace(p)


def apply_skip(workspace_root, *, action: str, meeting_id: Any = "", title: Any = "", recurring: Any = False,
               now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. `action` "skip" adds the meeting's rule, "unskip" takes it
    away (the Undo). Answers `{outcome, line, key}`."""
    key = skip_key(meeting_id, title, recurring)
    if not key or action not in ("skip", "unskip"):
        return {"outcome": "refused", "line": SKIP_BAD_LINE, "key": key}
    rules = skip_rules(workspace_root)
    if rules is None:
        return {"outcome": "refused", "line": HELD_LINE, "key": key}
    have = any(str(r.get("key")) == key for r in rules)
    if action == "skip":
        if have:
            return {"outcome": "already", "line": SKIP_ADDED_LINE, "key": key}
        at = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat()
        rules.append({"key": key, "title": str(title or "")[:SUBJECT_CAP], "added_at": at})
        _write_rules(workspace_root, rules)
        return {"outcome": "applied", "line": SKIP_ADDED_LINE, "key": key}
    if not have:
        return {"outcome": "already", "line": SKIP_REMOVED_LINE, "key": key}
    _write_rules(workspace_root, [r for r in rules if str(r.get("key")) != key])
    return {"outcome": "applied", "line": SKIP_REMOVED_LINE, "key": key}


def _hidden(root: Path) -> Optional[set]:
    """Everyone the owner may not write to from here: off limits, or with a
    personal tie. None when either cannot be read."""
    try:
        import hub_meeting

        fence = hub_meeting.personal_fence(root)
    except Exception:  # noqa: BLE001
        return None
    if fence is None:
        return None
    return set(fence["off"]) | set(fence["tie"])


def _owner(root: Path) -> str:
    try:
        import cockpit_helpers as ch

        return str(ch._owner_id(root) or "")
    except Exception:  # noqa: BLE001
        return ""


def _plain(text: Any) -> str:
    """The text as the screen reads it: accents folded off their letters,
    and characters that print nothing (zero-width and other format marks)
    taken out, so neither can split or disguise a name."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in t if not unicodedata.combining(c) and unicodedata.category(c) != "Cf")


def _flat(text: Any) -> str:
    """Letters and digits only, folded, one space between words."""
    return " ".join("".join(c if c.isalnum() else " " for c in _plain(text).casefold()).split())


#: cr1#1846 (rule L, coordinator decision on the issue): a closing word of a
#: name that is not its last name.
SURNAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v", "esq"})
#: Words that join a last name to the word after them ("da quellan", "van der
#: berg"): the phrase is held, and so is the last word alone.
SURNAME_PARTICLES = frozenset({"van", "von", "de", "da", "del", "della", "di", "du", "la", "le",
                               "bin", "al", "der", "den", "ter"})
#: cr1#1846 part 4: last names that are also ordinary words. One of these is
#: held only where it is written capitalised and not first in its sentence,
#: and it never travels in the page's hashed list (a hash carries no case).
#: A fixed list, changed only by ruling.
ORDINARY_SURNAMES = frozenset({
    "price", "brown", "long", "hall", "stone", "rose", "may", "bell", "king", "field",
    "page", "day", "cook", "ward", "banks", "lane", "love", "hope", "rich", "bond", "moore",
    "green", "white", "black", "small", "wood", "hill", "little", "short", "best", "noble",
    "fair", "frank", "grant", "mark", "chase", "rice", "fox", "bird", "west", "north", "south",
    "reed", "low", "hunt", "sharp", "strong", "bright", "close", "waters", "rivers", "mills",
})
#: review of 261a82f28 (MUST 3): a full stop closing one of these titles
#: does not start a sentence ("Mr. Brown").
NAME_TITLES = frozenset({"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "rev", "fr", "hon", "capt",
                         "col", "gen", "lt", "sgt", "st", "sen", "gov", "rep", "pres", "atty"})
#: The line for a draft held on a last name a visible person shares too.
SURNAME_LINE = ("That follow-up uses a last name that someone off limits or on your personal side "
                "shares, so it was not drafted.")


def surname_forms(name: Any) -> List[str]:
    """cr1#1846 (rule L): the forms of `name`'s last name that are held
    alone: the last word, after a closing suffix, and, when a particle comes
    before it, the particle phrase too. Only a name of two or more words has
    one; a word shorter than two letters is none. Folded as `_flat`. Pure."""
    raw = [w for w in _plain(_cut(name)).split() if _flat(w)]
    while raw and _flat(raw[-1]) in SURNAME_SUFFIXES:
        raw = raw[:-1]
    if len(raw) < 2:
        return []
    tail = _flat(raw[-1]).split()        # a two-part surname ("Vance-Okafor") is one word of the name
    parts = [w for t in raw[:-1] for w in _flat(t).split()]
    if not tail or len(tail[-1]) < 2:
        return []
    out = [tail[-1]]
    if len(tail) > 1:
        out.append(" ".join(tail))       # review of 261a82f28: the pair too
    i = len(parts)
    while i - 1 >= 1 and parts[i - 1] in SURNAME_PARTICLES:
        i -= 1
    if i < len(parts):
        out.append(" ".join(parts[i:] + tail))
    return out


def _cut(name: Any) -> str:
    """A name up to an opening bracket: "(Example Co)" after a name says
    where they work and is not part of the name (review of a386dda2c)."""
    return str(name or "").split("(", 1)[0].strip()


def _surname_names(rec: Dict[str, Any]) -> List[str]:
    """cr1#1846 (coordinator decision, review of a386dda2c): the only fields a
    last name comes from: the record's own name and its second name field,
    never an alias. An alias still holds as a whole name and by its first
    word, exactly as on main. Known limit: a real second surname kept only as
    an alias is not held alone."""
    return [_cut(v) for v in [rec.get("canonical_name"), rec.get("name")] if _cut(v)]


def _record_names(rec: Dict[str, Any]) -> List[str]:
    return [str(v) for v in [rec.get("canonical_name"), rec.get("name")] + list(rec.get("aliases") or [])
            if str(v or "").strip()]


def hidden_words(workspace_root) -> Optional[Dict[str, Any]]:
    """Every way a hidden person is written (`hub_work.hidden_people`: full
    name, first name, aliases), their last names (cr1#1846, rule L) and
    their addresses, for screening a draft's words. None when who is hidden
    cannot be read (nothing is kept or listed then).

    Rule L: each hidden person's own name fields of two or more words give a
    last name (`surname_forms` over `_surname_names`; never an alias). The owner's own surnames never do, and the owner's
    whole names are set aside first (`own`), as a visible person's are
    (`shown`). An ordinary-word last name (`ORDINARY_SURNAMES`) goes to
    `cased`, held only capitalised and not first in its sentence. A held last
    name that a visible person's name also ends in is in `shared`, so the
    held line can say so without naming anyone."""
    root = _root(workspace_root)
    try:
        import hub_work

        hp = hub_work.hidden_people(root)
        if not isinstance(hp, dict) or hp.get("unread"):
            return None
        from entities_io import entities_collection, read_entities_json
        from people_writer import get_person_emails

        people = entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"), "people")
        ids = {str(i) for i in hp.get("ids") or ()}
        mails = {_plain(m).strip().casefold() for p in people if isinstance(p, dict) and str(p.get("id") or "") in ids
                 for m in get_person_emails(p) if str(m).strip()}
        owner = _owner(root)
        records = [p for p in people if isinstance(p, dict)]
        # review of 261a82f28 (MUST 2): only the owner's names of two words
        # or more are set aside; a one-word alias is not the owner's alone
        own = {_flat(_cut(n)) for p in records if str(p.get("id") or "") == owner for n in _record_names(p)
               if len(_flat(_cut(n)).split()) >= 2}
        own_last = {f for p in records if str(p.get("id") or "") == owner
                    for n in _surname_names(p) for f in surname_forms(n)}
        visible_last = {f for p in records if str(p.get("id") or "") not in ids and str(p.get("id") or "") != owner
                        for n in _surname_names(p) for f in surname_forms(n)}
        last: set = set()
        cased: set = set()
        whole: set = set()
        for p in records:
            if str(p.get("id") or "") not in ids:
                continue
            for n in _surname_names(p):
                for f in surname_forms(n):
                    if f in own_last:
                        continue
                    (cased if f in ORDINARY_SURNAMES else last).add(f)
            whole |= {_flat(_cut(n)) for n in _record_names(p) if len(_flat(_cut(n)).split()) >= 2}
    except Exception:  # noqa: BLE001 - fail closed
        return None
    names = {f for f in (_flat(n) for n in hp.get("names") or ()) if f}
    return {"names": names | last,
            "shown": {f for f in (_flat(n) for n in hp.get("shown_full") or ()) if f},
            "emails": mails,
            "own": {n for n in own if n},
            "cased": cased,
            "shared": (last | cased) & visible_last,
            # review of 261a82f28 (MUST 1): the hidden people's whole names of
            # two words or more, matched before anything is set aside
            "whole": whole}


def voice_hidden_names(hw: Dict[str, Any]) -> List[str]:
    """cr1#1846 (coordinator decision): the hidden names the page's own
    screen may carry as hashes (`feed_voice.hidden`, ruling 143): every name
    and last name, but never an ordinary-word last name (`cased`), whose
    capital-letter rule a hash cannot carry; for those the page says nothing
    and the Update's recorder applies the rule. Pure."""
    # An ordinary-word last name lives in `cased` only and is never one of
    # `names`; a word in both is someone's first name or whole name, and
    # stays (review of 261a82f28).
    return sorted(str(n) for n in hw.get("names") or () if str(n).strip())


def _cased_hits(texts: List[Any], hw: Dict[str, Any]) -> List[str]:
    """The ordinary-word last names written capitalised and not first in
    their sentence, after the whole names of the owner and of visible people
    are set aside. Pure."""
    import re

    raw = " | ".join(_plain(t) for t in texts)
    for whole in sorted(set(hw.get("shown") or ()) | set(hw.get("own") or ()), key=len, reverse=True):
        words = [re.escape(w) for w in whole.split()]
        if words:
            raw = re.sub(r"(?i)(?<!\w)" + r"\W+".join(words) + r"(?!\w)", " ", raw)
    hits = []
    for w in sorted(hw.get("cased") or ()):
        for m in re.finditer(r"(?<![\w])" + re.escape(w.capitalize()) + r"(?![\w])", raw):
            before = raw[:m.start()].rstrip()
            if before.endswith("."):
                prev = re.findall(r"(\w+)\.$", before)
                if prev and prev[-1].casefold() in NAME_TITLES:
                    hits.append(w)   # "Mr. Brown": the title's stop opens no sentence
                    break
            if before and before[-1] not in ".!?:|\n":
                hits.append(w)
                break
    return hits


def hidden_reason(texts: List[Any], hw: Dict[str, Any]) -> Optional[str]:
    """None when `texts` names nobody hidden; else `named`, or `shared` when
    the only words it was held on are last names a visible person's name ends
    in too (cr1#1846). The addresses first; the whole names of the owner and
    of people on file who are not hidden are set aside before any name is
    matched (cr1#1733). Pure."""
    raw = _plain(" | ".join(str(t or "") for t in texts)).casefold()
    if any(m in raw for m in hw.get("emails") or ()):
        return "named"
    flat = " " + _flat(raw) + " "
    # review of 261a82f28 (MUST 1): a hidden person's whole name of two words
    # or more holds BEFORE anything is set aside, so neither the owner's name
    # nor a visible person's inside it ("Sam Sample Jr.") can hide it
    if any((" " + n + " ") in flat for n in hw.get("whole") or ()):
        return "named"
    for s in sorted(set(hw.get("shown") or ()) | set(hw.get("own") or ()), key=len, reverse=True):
        flat = flat.replace(" " + s + " ", "  ")
    hits = [n for n in hw.get("names") or () if (" " + n + " ") in flat]
    hits += _cased_hits([str(t or "") for t in texts], hw)
    if not hits:
        return None
    shared = set(hw.get("shared") or ())
    return "shared" if all(h in shared for h in hits) else "named"


def names_hidden(texts: List[Any], hw: Dict[str, Any]) -> bool:
    """True when any of `texts` names someone hidden: one of their names or
    last names as a whole word or phrase (an ordinary-word last name only
    capitalised and not first in its sentence), or one of their addresses.
    The whole names of the owner and of someone on file who is not hidden are
    theirs (cr1#1733) and are set aside first. Pure."""
    return hidden_reason(texts, hw) is not None


def _email_index(root: Path) -> Optional[Dict[str, str]]:
    """`{address: person id}` over the people on file. None when unread."""
    try:
        from entities_io import read_entities_json
        from event_refs import email_person_index

        return dict(email_person_index(read_entities_json(root / "_hq" / "data" / "entities.json")))
    except Exception:  # noqa: BLE001
        return None


def meeting_facts(ref: Any, index: Dict[str, List[dict]], email_index: Dict[str, str], owner: str) -> Dict[str, Any]:
    """What the book itself says of the processed meeting `ref` names:
    `{processed, has_row, people, others, external, titles, calendar,
    solo}`. `people` are the person ids on its `meeting` rows other than
    the owner, `others` how many other people are on it at all (an address
    or a name not on file counts), `external` the names not on file,
    `calendar` what its `meeting` rows say: "personal" when any says so,
    "business" when one says so and none says personal, "" when none says. Pure."""
    from event_refs import attendee_emails_of, meeting_person_ids

    mine = index.get(_ref_key(ref)) or []
    out: Dict[str, Any] = {"processed": any(e.get("type") == "meeting_processed" for e in mine), "has_row": False,
                           "people": set(), "others": 0, "external": [], "titles": [], "calendar": "", "solo": False}
    loose: set = set()
    for e in mine:
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if e.get("type") in ("meeting", "meeting_processed"):
            if str(d.get("title") or "").strip():
                out["titles"].append(str(d["title"]))
        if e.get("type") != "meeting":
            continue
        out["has_row"] = True
        cal = str(d.get("calendar") or "").strip().lower()
        if cal == "personal" or (cal == "business" and out["calendar"] != "personal"):
            out["calendar"] = cal
        out["people"] |= {str(p) for p in meeting_person_ids(e, email_index) if str(p).strip()}
        loose |= {m for m in attendee_emails_of(e) if m not in email_index}
        out["external"] += [str(n) for n in d.get("attendees_external") or [] if str(n).strip()]
        if d.get("working_session") is True or d.get("solo") is True:
            out["solo"] = True
    out["people"].discard(str(owner or ""))
    out["others"] = len(out["people"]) + len(loose) + len(out["external"])
    if out["has_row"] and not out["others"]:
        out["solo"] = True
    return out


def _skipped(rules: List[Dict[str, Any]], meeting_id: Any, titles: List[Any], recurring: Any = False) -> bool:
    """A skip rule matches the meeting: by its id (a rule is keyed by id only
    when the meeting recurs), or by any of its titles."""
    keys = {skip_key(meeting_id, "", True)} | {skip_key("", t, False) for t in titles}
    keys.discard("")
    return any(str(r.get("key")) in keys for r in rules)


def is_skipped(workspace_root, meeting_id: Any, title: Any = "") -> Optional[bool]:
    """READ. Whether a "don't draft for meetings like this" rule matches the
    meeting (the wrap-up's `nodraft` mark). None when the rules cannot be
    read."""
    rules = skip_rules(workspace_root)
    return None if rules is None else _skipped(rules, meeting_id, [title])


def drafts_by_ref(evs: List[dict]) -> Dict[str, Dict[str, Any]]:
    """The newest `followup_draft` row's data per meeting ref key. Pure."""
    from event_seq import event_seq

    out: Dict[str, Dict[str, Any]] = {}
    for e in evs:
        if isinstance(e, dict) and e.get("type") == EVENT and isinstance(e.get("data"), dict):
            k = _ref_key(e["data"].get("ref"))
            if k:
                out[k] = dict(e["data"], seq=event_seq(e))
    return out


def record_draft(workspace_root, *, meeting_id: str, ref: str, to_person_ids: List[str], subject: str, body: str,
                 start: Any = "", title: str = "", calendar: str = "", solo: bool = False, recurring: Any = False,
                 now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. Keep one meeting's follow-up text, already written in the
    owner's voice by email-writer's rules. Answers `{ok, outcome, line,
    seq}`; refuses (writes nothing) by the fence above. Who attended and
    which calendar it is on are read from the meeting's own rows in the
    book (no row saying "business": no draft); `calendar`, `solo`,
    `title` and `recurring` from the caller can only add a refusal."""
    from event_gate import append_event

    root = _root(workspace_root)

    def no(line: str, outcome: str = "refused") -> Dict[str, Any]:
        return {"ok": outcome == "already", "outcome": outcome, "line": line, "seq": None}

    if str(calendar or "").strip().lower() == "personal":
        return no(PERSONAL_LINE)
    if solo is True:
        return no(SOLO_LINE)
    subject = str(subject or "").strip()[:SUBJECT_CAP]
    body = str(body or "").strip()[:BODY_CAP]
    if not subject or not body or not str(ref or "").strip():
        return no(EMPTY_LINE)
    rules = skip_rules(root)
    if rules is None:
        return no(HELD_LINE)
    hidden = _hidden(root)
    if hidden is None:
        return no(HELD_LINE)
    hw = hidden_words(root)
    mails = _email_index(root)
    if hw is None or mails is None:
        return no(HELD_LINE)
    owner = _owner(root)
    try:
        import hub_meeting

        evs = _events(root)
        facts = meeting_facts(ref, hub_meeting.index_by_ref(evs), mails, owner)
    except Exception:  # noqa: BLE001 - the book unread: nothing is kept
        return no(HELD_LINE)
    # the meeting's own rows, never the caller's word for who was on it
    if not facts["processed"]:
        return no(NOT_PROCESSED_LINE)
    if not facts["has_row"]:
        return no(HELD_LINE)
    if facts["calendar"] == "personal":
        return no(PERSONAL_LINE)
    if facts["calendar"] != "business":
        return no(NO_CALENDAR_LINE)
    if facts["solo"]:
        return no(SOLO_LINE)
    if _skipped(rules, meeting_id, [title] + facts["titles"], recurring):
        return no(SKIPPED_LINE)
    to = [str(p) for p in (to_person_ids or []) if str(p).strip() and str(p) != owner]
    if facts["people"] & hidden or names_hidden(facts["external"], hw):
        return no(HIDDEN_LINE)
    if any(p not in facts["people"] for p in to):
        return no(NOT_ON_LINE)
    if not to:
        return no(NOBODY_LINE)
    why = hidden_reason([subject, body, title] + facts["titles"], hw)
    if why:
        return no(SURNAME_LINE if why == "shared" else NAMED_LINE)  # cr1#1846
    if _ref_key(ref) in drafts_by_ref(evs):
        return no(ALREADY_LINE, "already")
    ev = {"type": EVENT, "source_skill": SOURCE_SKILL, "person_ids": to,
          "data": {"meeting_id": str(meeting_id or "")[:200], "ref": str(ref)[:400], "start": str(start or "")[:40],
                   "title": str(title or "")[:SUBJECT_CAP], "to_person_ids": to, "subject": subject, "body": body,
                   "recurring": recurring is True}}
    if now_iso:
        ev["ts"] = now_iso
    written = append_event(root / "_hq" / "data" / "events.jsonl", ev, holder="followup_draft")
    seq = written[0].get("seq") if written and isinstance(written[0], dict) else None
    return {"ok": bool(written), "outcome": "applied" if written else "error", "line": KEPT_LINE, "seq": seq}


def _people(root: Path) -> Dict[str, Dict[str, Any]]:
    try:
        from entities_io import entities_collection, read_entities_json
        from people_writer import get_person_emails

        people = entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"), "people")
    except Exception:  # noqa: BLE001
        return {}
    return {str(p.get("id")): {"name": str(p.get("canonical_name") or ""), "emails": get_person_emails(p)}
            for p in people if isinstance(p, dict) and p.get("id")}


def feed_followups(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. The Hub's feed/followups doc: the newest FEED_CAP follow-ups
    still waiting (their meeting not yet followed up), each `{meeting_id,
    ref, start, title, to: [{id, name, email}], subject, body}`, checked
    against the wall as it stands now: a draft to anyone hidden, one whose
    meeting has someone hidden on it, one whose words name someone hidden,
    a recipient no longer on the meeting, or a recipient with no address,
    is left out and counted in `n_held`. A
    draft a skip rule now matches is left out too (`n_skipped`). The
    bodies live here only, never in the plate or the today feed. `ok:
    False` (nothing listed) when the wall, the rules or the book cannot be
    read."""
    root = _root(workspace_root)
    out: Dict[str, Any] = {"ok": False, "drafts": [], "n_held": 0, "n_skipped": 0}
    try:
        import hub_meeting

        hidden = _hidden(root)
        if hidden is None:
            return out
        hw = hidden_words(root)
        mails = _email_index(root)
        rules = skip_rules(root)
        if hw is None or mails is None or rules is None:
            return out
        owner = _owner(root)
        evs = _events(root)
        index = hub_meeting.index_by_ref(evs)
        people = _people(root)
        rows = sorted(drafts_by_ref(evs).values(), key=lambda d: d["seq"] if isinstance(d.get("seq"), int) else -1, reverse=True)
        n_held = n_skipped = 0
        for d in rows:
            if hub_meeting._followed_up(evs, d.get("ref"), index):
                continue
            facts = meeting_facts(d.get("ref"), index, mails, owner)
            if _skipped(rules, d.get("meeting_id"), [d.get("title")] + facts["titles"], d.get("recurring") is True):
                n_skipped += 1
                continue
            # the meeting's own people and the words, against the wall as it stands now
            if not facts["has_row"] or facts["calendar"] != "business" \
                    or facts["people"] & hidden or names_hidden(facts["external"], hw) \
                    or names_hidden([d.get("subject"), d.get("body"), d.get("title")] + facts["titles"], hw):
                n_held += 1
                continue
            to_ids = [str(p) for p in d.get("to_person_ids") or []]
            # Ruled into #1772: a recipient now off limits, or no longer on the
            # meeting's rows, holds the draft (its words were written for them).
            if any(pid in hidden or pid not in facts["people"] for pid in to_ids):
                n_held += 1
                continue
            to = []
            for pid in to_ids:
                rec = people.get(pid) or {}
                mail = (rec.get("emails") or [""])[0]
                if not mail:
                    to = None
                    break
                to.append({"id": pid, "name": rec.get("name") or "", "email": mail})
            if not to:
                n_held += 1
                continue
            if len(out["drafts"]) < FEED_CAP:
                out["drafts"].append({"meeting_id": d.get("meeting_id") or "", "ref": d.get("ref") or "",
                                      "start": d.get("start") or "", "title": d.get("title") or "",
                                      "to": to, "subject": d.get("subject") or "", "body": d.get("body") or ""})
        out.update(ok=True, n_held=n_held, n_skipped=n_skipped)
    except Exception:  # noqa: BLE001 - fail closed: nothing listed
        out.update(ok=False, drafts=[], n_held=0, n_skipped=0)
    return out


def waiting_refs(evs: List[dict]) -> set:
    """The ref keys with a follow-up kept (the wrap-up's `draft` mark). Pure."""
    return set(drafts_by_ref(evs))


# ---- the page's own drafts (cr1#1812, ruling 135a) ----
#
# The Hub page may write a draft itself (a redraft, a person added by hand, a
# changed kind, one email to several). It never sends: it saves the draft as
# one press, a `pagedraft` choice, and the next Update records it in the book
# beside the drafts written ahead. `plan_page_draft` is that recording's
# rule, as a pure function: it trusts nothing the page said. It runs the
# Python voice check again, checks the recipients against the wall, refuses
# a forged press and keys the record so one press is kept once. Not wired
# into the Update yet (v6.12 groundwork); the writer that appends its event
# comes with the wiring, with its row in the event types.
#
# The row's shape is agreed with #1787 (the follow-ups feed, 2026-11-25):
# the same names as `record_draft`'s rows, so `feed_followups` reads one
# shape: `kind` (the kind of email), `voice` ("main" when the main voice file
# was applied, else ""), `voice_block_sha256` (None: it is the main file);
# a meeting's draft carries `meeting_id`, `ref` (the meeting's processed
# ref), `meeting_key` and `email_id` (the plan email it drafts, "" for the
# automatic one) and `about: "meeting"`; a draft for an item carries
# `about: "item"`, which the follow-ups feed leaves out. The meeting fence
# (who was on it, the owner's removals, the plan's recipients) is #1787's
# `meeting_fence`; the wiring calls it for a meeting's draft before this.
#
# The voice stamp (cr1#1822, agreed with #1787 on 2026-11-25): "In your voice"
# reads the pair `voice_source` ("brand_voice", "learned_block" or "both") and
# `voice_rules_sha256` (the hash of that text), both set or both None;
# `voice_block_sha256` keeps its old meaning, the learned block's hash only.
# A page draft sets the pair only when its voice is the one that stands now.

PAGE_DRAFT_KIND = "pagedraft"
PAGE_DRAFT_ACTION = "keep"
PAGE_DRAFT_SOURCE = "hub-page"
#: Ruling 135a: the page writes on the standard model only, which the page's
#: sampler names "default" (runtime contract 0.2.99, probed on a real page).
PAGE_DRAFT_TIER = "default"
#: cr1#1822: the voice sources the stamp names; any other leaves it unset.
VOICE_SOURCES = ("brand_voice", "learned_block", "both")
#: A row left on "let Command Room pick" is not drafted on the page (136b).
PAGE_DRAFT_NO_KIND = frozenset({"", "auto", "pick", "any"})
_PAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:]{1,120}$")
#: What a page draft answers (agreed with #1787): a meeting, or an item.
PAGE_DRAFT_ABOUT = ("meeting", "item")
_MEETING_KEY_RE = re.compile(r"^(?:[0-9a-f]{32})?$")

PAGE_FORGED_LINE = "That draft did not come from the Hub page as it should, so it was not kept."
PAGE_NO_KIND_LINE = "That draft names no kind of email, so it was not kept."
PAGE_TIER_LINE = "That draft was not written on the standard model, so it was not kept."
PAGE_CHECK_LINE = "That draft did not pass the voice check, so it is kept as refused and is not ready to send."
PAGE_HELD_LINE = "Who that draft is for could not be checked, so it was not kept."
PAGE_HIDDEN_LINE = "Someone on that draft is off limits or on your personal side, so it was not kept."
PAGE_NAMED_LINE = "That draft names someone who is off limits or on your personal side, so it was not kept."
PAGE_NOBODY_LINE = "That draft is addressed to nobody else, so it was not kept."
PAGE_ALREADY_LINE = "That draft is already kept."
PAGE_KEPT_LINE = "Draft kept. It waits on the Hub; nothing is sent until you press."


def page_draft_press(*, draft_id: str, ref: str, to_person_ids: List[str], email_kind: str, subject: str,
                     body: str, voice: Optional[Dict[str, Any]], check: Optional[Dict[str, Any]],
                     about: str = "meeting", meeting_id: str = "", meeting_key: str = "", email_id: str = "",
                     tier: str = PAGE_DRAFT_TIER, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The press the page saves for one draft it wrote: a `choices`
    document, so the store rule holds (the page writes its own press and no
    shared document). `input` holds the draft: what it answers (`about`:
    a meeting, with its `meeting_id`, processed `ref`, `meeting_key` and the
    plan's `email_id`; or an item, its id in `ref`), the recipients as person
    ids, the kind of
    email, the subject and body, the voice it used (`{source, sha256}`), the
    page's own check (`{verdict, rules}`, recorded, never trusted) and the
    tier. Pure."""
    rules = sorted({str(f.get("rule")) for f in (check or {}).get("findings") or [] if isinstance(f, dict)})
    return {"id": PAGE_DRAFT_KIND + "-" + str(draft_id), "kind": PAGE_DRAFT_KIND, "action": PAGE_DRAFT_ACTION,
            "n": str(draft_id), "applied": False, "created_at": now_iso or "",
            "input": {"about": str(about or ""), "meeting_id": str(meeting_id or ""), "meeting_key": str(meeting_key or ""),
                      "email_id": str(email_id or ""),
                      "ref": str(ref or ""), "to_person_ids": [str(p) for p in to_person_ids or []],
                      "email_kind": str(email_kind or ""), "subject": str(subject or ""), "body": str(body or ""),
                      "voice": {"source": str((voice or {}).get("source") or ""),
                                "sha256": str((voice or {}).get("sha256") or "")},
                      "check": {"verdict": str((check or {}).get("verdict") or ""), "rules": rules},
                      "tier": str(tier or "")}}


def kept_page_drafts(evs: List[dict]) -> set:
    """The press ids already kept in the book (the once-only key). Pure."""
    return {str(e["data"].get("press_id")) for e in evs or []
            if isinstance(e, dict) and e.get("type") == EVENT and isinstance(e.get("data"), dict)
            and e["data"].get("origin") == "page" and e["data"].get("press_id")}


def plan_page_draft(press: Any, *, owner_id: str, hidden_ids: Optional[set], hidden_word_sets: Optional[Dict[str, Any]],
                    kept: set, voice_now: Optional[Dict[str, Any]], now_iso: Optional[str] = None) -> Dict[str, Any]:
    """What the next Update does with one `pagedraft` press. Pure: `{outcome,
    line, press_id, event, findings, voice}`.

    `outcome` is `keep` (with the `followup_draft` row to append, in the
    shape agreed with #1787: the draft, its kind, its voice stamp and the
    check's verdict, marked `origin: page`, `about` meeting or item, and
    keyed by the press id), `refused` (the Python check failed: its findings, and no
    ready draft), `not_kept` (a forged press, no kind, a tier other than the
    standard one, nothing to send, nobody else on it, someone hidden, or a
    wall that cannot be read: `hidden_ids` or `hidden_word_sets` None) or
    `already` (this press was kept before). The page's own verdict is never
    trusted; the check runs here again with the draft's kind (135d).

    `voice_now` is the owner's voice rules as they stand (`{source,
    sha256}`, `source` one of `VOICE_SOURCES`; None when there are none). The
    stamp (`voice_source`, `voice_rules_sha256`, cr1#1822) is set, and the
    draft is in the owner's voice (130c), only when the press's voice hash is
    that text's and the source is one of the three; otherwise both are None."""
    import voice_tell_detector as vtd

    def out(outcome: str, line: str, **kw) -> Dict[str, Any]:
        base = {"outcome": outcome, "line": line, "press_id": pid, "event": None, "findings": [], "voice": None}
        base.update(kw)
        return base

    pid = str(press.get("id") or "") if isinstance(press, dict) else ""
    inp = press.get("input") if isinstance(press, dict) and isinstance(press.get("input"), dict) else None
    if (inp is None or press.get("kind") != PAGE_DRAFT_KIND or press.get("action") != PAGE_DRAFT_ACTION
            or not _PAGE_ID_RE.match(str(press.get("n") or ""))
            or pid != PAGE_DRAFT_KIND + "-" + str(press.get("n") or "")
            or not isinstance(inp.get("to_person_ids"), list)
            or not all(isinstance(p, str) and _PAGE_ID_RE.match(p) for p in inp["to_person_ids"])
            or str(inp.get("about") or "") not in PAGE_DRAFT_ABOUT
            or not _MEETING_KEY_RE.match(str(inp.get("meeting_key") or ""))
            or (inp.get("about") == "item" and (inp.get("meeting_id") or inp.get("meeting_key") or inp.get("email_id")))):
        return out("not_kept", PAGE_FORGED_LINE)
    about = str(inp["about"])
    subject = str(inp.get("subject") or "").strip()[:SUBJECT_CAP]
    body = str(inp.get("body") or "").strip()[:BODY_CAP]
    ref = str(inp.get("ref") or "").strip()
    kind = str(inp.get("email_kind") or "").strip()
    if not subject or not body or not ref:
        return out("not_kept", EMPTY_LINE)
    if kind.lower() in PAGE_DRAFT_NO_KIND:
        return out("not_kept", PAGE_NO_KIND_LINE)
    if str(inp.get("tier") or "") != PAGE_DRAFT_TIER:
        return out("not_kept", PAGE_TIER_LINE)
    if hidden_ids is None or hidden_word_sets is None:
        return out("not_kept", PAGE_HELD_LINE)
    to = [p for p in inp["to_person_ids"] if p and p != str(owner_id or "")]
    if not to:
        return out("not_kept", PAGE_NOBODY_LINE)
    if any(p in hidden_ids for p in to):
        return out("not_kept", PAGE_HIDDEN_LINE)
    if names_hidden([subject, body], hidden_word_sets):
        return out("not_kept", PAGE_NAMED_LINE)
    if pid in (kept or set()):
        return out("already", PAGE_ALREADY_LINE)
    # review round 1 of #1813: the kind comes from the page, so its list allowance
    # (135d: a proposal or next steps may use bullets) holds for a meeting's
    # follow-up only, where such a list is the shape; an item's draft is checked
    # as any email (the allowance only drops warnings, never a fail)
    list_kind = kind if about == "meeting" else ""
    body_check = vtd.scan_text(body, context="email", kind=list_kind)
    # the subject of the message replied to, when the page had one: not the draft's own words
    subject_check = vtd.scan_subject(subject, inbound=str(inp.get("inbound_subject") or "")[:SUBJECT_CAP] or None)
    findings = [dict(f, where="subject") for f in subject_check["findings"]] + \
        [dict(f, where="body") for f in body_check["findings"]]
    verdict = "fail" if "fail" in (body_check["verdict"], subject_check["verdict"]) else \
        ("warn" if "warn" in (body_check["verdict"], subject_check["verdict"]) else "pass")
    if verdict == "fail":
        return out("refused", PAGE_CHECK_LINE, findings=findings)
    pv = inp.get("voice") if isinstance(inp.get("voice"), dict) else {}
    seen = str(pv.get("sha256") or "").strip().lower()
    now_sha = str((voice_now or {}).get("sha256") or "").strip().lower()
    now_src = str((voice_now or {}).get("source") or "")
    applied = bool(seen) and bool(now_sha) and seen == now_sha and now_src in VOICE_SOURCES
    voice = {"source": now_src if applied else None, "sha256": now_sha if applied else None, "applied": applied}
    data = {"meeting_id": str(inp.get("meeting_id") or "")[:200] if about == "meeting" else "",
            "ref": ref[:400], "meeting_key": str(inp.get("meeting_key") or "") if about == "meeting" else "",
            "email_id": str(inp.get("email_id") or "")[:200] if about == "meeting" else "",
            "about": about, "start": "", "title": "", "to_person_ids": to,
            "subject": subject, "body": body, "recurring": False,
            "kind": kind[:40],
            "voice": {"brand_voice": "main", "learned_block": "learned", "both": "main"}.get(voice["source"] or "", ""),
            "voice_block_sha256": voice["sha256"] if voice["source"] == "learned_block" else None,
            "voice_source": voice["source"], "voice_rules_sha256": voice["sha256"], "voice_applied": applied,
            "origin": "page", "press_id": pid, "tier": PAGE_DRAFT_TIER,
            "check_verdict": verdict}
    ev = {"type": EVENT, "source_skill": PAGE_DRAFT_SOURCE, "person_ids": to, "data": data}
    if now_iso:
        ev["ts"] = now_iso
    return out("keep", PAGE_KEPT_LINE, event=ev, findings=findings, voice=voice)
