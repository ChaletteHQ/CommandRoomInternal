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

THE OWNER'S PLAN (cr1#1787)
Before processing, the meeting page lets the owner choose who gets which
email (`hub_meetplan`, the `meetplan` press): one email to several people,
someone who was not on the call (ruling 125e), someone taken off, and the
kind of each email. The plan is read from its own store, never from the
caller. It changes the fence in three ways and no other:
  * the not-on-the-meeting refusal lifts ONLY for a person the plan names
    on that same email; the automatic run's own draft (no `email_id`) keeps
    it exactly;
  * a person the plan removed is refused, on every path, and a draft
    already written to them is held off the Hub;
  * one draft per meeting becomes one draft per chosen email: the "already"
    check is keyed on the meeting and the email, so a re-run still writes
    nothing twice. The automatic run's own draft is not written to anyone
    on a chosen email (they get the one the owner chose), and one already
    written to them is held; `drafts_wanted` lists what to draft.
Someone off limits or with a personal tie is refused always, whatever the
plan says, and an unreadable plan writes nothing.

Review 2 of #1814: which plan is the meeting's is `hub_meetplan.lookup`'s
answer, by the calendar key (the automatic run passes the calendar event's
id, `calendar_event_id`, hashed here the way the page hashes it), or the id
and start, or the processed notes' own id. When a plan could be this
meeting's and none is exactly, the meeting's drafting is held
(`PLAN_UNSURE_LINE`), never read as "no plan". A draft row says whether it
was written under a plan (`planned`) and, for a chosen email, the hash of
the owner's own words it was written to (`instruction_sha256`): a kept
draft counts as current only while it still matches the plan as it is now,
an Undo back to no plan included. With a plan, the automatic draft goes to
exactly everyone the plan leaves on it (`automatic_wanted`), no fewer. An
email already put in the mailbox or sent is never drafted again. The
voice stamp is stored only when it is the stamp of the voice rules on disk
now (`_voice_now`), never on the caller's word. `drafts_wanted` and
`redrafts_due` are what the automatic run reads before it drafts.

THE FEED (ruling 128h): one row per draft, newest first, each with its own
`draft_id`, the meeting's `meeting_key` when the draft was keyed to one, its
recipients (several allowed), its `kind`, the voice rules it was written
with (`voice`, 130c), its `state` and `created_at`; the document stamps
`generated_at`. A released page reads the first draft that matches its
meeting, as before. Only waiting drafts are listed, so a released page never
shows a sent one.

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
#: cr1#1787 part C (coordinator, GAP 2): a meeting whose drafting was held
#: (its plan could not be matched to it for certain) is recorded as one small
#: row, `{ref, meeting_key, meeting_id, start, reason}`, no names, so the page
#: can say so. Shown until a later draft for the meeting or a change to its
#: plan supersedes it (`held_rows`).
HELD_EVENT = "followup_held"
#: The reasons a hold is recorded for, each said on the page in plain words.
HELD_REASONS: Dict[str, str] = {
    "plan_unsure": ("This meeting's follow-up is held: your follow-up choices could not be matched to it for "
                    "certain (it may have moved), so nothing was drafted."),
    # cr1#1838: the write-up is tied to no calendar event, and a plan exists that day
    # review 5 of #1814: the window is a day either side, not the calendar day
    # sixth review of #1814: candidates reach two days, or any time when matched by id, so no "within a day"
    "no_binding": ("This meeting's follow-up is held: it could not be tied to its calendar event, and you have "
                   "follow-up choices for a meeting close to it in time, so nothing was drafted."),
}
#: Review 5 of #1814: a hold that has ended says what happens now, and stays
#: listed until the meeting is drafted or followed up (it never just vanishes).
RELEASED_LINE = ("This meeting's follow-up was held, and the hold has ended: it is drafted at the next update.")
#: Rule R2 of #1814: a hold not released inside the 14-day draft window is never drafted; said once, then dropped
HELD_LATE_LINE = ("This meeting's follow-up was held for longer than two weeks, so it will not be drafted "
                  "automatically. Write the follow-up yourself if one is needed.")
#: how long the once-said late line stays, past the window
LATE_LINE_DAYS = 1
#: Rule R2: a waiting draft to someone the meeting's plan now leaves out is retired, never left waiting
RETIRED_LINE = ("A follow-up drafted before your choices went to someone you left out, so it was taken off. "
                "Nothing was sent.")
ENDED_LATE_LINE = ("This meeting's follow-up was held, and the hold ended too late for it to be drafted "
                   "automatically. Write the follow-up yourself if one is needed.")
ENDED_NOTHING_LINE = ("This meeting's follow-up was held, and the hold has ended with nothing left to draft for it.")
ENDED_WHY_LINES: Dict[str, str] = {
    "solo": "This meeting's follow-up was held, and the hold has ended: nobody else was on it, so no follow-up is drafted.",
    "personal": "This meeting's follow-up was held, and the hold has ended: it is on your personal calendar, so no follow-up is drafted.",
    "no_calendar": "This meeting's follow-up was held, and the hold has ended: its calendar is not known, so no follow-up is drafted.",
    "walled": "This meeting's follow-up was held, and the hold has ended: someone on it is off limits, so no follow-up is drafted.",
}
#: The recent written-up meetings the feed names its people for (GAP 1).
MEETINGS_FEED_DAYS = 14
MEETINGS_FEED_CAP = 40
NOT_HELD_LINE = "That meeting's follow-up is not held, so nothing was written."
#: cr1#1838: what the writer and the list answer for a meeting tied to no calendar event
NO_BINDING_LINE = ("That meeting could not be tied to its calendar event, and you have follow-up choices for a "
                   "meeting close to it in time, so no follow-up was drafted. Open the meeting on the Hub to choose again.")
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
# cr1#1787: the owner's plan for the meeting (`hub_meetplan`)
REMOVED_LINE = "That follow-up is addressed to someone you took off it, so it was not drafted."
PLAN_NO_EMAIL_LINE = "That email is not in your follow-up plan for the meeting, so it was not drafted."
PLANNED_LINE = "Someone on that follow-up gets the email you chose for them, so this one was not drafted."
PLAN_OTHER_LINE = "That follow-up is addressed to someone your plan does not put on that email, so it was not drafted."
PLAN_UNSURE_LINE = ("Your follow-up plan for that meeting could not be matched to it for certain (it may have moved), "
                    "so no follow-up was drafted. Open the meeting on the Hub to choose again.")
#: The writer's and the list's answer per hold (the page says HELD_REASONS' words).
HOLD_LINES: Dict[str, str] = {"plan_unsure": PLAN_UNSURE_LINE, "no_binding": NO_BINDING_LINE}
AUTO_OTHER_LINE = ("That follow-up is not addressed to exactly the people your plan leaves on the meeting's own "
                   "email, so it was not drafted.")
SENT_LINE = "That email was already put in your mailbox or sent, so it was not drafted again."
#: The skills whose voice rules a follow-up is written with (email-writer's
#: rules, the follow-up-ritual path).
VOICE_SKILLS = ("email-writer", "follow-up-ritual")
#: How far back the automatic run looks for a follow-up the owner's plan
#: changed after it was drafted, and how many it redrafts in one pass.
REDRAFT_DAYS = 14
REDRAFT_CAP = 3
#: The voice rules a draft was written with (ruling 130c): the owner's main
#: voice file, or a learned block, whose hash is then stored with it.
VOICES = ("main", "learned")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
#: The stamp of the voice rules a draft was written with, as cr1#1822's
#: `draft_constraints.voice_source` gives it (cr1#1792, ruling 130c): which
#: rules (`voice_source`) and the hash of their text (`voice_rules_sha256`).
#: `voice_block_sha256` keeps its old meaning (the learned block's hash only)
#: for every existing reader. "In your voice" reads this pair; null shows no mark.
VOICE_SOURCES = ("brand_voice", "learned_block", "both")
#: What a listed draft's `state` is. Only waiting drafts are listed (a draft
#: saved to the mailbox or sent marks its meeting, or the draft itself,
#: followed up and leaves the feed), so a released page never offers a sent one.
STATE_WAITING = "waiting"


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
    # part D fix check: the Undo takes away every rule `_skipped` matches, by id AND by title, so a Skip
    # pressed before the row carried `recurring` (keyed by title) is undone by a press now keyed by id
    drop = {skip_key(meeting_id, "", True), skip_key("", title, False), key}
    drop.discard("")
    if not any(str(r.get("key")) in drop for r in rules):
        return {"outcome": "already", "line": SKIP_REMOVED_LINE, "key": key}
    _write_rules(workspace_root, [r for r in rules if str(r.get("key")) not in drop])
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
        # eighth check of #1814: an off-limits rule's own address, with no person on file, is screened too
        from account_scope_gate import off_limits_readable, off_limits_senders

        if not off_limits_readable(root):
            return None
        mails |= {_plain(m).strip().casefold() for m in off_limits_senders(root) if str(m).strip()}
        rule = last_name_rule([p for p in people if isinstance(p, dict)], ids, _owner(root))
    except Exception:  # noqa: BLE001 - fail closed
        return None
    names = {f for f in (_flat(n) for n in hp.get("names") or ()) if f}
    last, cased, whole = rule["last"], rule["cased"], rule["whole"]
    return {"names": names | last,
            "shown": {f for f in (_flat(n) for n in hp.get("shown_full") or ()) if f},
            "emails": mails,
            "own": rule["own"],
            "cased": cased,
            "shared": rule["shared"],
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


def last_name_rule(records: List[Dict[str, Any]], ids: Any, owner: Any) -> Dict[str, set]:
    """Rule L of cr1#1846 over the people `ids` (any set of hidden ids, off
    limits or personal), one place for every screen that names people
    (`hidden_words`, and `hub_meeting._off_words` since the combined review of
    train/meeting-f): `last`, their last names (`surname_forms` over
    `_surname_names`, never an alias; never one of the owner's own); `cased`,
    an ordinary-word last name, held only capitalised and not first in its
    sentence; `whole`, their whole names of two words or more, matched before
    anything is set aside; `own`, the owner's whole names of two words or
    more, set aside (review of 261a82f28, MUST 2); `shared`, a held last name
    a visible person's name also ends in. Pure."""
    ids = {str(i) for i in ids or ()}
    owner = str(owner or "")
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
    return {"last": last, "cased": cased, "whole": whole, "own": {n for n in own if n},
            "shared": (last | cased) & visible_last}


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
                           "people": set(), "others": 0, "external": [], "titles": [], "calendar": "", "solo": False,
                           "at": "", "binding": None}
    loose: set = set()
    for e in mine:
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if e.get("type") in ("meeting", "meeting_processed"):
            if str(d.get("title") or "").strip():
                out["titles"].append(str(d["title"]))
            # cr1#1838: which calendar occurrence the capture tied it to, as recorded;
            # review 4 of #1814: a later row whose binding names no start never
            # replaces one that does
            b = d.get("calendar_binding")
            if isinstance(b, dict) and (out["binding"] is None or str(b.get("start") or "").strip()):
                out["binding"] = b
        if e.get("type") != "meeting":
            continue
        out["has_row"] = True
        out["at"] = out["at"] or str(e.get("ts") or "")   # the meeting's own start (its capture's row)
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


def drafts_by_key(evs: List[dict]) -> Dict[tuple, Dict[str, Any]]:
    """cr1#1787: the newest `followup_draft` row's data per (meeting ref
    key, email id), with its `seq` and `ts`. A row from before the plan has
    the email id "" (the automatic run's one draft). Pure."""
    from event_seq import event_seq

    out: Dict[tuple, Dict[str, Any]] = {}
    for e in evs:
        if isinstance(e, dict) and e.get("type") == EVENT and isinstance(e.get("data"), dict):
            k = _ref_key(e["data"].get("ref"))
            if k:
                out[(k, str(e["data"].get("email_id") or ""))] = dict(e["data"], seq=event_seq(e),
                                                                      ts=str(e.get("ts") or ""))
    return out


def draft_id_of(ref: Any, email_id: Any, seq: Any) -> str:
    """The draft's own id (ruling 128h): stable for one row of the book, and
    different for every row, a redraft included. A press on a draft names
    it. Pure."""
    import hashlib

    raw = f"{_ref_key(ref)}|{str(email_id or '')}|{seq if isinstance(seq, int) else ''}"
    return "fd_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def follow_marks(evs: List[dict], ref: Any, index: Dict[str, List[dict]]) -> tuple:
    """(whole, ids): whether a `followup_pack_drafted` row marks the whole
    meeting followed up (one naming no draft, as every page before v6.12
    writes), and the draft ids marked one by one (a v6.12 press naming its
    draft, cr1#1787). Read the way `hub_meeting._followed_up` reads. Pure."""
    if not ref:
        return False, set()
    key = _ref_key(ref)
    mine = index.get(key) or []
    seqs = {e.get("seq") for e in mine if isinstance(e.get("seq"), int)}
    whole, ids = False, set()
    for e in mine + (index.get("") or []):
        if e.get("type") != "followup_pack_drafted":
            continue
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        hit = _ref_key(d.get("source_ref")) == key
        if not hit:
            try:
                hit = int(d.get("meeting_event_seq")) in seqs
            except (TypeError, ValueError):
                hit = False
        if not hit:
            continue
        if str(d.get("draft_id") or ""):
            ids.add(str(d["draft_id"]))
        else:
            whole = True
    return whole, ids


def calendar_key(calendar_event_id: Any = "", meeting_key: Any = "") -> str:
    """The calendar key of the occurrence: the key given, else the calendar
    event's id hashed the way the page hashes it (`cockpit_helpers.event_key`,
    cr1#1758). "" for neither."""
    import hub_meetplan

    k = hub_meetplan.norm_key(meeting_key)
    if k or not str(calendar_event_id or "").strip():
        return k
    try:
        from cockpit_helpers import event_key

        return hub_meetplan.norm_key(event_key(calendar_event_id))
    except Exception:  # noqa: BLE001 - no key: the id and start still find the plan
        return ""


def _ref_tail(ref: Any) -> str:
    """The processed notes' own id, from their pointer (`granola:<id>`)."""
    r = str(ref or "").strip()
    return r.rsplit(":", 1)[-1] if ":" in r else ""


def _start_of(start: Any, facts: Dict[str, Any]) -> str:
    """The occurrence's start: the caller's when it is a time with a zone,
    else the meeting's own (its capture's row)."""
    import hub_meetplan

    return str(start) if hub_meetplan.norm_start(start) else str(facts.get("at") or start or "")


def _zoned(value: Any, tz: Any = None) -> str:
    """A start with its zone: one written with no zone is read on the
    workspace's clock (`tz`; review 4 of #1814, as cr1#1838 reads it); ""
    when it is not a time, or has no zone and no clock to read it on. Pure."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        d = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if d.tzinfo is None:
        if tz is None:
            return ""
        d = d.replace(tzinfo=tz)
    return d.isoformat()


def writeup_context(facts: Optional[Dict[str, Any]], ref: Any, start: Any, meeting_id: Any = "",
                    tz: Any = None) -> Dict[str, Any]:
    """Rule R2's view of one write-up: its ref, its own start (zoned on the
    workspace's clock), its ids (the notes' own, and the caller's meeting
    id), and its binding when the write-up's own start confirms it (else
    None, treated as no binding). Pure."""
    import hub_meetplan

    tail = _ref_tail(ref)
    own = _zoned(start, tz)
    b = (facts or {}).get("binding") if isinstance((facts or {}).get("binding"), dict) else None
    bb = None
    if b is not None and str(b.get("event_id") or ""):
        bb = dict(b, start=_zoned(b.get("start"), tz), end=_zoned(b.get("end"), tz))
    return {"ref": str(ref or ""), "start": own, "tail": tail,
            "ids": tuple(i for i in (tail, str(meeting_id or "").strip()) if i),
            "binding": hub_meetplan.confirmed(bb, own), "bound_to_none": b is not None and bb is None}


def _resolve(store: Dict[str, Any], facts: Optional[Dict[str, Any]], meeting_key: Any, meeting_id: Any,
             start: Any, ref: Any, calendar_event_id: Any = "", tz: Any = None,
             out: Optional[Dict[str, Any]] = None) -> tuple:
    """(plan or None, state, reason) for one write-up, by rule R2
    (`hub_meetplan.resolve`, notes/spec_1814_rule_R2.md): the capture's
    recorded binding, kept only when the write-up's own start confirms it,
    and the write-up's own plan by its ref, never the caller's key or
    calendar id. `out`, when given, gets the rule's word (`rule`) for the
    run's receipt. Pure."""
    import hub_meetplan

    ctx = writeup_context(facts, ref, start, meeting_id, tz)
    rec, state, rule = hub_meetplan.resolve(store, ctx)
    if out is not None:
        out["rule"] = rule
    reason = "" if state != "unsure" else ("no_binding" if ctx["bound_to_none"] else "plan_unsure")
    return rec, state, reason


def writeup_ctx(workspace_root, ref: Any) -> Optional[Dict[str, Any]]:
    """READ. Rule R2's view of the write-up `ref` names, from the book (for a
    press made on its page), or None when that write-up is not on the book."""
    root = _root(workspace_root)
    try:
        import hub_meeting

        evs = _events(root)
        mails = _email_index(root)
        if mails is None:
            return None
        facts = meeting_facts(ref, hub_meeting.index_by_ref(evs), mails, _owner(root))
    except Exception:  # noqa: BLE001 - unread: no press on a write-up lands
        return None
    if not facts["processed"]:
        return None
    return writeup_context(facts, ref, facts.get("at") or "", "", _ws_tz(root))


#: Seventh check of #1814, MUST B: a write-up whose binding its own start confirms counts for a press by key or
#: id only within this many hours of the press's start; one whose binding is not confirmed only when it starts
#: from UNCONFIRMED_EARLY_MINUTES before the press's start to its end.
REFS_CONFIRMED_HOURS = 48
UNCONFIRMED_EARLY_MINUTES = 60


def writeup_refs_for(workspace_root, *, key: Any = "", meeting_id: Any = "", start: Any = "",
                     window: Any = None) -> set:
    """READ. The write-ups on the book (by ref identity, `hub_meetplan._ref_id`)
    that are one calendar occurrence's (sixth review of #1814, MUST 1), for a
    press whose occurrence starts at `start` and spans `window` (`(from, to)`,
    from WINDOW_EARLY_MINUTES before its start to its end, exclusive): a
    write-up starting inside `window`; or one bound to the press's key or id,
    as its capture recorded it, counted only when (seventh check, MUST B) its
    binding is confirmed by its own start and that start is within
    REFS_CONFIRMED_HOURS of the press's start, or its binding is not confirmed
    and its start lies from UNCONFIRMED_EARLY_MINUTES before the press's start
    to the window's end. So a wrongly bound write-up hours away, or last week's
    write-up of a series sharing one key, never refuses this press; a meeting
    that started early and ended early still does. An unreadable book raises,
    so the press asking waits rather than lands."""
    import hub_meeting
    import hub_meetplan

    root = _root(workspace_root)
    k, mid = hub_meetplan.norm_key(key), hub_meetplan.norm_meeting_id(meeting_id)
    ps = hub_meetplan._minute(start)
    tz = _ws_tz(root)
    out: set = set()
    for rk, rows_ in hub_meeting.index_by_ref(_events(root)).items():
        if not rk:
            continue
        ref, at, bound_ = "", "", None
        for e in rows_:
            if e.get("type") not in ("meeting", "meeting_processed"):
                continue
            d = e.get("data") if isinstance(e.get("data"), dict) else {}
            ref = ref or str(d.get("source_ref") or "")
            b = d.get("calendar_binding") if isinstance(d.get("calendar_binding"), dict) else {}
            if (k and hub_meetplan.norm_key(b.get("key")) == k) \
                    or (mid and hub_meetplan.norm_meeting_id(b.get("event_id")) == mid):
                bound_ = b
            if e.get("type") == "meeting":
                at = at or str(e.get("ts") or "")
        own = _zoned(at, tz)
        s = hub_meetplan._minute(own)
        hit = bool(window and s is not None and window[0] <= s < window[1])
        if not hit and bound_ is not None and s is not None and ps is not None:
            bb = dict(bound_, start=_zoned(bound_.get("start"), tz), end=_zoned(bound_.get("end"), tz))
            if hub_meetplan.confirmed(bb, own) is not None:
                hit = abs((s - ps).total_seconds()) <= REFS_CONFIRMED_HOURS * 3600
            else:
                to = window[1] if window else ps
                hit = ps - _dt.timedelta(minutes=UNCONFIRMED_EARLY_MINUTES) <= s < to
        if hit and hub_meetplan._ref_id(ref):
            out.add(hub_meetplan._ref_id(ref))
    return out


def _ws_tz(root: Path) -> Any:
    try:
        import hub_meeting

        return hub_meeting._ws_tz(root)
    except Exception:  # noqa: BLE001
        return None


def _plan_state(root: Path, meeting_key: Any, meeting_id: Any, start: Any = "", ref: Any = "",
                calendar_event_id: Any = "", facts: Optional[Dict[str, Any]] = None,
                out: Optional[Dict[str, Any]] = None) -> tuple:
    """(plan, state, reason) for this occurrence (`_resolve`, over the store
    on disk): the plan `{}` unless state is "exact"; "unread" or "unsure"
    draft nothing; `reason` the hold's code."""
    try:
        import hub_meetplan

        store, unread = hub_meetplan.load_store(root)
        if unread:
            return {}, "unread", ""
        rec, state, reason = _resolve(store, facts, meeting_key, meeting_id, start, ref, calendar_event_id,
                                      _ws_tz(root), out)
        import copy as _copy

        return (_copy.deepcopy(rec) if rec else {}), state, reason
    except Exception:  # noqa: BLE001 - fail closed
        return {}, "unread", ""


def _plan(root: Path, meeting_key: Any, meeting_id: Any, start: Any = "", ref: Any = "") -> Optional[Dict[str, Any]]:
    """The owner's plan for this occurrence of the meeting: `{}` for none,
    None when it cannot be read or cannot be told for certain."""
    plan, state, _why = _plan_state(root, meeting_key, meeting_id, start, ref)
    return None if state in ("unread", "unsure") else plan


def chosen_people(plan: Optional[Dict[str, Any]]) -> set:
    """Everyone on one of the owner's chosen emails for the meeting: the
    automatic run's own draft is not written to them (they get the email
    the owner chose), and one already written to them is held. Pure."""
    import hub_meetplan

    return {p for e in hub_meetplan.emails(plan) for p in e["to"]}


def _voice(voice: Any, sha: Any, source: Any = None, rules: Any = None) -> tuple:
    """(voice, hash) as stored. Never on the caller's word alone (review of
    #1814): `main` only with cr1#1822's pair saying the brand voice (or both)
    was applied, `learned` only with the pair saying the learned block was,
    and that block's hash (64 hex). Anything else is stored as no voice, so
    "In your voice" is never shown for a draft that cannot say which rules
    it used."""
    v = str(voice or "").strip().lower()
    h = str(sha or "").strip().lower()
    src, _rules = voice_pair(source, rules)
    if v == "main" and src in ("brand_voice", "both"):
        return "main", None
    if v == "learned" and src in ("learned_block", "both") and _SHA_RE.match(h):
        return "learned", h
    return "", None


def instruction_sha(text: Any) -> str:
    """The hash of the owner's own words for a chosen email ("" for none). Pure."""
    import hashlib

    t = " ".join(str(text or "").split())
    return hashlib.sha256(t.encode("utf-8")).hexdigest() if t else ""


def matches_now(prev: Dict[str, Any], to: List[str], kind: Optional[str] = None,
                instruction: Optional[str] = None) -> bool:
    """Whether a kept draft is still the email the plan wants now: the same
    recipients, and (for a chosen email, `kind` given) the same kind and the
    same own words (review 2 of #1814). Pure."""
    if set(str(p) for p in prev.get("to_person_ids") or []) != set(str(p) for p in to or []):
        return False
    if kind is None:
        return True
    return str(prev.get("kind") or "") == str(kind) \
        and str(prev.get("instruction_sha256") or "") == instruction_sha(instruction)


def current(prev: Optional[Dict[str, Any]], plan: Optional[Dict[str, Any]], to: List[str],
            chosen: Optional[Dict[str, Any]] = None) -> bool:
    """Whether a kept draft still counts for the email it was written for.
    With no plan now and none when it was written, the automatic draft is
    the step's own and stands as written; otherwise it counts only while it
    matches the plan as it is now (review 2 of #1814: an Undo back to no
    plan rewrites a draft narrowed by the plan). Pure."""
    if not prev:
        return False
    if not (chosen or has_plan(plan) or prev.get("planned") is True):
        return True
    return matches_now(prev, to, chosen["kind"] if chosen else None, chosen["instruction"] if chosen else None)


def has_plan(plan: Optional[Dict[str, Any]]) -> bool:
    """Whether the owner changed anything for this occurrence (a chosen
    email or a person taken off). Without that, the automatic draft is the
    step's own and stands as it was written. Pure."""
    import hub_meetplan

    return bool(hub_meetplan.emails(plan) or hub_meetplan.removed_ids(plan))


def automatic_wanted(facts: Dict[str, Any], plan: Optional[Dict[str, Any]], hidden: set, owner: str) -> set:
    """Who the automatic draft goes to now: everyone on the meeting but the
    owner, less anyone on a chosen email, taken off, or hidden. Pure."""
    import hub_meetplan

    return {p for p in facts["people"] if p != owner} - chosen_people(plan) \
        - hub_meetplan.removed_ids(plan) - set(hidden or ())


def _voice_now(root: Path) -> List[Dict[str, Any]]:
    """The voice rules on disk now, per skill a follow-up is written with:
    `[{source, rules, block}]`: cr1#1822's pair (`voice_source`,
    `voice_rules_sha256`, from `draft_constraints.load_draft_constraints`;
    None, None before it lands) and the learned block's hash. Never raises."""
    out = []
    for skill in VOICE_SKILLS:
        try:
            import draft_constraints as dc

            got = dc.load_draft_constraints(root, skill) or {}
            src, rules = voice_pair(got.get("voice_source"), got.get("voice_rules_sha256"))
            out.append({"source": src, "rules": rules, "block": dc.voice_block_sha256(root, skill)})
        except Exception:  # noqa: BLE001 - nothing read: no stamp is stored
            continue
    return out


def voice_on_disk(root: Path, voice: Any, block_sha: Any, source: Any, rules: Any) -> tuple:
    """(voice, block hash, source, rules) to store (review 2 of #1814): the
    caller's stamp only where it is the stamp of the voice rules on disk now,
    for one of `VOICE_SKILLS`; anything else is stored as no voice."""
    vo, vsha = _voice(voice, block_sha, source, rules)
    vsrc, vrules = voice_pair(source, rules)
    for now in _voice_now(root):
        pair_ok = vsrc is not None and (vsrc, vrules) == (now["source"], now["rules"])
        if not pair_ok:
            continue
        if vo == "learned" and vsha != now["block"]:
            vo, vsha = "", None
        return vo, vsha, vsrc, vrules
    return "", None, None, None


def voice_pair(source: Any, sha: Any) -> tuple:
    """(voice_source, voice_rules_sha256) as stored: both, or neither (None,
    None) when either is missing or not one cr1#1822 writes. Pure."""
    src = str(source or "").strip().lower()
    h = str(sha or "").strip().lower()
    if src in VOICE_SOURCES and _SHA_RE.match(h):
        return src, h
    return None, None


def voice_mark(row: Optional[Dict[str, Any]]) -> bool:
    """Whether a draft may show "In your voice" (ruling 130c, the pair cr1#1822
    defines): only when it carries both `voice_source` and
    `voice_rules_sha256`. Pure."""
    r = row or {}
    return voice_pair(r.get("voice_source"), r.get("voice_rules_sha256")) != (None, None)


def record_draft(workspace_root, *, meeting_id: str, ref: str, to_person_ids: List[str], subject: str, body: str,
                 start: Any = "", title: str = "", calendar: str = "", solo: bool = False, recurring: Any = False,
                 meeting_key: Any = "", email_id: Any = "", kind: Any = "", voice: Any = "",
                 voice_block_sha256: Any = "", voice_source: Any = None, voice_rules_sha256: Any = None,
                 calendar_event_id: Any = "", check_only: bool = False,
                 now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. Keep one meeting's follow-up text, already written in the
    owner's voice by email-writer's rules. Answers `{ok, outcome, line,
    seq}`; refuses (writes nothing) by the fence above. Who attended and
    which calendar it is on are read from the meeting's own rows in the
    book (no row saying "business": no draft); `calendar`, `solo`,
    `title` and `recurring` from the caller can only add a refusal.
    cr1#1787: `email_id` names one of the owner's chosen emails (its
    recipients and kind are the plan's, read from its store), `meeting_key`
    the calendar key the plan may be filed under, `voice` and
    `voice_block_sha256` which voice rules the words were written with.
    `check_only` runs the whole fence and stops before the once-per-email
    check and the write (`meeting_fence`, the page's own drafts).
    `calendar_event_id` is the calendar event's id as the calendar read gave
    it; it is hashed into the calendar key the plan is filed under."""
    from event_gate import append_event
    import hub_meetplan

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
    # cr1#1787: the owner's plan, read from its own store, never the caller's word
    meeting_key = calendar_key(calendar_event_id, meeting_key)
    # cr1#1838: the occurrence's key is the capture's recorded binding when there is one
    _b = facts.get("binding") if isinstance(facts.get("binding"), dict) else None
    if _b is not None and str(_b.get("event_id") or ""):
        meeting_key = str(_b.get("key") or "")
    plan, state, why = _plan_state(root, meeting_key, meeting_id, _start_of(start, facts), ref, calendar_event_id, facts)
    if state == "unread":
        return no(HELD_LINE)
    if state == "unsure":   # review 2 of #1814: a plan that may be this meeting's holds it
        if not check_only:
            # cr1#1787 part C (GAP 2): the hold is recorded, so the page can say so
            _record_held(root, evs, ref=ref, meeting_id=meeting_id, meeting_key=meeting_key,
                         start=_start_of(start, facts), reason=why or "plan_unsure", now_iso=now_iso)
        return no(HOLD_LINES.get(why) or PLAN_UNSURE_LINE, "held")
    eid = str(email_id or "").strip()
    chosen = hub_meetplan.email_of(plan, eid) if eid else None
    if eid and chosen is None:
        return no(PLAN_NO_EMAIL_LINE)
    if not eid and set(to) & chosen_people(plan):
        return no(PLANNED_LINE)
    if set(to) & hidden:
        return no(HIDDEN_LINE)
    if set(to) & hub_meetplan.removed_ids(plan):
        return no(REMOVED_LINE)
    named = set(chosen["to"]) if chosen else set()
    # review of #1814: a chosen email goes to exactly the plan's people, no fewer
    if chosen and set(to) != named:
        return no(PLAN_OTHER_LINE)
    if any(p not in facts["people"] and p not in named for p in to):
        return no(NOT_ON_LINE)
    # review 2 of #1814 (should-fix): with a plan, the automatic draft goes to
    # exactly everyone the plan leaves on it, never fewer
    if not eid and has_plan(plan) and set(to) != automatic_wanted(facts, plan, hidden, owner):
        return no(AUTO_OTHER_LINE)
    if not to:
        return no(NOBODY_LINE)
    why = hidden_reason([subject, body, title] + facts["titles"], hw)
    if why:
        return no(SURNAME_LINE if why == "shared" else NAMED_LINE)  # cr1#1846
    if check_only:
        return {"ok": True, "outcome": "ok", "line": "", "seq": None}
    if chosen:
        kind = chosen["kind"]
    kind = str(kind or "").strip().lower()
    kind = kind if kind in hub_meetplan.KINDS else hub_meetplan.DEFAULT_KIND
    # review of #1814, must-fix 1: a draft already kept counts only while it
    # matches the plan as it is now (the same people, and a chosen email's
    # kind); once the owner changed the plan, the email is written again
    prev = drafts_by_key(evs).get((_ref_key(ref), eid))
    if current(prev, plan, to, chosen):
        return no(ALREADY_LINE, "already")
    # review 2 of #1814: an email already put in the mailbox or sent (or the
    # whole meeting followed up) is never drafted again
    if prev:
        _index = hub_meeting.index_by_ref(evs)
        if follow_marks(evs, ref, _index)[0] or (_ref_key(ref), eid) in sent_emails(evs, _index):
            return no(SENT_LINE, "already")
    vo, vsha, vsrc, vrules = voice_on_disk(root, voice, voice_block_sha256, voice_source, voice_rules_sha256)
    ev = {"type": EVENT, "source_skill": SOURCE_SKILL, "person_ids": to,
          "data": {"meeting_id": str(meeting_id or "")[:200], "ref": str(ref)[:400], "start": str(start or "")[:40],
                   "title": str(title or "")[:SUBJECT_CAP], "to_person_ids": to, "subject": subject, "body": body,
                   "recurring": recurring is True, "meeting_key": hub_meetplan.norm_key(meeting_key),
                   "email_id": eid, "kind": kind, "voice": vo, "voice_block_sha256": vsha,
                   "voice_source": vsrc, "voice_rules_sha256": vrules,
                   # review 2 of #1814: what the draft was written to, so a later
                   # change of the plan (an Undo included) rewrites it
                   "planned": has_plan(plan),
                   "instruction_sha256": instruction_sha(chosen["instruction"]) if chosen else ""}}
    if now_iso:
        ev["ts"] = now_iso
    written = append_event(root / "_hq" / "data" / "events.jsonl", ev, holder="followup_draft")
    seq = written[0].get("seq") if written and isinstance(written[0], dict) else None
    return {"ok": bool(written), "outcome": "applied" if written else "error", "line": KEPT_LINE, "seq": seq}


def _record_held(root: Path, evs: List[dict], *, ref: Any, meeting_id: Any, meeting_key: Any, start: Any,
                 reason: str, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """WRITE (called by the two writers here). One `followup_held` row for
    the meeting, unless the newest one standing for it already says the same
    (a re-run writes nothing twice). No names: the ref, the ids, the start
    and a short reason code only."""
    from event_gate import append_event
    import hub_meetplan

    if reason not in HELD_REASONS:
        return {"outcome": "refused", "seq": None}
    k = _ref_key(ref)
    standing = held_rows(evs, None).get(k)
    if standing and standing.get("reason") == reason:
        return {"outcome": "already", "seq": standing.get("seq")}
    ev = {"type": HELD_EVENT, "source_skill": SOURCE_SKILL, "person_ids": [],
          "data": {"ref": str(ref)[:400], "meeting_key": hub_meetplan.norm_key(meeting_key),
                   "meeting_id": str(meeting_id or "")[:200], "start": str(start or "")[:40], "reason": reason}}
    if now_iso:
        ev["ts"] = now_iso
    written = append_event(root / "_hq" / "data" / "events.jsonl", ev, holder="followup_draft")
    return {"outcome": "applied" if written else "error",
            "seq": written[0].get("seq") if written and isinstance(written[0], dict) else None}


def held_rows(evs: List[dict], store: Optional[Dict[str, Any]] = None, stands=None) -> Dict[str, Dict[str, Any]]:
    """`{ref key: the hold standing for it}`: the newest `followup_held` row
    per meeting, while no later `followup_draft` row for that meeting
    supersedes it and, given `stands` (a hold row -> whether its meeting is
    still unsure, `hold_stands`), while the meeting's plan is still in doubt
    (review 4 of #1814: every hold is released by what ends the doubt, the
    owner's Undo, a binding arriving or the plan changing, and by nothing
    else; a press on another meeting's plan never hides a hold that stands).
    `store` is kept for callers and not read. Pure."""
    from event_seq import event_seq

    out: Dict[str, Dict[str, Any]] = {}
    drafted: Dict[str, int] = {}
    for e in evs:
        if not (isinstance(e, dict) and isinstance(e.get("data"), dict)):
            continue
        k = _ref_key(e["data"].get("ref"))
        if not k:
            continue
        sq = event_seq(e) if isinstance(event_seq(e), int) else -1
        if e.get("type") == HELD_EVENT:
            out[k] = dict(e["data"], seq=sq, ts=str(e.get("ts") or ""))
        elif e.get("type") == EVENT:
            drafted[k] = max(drafted.get(k, -1), sq)
    for k in list(out):
        h = out[k]
        if drafted.get(k, -1) > h["seq"]:
            del out[k]
            continue
        if stands is not None and not stands(h):
            del out[k]
    return out


def hold_stands(store: Dict[str, Any], index: Dict[str, List[dict]], email_index: Dict[str, str], owner: str,
                tz: Any = None):
    """A hold row -> whether its meeting is still held now: its plan worked
    out again exactly as the writer works it out (`_resolve`, over the book's
    own rows for the meeting), so the line on the page and the writer never
    disagree (review 4 of #1814). Pure."""
    def stands(h: Dict[str, Any]) -> bool:
        facts = meeting_facts(h.get("ref"), index, email_index, owner)
        _p, state, _why = _resolve(store, facts, h.get("meeting_key"), h.get("meeting_id"),
                                   _start_of(h.get("start"), facts), h.get("ref"), "", tz)
        return state == "unsure"
    return stands


def record_held(workspace_root, *, ref: Any, meeting_id: Any = "", meeting_key: Any = "", start: Any = "",
                calendar_event_id: Any = "", now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (cr1#1787 part C, GAP 2). Record that a written-up meeting's
    drafting is held, as `drafts_wanted` answered it (`held`), so the page can
    say so. Never on the caller's word: the plan's lookup is read again here,
    and only a meeting it cannot match for certain is recorded. Answers
    `{ok, outcome, line, seq}`. Writes one `followup_held` row at most."""
    root = _root(workspace_root)
    try:
        import hub_meeting

        evs = _events(root)
        mails = _email_index(root)
        if mails is None:
            return {"ok": False, "outcome": "refused", "line": HELD_LINE, "seq": None}
        facts = meeting_facts(ref, hub_meeting.index_by_ref(evs), mails, _owner(root))
        if not facts["processed"] or not facts["has_row"]:
            return {"ok": False, "outcome": "refused", "line": NOT_PROCESSED_LINE, "seq": None}
        if facts["calendar"] != "business" or facts["solo"]:
            # review 5 of #1814: a solo or personal meeting is never held
            return {"ok": False, "outcome": "refused", "line": NOT_HELD_LINE, "seq": None}
        key = calendar_key(calendar_event_id, meeting_key)
        _plan_, state, why = _plan_state(root, key, meeting_id, _start_of(start, facts), ref, calendar_event_id, facts)
        if state != "unsure":
            return {"ok": False, "outcome": "refused", "line": NOT_HELD_LINE, "seq": None}
        why = why or "plan_unsure"
        got = _record_held(root, evs, ref=ref, meeting_id=meeting_id, meeting_key=key,
                           start=_start_of(start, facts), reason=why, now_iso=now_iso)
    except Exception:  # noqa: BLE001 - nothing recorded
        return {"ok": False, "outcome": "error", "line": HELD_LINE, "seq": None}
    return {"ok": got["outcome"] in ("applied", "already"), "outcome": got["outcome"],
            "line": HELD_REASONS[why], "seq": got.get("seq")}


def meeting_fence(workspace_root, *, ref: Any, meeting_id: Any, to_person_ids: List[str], subject: Any, body: Any,
                  meeting_key: Any = "", email_id: Any = "", title: Any = "", start: Any = "",
                  calendar_event_id: Any = "") -> Optional[str]:
    """READ. The meeting fence `record_draft` keeps, for a draft written
    elsewhere (the page's own drafts, cr1#1812, ruling 135a; agreed with
    that slice on #1813 and #1814): None when a follow-up for the processed
    meeting `ref` names, to these people, with these words, may be kept;
    else the refusal line. The once-per-email check is not run (a page
    redraft replaces the draft before it), and nothing is written."""
    res = record_draft(workspace_root, meeting_id=str(meeting_id or ""), ref=str(ref or ""),
                       to_person_ids=list(to_person_ids or []), subject=str(subject or ""), body=str(body or ""),
                       start=start, title=str(title or ""), meeting_key=meeting_key, email_id=email_id,
                       calendar_event_id=calendar_event_id, check_only=True)
    return None if res.get("outcome") == "ok" else str(res.get("line") or HELD_LINE)


def _people(root: Path) -> Dict[str, Dict[str, Any]]:
    try:
        from entities_io import entities_collection, read_entities_json
        from people_writer import get_person_emails

        people = entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"), "people")
    except Exception:  # noqa: BLE001
        return {}
    return {str(p.get("id")): {"name": str(p.get("canonical_name") or ""), "emails": get_person_emails(p)}
            for p in people if isinstance(p, dict) and p.get("id")}


#: the plans listed in the feed (the newest), for the meeting page
PLAN_FEED_CAP = 20


def feed_followups(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. The Hub's feed/followups doc (ruling 128h: one row per draft):
    the newest FEED_CAP follow-ups still waiting, each `{draft_id,
    meeting_key, meeting_id, ref, start, title, email_id, kind, to: [{id,
    name, email}], subject, body, voice, voice_block_sha256, state,
    created_at}`, newest first, checked against the wall as it stands now:
    a draft to anyone hidden, one whose meeting has someone hidden on it,
    one whose words name someone hidden, a recipient no longer on the
    meeting, or a recipient with no address, is left out and counted in
    `n_held`; so is a draft to someone the owner's plan has since taken off,
    and a draft for a chosen email whose recipients the plan has changed
    since (cr1#1787). A draft a skip rule now matches is left out too
    (`n_skipped`). A draft is gone once its meeting is marked followed up
    (a mark naming no draft, as every page before v6.12 writes) or the
    draft itself is (a mark naming its `draft_id`). `plans` lists the
    owner's plans, newest first (`{meeting_key, meeting_id, emails:
    [{email_id, kind, instruction, to: [{id, name}]}], removed: [person
    ids], at}`), naming nobody hidden. The bodies live here only, never in
    the plate or the today feed. `ok: False` (nothing listed) when the
    wall, the rules, the plans or the book cannot be read."""
    import hub_meetplan

    root = _root(workspace_root)
    out: Dict[str, Any] = {"ok": False, "drafts": [], "plans": [], "n_held": 0, "n_skipped": 0, "n_more": 0,
                           "n_retired": 0, "held": [], "meetings": [], "retired": [], "generated_at": _iso_now(now_iso)}
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
        store, unread = hub_meetplan.load_store(root)
        if unread:
            # review 4 of #1814 (should-fix): the readers of this feed say what could not be read
            return dict(out, reason="plans_unread")
        owner = _owner(root)
        evs = _events(root)
        index = hub_meeting.index_by_ref(evs)
        people = _people(root)
        rows = sorted(drafts_by_key(evs).values(), key=lambda d: d["seq"] if isinstance(d.get("seq"), int) else -1, reverse=True)
        n_held = n_skipped = n_more = n_retired = 0
        retired: List[Dict[str, Any]] = []
        draft_holds: Dict[str, Dict[str, Any]] = {}
        # the 14-day draft window and its once-said late line, for held rows and draft holds alike
        since = (_dt.datetime.fromisoformat(_iso_now(now_iso).replace("Z", "+00:00"))
                 - _dt.timedelta(days=REDRAFT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        gone = (_dt.datetime.fromisoformat(_iso_now(now_iso).replace("Z", "+00:00"))
                - _dt.timedelta(days=REDRAFT_DAYS + LATE_LINE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # review of #1814: an email marked drafted or sent on any of its drafts
        # is done, so a redraft of a sent email never lists as waiting again
        done_emails = sent_emails(evs, index)
        for d in rows:
            # a page-written draft for an item, not a meeting, is another feed's (#1813)
            if str(d.get("about") or "meeting") == "item":
                continue
            did = draft_id_of(d.get("ref"), d.get("email_id"), d.get("seq"))
            whole, marked = follow_marks(evs, d.get("ref"), index)
            if whole or did in marked or (_ref_key(d.get("ref")), str(d.get("email_id") or "")) in done_emails:
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
            # a draft that failed its voice check is never shown as ready (cr1#1803)
            if str(d.get("check_verdict") or "") == "fail":
                n_held += 1
                continue
            to_ids = [str(p) for p in d.get("to_person_ids") or []]
            tail = _ref_tail(d.get("ref"))
            plan, state, why = _resolve(store, facts, d.get("meeting_key"), d.get("meeting_id"),
                                        _start_of(d.get("start"), facts), d.get("ref"), "", _ws_tz(root))
            plan = plan or {}
            if state == "unsure":   # review 2 of #1814: never read as "no plan"
                # sixth review of #1814 (should-fix): a draft written by default and held by a later read
                # gets its hold row and line, never just a count (every hold says why); seventh check
                # (should-fix): past the 14-day window it says once that it will not be drafted, then drops
                dts = str(d.get("ts") or "")
                if dts < gone:
                    continue
                n_held += 1   # eighth check of #1814: a hold dropped after the window is not counted either
                draft_holds.setdefault(_ref_key(d.get("ref")), {
                    "ref": d.get("ref") or "", "meeting_key": hub_meetplan.norm_key(d.get("meeting_key")),
                    "meeting_id": d.get("meeting_id") or "", "start": d.get("start") or "",
                    "reason": why or "plan_unsure", "state": "held", "draft_id": did,
                    "line": HELD_REASONS.get(why or "plan_unsure", "")})
                if dts < since:
                    draft_holds[_ref_key(d.get("ref"))].update(state="ended", line=HELD_LATE_LINE)
                continue
            eid = str(d.get("email_id") or "")
            if eid:
                # cr1#1787: a chosen email is held unless the plan still sends
                # exactly these people that email; the people it names count
                # as on the meeting for it (ruling 125e), and nobody else does
                chosen = hub_meetplan.email_of(plan, eid)
                if chosen is None:
                    # review 2 of #1814 (should-fix): the owner's own change took
                    # that email out of the plan; it is retired, not held
                    n_retired += 1
                    continue
                if not matches_now(d, to_ids, chosen["kind"], chosen["instruction"]) or set(chosen["to"]) != set(to_ids):
                    n_held += 1
                    continue
                facts = dict(facts, people=facts["people"] | set(chosen["to"]))
            # cr1#1787: the automatic draft steps aside for anyone the owner chose an email for
            elif set(to_ids) & chosen_people(plan):
                n_held += 1
                continue
            # Ruled into #1772: a recipient now off limits, or no longer on the
            # meeting's rows, holds the draft (its words were written for them).
            if any(pid in hidden or pid not in facts["people"] for pid in to_ids):
                n_held += 1
                continue
            # cr1#1787: a person the owner took off never comes back; rule R2 of #1814: a waiting
            # draft to them (one drafted by default before the plan) is retired, with a line
            if set(to_ids) & hub_meetplan.removed_ids(plan):
                n_retired += 1
                retired.append({"ref": d.get("ref") or "", "draft_id": did, "line": RETIRED_LINE})
                continue
            # review of #1814, must-fix 1: the automatic draft is shown only while
            # it goes to exactly whom it should now; otherwise it is written again
            if not eid and (has_plan(plan) or d.get("planned") is True) \
                    and set(to_ids) != automatic_wanted(facts, plan, hidden, owner):
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
            if len(out["drafts"]) >= FEED_CAP:
                n_more += 1   # review of #1814: the overflow past the cap is counted
                continue
            vo, vsha = _voice(d.get("voice"), d.get("voice_block_sha256"), d.get("voice_source"),
                              d.get("voice_rules_sha256"))
            vsrc, vrules = voice_pair(d.get("voice_source"), d.get("voice_rules_sha256"))
            kind = d.get("kind") if d.get("kind") in hub_meetplan.KINDS else hub_meetplan.DEFAULT_KIND
            out["drafts"].append({"meeting_id": d.get("meeting_id") or "", "ref": d.get("ref") or "",
                                  "start": d.get("start") or "", "title": d.get("title") or "",
                                  "to": to, "subject": d.get("subject") or "", "body": d.get("body") or "",
                                  "draft_id": did, "meeting_key": hub_meetplan.norm_key(d.get("meeting_key")),
                                  "email_id": eid, "kind": kind, "voice": vo, "voice_block_sha256": vsha,
                                  "voice_source": vsrc, "voice_rules_sha256": vrules,
                                  "state": STATE_WAITING, "created_at": d.get("ts") or ""})
        plans, n_plan_held = _feed_plans(store, hidden, hw, people)
        # cr1#1787 part C: the holds standing (GAP 2), said in plain words, and the
        # recent written-up meetings' people for the plan editor (GAP 1)
        held = []
        stands = hold_stands(store, index, mails, owner, _ws_tz(root))
        for k, h in sorted(held_rows(evs, store).items(), key=lambda kv: -kv[1]["seq"]):
            if follow_marks(evs, h.get("ref"), index)[0]:
                continue
            if str(h.get("ts") or "") < gone:
                continue   # rule R2: said once past the 14-day window, then dropped
            row = {"ref": h.get("ref") or "", "meeting_key": hub_meetplan.norm_key(h.get("meeting_key")),
                   "meeting_id": h.get("meeting_id") or "", "start": h.get("start") or "",
                   "reason": h.get("reason") or "", "state": "held",
                   "line": HELD_REASONS.get(str(h.get("reason") or ""), "")}
            if str(h.get("ts") or "") < since and stands(h):
                row.update(state="ended", line=HELD_LATE_LINE)   # rule R2: never drafted now
            elif not stands(h):
                # review 5 of #1814: the hold has ended; say what happens now, until it is drafted
                got = drafts_wanted(root, ref=h.get("ref"), meeting_id=h.get("meeting_id"),
                                    meeting_key=h.get("meeting_key"), start=h.get("start"))
                if got.get("reason") in ENDED_WHY_LINES:
                    row.update(state="ended", line=ENDED_WHY_LINES[got["reason"]])
                elif not got.get("ok") or not got.get("emails"):
                    row.update(state="ended", line=ENDED_NOTHING_LINE)
                elif str(h.get("ts") or "") < since:
                    row.update(state="ended", line=ENDED_LATE_LINE)
                else:
                    row.update(state="released", line=RELEASED_LINE)
            held.append(row)
        listed = {_ref_key(h["ref"]) for h in held}
        held += [h for k, h in draft_holds.items() if k not in listed]
        out.update(ok=True, plans=plans, n_held=n_held + n_plan_held, n_skipped=n_skipped, n_more=n_more,
                   n_retired=n_retired, held=held[:PLAN_FEED_CAP], retired=retired[:PLAN_FEED_CAP],
                   meetings=_feed_meetings(evs, index, mails, owner, hidden, hw, people, now_iso))
    except Exception:  # noqa: BLE001 - fail closed: nothing listed
        out.update(ok=False, drafts=[], plans=[], n_held=0, n_skipped=0, n_more=0, n_retired=0, held=[], meetings=[],
                   retired=[])
    return out


def sent_emails(evs: List[dict], index: Dict[str, List[dict]]) -> set:
    """(ref key, email id) for every email one of whose drafts a page press
    marked drafted or sent (`draft_id`). Pure."""
    from event_seq import event_seq

    out: set = set()
    marks: Dict[str, set] = {}
    for e in evs:
        if not (isinstance(e, dict) and e.get("type") == EVENT and isinstance(e.get("data"), dict)):
            continue
        ref = e["data"].get("ref")
        k = _ref_key(ref)
        if not k:
            continue
        if k not in marks:
            marks[k] = follow_marks(evs, ref, index)[1]
        eid = str(e["data"].get("email_id") or "")
        if draft_id_of(ref, eid, event_seq(e)) in marks[k]:
            out.add((k, eid))
    return out


def _feed_plans(store: Dict[str, Any], hidden: set, hw: Dict[str, Any],
                people: Dict[str, Dict[str, Any]]) -> tuple:
    """(the plans for the page, how many entries were held). An email to
    someone hidden now, or whose instruction names someone hidden, is left
    out whole; a hidden person taken off is not listed. Names nobody
    hidden. Pure."""
    import hub_meetplan

    recs = [r for r in store.get("meetings") or [] if isinstance(r, dict)]
    recs.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    plans, held = [], 0
    for rec in recs[:PLAN_FEED_CAP]:
        mails = []
        for e in hub_meetplan.emails(rec):
            if any(p in hidden for p in e["to"]) or names_hidden([e["instruction"]], hw):
                held += 1
                continue
            # sixth review of #1814 (MUST 3): an email the first page press copied from the meeting's Today
            # plan says where it came from, as the seeded removals do
            src = next((x.get("seeded_from") for x in rec.get("emails") or [] if isinstance(x, dict)
                        and str(x.get("email_id") or "") == e["email_id"] and isinstance(x.get("seeded_from"), dict)), None)
            mails.append({"email_id": e["email_id"], "kind": e["kind"], "instruction": e["instruction"],
                          "to": [{"id": p, "name": (people.get(p) or {}).get("name") or ""} for p in e["to"]],
                          "seeded": ({"from_start": str(src.get("start") or ""),
                                      "from_meeting_id": str(src.get("meeting_id") or "")} if src else None)})
        plans.append({"meeting_key": hub_meetplan.norm_key(rec.get("key")),
                      "meeting_id": hub_meetplan.norm_meeting_id(rec.get("meeting_id")),
                      # cr1#1787 part C: the occurrence, so the page finds this one's plan
                      "start": hub_meetplan.norm_start(rec.get("start")), "emails": mails,
                      "removed": sorted(p for p in hub_meetplan.removed_ids(rec) if p not in hidden),
                      # rule R2 of #1814: the write-up the plan is for, and each removal its first press
                      # carried over from another plan, with the plan it came from (shown on the held page)
                      "ref": hub_meetplan.norm_ref(rec.get("ref")),
                      "seeded": [{"id": hub_meetplan._pid(r.get("person_id")),
                                  "name": (people.get(hub_meetplan._pid(r.get("person_id"))) or {}).get("name") or "",
                                  "from_start": str((r.get("seeded_from") or {}).get("start") or ""),
                                  "from_meeting_id": str((r.get("seeded_from") or {}).get("meeting_id") or "")}
                                 for r in rec.get("removed") or [] if isinstance(r, dict) and not r.get("back_at")
                                 and isinstance(r.get("seeded_from"), dict)
                                 and hub_meetplan._pid(r.get("person_id")) not in hidden],
                      "at": str(rec.get("at") or "")})
    return plans, held


def _feed_meetings(evs: List[dict], index: Dict[str, List[dict]], mails: Dict[str, str], owner: str,
                   hidden: set, hw: Dict[str, Any], people: Dict[str, Dict[str, Any]],
                   now_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """cr1#1787 part C (coordinator, GAP 1): the written-up meetings of the
    last MEETINGS_FEED_DAYS, newest first, at most MEETINGS_FEED_CAP, each
    `{ref, people: [{id, name}], editable}` for the meeting page's follow-up
    choices. Nobody off limits or with a personal tie is in it. A meeting with
    anyone hidden on it, or whose title or outside names name someone hidden
    (People's title_off), carries no people and is not editable; so is a
    personal or solo one. Pure."""
    try:
        now = _dt.datetime.fromisoformat(_iso_now(now_iso).replace("Z", "+00:00"))
    except ValueError:
        now = _dt.datetime.now(_dt.timezone.utc)
    since = (now - _dt.timedelta(days=MEETINGS_FEED_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for e in reversed(evs):
        if not (isinstance(e, dict) and e.get("type") == "meeting_processed" and isinstance(e.get("data"), dict)):
            continue
        ref = str(e["data"].get("source_ref") or e.get("source_ref") or "")
        k = _ref_key(ref)
        if not k or k in seen or str(e.get("ts") or "") < since:
            continue
        seen.add(k)
        f = meeting_facts(ref, index, mails, owner)
        walled = bool(f["people"] & hidden) or names_hidden(f["external"] + f["titles"], hw)
        editable = f["has_row"] and f["calendar"] == "business" and not f["solo"] and not walled
        who = [] if not editable else sorted(
            ({"id": p, "name": (people.get(p) or {}).get("name") or ""} for p in f["people"] if p not in hidden),
            key=lambda x: (x["name"].lower(), x["id"]))
        out.append({"ref": ref, "people": who, "editable": bool(editable)})
        if len(out) >= MEETINGS_FEED_CAP:
            break
    return out


def _iso_now(now_iso: Optional[str] = None) -> str:
    try:
        dt = _dt.datetime.fromisoformat(str(now_iso).replace("Z", "+00:00")) if now_iso else None
    except ValueError:
        dt = None
    dt = dt or _dt.datetime.now(_dt.timezone.utc)
    dt = dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def drafts_wanted(workspace_root, *, ref: Any, meeting_id: Any = "", meeting_key: Any = "",
                  start: Any = "", calendar_event_id: Any = "") -> Dict[str, Any]:
    """READ. The seam the one-meeting write-up reads before it drafts
    (cr1#1787; the `writeup` job wires it): the emails to draft for the
    processed meeting `ref` names, each `{email_id, to, kind,
    instruction}`: the owner's chosen emails (a person taken off or hidden
    is never on one), then the automatic run's one draft (`email_id` "")
    to the people on the meeting other than the owner who are on no chosen
    email, less anyone taken off or hidden. An email already drafted is in
    `drafted` instead, only while it still matches the plan (the same people
    and, for a chosen email, the same kind and own words), or once it was put
    in the mailbox or sent. `ok: False` (draft nothing) when the plan, the
    wall or the book cannot be read, or the meeting is not written up; with
    `line` PLAN_UNSURE_LINE when a plan may be this meeting's and none is
    exactly (review 2 of #1814). Writes nothing."""
    import hub_meetplan

    root = _root(workspace_root)
    out: Dict[str, Any] = {"ok": False, "emails": [], "drafted": []}
    try:
        import hub_meeting

        hidden = _hidden(root)
        mails = _email_index(root)
        if hidden is None or mails is None:
            return out
        owner = _owner(root)
        evs = _events(root)
        index = hub_meeting.index_by_ref(evs)
        facts = meeting_facts(ref, index, mails, owner)
        if not facts["processed"] or not facts["has_row"]:
            return out
        # review 4 of #1814 (should-fix): a meeting that is never followed up (personal,
        # no calendar, solo, or with someone off limits on it) says so, never held
        hw = hidden_words(root)
        if hw is None:
            return out
        why = ("personal" if facts["calendar"] == "personal" else "no_calendar" if facts["calendar"] != "business"
               else "solo" if facts["solo"]
               else "walled" if facts["people"] & hidden or names_hidden(facts["external"], hw) else "")
        if why:
            return dict(out, reason=why)
        said: Dict[str, Any] = {}
        plan, state, why = _plan_state(root, calendar_key(calendar_event_id, meeting_key), meeting_id,
                                       _start_of(start, facts), ref, calendar_event_id, facts, said)
        if state == "unread":
            return out
        # rule R2 of #1814: which rule decided, for the run's receipt (numbers only there)
        out["rule"] = said.get("rule") or ""
        if state == "unsure":
            return dict(out, line=HOLD_LINES.get(why) or PLAN_UNSURE_LINE, held=True, reason=why or "plan_unsure")
        whole, _ids = follow_marks(evs, ref, index)
        done = sent_emails(evs, index)
        have = drafts_by_key(evs)
        gone = hub_meetplan.removed_ids(plan)
        chosen = hub_meetplan.emails(plan)
        on = chosen_people(plan)
        # the owner's chosen emails, then the automatic one to everyone else on the meeting
        wanted = chosen + [{"email_id": "", "kind": hub_meetplan.DEFAULT_KIND, "instruction": "",
                            "to": sorted(p for p in facts["people"] if p not in on)}]
        for e in wanted:
            to = [p for p in e["to"] if p not in hidden and p not in gone and p != owner]
            if not to:
                continue
            prev = have.get((_ref_key(ref), e["email_id"]))
            # review of #1814, must-fix 1: drafted only while it matches the plan now;
            # an email already put in the mailbox or sent is never drafted again
            if (prev and (whole or (_ref_key(ref), e["email_id"]) in done)) \
                    or current(prev, plan, to, e if e["email_id"] else None):
                out["drafted"].append(e["email_id"])
                continue
            out["emails"].append(dict(e, to=to))
        out["ok"] = True
    except Exception:  # noqa: BLE001 - fail closed: nothing to draft
        out.update(ok=False, emails=[], drafted=[])
    return out


def redrafts_due(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. The meetings whose follow-up the owner's plan changed after it
    was drafted (review 2 of #1814, must-fix 1): `{ok, meetings: [{ref,
    meeting_id, meeting_key, start, title, emails}]}`, newest first, at most
    REDRAFT_CAP, each with what `drafts_wanted` lists for it now. Only a
    meeting with a draft kept in the last REDRAFT_DAYS, not yet followed up
    as a whole. The automatic run drafts what is listed, the same way it
    drafts a meeting it has just written up. `ok: False` lists nothing.
    Writes nothing."""
    root = _root(workspace_root)
    out: Dict[str, Any] = {"ok": False, "meetings": []}
    try:
        import hub_meeting

        evs = _events(root)
        index = hub_meeting.index_by_ref(evs)
        try:
            now = _dt.datetime.fromisoformat(_iso_now(now_iso).replace("Z", "+00:00"))
        except ValueError:
            now = _dt.datetime.now(_dt.timezone.utc)
        since = (now - _dt.timedelta(days=REDRAFT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = sorted(drafts_by_key(evs).values(), key=lambda d: d["seq"] if isinstance(d.get("seq"), int) else -1,
                      reverse=True)
        # review 4 of #1814: a meeting held and never drafted is listed once its hold is
        # gone (the owner's Undo, a binding arriving, the plan changing), with the hold's
        # own ids and start; one still in doubt is not
        import hub_meetplan

        store, unread = hub_meetplan.load_store(root)
        mails = _email_index(root)
        if not unread and mails is not None:
            stands = hold_stands(store, index, mails, _owner(root), _ws_tz(root))
            for k, h in held_rows(evs).items():
                if str(h.get("ts") or "") < since or stands(h):
                    continue
                rows.append({"ref": h.get("ref"), "meeting_id": h.get("meeting_id"), "meeting_key": h.get("meeting_key"),
                             "start": h.get("start"), "title": "", "seq": h["seq"], "ts": h.get("ts")})
            rows.sort(key=lambda d: d["seq"] if isinstance(d.get("seq"), int) else -1, reverse=True)
        seen: set = set()
        for d in rows:
            k = _ref_key(d.get("ref"))
            if not k or k in seen or str(d.get("about") or "meeting") == "item" or str(d.get("ts") or "") < since:
                continue
            seen.add(k)
            if follow_marks(evs, d.get("ref"), index)[0]:
                continue
            got = drafts_wanted(root, ref=d.get("ref"), meeting_id=d.get("meeting_id"),
                                meeting_key=d.get("meeting_key"), start=d.get("start"))
            if got.get("ok") and got.get("emails"):
                out["meetings"].append({"ref": d.get("ref") or "", "meeting_id": d.get("meeting_id") or "",
                                        "meeting_key": d.get("meeting_key") or "", "start": d.get("start") or "",
                                        "title": d.get("title") or "", "emails": got["emails"]})
                if len(out["meetings"]) >= REDRAFT_CAP:
                    break
        out["ok"] = True
    except Exception:  # noqa: BLE001 - fail closed: nothing listed
        out.update(ok=False, meetings=[])
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
