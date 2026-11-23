#!/usr/bin/env python3
"""A project the owner makes gets its folder in the same act (cr1#1582;
ruling 3 of cr1#1579).

THE RULING. A project the OWNER creates (chat "new project", the Hub's New
work stream, onboarding's project build) gets its folder in the same act,
and the project's record carries it (`folder_name`). A project a BACKGROUND
job creates (a thread split, a project seen in a connector, the sync-copy
sweep) stays folderless until the owner presses "Make its folder" on the
Hub's tidy pile (cr1#1584), which calls `make_project_folders` here.
FOLDERGUARD's intent stands: nothing in the background invents a folder.
This module is the one place a project folder is made, and only an owner's
act reaches it: `tests/run_project_folder_test.py` fences which modules
import it and which skills name it.

THE FOLDER. At the workspace root, named as the project is named, unless
the owner names another folder (one already there, at any depth, or one to
make at the root). A folder already there is matched and recorded, never
duplicated: whatever its case, at the root, or one level down when exactly
one of that name is there (two or more, and the owner is asked which). A
folder another live project records is never shared: the owner is asked to
give this one its own. A name that cannot be a folder name on Windows or on
a Mac is refused with a plain line, so the owner is asked for another.
Nothing is guessed, and a refusal writes nothing (`folder_check`).

ORDER (a known unknown of cr1#1582, decided here). The folder is checked
first (`folder_check`); then the project record, whose writer refuses a
duplicate or a missing org before it writes anything and is told not to
guess a folder from the name (`guess_folder=False`); then the folder is made
or matched and recorded on the project. A folder is never left without its
project: when recording it fails, a folder this call made is removed again.
When the folder step fails after the record landed, the record stays and
the answer says so; the project is then one more project with no folder,
which the owner's "Make its folder" finishes.

UNDO. `undo_create_project` archives a project `create_project` started
(`thread_archive.archive_thread`, the one archive path), with or without its
folder, and takes its folder back; `take_back_folder` is that second half,
also used by the Hub's two Undos of a new work stream. Only a folder this
module MADE is taken back: an empty one is removed, and one with anything in
it is moved whole, archive-first, under `_archive/project-undo-<date>/`. A
folder that was matched, or that another live project records (itself or a
folder inside it), is never touched.

THE NOTES FILE NAME (cr1#1580). The files a new folder gets (notes,
context, brain) are written by the skill that made the project, through the
door's text write, now that the folder exists. The notes file is the one
`session_notes_file` names for the recorded folder (`notes_file` through
the read door: the ruled name, the folder's own name after the prefix);
nothing here spells one, and nothing here writes one.

Writers: `create_project`, `make_project_folders`, `undo_create_project`
(the door's run_writer list). Read: `plan_folder` (the door's helper list).
`folder_check`, `make_project_folder` and `take_back_folder` are the
in-process halves the Hub's presses and Undos call.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: the longest folder name taken (the Hub's work-stream name cap)
NAME_CAP = 80
#: characters no folder name may hold on Windows (`/` and `:` on a Mac too)
_BAD_CHARS = '<>:"/\\|?*'
#: names Windows keeps for devices, with or without an extension
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"}
                      | {f"COM{i}" for i in range(1, 10)}
                      | {f"LPT{i}" for i in range(1, 10)})

EVENT_MADE = "project_folder_made"
EVENT_TAKEN_BACK = "project_folder_taken_back"
#: where a taken-back folder with anything in it is kept, archive-first
UNDO_BUCKET = "project-undo-{date}"
_ARCHIVE_ROOT = "_archive"
#: thread statuses that take no folder
_CLOSED = ("archived", "resolved")

BAD_NAME_LINE = ("\"{name}\" cannot be a folder name on Windows or a Mac: {why}. "
                 "Nothing was written; give it a name without that.")
MADE_LINE = "Made the folder {folder} for {project}."
MATCHED_LINE = "{project} now uses the folder {folder}, which was already there."
ALREADY_LINE = "{project} already has its folder, {folder}."
NOT_FOUND_LINE = "That project is not on your book; nothing was written."
CLOSED_LINE = "{project} is {status}, so it gets no folder; nothing was written."
STALE_LINE = ("{project}'s record names the folder {folder}, which is not there; "
              "nothing was made. Find or re-link that folder first.")
IN_THE_WAY_LINE = ("A file called {folder} is in the way, so the folder could not be "
                   "made; nothing was written. Give it another name.")
NOT_MADE_LINE = "The folder {folder} could not be made; nothing was written."
NOT_RECORDED_LINE = ("The folder {folder} could not be recorded on {project}, so it was "
                     "not kept; nothing was written.")
CREATED_LINE = "Started {project}, with its folder {folder}."
CREATED_NO_FOLDER_LINE = ("Started {project}, but its folder could not be made: {why}. It has "
                          "no folder yet; give it another folder name, or clear the way and ask again.")
AMBIGUOUS_LINE = ("More than one folder called {name} is already here ({where}); nothing was "
                  "written. Say which one is {name}'s.")
TAKEN_LINE = ("The folder {folder} is already {other}'s; nothing was written. Give this one "
              "its own folder name.")
TAKEN_BACK_SHARED_LINE = "Its folder {folder} is also {other}'s, so it stays."
TAKEN_BACK_HOLDS_LINE = "Its folder {folder} holds {other}'s folder, so it stays."
TAKEN_BACK_REMOVED_LINE = "The empty folder {folder} made for it is removed."
TAKEN_BACK_ARCHIVED_LINE = ("The folder {folder} made for it had something in it, so it was "
                            "moved whole to {kept_as}, not removed.")
TAKEN_BACK_KEPT_LINE = "Its folder {folder} was already there before, so it stays."
UNDONE_LINE = "{project} is taken back and archived."
NOT_MINE_LINE = ("{project} was not started with its folder here, so it is not taken "
                 "back; archive it instead. Nothing was written.")


# ---------------------------------------------------------------------------
# the name
# ---------------------------------------------------------------------------

def name_problem(name: Any) -> Optional[str]:
    """None when `name` can be a folder name on Windows and on a Mac, else
    the reason in plain words. Outer spaces are not part of the name."""
    text = "" if name is None else str(name).strip()
    if not text:
        return "it has no letters in it"
    if len(text) > NAME_CAP:
        return f"it is longer than {NAME_CAP} characters"
    bad = sorted({c for c in text if c in _BAD_CHARS})
    if bad:
        return "it has " + " ".join(f"\"{c}\"" for c in bad) + " in it"
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        return "it has a character that cannot be typed in it"
    if text.endswith("."):
        return "it ends with a full stop"
    if text.startswith("."):
        return "it starts with a full stop, which hides a folder on a Mac"
    if text.startswith("_"):
        return "it starts with an underscore, which Command Room keeps for its own folders"
    if text.split(".")[0].strip().upper() in _RESERVED:
        return "Windows keeps that name for itself"
    return None


def _root(workspace_root) -> Path:
    return Path(str(workspace_root))


def _now(now_iso: Optional[str]) -> datetime:
    if now_iso:
        try:
            dt = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _thread(root: Path, thread_id: str) -> Optional[dict]:
    import thread_writer as tw

    for t in tw._threads(tw._load_entities(root)):
        if isinstance(t, dict) and str(t.get("id") or "") == str(thread_id or ""):
            return t
    return None


def _project_name(t: dict) -> str:
    return str(t.get("canonical_name") or t.get("display_name") or t.get("id") or "").strip()


def _fold(s: Any) -> str:
    import thread_writer as tw

    return tw._fold(str(s or ""))


def _find_existing(root: Path, canonical_name: str, folder: Optional[str]) -> tuple:
    """`(folder, others)`: the folder already there for this project, or
    None. The one the owner named, as written at any depth; else the
    project's own name (or its slug) at the root (`thread_writer.
    resolve_folder_name`). A bare name (the owner's, or the project's own)
    found nowhere at the root is then looked for further down: exactly one
    folder of that name is used, so a folder already there is never doubled
    at the root (cr1#1582 review round 2). Two or more answer `(None,
    [them])`: which one is the owner's to say, never a guess. Case never
    matters."""
    import thread_writer as tw

    if folder:
        hit = tw._resolve_on_disk(root, folder)
        if hit or "/" in tw._fold(folder):
            return hit, []
        want = tw._fold(folder)
    else:
        hit = tw.resolve_folder_name(root, canonical_name, None)
        if hit:
            return hit, []
        want = tw._fold(canonical_name)
    below = [p for p in tw._candidate_folders(root)
             if "/" in p and tw._fold(p.rsplit("/", 1)[1]) == want]
    if len(below) == 1:
        return below[0], []
    return None, sorted(below)


def _holders(root: Path, folder: str, exclude: Iterable[str], *, under: bool = False) -> List[dict]:
    """The live projects (not archived or resolved) that record `folder`,
    whatever its case, other than `exclude`. `under` counts a project whose
    folder is inside `folder` too: taking a folder back moves everything in
    it, so a live project recorded anywhere inside keeps it (cr1#1582 review
    round 2)."""
    import thread_writer as tw

    skip = {str(x) for x in exclude or ()}
    key = _fold(folder)
    out = []
    for t in tw._threads(tw._load_entities(root)):
        if not isinstance(t, dict) or str(t.get("id") or "") in skip:
            continue
        if str(t.get("status") or "active").strip().lower() in _CLOSED:
            continue
        mine = _fold(t.get("folder_name")) if t.get("folder_name") else ""
        if mine and (mine == key or (under and mine.startswith(key + "/"))):
            out.append(t)
    return out


def folder_check(workspace_root, name: Any, folder: Any = None,
                 exclude: Iterable[str] = ()) -> Dict[str, Any]:
    """READ. What making the folder for a project called `name` would do,
    before anything is written: `{ok: True, how: "match" | "new",
    folder_name}`, or `{ok: False, reason, line}` where `reason` is
    `bad_name` (a folder to be made whose name Windows or a Mac cannot hold),
    `ambiguous` (two or more of that name one level down: the owner says
    which, `folders` lists them) or `taken` (the folder another live project
    records, `holder` names it: the owner gives this one its own). `folder`
    is the folder the owner named instead of the project's own name: one
    already there is used at any depth; one that is not is made at the root.
    `exclude` are the projects the folder may already belong to (the project
    itself, a work stream's own first thread). Never raises."""
    root = _root(workspace_root)
    name = str(name or "").strip()
    given = str(folder).strip() if folder not in (None, "") else ""
    try:
        if given:
            hit, others = _find_existing(root, name, given)
            if hit is None:
                why = name_problem(given)
                if why:
                    return {"ok": False, "reason": "bad_name",
                            "line": BAD_NAME_LINE.format(name=given, why=why)}
        else:
            why = name_problem(name)
            if why:
                return {"ok": False, "reason": "bad_name",
                        "line": BAD_NAME_LINE.format(name=name, why=why)}
            hit, others = _find_existing(root, name, None)
        if others:
            return {"ok": False, "reason": "ambiguous", "folders": others,
                    "line": AMBIGUOUS_LINE.format(name=name, where=", ".join(others))}
        if hit:
            held = _holders(root, hit, exclude)
            if held:
                other = _project_name(held[0])
                return {"ok": False, "reason": "taken", "holder": other, "folder_name": hit,
                        "line": TAKEN_LINE.format(folder=hit, other=other)}
            return {"ok": True, "how": "match", "folder_name": hit}
        return {"ok": True, "how": "new", "folder_name": given or name}
    except Exception as exc:  # noqa: BLE001 - a read never fails its caller
        return {"ok": False, "reason": "unreadable",
                "line": f"The folders here could not be read ({type(exc).__name__}); nothing was written."}


# ---------------------------------------------------------------------------
# the read
# ---------------------------------------------------------------------------

def plan_folder(workspace_root, name: Any = None, folder: Any = None) -> Dict[str, Any]:
    """READ. `folder_check` with a line for every answer, so a skill can ask
    before it writes: `{ok: True, how: "match" | "new", folder_name, line}`,
    or `{ok: False, reason, line}`. Writes nothing; never raises."""
    got = folder_check(workspace_root, name, folder)
    if not got.get("ok"):
        return dict(got, folder_name=got.get("folder_name"))
    hit = got.get("folder_name")
    if got.get("how") == "match":
        return dict(got, line=f"The folder {hit} is already there, so it would be used.")
    return dict(got, line=f"A new folder {hit} would be made.")


# ---------------------------------------------------------------------------
# the folder for one project (the in-process half)
# ---------------------------------------------------------------------------

def _refuse(reason: str, line: str, **extra) -> Dict[str, Any]:
    out = {"ok": False, "outcome": "refused", "reason": reason, "line": line}
    out.update(extra)
    return out


def _log(root: Path, event_type: str, data: dict, source_skill: str) -> bool:
    from event_gate import append_event

    try:
        append_event(root / "_hq" / "data" / "events.jsonl",
                     {"type": event_type, "source_skill": source_skill, "data": data},
                     holder="project_folder")
        return True
    except Exception:  # noqa: BLE001 - the folder and record stand; the answer says so
        return False


def _remove_if_empty(path: Path) -> bool:
    try:
        path.rmdir()
        return True
    except OSError:
        return False


def make_project_folder(workspace_root, thread_id: str, *, source_skill: str,
                        folder: Optional[str] = None, with_project: bool = False,
                        also_thread_ids: Iterable[str] = (),
                        now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Make or match the folder for ONE project and record it on the
    project (`thread_writer.update_thread`, `folder_name`), with one
    `project_folder_made` row saying whether it was made. `also_thread_ids`
    are recorded with the same folder (the Hub's work stream and its own
    first thread). `folder_check` decides first: a name no folder can hold,
    two candidates, or a folder another live project records is refused
    with nothing written. Called only from an owner's act: the writers below
    and the Hub's New work stream presses. Answers `{ok, outcome: made |
    matched | already | refused, folder_name, made, line}`."""
    import thread_writer as tw

    root = _root(workspace_root)
    t = _thread(root, thread_id)
    if t is None:
        return _refuse("not_found", NOT_FOUND_LINE)
    project = _project_name(t)
    status = str(t.get("status") or "active").strip().lower()
    if status in _CLOSED:
        return _refuse("closed", CLOSED_LINE.format(project=project, status=status))
    also = [str(a) for a in also_thread_ids or () if a and str(a) != str(thread_id)]
    current = t.get("folder_name")
    if current:
        on_disk = tw._resolve_on_disk(root, str(current))
        if on_disk is None:
            return _refuse("stale", STALE_LINE.format(project=project, folder=current))
        try:
            for extra in also:
                rec = _thread(root, extra)
                if rec is not None and not rec.get("folder_name"):
                    tw.update_thread(root, extra, source_skill=source_skill, folder_name=on_disk)
        except Exception:  # noqa: BLE001 - the project itself has its folder
            pass
        if with_project:
            _log(root, EVENT_MADE, {"primary_thread_id": thread_id, "folder_name": on_disk,
                                    "made": False, "with_project": True, "also": also},
                 source_skill)
        return {"ok": True, "outcome": "already", "folder_name": on_disk, "made": False,
                "line": ALREADY_LINE.format(project=project, folder=on_disk)}
    plan = folder_check(root, project, folder, exclude=[thread_id] + also)
    if not plan.get("ok"):
        return _refuse(plan.get("reason") or "refused", plan.get("line") or NOT_MADE_LINE.format(folder=project),
                       **{k: plan[k] for k in ("folders", "holder") if k in plan})
    hit = plan.get("folder_name")
    made = False
    if plan.get("how") == "new":
        want = str(hit)
        target = root / want
        try:
            target.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            return _refuse("in_the_way", IN_THE_WAY_LINE.format(folder=want),
                           why=f"a file called {want} is in the way")
        except OSError:
            return _refuse("not_made", NOT_MADE_LINE.format(folder=want),
                           why="the disk did not let it be made")
        made = True
        hit = tw._resolve_on_disk(root, want)
        if hit is None:
            _remove_if_empty(target)
            return _refuse("not_made", NOT_MADE_LINE.format(folder=want),
                           why="the disk did not let it be made")
    try:
        tw.update_thread(root, thread_id, source_skill=source_skill, folder_name=hit)
        for extra in also:
            if _thread(root, extra) is not None:
                tw.update_thread(root, extra, source_skill=source_skill, folder_name=hit)
    except Exception:  # noqa: BLE001 - never a folder without its project
        if made:
            _remove_if_empty(root / hit)
        return _refuse("not_recorded", NOT_RECORDED_LINE.format(folder=hit, project=project),
                       why="it could not be recorded on the project")
    logged = _log(root, EVENT_MADE, {"primary_thread_id": thread_id, "folder_name": hit, "made": made,
                                     "with_project": bool(with_project), "also": also},
                  source_skill)
    line = (MADE_LINE if made else MATCHED_LINE).format(project=project, folder=hit)
    return {"ok": True, "outcome": "made" if made else "matched", "folder_name": hit, "made": made,
            "logged": logged, "line": line}


# ---------------------------------------------------------------------------
# the writers
# ---------------------------------------------------------------------------

def make_project_folders(workspace_root, thread_ids: Optional[List[str]] = None, *,
                         folders: Optional[Dict[str, str]] = None,
                         source_skill: str = "workspace-manager",
                         now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. The owner's "Make its folder", for one project or a pile:
    each project's folder made or matched and recorded (`make_project_folder`).
    `folders` maps a project to a folder the owner named instead of its own
    name (one already there, at any depth, or one to make at the root). One
    project refused never stops the rest. Answers `{ok, results: [{thread_id,
    outcome, folder_name, line}], line}`; `ok` is false when any was
    refused."""
    root = _root(workspace_root)
    ids = [str(t) for t in (thread_ids or []) if str(t or "").strip()]
    if not ids:
        return {"ok": False, "results": [], "reason": "nothing_named",
                "line": "No project was named, so nothing was written."}
    named = folders if isinstance(folders, dict) else {}
    results = []
    for tid in ids:
        try:
            got = make_project_folder(root, tid, source_skill=source_skill,
                                      folder=named.get(tid), now_iso=now_iso)
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the rest
            got = _refuse("error", f"The folder for {tid} could not be made: "
                                   f"{str(exc)[:160] or type(exc).__name__}.")
        results.append(dict(got, thread_id=tid))
    ok = all(r.get("ok") for r in results)
    return {"ok": ok, "results": results,
            "line": " ".join(r.get("line") or "" for r in results).strip()}


def create_project(workspace_root, *, canonical_name: str, org_id: str,
                   source_skill: str = "workspace-manager", folder: Optional[str] = None,
                   status: str = "active", kind: Optional[str] = None,
                   owner_person_id: Optional[str] = None,
                   stakeholder_person_ids: Optional[List[str]] = None,
                   parent_thread_id: Optional[str] = None,
                   now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. The owner's new project in one act. `folder_check` first: a
    name no folder can hold, two candidate folders, or a folder another live
    project records is refused with nothing written. Then the record through
    `thread_writer.create_thread` (its own dedup, org and schema checks), told
    not to guess a folder from the name (`guess_folder=False`: a same-named
    older folder is never taken for the one the owner named). Then its folder
    made or matched and recorded (`make_project_folder`). Answers `{ok,
    outcome: created | refused, thread, folder_name, made, line}`; a record
    whose folder could not be made is `ok` with `folder_name: None`, a line
    that says so, and a row that still lets its Undo take the record back."""
    import thread_writer as tw

    root = _root(workspace_root)
    name = str(canonical_name or "").strip()
    given = str(folder).strip() if folder not in (None, "") else None
    plan = folder_check(root, name, given)
    if not plan.get("ok"):
        return _refuse(plan.get("reason") or "refused", plan.get("line") or "",
                       **{k: plan[k] for k in ("folders", "holder") if k in plan})
    try:
        rec = tw.create_thread(root, canonical_name=name, status=status, org_id=org_id,
                               folder_name=None, guess_folder=False, kind=kind,
                               owner_person_id=owner_person_id,
                               stakeholder_person_ids=stakeholder_person_ids,
                               parent_thread_id=parent_thread_id, source_skill=source_skill)
    except ValueError as exc:
        return _refuse("not_created", f"{name} was not started: {str(exc)[:200]}. "
                                      "Nothing was written.")
    got = make_project_folder(root, rec["id"], source_skill=source_skill, folder=given,
                              with_project=True, now_iso=now_iso)
    rec = _thread(root, rec["id"]) or rec
    if not got.get("ok"):
        # the record stands without a folder; this row says the project was
        # started here, so its Undo can still take the record back
        _log(root, EVENT_MADE, {"primary_thread_id": rec["id"], "folder_name": None, "made": False,
                                "with_project": True, "also": []}, source_skill)
        return {"ok": True, "outcome": "created", "thread": rec, "folder_name": None, "made": False,
                "folder_reason": got.get("reason"),
                "line": CREATED_NO_FOLDER_LINE.format(
                    project=name, why=got.get("why") or "it could not be made there")}
    return {"ok": True, "outcome": "created", "thread": rec, "folder_name": got.get("folder_name"),
            "made": bool(got.get("made")),
            "line": CREATED_LINE.format(project=name, folder=got.get("folder_name"))}


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------

def _made_rows(root: Path, thread_id: str) -> tuple:
    """`(made row, taken back already, started here)`: the newest
    `project_folder_made` row for this project, whether a
    `project_folder_taken_back` row came after it, and whether any row says
    the project was started here (`with_project`)."""
    from events_io import load_events_org_scoped

    events, _skipped = load_events_org_scoped(root, drop_personal=False)
    made, back, started = None, False, False
    for ev in events:
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if str(d.get("primary_thread_id") or "") != str(thread_id):
            continue
        if ev.get("type") == EVENT_MADE:
            made, back = ev, False
            started = started or bool(d.get("with_project"))
        elif ev.get("type") == EVENT_TAKEN_BACK and made is not None:
            back = True
    return made, back, started


def _has_anything(path: Path) -> bool:
    try:
        return any(True for _ in os.scandir(path))
    except OSError:
        return True  # unreadable: never removed on a guess


def _archive_whole(root: Path, path: Path, now_iso: Optional[str]) -> Optional[str]:
    """Move `path` whole under `_archive/project-undo-<date>/`, never over
    another; one `os.replace` (the mount-safe move). The relative place it
    landed, or None."""
    bucket = UNDO_BUCKET.format(date=_now(now_iso).strftime("%Y-%m-%d"))
    dest_dir = root / _ARCHIVE_ROOT / bucket
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    dest = dest_dir / path.name
    n = 1
    while dest.exists():
        dest = dest_dir / f"{path.name}.{n}"
        n += 1
    try:
        os.replace(str(path), str(dest))
    except OSError:
        return None
    return dest.relative_to(root).as_posix()


def take_back_folder(workspace_root, thread_id: str, *, source_skill: str,
                     now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Undo's folder half: the folder `make_project_folder` MADE for this
    project is removed when empty and moved whole, archive-first, when
    anything is in it; the project (and any thread recorded with it) then
    carries no folder. Left alone: a matched folder, none made here, and a
    folder another live project records, or that holds a folder another
    live project records (whatever its case, outside this project and the
    threads recorded with it). Writes one
    `project_folder_taken_back` row when it moves or removes. Answers `{ok,
    outcome: removed | archived | gone | kept | none | already | refused,
    folder_name, line}`."""
    import thread_writer as tw

    root = _root(workspace_root)
    row, back, _started = _made_rows(root, thread_id)
    if row is None:
        return {"ok": True, "outcome": "none", "folder_name": None, "line": ""}
    d = row.get("data") or {}
    folder = str(d.get("folder_name") or "")
    if not folder:
        return {"ok": True, "outcome": "none", "folder_name": None, "line": ""}
    if back:
        return {"ok": True, "outcome": "already", "folder_name": folder, "line": ""}
    if not d.get("made"):
        return {"ok": True, "outcome": "kept", "folder_name": folder,
                "line": TAKEN_BACK_KEPT_LINE.format(folder=folder)}
    also = [str(a) for a in d.get("also") or []]
    held = _holders(root, folder, [str(thread_id)] + also, under=True)
    if held:
        same = [t for t in held if _fold(t.get("folder_name")) == _fold(folder)]
        line = (TAKEN_BACK_SHARED_LINE if same else TAKEN_BACK_HOLDS_LINE).format(
            folder=folder, other=_project_name((same or held)[0]))
        return {"ok": True, "outcome": "kept", "folder_name": folder, "line": line}
    path = root / folder
    kept_as = None
    if not path.is_dir():
        outcome = "gone"
    elif not _has_anything(path) and _remove_if_empty(path):
        outcome = "removed"
    else:
        kept_as = _archive_whole(root, path, now_iso)
        if kept_as is None:
            return _refuse("not_moved", f"The folder {folder} could not be moved to the "
                                        "archive, so it stays.", folder_name=folder)
        outcome = "archived"
    for tid in [str(thread_id)] + also:
        rec = _thread(root, tid)
        if rec is not None and _fold(rec.get("folder_name")) == _fold(folder):
            try:
                tw.update_thread(root, tid, source_skill=source_skill, folder_name=None)
            except Exception:  # noqa: BLE001 - the folder is back; the record says so next pass
                pass
    _log(root, EVENT_TAKEN_BACK, {"primary_thread_id": str(thread_id), "folder_name": folder,
                                  "outcome": outcome, "kept_as": kept_as,
                                  "reversal_of": row.get("seq")}, source_skill)
    if outcome == "archived":
        line = TAKEN_BACK_ARCHIVED_LINE.format(folder=folder, kept_as=kept_as)
    elif outcome == "removed":
        line = TAKEN_BACK_REMOVED_LINE.format(folder=folder)
    else:
        line = ""
    return {"ok": True, "outcome": outcome, "folder_name": folder, "kept_as": kept_as, "line": line}


def undo_create_project(workspace_root, thread_id: str, *,
                        source_skill: str = "workspace-manager",
                        now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. Takes back a project `create_project` started, with or
    without its folder: the project archived through
    `thread_archive.archive_thread` (the one archive path), then its folder
    taken back (`take_back_folder`). A project not started by
    `create_project` is refused with nothing written. Answers `{ok, outcome,
    folder_name, line}`."""
    import thread_archive as ta

    root = _root(workspace_root)
    t = _thread(root, thread_id)
    if t is None:
        return _refuse("not_found", NOT_FOUND_LINE)
    project = _project_name(t)
    _row, _back, started = _made_rows(root, thread_id)
    if not started:
        return _refuse("not_mine", NOT_MINE_LINE.format(project=project))
    ta.archive_thread(str(root), str(thread_id), reason="Taken back right after it was started",
                      source_skill=source_skill)
    back = take_back_folder(root, thread_id, source_skill=source_skill, now_iso=now_iso)
    line = " ".join(x for x in (UNDONE_LINE.format(project=project), back.get("line") or "") if x)
    return {"ok": bool(back.get("ok")), "outcome": "undone", "folder_name": back.get("folder_name"),
            "folder_outcome": back.get("outcome"), "line": line}


__all__ = ["NAME_CAP", "EVENT_MADE", "EVENT_TAKEN_BACK", "name_problem", "folder_check",
           "plan_folder", "make_project_folder", "make_project_folders", "create_project",
           "take_back_folder", "undo_create_project"]


if __name__ == "__main__":  # pragma: no cover - a quick read for a person
    print(json.dumps(plan_folder(sys.argv[1], *sys.argv[2:3]), indent=2))
