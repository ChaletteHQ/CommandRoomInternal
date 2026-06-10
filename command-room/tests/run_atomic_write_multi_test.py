#!/usr/bin/env python3
"""
Structural test for the v3.13.8 multi_write_context (Bug #20 + #18 + #21).

Verifies:
  1. Multiple writes inside one `with multi_write_context(...)` block succeed
     without deadlock (Bug #20 — same-process multi-write deadlock).
  2. A stale lock left by a dead pid is auto-reclaimed (Bug #18).
  3. A time-stale lock (>stale_after_s old) is auto-reclaimed even if the pid
     appears alive (Bug #18 belt-and-suspenders fallback).
  4. release_write_lock survives an OSError-on-unlink scenario via mv-aside
     fallback (Bug #21 — sandbox mount).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import atomic_write  # noqa: E402
from atomic_write import (  # noqa: E402
    AtomicWriteLockError,
    atomic_write_json,
    multi_write_context,
    release_write_lock,
)


def _setup_workspace() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="cr_multiwrite_test_"))
    (tmp / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    return tmp


def test_basic_multi_write() -> None:
    ws = _setup_workspace()
    target_a = ws / "_hq" / "data" / "a.json"
    target_b = ws / "_hq" / "data" / "b.json"
    target_c = ws / "_hq" / "data" / "c.json"

    with multi_write_context(ws, holder="test_basic") as lock_path:
        assert lock_path.exists(), "lock should be held during the with-block"
        atomic_write_json(target_a, {"step": 1})
        atomic_write_json(target_b, {"step": 2})
        atomic_write_json(target_c, {"step": 3})

    # Lock released on exit
    assert not lock_path.exists(), "lock should be released after the with-block"
    # All three writes landed
    assert json.loads(target_a.read_text()) == {"step": 1}
    assert json.loads(target_b.read_text()) == {"step": 2}
    assert json.loads(target_c.read_text()) == {"step": 3}
    print("PASS test_basic_multi_write")


def test_dead_pid_lock_reclaim() -> None:
    ws = _setup_workspace()
    lock_path = ws / "_hq" / ".system" / "atomic.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Plant a fake lock owned by a pid that is almost certainly not alive.
    # Use a very large pid number; on rare collision the time-stale fallback
    # still kicks in.
    fake_payload = {
        "pid": 999999999,
        "holder": "ghost",
        "acquired_at": "2026-01-01T00:00:00",
    }
    lock_path.write_text(json.dumps(fake_payload), encoding="utf-8")
    assert lock_path.exists()

    # acquire_write_lock should reclaim and succeed within timeout
    with multi_write_context(ws, holder="reclaimer", timeout_s=5.0):
        assert lock_path.exists()
        # We hold it now — read the payload and verify we are the owner
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["holder"] == "reclaimer", payload
        assert payload["pid"] == os.getpid()
    print("PASS test_dead_pid_lock_reclaim")


def test_time_stale_lock_reclaim() -> None:
    ws = _setup_workspace()
    lock_path = ws / "_hq" / ".system" / "atomic.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Plant a lock owned by the current pid (so pid-liveness check returns
    # True), but with an `acquired_at` deep in the past — the time-stale
    # backstop should reclaim it.
    fake_payload = {
        "pid": os.getpid(),
        "holder": "ancient_self",
        "acquired_at": "2020-01-01T00:00:00",
    }
    lock_path.write_text(json.dumps(fake_payload), encoding="utf-8")

    # stale_after_s=10 means anything >10s old is reclaimed
    with multi_write_context(ws, holder="reclaimer", timeout_s=5.0, stale_after_s=10.0):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["holder"] == "reclaimer", payload
    print("PASS test_time_stale_lock_reclaim")


def test_release_lock_unlink_failure_mv_aside() -> None:
    """Bug #21 — when unlink raises OSError (sandbox mount), the release path
    falls back to rename. Simulate by monkey-patching Path.unlink."""
    ws = _setup_workspace()
    lock_path = ws / "_hq" / ".system" / "atomic.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": os.getpid(), "holder": "x", "acquired_at": "2026-01-01T00:00:00"}))

    original_unlink = Path.unlink

    def boom(self, *args, **kwargs):
        if self == lock_path:
            raise OSError("simulated sandbox unlink refusal")
        return original_unlink(self, *args, **kwargs)

    Path.unlink = boom  # type: ignore
    try:
        release_write_lock(lock_path)
    finally:
        Path.unlink = original_unlink  # type: ignore

    # The original lock should be gone (renamed aside)
    assert not lock_path.exists(), "lock should have been renamed aside"
    # A .stale.* sibling should exist
    siblings = [p for p in lock_path.parent.iterdir() if p.name.startswith("atomic.lock")]
    assert any(".stale." in s.name for s in siblings), (
        f"expected a .stale.* mv-aside file; saw {[s.name for s in siblings]}"
    )
    print("PASS test_release_lock_unlink_failure_mv_aside")


def test_corrupt_lock_payload_reclaim() -> None:
    """A lock with unparseable JSON payload is treated as corrupt and reclaimed."""
    ws = _setup_workspace()
    lock_path = ws / "_hq" / ".system" / "atomic.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("not-json-at-all garbage", encoding="utf-8")

    with multi_write_context(ws, holder="reclaimer", timeout_s=3.0):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["holder"] == "reclaimer"
    print("PASS test_corrupt_lock_payload_reclaim")


def main() -> int:
    test_basic_multi_write()
    test_dead_pid_lock_reclaim()
    test_time_stale_lock_reclaim()
    test_release_lock_unlink_failure_mv_aside()
    test_corrupt_lock_payload_reclaim()
    print("\nALL atomic_write multi_write_context tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
