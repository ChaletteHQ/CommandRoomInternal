#!/usr/bin/env python3
"""Regression test for the Windows _pid_alive() Ctrl+C bug (found 2026-07-15).

THE BUG
-------
_pid_alive() in shared/scripts/atomic_write.py probed liveness with the standard
POSIX idiom `os.kill(pid, 0)` on every platform. On Windows, signal 0 is not a
no-op probe: CPython's os.kill special-cases it before TerminateProcess --

    if (signal == CTRL_C_EVENT || signal == CTRL_BREAK_EVENT)
        err = GenerateConsoleCtrlEvent((DWORD)signal, (DWORD)pid);

-- and CTRL_C_EVENT == 0 on Windows. So the "liveness check" broadcast a real
Ctrl+C to the console process group. os.kill returned normally (the caller
happily recorded "alive") and the Ctrl+C landed asynchronously a moment later,
killing everything sharing that console.

Symptom: tests/run_all.py died with KeyboardInterrupt inside WaitForSingleObject
on 4/4 runs, always at run_atomic_write_multi_test.py -- the only suite that
reaches this path -- with nobody at the keyboard. Indistinguishable from a human
pressing Ctrl+C, because it is the identical signal.

Callers are production lock paths, not just tests: atomic_write.multi_write_context
and writer_lock. Clients run Windows.

WHY THE TESTS ARE SHAPED THIS WAY
---------------------------------
The dangerous call is only ever made inside a child process running in its own
process group, so that a regression fails this suite instead of taking down the
whole battery with it. The in-process test traps os.kill before it can fire.

Exit non-zero on failure (house convention).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from atomic_write import _pid_alive  # noqa: E402

CREATE_NEW_PROCESS_GROUP = 0x00000200
CHILD_KEYBOARD_INTERRUPT = 42
CHILD_REPORTED_DEAD = 44


def test_windows_never_routes_through_os_kill() -> None:
    """THE REGRESSION, checked in-process.

    Traps os.kill so a regression is caught *before* it can fire a Ctrl+C. On
    Windows, _pid_alive() must never reach os.kill for any pid.
    """
    if sys.platform != "win32":
        print("SKIP test_windows_never_routes_through_os_kill (not Windows)")
        return

    import os as _os

    calls: list[tuple] = []
    original = _os.kill

    def trap(pid, sig, *a, **kw):
        calls.append((pid, sig))
        raise AssertionError(
            f"REGRESSION: _pid_alive() called os.kill(pid={pid}, sig={sig}) on Windows. "
            "sig 0 == CTRL_C_EVENT -> GenerateConsoleCtrlEvent -> broadcasts Ctrl+C."
        )

    _os.kill = trap
    try:
        _pid_alive(os.getpid())
        _pid_alive(999_999_999)
    finally:
        _os.kill = original

    assert not calls, f"os.kill reached with {calls}"
    print("PASS test_windows_never_routes_through_os_kill")


def test_impossible_pid_reports_dead() -> None:
    """Safe on both old and new code: a bogus pid never resolves to a group."""
    assert _pid_alive(0) is False, "pid 0 must report dead"
    assert _pid_alive(-1) is False, "negative pid must report dead"
    assert _pid_alive(999_999_999) is False, "impossible pid must report dead"
    print("PASS test_impossible_pid_reports_dead")


def test_exited_pid_reports_dead() -> None:
    """A dead writer's pid must read as dead, or stale locks never get reclaimed
    (the Bug #18 case _pid_alive was introduced to solve)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = proc.pid
    proc.wait()
    assert _pid_alive(pid) is False, (
        f"exited pid {pid} must report dead -- a stale lock must be reclaimable"
    )
    print("PASS test_exited_pid_reports_dead")


_CHILD = textwrap.dedent(
    r"""
    import os, sys, time
    sys.path.insert(0, r"{scripts}")

    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP disables Ctrl+C for this group; re-enable it
        # so a regression WOULD actually be delivered here. Without this the
        # test passes vacuously.
        import ctypes
        ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleCtrlHandler(None, False)

    from atomic_write import _pid_alive

    try:
        alive = _pid_alive(os.getpid())
        time.sleep(2)  # a delivered Ctrl+C arrives asynchronously
        print("alive=%s" % alive)
        sys.exit(0 if alive else {dead})
    except KeyboardInterrupt:
        sys.exit({kbd})
    """
)


def test_live_pid_reports_alive_without_sending_ctrl_c() -> None:
    """End-to-end: a live process reports alive AND the caller survives.

    Runs in its own process group so a regression cannot escape and kill this
    runner -- it just fails the assertion below.
    """
    code = _CHILD.format(
        scripts=str(SCRIPTS), kbd=CHILD_KEYBOARD_INTERRUPT, dead=CHILD_REPORTED_DEAD
    )
    kwargs: dict = dict(capture_output=True, text=True, timeout=60)
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.run([sys.executable, "-c", code], **kwargs)

    assert proc.returncode != CHILD_KEYBOARD_INTERRUPT, (
        "REGRESSION: _pid_alive() delivered a Ctrl+C to its own process group and "
        "raised KeyboardInterrupt. os.kill(pid, 0) on Windows == "
        "GenerateConsoleCtrlEvent(CTRL_C_EVENT). Use OpenProcess/WaitForSingleObject."
    )
    assert proc.returncode != CHILD_REPORTED_DEAD, (
        "_pid_alive() reported a live process as dead -- a live writer's lock "
        "would be reclaimed out from under it."
    )
    assert proc.returncode == 0, (
        f"child exited {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "alive=True" in proc.stdout, f"unexpected child output: {proc.stdout!r}"
    print("PASS test_live_pid_reports_alive_without_sending_ctrl_c")


def main() -> int:
    test_windows_never_routes_through_os_kill()
    test_impossible_pid_reports_dead()
    test_exited_pid_reports_dead()
    test_live_pid_reports_alive_without_sending_ctrl_c()
    print("\nALL _pid_alive windows-safety tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
