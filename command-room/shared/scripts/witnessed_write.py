#!/usr/bin/env python3
"""Witnessed writes — an atomic write that leaves a verifiable receipt.

`witnessed_write_text(path, text)` performs the standard atomic write
(temp sibling + fsync + rename, via `atomic_write.atomic_write_text`) and then
drops a `<name>.witness` sidecar next to the file recording exactly what was
written: `{sha256, bytes, source_seq?, written_at}`. A later
`check_witness(path)` compares the file on disk against its witness and
answers the question every Drive-synced migration needs answered before it
writes again: *is this still the file I wrote?*

Statuses returned by `check_witness`:

  ok                   file bytes hash-match the witness — safe to build on.
  externally_modified  hash mismatch and the file looks NEWER than the witness
                       (a human or another tool edited it since the witnessed
                       write). The caller's job is to RE-READ the file, decide
                       what to do with the new content, and re-witness — this
                       module never resolves the conflict itself.
  regressed            hash mismatch and the WITNESS is ahead of the file's own
                       stamps (the file's max LIVE-STATE `source_seq` is below
                       the witnessed one, or the file's mtime predates the
                       witness `written_at`). This is the sync-swap signature:
                       an OLDER version of the file was restored over the one
                       we wrote (OneDrive/Drive last-writer-wins, version
                       restore, stale mirror). Building on a regressed file
                       propagates the regression — refuse and surface it.
  no_witness           no sidecar exists — nothing to verify against.
  missing_file         the witness exists but the file is gone.

Every open in this module goes through an extended-length (`\\\\?\\`) path on
win32 — a 260-char workspace path must never kill a caller (MIGRATE1 fix 5).

stdlib + atomic_write only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))

from atomic_write import atomic_write_json, atomic_write_text  # noqa: E402

WITNESS_SUFFIX = ".witness"

# Grace applied to the mtime comparison — filesystem timestamp granularity
# plus the gap between the rename and the witness write.
_MTIME_TOLERANCE_S = 2.0

# LIVE-STATE start-marker attribute scan for the file-stamp side of the
# regression check. The marker grammar authority is
# render_brain_block._markers; this generalizes only the attribute lookup
# (`source_seq=<int>`) that read_block_meta performs per-id, because the
# witness check must consider EVERY block's stamp, not one id's.
_SOURCE_SEQ_RE = re.compile(
    r"<!--\s*LIVE-STATE:[A-Za-z0-9_-]+\b[^>]*?source_seq=(\d+)[^>]*-->",
    re.IGNORECASE)


def _lp(p) -> str:
    """Extended-length path form for win32; identity elsewhere."""
    s = str(p)
    if sys.platform != "win32":
        return s
    if s.startswith("\\\\?\\"):
        return s
    s = os.path.abspath(s)
    if s.startswith("\\\\"):
        return "\\\\?\\UNC" + s[1:]
    return "\\\\?\\" + s


def witness_path(path) -> Path:
    return Path(str(path) + WITNESS_SUFFIX)


def _read_bytes(path) -> bytes:
    with open(_lp(path), "rb") as fh:
        return fh.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_max_source_seq(text: str):
    """Max `source_seq` across every LIVE-STATE start marker, or None."""
    seqs = [int(m.group(1)) for m in _SOURCE_SEQ_RE.finditer(text)]
    return max(seqs) if seqs else None


def write_witness(path, data: bytes | None = None, source_seq=None) -> dict:
    """Witness the CURRENT on-disk content of `path` (or `data`, when the
    caller already holds the exact bytes it just wrote).

    This is also the caller-side second half of the `externally_modified`
    contract: re-read, absorb, then `write_witness(path)` to re-witness.
    """
    if data is None:
        data = _read_bytes(path)
    meta = {
        "sha256": _sha256(data),
        "bytes": len(data),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    if source_seq is not None:
        meta["source_seq"] = int(source_seq)
    atomic_write_json(Path(_lp(witness_path(path))), meta)
    return meta


def witnessed_write_text(path, text: str, *, source_seq=None,
                         encoding: str = "utf-8") -> dict:
    """Atomic write + witness sidecar. Returns the witness metadata.

    The witness hashes the exact bytes written (`text.encode(encoding)`;
    atomic_write_text writes with newline='' so no translation occurs).
    """
    p = Path(_lp(path))
    atomic_write_text(p, text, encoding=encoding)
    return write_witness(path, data=text.encode(encoding),
                         source_seq=source_seq)


def check_witness(path) -> dict:
    """Compare `path` against its witness sidecar. See module docstring for
    the status vocabulary. Never raises for the expected shapes — an
    unreadable/corrupt witness reports `no_witness` (there is nothing sound
    to verify against), an absent file reports `missing_file`."""
    wp = witness_path(path)
    result = {"path": str(path), "witness_path": str(wp)}
    try:
        wit = json.loads(_read_bytes(wp).decode("utf-8"))
        if not isinstance(wit, dict) or not isinstance(wit.get("sha256"), str):
            raise ValueError("witness shape")
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        result["status"] = "no_witness"
        return result
    result["witness"] = wit

    try:
        data = _read_bytes(path)
    except OSError:
        result["status"] = "missing_file"
        return result

    actual = _sha256(data)
    result["actual_sha256"] = actual
    result["actual_bytes"] = len(data)
    if actual == wit["sha256"]:
        result["status"] = "ok"
        return result

    # Hash mismatch — decide the direction. Witness ahead of the file's own
    # stamps = regression (an older version was swapped in underneath us);
    # otherwise the file moved forward without us = externally modified.
    witness_seq = wit.get("source_seq")
    file_seq = None
    try:
        file_seq = _file_max_source_seq(data.decode("utf-8", errors="replace"))
    except Exception:
        file_seq = None
    if (isinstance(witness_seq, int) and file_seq is not None
            and file_seq < witness_seq):
        result["status"] = "regressed"
        result["reason"] = (f"file max source_seq {file_seq} is behind the "
                            f"witnessed source_seq {witness_seq}")
        return result

    written_at = wit.get("written_at")
    try:
        witness_epoch = datetime.fromisoformat(written_at).timestamp()
        file_mtime = os.stat(_lp(path)).st_mtime
        if file_mtime < witness_epoch - _MTIME_TOLERANCE_S:
            result["status"] = "regressed"
            result["reason"] = (f"file mtime predates the witness written_at "
                               f"by {witness_epoch - file_mtime:.1f}s")
            return result
    except (TypeError, ValueError, OSError):
        pass

    result["status"] = "externally_modified"
    result["reason"] = ("content hash mismatch with no regression signal — "
                        "re-read the file and re-witness (caller's job)")
    return result
