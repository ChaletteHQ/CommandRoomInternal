#!/usr/bin/env python3
"""
Cross-platform advisory WRITER LOCK for events.jsonl (SPEC A1, v3.19.x).

WHAT THIS CLOSES
----------------
`atomic_append_jsonl` reads events.jsonl whole, appends in memory, and atomic-
renames the result. It is NOT an O_APPEND write. Two callers racing the
read->rename produce last-writer-wins: B's rename silently overwrites A's
event (event loss), or both reserve the same seq (duplicate seq). On Windows
the racing `os.replace` can even raise `PermissionError [WinError 5]` because
the destination is held open by the other process. This was RELIABILITY.md
§3 "known gap (v3.6.1)", deferred since then.

This module serializes the entire read->stamp->rename sequence behind a single
OS-level advisory lock so seq reservation and the write are one critical
section. It is wired into `atomic_write.atomic_append_jsonl` for
`events.jsonl` writes only (caller-agnostic — every skill that already calls
the helper gets the lock for free, no SKILL.md edits).

DESIGN (see SPEC_A1_writer_lock.md §3)
--------------------------------------
1. **Primary mechanism — OS byte-range lock on a dedicated, never-rewritten
   file `_hq/data/.writer.lock`.** `fcntl.flock(LOCK_EX|LOCK_NB)` on POSIX,
   `msvcrt.locking(LK_NBLCK, 1 byte)` on Windows. The kernel releases an OS
   lock automatically when the holder process dies, so a crashed writer needs
   ZERO manual cleanup — this is the property a sentinel file can only
   approximate. No external dependency (`portalocker` rejected — plugin is
   stdlib-only); the historical "platform split" objection is a ~15-line
   `if os.name == "nt"` branch, not a dependency.

2. **Cloud-sync safety (CLIENT SAFETY, mandatory).** The lock file's CONTENT
   never changes and the file is never renamed or deleted. OneDrive/Drive sync
   file content, not kernel lock state; a zero-churn lock file generates no
   sync traffic and no sync conflicts on the 5 live client workspaces. The
   file is created once with a single byte `b"L"` (Windows needs >=1 byte to
   lock byte 0) and never written again. Holder diagnostics (pid/holder/
   acquired_at) go to a SIBLING file `.writer.lock.info` — best-effort, for
   humans debugging, never read by the locking logic.

3. **Fallback — sentinel lock when the OS lock syscall raises OSError** (some
   network / virtual mounts refuse byte-range locks). Falls back to the
   existing `atomic_write.acquire_write_lock` / `release_write_lock` sentinel
   pair against `.writer.lock.sentinel`, stale_after_s=30, with a pid-liveness
   pre-reclaim (dead holder -> reclaim immediately) layered on top of the 30s
   mtime backstop. Strictly better than today (no lock at all); telemetry
   records when the fallback fires.

4. **30s timeout, jittered retry.** Poll the non-blocking acquire; on
   contention sleep `random.uniform(0.05, cap)` with `cap` doubling to a 1.0s
   ceiling. Jitter prevents lockstep retries (scheduled task + on-demand skill
   firing together — the realistic 7:00 morning-brief/inbox collision).

5. **Reentrancy.** Per-process/thread reentrant via a `threading.local` depth
   counter, so a skill that calls `atomic_append_jsonl` while already inside
   `events_writer_lock` does not deadlock itself (multi_write_context Bug #20
   lesson, applied preemptively).

6. **Contention telemetry — best-effort counters in
   `_hq/.system/lock_stats.json`** (`waits`, `total_wait_ms`, `timeouts`,
   `fallback_sentinel_acquires`, `last_timeout`). Written ONLY when a wait
   exceeded 100ms (no write on the uncontended fast path). `cleanup` surfaces
   these in its Monday note and resets them. All telemetry exceptions are
   swallowed — telemetry must NEVER break a write.

LOCK-ORDERING (deadlock avoidance)
----------------------------------
entities.json / aliases.json keep their own sentinel lock
(`atomic_write.acquire_write_lock`). A process that holds BOTH must acquire the
events writer lock SECOND (entities lock first, events lock last) to avoid an
ordering deadlock. Migrating entities.json to OS locks is a follow-up; this
module is the canonical lock for new code.
"""

from __future__ import annotations

import errno
import json
import os
import random
import threading
import time
from contextlib import contextmanager
from pathlib import Path

# These are all defined at atomic_write import time and atomic_write does NOT
# import this module at top level, so there is no import cycle (the wire-in in
# atomic_append_jsonl imports events_writer_lock lazily).
from atomic_write import (  # noqa: E402
    _pid_alive,
    _read_lock_payload,
    acquire_write_lock,
    atomic_write_json,
    atomic_write_text,
    release_write_lock,
)

LOCK_FILENAME = ".writer.lock"
INFO_FILENAME = ".writer.lock.info"
SENTINEL_FILENAME = ".writer.lock.sentinel"
STATS_RELPATH = ("_hq", ".system", "lock_stats.json")

# Errnos that mean "the region is locked by someone else" (retry), as opposed
# to "this mount does not support byte-range locking" (fall back to sentinel).
# POSIX flock -> EWOULDBLOCK/EAGAIN. Windows msvcrt.locking(LK_NBLCK) on a
# locked region -> EACCES (13) or EDEADLOCK (36).
_WOULDBLOCK_ERRNOS = {
    getattr(errno, "EWOULDBLOCK", 11),
    getattr(errno, "EAGAIN", 11),
    getattr(errno, "EACCES", 13),
    getattr(errno, "EDEADLK", 36),
    getattr(errno, "EDEADLOCK", 36),
}

_local = threading.local()


class _WouldBlock(Exception):
    """The OS lock is held by another process — retry. (Distinct from an
    OSError meaning the mount can't do byte-range locks, which triggers the
    sentinel fallback.)"""


# ---------------------------------------------------------------------------
# OS-level lock primitives (the only platform-split in the module)
# ---------------------------------------------------------------------------

def _os_lock_acquire(fd: int) -> None:
    """Acquire an exclusive, NON-BLOCKING OS lock on byte 0 (length 1) of `fd`.

    Raises `_WouldBlock` if another holder owns the region (caller retries).
    Raises `OSError` if the OS / mount does not support byte-range locking
    (caller falls back to the sentinel lock).
    """
    if os.name == "nt":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as e:
            if e.errno in _WOULDBLOCK_ERRNOS:
                raise _WouldBlock() from e
            raise
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in _WOULDBLOCK_ERRNOS:
                raise _WouldBlock() from e
            raise


def _os_lock_release(fd: int) -> None:
    """Release the OS lock held on `fd`. Best-effort — the kernel also
    releases on close()/process-death, so a failure here is non-fatal."""
    if os.name == "nt":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Path resolution + lock-file creation
# ---------------------------------------------------------------------------

def _resolve(workspace_root_or_events_path) -> tuple[Path, Path]:
    """Return (lock_path, workspace_root) from either the events.jsonl path or
    a workspace root. The lock always lives BESIDE events.jsonl (i.e.
    `_hq/data/.writer.lock` in normal use)."""
    p = Path(workspace_root_or_events_path)
    if p.name in (LOCK_FILENAME, INFO_FILENAME, SENTINEL_FILENAME):
        lock_dir = p.parent
    elif p.name == "events.jsonl" or p.suffix == ".jsonl":
        lock_dir = p.parent
    elif p.is_file():
        lock_dir = p.parent
    else:
        # Treated as a workspace root.
        lock_dir = p / "_hq" / "data"
    lock_path = lock_dir / LOCK_FILENAME
    return lock_path, _root_from_lock_dir(lock_dir)


def _root_from_lock_dir(lock_dir: Path) -> Path:
    """Derive the workspace root (the dir containing `_hq/`) from the lock dir,
    which is normally `<root>/_hq/data`. Best-effort — only telemetry depends
    on it, and telemetry swallows all errors."""
    if lock_dir.name == "data" and lock_dir.parent.name == "_hq":
        return lock_dir.parent.parent
    return lock_dir


def _ensure_lock_file(lock_path: Path) -> None:
    """Ensure the lock file exists with at least 1 byte of content. Writes the
    single byte `b"L"` ONLY on first creation (size < 1); once the file exists
    its content NEVER changes again (zero sync churn — CLIENT SAFETY)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock_path.stat().st_size >= 1:
            return
    except OSError:
        pass
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_size < 1:
            os.write(fd, b"L")
            try:
                os.fsync(fd)
            except OSError:
                pass
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Telemetry — best-effort, swallows every exception
# ---------------------------------------------------------------------------

def _record_lock_stats(workspace_root, **deltas) -> None:
    """Load-merge-atomic_write `_hq/.system/lock_stats.json`. Numeric deltas
    accumulate; non-numeric values (e.g. `last_timeout` dict) replace. Swallows
    ALL exceptions — telemetry must never break a write."""
    try:
        stats_path = Path(workspace_root).joinpath(*STATS_RELPATH)
        cur: dict = {}
        if stats_path.exists():
            try:
                loaded = json.loads(stats_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cur = loaded
            except (OSError, json.JSONDecodeError):
                cur = {}
        for k, v in deltas.items():
            if isinstance(v, bool):
                cur[k] = v
            elif isinstance(v, (int, float)):
                cur[k] = (cur.get(k, 0) or 0) + v
            else:
                cur[k] = v
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(stats_path, cur)
    except Exception:
        pass


def _write_info_sidecar(lock_path: Path, holder: str, mode: str) -> None:
    """Best-effort holder diagnostics to `.writer.lock.info` (a sibling file,
    NOT the lock file). Never read by the locking logic; swallows errors."""
    try:
        import datetime as _dt

        info = {
            "pid": os.getpid(),
            "holder": holder,
            "acquired_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
        }
        atomic_write_text(
            lock_path.parent / INFO_FILENAME,
            json.dumps(info) + "\n",
        )
    except Exception:
        pass


def _read_info_holder(lock_path: Path) -> str:
    """Best-effort read of the current holder name from `.writer.lock.info`,
    for the TimeoutError message + telemetry `blocked_by`."""
    try:
        raw = (lock_path.parent / INFO_FILENAME).read_text(encoding="utf-8").strip()
        info = json.loads(raw)
        if isinstance(info, dict):
            return f"{info.get('holder', '?')} (pid {info.get('pid', '?')})"
    except Exception:
        pass
    return "(unknown)"


# ---------------------------------------------------------------------------
# Thread-local reentrancy state
# ---------------------------------------------------------------------------

def _state() -> dict:
    d = getattr(_local, "locks", None)
    if d is None:
        d = {}
        _local.locks = d
    return d


# ---------------------------------------------------------------------------
# Acquire / release
# ---------------------------------------------------------------------------

def _acquire_os(lock_path: Path, root: Path, holder: str, timeout_s: float) -> dict:
    """Acquire the primary OS lock with jittered retry. On OSError from the
    lock syscall (mount can't lock), fall back to the sentinel. Raises
    TimeoutError if the region stays contended past `timeout_s`."""
    _ensure_lock_file(lock_path)
    start = time.monotonic()
    cap = 0.25
    waited = False
    while True:
        try:
            fd = os.open(str(lock_path), os.O_RDWR)
        except FileNotFoundError:
            # OneDrive "files on-demand" can dehydrate / drop the file between
            # iterations — recreate and retry, never crash (SPEC §8).
            _ensure_lock_file(lock_path)
            continue
        try:
            _os_lock_acquire(fd)
        except _WouldBlock:
            os.close(fd)
            if time.monotonic() - start > timeout_s:
                wait_ms = (time.monotonic() - start) * 1000.0
                blocked_by = _read_info_holder(lock_path)
                _record_lock_stats(
                    root,
                    timeouts=1,
                    last_timeout={
                        "ts": _now_iso(),
                        "holder": holder,
                        "blocked_by": blocked_by,
                    },
                )
                raise TimeoutError(
                    f"events.jsonl writer lock held by another process: "
                    f"{blocked_by}. Waited {timeout_s:.1f}s. Retry, or check "
                    f"for a hung writer."
                )
            waited = True
            time.sleep(random.uniform(0.05, cap))
            cap = min(cap * 2.0, 1.0)
            continue
        except OSError:
            # The mount refuses byte-range locks — fall back to the sentinel.
            os.close(fd)
            return _acquire_sentinel(lock_path, root, holder, timeout_s, start, waited)
        # Acquired.
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if waited and elapsed_ms > 100.0:
            _record_lock_stats(root, waits=1, total_wait_ms=int(elapsed_ms))
        _write_info_sidecar(lock_path, holder, "os")
        return {"mode": "os", "fd": fd}


def _acquire_sentinel(
    lock_path: Path,
    root: Path,
    holder: str,
    timeout_s: float,
    start: float,
    waited: bool,
) -> dict:
    """Fallback path: the existing sentinel lock against `.writer.lock.sentinel`
    with stale_after_s=30, plus a pid-liveness pre-reclaim (dead holder ->
    reclaim immediately, don't wait out the 30s mtime backstop)."""
    sentinel_target = lock_path.parent / SENTINEL_FILENAME
    sentinel_lock = lock_path.parent / (SENTINEL_FILENAME + ".lock")
    try:
        if sentinel_lock.exists():
            pid, _epoch = _read_lock_payload(sentinel_lock)
            if pid and not _pid_alive(pid):
                release_write_lock(sentinel_lock)
    except Exception:
        pass
    # acquire_write_lock raises TimeoutError with an actionable message if the
    # sentinel stays held; let that propagate (record the timeout first).
    try:
        remaining = max(0.5, timeout_s - (time.monotonic() - start))
        lock_file = acquire_write_lock(
            sentinel_target, holder=holder, timeout_s=remaining, stale_after_s=30.0
        )
    except TimeoutError:
        _record_lock_stats(
            root,
            timeouts=1,
            last_timeout={"ts": _now_iso(), "holder": holder, "blocked_by": "(sentinel)"},
        )
        raise
    elapsed_ms = (time.monotonic() - start) * 1000.0
    deltas: dict = {"fallback_sentinel_acquires": 1}
    if waited and elapsed_ms > 100.0:
        deltas["waits"] = 1
        deltas["total_wait_ms"] = int(elapsed_ms)
    _record_lock_stats(root, **deltas)
    _write_info_sidecar(lock_path, holder, "sentinel")
    return {"mode": "sentinel", "lock_file": lock_file}


def _release(entry: dict) -> None:
    """Release a lock acquired via _acquire_os/_acquire_sentinel. Unlock THEN
    close (SPEC §8 ordering note)."""
    mode = entry.get("mode")
    if mode == "os":
        fd = entry.get("fd")
        if fd is not None:
            try:
                _os_lock_release(fd)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
    elif mode == "sentinel":
        lock_file = entry.get("lock_file")
        if lock_file is not None:
            release_write_lock(lock_file)


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@contextmanager
def events_writer_lock(workspace_root_or_events_path, holder: str = "unknown", timeout_s: float = 30.0):
    """Hold the events.jsonl writer lock for the duration of the `with` block.

    Accepts either the events.jsonl path (normal — the lock lands beside it) or
    a workspace root (the lock resolves to `<root>/_hq/data/.writer.lock`).

    Reentrant per process+thread: a nested call while the lock is already held
    increments a depth counter and yields without re-acquiring, so it can never
    deadlock itself.

    Raises TimeoutError if the lock stays contended past `timeout_s`. Falls back
    to a sentinel lock if the OS lock syscall raises OSError (unsupported
    mount). Public so read-check-then-append callers (e.g. the A3 dedup index)
    can wrap their whole critical section in the same lock.
    """
    lock_path, root = _resolve(workspace_root_or_events_path)
    key = str(lock_path)
    state = _state()

    entry = state.get(key)
    if entry is not None:
        # Reentrant — already held by this thread. Just count depth.
        entry["depth"] += 1
        try:
            yield lock_path
        finally:
            entry["depth"] -= 1
        return

    acquired = _acquire_os(lock_path, root, holder, timeout_s)
    acquired["depth"] = 1
    state[key] = acquired
    try:
        yield lock_path
    finally:
        state.pop(key, None)
        _release(acquired)


__all__ = ["events_writer_lock", "LOCK_FILENAME"]
