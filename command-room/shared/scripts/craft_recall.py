#!/usr/bin/env python3
"""Craft recall: the craft files a task reads, and the Sources lines for the rules it used (SPEC CRAFT1 step 2, cr1#397).

WHY THIS EXISTS
---------------
Step 1 (`craft_contract`, cr1#396) fixed what a craft file is and where it
lives. Until now a file only worked when a folder's notes pointed at it by
hand. This module is the recall half: when a skill works for or with an
entity, it reads that entity's craft files on its own, and every rule the
output used is named in the Sources line in the rule's own words, so the owner
can see why the output came out the way it did.

WHAT LOADS (SPEC CRAFT1 section 4, owner rulings 2026-10-24)
------------------------------------------------------------
  - The link is the folder and the id, never a name: a file loads for a
    subject only when it sits in that subject's craft folder by the step 1
    convention AND its header names that subject's id.
  - `confirmed` files load. `proposed` files load only when the subject is
    the owner (the primary user). `superseded` files never load.
  - The task picks the scopes (`TASK_SCOPES`): a draft or a memo reads write
    and decide, a contract review reads review, call prep and apply read
    every scope.
  - A file that fails the step 1 shape is skipped and named in `skipped`; it
    is never half-read.
  - A contract review reads the owner's review method first, then the
    firm's, then the client business's; never the counterparty's
    (`TASK_LEADS`, `counterparty_ids`; ruling on cr1#397 review finding 4).
  - A subject with no craft file reads nothing, and the answer says nothing,
    except a contract review, which says no stored method was found
    (`NO_METHOD_LINES`).

CITATIONS (ruling C3: summaries are for display; recall goes back to the
source through ids)
-----------------------------------------------------------------------
Each loaded rule carries a `cite_id`, `<path in the workspace>#R<n>`.
`cite_rules` takes the ids of the rules an output used, opens each file again
as it is now, checks it may still load, and returns one line per rule:

    "<the rule's own words>", per how Sam Sample reviews (R3, confirmed Oct)

The line is composed here, never by hand. A rule that may not load (gone,
superseded, proposed about someone other than the owner, outside its
subject's folder) is refused with a reason in words and is never cited.

PUBLIC API
----------
  TASK_SCOPES, TASK_LEADS, NO_METHOD_LINES
  subject_folders(workspace_root, subject_id, *, entities=None) -> list[Path]
  craft_for_task(workspace_root, subject_ids, task, *, scope=None,
                 counterparty_ids=None, entities=None)
      -> {"files", "skipped"[, "empty_line"]} | {"error"}
  cite_rules(workspace_root, cite_ids, *, now_iso=None, entities=None)
      -> {"lines", "refused"}
  craft_inventory(workspace_root, subject_id, *, entities=None)
      -> {"subject_id", "subject_name", "files"}
  match_craft_verb(text, *, workspace_root=None)
      -> {"verb": "list"|"apply"|None, "name", "subject_ids"}
  craft_signature(workspace_root, subject_ids, *, entities=None) -> tuple

Read only: nothing here writes. Every function answers; none raises on a bad
file or a bad argument.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import craft_contract as cc  # noqa: E402

#: The scopes each task reads. None means every scope on the workspace's
#: list (the fixed five plus the ones the owner added).
TASK_SCOPES: dict = {
    "draft": ("write", "decide"),
    "memo": ("write", "decide"),
    "contract-review": ("review",),
    "call-prep": None,
    "apply": None,
}

#: How a scope reads after "per how <name>", third person then second.
_SCOPE_VERBS = {
    "review": ("reviews", "review"),
    "decide": ("decides", "decide"),
    "write": ("writes", "write"),
    "run-a-meeting": ("runs a meeting", "run a meeting"),
    "sell": ("sells", "sell"),
}

#: Subjects a task reads first, before the ones the caller names (ruling on
#: cr1#397 review finding 4): a contract review applies the owner's own
#: review method, then the firm's, then the client's; a draft and a memo
#: apply the owner's write method, then the firm's, then the recipient's,
#: project's or business's (ruling on cr1#399 open question 1). "owner" is
#: the primary user, "firm" the owner's own company.
TASK_LEADS: dict = {
    "contract-review": ("owner", "firm"),
    "draft": ("owner", "firm"),
    "memo": ("owner", "firm"),
}

#: The scopes a lead subject is read for, when narrower than the task's.
#: A draft or a memo reads the owner's and the firm's WRITE method on every
#: draft, never their other scopes (ruling on cr1#399 open question 1, the
#: coordinator's reading of cr1#381 and SPEC CRAFT1 section 6). Precedence:
#: the owner's method sets how he writes, so it loads first; a recipient's
#: or business's file, loaded after it, adds what that relationship needs.
LEAD_SCOPES: dict = {
    "draft": ("write",),
    "memo": ("write",),
}

#: The one sentence a task says when no method loaded. Only contract review
#: says anything; every other task stays silent.
NO_METHOD_LINES: dict = {
    "contract-review": "No stored method was found, so this review follows "
                       "your standard terms only.",
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")
_CONFIRMED_RE = re.compile(r"Confirmed by owner\s+(\d{4}-\d{2}-\d{2})")


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def _entities(workspace_root, entities) -> dict:
    if isinstance(entities, dict):
        return entities
    return cc.load_entities(workspace_root)


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
    """One pass over the records, made once per call: the owner, the owner's
    company, every record by id, and the threads. Every lookup after that is
    a dict read, so a call costs the same however many people are on file
    (cr1#397 review finding 1: resolving each person through the contract's
    resolver rescanned the records every time)."""

    def __init__(self, root: Path, ents: dict):
        self.root = root
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

    def record(self, entity_id) -> Optional[dict]:
        return self.by_id.get(entity_id) if entity_id else None

    def name(self, subject_id) -> str:
        if subject_id and subject_id == self.owner:
            return "you"
        rec = self.record(subject_id) or {}
        name = (rec.get("canonical_name") or rec.get("display_name")
                or rec.get("legal_name"))
        return str(name) if name else "someone not on file"

    def folders(self, subject_id) -> list:
        """The step 1 convention (`craft_contract.craft_path`), read from
        this index: the owner's `_hq/craft/`, the firm's `_hq/craft/firm/`,
        a person's `_people/<slug>/craft/`, a project's `<folder>/craft/`,
        and a business's project folders (each one, when it has several)."""
        if not subject_id or not isinstance(subject_id, str):
            return []
        root = self.root
        if subject_id == self.owner:
            return [root / cc.HQ_DIR / cc.CRAFT_DIR]
        rec = self.record(subject_id)
        if rec is None:
            return []
        if subject_id in self.own_orgs:
            return [root / cc.HQ_DIR / cc.CRAFT_DIR / cc.FIRM_DIR]
        if rec["_kind"] == "people":
            return [root / cc.PEOPLE_DIR
                    / cc.person_slug(rec.get("canonical_name"))
                    / cc.CRAFT_DIR]
        if rec["_kind"] == "threads":
            folder = _thread_folder(rec)
            return [root / folder / cc.CRAFT_DIR] if folder else []
        folders = sorted({_thread_folder(t) for t in self.threads
                          if subject_id in {t.get("org_id"),
                                            t.get("affiliation_id")}
                          and _thread_folder(t)})
        return [root / f / cc.CRAFT_DIR for f in folders]


def _index(workspace_root, entities) -> _Index:
    root = Path(workspace_root)
    return _Index(root, _entities(root, entities))


# ---------------------------------------------------------------------------
# Folders: the step 1 convention, read by id
# ---------------------------------------------------------------------------

def _thread_folder(thread: dict) -> Optional[str]:
    folder = str(thread.get("folder_name") or "").strip().replace("\\", "/")
    folder = folder.strip("/")
    if not folder or ".." in folder.split("/"):
        return None
    return folder


def subject_folders(workspace_root, subject_id, *, entities=None) -> list:
    """The craft folders a subject's files may sit in, by the step 1
    convention: the one folder `craft_contract.craft_path` names, or, for a
    business with several project folders, each of their `craft/` folders.
    Paths, existing or not. Never raises."""
    return _index(workspace_root, entities).folders(subject_id)


def _md_files(folder: Path, root: Path) -> list:
    """The `.md` files directly in a craft folder. A symlink, or anything
    that resolves outside the workspace, is never read
    (`craft_contract.is_safe_path`, the rule discovery follows)."""
    if not cc.is_safe_path(root, folder):
        return []
    try:
        return sorted(p for p in folder.iterdir()
                      if p.suffix == ".md" and p.is_file()
                      and cc.is_safe_path(root, p))
    except OSError:
        return []


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def craft_signature(workspace_root, subject_ids, *, entities=None) -> tuple:
    """A memo-cache witness: (path, size, mtime_ns, sha256) for every `.md`
    in the named subjects' craft folders AND in every person's craft folder
    that exists (`_people/*/craft`, one listing), plus each folder's own
    presence. A craft file added, removed or rewritten changes it. Its cost
    follows the craft folders on disk, never the people on file (review
    finding 1). Never raises."""
    root = Path(workspace_root)
    idx = _index(root, entities)
    folders = []
    for sid in subject_ids or []:
        folders.extend(idx.folders(sid))
    try:
        people_root = root / cc.PEOPLE_DIR
        if people_root.is_dir():
            folders.extend(sorted(p / cc.CRAFT_DIR
                                  for p in people_root.iterdir()
                                  if (p / cc.CRAFT_DIR).is_dir()))
    except OSError:
        pass
    parts = []
    seen = set()
    for folder in folders:
        if folder in seen:
            continue
        seen.add(folder)
        files = _md_files(folder, root)
        parts.append((str(folder), bool(files)))
        for p in files:
            # content hashed as well: a same-size rewrite that keeps its
            # mtime must still change the signature (the reader's R1
            # hazard for every .md input)
            try:
                st = p.stat()
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                parts.append((str(p), st.st_size, st.st_mtime_ns, digest))
            except OSError:
                parts.append((str(p), None))
    return tuple(parts)


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------

def _may_load(status, subject_id, owner) -> bool:
    if status == "confirmed":
        return True
    return status == "proposed" and bool(owner) and subject_id == owner


def _read_file(root: Path, path: Path, ents: dict) -> dict:
    """Parse and check one craft file. Returns {"ok", "reason", "parsed",
    "header"}; `reason` is plain words when the file fails the contract."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"ok": False, "reason": "the file could not be read",
                "parsed": None, "header": {}}
    res = cc.validate_craft(text, workspace_root=root, entities=ents,
                            path=path)
    if res["errors"]:
        return {"ok": False, "header": res["header"], "parsed": res,
                "reason": "it does not follow the craft file shape: "
                          + res["errors"][0]}
    return {"ok": True, "reason": None, "parsed": res,
            "header": res["header"]}


def _file_view(root: Path, path: Path, parsed: dict, idx: _Index) -> dict:
    header = parsed["header"]
    subject = header.get("subject")
    rel = _rel(root, path)
    rules = []
    for item in parsed["items"]:
        if not item.get("id"):
            continue
        rules.append({"cite_id": f"{rel}#{item['id']}",
                      "number": item["id"],
                      "section": item["section"],
                      "text": item["body"],
                      "evidence": item.get("evidence") or None})
    sections = parsed.get("sections") or {}
    return {
        "id": rel,
        "subject_id": subject,
        "subject_name": idx.name(subject),
        "scope": header.get("scope"),
        "scope_label": cc.scope_label(header.get("scope"), root),
        "status": header.get("status"),
        "confidence": header.get("confidence"),
        "tested": header.get("tested"),
        "verdict": header.get("verdict"),
        "last_updated": header.get("last_updated"),
        "consent": parsed.get("consent"),
        "rules": rules,
        "order_of_work": [it["body"] for it in
                          sections.get("Order of work", [])],
        "open_questions": [it["body"] for it in
                           sections.get("Open questions", [])],
    }


def _scope_order(root: Path) -> dict:
    return {row["slug"]: i for i, row in
            enumerate(cc.known_scopes(root))}


# ---------------------------------------------------------------------------
# Loading for a task
# ---------------------------------------------------------------------------

def craft_for_task(workspace_root, subject_ids, task, *, scope=None,
                   counterparty_ids=None, entities=None,
                   leads=True) -> dict:
    """The craft files `task` reads for `subject_ids`, in the order the
    subjects were named and, within a subject, in scope-list order.

    A task in `TASK_LEADS` reads its lead subjects first: a contract review
    reads the owner's review method, then the firm's, then the client
    businesses named in `subject_ids`; a draft and a memo read the owner's
    write method, then the firm's (`LEAD_SCOPES`), then the recipients',
    project's and business's write and decide. `leads=False` reads only the
    subjects named: the thread loader passes it, because the skill's own
    call already carries the leads and a thread payload must not repeat
    them. `counterparty_ids` are never read for
    any task, even when named as a subject: a business's review file loads
    only for a review done for it, never for a review of its paper. When a
    task in `NO_METHOD_LINES` loads nothing, the result carries that line as
    `empty_line`; no other task says anything.

    Returns {"files": [file, ...], "skipped": [{"id", "reason"}, ...]}, a
    file being {"id", "subject_id", "subject_name", "scope", "scope_label",
    "status", "confidence", "tested", "verdict", "last_updated", "consent",
    "rules": [{"cite_id", "number", "section", "text", "evidence"}],
    "order_of_work", "open_questions"}. An unknown task answers
    {"error": ...}. `skipped` names only files that should have loaded and
    failed the contract; a proposed file about someone other than the owner
    is simply not read."""
    if task not in TASK_SCOPES:
        return {"error": f"unknown task {task!r}; the tasks are "
                         + ", ".join(sorted(TASK_SCOPES))}
    if isinstance(subject_ids, str):
        subject_ids = [subject_ids]
    root = Path(workspace_root)
    idx = _index(root, entities)
    ents, owner = idx.ents, idx.owner
    never = {c for c in (counterparty_ids or []) if isinstance(c, str)}
    lead_ids = []
    for lead in (TASK_LEADS.get(task, ()) if leads else ()):
        if lead == "owner" and owner:
            lead_ids.append(owner)
        elif lead == "firm":
            lead_ids.extend(sorted(o for o in idx.own_orgs
                                   if idx.record(o)))
    wanted = TASK_SCOPES[task]
    if scope:
        wanted = (scope,) if wanted is None or scope in wanted else ()
    lead_wanted = wanted
    if task in LEAD_SCOPES:
        narrow = LEAD_SCOPES[task]
        lead_wanted = tuple(s for s in narrow
                            if wanted is None or s in wanted)
    plan = [(s, lead_wanted) for s in lead_ids if s not in never]
    plan += [(s, wanted) for s in subject_ids or [] if s not in never]
    order = _scope_order(root)

    files: list = []
    skipped: list = []
    seen_subjects: set = set()
    seen_paths: set = set()
    for sid, wanted in plan:
        if not isinstance(sid, str) or not sid \
                or (sid, wanted) in seen_subjects:
            continue
        seen_subjects.add((sid, wanted))
        mine = []
        for folder in idx.folders(sid):
            for path in _md_files(folder, root):
                if path in seen_paths:
                    continue
                head = cc.parse_craft(_safe_read(path))["header"]
                if head.get("subject") != sid:
                    continue
                if wanted is not None and head.get("scope") not in wanted:
                    continue
                if not _may_load(head.get("status"), sid, owner):
                    continue
                seen_paths.add(path)
                got = _read_file(root, path, ents)
                if not got["ok"]:
                    skipped.append({"id": _rel(root, path),
                                    "reason": got["reason"]})
                    continue
                if not cc.placement(root, path, got["header"],
                                    entities=ents)["placed"]:
                    skipped.append({"id": _rel(root, path),
                                    "reason": "no record points at the "
                                              "folder it sits in"})
                    continue
                mine.append(_file_view(root, path, got["parsed"], idx))
        mine.sort(key=lambda f: (order.get(f["scope"], len(order)),
                                 f["id"]))
        files.extend(mine)
    out = {"files": files, "skipped": skipped}
    if not files and task in NO_METHOD_LINES:
        out["empty_line"] = NO_METHOD_LINES[task]
    return out


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

def _when(date_text, now: _dt.date) -> Optional[str]:
    try:
        d = _dt.date.fromisoformat(str(date_text))
    except (TypeError, ValueError):
        return None
    month = _MONTHS[d.month - 1]
    return month if d.year == now.year else f"{month} {d.year}"


def _today(now_iso) -> _dt.date:
    if now_iso:
        try:
            return _dt.datetime.fromisoformat(
                str(now_iso).replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return _dt.date.today()


def _how(scope, label, owner_subject: bool) -> str:
    third, second = _SCOPE_VERBS.get(scope, (f"does {label}",
                                             f"do {label}"))
    return second if owner_subject else third


def _resolve_cite_path(root: Path, rel: str) -> Optional[Path]:
    if not rel or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        return None
    parts = rel.replace("\\", "/").split("/")
    if ".." in parts or not rel.endswith(".md"):
        return None
    path = root.joinpath(*parts)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    if not cc.is_safe_path(root, path):
        return None
    parent = path.parent
    in_craft = parent.name == cc.CRAFT_DIR or (
        parent.name == cc.FIRM_DIR and parent.parent.name == cc.CRAFT_DIR)
    return path if in_craft else None


def cite_rules(workspace_root, cite_ids, *, now_iso=None,
               entities=None) -> dict:
    """One Sources line per rule an output used, read from the file as it is
    now. Returns {"lines": [...], "refused": [{"id", "reason"}, ...]}; the
    lines keep the order the ids were given, each rule once."""
    root = Path(workspace_root)
    idx = _index(root, entities)
    ents, owner = idx.ents, idx.owner
    today = _today(now_iso)
    lines: list = []
    refused: list = []
    done: set = set()
    cache: dict = {}
    if isinstance(cite_ids, str):
        cite_ids = [cite_ids]
    for cid in cite_ids or []:
        if not isinstance(cid, str) or cid in done:
            continue
        done.add(cid)

        def refuse(reason):
            refused.append({"id": cid, "reason": reason})

        rel, sep, number = cid.rpartition("#")
        if not sep or not re.match(r"^R\d+$", number):
            refuse("that is not a rule's id (a file path, then #R and the "
                   "rule's number)")
            continue
        path = _resolve_cite_path(root, rel)
        if path is None:
            refuse("that path is not a craft file in this workspace")
            continue
        if path not in cache:
            cache[path] = _read_file(root, path, ents) if path.is_file() \
                else {"ok": False, "reason": "the file is not there any more",
                      "parsed": None, "header": {}}
        got = cache[path]
        if not got["ok"]:
            refuse(got["reason"])
            continue
        header = got["header"]
        subject = header.get("subject")
        if path.parent not in idx.folders(subject):
            refuse("the file is not in the folder of the one it is about")
            continue
        if not _may_load(header.get("status"), subject, owner):
            refuse(f"the file is {header.get('status')}, so it is not used")
            continue
        rule = next((it for it in got["parsed"]["items"]
                     if it.get("id") == number), None)
        if rule is None:
            refuse(f"the file has no rule {number}")
            continue
        status = header.get("status")
        stamp = None
        if status == "confirmed":
            m = _CONFIRMED_RE.search(rule.get("evidence_text") or "")
            stamp = m.group(1) if m else None
        when = _when(stamp or header.get("last_updated"), today)
        owner_subject = bool(owner) and subject == owner
        name = idx.name(subject)
        how = _how(header.get("scope"),
                   cc.scope_label(header.get("scope"), root), owner_subject)
        text = (rule.get("body") or "").strip().rstrip(".")
        tag = f"{number}, {status}" + (f" {when}" if when else "")
        lines.append(f'"{text}", per how {name} {how} ({tag})')
    return {"lines": lines, "refused": refused}


# ---------------------------------------------------------------------------
# people-crm: what do I know about how [name] works
# ---------------------------------------------------------------------------

def craft_inventory(workspace_root, subject_id, *, entities=None) -> dict:
    """Every craft file about one subject, for the owner to read: scope,
    status, confidence, last test, open questions and rule count. Lists
    proposed and superseded files too, marked; a file that fails the shape
    is listed with its problem. Returns {"subject_id", "subject_name",
    "files": [...]}."""
    root = Path(workspace_root)
    idx = _index(root, entities)
    ents = idx.ents
    order = _scope_order(root)
    rows = []
    for folder in idx.folders(subject_id):
        for path in _md_files(folder, root):
            head = cc.parse_craft(_safe_read(path))["header"]
            if head.get("subject") != subject_id:
                continue
            got = _read_file(root, path, ents)
            parsed = got["parsed"] or {}
            sections = parsed.get("sections") or {}
            tested, verdict = head.get("tested"), head.get("verdict")
            rows.append({
                "id": _rel(root, path),
                "scope": head.get("scope"),
                "scope_label": cc.scope_label(head.get("scope"), root),
                "status": head.get("status"),
                "confidence": head.get("confidence"),
                "last_test": (f"tested {tested}, verdict {verdict}"
                              if tested else "never tested"),
                "open_questions": [it["body"] for it in
                                   sections.get("Open questions", [])],
                "rule_count": len(parsed.get("rules") or []),
                "consent": parsed.get("consent"),
                "problem": got["reason"],
            })
    rows.sort(key=lambda r: (order.get(r["scope"], len(order)), r["id"]))
    return {"subject_id": subject_id,
            "subject_name": idx.name(subject_id),
            "files": rows}


_LIST_RE = re.compile(
    r"^\s*(?:what\s+do\s+i\s+know\s+about\s+how|how\s+does)\s+"
    r"(?P<name>.+?)\s+works?\s*[?.]?\s*$", re.IGNORECASE)
_APPLY_RE = re.compile(
    r"^\s*apply\s+(?P<name>.+?)(?:['’]s|['’])\s+"
    r"(?:method|way|approach)\s+to\s+(?:this|that|it)\b", re.IGNORECASE)


#: Words that name the thing in front of the owner, never a person.
_GENERIC_NAMES = frozenset({
    "this", "it", "that", "these", "those", "they", "them", "everything",
    "anything", "something", "all", "this one", "that one", "the thing",
})
#: The product and its surfaces: a question about them is a question about
#: how the product works, never about how a person or a business works.
_PRODUCT_NAMES = frozenset({
    "command room", "hub", "cockpit", "morning brief", "brief", "plate",
    "needs your call", "end of day", "friday wrap", "weekly recap",
    "people crm", "craft", "craft file", "craft files", "plugin",
    "maintenance", "the system", "system",
})
_LEADING_WORDS = re.compile(r"^(?:the|my|our|your|this|that)\s+", re.I)


def _plain_name(name: str) -> str:
    text = " ".join(str(name or "").strip().strip("?.!,;:").split()).lower()
    while True:
        shorter = _LEADING_WORDS.sub("", text)
        if shorter == text:
            return text
        text = shorter


def _not_a_subject(name: str, brain_name: Optional[str] = None) -> bool:
    plain = _plain_name(name)
    if not plain or name.strip().lower() in _GENERIC_NAMES \
            or plain in _GENERIC_NAMES or plain in _PRODUCT_NAMES:
        return True
    if brain_name and plain == _plain_name(brain_name):
        return True
    return False


def _resolve_subjects(workspace_root, name: str) -> list:
    """Person and business ids the name resolves to, through the canonical
    resolver, in its order. Projects and proposals are not subjects here."""
    try:
        from entity_resolve import resolve_all
        found = resolve_all(workspace_root, name)
    except Exception:  # noqa: BLE001 - an unreadable record resolves nothing
        return []
    out = []
    for hit in found:
        rid = (hit.record or {}).get("id")
        if hit.entity_type in ("person", "org") and rid and rid not in out:
            out.append(rid)
    return out


def match_craft_verb(text, *, workspace_root=None) -> dict:
    """Which craft verb an ask is, and the name it carries:
    {"verb": "list"|"apply"|None, "name": str|None, "subject_ids": [...]}.
    `how does [name] work` and `what do I know about how [name] works`
    list; `apply [name]'s method to this` applies. Anything else is None:
    `who is [name]`, `prep me for [name]`, and a question about the product
    or the thing in front of the owner ("how does this work", "how does the
    Hub work", "how does Command Room work"), because the name must be a
    person or a business. With `workspace_root` the name must also resolve
    to a person or a business on file, through the canonical resolver;
    `subject_ids` holds what it resolved to."""
    none = {"verb": None, "name": None, "subject_ids": []}
    if not isinstance(text, str):
        return none
    m = _APPLY_RE.match(text)
    verb = "apply" if m else None
    if m is None:
        m = _LIST_RE.match(text)
        verb = "list" if m else None
    if m is None:
        return none
    name = m.group("name").strip()
    brain_name = None
    if workspace_root is not None:
        ws = (cc.load_entities(workspace_root).get("workspace") or {})
        brain_name = ws.get("brain_name") if isinstance(ws, dict) else None
    if _not_a_subject(name, brain_name):
        return none
    subject_ids: list = []
    if workspace_root is not None:
        subject_ids = _resolve_subjects(workspace_root, name)
        if not subject_ids:
            return none
    return {"verb": verb, "name": name, "subject_ids": subject_ids}


__all__ = [
    "TASK_SCOPES", "TASK_LEADS", "NO_METHOD_LINES", "subject_folders", "craft_for_task", "cite_rules",
    "craft_inventory", "match_craft_verb", "craft_signature",
]
