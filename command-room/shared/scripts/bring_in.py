#!/usr/bin/env python3
"""bring_in: one door to bring things in (cr1#1586, parent cr1#1579).

The owner says "bring this in" and picks a source. This module is the door's
code: it lists the source, screens it against the owner's wall, shows one
preview of the whole batch, and writes only what that preview showed, as one
batch the owner can undo.

SOURCES. Four are offered (`SOURCES`): a folder on this computer, an export
(a `.zip` archive, or one file), past chats, and a connector. The first two
are built here; past chats and connectors answer `not_here` with one line
(cr1#1586 PR 2 and 3).

THE WALL COMES FIRST (cr1#1577, ruling 6 of cr1#1579). `plan` asks
`import_wall.wall` before the source is listed: a wall that cannot be read
stops the door, and nothing is listed, read or written. Every name the batch
would write (each item's name and path in the source, the summary written for
it, each person and company proposed from it) then goes through
`import_wall.screen_import`: an item that names somebody the owner keeps off
limits or personal is left out whole, a walled person or company is never
written, and the reply says how many were left out without naming anyone.
The model reads an item's contents only after the first screen has kept it.

COPY OR LINK, once per batch, LINK PRESELECTED (ruling 5): `mode` is the
batch's answer and `overrides` changes it per item.
  * link: one `note` row (`data.via: "bring_in_link"`) with the summary the
    model wrote after the wall (capped, never the item's own text, as
    connector content is kept: shared/PASSIVE_CAPTURE.md) and a pointer to
    the source in `source_ref` (`file:<path>`, `export:<archive>#<member>`),
    bound to its project.
  * copy: the bytes copied into the project's folder, never over a file that
    is there (same bytes: nothing written; other bytes: a free name beside
    it), and one `bring_in_item` row naming the copy. The source is never
    moved or changed.
  * no project: an item the model could not place, or placed on a project
    that is not on file, stays at its source. One `bring_in_item` row and one
    `left_at_source` row in the bring-in log list it in the Documents with no
    project pile (`doc_shelf.documents_with_no_project`, cr1#1581).

NOTHING IS WRITTEN BEFORE THE PREVIEW IS ACCEPTED. `plan` is read only and
hands back `digest`, a hash of exactly what it showed (every item's bytes,
its mode, its project and its summary, and every person and company).
`accept` plans again from the same arguments and writes nothing when the
digest differs: the source changed, or the arguments did.

PEOPLE AND COMPANIES go through their writers only
(`people_writer.create_person`, `org_writer.create_org`), stamped with the
batch and `person_org_creation_structured_fact`, whose reverser archives and
never deletes. One already on file is left as it is.

UNDO. Every row the batch writes carries `brain_batch_id`, so
`brain_undo.undo_batch({"kind": "brain_batch", "batch_id": ...})`, and a
bare `undo`, take the whole batch back: a link is taken off its project, a
copy is moved to `_archive/bring-in-undo-<batch>/` (archive first, never
deleted), an item left at its source leaves the pile, and every person and
company it added is archived. Each take back appends one
`bring_in_item_undone` row; the rows it undoes stay in history
(`take_back_item`, the three classes' reverser). The source is never touched.
"""
from __future__ import annotations

import datetime
import hashlib
import html
import io
import json
import lzma
import os
import re
import secrets
import shutil
import stat
import sys
import time
import unicodedata
import urllib.parse
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SOURCE_SKILL = "bring-in"

#: The four sources the door offers, in the order it offers them.
SOURCES = ("folder", "export", "past_chats", "connector")
BUILT_SOURCES = frozenset({"folder", "export"})
SOURCE_WORDS = {"folder": "a folder on this computer", "export": "an export",
                "past_chats": "past chats", "connector": "a connector"}

LINK = "link"
COPY = "copy"
MODES = (LINK, COPY)
DEFAULT_MODE = LINK

AT_PROJECT = "project"
AT_SOURCE = "at_source"

LINK_VIA = "bring_in_link"
ITEM_TYPE = "bring_in_item"
UNDONE_TYPE = "bring_in_item_undone"
CLASS_LINK = "bring_in_link"
CLASS_COPY = "bring_in_copy"
CLASS_AT_SOURCE = "bring_in_left_at_source"
ITEM_CLASSES = (CLASS_LINK, CLASS_COPY, CLASS_AT_SOURCE)
PERSON_ORG_CLASS = "person_org_creation_structured_fact"
BATCH_PREFIX = "bin_"

#: The bring-in door's own log, which `doc_shelf` reads the pile from beside
#: workspace-ingest's (security review of #1653, note 12): kept apart, so
#: workspace-ingest's "undo migration", which renames its whole log, never
#: takes a bring-in row with it.
LOG_REL = "_hq/_bring-in.jsonl"
LEFT_AT_SOURCE = "left_at_source"
#: The word on a source that takes it off the pile again ("the newest word
#: on each source wins", `doc_shelf._left_at_source`).
LOG_UNDONE = "bring_in_undone"
ARCHIVE_DIR = "_archive"

MAX_ITEMS = 500
MAX_DEPTH = 8
#: Security review of #1653, SHOULD 7: an item past MAX_ITEM_BYTES is skipped
#: and counted; a batch past MAX_TOTAL_BYTES is refused before anything is
#: read; an archive member whose declared size is past the item cap, or that
#: would inflate more than MAX_ZIP_RATIO times, is skipped and counted.
MAX_ITEM_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_RATIO = 100
#: Fix rounds 2 to 4 of #1653, MUST 3 (ruling 71): the door screens every
#: item's own text, read from the same bytes it hashed: UTF-8, UTF-16 (by
#: its byte-order mark or its pattern of zero bytes), anything else as UTF-8
#: and as cp1252 with what does not decode replaced, the readable runs of a
#: binary file, and the text inside a Word, Excel or PowerPoint file. Each
#: item is screened as it is read (round 4, item 3), in windows of
#: SCREEN_WINDOW bytes that overlap by SCREEN_OVERLAP, so a name across a
#: window edge is still whole in one window, and no item's whole text is
#: ever held: only whether it named somebody walled.
SCREEN_WINDOW = 1024 * 1024
SCREEN_OVERLAP = 2048
#: Every byte the screen reads in one batch counts here, an Office file's
#: inflated text included (round 4, item 3); past it the batch is refused,
#: counted and fail-closed (`too_much_to_check`). Ruling 77 (round 5,
#: item 2): 48 MB, about 64 s at the worst rate measured, and the screen
#: also stops at SCREEN_SECONDS of reading, so a plan and the accept that
#: plans again each stay inside the door's 150 s. One item bigger than the
#: whole budget is left out on its own, counted; it never refuses the batch.
MAX_SCREEN_BYTES = 48 * 1024 * 1024
SCREEN_SECONDS = 60
#: An Office file's XML is screened as text with its tags taken out first
#: (round 5, item 4), in windows of SCREEN_CHARS characters that overlap by
#: SCREEN_OVERLAP_CHARS.
SCREEN_CHARS = 1024 * 1024
SCREEN_OVERLAP_CHARS = 2048
#: A name part longer than this many UTF-16 units is one Windows will not
#: take (round 5, item 3).
NAME_UNITS_MAX = 255
#: The shortest run of readable characters kept from a file's text.
TEXT_RUN_MIN = 4
OFFICE_SUFFIXES = frozenset({".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".pptx", ".pptm"})
#: Names Windows reserves for devices (notes 3 and 6 of rounds 2 and 3): a
#: name that is one, with any extension, is never written as a file; the
#: superscript digits count as digits there.
_SUPERSCRIPTS = (chr(0xB9), chr(0xB2), chr(0xB3))
RESERVED_NAMES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"}
                           | {f"{dev}{n}" for dev in ("com", "lpt")
                              for n in [str(d) for d in range(1, 10)] + list(_SUPERSCRIPTS)})
#: Characters Windows refuses in a file name: the seven marks and every
#: control character.
RESERVED_CHARS = frozenset('<>:"|?*') | frozenset(chr(c) for c in range(32))
#: Windows file attributes (`os.stat_result.st_file_attributes`).
ATTR_HIDDEN = 0x2
ATTR_SYSTEM = 0x4
ATTR_REPARSE = 0x400
SUMMARY_CAP = 600
NAME_CAP = 200
_SKIP_NAMES = frozenset({"__macosx", "desktop.ini", "thumbs.db", ".ds_store"})

NOT_HERE_LINE = ("Bringing things in from {what} is not here yet. A folder on this "
                 "computer or an export works today.")
UNKNOWN_SOURCE_LINE = ("I can bring things in from a folder on this computer, an "
                       "export, past chats or a connector; nothing was read.")
NO_SOURCE_LINE = "I could not find {what} there, so nothing was read."
PROTECTED_LINE = ("That is a system folder or a place where passwords and keys are "
                  "kept, so I will not read it; nothing was read.")
SKIPPED_LINE = ("Skipped {what} I could not read or that point somewhere else "
                "(a link or a shortcut); nothing about them was brought in.")
TOO_BIG_ITEM_LINE = ("Left out {what} too big to check for the people you keep off "
                     "limits; nothing about them was brought in.")
LINKED_LINE = ("That file is a link to another file, so I will not read it; "
               "nothing was read.")
TOO_MUCH_TO_CHECK_LINE = ("That holds more than I can check for the people you keep off "
                          "limits in one go (about {mb} MB, or a minute of reading). Pick "
                          "a smaller folder or part of it; nothing was brought in.")
NOT_LOCAL_LINE = ("Pick a folder on one of this computer's drives, a place that "
                  "starts with a drive letter; nothing was read.")
TOO_BIG_LINE = ("That holds more than {gb} GB. Pick a smaller folder or part of it; "
                "nothing was read.")
IN_WORKSPACE_LINE = ("That is already inside your Command Room folder, so there is "
                     "nothing to bring in.")
TOO_MANY_LINE = ("That holds more than {n} items. Pick a smaller folder or part of "
                 "it; nothing was read.")
EMPTY_LINE = "There is nothing there to bring in."
CHANGED_LINE = ("What is there changed since the preview, so nothing was brought "
                "in. Show the preview again to see it as it is now.")
NOTHING_LEFT_LINE = "Nothing is left to bring in after that, so nothing was written."
BROUGHT_LINE = "Brought in {what}. Say undo to take the whole batch back."
UNDO_OWNER_ONLY = "a bring-in is taken back on the owner's own undo only"


# ---------------------------------------------------------------- small

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _clean(text: Any, cap: int) -> str:
    s = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    return " ".join(s.split())[:cap].strip()


def _item_id(source_ref: str) -> str:
    return "bi_" + hashlib.sha256(source_ref.encode("utf-8")).hexdigest()[:12]


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


def _hidden(name: str) -> bool:
    return name.startswith((".", "~$")) or name.lower() in _SKIP_NAMES


def _attrs(path: Path) -> int:
    """The entry's own Windows attributes (0 elsewhere, or when unreadable)."""
    try:
        return int(getattr(os.lstat(path), "st_file_attributes", 0) or 0)
    except OSError:
        return 0


def _hidden_entry(path: Path) -> bool:
    """Hidden by its name, or by the Windows hidden or system attribute
    (security review of #1653, MUST 4: `attrib +h`, `$Recycle.Bin`)."""
    return _hidden(path.name) or bool(_attrs(path) & (ATTR_HIDDEN | ATTR_SYSTEM))


def _rel_to(path: Path, root: Path) -> str:
    """`path` as a workspace-relative posix path, both sides resolved: a
    workspace reached through a link (every temp folder on a Mac sits under
    `/var`, a link to `/private/var`) must not crash the write."""
    return _posix(path.resolve().relative_to(root.resolve()))


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _plain(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ---------------------------------------------------------------- sources

def sources() -> Dict[str, Any]:
    """The four sources the door offers, in order, and which are built.
    Read only (`RUN_HELPER_ALLOWLIST`)."""
    return {"ok": True, "default_mode": DEFAULT_MODE,
            "sources": [{"kind": k, "words": SOURCE_WORDS[k], "built": k in BUILT_SOURCES}
                        for k in SOURCES]}


def _refuse(status: str, line: str, **extra) -> Dict[str, Any]:
    out = {"ok": False, "stop": True, "status": status, "line": line, "items": [],
           "people": [], "orgs": [], "left_out": 0}
    out.update(extra)
    return out


def _is_link(path: Path) -> bool:
    """A symbolic link, a Windows junction or any other reparse point
    (security review of #1653, MUST 5: `os.path.isjunction` exists only from
    Python 3.12, and an older one follows a junction as a folder)."""
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if isjunction and isjunction(path):
            return True
        return bool(_attrs(path) & ATTR_REPARSE)
    except OSError:
        return True


def _hardlinked(path: Path) -> bool:
    """A file with more than one name (fix round 2 of #1653, MUST 1): a hard
    link can be a second name for a file in a secrets area or in the
    workspace, so it is a link like any other. Unreadable counts as one."""
    try:
        st = os.stat(path)
    except OSError:
        return True
    return stat.S_ISREG(st.st_mode) and st.st_nlink > 1


def _units(text: str) -> int:
    """`text`'s length as Windows counts it: UTF-16 code units (an emoji is
    two), never Python characters (round 5, item 3)."""
    return len(str(text).encode("utf-16-le")) // 2


def _reserved(part: str) -> bool:
    """A name part Windows would not keep as written (round 2 note 3, round 3
    item 6), or one that hides what it is (round 4, item 5): a device name
    (`CON`, `aux`, `NUL.txt`, `CONIN$`, `COM` with a superscript digit), a
    character Windows refuses (`< > : " | ? *` or a control character), a
    name ending in a dot or a space, or a format character (the right-to-left
    override that makes `exe.txt` read backwards, and its kin)."""
    return part.split(".", 1)[0].strip().casefold() in RESERVED_NAMES \
        or any(ch in RESERVED_CHARS for ch in part) or part.endswith((".", " ")) \
        or any(unicodedata.category(ch) == "Cf" for ch in part) \
        or _units(part) > NAME_UNITS_MAX  # round 5, item 3: Windows counts UTF-16 units


_RUN_RX = re.compile("[^" + re.escape("".join(chr(c) for c in range(32) if c not in (9, 10, 13))
                                        + chr(0x7F) + chr(0xFFFD)) + "]{%d,}" % TEXT_RUN_MIN)
_TAG_BREAK_RX = re.compile(r"</(?:w:p|a:p|w:tr|row|si|c)>|<w:(?:tab|br)\s*/>|<a:br\s*/>")
_TAG_RX = re.compile(r"<[^<>]*>")  # round 6: linear (`<[^>]*>` was quadratic on a run of `<`)


_ASCII_BYTES = bytes(range(128))
_BOMS = ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))


def _encoding_of(data: bytes) -> Optional[str]:
    """The item's encoding, decided ONCE from its byte-order mark or its
    head (round 5, item 1): every window of the item is read with it, so a
    UTF-16 file is UTF-16 past its first megabyte too. None: UTF-8, with
    the fallbacks `_decode` gives it."""
    for mark, enc in _BOMS:
        if data.startswith(mark):
            return enc
    head = data[:4096]
    if len(head) >= 4:
        odd, even = head[1::2].count(0), head[0::2].count(0)
        if odd > len(head) // 8 and even < odd // 4:
            return "utf-16-le"
        if even > len(head) // 8 and odd < even // 4:
            return "utf-16-be"
    return None


def _parity16(data: bytes) -> Optional[str]:
    """A UTF-16 reading for text with no mark and no zero bytes (round 6,
    item 1): Cyrillic and CJK units keep one byte of each pair within a few
    values (0x04 for Cyrillic, 0x4E to 0x9F for CJK) while the other varies,
    so the parity with few distinct values is the high byte. None when the
    sample does not look that way. Round 7, item 3: decided on each window's
    own sample, and refused when more than one unit in ten would be a
    control character or sit at U+D800 and above, which text almost never
    holds and signed 16-bit data (audio, measurements) is mostly made of."""
    head = data[:4096]
    if len(head) < 64:
        return None
    lo, hi = len(set(head[0::2])), len(set(head[1::2]))
    if hi <= 96 and hi * 3 <= lo:
        enc, highs, lows = "utf-16-le", head[1::2], head[0::2]
    elif lo <= 96 and lo * 3 <= hi:
        enc, highs, lows = "utf-16-be", head[0::2], head[1::2]
    else:
        return None
    odd = sum(1 for h, l in zip(highs, lows)
              if h >= 0xD8 or (h == 0 and l < 32 and l not in (9, 10, 13)))
    return None if odd * 10 > len(highs) else enc


#: A run of Latin letters in UTF-16 of either layout, each letter beside a
#: zero byte (round 6; round 7, item 3: only these runs are read with their
#: zero bytes taken out, not every window that holds one zero byte).
_ASCII_RUN_RX = re.compile(rb"[ -~]{%d,}" % TEXT_RUN_MIN)
_U16_RUN_RX = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}|(?:\x00[\x20-\x7e]){%d,}"
                         % (TEXT_RUN_MIN, TEXT_RUN_MIN))


def _decode(data: bytes, enc: Optional[str] = None, extra: Optional[str] = None) -> str:
    """Every reading of one window the screen needs (MUST 3, ruling 71),
    with the item's own encoding (round 5, item 1), the UTF-16 reading the
    byte parity suggests (`extra`, round 6), and, when the window holds zero
    bytes, its text with them taken out (round 6: a Latin name in UTF-16 of
    any layout, each letter followed by a zero byte)."""
    if enc in ("utf-16-le", "utf-16-be"):
        out = [data.decode(enc, "replace")]
        if not any(data.startswith(m) for m, e in _BOMS if e == enc):
            # decided from the zero bytes, not a mark: UTF-8 is read too
            out.append(data.decode("utf-8", "replace"))
        else:
            # round 7: a mark then single-byte text (`FF FE` and plain
            # ASCII) keeps its readable runs, which the zero-byte reading
            # no longer gives it for free
            out.extend(m.group().decode("ascii") for m in _ASCII_RUN_RX.finditer(data))
    else:
        try:
            out = [data.decode("utf-8")]
        except UnicodeDecodeError:
            out = [data.decode("utf-8", "replace")]
            # Round 4, item 3: the single-byte reading only for text with a few
            # bytes past ASCII (a cp1252 export with an accent). On binary bytes
            # it reads every byte as a letter, one endless run for the matcher.
            if len(data.translate(None, _ASCII_BYTES)) * 10 <= len(data) * 3:
                out.append(data.decode("cp1252", "replace"))
    if extra is None and enc not in ("utf-16-le", "utf-16-be"):
        extra = _parity16(data)  # round 7: this window's own sample
    if extra and extra != enc:
        out.append(data.decode(extra, "replace"))
    if b"\x00" in data:
        out.extend(m.group().replace(b"\x00", b"").decode("ascii")
                   for m in _U16_RUN_RX.finditer(data))
    return "\n".join(out)


#: An attribute value that cannot name anybody (round 7): digits and marks
#: only (a date too), hex with a digit in it (ids, GUIDs, colours), or a cell
#: reference.
_ATTR_ID_RX = re.compile(r"[\d\s.,:;%+\-_#$()/TZ]*|(?=[^\d]*\d)[\dA-Fa-f{}\-]+"
                         r"|\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?")
_ATTR_RX = re.compile(r"""=\s*(?:"([^"]*)"|'([^']*)')""")  # round 7: single quotes too


def _xml_text(raw: bytes, keep_attrs: bool, fields: bool = False) -> str:
    """An Office XML part as text: read in its own encoding (a UTF-16 part
    too, round 5), its tags taken out BEFORE it is cut into windows (a name
    split across runs with properties between stays whole, round 5), and
    every attribute value kept: a relationships part's `mailto:` and, round
    7 item 1, the authors of comments and tracked changes (`w:author`), the
    people part, a workbook's persons and a deck's comment authors. With
    `fields` (a document-properties part, round 7) every tag is " | ", so
    each property stays on its own: `lastModifiedBy` is never glued to the
    date after it, and two titles side by side never join into one name."""
    enc = _encoding_of(raw) or "utf-8"
    text = raw.decode("utf-8" if enc == "utf-8-sig" else enc, "replace")
    attrs = ""
    if keep_attrs:
        # round 7: each distinct value once, and never one that is only a
        # number, an id, a colour or a cell reference (most of a sheet's)
        values = [v for v in dict.fromkeys(html.unescape(a or b) for a, b in _ATTR_RX.findall(text))
                  if len(v) >= 3 and not _ATTR_ID_RX.fullmatch(v)]
        # round 6: `mailto:rio%40...` is the same address
        # round 7: " | " between values, so two neighbours (sheets "Rio" and
        # "Sample") never read as one walled name; a name inside one value is
        # still whole
        attrs = " | ".join(values + [urllib.parse.unquote(v) for v in values if "%" in v])
    body = html.unescape(_TAG_RX.sub(" | ", text) if fields
                         else _TAG_RX.sub("", _TAG_BREAK_RX.sub(" \n", text)))
    return body + ("\n" + attrs if attrs else "")


class _CannotScreen(Exception):
    """An item the door cannot read for the screen (round 4, item 1)."""


class _TooBigToScreen(_CannotScreen):
    """One item bigger than the whole screening budget (ruling 77), left out
    alone and counted on its own line (round 6)."""


class _OverBudget(Exception):
    """The batch passed MAX_SCREEN_BYTES or SCREEN_SECONDS (round 4 item 3,
    round 5 item 2)."""


class _Screen:
    """One batch's screen: the wall's checker, read once
    (`import_wall.text_checker`), the bytes it may still read, and the time
    it may still take (ruling 77)."""

    def __init__(self, check, budget: int = MAX_SCREEN_BYTES, seconds: float = None):
        self.check = check
        self.total = int(budget)
        self.left = int(budget)
        self.deadline = time.monotonic() + (SCREEN_SECONDS if seconds is None else seconds)
        self.too_big = 0

    def _spend(self, n: int) -> None:
        if int(n) > self.total:
            self.too_big += 1
            raise _TooBigToScreen()  # bigger than the whole budget: this item alone
        self.left -= int(n)
        if self.left < 0:
            raise _OverBudget()

    def oversize(self) -> None:
        """An item past MAX_ITEM_BYTES, skipped before it is read, is also
        too big to check: counted on the same line (round 7)."""
        self.too_big += 1

    def _in_time(self) -> None:
        if time.monotonic() > self.deadline:
            raise _OverBudget()

    def _hit(self, text: str) -> bool:
        runs = "\n".join(_RUN_RX.findall(text))
        return bool(runs) and self.check(runs)

    def _found(self, data: bytes, enc: Optional[str], extra: Optional[str] = None) -> bool:
        """Whether any window of `data`, read in the item's encoding, names
        somebody walled. Windows start at even offsets, so a UTF-16 window
        is never split inside a unit."""
        for i in range(0, max(len(data), 1), SCREEN_WINDOW):
            self._in_time()
            if self._hit(_decode(data[max(0, i - SCREEN_OVERLAP): i + SCREEN_WINDOW], enc, extra)):
                return True
        return False

    def _found_text(self, text: str) -> bool:
        for i in range(0, max(len(text), 1), SCREEN_CHARS):
            self._in_time()
            if self._hit(text[max(0, i - SCREEN_OVERLAP_CHARS): i + SCREEN_CHARS]):
                return True
        return False

    def walled(self, data: bytes, name: str) -> bool:
        """True when the item's own text names somebody walled. Raises
        _CannotScreen for an item bigger than the whole budget, an Office
        file it cannot open, or one whose part is past the item cap or
        inflates past MAX_ZIP_RATIO (round 4, item 1); _OverBudget past the
        batch's bytes or time."""
        self._spend(len(data))
        enc = _encoding_of(data)
        if self._found(data, enc):
            return True
        office = _office_parts(data, name)
        if office is None:
            raise _CannotScreen()
        if not office:
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in office:
                    if info.file_size > MAX_ITEM_BYTES or (
                            info.file_size > 1024 * 1024
                            and info.file_size > MAX_ZIP_RATIO * max(1, info.compress_size)):
                        raise _CannotScreen()
                    self._spend(info.file_size)
                    with zf.open(info) as fh:
                        raw = fh.read(MAX_ITEM_BYTES + 1)
                    self._in_time()  # round 6: before the part's text is built
                    if self._found_text(_xml_text(raw, True, info.filename.casefold().startswith(
                            "docprops/"))):  # round 7: every part's attributes; properties as fields
                        return True
        except _TooBigToScreen:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
                ValueError, KeyError, EOFError, zlib.error, lzma.LZMAError):
            raise _CannotScreen()  # round 6: a corrupt deflate or LZMA part too
        return False


def _office_parts(data: bytes, name: str):
    """The XML and relationships parts of an Office file, recognised by its
    content (a zip holding `[Content_Types].xml`, round 5), not its name; []
    for anything that is not one; None for a file named as Office that is
    not a readable one (round 4, item 1: cannot screen, left out)."""
    named = Path(name).suffix.casefold() in OFFICE_SUFFIXES
    if data[:2] != b"PK":
        return None if named else []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, EOFError):
        return None if named else []
    if not any(i.filename == "[Content_Types].xml" for i in infos):
        return None if named else []
    return [i for i in infos if not i.is_dir()
            and i.filename.casefold().endswith((".xml", ".rels"))]


def _within(real: str, source: Path) -> bool:
    try:
        # the judged source as judged (already resolved by judge_source):
        # resolving it again would follow a junction put there since
        base = os.path.normcase(os.path.abspath(str(source)))
        return os.path.commonpath([os.path.normcase(real), base]) == base
    except (OSError, ValueError):
        return False


def _open_checked(path: Path, source: Path, ident: Optional[Tuple[int, int]] = None,
                  areas: Optional[set] = None):
    """THE ONE WAY a source file is read (round 3, item 2): opened once, then
    judged on the handle: a regular file with one name (no hard link), the
    very file its real path names, that real path inside the judged source,
    and, when `ident` is given, the file the listing saw (volume and file
    id). Answers the open handle, or None (closed) when any of it fails.
    The hash, the text and the copy all read from the handle, never by
    name again."""
    try:
        fh = open(path, "rb")
    except OSError:
        return None
    try:
        st = os.fstat(fh.fileno())
        real = os.path.realpath(path)
        rst = os.stat(real)
        ok = (stat.S_ISREG(st.st_mode) and st.st_nlink == 1
              and (st.st_ino, st.st_dev) == (rst.st_ino, rst.st_dev)
              and _within(real, source)
              and not _protected(Path(real), areas)  # round 4, item 2
              and (ident is None or (int(st.st_ino), int(st.st_dev)) == tuple(ident)))
    except (OSError, ValueError):
        ok = False
    if not ok:
        fh.close()
        return None
    return fh


def _read_checked(fh) -> Tuple[bytes, int, int]:
    st = os.fstat(fh.fileno())
    return fh.read(), int(st.st_ino), int(st.st_dev)


def _identity(path: Path) -> Tuple[int, int]:
    st = os.stat(path)
    return int(st.st_ino), int(st.st_dev)


def _stays_in(path: Path, source: Path) -> bool:
    """`path` really resolves inside `source` (both resolved)."""
    try:
        real = os.path.normcase(os.path.realpath(path))
        base = os.path.normcase(os.path.realpath(source))
        return os.path.commonpath([real, base]) == base
    except (OSError, ValueError):
        return False


def _count_files(folder: Path, cap: int = 10000) -> int:
    """How many files a folder past MAX_DEPTH holds (never followed through a
    link), for the skipped count; stops counting at `cap`."""
    n = 0
    for _dirpath, dirnames, filenames in os.walk(folder, followlinks=False, onerror=lambda e: None):
        here = Path(_dirpath)
        # note 6 of fix round 2: never into a hidden or refused folder, as
        # the walk above it never goes there either
        dirnames[:] = [d for d in dirnames if not _is_link(here / d)
                       and not _hidden_entry(here / d) and not _protected(here / d)]
        n += sum(1 for f in filenames if not _hidden_entry(here / f))
        if n >= cap:
            return cap
    return n


def _list_folder(folder: Path, screen: Optional[_Screen] = None
                 ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], int]:
    """`(items, why, skipped)`. Never follows a link, junction or reparse
    point to a folder; every file is read through `_open_checked` against
    the judged `folder`, its hash and its text from the same bytes. Each
    item it cannot take counts in `skipped`: a link out, a hard link, an
    unreadable file, a name Windows would refuse, a file past the byte cap,
    a folder the source check would refuse, and every file past MAX_DEPTH.
    Hidden files and folders (by name or by attribute) are left out and not
    Counted. Past MAX_TOTAL_BYTES the batch is `too_big`; past
    MAX_SCREEN_BYTES it is `too_much_to_check`. Round 4: a folder whose real
    path left the judged source or is a protected area is never entered,
    and an item the screen cannot read is skipped and counted."""
    items: List[Dict[str, Any]] = []
    skipped = 0
    total = 0
    base_depth = len(folder.parts)
    areas = _area_ids()

    def _unreadable(_err) -> None:
        nonlocal skipped
        skipped += 1

    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False, onerror=_unreadable):
        here = Path(dirpath)
        # round 4, item 2: a folder flipped since it was listed (a junction
        # put in its place) is judged by where it really is
        real_here = os.path.realpath(here)
        if not _within(real_here, folder) or _protected(Path(real_here), areas):
            skipped += 1
            dirnames[:] = []
            continue
        keep = []
        for d in sorted(dirnames):
            sub_dir = here / d
            if _hidden_entry(sub_dir):
                continue
            if _is_link(sub_dir) or _reserved(d) or _protected(sub_dir, areas):
                skipped += 1
                continue
            if len(here.parts) - base_depth >= MAX_DEPTH:
                skipped += _count_files(sub_dir)
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            path = here / name
            if _hidden_entry(path):
                continue
            if path.is_symlink() or _reserved(name):
                skipped += 1
                continue
            if len(items) >= MAX_ITEMS:
                return None, "too_many", skipped
            fh = _open_checked(path, folder, areas=areas)
            if fh is None:
                skipped += 1
                continue
            with fh:
                try:
                    size = os.fstat(fh.fileno()).st_size
                    if size > MAX_ITEM_BYTES:
                        skipped += 1
                        if screen is not None:
                            screen.oversize()  # round 7: on the too-big line
                        continue
                    total += size
                    if total > MAX_TOTAL_BYTES:
                        return None, "too_big", skipped
                    data, ino, dev = _read_checked(fh)
                except OSError:
                    skipped += 1
                    continue
            try:
                walled = screen.walled(data, name) if screen is not None else False
            except _CannotScreen:
                skipped += 1
                continue
            except _OverBudget:
                return None, "too_much_to_check", skipped
            member = _posix(path.relative_to(folder))
            ref = "file:" + _posix(path)
            items.append({"item_id": _item_id(ref), "name": name, "member": member,
                          "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "source_ref": ref, "_path": str(path), "_source": str(folder),
                          "_ino": ino, "_dev": dev, "_walled": walled})
            del data
    return items, None, skipped


def _is_archive(path: Path) -> bool:
    """Only a `.zip` is opened as an archive (SHOULD 10): a `.docx` or any
    other zip-shaped document is one item."""
    return path.suffix.casefold() == ".zip" and zipfile.is_zipfile(path)


def _list_export(path: Path, screen: Optional[_Screen] = None
                 ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], int]:
    """`(items, why, skipped)` for a `.zip`'s members, or the one file. The
    export is opened once through `_open_checked` (it is its own judged
    source) and the zip is read from that handle (round 3, item 3). A member
    that names a path out of the archive or a drive (`..`, a leading slash,
    any `:`), a name Windows would refuse, that cannot be read, or whose
    declared size is past the cap or inflates past MAX_ZIP_RATIO, is skipped
    and counted."""
    fh = _open_checked(path, path)
    if fh is None:
        return [], None, 1  # a hard link, a swap or an unreadable file: never read
    with fh:
        st = os.fstat(fh.fileno())
        zip_ident = (int(st.st_ino), int(st.st_dev))
        is_zip = path.suffix.casefold() == ".zip" and zipfile.is_zipfile(fh)
        fh.seek(0)
        if not is_zip:
            if st.st_size > MAX_ITEM_BYTES:
                if screen is not None:
                    screen.oversize()  # round 7: on the too-big line
                return [], None, 1
            data, ino, dev = _read_checked(fh)
            try:
                walled = screen.walled(data, path.name) if screen is not None else False
            except _CannotScreen:
                return [], None, 1
            except _OverBudget:
                return None, "too_much_to_check", 0
            ref = "file:" + _posix(path)
            return [{"item_id": _item_id(ref), "name": path.name, "member": path.name,
                     "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "source_ref": ref, "_path": str(path), "_source": str(path),
                     "_ino": ino, "_dev": dev, "_walled": walled}], None, 0
        items: List[Dict[str, Any]] = []
        skipped = 0
        total = 0
        with zipfile.ZipFile(fh) as zf:
            for info in sorted(zf.infolist(), key=lambda i: i.filename):
                if info.is_dir():
                    continue
                raw = info.filename.replace("\\", "/")
                parts = [p for p in raw.split("/") if p]
                if parts and (raw.startswith("/") or ".." in parts or any(":" in p for p in parts)):
                    skipped += 1  # a path out of the archive, or a drive: never taken, counted
                    continue
                if any(_reserved(p) for p in parts):
                    skipped += 1  # a device name, a refused mark, a trailing dot or space
                    continue
                if not parts or any(_hidden(p) for p in parts):
                    continue
                if info.file_size > MAX_ITEM_BYTES or (
                        info.file_size > 1024 * 1024
                        and info.file_size > MAX_ZIP_RATIO * max(1, info.compress_size)):
                    skipped += 1
                    if info.file_size > MAX_ITEM_BYTES and screen is not None:
                        screen.oversize()  # round 7: on the too-big line
                    continue
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    return None, "too_big", skipped
                if len(items) >= MAX_ITEMS:
                    return None, "too_many", skipped
                member = "/".join(parts)
                try:
                    with zf.open(info) as mf:
                        data = mf.read(MAX_ITEM_BYTES + 1)
                except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
                        EOFError, zlib.error, lzma.LZMAError):  # round 6: a corrupt member
                    skipped += 1
                    continue
                if len(data) > MAX_ITEM_BYTES:
                    skipped += 1
                    if screen is not None:
                        screen.oversize()  # round 7: on the too-big line
                    continue
                try:
                    walled = screen.walled(data, parts[-1]) if screen is not None else False
                except _CannotScreen:
                    skipped += 1
                    continue
                except _OverBudget:
                    return None, "too_much_to_check", skipped
                ref = f"export:{_posix(path)}#{member}"
                items.append({"item_id": _item_id(ref), "name": parts[-1], "member": member,
                              "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                              "source_ref": ref, "_zip": str(path), "_member": info.filename,
                              "_zip_ino": zip_ident[0], "_zip_dev": zip_ident[1],
                              "_walled": walled})
                del data
        return items, None, skipped


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------- the source

#: Areas a source may never be, or sit under (coordinator ruling on cr1#1586,
#: edge 1): the system's own folders and the places secrets are kept. A
#: hidden folder or dotfile anywhere on the way is refused too, which covers
#: `~/.ssh`, `~/.aws`, `~/.config` and `~/.gnupg`; they are named here as
#: well so the list reads whole.
SYSTEM_ROOTS_WINDOWS = ("Windows", "Program Files", "Program Files (x86)", "ProgramData")
SYSTEM_ROOTS_POSIX = ("/etc", "/System", "/Library", "/private/etc")
HOME_AREAS = ("AppData", "Library", ".ssh", ".aws", ".config", ".gnupg")
ANYWHERE_PARTS = ("keychains",)


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _not_local(text: str) -> bool:
    """Windows only: a spelling that is not a plain drive path (MUST 1): a
    `\\\\?\\` or `\\\\.\\` device path, `\\\\?\\UNC\\`, or any `\\\\server\\share`
    (an admin share such as `\\\\localhost\\C$` included). Such a spelling
    could reach an area by a name the area check does not compare."""
    if os.name != "nt":
        return False
    s = str(text).replace("/", "\\")
    return s.startswith("\\\\")


def _ancestors(path: Path) -> List[Path]:
    """`path` and every folder above it, the drive or root itself left out."""
    chain = [path] + list(path.parents)
    return chain[:-1] if len(chain) > 1 else chain


def _same(a: Path, b: Path) -> bool:
    """The same folder by identity (volume and file id), whatever the
    spelling or case (MUST 1: `/LIBRARY`, `~/library`, `/system`)."""
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def _areas() -> List[Path]:
    if os.name == "nt":
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        areas = [drive / name for name in SYSTEM_ROOTS_WINDOWS]
    else:
        areas = [Path(name) for name in SYSTEM_ROOTS_POSIX]
    areas += [_home() / name for name in HOME_AREAS]
    return [a for a in areas if a.exists()]


def _area_ids() -> set:
    """Each system or secrets area as `(volume, file id)`, for `_protected`
    to compare by identity with one look per folder (round 4: it now runs
    on every file the door opens)."""
    out = set()
    for area in _areas():
        try:
            st = os.stat(area)
            out.add((st.st_dev, st.st_ino))
        except OSError:
            pass
    return out


def _protected(src: Path, areas: Optional[set] = None) -> bool:
    """A hidden folder or dotfile on the way (by name, or by the Windows
    hidden or system attribute), a Keychains folder anywhere, or a system
    or secrets area, compared by identity on every folder above it."""
    chain = _ancestors(src)
    for anc in chain:
        if anc.name.startswith(".") or anc.name.casefold() in ANYWHERE_PARTS:
            return True
        if _attrs(anc) & (ATTR_HIDDEN | ATTR_SYSTEM):
            return True
    ids = _area_ids() if areas is None else areas
    for anc in chain:
        try:
            st = os.stat(anc)
        except OSError:
            continue
        if (st.st_dev, st.st_ino) in ids:
            return True
    return False


def _holds(outer: Path, inner: Path) -> bool:
    """`inner` is `outer` or sits under it, by identity."""
    return any(_same(anc, outer) for anc in [inner] + list(inner.parents))


def source_refusal(source: Any, kind: Any, workspace_root: Any) -> Optional[str]:
    """The status of `judge_source`, for the doors (`workspace_access.
    outside_source_view`)."""
    return judge_source(source, kind, workspace_root)[0]


def judge_source(source: Any, kind: Any, workspace_root: Any
                 ) -> Tuple[Optional[str], Optional[Path]]:
    """None when `source` may be read as a bring-in source of `kind`, else the
    status that refuses it. THE ONE CHECK, used by `plan` and by both doors
    (`workspace_access.outside_source_view`) before anything is listed:
      no_source      not a string, not there, or not the kind's shape (a
                     folder for `folder`, a file for `export`);
      not_local      on Windows, a spelling that is not a drive path (a
                     device or network spelling), as given or as resolved;
      protected      a hidden folder or dotfile on the way (by name or by
                     attribute), or a system or secrets area (`SYSTEM_ROOTS_*`,
                     `HOME_AREAS`, any Keychains folder), compared by identity;
      in_workspace   the workspace itself, inside it, or a folder holding it,
                     compared by identity;
      linked         an export file with more than one name (a hard link,
                     fix round 2 of #1653, MUST 1).
    Judged both as written and as resolved, so a link that leads into a
    protected area or the workspace is refused. Read only; never raises:
    anything it cannot judge is refused. Answers `(status, path)`: on a
    pass `(None, the judged path)`, which is the path listed and read, so
    nothing is resolved a second time (round 3, item 2); on a refusal
    `(status, None)`."""
    try:
        text = str(source).strip() if isinstance(source, str) else ""
        if not text:
            return "no_source", None
        if _not_local(text):
            return "not_local", None
        given = Path(os.path.abspath(os.path.expanduser(text)))
        if not given.exists():
            return "no_source", None
        src = given.resolve()
        if _not_local(str(src)):
            return "not_local", None
        if _protected(given) or _protected(src):
            return "protected", None
        if workspace_root:
            root = Path(str(workspace_root)).resolve()
            if any(_holds(root, a) or _holds(a, root) for a in (given, src)):
                return "in_workspace", None
        if kind == "folder" and not src.is_dir():
            return "no_source", None
        if kind == "export" and not src.is_file():
            return "no_source", None
        if src.is_file() and (_hardlinked(given) or _hardlinked(src)):
            return "linked", None
        return None, src
    except (OSError, ValueError, RuntimeError):
        return "no_source", None


# ---------------------------------------------------------------- projects

def _threads(root: Path) -> Dict[str, Dict[str, Any]]:
    try:
        from entities_io import entities_collection, read_entities_json
        data = read_entities_json(root / "_hq" / "data" / "entities.json")
        threads = entities_collection(data, "threads") if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001 - no book: nothing can be placed
        return {}
    out = {}
    for t in threads or []:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        if str(t.get("status") or "").lower() == "archived":
            continue
        out[str(t["id"])] = t
    return out


def _folder_of(thread: Dict[str, Any]) -> Optional[str]:
    folder = str(thread.get("folder_name") or "").strip().replace("\\", "/").strip("/")
    if not folder or folder in (".", "..") or ".." in folder.split("/") or ":" in folder:
        return None
    return folder


# ---------------------------------------------------------------- plan

def _proposals(by_item: Any, kind: str, listed_ids: set) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """People or companies proposed PER ITEM (MUST 3): `{item_id: [rows]}`.
    Returns the screen's collection, each with an import id of its own, and
    the ids each item names. Rows for an item not listed are dropped."""
    out: List[Dict[str, Any]] = []
    of_item: Dict[str, List[str]] = {}
    if not isinstance(by_item, dict):
        return out, of_item
    n = 0
    for item_id in sorted(by_item):
        if item_id not in listed_ids or not isinstance(by_item[item_id], list):
            continue
        for row in by_item[item_id]:
            if not isinstance(row, dict):
                continue
            name = _clean(row.get("canonical_name") or row.get("name"), NAME_CAP)
            if not name:
                continue
            n += 1
            rec: Dict[str, Any] = {"id": f"bring_in_{kind}_{n:04d}", "canonical_name": name}
            if kind == "people" and row.get("email"):
                rec["email"] = _clean(row.get("email"), NAME_CAP)
            if kind == "orgs" and isinstance(row.get("domains"), list):
                rec["domains"] = [_clean(d, NAME_CAP) for d in row["domains"] if _clean(d, NAME_CAP)]
            out.append(rec)
            of_item.setdefault(item_id, []).append(rec["id"])
    return out, of_item


def _kept_once(rows: List[Dict[str, Any]], keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """Each kept person or company once, by name, in order."""
    seen, out = set(), []
    for r in rows:
        name = r.get("canonical_name")
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append({k: r[k] for k in keys if r.get(k)})
    return out


def _digest(kind: str, source: str, items: List[Dict[str, Any]],
            people: List[Dict[str, Any]], orgs: List[Dict[str, Any]]) -> str:
    shown = {"kind": kind, "source": source,
             "items": [{k: i.get(k) for k in ("item_id", "sha256", "mode", "where", "place",
                                              "summary")} for i in items],
             "people": [{k: p.get(k) for k in ("canonical_name", "email")} for p in people],
             "orgs": [{k: o.get(k) for k in ("canonical_name", "domains")} for o in orgs]}
    blob = json.dumps(shown, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


SOURCE_DOC_ID = "bring_in_source"


def _target_folder(root: Path, thread: Dict[str, Any]) -> Optional[Path]:
    """The project's folder for a copy (SHOULD 8): resolved, inside the
    workspace, and already on disk; None otherwise (the item stays at its
    source, and no folder is ever made for it)."""
    rel = _folder_of(thread)
    if rel is None:
        return None
    try:
        folder = (root / rel).resolve()
        folder.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return folder if folder.is_dir() and not _is_link(root / rel) else None


def _plan(workspace_root, kind, source, mode, overrides, places, summaries, people,
          orgs) -> Dict[str, Any]:
    import import_wall

    root = Path(str(workspace_root or ""))
    kind = str(kind or "").strip()
    if kind not in SOURCES:
        return _refuse("unknown_source", UNKNOWN_SOURCE_LINE)
    if kind not in BUILT_SOURCES:
        return _refuse("not_here", NOT_HERE_LINE.format(what=SOURCE_WORDS[kind]),
                       sources=sources()["sources"])
    # The wall before the source is so much as listed.
    if import_wall.wall(str(root)) is None:
        return _refuse("wall_unreadable", import_wall.STOP_LINE)
    mode = mode if mode in MODES else DEFAULT_MODE
    overrides = overrides if isinstance(overrides, dict) else {}
    places = places if isinstance(places, dict) else {}
    summaries = summaries if isinstance(summaries, dict) else {}
    refused, src = judge_source(source, kind, root)
    if refused == "in_workspace":
        return _refuse("in_workspace", IN_WORKSPACE_LINE)
    if refused == "protected":
        return _refuse("protected", PROTECTED_LINE)
    if refused == "not_local":
        return _refuse("not_local", NOT_LOCAL_LINE)
    if refused == "linked":
        return _refuse("linked", LINKED_LINE)
    if refused is not None or src is None:
        return _refuse("no_source", NO_SOURCE_LINE.format(what=SOURCE_WORDS[kind]))
    # MUST 2: the source's own path is written into every pointer, the log
    # and the pile, so it is screened before anything is listed.
    first = import_wall.screen_import(str(root), {"documents": [
        {"id": SOURCE_DOC_ID, "path": _posix(src), "given": str(source)}]})
    if first.get("stop") or not first.get("ok"):
        return _refuse(first.get("reason") or "wall_unreadable",
                       first.get("line") or import_wall.STOP_LINE)
    if not first["items"].get("documents"):
        return _refuse("walled_source", first.get("line") or import_wall.left_out_line(1),
                       left_out=int(first.get("left_out") or 1))
    try:
        check = import_wall.text_checker(str(root))
        if check is None:
            return _refuse("wall_unreadable", import_wall.STOP_LINE)
        screen = _Screen(check, MAX_SCREEN_BYTES)
        listed, why, skipped = (_list_folder(src, screen) if kind == "folder"
                                else _list_export(src, screen))
        too_big = screen.too_big
    except (OSError, zipfile.BadZipFile):
        return _refuse("no_source", NO_SOURCE_LINE.format(what=SOURCE_WORDS[kind]))
    if listed is None:
        if why == "too_much_to_check":
            return _refuse("too_much_to_check", TOO_MUCH_TO_CHECK_LINE.format(
                mb=MAX_SCREEN_BYTES // (1024 ** 2)), left_out=int(first.get("left_out") or 0))
        if why == "too_big":
            return _refuse("too_big", TOO_BIG_LINE.format(gb=MAX_TOTAL_BYTES // (1024 ** 3)))
        return _refuse(why or "too_many", TOO_MANY_LINE.format(n=MAX_ITEMS))
    for item in listed:
        item["summary"] = _clean(summaries.get(item["item_id"]), SUMMARY_CAP)
    # MUST 3 (ruling 71): each item's own text was judged as it was read
    # (`_Screen`); an item with a walled name anywhere in it is left out
    # whole, counted, before anything else is screened
    walled_text = {i["item_id"] for i in listed if i.get("_walled")}
    listed = [i for i in listed if i["item_id"] not in walled_text]
    listed_ids = {i["item_id"] for i in listed}
    proposed_people, people_of = _proposals(people, "people", listed_ids)
    proposed_orgs, orgs_of = _proposals(orgs, "orgs", listed_ids)
    # MUST 3: each item carries the ids of the people and companies it names,
    # so an item whose people include somebody walled is left out whole (the
    # screen's cascade: a walled person's import id joins the wall). Fix
    # round 2: the door also screens the item's own text when it is text, so
    # the guarantee never rests on what the caller proposed.
    screen = import_wall.screen_import(str(root), {
        "documents": [{"id": i["item_id"], "name": i["name"], "member": i["member"],
                       "source_ref": i["source_ref"], "summary": i["summary"],
                       "person_ids": people_of.get(i["item_id"], []),
                       "org_ids": orgs_of.get(i["item_id"], [])} for i in listed],
        "people": proposed_people,
        "orgs": proposed_orgs,
    }, carried=int(first.get("left_out") or 0) + len(walled_text))
    if screen.get("stop") or not screen.get("ok"):
        return _refuse(screen.get("reason") or "wall_unreadable",
                       screen.get("line") or import_wall.STOP_LINE)
    kept = screen["items"]
    kept_ids = {d.get("id") for d in kept.get("documents") or []}
    kept_people_ids = {p.get("id") for p in kept.get("people") or []}
    kept_org_ids = {o.get("id") for o in kept.get("orgs") or []}
    by_id_people = {p["id"]: p for p in kept.get("people") or [] if p.get("id")}
    by_id_orgs = {o["id"]: o for o in kept.get("orgs") or [] if o.get("id")}
    kept_people = _kept_once([by_id_people[pid] for i in listed if i["item_id"] in kept_ids
                              for pid in people_of.get(i["item_id"], []) if pid in kept_people_ids],
                             ("canonical_name", "email"))
    kept_orgs = _kept_once([by_id_orgs[oid] for i in listed if i["item_id"] in kept_ids
                            for oid in orgs_of.get(i["item_id"], []) if oid in kept_org_ids],
                           ("canonical_name", "domains"))
    threads = _threads(root)
    items: List[Dict[str, Any]] = []
    for item in listed:
        if item["item_id"] not in kept_ids:
            continue
        item_mode = overrides.get(item["item_id"])
        item_mode = item_mode if item_mode in MODES else mode
        place = str(places.get(item["item_id"]) or "").strip()
        thread = threads.get(place)
        where = AT_PROJECT
        if thread is None or (item_mode == COPY and _target_folder(root, thread) is None):
            where, place = AT_SOURCE, ""
        elif item_mode == COPY and _too_long(_target_folder(root, thread), item["name"]):
            skipped += 1  # round 4, item 4: never a copy Windows cannot name
            continue
        row = dict(item, mode=item_mode, where=where, place=place,
                   place_name=_clean((thread or {}).get("canonical_name")
                                     or (thread or {}).get("display_name"), NAME_CAP)
                   if where == AT_PROJECT else "")
        if where == AT_PROJECT and item_mode == LINK and not row["summary"]:
            row["summary"] = _clean(item["name"], SUMMARY_CAP)
        items.append(row)
    digest = _digest(kind, _posix(src), items, kept_people, kept_orgs)
    left_out = int(screen.get("left_out") or 0)
    counts = {"link": sum(1 for i in items if i["where"] == AT_PROJECT and i["mode"] == LINK),
              "copy": sum(1 for i in items if i["where"] == AT_PROJECT and i["mode"] == COPY),
              "at_source": sum(1 for i in items if i["where"] == AT_SOURCE),
              "people": len(kept_people), "orgs": len(kept_orgs), "skipped": skipped,
              "too_big_to_check": too_big}
    return {"ok": True, "stop": False, "status": "preview" if (items or kept_people or kept_orgs)
            else "empty", "kind": kind, "source": _posix(src), "mode": mode,
            "items": items, "people": kept_people, "orgs": kept_orgs,
            "counts": counts, "left_out": left_out, "line": " ".join(x for x in (
                screen.get("line"),
                TOO_BIG_ITEM_LINE.format(what=_plain(too_big, "item", "items")) if too_big else "",
                SKIPPED_LINE.format(what=_plain(skipped - too_big, "item", "items"))
                if skipped > too_big else "") if x),
            "digest": digest, "lines": _preview_lines(items, kept_people, kept_orgs, mode)}


def _preview_lines(items, people, orgs, mode) -> List[str]:
    lines = [f"Each item is {'linked, left in place' if mode == LINK else 'copied in'} "
             "unless you change it below."]
    for i in items:
        if i["where"] == AT_SOURCE:
            lines.append(f"{i['member']}: stays where it is, in Documents with no project")
        elif i["mode"] == COPY:
            lines.append(f"{i['member']}: copied into {i['place_name'] or 'its project'}")
        else:
            lines.append(f"{i['member']}: linked to {i['place_name'] or 'its project'}")
    if people:
        lines.append("New contacts: " + ", ".join(p["canonical_name"] for p in people))
    if orgs:
        lines.append("New companies: " + ", ".join(o["canonical_name"] for o in orgs))
    return lines


def plan(workspace_root, kind: str, source: str = "", mode: str = DEFAULT_MODE,
         overrides: Optional[Dict[str, str]] = None, places: Optional[Dict[str, str]] = None,
         summaries: Optional[Dict[str, str]] = None, people: Optional[Dict[str, list]] = None,
         orgs: Optional[Dict[str, list]] = None) -> Dict[str, Any]:
    """The whole batch's preview. `people` and `orgs` are per item,
    `{item_id: [{"canonical_name", "email" | "domains"}]}`. READ ONLY (`RUN_HELPER_ALLOWLIST`); never
    raises. The wall first, then the source listed, every name screened, each
    item placed on a project the book has or left at its source, and `digest`,
    which `accept` must be handed back unchanged. Internal fields (`_path`,
    `_zip`, `_member`) are left out of the answer."""
    try:
        out = _plan(workspace_root, kind, source, mode, overrides, places, summaries,
                    people, orgs)
    except Exception as exc:  # noqa: BLE001 - a preview never raises
        return _refuse("failed", NO_SOURCE_LINE.format(what="anything"),
                       detail=type(exc).__name__)
    out["items"] = [{k: v for k, v in i.items() if not k.startswith("_")}
                    for i in out.get("items") or []]
    return out


# ---------------------------------------------------------------- accept

def _batch_id() -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(4)}"


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while target.exists():
        n += 1
        target = folder / f"{stem} ({n}){suffix}"
    return target


def _same_bytes_here(folder: Path, name: str, sha: str) -> Optional[Path]:
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while target.exists():
        try:
            if target.is_file() and _sha_file(target) == sha:
                return target
        except OSError:
            pass
        n += 1
        target = folder / f"{stem} ({n}){suffix}"
    return None


#: Windows' path limit (MAX_PATH, the terminating zero included), and the
#: room a copy's temporary name and a " (n)" suffix take (round 4, item 4).
PATH_LIMIT = 259
_TMP_ROOM = len(".bring-in-bin_20260101T000000Z-00000000-000000000000.part") + 8


def _too_long(folder: Optional[Path], name: str) -> bool:
    """A copy into `folder` whose target or temporary name would pass the
    Windows path limit (round 4, item 4: WinError 3 at accept)."""
    if folder is None:
        return False
    base = _units(str(folder)) + 1
    return base + max(_units(name) + 8, _TMP_ROOM) > PATH_LIMIT


def _pump(src, dst, h, size) -> bool:
    """Copy at most the listed `size` bytes (round 5, item 4); False when
    the source holds more, so the copy is refused as changed."""
    limit, n = int(size or 0), 0
    for chunk in iter(lambda: src.read(1 << 20), b""):
        n += len(chunk)
        if n > limit:
            return False
        h.update(chunk)
        dst.write(chunk)
    return True


def _copy_in(root: Path, item: Dict[str, Any], thread: Dict[str, Any], bid: str
             ) -> Tuple[str, Optional[Path]]:
    """`("copied", path)`, `("already_there", path)` or `("changed", None)`.
    Bytes go to a temporary name beside the target, named for the batch
    (`.bring-in-<batch>-<hex>.part`, so a tidy pass can find one a crash left)
    and hashed on the way; they take the target's name only when they are the
    bytes the preview showed, and never over a file that is there. The
    project's folder must already be there: none is made (SHOULD 8)."""
    folder = _target_folder(root, thread)
    if folder is None:
        return "changed", None
    here = _same_bytes_here(folder, item["name"], item["sha256"])
    if here is not None:
        return "already_there", here
    tmp = folder / f".bring-in-{bid}-{secrets.token_hex(6)}.part"
    h = hashlib.sha256()
    try:
        if item.get("_zip"):
            zpath = Path(item["_zip"])
            zfh = _open_checked(zpath, zpath, (item.get("_zip_ino"), item.get("_zip_dev")))
            if zfh is None:
                return "changed", None
            with zfh, zipfile.ZipFile(zfh) as zf, zf.open(item["_member"]) as src, \
                    open(tmp, "xb") as dst:
                within = _pump(src, dst, h, item.get("size"))
        else:
            # rounds 2 and 3: the file opened is the file listed, with one
            # name, inside the judged source, before a byte is read
            src = _open_checked(Path(item["_path"]), Path(item.get("_source") or item["_path"]),
                                (item.get("_ino"), item.get("_dev")))
            if src is None:
                return "changed", None
            with src, open(tmp, "xb") as dst:
                within = _pump(src, dst, h, item.get("size"))
        if not within or h.hexdigest() != item["sha256"]:
            tmp.unlink()
            return "changed", None
        target = _free_name(folder, item["name"])
        try:
            os.rename(tmp, target)  # never replaces: the name was free a moment ago
        except OSError:  # round 4, item 4: a name the system will not take
            tmp.unlink()
            return "changed", None
        return "copied", target
    except (zlib.error, lzma.LZMAError, zipfile.BadZipFile, EOFError):
        # round 6: a member that no longer inflates is a change; the batch
        # rolls back and no part file is left
        if tmp.exists():
            tmp.unlink()
        return "changed", None
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def accept(workspace_root, kind: str, source: str = "", mode: str = DEFAULT_MODE,
           overrides: Optional[Dict[str, str]] = None, places: Optional[Dict[str, str]] = None,
           summaries: Optional[Dict[str, str]] = None, people: Optional[list] = None,
           orgs: Optional[list] = None, digest: str = "",
           accepted_by: str = "") -> Dict[str, Any]:
    """WRITE (`RUN_WRITER_ALLOWLIST`). Bring in exactly what `plan` showed
    for the same arguments, as one batch, after the owner accepted it.

    Plans again first; writes nothing when the wall stops, when the preview
    is empty, or when `digest` is not the preview's (`status: "changed"`).
    Then: each copy into its project's folder, the link notes and the item
    rows in one append, a `left_at_source` row per item that stays at its
    source, and each new person and company through its writer. Every row
    carries the batch id; `batch_ref` is what Undo takes back."""
    root = Path(str(workspace_root or ""))
    shown = _plan(root, kind, source, mode, overrides, places, summaries, people, orgs)
    if shown.get("stop") or not shown.get("ok"):
        return {k: v for k, v in shown.items() if k != "items"} | {"items": []}
    if not digest or digest != shown["digest"]:
        return {"ok": False, "stop": True, "status": "changed", "line": CHANGED_LINE,
                "left_out": shown.get("left_out", 0)}
    if shown["status"] == "empty":
        return {"ok": True, "stop": False, "status": "empty",
                "line": " ".join(x for x in (shown.get("line"), NOTHING_LEFT_LINE) if x),
                "left_out": shown.get("left_out", 0)}
    from event_gate import append_event

    threads = _threads(root)
    bid = _batch_id()
    by = _accepted_by(root, accepted_by)
    rows: List[Dict[str, Any]] = []
    log_rows: List[Dict[str, Any]] = []
    made: List[Path] = []
    counts = {"linked": 0, "copied": 0, "already_there": 0, "at_source": 0,
              "people": 0, "orgs": 0, "on_file": 0}
    try:
        for item in shown["items"]:
            base = {"item_name": item["name"], "member": item["member"],
                    "source_ref": item["source_ref"], "source_kind": shown["kind"],
                    "sha256": item["sha256"], "brought_in_by": by, "brain_batch_id": bid}
            if item["where"] == AT_SOURCE:
                rows.append({"type": ITEM_TYPE, "source_skill": SOURCE_SKILL,
                             "data": dict(base, action=LEFT_AT_SOURCE,
                                          brain_change_class=CLASS_AT_SOURCE)})
                log_rows.append({"ts": _now(), "action": LEFT_AT_SOURCE,
                                 "source": item["source_ref"].split(":", 1)[1],
                                 "type": shown["kind"], "reason": "no project",
                                 "via": SOURCE_SKILL, "brain_batch_id": bid})
                counts["at_source"] += 1
                continue
            thread = threads[item["place"]]
            if item["mode"] == LINK:
                rows.append({"type": "note", "source_skill": SOURCE_SKILL,
                             "primary_thread_id": item["place"],
                             "data": dict(base, summary=item["summary"], via=LINK_VIA,
                                          brain_change_class=CLASS_LINK)})
                counts["linked"] += 1
                continue
            what, path = _copy_in(root, item, thread, bid)
            if what == "changed":
                raise _Changed()
            if what == "already_there":
                counts["already_there"] += 1
                continue
            made.append(path)
            rows.append({"type": ITEM_TYPE, "source_skill": SOURCE_SKILL,
                         "primary_thread_id": item["place"],
                         "data": dict(base, action="copied",
                                      dest_rel=_rel_to(path, root),
                                      brain_change_class=CLASS_COPY)})
            counts["copied"] += 1
        if rows:
            append_event(root / "_hq" / "data" / "events.jsonl", rows, holder=SOURCE_SKILL)
    except _Changed:
        for path in made:
            path.unlink(missing_ok=True)
        return {"ok": False, "stop": True, "status": "changed", "line": CHANGED_LINE,
                "left_out": shown.get("left_out", 0)}
    except BaseException:
        for path in made:  # our own copies, not yet named by any row
            path.unlink(missing_ok=True)
        raise
    if log_rows:
        from atomic_write import atomic_append_jsonl

        (root / "_hq").mkdir(parents=True, exist_ok=True)
        atomic_append_jsonl(root / LOG_REL, log_rows, holder=SOURCE_SKILL, create_ledger=True)
    _people_and_orgs(root, shown, bid, counts)
    return {"ok": True, "stop": False, "status": "brought_in",
            "batch_ref": {"kind": "brain_batch", "batch_id": bid}, "counts": counts,
            "left_out": shown.get("left_out", 0),
            "line": " ".join(x for x in (BROUGHT_LINE.format(what=_said(counts)),
                                         shown.get("line")) if x)}


def _accepted_by(root: Path, accepted_by: Any) -> str:
    """Who accepted, as written on every row (note 13): screened like
    everything else; anything the wall leaves out is written as "owner"."""
    import import_wall

    by = _clean(accepted_by, NAME_CAP)
    if not by:
        return "owner"
    got = import_wall.screen_import(str(root), {"notes": [{"id": "accepted_by", "by": by}]})
    return by if got.get("ok") and got["items"].get("notes") else "owner"


class _Changed(Exception):
    """An item's bytes are not the ones the preview showed."""


def _people_and_orgs(root: Path, shown: Dict[str, Any], bid: str, counts: Dict[str, int]) -> None:
    import org_writer
    import people_writer

    for p in shown.get("people") or []:
        try:
            people_writer.create_person(root, canonical_name=p["canonical_name"],
                                        email=p.get("email"), source_skill=SOURCE_SKILL,
                                        brain_batch_id=bid, brain_change_class=PERSON_ORG_CLASS)
            counts["people"] += 1
        except people_writer.DuplicatePersonError:
            counts["on_file"] += 1
        except ValueError:
            continue
    for o in shown.get("orgs") or []:
        try:
            org_writer.create_org(root, canonical_name=o["canonical_name"],
                                  domains=o.get("domains"), source_skill=SOURCE_SKILL,
                                  brain_batch_id=bid, brain_change_class=PERSON_ORG_CLASS)
            counts["orgs"] += 1
        except org_writer.DuplicateOrgError:
            counts["on_file"] += 1
        except ValueError:
            continue


def _said(counts: Dict[str, int]) -> str:
    parts = []
    if counts["linked"]:
        parts.append(_plain(counts["linked"], "link", "links"))
    if counts["copied"]:
        parts.append(_plain(counts["copied"], "copy", "copies"))
    if counts["at_source"]:
        parts.append(_plain(counts["at_source"], "item left where it is",
                            "items left where they are"))
    if counts["people"]:
        parts.append(_plain(counts["people"], "new contact", "new contacts"))
    if counts["orgs"]:
        parts.append(_plain(counts["orgs"], "new company", "new companies"))
    if counts["already_there"]:
        parts.append(_plain(counts["already_there"], "file already there", "files already there"))
    return ", ".join(parts) if parts else "nothing new"


# ---------------------------------------------------------------- undo

def taken_back_seqs(events: Iterable[dict]) -> set:
    """The seqs of every bring-in row taken back. Pure."""
    out: set = set()
    for ev in events or []:
        if isinstance(ev, dict) and ev.get("type") == UNDONE_TYPE:
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if isinstance(d.get("undoes_seq"), int):
                out.add(d["undoes_seq"])
    return out


def is_link_note(ev: dict) -> bool:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return ev.get("type") == "note" and d.get("via") == LINK_VIA


def _ours(ev: dict) -> bool:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return d.get("brain_change_class") in ITEM_CLASSES and (
        is_link_note(ev) or ev.get("type") == ITEM_TYPE)


def take_back_item(workspace_root, seq, *, undone_by: str, source_skill: str) -> Dict[str, Any]:
    """WRITE, the reverser of the three bring-in classes (`brain_undo`).
    Takes back the one row at `seq`, additively: a link leaves its project,
    a copy is moved to `_archive/bring-in-undo-<batch>/` (never deleted), an
    item left at its source leaves the pile. Then ONE `bring_in_item_undone`
    row names it; the row itself stays in history. `already_undone` when it
    was taken back before; `undo_refused` when there is no such row."""
    from atomic_write import events_write_section
    from event_gate import append_event
    from events_io import load_all

    root = Path(str(workspace_root))
    try:
        want = int(seq)
    except (TypeError, ValueError):
        return {"status": "undo_refused", "why": "not_found"}
    events_path = root / "_hq" / "data" / "events.jsonl"
    with events_write_section(events_path, holder=f"bring_in_undo:{source_skill}"):
        events = [e for e in load_all(root) if isinstance(e, dict)]
        row = next((e for e in events if e.get("seq") == want and _ours(e)), None)
        if row is None:
            return {"status": "undo_refused", "why": "not_found"}
        if want in taken_back_seqs(events):
            return {"status": "already_undone", "seq": want}
        d = row["data"]
        cls = d.get("brain_change_class")
        out: Dict[str, Any] = {"status": "taken_back", "seq": want, "class": cls}
        data: Dict[str, Any] = {"undoes_seq": want, "undone_class": cls,
                                "undone_by": _clean(undone_by, NAME_CAP),
                                # Never `brain_batch_id`: a take back is not a
                                # change of the batch it takes back.
                                "undone_batch_id": d.get("brain_batch_id"),
                                "source_ref": f"undo:{source_skill}:seq:{want}"}
        if cls == CLASS_COPY:
            # Security review of #1653, SHOULD 8 and 9: the copy must be there,
            # inside the workspace and still the bytes it brought in; anything
            # else is refused, writing nothing, and the copy is left as it is.
            dest = root / str(d.get("dest_rel") or "")
            if not (d.get("dest_rel") and _inside(dest, root) and dest.is_file()):
                return {"status": "undo_refused", "why": "gone", "seq": want}
            try:
                same = _sha_file(dest) == d.get("sha256")
            except OSError:
                same = False
            if not same:
                return {"status": "undo_refused", "why": "changed", "seq": want}
            folder = root / ARCHIVE_DIR / f"bring-in-undo-{d.get('brain_batch_id')}"
            try:
                folder.mkdir(parents=True, exist_ok=True)
                target = _free_name(folder, dest.name)
                shutil.move(str(dest), str(target))
            except OSError:
                return {"status": "undo_refused", "why": "could_not_archive", "seq": want}
            data["archived_to"] = _rel_to(target, root)
            out["archived_to"] = data["archived_to"]
        elif cls == CLASS_AT_SOURCE:
            from atomic_write import atomic_append_jsonl

            atomic_append_jsonl(root / LOG_REL, [{
                "ts": _now(), "action": LOG_UNDONE,
                "source": str(d.get("source_ref") or "").split(":", 1)[-1],
                "via": SOURCE_SKILL, "brain_batch_id": d.get("brain_batch_id")}],
                holder=SOURCE_SKILL, create_ledger=True)
        landed = append_event(events_path, [{"type": UNDONE_TYPE, "source_skill": source_skill,
                                             "data": data}], holder=source_skill)
    first = (landed[0] if isinstance(landed, list) and landed else {}) or {}
    out["undone_seq"] = first.get("seq")
    return out


__all__ = ["sources", "plan", "accept", "take_back_item", "taken_back_seqs", "is_link_note",
           "SOURCES", "BUILT_SOURCES", "DEFAULT_MODE", "LINK", "COPY", "ITEM_TYPE",
           "UNDONE_TYPE", "ITEM_CLASSES", "CLASS_LINK", "CLASS_COPY", "CLASS_AT_SOURCE",
           "LINK_VIA", "LOG_REL", "UNDO_OWNER_ONLY"]
