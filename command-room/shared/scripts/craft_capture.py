#!/usr/bin/env python3
"""Craft capture: gather, preview, held-out test and write for one craft file (SPEC CRAFT1 step 3, cr1#398).

WHY THIS EXISTS
---------------
Step 1 (`craft_contract`, cr1#396) fixed what a craft file is and where it
lives; step 2 (`craft_recall`, cr1#397) made the product read one on its own.
Until now the files were written by hand: the owner pointed the model at an
expert's real work and an interview, then typed the method out. This module is
the deterministic half of the `craft-capture` skill, which does the gathering
and the first draft and proves the draft before it is trusted. Extraction
itself (reading the sources and proposing rules) is the model's job in the
skill; everything here validates and shapes what the model proposes, so the
same proposals always give the same preview and the same file.

THE FLOW (SPEC CRAFT1 section 3.1)
----------------------------------
  1. `match_capture_ask`  which ask this is, the name and the scope it carries.
  2. `gather_sources`     every source the workspace holds about the subject,
                          listed by kind with its count and date range BEFORE
                          anything is read. One artifact is held out of the
                          reading list for the test. Mail is a source only
                          when the owner names it (owner ruling 2026-10-24).
  3. `build_preview`      the model's proposed rules, cite or drop: a rule
                          with no evidence pointer, or one whose pointers name
                          nothing gathered, never reaches the preview. A rule
                          drawn from the subject's own work starts at a higher
                          confidence than one drawn only from what they said.
                          At most five clarifying questions (owner ruling
                          2026-10-24); the rest go to Open questions.
  4. the held-out test    the model applies the confirmed rules to the
                          held-out artifact and asks one question.
  5. `write_craft`        only after at least one confirm: the file through
                          the write door, gated by
                          `craft_contract.gate_craft_text`, with the test's
                          verdict and date in its header; one `craft_captured`
                          event through the one append path; the CRAFT view
                          regenerated.

IDEMPOTENT
----------
Capturing the same subject and scope again opens the existing file and
proposes amendments; it never writes a second file. An amendment keeps the old
text, marked superseded, in the file's History (spec section 1: dated and
supersedable, never rewritten). A rule already in the file is named, not
proposed again.

WHAT IS NOT MACHINE-DETECTED (owner ruling of 2026-11-11 on cr1#396)
-------------------------------------------------------------------
The gate refuses figures, third-party names, emails, names the record cannot
place, and every tracked org and project name. A matter's facts written in
plain words are not detected: the preview is where a person catches them, and
`NOTICE` is the sentence the card says so in.

WHERE IT LANDS
--------------
A craft file is Markdown, and `deliverables.land` carries documents only
(`.docx`, `.pptx`, `.html`, `.xlsx`, `.pdf`), so the file lands through
`workspace_access.write`, the layer's text door, which is the call `land`
itself makes for text: the same fences, the same writer identity, the same
read-back. The event goes through `event_gate.append_event`.

PUBLIC API
----------
  NOTICE, TEST_QUESTION, MAX_QUESTIONS, SOURCE_KINDS
  match_capture_ask(text, *, workspace_root=None) -> dict          (read door)
  gather_sources(workspace_root, subject_id, scope, *, mail_named=False,
                 mail_items=None, transcript_hits=None, added=None,
                 entities=None) -> dict                            (read door)
  build_preview(workspace_root, gathered, proposals, *, questions=None,
                entities=None) -> dict                             (read door)
  write_craft(workspace_root, preview, decisions, *, answers=None,
              verdict=None, now_iso=None, source_skill="craft-capture",
              entities=None) -> dict                               (write door)
  last_capture(workspace_root, subject_id, scope) -> dict | None

The three read-door functions write nothing. `write_craft` is the one writer.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import craft_contract as cc  # noqa: E402

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: The kinds of source gathered, in the order they are listed and read: the
#: subject's own work first, because what people do outranks what they say
#: they do (spec section 3.1 step 3).
SOURCE_KINDS = ("documents", "transcripts", "notes", "facts", "mail")
SOURCE_LABELS = {
    "documents": "Documents, their own work",
    "transcripts": "Meetings they were in",
    "notes": "Session notes",
    "facts": "Things noted about them",
    "mail": "Mail you named",
}
#: Sources that are the subject's own work. Everything else is what was said.
OWN_WORK_KINDS = frozenset({"documents"})
#: Kinds an artifact may be held out from, in preference order.
HELD_OUT_KINDS = ("documents", "transcripts", "mail")
#: Starting confidence of a rule. Owner confirmation and the held-out test
#: move it later (SPEC CRAFT1 step 6, cr1#401), never here.
START_CONFIDENCE_OWN_WORK = 0.7
START_CONFIDENCE_SAID = 0.5
BASIS_OWN_WORK = "their own work"
BASIS_SAID = "what they said"
#: At most five clarifying questions per review card (owner ruling
#: 2026-10-24).
MAX_QUESTIONS = 5
TEST_QUESTION = "Would they have done it this way?"
NOTICE = (
    "Before you confirm, read each rule for a client matter's facts. The "
    "check on saving refuses figures, people's names and the names of the "
    "businesses and projects you track, but it does not catch a matter's "
    "facts written in plain words, so you are the check for those. A rule "
    "says how they work, never what happened in one matter: edit or dismiss "
    "any rule that tells a story.")
CITED_HELD_OUT_LINE = (
    "The artifact held out for the test was used to write the rules, so "
    "nothing unread is left to test them on; the file will be saved as never "
    "tested.")
EVENT_TYPE = "craft_captured"
WRITER = "craft-capture"

_DOC_SUFFIXES = (".md", ".txt", ".docx", ".doc", ".pdf", ".xlsx", ".xls",
                 ".csv", ".pptx", ".html", ".htm", ".rtf")
_NOTES_RE = re.compile(r"^SESSION_NOTES.*\.md$", re.IGNORECASE)
_TRANSCRIPT_FILE_RE = re.compile(r"_transcript\.md$", re.IGNORECASE)
_SKIP_DIRS = frozenset({cc.CRAFT_DIR, "_archive", "node_modules",
                        "__pycache__"})
_MAX_DEPTH = 4
_DATE_IN_NAME = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_KIND_PREFIX = {"documents": "document", "transcripts": "meeting",
                "notes": "note", "facts": "fact", "mail": "mail"}

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_ITEM_RE = re.compile(r"^ {0,1}(?:[-*]|\d+[.)])\s+(.*)$")
_ITEM_ID_RE = re.compile(r"^ {0,1}(?:[-*]|\d+[.)])\s+R(\d+)\.")

# ---------------------------------------------------------------------------
# The ask
# ---------------------------------------------------------------------------

_DOES_RE = re.compile(
    r"^\s*capture\s+how\s+(?P<name>.+?)\s+(?:does|do)\s+(?P<scope>.+?)"
    r"\s*[?.]?\s*$", re.IGNORECASE)
_WORKS_RE = re.compile(
    r"^\s*(?:capture|remember)\s+how\s+(?P<name>.+?)\s+works?\s*[?.]?\s*$",
    re.IGNORECASE)
_LOCK_RE = re.compile(r"^\s*lock\s+(?:this|that|it)\s+in\s*[?.]?\s*$",
                      re.IGNORECASE)
#: The owner's own phrasings (cr1#399 build brief): "capture how I decide",
#: "capture how I run a meeting". The verb is the kind of work; "run a
#: session" is run a meeting (reading on cr1#399, 2026-11-11). Nothing may
#: follow the verb, so "capture how I review this contract" is not a capture.
_OWNER_VERB_RE = re.compile(
    r"^\s*capture\s+how\s+(?P<name>i)\s+(?P<scope>decide|review|write|sell"
    r"|run\s+(?:a\s+|my\s+)?(?:meeting|session)s?)\s*[?.]?\s*$",
    re.IGNORECASE)
#: "capture my craft": the owner is the subject, the kind of work is asked.
_MY_CRAFT_RE = re.compile(r"^\s*capture\s+my\s+craft\s*[?.]?\s*$",
                          re.IGNORECASE)
_OWNER_WORDS = frozenset({"i", "me", "myself", "my own"})
#: Words for a fixed scope that are not its label.
_SCOPE_WORDS = {
    "reviews": "review", "reviewing": "review", "redline": "review",
    "redlines": "review", "redlining": "review",
    "decides": "decide", "deciding": "decide", "decision": "decide",
    "decisions": "decide",
    "writes": "write", "writing": "write",
    "meeting": "run-a-meeting", "meetings": "run-a-meeting",
    "runs a meeting": "run-a-meeting", "running a meeting": "run-a-meeting",
    "a meeting": "run-a-meeting",
    "session": "run-a-meeting", "sessions": "run-a-meeting",
    "a session": "run-a-meeting", "run a session": "run-a-meeting",
    "runs a session": "run-a-meeting",
    "running a session": "run-a-meeting",
    "sells": "sell", "selling": "sell", "sales": "sell",
}
_LEADING = re.compile(r"^(?:the|their|his|her|my|our|a|an)\s+", re.I)


def _scope_for(text, workspace_root) -> Optional[str]:
    plain = " ".join(str(text or "").lower().strip(" ?.!,;:").split())
    while True:
        shorter = _LEADING.sub("", plain)
        if shorter == plain:
            break
        plain = shorter
    if not plain:
        return None
    rows = cc.known_scopes(workspace_root)
    for row in rows:
        if plain in (row["slug"], row["label"]):
            return row["slug"]
    if plain in _SCOPE_WORDS:
        return _SCOPE_WORDS[plain]
    last = plain.split()[-1]
    for row in rows:
        if last in (row["slug"], row["label"]):
            return row["slug"]
    return _SCOPE_WORDS.get(last)


def _resolve_names(workspace_root, name: str) -> list:
    """Person, business and project ids the name resolves to, through the
    canonical resolver, in its order."""
    try:
        from entity_resolve import resolve_all
        found = resolve_all(workspace_root, name)
    except Exception:  # noqa: BLE001 - an unreadable record resolves nothing
        return []
    out = []
    for hit in found:
        rid = (hit.record or {}).get("id")
        if hit.entity_type in ("person", "org", "project") and rid \
                and rid not in out:
            out.append(rid)
    return out


def match_capture_ask(text, *, workspace_root=None) -> dict:
    """Is this a capture ask, and what does it carry?

    {"verb": "capture"|None, "name", "scope", "scope_text", "subject_ids",
    "infer_subject"}. `capture how [name] does [scope]`, `capture how [name]
    works`, `remember how [name] works`, the owner's own `capture how I
    decide` (review, write, run a meeting, run a session), `capture my
    craft` and `lock this in` are captures;
    `lock this in` names no one, so `infer_subject` is True and the subject
    comes from the session. `scope` is a slug on the workspace's list, or
    None with the words in `scope_text` for the skill to ask about. With
    `workspace_root` the name is resolved through the canonical resolver
    ("I" and "me" are the owner). Anything else, including `remember [fact]
    about [name]`, `research [name]`, `who is [name]` and people-crm's
    `how does [name] work`, is None. Never raises."""
    none = {"verb": None, "name": None, "scope": None, "scope_text": None,
            "subject_ids": [], "infer_subject": False}
    if not isinstance(text, str):
        return none
    if _LOCK_RE.match(text):
        return dict(none, verb="capture", infer_subject=True)
    if _MY_CRAFT_RE.match(text):
        text = "capture how I work"
    m = _DOES_RE.match(text) or _OWNER_VERB_RE.match(text)
    scope_text = None
    if m:
        scope_text = " ".join(m.group("scope").split())
    else:
        m = _WORKS_RE.match(text)
    if m is None:
        return none
    name = " ".join(m.group("name").strip().split())
    if not name or name.lower() in ("this", "that", "it", "things", "work"):
        return none
    out = dict(none, verb="capture", name=name, scope_text=scope_text)
    if scope_text:
        out["scope"] = _scope_for(scope_text, workspace_root)
    if workspace_root is not None:
        if name.lower() in _OWNER_WORDS:
            owner = cc.owner_person_id(cc.load_entities(workspace_root))
            out["subject_ids"] = [owner] if owner else []
        else:
            out["subject_ids"] = _resolve_names(workspace_root, name)
    return out


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _records(ents: dict, name: str) -> list:
    try:
        from entities_io import entities_collection, unwrap_entities
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(ents, dict) or not isinstance(
            unwrap_entities(ents).get(name), list):
        return []
    return [r for r in entities_collection(ents, name) if isinstance(r, dict)]


class _Index:
    def __init__(self, ents: dict):
        self.ents = ents
        self.owner = cc.owner_person_id(ents)
        self.own_orgs = cc.own_company_org_ids(ents)
        self.by_id: dict = {}
        for name in ("people", "orgs", "threads"):
            for rec in _records(ents, name):
                rid = rec.get("id")
                if isinstance(rid, str) and rid and rid not in self.by_id:
                    self.by_id[rid] = dict(rec, _kind=name)
        self.threads = [r for r in self.by_id.values()
                        if r["_kind"] == "threads"]

    def kind(self, sid) -> Optional[str]:
        if sid and sid == self.owner:
            return "owner"
        rec = self.by_id.get(sid)
        if rec is None:
            return None
        if rec["_kind"] == "orgs" and sid in self.own_orgs:
            return "firm"
        return {"people": "person", "orgs": "org",
                "threads": "project"}[rec["_kind"]]

    def name(self, sid) -> str:
        if sid and sid == self.owner:
            return "you"
        rec = self.by_id.get(sid) or {}
        name = (rec.get("canonical_name") or rec.get("display_name")
                or rec.get("legal_name"))
        return str(name) if name else "someone not on file"

    def org_threads(self, org_id) -> list:
        return [t for t in self.threads
                if org_id in {t.get("org_id"), t.get("affiliation_id")}]


def _folder(thread: dict) -> Optional[str]:
    folder = str(thread.get("folder_name") or "").strip().replace("\\", "/")
    folder = folder.strip("/")
    if not folder or ".." in folder.split("/"):
        return None
    return folder


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _date_of(path: Path) -> Optional[str]:
    m = _DATE_IN_NAME.search(path.name)
    if m:
        try:
            _dt.date.fromisoformat(m.group(1))
            return m.group(1)
        except ValueError:
            pass
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime).date() \
            .isoformat()
    except (OSError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Gather
# ---------------------------------------------------------------------------

def _walk_folder(root: Path, folder: Path) -> tuple[list, list]:
    """(documents, session notes) under one subject folder, as paths. Skips
    craft folders, the archive, hidden folders and anything not safe to
    read (`craft_contract.is_safe_path`)."""
    docs, notes = [], []
    if not folder.is_dir() or not cc.is_safe_path(root, folder):
        return docs, notes
    base_depth = len(folder.parts)
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        here = Path(dirpath)
        depth = len(here.parts) - base_depth
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in _SKIP_DIRS
            and not (here / d).is_symlink() and depth + 1 <= _MAX_DEPTH)
        for name in sorted(filenames):
            p = here / name
            if name.startswith(".") or not p.is_file() \
                    or not cc.is_safe_path(root, p):
                continue
            if _NOTES_RE.match(name):
                notes.append(p)
            elif _TRANSCRIPT_FILE_RE.search(name):
                continue
            elif p.suffix.lower() in _DOC_SUFFIXES:
                docs.append(p)
    return docs, notes


def _item(kind, ident, date, label, read) -> dict:
    return {"ref": f"{_KIND_PREFIX[kind]}:{ident}", "kind": kind,
            "date": date, "label": label, "own_work": kind in OWN_WORK_KINDS,
            "read": read}


def _load_events(root: Path) -> list:
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(root)
    except Exception:  # noqa: BLE001 - a reader never fails on a bad ledger
        return []
    return [e for e in events if isinstance(e, dict)]


def _ev_date(ev: dict) -> Optional[str]:
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for value in (data.get("date"), ev.get("ts")):
        if isinstance(value, str) and len(value) >= 10:
            try:
                _dt.date.fromisoformat(value[:10])
                return value[:10]
            except ValueError:
                continue
    return None


def _meetings_for(events: list, idx: _Index, sid: str, kind: str) -> list:
    from event_refs import meeting_person_ids
    if kind == "project":
        threads = {sid}
    elif kind == "org":
        threads = {t["id"] for t in idx.org_threads(sid)}
    else:
        threads = set()
    out = []
    for ev in events:
        if ev.get("type") != "meeting":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if kind in ("owner", "firm"):
            hit = True
        elif kind == "person":
            hit = sid in meeting_person_ids(ev)
        else:
            orgs = set(ev.get("org_ids") or []) | set(data.get("org_ids")
                                                      or [])
            hit = (ev.get("primary_thread_id") or data.get(
                "primary_thread_id")) in threads or (kind == "org"
                                                     and sid in orgs)
        if hit and ev.get("seq") is not None:
            out.append(ev)
    return out


def _fenced_added(root: Path, raw) -> Optional[Path]:
    """A file the owner added, as a path inside the workspace, or None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    rel = raw.strip().replace("\\", "/")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) \
            or ".." in rel.split("/"):
        return None
    p = root / rel
    try:
        p.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not p.is_file() or not cc.is_safe_path(root, p):
        return None
    return p


def _pick_held_out(items: list, used: set) -> Optional[dict]:
    """One gathered artifact the extraction did not use: from the first kind
    in `HELD_OUT_KINDS` that has at least two items (so the extraction keeps
    one of its kind) and an unused one, the newest unused one."""
    for kind in HELD_OUT_KINDS:
        of_kind = [it for it in items if it["kind"] == kind]
        if len(of_kind) < 2:
            continue
        free = [it for it in of_kind if it["ref"] not in used]
        if free:
            free.sort(key=lambda it: (it.get("date") or "", it["ref"]),
                      reverse=True)
            return dict(free[0])
    return None


def _source_summary(items: list) -> list:
    out = []
    for kind in SOURCE_KINDS:
        of_kind = [it for it in items if it["kind"] == kind]
        if not of_kind:
            continue
        dates = sorted(d for d in (it.get("date") for it in of_kind) if d)
        out.append({"kind": kind, "label": SOURCE_LABELS[kind],
                    "count": len(of_kind),
                    "first": dates[0] if dates else None,
                    "last": dates[-1] if dates else None})
    return out


def _source_line(row: dict) -> str:
    n = row["count"]
    if row["first"] and row["last"] and row["first"] != row["last"]:
        when = f"from {row['first']} to {row['last']}"
    elif row["first"]:
        when = f"on {row['first']}"
    else:
        when = "dates not known"
    return f"{row['label']}: {n}, {when}"


def _held_line(held: Optional[dict]) -> str:
    if held is None:
        return ("Nothing could be held out for the test, so the file will be "
                "saved as never tested.")
    kind = _KIND_PREFIX.get(held["kind"], held["kind"])
    when = f", {held['date']}" if held.get("date") else ""
    return (f"Held out for the test afterwards, and not read before it: "
            f"{held['label']} ({kind}{when}).")


def _refuse(reason: str, line: str, **extra) -> dict:
    return dict({"ok": False, "reason": reason, "line": line}, **extra)


def _existing_view(root: Path, target: Path, sid: str, ents: dict,
                   scope: str) -> dict:
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {"error": "the file could not be read"}
    res = cc.validate_craft(text, workspace_root=root, entities=ents,
                            path=target)
    if res["errors"]:
        return {"error": res["errors"][0]}
    if res["header"].get("subject") != sid:
        return {"error": "it names someone else"}
    sections = res.get("sections") or {}
    last = last_capture(root, sid, scope)
    return {
        "rel": _rel(root, target),
        "status": res["header"].get("status"),
        "consent": res["header"].get("consent"),
        "rule_count": len(res["rules"]),
        "rules": [{"number": r["id"], "text": r["body"],
                   "section": r["section"]} for r in res["rules"]],
        "open_questions": [it["body"] for it in
                           sections.get("Open questions", [])],
        "last_captured": last["date"] if last else None,
    }


def gather_sources(workspace_root, subject_id, scope, *, mail_named=False,
                   mail_items=None, transcript_hits=None, added=None,
                   entities=None) -> dict:
    """Every source the workspace holds about one subject, listed, not read.

    Sources by subject: documents in the subject's own folder (their work);
    the meetings they were in, from the record, plus any `transcript_hits`
    the transcript search found ({"ref", "date", "title"}); session notes in
    their project folders; their person facts; mail only when `mail_named`
    is True (owner ruling 2026-10-24), from `mail_items` ({"ref", "date",
    "title"}); and files the owner added (`added`: {"path"} inside the
    workspace, or {"ref", "date", "label"} for pasted text), which count as
    their own work.

    Returns {"ok": True, "subject_id", "subject_name", "scope",
    "scope_label", "target", "action": "create"|"amend", "existing",
    "consent", "sources": [{"kind", "label", "count", "first", "last"}],
    "lines", "items", "read_items", "held_out", "held_out_line",
    "mail_left_out", "added_refused"}. `items` carry a `ref` (the pointer a
    rule cites), a `date`, a `label` and `read` (what to open), never any
    content. On a scope not on the list, a subject not on file, a subject
    with no single folder, or an existing file that does not read as a craft
    file, {"ok": False, "reason", "line", ...}. Writes nothing."""
    root = Path(workspace_root)
    ents = entities if isinstance(entities, dict) else cc.load_entities(root)
    idx = _Index(ents)
    scopes = cc.known_scopes(root)
    if scope not in {s["slug"] for s in scopes}:
        labels = ", ".join(s["label"] for s in scopes)
        return _refuse("scope_unknown",
                       f"That kind of work is not on your list ({labels}). "
                       "Pick one, or say to add it as a new one.",
                       scopes=scopes)
    kind = idx.kind(subject_id)
    if kind is None:
        return _refuse("subject_unknown",
                       "That name is not on file, so there is nowhere to "
                       "keep how they work. Add them first.")
    name = idx.name(subject_id)
    try:
        target = cc.craft_path(root, subject_id, scope, entities=ents)
    except ValueError:
        target = None
    if target is None:
        if kind == "org":
            projects = sorted(t["id"] for t in idx.org_threads(subject_id)
                              if _folder(t))
            if len(projects) > 1:
                return _refuse(
                    "no_single_folder",
                    f"{name} has more than one project folder, so there is "
                    "no single place to keep this. Capture it for one of "
                    "its projects instead.", projects=projects)
        return _refuse("no_folder",
                       f"{name} has no folder yet, so there is nowhere to keep "
                       "this. Create the project first.")

    existing = None
    if target.exists():
        if not cc.is_safe_path(root, target) or not target.is_file():
            return _refuse("existing_unreadable",
                           "There is already something at that place that "
                           "is not a craft file, so nothing will be written "
                           "over it.", rel=_rel(root, target))
        existing = _existing_view(root, target, subject_id, ents, scope)
        if "error" in existing:
            return _refuse("existing_unreadable",
                           "The file already kept for this does not read as "
                           f"a craft file ({existing['error']}), so nothing "
                           "will be written over it. Fix it by hand first.",
                           rel=_rel(root, target))

    items: list = []
    seen: set = set()

    def add(it):
        if it["ref"] not in seen:
            seen.add(it["ref"])
            items.append(it)

    doc_folder = None if kind in ("owner", "firm") else target.parent.parent
    note_folders = []
    if doc_folder is not None:
        docs, notes = _walk_folder(root, doc_folder)
        for p in docs:
            rel = _rel(root, p)
            add(_item("documents", rel, _date_of(p), p.name, rel))
        note_folders.append(doc_folder)
        for p in notes:
            rel = _rel(root, p)
            add(_item("notes", rel, _date_of(p), p.name, rel))
    if kind in ("person", "owner", "firm"):
        for t in idx.threads:
            folder = _folder(t)
            if not folder:
                continue
            if kind == "person" and t.get("key_contact_id") != subject_id:
                continue
            _docs, notes = _walk_folder(root, root / folder)
            for p in notes:
                rel = _rel(root, p)
                add(_item("notes", rel, _date_of(p), p.name, rel))

    events = _load_events(root)
    for ev in _meetings_for(events, idx, subject_id, kind):
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        label = str(data.get("title") or data.get("summary")
                    or "a meeting")[:80]
        add(_item("transcripts", ev["seq"], _ev_date(ev), label,
                  {"seq": ev["seq"], "source_ref": data.get("source_ref")}))
    known_sources = {it["read"].get("source_ref") for it in items
                     if isinstance(it.get("read"), dict)}
    for hit in transcript_hits or []:
        if not isinstance(hit, dict) or not hit.get("ref"):
            continue
        ref = str(hit["ref"])
        if ref in known_sources:
            continue
        ident = ref.split(":", 1)[1] if ref.startswith("meeting:") else ref
        add(_item("transcripts", ident, _iso_date(hit.get("date")),
                  str(hit.get("title") or "a meeting")[:80],
                  {"source_ref": ref}))
    if kind == "person":
        for ev in events:
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if ev.get("type") == "person_fact_observed" \
                    and data.get("person_id") == subject_id \
                    and ev.get("seq") is not None:
                add(_item("facts", ev["seq"], _ev_date(ev), "a noted fact",
                          {"seq": ev["seq"]}))

    mail_left_out = False
    if mail_named:
        for m in mail_items or []:
            if isinstance(m, dict) and m.get("ref"):
                ref = str(m["ref"])
                ident = ref.split(":", 1)[1] if ref.startswith("mail:") \
                    else ref
                add(_item("mail", ident, _iso_date(m.get("date")),
                          str(m.get("title") or "a mail thread")[:80],
                          {"ref": ref}))
    elif mail_items:
        mail_left_out = True

    added_refused = []
    for n, a in enumerate(added or [], start=1):
        if not isinstance(a, dict):
            continue
        if a.get("path") is not None:
            p = _fenced_added(root, a.get("path"))
            if p is None:
                added_refused.append(str(a.get("path")))
                continue
            rel = _rel(root, p)
            add(_item("documents", rel, _date_of(p), p.name, rel))
        elif a.get("ref"):
            add(dict(_item("documents", f"pasted-{n}",
                           _iso_date(a.get("date")),
                           str(a.get("label") or "pasted text")[:80],
                           {"pasted": str(a["ref"])}),
                     ref=f"pasted:{a['ref']}"))

    order = {k: i for i, k in enumerate(SOURCE_KINDS)}
    items.sort(key=lambda it: (order[it["kind"]],
                               _neg_date(it.get("date")), it["ref"]))
    held = _pick_held_out(items, set())
    sources = _source_summary(items)
    consent = cc.consent_flag({"subject": subject_id,
                               "consent": (existing or {}).get("consent")},
                              ents)
    return {
        "ok": True,
        "subject_id": subject_id,
        "subject_kind": kind,
        "subject_name": name,
        "scope": scope,
        "scope_label": cc.scope_label(scope, root),
        "target": _rel(root, target),
        "action": "amend" if existing else "create",
        "existing": existing,
        "consent": consent,
        "sources": sources,
        "lines": [_source_line(r) for r in sources],
        "items": items,
        "read_items": [it for it in items
                       if held is None or it["ref"] != held["ref"]],
        "held_out": held,
        "held_out_line": _held_line(held),
        "mail_left_out": mail_left_out,
        "added_refused": added_refused,
        # what the gather was handed beyond the workspace, so the writer can
        # gather again and check every pointer (review of b7f4ca04, 3)
        "inputs": {
            "mail_named": bool(mail_named),
            "mail_items": [m for m in mail_items or [] if isinstance(m, dict)],
            "transcript_hits": [h for h in transcript_hits or []
                                if isinstance(h, dict)],
            "added": [a for a in added or [] if isinstance(a, dict)],
        },
    }


def _iso_date(value) -> Optional[str]:
    if isinstance(value, str) and len(value) >= 10:
        try:
            _dt.date.fromisoformat(value[:10])
            return value[:10]
        except ValueError:
            return None
    return None


def _neg_date(value) -> str:
    """Sort key putting the newest date first and unknown dates last."""
    if not value:
        return "~"
    return "".join(chr(ord("9") - int(c) + ord("0")) if c.isdigit() else c
                   for c in value)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def _plain(text) -> str:
    return " ".join(str(text or "").split())


def _norm(text) -> str:
    return _plain(text).lower().rstrip(" .")


def _findings(text: str, section: str, ents: dict) -> list:
    parsed = {"items": [{"section": section, "body": text, "id": "R1",
                         "index": 1}]}
    try:
        return [{"kind": f["kind"], "match": f["match"]}
                for f in cc.privacy_findings(parsed, entities=ents)]
    except Exception:  # noqa: BLE001 - the gate on saving still runs
        return []


def _fmt_conf(value) -> str:
    return ("%.2f" % float(value)).rstrip("0").rstrip(".") or "0"


def build_preview(workspace_root, gathered, proposals, *, questions=None,
                  entities=None) -> dict:
    """Shape the model's proposed rules into the review card.

    `proposals`: [{"text", "section": "Rules" | "Order of work" | "What they
    never do" (default Rules), "evidence": [ref, ...], "amends": "R<n>"
    (optional)}], each `ref` one of `gathered["items"]`. `questions`: the
    clarifying questions the material left open, as strings.

    Cite or drop: a proposal with no evidence pointer, or whose pointers name
    nothing gathered, is dropped and only counted. A proposal whose words are
    already a rule in the file is named in `already_on_file`. Each kept rule
    gets a key (P1, P2), its evidence, a starting confidence (higher when any
    pointer is the subject's own work), its basis, and `blocked`: what the
    gate on saving would refuse in it. At most `MAX_QUESTIONS` questions are
    asked; the rest go straight to Open questions. The held-out artifact is
    re-checked against the evidence used: when the extraction cited it,
    nothing is held out (everything else was read), and the file is saved as
    never tested. `card_lines` are the card, `notice` among them, to be said as
    given. Writes nothing."""
    root = Path(workspace_root)
    if not isinstance(gathered, dict) or not gathered.get("ok"):
        return _refuse("not_gathered",
                       "Gather the sources first; nothing was previewed.")
    ents = entities if isinstance(entities, dict) else cc.load_entities(root)
    items = [it for it in gathered.get("items") or []
             if isinstance(it, dict) and it.get("ref")]
    by_ref = {it["ref"]: it for it in items}
    existing = gathered.get("existing") or None
    old_rules = {r["number"]: r for r in (existing or {}).get("rules") or []
                 if isinstance(r, dict) and r.get("number")}
    old_norm = {_norm(r["text"]): n for n, r in old_rules.items()}

    rules, dropped, already = [], [], []
    seen_text: set = set()
    for raw in proposals or []:
        if not isinstance(raw, dict):
            continue
        text = _plain(raw.get("text"))
        if not text:
            dropped.append({"reason": "it said nothing"})
            continue
        section = raw.get("section") if raw.get("section") in \
            cc.EVIDENCE_SECTIONS else "Rules"
        refs = [r for r in (raw.get("evidence") or [])
                if isinstance(r, str) and r.strip()]
        known = []
        for r in refs:
            if r in by_ref and r not in known:
                known.append(r)
        if not refs:
            dropped.append({"reason": "it had no evidence to point at"})
            continue
        if not known:
            dropped.append({"reason": "its evidence points at nothing that "
                                      "was gathered"})
            continue
        if _norm(text) in seen_text:
            continue
        seen_text.add(_norm(text))
        amends = raw.get("amends") if raw.get("amends") in old_rules else None
        if amends is None and _norm(text) in old_norm:
            already.append({"number": old_norm[_norm(text)], "text": text})
            continue
        own = any(by_ref[r]["own_work"] for r in known)
        rules.append({
            "key": f"P{len(rules) + 1}",
            "text": text,
            "section": section,
            "evidence": [{"ref": r, "date": by_ref[r].get("date"),
                          "kind": by_ref[r]["kind"],
                          "label": by_ref[r].get("label")} for r in known],
            "confidence": (START_CONFIDENCE_OWN_WORK if own
                           else START_CONFIDENCE_SAID),
            "basis": BASIS_OWN_WORK if own else BASIS_SAID,
            "amends": amends,
            "old_text": old_rules[amends]["text"] if amends else None,
            "blocked": _findings(text, section, ents),
        })

    asked, unasked, qseen = [], [], set()
    new_q = [_plain(q) for q in (questions or []) if _plain(q)]
    old_q = [_plain(q) for q in (existing or {}).get("open_questions") or []]
    for text, is_old in [(q, False) for q in new_q] + [(q, True)
                                                        for q in old_q]:
        if _norm(text) in qseen:
            continue
        qseen.add(_norm(text))
        row = {"text": text, "existing": is_old,
               "blocked": _findings(text, "Open questions", ents)}
        if len(asked) < MAX_QUESTIONS:
            asked.append(dict(row, key=f"Q{len(asked) + 1}"))
        elif not is_old:
            unasked.append(row)

    used = {e["ref"] for r in rules for e in r["evidence"]}
    held = gathered.get("held_out")
    held_line = None
    if not isinstance(held, dict) or held.get("ref") not in by_ref:
        held = None
    elif held["ref"] in used:
        # Every other gathered item was on the reading list, so nothing is
        # left that the extraction did not read: no re-pick, never a test on
        # read material (review of b7f4ca04, finding 2).
        held = None
        held_line = CITED_HELD_OUT_LINE

    name = gathered.get("subject_name") or "they"
    label = gathered.get("scope_label") or gathered.get("scope")
    preview = {
        "ok": True,
        "subject_id": gathered.get("subject_id"),
        "subject_name": name,
        "scope": gathered.get("scope"),
        "scope_label": label,
        "target": gathered.get("target"),
        "action": gathered.get("action"),
        "consent": gathered.get("consent"),
        "sources": gathered.get("sources") or [],
        "gather_inputs": gathered.get("inputs") or {},
        "rules": rules,
        "already_on_file": already,
        "dropped": dropped,
        "questions": asked,
        "open_questions": unasked,
        "existing_open_questions": old_q,
        "held_out": held,
        "held_out_line": held_line or _held_line(held),
        "test_question": TEST_QUESTION,
        "notice": NOTICE,
    }
    preview["card_lines"] = _card_lines(preview)
    return preview


def _title_phrase(name: str, label: str) -> str:
    return f"How you do {label}" if name == "you" \
        else f"How {name} does {label}"


def _card_lines(pv: dict) -> list:
    name, label = pv["subject_name"], pv["scope_label"]
    head = _title_phrase(name, label)
    lines = [f"{head}: {'changes to the file' if pv['action'] == 'amend' else 'a first draft'}, "
             f"{len(pv['rules'])} rule(s) to check."]
    for r in pv["rules"]:
        where = {"Rules": "", "Order of work": " (a step in their order of "
                 "work)", "What they never do": " (something they never do)"
                 }[r["section"]]
        lines.append(f"{r['key']}. {r['text']}{where}")
        ev = "; ".join(f"{e['label'] or e['ref']}"
                       + (f", {e['date']}" if e.get("date") else "")
                       for e in r["evidence"])
        lines.append(f"    From {r['basis']}, confidence "
                     f"{_fmt_conf(r['confidence'])}. Evidence: {ev}.")
        if r.get("amends"):
            lines.append(f"    Replaces {r['amends']}, which says: "
                         f"{r['old_text']}")
        if r.get("blocked"):
            what = ", ".join(sorted({f["kind"] for f in r["blocked"]}))
            lines.append(f"    Needs an edit before it can be saved: it "
                         f"carries a {what}.")
    if pv["already_on_file"]:
        nums = ", ".join(a["number"] for a in pv["already_on_file"])
        lines.append(f"Already in the file, so not proposed again: {nums}.")
    if pv["dropped"]:
        lines.append(f"Left out: {len(pv['dropped'])} rule(s) with no "
                     "evidence to point at.")
    if pv["questions"]:
        lines.append("Questions (answer any, skip any; an unanswered one "
                     "stays in the file as an open question):")
        for q in pv["questions"]:
            lines.append(f"{q['key']}. {q['text']}")
    if pv["open_questions"]:
        lines.append(f"{len(pv['open_questions'])} more question(s) go "
                     "straight to the file's open questions.")
    lines.append(pv["notice"])
    if pv.get("consent"):
        lines.append(f"{name} is outside your company, so the file carries "
                     f"consent {pv['consent']}. It is a note, not a block.")
    lines.append("Say confirm, edit or dismiss for each rule, for example: "
                 "confirm P1 and P3, dismiss P2, edit P4 to your own words.")
    return lines


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _today(now_iso) -> str:
    if now_iso:
        try:
            return _dt.datetime.fromisoformat(
                str(now_iso).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return _dt.date.today().isoformat()


def _events_file(root: Path) -> Path:
    try:
        from events_io import active_path
        return Path(active_path(root))
    except Exception:  # noqa: BLE001 - a stripped install still answers
        return root / cc.HQ_DIR / "data" / "events.jsonl"


def _evidence_text(rule: dict, today: str) -> str:
    ptr = "; ".join(e["ref"] + (f", {e['date']}" if e.get("date") else "")
                    for e in rule["evidence"])
    return (f"Evidence: {ptr}. Confidence {_fmt_conf(rule['confidence'])}. "
            f"Confirmed by owner {today}.")


def _item_lines(rule: dict, number, today: str) -> list:
    if rule["section"] == "Order of work":
        return [f"{number}. {rule['text']}",
                f"   {_evidence_text(rule, today)}"]
    return [f"- {number}. {rule['text']}", f"  {_evidence_text(rule, today)}"]


def _header(subject, scope, status, confidence, consent, today, tested,
            verdict) -> tuple[str, str]:
    one = (f"<!-- craft: subject={subject} scope={scope} status={status} "
           f"confidence={_fmt_conf(confidence)}"
           + (f" consent={consent}" if consent else "") + " -->")
    two = f"<!-- last-updated: {today}  last-writer: {WRITER}  tested: "
    two += (f"{tested} verdict={verdict}" if tested else "never") + " -->"
    return one, two


def _split(text: str) -> tuple[list, list]:
    """(the lines before the first section, [[name, header, body], ...])."""
    pre, sections = [], []
    for line in text.split("\n"):
        m = _SECTION_RE.match(line)
        canon = None
        if m:
            canon = next((s for s in cc.SECTIONS
                          if s.lower() == m.group(1).strip().lower()), None)
        if canon:
            sections.append([canon, line, []])
        elif sections:
            sections[-1][2].append(line)
        else:
            pre.append(line)
    return pre, sections


def _items_of(body: list) -> tuple[list, list]:
    lead, items = [], []
    for line in body:
        if _ITEM_RE.match(line) and not line.startswith("  "):
            items.append([line])
        elif items:
            items[-1].append(line)
        else:
            lead.append(line)
    return lead, items


def _item_text(lines: list) -> str:
    first = _ITEM_RE.match(lines[0])
    body = [first.group(1) if first else lines[0]]
    for line in lines[1:]:
        if line.strip().startswith("Evidence:"):
            break
        body.append(line.strip())
    return _plain(" ".join(body))


def _join(pre: list, sections: list) -> str:
    out = list(pre)
    while out and not out[-1].strip():
        out.pop()
    for _name, header, body in sections:
        out += ["", header, ""]
        trimmed = list(body)
        while trimmed and not trimmed[0].strip():
            trimmed.pop(0)
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
        out += trimmed
    return "\n".join(out).rstrip("\n") + "\n"


def _file_confidence(text: str, verdict=None) -> float:
    """The mean of the rules', raised while the last test said yes
    (cr1#401, `craft_contract.file_confidence`)."""
    return cc.file_confidence(text, verdict, default=START_CONFIDENCE_SAID)


def _set_header(text: str, one: str, two: str) -> str:
    text = re.sub(r"<!--\s*craft:.*?-->", lambda _m: one, text, count=1,
                  flags=re.DOTALL)
    return re.sub(r"<!--\s*last-updated:.*?-->", lambda _m: two, text,
                  count=1, flags=re.DOTALL)


def _section_of(split: dict, number: str) -> Optional[str]:
    for name in ("Rules", "What they never do"):
        for lines in split.get(name, ([], []))[1]:
            m = _ITEM_ID_RE.match(lines[0])
            if m and f"R{m.group(1)}" == number:
                return name
    return None


def _compose(*, existing_text, preview, kept, answered_old, open_new,
             history, today, tested, verdict, status, consent) -> str:
    """The file's new text. Each kept rule carries its `number` (an amended
    one its `amends`); an amended rule is replaced in place, in its own
    section, and the caller keeps its old text in History."""
    if existing_text is None:
        title = _title_phrase(preview["subject_name"], preview["scope_label"])
        existing_text = "\n".join(
            ["<!-- craft: -->", "<!-- last-updated: -->", "", f"# {title}"]
            + sum((["", f"## {s}", ""] for s in cc.SECTIONS), []))
    pre, sections = _split(existing_text)
    by_name = {s[0]: s for s in sections}
    split = {name: _items_of(by_name[name][2]) for name in by_name}
    for rule in kept:
        number = rule.get("amends")
        if not number:
            continue
        section = _section_of(split, number)
        items = split[section][1]
        for i, lines in enumerate(items):
            m = _ITEM_ID_RE.match(lines[0])
            if m and f"R{m.group(1)}" == number:
                items[i] = _item_lines(dict(rule, section=section), number,
                                       today)
    for section in ("Rules", "What they never do"):
        for rule in kept:
            if not rule.get("amends") and rule["section"] == section:
                split[section][1].append(
                    _item_lines(rule, rule["number"], today))
    for rule in kept:
        if rule["section"] == "Order of work" and not rule.get("amends"):
            items = split["Order of work"][1]
            items.append(_item_lines(rule, len(items) + 1, today))
    lead, items = split["Open questions"]
    items = [lines for lines in items
             if _norm(_item_text(lines)) not in answered_old]
    split["Open questions"] = (lead, items + [[f"- {q}"] for q in open_new])
    split["History"][1].extend([f"- {entry}"] for entry in history)
    for name, (lead, items) in split.items():
        body = list(lead)
        for lines in items:
            lines = list(lines)
            while len(lines) > 1 and not lines[-1].strip():
                lines.pop()
            body += lines
        by_name[name][2] = body
    text = _join(pre, sections)
    one, two = _header(preview["subject_id"], preview["scope"], status,
                       _file_confidence(text, verdict), consent, today, tested,
                       verdict)
    return _set_header(text, one, two)


def _count_words(sources: list) -> str:
    words = {"documents": ("document", "documents"),
             "transcripts": ("meeting", "meetings"),
             "notes": ("session note", "session notes"),
             "facts": ("noted fact", "noted facts"),
             "mail": ("mail thread", "mail threads")}
    parts = []
    for row in sources or []:
        one, many = words.get(row.get("kind"), ("source", "sources"))
        n = row.get("count") or 0
        parts.append(f"{n} {one if n == 1 else many}")
    return ", ".join(parts) if parts else "no gathered source"


def last_capture(workspace_root, subject_id, scope) -> Optional[dict]:
    """The newest `craft_captured` row for one subject and scope, as
    {"date", "seq", "rule_count", "verdict", "action"}, or None. The named
    consumer of the event: the gather step says when a file was last
    captured. Never raises."""
    newest = None
    for ev in _load_events(Path(workspace_root)):
        if ev.get("type") != EVENT_TYPE:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if data.get("subject_id") != subject_id or data.get("scope") != scope:
            continue
        seq = ev.get("seq") if isinstance(ev.get("seq"), int) else -1
        best = newest.get("seq") if newest is not None \
            and isinstance(newest.get("seq"), int) else -1
        if newest is None or seq >= best:
            newest = ev
    if newest is None:
        return None
    data = newest.get("data") or {}
    return {"date": _iso_date(data.get("date")) or _ev_date(newest),
            "seq": newest.get("seq"),
            "rule_count": data.get("rule_count"),
            "verdict": data.get("verdict"), "action": data.get("action")}


def write_craft(workspace_root, preview, decisions, *, answers=None,
                verdict=None, now_iso=None, source_skill=WRITER,
                entities=None) -> dict:
    """Write the craft file the owner confirmed. The one writer here.

    `decisions` maps a preview key (P1) to "confirm", "dismiss" or
    {"edit": "<the owner's words>"}; an edit counts as a confirm. A rule
    with no decision is not written. `answers` maps a question key (Q1) to
    the owner's answer or None; an unanswered question, and every question
    that was not asked, goes to Open questions, and an answered one already
    in the file leaves it. `verdict` is the held-out test's answer (yes,
    partly or no). Only that fresh test can confirm the file, with yes or
    partly; a no keeps it proposed, so recall does not use it for anyone but
    the owner. With no fresh test a new file is proposed and never tested,
    and an amendment keeps the file's status and its last test. A
    superseded file is never promoted (`_status_after`).

    The preview came back through the chat, so it is not trusted: the
    writer gathers again (with `preview["gather_inputs"]`, what the first
    gather was handed beyond the workspace) and refuses every rule citing a
    pointer that gather does not find; evidence dates, kinds, the starting
    confidence and the held-out artifact are taken from that gather.

    Nothing is written without at least one confirm, nor when a kept rule
    would be refused by the gate (`craft_contract.gate_craft_text`), nor
    when the file changed on disk since it was read. The file lands through
    `workspace_access.write`; then ONE `craft_captured` event through
    `event_gate.append_event`; then the CRAFT view is regenerated.

    Returns {"ok": True, "rel", "action", "status", "rule_count", "tested",
    "verdict", "seq", "view", "line", "questions_dropped"} or {"ok": False,
    "refused", "line", ...}."""
    root = Path(workspace_root)
    if not isinstance(preview, dict) or not preview.get("ok"):
        return {"ok": False, "refused": "no_preview",
                "line": "There is no preview to save from, so nothing was "
                        "saved."}
    ents = entities if isinstance(entities, dict) else cc.load_entities(root)
    today = _today(now_iso)
    subject, scope = preview.get("subject_id"), preview.get("scope")
    try:
        target = cc.craft_path(root, subject, scope, entities=ents)
    except ValueError:
        target = None
    if target is None or _rel(root, target) != preview.get("target"):
        return {"ok": False, "refused": "target_changed",
                "line": "Where this file belongs has changed since the "
                        "preview, so nothing was saved. Capture it again."}
    rel = _rel(root, target)

    decisions = decisions if isinstance(decisions, dict) else {}
    kept, dismissed, undecided = [], 0, 0
    for rule in preview.get("rules") or []:
        if not isinstance(rule, dict) or not rule.get("evidence"):
            continue
        d = decisions.get(rule.get("key"))
        if d == "confirm":
            kept.append(dict(rule))
        elif isinstance(d, dict) and _plain(d.get("edit")):
            kept.append(dict(rule, text=_plain(d["edit"]), edited=True))
        elif d == "dismiss":
            dismissed += 1
        else:
            undecided += 1
    if not kept:
        return {"ok": False, "refused": "nothing_confirmed",
                "line": "Nothing was confirmed, so nothing was saved."}

    # The preview passed through the chat, so its pointers are not trusted:
    # gather again from the workspace (with what the first gather was
    # handed) and refuse a rule citing anything not gathered. Dates, kinds
    # and the starting confidence come from this gather, never the preview
    # (review of b7f4ca04, finding 3).
    inputs = preview.get("gather_inputs") if isinstance(
        preview.get("gather_inputs"), dict) else {}
    again = gather_sources(
        root, subject, scope, entities=ents,
        mail_named=inputs.get("mail_named") is True,
        mail_items=inputs.get("mail_items") if isinstance(
            inputs.get("mail_items"), list) else None,
        transcript_hits=inputs.get("transcript_hits") if isinstance(
            inputs.get("transcript_hits"), list) else None,
        added=inputs.get("added") if isinstance(inputs.get("added"), list)
        else None)
    if not again.get("ok"):
        return {"ok": False, "refused": "not_gathered_again",
                "line": "Nothing was saved: " + (again.get("line") or
                                                 "the sources could not be "
                                                 "gathered again.")}
    by_ref = {it["ref"]: it for it in again["items"]}
    unknown = []
    for rule in kept:
        refs = [e.get("ref") for e in rule["evidence"]
                if isinstance(e, dict)]
        if not refs or any(r not in by_ref for r in refs):
            unknown.append(rule.get("key") or "a rule")
            continue
        rule["evidence"] = [{"ref": r, "date": by_ref[r].get("date"),
                             "kind": by_ref[r]["kind"],
                             "label": by_ref[r].get("label")} for r in refs]
        own = any(by_ref[r]["own_work"] for r in refs)
        rule["confidence"] = (START_CONFIDENCE_OWN_WORK if own
                              else START_CONFIDENCE_SAID)
        rule["basis"] = BASIS_OWN_WORK if own else BASIS_SAID
        if rule["section"] not in cc.EVIDENCE_SECTIONS:
            rule["section"] = "Rules"
    if unknown:
        return {"ok": False, "refused": "evidence_not_gathered",
                "line": "Nothing was saved: " + ", ".join(unknown)
                        + " cites evidence that was not gathered. Capture "
                          "again so every rule points at what was found."}
    blocked = []
    for rule in kept:
        found = _findings(rule["text"], rule["section"], ents)
        if found:
            blocked.append(f"{rule['key']} carries a "
                           + ", ".join(sorted({f['kind'] for f in found})))
    if blocked:
        return {"ok": False, "refused": "privacy",
                "line": "Nothing was saved: " + "; ".join(blocked)
                        + ". Edit or dismiss it and save again."}

    answers = answers if isinstance(answers, dict) else {}
    answered = {q["key"] for q in preview.get("questions") or []
                if _plain(answers.get(q.get("key")))}
    answered_old = {_norm(q["text"]) for q in preview.get("questions") or []
                    if q.get("existing") and q["key"] in answered}
    open_new, dropped_q = [], 0
    for q in list(preview.get("questions") or []) + list(
            preview.get("open_questions") or []):
        if q.get("existing") or q.get("key") in answered:
            continue
        if _findings(q["text"], "Open questions", ents):
            dropped_q += 1
            continue
        open_new.append(q["text"])

    used = {e["ref"] for r in kept for e in r["evidence"]}
    held = preview.get("held_out")
    held = by_ref.get(held.get("ref")) if isinstance(held, dict) else None
    tested = verdict_out = None
    if verdict in cc.VERDICTS and held is not None \
            and held["kind"] in HELD_OUT_KINDS and held["ref"] not in used:
        tested, verdict_out = today, verdict

    existing_text = None
    before_mtime = None
    if target.exists():
        if preview.get("action") != "amend":
            return {"ok": False, "refused": "existing_appeared",
                    "line": "A file for this appeared since the preview, so "
                            "nothing was written over it. Capture it again "
                            "to amend it."}
        try:
            existing_text = target.read_text(encoding="utf-8")
            before_mtime = target.stat().st_mtime_ns
        except OSError:
            return {"ok": False, "refused": "existing_unreadable",
                    "line": "The existing file could not be read, so nothing "
                            "was saved."}
        check = cc.validate_craft(existing_text, workspace_root=root,
                                  entities=ents, path=target)
        if check["errors"] or check["header"].get("subject") != subject:
            return {"ok": False, "refused": "existing_unreadable",
                    "line": "The existing file does not read as a craft "
                            "file, so nothing was written over it."}
        consent = check["header"].get("consent")
        prior = check["header"]
    elif preview.get("action") == "amend":
        return {"ok": False, "refused": "existing_gone",
                "line": "The file this would amend is gone, so nothing was "
                        "saved. Capture it again."}
    else:
        consent = None
        prior = None
    status, kept_tested, kept_verdict = _status_after(prior, tested,
                                                      verdict_out)
    if cc.is_third_party_person(ents, subject):
        consent = consent if consent in cc.CONSENT_VALUES \
            else cc.DEFAULT_CONSENT
    else:
        consent = None

    old_rules = {r["id"]: r for r in
                 cc.parse_craft(existing_text)["rules"]} if existing_text \
        else {}
    numbers = [int(n[1:]) for n in old_rules]
    next_no = (max(numbers) if numbers else 0) + 1
    amended_numbers, new_numbers = [], []
    for rule in kept:
        if rule.get("amends") and rule["amends"] not in old_rules:
            rule["amends"] = None
        if rule.get("amends"):
            amended_numbers.append(rule["amends"])
    for section in ("Rules", "What they never do"):
        for rule in kept:
            if not rule.get("amends") and rule["section"] == section:
                rule["number"] = f"R{next_no}"
                new_numbers.append(rule["number"])
                next_no += 1
    steps = sum(1 for r in kept if r["section"] == "Order of work"
                and not r.get("amends"))
    what = []
    if new_numbers:
        what.append("confirmed " + ", ".join(new_numbers))
    if steps:
        what.append(f"{steps} step(s) in the order of work")
    if dismissed:
        what.append(f"dismissed {dismissed}")
    verb = "amended" if existing_text is not None else "captured"
    entry = (f"{today} {verb} by {WRITER} from "
             f"{_count_words(again['sources'])}"
             + (": " + "; ".join(what) if what else "") + ". ")
    if tested:
        kind = _KIND_PREFIX.get(held.get("kind"), "source")
        entry += f"Tested on a held-out {kind}: verdict {verdict_out}."
    else:
        entry += "Not tested on a held-out artifact."
    for n in amended_numbers:
        entry += (f" {n} amended; its superseded text: "
                  f"{old_rules[n]['body']}")
    history = [entry]
    text = _compose(
        existing_text=existing_text, preview=preview, kept=kept,
        answered_old=answered_old, open_new=open_new, history=history,
        today=today, tested=kept_tested, verdict=kept_verdict, status=status,
        consent=consent)

    try:
        res = cc.gate_craft_text(text, workspace_root=root, entities=ents,
                                 path=target)
    except cc.CraftRefused as exc:
        kind = "privacy" if exc.findings else "shape"
        named = sorted({f"{f.get('item')} carries a {f.get('kind')}"
                        for f in exc.findings})
        what = "; ".join(named) if named else "; ".join(exc.errors[:3])
        return {"ok": False, "refused": kind,
                "line": "Nothing was saved: the file did not pass the craft "
                        f"check ({what}).",
                "errors": exc.errors, "findings": exc.findings}

    landed = _land(root, rel, text, before_mtime)
    if not landed.get("ok"):
        return {"ok": False, "refused": "not_landed",
                "line": landed.get("line") or "The file could not be saved "
                        "to your folder, so nothing changed.",
                "detail": {k: landed.get(k) for k in ("reason", "detail")
                           if landed.get(k)}}

    rule_count = len(res["rules"])
    data = {
        "subject_id": subject, "scope": scope, "path": rel, "date": today,
        "action": "amend" if existing_text is not None else "create",
        "status": status, "rule_count": rule_count,
        "rules_added": len(new_numbers), "rules_amended": len(
            amended_numbers),
        "steps_added": steps, "dismissed": dismissed,
        "tested": tested, "verdict": verdict_out,
        "held_out": (held or {}).get("ref") if tested else None,
        "sources": [{"kind": s.get("kind"), "count": s.get("count"),
                     "first": s.get("first"), "last": s.get("last")}
                    for s in again["sources"]],
        "evidence_refs": sorted(used),
    }
    seq = None
    try:
        from event_gate import append_event
        written = append_event(_events_file(root), [{
            "type": EVENT_TYPE, "source_skill": source_skill, "data": data,
        }], holder=f"craft_capture:{source_skill}")
        seq = written[0].get("seq") if written else None
    except Exception as exc:  # noqa: BLE001 - the file landed; say so
        return {"ok": True, "rel": rel, "action": data["action"],
                "status": status, "rule_count": rule_count,
                "tested": tested, "verdict": verdict_out, "seq": None,
                "event_error": str(exc), "view": None,
                "line": _saved_line(preview, rule_count, tested, verdict_out,
                                    status, kept_tested, kept_verdict),
                "questions_dropped": dropped_q}
    try:
        import render_craft_view
        view = render_craft_view.regenerate(root)
    except Exception as exc:  # noqa: BLE001 - the view is rebuilt weekly too
        view = {"error": str(exc)}
    return {"ok": True, "rel": rel, "action": data["action"],
            "status": status, "rule_count": rule_count, "tested": tested,
            "verdict": verdict_out, "seq": seq, "view": view,
            "line": _saved_line(preview, rule_count, tested, verdict_out,
                                status, kept_tested, kept_verdict),
            "questions_dropped": dropped_q}


def _saved_line(preview, rule_count, tested, verdict, status,
                kept_tested=None, kept_verdict=None) -> str:
    head = _title_phrase(preview.get("subject_name") or "they",
                         preview.get("scope_label") or preview.get("scope"))
    line = (f"Saved {head[0].lower() + head[1:]}: {rule_count} "
            f"{'rule' if rule_count == 1 else 'rules'}, ")
    if tested:
        line += f"tested on {tested}, verdict {verdict}."
    elif kept_tested:
        line += (f"not tested this time; last tested on {kept_tested}, "
                 f"verdict {kept_verdict}.")
    else:
        line += "not tested yet."
    if status == "proposed":
        line += (" It is kept as proposed, so it is not used for drafts "
                 "until a held-out test says yes or partly.")
    elif status == "superseded":
        line += " It stays superseded, so it is not used."
    return line


def _status_after(prior, tested, verdict) -> tuple:
    """(status, tested, verdict) for the file after this write (review of
    b7f4ca04, finding 1). Only this run's own held-out test can confirm a
    file, with yes or partly; a no keeps it proposed. With no fresh test an
    amendment keeps the file's status and its last test, and a new file is
    proposed. A superseded file is never promoted."""
    prior = prior or {}
    prior_status = prior.get("status")
    if prior_status == "superseded":
        status = "superseded"
    elif tested and verdict in ("yes", "partly"):
        status = "confirmed"
    elif tested and verdict == "no":
        status = "proposed"
    elif prior_status in cc.STATUSES:
        status = prior_status
    else:
        status = "proposed"
    if tested:
        return status, tested, verdict
    return status, prior.get("tested"), prior.get("verdict")


def _land(root: Path, rel: str, text: str, expected_mtime) -> dict:
    """The file through the layer's text door, run against this workspace:
    the call `deliverables.land` makes for text, without its document-suffix
    rule, which a Markdown craft file would fail."""
    import workspace_access as wa
    ctx = wa.resolve(start=root)
    vm = ctx.get("vm_path")
    if not vm or Path(vm).resolve() != root.resolve():
        return {"ok": False, "reason": "workspace_mismatch",
                "line": "This run is not working in that folder, so nothing "
                        "was saved."}
    parents = rel.startswith((cc.HQ_DIR + "/", cc.PEOPLE_DIR + "/")) or \
        (root / rel).parent.parent.is_dir()
    out = wa.write(rel, text, expected_mtime, create_parents=parents,
                   holder="craft_capture", ctx=ctx)
    if out.get("ok") and out.get("verified"):
        return out
    out = dict(out, ok=False)
    if out.get("reason") == wa.R_MISSING_PARENT:
        out["line"] = ("That project does not have a folder yet, so nothing "
                       "was saved. Create the project first.")
    elif out.get("reason") == getattr(wa, "R_STALE_READ", "stale_read"):
        out["line"] = ("The file changed while you were reviewing, so nothing "
                       "was saved. Capture it again to see the change.")
    return out


__all__ = [
    "NOTICE", "TEST_QUESTION", "MAX_QUESTIONS", "SOURCE_KINDS",
    "START_CONFIDENCE_OWN_WORK", "START_CONFIDENCE_SAID", "EVENT_TYPE",
    "match_capture_ask", "gather_sources", "build_preview", "write_craft",
    "last_capture",
]
