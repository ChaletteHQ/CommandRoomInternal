#!/usr/bin/env python3
"""The document shelf: one index row per document that matters (cr1#1014, train 4).

WHY THIS EXISTS
---------------
Asks about what a document says (a clause in an agreement, a section of a
playbook, what a context file holds) had nothing to reach. The cards and the
lists are built from the log, and a document is not in the log. This module
keeps a shelf: one row per document in the workspace (where it is, its kind,
its date, its headings, one line on what it is, the thread it belongs to),
and opens the document itself when a section is asked for.

THE RULINGS IT FOLLOWS (owner, 2026-11-10; recorded on cr1#1009)
  * A7: no vector search. Finding a document is keyword matching over the
    row's name, path, headings, one line and thread. Nothing is embedded.
  * C3: the one line on a row is for DISPLAY. Recall always goes back to the
    source: `quote_section` opens the file the row points at and returns the
    section's own words, never the row's line.
  * A2: the live data files stay where they are. The stored shelf is a new
    file in a new folder beside them, `_hq/now/shelf.json`.

WHAT IT READS AND WRITES
  * Reads the workspace's folders, and `_hq/data/entities.json` for the
    threads' folders. Never the log.
  * Writes ONE file, `_hq/now/shelf.json`, and only from `rebuild_shelf`
    (on `workspace_access.RUN_WRITER_ALLOWLIST`). The two readers,
    `find_documents` and `quote_section`, write nothing and are on the read
    door's list. A reader with no stored shelf builds one in memory and says
    so (`source: "computed"`).
  * Never moves, renames, edits or deletes a document.
  * `documents_with_no_project` (cr1#1581) lists the rows of the
    "Documents with no project" tidy pile: imports left at their source and
    the files older runs parked in the old holding folders. It writes
    nothing; the pile is cr1#1584's.

WHAT IS ON THE SHELF
  Every file with a document extension (`DOC_EXTS`) under the workspace,
  except system and archive folders (`_SKIP_DIRS`, `_HQ_SKIP_DIRS`) and
  office lock files. A sync conflict copy is listed and flagged (`copy_of`),
  never hidden. A row is filed to the thread whose folder holds it (the
  deepest one). A document under `_hq/` that no thread holds, or that the
  thread kept on `_hq/` itself holds, is in the standing set (meeting
  documents aside). A row with no thread is unfiled, and listed, never
  hidden.

Run by hand (dry run without --apply):
    python3 doc_shelf.py --workspace <root> --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SCHEMA = "doc_shelf/1"
#: The stored shelf, beside the live data folder and never inside it (A2).
SHELF_REL = "_hq/now/shelf.json"

#: The extensions that are documents. Decks and sheets are listed; their
#: sections are not read here (`READABLE_EXTS`).
DOC_EXTS = frozenset({".md", ".markdown", ".txt", ".docx", ".pdf", ".html",
                      ".htm", ".pptx", ".xlsx"})
READABLE_EXTS = frozenset({".md", ".markdown", ".txt", ".docx", ".pdf",
                           ".html", ".htm"})

#: Folder names never walked, at any depth.
_SKIP_DIRS = frozenset({"_archive", "backups", "node_modules",
                        "__pycache__", "venv", ".venv"})
#: Folder names under `_hq/` that hold system state or generated views, not
#: documents (the log and the lists are reached by their own readers).
_HQ_SKIP_DIRS = frozenset({"data", "now", "views", ".system"})
#: The brief is always loaded; it is not a shelf document.
_SKIP_ROOT_FILES = frozenset({"claude.md"})

MAX_DEPTH = 10
MAX_ROWS = 20000
#: A file larger than this is listed but not opened.
MAX_PARSE_BYTES = 25 * 1024 * 1024
DEFAULT_BUILD_BUDGET_S = 90.0
#: A reader with no stored shelf builds one in memory inside the read door's
#: time limit: past this budget the rest are listed unopened (`pending`).
READ_BUILD_BUDGET_S = 20.0
HEADINGS_PER_ROW = 15
ABOUT_CHARS = 160
QUOTE_CHARS = 4000

_DATE_IN_NAME = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_NUMBERED = re.compile(r"^\s*(?:section|clause|article|§)?\s*"
                       r"(\d{1,3}(?:\.\d{1,3})*)[.)]?\s+(\S.*)$", re.IGNORECASE)
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_STOP = frozenset({
    "the", "and", "for", "with", "that", "this", "what", "does", "say", "says",
    "about", "our", "your", "from", "into", "are", "was", "were", "has",
    "have", "which", "who", "how", "when", "where", "there", "their", "them",
    "section", "clause", "article", "part", "doc", "document", "file", "show",
    "quote", "tell", "give", "please", "any", "all", "its", "it's", "not",
})

#: Kind by name or folder, first match wins. Display and filtering only.
_KIND_RULES: Tuple[Tuple[str, re.Pattern], ...] = (
    ("agreement", re.compile(r"agreement|contract|\bnda\b|_nda|nda_|terms|\bmsa\b"
                             r"|\bsow\b|engagement[ _-]letter|\blease\b")),
    ("playbook", re.compile(r"playbook|runbook|procedure|checklist|how[ _-]?to")),
    ("notes", re.compile(r"session_notes|notes")),
    ("context", re.compile(r"project_context|context|profile|background")),
    ("meeting", re.compile(r"(^|/)meetings?/|prep|brief|transcript|minutes|agenda")),
    ("deliverable", re.compile(r"(^|/)deliverables?/")),
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def doc_id_for(rel: str) -> str:
    """A row's id: stable while the document keeps its place."""
    digest = hashlib.sha1(rel.replace("\\", "/").lower().encode("utf-8")).hexdigest()
    return "doc_" + digest[:12]


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= n:
        return text
    cut = text[: n - 1].rsplit(" ", 1)[0]
    return cut + "…"


def _tokens(text: str) -> List[str]:
    return [t for t in _WORD.findall((text or "").lower())
            if len(t) >= 3 and t not in _STOP]


def _kind_of(rel: str) -> str:
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".pptx":
        return "deck"
    if ext == ".xlsx":
        return "sheet"
    low = rel.lower()
    for kind, rx in _KIND_RULES:
        if rx.search(low):
            return kind
    return "document"


def _date_of(name: str, mtime: float) -> Tuple[str, str]:
    m = _DATE_IN_NAME.search(name)
    if m:
        try:
            return (datetime(int(m.group(1)), int(m.group(2)),
                             int(m.group(3))).date().isoformat(), "name")
        except ValueError:
            pass
    return datetime.fromtimestamp(mtime).date().isoformat(), "modified"


def _copy_of(name: str, siblings: Iterable[str]) -> Optional[str]:
    """The sibling `name` looks like a sync service's conflict copy of, if any.

    Flagged on the row, never skipped: the sync services' spellings overlap
    real names (`Plan-Summary.md` beside `Plan.md` reads like a computer's
    copy), and a shelf that hid a real document would be worse than one that
    lists a copy twice."""
    try:
        from sync_conflict_copies import copy_tag
    except Exception:  # pragma: no cover - the module ships beside this one
        return None
    ext = os.path.splitext(name)[1].lower()
    for other in siblings:
        if other == name or os.path.splitext(other)[1].lower() != ext:
            continue
        if copy_tag(other, name) is not None:
            return other
    return None


# ---------------------------------------------------------------------------
# Threads: which folder belongs to which thread (read only)
# ---------------------------------------------------------------------------

def _thread_folders(root: Path) -> List[Dict[str, str]]:
    """Every thread that names a folder, deepest folder first."""
    path = root / "_hq" / "data" / "entities.json"
    try:
        from entities_io import read_entities_json, entities_collection
        data = read_entities_json(path)
        threads = entities_collection(data, "threads") if isinstance(data, dict) else []
    except Exception:
        return []
    out = []
    for t in threads:
        if not isinstance(t, dict):
            continue
        folder = (t.get("folder_name") or "").strip().replace("\\", "/").strip("/")
        if not folder or folder in (".", "..") or ".." in folder.split("/"):
            continue
        out.append({"id": str(t.get("id") or ""),
                    "name": str(t.get("canonical_name") or t.get("display_name") or ""),
                    "aliases": [a for a in (t.get("aliases") or []) if isinstance(a, str)],
                    "folder": folder.lower()})
    out.sort(key=lambda r: -len(r["folder"]))
    return out


def _thread_for(rel: str, folders: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    low = rel.lower()
    for t in folders:
        if low.startswith(t["folder"] + "/"):
            return t
    return None


# ---------------------------------------------------------------------------
# Reading a document into sections
# ---------------------------------------------------------------------------
# A section is {"heading", "level", "number", "text"}. `level` orders the
# outline (smaller is higher); `number` is a clause number when the heading
# carries one ("3", "4.2"). Text before the first heading is a section with
# an empty heading.

def _number_of(heading: str) -> Optional[str]:
    m = _NUMBERED.match(heading or "")
    return m.group(1) if m else None


def _numbered_heading(line: str) -> bool:
    """A short line that opens with a clause number and a capital: `3. Fees`."""
    s = line.strip()
    if not s or len(s) > 100:
        return False
    m = _NUMBERED.match(s)
    if not m:
        return False
    rest = m.group(2).strip()
    return bool(rest) and (rest[0].isupper()) and not rest.endswith((",", ";"))


def _num_tuple(num: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in num.split("."))


def _clause_lines(lines: List[str]) -> set:
    """The indexes of the lines that are numbered clause headings.

    A short numbered line (`3. Term`) is a clause heading unless it is part
    of a numbered LIST: two or more numbered lines in a row, nothing between
    them, each counting on by one from the last (`1. Setup`, `2. Training`).
    Clauses have text under them; list items follow one another. So a list
    inside clause 1 is never taken for clause 2, and "as follows:" before
    clause 1 changes nothing.
    """
    nonblank = [i for i, line in enumerate(lines) if line.strip()]
    cand = {i: _num_tuple(_number_of(lines[i].strip()))
            for i in nonblank if _numbered_heading(lines[i])}
    listed: set = set()
    run: List[int] = []
    for i in nonblank:
        if i in cand and run and _follows(cand[run[-1]], cand[i]):
            run.append(i)
            continue
        if len(run) >= 2:
            listed.update(run)
        run = [i] if i in cand else []
    if len(run) >= 2:
        listed.update(run)
    return set(cand) - listed


def _follows(prev: Tuple[int, ...], cur: Tuple[int, ...]) -> bool:
    """`cur` counts on by one from `prev` at the same depth (`2` after `1`)."""
    return (len(prev) == len(cur) and prev[:-1] == cur[:-1]
            and cur[-1] == prev[-1] + 1)


def _plain_sections(lines: List[str], markdown: bool) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = [{"heading": "", "level": 0, "number": None, "body": []}]
    clauses = set() if markdown else _clause_lines(lines)
    for idx, raw in enumerate(lines):
        line = raw.rstrip("\n")
        m = _MD_HEADING.match(line) if markdown else None
        if m:
            text = m.group(2).strip()
            sections.append({"heading": text, "level": len(m.group(1)),
                             "number": _number_of(text), "body": []})
            continue
        if idx in clauses:
            text = line.strip()
            num = _number_of(text)
            sections.append({"heading": text, "level": 10 + num.count("."),
                             "number": num, "body": []})
            continue
        sections[-1]["body"].append(line)
    return _finish(sections)


def _finish(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in sections:
        text = "\n".join(s.pop("body")).strip()
        if not s["heading"] and not text:
            continue
        s["text"] = text
        out.append(s)
    return out


def _docx_sections(path: Path) -> List[Dict[str, Any]]:
    import docx  # python-docx, pinned in command-room/requirements.txt
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx_accepted import paragraph_text

    document = docx.Document(str(path))
    body = document.element.body
    blocks: List[Tuple[str, str, Optional[str], bool]] = []
    styled = False
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, document)
            style = (p.style.name if p.style is not None else "") or ""
            bold = bool(p.runs) and all(r.bold for r in p.runs if r.text.strip())
            if style.lower().startswith(("heading", "title")):
                styled = True
            # cr1#1004: the text with tracked changes accepted. `p.text`
            # reads only direct runs and skips every tracked insertion.
            blocks.append(("p", paragraph_text(child), style, bold))
        elif tag == "tbl":
            t = Table(child, document)
            rows = []
            for r in t.rows:
                cells = []
                for c in r.cells:
                    txt = " ".join(" ".join(paragraph_text(cp._p)
                                            for cp in c.paragraphs).split())
                    if not cells or cells[-1] != txt:
                        cells.append(txt)
                rows.append(" | ".join(cells))
            blocks.append(("t", "\n".join(rows), None, False))

    sections: List[Dict[str, Any]] = [{"heading": "", "level": 0, "number": None, "body": []}]
    clauses = set() if styled else _clause_lines(
        [text if kind == "p" else "" for kind, text, _s, _b in blocks])
    for idx, (kind, text, style, bold) in enumerate(blocks):
        stripped = text.strip()
        is_heading = False
        level = 0
        if kind == "p" and stripped:
            low = (style or "").lower()
            numbered = idx in clauses
            if low.startswith("title"):
                is_heading, level = True, 0
            elif low.startswith("heading"):
                digits = re.findall(r"\d+", low)
                is_heading, level = True, int(digits[0]) if digits else 1
            elif numbered:
                is_heading, level = True, 10 + _number_of(stripped).count(".")
            elif (not styled and bold and len(stripped) <= 80
                  and not _numbered_heading(stripped)):
                is_heading, level = True, 5
        if is_heading:
            sections.append({"heading": stripped, "level": level,
                             "number": _number_of(stripped), "body": []})
        else:
            sections[-1]["body"].append(text)
    return _finish(sections)


def _pdf_sections(path: Path) -> List[Dict[str, Any]]:
    try:
        import pypdf
    except ImportError as exc:  # loud: the row says the reader is missing
        raise RuntimeError("no PDF reader is installed on this computer") from exc
    import logging
    # A damaged PDF makes the reader log a line per bad object; the row's
    # `unread` reason is the one place a failure is said.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = pypdf.PdfReader(str(path))
    lines: List[str] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        lines.extend(text.splitlines())
    sections = _plain_sections(lines, markdown=False)
    if len(sections) <= 1:
        # No numbered headings: one section per page, so a quote stays short.
        out = []
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                out.append({"heading": f"Page {i}", "level": 1, "number": None,
                            "text": text})
        return out
    return sections


class _HTMLSections(HTMLParser):
    _HEAD = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
    _SKIP = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: List[Dict[str, Any]] = [
            {"heading": "", "level": 0, "number": None, "body": []}]
        self._in_head: Optional[int] = None
        self._head_text: List[str] = []
        self._skip = 0
        self._buf: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._HEAD:
            self._flush()
            self._in_head = self._HEAD[tag]
            self._head_text = []
        elif tag in ("p", "li", "tr", "br", "div"):
            self._flush()

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._HEAD and self._in_head is not None:
            text = " ".join("".join(self._head_text).split())
            self.sections.append({"heading": text, "level": self._in_head,
                                  "number": _number_of(text), "body": []})
            self._in_head = None
        elif tag in ("p", "li", "tr", "div"):
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_head is not None:
            self._head_text.append(data)
        else:
            self._buf.append(data)

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        if text:
            self.sections[-1]["body"].append(text)
        self._buf = []

    def result(self) -> List[Dict[str, Any]]:
        self._flush()
        return _finish(self.sections)


def read_sections(path: Path) -> List[Dict[str, Any]]:
    """The document's sections, in order. Raises on a file it cannot read."""
    ext = path.suffix.lower()
    if ext in (".md", ".markdown"):
        return _plain_sections(path.read_text(encoding="utf-8", errors="replace").splitlines(),
                               markdown=True)
    if ext == ".txt":
        return _plain_sections(path.read_text(encoding="utf-8", errors="replace").splitlines(),
                               markdown=False)
    if ext == ".docx":
        return _docx_sections(path)
    if ext == ".pdf":
        return _pdf_sections(path)
    if ext in (".html", ".htm"):
        parser = _HTMLSections()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        parser.close()
        return parser.result()
    raise RuntimeError("this kind of file is listed but not read here")


def _about(sections: List[Dict[str, Any]]) -> str:
    """One line on what the document is: its first real sentence. Display only."""
    for s in sections:
        for para in re.split(r"\n\s*\n|\n", s.get("text") or ""):
            p = " ".join(para.split())
            if len(p) < 30 or p.startswith(("|", "-", "*", ">")):
                continue
            first = re.split(r"(?<=[.!?])\s", p, maxsplit=1)[0]
            return _clip(first, ABOUT_CHARS)
    for s in sections:
        if s.get("heading"):
            return _clip(s["heading"], ABOUT_CHARS)
    return ""


# ---------------------------------------------------------------------------
# Building the shelf
# ---------------------------------------------------------------------------

def _walk(root: Path) -> Iterable[Tuple[Path, List[str]]]:
    """Every candidate document under `root` with its folder's file names,
    in a stable order."""
    stack: List[Tuple[Path, int]] = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        names = [e.name for e in entries]
        subdirs = []
        for e in entries:
            name = e.name
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                low = name.lower()
                if name.startswith((".", "~")) or low in _SKIP_DIRS or low.startswith("_archive"):
                    continue
                if depth == 1 and d.name.lower() == "_hq" and d.parent == root \
                        and low in _HQ_SKIP_DIRS:
                    continue
                if depth < MAX_DEPTH:
                    subdirs.append((Path(e.path), depth + 1))
                continue
            low = name.lower()
            if os.path.splitext(low)[1] not in DOC_EXTS:
                continue
            if name.startswith(("~$", ".")):
                continue
            if depth == 0 and low in _SKIP_ROOT_FILES:
                continue
            yield Path(e.path), names
        stack.extend(reversed(subdirs))


def _row_for(path: Path, root: Path, st: os.stat_result,
             parse: bool) -> Dict[str, Any]:
    rel = _rel(path, root)
    date, date_source = _date_of(path.name, st.st_mtime)
    row: Dict[str, Any] = {
        "id": doc_id_for(rel),
        "path": rel,
        "name": path.name,
        "kind": _kind_of(rel),
        "date": date,
        "date_source": date_source,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "headings": [],
        "about": "",
        "readable": path.suffix.lower() in READABLE_EXTS,
    }
    if not row["readable"]:
        return row
    if not parse:
        row["pending"] = True
        return row
    if st.st_size > MAX_PARSE_BYTES:
        row["unread"] = "too large to open here"
        return row
    try:
        sections = read_sections(path)
    except Exception as exc:  # one bad file never stops the shelf
        row["unread"] = _clip(str(exc) or type(exc).__name__, 120)
        return row
    heads = [s for s in sections if s.get("heading")]
    if heads:
        top = min(s["level"] for s in heads)
        # The outline's top two levels, so a clause list shows its clauses.
        keep = [s for s in heads if s["level"] <= top + 1] or heads
        row["headings"] = [_clip(s["heading"], 80) for s in keep[:HEADINGS_PER_ROW]]
    row["about"] = _about(sections)
    return row


def load_stored(workspace_root) -> Optional[Dict[str, Any]]:
    """The stored shelf, or None when there is none or it is unreadable."""
    path = Path(workspace_root) / SHELF_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA \
            or not isinstance(data.get("rows"), list):
        return None
    return data


def build_shelf(workspace_root, *, previous: Optional[Dict[str, Any]] = None,
                budget_s: float = DEFAULT_BUILD_BUDGET_S,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Compute the shelf. Writes nothing.

    A row from `previous` whose file has the same size and modification time
    is kept as it was, so a rebuild opens only what changed. When `budget_s`
    runs out, the remaining documents are listed with `pending: true` and no
    headings, and the next rebuild opens them.
    """
    root = Path(workspace_root).resolve()
    started = time.monotonic()
    old: Dict[str, Dict[str, Any]] = {}
    for r in (previous or {}).get("rows") or []:
        if isinstance(r, dict) and r.get("path") and not r.get("pending"):
            old[r["path"]] = r
    folders = _thread_folders(root)
    rows: List[Dict[str, Any]] = []
    reused = opened = pending = 0
    truncated = False
    for path, siblings in _walk(root):
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        try:
            st = path.stat()
        except OSError:
            continue
        rel = _rel(path, root)
        prior = old.get(rel)
        if prior and prior.get("size") == st.st_size and prior.get("mtime_ns") == st.st_mtime_ns:
            row = dict(prior)
            reused += 1
        else:
            parse = (time.monotonic() - started) < budget_s
            row = _row_for(path, root, st, parse)
            if row.get("pending"):
                pending += 1
            elif row["readable"]:
                opened += 1
        copy_of = _copy_of(path.name, siblings) if len(siblings) <= 500 else None
        if copy_of:
            row["copy_of"] = copy_of
        else:
            row.pop("copy_of", None)
        thread = _thread_for(rel, folders)
        row["thread"] = thread["id"] if thread else None
        row["thread_name"] = thread["name"] if thread else None
        # The standing set: what sits under _hq/ (meeting documents aside)
        # and belongs to no thread, or to the thread kept on _hq/ itself.
        row["standing"] = bool((thread is None or thread["folder"] == "_hq")
                               and rel.lower().startswith("_hq/")
                               and not rel.lower().startswith("_hq/meetings/"))
        rows.append(row)
    built = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema": SCHEMA,
        "built_at": built.isoformat().replace("+00:00", "Z"),
        "counts": {
            "rows": len(rows),
            "threads": len({r["thread"] for r in rows if r.get("thread")}),
            "standing": sum(1 for r in rows if r.get("standing")),
            "unfiled": sum(1 for r in rows if not r.get("thread") and not r.get("standing")),
            "reused": reused,
            "opened": opened,
            "pending": pending,
            "unread": sum(1 for r in rows if r.get("unread")),
        },
        "truncated": truncated,
        "rows": rows,
    }


def rebuild_shelf(workspace_root, apply: bool = True,
                  budget_s: float = DEFAULT_BUILD_BUDGET_S) -> Dict[str, Any]:
    """Rebuild the stored shelf at `_hq/now/shelf.json` from the folders.

    The one writer in this module. It writes that one file and nothing else:
    no log entry, no document touched. A dry run (`apply=False`) computes and
    reports the counts and writes nothing.
    """
    root = Path(workspace_root)
    shelf = build_shelf(root, previous=load_stored(root), budget_s=budget_s)
    written = False
    if apply:
        from atomic_write import atomic_write_json
        atomic_write_json(root / SHELF_REL, shelf, indent=None)
        written = True
    return {"ok": True, "written": written, "rel": SHELF_REL,
            "built_at": shelf["built_at"], "counts": shelf["counts"],
            "truncated": shelf["truncated"]}


# ---------------------------------------------------------------------------
# Reading the shelf
# ---------------------------------------------------------------------------

def _shelf_for_read(root: Path, budget_s: float = READ_BUILD_BUDGET_S
                    ) -> Tuple[Dict[str, Any], str]:
    stored = load_stored(root)
    if stored is not None:
        # Walk the folder against the stored rows without opening anything:
        # an unchanged document keeps its stored row, a new or changed one
        # is listed `pending` (found by its name and place, and counted, so
        # the caller knows to rebuild), and a removed one stays listed,
        # marked `gone`, so an ask about it can say it moved.
        fresh = build_shelf(root, previous=stored, budget_s=0)
        fresh["built_at"] = stored.get("built_at")
        seen = {r.get("path") for r in fresh["rows"]}
        for r in stored.get("rows") or []:
            if isinstance(r, dict) and r.get("path") and r["path"] not in seen:
                fresh["rows"].append(dict(r, gone=True))
        return fresh, "stored"
    return build_shelf(root, budget_s=budget_s), "computed"


def _match_thread(rows: List[Dict[str, Any]], root: Path, thread: str) -> List[Dict[str, Any]]:
    want = (thread or "").strip().lower()
    ids = {want}
    for t in _thread_folders(root):
        names = [t["name"].lower()] + [a.lower() for a in t["aliases"]]
        if want in names:
            ids.add(t["id"].lower())
    return [r for r in rows if (r.get("thread") or "").lower() in ids
            or (r.get("thread_name") or "").lower() == want]


def _score_row(row: Dict[str, Any], terms: List[str]) -> int:
    hay_strong = " ".join([row.get("name") or "", " ".join(row.get("headings") or [])]).lower()
    hay_weak = " ".join([row.get("path") or "", row.get("about") or "",
                         row.get("thread_name") or "", row.get("kind") or ""]).lower()
    strong = set(_tokens(hay_strong))
    weak = set(_tokens(hay_weak))
    score = 0
    for t in terms:
        if t in strong:
            score += 3
        elif t in weak:
            score += 1
    return score


def find_documents(workspace_root, query: Optional[str] = None,
                   thread: Optional[str] = None, kind: Optional[str] = None,
                   standing: Optional[bool] = None, limit: int = 25) -> Dict[str, Any]:
    """Rows from the shelf, filtered and, with a query, ranked by keyword hits.

    Read only. `thread` is a thread id or name; `kind` one of the row kinds;
    `standing` true for the standing set. Each row carries its `id` and
    `path`: open the document with `quote_section`, never answer from the
    row's one line (C3).
    """
    root = Path(workspace_root)
    shelf, source = _shelf_for_read(root)
    rows = [r for r in shelf.get("rows") or [] if isinstance(r, dict)]
    if thread:
        rows = _match_thread(rows, root, thread)
    if kind:
        rows = [r for r in rows if (r.get("kind") or "") == kind.strip().lower()]
    if standing is not None:
        rows = [r for r in rows if bool(r.get("standing")) == bool(standing)]
    terms = _tokens(query or "")
    if terms:
        scored = [(_score_row(r, terms), r) for r in rows]
        scored = [(s, r) for s, r in scored if s > 0]
        scored.sort(key=lambda sr: (-sr[0], -(int(sr[1].get("mtime_ns") or 0))))
        rows = [r for _, r in scored]
    else:
        rows.sort(key=lambda r: (r.get("date") or "", r.get("path") or ""), reverse=True)
    total = len(rows)
    keep = []
    for r in rows[: max(1, min(int(limit or 25), 200))]:
        out = {k: r.get(k) for k in ("id", "path", "kind", "date", "headings", "about",
                                     "thread", "thread_name", "standing", "readable")}
        if r.get("unread"):
            out["unread"] = r["unread"]
        if r.get("pending"):
            out["pending"] = True
        if r.get("copy_of"):
            out["copy_of"] = r["copy_of"]
        if not (root / r["path"]).is_file():
            out["gone"] = True
        keep.append(out)
    return {"ok": True, "source": source, "built_at": shelf.get("built_at"),
            "total": total, "rows": keep, "pending": shelf.get("counts", {}).get("pending", 0),
            "truncated": bool(shelf.get("truncated"))}


def _resolve_doc(root: Path, doc: str) -> Tuple[Optional[Path], str, Optional[str]]:
    """The file a doc id or workspace-relative path names, inside the root."""
    doc = (doc or "").strip()
    if not doc:
        return None, "", "no document named"
    rel = None
    if re.fullmatch(r"doc_[0-9a-f]{12}", doc):
        shelf, _ = _shelf_for_read(root, budget_s=0)  # only the places are needed
        for r in shelf.get("rows") or []:
            if isinstance(r, dict) and r.get("id") == doc:
                rel = r.get("path")
                break
        if rel is None:
            return None, "", "that document is not on the shelf"
    else:
        rel = doc.replace("\\", "/")
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts) or Path(rel).is_absolute():
        return None, "", "that is not a document in this folder"
    path = root.joinpath(*parts)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return None, "", "that is not a document in this folder"
    if not path.is_file():
        return None, "", "that document is no longer in the folder"
    return path, "/".join(parts), None


def _spans(sections: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """For each section, the run of sections it covers, its subsections included."""
    out = []
    for i, s in enumerate(sections):
        end = i + 1
        if s.get("heading"):
            num = s.get("number")
            while end < len(sections):
                nxt = sections[end]
                if num and nxt.get("number"):
                    if not nxt["number"].startswith(num + "."):
                        break
                elif nxt.get("level", 0) <= s.get("level", 0) and nxt.get("heading"):
                    break
                end += 1
        out.append((i, end))
    return out


def _wanted_number(query: str) -> Optional[str]:
    m = re.fullmatch(r"\s*(?:section|clause|article|part|§)?\s*(\d{1,3}(?:\.\d{1,3})*)\.?\s*",
                     query or "", re.IGNORECASE)
    return m.group(1) if m else None


def quote_section(workspace_root, doc: str, query: Optional[str] = None,
                  max_chars: int = QUOTE_CHARS) -> Dict[str, Any]:
    """Open the document and return the section that answers `query`, in its own words.

    Read only. `doc` is a shelf id (`doc_...`) or a workspace-relative path.
    `query` is a clause number ("3", "section 4.2"), a heading, or keywords;
    without one the answer lists the document's headings. The text comes from
    the file as it is now, never from the shelf's stored line (C3).
    """
    root = Path(workspace_root)
    path, rel, why = _resolve_doc(root, doc)
    if path is None:
        return {"ok": False, "reason": why}
    base = {"doc_id": doc_id_for(rel), "path": rel}
    if path.suffix.lower() not in READABLE_EXTS:
        return dict(base, ok=False, reason="this kind of file is listed but not read here")
    try:
        sections = read_sections(path)
    except Exception as exc:
        return dict(base, ok=False, reason=_clip(str(exc) or type(exc).__name__, 120))
    headings = [s["heading"] for s in sections if s.get("heading")]
    if not (query or "").strip():
        return dict(base, ok=True, match=None, headings=headings)
    spans = _spans(sections)

    def text_of(i: int) -> str:
        a, b = spans[i]
        parts = []
        for s in sections[a:b]:
            if s.get("heading") and s is not sections[i]:
                parts.append(s["heading"])
            if s.get("text"):
                parts.append(s["text"])
        return "\n\n".join(parts).strip()

    ranked: List[Tuple[int, int, int]] = []  # (score, -span, index)
    number = _wanted_number(query or "")
    if number:
        for i, s in enumerate(sections):
            if s.get("number") == number:
                ranked.append((1000, 0, i))
                break
    if not ranked:
        terms = _tokens(query or "")
        qlow = " ".join((query or "").lower().split())
        for i, s in enumerate(sections):
            head = (s.get("heading") or "").lower()
            # Scored on the section's OWN words: a title or a parent clause
            # spans everything under it, and scoring that span would let it
            # win every query whose words sit in two clauses.
            body = (s.get("text") or "").lower()
            score = 0
            if qlow and head and qlow in head:
                score += 50
            body_tokens = _tokens(body)
            head_tokens = set(_tokens(head))
            for t in terms:
                if t in head_tokens:
                    score += 10
                hits = body_tokens.count(t)
                if hits:
                    score += 3 + min(hits, 5)
            if score:
                a, b = spans[i]
                ranked.append((score, -(b - a), i))
        ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    if not ranked:
        return dict(base, ok=True, match=None, headings=headings)
    best = ranked[0][2]
    text = text_of(best)
    clipped = len(text) > max_chars
    if clipped:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    others = [{"heading": sections[i].get("heading") or "(opening text)",
               "number": sections[i].get("number")}
              for _, _, i in ranked[1:4]]
    return dict(base, ok=True,
                match={"heading": sections[best].get("heading") or "(opening text)",
                       "number": sections[best].get("number"),
                       "text": text, "clipped": clipped},
                other_matches=others)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Documents with no project: the rows of the tidy pile (cr1#1581)
# ---------------------------------------------------------------------------

#: The bring-in's own run log (workspace-ingest Phase 8). An imported file
#: that matched no project is not copied anywhere: the run records it here as
#: left at its source, and this reader lists it from there.
INGEST_LOG_REL = "_hq/_ingest-undo.jsonl"
#: "undo migration" undoes the LATEST run and renames the WHOLE log to
#: `_hq/_ingest-undo-reverted-<ts>.jsonl` (workspace-ingest, Undo step 7), so
#: the runs before it live on in that file. Their rows still count; the undone
#: run's (from the file's last `run_start` on) do not (cr1#1581 review round 1).
INGEST_REVERTED_GLOB = "_ingest-undo-reverted-*.jsonl"
RUN_START = "run_start"
LEFT_AT_SOURCE = "left_at_source"
#: Holding folders that older runs and older skill text wrote to. Nothing
#: writes to them now; what is in them is listed, never moved (ruling 4 of
#: cr1#1579: the owner's material is a pile, cleared by a press).
OLD_HOLDING = (
    ("_hq/deliverables", "old_home"),
    ("_hq/_ingest-queue", "ingest_queue"),
    ("_hq/_unrouted", "set_aside"),
)


def _no_project_home() -> str:
    try:
        from deliverables import NO_PROJECT_HOME
    except Exception:  # noqa: BLE001 - a reader never fails on a borrowed name
        return "_hq/notes"
    return NO_PROJECT_HOME


def _log_rows(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _effective_ingest_rows(root: Path) -> List[Dict[str, Any]]:
    """Every bring-in row that still stands, oldest first: each reverted log
    up to its last `run_start` (the run "undo migration" took back starts
    there), in the order the reverted logs were made, then the live log. A
    reverted log with no run header is taken as undone whole."""
    rows: List[Dict[str, Any]] = []
    for path in sorted((root / "_hq").glob(INGEST_REVERTED_GLOB)):
        file_rows = _log_rows(path)
        starts = [i for i, r in enumerate(file_rows) if r.get("action") == RUN_START]
        rows.extend(file_rows[:starts[-1]] if starts else [])
    rows.extend(_log_rows(root / INGEST_LOG_REL))
    return rows


def _left_at_source(root: Path) -> List[Dict[str, Any]]:
    """The imports recorded as left at their source, the newest word on each
    source winning: a later run that filed the same file takes it off, and a
    run that was undone says nothing."""
    latest: Dict[str, Dict[str, Any]] = {}
    for row in _effective_ingest_rows(root):
        src = str(row.get("source") or "").strip()
        if src:
            latest[src] = row
    out = []
    for src, row in latest.items():
        if row.get("action") != LEFT_AT_SOURCE:
            continue
        out.append({
            "where": "at_source",
            "source": src,
            "name": Path(src).name,
            "present": Path(src).is_file(),
            "type": row.get("type"),
            "reason": row.get("reason"),
            "since": row.get("ts"),
        })
    return sorted(out, key=lambda r: r["source"])


def _queue_source(path: Path) -> Optional[str]:
    meta = path.with_name(path.name + ".meta.json")
    if not meta.is_file():
        meta = path.with_suffix(".meta.json")
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    src = data.get("source_path") or data.get("source")
    return str(src) if src else None


def _held_files(folder: Path) -> List[Path]:
    """Every file under a holding folder, whatever its kind (an import can be
    a picture or a sheet), minus a parked file's own note, hidden files and
    office lock files."""
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith((".", "~$")) or name.lower().endswith(".meta.json"):
                continue
            out.append(Path(dirpath) / name)
    return out


def documents_with_no_project(workspace_root) -> List[Dict[str, Any]]:
    """The "Documents with no project" pile's rows. Read only: nothing is
    moved, copied or created, and no folder is made (there is no Unfiled
    folder anywhere). The pile itself, its presses and its Undo are
    cr1#1584's.

    Rows, each with `where`:
      at_source     an imported file that matched no project, left where it
                    is (`source`, and `present` when it is still there)
      old_home      a document in the old no-project folder; `home` is where
                    one with no project lands now (`_hq/notes/`)
      ingest_queue  an import an older run parked (`source` from its note)
      set_aside     an import an older run set aside at the preview
    Documents already in `_hq/notes/` are home and are not listed."""
    root = Path(workspace_root)
    rows: List[Dict[str, Any]] = list(_left_at_source(root))
    home = _no_project_home()
    for folder_rel, where in OLD_HOLDING:
        folder = root / folder_rel
        if not folder.is_dir():
            continue
        for path in _held_files(folder):
            rel = _rel(path, root)
            row: Dict[str, Any] = {"where": where, "path": rel, "name": path.name}
            if where == "old_home":
                row["home"] = f"{home}/{path.name}"
            elif where == "ingest_queue":
                row["source"] = _queue_source(path)
            rows.append(row)
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild the document shelf.")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="write _hq/now/shelf.json (a dry run without it)")
    ap.add_argument("--budget-s", type=float, default=DEFAULT_BUILD_BUDGET_S)
    args = ap.parse_args(argv)
    result = rebuild_shelf(args.workspace, apply=args.apply, budget_s=args.budget_s)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
