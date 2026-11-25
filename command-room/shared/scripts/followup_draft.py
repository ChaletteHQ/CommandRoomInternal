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


def hidden_words(workspace_root) -> Optional[Dict[str, Any]]:
    """Every way a hidden person is written (`hub_work.hidden_people`: full
    name, first name, aliases) and their addresses, for screening a draft's
    words. None when who is hidden cannot be read (nothing is kept or
    listed then)."""
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
    except Exception:  # noqa: BLE001 - fail closed
        return None
    return {"names": {f for f in (_flat(n) for n in hp.get("names") or ()) if f},
            "shown": {f for f in (_flat(n) for n in hp.get("shown_full") or ()) if f},
            "emails": mails}


def names_hidden(texts: List[Any], hw: Dict[str, Any]) -> bool:
    """True when any of `texts` names someone hidden: one of their names as
    a whole word or phrase, or one of their addresses. The whole name of
    someone on file who is not hidden is theirs (cr1#1733) and is set aside
    first. Pure."""
    raw = _plain(" | ".join(str(t or "") for t in texts)).casefold()
    if any(m in raw for m in hw.get("emails") or ()):
        return True
    flat = " " + _flat(raw) + " "
    for s in sorted(hw.get("shown") or (), key=len, reverse=True):
        flat = flat.replace(" " + s + " ", "  ")
    return any((" " + n + " ") in flat for n in hw.get("names") or ())


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
    if names_hidden([subject, body, title] + facts["titles"], hw):
        return no(NAMED_LINE)
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
