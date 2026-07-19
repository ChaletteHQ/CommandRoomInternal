# repro/mode2_pid_alive

Evidence harness for the `_pid_alive()` Windows bug fixed in **PR #30** (`ae399e8`).

This is **not** a battery suite. It is the investigation harness that proved the
bug's second failure mode, kept because the reasoning is worth more than the
conclusion — if this class of defect recurs, the method is here.

## Why it is not in the battery

- `run_all.py` discovers via `TESTS_DIR.glob("*.py")` — **non-recursive**, so a
  subdirectory is structurally invisible to it.
- These files are `child.py` / `runner.py`, which do not match the `run_*` /
  `test_*` naming the runner requires. Safe two ways over.
- It spawns **detached** processes and takes ~65s. Neither belongs in the
  battery.

Permanent regression coverage for this bug lives in the battery proper, at
`tests/run_pid_alive_windows_test.py`. That is what guards the fix. This
directory documents *how it was proven*.

## The bug, in one paragraph

`_pid_alive()` asked "is this process alive?" with `os.kill(pid, 0)` — the
correct POSIX idiom, where signal 0 is a no-op existence probe. On Windows,
CPython maps signal 0 to `CTRL_C_EVENT` (which **is** 0) and routes it to
`GenerateConsoleCtrlEvent`. So on Windows the question was never asked. Two
mutually exclusive failure modes, no correct case:

| Context | What happens | Returns | Effect |
|---|---|---|---|
| **Console attached** (terminal, CI) | Ctrl+C broadcast to the console group | `True` | Kills the test runner / terminal. `run_all.py` could not complete on Windows. |
| **No console** (Cowork) | Call fails, `WinError 6` "The handle is invalid" → `OSError` → `except OSError: return False` | `False` | **Every** pid reads as dead. A live writer's lock is reclaimed. |

Mode 1 was proven first. This harness proves **Mode 2**.

## What it does

Two detached children against a throwaway workspace:

- **holder (A)** takes `multi_write_context` and sleeps 30s *inside* the
  critical section — unambiguously alive and holding.
- **challenger (B)** then attempts the same lock with `timeout_s=5`.

Run twice: once with `_pid_alive` monkeypatched back to the original
implementation (`buggy`), once against the shipped fix (`fixed`).

- **B ACQUIRES** → a live holder's lock was reclaimed → mutual exclusion defeated
- **B TIMES OUT** → the lock was respected → correct

## The control that makes it conclusive

**`stale_after_s=3600` on both sides.** The holder has held the lock for well
under a second when the challenger arrives. If B reclaims, it **cannot** be
Bug #18's time backstop — the lock is one second old against an hour-long
threshold. That leaves exactly one suspect: the pid check reading a live holder
as dead.

This control also settles a related question. The reclaim condition is:

```python
if not alive or stale_by_time:      # atomic_write.py -- OR, not AND
```

In Mode 2 `_pid_alive` returns `False` for every pid, so `not alive` is always
true and Python's `or` short-circuits — `stale_by_time` is **never evaluated**.
The time backstop can only ever *add* reclaim cases; it can never *prevent* one.
It is not a floor under the pid check. An hour-long timer changes nothing.

`writer_lock`'s 30s floor, by contrast, is a real floor — precisely because it
is not sitting behind an `or` with a pid check. `acquire_write_lock` gates purely
on mtime and never calls `_pid_alive`. Same concept, opposite outcome; the
difference is structural.

## Running it

```bash
# From a checkout containing the fix (ae399e8 or later). The buggy path is
# exercised by monkeypatching the module global, not by checking out old code.
python command-room/tests/repro/mode2_pid_alive/runner.py
python command-room/tests/repro/mode2_pid_alive/runner.py --keep   # retain temp dirs
```

Windows only; it exits with a SKIP notice elsewhere. Takes ~65s (two 30s holds).

## Expected output

```
buggy    ACQUIRED        0.015  OSError winerror=6 ... 'The handle is invalid'  | pid_alive(self)=False
fixed    TIMEOUT         5.015  OSError winerror=6 ... 'The handle is invalid'  | pid_alive(self)=True
```

Note both rows hit the **identical** `os.kill` failure — the mechanism is present
either way. Only the consequence differs, because `OpenProcess` needs no console.
That is the control for the fix.

In the recorded buggy run, the lock file named pid `22744`; that process was
alive, mid-sleep, inside its critical section. The challenger took the lock in
**15 milliseconds**, both held it for ~30s, and the holder then "released" a lock
it no longer owned.

## Safety

- **Synthetic only.** Every workspace is a fresh `tempfile.mkdtemp()`, removed on
  exit unless `--keep`. Nothing points at a real workspace.
- **No Ctrl+C is delivered.** With no console, `GenerateConsoleCtrlEvent` fails
  rather than firing. Mode 1 is already proven; this harness does not re-fire it.
  Do **not** run the buggy path in a console-attached child without
  `CREATE_NEW_PROCESS_GROUP` isolation.
