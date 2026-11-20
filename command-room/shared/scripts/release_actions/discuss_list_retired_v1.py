#!/usr/bin/env python3
"""discuss_list_retired_v1: empty the retired discuss-later list on update,
once, without asking (cr1#552, the owner's ruling 2026-11-06, option A1).

WHY
---
`show-my-list` was the only surface that rendered an open
`commitment_to_discuss` item in full, and it is gone. The owner ruled what
happens to whatever is still open on a seat: each item becomes a note on its
person or project (the destination the July list retirement named for new
captures), an item with no person or project becomes a task on My Plate, and
the update message says so in one line. Nothing is deleted and nothing is
closed by hand: the ledger keeps every discuss row, and each one is MOVED by
the row written for it.

HOW A MOVE IS RECORDED (and why not through the closure path)
-------------------------------------------------------------
A discuss item was never a `commitment` row, so the commitment index does not
hold it and `commitment_state.close_commitment` refuses its seq. The move is
therefore the destination write itself: the `note` or the `commitment` (a My
Plate task) carries `data.source_event_seq` = the discuss row's seq and the
stamp `plate_helpers.DISCUSS_MOVED_VIA`, and `plate_helpers.open_discuss_items`
treats an item one of those rows points at as moved. A live mute only HID an
item on the old list, so the migration moves muted items too (`include_muted`). One rule, read by the
plan, the apply and the suite. A second run finds nothing open and writes
nothing: idempotent on identity, never on a clock.

WHAT BECOMES A TASK (cr1#973)
-----------------------------
Only an item whose own words read as something to do. The first version made
a task of every item with no person or project, so empty items, bare names
and notes-to-self ("This is the company from the call") landed on My Plate as
open tasks, each read as the owner's own word. Now (`read_item`):
  * no words at all -> kept as a note, never a task;
  * a bare name, a label or a name correction -> a note on that person or
    project when the words name one, else on the owner's own record;
  * words that name a day already gone ("confirm by June 10th", "re-engage
    next week", read from the item's own day) -> kept as a note: the words
    keep the date, and a passed date never becomes a live undated task;
  * words that name a day still ahead -> a task due that day;
  * anything else -> a task with no date, as before.
Seats that ran the first version are repaired by hand, never on their own
(`repair_discuss_list_tasks.py`, preview first).

WHO WRITES
----------
The bridge, through the WRITE door (`workspace_access.py plan run_writer`),
under the customer's writer id. The My Plate task is owned by the workspace's
primary user (`primary_user.resolve_primary_user`); a seat that cannot name
one keeps those items open, counts them, and the line says so rather than
minting a task nobody owns.

stdlib + shared/scripts only. Action contract: `release_actions/__init__.py`.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from plate_helpers import DISCUSS_MOVED_VIA, open_discuss_items  # noqa: E402

SOURCE_SKILL = "command-room-update-bridge"
MIGRATION_ID = "discuss_list_retired_v1"
APPLIED_EVENT = "workspace_migration_applied"

STATUS_NOTHING_OPEN = "nothing_open"
STATUS_PENDING = "pending"
STATUS_ADJUDICATED = "adjudicated"
STATUS_APPLIED = "applied"
STATUS_PARTIAL = "partial"

#: Stamped on the pointer note written when an item's words were already on My
#: Plate (the write-time capture-once filter refuses a second task with the same
#: title); the note sits on the owner's own record and marks the item moved.
MOVED_TO_EXISTING_TASK = "my_plate_existing"

#: Evidence the moved rows carry, in the customer's words.
MOVED_NOTE_LINE = "Moved from your discuss-later list when it retired."


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _events(workspace_root) -> List[Dict[str, Any]]:
    from events_io import load_events_owner_scoped

    events, _skipped = load_events_owner_scoped(workspace_root)
    return list(events)


def _entities(workspace_root) -> dict:
    from entities_io import unwrap_entities

    path = Path(str(workspace_root)) / "_hq" / "data" / "entities.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return unwrap_entities(raw) if isinstance(raw, dict) else {}


def _adjudicated(workspace_root) -> bool:
    try:
        import migration_adjudication

        return bool(migration_adjudication.is_suppressed(workspace_root, MIGRATION_ID))
    except Exception:  # noqa: BLE001 - an unreadable ledger is not a suppression
        return False


def _summary(ev: Dict[str, Any]) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for key in ("summary", "title", "text"):
        val = str(d.get(key) or "").strip()
        if val:
            return val
    return ""


# --- cr1#973: what an item's words read as ----------------------------------

#: How an item that did not become a task was read (stamped on its note as
#: `data.read_as`, and what the repair lists).
READ_NO_WORDS = "no_words"
READ_LABEL = "label"
READ_DATE_PASSED = "date_passed"
READ_TASK = "task"

#: The text an item with no words carries on its note: it names its own entry
#: so two of them never share one.
UNTITLED_TEXT = "Discuss-later item with no words recorded (entry {seq})"

#: A note-to-self or a correction starts like this, never like a task. Kept
#: narrow on purpose (review): "We are late on the invoice, chase it" and
#: "Note to self: send the deck" are tasks, so only "this/that is" and the
#: explicit name and spelling phrasings count.
_LABEL_LEAD_RE = re.compile(
    r"^(?:(?:this|that)\s*(?:is|was|'s|’s)\b"
    r"|(?:aka|a\.k\.a)\b"
    r"|(?:also known as|known as|same as|not to be confused|spelled|spelt|spelling"
    r"|correction|corrected|correct name|correct spelling|the correct|real name"
    r"|full name|name is|goes by)\b)",
    re.IGNORECASE)
#: A name correction anywhere in the words.
_CORRECTION_RE = re.compile(
    r"\b(?:is|was|should be)\s+(?:spelled|spelt)\b|\b(?:name|spelling)\s+(?:is|should be)\b",
    re.IGNORECASE)
#: A token that may sit inside a name ("Sample & Co", "J. Sample", "St. Pat").
_NAME_JOINERS = {"&", "and", "of", "de", "la", "van", "von", "del", "da", "-"}
#: A first word that makes Title Case words a task, not a name ("Call Sam",
#: "Send Proposal To Acme").
_TASK_VERBS = frozenset((
    "add", "approve", "ask", "book", "build", "buy", "call", "cancel", "chase", "check",
    "circle", "close", "confirm", "contact", "create", "discuss", "draft", "email", "file",
    "find", "finish", "fix", "follow", "get", "introduce", "intro", "invite", "invoice",
    "look", "make", "meet", "move", "order", "organize", "pay", "pick", "ping", "plan",
    "prep", "prepare", "read", "reach", "remind", "renew", "research", "return", "review",
    "schedule", "send", "set", "share", "sign", "start", "submit", "talk", "tell", "test",
    "text", "think", "update", "visit", "write"))


def _entity_names(entities: dict) -> Dict[str, tuple]:
    """Every name the workspace knows, folded, to `(kind, id)`: a person or a
    thread names where its note goes; an org names nobody a note can sit on,
    so it maps to `(None, None)` and the note goes to the owner's record."""
    out: Dict[str, tuple] = {}
    for key, kind in (("people", "person"), ("threads", "thread"), ("orgs", None)):
        rows = entities.get(key) if isinstance(entities, dict) else None
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            names = [row.get("canonical_name"), row.get("name"), row.get("display_name")]
            aliases = row.get("aliases")
            if isinstance(aliases, list):
                names.extend(aliases)
            for name in names:
                folded = " ".join(str(name or "").split()).casefold()
                if folded and folded not in out:
                    out[folded] = (kind, str(row["id"]) if kind else None)
    return out


def _bare(text: str) -> str:
    return " ".join(text.strip().rstrip(".!?:;,").split())


def _reads_as_name(text: str) -> bool:
    """Two to four words shaped like a person's name and nothing else: each
    a capital then lowercase letters (joiners and initials aside), and the
    first not a task verb. "Sam Sample" is a name; "Call Sam", "Renew AWS
    Contract", "Q4 Review" and a single word ("Invoice") are not (review)."""
    words = _bare(text).split()
    if not 2 <= len(words) <= 4 or words[0].casefold() in _TASK_VERBS:
        return False
    named = 0
    for w in words:
        if w.casefold() in _NAME_JOINERS:
            continue
        core = w.replace("'", "").replace("’", "").replace("-", "").rstrip(".")
        initial = len(core) == 1 and core.isupper()
        if not initial and not (core.isalpha() and core[:1].isupper() and core[1:].islower()):
            return False
        named += 1
    return named >= 2


_MONTH_ALT = "(" + "|".join(sorted(
    ("jan", "january", "feb", "february", "mar", "march", "apr", "april", "may",
     "jun", "june", "jul", "july", "aug", "august", "sep", "sept", "september",
     "oct", "october", "nov", "november", "dec", "december"),
    key=len, reverse=True)) + r")\b\.?"
_DAY_ALT = r"(\d{1,2})(?:st|nd|rd|th)?\b"
_YEAR_ALT = r"(?:,?\s+(\d{4})\b)?"
_MONTH_DAY_RES = (
    (re.compile(rf"\b{_MONTH_ALT}\s+{_DAY_ALT}{_YEAR_ALT}", re.IGNORECASE), (1, 2, 3)),
    # Day first needs an ordinal or "of" ("10th June", "10 of June"), so
    # "Top 10 sept leads" names no day (review).
    (re.compile(r"\b(\d{1,2})(?:(?:st|nd|rd|th)\s+(?:of\s+)?|\s+of\s+)" + _MONTH_ALT + _YEAR_ALT,
                re.IGNORECASE), (2, 1, 3)),
)
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
#: A slashed date counts only with a year, or right after by/on/due/before/
#: until: "Pay 1/3 deposit" is a fraction, not a date (review).
_SLASH_RE = re.compile(
    r"(?:(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?![\d/]))"
    r"|(?:\b(?:by|on|due|before|until)\s+(\d{1,2})/(\d{1,2})(?![\d/]))")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b")


def _month_index(word: str) -> int:
    w = word.rstrip(".").lower()
    for i, names in enumerate((("jan", "january"), ("feb", "february"), ("mar", "march"),
                               ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
                               ("aug", "august"), ("sep", "sept", "september"),
                               ("oct", "october"), ("nov", "november"),
                               ("dec", "december")), start=1):
        if w in names:
            return i
    return 0


def _on_or_after(year: Optional[str], month: int, day: int, anchor: _dt.date):
    """The day named by a month and a day: with a year, that day; without
    one, that day in the item's own year, unless it falls more than three
    months before the item's day ("Jan 5" written in December is next year;
    "June 10th" written on June 15 is the day just gone)."""
    try:
        got = _dt.date(int(year) if year else anchor.year, month, day)
    except ValueError:
        return None
    if not year and got < anchor - _dt.timedelta(days=90):
        try:
            got = _dt.date(anchor.year + 1, month, day)
        except ValueError:
            return None
    return got


def _week_friday(day: _dt.date) -> _dt.date:
    return day + _dt.timedelta(days=(4 - day.weekday()) % 7) if day.weekday() <= 4 else day


def _month_end(year: int, month: int) -> _dt.date:
    nxt = _dt.date(year + (month // 12), month % 12 + 1, 1)
    return nxt - _dt.timedelta(days=1)


def named_day(text: str, anchor: Optional[_dt.date]) -> Optional[_dt.date]:
    """The day the words name as a due, read from the item's own day
    (`anchor`), or None. Only days that can be pinned: a written date, today,
    tomorrow, a weekday, this or next week, this or next month. A phrase
    about the past ("the call from last week") is not a due, and neither is
    a written date before the item's own day ("the recap of the Nov 2 call",
    written Nov 10): that is a mention (review)."""
    if anchor is None or not text:
        return None
    low = text.lower()
    for m in _ISO_RE.finditer(low):
        try:
            got = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if got >= anchor:
            return got
    for rx, (mi, di, yi) in _MONTH_DAY_RES:
        for m in rx.finditer(text):
            if m.group(mi).lower() == "may" and not m.group(mi)[0].isupper():
                continue  # "I may 2 or 3": the verb, not the month
            got = _on_or_after(m.group(yi), _month_index(m.group(mi)), int(m.group(di)), anchor)
            if got and got >= anchor:
                return got
    for m in _SLASH_RE.finditer(low):
        month, day, year = ((m.group(1), m.group(2), m.group(3)) if m.group(1)
                            else (m.group(4), m.group(5), None))
        if year and len(year) == 2:
            year = "20" + year
        got = _on_or_after(year, int(month), int(day), anchor) if 1 <= int(month) <= 12 else None
        if got and got >= anchor:
            return got
    if re.search(r"\b(?:today|tonight)\b", low):
        return anchor
    if re.search(r"\btomorrow\b", low):
        return anchor + _dt.timedelta(days=1)
    if re.search(r"\bnext\s+week\b", low):
        return _week_friday(anchor) + _dt.timedelta(days=7) if anchor.weekday() <= 4 \
            else _week_friday(anchor + _dt.timedelta(days=7 - anchor.weekday()))
    if re.search(r"\b(?:this\s+week|end\s+of\s+(?:the\s+)?week|eow)\b", low):
        return _week_friday(anchor)
    if re.search(r"\bnext\s+month\b", low):
        y, mo = (anchor.year + 1, 1) if anchor.month == 12 else (anchor.year, anchor.month + 1)
        return _month_end(y, mo)
    if re.search(r"\b(?:this\s+month|end\s+of\s+(?:the\s+)?month|eom)\b", low):
        return _month_end(anchor.year, anchor.month)
    m = _WEEKDAY_RE.search(low)
    if m and not re.search(r"\blast\s+" + m.group(1) + r"\b", low):
        ahead = (_WEEKDAYS.index(m.group(1)) - anchor.weekday()) % 7 or 7
        return anchor + _dt.timedelta(days=ahead)
    return None


def _day_of(value, workspace_root) -> Optional[_dt.date]:
    try:
        from commitment_state import _now_calendar_day

        return _now_calendar_day(value, str(workspace_root) if workspace_root else None)
    except Exception:  # noqa: BLE001 - an unreadable stamp names no day
        return None


def read_item(ev: Dict[str, Any], entities: dict, *, today: Optional[_dt.date],
              workspace_root=None) -> Dict[str, Any]:
    """What one open item's own words read as (cr1#973). Pure.

    Returns `{read_as, due, name_target: (kind, id)}`. `read_as` is
    `no_words`, `label` (a bare name, a note-to-self, a name correction),
    `date_passed` (the words name a day before `today`) or `task`. `due` is
    the day a task is due (ISO), only when the words name one still ahead.
    `name_target` is where a label's note goes when its words ARE a known
    person or project."""
    out: Dict[str, Any] = {"read_as": READ_TASK, "due": None, "name_target": (None, None)}
    text = _summary(ev)
    if not re.search(r"\w", text):
        out["read_as"] = READ_NO_WORDS
        return out
    names = _entity_names(entities)
    hit = names.get(_bare(text).casefold())
    if hit is not None:
        out["read_as"] = READ_LABEL
        out["name_target"] = hit
        return out
    if _reads_as_name(text) or _LABEL_LEAD_RE.match(text.strip()) or _CORRECTION_RE.search(text):
        out["read_as"] = READ_LABEL
        return out
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    day = None
    stored = str(d.get("due") or "").strip()
    if stored:
        day = _day_of(stored, workspace_root)
    if day is None:
        day = named_day(text, _day_of(ev.get("ts"), workspace_root))
    if day is not None:
        if today is not None and day < today:
            out["read_as"] = READ_DATE_PASSED
        else:
            out["due"] = day.isoformat()
    return out


def plan(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """What a run would do. A pure read: nothing is written.

    Returns `{migration_id, status, items: [{seq, summary, target_kind,
    target_id, read_as, due}], counts: {to_note, to_plate, kept_as_note,
    untitled}}`. `target_kind`/`target_id` say where a note goes (None for a
    task, or for a kept note that sits on the owner's record). `status` is
    `nothing_open` (the common answer), `adjudicated` (a durable skip or
    applied record for this id) or `pending`."""
    out: Dict[str, Any] = {"migration_id": MIGRATION_ID, "items": [],
                           "counts": {"to_note": 0, "to_plate": 0, "kept_as_note": 0,
                                      "untitled": 0}}
    if _adjudicated(workspace_root):
        out["status"] = STATUS_ADJUDICATED
        return out
    from orphan_note import resolve_note_target

    events = _events(workspace_root)
    entities = _entities(workspace_root)
    now = (now_iso or "").strip() or _now_iso()
    today = _day_of(now, workspace_root)
    open_items = open_discuss_items(events, now, include_muted=True)
    for ev in open_items:
        summary = _summary(ev)
        kind, target_id = resolve_note_target(ev, entities)
        read = read_item(ev, entities, today=today, workspace_root=workspace_root)
        row = {"seq": ev.get("seq"), "summary": summary,
               "target_kind": kind, "target_id": target_id,
               "read_as": read["read_as"], "due": None}
        out["items"].append(row)
        if not summary:
            out["counts"]["untitled"] += 1
        if kind:
            out["counts"]["to_note"] += 1
        elif read["read_as"] == READ_TASK:
            row["due"] = read["due"]
            out["counts"]["to_plate"] += 1
        else:
            # Not a task (cr1#973): a note on the person or project its words
            # name, else on the owner's record (target left None).
            row["target_kind"], row["target_id"] = read["name_target"]
            row["kept_as_note"] = True
            out["counts"]["kept_as_note"] += 1
    out["status"] = STATUS_PENDING if out["items"] else STATUS_NOTHING_OPEN
    return out


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary_line(counts: Dict[str, int]) -> str:
    """The ONE line the update message says; "" when nothing moved."""
    noted = int(counts.get("noted") or 0)
    tasked = int(counts.get("tasked") or 0)
    on_plate = int(counts.get("already_on_plate") or 0)
    left = int(counts.get("left_open") or 0)
    kept = int(counts.get("kept_as_note") or 0)
    parts: List[str] = []
    if noted:
        parts.append(f"{_plural(noted, 'item', 'items')} became "
                     + ("a note on its person or project" if noted == 1
                        else "notes on their people or projects"))
    if tasked:
        parts.append(f"{_plural(tasked, 'item', 'items')} became "
                     + ("a My Plate task" if tasked == 1 else "My Plate tasks"))
    if kept:
        parts.append(f"{_plural(kept, 'item was', 'items were')} kept as "
                     + ("a note because it was not a task" if kept == 1
                        else "notes because they were not tasks"))
    if on_plate:
        parts.append(f"{_plural(on_plate, 'item was', 'items were')} already on My Plate")
    if not parts and not left:
        return ""
    line = "Your discuss-later list is retired"
    if parts:
        line += ": " + " and ".join(parts)
    line += "."
    if left:
        line += (f" {_plural(left, 'item', 'items')} could not be moved because this "
                 "workspace has no owner on record; say `what's on my plate` once "
                 "it does.")
    return line


def _moved_task_landed(workspace_root, seq) -> bool:
    """Read back: did a My Plate task pointing at `seq` reach the ledger?
    `create_personal_task` answers `created` even when the write-time
    capture-once filter (`atomic_write`, keyed on `(source_ref, title)`)
    refused the row because the plate already holds those words, so the
    only honest answer is the ledger's own."""
    for ev in _events(workspace_root):
        if ev.get("type") != "commitment":
            continue
        d = ev.get("data") or {}
        if d.get("source") == DISCUSS_MOVED_VIA and d.get("source_event_seq") == seq:
            return True
    return False


def discuss_list_retired_v1(events_jsonl_path, workspace_root,
                            detector_context: Optional[Dict[str, Any]] = None
                            ) -> Dict[str, Any]:
    """The action: move every open item once, log once, or do nothing and say why."""
    ctx = dict(detector_context or {})
    try:
        from receipts import require_writer_identity

        require_writer_identity(workspace_root=workspace_root)
    except ImportError:
        pass
    planned = plan(workspace_root)
    context: Dict[str, Any] = {"migration_id": MIGRATION_ID, "status": planned["status"],
                               "counts": {"noted": 0, "tasked": 0, "already_on_plate": 0,
                                          "kept_as_note": 0, "left_open": 0},
                               "summary_line": ""}
    if planned["status"] != STATUS_PENDING:
        return {"success": True, "ran": False, "context": context, "error": None,
                "fallback_prompt": None}

    from commitment_state import create_personal_task
    from event_gate import append_event
    from primary_user import resolve_primary_user

    events_path = (Path(str(events_jsonl_path)) if events_jsonl_path
                   else Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl")
    owner = str(resolve_primary_user(workspace_root) or "").strip()
    counts = context["counts"]
    moved: List[Dict[str, Any]] = []
    for item in planned["items"]:
        seq, summary = item["seq"], item["summary"]
        # An untitled item names its own entry so two of them never share a
        # note's words.
        text = summary or UNTITLED_TEXT.format(seq=seq)
        kept = bool(item.get("kept_as_note"))
        if item["target_kind"] or (kept and owner):
            # A note on the item's person or project; an item that is not a
            # task and names neither sits on the owner's own record (cr1#973).
            note_ev: Dict[str, Any] = {
                "type": "note", "source_skill": SOURCE_SKILL,
                "data": {"summary": text, "via": DISCUSS_MOVED_VIA,
                         "source_event_seq": seq, "evidence": MOVED_NOTE_LINE},
            }
            if kept:
                note_ev["data"]["read_as"] = item["read_as"]
            if item["target_kind"] == "thread":
                note_ev["primary_thread_id"] = item["target_id"]
            else:
                note_ev["data"]["person_id"] = item["target_id"] or owner
            append_event(events_path, [note_ev], holder=SOURCE_SKILL)
            counts["kept_as_note" if kept else "noted"] += 1
            moved.append({"seq": seq, "became": "note",
                          "target_kind": item["target_kind"] or "person",
                          "target_id": item["target_id"] or owner,
                          **({"read_as": item["read_as"]} if kept else {})})
            continue
        if not owner:
            counts["left_open"] += 1
            continue
        create_personal_task(str(workspace_root), title=text[:240], owner_id=owner,
                             source_event_seq=seq, source_skill=SOURCE_SKILL,
                             source=DISCUSS_MOVED_VIA, due=item.get("due"))
        if _moved_task_landed(workspace_root, seq):
            counts["tasked"] += 1
            moved.append({"seq": seq, "became": "task", "owner_id": owner})
            continue
        # The plate already holds these words (the write was refused by the
        # capture-once filter, never a second copy). The item is not dropped:
        # a pointer note on the owner's own record marks it moved and says
        # where the words live.
        note_ev = {
            "type": "note", "source_skill": SOURCE_SKILL,
            "data": {"summary": text, "via": DISCUSS_MOVED_VIA,
                     "source_event_seq": seq, "person_id": owner,
                     "moved_to": MOVED_TO_EXISTING_TASK,
                     "evidence": "Already on My Plate when the discuss-later list retired."},
        }
        append_event(events_path, [note_ev], holder=SOURCE_SKILL)
        counts["already_on_plate"] += 1
        moved.append({"seq": seq, "became": "note", "target_kind": "person",
                      "target_id": owner, "moved_to": MOVED_TO_EXISTING_TASK})

    context["summary_line"] = summary_line(counts)
    if counts["left_open"]:
        # Items a seat with no owner could not move stay open and are said
        # out loud; no applied row, so the next update tries them again once
        # the workspace names its owner.
        context["status"] = STATUS_PARTIAL
        return {"success": True, "ran": bool(moved), "context": context, "error": None,
                "fallback_prompt": None}

    row = {"type": APPLIED_EVENT, "source_skill": SOURCE_SKILL,
           "data": {"migration_id": MIGRATION_ID, "target_file": "_hq/data/events.jsonl",
                    "from_version": ctx.get("from_version"),
                    "to_version": ctx.get("to_version"),
                    "actor": SOURCE_SKILL, "moved": moved,
                    "noted": counts["noted"], "tasked": counts["tasked"],
                    "already_on_plate": counts["already_on_plate"],
                    "kept_as_note": counts["kept_as_note"]}}
    stamped = append_event(events_path, [row], holder=SOURCE_SKILL)
    context.update({"status": STATUS_APPLIED,
                    "event_seq": (stamped[0].get("seq") if stamped else None)})
    return {"success": True, "ran": True, "context": context, "error": None,
            "fallback_prompt": None}


__all__ = [
    "APPLIED_EVENT", "MIGRATION_ID", "MOVED_NOTE_LINE", "MOVED_TO_EXISTING_TASK", "SOURCE_SKILL",
    "STATUS_ADJUDICATED", "STATUS_APPLIED", "STATUS_NOTHING_OPEN", "STATUS_PARTIAL",
    "STATUS_PENDING", "READ_DATE_PASSED", "READ_LABEL", "READ_NO_WORDS", "READ_TASK",
    "UNTITLED_TEXT",
    "discuss_list_retired_v1", "named_day", "plan", "read_item", "summary_line",
]
