#!/usr/bin/env python3
"""Mode 2 repro child -- runs DETACHED (no console).

Invoked only by runner.py in this directory. See README.md.

argv: role workspace variant resultfile
  role    : probe | holder | challenger
  variant : buggy | fixed

A detached process has no console and therefore no stdout, so everything --
including any traceback -- is reported via the result JSON file. Without that
the runner learns nothing when something goes wrong.
"""
import ctypes
import json
import os
import sys
import time
import traceback

role, ws, variant, resfile = sys.argv[1:5]

# Belt-and-suspenders: DETACHED_PROCESS already leaves us without a console.
try:
    ctypes.windll.kernel32.FreeConsole()
except Exception:
    pass

sys.path.insert(0, os.environ["CR_SCRIPTS_DIR"])
import atomic_write  # noqa: E402


def buggy(pid):
    """The original pre-fix _pid_alive(), verbatim (cr1 @ 74aea8e).

    On Windows os.kill(pid, 0) is NOT a liveness probe: CPython maps signal 0 to
    CTRL_C_EVENT and routes it to GenerateConsoleCtrlEvent. With no console that
    call fails, OSError is raised, and the final `except OSError: return False`
    converts "I could not ask" into "it is dead" -- for every pid.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


if variant == "buggy":
    # Module-global patch: multi_write_context resolves _pid_alive through
    # module globals at call time, so this is genuinely what it will call.
    atomic_write._pid_alive = buggy

res = {"role": role, "variant": variant, "pid": os.getpid()}

try:
    # Prove we really have no console rather than assuming it. 0 == none.
    try:
        res["console_hwnd"] = int(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception as e:
        res["console_hwnd"] = f"probe-failed: {e}"

    if role == "probe":
        # EXPERIMENT A -- mechanism: what does os.kill(self, 0) do with no console?
        try:
            os.kill(os.getpid(), 0)
            res["oskill"] = "returned-normally"
        except OSError as e:
            res["oskill"] = (
                f"OSError winerror={getattr(e, 'winerror', None)} "
                f"errno={e.errno} msg={getattr(e, 'strerror', None)!r}"
            )
        # If a Ctrl+C had actually been delivered we would not reach this line.
        time.sleep(1)
        res["survived_oskill"] = True
        res["pid_alive_self"] = atomic_write._pid_alive(os.getpid())

    elif role == "holder":
        with atomic_write.multi_write_context(
            ws, holder="A", timeout_s=60, stale_after_s=3600
        ):
            with open(os.path.join(ws, "HELD"), "w") as f:
                f.write(str(os.getpid()))
            time.sleep(30)  # hold well past the challenger's 5s timeout
        res["status"] = "released"

    elif role == "challenger":
        t0 = time.monotonic()
        try:
            with atomic_write.multi_write_context(
                ws, holder="B", timeout_s=5, stale_after_s=3600
            ):
                # Reclaiming a lock held by a LIVE holder == mutual exclusion defeated.
                res.update(
                    status="ACQUIRED",
                    elapsed_s=round(time.monotonic() - t0, 3),
                    held_marker_pid=(
                        open(os.path.join(ws, "HELD")).read()
                        if os.path.exists(os.path.join(ws, "HELD"))
                        else None
                    ),
                )
        except Exception as e:
            res.update(
                status="TIMEOUT",
                elapsed_s=round(time.monotonic() - t0, 3),
                err=type(e).__name__,
                err_msg=str(e)[:300],
            )
except Exception:
    res["fatal"] = traceback.format_exc()

with open(resfile, "w") as f:
    json.dump(res, f, indent=2)
