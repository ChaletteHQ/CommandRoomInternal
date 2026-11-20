#!/usr/bin/env python3
"""The owner's past rulings, pulled in once as decisions to confirm
(cr1#1015, train 6 of the memory and Hub programme).

WHY THIS EXISTS
---------------
A decision list built from the log alone states an older position as current
wherever the owner ruled later in a file the log never saw: a rulings file, a
decisions page, or the workspace brief itself (`CLAUDE.md`). The brief is cut
only after this list lands (ruling A5), so its rulings have to reach the list
first.

WHAT IT DOES
------------
`pull_past_rulings` reads the rulings files, finds each dated ruling in them,
and proposes each one as a decision row on the Review tab (the open question
queue the Staff Meeting and the Hub's Review tab read). A proposal is not a
decision: it counts only once the owner confirms it, through
`confirm_past_ruling`, which writes the `decision` (dated the day it was
ruled, `data.decided_at`) and the replacement links. A no, a snooze and a
lapse are the queue's own (`brain_proposals`).

THE RULINGS FILES
-----------------
  * the workspace brief at the workspace root, `CLAUDE.md`, where only a line
    that says it is a ruling or a decision, and carries a date or sits under
    a dated heading, is a ruling;
  * every Markdown or text file whose name says `ruling` or `decision`, read
    the same way as the brief: a dated line is a ruling only when it says so
    or sits under a heading that says so, and a table row is not read. A
    decision-memo draft's dated notes are not rulings (cr1#1073 item 2,
    owner's rule A);
  * any file the caller names (`paths`), where every dated line is a ruling,
    table rows included.
The folders the product keeps for itself (`_hq/data`, `_hq/views`, the now
folder, `.system`) and archives are never walked.

THE THREAD A RULING CARRIES
---------------------------
A ruling filed under a heading that names a thread on file, by its exact
name or alias ("## Acme Co", "## Acme Co: hosting"), carries that thread: the
proposal holds it and the yes stamps the decision with it. The innermost such
heading wins. Only a live thread's own name or alias counts: a folder or
display name, a near match, an archived thread, or a name two threads share
stamps nothing, and a thread archived before the yes is not stamped
(cr1#1073).

WHAT ONE RULING REPLACES
------------------------
A ruling replaces an older one on the same subject: the latest earlier
ruling, from this pull or already a decision in the log, whose words share
enough of its own (`same_subject`). The bar is lower when the newer ruling
says so in words ("replaces", "instead of", "retired"). The proposal names
what it would replace, so the owner sees it before confirming; the link is
written only on that yes. When the older ruling is confirmed after the newer
one, its decision is written already replaced.

READ AND WRITE
--------------
The pull reads the rulings files and the log, and appends only
`brain_proposal` rows (through `event_gate`). The confirm appends one
`decision`, a `decision_superseded` per ruling it replaces, and the
proposal's resolution. Nothing else is written. The tests run on invented
rulings only.

Command line, from the plugin root:
    python shared/scripts/past_rulings.py <workspace> [--apply] [--path REL ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

KIND = "past_ruling"
DETECTOR = "past-rulings"
SOURCE_SKILL = "decision-log"
#: The brief at the workspace root.
BRIEF_FILE = "CLAUDE.md"
#: A file whose name holds one of these is a rulings file.
FILE_WORDS = ("ruling", "decision")
SUFFIXES = (".md", ".markdown", ".txt")
#: Never walked, anywhere: archives and tooling.
SKIP_DIRS = frozenset({"_archive", ".system", ".git", "node_modules",
                       "__pycache__", ".claude"})
#: Never walked: the product's own folders under `_hq` (the log, the views
#: it regenerates, the now folder).
SKIP_HQ_DIRS = frozenset({"data", "views", "now"})
MAX_DEPTH = 6
MAX_FILE_BYTES = 2_000_000
#: A ruling shorter than this is a label, not a ruling.
MIN_CHARS = 12
WHAT_CHARS = 300
EVIDENCE_CHARS = 280
#: Rulings put on the Review tab per day; the rest wait their turn
#: (`surface_on`, the cr1#773 stamp), so a long file never floods the tab.
PER_DAY = 10

#: A date standing on its own: not inside a path or a word
#: (`cleanup-2026-08-19/x.md` is a folder name, not a date).
_DATE = re.compile(r"(?<![\w/.-])(20\d\d)-(\d\d)-(\d\d)(?![\w/-])")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")
#: A line that says it is a ruling (needed outside a rulings file).
_MARKER = re.compile(r"\b(rulings?|ruled|decided|decisions?|binding)\b", re.I)
#: A file title that names a working document, not a rulings log (cr1#1073).
#: Letters only around the word, so `memo_draft` reads as memo and draft.
_NOT_A_LOG_TITLE = re.compile(r"(?<![a-z])(memos?|memorandum|drafts?)(?![a-z])", re.I)
#: A newer ruling that says it replaces an older one.
_REPLACE_WORDS = re.compile(
    r"\b(supersed\w*|replac\w*|retir\w*|instead|revers\w*|overrid\w*|"
    r"no longer|amend\w*|withdraw\w*|scrap\w*|drop(?:s|ped)?)\b", re.I)
_NAME = r"[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,3}"
#: Who ruled: "(Sam ruling)", "ruled by Sam", "ruling by Sam".
_WHO = (re.compile(r"\(\s*(" + _NAME + r"|owner)\s+rul(?:ing|ed)\b"),
        re.compile(r"\brul(?:ing|ed)\s+by\s+(" + _NAME + r")"))
#: The whole parenthetical that names who ruled, to drop from what was ruled.
#: Only a name or `owner` before the word: "(see ruling above)" stays.
_WHO_NOTE = re.compile(r"\(\s*(?:the\s+)?(?:" + _NAME + r"|owner)\s+rul(?:ing|ed)\b[^)]*\)")
#: Words that say nothing about a ruling's subject.
_NOISE = frozenset({
    "ruling", "rulings", "ruled", "decided", "decision", "decisions", "binding",
    "owner", "set", "now", "new", "old", "also", "only", "never", "always",
    "will", "would", "should", "must", "can", "not", "but", "per", "via",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
    "nov", "dec", "replaces", "replaced", "replace", "supersedes",
    "superseded", "instead", "retired", "amended", "longer", "one", "two",
})


# ---------------------------------------------------------------------------
# Finding the rulings
# ---------------------------------------------------------------------------

def _ws(workspace_root) -> Path:
    return Path(str(workspace_root))


def _rel(ws: Path, p: Path) -> Optional[str]:
    try:
        return p.resolve().relative_to(ws.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def named_files(workspace_root, paths: Optional[Iterable[str]]) -> List[str]:
    """The files the caller named, as workspace paths; one outside the
    workspace, or missing, is left out."""
    ws = _ws(workspace_root)
    out: List[str] = []
    for raw in paths or ():
        if isinstance(raw, str) and raw.strip():
            p = ws / raw.strip()
            rel = _rel(ws, p)
            if rel and p.is_file() and rel not in out:
                out.append(rel)
    return out


def find_rulings_files(workspace_root, paths: Optional[Iterable[str]] = None) -> List[str]:
    """The rulings files, as paths relative to the workspace, brief first."""
    ws = _ws(workspace_root)
    out: List[str] = []
    if (ws / BRIEF_FILE).is_file():
        out.append(BRIEF_FILE)
    for root, dirs, files in os.walk(ws):
        here = Path(root).relative_to(ws).parts
        dirs[:] = sorted(d for d in dirs
                         if d not in SKIP_DIRS and len(here) < MAX_DEPTH
                         and not (here == ("_hq",) and d in SKIP_HQ_DIRS))
        for name in sorted(files):
            low = name.lower()
            if low.endswith(SUFFIXES) and any(w in low for w in FILE_WORDS):
                rel = _rel(ws, Path(root) / name)
                if rel and rel not in out:
                    out.append(rel)
    for rel in named_files(ws, paths):
        if rel not in out:
            out.append(rel)
    return out


def _clean(line: str) -> str:
    text = _BULLET.sub("", line, count=1)
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])", "", text)
    text = re.sub(r"^\s*>\s*", "", text)
    text = _DATE.sub("", text, count=1) if _DATE.match(text.strip()) else text
    text = re.sub(r"^\s*[:\-–—,.]+\s*", "", text.strip())
    # "(Sam ruling, 2026-03-02)" says who and when, not what.
    text = _WHO_NOTE.sub("", text)
    text = re.sub(r"\(\s*(?:on\s+)?20\d\d-\d\d-\d\d\s*\)", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return " ".join(text.split()).strip(" -–—:")


def _who_text(line: str) -> Optional[str]:
    for rx in _WHO:
        m = rx.search(line)
        if m:
            return m.group(1).strip()
    return None


def _line_date(line: str, heading_date: Optional[str]) -> Optional[str]:
    """A ruling's own date. The date in its who-note ("(Sam ruling,
    2026-08-18)") first, then a date the line opens with, then its
    heading's; a date only mentioned in the line ("replaces the 2026-07-30
    ruling") counts only when there is no heading date, and then the latest
    one does."""
    note = _WHO_NOTE.search(line)
    if note:
        m = _DATE.search(note.group(0))
        if m and _iso(m):
            return _iso(m)
    body = re.sub(r"\*\*|__|`", "", _BULLET.sub("", line, count=1)).strip()
    m = _DATE.match(body)
    if m and _iso(m):
        return _iso(m)
    if heading_date:
        return heading_date
    dates = [d for d in (_iso(x) for x in _DATE.finditer(line)) if d]
    return max(dates) if dates else None


def _logical_lines(text: str) -> List[tuple]:
    """(first line number, text, is_heading, heading level) for each logical
    line: a bullet or a paragraph with its wrapped continuation lines joined,
    a heading, or a table row. Fenced blocks and comments are left out."""
    out: List[list] = []
    fenced = False
    open_item = False
    for n, raw in enumerate((text or "").splitlines(), start=1):
        if _FENCE.match(raw):
            fenced = not fenced
            open_item = False
            continue
        if fenced or not raw.strip() or raw.lstrip().startswith("<!--"):
            open_item = False
            continue
        h = _HEADING.match(raw)
        if h:
            out.append([n, h.group(2), True, len(h.group(1))])
            open_item = False
            continue
        if raw.lstrip().startswith("|"):
            if not _TABLE_RULE.match(raw):
                out.append([n, raw, False, 0])
            open_item = False
            continue
        if open_item and not _BULLET.match(raw):
            out[-1][1] += " " + raw.strip()
            continue
        out.append([n, raw, False, 0])
        open_item = True
    return [tuple(x) for x in out]


def _table_ruling(row: str) -> Optional[str]:
    """A table row as one line: its date cell first, then its longest cell."""
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    dated = [c for c in cells if _DATE.fullmatch(c)]
    words = [c for c in cells if c and not _DATE.fullmatch(c)]
    if not dated or not words:
        return None
    return f"{dated[0]}: {max(words, key=len)}"


def parse_rulings(text: str, *, rel: str, rulings_file: bool) -> List[dict]:
    """Each dated ruling in one file's text: `{what, date, by, line, quote,
    says_replaces}`. A heading's date holds for every deeper heading under
    it; a wrapped bullet is one ruling. In a file that is not a rulings file,
    a line must also say it is a ruling, or sit under a heading that does,
    and a table row is not read."""
    out: List[dict] = []
    stack: List[tuple] = []  # (level, date, says rulings, heading text)
    first = True
    for n, raw, is_heading, level in _logical_lines(text):
        if is_heading:
            while stack and stack[-1][0] >= level:
                stack.pop()
            m = _DATE.search(raw)
            # The file's own title (its first heading, whatever sits above it:
            # a banner, front matter, a comment) counts for rule A only when
            # it names the file as rulings or decisions and is not a memo or
            # a draft: "Pricing rulings" and "Decisions" do; "Flour decision
            # memo, draft" does not, so a memo draft's dated notes stay off
            # the Review tab. Its date still holds (owner ruling, 2026-11-13,
            # cr1#1073).
            says = bool(_MARKER.search(raw)) and not (first and _NOT_A_LOG_TITLE.search(raw))
            stack.append((level, _iso(m) if m else None, says, raw))
            first = False
            continue
        if raw.lstrip().startswith("|"):
            if not rulings_file:
                continue
            raw = _table_ruling(raw) or ""
            if not raw:
                continue
        heading_date = next((d for _l, d, _r, _h in reversed(stack) if d), None)
        heading_rules = any(r for _l, _d, r, _h in stack)
        when = _line_date(raw, heading_date)
        if not when:
            continue
        if not rulings_file and not (heading_rules or _MARKER.search(raw)):
            continue
        what = _clean(raw)
        if len(what) < MIN_CHARS:
            continue
        out.append({"what": what[:WHAT_CHARS], "date": when, "by": _who_text(raw),
                    "file": rel, "line": n,
                    "quote": " ".join(raw.split())[:EVIDENCE_CHARS],
                    "says_replaces": bool(_REPLACE_WORDS.search(raw)),
                    "headings": [h for _l, _d, _r, h in reversed(stack)]})
    return out


_HEADING_SPLIT = re.compile(r"\s+[-–—]\s+|:\s+")


def _heading_names(heading: str) -> List[str]:
    """What a heading could name: its words without dates, parentheses or
    markup, and the part before its first separator ("Acme Co: hosting")."""
    text = _DATE.sub(" ", heading or "")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[*_`#]", "", text)
    text = " ".join(text.split()).strip(" -:–—")
    out = [text] if text else []
    head = _HEADING_SPLIT.split(text, maxsplit=1)[0].strip(" -:")
    if head and head not in out:
        out.append(head)
    return out


def _live_threads(ws: Path) -> List[dict]:
    """The threads on file that are not archived."""
    try:
        from entities_io import entities_collection, read_entities_json
        threads = entities_collection(read_entities_json(ws / "_hq" / "data" / "entities.json"), "projects")
    except Exception:  # noqa: BLE001: an unreadable registry stamps nothing
        return []
    return [t for t in threads if isinstance(t, dict) and isinstance(t.get("id"), str)
            and t["id"].strip() and t.get("status") != "archived"]


def _fold(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def thread_for_headings(workspace_root, headings: Iterable[str]) -> Optional[str]:
    """The thread on file that a ruling's headings name, innermost heading
    first, or None. Only a live thread's own name or one of its own aliases
    counts, said exactly: a folder or display name, a near match, an archived
    thread, or a name two threads share stamps nothing, and nothing is
    invented (cr1#1073)."""
    threads = _live_threads(_ws(workspace_root))
    if not threads:
        return None
    for heading in headings or ():
        for name in _heading_names(heading):
            want = _fold(name)
            ids = set()
            for t in threads:
                names = [t.get("canonical_name")] + [a for a in (t.get("aliases") or []) if isinstance(a, str)]
                if want in {_fold(n) for n in names if isinstance(n, str) and n.strip()}:
                    ids.add(t["id"])
            if len(ids) == 1:
                return ids.pop()
            if ids:
                return None
    return None


def _iso(m) -> Optional[str]:
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def read_rulings(workspace_root, paths: Optional[Iterable[str]] = None) -> List[dict]:
    """Every dated ruling in the rulings files, oldest first."""
    ws = _ws(workspace_root)
    named = set(named_files(ws, paths))
    out: List[dict] = []
    for rel in find_rulings_files(ws, paths):
        p = ws / rel
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # cr1#1073 item 2: only a file named by path keeps every dated line;
        # a file found by its name is read like the brief.
        is_rulings = rel in named
        out.extend(parse_rulings(text, rel=rel, rulings_file=is_rulings))
    out.sort(key=lambda r: (r["date"], r["file"], r["line"]))
    return out


# ---------------------------------------------------------------------------
# Same subject
# ---------------------------------------------------------------------------

def subject_words(text: str) -> set:
    from log_search import _tokens
    return {t for t in _tokens(text) if t not in _NOISE and not t.isdigit()}


def same_subject(newer: str, older: str, *, says_replaces: bool = False) -> bool:
    """Do two rulings speak to the same subject? Three shared subject words
    covering half the shorter one; two covering a third when the newer one
    says it replaces something."""
    a, b = subject_words(newer), subject_words(older)
    if not a or not b:
        return False
    shared = len(a & b)
    cover = shared / min(len(a), len(b))
    if shared >= 3 and cover >= 0.5:
        return True
    return says_replaces and shared >= 2 and cover >= 0.34


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split()).rstrip(".")


def fingerprint(ruling: dict) -> str:
    """The ruling's date and its exact words: two rulings that differ by a
    number or a "not" are two rulings."""
    h = hashlib.sha256(f"{ruling['date']}|{_norm(ruling['what'])}".encode("utf-8")).hexdigest()[:16]
    return f"{KIND}:{h}"


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def _events(ws: Path) -> list:
    from events_io import load_events_org_scoped
    try:
        return load_events_org_scoped(ws)[0]
    except Exception:  # noqa: BLE001: no log reads as nothing yet
        return []


def _on_file(events: list) -> tuple:
    """(fingerprints already decisions, normalised decision words)."""
    fps, words = set(), set()
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "decision":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if isinstance(d.get("import_fingerprint"), str):
            fps.add(d["import_fingerprint"])
        for k in ("summary", "decision", "title"):
            if isinstance(d.get(k), str) and d[k].strip():
                words.add(" ".join(d[k].lower().split()))
    return fps, words


def _open_decisions(ws: Path) -> List[dict]:
    """Decisions that stand, with their own date, for a replacement match."""
    import decision_list as dl
    rows, _ = dl.read_list(ws)
    return [r for r in rows if r.get("stands")]


def _match_older(r: dict, pulled: List[dict], standing: List[dict]) -> Optional[dict]:
    """The latest earlier ruling on the same subject: from this pull, or a
    decision that stands. None when there is none."""
    best = None
    for o in pulled:
        if o is r or o["date"] >= r["date"]:
            continue
        if same_subject(r["what"], o["what"], says_replaces=r["says_replaces"]):
            if best is None or o["date"] > best["date"]:
                best = {"date": o["date"], "what": o["what"], "fingerprint": fingerprint(o)}
    for d in standing:
        when = d.get("when") or ""
        if not when or when > r["date"] or d.get("import_fingerprint") == fingerprint(r):
            continue
        if when == r["date"] and not _same_line(d, r):
            continue  # same day: only the same line, reworded, is replaced
        if _same_line(d, r) or same_subject(r["what"], d.get("_text") or d.get("what") or "",
                                            says_replaces=r["says_replaces"]):
            if best is None or when > best["date"]:
                best = {"date": when, "what": d.get("what") or "",
                        "decision_id": d.get("id"), "decision_seq": d.get("seq")}
    return best


def _same_line(d: dict, r: dict) -> bool:
    """A decision pulled in from the very line this ruling sits on: the line
    was reworded since, so the new wording replaces it."""
    src = (d.get("source") or {}).get("imported_from") or {}
    return src.get("file") == r["file"] and src.get("line") == r["line"]


def _match_newer(r: dict, standing: List[dict]) -> Optional[dict]:
    """The earliest decision that stands, ruled after this one on the same
    subject: confirming this ruling writes it already replaced."""
    best = None
    for d in standing:
        when = d.get("when") or ""
        if when <= r["date"]:
            continue
        if same_subject(d.get("_text") or d.get("what") or "", r["what"]):
            if best is None or when < best["date"]:
                best = {"date": when, "what": d.get("what") or ""}
    return best


def _source_words(rel: str) -> str:
    """Where a ruling came from, in words: never a folder path."""
    if rel == BRIEF_FILE:
        return "your workspace brief"
    return " ".join(Path(rel).stem.replace("_", " ").replace("-", " ").split())


def _render_line(r: dict, older: Optional[dict], newer: Optional[dict] = None) -> str:
    from decision_list import human_date
    line = f"Ruled {human_date(r['date'])}: {r['what'].rstrip('.')}."
    if older:
        line += f" Replaces: {older['what'].rstrip('.')} ({human_date(older['date'])})."
    if newer:
        line += (f" A later decision already replaces it: {newer['what'].rstrip('.')} "
                 f"({human_date(newer['date'])}).")
    return line + " It counts as a decision once you confirm it."


def pull_past_rulings(workspace_root, *, apply: bool = False,
                      paths: Optional[Iterable[str]] = None,
                      today: Optional[str] = None) -> Dict[str, Any]:
    """Propose each past ruling in the rulings files as a decision to confirm.

    The reading is `preview_past_rulings`; this adds the one write, and only
    when `apply`. Same answer shape."""
    res = _compose_pull(workspace_root, apply=apply, paths=paths, today=today)
    if apply and res.get("applied"):
        from event_gate import append_event
        append_event(_ws(workspace_root) / "_hq" / "data" / "events.jsonl",
                     res["rows"], holder=DETECTOR)
    return res


def preview_past_rulings(workspace_root, *, paths: Optional[Iterable[str]] = None,
                         today: Optional[str] = None) -> Dict[str, Any]:
    """The pull as a dry run, with no write anywhere on its path, so a helper on
    the read door (`claude_md_diet:plan`) can ask what is still waiting
    without reaching `append_event` (ORCH1's seven-frame write pin)."""
    return _compose_pull(workspace_root, apply=False, paths=paths, today=today)


def _compose_pull(workspace_root, *, apply: bool,
                  paths: Optional[Iterable[str]],
                  today: Optional[str]) -> Dict[str, Any]:
    """What the pull proposes, read and composed, never written. `apply` only
    shapes the answer's words and `applied` flag; the caller writes.

    Dry run unless `apply`. A ruling already a decision, already open as a
    question, or declined within the cooldown is not proposed again, so the
    pull can be run again safely; one that lapsed unanswered comes back.
    Answers `{ok, files, found, proposed, skipped: {reason: n}, rows, line}`;
    `rows` are the proposals (written when `apply`)."""
    import brain_proposals as bp
    ws = _ws(workspace_root)
    if not (ws / "_hq" / "data").is_dir():
        return {"ok": False, "reason": "not_a_workspace",
                "line": "This folder is not a Command Room workspace; nothing was read."}
    files = find_rulings_files(ws, paths)
    rulings = read_rulings(ws, paths)
    events = _events(ws)
    on_file_fps, on_file_words = _on_file(events)
    standing = _open_decisions(ws)
    owner = _owner(ws)
    skipped: Dict[str, int] = {}
    specs: List[dict] = []
    seen: set = set()
    for r in rulings:
        fp = fingerprint(r)
        if fp in seen:
            skipped["repeated in the files"] = skipped.get("repeated in the files", 0) + 1
            continue
        seen.add(fp)
        if fp in on_file_fps or " ".join(r["what"].lower().split()) in on_file_words:
            skipped["already a decision"] = skipped.get("already a decision", 0) + 1
            continue
        older = _match_older(r, rulings, standing)
        newer = _match_newer(r, standing)
        extra = {"title": r["what"][:160], "ruling_text": r["what"], "ruling_date": r["date"],
                 "made_by": (_person_named(ws, r["by"]) or r["by"]
                             if r["by"] and r["by"].casefold() not in ("owner", "the owner")
                             else owner) or "", "source_file": r["file"], "source_line": r["line"],
                 "surface_hint": "staff-meeting"}
        thread_id = thread_for_headings(ws, r.get("headings") or ())
        if thread_id:
            extra["thread_id"] = thread_id
        if older:
            extra["replaces_what"] = older["what"]
            extra["replaces_date"] = older["date"]
            for k in ("fingerprint", "decision_id", "decision_seq"):
                if older.get(k) is not None:
                    extra["replaces_" + k] = older[k]
        specs.append({"kind": KIND, "fingerprint": fp, "tier": "confirm", "detector": DETECTOR,
                      "evidence": f"From {_source_words(r['file'])}, line {r['line']}: {r['quote']}",
                      "render_line": _render_line(r, older, newer),
                      "action_tuples": [{"action": "confirm proposal"},
                                        {"action": "dismiss proposal"},
                                        {"action": "snooze proposal 7d"}],
                      "extra": extra})
    # Newest first: the most recent rulings are the ones an ask needs.
    specs.sort(key=lambda s: (s["extra"]["ruling_date"], s["extra"]["source_line"]), reverse=True)
    prepared = bp.prepare_proposals(ws, specs)
    rows: List[dict] = []
    first = date.fromisoformat(today) if today else _today(ws)
    day = first
    slots = _open_slots(events)
    for spec, res in zip(specs, prepared):
        status = res.get("status")
        if status != "proposed":
            why = {"duplicate_open": "already waiting for your answer",
                   "suppressed_cooldown": "you said no to it recently"}.get(status, status or "error")
            skipped[why] = skipped.get(why, 0) + 1
            continue
        while slots.get(day.isoformat(), 0) >= PER_DAY:
            day += timedelta(days=1)
        slots[day.isoformat()] = slots.get(day.isoformat(), 0) + 1
        for row in res["rows"]:
            if row.get("type") == "brain_proposal":
                row["data"]["surface_on"] = day.isoformat()
            rows.append(row)
    n = len(rows)
    line = (f"{n} past ruling{'s' if n != 1 else ''} "
            f"{'put' if apply else 'would go'} under To confirm on the Hub's Commitments tab"
            + (f", up to {PER_DAY} a day." if n > PER_DAY else ".")
            if n else "No past ruling to add: each one found is already a decision or waiting for you.")
    if not rulings:
        line = "No dated ruling was found in the rulings files."
    return {"ok": True, "applied": bool(apply and rows), "files": files, "found": len(rulings),
            "proposed": n, "skipped": skipped, "rows": rows, "line": line}


def _open_slots(events: list) -> Dict[str, int]:
    """Past-ruling questions already stamped for a day, by day."""
    import brain_proposals as bp
    out: Dict[str, int] = {}
    for p in bp._open_brain_proposals(events):
        day = p.get("surface_on") or str(p.get("opened_at") or "")[:10]
        if p.get("kind") == KIND and day:
            out[day] = out.get(day, 0) + 1
    return out


def _today(ws: Path) -> date:
    try:
        from tz import localize_date
        from datetime import datetime, timezone
        return date.fromisoformat(localize_date(
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), workspace_path=str(ws)))
    except Exception:  # noqa: BLE001
        return date.today()


def _person_named(ws: Path, name: Optional[str]) -> Optional[str]:
    """The one person on file a name, alias or first name says, else None."""
    if not name:
        return None
    try:
        from entities_io import entities_collection, read_entities_json
        people = entities_collection(read_entities_json(ws / "_hq" / "data" / "entities.json"), "people")
    except Exception:  # noqa: BLE001
        return None
    want = name.strip().casefold()
    hits = set()
    for p in people:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        names = [str(p.get("canonical_name") or ""), str(p.get("display_name") or "")]
        names += [a for a in (p.get("aliases") or []) if isinstance(a, str)]
        folded = {n.strip().casefold() for n in names if n.strip()}
        if want in folded or want in {n.split()[0] for n in folded if n}:
            hits.add(str(p["id"]))
    return hits.pop() if len(hits) == 1 else None


def _owner(ws: Path) -> Optional[str]:
    try:
        from primary_user import resolve_primary_user
        return resolve_primary_user(ws)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# The yes
# ---------------------------------------------------------------------------

def _proposal(events: list, proposal_id: str) -> Optional[dict]:
    for ev in events:
        if (isinstance(ev, dict) and ev.get("type") == "brain_proposal"
                and isinstance(ev.get("data"), dict)
                and ev["data"].get("proposal_id") == proposal_id):
            return ev["data"]
    return None


def confirm_past_ruling(workspace_root, proposal_id: str, *, resolved_by: str,
                        source_skill: str = "apply-choices",
                        text: Optional[str] = None) -> Dict[str, Any]:
    """THE yes to a past ruling. Writes the `decision`, dated the day it was
    ruled, then a `decision_superseded` for each standing decision it
    replaces (the older ruling the proposal named, and any older ruling
    confirmed later that it names), then resolves the question. `text`, when
    given, is the owner's own wording and replaces the ruling's.

    Answers `{status, proposal_id, decision_id?, replaced?, line}` with status
    `applied`, `already_resolved`, `no_question` or `failed`. Never raises.
    A press after the question is answered writes nothing; a retry after a
    yes that did not finish writes only what is missing."""
    import brain_proposals as bp
    from decision_match import load_open_decisions
    from event_gate import append_event, new_decision_id
    ws = _ws(workspace_root)
    events_path = ws / "_hq" / "data" / "events.jsonl"
    landed = False
    try:
        events = _events(ws)
        data = _proposal(events, proposal_id)
        if data is None or data.get("kind") != KIND:
            return {"status": "no_question", "proposal_id": proposal_id,
                    "line": "That question is not on file; nothing was written."}
        open_ids = {p["id"] for p in bp.open_brain_proposals(ws, include_lapsed=True)}
        fp = data.get("fingerprint")
        mine = next((ev for ev in events if ev.get("type") == "decision"
                     and (ev.get("data") or {}).get("import_fingerprint") == fp), None)
        if proposal_id not in open_ids:
            return {"status": "already_resolved", "proposal_id": proposal_id,
                    "line": ("That one is already in your decisions; nothing was written."
                             if mine is not None else
                             "That one was already answered; nothing was written.")}
        own = " ".join(str(data.get("ruling_text") or data.get("title") or "").split())
        what = " ".join(str(text or "").split()) or own
        if mine is not None:
            # cr1#1073: a retry after a yes that did not finish writes no new
            # decision, so it answers with the wording already saved, never
            # with words typed on the retry.
            what = " ".join(str((mine.get("data") or {}).get("summary") or what).split())
        edited = what != own
        replaced: List[dict] = []
        replaced_by: List[str] = []
        if mine is None:
            made_by = data.get("made_by") or ""
            dec = {"type": "decision", "source_skill": SOURCE_SKILL,
                   "data": {"id": new_decision_id(), "summary": what,
                            "made_by": made_by, "decided_at": data.get("ruling_date"),
                            "rationale": "A past ruling, pulled in from the rulings files and confirmed.",
                            "source_ref": f"file:{data.get('source_file')}#L{data.get('source_line')}",
                            "import_fingerprint": fp,
                            "imported_from": {"file": data.get("source_file"),
                                              "line": data.get("source_line"),
                                              "ruling_date": data.get("ruling_date"),
                                              "proposal_id": proposal_id}}}
            if data.get("replaces_fingerprint"):
                dec["data"]["replaces_fingerprint"] = data["replaces_fingerprint"]
            # Checked again at the yes: a thread archived or gone since the
            # pull is not stamped.
            if (isinstance(data.get("thread_id"), str) and data["thread_id"].strip()
                    and any(th["id"] == data["thread_id"] for th in _live_threads(ws))):
                dec["primary_thread_id"] = data["thread_id"]
                dec["data"]["project_id"] = data["thread_id"]
            if isinstance(made_by, str) and made_by.startswith("person_"):
                dec["person_ids"] = [made_by]
            mine = append_event(events_path, [dec], holder=DETECTOR)[0]
        landed = True
        my_id = mine["data"]["id"]
        my_seq = mine.get("seq")
        my_date = data.get("ruling_date") or ""
        src = (data.get("source_file"), data.get("source_line"))
        # What it replaces, and what replaces it, among the decisions that
        # stand now. Only standing ones: a retry never closes a thing twice.
        from decision_list import _the_date
        from decision_match import _decision_id
        from event_time import event_time
        standing = load_open_decisions(events_path)
        closes: List[dict] = []
        later: List[tuple] = []
        mine_stands = any(_decision_id(ev) == my_id for ev in standing)
        for ev in standing:
            d = ev.get("data") or {}
            did = _decision_id(ev)
            if did == my_id:
                continue
            when = _the_date(d.get("decided_at"), ws) or _the_date(event_time(ev), ws)
            imp = d.get("imported_from") if isinstance(d.get("imported_from"), dict) else {}
            older = (did == data.get("replaces_decision_id")
                     or (data.get("replaces_fingerprint")
                         and d.get("import_fingerprint") == data.get("replaces_fingerprint"))
                     or ((imp.get("file"), imp.get("line")) == src and when <= my_date))
            if older:
                closes.append(_supersede(did, by_seq=my_seq, by_id=my_id,
                                         reason="A later ruling, confirmed from the rulings files, replaces it."))
                replaced.append({"id": did, "what": d.get("summary") or d.get("title") or d.get("decision")})
                continue
            text_of = " ".join(str(d.get(k) or "") for k in ("summary", "title", "decision", "rationale"))
            if mine_stands and (d.get("replaces_fingerprint") == fp
                                or (when > my_date and same_subject(text_of, what))):
                later.append((when, ev))
        if later:
            _when, ev = sorted(later, key=lambda w: w[0])[0]
            d = ev.get("data") or {}
            replaced_by.append(str(d.get("summary") or d.get("title") or d.get("decision") or "a later ruling"))
            closes.append(_supersede(my_id, by_seq=ev.get("seq"), by_id=_decision_id(ev),
                                     reason="A later ruling already replaces it."))
        if closes:
            append_event(events_path, closes, holder=DETECTOR)
        if proposal_id in open_ids:
            bp.resolve_proposal(ws, proposal_id, "edited" if edited else "applied",
                                resolved_by=resolved_by, source_skill=source_skill)
    except Exception as exc:  # noqa: BLE001: a yes never raises into a surface
        sys.stderr.write(f"[past-rulings] a yes did not land: {type(exc).__name__}: {exc}\n")
        if landed:
            return {"status": "failed", "proposal_id": proposal_id,
                    "line": "Your decision is saved, but linking it to the rulings it replaces did "
                            "not finish. Please confirm it again to finish."}
        return {"status": "failed", "proposal_id": proposal_id,
                "line": "That did not save, so the question is still open. Please confirm it again."}
    try:
        from render_decision_log import regenerate_if_changed
        regenerate_if_changed(ws)
    except Exception:  # noqa: BLE001: the view catches up on its next regeneration
        pass
    line = f"Added to your decisions: {what.rstrip('.')}."
    if replaced_by:
        line += " A later ruling already replaces it: " + "; ".join(
            r.rstrip(".") for r in replaced_by) + "."
    if replaced:
        line += " It replaces " + "; ".join(str(r["what"] or "an older ruling").rstrip(".")
                                           for r in replaced) + "."
    return {"status": "applied", "proposal_id": proposal_id, "decision_id": my_id,
            "replaced": [r["id"] for r in replaced], "line": line}


def _supersede(target_id: str, *, by_seq, by_id: str, reason: str) -> dict:
    data = {"decision_id": target_id, "superseded_by": by_id, "reason": reason}
    if isinstance(by_seq, int):
        data["superseded_by_decision_seq"] = by_seq
    return {"type": "decision_superseded", "source_skill": SOURCE_SKILL, "data": data}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pull the owner's past rulings in as decisions to confirm.")
    ap.add_argument("workspace")
    ap.add_argument("--apply", action="store_true", help="write the questions (default: a dry run)")
    ap.add_argument("--path", action="append", default=[], help="a rulings file, relative to the workspace")
    a = ap.parse_args(argv)
    out = pull_past_rulings(a.workspace, apply=a.apply, paths=a.path)
    out.pop("rows", None)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
