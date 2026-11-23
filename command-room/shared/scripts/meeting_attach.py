#!/usr/bin/env python3
"""meeting_attach: a link or a file attached to a meeting on the Hub
(cr1#1632, slice 2; the design board's E3, "Attached").

WHAT THE OWNER DOES
On a meeting's page he pastes a link (http or https only) or picks a file,
and presses Attach. The press is saved in the page's own store and nothing
reaches the book until Apply. A file's bytes go first to the Hub page's own
file store (the artifact's `assets` capability: an owner-only store kept
with the page, up to 20 MB a file, PDFs, pictures, text, CSV, Markdown,
JSON and video only); the press carries the file's name, size, type and the
store's id for it, never the bytes. Remove takes an attachment back: Undo
while the press waits, a remove press once it landed.

WHERE IT IS KEPT
No new store. The press's own `hub_edit` row is the record (the way an
update on a thread is, cr1#1540): `hub_edit.apply_one` writes it through the
one append path, kind `meeting_edit`, action `attach`, naming the meeting by
its calendar id as `target_id`, with the attachment under `attached` and the
meeting's resolved attendees under `with_ids` (from the newest prep brief's
receipt for that meeting, walled people cut). A Remove is one more row
naming the press (`undoes_choice`); both rows stay in the log and the
readers here leave a removed one out. The row names no thread, so an
attachment is no activity on any thread or person.

THE WALL
A link or a file whose name or address names a person the owner keeps off
limits or on the personal lane is refused before anything is written: the
same screen the imports use (`import_wall.screen_import`, cr1#1577), so one
rule decides who a free-text name names. The refusal is counted
(`left_out`) and its line names no one. A wall that cannot be read refuses
(fail closed). The readers screen again on every read, so an attachment
whose name names someone put off limits later is left out of the page and
the brief, and counted.

WHO READS IT (this module is READ ONLY, on the read door)
  * the Hub's own lists feed: `by_meeting`, every meeting's attachments as
    `attached` on `feed/mine` (`cockpit_helpers.feed_mine`), the feed Apply
    refreshes right after a Hub press lands;
  * call-prep's brief: `for_brief`, one "Attached" section for
    `prep_pipeline.assemble_prep_sections(extra_sections=...)`, listing what
    was attached to this meeting and, newest first, to earlier meetings with
    the same people.

THE FILE PATH'S SEAM (cr1#1586)
A file stays where the owner put it, in the Hub's own store: the link half
of the bring-in door's rule (link by default). The copy half, landing the
bytes in the meeting's project folder or, with no project, in `_hq/notes/`
(cr1#1581), is the bring-in door's (`bring_in.py`, not on main when this
was built). `copy_home` names that folder; nothing here copies.

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote_plus
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: The Hub press kind and its verbs (`cockpit_helpers._edit_verbs`).
KIND = "meeting_edit"
VERBS = frozenset({"attach", "undo"})
#: The `hub_edit` row's own type (`hub_edit.EVENT`), named here so a read
#: never imports the writer.
EVENT = "hub_edit"
LINK = "link"
FILE = "file"
NAME_CAP = 160
LINK_CAP = 2000
#: The page's file store takes up to 20 MiB a file (the `assets` capability).
SIZE_CAP = 20 * 1024 * 1024
#: The types the page keeps: the file store's own set, less fonts, styles,
#: scripts and SVG (not documents an owner attaches to a meeting).
FILE_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp",
                        "text/plain", "text/csv", "text/markdown", "application/json",
                        "video/mp4", "video/webm"})
#: The file store's id for a file: 32 lower-case hex characters.
ASSET_RE = re.compile(r"^[0-9a-f]{32}$")
_LINK_RE = re.compile(r"^https?://[^\s<>\"'\\/?#]+[^\s<>\"'\\]*$", re.IGNORECASE)
#: A start as the calendar gives it: a date, or a date and time with its zone.
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-][0-9:]+)?)?$")
#: Most attachments one brief lists.
BRIEF_CAP = 12
HEADING = "Attached"

NO_MEETING_LINE = "That press names no meeting; nothing was written."
BAD_LINK_LINE = "A link starts with http:// or https://; nothing was written."
BAD_FILE_LINE = "That file is not one the Hub keeps; nothing was written."
BIG_FILE_LINE = "That file is over 20 MB; nothing was written. Paste a link to it instead."
NO_NAME_LINE = "That file has no name; nothing was written."
#: Names no one: the attachment's own name may carry the walled person's.
WALLED_LINE = ("Not attached: its name or address names someone you keep off limits or personal. "
               "Nothing was written.")
UNREAD_LINE = ("The Hub cannot tell right now who this names, so it was not attached; nothing was written. "
               "Try again later.")
READ_UNREAD_LINE = ("The attachments could not be checked against the people you keep off limits, "
                    "so none are listed.")


# ---------------------------------------------------------------------------
# The press
# ---------------------------------------------------------------------------

def _clean(value: Any, cap: int) -> str:
    text = "".join(c if c.isprintable() else " " for c in str(value or ""))
    return " ".join(text.split())[:cap].strip()


def link_name(url: str) -> str:
    """A link's name when the owner gave none: its host and the last part of
    its path, as the page shows it."""
    rest = url.split("://", 1)[-1]
    host = re.split(r"[/?#]", rest, maxsplit=1)[0]
    path = rest[len(host):].split("?", 1)[0].split("#", 1)[0]
    last = [p for p in path.split("/") if p]
    return _clean(host + ("/" + last[-1] if last else ""), NAME_CAP)


def check_press(inp: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """`(attachment, "")` for a well-made press, or `(None, the line)`. The
    attachment is `{kind: link, name, url}` or `{kind: file, name, asset,
    size, content_type}`. Nothing else the page sent is kept."""
    inp = inp if isinstance(inp, dict) else {}
    kind = str(inp.get("kind") or "").strip().lower()
    if kind == LINK:
        url = str(inp.get("url") or "").strip()
        if len(url) > LINK_CAP or not _LINK_RE.match(url):
            return None, BAD_LINK_LINE
        name = _clean(inp.get("name"), NAME_CAP) or link_name(url)
        return {"kind": LINK, "name": name, "url": url}, ""
    if kind == FILE:
        asset = str(inp.get("asset") or "").strip()
        ctype = str(inp.get("content_type") or inp.get("type") or "").strip().lower()
        if not ASSET_RE.match(asset) or ctype not in FILE_TYPES:
            return None, BAD_FILE_LINE
        size = inp.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            return None, BAD_FILE_LINE
        if size > SIZE_CAP:
            return None, BIG_FILE_LINE
        # The name as picked: a path's folders, if a view ever sent them, left out.
        name = _clean(re.split(r"[\\/]", str(inp.get("name") or ""))[-1], NAME_CAP)
        if not name:
            return None, NO_NAME_LINE
        return {"kind": FILE, "name": name, "asset": asset, "size": size, "content_type": ctype}, ""
    return None, BAD_LINK_LINE if "url" in inp else BAD_FILE_LINE


def day_of(value: Any) -> Optional[str]:
    """The meeting's start as the page sent it, when it reads as a time."""
    s = str(value or "").strip()[:40]
    return s if _DAY_RE.match(s) else None


def key_of(att: Dict[str, Any]) -> str:
    """What makes two attachments the same one: the link; a file by its name
    and size, so the same file picked twice is attached once (cr1#1632
    review), whatever store id each pick was given."""
    if att.get("kind") == LINK:
        return LINK + ":" + str(att.get("url") or "")
    return FILE + ":" + str(att.get("name") or "").casefold() + "|" + str(att.get("size") or "")


#: How many times a value is decoded: a link encoded twice is still read.
DECODE_ROUNDS = 3
_FOLD = re.compile(r"[\W_]+")
#: Between a letter and a digit, either way round (cr1#1639 review round 2).
_DIGITS_APART = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")


#: Review round 3: where one piece of a link or a file name ends and the
#: next begins, and a file name's extension.
_PIECES = re.compile(r"[\s/?&=#]+")
_EXT = re.compile(r"\.[A-Za-z0-9]{1,8}$")
#: Review round 4: an all-small-letters run joined by `-`, `_` or `.` inside a
#: piece that has a capital elsewhere ("Final-sample-rio", "IMG_sample-rio").
_LOWER_RUN = re.compile(r"(?<![A-Za-z0-9])[a-z0-9]+(?:[-_.][a-z0-9]+)+(?![A-Za-z0-9])")


def forms_of(value: Any) -> List[str]:
    """Every form the wall reads a name or a link in (cr1#1632 review): as
    written, URL-decoded (`%20`, `+`, `%40`, up to `DECODE_ROUNDS` times),
    and each of those with every run of punctuation, underscores and spaces
    folded to one space, so "Rio%20Sample", "q=rio+sample", "Rio__Sample",
    "Rio + Sample" and a dash between the names read as the name they spell.

    cr1#1639 review round 2: each folded form also with letters and digits
    set apart ("RioSample2026", "riosample1" read as "RioSample 2026"), and,
    for a form written all in small letters (a slug: "sample-rio",
    "rio-j-sample"), a copy with every word capitalised, so it meets the
    wall's capitalised "Last First" and middle-initial forms. A name written
    with its own capitals is read as written ("Sample drew it.pdf" is
    no walled "Drew Sample")."""
    raw = str(value or "")
    seen: List[str] = []
    cur = raw
    for _ in range(DECODE_ROUNDS + 1):
        if cur not in seen:
            seen.append(cur)
        nxt = unquote_plus(cur)
        if nxt == cur:
            break
        cur = nxt
    out = list(seen)

    def add(form: str) -> None:
        if form and form not in out:
            out.append(form)

    def title(f: str) -> str:
        return " ".join(w[:1].upper() + w[1:] for w in f.split())

    for s in seen:
        folded = " ".join(_FOLD.sub(" ", s).split())
        apart = " ".join(_DIGITS_APART.sub(" ", folded).split())
        for f in (folded, apart):
            add(f)
            if f == f.lower():
                add(title(f))
        # review round 3: one capital elsewhere must not switch the copy off,
        # so each piece on its own (a URL's path, query and `=` pieces, a word
        # of a file name, and the name without its extension) gets the
        # title-cased copy when it is written all in small letters.
        for piece in _PIECES.split(s):
            # review round 4: and every all-small-letters run joined by a
            # hyphen, underscore or dot inside a piece with a capital elsewhere
            runs = [m.group(0) for m in _LOWER_RUN.finditer(piece)] if piece != piece.lower() else []
            for cand in [piece, _EXT.sub("", piece)] + runs:
                if not cand or cand != cand.lower() or not re.search(r"[a-z]", cand):
                    continue
                pf = " ".join(_FOLD.sub(" ", cand).split())
                for f in (pf, " ".join(_DIGITS_APART.sub(" ", pf).split())):
                    if " " in f:
                        add(title(f))
    return out


# ---------------------------------------------------------------------------
# The wall
# ---------------------------------------------------------------------------

def _screen_items(workspace_root, atts: List[Dict[str, Any]]) -> Optional[List[bool]]:
    """For each attachment, whether it names nobody walled (by name, joined
    name or address, in its name or its link); None when the wall cannot be
    read. One screen, the imports' own."""
    import import_wall

    items = [{"n": str(i), "forms": forms_of(a.get("name")) + forms_of(a.get("url"))}
             for i, a in enumerate(atts)]
    got = import_wall.screen_import(workspace_root, {"attachments": items})
    if not isinstance(got, dict) or not got.get("ok"):
        return None
    kept = {str(x.get("n")) for x in (got.get("items") or {}).get("attachments") or [] if isinstance(x, dict)}
    return [str(i) in kept for i in range(len(atts))]


def screen(workspace_root, att: Dict[str, Any]) -> Optional[bool]:
    """True when the attachment names nobody the owner keeps off limits or
    personal, False when it names one, None when that cannot be told."""
    got = _screen_items(workspace_root, [att])
    return None if got is None else got[0]


def _walled_ids(workspace_root) -> Optional[set]:
    import import_wall

    w = import_wall.wall(workspace_root)
    return None if w is None else {str(i) for i in w.get("ids") or set()}


def meeting_people(workspace_root, meeting_id: str) -> List[str]:
    """The resolved attendees the newest prep brief for this meeting named
    (its receipt's `attendee_person_ids`), walled people cut; none when the
    wall cannot be read or no brief was written."""
    try:
        from receipts import prep_receipts

        recs = prep_receipts(workspace_root, meeting_ids=[meeting_id])
    except Exception:  # noqa: BLE001 - no brief, no people
        return []
    if not recs:
        return []
    data = (recs[-1].get("raw") or {}).get("data") or {}
    ids = [str(i) for i in data.get("attendee_person_ids") or [] if isinstance(i, str) and i.strip()]
    walled = _walled_ids(workspace_root)
    if walled is None:
        return []
    return sorted({i for i in ids if i not in walled})


def copy_home(workspace_root, thread_id: Optional[str] = None) -> str:
    """SEAM (cr1#1586, the bring-in door's copy half): the folder a copy of
    an attached file would land in. The meeting's project folder when its
    thread names one, else the home of documents with no project
    (`deliverables.NO_PROJECT_HOME`, `_hq/notes`, cr1#1581). Read only:
    nothing here copies a file, and no caller copies one yet."""
    from deliverables import NO_PROJECT_HOME

    tid = str(thread_id or "").strip()
    if tid:
        try:
            from entities_io import entities_collection, read_entities_json

            data = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
            for t in entities_collection(data, "threads") if isinstance(data, dict) else []:
                if isinstance(t, dict) and str(t.get("id") or "") == tid:
                    folder = str(t.get("folder_name") or "").strip().replace("\\", "/").strip("/")
                    if folder and ".." not in folder.split("/"):
                        return folder
        except Exception:  # noqa: BLE001 - an unread book has no project folder to name
            pass
    return NO_PROJECT_HOME


# ---------------------------------------------------------------------------
# Reading the book
# ---------------------------------------------------------------------------

def _events(workspace_root) -> List[dict]:
    from events_io import load_events_org_scoped

    return [e for e in load_events_org_scoped(str(workspace_root))[0] if isinstance(e, dict)]


def standing(events: List[dict]) -> List[dict]:
    """The attach rows not taken back, newest first, as `{id, meeting_id,
    start, at, with_ids, attached}`."""
    rows = [e for e in events or [] if isinstance(e, dict) and e.get("type") == EVENT
            and isinstance(e.get("data"), dict) and e["data"].get("kind") == KIND]
    gone = {str(e["data"].get("undoes_choice")) for e in rows
            if e["data"].get("action") == "undo" and e["data"].get("undoes_choice")}
    out = []
    for e in rows:
        d = e["data"]
        att = d.get("attached") if isinstance(d.get("attached"), dict) else None
        cid = str(d.get("hub_choice") or "")
        if d.get("action") != "attach" or not att or not cid or cid in gone:
            continue
        out.append({"id": cid, "meeting_id": str(d.get("target_id") or ""), "start": d.get("start"),
                    "at": e.get("ts"), "seq": e.get("seq") if isinstance(e.get("seq"), int) else -1,
                    "with_ids": [str(i) for i in d.get("with_ids") or [] if isinstance(i, str)],
                    "attached": att})
    out.sort(key=lambda r: r["seq"], reverse=True)
    return out


def _shown(row: Dict[str, Any]) -> Dict[str, Any]:
    """One attachment as the page shows it: no people, no seq."""
    att = row["attached"]
    out = {"id": row["id"], "meeting_id": row["meeting_id"], "kind": att.get("kind"),
           "name": str(att.get("name") or "")}
    for k in ("url", "asset", "size", "content_type"):
        if att.get(k) not in (None, ""):
            out[k] = att[k]
    if row.get("start"):
        out["start"] = row["start"]
    if row.get("at"):
        out["at"] = row["at"]
    return out


def _screened(workspace_root, rows: List[Dict[str, Any]]) -> Tuple[Optional[List[Dict[str, Any]]], int]:
    """The rows whose attachment names nobody walled, and how many were left
    out; `(None, 0)` when the wall cannot be read."""
    if not rows:
        return [], 0
    ok = _screen_items(workspace_root, [r["attached"] for r in rows])
    if ok is None:
        return None, 0
    kept = [r for r, good in zip(rows, ok) if good]
    return kept, len(rows) - len(kept)


def by_meeting(workspace_root, meeting_ids: Optional[List[str]] = None,
               cap: int = 200, budget: int = 60_000) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """`by_meeting_cut`'s rows alone. READ ONLY."""
    got = by_meeting_cut(workspace_root, meeting_ids, cap=cap, budget=budget)
    return None if got is None else got[0]


def by_meeting_cut(workspace_root, meeting_ids: Optional[List[str]] = None,
                   cap: int = 200, budget: int = 60_000) -> Optional[Tuple[Dict[str, List[Dict[str, Any]]], bool]]:
    """`{meeting id: [attachment, ...]}`, newest first, at most `cap` in all
    and at most `budget` characters of JSON (cr1#1632 review: the feed
    document stays well under the refresh's own document limit), for the
    Hub's feed (`cockpit_helpers.feed_mine`): every meeting's, or only
    `meeting_ids`'. READ ONLY. None when the book or the wall cannot be read
    (fail closed: the feed then lists none and says so). Answers
    `(rows, cut)`: `cut` is True when the cap or the budget left a row out,
    so the page cannot say a meeting has nothing attached."""
    want = {str(m) for m in meeting_ids or [] if str(m or "").strip()} if meeting_ids is not None else None
    try:
        rows = [r for r in standing(_events(workspace_root)) if r["meeting_id"]
                and (want is None or r["meeting_id"] in want)]
        kept, _left = _screened(workspace_root, rows)
    except Exception:  # noqa: BLE001 - the feed never fails for this
        return None
    if kept is None:
        return None
    out: Dict[str, List[Dict[str, Any]]] = {}
    used = 2
    cut = len(kept) > max(0, int(cap))
    for r in kept[:max(0, int(cap))]:
        row = _shown(r)
        # an upper bound of what `json.dumps` writes: the row and its
        # separator, and a meeting's key, brackets and separators once
        size = len(json.dumps(row)) + 2 + (0 if r["meeting_id"] in out else len(json.dumps(r["meeting_id"])) + 6)
        if used + size > int(budget):
            cut = True
            break
        used += size
        out.setdefault(r["meeting_id"], []).append(row)
    return out, cut


def _size_words(n: Any) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return ""
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _day_words(workspace_root, start: Any) -> str:
    """The meeting's day in the owner's own time zone, as "Nov 3"."""
    s = str(start or "").strip()
    if not s:
        return ""
    try:
        if len(s) == 10:
            d = _dt.date.fromisoformat(s)
        else:
            from tz import to_local

            d = to_local(s, workspace_path=workspace_root).date()
    except Exception:  # noqa: BLE001 - a zone that cannot be read: the day as written
        try:
            d = _dt.date.fromisoformat(s[:10])
        except ValueError:
            return ""
    return f"{d.strftime('%b')} {d.day}"


def _bullet(workspace_root, row: Dict[str, Any], here: bool) -> str:
    att = row["attached"]
    name = str(att.get("name") or "")
    if att.get("kind") == LINK:
        line = f"{name}: {att.get('url')}"
    else:
        size = _size_words(att.get("size"))
        line = f"{name} ({size}), kept on your Hub with the meeting" if size else \
            f"{name}, kept on your Hub with the meeting"
    if not here:
        day = _day_words(workspace_root, row.get("start"))
        line += f" (from the {day} meeting)" if day else " (from an earlier meeting)"
    return line


def _gather(workspace_root, mid: str, people: set) -> Tuple[Optional[List[Dict[str, Any]]], int]:
    """The standing attachments of this meeting, and of other meetings with
    any of `people`, screened: `(rows, left out)`, or `(None, 0)` when the
    book or the wall cannot be read."""
    try:
        rows = [r for r in standing(_events(workspace_root))
                if (mid and r["meeting_id"] == mid) or (people and people & set(r["with_ids"]))]
        return _screened(workspace_root, rows)
    except Exception:  # noqa: BLE001 - a book that cannot be read lists nothing
        return None, 0


def attachments(workspace_root, meeting_id: str = "", person_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """READ ONLY. What was attached to this meeting and, newest first, to
    other meetings with any of `person_ids`: `{ok, rows, left_out, line}`,
    each row the page's shape plus `here` (attached to this meeting)."""
    mid = str(meeting_id or "").strip()
    kept, left = _gather(workspace_root, mid, {str(p) for p in person_ids or [] if str(p or "").strip()})
    if kept is None:
        return {"ok": False, "rows": [], "left_out": 0, "line": READ_UNREAD_LINE}
    out = [dict(_shown(r), here=bool(mid) and r["meeting_id"] == mid) for r in kept]
    return {"ok": True, "rows": out, "left_out": left, "line": ""}


def for_brief(workspace_root, meeting_id: str = "", person_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """READ ONLY. The brief's "Attached" section for this meeting:
    `{ok, section, n, left_out, line}`. `section` is a `brief_writer`
    section (`{heading, bullets}`) to pass, as it came back, in
    `prep_pipeline.assemble_prep_sections(extra_sections=[...])`, or None
    when nothing is attached (omit, don't pad). This meeting's own come
    first, then those from earlier meetings with the same people, at most
    `BRIEF_CAP`. Nothing walled is listed, and what was left out is only
    counted, never named."""
    mid = str(meeting_id or "").strip()
    kept, left = _gather(workspace_root, mid, {str(p) for p in person_ids or [] if str(p or "").strip()})
    if kept is None:
        return {"ok": False, "section": None, "n": 0, "left_out": 0, "line": READ_UNREAD_LINE}
    kept.sort(key=lambda r: (r["meeting_id"] != mid, -r["seq"]))
    bullets = [_bullet(workspace_root, r, bool(mid) and r["meeting_id"] == mid) for r in kept[:BRIEF_CAP]]
    section = {"heading": HEADING, "bullets": bullets} if bullets else None
    return {"ok": True, "section": section, "n": len(bullets), "left_out": left, "line": ""}


__all__ = ["KIND", "VERBS", "check_press", "screen", "meeting_people", "copy_home", "standing",
           "by_meeting", "by_meeting_cut", "attachments", "for_brief", "link_name", "key_of", "day_of",
           "FILE_TYPES", "SIZE_CAP", "WALLED_LINE", "UNREAD_LINE", "NO_MEETING_LINE"]
