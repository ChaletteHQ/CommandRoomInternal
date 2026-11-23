#!/usr/bin/env python3
"""Merge a project folder's session-notes files into the ruled name, archive first, with Undo (cr1#1584).

The Hub's Tidy pile "Two notes files" (`hub_tidy`, its rows from
`session_notes_file.two_notes_files`, cr1#1580) has one press: merge the
folder's live notes files into the one ruled name,
`SESSION_NOTES_<folder name>.md` (ruling 1 of cr1#1579). This module is that
press's writer and its Undo, and the one rule for which files may merge
(`merge_parts`). It is the only code that merges notes files.

NEVER A SILENT RENAME, NEVER A DELETE, NEVER A CHANGED BYTE.
  * Which files: the folder's live notes files (`session_notes_file.
    live_notes`), less a sync conflict copy of the ruled name or of a file
    an earlier merge moved away (its sibling is gone, but it is still a
    copy, never notes of its own). Each part must decode as UTF-8 (a BOM
    allowed); a part that does not (an older code page, UTF-16) refuses the
    merge, `undecodable`, and the pile leaves the folder out.
  * The ruled file is the live file whose name is the ruled name in any
    case (`session_notes_file.is_ruled`: an older capitalised
    `SESSION_NOTES_SAMPLE FOLDER.md` is the same file), and the merge is
    written to that same path. With none, the ruled name is new.
  * Archive first. One dated folder is claimed (`_archive/notes-merge-<date>`,
    `-2` and on, by one `os.mkdir`). A byte copy of the ruled file as it was
    goes in first, then `manifest.json` (and `MANIFEST.md` for a person)
    naming every part, its size and its hash, BEFORE anything in the folder
    changes.
  * The ruled file is then written whole, atomically, as bytes: its own
    text first (or, with none, the text of the file readers read first),
    then each other part under a heading that names it, `## Merged from
    <file name> (<date>)`. The date is the Apply run's day, never the
    computer's.
  * Each other part is then moved, by one `os.replace`, into the dated
    folder.
  * `unmerge_notes` puts it all back from the manifest, only while the
    ruled file's bytes are exactly what the merge wrote: a note written into
    it since stays, and the Undo says so and changes nothing. The ruled file
    comes back as the bytes it was (line endings and code page untouched),
    by `os.replace` of the archived copy; the merged file goes into the
    dated folder, never deleted. A file whose place is taken again is left
    there and named.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

ARCHIVE_ROOT = "_archive"
BUCKET_PREFIX = "notes-merge-"
MANIFEST_JSON = "manifest.json"
MANIFEST_MD = "MANIFEST.md"
MERGED_HEADING = "## Merged from {name} ({day})"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(root: Path, rel: Any) -> Optional[Path]:
    """`rel` as a folder inside the workspace, or None."""
    try:
        folder = (root / str(rel or "")).resolve()
        folder.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return folder if str(rel or "").strip() and folder.is_dir() and folder != root.resolve() else None


def _moved_before(root: Path, rel: str) -> List[str]:
    """The names earlier merges (not undone) moved out of this folder."""
    out: List[str] = []
    arch = root / ARCHIVE_ROOT
    try:
        buckets = [b for b in arch.iterdir() if b.name.startswith(BUCKET_PREFIX)]
    except OSError:
        return out
    for b in buckets:
        try:
            rec = json.loads((b / MANIFEST_JSON).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("under") == rel and not rec.get("undone"):
            out += [str(n) for n in rec.get("moved") or []]
    return out


def merge_parts(workspace_root, folder) -> Tuple[Optional[Path], List[Path], str]:
    """`(the ruled file or None, the parts to merge, why not)`: the rule for
    which of a folder's notes files merge. `why` is "" when two or more
    parts merge; else `too_few` or `undecodable`. Reads only."""
    import session_notes_file as snf
    from sync_conflict_copies import copy_tag

    root = Path(str(workspace_root))
    folder = Path(folder)
    try:
        rel = folder.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None, [], "too_few"
    live = snf.live_notes(folder)
    bases = [snf.ruled_name(folder)] + _moved_before(root, rel)
    parts = [p for p in live if snf.is_ruled(p)
             or not any(copy_tag(b, p.name) is not None for b in bases if b)]
    if len(parts) < 2:
        return None, parts, "too_few"
    for p in parts:
        try:
            p.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return None, parts, "undecodable"
    ruled = next((p for p in parts if snf.is_ruled(p)), None)
    return ruled, parts, ""


def merged_text(parts: List[Dict[str, str]], day: str) -> str:
    """The ruled file's new text: the first part as it is, then each other
    part under a heading naming the file it came from."""
    out = parts[0]["text"].rstrip("\r\n")
    for p in parts[1:]:
        out += "\n\n" + MERGED_HEADING.format(name=p["name"], day=day) + "\n\n" + p["text"].strip("\r\n")
    return out + "\n"


def _claim_bucket(root: Path, day: str) -> Path:
    arch = root / ARCHIVE_ROOT
    arch.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        name = BUCKET_PREFIX + day + ("" if n == 1 else f"-{n}")
        try:
            os.mkdir(arch / name)
            return arch / name
        except FileExistsError:
            n += 1


def _write_bytes(path: Path, data: bytes) -> None:
    """Bytes into `path` atomically: a new sibling written whole, then one
    `os.replace` over it."""
    n = 0
    while True:
        tmp = path.with_name(f".{path.name}.merge-{os.getpid()}-{n}")
        try:
            with open(tmp, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            break
        except FileExistsError:
            n += 1
    os.replace(str(tmp), str(path))


def merge_notes(workspace_root, folder_rel: str, *, today: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. Merge the folder's notes files into its ruled name, as the
    module docstring says. `today` is the Apply run's own day. Answers
    `{status, bucket, ruled, merged}`: `merged` (how many files went in,
    besides the ruled one); `noop` when fewer than two parts merge;
    `undecodable` when a part is not UTF-8 (nothing written); `not_found`
    when it is not a folder of this workspace."""
    import session_notes_file as snf
    from atomic_write import atomic_write_json, atomic_write_text

    root = Path(str(workspace_root))
    folder = _inside(root, folder_rel)
    if folder is None:
        return {"status": "not_found"}
    ruled_p, parts, why = merge_parts(root, folder)
    if why:
        return {"status": "noop" if why == "too_few" else why}
    target = ruled_p or (folder / snf.ruled_name(folder))
    had = ruled_p is not None
    raw = {p: p.read_bytes() for p in parts}
    texts = [{"name": p.name, "text": raw[p].decode("utf-8-sig")} for p in parts]
    # The ruled file leads when it is there; otherwise the file readers read
    # first does (`live_notes`' order).
    if had:
        texts.sort(key=lambda t: t["name"] != target.name)
    day = today or _dt.date.today().isoformat()
    data = merged_text(texts, day).encode("utf-8")
    bucket = _claim_bucket(root, day)
    rel = folder.resolve().relative_to(root.resolve()).as_posix()
    keep_dir = bucket / rel
    keep_dir.mkdir(parents=True, exist_ok=True)
    if had:
        shutil.copy2(target, keep_dir / target.name)
    moved = [p for p in parts if p != target]
    record = {"under": rel, "ruled": target.name, "ruled_existed": had, "day": day,
              "parts": [{"name": p.name, "sha256": _sha(raw[p]), "size": len(raw[p])} for p in parts],
              "moved": [p.name for p in moved], "merged_sha256": _sha(data), "undone": False}
    atomic_write_json(bucket / MANIFEST_JSON, record)
    atomic_write_text(bucket / MANIFEST_MD, "# Notes merge\n\n" + f"Folder: {rel}\n\nMerged into {target.name}:\n\n"
                      + "".join(f"- {t['name']}\n" for t in texts)
                      + "\nUndo puts every file back while the merged file is unchanged.\n")
    _write_bytes(target, data)
    for p in moved:
        os.replace(str(p), str(keep_dir / p.name))
    return {"status": "merged", "bucket": bucket.name, "ruled": target.name, "merged": len(moved)}


def unmerge_notes(workspace_root, bucket: str) -> Dict[str, Any]:
    """A WRITER. The Undo of one merge, from its manifest. Answers `{status,
    restored, left}`: `restored` with every file back as the bytes it was;
    `changed` (nothing written) when the ruled file is no longer what the
    merge wrote, or its archived copy is gone; `already` when this merge was
    undone; `not_found` for no such merge."""
    from atomic_write import atomic_write_json

    root = Path(str(workspace_root))
    name = str(bucket or "")
    if Path(name).name != name or not name.startswith(BUCKET_PREFIX):
        return {"status": "not_found"}
    folder_b = root / ARCHIVE_ROOT / name
    try:
        record = json.loads((folder_b / MANIFEST_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "not_found"}
    if record.get("undone"):
        return {"status": "already"}
    folder = _inside(root, record.get("under"))
    if folder is None:
        return {"status": "not_found"}
    ruled = folder / str(record.get("ruled") or "")
    try:
        now_bytes = ruled.read_bytes()
    except OSError:
        return {"status": "changed"}
    if _sha(now_bytes) != record.get("merged_sha256"):
        return {"status": "changed"}
    keep_dir = folder_b / str(record["under"])
    # review of #1629: the ruled file's archived copy must still be there
    # before anything moves, or the folder would be left with no notes file
    if record.get("ruled_existed") and not (keep_dir / ruled.name).is_file():
        return {"status": "changed"}
    left: List[str] = []
    # the merged file into the dated folder, never deleted; then the ruled
    # file's own bytes back by one move
    os.replace(str(ruled), str(keep_dir / (ruled.name + ".merged")))
    if record.get("ruled_existed"):
        os.replace(str(keep_dir / ruled.name), str(ruled))
    restored = 0
    for moved in record.get("moved") or []:
        src, dst = keep_dir / moved, folder / moved
        if dst.exists() or not src.is_file():
            left.append(moved)
            continue
        os.replace(str(src), str(dst))
        restored += 1
    record["undone"] = True
    atomic_write_json(folder_b / MANIFEST_JSON, record)
    return {"status": "restored", "restored": restored, "left": left}


__all__ = ["merge_notes", "unmerge_notes", "merge_parts", "merged_text", "BUCKET_PREFIX"]
