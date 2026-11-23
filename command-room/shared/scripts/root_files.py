#!/usr/bin/env python3
"""Loose files at a project's root: the detector, the move into deliverables/, and its Undo (cr1#1584).

A project folder keeps three of the product's own files at its root (the
context, the brain, the session notes) and puts the owner's work in
subfolders. A file of the owner's lying beside them is a loose end. The
Hub's Tidy pile "Loose files at a project root" lists them (integrity code
C21, `C21.loose_root_file`; C12 to C20 are taken) and has one press: move
each into the project's `deliverables/` folder. This module is that
detector, that press's writer and its Undo.

ARCHIVE FIRST, NEVER A DELETE, NEVER A CLOBBER.
  * Before a file moves, one record naming the press, where it was, where
    it goes, its size and its modification time is appended to
    `_archive/root-moves/manifest.jsonl`. Then ONE `os.replace` moves it. A
    name already taken in `deliverables/` gets a numeric suffix; nothing is
    overwritten.
  * `put_back` takes one press's move back from that record, only while the
    moved file is still the one that moved (same size and time) and its old
    place is free; otherwise it says so and moves nothing.

Read only (`loose_root_files`) apart from the two writers.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

CODE = "C21.loose_root_file"
DELIVERABLES = "deliverables"
LOG_REL = "_archive/root-moves/manifest.jsonl"
#: The stems of the product's own files at a project's root. A name that
#: begins with one is the product's: the file itself, a sync conflict copy
#: (`PROJECT_BRAIN-OFFICE.md`), a local twin (`CLAUDE.local.md`) or a backup
#: a release step keeps beside it for its rollback (`.pre-migrate1`).
OWN_STEMS = ("project_context", "project_brain", "claude")
#: The computer's own files.
OS_FILES = frozenset({"desktop.ini", "thumbs.db", "icon\r"})


def _is_own(name: str) -> bool:
    import session_notes_file as snf

    low = name.lower()
    return (low.startswith(OWN_STEMS) or low.startswith(snf.PREFIX.lower()) or snf.is_notes_name(name)
            or low in OS_FILES or name.startswith((".", "~$")) or low.endswith(".meta.json"))


def _project_folders(root: Path) -> List[Dict[str, str]]:
    from entities_io import entities_collection, read_entities_json

    out = []
    for t in entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"), "projects"):
        if not isinstance(t, dict) or str(t.get("status") or "").lower() == "archived":
            continue
        rel = str(t.get("folder_name") or "").replace("\\", "/").strip("/")
        if not rel or rel.startswith(("_", ".")) or ".." in rel.split("/"):
            continue
        if (root / rel).is_dir():
            out.append({"id": str(t.get("id") or ""), "under": rel,
                        "name": str(t.get("canonical_name") or t.get("display_name") or rel)})
    return out


def loose_root_files(workspace_root) -> List[Dict[str, str]]:
    """READ. `[{id, name, under, project}]`, each file of the owner's at
    the root of a live project's folder (`id` its workspace-relative path)."""
    root = Path(str(workspace_root))
    out: List[Dict[str, str]] = []
    seen = set()
    for p in _project_folders(root):
        if p["under"] in seen:
            continue
        seen.add(p["under"])
        try:
            entries = sorted(os.scandir(root / p["under"]), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_file(follow_symlinks=False) or _is_own(e.name):
                    continue
            except OSError:
                continue
            out.append({"id": f"{p['under']}/{e.name}", "name": e.name, "under": p["under"], "project": p["name"]})
    return out


def _move(src: str, dst: str) -> None:
    """The one move this module makes: one `os.replace`."""
    os.replace(src, dst)


def _free(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, n = path.stem, path.suffix, 2
    while (path.parent / f"{stem} ({n}){suffix}").exists():
        n += 1
    return path.parent / f"{stem} ({n}){suffix}"


def _records(root: Path) -> List[Dict[str, Any]]:
    try:
        lines = (root / LOG_REL).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def move_to_deliverables(workspace_root, rel: str, *, press: str) -> Dict[str, Any]:
    """A WRITER. Move one loose file into its project's `deliverables/`,
    archive first (the module docstring). Answers `{status, moved_to}`:
    `moved`, or `not_loose` (it is no longer a loose root file; nothing
    moved)."""
    from atomic_write import atomic_append_jsonl

    root = Path(str(workspace_root))
    loose = {r["id"]: r for r in loose_root_files(root)}
    row = loose.get(str(rel or ""))
    if row is None or not press:
        return {"status": "not_loose"}
    src = root / row["id"]
    dst_dir = root / row["under"] / DELIVERABLES
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = _free(dst_dir / row["name"])
    st = src.stat()
    to = dst.relative_to(root).as_posix()
    atomic_append_jsonl(root / LOG_REL, [{"press": str(press), "from": row["id"], "to": to, "size": st.st_size,
                                         "mtime_ns": st.st_mtime_ns, "at": _dt.datetime.now(_dt.timezone.utc).isoformat()}])
    _move(str(src), str(dst))
    return {"status": "moved", "moved_to": to}


def put_back(workspace_root, press: str) -> Dict[str, Any]:
    """A WRITER. The Undo of one press's move, from its record. Answers
    `{status}`: `restored`; `changed` (nothing moved) when the moved file
    is not the one that moved or its old place is taken; `busy` when the
    computer refused the move (nothing recorded, so it can be pressed
    again); `already` when it was put back; `not_found` for no such move."""
    from atomic_write import atomic_append_jsonl

    root = Path(str(workspace_root))
    recs = [r for r in _records(root) if r.get("press") == str(press or "")]
    if not recs or not press:
        return {"status": "not_found"}
    if any(r.get("undone") for r in recs):
        return {"status": "already"}
    r = next(x for x in recs if not x.get("undone"))
    src, dst = root / str(r.get("to") or ""), root / str(r.get("from") or "")
    try:
        st = src.stat()
    except OSError:
        return {"status": "changed"}
    if st.st_size != r.get("size") or st.st_mtime_ns != r.get("mtime_ns") or dst.exists():
        return {"status": "changed"}
    # the move first, the record after it landed: a move the computer
    # refuses (the file is open) leaves the Undo free to be pressed again
    try:
        _move(str(src), str(dst))
    except OSError:
        return {"status": "busy"}
    atomic_append_jsonl(root / LOG_REL, [{"press": str(press), "undone": True,
                                         "at": _dt.datetime.now(_dt.timezone.utc).isoformat()}])
    return {"status": "restored"}


__all__ = ["CODE", "loose_root_files", "move_to_deliverables", "put_back"]
