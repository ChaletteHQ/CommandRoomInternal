#!/usr/bin/env python3
"""
F-11 regression test — stale writer-lock litter (integration-2026-07).

THE BUG: one multi-write session (bridge + maintenance window) left seven
`entities.json.lock.stale.*` files in `_hq/data/`. Root cause was NOT crashed
writers or lock takeover — every write succeeded. On a Drive-synced workspace
the sync client briefly holds each freshly-created lock sentinel, so the
release-time `unlink` gets OSError-refused and the pre-fix code mv-asided
IMMEDIATELY — one `.stale.*` file per write in the burst. Separately, the
stale-RECLAIM path in acquire_write_lock called bare `unlink()` — a refused
unlink there crashed the acquiring writer outright.

THE FIX (v4.8.1): `_clear_lock_file` — unlink with brief retries (the sync
hold is transient, ms-scale), mv-aside only as the backstop; used by both
release_write_lock and acquire_write_lock's stale-reclaim, which now also
falls into the normal timeout instead of crashing/spinning when a mount
refuses both unlink and rename. The weekly sweep half of F-11 already shipped
in HYG1 (cleanup Rule 9 / cleanup_actions.sweep_stale_locks) — test 5 proves
the end-to-end chain: whatever still litters gets archived.

stdlib-only, temp dirs, non-zero exit on failure (house convention —
auto-discovered by run_all.py, unit tier).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import atomic_write  # noqa: E402
from atomic_write import (  # noqa: E402
    _clear_lock_file,
    acquire_write_lock,
    release_write_lock,
)


class RefusingPath:
    """Wraps a real lock path; unlink raises OSError `refusals` times before
    delegating. Mimics a cloud-sync client transiently holding the file."""

    def __init__(self, real: Path, refusals: int):
        self._real = real
        self.refusals_left = refusals

    def unlink(self):
        if self.refusals_left > 0:
            self.refusals_left -= 1
            raise OSError(13, "unlink refused (sync client holds the file)")
        self._real.unlink()

    @property
    def suffix(self):
        return self._real.suffix

    @property
    def name(self):
        return self._real.name

    def with_suffix(self, s):
        return self._real.with_suffix(s)

    def __str__(self):
        return str(self._real)

    def __fspath__(self):
        return str(self._real)


def _make_lock(d: Path, name: str = "entities.json.lock") -> Path:
    p = d / name
    p.write_text('{"pid": 1, "holder": "test"}', encoding="utf-8")
    return p


def _stale_files(d: Path) -> list[Path]:
    return sorted(d.glob("*.lock.stale.*"))


def test_transient_refusal_retried_no_litter():
    print("test_transient_refusal_retried_no_litter")
    d = Path(tempfile.mkdtemp(prefix="cr_f11_"))
    lock = _make_lock(d)
    wrapped = RefusingPath(lock, refusals=2)  # refused twice, third try lands
    assert _clear_lock_file(wrapped) is True
    assert not lock.exists(), "lock must be deleted after retries"
    assert _stale_files(d) == [], "a transient hold must NOT litter a .stale file"


def test_persistent_refusal_mv_aside_deterministic_name():
    print("test_persistent_refusal_mv_aside_deterministic_name")
    d = Path(tempfile.mkdtemp(prefix="cr_f11_"))
    lock = _make_lock(d)
    wrapped = RefusingPath(lock, refusals=999)
    assert _clear_lock_file(wrapped) is True
    assert not lock.exists(), "original lock must be out of the way"
    stales = _stale_files(d)
    assert len(stales) == 1, stales
    assert stales[0].name.startswith("entities.json.lock.stale."), stales[0].name


def test_release_is_idempotent_and_clean_on_normal_mounts():
    print("test_release_is_idempotent_and_clean_on_normal_mounts")
    d = Path(tempfile.mkdtemp(prefix="cr_f11_"))
    target = d / "entities.json"
    target.write_text("{}", encoding="utf-8")
    lock = acquire_write_lock(target, holder="t3", timeout_s=2.0)
    release_write_lock(lock)
    release_write_lock(lock)  # second release: silent no-op
    assert not lock.exists()
    assert _stale_files(d) == [], "normal path must never litter"


def test_stale_reclaim_survives_refused_unlink():
    print("test_stale_reclaim_survives_refused_unlink")
    d = Path(tempfile.mkdtemp(prefix="cr_f11_"))
    target = d / "entities.json"
    target.write_text("{}", encoding="utf-8")
    stale_lock = _make_lock(d)
    backdate = time.time() - 120
    os.utime(stale_lock, (backdate, backdate))  # stale (> default 60s)

    orig_unlink = Path.unlink

    def refusing_unlink(self, *a, **k):
        if self.name == "entities.json.lock":
            raise OSError(13, "unlink refused (sync client)")
        return orig_unlink(self, *a, **k)

    Path.unlink = refusing_unlink
    try:
        # Pre-fix: bare unlink() OSError propagated and CRASHED the writer.
        # Post-fix: reclaim mv-asides and the acquire succeeds.
        lock = acquire_write_lock(target, holder="t4", timeout_s=5.0,
                                  stale_after_s=60.0)
    finally:
        Path.unlink = orig_unlink

    assert lock.exists(), "new lock must be held after reclaim"
    assert len(_stale_files(d)) == 1, "reclaimed stale lock mv-asided exactly once"
    release_write_lock(lock)
    assert not lock.exists()


def test_unclearable_stale_lock_times_out_not_spins():
    print("test_unclearable_stale_lock_times_out_not_spins")
    d = Path(tempfile.mkdtemp(prefix="cr_f11_"))
    target = d / "entities.json"
    target.write_text("{}", encoding="utf-8")
    stale_lock = _make_lock(d)
    backdate = time.time() - 120
    os.utime(stale_lock, (backdate, backdate))

    orig = atomic_write._clear_lock_file
    atomic_write._clear_lock_file = lambda p, *a, **k: False  # mount refuses everything
    t0 = time.monotonic()
    try:
        try:
            acquire_write_lock(target, holder="t5", timeout_s=1.0,
                               stale_after_s=60.0)
            raise AssertionError("expected TimeoutError")
        except TimeoutError:
            pass
    finally:
        atomic_write._clear_lock_file = orig
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, f"must time out promptly, took {elapsed:.1f}s"


def test_multi_write_context_unclearable_lock_times_out_not_spins():
    print("test_multi_write_context_unclearable_lock_times_out_not_spins")
    # Second-eyes finding 1: the same F-11 contract must hold for
    # multi_write_context's reclaim branches (corrupt AND dead/stale-holder).
    from atomic_write import AtomicWriteLockError, multi_write_context

    ws = Path(tempfile.mkdtemp(prefix="cr_f11_mwc_"))
    lock_dir = ws / "_hq" / ".system"
    lock_dir.mkdir(parents=True)
    (lock_dir / "atomic.lock").write_text("corrupt-not-json", encoding="utf-8")

    orig = atomic_write._clear_lock_file
    atomic_write._clear_lock_file = lambda p, *a, **k: False
    t0 = time.monotonic()
    try:
        try:
            with multi_write_context(ws, holder="t7", timeout_s=2.0):
                raise AssertionError("must not acquire an unclearable lock")
        except AtomicWriteLockError:
            pass
    finally:
        atomic_write._clear_lock_file = orig
    elapsed = time.monotonic() - t0
    assert elapsed < 15.0, f"must time out promptly, took {elapsed:.1f}s"

    # Dead-pid/stale-holder branch: valid payload, dead pid, same contract.
    (lock_dir / "atomic.lock").write_text(
        '{"pid": 999999999, "holder": "gone", "acquired_at": "2026-01-01T00:00:00"}',
        encoding="utf-8",
    )
    atomic_write._clear_lock_file = lambda p, *a, **k: False
    try:
        try:
            with multi_write_context(ws, holder="t7b", timeout_s=2.0):
                raise AssertionError("must not acquire an unclearable stale lock")
        except AtomicWriteLockError:
            pass
    finally:
        atomic_write._clear_lock_file = orig


def test_sweep_archives_the_backstop_litter():
    print("test_sweep_archives_the_backstop_litter")
    # End-to-end chain with the HYG1 sweep: persistent refusal litters one
    # .stale file; once >1h old, cleanup's sweep archives it out of _hq/data/.
    from cleanup_actions import sweep_stale_locks

    ws = Path(tempfile.mkdtemp(prefix="cr_f11_ws_"))
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    lock = _make_lock(data)
    assert _clear_lock_file(RefusingPath(lock, refusals=999)) is True
    stales = _stale_files(data)
    assert len(stales) == 1
    old = time.time() - 7200
    os.utime(stales[0], (old, old))
    archived = sweep_stale_locks(ws)
    assert len(archived) == 1, archived
    assert _stale_files(data) == [], "sweep must clear the litter from _hq/data/"
    # _archive_move preserves the workspace-relative sub-path for traceability
    assert (ws / "_archive" / "stale-locks" / "_hq" / "data" / stales[0].name).exists()


TESTS = [
    test_transient_refusal_retried_no_litter,
    test_persistent_refusal_mv_aside_deterministic_name,
    test_release_is_idempotent_and_clean_on_normal_mounts,
    test_stale_reclaim_survives_refused_unlink,
    test_unclearable_stale_lock_times_out_not_spins,
    test_multi_write_context_unclearable_lock_times_out_not_spins,
    test_sweep_archives_the_backstop_litter,
]


def main() -> int:
    failures = 0
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  FAIL: {t.__name__}: {e}")
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
