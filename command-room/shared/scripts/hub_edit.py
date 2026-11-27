#!/usr/bin/env python3
"""hub_edit.py: add and edit work streams, people and priorities from the
Hub, each with an Undo (cr1#1383 part 2), edit a commitment's wording
(cr1#1383 slice 4), and add context to a thread or a person (cr1#1383, Add
context), with Take back.

WHY THIS EXISTS
The Hub could add a to-do, a reminder, a mail rule and a work stream from a
No home yet line, and nothing else. A work stream's name, a person and a
priority could only be corrected in a chat. This module is the Hub's side of
those edits. It owns no record: every write goes through the writer that
already owns the record.

  * a work stream, added:        `cockpit_helpers._create_stream` (the one
    thread writer, the same path No home yet's New work stream takes);
  * a work stream or a thread,
    renamed:                     `thread_writer.update_thread(display_name=)`;
  * a person, added:             `people_writer.create_person`;
  * a person, renamed:           `people_writer.rename_person`;
  * a person's role, email or
    company, set (cr1#1433):     `people_writer.set_person_field`, whose
    `person_field_set` row carries the value it replaced;
  * a priority, added:           `objective_state.create_objective` (bound
    to the owner's own word, `self`, the binding that needs no meeting or
    linked thread);
  * a priority's wording, edited: `objective_state.update_objective`;
  * a commitment's wording,
    edited (slice 4):            `commitment_state.edit_commitment_wording`
    (its `new_title`, the field the row shows);
  * a commitment's due date,
    set (slice 4, PR 2):         `due_set.set_due`, the writer the owner
    ruled for on 2026-11-19 (option B): it sets the date and nothing else,
    never hides the item, and carries a running Later hide without
    lengthening it. Snooze stays Snooze (it hides the item until the day).
    The same Edit press may carry new words, a new date or both: the
    verdict on both halves comes first (`due_set.check_due`, and the Hub's
    own fence on an item about a person the owner put off limits), so a
    press is refused whole, never half written. The words are written
    first and the date last, because the date's undo refuses when any newer
    row names the item; the Undo takes the date back first, then the words.
  * context on a thread (Add context): `thread_writer.update_thread(notes=)`,
    the thread's free-text notes, the new note kept after any already there;
  * context on a person (Add context): `people_writer.record_person_fact`,
    one fact in the owner's own word, sourced to the press. Its Take back is
    the registered retraction (`brain_undo.REVERSERS["entity_fact_structured"]`,
    which appends `entity_fact_retracted`; the person card and history leave
    a retracted fact out).
  * context on a commitment (Add context, cr1#1383): `commitment_state.
    add_commitment_note`, the one commitment-note writer with a registered
    reverser (cr1#1463, PR #1475); its Take back is that reverser
    (`brain_undo.REVERSERS["commitment_note"]`, which appends
    `commitment_note_retracted`; the note stays in history). An older page
    still sends the old `commitment` `context` row, which keeps its own
    path (`orphan_note.reroute_orphan_note`, from `apply_commitment_choices`)
    exactly as before. An item about a person the owner put off limits takes
    no note from the Hub; a note already on it can still be taken back.
  Work streams and priorities take no context here: they have no note
  writer the Hub may use.
  * a link or a file attached to a meeting (cr1#1632, slice 2, `meeting_edit`
    `attach`): the press's own row IS the record, as an update on a thread
    is: the meeting by its calendar id as `target_id`, the attachment
    (`meeting_attach.check_press`), and the meeting's resolved attendees as
    `with_ids`, judged first by the imports' wall (`meeting_attach.screen`:
    a name or address naming someone off limits or personal is refused,
    counted, never named). Its Remove is the `undo` press: one row naming
    the press, and the readers (`meeting_attach`) leave it out.

Each press that lands appends ONE `hub_edit` row through the event gate:
what was pressed, the record it reached, and the value it replaced. That row
is what an Undo is judged against. It names its record as `target_id`, never
as a thread key, so it is not activity on any thread.

THE UNDO CONTRACT (cr1#1364, as the Hub's other undos keep it)
  * The verdict comes before any write, and it is judged by seq: the press's
    own `hub_edit` row, any newer `hub_edit` on the same record and field,
    and the record as it stands now.
  * A refusal writes nothing and names the item.
  * The undo goes back through the same writer: the old name or wording
    written back; an added work stream or priority archived through its one
    archive path; an added person archived the way the registered person
    reverser does (`brain_undo._reverse_person_org_creation`); a person's
    rename, or a role, email or company it set, put back through
    `brain_undo.undo_batch`, the change's own registered reverser, which
    also refuses when the field has changed again since.
  * An undo is not a touch: the writer rows an undo makes are named on its
    own `hub_edit` row (`wrote_seqs`), and the thread cards' day-count skips
    them (`thread_cards._fold`), so a work stream or a priority is not "moved
    today" by an undo.

Nothing here sends anything. stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

EVENT = "hub_edit"
KINDS = ("stream_edit", "person_edit", "priority_edit", "commitment_edit", "meeting_edit")
VERBS = frozenset({"add", "rename", "set", "undo", "context", "update", "order", "star", "attach"})
#: cr1#1632: a link or a file attached to a meeting, and its Remove.
MEETING_KIND = "meeting_edit"
#: The longest meeting id taken, as `cockpit_helpers` takes a calendar event id.
MEETING_ID_CAP = 512
#: A longer one is refused whole, never cut (two cut ids would be one).
MEETING_ID_LONG_LINE = "That meeting's calendar id is longer than the Hub takes; nothing was written."
#: Add context (cr1#1383): the kinds that take a free note, a thread's
#: (`stream_edit` on a thread that is not a work stream) and a person's. A
#: commitment's note (`commitment_edit` `context`) has its own path,
#: `_commit_context`, through the commitment-note writer.
CONTEXT_KINDS = ("stream_edit", "person_edit")
#: cr1#1433: the person fields a Hub `set` press edits, as the press names
#: them, with the record field each writes and the word its line uses.
PERSON_FIELDS = {"role": ("role", "role"), "email": ("email", "email"),
                 "company": ("primary_org_id", "company")}
VALUE_CAP = 200
NAME_CAP = 80
STATEMENT_CAP = 240
SOURCE_SKILL = "cockpit"
UNDO_SOURCE = "commitment-triage"
RENAME_REASON = "Renamed on the Command Room Hub"
ARCHIVE_REASON = "Added on the Command Room Hub, then taken back there"

#: The record each kind reaches, in a word, for the lines.
WHAT = {"stream_edit": "work stream", "person_edit": "person", "priority_edit": "priority",
        "commitment_edit": "commitment"}
#: cr1#1383 slice 4: a commitment's wording, as `edit_commitment_wording`
#: caps a title.
COMMIT_CAP = 300
COMMIT_REASON = "Wording changed on the Command Room Hub"
COMMIT_UNDO_REASON = "Wording put back on the Command Room Hub"

NEEDS_NAME_LINE = "Type a name; nothing was written."
NEEDS_FULL_NAME_LINE = "A person needs a first and a last name; nothing was written."
NEEDS_WORDS_LINE = "Type the priority in a few words; nothing was written."
GONE_LINE = "{name} is not on your book any more; nothing was written."
NO_TARGET_LINE = "That press names nothing on your book; nothing was written."
SAME_LINE = "{name} is already called that; nothing was written."
STREAM_EXISTS_LINE = "You already have the work stream {name}; nothing was written."
NAME_TAKEN_LINE = "{name} is already the name of something else on your book; nothing was written. Pick another name."
PERSON_EXISTS_LINE = "{name} is already on file; nothing was written."
PRIORITY_EXISTS_LINE = "{name} is already one of your priorities; nothing was written."
PRIORITY_CLOSED_LINE = "{name} is closed, so it was not changed; nothing was written."
NEEDS_COMMIT_WORDS_LINE = "Type the commitment in a few words; nothing was written."
COMMIT_LONG_LINE = ("{name} is longer than Edit can put back exactly, so it was left as it is; "
                    "nothing was written. Fix its wording in a chat.")
COMMIT_CLOSED_LINE = "{name} is closed, so it was not changed; nothing was written."
#: cr1#1383 slice 4, PR 2: the date on the Edit form, through `due_set`.
COMMIT_DUE_REASON = "Due date changed on the Command Room Hub"
COMMIT_PAST_LINE = "{name}: {day} has already passed, so the date was not changed; nothing was written."
COMMIT_WAITING_LINE = ("{name} is waiting on something, so its date was not changed; nothing was written. "
                       "Say it is not waiting first.")
COMMIT_BAD_DATE_LINE = "{name}: that is not a date the Hub can set; nothing was written."
COMMIT_SAME_DUE_LINE = "{name} is already due then; nothing was written."
COMMIT_OFF_LIMITS_LINE = ("{name} is about someone you put off limits, so its date is not changed from the Hub; "
                          "nothing was written.")
COMMIT_DUE_LINE = "{name}: the date could not be set; nothing was written. Try again, or say it in a chat."
#: Review of #1606: who an item is about cannot be told (the wall or the mail
#: rules unreadable), so the fence says no (fail closed). Names no one.
COMMIT_FENCE_UNREAD_LINE = ("{name}: the Hub cannot tell right now who this is about, so its date was not "
                            "changed; nothing was written. Try again later.")
#: Review of #1606 (coordinator decision, reversible): an item the same Apply
#: run already planned on the Week keeps the plan; the Edit's date is refused.
COMMIT_JUST_PLANNED_LINE = "{name} was just planned for {day}, so its date was not changed. Edit it again if you meant to."
COMMIT_DUE_FAILED_WHY = "it could not be written. Try again, or say it in a chat."
COMMIT_DUE_PART_LINE = "The date was not changed: {why}"
#: cr1#1383, Add context on a commitment. The off-limits line names no one:
#: the item's own words may carry the walled person's name.
COMMIT_NOTE_OFF_LIMITS_LINE = ("That item is about someone you put off limits, so no note is added to it "
                               "from the Hub; nothing was written.")
COMMIT_NOTE_FENCE_UNREAD_LINE = ("{name}: the Hub cannot tell right now who this is about, so no note was "
                                 "added; nothing was written. Try again later.")
COMMIT_NOTE_REASON = "Note taken back on the Command Room Hub"
#: Review of #1611: a Take back refused because the item had other changes
#: after the note. Said without blaming one: a change since may itself have
#: been put back, and it still stands in the way.
COMMIT_NOTE_STALE_LINE = ("{name} had other changes after this note, so the note was left as it is; "
                          "nothing was written.")
#: cr1#1864 (147s): caught as a ValueError, which includes the event gate's
#: EventGateError, raised after the record was saved (`set_person_field`,
#: `create_objective`, `update_objective`): the line never says nothing changed.
FAILED_LINE = "{name} could not be confirmed just now; the item shows whether it changed."
NEEDS_VALUE_LINE = "Type the {what}; nothing was written. To clear it, say so in a chat."
NO_FIELD_LINE = "That is not something the Hub edits on a person; nothing was written."
NO_COMPANY_LINE = "{value} is not a company on file; nothing was written. Add it in a chat first."
EMAIL_TAKEN_LINE = "{value} is on file for someone else; nothing was written."
BAD_VALUE_LINE = "{value} is not a {what} the book can keep; nothing was written."
SAME_VALUE_LINE = "{name}'s {what} is already that; nothing was written."
#: cr1#1431: the apply step refreshes the Work and People feeds right after
#: an edit lands, so the line no longer sends the owner to the next refresh.
REFRESH = ""

UNDO_NO_PRESS_LINE = "That change is not on your book; nothing was written."
UNDO_ALREADY_LINE = "That was already put back; nothing was written."
UNDO_NEWER_LINE = "{name} was changed again on the Hub after this, so this was left as it is; nothing was written."
UNDO_CHANGED_LINE = "{name} has changed since, so it was left as it is; nothing was written."
UNDO_IN_USE_LINE = "{name} has other work on it now, so it stays; nothing was written."
UNDO_FAILED_LINE = "{name} could not be put back; nothing was written. Try again, or say undo in a chat."
#: cr1#1864 (147s): an Undo whose writer may have put back part of it before it
#: raised or reported an error (a stream and its threads archived one by one, a
#: batch undo with errors, words after a due date, a thread saved before its
#: log row, a reverser): the line never says nothing was written. UNDO_FAILED_LINE
#: stays only where nothing can have been written (a refusal before any write,
#: or the one `_record` row, a single atomic append that lands whole or not at all).
UNDO_UNCONFIRMED_LINE = "{name} could not be confirmed as put back just now; the item shows whether it changed."

#: Add context (cr1#1383). The note as kept, and the lines.
CONTEXT_CAP = 200
CONTEXT_REF = "user:hub_context"
CONTEXT_CATEGORY = "other"
CONTEXT_UNDO_REASON = "Note taken back on the Command Room Hub"
CONTEXT_LINE = "You told it: {note}"
CONTEXT_TAKEN_LINE = "Took back your note on {name}."
NEEDS_NOTE_LINE = "Type the note; nothing was written."
CONTEXT_SAME_LINE = "{name} already has that note; nothing was written."
CONTEXT_STREAM_LINE = ("{name} is a work stream, so a note goes on one of its threads, or say it in a chat. "
                       "Nothing was written.")
CONTEXT_NO_TARGET_LINE = "A note goes on a thread, a person or a commitment; nothing was written."

#: cr1#1540: an update the owner posts on a thread, the order he keeps his
#: priorities in, and a thread starred into a priority.
UPDATE_CAP = 240
UPDATE_LINE = "Posted on {name}: {note}"
UPDATE_TAKEN_LINE = "Took back your update on {name}."
NEEDS_UPDATE_LINE = "Type the update; nothing was written."
UPDATE_NO_TARGET_LINE = "An update goes on a thread; nothing was written."
#: The order is one record of the owner's, not a field on any priority, so
#: its row names this in place of a record id.
ORDER_TARGET = "priority_order"
ORDER_CAP = 60
ORDER_LINE = "Saved the order of your priorities."
ORDER_SAME_LINE = "Your priorities are already in that order; nothing was written."
ORDER_EMPTY_LINE = "That press names no priority; nothing was written."
ORDER_GONE_LINE = "One of those is not a priority any more, so the order was left as it is; nothing was written."
ORDER_UNDO_LINE = "Put your priorities back in the order they were."
ORDER_NEWER_LINE = "You changed the order again after this, so it was left as it is; nothing was written."
STAR_LINE = "Made {name} a priority."
STAR_ALREADY_LINE = "{name} is already behind one of your priorities; nothing was written."
STAR_FULL_LINE = "Five priorities at most. Take one off first. Nothing was written."
STAR_NO_TARGET_LINE = "A star goes on a thread; nothing was written."
STAR_NAME_TRIES = 9

#: cr1#1632, Attached. The lines name no attachment: its name may be the
#: very thing the wall refused, and a Remove may take back one that names
#: someone put off limits since.
ATTACH_LINE = "Attached to this meeting."
ATTACH_ALREADY_LINE = "That is already attached to this meeting; nothing was written."
ATTACH_FAILED_LINE = "It could not be attached; nothing was written. Try again, or say it in a chat."
REMOVE_LINE = "Removed from this meeting."
REMOVE_NO_PRESS_LINE = "That attachment is not on your book; nothing was written."
REMOVE_ALREADY_LINE = "That was already removed; nothing was written."


# ---------------------------------------------------------------------------
# Reading the book
# ---------------------------------------------------------------------------

def _events(root: Path) -> List[dict]:
    from events_io import load_events_owner_scoped

    return [e for e in load_events_owner_scoped(str(root))[0] if isinstance(e, dict)]


def _seq(ev: dict) -> int:
    s = ev.get("seq")
    return s if isinstance(s, int) else -1


def _max_seq(evs: List[dict]) -> int:
    return max((_seq(e) for e in evs), default=-1)


def _edits(evs: List[dict]) -> List[dict]:
    return [e for e in evs if e.get("type") == EVENT and isinstance(e.get("data"), dict)]


def _written(evs: List[dict], choice_id: str) -> Optional[dict]:
    """The `hub_edit` row this press already wrote, so Apply twice writes once."""
    if not choice_id:
        return None
    return next((e for e in _edits(evs) if e["data"].get("hub_choice") == choice_id), None)


def _clean(value: Any, cap: int) -> str:
    """The typed words, spaces tidied, control characters out, capped."""
    text = "".join(ch_ if ch_.isprintable() else " " for ch_ in str(value or ""))
    return " ".join(text.split())[:cap].strip()


def _norm(text: Any) -> str:
    import thread_writer as tw

    return tw._norm(text)


def _thread_recs(root: Path) -> List[dict]:
    import thread_writer as tw

    return [t for t in tw._threads(tw._load_entities(root)) if isinstance(t, dict)]


def _thread(root: Path, tid: str) -> Optional[dict]:
    return next((t for t in _thread_recs(root) if str(t.get("id") or "") == tid), None)


def _thread_name(t: dict) -> str:
    return str(t.get("display_name") or t.get("canonical_name") or t.get("id") or "")


def _person(root: Path, pid: str) -> Optional[dict]:
    import people_writer as pw
    from entities_io import entities_collection

    data = pw._load_entities(root)
    return next((p for p in entities_collection(data, "people")
                 if isinstance(p, dict) and str(p.get("id") or "") == pid), None)


def _live(rec: Optional[dict]) -> bool:
    return isinstance(rec, dict) and str(rec.get("status") or "active").strip().lower() not in (
        "archived", "resolved", "completed", "retired", "closed")


def _objective_open(t: Optional[dict]) -> bool:
    obj = (t or {}).get("objective") if isinstance((t or {}).get("objective"), dict) else None
    return (isinstance(t, dict) and t.get("kind") == "objective" and obj is not None
            and not obj.get("outcome") and _live(t))


def _record(root: Path, before: int, data: Dict[str, Any], ids: Optional[set] = None) -> dict:
    """The press's one `hub_edit` row, naming the writer rows the press made:
    every row newer than `before` (the newest seq read before it wrote) that
    names one of its records (`ids`, the target by default). A later undo
    tells the Hub's own writes from other work by them, and a row another
    writer landed meanwhile on something else is never claimed."""
    import json

    from event_gate import append_event

    quoted = [json.dumps(i) for i in (ids or {str(data.get("target_id") or "")}) if i]
    data = dict(data, wrote_seqs=sorted(
        _seq(e) for e in _events(root) if _seq(e) > before
        and any(q in json.dumps(e, ensure_ascii=False, default=str) for q in quoted)))
    landed = append_event(root / "_hq" / "data" / "events.jsonl",
                          {"type": EVENT, "source_skill": SOURCE_SKILL, "data": data},
                          holder="cockpit_helpers")
    return (landed[0] if isinstance(landed, list) and landed else {}) or {}


def _ok(line: str, **extra) -> Dict[str, Any]:
    return dict({"outcome": "applied", "line": line}, **extra)


def _no(line: str, outcome: str = "refused", **extra) -> Dict[str, Any]:
    return dict({"outcome": outcome, "line": line}, **extra)


# ---------------------------------------------------------------------------
# The presses
# ---------------------------------------------------------------------------

def apply_one(workspace_root, c: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """WRITE. One add, rename or undo press, `c` as `plan_apply` planned it
    (`kind`, `action`, `n`, `input`, `id`). Answers `{outcome, line, ...}`;
    `outcome` is `applied`, `already`, `refused` or `error`. A refusal
    writes nothing."""
    root = Path(str(workspace_root))
    kind = str(c.get("kind") or "").strip().lower()
    verb = str(c.get("action") or "").strip().lower()
    choice_id = str(c.get("id") or "").strip()
    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
    # cr1#1632 review: a meeting's calendar id is taken whole, as the
    # calendar actions take it (a Microsoft 365 id runs to ~150 characters).
    # A meeting id over the cap is refused below, never cut (review round 2:
    # two long ids sharing their first 512 characters would read as one).
    target = str(c.get("n") or "").strip()
    if kind != MEETING_KIND:
        target = target[:80]
    evs = _events(root)
    done = _written(evs, choice_id)
    if done is not None:
        d = done["data"]
        return _ok(str(d.get("line") or "Already applied."), replayed=True, target_id=d.get("target_id"),
                   seq=done.get("seq"))
    before = _max_seq(evs)
    if kind == MEETING_KIND:
        # cr1#1632: Attached, a link or a file on a meeting, and its Remove.
        if verb == "attach":
            if len(target) > MEETING_ID_CAP:
                return _no(MEETING_ID_LONG_LINE)
            return _meeting_attach(root, target, inp, choice_id, evs, before)
        if verb == "undo":
            return _meeting_remove(root, str(inp.get("press") or "").strip(), choice_id, evs)
        return _no(NO_TARGET_LINE)
    if verb == "update":
        # cr1#1540: a dated update on a thread, the press's own row and no other.
        return _thread_update(root, target, inp, choice_id, before) if kind == "stream_edit" \
            else _no(UPDATE_NO_TARGET_LINE)
    if verb in ("order", "star"):
        if kind != "priority_edit":
            return _no(NO_TARGET_LINE)
        return _priority_order(root, inp, choice_id, evs, before) if verb == "order" \
            else _priority_star(root, target, choice_id, before)
    if verb == "context":
        if kind == "stream_edit":
            return _thread_context(root, target, inp, choice_id, before)
        if kind == "person_edit":
            return _person_context(root, target, inp, choice_id, before)
        if kind == "commitment_edit":
            return _commit_context(root, target, inp, choice_id, actor, before)
        return _no(CONTEXT_NO_TARGET_LINE)
    if kind == "commitment_edit":
        if verb == "undo":
            return _commit_undo(root, str(inp.get("press") or "").strip(), choice_id, actor, evs)
        if verb == "rename":
            return _commit_rename(root, target, inp, choice_id, actor, before,
                                  pressed_at=str(c.get("created_at") or ""))
        if verb == "context":
            return _commit_context(root, target, inp, choice_id, actor, before)
        return _no(NO_TARGET_LINE)
    if verb == "undo":
        return _undo(root, kind, str(inp.get("press") or "").strip(), choice_id, actor, evs)
    if kind == "stream_edit":
        return _stream_add(root, inp, choice_id, evs, before) if verb == "add" else \
            _stream_rename(root, target, inp, choice_id, before)
    if kind == "person_edit":
        if verb == "set":
            return _person_set(root, target, inp, choice_id, before)
        return _person_add(root, inp, choice_id, before) if verb == "add" else \
            _person_rename(root, target, inp, choice_id, before)
    return _priority_add(root, inp, choice_id, before) if verb == "add" else \
        _priority_edit(root, target, inp, choice_id, before)


def _stream_add(root: Path, inp: dict, choice_id: str, evs: List[dict], before: int) -> Dict[str, Any]:
    import cockpit_helpers as ch

    name = _clean(inp.get("name"), NAME_CAP)
    if not name:
        return _no(NEEDS_NAME_LINE)
    how, found = ch._stream_for_name(root, name, evs)
    if how == "existing":
        held = _thread_name(_thread(root, str(found)) or {}) or name
        return _no(STREAM_EXISTS_LINE.format(name=held), "already", target_id=found)
    if how in ("taken", "archived", "closed"):
        rec = found or {}
        held = str(rec.get("display_name") or rec.get("canonical_name") or name)
        line = (ch.NEW_STREAM_TAKEN_LINE if how == "taken" else ch.NEW_STREAM_ARCHIVED_LINE
                if how == "archived" else ch.NEW_STREAM_CLOSED_LINE).format(name=held, what="a thread")
        return _no(line)
    if how == "new":
        # cr1#1582: the stream's folder is made with it, so a folder that
        # cannot be made as asked refuses before anything is written
        refused = ch._stream_folder_refusal(root, name)
        if refused:
            return _no(refused)
    folder: Dict[str, Any] = {}
    try:
        sid = ch._create_stream(root, name, found if how == "resume" else None, folder_out=folder)
    except ValueError:
        return _no(ch.NEW_STREAM_FAILED_LINE, "error")
    except ch._HalfMadeStream:
        return _no(ch.NEW_STREAM_HALF_LINE.format(name=name), "error")
    except ch._StreamUnsure:
        return _no(ch.NEW_STREAM_UNSURE_LINE.format(name=name), "error")
    # cr1#1582 review round 2: a folder that could not be made is said, as the
    # No home yet press says it (the stream stands; "make its folder" finishes it)
    line = (f"Started the work stream {name}."
            + (ch.NEW_STREAM_NO_FOLDER_LINE if folder and not folder.get("ok") else "") + REFRESH)
    kids = {str(t.get("id")) for t in _thread_recs(root) if str(t.get("parent_thread_id") or "") == sid}
    rec = _record(root, before, {"hub_choice": choice_id, "kind": "stream_edit", "action": "add",
                                 "target_id": sid, "new": name, "line": line}, ids={sid} | kids)
    return _ok(line, target_id=sid, seq=rec.get("seq"))


def _stream_rename(root: Path, tid: str, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    import thread_writer as tw

    rec = _thread(root, tid) if tid else None
    if rec is None or rec.get("kind") == "objective":
        return _no(NO_TARGET_LINE)
    old = _thread_name(rec)
    if not _live(rec):
        return _no(GONE_LINE.format(name=old))
    name = _clean(inp.get("name"), NAME_CAP)
    if not name:
        return _no(NEEDS_NAME_LINE)
    if name == old:
        return _no(SAME_LINE.format(name=old), "already")
    key = _norm(name)
    clash = [t for t in _thread_recs(root) if str(t.get("id") or "") != tid
             and key in (_norm(t.get("display_name")), _norm(t.get("canonical_name")))]
    if clash:
        return _no(NAME_TAKEN_LINE.format(name=name))
    try:
        tw.update_thread(root, tid, source_skill=SOURCE_SKILL, display_name=name)
    except ValueError:
        return _no(FAILED_LINE.format(name=old), "error")
    line = f"Renamed {old} to {name}." + REFRESH
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "stream_edit", "action": "rename",
                        "target_id": tid, "field": "display_name", "old": old,
                        "new": name, "line": line})
    return _ok(line, target_id=tid, seq=ev.get("seq"))


def _person_add(root: Path, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    import people_writer as pw

    name = _clean(inp.get("name"), NAME_CAP)
    if not name:
        return _no(NEEDS_NAME_LINE)
    try:
        rec = pw.create_person(root, canonical_name=name, source_skill=SOURCE_SKILL)
    except pw.DuplicatePersonError:
        return _no(PERSON_EXISTS_LINE.format(name=name), "already")
    except pw.SingleWordNameRefused:
        return _no(NEEDS_FULL_NAME_LINE)
    except ValueError:
        return _no(FAILED_LINE.format(name=name), "error")
    pid = str(rec.get("id") or "")
    line = f"Added {name} to your people." + REFRESH
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "person_edit", "action": "add",
                        "target_id": pid, "new": name, "line": line})
    return _ok(line, target_id=pid, seq=ev.get("seq"))


def _person_rename(root: Path, pid: str, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    import people_writer as pw

    rec = _person(root, pid) if pid else None
    if rec is None:
        return _no(NO_TARGET_LINE)
    old = str(rec.get("canonical_name") or pid)
    if not _live(rec):
        return _no(GONE_LINE.format(name=old))
    name = _clean(inp.get("name"), NAME_CAP)
    if not name:
        return _no(NEEDS_NAME_LINE)
    try:
        pw.refuse_single_word_name(name)
    except ValueError:
        return _no(NEEDS_FULL_NAME_LINE)
    try:
        out = pw.rename_person(root, pid, canonical_name=name, reason=RENAME_REASON, source_skill=SOURCE_SKILL)
    except pw.PersonNameCollisionError:
        return _no(NAME_TAKEN_LINE.format(name=name))
    except pw.PersonRecordNeedsRepairError as exc:
        # cr1#1430: the record holds a value that does not read; the
        # writer's line says why and what to do next.
        return _no(exc.line)
    except ValueError:
        return _no(FAILED_LINE.format(name=old), "error")
    if out.get("status") == "unchanged":
        return _no(SAME_LINE.format(name=old), "already")
    line = f"Renamed {old} to {name}." + REFRESH
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "person_edit", "action": "rename",
                        "target_id": pid, "field": "canonical_name", "old": old, "new": name,
                        "batch_id": out.get("batch_id"), "line": line})
    return _ok(line, target_id=pid, seq=ev.get("seq"))


def _org_by_name(root: Path, name: str) -> Optional[dict]:
    """The one live company on file whose name or an alias is `name`."""
    import people_writer as pw
    from entities_io import entities_collection

    want = _norm(name)
    hits = [o for o in entities_collection(pw._load_entities(root), "orgs")
            if isinstance(o, dict) and _live(o)
            and str(o.get("status") or "").lower() not in ("merged", "deleted")
            and want in ({_norm(o.get("canonical_name"))}
                         | {_norm(a) for a in (o.get("aliases") or []) if isinstance(a, str)})]
    return hits[0] if len(hits) == 1 else None


def _org_name(root: Path, org_id: str) -> str:
    import people_writer as pw
    from entities_io import entities_collection

    org = next((o for o in entities_collection(pw._load_entities(root), "orgs")
                if isinstance(o, dict) and o.get("id") == org_id), None)
    return str((org or {}).get("canonical_name") or org_id)


def _shown(root: Path, field: str, raw: str) -> str:
    """A field's stored value as the line says it."""
    if not raw:
        return "none"
    return _org_name(root, raw) if field == "primary_org_id" else raw


def _shown_before(root: Path, field: str, rec: dict) -> str:
    """What the page showed for the field before the press: the stored value,
    else what the person card falls back to (an older company field, the
    first of the address list)."""
    raw = str(rec.get(field) or "")
    if raw:
        return _shown(root, field, raw)
    try:
        if field == "primary_org_id":
            from people_view import company_of
            return company_of(rec, {}) or (_org_name(root, str(rec.get("org_id") or ""))
                                           if rec.get("org_id") else "") or "none"
        if field == "email":
            from person_cards import _email_of
            return _email_of(rec) or "none"
    except Exception:  # noqa: BLE001 - a line, never a refusal
        pass
    return "none"


def _person_set(root: Path, pid: str, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    """cr1#1433: a person's role, email or company, set through
    `people_writer.set_person_field`; the row it writes is what the Undo puts
    back through `brain_undo.undo_batch`."""
    import people_writer as pw

    named = str(inp.get("field") or "").strip().lower()
    if named not in PERSON_FIELDS:
        return _no(NO_FIELD_LINE)
    field, what = PERSON_FIELDS[named]
    rec = _person(root, pid) if pid else None
    if rec is None:
        return _no(NO_TARGET_LINE)
    who = str(rec.get("canonical_name") or pid)
    if not _live(rec):
        return _no(GONE_LINE.format(name=who))
    typed = " ".join(str(inp.get("value") or "").split())
    if not typed:
        return _no(NEEDS_VALUE_LINE.format(what=what))
    if len(typed) > VALUE_CAP:
        # Refused, never cut: a shortened address can be someone else's.
        return _no(BAD_VALUE_LINE.format(value=typed[:40] + "...", what=what))
    value = typed
    if field == "primary_org_id":
        org = _org_by_name(root, typed)
        if org is None:
            return _no(NO_COMPANY_LINE.format(value=typed))
        value = str(org.get("id") or "")
    old_raw = str(rec.get(field) or "")
    try:
        out = pw.set_person_field(root, pid, field=field, value=value, source_skill=SOURCE_SKILL)
    except pw.PersonFieldRefused as exc:
        if exc.reason == "email_taken":
            return _no(EMAIL_TAKEN_LINE.format(value=typed))
        if exc.reason == "unknown_org":
            return _no(NO_COMPANY_LINE.format(value=typed))
        return _no(BAD_VALUE_LINE.format(value=typed, what=what))
    except (KeyError, ValueError):
        return _no(FAILED_LINE.format(name=who), "error")
    if out.get("status") == "unchanged":
        return _no(SAME_VALUE_LINE.format(name=who, what=what), "already")
    new_raw = str(out.get("value") or "")
    new_shown = _shown(root, field, new_raw)
    line = f"Set {who}'s {what} to {new_shown}." + REFRESH
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "person_edit", "action": "set",
                                "target_id": pid, "field": field, "old": _shown_before(root, field, rec),
                                "new": new_shown, "old_value": old_raw, "new_value": new_raw,
                                "batch_id": out.get("batch_id"), "line": line})
    return _ok(line, target_id=pid, seq=ev.get("seq"))


def _priority_add(root: Path, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    import objective_state as obs

    text = _clean(inp.get("name"), STATEMENT_CAP)
    if not text:
        return _no(NEEDS_WORDS_LINE)
    key = _norm(text)
    for t in _thread_recs(root):
        if _objective_open(t) and _norm((t.get("objective") or {}).get("statement")) == key:
            return _no(PRIORITY_EXISTS_LINE.format(name=text), "already", target_id=t.get("id"))
    if any(key in (_norm(t.get("display_name")), _norm(t.get("canonical_name"))) for t in _thread_recs(root)):
        return _no(NAME_TAKEN_LINE.format(name=text))
    try:
        rec = obs.create_objective(root, statement=text, binding=dict(type="self"), source_skill=SOURCE_SKILL)
    except ValueError:
        return _no(FAILED_LINE.format(name=text), "error")
    tid = str(rec.get("id") or "")
    line = f"Added the priority {text}." + REFRESH
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "priority_edit", "action": "add",
                        "target_id": tid, "new": text, "line": line})
    return _ok(line, target_id=tid, seq=ev.get("seq"))


def _priority_edit(root: Path, tid: str, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    import objective_state as obs

    rec = _thread(root, tid) if tid else None
    if rec is None or rec.get("kind") != "objective":
        return _no(NO_TARGET_LINE)
    old = str((rec.get("objective") or {}).get("statement") or _thread_name(rec))
    if not _objective_open(rec):
        return _no(PRIORITY_CLOSED_LINE.format(name=old))
    text = _clean(inp.get("name"), STATEMENT_CAP)
    if not text:
        return _no(NEEDS_WORDS_LINE)
    if text == old:
        return _no(SAME_LINE.format(name=old), "already")
    try:
        obs.update_objective(root, tid, statement=text, source_skill=SOURCE_SKILL)
    except ValueError:
        return _no(FAILED_LINE.format(name=old), "error")
    line = f"Changed the priority to {text}." + REFRESH
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "priority_edit", "action": "rename",
                        "target_id": tid, "field": "statement", "old": old, "new": text, "line": line})
    return _ok(line, target_id=tid, seq=ev.get("seq"))


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def _current(root: Path, kind: str, tid: str, field: str = "") -> Tuple[Optional[dict], str]:
    """The record a press reached and the value its field holds now (for a
    person's `set`, the field the press named, as stored)."""
    if kind == "person_edit":
        rec = _person(root, tid)
        return rec, str((rec or {}).get(field or "canonical_name") or "")
    rec = _thread(root, tid)
    if kind == "priority_edit":
        return rec, str(((rec or {}).get("objective") or {}).get("statement") or "")
    return rec, _thread_name(rec or {})


def _named_since(root: Path, ids: set, after_seq: int, evs: List[dict]) -> bool:
    """Whether anything on the book names one of `ids` besides the press's own
    writes: a log row newer than the press (other than the Hub's own
    `hub_edit` rows and receipts; the writer rows the press made come before
    its `hub_edit` row), or another record on the book. Every doubt answers yes, so nothing is archived on a guess."""
    import copy
    import json

    from entities_io import entities_collection, read_entities_json

    quoted = [json.dumps(i) for i in ids]
    # The Hub's own later presses on these records, and their undos, are not
    # other work: the rows they wrote are named on their `hub_edit` rows.
    # A note added on the Hub (Add context, cr1#1383) is content, not
    # bookkeeping: it counts as work, as the same note from a chat would.
    hub_wrote = {s for e in _edits(evs) if str(e["data"].get("target_id") or "") in ids
                 and e["data"].get("action") != "context"
                 for s in e["data"].get("wrote_seqs") or [] if isinstance(s, int)}
    for ev in evs:
        if _seq(ev) <= after_seq or ev.get("type") == EVENT or _seq(ev) in hub_wrote:
            continue
        if ev.get("type") == "apply_choices_applied":
            continue  # the Hub's own receipts: never work
        blob = json.dumps(ev, ensure_ascii=False, default=str)
        if any(q in blob for q in quoted):
            return True
    data = copy.deepcopy(read_entities_json(root / "_hq" / "data" / "entities.json"))
    for coll in ("people", "threads", "orgs"):
        try:
            rows = entities_collection(data, coll)
        except Exception:  # noqa: BLE001 - a collection the book does not carry
            continue
        rows[:] = [r for r in rows if not (isinstance(r, dict) and str(r.get("id") or "") in ids)]
    rest = json.dumps(data, ensure_ascii=False, default=str)
    return any(q in rest for q in quoted)


def _undo(root: Path, kind: str, press_id: str, undo_id: str, actor: str, evs: List[dict]) -> Dict[str, Any]:
    """The verdict first, by seq, then the one writer. See the module's
    contract."""
    edits = _edits(evs)
    press = next((e for e in edits if press_id and e["data"].get("hub_choice") == press_id
                  and e["data"].get("action") != "undo"), None)
    if press is None or press["data"].get("kind") != kind:
        return _no(UNDO_NO_PRESS_LINE)
    pd = press["data"]
    tid = str(pd.get("target_id") or "")
    if any(e["data"].get("undoes_choice") == press_id for e in edits):
        return _no(UNDO_ALREADY_LINE, "already")
    if pd.get("action") == "context":
        return _context_undo(root, kind, press, undo_id, actor, evs)
    if pd.get("action") == "update":
        return _update_undo(root, press, undo_id, evs)
    if pd.get("action") == "order":
        return _order_undo(root, press, undo_id, evs)
    is_set = pd.get("action") == "set"
    rec, now_value = _current(root, kind, tid, str(pd.get("field") or "") if is_set else "")
    name = str(pd.get("new") or tid)
    if is_set:
        # The line names the person, not the value (cr1#1433).
        name = str((rec or {}).get("canonical_name") or tid)
    ids = {tid}
    if kind == "stream_edit":
        ids |= {str(t.get("id")) for t in _thread_recs(root) if str(t.get("parent_thread_id") or "") == tid}
    if rec is None:
        return _no(GONE_LINE.format(name=name), "already" if pd.get("action") == "add" else "refused")
    if is_set:
        # Judged on the one field the press set: a newer Hub edit of it, or
        # the field as it stands not being what the press wrote, refuses.
        newer = [e for e in edits if _seq(e) > _seq(press) and e["data"].get("target_id") == tid
                 and e["data"].get("action") == "set" and e["data"].get("field") == pd.get("field")]
        if newer:
            return _no(UNDO_NEWER_LINE.format(name=name))
        if now_value != str(pd.get("new_value") or ""):
            return _no(UNDO_CHANGED_LINE.format(name=name))
        if not _live(rec):
            return _no(UNDO_CHANGED_LINE.format(name=name))
    elif pd.get("action") == "rename":
        # A role, email or company set (cr1#1433) is another field: it does
        # not stand in the way of putting a name back.
        # Add context (cr1#1383) writes a note, not the name, so a note
        # added since does not stand in the way either.
        newer = [e for e in edits if _seq(e) > _seq(press) and e["data"].get("target_id") == tid
                 and e["data"].get("action") not in ("undo", "set", "context", "update")]
        if newer:
            return _no(UNDO_NEWER_LINE.format(name=now_value or name))
        if now_value != pd.get("new"):
            return _no(UNDO_CHANGED_LINE.format(name=now_value or name))
        if not _live(rec) or (kind == "priority_edit" and not _objective_open(rec)):
            return _no(UNDO_CHANGED_LINE.format(name=now_value or name))
    else:
        if not _live(rec) or (kind == "priority_edit" and not _objective_open(rec)):
            return _no(UNDO_ALREADY_LINE, "already")
        # The whole log is read, not only the rows after the press: a work
        # stream an earlier Apply left half made and this press finished
        # (`resume`) is older than the press, and what named it then counts.
        if _named_since(root, ids, -1, evs) or _has_update(evs, ids) \
                or (kind == "stream_edit" and len(ids) > 2):
            return _no(UNDO_IN_USE_LINE.format(name=now_value or name))
    before = _max_seq(evs)
    try:
        line = _put_back(root, kind, pd, tid, rec, actor)
    except Exception:  # noqa: BLE001 - cr1#1864: part may have been put back first
        return _no(UNDO_UNCONFIRMED_LINE.format(name=now_value or name), "error")
    if line is None:  # a batch undo that undid none, or some with errors
        return _no(UNDO_UNCONFIRMED_LINE.format(name=now_value or name), "error")
    ev = _record(root, before, {"hub_choice": undo_id, "kind": kind, "action": "undo", "target_id": tid,
                                "undoes_choice": press_id, "line": line}, ids=ids)
    return _ok(line, target_id=tid, seq=ev.get("seq"))


def _put_back(root: Path, kind: str, pd: dict, tid: str, rec: dict, actor: str) -> Optional[str]:
    """The undo's one write, through the record's own writer. Answers the
    line, or None when the writer put nothing back."""
    old = str(pd.get("old") or "")
    if kind == "stream_edit" and pd.get("action") == "rename":
        import thread_writer as tw

        # The schema keeps a name a string: a thread that had no display name
        # gets its old name written as one, so it reads exactly as before.
        tw.update_thread(root, tid, source_skill=SOURCE_SKILL, display_name=old)
        return f"Put back the name {old}."
    if kind == "stream_edit":
        import thread_archive as ta

        kids = [str(t.get("id")) for t in _thread_recs(root) if str(t.get("parent_thread_id") or "") == tid]
        ta.archive_thread(str(root), tid, reason=ARCHIVE_REASON, source_skill=SOURCE_SKILL)
        for k in kids:
            ta.archive_thread(str(root), k, reason=ARCHIVE_REASON, source_skill=SOURCE_SKILL,
                              regenerate_view=False)
        line = f"Took back the work stream {pd.get('new')}; it is archived."
        # cr1#1582: the folder the add made goes with it: removed when
        # empty, moved whole to the archive when anything is in it; one that
        # was already there, or another live project's, stays.
        import cockpit_helpers as ch
        import project_folder

        try:
            back = project_folder.take_back_folder(root, tid, source_skill=SOURCE_SKILL)
        except Exception:  # noqa: BLE001 - the stream is archived; the folder stays
            back = {"ok": False}
        if back.get("outcome") == "archived":
            line += ch.UNASSIGN_FOLDER_ARCHIVED_LINE
        elif not back.get("ok"):
            line += ch.UNASSIGN_FOLDER_STAYS_LINE
        return line
    if kind == "person_edit" and pd.get("action") == "set":
        import brain_undo as bu

        out = bu.undo_batch(str(root), {"kind": "brain_batch", "batch_id": str(pd.get("batch_id") or "")},
                            undone_by=actor, source_skill=UNDO_SOURCE, user_confirmed=True) or {}
        if not int(out.get("n_undone") or 0) or int(out.get("n_errors") or 0):
            return None
        what = next((w for f, w in PERSON_FIELDS.values() if f == pd.get("field")), "detail")
        who = str(rec.get("canonical_name") or tid)
        if not pd.get("old_value"):
            return f"Took back {who}'s {what} set on the Hub."
        return f"Put back {who}'s {what} as {pd.get('old')}."
    if kind == "person_edit" and pd.get("action") == "rename":
        import brain_undo as bu

        out = bu.undo_batch(str(root), {"kind": "brain_batch", "batch_id": str(pd.get("batch_id") or "")},
                            undone_by=actor, source_skill=UNDO_SOURCE, user_confirmed=True) or {}
        if not int(out.get("n_undone") or 0) or int(out.get("n_errors") or 0):
            return None
        return f"Put back the name {old}."
    if kind == "person_edit":
        # The registered reverser for a person the system added: archive,
        # never delete (`brain_undo._reverse_person_org_creation`).
        import people_writer as pw

        pw.update_person(root, tid, source_skill=SOURCE_SKILL, status="archived")
        return f"Took back {pd.get('new')}; the record is archived."
    import objective_state as obs

    if pd.get("action") == "rename":
        obs.update_objective(root, tid, statement=old, source_skill=SOURCE_SKILL)
        return f"Put back the priority as {old}."
    obs.archive_objective(root, tid, outcome_note=ARCHIVE_REASON, source_skill=SOURCE_SKILL)
    return f"Took back the priority {pd.get('new')}; it is archived."


# ---------------------------------------------------------------------------
# A commitment's wording (cr1#1383 slice 4)
# ---------------------------------------------------------------------------

def _events_path(root: Path) -> Path:
    return root / "_hq" / "data" / "events.jsonl"


def _commit_resolve(root: Path, raw: str) -> Tuple[Optional[str], Optional[dict]]:
    """The commitment's canonical id and its capture row, or (None, None)
    when the book holds no such commitment."""
    import commitment_state as cs

    if not raw:
        return None, None
    index = cs._scan_commitment_index(_events_path(root))
    try:
        cid = cs.normalize_commitment_id(raw, index)
    except cs.CommitmentIdError:
        return None, None
    return cid, index["by_id"].get(cid)


def _commit_open(root: Path, cid: str) -> Optional[dict]:
    """The commitment as the open set projects it now (its wording folded,
    newest wins), or None when it is closed."""
    from cru_match import _commitment_id, load_open_commitments

    p = _events_path(root)
    if not p.exists():
        return None
    return next((r for r in load_open_commitments(p) if _commitment_id(r) == cid), None)


def _commit_words(row: Optional[dict]) -> str:
    """The words the row shows: its title, else its summary (`row_title`)."""
    d = (row or {}).get("data") if isinstance((row or {}).get("data"), dict) else {}
    return " ".join(str(d.get("title") or d.get("summary") or "").split())


def _off_limits_item(root: Path, cid: str, row: Optional[dict], capture: Optional[dict]) -> Optional[bool]:
    """Whether the item is about a person the owner put off limits: its
    owner or any of its counterparties (`commitment_parties.counterparty_ids`,
    the scalar and the list), as shown now or as captured. The fence is the
    Hub piles' own (`hub_work.fence`); None when it cannot be read (the wall
    or the mail rules store unreadable), which the caller refuses on: an
    unreadable store is no answer, never "nobody is off limits"."""
    import hub_work
    from commitment_parties import counterparty_ids

    wall = hub_work.fence(root)
    if wall is None:
        return None
    off = {str(i) for i in wall.get("off") or set()}
    if not off:
        return False
    who = set()
    for ev in (row, capture):
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        who |= {str(v) for v in (d.get("owner_id"), ev.get("owner_id"), ev.get("counterparty_id")) if v}
        who |= {str(v) for v in counterparty_ids(ev) if v}
    return bool(who & off)


def _just_planned(root: Path, cid: str, pressed_at: str) -> Optional[str]:
    """The day the Week planned this item for, when its newest date move is a
    planned one (`plan_at`, `commitment_state.apply_later`) that landed after
    the Edit press was made: in one Apply run the plan's step runs before the
    Hub's, so a plan saved beside this press, in either order, has landed
    first. None otherwise, and when the press carries no time of its own."""
    import datetime as _dt
    from events_io import load_events_owner_scoped

    try:
        pressed = _dt.datetime.fromisoformat(str(pressed_at or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if pressed.tzinfo is None:
        return None
    newest = None
    for ev in load_events_owner_scoped(str(root))[0]:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if (ev.get("type") == "commitment_updated" and str(d.get("commitment_id") or "") == cid
                and ("new_due" in d or d.get("due_cleared"))):
            newest = ev
    d = (newest or {}).get("data") or {}
    if not newest or "plan_at" not in d:
        return None
    try:
        at = _dt.datetime.fromisoformat(str(newest.get("ts") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None or at <= pressed:
        return None
    return str(d.get("new_due") or "")[:10] or None


def _commit_rename(root: Path, raw: str, inp: dict, choice_id: str, actor: str, before: int,
                   pressed_at: str = "") -> Dict[str, Any]:
    """One Edit press: new words, a new date, or both. The verdict on both
    halves comes before any write; a refusal writes nothing and names the item.
    A press with a date and no `name` leaves the words alone (the page sends
    the words only when they changed, so a title the plate shortened is never
    written back shortened)."""
    import commitment_state as cs
    import due_set as ds

    cid, capture = _commit_resolve(root, raw)
    if cid is None:
        return _no(NO_TARGET_LINE)
    row = _commit_open(root, cid)
    old = _commit_words(row) or _commit_words(capture) or cid
    raw_due = str(inp.get("due") or inp.get("when") or "").strip()
    if row is None:
        return _no(COMMIT_CLOSED_LINE.format(name=old))
    if "name" in inp or not raw_due:
        if not raw_due and len(old) > COMMIT_CAP:
            # An older page's words-only press keeps its old order: too long
            # to put back is said before an empty box is.
            return _no(COMMIT_LONG_LINE.format(name=old[:80].rstrip() + "..."))
        text = _clean(inp.get("name"), COMMIT_CAP)
        if not text:
            return _no(NEEDS_COMMIT_WORDS_LINE)
    else:
        text = old  # a date alone: the words stay as they are
    new_words = text != old
    if new_words and len(old) > COMMIT_CAP:
        # Review round 1: the writer caps a title, so the Undo could not put
        # longer words back as they were; such a row is left to a chat.
        return _no(COMMIT_LONG_LINE.format(name=old[:80].rstrip() + "..."))
    due = None
    if raw_due:
        fenced = _off_limits_item(root, cid, row, capture)
        if fenced is None:
            return _no(COMMIT_FENCE_UNREAD_LINE.format(name=old))
        if fenced:
            return _no(COMMIT_OFF_LIMITS_LINE.format(name=old))
        planned = _just_planned(root, cid, pressed_at)
        if planned:
            return _no(COMMIT_JUST_PLANNED_LINE.format(name=old, day=planned))
        try:
            v = ds.check_due(str(root), cid, when_iso=raw_due)
        except cs.CommitmentIdError:
            return _no(NO_TARGET_LINE)
        except ValueError:
            return _no(COMMIT_BAD_DATE_LINE.format(name=old))
        status = v.get("status")
        if status == ds.STATUS_PAST:
            return _no(COMMIT_PAST_LINE.format(name=old, day=v.get("due") or raw_due))
        if status == ds.STATUS_NOT_OPEN:
            return _no(COMMIT_CLOSED_LINE.format(name=old))
        if status == ds.STATUS_WAITING:
            return _no(COMMIT_WAITING_LINE.format(name=old))
        if status == ds.STATUS_OK:
            due = v
        elif status != ds.STATUS_UNCHANGED:
            return _no(COMMIT_DUE_LINE.format(name=old), "error")
    if not new_words and due is None:
        return _no((COMMIT_SAME_DUE_LINE if raw_due else SAME_LINE).format(name=old), "already")
    data: Dict[str, Any] = {"hub_choice": choice_id, "kind": "commitment_edit", "action": "rename",
                            "target_id": cid}
    parts, said = [], []
    if new_words:
        try:
            cs.edit_commitment_wording(str(root), cid, edited_by=actor, source_skill=UNDO_SOURCE,
                                       new_title=text, reason=COMMIT_REASON)
        except ValueError:
            return _no(FAILED_LINE.format(name=old), "error")
        parts.append("title")
        data.update(old=old, new=text)
        said.append(f"Changed {old} to {text}.")
    if due is not None:
        try:
            s = ds.set_due(str(root), cid, when_iso=raw_due, actor_id=actor, source_skill=UNDO_SOURCE,
                           reason=COMMIT_DUE_REASON)
        except ValueError:
            s = {"status": "error", "ack": COMMIT_BAD_DATE_LINE.format(name=text)}
        except Exception:  # noqa: BLE001 - review of #1606: a failed date write after the words landed
            # is said as such, with the words' Undo; with no words it wrote nothing.
            s = {"status": "error", "ack": (COMMIT_DUE_FAILED_WHY if parts else COMMIT_DUE_LINE.format(name=old))}
        if s.get("status") == ds.STATUS_SET:
            parts.append("due")
            # `old_due` "" when the item had no date: the payload schema keeps
            # it a string. The Undo puts back exactly what the date row
            # replaced, read from that row (`undo_set_due`), not from here.
            data.update(old_due=str(s.get("prior_due") or "")[:10], new_due=str(s.get("due") or ""),
                        due_seq=s.get("seq"))
            said.append(str(s.get("ack") or ""))
        elif not parts:
            # Nothing landed: the writer judged again under its lock and refused.
            return _no(str(s.get("ack") or COMMIT_DUE_LINE.format(name=old)),
                       "refused" if s.get("status") != "error" else "error")
        else:
            # The words landed and the date did not: the press records only
            # what it wrote, so its Undo takes back exactly that.
            said.append(COMMIT_DUE_PART_LINE.format(why=str(s.get("ack") or "")))
    data["field"] = ",".join(parts)
    line = " ".join(x for x in said if x) + REFRESH
    data["line"] = line
    ev = _record(root, before, data)
    return _ok(line, target_id=cid, seq=ev.get("seq"))


def _touches(ev: dict, cid: str, capture_seq) -> bool:
    """Whether a log row is a hand on commitment `cid`: it names the item by
    id or by its capture seq through the chains the movement derivation
    reads, and it is not a pure bookkeeping mark (a park, a watch, an
    overdue ask), which `commitment_activity` rules is not a touch."""
    import commitment_activity as ca

    ids, seqs = ca.event_commitment_refs(ev)
    if cid not in ids and not (isinstance(capture_seq, int) and capture_seq in seqs):
        return False
    return not ca._is_bookkeeping_update(ev)


def _commit_undo(root: Path, press_id: str, undo_id: str, actor: str, evs: List[dict]) -> Dict[str, Any]:
    """The verdict first, by seq, then the one writer (the cr1#1364 contract).

    Refused, writing nothing and naming the item, when a newer Hub press
    reached the item, when any other row newer than the press's own writer
    row is a hand on it (a chat's wording fix, a close, a date, a note), or
    when the words it shows are no longer the ones the press wrote: an undo
    never erases a newer edit. The Hub's own `hub_edit` rows and the Apply
    receipts are not other work."""
    import commitment_state as cs

    edits = _edits(evs)
    press = next((e for e in edits if press_id and e["data"].get("hub_choice") == press_id
                  and e["data"].get("action") != "undo"), None)
    if press is None or press["data"].get("kind") != "commitment_edit":
        return _no(UNDO_NO_PRESS_LINE)
    pd = press["data"]
    if any(e["data"].get("undoes_choice") == press_id for e in edits):
        return _no(UNDO_ALREADY_LINE, "already")
    if pd.get("action") == "context":
        return _commit_note_undo(root, press, undo_id, actor, evs)
    cid, capture = _commit_resolve(root, str(pd.get("target_id") or ""))
    name = str(pd.get("new") or pd.get("target_id") or "")
    if cid is None:
        return _no(GONE_LINE.format(name=name))
    row = _commit_open(root, cid)
    now_value = _commit_words(row) or name
    if any(_seq(e) > _seq(press) and e["data"].get("target_id") == cid
           and e["data"].get("action") != "undo" for e in edits):
        return _no(UNDO_NEWER_LINE.format(name=now_value))
    own = {s for s in pd.get("wrote_seqs") or [] if isinstance(s, int)}
    since = min(own) if own else _seq(press)
    cap_seq = (capture or {}).get("seq")
    for ev in evs:
        if _seq(ev) <= since or _seq(ev) in own or ev.get("type") in (EVENT, "apply_choices_applied"):
            continue
        if _touches(ev, cid, cap_seq):
            return _no(UNDO_CHANGED_LINE.format(name=now_value))
    parts = str(pd.get("field") or "title").split(",")
    words_part, due_part = "title" in parts, "due" in parts
    old = str(pd.get("old") or "").strip()
    if row is None or (words_part and _commit_words(row) != pd.get("new")):
        return _no(UNDO_CHANGED_LINE.format(name=now_value))
    if due_part and (str(cs.effective_due(_events_path(root), cid) or "")[:10] != str(pd.get("new_due") or "")
                     or not isinstance(pd.get("due_seq"), int)):
        return _no(UNDO_CHANGED_LINE.format(name=now_value))
    if words_part and not old:
        return _no(UNDO_FAILED_LINE.format(name=now_value), "error")
    before = _max_seq(evs)
    if due_part:
        # The date first: its undo refuses when any newer row names the
        # item, and the words' put-back would be one. A refusal here is the
        # first write the Undo would make, so nothing is written.
        import due_set as ds

        back = ds.undo_set_due(str(root), pd["due_seq"], undone_by=actor, source_skill=UNDO_SOURCE)
        if back.get("status") == "already_undone":
            return _no(UNDO_ALREADY_LINE, "already")
        if back.get("status") != "restored":
            return _no(UNDO_CHANGED_LINE.format(name=now_value))
    if words_part:
        try:
            # The same writer puts the old words back as the title; a row that
            # showed its summary reads exactly as before.
            cs.edit_commitment_wording(str(root), cid, edited_by=actor, source_skill=UNDO_SOURCE,
                                       new_title=old, reason=COMMIT_UNDO_REASON)
        except ValueError:  # cr1#1864: the due date may already be put back above
            return _no(UNDO_UNCONFIRMED_LINE.format(name=now_value), "error")
    if words_part and due_part:
        line = f"Put back the wording {old} and the date it had."
    elif due_part:
        line = f"Put back the date on {now_value}."
    else:
        line = f"Put back the wording {old}."
    ev = _record(root, before, {"hub_choice": undo_id, "kind": "commitment_edit", "action": "undo",
                                "target_id": cid, "undoes_choice": press_id, "line": line})
    return _ok(line, target_id=cid, seq=ev.get("seq"))


def _commit_context(root: Path, raw: str, inp: dict, choice_id: str, actor: str, before: int) -> Dict[str, Any]:
    """Add context on a commitment (cr1#1383): one note through the
    commitment-note writer that has a reverser. The fence first: an item
    about a person the owner put off limits takes no note from the Hub, and
    the line names no one. A refusal writes nothing."""
    import commitment_state as cs

    cid, capture = _commit_resolve(root, raw)
    if cid is None:
        return _no(NO_TARGET_LINE)
    row = _commit_open(root, cid)
    name = _commit_words(row) or _commit_words(capture) or cid
    fenced = _off_limits_item(root, cid, row, capture)
    if fenced is None:
        # Review of #1606: who the item is about cannot be told, so no note
        # (fail closed), as for the date.
        return _no(COMMIT_NOTE_FENCE_UNREAD_LINE.format(name=name))
    if fenced:
        return _no(COMMIT_NOTE_OFF_LIMITS_LINE)
    # Review of #1611: a commitment note holds what chat's does (NOTE_CAP),
    # not a thread note's CONTEXT_CAP.
    text = _clean(inp.get("text"), cs.NOTE_CAP)
    if not text:
        return _no(NEEDS_NOTE_LINE)
    out = cs.add_commitment_note(str(root), cid, text, noted_by=actor, source_skill=UNDO_SOURCE,
                                 source_ref=f"{CONTEXT_REF}:{choice_id}" if choice_id else CONTEXT_REF)
    status = out.get("status")
    if status == "already_noted":
        return _no(CONTEXT_SAME_LINE.format(name=name), "already")
    if status == "not_open":
        return _no(COMMIT_CLOSED_LINE.format(name=name))
    if status == "needs_input":
        return _no(NEEDS_NOTE_LINE)
    if status != "noted" or not isinstance(out.get("seq"), int):
        return _no(NO_TARGET_LINE if status == "not_found" else FAILED_LINE.format(name=name),
                   "refused" if status == "not_found" else "error")
    line = CONTEXT_LINE.format(note=text)
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "commitment_edit", "action": "context",
                                "target_id": cid, "new": text, "note_seq": out["seq"], "line": line})
    return _ok(line, target_id=cid, seq=ev.get("seq"))


def _commit_note_undo(root: Path, press: dict, undo_id: str, actor: str, evs: List[dict]) -> Dict[str, Any]:
    """Take back a note added on a commitment from the Hub, through the
    registered reverser (`brain_undo.REVERSERS["commitment_note"]`), whose
    verdict comes first by seq: refused, writing nothing, when anything
    newer touched the item. Allowed on an item whose person went off limits
    after the note (coordinator ruling): it only removes the owner's own
    note. The retraction is the only row it writes besides this press's
    own record."""
    import brain_undo as bu

    pd = press["data"]
    cid, capture = _commit_resolve(root, str(pd.get("target_id") or ""))
    name = _commit_words(_commit_open(root, cid) if cid else None) or _commit_words(capture) \
        or str(pd.get("target_id") or "")
    note_seq = pd.get("note_seq")
    if cid is None or not isinstance(note_seq, int):
        return _no(UNDO_NO_PRESS_LINE)
    if any(_seq(e) > _seq(press) and e["data"].get("target_id") == cid
           and e["data"].get("action") not in ("undo",) for e in _edits(evs)):
        return _no(COMMIT_NOTE_STALE_LINE.format(name=name))
    before = _max_seq(evs)
    try:
        back = bu.REVERSERS["commitment_note"]["reverse"](
            str(root), {"commitment_id": cid, "change_ref": f"seq:{note_seq}", "reason": COMMIT_NOTE_REASON},
            undone_by=actor, source_skill=UNDO_SOURCE)
    except Exception:  # noqa: BLE001 - cr1#1864: the writer may have saved before it raised
        return _no(UNDO_UNCONFIRMED_LINE.format(name=name), "error")
    status = (back or {}).get("status")
    if status == "already_undone":
        return _no(UNDO_ALREADY_LINE, "already")
    if status != "retracted":
        return _no(COMMIT_NOTE_STALE_LINE.format(name=name))
    line = CONTEXT_TAKEN_LINE.format(name=name)
    ev = _record(root, before, {"hub_choice": undo_id, "kind": "commitment_edit", "action": "undo",
                                "target_id": cid, "undoes_choice": str(pd.get("hub_choice") or ""),
                                "line": line})
    return _ok(line, target_id=cid, seq=ev.get("seq"))


# ---------------------------------------------------------------------------
# Add context (cr1#1383)
# ---------------------------------------------------------------------------

def _is_stream(root: Path, tid: str) -> bool:
    """Whether another thread names `tid` as its parent: a work stream."""
    return any(str(t.get("parent_thread_id") or "") == tid for t in _thread_recs(root))


def _thread_context(root: Path, tid: str, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    """A note on a thread, kept in its `notes` through `update_thread`, after
    any notes already there; the row records the notes it replaced."""
    import thread_writer as tw

    rec = _thread(root, tid) if tid else None
    if rec is None or rec.get("kind") == "objective":
        return _no(NO_TARGET_LINE)
    name = _thread_name(rec)
    if not _live(rec):
        return _no(GONE_LINE.format(name=name))
    if _is_stream(root, tid):
        return _no(CONTEXT_STREAM_LINE.format(name=name))
    note = _clean(inp.get("text"), CONTEXT_CAP)
    if not note:
        return _no(NEEDS_NOTE_LINE)
    old = rec.get("notes") if isinstance(rec.get("notes"), str) else ""
    if note in [ln.strip() for ln in old.splitlines()]:
        return _no(CONTEXT_SAME_LINE.format(name=name), "already")
    new = (old.rstrip() + "\n" + note) if old.strip() else note
    try:
        tw.update_thread(root, tid, source_skill=SOURCE_SKILL, notes=new)
    except ValueError:
        return _no(FAILED_LINE.format(name=name), "error")
    line = CONTEXT_LINE.format(note=note)
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "stream_edit", "action": "context",
                                "target_id": tid, "field": "notes", "old": name, "new": note,
                                "old_value": old, "new_value": new, "line": line})
    return _ok(line, target_id=tid, seq=ev.get("seq"))


def _person_context(root: Path, pid: str, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    """A note on a person: one fact in the owner's own word, sourced to the
    press, never stamped as a change the system made on its own."""
    import people_writer as pw

    rec = _person(root, pid) if pid else None
    if rec is None:
        return _no(NO_TARGET_LINE)
    name = str(rec.get("canonical_name") or pid)
    if not _live(rec):
        return _no(GONE_LINE.format(name=name))
    note = _clean(inp.get("text"), CONTEXT_CAP)
    if not note:
        return _no(NEEDS_NOTE_LINE)
    try:
        pw.record_person_fact(root, pid, note, f"{CONTEXT_REF}:{choice_id}", category=CONTEXT_CATEGORY,
                              confidence="high", source_skill=SOURCE_SKILL)
    except (KeyError, ValueError):
        return _no(FAILED_LINE.format(name=name), "error")
    line = CONTEXT_LINE.format(note=note)
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "person_edit", "action": "context",
                                "target_id": pid, "field": "fact", "old": name, "new": note, "line": line})
    return _ok(line, target_id=pid, seq=ev.get("seq"))


def _record_write(ev: dict, kind: str, quoted: str) -> bool:
    """Whether a log row is a write to the record a note sits on: a thread
    writer's row (`thread_...`) naming the thread, or a person writer's row
    (`person_...`, a fact or its retraction) naming the person. Activity on
    the record (a mail, a meeting) is not a write to it."""
    import json

    t = str(ev.get("type") or "")
    family = ("thread_",) if kind == "stream_edit" else ("person_", "entity_fact_")
    return t.startswith(family) and quoted in json.dumps(ev, ensure_ascii=False, default=str)


def _context_undo(root: Path, kind: str, press: dict, undo_id: str, actor: str,
                  evs: List[dict]) -> Dict[str, Any]:
    """Take back a note (the cr1#1364 contract). The verdict first, by seq:
    refused, writing nothing and naming the item, when a newer Hub press
    reached the record, when any newer write to the record landed (other
    than the press's own and the rows a Hub undo wrote: an undo is not a
    touch), or when the note is no longer as the press left it. Then the
    one writer: the thread's notes put back as they were, or the person's
    fact retracted through its registered reverser."""
    import json

    pd = press["data"]
    tid = str(pd.get("target_id") or "")
    edits = _edits(evs)
    if kind == "stream_edit":
        rec = _thread(root, tid)
        name = _thread_name(rec or {}) or str(pd.get("old") or tid)
    else:
        rec = _person(root, tid)
        name = str((rec or {}).get("canonical_name") or pd.get("old") or tid)
    if rec is None:
        return _no(GONE_LINE.format(name=name))
    if any(_seq(e) > _seq(press) and e["data"].get("target_id") == tid
           and e["data"].get("action") not in ("undo", "update") for e in edits):
        return _no(UNDO_NEWER_LINE.format(name=name))
    fact = None
    if kind == "person_edit":
        # The press's own fact row, named on its `hub_edit` row (`wrote_seqs`).
        wrote = {s for s in pd.get("wrote_seqs") or [] if isinstance(s, int)}
        fact = next((e for e in evs if _seq(e) in wrote and e.get("type") == "person_fact_observed"
                     and isinstance(e.get("data"), dict) and e["data"].get("person_id") == tid), None)
        if fact is None:
            return _no(UNDO_NO_PRESS_LINE)
        if any(e.get("type") == "entity_fact_retracted" and isinstance(e.get("data"), dict)
               and e["data"].get("retracts_seq") == _seq(fact) for e in evs):
            return _no(UNDO_ALREADY_LINE, "already")
    own = {s for s in pd.get("wrote_seqs") or [] if isinstance(s, int)}
    since = min(own) if own else _seq(press)
    hub_undid = undo_seqs(evs)
    quoted = json.dumps(tid)
    for ev in evs:
        if _seq(ev) <= since or _seq(ev) in own or _seq(ev) in hub_undid:
            continue
        if ev.get("type") in (EVENT, "apply_choices_applied"):
            continue
        if _record_write(ev, kind, quoted):
            return _no(UNDO_CHANGED_LINE.format(name=name))
    if not _live(rec):
        return _no(UNDO_CHANGED_LINE.format(name=name))
    if kind == "stream_edit" and str(rec.get("notes") or "") != str(pd.get("new_value") or ""):
        return _no(UNDO_CHANGED_LINE.format(name=name))
    before = _max_seq(evs)
    try:
        if kind == "stream_edit":
            import thread_writer as tw

            # The notes as they were; a thread that had none gets none back.
            tw.update_thread(root, tid, source_skill=SOURCE_SKILL, notes=str(pd.get("old_value") or "") or None)
        else:
            import brain_undo as bu

            bu.REVERSERS["entity_fact_structured"]["reverse"](
                str(root), {"person_id": tid, "change_ref": f"seq:{_seq(fact)}", "reason": CONTEXT_UNDO_REASON},
                undone_by=actor, source_skill=SOURCE_SKILL)
    except Exception:  # noqa: BLE001 - cr1#1864: the writer may have saved before it raised
        return _no(UNDO_UNCONFIRMED_LINE.format(name=name), "error")
    line = CONTEXT_TAKEN_LINE.format(name=name)
    ev = _record(root, before, {"hub_choice": undo_id, "kind": kind, "action": "undo", "target_id": tid,
                                "undoes_choice": str(pd.get("hub_choice") or ""), "line": line})
    return _ok(line, target_id=tid, seq=ev.get("seq"))


# ---------------------------------------------------------------------------
# Updates, the order of the priorities, and a star (cr1#1540)
# ---------------------------------------------------------------------------

def _has_update(evs: List[dict], ids: set) -> bool:
    """Whether an update the owner posted, and did not take back, stands on
    one of `ids`: content, as a note is, so the record is in use."""
    edits = _edits(evs)
    undone = {e["data"].get("undoes_choice") for e in edits if e["data"].get("action") == "undo"}
    return any(e["data"].get("action") == "update" and str(e["data"].get("target_id") or "") in ids
               and e["data"].get("hub_choice") not in undone for e in edits)


def _thread_update(root: Path, tid: str, inp: dict, choice_id: str, before: int) -> Dict[str, Any]:
    """An update on a thread: a short note with the day it was posted. The
    press's `hub_edit` row IS the record (one append, no other writer), so an
    update never edits the thread and is no activity on it. It does not
    replace Add context, which writes the thread's notes."""
    rec = _thread(root, tid) if tid else None
    if rec is None or rec.get("kind") == "objective":
        return _no(UPDATE_NO_TARGET_LINE)
    name = _thread_name(rec)
    if not _live(rec):
        return _no(GONE_LINE.format(name=name))
    note = _clean(inp.get("text"), UPDATE_CAP)
    if not note:
        return _no(NEEDS_UPDATE_LINE)
    line = UPDATE_LINE.format(name=name, note=note)
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "stream_edit", "action": "update",
                                "target_id": tid, "old": name, "new": note, "line": line})
    if not ev:
        return _no(FAILED_LINE.format(name=name), "error")
    return _ok(line, target_id=tid, seq=ev.get("seq"))


def _update_undo(root: Path, press: dict, undo_id: str, evs: List[dict]) -> Dict[str, Any]:
    """Take back an update: one row that names the press. Both rows stay in
    the log; the readers leave a taken-back update out."""
    pd = press["data"]
    tid = str(pd.get("target_id") or "")
    name = _thread_name(_thread(root, tid) or {}) or str(pd.get("old") or tid)
    line = UPDATE_TAKEN_LINE.format(name=name)
    ev = _record(root, _max_seq(evs), {"hub_choice": undo_id, "kind": "stream_edit", "action": "undo",
                                       "target_id": tid, "undoes_choice": str(pd.get("hub_choice") or ""),
                                       "line": line})
    if not ev:
        return _no(UNDO_FAILED_LINE.format(name=name), "error")
    return _ok(line, target_id=tid, seq=ev.get("seq"))


def _open_priorities(root: Path) -> List[dict]:
    return [t for t in _thread_recs(root) if _objective_open(t)]


def _priority_order(root: Path, inp: dict, choice_id: str, evs: List[dict], before: int) -> Dict[str, Any]:
    """The owner's order for his priorities. The press names them in order;
    every one must be an open priority now, or nothing is written. The row
    keeps the whole order it leaves and the one it replaced. No priority's
    own record is touched, so none of them reads as moved."""
    import hub_work

    raw = inp.get("order") if isinstance(inp.get("order"), list) else []
    want: List[str] = []
    for i in raw[:ORDER_CAP]:
        i = str(i or "").strip()[:80]
        if i and i not in want:
            want.append(i)
    if not want:
        return _no(ORDER_EMPTY_LINE)
    live = sorted(str(t.get("id") or "") for t in _open_priorities(root))
    if any(i not in live for i in want):
        return _no(ORDER_GONE_LINE)
    old = hub_work.stored_order(evs)
    new = hub_work.ordered(live, want)
    if old and hub_work.ordered(live, old) == new:
        return _no(ORDER_SAME_LINE, "already")
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "priority_edit", "action": "order",
                                "target_id": ORDER_TARGET, "order": new, "old_order": old, "line": ORDER_LINE})
    if not ev:
        return _no(FAILED_LINE.format(name="The order"), "error")
    return _ok(ORDER_LINE, target_id=ORDER_TARGET, seq=ev.get("seq"))


def _order_undo(root: Path, press: dict, undo_id: str, evs: List[dict]) -> Dict[str, Any]:
    """Put the order back as it was before this press: refused when a newer
    order press stands, so an undo never erases a later choice."""
    edits = _edits(evs)
    undone = {e["data"].get("undoes_choice") for e in edits if e["data"].get("action") == "undo"}
    if any(_seq(e) > _seq(press) and e["data"].get("action") == "order"
           and e["data"].get("hub_choice") not in undone for e in edits):
        return _no(ORDER_NEWER_LINE)
    ev = _record(root, _max_seq(evs), {"hub_choice": undo_id, "kind": "priority_edit", "action": "undo",
                                       "target_id": ORDER_TARGET,
                                       "undoes_choice": str(press["data"].get("hub_choice") or ""),
                                       "line": ORDER_UNDO_LINE})
    if not ev:
        return _no(UNDO_FAILED_LINE.format(name="The order"), "error")
    return _ok(ORDER_UNDO_LINE, target_id=ORDER_TARGET, seq=ev.get("seq"))


def _priority_star(root: Path, tid: str, choice_id: str, before: int) -> Dict[str, Any]:
    """A thread starred into a priority: one objective in the thread's own
    name, tied to the thread the way a confirmed suggestion is
    (`thread_cards.suggested_priorities`'s own `confirm` shape), through
    `objective_state.create_objective`. Refused once five priorities are
    open. Its Undo is the priority's own (`_undo`: archived through the one
    archive path while nothing else is on it)."""
    import hub_work
    import objective_state as obs
    from objective_math import _linked_ids

    rec = _thread(root, tid) if tid else None
    if rec is None or rec.get("kind") == "objective":
        return _no(STAR_NO_TARGET_LINE)
    name = _clean(_thread_name(rec), STATEMENT_CAP)
    if not _live(rec):
        return _no(GONE_LINE.format(name=name))
    live = _open_priorities(root)
    held = next((t for t in live if tid in _linked_ids(t.get("objective") or {})), None)
    if held is not None:
        return _no(STAR_ALREADY_LINE.format(name=name), "already", target_id=held.get("id"))
    if len(live) >= hub_work.PRIORITY_SHOWN:
        return _no(STAR_FULL_LINE)
    key = _norm(name)
    if any(_norm((t.get("objective") or {}).get("statement")) == key for t in live):
        return _no(PRIORITY_EXISTS_LINE.format(name=name), "already")
    made = None
    for n in range(1, STAR_NAME_TRIES + 1):
        # A thread's name is its key, so the priority's record takes the
        # thread's name with a word after it; its statement is the name alone.
        label = f"{name} (priority)" if n == 1 else f"{name} (priority {n})"
        try:
            made = obs.create_objective(root, statement=name, name=label,
                                        binding=dict(type="activity", entity_ids=[tid]),
                                        anchor_thread_id=tid, source_skill=SOURCE_SKILL)
            break
        except ValueError as exc:
            if "already exists" not in str(exc):
                return _no(FAILED_LINE.format(name=name), "error")
    if made is None:
        return _no(FAILED_LINE.format(name=name), "error")
    pid = str(made.get("id") or "")
    line = STAR_LINE.format(name=name)
    ev = _record(root, before, {"hub_choice": choice_id, "kind": "priority_edit", "action": "star",
                                "target_id": pid, "thread_id": tid, "new": name, "line": line}, ids={pid})
    return _ok(line, target_id=pid, seq=ev.get("seq"))


def _meeting_attach(root: Path, mid: str, inp: dict, choice_id: str, evs: List[dict],
                    before: int) -> Dict[str, Any]:
    """A link or a file attached to a meeting (cr1#1632): judged whole before
    any write (the press, then the wall, then the same one already standing),
    then the press's one row, which is the record."""
    import meeting_attach as ma

    if not mid:
        return _no(ma.NO_MEETING_LINE)
    att, why = ma.check_press(inp)
    if att is None:
        return _no(why)
    clear = ma.screen(root, att)
    if clear is None:
        return _no(ma.UNREAD_LINE)
    if clear is False:
        return _no(ma.WALLED_LINE, left_out=1)
    key = ma.key_of(att)
    if any(r["meeting_id"] == mid and ma.key_of(r["attached"]) == key for r in ma.standing(evs)):
        return _no(ATTACH_ALREADY_LINE, "already", target_id=mid)
    data: Dict[str, Any] = {"hub_choice": choice_id, "kind": MEETING_KIND, "action": "attach",
                            "target_id": mid, "attached": att, "with_ids": ma.meeting_people(root, mid),
                            "line": ATTACH_LINE}
    start = ma.day_of(inp.get("start"))
    if start:
        data["start"] = start
    ev = _record(root, before, data)
    if not ev:
        return _no(ATTACH_FAILED_LINE, "error")
    return _ok(ATTACH_LINE, target_id=mid, seq=ev.get("seq"))


def _meeting_remove(root: Path, press_id: str, undo_id: str, evs: List[dict]) -> Dict[str, Any]:
    """Remove an attachment: one row naming the attach press. Both rows stay
    in the log; the readers leave a removed one out. Always allowed, a
    person put off limits since included."""
    rows = [e for e in _edits(evs) if e["data"].get("kind") == MEETING_KIND]
    press = next((e for e in rows if e["data"].get("action") == "attach"
                  and str(e["data"].get("hub_choice") or "") == press_id), None) if press_id else None
    if press is None:
        return _no(REMOVE_NO_PRESS_LINE)
    if any(e["data"].get("action") == "undo" and str(e["data"].get("undoes_choice") or "") == press_id
           for e in rows):
        return _no(REMOVE_ALREADY_LINE, "already")
    mid = str(press["data"].get("target_id") or "")
    ev = _record(root, _max_seq(evs), {"hub_choice": undo_id, "kind": MEETING_KIND, "action": "undo",
                                       "target_id": mid, "undoes_choice": press_id, "line": REMOVE_LINE})
    if not ev:
        return _no(UNDO_FAILED_LINE.format(name="It"), "error")
    return _ok(REMOVE_LINE, target_id=mid, seq=ev.get("seq"))


def taken_back_seqs(events: List[dict]) -> set:
    """cr1#1383 slice 4, PR 2: the writer rows of a commitment Edit press
    that was taken back, and of the Undo that took it back. Together they
    changed nothing on the item, so the item's quiet clock
    (`commitment_activity.derive_commitment_movement`) skips them: after an
    Undo the item is exactly as stuck, late or quiet as before the press."""
    rows = [e for e in _edits(events) if e["data"].get("kind") == "commitment_edit"]
    undone = {str(e["data"].get("undoes_choice") or "") for e in rows if e["data"].get("action") == "undo"}
    out: set = set()
    for e in rows:
        d = e["data"]
        if d.get("action") == "undo" or str(d.get("hub_choice") or "") in undone:
            out |= {s for s in d.get("wrote_seqs") or [] if isinstance(s, int)}
    return out


def undo_seqs(events: List[dict]) -> set:
    """READ. The writer rows a Hub undo made (`wrote_seqs` on its `hub_edit`
    row), so a day-count can skip them: an undo is not a touch."""
    out = set()
    for e in events or []:
        d = e.get("data") if isinstance(e, dict) and isinstance(e.get("data"), dict) else {}
        if e.get("type") == EVENT and d.get("action") == "undo":
            out.update(s for s in d.get("wrote_seqs") or [] if isinstance(s, int))
    return out
