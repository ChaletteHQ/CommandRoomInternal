#!/usr/bin/env python3
"""
Writer-lock test for events.jsonl (SPEC A1, v3.19.x).

Covers writer_lock.events_writer_lock + its wire-in to
atomic_write.atomic_append_jsonl. stdlib-only, temp workspaces, non-zero exit
on failure (house convention — auto-discovered by run_all.py, unit tier).

THE LOAD-BEARING TEST is test 3, the 8-process concurrent-append torture:
without the lock, atomic_append_jsonl's read->append->os.replace race loses
events (on Windows the racing os.replace also raises PermissionError). Proof
recorded at build time against pre-A1 HEAD: 8x25 concurrent appends yielded
25/200 lines (175 events LOST). With the lock: 200/200, seq 1..200, zero
duplicates, zero gaps.

Tests:
  1. Uncontended acquire/release — lock file exists, .info sidecar written,
     no stats file (uncontended fast path writes no telemetry).
  2. Reentrant acquire in the same process+thread does not deadlock.
  3. Concurrency torture — 8 processes x 25 appends = exactly 200 events,
     seqs exactly 1..200, no dups, no gaps.
  4. Crash-holder recovery — child acquires the OS lock then os._exit(1);
     parent acquires within 2s (kernel released it, zero manual cleanup).
  5. Sentinel fallback — _os_lock_acquire monkeypatched to raise OSError;
     sentinel path used, fallback_sentinel_acquires incremented, a stale
     sentinel (mtime backdated 31s + dead pid) reclaimed.
  6. Timeout — holder process holds the lock 5s; a second acquirer with
     timeout_s=1 raises TimeoutError; lock_stats.timeouts incremented.
  7. Telemetry never raises — force the stats write to raise under real
     contention; the append still succeeds.
  8. Lock-file content never changes across 100 acquire cycles (cloud-sync
     churn check — only the .info sidecar changes).
"""

from __future__ import annotations

# This rig tests the writer-lock/seq machinery BELOW the event gate with
# synthetic fixture types; the gate (strict on both entries as of Phase 4
# 2026-07-02) is covered by run_event_gate_test.py.
import os
os.environ["CR_EVENT_GATE"] = "0"

import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import writer_lock  # noqa: E402
from atomic_write import atomic_append_jsonl  # noqa: E402
from writer_lock import events_writer_lock  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ws() -> Path:
    """A temp workspace with the canonical _hq/data layout so the lock + stats
    land where they would on a real workspace."""
    ws = Path(tempfile.mkdtemp(prefix="cr_writer_lock_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    return ws


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _lock_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / ".writer.lock"


def _stats_path(ws: Path) -> Path:
    return ws / "_hq" / ".system" / "lock_stats.json"


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Test 1 — uncontended acquire/release
# ---------------------------------------------------------------------------

def test_uncontended_acquire_release():
    ws = _ws()
    ep = _events_path(ws)
    with events_writer_lock(ep, holder="test1"):
        pass
    assert _lock_path(ws).exists(), "lock file should exist after acquire"
    info = ws / "_hq" / "data" / ".writer.lock.info"
    assert info.exists(), ".info sidecar should be written on acquire"
    # Uncontended path writes NO telemetry.
    assert not _stats_path(ws).exists(), "uncontended acquire must not write lock_stats.json"
    print("PASS test_uncontended_acquire_release")


# ---------------------------------------------------------------------------
# Test 2 — reentrancy
# ---------------------------------------------------------------------------

def test_reentrant_no_deadlock():
    ws = _ws()
    ep = _events_path(ws)
    with events_writer_lock(ep, holder="outer", timeout_s=2.0):
        with events_writer_lock(ep, holder="inner", timeout_s=2.0):
            # A real append while holding the lock must also not deadlock
            # (atomic_append_jsonl re-enters the same lock).
            atomic_append_jsonl(ep, {"type": "reentrant"})
    events = _read_events(ep)
    assert len(events) == 1 and events[0]["seq"] == 1, events
    print("PASS test_reentrant_no_deadlock")


# ---------------------------------------------------------------------------
# Test 3 — concurrency torture (the load-bearing test)
# ---------------------------------------------------------------------------

def _torture_worker(events_path_str: str, n: int) -> None:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    from atomic_write import atomic_append_jsonl as _aaj
    for i in range(n):
        _aaj(events_path_str, {"type": "torture", "i": i})


def test_concurrency_torture():
    ws = _ws()
    ep = _events_path(ws)
    workers, per = 8, 25
    procs = [mp.Process(target=_torture_worker, args=(str(ep), per)) for _ in range(workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    for p in procs:
        assert p.exitcode == 0, f"worker exited {p.exitcode} (a racing os.replace likely crashed)"

    events = _read_events(ep)
    assert len(events) == workers * per, f"expected {workers * per} events, got {len(events)} (events LOST to the race)"
    seqs = sorted(e["seq"] for e in events)
    assert seqs == list(range(1, workers * per + 1)), (
        f"seqs not exactly 1..{workers * per}; dups/gaps present. "
        f"unique={len(set(seqs))} min={seqs[0]} max={seqs[-1]}"
    )
    print(f"PASS test_concurrency_torture ({workers}x{per} -> {len(events)} events, seq 1..{seqs[-1]}, 0 dups)")


# ---------------------------------------------------------------------------
# Test 4 — crash-holder recovery (kernel releases the OS lock on death)
# ---------------------------------------------------------------------------

def _crash_holder(events_path_str: str, ready_path: str) -> None:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    from writer_lock import events_writer_lock as _ewl
    cm = _ewl(events_path_str, holder="crasher", timeout_s=5.0)
    cm.__enter__()  # acquire and DO NOT release
    Path(ready_path).write_text("held", encoding="utf-8")
    time.sleep(30)  # parent kills us via os._exit in the child wrapper below


def _crash_holder_entry(events_path_str: str, ready_path: str) -> None:
    # Acquire, signal ready, then hard-exit WITHOUT releasing — the kernel
    # must drop the lock on process death.
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    from writer_lock import events_writer_lock as _ewl
    cm = _ewl(events_path_str, holder="crasher", timeout_s=5.0)
    cm.__enter__()
    Path(ready_path).write_text("held", encoding="utf-8")
    os._exit(1)


def test_crash_holder_recovery():
    ws = _ws()
    ep = _events_path(ws)
    ready = ws / "ready.flag"
    child = mp.Process(target=_crash_holder_entry, args=(str(ep), str(ready)))
    child.start()
    # Wait until the child has acquired + crashed.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not ready.exists():
        time.sleep(0.02)
    child.join(5)
    assert ready.exists(), "crash holder never signalled acquisition"

    t0 = time.monotonic()
    with events_writer_lock(ep, holder="recoverer", timeout_s=5.0):
        elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"recovery took {elapsed:.2f}s, expected < 2s (kernel should have released on crash)"
    print(f"PASS test_crash_holder_recovery (reacquired in {elapsed:.2f}s, no manual cleanup)")


# ---------------------------------------------------------------------------
# Test 5 — sentinel fallback on OSError + stale-sentinel reclaim
# ---------------------------------------------------------------------------

def test_sentinel_fallback():
    ws = _ws()
    ep = _events_path(ws)
    lock_dir = ep.parent

    # Pre-plant a STALE sentinel: lock file with a dead pid + mtime 31s old.
    sentinel_lock = lock_dir / ".writer.lock.sentinel.lock"
    dead_pid = 999999  # not a live process
    sentinel_lock.write_text(json.dumps({"pid": dead_pid, "holder": "ghost",
                                          "acquired_at": "2000-01-01T00:00:00"}), encoding="utf-8")
    old = time.time() - 31
    os.utime(sentinel_lock, (old, old))

    orig = writer_lock._os_lock_acquire

    def boom(fd):
        raise OSError("mount refuses byte-range locks")

    writer_lock._os_lock_acquire = boom
    try:
        with events_writer_lock(ep, holder="fallback-test", timeout_s=5.0):
            atomic_append_jsonl(ep, {"type": "via_sentinel"})
    finally:
        writer_lock._os_lock_acquire = orig

    # The append landed.
    events = _read_events(ep)
    assert len(events) == 1 and events[0]["type"] == "via_sentinel", events
    # fallback counter incremented.
    stats = json.loads(_stats_path(ws).read_text(encoding="utf-8"))
    assert stats.get("fallback_sentinel_acquires", 0) >= 1, f"fallback not recorded: {stats}"
    print("PASS test_sentinel_fallback (stale sentinel reclaimed, fallback recorded, append landed)")


# ---------------------------------------------------------------------------
# Test 6 — timeout
# ---------------------------------------------------------------------------

def _hold_5s(events_path_str: str, ready_path: str) -> None:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    from writer_lock import events_writer_lock as _ewl
    with _ewl(events_path_str, holder="holder", timeout_s=5.0):
        Path(ready_path).write_text("held", encoding="utf-8")
        time.sleep(5)


def test_timeout_raises_and_records():
    ws = _ws()
    ep = _events_path(ws)
    ready = ws / "ready.flag"
    holder = mp.Process(target=_hold_5s, args=(str(ep), str(ready)))
    holder.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists(), "holder never acquired"

        raised = False
        try:
            with events_writer_lock(ep, holder="impatient", timeout_s=1.0):
                pass
        except TimeoutError:
            raised = True
        assert raised, "expected TimeoutError when lock held by another process"
        stats = json.loads(_stats_path(ws).read_text(encoding="utf-8"))
        assert stats.get("timeouts", 0) >= 1, f"timeout not recorded: {stats}"
    finally:
        holder.join(10)
    print("PASS test_timeout_raises_and_records")


# ---------------------------------------------------------------------------
# Test 7 — telemetry never raises (a broken stats write can't break a write)
# ---------------------------------------------------------------------------

def _hold_briefly(events_path_str: str, ready_path: str, hold_s: float) -> None:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    from writer_lock import events_writer_lock as _ewl
    with _ewl(events_path_str, holder="holder", timeout_s=5.0):
        Path(ready_path).write_text("held", encoding="utf-8")
        time.sleep(hold_s)


def test_telemetry_never_raises():
    ws = _ws()
    ep = _events_path(ws)
    ready = ws / "ready.flag"

    # Force EVERY stats write to raise, then drive a CONTENDED acquire (a
    # holder process holds the lock ~0.4s, so the second acquirer waits >100ms
    # and would try to record telemetry). The append must still succeed.
    orig = writer_lock.atomic_write_json

    def boom(*a, **k):
        raise RuntimeError("stats disk full")

    writer_lock.atomic_write_json = boom
    holder = mp.Process(target=_hold_briefly, args=(str(ep), str(ready), 0.4))
    holder.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        # This acquire waits for the holder to release, then records telemetry
        # (which raises) — the exception must be swallowed and the append land.
        atomic_append_jsonl(ep, {"type": "survived_broken_telemetry"})
    finally:
        writer_lock.atomic_write_json = orig
        holder.join(10)

    events = _read_events(ep)
    types = [e["type"] for e in events]
    assert "survived_broken_telemetry" in types, f"append lost when telemetry broke: {events}"
    print("PASS test_telemetry_never_raises")


# ---------------------------------------------------------------------------
# Test 8 — lock-file content never changes (cloud-sync churn check)
# ---------------------------------------------------------------------------

def test_lock_file_content_stable():
    ws = _ws()
    ep = _events_path(ws)
    with events_writer_lock(ep, holder="seed"):
        pass
    lp = _lock_path(ws)
    content0 = lp.read_bytes()
    size0 = lp.stat().st_size
    for _ in range(100):
        with events_writer_lock(ep, holder="cycle"):
            pass
    assert lp.read_bytes() == content0, "lock file CONTENT changed across cycles (sync churn risk)"
    assert lp.stat().st_size == size0, "lock file SIZE changed across cycles (sync churn risk)"
    assert content0 == b"L", f"lock file should be the single sentinel byte, got {content0!r}"
    print("PASS test_lock_file_content_stable (content byte-identical across 100 cycles)")


def main():
    test_uncontended_acquire_release()
    test_reentrant_no_deadlock()
    test_concurrency_torture()
    test_crash_holder_recovery()
    test_sentinel_fallback()
    test_timeout_raises_and_records()
    test_telemetry_never_raises()
    test_lock_file_content_stable()
    print()
    print("OK — all 8 writer_lock tests passed.")


if __name__ == "__main__":
    main()
