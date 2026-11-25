#!/usr/bin/env python3
"""hub_meeting.py: a meeting's live prep page on the Hub, saved (cr1#1632
slice 1, design E1).

WHY THIS EXISTS
The owner goes through a meeting's brief on the Hub while the meeting runs:
he picks an option on each decision, ticks the questions he asked and types
what was said, and ticks the points he covered. All of that stays in the
page's own store until he presses "Meeting done, save this". That press is
one choice (`kind` `meeting`, `action` `save`); Apply writes it here, through
the writers that already own each record, and one Undo (`action` `undo`)
takes the whole save back.

  * a picked decision:   a `decision` row in decision-log's own shape
                         (`meeting_capture.build_decision_event`, source
                         `decision-log`), through the one append path;
  * a question answered: one fact on the person the meeting is with
                         (`people_writer.record_person_fact`, the owner's
                         own word, sourced to the press), as the Hub's Add
                         context on a person does;
  * a point covered:     kept on the save's own row; it closes nothing;
  * what is left:        the decisions not picked, the questions not
                         answered and the points not covered, kept on the
                         save's own row as `carried`, which the next brief
                         with that person reads (`carried_points`).

Each save appends ONE `hub_meeting_saved` row naming what it wrote. The
Undo is judged against that row: each fact it wrote is retracted through the
registered reverser (`brain_undo.REVERSERS["entity_fact_structured"]`), what
it carried no longer carries, and a second `hub_meeting_saved` row records
the Undo. The decisions it recorded STAY: a decision has no reverser, and
marking one `decision_superseded` with nothing in its place is forbidden
(meeting-notes, the 2026-09-07 rule; brain_undo LEDGERFENCE1), so the Undo's
line names them as kept. A save already applied answers from its first row.

The off-limits wall holds: a person the owner put off limits is never named
on a decision, never given a fact, and never carries a point; the save says
so in general words and names nobody.

Nothing here sends anything. stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

EVENT = "hub_meeting_saved"
SOURCE_SKILL = "cockpit"
DECISION_SOURCE = "decision-log"
REF = "user:hub_meeting"
FACT_CATEGORY = "other"
FACT_UNDO_REASON = "Meeting save taken back on the Command Room Hub"
#: The caps on what one save carries (the page sends no more; a longer list is cut).
LIST_CAP = 30
TEXT_CAP = 300
TITLE_CAP = 200

SAVED_LINE = "Saved {what}. {carry}"
NOTHING_LINE = "Nothing was picked, ticked or answered, so nothing was saved."
CARRY_LINE = "{n} carried to the next brief."
NO_CARRY_LINE = "Nothing is left to carry."
NO_PERSON_LINE = ("The answers stay with the meeting: it is not clear which one person said them, "
                  "so none was added to a person.")
WALLED_LINE = ("Someone on this meeting is off limits, so the answers and what is left were not saved; "
               "your decisions were.")
WALL_UNREAD_LINE = ("The off-limits list could not be read, so the answers and what is left were not saved; "
                    "your decisions were.")
PERSONAL_LINE = "A meeting on your personal calendar is not saved from the Hub; nothing was written."
GONE_LINE = "That save is not on your book; nothing was written."
UNDONE_LINE = "That save was already taken back; nothing was written."
UNDO_LINE = "Took back the save for {title}."
DECISIONS_KEPT_LINE = ("The {n} you picked {stay} on your book: a decision gives way only to a newer one, "
                       "so record the new one to change it.")
UNDO_FAILED_LINE = "The save for {title} could not be taken back in full; say undo in a chat."
SAVE_FAILED_LINE = ("The save for {title} did not finish; what it wrote before it stopped stays on your book. "
                    "Say undo in a chat to take it back.")
ALREADY_LINE = ("This meeting is already saved; nothing more was written. "
                "Take that save back first to save it again.")
NOBODY_CARRY_LINE = "Nothing carries: nobody on this meeting is on file, so no next brief would read it."
NO_MEETING_LINE = "That press names no meeting; nothing was written."
UNKNOWN_LINE = "That is not something the meeting page saves; nothing was written."


def _events_path(root: Path) -> Path:
    return root / "_hq" / "data" / "events.jsonl"


def _events(root: Path) -> List[dict]:
    from events_io import load_events_owner_scoped
    return list(load_events_owner_scoped(str(root))[0])


def _clean(value: Any, cap: int = TEXT_CAP) -> str:
    return " ".join(str(value or "").split())[:cap]


def _list(value: Any, cap: int = LIST_CAP) -> List[Any]:
    return list(value)[:cap] if isinstance(value, list) else []


def _saves(evs: List[dict]) -> List[dict]:
    return [e for e in evs if isinstance(e, dict) and e.get("type") == EVENT and isinstance(e.get("data"), dict)]


#: cr1#1694, review: the failed-Undo wording as rows written before the
#: `failed` marker carry it, frozen so a later change to UNDO_FAILED_LINE
#: never makes an older failed row read as a take-back that held.
_OLD_UNDO_FAILED = "The save for {title} could not be taken back in full;"


def _undo_failed(d: dict) -> bool:
    """cr1#1694: an Undo row whose take-back failed (a fact could not be
    removed). Marked `failed` since; an older row is known by its line."""
    if d.get("failed") is True:
        return True
    title = str(d.get("title") or "the meeting")
    return str(d.get("line") or "").startswith(_OLD_UNDO_FAILED.format(title=title))


def _retracted(events: List[dict]) -> set:
    """The fact seqs a retraction row takes back (`entity_fact_retracted`)."""
    out = set()
    for e in events or []:
        if isinstance(e, dict) and e.get("type") == "entity_fact_retracted":
            try:
                out.add(int((e.get("data") or {}).get("retracts_seq")))
            except (TypeError, ValueError):
                continue
    return out


def _undone(saves: List[dict], retracted: Optional[set] = None) -> set:
    """The saves taken back. cr1#1694: one whose Undo failed still stands
    (its facts may still be on the person), so a fresh save answers already
    and the feed keeps it, with the failure, until a take-back holds: a
    retry from the page, or every fact the save wrote retracted since (an
    undo in a chat writes the retractions, never a Hub Undo row)."""
    gone = retracted or set()
    seqs = {str(e["data"].get("hub_choice")): e["data"].get("fact_seqs") or []
            for e in saves if not e["data"].get("undoes_choice")}

    def _held(d: dict) -> bool:
        if not _undo_failed(d):
            return True
        try:
            return all(int(q) in gone for q in seqs.get(str(d.get("undoes_choice")), []))
        except (TypeError, ValueError):
            return False
    return {str(e["data"].get("undoes_choice")) for e in saves
            if e["data"].get("undoes_choice") and _held(e["data"])}


def _failed_undo(saves: List[dict], press_id: str) -> Optional[dict]:
    """The newest failed Undo of `press_id`, or None."""
    return next((e["data"] for e in reversed(saves) if str(e["data"].get("undoes_choice") or "") == press_id
                 and _undo_failed(e["data"])), None)


def _off_limits(root: Path) -> Optional[set]:
    try:
        from account_scope_gate import off_limits_person_ids
        return {str(i) for i in off_limits_person_ids(str(root))}
    except Exception:  # noqa: BLE001 - unknown: fail closed
        return None


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _ws_tz(root: Any):
    """The workspace's zone (entities.json `workspace.user_timezone`), or None."""
    try:
        from tz import load_workspace_tz
        return load_workspace_tz(str(root))
    except Exception:  # noqa: BLE001 - no zone: starts are read as written
        return None


def _when(t: str, tz: Any):
    """A start as a moment: an offset-less one is read in the workspace's zone
    (review round 3), else in UTC; None when it does not parse."""
    try:
        d = _dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=tz or _dt.timezone.utc)


def _day(t: str, tz: Any) -> str:
    """A start's calendar day in the workspace's zone (the feed sends an
    all-day start as its local midnight in UTC, which east of UTC is the day
    before); as written when there is no zone or it does not parse."""
    if _DATE_ONLY.match(t):
        return t
    d = _when(t, tz)
    return d.astimezone(tz).date().isoformat() if d is not None and tz is not None else t[:10]


def _same_start(a: Any, b: Any, tz: Any = None) -> bool:
    """Two starts name the same moment (to the minute), however spelled. A
    date-only start (an all-day meeting) matches on its day, the other start's
    day read in the workspace's zone `tz`."""
    sa, sb = str(a or "").strip(), str(b or "").strip()
    if not sa or not sb:
        return False
    if _DATE_ONLY.match(sa) or _DATE_ONLY.match(sb):
        return _day(sa, tz) == _day(sb, tz)

    def norm(t):
        d = _when(t, tz)
        return d.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M") if d is not None else t[:16]
    return norm(sa) == norm(sb)


def _receipt(evs: List[dict], meeting_id: str, start: Any = "", tz: Any = None) -> dict:
    """The meeting's newest `prep_brief` receipt for THIS occurrence (the
    calendar id is shared across a recurring series, so its `meeting_start`
    must match when both are known); else the newest for the id."""
    best, any_ = {}, {}
    for e in evs:
        d = e.get("data") if isinstance(e, dict) and isinstance(e.get("data"), dict) else {}
        if e.get("type") != "prep_brief" or str(d.get("meeting_id") or "") != meeting_id:
            continue
        any_ = d
        if not start or not d.get("meeting_start") or _same_start(d.get("meeting_start"), start, tz):
            best = d
    return best or ({} if start and any_.get("meeting_start") else any_)


def _attendees(evs: List[dict], meeting_id: str, start: Any = "", owner: str = "", tz: Any = None) -> List[str]:
    """The people the occurrence's brief was written for, the owner never
    among them (he is on every meeting; a fact or a carried point of his own
    would follow him into every other brief)."""
    d = _receipt(evs, meeting_id, start, tz)
    return [str(p) for p in d.get("attendee_person_ids") or [] if str(p).strip() and str(p) != str(owner or "")]


def _names(root: Path, ids: List[str]) -> Dict[str, str]:
    try:
        from entities_io import entities_collection, read_entities_json
        people = entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"), "people")
    except Exception:  # noqa: BLE001 - no names: nobody is matched by name
        return {}
    want = set(ids)
    return {str(p.get("id")): str(p.get("canonical_name") or "") for p in people
            if isinstance(p, dict) and str(p.get("id")) in want}


_ARROW = re.compile(r"^\s*(?:\u2192|->)\s*([^:]{1,80}):\s*")


def _who_said(q: str, people: List[str], names: Dict[str, str], sole: bool = True) -> str:
    """The one person an answer is about: the question's own "-> Name:" when
    it names exactly one of them, else the only person on the meeting (only
    when `sole`: nobody else is on it, on file or not); "" when it is not
    clear."""
    m = _ARROW.match(q or "")
    if m:
        key = m.group(1).strip().lower()
        hits = [p for p in people if names.get(p) and (names[p].lower() == key or names[p].lower().split()[0] == key)]
        return hits[0] if len(hits) == 1 else ""
    return people[0] if sole and len(people) == 1 else ""


def _append(root: Path, ev: dict) -> dict:
    from event_gate import append_event
    landed = append_event(_events_path(root), ev, holder="cockpit_helpers")
    return (landed[0] if isinstance(landed, list) and landed else {}) or {}


def apply_one(workspace_root, c: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """WRITE. One meeting-page press as `plan_apply` planned it (`kind`
    `meeting`; `action` `save` or `undo`). Answers `{outcome, line, ...}`."""
    root = Path(str(workspace_root))
    verb = str(c.get("action") or "").strip().lower()
    choice_id = str(c.get("id") or "").strip()
    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
    evs = _events(root)
    saves = _saves(evs)
    done = next((e for e in saves if str(e["data"].get("hub_choice")) == choice_id), None)
    if done is None:
        done = next((e for e in evs if isinstance(e, dict) and e.get("type") == WRAP_EVENT
                     and str((e.get("data") or {}).get("hub_choice")) == choice_id), None)
    if done is not None:
        # cr1#1694, review: the same failed Undo press again answers as it did
        # the first time (error), so the page never reads it as a take-back
        failed = bool(done["data"].get("undoes_choice")) and _undo_failed(done["data"])
        return {"outcome": "error" if failed else "applied", "line": str(done["data"].get("line") or ""), "replayed": True,
                "seq": done.get("seq")}
    if verb == "save":
        return _save(root, c, inp, choice_id, actor, evs)
    if verb == "undo":
        return _undo(root, str(inp.get("press") or "").strip(), choice_id, actor, saves)
    if verb == "wrap":
        return _wrap(root, c, inp, choice_id, actor, evs)
    if verb == "unwrap":
        return _unwrap(root, str(inp.get("press") or "").strip(), choice_id, actor, evs)
    if verb in ("skipdraft", "unskipdraft"):
        # cr1#1745: "Don't draft for meetings like this" and its Undo
        import followup_draft

        res = followup_draft.apply_skip(root, action="skip" if verb == "skipdraft" else "unskip",
                                        meeting_id=str(c.get("n") or inp.get("meeting_id") or ""),
                                        title=str(c.get("title") or inp.get("title") or ""),
                                        recurring=inp.get("recurring") is True)
        return {"outcome": res["outcome"], "line": res["line"]}
    return {"outcome": "refused", "line": UNKNOWN_LINE}


def _save(root: Path, c: dict, inp: dict, choice_id: str, actor: str, evs: List[dict]) -> Dict[str, Any]:
    meeting_id = _clean(c.get("n"), 512)
    if not meeting_id:
        return {"outcome": "refused", "line": NO_MEETING_LINE}
    if str(inp.get("calendar") or "") == "personal":
        return {"outcome": "refused", "line": PERSONAL_LINE}
    title = _clean(inp.get("title") or c.get("title"), TITLE_CAP) or "the meeting"
    start = _clean(inp.get("start"), 40)
    # review round 2, must 1: a second press (a double click, a second tab)
    # while this occurrence's save stands writes nothing; Undo it to save again.
    tz = _ws_tz(root)
    standing = saved_for(evs, meeting_id, start, tz)
    if standing:
        return {"outcome": "already", "line": ALREADY_LINE, "press": standing["press"], "meeting_id": meeting_id}
    off = _off_limits(root)
    # the people come from the occurrence's own brief, never from the page
    on_it = _attendees(evs, meeting_id, start, owner=str(actor or ""), tz=tz)
    people = [p for p in on_it if off is not None and p not in off]
    walled = off is None or len(people) < len(on_it)
    picks = [{"q": _clean(d.get("q")), "pick": _clean(d.get("pick"), 120)}
             for d in _list(inp.get("decide")) if isinstance(d, dict) and _clean(d.get("q")) and _clean(d.get("pick"))]
    answers = [] if walled else [{"q": _clean(a.get("q")), "said": _clean(a.get("said"))}
                                 for a in _list(inp.get("answers"))
                                 if isinstance(a, dict) and _clean(a.get("q")) and _clean(a.get("said"))]
    asked_only = [_clean(q) for q in _list(inp.get("asked")) if _clean(q)]
    covered = [_clean(x) for x in _list(inp.get("covered")) if _clean(x)]
    asked_carry = [_clean(x) for x in _list(inp.get("carried")) if _clean(x)]
    # review round 2, should 4: with nobody on file no next brief reads a carry
    nobody = not walled and not people
    carried = [] if walled or nobody else asked_carry
    if not (picks or answers or asked_only or covered or carried):
        return {"outcome": "refused", "line": NOTHING_LINE}
    ref = f"{REF}:{choice_id}"
    # G30: a stored source_ref is compared by its identity key, never raw
    from connector_adapters.provenance import dedup_key_of

    ref_key = dedup_key_of(ref)

    def _mine(stored: Any) -> bool:
        return bool(ref_key) and dedup_key_of(str(stored or "")) == ref_key

    rec = _receipt(evs, meeting_id, start, tz)
    thread = str(rec.get("thread_id") or "") or None
    # Save, Undo, Save: a decision this occurrence's earlier save already wrote
    # (the Undo keeps decisions) is not written again; nor is one a retry of
    # this very press wrote before its row landed.
    before = {str(dd.get("text") or "") for e in _saves(evs)
              if str(e["data"].get("meeting_id")) == meeting_id and _same_start(e["data"].get("start"), start, tz)
              for dd in e["data"].get("decisions") or []}
    before |= {_clean((e.get("data") or {}).get("summary")) for e in evs
               if isinstance(e, dict) and e.get("type") == "decision" and _mine((e.get("data") or {}).get("source_ref"))}
    decisions: List[Dict[str, Any]] = []
    from meeting_capture import build_decision_event

    for d in picks:
        text = f"{d['q']}: {d['pick']}"
        if text in before:
            continue
        ev = build_decision_event(text, source_ref=ref, source_skill=DECISION_SOURCE, person_ids=people,
                                  made_by=str(actor or ""), evidence=f"Picked on the Hub during {title}",
                                  primary_thread_id=thread, project_id=thread, workspace_root=str(root))
        landed = _append(root, ev)
        decisions.append({"id": str(((landed.get("data") or {}).get("id")) or ""), "seq": landed.get("seq"),
                          "text": text})
    names = _names(root, people) if answers else {}
    # a meeting with more people on it than are on file: no answer goes to the one who is
    others = inp.get("others")
    sole = not (isinstance(others, int) and not isinstance(others, bool) and others > len(on_it))
    kept: List[Dict[str, Any]] = []
    on_person: Dict[str, List[str]] = {}
    for a in answers:
        who = _who_said(a["q"], people, names, sole)
        if who:
            on_person.setdefault(who, []).append(_clean(f"In {title}: asked {a['q']} They said: {a['said']}"))
        else:
            kept.append(a)
    facts: List[int] = []
    fact_person: Dict[str, str] = {}
    if on_person:
        import people_writer as pw

        written = {(str((e.get("data") or {}).get("person_id")), _clean((e.get("data") or {}).get("fact")))
                   for e in evs if isinstance(e, dict) and e.get("type") == "person_fact_observed"
                   and _mine((e.get("data") or {}).get("source_ref"))}
        for who, lines in on_person.items():
            for fact in lines:
                if (who, fact) in written:
                    continue  # a retry of this press: written once
                try:
                    pw.record_person_fact(root, who, fact, ref, category=FACT_CATEGORY, confidence="high",
                                          source_skill=SOURCE_SKILL)
                except (KeyError, ValueError):
                    kept.append({"q": fact, "said": ""})
        for e in _events(root):
            dd = e.get("data") or {}
            if e.get("type") == "person_fact_observed" and isinstance(e.get("seq"), int) \
                    and _mine(dd.get("source_ref")):
                facts.append(int(e["seq"]))
                fact_person[str(e["seq"])] = str(dd.get("person_id") or "")
    n_answers = len(answers)
    what = ", ".join(x for x in (
        _n(len(decisions), "decision", "decisions"), _n(n_answers, "answer", "answers"),
        _n(len(covered), "point covered", "points covered")) if x) or "what was ticked"
    line = SAVED_LINE.format(what=what, carry=CARRY_LINE.format(n=len(carried)) if carried
                             else (NOBODY_CARRY_LINE if nobody and asked_carry else NO_CARRY_LINE))
    if kept:
        line += " " + NO_PERSON_LINE
    if off is None:
        line += " " + WALL_UNREAD_LINE
    elif walled:
        line += " " + WALLED_LINE
    row = _append(root, {"type": EVENT, "source_skill": SOURCE_SKILL, "person_ids": people, "data": {
        "hub_choice": choice_id, "meeting_id": meeting_id, "title": title, "start": start, "person_ids": people,
        "decisions": decisions, "fact_seqs": facts, "fact_person": ",".join(sorted(set(fact_person.values()))),
        "answers": kept, "asked": asked_only, "covered": covered, "carried": carried, "line": line}})
    if not row:
        return {"outcome": "error", "line": SAVE_FAILED_LINE.format(title=title)}
    return {"outcome": "applied", "line": line, "seq": row.get("seq"), "meeting_id": meeting_id}


def _n(n: int, one: str, many: str) -> str:
    return "" if not n else f"{n} {one if n == 1 else many}"


def _undo(root: Path, press_id: str, undo_id: str, actor: str, saves: List[dict]) -> Dict[str, Any]:
    press = next((e for e in saves if press_id and str(e["data"].get("hub_choice")) == press_id
                  and not e["data"].get("undoes_choice")), None)
    if press is None:
        return {"outcome": "refused", "line": GONE_LINE}
    evs = _events(root)
    gone = _retracted(evs)
    if press_id in _undone(saves, gone):
        return {"outcome": "already", "line": UNDONE_LINE}
    d = press["data"]
    title = str(d.get("title") or "the meeting")
    failed = False
    kept = len(d.get("decisions") or [])
    by_seq = {int(e["seq"]): str((e.get("data") or {}).get("person_id") or "") for e in evs
              if isinstance(e, dict) and e.get("type") == "person_fact_observed" and isinstance(e.get("seq"), int)}
    for seq in d.get("fact_seqs") or []:
        # cr1#1694, review: a retry after a failed Undo skips the facts an
        # earlier pass (or a chat undo) already took back
        try:
            if int(seq) in gone:
                continue
        except (TypeError, ValueError):
            pass
        try:
            import brain_undo as bu
            bu.REVERSERS["entity_fact_structured"]["reverse"](
                str(root), {"person_id": by_seq.get(int(seq), ""), "change_ref": f"seq:{seq}", "reason": FACT_UNDO_REASON},
                undone_by=actor, source_skill=SOURCE_SKILL)
        except Exception:  # noqa: BLE001
            failed = True
    line = (UNDO_FAILED_LINE if failed else UNDO_LINE).format(title=title)
    if kept:
        line += " " + DECISIONS_KEPT_LINE.format(n="decision" if kept == 1 else f"{kept} decisions",
                                                 stay="stays" if kept == 1 else "stay")
    data = {"hub_choice": undo_id, "meeting_id": str(d.get("meeting_id") or ""), "title": title,
            "undoes_choice": press_id, "line": line}
    if failed:
        data["failed"] = True   # cr1#1694: the save still stands
    row = _append(root, {"type": EVENT, "source_skill": SOURCE_SKILL, "data": data})
    return {"outcome": "error" if failed else "applied", "line": line, "seq": row.get("seq")}


def saved_for(events: List[dict], meeting_id: str, start: Any = "", tz: Any = None) -> Optional[Dict[str, Any]]:
    """READ. The occurrence's standing save, `{press, line, at}`, or None. A
    recurring meeting shares one calendar id, so the start must match too.
    cr1#1694: a save whose Undo failed still stands and carries
    `undo_failed`, that Undo's line, so the page says so after a Refresh."""
    saves = _saves(events)
    undone = _undone(saves, _retracted(events))
    for e in reversed(saves):
        d = e["data"]
        if str(d.get("meeting_id")) == str(meeting_id) and not d.get("undoes_choice") \
                and str(d.get("hub_choice")) not in undone and (not start or _same_start(d.get("start"), start, tz)):
            out = {"press": str(d.get("hub_choice") or ""), "line": str(d.get("line") or ""), "at": e.get("ts")}
            bad = _failed_undo(saves, out["press"])
            if bad:
                out["undo_failed"] = str(bad.get("line") or "")
            return out
    return None


#: How many carried points the next brief reads, newest first.
CARRIED_CAP = 12


_FROM = re.compile(r"\s*\(from [^)]*\)\s*$")


def _point(text: Any) -> str:
    return " ".join(_FROM.sub("", str(text or "")).lower().split())


def carried_points(workspace_root, person_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """READ. What the owner's meeting saves left for the next brief with any
    of `person_ids`: the decisions not made, the questions not answered and
    the points not covered, newest save first, as lines `{text, from, day}`.
    A save taken back carries nothing; a point a LATER save with those people
    covered (ticked on the page) is done and carries no more. Never writes.
    call-prep passes the lines to `prep_pipeline.assemble_prep_sections(carried=...)`."""
    root = Path(str(workspace_root))
    want = {str(p) for p in person_ids or [] if str(p).strip()}
    if not want:
        return {"lines": [], "n": 0}
    try:
        evs = _events(root)
        saves = _saves(evs)
    except Exception:  # noqa: BLE001 - an unread book carries nothing
        return {"lines": [], "n": 0}
    # review round 2, should 5: the wall as it stands now. A person put off
    # limits since a save carries nothing of it; an unread wall carries nothing.
    off = _off_limits(root)
    if off is None:
        return {"lines": [], "n": 0}
    want -= off
    undone = _undone(saves, _retracted(evs))
    live = [e for e in saves if not e["data"].get("undoes_choice") and str(e["data"].get("hub_choice")) not in undone
            and want & {str(p) for p in e["data"].get("person_ids") or []}
            and not off & {str(p) for p in e["data"].get("person_ids") or []}]
    per_save: List[List[Dict[str, Any]]] = []
    for i, e in enumerate(live):
        d = e["data"]
        later_done = {_point(x) for f in live[i + 1:] for x in (f["data"].get("covered") or [])}
        day = str(d.get("start") or e.get("ts") or "")[:10]
        per_save.append([{"text": str(text), "from": str(d.get("title") or ""), "day": day}
                         for text in d.get("carried") or [] if _point(text) not in later_done])
    # newest save first, each save's points in its own order
    out = [ln for lines in reversed(per_save) for ln in lines]
    return {"lines": out[:CARRIED_CAP], "n": len(out)}


# ---------------------------------------------------------------------------
# The wrap-up page (cr1#1632 slice 3, design E2)
# ---------------------------------------------------------------------------
#
# A past meeting Command Room processed has, on the book, the decisions and
# the promises captured from it (each row's `source_ref` is the processed
# meeting's own). The wrap-up page lists them; the owner places each of his
# own promises (Today / This week / Not mine), ticks theirs to track, marks a
# decision "Not right" (a correction, the decision row stands: a decision
# gives way only to a newer one), says what happens to the follow-up, and
# presses "Wrapped up". Apply writes that one `meeting` `wrap` choice here,
# each part through its own writer, and "Put it back" (`unwrap`) takes each
# write back through that writer's own undo.

WRAP_EVENT = "hub_meeting_wrapped"
FOLLOW_CHOICES = ("sent", "drafts", "none")
PLACE_CHOICES = ("today", "week", "notmine")
WRAP_CAP = 30
WRAPPED_LINE = "Wrapped up {title}. {what}"
WRAP_NOTHING = "Nothing was placed, tracked or corrected."
UNWRAP_LINE = "Put {title} back in the queue: each change it made is taken back."
UNWRAP_NONE_LINE = "Put {title} back in the queue. The wrap-up changed nothing, so nothing needed taking back."
UNWRAP_PART_LINE = "Put {title} back in the queue; {n} could not be taken back, because they changed again since."
UNWRAP_ONE_LINE = "Put {title} back in the queue; 1 change could not be taken back, because it changed again since."
WRAP_GONE_LINE = "That wrap-up is not on your book; nothing was written."
WRAP_UNDONE_LINE = "That wrap-up was already put back; nothing was written."
WRAP_HELD_LINE = ("I could not check the promises from {title} just now, so it was not wrapped up and nothing was written. "
                  "Try again after the next refresh.")
CORRECTION_LINE = "You said this was not right."


def _norm(text: Any) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in str(text or "")).split())


def _moment(value: Any) -> Optional[_dt.datetime]:
    """An aware moment from an ISO string (UTC when it names no zone), or None."""
    t = str(value or "").strip()
    if not t or _DATE_ONLY.match(t):
        return None
    try:
        d = _dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=_dt.timezone.utc)


def _in_occurrence(e: dict, meeting: Dict[str, Any]) -> bool:
    """A row belongs to this occurrence: written on or after the meeting's day
    (at or after its start, when both read as moments), and before the
    series' next occurrence when the feed names one (`next_start`)."""
    ts = str(e.get("ts") or "")
    start = str(meeting.get("start") or "")
    at, st = _moment(ts), _moment(start)
    if at is not None and st is not None:
        if at < st:
            return False
    elif ts[:10] < start[:10]:
        return False
    nxt = _moment(meeting.get("next_start"))
    return not (at is not None and nxt is not None and at >= nxt)


def _processed_ref(evs: List[dict], meeting: Dict[str, Any]) -> str:
    """The processed meeting's `source_ref` for a calendar meeting: its
    `meeting_processed` receipt whose pointer ends in the meeting's id, or
    else one with the same title, written for this occurrence (cr1#1668,
    `_in_occurrence`). "" when the meeting was not processed."""
    mid = str(meeting.get("meeting_id") or "")
    title = _norm(meeting.get("title"))
    by_title = ""
    for e in evs:
        if not isinstance(e, dict) or e.get("type") != "meeting_processed":
            continue
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        ref = str(d.get("source_ref") or e.get("source_ref") or "")
        if not ref or not _in_occurrence(e, meeting):
            continue
        if mid and ref.rsplit(":", 1)[-1] == mid:
            return ref
        if title and _norm(d.get("title")) == title:
            by_title = ref or by_title
    return by_title


# how far a meeting row's time may sit from the calendar's start and still be that occurrence
OCCURRENCE_SLACK_S = 12 * 3600


def _meeting_times(evs: List[dict]) -> Dict[str, _dt.datetime]:
    """The meeting's own time per pointer: the capture's `meeting` row
    (meeting_capture.build_meeting_event) carries it in `ts`."""
    out: Dict[str, _dt.datetime] = {}
    for e in evs:
        if isinstance(e, dict) and e.get("type") == "meeting" and isinstance(e.get("data"), dict):
            at = _moment(e.get("ts"))
            k = _ref_key(e["data"].get("source_ref"))
            if at is not None and k and k not in out:
                out[k] = at
    return out


def claim_receipts(evs: List[dict], meetings: List[Dict[str, Any]]) -> Dict[int, str]:
    """READ. Which listed meeting each `meeting_processed` receipt is for
    (review of #1669, items 3 and 4): `{index in meetings: source_ref}`. A
    receipt names a meeting by the pointer's id or by the same title; of the
    meetings it can name, a receipt whose capture wrote its `meeting` row is
    placed by that row's time: the free meeting starting nearest it (within
    OCCURRENCE_SLACK_S), however late the notes were written (round 3, item
    5). Without one: of the meetings that started before it was written, an
    id match wins, else the nearest free one, receipts taken newest first.
    The same notes written again (the same pointer) keep the meeting they
    first claimed. A same-titled meeting days earlier is never claimed by a
    later one's notes. Pass only the meetings that get a wrap-up."""
    cands = []
    for i, m in enumerate(meetings):
        st = _moment(m.get("start"))
        if st is not None:
            cands.append((i, st, str(m.get("meeting_id") or ""), _norm(m.get("title"))))
    # round 3, item 5: the capture's own `meeting` row carries the meeting's
    # time; a receipt with one is placed by it, nearest start first
    held_at = _meeting_times(evs)
    first: Dict[str, tuple] = {}   # a pointer written again keeps its first receipt's time
    for e in evs:
        if not isinstance(e, dict) or e.get("type") != "meeting_processed":
            continue
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        ref = str(d.get("source_ref") or e.get("source_ref") or "")
        at = _moment(e.get("ts"))
        if ref and at is not None:
            key = _ref_key(ref)
            if key not in first or at < first[key][0]:
                first[key] = (at, ref, _norm(d.get("title")))
    out: Dict[int, str] = {}
    timed = [r for r in first.values() if held_at.get(_ref_key(r[1])) is not None]
    placed: set = set()
    for at, ref, title in sorted(timed, key=lambda r: r[0]):
        when = held_at[_ref_key(ref)]
        rid = ref.rsplit(":", 1)[-1]
        fit = sorted((abs((st - when).total_seconds()), i) for i, st, mid, t in cands
                     if i not in out and ((mid and rid == mid) or (title and t == title)))
        if fit and fit[0][0] <= OCCURRENCE_SLACK_S:
            out[fit[0][1]] = ref
            placed.add(_ref_key(ref))
    # the rest (no meeting row, or one far from every listed occurrence,
    # round 4 item 4): newest first, each by the nearest earlier free meeting
    for at, ref, title in sorted((r for r in first.values() if _ref_key(r[1]) not in placed), key=lambda r: r[0], reverse=True):
        rid = ref.rsplit(":", 1)[-1]
        by_id = sorted(((st, i) for i, st, mid, _t in cands if st <= at and mid and rid == mid), reverse=True)
        by_title = sorted(((st, i) for i, st, _mid, t in cands if st <= at and title and t == title), reverse=True)
        for fit in (by_id, by_title):
            free = [i for _st, i in fit if i not in out]
            if free:
                out[free[0]] = ref
                break
    return out


def open_projection(workspace_root, evs: Optional[List[dict]] = None) -> Optional[Dict[str, dict]]:
    """READ. The open list as projected, `{commitment id: its projected
    event}` (review of #1669, items 1 and 10: read once per feed, and every
    field the wrap-up shows comes from here, never from the capture row).
    None when the list cannot be read."""
    root = Path(str(workspace_root))
    try:
        from cru_match import load_open_commitments

        rows = load_open_commitments(_events_path(root), events=evs) if evs is not None \
            else load_open_commitments(_events_path(root))
    except Exception:  # noqa: BLE001 - unread: None, and the wrap-up holds (round 3, item 2)
        return None
    out: Dict[str, dict] = {}
    for ev in rows or []:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = str(d.get("id") or ev.get("id") or "")
        if cid:
            out[cid] = ev
    return out


def _ref_key(stored: Any) -> str:
    """G30: a stored source_ref by its identity key, never raw. "" for none."""
    from connector_adapters.provenance import dedup_key_of

    return dedup_key_of(str(stored or "")) if stored else ""


def index_by_ref(evs: List[dict]) -> Dict[str, List[dict]]:
    """READ. The book's rows by their pointer's identity key, built once per
    feed (review of #1669, note 9): `wrap_for` and `_followed_up` read a
    meeting's rows from here instead of scanning the whole book per meeting.
    `followup_pack_drafted` rows are kept under "" too, for the ones that
    name the meeting only by `meeting_event_seq`."""
    out: Dict[str, List[dict]] = {}
    for e in evs:
        if not isinstance(e, dict):
            continue
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        k = _ref_key(d.get("source_ref"))
        if k:
            out.setdefault(k, []).append(e)
        if e.get("type") == "followup_pack_drafted":
            out.setdefault("", []).append(e)
    return out


def _tie_ids(root: Path) -> Optional[frozenset]:
    """The people with a personal tie (entities.json `tie: "personal"`).
    None when the file is there and cannot be read (review of #1669 round 3,
    item 4: `personal_tie_ids` answers an empty set then, which fails open);
    an empty set when there is no file. None too when the file parses but its
    `people` is there and not a list (cr1#1682): read as "nobody" that would
    fail open."""
    try:
        from personal_leak import people_records

        people = people_records(root)   # strict (cr1#1685): None when unreadable or `people` is not a list
    except Exception:  # noqa: BLE001
        return None
    if people is None:
        return None
    return frozenset(str(r["id"]) for r in people if isinstance(r, dict) and r.get("tie") == "personal" and r.get("id"))


def personal_fence(workspace_root) -> Optional[Dict[str, Any]]:
    """READ. The wrap-up's whole fence, read once per feed: `{ids, off, tie,
    is_personal}` -- the commitments on the personal lane and the people off
    limits, exactly as `hub_work.fence` reads them (it answers None on an
    unreadable chat wall, mail-rules store or lane); the people with a
    personal tie; and `is_personal` itself. No thread register is passed, so
    a tie-touching row is always held back (the safe direction). None when
    any of them cannot be told:
    the wrap-up then lists no promise and no decision, and the meeting is
    held, never done (rulings 66 and round 3, items 1, 4, 7)."""
    root = Path(str(workspace_root))
    try:
        import hub_work
        from personal_leak import is_personal

        fence = hub_work.fence(root)
        tie = _tie_ids(root)
    except Exception:  # noqa: BLE001
        return None
    if fence is None or tie is None:
        return None
    return {"ids": {str(i) for i in fence["personal"]}, "off": {str(i) for i in fence["off"]},
            "tie": set(tie), "is_personal": is_personal}


def _followed_up(evs: List[dict], ref: str, index: Optional[Dict[str, List[dict]]] = None) -> bool:
    """A follow-up was drafted for the processed meeting: a
    `followup_pack_drafted` row naming its `source_ref`, or pointing
    (`meeting_event_seq`) at a row that does."""
    if not ref:
        return False
    key = _ref_key(ref)
    index = index if index is not None else index_by_ref(evs)
    mine = index.get(key) or []
    seqs = {e.get("seq") for e in mine if isinstance(e.get("seq"), int)}
    for e in mine + (index.get("") or []):
        if e.get("type") != "followup_pack_drafted":
            continue
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if _ref_key(d.get("source_ref")) == key:
            return True
        try:
            if int(d.get("meeting_event_seq")) in seqs:
                return True
        except (TypeError, ValueError):
            continue
    return False


# cr1#1668: a past meeting's state on the list, in the order the list sorts by
STATES = ("unwritten", "follow_up", "promises", "held", "done")


def wrap_state(view: Dict[str, Any]) -> str:
    """Where a past meeting stands, from its `wrap_for` view: done when
    wrapped up; not written up while its notes are not captured; a
    follow-up to send while none was drafted; promises open while the
    owner's own are open; else done."""
    if view.get("wrapped"):
        return "done"
    if not view.get("processed"):
        return "unwritten"
    # ruling 66: its promises could not be checked, so it never reads done
    if view.get("held"):
        return "held"
    if not view.get("followed"):
        return "follow_up"
    if view.get("mine"):
        return "promises"
    return "done"


def _people_of(ev: dict) -> set:
    """Everyone a projected promise is about: its owner, its counterparties
    and the row's own person ids (review of #1669, item 5, as hub_work's
    fence reads an open-list row)."""
    from cru_match import _commitment_field

    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    people = {str(_commitment_field(ev, "owner_id") or "")}
    for k in ("counterparty_ids", "counterparty_id", "requester_id"):
        v = d.get(k, ev.get(k))
        people |= {str(p) for p in (v if isinstance(v, list) else [v]) if p}
    people |= {str(p) for p in ev.get("person_ids") or []}
    return people - {""}


def _personal(ev: dict, fence: Dict[str, Any]) -> bool:
    """A row on the personal lane, by the fence's own `is_personal` and its
    personal-tie people."""
    try:
        return bool(fence["is_personal"](ev, personal_ids=fence["tie"] or None))
    except Exception:  # noqa: BLE001 - a row that cannot be told is left out (fail closed)
        return True


def wrap_for(workspace_root, meeting: Dict[str, Any], evs: Optional[List[dict]] = None,
             owner_id: str = "", off: Optional[set] = None, *, ref: Optional[str] = None,
             open_rows: Optional[Dict[str, dict]] = None, tz: Any = None,
             personal: Any = False, index: Optional[Dict[str, List[dict]]] = None) -> Dict[str, Any]:
    """READ. What the wrap-up page shows for one past meeting: `{processed,
    decided[{id, text}], mine[{id, title, due}], theirs[{id, title,
    pending}], wrapped, followed, state}`. `ref` is the processed meeting's
    pointer when the caller claimed the receipts (`claim_receipts`), else it
    is looked up for this meeting alone. Only open promises, every field read
    from the projected open list (`open_rows`, else read here), never from
    the capture row: a promise handed to someone else is not his, a
    confirmed one is not pending. An item whose owner, counterparty or
    person is off limits, or in the personal lane, is left out (fail closed:
    an unread wall leaves every promise and decision out). Never writes."""
    root = Path(str(workspace_root))
    evs = evs if evs is not None else _events(root)
    if ref is None:
        ref = _processed_ref(evs, meeting)
    out: Dict[str, Any] = {"processed": bool(ref), "decided": [], "mine": [], "theirs": []}
    if tz is None:
        tz = _ws_tz(root)
    w = wrapped_for(evs, str(meeting.get("meeting_id") or ""), meeting.get("start"), tz)
    if w:
        out["wrapped"] = w
    if not ref:
        out["state"] = wrap_state(out)
        return out
    out["ref"] = ref
    index = index if index is not None else index_by_ref(evs)
    out["followed"] = _followed_up(evs, ref, index)
    # cr1#1745: a follow-up drafted after the meeting waits for the owner's press;
    # only the mark rides here, its words are in feed/followups
    # review round 1, item 4: a "don't draft for meetings like this" rule on
    # this meeting rides as `nodraft` (the page's Undo), and takes the mark away
    if not out["followed"]:
        try:
            import followup_draft

            skipped = followup_draft.is_skipped(root, meeting.get("meeting_id"), meeting.get("title"))
            if skipped is True:
                out["nodraft"] = True
            elif skipped is False and _ref_key(ref) in followup_draft.waiting_refs(evs):
                out["draft"] = True
        except Exception:  # noqa: BLE001 - no mark: the page shows the chat link as before
            pass
    if personal is False:
        personal = personal_fence(root)
    if off is None and personal is not None:
        off = personal["off"]
    if open_rows is None:
        open_rows = open_projection(root)
    if personal is None or off is None or open_rows is None:
        # the wall, the personal lane or the open list cannot be read: list
        # nothing, and say so: held, never "done" (ruling 66; round 3, items 1, 2)
        out["held"] = True
        out["state"] = wrap_state(out)
        return out
    from cru_match import _commitment_field

    seen: set = set()
    for e in index.get(_ref_key(ref)) or []:
        if e.get("type") not in ("decision", "commitment"):
            continue
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if set(str(p) for p in e.get("person_ids") or []) & off:
            continue
        if e.get("type") == "decision":
            # a decision keeps the tie check: hub_work.fence lists promises only (ruling 68)
            if _personal(e, personal):
                continue
            text = _clean(d.get("summary") or d.get("decision"))
            if text:
                out["decided"].append({"id": str(d.get("id") or e.get("seq") or ""), "text": text})
            continue
        cid = str(d.get("id") or "")
        now = open_rows.get(cid)
        if not cid or cid in seen or now is None or _people_of(now) & off \
                or cid in personal["ids"]:   # the lane exactly as the plate reads it (ruling 68)
            continue
        seen.add(cid)
        nd = now.get("data") if isinstance(now.get("data"), dict) else {}
        whose = str(_commitment_field(now, "owner_id") or "")
        if not whose:
            continue   # a "whose is this" row (one he disowned): neither his nor theirs to track (item 5)
        row = {"id": cid, "title": _clean(_commitment_field(now, "title")),
               "due": str(_commitment_field(now, "due") or "")[:10] or None}
        if owner_id and whose == owner_id:
            out["mine"].append(row)
        else:
            row["pending"] = bool(nd.get("pending_review", now.get("pending_review")))
            out["theirs"].append(row)
    for k in ("decided", "mine", "theirs"):
        out[k] = out[k][:WRAP_CAP]
    out["state"] = wrap_state(out)
    return out


def wrapped_for(events: List[dict], meeting_id: str, start: Any = "", tz: Any = None) -> Optional[Dict[str, Any]]:
    """READ. The meeting's standing wrap-up, `{press, line, at}`, or None.
    With `start`, only that occurrence's (cr1#1668: a recurring meeting's
    occurrences share one id); a wrap row with no start counts for any."""
    rows = [e for e in events if isinstance(e, dict) and e.get("type") == WRAP_EVENT and isinstance(e.get("data"), dict)]
    undone = {str(e["data"].get("undoes_choice")) for e in rows if e["data"].get("undoes_choice")}
    for e in reversed(rows):
        d = e["data"]
        if str(d.get("meeting_id")) == str(meeting_id) and not d.get("undoes_choice") \
                and str(d.get("hub_choice")) not in undone \
                and (not start or not d.get("start") or _same_start(d.get("start"), start, tz)):
            return {"press": str(d.get("hub_choice") or ""), "line": str(d.get("line") or ""), "at": e.get("ts")}
    return None


def _week_end(today: _dt.date) -> _dt.date:
    """This week's last working day: Friday, or Sunday when the day is past it."""
    wd = today.weekday()
    return today + _dt.timedelta(days=(4 - wd) if wd <= 4 else (6 - wd))


def _ref_of_occurrence(evs: List[dict], receipt: dict, stored: str, inp: dict, meeting_id: str) -> bool:
    """The receipt is for the occurrence the press names (round 3, item 6):
    its capture's meeting row is within OCCURRENCE_SLACK_S of the press's
    start. With no meeting row: a pointer naming this meeting's own id, or
    notes written within this occurrence, at or after its start and before
    the series' next one (round 4, item 1)."""
    st = _moment(inp.get("start"))
    when = _meeting_times(evs).get(_ref_key(stored))
    if when is not None:
        return st is not None and abs((when - st).total_seconds()) <= OCCURRENCE_SLACK_S
    if meeting_id and stored.rsplit(":", 1)[-1] == meeting_id:
        return True
    return st is not None and _in_occurrence(receipt, {"start": inp.get("start"), "next_start": inp.get("next_start")})


# what the page can send (round 8): the listed rows (PAST_MEETINGS_CAP 40), the hidden ones
# (PAST_HIDDEN_CAP 200) and today's list, with room; the claim over 300 entries costs nothing
OCC_CAP = 300


def _claimed_for(evs: List[dict], stored: str, inp: dict, meeting_id: str, title: str) -> bool:
    """One rule on both sides (round 5, item 1): the page sends the listed past
    occurrences sharing this meeting's id or title (`occ`), and the pointer
    stands only when `claim_receipts` over them gives it to this start, as
    the feed's claim did."""
    occ = []
    for o in inp.get("occ")[:OCC_CAP]:
        if isinstance(o, dict) and _moment(o.get("start")) is not None:
            occ.append({"meeting_id": _clean(o.get("meeting_id"), 512), "title": _clean(o.get("title"), TITLE_CAP),
                        "start": _clean(o.get("start"), 40)})
    me = [i for i, o in enumerate(occ) if o["meeting_id"] == meeting_id and _same_start(o["start"], inp.get("start"))]
    if not me:
        occ.append({"meeting_id": meeting_id, "title": title, "start": _clean(inp.get("start"), 40)})
        me = [len(occ) - 1]
    # this meeting's own entry carries the press's title, never one the list renamed it to (round 6);
    # an empty press title leaves the entry's own (rounds 7, 8)
    if title:
        occ[me[0]]["title"] = title
    got = claim_receipts(evs, occ).get(me[0])
    return bool(got) and _ref_key(got) == _ref_key(stored)


def _wrap(root: Path, c: dict, inp: dict, choice_id: str, actor: str, evs: List[dict]) -> Dict[str, Any]:
    import commitment_state as cs
    import due_set

    meeting_id = _clean(c.get("n"), 512)
    if not meeting_id:
        return {"outcome": "refused", "line": NO_MEETING_LINE}
    # round 8: titles match on the press's own title, which may be empty; "the meeting" is only words
    raw_title = _clean(inp.get("title") or c.get("title"), TITLE_CAP)
    title = raw_title or "the meeting"
    # the one fence, read once for this press (ruling 69); unread: no one is
    # named on the wrap row, and a held meeting is not wrapped up at all
    fence = personal_fence(root)
    if fence is None:   # ruling 74: the fence unread, no wrap press writes anything, pointer or not
        return {"outcome": "refused", "line": WRAP_HELD_LINE.format(title=title)}
    off = fence["off"]
    # the pointer the page showed, when it is a processed meeting on the book
    # (the feed claimed it among the listed meetings, review of #1669 item 3)
    # it stands only when its receipt names THIS meeting: the pointer's id
    # is the meeting's, or its title is (review of #1669 round 2, item 1)
    shown, ok = _clean(inp.get("ref"), 512), None
    for e in evs:
        if shown and isinstance(e, dict) and e.get("type") == "meeting_processed":
            d = e.get("data") if isinstance(e.get("data"), dict) else {}
            stored = str(d.get("source_ref") or e.get("source_ref") or "")
            if _ref_key(stored) == _ref_key(shown) and (
                    stored.rsplit(":", 1)[-1] == meeting_id or (_norm(d.get("title")) and _norm(d.get("title")) == _norm(raw_title))) \
                    and (_claimed_for(evs, stored, inp, meeting_id, raw_title) if isinstance(inp.get("occ"), list)
                         else _ref_of_occurrence(evs, e, stored, inp, meeting_id)):
                ok = stored
                break
    # a pointer the page showed and that does not stand for this occurrence
    # is no pointer: never look again by title (round 3, item 6)
    view = wrap_for(root, {"meeting_id": meeting_id, "title": raw_title, "start": inp.get("start"),
                           "next_start": inp.get("next_start")}, evs, owner_id=str(actor or ""), off=off,
                    ref=ok if ok or not shown else "", personal=fence)
    if view.get("held"):   # ruling 69: refused, nothing written
        return {"outcome": "refused", "line": WRAP_HELD_LINE.format(title=title)}
    mine = {r["id"]: r for r in view["mine"]}
    theirs = {r["id"]: r for r in view["theirs"]}
    # the workspace's own day, as every Hub date press reads it (CLOCK1)
    today = cs._now_calendar_day(cs._now_iso(), workspace_path=str(root)) or _dt.date.today()
    wrote: List[Dict[str, Any]] = []
    placed = tracked = 0
    place = inp.get("place") if isinstance(inp.get("place"), dict) else {}
    for cid, how in list(place.items())[:WRAP_CAP]:
        cid, how = str(cid), str(how or "")
        if cid not in mine or how not in PLACE_CHOICES:
            continue
        try:
            if how == "notmine":
                res = cs.disown_commitment(str(root), cid, disowned_by=str(actor or ""), source_skill=SOURCE_SKILL,
                                           reason="Not mine, said on the Hub's wrap-up")
                # only a disown that wrote is taken back by Put it back (review of #1669, item 2)
                if str((res or {}).get("status") or "") == "disowned":
                    wrote.append({"kind": "notmine", "id": cid})
                    placed += 1
            else:
                if how == "today":
                    day = today
                else:   # the Hub's one "this week" (cockpit_helpers.week_end, cr1#1657), never today
                    from cockpit_helpers import week_end
                    day = week_end(today)
                res = due_set.set_due(str(root), cid, when_iso=day.isoformat(), actor_id=str(actor or ""),
                                      source_skill=SOURCE_SKILL, reason="Placed on the Hub's wrap-up")
                seq = ((res or {}).get("event") or {}).get("seq")
                if str((res or {}).get("status") or "") == "set" and isinstance(seq, int):
                    wrote.append({"kind": "due", "id": cid, "seq": seq})
                    placed += 1
        except Exception:  # noqa: BLE001 - this one is not placed; the rest still are
            continue
    for cid in [str(x) for x in _list(inp.get("track"))]:
        row = theirs.get(cid)
        if row is None:
            continue
        if not row.get("pending"):
            continue   # already tracked: nothing to write, nothing to count
        from needs_review_queue import confirm_items
        try:
            res = confirm_items(str(root), [cid], source_skill=SOURCE_SKILL, confirm_weak_ids=[cid])
        except Exception:  # noqa: BLE001
            continue
        # one id per call, so its own count says whether it was confirmed
        if int((res or {}).get("n_confirmed") or 0) == 1:
            wrote.append({"kind": "track", "id": cid})
            tracked += 1
    decided = {r["id"]: r for r in view["decided"]}
    wrong = [{"id": str(x), "text": decided[str(x)]["text"], "note": CORRECTION_LINE}
             for x in _list(inp.get("wrong")) if str(x) in decided]
    follow = str(inp.get("follow") or "")
    follow = follow if follow in FOLLOW_CHOICES else ""
    carried = [_clean(x) for x in _list(inp.get("carried")) if _clean(x)]
    what = ", ".join(x for x in (
        _n(placed, "promise placed", "promises placed"), _n(tracked, "of theirs tracked", "of theirs tracked"),
        _n(len(wrong), "decision marked not right", "decisions marked not right"),
        _n(len(carried), "point carried", "points carried")) if x)
    line = WRAPPED_LINE.format(title=title, what=(what[0].upper() + what[1:] + ".") if what else WRAP_NOTHING)
    people = [p for p in _attendees(evs, meeting_id) if off is not None and p not in off] if off is not None else []
    row = _append(root, {"type": WRAP_EVENT, "source_skill": SOURCE_SKILL, "person_ids": people, "data": {
        "hub_choice": choice_id, "meeting_id": meeting_id, "title": title, "start": _clean(inp.get("start"), 40),
        "person_ids": people, "wrote": wrote, "wrong": wrong, "follow": follow, "carried": carried, "line": line}})
    if not row:
        return {"outcome": "error", "line": UNDO_FAILED_LINE.format(title=title)}
    return {"outcome": "applied", "line": line, "seq": row.get("seq"), "meeting_id": meeting_id}


def _unwrap(root: Path, press_id: str, undo_id: str, actor: str, evs: List[dict]) -> Dict[str, Any]:
    import commitment_state as cs
    import due_set

    rows = [e for e in evs if isinstance(e, dict) and e.get("type") == WRAP_EVENT and isinstance(e.get("data"), dict)]
    press = next((e for e in rows if press_id and str(e["data"].get("hub_choice")) == press_id
                  and not e["data"].get("undoes_choice")), None)
    if press is None:
        return {"outcome": "refused", "line": WRAP_GONE_LINE}
    if any(str(e["data"].get("undoes_choice")) == press_id for e in rows):
        return {"outcome": "already", "line": WRAP_UNDONE_LINE}
    d = press["data"]
    title = str(d.get("title") or "the meeting")
    kept = 0
    for w in reversed(d.get("wrote") or []):
        try:
            if w.get("kind") == "due":
                res = due_set.undo_set_due(str(root), int(w["seq"]), undone_by=str(actor or ""), source_skill=SOURCE_SKILL)
                # refused when the item changed again since (the date stays, and the line says so)
                kept += 1 if str((res or {}).get("status") or "") in (due_set.STATUS_UNDO_REFUSED, due_set.STATUS_NOT_RECORDED) else 0
            elif w.get("kind") == "notmine":
                res = cs.undo_disown(str(root), str(w["id"]), undone_by=str(actor or ""), source_skill=SOURCE_SKILL)
                kept += 0 if (res or {}).get("reversed") is True else 1
            elif w.get("kind") == "track":
                res = cs.restore_review_flags(str(root), str(w["id"]), restored_by=str(actor or ""), source_skill=SOURCE_SKILL)
                kept += 0 if str((res or {}).get("status") or "") == "restored" else 1
        except Exception:  # noqa: BLE001 - this one stays, and the line says so
            kept += 1
    line = (UNWRAP_ONE_LINE.format(title=title) if kept == 1 else UNWRAP_PART_LINE.format(title=title, n=kept) if kept
            else UNWRAP_LINE.format(title=title) if d.get("wrote") else UNWRAP_NONE_LINE.format(title=title))
    row = _append(root, {"type": WRAP_EVENT, "source_skill": SOURCE_SKILL, "data": {
        "hub_choice": undo_id, "meeting_id": str(d.get("meeting_id") or ""), "title": title,
        "undoes_choice": press_id, "line": line}})
    return {"outcome": "applied", "line": line, "seq": row.get("seq")}
