#!/usr/bin/env python3
"""Mode 2 repro runner -- evidence harness for the _pid_alive Windows bug.

NOT a battery suite. Deliberately outside run_all.py discovery (which globs
tests/*.py non-recursively, and these files do not match run_*/test_* anyway).
It spawns detached processes and takes ~65s; it does not belong in the battery.

    python runner.py            # run both experiments, clean up after
    python runner.py --keep     # leave temp workspaces for inspection

EXPERIMENT A -- mechanism: in a detached (no-console) process, what does
    os.kill(self, 0) do, and what does each _pid_alive return?
EXPERIMENT B -- consequence: does a LIVE lock get reclaimed? buggy vs fixed.

THE CONTROL THAT MAKES THIS CONCLUSIVE: stale_after_s=3600 on both sides. The
holder has held the lock for well under a second when the challenger arrives, so
a reclaim CANNOT be Bug #18's time backstop -- it can only be the pid check
reading a live holder as dead. That isolates the variable.

Expected against cr1 @ ae399e8 (the fix):

    buggy  ACQUIRED   0.015   OSError winerror=6 'The handle is invalid'  pid_alive=False
    fixed  TIMEOUT    5.015   OSError winerror=6 'The handle is invalid'  pid_alive=True

Both rows hit the identical os.kill failure -- the mechanism is present either
way. Only the consequence differs, because OpenProcess needs no console.

SYNTHETIC ONLY: every workspace is a fresh tempfile.mkdtemp(). Nothing here ever
points at a real workspace.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
CHILD = str(HERE / "child.py")

# command-room/tests/repro/mode2_pid_alive/runner.py -> command-room/
COMMAND_ROOM = HERE.parents[2]
SCRIPTS = COMMAND_ROOM / "shared" / "scripts"

os.environ["CR_SCRIPTS_DIR"] = str(SCRIPTS)
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

DETACHED_PROCESS = 0x00000008
KEEP = "--keep" in sys.argv

WORKSPACES = []


def _mkws(tag):
    ws = tempfile.mkdtemp(prefix=f"cr_mode2_{tag}_")
    (pathlib.Path(ws) / "_hq" / ".system").mkdir(parents=True, exist_ok=True)
    (pathlib.Path(ws) / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    WORKSPACES.append(ws)
    return ws


def _spawn(role, ws, variant, resfile):
    return subprocess.Popen(
        [sys.executable, CHILD, role, ws, variant, resfile],
        creationflags=DETACHED_PROCESS,
        close_fds=True,
    )


def experiment_a(variant):
    ws = _mkws(f"probe_{variant}")
    res = os.path.join(ws, "probe.json")
    p = _spawn("probe", ws, variant, res)
    p.wait(timeout=60)
    return json.load(open(res)) if os.path.exists(res) else {"error": "no result file"}


def experiment_b(variant):
    ws = _mkws(variant)
    hres = os.path.join(ws, "holder.json")
    cres = os.path.join(ws, "challenger.json")

    hp = _spawn("holder", ws, variant, hres)

    # Only challenge once the holder genuinely owns the lock.
    held = pathlib.Path(ws) / "HELD"
    for _ in range(200):
        if held.exists():
            break
        time.sleep(0.1)
    else:
        hp.kill()
        return {"error": "holder never acquired lock"}

    lock = pathlib.Path(ws) / "_hq" / ".system" / "atomic.lock"
    lock_payload = None
    if lock.exists():
        try:
            lock_payload = json.loads(lock.read_text())
        except Exception as e:
            lock_payload = f"unreadable: {e}"

    cp = _spawn("challenger", ws, variant, cres)
    cp.wait(timeout=120)
    hp.wait(timeout=120)

    out = json.load(open(cres)) if os.path.exists(cres) else {"error": "no result file"}
    out["_holder_pid_in_lock"] = (
        lock_payload.get("pid") if isinstance(lock_payload, dict) else lock_payload
    )
    out["_holder_result"] = (
        json.load(open(hres)) if os.path.exists(hres) else {"error": "no holder result"}
    )
    return out


def main():
    if sys.platform != "win32":
        print("SKIP: this repro characterises Windows-specific os.kill behaviour.")
        return 0

    print("=" * 74)
    print("EXPERIMENT A - mechanism (detached / no console)")
    print("=" * 74)
    a_buggy = experiment_a("buggy")
    a_fixed = experiment_a("fixed")
    print("BUGGY probe:", json.dumps(a_buggy, indent=2))
    print("FIXED probe:", json.dumps(a_fixed, indent=2))

    print()
    print("=" * 74)
    print("EXPERIMENT B - is a LIVE lock reclaimed?  (stale_after_s=3600)")
    print("=" * 74)
    b_buggy = experiment_b("buggy")
    print("BUGGY :", json.dumps(b_buggy, indent=2))
    b_fixed = experiment_b("fixed")
    print("FIXED :", json.dumps(b_fixed, indent=2))

    print()
    print("=" * 74)
    print("RESULTS")
    print("=" * 74)
    print(f"{'variant':8} {'status':10} {'elapsed_s':>10}  os.kill(self,0) outcome")
    print("-" * 74)
    for variant, a, b in (("buggy", a_buggy, b_buggy), ("fixed", a_fixed, b_fixed)):
        print(
            f"{variant:8} {str(b.get('status')):10} {str(b.get('elapsed_s')):>10}  "
            f"{a.get('oskill')}  | pid_alive(self)={a.get('pid_alive_self')}"
        )

    print()
    if KEEP:
        print("Workspaces kept for inspection:")
        for w in WORKSPACES:
            print(" ", w)
    else:
        for w in WORKSPACES:
            shutil.rmtree(w, ignore_errors=True)
        print(f"Cleaned up {len(WORKSPACES)} temp workspaces (--keep to retain).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
