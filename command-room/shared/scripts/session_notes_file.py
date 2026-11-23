#!/usr/bin/env python3
"""One name for a project's session notes (cr1#1580; ruling 1 of cr1#1579).

THE RULE. A project's session notes live at the root of its folder as
`SESSION_NOTES_<folder name>.md`: the prefix plus the folder's own name,
exactly as spelled (spaces and capitals kept, surrounding blanks dropped). A
project folder can be a nested path (`references/DATA_CONTRACT.md`,
`folder_name`); the name then uses the folder's own last segment, so the file
always sits inside the folder it names.

WHY ONE MODULE. Six naming rules were in use (the owner's first name, a
project label, two placeholders, an undefined token, the folder name), and two
readers took only one shape, so a project's notes could split across two
files with no warning. Every writer asks this module where to write
(`write_target`, or `notes_file` through the read door from a skill); every
reader asks it which file to read (`find_notes`, `reading_order`). The guard
in `tests/run_session_notes_name_test.py` fails when a skill, reference or
template names any other pattern, or when code outside this module spells a
notes file name or glob of its own.

OLDER SPELLINGS ARE STILL READ, AND NOTHING IS RENAMED. A folder whose only
live notes file has an older spelling keeps it: readers read it and writers
append to it. When a folder holds two live notes files, readers prefer the
ruled name and `two_notes_files` reports the folder for the tidy pile
(cr1#1584), whose press merges them into the ruled name with Undo. Nothing in
this module moves, renames, merges or writes a file.

WHAT COUNTS. A notes file is any `.md` at the folder's root whose name starts
with the prefix (any case). It is LIVE unless its name has one of the shapes
this product writes for something else (`_archive_<YYYY>.md`, `_index.md`,
the swept sidecar `.swept.md` / `.swept.rejected.md`, the scaffold template
`SESSION_NOTES_TEMPLATE.md`; a word such as "Archive" inside a name is not
one), or it is a sync conflict copy of a sibling
(`sync_conflict_copies.copy_tag`): those belong to the rollover and to the
sync sweep. The folder's own ruled-name file is always live, even when the
folder's name happens to contain one of those marks ("Archive Review"), and it
is never taken for a conflict copy of an older file.

Standard library only at import time, so the dependency-light integrity check
can use it; the conflict-copy rule is borrowed lazily.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional

_HERE = Path(__file__).resolve().parent

#: The one prefix every notes file carries. Code elsewhere spells a notes
#: name or glob through these constants, never through a literal of its own.
PREFIX = "SESSION_NOTES"
SUFFIX = ".md"
#: The root-of-folder glob for the whole notes family (live, archive, index,
#: sidecar), for the walkers that search a tree.
GLOB = PREFIX + "*" + SUFFIX


# ---------------------------------------------------------------------------
# The name
# ---------------------------------------------------------------------------

def _segment(folder: str) -> str:
    """The last segment of a folder name or workspace-relative path."""
    text = str(folder).replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1].strip()


def _disk_label(folder: Path) -> Optional[str]:
    """The folder's name as the disk spells it, when it exists: on a
    case-insensitive disk a record can say `acme` for the folder `Acme`."""
    seg = folder.name
    if not seg:
        return None
    try:
        names = os.listdir(folder.parent)
    except OSError:
        return None
    if seg in names:
        return seg
    folded = seg.casefold()
    for name in names:
        if name.casefold() == folded:
            return name
    return None


def folder_label(folder) -> str:
    """The folder's own name, exactly as spelled. An absolute path that
    exists answers with the disk's spelling; anything else with the last
    segment as given."""
    p = Path(folder)
    label = _disk_label(p) if p.is_absolute() else None
    if label is None:
        label = _segment(str(folder))
    return label.strip()


def ruled_name(folder) -> str:
    """`SESSION_NOTES_<folder name>.md` for `folder` (a name, a
    workspace-relative path or an absolute path). Empty when the folder has
    no name to give."""
    label = folder_label(folder)
    return f"{PREFIX}_{label}{SUFFIX}" if label else ""


def is_notes_name(name: str) -> bool:
    """True for any notes-family file name: the prefix (any case) and `.md`."""
    return name.upper().startswith(PREFIX) and name.lower().endswith(SUFFIX)


def _same_name(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.casefold() == b.casefold()


def is_ruled(path) -> bool:
    """True when `path` is its folder's ruled-name notes file."""
    p = Path(path)
    return _same_name(p.name, ruled_name(p.parent))


#: The only names this product gives a notes-family file that is not the live
#: notes file, in any case: the rollover's yearly archive and its index
#: (`cleanup_actions._sibling`), the sweep's sidecar and its rejected-residue
#: file (`session_narrative`), and the scaffold template. A word inside a name
#: is not one of these shapes: `SESSION_NOTES_Archive Migration.md` is a live
#: older spelling (cr1#1580 review round 1), where a fence on the word made
#: the door create a second file beside it.
_NOT_LIVE_RE = re.compile(r"(_archive_\d{4}\.md|_index\.md)$|\.swept\.", re.IGNORECASE)
_TEMPLATE_NAME = PREFIX + "_TEMPLATE" + SUFFIX


def _marked_not_live(name: str) -> bool:
    if name.casefold() == _TEMPLATE_NAME.casefold():
        return True
    return bool(_NOT_LIVE_RE.search(name))


def is_live_name(path) -> bool:
    """True when `path` names a notes file that is not an archive, an index,
    the template or a swept sidecar. The folder's own ruled-name file is
    always live. (A sync conflict copy is told apart only beside its
    siblings: see `live_notes`.)"""
    p = Path(path)
    if not is_notes_name(p.name):
        return False
    if is_ruled(p):
        return True
    return not _marked_not_live(p.name)


# ---------------------------------------------------------------------------
# The files in one folder
# ---------------------------------------------------------------------------

def _copy_tag() -> Optional[Callable[[str, str], Optional[str]]]:
    try:
        if str(_HERE) not in sys.path:
            sys.path.insert(0, str(_HERE))
        from sync_conflict_copies import copy_tag
    except Exception:  # noqa: BLE001 - a reader never fails on a borrowed rule
        return None
    return copy_tag


def notes_family(folder) -> List[Path]:
    """Every notes-family file at the folder's root (live, archive, index,
    template, sidecar, conflict copy), sorted by name."""
    f = Path(folder)
    try:
        names = sorted(os.listdir(f))
    except OSError:
        return []
    return [f / n for n in names if is_notes_name(n) and (f / n).is_file()]


def has_any_notes(folder) -> bool:
    """True when the folder holds any notes-family file at all. An archive or
    index alone still proves notes existed (never scaffold over history)."""
    return bool(notes_family(folder))


def live_notes(folder) -> List[Path]:
    """The folder's live notes files: the ruled name first, then any older
    spelling by name. A sync conflict copy of a sibling is left to the sync
    sweep, and the ruled-name file is never taken for one."""
    family = notes_family(folder)
    live = [p for p in family if is_live_name(p)]
    tag = _copy_tag()
    if tag is not None and len(live) > 1:
        names = [p.name for p in live]
        live = [p for p in live
                if is_ruled(p) or not any(tag(other, p.name) is not None
                                          for other in names if other != p.name)]
    live.sort(key=lambda p: (not is_ruled(p), p.name))
    return live


def find_notes(folder) -> Optional[Path]:
    """The notes file a reader reads: the ruled name when it exists, else the
    one older spelling, else (two older spellings) the first by name. None
    when the folder has no live notes file."""
    live = live_notes(folder)
    return live[0] if live else None


def write_target(folder) -> Optional[Path]:
    """The notes file a writer appends to: the file `find_notes` reads, so
    reader and writer never split; in a folder with none, the ruled name.
    None only when the folder has no name to give."""
    found = find_notes(folder)
    if found is not None:
        return found
    name = ruled_name(folder)
    return Path(folder) / name if name else None


def reading_order(folder) -> List[Path]:
    """Every notes-family file, in the order a narrative reader takes them:
    the live files first (the ruled name leading), then the rest by name."""
    live = live_notes(folder)
    return live + [p for p in notes_family(folder) if p not in live]


# ---------------------------------------------------------------------------
# The read door's answer, for a skill
# ---------------------------------------------------------------------------

def _inside(root: Path, folder) -> Optional[str]:
    """`folder` as a workspace-relative posix path, or None when it is empty
    or points outside the workspace."""
    if folder is None or not str(folder).strip():
        return None
    raw = Path(str(folder).strip())
    target = raw if raw.is_absolute() else root / raw
    try:
        rel = target.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    text = rel.as_posix()
    return None if text in ("", ".") else text


def notes_file(workspace_root, folder=None) -> dict:
    """Where a project's session notes are read and appended. Writes nothing.

    `folder` is the project's folder, relative to the workspace (a nested
    path is fine). The answer:
      ok            False when the folder is missing or outside the workspace
      path          the file to read and to append to, workspace-relative
      exists        whether that file is there yet (False: create it there)
      ruled_name    `SESSION_NOTES_<folder name>.md` for this folder
      older_spelling True when `path` is an older spelling kept as it is
      other_notes   any further live notes files in the folder (the tidy
                    pile offers the merge; never merge or rename here)
    """
    root = Path(workspace_root)
    rel = _inside(root, folder)
    if rel is None:
        return {"ok": False, "reason": "no_folder", "folder": folder}
    f = root / rel
    if not f.is_dir():
        return {"ok": False, "reason": "no_folder", "folder": rel}
    ruled = ruled_name(f)
    live = live_notes(f)
    target = live[0] if live else f / ruled
    return {
        "ok": True,
        "folder": rel,
        "path": f"{rel}/{target.name}",
        "exists": bool(live),
        "ruled_name": ruled,
        "older_spelling": bool(live) and not is_ruled(target),
        "other_notes": [f"{rel}/{p.name}" for p in live[1:]],
    }


# ---------------------------------------------------------------------------
# The detector for the "two notes files" pile (the pile itself is cr1#1584's)
# ---------------------------------------------------------------------------

def _thread_folders(root: Path) -> List[str]:
    try:
        if str(_HERE) not in sys.path:
            sys.path.insert(0, str(_HERE))
        from entities_io import entities_collection, read_entities_json
        data = read_entities_json(root / "_hq" / "data" / "entities.json")
        threads = entities_collection(data, "threads")
    except Exception:  # noqa: BLE001 - no register, no thread folders
        return []
    out = []
    for t in threads or []:
        if isinstance(t, dict) and t.get("folder_name"):
            rel = _inside(root, t.get("folder_name"))
            if rel:
                out.append(rel)
    return out


def _project_folders(root: Path) -> List[str]:
    """Each project folder once. Register rows that differ only in case name
    one folder on a case-blind disk (review round 1, cr1#1624), so they are
    folded together; the disk's own spelling wins."""
    found: dict = {}
    try:
        for d in root.iterdir():
            if d.is_dir() and not d.name.startswith((".", "_")):
                found.setdefault(d.name.casefold(), d.name)
    except OSError:
        pass
    for rel in _thread_folders(root):
        found.setdefault(rel.casefold(), rel)
    return sorted(rel for rel in found.values() if (root / rel).is_dir())


def project_folders(workspace_root) -> List[Path]:
    """READ. Every project folder the notes rule looks at: each top-level
    folder (not a dot- or `_`-folder) and each thread's `folder_name` on the
    register, nested ones included (cr1#1624: the sync sweep walks these, so
    a nested project's notes copy is found). Never every folder on disk."""
    root = Path(workspace_root)
    return [root / rel for rel in _project_folders(root)]


#: cr1#1624: the folders kept as archives, backups or quarantine, by exact
#: name in any case: the product's own (`_archive`, `_backups`,
#: `session-notes-archive`, `quarantine`) and an owner folder named exactly
#: "archive", "backup" or "backups" at any depth (review round 1, the
#: coordinator's decision, as `deliverable_sweep` and `doc_shelf` already
#: fence them). Only these and a dot-folder keep a notes file out of the
#: rollover and the pending-sidecar count. A folder whose name merely
#: contains such a word ("Archive Migration 2026", "Backup Plans") is live
#: there, as its notes file is to every reader: the shape rule (cr1#1580)
#: applied to folders, never a substring of a folder's name.
NOT_LIVE_DIRS = frozenset({"_archive", "_backups", "session-notes-archive", "quarantine",
                           "archive", "backup", "backups"})


def in_live_folder(parts) -> bool:
    """Whether a notes file whose folder parts (relative to the workspace,
    the file's own name left out) are `parts` sits in a live folder: no part
    is a dot-folder or one of `NOT_LIVE_DIRS`."""
    for part in parts:
        low = str(part).lower()
        if low.startswith(".") or low in NOT_LIVE_DIRS:
            return False
    return True


def two_notes_files(workspace_root) -> List[dict]:
    """Every project folder holding more than one live notes file: the rows
    of the "two notes files" tidy pile. Read only. Each row names the folder,
    the ruled name, whether a file by that name is already there, the file
    readers read today (`keep`) and every live notes file in the folder. The
    pile, its one press (merge into the ruled name) and its Undo are
    cr1#1584's; this never merges or renames."""
    root = Path(workspace_root)
    rows = []
    for rel in _project_folders(root):
        f = root / rel
        live = live_notes(f)
        if len(live) < 2:
            continue
        rows.append({
            "folder": rel,
            "ruled_name": ruled_name(f),
            "ruled_exists": any(is_ruled(p) for p in live),
            "keep": f"{rel}/{live[0].name}",
            "files": [f"{rel}/{p.name}" for p in live],
        })
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Where a project's session notes are read and written.")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--folder", help="the project's folder, workspace-relative")
    ap.add_argument("--two-notes-files", action="store_true",
                    help="list folders holding more than one notes file")
    args = ap.parse_args(argv)
    if args.two_notes_files:
        print(json.dumps(two_notes_files(args.workspace), indent=2))
    else:
        print(json.dumps(notes_file(args.workspace, args.folder), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
