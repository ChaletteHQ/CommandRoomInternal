#!/usr/bin/env python3
"""Write-side cleanup remediations (v3.19.x — SPEC CLEAN1).

`integrity_check.py` is strictly READ-ONLY (it detects). This module holds the
bounded, client-safe REMEDIATE actions the weekly `cleanup` skill is allowed to
take, plus the read-only staleness flags it surfaces in the Monday note.

CLIENT SAFETY (mandatory — this runs on 5 live client workspaces with hand-made
folders and real data):
  - Never deletes a user's folders or files. Orphan folders are FLAGGED by
    `integrity_check.scan_project_structure`, never removed here.
  - Never rewrites `entities.json` / `events.jsonl` (the substrate). Only derived
    views regenerate, and only when their content actually changed.
  - Never overwrites an existing SESSION_NOTES file — `backfill_session_notes`
    scaffolds only when none exists.
  - ARCHIVE-ONLY policy (Command Room build, 2026-06): nothing is ever deleted —
    not even machine cruft. The lock sweep MOVES `*.lock.stale.*` sentinels older
    than 1 hour (only inside `_hq/data/` and `_hq/.system/`) into
    `_archive/stale-locks/`, mirroring their original path (with the `_hq`
    component renamed `hq-system-locks` — HQRESOLVE1: the archive tree must
    never contain a dir literally named `_hq`). A non-technical CEO is
    never surprised by a vanished file; everything that leaves its working spot
    lands in `_archive/` instead. ONE ruled exception (LB2 D5, M 2026-07-19):
    `*.readalarm.json` sidecars >30 days past their last recorded failure are
    DELETED by `prune_stale_readalarms` — derived alarm state, never substrate
    or user files; see the function's comment block.

Every function is idempotent: a re-run on an already-clean workspace makes zero
writes (acceptance #7).

Public API
----------
  sweep_stale_locks(root, max_age_s=3600, now=None) -> list[str]   # archive-moves, never deletes
  prune_stale_readalarms(root, max_age_days=30, now=None) -> list[str]  # the ONE delete exception (LB2 D5 — derived alarm state, see the function)
  backfill_session_notes(root, folder_name, project_display=None, today=None) -> str | None
  rollover_session_notes(root, threshold_lines=150, keep_entries=5, today=None) -> list[dict]
  regenerate_decision_log_if_changed(root) -> dict
  regenerate_craft_view_if_changed(root) -> dict
  check_analytical_view_staleness(root, now=None) -> list[dict]
  insight_generator_staleness(root, now=None, threshold_days=14) -> dict
  check_aliases_staleness(root) -> dict | None
  plan_folder_tidy(root, now=None) -> dict      # T10 (cr1#1019): read-only plan + re-count
  tidy_folder(root, dry_run=False, ...) -> dict # archive-first into _archive/cleanup-<date>/ + manifest
  undo_folder_tidy(root, bucket=None) -> dict   # every move put back from the manifest
"""
from __future__ import annotations

import datetime
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(ROOT))

from atomic_write import atomic_write_text  # noqa: E402
from event_time import event_time  # noqa: E402
import render_decision_log  # noqa: E402
import render_master_tracker  # noqa: E402
import render_router_misses  # noqa: E402
import render_craft_view  # noqa: E402
import session_notes_file as snf  # noqa: E402  (cr1#1580: the one notes name)


# --- D6: stale lock-file sweep (archive-move, never delete) --------------------
#
# NOTE (A1 coordination): SPEC A1 also adds a lock-file sweep (its step 7). A1
# had not shipped when CLEAN1 landed, so the sweep lives here. Whichever of A1 /
# CLEAN1 ships SECOND must verify this implementation exists and SKIP adding a
# duplicate sweep — there must be exactly one owner of the stale-lock sweep.
#
# Stale locks are sentinels renamed by atomic_write.release_write_lock() when it
# can't cleanly unlink its own lock; they take the shape
# `<file>.lock.stale.<epoch>.<pid>`. We age them off by filesystem mtime (NOT the
# embedded epoch) so a clock skew on the writing host can't strand them.
#
# ARCHIVE-ONLY (Command Room build, 2026-06): aged sentinels are MOVED into
# `_archive/stale-locks/` (mirroring their workspace-relative path, `_hq`
# renamed `hq-system-locks` per HQRESOLVE1), never unlinked. Same selection logic, same return value (the original relative paths
# that were cleared from their working spot) — only the disposition changed from
# delete to archive.
_LOCK_GLOB = "*.lock.stale.*"
_LOCK_DIRS = ("_hq/data", "_hq/.system")
_ARCHIVE_ROOT = "_archive"

# ACCESS1 — the plugin-owned runtime cache inside the workspace
# (PLUGIN_BOUNDARY invariant 4, as amended). Content-hashed plugin CODE, not
# customer data and not machine cruft: every sweep in this module stops at its
# edge, and nothing here ever deletes, moves or archives anything under it. A
# swept runtime is a runtime whose manifest no longer verifies, which is a
# fire that refuses to run — the sweep would cause the outage it is tidying up
# after. Retirement of old versions is `runtime_cache_report`, which reports
# and deletes nothing (§0.26; on the merged mount deletes are refused anyway).
# ONE named exception, T10 (cr1#1019): `tidy_folder` archive-moves a stage
# the `current.json` pointer does NOT name (and not the version below it),
# never the pointer or the stage it names; see its block at the end.
RUNTIME_CACHE_REL = "_hq/.cache/cr-runtime"


def _under_runtime_cache(root: Path, path: Path) -> bool:
    """True when `path` is inside the runtime cache — the one exemption every
    sweep in this module shares."""
    try:
        rel = path.resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return False
    return rel == RUNTIME_CACHE_REL or rel.startswith(RUNTIME_CACHE_REL + "/")


def lock_dirs_can_reach_runtime_cache() -> bool:
    """True when a `_LOCK_DIRS` entry could contain the runtime cache.

    It cannot today (`_hq/data` and `_hq/.system` against `_hq/.cache/
    cr-runtime`), so the `_under_runtime_cache` calls inside `sweep_stale_locks`
    are defence-in-depth against a future widening of `_LOCK_DIRS` rather than a
    live fence — and a removal proof cannot make them red. The suite asserts
    this out loud instead of counting an unreachable branch as a proven one
    (review finding L-2). `prune_stale_readalarms`' exemption IS live: it walks
    the whole tree, and removing it deletes a readalarm inside the cache.
    """
    return any(RUNTIME_CACHE_REL == d or RUNTIME_CACHE_REL.startswith(d + "/")
               for d in _LOCK_DIRS)


def _never_clobber(dest: Path) -> Path:
    """The archive tree's never-overwrite rule, in ONE place: if `dest` is
    taken, append a numeric suffix until it isn't. Shared by `_archive_move`
    (the lock sweep) and the rollover's pre-reshape safety copy, so both obey
    the same guarantee — an archived copy is never clobbered by a later one."""
    if not dest.exists():
        return dest
    stem, suffix, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = dest.parent / f"{stem}.{n}{suffix}"
        n += 1
    return dest


# HQRESOLVE1 (attended-walk F-4): a mirrored path component literally named
# `_hq` would recreate the workspace-root marker INSIDE `_archive/` — e.g.
# `_archive/stale-locks/_hq/data/` — and any `_hq`-seeking discovery that does
# not prune `_archive/` binds the decoy and silently reads/writes an archived
# substrate. The mirror therefore renames that one component; the mapping is
# fixed and documented, so an archived file stays traceable to where it lived.
_HQ_MIRROR_RENAME = "hq-system-locks"


def _archive_dest(root: Path, src: Path, bucket: str) -> Path:
    """Where `src` lands under `root/_archive/<bucket>/`, mirroring its
    workspace-relative path so an archived file is always traceable back to
    where it lived — except that a path component named `_hq` mirrors as
    `hq-system-locks` (HQRESOLVE1: the archive tree must never contain a
    directory literally named `_hq`, the workspace-root discovery marker).
    Creates the parent; applies the never-clobber suffix."""
    try:
        rel = src.relative_to(root)
    except ValueError:
        rel = Path(src.name)
    rel = Path(*(_HQ_MIRROR_RENAME if part == "_hq" else part
                 for part in rel.parts))
    dest = root / _ARCHIVE_ROOT / bucket / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    return _never_clobber(dest)


def _archive_move(root: Path, src: Path, bucket: str) -> bool:
    """Move `src` into `root/_archive/<bucket>/<workspace-relative-path>`,
    preserving the original sub-path so an archived file is always traceable back
    to where it lived. Creates parents as needed. Returns True on success.

    Never overwrites: if a same-named archive entry already exists, a numeric
    suffix is appended so no archived copy is ever clobbered (still no delete)."""
    import shutil

    dest = _archive_dest(root, src, bucket)
    try:
        shutil.move(str(src), str(dest))
        return True
    except OSError:
        return False


def _archive_replace(root: Path, src: Path, bucket: str,
                     left: list | None) -> bool:
    """`_archive_move`'s MOUNT-SAFE twin (MAINTJOBS1 MUST 5): ONE
    `os.replace` into `_archive/<bucket>/...`, and nothing else. On the
    merged seat's mount `os.unlink` is refused (probe P1/P2) while
    `os.replace` works, and `shutil.move`'s copy-then-unlink fallback would
    leave the original AND a copy behind. A move that fails is recorded on
    `left` (path + the error's class) and is never retried in this call."""
    dest = _archive_dest(root, src, bucket)
    try:
        os.replace(str(src), str(dest))
        return True
    except OSError as exc:
        if left is not None:
            left.append({"path": str(src.relative_to(root)).replace("\\", "/"),
                         "reason": type(exc).__name__})
        return False


# Locks that exist BY DESIGN and must never be swept: the A1 events writer
# lock file (content never changes, byte-range locked at write time — its age
# means nothing) and its info sidecar's shape is not *.lock anyway.
_PERMANENT_LOCK_NAMES = frozenset({".writer.lock"})


def sweep_stale_locks(root: str | Path, max_age_s: int = 3600,
                      now: float | None = None, *,
                      orphans: bool = True,
                      left: list | None = None) -> list[str]:
    """Archive stale lock litter under `_hq/data/` and `_hq/.system/` by
    MOVING it into `_archive/stale-locks/` — nothing is deleted. Returns the
    workspace-relative paths cleared. Two passes:

      1. `*.lock.stale.*` mv-aside sentinels older than `max_age_s` (the
         original D6 sweep — a writer still mid-recovery is never disturbed).
      2. BARE ORPHANED `*.lock` sentinels (BUG-8330 item 15): a sentinel
         older than `max_age_s` whose recorded pid is DEAD is an abandoned
         lock from a crashed writer — before this pass, only the renamed
         `.stale.*` shape was ever swept, so daily fires re-littered bare
         orphans faster than the weekly sweep could not-collect them. A lock
         with a LIVE pid is never touched regardless of age (mirrors the
         item-7d reclaim-refusal), and `.writer.lock` (the A1 byte-range
         lock file, permanent by design) is exempt.

    Idempotent: a second run finds nothing to archive (the moved sentinels now
    live under `_archive/`, outside the `_LOCK_DIRS` it scans).

    LEASE3 MUST 1: once per sweep it also calls `lease_lock.reap_released` on
    `_hq/data/events.jsonl`, the events lease's own reaper (its released
    sidecars are removed by the lease module's allow-listed delete, not
    archived); the reaped names join the returned list.

    MAINTJOBS1 MUST 5: every move is ONE `os.replace` (`_archive_replace`) -
    mount-safe, never a copy-then-unlink - and a move that fails is appended
    to `left` when the caller hands a list. `orphans=False` skips pass 2: a
    bare lock's pid means nothing on a host that did not take it (a merged
    fire runs on the customer's computer's VM, not the process that holds a
    lock), so the merged fire sweeps only the `.stale.*` sentinels a writer
    already moved aside."""
    root = Path(root)
    cutoff_now = time.time() if now is None else now
    archived: list[str] = []
    for rel in _LOCK_DIRS:
        d = root / rel
        if not d.is_dir():
            continue
        for f in d.glob(_LOCK_GLOB):
            if not f.is_file() or _under_runtime_cache(root, f):
                continue
            try:
                age = cutoff_now - f.stat().st_mtime
            except OSError:
                continue
            if age > max_age_s:
                src_rel = str(f.relative_to(root)).replace("\\", "/")
                if _archive_replace(root, f, "stale-locks", left):
                    archived.append(src_rel)
        if not orphans:
            continue
        # Pass 2 — bare orphaned sentinels (dead holder + past the floor).
        from atomic_write import _pid_alive, _read_lock_payload
        for f in d.glob("*.lock"):
            if (not f.is_file() or f.name in _PERMANENT_LOCK_NAMES
                    or _under_runtime_cache(root, f)):
                continue
            try:
                age = cutoff_now - f.stat().st_mtime
            except OSError:
                continue
            if age <= max_age_s:
                continue
            try:
                pid, _epoch = _read_lock_payload(f)
            except Exception:
                pid = 0
            if pid and _pid_alive(pid):
                continue  # a live holder is never swept, however old
            src_rel = str(f.relative_to(root)).replace("\\", "/")
            if _archive_replace(root, f, "stale-locks", left):
                archived.append(src_rel)
    # LEASE3 MUST 1 — the events lease's reaper, once per sweep. A release
    # whose directory the mount refused to remove leaves an
    # `events.jsonl.lease.released.<stamp>` sidecar; `lease_lock.reap_released`
    # removes them through the lease module's own allow-listed delete
    # (`_remove_released`), and this sweep is its caller. A LIVE lease
    # (`events.jsonl.lease/`) never matches the `.released.` prefix. The reaped
    # names join the list, workspace-relative. A missing lease module or any
    # OSError never fails the sweep.
    try:
        try:
            import lease_lock as _lease_lock
        except ImportError:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            import lease_lock as _lease_lock
        _reaped = _lease_lock.reap_released(root / "_hq" / "data" / "events.jsonl")
        archived.extend(f"_hq/data/{n}" for n in _reaped)
    except Exception:
        pass
    return archived


# --- LB2 D5: stale readalarm-sidecar pruning (the ONE delete exception) --------
#
# read_alarm.py sidecars (`<file>.readalarm.json`) are BY DESIGN never cleared
# on a clean read — evidence preservation — and only age out of the SURFACED
# view (read_alarm.RECENT_HOURS). Nothing ever pruned the files themselves, so
# they accumulate forever next to substrate files. LB2 D5 (M ruling
# 2026-07-19): cleanup's hygiene phase DELETES sidecars whose `last_seen` is
# older than 30 days — delete, not archive, breaking the module's ARCHIVE-ONLY
# doctrine for exactly this class because a sidecar is DERIVED ALARM STATE
# (machine telemetry about a past read failure), never substrate and never a
# user's file. Everything the archive-only policy protects stays protected.
# Hard floor regardless of arguments: a sidecar younger than RECENT_HOURS x 2
# is never deleted — its evidence must have been surfaceable by at least one
# brief/system-health fire first.

def prune_stale_readalarms(root: str | Path, max_age_days: int = 30,
                           now: float | None = None, *,
                           left: list | None = None) -> list[str]:
    """Delete `*.readalarm.json` sidecars under `_hq/` whose recorded
    `last_seen` (fallback: file mtime, for an unreadable sidecar) is older
    than `max_age_days`. Returns the workspace-relative paths deleted.
    Floor: never deletes a sidecar younger than read_alarm.RECENT_HOURS * 2.
    Idempotent — a second run finds nothing."""
    import json as _json

    from read_alarm import RECENT_HOURS

    root = Path(root)
    hq = root / "_hq"
    if not hq.is_dir():
        return []
    cutoff_now = time.time() if now is None else now
    floor_s = RECENT_HOURS * 2 * 3600
    max_age_s = max(max_age_days * 86400, floor_s)
    deleted: list[str] = []
    for f in hq.rglob("*.readalarm.json"):
        if not f.is_file() or _under_runtime_cache(root, f):
            continue
        age_ref = None
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
            seen = str((data or {}).get("last_seen") or "")
            if seen:
                s = seen.replace("Z", "+00:00")
                dt = datetime.datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                age_ref = dt.timestamp()
        except (OSError, ValueError, _json.JSONDecodeError):
            pass
        if age_ref is None:
            try:
                age_ref = f.stat().st_mtime
            except OSError:
                continue
        if (cutoff_now - age_ref) > max_age_s:
            rel = str(f.relative_to(root)).replace("\\", "/")
            try:
                f.unlink()
                deleted.append(rel)
            except OSError as exc:
                # ACCESS1 (probe P2): on the merged mount every delete is
                # refused. The sidecar stays and the sweep carries on — the
                # posture was already right, the silence was not.
                try:
                    from atomic_write import left_in_place
                    left_in_place("readalarm prune", f, exc)
                except Exception:
                    pass
                if left is not None:
                    left.append({"path": rel, "reason": type(exc).__name__})
                continue
    return deleted


# --- TMPSWEEP1 (cr1#137): orphaned atomic-write temp files ---------------------
#
# `atomic_write` and `deliverables` both stage a write through
# `tempfile.mkstemp(prefix=".<name>.tmp.", suffix=".write", dir=<target dir>)`
# and finish with ONE `os.replace`. A write that died between the two leaves
# that file behind, next to the data and looking like data (one was 2.4 MB),
# and nothing swept it. The sweep below archive-MOVES them (never a delete,
# the module's doctrine) once they are older than `TMP_WRITE_MAX_AGE_S`: a
# write still in flight is seconds old, never a day.
_TMP_WRITE_RE = re.compile(r"^\..+\.tmp\.[^.]+\.write$")
TMP_WRITE_MAX_AGE_S = 24 * 3600
_TMP_WRITE_BUCKET = "tmp-writes"
#: Where the producers write beside their targets: the data files, the
#: system folder, the `_hq/` root (the tracker, the people view) and the
#: landed deliverables under `_hq/meetings/`. Non-recursive on purpose.
_TMP_WRITE_DIRS = ("_hq", "_hq/data", "_hq/.system", "_hq/meetings")


def sweep_tmp_write_files(root: str | Path, max_age_s: int = TMP_WRITE_MAX_AGE_S,
                          now: float | None = None, *,
                          left: list | None = None) -> list[str]:
    """Archive orphaned `.<name>.tmp.<id>.write` files in `_TMP_WRITE_DIRS`
    (`_hq/`, `_hq/data/`, `_hq/.system/`, `_hq/meetings/`, not recursive)
    that are older than `max_age_s` (a day by default), each by ONE
    `os.replace` into `_archive/tmp-writes/...` (mount-safe, never a
    copy-then-unlink, never a delete). Returns the workspace-relative paths
    moved. Only the producers' exact shape is matched (`atomic_write` and
    `deliverables`: `mkstemp(prefix=".<name>.tmp.", suffix=".write")`): a
    lookalike (`notes.tmp.txt`, a `.tmp` folder) is never touched, nor is
    any live file. A move that fails is named on `left` when the caller
    hands a list. Idempotent: the moved files live under `_archive/`.

    Known misses, stated rather than hidden: a temp file beside a target in
    any other folder (an exemplar in a project folder, a schedule config),
    `widget_transport`'s `<name>.html.tmp.<pid>` under `_hq/.system/widgets/`
    and `scaffold_automation`'s `.<name>.<rand>.tmp` under `automations/`
    are other shapes in other places; this sweep does not claim them."""
    root = Path(root)
    cutoff_now = time.time() if now is None else now
    archived: list[str] = []
    for rel in _TMP_WRITE_DIRS:
        d = root / rel
        if not d.is_dir():
            continue
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if not p.is_file() or not _TMP_WRITE_RE.match(p.name):
                continue
            try:
                age = cutoff_now - p.stat().st_mtime
            except OSError:
                continue
            if age < max_age_s:
                continue
            if _archive_replace(root, p, _TMP_WRITE_BUCKET, left):
                archived.append(f"{rel}/{p.name}")
    return archived


def sweep_lock_litter(workspace_root, *, max_age_s: int = 3600,
                      now: float | None = None) -> dict:
    """The maintenance fire's DAILY lock-litter sweep on the merged shape
    (MAINTJOBS1 MUST 5; on `workspace_access.RUN_WRITER_ALLOWLIST`).

    The walks left `_hq/data/entities.json.lock.stale.*` and
    `_hq/.system/atomic.lock.stale.*` behind because the mount refuses a
    delete and the only sweep (`cleanup` Rule 9) is weekly and in prose. This
    runs both hygiene passes a merged fire can run safely:
      * `sweep_stale_locks(..., orphans=False)` - `.stale.*` sentinels older
        than `max_age_s` archived by ONE `os.replace` each;
      * `prune_stale_readalarms` - the LB2 D5 sidecar prune.
    A file that cannot be moved or removed is named in `left` and is not
    tried again in this call. The writer is named first
    (`receipts.require_writer_identity`). Returns
    `{archived, pruned, tmp_writes, left, n_archived, n_pruned, n_tmp_writes,
    n_left}` - counts and workspace-relative paths only.

    TMPSWEEP1 (cr1#137): `sweep_tmp_write_files` runs here too, so the
    merged seat's daily fire clears orphaned write temp files the same way."""
    from receipts import require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    left: list = []
    archived = sweep_stale_locks(workspace_root, max_age_s=max_age_s, now=now,
                                 orphans=False, left=left)
    pruned = prune_stale_readalarms(workspace_root, now=now, left=left)
    tmp_writes = sweep_tmp_write_files(workspace_root, now=now, left=left)
    return {"archived": archived, "pruned": pruned, "tmp_writes": tmp_writes,
            "left": left, "n_archived": len(archived), "n_pruned": len(pruned),
            "n_tmp_writes": len(tmp_writes), "n_left": len(left)}


def runtime_cache_report(root: str | Path) -> dict:
    """What is installed under the runtime cache, and what is past its keep.

    READ-ONLY, always: it lists the versions present, names the current one
    from the `current.json` pointer, and says which ones are older than
    current + previous (§0.26's retention). It deletes nothing — the ruling's
    "remove older ones when the VM permits a Python unlink" collapsed to "keep,
    flag in the Monday note" the moment probe P2 came back EPERM, and a report
    that cannot act is still worth having: it is how an install that has been
    re-run a dozen times becomes visible before the folder is full.
    """
    import json as _json

    root = Path(root)
    cache = root / "_hq" / ".cache" / "cr-runtime"
    out: dict = {"present": [], "current": None, "keep": [], "retire": [],
                 "cache_rel": RUNTIME_CACHE_REL}
    if not cache.is_dir():
        return out
    versions = sorted(d.name for d in cache.iterdir() if d.is_dir())
    out["present"] = versions
    try:
        pointer = _json.loads((cache / "current.json").read_text(encoding="utf-8"))
        current = pointer.get("version")
        out["current"] = current if isinstance(current, str) else None
    except (OSError, ValueError, AttributeError):
        out["current"] = None
    # Keep the current version and the highest one BELOW it (§0.26's
    # "current + previous"). Everything else retires — including a version
    # ABOVE current, which is the residue of a downgrade and is exactly the
    # thing a keep-the-two-newest rule would protect by mistake.
    current = out["current"]
    keep = set()
    if current in versions:
        keep.add(current)
        below = [v for v in versions if _version_key(v) < _version_key(current)]
        if below:
            keep.add(max(below, key=_version_key))
    else:
        keep.update(sorted(versions, key=_version_key)[-2:])
    out["keep"] = [v for v in versions if v in keep]
    out["retire"] = [v for v in versions if v not in keep]
    return out


def _version_key(value: str) -> tuple:
    """A sortable key for a dotted version. Non-numeric parts sort as 0 with
    their text kept as a tiebreak, so an unexpected shape degrades to something
    ordered rather than raising in the middle of a weekly sweep."""
    parts = []
    for chunk in str(value).split("."):
        try:
            parts.append((int(chunk), ""))
        except ValueError:
            parts.append((0, chunk))
    return tuple(parts)


# --- D3: missing SESSION_NOTES scaffold backfill -------------------------------
_DEFAULT_TEMPLATE = ROOT.parent.parent / "references" / "session-notes-template.md"


def _render_scaffold(project_display: str, today: str, template_path: Path | None) -> str:
    tpl_path = template_path or _DEFAULT_TEMPLATE
    try:
        tpl = tpl_path.read_text(encoding="utf-8")
    except OSError:
        # Self-contained fallback so the backfill never fails on a missing
        # template file (mirrors the minimal content D3 specifies).
        tpl = ("# Session Notes — {{PROJECT}}\n\n"
               "> Backfilled by cleanup on {{DATE}} — no prior session notes existed.\n\n"
               "## Current Status\n\n## Active Work Items\n")
    return tpl.replace("{{PROJECT}}", project_display).replace("{{DATE}}", today)


def backfill_session_notes(root: str | Path, folder_name: str | None,
                           project_display: str | None = None,
                           today: str | None = None,
                           template_path: str | Path | None = None) -> str | None:
    """Scaffold a session-notes file for a project folder that has none.

    The file is `SESSION_NOTES_<folder name>.md`, named by
    `session_notes_file.ruled_name` (cr1#1580): the folder's own name exactly
    as spelled, and for a nested `folder_name` its last segment, so the file
    lands inside the folder (the whole nested value used to go into the file
    name, which made a stray subfolder).

    CLIENT SAFETY: if ANY notes-family file (live, archive or index, any
    spelling) already exists in the folder, this returns None and writes
    nothing — an existing notes file is never overwritten or doubled.
    Returns the workspace-relative path created, or None.

    The scaffold carries the backfill provenance line so it's obvious the file
    was machine-created and is safe for the next "end session" to fill in."""
    root = Path(root)
    # `folder_name` is nullable on a thread record since FOLDERGUARD (the writer
    # emits null rather than guessing), and maintenance-rules.md documents this
    # call as taking one. `root / None` is a TypeError, not a refusal.
    # Whitespace-only is rejected explicitly: Windows strips trailing spaces from
    # a path, so `root / "   "` silently normalizes to the workspace root itself
    # and the scaffold would land there.
    if not folder_name or not str(folder_name).strip():
        return None
    folder = root / folder_name
    if not folder.is_dir():
        return None
    # Never overwrite — any existing notes file (live / archive / index) wins.
    if snf.has_any_notes(folder):
        return None
    name = snf.ruled_name(folder.resolve())
    if not name:
        return None
    today = today or datetime.date.today().isoformat()
    target = folder / name
    content = _render_scaffold(project_display or folder_name, today,
                              Path(template_path) if template_path else None)
    atomic_write_text(target, content)
    return str(target.relative_to(root)).replace("\\", "/")


# --- MAINTAUTO1: scripted Rule 1 session-notes rollover ------------------------
#
# WHY THIS IS CODE AND NOT PROSE. Rule 1 (rollover a SESSION_NOTES file past 150
# lines) shipped as prose in two places while every maintenance action that
# actually executes every week is a python call with a receipt. Prose gets
# second-guessed at fire time; code blocks run. A live 2026-08-03 maintenance
# fire found ten notes files past the threshold — the largest thirteen times
# over — and the fire-time model declared rollover an attended-only step, a
# holdback that appears NOWHERE in the shipped contract. This function is the
# implementation the prose always claimed existed.
#
# THE DOCTRINE IT INHERITS. Archive-only (entries MOVE into a sibling archive
# file, nothing is ever deleted), idempotent (a rolled-over file re-runs to zero
# writes), atomic (`atomic_write_text`), substrate-blind (never reads or writes
# entities.json / events.jsonl). One addition of its own: EVERY reshape is
# preceded by a byte-copy of the original into `_archive/`, so the whole first
# pass over a years-deep backlog is one move away from being undone.
#
# MECHANICAL, NEVER INTERPRETIVE. The split is a pure text partition: H2 blocks
# whose heading carries a parseable date are session-log entries; everything
# else is kept verbatim where it sits. The Session-History and index lines are
# derived deterministically from the entry's own first content line — an LLM may
# polish them afterwards, but execution never waits on one.
#
# AND WHEN IT CANNOT TELL, IT DOES NOTHING. Real notes files drift: entries nest
# under H3, undated `## Meeting:` blocks sit between dated ones, an out-of-order
# append breaks chronology. Every one of those returns an `aborted` record and
# leaves the file byte-identical. A partial reshape of a CEO's memory is worse
# than no reshape (the STAFFCUT round-1 lesson: a fix that writes malformed
# output is worse than the drop it replaced), so the conservation check below
# re-derives that every original line survives into kept-or-archived output
# BEFORE the first byte is written, and abandons the file if it cannot.
_ROLLOVER_DEFAULT_LINES = 150
_ROLLOVER_DEFAULT_KEEP = 5
_PRE_ROLLOVER_BUCKET = "session-notes-pre-rollover"
# The one non-entry H2 the rollover itself authors, and therefore the one it
# must tolerate sitting inside the log region on every later run.
_SESSION_HISTORY_TITLE = "Session History"
_SUMMARY_WORDS = 8

_H2_RE = re.compile(r"^##[ \t]+(.*?)[ \t]*$")
_H3_RE = re.compile(r"^###[ \t]+(.*?)[ \t]*$")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_ISO_LEAD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\b")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_LONG_DATE_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),\s*(\d{4})\b")
# Only a heading that ANNOUNCES itself as a session/meeting entry may have its
# date found mid-string. Without that fence `## Current Status (as of April 7,
# 2026 — Session 15 close)` — a live-state section in a real workspace file —
# reads as a dated entry and gets archived away.
_ENTRY_PREFIX_RE = re.compile(r"^(Session|Meeting)\b")

# A markdown table's alignment rule, with or without leading/trailing pipes:
# `|---|---|`, `--- | :---:`, `:--|--:`. Rows themselves start with `|`.
_TABLE_RULE_RE = re.compile(r"^[|:\- ]*-{3,}[|:\- ]*$")


def _valid_date(y: int, m: int, d: int) -> str | None:
    try:
        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return None


def _entry_date(title: str) -> str | None:
    """The entry date a session-log H2 heading carries, or None if the heading
    is not a dated entry. Three shapes, all observed live:

      `## 2026-08-01 — topic`            ISO leading the heading
      `## April 3, 2026 — Session 10`    long-form date leading the heading
      `## Session 18 close — May 13, 2026` / `## Session: 2026-04-16 (#21) — x`
                                        date anywhere, but ONLY when the
                                        heading opens with Session/Meeting
    """
    if _ISO_LEAD_RE.match(title):
        m = _ISO_DATE_RE.match(title)
        if m:
            return _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _LONG_DATE_RE.match(title)
    if m:
        return _valid_date(int(m.group(3)), _MONTHS.index(m.group(1)) + 1,
                           int(m.group(2)))
    if _ENTRY_PREFIX_RE.match(title):
        m = _ISO_DATE_RE.search(title)
        if m:
            return _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _LONG_DATE_RE.search(title)
        if m:
            return _valid_date(int(m.group(3)), _MONTHS.index(m.group(1)) + 1,
                               int(m.group(2)))
    return None


def _split_h2_blocks(lines: list[str]) -> list[dict]:
    """Partition `lines` at H2 boundaries. blocks[0] is the preamble (title
    None) and may be empty. The concatenation of every block's lines is EXACTLY
    the input — the conservation check downstream depends on that."""
    blocks: list[dict] = []
    cur: dict = {"title": None, "lines": []}
    for ln in lines:
        m = _H2_RE.match(ln)
        if m:
            blocks.append(cur)
            cur = {"title": m.group(1), "lines": [ln]}
        else:
            cur["lines"].append(ln)
    blocks.append(cur)
    return blocks


def _entry_summary(block: dict) -> str:
    """~8 words describing the entry, derived from its own text: the first
    non-empty body line of PROSE with markdown furniture stripped, falling back
    to the heading with its leading date removed. Deterministic — no LLM in the
    path.

    Table rows and their separator rules are skipped rather than summarised. An
    entry that opens with a table is common (a decisions grid, an owner list),
    and "/ Item / Owner / Status /" as the Session-History line for that day
    defeats the whole point of Rule 1's summary — it tells the CEO nothing
    about what happened, and it is the line they scan instead of the archive."""
    body = ""
    for ln in block["lines"][1:]:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("|"):
            continue                      # a table row
        if _TABLE_RULE_RE.match(s):
            continue                      # a header/alignment rule
        s = re.sub(r"^[-*+>]\s*", "", s)
        s = re.sub(r"^\d+[.)]\s*", "", s)
        s = s.replace("**", "").replace("`", "").replace("__", "").strip()
        if not s or s.startswith("|") or _TABLE_RULE_RE.match(s):
            continue
        if s:
            body = s
            break
    if not body:
        body = re.sub(r"^\S+\s*[-—:|]\s*", "", block["title"] or "").strip()
    if not body:
        body = (block["title"] or "").strip()
    words = body.split()
    text = " ".join(words[:_SUMMARY_WORDS])
    if len(words) > _SUMMARY_WORDS:
        text += "..."
    # A pipe would silently split the Rule 12 index row into extra columns.
    return text.replace("|", "/")


def _notes_name_token(path: Path) -> str:
    """`SESSION_NOTES_ACME.md` -> `ACME`; a file with no name part -> ``.
    The archive and index beside a notes file follow that file's own name,
    whatever its spelling (cr1#1580 never renames one)."""
    stem = path.stem
    head = snf.PREFIX + "_"
    if stem.upper().startswith(head):
        return stem[len(head):]
    return ""


def _sibling(path: Path, suffix: str) -> Path:
    token = _notes_name_token(path)
    base = f"{snf.PREFIX}_{token}" if token else snf.PREFIX
    return path.parent / f"{base}_{suffix}{snf.SUFFIX}"


def _maintenance_overrides(root: Path) -> dict[str, str]:
    """The `CUSTOM_CONFIG.md` knobs maintenance-rules.md documents. Tolerant by
    design: any `key: value` line anywhere in the file counts, comments after
    `#` are dropped, and a missing/unreadable file means defaults."""
    out: dict[str, str] = {}
    for candidate in (root / "CUSTOM_CONFIG.md", root / "_hq" / "CUSTOM_CONFIG.md"):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip().lstrip("-* ").strip()
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if re.fullmatch(r"[a-z0-9_]+", key) and val:
                out.setdefault(key, val)
    return out


def _iter_session_notes(root: Path):
    """Live SESSION_NOTES files, resolved the way the rest of the module treats
    project folders: anything in a folder this product keeps as an archive,
    backup or quarantine, or a dot-directory, is out
    (`session_notes_file.in_live_folder`, cr1#1624: exact folder shapes, so a
    client's "Archive Migration 2026" rolls over); so is any file that IS an
    archive, an index, or the shipped template."""
    for p in sorted(root.rglob(snf.GLOB)):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if not snf.in_live_folder(rel.parts[:-1]):
            continue
        # The file-name fence is the one notes-name rule's
        # (`session_notes_file.is_live_name`, cr1#1580), case-insensitive like
        # the directory fence above: an archive or index (a workspace that
        # shouted `..._ARCHIVE_2026.md` included), the shipped template, and
        # SESSQUAR1's swept sidecar (`.swept.md`, and SWEEPSTALE1's
        # `.swept.rejected.md`) are never reshaped as the live file; rolling
        # the sidecar over would archive swept entries into the shared
        # archive family, un-quarantining them. A folder's own ruled-name file
        # stays live even when the folder's name carries one of those marks.
        if not snf.is_live_name(p):
            continue
        if p.is_file():
            yield p


def _plan_rollover(lines: list[str], keep_entries: int) -> dict:
    """Classify one notes file. Returns either {"abort": reason, ...},
    {"skip": reason, ...} or a full plan. Reads only — no I/O, no writes."""
    blocks = _split_h2_blocks(lines)
    entry_idx: list[int] = []
    dates: dict[int, str] = {}
    for i, b in enumerate(blocks):
        if b["title"] is None:
            continue
        d = _entry_date(b["title"])
        if d:
            entry_idx.append(i)
            dates[i] = d
    if not entry_idx:
        nested = any(_entry_date(m.group(1))
                     for m in (_H3_RE.match(ln) for ln in lines) if m)
        return {"abort": "entries_below_h2" if nested else "no_recognized_entries",
                "detail": ("dated entries are nested under H3, a shape this "
                           "rollover does not reshape")
                          if nested else "no dated session entries found"}
    lo, hi = entry_idx[0], entry_idx[-1]
    known = set(entry_idx)
    stray = [blocks[i]["title"] for i in range(lo, hi + 1)
             if i not in known and blocks[i]["title"] != _SESSION_HISTORY_TITLE]
    if stray:
        return {"abort": "unrecognized_section_in_log",
                "detail": f"undated section between dated entries: {stray[0]!r}"}
    seq = [dates[i] for i in entry_idx]
    newest_first = all(a >= b for a, b in zip(seq, seq[1:]))
    oldest_first = all(a <= b for a, b in zip(seq, seq[1:]))
    if not (newest_first or oldest_first):
        # Neither monotone means BOTH an ascending and a descending pair exist;
        # name the ascending one — it is the out-of-order append.
        break_at = next((f"{a} then {b}" for a, b in zip(seq, seq[1:]) if a < b),
                        " / ".join(seq[:3]))
        return {"abort": "entries_unordered",
                "detail": f"entry dates are not in one chronological direction "
                          f"({break_at})"}
    if len(entry_idx) <= keep_entries:
        return {"skip": "nothing_to_archive", "entries": len(entry_idx)}
    archived = (entry_idx[keep_entries:] if newest_first
                else entry_idx[:len(entry_idx) - keep_entries])
    return {
        "blocks": blocks,
        "entry_idx": entry_idx,
        "dates": dates,
        "archived_idx": archived,
        "newest_first": newest_first,
    }


def _render_rollover(path: Path, plan: dict, today: str) -> dict:
    """Turn a plan into the exact text each file gets. Pure — still no writes.
    Returns {active_lines, archives: {Path: [lines to append]}, history_lines,
    index_rows, archive_names, generated}.

    `generated` is every line this function AUTHORED rather than carried over
    from the original: the Session-History section and its lines, the Rule 1
    reference lines, the archive headers, and the blank separators. The
    conservation check subtracts it before looking for losses, because a
    generated line that happens to read identically to an original one would
    otherwise pay for that original's disappearance — and the history lines are
    derived FROM the entries, so reading identically to a line inside one is
    not a freak coincidence but a predictable collision."""
    blocks, archived = plan["blocks"], set(plan["archived_idx"])
    token = _notes_name_token(path)
    history: list[str] = []
    index_rows: list[str] = []
    archives: dict[Path, list[str]] = {}
    ref_names: list[str] = []
    generated: list[str] = []
    for i in plan["archived_idx"]:
        b = blocks[i]
        date = plan["dates"][i]
        summary = _entry_summary(b)
        history.append(f"- {date}: {summary}")
        arch = _sibling(path, f"archive_{date[:4]}")
        if arch.name not in ref_names:
            ref_names.append(arch.name)
        archives.setdefault(arch, [])
        if archives[arch]:
            archives[arch].append("")
            generated.append("")
        archives[arch].extend(b["lines"])
        # People stays blank on purpose: an honest blank beats attribution the
        # parser would have to invent (Rule 12, MAINTAUTO1 D2).
        index_rows.append(f"| {date} | {summary} |  | {arch.name} |")

    hist_idx = next((i for i, b in enumerate(blocks)
                     if b["title"] == _SESSION_HISTORY_TITLE), None)
    hist_section = [f"## {_SESSION_HISTORY_TITLE}", ""] + history + [""]

    active: list[str] = []
    emitted_history = False
    for i, b in enumerate(blocks):
        if i in archived:
            if hist_idx is None and not emitted_history:
                active.extend(hist_section)
                generated.extend(hist_section)
                emitted_history = True
            continue
        if i == hist_idx:
            kept = list(b["lines"])
            while kept and not kept[-1].strip():
                kept.pop()
            active.extend(kept + history + [""])
            generated.extend(history + [""])
            continue
        active.extend(b["lines"])
    if hist_idx is None and not emitted_history:  # pragma: no cover - archived is non-empty
        active.extend(hist_section)
        generated.extend(hist_section)

    while active and not active[-1].strip():
        active.pop()
    for name in ref_names:
        ref = f"> Full entries archived to {name}"
        # Dedupe: a file rolled over twice into the same year's archive must
        # not accrue a second identical reference line every week.
        if ref not in active:
            active.extend(["", ref])
            generated.extend(["", ref])
    active.append("")
    generated.append("")

    for arch, body in archives.items():
        if not arch.is_file():
            head = [f"# Session Notes Archive — {token or path.stem}",
                    "",
                    f"> Rolled over from {path.name} by cleanup on {today} "
                    f"(maintenance Rule 1). Full entries, never summaries — "
                    f"nothing here was deleted from anywhere.",
                    ""]
            archives[arch] = head + body
            generated.extend(head)
    return {"active_lines": active, "archives": archives,
            "history_lines": history, "index_rows": index_rows,
            "archive_names": ref_names, "generated": generated}


def _conservation_residual(original: list[str], rendered: dict) -> list[str]:
    """Every original line of CONTENT must survive into kept-or-archived
    output. Counted as a MULTISET so a duplicated line cannot cover for a
    dropped twin — the shape of the failure this check exists to catch is a
    parser that mishandles ONE of several look-alike entries.

    THE OUTPUT IS NOT THE EVIDENCE. Counting the rendered files as-is would let
    the renderer pay its own debts: it authors Session-History lines DERIVED
    from the entries it is archiving, so a generated line reading identically
    to a line inside one of those entries is not a freak coincidence but a
    predictable collision — and Counter subtraction would then show the
    original line as still present while the archive lost it, writing the
    reshape with content gone. So `have` counts only what was CARRIED OVER:
    the rendered lines minus the exact multiset the renderer authored.

    Blank lines are excluded on purpose, and the exclusion is load-bearing
    rather than a shortcut. The renderer legitimately normalises vertical
    whitespace (it collapses trailing blanks before appending the history and
    reference lines), so counting blanks would produce false aborts. Saying
    plainly that this check protects content and not spacing is worth more than
    a stricter-looking check that is neither."""
    def content(lines) -> Counter:
        return Counter(ln for ln in lines if ln.strip())

    have: Counter = content(rendered["active_lines"])
    for body in rendered["archives"].values():
        have += content(body)
    have -= content(rendered.get("generated") or ())
    missing = content(original) - have
    return sorted(missing.elements())[:5]


def rollover_session_notes(root: str | Path,
                           threshold_lines: int = _ROLLOVER_DEFAULT_LINES,
                           keep_entries: int = _ROLLOVER_DEFAULT_KEEP,
                           today: str | None = None) -> list[dict]:
    """Roll over every SESSION_NOTES file past `threshold_lines` (Rule 1), and
    index what moved (Rule 12). AUTOMATIC — cleanup calls this without asking;
    the pre-reshape copy under `_archive/session-notes-pre-rollover/` is the
    permission.

    Per eligible file, in order: keep `## Current Status` / `## Active Work
    Items` and every other non-entry section exactly where they are, keep the
    `keep_entries` newest dated entries, move the older full entries into
    `SESSION_NOTES_[NAME]_archive_[YYYY].md` (appended, never overwritten), add
    one deterministic `## Session History` line per archived entry plus the
    Rule 1 step-5 reference line, and append Rule 12 index rows to
    `SESSION_NOTES_[NAME]_index.md`.

    Honors the `session_notes_rollover_lines` and `archive_index_enabled` knobs
    from `CUSTOM_CONFIG.md`. Rule 13 embeddings are out of scope.

    Returns one record per file it CONSIDERED (over threshold), never one for a
    file it never had cause to touch:

      {file, status: "rolled_over", entries_archived, archive_files, index_file,
       safety_copy, lines_before, lines_after}
      {file, status: "skipped",  reason}   -- over threshold, nothing to archive
      {file, status: "aborted",  reason, detail}   -- file left byte-identical

    Idempotent: a second run over a rolled-over workspace makes zero writes.
    """
    import shutil

    root = Path(root)
    if not root.is_dir():
        return []
    overrides = _maintenance_overrides(root)
    if threshold_lines == _ROLLOVER_DEFAULT_LINES:
        try:
            threshold_lines = int(overrides.get("session_notes_rollover_lines",
                                                threshold_lines))
        except (TypeError, ValueError):
            pass
    index_enabled = str(overrides.get("archive_index_enabled", "true")).lower() \
        not in ("false", "no", "0", "off")
    today = today or datetime.date.today().isoformat()
    records: list[dict] = []

    for path in _iter_session_notes(root):
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            original_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            records.append({"file": rel, "status": "aborted",
                            "reason": "unreadable", "detail": str(exc)[:120]})
            continue
        lines = original_text.splitlines()
        if len(lines) <= threshold_lines:
            continue

        plan = _plan_rollover(lines, keep_entries)
        if "abort" in plan:
            records.append({"file": rel, "status": "aborted",
                            "reason": plan["abort"],
                            "detail": plan.get("detail", "")})
            continue
        if "skip" in plan:
            records.append({"file": rel, "status": "skipped",
                            "reason": plan["skip"],
                            "entries": plan.get("entries")})
            continue

        rendered = _render_rollover(path, plan, today)
        residual = _conservation_residual(lines, rendered)
        if residual:
            # The high-stakes branch: the split lost content, so this file gets
            # NOTHING written to it. Not a partial reshape, not a best guess.
            records.append({
                "file": rel, "status": "aborted", "reason": "conservation_failed",
                "detail": f"{len(residual)}+ original line(s) would be lost, "
                          f"first: {residual[0][:80]!r}"})
            continue

        # Safety copy FIRST — nothing below this line runs without a byte-exact
        # original sitting in `_archive/`.
        try:
            copy_dest = _archive_dest(root, path, _PRE_ROLLOVER_BUCKET)
            shutil.copy2(str(path), str(copy_dest))
        except OSError as exc:
            records.append({"file": rel, "status": "aborted",
                            "reason": "safety_copy_failed",
                            "detail": str(exc)[:120]})
            continue

        try:
            for arch, body in rendered["archives"].items():
                prior = arch.read_text(encoding="utf-8") if arch.is_file() else ""
                if prior and not prior.endswith("\n"):
                    prior += "\n"
                atomic_write_text(arch, prior + "\n".join(body) + "\n",
                                  create_parents=False)
            atomic_write_text(path, "\n".join(rendered["active_lines"]) + "\n",
                              create_parents=False)
            index_rel = None
            if index_enabled:
                index = _sibling(path, "index")
                if index.is_file():
                    prior = index.read_text(encoding="utf-8")
                    if prior and not prior.endswith("\n"):
                        prior += "\n"
                else:
                    token = _notes_name_token(path) or path.stem
                    prior = (f"# Session Notes Index — {token}\n\n"
                             "| Date | Topics | People | Archive File |\n"
                             "|------|--------|--------|-------------|\n")
                atomic_write_text(index,
                                  prior + "\n".join(rendered["index_rows"]) + "\n",
                                  create_parents=False)
                index_rel = str(index.relative_to(root)).replace("\\", "/")
        except OSError as exc:
            records.append({"file": rel, "status": "aborted",
                            "reason": "write_failed", "detail": str(exc)[:120],
                            "safety_copy": str(copy_dest.relative_to(root)
                                               ).replace("\\", "/")})
            continue

        records.append({
            "file": rel,
            "status": "rolled_over",
            "entries_archived": len(plan["archived_idx"]),
            "archive_files": rendered["archive_names"],
            "index_file": index_rel,
            "safety_copy": str(copy_dest.relative_to(root)).replace("\\", "/"),
            "lines_before": len(lines),
            "lines_after": len(rendered["active_lines"]),
        })
    return records


# --- D4: changed-only DECISION_LOG regeneration --------------------------------
def regenerate_decision_log_if_changed(root: str | Path) -> dict[str, Any]:
    """Regenerate `_hq/views/DECISION_LOG.md` from the substrate, writing only if
    the decision content changed (SPEC CLEAN1 / D4). Thin wrapper over the view's
    owner so cleanup records into actions_taken ONLY when `changed` is True."""
    return render_decision_log.regenerate_if_changed(root)


def regenerate_master_tracker_if_changed(root: str | Path) -> dict[str, Any]:
    """Regenerate `_hq/views/MASTER_TRACKER.md` (+ back-compat copy) from the
    substrate, writing only if the tracker content changed (SPEC CLEAN1 / D4).
    Thin wrapper over the view's owner so cleanup records into actions_taken
    ONLY when `changed` is True. This is the weekly backstop for the tracker
    freeze: end-session regenerates it per session, but a missed regen (the LLM
    end-session hand-render that froze M's tracker from 2026-06-11) would
    otherwise persist for weeks until the next end-session caught it."""
    return render_master_tracker.regenerate_if_changed(root)


def regenerate_router_misses_if_changed(root: str | Path) -> dict[str, Any]:
    """Regenerate `_hq/views/ROUTER_MISSES.md` (SPEC ROUTEMISS1) from the
    routing corrections on the log, writing only if the content changed. Thin
    wrapper over the view's owner so cleanup records into actions_taken ONLY
    when `changed` is True. This is the weekly backstop: the verb handler
    regenerates on each write, but a workspace where the last correction
    predates the renderer, or where a write path skipped the regen, would
    otherwise carry a stale (or absent) view indefinitely."""
    return render_router_misses.regenerate_if_changed(root)


def regenerate_craft_view_if_changed(root: str | Path) -> dict[str, Any]:
    """Regenerate `_hq/views/CRAFT.md` (SPEC CRAFT1 step 1, cr1#396) from the
    craft folders, writing only if the content changed. Thin wrapper over the
    view's owner so cleanup records into actions_taken ONLY when `changed` is
    True. The weekly cleanup is what keeps the index of craft files current
    and names a craft file no record points at."""
    return render_craft_view.regenerate_if_changed(root)


def router_misses_monday_line(root: str | Path) -> Optional[str]:
    """The Monday-note sentence for repeated redirects (SPEC ROUTEMISS1 D3),
    or None when no skill was redirected to 3+ times in 28 days — cleanup
    adds nothing in that case. Read-only; names no event type."""
    return render_router_misses.monday_note_line(root)


# --- D5 / D7: read-only staleness flags ----------------------------------------
# Analytical views are owned by insight-generator (per references/VIEW_GENERATION.md);
# cleanup never regenerates them — it FLAGS staleness and names the owner.
_ANALYTICAL_VIEWS = (
    "RELATIONSHIPS.md", "TIMELINE.md", "COMMITMENT_AGING.md", "DORMANT.md", "THEMES.md",
)
_ANALYTICAL_OWNER = "insight-generator"
_ALIASES_OWNER = "people-crm"


def _substrate_mtime(root: Path) -> float:
    data = root / "_hq" / "data"
    mtimes = [p.stat().st_mtime for p in (data / "events.jsonl", data / "entities.json")
              if p.is_file()]
    return max(mtimes) if mtimes else 0.0


def check_analytical_view_staleness(root: str | Path,
                                    now: float | None = None) -> list[dict]:
    """Flag analytical views older than the substrate they derive from (D5).

    Read-only. Returns one dict per stale view: {view, owner, view_mtime,
    source_mtime}. cleanup turns these into a single Monday-note line naming
    insight-generator as the owner — it NEVER regenerates them (that's
    insight-generator's expensive synthesis)."""
    root = Path(root)
    src_mtime = _substrate_mtime(root)
    if src_mtime == 0.0:
        return []
    views_dir = root / "_hq" / "views"
    stale: list[dict] = []
    for name in _ANALYTICAL_VIEWS:
        v = views_dir / name
        if not v.is_file():
            continue  # never-generated views aren't "stale"; insight-generator owns first-gen
        if v.stat().st_mtime < src_mtime:
            stale.append({
                "view": name,
                "owner": _ANALYTICAL_OWNER,
                "view_mtime": v.stat().st_mtime,
                "source_mtime": src_mtime,
            })
    return stale


def _insight_last_run_epoch(root: Path) -> float | None:
    """Most recent insight-generator fire, derived from events.jsonl + the
    insights output dir. Returns an epoch seconds float, or None if never run.

    Signals (any of): a `pack_run` event whose kind/source is insight-flavored,
    an insight-pass event type, or the mtime of the newest `_hq/insights/` doc."""
    import json

    latest: float | None = None
    events = root / "_hq" / "data" / "events.jsonl"
    _INSIGHT_TYPES = {"insight", "classification_review", "project_proposal", "org_proposal"}
    if events.is_file():
        from events_io import merged_lines as _merged_lines  # cr1#1061: every computer's log
        for line in _merged_lines(events, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            is_insight = t in _INSIGHT_TYPES
            if t == "pack_run":
                kind = str(data.get("kind") or ev.get("kind") or "")
                src = str(data.get("source_skill") or ev.get("source_skill") or "")
                if "insight" in kind.lower() or "insight" in src.lower():
                    is_insight = True
            if not is_insight:
                continue
            ts = event_time(ev)
            ep = _parse_ts_epoch(ts)
            if ep is not None and (latest is None or ep > latest):
                latest = ep
    insights_dir = root / "_hq" / "insights"
    if insights_dir.is_dir():
        for f in insights_dir.iterdir():
            if f.is_file():
                try:
                    m = f.stat().st_mtime
                except OSError:
                    continue
                if latest is None or m > latest:
                    latest = m
    return latest


def _parse_ts_epoch(ts: Any) -> float | None:
    if not isinstance(ts, str) or not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        pass
    # date-only
    try:
        return datetime.datetime.fromisoformat(s[:10]).timestamp()
    except ValueError:
        return None


def insight_generator_staleness(root: str | Path, now: float | None = None,
                                threshold_days: int = 14) -> dict:
    """Has insight-generator gone quiet for >threshold_days? (D5 nudge condition.)

    Read-only. Returns {stale: bool, last_run_epoch: float|None, age_days,
    threshold_days}. Used to add a Monday-note nudge so a paused insight-generator
    (the forensic root cause of the stale analytical views) surfaces plainly."""
    root = Path(root)
    cutoff_now = time.time() if now is None else now
    last = _insight_last_run_epoch(root)
    if last is None:
        return {"stale": True, "last_run_epoch": None, "age_days": None,
                "threshold_days": threshold_days, "reason": "never"}
    age_days = (cutoff_now - last) / 86400.0
    return {"stale": age_days > threshold_days, "last_run_epoch": last,
            "age_days": age_days, "threshold_days": threshold_days}


def check_aliases_staleness(root: str | Path) -> dict | None:
    """Flag ALIASES.md if it's older than aliases.json (SPEC CLEAN1 / D7).

    There is no standalone aliases-view renderer in shared/scripts (the view is
    owned by people-crm, which regenerates it on any aliases.json write). So this
    is a FLAG-only safety net — cleanup names people-crm as the owner and never
    regenerates the view itself. Returns a dict when stale/missing, else None."""
    root = Path(root)
    aliases_json = root / "_hq" / "data" / "aliases.json"
    view = root / "_hq" / "views" / "ALIASES.md"
    if not aliases_json.is_file():
        return None
    if not view.is_file():
        return {"view": "ALIASES.md", "owner": _ALIASES_OWNER, "reason": "missing"}
    if view.stat().st_mtime < aliases_json.stat().st_mtime:
        return {"view": "ALIASES.md", "owner": _ALIASES_OWNER, "reason": "stale",
                "view_mtime": view.stat().st_mtime,
                "source_mtime": aliases_json.stat().st_mtime}
    return None



# --- T10 (cr1#1019): folder tidy, archive first, never touching live files ----
#
# A large seat holds tens of thousands of files of which a small working set
# matters: over a thousand released lease folders beside the live data (#974
# is the code fix that stops new ones; this tidy moves the ones already
# there), retired unpacked runtime copies under the runtime cache, and backup
# copies past the hygiene rule's three newest per data file.
#
# THE CONTRACT (owner ruling A2: the live data files stay where they are):
#   * Archive first, never delete. Every move is ONE `os.replace` into ONE
#     dated folder, `_archive/cleanup-YYYY-MM-DD/` (a second tidy the same day
#     gets `-2`; the folder is CLAIMED with one `os.mkdir`, so two tidies
#     never share one), mirroring the workspace-relative path the way every
#     archive move in this module does (`_hq` mirrors as `hq-system-locks`,
#     HQRESOLVE1).
#   * The dated folder carries `manifest.json` (machine-readable, written
#     BEFORE the first move and rewritten after the last, so a tidy cut short
#     is still undoable) and `MANIFEST.md` (the same list for a person).
#     `undo_folder_tidy` puts every moved entry back from `manifest.json`,
#     which it treats as data: every entry is fenced again before it moves.
#   * THE FENCE is `_tidy_refusal`, asked by the planner, again by the mover
#     immediately before each move, and again by the undo: a live data file
#     or one of its live-state siblings (`TIDY_LIVE_NAMES`,
#     `_tidy_is_live_name`) is never moved or recreated, whatever folder it
#     sits in and however much it looks like junk; neither is anything that
#     is not the shape its class moves, a folder that holds a live file or a
#     `_hq` folder, a symlink, or anything outside the three tidy areas.
#   * Three classes, each in ONE folder, non-recursive:
#       lease_folder   `_hq/data/*.lease.released.<stamp>-...`, released more
#                      than an hour ago by BOTH its name's stamp and its mtime
#       runtime_copy   `_hq/.cache/cr-runtime/<stage>` and `cr-runtime-<v>.zip`
#                      whose version is BELOW the version before the one the
#                      `current.json` pointer names, older than a day; the
#                      stage in use, its version, the version below it and
#                      anything newer are never moved; no readable pointer ->
#                      the whole class is left alone
#       surplus_backup `_hq/data/_backups/<data file name>.<...>` past the
#                      three newest per data file, grouped by the full data
#                      file name (the prefix `atomic_write`'s restore and
#                      `reconcile_forward._backup_set` read by); any other
#                      backup shape (a dated multi-file set) is left alone
#   * A time budget covers the whole call, planning included; the live-file
#     fingerprint is size and mtime only (no live file is ever opened, so a
#     concurrent write on Windows is never blocked by this tidy).
#   * The runtime-cache exemption above (ACCESS1) still holds for every OTHER
#     sweep. This tidy is its one named exception.
TIDY_BUCKET_PREFIX = "cleanup-"
TIDY_MANIFEST_JSON = "manifest.json"
TIDY_MANIFEST_MD = "MANIFEST.md"
TIDY_KEEP_BACKUPS = 3
TIDY_LEASE_MIN_AGE_S = 3600
TIDY_RUNTIME_MIN_AGE_S = 24 * 3600
#: Moves per call; a call that stops short says how many remain, and the next
#: call carries on (idempotent). Sized for the door's 150 s verb budget.
TIDY_MAX_MOVES = 1500
TIDY_TIME_BUDGET_S = 100.0

#: The live data files (owner ruling A2). Exact names.
TIDY_LIVE_NAMES = frozenset({"entities.json", "events.jsonl", "aliases.json",
                             "processed-meetings.json"})
#: Their live-state siblings, by the shape each writer gives them:
#: `.writer.lock` / `.writer.lock.info` / `.writer.lock.sentinel`
#: (writer_lock), `.source_refs.idx` (source_ref_index), `<ledger>.seqhw`
#: and `<ledger>.seqregression.json` (atomic_write), and the LIVE lease
#: `<ledger>.lease` (lease_lock; a released one carries `.released.`).
TIDY_LIVE_CONTAINS = (".writer.lock", ".source_refs.idx")
TIDY_LIVE_SUFFIXES = (".seqhw", ".seqregression.json", ".lease")

_TIDY_LEASE_DIR = "_hq/data"
_TIDY_BACKUP_DIR = "_hq/data/_backups"
_TIDY_RUNTIME_DIR = RUNTIME_CACHE_REL
_TIDY_AREAS = {"lease_folder": _TIDY_LEASE_DIR,
               "surplus_backup": _TIDY_BACKUP_DIR,
               "runtime_copy": _TIDY_RUNTIME_DIR}
_TIDY_KINDS = ("lease_folder", "runtime_copy", "surplus_backup")
_TIDY_LEASE_INFIX = ".lease.released."
_TIDY_LEASE_STAMP_RE = re.compile(r"\.lease\.released\.(\d{10,})-")
_TIDY_RUNTIME_ZIP_RE = re.compile(r"^cr-runtime-(.+)\.zip$")
_TIDY_RUNTIME_STAGE_RE = re.compile(r"^(.+)-([0-9a-f]{8}|stage)$")
#: Backup groups: the full data file name, followed by a separator.
_TIDY_BACKUP_GROUPS = tuple(sorted(TIDY_LIVE_NAMES, key=len, reverse=True))
_TIDY_KIND_WORDS = {"lease_folder": ("leftover lock folder", "leftover lock folders"),
                    "runtime_copy": ("old plugin copy", "old plugin copies"),
                    "surplus_backup": ("surplus backup", "surplus backups")}
_TIDY_NOT_REACHED = "not reached (time budget)"


def _tidy_is_live_name(name: str) -> bool:
    """True for a live data file or a live-state sibling, by name alone. The
    one predicate the planner, the mover and the undo all ask."""
    if name in TIDY_LIVE_NAMES:
        return True
    if any(tok in name for tok in TIDY_LIVE_CONTAINS):
        return True
    return name.endswith(TIDY_LIVE_SUFFIXES)


def _rel_posix(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _tidy_refusal(root: Path, src: Path, kind: str) -> str | None:
    """Why `src` must NOT be moved by the tidy, or None when it may be.

    Asked for every entry when the plan is made and by the mover immediately
    before its one `os.replace`."""
    if kind not in _TIDY_AREAS:
        return "unknown_kind"
    try:
        if src.is_symlink():
            return "symlink"
        rel = _rel_posix(root, src)
    except (OSError, ValueError):
        return "outside_workspace"
    if rel.split("/", 1)[0] == _ARCHIVE_ROOT:
        return "already_archived"
    if rel.rsplit("/", 1)[0] != _TIDY_AREAS[kind]:
        return "outside_tidy_area"
    if _tidy_is_live_name(src.name):
        return "live_file"
    if not src.exists():
        return "gone"
    shape = _tidy_shape_refusal(src, kind)
    if shape:
        return shape
    if src.is_dir():
        return _tidy_subtree_refusal(src, kind)
    return None


def _tidy_subtree_refusal(src: Path, kind: str) -> str | None:
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        if "_hq" in dirnames:
            return "holds_hq_folder"
        if kind != "runtime_copy" and any(
                _tidy_is_live_name(n) for n in list(dirnames) + filenames):
            # A runtime stage ships seed copies named like the data files
            # (`shared/data-schemas/seed/`); it is fenced by the pointer and
            # the `_hq` check instead.
            return "holds_live_file"
        for n in dirnames:
            if os.path.islink(os.path.join(dirpath, n)):
                return "holds_symlink"
    return None


def _runtime_stage_version(stage: Path) -> str:
    """A stage's plugin version: its own manifest's, else its name less a
    `-<sha8>` suffix (`workspace_access.verify_command`'s naming)."""
    import json as _json
    try:
        v = _json.loads((stage / "manifest.json").read_text(encoding="utf-8")
                        ).get("plugin_version")
        if isinstance(v, str) and v:
            return v
    except (OSError, ValueError, AttributeError):
        pass
    m = _TIDY_RUNTIME_STAGE_RE.match(stage.name)
    return m.group(1) if m else stage.name


def _runtime_keep(cache: Path) -> tuple[dict | None, str | None]:
    """`({in_use, floor}, None)`, or `(None, reason)` when the pointer cannot
    be trusted. `in_use` is the stage the pointer names; `floor` is the
    version below which a stage or zip may retire: the highest version below
    the current one (the version before it stays), else the current one. A
    version at or above the floor never moves."""
    import json as _json
    try:
        pointer = _json.loads((cache / "current.json").read_text(encoding="utf-8"))
        current_version = pointer.get("version")
        current_dir = pointer.get("dir") or current_version
    except (OSError, ValueError, AttributeError):
        return None, "no_current_pointer"
    if (not isinstance(current_dir, str) or not current_dir
            or not isinstance(current_version, str) or not current_version
            or Path(current_dir).name != current_dir
            or not (cache / current_dir).is_dir()):
        return None, "pointer_target_missing"
    versions = set()
    try:
        for d in cache.iterdir():
            if d.is_dir() and not d.is_symlink() and (d / "manifest.json").is_file():
                versions.add(_runtime_stage_version(d))
    except OSError:
        return None, "cache_unreadable"
    older = [v for v in versions if _version_key(v) < _version_key(current_version)]
    floor = max(older, key=_version_key) if older else current_version
    return {"in_use": current_dir, "floor": floor}, None


def _tidy_shape_refusal(src: Path, kind: str) -> str | None:
    """The check that `src` IS what its class moves, so a plan (or a
    manifest) naming anything else in a tidy area is refused: a released
    lease carries `.lease.released.`; a surplus backup is a file named after
    a data file; a runtime copy is a stage folder (it has a `manifest.json`)
    or a `cr-runtime-<v>.zip`, never the stage the pointer names and never a
    version at or above the floor (the pointer re-read here, every time)."""
    name = src.name
    if kind == "lease_folder":
        return None if _TIDY_LEASE_INFIX in name else "wrong_shape"
    if kind == "surplus_backup":
        return None if src.is_file() and _backup_group(name) else "wrong_shape"
    # runtime_copy
    if src.is_file():
        m = _TIDY_RUNTIME_ZIP_RE.match(name)
        if not m:
            return "wrong_shape"
        version = m.group(1)
    elif (src / "manifest.json").is_file():
        version = _runtime_stage_version(src)
    else:
        return "wrong_shape"
    keep, why = _runtime_keep(src.parent)
    if keep is None:
        return why
    if name == keep["in_use"]:
        return "runtime_in_use"
    if _version_key(version) >= _version_key(keep["floor"]):
        return "runtime_kept"
    return None


def _entry_age(p: Path, now: float) -> float | None:
    try:
        return now - p.stat().st_mtime
    except OSError:
        return None


def _lease_age(p: Path, now: float) -> float | None:
    """A released lease's age: the YOUNGER of its mtime and the release time
    stamped in its name (`lease_lock._released_name`: microseconds), so a
    stale lease renamed a moment ago is never taken mid-release."""
    age = _entry_age(p, now)
    m = _TIDY_LEASE_STAMP_RE.search(p.name)
    if m and age is not None:
        try:
            age = min(age, now - int(m.group(1)) / 1_000_000)
        except (ValueError, OverflowError):
            pass
    return age


def _plan_runtime(root: Path, now: float, out: dict) -> None:
    cache = root / _TIDY_RUNTIME_DIR
    if not cache.is_dir():
        return
    keep, why = _runtime_keep(cache)
    if keep is None:
        out["skipped"].append({"kind": "runtime_copy", "reason": why})
        return
    for d in sorted(cache.iterdir()):
        is_stage = d.is_dir() and (d / "manifest.json").is_file()
        is_zip = d.is_file() and _TIDY_RUNTIME_ZIP_RE.match(d.name)
        if not (is_stage or is_zip):
            continue
        version = (_runtime_stage_version(d) if is_stage
                   else _TIDY_RUNTIME_ZIP_RE.match(d.name).group(1))
        if d.name == keep["in_use"] or _version_key(version) >= _version_key(keep["floor"]):
            continue
        age = _entry_age(d, now)
        if age is None or age < TIDY_RUNTIME_MIN_AGE_S:
            continue
        _plan_add(root, d, "runtime_copy", out)


def _backup_group(name: str) -> str | None:
    """The data file a backup belongs to: its name starts with the data
    file's full name and a separator. A file named exactly like a data file
    joins that group, where the fence refuses it if it is ever surplus. None
    for any other name (left alone)."""
    for g in _TIDY_BACKUP_GROUPS:
        if name == g or (name.startswith(g) and name[len(g)] in "._-"):
            return g
    return None


def _plan_backups(root: Path, out: dict) -> None:
    d = root / _TIDY_BACKUP_DIR
    if not d.is_dir():
        return
    groups: dict[str, list] = {}
    for p in d.iterdir():
        if not p.is_file() or p.is_symlink():
            continue
        g = _backup_group(p.name)
        if g is None:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        groups.setdefault(g, []).append((mtime, p.name, p))
    for g in sorted(groups):
        rows = sorted(groups[g], key=lambda r: (r[0], r[1]), reverse=True)
        for _m, _n, p in rows[TIDY_KEEP_BACKUPS:]:  # newest three stay
            _plan_add(root, p, "surplus_backup", out)


def _plan_leases(root: Path, now: float, out: dict) -> None:
    d = root / _TIDY_LEASE_DIR
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        if _TIDY_LEASE_INFIX not in p.name:
            continue
        age = _lease_age(p, now)
        if age is None or age < TIDY_LEASE_MIN_AGE_S:
            continue
        _plan_add(root, p, "lease_folder", out)


def _plan_add(root: Path, p: Path, kind: str, out: dict) -> None:
    why = _tidy_refusal(root, p, kind)
    rel = _rel_posix(root, p)
    if why:
        out["refused"].append({"kind": kind, "path": rel, "reason": why})
        return
    out["moves"].append({"kind": kind, "from": rel, "is_dir": p.is_dir()})


def _tidy_counts(root: Path) -> dict:
    """The re-count: the entries in each tidy folder (not recursive, so it
    stays cheap on a seat with thousands of lease folders). Read-only."""
    out = {"lease_folders": 0, "backups": 0, "runtime_copies": 0,
           "entries_in_tidy_folders": 0}
    n = 0
    d = root / _TIDY_LEASE_DIR
    if d.is_dir():
        names = [p.name for p in d.iterdir()]
        n += len(names)
        out["lease_folders"] = sum(1 for x in names if _TIDY_LEASE_INFIX in x)
    b = root / _TIDY_BACKUP_DIR
    if b.is_dir():
        entries = list(b.iterdir())
        n += len(entries)
        out["backups"] = sum(1 for p in entries if p.is_file())
    c = root / _TIDY_RUNTIME_DIR
    if c.is_dir():
        entries = list(c.iterdir())
        n += len(entries)
        out["runtime_copies"] = sum(
            1 for p in entries
            if (p.is_dir() and (p / "manifest.json").is_file())
            or (p.is_file() and _TIDY_RUNTIME_ZIP_RE.match(p.name)))
    out["entries_in_tidy_folders"] = n
    return out


def _live_fingerprint(root: Path) -> list[dict]:
    """Every live data file and live-state sibling in `_hq/data/` and
    `_hq/.system/`: size and mtime by `stat` alone. No live file is opened
    (on Windows an open handle makes a concurrent `os.replace` onto the file
    fail). Read-only; the manifest carries it before and after."""
    rows = []
    for area in ("_hq/data", "_hq/.system"):
        d = root / area
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not _tidy_is_live_name(p.name):
                continue
            row: dict = {"path": _rel_posix(root, p)}
            try:
                st = p.stat()
                row["size"] = st.st_size
                row["mtime_ns"] = st.st_mtime_ns
            except OSError:
                row["size"] = None
            rows.append(row)
    return rows


def plan_folder_tidy(workspace_root, *, now: float | None = None) -> dict:
    """READ-ONLY. What `tidy_folder` would move, what it refuses and why, and
    the re-count as it stands. Writes nothing, creates nothing.

    Returns `{moves: [{kind, from, is_dir}], refused: [{kind, path, reason}],
    skipped: [{kind, reason}], by_kind: {kind: n}, counts: {...}}`."""
    root = Path(workspace_root)
    now = time.time() if now is None else now
    out: dict = {"moves": [], "refused": [], "skipped": []}
    _plan_leases(root, now, out)
    _plan_runtime(root, now, out)
    _plan_backups(root, out)
    out["by_kind"] = {k: sum(1 for m in out["moves"] if m["kind"] == k)
                      for k in _TIDY_KINDS}
    out["counts"] = _tidy_counts(root)
    return out


def _claim_tidy_bucket(root: Path, today: str) -> str:
    """Claim the first free dated folder, `cleanup-<today>`, then `-2`, `-3`,
    with ONE `os.mkdir` each: a folder that already exists (an earlier tidy,
    a tidy running at the same moment, or a person's own dated cleanup with
    its own MANIFEST.md) is never written into."""
    arch = root / _ARCHIVE_ROOT
    arch.mkdir(parents=True, exist_ok=True)
    base = f"{TIDY_BUCKET_PREFIX}{today}"
    name, n = base, 2
    while True:
        try:
            os.mkdir(arch / name)
            return name
        except FileExistsError:
            name = f"{base}-{n}"
            n += 1


def _tidy_dest(root: Path, src: Path, bucket: str) -> Path:
    """Where `src` goes inside the dated folder: the workspace-relative path,
    `_hq` mirrored as `hq-system-locks` (HQRESOLVE1). No folder is made and no
    suffix is applied here (the mover refuses a taken slot instead)."""
    rel = src.relative_to(root)
    rel = Path(*(_HQ_MIRROR_RENAME if part == "_hq" else part for part in rel.parts))
    return root / _ARCHIVE_ROOT / bucket / rel


def _write_tidy_manifest(root: Path, bucket: str, record: dict) -> None:
    import json as _json

    folder = root / _ARCHIVE_ROOT / bucket
    atomic_write_text(folder / TIDY_MANIFEST_JSON,
                      _json.dumps(record, indent=2, sort_keys=True) + "\n")
    atomic_write_text(folder / TIDY_MANIFEST_MD, _render_tidy_manifest(record))


def _live_row_state(before: dict, after: dict | None) -> str:
    if not after:
        return "missing after the tidy"
    same = all(before.get(k) == after.get(k) for k in ("size", "mtime_ns"))
    return "unchanged" if same else "written by Command Room while the tidy ran; not moved"


def _render_tidy_manifest(record: dict) -> str:
    lines = [f"# Folder tidy, {record.get('date')}", "",
             "Everything below was MOVED here, never deleted. Each line names "
             "where it came from and where it is now. Ask Command Room to "
             "\"undo the folder tidy\" to put every entry back, or move an "
             "entry back by hand.", "",
             f"Status: {record.get('status')}.", ""]
    before = record.get("counts_before") or {}
    after = record.get("counts_after") or {}
    lines += ["## Re-count", "", "| | before | after |", "|---|---|---|"]
    for key, label in (("lease_folders", "Leftover lock folders"),
                       ("runtime_copies", "Plugin copies"),
                       ("backups", "Backups"),
                       ("entries_in_tidy_folders", "Entries in the three tidied folders")):
        lines.append(f"| {label} | {before.get(key, '')} | {after.get(key, '')} |")
    lines += ["", "## Live data files (never moved, never opened)", "",
              "| file | size before | size after | |", "|---|---|---|---|"]
    after_rows = {r["path"]: r for r in record.get("live_after") or []}
    for r in record.get("live_before") or []:
        a = after_rows.get(r["path"])
        state = _live_row_state(r, a) if record.get("live_after") is not None else ""
        lines.append(f"| {r['path']} | {r.get('size')} | {a.get('size') if a else ''} | {state} |")
    for kind in _TIDY_KINDS:
        rows = [m for m in record.get("moves") or [] if m["kind"] == kind]
        if not rows:
            continue
        lines += ["", f"## {_TIDY_KIND_WORDS[kind][1].capitalize()} ({len(rows)})", ""]
        for m in rows:
            lines.append(f"- `{m['from']}` -> `{m['to']}` ({m.get('state', 'planned')})")
    refused = record.get("refused") or []
    if refused:
        lines += ["", f"## Left alone on purpose ({len(refused)})", ""]
        for r in refused:
            lines.append(f"- `{r['path']}`: {r['reason']}")
    if record.get("undo"):
        u = record["undo"]
        lines += ["", "## Undo", "",
                  f"Undone {u.get('at')}: {u.get('restored', 0)} put back, "
                  f"{len(u.get('left') or [])} left where they are."]
    return "\n".join(lines) + "\n"


def _tidy_counts_phrase(by_kind: dict) -> str | None:
    parts = []
    for kind in _TIDY_KINDS:
        n = by_kind.get(kind, 0)
        if n:
            one, many = _TIDY_KIND_WORDS[kind]
            parts.append(f"{n:,} {one if n == 1 else many}")
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _tidy_line(by_kind: dict, bucket: str | None, remaining: int,
               *, dry_run: bool = False) -> str:
    """What a person reads after an attended tidy: counts and the dated
    folder's name, never a path."""
    said = _tidy_counts_phrase(by_kind)
    if said is None:
        return "The folder is already tidy; nothing was moved."
    if dry_run:
        return f"A tidy would move {said} into the archive. Nothing was moved."
    line = (f"Moved {said} into the archive folder {bucket}, with a list of "
            "everything moved. Your data files were not touched.")
    if remaining:
        line += f" {remaining:,} more will move on the next tidy."
    return line


def tidy_folder(workspace_root, *, dry_run: bool = False,
                today: str | None = None, now: float | None = None,
                max_moves: int = TIDY_MAX_MOVES,
                time_budget_s: float = TIDY_TIME_BUDGET_S) -> dict:
    """THE WRITER (on `workspace_access.RUN_WRITER_ALLOWLIST`). Move the
    planned entries into `_archive/cleanup-<today>/`, archive first, each by
    ONE `os.replace`, after writing the manifest. Names its writer first.

    `time_budget_s` covers the whole call from its start, planning included;
    what it does not reach is counted in `remaining` and the next tidy
    carries on. `dry_run=True` answers the plan and writes nothing. A move
    the mount refuses is named in `left` and not retried. Returns `{bucket,
    moved, by_kind, refused, skipped, left, remaining, counts_before,
    counts_after, live_unchanged, dry_run, line, note}`: `line` is for an
    attended ask (it names the dated folder), `note` is the same counts with
    no folder name (the weekly note). `bucket` is None when nothing moved
    (a folder is made only once a move is due; every move the mover then
    refuses is named in its manifest)."""
    from receipts import require_writer_identity

    started = time.monotonic()
    root = Path(workspace_root)
    require_writer_identity(workspace_root=root)
    now = time.time() if now is None else now
    today = today or datetime.date.today().isoformat()
    plan = plan_folder_tidy(root, now=now)
    moves = plan["moves"][:max(0, int(max_moves))]
    result: dict = {"bucket": None, "moved": 0,
                    "by_kind": {k: 0 for k in _TIDY_KINDS},
                    "refused": list(plan["refused"]), "skipped": plan["skipped"],
                    "left": [], "remaining": len(plan["moves"]) - len(moves),
                    "counts_before": plan["counts"], "counts_after": plan["counts"],
                    "live_unchanged": True, "dry_run": bool(dry_run), "note": None}
    if dry_run:
        result["planned"] = plan["moves"]
        result["remaining"] = len(plan["moves"])
        result["line"] = _tidy_line(plan["by_kind"], None, 0, dry_run=True)
        return result
    if not moves or time.monotonic() - started > time_budget_s:
        result["remaining"] = len(plan["moves"])
        result["line"] = (_tidy_line({}, None, 0) if not plan["moves"] else
                          f"The tidy ran out of time before it started; "
                          f"{len(plan['moves']):,} entries will move on the next tidy.")
        return result
    bucket = _claim_tidy_bucket(root, today)
    live_before = _live_fingerprint(root)
    for m in moves:
        m["to"] = _rel_posix(root, _tidy_dest(root, root / m["from"], bucket))
        m["state"] = "planned"
    record = {"kind": "folder_tidy", "issue": "cr1#1019", "date": today,
              "bucket": bucket, "status": "in progress",
              "counts_before": plan["counts"], "live_before": live_before,
              "live_after": None, "moves": moves, "refused": result["refused"],
              "skipped": plan["skipped"]}
    _write_tidy_manifest(root, bucket, record)  # the journal, before any move
    for m in moves:
        if time.monotonic() - started > time_budget_s:
            m["state"] = _TIDY_NOT_REACHED
            continue
        src = root / m["from"]
        why = _tidy_refusal(root, src, m["kind"])  # asked again, at the move
        if why:
            m["state"] = f"refused: {why}"
            result["refused"].append({"kind": m["kind"], "path": m["from"],
                                      "reason": why})
            continue
        dest = root / m["to"]
        if dest.exists() or dest.is_symlink():
            m["state"] = "left: archive slot taken"
            result["left"].append({"path": m["from"], "reason": "dest_exists"})
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(src), str(dest))
        except OSError as exc:
            m["state"] = f"left: {type(exc).__name__}"
            result["left"].append({"path": m["from"], "reason": type(exc).__name__})
            continue
        m["state"] = "moved"
        result["moved"] += 1
        result["by_kind"][m["kind"]] += 1
    not_reached = [m for m in moves if m["state"] == _TIDY_NOT_REACHED]
    result["remaining"] += len(not_reached)
    live_after = _live_fingerprint(root)
    counts_after = _tidy_counts(root)
    result["counts_after"] = counts_after
    result["live_unchanged"] = live_before == live_after
    record.update({"status": "done",
                   "moves": [m for m in moves if m["state"] != _TIDY_NOT_REACHED],
                   "counts_after": counts_after, "live_after": live_after,
                   "refused": result["refused"], "left": result["left"],
                   "remaining": result["remaining"]})
    _write_tidy_manifest(root, bucket, record)
    if result["moved"]:
        result["bucket"] = bucket
        said = _tidy_counts_phrase(result["by_kind"])
        result["note"] = f"moved {said} into the archive"
    result["line"] = _tidy_line(result["by_kind"], bucket, result["remaining"])
    return result


def _newest_tidy_bucket(arch: Path) -> str | None:
    import json as _json

    found = []
    if not arch.is_dir():
        return None
    for d in arch.iterdir():
        mf = d / TIDY_MANIFEST_JSON
        if not (d.name.startswith(TIDY_BUCKET_PREFIX) and mf.is_file()):
            continue
        try:
            rec = _json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("kind") == "folder_tidy" \
                and not rec.get("undo"):
            found.append((str(rec.get("date") or ""), mf.stat().st_mtime, d.name))
    return max(found)[2] if found else None


def _undo_refusal(root: Path, bucket: str, m: dict) -> str | None:
    """Why one manifest entry must NOT go back. The manifest is data: the
    entry is fenced exactly as a forward move would be, by its ORIGINAL
    place (a live name or a live-state sibling is never recreated, whether
    or not one is there now), by its class shape, and by its archived slot
    (inside THIS dated folder, no symlink on the way)."""
    src_rel, dest_rel = str(m.get("from") or ""), str(m.get("to") or "")
    kind = m.get("kind")
    if (kind not in _TIDY_AREAS or not src_rel
            or ".." in Path(src_rel).parts or ".." in Path(dest_rel).parts
            or src_rel.rsplit("/", 1)[0] != _TIDY_AREAS[kind]):
        return "not_this_tidy"
    src = root / src_rel
    dest = root / dest_rel
    if dest != _tidy_dest(root, src, bucket):
        return "not_this_tidy"
    if _tidy_is_live_name(src.name):
        return "live_file"
    if kind == "lease_folder" and _TIDY_LEASE_INFIX not in src.name:
        return "wrong_shape"
    if kind == "surplus_backup" and not _backup_group(src.name):
        return "wrong_shape"
    if kind == "runtime_copy" and dest.exists() and not (
            _TIDY_RUNTIME_ZIP_RE.match(src.name) or (dest / "manifest.json").is_file()):
        return "wrong_shape"
    folder = root / _ARCHIVE_ROOT / bucket
    p = dest
    while p != folder and p != p.parent:
        if p.is_symlink():
            return "symlink"
        p = p.parent
    if dest.is_dir():
        sub = _tidy_subtree_refusal(dest, kind)
        if sub:
            return sub
    return None


def undo_folder_tidy(workspace_root, bucket: str | None = None) -> dict:
    """THE UNDO WRITER (on `RUN_WRITER_ALLOWLIST`). Put every entry a tidy
    moved back where it came from, from that tidy's `manifest.json`. `bucket`
    names the dated folder; None means the newest tidy not yet undone.

    Every entry is fenced again (`_undo_refusal`); an entry whose original
    place is taken again, or whose archived copy is gone, is left and named.
    Nothing is overwritten and nothing is deleted. Returns `{bucket,
    restored, left, line}`."""
    import json as _json

    from receipts import require_writer_identity

    root = Path(workspace_root)
    require_writer_identity(workspace_root=root)
    arch = root / _ARCHIVE_ROOT
    none = {"bucket": bucket, "restored": 0, "left": [],
            "line": "There is no folder tidy to undo."}
    if bucket is None:
        bucket = _newest_tidy_bucket(arch)
        if bucket is None:
            return none
        none["bucket"] = bucket
    if Path(bucket).name != bucket or not bucket.startswith(TIDY_BUCKET_PREFIX):
        return none
    folder = arch / bucket
    try:
        record = _json.loads((folder / TIDY_MANIFEST_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return none
    if not isinstance(record, dict) or record.get("kind") != "folder_tidy":
        return none
    restored, left = 0, []
    for m in record.get("moves") or []:
        if not isinstance(m, dict):
            continue
        src_rel = str(m.get("from") or "")
        dest = root / str(m.get("to") or "")
        why = _undo_refusal(root, bucket, m)  # fenced first: the manifest is data
        if why:
            left.append({"path": src_rel, "reason": why})
            continue
        if not (dest.exists() or dest.is_symlink()):
            if m.get("state") == "moved":
                left.append({"path": src_rel, "reason": "archived_copy_gone"})
            continue
        src = root / src_rel
        if src.exists() or src.is_symlink():
            left.append({"path": src_rel, "reason": "original_place_taken"})
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(dest), str(src))
        except OSError as exc:
            left.append({"path": src_rel, "reason": type(exc).__name__})
            continue
        m["state"] = "restored"
        restored += 1
    record["undo"] = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
                      "restored": restored, "left": left}
    record["status"] = "undone"
    _write_tidy_manifest(root, bucket, record)
    line = (f"Put back {restored:,} moved entr{'y' if restored == 1 else 'ies'}."
            if restored else "Nothing was put back.")
    if left:
        line += f" {len(left):,} could not go back and stay in the archive."
    return {"bucket": bucket, "restored": restored, "left": left, "line": line}
