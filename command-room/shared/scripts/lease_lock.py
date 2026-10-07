"""lease_lock — a directory lease for the events ledger (findings LCK-01, LCK-02).

WHY THIS EXISTS. `writer_lock.events_writer_lock` takes an OS byte-range lock on
`_hq/data/.writer.lock`. On the bridged folder of a merged seat that lock does
NOT exclude across sessions (LCK-01): two sessions racing appends produced
duplicate seqs and corrupt lines. A folder lease taken by `os.mkdir` DOES
exclude there (LCK-02): mkdir is atomic on every filesystem the plugin runs on.

THE SHAPE. `events_lease(events_path, holder)` is a context manager with the
same call shape as `writer_lock.events_writer_lock(events_path, holder=...)`,
and `atomic_write.atomic_append_jsonl` takes its ONE events lock through
`writer_lock_backend()` (LEASE2 MUST 3):

    with writer_lock_backend()(path, holder=holder):

The backend is chosen by ONE flag, `shared/config/writer_lock.json`
`{"backend": "flock" | "lease"}`. Default `flock`: `writer_lock_backend()`
hands back `events_writer_lock` itself, so every seat behaves as before until
the flag flips. Under `lease` the append checks its hold right before the
write (`check_held`); a hold that is no longer its own writes nothing and
raises `LeaseLost` (door reason `lease_lost`).

HOW THE LEASE WORKS.
  take     `os.mkdir(<events>.lease/)` — succeeds for exactly one taker; the
           taker writes `holder.json` (holder, pid, taken_at, ttl_s, expires_at)
           inside it and re-reads it to confirm the record is its own.
  contend  a taker that gets FileExistsError waits and retries within its
           budget (`timeout_s` wall-clock, or `retries` rounds), re-reading
           the holder file each round.
  takeover a lease whose `expires_at` has passed is stale (its holder crashed
           or stalled). The taker renames the stale dir aside to
           `<events>.lease.released.<stamp>`, CONFIRMS the moved dir holds the
           record it judged stale (else it moved a live lease: put it back,
           claim nothing), and takes a fresh lease; the new holder file
           records `took_over_from`.
  release  refuses when the holder file is not provably ours (another
           holder's record, or none readable); otherwise
           `os.rename(<events>.lease/, <events>.lease.released.<stamp>)` —
           the rename is the atomic release — then the released dir is
           removed. Removal may fail on a mount that holds the directory open
           (PermissionError): bounded retries, then the `.released.<stamp>`
           sidecar is LEFT for `reap_released(events_path)` — never a loop,
           never a raise from the release path. The answer says why
           (`reason`).
  TTL      20 s by default, shorter than a contender's 30 s default wait, so
           a writer waiting behind a refused release takes over in time.

Never a live connector, never the workspace: the module only ever touches the
lease directory beside the events file it is given.

3.10-safe (the sandbox VM).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

LEASE_SUFFIX = ".lease"
RELEASED_INFIX = ".lease.released."
HOLDER_FILE = "holder.json"
CONFIG_REL = Path("shared") / "config" / "writer_lock.json"
BACKENDS = ("flock", "lease")
DEFAULT_BACKEND = "flock"
#: LEASE2 MUST 4 (review PARALLEL-A D4 / N-6): the lease's TTL is SHORTER than
#: a contender's wait. A release refused on the mount (an open handle on
#: Windows) leaves the lease held until its TTL runs out; a writer waiting
#: with the default timeout must still be waiting when that happens, so it
#: takes the lease over instead of timing out a beat before it could.
#: timeout = TTL + TAKEOVER_MARGIN_S.
DEFAULT_TTL_S = 20.0
TAKEOVER_MARGIN_S = 10.0
DEFAULT_TIMEOUT_S = DEFAULT_TTL_S + TAKEOVER_MARGIN_S
DEFAULT_RETRIES = 200
DEFAULT_BACKOFF_S = 0.02
RELEASE_REMOVE_RETRIES = 5
HOLDER_REREADS = 3

#: The one sentence a write refused for a lost lease carries to the door
#: (`ok:false`, reason `lease_lost`). Nothing was appended when it is said.
LEASE_LOST_LINE = ("Nothing was saved, because another session was writing to the "
                   "workspace at the same moment - say it again and it will be saved.")
#: Before the append, the hold must still have this much of its TTL left
#: (a quarter of it, at most this many seconds): a lease that could expire
#: mid-write is treated as lost, never written under.
WRITE_MARGIN_MAX_S = 5.0
WRITE_MARGIN_FRACTION = 0.25
#: LEASE3 MUST 3: the marker a batch saved under a hold that was lost AFTER
#: the pre-write check leaves (workspace-relative); `substrate_health` reads it.
HOLD_LOST_RELPATH = ("_hq", ".system", "lease_hold_lost.json")
#: LEASE3 fix N-2: the marker keeps the timestamps of its most recent
#: incidents (newest last), capped, so the health line can count a window.
HOLD_LOST_RECENT_CAP = 20

__all__ = [
    "LeaseError", "LeaseTimeout", "LeaseLost", "acquire", "release", "events_lease",
    "lease_dir", "read_holder", "reap_released", "writer_lock_backend", "events_write_section",
    "configured_backend", "check_held", "last_release", "last_acquire_stats",
    "record_hold_lost", "hold_lost_marker_path", "note_appended", "HOLD_LOST_RELPATH", "DEFAULT_BACKEND", "BACKENDS",
    "LEASE_LOST_LINE", "DEFAULT_TTL_S", "DEFAULT_TIMEOUT_S",
]


class LeaseError(RuntimeError):
    """A lease operation could not complete (bad arguments, unreadable dir)."""


class LeaseTimeout(TimeoutError):
    """The lease stayed held past the retry budget. Same class the flock
    backend raises (TimeoutError), so callers catch both the same way."""


class LeaseLost(LeaseError):
    """The hold is no longer this writer's (taken over, unreadable, or about
    to expire) at the moment it was about to write: NOTHING was written.

    Carries `cr_refusal_reason = "lease_lost"`, so a door running the writer
    answers `ok:false, reason: lease_lost` with `LEASE_LOST_LINE`, never a
    traceback. `cause` says which leg refused."""

    cr_refusal_reason = "lease_lost"

    def __init__(self, cause: str, detail: str = ""):
        super().__init__(f"events lease lost before the write ({cause}){': ' + detail if detail else ''}")
        self.cause = cause
        self.line = LEASE_LOST_LINE


# ---------------------------------------------------------------------------
# paths + holder file
# ---------------------------------------------------------------------------

def lease_dir(events_path: str | Path) -> Path:
    """`<events>.lease` beside the ledger — `events.jsonl.lease/`."""
    p = Path(events_path)
    return p.with_name(p.name + LEASE_SUFFIX)


def _released_name(lease: Path) -> Path:
    stamp = f"{time.time():.6f}".replace(".", "") + f"-{os.getpid()}-{random.randrange(1 << 20):05x}"
    return lease.with_name(lease.name.replace(LEASE_SUFFIX, RELEASED_INFIX + stamp, 1))


def _now() -> float:
    return time.time()


def read_holder(lease: Path) -> dict[str, Any] | None:
    """The holder file of a lease dir, or None when absent/unreadable (a
    lease dir with no readable holder file is treated as taken-but-unknown
    until its mtime-based age passes the TTL — see `_is_stale`)."""
    try:
        raw = (lease / HOLDER_FILE).read_text(encoding="utf-8")
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write_holder(lease: Path, holder: str, ttl_s: float, took_over_from: dict | None) -> dict[str, Any]:
    now = _now()
    info: dict[str, Any] = {
        "holder": str(holder), "pid": os.getpid(), "taken_at": now,
        "ttl_s": float(ttl_s), "expires_at": now + float(ttl_s),
    }
    if took_over_from is not None:
        info["took_over_from"] = {k: took_over_from.get(k) for k in ("holder", "pid", "taken_at", "expires_at")}
    # A tmp name per writer: two takers can never publish each other's record.
    tmp = lease / f"{HOLDER_FILE}.{os.getpid()}.{random.randrange(1 << 30):08x}.tmp"
    try:
        tmp.write_text(json.dumps(info, sort_keys=True), encoding="utf-8")
        os.replace(tmp, lease / HOLDER_FILE)
    except OSError as exc:
        # LEASE2 MUST 2 (A N-3): a failed holder write is a LeaseError, never
        # a bare FileNotFoundError out of acquire (the dir was moved from
        # under the taker, or the mount refused the write).
        raise LeaseError(f"cannot write the lease holder file in {lease}: {exc}") from exc
    return info


def _is_stale(lease: Path, ttl_s: float) -> tuple[bool, dict[str, Any] | None]:
    """A lease is stale when its holder file says `expires_at` has passed;
    a lease with NO readable holder file yet is stale only when the directory
    itself is older than the TTL (the taker may be between mkdir and the
    holder write)."""
    info = read_holder(lease)
    now = _now()
    if info is not None:
        try:
            return (now > float(info.get("expires_at", 0.0))), info
        except (TypeError, ValueError):
            return True, info
    try:
        age = now - lease.stat().st_mtime
    except OSError:
        return False, None
    return (age > float(ttl_s)), None


# ---------------------------------------------------------------------------
# acquire / release
# ---------------------------------------------------------------------------

def acquire(lock_dir: str | Path, *, holder: str, ttl_s: float = DEFAULT_TTL_S,
            retries: int = DEFAULT_RETRIES, backoff: float = DEFAULT_BACKOFF_S,
            timeout_s: float | None = None) -> dict[str, Any]:
    """Take the lease at `lock_dir` (the `.lease` directory itself).

    Returns the holder info written. Raises LeaseTimeout after `retries`
    contended rounds, or — when `timeout_s` is given — once that many
    wall-clock seconds have passed (a real budget, so it can be compared with
    the TTL: see DEFAULT_TIMEOUT_S). The ONLY primitive that decides ownership is
    `os.mkdir` — never `exists()` followed by `mkdir` (two takers can both
    see "absent"; only one mkdir succeeds).
    """
    lease = Path(lock_dir)
    if not holder or not str(holder).strip():
        raise LeaseError("holder must be a non-empty string")
    if float(ttl_s) <= 0:
        raise LeaseError("ttl_s must be positive")
    lease.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = None if timeout_s is None else started + max(0.0, float(timeout_s))
    took_over_from: dict[str, Any] | None = None
    rounds = 0
    takeovers = 0

    def spent() -> bool:
        if deadline is not None:
            return time.monotonic() >= deadline
        return rounds >= int(retries)

    while True:
        try:
            os.mkdir(lease)
        except FileExistsError:
            took_over_from = None
            stale, info = _is_stale(lease, ttl_s)
            if stale:
                # Takeover: move the dead lease aside (the rename is atomic —
                # exactly one contender wins it), confirm what moved is the
                # lease judged stale (LEASE2 MUST 1), then loop to mkdir fresh.
                took = _take_over(lease, info)
                if took is not None:
                    took_over_from = took
                    takeovers += 1
                    continue
                # Not taken over (another contender won the rename, the rename
                # was refused, or a live lease was put back): wait a round.
            if spent():
                break
            rounds += 1
            time.sleep(float(backoff) * (1.0 + random.random()))
            continue
        except OSError as exc:  # a parent that vanished, a read-only mount
            raise LeaseError(f"cannot take lease at {lease}: {exc}") from exc
        try:
            mine = _write_holder(lease, holder, ttl_s, took_over_from)
        except LeaseError:
            # The dir we made is no longer ours to write into: gone (moved
            # aside by a takeover that judged it) or now holding someone
            # else's record. That is a lost race, not a failure: retry the
            # mkdir loop within the budget. A dir still there with NO holder
            # is our own write failing on the mount: say so.
            if lease.is_dir() and read_holder(lease) is None:
                raise
            took_over_from = None
            if spent():
                break
            rounds += 1
            continue
        if not _same_holder(read_holder(lease), mine):
            took_over_from = None  # published over by another taker: not ours
            if spent():
                break
            rounds += 1
            continue
        _note_acquire(lease, rounds > 0, started, takeovers)
        return mine
    _note_acquire(lease, rounds > 0, started, takeovers, timed_out=True)
    info = read_holder(lease) or {}
    budget = f"{float(timeout_s):g}s" if timeout_s is not None else f"{retries} rounds"
    raise LeaseTimeout(
        f"events lease held by {info.get('holder', 'unknown')!r} (pid {info.get('pid', '?')}) "
        f"past {budget}")


def _record_lease_stats(lease: Path, **deltas: Any) -> None:
    """LEASE3 MUST 4 — the lease's counters in `_hq/.system/lock_stats.json`,
    through the ONE stats writer (`writer_lock._record_lock_stats`, lazy
    import). Never raises: a stats failure never touches the lease."""
    try:
        wl = _writer_lock_module()
        wl._record_lock_stats(wl._root_from_lock_dir(Path(lease).parent), **deltas)
    except Exception:
        pass


def _note_acquire(lease: Path, waited: bool, started: float, takeovers: int,
                  timed_out: bool = False) -> None:
    """Keep this thread's last acquire (`last_acquire_stats`) and count it:
    `lease_waits` + `lease_total_wait_ms` when it waited at least one round,
    `lease_takeovers` per stale lease it took over, `lease_timeouts` when it
    gave up. Nothing is written on the uncontended fast path. On success the
    counters are written while the lease is held, so two writers never lose
    each other's increment."""
    wait_ms = int((time.monotonic() - started) * 1000.0)
    _local.last_acquire = {"waited": bool(waited), "wait_ms": wait_ms if waited else 0,
                           "takeovers": int(takeovers), "timed_out": bool(timed_out)}
    deltas: dict[str, Any] = {}
    if timed_out:
        deltas["lease_timeouts"] = 1
    elif waited:
        deltas["lease_waits"] = 1
        deltas["lease_total_wait_ms"] = wait_ms
    if takeovers:
        deltas["lease_takeovers"] = int(takeovers)
    if deltas:
        _record_lease_stats(lease, **deltas)


def last_acquire_stats() -> dict[str, Any]:
    """This thread's most recent `acquire`: `{waited, wait_ms, takeovers,
    timed_out}` (empty before the first)."""
    return dict(getattr(_local, "last_acquire", None) or {})


def _same_holder(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """Two holder records name the same lease: same holder, pid and taken_at.
    Two unreadable holders (both None) are the same too — a holder-less dir
    judged stale by its age and a holder-less dir moved aside."""
    if a is None or b is None:
        return a is None and b is None
    return all(a.get(k) == b.get(k) for k in ("holder", "pid", "taken_at"))


def _put_back(lease: Path, aside: Path) -> bool:
    """Return a lease that was moved aside by mistake to its name.

    "Rename back if the name is free" (LEASE2 MUST 1), spelled so it can never
    REPLACE anything: `os.rename` onto an existing EMPTY directory succeeds on
    POSIX, which would swallow a fresh taker's dir between its mkdir and its
    holder write. So the name is claimed with `os.mkdir` (the one primitive
    that decides ownership) and the displaced holder file is moved into it.
    When the name is already taken, the displaced holder has lost its lease;
    it learns so at its pre-write check and at its release, and the aside is
    removed. Returns True when the lease is back under its name."""
    try:
        os.mkdir(lease)
    except OSError:
        _remove_released(aside)
        return False
    try:
        os.replace(aside / HOLDER_FILE, lease / HOLDER_FILE)
    except OSError:
        # The holder file could not follow: free the name again rather than
        # leave a holder-less dir that blocks every writer for a TTL.
        empty = _released_name(lease)
        try:
            os.rename(lease, empty)
        except OSError:
            pass
        else:
            _remove_released(empty)
        _remove_released(aside)
        return False
    _remove_released(aside)
    return True


def _take_over(lease: Path, judged: dict[str, Any] | None) -> dict[str, Any] | None:
    """Move the lease judged stale aside and confirm it was THAT lease.

    Between the stale read and the rename, the expired holder may release and
    a fresh holder may take the name (review PARALLEL-A D2 / N-2): the rename
    would then move a LIVE lease aside and two holders would coexist. So the
    moved dir's holder file is re-read and compared with the one judged
    stale (holder, pid, taken_at). The same -> the takeover stands and the
    caller may claim `took_over_from`. Not the same -> the live lease is put
    back (`_put_back`) or, when the moved dir has no holder file yet (a fresh
    taker between its mkdir and its holder write, whose write now fails and
    retries), removed; either way no `took_over_from` is claimed and the
    caller retries the mkdir loop. Returns the record taken over from, or
    None when nothing was taken over."""
    aside = _released_name(lease)
    try:
        os.rename(lease, aside)
    except OSError:
        return None  # another contender moved it; loop and race the mkdir
    moved = read_holder(aside)
    if _same_holder(moved, judged):
        _remove_released(aside)
        return judged or {"holder": "unknown"}
    if moved is not None:
        _put_back(lease, aside)
    else:
        _remove_released(aside)
    return None


def _remove_released(aside: Path, remove_retries: int = RELEASE_REMOVE_RETRIES) -> bool:
    """Remove a released lease dir. Bounded retries on PermissionError (a
    mount that still holds the directory open); on exhaustion the sidecar is
    LEFT for `reap_released` and False is returned. Never raises."""
    for i in range(int(remove_retries) + 1):
        try:
            shutil.rmtree(aside)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if i >= int(remove_retries):
                return False
            time.sleep(0.01 * (i + 1))
        except OSError:
            return False
    return False


def release(lock_dir: str | Path, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Release the lease: rename the dir aside (atomic), then remove it.

    Returns `{"released": bool, "removed": bool, "sidecar": str|None,
    "reason": str|None}`. A lease that is no longer ours (taken over after
    our TTL passed) is NOT renamed — the current holder keeps it; `released`
    is False with reason `not_ours`, and the caller learns from the return
    value that its hold was lost. Never raises from the release path.

    LEASE2 MUST 2 (review PARALLEL-A D3 / N-3): with `info` given, a lease
    dir with NO readable holder file is not provably ours — it may be a fresh
    taker's dir between its mkdir and its holder write — so the release
    REFUSES (`released: False`, reason `holder_unreadable`) and leaves it.
    The holder file is re-read a few times first (a put-back moves it in a
    beat after the mkdir). And what the rename moved is re-read too: when it
    is not our record, a taker replaced our expired lease between our read
    and our rename, and the lease is put back (the same N-2 rule as acquire).
    """
    lease = Path(lock_dir)
    out: dict[str, Any] = {"released": False, "removed": False, "sidecar": None, "reason": None}
    if info is not None:
        current = read_holder(lease)
        for i in range(HOLDER_REREADS):
            if current is not None:
                break
            time.sleep(0.01 * (i + 1))
            current = read_holder(lease)
        if current is None:
            out["reason"] = "holder_unreadable" if lease.is_dir() else "absent"
            return out
        if not _same_holder(current, info):
            out["reason"] = "not_ours"
            return out  # taken over; not ours to release
    aside = _released_name(lease)
    try:
        os.rename(lease, aside)
    except FileNotFoundError:
        out["reason"] = "absent"
        return out
    except OSError:
        # The rename itself refused (rare: an open handle inside on Windows).
        # Bounded retry, then give up without looping — the lease stays and
        # the TTL takeover reaps it.
        for i in range(RELEASE_REMOVE_RETRIES):
            time.sleep(0.01 * (i + 1))
            try:
                os.rename(lease, aside)
                break
            except OSError:
                continue
        else:
            out["reason"] = "rename_refused"
            return out
    if info is not None:
        moved = read_holder(aside)
        if not _same_holder(moved, info):
            if moved is not None:
                _put_back(lease, aside)
            else:
                _remove_released(aside)
            out["reason"] = "not_ours"
            return out
    out["released"] = True
    if _remove_released(aside):
        out["removed"] = True
    else:
        out["sidecar"] = str(aside)
    return out


def reap_released(events_path: str | Path) -> list[str]:
    """The maintenance sweep: remove every `<events>.lease.released.*`
    sidecar beside the ledger. Returns the names removed. A sidecar still
    refusing removal is left for the next sweep (never a loop)."""
    p = Path(events_path)
    prefix = p.name + RELEASED_INFIX
    removed: list[str] = []
    try:
        entries = list(p.parent.iterdir())
    except OSError:
        return removed
    for entry in entries:
        if entry.name.startswith(prefix) and entry.is_dir():
            if _remove_released(entry, remove_retries=1):
                removed.append(entry.name)
    return removed


@contextmanager
def events_lease(events_path: str | Path, holder: str = "unknown", timeout_s: float = DEFAULT_TIMEOUT_S,
                 *, ttl_s: float = DEFAULT_TTL_S, backoff: float = DEFAULT_BACKOFF_S) -> Iterator[Path]:
    """Hold the events lease for the duration of the `with` block.

    Same call shape as `writer_lock.events_writer_lock(events_path, holder=,
    timeout_s=)`: accepts the events.jsonl path (the lease lands beside it) or a
    workspace root (resolves to `<root>/_hq/data/events.jsonl`). Yields the
    lease directory path. Reentrant per process+thread like the flock backend:
    a nested call while held increments a depth counter.

    `timeout_s` is a wall-clock budget (default `DEFAULT_TIMEOUT_S`, longer
    than the default TTL — LEASE2 MUST 4), polled every `backoff` or so.
    """
    lease = lease_dir(_events_file(events_path))
    key = str(lease)
    state = _state()
    entry = state.get(key)
    if entry is not None:
        entry["depth"] += 1
        try:
            yield lease
        finally:
            entry["depth"] -= 1
        return
    retries = max(1, int(float(timeout_s) / max(float(backoff), 1e-4)))
    info = acquire(lease, holder=holder, ttl_s=ttl_s, retries=retries, backoff=backoff,
                   timeout_s=timeout_s)
    state[key] = {"depth": 1, "info": info}
    _last().pop(key, None)
    try:
        yield lease
    finally:
        state.pop(key, None)
        # The release's answer is KEPT, not discarded: a caller that wrote
        # under the lease reads it (`last_release`) to learn whether its hold
        # was still its own when it let go (LEASE2 MUST 3).
        _last()[key] = release(lease, info)


def _writer_lock_module():
    try:
        import writer_lock as _wl
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import writer_lock as _wl
    return _wl


@contextmanager
def events_write_section(events_path: str | Path, holder: str = "unknown", **flock_kw) -> Iterator[Any]:
    """LEASE3 MUST 2 — a read-check-then-append SECTION on the events ledger.

    The direct sections (`commitment_state`, `mute_ledger`, `plate_view`) read
    the ledger, decide, then append; their exclusion must cover the whole
    span, not only the append. Under the flag `lease` this takes the flock
    THEN the lease, the append's own order (`atomic_write.atomic_append_jsonl`),
    so the inner append re-enters both (reentrant per thread) and its
    `check_held` judges the section's own hold: a section that outlives the
    write margin refuses its append as `lease_lost`, nothing appended. Under
    `flock` it is exactly `writer_lock.events_writer_lock(events_path,
    holder=..., **flock_kw)`, the call the sections made before.

    `writer_lock.events_writer_lock` is looked up at call time, so a caller
    (or a test) that replaced it is honoured. `flock_kw` (`timeout_s`) goes to
    the flock as given; on `lease` a `timeout_s` is the lease's wait too, and
    `ttl_s` (lease only, default `DEFAULT_TTL_S`) is the section's hold."""
    ttl_s = float(flock_kw.pop("ttl_s", DEFAULT_TTL_S))
    flock = _writer_lock_module().events_writer_lock
    if configured_backend() != "lease":
        with flock(events_path, holder=holder, **flock_kw) as held:
            yield held
        return
    lease_kw: dict[str, Any] = {"ttl_s": ttl_s}
    if "timeout_s" in flock_kw:
        lease_kw["timeout_s"] = flock_kw["timeout_s"]
    key = str(lease_dir(_events_file(events_path)))
    with flock(events_path, holder=holder, **flock_kw) as held:
        outermost = key not in _state()
        entry: dict[str, Any] = {}
        try:
            with events_lease(events_path, holder=holder, **lease_kw):
                entry = _state().get(key) or {}
                yield held
        finally:
            # LEASE3 fix N-1 (REVIEW_T3_LEASE3): the appends inside the
            # section re-entered the lease, so none of them saw the release.
            # The section that took the lease reads it: a hold taken over
            # after rows landed is said on stderr and leaves the marker,
            # exactly as a lone append does. Nothing appended, nothing said.
            if outermost:
                _say_hold_lost_after_section(events_path, holder, entry)


_local = threading.local()


def _state() -> dict[str, dict[str, Any]]:
    # Per process+thread, mirroring writer_lock: a second thread of the same
    # process is a second writer and must take the lease, never ride along.
    d = getattr(_local, "leases", None)
    if d is None:
        d = {}
        _local.leases = d
    return d


def _last() -> dict[str, dict[str, Any]]:
    d = getattr(_local, "released", None)
    if d is None:
        d = {}
        _local.released = d
    return d


def _events_file(events_path: str | Path) -> Path:
    p = Path(events_path)
    return p / "_hq" / "data" / "events.jsonl" if p.is_dir() else p


def check_held(events_path: str | Path) -> None:
    """Raise `LeaseLost` unless this thread still holds the events lease with
    enough of its TTL left to finish a write. Called by `atomic_append_jsonl`
    immediately before it writes under the lease backend (LEASE2 MUST 3), so a
    hold that was taken over, whose holder file no longer reads, or that is
    about to expire writes NOTHING and the caller hears `lease_lost`. Every
    refusal is counted as `lease_lost` (LEASE3 MUST 4)."""
    try:
        _check_held(events_path)
    except LeaseLost:
        _record_lease_stats(lease_dir(_events_file(events_path)), lease_lost=1)
        raise


def _check_held(events_path: str | Path) -> None:
    lease = lease_dir(_events_file(events_path))
    entry = _state().get(str(lease))
    if entry is None:
        raise LeaseLost("not_held", str(lease))
    info = entry["info"]
    current = read_holder(lease)
    for i in range(HOLDER_REREADS):
        if current is not None:
            break
        time.sleep(0.01 * (i + 1))
        current = read_holder(lease)
    if current is None:
        raise LeaseLost("holder_unreadable", str(lease))
    if not _same_holder(current, info):
        raise LeaseLost("not_ours", f"now held by {current.get('holder', 'unknown')!r}")
    try:
        ttl = float(info.get("ttl_s", DEFAULT_TTL_S))
        left = float(info.get("expires_at", 0.0)) - _now()
    except (TypeError, ValueError):
        raise LeaseLost("expiring", "unreadable expiry") from None
    if left < min(WRITE_MARGIN_MAX_S, ttl * WRITE_MARGIN_FRACTION):
        raise LeaseLost("expiring", f"{left:.2f}s of the lease left")


def hold_lost_marker_path(workspace_root: str | Path) -> Path:
    """`<root>/_hq/.system/lease_hold_lost.json`."""
    return Path(workspace_root).joinpath(*HOLD_LOST_RELPATH)


def record_hold_lost(events_path: str | Path, holder: str, n_events: int) -> None:
    """LEASE3 MUST 3 — a batch landed under a lease that was taken over
    between the pre-write check and the release (`last_release` said
    `not_ours`). The batch is on disk and a retry would duplicate it, so the
    append says so on stderr AND leaves ONE marker,
    `_hq/.system/lease_hold_lost.json` `{count, last_ts, recent, last_holder,
    last_n_events}` (`recent`: the last `HOLD_LOST_RECENT_CAP` incident
    timestamps, so the health line can count a window), load-merge-atomic like `writer_lock._record_lock_stats`.
    Never raises: the marker is evidence, never a reason to fail a write that
    already landed."""
    try:
        import datetime as _dt
        wl = _writer_lock_module()
        root = wl._root_from_lock_dir(_events_file(events_path).parent)
        from atomic_write import atomic_write_json
        p = hold_lost_marker_path(root)
        cur: dict[str, Any] = {}
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cur = loaded
        except (OSError, ValueError):
            cur = {}
        try:
            count = int(cur.get("count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
        recent = cur.get("recent")
        recent = [str(x) for x in recent if isinstance(x, str)] if isinstance(recent, list) else []
        recent = (recent + [now_iso])[-HOLD_LOST_RECENT_CAP:]
        cur.update({"count": count + 1, "last_ts": now_iso, "recent": recent,
                    "last_holder": str(holder), "last_n_events": int(n_events)})
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(p, cur)
    except Exception:
        pass


def note_appended(events_path: str | Path, n: int) -> None:
    """LEASE3 fix N-1: add `n` rows to this thread's held-state entry for the
    ledger (`appended`), when the lease is held. Called by
    `atomic_append_jsonl` after a write under the lease. Never raises."""
    try:
        entry = _state().get(str(lease_dir(_events_file(events_path))))
        if entry is not None:
            entry["appended"] = int(entry.get("appended", 0) or 0) + int(n)
    except Exception:
        pass


def _say_hold_lost_after_section(events_path: str | Path, holder: str, entry: dict[str, Any]) -> None:
    try:
        appended = int(entry.get("appended", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        appended = 0
    if appended <= 0:
        return
    rel = last_release(events_path) or {}
    if rel.get("reason") != "not_ours":
        return
    import sys as _sys
    try:
        _sys.stderr.write(
            f"[lease] {appended} event(s) appended to {_events_file(events_path).name} by "
            f"{holder} under a lease that was taken over before release\n")
    except Exception:
        pass
    record_hold_lost(events_path, holder, appended)


def last_release(events_path: str | Path) -> dict[str, Any] | None:
    """The `release()` answer of this thread's most recent `events_lease` on
    this ledger, or None."""
    return _last().get(str(lease_dir(_events_file(events_path))))


# ---------------------------------------------------------------------------
# the backend flag
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    # Spelled the way the runtime manifest's asset scan reads it
    # (`workspace_access.referenced_shared_assets`), so the flag file ships in
    # every installed runtime on this module's own account (LEASE2 MUST 3).
    return Path(__file__).resolve().parent.parent / "config" / "writer_lock.json"


def configured_backend(config_path: str | Path | None = None, env: dict | None = None) -> str:
    """`flock` (default) or `lease`, from `shared/config/writer_lock.json`.
    `CR_WRITER_LOCK_BACKEND` in `env` (default `os.environ`) overrides the
    file for a single process — the suites use it to drive both backends
    against one tree. Any unknown or unreadable value is the default: the
    flag can only ever widen to the lease on purpose."""
    e = os.environ if env is None else env
    override = str(e.get("CR_WRITER_LOCK_BACKEND", "") or "").strip().lower()
    if override in BACKENDS:
        return override
    cfg = Path(config_path) if config_path else _config_path()
    try:
        d = json.loads(cfg.read_text(encoding="utf-8"))
        b = str(d.get("backend", "")).strip().lower()
    except (OSError, ValueError, AttributeError):
        return DEFAULT_BACKEND
    return b if b in BACKENDS else DEFAULT_BACKEND


def writer_lock_backend(config_path: str | Path | None = None, env: dict | None = None):
    """The context-manager factory `atomic_append_jsonl` calls at its ONE lock
    site: `events_writer_lock` when the flag is `flock` (today), `events_lease`
    when it is `lease`. Both take `(events_path, holder=..., timeout_s=...)`."""
    if configured_backend(config_path, env) == "lease":
        return events_lease
    try:
        from writer_lock import events_writer_lock
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from writer_lock import events_writer_lock
    return events_writer_lock
